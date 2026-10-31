import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    EvalCase,
    EvalReport,
    RunInterrupt,
    RunRecord,
    RuntimeSnapshot,
    UserMemory,
)
from sqlalchemy import inspect

SCHEMA = "runtime"

TABLE_MODELS: dict[str, sa.Table] = {
    "conversation": Conversation.__table__,
    "run_record": RunRecord.__table__,
    "runtime_snapshot": RuntimeSnapshot.__table__,
    "canonical_event": CanonicalEvent.__table__,
    "run_interrupt": RunInterrupt.__table__,
    "user_memory": UserMemory.__table__,
    "eval_case": EvalCase.__table__,
    "eval_report": EvalReport.__table__,
}

EXPECTED_INDEXES: dict[str, tuple[str, ...]] = {
    "conversation": ("ix_conversation_tenant_user_agent_update_time",),
    "run_record": (
        "uq_run_record_active_conversation",
        "ix_run_record_conversation_create_time",
        "ix_run_record_agent_status_create_time",
        "ix_run_record_running_lease_until",
        "ix_run_record_trace_id",
        "ix_run_record_task_family",
    ),
    "runtime_snapshot": ("uq_runtime_snapshot_run_id", "ix_runtime_snapshot_content_hash"),
    "canonical_event": (
        "uq_canonical_event_conversation_seq",
        "ix_canonical_event_run_seq",
        "ix_canonical_event_conversation_create_time",
    ),
    "run_interrupt": ("ix_run_interrupt_run_status", "ix_run_interrupt_conversation_status"),
    "user_memory": (
        "uq_user_memory_key",
        "ix_user_memory_candidates",
        "ix_user_memory_expiry",
        "ix_user_memory_active_learning",
    ),
    "eval_case": ("uq_eval_case_source_version", "ix_eval_case_family_time"),
    "eval_report": (
        "uq_eval_report_baseline_version",
        "uq_eval_report_experience_version",
        "ix_eval_report_family_time",
    ),
}

EXPECTED_UNIQUE_INDEXES: dict[str, tuple[str, ...]] = {
    "run_record": ("uq_run_record_active_conversation",),
    "runtime_snapshot": ("uq_runtime_snapshot_run_id",),
    "canonical_event": ("uq_canonical_event_conversation_seq",),
    "user_memory": ("uq_user_memory_key",),
    "eval_case": ("uq_eval_case_source_version",),
    "eval_report": ("uq_eval_report_baseline_version", "uq_eval_report_experience_version"),
}

EXPECTED_PARTIAL_INDEXES: dict[str, tuple[str, ...]] = {
    "run_record": ("uq_run_record_active_conversation", "ix_run_record_running_lease_until"),
    "user_memory": (
        "ix_user_memory_candidates",
        "ix_user_memory_expiry",
        "ix_user_memory_active_learning",
    ),
    "eval_report": ("uq_eval_report_baseline_version", "uq_eval_report_experience_version"),
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


def _orm_index_columns(index: sa.Index | sa.UniqueConstraint) -> list[str]:
    expressions = index.expressions if isinstance(index, sa.Index) else index.columns
    names: list[str] = []
    for column in expressions:
        name = getattr(column, "name", None)
        names.append(str(name) if name is not None else str(column).split()[0])
    return names


def _orm_index_objects(orm_table: sa.Table) -> dict[str, sa.Index | sa.UniqueConstraint]:
    objects: dict[str, sa.Index | sa.UniqueConstraint] = {}
    for index in orm_table.indexes:
        if index.name is not None:
            objects[index.name] = index
    for constraint in orm_table.constraints:
        if isinstance(constraint, sa.UniqueConstraint) and constraint.name is not None:
            objects[constraint.name] = constraint
    return objects


def _index_diffs(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    orm_objects = _orm_index_objects(orm_table)
    db_indexes = {str(item["name"]): item for item in reflected["indexes"]}
    diffs: list[str] = []
    for name in EXPECTED_INDEXES[orm_table.name]:
        orm_object = orm_objects.get(name)
        db_index = db_indexes.get(name)
        if orm_object is None:
            diffs.append(f"index {name} missing from ORM")
            continue
        if db_index is None:
            diffs.append(f"index {name} missing from database")
            continue
        orm_unique = bool(orm_object.unique) if isinstance(orm_object, sa.Index) else True
        if orm_unique != bool(db_index["unique"]):
            diffs.append(f"index {name} unique differs: orm={orm_unique} db={db_index['unique']}")
        orm_columns = _orm_index_columns(orm_object)
        db_columns = [str(column) for column in db_index["column_names"]]
        if orm_columns != db_columns:
            diffs.append(f"index {name} columns differ: orm={orm_columns} db={db_columns}")
        orm_partial = (
            isinstance(orm_object, sa.Index)
            and orm_object.dialect_options["postgresql"].get("where") is not None
        )
        db_options = db_index.get("dialect_options") or {}
        db_partial = db_options.get("postgresql_where") is not None
        if orm_partial != db_partial:
            diffs.append(f"index {name} partial predicate differs: orm={orm_partial} db={db_partial}")
    for name in EXPECTED_UNIQUE_INDEXES.get(orm_table.name, ()):
        db_index = db_indexes.get(name)
        if db_index is None:
            diffs.append(f"unique index {name} missing from database")
        elif not db_index["unique"]:
            diffs.append(f"index {name} must be unique")
    for name in EXPECTED_PARTIAL_INDEXES.get(orm_table.name, ()):
        db_index = db_indexes.get(name)
        db_options = (db_index or {}).get("dialect_options") or {}
        if db_index is None:
            diffs.append(f"partial index {name} missing from database")
        elif db_options.get("postgresql_where") is None:
            diffs.append(f"index {name} must be partial")
    return diffs


def _compare(orm_table: sa.Table, reflected: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    diffs.extend(_column_diffs(orm_table, reflected))
    diffs.extend(_primary_key_diffs(orm_table, reflected))
    diffs.extend(_foreign_key_diffs(orm_table, reflected))
    diffs.extend(_index_diffs(orm_table, reflected))
    return diffs


@pytest.mark.parametrize("table_name", sorted(TABLE_MODELS))
async def test_runtime_table_schema_parity(database_guard: None, table_name: str) -> None:
    diffs = _compare(TABLE_MODELS[table_name], await _reflect(table_name))
    assert not diffs, f"{table_name} schema mismatch:\n" + "\n".join(diffs)


# ---- B-101（08 TASK-001）：run_submission + 缺失 ORM + canonical_event 扩展 ----

def test_b101_run_submission_orm_registered() -> None:
    """[B-101] RunSubmission ORM 存在且带 partial unique (tenant,key,endpoint)。"""
    from muad_agent_runtime.infrastructure.models.runtime import RunSubmission

    assert RunSubmission.__tablename__ == "run_submission"
    index_names = {idx.name for idx in RunSubmission.__table__.indexes}
    assert "uq_run_submission_tenant_key_endpoint" in index_names
    assert "ix_run_submission_run_create_time" in index_names


def test_b101_missing_audit_orm_classes_exist() -> None:
    """[B-101] Memory/Artifact/三类审计 ORM 与既有表对齐（不重复建表）。"""
    from muad_agent_runtime.infrastructure.models.runtime import (
        Artifact,
        EgressAudit,
        ModelInvocationAudit,
        ToolCallAudit,
        UserMemory,
    )

    assert UserMemory.__tablename__ == "user_memory"
    assert Artifact.__tablename__ == "artifact"
    assert ToolCallAudit.__tablename__ == "tool_call_audit"
    assert EgressAudit.__tablename__ == "egress_audit"
    assert ModelInvocationAudit.__tablename__ == "model_invocation_audit"


async def test_b101_run_submission_table_partial_unique() -> None:
    """[B-101][integration] 真实 PostgreSQL：软删后同键可重建；活跃重复被拒。"""
    from muad_agent_runtime.infrastructure.models.runtime import RunSubmission
    from sqlalchemy.exc import IntegrityError

    async with get_session_factory()() as session:
        row = RunSubmission(
            tenant_id="parity-t",
            idempotency_key=f"k-{uuid.uuid4()}",
            endpoint="create-run",
            request_fingerprint="sha256:" + "0" * 64,
            actor_user_id=uuid.uuid4(),
            status="CLOSED",
        )
        session.add(row)
        await session.commit()
        tenant, key = row.tenant_id, row.idempotency_key

        def build() -> RunSubmission:
            return RunSubmission(
                tenant_id=tenant,
                idempotency_key=key,
                endpoint="create-run",
                request_fingerprint="sha256:" + "1" * 64,
                actor_user_id=uuid.uuid4(),
                status="CLOSED",
            )

        session.add(build())
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

        row.is_deleted = True
        session.add(row)
        await session.commit()
        session.add(build())
        await session.commit()  # 软删后可重建
        await session.execute(
            sa.delete(RunSubmission).where(
                RunSubmission.tenant_id == tenant
            )
        )
        await session.commit()


async def test_b101_canonical_event_submission_columns() -> None:
    """[B-101] canonical_event 具备 submission_id（FK）与 stream_type 列。"""
    reflected = await _reflect("canonical_event")
    columns = {col["name"] for col in reflected["columns"]}
    assert "submission_id" in columns
    assert "stream_type" in columns


# ---- B-04（agent-self-evolution TASK-001）：学习数据模型 ----

def test_b04_memory_category_exposes_experience_categories() -> None:
    """[B-04] 契约层 MemoryCategory 暴露经验类别，读取新类别不触发校验失败。"""
    from muad_contracts.memory import MemoryCategory

    values = {category.value for category in MemoryCategory}
    assert {"PROCEDURE", "FAILURE_PATTERN", "VERIFICATION"} <= values


async def test_b04_user_memory_defaults_and_experience_categories(database_guard: None) -> None:
    """[B-04][integration] 既有记忆行默认 ACTIVE；经验类别可写入，非法类别被 DB 拒绝。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.exc import IntegrityError

    tenant, other_tenant = f"parity-{uuid.uuid4()}", f"parity-{uuid.uuid4()}"
    expires = datetime.now(UTC) + timedelta(days=30)
    async with get_session_factory()() as session:
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                memory_key="proc.batch-customers",
                category="PROCEDURE",
                content_json={"value": "逐个客户调用 skill 并核对数量"},
                author="AGENT",
                confidence=2,
                explicit_request=False,
                expires_at=expires,
            )
        )
        await session.commit()
    async with get_session_factory()() as session:
        stored = await session.scalar(
            sa.select(UserMemory).where(UserMemory.tenant_id == tenant)
        )
        assert stored is not None
        assert stored.activation_state == "ACTIVE"
        assert stored.task_signature is None
        assert stored.trigger_text is None
        assert stored.evidence_json is None
        session.add(
            UserMemory(
                tenant_id=other_tenant,
                user_id=uuid.uuid4(),
                memory_key="bad.category",
                category="NOT_A_CATEGORY",
                content_json={"value": "x"},
                author="AGENT",
                confidence=2,
                explicit_request=False,
                expires_at=expires,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    async with get_session_factory()() as session:
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id.in_((tenant, other_tenant))))
        await session.commit()


async def test_b04_eval_report_baseline_versions_are_append_only(database_guard: None) -> None:
    """[B-04][integration] 同一基线配置同版本只能有一行，新版本可追加。"""
    from sqlalchemy.exc import IntegrityError

    tenant = f"parity-{uuid.uuid4()}"
    base = "sha256:" + "0" * 64

    def build(version: int) -> EvalReport:
        return EvalReport(
            tenant_id=tenant,
            task_family="skill:demo",
            experience_key=None,
            base_config_hash=base,
            metrics_json={},
            decision_json={},
            report_json={},
            content_hash="sha256:" + "1" * 64,
            version=version,
        )

    async with get_session_factory()() as session:
        session.add(build(1))
        await session.commit()
        session.add(build(2))
        await session.commit()
        session.add(build(1))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalReport).where(EvalReport.tenant_id == tenant))
        await session.commit()


async def test_b04_eval_case_requires_existing_source_run(database_guard: None) -> None:
    """[B-04][integration] eval_case 的来源 Run 必须存在（物理 FK）。"""
    from sqlalchemy.exc import IntegrityError

    async with get_session_factory()() as session:
        session.add(
            EvalCase(
                tenant_id=f"parity-{uuid.uuid4()}",
                task_family="skill:demo",
                source_run_id=uuid.uuid4(),
                input_ref_json={},
                criteria_json={},
                content_hash="sha256:" + "2" * 64,
                version=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
