from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class TrustLevel(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class PluginType(StrEnum):
    # ADR-EXT-001 终态：6 保留类型。TOOL/MEMORY/STORAGE 已删除
    #（TOOL→TOOL_EXECUTOR；MEMORY 由 ADR-MEM-001 删除；STORAGE 拆分为
    # ARTIFACT_STORE + SEMANTIC_STORE + SECRET_PROVIDER）。
    MODEL_PROVIDER = "model_provider"
    TOOL_EXECUTOR = "tool_executor"
    ARTIFACT_STORE = "artifact_store"
    SEMANTIC_STORE = "semantic_store"
    SECRET_PROVIDER = "secret_provider"
    HOOK = "hook"


class PluginExecutionMode(StrEnum):
    IN_PROCESS = "in_process"
    ISOLATED = "isolated"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    version: str
    plugin_type: PluginType
    entrypoint: str
    trust_level: TrustLevel
    permissions: list[str]
    dependencies: list[str]
    compatibility: dict[str, object]
    execution_mode: PluginExecutionMode = PluginExecutionMode.IN_PROCESS

    def __post_init__(self) -> None:
        if not self.plugin_id.strip():
            raise ValueError("plugin_id is required")
        if not self.version.strip():
            raise ValueError("version is required")


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    capability_id: str
    kind: str
    version: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginContext:
    tenant_id: str = "system"
    config: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class Plugin(Protocol):
    @property
    def manifest(self) -> PluginManifest: ...

    async def setup(self, ctx: PluginContext) -> None: ...

    async def shutdown(self) -> None: ...


@runtime_checkable
class CapabilityProvider(Protocol):
    def capabilities(self) -> list[CapabilityDescriptor]: ...


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """ModelProvider SPI 的模型侧工具描述符（ADR-A009）。

    与 `resources/contracts.py ToolDefinition`（Tool 是一等 Capability Resource）
    区分：本类只描述「给模型的工具 schema」，是 ModelProvider SPI 的一部分，
    不代表 Plugin 是 Tool。
    """

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: list[ModelMessage]
    tools: list[ToolDescriptor] = field(default_factory=list)
    timeout_ms: int = 60_000
    model: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    provider_version: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    provider_id: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelProviderError(RuntimeError):
    code = "model_provider_error"


class ModelProviderTimeoutError(ModelProviderError):
    code = "model_provider_timeout"


@runtime_checkable
class ModelProvider(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class StreamingModelProvider(Protocol):
    """支持流式输出 token 的模型 Provider（可选能力）。

    未实现该协议的 Provider 走非流式 complete；Runtime 用 isinstance 检测，
    向后兼容既有 ModelProvider 实现。

    注意：stream 是 async generator，签名用普通 def（不带 async），
    调用返回 AsyncGenerator[str, None]（可 async for 迭代、可 aclose）。
    """

    def stream(self, request: ModelRequest) -> AsyncGenerator[str, None]: ...


class ModelProviderRegistryProtocol(Protocol):
    def register(self, provider_id: str, provider: ModelProvider) -> None: ...

    def resolve(self, provider_id: str) -> ModelProvider: ...


class ProviderNotFoundError(RuntimeError):
    """typed provider registry resolve 未命中时抛（替代裸 KeyError，语义化错误码）。"""

    code = "provider_not_found"


# ---------------------------------------------------------------------------
# ADR-EXT-001 统一扩展模型 Provider SPI（形状层，只定形状不锁字段）
#
# 6 个保留 PluginType 各对应一个 typed Provider SPI Protocol + Registry Protocol。
# 本模块为纯契约层：不 import kernel/ runtime/ 具体 impl，不持有 in-memory registry
# 实例（loader per-PluginType 分派与 registry 实例归 TASK-002）。ModelProvider /
# ModelProviderRegistryProtocol 为参考实现（见上）。新 Provider SPI 的生产实现
#（pgvector / S3 / SecretProvider resolve 等）按 design §11 Rolling-wave 延后到
# Phase 1/5，此处不枚举。
# ---------------------------------------------------------------------------


# --- SPI-02: TOOL_EXECUTOR（= ADR-A009 CapabilityProvider 形状）---
# TOOL_EXECUTOR 是 Tool 的 SPI 实现载体（Plugin 提供 Tool 实现，不等于 Plugin 就是
# Tool）；与 ADR-A009 CapabilityProvider 同形。
# loader 既有 `isinstance(plugin, CapabilityProvider)` 不受影响。
ToolProvider = CapabilityProvider


class ToolProviderRegistryProtocol(Protocol):
    def register(self, provider_id: str, provider: ToolProvider) -> None: ...

    def resolve(self, provider_id: str) -> ToolProvider: ...


# --- SPI-03: SEMANTIC_STORE（user-scoped 长期记忆 / 语义检索）---


class SemanticStoreError(RuntimeError):
    code = "semantic_store_error"


@runtime_checkable
class SemanticStoreProvider(Protocol):
    async def store(
        self, tenant_id: str, user_id: str, record: dict[str, object], timeout_ms: int = 30_000
    ) -> None: ...

    async def recall(
        self, tenant_id: str, user_id: str, query: str, top_k: int = 5, timeout_ms: int = 30_000
    ) -> list[dict[str, object]]: ...

    async def search(
        self, tenant_id: str, user_id: str, filters: dict[str, object], timeout_ms: int = 30_000
    ) -> list[dict[str, object]]: ...


class SemanticStoreRegistryProtocol(Protocol):
    def register(self, provider_id: str, provider: SemanticStoreProvider) -> None: ...

    def resolve(self, provider_id: str) -> SemanticStoreProvider: ...


# --- SPI-04: ARTIFACT_STORE（版本化产物 / 文件存储）---


class ArtifactStoreError(RuntimeError):
    code = "artifact_store_error"


@runtime_checkable
class ArtifactStoreProvider(Protocol):
    async def put(
        self, tenant_id: str, namespace: str, key: str, value: bytes, timeout_ms: int = 30_000
    ) -> None: ...

    async def get(
        self, tenant_id: str, namespace: str, key: str, timeout_ms: int = 30_000
    ) -> bytes: ...

    async def delete(
        self, tenant_id: str, namespace: str, key: str, timeout_ms: int = 30_000
    ) -> None: ...


class ArtifactStoreRegistryProtocol(Protocol):
    def register(self, provider_id: str, provider: ArtifactStoreProvider) -> None: ...

    def resolve(self, provider_id: str) -> ArtifactStoreProvider: ...


# --- SPI-05: SECRET_PROVIDER（凭据解析，tenant scope 强制）---
# canonical shape：`resolve(tenant_id, secret_ref)`，tenant_id 显式首参（NFR-SEC-02
# tenant scope 全链路强制）。既有 runtime/secrets.py:SecretStore.resolve(ref) 缺
# 显式 tenant_id，Phase 5 对齐到本 SPI。返回类型 Secret 为契约形状（value+version），
# 非生产实现。


@dataclass(frozen=True, slots=True)
class Secret:
    value: str
    version: str


class SecretResolutionError(RuntimeError):
    code = "secret_resolution_error"


@runtime_checkable
class SecretProvider(Protocol):
    async def resolve(self, tenant_id: str, secret_ref: str, timeout_ms: int = 30_000) -> Secret: ...


class SecretRegistryProtocol(Protocol):
    def register(self, provider_id: str, provider: SecretProvider) -> None: ...

    def resolve(self, provider_id: str) -> SecretProvider: ...


# --- SPI-06: HOOK（typed-lifecycle-hook registry，对齐 ADR-007）---
# 形状对齐 kernel/events.py:HookScheduler（register / ordered）；registration 携带
# priority / timeout_ms / fail_policy / scope（ADR-007 HookRegistration）。本 SPI 只定
# registry 形状、不锁 registration 字段；kernel HookScheduler 为既有实现，Phase 5
# 对齐注入。非 @runtime_checkable（registration 为泛型字段），仅作结构契约。


class HookRegistryProtocol(Protocol):
    def register(self, registration: object) -> None: ...

    def ordered(self, event_type: str) -> list[object]: ...


# --- SPI-07: ProductionCapability（显式 production capability 声明，TASK-013）---
# 替换 production 装配守卫的 isinstance(InMemoryXXX) 黑名单：adapter 显式声明
# ``production_capabilities``（durability / multi-replica / production-ready），
# 守卫按白名单校验；未声明或缺能力的 adapter fail-closed（未知 adapter 不静默放行）。


@runtime_checkable
class ProductionCapability(Protocol):
    production_capabilities: frozenset[str]
