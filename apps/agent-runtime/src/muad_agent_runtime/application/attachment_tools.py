"""附件读取工具：`read_attachment` 与 `view_image`（设计 API-06 / AD-4-B）。

**类型无关、入口无关**：抽取落在 agent 侧，任何入口落进 artifact 的东西都能读（企微附件、
Console 上传、skill 产出）。渠道层因此不必背内容理解。

**为什么 `view_image` 要追加一条 user 消息**：历史轮次的图片只留文本引用（AD-3-B），模型想
重看得主动取用；而 OpenAI 协议的 **tool 角色不能携带图像块** —— 只能由运行时在工具结果之后
补一条带图像块的 user 消息。这是 `ToolDefinition.follow_up_messages` 存在的唯一理由。
"""

from __future__ import annotations

import base64
import io
import uuid
from collections.abc import Awaitable, Mapping
from typing import Any

from muad_agent_core.model import ImagePart, ModelMessage, ModelRole
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_artifact_store import NfsArtifactStore

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import Artifact

READ_ATTACHMENT_TOOL = "read_attachment"
VIEW_IMAGE_TOOL = "view_image"

READ_ATTACHMENT_DESCRIPTION = (
    "Read the content of a file the user sent (or that an earlier step produced) by its "
    "attachment id. Returns extracted text for documents; for images use view_image instead."
)
VIEW_IMAGE_DESCRIPTION = (
    "Attach a previously sent image to the conversation again so you can look at it. Use this "
    "when an earlier image is relevant but is no longer in front of you."
)

#: 抽取/读取的字节上限。与门控同量级：防呆的第二道，防超预期的大文件拖垮运行。
MAX_READ_BYTES = 20 * 1024 * 1024
#: 回给模型的文本上限。超出截断并显式标注，**不静默丢内容**。
MAX_TEXT_CHARS = 20_000

ATTACHMENT_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
ATTACHMENT_TYPE_UNSUPPORTED = "ATTACHMENT_TYPE_UNSUPPORTED"
ATTACHMENT_EXTRACT_FAILED = "ATTACHMENT_EXTRACT_FAILED"
ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"

VIEW_IMAGE_RESULT_PREFIX = "已重新附上图片（附件 ID "

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class AttachmentToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _extract_document(data: bytes, media_type: str) -> str:
    """按 MIME 抽取文本。**任何解析器异常都转成明确错误**，绝不返回乱码或空内容冒充成功。"""
    try:
        if media_type == "application/pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        if media_type == _DOCX:
            import docx

            return "\n".join(paragraph.text for paragraph in docx.Document(io.BytesIO(data)).paragraphs)
        if media_type == _XLSX:
            import openpyxl  # type: ignore[import-untyped]

            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            lines: list[str] = []
            for sheet in book.worksheets:
                lines.append(f"[{sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    lines.append("\t".join("" if cell is None else str(cell) for cell in row))
            return "\n".join(lines)
        if media_type == _PPTX:
            from pptx import Presentation

            return "\n".join(
                shape.text
                for slide in Presentation(io.BytesIO(data)).slides
                for shape in slide.shapes
                if hasattr(shape, "text")
            )
        if media_type.startswith("text/"):
            return data.decode("utf-8", errors="replace")
    except Exception as exc:  # 解析库的异常类型不统一（且含加密/损坏两类），统一转成明确错误
        raise AttachmentToolError(
            ATTACHMENT_EXTRACT_FAILED, f"文档无法解析（可能已加密或损坏）: {exc}"
        ) from exc
    raise AttachmentToolError(
        ATTACHMENT_TYPE_UNSUPPORTED, f"尚不支持的内容类型: {media_type}"
    )


class AttachmentToolSet:
    def __init__(
        self,
        *,
        session_factory: SessionFactoryProvider,
        artifact_root: str,
        tenant_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root
        self._tenant_id = tenant_id

    def register(self, registry: ToolRegistry) -> None:
        string_schema: Mapping[str, Any] = {"type": "string"}
        registry.register(
            ToolDefinition(
                name=READ_ATTACHMENT_TOOL,
                description=READ_ATTACHMENT_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {"artifact_id": string_schema},
                    "required": ["artifact_id"],
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ,
                handler=self.read_attachment,
            )
        )
        registry.register(
            ToolDefinition(
                name=VIEW_IMAGE_TOOL,
                description=VIEW_IMAGE_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {"artifact_id": string_schema},
                    "required": ["artifact_id"],
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ,
                handler=self.view_image,
                follow_up_messages=self._image_follow_up,
            )
        )

    async def read_attachment(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        row = await self._load(arguments.get("artifact_id"))
        if (row.metadata_json or {}).get("kind") == "IMAGE":
            return (
                f"这是图片附件 {row.id}（{_filename(row)}），不能当文本读；"
                f"如需查看请调用 {VIEW_IMAGE_TOOL}。"
            )
        data = self._read_bytes(row)
        try:
            text = _extract_document(data, row.media_type)
        except AttachmentToolError as exc:
            # 报错要指明**是哪个文件**读不出来，否则模型只能对用户说"有个文件读不了"
            raise AttachmentToolError(exc.code, f"{_filename(row)}：{exc.message}") from exc
        if len(text) > MAX_TEXT_CHARS:
            return f"{text[:MAX_TEXT_CHARS]}\n…（内容过长，已截断，共 {len(text)} 字符）"
        return f"[{_filename(row)}]\n{text}"

    async def view_image(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        row = await self._load(arguments.get("artifact_id"))
        if (row.metadata_json or {}).get("kind") != "IMAGE":
            raise AttachmentToolError(
                ATTACHMENT_TYPE_UNSUPPORTED, f"附件 {row.id} 不是图片，无法重看"
            )
        if not row.media_type.startswith("image/"):
            raise AttachmentToolError(
                ATTACHMENT_TYPE_UNSUPPORTED, "该附件的媒体类型不是图片，无法重看"
            )
        return f"{VIEW_IMAGE_RESULT_PREFIX}{row.id}）：{_filename(row)}"

    def _image_follow_up(self, result: str) -> Awaitable[tuple[ModelMessage, ...]]:
        return self._build_image_message(result)

    async def _build_image_message(self, result: str) -> tuple[ModelMessage, ...]:
        """工具结果 → 补一条带图像块的 user 消息。

        **不 mock 读取**：字节真的从 artifact store 取，与当前消息内联图片走同一条路。
        """
        if not result.startswith(VIEW_IMAGE_RESULT_PREFIX):
            return ()
        artifact_id = result[len(VIEW_IMAGE_RESULT_PREFIX) :].split("）")[0].strip()
        try:
            row = await self._load(artifact_id)
            data = self._read_bytes(row)
        except AttachmentToolError:
            # 取不回来就不补消息：工具结果里的文本已经说明了情况，不要追加半截内容
            return ()
        return (
            ModelMessage(
                role=ModelRole.USER,
                content=(
                    f"[重看附件] {_filename(row)}",
                    ImagePart(
                        media_type=row.media_type,
                        data_base64=base64.b64encode(data).decode("ascii"),
                    ),
                ),
            ),
        )

    async def _load(self, artifact_id: object) -> Artifact:
        """按标识取附件行。**租户隔离**：越权一律按"不存在"处理 —— 不回显对方的存在性、
        文件名或标识本身（`harness-auth#RULE-auth-001`：未授权资源不得进入 ToolRegistry 视野）。"""
        try:
            parsed = uuid.UUID(str(artifact_id))
        except (ValueError, TypeError) as exc:
            raise AttachmentToolError(ATTACHMENT_NOT_FOUND, "附件不存在或不可访问") from exc
        async with self._session_factory()() as session:
            row = await session.get(Artifact, parsed)
        if row is None or row.is_deleted or row.tenant_id != self._tenant_id:
            raise AttachmentToolError(ATTACHMENT_NOT_FOUND, "附件不存在或不可访问")
        return row

    def _read_bytes(self, row: Artifact) -> bytes:
        if row.size > MAX_READ_BYTES:
            raise AttachmentToolError(
                ATTACHMENT_TOO_LARGE, f"附件超过可读取上限（{MAX_READ_BYTES} 字节）"
            )
        path = NfsArtifactStore(self._artifact_root).resolve(row.storage_key)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AttachmentToolError(ATTACHMENT_NOT_FOUND, "附件内容不可读") from exc


def _filename(row: Artifact) -> str:
    name = (row.metadata_json or {}).get("filename")
    return name if isinstance(name, str) and name else "(未命名)"
