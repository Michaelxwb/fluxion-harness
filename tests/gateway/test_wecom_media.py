"""[TASK-002] 企微入站分流 + 媒体取件。

两条验收在这里落地：

- **S-08**：`mixed` 图文混排内**每个** `image` 项各成一个媒体引用，`text` 项并入消息文本；
  引用与"同消息单图"同型同序，因此数量门控对两者的计数口径必然一致（门控调用本身属落盘侧的
  编排，见 TASK-004）。
- **E-04**：密钥缺失与密钥不匹配两条解密失败路径。**不 mock 解密**——用真实 AES-256-CBC 密文、
  真实 HTTP 服务、官方 `decrypt_file`；断言失败明确、**绝不退化为返回密文**，且日志里不出现
  `aes_key` 与媒体明文 URL。

真实边界只用到一个本地 HTTP 服务（TASK-002 不写盘、不写库，故没有 PG/Redis 参与）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from muad_contracts import BotSnapshotItem
from muad_im_gateway.channels.wecom.adapter import WeComAdapter, _AibotClientPort
from muad_im_gateway.channels.wecom.media import (
    _filename_from_disposition,
    _media_type,
    download_media,
)
from muad_im_gateway.channels.wecom.sdk_port import (
    WeComMediaContent,
    WeComMediaDecryptError,
    WeComMediaNetworkError,
    WeComMediaRef,
    WeComMediaTimeoutError,
    WeComMediaTooLargeError,
)

BOT_ID = "bot-1"
AES_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
WRONG_AES_KEY = base64.b64encode(b"fedcba9876543210fedcba9876543210").decode()
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"pixels" * 4
SERVER_CHUNK_BYTES = 64 * 1024


# --------------------------------------------------------------------------- 帧


def text_frame(content: str = "hi", message_id: str = "m-text") -> dict[str, object]:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-1"},
        "body": {
            "msgid": message_id,
            "chatid": "conv-1",
            "from": {"userid": "ext-1"},
            "msgtype": "text",
            "text": {"content": content},
        },
    }


def image_frame(url: str, *, aeskey: str | None = None, message_id: str = "m-image") -> dict[str, Any]:
    image: dict[str, object] = {"url": url}
    if aeskey is not None:
        image["aeskey"] = aeskey
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-2"},
        "body": {
            "msgid": message_id,
            "from": {"userid": "ext-1"},
            "msgtype": "image",
            "image": image,
        },
    }


def mixed_frame(*, text: str = "看看这两张", message_id: str = "m-mixed") -> dict[str, Any]:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-3"},
        "body": {
            "msgid": message_id,
            "chatid": "conv-1",
            "from": {"userid": "ext-1"},
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": text}},
                    {"msgtype": "image", "image": {"url": "https://cdn.invalid/1", "aeskey": "k-1"}},
                    {"msgtype": "image", "image": {"url": "https://cdn.invalid/2"}},
                ]
            },
        },
    }


def unsupported_frame(msgtype: str) -> dict[str, Any]:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-4"},
        "body": {
            "msgid": f"m-{msgtype}",
            "from": {"userid": "ext-1"},
            "msgtype": msgtype,
        },
    }


class StubRawClient:
    """承载 `_AibotClientPort` 的原始 SDK 客户端替身：只记录回调注册，取件不走它。"""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.connected = True

    @property
    def is_connected(self) -> bool:
        return self.connected

    def on(self, event: str, handler: Callable[..., Any]) -> object:
        self.handlers[event] = handler
        return self

    async def connect(self) -> object:
        return self

    def disconnect(self) -> None:
        self.connected = False

    async def send_message(self, chatid: str, body: dict[str, object]) -> object:
        return {}

    async def reply_stream(
        self, frame: dict[str, object], stream_id: str, content: str, finish: bool = False
    ) -> object:
        return {}


def build_port(raw: StubRawClient) -> _AibotClientPort:
    return _AibotClientPort(raw, bot_id=BOT_ID)


@asynccontextmanager
async def adapter_for(raw: StubRawClient) -> AsyncIterator[WeComAdapter]:
    """把**真实** `_AibotClientPort` 接到适配器上：帧 → 分流解析走完整真实路径，不 mock。"""
    bot = BotSnapshotItem(
        bot_account_id=uuid4(), bot_id=BOT_ID, secret="s", agent_id=uuid4(), enabled=True
    )
    adapter = WeComAdapter(sdk_factory=lambda _bot_id, _secret: build_port(raw), bots=(bot,))
    await adapter.start()
    try:
        yield adapter
    finally:
        await adapter.stop()


async def next_envelope(adapter: WeComAdapter) -> Any:
    events = await adapter.iter_events()
    return await asyncio.wait_for(events.__anext__(), timeout=2)


# ------------------------------------------------------------------- 本地真实服务


class MediaServer:
    """本地真实 HTTP 服务：按路径返回字节，可配置文件名、响应前延迟与发送节流。"""

    def __init__(self) -> None:
        self.routes: dict[str, bytes] = {}
        self.filenames: dict[str, str] = {}
        self.delays: dict[str, float] = {}
        self.chunk_delay = 0.0
        self.sent: dict[str, int] = {}
        handler = _media_handler(self)
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve(self, path: str, body: bytes, *, filename: str | None = None, delay: float = 0.0) -> str:
        self.routes[path] = body
        self.delays[path] = delay
        if filename is not None:
            self.filenames[path] = filename
        return f"{self.base_url}{path}"

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _media_handler(media: MediaServer) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
            body = media.routes.get(self.path)
            if body is None:
                self.send_error(404)
                return
            delay = media.delays.get(self.path, 0.0)
            if delay:
                time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            filename = media.filenames.get(self.path)
            if filename is not None:
                # 非 ASCII 文件名只能走 RFC 5987：`send_header` 按 latin-1 编码，直接塞中文会抛
                self.send_header("Content-Disposition", _disposition(filename))
            self.end_headers()
            written = 0
            try:
                for offset in range(0, len(body), SERVER_CHUNK_BYTES):
                    chunk = body[offset : offset + SERVER_CHUNK_BYTES]
                    self.wfile.write(chunk)
                    written += len(chunk)
                    if media.chunk_delay:
                        time.sleep(media.chunk_delay)
            except (BrokenPipeError, ConnectionResetError):
                # 客户端在超上限时主动断开——这正是"没读完"的证据
                pass
            finally:
                media.sent[self.path] = written

        def log_message(self, *args: object) -> None:
            """静音默认的 stderr 访问日志。"""

    return Handler


@pytest.fixture
def media_server() -> Iterator[MediaServer]:
    server = MediaServer()
    try:
        yield server
    finally:
        server.close()


def _disposition(filename: str) -> str:
    """按服务端实际做法构造 `Content-Disposition`：ASCII 走 `filename=`，非 ASCII 走 RFC 5987。"""
    if filename.isascii():
        return f'attachment; filename="{filename}"'
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def wecom_ciphertext(plaintext: bytes, *, aes_key: str = AES_KEY) -> bytes:
    """按官方算法产出**真实密文**：AES-256-CBC，IV = 密钥前 16 字节，PKCS#7 填充。"""
    key = base64.b64decode(aes_key)
    pad_len = 16 - len(plaintext) % 16
    padded = plaintext + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    """把捕获到的日志拍平成文本（消息 + 参数），用于断言凭据不出现在日志里。"""
    return "\n".join(f"{record.getMessage()} {record.args!r}" for record in caplog.records)


# ------------------------------------------------------------------ S-08 分流


@pytest.mark.unit
async def test_mixed_message_yields_one_ref_per_image_item_and_merges_text() -> None:
    """S-08：`mixed` 的每个 image 项各成一个引用，text 项并入消息文本。"""
    raw = StubRawClient()
    received: list[Any] = []
    build_port(raw).on_message(received.append)

    raw.handlers["message"](mixed_frame())

    assert len(received) == 1
    message = received[0]
    assert message.message_type == "mixed"
    assert message.text == "看看这两张"
    assert message.media == (
        WeComMediaRef(url="https://cdn.invalid/1", aes_key="k-1"),
        WeComMediaRef(url="https://cdn.invalid/2", aes_key=None),
    )
    assert message.unsupported_media is None


@pytest.mark.unit
async def test_mixed_refs_are_the_same_shape_as_separate_image_messages() -> None:
    """S-08：`mixed` 的图片项与"同消息单图"产出**同型同序**的引用 ⇒ 数量门控计数口径一致。"""
    raw = StubRawClient()
    received: list[Any] = []
    build_port(raw).on_message(received.append)

    raw.handlers["message"](image_frame("https://cdn.invalid/1", aeskey="k-1", message_id="m-1"))
    raw.handlers["message"](image_frame("https://cdn.invalid/2", message_id="m-2"))
    raw.handlers["message"](mixed_frame())

    separate = received[0].media + received[1].media
    mixed = received[2].media
    assert mixed == separate, "mixed 内的图片项必须是同一种候选，否则门控会漏计"
    assert len(mixed) == 2


@pytest.mark.unit
async def test_text_message_keeps_its_existing_shape() -> None:
    """回归：文本路径逐字不变——没有媒体引用，也没有 unsupported 标记。"""
    raw = StubRawClient()
    received: list[Any] = []
    build_port(raw).on_message(received.append)

    raw.handlers["message"](text_frame())

    assert len(received) == 1
    assert received[0].message_type == "text"
    assert received[0].text == "hi"
    assert received[0].media == ()
    assert received[0].unsupported_media is None


@pytest.mark.unit
@pytest.mark.parametrize("msgtype", ["voice", "video"])
async def test_voice_and_video_are_marked_unsupported_without_media(msgtype: str) -> None:
    """RULE-01：不接收的形态不得静默消失——以 `unsupported_media` 送达核心域，且不产生取件。"""
    raw = StubRawClient()
    received: list[Any] = []
    build_port(raw).on_message(received.append)

    raw.handlers["message"](unsupported_frame(msgtype))

    assert len(received) == 1
    assert received[0].unsupported_media == msgtype.upper()
    assert received[0].media == ()
    assert received[0].text == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "frame",
    [
        "not-a-frame",
        {"body": {}},
        {"body": {"msgtype": "image", "msgid": "m-1", "from": {"userid": "ext-1"}}},
        {"body": {"msgtype": "text", "msgid": "", "from": {"userid": "ext-1"}}},
        {"body": {"msgtype": "text", "msgid": "m-1", "from": {}}},
        {"body": {"msgtype": "text", "msgid": "m-1", "from": {"userid": "ext-1"}, "text": {}}},
    ],
)
async def test_structurally_invalid_frames_are_still_ignored(frame: object) -> None:
    """结构不合法 ≠ "收不了的消息"：无法归属的帧（缺 msgid/from、图片缺 url）仍不产生消息。"""
    raw = StubRawClient()
    received: list[Any] = []
    build_port(raw).on_message(received.append)

    raw.handlers["message"](frame)

    assert received == []


@pytest.mark.integration
async def test_adapter_emits_envelope_with_merged_text_and_no_attachments() -> None:
    """S-08 的边界侧：`mixed` 走到 `ChannelEnvelope` 时文本已并入、`attachments` 尚无内容。

    附件引用只描述"已经拿到手的字节"（AD-8），此刻还没取件落盘，故必为空。
    """
    raw = StubRawClient()
    async with adapter_for(raw) as adapter:
        raw.handlers["message"](mixed_frame())
        envelope = await next_envelope(adapter)

    assert envelope.text == "看看这两张"
    assert envelope.attachments == []
    assert envelope.unsupported_media is None


@pytest.mark.integration
async def test_adapter_emits_envelope_for_unsupported_media() -> None:
    """RULE-01 的边界侧：语音消息**不再**在渠道边界消失，而是带 `unsupported_media` 送达。"""
    raw = StubRawClient()
    async with adapter_for(raw) as adapter:
        raw.handlers["message"](unsupported_frame("voice"))
        envelope = await next_envelope(adapter)

    assert envelope.unsupported_media == "VOICE"
    assert envelope.attachments == []


# --------------------------------------------------------------- E-04 取件/解密


@pytest.mark.integration
async def test_port_downloads_decrypts_and_reports_real_metadata(media_server: MediaServer) -> None:
    """取件走真实 HTTP + 真实解密：字节、类型、文件名、校验和都要对得上。"""
    url = media_server.serve(
        "/ok", wecom_ciphertext(PNG_BYTES), filename="截图 1.png"
    )
    raw = StubRawClient()

    content = await build_port(raw).download_media(url, AES_KEY, max_bytes=1024 * 1024)

    assert isinstance(content, WeComMediaContent)
    assert content.data == PNG_BYTES
    assert content.media_type == "image/png"
    assert content.filename == "截图 1.png"
    assert content.checksum == hashlib.sha256(PNG_BYTES).hexdigest()


@pytest.mark.integration
async def test_checksum_is_computed_after_decryption(media_server: MediaServer) -> None:
    """checksum 必须是**解密后**字节的摘要：同一明文用不同密钥加密，校验和必须相同。"""
    plaintext = b"%PDF-1.7 body"
    key_b = base64.b64encode(b"abcdefghijklmnopqrstuvwxyz012345").decode()
    first = media_server.serve("/a", wecom_ciphertext(plaintext, aes_key=AES_KEY))
    second = media_server.serve("/b", wecom_ciphertext(plaintext, aes_key=key_b))

    content_a = await download_media(first, AES_KEY, max_bytes=1024 * 1024)
    content_b = await download_media(second, key_b, max_bytes=1024 * 1024)

    assert content_a.data == content_b.data == plaintext
    assert content_a.checksum == content_b.checksum == hashlib.sha256(plaintext).hexdigest()


@pytest.mark.integration
@pytest.mark.parametrize("aes_key", [None, WRONG_AES_KEY])
async def test_decrypt_failures_are_explicit_and_never_return_ciphertext(
    media_server: MediaServer, aes_key: str | None
) -> None:
    """E-04：密钥缺失与密钥不匹配都必须**明确失败**，绝不把密文当文件返回。"""
    ciphertext = wecom_ciphertext(PNG_BYTES)
    url = media_server.serve("/secret", ciphertext)

    with pytest.raises(WeComMediaDecryptError) as failure:
        await download_media(url, aes_key, max_bytes=1024 * 1024)

    # 失败不得带上任何"半成品"：既没有返回值，异常文本里也没有密文/明文/取件凭据
    message = str(failure.value)
    assert message in {"wecom media aeskey missing", "wecom media decrypt failed"}
    assert url not in message


@pytest.mark.integration
async def test_decrypt_failure_logs_reason_without_credentials(
    media_server: MediaServer, caplog: pytest.LogCaptureFixture
) -> None:
    """E-04：失败要留日志（否则"日志里没有凭据"是个永远为真的断言），但日志里没有密钥与 URL。"""
    url = media_server.serve("/secret", wecom_ciphertext(PNG_BYTES))
    caplog.set_level(logging.INFO)

    with pytest.raises(WeComMediaDecryptError):
        await download_media(url, None, max_bytes=1024 * 1024)

    text = log_text(caplog)
    assert "wecom_media_failed" in text and "WeComMediaDecryptError" in text
    assert AES_KEY not in text
    assert url not in text


@pytest.mark.integration
async def test_success_logs_metadata_without_credentials(
    media_server: MediaServer, caplog: pytest.LogCaptureFixture
) -> None:
    """E-04 的另一半：成功路径留的日志同样只有类型与字节数。"""
    url = media_server.serve("/ok", wecom_ciphertext(PNG_BYTES))
    caplog.set_level(logging.INFO)

    await download_media(url, AES_KEY, max_bytes=1024 * 1024)

    text = log_text(caplog)
    assert "wecom_media_downloaded" in text and "image/png" in text
    assert AES_KEY not in text
    assert url not in text


@pytest.mark.integration
async def test_oversized_body_is_aborted_before_the_whole_response_is_read(
    media_server: MediaServer,
) -> None:
    """大小上限在**流式读取时**执行：超限立即中止，服务端因此写不完整个响应体。"""
    body = b"x" * (8 * 1024 * 1024)
    url = media_server.serve("/huge", body)
    media_server.chunk_delay = 0.002

    with pytest.raises(WeComMediaTooLargeError) as failure:
        await download_media(url, AES_KEY, max_bytes=1024 * 1024)

    assert failure.value.max_bytes == 1024 * 1024
    path = url.split(media_server.base_url, 1)[1]
    deadline = time.monotonic() + 2.0
    while path not in media_server.sent and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert path in media_server.sent, "服务端未记录到写出的字节数，断言会退化为恒真"
    assert media_server.sent[path] < len(body), "超限必须中止读取，而不是读完再判"


@pytest.mark.integration
async def test_slow_response_hits_the_timeout(media_server: MediaServer) -> None:
    """E-03 同族：下载超时必须以超时类型上抛，而不是挂住消息链路。"""
    url = media_server.serve("/slow", wecom_ciphertext(PNG_BYTES), delay=3.0)

    with pytest.raises(WeComMediaTimeoutError):
        await download_media(url, AES_KEY, max_bytes=1024 * 1024, timeout_sec=0.2)


@pytest.mark.integration
async def test_missing_media_url_is_a_network_failure(media_server: MediaServer) -> None:
    """E-03：URL 失效（404）归为网络类失败，与解密失败可区分。"""
    url = f"{media_server.base_url}/gone"

    with pytest.raises(WeComMediaNetworkError):
        await download_media(url, AES_KEY, max_bytes=1024 * 1024)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("plaintext", "filename", "expected"),
    [
        (PNG_BYTES, None, "image/png"),
        (b"\xff\xd8\xff\xe0jpeg", None, "image/jpeg"),
        (b"GIF89a data", None, "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", None, "image/webp"),
        (b"%PDF-1.7", None, "application/pdf"),
        (
            b"PK\x03\x04 docx",
            "季度报告.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (b"no magic at all", "notes.md", "text/markdown"),
        (b"no magic at all", None, "application/octet-stream"),
        (b"no magic at all", "archive.bin", "application/octet-stream"),
    ],
)
def test_media_type_is_detected_after_decryption(
    plaintext: bytes, filename: str | None, expected: str
) -> None:
    """类型判定顺序：解密字节的魔术字节 → 文件名扩展名 → `application/octet-stream` 兜底。

    兜底值不在门控白名单内 ⇒ 由实检拒绝并反馈，不会静默放行。
    """
    assert _media_type(plaintext, filename) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ('attachment; filename="report.pdf"', "report.pdf"),
        ("attachment; filename=report.pdf", "report.pdf"),
        ("attachment; filename*=UTF-8''%E6%88%AA%E5%9B%BE%201.png", "截图 1.png"),
        (
            'attachment; filename="fallback.png"; filename*=UTF-8\'\'%E6%88%AA%E5%9B%BE.png',
            "截图.png",
        ),
    ],
)
def test_filename_parsing_covers_both_disposition_forms(header: str | None, expected: str | None) -> None:
    """RFC 5987 形式优先于 `filename=`：真实服务对非 ASCII 名只发前者。"""
    assert _filename_from_disposition(header) == expected
