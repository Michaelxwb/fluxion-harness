"""Physical model pool: credentials, members and quota groups.

单次升级完成两件事：建 `model_quota_group` / `model_credential` / `model_deployment`；
把存量 `model_definition` 的 Key（按维护窗口清单去重）迁成物理凭据与成员。

旧执行列 `base_url` / `api_key` 本次**保留**（迁移只写 `model_credential`，不改旧列）：
物理删列是同一升级链的末段 revision，等消费方重写完再执行（见
`docs/model-pool-control-migration.md` §5）。维护窗口仍是一次 `upgrade head` 到完整 head。

所有校验发生在改表之前：排空门禁 → 清单解析 → 覆盖/冲突检查。任何一步失败整链回滚，
库停在 0020 且旧列原样保留，可以修清单后重跑。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

from migrations.model_pool_control_data import (
    apply_model_pool,
    load_manifest,
    parse_manifest,
    plan_model_pool,
    preflight_model_pool,
)

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _create_quota_group() -> None:
    op.create_table(
        "model_quota_group",
        *_standard(),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("rpm", sa.BigInteger()),
        sa.Column("tpm", sa.BigInteger()),
        sa.Column("max_inflight", sa.Integer(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_quota_group_tenant_id_id"),
        sa.CheckConstraint("max_inflight > 0", name="ck_model_quota_group_max_inflight"),
        sa.CheckConstraint("rpm IS NULL OR rpm > 0", name="ck_model_quota_group_rpm"),
        sa.CheckConstraint("tpm IS NULL OR tpm > 0", name="ck_model_quota_group_tpm"),
        sa.CheckConstraint("revision >= 1", name="ck_model_quota_group_revision"),
        schema="control",
    )
    op.create_index(
        "uq_model_quota_group_tenant_key",
        "model_quota_group",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_credential() -> None:
    op.create_table(
        "model_credential",
        *_standard(),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("provider_key", sa.String(128), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("api_key_digest", sa.String(64), nullable=False),
        sa.Column("quota_group_id", sa.Uuid()),
        sa.Column("max_inflight", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_credential_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quota_group_id"],
            ["control.model_quota_group.tenant_id", "control.model_quota_group.id"],
            name="fk_model_credential_tenant_quota_group",
        ),
        sa.CheckConstraint("max_inflight > 0", name="ck_model_credential_max_inflight"),
        sa.CheckConstraint("revision >= 1", name="ck_model_credential_revision"),
        sa.CheckConstraint("api_key_digest ~ '^[0-9a-f]{64}$'", name="ck_model_credential_api_key_digest"),
        schema="control",
    )
    op.create_index(
        "uq_model_credential_tenant_key",
        "model_credential",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_model_credential_tenant_provider_digest",
        "model_credential",
        ["tenant_id", "provider_key", "api_key_digest"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_deployment() -> None:
    op.create_table(
        "model_deployment",
        *_standard(),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_deployment_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "model_id"],
            ["control.model_definition.tenant_id", "control.model_definition.id"],
            name="fk_model_deployment_tenant_model_definition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["control.model_credential.tenant_id", "control.model_credential.id"],
            name="fk_model_deployment_tenant_model_credential",
        ),
        sa.CheckConstraint("weight BETWEEN 1 AND 100", name="ck_model_deployment_weight"),
        sa.CheckConstraint("revision >= 1", name="ck_model_deployment_revision"),
        schema="control",
    )
    op.create_index(
        "uq_model_deployment_tenant_model_credential",
        "model_deployment",
        ["tenant_id", "model_id", "credential_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_model_deployment_tenant_model_key",
        "model_deployment",
        ["tenant_id", "model_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    preflight_model_pool(bind)
    document = load_manifest(op.get_context().config)
    plan = plan_model_pool(bind, None if document is None else parse_manifest(document))
    op.add_column(
        "model_definition",
        sa.Column("provider_key", sa.String(128), nullable=False, server_default=sa.text("''")),
        schema="control",
    )
    # 复合 FK 需要目标列上有唯一约束（主键之外）：`model_deployment` 靠它做**数据库层**的
    # 同租户核属，跨租户挂载在约束上失败，而不是靠应用层自证。
    op.create_unique_constraint(
        "uq_model_definition_tenant_id_id", "model_definition", ["tenant_id", "id"], schema="control"
    )
    _create_quota_group()
    _create_credential()
    _create_deployment()
    apply_model_pool(bind, plan)


def downgrade() -> None:
    raise RuntimeError(
        "MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP: 0021 已按物理 Key 去重并建立成员归属，"
        "回退会丢失凭据归属；需要先确认新写入数据的保留策略并恢复维护窗口备份"
    )
