"""经验管理异步任务表。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_job",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("experience_key", sa.String(64), nullable=False),
        sa.Column("job_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("command_id", sa.String(256), nullable=False),
        sa.Column("request_json", JSONB(), nullable=False),
        sa.Column("result_json", JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(512)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        schema="runtime",
    )
    op.create_index(
        "uq_learning_job_command",
        "learning_job",
        ["tenant_id", "command_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_learning_job_claim",
        "learning_job",
        ["status", "lease_until", "create_time"],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text("status IN ('PENDING','RUNNING')"),
    )
    op.create_index(
        "ix_learning_job_experience",
        "learning_job",
        ["tenant_id", "user_id", "experience_key", sa.text("create_time DESC")],
        unique=False,
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_index("ix_learning_job_experience", table_name="learning_job", schema="runtime")
    op.drop_index("ix_learning_job_claim", table_name="learning_job", schema="runtime")
    op.drop_index("uq_learning_job_command", table_name="learning_job", schema="runtime")
    op.drop_table("learning_job", schema="runtime")
