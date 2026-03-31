# Rust Dice Server with OpenTelemetry → Splunk Observability Cloud

A Rust HTTP server that rolls dice, fully instrumented with [OpenTelemetry](https://opentelemetry.io/) to export **traces and metrics** to [Splunk Observability Cloud](https://www.splunk.com/en_us/products/observability.html).

> **Attribution:** This lab is based on the official [OpenTelemetry Rust Getting Started](https://opentelemetry.io/docs/languages/rust/getting-started/) example, licensed under [Apache 2.0](https://github.com/open-telemetry/opentelemetry.io/blob/main/LICENSE) by the OpenTelemetry Authors. It has been modified to export telemetry to Splunk O11y Cloud via OTLP/HTTP and adds custom metrics, configurable dice sides, health checks, and shared credential loading.

---

## What's Inside

| File | Purpose |
|---|---|
| `src/main.rs` | Hyper HTTP server with `/rolldice`, `/health` endpoints + OTEL instrumentation |
| `Cargo.toml` | Rust dependencies (hyper, tokio, opentelemetry, otlp-http exporter) |
| `../../.env` | Shared Splunk credentials (at repo root) |
| `Dockerfile` / `docker-compose.yml` | Container-ready deployment |

---

## Telemetry Emitted

### Traces (Spans)

| Span Name | Kind | Triggered When |
|---|---|---|
| `GET /rolldice` | Server | Request to roll dice |
| `GET /health` | Server | Health check request |
| `roll_dice` | Internal | Dice roll logic (child span) |

### Metrics

| Metric | Type | Description |
|---|---|---|
| `dice.rolls` | Counter | Total number of dice rolls |
| `dice.roll.value` | Histogram | Distribution of roll results |
| `http.server.requests` | Counter | Total HTTP requests served |
| `http.server.request.duration` | Histogram | Request latency (ms) |

---

## Quick Start

### Prerequisites

- [Rust](https://www.rust-lang.org/tools/install) (1.70+)
- A [Splunk Observability Cloud](https://www.splunk.com/en_us/products/observability.html) account
- A **Splunk Ingest Token** (Settings → Access Tokens → create one with *ingest* scope)

### 1. Configure credentials (shared root `.env`)

From the repo root:

```bash
cp .env.example .env
```

Edit `.env` — choose **one** of the two export modes:

#### Option A: OTEL Collector Gateway (recommended)

Route telemetry through an OTEL Collector that handles authentication and forwarding to Splunk:

```dotenv
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
# No SPLUNK_ACCESS_TOKEN needed on the app — the collector handles auth
```

#### Option B: Direct to Splunk Ingest

Send telemetry straight from the app to Splunk (requires access token):

```dotenv
SPLUNK_ACCESS_TOKEN=<your-ingest-token>
SPLUNK_REALM=us1
```

#### Resource Attributes

Add any required resource attributes via the standard OTEL env var. These are attached to **every span and metric**:

```dotenv
OTEL_RESOURCE_ATTRIBUTES=team.name=platform,app.tier=backend,region=us-east-1
OTEL_ENVIRONMENT=demo
```

### 2. Build and run

```bash
cd labs/rust-dice-server
cargo run
```

Collector gateway mode output:

```
📡 Collector gateway mode → http://otel-collector:4318
📋 Custom resource attributes: team.name=platform,app.tier=backend,region=us-east-1
✅ Traces exporter configured → http://otel-collector:4318/v1/traces
✅ Metrics exporter configured → http://otel-collector:4318/v1/metrics
🎲 Rust Dice Server listening on http://0.0.0.0:8080
```

Direct Splunk ingest mode output:

```
📡 Direct Splunk ingest mode (realm: us1)
✅ Traces exporter configured → https://ingest.us1.signalfx.com/v2/trace/otlp
✅ Metrics exporter configured → https://ingest.us1.signalfx.com/v2/datapoint/otlp
🎲 Rust Dice Server listening on http://0.0.0.0:8080
```

### 3. Generate traffic

```bash
# Roll a standard 6-sided die
curl http://localhost:8080/rolldice

# Roll a 20-sided die
curl http://localhost:8080/rolldice?sides=20

# Quick load test (100 requests)
for i in $(seq 1 100); do curl -s http://localhost:8080/rolldice; done
```

### 4. Check Splunk O11y Cloud

- **APM → Traces**: Look for the service `rust-dice-server`. You'll see `GET /rolldice` server spans with `roll_dice` child spans.
- **Infrastructure → Metrics**: Search for `dice.rolls`, `dice.roll.value`, `http.server.request.duration`.

---

## Run with Docker

```bash
# From labs/rust-dice-server/
docker compose up --build

# Or manually
docker build -t rust-dice-otel .
docker run --env-file ../../.env -p 8080:8080 rust-dice-otel
```

---

## Architecture Overview

```
┌──────────────┐     HTTP GET        ┌──────────────────────────┐
│   curl /     │───────────────────►│    Hyper + Tokio           │
│   browser    │◄───────────────────│    (Rust HTTP server)      │
└──────────────┘     dice result     │                          │
                                     │  ┌──────────────────────┐ │
                                     │  │  Manual OTEL spans   │ │
                                     │  │  + Custom metrics    │ │
                                     │  └──────────┬───────────┘ │
                                     │             │             │
                                     │  ┌──────────▼───────────┐ │
                                     │  │  OpenTelemetry SDK    │ │
                                     │  │  TracerProvider       │ │
                                     │  │  MeterProvider        │ │
                                     │  └──────────┬───────────┘ │
                                     └─────────────┼─────────────┘
                                                   │ OTLP/HTTP
                              ┌────────────────────┼────────────────────┐
                              │ Option A            │ Option B           │
                              │                     │                    │
                    ┌─────────▼──────────┐  ┌───────▼────────────┐       │
                    │  OTEL Collector     │  │  Splunk Ingest     │       │
                    │  Gateway            │  │  (direct)          │       │
                    │  :4318              │  │  X-SF-TOKEN auth   │       │
                    └─────────┬──────────┘  └───────┬────────────┘       │
                              │                     │                    │
                              └──────────┬──────────┘                    │
                              ┌──────────▼──────────┐                    │
                              │  Splunk O11y Cloud   │                   │
                              │  (APM + Metrics)     │                   │
                              └─────────────────────┘                    │
                              └─────────────────────────────────────────┘
```

---

## Customization Tips

| Want to… | Do this |
|---|---|
| Route through a collector | Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318` in `.env` |
| Add required resource attributes | Set `OTEL_RESOURCE_ATTRIBUTES=key1=val1,key2=val2` in `.env` |
| Override service name at deploy time | Set `OTEL_SERVICE_NAME=my-service` in `.env` |
| Change the port | Edit `SocketAddr::from(([0, 0, 0, 0], 8080))` in `main.rs` |
| Change export interval | Edit `with_interval(Duration::from_secs(10))` in `init_meter_provider` |
| Add more endpoints | Add match arms in the `handle` function |
| Test without any backend | Omit both `OTEL_EXPORTER_OTLP_ENDPOINT` and `SPLUNK_ACCESS_TOKEN` (warning shown) |

---

## Attribution

This lab is derived from the [OpenTelemetry Rust Getting Started Guide](https://opentelemetry.io/docs/languages/rust/getting-started/), which is part of the [opentelemetry.io](https://opentelemetry.io/) documentation. The original example is licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) by the [OpenTelemetry Authors](https://github.com/open-telemetry/opentelemetry.io/blob/main/LICENSE).

**What we changed from the original:**
- Replaced the stdout/console span exporter with OTLP/HTTP exporter targeting Splunk O11y Cloud
- Added 4 custom metrics instruments (counters + histograms)
- Added Splunk `X-SF-TOKEN` authentication header
- Added shared `.env` file loading (via `dotenvy`)
- Added configurable dice sides via `?sides=N` query parameter
- Added `/health` endpoint
- Added `deployment.environment` and `service.version` resource attributes for Splunk APM grouping

---

## License

MIT — use freely for demos and customer engagements.

The original OpenTelemetry example code is licensed under Apache 2.0.
