# Tasks: Phase 6 Hardening + Scale + Release（加固与规模化发布）

- **Source**: `.code-flow/tasks/2026-08-28/phase6-hardening-scale-release/phase6-hardening-scale-release.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-29（v0.4 登记 FEAT-P6-06 生产装配为 TASK-001）

## Proposal

Phase 6 加固 + 验证 + 发布（承接 Phase 1-5 已落地设计实现，不新增业务功能）。首个可执行任务为 **TASK-001 生产装配**：把 phase5 生产 provider（PostgresEncryptedSecretStore / S3CompatibleArtifactStore / Operations DBOS sysdb / release_gate_enforced=True）从测试/占位装配进生产 app，并落实 production profile 下 InMemory Secret/Approval/Eval/Trace 唯一实现 fail-fast（与 FEAT-P6-05 ④ 协同）。其余 FEAT-P6-01..05（capacity/chaos/migrate/dod/部署 Gate）后续按 design 立项。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-10 | phase6-hardening-scale-release.design.md#2.5.2 功能验收场景（FEAT-P6-06） | integration | 真实 PG secret + S3/MinIO artifact + enforced gate + Operations 真实端点 | TASK-001 | planned |

---

## TASK-001: Phase 5 生产装配（Secret/Artifact/ReleaseGate/Operations 接线）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase6-hardening-scale-release.design.md#2.2 功能方案（FEAT-P6-06）, phase5-governance-observability-eval.design.md#3.2 架构设计, phase5-governance-observability-eval.design.md#3.4 接口设计
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

### Checklist

- [ ] dev_bundle/production 装配 `PostgresEncryptedSecretStore`（替换内存 store；Master Key env + 注册表 active key 初始化）
- [ ] 装配 `S3CompatibleArtifactStore`（S3/MinIO endpoint + timeout/retry/fail policy）
- [ ] 装配 Operations 运营端点（`OperationsApplicationService` DSN 传入 console app，Queues/Workers 真实数据）
- [ ] 装配 `release_gate_enforced=True`（无 gate 参数 publish fail-closed，38_001）
- [ ] production profile 守卫：InMemory Secret/Approval/Eval/Trace 唯一实现 fail-fast（承接 FEAT-P6-05 ④）
- [ ] [S-10][integration] 修改生产代码前，编写验收测试并记录 RED：生产装配集成测试——Secret 落 PG 密文 + S3 artifact + enforced gate + Operations 真实端点
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | integration | 真实 PG secret（AES-256-GCM）+ S3/MinIO artifact + enforced gate + Operations 真实端点 | Secret 落 PG 密文、artifact 落 S3 + metadata、gate 强制（无 gate 参数 publish fail-closed）、Operations 返回真实 DBOS 状态、production InMemory fail-fast | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-29] created (draft)：phase5 review 遗留「生产装配」登记（Secret/Artifact/Operations 无生产装配点 + release_gate_enforced 无处开启）
