# Splunk Observability Cloud Labs

A collection of hands-on labs demonstrating **OpenTelemetry** instrumentation across different languages and frameworks, all exporting **traces and metrics** to [Splunk Observability Cloud](https://www.splunk.com/en_us/products/observability.html).

---

## Labs

| # | Lab | Language | Framework | What You'll Learn |
|---|-----|----------|-----------|-------------------|
| 1 | [Python — FastAPI WebSocket Chat](labs/python-fastapi-websocket/) | Python | FastAPI, Uvicorn | Manual WebSocket span instrumentation, custom metrics (UpDownCounter, Histogram), OTLP/HTTP export to Splunk |
| 2 | [Rust — Dice Server](labs/rust-dice-server/) | Rust | Hyper (tokio) | OTEL tracing + metrics in Rust, OTLP/HTTP export to Splunk, custom span attributes |

---

## Shared Setup

All labs share the same Splunk Observability Cloud credentials via a single `.env` file at the repo root.

### 1. Clone the repo

```bash
git clone https://github.com/markand4/splunk-observability-cloud-labs.git
cd splunk-observability-cloud-labs
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
SPLUNK_ACCESS_TOKEN=<your-ingest-token>
SPLUNK_REALM=us1
```

> **Get your token:** Splunk O11y Cloud → Settings → Access Tokens → Create with **ingest** scope.

### 3. Pick a lab and follow its README

Each lab directory has its own `README.md` with setup instructions and a `confluence-page.md` with detailed best practices.

---

## Repository Structure

```
splunk-observability-cloud-labs/
├── .env.example                          # Shared Splunk credentials template
├── .env                                  # Your credentials (gitignored)
├── README.md                             # This file
├── LICENSE
└── labs/
    ├── python-fastapi-websocket/         # Lab 1: Python WebSocket + OTEL
    │   ├── README.md
    │   ├── confluence-page.md
    │   ├── app/
    │   ├── requirements.txt
    │   ├── traffic_generator.py
    │   ├── Dockerfile & docker-compose.yml
    │   └── images/
    └── rust-dice-server/                 # Lab 2: Rust Dice Server + OTEL
        ├── README.md
        ├── confluence-page.md
        ├── Cargo.toml
        ├── src/main.rs
        ├── Dockerfile & docker-compose.yml
        └── images/
```

---

## What Is Splunk Observability Cloud?

[Splunk Observability Cloud](https://www.splunk.com/en_us/products/observability.html) provides full-stack observability powered by OpenTelemetry:

- **APM** — Distributed traces, service maps, error tracking
- **Infrastructure Monitoring** — Metrics dashboards, host/container views
- **Real User Monitoring** — Front-end performance
- **Synthetics** — Proactive uptime monitoring
- **Log Observer** — Correlated logs with traces

These labs focus on **APM + Metrics** via the OpenTelemetry SDK, exporting over **OTLP/HTTP** directly to Splunk ingest endpoints.

---

## Key Patterns Across All Labs

| Pattern | Details |
|---------|---------|
| **OTLP/HTTP (not gRPC)** | Splunk direct ingest doesn't support OTLP/gRPC. All labs use OTLP/HTTP with protobuf. |
| **Splunk ingest endpoints** | Traces: `https://ingest.{realm}.signalfx.com/v2/trace/otlp` — Metrics: `https://ingest.{realm}.signalfx.com/v2/datapoint/otlp` |
| **X-SF-TOKEN header** | All requests authenticated with your Splunk ingest token |
| **deployment.environment** | Set on the OTEL Resource so Splunk APM groups services by environment |
| **Service name** | Each lab uses a unique `OTEL_SERVICE_NAME` so services appear separately in APM |

---

## Adding a New Lab

1. Create a new directory under `labs/` (e.g., `labs/go-http-server/`)
2. Add a `README.md` with quick-start instructions
3. Add a `confluence-page.md` with detailed best practices
4. Read credentials from the root `.env` (or `../../.env`)
5. Update this root README's lab table

---

## Attribution

Some labs in this collection are based on official OpenTelemetry examples. Full attribution is provided in each lab's README and source files.

| Lab | Original Source | License |
|-----|----------------|---------|
| Rust — Dice Server | [OpenTelemetry Rust Getting Started](https://opentelemetry.io/docs/languages/rust/getting-started/) | Apache 2.0 |

---

## License

MIT — use freely for demos and customer engagements.
