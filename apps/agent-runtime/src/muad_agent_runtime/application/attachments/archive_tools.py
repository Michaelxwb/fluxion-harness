"""Create real ZIP artifacts from explicit text files without executing commands."""

from __future__ import annotations

import asyncio
import io
import json
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_contracts.platform_settings import ArtifactSettings

from .output_service import MAX_OUTPUT_BYTES, OutputArtifactError, OutputArtifactWriter
from .tool_results import TOOL_RESULT_ARTIFACT_BYTES

CREATE_ARCHIVE_TOOL = "create_archive"
MAX_ARCHIVE_PATH_BYTES = 1024
ARCHIVE_BYTES_LIMIT = MAX_OUTPUT_BYTES
#: `_utf8_capped` 的分块大小（按**字符**计）
_ENCODE_CHUNK_CHARS = 64 * 1024


class ArchiveToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ArchiveFile:
    path: str
    data: bytes


def _utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ArchiveToolError("ARCHIVE_CONTENT_INVALID", "文件内容和路径必须是有效 UTF-8 文本") from exc


def _utf8_capped(content: str, limit: int) -> bytes:
    """编码为 UTF-8，**一旦超过 limit 就失败**，不物化整个字节对象。

    只按字符数预检是不够的：CJK / 非 BMP 文本编码后可达字符数的 3–4×，一段接近上限的
    50 MiB 字符会先被编码成 ~200 MiB 的字节对象**再**被拒——纯放大，而且这段编码跑在
    `asyncio.to_thread` 的线程里，照样吃 worker 的内存。分块编码（按**字符**切，切片永远
    落在码位边界）把峰值压到 `limit` + 一个块（2026-10-03 review）。
    """
    if len(content) > limit:  # 快路径：字节数恒 ≥ 字符数，字符数已超则必然超
        raise ArchiveToolError("ARCHIVE_TOO_LARGE", "单个文件超过 ZIP 打包上限")
    chunks: list[bytes] = []
    size = 0
    for start in range(0, len(content), _ENCODE_CHUNK_CHARS):
        chunk = _utf8(content[start : start + _ENCODE_CHUNK_CHARS])
        size += len(chunk)
        if size > limit:
            raise ArchiveToolError("ARCHIVE_TOO_LARGE", "单个文件超过 ZIP 打包上限")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_member_path(value: object) -> str:
    """校验并**规范化**成员路径，返回最终写进 ZIP 的名字。

    两处规范化/收紧是 2026-10-03 review 补的，都会**改写入包里的名字**或**收紧拒绝面**：

    - **NFC 规范化**：NFC 与 NFD 是**同一个名字**（`é` 的两种编码）。不规范化的话它们会作为
      两个成员写进 ZIP，而在 macOS/Windows 上解压时**互相覆盖**。规范化后二者自然撞成
      "重复路径"，按既有规则拒绝——方向是安全的（拒绝，而不是静默丢一份）。
    - **控制/格式字符按 Unicode 类别判**：原来只挡 `ord < 32` 与 127（纯 ASCII），
      漏掉了 C1 控制符（`Cc`）与**零宽字符 / 双向覆盖符**（`Cf`，如 U+202E）——成员名里带这些
      会造成**显示欺骗**（文件名看起来是 `gpj.png`，实际是 `gnp.png`）。
    - **纯空白段**：`" "`、`"a/ /b"` 这类原来能过（空格不在 `{"", ".", ".."}` 里，也不落 ord 检查）。
    """
    if not isinstance(value, str) or not value:
        raise ArchiveToolError("ARCHIVE_PATH_INVALID", "文件路径必须是非空相对路径")
    path = unicodedata.normalize("NFC", value)
    if (
        "\\" in path
        or ":" in path
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in path)
        or any(part in {"", ".", ".."} or not part.strip() for part in path.split("/"))
        or len(_utf8(path)) > MAX_ARCHIVE_PATH_BYTES
    ):
        raise ArchiveToolError("ARCHIVE_PATH_INVALID", "文件路径不能包含绝对路径、目录逃逸或无效字符")
    return path


def archive_filename(value: object) -> str:
    name = validate_member_path(value)
    if "/" in name or not name.lower().endswith(".zip") or name.lower() == ".zip":
        raise ArchiveToolError("ARCHIVE_FILENAME_INVALID", "filename 必须是 ZIP 文件名，不能包含目录")
    return name


def archive_files(value: object, *, max_files: int | None = None) -> tuple[ArchiveFile, ...]:
    #: 文件数上限来自本次 Run 冻结的 `artifact.max_archive_files`；缺失时取 contracts schema 默认
    #: （单一来源，不在此复制一份常量）。
    limit = max_files if max_files is not None else ArtifactSettings().max_archive_files
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise ArchiveToolError("ARCHIVE_FILES_INVALID", f"files 必须包含 1..{limit} 个文本文件")
    files: list[ArchiveFile] = []
    names: set[str] = set()
    folded: set[str] = set()
    directories: set[str] = set()
    total = 0
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "content"}:
            raise ArchiveToolError("ARCHIVE_FILES_INVALID", "每个文件仅接受 path 和 content")
        path = validate_member_path(item["path"])
        parents = {str(parent) for parent in PurePosixPath(path).parents if str(parent) != "."}
        if path in names or path in directories or names & parents:
            raise ArchiveToolError("ARCHIVE_PATH_CONFLICT", "文件路径重复或存在文件与目录冲突")
        # **大小写冲突单独拒**：`A.txt` 与 `a.txt` 在 macOS/Windows 上是**同一个文件**，
        # 解压时后者静默覆盖前者——那不是"严格与否"，是**丢文件**。在创建时报错，
        # 作者改个名字即可；等用户在 Windows 上解压才发现，代价高得多。
        if path.casefold() in folded:
            raise ArchiveToolError(
                "ARCHIVE_PATH_CONFLICT",
                f"文件路径大小写冲突（在大小写不敏感的文件系统上会互相覆盖）：{path}",
            )
        content = item["content"]
        if not isinstance(content, str):
            raise ArchiveToolError("ARCHIVE_CONTENT_INVALID", "content 必须是文本字符串")
        # 单项按**编码后的字节数**判，再累加；按字符数判会让 CJK 串带着 3–4× 的字节对象
        # 走到这里才被拒（见 `_utf8_capped`）
        data = _utf8_capped(content, ARCHIVE_BYTES_LIMIT)
        total += len(data)
        if total > ARCHIVE_BYTES_LIMIT:
            raise ArchiveToolError("ARCHIVE_TOO_LARGE", "文件总大小超过 ZIP 打包上限")
        files.append(ArchiveFile(path, data))
        names.add(path)
        folded.add(path.casefold())
        directories.update(parents)
    return tuple(files)


def build_archive(files: tuple[ArchiveFile, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            archive.writestr(item.path, item.data)
    data = buffer.getvalue()
    if len(data) > ARCHIVE_BYTES_LIMIT:
        raise ArchiveToolError("ARCHIVE_TOO_LARGE", "生成的 ZIP 文件超过上限")
    return data


def _prepare_archive(raw: object, max_files: int) -> tuple[Any, bytes]:
    """编码 + 校验 + 压缩（纯 CPU，交给 `asyncio.to_thread` 跑）。"""
    files = archive_files(raw, max_files=max_files)
    return files, build_archive(files)


class ArchiveToolSet:
    def __init__(
        self,
        writer: OutputArtifactWriter,
        *,
        receipt_limit_bytes: int = TOOL_RESULT_ARTIFACT_BYTES,
        max_archive_files: int | None = None,
    ) -> None:
        """`receipt_limit_bytes` = **本次 Run 生效的**外置阈值（冻结配置里的
        `tool_result.persist_threshold_bytes`）。

        回执必须裁到这条线**以下**，否则它自己会被外置成引用，模型就拿不到
        「已生成 ZIP（附件 ID …）。需要发给用户时请调用 deliver_artifact」这句指引。
        与 `ToolCallRecorder` 用**同一个生效值**（`harness-skill` 的 RULE-skill-001 要求
        阈值只有一处事实来源；硬编码常量只在没有冻结配置时兜底）。

        `max_archive_files` = **本次 Run 冻结的** `artifact.max_archive_files`；缺失时取 contracts
        schema 默认（单一来源）。它同时钉住工具 schema 的 `maxItems` 与执行期校验，二者必须同值。
        """
        self._writer = writer
        self._receipt_limit_bytes = receipt_limit_bytes
        self._max_archive_files = (
            max_archive_files
            if max_archive_files is not None
            else ArtifactSettings().max_archive_files
        )

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                name=CREATE_ARCHIVE_TOOL,
                description=(
                    "Create a real ZIP file from explicitly supplied relative paths and UTF-8 text "
                    "contents, preserving directories. Use this to package generated Skill files. "
                    "Only supplied files are included. Returns an artifact_id; call deliver_artifact "
                    "to send it to the user. This does not import or validate a Skill. "
                    "Existing artifact references and binary input are not supported."
                ),
                input_schema={
                    "type": "object",
                    "required": ["filename", "files"],
                    "additionalProperties": False,
                    "properties": {
                        "filename": {"type": "string"},
                        "files": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": self._max_archive_files,
                            "items": {
                                "type": "object",
                                "required": ["path", "content"],
                                "additionalProperties": False,
                                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                            },
                        },
                    },
                },
                effect=ToolEffect.WRITE,
                handler=self.create_archive,
            )
        )

    async def create_archive(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        if set(arguments) != {"filename", "files"}:
            raise ArchiveToolError("ARCHIVE_ARGUMENTS_INVALID", "必须且仅提供 filename 和 files")
        filename = archive_filename(arguments["filename"])
        # **整段**留在事件循环外：UTF-8 编码、逐项校验、压缩都是纯 CPU 且无 IO，
        # 而输入可达 50 MiB。只把 `build_archive` 挪进线程，等于把最贵的编码与校验留在循环上
        # （2026-10-03 review）。
        files, data = await asyncio.to_thread(
            _prepare_archive, arguments["files"], self._max_archive_files
        )
        try:
            row = await self._writer.save(
                data, filename=filename, media_type="application/zip", kind="OTHER"
            )
        except OutputArtifactError as exc:
            # 与 `attachments.tools.write_artifact` **同口径**：存储/大小失败一律翻成工具自己的
            # 错误类型。直接放 `OutputArtifactError` 出去会让调用方拿到一个既不是本工具、也没
            # 走本工具错误码的错误（2026-10-03 review）。
            raise ArchiveToolError(exc.code, exc.message) from exc
        preview = [item.path for item in files[:3]]
        result = {
            "artifact_id": str(row.id),
            "filename": filename,
            "media_type": row.media_type,
            "size": row.size,
            "checksum": row.checksum,
            "file_count": len(files),
            "files": preview,
            "files_truncated": len(files) > 3,
            "message": f"已生成 ZIP（附件 ID {row.id}）。需要发给用户时请调用 deliver_artifact。",
        }
        receipt = json.dumps(result, ensure_ascii=False)
        while preview and len(receipt.encode("utf-8")) >= self._receipt_limit_bytes:
            preview.pop()
            result["files_truncated"] = True
            receipt = json.dumps(result, ensure_ascii=False)
        return receipt
