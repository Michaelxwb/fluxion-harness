"""TASK-004 集成夹具：真实 Redis session + 真实平台 adapter + 真实 Unix socket。

Console 的 ``resolve-egress-access`` 用与 ``tests/sdk/test_platform_sliding_session.py``
相同的记录桩：它只校验服务身份/租户/actor 并返回平台配置与凭据，代表 Console 的
凭据解析响应契约；Redis Session、adapter 登录、Unix socket 和 state 文件全部是真实实现。
"""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from muad_common import SharedSettings
from muad_platform_sdk import MSSAdapter, PlatformAdapterRegistry, PlatformConfig, SecretValue
from muad_platform_sdk.session.redis_manager import RedisPlatformSessionManager
from muad_platform_sdk.skill_access import SkillPlatformContext
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[3]
SESSION_CLI = ROOT / "packages/platform-sdk/src/muad_platform_sdk/session_cli.py"
DEFAULT_SERVICE_TOKEN = "session-manager-internal-token"


class ConsoleStub(BaseHTTPRequestHandler):
    """`/internal/runtime/resolve-egress-access` 的记录桩。"""

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if (
            urlsplit(self.path).path != "/internal/runtime/resolve-egress-access"
            or self.headers.get("X-Internal-Service") != self.server.service_token
            or self.headers.get("X-Tenant-Id") != self.server.tenant_id
        ):
            self._reply(403, {"code": "COMMON_FORBIDDEN"})
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            self._reply(422, {"code": "COMMON_INVALID_ARGUMENT"})
            return
        actor = (payload.get("execution_ref") or {}).get("id")
        if not actor or payload.get("actor_user_id") != self.server.actor_user_id:
            self._reply(403, {"code": "COMMON_FORBIDDEN"})
            return
        if payload.get("platform_key") != self.server.platform.key:
            self._reply(404, {"code": "COMMON_NOT_FOUND"})
            return
        platform = self.server.platform
        platform_dump = platform.model_dump(mode="json")
        resolver = dict(platform_dump.get("resolver_config") or {})
        base_url = str(resolver.get("base_url", ""))
        # 容器内 broker 用 host.docker.internal 访问宿主测试桩；平台 base_url 同步改写。
        if self.headers.get("Host", "").startswith("host.docker.internal"):
            resolver["base_url"] = base_url.replace("127.0.0.1", "host.docker.internal")
        platform_dump["resolver_config"] = resolver
        body = {
            "code": "0",
            "data": {
                "decision": "ALLOW",
                "platform": {
                    "id": self.server.platform_id,
                    **{key: value for key, value in platform_dump.items() if key not in ("name", "enabled")},
                },
                "credential": {
                    "ref_id": "credential-" + platform.key,
                    "credential_json": json.loads(self.server.credential.value),
                    "actor_scope": "user:" + self.server.actor_user_id,
                },
            },
        }
        self._reply(200, body)

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@dataclass
class SessionChain:
    context: SkillPlatformContext
    redis: Redis
    manager: RedisPlatformSessionManager
    console: ThreadingHTTPServer
    platform_server: Any
    tenant_id: str
    platform_id: str
    actor_user_id: str
    platform_key: str
    console_url: str
    service_token: str

    @property
    def container_console_url(self) -> str:
        return _container_url(self.console_url)

    @property
    def container_redis_url(self) -> str:
        assert self.context.redis_url is not None
        return _container_url(self.context.redis_url)

    async def cleanup(self) -> None:
        manager = self.manager
        keys = await self.redis.smembers(manager.index)
        if keys:
            await self.redis.delete(*keys)
        await self.redis.delete(manager.index, manager.epoch)
        await self.redis.aclose()
        _shutdown(self.console)
        _shutdown(self.platform_server)


def _shutdown(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def _container_url(url: str) -> str:
    """把宿主机 loopback 换成 Docker Desktop 可解析的 host.docker.internal。"""
    parts = urlsplit(url)
    host = "host.docker.internal"
    return urlunsplit((parts.scheme, f"{host}:{parts.port}" if parts.port else host, parts.path, "", ""))


@asynccontextmanager
async def open_session_chain(*, platform_key: str = "mssp") -> AsyncIterator[SessionChain]:
    settings = SharedSettings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required for session-manager integration tests")
    redis = Redis.from_url(settings.redis_url)
    await redis.ping()
    registry = PlatformAdapterRegistry()
    for adapter in (MSSAdapter(),):
        registry.register(adapter)

    from tests.sdk.platform_server import AuthPlatformServer

    ak, sk = secrets.token_hex(12), secrets.token_hex(24)
    platform_server = AuthPlatformServer(("0.0.0.0", 0), ak, sk)
    threading.Thread(target=platform_server.serve_forever, daemon=True).start()
    platform = PlatformConfig(
        key=platform_key,
        name="MSSP test platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": f"http://127.0.0.1:{platform_server.server_port}"},
        adapter_key="mss-session",
        adapter_config={"csrf_enabled": True},
        credential_mode="USER_ONLY",
    )
    credential = SecretValue(value=json.dumps({"ak": ak, "sk": sk}), version="1")

    tenant_id = "acc-session-" + uuid.uuid4().hex
    platform_id = str(uuid.uuid4())
    manager = RedisPlatformSessionManager(redis, registry, tenant_id=tenant_id, platform_id=platform_id)
    console = ThreadingHTTPServer(("0.0.0.0", 0), ConsoleStub)
    console.service_token = DEFAULT_SERVICE_TOKEN
    console.tenant_id = tenant_id
    console.actor_user_id = str(uuid.uuid4())
    console.platform = platform
    console.platform_id = platform_id
    console.credential = credential
    threading.Thread(target=console.serve_forever, daemon=True).start()

    context = SkillPlatformContext(
        tenant_id=tenant_id,
        actor_user_id=console.actor_user_id,
        execution_id=str(uuid.uuid4()),
        execution_type="RUN",
        platform_keys=(platform_key,),
        console_url=f"http://127.0.0.1:{console.server_port}",
        redis_url=settings.redis_url,
        service_token=DEFAULT_SERVICE_TOKEN,
    )
    chain = SessionChain(
        context=context,
        redis=redis,
        manager=manager,
        console=console,
        platform_server=platform_server,
        tenant_id=tenant_id,
        platform_id=platform_id,
        actor_user_id=console.actor_user_id,
        platform_key=platform_key,
        console_url=f"http://127.0.0.1:{console.server_port}",
        service_token=DEFAULT_SERVICE_TOKEN,
    )
    try:
        yield chain
    finally:
        await chain.cleanup()


def write_skill_package(root: Path, *, name: str, platform: str, main: str) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\nplatforms: [{platform}]\n---\n"
        f"Legacy session-manager probe\n",
        encoding="utf-8",
    )
    (scripts / "main.py").write_text(main, encoding="utf-8")
    return root


def legacy_probe_script(skill_name: str, platform: str) -> str:
    """旧 Skill 形态：只调 session-manager，从 stdout 的 platforms list + state 文件读取。"""
    return (
        "import json, os, subprocess\n"
        f"info = json.loads(subprocess.check_output(['session-manager', 'get-state', "
        f"'--skill-name', {skill_name!r}]))\n"
        "state = json.load(open(info['sessionStateFile'], encoding='utf-8'))\n"
        f"platform = state['platforms'][{platform!r}]\n"
        "cookies = {item['name']: item['value'] for item in platform['cookies']}\n"
        "print(json.dumps({\n"
        "    'path': info['sessionStateFile'],\n"
        "    'platforms': info['platforms'],\n"
        "    'base_url': platform['requestProfile']['baseUrl'],\n"
        "    'cookie_names': sorted(cookies),\n"
        "    'expires_at': platform['expiresAt'],\n"
        "    'socket': bool(os.environ.get('MUAD_SESSION_SOCKET')),\n"
        "}))\n"
    )
