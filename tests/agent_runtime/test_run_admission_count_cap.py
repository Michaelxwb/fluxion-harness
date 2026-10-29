"""P2：租户在飞 Run 计数在 LIMIT 处提前停止，`>= max` 判定不变。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    RunRecord,
    RunStatus,
)
from muad_agent_runtime.infrastructure.run_admission_store import RunAdmissionStore

pytestmark = pytest.mark.integration

INSTANCE = "admission-count-test"


@pytest.fixture
async def count_tenant(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    await runtime_db.dispose_engine()
    try:
        yield f"count-{uuid.uuid4().hex[:12]}"
    finally:
        await runtime_db.dispose_engine()


async def _seed(tenant_id: str, *, open_runs: int, released_runs: int) -> list[uuid.UUID]:
    now = datetime.now(UTC)
    open_ids: list[uuid.UUID] = []
    async with get_session_factory()() as session:
        for index in range(open_runs + released_runs):
            user_id = uuid.uuid4()
            conversation = Conversation(
                tenant_id=tenant_id, user_id=user_id, agent_id=uuid.uuid4()
            )
            session.add(conversation)
            await session.flush()
            released = index >= open_runs
            run = RunRecord(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                user_id=user_id,
                agent_id=conversation.agent_id,
                status=RunStatus.COMPLETED if released else RunStatus.RUNNING,
                input_text="count",
                trace_id=uuid.uuid4().hex,
                lease_owner=INSTANCE,
                execution_epoch=0,
                lease_until=now + timedelta(seconds=300),
                admission_released_at=now if released else None,
            )
            session.add(run)
            await session.flush()
            if not released:
                open_ids.append(run.id)
        await session.commit()
    return open_ids


async def test_count_open_runs_caps_at_limit_without_changing_guard(
    count_tenant: str,
) -> None:
    open_ids = await _seed(count_tenant, open_runs=5, released_runs=2)
    async with get_session_factory()() as session:
        store = RunAdmissionStore(session)
        assert await store.count_open_runs(tenant_id=count_tenant) == 5
        assert await store.count_open_runs(tenant_id=count_tenant, limit=3) == 3
        assert await store.count_open_runs(tenant_id=count_tenant, limit=5) == 5
        assert await store.count_open_runs(tenant_id=count_tenant, limit=6) == 5
        assert (
            await store.count_open_runs(
                tenant_id=count_tenant, exclude_run_id=open_ids[0]
            )
            == 4
        )
        assert (
            await store.count_open_runs(
                tenant_id=count_tenant, exclude_run_id=open_ids[0], limit=3
            )
            == 3
        )
