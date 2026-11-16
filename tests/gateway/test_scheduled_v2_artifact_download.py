"""B-11 任务产物下载：无 Conversation 的任务产物按 owner+agent 读取指定不可变版本。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用**：`muad_im_gateway.main.app`（生产路由表、api-kit 封套、真实主体门控）。
- **真实 Console HTTP（Owner）**：CHAT 主体与**当前授权 Agent 集合**每次下载都重新核验。
- **真实 Runtime HTTP（Owner）**：`RuntimeClient` 打真实本地 uvicorn；版本解析按真实契约
  （`GET /internal/artifacts/{id}/versions/{version}`）核属 `artifact.task_id → task_execution`
  的 tenant/actor/agent，版本/校验值不符是 `ARTIFACT_VERSION_CONFLICT`。
- **真实共享文件系统**：隔离 artifact root 上的真实字节；取件键只来自 Owner 解析，客户端
  提供的任何键都不被信任。

断言要点：

- 无会话也能下载；`version` + `checksum` 缺一不可，且绝不拿最新版顶替（冲突即 409）；
- 只在**当前授权 Agent** 之间查找归属：撤销/未授权 Agent 的产物 404 且 Owner 一个解析请求都
  收不到；跨用户 404 不泄露存在性；
- 字节长度不符视同"拿不到"，绝不按错误长度声明 `Content-Length`；
- 全程只有任务读面调用：没有投递/通知写入，也没有聊天推送。
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import uvicorn
from fakes import StubConsole
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.application.worker_client import WorkerClient
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-sched024-artifact"
USER_A = UUID("55555555-5555-4555-8555-555555555555")
USER_B = UUID("66666666-6666-4666-8666-666666666666")
AGENT_A1 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
AGENT_A2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
AGENT_REVOKED = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3")
SESSION_ID = UUID("77777777-7777-4777-8777-777777777777")
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"

ARTIFACT_ID = UUID("88888888-8888-4888-8888-888888888881")
OTHER_AGENT_ARTIFACT_ID = UUID("88888888-8888-4888-8888-888888888882")
REVOKED_ARTIFACT_ID = UUID("88888888-8888-4888-8888-888888888883")
OTHER_USER_ARTIFACT_ID = UUID("88888888-8888-4888-8888-888888888884")
EXECUTION_ID = UUID("99999999-9999-4999-8999-999999999999")

BASE = f"/api/v1/webchat/task-center/artifacts/{ARTIFACT_ID}"
V1_BYTES = b"# v1 report\nfirst published version\n"
V2_BYTES = b"# v2 report\nlatest version must not be substituted\n"
V1_CHECKSUM = "sha256:" + hashlib.sha256(V1_BYTES).hexdigest()
V2_CHECKSUM = "sha256:" + hashlib.sha256(V2_BYTES).hexdigest()
NO_CONVERSATION_QUERY = "conversation_id"


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-s024",
        "request_id": "request-s024",
        "timestamp": "2026-11-15T08:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int) -> JSONResponse:
    body = _envelope(None)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


# --- 真实 Console 主体核验桩 ---------------------------------------------------------------


class _PrincipalProbe:
    def __init__(self, stub: StubConsole) -> None:
        self.calls: list[dict[str, str]] = []
        self.agents: list[dict[str, str]] = [
            {"agent_id": str(AGENT_A1), "key": "agent-a1", "name": "助手一"},
            {"agent_id": str(AGENT_A2), "key": "agent-a2", "name": "助手二"},
        ]
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal

    async def _principal(self, request: Request) -> Response:
        await request.body()
        self.calls.append(dict(request.headers))
        return _json(
            {
                "principal": {
                    "tenant_id": GATEWAY_TENANT,
                    "platform_user_id": str(USER_A),
                    "session_id": str(SESSION_ID),
                    "auth_method": "USER_TOKEN",
                },
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "agents": list(self.agents),
            }
        )


# --- 真实 Runtime 版本解析桩 ---------------------------------------------------------------


@dataclass(frozen=True)
class _Version:
    key: str
    media_type: str
    filename: str
    checksum: str
    size: int


@dataclass(frozen=True)
class _Artifact:
    owner: UUID
    agent_id: UUID
    versions: dict[str, _Version]
    #: 软删 / 内部留档（`TOOL_RESULT`/`TRANSCRIPT`）在 Owner 侧都不可下载，与不存在同形。
    downloadable: bool = True


@dataclass
class _Recorded:
    path: str
    version: str
    query: dict[str, str]
    headers: dict[str, str]


@dataclass
class _RuntimeProbe:
    """Runtime 受信版本解析：归属按 owner+agent 核属，版本/校验值不符是冲突。"""

    artifacts: dict[UUID, _Artifact] = field(default_factory=dict)
    calls: list[_Recorded] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._app = self._build_app()

    def _build_app(self) -> FastAPI:
        runtime_app = FastAPI()

        @runtime_app.get("/internal/artifacts/{artifact_id}/versions/{version}")
        async def _resolve(artifact_id: str, version: str, request: Request) -> Response:
            if request.headers.get("x-internal-service") != INTERNAL_TOKEN:
                return _error(str(ErrorCode.FORBIDDEN), 403)
            self.calls.append(
                _Recorded(
                    path=request.url.path,
                    version=version,
                    query=dict(request.query_params),
                    headers=dict(request.headers),
                )
            )
            artifact = self.artifacts.get(UUID(artifact_id))
            if artifact is None:
                return _error(str(ErrorCode.TASK_ARTIFACT_NOT_FOUND), 404)
            if not artifact.downloadable:
                return _error(str(ErrorCode.TASK_ARTIFACT_NOT_FOUND), 404)
            if artifact.owner != UUID(request.query_params["user_id"]):
                return _error(str(ErrorCode.TASK_ARTIFACT_NOT_FOUND), 404)
            if artifact.agent_id != UUID(request.query_params["agent_id"]):
                return _error(str(ErrorCode.TASK_ARTIFACT_NOT_FOUND), 404)
            entry = artifact.versions.get(version)
            if entry is None or entry.checksum != request.query_params["checksum"]:
                return _error(str(ErrorCode.ARTIFACT_VERSION_CONFLICT), 409)
            return _json(
                {
                    "storage_key": entry.key,
                    "kind": "DOCUMENT",
                    "media_type": entry.media_type,
                    "size": entry.size,
                    "filename": entry.filename,
                    "checksum": entry.checksum,
                    "source_channel": None,
                    "artifact_id": artifact_id,
                }
            )

        return runtime_app

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("runtime probe did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    _app: FastAPI = field(init=False, repr=False)
    _server: uvicorn.Server | None = field(init=False, default=None)
    _thread: threading.Thread | None = field(init=False, default=None)
    url: str = field(init=False, default="")


class _TrackingStore(NfsArtifactStore):
    """真实共享存储 + 取件留痕：被拒的请求一个字节都不该读。"""

    def __init__(self, root: str | Path) -> None:
        super().__init__(root)
        self.resolved_keys: list[str] = []

    def resolve(self, storage_key: str) -> Path:
        self.resolved_keys.append(storage_key)
        return super().resolve(storage_key)


def _seed(
    probe: _RuntimeProbe,
    root: Path,
    *,
    artifact_id: UUID,
    owner: UUID,
    agent_id: UUID,
    versions: list[_Version],
    size_override: int | None = None,
    downloadable: bool = True,
) -> None:
    payload: dict[str, _Version] = {}
    for number, version in enumerate(versions, start=1):
        key = f"tasks/{GATEWAY_TENANT}/{EXECUTION_ID}/{artifact_id}/v{number}"
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        body = V1_BYTES if number == 1 else V2_BYTES
        path.write_bytes(body)
        payload[str(number)] = _Version(
            key=key,
            media_type=version.media_type,
            filename=version.filename,
            checksum=version.checksum,
            size=len(body) if size_override is None else size_override,
        )
    probe.artifacts[artifact_id] = _Artifact(
        owner=owner, agent_id=agent_id, versions=payload, downloadable=downloadable
    )


# --- 夹具 ---------------------------------------------------------------------------------


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def principal(stub_console: StubConsole) -> _PrincipalProbe:
    return _PrincipalProbe(stub_console)


@pytest.fixture
async def runtime_probe() -> AsyncIterator[_RuntimeProbe]:
    probe = _RuntimeProbe()
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def store(artifact_root: Path) -> _TrackingStore:
    return _TrackingStore(artifact_root)


_STATE_NAMES = ("console_client", "runtime_client", "worker_client", "artifact_store", "tenant_id")


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    probe: _RuntimeProbe,
    store: NfsArtifactStore,
    *,
    cookies: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    previous: dict[str, Any] = {name: getattr(app.state, name, None) for name in _STATE_NAMES}
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(probe.url, service_token=INTERNAL_TOKEN)
    # 本文件只测产物下载；Worker 端口指向同一真实 socket（不应被调用）。
    worker = WorkerClient(probe.url, service_token=INTERNAL_TOKEN)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.worker_client = worker
    app.state.artifact_store = store
    app.state.tenant_id = GATEWAY_TENANT
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://gateway.test",
            cookies={CHAT_SESSION_COOKIE: SESSION_TOKEN} if cookies is None else cookies,
        ) as client:
            yield client
    finally:
        for name, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)
        await console.aclose()
        await runtime.aclose()
        await worker.aclose()


@pytest.fixture
async def api(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    store: _TrackingStore,
) -> AsyncIterator[AsyncClient]:
    async with _gateway_client(stub_console, runtime_probe, store) as client:
        yield client


def _seed_default(probe: _RuntimeProbe, root: Path) -> None:
    _seed(
        probe,
        root,
        artifact_id=ARTIFACT_ID,
        owner=USER_A,
        agent_id=AGENT_A1,
        versions=[
            _Version(
                key="",
                media_type="text/markdown",
                filename="周报 v1.md",
                checksum=V1_CHECKSUM,
                size=len(V1_BYTES),
            ),
            _Version(
                key="",
                media_type="text/markdown",
                filename="周报 v2.md",
                checksum=V2_CHECKSUM,
                size=len(V2_BYTES),
            ),
        ],
    )


# --- 断言 ---------------------------------------------------------------------------------


async def test_download_serves_exact_version_without_conversation(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed_default(runtime_probe, artifact_root)

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(
            BASE, headers=_chat_headers(), params={"version": "1", "checksum": V1_CHECKSUM}
        )

    assert response.status_code == 200, response.text
    assert response.content == V1_BYTES, "必须返回请求的那一版，不能拿最新版顶替"
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-length"] == str(len(V1_BYTES))
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith("%E5%91%A8%E6%8A%A5%20v1.md")
    assert "tasks/" not in response.text and str(EXECUTION_ID) not in str(response.headers)

    assert len(runtime_probe.calls) == 1
    call = runtime_probe.calls[0]
    assert call.path == f"/internal/artifacts/{ARTIFACT_ID}/versions/1"
    assert call.query == {
        "user_id": str(USER_A),
        "agent_id": str(AGENT_A1),
        "checksum": V1_CHECKSUM,
    }
    assert NO_CONVERSATION_QUERY not in call.query, "任务产物下载不要求 Conversation"
    assert call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert store.resolved_keys == [f"tasks/{GATEWAY_TENANT}/{EXECUTION_ID}/{ARTIFACT_ID}/v1"]


async def test_download_searches_only_currently_authorized_agents(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=OTHER_AGENT_ARTIFACT_ID,
        owner=USER_A,
        agent_id=AGENT_A2,
        versions=[
            _Version(
                key="",
                media_type="application/pdf",
                filename="report.pdf",
                checksum=V1_CHECKSUM,
                size=len(V1_BYTES),
            )
        ],
        )
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=REVOKED_ARTIFACT_ID,
        owner=USER_A,
        agent_id=AGENT_REVOKED,
        versions=[
            _Version(
                key="",
                media_type="application/pdf",
                filename="revoked.pdf",
                checksum=V1_CHECKSUM,
                size=len(V1_BYTES),
            )
        ],
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        second_agent = await client.get(
            f"/api/v1/webchat/task-center/artifacts/{OTHER_AGENT_ARTIFACT_ID}",
            headers=_chat_headers(),
            params={"version": "1", "checksum": V1_CHECKSUM},
        )
        revoked = await client.get(
            f"/api/v1/webchat/task-center/artifacts/{REVOKED_ARTIFACT_ID}",
            headers=_chat_headers(),
            params={"version": "1", "checksum": V1_CHECKSUM},
        )

    assert second_agent.status_code == 200, second_agent.text
    assert [call.query["agent_id"] for call in runtime_probe.calls[:2]] == [
        str(AGENT_A1),
        str(AGENT_A2),
    ], "只在授权集合内逐个核属；第一个 Agent 不匹配后继续找自己授权的那个"

    assert revoked.status_code == 404, "撤销授权 Agent 的产物不可见"
    assert revoked.json()["code"] == str(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    # 网关不替客户端猜 Agent：只在**授权集合**里核属，未授权 Agent 的 id 永远不会被送去解析。
    assert [call.query["agent_id"] for call in runtime_probe.calls[2:]] == [
        str(AGENT_A1),
        str(AGENT_A2),
    ]
    assert all(call.query["agent_id"] != str(AGENT_REVOKED) for call in runtime_probe.calls)
    assert store.resolved_keys == [
        f"tasks/{GATEWAY_TENANT}/{EXECUTION_ID}/{OTHER_AGENT_ARTIFACT_ID}/v1"
    ]


async def test_wrong_version_or_checksum_is_conflict_not_latest(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed_default(runtime_probe, artifact_root)

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        wrong_checksum = await client.get(
            BASE, headers=_chat_headers(), params={"version": "1", "checksum": V2_CHECKSUM}
        )
        wrong_version = await client.get(
            BASE, headers=_chat_headers(), params={"version": "99", "checksum": V1_CHECKSUM}
        )

    for response in (wrong_checksum, wrong_version):
        assert response.status_code == 409, response.text
        assert response.json()["code"] == str(ErrorCode.ARTIFACT_VERSION_CONFLICT)
        assert response.content != V1_BYTES and response.content != V2_BYTES
    assert store.resolved_keys == [], "冲突时一个字节都不读"


async def test_version_and_checksum_are_required_before_touching_owner(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed_default(runtime_probe, artifact_root)

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        no_version = await client.get(BASE, headers=_chat_headers(), params={"checksum": V1_CHECKSUM})
        no_checksum = await client.get(BASE, headers=_chat_headers(), params={"version": "1"})

    assert no_version.status_code == 422
    assert no_checksum.status_code == 422
    assert runtime_probe.calls == []


async def test_cross_user_artifact_is_404_without_leaking(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=OTHER_USER_ARTIFACT_ID,
        owner=USER_B,
        agent_id=AGENT_A1,
        versions=[
            _Version(
                key="",
                media_type="text/markdown",
                filename="other.md",
                checksum=V1_CHECKSUM,
                size=len(V1_BYTES),
            )
        ],
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(
            f"/api/v1/webchat/task-center/artifacts/{OTHER_USER_ARTIFACT_ID}",
            headers=_chat_headers(),
            params={"version": "1", "checksum": V1_CHECKSUM},
        )

    assert response.status_code == 404
    assert response.json()["code"] == str(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    assert store.resolved_keys == []


async def test_deleted_or_internal_artifact_is_not_downloadable(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    """软删与内部留档（transcript/tool result）在 Owner 侧收敛成 404，网关不另开一条通路。"""
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        owner=USER_A,
        agent_id=AGENT_A1,
        versions=[
            _Version(
                key="",
                media_type="application/json",
                filename="transcript.json",
                checksum=V1_CHECKSUM,
                size=len(V1_BYTES),
            )
        ],
        downloadable=False,
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(
            BASE, headers=_chat_headers(), params={"version": "1", "checksum": V1_CHECKSUM}
        )

    assert response.status_code == 404
    assert response.json()["code"] == str(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    assert store.resolved_keys == [], "不可下载的产物一个字节都不读"


async def test_byte_length_mismatch_is_not_found(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        owner=USER_A,
        agent_id=AGENT_A1,
        versions=[
            _Version(
                key="",
                media_type="text/markdown",
                filename="truncated.md",
                checksum=V1_CHECKSUM,
                size=len(V1_BYTES),
            )
        ],
        size_override=len(V1_BYTES) + 10,
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(
            BASE, headers=_chat_headers(), params={"version": "1", "checksum": V1_CHECKSUM}
        )

    assert response.status_code == 404, "行内长度与实际字节不符视同拿不到"
    assert "content-length" not in response.headers or response.headers["content-length"] != str(
        len(V1_BYTES) + 10
    )


async def test_download_requires_chat_session(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed_default(runtime_probe, artifact_root)

    async with _gateway_client(stub_console, runtime_probe, store, cookies={}) as client:
        response = await client.get(
            BASE, headers=_chat_headers(), params={"version": "1", "checksum": V1_CHECKSUM}
        )

    assert response.status_code == 401
    assert runtime_probe.calls == []
    assert principal.calls == []
    assert store.resolved_keys == []


async def test_download_never_writes_delivery_or_notifications(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    store: _TrackingStore,
) -> None:
    _seed_default(runtime_probe, artifact_root)

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(
            BASE, headers=_chat_headers(), params={"version": "2", "checksum": V2_CHECKSUM}
        )

    assert response.status_code == 200, response.text
    assert response.content == V2_BYTES
    # Console 只被问过主体；Runtime 只收到版本解析；Worker 完全没被调用。
    assert all(
        headers.get("x-tenant-id") == GATEWAY_TENANT for headers in principal.calls
    )
    assert {call.path for call in runtime_probe.calls} == {
        f"/internal/artifacts/{ARTIFACT_ID}/versions/2"
    }
    assert SESSION_TOKEN not in json.dumps(principal.calls[0]), "CHAT 会话原文不进请求头"
