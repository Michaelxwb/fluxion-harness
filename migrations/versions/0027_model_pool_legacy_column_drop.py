"""Drop the legacy execution columns from `model_definition` (升级链末段 revision).

0021 建 `model_credential` / `model_deployment` / `model_quota_group` 并把存量 Key 迁进物理
凭据；本 revision 在消费方（逻辑模型 API、批量测试、解析入口、API-09）重写完成后，物理删除
`model_definition.base_url` / `api_key`，让"物理凭据是唯一执行入口"成为库层事实。

升级门禁（改表之前）：仍以旧列 `api_key` 为执行入口的模型必须已有**可用成员**
（未删除且启用的成员 + 同条件凭据，即 0021 迁移清单完成态）。否则删列会把唯一执行入口
静默丢掉，报 `MODEL_POOL_LEGACY_KEY_NOT_MIGRATED` 整链回滚，修完清单重跑。只带 `base_url`
（无明文 Key）的存量行不受门禁阻断：它们本来就没有可用的认证值。

回退会重新加回**空**的 `base_url NOT NULL` / `api_key` 列（NOT NULL 需先给默认值），
既恢复不了被删的存量端点，也让运行中的挂载把空连接当执行入口——一律拒绝，
`MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP`：需要恢复维护窗口备份，而不是靠结构回退。
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

#: 旧列仍是唯一执行入口的模型数（有明文 Key，但没有任何"成员 + 凭据"都可用的一对）
_UNMIGRATED_KEY_SQL = sa.text(
    "SELECT count(*) FROM control.model_definition AS d "
    "WHERE d.api_key IS NOT NULL AND d.api_key <> '' AND NOT EXISTS ("
    " SELECT 1 FROM control.model_deployment AS m"
    " JOIN control.model_credential AS c"
    " ON c.id = m.credential_id AND c.tenant_id = m.tenant_id"
    " WHERE m.tenant_id = d.tenant_id AND m.model_id = d.id"
    " AND m.is_deleted = false AND m.enabled = true"
    " AND c.is_deleted = false AND c.enabled = true)"
)


def upgrade() -> None:
    unmigrated = int(op.get_bind().execute(_UNMIGRATED_KEY_SQL).scalar_one())
    if unmigrated:
        raise RuntimeError(
            f"MODEL_POOL_LEGACY_KEY_NOT_MIGRATED: {unmigrated} 个模型仍以旧列 api_key 为唯一执行"
            "入口（没有可用成员凭据）。删列会丢掉唯一的认证值：先把这些模型迁成"
            "`model_credential` + `model_deployment`（见 docs/model-pool-control-migration.md），"
            "再重跑本 revision。"
        )
    op.drop_column("model_definition", "api_key", schema="control")
    op.drop_column("model_definition", "base_url", schema="control")


def downgrade() -> None:
    raise RuntimeError(
        "MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP: 0027 已物理删除 `model_definition.base_url` / "
        "`api_key`，回退只能补回空列（NOT NULL 的 base_url 连语义都无法恢复），旧执行入口"
        "不会回来；需要恢复维护窗口备份"
    )
