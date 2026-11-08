"""runtime.artifact 增加 agent/user 归属：历史产出按 agent + user 跨会话检索。"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifact", sa.Column("agent_id", sa.Uuid()), schema="runtime")
    op.add_column("artifact", sa.Column("user_id", sa.Uuid()), schema="runtime")
    op.create_index(
        "ix_artifact_agent_user_time",
        "artifact",
        ["tenant_id", "agent_id", "user_id", sa.text("create_time DESC")],
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_agent_user_time", table_name="artifact", schema="runtime")
    op.drop_column("artifact", "user_id", schema="runtime")
    op.drop_column("artifact", "agent_id", schema="runtime")
