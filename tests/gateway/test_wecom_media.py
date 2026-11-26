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
import ipaddress
import logging
import ssl
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from muad_contracts import BotSnapshotItem
from muad_im_connector.channels.wecom.adapter import WeComAdapter, _AibotClientPort
from muad_im_connector.channels.wecom.media import (
    _filename_from_disposition,
    _media_type,
    _safe_cause,
    build_media_ssl_context,
    download_media,
)
from muad_im_connector.channels.wecom.sdk_port import (
    WeComMediaContent,
    WeComMediaDecryptError,
    WeComMediaNetworkError,
    WeComMediaRef,
    WeComMediaTimeoutError,
    WeComMediaTooLargeError,
)

from tests.e2e.wecom_media_server import (
    AES_KEY,
    MediaServer,
    wecom_ciphertext,
)

BOT_ID = "bot-1"
WRONG_AES_KEY = base64.b64encode(b"fedcba9876543210fedcba9876543210").decode()
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"pixels" * 4


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


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    """把捕获到的日志拍平成文本（消息 + 参数），用于断言凭据不出现在日志里。"""
    return "\n".join(f"{record.getMessage()} {record.args!r}" for record in caplog.records)


@pytest.fixture
def media_server() -> Iterator[MediaServer]:
    server = MediaServer()
    try:
        yield server
    finally:
        server.close()


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
async def test_plaintext_at_the_limit_is_accepted(media_server: MediaServer) -> None:
    """边界：明文**恰好等于**上限必须收下。

    加密填充会让密文比明文长一个分组，所以"读的时候比密文、解密后又比明文"两处口径不一致时，
    恰好卡在边界上的合法附件会被自己的开销挤掉（2026-10-06 实测：1024 字节明文 → 1040 字节
    密文 ⇒ 报超限；生产 50 MiB 边界同理）。
    """
    plain = b"x" * 1024
    ciphertext = wecom_ciphertext(plain)
    assert len(ciphertext) > len(plain), "测试前提：密文确实比明文长（否则这条用例是恒真的）"
    url = media_server.serve("/at-limit", ciphertext)

    content = await download_media(url, AES_KEY, max_bytes=1024)

    assert content.data == plain


@pytest.mark.integration
async def test_plaintext_over_the_limit_is_still_rejected(media_server: MediaServer) -> None:
    """上限仍按**明文**判：多一个字节即超限（放宽的只是传输阶段的加密开销）。"""
    plain = b"x" * 1025
    url = media_server.serve("/over-limit", wecom_ciphertext(plain))

    with pytest.raises(WeComMediaTooLargeError) as failure:
        await download_media(url, AES_KEY, max_bytes=1024)

    assert failure.value.max_bytes == 1024


@pytest.mark.integration
async def test_oversized_body_is_aborted_before_the_whole_response_is_read(
    media_server: MediaServer,
) -> None:
    """大小上限在**流式读取时**执行：超限立即中止，服务端因此写不完整个响应体。

    上限说的是**明文**：流里流的是密文，所以读取时允许一个分组的加密开销（`+16`）——
    否则"恰好等于上限"的合法附件会被自己的填充挤掉。
    """
    body = b"x" * (8 * 1024 * 1024)
    url = media_server.serve("/huge", body)
    media_server.chunk_delay = 0.002

    with pytest.raises(WeComMediaTooLargeError) as failure:
        await download_media(url, AES_KEY, max_bytes=1024 * 1024)

    assert failure.value.max_bytes == 1024 * 1024  # 报给用户的是**产品上限**，不是含开销的读取上限
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


# --------------------------------------------------------------------------- TLS 版本上限
#
# 内网实测（2026-11-26）：connector 到企微媒体 CDN 的 TLS 1.3 握手被安全设备 RST，TLS 1.2 正常。
# 这里用**真实 TLS 栈**（本地自签服务）验证上限确实作用在握手层，而不是只断言参数被传下去。


def _self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """自签证书（CA + 127.0.0.1 SAN）：让测试客户端能真正验证服务端。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class _QuietServer(http.server.ThreadingHTTPServer):
    """TLS 握手失败发生在 accept 线程：默认会向 stderr 打 traceback，这里静音。"""

    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        pass


class TlsMediaServer:
    """真实 TLS 的媒体端点，协议版本区间由测试指定（只为把版本上限变成可判定的事实）。"""

    path = "/media"

    def __init__(
        self,
        cert_path: Path,
        key_path: Path,
        *,
        minimum: ssl.TLSVersion,
        maximum: ssl.TLSVersion,
    ) -> None:
        self.body = b""
        media = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
                if self.path != media.path:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(media.body)))
                self.end_headers()
                self.wfile.write(media.body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        context.minimum_version = minimum
        context.maximum_version = maximum
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"https://{host}:{port}{self.path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _trusting_context(cert_path: Path, *, maximum: ssl.TLSVersion | None = None) -> ssl.SSLContext:
    """信任测试自签 CA 的客户端 context；`maximum` 为空 = 不设上限。"""
    context = ssl.create_default_context(cafile=str(cert_path))
    if maximum is not None:
        context.maximum_version = maximum
    return context


@pytest.mark.unit
def test_no_cap_keeps_the_httpx_default() -> None:
    assert build_media_ssl_context(None) is None
    assert build_media_ssl_context("") is None


@pytest.mark.unit
def test_cap_keeps_certificate_verification_and_only_lowers_the_version() -> None:
    context = build_media_ssl_context("TLSv1_2")

    assert context is not None
    assert context.maximum_version is ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


@pytest.mark.unit
def test_unknown_cap_value_fails_loudly() -> None:
    with pytest.raises(ValueError, match="TLSv1_2"):
        build_media_ssl_context("TLSv1")


@pytest.mark.unit
def test_safe_cause_reports_the_innermost_reason_without_urls() -> None:
    outer = RuntimeError("wrapped")
    outer.__cause__ = ConnectionResetError(104, "Connection reset by peer")
    assert _safe_cause(outer) == "ConnectionResetError: [Errno 104] Connection reset by peer"

    leaky = RuntimeError("failed at https://cdn.test/media?q-signature=SECRET")
    text = _safe_cause(leaky)
    assert "SECRET" not in text and "<url>" in text

    long_text = _safe_cause(RuntimeError("x" * 400))
    assert long_text.endswith("…") and len(long_text) <= 220  # 200 文本 + 类型前缀 + 省略号


@pytest.mark.integration
async def test_capping_to_tls12_rejects_a_tls13_only_server(tmp_path: Path) -> None:
    """先把上限做错：TLS 1.3 独占的服务端 + 限到 1.2 的客户端必然握手失败。"""
    cert_path, key_path = _self_signed_cert(tmp_path)
    server = TlsMediaServer(
        cert_path,
        key_path,
        minimum=ssl.TLSVersion.TLSv1_3,
        maximum=ssl.TLSVersion.TLSv1_3,
    )
    server.body = wecom_ciphertext(PNG_BYTES)
    try:
        with pytest.raises(WeComMediaNetworkError):
            await download_media(
                server.url(),
                AES_KEY,
                max_bytes=1024 * 1024,
                timeout_sec=5.0,
                ssl_context=_trusting_context(cert_path, maximum=ssl.TLSVersion.TLSv1_2),
            )
    finally:
        server.close()


@pytest.mark.integration
async def test_same_server_is_reachable_without_the_cap(tmp_path: Path) -> None:
    """同一服务端、同一信任链，不设上限即可下载 —— 差异只来自版本上限。"""
    cert_path, key_path = _self_signed_cert(tmp_path)
    server = TlsMediaServer(
        cert_path,
        key_path,
        minimum=ssl.TLSVersion.TLSv1_3,
        maximum=ssl.TLSVersion.TLSv1_3,
    )
    server.body = wecom_ciphertext(PNG_BYTES)
    try:
        content = await download_media(
            server.url(),
            AES_KEY,
            max_bytes=1024 * 1024,
            timeout_sec=5.0,
            ssl_context=_trusting_context(cert_path),
        )
    finally:
        server.close()

    assert content.data == PNG_BYTES
    assert content.media_type == "image/png"


@pytest.mark.integration
async def test_capped_client_still_downloads_from_a_tls12_server(tmp_path: Path) -> None:
    """内网实际路径：TLS 1.2 服务端 + 限到 1.2 的客户端必须能取到附件。"""
    cert_path, key_path = _self_signed_cert(tmp_path)
    server = TlsMediaServer(
        cert_path,
        key_path,
        minimum=ssl.TLSVersion.TLSv1_2,
        maximum=ssl.TLSVersion.TLSv1_2,
    )
    server.body = wecom_ciphertext(PNG_BYTES)
    try:
        content = await download_media(
            server.url(),
            AES_KEY,
            max_bytes=1024 * 1024,
            timeout_sec=5.0,
            ssl_context=_trusting_context(cert_path, maximum=ssl.TLSVersion.TLSv1_2),
        )
    finally:
        server.close()

    assert content.data == PNG_BYTES


@pytest.mark.integration
async def test_port_passes_the_media_ssl_context_to_the_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """适配器端口把装配期构造的 context 原样交给下载函数（不在调用点丢参数）。"""
    captured: dict[str, object] = {}

    async def fake_download(
        url: str,
        aes_key: str | None,
        *,
        max_bytes: int,
        ssl_context: ssl.SSLContext | None = None,
    ) -> WeComMediaContent:
        captured["ssl_context"] = ssl_context
        return WeComMediaContent(
            data=PNG_BYTES, media_type="image/png", filename=None, checksum="0" * 64
        )

    monkeypatch.setattr(
        "muad_im_connector.channels.wecom.adapter.download_media_content", fake_download
    )
    context = ssl.create_default_context()
    port = _AibotClientPort(StubRawClient(), bot_id=BOT_ID, media_ssl_context=context)

    await port.download_media("https://cdn.test/media", AES_KEY, max_bytes=1024)

    assert captured["ssl_context"] is context
