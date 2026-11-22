"""请求构建缝的压缩适配器（design §3.2 第二条集成缝）。

`AgentRunner` 在组装 `ModelRequest` 之前调这里；本适配器把四个层的**副作用**落地：
micro/snip/条数兜底是纯函数（agent-core），摘要要调模型、要写 transcript、要落审计事件。

**RULE-04 落在这里**：整段包在 try 里，任何异常都退化到"原样返回输入历史"——压缩是尽力而为，
绝不能让 Run 失败。日志只记层级与字节数，不记内容。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from muad_agent_core.context.compactor import (
    LayerOutcome,
    history_bytes,
    trim_older_history,
)
from muad_agent_core.context.compactor import (
    compact_plan as compact_context_plan,
)
from muad_agent_core.context.plan import ContextPlan, render_context, with_summary
from muad_agent_core.context.summary import SummaryFields, summary_message, try_parse_summary
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


@dataclass(frozen=True, slots=True)
class _PendingSummary:
    """模型已经答了、**还没落库**的一次摘要。

    落库与其余压缩层合并成一个事务（`RuntimeContextCompactor._persist`），所以这里只带"要写的
    事实"：五字段、喂给摘要模型的逐字原文（transcript 的存档对象）、换出去的那条请求。
    """

    fields: SummaryFields
    #: 逐字存档的对象：喂给摘要模型的那一段（= 本次的全部输入）。
    summarized: tuple[ModelMessage, ...]
    layer: LayerOutcome
    coverage_seq: int = 0
    protected_event_ids: tuple[str, ...] = ()


#: 摘要指令：要求**恰好**五字段的 JSON 对象，不多不少。校验仍在 `try_parse_summary` 一侧
#: （提示词是引导，闸门是解析器——模型不听话时以解析器为准）。
SUMMARY_INSTRUCTION = (
    "请把上面的对话压缩成 JSON 对象，**恰好**包含这五个键："
    "user_goal（字符串）、constraints、progress、open_items（字符串数组）、"
    "artifacts（数组，每项恰好含 artifact_id/tool/note 三个字符串键）。"
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
            compacted, layers = compact_context_plan(
                plan,
                snip_settings=self._settings.snip,
                micro_settings=self._settings.micro,
                history_budget_messages=self._settings.history_budget_messages,
                trim_older=False,
            )
            pending = await self._maybe_summarize(compacted)
            if pending is not None:
                layers = (*layers, pending.layer)
            fired = tuple(layer for layer in layers if layer.fired)
            persisted = await self._persist(fired, pending) if fired else ()
            if pending is not None and any(
                layer.layer == "summary" for layer in persisted
            ):
                compacted = with_summary(compacted, summary_message(pending.fields))
            else:
                # Summary unavailable/rejected/failed: trim only now, so its coverage watermark
                # never includes messages that were dropped before reaching the summary model.
                compacted, budget = trim_older_history(
                    compacted, self._settings.history_budget_messages
                )
                if budget is not None:
                    persisted = (*persisted, *(await self._persist((budget,), None)))
            for layer in persisted:
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

    async def _maybe_summarize(self, plan: ContextPlan) -> _PendingSummary | None:
        """只消费 ContextPlan 的 older/coverage/protected；不再按 USER 重新划历史。"""
        settings = self._settings.summary
        if not settings.enabled or self._summary_runner is None or not plan.older:
            return None
        messages = render_context(plan)
        before = history_bytes(messages)
        if before <= settings.threshold_bytes:
            return None
        fields = await self._summary_runner(messages)
        if fields is None:
            record_outcome(CONTEXT_SUMMARY_METRIC, "REJECTED")
            return None
        # 逐字存档的是**喂给摘要模型的全部输入**（含旧摘要），不是只被替换的片段。
        summarized = messages
        outbound = render_context(with_summary(plan, summary_message(fields)))
        return _PendingSummary(
            fields=fields,
            summarized=summarized,
            layer=LayerOutcome(
                "summary", True, len(plan.older), before, history_bytes(outbound), outbound
            ),
            coverage_seq=plan.coverage_seq,
            protected_event_ids=plan.protected_event_ids,
        )

    async def _persist(
        self, layers: Sequence[LayerOutcome], pending: _PendingSummary | None
    ) -> tuple[LayerOutcome, ...]:
        """这一次压缩的全部事实**一个事务**落下：transcript 行 + 摘要事件 + 压缩审计。

        以前摘要先自己 commit、审计随后另开事务 commit：审计写失败时本轮返回原历史，摘要的覆盖
        边界却已经落库（下一轮据此排除原始事件）—— 一次压缩半个生效（2026-10-06 review）。现在
        要么全落、要么全不落：任一环失败 ⇒ 回滚 ⇒ 本轮就是"没压"（RULE-04），按 FAILED 记。

        唯一的分支是逐字存档写不成：那**只作废摘要层**（RULE-05），前三层是纯函数、产物早已算好，
        照常落库。返回**真正落下**的层，调用方据此决定发出去的请求与指标。
        """
        async with self._session_factory()() as session:
            summary_event_seq: int | None = None
            written = tuple(layers)
            if pending is not None:
                # transcript 是摘要的存档：写不成就**放弃这次摘要**（RULE-05——只有摘要没有逐字
                # 原文等于把被覆盖的历史净丢掉）。
                transcript = await TranscriptWriter(
                    self._artifact_root, session_factory=self._session_factory
                ).persist_with_session(
                    session,
                    tenant_id=self._tenant_id,
                    conversation_id=self._conversation_id,
                    run_id=self._run_id,
                    task_id=None,
                    messages=pending.summarized,
                )
                if transcript is None:
                    # 摘要有、逐字原文没有 ⇒ 放弃这次摘要（RULE-05），并把"没做成"记出来
                    await session.rollback()
                    record_outcome(CONTEXT_SUMMARY_METRIC, "FAILED")
                    written = tuple(layer for layer in layers if layer.layer != "summary")
                else:
                    summary_event_seq = await record_summary(
                        EventWriter(session),
                        tenant_id=self._tenant_id,
                        conversation_id=self._conversation_id,
                        run_id=self._run_id,
                        covers_up_to_seq=pending.coverage_seq,
                        summary=pending.fields.as_payload(),
                        transcript_artifact_id=transcript["artifact_id"],
                        bytes_before=pending.layer.bytes_before,
                        bytes_after=pending.layer.bytes_after,
                        protected_event_ids=pending.protected_event_ids,
                        submission_id=self._submission_id,
                    )
                    record_outcome(CONTEXT_SUMMARY_METRIC, "OK")
            if not written:
                return ()
            await record_compaction(
                EventWriter(session),
                tenant_id=self._tenant_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                layers=[layer.as_layer_payload() for layer in written],
                summary_event_seq=summary_event_seq,
                submission_id=self._submission_id,
            )
            await session.commit()
        return written
