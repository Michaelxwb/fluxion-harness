"""把冻结快照装配成一次真实 Agentic 执行（Kernel + 全部宿主端口）。

装配顺序就是 design §3.2.4 的步骤：快照 → 模型池/容量 → 工具目录（allowlist + 冻结授权）→
只读记忆 → 事件/checkpoint/挂起端口 → 压缩/整轮外置 → Kernel。

**这里没有第二个循环**：执行只调用 `AgentRunner.run`。宿主差异只体现在端口实现里：
模型来自容量租约、事件写 task_event、checkpoint 写 task_execution_checkpoint、挂起交给
WorkerLoop 落等待状态。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    Verifier,
)
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.recovery import active_skills as folded_active_skills
from muad_agent_core.agent.recovery import fold_events, history_messages, replay_failure_state
from muad_agent_core.agent.termination import FailureState
from muad_agent_core.context.compactor import split_groups
from muad_agent_core.context.plan import RunInput
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import ModelMessage, ModelRole, ModelUsage
from muad_agent_core.prompt import PromptSkill, resolve_zone
from muad_agent_core.sandbox import WorkspaceStore
from muad_agent_core.skill import SkillArtifactResolver, SkillExecutor
from muad_agent_core.tools import AttachmentReadTools, ResolvedAttachment, ToolExecutionPipeline
from muad_agent_core.tools.attachments import TextCursor, read_text_segment
from muad_agent_core.tools.result_budget import derive_tool_result_budget
from muad_api import AppError
from muad_common import SharedSettings
from muad_contracts import OperationWatermark
from muad_logging import get_logger
from muad_platform_sdk.egress_audit_store import EgressAuditIdentity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.console_credentials import ConsoleCredentialClient
from ...infrastructure.mcp_artifact_writer import TaskMcpArtifactWriter
from ...infrastructure.mcp_audit import DatabaseMcpAudit
from ...infrastructure.models.task import TaskEvent, TaskExecution, TaskExecutionCheckpoint
from ...infrastructure.task_artifact_client import TaskArtifactClient
from ...infrastructure.task_capacity_client import TaskCapacityClient
from ...infrastructure.task_operation_client import (
    TaskOperationClient,
    TaskOperationError,
    operation_result_content,
)
from ..platform_settings import resolve_platform_settings
from ..ports import PlatformSettingsClient
from ..task_events import EntityType, TaskEventType
from .artifacts import TaskArtifactRoundWriter
from .async_results import (
    DeclaredCall,
    OperationReader,
    read_operations,
    watermark_of,
)
from .cancellation import CancellationProbe
from .checkpoints import ExecutionIdentity, TaskCheckpointStore, checkpoint_from_state
from .compaction import SummaryGenerator, TaskContextCompactor, compaction_settings
from .errors import AgenticError
from .events import TaskEventSink
from .mcp import McpServerRuntime, McpToolBox
from .model import CapacityModelPool, ModelTarget
from .scheduled_turn import base_messages, load_scheduled_turn, task_run_input
from .session_context import SessionContextReader
from .snapshot import FrozenAgenticExecution, load_frozen_execution
from .suspension import AgenticSuspension, SuspensionBoundary
from .task_query import DbTaskQuery
from .tools import (
    AgenticToolRegistry,
    ScheduledPlatformTools,
    SkillToolRunner,
    build_registry,
    skill_tool_name,
)
from .turn_result import NO_CHANGE, ResultRecorder, user_visible_text

logger = get_logger("muad_agent_worker.application.agentic.run")

#: 确定性失败码：直接落终态，不消耗重试预算。
TASK_BUDGET_EXCEEDED = "TASK_BUDGET_EXCEEDED"
TASK_NO_PROGRESS = "TASK_NO_PROGRESS"
TOKEN_BUDGET_EXCEEDED = "TASK_BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True)
class AgenticDependencies:
    """一次 Agentic 执行需要的外部事实（装配根注入；这里不构造自建客户端）。"""

    settings: SharedSettings
    sessions: async_sessionmaker[AsyncSession]
    artifact_resolver: SkillArtifactResolver
    skill_executor: SkillExecutor
    #: 执行开始时读一次平台默认语言；None = 直构/测试不追加语言段。
    settings_client: PlatformSettingsClient | None = None
    capacity: TaskCapacityClient | None = None
    credentials: ConsoleCredentialClient | None = None
    operations: TaskOperationClient | None = None
    artifacts: TaskArtifactClient | None = None
    mcp_http: httpx.AsyncClient | None = None
    workspace_store: WorkspaceStore | None = None
    #: 会话目标上下文（`current`/`session:<id>`/`main`）；缺席时只执行 isolated 语义并留痕。
    session_context: SessionContextReader | None = None
    #: `current_time` 的时钟注入点（生产走系统时钟）。
    clock: Callable[[], datetime] | None = None
    #: 模型传输缝（生产留空 = OpenAI 兼容 provider）；契约/单测注入脚本化 provider。
    provider_factory: Any = None
    #: 中断事件观察者（`ON_INTERRUPT` Hook）：挂起事实的可观测出口，不改变 Kernel 行为。
    on_interrupt: Any = None


@dataclass(frozen=True, slots=True)
class AgenticOutcome:
    """一次执行的中立结论（挂起边界与 checkpoint 一起交给 WorkerLoop 落库）。"""

    status: AgentRunStatus
    final_text: str
    turns: int
    tool_calls: int
    usage: ModelUsage
    boundary: SuspensionBoundary | None = None
    last_failure: dict[str, str] | None = None
    #: `record_result` 的结构化结论（`no_change/progress/done/blocked/needs_attention`）。
    result_status: str | None = None


def agent_policy(frozen: FrozenAgenticExecution) -> AgentPolicy:
    """只读冻结快照：`PlatformSettings.agent` 的值已在 admission 写入 budget。

    期限取「agent 预算」与「Task 绝对 deadline」的较早值；运行时配置不覆盖冻结值。
    """
    budget = frozen.snapshot.budget
    defaults = AgentPolicy()
    remaining_ms = max(1, int((frozen.deadline_at - datetime.now(UTC)).total_seconds() * 1000))
    budget_ms = budget.deadline_ms or remaining_ms
    return AgentPolicy(
        max_turns=budget.max_steps or defaults.max_turns,
        max_tool_calls=budget.max_tool_calls or defaults.max_tool_calls,
        deadline_ms=min(remaining_ms, budget_ms),
        max_model_retries=budget.max_model_retries or defaults.max_model_retries,
        max_identical_failures=budget.max_identical_failures or defaults.max_identical_failures,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def prompt_skills(frozen: FrozenAgenticExecution) -> tuple[PromptSkill, ...]:
    skills: list[PromptSkill] = []
    for entry in frozen.snapshot.skills:
        frontmatter = entry.frontmatter
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        label = frontmatter.get("platform_label")
        skills.append(
            PromptSkill(
                key=entry.key,
                name=name if isinstance(name, str) and name else str(entry.skill_id),
                description=description if isinstance(description, str) else "",
                platform_label=label if isinstance(label, str) and label else None,
            )
        )
    return tuple(skills)


def build_verifier(contract: Mapping[str, Any]) -> Verifier | None:
    """输出契约：只认"最终答案必须是符合冻结 JSON schema 的 JSON"这一条明确规则。"""
    schema = contract.get("json_schema")
    if not isinstance(schema, Mapping):
        return None

    async def verify(candidate: str) -> tuple[bool, str]:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            return False, "最终答案不是合法 JSON"
        import jsonschema

        try:
            jsonschema.validate(instance=parsed, schema=dict(schema))
        except jsonschema.ValidationError as exc:
            return False, f"不符合输出契约：{exc.message}"
        return True, ""

    return verify


def _attachment_tools(deps: AgenticDependencies, frozen: FrozenAgenticExecution) -> AttachmentReadTools:
    from muad_artifact_store import NfsArtifactStore

    store = NfsArtifactStore(deps.settings.artifact_root)

    async def resolve(
        artifact_id: str, offset: int, limit: int, cursor: TextCursor | None
    ) -> ResolvedAttachment:
        assert deps.artifacts is not None
        ref = await deps.artifacts.read(
            tenant_id=frozen.tenant_id,
            execution_id=frozen.execution_id,
            artifact_id=artifact_id,
        )
        path = store.resolve(ref.storage_key)
        if ref.media_type.startswith("image/") or limit <= 0:
            data = await asyncio.to_thread(path.read_bytes)
            total = None
            next_cursor = None
        else:
            data, total, next_cursor = await asyncio.to_thread(
                read_text_segment, path, offset, limit, cursor
            )
        return ResolvedAttachment(
            artifact_id=artifact_id,
            media_type=ref.media_type,
            filename=ref.filename or artifact_id,
            data=data,
            total_chars=total,
            next_cursor=next_cursor,
        )

    return AttachmentReadTools(resolve)


async def _mcp_servers(
    frozen: FrozenAgenticExecution, deps: AgenticDependencies
) -> dict[UUID, McpServerRuntime]:
    """冻结 server 的定义 + 按 API-09 解析的认证值（每次执行解析一次，不缓存）。"""
    allowlisted = {
        key.split(":", 2)[1] for key in frozen.allowlist if key.startswith("MCP:")
    }
    # 只为 allowlist 里真的出现的 server 解析认证值：没有 MCP 工具时不多打一次 Console。
    servers = {
        server.server_id: server
        for server in frozen.snapshot.mcp
        if server.key in allowlisted
    }
    if not servers or deps.credentials is None:
        return {}
    target = ModelTarget.from_snapshot(frozen.snapshot.model)
    member = target.members[0]
    resolved = await deps.credentials.resolve(
        tenant_id=frozen.tenant_id,
        actor_user_id=frozen.actor_user_id,
        execution_id=frozen.execution_id,
        model_id=target.model_id,
        deployment_id=member.deployment_id,
        credential_id=member.credential_id,
        expected_revision=member.revision,
        mcp_server_ids=tuple(servers),
    )
    return {
        server_id: McpServerRuntime(server=server, auth_secret=resolved.mcp_secrets.get(server_id))
        for server_id, server in servers.items()
    }


async def _platform_reply_locale(deps: AgenticDependencies, tenant_id: str) -> str | None:
    """Read the platform default once at execution start; degrade without blocking.

    The Runner combines it with the frozen memory language; this function only
    owns the platform source so the priority is defined in one place.
    """
    if deps.settings_client is None:
        return None
    try:
        snapshot = await deps.settings_client.fetch_snapshot(tenant_id=tenant_id)
        return resolve_platform_settings(
            snapshot,
            batch_platform_limit=deps.settings.batch_platform_limit,
        ).locale.default_locale
    except AppError as exc:
        logger.warning(
            "agentic_locale_unavailable tenant=%s code=%s",
            tenant_id,
            exc.code,
        )
        return None


async def execute_agentic(
    deps: AgenticDependencies,
    task: TaskExecution,
    frozen: FrozenAgenticExecution,
    *,
    identity: ExecutionIdentity,
    skill_runner_factory: Callable[[AgenticSuspension], SkillToolRunner] | None = None,
    resume: ResumeState | None = None,
) -> AgenticOutcome:
    """跑一次执行：装配 → Kernel.run → 交回中立结论（挂起边界或最终文本）。"""
    if deps.capacity is None or deps.credentials is None:
        raise AgenticError("MODEL_UNAVAILABLE", "agentic model pool is not configured")
    target = ModelTarget.from_snapshot(frozen.snapshot.model)
    pool = CapacityModelPool(
        capacity=deps.capacity,
        credentials=deps.credentials,
        tenant_id=frozen.tenant_id,
        actor_user_id=frozen.actor_user_id,
        execution_id=frozen.execution_id,
        deadline_at=frozen.deadline_at,
        http_client=_http_client(deps),
        provider_factory=deps.provider_factory,
    )
    suspension = AgenticSuspension(execution_id=frozen.execution_id)
    events = TaskEventSink(
        deps.sessions,
        tenant_id=frozen.tenant_id,
        execution_id=frozen.execution_id,
        model_id=target.model_name,
    )
    checkpoints = TaskCheckpointStore(deps.sessions, identity)
    scheduled = await load_scheduled_turn(deps.sessions, frozen, deps.session_context)
    recorder = ResultRecorder()
    platform = (
        ScheduledPlatformTools(
            zone=resolve_zone(scheduled.timezone),
            result_recorder=recorder,
            clock=deps.clock or _utc_now,
            task_query=DbTaskQuery(
                deps.sessions,
                tenant_id=frozen.tenant_id,
                actor_user_id=frozen.task.actor_user_id,
                agent_id=frozen.task.agent_id,
            ),
        )
        if scheduled is not None
        else None
    )
    mcp_servers = await _mcp_servers(frozen, deps)
    mcp_box = (
        McpToolBox(
            mcp_servers,
            deps.mcp_http,
            audit=DatabaseMcpAudit(
                deps.sessions,
                EgressAuditIdentity(
                    frozen.tenant_id,
                    frozen.actor_user_id,
                    None,
                    frozen.execution_id,
                ),
            ),
            artifact_writer=(
                TaskMcpArtifactWriter(
                    deps.artifacts,
                    tenant_id=frozen.tenant_id,
                    execution_id=frozen.execution_id,
                )
                if deps.artifacts is not None
                else None
            ),
        )
        if mcp_servers and deps.mcp_http is not None
        else None
    )
    # 执行器与工具执行者必须拿到**同一个**挂起端口：否则工具已经暂存了异步提交，
    # Kernel 在回合收口时看不到它，会继续跑下一个模型回合（实测会把一次提交跑成 N 轮）。
    skill_runner = skill_runner_factory(suspension) if skill_runner_factory is not None else None
    # 定时任务没有人可回答：不注册 ask_user，避免模型挂起等待到 deadline。
    human = None if frozen.scheduled else suspension
    attachments = _attachment_tools(deps, frozen) if deps.artifacts is not None else None
    agentic_tools: AgenticToolRegistry = build_registry(
        frozen,
        mcp=mcp_box,
        skills=skill_runner,
        human=human,
        platform=platform,
        attachments=attachments,
    )
    hooks = HookPipeline()
    _observe_interrupts(hooks, deps)
    runner = AgentRunner(
        provider=_budget_provider(await pool.provider(target), frozen),
        registry=agentic_tools.registry,
        hooks=hooks,
        tool_pipeline=ToolExecutionPipeline(
            registry=agentic_tools.registry, policy=agentic_tools.policy
        ),
        tool_round_results=_round_writer(frozen, deps),
        context_compactor=_compactor(frozen, deps, pool),
        runtime_events=checkpoints,
        tool_suspension=suspension,
        answer_verifier=build_verifier(frozen.output_contract),
        verification_max_attempts=1,
    )
    history_messages = resume.messages if resume is not None else base_messages(frozen, scheduled)
    run_input = task_run_input(frozen, scheduled)
    material_scan_end, head_end = _worker_history_meta(history_messages, run_input)
    request = AgentRunRequest(
        model_id=target.model_name,
        instructions=frozen.instructions,
        messages=history_messages,
        run_input=run_input,
        covered_seq=0,
        material_scan_end_seq=material_scan_end,
        head_end_seq=head_end,
        earlier_records=0,
        skills=prompt_skills(frozen),
        active_skills=resume.active_skills if resume is not None else (),
        policy=agent_policy(frozen),
        reply_locale=await _platform_reply_locale(deps, frozen.tenant_id),
        memory_language=frozen.memory_language,
        temperature=_float_param(target.params, "temperature"),
        max_tokens=_int_param(target.params, "max_tokens"),
        params=dict(target.params),
        deadline_at=frozen.deadline_at,
    )
    probe = CancellationProbe(
        deps.sessions,
        tenant_id=frozen.tenant_id,
        execution_id=frozen.execution_id,
        owner=identity.owner,
        epoch=identity.epoch,
        deadline_at=frozen.deadline_at,
    )
    async with probe:
        result = await _run_kernel(
            runner, request, events, probe, resume.checkpoint if resume is not None else None
        )
    final_text = result.final_text
    final_status = recorder.status
    if result.status is AgentRunStatus.COMPLETED:
        final_text = user_visible_text(result.final_text, status=final_status)
        if not final_text.strip() and final_status != NO_CHANGE:
            raise AgenticError("AGENTIC_EMPTY_RESULT", "agentic execution produced no answer")
        if final_status is None and scheduled is not None:
            final_status = "done"
    return AgenticOutcome(
        status=result.status,
        final_text=final_text,
        turns=result.turns,
        tool_calls=result.tool_calls,
        usage=result.usage,
        boundary=suspension.take(),
        last_failure=result.last_failure,
        result_status=final_status if result.status is AgentRunStatus.COMPLETED else None,
    )


def _observe_interrupts(hooks: HookPipeline, deps: AgenticDependencies) -> None:
    observer = deps.on_interrupt
    if observer is None:
        return

    async def record(context: Any) -> None:
        await observer(dict(context.payload))

    hooks.register("on_interrupt", record)


def _http_client(deps: AgenticDependencies) -> httpx.AsyncClient:
    if deps.capacity is None or deps.credentials is None:
        raise AgenticError("MODEL_UNAVAILABLE", "agentic model pool is not configured")
    client = deps.mcp_http
    if client is None:
        raise AgenticError("MODEL_UNAVAILABLE", "agentic model transport is not configured")
    return client


async def _run_kernel(
    runner: AgentRunner,
    request: AgentRunRequest,
    events: TaskEventSink,
    probe: CancellationProbe,
    checkpoint: RunnerCheckpoint | None,
) -> AgentRunResult:
    result = await runner.run(
        request,
        is_cancelled=probe.is_cancelled,
        on_model_started=events.on_model_started,
        on_model_completed=events.on_model_completed,
        on_assistant_turn=events.on_assistant_turn,
        on_tool_prepared=events.on_tool_prepared,
        on_tool_completed=events.on_tool_completed,
        checkpoint=checkpoint,
    )
    return result


def _round_writer(
    frozen: FrozenAgenticExecution, deps: AgenticDependencies
) -> TaskArtifactRoundWriter | None:
    if deps.artifacts is None:
        return None
    settings = derive_tool_result_budget(compaction_settings(frozen.snapshot.policy))
    return TaskArtifactRoundWriter(
        deps.artifacts,
        settings,
        tenant_id=frozen.tenant_id,
        execution_id=frozen.execution_id,
    )


def _worker_history_meta(
    messages: Sequence[ModelMessage], run_input: RunInput
) -> tuple[int, int | None]:
    """Worker has no durable summary coverage: all older is material; keep first task goal."""
    groups = split_groups(messages)
    material_end = max((message.source_event_seq or 0 for message in messages), default=0)
    head_end = None
    for index, group in enumerate(groups):
        if not any(message.role.value == "user" for message in group):
            continue
        if any(message.message_id == run_input.message_id for message in group):
            break
        end = max(message.source_event_seq or 0 for message in group)
        if index + 1 < len(groups):
            end = max(
                end,
                max(message.source_event_seq or 0 for message in groups[index + 1]),
            )
        head_end = end or None
        break
    return material_end, head_end


def _compactor(
    frozen: FrozenAgenticExecution, deps: AgenticDependencies, pool: CapacityModelPool
) -> TaskContextCompactor:
    settings = compaction_settings(frozen.snapshot.policy)
    summary: SummaryGenerator | None = None
    if settings.summary.enabled:
        summary_target = ModelTarget.summary_from_snapshot(frozen.snapshot.model)
        if summary_target is not None:
            summary = SummaryGenerator(
                _LazyProvider(pool, summary_target),
                model_name=summary_target.model_name,
                max_tokens=_int_param(summary_target.params, "max_tokens"),
            )
    return TaskContextCompactor(
        settings,
        tenant_id=frozen.tenant_id,
        execution_id=frozen.execution_id,
        summary=summary,
        artifacts=deps.artifacts,
    )


class _LazyProvider:
    """摘要 provider 延迟到第一次调用再建（没有摘要需求就不解析凭据/预占容量）。"""

    def __init__(self, pool: CapacityModelPool, target: ModelTarget) -> None:
        self._pool, self._target = pool, target
        self._inner: Any | None = None

    async def complete(self, request: Any) -> Any:
        if self._inner is None:
            self._inner = await self._pool.provider(self._target)
        return await self._inner.complete(request)


class _BudgetedProvider:
    """冻结 `max_tokens` 的执行级硬预算：累计超限即停，不发起下一回合。"""

    def __init__(self, inner: Any, limit: int | None) -> None:
        self._inner = inner
        self._limit = limit if limit and limit > 0 else None
        self._used = 0

    async def complete(self, request: Any) -> Any:
        response = await self._inner.complete(request)
        self._used += (response.input_tokens or 0) + (response.output_tokens or 0)
        if self._limit is not None and self._used > self._limit:
            raise AgenticError(
                TOKEN_BUDGET_EXCEEDED,
                f"token budget exhausted: {self._used}/{self._limit}",
                retryable=False,
            )
        return response


def _budget_provider(provider: Any, frozen: FrozenAgenticExecution) -> Any:
    return _BudgetedProvider(provider, frozen.snapshot.budget.max_tokens)


def _float_param(params: Mapping[str, Any], key: str) -> float | None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int_param(params: Mapping[str, Any], key: str) -> int | None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@dataclass(frozen=True, slots=True)
class ResumeState:
    """恢复同一个执行：宿主自己落库的 checkpoint + 重建的历史 + 仍未终态的调用。"""

    checkpoint: RunnerCheckpoint
    messages: tuple[ModelMessage, ...]
    pending: tuple[OperationWatermark, ...] = ()
    active_skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FoldedHistory:
    """事件重建出来的历史、声明与恢复所需的预算/止损事实。"""

    messages: tuple[ModelMessage, ...]
    declared: tuple[DeclaredCall, ...]
    active_skills: tuple[str, ...] = ()
    turns: int = 0
    tool_calls: int = 0
    failure_state: dict[str, Any] | None = None
    consumed_event_seq: int = 0


async def load_resume_state(
    sessions: async_sessionmaker[AsyncSession],
    task: TaskExecution,
    *,
    operations: OperationReader | None,
    session_context: SessionContextReader | None = None,
    ensure_child: Callable[[str, Any, str, Mapping[str, Any]], Any] | None = None,
) -> ResumeState | None:
    """按事件事实恢复同一执行；checkpoint 只是可选的水位，不是恢复入口。

    先 fold 出每个 assistant 回合与其声明/ prepared / completed 事实，再按 `call_id` 对账：
    没有 checkpoint 也要恢复；声明窗口补显式 `NOT_EXECUTED`，不重跑模型、不重放原始参数。
    """
    checkpoint = RunnerCheckpoint()
    has_checkpoint = False
    if task.last_checkpoint_id is not None:
        async with sessions() as session:
            row = await session.scalar(
                select(TaskExecutionCheckpoint).where(
                    TaskExecutionCheckpoint.id == task.last_checkpoint_id,
                    TaskExecutionCheckpoint.tenant_id == task.tenant_id,
                )
            )
        if row is not None:
            checkpoint = checkpoint_from_state(row.state_json)
            has_checkpoint = True
    frozen = load_frozen_execution(task)
    scheduled = await load_scheduled_turn(sessions, frozen, session_context)
    async_tools = _async_skill_tools(frozen)
    folded = await _history_from_events(
        sessions,
        task,
        async_tools=async_tools,
        max_identical_failures=agent_policy(frozen).max_identical_failures,
    )
    if has_checkpoint:
        checkpoint = replace(
            checkpoint,
            turns=max(checkpoint.turns, folded.turns),
            tool_calls=max(checkpoint.tool_calls, folded.tool_calls),
            consumed_event_seq=max(checkpoint.consumed_event_seq, folded.consumed_event_seq),
            # 事件 fold 从 seq 0 重放，是失败事实的唯一权威；checkpoint 只在没有任何事件时
            # 才有意义。用"空状态字典"做兜底会让 checkpoint 的旧状态遮住后续失败。
            failure_state=(
                folded.failure_state
                if folded.consumed_event_seq
                else checkpoint.failure_state
            ),
        )
    else:
        checkpoint = RunnerCheckpoint(
            turns=folded.turns,
            tool_calls=folded.tool_calls,
            consumed_event_seq=folded.consumed_event_seq,
            failure_state=folded.failure_state,
        )
    messages = [*base_messages(frozen, scheduled), *folded.messages]
    pending = list(checkpoint.pending_tool_calls)
    has_history = has_checkpoint or bool(folded.messages)
    if operations is not None and (has_history or pending):
        try:
            views = await read_operations(
                operations,
                tenant_id=task.tenant_id,
                actor_user_id=task.actor_user_id,
                execution_id=task.id,
            )
        except TaskOperationError as exc:
            # 读失败与"调用冲突"必须分开：读失败可以等下一次，冲突不能。
            if not pending:
                raise AgenticError(
                    "TOOL_OPERATION_UNAVAILABLE", f"operation read failed: {exc.code}", retryable=True
                ) from exc
            logger.warning(
                "agentic_resume_operation_read_failed",
                extra={"execution_id": str(task.id), "code": exc.code},
            )
            return ResumeState(checkpoint=checkpoint, messages=tuple(messages), pending=tuple(pending))
        if views:
            has_history = True
        by_call = {item.call_id: item for item in pending}
        declared_by_id = {call.call_id: call for call in folded.declared}
        for call_id, view in list(views.items()):
            if view.result is None and view.task_id is None and ensure_child is not None:
                recovery = await operations.get_recovery(
                    tenant_id=task.tenant_id,
                    actor_user_id=task.actor_user_id,
                    execution_id=task.id,
                    call_id=call_id,
                )
                declared_call = declared_by_id.get(call_id)
                if (
                    declared_call is None
                    or declared_call.prepared_args_hash is None
                    or declared_call.prepared_args_hash != recovery.input_hash
                ):
                    raise AgenticError(
                        "TOOL_OPERATION_INPUT_MISMATCH",
                        "operation arguments do not match the prepared call",
                        retryable=False,
                    )
                await ensure_child(
                    call_id,
                    view.operation_id,
                    recovery.tool_name or "",
                    recovery.arguments,
                )
                view = await operations.get(
                    tenant_id=task.tenant_id,
                    actor_user_id=task.actor_user_id,
                    operation_id=view.operation_id,
                )
                views[call_id] = view
            if view.result is not None:
                _replace_tool_message(messages, call_id, operation_result_content(view.result))
                by_call.pop(call_id, None)
            else:
                by_call[call_id] = watermark_of(view)
        pending = [by_call[call_id] for call_id in sorted(by_call)]
    if not has_history and not pending:
        return None
    return ResumeState(
        checkpoint=checkpoint,
        messages=tuple(messages),
        pending=tuple(pending),
        active_skills=folded.active_skills,
    )


def _async_skill_tools(frozen: FrozenAgenticExecution) -> frozenset[str]:
    """冻结快照里所有 ASYNC Skill 的注册工具名：崩后重建只对它们按调用身份重提交。"""
    skills = {entry.skill_id: entry for entry in frozen.snapshot.skills}
    names: set[str] = set()
    for tool in frozen.snapshot.tools:
        skill = skills.get(tool.skill_id) if tool.skill_id is not None else None
        if (
            tool.source == "SKILL"
            and tool.skill_id is not None
            and skill is not None
            and skill.execution_mode == "ASYNC"
        ):
            names.add(skill_tool_name(tool.skill_id, tool.tool_name))
    return frozenset(names)


async def _history_from_events(
    sessions: async_sessionmaker[AsyncSession],
    task: TaskExecution,
    *,
    async_tools: Collection[str] = (),
    max_identical_failures: int,
) -> FoldedHistory:
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(TaskEvent)
                .where(
                    TaskEvent.tenant_id == task.tenant_id,
                    TaskEvent.entity_type == EntityType.EXECUTION.value,
                    TaskEvent.entity_id == task.id,
                    TaskEvent.event_type.in_(
                        (TaskEventType.ASSISTANT_TURN.value, TaskEventType.TOOL_CALL.value)
                    ),
                    TaskEvent.is_deleted.is_(False),
                )
                .order_by(TaskEvent.seq)
            )
        )
    return _fold_events(
        rows,
        async_tools=async_tools,
        consumed_event_seq=max((row.seq for row in rows), default=0),
        max_identical_failures=max_identical_failures,
    )


def _fold_events(
    rows: Sequence[TaskEvent],
    *,
    async_tools: Collection[str] = (),
    consumed_event_seq: int = 0,
    max_identical_failures: int = 1,
) -> FoldedHistory:
    """事件 → 合法历史：保留**所有** assistant 声明，每个声明都有显式结果。"""
    events = [
        {"event_type": row.event_type, "payload": row.payload_json or {}}
        for row in rows
    ]
    fold = fold_events(events)
    messages = history_messages(fold, async_tools=async_tools)
    declared = tuple(
        DeclaredCall(
            call.declaration.call_id,
            call.declaration.tool_name,
            dict(call.declaration.arguments),
            completed=call.completed is not None,
            prepared=call.prepared is not None,
            prepared_args_hash=call.prepared.args_hash if call.prepared is not None else None,
        )
        for call in fold.calls
    )
    failure_state = replay_failure_state(fold, max_identical_failures=max_identical_failures)
    return FoldedHistory(
        messages,
        declared,
        active_skills=folded_active_skills(fold),
        turns=len(fold.turns),
        tool_calls=sum(1 for call in fold.calls if call.prepared or call.completed),
        failure_state=(
            None if failure_state == FailureState() else failure_state.as_payload()
        ),
        consumed_event_seq=consumed_event_seq,
    )


def _completed_content(payload: Mapping[str, Any]) -> str:
    artifact_id = payload.get("artifact_id")
    if isinstance(artifact_id, str) and artifact_id:
        return json.dumps({"artifact": {"artifact_id": artifact_id}}, ensure_ascii=False)
    result = payload.get("result")
    return result if isinstance(result, str) else ""


def _replace_tool_message(messages: list[ModelMessage], call_id: str, content: str) -> None:
    """把某个 `call_id` 的 tool 消息换成真实结果（缺则补一条）；重复恢复是幂等的。"""
    for index, message in enumerate(messages):
        if message.role is ModelRole.TOOL and message.tool_call_id == call_id:
            messages[index] = ModelMessage(role=ModelRole.TOOL, content=content, tool_call_id=call_id)
            return
    messages.append(ModelMessage(role=ModelRole.TOOL, content=content, tool_call_id=call_id))


__all__ = [
    "AgenticDependencies",
    "AgenticOutcome",
    "ResumeState",
    "TASK_BUDGET_EXCEEDED",
    "TASK_NO_PROGRESS",
    "agent_policy",
    "base_messages",
    "build_verifier",
    "execute_agentic",
    "load_resume_state",
    "prompt_skills",
]
