"""[B-218] 共享账号额度组管理：限流维度的真实存储、降额排空与幂等创建。

真实边界（业务路径不 mock）：
- **真实 Console HTTP/应用服务**：ASGI 直连真实应用，真实登录会话裁决 ADMIN 角色与 CSRF；
- **真实 PostgreSQL**：额度组、凭据引用与 Runtime 的 `runtime.model_call_lease` 都用真实行构造，
  revision CAS、partial unique、复合 FK 与 `control.skill_import_idempotency` 均由数据库裁决；
- `rpm`/`tpm` 的 `null` 在 SQL 层真实落 NULL，读取投影与库内回读都核对「未设限 ≠ 零额度」。

清库隔离：conftest 的 tenant 清理不含模型池新表，`quota_tenant` 夹具自带
model_credential/model_quota_group/skill_import_idempotency/config_audit_log 与
runtime.model_call_lease 的清理（tenant_id 每用例唯一）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_BUILDER, ConsoleAccount
from muad_console_platform.infrastructure.models.control import ModelCredential
from muad_console_platform.main import app
from sqlalchemy import text

from console_platform.conftest import ADMIN_PASSWORD, TenantContext

URL = "/api/v1/model-quota-groups"
READ_FIELDS = {"id", "key", "max_inflight", "rpm", "tpm", "revision"}
PROVIDER = "openai"
SECRET = "sk-canary-quota-group-api"


def _h(ctx: TenantContext) -> dict[str, str]:
    """本用例租户头：真实登录会话在这里按租户定位账号。"""
    return {"X-Tenant-Id": ctx.tenant_id}


async def _purge_tenant(tenant_id: str) -> None:
    """删本租户的在飞租约/凭据/额度组/幂等回执/审计，不影响其他租户。"""
    factory = get_session_factory()
    async with factory() as session:
        for statement in (
            "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
            "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def quota_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """本用例租户；结束清理模型池新表与在飞租约行。"""
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


@pytest.fixture
async def builder_client(quota_tenant: TenantContext) -> AsyncIterator[AsyncClient]:
    """同租户 BUILDER 会话：ADMIN 边界以真实登录角色的裁决为准，不伪造身份。"""
    username = f"builder-{uuid.uuid4()}"
    factory = get_session_factory()
    async with factory() as session:
        account = ConsoleAccount(
            tenant_id=quota_tenant.tenant_id,
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
            headers=_h(quota_tenant),
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


def _payload(
    *,
    key: str = "group-a",
    max_inflight: int = 8,
    rpm: int | None = 600,
    tpm: int | None = None,
) -> dict[str, Any]:
    return {"key": key, "max_inflight": max_inflight, "rpm": rpm, "tpm": tpm}


def _put_body(item: dict[str, Any], *, expected_revision: int, **overrides: Any) -> dict[str, Any]:
    """整份字段替换 + expected_revision：缺省沿用读取投影里的当前值。"""
    body = {name: item[name] for name in ("key", "max_inflight", "rpm", "tpm")}
    body["expected_revision"] = expected_revision
    body.update(overrides)
    return body


async def _create(client: AsyncClient, ctx: TenantContext, **overrides: Any) -> dict[str, Any]:
    response = await client.post(URL, json=_payload(**overrides), headers=_h(ctx))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _get_item(client: AsyncClient, ctx: TenantContext, group_id: str) -> dict[str, Any]:
    response = await client.get(f"{URL}/{group_id}", headers=_h(ctx))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _db_group(tenant_id: str, group_id: str | uuid.UUID) -> dict[str, Any] | None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT key, max_inflight, rpm, tpm, revision, is_deleted "
                "FROM control.model_quota_group WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": uuid.UUID(str(group_id)), "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def _insert_lease(ctx: TenantContext, group_id: str | uuid.UUID, *, state: str = "ACTIVE") -> uuid.UUID:
    """真实在飞租约（Runtime 表）：按 quota_group_id 归属，ACTIVE/UNKNOWN 都保留占用。"""
    lease_id = uuid.uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO runtime.model_call_lease "
                "(id, tenant_id, call_attempt_id, probe_id, deployment_id, credential_id, "
                " key_resource_id, quota_group_id, credential_revision, workload, lease_owner, "
                " lease_until, reserved_tokens, state) "
                "VALUES (:id, :tenant_id, :attempt_id, :probe_id, :deployment_id, :credential_id, "
                " :key_resource_id, :quota_group_id, 1, 'PROBE', 'b218-test', "
                " now() + interval '5 minutes', 1, :state)"
            ),
            {
                "id": lease_id,
                "tenant_id": ctx.tenant_id,
                "attempt_id": uuid.uuid4(),
                "probe_id": uuid.uuid4(),
                "deployment_id": uuid.uuid4(),
                "credential_id": uuid.uuid4(),
                "key_resource_id": uuid.uuid4(),
                "quota_group_id": uuid.UUID(str(group_id)),
                "state": state,
            },
        )
        await session.commit()
    return lease_id


async def _set_lease_state(lease_id: uuid.UUID, state: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("UPDATE runtime.model_call_lease SET state = :state WHERE id = :id"),
            {"state": state, "id": lease_id},
        )
        await session.commit()


async def test_b218_admin_and_csrf_guard_every_write(
    client: AsyncClient, builder_client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218：整组仅 ADMIN；不安全方法在 ADMIN 会话下仍必须携带正确 CSRF。"""
    ctx = quota_tenant
    body = _payload()
    builder_read = await builder_client.get(URL, headers=_h(ctx))
    assert builder_read.status_code == 403
    assert builder_read.json()["code"] == "FORBIDDEN", "非 ADMIN 连读取都不放行，写更不放行"
    builder_write = await builder_client.post(URL, json=body, headers=_h(ctx))
    assert builder_write.status_code == 403 and builder_write.json()["code"] == "FORBIDDEN"

    csrf = client.headers.pop(CSRF_HEADER)
    try:
        missing = await client.post(URL, json=body, headers=_h(ctx))
    finally:
        client.headers[CSRF_HEADER] = csrf
    assert missing.status_code == 403
    assert missing.json()["code"] == "FORBIDDEN", "缺 CSRF 头的写请求不得落库"

    wrong = await client.post(URL, json=body, headers={**_h(ctx), CSRF_HEADER: f"{csrf}-stale"})
    assert wrong.status_code == 403 and wrong.json()["code"] == "FORBIDDEN"
    listing = await client.get(URL, headers=_h(ctx))
    assert listing.json()["data"]["total"] == 0, "被 CSRF 拒绝的请求不得留下任何行"

    created = await _create(client, ctx)
    assert created["revision"] == 1, "带正确 CSRF 的 ADMIN 写请求正常受理"


async def test_b218_limits_store_real_values_and_null_only_closes_that_dimension(
    client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218：rpm/tpm/max_inflight 按真实资源落库；null 只关闭该维度限流，不是零额度。"""
    ctx = quota_tenant
    open_rpm = await _create(
        client, ctx, key=f"group-{uuid.uuid4().hex[:8]}", max_inflight=8, rpm=None, tpm=900_000
    )
    assert set(open_rpm) == READ_FIELDS, "读取投影只有可编辑字段与 id/revision"
    assert open_rpm["rpm"] is None and open_rpm["tpm"] == 900_000
    row = await _db_group(ctx.tenant_id, open_rpm["id"])
    assert row is not None and row["rpm"] is None and row["tpm"] == 900_000, "未设限在 SQL 层是 NULL，不是 0"

    open_tpm = await _create(
        client, ctx, key=f"group-{uuid.uuid4().hex[:8]}", max_inflight=4, rpm=600, tpm=None
    )
    detail = await _get_item(client, ctx, open_tpm["id"])
    assert detail["rpm"] == 600 and detail["tpm"] is None and detail["max_inflight"] == 4

    listing = await client.get(URL, params={"page": 1, "page_size": 1}, headers=_h(ctx))
    envelope = listing.json()["data"]
    assert envelope["total"] == 2 and len(envelope["items"]) == 1
    assert set(envelope["items"][0]) == READ_FIELDS

    for invalid in (
        _payload(key="bad-zero-slots", max_inflight=0),
        _payload(key="bad-zero-rpm", rpm=0),
        _payload(key="bad-negative-tpm", tpm=-1),
    ):
        response = await client.post(URL, json=invalid, headers=_h(ctx))
        assert response.status_code == 422, invalid
    listing = await client.get(URL, params={"page_size": 100}, headers=_h(ctx))
    assert listing.json()["data"]["total"] == 2, "被拒的写请求不得留下行"


async def test_b218_duplicate_key_cas_conflict_and_missing_group_are_explicit(
    client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218：同 key 冲突、陈旧 revision 与缺失归属都是显式失败。"""
    ctx = quota_tenant
    first = await _create(client, ctx, key="group-a")
    duplicate = await client.post(URL, json=_payload(key="group-a"), headers=_h(ctx))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "MODEL_QUOTA_GROUP_EXISTS"

    item = await _get_item(client, ctx, first["id"])
    updated = await client.put(
        f"{URL}/{first['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=4),
        headers=_h(ctx),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["max_inflight"] == 4 and updated.json()["data"]["revision"] == 2

    stale = await client.put(
        f"{URL}/{first['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=2),
        headers=_h(ctx),
    )
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    row = await _db_group(ctx.tenant_id, first["id"])
    assert row is not None and row["max_inflight"] == 4 and row["revision"] == 2, (
        "陈旧 revision 不得覆盖已提交的写法"
    )

    missing_id = uuid.uuid4()
    missing = await client.get(f"{URL}/{missing_id}", headers=_h(ctx))
    assert missing.status_code == 404 and missing.json()["code"] == "COMMON_NOT_FOUND"
    put_missing = await client.put(
        f"{URL}/{missing_id}",
        json=_put_body(item, expected_revision=1, max_inflight=2),
        headers=_h(ctx),
    )
    assert put_missing.status_code == 404 and put_missing.json()["code"] == "COMMON_NOT_FOUND"
    delete_missing = await client.delete(f"{URL}/{missing_id}", headers=_h(ctx))
    assert delete_missing.status_code == 404 and delete_missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_b218_tightening_requires_drain_but_relaxation_and_expansion_do_not(
    client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218/§3.6：改小容量或新开限流维度先排空；扩容、放开限流与已结算租约实时生效。"""
    ctx = quota_tenant
    group_a = await _create(client, ctx, key="group-a", max_inflight=8, rpm=600, tpm=1000)
    group_b = await _create(client, ctx, key="group-b", max_inflight=8)
    await _insert_lease(ctx, group_b["id"], state="ACTIVE")

    item = await _get_item(client, ctx, group_a["id"])
    others_lease = await client.put(
        f"{URL}/{group_a['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=4),
        headers=_h(ctx),
    )
    assert others_lease.status_code == 200, "别的额度组的在飞占用不阻塞本组降额"

    lease = await _insert_lease(ctx, group_a["id"], state="ACTIVE")
    for label, body in (
        ("rpm 调小", _put_body(item, expected_revision=2, max_inflight=4, rpm=300)),
        ("max_inflight 调小", _put_body(item, expected_revision=2, max_inflight=2)),
    ):
        blocked = await client.put(f"{URL}/{group_a['id']}", json=body, headers=_h(ctx))
        assert blocked.status_code == 409, label
        assert blocked.json()["code"] == "MODEL_CAPACITY_DRAIN_REQUIRED", label

    relaxed = await client.put(
        f"{URL}/{group_a['id']}",
        json=_put_body(item, expected_revision=2, max_inflight=4, tpm=None),
        headers=_h(ctx),
    )
    assert relaxed.status_code == 200, "放开 tpm 限流不回收在飞调用"
    assert relaxed.json()["data"]["tpm"] is None and relaxed.json()["data"]["revision"] == 3

    expanded = await client.put(
        f"{URL}/{group_a['id']}",
        json=_put_body(item, expected_revision=3, max_inflight=12, rpm=700, tpm=None),
        headers=_h(ctx),
    )
    assert expanded.status_code == 200, "扩容不回收在飞调用"
    assert expanded.json()["data"]["max_inflight"] == 12 and expanded.json()["data"]["rpm"] == 700

    await _set_lease_state(lease, "UNKNOWN")
    unknown_blocked = await client.put(
        f"{URL}/{group_a['id']}",
        json=_put_body(item, expected_revision=4, max_inflight=4, rpm=700, tpm=None),
        headers=_h(ctx),
    )
    assert unknown_blocked.status_code == 409, "远端是否停止未知时保留占用，也按未排空处理"
    assert unknown_blocked.json()["code"] == "MODEL_CAPACITY_DRAIN_REQUIRED"

    no_op = await client.put(
        f"{URL}/{group_a['id']}",
        json=_put_body(item, expected_revision=4, max_inflight=12, rpm=700, tpm=None),
        headers=_h(ctx),
    )
    assert no_op.status_code == 200, "未变更任何限流维度不涉及回收占用"

    await _set_lease_state(lease, "SETTLED")
    tightened = await client.put(
        f"{URL}/{group_a['id']}",
        json=_put_body(item, expected_revision=5, max_inflight=4, rpm=None, tpm=2000),
        headers=_h(ctx),
    )
    assert tightened.status_code == 200, tightened.text
    assert tightened.json()["data"]["max_inflight"] == 4
    assert tightened.json()["data"]["rpm"] is None
    assert tightened.json()["data"]["tpm"] == 2000
    assert tightened.json()["data"]["revision"] == 6


async def test_b218_referenced_delete_conflicts_and_recreation_is_new_identity(
    client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218：仍被活跃凭据引用的额度组不可删；删除是软删，同名重建是新身份。"""
    ctx = quota_tenant
    created = await _create(client, ctx, key="group-a")
    factory = get_session_factory()
    async with factory() as session:
        credential = ModelCredential(
            tenant_id=ctx.tenant_id,
            key=f"cred-{uuid.uuid4().hex[:8]}",
            provider_key=PROVIDER,
            base_url="https://model.example/v1",
            api_key=SECRET,
            api_key_digest=hashlib.sha256(SECRET.encode()).hexdigest(),
            quota_group_id=uuid.UUID(created["id"]),
            max_inflight=8,
            enabled=True,
        )
        session.add(credential)
        await session.commit()
        credential_id = credential.id

    busy = await client.delete(f"{URL}/{created['id']}", headers=_h(ctx))
    assert busy.status_code == 409 and busy.json()["code"] == "MODEL_QUOTA_GROUP_IN_USE"

    async with factory() as session:
        await session.execute(
            text("UPDATE control.model_credential SET is_deleted = true WHERE id = :id"),
            {"id": credential_id},
        )
        await session.commit()
    deleted = await client.delete(f"{URL}/{created['id']}", headers=_h(ctx))
    assert deleted.status_code == 200 and deleted.json()["data"]["deleted"] is True
    assert (await client.get(f"{URL}/{created['id']}", headers=_h(ctx))).status_code == 404
    row = await _db_group(ctx.tenant_id, created["id"])
    assert row is not None and row["is_deleted"] is True, "删除是软删：行与审计保留"

    recreated = await _create(client, ctx, key="group-a")
    assert recreated["id"] != created["id"] and recreated["revision"] == 1, (
        "同名重建是新身份，不复用被软删的行"
    )


async def test_b218_create_replays_first_result_by_persisted_canonical_fingerprint(
    client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218/RULE-api-002：同 Idempotency-Key 同指纹重放首次结果；指纹是含判别键的规范化 JSON。"""
    ctx = quota_tenant
    body = _payload(key="group-idem")
    headers = {**_h(ctx), "Idempotency-Key": "b218-create-key"}
    first = await client.post(URL, json=body, headers=headers)
    assert first.status_code == 200, first.text
    replay = await client.post(URL, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["data"]["id"] == first.json()["data"]["id"]

    factory = get_session_factory()
    async with factory() as session:
        admin_id = await session.scalar(
            text(
                "SELECT id FROM control.console_account "
                "WHERE tenant_id = :tenant_id AND role = 'ADMIN' ORDER BY create_time LIMIT 1"
            ),
            {"tenant_id": ctx.tenant_id},
        )
        record = (
            (
                await session.execute(
                    text(
                        "SELECT endpoint, request_fingerprint, response_json "
                        "FROM control.skill_import_idempotency "
                        "WHERE tenant_id = :tenant_id AND idempotency_key = 'b218-create-key'"
                    ),
                    {"tenant_id": ctx.tenant_id},
                )
            )
            .mappings()
            .one()
        )
        group_count = await session.scalar(
            text("SELECT count(*) FROM control.model_quota_group WHERE tenant_id = :tenant_id"),
            {"tenant_id": ctx.tenant_id},
        )
        audit_actions = list(
            await session.scalars(
                text(
                    "SELECT action FROM control.config_audit_log "
                    "WHERE tenant_id = :tenant_id AND resource_type = 'MODEL_QUOTA_GROUP' "
                    "ORDER BY create_time"
                ),
                {"tenant_id": ctx.tenant_id},
            )
        )
    assert record["endpoint"] == "model-quota-group-create"
    assert group_count == 1 and audit_actions == ["CREATE"], "重放不得建第二行/写第二次审计"
    canonical = json.dumps(
        {
            "endpoint": "model-quota-group-create",
            "tenant_id": ctx.tenant_id,
            "actor": str(admin_id),
            "key": body["key"],
            "max_inflight": body["max_inflight"],
            "rpm": body["rpm"],
            "tpm": body["tpm"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert record["request_fingerprint"] == expected, (
        "指纹 = 规范化 JSON（键序无关、紧凑分隔符）+ endpoint/tenant/actor 判别键"
    )

    mismatch = await client.post(URL, json=_payload(key="group-idem", max_inflight=4), headers=headers)
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"


async def test_b218_cross_tenant_groups_are_invisible(
    client: AsyncClient, quota_tenant: TenantContext
) -> None:
    """B-218：额度组读写按登录账号租户裁决，异租户 ID 一律按归属缺失处理。"""
    ctx = quota_tenant
    other_tenant = f"{ctx.tenant_id}-other"
    foreign_id = uuid.uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.model_quota_group (id, tenant_id, key, max_inflight, revision) "
                "VALUES (:id, :tenant_id, 'foreign-group', 8, 1)"
            ),
            {"id": foreign_id, "tenant_id": other_tenant},
        )
        await session.commit()
    try:
        assert (await client.get(f"{URL}/{foreign_id}", headers=_h(ctx))).status_code == 404
        assert (await client.delete(f"{URL}/{foreign_id}", headers=_h(ctx))).status_code == 404
        listed = await client.get(URL, params={"page_size": 100}, headers=_h(ctx))
        assert listed.json()["data"]["total"] == 0 and "foreign-group" not in listed.text

        local = await _create(client, ctx, key="local-group")
        put = await client.put(
            f"{URL}/{foreign_id}",
            json=_put_body(await _get_item(client, ctx, local["id"]), expected_revision=1),
            headers=_h(ctx),
        )
        assert put.status_code == 404 and put.json()["code"] == "COMMON_NOT_FOUND"
        foreign_row = await _db_group(other_tenant, foreign_id)
        assert foreign_row is not None and foreign_row["is_deleted"] is False
    finally:
        await _purge_tenant(other_tenant)
