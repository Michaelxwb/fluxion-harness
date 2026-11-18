"""把冻结快照装配成一次真实 Agentic 执行（Kernel + 全部宿主端口）。

装配顺序就是 design §3.2.4 的步骤：快照 → 模型池/容量 → 工具目录（allowlist + 冻结授权）→
只读记忆 → 事件/checkpoint/挂起端口 → 压缩/整轮外置 → Kernel。

**这里没有第二个循环**：执行只调用 `AgentRunner.run`。宿主差异只体现在端口实现里：
模型来自容量租约、事件写 task_event、checkpoint 写 task_execution_checkpoint、挂起交给
WorkerLoop 落等待状态。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
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
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall, ModelUsage
from muad_agent_core.prompt import PromptSkill
from muad_agent_core.sandbox import WorkspaceStore
from muad_agent_core.skill import SkillArtifactResolver, SkillExecutor
from muad_agent_core.tools import ToolExecutionPipeline
from muad_common import SharedSettings
from muad_contracts import OperationWatermark
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.console_credentials import ConsoleCredentialClient
from ...infrastructure.models.task import TaskEvent, TaskExecution, TaskExecutionCheckpoint
from ...infrastructure.task_artifact_client import TaskArtifactClient
from ...infrastructure.task_capacity_client import TaskCapacityClient
from ...infrastructure.task_operation_client import TaskOperationClient, TaskOperationError
from ..task_events import EntityType, TaskEventType
from .artifacts import TaskArtifactRoundWriter
from .async_results import (
    DeclaredCall,
    OperationSubmitter,
    read_operations,
    reconcile_operations,
)
from .cancellation import CancellationProbe
from .checkpoints import ExecutionIdentity, TaskCheckpointStore, checkpoint_from_state
from .compaction import SummaryGenerator, TaskContextCompactor, compaction_settings
from .errors import AgenticError
from .events import TaskEventSink
from .mcp import McpServerRuntime, McpToolBox
from .memory import frozen_memory_message
from .model import CapacityModelPool, ModelTarget
from .snapshot import FrozenAgenticExecution, load_frozen_execution
from .suspension import AgenticSuspension, SuspensionBoundary
from .tools import AgenticToolRegistry, SkillToolRunner, build_registry, skill_tool_name

logger = logging.getLogger("muad_agent_worker.application.agentic.run")

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
    capacity: TaskCapacityClient | None = None
    credentials: ConsoleCredentialClient | None = None
    operations: TaskOperationClient | None = None
    artifacts: TaskArtifactClient | None = None
    mcp_http: httpx.AsyncClient | None = None
    workspace_store: WorkspaceStore | None = None
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


def agent_policy(frozen: FrozenAgenticExecution) -> AgentPolicy:
    """预算映射：快照 `max_steps/max_tool_calls` 是硬上限，期限是剩余绝对时间。"""
    budget = frozen.snapshot.budget
    config = frozen.runtime_config
    defaults = AgentPolicy()
    remaining_ms = max(1, int((frozen.deadline_at - datetime.now(UTC)).total_seconds() * 1000))
    return AgentPolicy(
        max_turns=budget.max_steps or defaults.max_turns,
        max_tool_calls=budget.max_tool_calls or defaults.max_tool_calls,
        deadline_ms=remaining_ms,
        max_model_retries=_runtime_limit(config, "max_model_retries", defaults.max_model_retries),
        max_identical_failures=_runtime_limit(
            config, "max_identical_failures", defaults.max_identical_failures
        ),
    )


def _runtime_limit(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def prompt_skills(frozen: FrozenAgenticExecution) -> tuple[PromptSkill, ...]:
    skills: list[PromptSkill] = []
    for entry in frozen.snapshot.skills:
        frontmatter = entry.frontmatter
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        label = frontmatter.get("platform_label")
        skills.append(
            PromptSkill(
                key=str(frontmatter.get("key") or entry.skill_id),
                name=name if isinstance(name, str) and name else str(entry.skill_id),
                description=description if isinstance(description, str) else "",
                platform_label=label if isinstance(label, str) and label else None,
            )
        )
    return tuple(skills)


def base_messages(frozen: FrozenAgenticExecution) -> tuple[ModelMessage, ...]:
    """起点消息：只读记忆块（若有）+ 本次目标。目标来自 Fire 冻结的输入。"""
    memory = frozen_memory_message(frozen.memory_block)
    goal = ModelMessage(role=ModelRole.USER, content=frozen.goal)
    return (memory, goal) if memory is not None else (goal,)


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
    mcp_servers = await _mcp_servers(frozen, deps)
    mcp_box = (
        McpToolBox(mcp_servers, deps.mcp_http)
        if mcp_servers and deps.mcp_http is not None
        else None
    )
    # 执行器与工具执行者必须拿到**同一个**挂起端口：否则工具已经暂存了异步提交，
    # Kernel 在回合收口时看不到它，会继续跑下一个模型回合（实测会把一次提交跑成 N 轮）。
    skill_runner = skill_runner_factory(suspension) if skill_runner_factory is not None else None
    agentic_tools: AgenticToolRegistry = build_registry(
        frozen, mcp=mcp_box, skills=skill_runner, human=suspension
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
    request = AgentRunRequest(
        model_id=target.model_name,
        instructions=frozen.instructions,
        messages=resume.messages if resume is not None else base_messages(frozen),
        skills=prompt_skills(frozen),
        policy=agent_policy(frozen),
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
    return AgenticOutcome(
        status=result.status,
        final_text=result.final_text,
        turns=result.turns,
        tool_calls=result.tool_calls,
        usage=result.usage,
        boundary=suspension.take(),
        last_failure=result.last_failure,
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
        on_tool_started=events.on_tool_started,
        on_tool_completed=events.on_tool_completed,
        checkpoint=checkpoint,
    )
    if not result.final_text.strip() and result.status is AgentRunStatus.COMPLETED:
        raise AgenticError("AGENTIC_EMPTY_RESULT", "agentic execution produced no answer")
    return result


def _round_writer(
    frozen: FrozenAgenticExecution, deps: AgenticDependencies
) -> TaskArtifactRoundWriter | None:
    if deps.artifacts is None:
        return None
    settings = compaction_settings(frozen.snapshot.policy).tool_result
    return TaskArtifactRoundWriter(
        deps.artifacts,
        settings,
        tenant_id=frozen.tenant_id,
        execution_id=frozen.execution_id,
    )


def _compactor(
    frozen: FrozenAgenticExecution, deps: AgenticDependencies, pool: CapacityModelPool
) -> TaskContextCompactor | None:
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
    if not settings.snip.enabled and not settings.micro.enabled and summary is None:
        return None
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


@dataclass(frozen=True, slots=True)
class FoldedHistory:
    """事件重建出来的历史 + 最后一个 assistant 回合声明过的工具调用。"""

    messages: tuple[ModelMessage, ...]
    declared: tuple[DeclaredCall, ...]


async def load_resume_state(
    sessions: async_sessionmaker[AsyncSession],
    task: TaskExecution,
    *,
    operations: OperationSubmitter | None,
) -> ResumeState | None:
    """没有 checkpoint 就是首次执行；有则重建历史并把已就绪的异步结果放回原位。

    恢复的唯一依据是 `last_checkpoint_id`（本执行自己的 checkpoint）：历史来自 `task_event`
    的 assistant/tool 成对事件，异步结果按**调用身份**从 Task-owned 收件箱取回并**替换**
    对应 `call_id` 的 tool 消息——提交回执被真实结果覆盖，重复恢复得到同一份历史。

    `pending` 非空表示这一挂起还没等齐：调用方据此补写等待，**不得**跑模型（历史里那些
    tool 消息还只是提交回执，等于"声明了 tool_call 却没有结果"的非法回合）。
    """
    if task.last_checkpoint_id is None:
        return None
    async with sessions() as session:
        row = await session.scalar(
            select(TaskExecutionCheckpoint).where(
                TaskExecutionCheckpoint.id == task.last_checkpoint_id,
                TaskExecutionCheckpoint.tenant_id == task.tenant_id,
            )
        )
    if row is None:
        return None
    checkpoint = checkpoint_from_state(row.state_json)
    frozen = load_frozen_execution(task)
    folded = await _history_from_events(sessions, task)
    messages = [*base_messages(frozen), *folded.messages]
    pending = checkpoint.pending_tool_calls
    if operations is not None and (pending or folded.declared):
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
                # 没有待定水位可兜底：历史里可能有"声明了却没有结果"的调用，跑模型只会得到
                # 非法回合。按可重试失败收尾，下一次尝试重新读。
                raise AgenticError(
                    "TOOL_OPERATION_UNAVAILABLE", f"operation read failed: {exc.code}", retryable=True
                ) from exc
            # 读不到就保持待定：补扫下一次接着推进（结果在 Runtime，丢不了）。
            logger.warning(
                "agentic_resume_operation_read_failed",
                extra={"execution_id": str(task.id), "code": exc.code},
            )
            return ResumeState(checkpoint=checkpoint, messages=tuple(messages), pending=pending)
        # 重提交撞上"同一调用换了参数"是确定性冲突：原样上抛，由执行器落精确错误码。
        reconciled = await reconcile_operations(
            operations,
            views=views,
            tenant_id=task.tenant_id,
            actor_user_id=task.actor_user_id,
            execution_id=task.id,
            declared=folded.declared,
            async_calls=_async_skill_tools(frozen),
            pending=pending,
        )
        for call_id, content in reconciled.ready.items():
            _replace_tool_message(messages, call_id, content)
        pending = reconciled.pending
    return ResumeState(checkpoint=checkpoint, messages=tuple(messages), pending=pending)


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
    sessions: async_sessionmaker[AsyncSession], task: TaskExecution
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
    return _fold_events(rows)


def _fold_events(rows: Sequence[TaskEvent]) -> FoldedHistory:
    """事件 → 合法历史：**每个 assistant 回合紧跟它自己的 tool 结果**，按回合顺序。

    早期实现把所有 tool 消息堆在所有 assistant 之后——单回合时看不出问题，多轮恢复时
    `assistant1, assistant2, tool1, tool2` 是非法历史（tool 必须紧随声明它的 assistant）。

    `declared` 覆盖**所有**回合：更早回合的异步结果同样要在恢复时从收件箱取回并替换掉
    事件里的提交回执，否则第二次挂起之后模型看到的是第一轮的"回执"而不是结果。
    """
    messages: list[ModelMessage] = []
    merged: list[DeclaredCall] = []
    current: tuple[DeclaredCall, ...] = ()
    results: dict[str, str] = {}

    def flush() -> None:
        for call in current:
            content = results.get(call.call_id)
            if content is not None:
                messages.append(
                    ModelMessage(role=ModelRole.TOOL, content=content, tool_call_id=call.call_id)
                )
        merged.extend(replace(call, completed=call.call_id in results) for call in current)

    for row in rows:
        payload = row.payload_json or {}
        if row.event_type == TaskEventType.ASSISTANT_TURN.value:
            flush()
            current = tuple(
                DeclaredCall(
                    str(entry.get("id")),
                    str(entry.get("name")),
                    dict(entry.get("arguments") or {}),
                )
                for entry in payload.get("tool_calls") or ()
                if isinstance(entry, dict)
            )
            messages.append(
                ModelMessage(
                    role=ModelRole.ASSISTANT,
                    content=str(payload.get("text") or ""),
                    tool_calls=tuple(
                        ModelToolCall(call.call_id, call.tool_name, dict(call.arguments))
                        for call in current
                    ),
                    reasoning_content=payload.get("reasoning_content"),
                )
            )
            results = {}
            continue
        if payload.get("state") != "COMPLETED":
            continue
        call_id = payload.get("tool_call_id")
        if isinstance(call_id, str):
            results[call_id] = _completed_content(payload)
    flush()
    return FoldedHistory(tuple(messages), tuple(merged))


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
