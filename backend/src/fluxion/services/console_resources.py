from __future__ import annotations

import asyncio

from pydantic import BaseModel, ValidationError

from fluxion.errors.console import (
    ConsoleResourceConflictError,
    ConsoleResourceNotFoundError,
    ConsoleValidationError,
    ConsoleVersionConflictError,
)
from fluxion.registry import (
    ChannelRegistryStore,
    VersionConflictError,
)
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)
from fluxion.runtime.secrets import CredentialResolver
from fluxion.services.approval_app import ApprovalStore
from fluxion.services.console_contracts import (
    ConsoleActor,
    CreateResourceDraftRequest,
    UpdateResourceDraftRequest,
)
from fluxion.services.console_resource_lifecycle import ConsoleResourceLifecycleOps
from fluxion.services.console_resource_schema import _definition_model as _schema_definition_model
from fluxion.services.console_resource_schema import (
    _ensure_same_tenant,
    _raise_for_invalid_workflow,
)
from fluxion.services.console_resource_validation import ConsoleResourceValidationOps
from fluxion.services.workflow_app import (
    WorkflowDefinitionValidator,
    WorkflowValidationResult,
)

# 长跑进程内存上限：publication lock 此前每个 (tenant, kind, resource_id) 一把且
# 从不淘汰 → dev/多租户压测下 _publication_locks 无界增长 OOM。此 cap 仅淘汰未被
# 持有的空闲锁，命中即保留。
_PUBLICATION_LOCK_CAP = 4096


def _definition_model(kind: ResourceKind) -> type[BaseModel] | None:
    """兼容入口：typed spec 分派实现位于 console_resource_schema。"""
    return _schema_definition_model(kind)


class ConsoleResourceOps(ConsoleResourceLifecycleOps, ConsoleResourceValidationOps):
    """资源生命周期操作 mixin：CRUD、publish/rollback/deprecate 与版本校验。

    由 ConsoleApplicationService 继承，依赖属性在主类 __init__ 中装配；此处仅
    声明类型，避免 mixin 直接持有构造逻辑。
    """

    _store: ChannelRegistryStore
    _workflow_validator: WorkflowDefinitionValidator
    _publication_locks: dict[tuple[str, ResourceKind, str], asyncio.Lock]
    _approval_store: ApprovalStore
    # 连接测试凭据注入（TASK-019 返工）：由装配方（dev/production bundle）注入。
    _credential_resolver: CredentialResolver | None

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
        if version is not None:
            resource = await self._store.get(
                kind,
                resource_id,
                tenant_id=actor.tenant_id,
                version=version,
            )
            if resource is None:
                raise ConsoleResourceNotFoundError()
            return resource
        # review 残留迁移发现的 UI 缺陷修复：Console 详情页打开资源时不带
        # version——store.get(None) 只查 latest PUBLISHED，刚创建的 draft 详情
        # 404（ResourcesPage 创建草稿后立即打开详情必现）。Console「打开详情」
        # 语义是当前版本（任意状态），经 list_versions 取最新一行；
        # store.get 的 latest-published 语义（resolver 消费）保持不变。
        items, _total = await self._store.list_versions(
            kind,
            resource_id,
            tenant_id=actor.tenant_id,
            offset=0,
            limit=1,
        )
        if not items:
            raise ConsoleResourceNotFoundError()
        return items[0]

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
        # console-creation-flow-fix（CF-S-01）：Console 列表语义 = 每资源「当前版本
        # （任意状态）」一行，新建 draft 立即可见；runtime/resolver 消费的
        # published-only（store.list_resources）保持不变。
        return await self._store.list_current_resources(
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
        # 同 list_resources：Console「当前版本（任意状态）」语义（CF-S-01）。
        return await self._store.list_current_resources(
            None,
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

    async def ensure_working_draft(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        resource_id: str,
    ) -> ResourceDefinition:
        """编辑已发布资源时自动创建/复用 working draft（remediation §14.3·§25）。

        - 已存在 DRAFT 版本 → 复用（不重复 fork）；
        - 否则以最新 PUBLISHED 版本为 base，fork 出 next 版本号的 DRAFT。

        已发布版本保持 immutable，绝不原地修改；working draft 对用户无感，
        用户无需理解「创建草稿」。
        """
        versions, _total = await self._store.list_versions(
            kind,
            resource_id,
            tenant_id=actor.tenant_id,
            offset=0,
            limit=100,
        )
        if not versions:
            raise ConsoleResourceNotFoundError()
        # 复用已存在的 draft（含并发下已 fork 出的同版本 draft）
        for version in versions:
            if version.status is ResourceStatus.DRAFT:
                return version
        base = max(versions, key=lambda v: int(v.version) if v.version.isdigit() else 0)
        if base.status is not ResourceStatus.PUBLISHED:
            raise ConsoleVersionConflictError("无已发布版本可 fork working draft")
        next_version = str(
            max(int(v.version) for v in versions if v.version.isdigit()) + 1
        )
        draft = ResourceDefinition(
            kind=kind,
            id=resource_id,
            tenant_id=actor.tenant_id,
            version=next_version,
            status=ResourceStatus.DRAFT,
            visibility=base.visibility,
            spec_json=dict(base.spec_json),
        )
        try:
            return await self._store.put(draft)
        except VersionConflictError:
            existing = await self._store.get(
                kind,
                resource_id,
                tenant_id=actor.tenant_id,
                version=next_version,
            )
            if existing is not None:
                return existing
            raise ConsoleResourceConflictError("working draft fork 冲突") from None
