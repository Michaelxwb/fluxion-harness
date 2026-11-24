"""Tool 大结果 Artifact 落盘与引用（不可变 temp+replace；DB 失败清理文件）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from muad_agent_core.tools.result_budget import RoundCandidate, select_round_persists
from muad_logging import get_logger
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.db import SessionFactoryProvider, get_session_factory
from ...infrastructure.models.runtime import Artifact
from ...metrics import ARTIFACT_BYTES_METRIC, record_counter
from .immutable_store import discard_written, write_immutable

logger = get_logger(__name__)

#: 通用工具结果的外置阈值、预览头尾字节数**只有 schema 一处来源**：
#: `muad_contracts.platform_settings.ToolResultSettings`（本 Run 冻结后注入）。此模块不再
#: 定义第二套默认常量——判定与裁剪都以调用方显式传入的生效值为准（ADR-05）。
TOOL_RESULT_ARTIFACT_TYPE = "TOOL_RESULT"


def _storage_key(tenant_id: str, run_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"tools/{tenant_id}/{run_id}/{artifact_id}/result.bin"


def preview_head_tail(text: str, *, head_bytes: int, tail_bytes: int) -> str:
    """头尾预览：按 **UTF-8 字节**截，截断处不切断多字节字符（用 `errors="ignore"` 丢掉残字节）。"""
    data = text.encode("utf-8")
    if head_bytes < 0 or tail_bytes < 0:
        raise ValueError("preview head/tail bytes must be >= 0")
    if len(data) <= head_bytes + tail_bytes:
        return text
    head = data[:head_bytes].decode("utf-8", errors="ignore")
    tail = data[len(data) - tail_bytes :].decode("utf-8", errors="ignore") if tail_bytes else ""
    omitted = len(data) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n…（中间 {omitted} 字节已省略）…\n{tail}"


@dataclass(frozen=True, slots=True)
class PreparedToolResults:
    rows: tuple[Artifact, ...] = ()
    references: Mapping[str, dict[str, JsonValue]] = field(default_factory=dict)


def reference_payload(reference: Mapping[str, Any]) -> str:
    """外置结果的**模型可见形态**：只给引用与预览，正文留在产物里（要原文用 `read_attachment`）。

    写入侧（`ToolCallRecorder`）与重建侧（`context_builder`）必须用**同一个**序列化：否则
    "重建的那份 == 当时真正发出去的那份"就不成立（design ADR-05）。
    """
    return json.dumps(
        {
            "artifact": {
                "artifact_id": reference["artifact_id"],
                "size": reference["size"],
                "checksum": reference["checksum"],
                "preview": reference["preview"],
            }
        },
        ensure_ascii=False,
    )


class ArtifactResultWriter:
    def __init__(
        self, artifact_root: Path | str, session_factory: SessionFactoryProvider | None = None
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._session_factory = session_factory or get_session_factory

    def _write_immutable(self, path: Path, data: bytes) -> None:
        """不可变写：同 `storage_key` 二次写入必须抛 `FileExistsError`，绝不静默覆盖。

        与 `packages/artifact-store` 的 `NfsArtifactStore.write` 同口径——产物一旦落盘就是
        证据，覆盖写会让"这只 Run 当时看到了什么"永远查不回来。
        """
        write_immutable(path, data)

    def _discard(self, keys: Sequence[str]) -> None:
        """回滚已写文件（整批中途失败时用），不留半批。"""
        discard_written(self._artifact_root, keys)

    async def discard_prepared(self, prepared: PreparedToolResults) -> None:
        await asyncio.to_thread(self._discard, [row.storage_key for row in prepared.rows])

    async def prepare_round(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        results: Sequence[tuple[str, str, str]],
        preview_head_bytes: int,
        preview_tail_bytes: int,
    ) -> PreparedToolResults:
        """Publish immutable files before acquiring any Run/operation business lock."""
        work = asyncio.create_task(asyncio.to_thread(
            self._prepare_files, tenant_id, conversation_id, run_id, results,
            preview_head_bytes, preview_tail_bytes,
        ))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            try:
                prepared = await work
            except Exception as exc:
                logger.warning(
                    "cancelled_artifact_preparation_failed", extra={"error_type": type(exc).__name__}
                )
            else:
                await self.discard_prepared(prepared)
            raise

    def _prepare_files(
        self, tenant: str, conversation: uuid.UUID, run: uuid.UUID,
        results: Sequence[tuple[str, str, str]], head: int, tail: int,
    ) -> PreparedToolResults:
        rows: list[Artifact] = []
        refs: dict[str, dict[str, JsonValue]] = {}
        try:
            for call_id, name, text in results:
                row, ref = self._prepare_one(tenant, conversation, run, None, call_id, name, text, head, tail)
                rows.append(row)
                refs[call_id] = ref
        except BaseException:
            self._discard([row.storage_key for row in rows])
            raise
        return PreparedToolResults(tuple(rows), refs)

    def _prepare_one(
        self,
        tenant: str,
        conversation: uuid.UUID,
        run: uuid.UUID,
        task: uuid.UUID | None,
        call_id: str,
        name: str,
        text: str,
        head: int,
        tail: int,
    ) -> tuple[Artifact, dict[str, JsonValue]]:
        data, artifact_id = text.encode("utf-8"), uuid.uuid4()
        key = _storage_key(tenant, run, artifact_id)
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        preview = preview_head_tail(text, head_bytes=head, tail_bytes=tail)
        row = Artifact(
            id=artifact_id,
            tenant_id=tenant,
            run_id=run,
            task_id=task,
            conversation_id=conversation,
            artifact_type=TOOL_RESULT_ARTIFACT_TYPE,
            storage_key=key,
            media_type="text/plain",
            size=len(data),
            checksum=checksum,
            preview_text=preview,
            metadata_json={"tool_call_id": call_id, "tool_name": name},
        )
        self._write_immutable(self._artifact_root / key, data)
        ref: dict[str, JsonValue] = {
            "artifact_id": str(artifact_id),
            "storage_key": key,
            "checksum": checksum,
            "size": len(data),
            "preview": preview,
        }
        return row, ref

    async def persist_mcp_artifact(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> str:
        """MCP 图片/二进制资源 → 不可变 Artifact，返回可被 read_attachment/view_image 使用的 id。"""
        artifact_id = uuid.uuid4()
        key = _storage_key(tenant_id, run_id, artifact_id)
        checksum = "sha256:" + hashlib.sha256(content).hexdigest()
        row = Artifact(
            id=artifact_id,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            conversation_id=conversation_id,
            artifact_type=TOOL_RESULT_ARTIFACT_TYPE,
            storage_key=key,
            media_type=media_type,
            size=len(content),
            checksum=checksum,
            metadata_json={"filename": filename, "source": "MCP"},
        )
        await asyncio.to_thread(self._write_immutable, self._artifact_root / key, content)
        async with self._session_factory()() as session:
            session.add(row)
            await session.commit()
        record_counter(ARTIFACT_BYTES_METRIC, len(content), {"type": TOOL_RESULT_ARTIFACT_TYPE})
        return str(artifact_id)

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
        preview_head_bytes: int,
        preview_tail_bytes: int,
    ) -> dict[str, Any]:
        factory: SessionFactoryProvider = self._session_factory

        async def run() -> dict[str, Any]:
            async with factory()() as session:
                reference = await self.persist_tool_result_with_session(
                    session,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    result_text=result_text,
                    user_id=user_id,
                    preview_head_bytes=preview_head_bytes,
                    preview_tail_bytes=preview_tail_bytes,
                )
                await session.commit()
                return reference

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
        preview_head_bytes: int,
        preview_tail_bytes: int,
    ) -> dict[str, Any]:
        if run_id is not None and task_id is not None:
            raise ValueError("run_id/task_id are mutually exclusive (XOR)")
        written: list[str] = []
        try:
            reference = await self._persist_one_with_session(
                session,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result_text=result_text,
                preview_head_bytes=preview_head_bytes,
                preview_tail_bytes=preview_tail_bytes,
                written=written,
            )
            await session.commit()
        except BaseException:
            # 行没落成，盘上的文件也不留（提交移到这里之后，这一步由调用方负责）
            self._discard(written)
            raise
        return reference

    async def persist_round_results_with_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None,
        results: Sequence[tuple[str, str, str]],
        preview_head_bytes: int,
        preview_tail_bytes: int,
    ) -> dict[str, dict[str, Any]]:
        """整轮批次落盘：一批一起写、**中途失败回滚本批已写产物**。

        `results` 是 `(tool_call_id, tool_name, result_text)` 三元组序列，只包含
        `select_round_persists` 选中的那些。返回 `tool_call_id -> 引用`。

        **文件与行一起成败**：整批只在最后提交一次事务。此前是逐条提交，中途失败时文件回滚了
        而已经提交的行留在库里指向已删文件 —— "整批回滚"名不副实。
        """
        written: list[str] = []
        references: dict[str, dict[str, Any]] = {}
        try:
            for tool_call_id, tool_name, result_text in results:
                references[tool_call_id] = await self._persist_one_with_session(
                    session,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    result_text=result_text,
                    preview_head_bytes=preview_head_bytes,
                    preview_tail_bytes=preview_tail_bytes,
                    written=written,
                )
            await session.commit()
        except BaseException:
            self._discard(written)
            raise
        return references

    async def _persist_one_with_session(
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
        preview_head_bytes: int,
        preview_tail_bytes: int,
        written: list[str],
    ) -> dict[str, Any]:
        row, reference = await asyncio.to_thread(
            self._prepare_one,
            tenant_id,
            conversation_id,
            run_id,
            task_id,
            tool_call_id,
            tool_name,
            result_text,
            preview_head_bytes,
            preview_tail_bytes,
        )
        written.append(row.storage_key)
        try:
            session.add(row)
        except BaseException:
            self._discard([row.storage_key])
            written.remove(row.storage_key)
            raise
        record_counter(ARTIFACT_BYTES_METRIC, row.size, {"type": TOOL_RESULT_ARTIFACT_TYPE})
        return dict(reference)
