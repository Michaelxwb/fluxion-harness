"""工具大结果的整轮外置（`ToolResultRoundPort` 的 Worker 实现；design ADR-04 / §3.5.6）。

- **判定单元是回合**：单条超阈值必落盘；其余合计仍超轮预算时按字节从大到小补落，一进预算即停。
- **整轮要么全外置、要么全内联**：任一条写失败就抛错，Kernel 保留内联结果（外置是优化，
  不得让执行失败）。写失败前已落盘的产物是未被引用的不可变行，不影响正确性。
- 模型可见形态只含引用与头尾预览：正文留在 Task 产物里，需要原文用 `read_attachment` 读取。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from muad_agent_core.tools import ArtifactRef, ToolResult
from muad_contracts.platform_settings import ToolResultSettings

from ...infrastructure.task_artifact_client import TaskArtifactClient

ARTIFACT_FILENAME = "tool-result-{call_id}.txt"
ARTIFACT_MEDIA_TYPE = "text/plain"


def preview_head_tail(text: str, *, head_bytes: int, tail_bytes: int) -> str:
    """头尾预览：按 UTF-8 字节截断，截断处不切断多字节字符。"""
    data = text.encode("utf-8")
    if head_bytes < 0 or tail_bytes < 0:
        raise ValueError("preview head/tail bytes must be >= 0")
    if len(data) <= head_bytes + tail_bytes:
        return text
    head = data[:head_bytes].decode("utf-8", errors="ignore")
    tail = data[len(data) - tail_bytes :].decode("utf-8", errors="ignore") if tail_bytes else ""
    omitted = len(data) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n…（中间 {omitted} 字节已省略）…\n{tail}"


def select_round_persists(
    candidates: Sequence[tuple[str, int]],
    *,
    persist_threshold_bytes: int,
    round_budget_bytes: int,
) -> tuple[str, ...]:
    """整轮批次选取（与冻结 `ToolResultSettings` 同一口径，纯逻辑）。"""
    over = [item for item in candidates if item[1] > persist_threshold_bytes]
    rest = [item for item in candidates if item[1] <= persist_threshold_bytes]
    remaining = sum(size for _, size in rest)
    selected = [call_id for call_id, _ in over]
    for call_id, size in sorted(rest, key=lambda item: (-item[1], item[0])):
        if remaining <= round_budget_bytes:
            break
        selected.append(call_id)
        remaining -= size
    return tuple(selected)


def reference_payload(
    *, artifact_id: UUID, size: int, checksum: str, preview: str
) -> str:
    """外置结果的模型可见形态（与读取侧 `artifact.artifact_id` 的约定一致）。"""
    return json.dumps(
        {
            "artifact": {
                "artifact_id": str(artifact_id),
                "size": size,
                "checksum": checksum,
                "preview": preview,
            }
        },
        ensure_ascii=False,
    )


class TaskArtifactRoundWriter:
    """把本回合的大结果写成 Task 产物，并把消息替换成引用+预览。"""

    def __init__(
        self,
        client: TaskArtifactClient,
        settings: ToolResultSettings,
        *,
        tenant_id: str,
        execution_id: UUID,
    ) -> None:
        self._client = client
        self._settings = settings
        self._tenant_id = tenant_id
        self._execution_id = execution_id

    async def finish_round(self, results: Sequence[ToolResult]) -> Sequence[ToolResult]:
        candidates = [
            (result.call_id or "unknown", len(result.content.encode("utf-8")))
            for result in results
            if result.externalizable
        ]
        selected = set(
            select_round_persists(
                candidates,
                persist_threshold_bytes=self._settings.persist_threshold_bytes,
                round_budget_bytes=self._settings.round_budget_bytes,
            )
        )
        if not selected:
            return results
        replaced: list[ToolResult] = []
        for result in results:
            call_id = result.call_id or "unknown"
            if not result.externalizable or call_id not in selected:
                replaced.append(result)
                continue
            replaced.append(await self._externalize(result, call_id))
        return tuple(replaced)

    async def _externalize(self, result: ToolResult, call_id: str) -> ToolResult:
        text = result.content
        data = text.encode("utf-8")
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        response = await self._client.write(
            tenant_id=self._tenant_id,
            execution_id=self._execution_id,
            filename=ARTIFACT_FILENAME.format(call_id=_safe_call_id(call_id)),
            media_type=ARTIFACT_MEDIA_TYPE,
            content=data,
            checksum=checksum,
            purpose="TOOL_RESULT",
        )
        preview = preview_head_tail(
            text,
            head_bytes=self._settings.preview_head_bytes,
            tail_bytes=self._settings.preview_tail_bytes,
        )
        return replace(
            result,
            content=reference_payload(
                artifact_id=response.artifact_id,
                size=len(data),
                checksum=response.checksum,
                preview=preview,
            ),
            artifacts=(
                *result.artifacts,
                ArtifactRef(
                    artifact_id=str(response.artifact_id),
                    media_type=ARTIFACT_MEDIA_TYPE,
                    filename=ARTIFACT_FILENAME.format(call_id=_safe_call_id(call_id)),
                    checksum=response.checksum,
                    size=len(data),
                ),
            ),
        )


def _safe_call_id(call_id: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in call_id)
    return cleaned[:64] or "call"


__all__ = [
    "TaskArtifactRoundWriter",
    "preview_head_tail",
    "reference_payload",
    "select_round_persists",
]
