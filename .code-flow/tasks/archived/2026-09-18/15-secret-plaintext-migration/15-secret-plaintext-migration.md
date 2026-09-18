# Tasks: 密钥明文入库专项（移除 SecretRef）

- **Source**: `.code-flow/tasks/2026-09-18/15-secret-plaintext-migration/`（15-secret-plaintext-migration.backend.design.md）
- **Created**: 2026-09-18
- **Updated**: 2026-09-18

## Proposal

按 project-owner 决策移除 SecretRef/SecretProvider 机制：6 张表密钥列改明文，跨表以主键引用，契约与消费侧（Runtime/Gateway/Console）直接读写字段；保留“日志/审计/Snapshot/LLM/API 响应不含密钥”的脱敏边界，并同步规范与文档基线。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | E2E | Browser→API→DB（模型） | TASK-004 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-01\""] |
| S-02 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | integration | Runtime→DB→模型端点 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_secret_consumers.py", "-k", "s02"] |
| S-03 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | integration | Gateway→DB→企业微信 SDK Port | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_secret_consumers.py", "-k", "s03"] |
| S-04 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | integration | Alembic expand→backfill→contract | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_secret_migration.py"] |
| E-01 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | unit | 代码库静态检查 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/test_secret_ref_residue.py"] |
| S-05 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | unit | 文档/规范静态一致性 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/test_secret_policy_docs.py"] |
| E-02 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | integration | 日志/审计捕获 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_secret_consumers.py", "-k", "e02"] |
| E-03 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | integration | backfill CLI | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_secret_migration.py", "-k", "e03"] |
| E-04 | 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景 | integration | Runtime/Gateway | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_secret_consumers.py", "-k", "e04"] |

> 本表覆盖 design 全部 P0 场景（S-01~S-04、E-01~E-04）；不存在缺口。

---

## TASK-001: 规范与文档基线同步

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 15-secret-plaintext-migration.backend.design.md#2.3.1 业务规则与约束, #3.1 技术选型与关键决策
- **Spec-Refs**:
- **Acceptance-Refs**: S-05

### Description
重写受影响规则与文档基线：harness-secret 规则（改为明文入库 + 主键引用 + 脱敏边界）、harness-project-platform 规则（删去“凭据只存 SecretRef”）；更新 docs/00、01、02、03、05、07、08、12 中 Secret Provider/SecretRef 相关章节；更新 04/06/10 设计简报中的凭据行；新增静态残留检查脚本（供 E-01 使用）。

### Checklist
- [x] 重写 harness-secret 规则与 Conventions（明文入库 + 主键引用 + 脱敏边界）（✅/❌：明文入库 + 主键引用；❌ 日志/审计/响应出现密钥）
- [x] 重写 harness-project-platform 规则（凭据明文存凭据表，credential_mode 语义保持）（凭据明文存 DB，credential_mode 语义保持）
- [x] 更新 docs/00、01、02、03、05、07、08、12（Secret 章节/图示/表格改为明文列） 的 Secret 章节与图示
- [x] 更新 04-project-platform、06-mcp-management、10-im-gateway 设计简报凭据行中的凭据/密钥行
- [x] 新增 `scripts/check_secret_ref_residue.py`（当前仍报 03/04 待重写测试，属预期）（扫描 `secret://`、`SecretProvider`、`secret_ref` 残留，排除 legacy/docs 例外清单）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | unit | docs 基线、specs 规则文本 | docs 无 SecretRef 残留；规则为明文政策 | tests/test_secret_policy_docs.py | ["uv", "run", "pytest", "-q", "tests/test_secret_policy_docs.py"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- 说明：本任务为规范/文档/脚本同步，无 Owned 场景；模块级 5 条 required Rule verifier 已全部通过：
  - harness-secret：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`（passed）
  - harness-data / harness-project-platform：`uv run pytest -q tests -k schema_parity`（passed）
  - harness-im：`uv run pytest -q tests/console_channel tests/gateway`（passed）
  - harness-test：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（passed）
- 残留检查：`scripts/check_secret_ref_residue.py` 已落地，当前命中集中在 TASK-003/004 待重写的供应商/网关测试（按计划处理）。
- S-05: verified — automated command passed; run_id=63e3a5c757e04e029da3bcbfc51c584f (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=ff225abd4a8c4599899cb64c3403df52 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-002: 契约、共享包与 expand 迁移

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 15-secret-plaintext-migration.backend.design.md#3.2 数据设计, #3.3 代码改造点
- **Spec-Refs**: harness-data#RULE-data-001, harness-project-platform#RULE-platform-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-04

### Description
改造共享层与 schema：contracts 中 `ResolvedModel.secret_ref→api_key`、`BotSnapshotItem.secret_ref→secret`；删除 platform-sdk `credential/` 模块与导出；`api-kit validate_startup` 移除 SecretProvider 探针参数；6 张表 ORM 增加新列并保留旧列；新增迁移 `0004` expand（加列，不删旧列）；同步 `test_schema_parity`。

### Checklist
- [x] contracts：`ResolvedModel.api_key`、`BotSnapshotItem.secret`（保留旧字段一版做过渡）
- [x] platform-sdk：删除 credential 模块（TASK-003 已执行）
- [x] api-kit：`validate_startup` 移除 `secret_provider` 参数与探针（TASK-003 已执行）
- [x] ORM + 迁移 `0004` expand：6 张表新增明文列（`api_key`/`secret`/`auth_secret`/`credential_json`）
- [x] [S-04][integration] 未留存 RED：迁移为既有 schema 变更补测（未伪造）；验收见 migration 测试
- [x] [S-04] 断言 expand 后新旧列并存、迁移链单头 0004、parity 通过（upgrade 冒烟）
- [x] [RULE-data-001] verifier：执行 `uv run pytest -q tests -k schema_parity` 与迁移冒烟
- [x] [RULE-platform-001] verifier：执行 `uv run pytest -q tests -k schema_parity`（凭据列明文 + 主键引用）
- [x] [RULE-time-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"`（模型时间列不回归）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | Alembic、PostgreSQL | expand 后新旧列并存；parity/升级/回滚通过 | tests/acceptance/test_secret_migration.py | planned | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- 说明：本任务只做 expand（保留旧列与 Provider）；消费侧切换与删除旧列在 TASK-003。
- S-04：`uv run pytest -q tests/acceptance/test_secret_migration.py`（2 passed）— 真实 PostgreSQL：`alembic upgrade head` 后 6 表新列存在且旧列保留、迁移链单头 `0004`；断言位置 `tests/acceptance/test_secret_migration.py:66`、`:75`。
- 契约与双写：`ResolvedModel.api_key`/`BotSnapshotItem.secret` 实施过渡双字段；`resolve_service`/`channel_service` 双写（Green：`tests/console_internal/test_resolve_definition_api.py`、`tests/console_channel/test_channel_bots_api.py` 通过）。
- 回归：`uv run make check` 全绿（594 tests / mypy 186 files）；`tests -k schema_parity` 通过。
- S-04: verified — automated command passed; run_id=978771cea8794faba77eda847f8bb63d (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=be56c39e4dd247d48dc949c75fee6cf6 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-003: 消费侧落地与 backfill/contract 迁移

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 15-secret-plaintext-migration.backend.design.md#3.3 代码改造点, #3.4 迁移与回滚, #3.5 质量实现方案
- **Spec-Refs**: harness-secret#RULE-secret-001, harness-im#RULE-im-001, harness-api#RULE-api-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-02, S-03, E-02, E-03, E-04

### Description
落地消费侧与迁移执行：console `resolve_service`/`channel_service` 输出明文字段；runtime executor 直接用 `ResolvedModel.api_key`（空值 `CREDENTIAL_MISSING`）；gateway wecom adapter 用 Bot 快照明文 Secret；新增 `backfill-secrets` CLI（读旧 `secret_ref` → 环境变量解析 → 写新列，失败清单 + 非零退出 + 幂等）；新增迁移 `0005` contract（删除旧列与过渡字段）；更新脱敏（日志/审计）断言。

### Checklist
- [x] console：resolve/channel 输出明文密钥；审计与日志保持脱敏
- [x] runtime：`resolve_model_api_key` 去 Provider，直接用 `api_key`；空值 `CREDENTIAL_MISSING` 且不发请求
- [x] gateway：wecom adapter 用 Bot 明文 Secret；连接缓存比较同步
- [x] CLI：`backfill-secrets`（幂等、失败清单、非零退出）
- [x] platform-sdk：删除 credential 模块与导出；api-kit `validate_startup` 移除 SecretProvider 探针并更新 foundation 验收测试
- [x] 迁移 `0005` contract：删旧列 + 删过渡契约字段（含 Provider 回退分支）；ORM/parity 同步
- [x] [S-02][integration] 已先写测试并记录 RED（Provider 移除后导入失败 → 实现后 4 passed），按 Runtime→DB→模型端点 真实边界编写验收测试并记录 RED
- [x] [S-02] 断言 请求携带 DB 中的 Key；无 Provider 依赖
- [x] [S-03][integration] 已先写测试并记录 RED，按 Gateway→DB→SDK Port 真实边界编写验收测试并记录 RED
- [x] [S-03] 断言 SDK 使用 DB 中的 Secret
- [x] [S-04][integration] 断言 contract 后旧列消失（TASK-002 扩展实现）
- [x] [E-02][integration] 断言 日志与 `config_audit_log` 无明文
- [x] [E-03][integration] 断言 环境变量缺失时列出不可解析行并非零退出
- [x] [E-04][integration] 断言 密钥为空 → `CREDENTIAL_MISSING`，不发起外部请求
- [x] [RULE-secret-001] verifier：执行 `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py tests/console_platform/test_models_api.py`
- [x] [RULE-im-001] verifier：执行 `uv run pytest -q tests/console_channel tests/gateway`
- [x] [RULE-api-001] verifier：执行 `uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`（api-kit 签名变更后封套不回归）
- [x] [RULE-snapshot-001] verifier：执行 `bash -lc "uv run pytest -q tests/agent_runtime -k \"executor or resolve\""`（Runtime 消费明文 key，不漂移）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Runtime、PostgreSQL、模型端点 | 请求携带 DB Key；无 Provider 依赖 | tests/acceptance/test_secret_consumers.py | planned | verified |
| S-03 | integration | Gateway、PostgreSQL、SDK Port | SDK 使用 DB Secret；无 Provider 依赖 | tests/acceptance/test_secret_consumers.py | planned | verified |
| E-02 | integration | 日志、config_audit_log | 明文不出现在日志与审计 | tests/acceptance/test_secret_consumers.py | planned | verified |
| E-03 | integration | backfill CLI | 环境变量缺失 → 失败清单 + 非零退出 | tests/acceptance/test_secret_migration.py | planned | verified |
| E-04 | integration | Runtime/Gateway | 空密钥 → `CREDENTIAL_MISSING` 且无外部请求 | tests/acceptance/test_secret_consumers.py | planned | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- S-02：`uv run pytest -q tests/acceptance/test_secret_consumers.py -k s02`（1 passed）— `resolve_model_api_key` 直接读 `api_key`（`secret.version="db"`），`muad_platform_sdk` 已无 `SecretProvider`。
- S-03：同文件 `-k s03`（1 passed）— 真实 PostgreSQL 取 Bot `secret`，真实 `WeComAdapter` + SDK Port fake 建连使用该值，无 Provider。
- S-04：`uv run pytest -q tests/acceptance/test_secret_migration.py`（3 passed）— contract 后旧列全部消失、单头 0005。
- E-02：同文件 `-k e02` — `sanitize_audit_payload` 丢弃 `api_key`/`secret`；真实 `config_audit_log` 落库为 `{}`，无明文。
- E-03：同文件 `-k e03` — downgrade 0004 后写入遗留 ref，CLI 在缺环境变量时输出 unresolved 清单并以 exit 1 结束。
- E-04：同文件 `-k e04` — 空 Key → `CREDENTIAL_MISSING`；Bot 无 secret → `ChannelAdapterUnavailable` 且未发起连接。
- 回归：`uv run make check` 全绿（582 tests / mypy 183 files）；`tests/gateway`、`tests/agent_runtime`、`tests/sdk` 全部更新为明文语义。
- S-02: verified — automated command passed; run_id=602706564f924384a6b4a189b33dbabe (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=602706564f924384a6b4a189b33dbabe (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=602706564f924384a6b4a189b33dbabe (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=602706564f924384a6b4a189b33dbabe (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=602706564f924384a6b4a189b33dbabe (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=b8808823929f4f7e97f84fe78bdfc293 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=b8808823929f4f7e97f84fe78bdfc293 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=b8808823929f4f7e97f84fe78bdfc293 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=b8808823929f4f7e97f84fe78bdfc293 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=b8808823929f4f7e97f84fe78bdfc293 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-004: 测试重写与端到端验收

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 15-secret-plaintext-migration.backend.design.md#2.3.2 功能验收场景, #3.5 质量实现方案
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, E-01

### Description
重写受影响的验收与契约测试（foundation startup 去 Provider、gateway/runtime/console 的 `secret://` 用例），新增静态残留检查用例与浏览器 E2E（模型页 API Key 明文落库、响应仅显示“已配置”），并把专项纳入模块级 `--include-e2e` 验收。

### Checklist
- [x] 重写 `tests/acceptance/test_foundation_ops_startup.py`（移除 SecretProvider 探针断言，随 TASK-003 落地）
- [x] 重写 gateway/runtime/console 中依赖 `secret://`/Provider 的测试（随 TASK-003 落地）
- [x] 新增 E-01 静态残留用例（`tests/test_secret_ref_residue.py`，例外清单已注释说明）
- [x] [S-01][E2E] 未执行：模型页 UI 属 03-model-management（尚未实现），本专项以 integration 覆盖同等边界（DB 明文 + 不回显），E2E 记 e2e_deferred
- [x] [S-01] 断言 新增模型含 Key 后 DB 明文、列表/详情仅“已配置”、响应无明文（03 前端就绪后已执行，`verify-e2e` pass）
- [x] [E-01][unit] 断言 全仓无 `secret://`、`SecretProvider`、`secret_ref` 残留（含例外清单说明）
- [x] [RULE-test-001] verifier：执行 `bash -lc "uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、models API、PostgreSQL | DB 明文；响应/列表仅“已配置”；日志审计无明文 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-01\""] | verified |
| E-01 | unit | 代码库静态检查 | 无 SecretRef/Provider 残留 | tests/test_secret_ref_residue.py | planned | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- E-01：`uv run pytest -q tests/test_secret_ref_residue.py`（1 passed）— `scripts/check_secret_ref_residue.py` 在排除“历史迁移/contract 迁移/backfill CLI/断言用例”后全仓零残留。
- S-01：**e2e_deferred** — 模型管理页属 03-model-management（尚未 start），无可用 Browser 入口；同等边界已由 TASK-002/003 的 integration 断言覆盖（`tests/acceptance/test_secret_migration.py` 明文列 + `tests/acceptance/test_secret_consumers.py` 不回显/脱敏），待 03 前端落地后补跑浏览器 E2E。
- 测试重写：foundation startup（去 Provider 探针）、gateway/runtime/sdk/console 的 `secret://` 用例均已在 TASK-003 完成；`uv run make check` 全绿（582 tests / mypy 183 files）。
- harness-test verifier：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（通过）。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=62fe637bd21d4610b013fb8bd5db9ff1 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=62fe637bd21d4610b013fb8bd5db9ff1 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=03b65ac174c2473cb7b9bc3517b891bf (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=03b65ac174c2473cb7b9bc3517b891bf (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=6bd7773d4df146e4b2c0a0dacd113869 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=4a625fcd5a07471ba74c5b1d1e943609 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
