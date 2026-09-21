from typing import Any

import sqlalchemy as sa
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import (
    DeliveryRoute,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
)
from sqlalchemy import inspect

SCHEMA = "task"

EXPECTED_INDEXES: dict[str, tuple[str, ...]] = {
    "delivery_route": (
        "uq_delivery_route_route_hash",
        "ix_delivery_route_platform_user_channel_update_time",
        "ix_delivery_route_bot_id_external_user_id",
    ),
    "task_schedule": (
        "ix_task_schedule_status_next_fire_at",
        "ix_task_schedule_actor_user_status",
        "ix_task_schedule_agent_status",
    ),
    "task_execution": (
        "uq_task_execution_tenant_idempotency_key",
        "uq_task_execution_parent_item_key",
        "ix_task_execution_status_not_before_priority_create_time",
        "ix_task_execution_running_lease_until",
        "ix_task_execution_root_parent_status",
        "ix_task_execution_actor_user_create_time",
        "ix_task_execution_schedule_create_time",
        "ix_task_execution_final_delivery",
    ),
    "task_event": (
        "uq_task_event_task_seq",
        "ix_task_event_task_create_time",
        "ix_task_event_event_type_create_time",
    ),
}

EXPECTED_CHECKS: dict[str, tuple[str, ...]] = {
    "task_schedule": ("ck_task_schedule_trigger",),
}

EXPECTED_FOREIGN_KEYS: dict[str, set[tuple[str, str]]] = {
    "delivery_route": set(),
    "task_schedule": {("delivery_route_id", "task.delivery_route.id")},
    "task_execution": {
        ("parent_id", "task.task_execution.id"),
        ("schedule_id", "task.task_schedule.id"),
        ("delivery_route_id", "task.delivery_route.id"),
    },
    "task_event": {("task_id", "task.task_execution.id")},
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
                "unique_constraints": inspector.get_unique_constraints(table_name, schema=SCHEMA),
                "checks": inspector.get_check_constraints(table_name, schema=SCHEMA),
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


def _orm_indexes(orm_table: sa.Table) -> dict[str, sa.Index | sa.UniqueConstraint]:
    indexes: dict[str, sa.Index | sa.UniqueConstraint] = {
        index.name: index for index in orm_table.indexes if index.name is not None
    }
    for constraint in orm_table.constraints:
        if isinstance(constraint, sa.UniqueConstraint) and constraint.name is not None:
            indexes.setdefault(constraint.name, constraint)
    return indexes


def _orm_index_columns(orm_index: sa.Index | sa.UniqueConstraint) -> list[str]:
    if isinstance(orm_index, sa.UniqueConstraint):
        return [column.name for column in orm_index.columns]
    names: list[str] = []
    for expression in orm_index.expressions:
        if isinstance(expression, sa.Column):
            names.append(expression.name)
        else:
            names.append(str(expression).split()[0])
    return names


def _index_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    orm_indexes = _orm_indexes(orm_table)
    db_indexes = {str(item["name"]): item for item in reflected["indexes"]}
    diffs: list[str] = []
    expected = set(EXPECTED_INDEXES[orm_table.name])
    unexpected = sorted(set(db_indexes) - expected)
    if unexpected:
        diffs.append(f"unexpected database indexes: {unexpected}")
    for name in EXPECTED_INDEXES[orm_table.name]:
        orm_index = orm_indexes.get(name)
        db_index = db_indexes.get(name)
        if orm_index is None:
            diffs.append(f"index {name} missing from ORM")
            continue
        if db_index is None:
            diffs.append(f"index {name} missing from database")
            continue
        orm_unique = True if isinstance(orm_index, sa.UniqueConstraint) else bool(orm_index.unique)
        if orm_unique != bool(db_index["unique"]):
            diffs.append(f"index {name} unique differs: orm={orm_unique} db={db_index['unique']}")
        orm_columns = _orm_index_columns(orm_index)
        db_columns = [str(column) for column in db_index["column_names"]]
        if orm_columns != db_columns:
            diffs.append(f"index {name} columns differ: orm={orm_columns} db={db_columns}")
        orm_partial = (
            isinstance(orm_index, sa.Index)
            and orm_index.dialect_options["postgresql"].get("where") is not None
        )
        db_options = db_index.get("dialect_options") or {}
        db_partial = db_options.get("postgresql_where") is not None
        if orm_partial != db_partial:
            diffs.append(f"index {name} partial predicate differs: orm={orm_partial} db={db_partial}")
    return diffs


def _check_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    expected = set(EXPECTED_CHECKS.get(orm_table.name, ()))
    orm_checks = {
        str(constraint.name)
        for constraint in orm_table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    db_checks = {str(item["name"]) for item in reflected["checks"]}
    diffs: list[str] = []
    missing_orm = sorted(expected - orm_checks)
    missing_db = sorted(expected - db_checks)
    unexpected_db = sorted(db_checks - expected)
    if missing_orm:
        diffs.append(f"check constraints missing from ORM: {missing_orm}")
    if missing_db:
        diffs.append(f"check constraints missing from database: {missing_db}")
    if unexpected_db:
        diffs.append(f"unexpected database check constraints: {unexpected_db}")
    return diffs


def _compare(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    diffs.extend(_column_diffs(orm_table, reflected))
    diffs.extend(_primary_key_diffs(orm_table, reflected))
    diffs.extend(_foreign_key_diffs(orm_table, reflected))
    diffs.extend(_index_diffs(orm_table, reflected))
    diffs.extend(_check_diffs(orm_table, reflected))
    return diffs


async def test_delivery_route_schema_parity(database_guard: None) -> None:
    diffs = _compare(DeliveryRoute.__table__, await _reflect("delivery_route"))
    assert not diffs, "delivery_route schema mismatch:\n" + "\n".join(diffs)


async def test_task_schedule_schema_parity(database_guard: None) -> None:
    diffs = _compare(TaskSchedule.__table__, await _reflect("task_schedule"))
    assert not diffs, "task_schedule schema mismatch:\n" + "\n".join(diffs)


async def test_task_execution_schema_parity(database_guard: None) -> None:
    diffs = _compare(TaskExecution.__table__, await _reflect("task_execution"))
    assert not diffs, "task_execution schema mismatch:\n" + "\n".join(diffs)


async def test_task_event_schema_parity(database_guard: None) -> None:
    diffs = _compare(TaskEvent.__table__, await _reflect("task_event"))
    assert not diffs, "task_event schema mismatch:\n" + "\n".join(diffs)


def _orm_foreign_keys(orm_table: sa.Table) -> set[tuple[str, str]]:
    return {(fk.parent.name, fk.target_fullname) for fk in orm_table.foreign_keys}


def _db_foreign_keys(reflected: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (
            str(item["constrained_columns"][0]),
            f"{item['referred_schema']}.{item['referred_table']}.{item['referred_columns'][0]}",
        )
        for item in reflected["foreign_keys"]
    }


async def test_same_schema_foreign_keys(database_guard: None) -> None:
    """同一 Owner Schema 的表必须用物理 FK 关联（RULE-data-001 / B-101）。"""
    for model in (DeliveryRoute, TaskSchedule, TaskExecution, TaskEvent):
        table_name = model.__table__.name
        expected = EXPECTED_FOREIGN_KEYS[table_name]
        orm_keys = _orm_foreign_keys(model.__table__)
        db_keys = _db_foreign_keys(await _reflect(table_name))
        assert orm_keys == expected, f"{table_name} ORM foreign keys: {orm_keys} != {expected}"
        assert db_keys == expected, f"{table_name} DB foreign keys: {db_keys} != {expected}"


async def test_task_execution_server_defaults(database_guard: None) -> None:
    """deadline_at 非空且默认 +24h；task_type 非空且默认 SKILL（B-101）。"""
    reflected = await _reflect("task_execution")
    columns = {str(item["name"]): item for item in reflected["columns"]}

    deadline = columns["deadline_at"]
    assert deadline["nullable"] is False, "deadline_at must be NOT NULL"
    default = str(deadline["default"])
    assert "24:00:00" in default or "24 hours" in default, f"deadline_at default: {default}"

    task_type = columns["task_type"]
    assert task_type["nullable"] is False, "task_type must be NOT NULL"
    assert "SKILL" in str(task_type["default"]), f"task_type default: {task_type['default']}"

