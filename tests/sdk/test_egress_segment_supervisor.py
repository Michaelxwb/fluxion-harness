"""Supervisor state machine with real Redis; source authority is a test double.

Real source-row authorization is covered separately, not claimed by this probe.
"""
import asyncio
import importlib
import time
import uuid

import pytest
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
from muad_platform_sdk.egress_segments import FencedRedisMeter, SegmentGrant
from muad_platform_sdk.egress_source_authority import AuthorizedSegment
from muad_platform_sdk.shared_redis import MeteringUnavailable

from tests.sdk.test_shared_redis_meter import policy
from tests.sdk.test_shared_redis_meter import shared_meter as shared_meter_fixture

shared_meter = shared_meter_fixture
pytestmark = pytest.mark.integration


class Authority:
    def __init__(self, grant):
        self.grant, self.calls, self.block, self.lost, self.delay = grant, 0, False, False, 0.0

    async def authorize(self, execution_id, claim, reader):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.block and self.calls > 1:
            await asyncio.Event().wait()
        if self.lost:
            raise IdentityUnavailable("source execution lease lost")
        return AuthorizedSegment(self.grant, time.monotonic() + .4)


@pytest.fixture
async def environment(shared_meter):
    redis, base, _, domain = shared_meter
    execution_id = uuid.uuid4()
    await base.publish(policy())
    await base.register_execution(str(execution_id), max_requests=50)
    grant = SegmentGrant(str(execution_id), 1, "segment-one")
    meter = FencedRedisMeter(redis, domain=domain, epoch="epoch-1", grant=grant)
    authority, closed = Authority(grant), []
    async def close():
        closed.append("closed")
    module = importlib.import_module("muad_platform_sdk.egress_segment_supervisor")
    supervisor = module.SourceSegmentSupervisor(
        authority=authority, execution_id=execution_id, claim=object(), reader=object(),
        meter=meter, refresh_seconds=.05, stop_margin_seconds=.1, close_clients=close,
    )
    next_meter = FencedRedisMeter(redis, domain=domain, epoch=meter.epoch,
                                 grant=SegmentGrant(str(execution_id), 2, "next"))
    return supervisor, authority, next_meter, closed


async def test_renews_only_after_source_authorization_and_confirms_closed(environment):
    supervisor, authority, next_meter, closed = environment
    async def work():
        await asyncio.sleep(.13)
        return "upstream-result"
    assert await supervisor.run(work) == "upstream-result"
    assert authority.calls >= 2
    assert closed == ["closed"]
    # Explicit closure confirmation permits an immediate higher-fence takeover.
    await next_meter.activate(lease_seconds=1, stop_margin_seconds=.1)


@pytest.mark.parametrize("blocked", [False, True])
async def test_source_loss_or_hung_renewal_cancels_work_before_expiry(environment, blocked):
    supervisor, authority, _, closed = environment
    stopped = asyncio.Event()
    async def work():
        authority.block, authority.lost = blocked, not blocked
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    started = time.monotonic()
    with pytest.raises(IdentityUnavailable):
        await asyncio.wait_for(supervisor.run(work), 1)
    assert stopped.is_set() and closed == ["closed"]
    assert time.monotonic() - started < .7


async def test_initial_denial_does_not_start_business(environment):
    supervisor, authority, _, closed = environment
    authority.lost = True
    async def work():
        pytest.fail("business started without authority")
    with pytest.raises(IdentityUnavailable):
        await supervisor.run(work)
    assert closed == []


async def test_authorization_may_exceed_refresh_cadence_within_shutdown_margin(environment):
    supervisor, authority, _, closed = environment
    authority.delay = .07  # refresh cadence is .05; shutdown margin is .1.
    async def work():
        return "authorized"
    assert await supervisor.run(work) == "authorized"
    assert closed == ["closed"]


async def test_failed_close_never_confirms_segment_stopped(environment):
    supervisor, _, next_meter, _ = environment
    async def failed_close():
        raise RuntimeError("cannot confirm sockets closed")
    supervisor._close_clients = failed_close
    async def work():
        return None
    with pytest.raises(IdentityUnavailable, match="termination not confirmed"):
        await supervisor.run(work)
    with pytest.raises(MeteringUnavailable):
        await next_meter.activate(lease_seconds=1, stop_margin_seconds=.1)


async def test_cancellation_closes_clients_before_allowing_takeover(environment):
    supervisor, _, _, closed = environment
    started, stopped = asyncio.Event(), asyncio.Event()
    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(supervisor.run(work))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and closed == ["closed"]


async def test_close_receipt_unblocks_cancellation_resistant_work(environment):
    supervisor, authority, _, _ = environment
    closed, stopped = asyncio.Event(), asyncio.Event()
    async def close():
        closed.set()
    supervisor._close_clients = close
    async def work():
        authority.lost = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await closed.wait()  # An owned socket/group must be forcibly closed.
            stopped.set()
    with pytest.raises(IdentityUnavailable, match="source execution lease lost"):
        await asyncio.wait_for(supervisor.run(work), 1)
    assert closed.is_set() and stopped.is_set()


async def test_expired_source_cannot_report_success_when_watch_is_delayed(environment):
    supervisor, _, _, _ = environment
    async def delayed_watch():
        await asyncio.sleep(1)
    supervisor._watch = delayed_watch
    async def work():
        time.sleep(.35)  # Model a delayed event loop, not a production network call.
        return "late-success"
    with pytest.raises(IdentityUnavailable, match="stop deadline"):
        await supervisor.run(work)


async def test_stuck_close_is_bounded_and_does_not_allow_takeover(environment):
    supervisor, _, next_meter, _ = environment
    release = asyncio.Event()
    async def stuck_close():
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
    supervisor._close_clients = stuck_close
    async def work():
        return None
    try:
        with pytest.raises(IdentityUnavailable, match="termination not confirmed"):
            await asyncio.wait_for(supervisor.run(work), .7)
        with pytest.raises(MeteringUnavailable):
            await next_meter.activate(lease_seconds=1, stop_margin_seconds=.1)
    finally:
        release.set()
        await asyncio.sleep(0)


async def test_business_failure_is_preserved_and_supervisor_cannot_restart(environment):
    supervisor, _, _, closed = environment
    async def work():
        raise ValueError("original business failure")
    with pytest.raises(ValueError, match="original business failure"):
        await supervisor.run(work)
    assert closed == ["closed"]
    with pytest.raises(IdentityUnavailable, match="single-use"):
        await supervisor.run(work)
