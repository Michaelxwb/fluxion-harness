"""Real Redis checks for the shared admission primitive (not request-path acceptance)."""

import asyncio
import uuid

import pytest
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy, SharedRule, TrustedInvocation
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
    return SharedEgressPolicy.model_validate(
        {
            "revision": revision,
            "rules": rules
            or [
                {
                    "id": "route",
                    "target_instance_id": "platform",
                    "route_id": "GET /assets",
                    "max_requests": 5,
                }
            ],
        }
    )


async def acquire(meter, rules, attempt):
    return await meter.admit(
        rules, target_instance_id="platform", route_id="GET /assets", attempt_id=attempt, lease_seconds=30
    )


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
    config = policy(
        rules=[
            {"id": "a", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 1},
            {"id": "b", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 5},
        ]
    )
    await meter.publish(config)
    await acquire(meter, config, "first")
    with pytest.raises(SharedLimitExceeded, match="total"):
        await acquire(meter, config, "second")
    assert await redis.get(meter._bucket("b", "total")) == "1"


async def test_non_integer_counter_rejected_before_any_bucket_is_changed(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {"id": "a", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 5},
            {"id": "b", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 5},
        ]
    )
    await meter.publish(config)
    await redis.set(meter._bucket("b", "total"), "1.0")
    with pytest.raises(MeteringUnavailable):
        await acquire(meter, config, "corrupt-counter")
    assert await redis.get(meter._bucket("a", "total")) == "0"


async def test_s_02(shared_meter):
    """S-02: all matching rules reserve together or none is changed."""
    await test_multi_rule_rejection_does_not_increment_other_rule(shared_meter)


async def test_revision_downgrade_and_missing_state_fail_closed(shared_meter):
    redis, meter, _, _ = shared_meter
    first = policy()
    await meter.publish(first)
    await acquire(meter, first, "first")
    changed = policy(
        revision=2,
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_requests": 1,
            }
        ],
    )
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
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_requests": 2,
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "first")
    with pytest.raises(SharedLimitExceeded, match="inflight"):
        await acquire(meter, config, "second")
    await meter.release(permit)
    await meter.release(permit)
    await acquire(meter, config, "second")
    with pytest.raises(SharedLimitExceeded, match="total"):
        await acquire(meter, config, "third")


async def test_new_window_starts_counting_immediately_and_epoch_must_match(shared_meter):
    redis, meter, prefix, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "window",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "window_requests": 3,
                "window_seconds": 60.0,
            }
        ]
    )
    await meter.publish(config)
    assert await acquire(meter, config, "first") is not None
    await redis.hset(prefix + "guard", "epoch", "different")
    with pytest.raises(MeteringUnavailable):
        await acquire(meter, config, "second")


async def test_b_01(shared_meter):
    """B-01: a newly introduced sliding window starts at its activation boundary."""
    await test_new_window_starts_counting_immediately_and_epoch_must_match(shared_meter)


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
        await meter.admit(
            config,
            target_instance_id="other",
            route_id="GET /assets",
            attempt_id="same-attempt",
            lease_seconds=30,
        )
    with pytest.raises(MeteringUnavailable):
        await meter.admit(
            config,
            target_instance_id="platform",
            route_id="GET /assets",
            attempt_id="same-attempt",
            lease_seconds=60,
        )


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


async def test_frozen_scope_retry_reuses_count_and_rejects_changed_snapshot(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=2)
    await meter.register_invocation("execution", "script", max_requests=1)
    identity = TrustedInvocation("execution", "script", "segment-1")
    args = dict(
        target_instance_id="platform",
        route_id="GET /assets",
        attempt_id="once",
        lease_seconds=30,
        invocation=identity,
    )
    first = await meter.admit(config, **args)
    assert await meter.admit(config, **args) == first
    with pytest.raises(MeteringUnavailable):
        await meter.register_execution("execution", max_requests=3)
    await meter.consume(first)
    with pytest.raises(MeteringUnavailable):
        await meter.consume(first)
    with pytest.raises(SharedLimitExceeded):
        await meter.admit(config, **{**args, "attempt_id": "next"})


async def test_frozen_scope_missing_state_fails_closed_without_reinitializing(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=2)
    await meter.register_invocation("execution", "script", max_requests=2)
    identity = TrustedInvocation("execution", "script", "segment-1")
    args = dict(
        target_instance_id="platform",
        route_id="GET /assets",
        attempt_id="once",
        lease_seconds=30,
        invocation=identity,
    )
    await meter.admit(config, **args)
    await redis.delete(meter._scope("execution", "execution")[2])
    with pytest.raises(MeteringUnavailable):
        await meter.admit(config, **args)
    with pytest.raises(MeteringUnavailable):
        await meter.register_execution("execution", max_requests=2)


async def test_frozen_scope_modified_limit_fails_closed(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=2)
    await meter.register_invocation("execution", "script", max_requests=1)
    identity = TrustedInvocation("execution", "script", "segment-1")
    await redis.hset(meter._scope("invocation", "execution", "script")[2], "limit", "100")
    with pytest.raises(MeteringUnavailable):
        await meter.admit(
            config,
            target_instance_id="platform",
            route_id="GET /assets",
            attempt_id="modified",
            lease_seconds=30,
            invocation=identity,
        )


async def test_frozen_budget_without_public_rules_still_counts(shared_meter):
    _, meter, _, _ = shared_meter
    config = SharedEgressPolicy(revision=1, rules=())
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=2)
    await meter.register_invocation("execution", "script", max_requests=1)
    identity = TrustedInvocation("execution", "script", "segment-1")
    args = dict(target_instance_id="platform", route_id="GET /assets", lease_seconds=30, invocation=identity)
    assert await meter.admit(config, attempt_id="one", **args) is not None
    with pytest.raises(SharedLimitExceeded):
        await meter.admit(config, attempt_id="two", **args)


@pytest.mark.parametrize("execution_limit,invocation_limit", [(2, None), (None, 2)])
async def test_execution_and_invocation_limits_can_be_enabled_independently(
    shared_meter,
    execution_limit,
    invocation_limit,
):
    _, meter, _, _ = shared_meter
    config = SharedEgressPolicy(revision=1, rules=())
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=execution_limit)
    await meter.register_invocation("execution", "script", max_requests=invocation_limit)
    identity = TrustedInvocation("execution", "script", "segment-1")
    args = dict(target_instance_id="platform", route_id="GET /assets", lease_seconds=30, invocation=identity)
    for number in range(2):
        assert await meter.admit(config, attempt_id=f"allowed-{number}", **args) is not None
    with pytest.raises(SharedLimitExceeded):
        await meter.admit(config, attempt_id="rejected", **args)


async def test_disabled_execution_limit_still_keeps_invocations_separate(shared_meter):
    _, meter, _, _ = shared_meter
    config = SharedEgressPolicy(revision=1, rules=())
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=None)
    for script in ("a", "b"):
        await meter.register_invocation("execution", script, max_requests=1)
        identity = TrustedInvocation("execution", script, "segment-1")
        assert (
            await meter.admit(
                config,
                target_instance_id="platform",
                route_id="GET /assets",
                lease_seconds=30,
                attempt_id=script,
                invocation=identity,
            )
            is not None
        )
    with pytest.raises(MeteringUnavailable):
        await meter.register_execution("execution", max_requests=2)


async def test_unlimited_scope_does_not_increment_its_counter(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=None)
    await meter.register_invocation("execution", "script", max_requests=2)
    identity = TrustedInvocation("execution", "script", "segment-1")
    await meter.admit(
        config,
        target_instance_id="platform",
        route_id="GET /assets",
        lease_seconds=30,
        attempt_id="one",
        invocation=identity,
    )
    assert await redis.hget(meter._scope("execution", "execution")[2], "used") == "0"


async def test_resume_keeps_consumed_budget_in_a_new_client(shared_meter):
    redis, meter, _, domain = shared_meter
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=2)
    await meter.register_invocation("execution", "script", max_requests=2)
    identity = TrustedInvocation("execution", "script", "segment-1")
    await meter.admit(
        config,
        target_instance_id="platform",
        route_id="GET /assets",
        lease_seconds=30,
        attempt_id="first",
        invocation=identity,
    )
    restored = SharedRedisMeter(redis, domain=domain, epoch="epoch-1")
    await restored.resume_execution("execution", max_requests=2)
    await restored.resume_invocation("execution", "script", max_requests=2)
    args = dict(
        target_instance_id="platform",
        route_id="GET /assets",
        lease_seconds=30,
        invocation=identity.with_segment("segment-2"),
    )
    assert await restored.admit(config, attempt_id="second", **args) is not None
    with pytest.raises(SharedLimitExceeded):
        await restored.admit(config, attempt_id="third", **args)


async def test_resume_never_initializes_missing_scope(shared_meter):
    redis, meter, prefix, _ = shared_meter
    await meter.publish(policy())
    with pytest.raises(MeteringUnavailable):
        await meter.resume_execution("missing", max_requests=2)
    assert not await redis.exists(prefix + "scope-index")
    await meter.register_execution("execution", max_requests=2)
    await meter.register_invocation("execution", "script", max_requests=2)
    name, _, key = meter._scope("invocation", "execution", "script")
    await redis.delete(key)
    await redis.hdel(prefix + "scope-index", name, "limit:" + name)
    with pytest.raises(MeteringUnavailable):
        await meter.resume_invocation("execution", "script", max_requests=2)
    assert not await redis.exists(key)


async def test_resume_rejects_changed_limit_and_malformed_counter(shared_meter):
    redis, meter, _, _ = shared_meter
    await meter.publish(policy())
    await meter.register_execution("execution", max_requests=2)
    with pytest.raises(MeteringUnavailable):
        await meter.resume_execution("execution", max_requests=3)
    key = meter._scope("execution", "execution")[2]
    await redis.hset(key, "used", "1.5")
    with pytest.raises(MeteringUnavailable):
        await meter.resume_execution("execution", max_requests=2)
    assert await redis.hget(key, "used") == "1.5"


async def test_frozen_target_limit_rejects_without_consuming_public_budget(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy()
    target = SharedRule(id="assets", target_instance_id="platform", route_id="GET /assets", max_requests=1)
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=None, targets=(target,))
    await meter.register_invocation("execution", "script", max_requests=None)
    identity = TrustedInvocation("execution", "script", "segment")
    args = dict(target_instance_id="platform", route_id="GET /assets", lease_seconds=30, invocation=identity)
    assert await meter.admit(config, attempt_id="first", **args) is not None
    with pytest.raises(SharedLimitExceeded):
        await meter.admit(config, attempt_id="second", **args)
    assert await redis.get(meter._bucket("route", "total")) == "1"
    assert await meter.admit(config, attempt_id="other-route", **{**args, "route_id": "GET /other"})


async def test_frozen_invocation_targets_are_independent_and_immutable(shared_meter):
    _, meter, _, _ = shared_meter
    target = SharedRule(id="assets", target_instance_id="platform", route_id="GET /assets", max_requests=1)
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=None)
    for script in ("a", "b"):
        await meter.register_invocation("execution", script, max_requests=None, targets=(target,))
        args = dict(
            target_instance_id="platform",
            route_id="GET /assets",
            lease_seconds=30,
            invocation=TrustedInvocation("execution", script, "segment"),
        )
        assert await meter.admit(config, attempt_id=f"{script}-first", **args)
        with pytest.raises(SharedLimitExceeded):
            await meter.admit(config, attempt_id=f"{script}-second", **args)
    with pytest.raises(MeteringUnavailable):
        await meter.resume_invocation("execution", "a", max_requests=None, targets=())


async def test_missing_frozen_target_counter_blocks_resume_and_admission(shared_meter):
    redis, meter, _, _ = shared_meter
    target = SharedRule(id="assets", target_instance_id="platform", route_id="GET /assets", max_requests=1)
    config = policy()
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=None, targets=(target,))
    await meter.register_invocation("execution", "script", max_requests=None)
    await redis.hdel(meter._scope("execution", "execution")[2], "target:assets")
    with pytest.raises(MeteringUnavailable):
        await meter.resume_execution("execution", max_requests=None, targets=(target,))
    with pytest.raises(MeteringUnavailable):
        await meter.admit(
            config,
            target_instance_id="platform",
            route_id="GET /assets",
            lease_seconds=30,
            attempt_id="missing",
            invocation=TrustedInvocation("execution", "script", "segment"),
        )
    assert await redis.get(meter._bucket("route", "total")) == "0"


async def test_lease_renewal_extends_inflight_and_rejects_wrong_or_expired_token(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "stream",
                "target_instance_id": "platform",
                "route_id": "GET /stream",
                "max_requests": 2,
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    first = await meter.admit(
        config,
        target_instance_id="platform",
        route_id="GET /stream",
        attempt_id="renew-first",
        lease_seconds=30,
    )
    await meter.consume(first)
    await meter.renew(first, lease_seconds=30)
    with pytest.raises(SharedLimitExceeded, match="inflight"):
        await meter.admit(
            config,
            target_instance_id="platform",
            route_id="GET /stream",
            attempt_id="renew-second",
            lease_seconds=30,
        )
    wrong = first.__class__(first.attempt_id, "wrong-proof", first.expires_at_ms, first.rule_ids)
    with pytest.raises(MeteringUnavailable):
        await meter.renew(wrong, lease_seconds=30)
    await meter.release(first)
    with pytest.raises(MeteringUnavailable):
        await meter.renew(first, lease_seconds=30)


async def test_renewal_requires_consumed_permit(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "not-sent")
    with pytest.raises(MeteringUnavailable):
        await meter.renew(permit, lease_seconds=30)


async def test_renewal_rejects_changed_epoch_without_mutation(shared_meter):
    redis, meter, prefix, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "old-owner")
    await meter.consume(permit)
    key = meter._bucket("route", "leases")
    before = await redis.zscore(key, permit.attempt_id)
    await redis.hset(prefix + "guard", "epoch", "epoch-2")
    with pytest.raises(MeteringUnavailable):
        await meter.renew(permit, lease_seconds=60)
    assert await redis.zscore(key, permit.attempt_id) == before


async def test_renewal_handles_mixed_rules_without_extending_send_permit(shared_meter):
    redis, meter, prefix, _ = shared_meter
    config = policy(
        rules=[
            {"id": "total", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 3},
            {
                "id": "concurrency",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            },
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "mixed")
    await meter.consume(permit)
    expiry = await redis.hget(prefix + "decisions", "__expiry:mixed")
    await meter.renew(permit, lease_seconds=60)
    assert await redis.hget(prefix + "decisions", "__expiry:mixed") == expiry
    assert await redis.zscore(meter._bucket("concurrency", "leases"), "mixed") > int(expiry)
    assert await redis.zscore(meter._bucket("total", "leases"), "mixed") is None


async def test_renewal_does_not_shorten_lease(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "long")
    await meter.consume(permit)
    key = meter._bucket("route", "leases")
    before = await redis.zscore(key, permit.attempt_id)
    await meter.renew(permit, lease_seconds=1)
    assert await redis.zscore(key, permit.attempt_id) >= before


async def test_renewed_lease_survives_original_expiry(shared_meter):
    _, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    permit = await meter.admit(
        config, target_instance_id="platform", route_id="GET /assets", attempt_id="short", lease_seconds=0.5
    )
    await meter.consume(permit)
    await meter.renew(permit, lease_seconds=5)
    await asyncio.sleep(0.6)
    with pytest.raises(SharedLimitExceeded, match="inflight"):
        await acquire(meter, config, "still-blocked")
    with pytest.raises(MeteringUnavailable):
        await meter.consume(permit)
    await meter.release(permit)
    assert await acquire(meter, config, "after-close") is not None


async def test_expired_lease_cannot_be_resurrected(shared_meter):
    redis, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    permit = await meter.admit(
        config, target_instance_id="platform", route_id="GET /assets", attempt_id="expired", lease_seconds=0.5
    )
    await meter.consume(permit)
    key = meter._bucket("route", "leases")
    before = await redis.zscore(key, permit.attempt_id)
    await asyncio.sleep(0.6)
    with pytest.raises(MeteringUnavailable):
        await meter.renew(permit, lease_seconds=30)
    assert await redis.zscore(key, permit.attempt_id) == before


@pytest.mark.parametrize("operation", ["release", "renew"])
async def test_lease_operation_rejects_incomplete_key_set(shared_meter, operation):
    redis, meter, _, _ = shared_meter
    config = policy(
        rules=[
            {"id": name, "target_instance_id": "platform", "route_id": "GET /assets", "max_inflight": 1}
            for name in ("a", "b")
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "two-leases")
    await meter.consume(permit)
    forged = permit.__class__(permit.attempt_id, permit.proof, permit.expires_at_ms, ("a",))
    before = [await redis.zscore(meter._bucket(name, "leases"), permit.attempt_id) for name in ("a", "b")]
    with pytest.raises(MeteringUnavailable):
        if operation == "renew":
            await meter.renew(forged, lease_seconds=60)
        else:
            await meter.release(forged)
    assert [
        await redis.zscore(meter._bucket(name, "leases"), permit.attempt_id) for name in ("a", "b")
    ] == before


async def test_corrupt_second_lease_does_not_partially_renew_first(shared_meter):
    redis, meter, prefix, _ = shared_meter
    config = policy(
        rules=[
            {"id": name, "target_instance_id": "platform", "route_id": "GET /assets", "max_inflight": 1}
            for name in ("a", "b")
        ]
    )
    await meter.publish(config)
    permit = await acquire(meter, config, "corrupt-second")
    await meter.consume(permit)
    first, second = (meter._bucket(name, "leases") for name in ("a", "b"))
    before = await redis.zscore(first, permit.attempt_id)
    guard = await redis.hget(prefix + "guard", "last_ms")
    await redis.delete(second)
    await redis.set(second, "wrong-type")
    with pytest.raises(MeteringUnavailable):
        await meter.renew(permit, lease_seconds=60)
    assert await redis.zscore(first, permit.attempt_id) == before
    assert await redis.hget(prefix + "guard", "last_ms") == guard


@pytest.mark.parametrize("operation", ["admit", "consume", "renew", "publish", "resume"])
async def test_missing_server_generation_cannot_be_relearned(shared_meter, operation):
    redis, meter, prefix, _ = shared_meter
    config = policy(
        rules=[
            {
                "id": "route",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
                "max_inflight": 1,
            }
        ]
    )
    await meter.publish(config)
    await meter.register_execution("execution", max_requests=3)
    permit = await acquire(meter, config, "guard-missing")
    if operation == "renew":
        await meter.consume(permit)
    await redis.hdel(prefix + "guard", "server_generation")
    before = await redis.hgetall(prefix + "guard")
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        if operation == "admit":
            await acquire(meter, config, "new-attempt")
        elif operation == "consume":
            await meter.consume(permit)
        elif operation == "renew":
            await meter.renew(permit, lease_seconds=60)
        elif operation == "publish":
            await meter.publish(config)
        else:
            await meter.resume_execution("execution", max_requests=3)
    assert await redis.hgetall(prefix + "guard") == before


async def test_info_permission_denial_fails_closed_without_increment(shared_meter):
    redis, meter, _, domain = shared_meter
    config = policy()
    await meter.publish(config)
    username = "egress-info-test-" + uuid.uuid4().hex
    # This user has no password and only exists inside the disposable test Redis.
    await redis.execute_command("ACL", "SETUSER", username, "on", "nopass", "~*", "+@all", "-info")
    connection = redis.connection_pool.connection_kwargs
    denied = Redis(
        host=connection["host"],
        port=connection["port"],
        username=username,
        decode_responses=True,
        db=connection.get("db", 0),
    )
    restricted = SharedRedisMeter(denied, domain=domain, epoch="epoch-1")
    try:
        with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
            await acquire(restricted, config, "no-info-permission")
        assert await redis.get(meter._bucket("route", "total")) == "0"
    finally:
        await denied.aclose()
        await redis.execute_command("ACL", "DELUSER", username)
