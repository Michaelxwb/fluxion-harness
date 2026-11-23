"""多层压缩的**共享执行顺序**：Runtime 与 Worker 只提供摘要与存档端口。

顺序是语义的一部分，不能由宿主各自拼：

1. `working` 只做 micro（当前回合正在推理的工具结果只降级为可找回的占位，不 snip/trim）；
2. 摘要材料 = 旧摘要 + **完整原始 older**（不含普通 prefix、pinned、working）；
3. 摘要模型返回五字段；
4. 宿主把 transcript、摘要事件、coverage 与其余层**原子持久化**；成功才替换 older；
5. 未触发、拒绝、异常或持久化失败 ⇒ 对**原始 older** 做 micro → snip → 条数兜底，不推进 coverage。

第 4 步的"成功"以宿主返回 `True` 为准（Runtime 在事务提交后返回，Worker 在存档成功后返回）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace

from muad_contracts.platform_settings import CompactionSettings
from muad_logging import get_logger

from ..model.provider import ModelMessage
from .compactor import (
    LayerOutcome,
    history_bytes,
    micro,
    split_groups,
)
from .compactor import (
    compact_plan as fallback_older_plan,
)
from .plan import ContextPlan, render_context, with_summary
from .summary import SummaryFields, is_summary_message, summary_message

logger = get_logger(__name__)

SummaryRunner = Callable[[Sequence[ModelMessage]], Awaitable[SummaryFields | None]]
SummaryPersister = Callable[["SummaryAttempt", Sequence[LayerOutcome]], Awaitable[bool]]
LayerPersister = Callable[[Sequence[LayerOutcome]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SummaryAttempt:
    """一次已经生成、等待宿主持久化的摘要。"""

    fields: SummaryFields
    #: 逐字存档对象：旧摘要 + 完整原始 older。
    material: tuple[ModelMessage, ...]
    layer: LayerOutcome
    coverage_seq: int
    protected_event_ids: tuple[str, ...]


def summary_material(plan: ContextPlan) -> tuple[ModelMessage, ...]:
    """摘要模型与 transcript 的共同输入：旧摘要 + 完整原始 older。

    普通 prefix（系统提示、memory）不属于被摘要的历史；pinned（当前输入、生效 Skill）与
    working（当前回合）也必须原样留在换出去的请求里，不能又进摘要材料。
    """
    return (
        *(message for message in plan.prefix if is_summary_message(message)),
        *(message for group in plan.older for message in group),
    )


def _prepare(
    plan: ContextPlan, settings: CompactionSettings
) -> tuple[ContextPlan, LayerOutcome, tuple[ModelMessage, ...] | None]:
    working = tuple(message for group in plan.working for message in group)
    working_layer = micro(working, settings.micro)
    prepared = replace(plan, working=split_groups(working_layer.messages))

    material: tuple[ModelMessage, ...] | None = None
    if settings.summary.enabled and prepared.older:
        candidate = summary_material(prepared)
        if history_bytes(candidate) > settings.summary.threshold_bytes:
            material = candidate
    return prepared, working_layer, material


def _summary_layer(plan: ContextPlan, outbound: ContextPlan) -> LayerOutcome:
    return LayerOutcome(
        "summary",
        True,
        len(plan.older),
        history_bytes(render_context(plan)),
        history_bytes(render_context(outbound)),
        render_context(outbound),
    )


async def compact_plan_layered(
    plan: ContextPlan,
    *,
    settings: CompactionSettings,
    summarize: SummaryRunner | None,
    persist_summary: SummaryPersister,
    persist_layers: LayerPersister,
) -> tuple[ContextPlan, tuple[LayerOutcome, ...]]:
    """按固定顺序执行全部层；宿主用端口提供摘要与持久化。"""
    prepared, working_layer, material = _prepare(plan, settings)
    base = (working_layer,)

    if material is not None and summarize is not None:
        try:
            fields = await summarize(material)
        except Exception as exc:  # noqa: BLE001 —— 摘要调用失败只作废摘要层
            logger.warning(
                "context_summary_call_failed", extra={"error_type": type(exc).__name__}
            )
            fields = None
        if fields is not None:
            outbound = with_summary(prepared, summary_message(fields))
            layer = _summary_layer(prepared, outbound)
            attempt = SummaryAttempt(
                fields=fields,
                material=material,
                layer=layer,
                coverage_seq=prepared.coverage_seq,
                protected_event_ids=prepared.protected_event_ids,
            )
            if await persist_summary(attempt, base):
                return outbound, (*base, layer)

    fallback, fallback_layers = fallback_older_plan(
        prepared,
        snip_settings=settings.snip,
        micro_settings=settings.micro,
        history_budget_messages=settings.history_budget_messages,
    )
    layers = (*base, *fallback_layers)
    await persist_layers(layers)
    return fallback, layers


__all__ = [
    "LayerPersister",
    "SummaryAttempt",
    "SummaryPersister",
    "SummaryRunner",
    "compact_plan_layered",
    "summary_material",
]
