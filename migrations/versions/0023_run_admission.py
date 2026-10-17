"""Global admission policy projection, per-scope counters, and Run queue fields.

0023 建 runtime 侧全局准入结构与 Run 排队投影（design §3.3）：

- `run_admission_policy`：Console capacity 设置在 Runtime 的 PG 阈值投影，每租户恰一行
  （完整唯一、不可软删）——`source_settings_revision` 只能原地单调前进，旧实例不能用旧
  阈值覆盖；阈值边界与配置契约（§3.7）一致；
- `run_admission_counter`：事务内准入计数（USER/TENANT 两个 scope，完整唯一且非负）。
  Run 为权威，此表只承载计数，不形成另一套执行状态；
- `run_record` 排队投影列：`queue_stage`（仅 WAITING_RUNTIME/WAITING_MODEL 或 null）、
  `queue_reason`、`queued_at`、`queue_deadline_at`、`admission_released_at`，以及只覆盖
  全部非终态等待行的 deadline partial index。终态与准入计数释放同事务由应用写入。

回退仅在"无等待中的排队投影且无准入投影行"时允许，否则
`RUN_ADMISSION_DOWNGRADE_REQUIRES_DRAIN`：先排空等待 Run，并确认计数按受控维护路径由
Run 权威重建，避免结构回退静默丢掉在飞准入状态。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

QUEUE_COLUMNS = ("admission_released_at", "queue_deadline_at", "queued_at", "queue_reason", "queue_stage")
WAITING_RUN = (
    "queue_stage IS NOT NULL AND status IN ('CREATED','RUNNING','WAITING_TOOL','WAITING_INPUT')"
)


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _create_policy() -> None:
    op.create_table(
        "run_admission_policy",
        *_standard(),
        sa.Column("source_settings_revision", sa.BigInteger(), nullable=False),
        sa.Column("max_open_runs_per_tenant", sa.Integer(), nullable=False),
        sa.Column("max_open_runs_per_user", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_run_admission_policy_tenant"),
        sa.CheckConstraint("source_settings_revision >= 0", name="ck_run_admission_policy_source_settings_revision"),
        sa.CheckConstraint("max_open_runs_per_tenant BETWEEN 2 AND 100000", name="ck_run_admission_policy_tenant_limit"),
        sa.CheckConstraint("max_open_runs_per_user BETWEEN 2 AND 100", name="ck_run_admission_policy_user_limit"),
        sa.CheckConstraint(
            "max_open_runs_per_user <= max_open_runs_per_tenant",
            name="ck_run_admission_policy_user_within_tenant",
        ),
        sa.CheckConstraint("is_deleted = false", name="ck_run_admission_policy_not_deleted"),
        schema="runtime",
    )


def _create_counter() -> None:
    op.create_table(
        "run_admission_counter",
        *_standard(),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("open_run_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("tenant_id", "scope_type", "scope_key", name="uq_run_admission_counter_scope"),
        sa.CheckConstraint("scope_type IN ('USER','TENANT')", name="ck_run_admission_counter_scope_type"),
        sa.CheckConstraint("open_run_count >= 0", name="ck_run_admission_counter_open_run_count"),
        sa.CheckConstraint("revision >= 1", name="ck_run_admission_counter_revision"),
        schema="runtime",
    )


def _extend_run_record() -> None:
    for name, type_ in (
        ("queue_stage", sa.String(24)),
        ("queue_reason", sa.String(64)),
        ("queued_at", sa.DateTime(timezone=True)),
        ("queue_deadline_at", sa.DateTime(timezone=True)),
        ("admission_released_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("run_record", sa.Column(name, type_), schema="runtime")
    op.create_check_constraint(
        "ck_run_record_queue_stage",
        "run_record",
        "queue_stage IS NULL OR queue_stage IN ('WAITING_RUNTIME','WAITING_MODEL')",
        schema="runtime",
    )
    op.create_index(
        "ix_run_record_queue_deadline",
        "run_record",
        ["queue_deadline_at"],
        schema="runtime",
        postgresql_where=sa.text(f"{WAITING_RUN} AND is_deleted = false"),
    )


def upgrade() -> None:
    _create_policy()
    _create_counter()
    _extend_run_record()


def downgrade() -> None:
    bind = op.get_bind()
    waiting = bind.execute(
        sa.text(f"SELECT count(*) FROM runtime.run_record WHERE {WAITING_RUN}")
    ).scalar_one()
    projected = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM runtime.run_admission_policy)"
        " + (SELECT count(*) FROM runtime.run_admission_counter)"
    )).scalar_one()
    if waiting or projected:
        raise RuntimeError(
            "RUN_ADMISSION_DOWNGRADE_REQUIRES_DRAIN: 0023 的排队投影与准入计数将被结构回退删除"
            f"（等待中的 Run {waiting} 个、准入投影行 {projected} 行）；先排空等待 Run，"
            "确认计数由 Run 权威按受控维护路径重建后再回退"
        )
    op.drop_index("ix_run_record_queue_deadline", table_name="run_record", schema="runtime")
    op.drop_constraint("ck_run_record_queue_stage", "run_record", type_="check", schema="runtime")
    for name in QUEUE_COLUMNS:
        op.drop_column("run_record", name, schema="runtime")
    op.drop_table("run_admission_counter", schema="runtime")
    op.drop_table("run_admission_policy", schema="runtime")
