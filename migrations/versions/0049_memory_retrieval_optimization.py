"""User-memory v2 semantics: structured facts, scoped notes, candidates.

Development has no history data, so this migration is additive for the
branch and does not preserve the legacy category-only contract.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("memory_kind", sa.String(24)),
        sa.Column("predicate", sa.String(64)),
        sa.Column("scope_state", sa.String(16), nullable=False, server_default=sa.text("'ABSENT'")),
        sa.Column("scope_raw", sa.String(512)),
        sa.Column("scope_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("scope_hash", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("title", sa.String(128), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("tags_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cardinality", sa.String(8), nullable=False, server_default=sa.text("'SINGLE'")),
        sa.Column("priority", sa.String(16), nullable=False, server_default=sa.text("'OPTIONAL'")),
        sa.Column("taxonomy_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    ):
        op.add_column("user_memory", column, schema="runtime")

    # v2 rows carry identity in memory_kind/predicate; category is the legacy lane's
    # discriminator (experience / TASK_STATE) and must stay NULL for v2 rows instead of
    # being filled with a fake legacy value.
    op.alter_column("user_memory", "category", nullable=True, schema="runtime")

    op.create_index(
        "ix_user_memory_v2_candidates",
        "user_memory",
        ["tenant_id", "user_id", "memory_kind", "predicate", sa.text("update_time DESC")],
        schema="runtime",
    )
    op.create_index(
        "ix_user_memory_v2_scope",
        "user_memory",
        ["tenant_id", "user_id", "predicate", "scope_hash"],
        schema="runtime",
    )

    op.create_table(
        "memory_conflict_candidate",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("memory_key", sa.String(128), nullable=False),
        sa.Column("predicate", sa.String(64), nullable=False),
        sa.Column("scope_state", sa.String(16), nullable=False),
        sa.Column("scope_raw", sa.String(512)),
        sa.Column("scope_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("scope_hash", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("content_json", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("title", sa.String(128), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", sa.String(512), nullable=False, server_default=sa.text("''")),
        sa.Column("source_quote", sa.String(512)),
        sa.Column("author", sa.String(16), nullable=False, server_default=sa.text("'AGENT'")),
        sa.Column("authority", sa.String(24), nullable=False, server_default=sa.text("'AGENT_INFERRED'")),
        sa.Column("confidence", sa.SmallInteger()),
        sa.Column("base_version", sa.BigInteger(), nullable=False),
        sa.Column("base_scope_revision", sa.BigInteger(), nullable=False),
        sa.Column("forget_epoch", sa.BigInteger(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_source_ref", sa.String(256)),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("resolved_by", sa.String(128)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("is_deleted = false", name="ck_memory_conflict_candidate_not_deleted"),
        sa.CheckConstraint(
            "status IN ('PENDING','ACCEPTED','REJECTED','STALE')",
            name="ck_memory_conflict_candidate_status",
        ),
        schema="runtime",
    )
    op.create_index(
        "uq_memory_conflict_pending",
        "memory_conflict_candidate",
        ["tenant_id", "user_id", "memory_key", "candidate_hash"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("status = 'PENDING' AND is_deleted = false"),
    )
    op.create_index(
        "ix_memory_conflict_pending",
        "memory_conflict_candidate",
        ["tenant_id", "user_id", "status", sa.text("create_time DESC")],
        schema="runtime",
    )

    op.drop_column("user_memory_history", "legacy_snapshots_json", schema="runtime")


def downgrade() -> None:
    op.add_column("user_memory_history", sa.Column("legacy_snapshots_json", JSONB()), schema="runtime")
    op.drop_index("ix_memory_conflict_pending", table_name="memory_conflict_candidate", schema="runtime")
    op.drop_index("uq_memory_conflict_pending", table_name="memory_conflict_candidate", schema="runtime")
    op.drop_table("memory_conflict_candidate", schema="runtime")
    op.drop_index("ix_user_memory_v2_scope", table_name="user_memory", schema="runtime")
    op.drop_index("ix_user_memory_v2_candidates", table_name="user_memory", schema="runtime")
    op.alter_column("user_memory", "category", nullable=False, schema="runtime")
    for name in (
        "taxonomy_version", "priority", "cardinality", "tags_json", "summary", "title",
        "content_text", "scope_hash", "scope_json", "scope_raw", "scope_state", "predicate",
        "memory_kind",
    ):
        op.drop_column("user_memory", name, schema="runtime")
