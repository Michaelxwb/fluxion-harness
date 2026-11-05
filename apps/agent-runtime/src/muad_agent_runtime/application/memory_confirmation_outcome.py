"""记忆确认结果的单一文案来源（公开投影与模型上下文共用）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: `outcome` → 用户可见结果。新增结果时必须同时给出文案：未知结果不猜测（调用方显式跳过）。
CONFIRMATION_OUTCOME_TEXT: dict[str, str] = {
    "CONFIRMED": "记忆已保存。",
    "REJECTED": "已拒绝保存该记忆。",
    "STALE": "原提议已失效，未保存记忆；请重新展示候选后确认。",
    "EXPIRED": "记忆提议已过期，未保存；可以继续对话或重新提出记忆请求。",
}


def confirmation_result_text(outcome: object) -> str | None:
    """确认结果的安全文案；未知/坏值返回 None（公开投影跳过，不伪造结果）。"""
    return CONFIRMATION_OUTCOME_TEXT.get(outcome) if isinstance(outcome, str) else None


def confirmation_context_note(payload: Mapping[str, Any]) -> str | None:
    """模型上下文里的确认结果说明。

    确认结果是**系统事实**，不是助手说过的话：模型历史里必须以引用块形态追加到用户确认之后，
    不能合成一条 assistant 消息（供应商会把它当成预填答案，最后一条消息因此不是 user）。
    """
    outcome = payload.get("outcome")
    text = confirmation_result_text(outcome)
    if text is None:
        return None
    details = [
        f"{name}={payload[name]}"
        for name in ("memory_key", "version")
        if payload.get(name) is not None
    ]
    suffix = f" ({', '.join(details)})" if details else ""
    return (
        f"[Memory confirmation result: {outcome}{suffix}; reference only, "
        f"not a new user request] {text}"
    )
