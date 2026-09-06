"""TASK-006（golden-path-closure）B-S-08：EvalSet target 转向 agent_definition。

- target=agent_definition → published 校验通过 → EvalRun 执行产出 score；
- target 不可解析（agent 未发布）→ fail-closed（EvalTraceabilityError）。

真实边界：真实 PG RegistryStore + InMemoryTraceStore + RuleBasedEvalExecutor；
不 mock。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ExecutionSnapshot, ResourceKind
from fluxion.runtime import InMemoryTraceStore, TraceRecord
from fluxion.runtime.context import TraceEvent
from fluxion.services.eval_app import (
    EvalRunRequest,
    EvalTraceabilityError,
    EvaluationApplicationService,
    InMemoryEvalRunStore,
    RuleBasedEvalExecutor,
)
from tests.runtime_helpers import publish_resource, TEST_POSTGRES_DSN


def _service(store: PostgreSQLRegistryStore, trace_store: InMemoryTraceStore) -> EvaluationApplicationService:
    return EvaluationApplicationService(
        store, trace_store, InMemoryEvalRunStore(), RuleBasedEvalExecutor(), timeout_seconds=1.0
    )


def _trace() -> TraceRecord:
    snapshot = ExecutionSnapshot(
        execution_id="execution-eval",
        tenant_id="dev",
        user_id="user-eval",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        model_resolution={
            "routes": [
                {
                    "provider_ref": {"id": "dev.echo", "version": "1"},
                    "model_ref": {"id": "model.dev.echo", "version": "1"},
                    "model": "echo",
                }
            ]
        },
        trace_id="trace-eval",
    )
    return TraceRecord(
        trace_id="trace-eval",
        execution_id="execution-eval",
        tenant_id="dev",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        snapshot=snapshot,
        events=(
            TraceEvent(
                name="execution.step",
                tenant_id="dev",
                execution_id="execution-eval",
                trace_id="trace-eval",
                attributes={"answer": "清晰答复"},
            ),
        ),
        latency_ms=10.0,
        error=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id,version", [("support-agent", "1"), (None, None), ("other-agent", "1"), ("support-agent", "2")])
async def test_bs08_agent_definition_target_resolves_and_scores(agent_id: str | None, version: str | None) -> None:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="runtime-main",
            version="7",
            spec={"request_timeout_ms": 30_000, "max_retries": 1, "default": True},
        )
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="support-agent",
            version="1",
            spec={
                "name": "support-agent",
                "system_prompt": "p",
                "owner": "builder",
                "model_policy": {
                    "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
                },
            },
        )
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.EVAL_SET,
            resource_id="agent-quality",
            version="3",
            spec={
                "name": "agent-quality",
                "target": {"kind": "agent_definition", "id": "support-agent", "version": "1"},
                "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
                "cases": [{"id": "case-1", "input": "退款", "expected": "清晰答复"}],
            },
        )
        trace = _trace()
        snapshot = trace.snapshot.model_copy(update={"agent_definition_id": agent_id, "agent_definition_version": version})
        await trace_store.append(replace(trace, snapshot=snapshot))
        service = _service(store, trace_store)
        if (agent_id, version) != ("support-agent", "1"):
            with pytest.raises(EvalTraceabilityError):
                await service.start_run(EvalRunRequest(run_id="wrong-target", tenant_id="dev", eval_set_id="agent-quality", eval_set_version="3", trace_id="trace-eval"))
            assert await service.list_runs(tenant_id="dev") == []
            return

        record = await service.start_run(
            EvalRunRequest(
                run_id="run-agent-target",
                tenant_id="dev",
                eval_set_id="agent-quality",
                eval_set_version="3",
                trace_id="trace-eval",
            )
        )
        assert record.target_kind == "agent_definition"
        assert record.target_id == "support-agent"
        assert record.target_version == "1"
        assert record.score == 1.0
        assert record.passed is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_bs08_unresolvable_agent_target_fails_closed() -> None:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="runtime-main",
            version="7",
            spec={"request_timeout_ms": 30_000, "max_retries": 1, "default": True},
        )
        # 未发布 agent（target 指向不存在的 agent）
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.EVAL_SET,
            resource_id="dangling-agent-eval",
            version="3",
            spec={
                "name": "dangling-agent-eval",
                "target": {"kind": "agent_definition", "id": "missing-agent", "version": "1"},
                "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
                "cases": [{"id": "case-1", "input": "退款", "expected": "清晰答复"}],
            },
        )
        await trace_store.append(_trace())
        service = _service(store, trace_store)

        with pytest.raises(EvalTraceabilityError):
            await service.start_run(
                EvalRunRequest(
                    run_id="run-dangling",
                    tenant_id="dev",
                    eval_set_id="dangling-agent-eval",
                    eval_set_version="3",
                    trace_id="trace-eval",
                )
            )
    finally:
        await store.close()
