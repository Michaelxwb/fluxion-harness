from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from muad_artifact_store import NfsArtifactStore, publish_if_absent
from muad_common import SharedSettings

ARTIFACT_FILE_NAME = "skill.zip"


def artifact_storage_key(skill_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"skills/{skill_id}/{artifact_id}/{ARTIFACT_FILE_NAME}"


def _root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else Path(SharedSettings().artifact_root)


def artifact_path(storage_key: str, *, root: Path | str | None = None) -> Path:
    return NfsArtifactStore(_root(root)).resolve(storage_key)


def write_artifact(storage_key: str, data: bytes, *, root: Path | str | None = None) -> None:
    """不可变写：同一 `storage_key` 二次写入抛 `FileExistsError`（`RULE-skill-001`）。

    发布走共享原语 `publish_if_absent`（临时文件 + `os.link`，原子判定交给内核）。**技能的键是
    确定性的**（`skills/{skill_id}/{artifact_id}/skill.zip`），导入重试/并发导入会让同一个键被
    写到第二次 —— 而"先检查再替换"在那个场景下两个写者都能"成功"，后者静默覆盖前者。
    """
    target = artifact_path(storage_key, root=root)
    try:
        publish_if_absent(target, data)
    except FileExistsError as exc:
        raise FileExistsError(f"artifact already exists and is immutable: {storage_key}") from exc


def remove_artifact(storage_key: str, *, root: Path | str | None = None) -> None:
    target = artifact_path(storage_key, root=root)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    try:
        target.parent.rmdir()
    except OSError:
        return


def iter_artifact_keys(root: Path | str | None = None) -> Iterator[tuple[str, float]]:
    """遍历 Artifact 根目录下的 skill.zip，产出 (storage_key, mtime)。"""
    base = _root(root)
    skills_root = base / "skills"
    if not skills_root.is_dir():
        return
    for path in sorted(skills_root.glob(f"*/*/{ARTIFACT_FILE_NAME}")):
        if not path.is_file():
            continue
        yield path.relative_to(base).as_posix(), path.stat().st_mtime


def iter_artifact_temp_files(root: Path | str | None = None) -> Iterator[tuple[str, float]]:
    """遍历崩溃残留的 .tmp-* 写入临时文件，产出 (相对路径, mtime)。"""
    base = _root(root)
    skills_root = base / "skills"
    if not skills_root.is_dir():
        return
    for path in sorted(skills_root.glob("*/*/.tmp-*")):
        if not path.is_file():
            continue
        yield path.relative_to(base).as_posix(), path.stat().st_mtime


def cleanup_orphan_files(
    known_keys: set[str],
    *,
    grace_seconds: float = 3600.0,
    root: Path | str | None = None,
    now: float | None = None,
) -> list[str]:
    """删除无 DB 记录且早于宽限期的孤儿文件（宽限期保护进行中的导入事务）。"""
    current = time.time() if now is None else now
    removed: list[str] = []
    for storage_key, mtime in iter_artifact_keys(root):
        if storage_key in known_keys:
            continue
        if current - mtime < grace_seconds:
            continue
        remove_artifact(storage_key, root=root)
        removed.append(storage_key)
    base = _root(root)
    for relative_key, mtime in iter_artifact_temp_files(root):
        if current - mtime < grace_seconds:
            continue
        (base / relative_key).unlink(missing_ok=True)
        removed.append(relative_key)
    return removed
