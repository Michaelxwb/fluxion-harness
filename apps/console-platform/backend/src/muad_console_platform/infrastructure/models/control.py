import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class ModelDefinition(StandardColumnsMixin, Base):
    __tablename__ = "model_definition"
    __table_args__ = (
        sa.Index(
            "uq_model_definition_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'OPENAI'"),
    )
    model_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(sa.String(256))
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    last_test_status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'UNTESTED'"),
    )
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AgentAccessGrant(StandardColumnsMixin, Base):
    __tablename__ = "agent_access_grant"
    __table_args__ = (
        sa.Index(
            "uq_agent_access_grant_user_agent",
            "user_id",
            "agent_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_agent_access_grant_agent_user", "agent_id", "user_id"),
        {"schema": "control"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.agent_definition.id"),
        nullable=False,
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


class AgentDefinition(StandardColumnsMixin, Base):
    __tablename__ = "agent_definition"
    __table_args__ = (
        sa.Index(
            "uq_agent_definition_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_agent_definition_model_enabled", "model_id", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text())
    instructions: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.model_definition.id"),
        nullable=False,
    )
    runtime_config: Mapped[dict[str, Any]] = mapped_column(
        "runtime_config_json",
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
