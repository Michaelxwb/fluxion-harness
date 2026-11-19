"""Persistent coordination state for synchronous auth-cache invalidation.

One row per (tenant, scope). `scope='TENANT'` is the tenant-level barrier
coordinator: it records the in-flight `operation_id`, the phase
(`PREPARING`/`COMMITTED`/`READY`), the affected user set, the pre-change
revision, and the random `cache_fence` that fences off stale back-fills.
`scope='PLATFORM'` records a platform's non-secret `policy_epoch` and
availability so a restart can reconcile what is actually active.

Nothing here stores factors or tokens: `policy_epoch` is an opaque, non-secret
activation version, and the tenant row only carries ids and counters.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0053"
# 接在外部身份迁移（已重排为 "0052"）之后——见该文件的编号说明。
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_cache_sync_state",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        # "TENANT" = tenant-level barrier coordinator; "PLATFORM" = per-platform activation.
        sa.Column("scope", sa.String(16), nullable=False),
        # "" for TENANT rows, platform_key for PLATFORM rows.
        sa.Column("scope_key", sa.String(64), nullable=False, server_default=sa.text("''")),
        # Barrier phase for TENANT rows: PREPARING / COMMITTED / READY.
        sa.Column("phase", sa.String(16), nullable=False, server_default=sa.text("'READY'")),
        # Operation id of the in-flight invalidation; null when idle/READY.
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        # Random fence guarding back-fills; rotated on every invalidation.
        sa.Column("cache_fence", sa.String(64), nullable=True),
        # Scope of the in-flight change, e.g. {"users": [...]} or {"kind": "agent", "id": ...}.
        sa.Column("affected_scope", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Non-secret platform activation version (PLATFORM rows only).
        sa.Column("policy_epoch", sa.String(64), nullable=True),
        # Platform availability flag (PLATFORM rows only).
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Recovery bookkeeping: how many times this scope needed reconciling.
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_auth_cache_sync_state"),
        schema="control",
    )
    op.create_index(
        "uq_auth_cache_sync_state_tenant_scope",
        "auth_cache_sync_state",
        ["tenant_id", "scope", "scope_key"],
        unique=True,
        schema="control",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_auth_cache_sync_state_tenant_scope",
        table_name="auth_cache_sync_state",
        schema="control",
    )
    op.drop_table("auth_cache_sync_state", schema="control")
