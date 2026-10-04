"""压缩 transcript 的落盘（FEAT-04 / RULE-05 / RULE-06）。

被摘要覆盖掉的**逐字原文**落共享产物存储（与工具结果同一套不可变写原语），类型 `TRANSCRIPT`，
数据库只留相对 `storage_key`。这里**只写不读**：本需求不新增任何对外读取/下载端点，运维按
`artifact_id` 直读共享存储即可（`RULE-06`）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from muad_agent_core.model import ModelMessage, text_of
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.db import SessionFactoryProvider, get_session_factory
from ...infrastructure.models.runtime import Artifact
from .immutable_store import discard_written, write_immutable

TRANSCRIPT_ARTIFACT_TYPE = "TRANSCRIPT"
#: 非 Skill 产物必须用自己的前缀——`skills/` 会被 `cleanup_orphan_files` 当孤儿回收（RULE-skill-001）。
TRANSCRIPT_PREFIX = "transcripts"

logger = logging.getLogger(__name__)


def transcript_storage_key(tenant_id: str, run_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"{TRANSCRIPT_PREFIX}/{tenant_id}/{run_id}/{artifact_id}/history.jsonl"


def _message_line(message: ModelMessage) -> dict[str, Any]:
    """逐字原文的一行。多模态内容只留文本视图（图像块是 base64，塞进 transcript 会让体积失控）。"""
    line: dict[str, Any] = {"role": str(message.role), "content": text_of(message.content)}
    if message.tool_call_id:
        line["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        line["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
            for call in message.tool_calls
        ]
    if message.reasoning_content:
        line["reasoning_content"] = message.reasoning_content
    return line


def render_transcript(messages: Sequence[ModelMessage]) -> bytes:
    """JSONL：一行一条消息，保持原顺序与原文（这就是"逐字"的含义）。"""
    lines = [json.dumps(_message_line(message), ensure_ascii=False) for message in messages]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


class TranscriptWriter:
    def __init__(
        self, artifact_root: Path | str, session_factory: SessionFactoryProvider | None = None
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._session_factory = session_factory or get_session_factory

    async def persist_with_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None,
        messages: Sequence[ModelMessage],
    ) -> dict[str, Any] | None:
        """写 transcript 并落 `runtime.artifact` 行；失败清掉文件后**返回 None**（RULE-04）。

        调用方拿到 None 必须**放弃这次摘要**（RULE-05：摘要与它的 transcript 是一对，缺了
        archive 就等于把原文净丢）。失败在这里显式记日志——"退化成不压缩"不等于"悄悄吞掉"。
        取消类异常（`BaseException` 非 `Exception`）照常上抛，不参与降级。
        """
        data = render_transcript(messages)
        if not data:
            return None
        artifact_id = uuid.uuid4()
        key = transcript_storage_key(tenant_id, run_id, artifact_id)
        try:
            write_immutable(self._artifact_root / key, data)
        except OSError as exc:
            logger.warning(
                "context_transcript_write_failed",
                extra={"run_id": str(run_id), "storage_key": key, "error": str(exc)},
            )
            return None
        try:
            session.add(
                Artifact(
                    id=artifact_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    conversation_id=conversation_id,
                    artifact_type=TRANSCRIPT_ARTIFACT_TYPE,
                    storage_key=key,
                    media_type="application/x-ndjson",
                    size=len(data),
                    checksum="sha256:" + hashlib.sha256(data).hexdigest(),
                    preview_text=None,
                    metadata_json={"message_count": len(messages)},
                )
            )
            await session.commit()
        except Exception as exc:
            discard_written(self._artifact_root, [key])
            logger.warning(
                "context_transcript_row_failed",
                extra={"run_id": str(run_id), "storage_key": key, "error": str(exc)},
            )
            return None
        return {
            "artifact_id": str(artifact_id),
            "storage_key": key,
            "size": len(data),
            "checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
        }


__all__ = [
    "TRANSCRIPT_ARTIFACT_TYPE",
    "TRANSCRIPT_PREFIX",
    "TranscriptWriter",
    "render_transcript",
    "transcript_storage_key",
]
