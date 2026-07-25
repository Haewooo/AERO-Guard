"""API security: key→operator identity, rate limiting, security headers.

Authentication resolves the presented key to a named operator identity
server-side. Nothing the client sends is trusted for attribution, so the
audit trail records who acted rather than who claimed to act.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .observability import metrics, new_request_id, request_id_var, route_of

logger = logging.getLogger("aeroguard")

UNKNOWN_IDENTITY = "unknown"


class KeyRegistry:
    """Constant-time API key → operator identity lookup.

    Keys are held as bytes: hmac.compare_digest rejects str inputs that
    carry non-ASCII characters with a TypeError, which on the request
    path would surface as an unauthenticated 500.
    """

    def __init__(self, keys: dict[str, str]):
        self._entries = [(name, key.encode("utf-8")) for name, key in keys.items() if key]
        if not self._entries:
            raise ValueError("KeyRegistry requires at least one API key")

    @property
    def identities(self) -> list[str]:
        return [name for name, _ in self._entries]

    def identify(self, provided: str | None) -> str | None:
        """Return the operator identity for a key, or None if unknown."""
        if not provided:
            return None
        candidate = provided.encode("utf-8", errors="replace")
        match: str | None = None
        for name, key in self._entries:
            # Every entry is compared — an early return would leak which
            # key matched through response timing.
            if hmac.compare_digest(candidate, key):
                match = name
        return match


class ClientResolver:
    """Resolve the originating client address for rate-limit accounting.

    X-Forwarded-For is attacker-controlled unless the immediate peer is a
    proxy we trust, so the header is consulted only for configured
    trusted proxies. With none configured (the default) the socket peer is
    always used.
    """

    def __init__(self, trusted_proxies: str = ""):
        self._trusted: list[ipaddress._BaseNetwork] = []
        for item in trusted_proxies.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                self._trusted.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                logger.warning("ignoring invalid trusted proxy entry: %r", item)

    def _is_trusted(self, host: str) -> bool:
        if not self._trusted:
            return False
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(addr in net for net in self._trusted)

    def client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else UNKNOWN_IDENTITY
        if not self._is_trusted(peer):
            return peer
        # Walk the forwarded chain right to left; the first hop we do not
        # operate is the real client. Everything left of it is forgeable.
        forwarded = request.headers.get("x-forwarded-for", "")
        for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
            if not self._is_trusted(hop):
                return hop
        return peer


class TokenBucket:
    """Per-client token bucket rate limiter (in-process).

    Idle buckets are swept so a large or hostile client population cannot
    grow the table without bound. Eviction is safe: a bucket refills to
    full capacity in 60s, so any bucket idle past the TTL is already
    indistinguishable from a fresh one.
    """

    def __init__(
        self,
        rate_per_minute: int,
        idle_ttl: float = 600.0,
        max_buckets: int = 50_000,
        sweep_interval: float = 60.0,
    ):
        self._capacity = float(rate_per_minute)
        self._refill_per_sec = rate_per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._idle_ttl = max(idle_ttl, 60.0)
        self._max_buckets = max_buckets
        self._sweep_interval = sweep_interval
        self._next_sweep = 0.0

    def _sweep(self, now: float) -> None:
        """Drop idle buckets. Caller must hold the lock."""
        cutoff = now - self._idle_ttl
        for key in [k for k, (_, last) in self._buckets.items() if last < cutoff]:
            del self._buckets[key]
        overflow = len(self._buckets) - self._max_buckets
        if overflow > 0:
            oldest = sorted(self._buckets.items(), key=lambda kv: kv[1][1])
            for key, _ in oldest[:overflow]:
                del self._buckets[key]
        self._next_sweep = now + self._sweep_interval

    def allow(self, client_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if now >= self._next_sweep:
                self._sweep(now)
            tokens, last = self._buckets.get(client_id, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill_per_sec)
            if tokens < 1.0:
                self._buckets[client_id] = (tokens, now)
                return False
            self._buckets[client_id] = (tokens - 1.0, now)
            return True

    @property
    def tracked(self) -> int:
        with self._lock:
            return len(self._buckets)


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        registry: KeyRegistry,
        rate_limit_per_minute: int,
        trusted_proxies: str = "",
    ):
        super().__init__(app)
        self._registry = registry
        self._limiter = TokenBucket(rate_limit_per_minute)
        self._resolver = ClientResolver(trusted_proxies)

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("x-request-id") or new_request_id()
        token = request_id_var.set(request_id)
        path = request.url.path
        route = route_of(path)
        client = self._resolver.client_ip(request)
        identity = UNKNOWN_IDENTITY
        try:
            if path.startswith("/api/"):
                if not self._limiter.allow(client):
                    metrics.inc("aeroguard_rate_limited_total", {"route": route})
                    return self._finish(
                        JSONResponse({"detail": "rate limit exceeded"}, status_code=429),
                        request_id, route, started, 429,
                    )
                identity = self._registry.identify(request.headers.get("x-api-key"))
                if identity is None:
                    metrics.inc("aeroguard_auth_failures_total", {"route": route})
                    return self._finish(
                        JSONResponse({"detail": "invalid API key"}, status_code=401),
                        request_id, route, started, 401,
                    )
                # Server-derived attribution for the audit trail.
                request.state.identity = identity

            response = await call_next(request)
            duration = time.perf_counter() - started
            if response.status_code >= 400 or duration > 1.0:
                logger.warning(
                    "request completed",
                    extra={
                        "method": request.method, "path": path,
                        "status": response.status_code,
                        "duration_ms": round(duration * 1000, 1),
                        "operator": identity, "client": client,
                    },
                )
            return self._finish(
                response, request_id, route, started, response.status_code
            )
        finally:
            request_id_var.reset(token)

    def _finish(self, response, request_id, route, started, status):
        elapsed = time.perf_counter() - started
        metrics.inc(
            "aeroguard_requests_total", {"route": route, "status": str(status)}
        )
        metrics.observe("aeroguard_request_duration_seconds", elapsed, {"route": route})
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        # Offline-first: everything is served same-origin, no external calls.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self' ws: wss:; "
            "img-src 'self' data:; style-src 'self'"
        )
        return response


def operator_of(request: Request) -> str:
    """Operator identity established by SecurityMiddleware."""
    return getattr(request.state, "identity", UNKNOWN_IDENTITY)
