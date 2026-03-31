"""
FastAPI WebSocket Demo with OpenTelemetry → Splunk Observability Cloud.

This application demonstrates:
  1. FastAPI WebSocket chat (connect, send, broadcast, disconnect)
  2. OpenTelemetry traces for every WebSocket lifecycle event
  3. Custom OTEL metrics (active connections, messages, latency)
  4. Export to Splunk O11y Cloud over OTLP/gRPC

Run:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.otel_config import configure_opentelemetry
from app.websocket_manager import ConnectionManager

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Globals (set during lifespan) ────────────────────────────────────
manager: ConnectionManager | None = None


# ── Application lifespan ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: configure OTEL and create the connection manager."""
    global manager
    tracer, meter = configure_opentelemetry()
    manager = ConnectionManager(tracer=tracer, meter=meter)
    logger.info("🚀 FastAPI WebSocket + OTEL demo is ready")
    yield
    logger.info("🛑 Shutting down…")


# ── FastAPI app ──────────────────────────────────────────────────────
app = FastAPI(
    title="FastAPI WebSocket OTEL Demo",
    description="WebSocket chat with OpenTelemetry traces & metrics exported to Splunk O11y Cloud",
    version="1.0.0",
    lifespan=lifespan,
)

# Auto-instrument all HTTP routes (not WebSocket, which we instrument manually)
FastAPIInstrumentor.instrument_app(app)

# Templates
templates = Jinja2Templates(directory="app/templates")


# ── HTTP Routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    """Serve the WebSocket chat UI."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    """Health-check endpoint (also traced automatically by the FastAPI instrumentor)."""
    active = len(manager.active_connections) if manager else 0
    return {"status": "healthy", "active_connections": active}


# ── WebSocket Endpoint ───────────────────────────────────────────────

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """
    Main WebSocket endpoint.

    1. Accepts the connection and assigns the client_id.
    2. Broadcasts a "joined" message.
    3. Relays all incoming messages to every connected client.
    4. On disconnect, broadcasts a "left" message.
    """
    assert manager is not None, "App not initialised"

    await manager.connect(websocket, client_id)

    # Tell everyone a new user joined
    await manager.broadcast(f"📢 {client_id} joined the chat", sender_id="system")

    try:
        while True:
            data = await manager.receive_message(websocket, client_id)
            await manager.broadcast(f"💬 {client_id}: {data}", sender_id=client_id)
    except WebSocketDisconnect:
        await manager.disconnect(websocket, client_id)
        await manager.broadcast(f"👋 {client_id} left the chat", sender_id="system")
