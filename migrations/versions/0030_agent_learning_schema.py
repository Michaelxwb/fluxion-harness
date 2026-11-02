"""Agent 自进化数据模型：经验状态、Golden Set 与回放报告。

- `runtime.user_memory` 增加 activation_state / task_signature / trigger_text / evidence_json，
  扩展 category 约束并增加 ACTIVE 集合 partial index；既有行默认 ACTIVE，行为不变。
- `runtime.run_record` 增加 task_family（Run 完成时写入）与按族查询索引。
- 新建 `runtime.eval_case`（Golden Set 用例，含 item_count）与 `runtime.eval_report`
  （两臂回放报告，append-only，按基线/经验版本 partial unique）。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_OLD_CATEGORIES = "('PREFERENCE','WORK_STYLE','EXPLICIT','TASK_STATE')"
_NEW_CATEGORIES = (
    "('PREFERENCE','WORK_STYLE','EXPLICIT','TASK_STATE',"
    "'PROCEDURE','FAILURE_PATTERN','VERIFICATION')"
)


def upgrade() -> None:
    op.add_column(
        "user_memory",
        sa.Column(
            "activation_state", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")
        ),
        schema="runtime",
    )
    op.add_column(
        "user_memory", sa.Column("task_signature", sa.String(64)), schema="runtime"
    )
    op.add_column(
        "user_memory", sa.Column("trigger_text", sa.String(512)), schema="runtime"
    )
    op.add_column(
        "user_memory", sa.Column("evidence_json", JSONB()), schema="runtime"
    )
    op.drop_constraint("ck_user_memory_category", "user_memory", type_="check", schema="runtime")
    op.create_check_constraint(
        "ck_user_memory_category",
        "user_memory",
        f"category IN {_NEW_CATEGORIES}",
        schema="runtime",
    )
    op.create_index(
        "ix_user_memory_active_learning",
        "user_memory",
        ["tenant_id", "user_id", "category", sa.text("update_time DESC")],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text(
            "is_deleted = false AND enabled = true AND activation_state = 'ACTIVE'"
        ),
    )

    op.add_column("run_record", sa.Column("task_family", sa.String(64)), schema="runtime")
    op.create_index(
        "ix_run_record_task_family",
        "run_record",
        ["tenant_id", "task_family", sa.text("end_time DESC")],
        unique=False,
        schema="runtime",
    )

    op.create_table(
        "eval_case",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("task_family", sa.String(64), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("input_ref_json", JSONB(), nullable=False),
        sa.Column("criteria_json", JSONB(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["source_run_id"], ["runtime.run_record.id"], name="fk_eval_case_source_run"),
        sa.UniqueConstraint(
            "tenant_id", "task_family", "source_run_id", "version", name="uq_eval_case_source_version"
        ),
        sa.CheckConstraint("item_count >= 1", name="ck_eval_case_item_count"),
        schema="runtime",
    )
    op.create_index(
        "ix_eval_case_family_time",
        "eval_case",
        ["tenant_id", "task_family", sa.text("create_time DESC")],
        unique=False,
        schema="runtime",
    )

    op.create_table(
        "eval_report",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("task_family", sa.String(64), nullable=False),
        sa.Column("experience_key", sa.String(64)),
        sa.Column("base_config_hash", sa.String(128), nullable=False),
        sa.Column("metrics_json", JSONB(), nullable=False),
        sa.Column("decision_json", JSONB(), nullable=False),
        sa.Column("report_json", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        schema="runtime",
    )
    op.create_index(
        "uq_eval_report_baseline_version",
        "eval_report",
        ["tenant_id", "task_family", "base_config_hash", "version"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("experience_key IS NULL"),
    )
    op.create_index(
        "uq_eval_report_experience_version",
        "eval_report",
        ["tenant_id", "task_family", "experience_key", "base_config_hash", "version"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("experience_key IS NOT NULL"),
    )
    op.create_index(
        "ix_eval_report_family_time",
        "eval_report",
        ["tenant_id", "task_family", sa.text("create_time DESC")],
        unique=False,
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_index("ix_eval_report_family_time", table_name="eval_report", schema="runtime")
    op.drop_index("uq_eval_report_experience_version", table_name="eval_report", schema="runtime")
    op.drop_index("uq_eval_report_baseline_version", table_name="eval_report", schema="runtime")
    op.drop_table("eval_report", schema="runtime")

    op.drop_index("ix_eval_case_family_time", table_name="eval_case", schema="runtime")
    op.drop_table("eval_case", schema="runtime")

    op.drop_index("ix_run_record_task_family", table_name="run_record", schema="runtime")
    op.drop_column("run_record", "task_family", schema="runtime")

    op.drop_index("ix_user_memory_active_learning", table_name="user_memory", schema="runtime")
    op.drop_constraint("ck_user_memory_category", "user_memory", type_="check", schema="runtime")
    op.create_check_constraint(
        "ck_user_memory_category",
        "user_memory",
        f"category IN {_OLD_CATEGORIES}",
        schema="runtime",
    )
    op.drop_column("user_memory", "evidence_json", schema="runtime")
    op.drop_column("user_memory", "trigger_text", schema="runtime")
    op.drop_column("user_memory", "task_signature", schema="runtime")
    op.drop_column("user_memory", "activation_state", schema="runtime")
