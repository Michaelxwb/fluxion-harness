"""Typed, complete-tool-round suspension owned by the runtime."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from muad_contracts.memory import MemoryConfirmationProposal

from .continuation import RunnerCheckpoint


@dataclass(frozen=True, slots=True)
class MemorySuspendRequest:
    interrupt_id: UUID
    proposal: MemoryConfirmationProposal
    prompt: str


class ToolSuspensionPort(Protocol):
    @property
    def suspend_request(self) -> MemorySuspendRequest | None: ...

    async def suspend(self, progress: RunnerCheckpoint) -> None: ...
