"""从 CanonicalEvent/受控 Memory/Artifact preview 构建模型请求（append-only，仅裁剪派生 request）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from muad_agent_core.context.builder import ContextInput
from muad_agent_core.model.provider import ModelMessage, ModelRequest, ModelRole
from sqlalchemy import select

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import Artifact, CanonicalEvent, UserMemory

_BUDGET_TOLERANCE = 2


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    max_messages: int = 40
    preview_max: int = 400


class DbBackedContextBuilder:
    def __init__(
        self,
        *,
        session_factory: SessionFactoryProvider,
        budget: BudgetPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._budget = budget or BudgetPolicy()

    async def build(self, context: ContextInput) -> ModelRequest:
        messages: list[ModelMessage] = [
            ModelMessage(role=ModelRole.SYSTEM, content=context.instructions)
        ]
        for skill_instruction in context.skill_instructions:
            messages.append(
                ModelMessage(role=ModelRole.SYSTEM, content=skill_instruction)
            )
        for preview in context.artifact_previews:
            messages.append(
                ModelMessage(role=ModelRole.USER, content=f"[artifact preview] {preview}")
            )
        for memory in context.memory:
            messages.append(ModelMessage(role=ModelRole.SYSTEM, content=f"[memory] {memory}"))

        if getattr(context, "conversation_id", None):
            history = await self._load_history(
                session_factory=self._session_factory,
                tenant_id=getattr(context, "tenant_id", ""),
                conversation_id=context.conversation_id,  # type: ignore[arg-type]
                user_id=getattr(context, "user_id", None),
                budget=getattr(context, "budget_messages", None) or self._budget.max_messages,
            )
            messages.extend(history)

        for tool in context.tools:
            schema = {
                "type": "object",
                "properties": dict(tool.input_schema.get("properties") or {}),
                "required": list(tool.input_schema.get("required") or []),
            }
            messages.append(
                ModelMessage(role=ModelRole.SYSTEM, content=f"[tool:{tool.name}] {json_compact(schema)}")
            )

        return ModelRequest(
            model_id=context.model_id,
            messages=tuple(messages),
            tools=tuple(context.tools),
        )

    async def _load_history(
        self,
        *,
        session_factory: SessionFactoryProvider,
        tenant_id: str,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID | None,
        budget: int,
    ) -> list[ModelMessage]:
        async with session_factory()() as session:
            events = (
                await session.execute(
                    select(CanonicalEvent)
                    .where(
                        CanonicalEvent.tenant_id == tenant_id,
                        CanonicalEvent.conversation_id == conversation_id,
                        CanonicalEvent.event_type.in_(
                            ("USER_MESSAGE", "ASSISTANT_MESSAGE", "TOOL_CALL")
                        ),
                    )
                    .order_by(CanonicalEvent.seq)
                    .limit(self._budget.max_messages * 4)
                )
            ).scalars().all()

        history: list[ModelMessage] = []
        previews: dict[uuid.UUID, str] = {}
        for event in events:
            payload = event.payload_json or {}
            if event.event_type == "USER_MESSAGE":
                history.append(
                    ModelMessage(role=ModelRole.USER, content=str(payload.get("text", "")))
                )
            elif event.event_type == "ASSISTANT_MESSAGE":
                history.append(
                    ModelMessage(role=ModelRole.ASSISTANT, content=str(payload.get("text", "")))
                )
            elif event.event_type == "TOOL_CALL":
                preview = await self._artifact_preview(
                    session_factory, tenant_id, event.artifact_id
                )
                if preview:
                    previews[event.id] = preview
                    history.append(
                        ModelMessage(
                            role=ModelRole.TOOL,
                            content=f"[tool:{payload.get('tool')}] {preview}",
                            tool_call_id=payload.get("call_id"),
                        )
                    )
                else:
                    history.append(
                        ModelMessage(
                            role=ModelRole.TOOL,
                            content=f"[tool:{payload.get('tool')}]",
                            tool_call_id=payload.get("call_id"),
                        )
                    )

        trimmed = _trim(history, budget)
        # Memory（受控长期记忆：tenant+user+enabled 过滤）
        if user_id is not None:
            memories = await self._load_memory(session_factory, tenant_id, user_id)
            if memories:
                memory_messages = [
                    ModelMessage(role=ModelRole.SYSTEM, content=f"[memory] {mem}")
                    for mem in memories
                ]
                trimmed = [*memory_messages, *trimmed]
        return trimmed

    async def _artifact_preview(
        self,
        session_factory: SessionFactoryProvider,
        tenant_id: str,
        artifact_id: uuid.UUID | None,
    ) -> str | None:
        if artifact_id is None:
            return None
        async with session_factory()() as session:
            row = await session.get(Artifact, artifact_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return (row.preview_text or "")[: self._budget.preview_max]

    async def _load_memory(
        self,
        session_factory: SessionFactoryProvider,
        tenant_id: str,
        user_id: uuid.UUID,
    ) -> list[str]:
        async with session_factory()() as session:
            rows = (
                await session.execute(
                    select(UserMemory).where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.enabled.is_(True),
                        UserMemory.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
            return [
                f"{row.memory_key}: {row.content_json.get('value', '')}" for row in rows
            ]


def _trim(history: list[ModelMessage], budget: int) -> list[ModelMessage]:
    """预算裁剪仅作用于派生 request：保留尾部 budget 条，且保留 TOOL 配对头部。"""
    if len(history) <= budget:
        return history
    tail = history[-budget:]
    # 若首条是 TOOL（截断了配对的 assistant tool_call），向后找最近的 USER 边界
    index = 0
    for i, message in enumerate(tail):
        if message.role is ModelRole.USER:
            index = i
            break
    return tail[index:]


def json_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
