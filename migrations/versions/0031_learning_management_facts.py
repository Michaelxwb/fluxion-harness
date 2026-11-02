"""经验管理事实修正：权威、回放报告用户归属与查询索引。

- `runtime.user_memory.authority`：USER_EXPLICIT / ADMIN_EDIT / AGENT_INFERRED；按既有 author 回填。
- `runtime.eval_report.experience_user_id`：候选臂报告绑定经验所属用户；基线报告保持 NULL。
- 候选报告唯一键加入用户；新增经验维度时间索引。
- 历史候选报告不回填用户（无法确证归属），页面上按「归属未知」展示。
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_memory",
        sa.Column(
            "authority", sa.String(16), nullable=False, server_default=sa.text("'AGENT_INFERRED'")
        ),
        schema="runtime",
    )
    op.create_check_constraint(
        "ck_user_memory_authority",
        "user_memory",
        "authority IN ('USER_EXPLICIT','ADMIN_EDIT','AGENT_INFERRED')",
        schema="runtime",
    )
    op.execute(
        "UPDATE runtime.user_memory"
        " SET authority = CASE author"
        "   WHEN 'USER' THEN 'USER_EXPLICIT'"
        "   WHEN 'ADMIN' THEN 'ADMIN_EDIT'"
        "   ELSE 'AGENT_INFERRED' END"
    )
    op.add_column(
        "eval_report",
        sa.Column("experience_user_id", sa.Uuid(), nullable=True),
        schema="runtime",
    )
    op.drop_index(
        "uq_eval_report_experience_version",
        table_name="eval_report",
        schema="runtime",
    )
    op.create_index(
        "uq_eval_report_experience_version",
        "eval_report",
        [
            "tenant_id",
            "task_family",
            "experience_key",
            "experience_user_id",
            "base_config_hash",
            "version",
        ],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("experience_key IS NOT NULL"),
    )
    op.create_index(
        "ix_eval_report_experience_time",
        "eval_report",
        ["tenant_id", "experience_key", "experience_user_id", sa.text("create_time DESC")],
        unique=False,
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_index("ix_eval_report_experience_time", table_name="eval_report", schema="runtime")
    op.drop_index(
        "uq_eval_report_experience_version",
        table_name="eval_report",
        schema="runtime",
    )
    op.create_index(
        "uq_eval_report_experience_version",
        "eval_report",
        ["tenant_id", "task_family", "experience_key", "base_config_hash", "version"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("experience_key IS NOT NULL"),
    )
    op.drop_column("eval_report", "experience_user_id", schema="runtime")
    op.drop_constraint("ck_user_memory_authority", "user_memory", type_="check", schema="runtime")
    op.drop_column("user_memory", "authority", schema="runtime")
