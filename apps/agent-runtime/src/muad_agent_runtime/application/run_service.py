from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    RunRequest,
    RunStatus,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.db import get_session_factory
from ..infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
    RuntimeSnapshot,
)
from .executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
    default_executor_factory,
)
from .ports import ResolveClient

ACTIVE_RUN_STATUSES = (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.WAITING_INPUT)
TERMINAL_RUN_STATUSES = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)
PROMPT_TEMPLATE_VERSION = "1"
DEFAULT_POLICY: dict[str, Any] = {"max_model_retries": 3}
RUN_ABANDONED = "RUN_ABANDONED"
USER_MESSAGE_EVENT = "USER_MESSAGE"
ASSISTANT_MESSAGE_EVENT = "ASSISTANT_MESSAGE"
CANCEL_EVENT = "CANCEL"
INTERRUPT_WAITING = "WAITING"
INTERRUPT_RESOLVED = "RESOLVED"
RUN_CREATED_EVENT = "run.created"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"


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


def _snapshot_hash(resolved: ResolveDefinitionResponse, policy: dict[str, Any]) -> str:
    canonical = _canonical_json(
        {
            "agent": resolved.agent.model_dump(mode="json"),
            "model": resolved.model.model_dump(mode="json"),
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
    ) -> None:
        self._session = session
        self._resolve_client = resolve_client
        self._instance_id = instance_id
        self._executor_factory = executor_factory
        self._cancel_hints = cancel_hints or NullCancelHintStore()
        self._settings = SharedSettings()

    async def start(self, request: RunRequest, tenant_id: str) -> RunStart:
        resolved = await self._resolve_client.resolve(
            ResolveDefinitionRequest(
                agent_id=request.agent_id,
                actor_user_id=request.platform_user_id,
                channel=request.channel.type,
            ),
            tenant_id=tenant_id,
            trace_id=current_trace_id(),
        )
        conversation = await self._resolve_conversation(request, tenant_id)
        active = await self._active_run(conversation.id)
        if active is not None:
            if active.status == RunStatus.WAITING_INPUT:
                return await self._resume_run(active, request.message.text)
            raise AppError(ErrorCode.RUN_BUSY)
        return await self._create_run(request, tenant_id, conversation, resolved)

    async def resume(self, run_id: uuid.UUID, input_text: str, tenant_id: str) -> RunStart:
        run = await self._load_run(run_id, tenant_id)
        if run.status != RunStatus.WAITING_INPUT:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        return await self._resume_run(run, input_text)

    async def cancel_run(self, run_id: uuid.UUID) -> RunRecord:
        run = await self._session.scalar(
            sa.select(RunRecord).where(RunRecord.id == run_id, RunRecord.is_deleted.is_(False))
        )
        if run is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Run"})
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
    ) -> Conversation:
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=platform_user_id,
            agent_id=agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        self._session.add(conversation)
        await self._session.commit()
        return conversation

    async def on_client_disconnect(self, run_id: uuid.UUID) -> None:
        now = _utcnow()
        async with get_session_factory()() as session:
            await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run_id, RunRecord.status.in_(ACTIVE_RUN_STATUSES))
                .values(cancel_requested=True, update_time=now)
            )
            await session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.status.in_((RunStatus.CREATED, RunStatus.RUNNING)),
                )
                .values(status=RunStatus.CANCELLED, end_time=now, update_time=now)
            )
            await session.commit()

    async def reap_abandoned(self) -> int:
        return await reap_abandoned_runs(get_session_factory())

    async def _load_run(self, run_id: uuid.UUID, tenant_id: str) -> RunRecord:
        run = await self._session.scalar(
            sa.select(RunRecord).where(
                RunRecord.id == run_id,
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
            )
        )
        if run is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Run"})
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
            if existing is not None:
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
            status="ACTIVE",
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
    ) -> RunStart:
        now = _utcnow()
        run = RunRecord(
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
        )
        self._session.add(run)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AppError(ErrorCode.RUN_BUSY) from exc
        snapshot = self._build_snapshot(run.id, tenant_id, resolved)
        self._session.add(snapshot)
        await self._session.flush()
        run.snapshot_id = snapshot.id
        await self._mark_running(run, conversation, tenant_id, request, now)
        return RunStart(
            run_id=run.id,
            conversation_id=conversation.id,
            resumed=False,
            events=self._executor_events(
                run,
                resolved.agent,
                resolved.model,
                resolved.skills,
                request.message.text,
                resumed=False,
            ),
        )

    async def _mark_running(
        self,
        run: RunRecord,
        conversation: Conversation,
        tenant_id: str,
        request: RunRequest,
        now: datetime,
    ) -> None:
        await self._append_event(
            self._session,
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            run_id=run.id,
            event_type=USER_MESSAGE_EVENT,
            payload={"text": request.message.text, "message_id": request.message.id},
        )
        run.status = RunStatus.RUNNING
        conversation.last_run_id = run.id
        conversation.update_time = now
        await self._session.commit()

    def _executor_events(
        self,
        run: RunRecord,
        agent: ResolvedAgent,
        model: ResolvedModel,
        skills: Sequence[ResolvedSkill],
        input_text: str,
        *,
        resumed: bool,
    ) -> AsyncIterator[ExecutorEvent]:
        return self._stream_run(
            run.id,
            run.conversation_id,
            run.trace_id,
            agent,
            model,
            skills,
            input_text,
            resumed=resumed,
        )

    async def _resume_run(self, run: RunRecord, input_text: str) -> RunStart:
        snapshot = await self._load_snapshot(run)
        now = _utcnow()
        await self._append_event(
            self._session,
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type=USER_MESSAGE_EVENT,
            payload={"text": input_text, "resumed": True},
        )
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
        agent = ResolvedAgent.model_validate(snapshot.agent_json)
        model = ResolvedModel.model_validate(snapshot.model_json)
        skills = [ResolvedSkill.model_validate(item) for item in snapshot.skill_catalog_json]
        return RunStart(
            run_id=run.id,
            conversation_id=run.conversation_id,
            resumed=True,
            events=self._executor_events(run, agent, model, skills, input_text, resumed=True),
        )

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
        if run.status in (RunStatus.CREATED, RunStatus.RUNNING):
            await self._cancel_hints.set(run.id)
        await self._session.refresh(run)
        return run

    def _build_snapshot(
        self,
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
            model_json=resolved.model.model_dump(mode="json"),
            skill_catalog_json=[skill.model_dump(mode="json") for skill in resolved.skills],
            mcp_catalog_json=[server.model_dump(mode="json") for server in resolved.mcp_servers],
            policy_json=dict(DEFAULT_POLICY),
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            content_hash=_snapshot_hash(resolved, DEFAULT_POLICY),
        )

    async def _append_event(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        seq = await session.scalar(
            sa.update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.is_deleted.is_(False))
            .values(last_seq=Conversation.last_seq + 1, update_time=sa.func.now())
            .returning(Conversation.last_seq)
        )
        if seq is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Conversation"})
        session.add(
            CanonicalEvent(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                event_type=event_type,
                payload_json=payload,
            )
        )
        return seq

    async def _is_cancel_requested(self, run_id: uuid.UUID) -> bool:
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
        now = _utcnow()
        async with get_session_factory()() as session:
            result = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run_id, RunRecord.status == RunStatus.RUNNING)
                .values(
                    lease_until=_lease_deadline(self._settings.run_lease_sec),
                    heartbeat_at=now,
                    update_time=now,
                )
                .returning(RunRecord.id)
            )
            renewed = result.scalar_one_or_none() is not None
            await session.commit()
        return renewed

    async def _build_executor(
        self,
        run_id: uuid.UUID,
        agent: ResolvedAgent,
        model: ResolvedModel,
        skills: Sequence[ResolvedSkill],
        input_text: str,
    ) -> RunExecutor:
        return await self._executor_factory(
            ExecutorRequest(
                agent=agent,
                model=model,
                input_text=input_text,
                is_cancel_requested=lambda: self._is_cancel_requested(run_id),
                skills=tuple(skills),
            )
        )

    async def _stream_run(
        self,
        run_id: uuid.UUID,
        conversation_id: uuid.UUID,
        trace_id: str,
        agent: ResolvedAgent,
        model: ResolvedModel,
        skills: Sequence[ResolvedSkill],
        input_text: str,
        *,
        resumed: bool,
    ) -> AsyncIterator[ExecutorEvent]:
        yield ExecutorEvent(
            type=RUN_CREATED_EVENT,
            data={"conversation_id": str(conversation_id), "resumed": resumed, "trace_id": trace_id},
        )
        heartbeat = asyncio.create_task(self._renew_lease_loop(run_id))
        final_text = ""
        failure: Exception | None = None
        try:
            executor = await self._build_executor(run_id, agent, model, skills, input_text)
            async for event in executor.run():
                if event.type == "message.delta":
                    final_text += str(event.data.get("delta", ""))
                yield event
        except Exception as exc:
            failure = exc
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if failure is not None:
            final = await self._finalize_failed(run_id, failure)
            yield ExecutorEvent(
                type=RUN_FAILED_EVENT,
                data={"status": final.status, "error_code": final.error_code},
            )
            return
        final = await self._finalize_run(run_id, final_text)
        if final.status == RunStatus.FAILED:
            yield ExecutorEvent(
                type=RUN_FAILED_EVENT,
                data={"status": final.status, "error_code": final.error_code},
            )
        else:
            yield ExecutorEvent(
                type=RUN_COMPLETED_EVENT,
                data={"status": final.status, "final_text": final_text},
            )

    async def _finalize_run(self, run_id: uuid.UUID, final_text: str) -> FinalState:
        async with get_session_factory()() as session:
            run = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run_id))
            if run is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Run"})
            if run.status in TERMINAL_RUN_STATUSES:
                return FinalState(status=run.status, error_code=run.error_code)
            cancelled = run.cancel_requested
            target = RunStatus.CANCELLED if cancelled else RunStatus.COMPLETED
            updated = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run_id, RunRecord.status == RunStatus.RUNNING)
                .values(status=target, end_time=sa.func.now(), update_time=sa.func.now())
                .returning(RunRecord.id)
            )
            if updated.scalar_one_or_none() is None:
                await session.rollback()
                return await self._current_state(run_id)
            await self._append_event(
                session,
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run_id,
                event_type=CANCEL_EVENT if cancelled else ASSISTANT_MESSAGE_EVENT,
                payload={"reason": "cancelled"} if cancelled else {"text": final_text},
            )
            await session.commit()
            return FinalState(status=target, error_code=None)

    async def _finalize_failed(self, run_id: uuid.UUID, exc: Exception) -> FinalState:
        code = exc.code if isinstance(exc, AppError) else str(ErrorCode.COMMON_INTERNAL_ERROR)
        async with get_session_factory()() as session:
            updated = await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == run_id, RunRecord.status == RunStatus.RUNNING)
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
                return await self._current_state(run_id)
            await session.commit()
        return FinalState(status=RunStatus.FAILED, error_code=code)

    async def _current_state(self, run_id: uuid.UUID) -> FinalState:
        async with get_session_factory()() as session:
            run = await session.scalar(sa.select(RunRecord).where(RunRecord.id == run_id))
        if run is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Run"})
        return FinalState(status=run.status, error_code=run.error_code)
