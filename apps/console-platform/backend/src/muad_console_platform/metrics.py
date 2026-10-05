"""Console 指标目录（design §3.5 可观测性 / docs/09 §6.5）。

只导出 api-kit 的进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）；
指标不进入业务 Console 渲染，也不作为业务审计事实源（权威数据在 PostgreSQL）。
label 只含路由模板 / HTTP 状态 / catalog 错误码，不得包含 Secret、凭据、消息正文或 PII。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Final

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
)



def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次结果：成功为 `SUCCESS`，失败为 catalog 错误码或 `FAILED`。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


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
