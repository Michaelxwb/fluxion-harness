"""Runtime model capacity identities, dispatch counters, and call leases.

0022 建 runtime 侧三张容量表（design §3.3）：

- `model_capacity`：同一实际 Key（KEY，用内部稳定 key_resource_id）/额度组在所有池中合并
  为一行计量身份；完整唯一（含 tombstone）——轮换或删除重录不重置旧计量，调度计数另表；
- `model_dispatch_state`：按冻结策略版本（policy_hash）分别解释的调度计数，池锁内更新，
  不承载物理额度；
- `model_call_lease`：一次 call_attempt 恰好归属 run/memory_job/probe 之一，冻结本次实际
  Key 身份与凭据 revision；UNKNOWN（远端是否停止未知）保留占用，且 `is_deleted = false`
  使该隔离证据不能被软删除隐藏。

纯 DDL，不读环境变量；回退仅在三表为空时允许，否则
`MODEL_CAPACITY_DOWNGRADE_REQUIRES_BACKUP` —— 在飞占用、UNKNOWN 隔离证据与旧计量
不能被结构回退静默丢掉。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TABLES = ("model_capacity", "model_dispatch_state", "model_call_lease")


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _create_capacity() -> None:
    op.create_table(
        "model_capacity",
        *_standard(),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("capacity_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("inflight", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("reserved_requests", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("tenant_id", "resource_type", "resource_id", name="uq_model_capacity_resource"),
        sa.CheckConstraint("resource_type IN ('KEY','QUOTA_GROUP')", name="ck_model_capacity_resource_type"),
        sa.CheckConstraint("capacity_revision >= 1", name="ck_model_capacity_capacity_revision"),
        sa.CheckConstraint("inflight >= 0", name="ck_model_capacity_inflight"),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_model_capacity_reserved_tokens"),
        sa.CheckConstraint("reserved_requests >= 0", name="ck_model_capacity_reserved_requests"),
        schema="runtime",
    )


def _create_dispatch_state() -> None:
    op.create_table(
        "model_dispatch_state",
        *_standard(),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("policy_hash", sa.String(128), nullable=False),
        sa.Column("dispatch_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("dispatch_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_picked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id", "model_id", "deployment_id", "policy_hash", name="uq_model_dispatch_state_policy"
        ),
        sa.CheckConstraint("dispatch_epoch >= 0", name="ck_model_dispatch_state_dispatch_epoch"),
        sa.CheckConstraint("dispatch_count >= 0", name="ck_model_dispatch_state_dispatch_count"),
        schema="runtime",
    )


def _create_lease() -> None:
    op.create_table(
        "model_call_lease",
        *_standard(),
        sa.Column("call_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runtime.run_record.id")),
        sa.Column("memory_job_id", sa.Uuid(), sa.ForeignKey("runtime.memory_consolidation_job.id")),
        sa.Column("probe_id", sa.Uuid()),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("key_resource_id", sa.Uuid(), nullable=False),
        sa.Column("quota_group_id", sa.Uuid()),
        sa.Column("credential_revision", sa.BigInteger(), nullable=False),
        sa.Column("workload", sa.String(16), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.UniqueConstraint("tenant_id", "call_attempt_id", name="uq_model_call_lease_attempt"),
        sa.CheckConstraint("num_nonnulls(run_id, memory_job_id, probe_id) = 1", name="ck_model_call_lease_owner"),
        sa.CheckConstraint("state IN ('ACTIVE','UNKNOWN','SETTLED')", name="ck_model_call_lease_state"),
        sa.CheckConstraint(
            "workload IN ('INTERACTIVE','BACKGROUND','PROBE')", name="ck_model_call_lease_workload"
        ),
        sa.CheckConstraint("credential_revision >= 1", name="ck_model_call_lease_credential_revision"),
        sa.CheckConstraint("epoch >= 1", name="ck_model_call_lease_epoch"),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_model_call_lease_reserved_tokens"),
        sa.CheckConstraint("is_deleted = false", name="ck_model_call_lease_not_deleted"),
        schema="runtime",
    )
    op.create_index(
        "ix_model_call_lease_active",
        "model_call_lease",
        ["tenant_id", "key_resource_id"],
        schema="runtime",
        postgresql_where=sa.text("state IN ('ACTIVE','UNKNOWN') AND is_deleted = false"),
    )


def upgrade() -> None:
    _create_capacity()
    _create_dispatch_state()
    _create_lease()


def downgrade() -> None:
    bind = op.get_bind()
    populated = sorted(
        table
        for table in TABLES
        if bind.execute(sa.text(f"SELECT count(*) FROM runtime.{table}")).scalar_one() > 0
    )
    if populated:
        raise RuntimeError(
            "MODEL_CAPACITY_DOWNGRADE_REQUIRES_BACKUP: runtime 容量表已有数据"
            f"（{', '.join(populated)}），回退会丢失在飞占用、UNKNOWN 隔离证据与旧计量；"
            "先在维护窗口结算/留档，确认按备份恢复方案执行"
        )
    for table in reversed(TABLES):
        op.drop_table(table, schema="runtime")
