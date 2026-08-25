from __future__ import annotations

import importlib
import os
from typing import cast

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

_CONFIGURED = False
_SERVICE_NAME = "fluxion"


def configure_tracer(
    *,
    service_name: str = "fluxion",
    otlp_endpoint: str | None = None,
    environment: str | None = None,
) -> None:
    """配置 OpenTelemetry TracerProvider。

    - otlp_endpoint 非空时挂 OTLP HTTP exporter（生产）；否则只建 TracerProvider 不 export
      （dev 默认，span 仍可通过 InMemory/SpanProcessor 观察）。
    - exporter 包缺失时降级为不 export 并记录 warning，不阻断服务启动。
    """
    global _CONFIGURED, _SERVICE_NAME
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment or os.environ.get("FLUXION_ENV", "development"),
        }
    )
    provider = TracerProvider(resource=resource)
    if otlp_endpoint:
        exporter = _otlp_exporter(otlp_endpoint)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _SERVICE_NAME = service_name
    _CONFIGURED = True


def get_tracer(name: str | None = None) -> trace.Tracer:
    """获取 tracer；未显式配置时用无 exporter 的默认 TracerProvider。"""
    if not _CONFIGURED:
        configure_tracer(service_name=name or _SERVICE_NAME)
    return trace.get_tracer(name or _SERVICE_NAME)


def _otlp_exporter(endpoint: str) -> SpanExporter | None:
    try:
        exporter_module = importlib.import_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
    except ImportError:
        return None
    exporter_class = exporter_module.OTLPSpanExporter
    return cast(SpanExporter, exporter_class(endpoint=endpoint))
