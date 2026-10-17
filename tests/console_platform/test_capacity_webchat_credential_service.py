"""[B-215] 物理模型凭据 service：按 tenant/provider/Key 摘要去重、稳定计量身份与明文卫生。

真实边界（业务路径不 mock）：
- **真实 PostgreSQL**：`control.model_credential` 的 partial unique 兜底同 Key 重复登记，
  `control.model_deployment` 的引用按真实行计数；服务只 flush、不提交，提交由调用方决定；
- **真实 Console 应用服务**：直接驱动 `ModelCredentialService`，无替身、无 HTTP 替身；
- 明文卫生以真实回读的 `config_audit_log` 为证据：密钥明文与指纹都不得进入快照文本。

清库隔离：conftest 的 tenant 清理不含本任务的新表，`credential_tenant` 夹具自带
`model_deployment/model_credential/model_quota_group` 与审计行清理（tenant_id 每用例唯一）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.model_credential_service import (
    ModelCredentialService,
    compute_api_key_digest,
    derive_key_resource_id,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    ModelCredential,
    ModelDeployment,
    ModelQuotaGroup,
)
from muad_console_platform.infrastructure.repositories.model_credential_repository import (
    ModelCredentialRepository,
)
from muad_contracts.model_pool import ModelCredentialCreate
from sqlalchemy import select, text

from console_platform.conftest import TenantContext

AUDIT_RESOURCE_TYPE = "MODEL_CREDENTIAL"
PROVIDER = "openai"
SECRET_ONE = "sk-canary-credential-one"
SECRET_TWO = "sk-canary-credential-two"
SECRET_THREE = "sk-canary-credential-three"


@dataclass(frozen=True)
class CredentialContext:
    tenant_id: str
    model_id: uuid.UUID
    quota_group_id: uuid.UUID


async def _purge_tenant(tenant_id: str) -> None:
    """删本租户的成员/凭据/额度组与审计行：顺序满足外键，不影响其他租户。"""
    factory = get_session_factory()
    async with factory() as session:
        for statement in (
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def credential_tenant(tenant: TenantContext) -> AsyncIterator[CredentialContext]:
    """本用例租户 + 真实额度组行（凭据的复合 FK 目标）+ 结束清理新表行。"""
    factory = get_session_factory()
    async with factory() as session:
        group = ModelQuotaGroup(
            tenant_id=tenant.tenant_id,
            key=f"group-{uuid.uuid4().hex[:8]}",
            rpm=None,
            tpm=None,
            max_inflight=8,
        )
        session.add(group)
        await session.commit()
        quota_group_id = group.id
    try:
        yield CredentialContext(
            tenant_id=tenant.tenant_id,
            model_id=tenant.model_id,
            quota_group_id=quota_group_id,
        )
    finally:
        await _purge_tenant(tenant.tenant_id)


def _actor() -> AuditActor:
    return AuditActor(account_id=uuid.uuid4())


def _payload(
    ctx: CredentialContext,
    *,
    api_key: str = SECRET_ONE,
    key: str,
    provider_key: str = PROVIDER,
    quota_group_id: uuid.UUID | None = None,
) -> ModelCredentialCreate:
    return ModelCredentialCreate(
        key=key,
        provider_key=provider_key,
        base_url="https://model.example/v1",
        quota_group_id=quota_group_id or ctx.quota_group_id,
        max_inflight=8,
        api_key=api_key,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _key_resource_id(ctx: CredentialContext, api_key_digest: str, provider_key: str = PROVIDER) -> uuid.UUID:
    return derive_key_resource_id(ctx.tenant_id, provider_key, api_key_digest)


async def _credentials(tenant_id: str) -> list[ModelCredential]:
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.scalars(
            select(ModelCredential)
            .where(ModelCredential.tenant_id == tenant_id)
            .order_by(ModelCredential.create_time, ModelCredential.id)
        )
        return list(rows)


async def _audit_rows(tenant_id: str) -> list[dict[str, Any]]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT action, resource_id::text AS resource_id, before_json, after_json "
                "FROM control.config_audit_log "
                "WHERE tenant_id = :tenant_id AND resource_type = :resource_type "
                "ORDER BY create_time"
            ),
            {"tenant_id": tenant_id, "resource_type": AUDIT_RESOURCE_TYPE},
        )
        return [dict(row._mapping) for row in result]


def _audit_text(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, default=str)


async def test_b215_create_stores_digest_identity_and_audits_without_plaintext(
    credential_tenant: CredentialContext,
) -> None:
    """B-215：登记写入 SHA-256 摘要与稳定 key_resource_id；审计快照不含明文与指纹。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        created = await ModelCredentialService(session).create_credential(
            ctx.tenant_id, _payload(ctx, key="cred-a"), _actor()
        )
        await session.commit()
        credential_id = created.id
        assert created.api_key_digest == _digest(SECRET_ONE)
        assert compute_api_key_digest(SECRET_ONE) == _digest(SECRET_ONE), "摘要口径 = SHA-256 小写十六进制"
        assert created.api_key == SECRET_ONE and created.provider_key == PROVIDER
        assert created.max_inflight == 8 and created.quota_group_id == ctx.quota_group_id
        assert created.enabled is True and created.revision == 1
        assert SECRET_ONE not in repr(created), "repr 不得带出密钥明文"

    derived = _key_resource_id(ctx, _digest(SECRET_ONE))
    assert derived == _key_resource_id(ctx, _digest(SECRET_ONE)), "同一物理 Key 的计量身份必须可重复派生"
    assert derived != credential_id, "计量身份来自 tenant/provider/Key 指纹，不是行 ID"

    rows = await _audit_rows(ctx.tenant_id)
    assert [row["action"] for row in rows] == ["CREATE"]
    assert rows[0]["resource_id"] == str(credential_id)
    blob = _audit_text(rows)
    assert SECRET_ONE not in blob, "密钥明文不得进入 config_audit_log"
    assert _digest(SECRET_ONE) not in blob, "密钥指纹不得进入配置快照"
    assert "provider_key" in blob and "max_inflight" in blob, "快照应保留非密钥字段供审计"


async def test_b215_same_physical_key_is_rejected_within_tenant_and_provider(
    credential_tenant: CredentialContext,
) -> None:
    """B-215：同 tenant+provider+摘要重复登记被拒；key 重名被拒；换 provider/租户是不同归属。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        service = ModelCredentialService(session)
        await service.create_credential(ctx.tenant_id, _payload(ctx, key="cred-a"), _actor())

        with pytest.raises(AppError) as duplicate:
            await service.create_credential(
                ctx.tenant_id, _payload(ctx, api_key=SECRET_ONE, key="cred-b"), _actor()
            )
        assert duplicate.value.code == ErrorCode.MODEL_CREDENTIAL_EXISTS

        with pytest.raises(AppError) as key_taken:
            await service.create_credential(
                ctx.tenant_id, _payload(ctx, api_key=SECRET_TWO, key="cred-a"), _actor()
            )
        assert key_taken.value.code == ErrorCode.MODEL_CREDENTIAL_EXISTS

        # 同明文换 provider：供方不同 → 不同归属，允许
        await service.create_credential(
            ctx.tenant_id, _payload(ctx, api_key=SECRET_ONE, key="cred-c", provider_key="azure"), _actor()
        )

        # 额度组不存在/跨租户：复合 FK 目标缺失按归属缺失处理，不落行
        with pytest.raises(AppError) as missing_group:
            await service.create_credential(
                ctx.tenant_id,
                _payload(ctx, api_key=SECRET_THREE, key="cred-g", quota_group_id=uuid.uuid4()),
                _actor(),
            )
        assert missing_group.value.code == ErrorCode.COMMON_NOT_FOUND
        await session.commit()

    assert sorted(row.key for row in await _credentials(ctx.tenant_id)) == ["cred-a", "cred-c"]
    assert [row["action"] for row in await _audit_rows(ctx.tenant_id)] == ["CREATE", "CREATE"], (
        "被拒的重复登记不得留下审计行"
    )

    # 同 provider+明文但不同租户：摘要唯一带 tenant 谓词，互不遮蔽
    other_tenant = f"{ctx.tenant_id}-other"
    try:
        # 跨租户额度组先被拒：本租户凭据不能挂别的租户的组
        async with factory() as session:
            with pytest.raises(AppError) as foreign_group:
                await ModelCredentialService(session).create_credential(
                    other_tenant, _payload(ctx, key="cred-a"), _actor()
                )
            assert foreign_group.value.code == ErrorCode.COMMON_NOT_FOUND

        async with factory() as session:
            other_group = ModelQuotaGroup(
                tenant_id=other_tenant,
                key=f"group-{uuid.uuid4().hex[:8]}",
                rpm=None,
                tpm=None,
                max_inflight=8,
            )
            session.add(other_group)
            await session.commit()
            other_group_id = other_group.id
        async with factory() as session:
            await ModelCredentialService(session).create_credential(
                other_tenant, _payload(ctx, key="cred-a", quota_group_id=other_group_id), _actor()
            )
            await session.commit()
        assert len(await _credentials(other_tenant)) == 1
        assert len(await _credentials(ctx.tenant_id)) == 2
    finally:
        await _purge_tenant(other_tenant)


async def test_b215_concurrent_duplicate_is_backed_by_the_real_unique_index(
    credential_tenant: CredentialContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-215：并发窗口绕过预检时，真实 partial unique 兜底，IntegrityError 归一为同一冲突码。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        await ModelCredentialService(session).create_credential(
            ctx.tenant_id, _payload(ctx, key="cred-a"), _actor()
        )
        await session.commit()

    async def _precheck_miss(
        self: ModelCredentialRepository, tenant_id: str, provider_key: str, api_key_digest: str
    ) -> None:
        """注入竞争窗口：预检失效后由数据库唯一索引裁决（真实约束，非替身）。"""
        return None

    monkeypatch.setattr(ModelCredentialRepository, "find_by_provider_digest", _precheck_miss)
    async with factory() as session:
        with pytest.raises(AppError) as conflict:
            await ModelCredentialService(session).create_credential(
                ctx.tenant_id, _payload(ctx, api_key=SECRET_ONE, key="cred-race"), _actor()
            )
        assert conflict.value.code == ErrorCode.MODEL_CREDENTIAL_EXISTS
        await session.rollback()

    rows = await _credentials(ctx.tenant_id)
    assert [row.key for row in rows] == ["cred-a"], "落败登记不得留下第二行"


async def test_b215_delete_then_re_record_keeps_the_same_metering_identity(
    credential_tenant: CredentialContext,
) -> None:
    """B-215：删除重录不重置窗口——同一物理 Key 复用同一 key_resource_id。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        service = ModelCredentialService(session)
        first = await service.create_credential(ctx.tenant_id, _payload(ctx, key="cred-a"), _actor())
        await session.commit()
        first_id = first.id

    async with factory() as session:
        service = ModelCredentialService(session)
        await service.delete_credential(ctx.tenant_id, first_id, _actor())
        await session.commit()

    async with factory() as session:
        with pytest.raises(AppError) as gone:
            await ModelCredentialService(session).get_credential(ctx.tenant_id, first_id)
        assert gone.value.code == ErrorCode.COMMON_NOT_FOUND

    async with factory() as session:
        re_recorded = await ModelCredentialService(session).create_credential(
            ctx.tenant_id, _payload(ctx, key="cred-a"), _actor()
        )
        await session.commit()
        re_recorded_id = re_recorded.id

    assert re_recorded_id != first_id
    rows = await _credentials(ctx.tenant_id)
    assert [(row.id, row.is_deleted) for row in rows] == [(first_id, True), (re_recorded_id, False)]
    by_id = {row.id: row for row in rows}
    assert _key_resource_id(ctx, by_id[re_recorded_id].api_key_digest) == _key_resource_id(
        ctx, by_id[first_id].api_key_digest
    ), "重录后计量身份必须与首次登记一致，窗口不被重置"
    assert [row["action"] for row in await _audit_rows(ctx.tenant_id)] == ["CREATE", "DELETE", "CREATE"]


async def test_b215_rotation_keeps_row_identity_and_switches_metering_identity(
    credential_tenant: CredentialContext,
) -> None:
    """B-215：轮换更新同一行（身份不变），新 Key 换用新计量身份，旧身份不迁移。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        created = await ModelCredentialService(session).create_credential(
            ctx.tenant_id, _payload(ctx, key="cred-a"), _actor()
        )
        await session.commit()
        credential_id = created.id

    async with factory() as session:
        rotated = await ModelCredentialService(session).update_credential(
            ctx.tenant_id, credential_id, _payload(ctx, api_key=SECRET_TWO, key="cred-a"), 1, _actor()
        )
        await session.commit()
        assert rotated.id == credential_id, "轮换更新同一身份（本行），删除重录才建新行"
        assert rotated.revision == 2
        assert rotated.provider_key == PROVIDER and rotated.max_inflight == 8
        assert rotated.api_key == SECRET_TWO and rotated.api_key_digest == _digest(SECRET_TWO)
        rotated_digest = rotated.api_key_digest

    assert _key_resource_id(ctx, rotated_digest) != _key_resource_id(ctx, _digest(SECRET_ONE)), (
        "新 Key 使用新计量身份，旧在飞占用留在旧身份"
    )

    # 身份属于物理 Key 而非行：旧 Key 值重新登记复用旧身份（旧占用不被搬走）
    async with factory() as session:
        revived = await ModelCredentialService(session).create_credential(
            ctx.tenant_id, _payload(ctx, api_key=SECRET_ONE, key="cred-old"), _actor()
        )
        await session.commit()
        assert revived.api_key_digest == _digest(SECRET_ONE)
        revived_digest = revived.api_key_digest

    assert _key_resource_id(ctx, revived_digest) == _key_resource_id(ctx, _digest(SECRET_ONE))
    assert _key_resource_id(ctx, rotated_digest) != _key_resource_id(ctx, revived_digest)

    # 原地重提同值：与自身比对不算重复，摘要与计量身份不变
    async with factory() as session:
        resubmitted = await ModelCredentialService(session).update_credential(
            ctx.tenant_id, credential_id, _payload(ctx, api_key=SECRET_TWO, key="cred-a"), 2, _actor()
        )
        await session.commit()
        assert resubmitted.id == credential_id and resubmitted.revision == 3
        assert resubmitted.api_key_digest == _digest(SECRET_TWO)
        assert _key_resource_id(ctx, resubmitted.api_key_digest) == _key_resource_id(ctx, rotated_digest)


async def test_b215_update_conflicts_are_explicit(
    credential_tenant: CredentialContext,
) -> None:
    """B-215/API-16 写边界：轮换撞他行摘要、过期 revision、未知 ID 都是明确错误。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        service = ModelCredentialService(session)
        first = await service.create_credential(ctx.tenant_id, _payload(ctx, key="cred-a"), _actor())
        await service.create_credential(
            ctx.tenant_id, _payload(ctx, api_key=SECRET_TWO, key="cred-b"), _actor()
        )
        await session.commit()
        first_id = first.id

    async with factory() as session:
        service = ModelCredentialService(session)
        with pytest.raises(AppError) as duplicate:
            await service.update_credential(
                ctx.tenant_id, first_id, _payload(ctx, api_key=SECRET_TWO, key="cred-a"), 1, _actor()
            )
        assert duplicate.value.code == ErrorCode.MODEL_CREDENTIAL_EXISTS

        with pytest.raises(AppError) as stale:
            await service.update_credential(
                ctx.tenant_id, first_id, _payload(ctx, api_key=SECRET_THREE, key="cred-a"), 99, _actor()
            )
        assert stale.value.code == ErrorCode.REVISION_CONFLICT

        with pytest.raises(AppError) as missing:
            await service.update_credential(
                ctx.tenant_id, uuid.uuid4(), _payload(ctx, api_key=SECRET_THREE, key="cred-x"), 1, _actor()
            )
        assert missing.value.code == ErrorCode.COMMON_NOT_FOUND

        # 换 provider 属于新归属：同值也换计量身份（provider 参与派生）
        moved = await service.update_credential(
            ctx.tenant_id,
            first_id,
            _payload(ctx, api_key=SECRET_ONE, key="cred-a", provider_key="azure"),
            1,
            _actor(),
        )
        await session.commit()

    assert moved.id == first_id and moved.revision == 2
    assert moved.api_key_digest == _digest(SECRET_ONE)
    assert _key_resource_id(ctx, moved.api_key_digest, provider_key="azure") != _key_resource_id(
        ctx, moved.api_key_digest
    ), "provider 参与计量身份派生：换归属即换身份"


async def test_b215_delete_referenced_credential_is_rejected(
    credential_tenant: CredentialContext,
) -> None:
    """B-215/API-16：仍被成员引用的凭据不可删除，行保留且不写删除审计。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        created = await ModelCredentialService(session).create_credential(
            ctx.tenant_id, _payload(ctx, key="cred-a"), _actor()
        )
        session.add(
            ModelDeployment(
                tenant_id=ctx.tenant_id,
                key=f"member-{uuid.uuid4().hex[:8]}",
                model_id=ctx.model_id,
                credential_id=created.id,
                weight=1,
                enabled=True,
            )
        )
        await session.commit()
        credential_id = created.id

    async with factory() as session:
        with pytest.raises(AppError) as busy:
            await ModelCredentialService(session).delete_credential(ctx.tenant_id, credential_id, _actor())
        assert busy.value.code == ErrorCode.MODEL_CREDENTIAL_IN_USE

    rows = await _credentials(ctx.tenant_id)
    assert [(row.id, row.is_deleted) for row in rows] == [(credential_id, False)]
    assert [row["action"] for row in await _audit_rows(ctx.tenant_id)] == ["CREATE"]


async def test_b215_list_and_get_are_tenant_scoped_and_skip_soft_deleted(
    credential_tenant: CredentialContext,
) -> None:
    """B-215 读取面：分页按租户、软删不出现，未知 ID 明确 404。"""
    ctx = credential_tenant
    factory = get_session_factory()
    async with factory() as session:
        service = ModelCredentialService(session)
        await service.create_credential(ctx.tenant_id, _payload(ctx, key="cred-a"), _actor())
        second = await service.create_credential(
            ctx.tenant_id, _payload(ctx, api_key=SECRET_TWO, key="cred-b"), _actor()
        )
        await service.delete_credential(ctx.tenant_id, second.id, _actor())
        await session.commit()

    async with factory() as session:
        service = ModelCredentialService(session)
        items, total = await service.list_credentials(ctx.tenant_id, 1, 20)
        assert total == 1 and [row.key for row in items] == ["cred-a"]

        page_items, page_total = await service.list_credentials(ctx.tenant_id, 1, 1)
        assert page_total == 1 and len(page_items) == 1
        empty, empty_total = await service.list_credentials(ctx.tenant_id, 2, 1)
        assert empty_total == 1 and empty == []

        with pytest.raises(AppError) as missing:
            await service.get_credential(ctx.tenant_id, uuid.uuid4())
        assert missing.value.code == ErrorCode.COMMON_NOT_FOUND

        with pytest.raises(AppError) as deleted:
            await service.get_credential(ctx.tenant_id, second.id)
        assert deleted.value.code == ErrorCode.COMMON_NOT_FOUND
