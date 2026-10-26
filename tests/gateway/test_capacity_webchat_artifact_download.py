"""B-255 Gateway 附件下载授权与安全预览（design §3.4 API-10、§3.6 附件与重试生命周期；TASK-055）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/
  错误目录、真实 `api/deps.py` 主体门控依赖图），并挂载**生产** `api/webchat.py` 路由；
  装配根状态（Console/Runtime 客户端、入口快照、共享存储、部署租户）按生产同形注入。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 打真实本地 uvicorn 服务（真实 socket）：
  CHAT 主体核验（**每次请求**都发生，授权集合是 Console 权威）与 bot 快照。
- **真实 Runtime HTTP（Owner）**：`RuntimeClient` 打真实本地 uvicorn 服务；受信解析端口
  `GET /internal/webchat/artifacts/{id}` 按**真实契约**形状（`WebchatConversationScope` +
  `AttachmentRef`）应答，记录网关实际发出的归属主体与租户/服务身份头。
- **真实共享文件系统**：隔离 artifact root 上真实 `NfsArtifactStore` 字节；取件字节与磁盘逐字节
  一致，且记录"字节读取发生在 Owner 归属解析**完整结束之后**"。
- **不读写 DB**：本文件不碰任何数据库；另对下载面模块做零 sqlalchemy 静态断言（禁止 Gateway 持库）。

断言要点（B-255）：

- 下载与图片预览**逐请求**复核会话/来源/当前授权：跨用户、跨租户、已失效（软删/内部留档）、
  他 Agent、停用入口一律拒绝且不泄露存在性；授权撤销 403；
- 安全预览：只有白名单栅格图片 `inline`，HTML/SVG 等可执行内容按**附件**处理（不执行），
  统一 `no-store`/`nosniff`；坏媒体类型降级为二进制流；
- 客户端只给 `artifact_id`：存储键由 Owner 解析（客户端提供的 storage_key 被忽略），
  存储键/内部路径/主体字段不进响应；
- 行内长度与实际字节不符视同"拿不到"（不按错误长度声明 `Content-Length`）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
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
from muad_artifact_store import NfsArtifactStore
from muad_contracts import BotSnapshotItem, BotSnapshotResponse
from muad_im_core import BotSnapshotCache
from muad_im_gateway.api.webchat import router as webchat_router
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b255"
PLATFORM_USER_ID = UUID("7f0a1b2c-3d4e-4f50-9a1b-2c3d4e5f6071")
OTHER_PLATFORM_USER_ID = UUID("8a1b2c3d-4e5f-4a60-8b1c-3d4e5f607182")
SESSION_ID = UUID("9b2c3d4e-5f60-4b71-9c2d-4e5f60718293")
AGENT_ID = UUID("ac3d4e5f-6071-4c82-8d3e-5f60718293a4")
OTHER_AGENT_ID = UUID("bd4e5f60-7182-4d93-9e4f-60718293a4b5")
ENTRY_ID = UUID("ce5f6071-8293-4ea4-8f50-718293a4b5c6")
OTHER_ENTRY_ID = UUID("df607182-93a4-4fb5-9051-8293a4b5c6d7")
DISABLED_ENTRY_ID = UUID("e0718293-a4b5-4ac6-8162-93a4b5c6d7e8")
CONVERSATION_ID = UUID("f18293a4-b5c6-4bd7-9273-a4b5c6d7e8f9")
RUN_ID = UUID("0293a4b5-c6d7-4ce8-8384-b5c6d7e8f901")
UPLOAD_ID = UUID("13a4b5c6-d7e8-4df9-8495-c6d7e8f90112")
ARTIFACT_ID = UUID("24b5c6d7-e8f9-4e0a-85a6-d7e8f9011223")
OTHER_ARTIFACT_ID = UUID("35c6d7e8-f901-4f1b-86b7-e8f901122334")
DELETED_ARTIFACT_ID = UUID("46d7e8f9-0112-402c-87c8-f90112233445")
TRANSCRIPT_ARTIFACT_ID = UUID("57e8f901-1223-413d-88d9-011223344556")

#: CHAT 会话原文 canary：只允许出现在发往 Console 的请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"
#: 取件字节 canary：只允许存在于共享存储与（授权通过的）响应体里。
PDF_BYTES = b"%PDF-1.7 b255 authorized bytes \x00\xff" + bytes(range(256))
IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"b255-image-bytes" * 32
HTML_BYTES = b"<html><script>alert('b255')</script></body></html>"
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
#: 客户端若被信任会读到的另一份字节：证明 storage_key 只由 Owner 解析。
CANARY_BYTES = b"b255-CLIENT-SUPPLIED-KEY-CANARY"

#: 真实 Run 制品键形状：`runs/<run>/attachments/<upload>`（系统生成，不含用户输入）。
STORAGE_KEY = f"runs/{RUN_ID}/attachments/{UPLOAD_ID}"
CANARY_KEY = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"
ESCAPING_KEY = "../../etc/passwd"

CONTENT_URL = f"/api/v1/webchat/artifacts/{ARTIFACT_ID}/content"
ARTIFACT_RESOLVE_PATH = "/internal/webchat/artifacts/{artifact_id}"
CHAT_PRINCIPAL_FIELDS = {"tenant_id", "platform_user_id", "session_id", "auth_method"}

#: 生产路由在真实应用上的注册（TASK-096 装配根只做同一件事）。
app.include_router(webchat_router)


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b255",
        "request_id": "request-b255",
        "timestamp": "2026-10-18T08:00:00Z",
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


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --- 真实 Console Owner 接口桩（真实 socket + 真实封套） ------------------------------------


class _ConsoleProbe:
    """API-12 的真实 HTTP 桩：chat-principal（**每次请求**核验）+ bot 快照。"""

    def __init__(self, stub: StubConsole) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.revoked_code: str | None = None
        #: 当前授权集合（Console 权威）：撤销就是把它从这次核验里拿掉。
        self.agents: list[dict[str, str]] = [
            {"agent_id": str(AGENT_ID), "key": "webchat-agent", "name": "网页助手"}
        ]
        self.items: list[BotSnapshotItem] = [
            BotSnapshotItem(
                bot_account_id=ENTRY_ID, bot_id=str(ENTRY_ID), channel="WEBCHAT", agent_id=AGENT_ID
            ),
            BotSnapshotItem(
                bot_account_id=OTHER_ENTRY_ID,
                bot_id=str(OTHER_ENTRY_ID),
                channel="WEBCHAT",
                agent_id=OTHER_AGENT_ID,
            ),
            BotSnapshotItem(
                bot_account_id=DISABLED_ENTRY_ID,
                bot_id=str(DISABLED_ENTRY_ID),
                channel="WEBCHAT",
                agent_id=AGENT_ID,
                enabled=False,
            ),
        ]
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal
        stub.handlers["/internal/channel/bots"] = self._bots

    async def _principal(self, request: Request) -> Response:
        body = json.loads(await request.body() or b"{}")
        self.calls.append((CHAT_PRINCIPAL_PATH, dict(request.headers), body))
        if self.revoked_code is not None:
            return _error(self.revoked_code, 401)
        return _json(
            {
                "principal": {
                    "tenant_id": GATEWAY_TENANT,
                    "platform_user_id": str(PLATFORM_USER_ID),
                    "session_id": str(SESSION_ID),
                    "auth_method": "USER_TOKEN",
                },
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "agents": list(self.agents),
            }
        )

    async def _bots(self, request: Request) -> Response:
        self.calls.append(("/internal/channel/bots", dict(request.headers), {}))
        return _json(
            BotSnapshotResponse(
                revision="rev-b255", items=self.items, page=1, page_size=20, total=len(self.items)
            ).model_dump(mode="json")
        )


# --- 真实 Runtime Owner 接口桩（真实 socket + 真实契约形状） --------------------------------


@dataclass(frozen=True)
class _ArtifactRecord:
    """Owner 侧的权威产物事实：归属主体 + 会话归属 + 渠道中立引用（存储键只在这里）。"""

    user_id: UUID
    storage_key: str
    media_type: str
    size: int
    filename: str | None
    conversation_id: UUID = CONVERSATION_ID
    entry_id: UUID = ENTRY_ID
    agent_id: UUID = AGENT_ID
    kind: str = "DOCUMENT"
    checksum: str = "sha256:" + "a" * 64


@dataclass(frozen=True)
class _ResolveCall:
    headers: dict[str, str]
    artifact_id: str
    user_id: str | None


@dataclass
class _RuntimeProbe:
    """Runtime 受信解析端口桩：真实 HTTP；按真实契约应答归属与渠道中立引用。"""

    artifacts: dict[str, _ArtifactRecord] = field(default_factory=dict)
    #: 归属解析**失败**的 id（跨租户/他主体/软删/内部留档在 Owner 侧都收敛成 404）。
    not_found: set[str] = field(default_factory=set)
    calls: list[_ResolveCall] = field(default_factory=list)
    resolve_started_at: float = 0.0
    resolve_finished_at: float = 0.0
    _app: FastAPI = field(init=False, default_factory=FastAPI)
    _server: uvicorn.Server | None = field(init=False, default=None)
    _thread: threading.Thread | None = field(init=False, default=None)
    url: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self._app = self._build_app()

    def _build_app(self) -> FastAPI:
        runtime_app = FastAPI()

        @runtime_app.get("/internal/webchat/artifacts/{artifact_id}")
        async def _resolve(artifact_id: str, request: Request) -> Response:
            self.resolve_started_at = time.monotonic()
            call = _ResolveCall(
                headers=dict(request.headers),
                artifact_id=artifact_id,
                user_id=request.query_params.get("user_id"),
            )
            self.calls.append(call)
            record = self.artifacts.get(artifact_id)
            # 真实 Owner 语义：跨租户/他主体/软删/内部留档都在解析面收敛成 404（不泄露存在性）。
            unauthorized = record is None or str(record.user_id) != call.user_id
            response = (
                _error("COMMON_NOT_FOUND", 404)
                if unauthorized or artifact_id in self.not_found
                else self._resolved(record)
            )
            self.resolve_finished_at = time.monotonic()
            return response

        return runtime_app

    @staticmethod
    def _resolved(record: _ArtifactRecord) -> JSONResponse:
        """真实契约形状：`WebchatConversationScope` + `AttachmentRef`（存储键只在受信面）。"""
        return _json(
            {
                "scope": {
                    "conversation_id": str(record.conversation_id),
                    "entry_id": str(record.entry_id),
                    "agent_id": str(record.agent_id),
                },
                "reference": {
                    "storage_key": record.storage_key,
                    "kind": record.kind,
                    "media_type": record.media_type,
                    "size": record.size,
                    "filename": record.filename,
                    "checksum": record.checksum,
                    "artifact_id": None,
                },
            }
        )

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


class _TrackingStore(NfsArtifactStore):
    """真实共享存储 + 读取留痕：被拒的请求必须**一个字节都不读**（`resolved_keys` 为空）。"""

    def __init__(self, root: str | Path) -> None:
        super().__init__(root)
        self.resolved_keys: list[str] = []
        self.resolved_at: list[float] = []

    def resolve(self, storage_key: str) -> Path:
        self.resolved_keys.append(storage_key)
        self.resolved_at.append(time.monotonic())
        return super().resolve(storage_key)


# --- 夹具：真实 HTTP 桩 + 真实 Gateway 应用（按生产装配根状态注入） --------------------------


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def console_probe(stub_console: StubConsole) -> _ConsoleProbe:
    return _ConsoleProbe(stub_console)


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


def _write_bytes(root: Path, storage_key: str, data: bytes) -> None:
    """把字节写进隔离共享存储（真实文件系统；目录结构由键决定，与生产一致）。

    键必须是系统生成的相对路径：这里再断言一次，测试自身也不会写到共享存储根之外。
    """
    path = root / storage_key
    resolved = path.resolve()
    assert resolved == root.resolve() or root.resolve() in resolved.parents, storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _seed(
    probe: _RuntimeProbe,
    root: Path,
    *,
    artifact_id: UUID,
    data: bytes,
    media_type: str,
    filename: str | None,
    storage_key: str = STORAGE_KEY,
    user_id: UUID = PLATFORM_USER_ID,
    size: int | None = None,
    entry_id: UUID = ENTRY_ID,
    agent_id: UUID = AGENT_ID,
) -> _ArtifactRecord:
    """登记一份真实产物：磁盘字节 + Owner 权威元信息（`size` 可显式错位以验证长度校验）。"""
    record = _ArtifactRecord(
        user_id=user_id,
        storage_key=storage_key,
        media_type=media_type,
        size=len(data) if size is None else size,
        filename=filename,
        entry_id=entry_id,
        agent_id=agent_id,
    )
    probe.artifacts[str(artifact_id)] = record
    _write_bytes(root, storage_key, data)
    return record


_STATE_NAMES = ("console_client", "runtime_client", "artifact_store", "bot_snapshot", "tenant_id")


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    runtime_probe: _RuntimeProbe,
    store: _TrackingStore,
    *,
    cookies: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 Console/Runtime 客户端；只注入装配根会发布的状态值。"""
    previous: dict[str, Any] = {name: getattr(app.state, name, None) for name in _STATE_NAMES}
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    snapshot = BotSnapshotCache(console, tenant_id=GATEWAY_TENANT)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.artifact_store = store
    app.state.bot_snapshot = snapshot
    app.state.tenant_id = GATEWAY_TENANT
    try:
        await snapshot.refresh()
        assert snapshot.is_ready(), "真实 Console 快照未就绪"
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


# --- B-255：逐请求授权取件（真实字节、真实协议探针） ----------------------------------------


async def test_b255_download_serves_authorized_bytes_without_leaking_storage_key(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """授权通过：真实字节逐字节送达；存储键/内部路径/主体字段不进响应。"""
    caplog.set_level(logging.DEBUG)
    store = _TrackingStore(artifact_root)
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=PDF_BYTES,
        media_type="application/pdf",
        filename="季度报告.pdf",
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(CONTENT_URL, headers=_chat_headers())

    assert response.status_code == 200, response.text
    assert response.content == PDF_BYTES, "响应字节必须与共享存储逐字节一致"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store", "私有聊天资源必须 no-store"
    assert response.headers["x-content-type-options"] == "nosniff", "下载/预览必须 nosniff"
    assert response.headers["content-length"] == str(len(PDF_BYTES))
    assert response.headers["content-disposition"] == (
        "attachment; filename*=utf-8''%E5%AD%A3%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf"
    ), "文件名只作元信息（非 ASCII 走 RFC 5987），可执行内容一律按附件处理"
    assert STORAGE_KEY not in response.text and STORAGE_KEY not in str(response.headers)
    assert "runs/" not in response.text
    assert SESSION_TOKEN not in response.text
    assert all(SESSION_TOKEN not in record.getMessage() for record in caplog.records)
    assert all(STORAGE_KEY not in record.getMessage() for record in caplog.records)

    # 客户端只给 artifact_id：解析请求带的是**当前主体**与部署租户/服务身份。
    assert len(runtime_probe.calls) == 1
    call = runtime_probe.calls[0]
    assert call.artifact_id == str(ARTIFACT_ID)
    assert call.user_id == str(PLATFORM_USER_ID)
    assert call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert store.resolved_keys == [STORAGE_KEY], "取件键只来自 Owner 解析，不由客户端提供"

    # 主体只经 Console 核验（每次下载都发生），主体字段不进响应。
    principals = [call for call in console_probe.calls if call[0] == CHAT_PRINCIPAL_PATH]
    assert len(principals) == 1
    assert CHAT_PRINCIPAL_FIELDS.isdisjoint(response.headers.keys())


async def test_b255_image_preview_is_inline_while_executable_content_downloads(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """安全预览：只有白名单栅格图片 `inline`；HTML/SVG 与坏媒体类型按附件下载（不执行）。"""
    store = _TrackingStore(artifact_root)
    image_key = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"
    html_key = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"
    svg_key = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"
    broken_key = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=IMAGE_BYTES,
        media_type="image/png",
        filename="截图.png",
        storage_key=image_key,
    )
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=OTHER_ARTIFACT_ID,
        data=HTML_BYTES,
        media_type="text/html",
        filename="report.html",
        storage_key=html_key,
    )
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=DELETED_ARTIFACT_ID,
        data=SVG_BYTES,
        media_type="image/svg+xml",
        filename="vector.svg",
        storage_key=svg_key,
    )
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=TRANSCRIPT_ARTIFACT_ID,
        data=HTML_BYTES,
        media_type="text/html\r\nx-injected: 1",
        filename=None,
        storage_key=broken_key,
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        image = await client.get(
            f"/api/v1/webchat/artifacts/{ARTIFACT_ID}/content", headers=_chat_headers()
        )
        html = await client.get(
            f"/api/v1/webchat/artifacts/{OTHER_ARTIFACT_ID}/content", headers=_chat_headers()
        )
        svg = await client.get(
            f"/api/v1/webchat/artifacts/{DELETED_ARTIFACT_ID}/content", headers=_chat_headers()
        )
        broken = await client.get(
            f"/api/v1/webchat/artifacts/{TRANSCRIPT_ARTIFACT_ID}/content", headers=_chat_headers()
        )

    assert image.status_code == 200, image.text
    assert image.content == IMAGE_BYTES
    assert image.headers["content-type"] == "image/png"
    assert image.headers["content-disposition"] == "inline; filename*=utf-8''%E6%88%AA%E5%9B%BE.png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["cache-control"] == "no-store"

    for response, expected_type, expected_name in (
        (html, "text/html; charset=utf-8", "report.html"),
        (svg, "image/svg+xml", "vector.svg"),
    ):
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == expected_type
        assert response.headers["content-disposition"] == f'attachment; filename="{expected_name}"', (
            "HTML/SVG 等可执行内容默认附件下载，禁止同源脚本执行"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
    assert html.content == HTML_BYTES and svg.content == SVG_BYTES

    # 坏媒体类型：绝不内联、绝不进响应头（降级为二进制流），字节仍可下载。
    assert broken.status_code == 200, broken.text
    assert broken.headers["content-type"] == "application/octet-stream"
    assert broken.headers["content-disposition"] == "attachment"
    assert broken.content == HTML_BYTES
    assert "x-injected" not in broken.headers


async def test_b255_cross_user_cross_tenant_and_deleted_artifacts_are_refused(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """他主体/跨租户/软删/内部留档一律 404，响应体不可分辨存在性，且不读一个字节。"""
    caplog.set_level(logging.DEBUG)
    store = _TrackingStore(artifact_root)
    # 真实 Owner 语义：他主体的产物在解析面就是 404（本桩按 user_id 裁决）。
    other = _seed(
        runtime_probe,
        artifact_root,
        artifact_id=OTHER_ARTIFACT_ID,
        data=CANARY_BYTES,
        media_type="application/pdf",
        filename="别人的报告.pdf",
        storage_key=CANARY_KEY,
        user_id=OTHER_PLATFORM_USER_ID,
    )
    assert other.user_id == OTHER_PLATFORM_USER_ID
    for artifact_id in (DELETED_ARTIFACT_ID, TRANSCRIPT_ARTIFACT_ID, uuid.uuid4()):
        runtime_probe.not_found.add(str(artifact_id))

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        responses = [
            await client.get(
                f"/api/v1/webchat/artifacts/{artifact_id}/content", headers=_chat_headers()
            )
            for artifact_id in (
                OTHER_ARTIFACT_ID,
                DELETED_ARTIFACT_ID,
                TRANSCRIPT_ARTIFACT_ID,
                uuid.uuid4(),
            )
        ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert [response.json()["code"] for response in responses] == ["COMMON_NOT_FOUND"] * 4
    signatures = {
        (response.status_code, response.json()["code"], response.json()["msg"], response.json()["data"])
        for response in responses
    }
    assert len(signatures) == 1, "四种失败不可分辨（只有逐请求的 trace 元数据不同）"
    assert all(CANARY_BYTES not in response.content for response in responses)
    assert store.resolved_keys == [], "被拒的请求不得读取任何字节"
    assert CANARY_KEY not in "".join(response.text for response in responses)

    # 归属解析用的是**当前主体**（而不是产物 owner）：跨用户不可能借 artifact_id 取件。
    assert {call.user_id for call in runtime_probe.calls} == {str(PLATFORM_USER_ID)}
    assert all(call.headers["x-internal-service"] == INTERNAL_TOKEN for call in runtime_probe.calls)
    assert all(CANARY_BYTES.decode(errors="ignore") not in record.getMessage() for record in caplog.records)


async def test_b255_revoked_agent_and_disabled_entry_refuse_before_reading_bytes(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """当前授权复核：Agent 撤销 403、入口停用/换绑 404 —— 都在读字节之前。"""
    store = _TrackingStore(artifact_root)
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=PDF_BYTES,
        media_type="application/pdf",
        filename="报告.pdf",
    )

    # ① Console 的当前授权集合里已没有该 Agent（撤销）：403，不读字节。
    console_probe.agents = []
    async with _gateway_client(stub_console, runtime_probe, store) as client:
        revoked = await client.get(CONTENT_URL, headers=_chat_headers())
    assert revoked.status_code == 403, revoked.text
    assert revoked.json()["code"] == "FORBIDDEN"
    assert store.resolved_keys == []

    # ② 入口已停用（快照事实）：404，不泄露入口状态，也不读字节。
    console_probe.agents = [{"agent_id": str(AGENT_ID), "key": "webchat-agent", "name": "网页助手"}]
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=PDF_BYTES,
        media_type="application/pdf",
        filename="报告.pdf",
        entry_id=DISABLED_ENTRY_ID,
    )
    async with _gateway_client(stub_console, runtime_probe, store) as client:
        disabled = await client.get(CONTENT_URL, headers=_chat_headers())
    assert disabled.status_code == 404, disabled.text
    assert disabled.json()["code"] == "COMMON_NOT_FOUND"
    assert store.resolved_keys == []

    # ③ 入口当前绑定已换 Agent（两个 Agent 都仍获授权）：旧会话的字节不在新 Agent 名下交付（404）。
    console_probe.agents = [
        {"agent_id": str(AGENT_ID), "key": "webchat-agent", "name": "网页助手"},
        {"agent_id": str(OTHER_AGENT_ID), "key": "other-agent", "name": "另一个助手"},
    ]
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=PDF_BYTES,
        media_type="application/pdf",
        filename="报告.pdf",
        entry_id=OTHER_ENTRY_ID,
        agent_id=AGENT_ID,
    )
    async with _gateway_client(stub_console, runtime_probe, store) as client:
        rebound = await client.get(CONTENT_URL, headers=_chat_headers())
    assert rebound.status_code == 404, rebound.text
    assert rebound.json()["code"] == "COMMON_NOT_FOUND"
    assert store.resolved_keys == []

    # ④ 撤销的 CHAT 会话：主体门控在解析之前就拒（401，沿用 Console 口径）。
    console_probe.revoked_code = "CHAT_SESSION_REVOKED"
    async with _gateway_client(stub_console, runtime_probe, store) as client:
        revoked_session = await client.get(CONTENT_URL, headers=_chat_headers())
    assert revoked_session.status_code == 401
    assert revoked_session.json()["code"] == "CHAT_SESSION_REVOKED"
    assert store.resolved_keys == []


async def test_b255_client_supplied_storage_key_is_ignored_and_bad_bytes_are_refused(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """客户端 storage_key 无位置；键越界/字节缺失/长度不符一律 404（不按错误长度声明长度）。"""
    store = _TrackingStore(artifact_root)
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=PDF_BYTES,
        media_type="application/pdf",
        filename="报告.pdf",
    )
    _write_bytes(artifact_root, CANARY_KEY, CANARY_BYTES)
    mismatch_key = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"
    missing_key = f"runs/{RUN_ID}/attachments/{uuid.uuid4()}"

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        # ① 客户端提供的 storage_key 被忽略：仍然只取 Owner 解析出的那份字节。
        ignored = await client.get(
            CONTENT_URL,
            headers=_chat_headers(),
            params={"storage_key": CANARY_KEY, "path": CANARY_KEY},
        )
        # ② 行内长度与实际字节不符：视同"拿不到"（绝不按错误长度声明 Content-Length）。
        _seed(
            runtime_probe,
            artifact_root,
            artifact_id=OTHER_ARTIFACT_ID,
            data=PDF_BYTES,
            media_type="application/pdf",
            filename="报告.pdf",
            storage_key=mismatch_key,
            size=len(PDF_BYTES) + 1,
        )
        mismatch = await client.get(
            f"/api/v1/webchat/artifacts/{OTHER_ARTIFACT_ID}/content", headers=_chat_headers()
        )
        # ③ 键越界（逃出共享存储根）：视同不存在，不落字节也不读字节。
        runtime_probe.artifacts[str(DELETED_ARTIFACT_ID)] = _ArtifactRecord(
            user_id=PLATFORM_USER_ID,
            storage_key=ESCAPING_KEY,
            media_type="application/pdf",
            size=6,
            filename=None,
        )
        escaping = await client.get(
            f"/api/v1/webchat/artifacts/{DELETED_ARTIFACT_ID}/content", headers=_chat_headers()
        )
        # ④ 字节在磁盘上缺失（清理过/盘掉过）：对取件者同样是"没有"。
        _seed(
            runtime_probe,
            artifact_root,
            artifact_id=TRANSCRIPT_ARTIFACT_ID,
            data=PDF_BYTES,
            media_type="application/pdf",
            filename=None,
            storage_key=missing_key,
        )
        (artifact_root / missing_key).unlink()
        missing = await client.get(
            f"/api/v1/webchat/artifacts/{TRANSCRIPT_ARTIFACT_ID}/content", headers=_chat_headers()
        )

    assert ignored.status_code == 200, ignored.text
    assert ignored.content == PDF_BYTES, "取件键只来自 Owner 解析，客户端给的键不参与取件"
    assert CANARY_BYTES not in ignored.content
    assert (artifact_root / CANARY_KEY).read_bytes() == CANARY_BYTES, "客户端点名的键从未被读取"
    assert [response.status_code for response in (mismatch, escaping, missing)] == [404, 404, 404]
    assert [response.json()["code"] for response in (mismatch, escaping, missing)] == [
        "COMMON_NOT_FOUND"
    ] * 3
    assert store.resolved_keys == [STORAGE_KEY, mismatch_key, ESCAPING_KEY, missing_key]
    assert all(CANARY_BYTES not in response.content for response in (mismatch, escaping, missing))


async def test_b255_owner_resolution_completes_before_shared_bytes_are_read(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """先核权限、释放 DB 事务，再流式输出：Owner 解析完整结束之后才读共享字节。"""
    store = _TrackingStore(artifact_root)
    _seed(
        runtime_probe,
        artifact_root,
        artifact_id=ARTIFACT_ID,
        data=PDF_BYTES,
        media_type="application/pdf",
        filename="报告.pdf",
    )

    async with _gateway_client(stub_console, runtime_probe, store) as client:
        response = await client.get(CONTENT_URL, headers=_chat_headers())

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert runtime_probe.resolve_started_at > 0.0 and runtime_probe.resolve_finished_at > 0.0
    assert len(store.resolved_at) == 1
    assert runtime_probe.resolve_finished_at <= store.resolved_at[0], (
        "归属解析（Owner 短事务）必须完整结束之后，网关才去读共享字节"
    )
    assert len(runtime_probe.calls) == 1, "一次下载只解析一次（逐请求授权，不重复往返）"


# --- B-255：不持库 --------------------------------------------------------------------------


def test_b255_download_modules_hold_no_database() -> None:
    """网关不持库：下载面模块零 sqlalchemy（与架构守卫同一口径）。"""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "apps/im-gateway/src/muad_im_gateway/application/webchat_artifacts.py",
        "apps/im-gateway/src/muad_im_gateway/api/webchat.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, relative
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("sqlalchemy" not in alias.name for alias in node.names), relative
            elif isinstance(node, ast.ImportFrom):
                assert "sqlalchemy" not in (node.module or ""), relative
