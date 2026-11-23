"""Capability keys and assembly-time prompt/tool consistency checks."""

from __future__ import annotations

from .registry import ToolCapability, ToolRegistry


class ToolCapabilityMissing(RuntimeError):
    def __init__(self, capability: ToolCapability) -> None:
        self.capability = capability
        super().__init__(f"prompt promises capability without a registered tool: {capability}")


class ToolAssembly:
    """Assembly boundary: a prompt must not promise a capability that has no tool."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def require(self, *capabilities: ToolCapability) -> None:
        available = {
            capability
            for definition in self._registry.list()
            for capability in definition.capabilities
        }
        for capability in capabilities:
            if capability not in available:
                raise ToolCapabilityMissing(capability)


__all__ = ["ToolAssembly", "ToolCapability", "ToolCapabilityMissing"]
