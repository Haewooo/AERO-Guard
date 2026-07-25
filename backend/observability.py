"""Structured logging, request correlation, and Prometheus metrics.

The service previously logged unstructured text with no request identity,
so a report of "the HMI showed a spurious warning at 14:02" could not be
tied to the classification, the alert and the audit record that produced
it. Every log line now carries a request id that is also returned to the
client in `X-Request-ID`, so an operator can quote it.

Metrics are exposed in Prometheus text format with no client library: the
whole point of this deployment is that it ships no dependency it does not
need, and a counter map plus a histogram is not worth a package.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Buckets in seconds. The upper ones exist because ASR and TTS legitimately
# take hundreds of milliseconds; without them everything piles into +Inf.
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": record.getMessage(),
        }
        for key in ("method", "path", "status", "duration_ms", "operator", "client"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str, json_logs: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_logs
        else logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
        )
    )
    if not json_logs:
        # The plain formatter references request_id, so every record needs it.
        old_factory = logging.getLogRecordFactory()

        def factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.request_id = request_id_var.get()
            return record

        logging.setLogRecordFactory(factory)

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def adopt_uvicorn_loggers() -> None:
    """Route uvicorn's own loggers through our handler.

    uvicorn installs its loggers with propagate=False when the server
    starts — after this module is imported — so configuring the root logger
    at import time is not enough: access lines would still come out in
    uvicorn's plain format and split the log stream in two.

    Must be called from the lifespan startup, which runs after uvicorn has
    applied its own configuration.
    """
    root_handlers = logging.getLogger().handlers
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        log = logging.getLogger(name)
        log.handlers[:] = list(root_handlers)
        log.propagate = False


class Metrics:
    """Minimal Prometheus registry: counters, gauges, one histogram family."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = {}
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._hist: dict[tuple[str, tuple], tuple[list[int], int, float]] = {}
        self._help: dict[str, tuple[str, str]] = {}

    def declare(self, name: str, kind: str, help_text: str) -> None:
        self._help[name] = (kind, help_text)

    @staticmethod
    def _key(labels: dict[str, str] | None) -> tuple:
        return tuple(sorted((labels or {}).items()))

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0):
        with self._lock:
            key = (name, self._key(labels))
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set(self, name: str, value: float, labels: dict[str, str] | None = None):
        with self._lock:
            self._gauges[(name, self._key(labels))] = value

    def observe(self, name: str, seconds: float, labels: dict[str, str] | None = None):
        with self._lock:
            key = (name, self._key(labels))
            counts, total, sum_ = self._hist.get(key, ([0] * len(_BUCKETS), 0, 0.0))
            for i, bound in enumerate(_BUCKETS):
                if seconds <= bound:
                    counts[i] += 1
            self._hist[key] = (counts, total + 1, sum_ + seconds)

    @staticmethod
    def _fmt_labels(key: tuple, extra: str = "") -> str:
        parts = [f'{k}="{v}"' for k, v in key]
        if extra:
            parts.append(extra)
        return "{" + ",".join(parts) + "}" if parts else ""

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            hist = {k: (list(v[0]), v[1], v[2]) for k, v in self._hist.items()}
        lines: list[str] = []
        emitted: set[str] = set()

        def header(name: str, default_kind: str) -> None:
            if name in emitted:
                return
            emitted.add(name)
            kind, help_text = self._help.get(name, (default_kind, name))
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")

        for (name, key), value in sorted(counters.items()):
            header(name, "counter")
            lines.append(f"{name}{self._fmt_labels(key)} {value:g}")
        for (name, key), value in sorted(gauges.items()):
            header(name, "gauge")
            lines.append(f"{name}{self._fmt_labels(key)} {value:g}")
        for (name, key), (counts, total, sum_) in sorted(hist.items()):
            header(name, "histogram")
            for bound, count in zip(_BUCKETS, counts, strict=True):
                lines.append(
                    f'{name}_bucket{self._fmt_labels(key, f"le=\"{bound}\"")} {count}'
                )
            lines.append(f'{name}_bucket{self._fmt_labels(key, "le=\"+Inf\"")} {total}')
            lines.append(f"{name}_sum{self._fmt_labels(key)} {sum_:g}")
            lines.append(f"{name}_count{self._fmt_labels(key)} {total}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
for _name, _kind, _help in (
    ("aeroguard_requests_total", "counter", "HTTP requests by route and status"),
    ("aeroguard_request_duration_seconds", "histogram", "HTTP request latency"),
    ("aeroguard_alerts_total", "counter", "Alerts raised by type and severity"),
    ("aeroguard_signals_total", "counter", "Marshalling classifications by signal"),
    ("aeroguard_audit_records", "gauge", "Records in the audit chain"),
    ("aeroguard_audit_chain_valid", "gauge", "1 when the audit chain verifies"),
    ("aeroguard_runway_occupied", "gauge", "Runways currently marked occupied"),
    ("aeroguard_websocket_clients", "gauge", "Connected HMI WebSocket clients"),
    ("aeroguard_rate_limited_total", "counter", "Requests rejected by the rate limiter"),
    ("aeroguard_auth_failures_total", "counter", "Requests rejected for a bad key"),
    ("aeroguard_inference_threads", "gauge", "Threads allotted to CPU inference"),
):
    metrics.declare(_name, _kind, _help)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def route_of(path: str) -> str:
    """Collapse ids out of paths so metric cardinality stays bounded."""
    if not path.startswith("/api/"):
        return "static"
    parts = path.split("/")
    if len(parts) > 4 and parts[2] == "alerts":
        return "/api/alerts/{id}/" + "/".join(parts[4:])
    return path
