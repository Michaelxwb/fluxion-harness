"""企微媒体取件：**流式**下载 + 按官方算法解密 + 解密后判定类型与校验和。

**为什么不复用 SDK 的 `WSClient.download_file`**（三条都是源码事实）：

1. 它内部 `await response.read()` —— **无字节上限**，多大都全量进内存（几百 KB 的图与 2 GiB 的文件一视同仁）；
2. 它的超时写死 10s（`WeComApiClient(timeout=10000)`）且外部不可配；
3. `aes_key` 缺失时它只打一条 warn 就把**加密原文**当文件返回 —— 上层会把密文当图片/文档用，
   而验收要求「密钥缺失必须明确失败」。

所以下载由本仓自实现，**只复用官方 SDK 的解密函数**（AES-256-CBC / IV = 密钥前 16 字节 /
PKCS#7），避免与官方算法分叉。

**凭据卫生**：`url` 与 `aes_key` 不进日志、不进异常消息（`RULE-secret-001`）。

**类型判定在解密之后**：解密字节的魔术字节 → `Content-Disposition` 文件名的扩展名 →
`application/octet-stream` 兜底。**不采信下载响应的 `Content-Type`** —— 那描述的是加密载荷，
不是文件本身；兜底值也不在白名单内，会由实检门控拒绝并反馈，不会静默放行。
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import unquote

import httpx

from .sdk_port import (
    WeComMediaContent,
    WeComMediaDecryptError,
    WeComMediaError,
    WeComMediaNetworkError,
    WeComMediaTimeoutError,
    WeComMediaTooLargeError,
    WeComMediaUploadTooLargeError,
)

logger = logging.getLogger(__name__)

#: 单文件下载超时。超时是**传输安全**属性（不是产品策略），故写死在这里而不由调用方注入。
MEDIA_TIMEOUT_SEC = 30.0

_CHUNK_BYTES = 64 * 1024
_OCTET_STREAM = "application/octet-stream"

_MAGIC_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)

_EXTENSION_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    # 按扩展名兜底时，这几个必须用官方 MIME：按扩展名猜 MIME 会漏判白名单
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_FILENAME_UTF8_RE = re.compile(r"filename\*=UTF-8''([^;\s]+)", re.IGNORECASE)
_FILENAME_RE = re.compile(r'filename="?([^";\s]+)"?', re.IGNORECASE)


async def download_media(
    url: str,
    aes_key: str | None,
    *,
    max_bytes: int,
    timeout_sec: float = MEDIA_TIMEOUT_SEC,
) -> WeComMediaContent:
    """下载并解密一个企微媒体文件。

    `max_bytes` 由调用方给定（产品策略常量的唯一来源是门控 `MAX_ATTACHMENT_BYTES`），
    本函数在**流式读取时**执行它：累计超限立刻中止，不把整个响应体读完。

    成功与失败**都留一条日志**（失败按原因分类）。日志里只有类型、字节数与失败原因——
    `url`/`aes_key`/文件名都不在其中，这样"日志不含凭据"才是可被检验的事实而不是空断言。
    """
    try:
        encrypted, filename = await _fetch_encrypted(
            url, max_bytes=max_bytes, timeout_sec=timeout_sec
        )
        data = _decrypt(encrypted, aes_key)
    except WeComMediaError as exc:
        logger.warning("wecom_media_failed reason=%s error=%s", type(exc).__name__, exc)
        raise
    content = WeComMediaContent(
        data=data,
        media_type=_media_type(data, filename),
        filename=filename,
        checksum=hashlib.sha256(data).hexdigest(),
    )
    logger.info("wecom_media_downloaded media_type=%s size=%s", content.media_type, len(data))
    return content


async def _fetch_encrypted(
    url: str,
    *,
    max_bytes: int,
    timeout_sec: float,
) -> tuple[bytes, str | None]:
    """取回**未解密**的原始字节与文件名（文件名来自 `Content-Disposition`）。

    异常消息里**不含 `url`**：httpx 的异常文本可能带完整地址（含签名），只取类名。
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                filename = _filename_from_disposition(response.headers.get("content-disposition"))
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                    total += len(chunk)
                    if total > max_bytes:
                        # 立刻退出 async with ⇒ 中断响应流，不读完剩余字节
                        raise WeComMediaTooLargeError(max_bytes)
                    chunks.append(chunk)
                return b"".join(chunks), filename
    except httpx.TimeoutException as exc:
        raise WeComMediaTimeoutError("wecom media download timed out") from exc
    except httpx.HTTPError as exc:
        raise WeComMediaNetworkError(
            f"wecom media download failed: {type(exc).__name__}"
        ) from exc


def _decrypt(encrypted: bytes, aes_key: str | None) -> bytes:
    """按官方算法解密。密钥缺失与密钥不匹配都归为解密失败（**绝不放行密文**）。"""
    if not aes_key:
        raise WeComMediaDecryptError("wecom media aeskey missing")
    from aibot.crypto_utils import decrypt_file  # type: ignore[import-untyped]

    try:
        decrypted: bytes = decrypt_file(encrypted, aes_key)
        return decrypted
    except (ValueError, RuntimeError) as exc:
        raise WeComMediaDecryptError("wecom media decrypt failed") from exc


def _media_type(data: bytes, filename: str | None) -> str:
    for magic, media_type in _MAGIC_TYPES:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    suffix = _suffix(filename)
    if suffix in _EXTENSION_TYPES:
        return _EXTENSION_TYPES[suffix]
    return _OCTET_STREAM


def _suffix(filename: str | None) -> str:
    if not filename:
        return ""
    _, dot, extension = filename.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def _filename_from_disposition(value: str | None) -> str | None:
    """优先 RFC 5987 的 `filename*=UTF-8''…`，否则退回 `filename=…`。"""
    if not value:
        return None
    utf8_match = _FILENAME_UTF8_RE.search(value)
    if utf8_match:
        return unquote(utf8_match.group(1))
    match = _FILENAME_RE.search(value)
    return unquote(match.group(1)) if match else None


# --------------------------------------------------------------------------- 上传（出站）
#
# 官方 **Python** SDK 没有上传能力（`aibot` 1.0.2 = PyPI 最新，只有 `download_file`），
# 所以按官方 **Node** SDK 的协议自行驱动：`init → chunk × N → finish`。以下口径全部来自
# TASK-001 的真机实测（事实表见设计 §3.6「真机探针结论」）。

#: 单分片大小（**base64 编码前**）。官方口径。
UPLOAD_CHUNK_BYTES = 512 * 1024
#: 分片数上限 ⇒ 单文件约 50 MB。**渠道硬上限**，不是我们的产品策略（与下载侧的
#: `MAX_ATTACHMENT_BYTES` 性质不同：那个数我们可以自己定，这个不能）。
MAX_UPLOAD_CHUNKS = 100

UPLOAD_INIT_CMD = "aibot_upload_media_init"
UPLOAD_CHUNK_CMD = "aibot_upload_media_chunk"
UPLOAD_FINISH_CMD = "aibot_upload_media_finish"


def uploadable_media_type(kind: str) -> str:
    """把渠道中立的 `AttachmentRef.kind` 映射成企微的**发送形态**。

    企微只收 `image`/`file` 两种，没有「其它」这一档；`kind=IMAGE` 判图片（与入站同口径），
    其余一律当文件。**「发不发得了」不在这里决定**（那是尺寸的事），这里只决定形态。
    """
    return "image" if kind == "IMAGE" else "file"


def chunk_upload(data: bytes) -> list[bytes]:
    """按官方口径切分上传分片。

    `chunk_index` 是 **0-based**：Node SDK 的类型注释写"从 1 开始"、它自己的实现却从 0 起，
    两者互相矛盾，**真机判 0-based**（TASK-001 实测 1 片与 3 片全过）。

    分片超上限**抛错**而不是截断 —— 截断会发出去一个**被砍掉一半的文件**，比明确失败更糟。
    """
    chunks = [
        data[offset : offset + UPLOAD_CHUNK_BYTES]
        for offset in range(0, len(data), UPLOAD_CHUNK_BYTES)
    ]
    if len(chunks) > MAX_UPLOAD_CHUNKS:
        raise WeComMediaUploadTooLargeError(len(data), MAX_UPLOAD_CHUNKS)
    return chunks or [b""]
