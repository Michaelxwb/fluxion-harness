"""Runtime 指标目录（design 11-audit-observability §3.5 / docs/09 §6.2）。

只导出 api-kit 的进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）；
指标不进入业务 Console 渲染，也不作为业务审计事实源（权威数据在 PostgreSQL 的审计表）。
label 只含 Agent/Skill 键、工具名、provider/model 标识与状态码；不得包含 Secret、凭据、
消息正文或资源 ID（UUID）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics

AGENT_RUNS_METRIC: Final = "agent_runs_total"
MODEL_INVOCATIONS_METRIC: Final = "model_invocations_total"
TOOL_CALLS_METRIC: Final = "tool_calls_total"
SKILL_LOAD_METRIC: Final = "skill_load_total"
# 被目录上限丢掉的技能条数记在 **amount**（同 memory 注入/摘要 token 的口径：条数进 label 会
# 裂出无界时间序列）。它不是"某条技能失败"，而是"这一轮有多少技能没能进提示词"。
SKILL_CATALOG_TRUNCATED_METRIC: Final = "skill_catalog_truncated_total"
EGRESS_CALLS_METRIC: Final = "egress_calls_total"
ARTIFACT_BYTES_METRIC: Final = "artifact_bytes_total"
RUN_RECLAIM_METRIC: Final = "run_reclaim_total"
MEMORY_WRITE_METRIC: Final = "memory_write_total"
MEMORY_INJECT_METRIC: Final = "memory_inject_total"
MEMORY_RECALL_METRIC: Final = "memory_recall_total"
MEMORY_RECALL_BYTES_METRIC: Final = "memory_recall_bytes_total"
CONTEXT_COMPACTION_METRIC: Final = "context_compaction_total"
CONTEXT_COMPACTION_BYTES_SAVED_METRIC: Final = "context_compaction_bytes_saved_total"
CONTEXT_SUMMARY_METRIC: Final = "context_summary_total"
CONTEXT_SUMMARY_TOKENS_METRIC: Final = "context_summary_tokens_total"
# 取平台设置快照的失败计数在**调用方**侧（Console 端点不可达时 Console 收不到请求）。
PLATFORM_SETTINGS_FETCH_METRIC: Final = "platform_settings_fetch_total"

COUNTER: Final = "counter"
GAUGE: Final = "gauge"

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (AGENT_RUNS_METRIC, COUNTER, ("agent", "status"), "Agent runs by agent key and outcome"),
    ("tool_control_dispatch_total", COUNTER, ("status",), "Durable tool control delivery outcomes"),
    (
        MODEL_INVOCATIONS_METRIC,
        COUNTER,
        ("provider", "model", "status"),
        "Model invocations by provider, model and status",
    ),
    (TOOL_CALLS_METRIC, COUNTER, ("kind", "tool", "status"), "Tool calls by kind, tool and status"),
    (SKILL_LOAD_METRIC, COUNTER, ("skill", "status"), "Skill package loads by skill key and status"),
    (
        SKILL_CATALOG_TRUNCATED_METRIC,
        COUNTER,
        (),
        "Skills dropped from the prompt catalog by the catalog budget",
    ),
    (EGRESS_CALLS_METRIC, COUNTER, ("platform", "status"), "Egress calls by platform and status"),
    (ARTIFACT_BYTES_METRIC, COUNTER, ("type",), "Artifact bytes written by artifact type"),
    (RUN_RECLAIM_METRIC, COUNTER, (), "Abandoned runs reaped back by the run reaper"),
    (
        MEMORY_WRITE_METRIC,
        COUNTER,
        ("source_type", "status"),
        "Long-term memory writes by source type and outcome",
    ),
    # 注入/检索的"条数与字节"记在 **amount** 而不是 label：label 里放计数值会按每次取值
    # 裂出新的时间序列（无界基数），与本模块 docstring 的 label 卫生口径冲突。
    (MEMORY_INJECT_METRIC, COUNTER, (), "Memory entries injected into model requests"),
    (MEMORY_RECALL_METRIC, COUNTER, ("status",), "Recall tool calls by outcome"),
    (MEMORY_RECALL_BYTES_METRIC, COUNTER, (), "Bytes returned by the recall tool"),
    # 压缩四级计数器（design §3.5 可观测性）：label 只有层名与结果，不带资源 ID。
    (
        CONTEXT_COMPACTION_METRIC,
        COUNTER,
        ("layer", "status"),
        "Context compaction layers by layer and status",
    ),
    (
        CONTEXT_COMPACTION_BYTES_SAVED_METRIC,
        COUNTER,
        ("layer",),
        "Bytes saved by context compaction by layer",
    ),
    (CONTEXT_SUMMARY_METRIC, COUNTER, ("status",), "Summary layer runs by status"),
    # token 用量记在 **amount**，不进 label（label 里放计数值会裂出无界时间序列）。
    (CONTEXT_SUMMARY_TOKENS_METRIC, COUNTER, (), "Tokens spent on summary model calls"),
    (
        PLATFORM_SETTINGS_FETCH_METRIC,
        COUNTER,
        ("caller", "result"),
        "Platform settings snapshot fetches by caller and result (ok/failed)",
    ),
)


def record_counter(name: str, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
    """累加一次计数（非结局语义的指标，如 artifact 字节数）。"""
    inc_counter(name, amount, labels)


def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次结果；status 取审计表口径（`OK`/`FAILED`）或 catalog 错误码。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


def install_runtime_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
