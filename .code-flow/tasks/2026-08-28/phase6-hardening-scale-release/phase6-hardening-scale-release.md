# Tasks: Phase 6 Hardening + Scale + Release（加固与规模化发布）

- **Source**: `.code-flow/tasks/2026-08-28/phase6-hardening-scale-release/phase6-hardening-scale-release.design.md`
- **Created**: 2026-08-29
- **Updated**: 2026-08-29（cf-task:plan 拆解；承接 phase5 review 生产装配登记）

## Proposal

Phase 6 加固 + 验证 + 发布（承接 Phase 1-5 已落地设计实现，不新增业务功能）。六条主线：Capacity Profile V1 契约（scale-test 复核 SLO）、Chaos 测试套件（进程级故障注入）、One-time Migration/Rollover（仅真实外部依赖）、Final DoD 自动化验收（14 项 verifier + 静态扫描）、真实部署 Gate 与生产运行边界（k8s 多副本 + production fail-fast）、**Phase 5 生产装配**（真实 provider 接线——phase5 review 遗留，release_gate_enforced 强制语义 + Secret/Artifact/Operations 装配）。

**拆解说明**：按设计 FEAT-P6-01..06 顺序拆为 TASK-001..006；TASK-006（生产装配）承接 phase5 review 登记的生产装配项（PostgresEncryptedSecretStore / S3CompatibleArtifactStore / Operations DBOS sysdb / release_gate_enforced=True），是其它 FEAT 跑真实 provider 的前提。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-01） | E2E | 真实 PostgreSQL + Runtime | TASK-001 | planned |
| S-02 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Runtime 真实进程 + Registry | TASK-002 | planned |
| S-03 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Workflow Engine + durable store | TASK-002 | planned |
| S-04 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | 真实 PostgreSQL | TASK-002 | planned |
| S-05 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-03） | E2E | 真实外部依赖（如有） | TASK-003 | planned |
| S-06 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-04） | E2E | 全套件边界 | TASK-004 | planned |
| S-07 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | E2E | 本地 k8s 真实集群 ≥2 RuntimeInstance 副本 | TASK-005 | planned |
| S-08 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | E2E | 已发布 Agent 运行中 → 停 Console | TASK-005 | planned |
| S-09 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | integration | Runtime 进程内全部 dict/list/cache | TASK-005 | planned |
| S-10 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-06） | integration | 真实 PG secret + S3/MinIO artifact + enforced gate + Operations 真实端点 | TASK-006 | planned |
| E-01 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | Cache + Registry | TASK-002 | planned |
| E-02 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | 外部 activity 真实调用 | TASK-002 | planned |
| E-03 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Workflow Engine + durable store | TASK-002 | planned |
| E-04 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | ArtifactStore（local-fs dev） | TASK-002 | planned |
| E-05 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | SemanticStore SPI | TASK-002 | planned |
| E-06 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Workflow Engine + durable store | TASK-002 | planned |
| E-07 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | integration | production profile 启动装配路径 | TASK-005 | planned |
| E-08 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | integration | production profile + RuntimeScheduler 本地实现 | TASK-005 | planned |
| B-01 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-01） | E2E | 真实 PostgreSQL + Runtime | TASK-001 | planned |
| B-02 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-04） | integration | Registry active pinned hard-delete | TASK-004 | planned |
| B-03 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-03） | integration | SurfaceEvidence 判定 | TASK-003 | planned |
| B-05 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-03） | integration | SurfaceEvidence 判定（UNKNOWN 保守） | TASK-003 | planned |

> RULE-P6-01..05 与 NFR（CONSIST/REC/REL/TRACE/SEC/UX/LEGACY/DEL）映射到各 TASK 的 Acceptance-Refs；本表覆盖 design 全部 P0 场景。

---

## TASK-001: Capacity Profile V1 契约 + scale-test 复核

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-01, B-01, RULE-P6-01, NFR-P6-CONSIST-01, NFR-P6-CONSIST-02, NFR-P6-REC-01

### Description

锁定 Capacity Profile V1 契约（7 项：50 tenant / 1,000 users-per-tenant / 5,000 concurrent sessions / 10 Runtime replicas / 100 workflow concurrency / 5 MCP servers / 1,000 memories）为部署/验收事实（非运行态配置，架构规则 #2），载体 `docs/capacity/capacity-profile-v1.md` + `tests/scale/` 压测套件。scale-test 实测复核 SLO（NFR-P6-REC-01 Runtime 恢复 P95≤30s；Snapshot digest cross-pod 一致率=100%；Capability equivalence=100%），V1 值只紧不松（RULE-P6-01，B-01）。

### Checklist

- [ ] 建 `docs/capacity/capacity-profile-v1.md` 契约文档（7 项 V1 值 + 只紧不松规则）
- [ ] 建 `tests/scale/test_capacity_verify.py` scale-test 套件（批量并发 session 构造，非逐 session 串行）
- [ ] `fluxion-capacity verify --profile v1` CLI（退出码 0=SLO 达标 / 非 0=未达标）
- [ ] [S-01][E2E] 修改生产代码前，编写验收测试并记录 RED：真实 PG + Runtime 满负载（50 tenant / 5000 sessions）→ 全部 SLO 达标或记录实际瓶颈
- [ ] [B-01][E2E] 并发 5,000 sessions 满负载 → SLO 仍达标或触发"只紧不松"评审
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 真实 PostgreSQL + Runtime | 全部性能 SLO 达标；记录实测值；V1 契约保持或收紧 | planned | planned | planned |
| B-01 | E2E | 真实 PostgreSQL + Runtime | 5,000 sessions SLO 达标或记录瓶颈 + 只紧不松评审 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)

---

## TASK-002: Chaos 测试套件（runtime / workflow / storage）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#3.2 架构设计, phase6-hardening-scale-release.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-02, S-03, S-04, E-01..E-06, RULE-P6-02, NFR-P6-REC-02, NFR-P6-REL-01, NFR-P6-REL-02

### Description

进程级故障注入 Chaos 套件（pytest fixture + subprocess kill/restart + 环境扰动，D1 选型）覆盖 roadmap 故障清单：Runtime 组（kill/rolling restart/cache flush）、Workflow 组（backend restart/activity timeout/duplicate delivery/approval long wait）、Storage 组（PG failover/ArtifactStore 不可达/SemanticStore 降级）。关键真实边界不得 mock（RULE-P6-02）：Runtime 进程 / Store / 外部 activity。依赖真实 provider 装配（Depends TASK-006）。

### Checklist

- [ ] 建 `tests/chaos/test_runtime_chaos.py` / `test_workflow_chaos.py` / `test_storage_chaos.py`（pytest fixture 进程起停 + 环境扰动）
- [ ] `fluxion-chaos run --group runtime|workflow|storage` CLI（等价 `pytest -m chaos_<group>`）
- [ ] [S-02][E2E] RED：kill Runtime → 重启恢复 P95≤30s + Snapshot digest 一致率=100%
- [ ] [S-03][E2E] RED：重启 workflow backend → 恢复 P95≤60s，durable state 无丢失、无重复 side effect
- [ ] [S-04][E2E] RED：PG 连接中断/failover → 已提交 durable state RPO=0
- [ ] [E-01..E-06][integration/E2E] RED：cache flush 降级 L2、activity timeout fail policy、重复投递幂等、ArtifactStore 不可达、SemanticStore 降级、审批长时等待不死锁
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | Runtime 真实进程 + Registry | 恢复 P95≤30s；Snapshot digest 一致率=100% | planned | planned | planned |
| S-03 | E2E | Workflow Engine + durable store | 恢复 P95≤60s；durable 无丢失；无重复 side effect | planned | planned | planned |
| S-04 | E2E | 真实 PostgreSQL | 已提交 durable state RPO=0 | planned | planned | planned |
| E-01..E-06 | integration/E2E | 真实 Cache/Activity/Store | 降级/幂等/不悬挂 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)

---

## TASK-003: One-time Migration / Rollover（仅真实外部依赖）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#3.4 接口设计, phase6-hardening-scale-release.design.md#4.4 数据迁移
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-05, B-03, B-05, RULE-P6-03

### Description

One-time Migration/Rollover：SurfaceEvidence（active_record_count/active_token_count/enabled_integration_count/traffic_30d/last_used_at/known_external_consumer/public_stable_contract/evidence_source）客观判定三级分类（EXTERNAL_ACTIVE/RESET_ALLOWED/UNKNOWN）。仅真实外部依赖 → 双写→一致性校验→切换→删旧（S-05）；无外部依赖 → 直接 reset 不建双写（B-03）；**UNKNOWN 一律按 EXTERNAL_ACTIVE，禁止 destructive reset**（B-05，RULE-P6-03 保守默认）。`fluxion-migrate rollover/cleanup` CLI。

### Checklist

- [ ] 实现 SurfaceEvidence 判定（三级分类 + UNKNOWN 保守默认）
- [ ] `fluxion-migrate rollover`（双写→校验→切换，仅真实外部依赖）/ `fluxion-migrate cleanup`（legacy 删除）
- [ ] [S-05][E2E] RED：真实外部依赖 → 双写→校验→切换→删旧全流程
- [ ] [B-03][integration] RED：无外部依赖 → 直接 reset 不建双写
- [ ] [B-05][integration] RED：证据不足（UNKNOWN）→ 禁止 destructive reset
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实外部依赖（如有） | 双写→校验→切换→删旧全流程成功 | planned | planned | planned |
| B-03 | integration | SurfaceEvidence 判定 | 无外部依赖直接 reset | planned | planned | planned |
| B-05 | integration | SurfaceEvidence 判定（UNKNOWN） | 禁止 destructive reset（保守默认） | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)

---

## TASK-004: Final DoD 自动化验收套件

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-005, TASK-006
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#2.5.3 非功能指标, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#4.2 发布与回滚
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-06, B-02, RULE-P6-04, NFR-P6-LEGACY-01..04, NFR-P6-DEL-01, NFR-P6-TRACE-01, NFR-P6-UX-01

### Description

Final DoD 14 项自动化验收（每项一个 verifier）+ 四类 legacy 静态扫描（dead PluginType / runtime raw `spec_json.get` / pseudo `_summarize` / permanent legacy path，D5 选型）+ active pinned hard-delete=0 断言。`fluxion-dod verify` CLI（14/14 全过才 Release，RULE-P6-04；任一失败阻断 S-06）。trace completeness≥99%（NFR-P6-TRACE-01）+ UX journey≥95%（NFR-P6-UX-01）纳入门禁。B-02：active pinned resource hard-delete 被拒（409）。

### Checklist

- [ ] 建 `tests/dod/` 14 项 verifier + `scripts/static_scan/` 四类扫描（spec_json_get/summarize_scan/legacy_path_scan/plugin_type_scan）
- [ ] `fluxion-dod verify` CLI（0=14/14 全过 / 非 0=存在失败）
- [ ] [S-06][E2E] RED：Final DoD 14 项全过 → Release 门禁通过
- [ ] [B-02][integration] RED：active pinned hard-delete → 409 拒绝
- [ ] NFR-P6-LEGACY-01..04 / DEL-01 / TRACE-01 / UX-01 断言接入门禁
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 全套件边界 | 14 项全过；Release 门禁通过 | planned | planned | planned |
| B-02 | integration | Registry active pinned hard-delete | 拒绝删除（409） | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)

---

## TASK-005: 真实部署 Gate 与生产运行边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#4.1 部署架构, phase6-hardening-scale-release.design.md#4.3 监控告警
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-07, S-08, S-09, E-07, E-08, RULE-P6-05

### Description

真实部署 Gate 与生产运行边界（P0-3/P0-4/P0-5 + Gate G3/G5/G7）：①本地 k8s ≥2 副本部署 Gate（rolling restart/kill pod，Snapshot digest 一致率=100%、committed durable state RPO=0，S-07）；②停 Console 后已发布 Agent 继续运行（Runtime 不调 Console API 获取配置 truth，G7/ARCH-14，S-08）；③本地状态审计脚本（全部标注 Ephemeral/Cache/Durable/SoT，Durable/SoT 本地命中=0，G5，S-09）；④production profile 禁止 InMemory Trace/Approval/Eval 唯一实现 fail-fast（P0-5，E-07）；⑤RuntimeScheduler 本地实现限定 test/dev fail-fast（P0-4，E-08）。

### Checklist

- [ ] 本地 k8s 部署 Gate（≥2 RuntimeInstance 副本 + rolling restart/kill pod + 扩缩容）
- [ ] 停 Console 后已发布 Agent 继续运行验证（G7/ARCH-14）
- [ ] 本地状态审计脚本（Ephemeral/Cache/Durable/SoT 标注 + 命中=0）
- [ ] production profile fail-fast 守卫：InMemory Trace/Approval/Eval 唯一实现 + RuntimeScheduler 本地实现（P0-4/P0-5）
- [ ] [S-07][E2E] RED：k8s ≥2 副本 rolling restart/kill → digest 一致率=100%、RPO=0、facts 零丢失
- [ ] [S-08][E2E] RED：停 Console → 已发布 Agent 继续执行（不调 Console API truth）
- [ ] [S-09][integration] RED：local state audit 扫描 → Durable/SoT 本地命中=0
- [ ] [E-07][integration] RED：production profile 下 InMemory Trace/Approval/Eval 唯一实现 → 启动 fail-fast
- [ ] [E-08][integration] RED：production + 本地 scheduler → fail-fast
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 本地 k8s 真实集群 ≥2 副本 | digest 一致率=100%；RPO=0；facts 零丢失 | planned | planned | planned |
| S-08 | E2E | 停 Console + 已发布 Agent | 执行不受影响；不调用 Console API truth | planned | planned | planned |
| S-09 | integration | Runtime 进程内全部 dict/list/cache | Durable/SoT 本地命中=0 | planned | planned | planned |
| E-07 | integration | production profile 装配路径 | InMemory 唯一实现 → fail-fast | planned | planned | planned |
| E-08 | integration | production + 本地 scheduler | fail-fast 拒绝 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)

---

## TASK-006: Phase 5 生产装配（Secret/Artifact/ReleaseGate/Operations 接线）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案（FEAT-P6-06）, phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-10

### Description

phase5 review 指出 **Secret/Artifact/Operations 生产 provider 无装配点**（全仓 grep 无构造点，仅测试接线），且 `release_gate_enforced` 默认 False 无装配传 True（P1-7 强制语义纸面）。本任务把 phase5 生产 provider 接线进生产 app（composition root：dev_bundle + production 装配）：

1. `PostgresEncryptedSecretStore` 装配（替换内存 `LocalEncryptedSecretStore`；`FLUXION_SECRET_MASTER_KEY` + 注册表 active key_id 初始化）；
2. `S3CompatibleArtifactStore` 装配（S3/MinIO endpoint 配置 + timeout/retry/fail policy）；
3. Operations 运营端点装配（`OperationsApplicationService(sysdb_dsn)` 传入 console app，Queues/Workers 返回真实 DBOS 状态而非空数组）；
4. **`release_gate_enforced=True`** 装配（phase5 P1-7：无 gate 参数的 publish fail-closed 在 production 生效）；
5. production profile 守卫：InMemory Secret/Approval/Eval/Trace 唯一实现 fail-fast（与 FEAT-P6-05 ④ 协同）。

完成后 production 装配集成测试走真实 PG secret + S3 artifact + enforced gate + Operations 真实端点。

**k8s 部署基建补强**（2026-08-29）：本地 k8s（OrbStack 单节点 + Docker runtime）已确认可用、`deploy/` 已有 Dockerfile + docker-compose + 最小 Helm Chart（仅 1 个 Deployment 承载 Runtime/Console API，`replicaCount=1`）。生产装配以**真实 k8s Pod 部署**为验收边界：构建镜像载入本地 k8s、扩 Chart 到 `fluxion-workflow-worker` Deployment + ≥2 副本、共享 PG（宿主 `mmuser` + DBOS sysdb 同库）/Redis 可达——S-10 集成测试在部署后的 Pod 上跑。

### Checklist

- [ ] dev_bundle/production 装配 `PostgresEncryptedSecretStore`（替换内存 store；Master Key env + 注册表 active key 初始化）
- [ ] 装配 `S3CompatibleArtifactStore`（S3/MinIO endpoint + timeout/retry/fail policy）
- [ ] 装配 Operations 运营端点（`OperationsApplicationService` DSN 传入 console app，Queues/Workers 真实数据）
- [ ] 装配 `release_gate_enforced=True`（无 gate 参数 publish fail-closed，38_001）
- [ ] production profile 守卫：InMemory Secret/Approval/Eval/Trace 唯一实现 fail-fast（承接 FEAT-P6-05 ④）
- [ ] k8s 基建：构建 Docker 镜像并载入本地 k8s（OrbStack，`docker build` + 更新 helm `image.tag`）
- [ ] k8s 基建：扩 Helm Chart——新增 `fluxion-workflow-worker` Deployment（DBOS 执行进程）+ Runtime/Console API `replicaCount ≥2`
- [ ] k8s 基建：共享 PG/Redis 可达（`externalDatabase` → 宿主 PG `mmuser` + DBOS sysdb 同库；Redis 若需）
- [ ] [S-10][integration] 修改生产代码前，编写验收测试并记录 RED：生产装配集成测试——Secret 落 PG 密文 + S3 artifact + enforced gate + Operations 真实端点（部署到 k8s Pod 上验证）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | integration | 真实 PG secret（AES-256-GCM）+ S3/MinIO artifact + enforced gate + Operations 真实端点（**k8s Pod 部署**：OrbStack 本地集群 + worker/API 多副本 + 共享 PG/Redis） | Secret 落 PG 密文、artifact 落 S3 + metadata、gate 强制（无 gate 参数 publish fail-closed）、Operations 返回真实 DBOS 状态、production InMemory fail-fast | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase5 review 遗留「生产装配」登记（Secret/Artifact/Operations 无生产装配点 + release_gate_enforced 无处开启）；cf-task:plan 拆解后归位 TASK-006
- [2026-08-29] k8s 部署基建补强：本地 k8s（OrbStack）确认可用；扩 Helm Chart（worker Deployment + ≥2 副本 + 共享 PG/Redis）+ S-10 升级为 k8s Pod 部署级验证
