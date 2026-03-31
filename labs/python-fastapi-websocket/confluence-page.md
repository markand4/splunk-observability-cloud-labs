# Implementing OpenTelemetry on FastAPI WebSockets (Python) with Splunk Observability Cloud

## Table of Contents

> **Confluence Users:** Use the `{toc}` macro instead of this manual TOC for auto-generated, clickable links.  
> Insert it by typing `/toc` in the Confluence editor.

- Overview
- Why WebSockets Need Special Instrumentation
- Architecture
- Prerequisites
- Step-by-Step Implementation Guide
  - Step 1: Install Dependencies
  - Step 2: Configure OpenTelemetry for Splunk O11y Cloud
  - Step 3: Create Custom WebSocket Instrumentation
  - Step 4: Wire It Into FastAPI
  - Step 5: Environment Configuration
  - Step 6: Run and Validate
- Custom Metrics Reference
- Custom Spans Reference
- Best Practices
  - Instrumentation Best Practices
  - Dashboarding in Splunk O11y Cloud
  - Recommended Detectors and Alerts
  - Troubleshooting
- Key Gotchas and Lessons Learned
- Example Result in Splunk APM
- Reference Links

---

## Overview

WebSocket connections are **long-lived, bidirectional, and stateful** — fundamentally different from HTTP request/response. Standard OpenTelemetry auto-instrumentation for FastAPI covers HTTP routes but **does not instrument WebSocket frames**. This guide shows how to build full observability for FastAPI WebSocket applications and export telemetry to Splunk Observability Cloud.

**Full working example:** [github.com/markand4/splunk-observability-cloud-labs](https://github.com/markand4/splunk-observability-cloud-labs/tree/main/labs/python-fastapi-websocket)

---

## Why WebSockets Need Special Instrumentation

| Aspect | HTTP | WebSocket |
|---|---|---|
| Connection lifetime | Milliseconds–seconds | Minutes–hours |
| Messages per connection | 1 request → 1 response | Many frames in both directions |
| OTEL auto-instrumentation | ✅ Supported via `opentelemetry-instrumentation-fastapi` | ❌ Not covered — manual spans needed |
| Span model | 1 span per request | 1 root span per session + child spans per frame |
| Error detection | HTTP status codes | Application-level (no status codes) |

Because the `opentelemetry-instrumentation-fastapi` package only instruments HTTP `Request`/`Response` cycles, **WebSocket `connect`, `receive`, `send`, and `disconnect` events must be instrumented manually** using the OTEL SDK.

---

## Architecture

```
┌──────────────┐   WebSocket (ws://)   ┌──────────────────────────┐
│  Browser /    │◄────────────────────►│    FastAPI + Uvicorn       │
│  Client App   │                       │                          │
└──────────────┘                       │  ┌──────────────────────┐ │
                                        │  │  ConnectionManager   │ │
                                        │  │  - Manual spans      │ │
                                        │  │  - Custom metrics    │ │
                                        │  └──────────┬───────────┘ │
                                        │             │             │
                                        │  ┌──────────▼───────────┐ │
                                        │  │  OpenTelemetry SDK    │ │
                                        │  │  TracerProvider       │ │
                                        │  │  MeterProvider        │ │
                                        │  └──────────┬───────────┘ │
                                        └─────────────┼─────────────┘
                                                      │ OTLP/HTTP
                                        ┌─────────────▼─────────────┐
                                        │  Splunk Observability      │
                                        │  Cloud (APM + Metrics)     │
                                        └────────────────────────────┘
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| Python | 3.10 or higher |
| Splunk O11y Cloud account | [Sign up](https://www.splunk.com/en_us/products/observability.html) |
| Splunk Ingest Token | Settings → Access Tokens → create with **ingest** scope |
| Splunk Realm | e.g., `us0`, `us1`, `eu0` (visible in your O11y Cloud URL) |

---

## Step-by-Step Implementation Guide

### Step 1: Install Dependencies

```bash
pip install \
  fastapi uvicorn[standard] websockets \
  opentelemetry-api opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-logging
```

> **Important:** Use `opentelemetry-exporter-otlp-proto-http` (not `grpc`) for direct Splunk ingest. The Splunk O11y Cloud ingest endpoint does not support the OTLP/gRPC service — it uses OTLP/HTTP (protobuf) instead.

### Step 2: Configure OpenTelemetry for Splunk O11y Cloud

Create `app/otel_config.py`:

```python
import os
import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

logger = logging.getLogger(__name__)


def configure_opentelemetry() -> tuple[trace.Tracer, metrics.Meter]:
    splunk_token = os.getenv("SPLUNK_ACCESS_TOKEN", "")
    splunk_realm = os.getenv("SPLUNK_REALM", "us0")
    service_name = os.getenv("OTEL_SERVICE_NAME", "fastapi-websocket-demo")
    environment  = os.getenv("OTEL_ENVIRONMENT", "demo")

    # Splunk O11y Cloud OTLP/HTTP ingest endpoints
    traces_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        f"https://ingest.{splunk_realm}.signalfx.com/v2/trace/otlp"
    )
    metrics_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        f"https://ingest.{splunk_realm}.signalfx.com/v2/datapoint/otlp"
    )

    resource = Resource.create({
        SERVICE_NAME: service_name,
        "deployment.environment": environment,
        "service.version": "1.0.0",
    })

    headers = {"X-SF-TOKEN": splunk_token} if splunk_token else {}

    # --- Traces ---
    span_exporter = OTLPSpanExporter(endpoint=traces_endpoint, headers=headers)
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer(service_name)

    # --- Metrics ---
    metric_exporter = OTLPMetricExporter(endpoint=metrics_endpoint, headers=headers)
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=10_000,  # export every 10 seconds
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter(service_name)

    return tracer, meter
```

**Key points:**
- Use `https://ingest.{realm}.signalfx.com/v2/trace/otlp` for traces
- Use `https://ingest.{realm}.signalfx.com/v2/datapoint/otlp` for metrics
- Authenticate with the `X-SF-TOKEN` header containing your ingest token
- Set `deployment.environment` on the Resource so Splunk APM groups services by environment

### Step 3: Create Custom WebSocket Instrumentation

Since OTEL auto-instrumentation doesn't cover WebSocket frames, we build a `ConnectionManager` that wraps every operation with spans and metrics.

Create `app/websocket_manager.py`:

```python
import time
import logging
from fastapi import WebSocket
from opentelemetry import trace, metrics

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self, tracer: trace.Tracer, meter: metrics.Meter):
        self.active_connections: list[WebSocket] = []
        self.tracer = tracer

        # ── Metrics instruments ──
        self.active_connections_gauge = meter.create_up_down_counter(
            name="websocket.connections.active",
            description="Currently active WebSocket connections",
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
            description="Time to process and broadcast a message",
            unit="ms",
        )
        self.errors_counter = meter.create_counter(
            name="websocket.errors",
            description="Total WebSocket errors encountered",
            unit="{errors}",
        )

    async def connect(self, websocket: WebSocket, client_id: str):
        with self.tracer.start_as_current_span(
            "websocket.connect",
            attributes={
                "websocket.client_id": client_id,
                "websocket.client_host": websocket.client.host if websocket.client else "unknown",
            },
        ) as span:
            await websocket.accept()
            self.active_connections.append(websocket)
            self.active_connections_gauge.add(1, {"client_id": client_id})
            self.total_connections_counter.add(1, {"client_id": client_id})
            span.set_attribute("websocket.active_connections", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket, client_id: str):
        with self.tracer.start_as_current_span(
            "websocket.disconnect",
            attributes={"websocket.client_id": client_id},
        ):
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            self.active_connections_gauge.add(-1, {"client_id": client_id})

    async def broadcast(self, message: str, sender_id: str = "system"):
        start = time.perf_counter()
        with self.tracer.start_as_current_span(
            "websocket.broadcast",
            attributes={
                "websocket.sender_id": sender_id,
                "websocket.message_length": len(message),
                "websocket.recipient_count": len(self.active_connections),
            },
        ) as span:
            for connection in self.active_connections:
                try:
                    await connection.send_text(message)
                    self.messages_sent_counter.add(1, {"sender_id": sender_id, "type": "broadcast"})
                except Exception:
                    self.errors_counter.add(1, {"operation": "broadcast"})
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.message_latency_histogram.record(elapsed_ms, {"sender_id": sender_id})

    async def receive_message(self, websocket: WebSocket, client_id: str) -> str:
        with self.tracer.start_as_current_span(
            "websocket.receive",
            attributes={"websocket.client_id": client_id},
        ) as span:
            data = await websocket.receive_text()
            self.messages_received_counter.add(1, {"client_id": client_id})
            span.set_attribute("websocket.message_length", len(data))
            return data
```

### Step 4: Wire It Into FastAPI

Create `app/main.py`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from app.otel_config import configure_opentelemetry
from app.websocket_manager import ConnectionManager

manager: ConnectionManager | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    tracer, meter = configure_opentelemetry()
    manager = ConnectionManager(tracer=tracer, meter=meter)
    yield

app = FastAPI(title="FastAPI WebSocket OTEL Demo", lifespan=lifespan)

# Auto-instrument HTTP routes (GET, POST, etc.)
FastAPIInstrumentor.instrument_app(app)

@app.get("/health")
async def health():
    active = len(manager.active_connections) if manager else 0
    return {"status": "healthy", "active_connections": active}

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    await manager.broadcast(f"{client_id} joined the chat", sender_id="system")
    try:
        while True:
            data = await manager.receive_message(websocket, client_id)
            await manager.broadcast(f"{client_id}: {data}", sender_id=client_id)
    except WebSocketDisconnect:
        await manager.disconnect(websocket, client_id)
        await manager.broadcast(f"{client_id} left the chat", sender_id="system")
```

**Key design decisions:**
- `FastAPIInstrumentor.instrument_app(app)` handles HTTP automatically
- WebSocket is **manually** instrumented via the `ConnectionManager`
- OTEL is initialized during FastAPI's `lifespan` startup (not at import time)

### Step 5: Environment Configuration

Create a `.env` file (never commit this — it contains your token):

```dotenv
SPLUNK_ACCESS_TOKEN=<your-ingest-token>
SPLUNK_REALM=us1
OTEL_SERVICE_NAME=fastapi-websocket-demo
OTEL_ENVIRONMENT=demo
```

### Step 6: Run and Validate

```bash
# Activate your virtual environment
source .venv/bin/activate

# Export environment variables
export $(grep -v '^#' ../../.env | xargs)
export OTEL_SERVICE_NAME=fastapi-websocket-demo
export OTEL_ENVIRONMENT=demo

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` in multiple browser tabs and exchange messages. After 30–60 seconds, check Splunk APM.

---

## Custom Metrics Reference

| Metric Name | OTEL Type | Description | Useful For |
|---|---|---|---|
| `websocket.connections.active` | UpDownCounter | Current open connections | Real-time capacity monitoring |
| `websocket.connections.total` | Counter | Cumulative connections since startup | Traffic volume trending |
| `websocket.messages.sent` | Counter | Total outbound messages | Throughput analysis |
| `websocket.messages.received` | Counter | Total inbound messages | Throughput analysis |
| `websocket.message.latency_ms` | Histogram | Broadcast processing time | Performance & SLA monitoring |
| `websocket.errors` | Counter | Errors during send/receive/broadcast | Error rate alerting |

---

## Custom Spans Reference

| Span Name | Triggered When | Key Attributes |
|---|---|---|
| `websocket.connect` | Client opens a WebSocket | `websocket.client_id`, `websocket.client_host`, `websocket.active_connections` |
| `websocket.disconnect` | Client disconnects | `websocket.client_id`, `websocket.active_connections` |
| `websocket.receive` | Server receives a message | `websocket.client_id`, `websocket.message_length` |
| `websocket.send_personal` | Server sends to one client | `websocket.client_id`, `websocket.message_length` |
| `websocket.broadcast` | Server broadcasts to all clients | `websocket.sender_id`, `websocket.recipient_count`, `websocket.broadcast_latency_ms` |

---

## Best Practices

### Instrumentation Best Practices

1. **Use `start_as_current_span` with context managers** — ensures spans are always closed, even on exceptions:
   ```python
   with tracer.start_as_current_span("websocket.connect", attributes={...}) as span:
       await websocket.accept()
   ```

2. **Don't record message content in span attributes** — message bodies can contain PII. Record `message_length` instead.

3. **Use meaningful span names with dot notation** — e.g., `websocket.connect`, `websocket.broadcast`. This groups well in Splunk APM's service map.

4. **Set `deployment.environment` on your Resource** — Splunk APM uses this to separate dev/staging/prod views.

5. **Set `service.version`** — enables deployment tracking and version-based filtering in APM.

6. **Use UpDownCounter (not Gauge) for active connections** — OTEL's metric model uses delta aggregation; UpDownCounter correctly tracks values that go up and down.

7. **Use Histogram for latency** — gives you p50/p90/p99 breakdowns in Splunk automatically.

8. **Keep metric cardinality low** — avoid using unbounded values (like `client_id`) as metric attributes in production. Use them in spans instead. High-cardinality attributes on metrics create MTS (metric time series) explosion.

9. **Export interval** — 10 seconds (`export_interval_millis=10_000`) is a good default for demos. In production, 60 seconds is more typical.

10. **Batch span processor** — always use `BatchSpanProcessor` (not `SimpleSpanProcessor`) in production to avoid blocking the event loop on export.

### Dashboarding in Splunk O11y Cloud

#### Recommended Dashboard Charts

| Chart | Signal/Formula | Chart Type | Purpose |
|---|---|---|---|
| **Active Connections** | `websocket.connections.active` | Line | Real-time connection count |
| **Connection Rate** | `rate(websocket.connections.total)` | Area | New connections per second |
| **Message Throughput** | `rate(websocket.messages.sent)` + `rate(websocket.messages.received)` | Stacked Area | Messages/sec in each direction |
| **Broadcast Latency (p99)** | `percentile(websocket.message.latency_ms, 99)` | Line | Tail latency for broadcasts |
| **Broadcast Latency (p50)** | `percentile(websocket.message.latency_ms, 50)` | Line | Median broadcast time |
| **Error Rate** | `rate(websocket.errors)` | Line | Errors per second |
| **Error Ratio** | `rate(websocket.errors) / rate(websocket.messages.sent)` | Single Value | % of messages that fail |
| **Connections by Client** | `websocket.connections.total` grouped by `client_id` | Bar | Top clients by connection count |

#### How to Create a Dashboard

1. Go to **Splunk O11y Cloud → Dashboards → Create Dashboard**
2. Add charts using the metric names above
3. Use **filters**: `sf_service:fastapi-websocket-demo` and `sf_environment:demo`
4. Group by `deployment.environment` to compare environments side by side

### Recommended Detectors and Alerts

Configure these in **Splunk O11y Cloud → Alerts & Detectors**:

| Detector Name | Condition | Severity | Why |
|---|---|---|---|
| **WebSocket Error Spike** | `rate(websocket.errors) > 10/min` for 5 min | Critical | Indicates systematic send failures (network issues, client drops) |
| **High Active Connections** | `websocket.connections.active > 1000` for 2 min | Warning | Approaching capacity — scale or investigate |
| **Broadcast Latency Degradation** | `p99(websocket.message.latency_ms) > 500ms` for 5 min | Warning | Slow broadcasts → poor user experience |
| **Zero Active Connections** | `websocket.connections.active == 0` for 10 min (during business hours) | Info | May indicate the service is down or unreachable |
| **Connection Churn** | `rate(websocket.connections.total) > 100/min` AND `websocket.connections.active < 5` | Warning | Clients connecting and immediately disconnecting — likely auth/handshake failures |
| **APM Service Health** | Use built-in APM detector on `fastapi-websocket-demo` | Auto | Monitors latency, error rate, and request rate for HTTP endpoints |

### Troubleshooting

#### No Data in Splunk APM

| Symptom | Cause | Fix |
|---|---|---|
| No service in APM | Token or realm wrong | Verify `SPLUNK_ACCESS_TOKEN` and `SPLUNK_REALM`. Check server logs for export errors. |
| Service appears, but no WebSocket spans | Auto-instrumentation only covers HTTP | Confirm you're using the manual `ConnectionManager` instrumentation, not relying solely on `FastAPIInstrumentor` |
| `StatusCode.UNIMPLEMENTED` errors | Using gRPC exporter against Splunk direct ingest | Switch to `opentelemetry-exporter-otlp-proto-http`. Splunk ingest doesn't support OTLP/gRPC. |
| `Illegal header key` / metadata errors | Uppercase header keys with newer `grpcio` | Use the HTTP exporter (recommended) or lowercase all gRPC metadata keys |
| Metrics appear but traces don't (or vice versa) | Endpoints misconfigured | Traces: `/v2/trace/otlp`, Metrics: `/v2/datapoint/otlp` |
| `SPLUNK_ACCESS_TOKEN not set` warning | `.env` not loaded | Run `export $(grep -v '^#' .env | xargs)` before starting uvicorn |
| Spans appear but are not linked to a service | `SERVICE_NAME` not set in Resource | Ensure `OTEL_SERVICE_NAME` is set and passed to `Resource.create()` |

#### High Cardinality Warnings in Splunk

If you see MTS (Metric Time Series) warnings:
- Remove `client_id` from **metric** attributes in production (keep it in span attributes)
- Use bounded attributes like `sender_type: "user" | "system"` instead
- Check your MTS count in **Settings → Billing & Usage**

#### WebSocket-Specific Issues

| Issue | Investigation Steps |
|---|---|
| Connections dropping unexpectedly | Check `websocket.errors` metric. Look for `websocket.disconnect` spans. Check if the server async loop is blocking. |
| Broadcast latency spikes | Check `websocket.message.latency_ms` histogram. Look at `websocket.recipient_count` — latency scales with connections. Consider chunking broadcasts. |
| Memory growth | Monitor `websocket.connections.active`. Ensure disconnected clients are removed from the connection pool. Check for connection leaks. |
| Trace too large (>155 spans) | Long-lived WebSocket sessions create many child spans. Consider sampling or starting new trace contexts periodically for very long sessions. |

---

## Key Gotchas and Lessons Learned

1. **Use OTLP/HTTP, not gRPC, for Splunk direct ingest.** The Splunk O11y Cloud ingest endpoints (`ingest.{realm}.signalfx.com`) do not implement the OTLP/gRPC service. You'll get `StatusCode.UNIMPLEMENTED`. Use `opentelemetry-exporter-otlp-proto-http` with the correct paths.

2. **Splunk ingest URLs include a path.** Unlike a generic OTEL Collector, Splunk wants:
   - Traces: `https://ingest.{realm}.signalfx.com/v2/trace/otlp`
   - Metrics: `https://ingest.{realm}.signalfx.com/v2/datapoint/otlp`

3. **`opentelemetry-semantic-conventions` import paths change between versions.** If you get `ModuleNotFoundError: No module named 'opentelemetry.semantic_conventions'`, use plain string keys like `"deployment.environment"` instead of `ResourceAttributes.DEPLOYMENT_ENVIRONMENT`.

4. **gRPC metadata keys must be lowercase.** Newer versions of `grpcio` (1.60+) enforce lowercase metadata keys. `X-SF-TOKEN` will throw `Illegal header key`. Use the HTTP exporter to avoid this entirely.

5. **WebSocket sessions produce long-lived traces.** A 60-second WebSocket session with rapid messaging can produce 100+ spans in a single trace. Monitor trace sizes and consider trace context rotation for production workloads.

6. **`FastAPIInstrumentor` does not cover WebSocket routes.** It only instruments ASGI HTTP request/response. All WebSocket observability must be manual.

7. **Never commit `.env` files.** Always use `.env.example` with placeholder values and add `.env` to `.gitignore`.

---

## Example Result in Splunk APM

After running the application with the traffic generator, here is what the trace waterfall looks like in Splunk Observability Cloud APM:

![Splunk APM Trace Waterfall — WebSocket Session](https://raw.githubusercontent.com/markand4/splunk-observability-cloud-labs/main/labs/python-fastapi-websocket/images/trace-example.png)

**What you're seeing:**
- The **root span** `HTTP /ws/{client_id}` represents the full WebSocket session (34 seconds)
- **Child spans** show the full lifecycle: `websocket.connect` → `websocket.receive` → `websocket.broadcast` (repeating for each message exchange)
- Each `websocket.broadcast` includes nested `websocket send` spans — one per connected client
- **Span tags** include `http.route: /ws/{client_id}`, `http.scheme: ws`, `http.host`, and `http.response.status_code: 200`
- **155 spans** were captured in a single trace, providing full visibility into every message exchange

---

## Reference Links

### OpenTelemetry
- [OpenTelemetry Python SDK Documentation](https://opentelemetry.io/docs/languages/python/)
- [OpenTelemetry Python API Reference](https://opentelemetry-python.readthedocs.io/en/stable/)
- [OTEL Python Instrumentation for FastAPI](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html)
- [OTEL Metrics SDK (Python)](https://opentelemetry.io/docs/languages/python/instrumentation/#metrics)
- [OTEL Manual Instrumentation Guide](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/)

### Splunk Observability Cloud
- [Splunk O11y Cloud — Get Started with OpenTelemetry](https://docs.splunk.com/observability/en/gdi/get-data-in/application/python/get-started.html)
- [Splunk O11y Cloud — Python Instrumentation](https://docs.splunk.com/observability/en/gdi/get-data-in/application/python/instrumentation/instrument-python-application.html)
- [Splunk O11y Cloud — OTLP Ingest Endpoints](https://docs.splunk.com/observability/en/gdi/get-data-in/connect/aws/aws-apiconfig.html)
- [Splunk APM — Service Map and Traces](https://docs.splunk.com/observability/en/apm/intro-to-apm.html)
- [Splunk O11y Cloud — Detectors and Alerts](https://docs.splunk.com/observability/en/alerts-detectors-notifications/create-detectors-for-alerts.html)
- [Splunk O11y Cloud — Custom Metrics](https://docs.splunk.com/observability/en/metrics-and-metadata/metrics.html)
- [Splunk O11y Cloud — Dashboards](https://docs.splunk.com/observability/en/data-visualization/dashboards/dashboards.html)

### FastAPI
- [FastAPI WebSocket Documentation](https://fastapi.tiangolo.com/advanced/websockets/)
- [Starlette WebSocket Reference](https://www.starlette.io/websockets/)

### Example Repository
- **Full working code:** [github.com/markand4/splunk-observability-cloud-labs](https://github.com/markand4/splunk-observability-cloud-labs/tree/main/labs/python-fastapi-websocket)

---

*Last updated: March 2026*
