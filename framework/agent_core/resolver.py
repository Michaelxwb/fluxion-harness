from uuid import UUID

from framework.domain.agent import AgentDefinition
from framework.web.errors import AppError


def _string_list(value: object) -> list[str]:
    """Coerce a config list into a list of strings; anything else becomes empty."""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _string_map(value: object) -> dict[str, object]:
    """Coerce a config mapping into a string-keyed dict; anything else becomes empty."""
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _int_value(value: object, field: str) -> int:
    """Coerce a config scalar to int, raising TypeError so callers map it to
    AGENT_CONFIGURATION_INVALID rather than silently defaulting."""
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"{field} must be a number, got {type(value).__name__}")


_REQUIRED_BINDING_KEYS = (
    "skill_bindings",
    "knowledge_bindings",
    "capability_bindings",
    "service_bindings",
)


def resolve_agent(agent_id: UUID, config: dict[str, object] | None) -> AgentDefinition:
    """Resolve the current effective Agent config (01 LIB-02, V1.7 D04).

    The caller fetches the current revision row (direct-effect, no published
    release); this function validates it into a domain object or raises an
    explicit error. Never returns a bare dict.

    Returns the **complete** aggregate: every binding set must be present in
    the supplied config. A config that merely omits bindings is rejected rather
    than silently resolved to empty lists — an Agent whose bindings were
    dropped is not a valid aggregate, and defaulting them to empty would
    silently strip capabilities from the resolved Agent.
    """
    if config is None:
        raise AppError(code="AGENT_NOT_FOUND", message="agent not found", status_code=404)
    missing = [key for key in _REQUIRED_BINDING_KEYS if key not in config]
    if missing:
        raise AppError(
            code="AGENT_CONFIGURATION_INVALID",
            message=f"agent config missing binding sets: {', '.join(missing)}",
            status_code=500,
        )
    try:
        return AgentDefinition(
            id=agent_id,
            name=str(config["name"]),
            description=str(config.get("description", "")),
            instructions=str(config["instructions"]),
            model_config_ref=str(config.get("model_config_ref", "")),
            skill_bindings=_string_list(config.get("skill_bindings")),
            knowledge_bindings=_string_list(config.get("knowledge_bindings")),
            capability_bindings=_string_list(config.get("capability_bindings")),
            service_bindings=_string_list(config.get("service_bindings")),
            memory_policy=_string_map(config.get("memory_policy")),
            revision=_int_value(config.get("revision", 1), "revision"),
            enabled=bool(config.get("enabled", True)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(
            code="AGENT_CONFIGURATION_INVALID",
            message=f"agent config invalid: {exc}",
            status_code=500,
        ) from exc
