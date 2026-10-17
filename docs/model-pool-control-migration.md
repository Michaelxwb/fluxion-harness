# 模型池迁移清单（0021）运行手册

`0021_model_pool_control` 把存量 `control.model_definition` 的**执行信息**（`base_url` +
`api_key`）拆成物理凭据与模型成员。旧表**不可能**推导出供方标识、
Key 名与真实预算，因此维护窗口必须提供一份**无密钥迁移清单**。

旧执行列的物理删除**不在 0021**：消费方（模型配置、探针、内部凭据解析）重写完成后，
由升级链末段的删列 revision 执行——维护窗口仍是一次 `upgrade head` 到完整 head，
生产上不会停留在"两处并存"的中间形态（见 §5）。

- 修订：0021（head 从 0020 前进一位，单次升级到完整 head，不引入运行兼容层或双写）
- 代码：`migrations/versions/0021_model_pool_control.py`、`migrations/model_pool_control_data.py`
- 相关 Spec：`harness-data`（RULE-data-001）、`harness-secret`、`harness-model`、`harness-snapshot`

## 1. 维护窗口步骤

1. **记录基线**：`alembic current` 的 revision、最近一次备份时间、当前四单元版本。
2. **停新受理并排空**：停止 Gateway 新受理/入口连接，等待 Run、Task 与记忆提炼 job 全部到终态。
   迁移自带排空门禁（`MODEL_POOL_MIGRATION_NOT_DRAINED`），未排空时不会改动任何结构。
3. **写清单**：把 ini 与清单放在同一目录（相对路径按 ini 所在目录解析），ini 增加一行：

   ```ini
   [alembic]
   sqlalchemy.url = postgresql+asyncpg://user:pass@host:5432/muad
   model_pool_migration_manifest = model-pool-manifest.json
   ```

4. **执行迁移**：`uv run python migrations/db_migrate.py -c <ini> upgrade head`
   （维护窗口一次升到完整 head）。
5. **验证**：结构 parity（ORM 与实际库）、受控探针（`POST /api/v1/model-credentials/test`）。
6. **发布四单元**，再恢复受理。

**空库不需要清单**：没有任何存量模型时迁移直接建表。库里有模型却缺清单会以
`MODEL_POOL_MIGRATION_MANIFEST_MISSING` 拒绝升级。

## 2. 清单格式（schema 1）

```json
{
  "schema": 1,
  "models": {
    "0f2c9d18-6a3b-4c1e-9a44-6b0f0e5d1c22": {
      "provider_key": "DeepSeek",
      "credential": {
        "key": "deepseek-main",
        "max_inflight": 8,
        "quota_group": "deepseek-prod"
      }
    },
    "77bd1c4e-2b77-4ad2-8c66-1f9c2f7a0d31": {
      "provider_key": "openai",
      "credential": null
    }
  },
  "quota_groups": {
    "acme": {
      "deepseek-prod": {"max_inflight": 16, "rpm": 600, "tpm": 2000000}
    }
  }
}
```

| 字段 | 含义 |
|---|---|
| `models."<model_definition.id>"` | 存量模型逐行声明；**每个**非空模型都要有一条 |
| `provider_key` | 供方标识；Owner 规范化（去空白 + 小写）后写库，成员凭据必须与之一致 |
| `credential.key` | 物理 Key 的稳定名（同租户内唯一）；轮换更新同一行，不新增身份 |
| `credential.max_inflight` | 该 Key 在飞调用上限（> 0，必填） |
| `credential.quota_group` | 共享账号额度组的 key；缺省/`null` = 该 Key 独占自己的额度 |
| `credential: null` | 该模型**没有**可用密钥（旧行 `api_key` 必须为空）；只写供方，不建成员 |
| `quota_groups."<tenant>"` | 该租户声明的额度组；`rpm`/`tpm` 可为 `null`（该窗口不设上限） |

**清单里不放密钥**：`api_key`（或任何未知键）出现即 `MODEL_POOL_MIGRATION_INVALID`——密钥只从
旧表读取，防止明文进版本库。清单解析错误不回显任何值。

## 3. 去重与冲突

同一物理 Key 的稳定身份 = `(tenant_id, provider_key, sha256(api_key))`：

- 多个逻辑模型引用同一把物理 Key ⇒ **合并成一行凭据 + 多个成员**（不按模型拆分计量）。
- 同一把 Key 被声明成不同的 `key` 名、容量、额度组或旧 `base_url` ⇒
  `MODEL_POOL_MIGRATION_CONFLICT`（无法判断哪个是权威，整链回滚，人工改清单）。
- 两个不同的物理 Key 用了同一个 `credential.key` ⇒ 同样 `CONFLICT`（会撞 active unique
  `(tenant_id, key)`）。

## 4. 错误码

| 错误码 | 触发条件 | 处理 |
|---|---|---|
| `MODEL_POOL_MIGRATION_NOT_DRAINED` | 仍有非终态 Run / Task / 提炼 job | 等排空或显式停掉后重跑 |
| `MODEL_POOL_MIGRATION_MANIFEST_MISSING` | 有存量模型但 ini 未指定清单 | 补清单 |
| `MODEL_POOL_MIGRATION_MANIFEST_UNREADABLE` | 清单文件不存在 / 不是合法 JSON | 修路径或 JSON |
| `MODEL_POOL_MIGRATION_UNSUPPORTED_SCHEMA` | `schema` ≠ 1 | 用对应版本的清单格式 |
| `MODEL_POOL_MIGRATION_INVALID` | 字段缺失/类型错/未知键、容量非正整数、额度组未声明、有 Key 却声明 `null`（或反之） | 按 §2 修正 |
| `MODEL_POOL_MIGRATION_UNMAPPED` | 库里有模型没在清单里 | 补齐逐行声明 |
| `MODEL_POOL_MIGRATION_UNKNOWN_MODEL` | 清单里有库中不存在的模型 id | 删掉或改正 id |
| `MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP` | 任何降级尝试 | 见 §6 |

上述任一错误都发生在**改动结构之前**：库停在 0020，`base_url`/`api_key` 原样保留，可以修清单后重跑。

## 5. 迁移后的字段变化

- `control.model_definition`：保留 `key/name/protocol/model_id/params_json/revision/enabled`，
  新增 `provider_key`（NOT NULL，默认空串 = 尚未声明供方）；`base_url`/`api_key` 在 0021
  **原值保留**（迁移只写 `model_credential`，不改旧列），由升级链末段的删列 revision 移除——
  届时执行入口才真正只剩 `model_credential`。存量行的 `provider_key` 由清单显式声明，
  默认空串只服务尚未重写的旧建模型入口（成员绑定要求供方一致，见 API-01）。
- 新增 `control.model_quota_group`、`control.model_credential`、`control.model_deployment`。
- 跨租户归属由**复合 FK**（`(tenant_id, model_id)`、`(tenant_id, credential_id)`、
  `(tenant_id, quota_group_id)`）在数据库层拒绝，不靠应用层自证。
- 凭据行 `is_deleted` 与模型一致语义：模型被软删时其成员的 `enabled` 置 `false` 且成员一并软删，
  但**共享的凭据行保留**（其他模型还在用，且历史计量不重置）。

## 6. 回退

0021 不可降级：它已按物理 Key 去重并建立了成员归属，回退映像无法还原"哪把 Key 属于哪个模型"。
需要回退时恢复维护窗口备份（`MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP` 会拒绝直接 `downgrade`）。
新写入数据的保留策略在回退前必须确认——不能假设回滚旧镜像即可降级 schema。
