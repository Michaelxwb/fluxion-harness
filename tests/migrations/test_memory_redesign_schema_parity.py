"""B-13: actual migration/create_all parity and physical uniqueness."""

from importlib import import_module
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.base import Base
from sqlalchemy.exc import IntegrityError

from tests.async_tool_datastores import async_tool_database as async_tool_database
from tests.async_tool_datastores import async_tool_datastore as async_tool_datastore

pytestmark = pytest.mark.integration
TABLES = (
    "user_memory",
    "memory_scope",
    "user_memory_history",
    "memory_command_receipt",
    "memory_consolidation_job",
    "run_interrupt",
    "canonical_event",
)


def _inspect(connection, name):
    inspector = sa.inspect(connection)
    columns = [
        (
            item["name"],
            str(item["type"]),
            item["nullable"],
            getattr(item["type"], "timezone", None),
            item["default"],
        )
        for item in inspector.get_columns(name, "runtime")
    ]
    indexes = [
        (
            item["name"],
            item["column_names"],
            item["unique"],
            str(item.get("dialect_options", {}).get("postgresql_where", "")),
            str(item.get("column_sorting", {})),
        )
        for item in inspector.get_indexes(name, "runtime")
    ]
    constraints = [
        (item["name"], item["sqltext"]) for item in inspector.get_check_constraints(name, "runtime")
    ]
    foreign_keys = [
        (
            tuple(item["constrained_columns"]),
            item["referred_schema"],
            item["referred_table"],
            tuple(item["referred_columns"]),
        )
        for item in inspector.get_foreign_keys(name, "runtime")
    ]
    primary_key = inspector.get_pk_constraint(name, "runtime")["constrained_columns"]
    return sorted(columns), sorted(indexes), sorted(constraints), sorted(foreign_keys), primary_key


async def test_b13_memory_redesign_schema_parity(async_tool_database):
    async with async_tool_database.begin() as connection:
        migrated = {name: await connection.run_sync(_inspect, name) for name in TABLES}
        await connection.execute(sa.text("DROP SCHEMA runtime CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA runtime"))
        await connection.run_sync(Base.metadata.create_all)
        created = {name: await connection.run_sync(_inspect, name) for name in TABLES}
    assert migrated == created
    assert "source_type" not in {item[0] for item in migrated["user_memory"][0]}
    names = {item[0] for item in migrated["user_memory"][1]}
    assert {"uq_user_memory_key", "ix_user_memory_candidates", "ix_user_memory_expiry"} <= names


async def test_b13_deleted_key_and_history_version_are_unique(async_tool_database):
    models = import_module("muad_agent_runtime.infrastructure.models.runtime")
    memory = models.UserMemory.__table__
    tenant, user, identity = "b13", uuid4(), uuid4()
    values = {
        "id": identity,
        "tenant_id": tenant,
        "user_id": user,
        "memory_key": "color",
        "category": "PREFERENCE",
        "content_json": {"value": "蓝色"},
        "author": "ADMIN",
        "is_deleted": True,
        "retirement_reason": "ADMIN_DELETE",
        "retirement_revision": 1,
    }
    async with async_tool_database.begin() as connection:
        await connection.execute(memory.insert().values(**values))
    with pytest.raises(IntegrityError):
        async with async_tool_database.begin() as connection:
            await connection.execute(memory.insert().values(**{**values, "id": uuid4(), "is_deleted": False}))
    history = models.UserMemoryHistory.__table__
    event = {
        "tenant_id": tenant,
        "user_id": user,
        "memory_key": "color",
        "origin_id": identity,
        "version": 1,
        "change_reason": "DELETE",
        "actor_type": "ADMIN",
        "command_id": "b13",
        "after_json": {"value": "蓝色"},
    }
    async with async_tool_database.begin() as connection:
        await connection.execute(history.insert().values(**event))
    with pytest.raises(IntegrityError):
        async with async_tool_database.begin() as connection:
            await connection.execute(history.insert().values(**event))


async def test_b13_confirmation_and_options_cross_outcome_are_physically_exclusive(async_tool_database):
    models = import_module("muad_agent_runtime.infrastructure.models.runtime")
    run, conversation, options = uuid4(), uuid4(), uuid4()
    proposal = {
        "tenant_id": "b13",
        "run_id": run,
        "conversation_id": conversation,
        "interrupt_type": "MEMORY_CONFIRMATION",
        "prompt_text": "confirm",
        "status": "WAITING",
    }
    async with async_tool_database.begin() as connection:
        await connection.execute(models.RunInterrupt.__table__.insert().values(**proposal))
    with pytest.raises(IntegrityError):
        async with async_tool_database.begin() as connection:
            await connection.execute(models.RunInterrupt.__table__.insert().values(**proposal))
    final = {
        "tenant_id": "b13",
        "conversation_id": conversation,
        "run_id": run,
        "source_event_id": options,
        "event_type": "MEMORY_RESUME_RESOLVED",
    }
    async with async_tool_database.begin() as connection:
        await connection.execute(
            models.CanonicalEvent.__table__.insert().values(
                **final, seq=1, payload_json={"outcome": "SELECTED"}
            )
        )
    with pytest.raises(IntegrityError):
        async with async_tool_database.begin() as connection:
            await connection.execute(
                models.CanonicalEvent.__table__.insert().values(
                    **final, seq=2, payload_json={"outcome": "CANCELLED"}
                )
            )
