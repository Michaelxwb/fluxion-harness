"""Connector 指标目录（无流量时也可见；label 只含封闭结局码）。"""

from __future__ import annotations

from typing import Final, Literal

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics

SERVICE = "muad-im-connector"

LEASE_METRIC: Final = "connector_lease_total"
LEASE_ACTIVE_METRIC: Final = "connector_bots_owned"
LEASE_LOST_METRIC: Final = "connector_lease_lost_total"
INBOUND_METRIC: Final = "connector_inbound_total"
INBOUND_LOST_METRIC: Final = "connector_inbound_lost_total"
OUTBOUND_METRIC: Final = "connector_outbound_total"
OUTBOUND_DEAD_METRIC: Final = "connector_outbound_dead_total"
SNAPSHOT_METRIC: Final = "connector_snapshot_refresh_total"

LeaseOutcome = Literal["acquired", "renewed", "released", "lost", "renew_failed"]
InboundOutcome = Literal["published", "duplicate", "lost", "failed"]
OutboundOutcome = Literal[
    "ACCEPTED",
    "DELIVERED",
    "DEGRADED",
    "FAILED",
    "FENCED",
    "EXPIRED",
    "DUPLICATE",
    "DEAD_LETTER",
    "RETRY",
]
SnapshotOutcome = Literal["ok", "failed", "incomplete"]

#: 指标目录：(名称, 类型, label 名, help)。`/metrics` 在无流量时也暴露完整目录（harness-arch）。
CATALOG: Final = (
    (LEASE_METRIC, "counter", ("outcome",), "Connector bot lease outcomes"),
    (LEASE_LOST_METRIC, "counter", (), "Connector bot lease losses"),
    (LEASE_ACTIVE_METRIC, "gauge", (), "Bots currently owned by this connector"),
    (INBOUND_METRIC, "counter", ("outcome",), "Connector inbound publish outcomes"),
    (INBOUND_LOST_METRIC, "counter", (), "Inbound messages lost before XADD"),
    (OUTBOUND_METRIC, "counter", ("outcome",), "Connector outbound command outcomes"),
    (OUTBOUND_DEAD_METRIC, "counter", (), "Connector outbound dead letters"),
    (SNAPSHOT_METRIC, "counter", ("outcome",), "Connector bot snapshot refresh outcomes"),
)


def record_lease(outcome: LeaseOutcome, count: int = 1) -> None:
    inc_counter(
        LEASE_METRIC,
        count,
        {"outcome": outcome},
        help="Connector bot lease outcomes",
    )
    if outcome == "lost":
        inc_counter(LEASE_LOST_METRIC, count, help="Connector bot lease losses")


def set_owned_bots(count: int) -> None:
    from muad_api import set_gauge

    set_gauge(LEASE_ACTIVE_METRIC, float(count), help="Bots currently owned by this connector")


def record_inbound(outcome: InboundOutcome, count: int = 1) -> None:
    inc_counter(
        INBOUND_METRIC,
        count,
        {"outcome": outcome},
        help="Connector inbound publish outcomes",
    )
    if outcome == "lost":
        inc_counter(INBOUND_LOST_METRIC, count, help="Inbound messages lost before XADD")


def record_outbound(outcome: str, reason: str = "") -> None:
    labels = {"outcome": outcome}
    if reason:
        labels["reason"] = reason
    inc_counter(
        OUTBOUND_METRIC,
        1,
        labels,
        help="Connector outbound command outcomes",
    )


def record_snapshot(outcome: SnapshotOutcome) -> None:
    inc_counter(
        SNAPSHOT_METRIC,
        1,
        {"outcome": outcome},
        help="Connector bot snapshot refresh outcomes",
    )


def install_connector_metrics(app: FastAPI) -> None:
    install_metrics(app)
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
