import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from muad_contracts import CredentialMode
from muad_platform_sdk import (
    PlatformAdapter,
    PlatformClient,
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PlatformSessionManager,
    PreparedRequest,
    SecretValue,
    SessionMode,
    SessionRequest,
)
from muad_skill_sdk import (
    ArtifactAccess,
    HttpClient,
    HttpResponse,
    McpClient,
    SkillContext,
    SkillUser,
    TaskClient,
)


class DummyAdapter:
    key: str = "dummy"
    name: str = "Dummy Adapter"
    version: str = "1.0.0"
    session_mode: SessionMode = SessionMode.SESSION
    platform_config_schema: dict[str, Any] = {"type": "object", "properties": {}}
    credential_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def authenticate(
        self,
        platform: PlatformConfig,
        credential: SecretValue,
    ) -> PlatformSession | None:
        return PlatformSession(session_id=f"{platform.key}:{credential.version}")

    async def validate(self, platform: PlatformConfig, session: PlatformSession) -> bool:
        return session.expires_at is None

    async def prepare_request(
        self,
        platform: PlatformConfig,
        session: PlatformSession | None,
        request: PlatformRequest,
        credential: SecretValue | None,
    ) -> PreparedRequest:
        target = request.target
        suffix = target.service or (target.path or "").lstrip("/")
        return PreparedRequest(
            method=target.method or "POST",
            url=f"https://{platform.key}.example/{suffix}",
        )


class DummyPlatformClient:
    async def call(self, platform_key: str, request: PlatformRequest) -> Mapping[str, Any]:
        return {"platform_key": platform_key, "payload": dict(request.payload)}

    async def request(
        self,
        platform_key: str,
        service: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "platform_key": platform_key,
            "service": service,
            "operation": operation,
            "payload": dict(payload),
        }


class DummySessionManager:
    async def acquire(self, request: SessionRequest) -> PlatformSession | None:
        if request.credential is None:
            return None
        return PlatformSession(session_id=f"{request.platform.key}:{request.credential.version}")

    async def invalidate(self, *, platform: PlatformConfig, actor_scope: str) -> None:
        return None

    async def renew(self, request: SessionRequest) -> PlatformSession | None:
        return await self.acquire(request)


class DummyArtifactAccess:
    def __init__(self) -> None:
        self._items: dict[str, bytes] = {}

    async def read(self, storage_key: str) -> bytes:
        return self._items[storage_key]

    async def write(self, storage_key: str, data: bytes) -> None:
        self._items[storage_key] = data


class DummyMcpClient:
    async def call(self, server: str, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"server": server, "tool": tool, "arguments": dict(arguments)}


class DummyTaskClient:
    async def map(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        script: str,
        max_concurrency: int,
    ) -> Sequence[Mapping[str, Any]]:
        return [{"script": script, "item": dict(item)} for item in items]


class DummyHttpClient:
    async def get(
        self,
        url: str,
        *,
        timeout_sec: float,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        return HttpResponse(status_code=200, headers=headers or {}, body=url.encode())

    async def post(
        self,
        url: str,
        *,
        timeout_sec: float,
        body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        return HttpResponse(status_code=200, headers=headers or {}, body=(body or url).encode())

    async def request(
        self,
        method: str,
        url: str,
        *,
        timeout_sec: float,
        body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        return HttpResponse(status_code=200, headers=headers or {}, body=f"{method}:{url}".encode())


def build_skill_context() -> SkillContext:
    artifact: ArtifactAccess = DummyArtifactAccess()
    platform: PlatformClient = DummyPlatformClient()
    mcp: McpClient = DummyMcpClient()
    task: TaskClient = DummyTaskClient()
    http: HttpClient = DummyHttpClient()
    return SkillContext(
        user=SkillUser(user_id="user-1", tenant_id="tenant-1", display_name="User One"),
        logger=logging.getLogger("tests.sdk"),
        artifact=artifact,
        platform=platform,
        mcp=mcp,
        task=task,
        http=http,
    )


@pytest.fixture
def platform_config() -> PlatformConfig:
    return PlatformConfig(
        key="mss",
        name="MSS Platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": "https://mss.example"},
        adapter_key="dummy",
        credential_mode=CredentialMode.USER_ONLY,
    )


@pytest.fixture
def adapter() -> PlatformAdapter:
    return DummyAdapter()


@pytest.fixture
def platform_client() -> PlatformClient:
    return DummyPlatformClient()


@pytest.fixture
def session_manager() -> PlatformSessionManager:
    return DummySessionManager()


@pytest.fixture
def skill_context() -> SkillContext:
    return build_skill_context()
