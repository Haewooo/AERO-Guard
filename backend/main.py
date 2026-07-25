"""AeroGuard — Ground Safety AI Assistant (on-premises, offline-first).

Single-node FastAPI application:
  - /api/*   : authenticated REST API (X-API-Key)
  - /ws      : authenticated WebSocket for live HMI updates
  - /healthz : liveness (public)   /readyz : readiness (public)
  - /        : HMI console (static, no external CDN — air-gap safe)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .audit import AuditLog, load_or_create_key
from .config import settings
from .fusion.risk import RiskEngine
from .observability import adopt_uvicorn_loggers, configure_logging, metrics
from .runtime import inference_threads
from .security import KeyRegistry, SecurityMiddleware
from .state import StateStore

configure_logging(settings.log_level, settings.json_logs)
logger = logging.getLogger("aeroguard")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            connections = list(self._connections)
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(ws)

    def count(self) -> int:
        return len(self._connections)


def _sidecar(explicit: str, filename: str) -> str:
    """Resolve a path that defaults to sitting beside the database."""
    return explicit or str(Path(settings.db_path).resolve().parent / filename)


@asynccontextmanager
async def lifespan(app: FastAPI):
    adopt_uvicorn_loggers()
    audit_key = (
        settings.audit_key.encode()
        if settings.audit_key
        else load_or_create_key(_sidecar(settings.audit_key_path, "audit.key"))
    )
    app.state.audit = AuditLog(
        settings.db_path,
        key=audit_key,
        anchor_path=_sidecar(settings.audit_anchor_path, "audit-anchors.log"),
    )
    app.state.store = StateStore(settings.db_path)
    app.state.risk = RiskEngine(
        max_alerts=settings.max_alerts_in_memory,
        store=app.state.store,
        signal_confirmations=settings.signal_confirmations,
        signal_release_windows=settings.signal_release_windows,
    )
    app.state.ws = WebSocketManager()
    app.state.ready = True
    # Retention runs before verification so a long-lived deployment does
    # not pay a full-table scan over history it is not keeping anyway.
    app.state.audit.prune(settings.audit_retention_days)
    chain = app.state.audit.verify_chain()
    app.state.audit.anchor()
    # NOTE: never log API keys here — a configured key would leak into
    # container logs (ephemeral dev keys are logged by resolve_api_key).
    logger.info(
        "AeroGuard up — audit chain valid=%s records=%s algo=%s anchors=%s | "
        "operators=%s | restored occupancy=%s alerts=%s",
        chain["valid"], chain["records"], chain["algo"], chain["anchors"]["checked"],
        ",".join(app.state.registry.identities),
        len(app.state.risk.get_occupancy()),
        len(app.state.risk.recent_alerts(settings.max_alerts_in_memory)),
    )
    if not chain["valid"]:
        logger.error(
            "AUDIT CHAIN VERIFICATION FAILED — %s", chain.get("reason", "unknown")
        )
    yield
    app.state.ready = False
    app.state.audit.close()
    app.state.store.close()


registry = KeyRegistry(settings.resolve_keys())

app = FastAPI(
    title="AeroGuard",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.registry = registry
app.add_middleware(
    SecurityMiddleware,
    registry=registry,
    rate_limit_per_minute=settings.rate_limit_per_minute,
    trusted_proxies=settings.trusted_proxies,
)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint.

    Public like the health probes: it carries operational counters, no
    payloads and no keys. Keep it off the network with the loopback bind,
    or put it behind the reverse proxy that already fronts the HMI.
    """
    risk = getattr(app.state, "risk", None)
    audit = getattr(app.state, "audit", None)
    if risk is not None:
        metrics.set("aeroguard_runway_occupied", len(risk.get_occupancy()))
    if audit is not None:
        chain = audit.verify_chain()
        metrics.set("aeroguard_audit_records", chain["records"])
        metrics.set("aeroguard_audit_chain_valid", 1 if chain["valid"] else 0)
    ws = getattr(app.state, "ws", None)
    if ws is not None:
        metrics.set("aeroguard_websocket_clients", ws.count())
    metrics.set("aeroguard_inference_threads", inference_threads())
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/readyz")
async def readyz() -> dict:
    ready = getattr(app.state, "ready", False)
    return {"status": "ready" if ready else "starting"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if registry.identify(ws.query_params.get("api_key")) is None:
        await ws.close(code=4401, reason="invalid API key")
        return
    manager: WebSocketManager = app.state.ws
    await manager.connect(ws)
    try:
        while True:
            # Keepalive: HMI is push-only; drain incoming pings.
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="hmi")
