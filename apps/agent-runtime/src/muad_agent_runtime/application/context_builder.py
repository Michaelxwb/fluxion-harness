"""Rebuild canonical history and summaries; memory is loaded at the request boundary."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import sqlalchemy as sa
from muad_agent_core.agent.recovery import fold_events
from muad_agent_core.context.compactor import Group, split_groups, split_protected_prefix
from muad_agent_core.context.summary import summary_from_payload, summary_message
from muad_agent_core.model.provider import ModelMessage, ModelRole, ModelToolCall, text_of
from pydantic import JsonValue
from sqlalchemy import select, true
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


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    # 过渡期读取守卫；TASK-002 改为按 history_budget_tokens 分页读取。
    max_messages: int = 40


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
    def __init__(
        self,
        *,
        session_factory: SessionFactoryProvider,
        budget: BudgetPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._budget = budget or BudgetPolicy()

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
        budget_messages: int | None = None,
        through_seq: int | None = None,
        unconsumed_run_id: uuid.UUID | None = None,
        consumed_event_seq: int = 0,
        max_history_tool_rounds: int | None = None,
    ) -> tuple[ModelMessage, ...]:
        """Read owned canonical history; memory and compression belong to the derived request."""
        budget = budget_messages or self._budget.max_messages
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
                    return ()
            summary_row = await latest_summary(session, tenant_id, conversation_id)
            summary_payload = summary_row.payload_json if summary_row is not None else None
            covered = covered_up_to(summary_payload)
            protected = protected_event_ids(summary_payload)
            events = await self._recent_events(
                session,
                tenant_id,
                conversation_id,
                budget,
                after_seq=covered,
                protected_event_ids=protected,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            )
            history = await self._to_messages(session, tenant_id, events, max_history_tool_rounds)
        summaries: list[ModelMessage] = []
        if summary_row is not None:
            fields = summary_from_payload((summary_row.payload_json or {}).get("summary"))
            if fields is not None:
                summaries.append(
                    replace(
                        summary_message(fields),
                        source_event_id=str(summary_row.id),
                        source_event_seq=summary_row.seq,
                        origin="SUMMARY",
                    )
                )
        return (*summaries, *history)

    async def _recent_events(
        self,
        session: Any,
        tenant_id: str,
        conversation_id: uuid.UUID,
        budget: int,
        *,
        after_seq: int = 0,
        protected_event_ids: Sequence[str] = (),
        through_seq: int | None = None,
        unconsumed_run_id: uuid.UUID | None = None,
        consumed_event_seq: int = 0,
    ) -> list[CanonicalEvent]:
        """取最近 `budget * 4` 条业务事件（倒序取再反转），保证长会话保留最新轮次。

        `after_seq` 是摘要覆盖边界：被摘要覆盖的原始事件不再进入装配（它们的历史由摘要承载）。
        一轮工具往返会落好几条事件（assistant 回合 + 每个工具结果），故守卫是条数的 4 倍——它只
        保证"取够了料"，裁多少由压缩层决定。
        """
        rows = (
            (
                await session.execute(
                    select(CanonicalEvent)
                    .where(
                        CanonicalEvent.tenant_id == tenant_id,
                        CanonicalEvent.conversation_id == conversation_id,
                        CanonicalEvent.event_type.in_(HISTORY_EVENT_TYPES),
                        sa.or_(
                            CanonicalEvent.seq > after_seq,
                            CanonicalEvent.id.in_(protected_event_ids),
                        )
                        if protected_event_ids
                        else CanonicalEvent.seq > after_seq,
                        CanonicalEvent.is_deleted.is_(False),
                        CanonicalEvent.seq <= through_seq if through_seq is not None else true(),
                        ~(
                            (CanonicalEvent.run_id == unconsumed_run_id)
                            & CanonicalEvent.event_type.in_(
                                ("TOOL_TASK_ACCEPTED", "BACKGROUND_RESULT", "TOOL_SUBMISSION_FAILED")
                            )
                            & (CanonicalEvent.seq > consumed_event_seq)
                        )
                        if unconsumed_run_id is not None
                        else true(),
                    )
                    .order_by(CanonicalEvent.seq.desc())
                    .limit(max(budget, 1) * 4)
                )
            )
            .scalars()
            .all()
        )
        return list(reversed(rows))

    async def _to_messages(
        self,
        session: AsyncSession,
        tenant_id: str,
        events: list[CanonicalEvent],
        max_history_tool_rounds: int | None,
    ) -> list[ModelMessage]:
        references = await self._artifact_references(
            session, tenant_id, [event.artifact_id for event in events if event.artifact_id]
        )
        entries = _history_entries(events, _paired_tool_results(events, references))
        keep = (
            _kept_tool_rounds([value for value, _ in entries], max_history_tool_rounds)
            if max_history_tool_rounds is not None
            else None
        )
        return [message for value, message in entries if value == 0 or keep is None or value in keep]

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
    """TOOL 消息的正文：外置过 ⇒ 当时那条引用 JSON；没外置 ⇒ 落库的有界预览；都没有才回落工具名。

    第三条兜底只该出现在**老数据**（预览上线前落的行）与工具自己写脏引用的场景：那时
    `artifact_id` 解析得出、Artifact 行却不存在，既没有引用也没有预览。
    """
    if artifact_ref is not None:
        return reference_payload(artifact_ref)
    preview = payload.get("preview")
    if isinstance(preview, str) and preview:
        return preview
    return f"[tool:{payload.get('tool_name')}]"


def _kept_tool_rounds(present: list[int], limit: int) -> set[int]:
    """Apply the frozen round budget to complete tool exchanges; zero omits them."""
    if not 0 <= limit <= 50:
        raise ValueError("invalid frozen history tool round budget")
    if limit == 0:
        return set()
    rounds = sorted({value for value in present if value > 0})
    return set(rounds[-limit:])
