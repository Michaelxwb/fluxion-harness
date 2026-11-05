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
    #: 文本渠道（企微等）的排版版本；缺省回落到 `prompt`（旧构造点/旧事件兼容）。
    text_prompt: str = ""


class ToolSuspensionPort(Protocol):
    @property
    def suspend_request(self) -> MemorySuspendRequest | None: ...

    async def suspend(self, progress: RunnerCheckpoint) -> None: ...
