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
    secret: Mapped[str | None] = mapped_column(sa.Text())
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


class ExternalIdentity(StandardColumnsMixin, Base):
    """用户级外部身份（RULE-data-002）：回答「这个外部标识对应哪个平台用户」。

    与旧 ``channel_identity`` 的区别：身份不绑定某个入口，``bot_account_id`` 不在本表；
    入口授权见 :class:`EntryAccessGrant`。``identity_hash`` 由平台 ``identity_keys``
    提取的字段按 key 排序做规范 JSON 再 sha256 得到，字段集合变更会让历史行无法复算。
    """

    __tablename__ = "external_identity"
    __table_args__ = (
        sa.Index(
            "uq_external_identity_tenant_platform_hash",
            "tenant_id",
            "platform_key",
            "identity_hash",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_external_identity_tenant_user", "tenant_id", "platform_user_id"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    identity_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    identity_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )


class EntryAccessGrant(StandardColumnsMixin, Base):
    """入口授权（RULE-data-002）：回答「这个身份能在哪个入口下使用」。

    解绑只软删本表行，:class:`ExternalIdentity` 保留 —— 重绑后历史会话归属不变。
    """

    __tablename__ = "entry_access_grant"
    __table_args__ = (
        sa.Index(
            "uq_entry_access_grant_tenant_identity_bot",
            "tenant_id",
            "external_identity_id",
            "bot_account_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "ix_entry_access_grant_tenant_bot_identity",
            "tenant_id",
            "bot_account_id",
            "external_identity_id",
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    external_identity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.external_identity.id"),
        nullable=False,
    )
    bot_account_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.bot_account.id"),
        nullable=False,
    )
