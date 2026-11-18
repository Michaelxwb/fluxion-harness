---
{"version":1,"requirement":"spec-rule-promotion"}
---

# Tasks: spec-rule-promotion

<!--
把 Conventions 中"可被测试/评审判定、可被违反、长期稳定"的强制约束提升为 ## Rules。
只提升高频/高风险 spec 的第一批，保持每条规则 <= 600B；理由、示例和模块细节留在 Conventions。
-->

## TASK-001: 提升架构、产物、IM、Worker、测试与 UI 规则

- **Status**: done
- **Depends**:
- **Source**:
- **Scenarios**:
- **Spec-Refs**: test/harness-test
- **Paths**: .code-flow/specs/arch/**, .code-flow/specs/artifact/**, .code-flow/specs/im/**, .code-flow/specs/worker/**, .code-flow/specs/test/**, .code-flow/specs/ui/**
- **E2E**:
- **Skip-Tests**: true
- **Skip-Reason**: 仅提升规范文本，无运行时代码；由 doctor 与 review 校验

### Tests

```json
[]
```

### Description

从各 spec 的 Conventions 提升第一批可执行规则：

- arch：依赖方向、Gateway/Connector 边界
- artifact：目录截断可见、非 Skill 产物前缀、resolve 越界防护
- im：内部投递受信、入口身份显式、绑定码一次性
- worker：扇出前持有父任务、fan-in 仅 BATCH
- test：耗时归因、两步写有界等待
- ui：列表页 RemoteTable 边界、菜单单一来源

### Checklist

- [x] arch 增加 2 条规则。
- [x] artifact 增加 3 条规则。
- [x] im 增加 3 条规则。
- [x] worker 增加 2 条规则。
- [x] test 增加 2 条规则。
- [x] ui 增加 2 条规则。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "`packages/*` 不依赖任何 app 模块；runtime/worker 不得 import `muad_console_platform`，跨部署单元只走 HTTP 契约；`skill-sdk` 不依赖 runtime/console/worker/connector。机检 `tests/architecture/test_import_direction.py`。",
    "evidence": "Conventions：依赖方向单向"
  },
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "IM Gateway web 角色不持库（不得 import `sqlalchemy`）；渠道 SDK 只允许出现在 Connector 的 `channels/` 适配器；`iter_events()` 是 IM 入站规范化唯一入口，Connector 只发 `im.inbound`/`im.outbound`，不直接调 Runtime。机检 `tests/architecture/test_im_gateway_boundaries.py`。",
    "evidence": "Conventions：IM Gateway / Connector 边界"
  },
  {
    "id": "DELTA-003",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "技能目录按 `MAX_CATALOG_ENTRIES`/`MAX_CATALOG_BYTES` 取前缀，超限整条丢弃并停止；确实丢弃时必须留 warning 与目录截断指标，并在目录尾部追加 `CATALOG_TRUNCATED_NOTE`；未丢弃时不得出现该提示。",
    "evidence": "Conventions：目录有界，且丢了要说出来"
  },
  {
    "id": "DELTA-004",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "非 Skill 产物必须使用 `skills/` 之外的前缀（导出用 `exports/{tenant}/{export_id}/...`，工具大结果用 `tools/{tenant}/{run_id}/{artifact_id}/result.bin`）；孤儿清理只扫 `skills/**`，禁止复用该前缀。",
    "evidence": "Conventions：非 Skill 产物必须用 skills/ 之外的前缀"
  },
  {
    "id": "DELTA-005",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "`NfsArtifactStore.resolve` 必须校验解析路径落在 `root` 内且不等于 `root`，否则抛 `ValueError(invalid storage_key)`；禁止用 `root / storage_key` 直接拼接。",
    "evidence": "Conventions：NfsArtifactStore.resolve 必须做越界防护"
  },
  {
    "id": "DELTA-006",
    "op": "add",
    "target": "im/harness-im",
    "rule": "`/internal/deliveries` 只接受带 `X-Internal-Service` 的内部调用；投递 `tenant_id` 必须等于本网关部署租户；产物必须按 `artifact_id` 经 Runtime `/internal/artifacts/{id}` 在本租户解析，调用方 `storage_key` 只能用于比对，不能直接读共享存储。",
    "evidence": "Conventions：/internal/deliveries 是受信内部端点"
  },
  {
    "id": "DELTA-007",
    "op": "add",
    "target": "im/harness-im",
    "rule": "入口身份必须显式传递 `ChannelContext.entry_id`（= `control.bot_account.id`），随消息、`/new`、`/stop` 全链保留；`bot_id` 只保存平台原值用于连接与路由；禁止用 `uuid.UUID(bot_id)` 或字符串形状推断入口。",
    "evidence": "Conventions：入口身份显式、必填，拒绝字符串形状推断"
  },
  {
    "id": "DELTA-008",
    "op": "add",
    "target": "im/harness-im",
    "rule": "绑定码状态映射固定：`USED`/`REVOKED` → `BIND_CODE_INVALID`，`EXPIRED`/过期 → `BIND_CODE_EXPIRED`；带 `Idempotency-Key` 的绑定请求使用共享幂等表 + `pg_advisory_xact_lock`；`identity_key=(channel,bot_id,external_user_id)` 每租户唯一，冲突返回 `IDENTITY_ALREADY_BOUND` 且不消费绑定码。",
    "evidence": "Conventions：绑定码一次性 + 绑定请求幂等"
  },
  {
    "id": "DELTA-009",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "扇出前必须在父任务行锁内同时确认 `status=RUNNING`、`cancel_requested=false`、`lease_until > now`、`lease_owner` 与本执行一致；已取消或已失约的父任务不得创建可执行 Child（`_require_live_owner`）。",
    "evidence": "Conventions：扇出前必须证明本次执行仍持有父任务"
  },
  {
    "id": "DELTA-010",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "fan-in 只对 BATCH 父执行生效；请求取消的父任务必须由取消路径收尾为 `CANCELLED`，不得被扇入改写成成功或失败；Agentic ASYNC Skill 子 Task 不是 BATCH 子任务，父 Execution 必须继续等待 operation 结果。",
    "evidence": "Conventions：fan-in 只对 BATCH 父执行生效"
  },
  {
    "id": "DELTA-011",
    "op": "add",
    "target": "test/harness-test",
    "rule": "验收耗时优化必须由 `pytest --durations` 归因驱动，分别看 `setup` 与 `call`；禁止按主观猜测优化慢点。",
    "evidence": "Conventions：验收耗时优化必须由 --durations 归因驱动"
  },
  {
    "id": "DELTA-012",
    "op": "add",
    "target": "test/harness-test",
    "rule": "被测组件分两步写共享状态时，测试必须对第二步做有界等待后才断言；不得在第一步事实出现时立即断言第二步。",
    "evidence": "Conventions：两步写不能等到第一步就断言第二步"
  },
  {
    "id": "DELTA-013",
    "op": "add",
    "target": "ui/harness-ui",
    "rule": "以「左上操作 + 右上搜索筛选 + 列表 + 右下分页」为形态的模块列表页必须使用 `RemoteTable`；仪表盘/概览页不受该约束，但区块内不得使用 `RemoteTable` 或 `ModuleToolbar`，并由契约测试反查其确实不含列表构件。",
    "evidence": "Conventions：RemoteTable 只约束列表页"
  },
  {
    "id": "DELTA-014",
    "op": "add",
    "target": "ui/harness-ui",
    "rule": "`/settings` 菜单项只能由 `config/menu.ts` 的 `menuItems` 派生，路由受 `<RequireRole role=ADMIN>` 守卫；`AppLayout` 不得内联第二处导航项数组，`MENU_ICONS` 必须登记对应图标。",
    "evidence": "Conventions：系统设置入口只能来自 config/menu.ts"
  }
]
```

### Log

- [2026-11-18] created
- [2026-11-18T14:06:36.612Z] started tests=4f53cda18c2b

### Verification

- 尚未执行
- 2026-11-18T14:06:36.715Z SKIPPED commands=0/0 executed=0 deduped=0 tests=4f53cda18c2b head=3783dac1e0de dirty=0 reason=仅提升规范文本，无运行时代码；由 doctor 与 review 校验
## TASK-002: 提升密钥与持久化规则

- **Status**: done
- **Depends**:
- **Source**:
- **Scenarios**:
- **Spec-Refs**: test/harness-test
- **Paths**: .code-flow/specs/secret/**, .code-flow/specs/persistence/**
- **E2E**:
- **Skip-Tests**: true
- **Skip-Reason**: 仅提升规范文本，无运行时代码；由 doctor 与 review 校验

### Tests

```json
[]
```

### Description

提升密钥泄漏面与数据库稳定身份/标准列/schema 的强制约束。

### Checklist

- [x] secret 增加 2 条规则。
- [x] persistence 增加 3 条规则。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "secret/harness-secret",
    "rule": "密钥不得进入 CanonicalEvent、`tool_call_audit`、`egress_audit`、`model_invocation_audit`、IM 出站消息、Skill package 及其 `SKILL.md`；验收以 canary 反查的实际表范围为准。",
    "evidence": "Conventions：禁止面比上述更宽"
  },
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "secret/harness-secret",
    "rule": "平台失败原因在离开适配器前必须把本次 `ak`/`sk`/`headers` 的具体值替换为 `***`，再进入 Skill stderr 或工具结果；日志只记 code 与状态，不记原文。",
    "evidence": "Conventions：第三方原文回传前必须擦除本次凭据值"
  },
  {
    "id": "DELTA-003",
    "op": "add",
    "target": "persistence/harness-data",
    "rule": "稳定 key 不因软删除重置：`user_memory(tenant_id,user_id,memory_key)`、`memory_scope(tenant_id,user_id)` 使用完整唯一；历史/receipt/job 的 `is_deleted` 恒为 false；同族新注册表同样使用包含 tombstone 的完整唯一。",
    "evidence": "Conventions：稳定 key 不因软删除而重置"
  },
  {
    "id": "DELTA-004",
    "op": "add",
    "target": "persistence/harness-data",
    "rule": "产品表的 `id`/`is_deleted`/`create_time`/`update_time` 必须继承 `StandardColumnsMixin`；Console、Worker、Runtime 三份 `models/base.py` 与 `NAMING_CONVENTION` 保持同名同内容。",
    "evidence": "Conventions：四列口径的唯一载体是 StandardColumnsMixin"
  },
  {
    "id": "DELTA-005",
    "op": "add",
    "target": "persistence/harness-data",
    "rule": "Schema 只允许 `control`/`runtime`/`task`，不得新建框架私有 schema，也不得放入无 ORM 模型的表。",
    "evidence": "Conventions：Schema 名单固定"
  }
]
```

### Log

- [2026-11-18] created
- [2026-11-18T14:06:36.760Z] started tests=4f53cda18c2b

### Verification

- 尚未执行
- 2026-11-18T14:06:36.833Z SKIPPED commands=0/0 executed=0 deduped=0 tests=4f53cda18c2b head=3783dac1e0de dirty=0 reason=仅提升规范文本，无运行时代码；由 doctor 与 review 校验
