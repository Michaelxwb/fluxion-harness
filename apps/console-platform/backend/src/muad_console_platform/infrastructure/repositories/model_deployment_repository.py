"""模型成员关系仓储（设计 §3.3 `control.model_deployment`）。

成员只引用 `credential_id`，**不复制任何密钥/连接字段**；同模型内成员名与凭据各自唯一，
由真实 partial unique 兜底（`uq_model_deployment_tenant_model_key` /
`uq_model_deployment_tenant_model_credential`）；本层只做带租户/模型谓词的读写，
不提交事务（提交/回滚由调用方决定）。
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ModelCredential, ModelDeployment


def _execution_member_predicates(tenant_id: str) -> tuple[Any, ...]:
    """可执行成员的谓词：成员与凭据都未删除、启用，且凭据带有非空密钥。

    停用是"暂时不可执行"，不是解绑；空密钥是坏行，`''` 不构成任何鉴权值 —— 这两种情况都
    不该被当成执行入口（调用方按 CREDENTIAL_MISSING 报错，不退回旧列/环境变量）。
    """
    return (
        ModelDeployment.tenant_id == tenant_id,
        ModelDeployment.is_deleted.is_(False),
        ModelDeployment.enabled.is_(True),
        ModelCredential.is_deleted.is_(False),
        ModelCredential.enabled.is_(True),
        ModelCredential.api_key != "",
    )


def _member_join() -> Any:
    """成员 → 凭据的同租户 JOIN（复合归属在库层已由 FK 锁定，这里只做显式连接）。"""
    return ModelDeployment.__table__.join(
        ModelCredential.__table__,
        and_(
            ModelDeployment.tenant_id == ModelCredential.tenant_id,
            ModelDeployment.credential_id == ModelCredential.id,
        ),
    )


class ModelDeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_model(
        self, tenant_id: str, model_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[ModelDeployment], int]:
        conditions = [
            ModelDeployment.tenant_id == tenant_id,
            ModelDeployment.model_id == model_id,
            ModelDeployment.is_deleted.is_(False),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(ModelDeployment).where(*conditions)
        )
        rows = await self._session.scalars(
            select(ModelDeployment)
            .where(*conditions)
            .order_by(ModelDeployment.create_time, ModelDeployment.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def get(
        self, tenant_id: str, model_id: uuid.UUID, deployment_id: uuid.UUID
    ) -> ModelDeployment | None:
        deployment: ModelDeployment | None = await self._session.scalar(
            select(ModelDeployment).where(
                ModelDeployment.id == deployment_id,
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return deployment

    async def find_by_key(self, tenant_id: str, model_id: uuid.UUID, key: str) -> ModelDeployment | None:
        deployment: ModelDeployment | None = await self._session.scalar(
            select(ModelDeployment).where(
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.key == key,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return deployment

    async def find_by_credential(
        self, tenant_id: str, model_id: uuid.UUID, credential_id: uuid.UUID
    ) -> ModelDeployment | None:
        deployment: ModelDeployment | None = await self._session.scalar(
            select(ModelDeployment).where(
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.credential_id == credential_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return deployment

    async def add(self, deployment: ModelDeployment) -> ModelDeployment:
        self._session.add(deployment)
        await self._session.flush()
        return deployment

    async def has_live_members(self, tenant_id: str, model_id: uuid.UUID) -> bool:
        """该逻辑模型是否仍有未解绑的成员（含停用成员：重新启用不需要再次声明供方）。"""
        exists = await self._session.scalar(
            select(func.count())
            .select_from(ModelDeployment)
            .where(
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return bool(exists)

    def _execution_credential_query(self, tenant_id: str) -> Any:
        return (
            select(ModelCredential)
            .select_from(_member_join())
            .where(*_execution_member_predicates(tenant_id))
        )

    async def find_execution_credential(
        self, tenant_id: str, model_id: uuid.UUID
    ) -> ModelCredential | None:
        """该模型当前可执行成员的凭据（按成员名稳定排序取第一条）。

        这是成员选取落地（按 Run 冻结）之前的**过渡解析口径**：只回答"当前有哪些成员可以
        执行"，不做加权均衡；没有可用成员即"没有执行入口"（调用方按 CREDENTIAL_MISSING
        报错，不退回任何旧列或环境变量）。
        """
        credential: ModelCredential | None = await self._session.scalar(
            self._execution_credential_query(tenant_id)
            .where(ModelDeployment.model_id == model_id)
            .order_by(ModelDeployment.key, ModelDeployment.id)
            .limit(1)
        )
        return credential

    async def find_execution_selection(
        self, tenant_id: str, model_id: uuid.UUID
    ) -> tuple[ModelDeployment, ModelCredential] | None:
        """取首个可执行成员及其凭据（一次 JOIN）；供受理材料一次解析复用。"""
        row = (
            await self._session.execute(
                self._execution_credential_query(tenant_id)
                .add_columns(ModelDeployment)
                .where(ModelDeployment.model_id == model_id)
                .order_by(ModelDeployment.key, ModelDeployment.id)
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        credential, deployment = row
        return deployment, credential

    async def find_execution_credentials(
        self, tenant_id: str, model_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ModelCredential]:
        """批量版：每个模型取其首个可执行成员凭据（`DISTINCT ON` 由稳定排序决定归属）。"""
        if not model_ids:
            return {}
        rows = await self._session.execute(
            select(ModelDeployment.model_id, ModelCredential)
            .select_from(_member_join())
            .where(
                *_execution_member_predicates(tenant_id),
                ModelDeployment.model_id.in_(model_ids),
            )
            .order_by(ModelDeployment.model_id, ModelDeployment.key, ModelDeployment.id)
            .distinct(ModelDeployment.model_id)
        )
        return {model_id: credential for model_id, credential in rows.all()}

    async def cas_update(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        deployment_id: uuid.UUID,
        expected_revision: int,
        values: dict[str, Any],
    ) -> bool:
        """条件更新做 CAS：只有 revision 仍等于 expected_revision 时才写入。

        返回是否命中（False = 期间被他人改过，调用方应抛 REVISION_CONFLICT）。
        """
        result = await self._session.execute(
            update(ModelDeployment)
            .where(
                ModelDeployment.id == deployment_id,
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.revision == expected_revision,
                ModelDeployment.is_deleted.is_(False),
            )
            .values(**values, revision=expected_revision + 1, update_time=func.now())
        )
        return cast(CursorResult[Any], result).rowcount == 1
