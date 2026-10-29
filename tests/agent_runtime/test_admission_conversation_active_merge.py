"""admission 读合并：conversation + active run 一次读与两次独立读语义一致。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from muad_agent_runtime.application.channel_conversations import (
    ConversationScope,
    load_explicit_conversation_with_active_run,
)
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    RunRecord,
    RunStatus,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

INSTANCE = "merge-test"
ACTIVE = (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.WAITING_INPUT, RunStatus.WAITING_TOOL)


@pytest.fixture
async def session_factory(
    async_tool_datastore: dict[str, str],
) -> AsyncIterator[async_sessionmaker[Any]]:
    engine = create_async_engine(async_tool_datastore["database_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(
    factory: async_sessionmaker[Any],
    *,
    status: RunStatus,
    channel_account_id: uuid.UUID | None = None,
) -> tuple[ConversationScope, uuid.UUID, uuid.UUID]:
    tenant_id = f"merge-{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    async with factory() as session:
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            channel_account_id=channel_account_id,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            input_text="merge",
            trace_id=uuid.uuid4().hex,
            lease_owner=INSTANCE,
            execution_epoch=0,
            lease_until=datetime.now(UTC) + timedelta(seconds=300),
        )
        session.add(run)
        await session.commit()
        return (
            ConversationScope(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                entry_id=None,
                external_thread_id="",
            ),
            conversation.id,
            run.id,
        )


async def test_returns_conversation_and_active_run(
    session_factory: async_sessionmaker[Any],
) -> None:
    scope, conversation_id, run_id = await _seed(session_factory, status=RunStatus.RUNNING)
    async with session_factory() as session:
        conversation, active = await load_explicit_conversation_with_active_run(
            session, scope, conversation_id, active_statuses=ACTIVE
        )
    assert conversation.id == conversation_id
    assert active is not None
    assert active.id == run_id


async def test_terminal_run_is_not_active(
    session_factory: async_sessionmaker[Any],
) -> None:
    scope, conversation_id, _ = await _seed(session_factory, status=RunStatus.COMPLETED)
    async with session_factory() as session:
        conversation, active = await load_explicit_conversation_with_active_run(
            session, scope, conversation_id, active_statuses=ACTIVE
        )
    assert conversation.id == conversation_id
    assert active is None


async def test_wrong_tenant_is_not_found(
    session_factory: async_sessionmaker[Any],
) -> None:
    scope, conversation_id, _ = await _seed(session_factory, status=RunStatus.RUNNING)
    wrong = ConversationScope(
        tenant_id=f"{scope.tenant_id}-other",
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        entry_id=None,
        external_thread_id="",
    )
    async with session_factory() as session:
        with pytest.raises(AppError) as error:
            await load_explicit_conversation_with_active_run(
                session, wrong, conversation_id, active_statuses=ACTIVE
            )
    assert error.value.code == ErrorCode.COMMON_NOT_FOUND


async def test_entry_attribution_is_preserved(
    session_factory: async_sessionmaker[Any],
) -> None:
    scope, conversation_id, _ = await _seed(
        session_factory,
        status=RunStatus.RUNNING,
        channel_account_id=uuid.uuid4(),
    )
    async with session_factory() as session:
        with pytest.raises(AppError) as error:
            await load_explicit_conversation_with_active_run(
                session, scope, conversation_id, active_statuses=ACTIVE
            )
    assert error.value.code == ErrorCode.COMMON_NOT_FOUND
