"""Message-level push delivery state machine (§4.5).

`runtime.message_delivery` is the single authoritative push state for Run and (later)
Task results: one row per stable `(tenant, message_key, target_key)`. The lease columns
mirror the existing channel-notification reserve/renew/ack protocol so both stores can
share the same state machine. `message_delivery_attempt` keeps one receipt per lease.

Development data may be cleared; no backfill from `channel_notification` or
`task_delivery` is performed (no dual-write).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

SCHEMA = "runtime"


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
        "message_delivery",
        *_standard(),
        sa.Column("owner_type", sa.String(8), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("message_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_key", sa.String(256), nullable=False),
        sa.Column("route_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("message_id", sa.String(256)),
        sa.Column("agent_id", sa.Uuid()),
        sa.Column("platform_user_id", sa.Uuid()),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column(
            "lease_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("fallback_url", sa.Text()),
        # First ack receipt, replayed instead of resending (same shape as channel_notification).
        sa.Column("delivery_result", JSONB()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "message_key",
            "target_key",
            name="uq_message_delivery_tenant_key_target",
        ),
        sa.CheckConstraint("owner_type IN ('RUN','TASK')", name="owner_type"),
        sa.CheckConstraint(
            "kind IN ('TERMINAL','ARTIFACT','HUMAN_QUESTION','TASK_RESULT')", name="kind"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','DELIVERING','DELIVERED','DEGRADED','UNKNOWN','FAILED')",
            name="status",
        ),
        sa.CheckConstraint("attempt_count >= 0 AND lease_epoch >= 0", name="counters"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_message_delivery_due",
        "message_delivery",
        ["tenant_id", "next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_message_delivery_lease",
        "message_delivery",
        ["tenant_id", "lease_until"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'DELIVERING'"),
    )
    op.create_index(
        "ix_message_delivery_owner",
        "message_delivery",
        ["tenant_id", "owner_type", "owner_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "message_delivery_attempt",
        *_standard(),
        sa.Column(
            "delivery_id",
            sa.Uuid(),
            sa.ForeignKey("runtime.message_delivery.id"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("result_json", JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "delivery_id",
            "attempt_no",
            name="uq_message_delivery_attempt_tenant_delivery_attempt",
        ),
        sa.CheckConstraint("attempt_no > 0 AND lease_epoch > 0", name="counters"),
        sa.CheckConstraint(
            "status IN ('PENDING','DELIVERING','DELIVERED','DEGRADED','UNKNOWN','FAILED')",
            name="status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_message_delivery_attempt_delivery",
        "message_delivery_attempt",
        ["tenant_id", "delivery_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_delivery_attempt_delivery",
        table_name="message_delivery_attempt",
        schema=SCHEMA,
    )
    op.drop_table("message_delivery_attempt", schema=SCHEMA)
    op.drop_index("ix_message_delivery_owner", table_name="message_delivery", schema=SCHEMA)
    op.drop_index("ix_message_delivery_lease", table_name="message_delivery", schema=SCHEMA)
    op.drop_index("ix_message_delivery_due", table_name="message_delivery", schema=SCHEMA)
    op.drop_table("message_delivery", schema=SCHEMA)
