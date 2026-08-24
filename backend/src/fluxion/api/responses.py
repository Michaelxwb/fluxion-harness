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


class PageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[object]
    page: int
    page_size: int
    total: int


def success(data: object | None, *, status_code: int = 200) -> JSONResponse:
    request_id = _request_id()
    content = ApiResponse(
        code=SUCCESS,
        message="success",
        data=data,
        request_id=request_id,
    ).model_dump(mode="json")
    return _json_response(content, status_code=status_code, biz_code=SUCCESS)


def failure(
    code: int,
    message: str,
    *,
    status_code: int,
    request: Request | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    content = ApiResponse(
        code=code,
        message=message,
        data=None,
        request_id=request_id,
    ).model_dump(mode="json")
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
