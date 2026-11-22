"""请求构建缝的压缩适配器（design §3.2 第二条集成缝）。

`AgentRunner` 在组装 `ModelRequest` 之前调这里；本适配器把四个层的**副作用**落地：
micro/snip/条数兜底是纯函数（agent-core），摘要要调模型、要写 transcript、要落审计事件。

**RULE-04 落在这里**：整段包在 try 里，任何异常都退化到"原样返回输入历史"——压缩是尽力而为，
绝不能让 Run 失败。日志只记层级与字节数，不记内容。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from muad_agent_core.context.compactor import LayerOutcome
from muad_agent_core.context.layers import SummaryAttempt, compact_plan_layered
from muad_agent_core.context.plan import ContextPlan
from muad_agent_core.context.summary import SummaryFields, try_parse_summary
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
)
from muad_contracts.platform_settings import CompactionSettings
from muad_logging import get_logger

from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from ..metrics import (
    CONTEXT_COMPACTION_BYTES_SAVED_METRIC,
    CONTEXT_COMPACTION_METRIC,
    CONTEXT_SUMMARY_METRIC,
    CONTEXT_SUMMARY_TOKENS_METRIC,
    record_counter,
    record_outcome,
)
from .attachments.transcripts import TranscriptWriter
from .context_events import record_compaction, record_summary
from .run_events import EventWriter

logger = get_logger(__name__)

#: 摘要模型调用：给定历史，返回五字段（失败/形状不符返回 None）。由装配方注入。
SummaryRunner = Callable[[Sequence[ModelMessage]], Awaitable[SummaryFields | None]]


#: 摘要指令：要求**恰好**五字段的 JSON 对象，不多不少。校验仍在 `try_parse_summary` 一侧
#: （提示词是引导，闸门是解析器——模型不听话时以解析器为准）。
SUMMARY_INSTRUCTION = (
    "请把上面的对话压缩成 JSON 对象，**恰好**包含这五个键："
    "user_goal（字符串）；constraints（字符串数组）；progress（字符串数组）；"
    "open_items（字符串数组）；artifacts（数组，每项恰好含 artifact_id/tool/note 三个字符串键）。"
    "只输出这个 JSON，不要任何解释、不要调用工具。"
)


def make_summary_runner(
    *, provider: ModelProvider, model_id: str, max_tokens: int | None = None
) -> SummaryRunner:
    """用既有 provider 组一个摘要器；模型返回什么形状由 `try_parse_summary` 把关。

    `max_tokens` 是**冻结摘要模型定义**里的输出预算：池化链路按"保守输入估算 + 本次实际发送的
    最大输出预算"预占（design §3.7），所以请求里必须真的发送它——预占不能只读一个没发出去的
    同义字段。旧行（本需求上线前的快照）没有该叶时保持原样发送（None = 不带上限）。
    """

    async def runner(messages: Sequence[ModelMessage]) -> SummaryFields | None:
        response = await provider.complete(
            ModelRequest(
                model_id=model_id,
                messages=(
                    *messages,
                    ModelMessage(role=ModelRole.USER, content=SUMMARY_INSTRUCTION),
                ),
                temperature=0.0,
                max_tokens=max_tokens,
            )
        )
        # token 用量只有发起调用的一侧知道，且**无论采不采用都已经花掉了**，所以记在这里；
        # "采不采用"由压缩器记在 `context_summary_total{outcome}` 上。
        record_counter(
            CONTEXT_SUMMARY_TOKENS_METRIC,
            float((response.input_tokens or 0) + (response.output_tokens or 0)),
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

    async def compact_plan(self, plan: ContextPlan) -> ContextPlan:
        try:
            async def summarize(material: Sequence[ModelMessage]) -> SummaryFields | None:
                assert self._summary_runner is not None
                try:
                    fields = await self._summary_runner(material)
                except Exception as exc:  # noqa: BLE001 —— 摘要失败只作废摘要层
                    record_outcome(CONTEXT_SUMMARY_METRIC, "FAILED")
                    record_outcome(
                        CONTEXT_COMPACTION_METRIC, "FAILED", {"layer": "summary"}
                    )
                    logger.warning(
                        "context_summary_call_failed",
                        extra={"run_id": str(self._run_id), "error_type": type(exc).__name__},
                    )
                    return None
                if fields is None:
                    record_outcome(CONTEXT_SUMMARY_METRIC, "REJECTED")
                return fields

            compacted, layers = await compact_plan_layered(
                plan,
                settings=self._settings,
                summarize=summarize if self._summary_runner is not None else None,
                persist_summary=self._persist_summary,
                persist_layers=self._persist_layers,
            )
            for layer in layers:
                if layer.fired:
                    record_outcome(CONTEXT_COMPACTION_METRIC, "FIRED", {"layer": layer.layer})
                    record_counter(
                        CONTEXT_COMPACTION_BYTES_SAVED_METRIC,
                        float(layer.bytes_saved),
                        {"layer": layer.layer},
                    )
            return compacted
        except Exception as exc:  # noqa: BLE001 —— RULE-04：压缩失败必须退化到原计划
            record_outcome(CONTEXT_COMPACTION_METRIC, "FAILED", {"layer": "*"})
            logger.warning(
                "context_compaction_failed",
                extra={"run_id": str(self._run_id), "error": str(exc)},
            )
            return plan

    async def _persist_summary(
        self, attempt: SummaryAttempt, layers: Sequence[LayerOutcome]
    ) -> bool:
        """摘要成功以**一个事务全部落下**为准：transcript + 摘要事件 + 压缩审计。

        任一步失败都回滚并返回 False；调用方据此对原始 older 走 fallback，coverage 不推进。
        """
        async with self._session_factory()() as session:
            transcript = await TranscriptWriter(
                self._artifact_root, session_factory=self._session_factory
            ).persist_with_session(
                session,
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                task_id=None,
                messages=attempt.material,
            )
            if transcript is None:
                # 摘要有、逐字原文没有 ⇒ 放弃这次摘要（RULE-05）。
                await session.rollback()
                record_outcome(CONTEXT_SUMMARY_METRIC, "FAILED")
                return False
            summary_event_seq = await record_summary(
                EventWriter(session),
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                covers_up_to_seq=attempt.coverage_seq,
                summary=attempt.fields.as_payload(),
                transcript_artifact_id=transcript["artifact_id"],
                bytes_before=attempt.layer.bytes_before,
                bytes_after=attempt.layer.bytes_after,
                protected_event_ids=attempt.protected_event_ids,
                submission_id=self._submission_id,
            )
            written = (*layers, attempt.layer)
            await record_compaction(
                EventWriter(session),
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                layers=[layer.as_layer_payload() for layer in written if layer.fired],
                summary_event_seq=summary_event_seq,
                submission_id=self._submission_id,
            )
            await session.commit()
        record_outcome(CONTEXT_SUMMARY_METRIC, "OK")
        return True

    async def _persist_layers(self, layers: Sequence[LayerOutcome]) -> None:
        """没有摘要（或摘要被作废）时落压缩审计；失败由外层退化为不压缩。"""
        fired = tuple(layer for layer in layers if layer.fired)
        if not fired:
            return
        async with self._session_factory()() as session:
            await record_compaction(
                EventWriter(session),
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                layers=[layer.as_layer_payload() for layer in fired],
                summary_event_seq=None,
                submission_id=self._submission_id,
            )
            await session.commit()
