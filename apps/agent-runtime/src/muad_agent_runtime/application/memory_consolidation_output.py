"""A complete, strict, unconfirmed batch; facts are v2 candidates, TASK_STATE keeps its lane."""

from __future__ import annotations

from uuid import UUID

from muad_api import AppError
from muad_contracts.memory import RememberMemoryInput, TaskStateContent
from muad_contracts.tasks import ContractModel
from pydantic import Field, StrictBool, ValidationError

from .memory_write import validate_memory_input_v2


class ConsolidationRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("REJECTED")


class ConsolidationBatch(ContractModel):
    candidates: list[RememberMemoryInput] = Field(default_factory=list, max_length=20)
    task_state: TaskStateContent | None = None
    task_completed: StrictBool


def parse_output(
    text: str,
    *,
    finish_reason: str,
    conversation_id: UUID,
    root_conversation_id: UUID,
    max_candidates: int,
    credential_canaries: tuple[str, ...],
) -> tuple[ConsolidationBatch, int]:
    """Return the accepted batch plus the number of items dropped by taxonomy/canary rules."""

    if finish_reason != "stop":
        raise ConsolidationRejected()
    try:
        batch = ConsolidationBatch.model_validate_json(text)
        if len(batch.candidates) > max_candidates:
            raise ConsolidationRejected()
        accepted: list[RememberMemoryInput] = []
        for item in batch.candidates:
            candidate = RememberMemoryInput.model_validate(item.model_dump(mode="json"))
            validate_memory_input_v2(candidate, credential_canaries=credential_canaries)
            accepted.append(candidate)
        task_state = _task_state(batch.task_state, conversation_id, root_conversation_id)
        if task_state is not None and batch.task_completed:
            raise ConsolidationRejected()
        return (
            ConsolidationBatch(
                candidates=accepted, task_state=task_state, task_completed=batch.task_completed
            ),
            0,
        )
    except (ValidationError, AppError):
        raise ConsolidationRejected() from None


def _task_state(
    content: TaskStateContent | None, conversation: UUID, root: UUID
) -> TaskStateContent | None:
    if content is None:
        return None
    if content.root_conversation_id != root or content.source_conversation_id != conversation:
        raise ConsolidationRejected()
    return content


__all__ = ["ConsolidationBatch", "ConsolidationRejected", "parse_output"]
