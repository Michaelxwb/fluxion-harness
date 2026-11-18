"""草稿文件存储：`skill-drafts/{tenant}/{user}/{agent}/{key}` 下的路径安全、原子写与 zip 导出。"""

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
from muad_common import SharedSettings, process_settings
from muad_common.draft_testing import draft_test_data_dir
from muad_common.workspaces import DRAFT_ACTIVITY_MARKER

from ..infrastructure.models.control import SkillDraft

DRAFT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ZIP_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FILE_NOT_FOUND_REPAIR = (
    'read the draft root with path="" to list the available files; write(path) recreates a missing file'
)
STORAGE_MISSING_IMPORT_REPAIR = (
    "import the effective artifact into the draft (action=import artifact_id=<effective_artifact_id>)"
)
STORAGE_MISSING_RECREATE_REPAIR = (
    "delete the draft (action=delete without path) and create it again with action=create key=<key>; "
    "files that were never submitted cannot be recovered"
)


@dataclass(frozen=True, slots=True)
class DraftFile:
    path: str
    content: str
    encoding: str
    size: int


class DraftRestoreError(Exception):
    """制品不能安全还原成草稿目录；由应用服务映射为 DRAFT_STORAGE_MISSING。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def invalid_path(path: str) -> AppError:
    return AppError(ErrorCode.DRAFT_PATH_INVALID, data={"path": path})


def file_not_found(draft: SkillDraft, path: str) -> AppError:
    return AppError(
        ErrorCode.DRAFT_FILE_NOT_FOUND,
        data={
            "draft_id": str(draft.id),
            "path": path,
            "repair": FILE_NOT_FOUND_REPAIR,
        },
    )


def storage_missing(
    draft: SkillDraft,
    path: str,
    *,
    effective_artifact_id: str | None = None,
    effective_artifact_version: str | None = None,
    reason: str | None = None,
) -> AppError:
    data: dict[str, Any] = {
        "draft_id": str(draft.id),
        "key": draft.key,
        "path": path,
        "effective_artifact_id": effective_artifact_id,
        "effective_artifact_version": effective_artifact_version,
        "repair": (
            STORAGE_MISSING_IMPORT_REPAIR
            if effective_artifact_id is not None
            else STORAGE_MISSING_RECREATE_REPAIR
        ),
    }
    if reason is not None:
        data["reason"] = reason
    return AppError(ErrorCode.DRAFT_STORAGE_MISSING, data=data)


def _safe_component(value: str, label: str) -> str:
    text = str(value)
    if not text or text in {".", ".."} or "/" in text or "\\" in text or "\x00" in text:
        raise ValueError(f"invalid draft {label}: {value!r}")
    return text


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
        settings = settings or process_settings()
        # 草稿落在 workspaces 卷内：{root}/{tenant}/{user}/{agent}/skill-drafts/{key}，
        # 与 outputs 同级，随 agent 目录统一清理。
        return cls(
            settings.workspace_root,
            quota_bytes=settings.skill_draft_quota_mib * 1024 * 1024,
            file_limit=settings.skill_draft_file_limit,
            file_max_bytes=settings.skill_draft_file_max_bytes,
        )

    def draft_dir(self, draft: SkillDraft) -> Path:
        validate_draft_key(draft.key)
        return (
            self._root
            / _safe_component(draft.tenant_id, "tenant_id")
            / _safe_component(str(draft.owner_user_id), "owner_user_id")
            / _safe_component(str(draft.agent_id), "agent_id")
            / "skill-drafts"
            / draft.key
        )

    def ensure(self, draft: SkillDraft) -> Path:
        directory = self.draft_dir(draft)
        if directory.is_symlink():
            raise invalid_path(draft.key)
        directory.mkdir(parents=True, exist_ok=True)
        self.touch_activity(draft)
        return directory

    def exists(self, draft: SkillDraft) -> bool:
        directory = self.draft_dir(draft)
        return directory.is_dir() and not directory.is_symlink()

    def touch_activity(self, draft: SkillDraft) -> None:
        """刷新 agent 目录上的草稿活动标记：workspace 清理据此保活。"""
        agent_dir = self.draft_dir(draft).parent.parent
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / DRAFT_ACTIVITY_MARKER).touch(exist_ok=True)

    def read(self, draft: SkillDraft, raw_path: str) -> DraftFile | dict[str, Any]:
        relative = normalize_relative_path(raw_path, allow_empty=True)
        target = self._safe_target(draft, relative)
        if not target.exists() and not target.is_symlink():
            if relative.parts:
                raise file_not_found(draft, relative.as_posix())
            raise storage_missing(draft, "")
        if target.is_symlink():
            raise invalid_path(raw_path)
        if target.is_dir():
            entries = self._entries(draft, relative)
            path = relative.as_posix() if relative.parts else ""
            return {"type": "directory", "path": path, "entries": entries}
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
        self.touch_activity(draft)
        return len(data)

    def remove_file(self, draft: SkillDraft, raw_path: str) -> int:
        relative = normalize_relative_path(raw_path)
        target = self._safe_target(draft, relative)
        if not target.exists() or target.is_dir():
            raise file_not_found(draft, relative.as_posix())
        if target.is_symlink():
            raise invalid_path(raw_path)
        size = target.stat().st_size
        target.unlink()
        self._prune_empty_dirs(target.parent, self.draft_dir(draft))
        self.touch_activity(draft)
        return size

    def remove_draft(self, draft: SkillDraft) -> None:
        directory = self.draft_dir(draft)
        if directory.is_symlink():
            raise invalid_path(draft.key)
        try:
            test_data = draft_test_data_dir(directory.parent.parent, str(draft.id))
        except ValueError as exc:
            raise invalid_path(draft.key) from exc
        if directory.exists():
            shutil.rmtree(directory)
        if test_data.parent.exists():
            shutil.rmtree(test_data.parent)

    def existing_size(self, draft: SkillDraft, raw_path: str) -> int | None:
        relative = normalize_relative_path(raw_path)
        target = self._safe_target(draft, relative)
        if not target.exists() or target.is_dir() or target.is_symlink():
            return None
        return target.stat().st_size

    def restore_zip(self, draft: SkillDraft, data: bytes) -> None:
        """从已提交制品还原缺失的草稿目录；先完整校验再原子换入。"""
        members = self._read_restore_members(data)
        directory = self.draft_dir(draft)
        if directory.is_symlink():
            raise invalid_path(draft.key)
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".restore-{draft.key}-", dir=directory.parent))
        try:
            for relative, payload in members:
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            if directory.exists():
                # 并发请求已恢复或创建了目录：保留现有内容，丢弃本次副本。
                shutil.rmtree(staging)
                return
            try:
                os.replace(staging, directory)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)
                if not directory.exists():
                    raise
                return
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.touch_activity(draft)

    def _read_restore_members(self, data: bytes) -> list[tuple[PurePosixPath, bytes]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise DraftRestoreError("ARTIFACT_INVALID") from exc
        members: list[tuple[PurePosixPath, bytes]] = []
        seen: set[str] = set()
        total = 0
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise DraftRestoreError("ARTIFACT_SYMLINK")
                try:
                    relative = normalize_relative_path(info.filename)
                except AppError as exc:
                    raise DraftRestoreError("ARTIFACT_PATH_INVALID") from exc
                key = relative.as_posix()
                if key in seen:
                    raise DraftRestoreError("ARTIFACT_DUPLICATE_PATH")
                seen.add(key)
                if info.file_size > self.file_max_bytes:
                    raise DraftRestoreError("ARTIFACT_FILE_TOO_LARGE")
                total += info.file_size
                if len(members) >= self.file_limit or total > self.quota_bytes:
                    raise DraftRestoreError("ARTIFACT_QUOTA_EXCEEDED")
                try:
                    payload = archive.read(info)
                except (RuntimeError, NotImplementedError) as exc:
                    raise DraftRestoreError("ARTIFACT_INVALID") from exc
                members.append((relative, payload))
        if not members:
            raise DraftRestoreError("ARTIFACT_EMPTY")
        return members

    def export_zip(self, draft: SkillDraft) -> bytes:
        directory = self.draft_dir(draft)
        if not directory.is_dir():
            raise storage_missing(draft, "")
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
