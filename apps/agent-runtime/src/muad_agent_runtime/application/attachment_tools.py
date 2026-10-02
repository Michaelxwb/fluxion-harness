"""附件读取工具：`read_attachment` 与 `view_image`（设计 API-06 / AD-4-B）。

**类型无关、入口无关**：抽取落在 agent 侧，任何入口落进 artifact 的东西都能读（企微附件、
Console 上传、skill 产出）。渠道层因此不必背内容理解。

**为什么 `view_image` 要追加一条 user 消息**：历史轮次的图片只留文本引用（AD-3-B），模型想
重看得主动取用；而 OpenAI 协议的 **tool 角色不能携带图像块** —— 只能由运行时在工具结果之后
补一条带图像块的 user 消息。这是 `ToolDefinition.follow_up_messages` 存在的唯一理由。
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Mapping
from typing import Any

import sqlalchemy as sa
from muad_agent_core.model import ImagePart, ModelMessage, ModelRole
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_artifact_store import NfsArtifactStore

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import Artifact
from .inbound_attachments import INBOUND_DOCUMENT, INBOUND_IMAGE, INBOUND_OTHER

READ_ATTACHMENT_TOOL = "read_attachment"
VIEW_IMAGE_TOOL = "view_image"
WRITE_ARTIFACT_TOOL = "write_artifact"
SEARCH_ATTACHMENT_TOOL = "search_attachment"
LIST_ATTACHMENTS_TOOL = "list_attachments"

AGENT_OUTPUT_ARTIFACT_TYPE = "AGENT_OUTPUT"
#: 入站产物类型（封闭集合，定义在入站落库模块里）。方向判定按**类型**而不是按 run_id 是否存在——
#: 后台任务的自产产物 `run_id` 为空，用"有没有 run"判方向会把它误判成入站。
INBOUND_ARTIFACT_TYPES = (INBOUND_DOCUMENT, INBOUND_IMAGE, INBOUND_OTHER)

READ_ATTACHMENT_DESCRIPTION = (
    "Read the content of a file the user sent (or that an earlier step produced) by its "
    "attachment id. Long documents come back one segment at a time: pass offset to continue "
    "where the previous segment ended. Returns extracted text for documents; for images use "
    "view_image instead."
)
SEARCH_ATTACHMENT_DESCRIPTION = (
    "Find where a keyword occurs in a file's extracted text and return the matching fragments "
    "with their character offsets. Use it to jump straight to the relevant part of a long "
    "document instead of paging through it."
)
LIST_ATTACHMENTS_DESCRIPTION = (
    "List the files you can currently address — both the ones the user sent and the ones you "
    "produced earlier. Use it when an id from an earlier turn is no longer in front of you."
)
VIEW_IMAGE_DESCRIPTION = (
    "Attach a previously sent image to the conversation again so you can look at it. Use this "
    "when an earlier image is relevant but is no longer in front of you."
)

#: 抽取/读取的字节上限。**与门控同量级**（`MAX_ATTACHMENT_BYTES`）：防呆的第二道，
#: 防超预期的大文件拖垮运行。2026-10-03 随门控 20 MiB → 50 MiB 一起放宽——两者若不同步，
#: 会出现"门控收下了、Runtime 读不了"的悬空文件。
MAX_READ_BYTES = 50 * 1024 * 1024
#: 回给模型的文本上限。超出截断并显式标注，**不静默丢内容**。
MAX_TEXT_CHARS = 20_000

ATTACHMENT_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
ATTACHMENT_TYPE_UNSUPPORTED = "ATTACHMENT_TYPE_UNSUPPORTED"
ATTACHMENT_EXTRACT_FAILED = "ATTACHMENT_EXTRACT_FAILED"
ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
ATTACHMENT_WRITE_UNAVAILABLE = "ATTACHMENT_WRITE_UNAVAILABLE"
#: 参数类拒绝。与 `memory_tools` 的 `<FIELD>_INVALID` 同口径：**码要能指名道姓**，
#: 模型才知道该改哪个参数，而不是笼统的"参数错误"。
ATTACHMENT_OFFSET_INVALID = "ATTACHMENT_OFFSET_INVALID"
ATTACHMENT_LIMIT_INVALID = "ATTACHMENT_LIMIT_INVALID"
ATTACHMENT_QUERY_INVALID = "ATTACHMENT_QUERY_INVALID"
ATTACHMENT_SCOPE_INVALID = "ATTACHMENT_SCOPE_INVALID"
ATTACHMENT_DIRECTION_INVALID = "ATTACHMENT_DIRECTION_INVALID"

DEFAULT_OFFSET = 0
#: 单次搜索返回的命中条数上限（与 `limit` 的字符语义无关，故另设常量）
DEFAULT_SEARCH_HITS = 5
MAX_SEARCH_HITS = 20
#: 命中片段两侧各取的上下文字符数——够模型判断相关性，又不至于把整篇搬回来
SEARCH_CONTEXT_CHARS = 80
#: 抽取结果缓存条数。按 Run 复用（`AttachmentToolSet` 每个 Run 一个），让"同一份文档读多段"
#: 不再重复解析；有界，避免一次 Run 把多份大文档的全文留在内存里。
MAX_TEXT_CACHE_ENTRIES = 4

#: 枚举范围：本次 Run 内 / 整个会话（缺省会话——历史引用被裁掉的附件靠它找回）
SCOPE_RUN = "run"
SCOPE_CONVERSATION = "conversation"
SCOPES = frozenset({SCOPE_RUN, SCOPE_CONVERSATION})
SCOPE_DEFAULT = SCOPE_CONVERSATION
#: 方向：用户发来的 / Agent 自产（缺省不限）
DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"
DIRECTIONS = frozenset({DIRECTION_INBOUND, DIRECTION_OUTBOUND})
#: 呈现给模型的中文标签。**只在这里定义一次**，测试按同一份常量取值。
DIRECTION_LABELS = {DIRECTION_INBOUND: "入站", DIRECTION_OUTBOUND: "自产"}
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50

VIEW_IMAGE_RESULT_PREFIX = "已重新附上图片（附件 ID "
WRITE_ARTIFACT_RESULT_PREFIX = "已写出产物（附件 ID "

WRITE_ARTIFACT_DESCRIPTION = (
    "Write the result you produced as a file the user can receive, and return its attachment id. "
    "Use it when the outcome is a document rather than a short chat reply."
)

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


def slice_text(text: str, offset: int, limit: int) -> str:
    """按字符偏移切片——**纯函数**，无 IO、无副作用，分段语义的唯一实现处。

    越界**不是错误**（设计 §2.3.2）：`offset` 等于或超出全文长度一律返回空串，由调用方
    连区间标注一并回给模型——"读到了末尾"与"参数写错了"是两件事，不能混成同一个错。
    """
    if offset < 0:
        raise AttachmentToolError(ATTACHMENT_OFFSET_INVALID, f"offset 不能为负：{offset}")
    if limit < 1 or limit > MAX_TEXT_CHARS:
        raise AttachmentToolError(
            ATTACHMENT_LIMIT_INVALID, f"limit 必须在 1..{MAX_TEXT_CHARS} 之间：{limit}"
        )
    return text[offset : offset + limit]


def _segment_note(offset: int, limit: int, total: int) -> str:
    """区间标注。**必须让多次调用可拼接还原全文**，没读完时还要给出可行动的下一步。

    旧实现只丢一句"内容过长，已截断"——模型既不知道后面还有多少，也不知道怎么继续读。
    """
    start = min(offset, total)
    end = min(offset + limit, total)
    note = f"（片段 {start}–{end} / 共 {total} 字符"
    if offset >= total:
        note += "；已到末尾，无更多内容"
    elif end < total:
        note += f"；未完，继续读请用 offset={end}，单次上限 {limit}"
    return note + "）"


def _require_int(arguments: Mapping[str, Any], name: str, default: int, code: str) -> int:
    """取整数参数。**bool 要挡掉**——Python 里 `True` 是 `int`，放过去会变成 offset=1。

    写成正向收窄（先认下 int、再排除 bool）而不是 `if bool or not int: raise`：后者
    在类型检查器眼里落空路径**没有**被收窄成 int（实测报 `Returning Any`）。
    """
    value = arguments.get(name, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise AttachmentToolError(code, f"{name} 必须是整数：{value!r}")


def list_window(arguments: Mapping[str, Any]) -> tuple[int, int]:
    """把枚举入参归一成 `(offset, limit)`；越界**一律拒绝**（不夹紧）。

    与 `read_attachment` 的 `limit` 同口径——同一个概念两套行为，模型学不会。
    """
    offset = _require_int(arguments, "offset", DEFAULT_OFFSET, ATTACHMENT_OFFSET_INVALID)
    limit = _require_int(arguments, "limit", DEFAULT_LIST_LIMIT, ATTACHMENT_LIMIT_INVALID)
    if offset < 0:
        raise AttachmentToolError(ATTACHMENT_OFFSET_INVALID, f"offset 不能为负：{offset}")
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise AttachmentToolError(
            ATTACHMENT_LIMIT_INVALID, f"limit 必须在 1..{MAX_LIST_LIMIT} 之间：{limit}"
        )
    return offset, limit


class AttachmentToolSet:
    def __init__(
        self,
        *,
        session_factory: SessionFactoryProvider,
        artifact_root: str,
        tenant_id: str,
        run_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        has_delivery_route: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._conversation_id = conversation_id
        self._has_delivery_route = has_delivery_route
        #: 抽取结果按 Run 复用：同一份长文档分多次读，只解析一次（NFR-PERF-01）
        self._text_cache: OrderedDict[uuid.UUID, str] = OrderedDict()

    def register(self, registry: ToolRegistry) -> None:
        string_schema: Mapping[str, Any] = {"type": "string"}
        integer_schema: Mapping[str, Any] = {"type": "integer"}
        registry.register(
            ToolDefinition(
                name=READ_ATTACHMENT_TOOL,
                description=READ_ATTACHMENT_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_id": string_schema,
                        "offset": {**integer_schema, "minimum": 0},
                        "limit": {**integer_schema, "minimum": 1, "maximum": MAX_TEXT_CHARS},
                    },
                    "required": ["artifact_id"],
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ,
                handler=self.read_attachment,
            )
        )
        registry.register(
            ToolDefinition(
                name=SEARCH_ATTACHMENT_TOOL,
                description=SEARCH_ATTACHMENT_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {
                        "artifact_id": string_schema,
                        "query": string_schema,
                        "limit": {**integer_schema, "minimum": 1, "maximum": MAX_SEARCH_HITS},
                    },
                    "required": ["artifact_id", "query"],
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ,
                handler=self.search_attachment,
            )
        )
        registry.register(
            ToolDefinition(
                name=LIST_ATTACHMENTS_TOOL,
                description=LIST_ATTACHMENTS_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": sorted(SCOPES)},
                        "direction": {"type": "string", "enum": sorted(DIRECTIONS)},
                        "limit": {**integer_schema, "minimum": 1, "maximum": MAX_LIST_LIMIT},
                        "offset": {**integer_schema, "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ,
                handler=self.list_attachments,
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
        registry.register(
            ToolDefinition(
                name=WRITE_ARTIFACT_TOOL,
                description=WRITE_ARTIFACT_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {"content": string_schema, "filename": string_schema},
                    "required": ["content"],
                    "additionalProperties": False,
                },
                effect=ToolEffect.WRITE,
                handler=self.write_artifact,
            )
        )

    async def read_attachment(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """分段读。返回值**永远**带区间与总长标注，多次调用可拼接还原全文。"""
        row = await self._load(arguments.get("artifact_id"))
        if (row.metadata_json or {}).get("kind") == "IMAGE":
            return (
                f"这是图片附件 {row.id}（{_filename(row)}），不能当文本读；"
                f"如需查看请调用 {VIEW_IMAGE_TOOL}。"
            )
        offset = _require_int(arguments, "offset", DEFAULT_OFFSET, ATTACHMENT_OFFSET_INVALID)
        limit = _require_int(arguments, "limit", MAX_TEXT_CHARS, ATTACHMENT_LIMIT_INVALID)
        text = self._extract(row)
        try:
            segment = slice_text(text, offset, limit)
        except AttachmentToolError as exc:
            # 参数错也要指明是哪个文件，模型才能说清"这份文档的 offset 写错了"
            raise AttachmentToolError(exc.code, f"{_filename(row)}：{exc.message}") from exc
        lines = [f"[{_filename(row)}]"]
        if segment:
            lines.append(segment)
        lines.append(_segment_note(offset, limit, len(text)))
        return "\n".join(lines)

    async def search_attachment(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """在已抽取文本里定位关键词，返回命中片段与字符偏移。

        **未命中必须明说**（RULE-01 的同一条精神）：返回空内容会被模型当成"读到了空文档"，
        于是凭空发挥——不命中就直说没命中。
        """
        row = await self._load(arguments.get("artifact_id"))
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise AttachmentToolError(ATTACHMENT_QUERY_INVALID, "query 必填且为非空字符串")
        limit = _require_int(
            arguments, "limit", DEFAULT_SEARCH_HITS, ATTACHMENT_LIMIT_INVALID
        )
        if limit < 1 or limit > MAX_SEARCH_HITS:
            raise AttachmentToolError(
                ATTACHMENT_LIMIT_INVALID, f"limit 必须在 1..{MAX_SEARCH_HITS} 之间：{limit}"
            )
        text = self._extract(row)
        hits: list[int] = []
        cursor = 0
        while len(hits) < limit:
            found = text.find(query, cursor)
            if found < 0:
                break
            hits.append(found)
            cursor = found + len(query)  # 不重叠地前进，避免同一处被反复命中
        name = _filename(row)
        if not hits:
            return f"[{name}] 未命中「{query}」 / 共 {len(text)} 字符"
        lines = [f"[{name}] 命中 {len(hits)} 处 / 共 {len(text)} 字符"]
        for hit in hits:
            start = max(0, hit - SEARCH_CONTEXT_CHARS)
            end = min(len(text), hit + len(query) + SEARCH_CONTEXT_CHARS)
            lines.append(f"offset={hit}：…{text[start:end].replace(chr(10), ' ')}…")
        return "\n".join(lines)

    async def list_attachments(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """列出当前可寻址的附件（入站 + 自产）。**空集不是错误**——报错会让模型以为工具坏了。

        租户与归属过滤从 Run 上下文取，**不进工具 schema**：归属一旦成为模型可填的参数，
        越权就只剩一层校验（NFR-SEC-01）。单条 SQL 完成，走既有 `ix_artifact_run` /
        `ix_artifact_conversation`，不做"先查全量再内存过滤"（NFR-PERF-02）。
        """
        scope = arguments.get("scope", SCOPE_DEFAULT)
        if not isinstance(scope, str) or scope not in SCOPES:
            raise AttachmentToolError(
                ATTACHMENT_SCOPE_INVALID, f"scope 只能是 {sorted(SCOPES)}：{scope!r}"
            )
        direction = arguments.get("direction")
        if direction is not None and (
            not isinstance(direction, str) or direction not in DIRECTIONS
        ):
            raise AttachmentToolError(
                ATTACHMENT_DIRECTION_INVALID,
                f"direction 只能是 {sorted(DIRECTIONS)}：{direction!r}",
            )
        offset, limit = list_window(arguments)

        stmt = (
            sa.select(Artifact)
            .where(Artifact.tenant_id == self._tenant_id, Artifact.is_deleted.is_(False))
            .order_by(Artifact.create_time.desc())
            .limit(limit)
            .offset(offset)
        )
        if scope == SCOPE_RUN:
            stmt = stmt.where(Artifact.run_id == self._run_id)
        else:
            stmt = stmt.where(Artifact.conversation_id == self._conversation_id)
        if direction == DIRECTION_INBOUND:
            stmt = stmt.where(Artifact.artifact_type.in_(INBOUND_ARTIFACT_TYPES))
        elif direction == DIRECTION_OUTBOUND:
            stmt = stmt.where(Artifact.artifact_type == AGENT_OUTPUT_ARTIFACT_TYPE)

        async with self._session_factory()() as session:
            rows = list((await session.execute(stmt)).scalars())

        header = f"[附件 {len(rows)} 条] scope={scope} offset={offset} limit={limit}"
        if not rows:
            return f"{header} —— 当前范围内没有可寻址的附件"
        return "\n".join([header, *(_list_line(row) for row in rows)])

    def _extract(self, row: Artifact) -> str:
        """抽取全量文本，**按 Run 复用**：同一份长文档分多次读只解析一次（NFR-PERF-01）。

        读字节与解析任一步失败都要**指明是哪个文件**，否则模型只能对用户说"有个文件读不了"。
        """
        cached = self._text_cache.get(row.id)
        if cached is not None:
            return cached
        try:
            text = _extract_document(self._read_bytes(row), row.media_type)
        except AttachmentToolError as exc:
            raise AttachmentToolError(exc.code, f"{_filename(row)}：{exc.message}") from exc
        self._text_cache[row.id] = text
        self._text_cache.move_to_end(row.id)
        while len(self._text_cache) > MAX_TEXT_CACHE_ENTRIES:
            self._text_cache.popitem(last=False)
        return text
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

    async def write_artifact(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """把结果写成产物并返回标识，与 `read_attachment` 形成闭环。

        两道前置拒绝都**显式报错**：没有 run 上下文（无处挂产物行），或**没有交付路由**
        （写出来没人收 —— 静默成功等于骗模型说"已经交付"）。
        """
        if self._run_id is None:
            raise AttachmentToolError(ATTACHMENT_WRITE_UNAVAILABLE, "当前运行不支持写出产物")
        if not self._has_delivery_route:
            raise AttachmentToolError(
                ATTACHMENT_WRITE_UNAVAILABLE,
                "当前会话没有可交付的通道，写出的产物无人接收；请直接在回复里给出内容",
            )
        content = arguments.get("content")
        if not isinstance(content, str) or not content:
            raise AttachmentToolError(ATTACHMENT_TYPE_UNSUPPORTED, "content 必填且为非空字符串")
        raw_name = arguments.get("filename")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "产出.md"

        data = content.encode("utf-8")
        if len(data) > MAX_READ_BYTES:
            raise AttachmentToolError(
                ATTACHMENT_TOO_LARGE, f"产物超过上限（{MAX_READ_BYTES} 字节）"
            )

        artifact_id = uuid.uuid4()
        storage_key = f"outbound/{self._run_id}/{artifact_id}"
        path = NfsArtifactStore(self._artifact_root).resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.parent / f".tmp-{uuid.uuid4().hex}"
        temp.write_bytes(data)
        os.replace(temp, path)  # 原子替换：不留半成品

        row = Artifact(
            id=artifact_id,
            tenant_id=self._tenant_id,
            run_id=self._run_id,
            task_id=None,
            conversation_id=self._conversation_id,
            artifact_type=AGENT_OUTPUT_ARTIFACT_TYPE,
            storage_key=storage_key,
            media_type="text/markdown",
            size=len(data),
            checksum="sha256:" + hashlib.sha256(data).hexdigest(),
            metadata_json={"kind": "DOCUMENT", "filename": name, "source": "agent"},
        )
        async with self._session_factory()() as session:
            session.add(row)
            await session.commit()
        return f"{WRITE_ARTIFACT_RESULT_PREFIX}{artifact_id}）：{name}"

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


def _list_line(row: Artifact) -> str:
    """枚举里的一行：`id · 文件名 · MIME · 大小 · 方向 · 时间`。

    **id 放最前且不加任何修饰** —— 模型要能原样复制去调 `read_attachment`/`search_attachment`；
    方向用类型判定（出站只有 `AGENT_OUTPUT` 一种），不按"有没有 run_id"——后台任务的自产产物
    `run_id` 为空，那样判会把它误报成入站。
    """
    direction = (
        DIRECTION_OUTBOUND
        if row.artifact_type == AGENT_OUTPUT_ARTIFACT_TYPE
        else DIRECTION_INBOUND
    )
    created = row.create_time.isoformat() if row.create_time is not None else "-"
    return (
        f"- {row.id} · {_filename(row)} · {row.media_type} · {row.size} B"
        f" · {DIRECTION_LABELS[direction]} · {created}"
    )
