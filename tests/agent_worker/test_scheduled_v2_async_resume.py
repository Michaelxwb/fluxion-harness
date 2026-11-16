"""TASK-020：ASYNC 多 operation 的等待、恢复与补扫（真实 PG + 真实并发会话）。

真实验证边界：

- **真实 PostgreSQL**（隔离租户）：等待行、`task_execution_checkpoint`、`task_event`、claim
  与补扫的行锁都是真行；并发用**真的并发会话**（两个连接同时扫同一行）。
- **生产代码**：`AgenticTaskExecutor` / `WorkerLoop._wait` / `WorkerLoop._supervise_task` /
  `AsyncResultSweeper` / `TaskClaimer` / `load_resume_state` 全部是生产实现。
- **替身只在网络边界**：模型 provider 是脚本化传输；Runtime API-30 的 operation 读写是内存里的
  同形实现（调用身份唯一、`input_hash` 冲突、结果按 `event_id` 幂等——它的真实语义由 TASK-007
  的验收覆盖）。Worker 的等待状态、水位与 checkpoint 一律真落库。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_agent_core.tools.pipeline import compute_args_hash
from muad_agent_worker.application.agentic.async_results import (
    advance_watermarks,
    all_settled,
    payload_from_watermarks,
)
from muad_agent_worker.application.agentic.checkpoints import (
    ExecutionIdentity,
    TaskCheckpointStore,
)
from muad_agent_worker.application.agentic.events import TaskEventSink
from muad_agent_worker.application.agentic.run import (
    AgenticDependencies,
    ResumeState,
    load_resume_state,
)
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_agent_worker.infrastructure.task_operation_client import TaskOperationError
from muad_agent_worker.worker.agentic_executor import AgenticTaskExecutor
from muad_agent_worker.worker.agentic_skills import AgenticSkillTools
from muad_agent_worker.worker.async_sweep import AsyncResultSweeper
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.dispatch import TaskTypeExecutor
from muad_agent_worker.worker.execution_outcomes import OutcomeKind, TaskOutcome, interpret_execution
from muad_agent_worker.worker.executor import TaskExecutionError
from muad_agent_worker.worker.reminder import ReminderExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_contracts import (
    CompletionMode,
    OperationStatus,
    OperationWatermark,
    TaskStatus,
    TaskToolOperationList,
    TaskToolOperationOwner,
    TaskToolOperationResult,
    TaskToolOperationView,
    TerminalStatus,
    WaitKind,
)
from sqlalchemy import select

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_agentic_adapter import (
    STAGING_TOOL,
    WORKER_INSTANCE,
    RecordingCapacity,
    RecordingCredentials,
    StubResolver,
    agentic_snapshot,
    insert_task,
)
from tests.kernel_adapter_contract import ScriptedModelProvider, text_reply, tool_call_reply

pytestmark = pytest.mark.integration

RECEIPT = '{"async_operation": {"operation_id": "00000000-0000-4000-8000-000000000009"}}'


def staging_call(call_id: str, query: str = "本周") -> ModelToolCall:
    return ModelToolCall(call_id, STAGING_TOOL, {"q": query})


# ---------------------------------------------------------------------------
# Runtime API-30 的内存同形实现（只替身网络边界）
# ---------------------------------------------------------------------------


@dataclass
class _Operation:
    operation_id: uuid.UUID
    call_id: str
    tool_name: str
    input_hash: str
    execution_id: uuid.UUID | None = None
    result: TaskToolOperationResult | None = None


class MemoryOperations:
    """调用身份唯一 + `input_hash` 冲突 + 结果按 `event_id` 幂等（与 API-30 同口径）。"""

    def __init__(self) -> None:
        self.submits: list[tuple[str, str, dict[str, Any]]] = []
        self.operations: dict[str, _Operation] = {}
        self.unavailable = False
        #: 下一次读取 operation 列表**之前**/之后把结果补上：两种到达时序。
        self.complete_before_read: list[str] = []
        self.complete_after_read: list[str] = []
        self._sequence = 0

    async def submit(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        execution_id: uuid.UUID,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        completion_mode: CompletionMode = CompletionMode.JOIN,
    ) -> TaskToolOperationView:
        self.submits.append((call_id, tool_name, dict(arguments)))
        fingerprint = compute_args_hash(arguments)
        existing = self.operations.get(call_id)
        if existing is not None:
            if existing.input_hash != fingerprint:
                raise TaskOperationError("input mismatch", code="TOOL_OPERATION_INPUT_MISMATCH")
            existing.execution_id = execution_id
            return self._view(existing, completion_mode)
        created = _Operation(uuid.uuid4(), call_id, tool_name, fingerprint, execution_id)
        self.operations[call_id] = created
        return self._view(created, completion_mode)

    async def list_operations(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        execution_id: uuid.UUID,
        ready: bool | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> TaskToolOperationList:
        if self.unavailable:
            raise TaskOperationError("runtime unavailable", code="GATEWAY_TIMEOUT")
        for call_id in self.complete_before_read:
            self.complete(call_id)
        self.complete_before_read.clear()
        items = [self._view(op, CompletionMode.JOIN) for op in self.operations.values()]
        for call_id in self.complete_after_read:
            self.complete(call_id)
        self.complete_after_read.clear()
        if ready is True:
            items = [item for item in items if item.result is not None]
        return TaskToolOperationList(items=items, page=page, page_size=page_size, total=len(items))

    async def get(
        self, *, tenant_id: str, actor_user_id: uuid.UUID, operation_id: uuid.UUID
    ) -> TaskToolOperationView:
        for operation in self.operations.values():
            if operation.operation_id == operation_id:
                return self._view(operation, CompletionMode.JOIN)
        raise TaskOperationError("operation not found", code="COMMON_NOT_FOUND")

    def complete(
        self,
        call_id: str,
        text: str | None = None,
        *,
        error_code: str | None = None,
        late: bool = False,
    ) -> None:
        operation = self.operations[call_id]
        self._sequence += 1
        operation.result = TaskToolOperationResult(
            event_id=uuid.uuid4(),
            task_event_id=uuid.uuid4(),
            task_event_seq=self._sequence,
            terminal_status=TerminalStatus.FAILED if error_code else TerminalStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            late=late,
            result=None if error_code else {"text": text or f"{call_id}-结果"},
            error_code=error_code,
        )

    def _view(self, operation: _Operation, mode: CompletionMode) -> TaskToolOperationView:
        result = operation.result
        status = OperationStatus.SUBMITTED
        if result is not None:
            status = (
                OperationStatus.LATE if result.late else OperationStatus(result.terminal_status.value)
            )
        return TaskToolOperationView(
            operation_id=operation.operation_id,
            owner=TaskToolOperationOwner(task_execution_id=operation.execution_id),
            source_tool_call_id=operation.call_id,
            tool_name=operation.tool_name,
            completion_mode=mode,
            status=status,
            cancel_requested=False,
            result=result,
        )


class ScriptedFactory:
    def __init__(self) -> None:
        self.script: list[Any] = []
        self.provider: ScriptedModelProvider | None = None

    def __call__(self, **_: Any) -> ScriptedModelProvider:
        self.provider = ScriptedModelProvider(self.script)
        return self.provider


@dataclass
class AsyncHarness:
    tenant: TenantContext
    operations: MemoryOperations
    factory: ScriptedFactory
    http: httpx.AsyncClient
    executor: AgenticTaskExecutor

    @property
    def model_calls(self) -> int:
        return self.factory.provider.call_count if self.factory.provider is not None else 0

    def last_tool_messages(self) -> dict[str, str]:
        assert self.factory.provider is not None and self.factory.provider.requests
        messages = self.factory.provider.requests[-1].messages
        return {
            str(message.tool_call_id): message.content
            for message in messages
            if message.role is ModelRole.TOOL
        }

    async def outcome(self, task: TaskExecution) -> TaskOutcome:
        envelope = await self.executor.execute(task)
        return interpret_execution(envelope, now=datetime.now(UTC))


def build_harness(tenant: TenantContext, http: httpx.AsyncClient) -> AsyncHarness:
    operations = MemoryOperations()
    factory = ScriptedFactory()
    deps = AgenticDependencies(
        settings=tenant.settings,
        sessions=tenant.session_factory,
        artifact_resolver=StubResolver(),  # type: ignore[arg-type]
        skill_executor=object(),  # type: ignore[arg-type]
        capacity=RecordingCapacity(),  # type: ignore[arg-type]
        credentials=RecordingCredentials(),  # type: ignore[arg-type]
        operations=operations,  # type: ignore[arg-type]
        artifacts=None,
        mcp_http=http,
        provider_factory=factory,
    )

    def skill_runner(task: TaskExecution, suspension: Any) -> AgenticSkillTools:
        return AgenticSkillTools(
            task=task,
            resolver=StubResolver(),  # type: ignore[arg-type]
            executor=object(),  # type: ignore[arg-type]
            settings=tenant.settings,
            sessions=tenant.session_factory,
            suspension=suspension,
            operations=operations,  # type: ignore[arg-type]
            workspace_store=None,
            create_async_child=False,
        )

    return AsyncHarness(
        tenant=tenant,
        operations=operations,
        factory=factory,
        http=http,
        executor=AgenticTaskExecutor(deps=deps, skill_runner=skill_runner),
    )


@pytest.fixture
async def harness(tenant: TenantContext) -> AsyncHarness:
    http = httpx.AsyncClient()
    try:
        yield build_harness(tenant, http)
    finally:
        await http.aclose()


def worker_loop(harness: AsyncHarness) -> WorkerLoop:
    """生产 WorkerLoop：等待状态与补扫都走真实现，只有 AGENTIC 执行器被注入。"""
    return WorkerLoop(
        harness.tenant.session_factory,
        harness.tenant.settings,
        instance_id=WORKER_INSTANCE,
        operations=harness.operations,  # type: ignore[arg-type]
        executor=TaskTypeExecutor(
            reminder=ReminderExecutor(),
            skill=object(),  # type: ignore[arg-type]
            agentic=harness.executor,
        ),
    )


def sweeper(harness: AsyncHarness, *, batch_size: int = 32) -> AsyncResultSweeper:
    return AsyncResultSweeper(
        harness.tenant.session_factory,
        harness.operations,  # type: ignore[arg-type]
        interval_sec=1.0,
        batch_size=batch_size,
    )


async def fetch(tenant: TenantContext, task_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session:
        row = await session.get(TaskExecution, task_id)
    assert row is not None
    return row


async def operations_of(task: TaskExecution) -> dict[str, dict[str, Any]]:
    return {
        str(entry["call_id"]): dict(entry)
        for entry in (task.wait_payload_json or {}).get("operations") or []
    }


async def execution_events(tenant: TenantContext, task_id: uuid.UUID) -> list[TaskEvent]:
    async with tenant.session_factory() as session:
        return list(
            await session.scalars(
                select(TaskEvent)
                .where(
                    TaskEvent.tenant_id == tenant.tenant_id,
                    TaskEvent.entity_id == task_id,
                    TaskEvent.entity_type == "EXECUTION",
                )
                .order_by(TaskEvent.seq)
            )
        )


async def seed_turn_boundary(
    tenant: TenantContext,
    task: TaskExecution,
    *,
    call_id: str = "call-1",
    arguments: Mapping[str, Any] | None = None,
    receipt: str | None = None,
) -> TaskExecution:
    """造一个真实的"回合边界崩溃现场"：assistant 事件 + 可选 tool 结果事件 + checkpoint。"""
    sink = TaskEventSink(
        tenant.session_factory,
        tenant_id=tenant.tenant_id,
        execution_id=task.id,
        model_id="gpt-test",
    )
    await sink.on_assistant_turn(
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=(ModelToolCall(call_id, STAGING_TOOL, dict(arguments or {"q": "本周"})),),
        )
    )
    if receipt is not None:
        await sink.on_tool_started(call_id, STAGING_TOOL)
        await sink.on_tool_completed(call_id, STAGING_TOOL, "SUCCEEDED", None, receipt)
    store = TaskCheckpointStore(
        tenant.session_factory,
        ExecutionIdentity(task.tenant_id, task.id, WORKER_INSTANCE, task.lease_epoch),
    )
    await store.persist(RunnerCheckpoint(turns=1, tool_calls=1))
    return await fetch(tenant, task.id)


# ---------------------------------------------------------------------------
# 水位：纯计算口径（乱序 / 重复 / 迟到不推回）
# ---------------------------------------------------------------------------


def test_watermarks_are_per_call_and_never_regress() -> None:
    first = OperationWatermark(
        operation_id=uuid.uuid4(), call_id="call-1", status=OperationStatus.SUBMITTED
    )
    second = OperationWatermark(
        operation_id=uuid.uuid4(), call_id="call-2", status=OperationStatus.SUBMITTED
    )
    result = TaskToolOperationResult(
        event_id=uuid.uuid4(),
        task_event_id=uuid.uuid4(),
        task_event_seq=7,
        terminal_status=TerminalStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        late=False,
        result={"text": "ok"},
    )
    views = {
        "call-2": TaskToolOperationView(
            operation_id=second.operation_id,
            owner=TaskToolOperationOwner(task_execution_id=uuid.uuid4()),
            source_tool_call_id="call-2",
            completion_mode=CompletionMode.JOIN,
            status=OperationStatus.COMPLETED,
            cancel_requested=False,
            result=result,
        )
    }
    advanced = advance_watermarks((first, second), views)
    assert advanced[0] == first, "一个调用早到的结果不能推进另一个调用的水位"
    assert advanced[1].consumed_event_id == result.event_id
    assert not all_settled(advanced)
    # 重复读到同一个 event_id：水位不变（按 event_id 幂等）。
    assert advance_watermarks(advanced, views) == advanced
    # 迟到的旧结果（更小的 receipt_seq）不得把水位推回去。
    stale = TaskToolOperationResult(
        event_id=uuid.uuid4(),
        task_event_id=uuid.uuid4(),
        task_event_seq=3,
        terminal_status=TerminalStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        late=False,
        result={"text": "old"},
    )
    stale_views = {
        "call-2": views["call-2"].model_copy(update={"result": stale}),
    }
    assert advance_watermarks(advanced, stale_views) == advanced


# ---------------------------------------------------------------------------
# 多 operation：同回合两条调用，各自水位、全部终态才续接
# ---------------------------------------------------------------------------


async def test_two_operations_keep_independent_watermarks_and_resume_only_when_all_settle(
    harness: AsyncHarness,
) -> None:
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.QUEUED, attempt=0)
    harness.factory.script = [
        tool_call_reply(staging_call("call-1", "A"), staging_call("call-2", "B")),
        text_reply("已合并"),
    ]
    worker = worker_loop(harness)
    assert await worker.run_once() == task.id

    waiting = await fetch(tenant, task.id)
    assert waiting.status == str(TaskStatus.WAITING_EXTERNAL)
    assert waiting.wait_kind == WaitKind.ASYNC_OPERATION.value
    assert waiting.lease_owner is None
    watermarks = (await operations_of(waiting)).values()
    assert {entry["call_id"] for entry in watermarks} == {"call-1", "call-2"}
    assert all(entry["status"] == OperationStatus.SUBMITTED.value for entry in watermarks)

    # 结果乱序：call-2 先到。只推进它自己，执行仍不可续接。
    harness.operations.complete("call-2", "结果B")
    assert await sweeper(harness).sweep() == 1
    partial = await operations_of(await fetch(tenant, task.id))
    assert partial["call-2"]["status"] == OperationStatus.COMPLETED.value
    assert partial["call-1"]["status"] == OperationStatus.SUBMITTED.value
    assert await TaskClaimer(tenant.session_factory, tenant.settings).claim_one(WORKER_INSTANCE) is None

    # call-1 到齐：两条都终态，claim 才按 EXTERNAL 续接（不消耗业务重试）。
    harness.operations.complete("call-1", "结果A")
    assert await sweeper(harness).sweep() == 1
    settled = await operations_of(await fetch(tenant, task.id))
    assert all(entry["status"] == OperationStatus.COMPLETED.value for entry in settled.values())

    harness.factory.script = [text_reply("已合并")]
    assert await worker.run_once() == task.id
    finished = await fetch(tenant, task.id)
    assert finished.status == str(TaskStatus.COMPLETED)
    assert finished.attempt == 1 and finished.resume_count == 1
    contents = harness.last_tool_messages()
    assert "结果A" in contents["call-1"] and "结果B" in contents["call-2"]


async def test_partial_failure_resumes_with_each_call_result_in_place(harness: AsyncHarness) -> None:
    tenant = harness.tenant
    await insert_task(tenant, agentic_snapshot(), status=TaskStatus.QUEUED, attempt=0)
    harness.factory.script = [
        tool_call_reply(staging_call("call-1", "A"), staging_call("call-2", "B")),
        text_reply("已按失败处理"),
    ]
    worker = worker_loop(harness)
    await worker.run_once()

    harness.operations.complete("call-1", "结果A")
    harness.operations.complete("call-2", error_code="SKILL_EXECUTION_FAILED")
    assert await sweeper(harness).sweep() == 1

    await worker.run_once()
    contents = harness.last_tool_messages()
    assert "结果A" in contents["call-1"]
    assert "SKILL_EXECUTION_FAILED" in contents["call-2"], "部分失败不得丢另一个已到的结果"


async def test_second_suspension_keeps_the_first_result_in_history(harness: AsyncHarness) -> None:
    """两轮异步提交：第一轮的结果在第二次挂起之后仍在历史里（不是提交回执）。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.QUEUED, attempt=0)
    harness.factory.script = [tool_call_reply(staging_call("call-1", "A")), text_reply("第一轮")]
    worker = worker_loop(harness)
    await worker.run_once()

    harness.operations.complete("call-1", "结果A")
    assert await sweeper(harness).sweep() == 1
    # 第二段：模型看到第一轮的真实结果后再提交一个异步调用（新 provider，按段消费脚本）。
    harness.factory.script = [tool_call_reply(staging_call("call-2", "B")), text_reply("第二轮")]
    await worker.run_once()

    second = await fetch(tenant, task.id)
    assert second.status == str(TaskStatus.WAITING_EXTERNAL)
    assert set(await operations_of(second)) == {"call-2"}
    harness.operations.complete("call-2", "结果B")
    assert await sweeper(harness).sweep() == 1
    harness.factory.script = [text_reply("两轮都完成")]
    await worker.run_once()

    assert (await fetch(tenant, task.id)).status == str(TaskStatus.COMPLETED)
    contents = harness.last_tool_messages()
    assert "结果A" in contents["call-1"], "第一次挂起的结果必须在第二次恢复后仍在历史里"
    assert "结果B" in contents["call-2"]


# ---------------------------------------------------------------------------
# 进入等待前的就绪检查
# ---------------------------------------------------------------------------


async def test_ready_result_continues_in_the_same_lease(harness: AsyncHarness) -> None:
    """结果在等待前就绪：不写 `WAITING_EXTERNAL`、不释放租约、不消耗 resume。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.QUEUED, attempt=0)
    harness.factory.script = [tool_call_reply(staging_call("call-1")), text_reply("已确认")]
    harness.operations.complete_before_read.append("call-1")

    assert await worker_loop(harness).run_once() == task.id
    finished = await fetch(tenant, task.id)
    assert finished.status == str(TaskStatus.COMPLETED)
    assert finished.resume_count == 0 and finished.attempt == 1
    assert finished.wait_kind is None and finished.wait_payload_json is None
    events = [event.event_type for event in await execution_events(tenant, task.id)]
    assert events.count("CLAIMED") == 1
    assert "WAITING_EXTERNAL" not in events
    assert "结果" in harness.last_tool_messages()["call-1"]


async def test_result_arriving_after_the_ready_check_is_recovered_by_the_sweep(
    harness: AsyncHarness,
) -> None:
    """就绪检查与等待写入之间到达的结果由**持久补扫**兜底（没有 hint 也能前进）。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.QUEUED, attempt=0)
    harness.factory.script = [tool_call_reply(staging_call("call-1")), text_reply("已确认")]
    harness.operations.complete_after_read.append("call-1")

    worker = worker_loop(harness)
    await worker.run_once()
    waiting = await fetch(tenant, task.id)
    assert waiting.status == str(TaskStatus.WAITING_EXTERNAL)
    assert (await operations_of(waiting))["call-1"]["status"] == OperationStatus.SUBMITTED.value

    # 丢了 hint / Redis 不可用 / 进程重启：一个全新的补扫器照样推进（默认 NullWakeupListener）。
    assert await sweeper(harness).sweep() == 1
    advanced = await operations_of(await fetch(tenant, task.id))
    assert advanced["call-1"]["status"] == OperationStatus.COMPLETED.value
    assert advanced["call-1"]["consumed_event_id"]

    harness.factory.script = [text_reply("已确认")]
    await worker.run_once()
    finished = await fetch(tenant, task.id)
    assert finished.status == str(TaskStatus.COMPLETED)
    assert finished.resume_count == 1 and finished.attempt == 1
    assert "结果" in harness.last_tool_messages()["call-1"]


async def test_unreadable_inbox_keeps_waiting_and_the_sweep_retries(harness: AsyncHarness) -> None:
    """Runtime 读不到时按"仍有未终态"处理：等待照样落库，补扫下一次继续。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.QUEUED, attempt=0)
    harness.factory.script = [tool_call_reply(staging_call("call-1")), text_reply("已确认")]
    worker = worker_loop(harness)
    harness.operations.unavailable = True
    await worker.run_once()
    waiting = await fetch(tenant, task.id)
    assert waiting.status == str(TaskStatus.WAITING_EXTERNAL)
    assert (await operations_of(waiting))["call-1"]["status"] == OperationStatus.SUBMITTED.value

    harness.operations.unavailable = False
    harness.operations.complete("call-1")
    assert await sweeper(harness).sweep() == 1
    harness.factory.script = [text_reply("已确认")]
    await worker.run_once()
    assert (await fetch(tenant, task.id)).status == str(TaskStatus.COMPLETED)


# ---------------------------------------------------------------------------
# 崩溃窗口：提交前/提交后、checkpoint 前/后
# ---------------------------------------------------------------------------


async def test_crash_before_checkpoint_refinds_the_operation_by_call_identity(
    harness: AsyncHarness,
) -> None:
    """提交后、回合收口前崩溃：按 `call_id` 重提交找回 operation，不跑模型、不建第二个调用。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.RUNNING)
    crashed = await seed_turn_boundary(tenant, task)
    harness.factory.script = [text_reply("不该被调用")]

    outcome = await harness.outcome(crashed)
    assert outcome.kind is OutcomeKind.WAITING
    assert harness.operations.submits == [("call-1", STAGING_TOOL, {"q": "本周"})]
    assert len(harness.operations.operations) == 1
    assert harness.model_calls == 0, "还有未终态调用时不得发起模型回合"

    loop = worker_loop(harness)
    assert await loop._wait(crashed, outcome, now=None) is False  # noqa: SLF001
    waiting = await fetch(tenant, task.id)
    assert waiting.status == str(TaskStatus.WAITING_EXTERNAL)
    assert (await operations_of(waiting))["call-1"]["status"] == OperationStatus.SUBMITTED.value

    harness.operations.complete("call-1", "找回的结果")
    assert await sweeper(harness).sweep() == 1
    harness.factory.script = [text_reply("已确认")]
    await loop.run_once()
    assert (await fetch(tenant, task.id)).status == str(TaskStatus.COMPLETED)
    assert "找回的结果" in harness.last_tool_messages()["call-1"]
    assert len(harness.operations.operations) == 1, "恢复不得产生第二个业务调用"


async def test_crash_after_the_receipt_replaces_it_without_resubmitting(
    harness: AsyncHarness,
) -> None:
    """回合收口后、checkpoint 前崩溃：事件里是提交回执，恢复时换成真实结果，不重提交。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.RUNNING)
    await harness.operations.submit(
        tenant_id=tenant.tenant_id,
        actor_user_id=task.actor_user_id,
        execution_id=task.id,
        call_id="call-1",
        tool_name=STAGING_TOOL,
        arguments={"q": "本周"},
    )
    harness.operations.complete("call-1", "真实结果")
    harness.operations.submits.clear()
    crashed = await seed_turn_boundary(tenant, task, receipt=RECEIPT)
    harness.factory.script = [text_reply("已确认")]

    outcome = await harness.outcome(crashed)
    assert outcome.kind is OutcomeKind.COMPLETED
    assert harness.operations.submits == [], "回执已落库的调用不得重提交"
    assert "真实结果" in harness.last_tool_messages()["call-1"]


async def test_recovery_with_changed_arguments_fails_explicitly(harness: AsyncHarness) -> None:
    """同一调用身份换了参数：显式 `TOOL_OPERATION_INPUT_MISMATCH`，不静默开第二个调用。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.RUNNING)
    await harness.operations.submit(
        tenant_id=tenant.tenant_id,
        actor_user_id=task.actor_user_id,
        execution_id=task.id,
        call_id="call-1",
        tool_name=STAGING_TOOL,
        arguments={"q": "崩溃前的参数"},
    )
    harness.operations.submits.clear()
    crashed = await seed_turn_boundary(tenant, task, arguments={"q": "换过的参数"})

    with pytest.raises(TaskExecutionError) as failure:
        await harness.executor.execute(crashed)
    assert failure.value.code == "TOOL_OPERATION_INPUT_MISMATCH"
    assert len(harness.operations.operations) == 1


async def test_resume_state_reports_pending_and_ready_per_call(harness: AsyncHarness) -> None:
    """恢复读模型：已终态的调用直接给出结果正文，未终态的进 `pending`（按 `call_id`）。"""
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.RUNNING)
    await harness.operations.submit(
        tenant_id=tenant.tenant_id,
        actor_user_id=task.actor_user_id,
        execution_id=task.id,
        call_id="call-1",
        tool_name=STAGING_TOOL,
        arguments={"q": "A"},
    )
    await harness.operations.submit(
        tenant_id=tenant.tenant_id,
        actor_user_id=task.actor_user_id,
        execution_id=task.id,
        call_id="call-2",
        tool_name=STAGING_TOOL,
        arguments={"q": "B"},
    )
    harness.operations.complete("call-1", "结果A")
    crashed = await seed_turn_boundary(tenant, task, arguments={"q": "A"})
    checkpoint = RunnerCheckpoint(
        turns=1,
        tool_calls=2,
        pending_tool_calls=(
            OperationWatermark(
                operation_id=harness.operations.operations["call-2"].operation_id,
                call_id="call-2",
                status=OperationStatus.SUBMITTED,
            ),
        ),
    )
    store = TaskCheckpointStore(
        tenant.session_factory,
        ExecutionIdentity(crashed.tenant_id, crashed.id, WORKER_INSTANCE, crashed.lease_epoch),
    )
    await store.persist(checkpoint)
    refreshed = await fetch(tenant, task.id)

    resume = await load_resume_state(
        tenant.session_factory, refreshed, operations=harness.operations
    )
    assert isinstance(resume, ResumeState)
    assert [item.call_id for item in resume.pending] == ["call-2"]
    tool_messages = {
        str(message.tool_call_id): message.content
        for message in resume.messages
        if message.role is ModelRole.TOOL
    }
    assert "结果A" in tool_messages["call-1"]


# ---------------------------------------------------------------------------
# 迟到结果与补扫边界
# ---------------------------------------------------------------------------


async def test_late_result_after_terminal_execution_is_recorded_never_resumed(
    harness: AsyncHarness,
) -> None:
    tenant = harness.tenant
    task = await insert_task(tenant, agentic_snapshot(), status=TaskStatus.RUNNING)
    await harness.operations.submit(
        tenant_id=tenant.tenant_id,
        actor_user_id=task.actor_user_id,
        execution_id=task.id,
        call_id="call-1",
        tool_name=STAGING_TOOL,
        arguments={"q": "本周"},
    )
    operation_id = harness.operations.operations["call-1"].operation_id
    payload = payload_from_watermarks(
        [
            OperationWatermark(
                operation_id=operation_id,
                call_id="call-1",
                status=OperationStatus.SUBMITTED,
            )
        ]
    )
    waiting = await insert_task(
        tenant,
        agentic_snapshot(),
        status=TaskStatus.WAITING_EXTERNAL,
        schedule_id=None,
        wait_kind=WaitKind.ASYNC_OPERATION.value,
        wait_payload_json=payload,
        lease_owner=None,
        lease_until=None,
    )
    # 候选读到之后、行锁之前执行被取消：补扫看到的是"已终态 + 结果已就绪"。
    stale_candidate = await fetch(tenant, waiting.id)
    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskExecution, waiting.id)
        assert row is not None
        row.status = str(TaskStatus.CANCELLED)
        row.cancel_requested = True
    harness.operations.complete("call-1", "迟到结果", late=True)

    assert await sweeper(harness)._sweep_one(stale_candidate, datetime.now(UTC)) == 0  # noqa: SLF001
    stored = await fetch(tenant, waiting.id)
    assert stored.status == str(TaskStatus.CANCELLED), "终态不得被迟到结果改写"
    assert stored.wait_payload_json == payload, "迟到结果不改等待水位"
    assert await TaskClaimer(tenant.session_factory, tenant.settings).claim_one(WORKER_INSTANCE) is None
    async with tenant.session_factory() as session:
        late_events = list(
            await session.scalars(
                select(TaskEvent).where(
                    TaskEvent.entity_type == "ASYNC",
                    TaskEvent.entity_id == operation_id,
                    TaskEvent.event_type == "RESULT_RECEIVED",
                )
            )
        )
    assert len(late_events) == 1 and late_events[0].payload_json["late"] is True


async def test_concurrent_sweeps_advance_a_shared_row_exactly_once(
    harness: AsyncHarness,
) -> None:
    """两个补扫器同时扫同一行（真实并发会话）：行锁让水位只推进一次。"""
    tenant = harness.tenant
    calls = []
    for call_id in ("call-1", "call-2"):
        await harness.operations.submit(
            tenant_id=tenant.tenant_id,
            actor_user_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            call_id=call_id,
            tool_name=STAGING_TOOL,
            arguments={"q": call_id},
        )
        harness.operations.complete(call_id, f"{call_id}-结果")
        calls.append(
            OperationWatermark(
                operation_id=harness.operations.operations[call_id].operation_id,
                call_id=call_id,
                status=OperationStatus.SUBMITTED,
            )
        )
    task = await insert_task(
        tenant,
        agentic_snapshot(),
        status=TaskStatus.WAITING_EXTERNAL,
        wait_kind=WaitKind.ASYNC_OPERATION.value,
        wait_payload_json=payload_from_watermarks(calls),
        lease_owner=None,
        lease_until=None,
    )
    first, second = sweeper(harness), sweeper(harness)
    counts = await asyncio.gather(first.sweep(), second.sweep())
    assert sum(counts) == 1, counts
    advanced = await operations_of(await fetch(tenant, task.id))
    assert all(entry["status"] == OperationStatus.COMPLETED.value for entry in advanced.values())


async def test_sweep_is_bounded_and_rotates_across_tenants(harness: AsyncHarness) -> None:
    """每拍有界、处理过的行排到队尾：任何租户的积压都不会把别的租户饿死。"""
    tenant = harness.tenant
    base = datetime.now(UTC) - timedelta(minutes=10)
    created: list[TaskExecution] = []
    for index, owner_tenant in enumerate((tenant.tenant_id, tenant.tenant_id, "test-other-tenant")):
        operation = OperationWatermark(
            operation_id=uuid.uuid4(),
            call_id=f"call-{index}",
            status=OperationStatus.SUBMITTED,
        )
        task = await insert_task(
            tenant,
            agentic_snapshot(),
            status=TaskStatus.WAITING_EXTERNAL,
            tenant_id=owner_tenant,
            wait_kind=WaitKind.ASYNC_OPERATION.value,
            wait_payload_json=payload_from_watermarks([operation]),
            lease_owner=None,
            lease_until=None,
            update_time=base + timedelta(seconds=index),
        )
        created.append(task)
    # 第三行属于另一个租户：它的 actor 无法通过归属校验，补扫只记日志、不阻塞别人。
    harness.operations.unavailable = True
    crossed = await sweeper(harness, batch_size=2).sweep()
    assert crossed == 0
    first_two = [await fetch(tenant, row.id) for row in created[:2]]
    assert [row.update_time for row in first_two] > [base, base + timedelta(seconds=1)]
    harness.operations.unavailable = False
    # 队尾的行（含另一个租户）下一拍仍然会被扫到：轮转不饿死任何租户。
    assert await sweeper(harness, batch_size=2).sweep() == 0
    remaining = await fetch(tenant, created[2].id)
    assert remaining.update_time > base + timedelta(seconds=2)
