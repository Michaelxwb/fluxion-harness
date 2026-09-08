"""TASK-005（Phase 5）ReleaseGateService 挂 publish 管道。

S-06 / S-07 / E-04 / NFR-PERF-01（design §3.4 / §3.5：score 回退阻断、达标放行
留档、基线不可用阻断、gate 超时 ≤2s fail-closed、publish 附加延迟 ≤500ms）。

真实边界：真实 PG registry + 真实 TraceStore/EvalRunStore + 真实
RuleBasedEvalExecutor + 真实 Console publish 管道（HTTP :publish 端点）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.console import create_app as create_console_app
from fluxion.config import DevModeSettings
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ExecutionSnapshot, ResourceKind, ResourceStatus
from fluxion.runtime import InMemoryTraceStore, TraceRecord
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.eval_app import (
    EvaluationApplicationService,
    InMemoryEvalRunStore,
    RuleBasedEvalExecutor,
)
from fluxion.services.release_gate import ReleaseGateService
from tests.runtime_helpers import publish_resource, TEST_POSTGRES_DSN


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def stack(store: PostgreSQLRegistryStore) -> AsyncGenerator[dict[str, object], None]:
    """组装 eval service + release gate + console service/app（同一 store）。"""
    trace_store = InMemoryTraceStore()
    run_store = InMemoryEvalRunStore()
    await _publish_runtime_profile(store)
    await _publish_eval_set(store)
    # ADR-A011（TASK-004）：gate 只作用于 AGENT_DEFINITION——seed 可发布的
    # agent 链（model + default runtime_profile），供 gate 测试用 agent 作目标。
    await _seed_agent_chain(store)
    await trace_store.append(_trace("trace-gate"))

    evaluation = EvaluationApplicationService(
        store, trace_store, run_store, RuleBasedEvalExecutor(), timeout_seconds=1.0
    )
    # score 由 expected 与 trace 的匹配度决定：注入多条用例控制分数
    gate = ReleaseGateService(evaluation, audit_sink=store, timeout_seconds=2.0)
    console = ConsoleApplicationService(store, release_gate=gate)
    app = create_console_app(console, dev_mode=DevModeSettings(enabled=True))
    yield {
        "store": store,
        "run_store": run_store,
        "trace_store": trace_store,
        "evaluation": evaluation,
        "app": app,
    }


async def _seed_agent_chain(store: PostgreSQLRegistryStore) -> None:
    """seed 可发布的 AGENT_DEFINITION 链（ADR-A008 三层模型链；runtime_profile
    默认链已由 _publish_runtime_profile 提供 default=true）。"""
    from tests.runtime_helpers import seed_model_definition

    await seed_model_definition(store, tenant_id="dev", provider_id="test")


async def _publish_runtime_profile(store: PostgreSQLRegistryStore) -> None:
    # ADR-A010：runtime-main 标记租户默认（default=true），供无 ref 的 agent
    # 默认链解析；mechanics-only spec（TASK-A104）。
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="runtime-main",
        version="7",
        spec={"max_rounds": 8, "default": True},
    )


async def _publish_eval_set(store: PostgreSQLRegistryStore) -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.EVAL_SET,
        resource_id="gate-quality",
        version="3",
        spec={
            "name": "gate-quality",
            "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
            "cases": [{"id": "case-1", "input": "退款", "expected": "清晰答复"}],
        },
    )


def _trace(trace_id: str, *, include_answer: bool = True) -> TraceRecord:
    from fluxion.runtime.context import TraceEvent

    snapshot = ExecutionSnapshot(
        execution_id="execution-gate",
        tenant_id="dev",
        user_id="user-gate",
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
        trace_id=trace_id,
    )
    events = (
        TraceEvent(
            name="model.response",
            tenant_id="dev",
            execution_id="execution-gate",
            trace_id=trace_id,
            attributes={"answer": "清晰答复"} if include_answer else {"answer": "含糊其辞"},
        ),
    )
    return TraceRecord(
        trace_id=trace_id,
        execution_id="execution-gate",
        tenant_id="dev",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        snapshot=snapshot,
        events=events,
        latency_ms=10.0,
        error=None,
    )


async def _create_draft(store: PostgreSQLRegistryStore, *, version: str) -> None:
    """创建 AGENT_DEFINITION draft（ADR-A011：gate 只作用于 Agent）。"""
    from fluxion.resources import ResourceDefinition

    await store.put(
        ResourceDefinition(
            kind=ResourceKind.AGENT_DEFINITION,
            id="agent-main",
            tenant_id="dev",
            version=version,
            status=ResourceStatus.DRAFT,
            spec_json={
                "name": "agent-main",
                "system_prompt": "p",
                "owner": "builder",
                "model_policy": {
                    "primary_model_ref": {"id": "model.test", "version": "1"}
                },
            },
        )
    )


# ---------------------------------------------------------------------------
# S-06：候选 score 回退 → publish 阻断 + score delta 诊断
# S-07：候选达标 → publish 放行 + EvalRun 留档
# E-04：基线不可用 → 阻断 + 明确错误
# ---------------------------------------------------------------------------


class TestReleaseGatePublishPipeline:
    async def test_enforced_gate_blocks_publish_without_gate_param(
        self, stack: dict[str, object]
    ) -> None:
        """review P1-7：enforced=True 时 gate 从 opt-in 变强制策略——不带 gate
        参数的 publish fail-closed 阻断（生产装配必须开启；此前 request.gate is
        None 即完全绕过，「Eval 阻断 P0」仅为可选能力）。"""
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        gate = ReleaseGateService(
            stack["evaluation"], audit_sink=store, timeout_seconds=2.0  # type: ignore[arg-type]
        )
        enforced = ConsoleApplicationService(
            store, release_gate=gate, release_gate_enforced=True
        )
        app = create_console_app(enforced, dev_mode=DevModeSettings(enabled=True))
        await _create_draft(store, version="9")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/9:publish",
                json={},
            )
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == 38_001
        assert "强制" in body["message"]
        # 阻断后资源仍是 draft（未发布）
        resource = await store.get(
            ResourceKind.AGENT_DEFINITION, "agent-main", tenant_id="dev", version="9"
        )
        assert resource is not None and resource.status is ResourceStatus.DRAFT

        # 对照：未启用 enforcement（默认）→ 不带 gate 参数的 publish 放行（既有语义）
        legacy = ConsoleApplicationService(store, release_gate=gate)
        legacy_app = create_console_app(legacy, dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(
            transport=ASGITransport(app=legacy_app), base_url="http://console"
        ) as client:
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/9:publish",
                json={},
            )
        assert response.status_code == 200, response.text

    async def test_bs04_enforced_non_gate_kind_publishes_without_gate(
        self, stack: dict[str, object]
    ) -> None:
        """B-S-04（ADR-A011）：enforced 装配下非门 kind（RUNTIME_PROFILE）空 body
        发布成功——ReleaseGate 只作用于 AGENT_DEFINITION，不阻断其余资源。"""
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        gate = ReleaseGateService(
            stack["evaluation"], audit_sink=store, timeout_seconds=2.0  # type: ignore[arg-type]
        )
        enforced = ConsoleApplicationService(
            store, release_gate=gate, release_gate_enforced=True
        )
        app = create_console_app(enforced, dev_mode=DevModeSettings(enabled=True))
        from fluxion.resources import ResourceDefinition

        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id="runtime-other",
                tenant_id="dev",
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={"max_rounds": 8},
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            response = await client.post(
                "/api/v1/resources/runtime_profile/runtime-other/versions/1:publish",
                json={},
            )
        assert response.status_code == 200, response.text

    async def test_bs05_enforced_agent_publishes_with_valid_gate(
        self, stack: dict[str, object]
    ) -> None:
        """B-S-05（ADR-A011）：enforced 装配下 AGENT_DEFINITION 携带合法 gate →
        发布成功（与无 gate 的 409 对照，见 test_enforced_gate_blocks...）。"""
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        evaluation: EvaluationApplicationService = stack["evaluation"]  # type: ignore[assignment]
        gate = ReleaseGateService(
            evaluation, audit_sink=store, timeout_seconds=2.0
        )
        enforced = ConsoleApplicationService(
            store, release_gate=gate, release_gate_enforced=True
        )
        app = create_console_app(enforced, dev_mode=DevModeSettings(enabled=True))
        await _create_draft(store, version="20")
        baseline = await evaluation.start_run(
            _run_request("run-baseline-20", trace_id="trace-gate")
        )
        candidate = await evaluation.start_run(
            _run_request("run-candidate-20", trace_id="trace-gate")
        )
        assert baseline.score == candidate.score == 1.0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/20:publish",
                json={
                    "gate": {
                        "candidate_eval_run_id": "run-candidate-20",
                        "baseline_eval_run_id": "run-baseline-20",
                        "threshold": 0.0,
                    }
                },
            )
        assert response.status_code == 200, response.text
        resource = await store.get(
            ResourceKind.AGENT_DEFINITION, "agent-main", tenant_id="dev", version="20"
        )
        assert resource is not None and resource.status is ResourceStatus.PUBLISHED

    async def test_s06_regression_blocks_publish(self, stack: dict[str, object]) -> None:
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        evaluation: EvaluationApplicationService = stack["evaluation"]  # type: ignore[assignment]
        trace_store: InMemoryTraceStore = stack["trace_store"]  # type: ignore[assignment]
        # 弱 trace：不含期望「清晰答复」→ 候选 run score=0.0
        await trace_store.append(_trace("trace-weak", include_answer=False))
        await _create_draft(store, version="8")

        # 基线 run：score=1.0（期望出现在 trace）
        baseline = await evaluation.start_run(_run_request("run-baseline", trace_id="trace-gate"))
        assert baseline.score == 1.0
        # 候选 run：score=0.0（期望缺失 → 回退）
        candidate = await evaluation.start_run(_run_request("run-candidate", trace_id="trace-weak"))
        assert candidate.score == 0.0

        app = stack["app"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/8:publish",
                json={
                    "gate": {
                        "candidate_eval_run_id": "run-candidate",
                        "baseline_eval_run_id": "run-baseline",
                        "threshold": 0.0,
                    }
                },
            )
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == 38_001
        assert "score_delta" in body["message"] or "回退" in body["message"]
        # 阻断后资源仍是 draft（未发布）
        resource = await store.get(
            ResourceKind.AGENT_DEFINITION, "agent-main", tenant_id="dev", version="8"
        )
        assert resource is not None and resource.status is ResourceStatus.DRAFT

    async def test_s07_passing_gate_publishes_and_keeps_runs(
        self, stack: dict[str, object]
    ) -> None:
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        evaluation: EvaluationApplicationService = stack["evaluation"]  # type: ignore[assignment]
        run_store: InMemoryEvalRunStore = stack["run_store"]  # type: ignore[assignment]
        await _create_draft(store, version="9")

        baseline = await evaluation.start_run(_run_request("run-baseline-9", trace_id="trace-gate"))
        candidate = await evaluation.start_run(_run_request("run-candidate-9", trace_id="trace-gate"))
        assert baseline.score == candidate.score == 1.0

        app = stack["app"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            started = time.monotonic()
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/9:publish",
                json={
                    "gate": {
                        "candidate_eval_run_id": "run-candidate-9",
                        "baseline_eval_run_id": "run-baseline-9",
                        "threshold": 0.0,
                    }
                },
            )
            elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        # NFR-PERF-01：gate 附加延迟（含 publish）远低于 500ms 上限
        assert elapsed < 0.5, f"publish+gate 耗时 {elapsed:.3f}s 超出 500ms 预算"
        # 发布成功：版本 9 变 published
        resource = await store.get(
            ResourceKind.AGENT_DEFINITION, "agent-main", tenant_id="dev", version="9"
        )
        assert resource is not None and resource.status is ResourceStatus.PUBLISHED
        # EvalRun 记录留档（run store 可查）
        runs = await run_store.list(tenant_id="dev")
        assert {"run-baseline-9", "run-candidate-9"} <= {run.run_id for run in runs}

    async def test_e04_missing_baseline_blocks_with_clear_error(
        self, stack: dict[str, object]
    ) -> None:
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        evaluation: EvaluationApplicationService = stack["evaluation"]  # type: ignore[assignment]
        await _create_draft(store, version="10")
        await evaluation.start_run(_run_request("run-candidate-10", trace_id="trace-gate"))

        app = stack["app"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/10:publish",
                json={
                    "gate": {
                        "candidate_eval_run_id": "run-candidate-10",
                        "baseline_eval_run_id": "run-no-such-baseline",
                        "threshold": 0.0,
                    }
                },
            )
        assert response.status_code == 409
        message = response.json()["message"]
        assert "基线不可用" in message

    async def test_gate_timeout_fails_closed(self, stack: dict[str, object]) -> None:
        """compare 超时 → fail-closed 阻断（≤2s 有界），不无限等待。"""
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        await _create_draft(store, version="11")

        class _SlowEvaluation:
            """真实阻塞的 evaluation 桩（get_run/compare 挂起 → gate 超时路径）。"""

            async def get_run(self, run_id: str, *, tenant_id: str) -> object:
                del run_id, tenant_id
                await asyncio.sleep(10)
                return None

            async def compare(self, **kwargs: object) -> object:
                del kwargs
                await asyncio.sleep(10)
                raise AssertionError("unreachable")

        gate = ReleaseGateService(
            _SlowEvaluation(),  # type: ignore[arg-type]
            audit_sink=None,
            timeout_seconds=0.2,
        )
        console = ConsoleApplicationService(store, release_gate=gate)
        app = create_console_app(console, dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as client:
            started = time.monotonic()
            response = await client.post(
                "/api/v1/resources/agent_definition/agent-main/versions/11:publish",
                json={
                    "gate": {
                        "candidate_eval_run_id": "run-x",
                        "baseline_eval_run_id": "run-y",
                        "threshold": 0.0,
                    }
                },
            )
            elapsed = time.monotonic() - started
        assert response.status_code == 409
        assert "超时" in response.json()["message"] or "fail-closed" in response.json()["message"]
        assert elapsed < 2.0, "gate 超时须有界（≤2s）"

    async def test_blocked_decision_audited(self, stack: dict[str, object]) -> None:
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        evaluation: EvaluationApplicationService = stack["evaluation"]  # type: ignore[assignment]
        await _create_draft(store, version="12")
        await evaluation.start_run(_run_request("run-base-12", trace_id="trace-gate"))

        gate: ReleaseGateService = ReleaseGateService(evaluation, audit_sink=store, timeout_seconds=2.0)
        decision = await gate.evaluate(
            release_id="runtime-main@12",
            tenant_id="dev",
            candidate_eval_run_id="run-candidate-12",  # 不存在 → 候选不可用阻断
            baseline_eval_run_id="run-base-12",
            threshold=0.0,
            actor_id="admin-a",
            request_id="req-gate-12",
        )
        assert decision.blocked is True
        # 阻断决策留档 AuditLog（发布回滚复用既有治理）
        from sqlalchemy import select

        from fluxion.registry.schema import audit_logs

        engine = store._engine
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(audit_logs).where(audit_logs.c.action == "release_gate.blocked")
                )
            ).fetchall()
        assert any(row.request_id == "req-gate-12" for row in rows)


def _run_request(run_id: str, *, trace_id: str):
    from fluxion.services.eval_app import EvalRunRequest

    return EvalRunRequest(
        run_id=run_id,
        tenant_id="dev",
        eval_set_id="gate-quality",
        eval_set_version="3",
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# 服务层直连：GateDecision 形状（复用 compare() + score_delta + 原因）
# ---------------------------------------------------------------------------


class TestGateDecisionShape:
    async def test_blocked_decision_contains_delta_and_reason(
        self, stack: dict[str, object]
    ) -> None:
        evaluation: EvaluationApplicationService = stack["evaluation"]  # type: ignore[assignment]
        store: PostgreSQLRegistryStore = stack["store"]  # type: ignore[assignment]
        await _create_draft(store, version="13")
        # 两条真实 run：同 trace → delta = 0 → 放行
        await evaluation.start_run(_run_request("run-b-13", trace_id="trace-gate"))
        await evaluation.start_run(_run_request("run-c-13", trace_id="trace-gate"))
        gate = ReleaseGateService(evaluation, audit_sink=None, timeout_seconds=2.0)
        decision = await gate.evaluate(
            release_id="runtime-main@13",
            tenant_id="dev",
            candidate_eval_run_id="run-c-13",
            baseline_eval_run_id="run-b-13",
            threshold=0.0,
            actor_id="admin-a",
            request_id="req-13",
        )
        assert decision.blocked is False
        assert decision.score_delta == 0.0
        assert decision.reason
