"""[B-07] `control.console_account` / `control.console_session` 的真实数据契约（design §3.3）。

真实边界：真实 PostgreSQL **系统目录**（`information_schema.columns`、`pg_index`/`pg_class`、
`pg_constraint`）——不以迁移文件内容代替对真实库结构的断言。ORM 元数据与 DDL 的一致性由
RULE-data-001 的原 verifier（`tests/console_platform/test_schema_parity.py`，argv
`pytest -q tests -k schema_parity`）覆盖；本文件负责该规则的口径部分：标准列、软删
partial unique、`timestamptz`、同 schema 物理 FK、关键索引。
"""

from __future__ import annotations

import sqlalchemy as sa
from muad_console_platform.infrastructure.db import get_session_factory

SCHEMA = "control"
ACCOUNT = "console_account"
SESSION = "console_session"

STANDARD_COLUMNS = ("id", "is_deleted", "create_time", "update_time")
TIMESTAMPTZ = "timestamp with time zone"
NAIVE_TIMESTAMP = "timestamp without time zone"

# design §3.3 的时间列清单（含可空列）；口径是「表内所有时间列都是 timestamptz」
TIME_COLUMNS = {
    ACCOUNT: ("create_time", "update_time", "locked_until", "last_login_at"),
    SESSION: ("create_time", "update_time", "issued_at", "expires_at", "revoked_at", "last_seen_at"),
}

_COLUMNS_SQL = sa.text(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns WHERE table_schema = :schema AND table_name = :table"
)

_INDEXES_SQL = sa.text(
    """
    SELECT i.relname AS name,
           ix.indisunique AS is_unique,
           ix.indpred IS NOT NULL AS is_partial,
           pg_get_expr(ix.indpred, ix.indrelid) AS predicate,
           (
               SELECT array_agg(a.attname ORDER BY key.ord)
               FROM unnest(ix.indkey) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = key.attnum
           ) AS columns
    FROM pg_index ix
    JOIN pg_class i ON i.oid = ix.indexrelid
    JOIN pg_class t ON t.oid = ix.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = :schema AND t.relname = :table
    """
)

_FOREIGN_KEYS_SQL = sa.text(
    """
    SELECT con.conname AS name,
           fn.nspname AS parent_schema,
           f.relname AS parent_table,
           (
               SELECT array_agg(a.attname ORDER BY key.ord)
               FROM unnest(con.conkey) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = key.attnum
           ) AS columns,
           (
               SELECT array_agg(a.attname ORDER BY key.ord)
               FROM unnest(con.confkey) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = key.attnum
           ) AS parent_columns
    FROM pg_constraint con
    JOIN pg_class t ON t.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_class f ON f.oid = con.confrelid
    JOIN pg_namespace fn ON fn.oid = f.relnamespace
    WHERE n.nspname = :schema AND t.relname = :table AND con.contype = 'f'
    """
)


async def _catalog(statement: sa.TextClause, table: str) -> list[dict[str, object]]:
    async with get_session_factory()() as session:
        result = await session.execute(statement, {"schema": SCHEMA, "table": table})
        return [dict(row) for row in result.mappings()]


async def _columns(table: str) -> dict[str, dict[str, object]]:
    return {str(row["column_name"]): row for row in await _catalog(_COLUMNS_SQL, table)}


async def _indexes(table: str) -> dict[str, dict[str, object]]:
    return {str(row["name"]): row for row in await _catalog(_INDEXES_SQL, table)}


async def _foreign_keys(table: str) -> list[dict[str, object]]:
    return await _catalog(_FOREIGN_KEYS_SQL, table)


async def test_b07_standard_columns_are_present() -> None:
    """[B-07][RULE-data-001] 两表统一 `id/is_deleted/create_time/update_time`，软删默认 false。"""
    for table in (ACCOUNT, SESSION):
        columns = await _columns(table)
        missing = [name for name in STANDARD_COLUMNS if name not in columns]
        assert not missing, f"{SCHEMA}.{table} 缺标准列：{missing}"

        assert columns["id"]["data_type"] == "uuid"
        assert columns["id"]["column_default"] == "gen_random_uuid()"

        assert columns["is_deleted"]["data_type"] == "boolean"
        assert columns["is_deleted"]["is_nullable"] == "NO"
        assert str(columns["is_deleted"]["column_default"]).startswith("false")

        for name in ("create_time", "update_time"):
            assert columns[name]["is_nullable"] == "NO"
            assert str(columns[name]["column_default"]).startswith("now()")


async def test_b07_soft_delete_unique_indexes_are_partial() -> None:
    """[B-07][RULE-data-001] 软删唯一约束必须带 `WHERE is_deleted = false` 谓词。"""
    account_index = (await _indexes(ACCOUNT))["uq_console_account_tenant_username"]
    assert account_index["is_unique"] is True
    assert account_index["is_partial"] is True
    assert list(account_index["columns"]) == ["tenant_id", "username"]
    assert str(account_index["predicate"]).replace(" ", "") == "(is_deleted=false)"

    session_index = (await _indexes(SESSION))["uq_console_session_token_hash"]
    assert session_index["is_unique"] is True
    assert session_index["is_partial"] is True
    assert list(session_index["columns"]) == ["token_hash"]
    assert str(session_index["predicate"]).replace(" ", "") == "(is_deleted=false)"


async def test_b07_time_columns_are_timestamptz() -> None:
    """[B-07][RULE-data-001] 时间列统一 `timestamptz`，不得出现无时区 timestamp。"""
    for table, expected in TIME_COLUMNS.items():
        columns = await _columns(table)
        naive = [name for name, meta in columns.items() if meta["data_type"] == NAIVE_TIMESTAMP]
        assert naive == [], f"{SCHEMA}.{table} 出现无时区时间列：{naive}"

        for name in expected:
            assert columns[name]["data_type"] == TIMESTAMPTZ, f"{SCHEMA}.{table}.{name} 不是 timestamptz"

        actual = {name for name, meta in columns.items() if meta["data_type"] == TIMESTAMPTZ}
        assert actual == set(expected), f"{SCHEMA}.{table} 时间列集合与 design §3.3 不一致"


async def test_b07_session_account_fk_is_same_schema_physical() -> None:
    """[B-07][RULE-data-001] 同 Owner Schema 用物理 FK：`account_id → control.console_account.id`。"""
    foreign_keys = await _foreign_keys(SESSION)
    assert len(foreign_keys) == 1, f"{SCHEMA}.{SESSION} 物理 FK 数量异常：{foreign_keys}"
    foreign_key = foreign_keys[0]
    assert foreign_key["parent_schema"] == SCHEMA
    assert foreign_key["parent_table"] == ACCOUNT
    assert list(foreign_key["columns"]) == ["account_id"]
    assert list(foreign_key["parent_columns"]) == ["id"]

    assert await _foreign_keys(ACCOUNT) == [], "账号表不得反向依赖会话表"


async def test_b07_session_account_expires_index_is_present() -> None:
    """[B-07] 按账号清理/审计用的 `ix_console_session_account_expires` 存在且列序正确。"""
    index = (await _indexes(SESSION))["ix_console_session_account_expires"]
    assert list(index["columns"]) == ["account_id", "expires_at"]
    assert index["is_unique"] is False
    assert index["is_partial"] is False
