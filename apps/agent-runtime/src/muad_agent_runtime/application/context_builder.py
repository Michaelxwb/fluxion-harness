"""Rebuild canonical history and summaries; memory is loaded at the request boundary."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import sqlalchemy as sa
from muad_agent_core.agent.recovery import fold_events
from muad_agent_core.context.compactor import Group, split_groups, split_protected_prefix
from muad_agent_core.context.summary import summary_from_payload, summary_message
from muad_agent_core.model.budget import MESSAGE_PROTOCOL_OVERHEAD_BYTES
from muad_agent_core.model.provider import ModelMessage, ModelRole, ModelToolCall, text_of
from pydantic import JsonValue
from sqlalchemy import select, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import Artifact, CanonicalEvent, Conversation
from .attachments.inbound import attachments_from_payload, render_attachment_reference
from .attachments.tool_results import reference_payload
from .context_events import covered_up_to, latest_summary, protected_event_ids
from .memory_confirmation_outcome import confirmation_context_note

HISTORY_EVENT_TYPES = (
    "USER_MESSAGE",
    "ASSISTANT_MESSAGE",
    "MEMORY_CONFIRMATION_RESOLVED",
    "TOOL_CALL",
    "ASSISTANT_TURN",
    "TOOL_TASK_ACCEPTED",
    "BACKGROUND_RESULT",
    "TOOL_SUBMISSION_FAILED",
)

#: keyset 分页的单页事件数：只是查询分块，不是历史上限。
READ_PAGE_SIZE = 20
#: 尾部读取留出的组边界余量。
READ_HEADROOM = 1.25
#: 头部锚点最多保留的组数（首个 user 组 + 其后首个 assistant/tool 组）。
HEAD_ANCHOR_GROUPS = 2


@dataclass(frozen=True, slots=True)
class LoadedHistory:
    """History assembly result: messages plus the seq boundaries needed by the request boundary."""

    messages: tuple[ModelMessage, ...] = ()
    covered_seq: int = 0
    material_scan_end_seq: int = 0
    head_end_seq: int | None = None
    earlier_records: int | None = 0


@dataclass(frozen=True, slots=True)
class ActiveSkillContext:
    keys: tuple[str, ...] = ()
    groups: tuple[Group, ...] = ()

    def merge(self, history: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        """Restore complete instruction groups before the request's protected-context plan."""
        if not self.groups:
            return tuple(history)
        prefix, body = split_protected_prefix(history)
        protected = {group[0].source_event_id: group for group in self.groups}
        groups = [protected.pop(group[0].source_event_id, group) for group in split_groups(body)]
        groups.extend(protected.values())
        groups.sort(key=lambda group: group[0].source_event_seq or 0)
        return (*prefix, *(message for group in groups for message in group))


class DbBackedContextBuilder:
    def __init__(self, *, session_factory: SessionFactoryProvider) -> None:
        self._session_factory = session_factory

    async def load_skill_context(
        self, *, tenant_id: str, conversation_id: uuid.UUID, run_id: uuid.UUID
    ) -> ActiveSkillContext:
        """Read this Run's completed Skill loads independently of the recent-history window."""
        async with self._session_factory()() as session:
            events = list(await session.scalars(
                select(CanonicalEvent).where(
                    CanonicalEvent.tenant_id == tenant_id,
                    CanonicalEvent.conversation_id == conversation_id,
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.event_type.in_(("ASSISTANT_TURN", "TOOL_CALL")),
                    CanonicalEvent.is_deleted.is_(False),
                ).order_by(CanonicalEvent.seq)
            ))
            references = await self._artifact_references(
                session, tenant_id, [event.artifact_id for event in events if event.artifact_id]
            )
        return _active_skill_context(events, references)

    async def load_history(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID | None,
        budget_tokens: int,
        current_message_id: str | None = None,
        through_seq: int | None = None,
        unconsumed_run_id: uuid.UUID | None = None,
        consumed_event_seq: int = 0,
    ) -> LoadedHistory:
        """Read owned canonical history within the token read budget; never trim or write."""
        if isinstance(budget_tokens, bool) or budget_tokens < 1:
            raise ValueError("history budget must be a positive integer")
        async with self._session_factory()() as session:
            if user_id is not None:
                owned = await session.scalar(
                    select(Conversation.id).where(
                        Conversation.id == conversation_id,
                        Conversation.tenant_id == tenant_id,
                        Conversation.user_id == user_id,
                        Conversation.is_deleted.is_(False),
                    )
                )
                if owned is None:
                    return LoadedHistory()

            summary_row = await latest_summary(session, tenant_id, conversation_id)
            summary_payload = summary_row.payload_json if summary_row is not None else None
            summary_fields = (
                summary_from_payload((summary_payload or {}).get("summary"))
                if summary_row is not None
                else None
            )
            covered = covered_up_to(summary_payload) if summary_fields is not None else 0
            protected = protected_event_ids(summary_payload)

            tail_events, tail_reached_boundary = await self._read_tail(
                session,
                tenant_id,
                conversation_id,
                budget_tokens,
                covered=covered,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            )
            material_events, material_scan_end = await self._read_material(
                session,
                tenant_id,
                conversation_id,
                budget_tokens,
                covered=covered,
                tail_events=tail_events,
                tail_reached_boundary=tail_reached_boundary,
                current_message_id=current_message_id,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            )
            head_events, head_end = await self._read_head_anchor(
                session,
                tenant_id,
                conversation_id,
                budget_tokens,
                covered=covered,
                current_message_id=current_message_id,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            )
            protected_events = await self._read_protected(
                session,
                tenant_id,
                conversation_id,
                protected,
                through_seq=through_seq,
            )
            events = _merge_events(head_events, material_events, tail_events, protected_events)
            references = await self._artifact_references(
                session, tenant_id, [event.artifact_id for event in events if event.artifact_id]
            )
            earlier = await self._earlier_records(
                session,
                tenant_id,
                conversation_id,
                gap_start=max(material_scan_end, head_end or 0),
                tail_start=min((event.seq for event in tail_events), default=None),
                protected=protected,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            )

        messages: list[ModelMessage] = []
        if summary_row is not None and summary_fields is not None:
            messages.append(
                replace(
                    summary_message(summary_fields),
                    source_event_id=str(summary_row.id),
                    source_event_seq=summary_row.seq,
                    origin="SUMMARY",
                )
            )
        messages.extend(
            message
            for value, message in _history_entries(
                events, _paired_tool_results(events, references)
            )
        )
        return LoadedHistory(
            messages=tuple(messages),
            covered_seq=covered,
            material_scan_end_seq=material_scan_end,
            head_end_seq=head_end,
            earlier_records=earlier,
        )

    async def _read_tail(
        self,
        session: AsyncSession,
        tenant_id: str,
        conversation_id: uuid.UUID,
        budget_tokens: int,
        *,
        covered: int,
        through_seq: int | None,
        unconsumed_run_id: uuid.UUID | None,
        consumed_event_seq: int,
    ) -> tuple[list[CanonicalEvent], bool]:
        events: list[CanonicalEvent] = []
        cursor: int | None = None
        reached_boundary = False
        while True:
            statement = select(CanonicalEvent).where(
                *self._history_conditions(
                    tenant_id,
                    conversation_id,
                    covered=covered,
                    through_seq=through_seq,
                    unconsumed_run_id=unconsumed_run_id,
                    consumed_event_seq=consumed_event_seq,
                ),
                CanonicalEvent.seq > covered,
                CanonicalEvent.seq < cursor if cursor is not None else true(),
            ).order_by(CanonicalEvent.seq.desc()).limit(READ_PAGE_SIZE)
            page = list((await session.execute(statement)).scalars().all())
            if not page:
                reached_boundary = True
                break
            events.extend(page)
            if _estimate_events_tokens(events) >= budget_tokens * READ_HEADROOM:
                break
            if len(page) < READ_PAGE_SIZE:
                reached_boundary = True
                break
            cursor = min(event.seq for event in page)
        events.sort(key=lambda event: event.seq)
        return events, reached_boundary

    async def _read_material(
        self,
        session: AsyncSession,
        tenant_id: str,
        conversation_id: uuid.UUID,
        budget_tokens: int,
        *,
        covered: int,
        tail_events: Sequence[CanonicalEvent],
        tail_reached_boundary: bool,
        current_message_id: str | None,
        through_seq: int | None,
        unconsumed_run_id: uuid.UUID | None,
        consumed_event_seq: int,
    ) -> tuple[list[CanonicalEvent], int]:
        if tail_reached_boundary:
            complete = [
                event
                for event in tail_events
                if not _is_current_input(event, current_message_id)
            ]
            return complete, max((event.seq for event in complete), default=0)

        material: list[CanonicalEvent] = []
        cursor: int | None = None
        while True:
            statement = select(CanonicalEvent).where(
                *self._history_conditions(
                    tenant_id,
                    conversation_id,
                    covered=covered,
                    through_seq=through_seq,
                    unconsumed_run_id=unconsumed_run_id,
                    consumed_event_seq=consumed_event_seq,
                ),
                CanonicalEvent.seq > covered,
                CanonicalEvent.seq > cursor if cursor is not None else true(),
            ).order_by(CanonicalEvent.seq).limit(READ_PAGE_SIZE)
            page = [
                event
                for event in (await session.execute(statement)).scalars().all()
                if not _is_current_input(event, current_message_id)
            ]
            if not page:
                break
            material.extend(page)
            if _estimate_events_tokens(material) >= budget_tokens:
                break
            if len(page) < READ_PAGE_SIZE:
                break
            cursor = max(event.seq for event in page)
        return material, max((event.seq for event in material), default=0)

    async def _read_head_anchor(
        self,
        session: AsyncSession,
        tenant_id: str,
        conversation_id: uuid.UUID,
        budget_tokens: int,
        *,
        covered: int,
        current_message_id: str | None,
        through_seq: int | None,
        unconsumed_run_id: uuid.UUID | None,
        consumed_event_seq: int,
    ) -> tuple[list[CanonicalEvent], int | None]:
        if covered != 0:
            return [], None
        statement = select(CanonicalEvent).where(
            *self._history_conditions(
                tenant_id,
                conversation_id,
                covered=0,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            ),
            CanonicalEvent.seq > 0,
        ).order_by(CanonicalEvent.seq).limit(READ_PAGE_SIZE)
        rows = [
            event
            for event in (await session.execute(statement)).scalars().all()
            if not _is_current_input(event, current_message_id)
        ]
        user_index = next(
            (index for index, event in enumerate(rows) if event.event_type == "USER_MESSAGE"),
            None,
        )
        if user_index is None:
            return [], None
        selected = [rows[user_index]]
        for event in rows[user_index + 1 :]:
            if event.event_type in ("ASSISTANT_TURN", "ASSISTANT_MESSAGE"):
                selected.append(event)
                if event.event_type == "ASSISTANT_TURN":
                    following_index = rows.index(event) + 1
                    for following in rows[following_index:]:
                        if following.event_type != "TOOL_CALL":
                            break
                        selected.append(following)
                break
            if event.event_type == "TOOL_CALL":
                selected.append(event)
                continue
            break
        if len(selected) > 1 and _estimate_events_tokens(selected) > (budget_tokens / 2):
            selected = [rows[user_index]]
        return selected, max(event.seq for event in selected)

    async def _read_protected(
        self,
        session: AsyncSession,
        tenant_id: str,
        conversation_id: uuid.UUID,
        protected: Sequence[str],
        *,
        through_seq: int | None,
    ) -> list[CanonicalEvent]:
        if not protected:
            return []
        statement = select(CanonicalEvent).where(
            CanonicalEvent.tenant_id == tenant_id,
            CanonicalEvent.conversation_id == conversation_id,
            CanonicalEvent.id.in_(protected),
            CanonicalEvent.is_deleted.is_(False),
            CanonicalEvent.seq <= through_seq if through_seq is not None else true(),
        )
        return list((await session.execute(statement)).scalars().all())

    async def _earlier_records(
        self,
        session: AsyncSession,
        tenant_id: str,
        conversation_id: uuid.UUID,
        *,
        gap_start: int,
        tail_start: int | None,
        protected: Sequence[str],
        through_seq: int | None,
        unconsumed_run_id: uuid.UUID | None,
        consumed_event_seq: int,
    ) -> int | None:
        if tail_start is None or tail_start <= gap_start:
            return 0
        conditions = self._history_conditions(
            tenant_id,
            conversation_id,
            covered=gap_start,
            through_seq=through_seq,
            unconsumed_run_id=unconsumed_run_id,
            consumed_event_seq=consumed_event_seq,
        )
        statement = select(sa.func.count()).select_from(CanonicalEvent).where(
            *conditions,
            CanonicalEvent.seq > gap_start,
            CanonicalEvent.seq < tail_start,
            ~CanonicalEvent.id.in_(protected) if protected else true(),
        )
        try:
            return int((await session.scalar(statement)) or 0)
        except SQLAlchemyError as exc:
            from muad_logging import get_logger

            get_logger(__name__).warning(
                "context_earlier_records_failed",
                extra={"conversation_id": str(conversation_id), "error_type": type(exc).__name__},
            )
            return None

    @staticmethod
    def _history_conditions(
        tenant_id: str,
        conversation_id: uuid.UUID,
        *,
        covered: int,
        through_seq: int | None,
        unconsumed_run_id: uuid.UUID | None,
        consumed_event_seq: int,
    ) -> tuple[Any, ...]:
        conditions: list[Any] = [
            CanonicalEvent.tenant_id == tenant_id,
            CanonicalEvent.conversation_id == conversation_id,
            CanonicalEvent.event_type.in_(HISTORY_EVENT_TYPES),
            CanonicalEvent.is_deleted.is_(False),
            CanonicalEvent.seq > covered,
            CanonicalEvent.seq <= through_seq if through_seq is not None else true(),
        ]
        if unconsumed_run_id is not None:
            conditions.append(
                ~(
                    (CanonicalEvent.run_id == unconsumed_run_id)
                    & CanonicalEvent.event_type.in_(
                        ("TOOL_TASK_ACCEPTED", "BACKGROUND_RESULT", "TOOL_SUBMISSION_FAILED")
                    )
                    & (CanonicalEvent.seq > consumed_event_seq)
                )
            )
        return tuple(conditions)

    async def _artifact_references(
        self,
        session: Any,
        tenant_id: str,
        artifact_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, dict[str, JsonValue]]:
        """外置产物的完整引用（含落库时的头尾预览，**不再二次截断**）。

        重建要还原"当时发出去的那份"引用 JSON（ADR-05），而那份预览是 `preview_head_tail`
        按配置裁出来的；在这里再截一刀就会与实发不等。
        """
        if not artifact_ids:
            return {}
        rows = (
            (
                await session.execute(
                    select(Artifact).where(
                        Artifact.id.in_(artifact_ids),
                        Artifact.tenant_id == tenant_id,
                        Artifact.is_deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            row.id: {
                "artifact_id": str(row.id),
                "size": row.size,
                "checksum": row.checksum,
                "preview": row.preview_text or "",
            }
            for row in rows
        }


def _is_current_input(event: CanonicalEvent, current_message_id: str | None) -> bool:
    if current_message_id is None:
        return False
    payload = event.payload_json or {}
    return payload.get("message_id") == current_message_id


def _estimate_events_tokens(events: Sequence[CanonicalEvent]) -> int:
    return sum(
        len(json.dumps(event.payload_json or {}, ensure_ascii=False).encode("utf-8"))
        + MESSAGE_PROTOCOL_OVERHEAD_BYTES
        for event in events
    )


def _merge_events(*groups: Sequence[CanonicalEvent]) -> list[CanonicalEvent]:
    unique: dict[uuid.UUID, CanonicalEvent] = {}
    for events in groups:
        for event in events:
            unique[event.id] = event
    return sorted(unique.values(), key=lambda event: event.seq)


def _active_skill_context(
    events: Sequence[CanonicalEvent], references: Mapping[uuid.UUID, dict[str, JsonValue]]
) -> ActiveSkillContext:
    fold = fold_events([
        {"event_type": event.event_type, "payload": event.payload_json or {}}
        for event in events
    ])
    latest: dict[str, str] = {}
    for call in fold.calls:
        key = call.declaration.arguments.get("skill_key")
        if (call.declaration.tool_name == "load_skill" and isinstance(key, str) and key
                and call.completed is not None and call.completed.status == "OK"):
            latest[key] = call.declaration.call_id
    selected = set(latest.values())
    results = _paired_tool_results(list(events), references)
    contents = {
        call.declaration.call_id: call.completed.content
        for call in fold.calls
        if call.declaration.call_id in selected and call.completed is not None
    }
    for turn_results in results.values():
        for call_id, message in turn_results.items():
            if call_id in contents:
                turn_results[call_id] = replace(message, content=contents[call_id])
    groups = tuple(
        _tool_exchange(event, results.get(event.seq, {}))
        for event in events
        if event.event_type == "ASSISTANT_TURN" and any(
            call.id in selected for call in _tool_calls((event.payload_json or {}).get("tool_calls"))
        )
    )
    return ActiveSkillContext(tuple(latest), tuple(group for group in groups if group))


def _paired_tool_results(
    events: list[CanonicalEvent], references: Mapping[uuid.UUID, dict[str, JsonValue]]
) -> dict[int, dict[str, ModelMessage]]:
    """按 Run 与声明轮次配对；跨轮可复用调用 ID，孤儿和重复结果不回放。"""
    pending: dict[tuple[uuid.UUID | None, str], int] = {}
    results: dict[int, dict[str, ModelMessage]] = {}
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "ASSISTANT_TURN":
            for call in _tool_calls(payload.get("tool_calls")):
                pending[event.run_id, call.id] = event.seq
        elif event.event_type == "TOOL_CALL":
            call_id = payload.get("tool_call_id")
            if not isinstance(call_id, str):
                continue
            turn_seq = pending.pop((event.run_id, call_id), None)
            if turn_seq is None:
                continue
            artifact_ref = references.get(event.artifact_id) if event.artifact_id else None
            results.setdefault(turn_seq, {})[call_id] = ModelMessage(
                role=ModelRole.TOOL,
                content=_tool_message_content(payload, artifact_ref),
                tool_call_id=call_id,
                source_event_id=str(event.id),
                source_event_seq=event.seq,
                origin="TOOL",
            )
    return results


def _history_entries(
    events: list[CanonicalEvent], results: Mapping[int, dict[str, ModelMessage]]
) -> list[tuple[int, ModelMessage]]:
    entries: list[tuple[int, ModelMessage]] = []
    round_no = 0
    previous_event_type: str | None = None
    for event in events:
        if event.event_type == "ASSISTANT_TURN":
            exchange = _tool_exchange(event, results.get(event.seq, {}))
            if exchange:
                round_no += 1
                entries.extend((round_no, message) for message in exchange)
        elif event.event_type == "MEMORY_CONFIRMATION_RESOLVED":
            note = confirmation_context_note(event.payload_json or {})
            if note is not None:
                _append_confirmation(entries, note, previous_event_type)
        else:
            message = _ordinary_message(event)
            if message is not None:
                entries.append((0, message))
        previous_event_type = event.event_type
    return entries


def _tool_exchange(
    event: CanonicalEvent, results: Mapping[str, ModelMessage]
) -> tuple[ModelMessage, ...]:
    """完整结果紧跟声明展开，交错的入站/确认事件不能拆开工具往返。"""
    payload = event.payload_json or {}
    calls = tuple(call for call in _tool_calls(payload.get("tool_calls")) if call.id in results)
    if not calls:
        return ()
    reasoning = payload.get("reasoning_content")
    assistant = ModelMessage(
        role=ModelRole.ASSISTANT,
        content=str(payload.get("text") or ""),
        tool_calls=calls,
        reasoning_content=reasoning if isinstance(reasoning, str) and reasoning else None,
        source_event_id=str(event.id),
        source_event_seq=event.seq,
        origin="ASSISTANT",
    )
    return (assistant, *(results[call.id] for call in calls))


def _ordinary_message(event: CanonicalEvent) -> ModelMessage | None:
    payload = event.payload_json or {}
    if event.event_type == "USER_MESSAGE":
        # 历史附件保留文本引用；模型需要正文时通过附件工具按需读取。
        reference = render_attachment_reference(attachments_from_payload(payload.get("attachments")))
        text = str(payload.get("text", ""))
        message_id = payload.get("message_id")
        return ModelMessage(
            role=ModelRole.USER,
            content="\n".join(part for part in (text, reference) if part),
            message_id=message_id if isinstance(message_id, str) and message_id else str(event.id),
            source_event_id=str(event.id),
            source_event_seq=event.seq,
            origin="USER_INPUT",
        )
    if event.event_type == "ASSISTANT_MESSAGE":
        return ModelMessage(
            role=ModelRole.ASSISTANT,
            content=str(payload.get("text", "")),
            source_event_id=str(event.id),
            source_event_seq=event.seq,
            origin="ASSISTANT",
        )
    if event.event_type in ("TOOL_TASK_ACCEPTED", "BACKGROUND_RESULT", "TOOL_SUBMISSION_FAILED"):
        from .async_tools.materialization import external_data

        return ModelMessage(
            role=ModelRole.USER,
            content=external_data(event.event_type, payload),
            source_event_id=str(event.id),
            source_event_seq=event.seq,
            origin="BACKGROUND_RESULT",
        )
    return None


def _append_confirmation(
    entries: list[tuple[int, ModelMessage]], note: str, previous_event_type: str | None
) -> None:
    """确认是引用事实，紧邻真实用户消息时合并，使请求仍以 user 结尾。"""
    if (
        previous_event_type == "USER_MESSAGE"
        and entries
        and entries[-1][0] == 0
        and entries[-1][1].role is ModelRole.USER
    ):
        content = text_of(entries[-1][1].content)
        entries[-1] = (
            0,
            replace(entries[-1][1], content=f"{content}\n{note}" if content else note),
        )
    else:
        entries.append((0, ModelMessage(role=ModelRole.USER, content=note)))


def _tool_calls(raw: JsonValue) -> tuple[ModelToolCall, ...]:
    """把事件里的 tool_calls JSON 还原成契约对象；脏项跳过，不让一条坏数据崩掉整段历史。"""
    if not isinstance(raw, list):
        return ()
    calls: list[ModelToolCall] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        call_id, name = item.get("id"), item.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str) or call_id in seen:
            continue
        seen.add(call_id)
        arguments = item.get("arguments")
        calls.append(
            ModelToolCall(
                id=call_id,
                name=name,
                arguments=dict(arguments) if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def _tool_message_content(payload: Mapping[str, JsonValue], artifact_ref: dict[str, JsonValue] | None) -> str:
    """TOOL 消息的正文：外置过 ⇒ 当时那条引用 JSON；没外置 ⇒ 落库的有界预览；都没有才回落工具名。"""
    if artifact_ref is not None:
        return reference_payload(artifact_ref)
    preview = payload.get("preview")
    if isinstance(preview, str) and preview:
        return preview
    return f"[tool:{payload.get('tool_name')}]"


__all__ = [
    "ActiveSkillContext",
    "DbBackedContextBuilder",
    "HISTORY_EVENT_TYPES",
    "LoadedHistory",
]
