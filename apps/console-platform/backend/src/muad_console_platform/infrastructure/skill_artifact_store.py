from __future__ import annotations

import os
import uuid
from pathlib import Path

from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings


def artifact_storage_key(skill_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"skills/{skill_id}/{artifact_id}/skill.zip"


def artifact_path(storage_key: str) -> Path:
    return NfsArtifactStore(SharedSettings().artifact_root).resolve(storage_key)


def write_artifact(storage_key: str, data: bytes) -> None:
    target = artifact_path(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.parent / f".tmp-{uuid.uuid4().hex}"
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def remove_artifact(storage_key: str) -> None:
    target = artifact_path(storage_key)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    try:
        target.parent.rmdir()
    except OSError:
        return
