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
from dataclasses import dataclass
from pathlib import Path

from muad_agent_core.context.compactor import (
    LayerOutcome,
    compact_history,
    history_bytes,
    split_summary_scopes,
)
from muad_agent_core.context.summary import SummaryFields, summary_message, try_parse_summary
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
)
from muad_contracts.platform_settings import CompactionSettings
from sqlalchemy import func, select

from ..infrastructure.db import SessionFactoryProvider, get_session_factory
from ..infrastructure.models.runtime import Conversation
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

logger = logging.getLogger(__name__)

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
    #: 摘要生效后真正要发出去的请求：受保护前缀 + 摘要 + 当前回合。
    outbound: tuple[ModelMessage, ...]
    layer: LayerOutcome


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

    async def compact(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        try:
            return await self._compact(messages)
        except Exception as exc:  # noqa: BLE001 —— RULE-04：压缩失败必须退化，不得让 Run 失败
            # 失败不可归因到某一层（异常可能出在任何一步），故 `layer="*"`；退化**不等于悄悄吞掉**。
            record_outcome(CONTEXT_COMPACTION_METRIC, "FAILED", {"layer": "*"})
            logger.warning(
                "context_compaction_failed",
                extra={"run_id": str(self._run_id), "error": str(exc)},
            )
            return tuple(messages)

    async def _compact(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        settings = self._settings
        plain, layers = compact_history(
            messages,
            snip_settings=settings.snip,
            micro_settings=settings.micro,
            history_budget_messages=settings.history_budget_messages,
        )
        pending = await self._maybe_summarize(plain)
        outbound = plain
        if pending is not None:
            layers = (*layers, pending.layer)
            outbound = pending.outbound
        fired = [layer for layer in layers if layer.fired]
        if not fired:
            return outbound
        persisted = await self._persist(fired, pending)
        if pending is not None and all(layer.layer != "summary" for layer in persisted):
            # 逐字存档写不成 ⇒ 这次摘要作废（RULE-05），回到前三层的产物
            outbound = plain
        # 记录点在**回合/请求的收口处**，不逐条进热点路径，也不做任何 IO；且只在**真的落库之后**
        # 才记，免得审计写失败时指标宣称"压缩发生了"
        for layer in persisted:
            record_outcome(CONTEXT_COMPACTION_METRIC, "FIRED", {"layer": layer.layer})
            record_counter(
                CONTEXT_COMPACTION_BYTES_SAVED_METRIC,
                float(layer.bytes_saved),
                {"layer": layer.layer},
            )
        return outbound

    async def _maybe_summarize(
        self, messages: tuple[ModelMessage, ...]
    ) -> _PendingSummary | None:
        """判定 + 调模型，**不碰库**：落库与其余层一个事务提交（见 `_persist`）。

        请求里换出去的是"更早的历史"，留下的必须是**受保护前缀 + 新摘要 + 当前回合**：整段换成
        一条摘要会把系统提示（等于 agent 失忆，`harness-arch` 的受保护前缀）与当前用户消息（含
        内联图片）一起丢掉（2026-10-06 review）。喂给摘要模型的仍是**全量**——摘要要覆盖到
        `covers_up_to_seq` 为止的一切，少喂一段就等于那段历史没人记得。
        """
        settings = self._settings.summary
        if not settings.enabled or self._summary_runner is None:
            return None
        scopes = split_summary_scopes(messages)
        if not scopes.older:
            # 没有"更早的历史"可压（整段都是受保护前缀与当前回合）：换不出任何东西，不触发
            return None
        before = history_bytes(messages)
        if before <= settings.threshold_bytes:
            return None
        fields = await self._summary_runner(messages)
        if fields is None:
            # 模型没按五字段给（RULE-03）：本次摘要作废，历史原样
            record_outcome(CONTEXT_SUMMARY_METRIC, "REJECTED")
            return None
        outbound = (*scopes.prefix, summary_message(fields), *scopes.current_turn)
        return _PendingSummary(
            fields=fields,
            summarized=tuple(messages),
            outbound=outbound,
            layer=LayerOutcome("summary", True, 0, before, history_bytes(outbound), outbound),
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
                    covered = await session.scalar(
                        select(func.coalesce(func.max(Conversation.last_seq), 0)).where(
                            Conversation.id == self._conversation_id
                        )
                    )
                    summary_event_seq = await record_summary(
                        EventWriter(session),
                        tenant_id=self._tenant_id,
                        conversation_id=self._conversation_id,
                        run_id=self._run_id,
                        covers_up_to_seq=int(covered or 0),
                        summary=pending.fields.as_payload(),
                        transcript_artifact_id=transcript["artifact_id"],
                        bytes_before=pending.layer.bytes_before,
                        bytes_after=pending.layer.bytes_after,
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


__all__ = ["RuntimeContextCompactor", "SummaryRunner", "make_summary_runner"]
