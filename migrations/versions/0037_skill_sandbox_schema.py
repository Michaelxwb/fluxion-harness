"""用户自建 Skill 与 workspace：来源/审批字段、草稿表、workspace 元数据与租约。

- `control.skill` 增加 source/author/owner_user_id；存量行 source=ADMIN。
- `control.skill_artifact` 增加审批与提交者字段；存量行 review_status/source=APPROVED/ADMIN，
  并建立单 Skill 最多一个活跃 PENDING 的 partial unique。
- 新建 `control.skill_draft`（tenant+owner+key 唯一，revision CAS）。
- 新建 `task.workspace` / `task.workspace_lease`（tenant+user+agent 唯一，execution 租约唯一）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _add_skill_columns() -> None:
    op.add_column(
        "skill",
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'ADMIN'")),
        schema="control",
    )
    op.add_column("skill", sa.Column("author", sa.String(128)), schema="control")
    op.add_column("skill", sa.Column("owner_user_id", sa.Uuid()), schema="control")
    op.create_index("ix_skill_tenant_source", "skill", ["tenant_id", "source"], schema="control")
    op.create_index("ix_skill_owner", "skill", ["owner_user_id"], schema="control")


def _add_artifact_columns() -> None:
    op.add_column(
        "skill_artifact",
        sa.Column(
            "review_status", sa.String(16), nullable=False, server_default=sa.text("'APPROVED'")
        ),
        schema="control",
    )
    op.add_column("skill_artifact", sa.Column("review_note", sa.Text()), schema="control")
    op.add_column("skill_artifact", sa.Column("reviewed_by", sa.Uuid()), schema="control")
    op.add_column(
        "skill_artifact",
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        schema="control",
    )
    op.add_column(
        "skill_artifact", sa.Column("submitted_by_user_id", sa.Uuid()), schema="control"
    )
    op.add_column(
        "skill_artifact", sa.Column("submitted_agent_id", sa.Uuid()), schema="control"
    )
    op.add_column(
        "skill_artifact", sa.Column("submitted_channel", sa.String(16)), schema="control"
    )
    op.add_column(
        "skill_artifact",
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'ADMIN'")),
        schema="control",
    )
    op.create_index(
        "ix_skill_artifact_review", "skill_artifact", ["review_status"], schema="control"
    )
    op.create_index(
        "ix_skill_artifact_submitter",
        "skill_artifact",
        ["submitted_by_user_id"],
        schema="control",
    )
    op.create_index(
        "uq_skill_artifact_one_pending",
        "skill_artifact",
        ["skill_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("review_status = 'PENDING' AND is_deleted = false"),
    )


def _create_skill_draft() -> None:
    op.create_table(
        "skill_draft",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "create_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "update_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_submitted_artifact_id", sa.Uuid()),
        sa.Column("last_submitted_revision", sa.BigInteger()),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_size", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        schema="control",
    )
    op.create_index(
        "uq_skill_draft_tenant_owner_key",
        "skill_draft",
        ["tenant_id", "owner_user_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_workspace_tables() -> None:
    op.create_table(
        "workspace",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "create_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "update_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "last_access_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("cleanup_error", sa.Text()),
        sa.CheckConstraint(
            "state IN ('ACTIVE','DELETING','DELETED')", name="ck_workspace_state"
        ),
        schema="task",
    )
    op.create_index(
        "uq_workspace_tenant_user_agent",
        "workspace",
        ["tenant_id", "user_id", "agent_id"],
        unique=True,
        schema="task",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index("ix_workspace_last_access", "workspace", ["last_access_time"], schema="task")
    op.create_index("ix_workspace_state", "workspace", ["state"], schema="task")

    op.create_table(
        "workspace_lease",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "create_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "update_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("task.workspace.id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        schema="task",
    )
    op.create_index(
        "uq_workspace_lease_workspace_execution",
        "workspace_lease",
        ["workspace_id", "execution_id"],
        unique=True,
        schema="task",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_workspace_lease_workspace", "workspace_lease", ["workspace_id"], schema="task"
    )
    op.create_index(
        "ix_workspace_lease_until", "workspace_lease", ["lease_until"], schema="task"
    )


def upgrade() -> None:
    _add_skill_columns()
    _add_artifact_columns()
    _create_skill_draft()
    _create_workspace_tables()


def downgrade() -> None:
    op.drop_index("ix_workspace_lease_until", table_name="workspace_lease", schema="task")
    op.drop_index("ix_workspace_lease_workspace", table_name="workspace_lease", schema="task")
    op.drop_index(
        "uq_workspace_lease_workspace_execution", table_name="workspace_lease", schema="task"
    )
    op.drop_table("workspace_lease", schema="task")
    op.drop_index("ix_workspace_state", table_name="workspace", schema="task")
    op.drop_index("ix_workspace_last_access", table_name="workspace", schema="task")
    op.drop_index("uq_workspace_tenant_user_agent", table_name="workspace", schema="task")
    op.drop_table("workspace", schema="task")
    op.drop_index("uq_skill_draft_tenant_owner_key", table_name="skill_draft", schema="control")
    op.drop_table("skill_draft", schema="control")
    op.drop_index(
        "uq_skill_artifact_one_pending", table_name="skill_artifact", schema="control"
    )
    op.drop_index(
        "ix_skill_artifact_submitter", table_name="skill_artifact", schema="control"
    )
    op.drop_index("ix_skill_artifact_review", table_name="skill_artifact", schema="control")
    for column in (
        "source",
        "submitted_channel",
        "submitted_agent_id",
        "submitted_by_user_id",
        "reviewed_at",
        "reviewed_by",
        "review_note",
        "review_status",
    ):
        op.drop_column("skill_artifact", column, schema="control")
    op.drop_index("ix_skill_owner", table_name="skill", schema="control")
    op.drop_index("ix_skill_tenant_source", table_name="skill", schema="control")
    for column in ("owner_user_id", "author", "source"):
        op.drop_column("skill", column, schema="control")
