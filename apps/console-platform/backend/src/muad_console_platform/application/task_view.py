"""Worker Execution 真值 → Console Task 出参的投影（design §3.4.6 Console API / §3.4.8）。

Task 的状态、等待类型、时间线都是 Worker 的事实；Console 只做三件事：

- 把 ISO 时刻换成带 offset 的展示格式；
- 把执行快照压成**白名单摘要**（`skills[].storage_key` 之类的内部存储键不外泄）；
- 从执行时间线里的 `WAITING_HUMAN` 事件还原 Human 摘要（问题/截止时间/是否仍在等待）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schedule_view import console_time

#: 列表/详情共有的时刻字段。
_TASK_TIME_FIELDS = (
    "scheduled_at",
    "deadline_at",
    "not_before",
    "create_time",
    "update_time",
    "started_at",
    "finished_at",
)

#: 只有详情才出现的租约时刻。
_DETAIL_TIME_FIELDS = _TASK_TIME_FIELDS + ("lease_until", "heartbeat_at")


def _with_times(payload: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = dict(payload)
    for field in fields:
        if field in result:
            result[field] = console_time(result[field])
    return result


def task_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """列表项：保留 Worker 的 `wait_kind`/`task_type`，只换时刻格式。"""
    return _with_times(item, _TASK_TIME_FIELDS)


def _snapshot_summary(detail: Mapping[str, Any]) -> dict[str, Any]:
    """快照白名单：schema/hash + 模式、tool allowlist、记忆模式与预算；原样丢弃其余字段。"""
    raw = detail.get("execution_snapshot")
    snapshot = raw if isinstance(raw, Mapping) else {}
    memory = snapshot.get("memory")
    budget = snapshot.get("budget")
    return {
        "schema_version": detail.get("execution_snapshot_schema_version"),
        "hash": detail.get("snapshot_hash"),
        "mode": snapshot.get("mode"),
        "allowlist": list(snapshot.get("allowlist") or []),
        "memory_mode": memory.get("mode") if isinstance(memory, Mapping) else None,
        "budget": dict(budget) if isinstance(budget, Mapping) else None,
    }


def _human_summary(detail: Mapping[str, Any]) -> dict[str, Any] | None:
    """最近一次 Human 提问；`open` 以 Execution 状态为准（实体行才是状态权威）。"""
    latest: Mapping[str, Any] | None = None
    for event in _timeline(detail):
        if event.get("event_type") == "WAITING_HUMAN":
            latest = event
    if latest is None:
        return None
    payload = latest.get("payload")
    values = payload if isinstance(payload, Mapping) else {}
    return {
        "interaction_id": values.get("interaction_id"),
        "question": values.get("question"),
        "deadline_at": console_time(values.get("deadline_at")),
        "open": detail.get("status") == "WAITING_HUMAN",
    }


def _timeline(detail: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    events = detail.get("timeline") or []
    return [event for event in events if isinstance(event, Mapping)]


def _timeline_item(event: Mapping[str, Any]) -> dict[str, Any]:
    return _with_times(event, ("create_time",))


def task_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """详情 = 摘要 + 时间线 + 子任务 + Snapshot 摘要 + Human 摘要。"""
    payload = _with_times(detail, _DETAIL_TIME_FIELDS)
    # 原始快照含 `skills[].storage_key` 等内部存储键：Console 只拿白名单摘要。
    payload.pop("execution_snapshot", None)
    payload["snapshot"] = _snapshot_summary(detail)
    payload["human"] = _human_summary(detail)
    payload["timeline"] = [_timeline_item(event) for event in _timeline(detail)]
    payload["children"] = [
        _with_times(child, ("create_time",))
        for child in detail.get("children") or []
        if isinstance(child, Mapping)
    ]
    return payload
