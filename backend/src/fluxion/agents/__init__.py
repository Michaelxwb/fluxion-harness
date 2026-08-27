"""Agent Domain（Phase 1 / TASK-A101..A105 的领域包骨架）。

AgentDefinition 是产品领域实体（PRD §4.2）；本包只依赖 resources/registry
契约，不 import kernel/runtime 实现（architecture-test 守护，TASK-002 落地）。
"""

from fluxion.agents.definitions import (
    AgentDefinition,
    CapabilityBinding,
    CapabilityType,
)
from fluxion.agents.repository import (
    AgentDefinitionNotFoundError,
    AgentDefinitionRepository,
    AgentDomainError,
    AgentSpecValidationError,
    AgentVersionConflictError,
)

__all__ = [
    "AgentDefinition",
    "AgentDefinitionNotFoundError",
    "AgentDefinitionRepository",
    "AgentDomainError",
    "AgentSpecValidationError",
    "AgentVersionConflictError",
    "CapabilityBinding",
    "CapabilityType",
]
