from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _standard_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    op.create_table(
        "run_submission",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runtime.run_record.id")),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("runtime.conversation.id")),
        sa.Column("response_status", sa.Integer(), nullable=False, server_default=sa.text("200")),
        sa.Column("response_headers_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_seq", sa.BigInteger()),
        sa.Column("last_seq", sa.BigInteger()),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("terminal_result_json", JSONB()),
        schema="runtime",
    )
    op.create_index(
        "uq_run_submission_tenant_key_endpoint",
        "run_submission",
        ["tenant_id", "idempotency_key", "endpoint"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_run_submission_run_create_time",
        "run_submission",
        ["run_id", "create_time"],
        schema="runtime",
    )

    op.add_column(
        "canonical_event",
        sa.Column(
            "submission_id",
            sa.Uuid(),
            sa.ForeignKey("runtime.run_submission.id"),
            nullable=True,
        ),
        schema="runtime",
    )
    op.add_column(
        "canonical_event",
        sa.Column("stream_type", sa.String(64), nullable=True),
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_column("canonical_event", "stream_type", schema="runtime")
    op.drop_column("canonical_event", "submission_id", schema="runtime")
    op.drop_index("ix_run_submission_run_create_time", "run_submission", schema="runtime")
    op.drop_index("uq_run_submission_tenant_key_endpoint", "run_submission", schema="runtime")
    op.drop_table("run_submission", schema="runtime")
