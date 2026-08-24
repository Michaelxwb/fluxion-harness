from __future__ import annotations

import asyncio
import hashlib
import secrets
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from fluxion.errors.console import (
    INTERNAL_ERROR,
    ConsoleBindingConflictError,
    ConsoleBindingValidationError,
    ConsoleForbiddenError,
    ConsoleResourceConflictError,
    ConsoleResourceNotFoundError,
    ConsoleValidationError,
    ConsoleVersionConflictError,
)
from fluxion.observability.logging import emit_error_log
from fluxion.registry import (
    AuditRecord,
    ChannelRegistryStore,
    ChatAccessRecord,
    NotFoundError,
    PlatformUserRecord,
    PublicationCommand,
    PublicationOperation,
    VersionConflictError,
)
from fluxion.resources import (
    MCPDefinition,
    ModelProviderDefinition,
    PolicyDefinition,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
    SkillDefinition,
)
from fluxion.runtime.secrets import SecretMetadata, SecretMetadataStore
from fluxion.runtime.tracing import TraceRecord, TraceStore
from fluxion.services.approval_app import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalStore,
    InMemoryApprovalStore,
    default_expiry,
    new_approval_id,
    utc_now,
)
from fluxion.services.console_contracts import (
    ApprovalRecordView,
    ConsoleActor,
    CreateApprovalRequest,
    CreateBindingRequest,
    CreateResourceDraftRequest,
    DecideApprovalRequest,
    DeprecateResourceVersionRequest,
    PublishResourceResult,
    PublishResourceVersionRequest,
    RollbackResourceRequest,
    UpdateResourceDraftRequest,
)
from fluxion.services.runtime_contracts import PluginSummary
from fluxion.services.workflow_app import (
    WorkflowDefinitionValidator,
    WorkflowValidationResult,
)


@dataclass(frozen=True, slots=True)
class IssuedChatAccess:
    record: ChatAccessRecord
    token: str


class ConsoleApplicationService:
    def __init__(
        self,
        store: ChannelRegistryStore,
        *,
        trace_store: TraceStore | None = None,
        secret_metadata_store: SecretMetadataStore | None = None,
        approval_store: ApprovalStore | None = None,
        plugin_summaries: Sequence[PluginSummary] = (),
        service_instance_id: str | None = None,
    ) -> None:
        self._store = store
        self._trace_store = trace_store
        self._secret_metadata_store = secret_metadata_store
        self._approval_store = approval_store or InMemoryApprovalStore()
        self._workflow_validator = WorkflowDefinitionValidator(store)
        self._deployment_actions: list[str] = []
        # 只读运行时身份快照：由装配方（dev bundle）注入，避免 Console 反向依赖 Runtime。
        self._plugin_summaries = tuple(plugin_summaries)
        self._service_instance_id = service_instance_id or "console-standalone"
        # 单进程内按资源串行化 publish/rollback/deprecate，保证 optimistic-lock
        # 的 check-then-commit 原子；多实例部署需依赖 DB 级串行化（如 advisory lock）。
        self._publication_locks: dict[tuple[str, ResourceKind, str], asyncio.Lock] = {}

    @property
    def deployment_actions(self) -> tuple[str, ...]:
        return tuple(self._deployment_actions)

    async def initialize(self) -> None:
        await self._store.initialize()

    async def close(self) -> None:
        await self._store.close()

    def _publication_lock(
        self,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
    ) -> asyncio.Lock:
        key = (tenant_id, kind, resource_id)
        lock = self._publication_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._publication_locks[key] = lock
        return lock

    async def create_platform_user(
        self,
        actor: ConsoleActor,
        *,
        platform_user_id: str,
        display_name: str,
    ) -> PlatformUserRecord:
        now = datetime.now(UTC)
        record = PlatformUserRecord(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            display_name=display_name or platform_user_id,
            created_at=now,
        )
        created = await self._store.create_platform_user(record)
        await self._append_audit(
            actor,
            action="platform_user.create",
            target_type="platform_user",
            target_id=platform_user_id,
            before=None,
            after=platform_user_payload(created),
        )
        return created

    async def list_platform_users(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformUserRecord], int]:
        return await self._store.list_platform_users(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_policies(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResourceDefinition], int]:
        """列出 tenant 已注册的 Policy 资源（P1 Plugin/Hook Policy 视图只读数据源）。"""
        return await self._store.list_resources(
            kind=ResourceKind.POLICY,
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_capabilities(
        self,
        actor: ConsoleActor,
    ) -> list[dict[str, object]]:
        """Capability Registry 视图：枚举已装配运行时插件的能力描述（只读快照）。"""
        del actor  # 能力注册表为全局装配快照，不按 tenant 划分
        return [_capability_payload(summary) for summary in self._plugin_summaries]

    async def runtime_status(
        self,
        actor: ConsoleActor,
    ) -> dict[str, object]:
        """Runtime Status 视图：只读运行时身份与健康摘要，不管理 Agent Pod。"""
        del actor
        return {
            "service_instance_id": self._service_instance_id,
            "status": "healthy",
            "provider_count": len(self._plugin_summaries),
            "plugin_count": len(self._plugin_summaries),
        }

    async def get_trace(self, actor: ConsoleActor, trace_id: str) -> TraceRecord:
        if self._trace_store is None:
            raise ConsoleResourceNotFoundError("trace store is not configured")
        trace = await self._trace_store.get(trace_id)
        if trace is None or trace.tenant_id != actor.tenant_id:
            raise ConsoleResourceNotFoundError("trace not found")
        return trace

    async def list_credentials(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[SecretMetadata], int]:
        if self._secret_metadata_store is None:
            return [], 0
        return await self._secret_metadata_store.list_metadata(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_runs(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[TraceRecord], int]:
        if self._trace_store is None:
            return [], 0
        return await self._trace_store.list_recent(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def get_run(self, actor: ConsoleActor, execution_id: str) -> TraceRecord:
        if self._trace_store is None:
            raise ConsoleResourceNotFoundError("trace store is not configured")
        trace = await self._trace_store.get_by_execution(
            tenant_id=actor.tenant_id,
            execution_id=execution_id,
        )
        if trace is None:
            raise ConsoleResourceNotFoundError("run not found")
        return trace

    async def list_audit(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[AuditRecord], int]:
        return await self._store.list_audit(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def issue_chat_access(
        self,
        actor: ConsoleActor,
        *,
        platform_user_id: str,
        runtime_profile_id: str,
    ) -> IssuedChatAccess:
        user = await self._store.get_platform_user(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
        )
        if user is None:
            raise ConsoleResourceNotFoundError("platform user not found")
        # 已知 gap：不校验 runtime_profile 是否已发布——dev 模式下 profile 可由
        # 运行时按需解析（见 test_S_P13_04），强制校验会破坏该契约。token 绑定到
        # profile_id，运行时解析失败会以 runtime 错误呈现，而非越权风险。
        token = secrets.token_urlsafe(32)
        record = ChatAccessRecord(
            access_id=f"chat_access_{uuid4().hex}",
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            runtime_profile_id=runtime_profile_id,
            token_hash=_hash_access_token(token),
            created_at=datetime.now(UTC),
        )
        await self._store.create_chat_access(record)
        await self._append_audit(
            actor,
            action="chat_access.create",
            target_type="chat_access",
            target_id=record.access_id,
            before=None,
            after=_chat_access_audit_payload(record),
        )
        return IssuedChatAccess(record=record, token=token)

    async def revoke_chat_access(
        self,
        actor: ConsoleActor,
        *,
        access_id: str,
    ) -> ChatAccessRecord:
        try:
            record = await self._store.revoke_chat_access(
                tenant_id=actor.tenant_id,
                access_id=access_id,
                revoked_at=datetime.now(UTC),
            )
        except NotFoundError as exc:
            raise ConsoleResourceNotFoundError("chat access not found") from exc
        await self._append_audit(
            actor,
            action="chat_access.revoke",
            target_type="chat_access",
            target_id=access_id,
            before=None,
            after=_chat_access_audit_payload(record),
        )
        return record

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
        # Binding 仍沿用独立 Audit 写入；Publication 使用 Registry 原子事务。
        try:
            await self._store.append_audit(
                AuditRecord(
                    audit_id=f"audit_{uuid4().hex}",
                    tenant_id=actor.tenant_id,
                    actor_id=actor.actor_id,
                    request_id=actor.request_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    before=before,
                    after=after,
                )
            )
        except Exception as exc:  # noqa: BLE001 - audit 失败必须不影响主操作
            emit_error_log(
                request_id=actor.request_id,
                trace_id=actor.trace_id,
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                method="service",
                route=f"audit.{action}",
                error_type=type(exc).__name__,
                error_code=INTERNAL_ERROR,
                stack=traceback.format_exc(),
            )

    async def create_resource_draft(
        self,
        actor: ConsoleActor,
        request: CreateResourceDraftRequest,
    ) -> ResourceDefinition:
        _ensure_same_tenant(actor, request.tenant_id)
        definition = ResourceDefinition(
            kind=request.kind,
            id=request.resource_id,
            tenant_id=request.tenant_id,
            version=request.version,
            status=ResourceStatus.DRAFT,
            visibility=request.visibility,
            spec_json=dict(request.spec),
        )
        try:
            return await self._store.put(definition)
        except VersionConflictError as exc:
            raise ConsoleResourceConflictError("resource version already exists") from exc
        except (ValueError, ValidationError) as exc:
            raise ConsoleValidationError("validation failed") from exc

    async def get_resource(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        resource_id: str,
        *,
        version: str | None = None,
    ) -> ResourceDefinition:
        resource = await self._store.get(
            kind,
            resource_id,
            tenant_id=actor.tenant_id,
            version=version,
        )
        if resource is None:
            raise ConsoleResourceNotFoundError()
        return resource

    async def list_resource_versions(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        resource_id: str,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResourceDefinition], int]:
        return await self._store.list_versions(
            kind,
            resource_id,
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_resources(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResourceDefinition], int]:
        return await self._store.list_resources(
            kind,
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def validate_workflow_version(
        self,
        actor: ConsoleActor,
        resource_id: str,
        version: str,
    ) -> WorkflowValidationResult:
        resource = await self._get_exact_resource(
            ResourceKind.WORKFLOW,
            resource_id,
            actor.tenant_id,
            version,
        )
        result = await self._workflow_validator.validate(
            tenant_id=actor.tenant_id,
            spec=resource.spec_json,
        )
        _raise_for_invalid_workflow(result)
        return result

    async def update_resource_draft(
        self,
        actor: ConsoleActor,
        request: UpdateResourceDraftRequest,
    ) -> ResourceDefinition:
        _ensure_same_tenant(actor, request.tenant_id)
        existing = await self._store.get(
            request.kind,
            request.resource_id,
            tenant_id=request.tenant_id,
            version=request.version,
        )
        if existing is None:
            raise ConsoleResourceNotFoundError()
        if existing.status is ResourceStatus.PUBLISHED:
            raise ConsoleVersionConflictError("已发布版本不可直接修改，请创建新的 Draft Version")
        definition = existing.model_copy(update={"spec_json": dict(request.spec)})
        try:
            return await self._store.update_draft(definition)
        except VersionConflictError as exc:
            raise ConsoleVersionConflictError("version conflict") from exc

    async def publish_resource_version(
        self,
        actor: ConsoleActor,
        request: PublishResourceVersionRequest,
    ) -> PublishResourceResult:
        _ensure_same_tenant(actor, request.tenant_id)
        # 单进程内串行化同资源的发布，消除 expected_base_version 乐观锁的
        # check-then-commit 竞态（store 事务内对 base 行不加锁，SQLite 下
        # FOR UPDATE 无效）。多进程部署需在 store 层加 advisory lock。
        lock = self._publication_lock(request.tenant_id, request.kind, request.resource_id)
        async with lock:
            return await self._publish_resource_version_locked(actor, request)

    async def _publish_resource_version_locked(
        self,
        actor: ConsoleActor,
        request: PublishResourceVersionRequest,
    ) -> PublishResourceResult:
        existing = await self._store.get(
            request.kind,
            request.resource_id,
            tenant_id=request.tenant_id,
            version=request.version,
        )
        if existing is None:
            raise ConsoleResourceNotFoundError()
        if existing.status is ResourceStatus.PUBLISHED:
            raise ConsoleVersionConflictError("version conflict")
        if request.kind is ResourceKind.WORKFLOW:
            result = await self._workflow_validator.validate(
                tenant_id=actor.tenant_id,
                spec=existing.spec_json,
            )
            _raise_for_invalid_workflow(result)
        else:
            # S_P13_05：非 workflow 资源在发布时也按定义模型校验，invalid spec 不得发布。
            _raise_for_invalid_workflow(_validate_definition(request.kind, existing.spec_json))
        if request.expected_base_version is not None:
            # 乐观并发控制：expected_base_version 对齐当前已发布 base。
            # 首次发布（尚无 base）时退化为与 draft 版本一致。
            base = await self._store.get(
                request.kind,
                request.resource_id,
                tenant_id=request.tenant_id,
            )
            expected_base = base.version if base is not None else existing.version
            if request.expected_base_version != expected_base:
                raise ConsoleVersionConflictError("version conflict")
        return await self._commit_publication(
            actor,
            kind=request.kind,
            resource_id=request.resource_id,
            version=request.version,
            operation=PublicationOperation.PUBLISH,
            expected_base_version=request.expected_base_version,
            publish_note=request.publish_note,
        )

    async def rollback_resource(
        self,
        actor: ConsoleActor,
        request: RollbackResourceRequest,
    ) -> PublishResourceResult:
        _ensure_same_tenant(actor, request.tenant_id)
        lock = self._publication_lock(request.tenant_id, request.kind, request.resource_id)
        async with lock:
            return await self._rollback_resource_locked(actor, request)

    async def _rollback_resource_locked(
        self,
        actor: ConsoleActor,
        request: RollbackResourceRequest,
    ) -> PublishResourceResult:
        target = await self._get_exact_resource(
            request.kind,
            request.resource_id,
            request.tenant_id,
            request.target_version,
        )
        # 回滚到当前已发布版本是空操作，拒绝以避免无意义 revision bump / audit 污染。
        latest = await self._store.get(
            request.kind,
            request.resource_id,
            tenant_id=request.tenant_id,
        )
        if latest is not None and latest.version == request.target_version:
            raise ConsoleVersionConflictError("目标版本已是当前已发布版本")
        if _rollback_requires_approval(target):
            if not request.force or not request.approval_id:
                raise ConsoleVersionConflictError("回滚目标存在兼容性风险，需要强制审批")
            await self._verify_rollback_approval(actor, request, request.approval_id)
        return await self._commit_publication(
            actor,
            kind=request.kind,
            resource_id=request.resource_id,
            version=request.target_version,
            operation=PublicationOperation.ROLLBACK,
            approval_id=request.approval_id,
        )

    async def _verify_rollback_approval(
        self,
        actor: ConsoleActor,
        request: RollbackResourceRequest,
        approval_id: str,
    ) -> None:
        record = await self._approval_store.get(approval_id, tenant_id=actor.tenant_id)
        if record is None:
            raise ConsoleForbiddenError("审批不存在或不属于当前租户")
        if record.status is not ApprovalStatus.APPROVED:
            raise ConsoleForbiddenError("审批未通过")
        if record.expires_at <= utc_now():
            raise ConsoleForbiddenError("审批已过期")
        if (
            record.kind != request.kind
            or record.resource_id != request.resource_id
            or record.target_version != request.target_version
            or record.operation != "rollback"
        ):
            raise ConsoleForbiddenError("审批内容与回滚请求不匹配")
        if actor.actor_id == record.approver_actor_id:
            raise ConsoleForbiddenError("审批人不能执行本次回滚")
        if actor.actor_id != record.requester_actor_id:
            raise ConsoleForbiddenError("仅审批请求人可执行本次回滚")

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

    async def deprecate_resource_version(
        self,
        actor: ConsoleActor,
        request: DeprecateResourceVersionRequest,
    ) -> PublishResourceResult:
        _ensure_same_tenant(actor, request.tenant_id)
        lock = self._publication_lock(request.tenant_id, request.kind, request.resource_id)
        async with lock:
            return await self._deprecate_resource_version_locked(actor, request)

    async def _deprecate_resource_version_locked(
        self,
        actor: ConsoleActor,
        request: DeprecateResourceVersionRequest,
    ) -> PublishResourceResult:
        await self._get_exact_resource(
            request.kind,
            request.resource_id,
            request.tenant_id,
            request.version,
        )
        return await self._commit_publication(
            actor,
            kind=request.kind,
            resource_id=request.resource_id,
            version=request.version,
            operation=PublicationOperation.DEPRECATE,
            publish_note=request.reason,
        )

    async def _get_exact_resource(
        self,
        kind: ResourceKind,
        resource_id: str,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition:
        resource = await self._store.get(
            kind,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )
        if resource is None:
            raise ConsoleResourceNotFoundError()
        return resource

    async def _commit_publication(
        self,
        actor: ConsoleActor,
        *,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        operation: PublicationOperation,
        expected_base_version: str | None = None,
        publish_note: str | None = None,
        approval_id: str | None = None,
    ) -> PublishResourceResult:
        publish_id = f"pub_{uuid4().hex}"
        try:
            commit = await self._store.commit_publication(
                PublicationCommand(
                    publish_id=publish_id,
                    event_id=f"evt_{uuid4().hex}",
                    tenant_id=actor.tenant_id,
                    kind=kind,
                    resource_id=resource_id,
                    version=version,
                    operation=operation,
                    actor_id=actor.actor_id,
                    request_id=actor.request_id,
                    trace_id=actor.trace_id,
                    expected_base_version=expected_base_version,
                    publish_note=publish_note,
                    approval_id=approval_id,
                )
            )
        except NotFoundError as exc:
            raise ConsoleResourceNotFoundError() from exc
        except VersionConflictError as exc:
            raise ConsoleVersionConflictError(str(exc)) from exc
        return PublishResourceResult(
            resource_id=commit.resource.id,
            version=commit.resource.version,
            status=commit.resource.status.value,
            publish_id=commit.publish_id,
            event_status=commit.event_status.value,
            kubernetes_workload_created=False,
        )

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

    async def validate_resource_version(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        resource_id: str,
        version: str,
    ) -> WorkflowValidationResult:
        resource = await self._get_exact_resource(
            kind,
            resource_id,
            actor.tenant_id,
            version,
        )
        if kind is ResourceKind.WORKFLOW:
            result = await self._workflow_validator.validate(
                tenant_id=actor.tenant_id,
                spec=resource.spec_json,
            )
            # E-C104：workflow DSL / capability 校验失败以 400 返回具体诊断。
            _raise_for_invalid_workflow(result)
            return result
        # 其余资源类型：校验失败返回 valid=false 结果（200），由调用方决定行为。
        return _validate_definition(kind, resource.spec_json)


def resource_payload(resource: ResourceDefinition) -> dict[str, object]:
    return {
        "resource_type": resource.kind.value,
        "resource_id": resource.id,
        "tenant_id": resource.tenant_id,
        "version": resource.version,
        "status": resource.status.value,
        "visibility": resource.visibility.value,
        "spec": resource.spec_json,
        "updated_at": (resource.published_at or resource.created_at).isoformat(),
    }


def binding_payload(binding: ResourceBinding) -> dict[str, object]:
    return {
        "binding_id": binding.binding_id,
        "tenant_id": binding.tenant_id,
        "subject_type": str(binding.subject_type),
        "subject_id": binding.subject_id,
        "resource_type": binding.resource_type.value,
        "resource_id": binding.resource_id,
        "version_selector": binding.resource_version_selector,
        "credential_ref": binding.credential_ref,
        "config": binding.config_json or {},
        "enabled": binding.enabled,
    }


def platform_user_payload(user: PlatformUserRecord) -> dict[str, object]:
    return {
        "tenant_id": user.tenant_id,
        "platform_user_id": user.platform_user_id,
        "display_name": user.display_name,
        "created_at": user.created_at.isoformat(),
    }


def policy_payload(resource: ResourceDefinition) -> dict[str, object]:
    spec = resource.spec_json or {}
    return {
        "policy_id": resource.id,
        "name": spec.get("name", resource.id),
        "version": resource.version,
        "status": resource.status.value,
        "visibility": resource.visibility.value,
        "allowed_tools": spec.get("allowed_tools", []),
        "denied_tools": spec.get("denied_tools", []),
    }


def _capability_payload(summary: PluginSummary) -> dict[str, object]:
    return {
        "capability_id": f"model.{summary.plugin_id}",
        "kind": summary.plugin_type,
        "version": "1",
        "provider_id": summary.plugin_id,
        "status": "loaded",
    }


def issued_chat_access_payload(issued: IssuedChatAccess) -> dict[str, object]:
    record = issued.record
    return {
        "access_id": record.access_id,
        "tenant_id": record.tenant_id,
        "platform_user_id": record.platform_user_id,
        "runtime_profile_id": record.runtime_profile_id,
        "token": issued.token,
        "chat_path": f"/chat/#/{issued.token}",
        "created_at": record.created_at.isoformat(),
    }


def trace_payload(trace: TraceRecord) -> dict[str, object]:
    snapshot = trace.snapshot
    return {
        "trace_id": trace.trace_id,
        "execution_id": trace.execution_id,
        "tenant_id": trace.tenant_id,
        "user_id": snapshot.user_id,
        "runtime_profile": {
            "id": snapshot.runtime_profile_id,
            "version": snapshot.runtime_profile_version,
        },
        "skills": snapshot.skill_versions,
        "mcps": snapshot.mcp_versions,
        "plugins": snapshot.plugin_versions,
        "policy_version": snapshot.policy_version,
        "tools": list(trace.tools),
        "error": trace.error,
        "latency_ms": trace.latency_ms,
    }


def credential_payload(metadata: SecretMetadata) -> dict[str, object]:
    return {
        "credential_ref": metadata.ref,
        "provider": metadata.provider,
        "status": "disabled" if metadata.revoked else "active",
        "version": metadata.version,
        "last_rotated_at": metadata.created_at.isoformat(),
    }


def run_payload(trace: TraceRecord) -> dict[str, object]:
    snapshot = trace.snapshot
    started_at = snapshot.created_at.isoformat()
    policies = []
    if snapshot.policy_version is not None:
        policies.append({"id": "tenant-policy", "version": snapshot.policy_version})
    return {
        "execution_id": trace.execution_id,
        "trace_id": trace.trace_id,
        "status": "failed" if trace.error is not None else "succeeded",
        "started_at": started_at,
        "snapshot": {
            "runtime_profile": {
                "id": snapshot.runtime_profile_id,
                "version": snapshot.runtime_profile_version,
            },
            "skills": _version_refs(snapshot.skill_versions),
            "mcps": _version_refs(snapshot.mcp_versions),
            "plugins": _version_refs(snapshot.plugin_versions),
            "policies": policies,
        },
        "trace_events": [
            {
                "id": f"{trace.trace_id}:{index}",
                "event": event.name,
                "at": started_at,
            }
            for index, event in enumerate(trace.events)
        ],
    }


def audit_payload(record: AuditRecord) -> dict[str, object]:
    return {
        "id": record.audit_id,
        "action": record.action,
        "actor_id": record.actor_id,
        "resource_id": record.target_id,
        "resource_version": _audit_version(record),
        "at": record.created_at.isoformat() if record.created_at is not None else "",
    }


def _version_refs(versions: dict[str, str]) -> list[dict[str, str]]:
    return [{"id": resource_id, "version": version} for resource_id, version in versions.items()]


def _audit_version(record: AuditRecord) -> str:
    for payload in (record.after, record.before):
        if payload is not None and isinstance(payload.get("version"), str):
            return str(payload["version"])
    return record.publish_id or ""


def publish_payload(result: PublishResourceResult) -> dict[str, object]:
    return {
        "resource_id": result.resource_id,
        "version": result.version,
        "status": result.status,
        "publish_id": result.publish_id,
        "event_status": result.event_status,
        "kubernetes_workload_created": result.kubernetes_workload_created,
    }


def _ensure_same_tenant(actor: ConsoleActor, tenant_id: str) -> None:
    if not tenant_id.strip():
        raise ConsoleValidationError("tenant_id is required")
    if tenant_id != actor.tenant_id:
        raise ConsoleForbiddenError()


def _hash_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _chat_access_audit_payload(record: ChatAccessRecord) -> dict[str, object]:
    return {
        "access_id": record.access_id,
        "platform_user_id": record.platform_user_id,
        "runtime_profile_id": record.runtime_profile_id,
        "revoked": record.revoked_at is not None,
    }


def _rollback_requires_approval(resource: ResourceDefinition) -> bool:
    if resource.status is ResourceStatus.DEPRECATED:
        return True
    compatibility = resource.spec_json.get("compatibility")
    if not isinstance(compatibility, dict):
        return False
    return compatibility.get("rollback_safe") is False


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


def approval_payload(record: ApprovalRecordView) -> dict[str, object]:
    return {
        "approval_id": record.approval_id,
        "tenant_id": record.tenant_id,
        "kind": record.kind,
        "resource_id": record.resource_id,
        "target_version": record.target_version,
        "operation": record.operation,
        "status": record.status,
        "requester_actor_id": record.requester_actor_id,
        "approver_actor_id": record.approver_actor_id,
        "reason": record.reason,
        "expires_at": record.expires_at.isoformat(),
        "created_at": record.created_at.isoformat(),
        "decided_at": record.decided_at.isoformat() if record.decided_at else None,
    }


def _raise_for_invalid_workflow(result: WorkflowValidationResult) -> None:
    if not result.valid:
        raise ConsoleValidationError("；".join(result.diagnostics))


def _definition_model(kind: ResourceKind) -> type[BaseModel] | None:
    if kind is ResourceKind.RUNTIME_PROFILE:
        return RuntimeProfile
    if kind is ResourceKind.SKILL:
        return SkillDefinition
    if kind is ResourceKind.MCP:
        return MCPDefinition
    if kind is ResourceKind.PLUGIN:
        return ModelProviderDefinition
    if kind is ResourceKind.POLICY:
        return PolicyDefinition
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
