from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from fluxion.errors.console import SUCCESS
from fluxion.observability.context import current_context

_DEFAULT_REQUEST_ID = "req_unknown"


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: object | None
    request_id: str
    # ADR-A015（TASK-019）：稳定机器可读 slug（snake_case），与 SSE error 事件
    # 同一词汇。成功时 None；旧载荷缺失时 None（解码侧回落 unknown_error，不读 message）。
    error: str | None = None


class PageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[object]
    page: int
    page_size: int
    total: int


# ADR-A015（TASK-020）：未知/兜底错误的固定安全文案——原文（含 SQL/DSN/Secret/
# 堆栈）永不回传，只进日志。已知 slug 的 message 由抛出处精心编写，保持可读。
SAFE_FALLBACK_MESSAGE = "execution failed"


def runtime_sse_error_data(code: int, slug: str | None, message: str, request_id: str) -> dict[str, object]:
    """SSE error 帧与 HTTP envelope 同一映射（TASK-020）：整数码 + slug + 安全文案。"""
    data: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if slug is not None:
        data["error"] = slug
    return data


def success(data: object | None, *, status_code: int = 200) -> JSONResponse:
    request_id = _request_id()
    response = ApiResponse(
        code=SUCCESS,
        message="success",
        data=data,
        request_id=request_id,
        error=None,
    )
    # 规则 22：Console 四字段 wire 契约不变——error 仅在有 slug 时出现；
    # data 键恒在（null 亦保留）。
    content = response.model_dump(mode="json", exclude={"error"})
    return _json_response(content, status_code=status_code, biz_code=SUCCESS)


def failure(
    code: int,
    message: str,
    *,
    status_code: int,
    request: Request | None = None,
    error: str | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    response = ApiResponse(
        code=code,
        message=message,
        data=None,
        request_id=request_id,
        error=error,
    )
    content = response.model_dump(
        mode="json", exclude={"error"} if error is None else None
    )
    return _json_response(content, status_code=status_code, biz_code=code, request=request)


def _json_response(
    content: dict[str, object],
    *,
    status_code: int,
    biz_code: int,
    request: Request | None = None,
) -> JSONResponse:
    headers = {"X-Request-ID": str(content["request_id"])}
    response = JSONResponse(status_code=status_code, content=content, headers=headers)
    response.headers["X-Biz-Code"] = str(biz_code)
    trace_id = _trace_id(request)
    if trace_id:
        response.headers["X-Trace-ID"] = trace_id
    return response


def _request_id(request: Request | None = None) -> str:
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    context = current_context()
    if context is None:
        return _DEFAULT_REQUEST_ID
    return context.request_id


def _trace_id(request: Request | None = None) -> str | None:
    if request is not None:
        state_id = getattr(request.state, "trace_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    context = current_context()
    if context is None:
        return None
    return context.trace_id
