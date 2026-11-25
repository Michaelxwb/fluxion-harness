"""Allow one task delivery key to fan out to multiple bound routes."""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

INDEX = "uq_task_delivery_key"
SCHEMA = "task"
TABLE = "task_delivery"


def upgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE, schema=SCHEMA)
    op.create_index(
        INDEX,
        TABLE,
        ["tenant_id", "delivery_key", "delivery_route_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE, schema=SCHEMA)
    op.create_index(
        INDEX,
        TABLE,
        ["tenant_id", "delivery_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
