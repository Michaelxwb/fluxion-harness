"""IM Gateway 指标目录（harness-arch「指标暴露与 label 卫生」）。

与 runtime/worker 的 `metrics.py` 同构：**安装时声明目录**，使 `GET /metrics` 在**没有流量时
也暴露完整目录**（此前本单元只有 `install_metrics(app)`、零声明 ⇒ 目录为空，监控侧看到的是
「服务活着但一个指标都没有」）。

指标名与 label 仍在各自使用处定义（`application/inbound.py`、`api/delivery.py`），这里只做
集中声明；label 只含渠道类型/结局码这类**低基数、非敏感**维度，不含 message_id、用户、UUID。

本任务（TASK-098）补公开流（API-07）与出站交付的观测面：

- **连接与重连**：`gateway_sse_subscription_total{status}` 区分新开/断线重连/上限拒绝/游标分型；
- **连接与缓冲水位**：`gateway_sse_ledger{scope}`（活跃订阅数、进程未下发字节）；
- **缓冲回压**：`gateway_sse_closed_total{reason}`（慢订阅/总缓冲/坏载荷/超限帧/上游中断）与
  `gateway_sse_subscribers_rejected_total`（订阅上限显式拒绝）；
- **帧量**：`gateway_sse_frames_total{kind}`（公开事件/checkpoint/心跳/分片/过滤）；
- **池等待**：`gateway_http_pool_wait_total{pool}`（control/stream/upload/egress 真实池名）；
- **交付**：`gateway_channel_delivery_total{status}`（发送/去重/失败，不互相冒充）。

label 取值一律是封闭字面量（本模块的 Literal 别名）或目录错误码，**不含** run/conversation/
message UUID、投递键、正文与凭据。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics, set_gauge

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

# --- 公开流与出站交付（TASK-098） ---------------------------------------------------------
#: 订阅结局：新开/断线重连/上限拒绝/Owner 游标分型，各自是不同样本。
SSE_SUBSCRIPTION_METRIC: Final = "gateway_sse_subscription_total"
#: 进程级账本水位：活跃订阅数与未下发字节（连接与缓冲都可见）。
SSE_LEDGER_METRIC: Final = "gateway_sse_ledger"
#: 显式关闭原因：慢订阅/总缓冲/坏载荷/超限帧/上游中断（每条路径都留痕，不静默丢帧）。
SSE_CLOSED_METRIC: Final = "gateway_sse_closed_total"
#: 订阅者数量上限的显式拒绝（不排队、不静默丢）。
SSE_REJECTED_METRIC: Final = "gateway_sse_subscribers_rejected_total"
#: 下发帧量：公开事件/checkpoint/心跳/分片/过滤行（事件写入量可对账）。
SSE_FRAMES_METRIC: Final = "gateway_sse_frames_total"
#: 出站池等待超时（按真实池名，与模型/上传/控制/业务分池同源）。
HTTP_POOL_WAIT_METRIC: Final = "gateway_http_pool_wait_total"
#: 渠道交付结局：发送/去重/失败不互相冒充（与 Runtime 泵的诚实结局同一口径）。
CHANNEL_DELIVERY_METRIC: Final = "gateway_channel_delivery_total"

#: Gateway 出站池名（与 `infrastructure/http_clients.GATEWAY_HTTP_POOLS` 同源封闭枚举）。
GatewayPool = Literal["control", "stream", "upload", "egress"]
#: 订阅结局的封闭词表（`gateway_sse_subscription_total` 的 label 取值）。
SseSubscriptionStatus = Literal[
    "opened", "resumed", "rejected", "CURSOR_EXPIRED", "CURSOR_AHEAD", "failed"
]
#: 公开流关闭原因的封闭词表（`gateway_sse_closed_total` 的 label 取值）。
SseCloseReason = Literal[
    "slow_subscriber", "total_buffer", "malformed", "oversized", "owner_stream_failed"
]
#: 帧种类的封闭词表（`gateway_sse_frames_total` 的 label 取值）。
SseFrameKind = Literal["event", "checkpoint", "heartbeat", "snapshot_part", "filtered"]
#: 账本水位范围的封闭词表（`gateway_sse_ledger` 的 label 取值）。
SseLedgerScope = Literal["active_subscribers", "pending_bytes"]
#: 渠道交付结局的封闭词表（`gateway_channel_delivery_total` 的 label 取值）。
#: 与 `application/channel_delivery.py` 的 `TerminalDeliveryStatus` 同一词表（测试断言两者相等）。
DeliveryStatus = Literal["delivered", "deduplicated", "failed"]

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
    # --- TASK-098：公开流、池等待与渠道交付 ---------------------------------------------
    (
        SSE_SUBSCRIPTION_METRIC,
        COUNTER,
        ("status",),
        "Public SSE subscriptions by outcome (opened/resumed/rejected or Owner cursor code)",
    ),
    (
        SSE_LEDGER_METRIC,
        GAUGE,
        ("scope",),
        "Public SSE ledger watermarks (active subscribers, pending bytes)",
    ),
    (
        SSE_CLOSED_METRIC,
        COUNTER,
        ("reason",),
        "Public SSE explicit closes by bounded reason",
    ),
    (
        SSE_REJECTED_METRIC,
        COUNTER,
        (),
        "Public SSE subscriptions rejected at the process subscriber limit",
    ),
    (
        SSE_FRAMES_METRIC,
        COUNTER,
        ("kind",),
        "Public SSE frames by kind (event/checkpoint/heartbeat/snapshot/filtered)",
    ),
    (
        HTTP_POOL_WAIT_METRIC,
        COUNTER,
        ("pool",),
        "Outbound HTTP pool wait timeouts by pool (control/stream/upload/egress)",
    ),
    (
        CHANNEL_DELIVERY_METRIC,
        COUNTER,
        ("status",),
        "Channel deliveries by outcome (delivered/deduplicated/failed)",
    ),
)


def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次结果：status 取封闭词表或目录错误码。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


def record_reason(name: str, reason: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{reason}` 记一次带原因的结果：reason 只取封闭词表，不含资源 ID/凭据。"""
    inc_counter(name, 1, {**(labels or {}), "reason": reason})


def record_subscription(status: SseSubscriptionStatus) -> None:
    """记一次公开流订阅结局：新开/重连/拒绝/游标分型各自可区分。"""
    record_outcome(SSE_SUBSCRIPTION_METRIC, status)


def record_sse_close(reason: SseCloseReason) -> None:
    """记一次公开流显式关闭：每条关闭路径都留痕（不静默丢帧）。"""
    record_reason(SSE_CLOSED_METRIC, reason)


def record_sse_frame(kind: SseFrameKind) -> None:
    """记一帧下发/过滤：帧量按种类可对账。"""
    inc_counter(SSE_FRAMES_METRIC, 1, {"kind": kind})


def record_sse_rejection() -> None:
    """记一次订阅上限拒绝：显式拒绝（不排队、不静默丢），由服务层按目录错误码上抛。"""
    inc_counter(SSE_REJECTED_METRIC, 1)


def record_pool_wait(pool: GatewayPool) -> None:
    """记一次出站池等待超时：家族本身即「等池超时」，`pool` 是封闭枚举（不冒充别的池）。"""
    inc_counter(HTTP_POOL_WAIT_METRIC, 1, {"pool": pool})


def set_ledger_gauge(scope: SseLedgerScope, value: int) -> None:
    """发布进程级公开流账本水位（活跃订阅数 / 未下发字节）。"""
    set_gauge(SSE_LEDGER_METRIC, value, {"scope": scope})


def install_gateway_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
