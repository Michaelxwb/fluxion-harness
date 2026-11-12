"""Plan-only collection skeletons. Replace before executing acceptance.

Never treat PLAN_SKELETON failures as functional RED evidence.
"""

import pytest

from tests.sdk import test_shared_http_transport as stream_cases
from tests.sdk import test_shared_redis_meter as meter_cases
from tests.sdk.test_shared_http_transport import fault_proxy as fault_proxy
from tests.sdk.test_shared_http_transport import http_probe as http_probe
from tests.sdk.test_shared_http_transport import meter as meter
from tests.sdk.test_shared_redis_meter import shared_meter as shared_meter


def test_s_01() -> None:
    """跨进程共享接口上限: 两进程合计最多50次获准并到达HTTP探针；所有KEY同slot，无CROSSSLOT."""
    pytest.fail("PLAN_SKELETON: implement real S-01 boundary assertions before execution")


@pytest.mark.integration
async def test_s_02() -> None:
    """真实 Redis 中执行、脚本和公共规则共同扣减，拒绝不产生部分扣减。"""
    import asyncio
    import os
    import uuid

    from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
    from muad_platform_sdk.shared_redis import SharedLimitExceeded, SharedRedisMeter
    from redis.asyncio import RedisCluster

    url = os.environ.get("MUAD_CLUSTER_REDIS_URL")
    if not url:
        pytest.fail("env_blocked: MUAD_CLUSTER_REDIS_URL must point to a real Redis Cluster")
    client = RedisCluster.from_url(url, decode_responses=True)
    domain = "egress-parent-child-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    first = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
    second = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
    policy = SharedEgressPolicy.model_validate(
        {
            "revision": 1,
            "rules": [
                {
                    "id": "route",
                    "target_instance_id": "platform",
                    "route_id": "GET /assets",
                    "max_requests": 2,
                }
            ],
        }
    )
    try:
        await client.hset(prefix + "guard", mapping={"epoch": "test-epoch"})
        await first.publish(policy)
        await first.register_execution(
            "execution-1",
            max_requests=3,
            targets=(policy.rules[0].model_copy(update={"id": "frozen-route", "max_requests": 3}),),
        )
        for invocation in ("a", "b", "c", "d"):
            await first.register_invocation("execution-1", invocation, max_requests=1)

        async def acquire(meter, invocation, attempt, *, segment="segment-1", current=policy):
            return await meter.admit(
                current,
                target_instance_id="platform",
                route_id="GET /assets",
                attempt_id=attempt,
                lease_seconds=30,
                invocation=TrustedInvocation("execution-1", invocation, segment),
            )

        permits = await asyncio.gather(acquire(first, "a", "a-1"), acquire(second, "b", "b-1"))
        assert all(permit is not None for permit in permits)
        with pytest.raises(SharedLimitExceeded):
            await acquire(first, "c", "public-full")

        larger = policy.model_copy(
            update={"revision": 2, "rules": (policy.rules[0].model_copy(update={"max_requests": 4}),)}
        )
        await first.publish(larger)
        with pytest.raises(SharedLimitExceeded):
            await acquire(first, "a", "invocation-full", current=larger)
        assert await acquire(second, "c", "c-1", segment="segment-2", current=larger) is not None
        with pytest.raises(SharedLimitExceeded):
            await acquire(first, "d", "execution-full", current=larger)

        # A rejected request must not consume the remaining public rule capacity.
        await first.register_execution("execution-2", max_requests=1)
        await first.register_invocation("execution-2", "e", max_requests=1)
        permit = await second.admit(
            larger,
            target_instance_id="platform",
            route_id="GET /assets",
            attempt_id="other-execution",
            lease_seconds=30,
            invocation=TrustedInvocation("execution-2", "e", "segment-1"),
        )
        assert permit is not None
    finally:
        try:
            async for key in client.scan_iter(match=prefix + "*"):
                await client.delete(key)
        finally:
            await client.aclose()


def test_s_03() -> None:
    """Real Runtime recovery and Worker reclaim preserve shared counters."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/sdk/test_runtime_egress_recovery_linux.py",
            "tests/sdk/test_worker_egress_recovery_linux.py",
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout


@pytest.mark.integration
async def test_s_04() -> None:
    """Controlled Linux wheel/system-site/bwrap startup, not a host import smoke."""
    import asyncio
    import os
    import sys

    if not os.environ.get("EGRESS_SITE_TEST_IMAGE") or not os.environ.get("EGRESS_SITE_TEST_REDIS_URL"):
        pytest.fail("env_blocked: provision controlled Linux image and its isolated Redis URL")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/sdk/test_egress_site_linux.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(120):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, output.decode(errors="replace")
    assert b"1 passed" in output, output.decode(errors="replace")


@pytest.mark.integration
async def test_s_05() -> None:
    """Real MSS auth/validation/retry and MCP initialize/notification/tool + Redis."""
    await _run_host_cases(
        "test_s_05_mss_auth_validate_business_and_retry_count_every_send",
        "test_s_05_mcp_initialize_notification_tool_share_counter",
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    [
        "test_lost_redis_reply_never_sends_and_same_attempt_retry_does_not_recount",
        "test_admission_deadline_is_dependency_failure_not_http_timeout",
    ],
)
async def test_e_01(case, meter, http_probe, fault_proxy) -> None:
    """Real Redis TCP reply loss and real HTTP zero-send boundary."""
    await getattr(stream_cases, case)(meter, http_probe, fault_proxy)


@pytest.mark.integration
async def test_e_01_connection_refused() -> None:
    """Redis 网络故障时不外发，恢复后同 attempt 只产生一次许可。"""
    import os
    import uuid

    from muad_contracts.shared_egress import SharedEgressPolicy
    from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedRedisMeter
    from redis.asyncio import Redis

    url = os.environ.get("EGRESS_FAULT_REDIS_URL", "redis://127.0.0.1:16499/0")
    client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.2)
    meter = SharedRedisMeter(client, domain="fault-" + uuid.uuid4().hex, epoch="epoch")
    policy = SharedEgressPolicy.model_validate(
        {
            "revision": 1,
            "rules": [
                {
                    "id": "route",
                    "target_instance_id": "platform",
                    "route_id": "GET /assets",
                    "max_requests": 1,
                }
            ],
        }
    )
    with pytest.raises(MeteringUnavailable):
        await meter.admit(
            policy,
            target_instance_id="platform",
            route_id="GET /assets",
            attempt_id="faulted",
            lease_seconds=1,
        )
    await client.aclose()


def test_e_02() -> None:
    """Real Runtime and Worker Linux process trees, PG, Redis and slow HTTP."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/sdk/test_runtime_egress_linux.py",
            "tests/sdk/test_worker_egress_linux.py",
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout


@pytest.mark.integration
async def test_e_03() -> None:
    """Real Proxy 429 status/headers/body, UPSTREAM marker and no counter refund."""
    await _run_host_cases("test_e_03_proxy_response_preserved_and_sent_attempt_not_refunded")


@pytest.mark.integration
async def test_e_04() -> None:
    """Lightweight PG/spool evidence; project-wide logging/time checks remain deferred."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/agent_runtime/test_egress_observability.py",
            "tests/agent_runtime/test_egress_observability_store.py",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7 passed" in result.stdout, result.stdout


def test_b_01() -> None:
    """连续窗口边界及时钟回拨: 有效区间为(now-window,now]，不存在跨整分放行100次；回拨不提前释放窗口."""
    pytest.fail("PLAN_SKELETON: implement real B-01 boundary assertions before execution")


@pytest.mark.integration
async def test_b_02() -> None:
    """Real requests sends, redirects and explicit retries through private IPC."""
    import asyncio
    import os
    import sys

    if not os.environ.get("REDIS_URL"):
        pytest.fail("env_blocked: REDIS_URL must point to isolated Redis")
    path = "tests/sdk/test_requests_egress_hook.py"
    names = [
        "test_redirects_are_counted_individually_and_auth_headers_preserved",
        "test_explicit_retries_each_acquire_a_new_permit",
        "test_implicit_retries_are_explicitly_rejected_without_sending",
        "test_unauthorized_redirect_is_rejected_before_second_send",
        "test_custom_adapter_is_rejected_before_its_send",
    ]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *(f"{path}::{name}" for name in names),
        "tests/sdk/test_requests_control_lifecycle.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(120):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, output.decode(errors="replace")
    assert b"17 passed" in output, output.decode(errors="replace")


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    [
        "test_stream_holds_lease_until_close_and_preserves_proxy_response",
        "test_deadline_covers_body_read_and_closes_real_socket",
        "test_external_cancellation_closes_stream_and_keeps_cancelled_error",
        "test_renewal_epoch_failure_closes_stream_as_dependency_error",
        "test_renewal_network_timeout_closes_active_stream",
        "lease_release_key_set",
        "lease_renew_key_set",
    ],
)
async def test_b_03(case, meter, http_probe, fault_proxy, shared_meter) -> None:
    """Real stream/deadline/renewal closure plus ownership-bound lease release."""
    if case.startswith("lease_"):
        operation = "release" if case == "lease_release_key_set" else "renew"
        await meter_cases.test_lease_operation_rejects_incomplete_key_set(shared_meter, operation)
    elif case == "test_renewal_network_timeout_closes_active_stream":
        await getattr(stream_cases, case)(meter, http_probe, fault_proxy)
    else:
        await getattr(stream_cases, case)(meter, http_probe)


def test_b_04() -> None:
    """公共规则版本一致；旧缓存先拒后刷新，新窗口立即计数，冻结累计不变。"""
    pytest.fail("PLAN_SKELETON: implement real B-04 boundary assertions before execution")


@pytest.mark.integration
async def test_b_05() -> None:
    """Run the real PG/Cluster composition; missing opt-in is an environment failure."""
    import asyncio
    import os
    import sys

    if os.environ.get("EGRESS_ALLOW_CLUSTER_FAILOVER") != "1":
        pytest.fail("env_blocked: B-05 requires an explicitly disposable Redis Cluster")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/sdk/test_persistent_egress_failover.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(120):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, output.decode(errors="replace")
    assert b"2 passed" in output, output.decode(errors="replace")


@pytest.mark.integration
async def test_b_05_redis_state_probe() -> None:
    """恢复时缺少 scope 或 epoch 不确定必须阻断，不能从零恢复。"""
    import os

    from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedRedisMeter
    from redis.asyncio import Redis

    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.fail("env_blocked: REDIS_URL is required")
    client = Redis.from_url(url, decode_responses=True)
    meter = SharedRedisMeter(client, domain="recover-b05", epoch="epoch")
    try:
        with pytest.raises(MeteringUnavailable):
            await meter.resume_execution("missing", max_requests=1)
        await client.hset(meter._key("guard"), mapping={"epoch": "other"})
        with pytest.raises(MeteringUnavailable):
            await meter.register_execution("new", max_requests=1)
    finally:
        await client.aclose()


def test_b_06() -> None:
    """无规则不访问Redis；非法配置拒绝，未知目标和未支持客户端明确区分。"""
    pytest.fail("PLAN_SKELETON: implement real B-06 boundary assertions before execution")


async def _run_host_cases(*names: str) -> None:
    import asyncio

    nodes = ["tests/sdk/test_shared_host_transport.py::" + name for name in names]
    process = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "pytest",
        "-q",
        *nodes,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = b""
    try:
        async with asyncio.timeout(120):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, output.decode(errors="replace")
    assert f"{len(names)} passed".encode() in output, output.decode(errors="replace")
