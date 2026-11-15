"""scheduled-task-v2 的数据库不变量（§3.3.12）。

真实边界：迁移到 head 的真实 PostgreSQL。应用层 CAS 只是第二道防线：唯一约束与 XOR 必须由
数据库兜底，否则两个 Worker 或两次用户动作能写出两份“唯一”事实。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64
TENANT = "b16-schema"


@pytest.fixture
async def engine(datastore_at_revision) -> AsyncEngine:
    info = datastore_at_revision("0050")
    created = create_async_engine(info["database_url"])
    try:
        yield created
    finally:
        await created.dispose()


async def _seed_schedule(engine: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    schedule_id, revision_id, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO task.task_schedule "
                "(id, tenant_id, owner_user_id, agent_id, name, created_by) "
                "VALUES (:id, :tenant, :user, :user, 'nightly', :user)"
            ),
            {"id": schedule_id, "tenant": TENANT, "user": user},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO task.task_schedule_revision "
                "(id, tenant_id, schedule_id, revision_no, mode, instruction_raw, normalized_goal, "
                " recurrence_kind, cron_expr, timezone, created_by) "
                "VALUES (:id, :tenant, :schedule, 1, 'SKILL', 'nightly', 'nightly', "
                " 'CRON', '0 9 * * *', 'UTC', :user)"
            ),
            {"id": revision_id, "tenant": TENANT, "schedule": schedule_id, "user": user},
        )
    return schedule_id, revision_id, user


async def _seed_fire(engine: AsyncEngine, schedule_id: uuid.UUID, revision_id: uuid.UUID) -> uuid.UUID:
    fire_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO task.schedule_fire "
                "(id, tenant_id, schedule_id, schedule_revision_id, scheduled_at, status) "
                "VALUES (:id, :tenant, :schedule, :revision, :at, 'CREATED')"
            ),
            {
                "id": fire_id,
                "tenant": TENANT,
                "schedule": schedule_id,
                "revision": revision_id,
                "at": NOW,
            },
        )
    return fire_id


async def _insert_execution(engine: AsyncEngine, **overrides: object) -> uuid.UUID:
    execution_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": execution_id,
        "tenant": TENANT,
        "agent": uuid.uuid4(),
        "actor": uuid.uuid4(),
        "schedule": None,
        "fire": None,
        "revision": None,
        "parent": None,
        "idempotency_key": f"k-{execution_id}",
    }
    values.update(overrides)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO task.task_execution "
                "(id, tenant_id, agent_id, actor_user_id, schedule_id, schedule_fire_id, "
                " schedule_revision_id, parent_id, trigger_type, status, input_json, "
                " execution_snapshot_json, snapshot_hash, idempotency_key) "
                "VALUES (:id, :tenant, :agent, :actor, :schedule, :fire, :revision, :parent, "
                " 'SCHEDULED', 'QUEUED', '{}'::jsonb, '{}'::jsonb, :hash, :idempotency_key)"
            ),
            {**values, "hash": HASH},
        )
    return execution_id


async def test_one_root_execution_per_fire(engine: AsyncEngine) -> None:
    schedule_id, revision_id, _ = await _seed_schedule(engine)
    fire_id = await _seed_fire(engine, schedule_id, revision_id)
    await _insert_execution(engine, schedule=schedule_id, fire=fire_id, revision=revision_id)

    with pytest.raises(sa.exc.IntegrityError):
        await _insert_execution(engine, schedule=schedule_id, fire=fire_id, revision=revision_id)


async def test_child_executions_are_not_limited_by_the_fire(engine: AsyncEngine) -> None:
    schedule_id, revision_id, _ = await _seed_schedule(engine)
    fire_id = await _seed_fire(engine, schedule_id, revision_id)
    root = await _insert_execution(engine, schedule=schedule_id, fire=fire_id, revision=revision_id)
    for _ in range(2):
        await _insert_execution(engine, schedule=schedule_id, fire=fire_id, parent=root)


async def test_only_one_open_human_interaction_per_execution(engine: AsyncEngine) -> None:
    execution_id = await _insert_execution(engine)
    insert = sa.text(
        "INSERT INTO task.task_human_interaction "
        "(tenant_id, execution_id, owner_user_id, agent_id, interaction_type, status, question, deadline_at) "
        "VALUES (:tenant, :execution, :user, :user, 'INPUT', :status, '请选择客户', :deadline)"
    )
    params = {
        "tenant": TENANT,
        "execution": execution_id,
        "user": uuid.uuid4(),
        "deadline": NOW,
    }
    async with engine.begin() as connection:
        await connection.execute(insert, {**params, "status": "RESOLVED"})
        await connection.execute(insert, {**params, "status": "OPEN"})
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(insert, {**params, "status": "OPEN"})


async def test_confirmation_ref_can_only_confirm_one_draft(engine: AsyncEngine) -> None:
    insert = sa.text(
        "INSERT INTO task.task_schedule_draft "
        "(tenant_id, owner_user_id, agent_id, kind, draft_hash, payload_json, preview_json, status, "
        " expires_at, confirmation_surface, confirmation_ref, idempotency_key) "
        "VALUES (:tenant, :user, :user, 'CREATE', :hash, '{}'::jsonb, '{}'::jsonb, 'CONFIRMED', "
        " :expires, 'WEBCHAT', 'msg-1', :key)"
    )
    user = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            insert, {"tenant": TENANT, "user": user, "hash": HASH, "expires": NOW, "key": "draft-1"}
        )
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                insert, {"tenant": TENANT, "user": user, "hash": HASH, "expires": NOW, "key": "draft-2"}
            )


async def test_draft_hash_must_be_a_full_sha256(engine: AsyncEngine) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO task.task_schedule_draft "
                    "(tenant_id, owner_user_id, agent_id, kind, draft_hash, payload_json, preview_json, "
                    " status, expires_at, idempotency_key) "
                    "VALUES (:tenant, :user, :user, 'CREATE', :hash, '{}'::jsonb, '{}'::jsonb, "
                    " 'PENDING', :expires, 'draft-invalid')"
                ),
                {"tenant": TENANT, "user": uuid.uuid4(), "hash": "a" * 64, "expires": NOW},
            )


async def test_model_call_lease_owner_is_exclusive(engine: AsyncEngine) -> None:
    execution_id = await _insert_execution(engine)
    insert = sa.text(
        "INSERT INTO runtime.model_call_lease "
        "(tenant_id, call_attempt_id, task_execution_id, owner_type, deployment_id, credential_id, "
        " key_resource_id, credential_revision, workload, lease_owner, lease_until, reserved_tokens) "
        "VALUES (:tenant, :attempt, :execution, :owner_type, gen_random_uuid(), gen_random_uuid(), "
        " gen_random_uuid(), 1, 'BACKGROUND', 'worker', :lease_until, 128)"
    )
    async with engine.begin() as connection:
        await connection.execute(
            insert,
            {
                "tenant": TENANT,
                "attempt": uuid.uuid4(),
                "execution": execution_id,
                "owner_type": "TASK",
                "lease_until": NOW,
            },
        )
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                insert,
                {
                    "tenant": TENANT,
                    "attempt": uuid.uuid4(),
                    "execution": execution_id,
                    "owner_type": "RUN",
                    "lease_until": NOW,
                },
            )


async def test_tool_operation_owner_type_must_match_the_owner_column(engine: AsyncEngine) -> None:
    insert = sa.text(
        "INSERT INTO runtime.tool_operation "
        "(tenant_id, owner_type, run_id, owner_task_execution_id, actor_user_id, source_tool_call_id, "
        " completion_mode, status, task_snapshot_hash, submission_json, input_hash) "
        "VALUES (:tenant, :owner_type, NULL, :execution, :actor, :call_id, 'JOIN', 'SUBMITTED', "
        " :hash, '{}'::jsonb, :hash)"
    )
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                insert,
                {
                    "tenant": TENANT,
                    "owner_type": "TASK",
                    "execution": None,
                    "actor": uuid.uuid4(),
                    "call_id": "call-missing-owner",
                    "hash": HASH,
                },
            )
