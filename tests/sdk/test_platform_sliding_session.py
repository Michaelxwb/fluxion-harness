"""Real Redis keys + HTTP platform_server + actual Skill subprocess; no shared PG data."""

import asyncio
import json
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from muad_common import SharedSettings
from muad_platform_sdk import MSSAdapter, PlatformAdapterRegistry, SecretValue
from muad_platform_sdk.session.manager import SessionRequest
from muad_platform_sdk.session.redis_manager import RedisPlatformSessionManager
from muad_platform_sdk.skill_access import SkillPlatformContext
from redis.asyncio import Redis

from tests.sdk.test_mss_sessions import platform_server as _platform_server_fixture

platform_server = _platform_server_fixture

pytestmark = pytest.mark.integration


@pytest.fixture
async def cache():
    url = SharedSettings().redis_url
    if not url:
        pytest.skip("REDIS_URL is required for real Redis session integration tests")
    redis = Redis.from_url(url)
    await redis.ping()
    registry = PlatformAdapterRegistry()
    for adapter in (MSSAdapter(),):
        registry.register(adapter)
    tenant, platform_id = "session-test-" + uuid.uuid4().hex, str(uuid.uuid4())
    manager = RedisPlatformSessionManager(redis, registry, tenant_id=tenant, platform_id=platform_id)
    yield redis, registry, manager, url
    keys = await redis.smembers(manager.index)
    if keys:
        await redis.delete(*keys)
    await redis.delete(manager.index, manager.epoch)
    await redis.aclose()


def request_for(platform, credential, scope="user:test"):
    return SessionRequest(platform, scope, credential.version, "test-ref", credential)


async def test_two_hour_sliding_ttl_and_idle_expiry(platform_server, cache):
    server, platform, credential = platform_server
    redis, _, manager, _ = cache
    req = request_for(platform, credential)
    first = await manager.acquire(req)
    assert 7198 <= await redis.ttl(manager.key(req)) <= 7200
    await redis.expire(manager.key(req), 1)  # Simulate nearly two hours idle, without waiting two hours.
    await manager.acquire(req)  # A new Skill starts and touches the same login state.
    assert 7198 <= await redis.ttl(manager.key(req)) <= 7200
    assert len(server.sessions) == 1
    await redis.expire(manager.key(req), 1)
    await asyncio.sleep(1.1)
    second = await manager.acquire(req)
    assert first.cookie != second.cookie
    assert len(server.sessions) == 2


async def test_concurrent_login_and_user_isolation(platform_server, cache):
    server, platform, credential = platform_server
    redis, _, manager, _ = cache
    req = request_for(platform, credential)
    sessions = await asyncio.gather(*(manager.acquire(req) for _ in range(5)))
    assert len({s.cookie for s in sessions}) == 1
    assert len(server.sessions) == 1
    other = request_for(platform, credential, "user:other")
    await manager.acquire(other)
    assert manager.key(req) != manager.key(other)
    assert len(server.sessions) == 2
    assert json.loads(await redis.get(manager.key(req))).get("credential") is None


async def test_server_revocation_and_credential_rotation(platform_server, cache):
    server, platform, credential = platform_server
    _, _, manager, _ = cache
    req = request_for(platform, credential)
    original = await manager.acquire(req)
    server.sessions.clear()
    renewed = await manager.acquire(req)
    assert original.cookie != renewed.cookie
    rotated = request_for(platform, SecretValue(value=credential.value, version="2"))
    assert manager.key(req) != manager.key(rotated)
    await manager.acquire(rotated)
    assert len(server.sessions) == 2


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if (
            self.headers.get("X-Internal-Service") != self.server.token
            or payload["actor_user_id"] != self.server.actor
        ):
            self.send_response(403)
            self.end_headers()
            return
        platform, credential = self.server.platform, self.server.credential
        body = json.dumps(
            {
                "code": "0",
                "data": {
                    "decision": "ALLOW",
                    "platform": {
                        "id": self.server.platform_id,
                        **{
                            k: v
                            for k, v in platform.model_dump(mode="json").items()
                            if k not in ("name", "enabled")
                        },
                    },
                    "credential": {
                        "ref_id": "test-ref",
                        "credential_json": json.loads(credential.value),
                        "actor_scope": "user:" + self.server.actor,
                    },
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
async def execution_context(platform_server, cache):
    _, platform, credential = platform_server
    _, _, manager, url = cache
    control = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    control.token, control.actor = secrets.token_hex(24), str(uuid.uuid4())
    control.platform, control.credential, control.platform_id = platform, credential, manager.platform_id
    thread = threading.Thread(target=control.serve_forever, daemon=True)
    thread.start()
    context = SkillPlatformContext(
        manager.tenant_id,
        control.actor,
        str(uuid.uuid4()),
        "RUN",
        ("platform_server",),
        f"http://127.0.0.1:{control.server_port}",
        url,
        control.token,
    )
    yield context
    control.shutdown()
    control.server_close()
    thread.join()


def skill_script(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "---\nname: platform_server\ndescription: platform_server\n"
        "platforms: [platform_server]\n---\nTest platform query"
    )
    (tmp_path / "scripts/main.py").write_text(
        "from muad_skill_sdk import platform_request\nimport json\n"
        'print(json.dumps(platform_request("platform_server", "GET", "/events")))'
    )
    return tmp_path


async def test_real_skill_subprocess_calls_platform_and_touches_cache(
    tmp_path, platform_server, cache, execution_context
):
    from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest

    redis, registry, manager, _ = cache
    result = await ScriptSkillExecutor().execute(
        SkillExecutionRequest(
            ready_dir=skill_script(tmp_path), input={}, timeout_sec=20, platform_context=execution_context
        )
    )
    assert result.result == {"code": 0}
    assert result.exit_code == 0
    keys = await redis.smembers(manager.index)
    assert len(keys) == 1
    key = next(iter(keys))
    await redis.expire(key, 1)
    # No platform request in this execution: declaration alone renews the login on Skill start.
    (tmp_path / "scripts/main.py").write_text('print("{}")')
    await ScriptSkillExecutor().execute(
        SkillExecutionRequest(
            ready_dir=tmp_path, input={}, timeout_sec=20, platform_context=execution_context
        )
    )
    assert 7198 <= await redis.ttl(key) <= 7200
    assert len(platform_server[0].sessions) == 1


async def test_script_cannot_override_execution_identity(tmp_path, execution_context):
    from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest

    root = skill_script(tmp_path)
    (root / "scripts/main.py").write_text(
        "from muad_skill_sdk import platform_request\nimport json\ntry:\n"
        ' platform_request("other", "GET", "/events")\nexcept RuntimeError as e:\n'
        ' print(json.dumps({"error": str(e)}))'
    )
    result = await ScriptSkillExecutor().execute(
        SkillExecutionRequest(ready_dir=root, input={}, timeout_sec=20, platform_context=execution_context)
    )
    assert result.result == {"error": "PLATFORM_ACCESS_UNAVAILABLE"}


async def test_runtime_skill_tool_path_uses_platform_context(tmp_path, execution_context):
    from muad_agent_runtime.application.skill_tools import SkillToolSet
    from muad_contracts import ResolvedSkill

    root = skill_script(tmp_path)
    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="platform_server",
        name="Query",
        description="Query",
        version="1",
        checksum="test",
        storage_key="test",
        frontmatter={"platforms": ["platform_server"]},
    )

    class Cache:
        async def ensure(self, **kwargs):
            return root

    tools = SkillToolSet(cache=Cache(), skills=[skill], timeout_sec=20, platform_context=execution_context)
    result = json.loads(await tools.execute_skill({"skill_key": skill.key, "input": {}}, call_id="test"))
    assert result["result"] == {"code": 0}


async def test_worker_skill_path_uses_task_identity(tmp_path, execution_context, monkeypatch):

    from muad_agent_worker.infrastructure.models.task import TaskExecution
    from muad_agent_worker.worker import executor as module
    from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskPlatformAudit
    from muad_common import process_settings

    root = skill_script(tmp_path)
    artifact = uuid.uuid4()
    task = TaskExecution(
        id=uuid.uuid4(),
        tenant_id=execution_context.tenant_id,
        actor_user_id=uuid.UUID(execution_context.actor_user_id),
        skill_artifact_id=artifact,
        task_type="SKILL",
        deadline_at=datetime.now(UTC) + timedelta(seconds=20),
        input_json={},
        attempt=1,
        idempotency_key="test-task",
        external_ref_json={},
        execution_snapshot_json={
            "skills": [
                {
                    "artifact_id": str(artifact),
                    "storage_key": "test",
                    "checksum": "test",
                    "platforms": ["platform_server"],
                }
            ]
        },
    )
    settings = process_settings().model_copy(
        update={
            "redis_url": execution_context.redis_url,
            "console_platform_url": execution_context.console_url,
            "internal_service_token": execution_context.service_token,
        }
    )
    monkeypatch.setattr(module, "process_settings", lambda: settings)
    recorded = []

    async def audit(self, **kwargs):
        recorded.append((self.tenant_id, self.task_id, kwargs))

    monkeypatch.setattr(TaskPlatformAudit, "record_egress", audit)

    class Cache:
        async def ensure(self, **kwargs):
            return root

    result = await SkillTaskExecutor(Cache()).execute(task)
    assert result["result"] == {"code": 0}
    assert recorded[0][0:2] == (task.tenant_id, task.id)


async def test_invalidation_prevents_inflight_login_resurrection(platform_server, cache, monkeypatch):
    from muad_platform_sdk import PlatformAuthError
    from muad_platform_sdk.session.invalidator import RedisPlatformSessionInvalidator

    redis, registry, manager, _ = cache
    _, platform, credential = platform_server
    adapter = registry.get(platform.adapter_key)
    original = adapter.authenticate
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        state = await original(*args)
        entered.set()
        await resume.wait()
        return state

    monkeypatch.setattr(adapter, "authenticate", delayed)
    operation = asyncio.create_task(manager.acquire(request_for(platform, credential)))
    await entered.wait()
    await RedisPlatformSessionInvalidator(redis).clear_platform(
        tenant_id=manager.tenant_id, platform_id=uuid.UUID(manager.platform_id)
    )
    resume.set()
    with pytest.raises(PlatformAuthError, match="SESSION_CHANGED"):
        await operation
    assert await redis.get(manager.key(request_for(platform, credential))) is None


def test_task_snapshot_freezes_only_platform_keys():
    from muad_contracts import ResolvedSkill
    from muad_contracts.snapshot import _skill_entry

    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="query",
        name="Query",
        description="Query",
        version="1",
        checksum="test",
        storage_key="test",
        frontmatter={"platforms": ["mssp", "mssw", "mssp"], "unrelated_private_field": "excluded"},
    )
    frozen = _skill_entry(skill)
    assert frozen["platforms"] == ["mssp", "mssw"]
    assert "frontmatter" not in frozen and "unrelated_private_field" not in frozen


@pytest.mark.parametrize("scope", ["user:test", "shared:default"])
async def test_custom_json_login_through_redis(platform_server, cache, scope):
    server, platform, _ = platform_server
    redis, _, manager, _ = cache
    server.json_login = True
    server.expected_login_body = {"region": "private", "options": {"roles": ["read"]}}
    platform = platform.model_copy(
        update={
            "adapter_config": {"auth_mode": "json", "csrf_enabled": True, "login_body": {"region": "public"}}
        }
    )
    credential = SecretValue(value=json.dumps({"body": server.expected_login_body}), version="1")
    req = request_for(platform, credential, scope)
    first = await manager.acquire(req)
    assert server.last_login_body == server.expected_login_body
    await redis.expire(manager.key(req), 60)
    assert (await manager.acquire(req)).cookie == first.cookie
    assert len(server.sessions) == 1
    assert 7198 <= await redis.ttl(manager.key(req)) <= 7200
    stored = await redis.get(manager.key(req))
    assert b"private" not in stored and b"roles" not in stored
    await manager.invalidate(platform=platform, actor_scope=scope)
    assert await redis.get(manager.key(req)) is None
