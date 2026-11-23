"""Agentic 执行的上下文压缩（`ContextCompactor` 的 Worker 实现）。

四层与 Runtime **同一套执行顺序**（`agent-core` 的 `compact_plan_layered` + 摘要 schema）：

1. 工具结果整轮外置（`agentic/artifacts.py`，发生在回合末，不在本模块）；
2. micro：旧的外置结果降级成引用占位；
3. snip：按消息组省掉中段历史（当前回合永远保留）；
4. 条数兜底；摘要层在阈值之上才跑，且**先存档再放行**——存档（Task 产物）写不成就作废这次摘要，
   对原始 older 走 micro/snip/trim 的可见兜底。

降级口径（RULE-04）：任一层失败 ⇒ 本次返回原历史（或前三层的产物），并发 warning + 指标
`context_compaction_total{layer,status}`；绝不因为压缩失败让执行失败。摘要失败只作废摘要层。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from muad_agent_core.context.compactor import LayerOutcome
from muad_agent_core.context.layers import SummaryAttempt, compact_plan_layered
from muad_agent_core.context.plan import ContextPlan
from muad_agent_core.context.summary import SummaryFields, try_parse_summary
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
    text_of,
)
from muad_contracts.platform_settings import (
    CompactionSettings,
    MicroSettings,
    SnipSettings,
    SummarySettings,
    ToolResultSettings,
)
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
    """从冻结策略解析压缩设置；非法块整体回落默认值（不阻断执行，但留下可见的降级）。"""
    raw = policy.get("compaction")
    block = raw if isinstance(raw, Mapping) else {}
    try:
        return CompactionSettings(
            snip=_snip(block.get("snip")),
            tool_result=_tool_result(block.get("tool_result")),
            micro=_micro(block.get("micro")),
            summary=_summary(block.get("summary")),
            history_budget_messages=_positive(block.get("history_budget_messages"), 40),
            max_history_tool_rounds=_positive(block.get("max_history_tool_rounds"), 6),
        )
    except (TypeError, ValueError):
        logger.warning("agentic_compaction_settings_invalid")
        record_outcome(CONTEXT_COMPACTION_METRIC, "FAILED", {"layer": "*"})
        return CompactionSettings()


def _section(raw: Any) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


def _snip(raw: Any) -> SnipSettings:
    block, defaults = _section(raw), SnipSettings()
    return SnipSettings(
        enabled=_flag(block.get("enabled"), defaults.enabled),
        max_groups=_positive(block.get("max_groups"), defaults.max_groups),
        keep_head_groups=_nonnegative(block.get("keep_head_groups"), defaults.keep_head_groups),
        keep_tail_groups=_nonnegative(block.get("keep_tail_groups"), defaults.keep_tail_groups),
    )


def _tool_result(raw: Any) -> ToolResultSettings:
    block, defaults = _section(raw), ToolResultSettings()
    return ToolResultSettings(
        persist_threshold_bytes=_positive(
            block.get("persist_threshold_bytes"), defaults.persist_threshold_bytes
        ),
        round_budget_bytes=_positive(block.get("round_budget_bytes"), defaults.round_budget_bytes),
        preview_head_bytes=_nonnegative(block.get("preview_head_bytes"), defaults.preview_head_bytes),
        preview_tail_bytes=_nonnegative(block.get("preview_tail_bytes"), defaults.preview_tail_bytes),
    )


def _micro(raw: Any) -> MicroSettings:
    block, defaults = _section(raw), MicroSettings()
    return MicroSettings(
        enabled=_flag(block.get("enabled"), defaults.enabled),
        keep_recent_tool_groups=_nonnegative(
            block.get("keep_recent_tool_groups"), defaults.keep_recent_tool_groups
        ),
    )


def _summary(raw: Any) -> SummarySettings:
    block, defaults = _section(raw), SummarySettings()
    model_ref = block.get("model_ref")
    return SummarySettings(
        enabled=_flag(block.get("enabled"), defaults.enabled),
        threshold_bytes=_positive(block.get("threshold_bytes"), defaults.threshold_bytes),
        model_ref=model_ref if isinstance(model_ref, str) and model_ref else None,
    )


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _positive(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default


def _nonnegative(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


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

    async def compact_plan(self, plan: ContextPlan) -> ContextPlan:
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
