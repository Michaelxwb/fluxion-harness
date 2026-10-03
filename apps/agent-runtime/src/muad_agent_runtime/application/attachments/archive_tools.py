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

from .output_service import MAX_OUTPUT_BYTES, OutputArtifactWriter
from .tool_results import TOOL_RESULT_ARTIFACT_BYTES

CREATE_ARCHIVE_TOOL = "create_archive"
MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_PATH_BYTES = 1024
ARCHIVE_BYTES_LIMIT = MAX_OUTPUT_BYTES


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


def archive_files(value: object) -> tuple[ArchiveFile, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ARCHIVE_FILES:
        raise ArchiveToolError("ARCHIVE_FILES_INVALID", f"files 必须包含 1..{MAX_ARCHIVE_FILES} 个文本文件")
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
        if len(content) > ARCHIVE_BYTES_LIMIT:
            raise ArchiveToolError("ARCHIVE_TOO_LARGE", "文件总大小超过 ZIP 打包上限")
        data = _utf8(content)
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


def _prepare_archive(raw: object) -> tuple[Any, bytes]:
    """编码 + 校验 + 压缩（纯 CPU，交给 `asyncio.to_thread` 跑）。"""
    files = archive_files(raw)
    return files, build_archive(files)


class ArchiveToolSet:
    def __init__(self, writer: OutputArtifactWriter) -> None:
        self._writer = writer

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
                            "maxItems": MAX_ARCHIVE_FILES,
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
        files, data = await asyncio.to_thread(_prepare_archive, arguments["files"])
        row = await self._writer.save(data, filename=filename, media_type="application/zip", kind="OTHER")
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
        while preview and len(receipt.encode("utf-8")) >= TOOL_RESULT_ARTIFACT_BYTES:
            preview.pop()
            result["files_truncated"] = True
            receipt = json.dumps(result, ensure_ascii=False)
        return receipt
