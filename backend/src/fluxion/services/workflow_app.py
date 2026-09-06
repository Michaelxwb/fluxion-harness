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
from fluxion.resources.workflow_nodes import CapabilityNode


@dataclass(frozen=True, slots=True)
class WorkflowValidationResult:
    valid: bool
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowDiagnostic:
    """TASK-015：逐字段定位诊断（E-02；nodeId/field 对齐前端诊断契约）。"""

    field: str
    message: str
    node_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowStructuredValidationResult:
    valid: bool
    diagnostics: tuple[WorkflowDiagnostic, ...]


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

    async def validate_structured(
        self,
        *,
        tenant_id: str,
        spec: dict[str, object],
    ) -> WorkflowStructuredValidationResult:
        """V2 结构化校验：pydantic 判别联合（loc → nodeId/field）+ capability refs。

        诊断结构对齐前端 `validateWorkflowV2`（{node_id?, field, message}），
        供 POST /api/v1/workflows/validate 与 Designer 逐字段定位呈现。
        """
        diagnostics: list[WorkflowDiagnostic] = []
        node_ids: list[str] = []
        steps = spec.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict) and isinstance(step.get("id"), str):
                    node_ids.append(step["id"])
        try:
            workflow = WorkflowDefinition.model_validate(spec)
        except ValidationError as exc:
            for error in exc.errors(include_url=False):
                loc = [part for part in error["loc"] if isinstance(part, (str, int))]
                node_id: str | None = None
                field = "steps"
                if len(loc) >= 2 and loc[0] == "steps" and isinstance(loc[1], int):
                    node_id = node_ids[loc[1]] if loc[1] < len(node_ids) else None
                    # 判别联合 loc 会插入 tag 段（steps.0.capability.capability_ref），
                    # field 取尾段（真实字段名）。
                    field = str(loc[-1]) if len(loc) >= 3 else "steps"
                elif loc:
                    field = str(loc[-1])
                diagnostics.append(
                    WorkflowDiagnostic(field=field, message=str(error["msg"]), node_id=node_id)
                )
            return WorkflowStructuredValidationResult(False, tuple(diagnostics))
        capability_nodes = [node for node in workflow.steps if isinstance(node, CapabilityNode)]
        for step in capability_nodes:
            parsed = _parse_capability_ref(step.capability_ref)
            if parsed is None:
                diagnostics.append(
                    WorkflowDiagnostic(
                        field="capability_ref",
                        message=f"无效 Capability ref: {step.capability_ref}",
                        node_id=step.id,
                    )
                )
                continue
            kind, resource_id, version = parsed
            resource = await self._store.get(
                kind,
                resource_id,
                tenant_id=tenant_id,
                version=version,
            )
            if resource is None or resource.status is not ResourceStatus.PUBLISHED:
                diagnostics.append(
                    WorkflowDiagnostic(
                        field="capability_ref",
                        message=f"Capability ref 不可用: {step.capability_ref}",
                        node_id=step.id,
                    )
                )
        return WorkflowStructuredValidationResult(not diagnostics, tuple(diagnostics))

    async def _validate_capabilities(
        self,
        tenant_id: str,
        workflow: WorkflowDefinition,
    ) -> list[str]:
        diagnostics: list[str] = []
        # V2 节点判别联合：仅 `capability` 节点承载 capability_ref（design §2.3.2）；
        # agent/condition/switch/parallel 等节点引用在各自 validator 层校验。
        capability_nodes = [node for node in workflow.steps if isinstance(node, CapabilityNode)]
        for step in capability_nodes:
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
    # TASK-006：workflow 语法与 Agent AgentCapabilityReference 共用同一解析实现
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
