"""
WebSocket Connection Manager with OpenTelemetry instrumentation.

Manages active WebSocket connections and broadcasts messages,
while emitting traces and custom metrics for every operation.
"""

from __future__ import annotations

import time
import logging
from typing import Any

from fastapi import WebSocket
from opentelemetry import trace, metrics

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections with full OTEL observability.

    Metrics emitted:
      - websocket.connections.active    (UpDownCounter)
      - websocket.connections.total     (Counter)
      - websocket.messages.sent         (Counter)
      - websocket.messages.received     (Counter)
      - websocket.message.latency_ms    (Histogram)
      - websocket.errors                (Counter)
    """

    def __init__(self, tracer: trace.Tracer, meter: metrics.Meter):
        self.active_connections: list[WebSocket] = []
        self.tracer = tracer

        # ── Metrics instruments ──────────────────────────────────────
        self.active_connections_gauge = meter.create_up_down_counter(
            name="websocket.connections.active",
            description="Number of currently active WebSocket connections",
            unit="{connections}",
        )
        self.total_connections_counter = meter.create_counter(
            name="websocket.connections.total",
            description="Total WebSocket connections opened since startup",
            unit="{connections}",
        )
        self.messages_sent_counter = meter.create_counter(
            name="websocket.messages.sent",
            description="Total messages sent to WebSocket clients",
            unit="{messages}",
        )
        self.messages_received_counter = meter.create_counter(
            name="websocket.messages.received",
            description="Total messages received from WebSocket clients",
            unit="{messages}",
        )
        self.message_latency_histogram = meter.create_histogram(
            name="websocket.message.latency_ms",
            description="Time taken to process and broadcast a message",
            unit="ms",
        )
        self.errors_counter = meter.create_counter(
            name="websocket.errors",
            description="Total WebSocket errors encountered",
            unit="{errors}",
        )

    # ── Connection lifecycle ─────────────────────────────────────────

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept a WebSocket connection and track it."""
        with self.tracer.start_as_current_span(
            "websocket.connect",
            attributes={
                "websocket.client_id": client_id,
                "websocket.client_host": websocket.client.host if websocket.client else "unknown",
            },
        ) as span:
            await websocket.accept()
            self.active_connections.append(websocket)

            # Update metrics
            self.active_connections_gauge.add(1, {"client_id": client_id})
            self.total_connections_counter.add(1, {"client_id": client_id})

            span.set_attribute("websocket.active_connections", len(self.active_connections))
            logger.info(f"Client {client_id} connected  (active={len(self.active_connections)})")

    async def disconnect(self, websocket: WebSocket, client_id: str) -> None:
        """Remove a WebSocket connection from the pool."""
        with self.tracer.start_as_current_span(
            "websocket.disconnect",
            attributes={"websocket.client_id": client_id},
        ) as span:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            self.active_connections_gauge.add(-1, {"client_id": client_id})

            span.set_attribute("websocket.active_connections", len(self.active_connections))
            logger.info(f"Client {client_id} disconnected  (active={len(self.active_connections)})")

    # ── Messaging ────────────────────────────────────────────────────

    async def send_personal_message(self, message: str, websocket: WebSocket, client_id: str) -> None:
        """Send a message to a single client."""
        with self.tracer.start_as_current_span(
            "websocket.send_personal",
            attributes={
                "websocket.client_id": client_id,
                "websocket.message_length": len(message),
            },
        ):
            await websocket.send_text(message)
            self.messages_sent_counter.add(1, {"client_id": client_id, "type": "personal"})

    async def broadcast(self, message: str, sender_id: str = "system") -> None:
        """Broadcast a message to ALL connected clients and record latency."""
        start = time.perf_counter()

        with self.tracer.start_as_current_span(
            "websocket.broadcast",
            attributes={
                "websocket.sender_id": sender_id,
                "websocket.message_length": len(message),
                "websocket.recipient_count": len(self.active_connections),
            },
        ) as span:
            disconnected: list[WebSocket] = []

            for connection in self.active_connections:
                try:
                    await connection.send_text(message)
                    self.messages_sent_counter.add(1, {"sender_id": sender_id, "type": "broadcast"})
                except Exception as exc:
                    logger.warning(f"Failed to send to a client: {exc}")
                    self.errors_counter.add(1, {"operation": "broadcast"})
                    disconnected.append(connection)

            # Clean up broken connections
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)
                    self.active_connections_gauge.add(-1)

            elapsed_ms = (time.perf_counter() - start) * 1000
            self.message_latency_histogram.record(elapsed_ms, {"sender_id": sender_id})
            span.set_attribute("websocket.broadcast_latency_ms", round(elapsed_ms, 2))

    async def receive_message(self, websocket: WebSocket, client_id: str) -> str:
        """Receive a text message and record the metric."""
        with self.tracer.start_as_current_span(
            "websocket.receive",
            attributes={"websocket.client_id": client_id},
        ) as span:
            data = await websocket.receive_text()
            self.messages_received_counter.add(1, {"client_id": client_id})
            span.set_attribute("websocket.message_length", len(data))
            return data
