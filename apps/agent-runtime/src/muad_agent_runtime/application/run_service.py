from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_core.agent import AgentPolicy, RunnerModelError
from muad_agent_core.model import ModelMessage
from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_contracts import (
    AttachmentRef,
    ChannelContext,
    DeliveryRouteInput,
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    ResolveModelRequest,
    RunRequest,
    RunStatus,
)
from muad_contracts.platform_settings import (
    COMPACTION_POLICY_KEY,
    AgentSettings,
    ArtifactSettings,
    CompactionConfigError,
    CompactionSettings,
    LocaleSettings,
    MemoryPolicySettings,
    compaction_payload,
    default_compaction_settings,
    parse_compaction_settings,
    parse_platform_settings,
)
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.db import get_session_factory
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
from .context_settings import resolve_compaction_settings
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
from .ports import (
    CredentialsClient,
    PlatformSettingsClient,
    ResolveClient,
)
from .run_events import EventWriter
from .run_lease import RunLeaseService
from .run_submission import (
    ENDPOINT_CREATE_CONVERSATION,
    ENDPOINT_CREATE_RUN,
    ENDPOINT_RESUME_RUN,
    SUBMISSION_CLOSED,
    RunSubmissionService,
    require_resume_idempotency,
    submission_fingerprint,
)

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.WAITING_INPUT)
TERMINAL_RUN_STATUSES = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)
PROMPT_TEMPLATE_VERSION = "1"


def snapshot_policy(
    compaction: CompactionSettings | None = None,
    execution: ExecutionDefaults | None = None,
    summary_model: ResolvedModel | None = None,
) -> dict[str, Any]:
    """Run 侧 execution snapshot 的执行期策略——`policy_json` 的唯一构建口径。

    `agent` 分组四叶（`max_turns` / `max_tool_calls` / `deadline_ms` / `max_model_retries`，见
    ADR-07）、`memory` 分组五叶、`artifact.max_archive_files`、`locale.default_timezone` 与压缩
    配置一并冻结在这里：配置变更只影响后续新 Run，在飞的 Run 用的是它自己那一份冻结值
    （`harness-snapshot#RULE-snapshot-001` 的"Run 侧等价载体"）。

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
    policy = {
        "max_turns": agent.max_turns,
        "max_tool_calls": agent.max_tool_calls,
        "deadline_ms": agent.deadline_ms,
        "max_model_retries": agent.max_model_retries,
        "memory": {
            "write_enabled": memory.write_enabled,
            "max_injected_memories": memory.max_injected_memories,
            "max_injected_bytes": memory.max_injected_bytes,
            "max_recall_bytes": memory.max_recall_bytes,
            "recall_default_limit": memory.recall_default_limit,
        },
        "artifact": {"max_archive_files": defaults.max_archive_files},
        "locale": {"default_timezone": defaults.timezone},
        "compaction": compaction_payload(settings),
    }
    if summary_model is not None:
        # 只在真的解析出摘要模型时写这个键：老快照没有它 ⇔ 该 Run 的摘要层没跑（`summary_model_of`
        # 读回 None），语义与"配置里没开摘要"一致。
        policy["summary_model"] = _snapshot_model(summary_model)
    return policy


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


def _frozen_bool(source: Mapping[str, Any], key: str, default: bool) -> bool:
    value = source.get(key)
    return value if isinstance(value, bool) else default


def _frozen_str(source: Mapping[str, Any], key: str, default: str) -> str:
    value = source.get(key)
    return value if isinstance(value, str) and value else default


def _frozen_memory_policy(policy: Mapping[str, Any]) -> MemoryPolicySettings:
    section = _section(policy, "memory")
    defaults = MemoryPolicySettings()
    return MemoryPolicySettings(
        write_enabled=_frozen_bool(section, "write_enabled", defaults.write_enabled),
        max_injected_memories=_frozen_int(
            section, "max_injected_memories", defaults.max_injected_memories
        ),
        max_injected_bytes=_frozen_int(section, "max_injected_bytes", defaults.max_injected_bytes),
        max_recall_bytes=_frozen_int(section, "max_recall_bytes", defaults.max_recall_bytes),
        recall_default_limit=_frozen_int(
            section, "recall_default_limit", defaults.recall_default_limit
        ),
    )


def execution_defaults_of(policy: Mapping[str, Any] | None) -> ExecutionDefaults:
    """从**已冻结**的 `policy_json` 还原执行期平台默认；缺键逐叶回落 schema 默认。

    resume 走这条：在跑的 Run 用的永远是它自己那一份冻结值，不吃当前平台设置。**逐叶**回落
    （而不是整块 None）让本需求上线**前**创建的 Run 仍能读回它当时冻结的 `deadline_ms` /
    `max_model_retries`（旧行没有 `max_turns` / `memory` 等键，这些按 schema 默认补齐）。
    """
    if not isinstance(policy, Mapping):
        return ExecutionDefaults()
    agent_defaults = AgentSettings()
    return ExecutionDefaults(
        agent_policy=AgentPolicy(
            max_turns=_frozen_int(policy, "max_turns", agent_defaults.max_turns),
            max_tool_calls=_frozen_int(policy, "max_tool_calls", agent_defaults.max_tool_calls),
            deadline_ms=_frozen_int(policy, "deadline_ms", agent_defaults.deadline_ms),
            max_model_retries=_frozen_int(
                policy, "max_model_retries", agent_defaults.max_model_retries
            ),
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


def compaction_settings_of(policy: Mapping[str, Any] | None) -> CompactionSettings | None:
    """从**已冻结**的 `policy_json` 还原压缩配置；缺键或形状坏返回 None（等于不压缩）。

    resume 走这条：在跑的 Run 用的永远是它自己那一份冻结值，不吃当前配置。
    """
    compaction = (policy or {}).get(COMPACTION_POLICY_KEY)
    if not isinstance(compaction, Mapping):
        return None
    try:
        return parse_compaction_settings(compaction)
    except CompactionConfigError:
        return None


def history_budget_of(policy: Mapping[str, Any] | None) -> int | None:
    """从已冻结的 `policy_json` 取历史预算（消息条数）；缺失/形状不对时返回 None。

    这一个值有两个消费者，且**必须是同一个**：装配侧的取数守卫（`load_history`）与压缩层的条数
    兜底（`trim_history`）。返回 None 让两边各自回落默认值——resume 读到的是**这一行当时**冻结的
    预算，而不是当前配置，配置改动因此只影响后续新 Run。
    """
    compaction = (policy or {}).get("compaction")
    value = compaction.get("history_budget_messages") if isinstance(compaction, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
) -> RuntimeSnapshot:
    # 未显式传入时按「无平台设置」（`platform_overrides={}`）合并 Agent 覆盖——仅用于不经过
    # Run 创建边界的直构调用点；生产路径由 `_create_run` 在取到平台快照后显式传入。
    settings = (
        compaction
        if compaction is not None
        else resolve_compaction_settings(resolved.agent.runtime_config, platform_overrides={})
    )
    policy = snapshot_policy(settings, execution, summary_model)
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
        result = await session.execute(
            sa.update(RunRecord)
            .where(
                RunRecord.status == RunStatus.RUNNING,
                RunRecord.lease_until < sa.func.now(),
                RunRecord.is_deleted.is_(False),
            )
            .values(
                status=RunStatus.FAILED,
                error_code=RUN_ABANDONED,
                end_time=sa.func.now(),
                update_time=sa.func.now(),
            )
            .returning(RunRecord.id)
        )
        reaped = list(result.scalars())
        await session.commit()
    if reaped:
        record_counter(RUN_RECLAIM_METRIC, len(reaped))
    return len(reaped)


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
    ) -> None:
        self._session = session
        self._resolve_client = resolve_client
        self._instance_id = instance_id
        self._executor_factory = executor_factory
        self._cancel_hints = cancel_hints or NullCancelHintStore()
        self._credentials_client = credentials_client
        # 平台设置源是**必填**：新 Run 在创建边界取一次快照并冻结。不留隐式 Null 默认——
        # 新建调用点忘记注入真 client 会静默按空设置跑；取消/回收/直构的调用点若要
        # 「无设置源」语义，显式传 `NullPlatformSettingsClient()`。
        self._settings_client: PlatformSettingsClient = settings_client
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
        key = idempotency_key or request.message.id
        fingerprint = submission_fingerprint(
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
                    (ref.storage_key, ref.checksum, str(ref.kind), ref.media_type, ref.size)
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
        conversation = await self._resolve_conversation(request, tenant_id)
        active = await self._active_run(conversation.id)
        if active is not None:
            if active.status == RunStatus.WAITING_INPUT:
                return await self._resume_run(
                    active,
                    request.message.text,
                    # 暂停期间这条消息带的附件必须与文本一起进去（否则等于静默丢弃）
                    attachments=request.message.attachments,
                    submission_key=key,
                    request_fingerprint=fingerprint,
                    endpoint=ENDPOINT_CREATE_RUN,
                )
            raise AppError(ErrorCode.RUN_BUSY)
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

    async def resume(
        self,
        run_id: uuid.UUID,
        input_text: str,
        tenant_id: str,
        *,
        idempotency_key: str | None = None,
        input_id: str | None = None,
    ) -> RunStart:
        run = await self._load_run(run_id, tenant_id)
        key = require_resume_idempotency(idempotency_key=idempotency_key, input_id=input_id)
        fingerprint = submission_fingerprint(
            endpoint=ENDPOINT_RESUME_RUN,
            run_id=run_id,
            key_payload={"type": "text", "text": input_text},
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
        if run.status != RunStatus.WAITING_INPUT:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        return await self._resume_run(
            run,
            input_text,
            submission_key=key,
            request_fingerprint=fingerprint,
            endpoint=ENDPOINT_RESUME_RUN,
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
    ) -> tuple[RunRecord, RuntimeSnapshot | None]:
        run = await self._load_run(run_id, tenant_id)
        snapshot = None
        if run.snapshot_id is not None:
            snapshot = await self._session.scalar(
                sa.select(RuntimeSnapshot).where(RuntimeSnapshot.id == run.snapshot_id)
            )
        return run, snapshot

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
            .order_by(Conversation.update_time.desc())
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
        # 平台设置在此边界取一次（每个新 Run 一次），随即冻结进这一行的 snapshot。源不可读即
        # 明确失败（RULE-06）——绝不回退到任何过期默认值，失败计数由 client 在调用方侧记。
        snapshot_settings = await self._settings_client.fetch_snapshot(
            tenant_id=tenant_id, trace_id=current_trace_id() or ""
        )
        # 压缩配置在此解析一次：既冻进这一行的 snapshot，也供本次历史装配用同一个值——
        # 二者若各解析一次，配置恰好在两次之间变更就会让"冻结值"与"实际用的值"分叉。
        compaction = resolve_compaction_settings(
            resolved.agent.runtime_config,
            platform_overrides=snapshot_settings.settings.get("compaction"),
        )
        # 执行期平台默认（`agent`/`memory`/`artifact.max_archive_files`/`locale.default_timezone`）
        # 同样在此冻结一次（ADR-07 等）：既是本次装配的基值，也冻进 policy_json 供 resume 读回。
        execution = execution_defaults_from_platform(snapshot_settings.settings)
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
            cancel_requested=False,
            lease_owner=self._instance_id,
            lease_until=_lease_deadline(self._settings.run_lease_sec),
            heartbeat_at=now,
            channel_json=request.channel.model_dump(mode="json"),
        )
        self._session.add(run)
        try:
            await self._session.flush()
            snapshot = self._build_snapshot(
                run.id, tenant_id, resolved, compaction, execution, summary_model
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
            seq = await self._event_writer().append(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                run_id=run.id,
                event_type=USER_MESSAGE_EVENT,
                payload={
                    "text": request.message.text,
                    "message_id": request.message.id,
                    "attachments": attachment_payload(persisted),
                },
                submission_id=submission.id,
            )
            submission.first_seq = seq
            run.status = RunStatus.RUNNING
            conversation.last_run_id = run.id
            conversation.update_time = now
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
        history = await self._load_history(
            conversation.id,
            tenant_id,
            request.platform_user_id,
            budget_messages=compaction.history_budget_messages,
            memory_budget_ratio=compaction.memory.budget_ratio,
            memory_policy=execution.memory_policy,
        )
        return RunStart(
            run_id=run.id,
            conversation_id=conversation.id,
            resumed=False,
            events=self._stream_run(
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
            ),
        )

    async def _resume_run(
        self,
        run: RunRecord,
        input_text: str,
        *,
        attachments: Sequence[AttachmentRef] = (),
        submission_key: str,
        request_fingerprint: str,
        endpoint: str,
    ) -> RunStart:
        snapshot = await self._load_snapshot(run)
        agent = ResolvedAgent.model_validate(snapshot.agent_json)
        model = ResolvedModel.model_validate(snapshot.model_json)
        skills = [ResolvedSkill.model_validate(item) for item in snapshot.skill_catalog_json]
        mcp_servers = [
            ResolvedMcpServer.model_validate(item) for item in snapshot.mcp_catalog_json
        ]
        model, summary_model, mcp_secrets = await self._runtime_credentials(
            run.id,
            run.tenant_id,
            run.user_id,
            model,
            mcp_servers,
            # 摘要模型从**已冻结**的快照读回（`config 变更只影响后续新 Run`）：没冻结过即不跑
            summary_model=summary_model_of(snapshot.policy_json),
        )
        now = _utcnow()
        try:
            submission = await self._submissions.record_in(
                self._session,
                tenant_id=run.tenant_id,
                idempotency_key=submission_key,
                endpoint=endpoint,
                actor_user_id=run.user_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                request_fingerprint=request_fingerprint,
            )
            # 续跑同样要落**这一次**的入站附件：`WAITING_INPUT` 期间用户发来的文件不能只取文本
            # （2026-10-06 review：此前附件既不落 artifact 行、也不进事件，等于静默丢弃）。
            # 与事件同事务，保证"事件说带了附件"与"行真的在"永远一致。
            persisted = await persist_inbound_attachments(
                self._session,
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                refs=attachments,
            )
            seq = await self._event_writer().append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type=USER_MESSAGE_EVENT,
                payload={
                    "text": input_text,
                    "resumed": True,
                    "attachments": attachment_payload(persisted),
                },
                submission_id=submission.id,
            )
            submission.first_seq = seq
            cas = await self._session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.WAITING_INPUT)
                .values(
                    status=RunStatus.RUNNING,
                    lease_owner=self._instance_id,
                    lease_until=_lease_deadline(self._settings.run_lease_sec),
                    heartbeat_at=now,
                    update_time=now,
                )
                .returning(RunRecord.id)
            )
            if cas.scalar_one_or_none() is None:
                raise AppError(ErrorCode.RUN_BUSY)
            await self._session.execute(
                sa.update(RunInterrupt)
                .where(RunInterrupt.run_id == run.id, RunInterrupt.status == INTERRUPT_WAITING)
                .values(
                    status=INTERRUPT_RESOLVED,
                    resolution_json={"input": input_text},
                    resolved_at=now,
                    update_time=now,
                )
            )
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            replay = await self._submissions.find_replay_in(
                self._session,
                tenant_id=run.tenant_id,
                idempotency_key=submission_key,
                endpoint=endpoint,
                fingerprint=request_fingerprint,
            )
            if replay is not None:
                return self._replay_start(replay)
            raise AppError(ErrorCode.RUN_BUSY) from exc
        # resume 不重解析配置：历史预算从这一行已冻结的 `policy_json` 取，配置改动只影响后续新 Run。
        frozen = compaction_settings_of(snapshot.policy_json)
        execution = execution_defaults_of(snapshot.policy_json)
        history = await self._load_history(
            run.conversation_id,
            run.tenant_id,
            run.user_id,
            budget_messages=history_budget_of(snapshot.policy_json),
            memory_budget_ratio=frozen.memory.budget_ratio if frozen is not None else None,
            memory_policy=execution.memory_policy,
        )
        return RunStart(
            run_id=run.id,
            conversation_id=run.conversation_id,
            resumed=True,
            events=self._stream_run(
                run=run,
                agent=agent,
                model=model,
                skills=tuple(skills),
                mcp_servers=tuple(mcp_servers),
                mcp_secrets=mcp_secrets,
                history=history,
                submission_id=submission.id,
                resumed=True,
                compaction=frozen,
                execution=execution,
                summary_model=summary_model,
                current_text=input_text,
                # 只带**本次新落的**那批：上一轮的字节已经进过上下文，重放会把旧图再内联一次
                current_attachments=persisted,
            ),
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
            request = ResolveModelRequest(
                model_id=uuid.UUID(settings.model_ref), actor_user_id=actor_user_id
            )
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
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        now = _utcnow()
        if run.status == RunStatus.WAITING_INPUT:
            cancelled = await self._session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.WAITING_INPUT)
                .values(status=RunStatus.CANCELLED, end_time=now, update_time=now)
                .returning(RunRecord.id)
            )
            if cancelled.scalar_one_or_none() is None:
                # CAS 零行：并发的 resume 抢先把 WAITING_INPUT→RUNNING（或状态已被别处改走）。
                # 此时**一行事件都不能写** —— 写了就是"Run 实际 RUNNING、事件却宣称已取消"的假
                # 终态（2026-10-06 review）。回滚掉本事务里已排队的 interrupt 更新，按当前事实
                # （未取消）返回，由调用方按状态说话（Gateway 见非 CANCELLED 即回"已受理"）。
                await self._session.rollback()
                await self._session.refresh(run)
                return run
            await self._session.execute(
                sa.update(RunInterrupt)
                .where(RunInterrupt.run_id == run.id, RunInterrupt.status == INTERRUPT_WAITING)
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
                payload={"status": str(RunStatus.CANCELLED), "final_text": "", "reason": "cancelled"},
                stream_type=RUN_COMPLETED_EVENT,
            )
            await self._session.commit()
        else:
            await self._session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run.id,
                    RunRecord.status.in_((RunStatus.CREATED, RunStatus.RUNNING)),
                )
                .values(cancel_requested=True, update_time=now)
            )
            await self._session.commit()
            await self._safe_set_cancel_hint(run.id)
        await self._session.refresh(run)
        return run

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
        memory_budget_ratio: float | None = None,
        memory_policy: MemoryPolicySettings | None = None,
    ) -> tuple[ModelMessage, ...]:
        """取装配用的历史。

        `budget_messages` 只做取数守卫；裁剪在压缩层（同一冻结值）。`memory_budget_ratio` 是
        FEAT-09 的注入占比（分母是装配出的历史字节），`memory_policy` 是这一行冻结的注入条数/字节
        上限，三者都来自本 Run 的冻结配置。
        """
        return await self._context_builder.load_history(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            budget_messages=budget_messages,
            memory_budget_ratio=memory_budget_ratio,
            memory_policy=memory_policy,
        )

    def _build_snapshot(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        resolved: ResolveDefinitionResponse,
        compaction: CompactionSettings | None = None,
        execution: ExecutionDefaults | None = None,
        summary_model: ResolvedModel | None = None,
    ) -> RuntimeSnapshot:
        return build_snapshot(
            run_id=run_id,
            tenant_id=tenant_id,
            resolved=resolved,
            compaction=compaction,
            execution=execution,
            summary_model=summary_model,
        )

    def _event_writer(self) -> EventWriter:
        return EventWriter(self._session)

    async def _is_cancel_requested(self, run_id: uuid.UUID) -> bool:
        """执行期的唯一存活判据：True ⇒ **立刻停止**再产生任何副作用。

        名字沿用既有的回调契约，但语义比"用户按了停止"宽——它回答的是"这个 Run 还该由本实例
        继续跑吗"：

        - Redis hint：仅加速通道；
        - DB `cancel_requested`：协作式取消的权威事实；
        - **状态已离开 {RUNNING, WAITING_INPUT} / 行不存在**：这个 Run 已经不由我们负责了。
          Reaper 会把租约过期的 RUNNING 直接置 FAILED（`reap_abandoned_runs`），此时旧执行器
          若只看取消标记，就会继续建任务、发消息、写产物，与接管方同时产生副作用
          （2026-10-06 review）。WAITING_INPUT 仍算"在跑"，因为暂停的那一轮要跑完收尾。
        """
        if await self._cancel_hints.is_set(run_id):
            return True
        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    sa.select(RunRecord.status, RunRecord.cancel_requested).where(
                        RunRecord.id == run_id
                    )
                )
            ).one_or_none()
        if row is None:
            return True
        status, cancel_requested = row
        if cancel_requested:
            return True
        return status not in (RunStatus.RUNNING, RunStatus.WAITING_INPUT)

    async def _load_inbound_attachments(self, run: RunRecord) -> tuple[PersistedAttachment, ...]:
        """本 Run 的入站附件（渠道侧已写好字节，这里只回读引用）。"""
        rows = (
            await self._session.execute(
                sa.select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.artifact_type.in_((INBOUND_IMAGE, INBOUND_DOCUMENT, INBOUND_OTHER)),
                )
            )
        ).scalars().all()
        return tuple(_persisted_from_row(row) for row in rows)

    def _read_artifact_bytes(self, storage_key: str) -> bytes:
        return NfsArtifactStore(self._settings.artifact_root).resolve(storage_key).read_bytes()

    async def _renew_lease_loop(self, run_id: uuid.UUID) -> None:
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
                renewed = await self._renew_lease(run_id)
            except Exception as exc:  # noqa: BLE001 —— 心跳是后台任务，异常不得带走整条流
                logger.warning(
                    "run_lease_renew_failed",
                    extra={"run_id": str(run_id), "error": type(exc).__name__},
                )
                continue
            if not renewed:
                return

    async def _renew_lease(self, run_id: uuid.UUID) -> bool:
        return await RunLeaseService(get_session_factory).renew(
            run_id, self._instance_id, lease_sec=self._settings.run_lease_sec
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
    ) -> RunExecutor:
        text = run.input_text if current_text is None else current_text
        # `None` ⇒ 取本 Run 全部的入站附件（新建 Run 的常规路径）；续跑显式传**本次新落的**那批，
        # 不重放上一轮的入站附件——那一轮的字节已经进过上下文，重放会把旧图再内联一次。
        attachments = (
            await self._load_inbound_attachments(run)
            if current_attachments is None
            else current_attachments
        )
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
                is_cancel_requested=lambda: self._is_cancel_requested(run.id),
                skills=tuple(skills),
                mcp_servers=tuple(mcp_servers),
                history=tuple(history),
                compaction=compaction,
                execution=execution,
                summary_model=summary_model,
                credentials=ExecutorCredentials(mcp_secrets=mcp_secrets),
                run_context=ExecutorRunContext(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    user_id=run.user_id,
                    delivery_route=delivery_route_of(run.channel_json),
                ),
            )
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
    ) -> AsyncIterator[ExecutorEvent]:
        yield await self._persist_event(
            run,
            submission_id,
            RUN_CREATED_EVENT,
            {
                "conversation_id": str(run.conversation_id),
                "resumed": resumed,
                "trace_id": run.trace_id,
            },
        )
        heartbeat = asyncio.create_task(self._renew_lease_loop(run.id))
        final_text = ""
        failure: Exception | None = None
        try:
            executor = await self._build_executor(
                run,
                agent,
                model,
                skills,
                mcp_servers,
                mcp_secrets,
                history,
                compaction=compaction,
                execution=execution,
                summary_model=summary_model,
                current_text=current_text,
                current_attachments=current_attachments,
            )
            async for event in executor.run():
                if event.type == "message.delta":
                    final_text += str(event.data.get("delta", ""))
                if event.seq is not None:
                    yield event
                    continue
                yield await self._persist_event(run, submission_id, event.type, event.data)
        except Exception as exc:
            failure = exc
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if failure is not None:
            yield await self._finalize_failed(run, submission_id, failure, agent_key=agent.key)
            return
        yield await self._finalize_run(run, submission_id, final_text, agent_key=agent.key)

    async def _persist_event(
        self,
        run: RunRecord,
        submission_id: uuid.UUID,
        sse_type: str,
        data: dict[str, Any],
    ) -> ExecutorEvent:
        async with get_session_factory()() as session:
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
        return ExecutorEvent(
            type=sse_type, data=data, seq=int(seq), timestamp=_utcnow().isoformat()
        )

    async def _finalize_run(
        self, run: RunRecord, submission_id: uuid.UUID, final_text: str, *, agent_key: str
    ) -> ExecutorEvent:
        async with get_session_factory()() as session:
            row = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run.id))
            if row is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            if row.status in TERMINAL_RUN_STATUSES:
                return await self._terminal_event(session, row)
            cancelled = row.cancel_requested
            target = RunStatus.CANCELLED if cancelled else RunStatus.COMPLETED
            updated = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.RUNNING)
                .values(status=target, end_time=sa.func.now(), update_time=sa.func.now())
                .returning(RunRecord.id)
            )
            if updated.scalar_one_or_none() is None:
                await session.rollback()
                # rollback 之后 `row` 已过期：异步会话下碰它的任何属性都会抛 MissingGreenlet
                # （2026-10-06 review）。与 `_finalize_failed` 同形——重新取一行再交给
                # `_terminal_event`（它要读 id/conversation_id/status）。
                fresh = await session.scalar(
                    sa.select(RunRecord).where(RunRecord.id == run.id)
                )
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
            updated = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.RUNNING)
                .values(
                    status=RunStatus.FAILED,
                    error_code=code,
                    error_message=str(exc),
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
        if submission.conversation_id is None:
            return
        after = (submission.first_seq or 1) - 1
        while True:
            emitted_terminal = False
            async with get_session_factory()() as session:
                rows = await EventWriter(session).list_events(
                    submission.conversation_id,
                    tenant_id=submission.tenant_id,
                    submission_id=submission.id,
                    after_seq=after,
                )
                for row in rows:
                    after = int(row.seq)
                    if row.stream_type is None:
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
                run = await session.scalar(
                    sa.select(RunRecord).where(RunRecord.id == submission.run_id)
                )
                if run is None:
                    return
                if run.status in TERMINAL_RUN_STATUSES:
                    yield await self._terminal_event(session, run)
                    return
            await asyncio.sleep(REPLAY_POLL_SEC)


def _error_code_for(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return str(exc.code)
    if isinstance(exc, RunnerModelError):
        return str(ErrorCode.MODEL_UNAVAILABLE)
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


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

