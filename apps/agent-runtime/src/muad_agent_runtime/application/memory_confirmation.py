"""Stage an unconfirmed candidate from a trusted submission; pause after the round."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.suspension import MemorySuspendRequest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    FactContent,
    MemoryCandidate,
    MemoryCategory,
    RememberMemoryInput,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.tasks import ContractModel
from sqlalchemy.exc import SQLAlchemyError

from ..infrastructure.db import SessionFactory
from .async_tools.continuation_service import ExecutionIdentity
from .checkpoint import suspend_memory_confirmation
from .memory_explicit_command import MemorySavedReceipt, matches_user_command, save_user_command
from .memory_proposals import (
    _memory_state,
    _prompt,
    _proposal,
    confirmation_source,
    ensure_no_memory_confirmation,
    locked_confirmation_execution,
)
from .memory_write import validate_memory_content

logger = logging.getLogger(__name__)


class MemoryProposalReceipt(ContractModel):
    saved: Literal[False] = False
    confirmation_required: Literal[True] = True
    proposal_id: UUID


class MemoryConfirmationService:
    def __init__(
        self,
        factory: SessionFactory,
        identity: ExecutionIdentity,
        *,
        submission_id: UUID,
        settings: MemoryPolicySettings | None = None,
        credential_canaries: tuple[str, ...] = (),
    ) -> None:
        self._factory, self._identity, self._submission_id = factory, identity, submission_id
        self._settings, self._canaries = settings or MemoryPolicySettings(), credential_canaries
        self._pending: MemorySuspendRequest | None = None
        self._lock = asyncio.Lock()

    @property
    def suspend_request(self) -> MemorySuspendRequest | None:
        return self._pending

    async def propose(
        self, candidate: RememberMemoryInput, *, call_id: str
    ) -> MemoryProposalReceipt | MemorySavedReceipt:
        async with self._lock:
            if self._pending is not None:
                raise AppError(ErrorCode.MEMORY_CONFIRMATION_PENDING)
            if not self._settings.write_enabled:
                raise AppError(ErrorCode.FORBIDDEN)
            if not call_id.strip():
                raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
            candidate = RememberMemoryInput.model_validate(candidate.model_dump(mode="json"))
            validate_memory_content(
                MemoryCandidate(
                    memory_key=candidate.memory_key,
                    category=MemoryCategory(candidate.category),
                    content=FactContent(value=candidate.value),
                ),
                source_quote=candidate.source_quote,
                credential_canaries=self._canaries,
            )
            try:
                async with self._factory.begin() as session:
                    await ensure_no_memory_confirmation(session, self._identity)
                    run, _, now = await locked_confirmation_execution(session, self._identity)
                    event = await confirmation_source(session, run, self._submission_id)
                    if matches_user_command(event, candidate):
                        return await save_user_command(self._factory, session, run, event, self._identity,
                            candidate, self._settings, self._canaries)
                    row, epoch = await _memory_state(session, run, candidate.memory_key)
                    proposal = _proposal(candidate, run, event, row, epoch, now)
                    proposal_id = uuid4()
                    request = MemorySuspendRequest(
                        proposal_id, proposal, _prompt(proposal_id, proposal, row, now)
                    )
            except SQLAlchemyError as exc:
                self._database_error(exc)
            self._pending = request
            return MemoryProposalReceipt(proposal_id=proposal_id)

    async def suspend(self, progress: RunnerCheckpoint) -> None:
        async with self._lock:
            request = self._pending
            if request is None:
                raise ValueError("no memory confirmation staged")
            try:
                await suspend_memory_confirmation(self._factory, self._identity, request, progress)
            except SQLAlchemyError as exc:
                self._database_error(exc)
            self._pending = None

    def _database_error(self, exc: SQLAlchemyError) -> NoReturn:
        logger.warning(
            "memory confirmation transaction failed",
            extra={
                "tenant_id": self._identity.tenant_id,
                "run_id": str(self._identity.run_id),
                "error_type": type(exc).__name__,
            },
        )
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
