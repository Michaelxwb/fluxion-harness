from __future__ import annotations

from pydantic import BaseModel, ValidationError

from fluxion.agents import AgentDefinition
from fluxion.errors.console import ConsoleForbiddenError, ConsoleValidationError
from fluxion.resources import (
    EvalSetDefinition,
    MCPDefinition,
    ModelDefinition,
    PluginDefinition,
    PolicyDefinition,
    ProviderDefinition,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
    SecretDefinition,
    SkillDefinition,
    ToolDefinition,
    WorkflowDefinition,
)
from fluxion.services.console_contracts import ConsoleActor
from fluxion.services.workflow_app import WorkflowValidationResult


def _ensure_same_tenant(actor: ConsoleActor, tenant_id: str) -> None:
    if not tenant_id.strip():
        raise ConsoleValidationError("tenant_id is required")
    if tenant_id != actor.tenant_id:
        raise ConsoleForbiddenError()


def _rollback_requires_approval(resource: ResourceDefinition) -> bool:
    if resource.status is ResourceStatus.DEPRECATED:
        return True
    compatibility = resource.spec_json.get("compatibility")
    if not isinstance(compatibility, dict):
        return False
    return compatibility.get("rollback_safe") is False


def _raise_for_invalid_workflow(result: WorkflowValidationResult) -> None:
    if not result.valid:
        raise ConsoleValidationError("；".join(result.diagnostics))


def _definition_model(kind: ResourceKind) -> type[BaseModel] | None:
    if kind is ResourceKind.AGENT_DEFINITION:
        return AgentDefinition
    if kind is ResourceKind.RUNTIME_PROFILE:
        return RuntimeProfile
    if kind is ResourceKind.MODEL_PROVIDER:
        return ProviderDefinition
    if kind is ResourceKind.MODEL_DEFINITION:
        return ModelDefinition
    if kind is ResourceKind.TOOL:
        return ToolDefinition
    if kind is ResourceKind.SKILL:
        return SkillDefinition
    if kind is ResourceKind.MCP:
        return MCPDefinition
    if kind is ResourceKind.SECRET:
        return SecretDefinition
    if kind is ResourceKind.PLUGIN:
        return PluginDefinition
    if kind is ResourceKind.POLICY:
        return PolicyDefinition
    if kind is ResourceKind.WORKFLOW:
        # 发布路径仍走带能力引用存在性检查的 WorkflowDefinitionValidator；
        # 此处提供结构校验兜底与表单 schema 来源（ADR-012）。
        return WorkflowDefinition
    if kind is ResourceKind.EVAL_SET:
        return EvalSetDefinition
    return None


def _validate_definition(kind: ResourceKind, spec: dict[str, object]) -> WorkflowValidationResult:
    model = _definition_model(kind)
    if model is None:
        return WorkflowValidationResult(True, ("校验通过",))
    try:
        model.model_validate(spec)
    except (ValidationError, ValueError) as exc:
        return WorkflowValidationResult(False, (_format_definition_error(exc),))
    return WorkflowValidationResult(True, ("校验通过",))


def _format_definition_error(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError):
        parts: list[str] = []
        for error in exc.errors(include_url=False)[:5]:
            path = ".".join(str(part) for part in error["loc"])
            message = str(error["msg"])
            parts.append(f"{path}: {message}" if path else message)
        return "；".join(parts)
    return str(exc)

