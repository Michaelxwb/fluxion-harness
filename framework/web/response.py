from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from framework.observability.context import request_id_ctx

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = "success"
    data: T | None = None
    request_id: str = Field(default_factory=request_id_ctx.get)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def ok(data: T | None = None, *, message: str = "success") -> ApiResponse[T]:
    return ApiResponse[T](data=data, message=message)


def failure(*, code: str, message: str, data: object | None = None) -> ApiResponse[object]:
    return ApiResponse[object](code=code, message=message, data=data)
