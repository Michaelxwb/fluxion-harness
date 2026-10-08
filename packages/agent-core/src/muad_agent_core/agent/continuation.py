"""Runtime input port; agent-core owns no persistence or service dependencies."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..model.provider import ModelMessage


class WaitDecision(StrEnum):
    CONTINUE = "CONTINUE"
    WAIT = "WAIT"
    FINISH = "FINISH"


@dataclass(frozen=True, slots=True)
class RunnerCheckpoint:
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    consumed_event_seq: int = 0

    def __post_init__(self) -> None:
        for value in (
            self.turns,
            self.tool_calls,
            self.input_tokens,
            self.output_tokens,
            self.consumed_event_seq,
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError("checkpoint counters must be nonnegative integers or unknown tokens")


@dataclass(frozen=True, slots=True)
class RuntimeEventBatch:
    messages: tuple[ModelMessage, ...]
    cursor: int
    has_more: bool = False


class RuntimeEventPort(Protocol):
    async def drain_ready(self, cursor: int, limit: int) -> RuntimeEventBatch: ...

    async def checkpoint(self, checkpoint: RunnerCheckpoint) -> None: ...

    async def try_wait(self, checkpoint: RunnerCheckpoint, *, assistant_text: str) -> WaitDecision: ...
