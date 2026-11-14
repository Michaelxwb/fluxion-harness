"""Canonical named memory component; replacement never relies on text prefixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from ..model.provider import ModelMessage, ModelRole
from .render import RenderedExperienceBlock


@dataclass(frozen=True, slots=True)
class MemoryReference:
    memory_key: str
    version: int
    omitted: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryComponent:
    name: ClassVar[Literal["memory"]] = "memory"
    snapshot_revision: int
    text: str | None
    references: tuple[MemoryReference, ...] = ()
    block: RenderedExperienceBlock | None = None

    @property
    def message(self) -> ModelMessage | None:
        if self.text is None:
            return None
        return ModelMessage(role=ModelRole.SYSTEM, content=self.text)


class ModelContextLoader(Protocol):
    async def refresh(self) -> MemoryComponent | None: ...
