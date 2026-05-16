"""OTEL bootstrap for DI AI Framework agents.

Exporters are configured via env (so they're reconfigurable per §6.4 without
code changes):

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — collector address (default ``http://otel-collector:4317``)
* ``OTEL_SERVICE_NAME`` — service identifier
* ``OTEL_RESOURCE_ATTRIBUTES`` — additional resource attrs

The Collector splits CAT vs PST pipelines based on the ``audit.type`` span
attribute set by ``observability.audit``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


@dataclass(frozen=True)
class TelemetryConfig:
    service_name: str
    service_version: str = "0.1.0"
    otlp_endpoint: str | None = None
    extra_resource: dict[str, str] | None = None


def init_telemetry(config: TelemetryConfig, app: FastAPI | None = None) -> None:
    """Initialise OTEL providers, exporters, and FastAPI/httpx instrumentation.

    Idempotent: safe to call once per process. Subsequent calls are no-ops.
    """
    if getattr(init_telemetry, "_initialised", False):
        return

    endpoint = config.otlp_endpoint or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"
    )

    attrs: dict[str, str] = {
        "service.name": config.service_name,
        "service.version": config.service_version,
        "framework": "di-ai",
        "layer": "tactical",
    }
    if config.extra_resource:
        attrs.update(config.extra_resource)
    resource = Resource.create(attrs)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True))
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    init_telemetry._initialised = True  # type: ignore[attr-defined]
