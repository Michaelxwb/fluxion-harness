# DeepSeek 评审问题修复记录 — Fluxion V3.2

## 结论

本轮不是只做文档勘误，而是同步修正设计、验收场景、TASK 拆分、依赖图和验证门禁。

## P0

### 1. Depends 依赖图

已补齐 12 个 TASK 的显式 Depends，并在 `.code-flow/tasks/2026-08-23/README.md` 增加 Mermaid 依赖图。

### 2. S-R01 前向依赖

- `TASK-005` 不再负责完整 UI Golden Path。
- 新增 `S-R12`：CLI → ApplicationService → SQLite Registry → Runtime，保证 TASK-005 自己可 GREEN。
- `S-R02` 明确在 TASK-005 使用 CLI/ApplicationService 发布 RuntimeProfile v2。
- 原完整产品 `S-R01` 由 `TASK-103` 负责：Console + SQLite + Runtime + Web Chat。

## P1

### 3. TASK-104 拆分

原收尾大任务拆为：

- `TASK-104` Publish / Outbox / Audit
- `TASK-105` WorkflowDefinition 管理
- `TASK-106` Governance / Eval / P1 Views
- `TASK-107` DFX / Quality Hardening

### 4. TASK-102 UI 覆盖

新增：

- `S-C114` RuntimeProfile Draft/Edit/Validate/Publish/Rollback UI E2E
- `S-C115` Binding/Policy/CredentialRef UI E2E

TASK-102 只负责 P0 管理 UI；Users/Channels、Plugin/Hook Policy、Capability、Eval、Runtime Status 明确归 TASK-106。

### 5. Approval / Eval

新增：

- `S-C116` low/medium/high Risk Approval
- `E-C113` high-risk timeout/reject fail closed
- `S-C117` EvalSet/EvalRun → Snapshot/Trace 精确版本
- `E-C114` Eval 引用无效版本时拒绝且不静默 latest

### 6. P1 Feature Owner

- Runtime FEAT-14 A2A → TASK-004 / `S-R11`
- Console FEAT-07/08/10/18/20 → TASK-106 / `S-C118`
- Approval → TASK-106
- Eval → TASK-106

## P2 / 勘误

- Runtime H1 → V1.7；Console H1 → V1.6。
- `muad_agent/`、`muad-agent run`、`muad dev` 等实现命名统一为 Fluxion；仅保留“旧 muad-openclaw”作为问题来源历史描述。
- Runtime §3.2.5 重复已修复：SQLite/PG 一致性策略改为 §3.2.6。
- 场景 ID 命名空间：
  - Runtime：`S-Rxx / E-Rxx / B-Rxx`
  - Console：`S-Cxxx / E-Cxxx / B-Cxxx`

## 其他修复

### SecretStore Owner

TASK-004 明确负责：

- SecretStore SPI
- Dev `LocalEncryptedSecretStore`
- AES-256-GCM
- Master Key 外部注入
- Registry/Log/Trace 不保存 Secret 明文
- `E-R09` 验收

企业 Secret Provider 产品本身仍属于外部系统，通过 SPI 接入。

### NFR Acceptance Gate

新增独立 benchmark 场景：

- `B-R04` Resolver L1 P95 ≤ 5ms
- `B-R05` Hook Framework P95 ≤ 10ms
- `B-R06` Runtime Framework P95 ≤ 50ms / P99 ≤ 100ms
- `B-R07` Snapshot P95 ≤ 20ms
- `B-C104` Resource API P95 ≤ 300ms
- `B-C105` Publish API P95 ≤ 500ms
- `B-C106` Bind P95 ≤ 300ms / Chat P95 ≤ 200ms
- `B-C107` Trace Query P95 ≤ 500ms
- `B-C103` 同时要求 1000 版本首屏 P95 ≤ 800ms

并加入 `pytest-benchmark` dev dependency。

### PostgreSQL Contract Test

新增 `scripts/run_registry_contract_tests.py`：

- 本地无 Docker：允许日常 validation 只跑 SQLite，并明确提示 PostgreSQL 未验证。
- TASK-001 / CI / Release Gate：必须设置 `FLUXION_REQUIRE_POSTGRES_CONTRACT=1`；无 Docker 时直接失败，禁止把 PG Contract 静默跳过。

## 最终覆盖

- Runtime 验收场景：28 个，全部恰好一个 TASK owner。
- Console 验收场景：39 个，全部恰好一个 TASK owner。
- TASK：12 个。
- 无 Acceptance-Refs 遗漏、无重复 owner、无悬空 Depends。
