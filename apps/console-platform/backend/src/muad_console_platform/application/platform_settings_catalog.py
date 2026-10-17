"""API-01 读取设置所需的分组/字段元数据（design §3.4 API-01）。

**默认值与类型**从 `muad_contracts.platform_settings` 的 schema 默认**派生**（不复制默认值，
避免第二处权威）；本模块只补充前端预检与展示需要的**范围/枚举/生效方式/单位/词条键**。
`applies_to` 取 {new_run / new_task / next_operation / restart_required / code}。

`overridden_by_resources`：被多少资源显式覆盖。当前**有资源级覆盖载体**的分组只有三个，
载体都落在 Agent 定义的 `runtime_config_json`：`budget.compaction`（compaction 分组）、四个
agent 执行预算键（agent 分组）、`memory_write`（memory 分组）。其余分组没有任何资源表存逐资源
覆盖，计数**结构上就是 0**（不是"未实现"的占位）——`count_resource_overrides` 是唯一计数入口。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, fields
from typing import Any

from muad_contracts.platform_settings import (
    MEMORY_SETTING_BOUNDS,
    PlatformSettings,
    default_platform_settings,
)
from sqlalchemy import func, or_, select
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
    "capacity": NEXT_OPERATION,
    "model_pool": NEW_RUN,
    "webchat": NEXT_OPERATION,
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
    "max_history_tool_rounds": {"min": 0, "max": 50},
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
    "memory.max_injected_bytes": {"unit_key": "settings.unit.bytes"},
    "memory.max_recall_bytes": {"unit_key": "settings.unit.bytes"},
    "memory.max_memory_bytes_per_user": {"unit_key": "settings.unit.bytes"},
    "memory.consolidation_max_input_bytes": {"unit_key": "settings.unit.bytes"},
    "memory.ttl_days_user": {"unit_key": "settings.unit.days"},
    "memory.ttl_days_agent": {"unit_key": "settings.unit.days"},
    "memory.ttl_days_task_state": {"unit_key": "settings.unit.days"},
    "memory.consolidation_timeout_sec": {"unit_key": "settings.unit.seconds"},
    "memory.consolidation_retry_delay_sec": {"unit_key": "settings.unit.seconds"},
    "memory.consolidation_lease_sec": {"unit_key": "settings.unit.seconds"},
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
    "capacity.max_open_runs_per_tenant": {"effect_key": "settings.effect.new_admission"},
    "capacity.max_open_runs_per_user": {"effect_key": "settings.effect.new_admission"},
    "capacity.queue_wait_timeout_sec": {"applies_to": NEW_RUN, "effect_key": "settings.effect.new_run"},
    "capacity.model_wait_timeout_sec": {"applies_to": NEW_RUN, "effect_key": "settings.effect.new_run"},
    "model_pool.dispatch_epoch_sec": {"effect_key": "settings.effect.new_run_or_job"},
    "model_pool.input_estimate_multiplier": {"effect_key": "settings.effect.new_run_or_job"},
    "model_pool.interactive_weight": {"effect_key": "settings.effect.new_run_or_job"},
    "model_pool.background_weight": {"effect_key": "settings.effect.new_run_or_job"},
    "model_pool.aging_interval_sec": {"effect_key": "settings.effect.new_run_or_job"},
    "webchat.access_ttl_days": {"effect_key": "settings.effect.token_issuance"},
    "webchat.session_ttl_hours": {"effect_key": "settings.effect.session_exchange"},
    "webchat.text_max_bytes": {"effect_key": "settings.effect.new_message"},
    "webchat.upload_max_bytes": {"effect_key": "settings.effect.new_upload"},
    "webchat.upload_max_per_message": {"effect_key": "settings.effect.new_message"},
    "webchat.upload_staging_ttl_sec": {"effect_key": "settings.effect.new_upload"},
    "webchat.history_page_size": {"effect_key": "settings.effect.new_query"},
    "webchat.notification_poll_sec": {"effect_key": "settings.effect.page_initialization"},
    "webchat.reconnect_base_ms": {"effect_key": "settings.effect.new_connection"},
    "webchat.reconnect_cap_ms": {"effect_key": "settings.effect.new_connection"},
    "webchat.reconnect_max_attempts": {"effect_key": "settings.effect.new_connection"},
    "webchat.stream_replay_retention_hours": {"effect_key": "settings.effect.next_cleanup"},
    "webchat.notification_retention_days": {"effect_key": "settings.effect.next_cleanup"},
    "webchat.history_retention_days": {"effect_key": "settings.effect.next_cleanup"},
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
    if path.startswith("memory."):
        bounds = MEMORY_SETTING_BOUNDS.get(path.removeprefix("memory."))
        if bounds is not None:
            meta.update(min=bounds[0], max=bounds[1])
    if "applies_to" not in override:
        meta["applies_to"] = applies_to
    return meta


def build_groups(
    settings: PlatformSettings, *, overrides: Mapping[str, int],
    chat_message_hard_max_bytes: int | None = None, chat_upload_hard_max_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """默认/类型/新字段范围与单位取schema；部署硬上限只约束可编辑范围，不裁剪生效值。

    `overrides` 是**分组 → 资源覆盖数**（`count_resource_overrides`）；没有载体的分组缺省即 0。
    """
    document = asdict(settings)
    defaults = default_platform_settings()
    schema_meta = _bounded_schema_metadata(defaults, chat_message_hard_max_bytes, chat_upload_hard_max_bytes)
    groups: list[dict[str, Any]] = []
    for group_key, group_defaults in asdict(defaults).items():
        applies_to = _GROUP_APPLIES_TO[group_key]
        values = _flatten(group_key, document[group_key])
        fields = []
        for path, value in _flatten(group_key, group_defaults).items():
            field = _field_meta(path, value, applies_to)
            field.update(schema_meta.get(path, {}))
            field["value"] = values[path]
            field["overridden_by_resources"] = overrides.get(group_key, 0)
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


def _bounded_schema_metadata(
    defaults: PlatformSettings, message_hard_cap: int | None, upload_hard_cap: int | None,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for group_key in ("capacity", "model_pool", "webchat"):
        for item in fields(getattr(defaults, group_key)):
            meta: dict[str, object] = {"min": item.metadata["minimum"]}
            if "maximum" in item.metadata:
                meta["max"] = item.metadata["maximum"]
            if "unit" in item.metadata:
                meta["unit_key"] = f"settings.unit.{item.metadata['unit']}"
            result[f"{group_key}.{item.name}"] = meta
    for path, hard_cap in (("webchat.text_max_bytes", message_hard_cap),
                           ("webchat.upload_max_bytes", upload_hard_cap)):
        if hard_cap is not None:
            maximum = result[path].get("max", hard_cap)
            if not isinstance(maximum, (int, float)):
                raise ValueError(f"schema maximum must be numeric: {path}")
            result[path]["max"] = min(maximum, hard_cap)
    return result


def readonly_notes() -> list[dict[str, str]]:
    return [dict(note) for note in _READONLY_NOTES]


async def count_resource_overrides(session: AsyncSession, tenant_id: str) -> dict[str, int]:
    """各分组的资源覆盖数：被多少**未删除的 Agent 定义**显式覆盖。

    覆盖载体只有 Agent 的 `runtime_config_json`（见模块说明）：`budget.compaction` 计入
    compaction 分组，四个执行预算键任一命中即计入 agent 分组，`memory_write` 计入 memory 分组。
    其余分组无载体，返回 0——这是**结构事实**，不是未实现的占位。
    """
    runtime_config = AgentDefinition.runtime_config
    base = (
        select(func.count())
        .select_from(AgentDefinition)
        .where(
            AgentDefinition.tenant_id == tenant_id,
            AgentDefinition.is_deleted.is_(False),
        )
    )
    agent_override = or_(
        *(
            runtime_config[key].isnot(None)
            for key in ("max_turns", "max_tool_calls", "deadline_ms", "max_model_retries")
        )
    )
    queries = {
        "compaction": base.where(runtime_config["budget"]["compaction"].isnot(None)),
        "agent": base.where(agent_override),
        "memory": base.where(runtime_config["memory_write"].isnot(None)),
    }
    return {group: int(await session.scalar(query) or 0) for group, query in queries.items()}
