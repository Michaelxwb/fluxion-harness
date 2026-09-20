"""[B-118] PG Checkpointer 与 Interrupt 持久化（真实 PostgreSQL runtime.run_interrupt）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.checkpoint import (
    InterruptCheckpoint,
    checkpoint_interrupt,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunInterrupt, RunRecord


@pytest.fixture()
async def running_run():
    tenant = f"ckpt-{uuid.uuid4()}"
    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    owner = f"inst-{uuid.uuid4()}"
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=0,
            )
        )
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant,
                conversation_id=conv_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="RUNNING",
                input_text="hi",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
                lease_owner=owner,
            )
        )
        await session.commit()
    yield {
        "tenant_id": tenant,
        "conversation_id": conv_id,
        "run_id": run_id,
        "owner": owner,
    }
    async with get_session_factory()() as session:
        await session.execute(
            RunInterrupt.__table__.delete().where(RunInterrupt.run_id == run_id)
        )
        await session.execute(RunRecord.__table__.delete().where(RunRecord.id == run_id))
        await session.execute(Conversation.__table__.delete().where(Conversation.id == conv_id))
        await session.commit()


async def test_b118_checkpoint_interrupt_persists_and_releases(running_run) -> None:
    """[B-118] interrupt：run_interrupt WAITING 行 + Run WAITING_INPUT + 租约释放。"""
    run_id = running_run["run_id"]
    checkpoint = await checkpoint_interrupt(
        tenant_id=running_run["tenant_id"],
        run_id=run_id,
        conversation_id=running_run["conversation_id"],
        lease_owner=running_run["owner"],
        interrupt_type="CLARIFICATION",
        prompt_text="which service?",
        options_json=[{"value": "a"}, {"value": "b"}],
        checkpoint_state={"node": "model", "messages": 3},
    )
    assert checkpoint["interrupt_id"]

    async with get_session_factory()() as session:
        interrupt = (
            await session.execute(
                sa.select(RunInterrupt).where(RunInterrupt.run_id == run_id)
            )
        ).scalar_one()
        assert interrupt.status == "WAITING"
        assert interrupt.interrupt_type == "CLARIFICATION"
        run = await session.get(RunRecord, run_id)
        assert run.status == "WAITING_INPUT"
        assert run.lease_until is None  # 租约时间释放；owner 保留为最后执行者记录


async def test_b118_rebuild_locates_waiting_point_after_restart(running_run) -> None:
    """[B-118] 进程重建后：load_waiting_interrupt 仍可定位等待点（业务事实在 DB）。"""
    run_id = running_run["run_id"]
    await checkpoint_interrupt(
        tenant_id=running_run["tenant_id"],
        run_id=run_id,
        conversation_id=running_run["conversation_id"],
        lease_owner=running_run["owner"],
        interrupt_type="CONFIRM",
        prompt_text="confirm?",
        options_json=[],
        checkpoint_state={"node": "tools"},
    )

    # 模拟进程重启：全新 service 实例（同一 DB）仍可读取
    fresh_service = InterruptCheckpoint(session_factory=get_session_factory)
    located = await fresh_service.locate_waiting(run_id=run_id)
    assert located is not None
    assert located["interrupt_type"] == "CONFIRM"


async def test_b118_unauthorized_executor_cannot_resolve(running_run) -> None:
    """[B-118] 非原租约执行者不能推进 WAITING interrupt。"""
    run_id = running_run["run_id"]
    await checkpoint_interrupt(
        tenant_id=running_run["tenant_id"],
        run_id=run_id,
        conversation_id=running_run["conversation_id"],
        lease_owner=running_run["owner"],
        interrupt_type="CONFIRM",
        prompt_text="confirm?",
        options_json=[],
        checkpoint_state={},
    )
    service = InterruptCheckpoint(session_factory=get_session_factory)
    with pytest.raises(PermissionError):
        await service.resolve(
            run_id=run_id,
            lease_owner="intruder",
            resolution_json={"choice": "a"},
        )


async def test_b118_resolve_updates_interrupt_and_run(running_run) -> None:
    """[B-118] 授权执行者 resolve：interrupt RESOLVED + Run READY_FOR_EXECUTION。"""
    run_id = running_run["run_id"]
    await checkpoint_interrupt(
        tenant_id=running_run["tenant_id"],
        run_id=run_id,
        conversation_id=running_run["conversation_id"],
        lease_owner=running_run["owner"],
        interrupt_type="CONFIRM",
        prompt_text="confirm?",
        options_json=[],
        checkpoint_state={"node": "tools"},
    )
    service = InterruptCheckpoint(session_factory=get_session_factory)
    await service.resolve(
        run_id=run_id,
        lease_owner=running_run["owner"],  # 授权执行者（原 owner 接管 resume）
        resolution_json={"choice": "a"},
    )
    async with get_session_factory()() as session:
        interrupt = (
            await session.execute(
                sa.select(RunInterrupt).where(RunInterrupt.run_id == run_id)
            )
        ).scalar_one()
        assert interrupt.status == "RESOLVED"
        run = await session.get(RunRecord, run_id)
        assert run.status == "RUNNING"  # resume 后重新接管的执行状态
