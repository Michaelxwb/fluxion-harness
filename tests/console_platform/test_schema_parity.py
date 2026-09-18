from typing import Any

import sqlalchemy as sa
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ConsoleAccount, ConsoleSession
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    ProjectPlatform,
    SharedCredentialRef,
    UserCredentialRef,
)
from sqlalchemy import inspect

SCHEMA = "control"

EXPECTED_INDEXES: dict[str, tuple[str, ...]] = {
    "agent_definition": (
        "uq_agent_definition_tenant_key",
        "ix_agent_definition_model_enabled",
    ),
    "model_definition": ("uq_model_definition_tenant_key",),
    "project_platform": (
        "uq_project_platform_tenant_key",
        "ix_project_platform_adapter_key_enabled",
    ),
    "user_credential_ref": ("uq_user_credential_ref_user_platform",),
    "shared_credential_ref": (
        "uq_shared_credential_ref_platform_id",
        "ix_shared_credential_ref_platform_status",
    ),
    "console_account": ("uq_console_account_tenant_username",),
    "console_session": (
        "uq_console_session_token_hash",
        "ix_console_session_account_expires",
    ),
}


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


def _index_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    orm_indexes = {index.name: index for index in orm_table.indexes}
    db_indexes = {str(item["name"]): item for item in reflected["indexes"]}
    diffs: list[str] = []
    for name in EXPECTED_INDEXES[orm_table.name]:
        orm_index = orm_indexes.get(name)
        db_index = db_indexes.get(name)
        if orm_index is None:
            diffs.append(f"index {name} missing from ORM")
            continue
        if db_index is None:
            diffs.append(f"index {name} missing from database")
            continue
        if bool(orm_index.unique) != bool(db_index["unique"]):
            diffs.append(f"index {name} unique differs: orm={orm_index.unique} db={db_index['unique']}")
        orm_columns = [column.name for column in orm_index.columns]
        db_columns = [str(column) for column in db_index["column_names"]]
        if orm_columns != db_columns:
            diffs.append(f"index {name} columns differ: orm={orm_columns} db={db_columns}")
        orm_partial = orm_index.dialect_options["postgresql"].get("where") is not None
        db_options = db_index.get("dialect_options") or {}
        db_partial = db_options.get("postgresql_where") is not None
        if orm_partial != db_partial:
            diffs.append(f"index {name} partial predicate differs: orm={orm_partial} db={db_partial}")
    return diffs


def _compare(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    diffs.extend(_column_diffs(orm_table, reflected))
    diffs.extend(_primary_key_diffs(orm_table, reflected))
    diffs.extend(_foreign_key_diffs(orm_table, reflected))
    diffs.extend(_index_diffs(orm_table, reflected))
    return diffs


async def test_agent_definition_schema_parity(database_guard: None) -> None:
    diffs = _compare(AgentDefinition.__table__, await _reflect("agent_definition"))
    assert not diffs, "agent_definition schema mismatch:\n" + "\n".join(diffs)


async def test_model_definition_schema_parity(database_guard: None) -> None:
    diffs = _compare(ModelDefinition.__table__, await _reflect("model_definition"))
    assert not diffs, "model_definition schema mismatch:\n" + "\n".join(diffs)


async def test_console_account_schema_parity(database_guard: None) -> None:
    diffs = _compare(ConsoleAccount.__table__, await _reflect("console_account"))
    assert not diffs, "console_account schema mismatch:\n" + "\n".join(diffs)


async def test_console_session_schema_parity(database_guard: None) -> None:
    diffs = _compare(ConsoleSession.__table__, await _reflect("console_session"))
    assert not diffs, "console_session schema mismatch:\n" + "\n".join(diffs)


async def test_project_platform_schema_parity(database_guard: None) -> None:
    diffs = _compare(ProjectPlatform.__table__, await _reflect("project_platform"))
    assert not diffs, "project_platform schema mismatch:\n" + "\n".join(diffs)


async def test_user_credential_ref_schema_parity(database_guard: None) -> None:
    diffs = _compare(UserCredentialRef.__table__, await _reflect("user_credential_ref"))
    assert not diffs, "user_credential_ref schema mismatch:\n" + "\n".join(diffs)


async def test_shared_credential_ref_schema_parity(database_guard: None) -> None:
    diffs = _compare(SharedCredentialRef.__table__, await _reflect("shared_credential_ref"))
    assert not diffs, "shared_credential_ref schema mismatch:\n" + "\n".join(diffs)
