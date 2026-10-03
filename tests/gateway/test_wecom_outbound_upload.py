"""[TASK-006] 企微出站媒体：三步分片上传 + 直发。

协议口径**全部来自 TASK-001 真机实测**（设计 §3.6 事实表）：`init → chunk × N → finish`，
分片 ≤512 KiB、≤100 片，`chunk_index` **0-based**（Node SDK 的类型注释与它自己的实现矛盾，
真机判 0-based）。官方 **Python** SDK 没有这套能力（`aibot` 1.0.2 = PyPI 最新，只有下载），
所以由本仓按官方 Node SDK 的协议驱动。

真实边界：真实文件系统（产物字节真落盘、真按 `storage_key` 读回）；SDK 客户端是替身
（真机协议已由 TASK-001 的探针证过，这里验的是**驱动它的那串帧对不对**）。
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef, BotSnapshotItem, DeliveryRouteInput
from muad_im_gateway.channels.base import (
    ARTIFACT_DEGRADED,
    ARTIFACT_DELIVERED,
    ArtifactDeliveryError,
)
from muad_im_gateway.channels.wecom.adapter import WeComAdapter, _AibotClientPort
from muad_im_gateway.channels.wecom.media import (
    MAX_UPLOAD_CHUNKS,
    UPLOAD_CHUNK_BYTES,
    chunk_upload,
    uploadable_media_type,
)
from muad_im_gateway.channels.wecom.sdk_port import WeComMediaUploadTooLargeError

BOT_ID = "bot-outbound-1"
ROUTE = DeliveryRouteInput(channel="WECOM", bot_id=BOT_ID, external_user_id="ext-1")
#: 交付调用的租户作用域：降级签链接要用它做归属校验（不是渠道形状）。
TENANT = "tenant-outbound-1"


class _RecordingClient:
    """记录所有出站帧的 SDK 替身：上传三步与媒体发送都从它这里过。

    `reply(frame, body, cmd)` 是官方 SDK 的**公开**通用方法（可带任意 cmd）——本仓就是靠它
    驱动上传的，所以替身只要实现它，就在验证"我们发出去的到底是什么帧"。
    """

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        #: 依次记下 (cmd, body, req_id)
        self.frames: list[tuple[str | None, dict[str, Any], str]] = []

    @property
    def is_connected(self) -> bool:
        return True

    def on(self, event: str, handler: Any) -> object:
        self.handlers[event] = handler
        return self

    async def connect(self) -> object:
        handler = self.handlers.get("authenticated")
        if handler is not None:
            handler()  # 认证回调是"进 CONNECTED"的唯一开关
        return self

    def disconnect(self) -> None: ...

    async def reply(
        self, frame: dict[str, Any], body: dict[str, Any], cmd: str | None = None
    ) -> object:
        self.frames.append((cmd, body, str((frame.get("headers") or {}).get("req_id") or "")))
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1", "type": body.get("type")}}
        return {"errcode": 0}

    async def reply_stream(
        self, frame: dict[str, Any], stream_id: str, content: str, finish: bool = False
    ) -> object:
        return {}

    async def send_message(self, chatid: str, body: dict[str, Any]) -> object:
        self.frames.append(("aibot_send_msg", {"chatid": chatid, **body}, ""))
        return {}


class _RecordingIssuer:
    """`ArtifactLinkIssuer` 的替身：记录被问过谁，按脚本返回链接或 `None`。

    真实实现是 `ConsoleClient.issue_fetch_link`（真 HTTP 打 Console 的内部端点）——
    那条链路由 TASK-008 的 S-09 端到端验过；这里要验的是**适配器怎么用它**。
    """

    def __init__(self, url: str | None = "https://console.invalid/artifacts/x/content?token=t") -> None:
        self.url = url
        self.asked: list[tuple[uuid.UUID | None, str]] = []

    async def issue_fetch_link(self, artifact_id: uuid.UUID, *, tenant_id: str) -> str | None:
        self.asked.append((artifact_id, tenant_id))
        return self.url


def _adapter(
    client: _RecordingClient,
    *,
    store: NfsArtifactStore | None,
    fetch_links: _RecordingIssuer | None = None,
) -> WeComAdapter:
    bot = BotSnapshotItem(
        bot_account_id=uuid.uuid4(), bot_id=BOT_ID, secret="s", agent_id=uuid.uuid4(), enabled=True
    )
    return WeComAdapter(
        sdk_factory=lambda _bot_id, _secret: _AibotClientPort(client, bot_id=BOT_ID),
        bots=(bot,),
        artifact_store=store,
        fetch_links=fetch_links,
    )


@asynccontextmanager
async def _running(
    client: _RecordingClient,
    *,
    store: NfsArtifactStore | None,
    fetch_links: _RecordingIssuer | None = None,
) -> AsyncIterator[WeComAdapter]:
    """起适配器并在用例结束时**停掉它**：`_BotConnection` 的监督任务是长驻的，
    不收尾就会留下 "Task was destroyed but it is pending" 并污染同批的其他用例。"""
    adapter = _adapter(client, store=store, fetch_links=fetch_links)
    await adapter.start()
    try:
        yield adapter
    finally:
        await adapter.stop()


def _artifact_ref(storage_key: str, *, kind: str = "DOCUMENT") -> AttachmentRef:
    return AttachmentRef(
        storage_key=storage_key,
        kind=kind,  # type: ignore[arg-type]
        media_type="image/png" if kind == "IMAGE" else "text/markdown",
        size=12,
        filename="报告.png" if kind == "IMAGE" else "汇总.md",
        checksum="sha256:" + "0" * 64,
        artifact_id=uuid.uuid4(),
    )


# --------------------------------------------------------------------- 纯函数口径


def test_chunk_upload_splits_by_the_official_chunk_size() -> None:
    """分片口径：**512 KiB/片**，按 base64 编码**前**的字节切。"""
    data = b"x" * (UPLOAD_CHUNK_BYTES * 2 + 10)

    chunks = chunk_upload(data)

    assert [len(chunk) for chunk in chunks] == [UPLOAD_CHUNK_BYTES, UPLOAD_CHUNK_BYTES, 10]
    assert b"".join(chunks) == data, "切片必须无损，否则用户拿到的是被拼错的文件"


def test_chunk_upload_refuses_more_chunks_than_the_channel_allows() -> None:
    """超 ≈50 MB 是**渠道硬上限**：抛错，不截断 —— 截断会发出去一个被砍一半的文件。"""
    data = b"x" * (UPLOAD_CHUNK_BYTES * (MAX_UPLOAD_CHUNKS + 1))

    with pytest.raises(WeComMediaUploadTooLargeError):
        chunk_upload(data)


def test_uploadable_media_type_only_knows_image_and_file() -> None:
    """企微只收 `image`/`file` 两种形态：图片按 `kind` 判（与入站同口径），其余当文件。"""
    assert uploadable_media_type("IMAGE") == "image"
    assert uploadable_media_type("DOCUMENT") == "file"
    assert uploadable_media_type("OTHER") == "file"


# --------------------------------------------------------------------- 三步驱动


async def test_upload_drives_init_chunk_finish_with_zero_based_indices() -> None:
    """帧序列必须是 `init → chunk×N → finish`，且 `chunk_index` **从 0 起**。"""
    client = _RecordingClient()
    port = _AibotClientPort(client, bot_id=BOT_ID)
    data = b"a" * (UPLOAD_CHUNK_BYTES + 5)

    media_id = await port.upload_media(data, media_type="file", filename="大文件.md")

    assert media_id == "media-1"
    commands = [cmd for cmd, _body, _req in client.frames]
    assert commands == [
        "aibot_upload_media_init",
        "aibot_upload_media_chunk",
        "aibot_upload_media_chunk",
        "aibot_upload_media_finish",
    ]

    init_body = client.frames[0][1]
    assert init_body["total_chunks"] == 2
    assert init_body["total_size"] == len(data)
    assert init_body["md5"] == hashlib.md5(data).hexdigest()
    assert init_body["type"] == "file"

    chunk_bodies = [body for cmd, body, _req in client.frames if cmd == "aibot_upload_media_chunk"]
    assert [body["chunk_index"] for body in chunk_bodies] == [0, 1], "真机判 0-based"
    assert base64.b64decode(str(chunk_bodies[0]["base64_data"])) == data[:UPLOAD_CHUNK_BYTES]

    assert client.frames[-1][1]["upload_id"] == "upload-1"


# --------------------------------------------------------------------- 交付落点


async def test_deliver_artifact_reads_bytes_and_sends_media(tmp_path: Path) -> None:
    """适配器按 `storage_key` **直读共享 store**，上传后以媒体形态发出（字节不经核心域搬运）。"""
    store = NfsArtifactStore(tmp_path)
    storage_key = "outbound/run-1/artifact-1/v1"
    payload = b"\x89PNG-outbound"
    target = store.resolve(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    client = _RecordingClient()
    issuer = _RecordingIssuer()

    async with _running(client, store=store, fetch_links=issuer) as adapter:
        outcome = await adapter.deliver_artifact(
            ROUTE, _artifact_ref(storage_key, kind="IMAGE"), tenant_id=TENANT
        )

    assert outcome.outcome == ARTIFACT_DELIVERED
    init_body = client.frames[0][1]
    assert init_body["type"] == "image", "kind=IMAGE 要按图片形态发"
    assert init_body["total_size"] == len(payload), "上传的必须是 store 里那份字节"
    assert init_body["filename"] == "报告.png", "文件名要带给用户"
    assert issuer.asked == [], "发得出去就不该去要链接——降级是**兜底**，不是常规路径"


async def test_deliver_artifact_without_a_store_fails_explicitly() -> None:
    """没配 `ARTIFACT_ROOT` = 部署漏配：**显式失败**，不静默吞掉这次的交付。"""
    client = _RecordingClient()
    issuer = _RecordingIssuer()

    async with _running(client, store=None, fetch_links=issuer) as adapter:
        with pytest.raises(ArtifactDeliveryError):
            await adapter.deliver_artifact(ROUTE, _artifact_ref("outbound/run-1/x/v1"), tenant_id=TENANT)

    assert issuer.asked == [], "字节都读不到，签链接也救不回来：不该白问一次"


async def test_deliver_artifact_over_the_channel_cap_degrades_to_a_fetch_link(
    tmp_path: Path,
) -> None:
    """超 ≈50 MB（**渠道硬上限**）⇒ 降级：**把链接作为文本发给用户**，并如实标 `DEGRADED`。

    这条路径的关键不是"没报错"，而是三件事同时成立：
    ① 用户**真的收到了东西**（一条可点的链接），② 返回的结局是 `DEGRADED` 而不是 `DELIVERED`
    （调用方据此告诉模型"形态变了"），③ `fallback_url` 就是用户收到的那个 URL。
    少任何一条都会变成谎报：①没有=用户什么都没收到却说成功了，②没有=审计把降级记成直发。
    """
    store = NfsArtifactStore(tmp_path)
    storage_key = "outbound/run-1/big/v1"
    target = store.resolve(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * (UPLOAD_CHUNK_BYTES * (MAX_UPLOAD_CHUNKS + 1)))

    client = _RecordingClient()
    issuer = _RecordingIssuer("https://console.example/api/v1/artifacts/a1/content?token=tok")

    async with _running(client, store=store, fetch_links=issuer) as adapter:
        outcome = await adapter.deliver_artifact(
            ROUTE, _artifact_ref(storage_key), tenant_id=TENANT
        )

    assert outcome.outcome == ARTIFACT_DEGRADED
    assert outcome.fallback_url == issuer.url, "回给调用方的必须就是用户收到的那条链接"

    commands = [cmd for cmd, _body, _req in client.frames]
    assert commands == ["aibot_send_msg"], f"降级只发一条文本，不上传：{commands}"
    body = client.frames[0][1]
    assert body["msgtype"] == "markdown", "主动投递用 markdown 体（`text` 会被 40008 拒收）"
    assert body["markdown"]["content"] == issuer.url, "用户收到的正是那条链接"

    assert len(issuer.asked) == 1
    artifact_id, tenant_id = issuer.asked[0]
    assert tenant_id == TENANT, "签发必须带租户：取件端点按租户做归属校验"
    assert artifact_id is not None


async def test_deliver_artifact_over_the_cap_without_an_issuer_fails_explicitly(
    tmp_path: Path,
) -> None:
    """没接签发口（部署漏配）⇒ 超上限时**显式失败**，不自己拼一条像链接的串。

    拼一条出来的后果是用户点开一个 404——比直接报错更糟，而且它看起来"成功了"。
    """
    store = NfsArtifactStore(tmp_path)
    storage_key = "outbound/run-1/big/v1"
    target = store.resolve(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * (UPLOAD_CHUNK_BYTES * (MAX_UPLOAD_CHUNKS + 1)))

    client = _RecordingClient()

    async with _running(client, store=store, fetch_links=None) as adapter:
        with pytest.raises(ArtifactDeliveryError):
            await adapter.deliver_artifact(ROUTE, _artifact_ref(storage_key), tenant_id=TENANT)

    assert [cmd for cmd, _body, _req in client.frames] == [], "失败时一条帧都不该发出去"


async def test_deliver_artifact_over_the_cap_fails_when_no_link_can_be_issued(
    tmp_path: Path,
) -> None:
    """签发口**答不出来**（产物已清掉 / Console 不通）⇒ 仍是显式失败，不是"降级成功"。

    `DEGRADED` 的前提是用户确实收到了链接；签不出来还说降级，就是把 RULE-03 禁的那种谎报
    换了个位置。
    """
    store = NfsArtifactStore(tmp_path)
    storage_key = "outbound/run-1/big/v1"
    target = store.resolve(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * (UPLOAD_CHUNK_BYTES * (MAX_UPLOAD_CHUNKS + 1)))

    client = _RecordingClient()

    async with _running(client, store=store, fetch_links=_RecordingIssuer(None)) as adapter:
        with pytest.raises(ArtifactDeliveryError):
            await adapter.deliver_artifact(ROUTE, _artifact_ref(storage_key), tenant_id=TENANT)

    assert [cmd for cmd, _body, _req in client.frames] == [], "签不出链接就什么都不该发"
