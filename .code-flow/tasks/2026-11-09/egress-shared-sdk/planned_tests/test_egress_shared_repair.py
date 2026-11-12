"""Real Redis Cluster and subprocess probes for the shared egress repair."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter
from muad_platform_sdk.shared_redis_scripts import ADMIT
from redis.asyncio import RedisCluster
from redis.exceptions import RedisError


def _policy() -> SharedEgressPolicy:
    return SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "asset-window", "target_instance_id": "platform", "route_id": "GET /assets",
        "window_requests": 50, "window_seconds": 60,
    }]})


async def _worker(redis_url: str, domain: str, port: int, worker_id: int) -> None:
    client = RedisCluster.from_url(redis_url, decode_responses=True)
    meter = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
    try:
        for number in range(40):
            try:
                permit = await meter.admit(
                    _policy(), target_instance_id="platform", route_id="GET /assets",
                    attempt_id=f"{worker_id}-{number}", lease_seconds=30,
                )
            except SharedLimitExceeded:
                continue
            assert permit is not None
            await meter.consume(permit)
            with pytest.raises(MeteringUnavailable):
                await meter.consume(permit)
            await asyncio.to_thread(_send, port)
            await meter.release(permit)
    finally:
        await client.aclose()


async def _revision_worker(redis_url: str, domain: str, revision: int, limit: int,
                           count: int, worker_id: str, refresh: bool = False) -> None:
    client = RedisCluster.from_url(redis_url, decode_responses=True)
    meter = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
    policy = _revision_policy(revision, limit)
    allowed = limited = stale = 0
    try:
        await meter.resume_execution("execution-1", max_requests=60)
        await meter.resume_invocation("execution-1", "invocation-1", max_requests=60)
        for number in range(count):
            try:
                permit = await meter.admit(
                    policy, target_instance_id="platform", route_id="GET /assets",
                    attempt_id=f"{worker_id}-{number}", lease_seconds=30,
                    invocation=TrustedInvocation("execution-1", "invocation-1", f"segment-{worker_id}"),
                )
                assert permit is not None
                await meter.consume(permit)
                await meter.release(permit)
                allowed += 1
            except SharedLimitExceeded:
                limited += 1
            except MeteringUnavailable as exc:
                if "POLICY" not in str(exc):
                    raise
                stale += 1
                if refresh:
                    current = await client.hget(f"fx:egress:{{{domain}}}:policy", "json")
                    assert current is not None
                    policy = SharedEgressPolicy.model_validate_json(current)
        print(json.dumps({"allowed": allowed, "limited": limited, "stale": stale}))
    finally:
        await client.aclose()


def _revision_policy(revision: int, limit: int, *, window_seconds: int | None = None) -> SharedEgressPolicy:
    rule: dict[str, object] = {"id": "revision-rule", "target_instance_id": "platform",
                               "route_id": "GET /assets", "max_requests": limit}
    if window_seconds is not None:
        rule.update(window_requests=limit, window_seconds=window_seconds)
    return SharedEgressPolicy.model_validate({"revision": revision, "rules": [rule]})


async def _run_revision_worker(
    domain: str, revision: int, limit: int, count: int, name: str,
    refresh: bool = False,
) -> dict[str, int]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), "--revision-worker", str(revision),
        str(limit), str(count), name, *(["--refresh"] if refresh else []),
        env={**os.environ, "EGRESS_TEST_DOMAIN": domain},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        assert process.returncode == 0, stderr.decode(errors="replace")[-2000:]
        result = json.loads(stdout)
        assert isinstance(result, dict)
        return result
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def _send(port: int) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/assets", timeout=5) as response:
        assert response.status == 200


class _Probe(BaseHTTPRequestHandler):
    count = 0
    lock = threading.Lock()

    def do_GET(self) -> None:
        with self.lock:
            type(self).count += 1
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


class _IsolatedAdmissionClock:
    """Replace only the admission script's TIME call in a test-owned domain."""

    def __init__(self, client: RedisCluster, milliseconds: int) -> None:
        self.client = client
        self.milliseconds = milliseconds

    async def execute_command(self, *args: str | int) -> object:
        if args[0] == "EVAL" and args[1] == ADMIT:
            marker = "local now = redis.call('TIME')"
            assert ADMIT.count(marker) == 1
            seconds, milliseconds = divmod(self.milliseconds, 1000)
            clock = f"local now = {{'{seconds}', '{milliseconds * 1000}'}}"
            args = (args[0], ADMIT.replace(marker, clock, 1), *args[2:])
        return await self.client.execute_command(*args)


async def _check_controlled_window(redis_url: str) -> None:
    client = RedisCluster.from_url(redis_url, decode_responses=True)
    domain = "egress-controlled-window-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    try:
        await client.hset(prefix + "guard", mapping={"epoch": "test-epoch"})
        await SharedRedisMeter(client, domain=domain, epoch="test-epoch").publish(_policy())
        last_ms = int(await client.hget(prefix + "guard", "last_ms"))
        base = ((last_ms // 60_000) + 2) * 60_000 + 59_500
        clock = _IsolatedAdmissionClock(client, base)
        meter = SharedRedisMeter(clock, domain=domain, epoch="test-epoch")
        for number in range(50):
            assert await meter.admit(
                _policy(), target_instance_id="platform", route_id="GET /assets",
                attempt_id=f"controlled-{number}", lease_seconds=30,
            ) is not None
        for offset in (500, 59_999):
            clock.milliseconds = base + offset
            with pytest.raises(SharedLimitExceeded, match="window"):
                await meter.admit(
                    _policy(), target_instance_id="platform", route_id="GET /assets",
                    attempt_id=f"denied-{offset}", lease_seconds=30,
                )
        clock.milliseconds = base + 60_000
        assert await meter.admit(
            _policy(), target_instance_id="platform", route_id="GET /assets",
            attempt_id="exact-expiry", lease_seconds=30,
        ) is not None
        clock.milliseconds -= 1
        with pytest.raises(MeteringUnavailable, match="CLOCK"):
            await meter.admit(
                _policy(), target_instance_id="platform", route_id="GET /assets",
                attempt_id="controlled-backwards", lease_seconds=30,
            )
    finally:
        try:
            async for key in client.scan_iter(match=prefix + "*"):
                await client.delete(key)
        finally:
            await client.aclose()


@pytest.mark.integration
async def test_b_01_controlled_boundary() -> None:
    redis_url = os.environ.get("MUAD_CLUSTER_REDIS_URL")
    if not redis_url:
        pytest.fail("env_blocked: MUAD_CLUSTER_REDIS_URL must point to a real Redis Cluster")
    await _check_controlled_window(redis_url)


@pytest.mark.integration
async def test_s_01() -> None:
    redis_url = os.environ.get("MUAD_CLUSTER_REDIS_URL")
    if not redis_url:
        pytest.fail("env_blocked: MUAD_CLUSTER_REDIS_URL must point to a real Redis Cluster")
    client = RedisCluster.from_url(redis_url, decode_responses=True)
    domain = "egress-repair-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Probe)
    workers: list[asyncio.subprocess.Process] = []
    _Probe.count = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            pytest.fail(f"env_blocked: Redis Cluster unavailable: {type(exc).__name__}")
        await client.hset(prefix + "guard", mapping={"epoch": "test-epoch"})
        meter = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
        await meter.publish(_policy())
        # A newly introduced window starts counting at its publication boundary.
        for index in range(2):
            workers.append(await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()), "--worker", str(server.server_port),
                str(index), env={**os.environ, "EGRESS_TEST_DOMAIN": domain},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            ))
        outcomes = await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in workers)), 90)
        for process, (_, stderr) in zip(workers, outcomes, strict=True):
            assert process.returncode == 0, stderr.decode(errors="replace")[-2000:]
        assert _Probe.count == 50
        keys = [key async for key in client.scan_iter(match=prefix + "*")]
        assert keys
        assert len({client.keyslot(key) for key in keys}) == 1
    finally:
        for process in workers:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in workers))
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        try:
            keys = [key async for key in client.scan_iter(match=prefix + "*")]
            for key in keys:
                await client.delete(key)
        finally:
            await client.aclose()


@pytest.mark.integration
async def test_b_01() -> None:
    redis_url = os.environ.get("MUAD_CLUSTER_REDIS_URL")
    if not redis_url:
        pytest.fail("env_blocked: MUAD_CLUSTER_REDIS_URL must point to a real Redis Cluster")
    await _check_controlled_window(redis_url)
    client = RedisCluster.from_url(redis_url, decode_responses=True)
    domain = "egress-window-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    meter = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
    try:
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            pytest.fail(f"env_blocked: Redis Cluster unavailable: {type(exc).__name__}")
        await client.hset(prefix + "guard", mapping={"epoch": "test-epoch"})
        await meter.publish(_policy())
        await asyncio.sleep(60.1)
        for number in range(50):
            permit = await meter.admit(
                _policy(), target_instance_id="platform", route_id="GET /assets",
                attempt_id=f"window-{number}", lease_seconds=30,
            )
            assert permit is not None
            await meter.consume(permit)
            await meter.release(permit)
        with pytest.raises(SharedLimitExceeded) as error:
            await meter.admit(
                _policy(), target_instance_id="platform", route_id="GET /assets",
                attempt_id="window-denied", lease_seconds=30,
            )
        assert error.value.kind == "WINDOW"
        # Wait from the final admission, ensuring all 50 timestamps leave the window.
        await asyncio.sleep(60.1)
        permit = await meter.admit(
            _policy(), target_instance_id="platform", route_id="GET /assets",
            attempt_id="window-after-expiry", lease_seconds=30,
        )
        assert permit is not None
        await meter.release(permit)
        # Simulate a Redis clock moving backwards by advancing the persisted guard.
        last_ms = int(await client.hget(prefix + "guard", "last_ms"))
        await client.hset(prefix + "guard", "last_ms", last_ms + 300_000)
        with pytest.raises(MeteringUnavailable, match="CLOCK"):
            await meter.admit(
                _policy(), target_instance_id="platform", route_id="GET /assets",
                attempt_id="window-clock-backwards", lease_seconds=30,
            )
    finally:
        try:
            keys = [key async for key in client.scan_iter(match=prefix + "*")]
            for key in keys:
                await client.delete(key)
        finally:
            await client.aclose()


@pytest.mark.integration
async def test_b_04() -> None:
    redis_url = os.environ.get("MUAD_CLUSTER_REDIS_URL")
    if not redis_url:
        pytest.fail("env_blocked: MUAD_CLUSTER_REDIS_URL must point to a real Redis Cluster")
    client = RedisCluster.from_url(redis_url, decode_responses=True)
    domain = "egress-revision-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    meter = SharedRedisMeter(client, domain=domain, epoch="test-epoch")
    try:
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            pytest.fail(f"env_blocked: Redis Cluster unavailable: {type(exc).__name__}")
        await client.hset(prefix + "guard", mapping={"epoch": "test-epoch"})
        await meter.publish(_revision_policy(1, 100))
        await meter.register_execution("execution-1", max_requests=60)
        await meter.register_invocation("execution-1", "invocation-1", max_requests=60)
        assert await _run_revision_worker(domain, 1, 100, 30, "old-before") == {
            "allowed": 30, "limited": 0, "stale": 0,
        }
        await meter.publish(_revision_policy(2, 50))
        old, new = await asyncio.gather(
            _run_revision_worker(domain, 1, 100, 1, "old-after"),
            _run_revision_worker(domain, 2, 50, 30, "new-after"),
        )
        assert old == {"allowed": 0, "limited": 0, "stale": 1}
        assert new == {"allowed": 20, "limited": 10, "stale": 0}
        assert await _run_revision_worker(domain, 1, 100, 2, "refresh", refresh=True) == {
            "allowed": 0, "limited": 1, "stale": 1,
        }
        window_policy = _revision_policy(3, 100, window_seconds=60)
        window_policy = window_policy.model_copy(update={
            "rules": (window_policy.rules[0].model_copy(update={"window_requests": 1}),),
        })
        await meter.publish(window_policy)
        invocation = TrustedInvocation("execution-1", "invocation-1", "segment-activation")
        permit = await meter.admit(
            window_policy, target_instance_id="platform", route_id="GET /assets",
            attempt_id="new-window-first", lease_seconds=30, invocation=invocation,
        )
        assert permit is not None
        await meter.consume(permit)
        await meter.release(permit)
        with pytest.raises(SharedLimitExceeded) as error:
            await meter.admit(
                window_policy, target_instance_id="platform", route_id="GET /assets",
                attempt_id="new-window-next", lease_seconds=30, invocation=invocation,
            )
        assert error.value.kind == "WINDOW" and error.value.scope == "public"
        await meter.publish(_revision_policy(4, 200))
        assert await _run_revision_worker(domain, 4, 200, 12, "restored") == {
            "allowed": 9, "limited": 3, "stale": 0,
        }
        await meter.publish(_revision_policy(5, 300))
        assert await _run_revision_worker(domain, 5, 300, 1, "restored-again") == {
            "allowed": 0, "limited": 1, "stale": 0,
        }
    finally:
        try:
            keys = [key async for key in client.scan_iter(match=prefix + "*")]
            for key in keys:
                await client.delete(key)
        finally:
            await client.aclose()


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--worker":
    asyncio.run(_worker(os.environ["MUAD_CLUSTER_REDIS_URL"], os.environ["EGRESS_TEST_DOMAIN"],
                        int(sys.argv[2]), int(sys.argv[3])))
elif __name__ == "__main__" and len(sys.argv) in (6, 7) and sys.argv[1] == "--revision-worker":
    refresh = len(sys.argv) == 7 and sys.argv[6] == "--refresh"
    asyncio.run(_revision_worker(os.environ["MUAD_CLUSTER_REDIS_URL"], os.environ["EGRESS_TEST_DOMAIN"],
                                 int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], refresh))
