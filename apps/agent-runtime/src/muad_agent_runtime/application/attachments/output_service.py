"""Persist user-facing text or binary artifacts with run-scoped ownership."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from muad_agent_core.skill import OutputFile
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
    #: 产物归属：历史产出按 agent + user 检索（跨会话）。
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


def output_version_key(run_id: uuid.UUID | None, artifact_id: uuid.UUID, version: int | str) -> str:
    """产物的不可变存储键。`version` 允许是**不透明后缀**（追加写用 uuid），不一定是序号。"""
    return f"outbound/{run_id}/{artifact_id}/v{version}"


class OutputArtifactWriter:
    def __init__(
        self, *, artifact_root: str | Path, session_factory: SessionFactoryProvider, scope: OutputScope
    ) -> None:
        self._store = NfsArtifactStore(artifact_root)
        self._session_factory = session_factory
        self.scope = scope

    async def save(
        self,
        data: bytes,
        *,
        filename: str,
        media_type: str,
        kind: Literal["DOCUMENT", "OTHER"],
        metadata: dict[str, object] | None = None,
    ) -> Artifact:
        if self.scope.run_id is None:
            raise OutputArtifactError("ATTACHMENT_WRITE_UNAVAILABLE", "当前运行不支持写出产物")
        if len(data) > MAX_OUTPUT_BYTES:
            raise OutputArtifactError("ATTACHMENT_TOO_LARGE", f"产物超过上限（{MAX_OUTPUT_BYTES} 字节）")
        row = self._row(data, filename=filename, media_type=media_type, kind=kind, metadata=metadata)
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
        self,
        data: bytes,
        *,
        filename: str,
        media_type: str,
        kind: Literal["DOCUMENT", "OTHER"],
        metadata: dict[str, object] | None = None,
    ) -> Artifact:
        artifact_id = uuid.uuid4()
        return Artifact(
            id=artifact_id,
            tenant_id=self.scope.tenant_id,
            run_id=self.scope.run_id,
            task_id=None,
            conversation_id=self.scope.conversation_id,
            agent_id=self.scope.agent_id,
            user_id=self.scope.user_id,
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
                **dict(metadata or {}),
            },
        )


class SkillOutputArtifactRegistrar:
    """把 ``SKILL_OUTPUT_DIR`` 中已登记的产物写入既有 Artifact Store。

    只复用 ``OutputArtifactWriter``：产物行仍是 ``AGENT_OUTPUT`` + 本次 Run，
    因此后续仍由现有 ``deliver_artifact``/附件机制交付，不新增下载面。
    """

    def __init__(self, writer: OutputArtifactWriter) -> None:
        self._writer = writer

    async def register(self, output: OutputFile) -> str:
        data = await asyncio.to_thread(output.path.read_bytes)
        media_type = mimetypes.guess_type(output.relative_path)[0] or "application/octet-stream"
        row = await self._writer.save(
            data,
            filename=Path(output.relative_path).name,
            media_type=media_type,
            kind="DOCUMENT",
            metadata={"relative_path": output.relative_path, "skill_output": True},
        )
        return str(row.id)
