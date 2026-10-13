"""Named memory component; replacement never relies on text-prefix detection."""

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from ..model.provider import ModelMessage, ModelRole
from .render import RenderedMemoryBlock


@dataclass(frozen=True, slots=True)
class MemoryReference:
    memory_key: str
    version: int
    omitted: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryComponent:
    name: ClassVar[Literal["memory"]] = "memory"
    snapshot_revision: int
    block: RenderedMemoryBlock

    @property
    def references(self) -> tuple[MemoryReference, ...]:
        return tuple(
            MemoryReference(item.record.memory_key, item.record.version, item.omitted)
            for item in self.block.items
        )

    @property
    def message(self) -> ModelMessage | None:
        if self.block.text is None:
            return None
        return ModelMessage(role=ModelRole.SYSTEM, content=self.block.text)


class ModelContextLoader(Protocol):
    async def refresh(self) -> MemoryComponent | None: ...
