from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_core.agent import RunnerModelError
from muad_agent_core.model import ModelMessage
from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    ChannelContext,
    DeliveryRouteInput,
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    RunRequest,
    RunStatus,
)
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.db import get_session_factory
from ..infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
    RunSubmission,
    RuntimeSnapshot,
)
from .context_builder import BudgetPolicy, DbBackedContextBuilder
from .executor import (
    ExecutorCredentials,
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    ExecutorRunContext,
    RunExecutor,
    default_executor_factory,
)
from .ports import CredentialsClient, ResolveClient
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
DEFAULT_POLICY: dict[str, Any] = {"max_model_retries": 3}
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
HISTORY_BUDGET_MESSAGES = 40
CONVERSATION_ACTIVE = "ACTIVE"

STREAM_BUSINESS_TYPES: dict[str, str] = {
    RUN_CREATED_EVENT: "RUN_CREATED",
    "message.delta": "ASSISTANT_DELTA",
    "tool.started": "TOOL_CALL_STARTED",
    "tool.completed": "TOOL_CALL",
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


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_snapshot(
    *,
    run_id: uuid.UUID,
    tenant_id: str,
    resolved: ResolveDefinitionResponse,
) -> RuntimeSnapshot:
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
        policy_json=dict(DEFAULT_POLICY),
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        content_hash=_snapshot_hash(resolved, DEFAULT_POLICY),
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
    return len(reaped)


class RunService:
    def __init__(
        self,
        session: AsyncSession,
        resolve_client: ResolveClient,
        instance_id: str,
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
        self._submissions = submissions or RunSubmissionService(get_session_factory)
        self._context_builder = context_builder or DbBackedContextBuilder(
            session_factory=get_session_factory, budget=BudgetPolicy(max_messages=HISTORY_BUDGET_MESSAGES)
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
                "agent_id": str(request.agent_id),
                "platform_user_id": str(request.platform_user_id),
                "conversation_id": str(request.conversation_id) if request.conversation_id else None,
                "channel": request.channel.type,
                "message_type": request.message.type,
                "message_text": request.message.text,
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
            key_payload={"agent_id": str(agent_id), "platform_user_id": str(platform_user_id)},
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
                channel="WECOM",
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
        model, mcp_secrets = await self._runtime_credentials(
            run_id,
            tenant_id,
            request.platform_user_id,
            resolved.model,
            resolved.mcp_servers,
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
            snapshot = self._build_snapshot(run.id, tenant_id, resolved)
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
            seq = await self._event_writer().append(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                run_id=run.id,
                event_type=USER_MESSAGE_EVENT,
                payload={"text": request.message.text, "message_id": request.message.id},
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
        history = await self._load_history(conversation.id, tenant_id, request.platform_user_id)
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
            ),
        )

    async def _resume_run(
        self,
        run: RunRecord,
        input_text: str,
        *,
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
        model, mcp_secrets = await self._runtime_credentials(
            run.id, run.tenant_id, run.user_id, model, mcp_servers
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
            seq = await self._event_writer().append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type=USER_MESSAGE_EVENT,
                payload={"text": input_text, "resumed": True},
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
        history = await self._load_history(run.conversation_id, run.tenant_id, run.user_id)
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
            ),
        )

    async def _runtime_credentials(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        model: ResolvedModel,
        mcp_servers: Sequence[ResolvedMcpServer],
    ) -> tuple[ResolvedModel, dict[str, str]]:
        """API-09 读取当前认证值；未配置客户端时使用定义内密钥（测试/本地）。"""
        if self._credentials_client is None:
            return model, {}
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
        return model.model_copy(update={"api_key": str(api_key)}), secrets

    async def _cancel(self, run: RunRecord) -> RunRecord:
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        now = _utcnow()
        if run.status == RunStatus.WAITING_INPUT:
            await self._session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run.id, RunRecord.status == RunStatus.WAITING_INPUT)
                .values(status=RunStatus.CANCELLED, end_time=now, update_time=now)
            )
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
        self, conversation_id: uuid.UUID, tenant_id: str, user_id: uuid.UUID
    ) -> tuple[ModelMessage, ...]:
        return await self._context_builder.load_history(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )

    def _build_snapshot(
        self,
        run_id: uuid.UUID,
        tenant_id: str,
        resolved: ResolveDefinitionResponse,
    ) -> RuntimeSnapshot:
        return build_snapshot(run_id=run_id, tenant_id=tenant_id, resolved=resolved)

    def _event_writer(self) -> EventWriter:
        return EventWriter(self._session)

    async def _is_cancel_requested(self, run_id: uuid.UUID) -> bool:
        # Redis hint 仅加速；权威事实是 DB cancel_requested
        if await self._cancel_hints.is_set(run_id):
            return True
        async with get_session_factory()() as session:
            value = await session.scalar(
                sa.select(RunRecord.cancel_requested).where(RunRecord.id == run_id)
            )
        return bool(value)

    async def _renew_lease_loop(self, run_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self._settings.run_heartbeat_sec)
            if not await self._renew_lease(run_id):
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
    ) -> RunExecutor:
        return await self._executor_factory(
            ExecutorRequest(
                agent=agent,
                model=model,
                input_text=run.input_text,
                is_cancel_requested=lambda: self._is_cancel_requested(run.id),
                skills=tuple(skills),
                mcp_servers=tuple(mcp_servers),
                history=tuple(history),
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
                run, agent, model, skills, mcp_servers, mcp_secrets, history
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
            yield await self._finalize_failed(run, submission_id, failure)
            return
        yield await self._finalize_run(run, submission_id, final_text)

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
            )
            await session.commit()
        return ExecutorEvent(
            type=sse_type, data=data, seq=int(seq), timestamp=_utcnow().isoformat()
        )

    async def _finalize_run(
        self,
        run: RunRecord,
        submission_id: uuid.UUID,
        final_text: str,
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
                return await self._terminal_event(session, row)
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
        return ExecutorEvent(
            type=RUN_COMPLETED_EVENT,
            data=payload,
            seq=int(seq),
            timestamp=_utcnow().isoformat(),
        )

    async def _finalize_failed(
        self,
        run: RunRecord,
        submission_id: uuid.UUID,
        exc: Exception,
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
