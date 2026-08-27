from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from fluxion.agents.capabilities import parse_capability_ref
from fluxion.registry import RegistryReadStore
from fluxion.resources import (
    ResourceKind,
    ResourceStatus,
    WorkflowDefinition,
)


@dataclass(frozen=True, slots=True)
class WorkflowValidationResult:
    valid: bool
    diagnostics: tuple[str, ...]


class WorkflowDefinitionValidator:
    def __init__(self, store: RegistryReadStore) -> None:
        self._store = store

    async def validate(
        self,
        *,
        tenant_id: str,
        spec: dict[str, object],
    ) -> WorkflowValidationResult:
        try:
            workflow = WorkflowDefinition.model_validate(spec)
        except ValidationError as exc:
            return WorkflowValidationResult(False, (_format_schema_error(exc),))
        diagnostics = await self._validate_capabilities(tenant_id, workflow)
        if diagnostics:
            return WorkflowValidationResult(False, tuple(diagnostics))
        return WorkflowValidationResult(True, ("校验通过",))

    async def _validate_capabilities(
        self,
        tenant_id: str,
        workflow: WorkflowDefinition,
    ) -> list[str]:
        diagnostics: list[str] = []
        for step in workflow.steps:
            parsed = _parse_capability_ref(step.capability_ref)
            if parsed is None:
                diagnostics.append(f"无效 Capability ref: {step.capability_ref}")
                continue
            kind, resource_id, version = parsed
            resource = await self._store.get(
                kind,
                resource_id,
                tenant_id=tenant_id,
                version=version,
            )
            if resource is None or resource.status is not ResourceStatus.PUBLISHED:
                diagnostics.append(f"Capability ref 不可用: {step.capability_ref}")
        return diagnostics


def _parse_capability_ref(
    value: str,
) -> tuple[ResourceKind, str, str] | None:
    # TASK-006：workflow 语法与 Agent CapabilityBinding 共用同一解析实现
    # （agents.capabilities）——禁止两端各自维护 kind 映射与拆装逻辑。
    ref = parse_capability_ref(value)
    if ref is None:
        return None
    return ref.resource_kind, ref.resource_id, ref.version


def _format_schema_error(exc: ValidationError) -> str:
    # 汇总前几个错误，避免一次只暴露一个诊断，符合 E-C104「显示具体校验错误」。
    parts: list[str] = []
    for error in exc.errors(include_url=False)[:5]:
        path = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"])
        parts.append(f"Workflow DSL {path}: {message}" if path else f"Workflow DSL: {message}")
    return "；".join(parts)
