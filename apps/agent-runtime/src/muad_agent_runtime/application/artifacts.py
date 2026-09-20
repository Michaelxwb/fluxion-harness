"""Tool 大结果 Artifact 落盘与引用（不可变 temp+replace；DB 失败清理文件）。"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import get_session_factory
from ..infrastructure.models.runtime import Artifact

PREVIEW_DEFAULT_LIMIT = 200


def _storage_key(tenant_id: str, run_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"tools/{tenant_id}/{run_id}/{artifact_id}/result.bin"


class ArtifactResultWriter:
    def __init__(self, artifact_root: Path | str, session_factory=None) -> None:
        self._artifact_root = Path(artifact_root)
        self._session_factory = session_factory or get_session_factory

    def _write_immutable(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.parent / f".tmp-{uuid.uuid4().hex}"
        temp.write_bytes(data)
        os.replace(temp, path)

    async def persist_tool_result(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None,
        tool_call_id: str,
        tool_name: str,
        result_text: str,
        user_id: uuid.UUID,
        preview_limit: int = PREVIEW_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        session_factory = self._session_factory
        if callable(session_factory) and not hasattr(session_factory, "__enter__"):
            factory = session_factory
        else:
            factory = session_factory  # type: ignore[assignment]

        async def run() -> dict[str, Any]:
            async with factory()() as session:  # type: ignore[operator]
                return await self.persist_tool_result_with_session(
                    session,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    result_text=result_text,
                    user_id=user_id,
                    preview_limit=preview_limit,
                )

        return await run()

    async def persist_tool_result_with_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None,
        tool_call_id: str,
        tool_name: str,
        result_text: str,
        user_id: uuid.UUID,
        preview_limit: int = PREVIEW_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        if run_id is not None and task_id is not None:
            raise ValueError("run_id/task_id are mutually exclusive (XOR)")
        data = result_text.encode("utf-8")
        artifact_id = uuid.uuid4()
        key = _storage_key(tenant_id, run_id, artifact_id)
        path = self._artifact_root / key
        self._write_immutable(path, data)
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        preview = result_text[:preview_limit]
        try:
            row = Artifact(
                id=artifact_id,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                conversation_id=conversation_id,
                artifact_type="TOOL_RESULT",
                storage_key=key,
                media_type="text/plain",
                size=len(data),
                checksum=checksum,
                preview_text=preview,
                metadata_json={"tool_call_id": tool_call_id, "tool_name": tool_name},
            )
            session.add(row)
            await session.commit()
        except BaseException:
            # DB 失败：清理本次写入的文件（不留孤儿）
            temp_target = self._artifact_root / key
            try:
                temp_target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return {
            "artifact_id": str(artifact_id),
            "storage_key": key,
            "checksum": checksum,
            "size": len(data),
            "preview": preview,
        }
