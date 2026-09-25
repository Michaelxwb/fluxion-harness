from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 审计导出任务事实（API-05 创建、API-06 状态/下载）。幂等唯一性由共享幂等表
    # control.skill_import_idempotency (tenant_id, idempotency_key, endpoint) 承载，
    # 本表不重复承担该约束。
    op.create_table(
        "audit_export_job",
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
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("export_format", sa.String(8), nullable=False),
        sa.Column("filters_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("artifact_ref", sa.String(256), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Index(
            "ix_audit_export_job_tenant_status_create_time",
            "tenant_id",
            "status",
            sa.text("create_time"),
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("audit_export_job", schema="control")
