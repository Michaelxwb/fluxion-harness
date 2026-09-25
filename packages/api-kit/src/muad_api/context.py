from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_locale: ContextVar[str] = ContextVar("muad_locale", default="zh-CN")
_trace_id: ContextVar[str] = ContextVar("muad_trace_id", default="")
_request_id: ContextVar[str] = ContextVar("muad_request_id", default="")
_tenant_id: ContextVar[str] = ContextVar("muad_tenant_id", default="")
_caller_service: ContextVar[str] = ContextVar("muad_caller_service", default="")

# docs/09 §6.1 trace 关联字段：全仓库单一口径，各服务不得自造字段名。
TRACE_CORRELATION_FIELDS: tuple[str, ...] = (
    "trace_id",
    "request_id",
    "run_id",
    "conversation_id",
    "platform_user_id",
    "agent_id",
    "snapshot_id",
    "skill_artifact_id",
    "tool_call_id",
    "task_id",
    "schedule_id",
)

_correlation: ContextVar[dict[str, str] | None] = ContextVar("muad_trace_correlation", default=None)
# 响应封套与下游调用头消费这两个字段的独立 contextvar；绑定关联字段时保持一致，避免双写漂移。
_MIRRORED_FIELDS: dict[str, ContextVar[str]] = {
    "trace_id": _trace_id,
    "request_id": _request_id,
}


def _field_value(value: Any) -> str:
    """关联字段一律字符串化；缺失（None）显式置空，绝不伪造。"""
    return "" if value is None else str(value)


def set_trace_context(**fields: Any) -> None:
    """绑定 trace 关联字段；未绑定的字段保持显式空串。

    未知字段名直接拒绝：关联字段集是文档口径（docs/09 §6.1），不允许静默漂移。
    """
    unknown = sorted(set(fields) - set(TRACE_CORRELATION_FIELDS))
    if unknown:
        raise ValueError(f"unknown trace correlation fields: {', '.join(unknown)}")
    current = dict(_correlation.get() or {})
    for name, value in fields.items():
        resolved = _field_value(value)
        current[name] = resolved
        mirror = _MIRRORED_FIELDS.get(name)
        if mirror is not None:
            mirror.set(resolved)
    _correlation.set(current)


def trace_correlation_fields() -> dict[str, str]:
    """docs/09 §6.1 的全部关联字段；未绑定的一律显式空串（不省略、不伪造）。"""
    bound = _correlation.get() or {}
    return {name: bound.get(name, "") for name in TRACE_CORRELATION_FIELDS}


def set_request_context(
    *,
    locale: str,
    trace_id: str,
    request_id: str,
    tenant_id: str = "",
    caller_service: str = "",
    **correlation: Any,
) -> None:
    _locale.set(locale)
    _tenant_id.set(tenant_id)
    _caller_service.set(caller_service)
    set_trace_context(trace_id=trace_id, request_id=request_id, **correlation)


def current_locale() -> str:
    return _locale.get()


def current_trace_id() -> str:
    return _trace_id.get()


def current_request_id() -> str:
    return _request_id.get()


def current_tenant_id() -> str:
    return _tenant_id.get()


def current_caller_service() -> str:
    return _caller_service.get()
