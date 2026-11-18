"""草稿导入：把上传的代码包（归档附件）按成员写入草稿。

产品口径（2026-11-17 对齐）：
- 只在用户**明确要求**「基于这个包创建或修改 skill」时由模型触发；普通上传（图片、PDF、
  数据文件）不产生任何草稿写入。
- 字节由 Runtime 从 artifact 存储读出、按成员交给 Console 既有的 apply 端点（一次批量变更 +
  revision CAS），模型不参与内容传递。这解决了两个既有缺口：`skill_draft write` 只能写
  UTF-8 文本、整文件覆盖，大文件装不进一条消息，而让模型转写会引入字符错。

边界：
- 非归档媒体类型直接拒绝，不做静默降级（图片/PDF 不属于「基于代码写 skill」）；
- 成员路径走 `member_paths` 的唯一实现；符号链接、加密成员、重复路径一律整包拒绝；
- 单成员与累计解压上限沿用 `archive_reader` 的同一组常量，与包读取口径一致；
- 归属口径与 `attachments/tools.py` 一致：租户 + 未软删 + 经会话或 Run 推出本人。
"""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts import is_archive_media_type

from ..infrastructure.models.runtime import Artifact, Conversation, RunRecord
from ..infrastructure.workspace_store import SessionFactoryProvider
from .attachments.archive_reader import (
    MAX_ARCHIVE_COMPRESSION_RATIO,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_TOTAL_BYTES,
)
from .attachments.member_paths import MemberPathError, normalize_member_path

#: 与 `read_attachment` 的附件读取上限同量级：压缩包本身也要先整体读进内存。
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_SYMLINK_MODE = 0o120000

#: 复用附件读取的既有错误码（消息目录已登记），不为此新增词条。
PACKAGE_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
PACKAGE_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
PACKAGE_EXTRACT_FAILED = "ATTACHMENT_EXTRACT_FAILED"


class PackageImportError(RuntimeError):
    """导入失败：`code` 是既有错误码，`message` 可直接展示给模型/用户。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PackageMember:
    """解出的一个成员：草稿内相对路径 + 原始字节（二进制成员照原样保留）。"""

    path: str
    data: bytes


def extract_package_members(data: bytes) -> tuple[PackageMember, ...]:
    """把压缩包解成成员列表；任何不合规成员都让整包失败（草稿不留半成品）。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageImportError(PACKAGE_EXTRACT_FAILED, "压缩包无法解析（可能已损坏）") from exc
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise PackageImportError(
                PACKAGE_TOO_LARGE,
                f"压缩包成员数超过上限 {MAX_ARCHIVE_MEMBERS}：{len(infos)}",
            )
        members: list[PackageMember] = []
        seen: set[str] = set()
        total = 0
        for info in infos:
            path = _member_path(info)
            if path in seen:
                raise PackageImportError(
                    ErrorCode.DRAFT_PATH_INVALID.value, f"压缩包内有重复路径：{path}"
                )
            seen.add(path)
            raw, total = _read_member(archive, info, total)
            members.append(PackageMember(path=path, data=raw))
    return tuple(members)


def _member_path(info: zipfile.ZipInfo) -> str:
    """成员路径：符号链接与非法名一律拒绝，返回规范化后的相对路径。"""
    if (info.external_attr >> 16) & 0o170000 == _SYMLINK_MODE:
        raise PackageImportError(
            ErrorCode.DRAFT_PATH_INVALID.value, f"压缩包内不允许符号链接：{info.filename}"
        )
    try:
        return normalize_member_path(info.filename)
    except MemberPathError as exc:
        raise PackageImportError(
            ErrorCode.DRAFT_PATH_INVALID.value, f"{info.filename}：{exc}"
        ) from exc


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, total: int
) -> tuple[bytes, int]:
    """读一个成员：单成员与累计上限在读取途中判定并中止，不先解完再判。"""
    if info.flag_bits & 0x1:
        raise PackageImportError(PACKAGE_EXTRACT_FAILED, "压缩包已加密，无法读取")
    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise PackageImportError(
            PACKAGE_TOO_LARGE,
            f"成员 {info.filename} 解压后超过单成员上限 {MAX_ARCHIVE_MEMBER_BYTES} 字节",
        )
    ratio = info.file_size / max(info.compress_size, 1)
    if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
        raise PackageImportError(
            PACKAGE_TOO_LARGE,
            f"成员 {info.filename} 压缩比 {ratio:.0f}:1 超过上限 "
            f"{MAX_ARCHIVE_COMPRESSION_RATIO}:1",
        )
    chunks: list[bytes] = []
    size = 0
    try:
        with archive.open(info) as handle:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                size += len(chunk)
                total += len(chunk)
                if size > MAX_ARCHIVE_MEMBER_BYTES or total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise PackageImportError(
                        PACKAGE_TOO_LARGE,
                        f"解压内容超过上限（单成员 {MAX_ARCHIVE_MEMBER_BYTES} / 累计 "
                        f"{MAX_ARCHIVE_TOTAL_BYTES} 字节）",
                    )
                chunks.append(chunk)
    except PackageImportError:
        # 读取途中的上限判定必须保留原错误码；不要被下面的通用 RuntimeError 包装成“解析失败”。
        raise
    except RuntimeError as exc:  # 加密成员在 open/read 时才暴露
        raise PackageImportError(
            PACKAGE_EXTRACT_FAILED, f"成员 {info.filename} 无法读取: {exc}"
        ) from exc
    except zipfile.BadZipFile as exc:
        raise PackageImportError(PACKAGE_EXTRACT_FAILED, "压缩包内容损坏") from exc
    return b"".join(chunks), total


class ArtifactPackageReader:
    """按 Run 上下文取归档附件字节：租户 + 未软删 + 归属本人，非归档直接拒绝。"""

    def __init__(
        self,
        session_factory: SessionFactoryProvider,
        artifact_root: str | Path,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root

    async def read(self, artifact_id: object, *, tenant_id: str, owner_user_id: str) -> bytes:
        row = await self._row(artifact_id, tenant_id=tenant_id, owner_user_id=owner_user_id)
        if not is_archive_media_type(row.media_type):
            raise PackageImportError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                f"只有压缩包能导入草稿，这份附件是 {row.media_type}；"
                "普通附件用 read_attachment 阅读，不要导入草稿",
            )
        if row.size > MAX_PACKAGE_BYTES:
            raise PackageImportError(
                PACKAGE_TOO_LARGE, f"附件超过可读取上限（{MAX_PACKAGE_BYTES} 字节）"
            )
        try:
            path = NfsArtifactStore(self._artifact_root).resolve(row.storage_key)
            return path.read_bytes()
        except (OSError, ValueError) as exc:
            raise PackageImportError(PACKAGE_NOT_FOUND, "附件内容不可读") from exc

    async def _row(self, artifact_id: object, *, tenant_id: str, owner_user_id: str) -> Any:
        parsed = _parse_uuid(artifact_id)
        owner = _parse_uuid(owner_user_id)
        async with self._session_factory()() as session:
            rows = await session.execute(_scoped_query(tenant_id, parsed, owner))
            row = rows.scalar_one_or_none()
        if row is None:
            # 不存在 / 跨租户 / 别人的附件 / 已软删：同一种语义，不回显存在性
            raise PackageImportError(PACKAGE_NOT_FOUND, "附件不存在或不可访问")
        return row


def _parse_uuid(value: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise PackageImportError(PACKAGE_NOT_FOUND, "附件不存在或不可访问") from exc


def _scoped_query(tenant_id: str, artifact_id: uuid.UUID, user_id: uuid.UUID) -> Any:
    """归属谓词与 `attachments/tools.py::_owned_by_actor` 同一口径：

    `artifact` 的归属由它的会话（`conversation.user_id`）或它的 Run（`run_record.user_id`）
    推出；两个 EXISTS 走既有索引，不做「先查全量再内存过滤」。
    """
    by_conversation = (
        sa.select(sa.literal(1))
        .where(
            Conversation.id == Artifact.conversation_id,
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
            Conversation.is_deleted.is_(False),
        )
        .exists()
    )
    by_run = (
        sa.select(sa.literal(1))
        .where(
            RunRecord.id == Artifact.run_id,
            RunRecord.tenant_id == tenant_id,
            RunRecord.user_id == user_id,
            RunRecord.is_deleted.is_(False),
        )
        .exists()
    )
    return sa.select(Artifact).where(
        Artifact.id == artifact_id,
        Artifact.tenant_id == tenant_id,
        Artifact.is_deleted.is_(False),
        sa.or_(by_conversation, by_run),
    )


__all__ = [
    "ArtifactPackageReader",
    "PackageImportError",
    "PackageMember",
    "extract_package_members",
]
