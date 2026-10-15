"""TASK-024 辅助验收：Console 记忆状态/历史只读仓储（LOCAL，unit/integration）。

真实边界：真实 Console HTTP（ASGI + 真实会话鉴权）+ 真实 PostgreSQL 的 0020 head schema
（`runtime.user_memory` / `runtime.user_memory_history`）；不 mock 仓储、不 mock 数据库。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient
from muad_common import SharedSettings
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
    """写一条真实 `runtime.user_memory` 行，覆盖 0020 的 author/确认/TTL 约束。"""
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "user_id": uuid.UUID(user_id),
        "memory_key": f"key-{uuid.uuid4().hex[:8]}",
        "category": "PREFERENCE",
        "content": {"note": "likes concise answers"},
        "author": "AGENT",
        "explicit_request": False,
        "confirmation_event_id": None,
        "enabled": True,
        "version": 1,
        "expires_at": now + timedelta(days=30),
        "source_ref": None,
        "source_quote": None,
        "create_time": now,
        "update_time": now,
        "is_deleted": False,
    }
    row.update(overrides)
    await session.execute(
        text(
            "INSERT INTO runtime.user_memory "
            "(id, tenant_id, user_id, memory_key, category, content_json, author, explicit_request, "
            "confirmation_event_id, enabled, version, expires_at, source_ref, source_quote, "
            "create_time, update_time, is_deleted) "
            "VALUES (:id, :tenant_id, :user_id, :memory_key, :category, CAST(:content AS jsonb), "
            ":author, :explicit_request, :confirmation_event_id, :enabled, :version, :expires_at, "
            ":source_ref, :source_quote, :create_time, :update_time, :is_deleted)"
        ),
        {**row, "content": json.dumps(row["content"])},
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
    actor_type: str = "ADMIN",
    actor_id: str | None = None,
    command_id: str | None = None,
    create_time: datetime | None = None,
    after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "user_id": uuid.UUID(user_id),
        "memory_key": memory_key,
        "origin_id": origin_id,
        "version": version,
        "change_reason": change_reason,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "command_id": command_id or f"cmd-{uuid.uuid4().hex[:8]}",
        "source_run_id": None,
        "after": after if after is not None else {"content": {"note": "v"}, "version": version},
        "create_time": create_time or datetime.now(UTC),
    }
    await session.execute(
        text(
            "INSERT INTO runtime.user_memory_history "
            "(id, tenant_id, user_id, memory_key, origin_id, version, change_reason, actor_type, "
            "actor_id, command_id, after_json, create_time) "
            "VALUES (:id, :tenant_id, :user_id, :memory_key, :origin_id, :version, :change_reason, "
            ":actor_type, :actor_id, :command_id, CAST(:after AS jsonb), :create_time)"
        ),
        {**row, "after": json.dumps(row["after"])},
    )
    return row


async def _seed_foreign_tenant(user_id: str, memory_key: str) -> str:
    """另建一个租户的用户 + 一条记忆 + 一个可登录管理员，返回该租户 id。"""
    tenant_id = f"test-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_user (id, tenant_id, user_code, display_name) "
                "VALUES (:id, :tenant_id, :user_code, 'Foreign User')"
            ),
            {"id": uuid.UUID(user_id), "tenant_id": tenant_id, "user_code": f"u-{uuid.uuid4().hex[:8]}"},
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


async def test_local_01_list_filters_status_author_category_with_tenant_scope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    other_user = await _create_user(client, tenant)
    base = datetime.now(UTC)
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _seed_memory(
            session, tenant.tenant_id, user_id,
            memory_key="pref-active", update_time=base - timedelta(minutes=1),
        )
        await _seed_memory(
            session, tenant.tenant_id, user_id,
            memory_key="style-disabled", category="WORK_STYLE", author="ADMIN",
            enabled=False, version=2, update_time=base - timedelta(minutes=2),
        )
        await _seed_memory(
            session, tenant.tenant_id, user_id,
            memory_key="explicit-expired", category="EXPLICIT", author="USER",
            explicit_request=True, confirmation_event_id=uuid.uuid4(),
            version=3, expires_at=base - timedelta(hours=1),
            update_time=base - timedelta(minutes=3),
        )
        await _seed_memory(
            session, tenant.tenant_id, user_id,
            memory_key="task-open", category="TASK_STATE", update_time=base - timedelta(minutes=4),
        )
        await _seed_memory(
            session, tenant.tenant_id, other_user, memory_key="other-user-key",
            author="USER", explicit_request=True, confirmation_event_id=uuid.uuid4(),
        )
        await session.commit()

    listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == 4
    assert page["total"] == len(page["items"]), "total 必须与同一筛选条件下的 items 齐平"
    assert [item["memory_key"] for item in page["items"]] == [
        "pref-active", "style-disabled", "explicit-expired", "task-open",
    ], "列表按 update_time 倒序"
    assert all("source_type" not in item for item in page["items"]), "旧 source_type 投影必须移除"
    assert {item["memory_key"]: item["status"] for item in page["items"]} == {
        "pref-active": "ACTIVE",
        "style-disabled": "DISABLED",
        "explicit-expired": "EXPIRED",
        "task-open": "ACTIVE",
    }
    assert {item["memory_key"]: item["author"] for item in page["items"]} == {
        "pref-active": "AGENT",
        "style-disabled": "ADMIN",
        "explicit-expired": "USER",
        "task-open": "AGENT",
    }
    assert page["items"][2]["explicit_request"] is True

    for query, expected_keys in (
        ({"status": "EXPIRED"}, ["explicit-expired"]),
        ({"author": "ADMIN"}, ["style-disabled"]),
        ({"category": "TASK_STATE"}, ["task-open"]),
        ({"status": "ACTIVE", "author": "AGENT"}, ["pref-active", "task-open"]),
    ):
        filtered = await client.get(
            f"/api/v1/users/{user_id}/memory", params=query, headers=_headers(tenant)
        )
        assert filtered.status_code == 200
        data = filtered.json()["data"]
        assert [item["memory_key"] for item in data["items"]] == expected_keys
        assert data["total"] == len(expected_keys), f"{query} 的 total 必须与 items 齐平"

    empty = await client.get(
        f"/api/v1/users/{user_id}/memory",
        params={"author": "ADMIN", "status": "ACTIVE"},
        headers=_headers(tenant),
    )
    assert empty.json()["data"] == {"items": [], "page": 1, "page_size": 20, "total": 0}

    second_page = await client.get(
        f"/api/v1/users/{user_id}/memory",
        params={"page": 2, "page_size": 3},
        headers=_headers(tenant),
    )
    assert second_page.json()["data"]["total"] == 4
    assert [item["memory_key"] for item in second_page.json()["data"]["items"]] == ["task-open"]


async def test_local_02_deleted_hidden_from_list_but_history_paginates_by_version(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    base = datetime.now(UTC)
    session_factory = get_session_factory()
    async with session_factory() as session:
        deleted = await _seed_memory(
            session, tenant.tenant_id, user_id,
            memory_key="pref-deleted", author="USER", explicit_request=True,
            confirmation_event_id=uuid.uuid4(), enabled=False, version=3, is_deleted=True,
        )
        for version, reason, actor_type, actor_id in (
            (1, "MIGRATION_BASELINE", "SYSTEM", "migration-0020"),
            (2, "CREATE", "ADMIN", str(uuid.uuid4())),
            (3, "ADMIN_DELETE", "ADMIN", str(uuid.uuid4())),
        ):
            await _seed_history(
                session, tenant_id=tenant.tenant_id, user_id=user_id,
                memory_key="pref-deleted", origin_id=deleted["id"], version=version,
                change_reason=reason, actor_type=actor_type, actor_id=actor_id,
                create_time=base + timedelta(minutes=version),
                after={"version": version, "source_type": "USER_EXPLICIT"},
            )
        await session.commit()

    listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert listing.json()["data"] == {"items": [], "page": 1, "page_size": 20, "total": 0}, (
        "已删除记忆不得出现在列表中"
    )

    history = await client.get(
        f"/api/v1/users/{user_id}/memory/pref-deleted/history", headers=_headers(tenant)
    )
    assert history.status_code == 200
    data = history.json()["data"]
    assert data["total"] == 3
    assert [item["version"] for item in data["items"]] == [3, 2, 1], "历史按 version 倒序"
    latest = data["items"][0]
    assert latest["change_reason"] == "ADMIN_DELETE"
    assert latest["actor_type"] == "ADMIN"
    assert latest["actor_id"]
    assert latest["command_id"]
    assert latest["after"]["version"] == 3
    assert "source_type" not in latest

    paged = await client.get(
        f"/api/v1/users/{user_id}/memory/pref-deleted/history",
        params={"page": 1, "page_size": 2},
        headers=_headers(tenant),
    )
    assert paged.json()["data"]["total"] == 3
    assert [item["version"] for item in paged.json()["data"]["items"]] == [3, 2]
    tail = await client.get(
        f"/api/v1/users/{user_id}/memory/pref-deleted/history",
        params={"page": 3, "page_size": 2},
        headers=_headers(tenant),
    )
    assert tail.json()["data"] == {"items": [], "page": 3, "page_size": 2, "total": 3}

    unknown = await client.get(
        f"/api/v1/users/{user_id}/memory/no-such-key/history", headers=_headers(tenant)
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "MEMORY_NOT_FOUND"


async def test_local_03_reads_are_read_only_and_share_console_time_projection(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    base = datetime.now(UTC).replace(microsecond=0)
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await _seed_memory(
            session, tenant.tenant_id, user_id,
            memory_key="pref-audited", author="ADMIN", version=2,
            source_ref="run-ref-1", source_quote="请记住我偏好简洁回答",
            expires_at=base + timedelta(days=7), update_time=base,
        )
        actor_id = str(uuid.uuid4())
        history = await _seed_history(
            session, tenant_id=tenant.tenant_id, user_id=user_id,
            memory_key="pref-audited", origin_id=row["id"], version=2,
            change_reason="OVERWRITE", actor_type="ADMIN", actor_id=actor_id,
            create_time=base,
        )
        await session.commit()

    statements: list[str] = []
    engine = get_engine()

    def capture(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        history_response = await client.get(
            f"/api/v1/users/{user_id}/memory/pref-audited/history", headers=_headers(tenant)
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)

    assert listing.status_code == 200
    assert history_response.status_code == 200
    memory_statements = [s for s in statements if "runtime.user_memory" in s]
    assert memory_statements, "读列表/历史必须产生真实 SQL"
    lowered = [s.lower().lstrip() for s in memory_statements]
    assert all(s.startswith(("select", "with")) for s in lowered), (
        "本任务读路径只读 runtime 表：" + "; ".join(s[:60] for s in memory_statements)
    )

    item = listing.json()["data"]["items"][0]
    assert item["author"] == "ADMIN" and item["version"] == 2
    assert item["create_time"] == format_console_time(row["create_time"]), (
        "时间必须复用 Console 统一投影 format_console_time"
    )
    assert item["update_time"] == format_console_time(row["update_time"])
    assert item["expires_at"] == format_console_time(row["expires_at"])
    assert item["last_confirmed_at"] is None
    assert item["source_quote"] == "请记住我偏好简洁回答"

    entry = history_response.json()["data"]["items"][0]
    assert entry["actor_type"] == "ADMIN" and entry["actor_id"] == actor_id
    assert entry["create_time"] == format_console_time(history["create_time"])
    assert entry["after"]["version"] == 2


async def test_local_04_unknown_and_legacy_values_fall_back_explicitly(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await _seed_memory(
            session, tenant.tenant_id, user_id, memory_key="key-legacy", author="AGENT"
        )
        await _seed_history(
            session, tenant_id=tenant.tenant_id, user_id=user_id,
            memory_key="key-legacy", origin_id=row["id"], version=1,
            change_reason="LEGACY_REASON_V9", actor_type="LEGACY_SYNC", actor_id="legacy-writer",
            after={"source_type": "AGENT_INFERRED", "legacy": True},
        )
        await session.commit()

    history = await client.get(
        f"/api/v1/users/{user_id}/memory/key-legacy/history", headers=_headers(tenant)
    )
    assert history.status_code == 200
    item = history.json()["data"]["items"][0]
    assert item["actor_type"] == "LEGACY_SYNC", "未知 actor 原样回退，不得臆造成已知作者"
    assert item["change_reason"] == "LEGACY_REASON_V9"
    assert item["after"] == {"source_type": "AGENT_INFERRED", "legacy": True}
    assert "source_type" not in item, "旧 source_type 不得再作为当前来源投影"

    listing = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert "source_type" not in listing.json()["data"]["items"][0]
    assert listing.json()["data"]["items"][0]["author"] == "AGENT"

    for query in ({"status": "BOGUS"}, {"author": "BOGUS"}, {"category": "BOGUS"}):
        invalid = await client.get(
            f"/api/v1/users/{user_id}/memory", params=query, headers=_headers(tenant)
        )
        assert invalid.status_code == 422, f"{query} 必须被拒绝"
        assert invalid.json()["code"] == "COMMON_VALIDATION_ERROR"

    invalid_key = await client.get(
        f"/api/v1/users/{user_id}/memory/Not_A_Key!/history", headers=_headers(tenant)
    )
    assert invalid_key.status_code == 422
    assert invalid_key.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_local_05_cross_tenant_user_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    own_user = await _create_user(client, tenant)
    foreign_user = str(uuid.uuid4())
    foreign_tenant = await _seed_foreign_tenant(foreign_user, "foreign-key")
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            foreign_memory_key = await session.scalar(
                text("SELECT memory_key FROM runtime.user_memory WHERE tenant_id = :tenant_id"),
                {"tenant_id": foreign_tenant},
            )
            foreign_username = await session.scalar(
                text("SELECT username FROM control.console_account WHERE tenant_id = :tenant_id"),
                {"tenant_id": foreign_tenant},
            )
        assert foreign_memory_key == "foreign-key"
        assert foreign_username
        listing = await client.get(f"/api/v1/users/{foreign_user}/memory", headers=_headers(tenant))
        assert listing.status_code == 404
        assert listing.json()["code"] == "COMMON_NOT_FOUND"

        history = await client.get(
            f"/api/v1/users/{foreign_user}/memory/foreign-key/history", headers=_headers(tenant)
        )
        assert history.status_code == 404
        assert history.json()["code"] == "COMMON_NOT_FOUND"

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": foreign_username, "password": ADMIN_PASSWORD},
            headers={"X-Tenant-Id": foreign_tenant},
        )
        assert login.status_code == 200
        reverse = await client.get(
            f"/api/v1/users/{own_user}/memory",
            headers={"X-Tenant-Id": foreign_tenant},
        )
        assert reverse.status_code == 404
        assert reverse.json()["code"] == "COMMON_NOT_FOUND"
        assert own_user not in reverse.text
    finally:
        await _drop_foreign_tenant(foreign_tenant)


def _sync_dsn() -> str:
    """同一 `DATABASE_URL` 的同步 DSN：注入写入要用另一条连接提交，不能借用被测会话。"""
    return SharedSettings().require_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


@contextmanager
def _committed_write_after_first_read(
    table: str, mutate: Callable[[psycopg.Connection[Any]], None]
) -> Iterator[None]:
    """在请求对目标表的**第一条**读取语句完成后，用另一条连接提交一次真实写入。

    READ COMMITTED 下每条语句各取快照：仓储若分两条语句取 count 与行集，这条提交只被第二条
    语句看见 —— 正是评审条目 4 的 total/items 分叉；单条语句实现整段只看同一个快照。
    """
    engine = get_engine()
    fired = False

    def on_after_execute(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal fired
        if fired or re.search(rf"\b{re.escape(table)}\b", statement) is None:
            return
        fired = True
        with psycopg.connect(_sync_dsn(), autocommit=True) as connection:
            mutate(connection)

    event.listen(engine.sync_engine, "after_cursor_execute", on_after_execute)
    try:
        yield
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", on_after_execute)
        assert fired, f"故障注入未触发：请求没有读取 {table}"


@pytest.mark.integration
async def test_local_06_list_total_and_items_share_one_snapshot_across_expiry_boundary(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """到期跨界：行在 count 与行集之间越过 `expires_at`（评审条目 4 的 total=0/items=1）。"""
    user_id = await _create_user(client, tenant)
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _seed_memory(session, tenant.tenant_id, user_id, memory_key="expiry-crossing")
        await session.commit()

    def expire_row(connection: psycopg.Connection[Any]) -> None:
        connection.execute(
            "UPDATE runtime.user_memory SET expires_at = statement_timestamp() - interval '1 hour' "
            "WHERE tenant_id = %s AND user_id = %s",
            (tenant.tenant_id, uuid.UUID(user_id)),
        )

    with _committed_write_after_first_read("runtime.user_memory", expire_row):
        listing = await client.get(
            f"/api/v1/users/{user_id}/memory",
            params={"status": "EXPIRED"},
            headers=_headers(tenant),
        )

    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == len(page["items"]), (
        "count 与行集必须同一快照：到期跨界不得让 total=0 而 items 有 1 条"
    )
    assert (page["total"], len(page["items"])) == (0, 0), (
        "单条语句的快照里该行仍 ACTIVE，EXPIRED 过滤不得只落在其中一半"
    )


@pytest.mark.integration
async def test_local_07_history_total_and_items_share_one_snapshot_on_concurrent_append(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """并发追加：新版本在 count 与行集之间提交，历史 total 与 items 必须同一快照。"""
    user_id = await _create_user(client, tenant)
    base = datetime.now(UTC)
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await _seed_memory(session, tenant.tenant_id, user_id, memory_key="history-crossing")
        await _seed_history(
            session, tenant_id=tenant.tenant_id, user_id=user_id,
            memory_key="history-crossing", origin_id=row["id"], version=1,
            change_reason="CREATE", actor_type="ADMIN", actor_id=str(uuid.uuid4()),
            create_time=base,
        )
        await session.commit()

    appended = {"id": uuid.uuid4(), "actor_id": str(uuid.uuid4())}

    def append_version(connection: psycopg.Connection[Any]) -> None:
        connection.execute(
            "INSERT INTO runtime.user_memory_history "
            "(id, tenant_id, user_id, memory_key, origin_id, version, change_reason, actor_type, "
            "actor_id, command_id, after_json, create_time) "
            "VALUES (%s, %s, %s, %s, %s, 2, 'OVERWRITE', 'ADMIN', %s, %s, CAST(%s AS jsonb), %s)",
            (
                appended["id"], tenant.tenant_id, uuid.UUID(user_id), "history-crossing",
                row["id"], appended["actor_id"], f"cmd-{uuid.uuid4().hex[:8]}",
                json.dumps({"version": 2}), base + timedelta(minutes=1),
            ),
        )

    with _committed_write_after_first_read("runtime.user_memory_history", append_version):
        history = await client.get(
            f"/api/v1/users/{user_id}/memory/history-crossing/history", headers=_headers(tenant)
        )

    assert history.status_code == 200
    data = history.json()["data"]
    assert data["total"] == len(data["items"]), (
        "历史 count 与行集必须同一快照：并发追加不得让 total=1 而 items 有 2 条"
    )
    assert (data["total"], [item["version"] for item in data["items"]]) == (1, [1]), (
        "单条语句的快照里 v2 尚未提交"
    )
