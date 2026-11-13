"""Task family derivation: first successfully executed skill wins, else the agent."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent, RunRecord

SKILL_TOOLS = frozenset({"execute", "execute_skill", "run_skill_script"})
ASSISTANT_TURN = "ASSISTANT_TURN"
TOOL_CALL = "TOOL_CALL"
SUCCESS_STATUS = "OK"


def skill_key_for_call(name: object, arguments: object) -> str | None:
    """成功 skill 调用的族键；执行层切换与 Run 族判定共用这一条规则。"""
    if name not in SKILL_TOOLS or not isinstance(arguments, Mapping):
        return None
    if name == "execute" and arguments.get("target") != "skill":
        return None
    key = arguments.get("skill_key")
    return key.strip() if isinstance(key, str) and key.strip() else None


def derive_task_family(events: Iterable[Mapping[str, Any]], *, agent_id: str) -> str:
    """按事件顺序取首个成功执行的 skill key；没有 skill 时回退 agent 族。

    `TOOL_CALL` 事件本身不带参数（只有 tool_call_id/status），skill_key 从同一 Run 的
    `ASSISTANT_TURN.tool_calls[].arguments` 关联；失败调用不参与族判定。
    """
    calls: dict[str, Mapping[str, Any]] = {}
    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event_type == ASSISTANT_TURN:
            for call in payload.get("tool_calls") or ():
                if isinstance(call, Mapping) and isinstance(call.get("id"), str):
                    calls[call["id"]] = call
        elif event_type == TOOL_CALL:
            call_id = payload.get("tool_call_id")
            if payload.get("status") != SUCCESS_STATUS or not isinstance(call_id, str):
                continue
            call = calls.get(call_id)
            if call is None:
                continue
            key = skill_key_for_call(call.get("name"), call.get("arguments"))
            if key is not None:
                return f"skill:{key}"
    return f"agent:{agent_id}"


async def read_run_task_family(session: AsyncSession, run: RunRecord) -> str:
    """Run 完成时从真实事件派生任务族（纯函数 + 一次有序读取）。"""
    rows = await session.execute(
        sa.select(CanonicalEvent.event_type, CanonicalEvent.payload_json)
        .where(
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.run_id == run.id,
            CanonicalEvent.event_type.in_((ASSISTANT_TURN, TOOL_CALL)),
            CanonicalEvent.is_deleted.is_(False),
        )
        .order_by(CanonicalEvent.seq)
    )
    events = ({"event_type": event_type, "payload": payload} for event_type, payload in rows.all())
    return derive_task_family(events, agent_id=str(run.agent_id))
