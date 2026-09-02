from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from fluxion.errors.console import (
    ConsoleForbiddenError,
    ConsoleResourceNotFoundError,
    ConsoleValidationError,
    ConsoleVersionConflictError,
)
from fluxion.registry import (
    ChannelRegistryStore,
    NotFoundError,
    PublicationCommand,
    PublicationOperation,
    VersionConflictError,
)
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.services.approval_app import ApprovalStatus, ApprovalStore, utc_now
from fluxion.services.console_contracts import (
    ConsoleActor,
    DeprecateResourceVersionRequest,
    PublishResourceResult,
    PublishResourceVersionRequest,
    RollbackResourceRequest,
)
from fluxion.services.console_resource_schema import (
    _ensure_same_tenant,
    _raise_for_invalid_workflow,
    _rollback_requires_approval,
    _validate_definition,
)
from fluxion.services.release_gate import ConsoleReleaseGateBlockedError, GateDecision
from fluxion.services.workflow_app import WorkflowDefinitionValidator


class ConsoleResourceLifecycleOps:
    """资源 publish、rollback、deprecate 与 Release Gate 生命周期。"""

    _store: ChannelRegistryStore
    _approval_store: ApprovalStore
    _workflow_validator: WorkflowDefinitionValidator

    if TYPE_CHECKING:

        def _publication_lock(
            self, tenant_id: str, kind: ResourceKind, resource_id: str
        ) -> asyncio.Lock: ...

        async def _agent_reference_issues(
            self, tenant_id: str, spec: dict[str, object]
        ) -> list[str]: ...

        async def _model_definition_reference_issues(
            self, tenant_id: str, spec: dict[str, object]
        ) -> list[str]: ...

        async def _credential_issues(
            self, tenant_id: str, kind: ResourceKind, spec: dict[str, object]
        ) -> list[str]: ...

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
        # RULE-04/S-04（TASK-009 返工）：发布完整校验 fail-closed——引用完整性 +
        # 凭据可用性与 validate_publish 同源；失败不产生 published 版本。
        publish_issues: list[str] = []
        if request.kind is ResourceKind.AGENT_DEFINITION:
            publish_issues.extend(
                await self._agent_reference_issues(request.tenant_id, existing.spec_json)
            )
        if request.kind is ResourceKind.MODEL_DEFINITION:
            publish_issues.extend(
                await self._model_definition_reference_issues(
                    request.tenant_id, existing.spec_json
                )
            )
        publish_issues.extend(
            await self._credential_issues(request.tenant_id, request.kind, existing.spec_json)
        )
        if publish_issues:
            raise ConsoleValidationError("；".join(publish_issues))
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
        # Phase 5 TASK-005：Release Gate 挂 publish 管道——gate 参数存在即评估；
        # blocked → 阻断发布（score_delta 诊断入 envelope；决策留档 AuditLog）。
        await self._evaluate_release_gate(actor, request)
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

    async def _evaluate_release_gate(
        self,
        actor: ConsoleActor,
        request: PublishResourceVersionRequest,
    ) -> None:
        """Phase 5 TASK-005：请求带 gate 参数时评估 Release Gate。

        gate 未配置（service 无 ReleaseGateService）→ fail-closed 阻断；
        blocked → ConsoleReleaseGateBlockedError（envelope 带 score_delta 诊断，
        决策留档由 ReleaseGateService 写 AuditLog）。

        review P1-7：``release_gate_enforced=True`` 时 gate 从 opt-in 变强制
        策略——不带 gate 参数的 publish 同样 fail-closed 阻断（生产装配开启）。
        """
        if request.gate is None:
            if getattr(self, "_release_gate_enforced", False):
                raise ConsoleReleaseGateBlockedError(
                    GateDecision(
                        release_id=f"{request.kind.value}/{request.resource_id}@{request.version}",
                        tenant_id=request.tenant_id,
                        blocked=True,
                        score_delta=None,
                        reason="Release Gate 强制启用：publish 请求必须携带 gate 参数（基线不可用请先跑 EvalRun）",
                        candidate_run_id=None,
                        baseline_run_id=None,
                    )
                )
            return
        gate = getattr(self, "_release_gate", None)
        if gate is None:
            raise ConsoleReleaseGateBlockedError(
                GateDecision(
                    release_id=f"{request.kind.value}/{request.resource_id}@{request.version}",
                    tenant_id=request.tenant_id,
                    blocked=True,
                    score_delta=None,
                    reason="Release Gate 未配置（fail-closed）",
                    candidate_run_id=request.gate.candidate_eval_run_id,
                    baseline_run_id=request.gate.baseline_eval_run_id,
                )
            )
        decision = await gate.evaluate(
            release_id=f"{request.kind.value}/{request.resource_id}@{request.version}",
            tenant_id=request.tenant_id,
            candidate_eval_run_id=request.gate.candidate_eval_run_id,
            baseline_eval_run_id=request.gate.baseline_eval_run_id,
            threshold=request.gate.threshold,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
            trace_id=actor.trace_id,
        )
        if decision.blocked:
            raise ConsoleReleaseGateBlockedError(decision)

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
        # review 残留修复：不再宽泛捕获 RegistryStoreError 映射 409——commit_publication
        # 在此路径唯一可达的 RegistryStoreError 是 revision bump 等 infra 错误（应 500，
        # 由 console_errors 通用 Exception handler 出 INTERNAL_ERROR envelope）；真正的
        # 客户端冲突 guard（not draft / only published can be deprecated 等）全部抛
        # VersionConflictError，已被上一条映射 409。active_reference_blocked 是
        # hard_delete guard、不经 commit_publication 抛，hard-delete HTTP 端点不存在时
        # 无需映射（新增端点时再定码）。
        return PublishResourceResult(
            resource_id=commit.resource.id,
            version=commit.resource.version,
            status=commit.resource.status.value,
            publish_id=commit.publish_id,
            event_status=commit.event_status.value,
            kubernetes_workload_created=False,
        )
