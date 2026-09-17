from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BindCode, BotAccount, ChannelIdentity
from sqlalchemy import inspect

SCHEMA = "control"


@dataclass(frozen=True)
class IndexSpec:
    name: str
    columns: tuple[str, ...]
    unique: bool
    partial: bool


BOT_ACCOUNT_INDEXES = (
    IndexSpec("uq_bot_account_bot_id", ("bot_id",), unique=True, partial=True),
    IndexSpec(
        "ix_bot_account_agent_channel_enabled",
        ("agent_id", "channel", "enabled"),
        unique=False,
        partial=False,
    ),
)

CHANNEL_IDENTITY_INDEXES = (
    IndexSpec(
        "uq_channel_identity_tenant_identity_key",
        ("tenant_id", "identity_key"),
        unique=True,
        partial=True,
    ),
    IndexSpec(
        "ix_channel_identity_platform_user_channel",
        ("platform_user_id", "channel"),
        unique=False,
        partial=False,
    ),
)

BIND_CODE_INDEXES = (
    IndexSpec("uq_bind_code_code_hash", ("code_hash",), unique=True, partial=True),
    IndexSpec(
        "ix_bind_code_platform_user_status_expires",
        ("platform_user_id", "status", "expires_at"),
        unique=False,
        partial=False,
    ),
)


async def _reflect(table_name: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        connection = await session.connection()

        def probe(sync_connection: sa.Connection) -> dict[str, Any]:
            inspector = inspect(sync_connection)
            return {
                "columns": inspector.get_columns(table_name, schema=SCHEMA),
                "primary_key": inspector.get_pk_constraint(table_name, schema=SCHEMA),
                "foreign_keys": inspector.get_foreign_keys(table_name, schema=SCHEMA),
                "indexes": inspector.get_indexes(table_name, schema=SCHEMA),
            }

        return await connection.run_sync(probe)


def _column_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    orm_columns = {column.name: column for column in orm_table.columns}
    db_columns = {str(item["name"]): item for item in reflected["columns"]}
    diffs: list[str] = []
    only_orm = sorted(set(orm_columns) - set(db_columns))
    only_db = sorted(set(db_columns) - set(orm_columns))
    if only_orm:
        diffs.append(f"columns only in ORM: {only_orm}")
    if only_db:
        diffs.append(f"columns only in database: {only_db}")
    for name in sorted(set(orm_columns) & set(db_columns)):
        orm_nullable = bool(orm_columns[name].nullable)
        db_nullable = bool(db_columns[name]["nullable"])
        if orm_nullable != db_nullable:
            diffs.append(f"column {name} nullable differs: orm={orm_nullable} db={db_nullable}")
    return diffs


def _primary_key_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    orm_pk = [column.name for column in orm_table.primary_key.columns]
    db_pk = [str(column) for column in reflected["primary_key"]["constrained_columns"]]
    if orm_pk != db_pk:
        return [f"primary key differs: orm={orm_pk} db={db_pk}"]
    return []


def _foreign_key_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    orm_keys = sorted((fk.parent.name, fk.target_fullname) for fk in orm_table.foreign_keys)
    db_keys = sorted(
        (
            str(item["constrained_columns"][0]),
            f"{item['referred_schema']}.{item['referred_table']}.{item['referred_columns'][0]}",
        )
        for item in reflected["foreign_keys"]
    )
    if orm_keys != db_keys:
        return [f"foreign keys differ: orm={orm_keys} db={db_keys}"]
    return []


def _index_diffs(
    orm_table: sa.Table,
    reflected: dict[str, Any],
    specs: tuple[IndexSpec, ...],
) -> list[str]:
    orm_indexes = {index.name: index for index in orm_table.indexes}
    db_indexes = {str(item["name"]): item for item in reflected["indexes"]}
    diffs: list[str] = []
    for spec in specs:
        orm_index = orm_indexes.get(spec.name)
        db_index = db_indexes.get(spec.name)
        if orm_index is None:
            diffs.append(f"index {spec.name} missing from ORM")
        else:
            orm_columns = [column.name for column in orm_index.columns]
            if orm_columns != list(spec.columns):
                diffs.append(f"index {spec.name} columns differ: orm={orm_columns}")
            if bool(orm_index.unique) != spec.unique:
                diffs.append(f"index {spec.name} unique differs: orm={orm_index.unique}")
            orm_partial = orm_index.dialect_options["postgresql"].get("where") is not None
            if orm_partial != spec.partial:
                diffs.append(f"index {spec.name} partial differs: orm={orm_partial}")
        if db_index is None:
            diffs.append(f"index {spec.name} missing from database")
        else:
            db_columns = [str(column) for column in db_index["column_names"]]
            if db_columns != list(spec.columns):
                diffs.append(f"index {spec.name} columns differ: db={db_columns}")
            if bool(db_index["unique"]) != spec.unique:
                diffs.append(f"index {spec.name} unique differs: db={db_index['unique']}")
            db_options = db_index.get("dialect_options") or {}
            db_partial = db_options.get("postgresql_where") is not None
            if db_partial != spec.partial:
                diffs.append(f"index {spec.name} partial differs: db={db_partial}")
    return diffs


async def _assert_parity(
    model: type[BotAccount] | type[ChannelIdentity] | type[BindCode],
    specs: tuple[IndexSpec, ...],
) -> None:
    orm_table = model.__table__
    reflected = await _reflect(orm_table.name)
    diffs: list[str] = []
    diffs.extend(_column_diffs(orm_table, reflected))
    diffs.extend(_primary_key_diffs(orm_table, reflected))
    diffs.extend(_foreign_key_diffs(orm_table, reflected))
    diffs.extend(_index_diffs(orm_table, reflected, specs))
    assert not diffs, f"{orm_table.name} schema mismatch:\n" + "\n".join(diffs)


async def test_bot_account_schema_parity(database_guard: None) -> None:
    await _assert_parity(BotAccount, BOT_ACCOUNT_INDEXES)


async def test_channel_identity_schema_parity(database_guard: None) -> None:
    await _assert_parity(ChannelIdentity, CHANNEL_IDENTITY_INDEXES)


async def test_bind_code_schema_parity(database_guard: None) -> None:
    await _assert_parity(BindCode, BIND_CODE_INDEXES)
