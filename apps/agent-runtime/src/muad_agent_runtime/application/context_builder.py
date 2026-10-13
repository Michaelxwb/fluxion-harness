"""Rebuild canonical history and summaries; memory is loaded at the request boundary."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from muad_agent_core.context.summary import summary_from_payload, summary_message
from muad_agent_core.model.provider import ModelMessage, ModelRole, ModelToolCall
from muad_contracts.platform_settings import default_compaction_settings
from sqlalchemy import select, true

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import Artifact, CanonicalEvent, Conversation
from .attachments.inbound import attachments_from_payload, render_attachment_reference
from .attachments.tool_results import reference_payload
from .context_events import covered_up_to, latest_summary

HISTORY_EVENT_TYPES = (
    "USER_MESSAGE",
    "ASSISTANT_MESSAGE",
    "TOOL_CALL",
    "ASSISTANT_TURN",
    "TOOL_TASK_ACCEPTED",
    "BACKGROUND_RESULT",
    "TOOL_SUBMISSION_FAILED",
)

# 历史里保留的 tool 回合数上限（一个回合 = assistant(tool_calls) + 它的若干 tool 结果）。
# 依据：思考模式的 `reasoning_content` 实测约 3KB/轮（单轮 2840 字符），全量回放会让请求体
# 随会话线性膨胀，而消息条数预算（`BudgetPolicy.max_messages`）管不住字节数。
# 超出的回合**整回合丢弃**（绝不半截保留，否则会造出孤儿 tool 消息 ⇒ 供应商直接拒绝）。
MAX_HISTORY_TOOL_ROUNDS = 6


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    # `max_messages` 是**取事件的查询守卫**（`_recent_events` 按它 ×4 限流），不是请求预算本身：
    # 请求里的条数由压缩层按冻结配置的 `history_budget_messages` 兜底，两边用的是同一个值。
    # 默认值**派生自 compaction schema**（不写第二套字面量）：两者必须同源，否则守卫与裁剪会漂移。
    max_messages: int = default_compaction_settings().history_budget_messages


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
        budget_messages: int | None = None,
        through_seq: int | None = None,
        unconsumed_run_id: uuid.UUID | None = None,
        consumed_event_seq: int = 0,
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
            covered = covered_up_to(summary_row.payload_json if summary_row is not None else None)
            events = await self._recent_events(
                session,
                tenant_id,
                conversation_id,
                budget,
                after_seq=covered,
                through_seq=through_seq,
                unconsumed_run_id=unconsumed_run_id,
                consumed_event_seq=consumed_event_seq,
            )
            history = await self._to_messages(session, tenant_id, events)
        summaries: list[ModelMessage] = []
        if summary_row is not None:
            fields = summary_from_payload((summary_row.payload_json or {}).get("summary"))
            if fields is not None:
                summaries.append(summary_message(fields))
        return (*summaries, *history)

    async def _recent_events(
        self,
        session: Any,
        tenant_id: str,
        conversation_id: uuid.UUID,
        budget: int,
        *,
        after_seq: int = 0,
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
                        CanonicalEvent.seq > after_seq,
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
        session: Any,
        tenant_id: str,
        events: list[CanonicalEvent],
    ) -> list[ModelMessage]:
        references = await self._artifact_references(
            session, tenant_id, [event.artifact_id for event in events if event.artifact_id]
        )

        # 只有被 `ASSISTANT_TURN` **声明过**的工具调用才进历史。缺了这层校验，历史里会出现
        # 「孤儿 tool 消息」（前面没有任何 assistant `tool_calls`），供应商会直接拒绝**整个**
        # 请求 —— 实测：`Messages with role 'tool' must be a response to a preceding message
        # with 'tool_calls'`。本事件类型上线前的存量会话全是这种孤儿，故一律跳过（丢那几轮
        # 的工具上下文，换取会话立刻可用）。
        #
        # `answered` 是这层校验的**反向**：assistant 声明了、但库里没有对应 `TOOL_CALL` 行的
        # 调用不能回放。中断/取消打断一个多工具回合、或工具预算耗尽时，声明已经落库而结果没有，
        # 整份回放就得到「声明 2 个、只答 1 个」的请求 —— 同一类形状问题，供应商同样会拒整个请求
        # （2026-10-06 review）。声明与结果必须成对：成不了对的那条声明整条丢掉，与
        # `compactor.split_groups` 的孤儿口径同源。
        declared: set[str] = set()
        answered: set[str] = set()
        for event in events:
            payload = event.payload_json or {}
            if event.event_type == "ASSISTANT_TURN":
                for call in payload.get("tool_calls") or []:
                    if isinstance(call, dict) and isinstance(call.get("id"), str):
                        declared.add(call["id"])
            elif event.event_type == "TOOL_CALL":
                call_id = payload.get("tool_call_id")
                if isinstance(call_id, str):
                    answered.add(call_id)

        # round_no 标记「属于哪个 tool 回合」；0 = 与工具无关的普通消息（永远保留）
        entries: list[tuple[int, ModelMessage]] = []
        round_no = 0
        awaiting: set[str] = set()
        external_pending: list[ModelMessage] = []
        for event in events:
            payload = event.payload_json or {}
            if event.event_type == "USER_MESSAGE":
                # 历史轮次的附件**只留文本引用**（设计 AD-3-B）：全量重发旧图会让请求体随
                # 轮次线性膨胀，而多数轮次根本用不到它。模型要重看得主动调附件工具。
                reference = render_attachment_reference(attachments_from_payload(payload.get("attachments")))
                text = str(payload.get("text", ""))
                entries.append(
                    (
                        0,
                        ModelMessage(
                            role=ModelRole.USER,
                            content="\n".join(part for part in (text, reference) if part),
                        ),
                    )
                )
            elif event.event_type in ("TOOL_TASK_ACCEPTED", "BACKGROUND_RESULT", "TOOL_SUBMISSION_FAILED"):
                from .async_tools.materialization import external_data

                message = ModelMessage(role=ModelRole.USER, content=external_data(event.event_type, payload))
                if awaiting:
                    external_pending.append(message)
                else:
                    entries.append((0, message))
            elif event.event_type == "ASSISTANT_MESSAGE":
                entries.append(
                    (0, ModelMessage(role=ModelRole.ASSISTANT, content=str(payload.get("text", ""))))
                )
            elif event.event_type == "ASSISTANT_TURN":
                # 只回放**真有结果落库**的调用（见上面 `answered` 的注释）：声明与 tool 结果
                # 成不了对的那条声明整条丢弃，否则供应商会拒掉整个请求。
                calls = tuple(call for call in _tool_calls(payload.get("tool_calls")) if call.id in answered)
                if not calls:
                    continue
                awaiting = {call.id for call in calls}
                round_no += 1
                reasoning = payload.get("reasoning_content")
                entries.append(
                    (
                        round_no,
                        ModelMessage(
                            role=ModelRole.ASSISTANT,
                            content=str(payload.get("text") or ""),
                            tool_calls=calls,
                            reasoning_content=(
                                reasoning if isinstance(reasoning, str) and reasoning else None
                            ),
                        ),
                    )
                )
            elif event.event_type == "TOOL_CALL":
                # 键名以事件 payload 为准：`tool_call_id` / `tool_name`（曾误读 `call_id` /
                # `tool`，导致 tool 消息的 id 恒为 None —— 那正是「tool 消息配不上 assistant
                # tool_calls」这个历史故障的根因；`tool` 那处还会把工具名显示成 None）。
                call_id = payload.get("tool_call_id")
                if not isinstance(call_id, str) or call_id not in declared:
                    continue  # 孤儿 tool 消息：整条请求会因此被拒
                # 外置过的结果按**当时发出去的那条引用 JSON** 还原（ADR-05）：模型据此既能
                # 看到预览，也拿得到 `artifact_id` 去 `read_attachment`。没外置的按落库时那份
                # **有界预览**还原（2026-10-06 review）：不落预览，这条结果过了一个 Run 就只剩
                # 工具名，任务 id / 查询结论 / 短正文全部找不回来。
                artifact_ref = references.get(event.artifact_id) if event.artifact_id else None
                entries.append(
                    (
                        round_no,
                        ModelMessage(
                            role=ModelRole.TOOL,
                            content=_tool_message_content(payload, artifact_ref),
                            tool_call_id=call_id,
                        ),
                    )
                )
                awaiting.discard(call_id)
                if not awaiting:
                    entries.extend((0, message) for message in external_pending)
                    external_pending.clear()

        entries.extend((0, message) for message in external_pending)
        keep = _kept_tool_rounds([value for value, _ in entries])
        return [message for value, message in entries if value == 0 or value in keep]

    async def _artifact_references(
        self,
        session: Any,
        tenant_id: str,
        artifact_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, dict[str, Any]]:
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


def _tool_calls(raw: Any) -> tuple[ModelToolCall, ...]:
    """把事件里的 tool_calls JSON 还原成契约对象；脏项跳过，不让一条坏数据崩掉整段历史。"""
    if not isinstance(raw, list):
        return ()
    calls: list[ModelToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        call_id, name = item.get("id"), item.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str):
            continue
        arguments = item.get("arguments")
        calls.append(
            ModelToolCall(
                id=call_id,
                name=name,
                arguments=dict(arguments) if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def _tool_message_content(payload: Mapping[str, Any], artifact_ref: dict[str, Any] | None) -> str:
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


def _kept_tool_rounds(present: list[int]) -> set[int]:
    """保留最近 `MAX_HISTORY_TOOL_ROUNDS` 个 tool 回合；**整回合**保留或丢弃，绝不半截。"""
    rounds = sorted({value for value in present if value > 0})
    return set(rounds[-MAX_HISTORY_TOOL_ROUNDS:])
