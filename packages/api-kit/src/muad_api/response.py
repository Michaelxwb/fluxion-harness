from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from .catalog import MessageCatalog
from .context import current_locale, current_request_id, current_trace_id
from .error_codes import ErrorCode
from .errors import AppError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class ApiResponse[T](BaseModel):
    code: str
    msg: str
    data: T | None = None
    trace_id: str
    request_id: str
    timestamp: str


@dataclass(frozen=True)
class Page:
    page: int
    page_size: int


def validate_page(page: int, page_size: int) -> Page:
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return Page(page=page, page_size=page_size)


def paginate(
    *,
    items: Sequence[Any],
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    total: int,
) -> dict[str, Any]:
    valid = validate_page(page, page_size)
    return {
        "items": list(items),
        "page": valid.page,
        "page_size": valid.page_size,
        "total": total,
    }


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
        timestamp=datetime.now(UTC).isoformat(),
    )


def ok(catalog: MessageCatalog, data: Any = None) -> ApiResponse[Any]:
    return build_response(catalog, code="0", data=data)
