"""受限的 ZIP 读取：内存内解压、清单先行、只投影文本成员。

三条边界（对应 harness-im 的压缩包读取规则）：

- **不落盘**：`zipfile.ZipFile(io.BytesIO(...))`，成员字节只在内存里过一遍，不产生任何解压目录，
  因此不存在「解压路径穿越」这一类问题；成员名仍然按 `member_paths` 的规则校验，用于展示。
- **先判后读**：成员数、单成员字节、累计解压字节、单成员压缩比都在**读取途中**判定并中止，
  不先解完再判——无上限的解压是典型的资源耗尽面。
- **只投影文本**：二进制成员与嵌套压缩包只出现在清单里，不出内容、不生成图像块；
  读不了的成员必须在清单里说明原因，不能静默跳过（否则模型会当成「包里没有这个文件」）。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Literal

from muad_contracts import SUPPORTED_UPLOAD_FORMATS, is_archive_media_type, is_text_media_type

from .member_paths import MemberPathError, normalize_member_path

#: 一次读取最多列出的成员数（目录项不计）。
MAX_ARCHIVE_MEMBERS = 200
#: 单个成员解压后的字节上限。
MAX_ARCHIVE_MEMBER_BYTES = 10 * 1024 * 1024
#: 一次读取累计解压字节上限（只统计真正读取的文本成员）。
MAX_ARCHIVE_TOTAL_BYTES = 40 * 1024 * 1024
#: 单成员压缩比上限（解压后 / 压缩后），压缩包炸弹的第二道闸。
MAX_ARCHIVE_COMPRESSION_RATIO = 100

_READ_CHUNK_BYTES = 64 * 1024
_MIB = 1024 * 1024
_TEXT_LABEL = "text"
_BINARY_LABEL = "二进制（不读取）"
_NESTED_LABEL = "嵌套压缩包（不展开）"
_PATH_LABEL = "路径不合法（不读取）"
_ENCODING_LABEL = "非 UTF-8 文本（不读取）"
_EMPTY_FILE_LABEL = "空文件"


class ArchiveReadError(Exception):
    """压缩包不可读。`kind` 让调用方映射到既有错误码：超限 vs 其它解析失败。"""

    def __init__(self, kind: Literal["TOO_LARGE", "INVALID"], message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _Member:
    """清单的一行：展示名、原始大小、可读性与原因（可读时为 None）。"""

    path: str
    size: int
    text: str | None
    note: str | None

    @property
    def readable(self) -> bool:
        return self.text is not None


def render_archive_text(data: bytes) -> str:
    """把压缩包投影成一份可分段文本：`清单 + 文本成员正文`；空包不算错误。"""
    members = _read_members(data)
    return _render(members)


def _read_members(data: bytes) -> tuple[_Member, ...]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveReadError("INVALID", f"压缩包无法解析（可能已损坏）: {exc}") from exc
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ArchiveReadError(
                "TOO_LARGE", f"压缩包成员数超过上限 {MAX_ARCHIVE_MEMBERS}：{len(infos)}"
            )
        members: list[_Member] = []
        total = 0
        for info in infos:
            member, total = _read_member(archive, info, total)
            members.append(member)
    return tuple(members)


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, total: int
) -> tuple[_Member, int]:
    """读一个成员：不合规/非文本只登记原因，文本成员才真正解压并计入累计上限。"""
    path = _display_path(info.filename)
    if path is None:
        return _Member(info.filename, info.file_size, None, _PATH_LABEL), total
    if info.flag_bits & 0x1:
        raise ArchiveReadError("INVALID", "压缩包已加密，无法读取")
    media_type = _member_media_type(path)
    if media_type is not None and is_archive_media_type(media_type):
        return _Member(path, info.file_size, None, _NESTED_LABEL), total
    if media_type is None or not is_text_media_type(media_type):
        return _Member(path, info.file_size, None, _BINARY_LABEL), total
    if info.file_size == 0:
        return _Member(path, 0, "", _EMPTY_FILE_LABEL), total
    _assert_bounded(info)
    raw, total = _read_bounded(archive, info, total)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _Member(path, info.file_size, None, _ENCODING_LABEL), total
    return _Member(path, info.file_size, text, None), total


def _assert_bounded(info: zipfile.ZipInfo) -> None:
    """读取之前的廉价判定：单成员字节与压缩比。"""
    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise ArchiveReadError(
            "TOO_LARGE", f"成员 {info.filename} 解压后超过单成员上限 {MAX_ARCHIVE_MEMBER_BYTES} 字节"
        )
    ratio = info.file_size / max(info.compress_size, 1)
    if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
        raise ArchiveReadError(
            "TOO_LARGE",
            f"成员 {info.filename} 压缩比 {ratio:.0f}:1 超过上限 {MAX_ARCHIVE_COMPRESSION_RATIO}:1",
        )


def _read_bounded(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, total: int
) -> tuple[bytes, int]:
    """按块读取并在途中中止：单成员与累计上限都在这里生效。"""
    chunks: list[bytes] = []
    size = 0
    try:
        with archive.open(info) as handle:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                size += len(chunk)
                total += len(chunk)
                if size > MAX_ARCHIVE_MEMBER_BYTES or total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise ArchiveReadError(
                        "TOO_LARGE",
                        f"解压内容超过上限（单成员 {MAX_ARCHIVE_MEMBER_BYTES} / 累计 "
                        f"{MAX_ARCHIVE_TOTAL_BYTES} 字节）",
                    )
                chunks.append(chunk)
    except RuntimeError as exc:  # 加密成员在 open/read 时才暴露
        raise ArchiveReadError("INVALID", f"成员 {info.filename} 无法读取: {exc}") from exc
    except zipfile.BadZipFile as exc:
        raise ArchiveReadError("INVALID", f"压缩包内容损坏: {exc}") from exc
    return b"".join(chunks), total


def _render(members: tuple[_Member, ...]) -> str:
    readable = [member for member in members if member.readable]
    total_bytes = sum(member.size for member in readable)
    header = (
        f"[压缩包] 共 {len(members)} 个成员（可读文本 {len(readable)} 个，"
        f"解压内容 {_human_size(total_bytes)}）"
    )
    if not members:
        return f"{header}\n压缩包内没有可读文件"
    lines = [header]
    for member in members:
        label = _TEXT_LABEL if member.readable else member.note or _BINARY_LABEL
        lines.append(f"- {member.path} · {_human_size(member.size)} · {label}")
    for member in readable:
        lines.append(f"--- {member.path} ---")
        lines.append(member.text or "")
    return "\n".join(lines)


def _display_path(name: str) -> str | None:
    """规范性校验：不合法返回 None（调用方只在清单里标注，不读取）。"""
    try:
        return normalize_member_path(name)
    except MemberPathError:
        return None


def _member_media_type(path: str) -> str | None:
    """成员名 → 契约里登记的媒体类型；没有扩展名或未登记返回 None。"""
    suffix = _suffix(path)
    if not suffix:
        return None
    for item in SUPPORTED_UPLOAD_FORMATS:
        if suffix in item.extensions:
            return item.media_type
    return None


def _suffix(path: str) -> str:
    _, dot, extension = path.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < _MIB:
        return f"{size / 1024:.1f} KiB"
    return f"{size / _MIB:.1f} MiB"
