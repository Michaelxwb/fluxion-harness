"""TASK-007（Phase 5）traced_scope 上下文助手 + OTLP Collector 接线。

B-03 / O507 支撑断言（design §3.4 traced_scope 签名 + §4 部署与运维）。

真实边界：
- B-03：真实 exporter 依赖探测路径（importlib ImportError → 降级 + warning）；
- traced_scope：真实 OTel SDK span（span.attributes 可断言）、真实 RequestContext
  ContextVar、真实 redaction 脱敏（Secret 明文不进 span）。
"""

from __future__ import annotations

import importlib
import logging

import pytest
from opentelemetry.trace import SpanKind, StatusCode

from fluxion.observability.context import (
    RequestContext,
    bind_execution_id,
    bind_request_context,
    reset_execution_id,
    reset_request_context,
)
from fluxion.observability.tracing import configure_tracer, traced_scope


@pytest.fixture
async def bound_context() -> None:
    """绑定 RequestContext + execution_id（测试后复位）。"""
    token = bind_request_context(
        RequestContext(
            request_id="req-scope-1",
            trace_id="trace-scope-1",
            tenant_id="tenant-a",
            actor_id="admin-a",
            method="POST",
            route="/api/v1/eval/runs",
            client_ip="127.0.0.1",
            user_agent="pytest",
        )
    )
    exec_token = bind_execution_id("exec-scope-1")
    yield
    reset_request_context(token)
    reset_execution_id(exec_token)


# ---------------------------------------------------------------------------
# B-03：OTLP exporter 缺失 → 降级不 export + warning，服务不阻断
# ---------------------------------------------------------------------------


class TestB03OtlpExporterDegradation:
    def test_missing_exporter_returns_none(self) -> None:
        from fluxion.observability.tracing import _otlp_exporter

        real_import = importlib.import_module

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if "otlp" in name:
                raise ImportError(f"No module named {name!r} (simulated)")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        importlib.import_module = _fake_import  # type: ignore[assignment]
        try:
            exporter = _otlp_exporter("http://localhost:4318/v1/traces")
        finally:
            importlib.import_module = real_import  # type: ignore[assignment]
        assert exporter is None, "exporter 包缺失时应返回 None（降级不 export）"

    def test_configure_tracer_with_missing_exporter_warns_not_blocks(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """endpoint 配置但 exporter 包缺失 → warning + 不抛异常（B-03 fail-soft）。"""
        from fluxion.observability import tracing as tracing_module

        monkeypatch.setattr(
            tracing_module, "_otlp_exporter", lambda endpoint: None
        )
        caplog.set_level(logging.WARNING, logger="fluxion.observability.tracing")
        # 不抛异常即「服务不阻断」
        configure_tracer(otlp_endpoint="http://collector:4318/v1/traces")
        warnings = [
            record.getMessage()
            for record in caplog.records
            if "OTLP" in record.getMessage()
        ]
        assert warnings, "exporter 缺失必须发 warning（可观测降级，不静默）"

    def test_configure_tracer_without_endpoint_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """dev 无 endpoint：本地 TracerProvider，无 warning。"""
        caplog.set_level(logging.WARNING, logger="fluxion.observability.tracing")
        configure_tracer()
        assert not [r for r in caplog.records if "OTLP" in r.getMessage()]


# ---------------------------------------------------------------------------
# traced_scope：统一 span 入口（关联字段 + 脱敏）
# ---------------------------------------------------------------------------


class TestTracedScope:
    async def test_span_carries_four_correlation_fields(self, bound_context: None) -> None:
        async with traced_scope("test.operation") as span:
            attrs = dict(span.attributes)
        assert attrs["fluxion.trace_id"] == "trace-scope-1"
        assert attrs["fluxion.tenant_id"] == "tenant-a"
        assert attrs["fluxion.request_id"] == "req-scope-1"
        assert attrs["fluxion.execution_id"] == "exec-scope-1"
        assert span.name == "test.operation"

    async def test_span_kind_and_attributes(
        self, bound_context: None
    ) -> None:
        async with traced_scope(
            "db.query",
            kind=SpanKind.CLIENT,
            attributes={"db.system": "sqlite", "rows": 42},
        ) as span:
            attrs = dict(span.attributes)
        assert span.kind is SpanKind.CLIENT
        assert attrs["db.system"] == "sqlite"
        assert attrs["rows"] == 42

    async def test_sensitive_attributes_redacted(self, bound_context: None) -> None:
        """Secret 明文不进 span（redaction 全链路，RISK-P5-03）。"""
        marker = "PLAINTEXT-SECRET-7f3a"
        async with traced_scope(
            "secret.resolve",
            attributes={
                "credential": marker,
                "nested": {"api_key": marker, "keep": "ok"},
                "plain": "visible",
            },
        ) as span:
            attrs = dict(span.attributes)
        assert attrs["credential"] == "[REDACTED]"
        assert marker not in str(attrs)
        assert attrs["plain"] == "visible"

    async def test_exception_recorded_and_propagates(self, bound_context: None) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with traced_scope("failing.op"):
                raise RuntimeError("boom")
        # 异常状态/事件断言由 test_traced_scope_marks_error_status 承载；
        # 此处断言异常传播本身（traced_scope 不吞异常）

    async def test_without_context_no_correlation_fields(self) -> None:
        async with traced_scope("bare.op") as span:
            attrs = dict(span.attributes)
        assert "fluxion.trace_id" not in attrs
        assert "fluxion.tenant_id" not in attrs

    async def test_scope_sets_current_span(self, bound_context: None) -> None:
        from opentelemetry import trace as otel_trace

        async with traced_scope("outer.op") as span:
            current = otel_trace.get_current_span()
            assert current is span, "traced_scope 须将 span 设为 current（嵌套父子关系）"


# ---------------------------------------------------------------------------
# 异常路径的 span 状态断言（B-03 之外的 traced_scope 行为完整性）
# ---------------------------------------------------------------------------


async def test_traced_scope_marks_error_status() -> None:
    """span 异常 → Status(ERROR) + 异常事件记录（可观测失败）。"""
    from opentelemetry.trace import Span

    span_ref: dict[str, Span] = {}
    with pytest.raises(ValueError, match="bad-input"):
        async with traced_scope("err.op") as span:
            span_ref["span"] = span
            raise ValueError("bad-input")
    span = span_ref["span"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.events, "异常须 record_exception（span 事件留痕）"


async def test_execution_id_binding_scoped() -> None:
    """execution_id ContextVar 绑定/复位正确（运行期由 TASK-008 O502 接线）。"""
    token = bind_execution_id("exec-x")
    try:
        async with traced_scope("with.exec") as span:
            assert span.attributes["fluxion.execution_id"] == "exec-x"
    finally:
        reset_execution_id(token)
    async with traced_scope("without.exec") as span:
        assert "fluxion.execution_id" not in dict(span.attributes)
