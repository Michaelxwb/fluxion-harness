"""Rename the MSS adapter key without changing platform or credential identities."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def _rename(source: str, target: str) -> None:
    op.execute(
        sa.text(
            "UPDATE control.project_platform SET adapter_key = :target, update_time = now() "
            "WHERE adapter_key = :source"
        ).bindparams(source=source, target=target)
    )


def upgrade() -> None:
    _rename("mss-hmac-session", "mss")


def downgrade() -> None:
    _rename("mss", "mss-hmac-session")
