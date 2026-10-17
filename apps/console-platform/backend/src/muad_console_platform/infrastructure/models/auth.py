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


class ChatAccessToken(StandardColumnsMixin, Base):
    """用户 Webchat Token（设计 §3.3 / API-03）：**只存摘要**，原文只在签发响应出现一次。

    每用户至多一个未撤销 Token（partial unique `WHERE is_deleted = false AND revoked_at IS NULL`）：
    重签必须先显式撤销旧行——**含已过期但未撤销的记录**——再按更高 `revision` 新签；旧行保留作
    审计，不物理删除。`token_hash` 是 64 位小写十六进制摘要，随机 Token 原文的常见形态（urlsafe
    串、大写十六进制）无法通过该约束 ⇒"不落原文"是数据库事实，不依赖调用方自觉。

    `platform_user_id` 是同 Owner（control schema）物理 FK：Token 只能签给已存在的平台用户；
    撤销/重置只作用于本行与 `chat_session` 关联行，不授予 Agent/Console 权限。
    """

    __tablename__ = "chat_access_token"
    __table_args__ = (
        sa.Index(
            "uq_chat_access_token_token_hash",
            "token_hash",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_chat_access_token_tenant_user_revision",
            "tenant_id",
            "platform_user_id",
            "revision",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_chat_access_token_tenant_user_unrevoked",
            "tenant_id",
            "platform_user_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false AND revoked_at IS NULL"),
        ),
        sa.CheckConstraint("revision >= 1", name="revision"),
        sa.CheckConstraint("expires_at > issued_at", name="expires_after_issued"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    issued_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ChatSession(StandardColumnsMixin, Base):
    """独立 CHAT 登录会话（设计 §3.3 / API-04）：随机会话 Token 的摘要，原文只进 HttpOnly Cookie。

    来源 `access_token_id` 与 `platform_user_id` 都是同 Owner 物理 FK；一个用户 Token 可以有多条
    会话（重复登录/多设备），`ix_chat_session_access_token` 支撑"撤销 Token 时按来源关联撤销"，
    `ix_chat_session_tenant_user_expires` 支撑"某用户未过期会话"查询。

    "本会话期限 ≤ 来源 Token 剩余期限"是服务端原子校验（跨表规则，不是库级约束），因此换回的
    会话 Token 独立随机、不把用户 Token 原文写 Cookie；logout 只撤销本会话，不撤销用户 Token。
    """

    __tablename__ = "chat_session"
    __table_args__ = (
        sa.Index(
            "uq_chat_session_token_hash",
            "token_hash",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_chat_session_tenant_user_expires", "tenant_id", "platform_user_id", "expires_at"),
        sa.Index(
            "ix_chat_session_access_token",
            "access_token_id",
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.CheckConstraint("expires_at > issued_at", name="expires_after_issued"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    access_token_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.chat_access_token.id"),
        nullable=False,
    )
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
