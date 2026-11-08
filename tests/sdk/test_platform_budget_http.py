"""Real HTTP / Redis / Skill executor tests for platform budget transport."""
import asyncio
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from muad_contracts.egress_budget import resolve_egress_budget
from muad_platform_sdk import MSSAdapter, PlatformRequest, PlatformTarget, PreparedRequest
from muad_platform_sdk.request_budget import LocalRateLimitError, create_budget_controller

from tests.sdk.test_mss_sessions import platform_server as platform_server_fixture
from tests.sdk.test_platform_sliding_session import (
    cache as cache_fixture,
)
from tests.sdk.test_platform_sliding_session import (
    execution_context as execution_context_fixture,
)
from tests.sdk.test_platform_sliding_session import (
    skill_script,
)

platform_server = platform_server_fixture
cache = cache_fixture
execution_context = execution_context_fixture
pytestmark = pytest.mark.integration


def controller(policy):
    return create_budget_controller(resolve_egress_budget(policy), segment_id="platform-segment")


def record_requests(server):
    base = server.RequestHandlerClass
    server.requests = []
    class Recording(base):
        def do_GET(self):
            server.requests.append(("GET", self.path))
            return super().do_GET()
        def do_POST(self):
            server.requests.append(("POST", self.path))
            return super().do_POST()
    server.RequestHandlerClass = Recording


async def test_s_02_auth_validation_business_and_explicit_retry_share_gate(platform_server):
    from muad_platform_sdk.platform_transport import PlatformBudgetScope, PlatformHttpSender
    server, platform, credential = platform_server
    record_requests(server)
    budget = controller({"execution": {"max_requests": 8}})
    class Audit:
        def __init__(self):
            self.records = []
        async def record_egress(self, **fields):
            self.records.append(fields)
    audit = Audit()
    scope = PlatformBudgetScope(budget, audit=audit)
    adapter = MSSAdapter(budget=scope)
    state = await adapter.authenticate(platform, credential)  # CSRF + login
    assert await adapter.validate(platform, state)  # health
    request = PlatformRequest(target=PlatformTarget(method="GET", path="/events"))
    prepared = await adapter.prepare_request(platform, state, request, None)
    async with httpx.AsyncClient(trust_env=False) as client:
        sender = PlatformHttpSender(platform, client, budget=scope)
        assert (await sender.send(prepared)).status_code == 200
        server.sessions.clear()
        assert (await sender.send(prepared)).status_code == 401
        state = await adapter.authenticate(platform, credential)  # controlled caller renews
        prepared = await adapter.prepare_request(platform, state, request, None)
        assert (await sender.send(prepared)).status_code == 200
        with pytest.raises(LocalRateLimitError):
            await sender.send(prepared)
    assert budget.used == 8
    assert len(server.requests) == 8  # denial never reached real HTTP server
    assert len(server.sessions) == 1
    assert len(audit.records) == 1
    assert audit.records[0]["policy_decision"] == "DENY"
    assert credential.value not in repr(audit.records)


async def test_s_02_budget_context_reaches_real_skill_executor(tmp_path, execution_context, platform_server):
    from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest
    from muad_platform_sdk.platform_transport import PlatformBudgetScope
    record_requests(platform_server[0])
    budget = controller({"execution": {"max_requests": 2}})
    context = replace(execution_context, budget_scope=PlatformBudgetScope(budget))
    root = skill_script(tmp_path)
    (root / "scripts/main.py").write_text('print("{}")')
    result = await ScriptSkillExecutor().execute(
        SkillExecutionRequest(ready_dir=root, input={}, timeout_sec=10, platform_context=context)
    )
    assert result.exit_code == 0
    assert budget.used == 2
    assert len(platform_server[0].requests) == 2


async def test_s_02_legacy_direct_requests_are_explicitly_outside_sdk(
    tmp_path, execution_context, platform_server,
):
    from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest
    from muad_platform_sdk.platform_transport import PlatformBudgetScope
    server, _, _ = platform_server
    record_requests(server)
    budget = controller({"targets": [{"id": "events", "match": {
        "platform_key": "platform_server", "path_template": "/events",
    }, "limits": {"max_requests": 1}}]})
    root = skill_script(tmp_path)
    script = root / "scripts/main.py"
    script.write_text(script.read_text().replace(
        'print(urllib.request.urlopen(req).read().decode())',
        'urllib.request.urlopen(req).read(); print(urllib.request.urlopen(req).read().decode())',
    ))
    result = await ScriptSkillExecutor().execute(SkillExecutionRequest(
        ready_dir=root, input={}, timeout_sec=20,
        platform_context=replace(execution_context, budget_scope=PlatformBudgetScope(budget)),
    ))
    assert result.exit_code == 0
    assert sum(path == "/events" for _, path in server.requests) == 2
    assert budget.used == 0  # must never claim these old direct calls were controlled


@pytest.fixture
def slow_server(platform_server):
    _, platform, _ = platform_server
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            server.hits += 1
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"a")
            self.wfile.flush()
            server.started.set()
            server.release.wait(5)
            if not server.abort:
                try:
                    self.wfile.write(b"b")
                except (BrokenPipeError, ConnectionResetError):
                    pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.started, server.release = threading.Event(), threading.Event()
    server.hits, server.abort = 0, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    platform = platform.model_copy(update={"resolver_config": {"base_url": f"http://127.0.0.1:{server.server_port}"}})
    yield server, platform
    server.release.set()
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("outcome", ["normal", "cancel", "broken"])
async def test_b_03_stream_holds_slot_until_body_close(slow_server, outcome):
    from muad_platform_sdk.adapter.mss_config import PlatformAuthError
    from muad_platform_sdk.platform_transport import PlatformBudgetScope, PlatformHttpSender
    server, platform = slow_server
    budget = controller({"execution": {"max_inflight": 1}})
    prepared = PreparedRequest(method="GET", url=platform.resolver_config["base_url"] + "/slow")
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        sender = PlatformHttpSender(platform, client, budget=PlatformBudgetScope(budget))
        first = asyncio.create_task(sender.send(prepared))
        try:
            assert await asyncio.to_thread(server.started.wait, 3)
            assert budget.inflight == 1
            with pytest.raises(LocalRateLimitError):
                await sender.send(prepared)
            assert server.hits == 1
            if outcome == "cancel":
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                server.abort = outcome == "broken"
                server.release.set()
                if server.abort:
                    with pytest.raises(PlatformAuthError, match="PLATFORM_NETWORK_ERROR"):
                        await first
                else:
                    assert (await first).content == b"ab"
            assert budget.inflight == 0
        finally:
            server.release.set()
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
