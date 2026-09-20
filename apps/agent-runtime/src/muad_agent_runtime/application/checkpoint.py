"""Interrupt Checkpoint：run_interrupt WAITING 持久化 + 租约释放 + 定位/resolve。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from ..infrastructure.models.runtime import Conversation, RunInterrupt, RunRecord

STATUS_WAITING_INPUT = "WAITING_INPUT"
STATUS_RUNNING = "RUNNING"
INTERRUPT_WAITING = "WAITING"
INTERRUPT_RESOLVED = "RESOLVED"


async def checkpoint_interrupt(
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    lease_owner: str,
    interrupt_type: str,
    prompt_text: str,
    options_json: list[Any],
    checkpoint_state: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    factory = session_factory or get_session_factory()
    async with factory() as session:
        session.add(
            RunInterrupt(
                tenant_id=tenant_id,
                run_id=run_id,
                conversation_id=conversation_id,
                interrupt_type=interrupt_type,
                prompt_text=prompt_text,
                options_json=options_json,
                status=INTERRUPT_WAITING,
            )
        )
        run = await session.get(RunRecord, run_id)
        if run is not None:
            run.status = STATUS_WAITING_INPUT
            run.lease_until = None  # 释放租约（时间维度）；lease_owner 保留为最后执行者记录
        conversation = await session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.status = "WAITING_INPUT"
        await session.commit()
    return {
        "interrupt_id": str(run_id),
        "kind": interrupt_type,
        "prompt": prompt_text,
        "checkpoint_state": checkpoint_state,
    }


async def load_waiting_interrupt(
    run_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any] | None:
    factory = session_factory or get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(RunInterrupt).where(
                    RunInterrupt.run_id == run_id,
                    RunInterrupt.status == INTERRUPT_WAITING,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "interrupt_id": str(row.id),
            "run_id": str(row.run_id),
            "conversation_id": str(row.conversation_id),
            "interrupt_type": row.interrupt_type,
            "prompt_text": row.prompt_text,
            "options_json": row.options_json,
        }


class InterruptCheckpoint:
    """进程重建后仍可经 DB 定位等待点（业务事实不依赖进程内存）。"""

    def __init__(self, *, session_factory: SessionFactoryProvider) -> None:
        self._session_factory = session_factory

    async def locate_waiting(self, *, run_id: uuid.UUID) -> dict[str, Any] | None:
        return await load_waiting_interrupt(run_id, session_factory=self._session_factory())

    async def resolve(
        self,
        *,
        run_id: uuid.UUID,
        lease_owner: str | None,
        resolution_json: dict[str, Any],
    ) -> None:
        """推进 WAITING interrupt：仅持有租约/合法接管者（lease_owner=None resume 接管）可操作。"""
        async with self._session_factory()() as session:
            row = (
                await session.execute(
                    select(RunInterrupt).where(
                        RunInterrupt.run_id == run_id,
                        RunInterrupt.status == INTERRUPT_WAITING,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            run = await session.get(RunRecord, run_id)
            current_owner = run.lease_owner if run is not None else None
            if current_owner is not None and lease_owner is not None and current_owner != lease_owner:
                raise PermissionError("only the lease owner can resolve the interrupt")
            row.status = INTERRUPT_RESOLVED
            row.resolution_json = resolution_json
            row.resolved_at = datetime.now(UTC)
            if run is not None:
                run.status = STATUS_RUNNING
                run.lease_owner = None  # resume 由新 submission 接管
            await session.commit()


# 兼容直接调用（事务内调用方传入 session）
async def release_lease_and_wait(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    run = await session.get(RunRecord, run_id)
    if run is not None:
        run.status = STATUS_WAITING_INPUT
        run.lease_owner = None
        run.lease_until = None
    conversation = await session.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.status = "WAITING_INPUT"
    await session.flush()
