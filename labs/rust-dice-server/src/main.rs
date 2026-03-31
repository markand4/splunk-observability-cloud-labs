//! # Rust Dice Server with OpenTelemetry → Splunk Observability Cloud
//!
//! Based on the official OpenTelemetry Rust Getting Started example:
//!   https://opentelemetry.io/docs/languages/rust/getting-started/
//!
//! Original example code is licensed under Apache 2.0 by the OpenTelemetry Authors.
//! See: https://github.com/open-telemetry/opentelemetry.io/blob/main/LICENSE
//!
//! Modifications from the original:
//!   - Replaced stdout exporter with OTLP/HTTP exporter targeting Splunk O11y Cloud
//!   - Added custom metrics (dice.rolls, dice.roll.value, http.server.requests, request latency)
//!   - Added Splunk authentication via X-SF-TOKEN header
//!   - Added .env file loading for shared credentials
//!   - Added configurable dice sides via query parameter
//!   - Added health check endpoint
//!   - Added deployment.environment and service.version resource attributes

use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::OnceLock;

use http_body_util::Full;
use hyper::body::Bytes;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::Method;
use hyper::{Request, Response};
use hyper_util::rt::TokioIo;
use opentelemetry::global::{self, BoxedTracer};
use opentelemetry::metrics::{Counter, Histogram, Meter};
use opentelemetry::trace::{Span, SpanKind, Status, Tracer};
use opentelemetry::KeyValue;
use opentelemetry_otlp::{MetricExporter, SpanExporter, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::metrics::{PeriodicReader, SdkMeterProvider};
use opentelemetry_sdk::trace::SdkTracerProvider;
use opentelemetry_sdk::Resource;
use rand::Rng;
use reqwest::blocking::Client as BlockingClient;
use tokio::net::TcpListener;

// ─── Configuration ───────────────────────────────────────────────────

const SERVICE_NAME: &str = "rust-dice-server";
const SERVICE_VERSION: &str = "1.0.0";

/// Telemetry export mode:
/// - **Collector Gateway:** Set `OTEL_EXPORTER_OTLP_ENDPOINT` to your collector
///   (e.g. `http://otel-collector:4318`). No access token needed on the app —
///   the collector handles authentication and routing to Splunk.
/// - **Direct to Splunk:** Omit `OTEL_EXPORTER_OTLP_ENDPOINT` and set
///   `SPLUNK_ACCESS_TOKEN` + `SPLUNK_REALM`. The app sends directly to Splunk
///   ingest endpoints with `X-SF-TOKEN` auth.
struct ExportConfig {
    token: String,
    realm: String,
    environment: String,
    /// When set, all OTLP goes to this collector; Splunk-specific headers are skipped.
    collector_endpoint: Option<String>,
}

impl ExportConfig {
    fn from_env() -> Self {
        let collector_endpoint = std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT").ok();
        Self {
            token: std::env::var("SPLUNK_ACCESS_TOKEN").unwrap_or_default(),
            realm: std::env::var("SPLUNK_REALM").unwrap_or_else(|_| "us0".to_string()),
            environment: std::env::var("OTEL_ENVIRONMENT").unwrap_or_else(|_| "demo".to_string()),
            collector_endpoint,
        }
    }

    /// Returns true when routing through a collector gateway.
    fn uses_collector(&self) -> bool {
        self.collector_endpoint.is_some()
    }

    fn traces_endpoint(&self) -> String {
        // Per-signal override takes priority
        if let Ok(ep) = std::env::var("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") {
            return ep;
        }
        // Collector gateway — standard OTLP path
        if let Some(ref base) = self.collector_endpoint {
            return format!("{}/v1/traces", base.trim_end_matches('/'));
        }
        // Fallback: Splunk direct ingest
        format!(
            "https://ingest.{}.signalfx.com/v2/trace/otlp",
            self.realm
        )
    }

    fn metrics_endpoint(&self) -> String {
        if let Ok(ep) = std::env::var("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") {
            return ep;
        }
        if let Some(ref base) = self.collector_endpoint {
            return format!("{}/v1/metrics", base.trim_end_matches('/'));
        }
        format!(
            "https://ingest.{}.signalfx.com/v2/datapoint/otlp",
            self.realm
        )
    }

    /// Headers for Splunk direct ingest; empty when using a collector gateway.
    fn headers(&self) -> HashMap<String, String> {
        let mut h = HashMap::new();
        if !self.uses_collector() && !self.token.is_empty() {
            h.insert("X-SF-TOKEN".to_string(), self.token.clone());
        }
        h
    }
}

// ─── OpenTelemetry Setup ─────────────────────────────────────────────

/// Build the OTEL Resource with standard + custom attributes.
///
/// Fixed attributes set in code:
///   - `service.name`              (from SERVICE_NAME const)
///   - `service.version`           (from SERVICE_VERSION const)
///   - `deployment.environment`    (from OTEL_ENVIRONMENT env var)
///
/// Additional / required attributes can be injected at deploy time
/// via the standard OTEL env var:
///
///   OTEL_RESOURCE_ATTRIBUTES="team.name=platform,app.tier=backend,region=us-east-1"
///
/// The format is comma-separated `key=value` pairs. Any attributes
/// defined here will be attached to **every span and metric** exported
/// by this service.
fn build_resource(config: &ExportConfig) -> Resource {
    let mut builder = Resource::builder()
        .with_attribute(KeyValue::new(
            opentelemetry_semantic_conventions::attribute::SERVICE_NAME,
            std::env::var("OTEL_SERVICE_NAME").unwrap_or_else(|_| SERVICE_NAME.to_string()),
        ))
        .with_attribute(KeyValue::new("service.version", SERVICE_VERSION))
        .with_attribute(KeyValue::new(
            "deployment.environment",
            config.environment.clone(),
        ));

    // ── Custom resource attributes from OTEL_RESOURCE_ATTRIBUTES ──
    // Format: key1=value1,key2=value2,...
    if let Ok(attrs) = std::env::var("OTEL_RESOURCE_ATTRIBUTES") {
        for pair in attrs.split(',') {
            let pair = pair.trim();
            if let Some((key, value)) = pair.split_once('=') {
                let key = key.trim().to_string();
                let value = value.trim().to_string();
                if !key.is_empty() {
                    builder = builder.with_attribute(KeyValue::new(key, value));
                }
            }
        }
    }

    builder.build()
}

fn init_tracer_provider(config: &ExportConfig, resource: Resource) -> SdkTracerProvider {
    let http_client = BlockingClient::new();
    let exporter = SpanExporter::builder()
        .with_http()
        .with_http_client(http_client)
        .with_endpoint(&config.traces_endpoint())
        .with_headers(config.headers())
        .build()
        .expect("Failed to create OTLP span exporter");

    SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_resource(resource)
        .build()
}

fn init_meter_provider(config: &ExportConfig, resource: Resource) -> SdkMeterProvider {
    let http_client = BlockingClient::new();
    let exporter = MetricExporter::builder()
        .with_http()
        .with_http_client(http_client)
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

// ─── Global Accessors ────────────────────────────────────────────────

fn get_tracer() -> &'static BoxedTracer {
    static TRACER: OnceLock<BoxedTracer> = OnceLock::new();
    TRACER.get_or_init(|| global::tracer(SERVICE_NAME))
}

struct Metrics {
    roll_counter: Counter<u64>,
    request_counter: Counter<u64>,
    roll_value_histogram: Histogram<u64>,
    request_latency: Histogram<f64>,
}

fn get_metrics() -> &'static Metrics {
    static METRICS: OnceLock<Metrics> = OnceLock::new();
    METRICS.get_or_init(|| {
        let meter: Meter = global::meter(SERVICE_NAME);

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
    })
}

// ─── Request Handlers ────────────────────────────────────────────────

async fn roll_dice(
    num_sides: u32,
) -> Result<Response<Full<Bytes>>, Infallible> {
    let tracer = get_tracer();
    let metrics = get_metrics();

    let mut span = tracer
        .span_builder("roll_dice")
        .with_kind(SpanKind::Internal)
        .start(tracer);

    let result = rand::rng().random_range(1..=num_sides);

    span.set_attribute(KeyValue::new("dice.sides", num_sides as i64));
    span.set_attribute(KeyValue::new("dice.result", result as i64));
    span.set_status(Status::Ok);

    metrics
        .roll_counter
        .add(1, &[KeyValue::new("sides", num_sides as i64)]);
    metrics
        .roll_value_histogram
        .record(result as u64, &[KeyValue::new("sides", num_sides as i64)]);

    Ok(Response::new(Full::new(Bytes::from(result.to_string()))))
}

async fn health_check() -> Result<Response<Full<Bytes>>, Infallible> {
    Ok(Response::new(Full::new(Bytes::from(
        r#"{"status":"healthy","service":"rust-dice-server"}"#,
    ))))
}

async fn handle(req: Request<hyper::body::Incoming>) -> Result<Response<Full<Bytes>>, Infallible> {
    let tracer = get_tracer();
    let metrics = get_metrics();
    let start = std::time::Instant::now();

    let method = req.method().to_string();
    let path = req.uri().path().to_string();

    let mut span = tracer
        .span_builder(format!("{} {}", method, path))
        .with_kind(SpanKind::Server)
        .start(tracer);

    span.set_attribute(KeyValue::new("http.method", method.clone()));
    span.set_attribute(KeyValue::new("http.route", path.clone()));

    let response = match (req.method(), req.uri().path()) {
        (&Method::GET, "/rolldice") => {
            // Default: 6-sided die
            let sides = parse_sides(req.uri().query());
            roll_dice(sides).await
        }
        (&Method::GET, "/health") => health_check().await,
        (&Method::GET, "/") => Ok(Response::new(Full::new(Bytes::from(
            "🎲 Rust Dice Server with OpenTelemetry → Splunk O11y Cloud\n\n\
             Endpoints:\n  GET /rolldice       — Roll a 6-sided die (or ?sides=N)\n  \
             GET /health         — Health check\n",
        )))),
        _ => {
            span.set_status(Status::error("Not Found"));
            span.set_attribute(KeyValue::new("http.status_code", 404));
            Ok(Response::builder()
                .status(404)
                .body(Full::new(Bytes::from("Not Found")))
                .unwrap())
        }
    };

    let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;
    metrics.request_counter.add(
        1,
        &[
            KeyValue::new("http.method", method),
            KeyValue::new("http.route", path.clone()),
        ],
    );
    metrics.request_latency.record(
        elapsed_ms,
        &[KeyValue::new("http.route", path)],
    );

    response
}

fn parse_sides(query: Option<&str>) -> u32 {
    query
        .and_then(|q| {
            q.split('&')
                .find_map(|pair| {
                    let mut kv = pair.splitn(2, '=');
                    match (kv.next(), kv.next()) {
                        (Some("sides"), Some(v)) => v.parse::<u32>().ok(),
                        _ => None,
                    }
                })
        })
        .unwrap_or(6)
        .clamp(2, 100)
}

// ─── Main ────────────────────────────────────────────────────────────

fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Load .env from repo root (two levels up) or current dir
    let root_env = std::path::PathBuf::from("../../.env");
    if root_env.exists() {
        dotenvy::from_path(&root_env).ok();
    } else {
        dotenvy::dotenv().ok();
    }

    let config = ExportConfig::from_env();

    // ── Validate export configuration ──
    if config.uses_collector() {
        println!(
            "📡 Collector gateway mode → {}",
            config.collector_endpoint.as_deref().unwrap_or("")
        );
    } else if config.token.is_empty() {
        eprintln!(
            "⚠️  SPLUNK_ACCESS_TOKEN not set and no OTEL_EXPORTER_OTLP_ENDPOINT configured.\n   \
             Telemetry will NOT reach any backend.\n   \
             Set OTEL_EXPORTER_OTLP_ENDPOINT for collector mode, or SPLUNK_ACCESS_TOKEN for direct ingest."
        );
    } else {
        println!("📡 Direct Splunk ingest mode (realm: {})", config.realm);
    }

    // ── Show resource attributes ──
    let resource = build_resource(&config);
    if let Ok(attrs) = std::env::var("OTEL_RESOURCE_ATTRIBUTES") {
        println!("📋 Custom resource attributes: {}", attrs);
    }

    // Initialize tracing (MUST happen before Tokio runtime starts,
    // because the blocking reqwest client creates its own internal runtime)
    let tracer_provider = init_tracer_provider(&config, resource.clone());
    global::set_tracer_provider(tracer_provider.clone());
    println!("✅ Traces exporter configured → {}", config.traces_endpoint());

    // Initialize metrics
    let meter_provider = init_meter_provider(&config, resource);
    global::set_meter_provider(meter_provider.clone());
    println!(
        "✅ Metrics exporter configured → {}",
        config.metrics_endpoint()
    );

    // Now start the Tokio runtime for the HTTP server
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async {
        let addr = SocketAddr::from(([0, 0, 0, 0], 8080));
        let listener = TcpListener::bind(addr).await.unwrap();
        println!("🎲 Rust Dice Server listening on http://{}", addr);
        println!("   Try: curl http://localhost:8080/rolldice");
        println!("         curl \"http://localhost:8080/rolldice?sides=20\"");

        loop {
            let (stream, _) = listener.accept().await.unwrap();
            let io = TokioIo::new(stream);

            tokio::task::spawn(async move {
                if let Err(err) = http1::Builder::new()
                    .serve_connection(io, service_fn(handle))
                    .await
                {
                    eprintln!("Error serving connection: {:?}", err);
                }
            });
        }
    })
}
