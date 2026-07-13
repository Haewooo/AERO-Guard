"""API security middleware: key auth, rate limiting, security headers."""

from __future__ import annotations

import hmac
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

PUBLIC_PATHS = {"/healthz", "/readyz"}


class TokenBucket:
    """Per-client token bucket rate limiter (in-process)."""

    def __init__(self, rate_per_minute: int):
        self._capacity = float(rate_per_minute)
        self._refill_per_sec = rate_per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, client_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(client_id, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill_per_sec)
            if tokens < 1.0:
                self._buckets[client_id] = (tokens, now)
                return False
            self._buckets[client_id] = (tokens - 1.0, now)
            return True


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str, rate_limit_per_minute: int):
        super().__init__(app)
        self._api_key = api_key
        self._limiter = TokenBucket(rate_limit_per_minute)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        needs_auth = path.startswith("/api/")

        if needs_auth:
            client = request.client.host if request.client else "unknown"
            if not self._limiter.allow(client):
                return JSONResponse(
                    {"detail": "rate limit exceeded"}, status_code=429
                )
            provided = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, self._api_key):
                return JSONResponse({"detail": "invalid API key"}, status_code=401)

        response = await call_next(request)
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


def check_ws_key(provided: str | None, api_key: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided, api_key)
