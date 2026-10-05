"""API-01 读取设置所需的分组/字段元数据（design §3.4 API-01）。

**默认值与类型**从 `muad_contracts.platform_settings` 的 schema 默认**派生**（不复制默认值，
避免第二处权威）；本模块只补充前端预检与展示需要的**范围/枚举/生效方式/单位/词条键**。
`applies_to` 取 {new_run / new_task / next_operation / restart_required / code}。

`overridden_by_resources`：被多少资源显式覆盖。当前只有压缩分组有资源级覆盖载体
（Agent 的 `runtime_config_json.budget.compaction`），其余分组返回 0。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from muad_contracts.platform_settings import PlatformSettings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AgentDefinition

NEW_RUN = "new_run"
NEW_TASK = "new_task"
NEXT_OPERATION = "next_operation"

#: 分组 → 生效方式（组内字段默认沿用；个别字段在 `_FIELD_META` 里覆盖）。
_GROUP_APPLIES_TO: dict[str, str] = {
    "compaction": NEW_RUN,
    "agent": NEW_RUN,
    "memory": NEW_RUN,
    "task": NEW_TASK,
    "artifact": NEXT_OPERATION,
    "auth": NEXT_OPERATION,
    "locale": NEXT_OPERATION,
    "im": NEXT_OPERATION,
    "mcp": NEXT_OPERATION,
}

#: 逐字段范围/枚举/单位/生效方式覆盖（键为前端使用的字段路径）。
_FIELD_META: dict[str, dict[str, Any]] = {
    "snip.max_groups": {"min": 2},
    "snip.keep_head_groups": {"min": 1},
    "snip.keep_tail_groups": {"min": 1},
    "tool_result.persist_threshold_bytes": {"min": 0, "unit_key": "settings.unit.bytes"},
    "tool_result.round_budget_bytes": {"min": 0, "unit_key": "settings.unit.bytes"},
    "tool_result.preview_head_bytes": {"min": 0, "unit_key": "settings.unit.bytes"},
    "tool_result.preview_tail_bytes": {"min": 0, "unit_key": "settings.unit.bytes"},
    "micro.keep_recent_tool_groups": {"min": 1},
    "summary.threshold_bytes": {"min": 0, "unit_key": "settings.unit.bytes"},
    "history_budget_messages": {"min": 1},
    "memory.budget_ratio": {"min": 0, "max": 1},
    "agent.max_turns": {"min": 1},
    "agent.max_tool_calls": {"min": 0},
    "agent.deadline_ms": {"min": 1, "unit_key": "settings.unit.ms"},
    "agent.max_model_retries": {"min": 0},
    "task.default_deadline_hours": {"min": 1, "unit_key": "settings.unit.hours"},
    "task.max_attempts": {"min": 1},
    "task.batch_max_concurrency": {"min": 1},
    "task.misfire_grace_sec": {"min": 0, "unit_key": "settings.unit.seconds"},
    "task.delivery_max_attempts": {"min": 1},
    "task.delivery_backoff_base_sec": {"min": 1, "unit_key": "settings.unit.seconds"},
    "memory.max_injected_memories": {"min": 0},
    "memory.max_injected_bytes": {"min": 0, "unit_key": "settings.unit.bytes"},
    "memory.max_recall_bytes": {"min": 1, "unit_key": "settings.unit.bytes"},
    "memory.recall_default_limit": {"min": 0},
    "artifact.retention_days": {"min": 1, "unit_key": "settings.unit.days"},
    "artifact.max_archive_files": {"min": 1},
    "artifact.cleanup_batch_size": {"min": 1},
    "auth.min_password_length": {"min": 8},
    "auth.max_failed_attempts": {"min": 1},
    "auth.lock_duration_minutes": {"min": 1, "unit_key": "settings.unit.minutes"},
    "auth.session_ttl_hours": {"min": 1, "unit_key": "settings.unit.hours"},
    "locale.default_locale": {"enum": ["zh-CN", "en-US"]},
    "im.progress_interval_sec": {"min": 1.0, "unit_key": "settings.unit.seconds"},
    "mcp.max_tools_per_server": {"min": 1},
}

#: 非本页管理的项（`.env` + 重启 / 资源页面 / 代码）的归属说明。
_READONLY_NOTES: tuple[dict[str, str], ...] = (
    {
        "key": "env.runtime",
        "label_key": "settings.readonly.env",
        "note_key": "settings.readonly.env_note",
        "owner": "env",
    },
    {
        "key": "resources.override",
        "label_key": "settings.readonly.resources",
        "note_key": "settings.readonly.resources_note",
        "owner": "resources",
    },
    {
        "key": "code.invariants",
        "label_key": "settings.readonly.code",
        "note_key": "settings.readonly.code_note",
        "owner": "code",
    },
)


def _field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "unknown"


def _flatten(group_key: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """把分组文档压成字段路径 → 值；compaction 用组内相对路径（与校验错误路径同形）。"""
    flat: dict[str, Any] = {}

    def walk(prefix: str, node: Mapping[str, Any]) -> None:
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, Mapping):
                walk(path, value)
            else:
                flat[path] = value

    prefix = "" if group_key == "compaction" else group_key
    walk(prefix, document)
    return flat


def _field_meta(path: str, value: Any, applies_to: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "path": path,
        "label_key": f"settings.field.{path}",
        "type": _field_type(value),
        "default": value,
        "value": value,
        "applies_to": applies_to,
        "overridden_by_resources": 0,
    }
    override = _FIELD_META.get(path, {})
    meta.update(override)
    if "applies_to" not in override:
        meta["applies_to"] = applies_to
    return meta


def build_groups(
    settings: PlatformSettings, *, compaction_overrides: int
) -> list[dict[str, Any]]:
    """构建 API-01 的 `data.groups[]`：默认值/类型取自 schema，范围/生效方式取自本模块。"""
    document = asdict(settings)
    groups: list[dict[str, Any]] = []
    for group_key, group_document in document.items():
        applies_to = _GROUP_APPLIES_TO[group_key]
        fields = []
        for path, value in _flatten(group_key, group_document).items():
            field = _field_meta(path, value, applies_to)
            if group_key == "compaction":
                field["overridden_by_resources"] = compaction_overrides
            fields.append(field)
        groups.append(
            {
                "key": group_key,
                "label_key": f"settings.group.{group_key}",
                "applies_to": applies_to,
                "readonly": False,
                "fields": fields,
            }
        )
    return groups


def readonly_notes() -> list[dict[str, str]]:
    return [dict(note) for note in _READONLY_NOTES]


async def count_compaction_overrides(session: AsyncSession, tenant_id: str) -> int:
    """压缩分组的资源覆盖数：`runtime_config_json.budget.compaction` 非空的 Agent 数。"""
    override = AgentDefinition.runtime_config["budget"]["compaction"]
    value = await session.scalar(
        select(func.count())
        .select_from(AgentDefinition)
        .where(
            AgentDefinition.tenant_id == tenant_id,
            AgentDefinition.is_deleted.is_(False),
            override.isnot(None),
        )
    )
    return int(value or 0)
