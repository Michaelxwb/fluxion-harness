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
    RuleBasedEvalExecutor,
)


@pytest.mark.asyncio
async def test_S_P13_02_eval_api_creates_lists_gets_and_compares_runs() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store)
        await _publish_eval_set(store)
        await trace_store.append(_trace())
        service = _service(store, trace_store)
        app = create_eval_app(service, dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
            created = await client.post(
                "/api/v1/eval/runs",
                json={
                    "run_id": "eval-run-1",
                    "eval_set_id": "support-quality",
                    "eval_set_version": "3",
                    "trace_id": "trace-eval",
                },
            )
            listed = await client.get("/api/v1/eval/runs")
            fetched = await client.get("/api/v1/eval/runs/eval-run-1")
            compared = await client.post(
                "/api/v1/eval/runs:compare",
                json={"run_id": "eval-run-1", "baseline_run_id": "eval-run-1"},
            )
    finally:
        await store.close()

    assert created.status_code == 200
    data = created.json()["data"]
    assert data["run_id"] == "eval-run-1"
    assert data["tenant_id"] == "dev"
    assert data["eval_set_id"] == "support-quality"
    assert data["runtime_profile_version"] == "7"
    assert data["score"] == 1.0
    assert data["passed"] is True

    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["run_id"] == "eval-run-1"

    assert fetched.status_code == 200
    assert fetched.json()["data"]["run_id"] == "eval-run-1"

    assert compared.status_code == 200
    assert compared.json()["data"]["score_delta"] == 0.0


@pytest.mark.asyncio
async def test_E_P13_02_eval_api_rejects_unavailable_trace_with_envelope() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store)
        await _publish_eval_set(store)
        await trace_store.append(_trace())
        service = _service(store, trace_store)
        app = create_eval_app(service, dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval") as client:
            response = await client.post(
                "/api/v1/eval/runs",
                json={
                    "run_id": "eval-run-bad",
                    "eval_set_id": "support-quality",
                    "eval_set_version": "3",
                    "trace_id": "trace-other",
                },
            )
    finally:
        await store.close()

    assert response.status_code == 404
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "Trace 不可用" in body["message"]


def _service(store: SQLiteRegistryStore, trace_store: InMemoryTraceStore) -> EvaluationApplicationService:
    return EvaluationApplicationService(
        store,
        trace_store,
        InMemoryEvalRunStore(),
        RuleBasedEvalExecutor(),
        timeout_seconds=1.0,
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


async def _publish_eval_set(store: SQLiteRegistryStore) -> None:
    await publish_resource(
        store,
        tenant_id="dev",
        kind=ResourceKind.EVAL_SET,
        resource_id="support-quality",
        version="3",
        spec={
            "name": "support-quality",
            "runtime_profile_ref": {"id": "runtime-main", "version": "7"},
            "cases": [{"id": "case-1", "input": "退款", "expected": "清晰答复"}],
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
                name="execution.step",
                tenant_id="dev",
                execution_id="execution-eval",
                trace_id="trace-eval",
                attributes={"content": "清晰答复"},
            ),
        ),
        latency_ms=12.0,
        error=None,
    )
