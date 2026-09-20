from __future__ import annotations

import uuid
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
    # DB-backed 构建（08 TASK-009）：来源隔离与预算
    conversation_id: uuid.UUID | None = None
    tenant_id: str | None = None
    user_id: uuid.UUID | None = None
    budget_messages: int | None = None


class ContextBuilder(Protocol):
    async def build(self, context: ContextInput) -> ModelRequest: ...
