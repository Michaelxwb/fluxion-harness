"""TASK-007 User Domain 三表 + TASK-008 chat access agent_id 列改名（H7）。

Revision ID: f7a3c91d2e84
Revises: a3f8c2e9b517
Create Date: 2026-08-27

- user_profiles / user_preferences / capability_grants 三张新表（Gate 1B）。
- chat_access_tokens.runtime_profile_id → agent_id（TASK-A105 产品路由迁移）。
 存量行值本就是 agent 语义（A105 前后端同以 profile id 占位），RENAME 后由
 应用层迁移脚本（scripts/migrate_runtime_profiles_to_agents.py）补数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a3c91d2e84"
down_revision = "a3f8c2e9b517"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "chat_access_tokens",
        "runtime_profile_id",
        new_column_name="agent_id",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )

    op.create_table(
        "user_profiles",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("platform_user_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "platform_user_id", "version"),
    )
    op.create_index(
        "idx_user_profiles_latest",
        "user_profiles",
        ["tenant_id", "platform_user_id", sa.text("version DESC")],
    )

    op.create_table(
        "user_preferences",
        sa.Column("tenant_id", sa.String(length=128), primary_key=True),
        sa.Column("platform_user_id", sa.String(length=128), primary_key=True),
        sa.Column("preference_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "capability_grants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("platform_user_id", sa.String(length=128), nullable=False),
        sa.Column("capability_ref", sa.String(length=255), nullable=False),
        sa.Column("granted_scope", sa.String(length=32), nullable=False),
        sa.Column("version_pin", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_capability_grants_user",
        "capability_grants",
        ["tenant_id", "platform_user_id", "capability_ref"],
    )


def downgrade() -> None:
    op.drop_table("capability_grants")
    op.drop_table("user_preferences")
    op.drop_index("idx_user_profiles_latest", table_name="user_profiles")
    op.drop_table("user_profiles")
    op.alter_column(
        "chat_access_tokens",
        "agent_id",
        new_column_name="runtime_profile_id",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
