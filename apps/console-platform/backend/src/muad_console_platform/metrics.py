"""Console 指标目录（design §3.5 可观测性 / docs/09 §6.5）。

只导出 api-kit 的进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）；
指标不进入业务 Console 渲染，也不作为业务审计事实源（权威数据在 PostgreSQL）。
label 只含路由模板 / HTTP 状态 / catalog 错误码，不得包含 Secret、凭据、消息正文或 PII。

CHAT 认证、模型探针、池等待与配置失败是四个**可区分**的观测面（TASK-099）：
- `chat_auth_total{operation,status}`：认证结局按目录错误码分桶（`CHAT_TOKEN_*` 与
  `PLATFORM_SETTINGS_UNAVAILABLE` 是不同样本，认证失败不冒充配置失败）；
- `model_probe_total{status}` / `model_probe_failure_total{reason}`：探针结论与封闭失败原因；
- `console_http_pool_wait_total{pool}`：真实的池等待超时（control/model/batch）；
- `console_config_failure_total{component,reason}`：缺服务身份 / 预算冲突这类**启动或装配**配置失败。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Final, Literal

from fastapi import FastAPI, Request, Response
from muad_api import AppError, declare_metric, inc_counter, install_metrics
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

API_REQUESTS_METRIC: Final = "console_api_requests_total"
SKILL_IMPORT_METRIC: Final = "skill_import_total"
RESOLVE_DEFINITION_METRIC: Final = "runtime_definition_resolve_total"
BIND_METRIC: Final = "bind_total"
# 平台设置（design §3.5 可观测性）：保存结局 + 内部取设置结局。
PLATFORM_SETTINGS_SAVE_METRIC: Final = "platform_settings_save_total"
PLATFORM_SETTINGS_FETCH_METRIC: Final = "platform_settings_fetch_total"
# CHAT 公开认证（TASK-099）：按 operation（exchange/me/logout）与结局码计数。
CHAT_AUTH_METRIC: Final = "chat_auth_total"
# 模型探针（TASK-099）：结论 + 封闭失败原因（池等待与模型失败分开）。
MODEL_PROBE_METRIC: Final = "model_probe_total"
MODEL_PROBE_FAILURE_METRIC: Final = "model_probe_failure_total"
# 出站池等待超时（TASK-099）：与配置失败分开的容量压力观测面。
HTTP_POOL_WAIT_METRIC: Final = "console_http_pool_wait_total"
# 装配/启动配置失败（TASK-099）：缺服务身份、预算冲突等，不混进运行期结局。
CONFIG_FAILURE_METRIC: Final = "console_config_failure_total"

#: Console 出站池名（封闭枚举；`batch` 是既有批量测试自建客户端的池）。
ConsolePool = Literal["control", "model", "batch"]

SUCCESS_STATUS: Final = "SUCCESS"
FAILED_STATUS: Final = "FAILED"
COUNTER: Final = "counter"
# 未命中任何路由时不记录具体路径：避免把用户可控路径（含标识符）当 label。
UNMATCHED_PATH: Final = "unmatched"

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (
        PLATFORM_SETTINGS_SAVE_METRIC,
        COUNTER,
        ("result",),
        "Platform settings saves by result (ok/validation_failed/conflict)",
    ),
    (
        PLATFORM_SETTINGS_FETCH_METRIC,
        COUNTER,
        ("caller", "result"),
        "Platform settings snapshot fetches by caller and result",
    ),
    (
        CHAT_AUTH_METRIC,
        COUNTER,
        ("operation", "status"),
        "CHAT auth outcomes by operation and result (SUCCESS or catalog error code)",
    ),
    (MODEL_PROBE_METRIC, COUNTER, ("status",), "Model probe results by status"),
    (
        MODEL_PROBE_FAILURE_METRIC,
        COUNTER,
        ("reason",),
        "Model probe failures by bounded reason (pool wait separated from model failures)",
    ),
    (
        HTTP_POOL_WAIT_METRIC,
        COUNTER,
        ("pool",),
        "Outbound HTTP pool wait timeouts by pool (control/model/batch)",
    ),
    (
        CONFIG_FAILURE_METRIC,
        COUNTER,
        ("component", "reason"),
        "Console outbound config failures by component and bounded reason",
    ),
)



def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次结果：成功为 `SUCCESS`，失败为 catalog 错误码或 `FAILED`。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


def record_reason(name: str, reason: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{reason}` 记一次带原因的结果：reason 只取封闭枚举，不含资源 ID/凭据。"""
    inc_counter(name, 1, {**(labels or {}), "reason": reason})


def record_pool_wait(pool: ConsolePool) -> None:
    """记一次出站池等待超时：家族本身即「等池超时」，`pool` 是封闭枚举（不冒充别的池）。"""
    inc_counter(HTTP_POOL_WAIT_METRIC, 1, {"pool": pool})


@contextmanager
def count_outcome(name: str, labels: Mapping[str, str] | None = None) -> Iterator[None]:
    """统计一次领域操作的结局；异常继续上抛，由统一异常处理转成响应封套。"""
    try:
        yield
    except AppError as error:
        record_outcome(name, error.code, labels)
        raise
    except Exception:
        record_outcome(name, FAILED_STATUS, labels)
        raise
    record_outcome(name, SUCCESS_STATUS, labels)


def _request_path(request: Request) -> str:
    """路由模板（如 `/api/v1/models/{model_id}`），不返回请求的具体路径取值。"""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else UNMATCHED_PATH


class ApiRequestMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        inc_counter(
            API_REQUESTS_METRIC,
            1,
            {"path": _request_path(request), "status": str(response.status_code)},
            help="Console API requests by route template and status",
        )
        return response


def install_console_metrics(app: FastAPI) -> None:
    """注册真实 HTTP `GET /metrics` 与请求计数中间件（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
    app.add_middleware(ApiRequestMetricsMiddleware)
