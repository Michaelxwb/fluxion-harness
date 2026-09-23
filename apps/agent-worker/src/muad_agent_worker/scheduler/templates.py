"""Schedule `input_template` 的受控动态变量（设计 §3.3.1 `input_template_json`）。

只支持白名单变量，按 Schedule 的 IANA 时区、以本次 `scheduled_fire_time` 为基准渲染：

- `{{fire_date}}`：触发日（YYYY-MM-DD）
- `{{previous_day}}`：触发日前一天（YYYY-MM-DD）
- `{{fire_time}}`：触发时刻（本地 ISO8601，含偏移）

只在字符串值内替换；未知变量在创建/更新时即拒绝，不在触发时静默留空。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from muad_api import AppError
from muad_api.error_codes import ErrorCode

VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
ALLOWED_VARIABLES = frozenset({"fire_date", "previous_day", "fire_time"})


def _walk(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _walk(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, transform) for item in value]
    return value


def _check(text: str) -> str:
    unknown = {name for name in VARIABLE_PATTERN.findall(text) if name not in ALLOWED_VARIABLES}
    if unknown:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return text


def validate_input_template(template: dict[str, Any]) -> None:
    _walk(template, _check)


def template_variables(fire_at: datetime, timezone: str) -> dict[str, str]:
    local = fire_at.astimezone(ZoneInfo(timezone))
    return {
        "fire_date": local.date().isoformat(),
        "previous_day": (local.date() - timedelta(days=1)).isoformat(),
        "fire_time": local.isoformat(),
    }


def render_input_template(
    template: dict[str, Any], fire_at: datetime, timezone: str
) -> dict[str, Any]:
    variables = template_variables(fire_at, timezone)

    def substitute(text: str) -> str:
        return VARIABLE_PATTERN.sub(lambda match: variables.get(match.group(1), match.group(0)), text)

    rendered = _walk(template, substitute)
    if not isinstance(rendered, dict):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return rendered
