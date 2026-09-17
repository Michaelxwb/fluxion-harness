from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .catalog import MessageCatalog
from .context import current_locale, current_request_id, current_trace_id

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: str
    msg: str
    data: T | None = None
    trace_id: str
    request_id: str
    timestamp: str


def build_response(
    catalog: MessageCatalog,
    *,
    code: str,
    data: Any = None,
    message_args: dict[str, Any] | None = None,
) -> ApiResponse[Any]:
    return ApiResponse(
        code=code,
        msg=catalog.message(code, current_locale(), message_args),
        data=data,
        trace_id=current_trace_id(),
        request_id=current_request_id(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def ok(catalog: MessageCatalog, data: Any = None) -> ApiResponse[Any]:
    return build_response(catalog, code="0", data=data)
