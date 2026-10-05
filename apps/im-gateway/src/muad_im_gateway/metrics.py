"""IM Gateway 指标目录（harness-arch「指标暴露与 label 卫生」）。

与 runtime/worker 的 `metrics.py` 同构：**安装时声明目录**，使 `GET /metrics` 在**没有流量时
也暴露完整目录**（此前本单元只有 `install_metrics(app)`、零声明 ⇒ 目录为空，监控侧看到的是
「服务活着但一个指标都没有」）。

指标名与 label 仍在各自使用处定义（`application/inbound.py`、`api/delivery.py`），这里只做
集中声明；label 只含渠道类型/结局码这类**低基数、非敏感**维度，不含 message_id、用户、UUID。
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI
from muad_api import declare_metric, install_metrics

from .api.delivery import BACKGROUND_DELIVERY_METRIC
from .application.inbound import (
    DEDUPE_HITS_METRIC,
    MESSAGE_FAILURES_METRIC,
    MESSAGES_METRIC,
    RUNTIME_ERRORS_METRIC,
    RUNTIME_REQUEST_LATENCY_METRIC,
    STREAM_FIRST_CHUNK_METRIC,
    STREAM_LATENCY_METRIC,
)

COUNTER: Final = "counter"
GAUGE: Final = "gauge"
#: 取平台设置快照的失败计数在**调用方**侧（Console 端点不可达时 Console 收不到请求）。
PLATFORM_SETTINGS_FETCH_METRIC: Final = "platform_settings_fetch_total"

#: 目录：(指标名, 类型, label 名, help)。help 与使用处 `inc_counter/set_gauge` 的 `help` 一致。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (MESSAGES_METRIC, COUNTER, ("type",), "Inbound IM messages by type"),
    (DEDUPE_HITS_METRIC, COUNTER, (), "Inbound dedupe hits"),
    (MESSAGE_FAILURES_METRIC, COUNTER, ("reason",), "IM message failures"),
    (RUNTIME_ERRORS_METRIC, COUNTER, ("code",), "Runtime error codes observed"),
    (STREAM_FIRST_CHUNK_METRIC, GAUGE, (), "Time to first streamed chunk (ms)"),
    (STREAM_LATENCY_METRIC, GAUGE, (), "Whole-stream latency until finalize (ms)"),
    (
        RUNTIME_REQUEST_LATENCY_METRIC,
        GAUGE,
        (),
        "Runtime run request latency until first SSE event (ms)",
    ),
    (BACKGROUND_DELIVERY_METRIC, COUNTER, ("status",), "Background deliveries by status"),
    (
        PLATFORM_SETTINGS_FETCH_METRIC,
        COUNTER,
        ("caller", "result"),
        "Platform settings snapshot fetches by caller and result (ok/failed)",
    ),
)


def install_gateway_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
