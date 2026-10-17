"""B-212: digest-only Chat access tokens and independent CHAT sessions (0024).

0024 建 Console 侧 `control.chat_access_token` / `control.chat_session`（design §3.3）：

- 两张表只存 `token_hash` 摘要（varchar(64) + 小写十六进制形态 CHECK），时间列全部带时区，
  `expires_at > issued_at` 由数据库保证；
- `platform_user_id` 与 `access_token_id` 都是同 Owner 物理 FK（control → control）；
- 每用户至多一个未撤销 Token（partial unique `WHERE is_deleted = false AND revoked_at IS NULL`）：
  旧记录（含已过期但未撤销的）先显式撤销，再按更高 revision 新签；旧记录保留、不物理删除；
  软删 tombstone 不阻塞重录；
- 会话以独立随机 Token 摘要标识（用户 Token 原文不进 Cookie），同一来源 Token 可有多条会话，
  撤销 Token 时按 access_token_id 活跃索引关联撤销。

回退仅在两张表都为空时允许，否则 `CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP`。
"""

import asyncio
import hashlib
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
TABLES = ("chat_access_token", "chat_session")
TIMES = ("issued_at", "expires_at", "revoked_at")
STANDARD = {"id", "is_deleted", "create_time", "update_time", "tenant_id"}
FORBIDDEN_PLAINTEXT = {"token", "raw_token", "access_token", "secret", "token_plain"}


def _models() -> Any:
    import_module("muad_console_platform.infrastructure.models.control")  # platform_user FK 目标
    return import_module("muad_console_platform.infrastructure.models.auth")


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _structure(connection: sa.Connection, name: str) -> tuple[object, ...]:
    inspector = sa.inspect(connection)
    columns = sorted(
        (c["name"], str(c["type"]), c["nullable"], c["default"], getattr(c["type"], "timezone", None))
        for c in inspector.get_columns(name, "control")
    )
    indexes = sorted(
        (i["name"], tuple(i["column_names"]), i["unique"],
         str(i.get("dialect_options", {}).get("postgresql_where", "")))
        for i in inspector.get_indexes(name, "control")
    )
    checks = sorted((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(name, "control"))
    foreign = sorted(
        (tuple(f["constrained_columns"]), f["referred_schema"], f["referred_table"],
         tuple(f["referred_columns"]))
        for f in inspector.get_foreign_keys(name, "control")
    )
    unique = sorted((u["name"], tuple(u["column_names"]))
                    for u in inspector.get_unique_constraints(name, "control"))
    primary = inspector.get_pk_constraint(name, "control")["constrained_columns"]
    return columns, indexes, checks, foreign, unique, primary


def _columns(structure: tuple[object, ...]) -> dict[str, tuple[object, ...]]:
    return {str(column[0]): column for column in structure[0]}  # type: ignore[index]


def _index(structure: tuple[object, ...], name: str) -> tuple[object, ...]:
    return next(item for item in structure[1] if item[0] == name)  # type: ignore[union-attr]


async def _migrate(
    info: dict[str, str], tmp_path: Path, *, direction: str, target: str,
) -> subprocess.CompletedProcess[str]:
    ini = tmp_path / "chat_auth.ini"
    ini.write_text("[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
                   f"sqlalchemy.url = {info['database_url']}\n")
    return await asyncio.to_thread(
        subprocess.run, [sys.executable, "migrations/db_migrate.py", "-c", str(ini), direction, target],
        capture_output=True, text=True, timeout=120,
    )


async def _platform_user(
    connection: AsyncConnection, *, tenant: str = "b212", user: UUID | None = None,
) -> UUID:
    identity = user or uuid4()
    await connection.execute(sa.text(
        "INSERT INTO control.platform_user (id,tenant_id,user_code,display_name) "
        "VALUES (:id,:tenant,:code,'Webchat User')"
    ), {"id": identity, "tenant": tenant, "code": str(identity)})
    return identity


def _token_values(user: UUID, *, tenant: str = "b212", digest: str | None = None,
                  **changes: object) -> dict[str, object]:
    issued = datetime.now(UTC)
    values: dict[str, object] = {
        "tenant_id": tenant,
        "platform_user_id": user,
        "token_hash": digest or _digest(uuid4().hex),
        "revision": 1,
        "issued_at": issued,
        "expires_at": issued + timedelta(days=30),
    }
    values.update(changes)
    return values


async def _insert(connection: AsyncConnection, table: sa.Table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(table.insert().values(**values))


async def test_b212_empty_database_schema_parity(async_tool_database: AsyncEngine) -> None:
    models = _models()
    token_table, session_table = models.ChatAccessToken.__table__, models.ChatSession.__table__
    async with async_tool_database.begin() as connection:
        before = {name: await connection.run_sync(_structure, name) for name in TABLES}
        await connection.execute(sa.text("DROP SCHEMA control CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA control"))
        await connection.run_sync(models.Base.metadata.create_all)
        created = {name: await connection.run_sync(_structure, name) for name in TABLES}
    assert before == created
    assert token_table.name == "chat_access_token" and session_table.name == "chat_session"
    for structure in before.values():
        assert STANDARD <= set(_columns(structure))
        # 只存摘要：结构上没有存放原文的列，摘要列是 varchar(64)。
        assert not (FORBIDDEN_PLAINTEXT & set(_columns(structure)))
        assert _columns(structure)["token_hash"][1] == "VARCHAR(64)"
    for name in TABLES:
        columns = _columns(before[name])
        # 时间列全部带时区；签发/到期不可空，撤销时间可空；issued_at 默认 now()。
        for column in TIMES:
            assert columns[column][4] is True
        assert columns["issued_at"][2] is False and columns["expires_at"][2] is False
        assert columns["revoked_at"][2] is True and columns["revoked_at"][3] is None
        assert "now()" in str(columns["issued_at"][3])
        # 来源 Token / platform_user 都是同 Owner 物理 FK。
        assert (("platform_user_id",), "control", "platform_user", ("id",)) in set(before[name][3])
    assert (("access_token_id",), "control", "chat_access_token", ("id",)) in set(before["chat_session"][3])

    columns = _columns(before["chat_access_token"])
    assert columns["revision"][1] == "BIGINT" and columns["revision"][2] is False
    checks = {check[0] for check in before["chat_access_token"][2]}
    assert {"ck_chat_access_token_revision", "ck_chat_access_token_expires_after_issued",
            "ck_chat_access_token_token_hash"} <= checks
    checks = {check[0] for check in before["chat_session"][2]}
    assert {"ck_chat_session_expires_after_issued", "ck_chat_session_token_hash"} <= checks
    # 每用户至多一个未撤销 Token；revision 唯一；摘要唯一（前两者以软删为界）。
    digest_index = _index(before["chat_access_token"], "uq_chat_access_token_token_hash")
    assert digest_index[1] == ("token_hash",) and digest_index[2] is True
    assert "is_deleted = false" in digest_index[3]
    revision_index = _index(before["chat_access_token"], "uq_chat_access_token_tenant_user_revision")
    assert revision_index[1] == ("tenant_id", "platform_user_id", "revision") and revision_index[2] is True
    assert "is_deleted = false" in revision_index[3]
    unrevoked = _index(before["chat_access_token"], "uq_chat_access_token_tenant_user_unrevoked")
    assert unrevoked[1] == ("tenant_id", "platform_user_id") and unrevoked[2] is True
    assert "is_deleted = false" in unrevoked[3] and "revoked_at IS NULL" in unrevoked[3]
    assert _index(before["chat_session"], "uq_chat_session_token_hash")[1] == ("token_hash",)
    assert _index(before["chat_session"], "uq_chat_session_token_hash")[2] is True
    assert "is_deleted = false" in _index(before["chat_session"], "uq_chat_session_token_hash")[3]
    expires_index = _index(before["chat_session"], "ix_chat_session_tenant_user_expires")
    assert expires_index[1] == ("tenant_id", "platform_user_id", "expires_at") and expires_index[2] is False
    token_index = _index(before["chat_session"], "ix_chat_session_access_token")
    assert token_index[1] == ("access_token_id",) and token_index[2] is False
    assert "is_deleted = false" in token_index[3]  # 活跃（未软删）会话索引


async def test_b212_token_and_session_stores_only_digests_with_timezone_times(
    async_tool_database: AsyncEngine,
) -> None:
    models = _models()
    token = models.ChatAccessToken.__table__
    async with async_tool_database.begin() as connection:
        user = await _platform_user(connection)
        raw = "chat-raw-" + uuid4().hex
        token_id = (await connection.execute(token.insert().values(
            **_token_values(user=user, digest=_digest(raw))
        ).returning(token.c.id))).scalar_one()
        row = (await connection.execute(token.select().where(token.c.id == token_id))).mappings().one()
        assert row["token_hash"] == _digest(raw) and row["revision"] == 1
        assert row["revoked_at"] is None and row["is_deleted"] is False
        assert row["expires_at"] - row["issued_at"] == timedelta(days=30)
        # 原文（随机 Token 的常见形态、长度不等于 64）与非法摘要形态都被拒。
        await _insert(connection, token, _token_values(user=user, token_hash=raw))
        await _insert(connection, token, _token_values(user=user, token_hash="z" * 64))
        with pytest.raises(sa.exc.DBAPIError) as truncated:
            async with connection.begin_nested():
                await connection.execute(token.insert().values(
                    **_token_values(user=user, token_hash="a" * 65)))
        assert "varying(64)" in str(truncated.value)  # varchar(64) 的物理上限：摘要不可能是别的形态
        now = datetime.now(UTC)
        for expires in (now, now - timedelta(seconds=1)):
            await _insert(connection, token, _token_values(user=user, issued_at=now, expires_at=expires))
        for changes in ({"revision": 0}, {"revision": -1}):
            await _insert(connection, token, _token_values(user=user, **changes))
        await _insert(connection, token, _token_values(user=uuid4()))  # 同 Owner FK：用户必须存在


async def test_b212_one_unrevoked_token_per_user_revisions_and_tombstones(
    async_tool_database: AsyncEngine,
) -> None:
    token = _models().ChatAccessToken.__table__
    async with async_tool_database.begin() as connection:
        user = await _platform_user(connection)
        first = _digest("token-v1")
        first_id = (await connection.execute(token.insert().values(
            **_token_values(user=user, digest=first)).returning(token.c.id))).scalar_one()
        # 未撤销 Token 仍在：不得并行再签（不同摘要、更高 revision 仍被撤销约束拒绝）。
        await _insert(connection, token, _token_values(user=user, revision=2))
        await connection.execute(token.update().where(token.c.id == first_id).values(
            revoked_at=sa.func.now(), update_time=sa.func.now()))
        second = _digest("token-v2")
        second_id = (await connection.execute(token.insert().values(
            **_token_values(user=user, digest=second, revision=2)).returning(token.c.id))).scalar_one()
        rows = (await connection.execute(token.select().where(
            token.c.platform_user_id == user).order_by(token.c.revision))).mappings().all()
        # 旧记录保留（不物理删除），最新 revision 是唯一未撤销行。
        assert [row["revision"] for row in rows] == [1, 2]
        assert rows[0]["id"] == first_id and rows[0]["revoked_at"] is not None
        assert rows[1]["id"] == second_id and rows[1]["revoked_at"] is None
        # 同用户同 revision 不得重复（不同摘要，隔离 revision 唯一）。
        await _insert(connection, token, _token_values(user=user, revision=2))
        # 活跃摘要全局唯一：同租户另一用户、其他租户都被拒（摘要标识不按租户复用）。
        other = await _platform_user(connection)
        await _insert(connection, token, _token_values(user=other, digest=second))
        other_tenant_user = await _platform_user(connection, tenant="b212b")
        await _insert(connection, token, _token_values(user=other_tenant_user, tenant="b212b", digest=second))
        # 软删 tombstone 不占用摘要与 revision 身份：同摘要同 revision 可以重录。
        await connection.execute(token.update().where(token.c.id == second_id).values(
            is_deleted=True, update_time=sa.func.now()))
        await connection.execute(token.insert().values(
            **_token_values(user=user, digest=second, revision=2)))
        # 过期记录同样必须先显式撤销：撤销前新签被拒，撤销后成功。
        expired_user = await _platform_user(connection)
        issued = datetime.now(UTC) - timedelta(days=40)
        await connection.execute(token.insert().values(**_token_values(
            user=expired_user, issued_at=issued, expires_at=issued + timedelta(days=30))))
        await _insert(connection, token, _token_values(user=expired_user, revision=2))
        await connection.execute(token.update().where(token.c.platform_user_id == expired_user).values(
            revoked_at=sa.func.now(), update_time=sa.func.now()))
        await connection.execute(token.insert().values(
            **_token_values(user=expired_user, revision=2,
                            expires_at=datetime.now(UTC) + timedelta(days=30))))


async def test_b212_chat_session_requires_persisted_token_and_user_and_digest(
    async_tool_database: AsyncEngine,
) -> None:
    models = _models()
    token, session = models.ChatAccessToken.__table__, models.ChatSession.__table__
    async with async_tool_database.begin() as connection:
        user = await _platform_user(connection)
        token_id = (await connection.execute(token.insert().values(
            **_token_values(user=user)).returning(token.c.id))).scalar_one()
        issued = datetime.now(UTC)
        base: dict[str, object] = {
            "tenant_id": "b212",
            "access_token_id": token_id,
            "platform_user_id": user,
            "token_hash": _digest("session-v1"),
            "issued_at": issued,
            "expires_at": issued + timedelta(hours=12),  # 本会话期限由服务端保证 ≤ 来源 Token 期限
        }
        session_id = (await connection.execute(session.insert().values(**base).returning(
            session.c.id))).scalar_one()
        row = (await connection.execute(session.select().where(session.c.id == session_id))).mappings().one()
        assert row["access_token_id"] == token_id and row["revoked_at"] is None
        assert row["is_deleted"] is False and row["expires_at"] - row["issued_at"] == timedelta(hours=12)
        # 同一来源 Token 的多条独立会话可以并存（logout 只撤销本会话）。
        await connection.execute(session.insert().values(
            **{**base, "token_hash": _digest("session-v2")}))
        # 来源 Token / platform_user 必须是已存在的同 Owner 行。
        await _insert(connection, session, {**base, "token_hash": _digest("session-v3"),
                                            "access_token_id": uuid4()})
        await _insert(connection, session, {**base, "token_hash": _digest("session-v4"),
                                            "platform_user_id": uuid4()})
        raw = "session-raw-" + uuid4().hex
        await _insert(connection, session, {**base, "token_hash": raw})
        await _insert(connection, session, {**base, "token_hash": "A" * 64})
        await _insert(connection, session, {**base, "expires_at": issued})
        await _insert(connection, session, {**base, "token_hash": _digest("session-v1")})  # 活跃摘要唯一
        await connection.execute(session.update().where(session.c.id == session_id).values(
            is_deleted=True, update_time=sa.func.now()))
        await connection.execute(session.insert().values(
            **{**base, "token_hash": _digest("session-v1")}))  # tombstone 释放摘要
        live = (await connection.execute(sa.text(
            "SELECT count(*) FROM control.chat_session "
            "WHERE access_token_id = :id AND is_deleted = false"), {"id": token_id})).scalar_one()
        assert live == 2  # session-v2 + 重录的 session-v1；tombstone 不计


async def test_b212_downgrade_requires_backup_for_issued_credentials(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path,
) -> None:
    # 只验证 0024 自身的回退门禁：建库停在 0024（head 之后的 revision 有自己的门禁）
    info = datastore_at_revision("0024")
    models = _models()
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            user = await _platform_user(connection)
            token_id = (await connection.execute(models.ChatAccessToken.__table__.insert().values(
                **_token_values(user=user)).returning(models.ChatAccessToken.__table__.c.id))).scalar_one()
            issued = datetime.now(UTC)
            await connection.execute(models.ChatSession.__table__.insert().values(
                tenant_id="b212", access_token_id=token_id, platform_user_id=user,
                token_hash=_digest("session-v1"), issued_at=issued,
                expires_at=issued + timedelta(hours=12)))
            head = (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()
    result = await _migrate(info, tmp_path, direction="downgrade", target="0023")
    assert result.returncode != 0 and "CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP" in result.stderr
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == head
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("control")))
            assert set(TABLES) <= tables
            assert (await connection.execute(sa.text(
                "SELECT count(*) FROM control.chat_access_token"))).scalar_one() == 1
    finally:
        await engine.dispose()


async def test_b212_downgrade_on_pristine_schema_returns_to_0023(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path,
) -> None:
    info = datastore_at_revision("0024")
    result = await _migrate(info, tmp_path, direction="downgrade", target="0023")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0023"
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("control")))
            assert not (set(TABLES) & tables)
    finally:
        await engine.dispose()
    rerun = await _migrate(info, tmp_path, direction="upgrade", target="head")
    assert rerun.returncode == 0, rerun.stderr
