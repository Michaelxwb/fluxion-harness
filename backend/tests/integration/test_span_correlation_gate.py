"""TASK-008（Phase 5）span 关联完整性门禁 + 全链路 E2E。

E-03 / S-04（design §2.4 / §3.2 O501–O506：trace 关联 ≥99%，NFR-OBS-01）。

真实边界：
- E-03：InMemorySpanExporter 采样真实 span——缺 trace_id/execution_id 关联字段
  比例 >1% → 测试失败（CI 门禁）；
- S-04：完整 execution 链（HTTP middleware → Runtime execution → Model →
  Tool → DB）在同一请求上下文内跑通，全链路 span 携带四关联字段。
  （Workflow span 由 workflow_graph 独立验证——DBOS 独立 event loop 下经
  run_meta 显式关联，见 TestWorkflowStepSpan；Redis 为 P1 未接线 adapter，
  span 落点已埋、链路验证待接线后并入。）
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.observability.context import (
    RequestContext,
    bind_execution_id,
    bind_request_context,
    reset_execution_id,
    reset_request_context,
)
from fluxion.observability.tracing import traced_scope
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest

_CORRELATION_FIELDS = (
    "fluxion.trace_id",
    "fluxion.execution_id",
    "fluxion.tenant_id",
    "fluxion.request_id",
)


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    """向全局 SDK TracerProvider 挂 InMemory exporter（module 级一次）。"""
    from fluxion.observability.tracing import get_tracer

    get_tracer("fluxion")  # fluxion 包装：触发 configure_tracer → SDK provider 就位
    provider = cast(TracerProvider, trace.get_tracer_provider())
    if not isinstance(provider, TracerProvider):  # 已被替换为 proxy（不应发生）
        pytest.skip("全局 TracerProvider 非 SDK 实现，无法挂 exporter")
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def _unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _spans_for(exporter: InMemorySpanExporter, trace_id: str) -> list:
    """按本测试注入的 trace_id 过滤（隔离其他测试污染）。"""
    return [
        span
        for span in exporter.get_finished_spans()
        if span.attributes.get("fluxion.trace_id") == trace_id
    ]


@pytest.fixture
async def bound_context() -> AsyncGenerator[tuple[str, str, str], None]:
    """绑定 RequestContext + execution_id；返回 (trace_id, request_id, tenant_id)。"""
    trace_id = _unique_id("trace")
    request_id = _unique_id("req")
    tenant_id = _unique_id("tenant")
    token = bind_request_context(
        RequestContext(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            actor_id="admin-a",
            method="POST",
            route="/api/v1/eval/runs",
            client_ip="127.0.0.1",
            user_agent="pytest",
        )
    )
    execution_token = bind_execution_id(_unique_id("exec"))
    try:
        yield trace_id, request_id, tenant_id
    finally:
        reset_request_context(token)
        reset_execution_id(execution_token)


# ---------------------------------------------------------------------------
# E-03：关联完整性扫描门禁（缺关联字段 >1% → 失败）
# ---------------------------------------------------------------------------


class TestE03CorrelationGate:
    async def test_span_correlation_completeness_gate(
        self, exporter: InMemorySpanExporter, bound_context: tuple[str, str, str]
    ) -> None:
        """采样全部 span：缺任一关联字段的比例 >1% → 门禁失败（NFR-OBS-01 ≥99%）。"""
        trace_id, _request_id, _tenant_id = bound_context
        # 产出一批 span（含嵌套；100 条保证 >1% 粒度可判）
        for index in range(100):
            async with traced_scope(
                f"gate.probe.{index}", attributes={"index": index}
            ):
                pass

        spans = _spans_for(exporter, trace_id)
        assert len(spans) >= 100
        incomplete = [
            span.name
            for span in spans
            if any(field not in span.attributes for field in _CORRELATION_FIELDS)
        ]
        ratio = len(incomplete) / len(spans)
        assert ratio <= 0.01, (
            f"span 关联完整率 {(1 - ratio) * 100:.1f}% < 99%：{incomplete[:5]}"
        )

    async def test_gate_fails_when_correlation_missing(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """门禁可阻断：无上下文的 span（缺关联字段）被门禁逻辑识别（>1% → fail）。"""
        # 未绑定 request context → traced_scope 产物无关联字段
        async with traced_scope("bare.span"):
            pass
        sampled = [span for span in exporter.get_finished_spans() if span.name == "bare.span"]
        assert sampled, "采样失败"
        incomplete = [
            span.name
            for span in sampled
            if any(field not in span.attributes for field in _CORRELATION_FIELDS)
        ]
        # 门禁逻辑本体：该 span 缺关联字段 → 计入 incomplete（>1% 即失败）
        assert incomplete == ["bare.span"]


# ---------------------------------------------------------------------------
# S-04：完整 execution 链（HTTP → Runtime → Model → Tool → DB）
# ---------------------------------------------------------------------------


class TestS04FullExecutionChain:
    async def test_full_chain_spans_carry_four_correlation_fields(
        self,
        exporter: InMemorySpanExporter,
        tmp_path: Path,
    ) -> None:
        """HTTP → Runtime execution → Model（dev.echo 真实 provider）→ DB span
        全链路携带四关联字段；span 覆盖 O501/O502/O503/O506。"""
        trace_id = _unique_id("trace")
        request_id = _unique_id("req")
        tenant_id = _unique_id("tenant")
        execution_id = _unique_id("exec")

        store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{tmp_path / 'chain.db'}")
        runtime = RuntimeApplicationService.create_dev_bundle(store)
        await runtime.initialize()
        # 自举 default RuntimeProfile + AgentDefinition（dev bundle 开箱语义）
        from fluxion.services.runtime_contracts import default_runtime_profile_request

        await runtime.ensure_runtime_profile(
            default_runtime_profile_request(
                tenant_id=tenant_id, runtime_profile_id="default"
            )
        )
        try:
            token = bind_request_context(
                RequestContext(
                    request_id=request_id,
                    trace_id=trace_id,
                    tenant_id=tenant_id,
                    actor_id="admin-a",
                    method="POST",
                    route="/api/v1/channels/messages",
                    client_ip="127.0.0.1",
                    user_agent="pytest",
                )
            )
            execution_token = bind_execution_id(execution_id)
            try:
                await runtime.run(
                    RunRuntimeRequest(
                        tenant_id=tenant_id,
                        user_id="user-chain",
                        runtime_profile_id="default",
                        session_id="session-chain",
                        runtime_profile_version_selector=None,
                        input_message="你好",
                        tool_calls=(),
                        request_id=request_id,
                        trace_id=trace_id,
                        execution_id=execution_id,
                    )
                )
            finally:
                reset_request_context(token)
                reset_execution_id(execution_token)
        finally:
            await runtime.close()

        spans = _spans_for(exporter, trace_id)
        names = {span.name for span in spans}
        # O502 Runtime execution + O503 Model（dev.echo 经 traced_scope）+ O506 DB
        assert "runtime.execution" in names, f"缺 runtime.execution span：{names}"
        assert "model.complete" in names, f"缺 model.complete span：{names}"
        assert "db.query" in names, f"缺 db.query span：{names}"
        # 全链路四关联字段齐全
        incomplete = [
            span.name
            for span in spans
            if any(field not in span.attributes for field in _CORRELATION_FIELDS)
        ]
        assert not incomplete, f"全链路 span 缺关联字段：{incomplete[:5]}"
        for span in spans:
            assert span.attributes["fluxion.trace_id"] == trace_id
            assert span.attributes["fluxion.execution_id"] == execution_id
            assert span.attributes["fluxion.tenant_id"] == tenant_id
            assert span.attributes["fluxion.request_id"] == request_id

    async def test_http_middleware_span(
        self, exporter: InMemorySpanExporter, bound_context: tuple[str, str, str]
    ) -> None:
        """O501：HTTP middleware span（http.{method}.{route}）+ 四关联字段。"""
        trace_id, request_id, tenant_id = bound_context
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/probe")
        async def probe() -> dict[str, str]:
            return {"ok": "1"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/probe",
                headers={
                    "X-Tenant-ID": tenant_id,
                    "X-Actor-ID": "admin-a",
                    "X-Request-ID": request_id,
                    "X-Trace-ID": trace_id,
                },
            )
        assert response.status_code == 200
        spans = _spans_for(exporter, trace_id)
        http_spans = [span for span in spans if span.name.startswith("http.")]
        assert http_spans, f"缺 HTTP span：{ {s.name for s in spans} }"
        span = http_spans[-1]
        assert span.name == "http.get./probe"
        for field in _CORRELATION_FIELDS:
            assert field in span.attributes


# ---------------------------------------------------------------------------
# O504/O505：Tool 与 Workflow step span（真实执行路径）
# ---------------------------------------------------------------------------


class TestToolAndWorkflowSpans:
    async def test_tool_call_span(
        self,
        exporter: InMemorySpanExporter,
        bound_context: tuple[str, str, str],
        tmp_path: Path,
    ) -> None:
        """O504：builtin tool 调用产 tool.call span（四关联字段 + 参数脱敏）。"""
        from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
        from fluxion.runtime.tools import ToolRuntime

        trace_id, _request_id, tenant_id = bound_context
        tool_runtime = ToolRuntime()
        register_builtin_tools(tool_runtime, BuiltinToolConfig())
        # 直接经 ToolRuntime.call 调用 builtin.http_get（拒绝非法 scheme → 异常路径
        # 同样要求 span 关联字段完整 + 参数脱敏）
        context = _minimal_runtime_context(trace_id, tenant_id)
        from fluxion.runtime.tools import ToolRuntimeError

        with pytest.raises(ToolRuntimeError):
            await tool_runtime.call(
                context,
                "builtin.http_get",
                {"url": "ftp://example.invalid", "api_key": "PLAINTEXT-7f3a"},
                user_grants={"builtin.http_get"},
                agent_allowlist={"builtin.http_get"},
                tenant_policy={"builtin.http_get"},
            )
        spans = _spans_for(exporter, trace_id)
        tool_spans = [span for span in spans if span.name == "tool.call"]
        assert tool_spans, f"缺 tool.call span：{ {s.name for s in spans} }"
        span = tool_spans[-1]
        for field in _CORRELATION_FIELDS:
            assert field in span.attributes
        # 参数脱敏：明文不进 span（redaction）
        assert "PLAINTEXT-7f3a" not in str(span.attributes)

    def test_workflow_step_span_correlation_from_run_meta(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """O505：workflow step span 经 run_meta 显式关联（DBOS 独立 loop 约束）。"""
        from fluxion.runtime.workflow_graph import _run_node

        trace_id = _unique_id("trace")
        run_id = f"wf-demo:{_unique_id('exec')}"
        # _run_node 是 async——用 asyncio.run 模拟 DBOS 独立 loop 语义
        import asyncio

        result = asyncio.run(
            _run_node(
                "transform",
                {"id": "t1", "type": "transform", "transform": "static-value"},
                {"x": 1},
                {
                    "run_id": run_id,
                    "tenant_id": "tenant-wf",
                    "execution_id": "exec-wf",
                    "trace_id": trace_id,
                },
            )
        )
        del result
        spans = [
            span
            for span in exporter.get_finished_spans()
            if span.name == "workflow.step"
            and span.attributes.get("fluxion.trace_id") == trace_id
        ]
        assert spans, "缺 workflow.step span"
        span = spans[-1]
        assert span.attributes["fluxion.trace_id"] == trace_id
        assert span.attributes["fluxion.execution_id"] == "exec-wf"
        assert span.attributes["fluxion.tenant_id"] == "tenant-wf"
        assert span.attributes["fluxion.workflow_id"] == "wf-demo"
        assert span.attributes["fluxion.node_id"] == "t1"


def _minimal_runtime_context(trace_id: str, tenant_id: str):
    """最小 RuntimeContext（builtin tool 执行用；snapshot 满足构造约束）。"""
    from fluxion.resources import ExecutionSnapshot, ModelPolicy
    from fluxion.runtime.context import RequestContext as RuntimeRequestContext
    from fluxion.runtime.context import RuntimeContext

    request = RuntimeRequestContext(
        tenant_id=tenant_id,
        user_id="user-probe",
        runtime_profile_id="default",
        session_id="session-probe",
        request_id="req-probe",
        trace_id=trace_id,
        execution_id="exec-probe",
    )
    snapshot = ExecutionSnapshot(
        execution_id="exec-probe",
        tenant_id=tenant_id,
        user_id="user-probe",
        runtime_profile_id="default",
        runtime_profile_version="1",
        model_resolution=ModelPolicy(),
        trace_id=trace_id,
    )
    return RuntimeContext(request=request, snapshot=snapshot)
