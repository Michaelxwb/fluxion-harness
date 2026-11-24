"""Persist a normal user question at the existing complete-round boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.suspension import SuspensionRequest
from muad_agent_core.tools import ToolCall, ToolDefinition, ToolEffect, ToolError, ToolResult
from muad_contracts import RunStatus
from muad_logging.redaction import redact_text
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import CanonicalEvent, RunInterrupt, RunRecord
from .async_tools.continuation_service import ExecutionIdentity, _write_checkpoint
from .checkpoint import _notify_waiting_confirmation
from .execution_status import project_execution_status
from .memory_confirmation import MemoryConfirmationService
from .memory_proposals import locked_confirmation_execution
from .run_events import EventWriter


class HumanQuestion:
    def __init__(
        self,
        factory: SessionFactory,
        identity: ExecutionIdentity,
        submission_id: UUID,
        memory: MemoryConfirmationService | None,
    ) -> None:
        self._factory = factory
        self._identity = identity
        self._submission = submission_id
        self._memory = memory
        self._question: SuspensionRequest | None = None

    @property
    def suspend_request(self) -> SuspensionRequest | None:
        if self._memory is not None and self._memory.suspend_request is not None:
            return self._memory.suspend_request
        return self._question

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ask_user",
            description="Pause and ask for a value required to continue this request.",
            input_schema={
                "type": "object",
                "required": ["question"],
                "additionalProperties": False,
                "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 1500}},
            },
            effect=ToolEffect.READ,
            handler=self.ask,
        )

    async def ask(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 1500:
            raise ToolError("COMMON_VALIDATION_ERROR", "Provide a short question.")
        if self.suspend_request is not None:
            raise ToolError("COMMON_CONFLICT", "A request for user input is already staged.")
        self._question = SuspensionRequest(uuid4(), "HUMAN_INPUT", redact_text(question.strip()))
        return ToolResult('{"waiting_for_user":true}')

    async def suspend(self, progress: RunnerCheckpoint) -> None:
        if self._memory is not None and self._memory.suspend_request is not None:
            await self._memory.suspend(progress)
            return
        request = self._question
        if request is None:
            raise RuntimeError("question was not staged")
        async with self._factory.begin() as session:
            run, saved, now = await locked_confirmation_execution(session, self._identity)
            progress_row = await _write_checkpoint(session, run, saved, progress, now)
            options = await _customer_options(session, run, self._submission)
            prompt = _question_with_options(request.prompt, options)
            session.add(
                RunInterrupt(
                    id=request.interrupt_id,
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    interrupt_type=request.reason,
                    prompt_text=prompt,
                    status="WAITING",
                    options_json=options,
                    expires_at=run.deadline_at,
                )
            )
            run.status, run.lease_owner, run.lease_until, run.update_time = (
                RunStatus.WAITING_INPUT,
                None,
                None,
                now,
            )
            writer = EventWriter(session)
            event_id = uuid4()
            progress_row.context_upto_seq = await writer.append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                submission_id=self._submission,
                event_type="ASSISTANT_MESSAGE",
                payload={"text": prompt, "reply_generation": 0},
                event_id=event_id,
                stream_type="message.snapshot",
            )
            await writer.append_run_stage(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                status=project_execution_status(run),
            )
            await _notify_waiting_confirmation(
                session, run=run, interrupt_id=request.interrupt_id, event_id=event_id
            )


async def _customer_options(
    session: AsyncSession, run: RunRecord, submission_id: UUID
) -> list[dict[str, str]]:
    event = CanonicalEvent
    value = await session.scalar(
        select(event.payload_json["outcome"]["customer_options"])
        .where(
            event.tenant_id == run.tenant_id,
            event.run_id == run.id,
            event.submission_id == submission_id,
            event.event_type == "TOOL_CALL",
            event.is_deleted.is_(False),
            event.payload_json["outcome"]["business_status"].as_string() == "NEEDS_INPUT",
        )
        .order_by(event.seq.desc())
        .limit(1)
    )
    return value if isinstance(value, list) else []


def _question_with_options(prompt: str, options: list[dict[str, str]]) -> str:
    if not options:
        return prompt
    return prompt + "\n\n" + "\n".join(f"- {item['name']} ({item['id']})" for item in options)
