from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, MetaData, String, false, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


DEFAULT_TENANT_ID = "default"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class TenantMixin:
    """Tenant scoping for every framework-owned table (08-表所有权 V1.12).

    V1 is single-tenant: the column always carries DEFAULT_TENANT_ID and is
    resolved from TrustedExecutionContext at runtime. Unique constraints are
    tenant-prefixed so a future multi-tenant deployment needs no schema change.
    """

    tenant_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default=DEFAULT_TENANT_ID,
        server_default=DEFAULT_TENANT_ID,
    )


class SoftDeleteTimestampMixin:
    """Mandatory columns for every framework-owned application table.

    is_deleted/create_time/update_time are persistence concerns and intentionally
    do not force every domain DTO to expose them.
    """

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
        default=False,
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
