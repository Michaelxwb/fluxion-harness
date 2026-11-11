"""Independent Task claim generation; WAITING continuation does not spend retries."""
import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_execution", sa.Column(
        "lease_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0"),
    ), schema="task")
    op.create_check_constraint("ck_task_execution_lease_epoch", "task_execution",
                               "lease_epoch >= 0", schema="task")


def downgrade() -> None:
    op.drop_constraint("ck_task_execution_lease_epoch", "task_execution", type_="check", schema="task")
    op.drop_column("task_execution", "lease_epoch", schema="task")
