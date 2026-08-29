from __future__ import annotations

import importlib
import json
import logging
import os
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import cast

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from fluxion.observability.context import current_context, current_execution_id
from fluxion.observability.redaction import redact_mapping

_CONFIGURED = False
_SERVICE_NAME = "fluxion"
_TRACING_LOGGER = logging.getLogger("fluxion.observability.tracing")


def configure_tracer(
    *,
    service_name: str = "fluxion",
    otlp_endpoint: str | None = None,
    environment: str | None = None,
) -> None:
    """配置 OpenTelemetry TracerProvider。

    - otlp_endpoint 非空（或设置 FLUXION_OTLP_ENDPOINT 环境变量）时挂 OTLP HTTP
      exporter（生产）；否则只建 TracerProvider 不 export（dev 默认，span 仍可通过
      InMemory/SpanProcessor 观察）。此前 configure_tracer 仅由 get_tracer 懒调用、
      且从不经 env 注入 endpoint → OTel span 永不导出；现从 env 解析 endpoint。
    - exporter 包缺失时降级为不 export 并记录 warning，不阻断服务启动（B-03）。
    """
    global _CONFIGURED, _SERVICE_NAME
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment or os.environ.get("FLUXION_ENV", "development"),
        }
    )
    provider = TracerProvider(resource=resource)
    endpoint = otlp_endpoint or os.environ.get("FLUXION_OTLP_ENDPOINT")
    if endpoint:
        exporter = _otlp_exporter(endpoint)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        else:
            # B-03：降级可观测——不静默、不阻断（本地 TracerProvider 仍可用）
            _TRACING_LOGGER.warning(
                "OTLP exporter 包缺失（opentelemetry-exporter-otlp-proto-http）："
                "降级为不 export（span 不上报 Collector），服务不受阻断"
            )
    trace.set_tracer_provider(provider)
    _SERVICE_NAME = service_name
    _CONFIGURED = True


def get_tracer(name: str | None = None) -> trace.Tracer:
    """获取 tracer；未显式配置时用无 exporter 的默认 TracerProvider。"""
    if not _CONFIGURED:
        configure_tracer(service_name=name or _SERVICE_NAME)
    return trace.get_tracer(name or _SERVICE_NAME)


@asynccontextmanager
async def traced_scope(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, object] | None = None,
) -> AsyncIterator[Span]:
    """统一 span 创建入口（Phase 5 TASK-007，design §3.4）。

    - 自动挂关联字段：`fluxion.trace_id` / `fluxion.execution_id` /
      `fluxion.tenant_id` / `fluxion.request_id`（来自 RequestContext ContextVar
      与 execution_id ContextVar，存在才挂）；
    - attributes 经 `observability/redaction.py` 脱敏（Secret 明文不进 span，
      RISK-P5-03）；容器值 JSON 序列化（OTel span attribute 仅接受标量）；
    - 异常：record_exception + Status(ERROR) 后原样上抛（异常不吞）。
    """
    tracer = get_tracer(_SERVICE_NAME)
    span = tracer.start_span(name, kind=kind)
    for key, value in _correlation_attributes().items():
        span.set_attribute(key, value)
    if attributes is not None:
        for attr_key, attr_value in _span_attribute_values(redact_mapping(attributes)).items():
            span.set_attribute(attr_key, attr_value)
    try:
        with trace.use_span(span, end_on_exit=False):
            yield span
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        raise
    finally:
        span.end()


@contextmanager
def traced_span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, object] | None = None,
    correlation: Mapping[str, str] | None = None,
) -> Iterator[Span]:
    """`traced_scope` 的 sync 兼容形态（O505：DBOS workflow 在独立 event loop /
    线程上下文运行，ContextVar 不保证传播——workflow 侧经 `correlation` 显式
    传入 run_meta 中的关联字段）。

    语义与 traced_scope 一致：关联字段 + redaction 脱敏 + 异常 record/ERROR 不吞。
    """
    tracer = get_tracer(_SERVICE_NAME)
    span = tracer.start_span(name, kind=kind)
    correlation_attrs = dict(correlation) if correlation is not None else _correlation_attributes()
    for key, value in correlation_attrs.items():
        span.set_attribute(key, value)
    if attributes is not None:
        for attr_key, attr_value in _span_attribute_values(redact_mapping(attributes)).items():
            span.set_attribute(attr_key, attr_value)
    try:
        with trace.use_span(span, end_on_exit=False):
            yield span
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        raise
    finally:
        span.end()


def _correlation_attributes() -> dict[str, str]:
    """四关联字段（存在才挂）；值为 str（OTel 标量）。"""
    attrs: dict[str, str] = {}
    context = current_context()
    if context is not None:
        attrs["fluxion.trace_id"] = context.trace_id
        attrs["fluxion.tenant_id"] = context.tenant_id
        attrs["fluxion.request_id"] = context.request_id
    execution_id = current_execution_id()
    if execution_id is not None:
        attrs["fluxion.execution_id"] = execution_id
    return attrs


def _span_attribute_values(redacted: Mapping[str, object]) -> dict[str, str | bool | int | float]:
    """脱敏后的 attributes 规整为 OTel 标量（容器 JSON 序列化）。"""
    values: dict[str, str | bool | int | float] = {}
    for key, value in redacted.items():
        if isinstance(value, (str, bool, int, float)):
            values[key] = value
        else:
            values[key] = json.dumps(value, ensure_ascii=False, default=str)
    return values


def _otlp_exporter(endpoint: str) -> SpanExporter | None:
    try:
        exporter_module = importlib.import_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
    except ImportError:
        return None
    exporter_class = exporter_module.OTLPSpanExporter
    return cast(SpanExporter, exporter_class(endpoint=endpoint))
