import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition
from ..infrastructure.repositories.model_deployment_repository import ModelDeploymentRepository
from ..infrastructure.repositories.model_repository import ModelRepository
from .audit_service import AuditActor, AuditService
from .dto import ModelCreateRequest, ModelUpdateRequest

AUDIT_RESOURCE_TYPE = "MODEL"


def model_snapshot(model: ModelDefinition) -> dict[str, Any]:
    """审计快照：只列逻辑字段；连接与密钥的权威在 `model_credential`，不在此投影。"""
    return {
        "key": model.key,
        "name": model.name,
        "protocol": model.protocol,
        "model_id": model.model_id,
        "provider_key": model.provider_key,
        "params": model.params_json,
        "revision": model.revision,
        "enabled": model.enabled,
    }


class ModelService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._models = ModelRepository(session)
        self._deployments = ModelDeploymentRepository(session)
        self._audit = AuditService(session)

    async def list_models(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None,
        enabled: bool | None,
        last_test_status: str | None,
    ) -> tuple[list[ModelDefinition], int]:
        return await self._models.list(tenant_id, page, page_size, keyword, enabled, last_test_status)

    async def get_model(self, tenant_id: str, model_id: uuid.UUID) -> ModelDefinition:
        model = await self._models.get(tenant_id, model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return model

    async def create_model(
        self,
        tenant_id: str,
        payload: ModelCreateRequest,
        actor: AuditActor,
    ) -> ModelDefinition:
        if await self._models.find_by_key(tenant_id, payload.key) is not None:
            raise AppError(ErrorCode.MODEL_KEY_EXISTS, message_args={"key": payload.key})
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=payload.key,
            name=payload.name,
            protocol=payload.protocol,
            model_id=payload.model_id,
            provider_key=payload.provider_key,
            params_json=payload.params,
            enabled=payload.enabled,
        )
        try:
            created = await self._models.add(model)
        except IntegrityError as exc:
            raise AppError(ErrorCode.MODEL_KEY_EXISTS, message_args={"key": payload.key}) from exc
        await self._record_audit(tenant_id, actor, "CREATE", created, None, created)
        return created

    async def update_model(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        payload: ModelUpdateRequest,
        actor: AuditActor,
    ) -> ModelDefinition:
        model = await self.get_model(tenant_id, model_id)
        before = model_snapshot(model)
        values: dict[str, Any] = {}
        if payload.name is not None:
            values["name"] = payload.name
        if payload.model_id is not None:
            values["model_id"] = payload.model_id
        values.update(await self._provider_values(tenant_id, model, payload.provider_key))
        if payload.params is not None:
            values["params_json"] = payload.params
        if payload.enabled is not None:
            values["enabled"] = payload.enabled
        # 条件更新做 CAS（WHERE revision=expected），避免 read-then-write 丢更新
        updated = await self._models.cas_update(
            tenant_id, model_id, payload.expected_revision, values
        )
        if not updated:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        await self._session.refresh(model)
        await self._record_audit(tenant_id, actor, "UPDATE", model, before, model)
        return model

    async def delete_model(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        model = await self.get_model(tenant_id, model_id)
        references = await self._models.count_agent_references(tenant_id, model.id)
        if references > 0:
            raise AppError(
                ErrorCode.MODEL_IN_USE,
                message_args={"model_key": model.key, "agent_count": references},
            )
        before = model_snapshot(model)
        model.is_deleted = True
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", model, before, None)

    async def _provider_values(
        self, tenant_id: str, model: ModelDefinition, provider_key: str | None
    ) -> dict[str, Any]:
        """供方标识随逻辑模型维护：无成员时可声明/更正，已有成员后不得改动。

        成员（`model_deployment`）的凭据必须与逻辑模型同供方（绑定时的 422 规则）；
        改动供方会让既有成员悄悄失效，因此只允许在**没有活跃成员**时声明，之后要改先解绑。
        相同值是无副作用的重复声明，不视为改动。
        """
        if provider_key is None or provider_key == model.provider_key:
            return {}
        if await self._deployments.has_live_members(tenant_id, model.id):
            raise AppError(ErrorCode.COMMON_CONFLICT)
        return {"provider_key": provider_key}

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        model: ModelDefinition,
        before: dict[str, Any] | None,
        after: ModelDefinition | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=model.id,
            action=action,
            before=before,
            after=model_snapshot(after) if after is not None else None,
        )
