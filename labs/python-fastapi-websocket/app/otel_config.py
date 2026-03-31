"""
OpenTelemetry Configuration for Splunk Observability Cloud.

This module sets up tracing and metrics exporters that send telemetry
data to Splunk O11y Cloud via OTLP/HTTP.

Environment Variables Required:
  - SPLUNK_ACCESS_TOKEN:  Your Splunk Observability Cloud ingest token
  - SPLUNK_REALM:         Your Splunk realm (e.g., us0, us1, eu0)
  - OTEL_SERVICE_NAME:    Name of this service (default: fastapi-websocket-demo)
  - OTEL_ENVIRONMENT:     Deployment environment (default: demo)
"""

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
    """
    Initialize OpenTelemetry with Splunk Observability Cloud exporters.

    Returns:
        A tuple of (Tracer, Meter) configured to export to Splunk O11y Cloud.
    """

    # --- Read configuration from environment ---
    splunk_token = os.getenv("SPLUNK_ACCESS_TOKEN", "")
    splunk_realm = os.getenv("SPLUNK_REALM", "us0")
    service_name = os.getenv("OTEL_SERVICE_NAME", "fastapi-websocket-demo")
    environment = os.getenv("OTEL_ENVIRONMENT", "demo")

    # Splunk O11y Cloud OTLP/HTTP ingest endpoints
    traces_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        f"https://ingest.{splunk_realm}.signalfx.com/v2/trace/otlp"
    )
    metrics_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        f"https://ingest.{splunk_realm}.signalfx.com/v2/datapoint/otlp"
    )

    if not splunk_token:
        logger.warning(
            "⚠️  SPLUNK_ACCESS_TOKEN not set — telemetry will NOT reach Splunk O11y Cloud. "
            "Set it to your ingest token to enable export."
        )

    # --- Build the shared Resource ---
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            "deployment.environment": environment,
            "service.version": "1.0.0",
        }
    )

    # --- Headers for Splunk authentication ---
    headers = {"X-SF-TOKEN": splunk_token} if splunk_token else {}

    # --- Tracing setup ---
    span_exporter = OTLPSpanExporter(
        endpoint=traces_endpoint,
        headers=headers,
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer(service_name)

    logger.info(f"✅  Traces exporter configured → {traces_endpoint}")

    # --- Metrics setup ---
    metric_exporter = OTLPMetricExporter(
        endpoint=metrics_endpoint,
        headers=headers,
    )
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=10_000,  # export every 10 seconds
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter(service_name)

    logger.info(f"✅  Metrics exporter configured → {metrics_endpoint}")

    return tracer, meter
