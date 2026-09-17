from typing import Any

import sqlalchemy as sa
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import AgentAccessGrant
from sqlalchemy import inspect

SCHEMA = "control"
TABLE = "agent_access_grant"
UNIQUE_INDEX = "uq_agent_access_grant_user_agent"
LOOKUP_INDEX = "ix_agent_access_grant_agent_user"

EXPECTED_PARTIAL_PREDICATE = "is_deleted = false"


async def _reflect() -> dict[str, Any]:
    async with get_session_factory()() as session:
        connection = await session.connection()

        def probe(sync_connection: sa.Connection) -> dict[str, Any]:
            inspector = inspect(sync_connection)
            return {
                "columns": inspector.get_columns(TABLE, schema=SCHEMA),
                "primary_key": inspector.get_pk_constraint(TABLE, schema=SCHEMA),
                "indexes": inspector.get_indexes(TABLE, schema=SCHEMA),
            }

        return await connection.run_sync(probe)


def _normalize_predicate(value: str) -> str:
    text = " ".join(value.split())
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text


def _column_diffs(reflected: dict[str, Any]) -> list[str]:
    orm_columns = {column.name: column for column in AgentAccessGrant.__table__.columns}
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


def _primary_key_diffs(reflected: dict[str, Any]) -> list[str]:
    orm_pk = [column.name for column in AgentAccessGrant.__table__.primary_key.columns]
    db_pk = [str(column) for column in reflected["primary_key"]["constrained_columns"]]
    if orm_pk != db_pk:
        return [f"primary key differs: orm={orm_pk} db={db_pk}"]
    return []


def _index_diffs(reflected: dict[str, Any]) -> list[str]:
    db_indexes = {str(item["name"]): item for item in reflected["indexes"]}
    orm_indexes = {index.name: index for index in AgentAccessGrant.__table__.indexes}
    diffs: list[str] = []
    unique_orm = orm_indexes.get(UNIQUE_INDEX)
    if unique_orm is None:
        diffs.append(f"index {UNIQUE_INDEX} missing from ORM")
    else:
        orm_columns = [column.name for column in unique_orm.columns]
        if orm_columns != ["user_id", "agent_id"]:
            diffs.append(f"index {UNIQUE_INDEX} orm columns differ: {orm_columns}")
        if not bool(unique_orm.unique):
            diffs.append(f"index {UNIQUE_INDEX} not marked unique in ORM")
        orm_predicate = unique_orm.dialect_options["postgresql"].get("where")
        orm_text = _normalize_predicate(str(orm_predicate)) if orm_predicate is not None else ""
        if orm_text != EXPECTED_PARTIAL_PREDICATE:
            diffs.append(f"index {UNIQUE_INDEX} orm predicate differs: {orm_text!r}")
    unique_db = db_indexes.get(UNIQUE_INDEX)
    if unique_db is None:
        diffs.append(f"index {UNIQUE_INDEX} missing from database")
    else:
        if not bool(unique_db["unique"]):
            diffs.append(f"index {UNIQUE_INDEX} is not unique in database")
        db_columns = [str(column) for column in unique_db["column_names"]]
        if db_columns != ["user_id", "agent_id"]:
            diffs.append(f"index {UNIQUE_INDEX} db columns differ: {db_columns}")
        db_options = unique_db.get("dialect_options") or {}
        db_predicate = db_options.get("postgresql_where")
        db_text = _normalize_predicate(str(db_predicate)) if db_predicate is not None else ""
        if db_text != EXPECTED_PARTIAL_PREDICATE:
            diffs.append(f"index {UNIQUE_INDEX} db predicate differs: {db_text!r}")
    lookup_orm = orm_indexes.get(LOOKUP_INDEX)
    if lookup_orm is None:
        diffs.append(f"index {LOOKUP_INDEX} missing from ORM")
    else:
        lookup_orm_columns = [column.name for column in lookup_orm.columns]
        if lookup_orm_columns != ["agent_id", "user_id"]:
            diffs.append(f"index {LOOKUP_INDEX} orm columns differ: {lookup_orm_columns}")
    lookup_db = db_indexes.get(LOOKUP_INDEX)
    if lookup_db is None:
        diffs.append(f"index {LOOKUP_INDEX} missing from database")
    else:
        if bool(lookup_db["unique"]):
            diffs.append(f"index {LOOKUP_INDEX} unexpectedly unique")
        lookup_db_columns = [str(column) for column in lookup_db["column_names"]]
        if lookup_db_columns != ["agent_id", "user_id"]:
            diffs.append(f"index {LOOKUP_INDEX} db columns differ: {lookup_db_columns}")
    return diffs


async def test_agent_access_grant_schema_parity(database_guard: None) -> None:
    reflected = await _reflect()
    diffs: list[str] = []
    diffs.extend(_column_diffs(reflected))
    diffs.extend(_primary_key_diffs(reflected))
    diffs.extend(_index_diffs(reflected))
    assert not diffs, "agent_access_grant schema mismatch:\n" + "\n".join(diffs)
