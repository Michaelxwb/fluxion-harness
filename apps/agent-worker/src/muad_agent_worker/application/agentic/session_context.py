"""Read the bounded tail of a scheduled turn's target conversation.

Session targets are context, never a delivery decision. The reader is read-only and scoped by
``tenant + owner + agent``: a conversation that belongs to someone else is treated as missing.
A deleted or archived conversation raises ``SessionUnavailable`` so the execution fails (or is
paused by the caller) explicitly; the platform never substitutes another conversation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from muad_agent_core.model import ModelMessage, ModelRole
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

MAX_SESSION_MESSAGES = 20
MAX_SESSION_MESSAGE_CHARS = 2000
SESSION_ACTIVE = "ACTIVE"
SESSION_ARCHIVED = "ARCHIVED"
VISIBLE_MESSAGE_TYPES = ("USER_MESSAGE", "ASSISTANT_MESSAGE")

_CONVERSATION_QUERY = text(
    "SELECT id, status FROM runtime.conversation "
    "WHERE id = :conversation_id AND tenant_id = :tenant_id AND user_id = :user_id "
    "AND agent_id = :agent_id AND is_deleted = false"
)
_MESSAGES_QUERY = text(
    "SELECT event_type, payload_json FROM runtime.canonical_event "
    "WHERE tenant_id = :tenant_id AND conversation_id = :conversation_id "
    "AND run_id IS NOT NULL AND event_type IN ('USER_MESSAGE', 'ASSISTANT_MESSAGE') "
    "AND is_deleted = false ORDER BY seq DESC LIMIT :limit"
)


class SessionUnavailable(Exception):
    """The target session cannot be used (missing/archived/deleted)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SessionContextReader(Protocol):
    async def tail(
        self,
        *,
        tenant_id: str,
        owner_user_id: UUID,
        agent_id: UUID,
        session_id: UUID,
        limit: int = MAX_SESSION_MESSAGES,
    ) -> tuple[ModelMessage, ...]: ...


@dataclass(frozen=True, slots=True)
class DbSessionContextReader:
    """Read the visible message tail from Runtime's canonical events (read-only)."""

    sessions: async_sessionmaker[AsyncSession]

    async def tail(
        self,
        *,
        tenant_id: str,
        owner_user_id: UUID,
        agent_id: UUID,
        session_id: UUID,
        limit: int = MAX_SESSION_MESSAGES,
    ) -> tuple[ModelMessage, ...]:
        bounded = max(1, min(int(limit), MAX_SESSION_MESSAGES))
        async with self.sessions() as session:
            conversation = (
                await session.execute(
                    _CONVERSATION_QUERY,
                    {
                        "conversation_id": session_id,
                        "tenant_id": tenant_id,
                        "user_id": owner_user_id,
                        "agent_id": agent_id,
                    },
                )
            ).first()
            if conversation is None:
                raise SessionUnavailable("SESSION_NOT_FOUND", "target conversation does not exist")
            if conversation.status == SESSION_ARCHIVED:
                raise SessionUnavailable("SESSION_ARCHIVED", "target conversation is archived")
            if conversation.status != SESSION_ACTIVE:
                raise SessionUnavailable(
                    "SESSION_UNAVAILABLE", f"target conversation status is {conversation.status!r}"
                )
            rows = (
                await session.execute(
                    _MESSAGES_QUERY,
                    {
                        "tenant_id": tenant_id,
                        "conversation_id": session_id,
                        "limit": bounded,
                    },
                )
            ).all()
        return _messages(tuple(reversed(rows)))


def _messages(rows: Sequence[object]) -> tuple[ModelMessage, ...]:
    messages: list[ModelMessage] = []
    for row in rows:
        payload = getattr(row, "payload_json", None) or {}
        text_value = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        role = (
            ModelRole.USER
            if getattr(row, "event_type", None) == "USER_MESSAGE"
            else ModelRole.ASSISTANT
        )
        content = text_value.strip()
        if len(content) > MAX_SESSION_MESSAGE_CHARS:
            content = content[: MAX_SESSION_MESSAGE_CHARS - 1] + "…"
        messages.append(ModelMessage(role=role, content=content))
    return tuple(messages)


__all__ = [
    "DbSessionContextReader",
    "MAX_SESSION_MESSAGE_CHARS",
    "MAX_SESSION_MESSAGES",
    "SessionContextReader",
    "SessionUnavailable",
]
