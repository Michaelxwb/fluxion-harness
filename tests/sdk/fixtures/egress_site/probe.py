"""Linux wheel/system-site/bwrap/HTTP/Redis probe; no injected fake transports."""
import asyncio
import json
import os
import sys
import sysconfig
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.egress_ipc import EgressTarget, InvocationEgressBroker
from muad_platform_sdk.shared_redis import SharedRedisMeter
from redis.asyncio import Redis

BUSINESS = '''import os, sys, requests, sibling
from pathlib import Path
assert sys.flags.ignore_environment and sys.flags.no_user_site and sys.dont_write_bytecode
assert not any(k in os.environ for k in ('REDIS_URL','DATABASE_URL','API_KEY'))
r = requests.get(os.environ['PROBE_ORIGIN']+'/assets', headers={
    'X-CSRFToken':'fixed-test-csrf','Cookie':'test-session=value'}, timeout=3)
assert r.status_code == 200 and r.text == 'ok' and sibling.VALUE == 42
Path(os.environ['BUSINESS_MARKER']).write_text('ran')
'''


def http_server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, seen


async def child(root, output, socket_path, origin, *, missing_pth=False, missing_ipc=False, controlled=True):
    command = ["bwrap", "--unshare-user", "--uid", "0", "--gid", "0"]
    for name in ("/usr", "/lib", "/lib64", "/etc"):
        if Path(name).exists():
            command += ["--ro-bind", name, name]
    command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--ro-bind", str(root), "/skill", "--bind", str(output), "/output",
                "--ro-bind", str(socket_path.parent), "/run/egress", "--chdir", "/skill"]
    site = Path(sysconfig.get_path("purelib"))
    if missing_pth:
        command += ["--ro-bind", "/dev/null", str(site / "muad_egress.pth")]
    command += [sys.executable, "-E", "-s", "-B"]
    if controlled:
        command += [str(site / "muad_egress_launch.py")]
    command += ["/skill/business.py"]
    env = {"PATH": os.environ["PATH"], "PROBE_ORIGIN": origin,
           "BUSINESS_MARKER": "/output/marker", "PYTHONPATH": "/ignored-by-E"}
    if controlled:
        env["MUAD_EGRESS_SOCKET"] = "/run/egress/missing.sock" if missing_ipc else "/run/egress/broker.sock"
    process = await asyncio.create_subprocess_exec(
        *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            stdout, stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, stdout.decode(), stderr.decode()


async def exercise(meter, policy, redis, origin, seen):
    def resolve(attempt):
        if attempt.origin == origin and attempt.path == "/assets" and attempt.method == "GET":
            return EgressTarget("platform", "GET /assets")
        return None

    with tempfile.TemporaryDirectory(prefix="egress-business-") as temp:
        root, output = Path(temp) / "skill", Path(temp) / "output"
        root.mkdir()
        output.mkdir()
        (root / "business.py").write_text(BUSINESS)
        (root / "sibling.py").write_text("VALUE=42\n")
        async with InvocationEgressBroker(meter=meter, policy=policy,
                                          invocation=TrustedInvocation("execution", "script", "segment"),
                                          resolve=resolve, deadline_seconds=30, cleanup_seconds=1) as broker:
            result = await child(root, output, broker.socket_path, origin)
            assert result[0] == 0, result
            assert (output / "marker").read_text() == "ran"
            assert len(seen) == 1 and await redis.get(meter._bucket("assets", "total")) == "1"
            for options, code in (({"missing_pth": True}, "EGRESS_HOOK_NOT_READY"),
                                  ({"missing_ipc": True}, "EGRESS_HOOK_STARTUP_FAILED")):
                (output / "marker").unlink(missing_ok=True)
                result = await child(root, output, broker.socket_path, origin, **options)
                assert result[0] != 0 and code in result[2], result
                assert not (output / "marker").exists() and len(seen) == 1
            result = await child(root, output, broker.socket_path, origin, controlled=False)
            assert result[0] == 0, result
            assert len(seen) == 2 and await redis.get(meter._bucket("assets", "total")) == "1"
        assert not list(root.rglob("__pycache__"))


async def main():
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    meter = SharedRedisMeter(redis, domain="site-" + uuid.uuid4().hex, epoch="epoch")
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "assets", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 5,
    }]})
    server, worker, seen = http_server()
    try:
        await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
        await meter.publish(policy)
        await meter.register_execution("execution", max_requests=5)
        await meter.register_invocation("execution", "script", max_requests=2)
        await exercise(meter, policy, redis, f"http://127.0.0.1:{server.server_port}", seen)
        assert all(h["X-CSRFToken"] == "fixed-test-csrf"
                   and h["Cookie"] == "test-session=value" for h in seen)
        print(json.dumps({"status": "passed", "controlled_requests": 1, "startup_blocks": 2,
                          "uncontrolled_requests": 1, "python": sys.version.split()[0]}))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        keys = [key async for key in redis.scan_iter(match=meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
