# Tasks: Phase 5 Governance + Observability + Eval

- **Source**: `.code-flow/tasks/2026-08-28/phase5-governance-observability-eval/phase5-governance-observability-eval.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-28（v0.2： remediation §16 修订）

## Proposal

Phase 5 生产化收尾：为保留 PluginType 补齐生产 provider（`PostgresEncryptedSecretStore` 持久化加密 + key rotation、`S3CompatibleArtifactStore` 生产 provider + `LocalFileArtifactStore` dev 必通 + `artifact_metadata` 表），落地统一 OTel 埋点（`traced_scope` + 7 类 span，trace 关联≥99%）、Eval 生产化（EvalSet 版本化 + RuleBased 默认 + `ReleaseGateService` 挂 publish 管道阻断 P0 回归）与 Console `/build/eval` 实页；Async Task 作为 P1 条件 FEAT。闭合 Phase 5 Gate 四项：trace 关联≥99%、Secret 明文泄漏=0、tenant escape=0、Eval Gate 可阻断 P0 回归。

依据 design 对齐项（用户 2026-08-28 确认 + v0.2 翻案）：Secret 生产走 PostgreSQL AES-256-GCM 不引 Vault；**ArtifactStore 生产必须落地 S3Compatible（remediation §16.1 翻案原「SMB 预留」决策），SMB 仍预留，metadata 落 PG 表（§16.2）**；Collector = OTLP env 接线 + 部署文档；Eval 默认 RuleBased（S-P13-07 不伪造）；Async Task P1 条件。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 真实文件系统（tmp）→ ArtifactStoreProvider | TASK-001 | planned |
| S-02 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 真实 DB（SQLite+PG 双库契约，含 key rotation） | TASK-002 | planned |
| S-03 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | CredentialResolver + 双租户 | TASK-003 | planned |
| S-04 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | 完整 execution：HTTP→Runtime→Model→Tool→Workflow→DB/Redis | TASK-008 | planned |
| S-05 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | EvalSet（workflow 用例）+ EvalExecutor | TASK-004 | planned |
| S-06 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | Publish 管道 + ReleaseGateService + EvalRun | TASK-005 | planned |
| S-07 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | Publish 管道 + ReleaseGateService | TASK-005 | planned |
| S-08 | phase5-governance-observability-eval.design.md#2.4 验收条件 | E2E | Browser → Router → Service → Eval API | TASK-006 | planned |
| S-09 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 真实 DB + worker | TASK-009 | planned |
| E-01 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | 日志/trace/spec/response 扫描 | TASK-003 | planned |
| E-02 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | ArtifactStoreProvider tenant scope | TASK-001 | planned |
| E-03 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | span 完整性扫描 | TASK-008 | planned |
| E-04 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | ReleaseGateService + EvalRunStore | TASK-005 | planned |
| B-01 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | SMB provider 注册点 | TASK-001 | planned |
| B-02 | phase5-governance-observability-eval.design.md#2.4 验收条件 | unit | `FLUXION_SECRET_MASTER_KEY` env | TASK-002 | planned |
| B-03 | phase5-governance-observability-eval.design.md#2.4 验收条件 | unit | OTLP exporter 包存在性 | TASK-007 | planned |
| B-04 | phase5-governance-observability-eval.design.md#2.4 验收条件 | unit | Async Task 开关 | TASK-009 | planned |
| S-10 | phase5-governance-observability-eval.design.md#2.4 验收条件 | integration | S3/MinIO（docker）→ S3CompatibleArtifactStore | TASK-001 | planned |

> NFR-SEC-01（明文=0）由 E-01（TASK-003）承载；NFR-OBS-01（≥99%）由 E-03+S-04（TASK-008）承载；NFR-ARCH-05（6 保留 PluginType 全有 provider 或显式预留）由 S-01/B-01（TASK-001）承载；NFR-PERF-01（gate 附加 P95 ≤500ms）由 TASK-005 承载。Phase 5 Gate 四项闭合：trace≥99%（S-04+E-03）、明文泄漏=0（E-01）、tenant escape=0（S-03+E-02）、Eval 阻断 P0（S-06+S-07）。

---

## TASK-001: LocalFileArtifactStore + artifact:// 引用 + provider 接线/隔离

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: S-01, E-02, B-01, NFR-ARCH-05

### Description

实现两个 `ArtifactStoreProvider`：`plugins/artifact/local_fs.py` `LocalFileArtifactStore`（dev provider，必须通，目录前缀 `{root}/{tenant_id}/{namespace}/{key}`）与 **`S3CompatibleArtifactStore` 生产 provider**（S3/MinIO 兼容 endpoint，remediation §16.1 翻案原「SMB 预留」决策），签名同为 `put(tenant_id, namespace, key, data) -> ArtifactMetadata`/`get`/`delete`，metadata 含 ref/tenant/namespace/key/version/size/sha256/created_at，全方法 timeout/fail policy（S3 为新增外部依赖，规则 18：timeout/retry/fail policy）。**`artifact_metadata` PostgreSQL 表**（artifact_id/tenant_id/owner_type·owner_id/execution_id/workflow_id/content_type/size/sha256/classification/retention_policy/status/created_by/created_at/deleted_at，remediation §16.2——对象存储只存 blob，治理事实落表，支撑 Audit/Retention/GC/User deletion/Access control）。`artifact://{tenant}/{namespace}/{key}@{version}` URI 引用模型入 Resource spec / ExecutionSnapshot（pin，规则 6/10）。SMB 注册点仅预留（B-01：配置 SMB → 明确「SMB 未实现」错误或降级 dev，不崩溃）。经 PluginLoader 注册进 per-PluginType registry，补 E506 lifecycle/isolation 测试（untrusted→isolated、单 provider 故障不拖垮 Runtime）。

### Checklist

- [ ] 实现 `LocalFileArtifactStore`（dev）与 `S3CompatibleArtifactStore`（生产，MinIO dev 端点 + timeout/retry/fail policy）
- [ ] 建 `artifact_metadata` 表（幂等 DDL）并在 put/delete 时落库（含 status/deleted_at）
- [ ] 实现 `artifact://` URI 引用模型（入 spec/snapshot pin，规则 6/10）
- [ ] SMB 注册点预留（明确错误或降级 dev，不崩溃）
- [ ] [S-01][integration] 修改生产代码前，编写验收测试并记录 RED：真实文件系统 put → get → delete，内容一致、metadata（size/sha256/version）正确且落 `artifact_metadata` 表、tenant 命名空间隔离
- [ ] [S-10][integration] 修改生产代码前，编写验收测试并记录 RED：MinIO（docker）端点 put → get → delete + metadata 落表，内容一致、tenant 隔离、超时/失败策略生效
- [ ] [E-02][integration] 修改生产代码前，编写验收测试并记录 RED：tenant A 读 tenant B 的 artifact key → 拒绝访问（tenant escape=0），无数据返回
- [ ] [B-01][integration] 修改生产代码前，编写验收测试并记录 RED：配置 SMB provider → 明确错误或降级 dev provider，不崩溃
- [ ] E506 lifecycle/isolation 测试：provider 注册失败无 partial registry、untrusted→isolated、单 provider 故障不拖垮 Runtime
- [ ] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 `python -m pytest backend/tests/plugins/ backend/tests/architecture/ -k provider`（planned）：断言 Kernel 只依赖 Contract、concrete providers 经 PluginLoader/registry 间接 resolve（无 Kernel→具体 provider 直依赖）
- [ ] **Spec verifier**：`RULE-backend-directory-001` — 运行 `python -m pytest backend/tests/architecture/ -k directory`（planned，AST 守护）：断言 provider 落 `plugins/artifact/`、`plugins/secret/`、目录深度 ≤3、测试目录与源码同构
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实文件系统（tmp 目录）+ 真实 registry | put/get/delete 一致；metadata 正确且落表；tenant 隔离 | planned | planned | planned |
| S-10 | integration | 真实 MinIO（docker）+ artifact_metadata 表 | put/get/delete 一致；metadata 落表；tenant 隔离；超时策略生效 | planned | planned | planned |
| E-02 | integration | 真实 provider + 双租户数据 | 跨租户读取拒绝；明确错误 | planned | planned | planned |
| B-01 | integration | 真实 provider registry + SMB 配置项 | 明确错误或降级；不崩溃 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-002: secret_credentials 表 + PostgresEncryptedSecretStore

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.3 数据设计, phase5-governance-observability-eval.design.md#3.4 接口设计
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-02, B-02

### Description

新增 `secret_credentials` 表（tenant_id+ref 复合 PK、name/version、nonce bytea、ciphertext bytea、revoked、**key_id/cipher_version/rotated_at**、created_at；索引 `(tenant_id, name)`）。实现 `PostgresEncryptedSecretStore`（`plugins/secret/postgres.py`）：`put/rotate/revoke/resolve/list_metadata`，AES-256-GCM（12B nonce），与现有 `LocalEncryptedSecretStore` 同形 API；密文入表绝不存明文。**Key rotation（remediation §16.3）**：按 `key_id` 选择旧 key 解密 → 新 key 加密 → 批量 re-encrypt → revoke old key；rotation 进 AuditLog。Master Key `FLUXION_SECRET_MASTER_KEY`（base64 32B）外置 env，缺失/长度≠32 启动 fail-fast 报错、不静默生成（B-02）。SQLite + PostgreSQL 实现同一 Repository 契约并跑同一 Contract Test（规则 7，PG 复用 `local-pg-test-env`）。全方法定义 timeout + fail policy（规则 18）。

### Checklist

- [ ] 建 `secret_credentials` 表（幂等 DDL）+ 索引；实现 `PostgresEncryptedSecretStore`（put/rotate/revoke/resolve/list_metadata）
- [ ] Master Key 外置 env 校验：缺失/长度≠32 → 启动明确报错（fail-fast，不静默生成）
- [ ] 实现 key rotation：按 `key_id` 解旧密 → 新密加密 → 批量 re-encrypt → revoke 旧 key（rotation 进 AuditLog）
- [ ] [S-02][integration] 修改生产代码前，编写验收测试并记录 RED：真实双库（SQLite+PG）put → rotate（批量重加密）→ 重启 store → resolve 一致；rotate 后经 key_id/cipher_version 可解旧密文；revoke 后 resolve 拒绝；表中 `ciphertext` 非明文
- [ ] [B-02][unit] 修改生产代码前，编写验收测试并记录 RED：Master Key 缺失/长度≠32 → 启动明确报错
- [ ] 类型注解齐全、异常不吞、timeout/fail policy 全覆盖（provider 外部 IO 带 deadline）
- [ ] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/contract/ -k secret`（planned，SQLite + PG `local-pg-test-env` 各一套）：断言双库同契约、索引生效、密文字节存储、无 N+1
- [ ] **Spec verifier**：`RULE-backend-quality-001` — 运行 `ruff check` + `mypy backend/src/fluxion/plugins/secret/`（planned）+ S-02/B-02 verifier 用例：断言全类型注解、无静默吞异常、全方法 timeout/fail policy
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 SQLite + PG 双库（重启 store 进程级重建 + key rotation） | resolve 持久一致；rotate 后旧密文可解；revoke 拒绝；ciphertext 非明文 | planned | planned | planned |
| B-02 | unit | 真实 env 读取路径 | 缺失/长度≠32 → 明确报错不启动 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-003: Secret tenant 隔离 + 泄漏门禁 + AuditLog

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: phase5-governance-observability-eval.design.md#2.4 验收条件, phase5-governance-observability-eval.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-03, E-01, NFR-SEC-01

### Description

tenant 隔离收口（Phase 5 Gate「tenant escape=0」）：CredentialResolver 双租户场景——tenant A 引用 tenant B ref → `secret_tenant_mismatch` 拒绝。泄漏门禁（Gate「明文泄漏=0」）：扫描测试覆盖日志（structlog 输出）、trace（span attributes）、Resource spec、API response 四个面，任一面出现 secret 明文 → 测试失败阻断 CI（E-01）。Secret 高影响操作进 AuditLog（规则 24）：publish/revoke secret；redaction 全链路（RISK-P5-03）。

### Checklist

- [ ] CredentialResolver tenant 收口断言（provider 方法首参 tenant_id 强制）
- [ ] [S-03][integration] 修改生产代码前，编写验收测试并记录 RED：tenant A/B 各持 secret，A 引 B ref → `secret_tenant_mismatch` 拒绝
- [ ] [E-01][integration] 修改生产代码前，编写泄漏扫描测试并记录 RED：日志/trace/spec/response 四面注入已知明文 secret → 任一面出现即失败（明文=0 门禁）
- [ ] secret publish/revoke 进 AuditLog（关联 request_id/trace_id/tenant_id）
- [ ] **Spec verifier**：`RULE-backend-logging-001` — 运行 E-01 泄漏套件 + AuditLog 断言（planned）：断言 structlog JSON 脱敏生效、Secret 操作全部进 AuditLog、明文=0 门禁可阻断
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 真实 CredentialResolver + 双租户 secret 数据 | 跨租户 resolve 拒绝；`secret_tenant_mismatch` | planned | planned | planned |
| E-01 | integration | 真实日志/trace/spec/response 输出通道 | 四面明文=0；门禁可阻断 CI | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-004: EvalSet 版本化 + EvalExecutor 扩展

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-05

### Description

EvalSet 走 resource_definitions 版本化生命周期（draft→publish→版本递增）。`EvalExecutor` 扩展：模型评测 harness SPI 预留（接口形态），**RuleBased 默认评测器**（确定性、可测；真实模型评测需凭据，按 S-P13-07 约束无凭据不实现不伪造）；支持 Workflow 类型用例与 Capability 契约评测（对齐能力层，US-11：Step 与 Tool 复用 Capability Contract）。Eval API 扩展：`GET /admin/evals`、`POST /admin/evals/{id}/run`、`GET /admin/evals/runs`，统一 envelope 封装（Handler 不手写响应结构）。

### Checklist

- [ ] EvalSet 版本化 lifecycle（走 resource_definitions）；`EvalExecutor` SPI + RuleBased 默认实现
- [ ] Workflow 类型用例 + Capability 契约评测支持（模型 harness 仅预留接口）
- [ ] Eval API 三端点（envelope 封装，标准响应结构）
- [ ] [S-05][integration] 修改生产代码前，编写验收测试并记录 RED：含 workflow 用例的 EvalSet → start EvalRun → score/passed 正确、EvalRun 记录可查
- [ ] RuleBased 评测器确定性断言：同输入同 score（真实模型评测不伪造，无凭据保持 RuleBased）
- [ ] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/services/ backend/tests/resources/ -k eval`（planned）：断言 EvalSet 版本化生命周期、`artifact://` 引用可 pin 进 snapshot（规则 6/10）
- [ ] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 S-05 verifier 用例（planned）：断言 Workflow 用例/Capability 契约评测对齐能力层（复用 Capability Contract，不另起评测语义）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | 真实 EvalSet + EvalExecutor + EvalRunStore | workflow 用例 score/passed 正确；EvalRun 可查 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-005: ReleaseGateService 挂 publish 管道

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.4 接口设计, phase5-governance-observability-eval.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-06, S-07, E-04, NFR-PERF-01

### Description

`ReleaseGateService.evaluate(release_id, candidate_eval_run_id, baseline_run_id, threshold) -> GateDecision`，复用 `EvaluationApplicationService.compare()`；blocked 决策含 score_delta 与原因。挂 publish 管道：候选版本跑 EvalRun 对比基线，score 回退超阈值 → 阻断 P0 发布（S-06）；达标 → 放行且 EvalRun 记录留档（S-07）。gate 等待超时 ≤2s，超时 fail-closed 阻断并记录（不阻塞 publish 主路径，评测结果异步落 EvalRunStore）。基线 run 不存在 → 阻断 + 明确错误「基线不可用」（E-04，RISK-P5-04）。阻断决策留档 AuditLog（发布回滚复用既有治理）。NFR-PERF-01：publish P95 增量 ≤500ms。

### Checklist

- [ ] 实现 `ReleaseGateService`（compare 复用 + GateDecision 含 score_delta/原因）并挂 publish 管道
- [ ] gate 超时 ≤2s fail-closed；阻断决策留档 AuditLog
- [ ] [S-06][E2E] 修改生产代码前，编写验收测试并记录 RED：候选版本 score < 基线阈值 → publish 被阻断 + 明确诊断（score delta）
- [ ] [S-07][E2E] 修改生产代码前，编写验收测试并记录 RED：候选 score ≥ 阈值 → publish 放行、EvalRun 记录留档
- [ ] [E-04][integration] 修改生产代码前，编写验收测试并记录 RED：基线 run 不存在 → 阻断 + 明确错误「基线不可用」
- [ ] NFR-PERF-01 断言：publish P95 增量 ≤500ms（gate 计时）
- [ ] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 S-06/S-07/E-04 verifier 套件（planned）：断言 provider/gate 外部 IO 全 timeout、gate 超时 fail-closed（2s）、异常不吞、DFX 为编码期自动化证据
- [ ] **Spec verifier**：`RULE-backend-platform-001` — 运行 S-06/S-07 verifier 用例（planned）：断言 gate 走控制面 API 标准响应、阻断决策留档 AuditLog、错误码命名空间
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实 publish 管道 + EvalRun（真实 EvalSet） | 回退阻断 + score delta 诊断 | planned | planned | planned |
| S-07 | E2E | 真实 publish 管道 + EvalRun | 达标放行；EvalRun 留档 | planned | planned | planned |
| E-04 | integration | 真实 EvalRunStore（无基线 run） | 阻断 + 明确错误「基线不可用」 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-006: Console `/build/eval` 实页

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-005
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#2.4 验收条件
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, frontend-semi-design#RULE-frontend-semi-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-08, E-04

### Description

Phase 4 `/build/eval` 占位升级为实页：EvalSet 列表 / EvalRun 列表 / 详情 / 触发评测（`POST /admin/evals/{id}/run`）；gate 阻断决策（score delta、基线不可用）以标准响应展示。复用 Phase 4 前端模式：`src/pages/eval/`、全 Semi 组件、经 services（in-memory/http 同契约、无裸 fetch）、容器/展示分离（props 只读 + 事件上抛）、四态完备（loading/empty/error/success）。

### Checklist

- [ ] 实现 Eval 页（列表/详情/触发评测 + gate 决策展示），页面落 `src/pages/eval/`
- [ ] 数据经 services（in-memory 先行，http 同契约），组件零裸 fetch；全 Semi 组件、容器/展示分离
- [ ] [S-08][E2E] 修改生产代码前，编写验收测试并记录 RED：打开 `/build/eval` → EvalSet/Run 列表/详情/触发评测可见，四态完备
- [ ] E-04 联动断言：gate 阻断（基线不可用/score 回退）在页面呈现标准错误响应
- [ ] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 S-08 verifier 用例（planned）：断言全部 Eval API 经统一 envelope 消费（`code=0`/错误路径 + request_id）、services 层无手写响应结构
- [ ] **Spec verifier**：`RULE-frontend-semi-001` — 运行 UI 规则套件（planned）：断言页面全 Semi 组件、无第二套通用组件库、react19-adapter 首导入保持
- [ ] **Spec verifier**：`RULE-frontend-quality-001` — 运行质量扫描（planned）：断言无裸 fetch、TS 无 `any`/`@ts-ignore` 滥用、页面测试覆盖、四态用例齐全
- [ ] **Spec verifier**：`RULE-frontend-directory-001` — 运行目录纪律扫描（planned）：断言页面在 `src/pages/eval/`、组件在 `src/components/`/shared、测试目录同构
- [ ] **Spec verifier**：`RULE-frontend-component-001` — 运行组件契约套件（planned）：断言容器/展示分离、props 只读、事件上抛、接口契约类型完整
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实浏览器 + Router + Eval API（in-memory service） | 列表/详情/触发可见；四态完备 | planned | planned | planned |
| E-04 联动 | integration | 真实组件树 + gate 阻断响应 | 阻断决策标准错误呈现 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-007: traced_scope 助手 + OTLP Collector 接线

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#3.4 接口设计, phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#4 部署与运维
- **Acceptance-Refs**: B-03, O507

### Description

实现 `traced_scope` 上下文助手：`async with traced_scope(name, kind=..., attributes={}): ...`——统一 span 创建入口，自动挂 trace_id/execution_id/tenant_id/request_id 关联字段，红色内容经 `observability/redaction.py` 脱敏（Secret 明文不进 span）。OTLP Collector 接线：`FLUXION_OTLP_ENDPOINT` env → OTLP exporter；exporter 包缺失 → 降级不 export + warning、不阻断服务（B-03）；本地 TracerProvider（dev 无 exporter）可用。Collector 部署配置文档（O507）。

### Checklist

- [ ] 实现 `traced_scope`（统一关联字段注入 + 自动脱敏）
- [ ] OTLP env 接线 + exporter 缺失降级（warning 不阻断）；Collector 部署配置文档
- [ ] [B-03][unit] 修改生产代码前，编写验收测试并记录 RED：otlp exporter 缺失 → 降级不 export + warning，服务不阻断
- [ ] 断言 traced_scope 产物 span 携带四关联字段且红色内容已脱敏
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | unit | 真实 exporter 依赖探测路径 | 缺失降级 + warning；服务不阻断 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-008: 7 类 span 埋点接线 + 关联完整性门禁

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#2.4 验收条件, phase5-governance-observability-eval.design.md#3.5 质量实现方案
- **Acceptance-Refs**: S-04, E-03, NFR-OBS-01

### Description

按 O501–O506 埋点清单接线（全部经 `traced_scope`）：O501 HTTP（`api/console_routes_*`/`channel.py`）、O502 Runtime execution、O503 Model（`model_providers.py`）、O504 Tool/MCP（`tool_*`/`mcp.py`）、O505 Workflow（`runtime/workflow_dbos.py`；若 Phase 3 未落地按契约预留接点）、O506 DB/Redis（`registry/store.py`/cache）。span 名与关联字段按清单一致。门禁测试：完整性扫描——采样全部 span，缺 trace_id/execution_id 关联字段的比例 >1% → 失败阻断 CI（E-03，NFR-OBS-01 ≥99%）。E2E：完整 execution 跑一遍，全链路 span 携带四关联字段（S-04）。

### Checklist

- [ ] O501–O506 六类埋点接线（全部经 `traced_scope`，span 名/字段按清单）
- [ ] [S-04][E2E] 修改生产代码前，编写验收测试并记录 RED：真实 execution（HTTP→Runtime→Model→Tool→Workflow→DB/Redis）→ 全链路 span 携带 trace_id/execution_id/tenant_id/request_id，关联完整率≥99%
- [ ] [E-03][integration] 修改生产代码前，编写完整性扫描测试并记录 RED：span 采样缺关联字段 >1% → 测试失败（CI 门禁）
- [ ] 断言 span 中红色内容已脱敏（明文不进 span）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 execution 全链路（HTTP/Runtime/Model/Tool/Workflow/DB·Redis） | span 四关联字段齐全；完整率≥99% | planned | planned | planned |
| E-03 | integration | 真实 span 采样扫描 | 缺关联字段 >1% → 门禁失败 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-009: durable_task 表 + 无状态 worker（P1 条件 FEAT）

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: phase5-governance-observability-eval.design.md#2.2 功能方案, phase5-governance-observability-eval.design.md#3.3 数据设计, phase5-governance-observability-eval.design.md#2.4 验收条件
- **Acceptance-Refs**: S-09, B-04, RISK-P5-05

### Description

**P1 条件 FEAT**（对齐项 E）：仅在明确存在耗时后台逻辑时实施启用。交付契约就绪：`durable_task` 表（task_id PK 幂等键、tenant_id、payload jsonb、status pending/claimed/done/failed、attempts 有限、claimed_at/done_at/created_at；索引 `(status, claimed_at)`）+ 无状态 worker（poll/claim/resume，tenant scope 全链路）。启用时：任务状态正确、失败可重试（有限）、无重复执行（task_id 幂等，RISK-P5-05）。未启用（默认）：B-04 断言功能开关关闭无副作用——表可建、worker 不启动、现有路径零变化。V2.2 不引 Event Bus。

### Checklist

- [ ] 建 `durable_task` 表（幂等 DDL）+ 索引；实现无状态 worker（poll/claim/resume，默认不启动）
- [ ] 功能开关默认关闭；未启用时 B-04 断言零副作用
- [ ] [B-04][unit] 修改生产代码前，编写验收测试并记录 RED：开关未启用 → worker 不启动、现有路径零变化（无副作用）
- [ ] [S-09][integration] 修改生产代码前，编写验收测试并记录 RED（启用态）：真实 DB + worker enqueue → claim → 完成/失败；状态正确、失败有限重试、task_id 幂等无重复执行
- [ ] 双库契约：`durable_task` 纳入 SQLite/PG 共享 Contract Test
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | integration | 真实 DB + worker 进程（启用态） | enqueue→claim→终态正确；有限重试；幂等无重复 | planned | planned | planned |
| B-04 | unit | 真实开关 + 启动路径 | 未启用零副作用 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)
