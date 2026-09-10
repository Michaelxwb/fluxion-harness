from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.orm import DeclarativeBase


T = TypeVar("T", bound=DeclarativeBase)


def active_only(stmt: Select, model: type[T]) -> Select:
    """Apply the framework-wide soft-delete filter.

    Repository code must use this helper or an equivalent explicit predicate.
    ORM-level global filters are intentionally avoided because administrative
    recovery/audit flows sometimes need to query deleted rows explicitly.
    """

    return stmt.where(model.is_deleted.is_(False))
