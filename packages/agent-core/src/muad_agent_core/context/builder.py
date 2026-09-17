from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..model.provider import ModelMessage, ModelRequest
from ..tools.registry import ToolDefinition


@dataclass(frozen=True, slots=True)
class ContextInput:
    model_id: str
    instructions: str
    history: tuple[ModelMessage, ...] = ()
    skill_instructions: tuple[str, ...] = ()
    artifact_previews: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    tools: tuple[ToolDefinition, ...] = ()


class ContextBuilder(Protocol):
    async def build(self, context: ContextInput) -> ModelRequest: ...
