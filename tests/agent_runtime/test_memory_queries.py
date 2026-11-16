"""TASK-002: v2 snapshot query returns real revision/as_of and structured fields."""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_contracts.memory import MemoryCategory
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_datastores import async_tool_database as async_tool_database

pytestmark = pytest.mark.integration


async def test_v2_snapshot_reads_structured_fields_and_snapshot_time(async_tool_database):
    tenant, user, memory_id = "v2-query", uuid4(), uuid4()
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (id,tenant_id,user_id,memory_key,category,memory_kind,predicate,scope_state,"
                " content_json,title,summary,tags_json,cardinality,priority,taxonomy_version,"
                " content_text,scope_json,scope_hash,author,authority,version,"
                " explicit_request,confirmation_event_id)"
                " VALUES (:id,:tenant,:user,'v1.slot.preference.report_format.x','PREFERENCE',"
                " 'PREFERENCE','report_format','ABSENT',jsonb_build_object('format','PDF'),"
                " '内部报告','摘要',jsonb_build_array('topic:报告格式'),'SINGLE','REQUIRED',1,"
                " '内部报告','{}'::jsonb,'','USER','USER_EXPLICIT',1,true,:event)"
            ),
            {"id": memory_id, "tenant": tenant, "user": user, "event": uuid4()},
        )
    queries = MemoryQueries(async_sessionmaker(async_tool_database, expire_on_commit=False))
    snapshot = await queries.read_snapshot(MemoryReadScope(tenant_id=tenant, user_id=user))
    assert snapshot.revision == 0
    assert snapshot.as_of is not None
    assert len(snapshot.rows) == 1
    row = snapshot.rows[0]
    assert row.memory_kind == "PREFERENCE"
    assert row.predicate == "report_format"
    assert row.content == {"format": "PDF"}
    assert row.tags == ("topic:报告格式",)
    assert row.cardinality == "SINGLE"
    assert row.priority == "REQUIRED"


async def test_snapshot_keeps_v2_and_legacy_lanes_separate(async_tool_database):
    tenant, user = "v2-lane", uuid4()
    v2_id, legacy_id = uuid4(), uuid4()
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (id,tenant_id,user_id,memory_key,memory_kind,predicate,content_json,expires_at)"
                " VALUES (:id,:tenant,:user,'user:fact:role','FACT','role',"
                " CAST(:content AS jsonb), statement_timestamp()+interval '30 days')"
            ),
            {"id": v2_id, "tenant": tenant, "user": user, "content": '{"value":"pm"}'},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (id,tenant_id,user_id,memory_key,category,content_json,expires_at)"
                " VALUES (:id,:tenant,:user,'exp.deploy.check','PROCEDURE',"
                " CAST(:content AS jsonb), statement_timestamp()+interval '30 days')"
            ),
            {"id": legacy_id, "tenant": tenant, "user": user, "content": '{"value":"verified step"}'},
        )
    queries = MemoryQueries(async_sessionmaker(async_tool_database, expire_on_commit=False))
    scope = MemoryReadScope(tenant_id=tenant, user_id=user)

    snapshot = await queries.read_snapshot(scope)
    assert [row.id for row in snapshot.rows] == [v2_id]
    assert [row.id for row in snapshot.legacy_rows] == [legacy_id]
    assert snapshot.rows[0].memory_kind == "FACT"
    assert snapshot.legacy_rows[0].record.category == MemoryCategory.PROCEDURE
    assert snapshot.legacy_rows[0].record.activation_state == "ACTIVE"

    legacy_snapshot = await queries.read_experience_snapshot(scope)
    assert [row.id for row in legacy_snapshot.rows] == [legacy_id]
    assert legacy_snapshot.revision == snapshot.revision
    assert legacy_snapshot.forget_epoch == snapshot.forget_epoch


async def test_cached_snapshot_keeps_both_lanes(async_tool_database):
    tenant, user = "v2-cache-lane", uuid4()
    v2_id, legacy_id = uuid4(), uuid4()
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (id,tenant_id,user_id,memory_key,memory_kind,predicate,content_json,expires_at)"
                " VALUES (:id,:tenant,:user,'user:fact:role','FACT','role',"
                " CAST(:content AS jsonb), statement_timestamp()+interval '30 days')"
            ),
            {"id": v2_id, "tenant": tenant, "user": user, "content": '{"value":"pm"}'},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (id,tenant_id,user_id,memory_key,category,content_json,expires_at)"
                " VALUES (:id,:tenant,:user,'exp.deploy.check','PROCEDURE',"
                " CAST(:content AS jsonb), statement_timestamp()+interval '30 days')"
            ),
            {"id": legacy_id, "tenant": tenant, "user": user, "content": '{"value":"verified step"}'},
        )
    queries = MemoryQueries(async_sessionmaker(async_tool_database, expire_on_commit=False))
    cache = MemoryCandidateCache(
        queries, scope=MemoryReadScope(tenant_id=tenant, user_id=user), run_id=uuid4()
    )

    await cache.read_for_injection(max_items=10, max_bytes=4096)
    warm = await cache.read_for_injection(max_items=10, max_bytes=4096)

    assert [row.id for row in warm.rows] == [v2_id]
    assert [row.id for row in warm.legacy_rows] == [legacy_id]
