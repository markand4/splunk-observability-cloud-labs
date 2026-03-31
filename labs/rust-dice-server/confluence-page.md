# Implementing OpenTelemetry in Rust (Dice Server) with Splunk Observability Cloud

## Table of Contents

> **Confluence Users:** Use the `{toc}` macro instead of this manual TOC for auto-generated, clickable links.
> Insert it by typing `/toc` in the Confluence editor.

- Overview
- Attribution
- Architecture
- Prerequisites
- Step-by-Step Implementation Guide
  - Step 1: Create the Rust Project
  - Step 2: Configure OpenTelemetry for Splunk O11y Cloud
  - Step 3: Add Custom Metrics
  - Step 4: Instrument Request Handlers with Spans
  - Step 5: Environment Configuration
  - Step 6: Build, Run, and Validate
- Custom Metrics Reference
- Custom Spans Reference
- Best Practices
  - Instrumentation Best Practices
  - Dashboarding in Splunk O11y Cloud
  - Recommended Detectors and Alerts
  - Troubleshooting
- Key Gotchas and Lessons Learned
- Reference Links

---

## Overview

This guide walks through building a **Rust HTTP server** (using [hyper](https://hyper.rs/) + [tokio](https://tokio.rs/)) that is fully instrumented with [OpenTelemetry](https://opentelemetry.io/), exporting **traces and metrics** to [Splunk Observability Cloud](https://www.splunk.com/en_us/products/observability.html) via OTLP/HTTP.

The application is a "dice server" — you request `GET /rolldice` and it returns a random number. Every request is traced, and custom metrics track roll counts, result distribution, request counts, and latency.

**Full working example:** [github.com/markand4/splunk-observability-cloud-labs](https://github.com/markand4/splunk-observability-cloud-labs/tree/main/labs/rust-dice-server)

---

## Attribution

This lab is based on the official **OpenTelemetry Rust Getting Started** guide:

- **Source:** [https://opentelemetry.io/docs/languages/rust/getting-started/](https://opentelemetry.io/docs/languages/rust/getting-started/)
- **License:** [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) by the [OpenTelemetry Authors](https://github.com/open-telemetry/opentelemetry.io/blob/main/LICENSE)

**What was modified from the original:**

| Original (OTEL Getting Started) | This Lab (Splunk Adaptation) |
|---|---|
| Exports spans to **stdout** (`opentelemetry-stdout`) | Exports to **Splunk O11y Cloud** via OTLP/HTTP (`opentelemetry-otlp`) |
| No metrics | 4 custom metrics: `dice.rolls`, `dice.roll.value`, `http.server.requests`, `http.server.request.duration` |
| No authentication | `X-SF-TOKEN` header for Splunk ingest authentication |
| No environment config | Reads `SPLUNK_ACCESS_TOKEN`, `SPLUNK_REALM`, `OTEL_ENVIRONMENT` from `.env` |
| Fixed 6-sided die | Configurable sides via `?sides=N` query parameter |
| Single endpoint | Added `/health` endpoint |
| No resource attributes | Adds `deployment.environment`, `service.version` for Splunk APM grouping |

---

## Architecture

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
                                     ┌─────────────▼─────────────┐
                                     │  Splunk Observability      │
                                     │  Cloud (APM + Metrics)     │
                                     └────────────────────────────┘
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| Rust | 1.70+ ([install](https://www.rust-lang.org/tools/install)) |
| Cargo | Included with Rust |
| Splunk O11y Cloud account | [Sign up](https://www.splunk.com/en_us/products/observability.html) |
| Splunk Ingest Token | Settings → Access Tokens → create with **ingest** scope |
| Splunk Realm | e.g., `us0`, `us1`, `eu0` (visible in your O11y Cloud URL) |

---

## Step-by-Step Implementation Guide

### Step 1: Create the Rust Project

```bash
cargo new dice_server
cd dice_server
```

Add these dependencies to `Cargo.toml`:

```toml
[dependencies]
# HTTP server
hyper = { version = "1", features = ["full"] }
tokio = { version = "1", features = ["full"] }
http-body-util = "0.1"
hyper-util = { version = "0.1", features = ["full"] }

# Random dice
rand = "0.9"

# OpenTelemetry core
opentelemetry = "0.28"
opentelemetry_sdk = { version = "0.28", features = ["rt-tokio"] }

# OTLP exporter — use HTTP (not gRPC) for Splunk direct ingest
opentelemetry-otlp = { version = "0.28", features = ["http-proto", "reqwest-client", "trace", "metrics"] }

# Semantic conventions
opentelemetry-semantic-conventions = "0.28"

# .env file loading
dotenvy = "0.15"
```

> **Important:** Use the `http-proto` + `reqwest-client` features of `opentelemetry-otlp`. Splunk O11y Cloud direct ingest does **not** support OTLP/gRPC — you must use OTLP/HTTP (protobuf).

### Step 2: Configure OpenTelemetry for Splunk O11y Cloud

The key pieces are:
1. **Build a Resource** with `service.name`, `service.version`, and `deployment.environment`
2. **Create an OTLP/HTTP span exporter** pointing to Splunk's trace ingest endpoint
3. **Create an OTLP/HTTP metric exporter** pointing to Splunk's metric ingest endpoint
4. **Authenticate** with the `X-SF-TOKEN` header

```rust
use opentelemetry_otlp::{SpanExporter, MetricExporter, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::trace::SdkTracerProvider;
use opentelemetry_sdk::metrics::{PeriodicReader, SdkMeterProvider};
use opentelemetry_sdk::Resource;
use opentelemetry::KeyValue;

struct SplunkConfig {
    token: String,
    realm: String,
    environment: String,
}

impl SplunkConfig {
    fn from_env() -> Self {
        Self {
            token: std::env::var("SPLUNK_ACCESS_TOKEN").unwrap_or_default(),
            realm: std::env::var("SPLUNK_REALM").unwrap_or_else(|_| "us0".into()),
            environment: std::env::var("OTEL_ENVIRONMENT").unwrap_or_else(|_| "demo".into()),
        }
    }

    fn traces_endpoint(&self) -> String {
        format!("https://ingest.{}.signalfx.com/v2/trace/otlp", self.realm)
    }

    fn metrics_endpoint(&self) -> String {
        format!("https://ingest.{}.signalfx.com/v2/datapoint/otlp", self.realm)
    }

    fn headers(&self) -> HashMap<String, String> {
        let mut h = HashMap::new();
        if !self.token.is_empty() {
            h.insert("X-SF-TOKEN".into(), self.token.clone());
        }
        h
    }
}

fn init_tracer_provider(config: &SplunkConfig, resource: Resource) -> SdkTracerProvider {
    let exporter = SpanExporter::builder()
        .with_http()
        .with_endpoint(&config.traces_endpoint())
        .with_headers(config.headers())
        .build()
        .expect("Failed to create OTLP span exporter");

    SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_resource(resource)
        .build()
}

fn init_meter_provider(config: &SplunkConfig, resource: Resource) -> SdkMeterProvider {
    let exporter = MetricExporter::builder()
        .with_http()
        .with_endpoint(&config.metrics_endpoint())
        .with_headers(config.headers())
        .build()
        .expect("Failed to create OTLP metric exporter");

    let reader = PeriodicReader::builder(exporter)
        .with_interval(std::time::Duration::from_secs(10))
        .build();

    SdkMeterProvider::builder()
        .with_reader(reader)
        .with_resource(resource)
        .build()
}
```

**Key points:**
- Use `https://ingest.{realm}.signalfx.com/v2/trace/otlp` for traces
- Use `https://ingest.{realm}.signalfx.com/v2/datapoint/otlp` for metrics
- `.with_http()` — selects OTLP/HTTP transport (not gRPC)
- `.with_headers()` — attaches `X-SF-TOKEN` for Splunk authentication

### Step 3: Add Custom Metrics

Define metric instruments using the `Meter`:

```rust
use opentelemetry::metrics::{Counter, Histogram, Meter};

struct Metrics {
    roll_counter: Counter<u64>,
    request_counter: Counter<u64>,
    roll_value_histogram: Histogram<u64>,
    request_latency: Histogram<f64>,
}

fn init_metrics(meter: &Meter) -> Metrics {
    Metrics {
        roll_counter: meter
            .u64_counter("dice.rolls")
            .with_description("Total number of dice rolls")
            .with_unit("{rolls}")
            .build(),
        request_counter: meter
            .u64_counter("http.server.requests")
            .with_description("Total HTTP requests served")
            .with_unit("{requests}")
            .build(),
        roll_value_histogram: meter
            .u64_histogram("dice.roll.value")
            .with_description("Distribution of dice roll results")
            .with_unit("{value}")
            .build(),
        request_latency: meter
            .f64_histogram("http.server.request.duration")
            .with_description("Server request latency")
            .with_unit("ms")
            .build(),
    }
}
```

**Best practices:**
- Use `Counter` for monotonically increasing values (rolls, requests)
- Use `Histogram` for distributions (latency, roll values) — Splunk auto-generates p50/p90/p99
- Keep attribute cardinality low (e.g., `sides` is bounded 2–100)

### Step 4: Instrument Request Handlers with Spans

```rust
use opentelemetry::trace::{Span, SpanKind, Status, Tracer};

async fn handle(req: Request<hyper::body::Incoming>) -> Result<Response<Full<Bytes>>, Infallible> {
    let tracer = get_tracer();
    let start = std::time::Instant::now();

    // Create a server span for the entire request
    let mut span = tracer
        .span_builder(format!("{} {}", req.method(), req.uri().path()))
        .with_kind(SpanKind::Server)
        .start(tracer);

    span.set_attribute(KeyValue::new("http.method", req.method().to_string()));
    span.set_attribute(KeyValue::new("http.route", req.uri().path().to_string()));

    let response = match (req.method(), req.uri().path()) {
        (&Method::GET, "/rolldice") => {
            // roll_dice creates a child Internal span
            roll_dice(parse_sides(req.uri().query())).await
        }
        _ => { /* ... */ }
    };

    // Record request metrics
    let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;
    metrics.request_latency.record(elapsed_ms, &[KeyValue::new("http.route", path)]);

    response
}
```

**Pattern:** One `Server` span per request + child `Internal` spans for business logic.

### Step 5: Environment Configuration

The Rust app reads from the root `.env` file (shared with all labs):

```dotenv
# ../../.env (repo root)
SPLUNK_ACCESS_TOKEN=<your-ingest-token>
SPLUNK_REALM=us1
```

The app loads this in `main()`:

```rust
let root_env = std::path::PathBuf::from("../../.env");
if root_env.exists() {
    dotenvy::from_path(&root_env).ok();
} else {
    dotenvy::dotenv().ok();  // fallback: .env in current dir
}
```

### Step 6: Build, Run, and Validate

```bash
cd labs/rust-dice-server
cargo run
```

Generate traffic:

```bash
curl http://localhost:8080/rolldice
curl http://localhost:8080/rolldice?sides=20

# Load test
for i in $(seq 1 100); do curl -s http://localhost:8080/rolldice; done
```

After 30–60 seconds, check Splunk APM for the service `rust-dice-server`.

---

## Custom Metrics Reference

| Metric Name | OTEL Type | Description | Useful For |
|---|---|---|---|
| `dice.rolls` | Counter (u64) | Total dice rolls since startup | Traffic volume |
| `dice.roll.value` | Histogram (u64) | Distribution of roll results | Verifying randomness, fun dashboards |
| `http.server.requests` | Counter (u64) | Total HTTP requests served | Request rate monitoring |
| `http.server.request.duration` | Histogram (f64) | Request processing time (ms) | Latency SLAs, p99 tracking |

---

## Custom Spans Reference

| Span Name | Kind | Triggered When | Key Attributes |
|---|---|---|---|
| `GET /rolldice` | Server | Dice roll request | `http.method`, `http.route` |
| `GET /health` | Server | Health check request | `http.method`, `http.route` |
| `GET /` | Server | Root page request | `http.method`, `http.route` |
| `roll_dice` | Internal | Dice roll logic (child span) | `dice.sides`, `dice.result` |

---

## Best Practices

### Instrumentation Best Practices

1. **Use `SpanKind::Server` for request handlers, `SpanKind::Internal` for business logic.** This maps correctly to Splunk APM's service map — Server spans represent entry points.

2. **Set `deployment.environment` on your Resource.** Splunk APM uses this to separate dev/staging/prod views.

3. **Use `BatchSpanProcessor` (not simple).** The `with_batch_exporter` method avoids blocking the request handler thread on export.

4. **Use `OnceLock` for global tracer/meter access.** Rust's zero-cost abstraction for lazy static initialization — safe and efficient.

5. **Keep metric attribute cardinality bounded.** The `sides` attribute is clamped to 2–100. Avoid unbounded strings as metric attributes.

6. **Histogram for latency and distributions.** Splunk auto-generates p50, p90, p99 percentile breakdowns from Histogram data.

7. **Don't record request/response bodies in span attributes.** They can contain PII and bloat trace storage.

8. **Clone `Resource` for reuse.** Both `TracerProvider` and `MeterProvider` need the same resource — clone it rather than rebuilding.

### Dashboarding in Splunk O11y Cloud

#### Recommended Dashboard Charts

| Chart | Signal/Formula | Chart Type | Purpose |
|---|---|---|---|
| **Dice Rolls/sec** | `rate(dice.rolls)` | Line | Roll throughput |
| **Roll Distribution** | `dice.roll.value` grouped by `sides` | Histogram | Are rolls actually random? |
| **Request Rate** | `rate(http.server.requests)` grouped by `http.route` | Stacked Area | Traffic per endpoint |
| **Request Latency (p99)** | `percentile(http.server.request.duration, 99)` | Line | Tail latency |
| **Request Latency (p50)** | `percentile(http.server.request.duration, 50)` | Line | Median latency |
| **Popular Dice Sides** | `dice.rolls` grouped by `sides` | Bar | Which dice are most requested |

#### How to Create a Dashboard

1. Go to **Splunk O11y Cloud → Dashboards → Create Dashboard**
2. Add charts using the metric names above
3. Use **filters**: `sf_service:rust-dice-server` and `sf_environment:demo`

### Recommended Detectors and Alerts

| Detector Name | Condition | Severity | Why |
|---|---|---|---|
| **High Request Latency** | `p99(http.server.request.duration) > 100ms` for 5 min | Warning | Rust should be fast — this indicates a problem |
| **Error Rate Spike** | `rate(requests with status 5xx) > 5/min` | Critical | Server errors |
| **Zero Traffic** | `rate(dice.rolls) == 0` for 10 min (during business hours) | Info | Service may be down |

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No service in Splunk APM | Token or realm wrong | Check `SPLUNK_ACCESS_TOKEN` and `SPLUNK_REALM` in `../../.env`. Look for the ⚠️ warning at startup. |
| `Failed to create OTLP span exporter` | Missing TLS certs or network issue | Ensure `ca-certificates` is installed. Check firewall rules for `ingest.{realm}.signalfx.com:443`. |
| Spans appear but no metrics | Metric exporter misconfigured | Verify metrics endpoint: `/v2/datapoint/otlp` (not `/v2/trace/otlp`) |
| Metrics appear but no traces | Trace exporter misconfigured | Verify traces endpoint: `/v2/trace/otlp` (not `/v2/datapoint/otlp`) |
| `reqwest` TLS errors | Missing OpenSSL or native-tls | Run `apt-get install -y ca-certificates` in Docker |
| Compile errors on OTEL crates | Version mismatch | All `opentelemetry*` crates must use the same minor version (0.28.x) |
| `.env` not loading | Wrong working directory | Run `cargo run` from `labs/rust-dice-server/`, or set env vars manually |

---

## Key Gotchas and Lessons Learned

1. **Use OTLP/HTTP, not gRPC, for Splunk direct ingest.** The Splunk O11y Cloud ingest endpoints do not implement the OTLP/gRPC service. Use the `http-proto` feature of `opentelemetry-otlp`.

2. **Splunk ingest URLs include a path.**
   - Traces: `https://ingest.{realm}.signalfx.com/v2/trace/otlp`
   - Metrics: `https://ingest.{realm}.signalfx.com/v2/datapoint/otlp`

3. **All OTEL crate versions must match.** Using `opentelemetry 0.28` with `opentelemetry_sdk 0.27` will cause trait mismatch compile errors. Pin all `opentelemetry*` crates to the same minor version.

4. **`OnceLock` is the idiomatic way to store global tracer/meter in Rust.** It avoids `unsafe` and is zero-cost after initialization.

5. **Rust's async span model requires care.** Spans in `opentelemetry` Rust are not automatically associated with async contexts like in Python. Create spans explicitly in each handler.

6. **Docker builds need `ca-certificates`.** The OTLP/HTTP exporter uses TLS to connect to Splunk. In slim Docker images, install `ca-certificates` or the export will fail silently.

7. **Never commit `.env` files.** Always use `.env.example` with placeholder values and add `.env` to `.gitignore`.

---

## Reference Links

### OpenTelemetry Rust
- [OpenTelemetry Rust Getting Started](https://opentelemetry.io/docs/languages/rust/getting-started/) — **Original source for this lab**
- [OpenTelemetry Rust API Reference](https://docs.rs/opentelemetry/latest/opentelemetry/)
- [OpenTelemetry Rust SDK](https://docs.rs/opentelemetry_sdk/latest/opentelemetry_sdk/)
- [OTLP Exporter Crate](https://docs.rs/opentelemetry-otlp/latest/opentelemetry_otlp/)
- [OTEL Rust Examples](https://opentelemetry.io/docs/languages/rust/examples/)
- [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/)

### Splunk Observability Cloud
- [Splunk O11y Cloud — Get Started](https://docs.splunk.com/observability/en/gdi/get-data-in/application/other-languages.html)
- [Splunk O11y Cloud — OTLP Ingest](https://docs.splunk.com/observability/en/gdi/get-data-in/connect/aws/aws-apiconfig.html)
- [Splunk APM — Service Map and Traces](https://docs.splunk.com/observability/en/apm/intro-to-apm.html)
- [Splunk O11y Cloud — Detectors and Alerts](https://docs.splunk.com/observability/en/alerts-detectors-notifications/create-detectors-for-alerts.html)
- [Splunk O11y Cloud — Custom Metrics](https://docs.splunk.com/observability/en/metrics-and-metadata/metrics.html)
- [Splunk O11y Cloud — Dashboards](https://docs.splunk.com/observability/en/data-visualization/dashboards/dashboards.html)

### Hyper / Tokio
- [hyper.rs](https://hyper.rs/)
- [Tokio](https://tokio.rs/)

### Example Repository
- **Full working code:** [github.com/markand4/splunk-observability-cloud-labs](https://github.com/markand4/splunk-observability-cloud-labs/tree/main/labs/rust-dice-server)

---

*Based on the [OpenTelemetry Rust Getting Started Guide](https://opentelemetry.io/docs/languages/rust/getting-started/) (Apache 2.0). Modified for Splunk Observability Cloud.*

*Last updated: March 2026*
