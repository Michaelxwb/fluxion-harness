"""B-106: actual heartbeat and canonical terminal transaction guards."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from muad_agent_runtime.application import run_service
from muad_agent_runtime.application.async_tools.continuation_service import (
    ExecutionLeaseLost,
    renew_execution,
)
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_submission import RunSubmissionService
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord
from sqlalchemy import select

from tests.async_tool_continuations import running_source


async def test_b106_renew_requires_tenant_owner_epoch_and_unexpired_lease(async_tool_database):
    factory, context, skill, run, identity = await running_source(async_tool_database)
    assert await renew_execution(factory, identity, lease_sec=60)
    for rejected in (replace(identity, tenant_id="other-tenant"),
        replace(identity, owner="other-owner"), replace(identity, epoch=0)):
        assert not await renew_execution(factory, rejected, lease_sec=60)
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run.id)
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert not await renew_execution(factory, identity, lease_sec=60)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("rejected", ["owner", "epoch", "expired"])
async def test_b106_terminal_rejects_stale_segment_without_event(
    async_tool_database, monkeypatch, fake_resolve, failure, rejected,
):
    factory, context, skill, source, identity = await running_source(async_tool_database)
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)
    async with factory() as session:
        source = await session.get(RunRecord, source.id)
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, source.id)
        if rejected == "owner":
            row.lease_owner = "new-owner"
        elif rejected == "epoch":
            row.execution_epoch += 1
        else:
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    async with factory() as session:
        service = run_service.RunService(session, fake_resolve, identity.owner,
            NullPlatformSettingsClient(), supervisor=ExecutionSupervisor())
        with pytest.raises(ExecutionLeaseLost):
            if failure:
                await service._finalize_failed(source, uuid4(), RuntimeError("failed"), agent_key="guard")
            else:
                await service._finalize_run(source, uuid4(), "done", agent_key="guard")
    async with factory() as session:
        assert (await session.get(RunRecord, source.id)).status == "RUNNING"
        assert await session.scalar(select(CanonicalEvent.id)) is None


async def test_b106_terminal_cas_and_canonical_event_are_atomic(
    async_tool_database, monkeypatch, fake_resolve
):
    factory, context, skill, source, identity = await running_source(async_tool_database)
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)
    async with factory() as session:
        source = await session.get(RunRecord, source.id)
        submission = await RunSubmissionService(lambda: factory).record_in(session,
            tenant_id=source.tenant_id, idempotency_key=str(uuid4()), endpoint="create-run",
            actor_user_id=source.user_id, run_id=source.id, conversation_id=source.conversation_id,
            request_fingerprint="sha256:" + "a" * 64)
        await session.commit()
        service = run_service.RunService(session, fake_resolve, identity.owner,
            NullPlatformSettingsClient(), supervisor=ExecutionSupervisor())
        first = await service._finalize_run(source, submission.id, "done", agent_key="guard")
        replay = await service._finalize_run(source, submission.id, "rewritten", agent_key="guard")
        assert first.seq == replay.seq and replay.data["final_text"] == "done"
    async with factory() as session:
        saved = await session.get(RunRecord, source.id)
        assert saved.status == "COMPLETED" and saved.lease_owner is None and saved.lease_until is None
        events = list(await session.scalars(select(CanonicalEvent).order_by(CanonicalEvent.seq)))
        assert [event.event_type for event in events] == ["ASSISTANT_MESSAGE", "RUN_COMPLETED"]
