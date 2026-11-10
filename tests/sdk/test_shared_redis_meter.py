"""Real Redis checks for the shared admission primitive (not request-path acceptance)."""
import asyncio
import uuid

import pytest
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest.fixture
async def shared_meter():
    url = SharedSettings().redis_url
    if not url:
        pytest.skip("REDIS_URL is required")
    redis = Redis.from_url(url, decode_responses=True)
    domain = "test-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    await redis.hset(prefix + "guard", mapping={"epoch": "epoch-1"})
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch-1")
    try:
        yield redis, meter, prefix, domain
    finally:
        keys = [key async for key in redis.scan_iter(prefix + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


def policy(revision=1, rules=None):
    return SharedEgressPolicy.model_validate({"revision": revision, "rules": rules or [{
        "id": "route", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 5,
    }]})


async def acquire(meter, rules, attempt):
    return await meter.admit(rules, target_instance_id="platform", route_id="GET /assets",
                             attempt_id=attempt, lease_seconds=30)


async def test_shared_counter_is_atomic_across_clients(shared_meter):
    redis, first, _, domain = shared_meter
    second = SharedRedisMeter(redis, domain=domain, epoch="epoch-1")
    config = policy()
    await first.publish(config)

    async def run(index):
        try:
            return await acquire(first if index % 2 else second, config, str(index))
        except SharedLimitExceeded:
            return None

    permits = await asyncio.gather(*(run(i) for i in range(20)))
    assert sum(item is not None for item in permits) == 5


async def test_s_01(shared_meter):
    """S-01: two SDK clients share one atomic Redis counter."""
    await test_shared_counter_is_atomic_across_clients(shared_meter)


async def test_multi_rule_rejection_does_not_increment_other_rule(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy(rules=[
        {"id": "a", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 1},
        {"id": "b", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 5},
    ])
    await meter.publish(config)
    await acquire(meter, config, "first")
    with pytest.raises(SharedLimitExceeded, match="total"):
        await acquire(meter, config, "second")
    assert await redis.get(meter._bucket("b", "total")) == "1"


async def test_s_02(shared_meter):
    """S-02: all matching rules reserve together or none is changed."""
    await test_multi_rule_rejection_does_not_increment_other_rule(shared_meter)


async def test_revision_downgrade_and_missing_state_fail_closed(shared_meter):
    redis, meter, _, _ = shared_meter
    first = policy()
    await meter.publish(first)
    await acquire(meter, first, "first")
    changed = policy(revision=2, rules=[{
        "id": "route", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 1,
    }])
    await meter.publish(changed)
    with pytest.raises(MeteringUnavailable):
        await acquire(meter, first, "old-policy")
    with pytest.raises(SharedLimitExceeded):
        await acquire(meter, changed, "new-policy")
    await redis.delete(meter._bucket("route", "total"))
    with pytest.raises(MeteringUnavailable):
        await acquire(meter, changed, "missing-counter")


async def test_b_04(shared_meter):
    """B-04: current policy revision is authoritative and counters are retained."""
    await test_revision_downgrade_and_missing_state_fail_closed(shared_meter)


async def test_concurrency_release_is_idempotent_and_does_not_refund_total(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy(rules=[{
        "id": "route", "target_instance_id": "platform", "route_id": "GET /assets",
        "max_requests": 2, "max_inflight": 1,
    }])
    await meter.publish(config)
    permit = await acquire(meter, config, "first")
    with pytest.raises(SharedLimitExceeded, match="inflight"):
        await acquire(meter, config, "second")
    await meter.release(permit)
    await meter.release(permit)
    await acquire(meter, config, "second")
    with pytest.raises(SharedLimitExceeded, match="total"):
        await acquire(meter, config, "third")


async def test_new_window_waits_for_complete_history_and_epoch_must_match(shared_meter):
    redis, meter, prefix, _ = shared_meter
    config = policy(rules=[{
        "id": "window", "target_instance_id": "platform", "route_id": "GET /assets",
        "window_requests": 3, "window_seconds": 60.0,
    }])
    await meter.publish(config)
    with pytest.raises(SharedLimitExceeded, match="window"):
        await acquire(meter, config, "first")
    await redis.hset(prefix + "guard", "epoch", "different")
    with pytest.raises(MeteringUnavailable):
        await acquire(meter, config, "second")


async def test_b_01(shared_meter):
    """B-01: a newly introduced sliding window blocks until history is complete."""
    await test_new_window_waits_for_complete_history_and_epoch_must_match(shared_meter)


async def test_lost_decision_ledger_cannot_be_silently_reinitialized(shared_meter):
    redis, meter, prefix, _ = shared_meter
    config = policy()
    await meter.publish(config)
    await acquire(meter, config, "first")
    await redis.delete(prefix + "decisions")
    with pytest.raises(MeteringUnavailable):
        await acquire(meter, config, "second")
    with pytest.raises(MeteringUnavailable):
        await meter.publish(policy(revision=2))


async def test_same_attempt_retry_returns_the_original_permit_without_double_counting(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    first = await acquire(meter, config, "retryable-attempt")
    second = await acquire(meter, config, "retryable-attempt")
    assert second == first
    assert await redis.get(meter._bucket("route", "total")) == "1"


async def test_same_attempt_with_different_proof_is_rejected(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    first = await acquire(meter, config, "conflicting-attempt")
    conflict = first.__class__(first.attempt_id, "different-proof", first.expires_at_ms, first.rule_ids)
    with pytest.raises(MeteringUnavailable):
        await meter.release(conflict)


async def test_reused_attempt_cannot_change_target_or_lease(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    await acquire(meter, config, "same-attempt")
    with pytest.raises(MeteringUnavailable):
        await meter.admit(config, target_instance_id="other", route_id="GET /assets",
                          attempt_id="same-attempt", lease_seconds=30)
    with pytest.raises(MeteringUnavailable):
        await meter.admit(config, target_instance_id="platform", route_id="GET /assets",
                          attempt_id="same-attempt", lease_seconds=60)


async def test_permit_can_only_be_consumed_once_and_not_after_release(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    first = await acquire(meter, config, "consume-first")
    retry = await acquire(meter, config, "consume-first")
    await meter.consume(first)
    with pytest.raises(MeteringUnavailable):
        await meter.consume(retry)
    second = await acquire(meter, config, "released-first")
    await meter.release(second)
    with pytest.raises(MeteringUnavailable):
        await meter.consume(second)
