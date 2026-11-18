"""Schedule payload model: job = schedule + payload + session + delivery + policy.

The old `execution_spec` column mixed three half-modes into one user-writable shape.
From this revision the authoritative config is `payload_kind` + `payload_json`, with
`mode` kept only as an internally derived column for the scheduler/snapshot paths.
`session_target` and `tools_policy_json` freeze the context and tool surface separately
from delivery; `delivery_intent` / `delivery_route_id` keep their existing meaning
(NONE or PUSH; Web pull is not a delivery intent).

Development data is migrated in place: SKILL -> SKILL_PLAN, AGENTIC -> AGENT_TURN,
REMINDER -> REMINDER. There is no dual read: the old column is dropped here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

SCHEMA = "task"

_AGENTIC_TOOLS = (
    "CASE"
    " WHEN jsonb_typeof(execution_spec->'tool_allowlist') = 'array'"
    "  AND jsonb_array_length(execution_spec->'tool_allowlist') > 0"
    " THEN ("
    "  SELECT coalesce(jsonb_agg("
    "   CASE WHEN entry->>'source' = 'MCP'"
    "    THEN 'MCP:' || coalesce(entry->>'server_key', '') || ':' || coalesce(entry->>'tool_name', '')"
    "    ELSE 'SKILL:' || coalesce(entry->>'skill_id', '') || ':' || coalesce(entry->>'tool_name', '')"
    "   END"
    "  ), '[]'::jsonb)"
    "  FROM jsonb_array_elements(execution_spec->'tool_allowlist') AS entry"
    " )"
    " ELSE '[\"*\"]'::jsonb"
    " END"
)


def upgrade() -> None:
    op.add_column(
        "task_schedule_revision",
        sa.Column(
            "payload_kind", sa.String(16), nullable=False, server_default=sa.text("'REMINDER'")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "task_schedule_revision",
        sa.Column(
            "payload_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "task_schedule_revision",
        sa.Column(
            "session_target",
            sa.String(128),
            nullable=False,
            server_default=sa.text("'isolated'"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "task_schedule_revision",
        sa.Column(
            "tools_policy_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "task_schedule",
        sa.Column("last_result_json", JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "task_schedule",
        sa.Column(
            "scratch_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "task_schedule",
        sa.Column(
            "scratch_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )

    op.execute(
        f"""
        UPDATE task.task_schedule_revision
        SET payload_kind = CASE mode
                WHEN 'SKILL' THEN 'SKILL_PLAN'
                WHEN 'AGENTIC' THEN 'AGENT_TURN'
                ELSE 'REMINDER'
            END,
            payload_json = CASE mode
                WHEN 'SKILL' THEN jsonb_build_object(
                    'kind', 'SKILL_PLAN',
                    'skill_id', execution_spec->>'skill_id',
                    'input', coalesce(execution_spec->'input_template', '{{}}'::jsonb),
                    'argv', '[]'::jsonb,
                    'window', jsonb_build_object('kind', 'DAY', 'offset', 0)
                )
                WHEN 'AGENTIC' THEN jsonb_build_object(
                    'kind', 'AGENT_TURN',
                    'prompt', coalesce(execution_spec->>'goal', ''),
                    'model_ref', NULL,
                    'tools_allow', {_AGENTIC_TOOLS},
                    'budget_ref', 'platform'
                )
                ELSE jsonb_build_object(
                    'kind', 'REMINDER',
                    'text', coalesce(execution_spec->>'reminder_text', '')
                )
            END,
            session_target = 'isolated',
            tools_policy_json = CASE mode
                WHEN 'AGENTIC' THEN jsonb_build_object('tools_allow', {_AGENTIC_TOOLS})
                ELSE jsonb_build_object('tools_allow', '[]'::jsonb)
            END
        """
    )
    op.drop_column("task_schedule_revision", "execution_spec", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "task_schedule_revision",
        sa.Column(
            "execution_spec",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE task.task_schedule_revision
        SET execution_spec = CASE payload_kind
                WHEN 'SKILL_PLAN' THEN jsonb_build_object(
                    'mode', 'SKILL',
                    'skill_id', payload_json->>'skill_id',
                    'input_template', coalesce(payload_json->'input', '{}'::jsonb)
                )
                WHEN 'AGENT_TURN' THEN jsonb_build_object(
                    'mode', 'AGENTIC',
                    'goal', payload_json->>'prompt',
                    'tool_allowlist', '[]'::jsonb,
                    'output_contract', '{}'::jsonb,
                    'budget', jsonb_build_object(
                        'max_steps', 1, 'max_tool_calls', 1, 'max_tokens', 0
                    )
                )
                ELSE jsonb_build_object(
                    'mode', 'REMINDER',
                    'reminder_text', payload_json->>'text'
                )
            END
        """
    )
    op.drop_column("task_schedule", "scratch_revision", schema=SCHEMA)
    op.drop_column("task_schedule", "scratch_json", schema=SCHEMA)
    op.drop_column("task_schedule", "last_result_json", schema=SCHEMA)
    op.drop_column("task_schedule_revision", "tools_policy_json", schema=SCHEMA)
    op.drop_column("task_schedule_revision", "session_target", schema=SCHEMA)
    op.drop_column("task_schedule_revision", "payload_json", schema=SCHEMA)
    op.drop_column("task_schedule_revision", "payload_kind", schema=SCHEMA)
