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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .audit import AuditLog
from .config import settings
from .fusion.risk import RiskEngine
from .security import SecurityMiddleware, check_ws_key

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.audit = AuditLog(settings.db_path)
    app.state.risk = RiskEngine(max_alerts=settings.max_alerts_in_memory)
    app.state.ws = WebSocketManager()
    app.state.ready = True
    chain = app.state.audit.verify_chain()
    # NOTE: never log settings.api_key here — a configured key would leak
    # into container logs (ephemeral dev keys are logged by resolve_api_key).
    logger.info(
        "AeroGuard up — audit chain valid=%s records=%s",
        chain["valid"], chain["records"],
    )
    yield
    app.state.ready = False
    app.state.audit.close()


settings.resolve_api_key()

app = FastAPI(
    title="AeroGuard",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    SecurityMiddleware,
    api_key=settings.api_key,
    rate_limit_per_minute=settings.rate_limit_per_minute,
)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    ready = getattr(app.state, "ready", False)
    return {"status": "ready" if ready else "starting"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not check_ws_key(ws.query_params.get("api_key"), settings.api_key):
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
