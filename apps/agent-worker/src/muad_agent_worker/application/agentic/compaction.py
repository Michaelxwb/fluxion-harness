"""Agentic 执行的上下文压缩（`ContextCompactor` 的 Worker 实现）。

与 Runtime 共用 `agent-core` 的摘要 + `fit_history` 编排：工具结果整轮外置发生在回合末
（`agentic/artifacts.py`），请求边界只在 `fit_history` 做一次保头保尾裁剪。摘要先存档再放行；
存档失败只作废摘要层，回落到预算裁剪，不让执行失败。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from muad_agent_core.context.compactor import LayerOutcome
from muad_agent_core.context.layers import SummaryAttempt, SummaryCache, compact_plan_layered
from muad_agent_core.context.plan import ContextPlan
from muad_agent_core.context.summary import SummaryFields, try_parse_summary
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
    text_of,
)
from muad_contracts.platform_settings import CompactionSettings, parse_frozen_compaction_settings
from muad_logging import get_logger

from ...infrastructure.task_artifact_client import TaskArtifactClient, TaskArtifactError
from ...metrics import CONTEXT_COMPACTION_METRIC, record_outcome

logger = get_logger("muad_agent_worker.application.agentic.compaction")

SUMMARY_ARTIFACT_FILENAME = "context-summary-{index}.txt"
SUMMARY_ARTIFACT_MEDIA_TYPE = "text/plain"
#: 摘要指令：五字段、只输出 JSON、不得带工具调用（与 `parse_summary` 的校验一一对应）。
SUMMARY_INSTRUCTION = (
    "把下面的对话压缩成一份权威摘要，严格只输出一个 JSON 对象，字段恰好是："
    "user_goal(字符串)、constraints(字符串数组)、progress(字符串数组)、"
    "open_items(字符串数组)、artifacts(数组，元素含 artifact_id/tool/note 三个字符串)。"
    "不要输出任何解释、不要调用工具。"
)


def compaction_settings(policy: Mapping[str, Any]) -> CompactionSettings:
    """严格解析已冻结 compaction 块；缺键、未知键、旧键显式失败。"""
    raw = policy.get("compaction")
    if not isinstance(raw, Mapping):
        raise ValueError("worker frozen policy is missing compaction")
    return parse_frozen_compaction_settings(raw)


@dataclass(frozen=True, slots=True)
class SummaryRun:
    """摘要层的一次尝试结果：五字段 + 被它取代的历史（存档用）。"""

    fields: SummaryFields
    summarized: tuple[ModelMessage, ...]


class SummaryGenerator:
    """用**冻结的摘要模型**生成五字段摘要；失败返回 None（不抛）。"""

    def __init__(self, provider: ModelProvider, *, model_name: str, max_tokens: int | None = None) -> None:
        self._provider = provider
        self._model_name = model_name
        self._max_tokens = max_tokens

    async def __call__(self, messages: Sequence[ModelMessage]) -> SummaryFields | None:
        body = "\n".join(
            f"[{message.role}] {text_of(message.content)}" for message in messages
        )
        try:
            response = await self._provider.complete(
                ModelRequest(
                    model_id=self._model_name,
                    messages=(
                        ModelMessage(role=ModelRole.SYSTEM, content=SUMMARY_INSTRUCTION),
                        ModelMessage(role=ModelRole.USER, content=body),
                    ),
                    max_tokens=self._max_tokens,
                )
            )
        except Exception as exc:  # noqa: BLE001 —— 摘要失败只作废这一层
            logger.warning("agentic_summary_call_failed", extra={"error_type": type(exc).__name__})
            return None
        fields, reason = try_parse_summary(
            response.content,
            finish_reason=response.finish_reason,
            tool_calls=response.tool_calls,
        )
        if fields is None:
            logger.warning("agentic_summary_rejected", extra={"reason": reason})
            record_outcome(CONTEXT_COMPACTION_METRIC, "FAILED", {"layer": "summary"})
        return fields


class TaskContextCompactor:
    def __init__(
        self,
        settings: CompactionSettings,
        *,
        tenant_id: str,
        execution_id: UUID,
        summary: SummaryGenerator | None = None,
        artifacts: TaskArtifactClient | None = None,
    ) -> None:
        self._settings = settings
        self._tenant_id = tenant_id
        self._execution_id = execution_id
        self._summary = summary
        self._artifacts = artifacts
        self._summary_index = 0
        self._summary_cache = SummaryCache()

    async def compact_plan(
        self, plan: ContextPlan, *, budget_ratio: float = 1.0
    ) -> ContextPlan:
        try:
            async def summarize(material: Sequence[ModelMessage]) -> SummaryFields | None:
                assert self._summary is not None
                fields = await self._summary(material)
                if fields is None:
                    record_outcome(CONTEXT_COMPACTION_METRIC, "REJECTED", {"layer": "summary"})
                return fields

            async def persist_summary(
                attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]
            ) -> bool:
                return await self._archive(attempt.material)

            async def persist_layers(_layers: Sequence[LayerOutcome]) -> None:
                return None

            compacted, layers = await compact_plan_layered(
                plan,
                settings=self._settings,
                summarize=summarize if self._summary is not None else None,
                persist_summary=persist_summary,
                persist_layers=persist_layers,
                summary_cache=self._summary_cache,
                budget_ratio=budget_ratio,
            )
            for layer in layers:
                if layer.fired:
                    record_outcome(CONTEXT_COMPACTION_METRIC, "FIRED", {"layer": layer.layer})
            return compacted
        except Exception as exc:  # noqa: BLE001 —— 压缩失败保留原计划
            record_outcome(CONTEXT_COMPACTION_METRIC, "FAILED", {"layer": "*"})
            logger.warning(
                "agentic_context_compaction_failed",
                extra={"execution_id": str(self._execution_id), "error_type": type(exc).__name__},
            )
            return plan

    async def _archive(self, summarized: Sequence[ModelMessage]) -> bool:
        """逐字存档被摘要取代的历史；没有产物端口就不放行摘要层。"""
        if self._artifacts is None:
            return False
        self._summary_index += 1
        payload = json.dumps(
            [
                {"role": str(message.role), "content": text_of(message.content)}
                for message in summarized
            ],
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            await self._artifacts.write(
                tenant_id=self._tenant_id,
                execution_id=self._execution_id,
                filename=SUMMARY_ARTIFACT_FILENAME.format(index=self._summary_index),
                media_type=SUMMARY_ARTIFACT_MEDIA_TYPE,
                content=payload,
                checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
            )
        except TaskArtifactError as exc:
            logger.warning(
                "agentic_summary_archive_failed",
                extra={"execution_id": str(self._execution_id), "code": exc.code},
            )
            return False
        return True


__all__ = [
    "SUMMARY_INSTRUCTION",
    "SummaryGenerator",
    "SummaryRun",
    "TaskContextCompactor",
    "compaction_settings",
]
