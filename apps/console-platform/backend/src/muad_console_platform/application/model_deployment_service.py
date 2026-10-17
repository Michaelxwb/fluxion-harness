"""模型成员关系服务（设计 §3.3、§3.4 API-01）。

- 成员是**关系行**：只引用 `credential_id`，不复制密钥/连接字段；同模型内成员名与凭据
  各自唯一（partial unique 兜底），重复绑定归一到 `MODEL_DEPLOYMENT_EXISTS`；
- 核属与同供方：模型与凭据都必须同租户（复合 FK 的真实意图，服务先给明确错误码），
  `credential.provider_key` 必须等于 `model.provider_key`，否则 `MODEL_PROVIDER_MISMATCH`；
- 关系修改是**单关系**操作：POST 绑定一条、PUT 改一条（`expected_revision` CAS）、
  DELETE 解绑一条；没有覆盖整个关系集合的写入口（RULE-rel-001）；
- 审计快照只列关系字段（无密钥/连接字段可泄漏）。
"""

from __future__ import annotations

import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.model_pool import ModelDeploymentInput, ModelDeploymentUpdate
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition, ModelDeployment
from ..infrastructure.repositories.model_credential_repository import ModelCredentialRepository
from ..infrastructure.repositories.model_deployment_repository import ModelDeploymentRepository
from ..infrastructure.repositories.model_repository import ModelRepository
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "MODEL_DEPLOYMENT"
#: 去重语义由数据库 partial unique 兜底；并发落败者按这些约束名归一到同一冲突码
DUPLICATE_CONSTRAINTS = frozenset(
    {"uq_model_deployment_tenant_model_key", "uq_model_deployment_tenant_model_credential"}
)


def deployment_snapshot(deployment: ModelDeployment) -> dict[str, Any]:
    """审计快照只列关系字段：不含任何密钥/连接字段。"""
    return {
        "key": deployment.key,
        "model_id": str(deployment.model_id),
        "credential_id": str(deployment.credential_id),
        "weight": deployment.weight,
        "enabled": deployment.enabled,
        "revision": deployment.revision,
    }


def _is_duplicate_violation(exc: IntegrityError) -> bool:
    detail = str(exc.orig)
    return any(name in detail for name in DUPLICATE_CONSTRAINTS)


class ModelDeploymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._deployments = ModelDeploymentRepository(session)
        self._credentials = ModelCredentialRepository(session)
        self._models = ModelRepository(session)
        self._audit = AuditService(session)

    async def list_deployments(
        self, tenant_id: str, model_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[ModelDeployment], int]:
        await self._require_model(tenant_id, model_id)
        return await self._deployments.list_for_model(tenant_id, model_id, page, page_size)

    async def create_deployment(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        payload: ModelDeploymentInput,
        actor: AuditActor,
    ) -> ModelDeployment:
        """绑定一条成员关系：核属模型/凭据，校验同供方，拒绝重复的关系或成员名。"""
        model = await self._require_model(tenant_id, model_id)
        credential = await self._credentials.get(tenant_id, payload.credential_id)
        if credential is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if credential.provider_key != model.provider_key:
            raise AppError(ErrorCode.MODEL_PROVIDER_MISMATCH)
        await self._assert_relation_free(tenant_id, model_id, payload.key, payload.credential_id)
        deployment = ModelDeployment(
            tenant_id=tenant_id,
            key=payload.key,
            model_id=model_id,
            credential_id=payload.credential_id,
            weight=payload.weight,
            enabled=payload.enabled,
        )
        try:
            created = await self._deployments.add(deployment)
        except IntegrityError as exc:
            if not _is_duplicate_violation(exc):
                raise
            raise AppError(ErrorCode.MODEL_DEPLOYMENT_EXISTS) from exc
        await self._record_audit(tenant_id, actor, "CREATE", created, None, created)
        return created

    async def update_deployment(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        deployment_id: uuid.UUID,
        payload: ModelDeploymentUpdate,
        actor: AuditActor,
    ) -> ModelDeployment:
        """更新单条关系（成员名/权重/启用）：`expected_revision` CAS，未命中即版本冲突。"""
        await self._require_model(tenant_id, model_id)
        deployment = await self._deployments.get(tenant_id, model_id, deployment_id)
        if deployment is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = deployment_snapshot(deployment)
        by_key = await self._deployments.find_by_key(tenant_id, model_id, payload.key)
        if by_key is not None and by_key.id != deployment_id:
            raise AppError(ErrorCode.MODEL_DEPLOYMENT_EXISTS)
        values: dict[str, Any] = {
            "key": payload.key,
            "weight": payload.weight,
            "enabled": payload.enabled,
        }
        try:
            updated = await self._deployments.cas_update(
                tenant_id, model_id, deployment_id, payload.expected_revision, values
            )
        except IntegrityError as exc:
            if not _is_duplicate_violation(exc):
                raise
            raise AppError(ErrorCode.MODEL_DEPLOYMENT_EXISTS) from exc
        if not updated:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        await self._session.refresh(deployment)
        await self._record_audit(tenant_id, actor, "UPDATE", deployment, before, deployment)
        return deployment

    async def delete_deployment(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        deployment_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        """解绑单条关系（软删）：行与审计证据保留，不触碰同模型的其他成员。"""
        await self._require_model(tenant_id, model_id)
        deployment = await self._deployments.get(tenant_id, model_id, deployment_id)
        if deployment is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = deployment_snapshot(deployment)
        deployment.is_deleted = True
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", deployment, before, None)

    async def _require_model(self, tenant_id: str, model_id: uuid.UUID) -> ModelDefinition:
        """模型必须同租户存在（跨租户/不存在都按归属缺失处理；§3.4 资源缺失=COMMON_NOT_FOUND）。"""
        model = await self._models.get(tenant_id, model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return model

    async def _assert_relation_free(
        self, tenant_id: str, model_id: uuid.UUID, key: str, credential_id: uuid.UUID
    ) -> None:
        """同一模型内成员名与凭据各自至多一条关系（含软删行的重绑定允许）。"""
        if await self._deployments.find_by_key(tenant_id, model_id, key) is not None:
            raise AppError(ErrorCode.MODEL_DEPLOYMENT_EXISTS)
        if await self._deployments.find_by_credential(tenant_id, model_id, credential_id) is not None:
            raise AppError(ErrorCode.MODEL_DEPLOYMENT_EXISTS)

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        deployment: ModelDeployment,
        before: dict[str, Any] | None,
        after: ModelDeployment | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=deployment.id,
            action=action,
            before=before,
            after=deployment_snapshot(after) if after is not None else None,
        )
