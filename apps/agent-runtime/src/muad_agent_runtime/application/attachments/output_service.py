"""Persist user-facing text or binary artifacts with run-scoped ownership."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from muad_artifact_store import NfsArtifactStore

from ...infrastructure.db import SessionFactoryProvider
from ...infrastructure.models.runtime import Artifact

AGENT_OUTPUT_ARTIFACT_TYPE = "AGENT_OUTPUT"
MAX_OUTPUT_BYTES = 50 * 1024 * 1024


class OutputArtifactError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class OutputScope:
    tenant_id: str
    run_id: uuid.UUID | None
    conversation_id: uuid.UUID | None


def output_version_key(run_id: uuid.UUID | None, artifact_id: uuid.UUID, version: int) -> str:
    return f"outbound/{run_id}/{artifact_id}/v{version}"


class OutputArtifactWriter:
    def __init__(
        self, *, artifact_root: str | Path, session_factory: SessionFactoryProvider, scope: OutputScope
    ) -> None:
        self._store = NfsArtifactStore(artifact_root)
        self._session_factory = session_factory
        self.scope = scope

    async def save(
        self, data: bytes, *, filename: str, media_type: str, kind: Literal["DOCUMENT", "OTHER"]
    ) -> Artifact:
        if self.scope.run_id is None:
            raise OutputArtifactError("ATTACHMENT_WRITE_UNAVAILABLE", "当前运行不支持写出产物")
        if len(data) > MAX_OUTPUT_BYTES:
            raise OutputArtifactError("ATTACHMENT_TOO_LARGE", f"产物超过上限（{MAX_OUTPUT_BYTES} 字节）")
        row = self._row(data, filename=filename, media_type=media_type, kind=kind)
        try:
            path = self._store.write(row.storage_key, data)
        except OSError as exc:
            raise OutputArtifactError("ARTIFACT_WRITE_FAILED", "产物文件保存失败") from exc
        try:
            async with self._session_factory()() as session:
                session.add(row)
                await session.commit()
        except BaseException as exc:
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                exc.add_note(f"Artifact cleanup failed: {cleanup_error}")
            raise
        return row

    def _row(
        self, data: bytes, *, filename: str, media_type: str, kind: Literal["DOCUMENT", "OTHER"]
    ) -> Artifact:
        artifact_id = uuid.uuid4()
        return Artifact(
            id=artifact_id,
            tenant_id=self.scope.tenant_id,
            run_id=self.scope.run_id,
            task_id=None,
            conversation_id=self.scope.conversation_id,
            artifact_type=AGENT_OUTPUT_ARTIFACT_TYPE,
            storage_key=output_version_key(self.scope.run_id, artifact_id, 1),
            media_type=media_type,
            size=len(data),
            checksum="sha256:" + hashlib.sha256(data).hexdigest(),
            metadata_json={
                "kind": kind,
                "filename": filename,
                "source": "agent",
                "version": 1,
                "versions": [],
            },
        )
