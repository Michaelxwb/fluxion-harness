from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from fluxion.registry import RegistryReadStore
from fluxion.resources import (
    EvalCaseDefinition,
    EvalSetDefinition,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)
from fluxion.runtime.tracing import TraceRecord, TraceStore


class EvalTraceabilityError(RuntimeError):
    pass


class EvalExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvalRunRequest:
    run_id: str
    tenant_id: str
    eval_set_id: str
    eval_set_version: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class EvalExecutionResult:
    score: float
    passed: bool


@dataclass(frozen=True, slots=True)
class EvalRunRecord:
    run_id: str
    tenant_id: str
    eval_set_id: str
    eval_set_version: str
    runtime_profile_id: str
    runtime_profile_version: str
    trace_id: str
    execution_snapshot: dict[str, object]
    score: float
    passed: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvalRegression:
    run_id: str
    baseline_run_id: str
    score_delta: float


class EvalExecutor(Protocol):
    async def evaluate(
        self,
        cases: list[EvalCaseDefinition],
        trace: TraceRecord,
    ) -> EvalExecutionResult: ...


class RuleBasedEvalExecutor:
    """Deterministic rule-based executor (the dev-bundle default).

    A case passes when its ``expected`` string appears in the observable trace
    text — event names, attribute values, tool payloads and any error message.
    Workflow cases（TASK-004）additionally require every ``expected_steps``
    marker（step/capability 结果）to appear in the trace text — Step 与 Tool
    复用 Capability Contract（US-11），评测语义不另起炉灶。
    Kept dependency-free so the Eval API works end-to-end without a model
    harness; scoring is a plain substring match over the serialized trace.
    """

    def __init__(self, *, passed_threshold: float = 1.0) -> None:
        if not 0 <= passed_threshold <= 1:
            raise ValueError("passed_threshold must be within [0, 1]")
        self._passed_threshold = passed_threshold

    async def evaluate(
        self,
        cases: list[EvalCaseDefinition],
        trace: TraceRecord,
    ) -> EvalExecutionResult:
        if not cases:
            raise EvalExecutionError("EvalSet 没有评测用例")
        corpus = _trace_text(trace).casefold()
        matched = sum(1 for case in cases if self._case_matches(case, corpus))
        score = matched / len(cases)
        return EvalExecutionResult(score=score, passed=score >= self._passed_threshold)

    @staticmethod
    def _case_matches(case: EvalCaseDefinition, corpus: str) -> bool:
        if case.expected.casefold() not in corpus:
            return False
        return all(step.casefold() in corpus for step in case.expected_steps)


class ModelEvalHarness(Protocol):
    """模型评测 harness SPI（Phase 5 TASK-004：仅预留接口形态，不实现）。

    真实模型评测需外部凭据；按 S-P13-07 约束（无凭据不实现不伪造），
    当前唯一评测器是 RuleBasedEvalExecutor。接入模型评测时实现本 SPI 并
    注入 EvaluationApplicationService，接口形态保持稳定。
    """

    async def score_case(
        self,
        case: EvalCaseDefinition,
        trace: TraceRecord,
    ) -> float:
        """对单条用例打分（0.0–1.0）；实现方必须确定性可测或有凭据支撑。"""
        ...


class EvalRunStore(Protocol):
    async def put(self, record: EvalRunRecord) -> None: ...

    async def get(self, run_id: str, *, tenant_id: str) -> EvalRunRecord | None: ...

    async def list(self, *, tenant_id: str) -> list[EvalRunRecord]: ...


class EvalSetCatalog(Protocol):
    """EvalSet 目录读取（resource_definitions 版本化 lifecycle 的列表视图）。"""

    async def list_resources(
        self,
        kind: ResourceKind,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]: ...


class InMemoryEvalRunStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], EvalRunRecord] = {}

    async def put(self, record: EvalRunRecord) -> None:
        key = (record.tenant_id, record.run_id)
        if key in self._records:
            raise EvalExecutionError(f"EvalRun 已存在: {record.run_id}")
        self._records[key] = record

    async def get(self, run_id: str, *, tenant_id: str) -> EvalRunRecord | None:
        return self._records.get((tenant_id, run_id))

    async def list(self, *, tenant_id: str) -> list[EvalRunRecord]:
        return [
            record
            for (record_tenant, _), record in self._records.items()
            if record_tenant == tenant_id
        ]


class EvaluationApplicationService:
    def __init__(
        self,
        registry: RegistryReadStore,
        traces: TraceStore,
        runs: EvalRunStore,
        executor: EvalExecutor,
        *,
        timeout_seconds: float,
        catalog: EvalSetCatalog | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._registry = registry
        self._traces = traces
        self._runs = runs
        self._executor = executor
        self._timeout_seconds = timeout_seconds
        self._catalog = catalog

    async def list_eval_sets(
        self,
        *,
        tenant_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ResourceDefinition], int]:
        """EvalSet 列表（GET /admin/evals 数据源；resource_definitions 版本化视图）。"""
        if self._catalog is None:
            raise EvalTraceabilityError("EvalSet 目录不可用（catalog 未注入）")
        if page < 1 or page_size < 1:
            raise EvalExecutionError("分页参数无效")
        return await self._catalog.list_resources(
            ResourceKind.EVAL_SET,
            tenant_id=tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def start_run(self, request: EvalRunRequest) -> EvalRunRecord:
        definition = await self._published_exact(
            ResourceKind.EVAL_SET,
            request.eval_set_id,
            request.eval_set_version,
            request.tenant_id,
        )
        eval_set = _parse_eval_set(definition)
        runtime_ref = eval_set.runtime_profile_ref
        await self._published_exact(
            ResourceKind.RUNTIME_PROFILE,
            runtime_ref.id,
            runtime_ref.version,
            request.tenant_id,
        )
        # workflow 用例（TASK-004）：workflow_ref pin 精确 published 版本（规则 5/6）
        for case in eval_set.cases:
            if case.case_type == "workflow" and case.workflow_ref is not None:
                await self._published_exact(
                    ResourceKind.WORKFLOW,
                    case.workflow_ref.id,
                    case.workflow_ref.version,
                    request.tenant_id,
                )
        trace = await self._exact_trace(request, runtime_ref.id, runtime_ref.version)
        result = await self._evaluate(eval_set, trace)
        record = _run_record(request, eval_set, trace, result)
        await self._runs.put(record)
        return record

    async def list_runs(self, *, tenant_id: str) -> list[EvalRunRecord]:
        return await self._runs.list(tenant_id=tenant_id)

    async def get_run(self, run_id: str, *, tenant_id: str) -> EvalRunRecord | None:
        return await self._runs.get(run_id, tenant_id=tenant_id)

    async def compare(
        self,
        *,
        tenant_id: str,
        run_id: str,
        baseline_run_id: str,
    ) -> EvalRegression:
        current = await self._runs.get(run_id, tenant_id=tenant_id)
        baseline = await self._runs.get(baseline_run_id, tenant_id=tenant_id)
        if current is None or baseline is None:
            raise EvalTraceabilityError("EvalRun regression 引用不存在")
        return EvalRegression(run_id, baseline_run_id, round(current.score - baseline.score, 6))

    async def _published_exact(
        self,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        tenant_id: str,
    ) -> ResourceDefinition:
        resource = await self._registry.get(
            kind,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )
        if resource is None or resource.status is not ResourceStatus.PUBLISHED:
            raise EvalTraceabilityError(f"精确资源版本不可用: {resource_id}@{version}")
        return resource

    async def _exact_trace(
        self,
        request: EvalRunRequest,
        runtime_profile_id: str,
        runtime_profile_version: str,
    ) -> TraceRecord:
        trace = await self._traces.get(request.trace_id)
        if trace is None or trace.tenant_id != request.tenant_id:
            raise EvalTraceabilityError(f"Trace 不可用: {request.trace_id}")
        exact = (trace.runtime_profile_id, trace.runtime_profile_version)
        expected = (runtime_profile_id, runtime_profile_version)
        if exact != expected:
            raise EvalTraceabilityError("Trace 与 EvalSet 精确 RuntimeProfile 版本不一致")
        return trace

    async def _evaluate(
        self,
        eval_set: EvalSetDefinition,
        trace: TraceRecord,
    ) -> EvalExecutionResult:
        try:
            return await asyncio.wait_for(
                self._executor.evaluate(eval_set.cases, trace),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise EvalExecutionError("Eval 执行超时") from exc
        except EvalExecutionError:
            raise
        except Exception as exc:
            raise EvalExecutionError(f"Eval 执行失败: {type(exc).__name__}") from exc


def _trace_text(trace: TraceRecord) -> str:
    parts: list[str] = [trace.trace_id, trace.execution_id, trace.runtime_profile_id]
    if trace.error:
        parts.append(trace.error)
    for event in trace.events:
        parts.append(event.name)
        parts.extend(_string_values(event.attributes))
    if trace.model:
        parts.extend(_string_values(trace.model))
    for tool in trace.tools:
        parts.extend(_string_values(tool))
    return "\n".join(parts)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, bool):
        return [str(value)]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_string_values(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_string_values(item))
        return result
    return []


def _parse_eval_set(resource: ResourceDefinition) -> EvalSetDefinition:
    try:
        return EvalSetDefinition.model_validate(resource.spec_json)
    except ValidationError as exc:
        raise EvalTraceabilityError(f"EvalSet Schema 无效: {exc}") from exc


def _run_record(
    request: EvalRunRequest,
    eval_set: EvalSetDefinition,
    trace: TraceRecord,
    result: EvalExecutionResult,
) -> EvalRunRecord:
    runtime_ref = eval_set.runtime_profile_ref
    snapshot = trace.snapshot.model_dump(mode="python")
    return EvalRunRecord(
        run_id=request.run_id,
        tenant_id=request.tenant_id,
        eval_set_id=request.eval_set_id,
        eval_set_version=request.eval_set_version,
        runtime_profile_id=runtime_ref.id,
        runtime_profile_version=runtime_ref.version,
        trace_id=request.trace_id,
        execution_snapshot=snapshot,
        score=result.score,
        passed=result.passed,
        created_at=datetime.now(UTC),
    )
