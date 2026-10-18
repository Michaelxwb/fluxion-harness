"""Console 出站 HTTP 客户端装配（design §3.7「启动/部署配置」；TASK-097）。

- **预算唯一来源是 `SharedSettings`**：池大小（连接/keepalive）与超时都从 typed 字段读，
  本模块不复制默认值；非法预算在 `SharedSettings` 构造时显式失败，不做静默回落。
- **探针独立**：物理 Key 检测（TASK-028）用两个自己的池——容量端口（API-17）走 control 池、
  外部模型探测走 model 池；两者互不共享，也不与 Console 其它出站客户端共享连接。
- **探针执行预算是契约常量**：单次 5 秒、并发 4（design §3.7「这些上限无需另建运营旋钮」），
  以 `ProbeBudget` 注入执行器；装配根不重抄这两个值。
- **冲突显式失败**：探针的单次预算超出对应池的读预算时（部署预算与契约冲突），启动即报错，
  不静默取较小值（design §3.7「资源硬预算约束业务上限，但不静默取较小值掩盖错误」）。
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from muad_common import SharedSettings

from ..application.model_probe_service import (
    CAPACITY_REQUEST_TIMEOUT_SEC,
    PROBE_TIMEOUT_SEC,
    ProbeBudget,
    ProbeCapacityClient,
)
from ..metrics import CONFIG_FAILURE_METRIC, record_reason


class ProbeCapacityUnconfigured(RuntimeError):
    """容量端口客户端缺部署配置：指名缺失项，调用方显式失败（不建必然被 403 的裸客户端）。"""

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__("missing: " + ", ".join(missing))
        self.missing: tuple[str, ...] = tuple(missing)


def control_pool_limits(settings: SharedSettings) -> httpx.Limits:
    """控制面池预算（design §3.7 `CONTROL_HTTP_*`）：短控制/续租/认证请求专用。"""
    return httpx.Limits(
        max_connections=settings.control_http_max_connections,
        max_keepalive_connections=settings.control_http_max_keepalive,
    )


def model_pool_limits(settings: SharedSettings) -> httpx.Limits:
    """模型池预算（design §3.7 `MODEL_HTTP_*`）：Console 侧探测外呼专用。"""
    return httpx.Limits(
        max_connections=settings.model_http_max_connections,
        max_keepalive_connections=settings.model_http_max_keepalive,
    )


def probe_budget(settings: SharedSettings) -> ProbeBudget:
    """探针执行预算（契约常量）+ 与模型池读预算的冲突校验。"""
    _require_within_pool_read_budget(
        "probe single-shot",
        PROBE_TIMEOUT_SEC,
        "MODEL_HTTP_READ_TIMEOUT_SEC",
        settings.model_http_read_timeout_sec,
    )
    return ProbeBudget()


def build_probe_capacity_client(
    settings: SharedSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> ProbeCapacityClient:
    """API-17 容量端口客户端：control 池 + 5 秒请求预算；缺服务身份时指名失败。"""
    _require_within_pool_read_budget(
        "probe capacity request",
        CAPACITY_REQUEST_TIMEOUT_SEC,
        "CONTROL_HTTP_READ_TIMEOUT_SEC",
        settings.control_http_read_timeout_sec,
    )
    missing = tuple(
        name
        for name, value in (
            ("AGENT_RUNTIME_URL", settings.agent_runtime_url),
            ("INTERNAL_SERVICE_TOKEN", settings.internal_service_token),
        )
        if not value
    )
    if missing:
        raise ProbeCapacityUnconfigured(missing)
    return ProbeCapacityClient(
        settings.agent_runtime_url,
        service_token=settings.internal_service_token,
        timeout_sec=CAPACITY_REQUEST_TIMEOUT_SEC,
        limits=control_pool_limits(settings),
        transport=transport,
    )


def build_probe_http_client(
    settings: SharedSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """Console 侧探测外呼客户端：model 池 + 单次 5 秒统一预算（连接/读/写/池）。"""
    budget = probe_budget(settings)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(budget.timeout_sec),
        limits=model_pool_limits(settings),
        transport=transport,
    )


def _require_within_pool_read_budget(
    name: str, budget_sec: float, field: str, pool_read_sec: float
) -> None:
    """契约预算与部署池读预算冲突时显式失败：不静默取较小值（design §3.7）。

    冲突是**配置失败**（不是运行期池等待）：先记进 `console_config_failure_total` 再报错，
    运维能从指标面看到"启动被预算冲突挡住"，而不是和容量压力混在一起。
    """
    if budget_sec > pool_read_sec:
        record_reason(CONFIG_FAILURE_METRIC, "budget_conflict", {"component": "probe_budget"})
        raise ValueError(
            f"{name} budget {budget_sec}s exceeds {field}={pool_read_sec}s; "
            "raise the pool read budget or change the contract budget explicitly"
        )
