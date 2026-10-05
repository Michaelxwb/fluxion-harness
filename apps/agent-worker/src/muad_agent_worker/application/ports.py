"""Worker 应用层端口（TASK-006）。

与 Runtime 同形：平台设置快照在**业务操作边界**取一次，失败即抛错（RULE-06），
绝不回退到过期默认值。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PlatformSettingsSnapshot:
    """一个租户在某个业务边界取到的设置快照（revision + 整份设置文档）。"""

    revision: int
    settings: Mapping[str, Any] = field(default_factory=dict)


class PlatformSettingsClient(Protocol):
    """内部取设置快照（Console `GET /internal/v1/platform-settings`）。

    只在业务操作边界调用一次（任务创建/批量扇出、投递尝试）；失败即抛错（RULE-06）。
    """

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot: ...


class NullPlatformSettingsClient:
    """无设置源的调用方默认：等价于「该租户无记录」——revision 0 + 空文档（B-04 语义）。

    生产装配（`main`）一律注入真实 HTTP client；此空对象只服务于不触达设置的
    直构调用点（claim/回收/cancel 等），不表示「源不可读」失败路径。
    """

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=0)
