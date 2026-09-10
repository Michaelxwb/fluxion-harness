from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageData(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
