"""Console reads the v2 memory projection and its history without writing Runtime tables."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from muad_console_platform.application.audit_query_service import format_console_time
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import TenantContext

ADMIN_PASSWORD = "console-admin-password"


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Memory User"},
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    return created.json()["data"]["id"]


async def _seed_memory(
    session: AsyncSession, tenant_id: str, user_id: str, **overrides: Any
) -> dict[str, Any]:
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "user_id": uuid.UUID(user_id),
        "memory_key": f"v1.slot.preference.output_style.{uuid.uuid4().hex[:8]}",
        "category": None,
        "memory_kind": "PREFERENCE",
        "predicate": "output_style",
        "scope_state": "ABSENT",
        "scope_json": {},
        "scope_hash": "",
        "content_text": "",
        "title": "输出风格",
        "summary": "",
        "tags_json": [],
        "cardinality": "SINGLE",
        "priority": "OPTIONAL",
        "taxonomy_version": 1,
        "content": {"value": "concise answers"},
        "author": "ADMIN",
        "authority": "ADMIN_EDIT",
        "explicit_request": False,
        "confirmation_event_id": None,
        "enabled": True,
        "version": 1,
        "expires_at": now + timedelta(days=30),
        "create_time": now,
        "update_time": now,
        "is_deleted": False,
    }
    row.update(overrides)
    await session.execute(
        text(
            "INSERT INTO runtime.user_memory "
            "(id, tenant_id, user_id, memory_key, category, memory_kind, predicate, scope_state, "
            "scope_json, scope_hash, content_text, title, summary, tags_json, cardinality, priority, "
            "taxonomy_version, content_json, author, authority, explicit_request, "
            "confirmation_event_id, enabled, version, expires_at, create_time, update_time, is_deleted) "
            "VALUES (:id, :tenant_id, :user_id, :memory_key, :category, :memory_kind, :predicate, "
            ":scope_state, CAST(:scope_json AS jsonb), :scope_hash, :content_text, :title, :summary, "
            "CAST(:tags_json AS jsonb), :cardinality, :priority, :taxonomy_version, "
            "CAST(:content AS jsonb), :author, :authority, :explicit_request, :confirmation_event_id, "
            ":enabled, :version, :expires_at, :create_time, :update_time, :is_deleted)"
        ),
        {
            **row,
            "scope_json": json.dumps(row["scope_json"]),
            "tags_json": json.dumps(row["tags_json"]),
            "content": json.dumps(row["content"]),
        },
    )
    return row


async def _seed_history(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    memory_key: str,
    origin_id: uuid.UUID,
    version: int,
    change_reason: str = "CREATE",
) -> None:
    await session.execute(
        text(
            "INSERT INTO runtime.user_memory_history "
            "(id, tenant_id, user_id, memory_key, origin_id, version, change_reason, actor_type, "
            "actor_id, command_id, after_json, create_time) "
            "VALUES (:id, :tenant_id, :user_id, :memory_key, :origin_id, :version, :change_reason, "
            "'ADMIN', 'fixture', :command_id, CAST(:after AS jsonb), :create_time)"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": uuid.UUID(user_id),
            "memory_key": memory_key,
            "origin_id": origin_id,
            "version": version,
            "change_reason": change_reason,
            "command_id": f"cmd-{uuid.uuid4().hex[:8]}",
            "after": json.dumps({"version": version}),
            "create_time": datetime.now(UTC),
        },
    )


async def _seed_foreign_tenant(user_id: str, memory_key: str) -> str:
    tenant_id = f"test-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_user (id, tenant_id, user_code, display_name) "
                "VALUES (:id, :tenant_id, :user_code, 'Foreign User')"
            ),
            {
                "id": uuid.UUID(user_id),
                "tenant_id": tenant_id,
                "user_code": f"u-{uuid.uuid4().hex[:8]}",
            },
        )
        await _seed_memory(session, tenant_id, user_id, memory_key=memory_key)
        session.add(
            ConsoleAccount(
                tenant_id=tenant_id,
                username=f"admin-{uuid.uuid4().hex[:8]}",
                display_name="Foreign Admin",
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
            )
        )
        await session.commit()
    return tenant_id


async def _drop_foreign_tenant(tenant_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for statement in (
            "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant_id",
            "DELETE FROM control.console_session WHERE account_id IN "
            "(SELECT id FROM control.console_account WHERE tenant_id = :tenant_id)",
            "DELETE FROM control.console_account WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
            "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


async def test_local_01_list_filters_kind_predicate_and_status(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _seed_memory(session, tenant.tenant_id, user_id, title="偏好")
        await _seed_memory(
            session,
            tenant.tenant_id,
            user_id,
            memory_kind="FACT",
            predicate="role",
            title="角色",
            content={"value": "pm"},
        )
        await _seed_memory(session, tenant.tenant_id, user_id, title="禁用", enabled=False)
        await session.commit()

    listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 3

    facts = await client.get(
        f"/api/v1/users/{user_id}/memory",
        params={"memory_kind": "FACT"},
        headers=_headers(tenant),
    )
    assert facts.json()["data"]["total"] == 1
    assert facts.json()["data"]["items"][0]["predicate"] == "role"

    disabled = await client.get(
        f"/api/v1/users/{user_id}/memory",
        params={"status": "DISABLED"},
        headers=_headers(tenant),
    )
    assert disabled.json()["data"]["total"] == 1
    assert disabled.json()["data"]["items"][0]["status"] == "DISABLED"


async def test_local_02_deleted_hidden_unless_requested_and_history_by_id(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        live = await _seed_memory(session, tenant.tenant_id, user_id)
        deleted = await _seed_memory(session, tenant.tenant_id, user_id, is_deleted=True, version=2)
        for version in (1, 2):
            await _seed_history(
                session,
                tenant_id=tenant.tenant_id,
                user_id=user_id,
                memory_key=deleted["memory_key"],
                origin_id=deleted["id"],
                version=version,
            )
        await session.commit()

    listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert [item["id"] for item in listing.json()["data"]["items"]] == [str(live["id"])]

    recycle = await client.get(
        f"/api/v1/users/{user_id}/memory",
        params={"include_deleted": True},
        headers=_headers(tenant),
    )
    assert {item["status"] for item in recycle.json()["data"]["items"]} == {"ACTIVE", "DELETED"}

    history = await client.get(
        f"/api/v1/users/{user_id}/memory/{deleted['id']}/history",
        params={"page": 1, "page_size": 1},
        headers=_headers(tenant),
    )
    assert history.status_code == 200
    assert history.json()["data"]["total"] == 2
    assert history.json()["data"]["items"][0]["version"] == 2


async def test_local_03_reads_are_read_only_and_use_console_time(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        seeded = await _seed_memory(session, tenant.tenant_id, user_id)
        await _seed_history(
            session,
            tenant_id=tenant.tenant_id,
            user_id=user_id,
            memory_key=seeded["memory_key"],
            origin_id=seeded["id"],
            version=1,
        )
        await session.commit()

    statements: list[str] = []

    def capture(*args: Any) -> None:
        statements.append(str(args[2]))

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        history = await client.get(
            f"/api/v1/users/{user_id}/memory/{seeded['id']}/history", headers=_headers(tenant)
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert listing.status_code == 200 and history.status_code == 200
    runtime_writes = [
        statement
        for statement in statements
        if "runtime.user_memory" in statement
        and not statement.lower().lstrip().startswith(("select", "with"))
    ]
    assert runtime_writes == [], "Console reads must not write runtime memory tables"
    assert listing.json()["data"]["items"][0]["update_time"] == format_console_time(
        seeded["update_time"]
    )


async def test_local_04_unknown_memory_history_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    missing = await client.get(
        f"/api/v1/users/{user_id}/memory/{uuid.uuid4()}/history", headers=_headers(tenant)
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "MEMORY_NOT_FOUND"


async def test_local_05_cross_tenant_user_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    foreign_user = str(uuid.uuid4())
    foreign_tenant = await _seed_foreign_tenant(foreign_user, "v1.slot.preference.output_style.foreign")
    try:
        listing = await client.get(f"/api/v1/users/{foreign_user}/memory", headers=_headers(tenant))
        assert listing.status_code in (403, 404)
        assert listing.json()["code"] in ("COMMON_NOT_FOUND", "FORBIDDEN")
        assert foreign_user not in listing.text
        history = await client.get(
            f"/api/v1/users/{foreign_user}/memory/{uuid.uuid4()}/history",
            headers=_headers(tenant),
        )
        assert history.status_code in (403, 404)
    finally:
        await _drop_foreign_tenant(foreign_tenant)


async def test_local_06_expired_rows_are_reported_not_hidden(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _seed_memory(
            session,
            tenant.tenant_id,
            user_id,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await _seed_memory(session, tenant.tenant_id, user_id)
        await session.commit()

    expired = await client.get(
        f"/api/v1/users/{user_id}/memory",
        params={"status": "EXPIRED"},
        headers=_headers(tenant),
    )
    assert expired.json()["data"]["total"] == 1
    listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    data = listing.json()["data"]
    assert data["total"] == len(data["items"]) == 2


async def test_local_07_history_paginates_by_version(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        seeded = await _seed_memory(session, tenant.tenant_id, user_id)
        for version in (1, 2, 3):
            await _seed_history(
                session,
                tenant_id=tenant.tenant_id,
                user_id=user_id,
                memory_key=seeded["memory_key"],
                origin_id=seeded["id"],
                version=version,
            )
        await session.commit()

    page = await client.get(
        f"/api/v1/users/{user_id}/memory/{seeded['id']}/history",
        params={"page": 1, "page_size": 2},
        headers=_headers(tenant),
    )
    assert page.json()["data"]["total"] == 3
    assert [item["version"] for item in page.json()["data"]["items"]] == [3, 2]
