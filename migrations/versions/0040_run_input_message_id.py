"""runtime.run_record 增加 input_message_id：终态投递按原入站消息收尾回复流。"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_record", sa.Column("input_message_id", sa.String(256)), schema="runtime")


def downgrade() -> None:
    op.drop_column("run_record", "input_message_id", schema="runtime")
