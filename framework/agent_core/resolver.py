from uuid import UUID

from framework.domain.agent import AgentDefinition
from framework.web.errors import AppError


def resolve_agent(agent_id: UUID, config: dict[str, object] | None) -> AgentDefinition:
    """Resolve the current effective Agent config (01 LIB-02, V1.7 D04).

    The caller fetches the current revision row (direct-effect, no published
    release); this function validates it into a domain object or raises an
    explicit error. Never returns a bare dict.
    """
    if config is None:
        raise AppError(code="AGENT_NOT_FOUND", message="agent not found", status_code=404)
    try:
        return AgentDefinition(
            id=agent_id,
            name=str(config["name"]),
            description=str(config.get("description", "")),
            instructions=str(config["instructions"]),
            model_config_ref=str(config.get("model_config_ref", "")),
            skill_bindings=list(config.get("skill_bindings", []) or []),
            knowledge_bindings=list(config.get("knowledge_bindings", []) or []),
            capability_bindings=list(config.get("capability_bindings", []) or []),
            service_bindings=list(config.get("service_bindings", []) or []),
            memory_policy=dict(config.get("memory_policy", {}) or {}),
            revision=int(config.get("revision", 1)),
            enabled=bool(config.get("enabled", True)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(
            code="AGENT_CONFIGURATION_INVALID",
            message=f"agent config invalid: {exc}",
            status_code=500,
        ) from exc
