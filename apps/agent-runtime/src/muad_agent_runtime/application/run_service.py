from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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

from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.db import get_session_factory
from ..infrastructure.db_clock import database_now
from ..infrastructure.model_dispatch_store import MemberRef, ModelDispatchStore
from ..infrastructure.models.async_tools import RunContinuation, ToolOperation
from ..infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    ChannelUpload,
    Conversation,
    RunInterrupt,
    RunRecord,
    RunSubmission,
    RuntimeSnapshot,
)
from ..metrics import (
    AGENT_RUNS_METRIC,
    CONTEXT_SUMMARY_METRIC,
    RUN_QUEUE_TIMEOUT_METRIC,
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
from .attachments.immutable_store import discard_written
from .attachments.inbound import (
    INBOUND_DOCUMENT,
    INBOUND_IMAGE,
    INBOUND_OTHER,
    PersistedAttachment,
    attachment_payload,
    build_current_content,
    persist_inbound_attachments,
    persist_staged_inbound_attachments,
)
from .channel_conversations import (
    ConversationScope,
    assert_entry_attribution,
    create_entry_conversation,
    load_explicit_conversation,
    record_user_message,
    register_pointer,
    resolve_pointer_conversation,
    resolve_source_conversation,
    scope_of,
    title_locale_of,
)
from .channel_notifications import (
    KIND_RUN_COMPLETED,
    run_terminal_delivery_key,
    write_run_notification,
)
from .channel_uploads import (
    resolve_message_uploads,
    upload_limits_from_settings,
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
from .dto import CancelSource, RunWaitSummary, run_view
from .event_tail_hub import CanonicalEventView, EventTailHub, TailBatch, get_event_tail_hub
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
from .memory_confirmation_resume import (
    ConfirmationBinding,
    consume_confirmation,
    prepare_confirmation,
)
from .memory_resume import MemoryResumeService, resume_decision
from .ports import (
    CredentialsClient,
    PlatformSettingsClient,
    PlatformSettingsSnapshot,
    ResolveClient,
)
from .run_admission import (
    QUEUE_STAGE_WAITING_RUNTIME,
    AdmissionPolicyService,
    AdmissionPolicyStaleError,
    parse_capacity,
    queue_deadline,
    release_admission_in,
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
from .webchat_history import WebchatHistoryService

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
    summary_members: Sequence[MemberRef] | None = None,
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
    本快照（`api_key` 剥离，认证走 API-09 实时读）。**它的成员引用**（`summary_model_members`）
    走与主模型同一口径冻结：摘要调用与主调用共享物理资源上限，冻结的是非密钥关系与权重，
    实际 Key 与在飞额度实时协调（`members=None` 与 `[]` 的区别同主模型）。

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
    if summary_members is not None:
        policy[SUMMARY_MODEL_MEMBERS_POLICY_KEY] = [
            frozen_member_payload(member) for member in summary_members
        ]
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
    return _frozen_members_section(policy, MODEL_MEMBERS_POLICY_KEY)


def frozen_summary_members_of(policy: Mapping[str, Any] | None) -> tuple[MemberRef, ...] | None:
    """从**已冻结**的 `policy_json` 还原**摘要模型**的成员非密钥引用；缺该键返回 None。

    与主模型同一读回口径（同一形状校验、同样不补新默认）：摘要调用与主调用共享物理资源上限，
    冻结的只是非密钥关系与权重，实际 Key 实时经受信接口解析。
    """
    return _frozen_members_section(policy, SUMMARY_MODEL_MEMBERS_POLICY_KEY)


def _frozen_members_section(
    policy: Mapping[str, Any] | None, key: str
) -> tuple[MemberRef, ...] | None:
    entries = (policy or {}).get(key)
    if entries is None:
        return None
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise PlatformSettingsError(f"frozen {key} must be a list")
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
#: 初始排队期限到期（与领取竞争同一状态 CAS）：超时后不得再调用模型。
RUN_QUEUE_TIMEOUT = "RUN_QUEUE_TIMEOUT"
ASSISTANT_MESSAGE_EVENT = "ASSISTANT_MESSAGE"
CANCEL_EVENT = "CANCEL"
RUN_CREATED_EVENT = "run.created"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"
INTERRUPT_WAITING = "WAITING"
INTERRUPT_RESOLVED = "RESOLVED"
INTERRUPT_CANCELLED = "CANCELLED"
TERMINAL_STREAM_TYPES = (RUN_COMPLETED_EVENT, RUN_FAILED_EVENT)


@dataclass(frozen=True, slots=True)
class _AdmissionMaterials:
    """受理边界（写事务外）取齐的 Console 材料：定义、设置快照、摘要模型与凭据。

    写事务内只允许纯计算与 DB 读写；网络材料一律在这里一次取齐。凭据只进内存、不落快照。
    """

    run_id: uuid.UUID
    resolved: ResolveDefinitionResponse
    settings: PlatformSettingsSnapshot
    compaction: CompactionSettings
    model: ResolvedModel
    summary_model: ResolvedModel | None
    mcp_secrets: dict[str, str]

STREAM_BUSINESS_TYPES: dict[str, str] = {
    RUN_CREATED_EVENT: "RUN_CREATED",
    # 正文流事件的**业务名**按 Console 词表归类（内容流不进运行轮廓，与 token 级增量同类）；
    # 行的精确身份仍是 `stream_type`（`message.delta`/`message.snapshot`），公开投影按它分派。
    "message.delta": "ASSISTANT_DELTA",
    "message.snapshot": "ASSISTANT_DELTA",
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
#: 摘要模型的成员段：与主模型同一冻结口径（摘要调用与主调用共享物理资源上限）
SUMMARY_MODEL_MEMBERS_POLICY_KEY = "summary_model_members"
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


@dataclass(frozen=True, slots=True)
class _TailStop:
    """追尾停止判定：`stop` 为真即结束流；`event` 非空则先产出（缺终态行时按 Run 记录合成）。"""

    stop: bool
    event: ExecutorEvent | None = None


def _executor_event(row: CanonicalEventView) -> ExecutorEvent:
    """已提交 canonical 行 → 执行器事件（只搬运事实；公开白名单投影属 SSE 接缝）。"""
    return ExecutorEvent(
        type=str(row.stream_type),
        data=dict(row.payload_json or {}),
        seq=int(row.seq),
        timestamp=row.create_time.isoformat() if row.create_time else None,
    )


def _utcnow() -> datetime:
    """进程墙钟：只用于事件时间戳等展示字段；业务期限一律取 PG 时钟（`database_now`）。"""
    return datetime.now(UTC)


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
    summary_members: Sequence[MemberRef] | None = None,
) -> RuntimeSnapshot:
    # 未显式传入时按「无平台设置」（`platform_overrides={}`）合并 Agent 覆盖——仅用于不经过
    # Run 创建边界的直构调用点；生产路径由 `_create_run` 在取到平台快照后显式传入。
    settings = (
        compaction
        if compaction is not None
        else resolve_compaction_settings(resolved.agent.runtime_config, platform_overrides={})
    )
    policy = snapshot_policy(settings, execution, summary_model, pool, members, summary_members)
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
        now = await database_now(session)
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
                queue_stage=None,
                update_time=now,
            )
            .returning(RunRecord.id)
        )
        if changed is None:
            return 0
        # 接管后的终态释放：与回收同事务，重复回收不再递减（CAS 幂等）。
        await release_admission_in(session, run=run)
        terminal_event_id = uuid.uuid4()
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_FAILED",
            stream_type=RUN_FAILED_EVENT,
            payload={"status": str(RunStatus.FAILED), "error_code": RUN_ABANDONED},
            event_id=terminal_event_id,
        )
        # 回收也是用户可见的终态：通知与终态同一事务（design §3.6）。
        await write_run_notification(
            session,
            run=run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=run_terminal_delivery_key(run.id),
            event_id=terminal_event_id,
        )
    return 1


async def expire_queued_runs(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int = 128
) -> int:
    """初始排队期限到期的 Run：CAS 置 FAILED/RUN_QUEUE_TIMEOUT 并释放份额（§3.2）。

    与「领取」竞争同一行状态 CAS：领取胜出即清 `queue_stage` ⇒ 本扫描不再命中；本扫描胜出
    ⇒ 领取的 `starting` 判定失败 ⇒ 超时后绝不调用模型。候选限定「从未被领取过」
    （`execution_epoch == 0` 且 `queue_stage` 非空）：只约束首次领取前的等待，已开跑/续跑/
    人工恢复的 Run 不会被旧排队期限误杀（后续模型等待另受等待预算与 Run 绝对期限约束）。
    """
    async with session_factory() as session:
        candidates = list(
            await session.scalars(
                sa.select(RunRecord)
                .where(
                    RunRecord.queue_stage.is_not(None),
                    RunRecord.status == RunStatus.CREATED,
                    RunRecord.execution_epoch == 0,
                    RunRecord.queue_deadline_at.is_not(None),
                    RunRecord.queue_deadline_at <= sa.func.now(),
                    RunRecord.is_deleted.is_(False),
                )
                .order_by(RunRecord.queue_deadline_at, RunRecord.id)
                .limit(limit)
            )
        )
    expired = 0
    for candidate in candidates:
        expired += await _expire_queued_one(session_factory, candidate)
    return expired


async def _expire_queued_one(
    factory: async_sessionmaker[AsyncSession], candidate: RunRecord
) -> int:
    from .tool_result_receipts import lock_run_source

    run_id, tenant_id = candidate.id, candidate.tenant_id
    async with factory() as session, session.begin():
        run, _ = await lock_run_source(session, tenant_id, candidate)
        changed = await session.scalar(
            sa.update(RunRecord)
            .where(
                RunRecord.id == run.id,
                RunRecord.tenant_id == run.tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status == RunStatus.CREATED,
                RunRecord.execution_epoch == 0,
                RunRecord.queue_stage.is_not(None),
                RunRecord.queue_deadline_at.is_not(None),
                RunRecord.queue_deadline_at <= sa.func.now(),
            )
            .values(
                status=RunStatus.FAILED,
                error_code=RUN_QUEUE_TIMEOUT,
                error_message=RUN_QUEUE_TIMEOUT,
                queue_stage=None,
                queue_reason=RUN_QUEUE_TIMEOUT,
                end_time=sa.func.now(),
                lease_owner=None,
                lease_until=None,
                update_time=sa.func.now(),
            )
            .returning(RunRecord.id)
        )
        if changed is None:
            return 0
        await release_admission_in(session, run=run)
        await _close_queued_run(session, run)
    record_counter(RUN_QUEUE_TIMEOUT_METRIC)
    logger.warning("run_queue_timeout", extra={"run_id": str(run_id), "tenant_id": tenant_id})
    return 1


async def _close_queued_run(session: AsyncSession, run: RunRecord) -> None:
    """排队过期的收尾（与终态更新同事务）：RUN_FAILED 事件 + 用户通知 + 关闭仍 OPEN 的提交。

    终态 Run 不得留下可被继续领取的 OPEN 提交，否则重放/领取路径会读到半开的旧提交。
    """
    terminal_event_id = uuid.uuid4()
    seq = await EventWriter(session).append(
        tenant_id=run.tenant_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type="RUN_FAILED",
        stream_type=RUN_FAILED_EVENT,
        payload={"status": str(RunStatus.FAILED), "error_code": RUN_QUEUE_TIMEOUT},
        event_id=terminal_event_id,
    )
    # 排队超时也是用户可见的终态：通知与终态同一事务（design §3.6）。
    await write_run_notification(
        session,
        run=run,
        kind=KIND_RUN_COMPLETED,
        delivery_key=run_terminal_delivery_key(run.id),
        event_id=terminal_event_id,
    )
    submission_id = await session.scalar(
        sa.select(RunSubmission.id).where(
            RunSubmission.tenant_id == run.tenant_id,
            RunSubmission.run_id == run.id,
            RunSubmission.status == "OPEN",
            RunSubmission.is_deleted.is_(False),
        )
    )
    if submission_id is None:
        return
    await RunSubmissionService(get_session_factory).finalize_in(
        session,
        submission_id,
        status=SUBMISSION_CLOSED,
        last_seq=int(seq),
        terminal_result_json={"status": str(RunStatus.FAILED), "error_code": RUN_QUEUE_TIMEOUT},
    )


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
            # 暂存上传引用（upload_id 是不透明 UUID）：同 key 换上传就是另一个请求；
            # 指纹只含 upload_id，不含权威元信息——那些在受理时从登记行读取。
            "upload_ids": sorted(str(upload_id) for upload_id in request.message.upload_ids),
            "route": {
                "bot_id": request.channel.bot_id,
                "external_user_id": request.channel.external_user_id,
                "external_conversation_id": request.channel.external_conversation_id,
            },
        },
        message_id=request.message.id,
    )

def _resume_fingerprint(
    run_id: uuid.UUID,
    tenant_id: str,
    platform_user_id: uuid.UUID,
    input_text: str,
    message_id: str | None,
    confirmation: ConfirmationBinding | None,
) -> str:
    """resume 幂等指纹：原提议绑定（submission/source）是**请求语义的一部分**。

    同键换了绑定就是另一个请求（`IDEMPOTENCY_MISMATCH`），而不是把新绑定悄悄丢给旧回执。
    """
    return submission_fingerprint(
        endpoint=ENDPOINT_RESUME_RUN,
        run_id=run_id,
        key_payload={
            "tenant_id": tenant_id,
            "platform_user_id": str(platform_user_id),
            "type": "text",
            "text": input_text,
            "submission_id": str(confirmation.submission_id) if confirmation else None,
            "source_event_id": str(confirmation.source_event_id) if confirmation else None,
        },
        message_id=message_id,
    )


def _credential_canaries(
    model: ResolvedModel, summary: ResolvedModel | None, secrets: Mapping[str, str],
) -> tuple[str, ...]:
    values = (model.api_key, summary.api_key if summary else None, *secrets.values())
    return tuple(value for value in values if value)


#: 每进程同时进行的准入上限：把写事务占用的 DB 连接数封顶，池的其余连接留给执行与调度。
#: 800 用户实测：200 个并发准入各持连接约 4s，把单 Pod 200 的池占满，准入在 claim_in
#: 排队 26s（正是 30s 客户端超时的来源）。超出闸门的请求在 asyncio 排队、不占连接。
ADMISSION_CONCURRENCY_LIMIT = 64


_process_admission_gates: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()


def process_admission_gate(limit: int = ADMISSION_CONCURRENCY_LIMIT) -> asyncio.Semaphore:
    """取当前事件循环的准入闸门（与执行/调度共享同一个 DB 池）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.Semaphore(limit)
    gate = _process_admission_gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(limit)
        _process_admission_gates[loop] = gate
    return gate


#: 取消探测的 PG 兜底间隔：Redis 提示是快路径，PG 只在提示命中或间隔到时读一次。
CANCEL_DB_FALLBACK_SEC = 5.0


def _log_launch_failure(task: asyncio.Task[None]) -> None:
    """后台启动任务失败必须留痕（不得静默丢失）：被取消是正常收尾。"""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning(
            "run_launch_failed", extra={"error_type": type(error).__name__}
        )


class _CancelProbe:
    """单 Run 的取消探测节流：每个 250ms 节拍只问 Redis，PG 每 `CANCEL_DB_FALLBACK_SEC` 兜底一次。

    旧实现每个节拍都开一个 DB 会话（800 个 Run ≈ 3,200 次/秒）：会话 churn 耗尽连接池，
    SQLAlchemy 的阻塞式取连接把事件循环冻住（实测 14s 的 idle-in-transaction 停顿、准入
    写事务 P50 4.5s）。DB 仍是权威事实：Redis 提示命中立即用 PG 确认，未命中按间隔兜底；
    Redis 不可用时退化为纯兜底（取消检测 ≤5s），不影响正确性。
    """

    def __init__(
        self,
        *,
        hint_set: Callable[[], Awaitable[bool]],
        check_db: Callable[[], Awaitable[bool]],
        fallback_sec: float = CANCEL_DB_FALLBACK_SEC,
    ) -> None:
        self._hint_set = hint_set
        self._check_db = check_db
        self._fallback_sec = fallback_sec
        self._last_db_check = float("-inf")

    async def check(self) -> bool:
        if await self._hint_set():
            self._last_db_check = time.monotonic()
            return await self._check_db()
        now = time.monotonic()
        if now - self._last_db_check < self._fallback_sec:
            return False
        self._last_db_check = now
        return await self._check_db()


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
        artifact_root: str | None = None,
        tail_hub: EventTailHub | None = None,
    ) -> None:
        self._session = session
        self._supervisor = supervisor
        self._resolve_client = resolve_client
        self._instance_id = instance_id
        self._executor_factory = executor_factory
        self._cancel_hints = cancel_hints or NullCancelHintStore()
        self._launch_tasks: set[asyncio.Task[None]] = set()
        self._credentials_client = credentials_client
        # 平台设置源是**必填**：新 Run 在创建边界取一次快照并冻结。不留隐式 Null 默认——
        # 新建调用点忘记注入真 client 会静默按空设置跑；取消/回收/直构的调用点若要
        # 「无设置源」语义，显式传 `NullPlatformSettingsClient()`。
        self._settings_client: PlatformSettingsClient = settings_client
        # 全局准入（TASK-029/030）：策略版本应用与 USER→TENANT 计数都在 Run 创建事务内完成，
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
        # 共享事件追尾（ADR-02）：同进程按会话共享上游读取、提交后唤醒、超时按 PG 有界补查。
        # 写入方与追尾方必须是**同一份**唤醒状态；显式注入用于测试或替换装配。会话工厂按
        # 本模块的 `get_session_factory` 绑定（与其它共享服务同源，测试可替换）。
        self._tail_hub = tail_hub or get_event_tail_hub(get_session_factory)
        # 共享 artifact store 根：Run 独立不可变制品（暂存上传受理）与内联图片读取共用；
        # 显式注入用于测试隔离，生产装配回落 `SharedSettings.artifact_root`。
        fallback_root = Path(self._settings.artifact_root)
        self._artifact_root = Path(artifact_root) if artifact_root is not None else fallback_root

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
        gate_started = time.monotonic()
        async with process_admission_gate():
            gate_ms = int((time.monotonic() - gate_started) * 1000)
            for attempt in range(self._admission.stale_refresh_attempts + 1):
                attempt_started = time.monotonic()
                try:
                    started = await self._admit(request, tenant_id, key, fingerprint)
                    elapsed = time.monotonic() - attempt_started
                    if elapsed > 1.0:
                        logger.warning(
                            "admission_slow",
                            extra={
                                "tenant_id": tenant_id,
                                "attempt": attempt,
                                "gate_ms": gate_ms,
                                "elapsed_ms": int(elapsed * 1000),
                            },
                        )
                    return started
                except AdmissionPolicyStaleError:
                    # 先退出事务（连同未提交的 Run/会话）再重取，事务内不联网。
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
        """一次受理尝试：重放预检 →（事务外）定义/设置/凭据 → 短事务：同键串行化 + 建 Run。

        网络调用全部在写事务**之外**：预检后先 `rollback` 把连接还给池，再取 Console 材料；
        进入写事务后只剩 DB 语句。这样每进程的 DB 池按"并发 × 毫秒级持有"排队，而不是按
        3 次 Console 往返排队。
        """
        t0 = time.monotonic()
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
        # 只读预检：已有活跃 Run 的请求走 resume / RUN_BUSY，**不得**重新 resolve 定义与设置
        # （快照续跑的铁律）。指针会话的解析即使发生切换也会随下面的 rollback 回滚，由写事务重做。
        conversation = await self._resolve_conversation(request, tenant_id)
        has_active = await self._active_run(conversation.id) is not None
        # 预检到此结束：后面的 Console 读取不得占用 DB 连接（池按并发而非 RTT 排队）。
        await self._session.rollback()
        t_precheck = time.monotonic() - t0
        materials = None if has_active else await self._admission_materials(request, tenant_id)
        t_materials = time.monotonic() - t0 - t_precheck
        # 同键请求先经事务 advisory 锁串行化（RULE-api-002）：同键事务只在 advisory 锁上
        # 排队；锁内复查首次提交 ⇒ 落败者直接重放，不建第二个 Run。
        claim_started = time.monotonic()
        replay = await self._submissions.claim_in(
            self._session,
            tenant_id=tenant_id,
            idempotency_key=key,
            endpoint=ENDPOINT_CREATE_RUN,
            fingerprint=fingerprint,
        )
        claim_ms = int((time.monotonic() - claim_started) * 1000)
        if replay is not None:
            self._assert_replay_owner(replay, request.platform_user_id, request.conversation_id)
            return self._replay_start(replay)
        resolve_started = time.monotonic()
        conversation = await self._resolve_conversation(request, tenant_id)
        resolve_ms = int((time.monotonic() - resolve_started) * 1000)
        active_started = time.monotonic()
        active = await self._active_run(conversation.id)
        active_ms = int((time.monotonic() - active_started) * 1000)
        if active is not None:
            return await self._resume_active(active, request, key, fingerprint)
        if materials is None:  # 预检与写事务之间出现了活跃 Run 又被清理：补取材料
            materials = await self._admission_materials(request, tenant_id)
        started = await self._create_run(
            request, tenant_id, conversation, materials, key, fingerprint
        )
        t_write = time.monotonic() - t0 - t_precheck - t_materials
        if t_precheck + t_materials + t_write > 1.0:
            logger.warning(
                "admission_stage_slow",
                extra={
                    "tenant_id": tenant_id,
                    "precheck_ms": int(t_precheck * 1000),
                    "materials_ms": int(t_materials * 1000),
                    "write_ms": int(t_write * 1000),
                    "claim_ms": claim_ms,
                    "resolve_ms": resolve_ms,
                    "active_ms": active_ms,
                },
            )
        return started

    async def _admission_materials(
        self, request: RunRequest, tenant_id: str
    ) -> _AdmissionMaterials:
        """受理边界取齐的全部**非 DB**材料：定义 → 设置 → 摘要模型 → 凭据。

        调用方必须已结束当前事务（`_admit` 的预检后 `rollback`）：这里只发网络请求，不持有
        任何数据库连接。这是"设置/凭据在业务操作边界取一次"的同一边界，不引入进程内缓存。
        """
        t0 = time.monotonic()
        resolved = await self._resolve_client.resolve(
            ResolveDefinitionRequest(
                agent_id=request.agent_id,
                actor_user_id=request.platform_user_id,
                channel=request.channel.type,
            ),
            tenant_id=tenant_id,
            trace_id=current_trace_id(),
        )
        t1 = time.monotonic()
        settings = await self._settings_client.fetch_snapshot(
            tenant_id=tenant_id, trace_id=current_trace_id() or ""
        )
        t2 = time.monotonic()
        compaction = resolve_compaction_settings(
            resolved.agent.runtime_config,
            platform_overrides=settings.settings.get("compaction"),
        )
        run_id = uuid.uuid4()
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
        t3 = time.monotonic()
        if t3 - t0 > 1.0:
            logger.warning(
                "admission_materials_slow",
                extra={
                    "tenant_id": tenant_id,
                    "resolve_ms": int((t1 - t0) * 1000),
                    "settings_ms": int((t2 - t1) * 1000),
                    "credentials_ms": int((t3 - t2) * 1000),
                },
            )
        return _AdmissionMaterials(
            run_id=run_id,
            resolved=resolved,
            settings=settings,
            compaction=compaction,
            model=model,
            summary_model=summary_model,
            mcp_secrets=mcp_secrets,
        )

    async def _resume_active(
        self, active: RunRecord, request: RunRequest, key: str, fingerprint: str
    ) -> RunStart:
        """会话已有活跃 Run：暂停中走 resume，其余明确 `RUN_BUSY`（不建第二个 Run）。"""
        if request.message.upload_ids:
            # resume 显式绑定原 Run/submission/source（API-08），不承载新的暂存上传：
            # 与其静默丢弃，不如显式校验失败。
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
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
        confirmation: ConfirmationBinding | None = None,
    ) -> RunStart:
        """续跑/确认入口：路径绑定原 Run、身份核属原用户、`confirmation` 绑定原提议身份。

        网页 resume 的 `submission_id`+`source_event_id` 是**请求语义的一部分**（进幂等指纹）：
        同键换了绑定就是另一个请求（`IDEMPOTENCY_MISMATCH`），而不是把新绑定悄悄丢给旧回执。
        """
        run = await self._load_run(run_id, tenant_id)
        if run.user_id != platform_user_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        key = require_resume_idempotency(idempotency_key=idempotency_key, input_id=input_id)
        fingerprint = _resume_fingerprint(
            run_id, tenant_id, platform_user_id, input_text, input_id, confirmation
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
            confirmation=confirmation,
        )

    async def tail_run_events(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        *,
        after_seq: int = 0,
    ) -> RunStart:
        """API-04：按已确认 canonical seq 重放/续跟既有 Run 的持久事件流。

        游标先分型：超前（`CURSOR_AHEAD`）与已过期（`CURSOR_EXPIRED`）显式失败，不静默夹取；
        只读持久事件，不重新执行、不分配新序号、不重新 resolve 配置；终态排空后关闭。
        """
        run = await self._load_run(run_id, tenant_id)
        after = max(0, after_seq)
        await self._tail_hub.check_cursor(
            tenant_id=run.tenant_id, conversation_id=run.conversation_id, after_seq=after
        )
        return RunStart(
            run_id=run.id,
            conversation_id=run.conversation_id,
            resumed=True,
            events=self._tail_events(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                after_seq=after,
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
        *,
        source: CancelSource | None = None,
    ) -> RunRecord:
        """停止命令：按**来源身份**定位该来源的活跃 Run（RISK-F03：不用「用户+Agent 最新 Run」）。

        `source` 给出显式会话或入口+外线线程时，只在该来源的会话里找活跃 Run —— 同 Agent/用户的
        其他会话（网页 vs 企微、两个历史会话）完全不受影响；未给来源时保留既有 (agent,user)
        口径（调用方没有来源事实时不猜来源）。
        """
        run = await self._active_run_for(agent_id, platform_user_id, tenant_id, source)
        if run is None:
            raise AppError(ErrorCode.NO_ACTIVE_RUN)
        return await self._cancel(run)

    async def _active_run_for(
        self,
        agent_id: uuid.UUID,
        platform_user_id: uuid.UUID,
        tenant_id: str,
        source: CancelSource | None,
    ) -> RunRecord | None:
        """来源 → 活跃 Run：给来源就核属到具体会话再取该会话的活跃 Run（只读定位，不建会话）。"""
        if source is None:
            latest: RunRecord | None = await self._session.scalar(
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
            return latest
        scope = ConversationScope(
            tenant_id=tenant_id,
            user_id=platform_user_id,
            agent_id=agent_id,
            entry_id=source.entry_id,
            external_thread_id=source.external_thread_id,
        )
        conversation = await resolve_source_conversation(
            self._session, scope, conversation_id=source.conversation_id
        )
        if conversation is None:
            return None
        return await self._active_run(conversation.id)

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

    async def get_run_scope(
        self, run_id: uuid.UUID, tenant_id: str, *, user_id: uuid.UUID
    ) -> dict[str, Any]:
        """API-07 打开流的一步归属解析：Run 必须属于该主体，入口/Agent 只从 **Run 行的会话** 导出。

        与「`get_run` + `resolve_conversation_scope`」两次往返同义：会话由已核属的 Run 本身确定，
        调用方不能拿任意 `conversation_id` 去做入口解析。
        """
        run, snapshot, wait = await self.get_run(run_id, tenant_id)
        if run.user_id != user_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        scope = await WebchatHistoryService(self._session).resolve_conversation_scope(
            tenant_id=tenant_id, user_id=user_id, conversation_id=run.conversation_id
        )
        return {
            "run": run_view(run, snapshot, wait),
            "conversation": scope.model_dump(mode="json"),
        }

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
        *,
        entry_id: uuid.UUID | None = None,
        external_thread_id: str = "",
    ) -> Conversation:
        """新建会话；带 Idempotency-Key 时按设计 §3.4.2 持久重放，不创建第二会话。

        入口可归属时（`entry_id` 非空）会话同时归因入口与外线线程，并把该入口线程的
        **当前指针原地切换**到新会话（design §3.3）；新建**不取消**任何既有会话的 Run。
        """
        scope = ConversationScope(
            tenant_id=tenant_id,
            user_id=platform_user_id,
            agent_id=agent_id,
            entry_id=entry_id,
            external_thread_id=external_thread_id,
        )
        fingerprint = submission_fingerprint(
            endpoint=ENDPOINT_CREATE_CONVERSATION,
            # `tenant_id` 与 `endpoint` 是 RULE-api-002 要求的两个判别键；入口/线程同属幂等身份
            key_payload={
                "tenant_id": tenant_id,
                "agent_id": str(agent_id),
                "platform_user_id": str(platform_user_id),
                "entry_id": str(entry_id) if entry_id is not None else None,
                "external_thread_id": external_thread_id,
            },
        )
        if idempotency_key:
            replay = await self._find_conversation_replay(
                tenant_id, idempotency_key, fingerprint, scope
            )
            if replay is not None:
                return replay
        conversation = await self._open_entry_conversation(scope)
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
            replay = await self._find_conversation_replay(
                tenant_id, idempotency_key, fingerprint, scope
            )
            if replay is not None:
                return replay
            raise AppError(ErrorCode.COMMON_CONFLICT) from exc
        return conversation

    async def _open_entry_conversation(self, scope: ConversationScope) -> Conversation:
        """解析定义 → 建入口会话 → 可归属入口**原地切换**该入口线程的当前指针（design §3.3）。"""
        await self._resolve_client.resolve(
            ResolveDefinitionRequest(
                agent_id=scope.agent_id,
                actor_user_id=scope.user_id,
                # 建会话**不经渠道**（调用方只有 agent/用户，请求体里也没有通道）⇒ 显式省略，
                # **不编一个通道名**：编造的值一旦被消费方读走就是错的（B-09）。
            ),
            tenant_id=scope.tenant_id,
            trace_id=current_trace_id(),
        )
        conversation = await create_entry_conversation(
            self._session, scope, last_message_at=await database_now(self._session)
        )
        if scope.entry_id is not None:
            await register_pointer(self._session, scope, conversation.id)
        return conversation

    async def _find_conversation_replay(
        self,
        tenant_id: str,
        idempotency_key: str,
        fingerprint: str,
        scope: ConversationScope,
    ) -> Conversation | None:
        """幂等重放：先核当前身份的入口归属，再返回既有会话（不借缓存读回别的入口结果）。"""
        replay = await self._submissions.find_replay_in(
            self._session,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=ENDPOINT_CREATE_CONVERSATION,
            fingerprint=fingerprint,
        )
        if replay is None or replay.conversation_id is None:
            return None
        conversation = await self._load_conversation(replay.conversation_id, tenant_id)
        assert_entry_attribution(conversation, scope)
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
        """会话解析（design §3.4 API-06 / ADR-07）：

        - 显式 `conversation_id`：核 tenant/user/agent/**entry** 同时归属，缺失即显式拒绝；
        - 无显式会话的入口：按 (tenant,user,agent,入口,外线线程) 锁内切换当前指针。
        """
        scope = scope_of(request, tenant_id)
        if request.conversation_id is not None:
            return await load_explicit_conversation(self._session, scope, request.conversation_id)
        return await resolve_pointer_conversation(self._session, scope)

    async def _resolve_message_uploads(
        self, request: RunRequest, tenant_id: str, settings: Mapping[str, Any]
    ) -> tuple[ChannelUpload, ...]:
        """暂存上传核属（B-253）：upload_ids 只作不透明引用，权威元信息从登记行读取。

        不可归属入口（入口键不是 UUID 形状）不能承载暂存上传——显式校验失败，不静默丢。
        """
        if not request.message.upload_ids:
            return ()
        entry_id = scope_of(request, tenant_id).entry_id
        if entry_id is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        return await resolve_message_uploads(
            self._session,
            tenant_id=tenant_id,
            user_id=request.platform_user_id,
            entry_id=entry_id,
            text=request.message.text,
            upload_ids=list(request.message.upload_ids),
            limits=upload_limits_from_settings(settings),
        )

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
        materials: _AdmissionMaterials,
        submission_key: str,
        submission_fingerprint_value: str,
    ) -> RunStart:
        started = time.monotonic()
        marks: list[tuple[str, float]] = []

        def mark(name: str) -> None:
            marks.append((name, time.monotonic() - started))

        # 受理时刻与绝对期限取自 **PG 服务端时钟**（B-235）：这份 `now` 同时冻结
        # `deadline_at`/`queued_at`/`queue_deadline_at`，之后由 PG 时钟的领取/扫描裁决——
        # 本机墙钟偏差既不能缩短也不能延长这个窗口。
        now = await database_now(self._session)
        mark("first_stmt")
        resolved = materials.resolved
        # 平台设置与凭据在**写事务之外**已取齐（`_admission_materials`）：事务内不再访问网络，
        # 连接持有只剩 DB 语句。这里只做纯计算与 DB 读写。
        snapshot_settings = materials.settings
        capacity = parse_capacity(snapshot_settings)
        compaction = materials.compaction
        # 执行期平台默认（`agent`/`memory`/`artifact.max_archive_files`/`locale.default_timezone`）
        # 同样在此冻结一次（ADR-07 等）：既是本次装配的基值，也冻进 policy_json 供 resume 读回。
        execution = execution_defaults_from_platform(snapshot_settings.settings)
        # 暂存上传核属（B-253）：只在新受理路径调用（重放已在 `_admit` 返回）；归属/数量/TTL
        # 在**昂贵解析与模型调用之前**判定，失败不留任何半建状态。权威元信息只从登记行读，
        # 客户端给的 upload_id 不携带存储键。
        staged_uploads = await self._resolve_message_uploads(
            request, tenant_id, snapshot_settings.settings
        )
        # 模型池路由（`model_pool` 五叶 + 规范 policy_hash）与模型等待预算（`capacity`）走**同一份**
        # 设置快照冻结：池策略变更只影响后续新 Run，旧 Run 的调度分布按自己那份 policy_hash 解释。
        pool = resolve_pool_execution_policy(snapshot_settings.settings)
        # 成员的非密钥引用与权重（B-224）：Key/指纹/计量身份实时解析，不落快照。这是"关系随 Run
        # 冻结"的输入——绑定/解绑/权重变更只影响后续新 Run，旧 Run 不能带出另一份物理额度。
        members = await ModelDispatchStore(self._session).read_member_refs(
            tenant_id=tenant_id, model_id=resolved.model.id
        )
        # 摘要模型（`compaction.summary.model_ref`）已在受理边界解析（ADR-06）；它的成员引用
        # 在此冻结（与主模型同一口径）：摘要调用与主调用共享物理资源上限。
        summary_model = materials.summary_model
        summary_members = (
            None
            if summary_model is None
            else await ModelDispatchStore(self._session).read_member_refs(
                tenant_id=tenant_id, model_id=summary_model.id
            )
        )
        model, mcp_secrets = materials.model, materials.mcp_secrets
        run_id = materials.run_id
        # 准入段（事务内不联网）：策略版本用原子 upsert 应用（不锁策略行），随后 Run →
        # USER → TENANT 计数行锁。更低设置版本在此抛 `AdmissionPolicyStaleError`，由
        # `start` 退出事务后重取一次。
        policy = await self._admission.apply_policy_in(
            self._session, tenant_id=tenant_id, capacity=capacity
        )
        mark("policy")
        # 初始排队投影在此冻结（与绝对期限同一份 `now`）：`queue_deadline = min(受理+配置,
        # 原 Run 绝对 deadline)`，只约束开始执行前的等待；领取/终态由各自的 CAS 清空 stage。
        deadline_at = now + timedelta(
            milliseconds=AgentPolicy.from_runtime_config(
                resolved.agent.runtime_config, base=execution.agent_policy
            ).deadline_ms
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
            deadline_at=deadline_at,
            queue_stage=QUEUE_STAGE_WAITING_RUNTIME,
            queued_at=now,
            queue_deadline_at=queue_deadline(now, capacity.queue_wait_timeout_sec, deadline_at),
            cancel_requested=False,
            execution_epoch=0,
            channel_json=request.channel.model_dump(mode="json"),
        )
        self._session.add(run)
        staged_written: list[str] = []
        try:
            await self._session.flush()
            snapshot = self._build_snapshot(
                run.id, tenant_id, resolved, compaction, execution, summary_model, pool, members,
                summary_members,
            )
            self._session.add(snapshot)
            await self._session.flush()
            run.snapshot_id = snapshot.id
            mark("inserts")
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
            # 暂存上传（B-253）：受理为**本 Run 自己的**不可变制品（发布≠暂存键），与
            # Run/Snapshot/事件同事务；`staged_written` 供本事务失败时回滚盘上文件。
            staged_persisted = await persist_staged_inbound_attachments(
                self._session,
                tenant_id=tenant_id,
                run_id=run.id,
                conversation_id=conversation.id,
                uploads=staged_uploads,
                artifact_root=self._artifact_root,
                source=str(request.channel.type),
                written=staged_written,
            )
            persisted = persisted + staged_persisted
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
            # 排队阶段随受理事务一起持久化（行 + `run.stage` 事件同一份 now()；B-236）
            await self._append_run_stage(run)
            conversation.last_run_id = run.id
            conversation.update_time = now
            # 真实可见用户消息事务内更新会话活动与首条标题（design §3.3）：标题不调模型，
            # locale 与执行默认同源（同一份设置快照；坏文档在 `execution_defaults_from_platform`
            # 处已显式失败）。
            record_user_message(
                conversation,
                text=request.message.text,
                has_attachments=bool(request.message.attachments),
                locale=title_locale_of(snapshot_settings.settings),
                at=now,
            )
            # 计数自增与 Run 行同事务（B-230）：达阈值即拒，整个事务回滚——不留半建 Run，
            # 也不留下只增不减的占位。
            await self._admission.reserve_counts_in(
                self._session,
                tenant_id=tenant_id,
                user_id=request.platform_user_id,
                policy=policy,
                exclude_run_id=run.id,
            )
            mark("work")
            await self._session.commit()
            mark("commit")
        except IntegrityError as exc:
            # 并发同 key 幂等记录落败：读取首次提交结果（设计 API-01）
            await self._session.rollback()
            discard_written(self._artifact_root, staged_written)
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
            discard_written(self._artifact_root, staged_written)
            raise
        except BaseException:
            # 提交前任何其他失败：事务不会带上盘上的 Run 制品，文件先回滚再让异常继续上抛。
            discard_written(self._artifact_root, staged_written)
            raise
        mark("wake_head")
        await self._wake_tail(tenant_id, conversation.id)  # 提交后唤醒（ADR-02）：受理事务已落库
        mark("wake")
        history = await self._load_history(
            conversation.id,
            tenant_id,
            request.platform_user_id,
            budget_messages=compaction.history_budget_messages,
            max_history_tool_rounds=compaction.max_history_tool_rounds,
        )
        mark("history")
        if marks and marks[-1][1] > 1.0:
            logger.warning(
                "admission_write_slow",
                extra={
                    "tenant_id": tenant_id,
                    **{f"{name}_ms": int(elapsed * 1000) for name, elapsed in marks},
                },
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
            summary_members=summary_members,
        )
        self._start_launch_task(run, stream)
        return RunStart(
            run_id=run.id,
            conversation_id=conversation.id,
            resumed=False,
            events=self._replay_events(submission),
        )

    async def _resume_run(
        self, run: RunRecord, input_text: str, *, attachments: Sequence[AttachmentRef] = (),
        submission_key: str, request_fingerprint: str, endpoint: str, message_id: str | None,
        confirmation: ConfirmationBinding | None = None,
    ) -> RunStart:
        """冻结快照续跑：锁内复核等待态/期限，再按本 Run 自己那份快照执行（不重新 resolve）。"""
        snapshot = await self._resume_snapshot(run)
        agent, model, skills, servers = self._frozen_definitions(snapshot)
        model, summary_model, secrets = await self._runtime_credentials(run.id, run.tenant_id,
            run.user_id, model, servers, summary_model=summary_model_of(snapshot.policy_json))
        canaries = _credential_canaries(model, summary_model, secrets)
        try:
            submission, persisted, replayed = await self._persist_human_resume(run, input_text,
                message_id=message_id, attachments=attachments, submission_key=submission_key,
                request_fingerprint=request_fingerprint, endpoint=endpoint,
                settings=execution_defaults_of(snapshot.policy_json).memory_policy,
                credential_canaries=canaries, confirmation=confirmation)
            await self._session.commit()
            await self._wake_tail(run.tenant_id, run.conversation_id)  # 提交后唤醒（ADR-02）
            if replayed:
                return self._replay_start(submission)
        except IntegrityError as exc:
            await self._session.rollback()
            replay = await self._submissions.find_replay_in(self._session, tenant_id=run.tenant_id,
                idempotency_key=submission_key, endpoint=endpoint, fingerprint=request_fingerprint)
            if replay is not None:
                return self._replay_start(replay)
            raise AppError(ErrorCode.RUN_BUSY) from exc
        except AppError:
            await self._session.rollback()
            raise
        except SQLAlchemyError as exc:
            await self._session.rollback()
            logger.warning("human_resume_transaction_failed", extra={"tenant_id": run.tenant_id,
                "run_id": str(run.id), "error_type": type(exc).__name__})
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
            members=frozen_model_members_of(snapshot.policy_json),
            summary_members=frozen_summary_members_of(snapshot.policy_json))
        self._start_launch_task(run, stream)
        return RunStart(run_id=run.id, conversation_id=run.conversation_id, resumed=True,
            events=self._replay_events(submission))

    async def _resume_snapshot(self, run: RunRecord) -> RuntimeSnapshot:
        """resume 前置：等待态与绝对期限在**锁内**复核，再取本 Run 自己那份冻结快照。"""
        ensure_human_resume(run, await database_now(self._session))
        return await self._load_snapshot(run)

    @staticmethod
    def _frozen_definitions(
        snapshot: RuntimeSnapshot,
    ) -> tuple[
        ResolvedAgent, ResolvedModel, tuple[ResolvedSkill, ...], tuple[ResolvedMcpServer, ...]
    ]:
        """冻结快照 → 本 Run 的定义：resume 只读它自己那一份，不重新 resolve 任何配置。"""
        return (
            ResolvedAgent.model_validate(snapshot.agent_json),
            ResolvedModel.model_validate(snapshot.model_json),
            tuple(ResolvedSkill.model_validate(item) for item in snapshot.skill_catalog_json),
            tuple(ResolvedMcpServer.model_validate(item) for item in snapshot.mcp_catalog_json),
        )

    async def _persist_human_resume(
        self, run: RunRecord, input_text: str, *, attachments: Sequence[AttachmentRef],
        submission_key: str, request_fingerprint: str, endpoint: str, message_id: str | None,
        settings: MemoryPolicySettings, credential_canaries: tuple[str, ...],
        confirmation: ConfirmationBinding | None = None,
    ) -> tuple[RunSubmission, tuple[PersistedAttachment, ...], bool]:
        await self._submissions.lock_identity(self._session, tenant_id=run.tenant_id,
            idempotency_key=submission_key, endpoint=endpoint)
        # 幂等重放先于任何复核：同一提交的重放返回**原回执**（中断已 RESOLVED，不能再复核一次）。
        replay = await self._submissions.find_replay_in(self._session, tenant_id=run.tenant_id,
            idempotency_key=submission_key, endpoint=endpoint, fingerprint=request_fingerprint)
        if replay is not None:
            self._assert_replay_owner(replay, run.user_id, run.conversation_id)
            return replay, (), True
        interrupts = await lock_human_resume(self._session, run)
        # 绑定复核在行锁之后：原提议身份必须与**锁内读到的**那一份一致，否则显式拒绝且不消费。
        pending = prepare_confirmation(interrupts, input_text, confirmation=confirmation)
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
        # 续聊也是真实可见用户消息：会话活动时间在同一事务内更新（design §3.3）；
        # 首条标题不在这里改写（首条消息在创建路径已定稿，空标题由读取层显示占位）。
        await self._session.execute(
            sa.update(Conversation)
            .where(Conversation.id == run.conversation_id)
            .values(last_message_at=sa.func.now())
        )
        return submission, tuple(persisted), False

    def _start_launch_task(
        self, run: RunRecord, stream: AsyncIterator[ExecutorEvent]
    ) -> None:
        """后台启动本地领取/消费：claim 与执行槽排队不计入受理响应时间。

        800 用户实测：内联 `claim_and_submit` 在准入响应里占约 14s（写事务分段只剩 2s）。
        领取仍由本实例发起（跑在后台任务里），失败显式留痕；Pod 退出时任务被取消，未领取的
        Run 由续跑泵按租约接管。
        """
        # 会话操作必须发生在请求会话仍打开时：后台任务里会话可能已随响应关闭
        self._session.expunge(run)
        task = asyncio.create_task(
            self._launch_execution(run, stream), name=f"launch:{run.id}"
        )
        self._launch_tasks.add(task)
        task.add_done_callback(self._launch_tasks.discard)
        task.add_done_callback(_log_launch_failure)

    async def _launch_execution(self, run: RunRecord, stream: AsyncIterator[ExecutorEvent]) -> None:
        from muad_api.context import TRACE_CORRELATION_FIELDS, context_scope

        launched_at = time.monotonic()

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

        admitted = await self._supervisor.claim_and_submit(claim)
        elapsed_ms = int((time.monotonic() - launched_at) * 1000)
        if elapsed_ms > 5000:
            logger.warning(
                "run_launch_slow",
                extra={
                    "tenant_id": run.tenant_id,
                    "run_id": str(run.id),
                    "elapsed_ms": elapsed_ms,
                    "claimed": admitted,
                },
            )

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

    async def _append_run_stage(self, run: RunRecord) -> int:
        """把当前持久阶段投影写成 `run.stage` 事件（与行状态同一事务、同一词表；B-236）。"""
        from .execution_status import project_execution_status

        return await self._event_writer().append_run_stage(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            status=project_execution_status(run),
        )

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
        now = await database_now(self._session)
        # 未开始执行（CREATED 排队 / WAITING_*）：直接 CAS 终态并释放份额；执行中才走协作取消。
        cooperative = run.status not in (
            RunStatus.CREATED,
            RunStatus.WAITING_INPUT,
            RunStatus.WAITING_TOOL,
        )
        if cooperative:
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
        else:
            await self._cancel_not_started(run, now)
        await self._session.commit()
        if not cooperative:
            # 提交后唤醒：未开始执行的取消写入了取消事件（同事务），订阅方立即看到。
            await self._wake_tail(run.tenant_id, run.conversation_id)
        if cooperative:
            await self._safe_set_cancel_hint(run.id)
        await self._session.refresh(run)
        return run

    async def _cancel_not_started(self, run: RunRecord, now: datetime) -> None:
        """未开始执行的 Run：CAS 置 CANCELLED、释放准入份额、写取消事件（同一事务）。

        与「领取」「排队过期」竞争同一行状态 CAS：只有一个终态，`admission_released_at` 只被
        赢家置位一次，落败者不释放（不双扣）。调用方已持有 Run 行锁（`lock_run_source`）。
        """
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
                queue_stage=None,
                update_time=now,
            )
            .returning(RunRecord.id)
        )
        if changed is None:
            raise AppError(ErrorCode.RUN_BUSY)
        await release_admission_in(self._session, run=run)
        await self._close_waiting_interrupts(run, now)
        await self._append_cancelled_events(run)

    async def _close_waiting_interrupts(self, run: RunRecord, now: datetime) -> None:
        """把仍在 WAITING 的中断标为取消（未开始执行的 Run 不再接受人工恢复）。"""
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

    async def _append_cancelled_events(self, run: RunRecord) -> None:
        """取消的持久事件：CANCEL 业务事实 + 终态 `RUN_COMPLETED(CANCELLED)`（SSE 排空）。

        取消是用户可见的终态：用户通知与这两行**同一事务**（design §3.6）。
        """
        writer = self._event_writer()
        await writer.append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type=CANCEL_EVENT,
            payload={"reason": "cancelled"},
        )
        terminal_event_id = uuid.uuid4()
        await writer.append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_COMPLETED",
            stream_type=RUN_COMPLETED_EVENT,
            payload={"status": str(RunStatus.CANCELLED), "final_text": "", "reason": "cancelled"},
            event_id=terminal_event_id,
        )
        await write_run_notification(
            self._session,
            run=run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=run_terminal_delivery_key(run.id),
            event_id=terminal_event_id,
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
        summary_members: Sequence[MemberRef] | None = None,
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
            summary_members=summary_members,
        )

    def _event_writer(self) -> EventWriter:
        return EventWriter(self._session)

    async def _cancel_state_in_db(self, run: RunRecord) -> bool:
        """PG 权威判定：Run 仍存在、未请求取消、执行身份仍有效（失约即停）。"""
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
                ensure_execution(row, ExecutionIdentity.of(run), await database_now(session))
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
        return NfsArtifactStore(self._artifact_root).resolve(storage_key).read_bytes()

    async def _renew_lease_loop(self, run: RunRecord) -> None:
        """续租心跳：**失去租约才退出**，瞬时故障不退出。

        以前 `_renew_lease` 一抛错就让这个后台任务带着异常结束（异常只在 `_stream_run` 的
        finally 里 `await` 时才炸出来），租约随之到期、Reaper 把**仍在执行**的 Run 置 FAILED
        —— 那正是"回收后旧执行器还在跑"的可达路径（2026-10-06 review）。瞬时故障下一轮照续；
        返回 False 才是真的不再由本实例负责（状态已变 / owner 被接管），此时退出，交给
        `_cancel_state_in_db` 的状态判据让执行器停下。
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
        summary_members: Sequence[MemberRef] | None = None,
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
                is_cancel_requested=_CancelProbe(
                    hint_set=lambda: self._cancel_hints.is_set(run.id),
                    check_db=lambda: self._cancel_state_in_db(run),
                ).check,
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
                summary_model_members=None if summary_members is None else tuple(summary_members),
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
        summary_members: Sequence[MemberRef] | None = None,
    ) -> AsyncIterator[ExecutorEvent]:
        yield await self._start_segment_event(run, submission_id, resumed)
        resume = await resume_decision(get_session_factory(), run, submission_id=submission_id)

        async def build() -> RunExecutor:
            return await self._build_executor(run, agent, model, skills, mcp_servers, mcp_secrets, history,
                compaction=compaction, execution=execution, summary_model=summary_model,
                current_text=current_text, current_attachments=current_attachments,
                submission_id=submission_id, memory_resume_ref=resume.reference,
                pool=pool, members=members, summary_members=summary_members)

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
        consume_started = time.monotonic()
        first_event = True
        first_delta_ms: int | None = None
        async for event in executor.run():
            if first_event:
                first_event = False
                first_ms = int((time.monotonic() - consume_started) * 1000)
                if first_ms > 5000:
                    logger.warning(
                        "run_first_event_slow",
                        extra={
                            "tenant_id": run.tenant_id,
                            "run_id": str(run.id),
                            "event_type": event.type,
                            "elapsed_ms": first_ms,
                        },
                    )
            if event.type == "runner.outcome":
                outcome = AgentRunStatus(event.data["status"])
                text = str(event.data.get("final_text", text))
                continue
            if event.type == "message.delta":
                if first_delta_ms is None:
                    first_delta_ms = int((time.monotonic() - consume_started) * 1000)
                    if first_delta_ms > 5000:
                        logger.warning(
                            "run_first_delta_slow",
                            extra={
                                "tenant_id": run.tenant_id,
                                "run_id": str(run.id),
                                "elapsed_ms": first_delta_ms,
                            },
                        )
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

    async def _wake_tail(self, tenant_id: str, conversation_id: uuid.UUID) -> None:
        """提交后唤醒（ADR-02）：必须在事务提交之后调用；只发轻量提示，不改写任何事实。

        提示丢失/Redis 故障只退化为超时补查，绝不改变写入结果，也不向上抛。
        """
        await self._tail_hub.publish(tenant_id=tenant_id, conversation_id=conversation_id)

    async def _persist_event(
        self,
        run: RunRecord,
        submission_id: uuid.UUID,
        sse_type: str,
        data: dict[str, Any],
    ) -> ExecutorEvent:
        async with get_session_factory()() as session:
            await lock_execution(session, ExecutionIdentity.of(run), await database_now(session))
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
        await self._wake_tail(run.tenant_id, run.conversation_id)  # 提交后唤醒（ADR-02）
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
            ensure_execution(row, ExecutionIdentity.of(run), await database_now(session))
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
                    queue_stage=None,
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
            # 终态释放与终态更新同事务：CAS 落败（并发收尾/回收已释放）即不递减。
            await release_admission_in(session, run=row)
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
            terminal_event_id = uuid.uuid4()
            seq = await writer.append(
                tenant_id=row.tenant_id,
                conversation_id=row.conversation_id,
                run_id=row.id,
                event_type="RUN_COMPLETED",
                payload=payload,
                submission_id=submission_id,
                stream_type=RUN_COMPLETED_EVENT,
                event_id=terminal_event_id,
            )
            await self._submissions.finalize_in(
                session,
                submission_id,
                status=SUBMISSION_CLOSED,
                last_seq=int(seq),
                terminal_result_json={"status": str(target)},
            )
            # 用户通知与终态**同一事务**（design §3.6）：feed 锁在 Conversation/Run 锁之后；
            # 写不进去就整体回滚，绝不先提交终态再补通知。
            await write_run_notification(
                session,
                run=row,
                kind=KIND_RUN_COMPLETED,
                delivery_key=run_terminal_delivery_key(row.id),
                event_id=terminal_event_id,
            )
            if target == RunStatus.COMPLETED:
                # 终态 assistant 正文是真实可见消息：会话活动时间与它同事务更新（design §3.3）；
                # 取消/失败不写可见 assistant 正文，不刷新（heartbeat/订阅同样不刷新）。
                await session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id == row.conversation_id)
                    .values(last_message_at=sa.func.now())
                )
                from .memory_job_enqueue import enqueue_completed

                await enqueue_completed(session, row, through_seq=int(seq))
            await session.commit()
        await self._wake_tail(run.tenant_id, run.conversation_id)  # 提交后唤醒（ADR-02）
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
            ensure_execution(current, ExecutionIdentity.of(run), await database_now(session))
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
                    queue_stage=None,
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
            # 终态释放与终态更新同事务：CAS 落败（并发收尾/回收已释放）即不递减。
            await release_admission_in(session, run=current)
            payload = {"status": str(RunStatus.FAILED), "error_code": code}
            terminal_event_id = uuid.uuid4()
            seq = await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="RUN_FAILED",
                payload=payload,
                submission_id=submission_id,
                stream_type=RUN_FAILED_EVENT,
                event_id=terminal_event_id,
            )
            await self._submissions.finalize_in(
                session,
                submission_id,
                status=SUBMISSION_CLOSED,
                last_seq=int(seq),
                terminal_result_json={"status": str(RunStatus.FAILED), "error_code": code},
            )
            # 失败也是用户可见的终态：通知与终态同一事务（design §3.6）。
            await write_run_notification(
                session,
                run=current,
                kind=KIND_RUN_COMPLETED,
                delivery_key=run_terminal_delivery_key(current.id),
                event_id=terminal_event_id,
            )
            await session.commit()
        await self._wake_tail(run.tenant_id, run.conversation_id)  # 提交后唤醒（ADR-02）
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
        """共享追尾（ADR-02）：注册唤醒 → 读水位 → 按页追到水位 → 等待或超时补查。

        - 上游读取按会话共享（`EventTailHub`）：订阅者数量不线性放大查询；
        - 只产出已提交行，按 seq 严格递增（`seq <= 游标` 不重复投递；过滤跳号合法）；
        - 读取会话在 yield 之前关闭（慢 SSE 不占数据库连接；design §3.6）；
        - 心跳由 HTTP 层产出、不经过本游标（心跳不推进业务游标）。
        """
        async for batch in self._tail_hub.tail(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            after_seq=max(0, after_seq),
        ):
            rows = [
                row for row in batch.rows if row.run_id == run_id and row.stream_type is not None
            ]
            for row in rows:
                yield _executor_event(row)
                if row.stream_type in TERMINAL_STREAM_TYPES:
                    return
            if rows:
                continue
            stop = await self._tail_stop(batch, run_id, tenant_id)
            if stop.event is not None:
                yield stop.event
            if stop.stop:
                return

    async def _tail_stop(
        self, batch: TailBatch, run_id: uuid.UUID, tenant_id: str
    ) -> _TailStop:
        """本批没有本 Run 的行：按 Run 行事实判定停止（只在空闲轮/无关行批次上查一次）。

        终态可能刚提交而终态行尚未读入：此时**不合成**，让下一轮把已提交的终态行追平后送出
        —— 终态不能先于最后持久正文。
        """
        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(RunRecord).where(
                    RunRecord.id == run_id,
                    RunRecord.tenant_id == tenant_id,
                    RunRecord.is_deleted.is_(False),
                )
            )
            if row is None:
                return _TailStop(stop=True)
            status = str(row.status)
            if status == RunStatus.WAITING_INPUT:
                return _TailStop(stop=True)
            if status not in TERMINAL_RUN_STATUSES:
                return _TailStop(stop=False)
            if await self._has_terminal_row(session, run_id):
                return _TailStop(stop=False)
            return _TailStop(stop=True, event=await self._terminal_event(session, row))

    async def _has_terminal_row(self, session: AsyncSession, run_id: uuid.UUID) -> bool:
        """已提交终态行存在性：存在即等追平后送出持久事实，不合成第二份终态。"""
        found = await session.scalar(
            sa.select(CanonicalEvent.id)
            .where(
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.stream_type.in_(TERMINAL_STREAM_TYPES),
                CanonicalEvent.is_deleted.is_(False),
            )
            .limit(1)
        )
        return found is not None


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
