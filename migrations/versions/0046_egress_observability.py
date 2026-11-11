# ruff: noqa: E501
"""Attempt-phase idempotency, interface summaries and visible audit gaps."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("egress_audit", sa.Column("attempt_id", sa.String(128)), schema="runtime")
    op.add_column("egress_audit", sa.Column("phase", sa.String(32)), schema="runtime")
    op.execute(sa.text("UPDATE runtime.egress_audit SET attempt_id = id::text WHERE attempt_id IS NULL"))
    op.execute(sa.text("UPDATE runtime.egress_audit SET phase = 'completed' WHERE phase IS NULL"))
    op.alter_column("egress_audit", "attempt_id", nullable=False, schema="runtime")
    op.alter_column("egress_audit", "phase", nullable=False, schema="runtime")
    op.create_index(
        "uq_egress_audit_attempt_phase",
        "egress_audit",
        ["tenant_id", "attempt_id", "phase"],
        unique=True,
        schema="runtime",
    )
    op.create_index(
        "ix_egress_audit_interface_time",
        "egress_audit",
        ["tenant_id", "platform_id", "adapter_key", "create_time"],
        schema="runtime",
    )
    op.create_table(
        "egress_interface_summary",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("target_instance_id", sa.String(128), nullable=False),
        sa.Column("route_id", sa.String(256), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        *[
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
            for name in ("attempted", "allowed", "sent", "completed", "blocked", "failed", "latency_total_ms")
        ],
        schema="runtime",
    )
    op.create_index(
        "uq_egress_summary_bucket",
        "egress_interface_summary",
        ["tenant_id", "target_instance_id", "route_id", "bucket_start"],
        unique=True,
        schema="runtime",
    )
    op.create_table(
        "egress_audit_gap",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lost_count", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        schema="runtime",
    )
    op.create_index(
        "uq_egress_gap_window",
        "egress_audit_gap",
        ["tenant_id", "window_start", "window_end"],
        unique=True,
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_index("uq_egress_gap_window", table_name="egress_audit_gap", schema="runtime")
    op.drop_table("egress_audit_gap", schema="runtime")
    op.drop_index("uq_egress_summary_bucket", table_name="egress_interface_summary", schema="runtime")
    op.drop_table("egress_interface_summary", schema="runtime")
    op.drop_index("ix_egress_audit_interface_time", table_name="egress_audit", schema="runtime")
    op.drop_index("uq_egress_audit_attempt_phase", table_name="egress_audit", schema="runtime")
    op.drop_column("egress_audit", "phase", schema="runtime")
    op.drop_column("egress_audit", "attempt_id", schema="runtime")
