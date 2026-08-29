"""Chat Workspace 视图层纯函数（Phase 5 TASK-014 / review P2 拆分）。

`workspace_app.py` 超 500 行预算（质量硬约束：单文件按职责拆分）——把无 IO 的
投影/映射/推导函数收拢到本模块：workflow_run 投影 → 任务视图、session 聚合 →
chat 视图、profile/memory wire 映射、human_task 挂起推导。全部纯函数（输入行/
定义/聚合 dict，输出 wire dict），便于单测与复用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fluxion.errors.console import ConsoleValidationError

_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
_RUN_STATUS_SUMMARY = {
    "running": "运行中",
    "succeeded": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def workflow_task(row: Any, definition: dict[str, object] | None) -> dict[str, object]:
    """workflow_run 投影行 → 任务视图（progress = 已完成节点/定义节点数）。"""
    status = str(row["status"])
    progress = 0
    if status in _TERMINAL_RUN_STATUSES:
        progress = 100
    elif definition is not None:
        steps = definition_steps(definition)
        done = sum(
            1 for node_id in completed_node_ids(row) if has_step(steps, node_id)
        )
        if steps:
            progress = int(100 * done / len(steps))
    title = str(row["workflow_id"])
    if definition is not None and definition.get("name"):
        title = str(definition["name"])
    return {
        "task_id": str(row["run_id"]),
        "title": title,
        "kind": "workflow",
        "status": status,
        "progress": progress,
        "started_at": iso(row["created_at"]),
        "updated_at": iso(row["updated_at"]),
    }


def chat_task(session: dict[str, object]) -> dict[str, object]:
    """session 聚合（首末消息 + 起止时间）→ chat 任务视图。"""
    return {
        "task_id": str(session["session_id"]),
        "title": truncate(session["first_content"]),
        "kind": "chat",
        "status": "succeeded",
        "progress": 100,
        "started_at": iso(session["first_at"]),
        "updated_at": iso(session["last_at"]),
    }


def run_status_summary(status: str) -> str:
    """run 状态 → 历史时间线摘要文案。"""
    return _RUN_STATUS_SUMMARY.get(status, status)


def completed_node_ids(row: Any) -> set[str]:
    """node_states 中已落定（succeeded/skipped/failed）的节点 ID 集合。

    human_task 挂起中的节点不在 node_states（波次完成后才写）——缺位即待审批。
    """
    node_states = row["node_states"] or {}
    if not isinstance(node_states, dict):
        return set()
    return {
        node_id
        for node_id, state in node_states.items()
        if isinstance(state, dict) and state.get("status") in ("succeeded", "skipped", "failed")
    }


def definition_steps(definition: dict[str, object]) -> list[dict[str, object]]:
    """workflow 定义 steps（spec_json 为 dict[str, object]，过滤出 dict 节点）。"""
    steps = definition.get("steps")
    if not isinstance(steps, list):
        return []
    return [node for node in steps if isinstance(node, dict)]


def find_human_task_node(
    definition: dict[str, object], node_id: str
) -> dict[str, object] | None:
    for node in definition_steps(definition):
        if node.get("type") == "human_task" and str(node.get("id")) == node_id:
            return node
    return None


def has_step(steps: list[dict[str, object]], node_id: str) -> bool:
    return any(str(node.get("id")) == node_id for node in steps)


def entry_id(memory_id: str) -> int:
    try:
        return int(memory_id)
    except ValueError as error:
        raise ConsoleValidationError(f"memory_id 无效: {memory_id}") from error


def profile_wire(user_id: str, profile_json: dict[str, object]) -> dict[str, object]:
    wire: dict[str, object] = {
        "platform_user_id": user_id,
        "display_name": str(profile_json.get("display_name", "")),
    }
    if isinstance(profile_json.get("timezone"), str):
        wire["timezone"] = profile_json["timezone"]
    if isinstance(profile_json.get("language"), str):
        wire["locale"] = profile_json["language"]
    return wire


def memory_wire(entry: Any) -> dict[str, object]:
    return {
        "memory_id": str(entry.id),
        "content": entry.content,
        "source": entry.memory_type.value,
        "created_at": iso(entry.created_at),
        "updated_at": iso(entry.updated_at),
    }


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def truncate(content: object, *, limit: int = 48) -> str:
    text = str(content)
    return text if len(text) <= limit else f"{text[:limit]}…"
