"""平台设置文档的唯一 schema、默认值与跨字段联动校验（design §2.3.2 / ADR-10）。

整份文档按 9 个分组建模（`compaction` 15 / `agent` 4 / `task` 6 / `memory` 5 / `artifact` 3 /
`auth` 4 / `locale` 2 / `im` 1 / `mcp` 1，共 41 个叶子）。字段、默认值与联动校验只在这里写一次，
Console 与三个执行服务共用。按 ADR-10，压缩分组 schema 由 `muad_agent_core.context.settings`
**整体迁移**至此（原模块已删除，不留 re-export 薄壳）。

本模块是**纯数据 + 纯校验**：不读环境变量、不碰数据库、不 import 任何 app 包。默认值只写一次——
改默认值即等于改后续新 Run/Task 的行为。压缩分组形状与既有 `CompactionSettings` 一致，Agent 级
`runtime_config.budget.compaction` 仍按 `merge_compaction_payload` 覆盖平台默认。

`compaction.summary.model_ref` 只做**主键引用**的**形状**校验（UUID）：本模块不引入任何平台默认
模型；模型是否存在/启用由保存事务的 service 结合租户判定（`RULE-model-001`）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: execution snapshot `policy_json` 里压缩配置所在的键（与 Task 快照的 `budget.compaction` 同结构）。
COMPACTION_POLICY_KEY = "compaction"

#: 记忆召回默认条数的上界（与 agent-runtime `memory_tools.RECALL_MAX_LIMIT` 同口径的容量上界）。
RECALL_MAX_LIMIT = 20


class PlatformSettingsError(ValueError):
    """非法平台设置文档。

    显式抛出而不是静默回落到默认值：设置文档是运维输入，悄悄用默认值会让"改错了键名/改出了
    非法组合"完全无从发现（fail-closed）。
    """


class CompactionConfigError(PlatformSettingsError):
    """非法压缩配置。

    保留既有异常类型：调用方仍可 `except CompactionConfigError`；它同时是
    `PlatformSettingsError` 的子类，整份设置文档的校验统一以父类兜底。
    """


@dataclass(frozen=True, slots=True)
class SnipSettings:
    """头尾保留 + 省略标记（FEAT-01）。

    `max_groups` 是**触发阈值**（组数严格大于它才 snip），不是保留总数；保留数量由
    `keep_head_groups` + `keep_tail_groups` 决定，两者之间换成一条按组报数的省略标记。
    """

    enabled: bool = True
    max_groups: int = 50
    keep_head_groups: int = 3
    keep_tail_groups: int = 20


@dataclass(frozen=True, slots=True)
class ToolResultSettings:
    """工具结果的整轮批次预算与预览（FEAT-02）。"""

    persist_threshold_bytes: int = 8 * 1024
    round_budget_bytes: int = 200_000
    preview_head_bytes: int = 2000
    preview_tail_bytes: int = 2000


@dataclass(frozen=True, slots=True)
class MicroSettings:
    """旧工具结果降级（FEAT-03）；默认关。"""

    enabled: bool = False
    keep_recent_tool_groups: int = 3


@dataclass(frozen=True, slots=True)
class SummarySettings:
    """摘要层（FEAT-04）；默认关，开启时必须给出 `model_ref`。"""

    enabled: bool = False
    threshold_bytes: int = 50_000
    model_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MemorySettings:
    """memory 注入在上下文预算里的占比（FEAT-09）。"""

    budget_ratio: float = 0.2


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    snip: SnipSettings = field(default_factory=SnipSettings)
    tool_result: ToolResultSettings = field(default_factory=ToolResultSettings)
    micro: MicroSettings = field(default_factory=MicroSettings)
    summary: SummarySettings = field(default_factory=SummarySettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    history_budget_messages: int = 40


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Agent 执行预算（ADR-07）。"""

    max_turns: int = 20
    max_tool_calls: int = 30
    deadline_ms: int = 120_000
    max_model_retries: int = 3


@dataclass(frozen=True, slots=True)
class TaskSettings:
    """任务默认与投递节拍；并发不得超过环境项 `batch_platform_limit`。"""

    default_deadline_hours: int = 24
    max_attempts: int = 3
    batch_max_concurrency: int = 8
    misfire_grace_sec: int = 60
    delivery_max_attempts: int = 5
    delivery_backoff_base_sec: int = 5


@dataclass(frozen=True, slots=True)
class MemoryPolicySettings:
    """记忆写入与召回规模（`memory` 分组，区别于压缩的 `MemorySettings`）。"""

    write_enabled: bool = True
    max_injected_memories: int = 10
    max_injected_bytes: int = 2048
    max_recall_bytes: int = 4096
    recall_default_limit: int = 10


@dataclass(frozen=True, slots=True)
class ArtifactSettings:
    """产物保留与清理规模。"""

    retention_days: int = 30
    max_archive_files: int = 2000
    cleanup_batch_size: int = 500


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """认证策略：口令长度、失败锁定与会话时长（Cookie `Max-Age` 同源）。"""

    min_password_length: int = 12
    max_failed_attempts: int = 5
    lock_duration_minutes: int = 15
    session_ttl_hours: int = 12


@dataclass(frozen=True, slots=True)
class LocaleSettings:
    """默认语言与 IANA 时区（`RULE-time-001`）。"""

    default_locale: str = "zh-CN"
    default_timezone: str = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class ImSettings:
    """IM 回复生命周期内的展示节拍。"""

    progress_interval_sec: float = 5.0


@dataclass(frozen=True, slots=True)
class McpSettings:
    """MCP 接入规模默认；连接参数仍留 MCP 页面。"""

    max_tools_per_server: int = 200


@dataclass(frozen=True, slots=True)
class PlatformSettings:
    """整份平台设置文档（9 个分组）。"""

    compaction: CompactionSettings = field(default_factory=CompactionSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    task: TaskSettings = field(default_factory=TaskSettings)
    memory: MemoryPolicySettings = field(default_factory=MemoryPolicySettings)
    artifact: ArtifactSettings = field(default_factory=ArtifactSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    locale: LocaleSettings = field(default_factory=LocaleSettings)
    im: ImSettings = field(default_factory=ImSettings)
    mcp: McpSettings = field(default_factory=McpSettings)


_SECTIONS: dict[str, type] = {
    "snip": SnipSettings,
    "tool_result": ToolResultSettings,
    "micro": MicroSettings,
    "summary": SummarySettings,
    "memory": MemorySettings,
}

_GROUPS: dict[str, type] = {
    "agent": AgentSettings,
    "task": TaskSettings,
    "memory": MemoryPolicySettings,
    "artifact": ArtifactSettings,
    "auth": AuthSettings,
    "locale": LocaleSettings,
    "im": ImSettings,
    "mcp": McpSettings,
}


def default_compaction_settings() -> CompactionSettings:
    return CompactionSettings()


def default_platform_settings() -> PlatformSettings:
    return PlatformSettings()


def compaction_payload(settings: CompactionSettings) -> dict[str, Any]:
    """冻进 snapshot 的 JSON 形状（键序固定，便于逐键比对与 hash 稳定）。"""
    return {
        "snip": {
            "enabled": settings.snip.enabled,
            "max_groups": settings.snip.max_groups,
            "keep_head_groups": settings.snip.keep_head_groups,
            "keep_tail_groups": settings.snip.keep_tail_groups,
        },
        "tool_result": {
            "persist_threshold_bytes": settings.tool_result.persist_threshold_bytes,
            "round_budget_bytes": settings.tool_result.round_budget_bytes,
            "preview_head_bytes": settings.tool_result.preview_head_bytes,
            "preview_tail_bytes": settings.tool_result.preview_tail_bytes,
        },
        "micro": {
            "enabled": settings.micro.enabled,
            "keep_recent_tool_groups": settings.micro.keep_recent_tool_groups,
        },
        "summary": {
            "enabled": settings.summary.enabled,
            "threshold_bytes": settings.summary.threshold_bytes,
            "model_ref": settings.summary.model_ref,
        },
        "history_budget_messages": settings.history_budget_messages,
        "memory": {"budget_ratio": settings.memory.budget_ratio},
    }


def merge_compaction_payload(
    *,
    base: Mapping[str, Any] | None = None,
    override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按节逐键合并（不是整块替换）：`override` 没给的键回落 `base`，`base` 没给的回落默认。"""
    merged: dict[str, Any] = {}
    for name in (*_SECTIONS, "history_budget_messages"):
        base_value = (base or {}).get(name)
        override_value = (override or {}).get(name)
        if name == "history_budget_messages":
            if override_value is not None:
                merged[name] = override_value
            elif base_value is not None:
                merged[name] = base_value
            continue
        section: dict[str, Any] = {}
        if isinstance(base_value, Mapping):
            section.update(base_value)
        if isinstance(override_value, Mapping):
            section.update(override_value)
        if section:
            merged[name] = section
    return merged


def _coerce_section(
    name: str,
    raw: Any,
    cls: type,
    error: type[PlatformSettingsError] = CompactionConfigError,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise error(f"{name} 必须是对象，收到 {type(raw).__name__}")
    template = cls()
    allowed = {item.name for item in fields(cls)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise error(f"{name} 含未知配置键: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        reference = getattr(template, key)
        if isinstance(reference, bool):
            if not isinstance(value, bool):
                raise error(f"{name}.{key} 必须是布尔值")
        elif isinstance(reference, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise error(f"{name}.{key} 必须是整数")
        elif isinstance(reference, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise error(f"{name}.{key} 必须是数字")
        elif value is not None and not isinstance(value, str):
            raise error(f"{name}.{key} 必须是字符串或 null")
        values[key] = value
    return values


def parse_compaction_settings(payload: Mapping[str, Any] | None) -> CompactionSettings:
    """从（部分）JSON 覆盖解析出完整配置；任何非法输入都显式抛 `CompactionConfigError`。"""
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise CompactionConfigError("compaction 必须是对象")
    unknown = sorted(set(payload) - {*_SECTIONS, "history_budget_messages"})
    if unknown:
        raise CompactionConfigError(f"compaction 含未知配置键: {', '.join(unknown)}")

    kwargs: dict[str, Any] = {}
    for name, cls in _SECTIONS.items():
        if name in payload:
            kwargs[name] = cls(**_coerce_section(name, payload[name], cls))
    if "history_budget_messages" in payload:
        value = payload["history_budget_messages"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise CompactionConfigError("history_budget_messages 必须是整数")
        kwargs["history_budget_messages"] = value

    settings = CompactionSettings(**kwargs)
    _validate(settings)
    return settings


def parse_platform_settings(
    payload: Mapping[str, Any] | None = None, *, batch_platform_limit: int | None = None
) -> PlatformSettings:
    """从（部分）JSON 解析出完整设置文档；任何非法输入显式抛 `PlatformSettingsError`。

    未提供的分组回落 schema 默认；未知分组/键一律拒绝（fail-closed）。`batch_platform_limit`
    是环境项上界，由调用方注入（本模块不读环境）。
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise PlatformSettingsError("平台设置必须是对象")
    unknown = sorted(set(payload) - {"compaction", *_GROUPS})
    if unknown:
        raise PlatformSettingsError(f"平台设置含未知分组: {', '.join(unknown)}")

    kwargs: dict[str, Any] = {}
    if "compaction" in payload:
        kwargs["compaction"] = parse_compaction_settings(payload["compaction"])
    for name, cls in _GROUPS.items():
        if name in payload:
            kwargs[name] = cls(**_coerce_section(name, payload[name], cls, PlatformSettingsError))

    settings = PlatformSettings(**kwargs)
    validate_platform_settings(settings, batch_platform_limit=batch_platform_limit)
    return settings


def _at_least(path: str, value: float, minimum: float) -> None:
    if value < minimum:
        raise PlatformSettingsError(f"{path} 必须 ≥ {minimum}")


def _validate(settings: CompactionSettings) -> None:
    snip = settings.snip
    if snip.keep_head_groups < 1:
        raise CompactionConfigError("snip.keep_head_groups 必须 ≥ 1")
    if snip.keep_tail_groups < 1:
        raise CompactionConfigError("snip.keep_tail_groups 必须 ≥ 1")
    if snip.max_groups < snip.keep_head_groups + snip.keep_tail_groups + 1:
        raise CompactionConfigError(
            "snip.max_groups 必须 ≥ keep_head_groups + keep_tail_groups + 1"
        )

    tool = settings.tool_result
    if tool.persist_threshold_bytes < 0:
        raise CompactionConfigError("tool_result.persist_threshold_bytes 必须 ≥ 0")
    if tool.round_budget_bytes < tool.persist_threshold_bytes:
        raise CompactionConfigError(
            "tool_result.round_budget_bytes 不得小于 persist_threshold_bytes"
        )
    if tool.preview_head_bytes < 0 or tool.preview_tail_bytes < 0:
        raise CompactionConfigError("tool_result.preview_*_bytes 必须 ≥ 0")
    if tool.preview_head_bytes + tool.preview_tail_bytes > tool.round_budget_bytes:
        raise CompactionConfigError(
            "tool_result.preview_head_bytes + preview_tail_bytes 不得超过 round_budget_bytes"
        )

    if settings.micro.keep_recent_tool_groups < 1:
        raise CompactionConfigError("micro.keep_recent_tool_groups 必须 ≥ 1")

    if settings.summary.enabled and not (settings.summary.model_ref or "").strip():
        raise CompactionConfigError("summary.enabled 必须同时给出 model_ref")
    if settings.summary.threshold_bytes < 0:
        raise CompactionConfigError("summary.threshold_bytes 必须 ≥ 0")

    if settings.history_budget_messages < 1:
        raise CompactionConfigError("history_budget_messages 必须 ≥ 1")

    if not 0 <= settings.memory.budget_ratio <= 1:
        raise CompactionConfigError("memory.budget_ratio 必须落在 [0, 1]")


def _validate_summary_model_ref(summary: SummarySettings) -> None:
    """`model_ref` 只作主键引用的形状校验（UUID），不引入平台默认模型。"""
    if not summary.enabled:
        return
    try:
        uuid.UUID((summary.model_ref or "").strip())
    except ValueError as exc:
        raise CompactionConfigError(
            "compaction.summary.model_ref 必须是既有模型定义的主键（UUID）"
        ) from exc


def _validate_agent(agent: AgentSettings) -> None:
    _at_least("agent.max_turns", agent.max_turns, 1)
    _at_least("agent.max_tool_calls", agent.max_tool_calls, 0)
    _at_least("agent.deadline_ms", agent.deadline_ms, 1)
    _at_least("agent.max_model_retries", agent.max_model_retries, 0)


def _validate_task(task: TaskSettings, batch_platform_limit: int | None) -> None:
    _at_least("task.default_deadline_hours", task.default_deadline_hours, 1)
    _at_least("task.max_attempts", task.max_attempts, 1)
    _at_least("task.batch_max_concurrency", task.batch_max_concurrency, 1)
    if batch_platform_limit is not None and task.batch_max_concurrency > batch_platform_limit:
        raise PlatformSettingsError(
            f"task.batch_max_concurrency 不得超过 batch_platform_limit（{batch_platform_limit}）"
        )
    _at_least("task.misfire_grace_sec", task.misfire_grace_sec, 0)
    _at_least("task.delivery_max_attempts", task.delivery_max_attempts, 1)
    _at_least("task.delivery_backoff_base_sec", task.delivery_backoff_base_sec, 1)


def _validate_memory(memory: MemoryPolicySettings) -> None:
    _at_least("memory.max_injected_memories", memory.max_injected_memories, 0)
    _at_least("memory.max_injected_bytes", memory.max_injected_bytes, 0)
    _at_least("memory.max_recall_bytes", memory.max_recall_bytes, 1)
    _at_least("memory.recall_default_limit", memory.recall_default_limit, 0)
    if memory.recall_default_limit > RECALL_MAX_LIMIT:
        raise PlatformSettingsError(
            f"memory.recall_default_limit 不得超过 RECALL_MAX_LIMIT（{RECALL_MAX_LIMIT}）"
        )


def _validate_artifact(artifact: ArtifactSettings) -> None:
    _at_least("artifact.retention_days", artifact.retention_days, 1)
    _at_least("artifact.max_archive_files", artifact.max_archive_files, 1)
    _at_least("artifact.cleanup_batch_size", artifact.cleanup_batch_size, 1)


def _validate_auth(auth: AuthSettings) -> None:
    _at_least("auth.min_password_length", auth.min_password_length, MIN_PASSWORD_LENGTH_FLOOR)
    _at_least("auth.max_failed_attempts", auth.max_failed_attempts, 1)
    _at_least("auth.lock_duration_minutes", auth.lock_duration_minutes, 1)
    _at_least("auth.session_ttl_hours", auth.session_ttl_hours, 1)


def _validate_locale(locale: LocaleSettings) -> None:
    if locale.default_locale not in ("zh-CN", "en-US"):
        raise PlatformSettingsError(
            f"locale.default_locale 只接受 zh-CN / en-US，收到 {locale.default_locale!r}"
        )
    try:
        ZoneInfo(locale.default_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PlatformSettingsError(
            f"locale.default_timezone 必须是合法的 IANA 时区，收到 {locale.default_timezone!r}"
        ) from exc


def _validate_im(im: ImSettings) -> None:
    _at_least("im.progress_interval_sec", im.progress_interval_sec, 1.0)


def _validate_mcp(mcp: McpSettings) -> None:
    _at_least("mcp.max_tools_per_server", mcp.max_tools_per_server, 1)


def validate_platform_settings(
    settings: PlatformSettings, *, batch_platform_limit: int | None = None
) -> None:
    """校验整份设置文档：分组内范围 + 跨字段联动（未知键在 parse 层拒绝）。

    `batch_platform_limit` 是环境项上界（`SharedSettings.batch_platform_limit`），由调用方注入
    ——contracts 不读环境、不反向依赖 app；传 None 表示不校该条。
    """
    _validate(settings.compaction)
    _validate_summary_model_ref(settings.compaction.summary)
    _validate_agent(settings.agent)
    _validate_task(settings.task, batch_platform_limit)
    _validate_memory(settings.memory)
    _validate_artifact(settings.artifact)
    _validate_auth(settings.auth)
    _validate_locale(settings.locale)
    _validate_im(settings.im)
    _validate_mcp(settings.mcp)


#: `auth.min_password_length` 的**绝对下界**（schema 不变量，不是策略值）：策略值由平台设置给出，
#: 但任何租户都不得把它压到这条线以下。请求 DTO 只守这条线（形状校验），更严的策略下界由服务层
#: 按平台设置执行——否则设置调到 8–11 时页面写 min=8、DTO 仍按旧值拦，设置就是假的。
MIN_PASSWORD_LENGTH_FLOOR = 8


__all__ = [
    "COMPACTION_POLICY_KEY",
    "RECALL_MAX_LIMIT",
    "MIN_PASSWORD_LENGTH_FLOOR",
    "AgentSettings",
    "ArtifactSettings",
    "AuthSettings",
    "CompactionConfigError",
    "CompactionSettings",
    "ImSettings",
    "LocaleSettings",
    "McpSettings",
    "MemoryPolicySettings",
    "MemorySettings",
    "MicroSettings",
    "PlatformSettings",
    "PlatformSettingsError",
    "SnipSettings",
    "SummarySettings",
    "TaskSettings",
    "ToolResultSettings",
    "compaction_payload",
    "default_compaction_settings",
    "default_platform_settings",
    "merge_compaction_payload",
    "parse_compaction_settings",
    "parse_platform_settings",
    "validate_platform_settings",
]
