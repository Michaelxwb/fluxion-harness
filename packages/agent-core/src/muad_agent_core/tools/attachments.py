"""Shared attachment read/view tools (Runtime and Worker)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..model.provider import ImagePart, ModelMessage, ModelRole
from .identity import ToolCall
from .registry import ToolCapability, ToolDefinition, ToolEffect, ToolRegistry
from .result import ToolError, ToolResult

READ_ATTACHMENT_TOOL = "read_attachment"
VIEW_IMAGE_TOOL = "view_image"
MAX_TEXT_CHARS = 20_000
#: 跳过前文时的单次读取上限：按块计数，不把整段前文读进内存。
SKIP_CHUNK_CHARS = 64 * 1024
VIEW_PREFIX = "已重看附件（附件 ID "
VIEW_SUFFIX = "）；图像内容已作为下一条消息附加。"


@dataclass(frozen=True, slots=True)
class TextCursor:
    """可续读的文本位置：文本 IO cookie + 已消费字符数。"""

    cookie: int
    chars: int


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    artifact_id: str
    media_type: str
    filename: str
    #: 文本读取只带请求的分段；图片读取（offset=0, limit=0）带完整字节。
    data: bytes
    total_chars: int | None = None
    next_cursor: TextCursor | None = None


Resolver = Callable[[str, int, int, TextCursor | None], Awaitable[ResolvedAttachment]]


def encode_cursor(cursor: TextCursor) -> str:
    """Opaque token; the text IO cookie makes the next page O(1) instead of re-scanning."""
    return f"v1:{cursor.cookie}:{cursor.chars}"


def decode_cursor(raw: str) -> TextCursor:
    try:
        version, cookie, chars = raw.split(":")
        if version != "v1":
            raise ValueError
        parsed = TextCursor(int(cookie), int(chars))
        if parsed.cookie < 0 or parsed.chars < 0:
            raise ValueError
        return parsed
    except (AttributeError, TypeError, ValueError) as exc:
        raise ToolError("COMMON_VALIDATION_ERROR", "cursor is invalid") from exc


class AttachmentReadTools:
    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                name=READ_ATTACHMENT_TOOL,
                description=(
                    "Read a text attachment/tool result in segments. Returns the segment with "
                    "offset/limit annotations and a cursor; pass that cursor back to continue "
                    "without re-scanning earlier text."
                ),
                input_schema={
                    "type": "object",
                    "required": ["artifact_id"],
                    "additionalProperties": False,
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                        "cursor": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_TEXT_CHARS},
                    },
                },
                effect=ToolEffect.READ,
                handler=self.read_attachment,
                externalizable_result=False,
                capabilities=frozenset({ToolCapability.ATTACHMENT_READ}),
            )
        )
        registry.register(
            ToolDefinition(
                name=VIEW_IMAGE_TOOL,
                description="Re-open an image attachment as an image message for the model.",
                input_schema={
                    "type": "object",
                    "required": ["artifact_id"],
                    "additionalProperties": False,
                    "properties": {"artifact_id": {"type": "string"}},
                },
                effect=ToolEffect.READ,
                handler=self.view_image,
                capabilities=frozenset({ToolCapability.ATTACHMENT_READ}),
            )
        )

    async def read_attachment(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        artifact_id = _required(arguments, "artifact_id")
        offset = _int_arg(arguments, "offset", 0)
        limit = _int_arg(arguments, "limit", MAX_TEXT_CHARS)
        cursor = _cursor_arg(arguments)
        if cursor is not None and offset:
            raise ToolError(
                "COMMON_VALIDATION_ERROR", "offset and cursor cannot be used together"
            )
        row = await self._resolver(artifact_id, offset, limit, cursor)
        if row.media_type.startswith("image/"):
            raise ToolError(
                "ATTACHMENT_TYPE_UNSUPPORTED",
                f"图片附件 {row.artifact_id} 不能当文本读；请调用 {VIEW_IMAGE_TOOL}",
            )
        if not _is_text_media(row.media_type):
            raise ToolError(
                "ATTACHMENT_TYPE_UNSUPPORTED",
                f"{row.filename}：非文本附件（{row.media_type}）不支持 read_attachment",
            )
        segment = row.data.decode("utf-8", errors="replace")
        start = cursor.chars if cursor is not None else offset
        end = start + len(segment)
        total = row.total_chars
        note = (
            f"[{row.filename}] 字符 {start}..{end} / 共 {total} 字符"
            if total is not None
            else f"[{row.filename}] 字符 {start}..{end}"
        )
        if row.next_cursor is not None:
            note += f"；继续读请用 cursor={encode_cursor(row.next_cursor)}"
        return ToolResult("\n".join([note, segment]) if segment else note)

    async def view_image(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        artifact_id = _required(arguments, "artifact_id")
        row = await self._resolver(artifact_id, 0, 0, None)
        if not row.media_type.startswith("image/"):
            raise ToolError(
                "ATTACHMENT_TYPE_UNSUPPORTED", f"附件 {row.artifact_id} 不是图片，无法重看"
            )
        import base64

        return ToolResult(
            f"{VIEW_PREFIX}{row.artifact_id}）：{row.filename}{VIEW_SUFFIX}",
            follow_ups=(
                ModelMessage(
                    role=ModelRole.USER,
                    content=(
                        f"[重看附件] {row.filename}",
                        ImagePart(
                            media_type=row.media_type,
                            data_base64=base64.b64encode(row.data).decode("ascii"),
                            artifact_id=row.artifact_id,
                        ),
                    ),
                ),
            ),
        )


def _required(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ToolError("COMMON_VALIDATION_ERROR", f"{key} must be a non-empty string")
    return value


def _cursor_arg(arguments: Mapping[str, Any]) -> TextCursor | None:
    raw = arguments.get("cursor")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ToolError("COMMON_VALIDATION_ERROR", "cursor must be a non-empty string")
    return decode_cursor(raw)


def _int_arg(arguments: Mapping[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolError("COMMON_VALIDATION_ERROR", f"{key} must be a non-negative integer")
    return int(value)


def read_text_segment(
    path: Path,
    offset: int,
    limit: int,
    cursor: TextCursor | None = None,
) -> tuple[bytes, int | None, TextCursor | None]:
    """按字符读取文本；cursor 续读时直接从上次的 IO cookie 开始，不重扫前文。"""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if cursor is not None:
            handle.seek(cursor.cookie)
            start = cursor.chars
        else:
            handle.seek(0)
            skipped = 0
            while skipped < offset:
                chunk = handle.read(min(offset - skipped, SKIP_CHUNK_CHARS))
                if not chunk:
                    break
                skipped += len(chunk)
            start = skipped
        segment = handle.read(limit)
        end = start + len(segment)
        if len(segment) < limit:
            return segment.encode("utf-8"), end, None
        return segment.encode("utf-8"), None, TextCursor(handle.tell(), end)


def _is_text_media(media_type: str) -> bool:
    return (
        media_type.startswith("text/")
        or media_type in {"application/json", "application/xml", "application/yaml"}
    )


__all__ = [
    "AttachmentReadTools",
    "MAX_TEXT_CHARS",
    "READ_ATTACHMENT_TOOL",
    "ResolvedAttachment",
    "SKIP_CHUNK_CHARS",
    "TextCursor",
    "VIEW_IMAGE_TOOL",
    "decode_cursor",
    "encode_cursor",
    "read_text_segment",
]
