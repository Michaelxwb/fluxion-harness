from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from muad_agent_core.agent import AgentPolicy, RunnerModelError
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.runner import AgentRunStatus, RunnerDeadlineExceeded
from muad_agent_core.model import ModelMessage
from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_contracts import (
    AsyncToolPolicy,
    AttachmentRef,
    ChannelContext,
    CompletionMode,
    DeliveryRouteInput,
    OperationStatus,
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    ResolveModelRequest,
    RunRequest,
    RunStatus,
    tool_waiting_reason,
)
from muad_contracts.memory import MemoryResumeRef
from muad_contracts.platform_settings import (
    AgentSettings,
    ArtifactSettings,
    CompactionSettings,
    LocaleSettings,
    MemoryPolicySettings,
    PlatformSettingsError,
    compaction_payload,
    default_compaction_settings,
    parse_platform_settings,
)
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.db import get_session_factory
from ..infrastructure.model_dispatch_store import MemberRef, ModelDispatchStore
from ..infrastructure.models.async_tools import RunContinuation, ToolOperation
from ..infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
    RunSubmission,
    RuntimeSnapshot,
)
from ..metrics import (
    AGENT_RUNS_METRIC,
    CONTEXT_SUMMARY_METRIC,
    RUN_RECLAIM_METRIC,
    record_counter,
    record_outcome,
)
from .async_tools.continuation_service import (
    ExecutionIdentity,
    ExecutionLeaseLost,
    claim_continuation,
    ensure_execution,
    lock_execution,
    renew_execution,
)
from .async_tools.supervisor import ExecutionSupervisor
from .attachments.inbound import (
    INBOUND_DOCUMENT,
    INBOUND_IMAGE,
    INBOUND_OTHER,
    PersistedAttachment,
    attachment_payload,
    build_current_content,
    persist_inbound_attachments,
)
from .context_builder import BudgetPolicy, DbBackedContextBuilder
from .context_settings import (
    PoolExecutionPolicy,
    compaction_settings_of,
    pool_execution_policy_of,
    pool_policy_payload,
    resolve_compaction_settings,
    resolve_pool_execution_policy,
)
from .dto import RunWaitSummary
from .executor import (
    ExecutionDefaults,
    ExecutorCredentials,
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    ExecutorRunContext,
    RunExecutor,
    default_executor_factory,
)
from .memory_confirmation_resume import consume_confirmation, prepare_confirmation
from .memory_proposals import _database_now
from .memory_resume import MemoryResumeService, resume_decision
from .ports import (
    CredentialsClient,
    PlatformSettingsClient,
    ResolveClient,
)
from .run_admission import (
    AdmissionPolicyService,
    AdmissionPolicyStaleError,
    parse_capacity,
)
from .run_events import EventWriter
from .run_resume import (
    ensure_human_resume,
    lock_human_resume,
    resolve_generic_interrupts,
    transition_human_resume,
)
from .run_submission import (
    ENDPOINT_CREATE_CONVERSATION,
    ENDPOINT_CREATE_RUN,
    ENDPOINT_RESUME_RUN,
    SUBMISSION_CLOSED,
    RunSubmissionService,
    require_resume_idempotency,
    submission_fingerprint,
)
from .tool_result_receipts import OP_TERMINAL

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.WAITING_INPUT, RunStatus.WAITING_TOOL)
TERMINAL_RUN_STATUSES = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)
PROMPT_TEMPLATE_VERSION = "1"


def snapshot_policy(
    compaction: CompactionSettings | None = None,
    execution: ExecutionDefaults | None = None,
    summary_model: ResolvedModel | None = None,
    pool: PoolExecutionPolicy | None = None,
    members: Sequence[MemberRef] | None = None,
) -> dict[str, Any]:
    """Run 侧 execution snapshot 的执行期策略——`policy_json` 的唯一构建口径。

    `agent` 分组四叶（`max_turns` / `max_tool_calls` / `deadline_ms` / `max_model_retries`，见
    ADR-07）、完整 `memory` 分组、`artifact.max_archive_files`、`locale.default_timezone` 与压缩
    配置一并冻结在这里：配置变更只影响后续新 Run，在飞的 Run 用的是它自己那一份冻结值
    （`harness-snapshot#RULE-snapshot-001` 的"Run 侧等价载体"）。

    **模型池语义与非密钥执行策略**同样冻在这里（B-224）：

    - `model_pool`：路由五叶 + `dispatch_policy_hash`（与 TASK-023 同一函数，唯一口径）；
    - `capacity`：Run 创建时冻结的模型等待预算（准入/排队叶属 TASK-029/030 的边界）；
    - `model_members`：成员的非密钥引用与权重（`deployment_id` / `credential_id` / `weight`）；
      Key 明文、去重指纹、由其派生的计量身份与 `credential_revision` 都**不进快照**——调用
      凭据实时经 API-09 按冻结引用解析。输出预算就是 `model_json.params` 里真正发送的
      `max_tokens`（TASK-019 起写入即要求正整数），不在这里另存一份。
    - `members=None` 表示调用点没有成员事实可冻结（直构/历史路径）——**不写空列表冒充**；
      `[]` 才是"该模型当前确实没有成员"的冻结事实。

    `summary_model` 是 `compaction.summary.model_ref` 指向的那条模型定义**解析后的结果**（ADR-06）：
    摘要要打到自己那个 endpoint / 凭据上，所以在 Run 创建边界解析一次、连同非密钥字段一起冻进
    本快照（`api_key` 剥离，认证走 API-09 实时读）。

    `artifact.retention_days` / `artifact.cleanup_batch_size` **不进本快照**：它们属 Console
    清理（一条 CLI 操作，不是 Run），在操作边界从平台设置取。
    """
    settings = compaction if compaction is not None else default_compaction_settings()
    defaults = execution if execution is not None else ExecutionDefaults()
    agent = defaults.agent_policy
    memory = defaults.memory_policy
    policy: dict[str, Any] = {
        "async_tools": AsyncToolPolicy().model_dump(mode="json"),
        "max_turns": agent.max_turns,
        "max_tool_calls": agent.max_tool_calls,
        "deadline_ms": agent.deadline_ms,
        "max_model_retries": agent.max_model_retries,
        "memory": asdict(memory),
        "artifact": {"max_archive_files": defaults.max_archive_files},
        "locale": {"default_timezone": defaults.timezone},
        "compaction": compaction_payload(settings),
    }
    frozen_pool = pool if pool is not None else resolve_pool_execution_policy(None)
    policy.update(pool_policy_payload(frozen_pool))
    if members is not None:
        policy[MODEL_MEMBERS_POLICY_KEY] = [frozen_member_payload(member) for member in members]
    if summary_model is not None:
        # 只在真的解析出摘要模型时写这个键：老快照没有它 ⇔ 该 Run 的摘要层没跑（`summary_model_of`
        # 读回 None），语义与"配置里没开摘要"一致。
        policy["summary_model"] = _snapshot_model(summary_model)
    return policy


def frozen_member_payload(member: MemberRef) -> dict[str, Any]:
    """一名成员被冻结成**恰三键**：非密钥身份 + 权重（每一项都参与 content_hash）。"""
    return {
        "deployment_id": str(member.deployment_id),
        "credential_id": str(member.credential_id),
        "weight": member.weight,
    }


def frozen_model_members_of(policy: Mapping[str, Any] | None) -> tuple[MemberRef, ...] | None:
    """从**已冻结**的 `policy_json` 还原成员非密钥引用；缺该键（旧行/直构路径）返回 None。

    形状坏（键集不符 / 引用非 UUID / 权重非正整数）显式抛 `PlatformSettingsError`：冻结数据
    是 Run 的执行语义，宁可失败也不按猜出的值解释。旧行**不补新默认**——返回 None 而不是
    当前库里的成员事实。
    """
    entries = (policy or {}).get(MODEL_MEMBERS_POLICY_KEY)
    if entries is None:
        return None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise PlatformSettingsError("frozen model members must be a list")
    return tuple(_frozen_member(entry) for entry in entries)


def _frozen_member(entry: Any) -> MemberRef:
    if not isinstance(entry, Mapping) or set(entry) != set(FROZEN_MEMBER_KEYS):
        raise PlatformSettingsError("frozen model member must carry exactly its non-secret keys")
    weight = entry["weight"]
    if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
        raise PlatformSettingsError("frozen model member weight must be a positive integer")
    try:
        return MemberRef(
            deployment_id=uuid.UUID(str(entry["deployment_id"])),
            credential_id=uuid.UUID(str(entry["credential_id"])),
            weight=weight,
        )
    except (ValueError, AttributeError) as exc:
        raise PlatformSettingsError("frozen model member references must be UUIDs") from exc


def summary_model_of(policy: Mapping[str, Any] | None) -> ResolvedModel | None:
    """从**已冻结**的 `policy_json` 还原摘要模型（resume 与执行期都走这条）。

    没有这个键（未开摘要 / 解析失败 / 本需求上线前创建的行）⇒ 返回 None：摘要层本轮不跑。
    **绝不**退回主模型——那正是修复前的行为（配置写了却不生效）。
    """
    if not isinstance(policy, Mapping):
        return None
    section = policy.get("summary_model")
    if not isinstance(section, Mapping):
        return None
    try:
        return ResolvedModel.model_validate(dict(section))
    except ValidationError:
        return None


def _section(policy: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = policy.get(key)
    return value if isinstance(value, Mapping) else {}


def _frozen_int(source: Mapping[str, Any], key: str, default: int) -> int:
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _frozen_str(source: Mapping[str, Any], key: str, default: str) -> str:
    value = source.get(key)
    return value if isinstance(value, str) and value else default


def _frozen_memory_policy(policy: Mapping[str, Any]) -> MemoryPolicySettings:
    section = _section(policy, "memory")
    if set(section) != set(asdict(MemoryPolicySettings())):
        raise PlatformSettingsError("snapshot memory policy must contain every schema leaf")
    return parse_platform_settings({"memory": dict(section)}).memory


def execution_defaults_of(policy: Mapping[str, Any] | None) -> ExecutionDefaults:
    """Restore the frozen execution policy; incomplete memory snapshots are invalid.

    Maintenance drains legacy in-flight Runs before deployment. Historical terminal snapshots
    stay unchanged for audit and do not participate in resume or consolidation.
    """
    if not isinstance(policy, Mapping):
        return ExecutionDefaults()
    agent_defaults = AgentSettings()
    return ExecutionDefaults(
        agent_policy=AgentPolicy(
            max_turns=_frozen_int(policy, "max_turns", agent_defaults.max_turns),
            max_tool_calls=_frozen_int(policy, "max_tool_calls", agent_defaults.max_tool_calls),
            deadline_ms=_frozen_int(policy, "deadline_ms", agent_defaults.deadline_ms),
            max_model_retries=_frozen_int(policy, "max_model_retries", agent_defaults.max_model_retries),
        ),
        memory_policy=_frozen_memory_policy(policy),
        max_archive_files=_frozen_int(
            _section(policy, "artifact"),
            "max_archive_files",
            ArtifactSettings().max_archive_files,
        ),
        timezone=_frozen_str(
            _section(policy, "locale"),
            "default_timezone",
            LocaleSettings().default_timezone,
        ),
    )


def execution_defaults_from_platform(document: Mapping[str, Any] | None) -> ExecutionDefaults:
    """从平台设置快照（整份文档）解析 Run 的执行期默认，缺分组回落 schema 默认。

    非法文档显式抛 `PlatformSettingsError`（Console 是唯一写入口，已在校验层把住）——与
    "源不可读即明确失败"同一条纪律，绝不静默回退到过期默认值。
    """
    parsed = parse_platform_settings(document)
    agent = parsed.agent
    return ExecutionDefaults(
        agent_policy=AgentPolicy(
            max_turns=agent.max_turns,
            max_tool_calls=agent.max_tool_calls,
            deadline_ms=agent.deadline_ms,
            max_model_retries=agent.max_model_retries,
        ),
        memory_policy=parsed.memory,
        max_archive_files=parsed.artifact.max_archive_files,
        timezone=parsed.locale.default_timezone,
    )


def history_budget_of(policy: Mapping[str, Any] | None) -> int | None:
    """从已冻结的 `policy_json` 取历史预算（消息条数）；缺失/形状不对时返回 None。

    这一个值有两个消费者，且**必须是同一个**：装配侧的取数守卫（`load_history`）与压缩层的条数
    兜底（`trim_history`）。返回 None 让两边各自回落默认值——resume 读到的是**这一行当时**冻结的
    预算，而不是当前配置，配置改动因此只影响后续新 Run。
    """
    compaction = (policy or {}).get("compaction")
    value = compaction.get("history_budget_messages") if isinstance(compaction, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def history_tool_rounds_of(policy: Mapping[str, Any]) -> int:
    """Use the source Run's frozen tool history budget, including its zero value."""
    settings = compaction_settings_of(policy)
    return (settings or default_compaction_settings()).max_history_tool_rounds


#: 「无平台设置时」的 schema 默认策略快照（导入期求值一次）。它不是运行期动态默认源：
#: 新 Run 的 `policy_json` 一律由 Run 创建边界取到的平台快照 + Agent 覆盖解析后冻结（`_create_run`）。
DEFAULT_POLICY: dict[str, Any] = snapshot_policy()
RUN_ABANDONED = "RUN_ABANDONED"
USER_MESSAGE_EVENT = "USER_MESSAGE"
ASSISTANT_MESSAGE_EVENT = "ASSISTANT_MESSAGE"
CANCEL_EVENT = "CANCEL"
RUN_CREATED_EVENT = "run.created"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"
INTERRUPT_WAITING = "WAITING"
INTERRUPT_RESOLVED = "RESOLVED"
INTERRUPT_CANCELLED = "CANCELLED"
TERMINAL_STREAM_TYPES = (RUN_COMPLETED_EVENT, RUN_FAILED_EVENT)
REPLAY_POLL_SEC = 0.5
CONVERSATION_ACTIVE = "ACTIVE"

STREAM_BUSINESS_TYPES: dict[str, str] = {
    RUN_CREATED_EVENT: "RUN_CREATED",
    "message.delta": "ASSISTANT_DELTA",
    "model.started": "MODEL_CALL_STARTED",
    "model.completed": "MODEL_CALL_COMPLETED",
    "tool.started": "TOOL_CALL_STARTED",
    "tool.completed": "TOOL_CALL",
    "assistant.turn": "ASSISTANT_TURN",
    "skill.loaded": "SKILL_LOADED",
    "artifact.created": "ARTIFACT_CREATED",
    "interrupt.required": "INTERRUPT_REQUIRED",
    "task.accepted": "TASK_ACCEPTED",
    RUN_COMPLETED_EVENT: ASSISTANT_MESSAGE_EVENT,
    RUN_FAILED_EVENT: "RUN_FAILED",
}

#: `policy_json` 里冻结的成员段：非密钥引用 + 权重（Key/指纹/计量身份不在其中）
MODEL_MEMBERS_POLICY_KEY = "model_members"
#: 一名冻结成员允许携带的全部键；多一个（例如 `key_resource_id`）就是泄漏，显式拒绝
FROZEN_MEMBER_KEYS = ("deployment_id", "credential_id", "weight")


@dataclass(frozen=True)
class RunStart:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    resumed: bool
    events: AsyncIterator[ExecutorEvent]


@dataclass(frozen=True)
class FinalState:
    status: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class SegmentResult:
    outcome: AgentRunStatus = AgentRunStatus.COMPLETED
    final_text: str = ""
    failure: Exception | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _lease_deadline(seconds: int) -> datetime:
    return _utcnow() + timedelta(seconds=seconds)


def _event_artifact_id(data: Mapping[str, Any]) -> uuid.UUID | None:
    """流事件载荷里的产物 id → canonical 行的 `artifact_id` **列**（design ADR-05）。

    重建历史时按**列**取回产物（预览 + 引用 JSON），所以这一步必须把 id 提上来：此前只写在
    `payload_json` 里，列恒为 NULL，于是外置过的工具结果跨 Run 重建时退化成 `[tool:名称]`。
    工具返回的引用 JSON 是**模型自己写的内容**，不是可信 UUID：解析失败按"没有产物"处理并留
    一条警告 —— 一个坏 id 不该让整条 Run 挂掉。
    """
    raw = data.get("artifact_id")
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        logger.warning("event_artifact_id_invalid", extra={"artifact_id": str(raw)[:64]})
        return None


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_snapshot(
    *,
    run_id: uuid.UUID,
    tenant_id: str,
    resolved: ResolveDefinitionResponse,
    compaction: CompactionSettings | None = None,
    execution: ExecutionDefaults | None = None,
    summary_model: ResolvedModel | None = None,
    pool: PoolExecutionPolicy | None = None,
    members: Sequence[MemberRef] | None = None,
) -> RuntimeSnapshot:
    # 未显式传入时按「无平台设置」（`platform_overrides={}`）合并 Agent 覆盖——仅用于不经过
    # Run 创建边界的直构调用点；生产路径由 `_create_run` 在取到平台快照后显式传入。
    settings = (
        compaction
        if compaction is not None
        else resolve_compaction_settings(resolved.agent.runtime_config, platform_overrides={})
    )
    policy = snapshot_policy(settings, execution, summary_model, pool, members)
    from muad_contracts import AsyncToolPolicy

    policy["async_tools"] = AsyncToolPolicy.model_validate(
        resolved.agent.runtime_config.get("async_tools", {})
    ).model_dump(mode="json")
    return RuntimeSnapshot(
        tenant_id=tenant_id,
        run_id=run_id,
        schema_version=1,
        agent_revision=resolved.agent.revision,
        model_revision=resolved.model.revision,
        agent_json=resolved.agent.model_dump(mode="json"),
        model_json=_snapshot_model(resolved.model),
        skill_catalog_json=[skill.model_dump(mode="json") for skill in resolved.skills],
        mcp_catalog_json=[server.model_dump(mode="json") for server in resolved.mcp_servers],
        policy_json=policy,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        content_hash=_snapshot_hash(resolved, policy),
    )


def delivery_route_of(channel_json: dict[str, Any] | None) -> DeliveryRouteInput | None:
    """从 Run 持久化的入站渠道构建后台任务投递路由；缺接收方时返回 None（不投递）。"""
    if not channel_json:
        return None
    try:
        channel = ChannelContext.model_validate(channel_json)
    except ValidationError:
        return None
    if not channel.external_user_id:
        return None
    return DeliveryRouteInput(
        channel=channel.type,
        bot_id=channel.bot_id,
        external_user_id=channel.external_user_id,
        external_conversation_id=channel.external_conversation_id,
    )


def _snapshot_model(model: Any) -> dict[str, Any]:
    """Snapshot/hash 中的模型信息剥离认证字段（api_key 只走 API-09 实时读取）。"""
    data: dict[str, Any] = model.model_dump(mode="json")
    data.pop("api_key", None)
    return data


def _snapshot_hash(resolved: ResolveDefinitionResponse, policy: dict[str, Any]) -> str:
    canonical = _canonical_json(
        {
            "agent": resolved.agent.model_dump(mode="json"),
            "model": _snapshot_model(resolved.model),
            "skills": [skill.model_dump(mode="json") for skill in resolved.skills],
            "mcp_servers": [server.model_dump(mode="json") for server in resolved.mcp_servers],
            "policy": policy,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        }
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def reap_abandoned_runs(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        candidates = list(
            await session.scalars(
                sa.select(RunRecord)
                .where(
                    RunRecord.status == RunStatus.RUNNING,
                    RunRecord.lease_until < sa.func.now(),
                    RunRecord.is_deleted.is_(False),
                )
                .order_by(RunRecord.lease_until, RunRecord.id)
                .limit(128)
            )
        )
    reaped = 0
    for candidate in candidates:
        reaped += await _reap_one(session_factory, candidate)
    if reaped:
        record_counter(RUN_RECLAIM_METRIC, reaped)
    return reaped


async def _reap_one(factory: async_sessionmaker[AsyncSession], candidate: RunRecord) -> int:
    from .async_tools.operations import cancel_operations
    from .tool_result_receipts import lock_run_source

    async with factory() as session, session.begin():
        run, _ = await lock_run_source(session, candidate.tenant_id, candidate)
        now = _utcnow()
        if run.status != RunStatus.RUNNING or run.lease_until is None or run.lease_until >= now:
            return 0
        await cancel_operations(session, run)
        changed = await session.scalar(
            sa.update(RunRecord)
            .where(
                RunRecord.id == run.id,
                RunRecord.tenant_id == run.tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status == RunStatus.RUNNING,
                RunRecord.lease_until < now,
            )
            .values(
                status=RunStatus.FAILED,
                error_code=RUN_ABANDONED,
                end_time=now,
                lease_owner=None,
                lease_until=None,
                update_time=now,
            )
            .returning(RunRecord.id)
        )
        if changed is None:
            return 0
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_FAILED",
            stream_type=RUN_FAILED_EVENT,
            payload={"status": str(RunStatus.FAILED), "error_code": RUN_ABANDONED},
        )
    return 1


def _creation_fingerprint(request: RunRequest, tenant_id: str) -> str:
    return submission_fingerprint(
        endpoint=ENDPOINT_CREATE_RUN,
        key_payload={
            # `tenant_id` 与 `endpoint` 是 RULE-api-002 要求的两个判别键
            "tenant_id": tenant_id,
            "agent_id": str(request.agent_id),
            "platform_user_id": str(request.platform_user_id),
            "conversation_id": str(request.conversation_id) if request.conversation_id else None,
            "channel": request.channel.type,
            "message_type": request.message.type,
            "message_text": request.message.text,
            # 附件与投递路由**都是请求语义的一部分**：同一个 key 换了附件、或换了接收方，
            # 就不是同一个请求 —— 必须撞 `IDEMPOTENCY_MISMATCH`，而不是把新附件悄悄丢掉、
            # 或把回复发给上一个人（2026-10-06 review）。附件取存储键 + 校验和（字节的稳定
            # 标识），路由取 `delivery_route_of` 用的那三个判别字段。
            "attachments": sorted(
                [ref.storage_key, ref.checksum, str(ref.kind), ref.media_type, ref.size]
                for ref in request.message.attachments
            ),
            "route": {
                "bot_id": request.channel.bot_id,
                "external_user_id": request.channel.external_user_id,
                "external_conversation_id": request.channel.external_conversation_id,
            },
        },
        message_id=request.message.id,
    )

def _credential_canaries(
    model: ResolvedModel, summary: ResolvedModel | None, secrets: Mapping[str, str],
) -> tuple[str, ...]:
    values = (model.api_key, summary.api_key if summary else None, *secrets.values())
    return tuple(value for value in values if value)


def _conversation_recency_key() -> ColumnElement[datetime]:
    """「当前会话」排序键：**用户最近发言**时间；从未发言回落 `create_time`。

    **不用 `conversation.update_time`**：`EventWriter._next_seq` 对任何事件（含后台收尾写向
    旧会话的 `MEMORY_CONSOLIDATED`）都会 bump 它，按它取最新会把 `/new` 刚建出的新会话顶掉，
    下一条入站（WS 不带会话指针）落回旧会话。用户发言以 `USER_MESSAGE` 事件为准；新会话在
    用户下次发言前以 `create_time` 保持当前。
    """
    last_user_message_at = (
        sa.select(sa.func.max(CanonicalEvent.create_time))
        .where(
            CanonicalEvent.conversation_id == Conversation.id,
            CanonicalEvent.event_type == USER_MESSAGE_EVENT,
            CanonicalEvent.is_deleted.is_(False),
        )
        .correlate(Conversation)
        .scalar_subquery()
    )
    return sa.func.coalesce(last_user_message_at, Conversation.create_time)


class RunService:
    def __init__(
        self,
        session: AsyncSession,
        resolve_client: ResolveClient,
        instance_id: str,
        settings_client: PlatformSettingsClient,
        executor_factory: ExecutorFactory = default_executor_factory,
        cancel_hints: CancelHintStore | None = None,
        credentials_client: CredentialsClient | None = None,
        submissions: RunSubmissionService | None = None,
        context_builder: DbBackedContextBuilder | None = None,
        *,
        supervisor: ExecutionSupervisor,
        admission: AdmissionPolicyService | None = None,
    ) -> None:
        self._session = session
        self._supervisor = supervisor
        self._resolve_client = resolve_client
        self._instance_id = instance_id
        self._executor_factory = executor_factory
        self._cancel_hints = cancel_hints or NullCancelHintStore()
        self._credentials_client = credentials_client
        # 平台设置源是**必填**：新 Run 在创建边界取一次快照并冻结。不留隐式 Null 默认——
        # 新建调用点忘记注入真 client 会静默按空设置跑；取消/回收/直构的调用点若要
        # 「无设置源」语义，显式传 `NullPlatformSettingsClient()`。
        self._settings_client: PlatformSettingsClient = settings_client
        # 全局准入（TASK-029/030）：策略行锁与 USER→TENANT 计数都在 Run 创建事务内完成，
        # 与设置源同一份快照；显式注入用于测试或替换装配。
        self._admission = admission or AdmissionPolicyService(
            get_session_factory, settings_client
        )
        self._submissions = submissions or RunSubmissionService(get_session_factory)
        self._context_builder = context_builder or DbBackedContextBuilder(
            session_factory=get_session_factory,
            budget=BudgetPolicy(max_messages=default_compaction_settings().history_budget_messages),
        )
        self._settings = SharedSettings()

    async def start(
        self,
        request: RunRequest,
        tenant_id: str,
        idempotency_key: str | None = None,
    ) -> RunStart:
        """受理入口：幂等重放优先；新提交在建 Run 的事务内完成全局准入与计数占位。

        读到更低设置版本（`AdmissionPolicyStaleError`）时退出事务后**重取一次**有效配置，
        绝不按旧阈值放行；重取仍低即显式失败（§3.7）。
        """
        key = idempotency_key or request.message.id
        fingerprint = _creation_fingerprint(request, tenant_id)
        for attempt in range(self._admission.stale_refresh_attempts + 1):
            try:
                return await self._admit(request, tenant_id, key, fingerprint)
            except AdmissionPolicyStaleError:
                # 先退出事务（连同 policy 行锁与未提交的 Run/会话）再重取，锁内不联网。
                await self._session.rollback()
                logger.warning(
                    "admission_policy_stale_on_create",
                    extra={"tenant_id": tenant_id, "attempt": attempt},
                )
                continue
        raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)

    async def _admit(
        self, request: RunRequest, tenant_id: str, key: str, fingerprint: str
    ) -> RunStart:
        """一次受理尝试：重放检查 → 同键串行化 → 会话/活跃 Run 判定 → 建 Run（含准入）。"""
        replay = await self._submissions.find_replay_in(
            self._session,
            tenant_id=tenant_id,
            idempotency_key=key,
            endpoint=ENDPOINT_CREATE_RUN,
            fingerprint=fingerprint,
        )
        if replay is not None:
            self._assert_replay_owner(replay, request.platform_user_id, request.conversation_id)
            return self._replay_start(replay)
        # 同键请求先经事务 advisory 锁串行化（RULE-api-002）：锁先于 policy 行锁，同键事务
        # 只在 advisory 锁上排队；锁内复查首次提交 ⇒ 落败者直接重放，不建第二个 Run。
        replay = await self._submissions.claim_in(
            self._session,
            tenant_id=tenant_id,
            idempotency_key=key,
            endpoint=ENDPOINT_CREATE_RUN,
            fingerprint=fingerprint,
        )
        if replay is not None:
            self._assert_replay_owner(replay, request.platform_user_id, request.conversation_id)
            return self._replay_start(replay)
        conversation = await self._resolve_conversation(request, tenant_id)
        active = await self._active_run(conversation.id)
        if active is not None:
            return await self._resume_active(active, request, key, fingerprint)
        resolved = await self._resolve_client.resolve(
            ResolveDefinitionRequest(
                agent_id=request.agent_id,
                actor_user_id=request.platform_user_id,
                channel=request.channel.type,
            ),
            tenant_id=tenant_id,
            trace_id=current_trace_id(),
        )
        return await self._create_run(request, tenant_id, conversation, resolved, key, fingerprint)

    async def _resume_active(
        self, active: RunRecord, request: RunRequest, key: str, fingerprint: str
    ) -> RunStart:
        """会话已有活跃 Run：暂停中走 resume，其余明确 `RUN_BUSY`（不建第二个 Run）。"""
        if active.status != RunStatus.WAITING_INPUT:
            raise AppError(ErrorCode.RUN_BUSY)
        return await self._resume_run(
            active,
            request.message.text,
            message_id=request.message.id,
            # 暂停期间这条消息带的附件必须与文本一起进去（否则等于静默丢弃）
            attachments=request.message.attachments,
            submission_key=key,
            request_fingerprint=fingerprint,
            endpoint=ENDPOINT_CREATE_RUN,
        )

    async def resume(
        self,
        run_id: uuid.UUID,
        input_text: str,
        tenant_id: str,
        *,
        platform_user_id: uuid.UUID,
        idempotency_key: str | None = None,
        input_id: str | None = None,
    ) -> RunStart:
        run = await self._load_run(run_id, tenant_id)
        if run.user_id != platform_user_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        key = require_resume_idempotency(idempotency_key=idempotency_key, input_id=input_id)
        fingerprint = submission_fingerprint(
            endpoint=ENDPOINT_RESUME_RUN,
            run_id=run_id,
            key_payload={"tenant_id": tenant_id, "platform_user_id": str(platform_user_id),
                "type": "text", "text": input_text},
            message_id=input_id,
        )
        replay = await self._submissions.find_replay_in(
            self._session,
            tenant_id=tenant_id,
            idempotency_key=key,
            endpoint=ENDPOINT_RESUME_RUN,
            fingerprint=fingerprint,
        )
        if replay is not None:
            self._assert_replay_owner(replay, run.user_id, run.conversation_id)
            return self._replay_start(replay)
        if run.status in TERMINAL_RUN_STATUSES:
            return self._terminal_start(run)
        if run.status == RunStatus.WAITING_TOOL:
            # 工具等待由结果驱动接续（ContinuationPump），不能靠显式人类 resume 绕过：
            # 这是"会话仍有一个活跃 Run"的明确拒绝（RUN_BUSY），而不是把它解释成唤醒。
            raise AppError(ErrorCode.RUN_BUSY)
        if run.status != RunStatus.WAITING_INPUT:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        return await self._resume_run(
            run,
            input_text,
            message_id=input_id,
            submission_key=key,
            request_fingerprint=fingerprint,
            endpoint=ENDPOINT_RESUME_RUN,
        )

    async def tail_run_events(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        *,
        after_seq: int = 0,
    ) -> RunStart:
        """API-04：按已确认 canonical seq 重放/续跟既有 Run 的持久事件流。

        只读持久事件，不重新执行、不分配新序号、不重新 resolve 配置；终态排空后关闭。
        """
        run = await self._load_run(run_id, tenant_id)
        return RunStart(
            run_id=run.id,
            conversation_id=run.conversation_id,
            resumed=True,
            events=self._tail_events(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                after_seq=max(0, after_seq),
            ),
        )

    async def cancel_run(self, run_id: uuid.UUID, tenant_id: str) -> RunRecord:
        run = await self._load_run(run_id, tenant_id)
        return await self._cancel(run)

    async def cancel_active(
        self,
        agent_id: uuid.UUID,
        platform_user_id: uuid.UUID,
        tenant_id: str,
    ) -> RunRecord:
        run = await self._session.scalar(
            sa.select(RunRecord)
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.agent_id == agent_id,
                RunRecord.user_id == platform_user_id,
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
                RunRecord.is_deleted.is_(False),
            )
            .order_by(RunRecord.create_time.desc())
            .limit(1)
        )
        if run is None:
            raise AppError(ErrorCode.NO_ACTIVE_RUN)
        return await self._cancel(run)

    async def get_run(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
    ) -> tuple[RunRecord, RuntimeSnapshot | None, RunWaitSummary]:
        run = await self._load_run(run_id, tenant_id)
        snapshot = None
        if run.snapshot_id is not None:
            snapshot = await self._session.scalar(
                sa.select(RuntimeSnapshot).where(RuntimeSnapshot.id == run.snapshot_id)
            )
        return run, snapshot, await self._wait_summary(run)

    async def _wait_summary(self, run: RunRecord) -> RunWaitSummary:
        """API-05：等待起点/原因与异步操作计数；只读，一次聚合 + 一次检查点读取。"""
        join_count, submission_count = (
            await self._session.execute(
                sa.select(
                    sa.func.count().filter(
                        ToolOperation.completion_mode == CompletionMode.JOIN,
                        ToolOperation.status.not_in(OP_TERMINAL),
                    ),
                    sa.func.count().filter(ToolOperation.status == OperationStatus.SUBMIT_PENDING),
                ).where(
                    ToolOperation.tenant_id == run.tenant_id,
                    ToolOperation.run_id == run.id,
                    ToolOperation.is_deleted.is_(False),
                )
            )
        ).one()
        continuation = await self._session.scalar(
            sa.select(RunContinuation).where(
                RunContinuation.tenant_id == run.tenant_id,
                RunContinuation.run_id == run.id,
                RunContinuation.is_deleted.is_(False),
            )
        )
        return RunWaitSummary(
            waiting_since=await self._waiting_since(run),
            waiting_reason=tool_waiting_reason(
                run.status,
                resume_ready=continuation.ready if continuation is not None else False,
                pending_submission_count=int(submission_count),
                pending_join_count=int(join_count),
            ),
            pending_join_count=int(join_count),
            pending_submission_count=int(submission_count),
            continuation_count=continuation.wait_generation if continuation is not None else 0,
        )

    async def _waiting_since(self, run: RunRecord) -> datetime | None:
        """等待起点：WAITING_TOOL 取最近一次 `RUN_WAITING_TOOL` 事件，WAITING_INPUT 取等待中断。"""
        if run.status == RunStatus.WAITING_TOOL:
            return cast(
                datetime | None,
                await self._session.scalar(
                    sa.select(sa.func.max(CanonicalEvent.create_time)).where(
                        CanonicalEvent.tenant_id == run.tenant_id,
                        CanonicalEvent.run_id == run.id,
                        CanonicalEvent.event_type == "RUN_WAITING_TOOL",
                        CanonicalEvent.is_deleted.is_(False),
                    )
                ),
            )
        if run.status == RunStatus.WAITING_INPUT:
            return cast(
                datetime | None,
                await self._session.scalar(
                    sa.select(sa.func.max(RunInterrupt.create_time)).where(
                        RunInterrupt.tenant_id == run.tenant_id,
                        RunInterrupt.run_id == run.id,
                        RunInterrupt.status == "WAITING",
                        RunInterrupt.is_deleted.is_(False),
                    )
                ),
            )
        return None

    async def create_conversation(
        self,
        agent_id: uuid.UUID,
        platform_user_id: uuid.UUID,
        tenant_id: str,
        idempotency_key: str | None = None,
    ) -> Conversation:
        """新建会话；带 Idempotency-Key 时按设计 §3.4.2 持久重放，不创建第二会话。"""
        fingerprint = submission_fingerprint(
            endpoint=ENDPOINT_CREATE_CONVERSATION,
            # `tenant_id` 与 `endpoint` 是 RULE-api-002 要求的两个判别键
            key_payload={
                "tenant_id": tenant_id,
                "agent_id": str(agent_id),
                "platform_user_id": str(platform_user_id),
            },
        )
        if idempotency_key:
            replay = await self._submissions.find_replay_in(
                self._session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=ENDPOINT_CREATE_CONVERSATION,
                fingerprint=fingerprint,
            )
            if replay is not None and replay.conversation_id is not None:
                return await self._load_conversation(replay.conversation_id, tenant_id)
        await self._resolve_client.resolve(
            ResolveDefinitionRequest(
                agent_id=agent_id,
                actor_user_id=platform_user_id,
                # 建会话**不经渠道**（调用方只有 agent/用户，请求体里也没有通道）⇒ 显式省略，
                # **不编一个通道名**：编造的值一旦被消费方读走就是错的（B-09）。
            ),
            tenant_id=tenant_id,
            trace_id=current_trace_id(),
        )
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=platform_user_id,
            agent_id=agent_id,
            status=CONVERSATION_ACTIVE,
            last_seq=0,
        )
        self._session.add(conversation)
        await self._session.flush()
        if not idempotency_key:
            await self._session.commit()
            return conversation
        await self._submissions.record_in(
            self._session,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=ENDPOINT_CREATE_CONVERSATION,
            actor_user_id=platform_user_id,
            run_id=None,
            conversation_id=conversation.id,
            request_fingerprint=fingerprint,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # 并发同 key 幂等记录落败：读取首次提交结果（与 create-run 同口径）
            await self._session.rollback()
            replay = await self._submissions.find_replay_in(
                self._session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=ENDPOINT_CREATE_CONVERSATION,
                fingerprint=fingerprint,
            )
            if replay is not None and replay.conversation_id is not None:
                return await self._load_conversation(replay.conversation_id, tenant_id)
            raise AppError(ErrorCode.COMMON_CONFLICT) from exc
        return conversation

    async def _load_conversation(self, conversation_id: uuid.UUID, tenant_id: str) -> Conversation:
        conversation = await self._session.scalar(
            sa.select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.is_deleted.is_(False),
            )
        )
        if conversation is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return conversation

    async def reap_abandoned(self) -> int:
        return await reap_abandoned_runs(get_session_factory())

    def _assert_replay_owner(
        self,
        submission: RunSubmission,
        actor_user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
    ) -> None:
        if submission.actor_user_id != actor_user_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if conversation_id is not None and submission.conversation_id != conversation_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def _load_run(self, run_id: uuid.UUID, tenant_id: str) -> RunRecord:
        run = await self._session.scalar(
            sa.select(RunRecord).where(
                RunRecord.id == run_id,
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
            )
        )
        if run is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return run

    async def _load_snapshot(self, run: RunRecord) -> RuntimeSnapshot:
        if run.snapshot_id is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        snapshot = await self._session.scalar(
            sa.select(RuntimeSnapshot).where(RuntimeSnapshot.id == run.snapshot_id)
        )
        if snapshot is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return snapshot

    async def _resolve_conversation(self, request: RunRequest, tenant_id: str) -> Conversation:
        if request.conversation_id is not None:
            existing = await self._session.scalar(
                sa.select(Conversation).where(
                    Conversation.id == request.conversation_id,
                    Conversation.tenant_id == tenant_id,
                    Conversation.user_id == request.platform_user_id,
                    Conversation.agent_id == request.agent_id,
                    Conversation.is_deleted.is_(False),
                )
            )
            if existing is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            return existing
        latest = await self._session.scalar(
            sa.select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == request.platform_user_id,
                Conversation.agent_id == request.agent_id,
                Conversation.is_deleted.is_(False),
            )
            .order_by(_conversation_recency_key().desc(), Conversation.create_time.desc())
            .limit(1)
        )
        if latest is not None:
            return latest
        created = Conversation(
            tenant_id=tenant_id,
            user_id=request.platform_user_id,
            agent_id=request.agent_id,
            status=CONVERSATION_ACTIVE,
            last_seq=0,
        )
        self._session.add(created)
        await self._session.flush()
        return created

    async def _active_run(self, conversation_id: uuid.UUID) -> RunRecord | None:
        run: RunRecord | None = await self._session.scalar(
            sa.select(RunRecord)
            .where(
                RunRecord.conversation_id == conversation_id,
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
                RunRecord.is_deleted.is_(False),
            )
            .limit(1)
        )
        return run

    async def _create_run(
        self,
        request: RunRequest,
        tenant_id: str,
        conversation: Conversation,
        resolved: ResolveDefinitionResponse,
        submission_key: str,
        submission_fingerprint_value: str,
    ) -> RunStart:
        now = _utcnow()
        run_id = uuid.uuid4()
        # 平台设置在此边界取**一次**：这份快照既冻结本 Run 的执行策略，也给出本次受理的
        # capacity 阈值（准入投影与冻结策略同源，不二次取配置）。源不可读即明确失败
        # （RULE-06）——绝不回退到任何过期默认值，失败计数由 client 在调用方侧记。
        snapshot_settings = await self._settings_client.fetch_snapshot(
            tenant_id=tenant_id, trace_id=current_trace_id() or ""
        )
        capacity = parse_capacity(snapshot_settings)
        # 压缩配置在此解析一次：既冻进这一行的 snapshot，也供本次历史装配用同一个值——
        # 二者若各解析一次，配置恰好在两次之间变更就会让"冻结值"与"实际用的值"分叉。
        compaction = resolve_compaction_settings(
            resolved.agent.runtime_config,
            platform_overrides=snapshot_settings.settings.get("compaction"),
        )
        # 执行期平台默认（`agent`/`memory`/`artifact.max_archive_files`/`locale.default_timezone`）
        # 同样在此冻结一次（ADR-07 等）：既是本次装配的基值，也冻进 policy_json 供 resume 读回。
        execution = execution_defaults_from_platform(snapshot_settings.settings)
        # 模型池路由（`model_pool` 五叶 + 规范 policy_hash）与模型等待预算（`capacity`）走**同一份**
        # 设置快照冻结：池策略变更只影响后续新 Run，旧 Run 的调度分布按自己那份 policy_hash 解释。
        pool = resolve_pool_execution_policy(snapshot_settings.settings)
        # 成员的非密钥引用与权重（B-224）：Key/指纹/计量身份实时解析，不落快照。这是"关系随 Run
        # 冻结"的输入——绑定/解绑/权重变更只影响后续新 Run，旧 Run 不能带出另一份物理额度。
        members = await ModelDispatchStore(self._session).read_member_refs(
            tenant_id=tenant_id, model_id=resolved.model.id
        )
        # 摘要模型（`compaction.summary.model_ref`）同样在这个边界解析一次：它是一条普通的模型
        # 定义，endpoint / 凭据都与主模型不同，必须随快照冻结（ADR-06）。
        summary_model = await self._resolve_summary_model(
            tenant_id, compaction, run_id=run_id, actor_user_id=request.platform_user_id
        )
        model, summary_model, mcp_secrets = await self._runtime_credentials(
            run_id,
            tenant_id,
            request.platform_user_id,
            resolved.model,
            resolved.mcp_servers,
            summary_model=summary_model,
        )
        # 准入段（锁内不联网）：策略行锁是首个共享行锁，随后 Run → USER → TENANT（§3.2 锁序）。
        # 更低设置版本在此抛 `AdmissionPolicyStaleError`，由 `start` 退出事务后重取一次。
        policy = await self._admission.lock_and_apply_in(
            self._session, tenant_id=tenant_id, capacity=capacity
        )
        run = RunRecord(
            id=run_id,
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            user_id=request.platform_user_id,
            agent_id=request.agent_id,
            status=RunStatus.CREATED,
            input_text=request.message.text,
            trace_id=current_trace_id() or uuid.uuid4().hex,
            start_time=now,
            deadline_at=now
            + timedelta(
                milliseconds=AgentPolicy.from_runtime_config(
                    resolved.agent.runtime_config, base=execution.agent_policy
                ).deadline_ms
            ),
            cancel_requested=False,
            execution_epoch=0,
            channel_json=request.channel.model_dump(mode="json"),
        )
        self._session.add(run)
        try:
            await self._session.flush()
            snapshot = self._build_snapshot(
                run.id, tenant_id, resolved, compaction, execution, summary_model, pool, members
            )
            self._session.add(snapshot)
            await self._session.flush()
            run.snapshot_id = snapshot.id
            submission = await self._submissions.record_in(
                self._session,
                tenant_id=tenant_id,
                idempotency_key=submission_key,
                endpoint=ENDPOINT_CREATE_RUN,
                actor_user_id=request.platform_user_id,
                run_id=run.id,
                conversation_id=conversation.id,
                request_fingerprint=submission_fingerprint_value,
            )
            # 入站附件：落 artifact 行（与 Run 同事务，设计 AD-1-B），并把紧凑摘要写进事件载荷
            # 供历史组装直接渲染文本引用——历史是逐条回放的热路径，不为一句引用回查产物表。
            persisted = await persist_inbound_attachments(
                self._session,
                tenant_id=tenant_id,
                run_id=run.id,
                conversation_id=conversation.id,
                refs=request.message.attachments,
            )
            seq = await MemoryResumeService().append_inbound(
                self._session, run, text=request.message.text, settings=execution.memory_policy,
                payload={
                    "message_id": request.message.id,
                    "attachments": attachment_payload(persisted),
                },
                submission_id=submission.id,
            )
            submission.first_seq = seq
            await self._append_submission_start(self._session, run, submission.id, resumed=False)
            conversation.last_run_id = run.id
            conversation.update_time = now
            # 计数自增与 Run 行同事务（B-230）：达阈值即拒，整个事务回滚——不留半建 Run，
            # 也不留下只增不减的占位。
            await self._admission.reserve_counts_in(
                self._session, tenant_id=tenant_id, user_id=request.platform_user_id, policy=policy
            )
            await self._session.commit()
        except IntegrityError as exc:
            # 并发同 key 幂等记录落败：读取首次提交结果（设计 API-01）
            await self._session.rollback()
            replay = await self._submissions.find_replay_in(
                self._session,
                tenant_id=tenant_id,
                idempotency_key=submission_key,
                endpoint=ENDPOINT_CREATE_RUN,
                fingerprint=submission_fingerprint_value,
            )
            if replay is not None:
                return self._replay_start(replay)
            raise AppError(ErrorCode.RUN_BUSY) from exc
        except AppError:
            # 准入拒绝等显式失败：整事务回滚，绝不留下半建 Run/会话/提交/计数。
            await self._session.rollback()
            raise
        history = await self._load_history(
            conversation.id,
            tenant_id,
            request.platform_user_id,
            budget_messages=compaction.history_budget_messages,
            max_history_tool_rounds=compaction.max_history_tool_rounds,
        )
        stream = self._stream_run(
            run=run,
            agent=resolved.agent,
            model=model,
            skills=tuple(resolved.skills),
            mcp_servers=tuple(resolved.mcp_servers),
            mcp_secrets=mcp_secrets,
            history=history,
            submission_id=submission.id,
            resumed=False,
            compaction=compaction,
            execution=execution,
            summary_model=summary_model,
            pool=pool,
            members=members,
        )
        await self._launch_execution(run, stream)
        return RunStart(
            run_id=run.id,
            conversation_id=conversation.id,
            resumed=False,
            events=self._replay_events(submission),
        )

    async def _resume_run(
        self, run: RunRecord, input_text: str, *, attachments: Sequence[AttachmentRef] = (),
        submission_key: str, request_fingerprint: str, endpoint: str, message_id: str | None,
    ) -> RunStart:
        ensure_human_resume(run, await _database_now(self._session))
        snapshot = await self._load_snapshot(run)
        agent = ResolvedAgent.model_validate(snapshot.agent_json)
        model = ResolvedModel.model_validate(snapshot.model_json)
        skills = tuple(ResolvedSkill.model_validate(item) for item in snapshot.skill_catalog_json)
        servers = tuple(ResolvedMcpServer.model_validate(item) for item in snapshot.mcp_catalog_json)
        model, summary_model, secrets = await self._runtime_credentials(
            run.id, run.tenant_id, run.user_id, model, servers,
            summary_model=summary_model_of(snapshot.policy_json))
        tenant_id, run_id = run.tenant_id, run.id
        canaries = _credential_canaries(model, summary_model, secrets)
        try:
            submission, persisted, replayed = await self._persist_human_resume(run, input_text,
                message_id=message_id, attachments=attachments, submission_key=submission_key,
                request_fingerprint=request_fingerprint, endpoint=endpoint,
                settings=execution_defaults_of(snapshot.policy_json).memory_policy,
                credential_canaries=canaries)
            await self._session.commit()
            if replayed:
                return self._replay_start(submission)
        except IntegrityError as exc:
            await self._session.rollback()
            replay = await self._submissions.find_replay_in(self._session, tenant_id=tenant_id,
                idempotency_key=submission_key, endpoint=endpoint, fingerprint=request_fingerprint)
            if replay is not None:
                return self._replay_start(replay)
            raise AppError(ErrorCode.RUN_BUSY) from exc
        except AppError:
            await self._session.rollback()
            raise
        except SQLAlchemyError as exc:
            await self._session.rollback()
            logger.warning("human_resume_transaction_failed", extra={"tenant_id": tenant_id,
                "run_id": str(run_id), "error_type": type(exc).__name__})
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        history = await self._load_history(run.conversation_id, run.tenant_id, run.user_id,
            budget_messages=history_budget_of(snapshot.policy_json),
            max_history_tool_rounds=history_tool_rounds_of(snapshot.policy_json))
        stream = self._stream_run(run=run, agent=agent, model=model, skills=skills,
            mcp_servers=servers, mcp_secrets=secrets, history=history, submission_id=submission.id,
            resumed=True, compaction=compaction_settings_of(snapshot.policy_json),
            execution=execution_defaults_of(snapshot.policy_json), summary_model=summary_model,
            current_text=input_text, current_attachments=persisted,
            pool=pool_execution_policy_of(snapshot.policy_json),
            members=frozen_model_members_of(snapshot.policy_json))
        await self._launch_execution(run, stream)
        return RunStart(run_id=run.id, conversation_id=run.conversation_id, resumed=True,
            events=self._replay_events(submission))

    async def _persist_human_resume(
        self, run: RunRecord, input_text: str, *, attachments: Sequence[AttachmentRef],
        submission_key: str, request_fingerprint: str, endpoint: str, message_id: str | None,
        settings: MemoryPolicySettings, credential_canaries: tuple[str, ...],
    ) -> tuple[RunSubmission, tuple[PersistedAttachment, ...], bool]:
        await self._submissions.lock_identity(self._session, tenant_id=run.tenant_id,
            idempotency_key=submission_key, endpoint=endpoint)
        replay = await self._submissions.find_replay_in(self._session, tenant_id=run.tenant_id,
            idempotency_key=submission_key, endpoint=endpoint, fingerprint=request_fingerprint)
        if replay is not None:
            self._assert_replay_owner(replay, run.user_id, run.conversation_id)
            return replay, (), True
        interrupts = await lock_human_resume(self._session, run)
        pending = prepare_confirmation(interrupts, input_text)
        submission = await self._submissions.record_in(self._session,
            tenant_id=run.tenant_id, idempotency_key=submission_key, endpoint=endpoint,
            actor_user_id=run.user_id, run_id=run.id, conversation_id=run.conversation_id,
            request_fingerprint=request_fingerprint)
        persisted = await persist_inbound_attachments(self._session,
            tenant_id=run.tenant_id, run_id=run.id, conversation_id=run.conversation_id, refs=attachments)
        seq = await MemoryResumeService().append_inbound(self._session, run, text=input_text,
            settings=settings, payload={"resumed": True, "message_id": message_id,
                "attachments": attachment_payload(persisted)}, submission_id=submission.id)
        submission.first_seq = seq
        await self._append_submission_start(self._session, run, submission.id, resumed=True)
        if pending is not None:
            await consume_confirmation(self._session, run, pending, submission_id=submission.id,
                settings=settings, credential_canaries=credential_canaries)
        else:
            await resolve_generic_interrupts(self._session, interrupts, input_text)
        await transition_human_resume(self._session, run)
        return submission, tuple(persisted), False

    async def _launch_execution(self, run: RunRecord, stream: AsyncIterator[ExecutorEvent]) -> None:
        from muad_api.context import TRACE_CORRELATION_FIELDS, context_scope

        self._session.expunge(run)
        async def consume() -> None:
            fields = {name: "" for name in TRACE_CORRELATION_FIELDS}
            fields.update(
                trace_id=run.trace_id,
                run_id=str(run.id),
                conversation_id=str(run.conversation_id),
                platform_user_id=str(run.user_id),
                agent_id=str(run.agent_id),
                snapshot_id=str(run.snapshot_id or ""),
            )
            with context_scope(tenant_id=run.tenant_id, caller_service="agent-runtime", **fields):
                async for _ in stream:
                    pass

        async def claim(
            excluded: frozenset[uuid.UUID],
        ) -> tuple[uuid.UUID, Callable[[], Awaitable[object]]] | None:
            claimed = await claim_continuation(get_session_factory(), instance_id=self._instance_id,
                lease_sec=self._settings.run_lease_sec, run_id=run.id, exclude_runs=excluded)
            if claimed is None:
                return None
            run.status, run.execution_epoch = claimed.status, claimed.execution_epoch
            run.lease_owner, run.lease_until = claimed.lease_owner, claimed.lease_until
            return run.id, consume

        await self._supervisor.claim_and_submit(claim)

    async def _append_submission_start(self, session: AsyncSession, run: RunRecord,
                                       submission_id: uuid.UUID, *, resumed: bool) -> int:
        return await EventWriter(session).append(tenant_id=run.tenant_id,
            conversation_id=run.conversation_id, run_id=run.id, submission_id=submission_id,
            event_type="RUN_CREATED", stream_type=RUN_CREATED_EVENT,
            source_event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"muad:run-start:{submission_id}"),
            payload={
                "conversation_id": str(run.conversation_id),
                "resumed": resumed,
                "trace_id": run.trace_id,
            })

    async def _runtime_credentials(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        model: ResolvedModel,
        mcp_servers: Sequence[ResolvedMcpServer],
        *,
        summary_model: ResolvedModel | None = None,
    ) -> tuple[ResolvedModel, ResolvedModel | None, dict[str, str]]:
        """API-09 读取当前认证值；未配置客户端时使用定义内密钥（测试/本地）。

        摘要模型（`summary_model`）走**同一个端点再取一次**：它是另一条模型定义，凭据与主模型
        无关。凭据只进内存（`ExecutorRequest`），不写快照。
        """
        if self._credentials_client is None:
            return model, summary_model, {}
        data = await self._credentials_client.resolve_credentials(
            tenant_id=tenant_id,
            payload={
                "execution_ref": {"type": "RUN", "id": str(run_id)},
                "actor_user_id": str(actor_user_id),
                "model_id": str(model.id),
                "mcp_server_ids": [str(server.mcp_server_id) for server in mcp_servers],
            },
            trace_id=current_trace_id(),
        )
        model_data = data.get("model") or {}
        api_key = model_data.get("api_key")
        if not api_key:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        secrets = {
            str(item.get("mcp_server_id")): str(item["auth_secret"])
            for item in data.get("mcp_servers") or []
            if item.get("auth_secret")
        }
        if summary_model is not None:
            summary_model = await self._summary_model_with_credentials(
                run_id, tenant_id, actor_user_id, summary_model
            )
        return model.model_copy(update={"api_key": str(api_key)}), summary_model, secrets

    async def _summary_model_with_credentials(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        summary_model: ResolvedModel,
    ) -> ResolvedModel | None:
        """给摘要模型取一次凭据；取不到就**关掉这一层**（RULE-04），不让 Run 失败。"""
        assert self._credentials_client is not None
        data = await self._credentials_client.resolve_credentials(
            tenant_id=tenant_id,
            payload={
                "execution_ref": {"type": "RUN", "id": str(run_id)},
                "actor_user_id": str(actor_user_id),
                "model_id": str(summary_model.id),
                "mcp_server_ids": [],
            },
            trace_id=current_trace_id(),
        )
        api_key = (data.get("model") or {}).get("api_key")
        if not api_key:
            logger.warning(
                "summary_model_credential_missing",
                extra={"run_id": str(run_id), "model_id": str(summary_model.id)},
            )
            record_outcome(CONTEXT_SUMMARY_METRIC, "SKIPPED")
            return None
        return summary_model.model_copy(update={"api_key": str(api_key)})

    async def _resolve_summary_model(
        self,
        tenant_id: str,
        compaction: CompactionSettings,
        *,
        run_id: uuid.UUID,
        actor_user_id: uuid.UUID,
    ) -> ResolvedModel | None:
        """解析 `compaction.summary.model_ref` 指向的模型（ADR-06：既有 `model_definition` 主键）。

        **解析失败不让 Run 失败**（RULE-04：压缩是尽力而为）：记 warning + 指标后返回 None，
        该 Run 的摘要层就不跑。**绝不退回主模型**——那正是修复前的行为（配置写了却不生效，
        2026-10-06 review）。解析出的非密钥字段随 `policy_json` 冻结，resume 读回同一份。
        """
        settings = compaction.summary
        if not settings.enabled or not settings.model_ref:
            return None
        try:
            request = ResolveModelRequest(model_id=uuid.UUID(settings.model_ref), actor_user_id=actor_user_id)
            resolved = await self._resolve_client.resolve_model(
                request, tenant_id=tenant_id, trace_id=current_trace_id() or ""
            )
        except (AppError, ValueError) as exc:
            logger.warning(
                "summary_model_unresolved",
                extra={"run_id": str(run_id), "error": type(exc).__name__},
            )
            record_outcome(CONTEXT_SUMMARY_METRIC, "SKIPPED")
            return None
        return resolved.model

    async def _cancel(self, run: RunRecord) -> RunRecord:
        from .async_tools.operations import cancel_operations
        from .tool_result_receipts import lock_run_source

        run, _ = await lock_run_source(self._session, run.tenant_id, run)
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        await cancel_operations(self._session, run)
        now = _utcnow()
        waiting = run.status in (RunStatus.WAITING_INPUT, RunStatus.WAITING_TOOL)
        if run.status in (RunStatus.WAITING_INPUT, RunStatus.WAITING_TOOL):
            await self._cancel_waiting(run, now)
        else:
            await self._session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run.id,
                    RunRecord.tenant_id == run.tenant_id,
                    RunRecord.is_deleted.is_(False),
                    RunRecord.status.in_((RunStatus.CREATED, RunStatus.RUNNING)),
                )
                .values(cancel_requested=True, update_time=now)
            )
        await self._session.commit()
        if not waiting:
            await self._safe_set_cancel_hint(run.id)
        await self._session.refresh(run)
        return run

    async def _cancel_waiting(self, run: RunRecord, now: datetime) -> None:
        changed = await self._session.scalar(
            sa.update(RunRecord)
            .where(
                RunRecord.id == run.id,
                RunRecord.tenant_id == run.tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status == run.status,
            )
            .values(
                status=RunStatus.CANCELLED,
                cancel_requested=True,
                end_time=now,
                lease_owner=None,
                lease_until=None,
                update_time=now,
            )
            .returning(RunRecord.id)
        )
        if changed is None:
            raise AppError(ErrorCode.RUN_BUSY)
        await self._session.execute(
            sa.update(RunInterrupt)
            .where(
                RunInterrupt.run_id == run.id,
                RunInterrupt.tenant_id == run.tenant_id,
                RunInterrupt.status == INTERRUPT_WAITING,
                RunInterrupt.is_deleted.is_(False),
            )
            .values(
                status=INTERRUPT_CANCELLED,
                resolution_json={"reason": "cancelled"},
                resolved_at=now,
                update_time=now,
            )
        )
        writer = self._event_writer()
        await writer.append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type=CANCEL_EVENT,
            payload={"reason": "cancelled"},
        )
        await writer.append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_COMPLETED",
            stream_type=RUN_COMPLETED_EVENT,
            payload={"status": str(RunStatus.CANCELLED), "final_text": "", "reason": "cancelled"},
        )

    async def _safe_set_cancel_hint(self, run_id: uuid.UUID) -> None:
        try:
            await self._cancel_hints.set(run_id)
        except Exception:
            # Redis 仅加速通道，失败不阻塞取消（DB cancel_requested 为权威事实）
            logger.warning("cancel_hint_set_failed", extra={"run_id": str(run_id)})

    async def _load_history(
        self,
        conversation_id: uuid.UUID,
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        budget_messages: int | None = None,
        max_history_tool_rounds: int = default_compaction_settings().max_history_tool_rounds,
    ) -> tuple[ModelMessage, ...]:
        """Read canonical history; the executor owns request-time memory refresh."""
        return await self._context_builder.load_history(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            budget_messages=budget_messages,
            max_history_tool_rounds=max_history_tool_rounds,
        )

    def _build_snapshot(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        resolved: ResolveDefinitionResponse,
        compaction: CompactionSettings | None = None,
        execution: ExecutionDefaults | None = None,
        summary_model: ResolvedModel | None = None,
        pool: PoolExecutionPolicy | None = None,
        members: Sequence[MemberRef] | None = None,
    ) -> RuntimeSnapshot:
        return build_snapshot(
            run_id=run_id,
            tenant_id=tenant_id,
            resolved=resolved,
            compaction=compaction,
            execution=execution,
            summary_model=summary_model,
            pool=pool,
            members=members,
        )

    def _event_writer(self) -> EventWriter:
        return EventWriter(self._session)

    async def _is_cancel_requested(self, run: RunRecord) -> bool:
        if await self._cancel_hints.is_set(run.id):
            return True
        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(RunRecord).where(
                    RunRecord.id == run.id,
                    RunRecord.tenant_id == run.tenant_id,
                    RunRecord.is_deleted.is_(False),
                )
            )
        if row is None or row.cancel_requested:
            return True
        try:
            ensure_execution(row, ExecutionIdentity.of(run), _utcnow())
        except ExecutionLeaseLost:
            return True
        return False

    async def _load_inbound_attachments(self, run: RunRecord) -> tuple[PersistedAttachment, ...]:
        async with get_session_factory()() as session:
            rows = tuple(
                await session.scalars(
                    sa.select(Artifact).where(
                        Artifact.run_id == run.id,
                        Artifact.tenant_id == run.tenant_id,
                        Artifact.is_deleted.is_(False),
                        Artifact.artifact_type.in_((INBOUND_IMAGE, INBOUND_DOCUMENT, INBOUND_OTHER)),
                    )
                )
            )
        return tuple(_persisted_from_row(row) for row in rows)

    def _read_artifact_bytes(self, storage_key: str) -> bytes:
        return NfsArtifactStore(self._settings.artifact_root).resolve(storage_key).read_bytes()

    async def _renew_lease_loop(self, run: RunRecord) -> None:
        """续租心跳：**失去租约才退出**，瞬时故障不退出。

        以前 `_renew_lease` 一抛错就让这个后台任务带着异常结束（异常只在 `_stream_run` 的
        finally 里 `await` 时才炸出来），租约随之到期、Reaper 把**仍在执行**的 Run 置 FAILED
        —— 那正是"回收后旧执行器还在跑"的可达路径（2026-10-06 review）。瞬时故障下一轮照续；
        返回 False 才是真的不再由本实例负责（状态已变 / owner 被接管），此时退出，交给
        `_is_cancel_requested` 的状态判据让执行器停下。
        """
        while True:
            await asyncio.sleep(self._settings.run_heartbeat_sec)
            try:
                renewed = await self._renew_lease(run)
            except Exception as exc:  # noqa: BLE001 —— 心跳是后台任务，异常不得带走整条流
                logger.warning(
                    "run_lease_renew_failed",
                    extra={"run_id": str(run.id), "error": type(exc).__name__},
                )
                continue
            if not renewed:
                return

    async def _renew_lease(self, run: RunRecord) -> bool:
        return await renew_execution(
            get_session_factory(), ExecutionIdentity.of(run), lease_sec=self._settings.run_lease_sec
        )

    async def _build_executor(
        self,
        run: RunRecord,
        agent: ResolvedAgent,
        model: ResolvedModel,
        skills: Sequence[ResolvedSkill],
        mcp_servers: Sequence[ResolvedMcpServer],
        mcp_secrets: dict[str, str],
        history: Sequence[ModelMessage],
        *,
        compaction: CompactionSettings | None = None,
        execution: ExecutionDefaults | None = None,
        summary_model: ResolvedModel | None = None,
        current_text: str | None = None,
        current_attachments: tuple[PersistedAttachment, ...] | None = None,
        submission_id: uuid.UUID | None = None,
        memory_resume_ref: MemoryResumeRef | None = None,
        pool: PoolExecutionPolicy | None = None,
        members: Sequence[MemberRef] | None = None,
    ) -> RunExecutor:
        text = run.input_text if current_text is None else current_text
        # `None` ⇒ 取本 Run 全部的入站附件（新建 Run 的常规路径）；续跑显式传**本次新落的**那批，
        # 不重放上一轮的入站附件——那一轮的字节已经进过上下文，重放会把旧图再内联一次。
        attachments = (
            await self._load_inbound_attachments(run) if current_attachments is None else current_attachments
        )
        from .execution_checkpoint import execution_checkpoint

        progress, port, async_policy = await execution_checkpoint(get_session_factory(), run)
        history = await self._restored_history(run, history, progress, compaction)
        return await self._executor_factory(
            ExecutorRequest(
                agent=agent,
                model=model,
                input_text=text,
                # 无附件时 build_current_content 原样返回文本，故无需额外判空分支
                input_content=build_current_content(
                    text,
                    attachments=attachments,
                    read_bytes=self._read_artifact_bytes,
                ),
                is_cancel_requested=lambda: self._is_cancel_requested(run),
                skills=tuple(skills),
                mcp_servers=tuple(mcp_servers),
                history=tuple(history),
                compaction=compaction,
                execution=execution,
                summary_model=summary_model,
                checkpoint=progress,
                runtime_events=port,
                deadline_at=run.deadline_at,
                event_batch_size=async_policy.event_batch_size,
                parallel_limit=async_policy.parallel_limit,
                execution_identity=ExecutionIdentity.of(run),
                credentials=ExecutorCredentials(mcp_secrets=mcp_secrets),
                # 模型池执行链路（TASK-025）：冻结策略与**非密钥**成员引用只从本 Run 自己的
                # 快照读回（旧行缺键 ⇒ None，不补新默认）；受信解析客户端只进调用内存。
                pool=pool,
                model_members=None if members is None else tuple(members),
                credentials_client=self._credentials_client,
                run_context=ExecutorRunContext(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    user_id=run.user_id,
                    delivery_route=delivery_route_of(run.channel_json),
                    submission_id=submission_id,
                    memory_resume_ref=memory_resume_ref,
                ),
            )
        )

    async def _restored_history(
        self,
        run: RunRecord,
        history: Sequence[ModelMessage],
        progress: RunnerCheckpoint,
        compaction: CompactionSettings | None,
    ) -> tuple[ModelMessage, ...]:
        if run.execution_epoch <= 1 and history:
            return tuple(history)
        return await self._context_builder.load_history(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            budget_messages=compaction.history_budget_messages if compaction else None,
            max_history_tool_rounds=(compaction or default_compaction_settings()).max_history_tool_rounds,
            unconsumed_run_id=run.id,
            consumed_event_seq=progress.consumed_event_seq,
        )

    async def _stream_run(
        self,
        *,
        run: RunRecord,
        agent: ResolvedAgent,
        model: ResolvedModel,
        skills: Sequence[ResolvedSkill],
        mcp_servers: Sequence[ResolvedMcpServer],
        mcp_secrets: dict[str, str],
        history: Sequence[ModelMessage],
        submission_id: uuid.UUID,
        resumed: bool,
        compaction: CompactionSettings | None = None,
        execution: ExecutionDefaults | None = None,
        summary_model: ResolvedModel | None = None,
        current_text: str | None = None,
        current_attachments: tuple[PersistedAttachment, ...] | None = None,
        pool: PoolExecutionPolicy | None = None,
        members: Sequence[MemberRef] | None = None,
    ) -> AsyncIterator[ExecutorEvent]:
        yield await self._start_segment_event(run, submission_id, resumed)
        resume = await resume_decision(get_session_factory(), run, submission_id=submission_id)

        async def build() -> RunExecutor:
            return await self._build_executor(run, agent, model, skills, mcp_servers, mcp_secrets, history,
                compaction=compaction, execution=execution, summary_model=summary_model,
                current_text=current_text, current_attachments=current_attachments,
                submission_id=submission_id, memory_resume_ref=resume.reference,
                pool=pool, members=members)

        result = (SegmentResult(final_text=resume.message) if resume.message is not None
            else await self._run_segment(run, submission_id, agent.key, build))
        if result is not None:
            terminal = await self._finish_segment(run, submission_id, agent.key, result)
            if terminal is not None:
                yield terminal

    async def _start_segment_event(
        self, run: RunRecord, submission_id: uuid.UUID, resumed: bool
    ) -> ExecutorEvent:
        async with get_session_factory()() as session:
            existing = await session.scalar(sa.select(CanonicalEvent).where(
                CanonicalEvent.tenant_id == run.tenant_id, CanonicalEvent.run_id == run.id,
                CanonicalEvent.submission_id == submission_id, CanonicalEvent.event_type == "RUN_CREATED",
                CanonicalEvent.is_deleted.is_(False)).order_by(CanonicalEvent.seq).limit(1))
            if existing is not None:
                return ExecutorEvent(type=RUN_CREATED_EVENT, data=dict(existing.payload_json),
                    seq=int(existing.seq), timestamp=existing.create_time.isoformat())
        return await self._persist_event(run, submission_id, RUN_CREATED_EVENT,
            {"conversation_id": str(run.conversation_id), "resumed": resumed, "trace_id": run.trace_id})

    async def _run_segment(self, run: RunRecord, submission_id: uuid.UUID, agent_key: str,
                           build: Callable[[], Awaitable[RunExecutor]]) -> SegmentResult | None:
        heartbeat = asyncio.create_task(self._renew_lease_loop(run))
        try:
            return await self._consume_executor(run, submission_id, await build())
        except ExecutionLeaseLost:
            return None
        except asyncio.CancelledError:
            await self._finalize_failed(
                run, submission_id, RunnerShutdown("runtime shutdown"), agent_key=agent_key
            )
            raise
        except Exception as exc:
            return SegmentResult(failure=exc)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _consume_executor(
        self, run: RunRecord, submission_id: uuid.UUID, executor: RunExecutor
    ) -> SegmentResult:
        text, outcome = "", AgentRunStatus.COMPLETED
        async for event in executor.run():
            if event.type == "runner.outcome":
                outcome = AgentRunStatus(event.data["status"])
                text = str(event.data.get("final_text", text))
                continue
            if event.type == "message.delta":
                text += str(event.data.get("delta", ""))
            if event.seq is None:
                await self._persist_event(run, submission_id, event.type, event.data)
        return SegmentResult(outcome, text)

    async def _finish_segment(self, run: RunRecord, submission_id: uuid.UUID, agent_key: str,
                              result: SegmentResult) -> ExecutorEvent | None:
        if result.failure is not None:
            return await self._finalize_failed(run, submission_id, result.failure, agent_key=agent_key)
        if result.outcome in (AgentRunStatus.WAITING_TOOL, AgentRunStatus.WAITING_INPUT):
            return None
        if result.outcome == AgentRunStatus.BUDGET_EXCEEDED:
            return await self._finalize_failed(
                run, submission_id, RunnerBudgetExceeded("frozen budget exhausted"), agent_key=agent_key
            )
        return await self._finalize_run(run, submission_id, result.final_text, agent_key=agent_key)

    async def _persist_event(
        self,
        run: RunRecord,
        submission_id: uuid.UUID,
        sse_type: str,
        data: dict[str, Any],
    ) -> ExecutorEvent:
        async with get_session_factory()() as session:
            await lock_execution(session, ExecutionIdentity.of(run), _utcnow())
            seq = await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type=STREAM_BUSINESS_TYPES.get(sse_type, sse_type),
                payload=data,
                submission_id=submission_id,
                stream_type=sse_type,
                artifact_id=_event_artifact_id(data),
            )
            await session.commit()
        return ExecutorEvent(type=sse_type, data=data, seq=int(seq), timestamp=_utcnow().isoformat())

    async def _finalize_run(
        self, run: RunRecord, submission_id: uuid.UUID, final_text: str, *, agent_key: str
    ) -> ExecutorEvent:
        async with get_session_factory()() as session:
            row = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run.id))
            if row is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            if row.status in TERMINAL_RUN_STATUSES:
                return await self._terminal_event(session, row)
            from .async_tools.operations import cancel_operations
            from .tool_result_receipts import lock_run_source

            row, _ = await lock_run_source(session, run.tenant_id, row)
            ensure_execution(row, ExecutionIdentity.of(run), _utcnow())
            cancelled = row.cancel_requested
            if cancelled:
                await cancel_operations(session, row)
            target = RunStatus.CANCELLED if cancelled else RunStatus.COMPLETED
            updated = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.RUNNING)
                .values(
                    status=target,
                    end_time=sa.func.now(),
                    update_time=sa.func.now(),
                    lease_owner=None,
                    lease_until=None,
                )
                .returning(RunRecord.id)
            )
            if updated.scalar_one_or_none() is None:
                await session.rollback()
                # rollback 之后 `row` 已过期：异步会话下碰它的任何属性都会抛 MissingGreenlet
                # （2026-10-06 review）。与 `_finalize_failed` 同形——重新取一行再交给
                # `_terminal_event`（它要读 id/conversation_id/status）。
                fresh = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run.id))
                if fresh is None:
                    raise AppError(ErrorCode.COMMON_NOT_FOUND)
                return await self._terminal_event(session, fresh)
            writer = EventWriter(session)
            # 业务事实行（ContextBuilder 读取）与对外 SSE 行分开，保证重放与历史互不混淆
            await writer.append(
                tenant_id=row.tenant_id,
                conversation_id=row.conversation_id,
                run_id=row.id,
                event_type=CANCEL_EVENT if cancelled else ASSISTANT_MESSAGE_EVENT,
                payload={"reason": "cancelled"} if cancelled else {"text": final_text},
                submission_id=submission_id,
            )
            payload: dict[str, Any] = {"status": str(target), "final_text": final_text}
            if cancelled:
                payload["reason"] = "cancelled"
            seq = await writer.append(
                tenant_id=row.tenant_id,
                conversation_id=row.conversation_id,
                run_id=row.id,
                event_type="RUN_COMPLETED",
                payload=payload,
                submission_id=submission_id,
                stream_type=RUN_COMPLETED_EVENT,
            )
            await self._submissions.finalize_in(
                session,
                submission_id,
                status=SUBMISSION_CLOSED,
                last_seq=int(seq),
                terminal_result_json={"status": str(target)},
            )
            if target == RunStatus.COMPLETED:
                from .memory_job_enqueue import enqueue_completed

                await enqueue_completed(session, row, through_seq=int(seq))
            await session.commit()
        record_outcome(AGENT_RUNS_METRIC, str(target), {"agent": agent_key})
        return ExecutorEvent(
            type=RUN_COMPLETED_EVENT,
            data=payload,
            seq=int(seq),
            timestamp=_utcnow().isoformat(),
        )

    async def _finalize_failed(
        self, run: RunRecord, submission_id: uuid.UUID, exc: Exception, *, agent_key: str
    ) -> ExecutorEvent:
        code = _error_code_for(exc)
        async with get_session_factory()() as session:
            from .async_tools.operations import cancel_operations
            from .tool_result_receipts import lock_run_source

            current, _ = await lock_run_source(session, run.tenant_id, run)
            if current.status in TERMINAL_RUN_STATUSES:
                return await self._terminal_event(session, current)
            ensure_execution(current, ExecutionIdentity.of(run), _utcnow())
            await cancel_operations(session, current)
            updated = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.RUNNING)
                .values(
                    status=RunStatus.FAILED,
                    error_code=code,
                    error_message=code,
                    lease_owner=None,
                    lease_until=None,
                    end_time=sa.func.now(),
                    update_time=sa.func.now(),
                )
                .returning(RunRecord.id)
            )
            if updated.scalar_one_or_none() is None:
                await session.rollback()
                row = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run.id))
                if row is None:
                    raise AppError(ErrorCode.COMMON_NOT_FOUND)
                return await self._terminal_event(session, row)
            payload = {"status": str(RunStatus.FAILED), "error_code": code}
            seq = await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="RUN_FAILED",
                payload=payload,
                submission_id=submission_id,
                stream_type=RUN_FAILED_EVENT,
            )
            await self._submissions.finalize_in(
                session,
                submission_id,
                status=SUBMISSION_CLOSED,
                last_seq=int(seq),
                terminal_result_json={"status": str(RunStatus.FAILED), "error_code": code},
            )
            await session.commit()
        record_outcome(AGENT_RUNS_METRIC, str(RunStatus.FAILED), {"agent": agent_key})
        return ExecutorEvent(
            type=RUN_FAILED_EVENT,
            data=payload,
            seq=int(seq),
            timestamp=_utcnow().isoformat(),
        )

    async def _terminal_event(self, session: AsyncSession, run: RunRecord) -> ExecutorEvent:
        """已有终态（并发收尾/Reaper）：返回已持久化的终态事件或按 Run 记录合成。"""
        row = await session.scalar(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.stream_type.in_(TERMINAL_STREAM_TYPES),
            )
            .order_by(CanonicalEvent.seq.desc())
            .limit(1)
        )
        if row is not None:
            return ExecutorEvent(
                type=str(row.stream_type),
                data=dict(row.payload_json or {}),
                seq=int(row.seq),
                timestamp=row.create_time.isoformat() if row.create_time else None,
            )
        last_seq = await session.scalar(
            sa.select(Conversation.last_seq).where(Conversation.id == run.conversation_id)
        )
        if run.status == RunStatus.FAILED:
            return ExecutorEvent(
                type=RUN_FAILED_EVENT,
                data={"status": str(RunStatus.FAILED), "error_code": run.error_code},
                seq=int(last_seq or 0),
                timestamp=_utcnow().isoformat(),
            )
        return ExecutorEvent(
            type=RUN_COMPLETED_EVENT,
            data={"status": str(run.status), "final_text": ""},
            seq=int(last_seq or 0),
            timestamp=_utcnow().isoformat(),
        )

    def _replay_start(self, submission: RunSubmission) -> RunStart:
        if submission.run_id is None or submission.conversation_id is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return RunStart(
            run_id=submission.run_id,
            conversation_id=submission.conversation_id,
            resumed=submission.endpoint == ENDPOINT_RESUME_RUN,
            events=self._replay_events(submission),
        )

    def _terminal_start(self, run: RunRecord) -> RunStart:
        async def events() -> AsyncIterator[ExecutorEvent]:
            async with get_session_factory()() as session:
                row = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run.id))
                if row is None:
                    raise AppError(ErrorCode.COMMON_NOT_FOUND)
                yield await self._terminal_event(session, row)

        return RunStart(
            run_id=run.id,
            conversation_id=run.conversation_id,
            resumed=True,
            events=events(),
        )

    async def _replay_events(self, submission: RunSubmission) -> AsyncIterator[ExecutorEvent]:
        """重放已持久化事件并接续未结束流；不调用 LLM/Tool，不分配新序号。"""
        if submission.conversation_id is None or submission.run_id is None:
            return
        async for event in self._tail_events(
            tenant_id=submission.tenant_id,
            conversation_id=submission.conversation_id,
            run_id=submission.run_id,
            after_seq=(submission.first_seq or 1) - 1,
        ):
            yield event

    async def _tail_events(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        after_seq: int,
    ) -> AsyncIterator[ExecutorEvent]:
        """tail 持久 canonical 事件直到终态排空；等待态保持连接（heartbeat 由 HTTP 层加）。

        `after_seq` 是调用方**已确认**的 canonical seq：只在它之后回放，不重复不跳跃。
        """
        after = max(0, after_seq)
        while True:
            emitted_terminal = False
            async with get_session_factory()() as session:
                rows = await EventWriter(session).list_events(
                    conversation_id,
                    tenant_id=tenant_id,
                    after_seq=after,
                )
                for row in rows:
                    after = int(row.seq)
                    if row.run_id != run_id or row.stream_type is None:
                        continue
                    yield ExecutorEvent(
                        type=str(row.stream_type),
                        data=dict(row.payload_json or {}),
                        seq=int(row.seq),
                        timestamp=row.create_time.isoformat() if row.create_time else None,
                    )
                    if row.stream_type in TERMINAL_STREAM_TYPES:
                        emitted_terminal = True
                if emitted_terminal:
                    return
                fresh = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run_id))
                if fresh is None:
                    return
                if fresh.status in TERMINAL_RUN_STATUSES:
                    yield await self._terminal_event(session, fresh)
                    return
                if fresh.status == RunStatus.WAITING_INPUT:
                    return
            await asyncio.sleep(REPLAY_POLL_SEC)


def _error_code_for(exc: Exception) -> str:
    if isinstance(exc, RunnerBudgetExceeded):
        return "RUN_BUDGET_EXCEEDED"
    if isinstance(exc, RunnerDeadlineExceeded):
        return "RUN_DEADLINE_EXCEEDED"
    if isinstance(exc, RunnerShutdown):
        return "RUN_SHUTDOWN"
    if isinstance(exc, AppError):
        return str(exc.code)
    if isinstance(exc, RunnerModelError):
        return str(ErrorCode.MODEL_UNAVAILABLE)
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


class RunnerBudgetExceeded(RuntimeError):
    pass


class RunnerShutdown(RuntimeError):
    pass


def _persisted_from_row(row: Artifact) -> PersistedAttachment:
    """`artifact` 行 → 引用结构。kind 直接取落库时写进 metadata 的原值。"""
    metadata = row.metadata_json or {}
    filename = metadata.get("filename")
    return PersistedAttachment(
        artifact_id=row.id,
        kind=str(metadata.get("kind") or "OTHER"),
        media_type=row.media_type,
        filename=filename if isinstance(filename, str) else None,
        size=row.size,
        storage_key=row.storage_key,
    )
