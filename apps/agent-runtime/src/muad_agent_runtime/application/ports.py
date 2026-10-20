from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from muad_contracts import (
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolveModelRequest,
    ResolveModelResponse,
)


@dataclass(frozen=True, slots=True)
class PlatformSettingsSnapshot:
    """一个租户在某个业务边界取到的设置快照（revision + 整份设置文档）。"""

    revision: int
    settings: Mapping[str, Any] = field(default_factory=dict)


class PlatformSettingsClient(Protocol):
    """内部取设置快照（Console `GET /internal/v1/platform-settings`）。

    只在业务操作边界调用一次（Run 创建）；失败即抛错，绝不回退到过期默认值（RULE-06）。
    """

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot: ...


class NullPlatformSettingsClient:
    """无设置源的调用方默认：等价于「该租户无记录」——revision 0 + 空文档（B-04 语义）。

    生产装配（`main`/`deps`）一律注入真实 HTTP client；此空对象只服务于不触达设置的
    直构调用点（取消/回收/resume 等），不表示「源不可读」失败路径。
    """

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=0)


class ResolveClient(Protocol):
    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse: ...

    async def resolve_model(
        self,
        request: ResolveModelRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveModelResponse:
        """按既有模型定义的主键解析单个模型（摘要模型，ADR-06）。

        与 `resolve` 分开：摘要在 Run 创建边界**额外**解析自己那条模型定义并冻进快照。
        """
        ...


class CredentialsClient(Protocol):
    """API-09：按冻结主键读取当前认证值（只进内存，不落盘）。"""

    async def resolve_credentials(
        self,
        *,
        tenant_id: str,
        payload: dict[str, Any],
        trace_id: str = "",
    ) -> dict[str, Any]: ...
