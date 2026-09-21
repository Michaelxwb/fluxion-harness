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

HISTORY_EVENT_TYPES = ("USER_MESSAGE", "ASSISTANT_MESSAGE", "TOOL_CALL")


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

    async def load_history(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID | None,
        include_memory: bool = True,
        budget: int | None = None,
    ) -> tuple[ModelMessage, ...]:
        """执行链使用：取最近事件（最新保留）+ 受控 Memory，返回可直接发送的消息序列。"""
        async with self._session_factory()() as session:
            events = await self._recent_events(
                session, tenant_id, conversation_id, budget or self._budget.max_messages
            )
            history = await self._to_messages(session, tenant_id, events)
            memories = (
                await self._load_memory(session, tenant_id, user_id)
                if include_memory and user_id is not None
                else []
            )
        trimmed = _trim(history, budget or self._budget.max_messages)
        memory_messages = [
            ModelMessage(role=ModelRole.SYSTEM, content=f"[memory] {mem}") for mem in memories
        ]
        return tuple([*memory_messages, *trimmed])

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
            history = await self.load_history(
                tenant_id=getattr(context, "tenant_id", ""),
                conversation_id=context.conversation_id,  # type: ignore[arg-type]
                user_id=getattr(context, "user_id", None),
                include_memory=not bool(context.memory),
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

    async def _recent_events(
        self,
        session: Any,
        tenant_id: str,
        conversation_id: uuid.UUID,
        budget: int,
    ) -> list[CanonicalEvent]:
        """取最近 budget*4 条业务事件（倒序取再反转），保证长会话保留最新轮次。"""
        rows = (
            await session.execute(
                select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == tenant_id,
                    CanonicalEvent.conversation_id == conversation_id,
                    CanonicalEvent.event_type.in_(HISTORY_EVENT_TYPES),
                )
                .order_by(CanonicalEvent.seq.desc())
                .limit(max(budget, 1) * 4)
            )
        ).scalars().all()
        return list(reversed(rows))

    async def _to_messages(
        self,
        session: Any,
        tenant_id: str,
        events: list[CanonicalEvent],
    ) -> list[ModelMessage]:
        previews = await self._artifact_previews(
            session, tenant_id, [event.artifact_id for event in events if event.artifact_id]
        )
        history: list[ModelMessage] = []
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
                preview = previews.get(event.artifact_id) if event.artifact_id else None
                content = (
                    f"[tool:{payload.get('tool')}] {preview}"
                    if preview
                    else f"[tool:{payload.get('tool')}]"
                )
                history.append(
                    ModelMessage(
                        role=ModelRole.TOOL,
                        content=content,
                        tool_call_id=payload.get("call_id"),
                    )
                )
        return history

    async def _artifact_previews(
        self,
        session: Any,
        tenant_id: str,
        artifact_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, str]:
        if not artifact_ids:
            return {}
        rows = (
            await session.execute(
                select(Artifact).where(
                    Artifact.id.in_(artifact_ids),
                    Artifact.tenant_id == tenant_id,
                    Artifact.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        return {
            row.id: (row.preview_text or "")[: self._budget.preview_max] for row in rows
        }

    async def _load_memory(
        self,
        session: Any,
        tenant_id: str,
        user_id: uuid.UUID,
    ) -> list[str]:
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
        return [f"{row.memory_key}: {row.content_json.get('value', '')}" for row in rows]


def _trim(history: list[ModelMessage], budget: int) -> list[ModelMessage]:
    """预算裁剪仅作用于派生 request：保留尾部 budget 条，且不产生无配对的 TOOL 开头。"""
    if len(history) <= budget:
        return _drop_leading_tool(history)
    tail = history[-budget:]
    for index, message in enumerate(tail):
        if message.role is ModelRole.USER:
            return tail[index:]
    return _drop_leading_tool(tail)


def _drop_leading_tool(history: list[ModelMessage]) -> list[ModelMessage]:
    index = 0
    while index < len(history) and history[index].role is ModelRole.TOOL:
        index += 1
    return history[index:]


def json_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
