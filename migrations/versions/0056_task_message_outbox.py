"""Task success terminal message outbox (§4.6).

`task.message_outbox` freezes one user-visible message (text + attachments) when a
scheduled Task succeeds with a PUSH route. The Worker consumer hands it to Runtime
`message_delivery`; success is handoff, not channel delivery. Failed/cancelled Tasks
never write here, and a Task without a push route keeps Web pull only.

Development data may be cleared; no backfill from `task_delivery` is performed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

SCHEMA = "task"


def _standard() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "create_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "update_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "message_outbox",
        *_standard(),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("task.task_execution.id"),
            nullable=False,
        ),
        sa.Column("message_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_key", sa.String(256), nullable=False),
        sa.Column("route_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "message_key",
            "target_key",
            name="uq_task_message_outbox_identity",
        ),
        sa.CheckConstraint("status IN ('PENDING','APPLIED','FAILED')", name="status"),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_message_outbox_due",
        "message_outbox",
        ["tenant_id", "next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_task_message_outbox_task",
        "message_outbox",
        ["task_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_task_message_outbox_task", table_name="message_outbox", schema=SCHEMA)
    op.drop_index("ix_task_message_outbox_due", table_name="message_outbox", schema=SCHEMA)
    op.drop_table("message_outbox", schema=SCHEMA)
