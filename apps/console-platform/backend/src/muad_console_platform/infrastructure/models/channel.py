import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin

BIND_CODE_STATUS_ACTIVE = "ACTIVE"
BIND_CODE_STATUS_USED = "USED"
BIND_CODE_STATUS_EXPIRED = "EXPIRED"
BIND_CODE_STATUS_REVOKED = "REVOKED"


class BotAccount(StandardColumnsMixin, Base):
    __tablename__ = "bot_account"
    __table_args__ = (
        sa.Index(
            "uq_bot_account_bot_id",
            "bot_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_bot_account_agent_channel_enabled", "agent_id", "channel", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    channel: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'WECOM'"),
    )
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    bot_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    secret_ref: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.agent_definition.id"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    last_connected_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ChannelIdentity(StandardColumnsMixin, Base):
    __tablename__ = "channel_identity"
    __table_args__ = (
        sa.Index(
            "uq_channel_identity_tenant_identity_key",
            "tenant_id",
            "identity_key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_channel_identity_platform_user_channel", "platform_user_id", "channel"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    identity_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    external_user_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    bot_account_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.bot_account.id"),
        nullable=False,
    )
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    bound_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    last_active_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class BindCode(StandardColumnsMixin, Base):
    __tablename__ = "bind_code"
    __table_args__ = (
        sa.Index(
            "uq_bind_code_code_hash",
            "code_hash",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_bind_code_platform_user_status_expires", "platform_user_id", "status", "expires_at"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    used_channel_identity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
