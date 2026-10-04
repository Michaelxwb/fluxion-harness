"""请求构建缝的压缩适配器（design §3.2 第二条集成缝）。

`AgentRunner` 在组装 `ModelRequest` 之前调这里；本适配器把四个层的**副作用**落地：
micro/snip/条数兜底是纯函数（agent-core），摘要要调模型、要写 transcript、要落审计事件。

**RULE-04 落在这里**：整段包在 try 里，任何异常都退化到"原样返回输入历史"——压缩是尽力而为，
绝不能让 Run 失败。日志只记层级与字节数，不记内容。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from muad_agent_core.context.compactor import LayerOutcome, compact_history, history_bytes
from muad_agent_core.context.settings import CompactionSettings
from muad_agent_core.context.summary import SummaryFields, summary_message, try_parse_summary
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
    text_of,
)
from sqlalchemy import func, select

from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from ..infrastructure.models.runtime import Conversation
from .attachments.transcripts import TranscriptWriter
from .context_events import record_compaction, record_summary
from .run_events import EventWriter

logger = logging.getLogger(__name__)

#: 摘要模型调用：给定历史，返回五字段（失败/形状不符返回 None）。由装配方注入。
SummaryRunner = Callable[[Sequence[ModelMessage]], Awaitable[SummaryFields | None]]

#: 摘要指令：要求**恰好**五字段的 JSON 对象，不多不少。校验仍在 `try_parse_summary` 一侧
#: （提示词是引导，闸门是解析器——模型不听话时以解析器为准）。
SUMMARY_INSTRUCTION = (
    "请把上面的对话压缩成 JSON 对象，**恰好**包含这五个键："
    "user_goal（字符串）、constraints、progress、open_items（字符串数组）、"
    "artifacts（数组，每项恰好含 artifact_id/tool/note 三个字符串键）。"
    "只输出这个 JSON，不要任何解释、不要调用工具。"
)


def make_summary_runner(*, provider: ModelProvider, model_id: str) -> SummaryRunner:
    """用既有 provider 组一个摘要器；模型返回什么形状由 `try_parse_summary` 把关。"""

    async def runner(messages: Sequence[ModelMessage]) -> SummaryFields | None:
        response = await provider.complete(
            ModelRequest(
                model_id=model_id,
                messages=(
                    *messages,
                    ModelMessage(role=ModelRole.USER, content=SUMMARY_INSTRUCTION),
                ),
                temperature=0.0,
            )
        )
        fields, _ = try_parse_summary(
            response.content,
            finish_reason=response.finish_reason,
            tool_calls=response.tool_calls,
        )
        return fields

    return runner


class RuntimeContextCompactor:
    def __init__(
        self,
        *,
        settings: CompactionSettings,
        tenant_id: str,
        run_id: uuid.UUID,
        conversation_id: uuid.UUID,
        artifact_root: Path | str,
        summary_runner: SummaryRunner | None = None,
        session_factory: SessionFactoryProvider | None = None,
        submission_id: uuid.UUID | None = None,
    ) -> None:
        self._settings = settings
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._conversation_id = conversation_id
        self._artifact_root = Path(artifact_root)
        self._summary_runner = summary_runner
        self._session_factory = session_factory or get_session_factory
        self._submission_id = submission_id
        self._summary_event_seq: int | None = None

    async def compact(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        try:
            return await self._compact(messages)
        except Exception as exc:  # noqa: BLE001 —— RULE-04：压缩失败必须退化，不得让 Run 失败
            logger.warning(
                "context_compaction_failed",
                extra={"run_id": str(self._run_id), "error": str(exc)},
            )
            return tuple(messages)

    async def _compact(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        settings = self._settings
        compacted, layers = compact_history(
            messages,
            snip_settings=settings.snip,
            micro_settings=settings.micro,
            history_budget_messages=settings.history_budget_messages,
        )
        summary_layer = await self._maybe_summarize(compacted)
        if summary_layer is not None:
            compacted, layers = summary_layer.messages, (*layers, summary_layer)
        fired = [layer for layer in layers if layer.fired]
        if fired:
            await self._record_layers(fired, compacted)
        return compacted

    async def _maybe_summarize(
        self, compacted: tuple[ModelMessage, ...]
    ) -> LayerOutcome | None:
        settings = self._settings.summary
        if not settings.enabled or self._summary_runner is None:
            return None
        before = history_bytes(compacted)
        if before <= settings.threshold_bytes:
            return None
        fields = await self._summary_runner(compacted)
        if fields is None:
            return None
        # transcript 是摘要的存档：写不成就**放弃这次摘要**（RULE-05——只有摘要没有逐字原文
        # 等于把被覆盖的历史净丢掉）。
        async with self._session_factory()() as session:
            transcript = await TranscriptWriter(
                self._artifact_root, session_factory=self._session_factory
            ).persist_with_session(
                session,
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                task_id=None,
                messages=compacted,
            )
            if transcript is None:
                return None
            covered = await session.scalar(
                select(func.coalesce(func.max(Conversation.last_seq), 0)).where(
                    Conversation.id == self._conversation_id
                )
            )
            summary_seq = await record_summary(
                EventWriter(session),
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                covers_up_to_seq=int(covered or 0),
                summary=fields.as_payload(),
                transcript_artifact_id=transcript["artifact_id"],
                bytes_before=before,
                bytes_after=len(text_of(summary_message(fields).content).encode("utf-8")),
                submission_id=self._submission_id,
            )
            await session.commit()
        self._summary_event_seq = summary_seq
        outbound = (summary_message(fields),)
        return LayerOutcome(
            "summary",
            True,
            0,
            before,
            history_bytes(outbound),
            outbound,
        )

    async def _record_layers(
        self, layers: Sequence[LayerOutcome], outbound: tuple[ModelMessage, ...]
    ) -> None:
        async with self._session_factory()() as session:
            await record_compaction(
                EventWriter(session),
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                layers=[layer.as_layer_payload() for layer in layers],
                summary_event_seq=self._summary_event_seq,
                submission_id=self._submission_id,
            )
            await session.commit()


__all__ = ["RuntimeContextCompactor", "SummaryRunner", "make_summary_runner"]
