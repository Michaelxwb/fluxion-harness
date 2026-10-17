"""[B-217] 模型成员关系：只引用物理凭据、同供方/租户核属、单关系 POST/DELETE 与权重版本冲突。

真实边界（业务路径不 mock）：
- **真实 Console HTTP/应用服务**：ASGI 直连真实应用，登录会话裁决 ADMIN 角色与 CSRF；
- **真实 PostgreSQL**：成员/凭据/逻辑模型与审计都写真实行；`model_deployment` 的
  partial unique（同模型同 key、同模型同 credential）与复合 FK（模型/凭据必须同租户）
  由数据库真实裁决；「成员不复制 Key」以表结构（information_schema）+ 响应文本双重断言；
- 关系的加/改/删走单关系端点，并由独立请求事务完成（RULE-rel-001）；集合整体 PUT 必须不可用。

清库隔离：`relation_tenant` 夹具自带 model_deployment/model_credential/model_definition/
config_audit_log 清理（tenant_id 每用例唯一）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_BUILDER, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    ModelCredential,
    ModelDefinition,
)
from muad_console_platform.main import app
from sqlalchemy import text

from console_platform.conftest import ADMIN_PASSWORD, TenantContext

URL = "/api/v1/models"
PROVIDER = "openai"
OTHER_PROVIDER = "azure"
SECRET = "sk-canary-deployment-relations"
SECRET_TWO = "sk-canary-deployment-relations-two"
SECRET_ROTATED = "sk-canary-deployment-relations-rotated"
SECRET_FOREIGN = "sk-canary-deployment-relations-foreign"
MEMBER_FIELDS = {"id", "key", "model_id", "credential_id", "weight", "enabled", "revision"}


@dataclass(frozen=True)
class RelationContext:
    tenant_id: str
    model_id: uuid.UUID
    other_provider_model_id: uuid.UUID
    credential_id: uuid.UUID
    second_credential_id: uuid.UUID
    other_provider_credential_id: uuid.UUID


def _h(ctx: RelationContext) -> dict[str, str]:
    """本用例租户头：真实登录会话在这里按租户定位账号。"""
    return {"X-Tenant-Id": ctx.tenant_id}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _purge_tenant(tenant_id: str) -> None:
    """删本租户的成员/凭据/模型与审计行：顺序满足外键，不影响其他租户。"""
    factory = get_session_factory()
    async with factory() as session:
        for statement in (
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def relation_tenant(tenant: TenantContext) -> AsyncIterator[RelationContext]:
    """本用例租户 + 供方一致的模型/凭据 + 供方不一致的对照组 + 结束清理。"""
    factory = get_session_factory()
    async with factory() as session:
        model = ModelDefinition(
            tenant_id=tenant.tenant_id,
            key=f"model-{uuid.uuid4().hex[:8]}",
            name="Pooled Model",
            model_id="gpt-4o-mini",
            provider_key=PROVIDER,
            base_url="https://model.example/v1",
            enabled=True,
        )
        other_model = ModelDefinition(
            tenant_id=tenant.tenant_id,
            key=f"model-{uuid.uuid4().hex[:8]}",
            name="Azure Model",
            model_id="gpt-4o-mini",
            provider_key=OTHER_PROVIDER,
            base_url="https://model.example/v1",
            enabled=True,
        )
        credentials = [
            ModelCredential(
                tenant_id=tenant.tenant_id,
                key=f"cred-{uuid.uuid4().hex[:8]}",
                provider_key=PROVIDER,
                base_url="https://model.example/v1",
                api_key=secret,
                api_key_digest=_digest(secret),
                quota_group_id=None,
                max_inflight=8,
                enabled=True,
            )
            for secret in (SECRET, SECRET_TWO)
        ]
        other_credential = ModelCredential(
            tenant_id=tenant.tenant_id,
            key=f"cred-{uuid.uuid4().hex[:8]}",
            provider_key=OTHER_PROVIDER,
            base_url="https://model.example/v1",
            api_key="sk-canary-deployment-relations-azure",
            api_key_digest=_digest("sk-canary-deployment-relations-azure"),
            quota_group_id=None,
            max_inflight=8,
            enabled=True,
        )
        session.add_all([model, other_model, other_credential, *credentials])
        await session.commit()
        context = RelationContext(
            tenant_id=tenant.tenant_id,
            model_id=model.id,
            other_provider_model_id=other_model.id,
            credential_id=credentials[0].id,
            second_credential_id=credentials[1].id,
            other_provider_credential_id=other_credential.id,
        )
    try:
        yield context
    finally:
        await _purge_tenant(tenant.tenant_id)


@pytest.fixture
async def builder_client(relation_tenant: RelationContext) -> AsyncIterator[AsyncClient]:
    """同租户 BUILDER 会话：非 ADMIN 边界以真实角色裁决为准，不伪造身份。"""
    username = f"builder-{uuid.uuid4()}"
    factory = get_session_factory()
    async with factory() as session:
        account = ConsoleAccount(
            tenant_id=relation_tenant.tenant_id,
            username=username,
            display_name="Console Builder",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_BUILDER,
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login = await http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": ADMIN_PASSWORD},
            headers=_h(relation_tenant),
        )
        assert login.status_code == 200
        csrf_token = http_client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        http_client.headers[CSRF_HEADER] = csrf_token
        yield http_client
    async with factory() as session:
        for statement in (
            "DELETE FROM control.console_session WHERE account_id = :account_id",
            "DELETE FROM control.console_account WHERE id = :account_id",
        ):
            await session.execute(text(statement), {"account_id": account_id})
        await session.commit()


def _member_body(
    ctx: RelationContext,
    *,
    key: str = "member-a",
    credential_id: uuid.UUID | None = None,
    weight: int = 1,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "credential_id": str(credential_id or ctx.credential_id),
        "weight": weight,
        "enabled": enabled,
    }


async def _bind(
    client: AsyncClient, ctx: RelationContext, model_id: uuid.UUID | None = None, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        f"{URL}/{model_id or ctx.model_id}/deployments", json=_member_body(ctx, **overrides), headers=_h(ctx)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _list(
    client: AsyncClient, ctx: RelationContext, model_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    response = await client.get(
        f"{URL}/{model_id or ctx.model_id}/deployments", params={"page_size": 100}, headers=_h(ctx)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


async def _deployment_rows(tenant_id: str, model_id: uuid.UUID) -> list[dict[str, Any]]:
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT id, key, credential_id, weight, enabled, revision, is_deleted "
                "FROM control.model_deployment "
                "WHERE tenant_id = :tenant_id AND model_id = :model_id "
                "ORDER BY create_time, id"
            ),
            {"tenant_id": tenant_id, "model_id": model_id},
        )
        return [dict(row) for row in rows.mappings()]


async def _audit_actions(tenant_id: str) -> list[str]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            await session.scalars(
                text(
                    "SELECT action FROM control.config_audit_log "
                    "WHERE tenant_id = :tenant_id AND resource_type = 'MODEL_DEPLOYMENT' "
                    "ORDER BY create_time"
                ),
                {"tenant_id": tenant_id},
            )
        )


async def test_b217_bind_member_references_credential_without_copying_key(
    client: AsyncClient, relation_tenant: RelationContext
) -> None:
    """B-217：成员只引用 credential，不复制 Key；响应与表结构都不含密钥/连接副本。"""
    ctx = relation_tenant
    member = await _bind(client, ctx, weight=3)
    assert set(member) == MEMBER_FIELDS, "成员投影恰为关系字段：多一个字段都是复制面"
    assert member["weight"] == 3 and member["enabled"] is True and member["revision"] == 1
    assert member["model_id"] == str(ctx.model_id)
    assert member["credential_id"] == str(ctx.credential_id)
    assert SECRET not in json.dumps(member) and "api_key" not in json.dumps(member)

    rows = await _deployment_rows(ctx.tenant_id, ctx.model_id)
    assert len(rows) == 1
    assert str(rows[0]["credential_id"]) == str(ctx.credential_id)
    assert rows[0]["weight"] == 3 and rows[0]["is_deleted"] is False

    factory = get_session_factory()
    async with factory() as session:
        columns = set(
            await session.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'control' AND table_name = 'model_deployment'"
                )
            )
        )
    assert not columns & {"api_key", "api_key_digest", "base_url", "provider_key"}, (
        "成员表没有可承载密钥/连接副本的列：Key 只在 credential 行"
    )

    listing = await client.get(f"{URL}/{ctx.model_id}/deployments", headers=_h(ctx))
    assert SECRET not in listing.text, "成员读取不含密钥明文"


async def test_b217_provider_and_tenancy_are_verified(
    client: AsyncClient, relation_tenant: RelationContext
) -> None:
    """B-217：同供方校验与逻辑模型/凭据的租户核属都必须明确失败且不落行。"""
    ctx = relation_tenant
    mismatch = await client.post(
        f"{URL}/{ctx.model_id}/deployments",
        json=_member_body(ctx, credential_id=ctx.other_provider_credential_id),
        headers=_h(ctx),
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "MODEL_PROVIDER_MISMATCH"

    unknown_model = await client.post(
        f"{URL}/{uuid.uuid4()}/deployments", json=_member_body(ctx), headers=_h(ctx)
    )
    assert unknown_model.status_code == 404
    assert unknown_model.json()["code"] == "COMMON_NOT_FOUND"

    unknown_credential = await client.post(
        f"{URL}/{ctx.model_id}/deployments",
        json=_member_body(ctx, credential_id=uuid.uuid4()),
        headers=_h(ctx),
    )
    assert unknown_credential.status_code == 404
    assert unknown_credential.json()["code"] == "COMMON_NOT_FOUND"

    assert await _deployment_rows(ctx.tenant_id, ctx.model_id) == [], "被拒绑定不得留下关系行"

    # 跨租户：异租户的模型/凭据都按归属缺失处理，且异租户行不被改动
    other_tenant = f"{ctx.tenant_id}-other"
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            ModelDefinition(
                tenant_id=other_tenant,
                key=f"model-{uuid.uuid4().hex[:8]}",
                name="Foreign Model",
                model_id="gpt-4o-mini",
                provider_key=PROVIDER,
                base_url="https://model.example/v1",
                enabled=True,
            )
        )
        session.add(
            ModelCredential(
                tenant_id=other_tenant,
                key=f"cred-{uuid.uuid4().hex[:8]}",
                provider_key=PROVIDER,
                base_url="https://model.example/v1",
                api_key=SECRET_FOREIGN,
                api_key_digest=_digest(SECRET_FOREIGN),
                quota_group_id=None,
                max_inflight=8,
                enabled=True,
            )
        )
        await session.commit()
        foreign_model_id = await session.scalar(
            text("SELECT id FROM control.model_definition WHERE tenant_id = :tenant_id"),
            {"tenant_id": other_tenant},
        )
        foreign_credential_id = await session.scalar(
            text("SELECT id FROM control.model_credential WHERE tenant_id = :tenant_id"),
            {"tenant_id": other_tenant},
        )
    try:
        foreign_model = await client.post(
            f"{URL}/{foreign_model_id}/deployments", json=_member_body(ctx), headers=_h(ctx)
        )
        assert foreign_model.status_code == 404
        assert foreign_model.json()["code"] == "COMMON_NOT_FOUND"
        foreign_credential = await client.post(
            f"{URL}/{ctx.model_id}/deployments",
            json=_member_body(ctx, credential_id=uuid.UUID(str(foreign_credential_id))),
            headers=_h(ctx),
        )
        assert foreign_credential.status_code == 404
        assert foreign_credential.json()["code"] == "COMMON_NOT_FOUND"
        async with factory() as session:
            foreign_deployments = await session.scalar(
                text("SELECT count(*) FROM control.model_deployment WHERE tenant_id = :tenant_id"),
                {"tenant_id": other_tenant},
            )
        assert foreign_deployments == 0
    finally:
        await _purge_tenant(other_tenant)


async def test_b217_duplicate_member_key_or_credential_conflicts(
    client: AsyncClient, relation_tenant: RelationContext
) -> None:
    """B-217：同模型内成员 key 与 credential 各自唯一；软删后同 key/同凭据可重绑。"""
    ctx = relation_tenant
    first = await _bind(client, ctx, key="member-a")

    duplicate_key = await client.post(
        f"{URL}/{ctx.model_id}/deployments",
        json=_member_body(ctx, key="member-a", credential_id=ctx.second_credential_id),
        headers=_h(ctx),
    )
    assert duplicate_key.status_code == 409
    assert duplicate_key.json()["code"] == "MODEL_DEPLOYMENT_EXISTS"

    duplicate_credential = await client.post(
        f"{URL}/{ctx.model_id}/deployments",
        json=_member_body(ctx, key="member-b", credential_id=ctx.credential_id),
        headers=_h(ctx),
    )
    assert duplicate_credential.status_code == 409
    assert duplicate_credential.json()["code"] == "MODEL_DEPLOYMENT_EXISTS"

    rows = await _deployment_rows(ctx.tenant_id, ctx.model_id)
    assert len(rows) == 1 and rows[0]["is_deleted"] is False, "被拒绑定不得建第二行"
    assert await _audit_actions(ctx.tenant_id) == ["CREATE"], "被拒绑定不得写审计"

    deleted = await client.delete(f"{URL}/{ctx.model_id}/deployments/{first['id']}", headers=_h(ctx))
    assert deleted.status_code == 200 and deleted.json()["data"]["deleted"] is True
    rebound = await _bind(client, ctx, key="member-a")
    assert rebound["id"] != first["id"], "重绑是新的关系行；软删行保留证据"
    rows = await _deployment_rows(ctx.tenant_id, ctx.model_id)
    assert [row["is_deleted"] for row in rows] == [True, False]


async def test_b217_single_relation_mutations_never_replace_the_set(
    client: AsyncClient, relation_tenant: RelationContext
) -> None:
    """B-217/RULE-rel-001：单关系 PUT/DELETE 独立生效；集合整体 PUT 不可用，权重的版本冲突可见。"""
    ctx = relation_tenant
    first = await _bind(client, ctx, key="member-a")
    second = await _bind(client, ctx, key="member-b", credential_id=ctx.second_credential_id)

    updated = await client.put(
        f"{URL}/{ctx.model_id}/deployments/{first['id']}",
        json={"key": "member-a", "weight": 5, "enabled": False, "expected_revision": 1},
        headers=_h(ctx),
    )
    assert updated.status_code == 200, updated.text
    item = updated.json()["data"]
    assert set(item) == MEMBER_FIELDS
    assert item["weight"] == 5 and item["enabled"] is False and item["revision"] == 2

    intact = await _list(client, ctx)
    by_key = {member["key"]: member for member in intact}
    assert len(intact) == 2, "单关系修改不得影响同模型的另一条关系"
    assert by_key["member-b"]["weight"] == 1 and by_key["member-b"]["revision"] == 1
    assert by_key["member-b"]["enabled"] is True

    stale = await client.put(
        f"{URL}/{ctx.model_id}/deployments/{first['id']}",
        json={"key": "member-a", "weight": 1, "enabled": True, "expected_revision": 1},
        headers=_h(ctx),
    )
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"

    rename_collision = await client.put(
        f"{URL}/{ctx.model_id}/deployments/{first['id']}",
        json={"key": "member-b", "weight": 5, "enabled": False, "expected_revision": 2},
        headers=_h(ctx),
    )
    assert rename_collision.status_code == 409
    assert rename_collision.json()["code"] == "MODEL_DEPLOYMENT_EXISTS"

    collection_put = await client.put(
        f"{URL}/{ctx.model_id}/deployments",
        json=[_member_body(ctx, key="member-a"), _member_body(ctx, key="member-b")],
        headers=_h(ctx),
    )
    assert collection_put.status_code == 405, "禁止用全量 PUT 覆盖整个关系集合（RULE-rel-001）"

    rows = await _deployment_rows(ctx.tenant_id, ctx.model_id)
    assert [(row["key"], row["weight"], row["enabled"], row["revision"]) for row in rows] == [
        ("member-a", 5, False, 2),
        ("member-b", 1, True, 1),
    ], "被拒写入不得改动已提交关系"

    removed = await client.delete(f"{URL}/{ctx.model_id}/deployments/{second['id']}", headers=_h(ctx))
    assert removed.status_code == 200 and removed.json()["data"]["deleted"] is True
    remaining = await _list(client, ctx)
    assert [member["key"] for member in remaining] == ["member-a"], "解绑单关系不连带其他成员"
    gone = await client.delete(f"{URL}/{ctx.model_id}/deployments/{second['id']}", headers=_h(ctx))
    assert gone.status_code == 404 and gone.json()["code"] == "COMMON_NOT_FOUND"
    assert await _audit_actions(ctx.tenant_id) == ["CREATE", "CREATE", "UPDATE", "DELETE"]


async def test_b217_member_identity_survives_credential_rotation(
    client: AsyncClient, relation_tenant: RelationContext
) -> None:
    """B-217：轮换凭据密钥不触碰成员关系（关系不是密钥副本，不需要跟随更新）。"""
    ctx = relation_tenant
    member = await _bind(client, ctx, key="member-a", weight=2)

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE control.model_credential "
                "SET api_key = :api_key, api_key_digest = :digest, revision = revision + 1 "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {
                "api_key": SECRET_ROTATED,
                "digest": _digest(SECRET_ROTATED),
                "id": ctx.credential_id,
                "tenant_id": ctx.tenant_id,
            },
        )
        await session.commit()

    listing = await client.get(f"{URL}/{ctx.model_id}/deployments", headers=_h(ctx))
    assert listing.status_code == 200
    item = listing.json()["data"]["items"][0]
    assert item["id"] == member["id"] and item["credential_id"] == str(ctx.credential_id)
    assert item["weight"] == 2 and item["revision"] == 1, "轮换不递增成员 revision"
    rotated_absent = all(s not in listing.text for s in (SECRET, SECRET_TWO, SECRET_ROTATED))
    assert rotated_absent, "成员读取不因轮换而出现任何密钥明文"


async def test_b217_writes_are_admin_only_and_csrf_guarded(
    client: AsyncClient, builder_client: AsyncClient, relation_tenant: RelationContext
) -> None:
    """B-217：成员关系属 ADMIN 维护面；写请求在 ADMIN 会话下仍需正确 CSRF。"""
    ctx = relation_tenant
    builder_read = await builder_client.get(f"{URL}/{ctx.model_id}/deployments", headers=_h(ctx))
    assert builder_read.status_code == 403
    assert builder_read.json()["code"] == "FORBIDDEN", "非 ADMIN 读取成员关系也不放行"
    builder_write = await builder_client.post(
        f"{URL}/{ctx.model_id}/deployments", json=_member_body(ctx), headers=_h(ctx)
    )
    assert builder_write.status_code == 403 and builder_write.json()["code"] == "FORBIDDEN"

    csrf = client.headers.pop(CSRF_HEADER)
    try:
        missing = await client.post(
            f"{URL}/{ctx.model_id}/deployments", json=_member_body(ctx), headers=_h(ctx)
        )
    finally:
        client.headers[CSRF_HEADER] = csrf
    assert missing.status_code == 403 and missing.json()["code"] == "FORBIDDEN"

    wrong = await client.post(
        f"{URL}/{ctx.model_id}/deployments",
        json=_member_body(ctx),
        headers={**_h(ctx), CSRF_HEADER: f"{csrf}-stale"},
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "FORBIDDEN"
    assert await _deployment_rows(ctx.tenant_id, ctx.model_id) == [], "被拒写请求不得留下关系行"

    bound = await _bind(client, ctx)
    assert bound["key"] == "member-a", "带正确 CSRF 的 ADMIN 写请求正常受理"
