"""压缩配置的读取缝：平台设置快照（调用方在边界取好）+ Agent 覆盖 + 默认值，三层合并。

ADR-03：**不保留任何进程内设置缓存**——平台设置快照在业务操作边界取一次（Runtime 是 Run
创建事务内），随调用栈显式传入 `platform_overrides`，解析结果立即冻结进 `policy_json`。
因此「保存后等一会儿才生效」的 TTL 语义被彻底删除；在飞的 Run 用的仍是它自己那份冻结值。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from muad_contracts.platform_settings import (
    CompactionConfigError,
    CompactionSettings,
    merge_compaction_payload,
    parse_compaction_settings,
)


def resolve_compaction_settings(
    agent_runtime_config: Mapping[str, Any] | None,
    *,
    platform_overrides: Mapping[str, Any] | None,
) -> CompactionSettings:
    """把**平台设置快照**（`compaction` 分组）与 Agent 显式覆盖合并成完整压缩配置。

    `platform_overrides` 由调用方在业务边界取好（Runtime = `_create_run` 事务内）；
    Agent 的 `runtime_config.budget.compaction` 优先级更高。非法配置显式报错。
    """
    budget = (agent_runtime_config or {}).get("budget") or {}
    if not isinstance(budget, Mapping):
        raise CompactionConfigError("runtime_config.budget 必须是对象")
    payload = merge_compaction_payload(
        base=platform_overrides, override=budget.get("compaction")
    )
    return parse_compaction_settings(payload)


__all__ = ["resolve_compaction_settings"]
