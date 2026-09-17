import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin

ROLE_ADMIN = "ADMIN"
ROLE_BUILDER = "BUILDER"


class ConsoleAccount(StandardColumnsMixin, Base):
    __tablename__ = "console_account"
    __table_args__ = (
        sa.Index(
            "uq_console_account_tenant_username",
            "tenant_id",
            "username",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    username: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'BUILDER'"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    failed_attempts: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    locked_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ConsoleSession(StandardColumnsMixin, Base):
    __tablename__ = "console_session"
    __table_args__ = (
        sa.Index(
            "uq_console_session_token_hash",
            "token_hash",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_console_session_account_expires", "account_id", "expires_at"),
        {"schema": "control"},
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.console_account.id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    source_ip: Mapped[str | None] = mapped_column(sa.String(64))
