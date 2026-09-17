from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS control")
    op.execute("CREATE SCHEMA IF NOT EXISTS runtime")
    op.execute("CREATE SCHEMA IF NOT EXISTS task")


def downgrade() -> None:
    pass
