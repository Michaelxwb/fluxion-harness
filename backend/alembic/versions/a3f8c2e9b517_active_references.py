"""add active_references

ADR-SNAPSHOT-001：pinned 版本的 active 引用追踪表。initial_schema 后新增，PG
serving 路径（alembic 管 schema）由此迁移建表，与 schema.py metadata 完全一致
（含 REVIEW-D 的复合 PK tenant,kind,resource_id,version,ref_type,ref_id）。

Revision ID: a3f8c2e9b517
Revises: 7edbf5dfb136
Create Date: 2026-08-27 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a3f8c2e9b517'
down_revision = '7edbf5dfb136'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('active_references',
    sa.Column('tenant_id', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('resource_id', sa.String(length=255), nullable=False),
    sa.Column('version', sa.String(length=64), nullable=False),
    sa.Column('ref_type', sa.String(length=32), nullable=False),
    sa.Column('ref_id', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('tenant_id', 'kind', 'resource_id', 'version', 'ref_type', 'ref_id')
    )
    op.create_index('idx_active_reference_scope', 'active_references', ['tenant_id', 'kind', 'resource_id', 'version', 'ref_type'], unique=False)
    op.create_index('idx_active_reference_tenant_created', 'active_references', ['tenant_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_active_reference_tenant_created', table_name='active_references')
    op.drop_index('idx_active_reference_scope', table_name='active_references')
    op.drop_table('active_references')
