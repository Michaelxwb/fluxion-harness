import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition
from ..infrastructure.repositories.model_repository import ModelRepository
from .audit_service import AuditActor, AuditService
from .dto import ModelCreateRequest, ModelUpdateRequest

AUDIT_RESOURCE_TYPE = "MODEL"


def model_snapshot(model: ModelDefinition) -> dict[str, Any]:
    return {
        "key": model.key,
        "name": model.name,
        "protocol": model.protocol,
        "model_id": model.model_id,
        "base_url": model.base_url,
        "params": model.params_json,
        "revision": model.revision,
        "enabled": model.enabled,
    }


def model_configured(model: ModelDefinition) -> bool:
    return bool(model.api_key)


class ModelService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._models = ModelRepository(session)
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
            raise AppError(ErrorCode.COMMON_CONFLICT, message_args={"key": payload.key})
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=payload.key,
            name=payload.name,
            protocol=payload.protocol,
            model_id=payload.model_id,
            base_url=payload.base_url,
            api_key=payload.api_key,
            params_json=payload.params,
            enabled=payload.enabled,
        )
        try:
            created = await self._models.add(model)
        except IntegrityError as exc:
            raise AppError(ErrorCode.COMMON_CONFLICT, message_args={"key": payload.key}) from exc
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
        if payload.expected_revision != model.revision:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        before = model_snapshot(model)
        if payload.name is not None:
            model.name = payload.name
        if payload.model_id is not None:
            model.model_id = payload.model_id
        if payload.base_url is not None:
            model.base_url = payload.base_url
        if payload.api_key:
            model.api_key = payload.api_key
        if payload.params is not None:
            model.params_json = payload.params
        if payload.enabled is not None:
            model.enabled = payload.enabled
        model.revision += 1
        model.last_test_status = "UNTESTED"
        model.last_test_at = None
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "UPDATE", model, before, model)
        return model

    async def delete_model(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        model = await self.get_model(tenant_id, model_id)
        references = await self._models.count_agent_references(model.id)
        if references > 0:
            raise AppError(
                ErrorCode.COMMON_CONFLICT,
                message_args={"model_key": model.key, "agent_count": references},
            )
        before = model_snapshot(model)
        model.is_deleted = True
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", model, before, None)

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
