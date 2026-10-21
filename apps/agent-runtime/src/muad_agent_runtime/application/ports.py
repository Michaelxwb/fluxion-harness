from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelName,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedModel,
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


@dataclass(frozen=True, slots=True)
class AdmissionMaterials:
    """受理边界一次取齐的材料：定义、设置快照、带当前认证值的主模型与 MCP 凭据。"""

    definition: ResolveDefinitionResponse
    settings: PlatformSettingsSnapshot
    model: ResolvedModel
    mcp_secrets: Mapping[str, str]


@runtime_checkable
class AdmissionMaterialsClient(Protocol):
    """合并取数（Console `POST /internal/runtime/admission-materials`）。

    单 Console 进程时，受理边界的三次内部往返各自付一份固定开销（DB 会话、路由与响应
    校验）；合并成一次请求把这份开销从三份降到一份（实测每请求 ~3.8ms，800 并发下直接
    抬升受理延迟）。
    """

    async def fetch_admission_materials(
        self,
        *,
        tenant_id: str,
        agent_id: UUID,
        actor_user_id: UUID,
        execution_id: UUID,
        channel: ChannelName | None = None,
        trace_id: str = "",
    ) -> AdmissionMaterials: ...


class ComposedAdmissionMaterialsClient:
    """按既有三个端口组合取数：无 HTTP 的装配与测试用。

    与 Console 的合并端点同一语义：定义 → 设置快照 → 按定义的主模型/MCP 取当前认证值。
    """

    def __init__(
        self,
        resolve: ResolveClient,
        settings: PlatformSettingsClient,
        credentials: CredentialsClient | None,
    ) -> None:
        self._resolve = resolve
        self._settings = settings
        self._credentials = credentials

    async def fetch_admission_materials(
        self,
        *,
        tenant_id: str,
        agent_id: UUID,
        actor_user_id: UUID,
        execution_id: UUID,
        channel: ChannelName | None = None,
        trace_id: str = "",
    ) -> AdmissionMaterials:
        definition = await self._resolve.resolve(
            ResolveDefinitionRequest(
                agent_id=agent_id, actor_user_id=actor_user_id, channel=channel
            ),
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        snapshot = await self._settings.fetch_snapshot(tenant_id=tenant_id, trace_id=trace_id)
        model = definition.model
        secrets: dict[str, str] = {}
        if self._credentials is not None:
            data = await self._credentials.resolve_credentials(
                tenant_id=tenant_id,
                payload={
                    "execution_ref": {"type": "RUN", "id": str(execution_id)},
                    "actor_user_id": str(actor_user_id),
                    "model_id": str(model.id),
                    "mcp_server_ids": [
                        str(server.mcp_server_id) for server in definition.mcp_servers
                    ],
                },
                trace_id=trace_id,
            )
            api_key = (data.get("model") or {}).get("api_key")
            if not api_key:
                raise AppError(ErrorCode.CREDENTIAL_MISSING)
            model = model.model_copy(update={"api_key": str(api_key)})
            secrets = {
                str(item.get("mcp_server_id")): str(item["auth_secret"])
                for item in data.get("mcp_servers") or []
                if item.get("auth_secret")
            }
        return AdmissionMaterials(
            definition=definition, settings=snapshot, model=model, mcp_secrets=secrets
        )
