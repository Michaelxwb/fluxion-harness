"""[B-216] ADMIN 模型凭据路由：读写边界、排空冲突与 strict canonical JSON 持久幂等回放。

真实边界（业务路径不 mock）：
- **真实 Console HTTP/应用服务**：ASGI 直连真实应用，真实登录会话裁决 ADMIN 角色与 CSRF；
- **真实 PostgreSQL**：凭据/成员/额度组、`control.skill_import_idempotency` 与 Runtime 的
  `runtime.model_call_lease` 都用真实行构造，引用计数、revision CAS、事务 advisory 锁与
  partial unique 均由数据库裁决；密钥从库里真实回读；
- 外部模型探针不属于本任务（物理 Key 检测是 TASK-028 的 `POST /api/v1/model-credentials/test`）。

清库隔离：conftest 的 tenant 清理不含模型池新表，`credential_tenant` 夹具自带
model_deployment/model_credential/model_quota_group/skill_import_idempotency/
config_audit_log 与 runtime.model_call_lease 的清理（tenant_id 每用例唯一）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.application.model_credential_service import (
    ModelCredentialService,
    derive_key_resource_id,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_BUILDER, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    ModelCredential,
    ModelDeployment,
    ModelQuotaGroup,
)
from muad_console_platform.main import app
from muad_contracts.model_pool import ModelCredentialCreate
from sqlalchemy import text

from console_platform.conftest import ADMIN_PASSWORD, TenantContext

URL = "/api/v1/model-credentials"
PROVIDER = "openai"
SECRET_ONE = "sk-canary-credential-api-one"
SECRET_TWO = "sk-canary-credential-api-two"
SECRET_LEGACY = "sk-canary-credential-api-legacy"
READ_FIELDS = {
    "id",
    "key",
    "provider_key",
    "base_url",
    "quota_group_id",
    "max_inflight",
    "enabled",
    "revision",
    "configured",
}


@dataclass(frozen=True)
class ApiContext:
    tenant_id: str
    model_id: uuid.UUID
    quota_group_id: uuid.UUID
    other_group_id: uuid.UUID


def _h(ctx: ApiContext) -> dict[str, str]:
    """本用例租户头：真实登录会话在这里按租户定位账号。"""
    return {"X-Tenant-Id": ctx.tenant_id}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _purge_tenant(tenant_id: str) -> None:
    """删本租户的成员/凭据/额度组/幂等回执/审计与在飞租约，不影响其他租户。"""
    factory = get_session_factory()
    async with factory() as session:
        for statement in (
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
            "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def credential_tenant(tenant: TenantContext) -> AsyncIterator[ApiContext]:
    """本用例租户 + 两个真实额度组（默认归属与换组目标）+ 结束清理新表行。"""
    factory = get_session_factory()
    async with factory() as session:
        groups = [
            ModelQuotaGroup(
                tenant_id=tenant.tenant_id,
                key=f"group-{uuid.uuid4().hex[:8]}",
                rpm=None,
                tpm=None,
                max_inflight=8,
            )
            for _ in range(2)
        ]
        session.add_all(groups)
        await session.commit()
        group_ids = [group.id for group in groups]
    try:
        yield ApiContext(
            tenant_id=tenant.tenant_id,
            model_id=tenant.model_id,
            quota_group_id=group_ids[0],
            other_group_id=group_ids[1],
        )
    finally:
        await _purge_tenant(tenant.tenant_id)


@pytest.fixture
async def builder_client(credential_tenant: ApiContext) -> AsyncIterator[AsyncClient]:
    """同租户 BUILDER 会话：ADMIN 边界以真实登录角色的裁决为准，不伪造身份。"""
    username = f"builder-{uuid.uuid4()}"
    factory = get_session_factory()
    async with factory() as session:
        account = ConsoleAccount(
            tenant_id=credential_tenant.tenant_id,
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
            headers=_h(credential_tenant),
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
    ctx: ApiContext,
    *,
    key: str = "cred-a",
    api_key: str = SECRET_ONE,
    provider_key: str = PROVIDER,
    max_inflight: int = 8,
    enabled: bool = True,
    base_url: str = "https://model.example/v1",
) -> dict[str, Any]:
    return {
        "key": key,
        "provider_key": provider_key,
        "base_url": base_url,
        "api_key": api_key,
        "quota_group_id": str(ctx.quota_group_id),
        "max_inflight": max_inflight,
        "enabled": enabled,
    }


def _put_body(item: dict[str, Any], *, expected_revision: int, **overrides: Any) -> dict[str, Any]:
    """整份字段替换 + expected_revision：缺省沿用读取投影里的当前值。"""
    body = {
        name: item[name]
        for name in ("key", "provider_key", "base_url", "quota_group_id", "max_inflight", "enabled")
    }
    body["expected_revision"] = expected_revision
    body.update(overrides)
    return body


async def _create(client: AsyncClient, ctx: ApiContext, **overrides: Any) -> dict[str, Any]:
    response = await client.post(URL, json=_payload(ctx, **overrides), headers=_h(ctx))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _get_item(client: AsyncClient, ctx: ApiContext, credential_id: str) -> dict[str, Any]:
    response = await client.get(f"{URL}/{credential_id}", headers=_h(ctx))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _db_credential(tenant_id: str, credential_id: str | uuid.UUID) -> dict[str, Any] | None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT api_key, api_key_digest, quota_group_id, max_inflight, enabled, revision, "
                "is_deleted FROM control.model_credential WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": uuid.UUID(str(credential_id)), "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def _insert_lease(
    ctx: ApiContext, credential_id: str, *, api_key: str = SECRET_ONE, state: str = "ACTIVE"
) -> uuid.UUID:
    """真实在飞租约（Runtime 表）：ACTIVE/UNKNOWN 都保留占用，供降额冲突裁决。"""
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
                " :key_resource_id, :quota_group_id, 1, 'PROBE', 'b216-test', "
                " now() + interval '5 minutes', 1, :state)"
            ),
            {
                "id": lease_id,
                "tenant_id": ctx.tenant_id,
                "attempt_id": uuid.uuid4(),
                "probe_id": uuid.uuid4(),
                "deployment_id": uuid.uuid4(),
                "credential_id": uuid.UUID(credential_id),
                "key_resource_id": derive_key_resource_id(ctx.tenant_id, PROVIDER, _digest(api_key)),
                "quota_group_id": ctx.quota_group_id,
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


async def test_b216_admin_and_csrf_guard_every_write(
    client: AsyncClient, builder_client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216：整组仅 ADMIN；不安全方法在 ADMIN 会话下仍必须携带正确 CSRF。"""
    ctx = credential_tenant
    body = _payload(ctx)
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
    assert created["configured"] is True, "带正确 CSRF 的 ADMIN 写请求正常受理"


async def test_b216_reads_expose_only_configured_and_accept_group_less_keys(
    client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216：读取投影没有能装下明文/指纹的字段；迁移遗留的独立容量 Key（无额度组）可读可建。"""
    ctx = credential_tenant
    created = await _create(client, ctx, key=f"cred-{uuid.uuid4().hex[:8]}")
    assert set(created) == READ_FIELDS, "读取仅 configured：多一个字段都是泄漏面"
    assert created["configured"] is True and created["quota_group_id"] == str(ctx.quota_group_id)

    detail = await client.get(f"{URL}/{created['id']}", headers=_h(ctx))
    assert SECRET_ONE not in detail.text and "api_key" not in detail.json()["data"]
    listing = await client.get(URL, params={"page": 1, "page_size": 1}, headers=_h(ctx))
    envelope = listing.json()["data"]
    assert envelope["total"] == 1 and len(envelope["items"]) == 1
    assert set(envelope["items"][0]) == READ_FIELDS
    assert SECRET_ONE not in listing.text

    # 无额度组的 Key 独占自己的容量（0021 迁移清单允许 quota_group: null）：仍可读、可新建
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            ModelCredential(
                tenant_id=ctx.tenant_id,
                key="legacy-standalone",
                provider_key=PROVIDER,
                base_url="https://model.example/v1",
                api_key=SECRET_LEGACY,
                api_key_digest=_digest(SECRET_LEGACY),
                quota_group_id=None,
                max_inflight=4,
                enabled=True,
            )
        )
        await session.commit()
    grouped = await _create(client, ctx, key="standalone-2", api_key=SECRET_TWO)
    assert grouped["quota_group_id"] == str(ctx.quota_group_id), "带组创建仍写组"

    listing = await client.get(URL, params={"page_size": 100}, headers=_h(ctx))
    items = listing.json()["data"]["items"]
    assert any(item["key"] == "legacy-standalone" and item["quota_group_id"] is None for item in items)
    assert SECRET_LEGACY not in listing.text

    standalone_body = _payload(ctx, key="standalone-3", api_key=SECRET_LEGACY + "-x")
    standalone_body.pop("quota_group_id")
    standalone = await client.post(URL, json=standalone_body, headers=_h(ctx))
    assert standalone.status_code == 200, standalone.text
    assert standalone.json()["data"]["quota_group_id"] is None


async def test_b216_create_conflicts_and_referenced_delete_are_explicit(
    client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216：额度组归属缺失、同物理 Key/同名冲突与「仍被成员引用」的删除都必须明确失败。"""
    ctx = credential_tenant
    unknown_group = _payload(ctx, key="cred-unknown-group", api_key=SECRET_TWO)
    unknown_group["quota_group_id"] = str(uuid.uuid4())
    missing = await client.post(URL, json=unknown_group, headers=_h(ctx))
    assert missing.status_code == 404 and missing.json()["code"] == "COMMON_NOT_FOUND"

    first = await _create(client, ctx, key="cred-a")
    duplicate_key = await client.post(URL, json=_payload(ctx, key="cred-a"), headers=_h(ctx))
    assert duplicate_key.status_code == 409
    assert duplicate_key.json()["code"] == "MODEL_CREDENTIAL_EXISTS"
    duplicate_digest = await client.post(URL, json=_payload(ctx, key="cred-b"), headers=_h(ctx))
    assert duplicate_digest.status_code == 409
    assert duplicate_digest.json()["code"] == "MODEL_CREDENTIAL_EXISTS"

    factory = get_session_factory()
    async with factory() as session:
        session.add(
            ModelDeployment(
                tenant_id=ctx.tenant_id,
                key=f"member-{uuid.uuid4().hex[:8]}",
                model_id=ctx.model_id,
                credential_id=uuid.UUID(first["id"]),
                weight=1,
                enabled=True,
            )
        )
        await session.commit()
    busy = await client.delete(f"{URL}/{first['id']}", headers=_h(ctx))
    assert busy.status_code == 409 and busy.json()["code"] == "MODEL_CREDENTIAL_IN_USE"

    async with factory() as session:
        await session.execute(
            text("UPDATE control.model_deployment SET is_deleted = true WHERE credential_id = :id"),
            {"id": uuid.UUID(first["id"])},
        )
        await session.commit()
    deleted = await client.delete(f"{URL}/{first['id']}", headers=_h(ctx))
    assert deleted.status_code == 200 and deleted.json()["data"]["deleted"] is True
    gone = await client.get(f"{URL}/{first['id']}", headers=_h(ctx))
    assert gone.status_code == 404
    row = await _db_credential(ctx.tenant_id, first["id"])
    assert row is not None and row["is_deleted"] is True, "删除是软删：行与计量证据保留"


async def test_b216_update_keeps_secret_unless_explicit_rotation(
    client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216：PUT 为整份字段替换 + revision CAS；不带 api_key 保留原值，带值才轮换。"""
    ctx = credential_tenant
    created = await _create(client, ctx)
    item = await _get_item(client, ctx, created["id"])

    kept = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=6),
        headers=_h(ctx),
    )
    assert kept.status_code == 200, kept.text
    updated = kept.json()["data"]
    assert updated["max_inflight"] == 6 and updated["revision"] == 2
    assert "api_key" not in updated and SECRET_ONE not in kept.text
    row = await _db_credential(ctx.tenant_id, created["id"])
    assert row is not None and row["api_key"] == SECRET_ONE
    assert row["api_key_digest"] == _digest(SECRET_ONE)
    assert updated["id"] == item["id"], "更新同一身份（本行），不新建行"

    blank = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=2, api_key=""),
        headers=_h(ctx),
    )
    assert blank.status_code == 422, "空密钥不是「保留原值」的表达，缺省才是"

    # 整份替换：轮换按「读当前状态 → 改密钥 → 提交」推进，其余字段沿用当前值（6）
    current = await _get_item(client, ctx, created["id"])
    rotated = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(current, expected_revision=current["revision"], api_key=SECRET_TWO),
        headers=_h(ctx),
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["data"]["revision"] == 3
    row = await _db_credential(ctx.tenant_id, created["id"])
    assert row is not None and row["api_key_digest"] == _digest(SECRET_TWO)

    stale = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=2),
        headers=_h(ctx),
    )
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    row = await _db_credential(ctx.tenant_id, created["id"])
    assert row is not None and row["revision"] == 3 and row["max_inflight"] == 6, (
        "陈旧 revision 不得覆盖已提交的写法"
    )


async def test_b216_downgrade_and_ownership_change_require_drain(
    client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216/§3.6：改小容量或换归属/连接目标先排空；扩容与值轮换可实时生效。"""
    ctx = credential_tenant
    created = await _create(client, ctx, key=f"cred-{uuid.uuid4().hex[:8]}")
    item = await _get_item(client, ctx, created["id"])
    lease = await _insert_lease(ctx, created["id"], state="ACTIVE")

    downgrade = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=4),
        headers=_h(ctx),
    )
    assert downgrade.status_code == 409
    assert downgrade.json()["code"] == "MODEL_CAPACITY_DRAIN_REQUIRED"

    upgrade = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=1, max_inflight=12),
        headers=_h(ctx),
    )
    assert upgrade.status_code == 200, "扩容不回收在飞调用，无需排空"
    rotation = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=2, max_inflight=12, api_key=SECRET_TWO),
        headers=_h(ctx),
    )
    assert rotation.status_code == 200, "相同调用目标的值轮换可实时生效"

    await _set_lease_state(lease, "UNKNOWN")
    moved = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=3, max_inflight=12, quota_group_id=str(ctx.other_group_id)),
        headers=_h(ctx),
    )
    assert moved.status_code == 409 and moved.json()["code"] == "MODEL_CAPACITY_DRAIN_REQUIRED", (
        "远端是否停止未知时保留占用，也按未排空处理"
    )

    await _set_lease_state(lease, "SETTLED")
    settled = await client.put(
        f"{URL}/{created['id']}",
        json=_put_body(item, expected_revision=3, max_inflight=4, quota_group_id=str(ctx.other_group_id)),
        headers=_h(ctx),
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["data"]["quota_group_id"] == str(ctx.other_group_id)
    assert settled.json()["data"]["max_inflight"] == 4
    assert settled.json()["data"]["revision"] == 4


async def test_b216_create_replays_first_result_by_persisted_canonical_fingerprint(
    client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216/RULE-api-002：同 Idempotency-Key 同指纹重放首次结果；指纹是含判别键的规范化 JSON。"""
    ctx = credential_tenant
    body = _payload(ctx, key="cred-idem")
    headers = {**_h(ctx), "Idempotency-Key": "b216-create-key"}
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
                        "WHERE tenant_id = :tenant_id AND idempotency_key = 'b216-create-key'"
                    ),
                    {"tenant_id": ctx.tenant_id},
                )
            )
            .mappings()
            .one()
        )
        credential_count = await session.scalar(
            text("SELECT count(*) FROM control.model_credential WHERE tenant_id = :tenant_id"),
            {"tenant_id": ctx.tenant_id},
        )
        audit_actions = list(
            await session.scalars(
                text(
                    "SELECT action FROM control.config_audit_log "
                    "WHERE tenant_id = :tenant_id AND resource_type = 'MODEL_CREDENTIAL' "
                    "ORDER BY create_time"
                ),
                {"tenant_id": ctx.tenant_id},
            )
        )
    assert record["endpoint"] == "model-credential-create"
    assert credential_count == 1 and audit_actions == ["CREATE"], "重放不得建第二行/写第二次审计"
    canonical = json.dumps(
        {
            "endpoint": "model-credential-create",
            "tenant_id": ctx.tenant_id,
            "actor": str(admin_id),
            "key": body["key"],
            "provider_key": body["provider_key"],
            "base_url": body["base_url"],
            "quota_group_id": body["quota_group_id"],
            "max_inflight": body["max_inflight"],
            "enabled": body["enabled"],
            "api_key_checksum": _digest(body["api_key"]),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert record["request_fingerprint"] == "sha256:" + _digest(canonical), (
        "指纹 = 规范化 JSON（键序无关、紧凑分隔符）+ endpoint/tenant/actor 判别键，密钥只入摘要"
    )
    assert SECRET_ONE not in json.dumps(record["response_json"]), "回执不得带密钥明文"

    mismatch = await client.post(URL, json=_payload(ctx, key="cred-idem", max_inflight=4), headers=headers)
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"


async def test_b216_same_idempotency_key_serializes_concurrent_creation(
    credential_tenant: ApiContext,
) -> None:
    """RULE-api-002：同键并发由 (tenant,key,endpoint) 事务 advisory 锁串行，后到者重放首次结果。"""
    ctx = credential_tenant
    factory = get_session_factory()
    payload = ModelCredentialCreate(
        key="cred-race",
        provider_key=PROVIDER,
        base_url="https://model.example/v1",
        quota_group_id=ctx.quota_group_id,
        max_inflight=8,
        api_key=SECRET_ONE,
    )
    actor = AuditActor(account_id=uuid.uuid4())
    started, release = asyncio.Event(), asyncio.Event()
    holder: dict[str, Any] = {}

    async def _first() -> None:
        async with factory() as session:
            created = await ModelCredentialService(session).create_credential(
                ctx.tenant_id, payload, actor, idempotency_key="b216-race-key"
            )
            holder["id"] = created.id
            started.set()
            await release.wait()
            await session.commit()

    async def _second() -> ModelCredential:
        async with factory() as session:
            replayed = await ModelCredentialService(session).create_credential(
                ctx.tenant_id, payload, actor, idempotency_key="b216-race-key"
            )
            await session.commit()
            return replayed

    # 首个写者留在未提交事务里；同键第二个写者必须等它提交后才推进。
    # 不用 `await started.wait()` 裸等：缺实现时首个写者会立即失败，裸等会挂死。
    first = asyncio.create_task(_first())
    entered = asyncio.create_task(started.wait())
    try:
        done, _ = await asyncio.wait({first, entered}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if first in done:
            await first  # 首个写者的真实失败（RED：缺 idempotency 参数）立即冒泡
            raise AssertionError("首个写者未进入等待窗口就结束")
        assert entered in done, "首个写者 10s 内未持有未提交事务"
        second = asyncio.create_task(_second())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), 0.5)
        release.set()
        await first
        replayed = await second
    finally:
        for task in (entered, first):
            if not task.done():
                task.cancel()
        release.set()

    assert replayed.id == holder["id"], "未提交期间同键写者被串行；提交后重放首次结果，而不是撞唯一索引报冲突"
    assert await _db_credential(ctx.tenant_id, holder["id"]) is not None
    async with factory() as session:
        total = await session.scalar(
            text("SELECT count(*) FROM control.model_credential WHERE tenant_id = :tenant_id"),
            {"tenant_id": ctx.tenant_id},
        )
    assert total == 1, "同键并发不得创建第二行"


async def test_b216_cross_tenant_credentials_are_invisible(
    client: AsyncClient, credential_tenant: ApiContext
) -> None:
    """B-216：凭据读写按登录账号租户裁决，异租户 ID 一律按归属缺失处理。"""
    ctx = credential_tenant
    other_tenant = f"{ctx.tenant_id}-other"
    factory = get_session_factory()
    async with factory() as session:
        foreign = ModelCredential(
            tenant_id=other_tenant,
            key="foreign-cred",
            provider_key=PROVIDER,
            base_url="https://model.example/v1",
            api_key=SECRET_TWO,
            api_key_digest=_digest(SECRET_TWO),
            quota_group_id=None,
            max_inflight=8,
            enabled=True,
        )
        session.add(foreign)
        await session.commit()
        foreign_id = foreign.id
    try:
        assert (await client.get(f"{URL}/{foreign_id}", headers=_h(ctx))).status_code == 404
        assert (await client.delete(f"{URL}/{foreign_id}", headers=_h(ctx))).status_code == 404
        listed = await client.get(URL, params={"page_size": 100}, headers=_h(ctx))
        assert listed.json()["data"]["total"] == 0 and "foreign-cred" not in listed.text

        local = await _create(client, ctx, key="local-cred")
        put = await client.put(
            f"{URL}/{foreign_id}",
            json=_put_body(await _get_item(client, ctx, local["id"]), expected_revision=1),
            headers=_h(ctx),
        )
        assert put.status_code == 404 and put.json()["code"] == "COMMON_NOT_FOUND"
        foreign_row = await _db_credential(other_tenant, foreign_id)
        assert foreign_row is not None and foreign_row["is_deleted"] is False
    finally:
        await _purge_tenant(other_tenant)
