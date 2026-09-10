from pathlib import Path

from sqlalchemy import Boolean, DateTime

from adapters.postgres.base import Base

MANDATORY_COLUMNS = {"is_deleted", "create_time", "update_time"}


def test_every_framework_owned_table_has_soft_delete_and_timestamps() -> None:
    missing: dict[str, set[str]] = {}
    for table_name, table in Base.metadata.tables.items():
        absent = MANDATORY_COLUMNS - set(table.c.keys())
        if absent:
            missing[table_name] = absent
    assert not missing, f"framework-owned tables missing mandatory columns: {missing}"


def test_mandatory_columns_have_consistent_schema_contract() -> None:
    violations: list[str] = []
    for table_name, table in Base.metadata.tables.items():
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
    assert len(create_statements) == len(Base.metadata.tables)
    for statement in create_statements:
        assert "\tis_deleted BOOLEAN DEFAULT false NOT NULL" in statement
        assert "\tcreate_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in statement
        assert "\tupdate_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in statement
