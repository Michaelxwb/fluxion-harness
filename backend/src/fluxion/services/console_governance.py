from __future__ import annotations

from uuid import uuid4

from pydantic import ValidationError

from fluxion.errors.console import (
    ConsoleBindingConflictError,
    ConsoleBindingValidationError,
    ConsoleForbiddenError,
    ConsoleResourceConflictError,
    ConsoleResourceNotFoundError,
    ConsoleValidationError,
)
from fluxion.registry import (
    ChannelRegistryStore,
    NotFoundError,
    VersionConflictError,
)
from fluxion.resources import ResourceBinding
from fluxion.services.approval_app import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalStore,
    default_expiry,
    new_approval_id,
    utc_now,
)
from fluxion.services.console_contracts import (
    ApprovalRecordView,
    ConsoleActor,
    CreateApprovalRequest,
    CreateBindingRequest,
    DecideApprovalRequest,
)
from fluxion.services.console_resources import _ensure_same_tenant


class ConsoleGovernanceOps:
    """审批与绑定治理操作 mixin。

    由 ConsoleApplicationService 继承；依赖属性在主类 __init__ 中装配。
    """

    _store: ChannelRegistryStore
    _approval_store: ApprovalStore

    async def _append_audit(
        self,
        actor: ConsoleActor,
        *,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> None:
        # 桩方法：实际实现由 ConsoleApplicationService 提供，mixin 仅声明签名
        # 供审批/绑定操作调用，避免 mixin 依赖主类的私有实现。
        raise NotImplementedError

    async def create_approval(
        self,
        actor: ConsoleActor,
        request: CreateApprovalRequest,
    ) -> ApprovalRecordView:
        _ensure_same_tenant(actor, request.tenant_id)
        if request.operation != "rollback":
            raise ConsoleValidationError("当前仅支持 rollback 审批")
        now = utc_now()
        record = ApprovalRecord(
            approval_id=new_approval_id(),
            tenant_id=actor.tenant_id,
            kind=request.kind,
            resource_id=request.resource_id,
            target_version=request.target_version,
            operation=request.operation,
            requester_actor_id=actor.actor_id,
            status=ApprovalStatus.PENDING,
            approver_actor_id=None,
            reason=request.reason,
            expires_at=default_expiry(now, request.ttl_seconds),
            created_at=now,
            decided_at=None,
        )
        await self._approval_store.create(record)
        await self._append_audit(
            actor,
            action="approval.created",
            target_type="approval",
            target_id=record.approval_id,
            before=None,
            after={
                "kind": request.kind.value,
                "resource_id": request.resource_id,
                "target_version": request.target_version,
                "operation": request.operation,
            },
        )
        return _approval_view(record)

    async def decide_approval(
        self,
        actor: ConsoleActor,
        request: DecideApprovalRequest,
    ) -> ApprovalRecordView:
        _ensure_same_tenant(actor, request.tenant_id)
        record = await self._approval_store.get(
            request.approval_id,
            tenant_id=actor.tenant_id,
        )
        if record is None:
            raise ConsoleResourceNotFoundError("审批不存在或不属于当前租户")
        if record.status is not ApprovalStatus.PENDING:
            raise ConsoleResourceConflictError("审批已处理")
        if actor.actor_id == record.requester_actor_id:
            raise ConsoleForbiddenError("审批请求人不能审批自己的请求")
        now = utc_now()
        if record.expires_at <= now:
            raise ConsoleResourceConflictError("审批已过期")
        try:
            decided = await self._approval_store.decide(
                request.approval_id,
                tenant_id=actor.tenant_id,
                approver_actor_id=actor.actor_id,
                approved=request.approved,
                reason=request.reason,
                decided_at=now,
            )
        except (KeyError, ValueError) as exc:
            raise ConsoleResourceConflictError("审批已处理") from exc
        await self._append_audit(
            actor,
            action="approval.decided",
            target_type="approval",
            target_id=decided.approval_id,
            before=None,
            after={
                "kind": decided.kind.value,
                "resource_id": decided.resource_id,
                "target_version": decided.target_version,
                "decision": decided.status.value,
            },
        )
        return _approval_view(decided)

    async def create_binding(
        self,
        actor: ConsoleActor,
        request: CreateBindingRequest,
    ) -> ResourceBinding:
        _ensure_same_tenant(actor, request.tenant_id)
        resource = await self._store.get(
            request.resource_type,
            request.resource_id,
            tenant_id=request.tenant_id,
            version=None
            if request.version_selector == "latest-published"
            else request.version_selector,
        )
        if resource is None:
            raise ConsoleResourceNotFoundError()
        try:
            binding = ResourceBinding(
                binding_id=f"bind_{uuid4().hex}",
                tenant_id=request.tenant_id,
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                resource_type=request.resource_type,
                resource_id=request.resource_id,
                resource_version_selector=request.version_selector,
                config_json=dict(request.config),
                credential_ref=request.credential_ref,
                enabled=True,
            )
            created = await self._store.put_binding(binding)
        except (ValueError, ValidationError) as exc:
            raise ConsoleBindingValidationError("binding validation failed") from exc
        except VersionConflictError as exc:
            raise ConsoleBindingConflictError("binding already exists") from exc
        await self._append_audit(
            actor,
            action="binding.create",
            target_type="binding",
            target_id=created.binding_id,
            before=None,
            after={
                "subject_type": str(created.subject_type),
                "subject_id": created.subject_id,
                "resource_type": created.resource_type.value,
                "resource_id": created.resource_id,
                "version_selector": created.resource_version_selector,
                "enabled": created.enabled,
            },
        )
        return created

    async def list_bindings(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResourceBinding], int]:
        return await self._store.list_bindings_page(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def disable_binding(
        self,
        actor: ConsoleActor,
        *,
        binding_id: str,
    ) -> None:
        try:
            await self._store.disable_binding(binding_id, tenant_id=actor.tenant_id)
        except NotFoundError as exc:
            raise ConsoleResourceNotFoundError("binding not found") from exc
        await self._append_audit(
            actor,
            action="binding.disable",
            target_type="binding",
            target_id=binding_id,
            before=None,
            after={"enabled": False},
        )


def _approval_view(record: ApprovalRecord) -> ApprovalRecordView:
    return ApprovalRecordView(
        approval_id=record.approval_id,
        tenant_id=record.tenant_id,
        kind=record.kind.value,
        resource_id=record.resource_id,
        target_version=record.target_version,
        operation=record.operation,
        status=record.status.value,
        requester_actor_id=record.requester_actor_id,
        approver_actor_id=record.approver_actor_id,
        reason=record.reason,
        expires_at=record.expires_at,
        created_at=record.created_at,
        decided_at=record.decided_at,
    )
