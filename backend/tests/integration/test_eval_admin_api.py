"""TASK-004（Phase 5）EvalSet 版本化 + workflow 用例 + Eval admin API。

S-05（design §2.4 / FEAT-P5-05）。

真实边界：真实 SQLite registry（resource_definitions 版本化 lifecycle）+ 真实
TraceStore + 真实 RuleBasedEvalExecutor + 真实 HTTP（ASGITransport）——
`/api/v1/admin/evals` 三端点经统一 envelope。

约束（S-P13-07）：模型评测 harness 仅 SPI 预留，默认 RuleBased，不伪造模型评测。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from tests.runtime_helpers import publish_resource

from fluxion.api.eval import create_app as create_eval_app
from fluxion.config import DevModeSettings
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ExecutionSnapshot, ResourceKind
from fluxion.runtime import InMemoryTraceStore, TraceRecord
from fluxion.runtime.context import TraceEvent
from fluxion.services.eval_app import (
    EvaluationApplicationService,
    InMemoryEvalRunStore,
    ModelEvalHarness,
    RuleBasedEvalExecutor,
)


@pytest.mark.asyncio
async def test_S05_workflow_eval_set_run_and_admin_api() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store)
        await _publish_workflow(store, version="2")
        await _publish_eval_set(store)
        await trace_store.append(_trace())

        app = create_eval_app(_service(store, trace_store), dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
            # GET /admin/evals：EvalSet 列表（版本化 lifecycle 可见）
            evals = await client.get("/api/v1/admin/evals")

            # POST /admin/evals/{id}/run：触发 workflow 用例评测
            triggered = await client.post(
                "/api/v1/admin/evals/support-quality/run",
                json={"run_id": "run-wf-1", "eval_set_version": "3", "trace_id": "trace-eval"},
            )

            # GET /admin/evals/runs：EvalRun 记录可查
            runs = await client.get("/api/v1/admin/evals/runs")
    finally:
        await store.close()

    assert evals.status_code == 200
    evals_body = evals.json()
    assert evals_body["code"] == 0
    assert "request_id" in evals_body
    items = evals_body["data"]["items"]
    assert items and items[0]["id"] == "support-quality"
    assert items[0]["version"] == "3"
    assert items[0]["status"] == "published"

    assert triggered.status_code == 200
    run = triggered.json()["data"]
    assert run["run_id"] == "run-wf-1"
    assert run["score"] == 1.0
    assert run["passed"] is True

    assert runs.status_code == 200
    runs_items = runs.json()["data"]["items"]
    assert [item["run_id"] for item in runs_items] == ["run-wf-1"]


@pytest.mark.asyncio
async def test_S05_workflow_case_partial_failure_scores_deterministically() -> None:
    """workflow 用例 expected_steps 缺失 → score 反映；同输入同 score（确定性）。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store)
        await _publish_workflow(store, version="2")
        # 两条 workflow 用例：case-ok 的 steps 全在 trace；case-bad 的 step 缺失
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.EVAL_SET,
            resource_id="mixed-quality",
            version="1",
            spec={
                "name": "mixed-quality",
                "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
                "cases": [
                    {
                        "id": "case-ok",
                        "case_type": "workflow",
                        "input": "退款",
                        "expected": "退款完成",
                        "workflow_ref": {"id": "wf-refund", "version": "2"},
                        "expected_steps": ["step_validate"],
                    },
                    {
                        "id": "case-bad",
                        "case_type": "workflow",
                        "input": "投诉",
                        "expected": "投诉升级",
                        "workflow_ref": {"id": "wf-refund", "version": "2"},
                        "expected_steps": ["step_missing"],
                    },
                ],
            },
        )
        await trace_store.append(_trace())
        app = create_eval_app(_service(store, trace_store), dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
            first = await client.post(
                "/api/v1/admin/evals/mixed-quality/run",
                json={"run_id": "run-mixed-1", "eval_set_version": "1", "trace_id": "trace-eval"},
            )
            second = await client.post(
                "/api/v1/admin/evals/mixed-quality/run",
                json={"run_id": "run-mixed-2", "eval_set_version": "1", "trace_id": "trace-eval"},
            )
    finally:
        await store.close()

    assert first.status_code == 200
    assert first.json()["data"]["score"] == 0.5
    assert first.json()["data"]["passed"] is False
    # RuleBased 确定性：同输入同 score
    assert second.json()["data"]["score"] == first.json()["data"]["score"]


@pytest.mark.asyncio
async def test_S05_workflow_ref_must_be_published_exact() -> None:
    """workflow 用例引用未发布 workflow → 拒绝（版本化 pin，规则 5/6）。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store)
        # workflow 只发布 v2，用例引用 v9（不存在）→ EvalTraceabilityError
        await _publish_workflow(store, version="2")
        await publish_resource(
            store,
            tenant_id="dev",
            kind=ResourceKind.EVAL_SET,
            resource_id="bad-ref",
            version="1",
            spec={
                "name": "bad-ref",
                "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
                "cases": [
                    {
                        "id": "case-1",
                        "case_type": "workflow",
                        "input": "退款",
                        "expected": "退款完成",
                        "workflow_ref": {"id": "wf-refund", "version": "9"},
                        "expected_steps": ["step_validate"],
                    }
                ],
            },
        )
        await trace_store.append(_trace())
        app = create_eval_app(_service(store, trace_store), dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
            response = await client.post(
                "/api/v1/admin/evals/bad-ref/run",
                json={"run_id": "run-bad", "eval_set_version": "1", "trace_id": "trace-eval"},
            )
    finally:
        await store.close()

    assert response.status_code == 404
    body = response.json()
    assert body["code"] != 0
    assert "wf-refund@9" in body["message"]


@pytest.mark.asyncio
async def test_S05_eval_set_version_increments_on_republish() -> None:
    """EvalSet 走 resource_definitions 版本化 lifecycle：publish → 版本递增。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _publish_runtime_profile(store)
        await _publish_workflow(store, version="2")
        await _publish_eval_set(store)  # v3
        await _publish_eval_set(store, version="4")  # 再发布 → v4
        service = _service(store, InMemoryTraceStore())
        app = create_eval_app(service, dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
            evals = await client.get("/api/v1/admin/evals")
    finally:
        await store.close()

    items = evals.json()["data"]["items"]
    assert items[0]["id"] == "support-quality"
    assert items[0]["version"] == "4"  # 最新版本可见（版本递增）


def test_model_eval_harness_is_spi_only() -> None:
    """模型评测 harness 仅 SPI 预留（S-P13-07：无凭据不实现不伪造）。"""
    import inspect

    from fluxion.services import eval_app

    assert inspect.isclass(ModelEvalHarness)
    # 无具体实现注册（只有 Protocol 形态）；RuleBased 为默认评测器
    concrete = [
        name
        for name, obj in vars(eval_app).items()
        if inspect.isclass(obj) and not inspect.isabstract(obj)
        and obj is not ModelEvalHarness
        and "Harness" in name
        and obj.__module__ == eval_app.__name__
    ]
    assert concrete == [], f"模型 harness 不应有具体实现（发现 {concrete}）"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _service(
    store: SQLiteRegistryStore, trace_store: InMemoryTraceStore
) -> EvaluationApplicationService:
    return EvaluationApplicationService(
        store,
        trace_store,
        InMemoryEvalRunStore(),
        RuleBasedEvalExecutor(),
        timeout_seconds=1.0,
        catalog=store,
    )


async def _publish_runtime_profile(store: SQLiteRegistryStore) -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="runtime-main",
        version="7",
        spec={
            "id": "runtime-main",
            "version": "7",
            "prompt": "评测",
            "model_policy": {"provider": "dev.echo", "timeout_ms": 1000},
        },
    )


async def _publish_workflow(store: SQLiteRegistryStore, *, version: str) -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.WORKFLOW,
        resource_id="wf-refund",
        version=version,
        spec={
            "name": "wf-refund",
            "steps": [
                {
                    "id": "step_validate",
                    "type": "capability",
                    "capability_ref": "skill:validate@1",
                    "input": {"order": "o-1"},
                },
                {
                    "id": "step_refund",
                    "type": "capability",
                    "capability_ref": "skill:refund@1",
                    "input": {"order": "o-1"},
                },
            ],
        },
    )


async def _publish_eval_set(store: SQLiteRegistryStore, *, version: str = "3") -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.EVAL_SET,
        resource_id="support-quality",
        version=version,
        spec={
            "name": "support-quality",
            "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
            "cases": [
                {
                    "id": "case-wf",
                    "case_type": "workflow",
                    "input": "退款",
                    "expected": "退款完成",
                    "workflow_ref": {"id": "wf-refund", "version": "2"},
                    "expected_steps": ["step_validate", "step_refund"],
                }
            ],
        },
    )


def _trace() -> TraceRecord:
    snapshot = ExecutionSnapshot(
        execution_id="execution-eval",
        tenant_id="dev",
        user_id="user-eval",
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        model_resolution={"provider": "dev.echo"},
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
                name="step_validate",
                tenant_id="dev",
                execution_id="execution-eval",
                trace_id="trace-eval",
                attributes={"result": "ok"},
            ),
            TraceEvent(
                name="step_refund",
                tenant_id="dev",
                execution_id="execution-eval",
                trace_id="trace-eval",
                attributes={"result": "退款完成"},
            ),
        ),
        latency_ms=12.0,
        error=None,
    )
