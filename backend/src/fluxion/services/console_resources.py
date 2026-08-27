from __future__ import annotations

import asyncio
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from fluxion.agents import AgentDefinition
from fluxion.errors.console import (
    ConsoleForbiddenError,
    ConsoleResourceConflictError,
    ConsoleResourceNotFoundError,
    ConsoleValidationError,
    ConsoleVersionConflictError,
    StudioSpecValidationError,
)
from fluxion.registry import (
    ChannelRegistryStore,
    NotFoundError,
    PublicationCommand,
    PublicationOperation,
    VersionConflictError,
)
from fluxion.resources import (
    EvalSetDefinition,
    MCPDefinition,
    ModelProviderDefinition,
    PolicyDefinition,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
    SecretDefinition,
    SkillDefinition,
    ToolDefinition,
    WorkflowDefinition,
)
from fluxion.services.approval_app import ApprovalStatus, ApprovalStore, utc_now
from fluxion.services.console_contracts import (
    ConsoleActor,
    CreateResourceDraftRequest,
    DeprecateResourceVersionRequest,
    PublishResourceResult,
    PublishResourceVersionRequest,
    RollbackResourceRequest,
    UpdateResourceDraftRequest,
)
from fluxion.services.workflow_app import (
    WorkflowDefinitionValidator,
    WorkflowValidationResult,
)

# 长跑进程内存上限：publication lock 此前每个 (tenant, kind, resource_id) 一把且
# 从不淘汰 → dev/多租户压测下 _publication_locks 无界增长 OOM。此 cap 仅淘汰未被
# 持有的空闲锁，命中即保留。
_PUBLICATION_LOCK_CAP = 4096


class ConsoleResourceOps:
    """资源生命周期操作 mixin：CRUD、publish/rollback/deprecate 与版本校验。

    由 ConsoleApplicationService 继承，依赖属性在主类 __init__ 中装配；此处仅
    声明类型，避免 mixin 直接持有构造逻辑。
    """

    _store: ChannelRegistryStore
    _workflow_validator: WorkflowDefinitionValidator
    _publication_locks: dict[tuple[str, ResourceKind, str], asyncio.Lock]
    _approval_store: ApprovalStore

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
            self._evict_idle_publication_locks(exclude=key)
        return lock

    def _evict_idle_publication_locks(
        self, *, exclude: tuple[str, ResourceKind, str]
    ) -> None:
        # 超过 cap 时按插入序（最旧优先）淘汰未被持有的锁；排除当前 key 以免淘汰
        # 刚插入的锁。dict 保持插入序，list() 拷贝避免在迭代中修改 size。
        if len(self._publication_locks) <= _PUBLICATION_LOCK_CAP:
            return
        for candidate_key in list(self._publication_locks):
            if len(self._publication_locks) <= _PUBLICATION_LOCK_CAP:
                break
            if candidate_key == exclude:
                continue
            candidate = self._publication_locks.get(candidate_key)
            if candidate is not None and not candidate.locked():
                self._publication_locks.pop(candidate_key, None)

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

    async def list_all_resources(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResourceDefinition], int]:
        return await self._store.list_all_resources(
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
            await self._verify_and_consume_rollback_approval(actor, request, request.approval_id)
        return await self._commit_publication(
            actor,
            kind=request.kind,
            resource_id=request.resource_id,
            version=request.target_version,
            operation=PublicationOperation.ROLLBACK,
            approval_id=request.approval_id,
        )

    async def _verify_and_consume_rollback_approval(
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
        # A9：审批单一次性消费——校验通过后立即 CAS 置 consumed_at（fail-closed，
        # 消费先于 _commit_publication）。已消费的审批单重放 → store.consume 抛
        # ValueError → 403「已消费」。消费先于 commit：若 commit 失败，审批单已
        # burnt，操作者需重新申请；对高风险回滚而言，宁可 burnt 也不可重放。
        try:
            await self._approval_store.consume(
                approval_id,
                tenant_id=actor.tenant_id,
                consumed_at=utc_now(),
            )
        except ValueError as exc:
            raise ConsoleForbiddenError("审批已消费，不可重放") from exc

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

    async def resource_schema(self, kind: ResourceKind) -> dict[str, object]:
        # ADR-012：前端表单 schema 直接取自 spec model 的 model_json_schema()，
        # 校验模型与表单结构不可能漂移。schema 是 kind 级静态数据，无租户上下文。
        model = _definition_model(kind)
        if model is None:
            raise ConsoleValidationError(f"unsupported resource type: {kind.value}")
        return {"schema": model.model_json_schema()}

    def validate_spec_shape(
        self, kind: ResourceKind, spec: dict[str, object]
    ) -> None:
        """Product API 前置 typed 校验（TASK-004 E-01）：进入 draft 前定位字段错误。

        slug=agent_definition_invalid（agents 语义），诊断含 pydantic 字段路径；
        其它 kind 同样前置（schema 驱动表单一套行为）。
        """
        result = _validate_definition(kind, spec)
        if not result.valid:
            raise StudioSpecValidationError(
                "agent_definition_invalid：" + "；".join(result.diagnostics)
            )


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
    if kind is ResourceKind.MODEL:
        return ModelProviderDefinition
    if kind is ResourceKind.TOOL:
        return ToolDefinition
    if kind is ResourceKind.SKILL:
        return SkillDefinition
    if kind is ResourceKind.MCP:
        return MCPDefinition
    if kind is ResourceKind.SECRET:
        return SecretDefinition
    if kind is ResourceKind.PLUGIN:
        return ModelProviderDefinition
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
