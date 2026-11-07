"""Skill 草稿增加 Agent 维度：路径落到 {workspace}/{tenant}/{user}/{agent}/skill-drafts。

开发阶段没有存量数据：旧草稿行无法归属到 Agent，迁移时直接清空后落 NOT NULL 列。
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM control.skill_draft")
    op.drop_index("uq_skill_draft_tenant_owner_key", table_name="skill_draft", schema="control")
    op.add_column(
        "skill_draft",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        schema="control",
    )
    op.create_index(
        "uq_skill_draft_tenant_owner_agent_key",
        "skill_draft",
        ["tenant_id", "owner_user_id", "agent_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_skill_draft_tenant_owner_agent_key", table_name="skill_draft", schema="control")
    op.drop_column("skill_draft", "agent_id", schema="control")
    op.create_index(
        "uq_skill_draft_tenant_owner_key",
        "skill_draft",
        ["tenant_id", "owner_user_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
