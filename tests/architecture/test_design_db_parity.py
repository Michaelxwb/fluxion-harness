"""Design <-> migration <-> ORM parity gate (ADR-067 / A7).

The design documents are the source of truth for the physical schema, but until
now nothing checked that the baseline migration and the ORM actually matched
them: the 0001 baseline had been generated from an older ORM snapshot, which is
exactly how "documented column missing / renamed column" defects survived review
(see docs/05-变更记录/12-第五轮Review裁决修复.md).

This gate fails when:

1. the migration and the ORM disagree about a table's columns or its index
   inventory (the two artefacts must always be generated from each other);
2. a table documented with a full field table in a module design loses a
   documented column, or gains an undocumented one, unless the drift is listed
   in ``KNOWN_DESIGN_DRIFT`` with a reason.

``KNOWN_DESIGN_DRIFT`` is a burn-down list, not a permanent allowlist: every
entry names the module that must align it. Adding an entry requires a reason.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

import adapters.postgres.models  # noqa: F401  (triggers ORM table registration)
from adapters.postgres.base import Base

REPO_ROOT = Path(__file__).parents[2]
MIGRATION_PATH = REPO_ROOT / "migrations" / "versions" / "0001_initial_schema.py"
DESIGN_ROOT = REPO_ROOT / "docs" / "02-模块设计"

# Columns every framework-owned table carries regardless of its field table.
COMMON_COLUMNS = {"id", "is_deleted", "create_time", "update_time", "tenant_id"}

# The single documented exception (module 06 §3.3 / ADR-056): a coordination
# table with resource_class as its primary key, permanent rows, no soft delete and
# no tenant scoping. Its field table therefore does not imply the common columns.
NO_COMMON_COLUMNS = {"worker_slot_counter"}

# Burn-down list for design drift. It is empty because the P3 alignment closed
# every table: the design column set, the 0001 baseline and the ORM now agree for
# all 40 documented tables. Re-adding an entry requires an owning module plus a
# reason, and the gate fails on an entry that is already fixed — parking new
# drift here instead of fixing it is what this file exists to prevent.
KNOWN_DESIGN_DRIFT: dict[str, str] = {}

# Tables that physically exist but whose field-level spec is owned by a document
# that is not a module design field table: the Knowledge tables are explicitly
# deferred in V1 (总设: "Knowledge 当前为规划项，不在 Phase 1 固定 knowledge_source
# 表"), so their placeholder rows are the only artefact until that module is designed.
UNDOCUMENTED_BY_DESIGN = {"agent_knowledge_binding", "knowledge_source"}

def _migration_columns() -> dict[str, set[str]]:
    spec = importlib.util.spec_from_file_location("baseline_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    columns: dict[str, set[str]] = {}
    for statement in module.UPGRADE_STATEMENTS:
        if not statement.startswith("CREATE TABLE "):
            continue
        table = statement.split()[2]
        body = statement.split("(", 1)[1].rsplit("\n)", 1)[0]
        names: set[str] = set()
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line:
                continue
            first_token = line.split()[0].upper()
            # Table constraints start with a keyword token; comparing the whole
            # first token avoids eating columns such as "checksum" (CHECK...).
            if first_token in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "EXCLUDE"}:
                continue
            names.add(line.split()[0])
        columns[table] = names
    return columns


def _migration_indexes() -> set[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location("baseline_migration_idx", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found: set[tuple[str, str]] = set()
    for statement in module.UPGRADE_STATEMENTS:
        match = re.match(r"CREATE (?:UNIQUE )?INDEX (\S+) ON (\S+)", statement)
        if match:
            found.add((match.group(2), match.group(1)))
    return found


def _design_columns() -> dict[str, set[str]]:
    """Parse the module design field tables (`#### 表 \\`name\\`` blocks)."""
    design: dict[str, set[str]] = {}
    for path in sorted(DESIGN_ROOT.glob("*/design-full.md")):
        text = path.read_text()
        parts = re.split(r"^#### 表 `([a-z_]+)`", text, flags=re.M)
        for index in range(1, len(parts), 2):
            table, body = parts[index], parts[index + 1]
            rows: set[str] = set()
            started = False
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("|"):
                    if started and line.startswith("**"):
                        break
                    continue
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if len(cells) < 4:
                    continue
                if cells[0] == "字段名":
                    started = True
                    continue
                if set(cells[0]) <= set("-: "):
                    continue
                if started:
                    if not re.match(r"^[a-z][a-z0-9_]*$", cells[0]):
                        break
                    rows.add(cells[0])
            if rows:
                common = set() if table in NO_COMMON_COLUMNS else COMMON_COLUMNS
                design[table] = rows | common
    return design


def test_migration_and_orm_declare_the_same_tables() -> None:
    migration = set(_migration_columns())
    orm = set(Base.metadata.tables)
    assert migration == orm, (
        f"baseline migration and ORM table sets differ: "
        f"migration-only={sorted(migration - orm)} orm-only={sorted(orm - migration)}"
    )


def test_migration_columns_match_orm_columns() -> None:
    migration = _migration_columns()
    mismatches: dict[str, dict[str, list[str]]] = {}
    for name, table in Base.metadata.tables.items():
        orm_columns = set(table.columns.keys())
        if migration[name] != orm_columns:
            mismatches[name] = {
                "migration_only": sorted(migration[name] - orm_columns),
                "orm_only": sorted(orm_columns - migration[name]),
            }
    assert not mismatches, (
        "the 0001 baseline must be regenerated from the ORM whenever models change: "
        f"{mismatches}"
    )


def test_migration_indexes_match_orm_indexes() -> None:
    migration = _migration_indexes()
    orm = {
        (table.name, index.name)
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    assert migration == orm, (
        f"index inventory differs: migration-only={sorted(migration - orm)} "
        f"orm-only={sorted(orm - migration)}"
    )


def test_documented_tables_exist_in_orm() -> None:
    design = _design_columns()
    orm = set(Base.metadata.tables)
    missing = sorted(set(design) - orm)
    assert not missing, f"tables documented in module designs but absent from the ORM: {missing}"


def test_no_new_design_drift() -> None:
    """Documented columns must exist physically unless the drift is on the burn-down list."""
    design = _design_columns()
    fresh: dict[str, dict[str, list[str]]] = {}
    for table, documented in sorted(design.items()):
        if table in KNOWN_DESIGN_DRIFT:
            continue
        physical = set(Base.metadata.tables[table].columns.keys())
        missing = sorted(documented - physical)
        undocumented = sorted(physical - documented)
        if missing or undocumented:
            fresh[table] = {"missing": missing, "undocumented": undocumented}
    assert not fresh, (
        "documented schema and physical schema diverged for tables not on the "
        f"KNOWN_DESIGN_DRIFT burn-down list: {fresh}"
    )


def test_known_design_drift_still_exists() -> None:
    """Burn-down hygiene: a fixed table must be removed from the list."""
    design = _design_columns()
    resolved: list[str] = []
    for table in KNOWN_DESIGN_DRIFT:
        documented = design.get(table)
        if documented is None:
            pytest.fail(f"KNOWN_DESIGN_DRIFT names {table!r}, which has no documented field table")
        physical = set(Base.metadata.tables[table].columns.keys())
        if documented == physical:
            resolved.append(table)
    assert not resolved, (
        "these tables are now aligned and must be removed from KNOWN_DESIGN_DRIFT: "
        f"{sorted(resolved)}"
    )


def test_drift_entries_carry_a_reason() -> None:
    empty = sorted(table for table, reason in KNOWN_DESIGN_DRIFT.items() if not reason.strip())
    assert not empty, f"KNOWN_DESIGN_DRIFT entries need a reason: {empty}"


# Columns introduced or renamed by the fifth review round (D8~D16). These must
# exist physically with the documented name in every subsequent change; the rest
# of each table's historical column set is now fully aligned (P3 closed), so the
REVIEW_ROUND_COLUMNS: dict[str, set[str]] = {
    "execution_proposal": {"superseded_at", "status", "confirmation_digest", "template_hash"},
    "capability_implementation": {
        "auth_mode",
        "async_submittable",
        "project_platform_id",
        "shared_secret_ref",
    },
    "channel_delivery": {"message_key", "event_id", "dedupe_key", "attempt", "status"},
    "execution_step": {"execution_mode", "slot_resource_class", "step_key", "status"},
    "service_execution": {
        "capability_id",
        "execution_source",
        "execution_mode",
        "test_mode",
        "draft_revision",
        "cancel_requested_at",
        "context_summary",
        "snapshot_id",
    },
    "artifact": {
        "name",
        "checksum",
        "content_type",
        "size_bytes",
        "created_by",
        "execution_id",
    },
}


def test_review_round_columns_are_aligned() -> None:
    """Regression guard for the columns fixed by the fifth review round (D8~D16)."""
    physical_tables = {name: set(t.columns.keys()) for name, t in Base.metadata.tables.items()}
    problems: dict[str, list[str]] = {}
    for table, required in REVIEW_ROUND_COLUMNS.items():
        missing = sorted(required - physical_tables[table])
        if missing:
            problems[table] = missing
    assert not problems, f"fifth-round columns regressed: {problems}"


def test_burn_down_list_is_empty() -> None:
    """P3 is closed: no table may sit on the drift list without an owner and reason.

    Re-adding an entry is allowed only with an owning module and a reason (see
    ``test_drift_entries_carry_a_reason``); silently parking new drift here is
    what this gate exists to prevent.
    """
    assert KNOWN_DESIGN_DRIFT == {}, (
        "design drift must be fixed, not parked: "
        f"{sorted(KNOWN_DESIGN_DRIFT)}"
    )


def test_orm_tables_are_all_documented() -> None:
    """Every physical table must be traceable to a design field table or an explicit deferral."""
    design = _design_columns()
    undocumented = sorted(set(Base.metadata.tables) - set(design) - UNDOCUMENTED_BY_DESIGN)
    assert not undocumented, (
        "these tables exist physically with no design field table: "
        f"{undocumented}; either document them in the owning module or add them to "
        "UNDOCUMENTED_BY_DESIGN with a reason"
    )


def test_deferred_tables_stay_deferred() -> None:
    """The deferral list is a V1 scope decision, so it must match the deferred set exactly."""
    design = _design_columns()
    designed_later = sorted(UNDOCUMENTED_BY_DESIGN & set(design))
    assert not designed_later, (
        f"tables are now documented and must leave UNDOCUMENTED_BY_DESIGN: {designed_later}"
    )
