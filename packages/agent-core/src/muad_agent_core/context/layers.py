"""共享压缩编排：可选摘要 + 唯一请求边界 `fit_history`。

Runtime 与 Worker 只提供摘要、存档和审计端口；执行顺序固定：

1. 历史区包含材料且超预算/有缺口时，才尝试摘要；
2. 摘要材料 = 旧摘要 + 连续材料组；
3. `with_summary` 只移除材料组，保留头部/独立尾部/pinned；
4. 摘要成功或复用缓存后，再执行 `fit_history`；
5. 摘要未启用/失败/持久化失败时，直接执行 `fit_history`，不推进 coverage。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from muad_contracts.platform_settings import CompactionSettings
from muad_logging import get_logger

from ..model.provider import ModelMessage
from .compactor import LayerOutcome, estimate_messages_tokens, fit_history, history_bytes
from .plan import (
    ContextPlan,
    material_groups,
    pinned_event_ids,
    render_context,
    with_summary,
)
from .summary import SummaryFields, is_summary_message, summary_message

logger = get_logger(__name__)

SummaryRunner = Callable[[Sequence[ModelMessage]], Awaitable[SummaryFields | None]]
SummaryPersister = Callable[["SummaryAttempt", Sequence[LayerOutcome]], Awaitable[bool]]
LayerPersister = Callable[[Sequence[LayerOutcome]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SummaryAttempt:
    """一次已经生成、等待宿主持久化的摘要。"""

    fields: SummaryFields
    material: tuple[ModelMessage, ...]
    layer: LayerOutcome
    coverage_seq: int
    protected_event_ids: tuple[str, ...]


@dataclass
class SummaryCache:
    """宿主压缩器持有的摘要去重状态；同一 Run 的后续模型调用复用。"""

    summarized_through_seq: int = 0
    message: ModelMessage | None = None


def summary_material(plan: ContextPlan) -> tuple[ModelMessage, ...]:
    """摘要模型与 transcript 的共同输入：旧摘要 + 连续材料组。"""
    return (
        *(message for message in plan.prefix if is_summary_message(message)),
        *(message for group in material_groups(plan) for message in group),
    )


def _area(plan: ContextPlan) -> tuple[ModelMessage, ...]:
    return (
        *(message for group in plan.older for message in group),
        *(message for group in plan.pinned for message in group),
        *(message for group in plan.working for message in group),
    )


def _summary_layer(plan: ContextPlan, outbound: ContextPlan) -> LayerOutcome:
    material = material_groups(plan)
    return LayerOutcome(
        "summary",
        True,
        len(material),
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
    summary_cache: SummaryCache | None = None,
    budget_ratio: float = 1.0,
) -> tuple[ContextPlan, tuple[LayerOutcome, ...]]:
    """按固定顺序执行摘要与请求边界裁剪；任何失败都回落为不裁剪。"""
    budget_tokens = max(1, int(settings.history_budget_tokens * budget_ratio))
    area = _area(plan)
    if estimate_messages_tokens(area) <= budget_tokens and plan.earlier_records == 0:
        return plan, ()

    older_tokens = estimate_messages_tokens(
        tuple(message for group in plan.older for message in group)
    )
    should_summarize = plan.earlier_records != 0 or older_tokens > budget_tokens
    material = summary_material(plan) if settings.summary.enabled and material_groups(plan) else ()

    if should_summarize and material and summarize is not None:
        cache = summary_cache or SummaryCache()
        if (
            cache.message is not None
            and plan.material_end_seq > 0
            and plan.material_end_seq <= cache.summarized_through_seq
        ):
            outbound = with_summary(plan, cache.message)
            fitted, fit_layer = fit_history(outbound, budget_tokens=budget_tokens)
            layers = (fit_layer,) if fit_layer is not None else ()
            await persist_layers(layers)
            return fitted, layers

        try:
            fields = await summarize(material)
        except Exception as exc:  # noqa: BLE001 —— 摘要调用失败只作废摘要层
            logger.warning(
                "context_summary_call_failed", extra={"error_type": type(exc).__name__}
            )
            fields = None
        if fields is not None:
            summary_message_value = summary_message(fields)
            outbound = with_summary(plan, summary_message_value)
            layer = _summary_layer(plan, outbound)
            fitted, fit_layer = fit_history(outbound, budget_tokens=budget_tokens)
            layers = (fit_layer,) if fit_layer is not None else ()
            attempt = SummaryAttempt(
                fields=fields,
                material=material,
                layer=layer,
                coverage_seq=plan.material_end_seq,
                protected_event_ids=pinned_event_ids(plan),
            )
            if await persist_summary(attempt, layers):
                cache.summarized_through_seq = plan.material_end_seq
                cache.message = summary_message_value
                if summary_cache is not None:
                    summary_cache.summarized_through_seq = cache.summarized_through_seq
                    summary_cache.message = cache.message
                return fitted, (layer, *layers)

    fitted, fit_layer = fit_history(plan, budget_tokens=budget_tokens)
    layers = (fit_layer,) if fit_layer is not None else ()
    await persist_layers(layers)
    return fitted, layers


__all__ = [
    "LayerPersister",
    "SummaryAttempt",
    "SummaryCache",
    "SummaryPersister",
    "SummaryRunner",
    "compact_plan_layered",
    "summary_material",
]
