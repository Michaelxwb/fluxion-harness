"""草稿文件存储：`skill-drafts/{tenant}/{user}/{key}` 下的路径安全、原子写与 zip 导出。"""

from __future__ import annotations

import base64
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings

from ..infrastructure.models.control import SkillDraft

DRAFT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ZIP_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class DraftFile:
    path: str
    content: str
    encoding: str
    size: int


def invalid_path(path: str) -> AppError:
    return AppError(ErrorCode.DRAFT_PATH_INVALID, data={"path": path})


def validate_draft_key(key: str) -> str:
    if not DRAFT_KEY_PATTERN.fullmatch(key) or ".." in key:
        raise invalid_path(key)
    return key


def normalize_relative_path(raw: str, *, allow_empty: bool = False) -> PurePosixPath:
    """规范化草稿内相对路径；绝对路径、`..`、反斜杠与盘符一律拒绝。"""
    if not isinstance(raw, str) or "\x00" in raw or "\\" in raw or re.match(r"^[A-Za-z]:", raw):
        raise invalid_path(str(raw))
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise invalid_path(raw)
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts and not allow_empty:
        raise invalid_path(raw)
    return PurePosixPath(*parts)


class SkillDraftStore:
    def __init__(
        self,
        root: Path | str,
        *,
        quota_bytes: int,
        file_limit: int,
        file_max_bytes: int,
    ) -> None:
        self._root = Path(root)
        self.quota_bytes = quota_bytes
        self.file_limit = file_limit
        self.file_max_bytes = file_max_bytes

    @classmethod
    def from_settings(cls, settings: SharedSettings | None = None) -> SkillDraftStore:
        active = settings or SharedSettings()
        return cls(
            active.skill_draft_root,
            quota_bytes=active.skill_draft_quota_mib * 1024 * 1024,
            file_limit=active.skill_draft_file_limit,
            file_max_bytes=active.skill_draft_file_max_bytes,
        )

    def draft_dir(self, draft: SkillDraft) -> Path:
        validate_draft_key(draft.key)
        return self._root / draft.tenant_id / str(draft.owner_user_id) / draft.key

    def ensure(self, draft: SkillDraft) -> Path:
        directory = self.draft_dir(draft)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def read(self, draft: SkillDraft, raw_path: str) -> DraftFile | dict[str, Any]:
        relative = normalize_relative_path(raw_path, allow_empty=True)
        target = self._safe_target(draft, relative)
        if not target.exists() and not target.is_symlink():
            if not relative.parts:
                raise AppError(ErrorCode.DRAFT_NOT_FOUND)
            raise AppError(ErrorCode.DRAFT_NOT_FOUND)
        if target.is_symlink():
            raise invalid_path(raw_path)
        if target.is_dir():
            entries = self._entries(draft, relative)
            return {"type": "directory", "path": relative.as_posix(), "entries": entries}
        data = target.read_bytes()
        try:
            content = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            content = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        return DraftFile(path=relative.as_posix(), content=content, encoding=encoding, size=len(data))

    def write(self, draft: SkillDraft, raw_path: str, content: str) -> int:
        return self.write_bytes(draft, raw_path, content.encode("utf-8"))

    def write_bytes(self, draft: SkillDraft, raw_path: str, data: bytes) -> int:
        relative = normalize_relative_path(raw_path)
        target = self._safe_target(draft, relative)
        if len(data) > self.file_max_bytes:
            raise AppError(
                ErrorCode.DRAFT_QUOTA_EXCEEDED,
                data={"reason": "FILE_TOO_LARGE", "path": relative.as_posix()},
            )
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_dir():
            raise invalid_path(raw_path)
        descriptor, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return len(data)

    def remove_file(self, draft: SkillDraft, raw_path: str) -> int:
        relative = normalize_relative_path(raw_path)
        target = self._safe_target(draft, relative)
        if not target.exists() or target.is_dir():
            raise AppError(ErrorCode.DRAFT_NOT_FOUND)
        if target.is_symlink():
            raise invalid_path(raw_path)
        size = target.stat().st_size
        target.unlink()
        self._prune_empty_dirs(target.parent, self.draft_dir(draft))
        return size

    def remove_draft(self, draft: SkillDraft) -> None:
        directory = self.draft_dir(draft)
        if not directory.exists() and not directory.is_symlink():
            return
        if directory.is_symlink():
            raise invalid_path(draft.key)
        shutil.rmtree(directory)

    def existing_size(self, draft: SkillDraft, raw_path: str) -> int | None:
        relative = normalize_relative_path(raw_path)
        target = self._safe_target(draft, relative)
        if not target.exists() or target.is_dir() or target.is_symlink():
            return None
        return target.stat().st_size

    def export_zip(self, draft: SkillDraft) -> bytes:
        directory = self.draft_dir(draft)
        if not directory.is_dir():
            raise AppError(ErrorCode.DRAFT_NOT_FOUND)
        buffer = _ZipBuffer()
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise invalid_path(path.relative_to(directory).as_posix())
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            info = zipfile.ZipInfo(relative, date_time=ZIP_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            buffer.writestr(info, path.read_bytes())
        return buffer.bytes()

    def _entries(self, draft: SkillDraft, relative: PurePosixPath) -> list[dict[str, Any]]:
        target = self._safe_target(draft, relative)
        entries: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda item: item.name):
            if child.is_symlink():
                raise invalid_path(child.relative_to(self.draft_dir(draft)).as_posix())
            child_path = relative / child.name
            entries.append(
                {
                    "name": child.name,
                    "path": child_path.as_posix(),
                    "type": "directory" if child.is_dir() else "file",
                    "size": 0 if child.is_dir() else child.stat().st_size,
                }
            )
        return entries

    def _safe_target(self, draft: SkillDraft, relative: PurePosixPath) -> Path:
        base = self.draft_dir(draft)
        probe = base
        for part in relative.parts:
            probe = probe / part
            if probe.is_symlink():
                raise invalid_path(relative.as_posix())
        resolved_base = base.resolve()
        target = base.joinpath(*relative.parts)
        resolved = target.resolve()
        if resolved != resolved_base and resolved_base not in resolved.parents:
            raise invalid_path(relative.as_posix())
        return target

    @staticmethod
    def _prune_empty_dirs(directory: Path, stop: Path) -> None:
        current = directory
        while current != stop and current.is_dir():
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent


class _ZipBuffer:
    """小包装：把 zip 写到内存，避免调用方直接管理 BytesIO 生命周期。"""

    def __init__(self) -> None:
        self._stream = io.BytesIO()
        self._archive = zipfile.ZipFile(self._stream, "w", zipfile.ZIP_DEFLATED)

    def writestr(self, info: zipfile.ZipInfo, data: bytes) -> None:
        self._archive.writestr(info, data)

    def bytes(self) -> bytes:
        self._archive.close()
        return self._stream.getvalue()
