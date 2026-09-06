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
    BindingCommand,
    BindingOperation,
    ChannelRegistryStore,
    NotFoundError,
    VersionConflictError,
)
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import secret_ref_tenant
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
from fluxion.services.console_resource_schema import _ensure_same_tenant


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
        if request.credential_ref is not None:
            # binding 不校验 credential_ref 归属 → tenant A 管理员可把 ref 填成
            # secret://tenant-b/... 仅在解算时才拦截（CredentialResolver 已加租户
            # 校验）。此处前置阻断，避免越权 binding 落库。
            ref_tenant = secret_ref_tenant(request.credential_ref)
            if ref_tenant is not None and ref_tenant != request.tenant_id:
                raise ConsoleBindingValidationError(
                    "credential_ref does not belong to this tenant"
                )
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
        # ADR-A008 amend（方案 B）：MODEL_PROVIDER Binding 绑定逻辑 Provider ID
        # 跨版本继承；`resource_version_selector` 对该类型无效（死配置移除），
        # 统一写 latest-published，不承载版本敏感语义。
        effective_selector = (
            "latest-published"
            if request.resource_type is ResourceKind.MODEL_PROVIDER
            else request.version_selector
        )
        try:
            binding = ResourceBinding(
                binding_id=f"bind_{uuid4().hex}",
                tenant_id=request.tenant_id,
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                resource_type=request.resource_type,
                resource_id=request.resource_id,
                resource_version_selector=effective_selector,
                config_json=dict(request.config),
                credential_ref=request.credential_ref,
                enabled=True,
            )
            # A12：binding 治理走 commit_binding 单事务（insert+revision+audit+
            # outbox 原子化），不再 store.put_binding + 独立 _append_audit。
            commit = await self._store.commit_binding(
                BindingCommand(
                    event_id=f"evt_{uuid4().hex}",
                    tenant_id=request.tenant_id,
                    binding_id=binding.binding_id,
                    operation=BindingOperation.CREATE,
                    actor_id=actor.actor_id,
                    request_id=actor.request_id,
                    trace_id=actor.trace_id,
                    binding=binding,
                )
            )
        except (ValueError, ValidationError) as exc:
            raise ConsoleBindingValidationError("binding validation failed") from exc
        except VersionConflictError as exc:
            raise ConsoleBindingConflictError("binding already exists") from exc
        return commit.binding

    async def list_bindings(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
        resource_type: ResourceKind | None = None,
    ) -> tuple[list[ResourceBinding], int]:
        return await self._store.list_bindings_page(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
            resource_type=resource_type,
        )

    async def disable_binding(
        self,
        actor: ConsoleActor,
        *,
        binding_id: str,
    ) -> None:
        # A12：disable 走 commit_binding 单事务（update+revision+audit+outbox 原子化），
        # 取代 store.disable_binding + 独立 _append_audit。
        try:
            await self._store.commit_binding(
                BindingCommand(
                    event_id=f"evt_{uuid4().hex}",
                    tenant_id=actor.tenant_id,
                    binding_id=binding_id,
                    operation=BindingOperation.DISABLE,
                    actor_id=actor.actor_id,
                    request_id=actor.request_id,
                    trace_id=actor.trace_id,
                    binding=None,
                )
            )
        except NotFoundError as exc:
            raise ConsoleResourceNotFoundError("binding not found") from exc

    # ---- TASK-013：Agent → 用户授权产品投影（§9.1 添加用户 / Agent Access /
    # User Binding 产品投影；授权语义 = user→agent_definition ResourceBinding，
    # 不暴露 Binding 内部结构）----

    async def list_agent_authorized_users(
        self,
        actor: ConsoleActor,
        *,
        agent_id: str,
    ) -> list[dict[str, object]]:
        agent = await self._store.get(
            ResourceKind.AGENT_DEFINITION, agent_id, tenant_id=actor.tenant_id
        )
        if agent is None:
            raise ConsoleResourceNotFoundError("agent not found")
        agent_capability_refs = {
            item["capability_ref"]
            for item in agent.spec_json.get("capabilities", [])
            if isinstance(item, dict) and "capability_ref" in item
        }
        bindings, _total = await self._store.list_bindings_page(
            tenant_id=actor.tenant_id,
            offset=0,
            limit=1000,
            resource_type=ResourceKind.AGENT_DEFINITION,
            resource_id=agent_id,
            subject_type="user",
        )
        rows: list[dict[str, object]] = []
        for binding in bindings:
            if not binding.enabled:
                # 已撤销授权不再出现在 Agent Access 列表（撤销 = 退出投影）
                continue
            user = await self._store.get_platform_user(
                tenant_id=actor.tenant_id,
                platform_user_id=binding.subject_id,
            )
            grants = await self._store.list_capability_grants(
                tenant_id=actor.tenant_id,
                platform_user_id=binding.subject_id,
            )
            grant_refs = {grant.capability_ref for grant in grants}
            rows.append(
                {
                    "platform_user_id": binding.subject_id,
                    "display_name": user.display_name if user else binding.subject_id,
                    "binding_id": binding.binding_id,
                    "enabled": binding.enabled,
                    "capability_overlap": sorted(grant_refs & agent_capability_refs),
                    "capability_additions": sorted(grant_refs - agent_capability_refs),
                }
            )
        return rows

    async def authorize_agent_user(
        self,
        actor: ConsoleActor,
        *,
        agent_id: str,
        platform_user_id: str,
    ) -> ResourceBinding:
        user = await self._store.get_platform_user(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
        )
        if user is None:
            raise ConsoleResourceNotFoundError("platform user not found")
        return await self.create_binding(
            actor,
            CreateBindingRequest(
                tenant_id=actor.tenant_id,
                subject_type="user",
                subject_id=platform_user_id,
                resource_type=ResourceKind.AGENT_DEFINITION,
                resource_id=agent_id,
            ),
        )

    async def revoke_agent_user_authorization(
        self,
        actor: ConsoleActor,
        *,
        agent_id: str,
        platform_user_id: str,
    ) -> None:
        """撤销授权：disable binding + revoke 该 (user, agent) 全部 Chat Access。

        高影响操作：binding 撤销经 commit_binding 审计；chat access 撤销补独立
        AuditLog（规则 24），保证撤销后已签发链接立即失效（fail-closed）。
        """
        bindings, _total = await self._store.list_bindings_page(
            tenant_id=actor.tenant_id,
            offset=0,
            limit=1000,
            resource_type=ResourceKind.AGENT_DEFINITION,
            resource_id=agent_id,
            subject_type="user",
        )
        matched = [
            binding
            for binding in bindings
            if binding.subject_id == platform_user_id and binding.enabled
        ]
        if not matched:
            raise ConsoleResourceNotFoundError("authorization not found")
        for binding in matched:
            await self.disable_binding(actor, binding_id=binding.binding_id)
        accesses = await self._store.list_chat_access(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
        )
        for access in accesses:
            await self._store.revoke_chat_access(
                tenant_id=actor.tenant_id,
                access_id=access.access_id,
                revoked_at=utc_now(),
            )
            await self._append_audit(
                actor,
                action="chat_access.revoke",
                target_type="chat_access",
                target_id=access.access_id,
                before={"agent_id": agent_id, "platform_user_id": platform_user_id},
                after={"revoked": True},
            )

    # ---- TASK-014（§9.2）：Agent → 渠道产品投影。Web Chat 是正式 Channel（规则
    # 15）；渠道条目 = 该 Agent 的活跃 Chat Access 入口（token 不回显，仅元数据）。

    async def list_agent_channels(
        self,
        actor: ConsoleActor,
        *,
        agent_id: str,
    ) -> dict[str, object]:
        # 渠道状态锚定「存在已发布版本」（latest-published），而非当前 working
        # draft 状态——编辑器打开即产生 draft，不应使已发布渠道误判为未发布。
        agent = await self._store.get(
            ResourceKind.AGENT_DEFINITION,
            agent_id,
            tenant_id=actor.tenant_id,
            version=None,
        )
        if agent is None:
            raise ConsoleResourceNotFoundError("agent not found")
        entries = await self._store.list_chat_access(
            tenant_id=actor.tenant_id,
            agent_id=agent_id,
        )
        platform_users, _user_total = await self._store.list_platform_users(
            tenant_id=actor.tenant_id, offset=0, limit=1000
        )
        users = {user.platform_user_id: user.display_name for user in platform_users}
        return {
            "web": {
                "channel_type": "web",
                # Web Chat 渠道状态锚定 Agent 发布态：入口签发即校验 PUBLISHED
                "status": "active" if agent.status.value == "published" else "inactive",
                "entries": [
                    {
                        "access_id": access.access_id,
                        "platform_user_id": access.platform_user_id,
                        "display_name": users.get(
                            access.platform_user_id, access.platform_user_id
                        ),
                        "created_at": access.created_at.isoformat(),
                    }
                    for access in entries
                ],
            }
        }


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
