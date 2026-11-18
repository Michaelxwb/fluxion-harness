"""平台直出的收尾事实与确定性渲染（design §4.4-4.6）。"""

from .facts import (
    SCHEMA_VERSION,
    BusinessFailureFacts,
    CompletionFacts,
    EvidenceStatus,
    FailureSummary,
    FinalizationFacts,
    TerminationFacts,
    ToolCallRecord,
    ToolOutcomeCounts,
    UsageFacts,
    build_finalization_facts,
    tool_call_record,
)
from .render import render_cancelled, render_finalization

__all__ = [
    "SCHEMA_VERSION",
    "BusinessFailureFacts",
    "CompletionFacts",
    "EvidenceStatus",
    "FailureSummary",
    "FinalizationFacts",
    "TerminationFacts",
    "ToolCallRecord",
    "ToolOutcomeCounts",
    "UsageFacts",
    "build_finalization_facts",
    "render_cancelled",
    "render_finalization",
    "tool_call_record",
]
