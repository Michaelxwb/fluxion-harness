"""History paging index for canonical events (conversation + seq, history rows only)."""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

INDEX = "ix_canonical_event_history"
EVENT_TYPES = (
    "USER_MESSAGE",
    "ASSISTANT_MESSAGE",
    "MEMORY_CONFIRMATION_RESOLVED",
    "TOOL_CALL",
    "ASSISTANT_TURN",
    "TOOL_TASK_ACCEPTED",
    "BACKGROUND_RESULT",
    "TOOL_SUBMISSION_FAILED",
)


def upgrade() -> None:
    op.create_index(
        INDEX,
        "canonical_event",
        ["conversation_id", "seq"],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text(
            "is_deleted = false AND event_type IN ("
            + ", ".join(f"'{event_type}'" for event_type in EVENT_TYPES)
            + ")"
        ),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="canonical_event", schema="runtime")
