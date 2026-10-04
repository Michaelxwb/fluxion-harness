"""上下文压缩配置的唯一 schema、默认值与校验（design §2.3 字段约束表 / §3.4「配置」）。

这份配置随 execution snapshot 冻结（Run 侧载体是 `policy_json`，见
`harness-snapshot#RULE-snapshot-001`），所以本模块是**纯数据 + 纯校验**：不读环境变量、不碰
数据库、不 import 任何 app 包。默认值只在这里写一次——改默认值即等于改后续新 Run 的行为，
已在飞的 Run 用的仍是它自己那一份冻结值。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

#: execution snapshot `policy_json` 里压缩配置所在的键（与 Task 快照的 `budget.compaction` 同结构）。
COMPACTION_POLICY_KEY = "compaction"


class CompactionConfigError(ValueError):
    """非法压缩配置。

    显式抛出而不是静默回落到默认值：配置是随 snapshot 冻结的运维输入，悄悄用默认值会让
    "改错了键名/改出了非法组合"完全无从发现。
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


_SECTIONS: dict[str, type] = {
    "snip": SnipSettings,
    "tool_result": ToolResultSettings,
    "micro": MicroSettings,
    "summary": SummarySettings,
    "memory": MemorySettings,
}


def default_compaction_settings() -> CompactionSettings:
    return CompactionSettings()


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


def _coerce_section(name: str, raw: Any, cls: type) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CompactionConfigError(f"{name} 必须是对象，收到 {type(raw).__name__}")
    template = cls()
    allowed = {item.name for item in fields(cls)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CompactionConfigError(f"{name} 含未知配置键: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        reference = getattr(template, key)
        if isinstance(reference, bool):
            if not isinstance(value, bool):
                raise CompactionConfigError(f"{name}.{key} 必须是布尔值")
        elif isinstance(reference, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise CompactionConfigError(f"{name}.{key} 必须是整数")
        elif isinstance(reference, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CompactionConfigError(f"{name}.{key} 必须是数字")
        elif value is not None and not isinstance(value, str):
            raise CompactionConfigError(f"{name}.{key} 必须是字符串或 null")
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

    if settings.micro.keep_recent_tool_groups < 1:
        raise CompactionConfigError("micro.keep_recent_tool_groups 必须 ≥ 1")

    if settings.summary.enabled and not (settings.summary.model_ref or "").strip():
        raise CompactionConfigError("summary.enabled 必须同时给出 model_ref")
    if settings.summary.threshold_bytes < 0:
        raise CompactionConfigError("summary.threshold_bytes 必须 ≥ 0")

    if settings.history_budget_messages < 1:
        raise CompactionConfigError("history_budget_messages 必须 ≥ 1")

    if not 0 < settings.memory.budget_ratio <= 1:
        raise CompactionConfigError("memory.budget_ratio 必须落在 (0, 1]")


__all__ = [
    "COMPACTION_POLICY_KEY",
    "CompactionConfigError",
    "CompactionSettings",
    "MemorySettings",
    "MicroSettings",
    "SnipSettings",
    "SummarySettings",
    "ToolResultSettings",
    "compaction_payload",
    "default_compaction_settings",
    "merge_compaction_payload",
    "parse_compaction_settings",
]
