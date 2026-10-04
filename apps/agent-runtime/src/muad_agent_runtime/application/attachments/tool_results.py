"""Tool 大结果 Artifact 落盘与引用（不可变 temp+replace；DB 失败清理文件）。"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.db import SessionFactoryProvider, get_session_factory
from ...infrastructure.models.runtime import Artifact
from ...metrics import ARTIFACT_BYTES_METRIC, record_counter
from .immutable_store import discard_written, write_immutable

#: 通用工具结果的外置阈值：超过它就换成 Artifact + 预览（`RULE-skill-001` 的产物侧）。
#: **只有这一处定义**——`executor` 的外置判定与 `archive_tools` 的回执裁剪都用它，
#: 各写一份的话"回执刚好不被外置"这条保证会随两边改动而失效（2026-10-03 review）。
TOOL_RESULT_ARTIFACT_BYTES = 8 * 1024

#: 预览按**头尾各留一段**（design §2.3 FEAT-02）：只留头部会让模型看不到结果的结尾
#: （错误栈末行、汇总行、JSON 的闭合结构都在尾部）。
PREVIEW_HEAD_BYTES = 2000
PREVIEW_TAIL_BYTES = 2000
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
class RoundCandidate:
    """整轮里一个**未被单条阈值命中**的工具结果。"""

    tool_call_id: str
    size_bytes: int


def select_round_persists(
    candidates: Sequence[RoundCandidate],
    *,
    persist_threshold_bytes: int,
    round_budget_bytes: int,
) -> tuple[str, ...]:
    """整轮批次预算的选取（纯逻辑，design §2.3 FEAT-02）。

    两段判定，都不涉及 IO：
    1. 单条 **严格大于** `persist_threshold_bytes` ⇒ 必落盘（沿用既有单项阈值）；
    2. 剩下的合计若仍 **严格大于** `round_budget_bytes`，按字节**从大到小**逐条落盘，
       一进预算就停（先落大的收敛最快）。

    返回需要落盘的 `tool_call_id`（顺序稳定：超阈值的按入参顺序，补落的按「大到小 + id」）。
    """
    over = [item for item in candidates if item.size_bytes > persist_threshold_bytes]
    rest = [item for item in candidates if item.size_bytes <= persist_threshold_bytes]
    remaining = sum(item.size_bytes for item in rest)

    selected = list(over)
    for item in sorted(rest, key=lambda entry: (-entry.size_bytes, entry.tool_call_id)):
        if remaining <= round_budget_bytes:
            break
        selected.append(item)
        remaining -= item.size_bytes
    return tuple(item.tool_call_id for item in selected)


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
        preview_head_bytes: int = PREVIEW_HEAD_BYTES,
        preview_tail_bytes: int = PREVIEW_TAIL_BYTES,
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
        preview_head_bytes: int = PREVIEW_HEAD_BYTES,
        preview_tail_bytes: int = PREVIEW_TAIL_BYTES,
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
        preview_head_bytes: int = PREVIEW_HEAD_BYTES,
        preview_tail_bytes: int = PREVIEW_TAIL_BYTES,
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
        data = result_text.encode("utf-8")
        artifact_id = uuid.uuid4()
        key = _storage_key(tenant_id, run_id, artifact_id)
        path = self._artifact_root / key
        self._write_immutable(path, data)
        written.append(key)
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        preview = preview_head_tail(
            result_text, head_bytes=preview_head_bytes, tail_bytes=preview_tail_bytes
        )
        try:
            row = Artifact(
                id=artifact_id,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                conversation_id=conversation_id,
                artifact_type=TOOL_RESULT_ARTIFACT_TYPE,
                storage_key=key,
                media_type="text/plain",
                size=len(data),
                checksum=checksum,
                preview_text=preview,
                metadata_json={"tool_call_id": tool_call_id, "tool_name": tool_name},
            )
            session.add(row)
        except BaseException:
            self._discard([key])
            written.remove(key)
            raise
        record_counter(ARTIFACT_BYTES_METRIC, len(data), {"type": TOOL_RESULT_ARTIFACT_TYPE})
        return {
            "artifact_id": str(artifact_id),
            "storage_key": key,
            "checksum": checksum,
            "size": len(data),
            "preview": preview,
        }
