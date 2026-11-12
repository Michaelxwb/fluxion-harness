"""Persistent, tenant-scoped egress identities; no per-request counters."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default=sa.text("'NEW'")),
        sa.Column("init_token_hash", sa.String(64), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "egress_execution", *_identity_columns(),
        sa.Column("source_kind", sa.String(8), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("policy_json", JSONB(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("meter_epoch", sa.String(128), nullable=False),
        sa.Column("current_fence", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("tenant_id", "source_kind", "source_id", name="uq_egress_execution_source"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_egress_execution_tenant_id"),
        sa.CheckConstraint("source_kind IN ('RUN','TASK')", name="ck_egress_execution_source_kind"),
        sa.CheckConstraint("state IN ('NEW','INITIALIZING','READY','BLOCKED','TERMINAL')",
                           name="ck_egress_execution_state"),
        sa.CheckConstraint("current_fence >= 0", name="ck_egress_execution_fence"),
        schema="runtime",
    )
    op.create_index("ix_egress_execution_state_time", "egress_execution", ["state", "update_time"],
                    schema="runtime")
    op.create_table(
        "egress_invocation", *_identity_columns(),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("skill_key", sa.Text(), nullable=False),
        sa.UniqueConstraint("execution_id", "operation_key", name="uq_egress_invocation_operation"),
        sa.ForeignKeyConstraint(["tenant_id", "execution_id"],
                                ["runtime.egress_execution.tenant_id", "runtime.egress_execution.id"],
                                name="fk_egress_invocation_tenant_execution"),
        sa.CheckConstraint("state IN ('NEW','INITIALIZING','READY','BLOCKED','TERMINAL')",
                           name="ck_egress_invocation_state"),
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_table("egress_invocation", schema="runtime")
    op.drop_index("ix_egress_execution_state_time", table_name="egress_execution", schema="runtime")
    op.drop_table("egress_execution", schema="runtime")
