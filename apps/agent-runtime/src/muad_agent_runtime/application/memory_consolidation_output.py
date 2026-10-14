"""A complete, strict, unconfirmed batch; one invalid item rejects everything."""

from __future__ import annotations

from uuid import UUID

from muad_api import AppError
from muad_contracts.memory import MemoryCandidate, MemoryCategory, TaskStateContent, task_memory_key
from muad_contracts.tasks import ContractModel
from pydantic import Field, StrictBool, ValidationError

from .memory_write import validate_memory_content


class ConsolidationRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("REJECTED")


class ConsolidationBatch(ContractModel):
    candidates: list[MemoryCandidate] = Field(max_length=20)
    task_completed: StrictBool


def parse_output(
    text: str,
    *,
    finish_reason: str,
    conversation_id: UUID,
    root_conversation_id: UUID,
    max_candidates: int,
    credential_canaries: tuple[str, ...],
) -> ConsolidationBatch:
    if finish_reason != "stop":
        raise ConsolidationRejected()
    try:
        batch = ConsolidationBatch.model_validate_json(text)
        if len(batch.candidates) > max_candidates:
            raise ConsolidationRejected()
        candidates = [_normalize(item, conversation_id, root_conversation_id) for item in batch.candidates]
        if len({item.memory_key for item in candidates}) != len(candidates):
            raise ConsolidationRejected()
        tasks = [item for item in candidates if item.category is MemoryCategory.TASK_STATE]
        if len(tasks) > 1 or (tasks and batch.task_completed):
            raise ConsolidationRejected()
        for candidate in candidates:
            validate_memory_content(candidate, source_quote=None, credential_canaries=credential_canaries)
        return ConsolidationBatch(candidates=candidates, task_completed=batch.task_completed)
    except (ValidationError, AppError):
        raise ConsolidationRejected() from None


def _normalize(candidate: MemoryCandidate, conversation: UUID, root: UUID) -> MemoryCandidate:
    if candidate.category is MemoryCategory.TASK_STATE:
        content = candidate.content
        if (
            not isinstance(content, TaskStateContent)
            or content.root_conversation_id != root
            or content.source_conversation_id != conversation
            or candidate.memory_key != task_memory_key(root)
        ):
            raise ConsolidationRejected()
        return candidate
    key = candidate.memory_key
    if not key.startswith("infer."):
        key = "infer." + key
    return MemoryCandidate(memory_key=key, category=candidate.category, content=candidate.content)
