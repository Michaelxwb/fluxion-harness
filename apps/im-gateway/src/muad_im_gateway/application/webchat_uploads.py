"""Webchat 流式上传与动态限额（design §3.4 API-10、§3.7 配置契约；TASK-054）。

职责与边界：

- **流式落共享字节**：`multipart/form-data` 的部件体按入站块读入并写入**共享** artifact store
  （暂存键 `webchat/staging/<uuid>`，只由系统生成），不整包驻留内存；临时文件 + `os.link`
  原子发布（目标已存在即冲突、绝不覆盖），与入站附件落盘同一不可变契约。
- **动态限额**：`webchat.upload_max_bytes` / `upload_max_per_message` 只来自平台设置快照
  （Console 权威、唯一来源），并以启动硬预算 `chat_upload_hard_max_bytes` 作为解析上界——
  设置超预算**显式失败**，不静默取小值掩盖配置冲突（§3.7）。
- **门控复用既有词表**：MIME 白名单与 `ATTACHMENT_TYPE_NOT_ALLOWED` / `ATTACHMENT_COUNT_EXCEEDED`
  / `ATTACHMENT_TOO_LARGE` 与入站附件门控同源；超限在**写入途中**中止（拒绝发生在昂贵处理
  之前），半成品与临时文件立即清理。
- **登记转 Owner**：字节写好后才经受信端口（`POST /internal/webchat/uploads`，TASK-058）登记
  权威元信息；响应只回 `upload_id` 与元信息（不伪造 artifact_id、不回存储键），网关不持库。
- **失败即清理**：任一失败路径（坏形状/超限/登记被拒）都删除已写字节——没有登记行引用的暂存
  字节是孤儿；而登记行缺失时读取侧本就按 `UPLOAD_EXPIRED` 处理（"重新上传"是既有语义）。
- **请求形状**：一次请求只登记**一个** `file` 部件（响应契约是单个 `WebchatUpload`，前端按文件
  逐个上传）；单请求文件部件数仍受 `upload_max_per_message` 上界约束。文件名只作元信息，
  绝不参与路径拼接。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts.channel import AttachmentKind
from muad_contracts.webchat import WebchatUpload, WebchatUploadRegistration
from pydantic import ValidationError

from .attachment_gate import ALLOWED_MEDIA_TYPES
from .chat_principal import ChatIdentity
from .inbound_attachments import kind_for
from .platform_settings import resolve_platform_settings
from .ports import PlatformSettingsClient, PlatformSettingsSnapshot
from .webchat_service import WebchatEntrySource, resolve_authorized_entry

logger = logging.getLogger(__name__)

#: 上传部件的字段名（前端 `WEBCHAT_UPLOAD_FIELD` 同源）：请求里只有这一个文件部件。
UPLOAD_FIELD_NAME = "file"
#: 暂存键前缀：`webchat/staging/<uuid>`，只由系统生成（用户文件名只作元信息）。
STAGING_KEY_PREFIX = "webchat/staging"
#: 部件头块与传输前言的有界上限（缓冲上界 = 单个入站块 + 分隔符长度）。
MAX_HEADER_BYTES = 8 * 1024
#: boundary 长度上限（RFC 7578 的 boundary ≤ 70；这里给应用一个明确边界）。
MAX_BOUNDARY_LENGTH = 200
#: 文件名长度上限（与 Runtime 登记校验同值）。
MAX_FILENAME_LENGTH = 255

_HEADER_LINE_PATTERN = re.compile(r"^([A-Za-z0-9-]+):[ \t]*(.*)$")


@dataclass(frozen=True, slots=True)
class WebchatUploadLimits:
    """上传门控：字节上限与单请求文件部件数（都来自平台设置，Console 权威）。"""

    max_bytes: int
    max_per_request: int

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.max_per_request <= 0:
            raise ValueError("webchat upload limits must be positive")


def resolve_upload_limits(
    snapshot: PlatformSettingsSnapshot, *, hard_max_bytes: int
) -> WebchatUploadLimits:
    """平台设置快照 → 上传门控；设置超启动硬预算显式失败（不静默取小值，§3.7）。"""
    if isinstance(hard_max_bytes, bool) or hard_max_bytes <= 0:
        raise ValueError("hard_max_bytes must be a positive integer")
    webchat = resolve_platform_settings(
        snapshot, chat_upload_hard_max_bytes=hard_max_bytes
    ).webchat
    return WebchatUploadLimits(
        max_bytes=webchat.upload_max_bytes, max_per_request=webchat.upload_max_per_message
    )


class WebchatUploadOwnerPort(Protocol):
    """登记接口要的 Owner 能力（Runtime 是唯一实现；结构替身只需实现同一动词面）。"""

    async def register_upload(
        self, registration: WebchatUploadRegistration, *, tenant_id: str
    ) -> dict[str, Any]: ...


class WebchatUploadService:
    """API-10 应用服务：入口授权 → 动态限额 → 流式落共享字节 → 转 Owner 登记。"""

    def __init__(
        self,
        *,
        runtime: WebchatUploadOwnerPort,
        entries: WebchatEntrySource,
        settings: PlatformSettingsClient,
        store: NfsArtifactStore,
        tenant_id: str,
        hard_max_bytes: int,
    ) -> None:
        if isinstance(hard_max_bytes, bool) or hard_max_bytes <= 0:
            raise ValueError("hard_max_bytes must be a positive integer")
        self._runtime = runtime
        self._entries = entries
        self._settings = settings
        self._store = store
        self._tenant_id = tenant_id
        self._hard_max_bytes = hard_max_bytes

    async def upload(
        self,
        identity: ChatIdentity,
        entry_id: uuid.UUID,
        *,
        stream: AsyncIterator[bytes],
        content_type: str | None,
    ) -> WebchatUpload:
        """流式接收一个上传：字节落共享存储后经受信端口登记，返回权威元信息。"""
        ref = resolve_authorized_entry(self._entries, identity, entry_id)
        limits = await self._limits()
        reader = _MultipartReader(stream, _boundary_of(content_type))
        sink = _StagingSink(
            store=self._store, staging_key=_staging_key(), max_bytes=limits.max_bytes
        )
        try:
            staged = await _stage_single_part(reader, sink, limits)
            sink.publish()
            data = await self._runtime.register_upload(
                WebchatUploadRegistration(
                    user_id=identity.principal.platform_user_id,
                    agent_id=ref.agent_id,
                    entry_id=ref.entry_id,
                    storage_key=staged.staging_key,
                    kind=staged.kind,
                    media_type=staged.media_type,
                    checksum=staged.checksum,
                    filename=staged.filename,
                    size=staged.size,
                ),
                tenant_id=self._tenant_id,
            )
            return _upload_view(data)
        except BaseException:
            sink.discard()
            raise

    async def _limits(self) -> WebchatUploadLimits:
        """动态限额唯一来源：平台设置快照 + 启动硬预算上界（设置源损坏即显式失败）。"""
        snapshot = await self._settings.fetch_snapshot(tenant_id=self._tenant_id)
        return resolve_upload_limits(snapshot, hard_max_bytes=self._hard_max_bytes)


@dataclass(frozen=True, slots=True)
class _StagedPart:
    """一个文件部件的权威元信息（字节已完整落盘后才有值）。"""

    staging_key: str
    kind: AttachmentKind
    media_type: str
    filename: str | None
    size: int
    checksum: str


async def _stage_single_part(
    reader: _MultipartReader, sink: _StagingSink, limits: WebchatUploadLimits
) -> _StagedPart:
    """扫描部件：只接受一个 `file` 部件；MIME/数量门控都在写字节之前判定。"""
    await reader.start()
    file_parts = 0
    staged: _StagedPart | None = None
    while True:
        headers = await reader.read_headers()
        disposition = _parse_disposition(headers.get("content-disposition"))
        is_file = disposition.name == UPLOAD_FIELD_NAME and disposition.filename is not None
        if is_file:
            file_parts += 1
            if file_parts > limits.max_per_request:
                raise AppError(
                    ErrorCode.ATTACHMENT_COUNT_EXCEEDED,
                    message_args={"limit": limits.max_per_request},
                )
            if file_parts > 1:
                # 响应契约是单个 WebchatUpload：一次请求只登记一个上传。
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            media_type = _allowed_media_type(headers.get("content-type"))
            sink.open()
            finished = await reader.stream_part_body(sink.write)
            if sink.size == 0:
                # 空文件在登记端口也是非法元信息：在这里就拒，不花一次登记往返。
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            staged = _StagedPart(
                staging_key=sink.staging_key,
                kind=kind_for(media_type),
                media_type=media_type,
                filename=_safe_filename(_decode_header_value(disposition.filename or "")),
                size=sink.size,
                checksum=sink.checksum,
            )
        else:
            finished = await reader.stream_part_body(_discard_chunk)
        if finished:
            break
    if staged is None:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return staged


class _StagingSink:
    """暂存字节的有界流式写入：临时文件 → 原子发布；失败路径统一 `discard`（不留半成品）。"""

    def __init__(self, *, store: NfsArtifactStore, staging_key: str, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.staging_key = staging_key
        self._target = store.resolve(staging_key)
        self._tmp = self._target.with_name(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        self._max_bytes = max_bytes
        self._digest = hashlib.sha256()
        self._size = 0
        self._handle: BinaryIO | None = None
        self._published = False

    @property
    def size(self) -> int:
        return self._size

    @property
    def checksum(self) -> str:
        return f"sha256:{self._digest.hexdigest()}"

    def open(self) -> None:
        if self._handle is not None:
            return
        self._target.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._tmp.open("wb")

    def write(self, chunk: bytes) -> None:
        """写入一块：**先判上限**（超限立即抛错，字节不越过上限），再累计摘要。"""
        self._size += len(chunk)
        if self._size > self._max_bytes:
            raise AppError(
                ErrorCode.ATTACHMENT_TOO_LARGE, message_args={"limit": self._max_bytes}
            )
        handle = self._handle
        if handle is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        self._digest.update(chunk)
        handle.write(chunk)

    def publish(self) -> None:
        """临时文件 → 暂存键的原子发布（`os.link`：目标已存在即冲突，绝不覆盖）。"""
        handle, self._handle = self._handle, None
        self._close(handle)
        try:
            os.link(self._tmp, self._target)
        except FileExistsError as exc:
            raise AppError(ErrorCode.COMMON_CONFLICT) from exc
        except OSError as exc:
            logger.warning("webchat_upload_publish_failed error_type=%s", type(exc).__name__)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        self._published = True
        self._remove(self._tmp)

    def discard(self) -> None:
        """失败路径的统一清理：临时文件与（已发布的）暂存字节都不留。"""
        handle, self._handle = self._handle, None
        self._close(handle)
        self._remove(self._tmp)
        if self._published:
            self._remove(self._target)

    @staticmethod
    def _close(handle: BinaryIO | None) -> None:
        if handle is None:
            return
        try:
            handle.close()
        except OSError as exc:
            logger.warning("webchat_upload_close_failed error_type=%s", type(exc).__name__)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("webchat_upload_cleanup_failed error_type=%s", type(exc).__name__)


class _MultipartReader:
    """`multipart/form-data` 的有界流式读取：部件头有界，部件体按块交出，不整包驻留内存。"""

    def __init__(self, stream: AsyncIterator[bytes], boundary: bytes) -> None:
        self._iterator = stream.__aiter__()
        self._marker = b"--" + boundary
        self._delimiter = b"\r\n" + self._marker
        self._buffer = bytearray()
        self._eof = False

    async def start(self) -> None:
        """消费首个分隔行（`--boundary` + CRLF）；传输前言有界丢弃。"""
        while True:
            index = self._buffer.find(self._marker)
            if index >= 0:
                break
            if len(self._buffer) > MAX_HEADER_BYTES or not await self._pull():
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        del self._buffer[: index + len(self._marker)]
        await self._expect_crlf()

    async def read_headers(self) -> dict[str, str]:
        """读取一个部件的头块（有界）；返回小写头名 → 原值。"""
        while True:
            index = self._buffer.find(b"\r\n\r\n")
            if index >= 0:
                break
            if len(self._buffer) > MAX_HEADER_BYTES or not await self._pull():
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        block = bytes(self._buffer[:index])
        del self._buffer[: index + 4]
        return _parse_header_block(block)

    async def stream_part_body(self, sink: Callable[[bytes], None]) -> bool:
        """把部件体按块交给 `sink`，直到下一个分隔行；返回是否已到闭合分隔行（`--`）。"""
        hold = len(self._delimiter) - 1
        while True:
            index = self._buffer.find(self._delimiter)
            if index >= 0:
                if index:
                    sink(bytes(self._buffer[:index]))
                del self._buffer[: index + len(self._delimiter)]
                return await self._after_boundary()
            if self._eof:
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            flush = len(self._buffer) - hold
            if flush > 0:
                sink(bytes(self._buffer[:flush]))
                del self._buffer[:flush]
            await self._pull()

    async def _after_boundary(self) -> bool:
        """分隔行之后：`--` = 已到闭合分隔符，CRLF = 还有下一个部件。"""
        if not await self._fill(2):
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        suffix = bytes(self._buffer[:2])
        if suffix == b"--":
            del self._buffer[:2]
            await self._consume_optional_crlf()
            return True
        if suffix == b"\r\n":
            del self._buffer[:2]
            return False
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)

    async def _expect_crlf(self) -> None:
        if not await self._fill(2) or bytes(self._buffer[:2]) != b"\r\n":
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        del self._buffer[:2]

    async def _consume_optional_crlf(self) -> None:
        if await self._fill(2) and bytes(self._buffer[:2]) == b"\r\n":
            del self._buffer[:2]

    async def _fill(self, minimum: int) -> bool:
        while len(self._buffer) < minimum:
            if not await self._pull():
                return False
        return True

    async def _pull(self) -> bool:
        """拉一个**非空**入站块进缓冲；流结束返回 False（空块跳过，不空转）。"""
        while not self._eof:
            try:
                chunk = await anext(self._iterator)
            except StopAsyncIteration:
                self._eof = True
                return False
            if chunk:
                self._buffer.extend(chunk)
                return True
        return False


def _boundary_of(content_type: str | None) -> bytes:
    """`Content-Type` → boundary 字节；非 multipart/缺 boundary 一律显式拒绝。"""
    if content_type is None:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    media_type, _, params = content_type.partition(";")
    if media_type.strip().lower() != "multipart/form-data":
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    boundary: str | None = None
    for raw in params.split(";"):
        key, _, value = raw.partition("=")
        if key.strip().lower() == "boundary":
            boundary = value.strip().strip('"')
    if not boundary or len(boundary) > MAX_BOUNDARY_LENGTH:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    try:
        return boundary.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


def _parse_header_block(block: bytes) -> dict[str, str]:
    """部件头块 → 小写头名映射；坏行/折行显式拒绝（浏览器不产生折行）。"""
    headers: dict[str, str] = {}
    for raw_line in block.split(b"\r\n"):
        if not raw_line:
            continue
        match = _HEADER_LINE_PATTERN.match(raw_line.decode("latin-1"))
        if match is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        headers[match.group(1).lower()] = match.group(2).strip()
    return headers


@dataclass(frozen=True, slots=True)
class _PartDisposition:
    name: str | None
    filename: str | None


def _parse_disposition(value: str | None) -> _PartDisposition:
    """`Content-Disposition` → (字段名, 文件名)；非 `form-data` 视作非文件部件。"""
    if value is None or value.split(";")[0].strip().lower() != "form-data":
        return _PartDisposition(name=None, filename=None)
    name: str | None = None
    filename: str | None = None
    for raw in value.split(";")[1:]:
        key, _, raw_value = raw.partition("=")
        parsed = _unquote(raw_value.strip())
        if key.strip().lower() == "name":
            name = parsed
        elif key.strip().lower() == "filename":
            filename = parsed
    return _PartDisposition(name=name, filename=filename)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _decode_header_value(value: str) -> str:
    """头块按 latin-1 读入：这里把浏览器原样放入的 UTF-8 文件名还原成文本。"""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _allowed_media_type(value: str | None) -> str:
    """部件 MIME 必须在既有附件白名单内（可执行内容默认拒绝），否则显式拒绝。"""
    media_type = (value or "").strip().lower()
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise AppError(ErrorCode.ATTACHMENT_TYPE_NOT_ALLOWED)
    return media_type


def _safe_filename(value: str | None) -> str | None:
    """文件名只作元信息：空/纯空白 → None；超长或含控制字符显式拒绝（绝不参与路径拼接）。"""
    if value is None:
        return None
    name = value.strip()
    if not name:
        return None
    if len(name) > MAX_FILENAME_LENGTH or any(char in name for char in "\r\n\x00"):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return name


def _discard_chunk(_chunk: bytes) -> None:
    """跳过非文件部件的体（仍按块读走：内存有界，不把请求体整包留在缓冲里）。"""


def _staging_key() -> str:
    """暂存键只由系统生成：用户文件名只作元信息，绝不参与路径拼接。"""
    return f"{STAGING_KEY_PREFIX}/{uuid.uuid4().hex}"


def _upload_view(data: dict[str, Any]) -> WebchatUpload:
    """Owner 登记回执 → 公开契约；字段不符显式失败（不回显内部内容）。"""
    try:
        return WebchatUpload.model_validate(data)
    except ValidationError as exc:
        logger.warning("webchat_upload_registration_shape_invalid")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
