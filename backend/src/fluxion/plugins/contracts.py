from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class TrustLevel(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class PluginType(StrEnum):
    MODEL_PROVIDER = "model_provider"
    TOOL = "tool"
    MEMORY = "memory"
    STORAGE = "storage"
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
class ToolDefinition:
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
    tools: list[ToolDefinition] = field(default_factory=list)
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


class ModelProviderRegistryProtocol(Protocol):
    def register(self, provider_id: str, provider: ModelProvider) -> None: ...

    def resolve(self, provider_id: str) -> ModelProvider: ...
