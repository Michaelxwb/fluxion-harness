from pathlib import Path

from sqlalchemy import Boolean, CheckConstraint, DateTime

import adapters.postgres.models  # noqa: F401  (触发 ORM 表注册)
from adapters.postgres.base import Base

MANDATORY_COLUMNS = {"is_deleted", "create_time", "update_time"}

# The one deliberate exception to the framework persistence contract: module 06
# §3.3 (ADR-056) defines `worker_slot_counter` as a coordination table with
# `resource_class` as its primary key, exactly 3 permanent rows, no soft delete
# and no tenant scoping. Asserted negatively below so it cannot silently become
# a business table (or lose its shape).
COORDINATION_TABLES = {"worker_slot_counter"}


def test_every_framework_owned_table_has_soft_delete_and_timestamps() -> None:
    missing: dict[str, set[str]] = {}
    for table_name, table in Base.metadata.tables.items():
        if table_name in COORDINATION_TABLES:
            continue
        absent = MANDATORY_COLUMNS - set(table.c.keys())
        if absent:
            missing[table_name] = absent
    assert not missing, f"framework-owned tables missing mandatory columns: {missing}"


def test_mandatory_columns_have_consistent_schema_contract() -> None:
    violations: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name in COORDINATION_TABLES:
            continue
        is_deleted = table.c.is_deleted
        create_time = table.c.create_time
        update_time = table.c.update_time

        if (
            not isinstance(is_deleted.type, Boolean)
            or is_deleted.nullable
            or is_deleted.server_default is None
        ):
            violations.append(f"{table_name}.is_deleted")

        for column in (create_time, update_time):
            if not isinstance(column.type, DateTime) or column.nullable or column.server_default is None:
                violations.append(f"{table_name}.{column.name}")
            if getattr(column.type, "timezone", False) is not True:
                violations.append(f"{table_name}.{column.name}:timezone")

        if update_time.onupdate is None:
            violations.append(f"{table_name}.update_time:onupdate")

    assert not violations, f"mandatory column contract violations: {violations}"


def test_no_plain_is_deleted_index_is_generated() -> None:
    # is_deleted is low-cardinality. Prefer composite/partial indexes for actual
    # access paths instead of adding a standalone index to every table.
    offenders: list[str] = []
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            column_names = [column.name for column in index.columns]
            if column_names == ["is_deleted"]:
                offenders.append(f"{table.name}.{index.name}")
    assert not offenders, f"avoid low-selectivity standalone is_deleted indexes: {offenders}"


def test_initial_migration_create_tables_include_mandatory_columns() -> None:
    import importlib.util

    migration_path = Path(__file__).parents[2] / "migrations" / "versions" / "0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("initial_schema", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    create_statements = [
        statement for statement in module.UPGRADE_STATEMENTS if statement.startswith("CREATE TABLE ")
    ]
    # V1.14 is a single-baseline chain: the live table set is exactly 0001.
    retired: set[str] = set()
    created_0001 = {statement.split()[2] for statement in create_statements} - retired
    assert created_0001.issubset(set(Base.metadata.tables)), (
        f"0001 tables missing from live metadata: {created_0001 - set(Base.metadata.tables)}"
    )
    for statement in create_statements:
        table_name = statement.split()[2]
        if table_name in COORDINATION_TABLES:
            continue
        assert "\tis_deleted BOOLEAN DEFAULT false NOT NULL" in statement
        assert "\tcreate_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in statement
        assert "\tupdate_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in statement


def test_every_framework_owned_table_is_tenant_scoped() -> None:
    missing = [
        name
        for name, table in Base.metadata.tables.items()
        if "tenant_id" not in table.c and name not in COORDINATION_TABLES
    ]
    assert not missing, f"tables missing tenant_id (08-表所有权 V1.12): {missing}"


def test_retired_tables_are_absent_from_live_metadata() -> None:
    retired = {"external_auth_profile", "user_service_auth", "integration_registration"}
    assert retired.isdisjoint(set(Base.metadata.tables)), (
        f"retired tables must not be modeled: {retired & set(Base.metadata.tables)}"
    )


def test_foundation_tables_exist() -> None:
    required = {
        "auth_account",
        "auth_session",
        "project_platform",
        "user_project_credential",
        "agent_access_grant",
        "bind_code",
        "skill_definition",
        "async_task_run",
        "channel_delivery",
    }
    assert required.issubset(set(Base.metadata.tables)), (
        f"missing foundation tables: {required - set(Base.metadata.tables)}"
    )


def test_coordination_tables_keep_their_documented_shape() -> None:
    """ADR-056: `worker_slot_counter` is a 3-row coordination table, not a fact table.

    Guards both directions: it must not gain the common business columns
    (`id`/`is_deleted`/`tenant_id`), and it must keep `resource_class` as the
    primary key with the used<=slot_limit invariant — the atomic claim
    (`UPDATE ... WHERE resource_class=:rc AND used<slot_limit`) depends on it.
    """
    table = Base.metadata.tables["worker_slot_counter"]
    assert [column.name for column in table.primary_key] == ["resource_class"]
    forbidden = {"id", "is_deleted", "tenant_id"} & set(table.c.keys())
    assert not forbidden, f"coordination table must not carry business columns: {sorted(forbidden)}"
    checks = {constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
    assert "ck_worker_slot_counter_ck_worker_slot_counter_used_range" in checks
