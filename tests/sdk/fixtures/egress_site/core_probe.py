"""Real SandboxedSkillExecutor + bwrap + standard requests + Redis/HTTP.

Host identities are constructed by this probe. It does not invoke Runtime or
Worker executors or authenticate a PG lease, so it is only part of E-02.
"""
import asyncio
import json
import os
import sysconfig
import tempfile
import threading
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from muad_agent_core.sandbox import SandboxMount, build_sandbox_runner
from muad_agent_core.skill import SandboxedSkillExecutor, SkillExecutionError, SkillExecutionRequest
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.egress_ipc import EgressTarget, InvocationEgressBroker
from muad_platform_sdk.egress_segments import FencedRedisMeter, SegmentGrant
from redis.asyncio import Redis

BUSINESS = '''import json, os, sys, requests, sibling
from pathlib import Path
assert sys.flags.ignore_environment and sys.flags.no_user_site and sys.dont_write_bytecode
assert not any(key in os.environ for key in ("REDIS_URL", "DATABASE_URL", "INTERNAL_SERVICE_TOKEN"))
payload = json.load(sys.stdin)
Path("/workspace/business-marker").write_text("started")
response = requests.get(payload["origin"] + "/ok", headers={"X-CSRFToken":"fixed", "Cookie":"csrf=fixed"})
assert response.status_code == 200 and response.content == b"ok"
print(json.dumps({"argv":sys.argv[1:], "sibling":sibling.VALUE}))
'''
STOP_BUSINESS = '''import json, os, signal, subprocess, sys, threading, time, requests
from pathlib import Path
payload = json.load(sys.stdin)
child_code = ("import signal,time; from pathlib import Path; "
              "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
              "Path('/workspace/grandchild-ready').write_text('ready'); time.sleep(60)")
subprocess.Popen([sys.executable, "-c", child_code, payload["marker"]])
while not Path("/workspace/grandchild-ready").exists(): time.sleep(.01)
thread = threading.Thread(target=lambda: requests.get(payload["origin"] + "/slow"), daemon=True)
thread.start()
while not Path("/workspace/slow-started").exists(): time.sleep(.01)
for index in range(3):
    try: requests.get(payload["origin"] + "/ok")
    except Exception: pass  # Deliberately swallow the SDK error; the host must still kill the group.
while True: time.sleep(1)
'''


class Network:
    def __init__(self, workspace):
        self.calls, self.headers = [], []
        self.closed = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return
            def do_GET(self):
                owner.calls.append(self.path)
                owner.headers.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Length", "20" if self.path == "/slow" else "2")
                self.end_headers()
                self.wfile.write(b"x" if self.path == "/slow" else b"ok")
                self.wfile.flush()
                if self.path == "/slow":
                    (workspace / "slow-started").write_text("started")
                    self.connection.settimeout(15)
                    try:
                        if self.connection.recv(1) == b"":
                            owner.closed.set()
                    except (ConnectionResetError, BrokenPipeError):
                        owner.closed.set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def package(root, code):
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "SKILL.md").write_text("---\nname: probe\ndescription: probe\n---\n")
    (root / "scripts/main.py").write_text(code)
    (root / "scripts/sibling.py").write_text("VALUE=42\n")
    return root


def broker(meter, policy, origin):
    return InvocationEgressBroker(
        meter=meter,  invocation=TrustedInvocation("execution", "script", "segment"),
        resolve=lambda request: EgressTarget("platform", "GET /probe")
        if request.origin == origin and request.path in {"/ok", "/slow"} else None,
        deadline_seconds=12, cleanup_seconds=1, renewal_interval_seconds=.05,
    )


def active_processes(marker):
    found = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if marker.encode() in path.read_bytes():
                found.append(path.parent.name)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue  # Concurrent exit or unrelated UID; sandbox children use this probe's UID.
    return found


async def run_stopping(executor, meter, policy, redis, parent):
    workspace = parent / "stop-workspace"
    workspace.mkdir()
    network = Network(workspace)
    marker = "egress-grandchild-" + uuid.uuid4().hex
    try:
        root = package(parent / "stop-package", STOP_BUSINESS)
        context = broker(meter, policy, network.origin)
        try:
            await executor.execute(SkillExecutionRequest(
                ready_dir=root, workspace_dir=workspace, source="USER", timeout_sec=12,
                egress_broker=context, input={"origin": network.origin, "marker": marker},
            ))
        except SkillExecutionError as exc:
            assert exc.code == "LOCAL_BUDGET_EXHAUSTED" and not exc.retryable, (exc.code, exc)
        else:
            raise AssertionError("host did not stop the script")
        assert network.calls == ["/slow", "/ok", "/ok"], network.calls
        assert await asyncio.to_thread(network.closed.wait, 3), "slow HTTP remained open"
        async with asyncio.timeout(3):
            while active_processes(marker):
                await asyncio.sleep(.01)
        assert (workspace / "grandchild-ready").exists()
        assert not context.socket_path.parent.exists()
        assert await redis.zcard(meter._bucket("core", "leases")) == 1  # guard only, no live occupancy
        assert await redis.get(meter._bucket("core", "total")) == "3"
    finally:
        network.close()


class MaskedSite:
    name = "bwrap"
    def __init__(self, runner):
        self.runner = runner
    async def run(self, spec):
        target = Path(sysconfig.get_path("purelib")) / "muad_egress.pth"
        mask = SandboxMount(Path("/dev/null"), str(target), readonly=True)
        return await self.runner.run(replace(spec, mounts=(*spec.mounts, mask)))


async def run_startup(runner, meter, policy, redis, parent):
    workspace = parent / "startup-workspace"
    workspace.mkdir()
    network = Network(workspace)
    root = package(parent / "startup-package", BUSINESS)
    try:
        def request(context):
            return SkillExecutionRequest(
                ready_dir=root, workspace_dir=workspace, source="USER", timeout_sec=12,
                egress_broker=context, argv=("--literal", "$HOME"), input={"origin": network.origin},
                env={"REDIS_URL": "must-not-leak", "DATABASE_URL": "must-not-leak",
                     "INTERNAL_SERVICE_TOKEN": "must-not-leak", "MUAD_EGRESS_SOCKET": "/untrusted"},
            )
        executor = SandboxedSkillExecutor(runner=runner)
        result = await executor.execute(request(broker(meter, policy, network.origin)))
        assert result.exit_code == 0, (result.status, result.stderr)
        assert result.result == {"argv": ["--literal", "$HOME"], "sibling": 42}
        assert network.headers[0]["X-CSRFToken"] == "fixed"
        assert network.headers[0]["Cookie"] == "csrf=fixed"
        (workspace / "business-marker").unlink()
        masked = SandboxedSkillExecutor(runner=MaskedSite(runner))
        result = await masked.execute(request(broker(meter, policy, network.origin)))
        assert result.exit_code == 78 and "EGRESS_HOOK_NOT_READY" in result.stderr, result
        assert not (workspace / "business-marker").exists()
        assert len(network.calls) == 1
        result = await SandboxedSkillExecutor(runner=runner).execute(request(None))
        assert result.exit_code == 0, result.stderr
        assert len(network.calls) == 2
        assert await redis.get(meter._bucket("core", "total")) == "1"
    finally:
        network.close()


async def main():
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    domain = "core-" + uuid.uuid4().hex
    meter = FencedRedisMeter(redis, domain=domain, epoch="epoch",
                             grant=SegmentGrant("execution", 1, "segment"))
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "core", "target_instance_id": "platform", "route_id": "GET /probe",
        "max_requests": 100, "max_inflight": 4,
    }]})
    await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    await meter.publish(policy)
    await meter.register_execution("execution", max_requests=100)
    await meter.register_invocation("execution", "script", max_requests=3)
    await meter.activate(lease_seconds=30, stop_margin_seconds=1)
    runner = build_sandbox_runner(mode="bwrap", env="test")
    try:
        with tempfile.TemporaryDirectory(prefix="egress-core-") as directory:
            parent = Path(directory)
            await run_stopping(SandboxedSkillExecutor(runner=runner), meter, policy, redis, parent)
        await run_fresh_startup(runner, redis)
        print(json.dumps({"status": "passed", "quota_requests": 3, "grandchild_reclaimed": True,
                          "slow_http_closed": True, "startup_checks": 3}))
    finally:
        keys = [key async for key in redis.scan_iter(meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def run_fresh_startup(runner, redis):
    domain = "startup-" + uuid.uuid4().hex
    meter = FencedRedisMeter(redis, domain=domain, epoch="epoch",
                             grant=SegmentGrant("execution", 1, "segment"))
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "core", "target_instance_id": "platform", "route_id": "GET /probe", "max_requests": 100,
    }]})
    await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    await meter.publish(policy)
    await meter.register_execution("execution", max_requests=100)
    await meter.register_invocation("execution", "script", max_requests=3)
    await meter.activate(lease_seconds=30, stop_margin_seconds=1)
    try:
        with tempfile.TemporaryDirectory(prefix="egress-startup-") as directory:
            await run_startup(runner, meter, policy, redis, Path(directory))
    finally:
        keys = [key async for key in redis.scan_iter(meter._key("*"))]
        if keys:
            await redis.delete(*keys)


if __name__ == "__main__":
    asyncio.run(main())
