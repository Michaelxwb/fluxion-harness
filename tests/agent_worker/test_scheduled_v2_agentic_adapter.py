"""TASK-019：Worker Agentic ExecutionAdapter（真实 PG + 中立 Kernel 契约）。

真实边界（不 mock 适配器本身）：

- **真实 PostgreSQL**（隔离租户）：`task_execution` / `task_execution_checkpoint` / `task_event` /
  `task_human_interaction` 都是真行；checkpoint 由生产 `TaskCheckpointStore` 落库；
- **生产执行器**：`AgenticTaskExecutor`（装配根注入的就是它）→ 中立 Kernel `AgentRunner`；
- **只在网络边界上替身**：模型 provider 是脚本化传输（Kernel 契约不测 HTTP）、Runtime/Console
  的容量/操作/产物/凭据是记录型替身（它们的真实语义由 TASK-006/007/008 的验收覆盖）。
- **共享契约**：`tests.kernel_adapter_contract` 的同一组用例在 Worker 适配器上跑一遍。

Skill 沙箱子进程不在本文件：ASYNC 提交走操作协议（本文件覆盖）；SYNC 执行复用 Skill 任务的
真实沙箱边界，由 `test_scheduled_v2_executors` 的真实子进程用例覆盖。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_agent_core.agent import AgentRunStatus
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.model import ModelMessage, ModelResponse, ModelRole, ModelToolCall
from muad_agent_core.skill import SkillExecutionStatus
from muad_agent_worker.application.agentic.checkpoints import (
    ExecutionIdentity,
    TaskCheckpointStore,
    checkpoint_from_state,
)
from muad_agent_worker.application.agentic.run import AgenticDependencies
from muad_agent_worker.application.agentic.suspension import ASYNC_SUBMITTED_PROMPT
from muad_agent_worker.application.agentic.tools import build_registry
from muad_agent_worker.infrastructure.console_credentials import (
    ResolvedCredentials,
    ResolvedModelCredential,
)
from muad_agent_worker.infrastructure.models.task import (
    TaskEvent,
    TaskExecution,
    TaskExecutionCheckpoint,
    TaskHumanInteraction,
)
from muad_agent_worker.worker.agentic_executor import AgenticTaskExecutor
from muad_agent_worker.worker.agentic_skills import AgenticSkillTools
from muad_agent_worker.worker.execution_outcomes import (
    OutcomeKind,
    interpret_execution,
)
from muad_agent_worker.worker.service import WorkerLoop
from muad_contracts import (
    CompletionMode,
    HumanInteractionStatus,
    OperationStatus,
    ScheduleMode,
    SnapshotAgent,
    SnapshotBudget,
    SnapshotGrants,
    SnapshotMcpServer,
    SnapshotMemory,
    SnapshotModel,
    SnapshotSkill,
    SnapshotTool,
    TaskArtifactWriteResponse,
    TaskCapacityGrant,
    TaskCapacityRelease,
    TaskStatus,
    TaskToolOperationList,
    TaskToolOperationOwner,
    TaskToolOperationResult,
    TaskToolOperationView,
    TerminalStatus,
    build_schedule_snapshot,
    schedule_snapshot_hash,
)
from sqlalchemy import select

from agent_worker.conftest import TenantContext
from tests.kernel_adapter_contract import (
    CONTRACT_CASES,
    Observation,
    RoundPlan,
    ScriptedModelProvider,
    text_reply,
    tool_call_reply,
)

pytestmark = pytest.mark.integration

AGENT_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000001")
ACTOR_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000002")
MODEL_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000003")
DEPLOYMENT_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000004")
CREDENTIAL_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000005")
SKILL_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000006")
ARTIFACT_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000007")
MCP_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000008")
OPERATION_ID = uuid.UUID("8b0f6c1e-0000-4000-8000-000000000009")
CHECKSUM = "sha256:" + "a" * 64
WORKER_INSTANCE = "worker-agentic"
STAGING_TOOL = "skill::" + str(SKILL_ID) + "::deliver_report"
MEMORY_TEXT = "用户偏好中文简报；每份报告不超过 200 字。"


def agentic_snapshot(
    *,
    max_tool_calls: int = 4,
    max_steps: int = 4,
    max_tokens: int = 100_000,
    memory_text: str | None = MEMORY_TEXT,
    compaction: dict[str, Any] | None = None,
    grants: SnapshotGrants | None = None,
) -> dict[str, Any]:
    return build_schedule_snapshot(
        mode=ScheduleMode.AGENTIC,
        agent=SnapshotAgent(
            id=AGENT_ID,
            key="agent-key",
            revision=1,
            instructions="你是定时任务助手，只依据工具结果作答。",
            runtime_config={"max_model_retries": 1},
        ),
        model=SnapshotModel(
            id=MODEL_ID,
            revision=2,
            deployment_id=DEPLOYMENT_ID,
            model_json={"model_id": "gpt-test", "protocol": "OPENAI", "params": {"temperature": 0.0}},
            pool_members=[
                {
                    "deployment_id": str(DEPLOYMENT_ID),
                    "credential_id": str(CREDENTIAL_ID),
                    "key": "primary",
                    "weight": 1,
                    "revision": 3,
                }
            ],
        ),
        max_snapshot_bytes=1_000_000,
        policy={
            "compaction": compaction
            or {
                "snip": {"enabled": False},
                "micro": {"enabled": False},
                "summary": {"enabled": False},
            }
        },
        skills=[
            SnapshotSkill(
                skill_id=SKILL_ID,
                artifact_id=ARTIFACT_ID,
                version="1.0.0",
                checksum=CHECKSUM,
                storage_key="skills/deliver.zip",
                source="ADMIN",
                execution_mode="ASYNC",
                frontmatter={"key": "deliver", "name": "投递报告", "description": "异步投递"},
            )
        ],
        tools=[
            SnapshotTool(
                source="SKILL",
                tool_name="deliver_report",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
                retry_safety="IDEMPOTENT",
                skill_id=SKILL_ID,
                artifact_id=ARTIFACT_ID,
                checksum=CHECKSUM,
                execution_mode="ASYNC",
            ),
            SnapshotTool(
                source="MCP",
                tool_name="customer.report",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
                retry_safety="READ_ONLY",
                catalog_revision=4,
            ),
        ],
        mcp=[
            SnapshotMcpServer(
                server_id=MCP_ID,
                key="crm",
                endpoint="https://mcp.example/rpc",
                definitions=[
                    {
                        "name": "customer.report",
                        "description": "customer report",
                        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                    }
                ],
            )
        ],
        memory=(
            SnapshotMemory(
                taxonomy_version=1,
                revision=7,
                block={"entries": [], "rendered_text": memory_text},
                degraded=False,
            )
            if memory_text is not None
            else SnapshotMemory(taxonomy_version=1, revision=0, block={}, degraded=True)
        ),
        budget=SnapshotBudget(
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            max_tokens=max_tokens,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            retry_safety="IDEMPOTENT",
        ),
        allowlist=[f"SKILL:{SKILL_ID}:deliver_report", "MCP:crm:customer.report"],
        prompt_template={"version": "v1", "source": "agent.instructions"},
        context={},
        grants=grants
        or SnapshotGrants(agent_access=True, skill_grants=[SKILL_ID], mcp_grants=[MCP_ID]),
    )


def input_payload(prompt: str = "整理本周客户报告") -> dict[str, Any]:
    return {
        "kind": "AGENT_TURN",
        "mode": "AGENTIC",
        "prompt": prompt,
        "tools_allow": [f"SKILL:{SKILL_ID}:deliver_report", "MCP:crm:customer.report"],
        "budget_ref": "platform",
        "output_contract": {},
        "context": {},
    }


async def insert_task(
    tenant: TenantContext,
    snapshot: dict[str, Any] | None = None,
    *,
    prompt: str = "整理本周客户报告",
    status: TaskStatus = TaskStatus.RUNNING,
    **overrides: Any,
) -> TaskExecution:
    frozen = snapshot if snapshot is not None else agentic_snapshot()
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant.tenant_id,
        "agent_id": AGENT_ID,
        "actor_user_id": ACTOR_ID,
        "trigger_type": "IMMEDIATE",
        "task_type": "AGENTIC",
        "status": str(status),
        "input_json": input_payload(prompt),
        "execution_snapshot_schema_version": 4,
        "execution_snapshot_json": frozen,
        "snapshot_hash": schedule_snapshot_hash(frozen),
        "snapshot_bytes": int(frozen["snapshot_bytes"]),
        "idempotency_key": f"agentic-{uuid.uuid4()}",
        "priority": 100,
        "attempt": 1,
        "max_attempts": 3,
        "not_before": now,
        "deadline_at": now + timedelta(hours=1),
        "lease_owner": WORKER_INSTANCE if status is TaskStatus.RUNNING else None,
        "lease_until": now + timedelta(minutes=10) if status is TaskStatus.RUNNING else None,
        "lease_epoch": 1,
        "started_at": now,
    }
    values.update(overrides)
    async with tenant.session_factory() as session, session.begin():
        task = TaskExecution(**values)
        session.add(task)
        await session.flush()
    return task


# ---------------------------------------------------------------------------
# 网络边界替身：容量 / 凭据 / 异步操作 / 产物（只记录，不联网）
# ---------------------------------------------------------------------------


@dataclass
class RecordingCapacity:
    reserves: list[Any] = field(default_factory=list)
    renewals: list[Any] = field(default_factory=list)
    settlements: list[Any] = field(default_factory=list)

    async def reserve(self, *, tenant_id: str, actor_user_id: uuid.UUID, request: Any) -> Any:
        self.reserves.append(request)
        return TaskCapacityGrant(
            reservation_id=uuid.uuid4(), epoch=1, lease_until=datetime.now(UTC) + timedelta(seconds=60)
        )

    async def renew(
        self, *, tenant_id: str, actor_user_id: uuid.UUID, reservation_id: Any, renewal: Any
    ) -> Any:
        self.renewals.append(renewal)
        return TaskCapacityGrant(
            reservation_id=reservation_id,
            epoch=renewal.epoch,
            lease_until=datetime.now(UTC) + timedelta(seconds=60),
        )

    async def settle(
        self, *, tenant_id: str, actor_user_id: uuid.UUID, reservation_id: Any, settlement: Any
    ) -> Any:
        self.settlements.append(settlement)
        return TaskCapacityRelease(reservation_id=reservation_id, state="SETTLED", released=True)


@dataclass
class RecordingCredentials:
    calls: list[dict[str, Any]] = field(default_factory=list)
    resolved_calls: int = 0

    async def resolve(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        execution_id: uuid.UUID,
        model_id: uuid.UUID,
        deployment_id: uuid.UUID | None = None,
        credential_id: uuid.UUID | None = None,
        expected_revision: int | None = None,
        mcp_server_ids: tuple[uuid.UUID, ...] = (),
    ) -> ResolvedCredentials:
        self.calls.append(
            {
                "execution_id": execution_id,
                "model_id": model_id,
                "deployment_id": deployment_id,
                "credential_id": credential_id,
                "expected_revision": expected_revision,
                "mcp_server_ids": tuple(mcp_server_ids),
            }
        )
        self.resolved_calls += 1
        return ResolvedCredentials(
            model=ResolvedModelCredential(
                model_id=model_id,
                deployment_id=DEPLOYMENT_ID,
                credential_id=CREDENTIAL_ID,
                credential_revision=3,
                key_resource_id=uuid.uuid4(),
                quota_group_id=None,
                provider_key="openai",
                base_url="https://model.example/v1",
                api_key="sk-test",
            ),
            mcp_secrets={server_id: None for server_id in mcp_server_ids},
        )


@dataclass
class RecordingOperations:
    """API-30 的内存替身：同一调用身份返回同一 operation（`submits` 只记本次执行的提交）。"""

    submits: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    ready: bool = False
    result_text: str = "报告已生成"
    registered: dict[str, str] = field(default_factory=dict)
    execution_id: uuid.UUID | None = None

    async def submit(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        execution_id: uuid.UUID,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        completion_mode: CompletionMode = CompletionMode.JOIN,
    ) -> TaskToolOperationView:
        self.submits.append((call_id, tool_name, dict(arguments)))
        self.execution_id = execution_id
        self.registered[call_id] = tool_name
        return self._view(call_id, completion_mode)

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
        items = [
            self._view(call_id, CompletionMode.JOIN) for call_id in sorted(self.registered)
        ]
        if ready is True:
            items = [item for item in items if item.result is not None]
        return TaskToolOperationList(items=items, page=page, page_size=page_size, total=len(items))

    async def get(self, *, tenant_id: str, actor_user_id: uuid.UUID, operation_id: uuid.UUID) -> Any:
        raise AssertionError("contract runs read operations through the owner listing only")

    def _view(self, call_id: str, completion_mode: CompletionMode) -> TaskToolOperationView:
        result = (
            TaskToolOperationResult(
                event_id=uuid.uuid4(),
                task_event_id=uuid.uuid4(),
                task_event_seq=7,
                terminal_status=TerminalStatus.COMPLETED,
                completed_at=datetime.now(UTC),
                late=False,
                result={"text": self.result_text},
            )
            if self.ready
            else None
        )
        return TaskToolOperationView(
            operation_id=OPERATION_ID,
            owner=TaskToolOperationOwner(task_execution_id=self.execution_id),
            source_tool_call_id=call_id,
            tool_name=self.registered.get(call_id),
            completion_mode=completion_mode,
            status=OperationStatus.COMPLETED if self.ready else OperationStatus.SUBMITTED,
            cancel_requested=False,
            result=result,
        )


@dataclass
class RecordingArtifacts:
    writes: list[dict[str, Any]] = field(default_factory=list)

    async def write(self, **kwargs: Any) -> TaskArtifactWriteResponse:
        self.writes.append(kwargs)
        return TaskArtifactWriteResponse(
            artifact_id=uuid.uuid4(), version="1", checksum=kwargs["checksum"]
        )


class StubResolver:
    """Skill artifact 解析替身：ASYNC 提交不需要真实制品。"""

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        raise AssertionError("ASYNC skill submission must not fetch the artifact")


class _ScriptedFactory:
    def __init__(self) -> None:
        self.script: list[ModelResponse] = []
        self.provider: ScriptedModelProvider | None = None

    def __call__(self, **_: Any) -> ScriptedModelProvider:
        self.provider = ScriptedModelProvider(self.script)
        return self.provider


# ---------------------------------------------------------------------------
# 共享 Kernel 契约：绑定到真实 Worker 适配器
# ---------------------------------------------------------------------------


class WorkerAdapterHarness:
    """宿主差异只在这里：Worker 用 Task-owned ASYNC 提交表达工具级挂起。"""

    label = "worker"
    expected_status = AgentRunStatus.WAITING_TOOL
    reason = "ASYNC_OPERATION"
    prompt = ASYNC_SUBMITTED_PROMPT

    def __init__(self, tenant: TenantContext) -> None:
        self._tenant = tenant
        self._capacity = RecordingCapacity()
        self._credentials = RecordingCredentials()
        self._operations = RecordingOperations()
        self._artifacts = RecordingArtifacts()
        self._factory = _ScriptedFactory()
        self._mcp_http = httpx.AsyncClient()
        self._observed: list[dict[str, Any]] = []
        self._task: TaskExecution | None = None
        self.last_outcome: Any = None
        deps = AgenticDependencies(
            settings=tenant.settings,
            sessions=tenant.session_factory,
            artifact_resolver=StubResolver(),  # type: ignore[arg-type]
            skill_executor=object(),  # type: ignore[arg-type]
            capacity=self._capacity,  # type: ignore[arg-type]
            credentials=self._credentials,  # type: ignore[arg-type]
            operations=self._operations,  # type: ignore[arg-type]
            artifacts=None,
            mcp_http=self._mcp_http,
            provider_factory=self._factory,
            on_interrupt=self._on_interrupt,
        )
        self._executor = AgenticTaskExecutor(deps=deps, skill_runner=self._skill_runner)

    @property
    def capacity(self) -> RecordingCapacity:
        return self._capacity

    @property
    def credentials(self) -> RecordingCredentials:
        return self._credentials

    @property
    def operations(self) -> RecordingOperations:
        return self._operations

    @property
    def task(self) -> TaskExecution:
        assert self._task is not None, "run() has not created an execution yet"
        return self._task

    @property
    def pending(self) -> tuple[Any, ...]:
        from muad_contracts import OperationWatermark

        return (
            OperationWatermark(
                operation_id=OPERATION_ID,
                call_id="call-1",
                status=OperationStatus.SUBMITTED,
                receipt_seq=0,
                consumed_event_id=None,
            ),
        )

    def staging_call(self, call_id: str = "call-1") -> ModelToolCall:
        return ModelToolCall(call_id, STAGING_TOOL, {"q": "本周"})

    def _skill_runner(self, task: TaskExecution, suspension: Any) -> Any:
        return AgenticSkillTools(
            task=task,
            resolver=StubResolver(),  # type: ignore[arg-type]
            executor=object(),  # type: ignore[arg-type]
            settings=self._tenant.settings,
            sessions=self._tenant.session_factory,
            suspension=suspension,
            operations=self._operations,  # type: ignore[arg-type]
            workspace_store=None,
            create_async_child=False,
        )

    async def _on_interrupt(self, payload: dict[str, Any]) -> None:
        self._observed.append(payload)

    async def run(self, plan: RoundPlan) -> Observation:
        self._factory.script = list(plan.script)
        self._task = await insert_task(
            self._tenant, agentic_snapshot(max_tool_calls=plan.max_tool_calls or 4)
        )
        if plan.cancel:
            await self._set_cancel(self._task.id)
        return await self._execute(self._task)

    async def resume(self) -> Observation:
        prior = await self.run(RoundPlan((tool_call_reply(self.staging_call()),)))
        assert prior.status is self.expected_status
        task = self._task
        assert task is not None
        await self._mark_resumed(task.id)
        refreshed = await self._refresh(task.id)
        refreshed.last_resume_reason = "EXTERNAL"
        # 唤醒是因为结果已就绪：恢复路径必须按调用身份取回它，而不是重跑工具。
        self._operations.ready = True
        self._factory.script = [text_reply("已确认")]
        return await self._execute(refreshed)

    async def _execute(self, task: TaskExecution) -> Observation:
        observation = Observation()
        self._observed = []
        self._operations.submits = []
        envelope = await self._executor.execute(task)
        outcome = interpret_execution(envelope, now=datetime.now(UTC))
        self.last_outcome = outcome
        observation.status = _status_of(outcome)
        observation.interrupts = list(self._observed)
        observation.model_calls = self._factory.provider.call_count if self._factory.provider else 0
        observation.executed = [call_id for call_id, _name, _arguments in self._operations.submits]
        if outcome.checkpoint_state is not None:
            checkpoint = checkpoint_from_state(outcome.checkpoint_state)
            observation.checkpoints = [checkpoint]
            await self._persist_boundary(task, checkpoint)
        events = await self._events(task.id)
        observation.order = _order_of(events)
        if observation.interrupts:
            observation.order.append("suspend")
        observation.tool_results = _tool_results(events)
        observation.cancelled = outcome.kind is OutcomeKind.CANCELLED
        return observation

    async def _persist_boundary(self, task: TaskExecution, checkpoint: RunnerCheckpoint) -> None:
        """生产把它与等待状态写在同一事务；契约只要求「宿主自己的 checkpoint store」可读回。"""
        store = TaskCheckpointStore(
            self._tenant.session_factory,
            ExecutionIdentity(task.tenant_id, task.id, WORKER_INSTANCE, task.lease_epoch),
        )
        await store.persist(checkpoint)

    async def _events(self, execution_id: uuid.UUID) -> list[TaskEvent]:
        async with self._tenant.session_factory() as session:
            return list(
                await session.scalars(
                    select(TaskEvent)
                    .where(TaskEvent.entity_id == execution_id, TaskEvent.entity_type == "EXECUTION")
                    .order_by(TaskEvent.seq)
                )
            )

    async def _set_cancel(self, execution_id: uuid.UUID) -> None:
        async with self._tenant.session_factory() as session, session.begin():
            row = await session.get(TaskExecution, execution_id)
            assert row is not None
            row.cancel_requested = True

    async def _mark_resumed(self, execution_id: uuid.UUID) -> None:
        async with self._tenant.session_factory() as session, session.begin():
            row = await session.get(TaskExecution, execution_id)
            assert row is not None
            row.resume_count += 1
            row.last_resume_reason = "EXTERNAL"
            row.lease_owner = WORKER_INSTANCE
            row.lease_until = datetime.now(UTC) + timedelta(minutes=10)
            row.lease_epoch += 1

    async def _refresh(self, execution_id: uuid.UUID) -> TaskExecution:
        async with self._tenant.session_factory() as session:
            row = await session.get(TaskExecution, execution_id)
        assert row is not None
        return row


def _status_of(outcome: Any) -> AgentRunStatus | None:
    if outcome.kind is OutcomeKind.COMPLETED:
        return AgentRunStatus.COMPLETED
    if outcome.kind is OutcomeKind.WAITING:
        return AgentRunStatus.WAITING_TOOL
    if outcome.kind is OutcomeKind.WAITING_HUMAN:
        return AgentRunStatus.WAITING_INPUT
    return None


def _order_of(events: list[TaskEvent]) -> list[str]:
    order: list[str] = []
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "ASSISTANT_TURN":
            order.append("assistant")
        elif event.event_type == "TOOL_CALL" and payload.get("state") == "STARTED":
            order.append("tool_started")
        elif event.event_type == "TOOL_CALL" and payload.get("state") == "COMPLETED":
            order.append("tool_completed")
    return order


def _tool_results(events: list[TaskEvent]) -> list[tuple[str, str, str, str]]:
    results: list[tuple[str, str, str, str]] = []
    for event in events:
        payload = event.payload_json or {}
        if event.event_type != "TOOL_CALL" or payload.get("state") != "COMPLETED":
            continue
        results.append(
            (
                str(payload.get("tool_call_id")),
                str(payload.get("tool_name")),
                str(payload.get("status")),
                str(payload.get("result") or ""),
            )
        )
    return results


@pytest.fixture
async def worker_adapter(tenant: TenantContext) -> WorkerAdapterHarness:
    harness = WorkerAdapterHarness(tenant)
    try:
        yield harness
    finally:
        await harness._mcp_http.aclose()  # noqa: SLF001


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=lambda case: case.__name__)
async def test_worker_adapter_satisfies_the_shared_kernel_contract(
    worker_adapter: WorkerAdapterHarness, case: Any
) -> None:
    await case(worker_adapter)


# ---------------------------------------------------------------------------
# 行为回归（清单逐条）
# ---------------------------------------------------------------------------


async def test_suspension_persists_checkpoint_and_execution_state(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """挂起边界：checkpoint 落 `task_execution_checkpoint`，等待状态由 WorkerLoop 同事务写。"""
    observation = await worker_adapter.run(
        RoundPlan((tool_call_reply(worker_adapter.staging_call()),))
    )
    outcome = worker_adapter.last_outcome
    assert outcome is not None and observation.checkpoints
    loop = WorkerLoop(
        worker_adapter._tenant.session_factory,  # noqa: SLF001
        worker_adapter._tenant.settings,  # noqa: SLF001
        instance_id=WORKER_INSTANCE,
    )
    task = await worker_adapter._refresh(worker_adapter.task.id)  # noqa: SLF001
    await loop._wait(task, outcome, now=None)  # noqa: SLF001
    async with worker_adapter._tenant.session_factory() as session:  # noqa: SLF001
        current = await session.get(TaskExecution, task.id)
        assert current is not None
        assert current.status == str(TaskStatus.WAITING_EXTERNAL)
        assert current.wait_kind == "ASYNC_OPERATION"
        assert current.lease_owner is None
        operations = (current.wait_payload_json or {}).get("operations")
        assert operations and operations[0]["call_id"] == "call-1"
        checkpoints = list(
            await session.scalars(
                select(TaskExecutionCheckpoint).where(
                    TaskExecutionCheckpoint.execution_id == task.id
                )
            )
        )
        assert checkpoints, "wait must persist the kernel checkpoint in the same transaction"
        assert current.last_checkpoint_id == checkpoints[-1].id
        events = await session.scalars(
            select(TaskEvent).where(TaskEvent.entity_id == task.id)
        )
        assert any(event.event_type == "WAITING_EXTERNAL" for event in events)


async def test_human_question_opens_interaction_and_checkpoint(
    tenant: TenantContext, worker_adapter: WorkerAdapterHarness
) -> None:
    """Human 挂起：OPEN 提问 + WAITING_HUMAN + checkpoint 同一事务，问题期限不晚于执行期限。"""
    deadline = datetime.now(UTC) + timedelta(minutes=30)
    human_runner = _HumanSkillRunner(deadline)
    harness = worker_adapter
    harness._executor = AgenticTaskExecutor(  # noqa: SLF001
        deps=_deps_for(tenant, harness), skill_runner=lambda task, suspension: human_runner(suspension)
    )
    harness._factory.script = [tool_call_reply(harness.staging_call())]  # noqa: SLF001
    task = await insert_task(tenant)
    envelope = await harness._executor.execute(task)  # noqa: SLF001
    outcome = interpret_execution(envelope, now=datetime.now(UTC))
    assert outcome.kind is OutcomeKind.WAITING_HUMAN
    assert outcome.human is not None and outcome.human.question == "请确认客户名单"

    loop = WorkerLoop(tenant.session_factory, tenant.settings, instance_id=WORKER_INSTANCE)
    await loop._wait_human(task, outcome, now=None)  # noqa: SLF001
    async with tenant.session_factory() as session:
        current = await session.get(TaskExecution, task.id)
        assert current is not None and current.status == str(TaskStatus.WAITING_HUMAN)
        assert current.wait_kind is None and current.wait_payload_json is None
        interaction = await session.scalar(
            select(TaskHumanInteraction).where(TaskHumanInteraction.execution_id == task.id)
        )
        assert interaction is not None
        assert interaction.status == str(HumanInteractionStatus.OPEN)
        assert interaction.question == "请确认客户名单"
        assert interaction.deadline_at <= task.deadline_at
        checkpoint = await session.get(TaskExecutionCheckpoint, current.last_checkpoint_id)
        assert checkpoint is not None and checkpoint.state_json["schema_version"] == 1
        human_events = list(
            await session.scalars(
                select(TaskEvent).where(
                    TaskEvent.entity_id == interaction.id, TaskEvent.entity_type == "HUMAN"
                )
            )
        )
        assert [event.event_type for event in human_events] == ["REQUESTED"]


async def test_ask_user_tool_stages_human_question(worker_adapter: WorkerAdapterHarness) -> None:
    """平台内置 `ask_user`：模型直接提问也走同一条 Human 挂起，不依赖 Skill。"""
    worker_adapter._factory.script = [  # noqa: SLF001
        tool_call_reply(
            ModelToolCall(
                "call-ask",
                "ask_user",
                {
                    "question": "请确认客户名单",
                    "input_schema": {
                        "type": "object",
                        "properties": {"customer": {"type": "string"}},
                    },
                },
            )
        )
    ]
    task = await insert_task(worker_adapter._tenant)
    envelope = await worker_adapter._executor.execute(task)  # noqa: SLF001
    outcome = interpret_execution(envelope, now=datetime.now(UTC))
    assert outcome.kind is OutcomeKind.WAITING_HUMAN
    assert outcome.human is not None and outcome.human.question == "请确认客户名单"

    loop = WorkerLoop(
        worker_adapter._tenant.session_factory,  # noqa: SLF001
        worker_adapter._tenant.settings,  # noqa: SLF001
        instance_id=WORKER_INSTANCE,
    )
    await loop._wait_human(task, outcome, now=None)  # noqa: SLF001
    async with worker_adapter._tenant.session_factory() as session:  # noqa: SLF001
        current = await session.get(TaskExecution, task.id)
        assert current is not None and current.status == str(TaskStatus.WAITING_HUMAN)
        interaction = await session.scalar(
            select(TaskHumanInteraction).where(TaskHumanInteraction.execution_id == task.id)
        )
        assert interaction is not None
        assert interaction.status == str(HumanInteractionStatus.OPEN)
        assert interaction.question == "请确认客户名单"
        assert interaction.input_schema == {
            "type": "object",
            "properties": {"customer": {"type": "string"}},
        }


class _HumanSkillRunner:
    def __init__(self, deadline: datetime) -> None:
        self._deadline = deadline

    def __call__(self, suspension: Any) -> Any:
        runner = self

        class _Runner:
            async def call(self, skill: Any, tool_name: str, arguments: Any, *, call_id: str) -> str:
                suspension.stage_human(
                    question="请确认客户名单",
                    input_schema={"type": "object"},
                    deadline_at=runner._deadline,
                )
                return "等待用户确认"

        return _Runner()


def _deps_for(tenant: TenantContext, harness: WorkerAdapterHarness) -> AgenticDependencies:
    return AgenticDependencies(
        settings=tenant.settings,
        sessions=tenant.session_factory,
        artifact_resolver=StubResolver(),  # type: ignore[arg-type]
        skill_executor=object(),  # type: ignore[arg-type]
        capacity=harness.capacity,  # type: ignore[arg-type]
        credentials=harness.credentials,  # type: ignore[arg-type]
        operations=harness.operations,  # type: ignore[arg-type]
        artifacts=None,
        mcp_http=harness._mcp_http,  # noqa: SLF001
        provider_factory=harness._factory,  # noqa: SLF001
        on_interrupt=harness._on_interrupt,  # noqa: SLF001
    )


async def test_model_call_uses_frozen_pool_credentials_and_capacity(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """冻结成员 → API-09 解析（不冻密钥）→ BACKGROUND 预占 → 结算。"""
    observation = await worker_adapter.run(RoundPlan((text_reply("完成"),)))
    assert observation.status is AgentRunStatus.COMPLETED
    resolve = worker_adapter.credentials.calls[0]
    assert resolve["model_id"] == MODEL_ID
    assert resolve["deployment_id"] == DEPLOYMENT_ID
    assert resolve["credential_id"] == CREDENTIAL_ID
    assert resolve["expected_revision"] == 3
    reserve = worker_adapter.capacity.reserves[0]
    assert str(reserve.owner.task_execution_id) == str(worker_adapter.task.id)
    assert reserve.model_id == MODEL_ID
    assert [settlement.outcome for settlement in worker_adapter.capacity.settlements] == ["SUCCEEDED"]


async def test_frozen_memory_is_injected_and_never_written(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """只读记忆：冻结块进提示词；没有记忆写工具；运行期不重读（凭据/容量各只解析一次）。"""
    observation = await worker_adapter.run(RoundPlan((text_reply("完成"),)))
    assert observation.status is AgentRunStatus.COMPLETED
    provider = worker_adapter._factory.provider  # noqa: SLF001
    assert provider is not None
    request = provider.requests[0]
    system_messages = [message for message in request.messages if message.role is ModelRole.SYSTEM]
    assert any(MEMORY_TEXT in message.content for message in system_messages)
    assert any(message.role is ModelRole.USER for message in request.messages)
    names = {tool.name for tool in request.tools}
    assert not any("memory" in name for name in names)
    # 一次 MCP 认证值解析 + 一次模型调用解析（密钥不进快照、每次执行解析当前值）
    assert worker_adapter.credentials.resolved_calls >= 1


async def test_registry_only_contains_allowlisted_and_granted_tools(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """allowlist 之外的工具不注册；冻结授权撤销后即使 allowlist 有也不注册。"""
    from muad_agent_worker.application.agentic.snapshot import load_frozen_execution

    task = await insert_task(worker_adapter._tenant, agentic_snapshot())  # noqa: SLF001
    frozen = load_frozen_execution(task)
    registered = build_registry(frozen, mcp=None, skills=None)
    assert registered.registry.list() == ()  # 没有 MCP client / skill runner 不凭空注册

    revoked = await insert_task(
        worker_adapter._tenant,  # noqa: SLF001
        agentic_snapshot(grants=SnapshotGrants(agent_access=True, skill_grants=[], mcp_grants=[])),
    )
    frozen_revoked = load_frozen_execution(revoked)
    registry = build_registry(frozen_revoked, mcp=None, skills=None)
    assert registry.registry.list() == ()


async def test_skill_tool_without_input_schema_accepts_free_form_arguments(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """Skill 没有服务端 input schema：工具必须接受任意 JSON，不能把参数挡成 {}。"""
    from muad_agent_worker.application.agentic.snapshot import load_frozen_execution

    snapshot = agentic_snapshot()
    for tool in snapshot["tools"]:
        if tool.get("source") == "SKILL":
            tool["input_schema"] = {}
    task = await insert_task(worker_adapter._tenant, snapshot)  # noqa: SLF001
    frozen = load_frozen_execution(task)
    registry = build_registry(frozen, mcp=None, skills=object())  # type: ignore[arg-type]
    definition = registry.registry.get(STAGING_TOOL)
    assert definition.input_schema == {"type": "object", "additionalProperties": True}


async def test_unknown_tool_never_produces_an_external_request(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """模型点名的未注册工具不执行，失败作为工具结果回到回合里。"""
    observation = await worker_adapter.run(
        RoundPlan((tool_call_reply(ModelToolCall("call-1", "missing", {})), text_reply("收口")))
    )
    assert observation.status is AgentRunStatus.COMPLETED
    assert observation.executed == []
    assert worker_adapter.operations.submits == []


async def test_cancel_before_the_first_turn_stops_the_execution(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """执行前已请求取消：不发起模型调用、不落 checkpoint。"""
    observation = await worker_adapter.run(
        RoundPlan((tool_call_reply(worker_adapter.staging_call()),), cancel=True)
    )
    assert observation.cancelled is True
    assert observation.model_calls == 0
    assert observation.checkpoints == []


async def test_token_budget_is_a_hard_stop(worker_adapter: WorkerAdapterHarness) -> None:
    """冻结 `max_tokens` 超限即停：不把超预算的答案当成功结果。"""
    snapshot = agentic_snapshot(max_tokens=1)
    worker_adapter._factory.script = [
        ModelResponse(content="很长的答案", finish_reason="stop", input_tokens=5, output_tokens=5)
    ]
    task = await insert_task(worker_adapter._tenant, snapshot)  # noqa: SLF001
    with pytest.raises(Exception) as caught:
        await worker_adapter._executor.execute(task)  # noqa: SLF001
    assert getattr(caught.value, "code", None) == "TASK_BUDGET_EXCEEDED"


# ---------------------------------------------------------------------------
# 压缩与整轮外置（RULE-04：降级可见、绝不失败；外置整轮要么全落、要么全内联）
# ---------------------------------------------------------------------------


class _SummaryProvider:
    def __init__(self, *, payload: str | None = None, fail: bool = False) -> None:
        self._payload, self._fail = payload, fail
        self.calls = 0

    async def complete(self, request: Any) -> ModelResponse:
        self.calls += 1
        if self._fail:
            raise RuntimeError("summary model down")
        assert self._payload is not None
        return ModelResponse(content=self._payload, finish_reason="stop")


def _summary_payload() -> str:
    return json.dumps(
        {
            "user_goal": "整理本周客户报告",
            "constraints": ["中文"],
            "progress": ["已取数"],
            "open_items": [],
            "artifacts": [],
        }
    )


def _long_history() -> tuple[ModelMessage, ...]:
    # 合法历史：assistant(带 tool_calls) + 它的 tool 结果成对出现（压缩器按组处理）。
    messages: list[ModelMessage] = [ModelMessage(role=ModelRole.SYSTEM, content="你是助手")]
    for index in range(12):
        call = ModelToolCall(f"call-{index}", "echo", {})
        messages.append(ModelMessage(role=ModelRole.USER, content=f"问题 {index}"))
        messages.append(ModelMessage(role=ModelRole.ASSISTANT, content="", tool_calls=(call,)))
        messages.append(
            ModelMessage(role=ModelRole.TOOL, content="x" * 400, tool_call_id=call.id)
        )
    messages.append(ModelMessage(role=ModelRole.USER, content="当前问题"))
    return tuple(messages)


async def test_compaction_applies_the_free_layers_and_degrades_summary(
    tenant: TenantContext,
) -> None:
    """摘要模型失败只作废摘要层：前三层照常生效，且不抛异常（RULE-04）。"""
    from muad_agent_worker.application.agentic.compaction import (
        SummaryGenerator,
        TaskContextCompactor,
        compaction_settings,
    )

    settings = compaction_settings(
        {
            "compaction": {
                "snip": {"enabled": True, "max_groups": 4, "keep_head_groups": 1, "keep_tail_groups": 2},
                "micro": {"enabled": True, "keep_recent_tool_groups": 1},
                "summary": {"enabled": True, "threshold_bytes": 1, "model_ref": "summary-model"},
            }
        }
    )
    failing = _SummaryProvider(fail=True)
    compactor = TaskContextCompactor(
        settings,
        tenant_id=tenant.tenant_id,
        execution_id=uuid.uuid4(),
        summary=SummaryGenerator(failing, model_name="summary-model"),
        artifacts=RecordingArtifacts(),  # type: ignore[arg-type]
    )
    history = _long_history()
    outbound = await compactor.compact(history)
    assert failing.calls == 1
    assert len(outbound) < len(history)
    assert outbound[0].content.startswith("你是助手")


async def test_summary_without_archive_is_discarded(tenant: TenantContext) -> None:
    """摘要没有逐字存档就不放行：被覆盖的历史不能净丢（RULE-05）。"""
    from muad_agent_worker.application.agentic.compaction import (
        SummaryGenerator,
        TaskContextCompactor,
        compaction_settings,
    )

    class _FailingArtifacts:
        async def write(self, **_: Any) -> Any:
            from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactError

            raise TaskArtifactError("archive down")

    settings = compaction_settings(
        {
            "compaction": {
                "snip": {"enabled": False},
                "micro": {"enabled": False},
                "summary": {"enabled": True, "threshold_bytes": 1, "model_ref": "summary-model"},
            }
        }
    )
    provider = _SummaryProvider(payload=_summary_payload())
    compactor = TaskContextCompactor(
        settings,
        tenant_id=tenant.tenant_id,
        execution_id=uuid.uuid4(),
        summary=SummaryGenerator(provider, model_name="summary-model"),
        artifacts=_FailingArtifacts(),  # type: ignore[arg-type]
    )
    history = _long_history()
    outbound = await compactor.compact(history)
    assert provider.calls == 1
    assert outbound == history  # 摘要作废 ⇒ 历史原样
    assert not any(message.content.startswith("[历史摘要]") for message in outbound)


async def test_summary_is_archived_before_it_replaces_history(tenant: TenantContext) -> None:
    """存档成功才把更早历史换成摘要前缀；当前回合原样保留。"""
    from muad_agent_worker.application.agentic.compaction import (
        SummaryGenerator,
        TaskContextCompactor,
        compaction_settings,
    )

    artifacts = RecordingArtifacts()
    settings = compaction_settings(
        {
            "compaction": {
                "snip": {"enabled": False},
                "micro": {"enabled": False},
                "summary": {"enabled": True, "threshold_bytes": 1, "model_ref": "summary-model"},
            }
        }
    )
    compactor = TaskContextCompactor(
        settings,
        tenant_id=tenant.tenant_id,
        execution_id=uuid.uuid4(),
        summary=SummaryGenerator(_SummaryProvider(payload=_summary_payload()), model_name="summary-model"),
        artifacts=artifacts,  # type: ignore[arg-type]
    )
    outbound = await compactor.compact(_long_history())
    assert artifacts.writes, "摘要必须先逐字存档"
    assert any(message.content.startswith("[历史摘要]") for message in outbound)
    assert outbound[-1].content == "当前问题"


async def test_round_artifact_selection_and_rollback(tenant: TenantContext) -> None:
    """整轮外置：超阈值/超轮预算才落盘；写失败抛出 ⇒ Kernel 保留内联结果。"""
    from muad_agent_worker.application.agentic.artifacts import (
        TaskArtifactRoundWriter,
        select_round_persists,
    )
    from muad_contracts.platform_settings import ToolResultSettings

    settings = ToolResultSettings(
        persist_threshold_bytes=100,
        round_budget_bytes=200,
        preview_head_bytes=10,
        preview_tail_bytes=10,
    )
    assert select_round_persists(
        [("a", 101), ("b", 50), ("c", 50)],
        persist_threshold_bytes=100,
        round_budget_bytes=10_000,
    ) == ("a",)
    # 剩余合计 65 > 轮预算 10 ⇒ 按大到小补一条后即进预算
    assert select_round_persists(
        [("a", 100), ("b", 60), ("c", 5)],
        persist_threshold_bytes=100,
        round_budget_bytes=10,
    ) == ("a", "b")

    class _FailingClient:
        async def write(self, **_: Any) -> Any:
            from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactError

            raise TaskArtifactError("artifact store down")

    results = (
        ModelMessage(role=ModelRole.TOOL, content="y" * 200, tool_call_id="call-1"),
        ModelMessage(role=ModelRole.TOOL, content="short", tool_call_id="call-2"),
    )
    writer = TaskArtifactRoundWriter(
        _FailingClient(),  # type: ignore[arg-type]
        settings,
        tenant_id=tenant.tenant_id,
        execution_id=uuid.uuid4(),
    )
    from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactError

    with pytest.raises(TaskArtifactError):
        await writer.finish_round(results)

    artifacts = RecordingArtifacts()
    ok_writer = TaskArtifactRoundWriter(
        artifacts,  # type: ignore[arg-type]
        settings,
        tenant_id=tenant.tenant_id,
        execution_id=uuid.uuid4(),
    )
    replaced = await ok_writer.finish_round(results)
    assert "short" == replaced[1].content
    payload = json.loads(replaced[0].content)
    assert payload["artifact"]["artifact_id"]
    assert payload["artifact"]["preview"].startswith("y" * 10)


async def test_degraded_memory_block_is_empty_and_non_blocking(
    worker_adapter: WorkerAdapterHarness,
) -> None:
    """记忆读取失败/降级 ⇒ 空块：任务照常执行，也不在运行期补读。"""
    worker_adapter._factory.script = [text_reply("完成")]  # noqa: SLF001
    task = await insert_task(worker_adapter._tenant, agentic_snapshot(memory_text=None))  # noqa: SLF001
    envelope = await worker_adapter._executor.execute(task)  # noqa: SLF001
    assert envelope["status"] == str(SkillExecutionStatus.SUCCEEDED)
    assert envelope["result"]["text"] == "完成"
