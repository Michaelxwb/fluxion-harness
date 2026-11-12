"""Store standalone Task audits and complete request paths."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("egress_audit", "run_id", schema="runtime", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("egress_audit", "operation", schema="runtime",
                    existing_type=sa.String(64), type_=sa.Text())
    op.execute(sa.text("UPDATE runtime.egress_audit SET operation = COALESCE(method, 'HTTP'), "
                       "update_time = now() WHERE operation = '__unmatched__'"))


def downgrade() -> None:
    # Refuse a lossy downgrade when Task-only rows or long route templates exist.
    op.execute(sa.text("DO $$ BEGIN IF EXISTS (SELECT 1 FROM runtime.egress_audit "
                       "WHERE length(operation) > 64) THEN RAISE EXCEPTION "
                       "'long egress route templates prevent downgrade'; END IF; END $$"))
    op.alter_column("egress_audit", "operation", schema="runtime",
                    existing_type=sa.Text(), type_=sa.String(64))
    op.alter_column("egress_audit", "run_id", schema="runtime", existing_type=sa.Uuid(), nullable=False)
