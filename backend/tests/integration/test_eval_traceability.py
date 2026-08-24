from __future__ import annotations

from tests.runtime_helpers import publish_resource

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import EvalCaseDefinition, ExecutionSnapshot, ResourceKind
from fluxion.runtime import InMemoryTraceStore, TraceRecord
from fluxion.services.eval_app import (
    EvalExecutionResult,
    EvalRunRequest,
    EvalTraceabilityError,
    EvaluationApplicationService,
    InMemoryEvalRunStore,
)


class FixedEvalExecutor:
    def __init__(self, score: float) -> None:
        self.score = score

    async def evaluate(
        self, cases: list[EvalCaseDefinition], trace: TraceRecord
    ) -> EvalExecutionResult:
        assert len(cases) == 1
        assert cases[0].id == "case-1"
        assert trace.trace_id == "trace-eval"
        return EvalExecutionResult(score=self.score, passed=self.score >= 0.8)


async def test_S_C117_eval_run_pins_eval_set_snapshot_resource_and_trace_versions() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    run_store = InMemoryEvalRunStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store, version="7")
        await _publish_eval_set(store, runtime_version="7")
        await trace_store.append(_trace(runtime_version="7"))
        service = EvaluationApplicationService(
            store,
            trace_store,
            run_store,
            FixedEvalExecutor(0.92),
            timeout_seconds=0.1,
        )

        current = await service.start_run(_request("eval-run-current"))
        baseline_service = EvaluationApplicationService(
            store,
            trace_store,
            run_store,
            FixedEvalExecutor(0.85),
            timeout_seconds=0.1,
        )
        baseline = await baseline_service.start_run(_request("eval-run-baseline"))
        regression = await service.compare(
            tenant_id="tenant-a",
            run_id=current.run_id,
            baseline_run_id=baseline.run_id,
        )
    finally:
        await store.close()

    assert current.eval_set_version == "3"
    assert current.runtime_profile_version == "7"
    assert current.trace_id == "trace-eval"
    assert current.execution_snapshot["runtime_profile_version"] == "7"
    assert regression.score_delta == 0.07


async def test_E_C114_missing_exact_version_is_rejected_without_latest_fallback() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    trace_store = InMemoryTraceStore()
    run_store = InMemoryEvalRunStore()
    await store.initialize()
    try:
        await _publish_runtime_profile(store, version="8")
        await _publish_eval_set(store, runtime_version="7")
        await trace_store.append(_trace(runtime_version="8"))
        service = EvaluationApplicationService(
            store,
            trace_store,
            run_store,
            FixedEvalExecutor(1.0),
            timeout_seconds=0.1,
        )

        try:
            await service.start_run(_request("eval-run-invalid"))
        except EvalTraceabilityError as exc:
            error = str(exc)
        else:
            raise AssertionError("missing exact version must be rejected")
    finally:
        await store.close()

    assert "runtime-main@7" in error
    assert await run_store.list(tenant_id="tenant-a") == []


async def _publish_runtime_profile(store: SQLiteRegistryStore, *, version: str) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="runtime-main",
        version=version,
        spec={
            "id": "runtime-main",
            "version": version,
            "prompt": "评测",
            "model_policy": {"provider": "dev.echo", "timeout_ms": 1000},
        },
    )


async def _publish_eval_set(
    store: SQLiteRegistryStore,
    *,
    runtime_version: str,
) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.EVAL_SET,
        resource_id="support-quality",
        version="3",
        spec={
            "name": "support-quality",
            "runtime_profile_ref": {"id": "runtime-main", "version": runtime_version},
            "cases": [{"id": "case-1", "input": "退款", "expected": "清晰答复"}],
        },
    )


def _request(run_id: str) -> EvalRunRequest:
    return EvalRunRequest(
        run_id=run_id,
        tenant_id="tenant-a",
        eval_set_id="support-quality",
        eval_set_version="3",
        trace_id="trace-eval",
    )


def _trace(*, runtime_version: str) -> TraceRecord:
    snapshot = ExecutionSnapshot(
        execution_id="execution-eval",
        tenant_id="tenant-a",
        user_id="user-eval",
        runtime_profile_id="runtime-main",
        runtime_profile_version=runtime_version,
        model_resolution={"provider": "dev.echo"},
        trace_id="trace-eval",
    )
    return TraceRecord(
        trace_id="trace-eval",
        execution_id="execution-eval",
        tenant_id="tenant-a",
        runtime_profile_id="runtime-main",
        runtime_profile_version=runtime_version,
        snapshot=snapshot,
        events=(),
        latency_ms=12.0,
        error=None,
    )
