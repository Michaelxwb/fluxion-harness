"""Worker Schedule 真值 → Console 出参的投影（design §3.4.6 Console API / §3.4.8）。

Console 只做两件事：把 Worker 的 ISO 时刻换成带 offset 的展示格式（`format_console_time`），
以及剔除 Console 不该暴露的字段。字段集合、状态与判定全部来自 Worker，不在这里复制一份
状态机或结果库。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode

from .audit_query_service import format_console_time


def console_time(value: Any) -> str | None:
    """Worker ISO 时刻 → Console 展示格式；无法解析说明上游契约被破坏，显式报错。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    return format_console_time(moment)


def _with_times(
    payload: Mapping[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    result = dict(payload)
    for field in fields:
        if field in result:
            result[field] = console_time(result[field])
    return result


def _last_fire(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return _with_times(value, ("scheduled_at",))


def schedule_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """列表项：新状态/模式/时区/版本/下次触发/最近 Fire/待处理标记 + Console 时刻。"""
    payload = _with_times(item, ("next_fire_at",))
    payload["last_fire"] = _last_fire(item.get("last_fire"))
    return payload


#: Worker 的冻结目标只用于执行；Console 只展示投递意图，不暴露内部路由主键/外部会话 ID。
_INTERNAL_DELIVERY_FIELDS = ("delivery_route_id", "delivery_route")


def schedule_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """详情 = 列表项 + 当前 Revision/依赖/投递意图/策略/预算/结束原因。"""
    payload = schedule_item(detail)
    for field in _INTERNAL_DELIVERY_FIELDS:
        payload.pop(field, None)
    return payload


def fire_history(page: Mapping[str, Any]) -> dict[str, Any]:
    """Fire 历史：真实 Fire 与 DST 调度事件分开返回，不把事件伪造成 Fire。"""
    items = [
        _with_times(fire, ("scheduled_at",))
        for fire in page.get("items") or []
        if isinstance(fire, Mapping)
    ]
    dst_events = [
        _with_times(event, ("occurred_at",))
        for event in page.get("dst_events") or []
        if isinstance(event, Mapping)
    ]
    payload = dict(page)
    payload["items"] = items
    payload["dst_events"] = dst_events
    return payload


#: 生命周期动作回执里出现的时刻字段（Worker `_payload` 的 Schedule 行投影）。
_ACTION_TIME_FIELDS = (
    "next_fire_at",
    "last_fire_at",
    "attention_since",
    "ended_at",
    "create_time",
    "update_time",
)


def action_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """PAUSE/RESUME/CANCEL 的 Worker 回执：状态与调度指针，加上可选的准入失败原因。"""
    return _with_times(payload, _ACTION_TIME_FIELDS)
