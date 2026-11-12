"""Persistent egress registry constraints against migrated, isolated PostgreSQL."""
import importlib
import uuid

import pytest
import sqlalchemy as sa
from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


async def test_egress_identity_schema_parity():
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda conn: sa.inspect(conn).get_table_names(schema="runtime")
            )
            assert {"egress_execution", "egress_invocation"} <= set(tables)
            models = importlib.import_module("muad_platform_sdk.egress_identity_models")
            for model in (models.EgressExecution, models.EgressInvocation):
                columns = await connection.run_sync(
                    lambda conn, table=model.__tablename__: sa.inspect(conn).get_columns(
                        table, schema="runtime"
                    )
                )
                assert {c["name"] for c in columns} == set(model.__table__.columns.keys())
                assert {c["name"]: c["nullable"] for c in columns} == {
                    c.name: c.nullable for c in model.__table__.columns
                }
                uniques = await connection.run_sync(
                    lambda conn, table=model.__tablename__: sa.inspect(conn).get_unique_constraints(
                        table, schema="runtime"
                    )
                )
                assert {tuple(c["column_names"]) for c in uniques} == {
                    tuple(c.name for c in constraint.columns) for constraint in model.__table__.constraints
                    if isinstance(constraint, sa.UniqueConstraint)
                }
                for column in columns:
                    if column["name"] in {"create_time", "update_time"}:
                        assert column["type"].timezone
                    if column["name"] == "policy_json":
                        assert isinstance(column["type"], sa.dialects.postgresql.JSONB)
    finally:
        await engine.dispose()


async def test_egress_identity_tombstone_and_cross_tenant_constraints():
    engine = create_async_engine(SharedSettings().require_database_url())
    tenant = "egress-schema-" + uuid.uuid4().hex
    execution, source, invocation = (uuid.uuid4() for _ in range(3))
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("""
                INSERT INTO runtime.egress_execution
                  (id,tenant_id,source_kind,source_id,policy_json,policy_hash,meter_epoch,is_deleted)
                VALUES (:id,:tenant,'RUN',:source,'{}',:hash,'epoch',true)
            """), {"id": execution, "tenant": tenant, "source": source, "hash": "a" * 64})
            with pytest.raises(sa.exc.IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(sa.text("""
                        INSERT INTO runtime.egress_execution
                          (tenant_id,source_kind,source_id,policy_json,policy_hash,meter_epoch)
                        VALUES (:tenant,'RUN',:source,'{}',:hash,'epoch')
                    """), {"tenant": tenant, "source": source, "hash": "a" * 64})
            with pytest.raises(sa.exc.IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(sa.text("""
                        INSERT INTO runtime.egress_invocation
                          (id,tenant_id,execution_id,operation_key,skill_key)
                        VALUES (:id,:tenant,:execution,'call','skill')
                    """), {"id": invocation, "tenant": tenant + "-other", "execution": execution})
    finally:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DELETE FROM runtime.egress_execution WHERE tenant_id=:tenant"),
                                     {"tenant": tenant})
        await engine.dispose()


async def test_egress_invocation_operation_key_cannot_be_reused_after_tombstone():
    models = importlib.import_module("muad_platform_sdk.egress_identity_models")
    engine = create_async_engine(SharedSettings().require_database_url())
    tenant, execution = "egress-schema-" + uuid.uuid4().hex, uuid.uuid4()
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(sa.insert(models.EgressExecution).values(
                    id=execution, tenant_id=tenant, source_kind="TASK", source_id=uuid.uuid4(),
                    policy_json={}, policy_hash="a" * 64, meter_epoch="epoch",
                ))
                values = dict(tenant_id=tenant, execution_id=execution,
                              operation_key="durable-tool-call", skill_key="skill")
                await connection.execute(sa.insert(models.EgressInvocation).values(**values, is_deleted=True))
                with pytest.raises(sa.exc.IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(sa.insert(models.EgressInvocation).values(**values))
                await connection.rollback()
    finally:
        await engine.dispose()
