"""Real inbound selection, canonical options and original task lineage in one transaction."""

import logging
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID, uuid4

from muad_agent_core.memory.resume import ResumeIntent, choose_resume_option, parse_resume_intent
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemoryResumeOption, MemoryResumeOptions, MemoryResumeRef
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent, RunRecord
from ..metrics import MEMORY_RESUME_METRIC, record_counter
from .memory_queries import MemoryQueries, MemoryReadScope, MemorySnapshot
from .memory_resume_metrics import committed_resume_metric
from .memory_resume_options import build_options, options_prompt, task_candidates, valid_option
from .memory_resume_queries import owned_conversation, pending_options, resume_decision
from .run_events import EventWriter

__all__ = ["MemoryResumeService", "resume_decision"]
logger = logging.getLogger(__name__)
Outcome = Literal["SELECTED", "CANCELLED", "EXPIRED"]


class MemoryResumeService:
    async def append_inbound(
        self,
        session: AsyncSession,
        run: RunRecord,
        *,
        submission_id: UUID,
        text: str,
        payload: Mapping[str, Any],
        settings: MemoryPolicySettings,
    ) -> int:
        await owned_conversation(session, run, lock=True)
        pending = await pending_options(session, run)
        intent = parse_resume_intent(text)
        enabled = (
            settings.task_state_injection_enabled
            and settings.max_injected_memories > 0
            and settings.max_injected_bytes > 0
        )
        revision, snapshot = await self._read(
            session, run, tasks=enabled and (pending is not None or intent is not None)
        )
        user_id = uuid4()
        seq = await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="USER_MESSAGE",
            submission_id=submission_id,
            event_id=user_id,
            payload={**payload, "text": text, "source_scope_revision": revision},
        )
        if pending is not None:
            await self._pending(
                session, run, submission_id, user_id, pending, text, snapshot if enabled else None
            )
        elif enabled and intent is not None:
            if snapshot is None:
                await self._notice(session, run, submission_id, "任务线索暂时不可读，请稍后重试。")
            else:
                await self._offer(session, run, submission_id, user_id, intent, snapshot)
        return seq

    async def _read(
        self,
        session: AsyncSession,
        run: RunRecord,
        *,
        tasks: bool,
    ) -> tuple[int | None, MemorySnapshot | None]:
        scope = MemoryReadScope(tenant_id=run.tenant_id, user_id=run.user_id)
        try:
            async with session.begin_nested():
                if tasks:
                    snapshot = await MemoryQueries._read_snapshot(session, scope)
                    return snapshot.revision, snapshot
                head = await MemoryQueries.read_scope_version_in_session(session, scope)
                return head.revision, None
        except (SQLAlchemyError, AppError, ValueError) as exc:
            record_counter(MEMORY_RESUME_METRIC, labels={"outcome": "ERROR"})
            logger.warning(
                "memory_resume_read_failed",
                extra={"tenant_id": run.tenant_id, "run_id": str(run.id), "error_type": type(exc).__name__},
            )
            return None, None

    async def _offer(
        self,
        session: AsyncSession,
        run: RunRecord,
        submission_id: UUID,
        user_id: UUID,
        intent: ResumeIntent,
        snapshot: MemorySnapshot,
        *,
        relist: bool = False,
    ) -> None:
        rows = task_candidates(snapshot.records, intent.theme)
        if not rows:
            await self._notice(session, run, submission_id, "没有可用的任务线索，请提供具体主题或任务内容。")
            return
        options = build_options(
            rows, user_id=run.user_id, source_user_event_id=user_id, theme=intent.theme, now=snapshot.as_of
        )
        option_id = uuid4()
        show = relist or len(rows) != 1
        payload = options.model_dump(mode="json")
        if show:
            payload["delta"] = options_prompt(options)
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="MEMORY_RESUME_OPTIONS",
            submission_id=submission_id,
            event_id=option_id,
            payload=payload,
            stream_type="message.delta" if show else None,
        )
        if not show:
            await self._resolve(session, run, submission_id, user_id, option_id, "SELECTED", options.items[0])

    async def _pending(
        self,
        session: AsyncSession,
        run: RunRecord,
        submission_id: UUID,
        user_id: UUID,
        pending: CanonicalEvent,
        text: str,
        snapshot: MemorySnapshot | None,
    ) -> None:
        options = self._options(pending, run)
        if options is None:
            await self._resolve(session, run, submission_id, user_id, pending.id, "EXPIRED")
            if snapshot is not None:
                await self._offer(
                    session, run, submission_id, user_id, ResumeIntent(None), snapshot, relist=True
                )
            return
        if options.user_id != run.user_id:
            raise AppError(ErrorCode.FORBIDDEN)
        selected = choose_resume_option(text, options.items)
        if selected is None or snapshot is None:
            await self._resolve(session, run, submission_id, user_id, pending.id, "CANCELLED")
            intent = parse_resume_intent(text)
            if snapshot is not None and intent is not None:
                await self._offer(session, run, submission_id, user_id, intent, snapshot)
            return
        if options.expires_at <= snapshot.as_of or valid_option(selected, snapshot.records) is None:
            await self._resolve(session, run, submission_id, user_id, pending.id, "EXPIRED")
            await self._offer(
                session, run, submission_id, user_id, ResumeIntent(options.theme), snapshot, relist=True
            )
            return
        await self._resolve(session, run, submission_id, user_id, pending.id, "SELECTED", selected)

    @staticmethod
    def _options(pending: CanonicalEvent, run: RunRecord) -> MemoryResumeOptions | None:
        try:
            return MemoryResumeOptions.model_validate(
                {k: v for k, v in pending.payload_json.items() if k != "delta"}
            )
        except ValueError as exc:
            record_counter(MEMORY_RESUME_METRIC, labels={"outcome": "ERROR"})
            logger.warning(
                "memory_resume_options_failed",
                extra={"tenant_id": run.tenant_id, "run_id": str(run.id), "error_type": type(exc).__name__},
            )
            return None

    async def _resolve(
        self,
        session: AsyncSession,
        run: RunRecord,
        submission_id: UUID,
        user_id: UUID,
        option_id: UUID,
        outcome: Outcome,
        selected: MemoryResumeOption | None = None,
    ) -> None:
        event_id = uuid4()
        payload: dict[str, Any] = {"outcome": outcome, "source_user_event_id": str(user_id)}
        if selected is not None:
            payload["resume_ref"] = MemoryResumeRef(
                memory_key=selected.memory_key,
                expected_version=selected.version,
                root_conversation_id=selected.root_conversation_id,
                options_event_id=option_id,
                resolution_event_id=event_id,
                source_user_event_id=user_id,
                candidate_hash=selected.candidate_hash,
            ).model_dump(mode="json")
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="MEMORY_RESUME_RESOLVED",
            submission_id=submission_id,
            source_event_id=option_id,
            event_id=event_id,
            payload=payload,
        )
        committed_resume_metric(session, outcome)

    async def _notice(self, session: AsyncSession, run: RunRecord, submission_id: UUID, text: str) -> None:
        await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="MEMORY_RESUME_NOTICE",
            submission_id=submission_id,
            payload={"delta": text},
            stream_type="message.delta",
        )
