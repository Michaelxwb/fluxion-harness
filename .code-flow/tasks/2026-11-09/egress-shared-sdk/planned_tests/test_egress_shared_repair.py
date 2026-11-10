"""Real Redis Cluster and subprocess probes for the shared egress repair."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter
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
        # A newly introduced strict window must observe its entire history.
        await asyncio.sleep(60.1)
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


def test_b_04() -> None:
    pytest.fail("PLAN_SKELETON: requires two processes, policy changes and frozen budget")


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--worker":
    asyncio.run(_worker(os.environ["MUAD_CLUSTER_REDIS_URL"], os.environ["EGRESS_TEST_DOMAIN"],
                        int(sys.argv[2]), int(sys.argv[3])))
