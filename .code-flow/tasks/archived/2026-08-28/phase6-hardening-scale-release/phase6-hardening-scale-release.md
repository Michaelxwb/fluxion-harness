# Tasks: Phase 6 Hardening + Scale + Release（加固与规模化发布）

- **Source**: `.code-flow/tasks/2026-08-28/phase6-hardening-scale-release/phase6-hardening-scale-release.design.md`
- **Created**: 2026-08-29
- **Updated**: 2026-08-30（六任务 done + Review 两轮修复全闭合：P0×2 / P1×6 / P2 批量 + 复审收尾测试）

## Proposal

Phase 6 加固 + 验证 + 发布（承接 Phase 1-5 已落地设计实现，不新增业务功能）。六条主线：Capacity Profile V1 契约（scale-test 复核 SLO）、Chaos 测试套件（进程级故障注入）、One-time Migration/Rollover（仅真实外部依赖）、Final DoD 自动化验收（14 项 verifier + 静态扫描）、真实部署 Gate 与生产运行边界（k8s 多副本 + production fail-fast）、**Phase 5 生产装配**（真实 provider 接线——phase5 review 遗留，release_gate_enforced 强制语义 + Secret/Artifact/Operations 装配）。

**拆解说明**：按设计 FEAT-P6-01..06 顺序拆为 TASK-001..006；TASK-006（生产装配）承接 phase5 review 登记的生产装配项（PostgresEncryptedSecretStore / S3CompatibleArtifactStore / Operations DBOS sysdb / release_gate_enforced=True），是其它 FEAT 跑真实 provider 的前提。


## 深度 Review 修复批次（2026-08-30，用户 Review 结论驱动）

| 级别 | 问题 | 修复 | 验证 |
|------|------|------|------|
| P0-1 | worker 每 1h exit 0 重启（`_mode_serve` 纯墙钟 idle_seconds=3600，Helm 未覆盖）——「4 Pod 稳定 0 重启」表述与实测冲突（restarts=3） | `--idle-seconds 0` 语义改为常驻（默认 0）+ entrypoint 注入 `FLUXION_WORKER_IDLE_SECONDS`（默认 0）+ 正值保留给测试基建 | 本地行为验证：idle=0 存活 / idle=3 定时 exit 0；k8s 0.2.3 部署 16min 0 重启（1h 观测点待后续确认） |
| P0-2 | P95 SLO 500→1000ms「先测后改阈值」未披露；契约表 500ms 与 evidence 矛盾；实测数字两处不一致 | 契约表（S-01/B-01）/evidence/`capacity_verify.py` 注释/契约文档 §2.1 四处显式披露「初始草案 500ms → 实测 580-688ms 不达标 → 用户确认放宽至 1000ms（校准而非保持）」；实测统一为 CLI 实跑输出 | `test_slo_thresholds_match_contract` 改为**解析契约文档**锚定（改文档不改代码会红，消除自引用） |
| P1-1 | `workflow_dbos.py:484` 引用未定义名 `_reference_releaser`——start 失败回滚路径 NameError（引用残留 + 原始错误被掩盖，phase3 存量） | 修复为 `get_reference_releaser()` + sync 直接调用（不 await） | 新增 `test_release_run_refs.py`（修复前复现 NameError / 修复后绿——2 用例） |
| P1-2 | S-09 durable/SoT「=0」恒真（标注表值域只有 Ephemeral/Cache，查表恒空） | 独立硬性规则：R1 durable/SoT 语义命名（AST，类/模块级标识符）+ R2 本地持久化通道（sqlite3.connect/shelve.open）——不从标注表查 | 探针验证：注入 LocalLedger/sqlite 调用三规则全命中，移除后恢复绿 |
| P1-3 | RPO 系列（dod-5/S-04/S-07）测自插 PG 行——不经过应用 durable 写通道 | S-04/dod-5 改经**真实治理事务**（commit_publication：audit_logs + publish_records + outbox 原子落库）；S-07 保持 SQL 提交但 evidence 口径如实标注 | chaos S-04/dod-5 重跑绿；任务文件口径同步 |
| P1-4 | E-03 二次投递无消费者（listen_queues=[]，断言空转恒真） | 二次投递后起**真实 worker serve**（轮询队列消费）再断言业务记录恰 1 行 + executions==1 | chaos E-03 重跑绿（3.56s） |
| P1-5 | 四类 legacy 扫描可逃逸（注释算使用/别名逃逸/self 前缀漏配/死代码正则） | plugin_type AST 化（Attribute 访问）；spec_json_get 别名追踪（`sj = x.spec_json`）；summarize 精确 `_summarize` 符号（AST，含方法调用）；删死代码 | 探针验证：注释提及的 dead enum/别名 get/_summarize 定义全命中 |
| P1-6 | Helm 默认值矛盾（postgresql.enabled=true 但依赖注释→默认装不上）；worker sysdb/registry 库混淆静默失败；serve --production env 覆盖显式 CLI 参数 | values 默认 `postgresql.enabled=false`（注释说明开启路径）；worker bootstrap 启动校验 resource_definitions 表存在（缺失 fail-fast）；显式 --registry-dsn 直接覆盖 env（不再 setdefault） | 部署走显式参数不变；bootstrap 校验经 0.2.3 镜像部署验证 |
| P2 批次 | E-02 无 attempts 断言；S-02 digest 只用测试进程自算；契约表引用错误（测试名/12+12）；E-05 只测私有方法；Column.copy 弃用；shadow 非幂等；read_path reset 语义；契约锚点自引用；B-05 措辞；S-03 PENDING 残留；scale 同池代理 | E-02 断言 executions==3；S-02 补被杀进程真实产物对拍（HTTP 响应字段）；E-05 改完整 resolve（资源段真实 + semantic 故障注入）；to_metadata + 索引后缀重命名 + dual_write 先清本 tenant 旧行；durable 契约 12+12 修正 + k8s gate 引用修正；阈值锚点解析契约文档；B-05 措辞诚实化（test double 披露）；S-03 setup 补 PENDING 清理；consistency 改双独立 engine | chaos 9 + scale 6 + dod 8 全绿（22 passed + 1 skip） |

### 复 Review 收尾（2026-08-30 第二轮）

复 review 结论：P0×2 / P1×6 全部确认真修复（含 k8s 0.2.3 已上线验证：worker cmdline 实参 `--idle-seconds 0`、4 Pod 0 重启）。两处「代码真但测试未覆盖」收尾已补：

- **rollover 幂等重跑用例**：`TestRolloverIdempotency.test_same_tenant_second_rollover_shadow_not_accumulating`——同 tenant 二次 rollover → 影子行清空重建（=当前源行数 1，非累积 2）、二次校验通过（6 passed）
- **审计负向探针回归**：`TestS09LocalStateAudit.test_s09_negative_probes_detected`——临时注入 LocalLedger/sqlite3.connect/未标注容器三类违规 → R1/R2/未标注断言全命中 + 审计 fail → 清理后恢复 pass（3 passed）
- **values.worker.bootstrap 死配置**：worker-deployment.yaml 显式注入 `FLUXION_WORKER_BOOTSTRAP`（此前仅 entrypoint 默认兜底生效）

**显式登记的未修缺口**（review P2 清单中不修的部分）：
- hard-delete 的 HTTP 409 出口：Console 无 hard-delete 端点（新增端点属产品功能，超出 phase6 加固范围）——DoD-14 的 409 语义目前只在 Registry 存储层（`active_reference_blocked`），HTTP 出口留待后续；
- chaos/scale 恢复 P95 样本量 1-3（统计学弱）：30s/60s 预算宽裕，真实能抓的是「悬挂不恢复」——已在 evidence 措辞如实化。

**存量债务清理**（2026-08-30 第三轮收尾）：
- `plugins/secret/postgres.py` 591→**489 行**（<500 架构行数限制）：AES-GCM 加解密原语抽至 `plugins/secret/crypto.py`（encrypt/decrypt/常量），master key 批量重加密抽至 `plugins/secret/rotation.py`（rotate_master_key_batch）——行为不变（secret 契约 9 passed + production assembly/secret governance/boundaries 19 passed + 架构行数测试通过）。

**前端 journey specs 迁移已完成**（2026-08-30 第三轮收尾）：
- `agent-golden-path`（5 类资源创建 + 用户/chat-link + 真实模型/MCP 旅程 + trace/evidence 断言）、`agent-error-path`、`console-real-http` 全部对齐 phase4/5 新 Console UI；`chat-nfr` 无需迁移；
- 修复的 3 个真实缺陷：①Console `get_resource(version=None)` 只查 latest published → 刚建 draft 详情 404（改 list_versions 取最新任意状态）；②插件 spec `name` 字段被 ModelProviderDefinition extra=forbid 拒绝（从 spec 移除）；③Semi Select agent 选择在整套件下偶发错选（下拉开启动画竞态）→ 验证-重试 helper；
- DoD-9 门禁已纳入**全量**真浏览器 journey specs（`npx playwright test frontend/e2e`，4 spec 8 用例全绿）+ pnpm test（8 passed / 207s）；UX journey 边界说明同步更新。

---
## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-01） | E2E | 真实 PostgreSQL + Runtime | TASK-001 | verified |
| S-02 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Runtime 真实进程 + Registry | TASK-002 | verified |
| S-03 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Workflow Engine + durable store | TASK-002 | verified |
| S-04 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | 真实 PostgreSQL | TASK-002 | verified |
| S-05 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-03） | E2E | 真实外部依赖（如有） | TASK-003 | verified |
| S-06 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-04） | E2E | 全套件边界 | TASK-004 | verified |
| S-07 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | E2E | 本地 k8s 真实集群 ≥2 RuntimeInstance 副本 | TASK-005 | verified |
| S-08 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | E2E | 已发布 Agent 运行中 → 停 Console | TASK-005 | verified |
| S-09 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | integration | Runtime 进程内全部 dict/list/cache | TASK-005 | verified |
| S-10 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-06） | integration | 真实 PG secret + S3/MinIO artifact + enforced gate + Operations 真实端点 | TASK-006 | verified |
| E-01 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | Cache + Registry | TASK-002 | verified |
| E-02 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | 外部 activity 真实调用 | TASK-002 | verified |
| E-03 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Workflow Engine + durable store | TASK-002 | verified |
| E-04 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | ArtifactStore（local-fs dev） | TASK-002 | verified |
| E-05 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | integration | SemanticStore SPI | TASK-002 | verified |
| E-06 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-02） | E2E | Workflow Engine + durable store | TASK-002 | verified |
| E-07 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | integration | production profile 启动装配路径 | TASK-005 | verified |
| E-08 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-05） | integration | production profile + RuntimeScheduler 本地实现 | TASK-005 | verified |
| B-01 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-01） | E2E | 真实 PostgreSQL + Runtime | TASK-001 | verified |
| B-02 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-04） | integration | Registry active pinned hard-delete | TASK-004 | verified |
| B-03 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-03） | integration | SurfaceEvidence 判定 | TASK-003 | verified |
| B-05 | phase6-hardening-scale-release.design.md#2.5 验收条件（FEAT-P6-03） | integration | SurfaceEvidence 判定（UNKNOWN 保守） | TASK-003 | verified |

> RULE-P6-01..05 与 NFR（CONSIST/REC/REL/TRACE/SEC/UX/LEGACY/DEL）映射到各 TASK 的 Acceptance-Refs；本表覆盖 design 全部 P0 场景。

---

## TASK-001: Capacity Profile V1 契约 + scale-test 复核

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-01, B-01, RULE-P6-01, NFR-P6-CONSIST-01, NFR-P6-CONSIST-02, NFR-P6-REC-01

### Description

锁定 Capacity Profile V1 契约（7 项：50 tenant / 1,000 users-per-tenant / 5,000 concurrent sessions / 10 Runtime replicas / 100 workflow concurrency / 5 MCP servers / 1,000 memories）为部署/验收事实（非运行态配置，架构规则 #2），载体 `docs/capacity/capacity-profile-v1.md` + `tests/scale/` 压测套件。scale-test 实测复核 SLO（NFR-P6-REC-01 Runtime 恢复 P95≤30s；Snapshot digest cross-pod 一致率=100%；Capability equivalence=100%），V1 值只紧不松（RULE-P6-01，B-01）。

### Checklist

- [x] 建 `docs/capacity/capacity-profile-v1.md` 契约文档（7 项 V1 值 + 只紧不松规则）
- [x] 建 `tests/scale/test_capacity_verify.py` scale-test 套件（批量并发 session 构造，非逐 session 串行）
- [x] `fluxion-capacity verify --profile v1` CLI（退出码 0=SLO 达标 / 非 0=未达标）
- [x] [S-01][E2E] 修改生产代码前，编写验收测试并记录 RED：真实 PG + Runtime 满负载（50 tenant / 5000 sessions）→ 全部 SLO 达标或记录实际瓶颈
- [x] [B-01][E2E] 并发 5,000 sessions 满负载 → SLO 仍达标或触发"只紧不松"评审
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 真实 PostgreSQL + Runtime | 全部性能 SLO 达标（成功率 100%、P95 满负载 ≤1000ms——**校准值**：初始草案 500ms，满负载实测 580-688ms 不达标后经用户确认放宽至 1000ms，见 docs/capacity/capacity-profile-v1.md §2 校准披露；测试口径为单进程集中承载 10× 单副本契约负载、digest 一致率=100%、capability equivalence=100%）；实测值记录进契约文档 | `backend/tests/scale/test_capacity_verify.py`（TestS01CapacityProfile.test_s01_full_load_slo / .test_s01_digest_cross_instance_consistency / .test_s01_capability_equivalence）；满负载经 `fluxion-capacity verify --profile v1` CLI | `python -m pytest backend/tests/scale/test_capacity_verify.py -q`（真实 PG fluxion_test）；满负载验收 `fluxion-capacity verify --profile v1`（退出码 0=SLO 达标） | verified |
| B-01 | E2E | 真实 PostgreSQL + Runtime | 5,000 sessions 满负载（50 tenant × 100 sessions）SLO 达标（P95≤1000ms 校准阈值，见 S-01 披露）或记录实际瓶颈并触发「只紧不松」评审 | `backend/tests/scale/test_capacity_verify.py`（TestB01FullLoad.test_b01_five_thousand_sessions）+ CLI 满负载运行实测记录（docs/capacity/capacity-profile-v1.md 实测表） | `FLUXION_SCALE_FULL=1 python -m pytest backend/tests/scale/test_capacity_verify.py -q`（全量 5000 sessions 门控执行）+ `fluxion-capacity verify --profile v1` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: `ModuleNotFoundError: No module named 'fluxion.services.capacity_verify'`（capacity verify 逻辑/CLI/契约文档均不存在） | PASS: 缩样套件 5 passed（`test_s01_full_load_slo` 成功率 100%+P95≤1000ms / `test_s01_digest_cross_instance_consistency` 50/50=100% / `test_s01_capability_equivalence` 100% / `test_slo_thresholds_match_contract` 7 项契约值+3 项 SLO 锚点）；`fluxion-capacity verify --profile v1` 满负载 exit=0（4/4 SLO OK，P95 646.4ms） | `backend/tests/scale/test_capacity_verify.py` L75-L97（断言成功率/P95/digest 一致率/equivalence rate）+ L134-L147（契约值锚点）；CLI 判定 `backend/src/fluxion/cli/capacity.py` L72-L93 | 真实 PG（fluxion_test：50 tenant 版本化资源 put/publish + 5,000 executions 写路径）+ 真实 Runtime（dev.echo 本地模型）+ 双独立 ContextResolver 对拍（架构规则 28） | verified |
| B-01 | 同上；首轮全量实测 P95=608ms 超初始 500ms 草案阈值（真实瓶颈暴露） | PASS: `FLUXION_SCALE_FULL=1` 全量 6 passed（B-01 满负载 5,000 sessions：成功率 100%、P95 633.5ms≤1000ms、digest 100%、equivalence 100%）；瓶颈分析记录进契约文档 §3（事件循环 CPU 串行化，单进程吞吐 225-275/s；pool 5→32 实测无改善排除连接池假设）；P95 阈值由初始草案 500ms 经用户确认放宽至 1000ms（CLI 实跑 4 轮 583.6/603.9/687.5/580.1ms + 校准验证轮 646.4ms；「校准而非保持」——RULE-P6-01 只紧不松约束 7 项容量值，SLO 阈值为实测校准初始值，落定后只紧不松） | `backend/tests/scale/test_capacity_verify.py` L100-L131（全量断言+实测 print）；实测记录 `docs/capacity/capacity-profile-v1.md` §3 实测表 | 同上（50 tenant × 100 sessions = 5,000 满负载，单进程集中承载 10× 副本契约负载——更严于契约分布口径） | verified |

### Log
- [2026-08-29] created (draft)
- [2026-08-30] started (in-progress)：Start Gate 通过（refresh ok / active marker（承接 TASK-006 变更归属 22 路径）/ session spec）；Acceptance Contract 已填测试文件与命令；RED 记录（capacity_verify 模块不存在）
- [2026-08-30] 实现：`services/capacity_verify.py`（V1_PROFILE 锚点 + 批量并发 load + digest/equivalence 对拍）+ `cli/capacity.py`（fluxion-capacity verify，退出码 0/非0）+ `docs/capacity/capacity-profile-v1.md`（7 项契约值 + SLO + 4 轮实测记录 + 瓶颈分析）+ registry engine 连接池可配置（FLUXION_PG_POOL_SIZE，诊断需要）
- [2026-08-30] B-01 瓶颈实测：串行单次 9ms 达标框架基线；满负载墙钟延迟来自单进程事件循环 CPU 串行化（非连接池——pool 32 实测反劣化 683.8ms）；单进程吞吐 225-275/s；P95 阈值按实测校准 1000ms（用户确认「把 P95 的限制改成 1000ms」）；全量 GREEN（CLI exit=0 + pytest 6 passed）→ completed (done)
- [2026-08-30] review P0-2 修复：S-01/B-01 契约表 500ms→1000ms 同步（原表与 evidence 矛盾）；实测数字统一为 CLI 实跑输出（583.6/603.9/687.5/580.1 + 验证轮 646.4）；500→1000 校准决策在契约表/evidence/契约文档三处显式披露（初始草案 500ms → 实测不达标 → 用户确认放宽——「校准而非保持」）

---

## TASK-002: Chaos 测试套件（runtime / workflow / storage）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#3.2 架构设计, phase6-hardening-scale-release.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-02, S-03, S-04, E-01..E-06, RULE-P6-02, NFR-P6-REC-02, NFR-P6-REL-01, NFR-P6-REL-02

### Description

进程级故障注入 Chaos 套件（pytest fixture + subprocess kill/restart + 环境扰动，D1 选型）覆盖 roadmap 故障清单：Runtime 组（kill/rolling restart/cache flush）、Workflow 组（backend restart/activity timeout/duplicate delivery/approval long wait）、Storage 组（PG failover/ArtifactStore 不可达/SemanticStore 降级）。关键真实边界不得 mock（RULE-P6-02）：Runtime 进程 / Store / 外部 activity。依赖真实 provider 装配（Depends TASK-006）。

### Checklist

- [x] 建 `tests/chaos/test_runtime_chaos.py` / `test_workflow_chaos.py` / `test_storage_chaos.py`（pytest fixture 进程起停 + 环境扰动）
- [x] `fluxion-chaos run --group runtime|workflow|storage` CLI（等价 `pytest -m chaos_<group>`）
- [x] [S-02][E2E] RED：kill Runtime → 重启恢复 P95≤30s + Snapshot digest 一致率=100%
- [x] [S-03][E2E] RED：重启 workflow backend → 恢复 P95≤60s，durable state 无丢失、无重复 side effect
- [x] [S-04][E2E] RED：PG 连接中断/failover → 已提交 durable state RPO=0
- [x] [E-01..E-06][integration/E2E] RED：cache flush 降级 L2、activity timeout fail policy、重复投递幂等、ArtifactStore 不可达、SemanticStore 降级、审批长时等待不死锁
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | Runtime 真实进程 + Registry | 恢复 P95≤30s；Snapshot digest 一致率=100% | `backend/tests/chaos/test_runtime_chaos.py`（TestS02RuntimeChaos.test_s02_kill_runtime_process_recovery_p95_and_digest_consistency） | `python -m pytest backend/tests/chaos/test_runtime_chaos.py -q`（真实 PG + uvicorn 子进程） | verified |
| S-03 | E2E | Workflow Engine + durable store | 恢复 P95≤60s；durable 无丢失；无重复 side effect | `backend/tests/chaos/test_workflow_chaos.py`（TestS03WorkflowChaos.test_s03_backend_restart_recovery_no_loss_no_duplicate） | `python -m pytest backend/tests/chaos/test_workflow_chaos.py -q`（真实 DBOS sysdb + worker 子进程） | verified |
| S-04 | E2E | 真实 PostgreSQL | 已提交 durable state RPO=0 | `backend/tests/chaos/test_storage_chaos.py`（TestS04StorageChaos.test_s04_pg_connection_interruption_rpo_zero） | `python -m pytest backend/tests/chaos/test_storage_chaos.py -q`（真实 PG：pg_terminate_backend 断连） | verified |
| E-01 | integration | Cache + Registry | cache flush → L1 miss 降级 L2，数据无损 | `backend/tests/chaos/test_runtime_chaos.py`（TestE01CacheFlush.test_e01_cache_flush_degrades_to_l2） | 同上 | verified |
| E-02 | integration | 外部 activity 真实调用 | activity timeout → fail policy 有界重试后显式失败，不悬挂 | `backend/tests/chaos/test_workflow_chaos.py`（TestE02ActivityTimeout.test_e02_activity_timeout_fail_policy） | 同 S-03 | verified |
| E-03 | E2E | Workflow Engine + durable store | 重复投递 → 幂等执行，仅一次 side effect | `backend/tests/chaos/test_workflow_chaos.py`（TestE03DuplicateDelivery.test_e03_duplicate_delivery_idempotent） | 同 S-03 | verified |
| E-04 | integration | ArtifactStore（local-fs dev） | 不可达 → 显式失败，不损坏已存 artifact | `backend/tests/chaos/test_storage_chaos.py`（TestE04ArtifactUnreachable.test_e04_artifact_store_unreachable_explicit_failure） | 同 S-04 | verified |
| E-05 | integration | SemanticStore SPI | 未配置/不可用 → no-memory 检索降级不崩溃 | `backend/tests/chaos/test_storage_chaos.py`（TestE05SemanticDegraded.test_e05_semantic_store_unavailable_degrades_to_no_memory） | 同 S-04 | verified |
| E-06 | E2E | Workflow Engine + durable store | 审批长时等待 → 无死锁，可恢复/可取消 | `backend/tests/chaos/test_workflow_chaos.py`（TestE06ApprovalLongWait.test_e06_approval_long_wait_no_deadlock_recoverable） | 同 S-03 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: 首轮 chaos 套件运行失败（基建缺失：进程路径错误/断言偏差） | PASS: 3 轮 SIGKILL→重启恢复 max≤30s（NFR-P6-REC-01）+ kill 前后 digest 3/3=100%（NFR-P6-CONSIST-01） | `test_runtime_chaos.py` TestS02RuntimeChaos（max(recoveries)≤30 + digest 全等断言） | 真实 Runtime API 进程（`fluxion serve` 子进程，SIGKILL）+ 真实 PG Registry + 独立 ContextResolver 对拍 | verified |
| E-01 | 同上 | PASS: cache flush 后 RevisionAwareResourceResolver 从 PG L2 回读等价资源（数据无损） | `test_runtime_chaos.py` TestE01CacheFlush（flush 前后资源相等断言） | 真实 TenantResourceCache（L1）+ 真实 PG Registry（L2） | verified |
| S-03 | FAIL: 首轮断言失败（step_c executions==2 暴露 at-least-once 语义理解偏差，修正为业务行数断言） | PASS: kill worker→recover ≤60s（NFR-P6-REC-02）+ step_a/b executions==1（durable 不重跑）+ 每步业务记录恰 1 行（NFR-P6-REL-02） | `test_workflow_chaos.py` TestS03WorkflowChaos（恢复计时 + executions + 3 行恰一断言） | 真实 DBOS sysdb（fluxion_workflow）+ 真实 worker 子进程（SIGKILL + recover）+ psycopg 直写业务记录表 | verified |
| E-02 | FAIL: `fail-flow` 场景/fail capability 不存在 | PASS: 永久失败 capability → DBOS 有界重试（3 attempts×0.2s）后 RUN_FAILED 显式退出（≤30s 不悬挂）+ 尝试留痕 | `test_workflow_chaos.py` TestE02ActivityTimeout | 真实 worker 子进程 + 真实 DBOS step 重试机制（fail policy） | verified |
| E-03 | FAIL: 首轮 worker rc=1（arguments 缺失致模板渲染失败） | PASS: 同 execution 二次 start → 同 run_id + 业务记录恰 1 行 executions==1（幂等，仅一次 side effect） | `test_workflow_chaos.py` TestE03DuplicateDelivery | 真实 DBOS sysdb + SetWorkflowID 幂等语义 + 业务记录表 | verified |
| E-06 | 同上（复用 approval-flow 场景，chaos 侧新增长时等待断言） | PASS: 审批挂起 3s 无人处理 → run 状态可查询 + worker 存活（无死锁）→ approve signal → finalize 完成 | `test_workflow_chaos.py` TestE06ApprovalLongWait | 真实 worker 子进程 + 真实 DBOS human_task durable 等待 + signal | verified |
| S-04 | FAIL: 首轮断连后查询抛 ConnectionDoesNotExistError（测试 engine 缺 pre_ping——暴露真实配置语义） | PASS: 定向 pg_terminate_backend（application_name 隔离）→ 已提交行=1（RPO=0，NFR-P6-REL-01 App 层）+ 恢复后写可用（2 行） | `test_storage_chaos.py` TestS04StorageChaos（RPO 计数断言） | 真实 PostgreSQL（pg_terminate_backend 故障注入，独立管理连接） | verified |
| E-04 | FAIL: 首轮 NotADirectoryError 在 pytest.raises 之外抛出 | PASS: 不可达路径（父目录为文件）→ initialize/put 显式异常；已存 artifact get 不损坏 | `test_storage_chaos.py` TestE04ArtifactUnreachable | 真实 local-fs ArtifactStore + 真实文件系统故障注入 | verified |
| E-05 | 同上 | PASS: SemanticStore 不可达（不存在库）→ Memory 段降级 no-memory manifest（content_hash="unavailable"+entry_refs=[]+truncated=True），不崩溃 | `test_storage_chaos.py` TestE05SemanticDegraded | 真实 ContextResolver + 不可达 PG engine（真实连接失败路径） | verified |

### Log
- [2026-08-29] created (draft)
- [2026-08-30] started (in-progress)：Start Gate（补绑 fluxion-workflow-capability spec）；Acceptance Contract 已填（9 场景三文件）
- [2026-08-30] GREEN：三组套件 9/9 passed（runtime 2 + workflow 4 + storage 3）+ `fluxion-chaos run --group X` CLI 三组全过（等价 pytest -m chaos_<group>）+ fixture 扩展（fail capability/fail-flow 场景）→ completed (done)

---

## TASK-003: One-time Migration / Rollover（仅真实外部依赖）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#3.4 接口设计, phase6-hardening-scale-release.design.md#4.4 数据迁移
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-05, B-03, B-05, RULE-P6-03

### Description

One-time Migration/Rollover：SurfaceEvidence（active_record_count/active_token_count/enabled_integration_count/traffic_30d/last_used_at/known_external_consumer/public_stable_contract/evidence_source）客观判定三级分类（EXTERNAL_ACTIVE/RESET_ALLOWED/UNKNOWN）。仅真实外部依赖 → 双写→一致性校验→切换→删旧（S-05）；无外部依赖 → 直接 reset 不建双写（B-03）；**UNKNOWN 一律按 EXTERNAL_ACTIVE，禁止 destructive reset**（B-05，RULE-P6-03 保守默认）。`fluxion-migrate rollover/cleanup` CLI。

### Checklist

- [x] 实现 SurfaceEvidence 判定（三级分类 + UNKNOWN 保守默认）
- [x] `fluxion-migrate rollover`（双写→校验→切换，仅真实外部依赖）/ `fluxion-migrate cleanup`（legacy 删除）
- [x] [S-05][E2E] RED：真实外部依赖 → 双写→校验→切换→删旧全流程
- [x] [B-03][integration] RED：无外部依赖 → 直接 reset 不建双写
- [x] [B-05][integration] RED：证据不足（UNKNOWN）→ 禁止 destructive reset
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实外部依赖（如有）：真实 PG 表上的活跃 token/记录证据（SurfaceEvidence 由真实 SQL 查询得出） | 双写（影子拷贝）→一致性校验→切换（migration_records）→删旧全流程成功；旧路径删除后无回归（读路径走 shadow） | `backend/tests/integration/test_migration_rollover.py`（TestS05Rollover.test_s05_rollover_full_flow_with_external_active_evidence） | `python -m pytest backend/tests/integration/test_migration_rollover.py -q`（真实 PG fluxion_test） | verified |
| B-03 | integration | SurfaceEvidence 判定（真实 PG：空 surface 零证据） | 无外部依赖 → RESET_ALLOWED → 直接 reset 不建双写（无影子表/无 dual-write 记录） | 同上（TestB03ResetAllowed.test_b03_no_external_dependency_direct_reset） | 同上 | verified |
| B-05 | integration | SurfaceEvidence 判定（UNKNOWN：证据字段缺失且无法确认） | 禁止 destructive reset（MigrationRefusedError）；UNKNOWN 按 EXTERNAL_ACTIVE 保守处理 | 同上（TestB05UnknownConservative.test_b05_unknown_refuses_destructive_reset） | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | FAIL: `ModuleNotFoundError: No module named 'fluxion.services.migration_rollover'`（rollover 引擎/SurfaceEvidence/CLI 均不存在） | PASS: `test_s05_rollover_full_flow_with_external_active_evidence`——真实活跃 token 证据 → EXTERNAL_ACTIVE → 影子拷贝（checksum sha256）→ 校验（行数+逐行 checksum 双侧一致）→ 切换（migration_records status=switched/completed）→ 删旧（legacy 行数=0）→ 读路径='shadow'；5/5 passed | 断言位置：test 文件 L127-L175（classification/dual_written/verified/switched/legacy=0/shadow 行=1）；引擎判定 `migration_rollover.py` rollover() L221-L266 | 真实 PG fluxion_test（chat_access_tokens 真实活跃 token 行 + 影子表 DDL + migration_records 事实；SurfaceEvidence 由真实 SQL 查询） | verified |
| B-03 | 同上 | PASS: `test_b03_no_external_dependency_direct_reset`——零证据 → RESET_ALLOWED → reset（dual_written=False、switched=False、影子表不存在） | 断言位置：test 文件 L189-L216（information_schema 断言影子表未创建） | 真实 PG（channel_identities surface 零证据真实 SQL 判定） | verified |
| B-05 | 同上 | PASS: `test_b05_unknown_refuses_destructive_reset`——证据字段缺失（None）→ UNKNOWN → reset 抛 `MigrationRefusedError`（match "UNKNOWN"）；UNKNOWN 按 EXTERNAL_ACTIVE 保守处理 | 断言位置：test 文件 L219-L272（pytest.raises + classify UNKNOWN 双断言） | SurfaceEvidence 客观字段（缺失=无法确认）+ 保守默认门禁 | verified |

### Log
- [2026-08-29] created (draft)
- [2026-08-30] started (in-progress)：Start Gate 通过；Acceptance Contract 已填；设计定型——通用 Rollover 引擎（影子表双写→校验→切换→删旧 + migration_records 事实）挂真实 surface（token=chat_access_tokens / channel=channel_identities / data=legacy l2 session_memory）
- [2026-08-30] GREEN：`services/surface_evidence.py`（8 客观字段 + 三级分类 + UNKNOWN 保守）+ `services/migration_rollover.py`（RolloverService：rollover/reset/cleanup/read_path + 影子表逐列拷贝 + sha256 逐行 checksum + migration_records 阶段事实）+ `cli/migrate.py`（fluxion-migrate rollover/cleanup，退出码 0/非0）+ schema migration_records 表；测试 5/5 passed（真实 PG）；CLI 冒烟（空 tenant → RESET_ALLOWED 直接 reset）→ completed (done)

---

## TASK-004: Final DoD 自动化验收套件

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-005, TASK-006
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#2.5.3 非功能指标, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#4.2 发布与回滚
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-06, B-02, RULE-P6-04, NFR-P6-LEGACY-01..04, NFR-P6-DEL-01, NFR-P6-TRACE-01, NFR-P6-UX-01

### Description

Final DoD 14 项自动化验收（每项一个 verifier）+ 四类 legacy 静态扫描（dead PluginType / runtime raw `spec_json.get` / pseudo `_summarize` / permanent legacy path，D5 选型）+ active pinned hard-delete=0 断言。`fluxion-dod verify` CLI（14/14 全过才 Release，RULE-P6-04；任一失败阻断 S-06）。trace completeness≥99%（NFR-P6-TRACE-01）+ UX journey≥95%（NFR-P6-UX-01）纳入门禁。B-02：active pinned resource hard-delete 被拒（409）。

### Checklist

- [x] 建 `tests/dod/` 14 项 verifier + `scripts/static_scan/` 四类扫描（spec_json_get/summarize_scan/legacy_path_scan/plugin_type_scan）
- [x] `fluxion-dod verify` CLI（0=14/14 全过 / 非 0=存在失败）
- [x] [S-06][E2E] RED：Final DoD 14 项全过 → Release 门禁通过
- [x] [B-02][integration] RED：active pinned hard-delete → 409 拒绝
- [x] NFR-P6-LEGACY-01..04 / DEL-01 / TRACE-01 / UX-01 断言接入门禁
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 全套件边界（dod + chaos 三组 + scale + 静态扫描 + 前端维护套件 + 真浏览器） | 14 项全过；Release 门禁通过（`fluxion-dod verify` exit 0） | `backend/tests/dod/test_dod_verify.py`（9 verifier：DoD 1-14 映射）+ chaos/scale 套件编排（CLI `-m 'dod or chaos_* or scale'`） | `fluxion-dod verify`（实测 23 passed + 1 skipped（B-01 全量门控）/ 95.81s / exit 0） | verified |
| B-02 | integration | Registry active pinned hard-delete（真实 PG） | active 引用中的 pinned 版本 hard-delete → `active_reference_blocked` 拒绝（409 语义） | `backend/tests/dod/test_dod_verify.py`（TestDod14HardDelete.test_dod_14_active_pinned_hard_delete_rejected） | `python -m pytest backend/tests/dod/test_dod_verify.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | FAIL: dod 套件不存在（首跑 `pytest backend/tests/dod` no tests）+ LEGACY-02/04 存量违规暴露（resolver raw spec_json.get ×2 + `_strip_legacy_status_keys` permanent 兼容读）+ 前端旧 Console journey spec 漂移（5 spec 指向已删除 UI 元素） | PASS: `fluxion-dod verify` → 23 passed + 1 skipped（B-01 全量门控）/ 95.81s / **exit 0——Release 门禁通过**。DoD 14 项全绿：1-2 digest/equivalence 对拍、3 无状态重建执行、5 RPO 轻量、7 span 采样完整率 100%（≥99%）、8 tenant escape=0 正反断言、9 前端维护套件（console 28 文件 101 tests + chat vitest 全过）+ chat-nfr 真浏览器 spec、10-13 四类静态扫描=0（LEGACY-02 修复：resolver 经 `_SkillSpecView` 类型化读取；LEGACY-04 修复：删除 `_strip_legacy_status_keys` 兼容读，legacy 键显式拒绝）、14 active pinned hard-delete 拒绝 | `backend/tests/dod/test_dod_verify.py`（TestDod01/02/03/05/07/08/09/10LegacyScans/14）；CLI 编排 `backend/src/fluxion/cli/dod.py`（`-m 'dod or chaos_* or scale'`） | 真实 PG（fluxion_test）+ 真实 trace span（InMemory exporter 挂全局 SDK provider）+ 真实源码 AST 扫描 + 真实前端套件（vitest 产品契约 + Playwright chat-nfr 真浏览器）+ chaos/scale 完整故障注入经 CLI 同门禁 | verified |
| B-02 | 同上（dod 套件不存在） | PASS: `test_dod_14_active_pinned_hard_delete_rejected`——tombstone 后 + active reference pin → `pytest.raises(RegistryStoreError, match='active_reference_blocked')` | `test_dod_verify.py` TestDod14HardDelete（L400-L445） | 真实 PG：commit_publication TOMBSTONE 治理事务 + add_active_reference + hard_delete 三重 guard 拒绝路径 | verified |

> UX journey 边界说明（已更新）：`frontend/e2e/` 的 journey specs（agent-golden-path / agent-error-path / console-real-http / chat-nfr）已全部对齐 phase4/5 新 Console UI（迁移完成，2026-08-30）——DoD-9 门禁纳入**全量**真浏览器 journey specs（`npx playwright test frontend/e2e`）+ pnpm test，全绿 = journey success 100% ≥95%。

### Log
- [2026-08-29] created (draft)
- [2026-08-30] started (in-progress)：Start Gate；实现 14 项 verifier（tests/dod）+ 四类静态扫描（scripts/static_scan/scan_legacy.py）+ fluxion-dod CLI 编排门禁
- [2026-08-30] legacy 整改（LEGACY=0）：①resolver.py raw spec_json.get ×2 → `_SkillSpecView`（SkillDefinition extra=ignore 读取视图）类型化读取；②删除 agents/definitions.py `_strip_legacy_status_keys` permanent 兼容读（P1C-01 存量兼容策略——项目未上线无生产存量数据，legacy 键改为 extra=forbid 显式拒绝；对应 8 个测试 fixture/断言同步更新）；③扫描白名单：PluginType.HOOK（ADR-EXT-001 保留类型，经 HookRegistryProtocol）+ 迁移工具文件（agents/migration.py、migration_rollover.py——one-time 语义非 permanent 兼容路径）
- [2026-08-30] UX journey 边界决策：5 个旧 Console playwright spec 指向已删除 UI（存量漂移，非本次回归）——显式缺口登记；DoD-9 门禁 = 现行维护套件（pnpm test 全绿 + chat-nfr 真浏览器）
- [2026-08-30] GREEN：dod 套件 9/9（77.33s，含 UX journey）+ `fluxion-dod verify` 全门禁 exit 0（23 passed + 1 skipped，95.81s）→ completed (done)

---

## TASK-005: 真实部署 Gate 与生产运行边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: phase6-hardening-scale-release.design.md#2.3 功能方案, phase6-hardening-scale-release.design.md#2.5 验收条件, phase6-hardening-scale-release.design.md#3.1 方案选型, phase6-hardening-scale-release.design.md#4.1 部署架构, phase6-hardening-scale-release.design.md#4.3 监控告警
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-07, S-08, S-09, E-07, E-08, RULE-P6-05

### Description

真实部署 Gate 与生产运行边界（P0-3/P0-4/P0-5 + Gate G3/G5/G7）：①本地 k8s ≥2 副本部署 Gate（rolling restart/kill pod，Snapshot digest 一致率=100%、committed durable state RPO=0，S-07）；②停 Console 后已发布 Agent 继续运行（Runtime 不调 Console API 获取配置 truth，G7/ARCH-14，S-08）；③本地状态审计脚本（全部标注 Ephemeral/Cache/Durable/SoT，Durable/SoT 本地命中=0，G5，S-09）；④production profile 禁止 InMemory Trace/Approval/Eval 唯一实现 fail-fast（P0-5，E-07）；⑤RuntimeScheduler 本地实现限定 test/dev fail-fast（P0-4，E-08）。

### Checklist

- [x] 本地 k8s 部署 Gate（≥2 RuntimeInstance 副本 + rolling restart/kill pod + 扩缩容）
- [x] 停 Console 后已发布 Agent 继续运行验证（G7/ARCH-14）
- [x] 本地状态审计脚本（Ephemeral/Cache/Durable/SoT 标注 + 命中=0）
- [x] production profile fail-fast 守卫：InMemory Trace/Approval/Eval 唯一实现 + RuntimeScheduler 本地实现（P0-4/P0-5）
- [x] [S-07][E2E] RED：k8s ≥2 副本 rolling restart/kill → digest 一致率=100%、RPO=0、facts 零丢失
- [x] [S-08][E2E] RED：停 Console → 已发布 Agent 继续执行（不调 Console API truth）
- [x] [S-09][integration] RED：local state audit 扫描 → Durable/SoT 本地命中=0
- [x] [E-07][integration] RED：production profile 下 InMemory Trace/Approval/Eval 唯一实现 → 启动 fail-fast
- [x] [E-08][integration] RED：production + 本地 scheduler → fail-fast
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 本地 k8s 真实集群 ≥2 副本（OrbStack） | rolling restart + kill pod → digest 一致率=100%（逐 Pod 内解析对拍）；RPO=0（kill 前提交 facts 后仍在）；facts 零丢失（关键表行数不变） | `backend/tests/integration/test_k8s_gate.py`（TestS07DeploymentGate.test_s07_rolling_restart_digest_consistency_rpo_zero——kill 副本与 rolling restart 合并单用例顺序执行） | `FLUXION_K8S_TEST=1 python -m pytest backend/tests/integration/test_k8s_gate.py -q`（依赖 helm 部署） | verified |
| S-08 | E2E | 停 Console + 已发布 Agent（真实 PG） | Console 进程不存在时已发布 Agent 执行成功；Runtime API 无 Console 路由（不调 Console truth，G7/ARCH-14） | `backend/tests/integration/test_production_boundaries.py`（TestS08ConsoleIndependence.test_s08_runtime_executes_without_console） | `python -m pytest backend/tests/integration/test_production_boundaries.py -q`（真实 PG） | verified |
| S-09 | integration | Runtime 进程内全部 dict/list/cache（AST 扫描 backend/src/fluxion/runtime/） | 全部标注 Ephemeral/Cache；Durable/SoT 本地命中=0；Scheduler/Trace/Approval/Eval/Workflow 全覆盖（G5） | `backend/tests/integration/test_local_state_audit.py`（TestS09LocalStateAudit.test_s09_all_local_state_annotated_no_durable_sot）+ `scripts/local_state_audit.py` | `python -m pytest backend/tests/integration/test_local_state_audit.py -q` | verified |
| E-07 | integration | production profile 装配路径（真实 PG） | InMemory Trace/Approval/Eval/Secret 逐项 fail-fast；durable 装配放行 | `backend/tests/integration/test_production_boundaries.py`（TestE07ProductionFailFast.test_e07_inmemory_unique_implementations_fail_fast） | 同 S-08 | verified |
| E-08 | integration | production + RuntimeScheduler 本地 `_tasks` 实现 | fail-fast 拒绝启用（仅 test/dev 放行，REQ-SCH-001） | `backend/tests/integration/test_production_boundaries.py`（TestE08SchedulerGuard.test_e08_local_scheduler_production_fail_fast） | 同 S-08 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | FAIL: 首轮 k8s gate 部署库无 schema（PG serving initialize() no-op + 迁移链只覆盖 phase1——真实 schema 漂移暴露）；修复后仍有 rollout/terminating pod 竞态 | PASS: 41.86s 全流程——seed agent → 逐 Pod digest 基线一致 → kill 1 副本 + rollout restart 全滚动 → 新副本逐 Pod digest 一致且与基线相等（一致率=100%，架构规则 28）+ kill 前提交 audit_logs fact 行仍在（RPO=0）+ gate agent 2 条 resource_definitions 零丢失 | `test_k8s_gate.py` TestS07DeploymentGate（digest 对拍/RPO 计数/facts 计数断言） | OrbStack 真实集群：API Deployment 2 副本 + worker 2 副本 + alembic pre-upgrade 迁移 Job + 共享宿主 PG fluxion 库 + Pod 内真实 ContextResolver 解析 | verified |
| S-08 | 同期基建缺失 | PASS: Runtime API 独立进程（Console 自始不存在）→ Console 路由 404（无代理转发）+ 已发布 Agent HTTP Execution 成功（配置 truth=Registry） | `test_production_boundaries.py` TestS08ConsoleIndependence（404 断言 + run code==0） | 真实 Runtime API 子进程 + 真实 PG + 真实 HTTP Execution | verified |
| S-09 | FAIL: 审计脚本不存在（首跑 26 容器未标注） | PASS: `scripts/local_state_audit.py` AST 扫描 21 容器全标注（16 Ephemeral + 5 Cache），Durable/SoT 命中=0，Scheduler/Trace/Workflow Stub 覆盖检查通过 | `test_local_state_audit.py`（audit() 三断言 + 覆盖检查） | 真实源码 AST 扫描（backend/src/fluxion/runtime/ 全部 dict/list/set/deque 容器） | verified |
| E-07 | 同上 | PASS: durable 四件套装配放行 + InMemory Trace/Approval/Eval/Secret 逐项替换 → ProductionProfileError（错误点名违规组件） | `test_production_boundaries.py` TestE07ProductionFailFast（4 项逐项 raises + durable 放行） | 真实 PG engine 构造的 durable store 实例 + 真实 InMemory 实例装配路径 | verified |
| E-08 | FAIL: RuntimeScheduler 无 profile 守卫 | PASS: `RuntimeScheduler(runtime, profile="production")` → SchedulerProfileError（REQ-SCH-001）；dev profile 放行 | `test_production_boundaries.py` TestE08SchedulerGuard + `runtime/scheduler.py` 构造守卫 | 真实构造路径（production 拒绝/dev 放行） | verified |

### Log
- [2026-08-29] created (draft)
- [2026-08-30] started (in-progress)：Start Gate；实现 14 项 verifier（tests/dod）+ 四类静态扫描（scripts/static_scan/scan_legacy.py）+ fluxion-dod CLI 编排门禁
- [2026-08-30] legacy 整改（LEGACY=0）：①resolver.py raw spec_json.get ×2 → `_SkillSpecView`（SkillDefinition extra=ignore 读取视图）类型化读取；②删除 agents/definitions.py `_strip_legacy_status_keys` permanent 兼容读（P1C-01 存量兼容策略——项目未上线无生产存量数据，legacy 键改为 extra=forbid 显式拒绝；对应 8 个测试 fixture/断言同步更新）；③扫描白名单：PluginType.HOOK（ADR-EXT-001 保留类型，经 HookRegistryProtocol）+ 迁移工具文件（agents/migration.py、migration_rollover.py——one-time 语义非 permanent 兼容路径）
- [2026-08-30] UX journey 边界决策：5 个旧 Console playwright spec 指向已删除 UI（存量漂移，非本次回归）——显式缺口登记；DoD-9 门禁 = 现行维护套件（pnpm test 全绿 + chat-nfr 真浏览器）
- [2026-08-30] GREEN：dod 套件 9/9（77.33s，含 UX journey）+ `fluxion-dod verify` 全门禁 exit 0（23 passed + 1 skipped，95.81s）→ completed (done)
- [2026-08-30] started (in-progress)：Start Gate；发现并修复真实缺口——①k8s 部署库 schema 漂移（alembic 迁移链仅 phase1，phase2-6 表缺失；env.py psycopg2→psycopg3 修正 + autogenerate e5189f5b1ed1 补齐 12 表漂移 + Helm pre-install/pre-upgrade 迁移 Job）②RuntimeScheduler 无 production 守卫（E-08 实现）；③`scripts/local_state_audit.py` G5 审计
- [2026-08-30] GREEN：S-07 k8s Gate 41.86s（digest 一致率=100% + RPO=0 + facts 零丢失）+ S-08/S-09/E-07/E-08 全绿；回归 chunk1 150 passed + chunk2 279 passed/1 skipped → completed (done)

---

## TASK-006: Phase 5 生产装配（Secret/Artifact/ReleaseGate/Operations 接线）

- **Status**: done
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

- [x] dev_bundle/production 装配 `PostgresEncryptedSecretStore`（替换内存 store；Master Key env + 注册表 active key 初始化）
- [x] 装配 `S3CompatibleArtifactStore`（S3/MinIO endpoint + timeout/retry/fail policy）
- [x] 装配 Operations 运营端点（`OperationsApplicationService` DSN 传入 console app，Queues/Workers 真实数据）
- [x] 装配 `release_gate_enforced=True`（无 gate 参数 publish fail-closed，38_001）
- [x] production profile 守卫：InMemory Secret/Approval/Eval/Trace 唯一实现 fail-fast（承接 FEAT-P6-05 ④）
- [x] k8s 基建：构建 Docker 镜像并载入本地 k8s（OrbStack，`docker build` + 更新 helm `image.tag`）
- [x] k8s 基建：扩 Helm Chart——新增 `fluxion-workflow-worker` Deployment（DBOS 执行进程）+ Runtime/Console API `replicaCount ≥2`
- [x] k8s 基建：共享 PG/Redis 可达（`externalDatabase` → 宿主 PG `mmuser` + DBOS sysdb 同库；Redis 若需——当前生产 bundle 未消费 Redis，暂未接线）
- [x] [S-10][integration] 修改生产代码前，编写验收测试并记录 RED：生产装配集成测试——Secret 落 PG 密文 + S3 artifact + enforced gate + Operations 真实端点（部署到 k8s Pod 上验证）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | integration | 真实 PG secret（AES-256-GCM）+ S3/MinIO artifact + enforced gate + Operations 真实端点 | Secret 落 PG 密文、artifact 落 S3 + metadata、gate 强制（无 gate 参数 publish fail-closed 38_001）、Operations 返回真实 DBOS 状态、production InMemory fail-fast | `backend/tests/integration/test_production_assembly.py`（TestS10ProductionAssembly.test_secret_persisted_encrypted_in_pg / .test_artifact_put_get_with_s3_and_metadata / .test_release_gate_enforced_publish_fail_closed / .test_operations_endpoints_return_real_dbos_state / .test_production_inmemory_fail_fast）+ `backend/tests/contract/test_durable_stores.py`（双库契约）+ `backend/tests/unit/test_production_profile.py`（守卫） | `FLUXION_REQUIRE_POSTGRES_CONTRACT=1 python -m pytest backend/tests/integration/test_production_assembly.py backend/tests/contract/test_durable_stores.py backend/tests/unit/test_production_profile.py -q` | verified |
| S-10（k8s Pod 部署级） | integration | 本地 k8s（OrbStack）真实集群：API Deployment ≥2 副本 + fluxion-workflow-worker Deployment ≥2 副本 + 共享宿主 PG | Pod 全部 Ready；Pod 内 /healthz 200；worker 就绪（DBOS launch）；镜像载入本地 k8s | `backend/tests/integration/test_k8s_deployment.py`（TestS10K8sDeployment.test_api_replicas_ready / .test_worker_replicas_ready / .test_pod_healthz） | `FLUXION_K8S_TEST=1 python -m pytest backend/tests/integration/test_k8s_deployment.py -q`（依赖先执行 `docker build` + `helm upgrade` 部署） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-10 | FAIL: `ModuleNotFoundError: No module named 'fluxion.api.production_bundle'`（生产装配点不存在——phase5 review 缺陷复现） | PASS: `test_secret_persisted_encrypted_in_pg`（`plaintext not in ciphertext` + nonce 12B + key_id/cipher_version 断言，L116-L131）；`test_artifact_put_get_with_s3_and_metadata`（put/get 往返 + artifact_metadata 行 size/sha256/active，L136-L162）；`test_release_gate_enforced_publish_fail_closed`（409 + code 38_001 + 「强制」+ 资源保持 DRAFT，L167-L199）；`test_operations_endpoints_return_real_dbos_state`（fluxion-workflow queue depth≥1 + workers 非空，L204-L262）；`test_production_inmemory_fail_fast`（ProductionProfileError）；`test_production_bundle_rejects_non_postgres_dsn`（sqlite DSN → 拒绝）——`FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 下 34 passed | 真实 PG（fluxion_test：secret_credentials 密文行 + registry publish 管道）+ 真实 MinIO docker（S3 blob + artifact_metadata）+ 真实 DBOS sysdb（fluxion_workflow + worker 子进程 worker-s10 + ENQUEUED 深度）+ 真实 HTTP（ASGITransport 统一 envelope）；composition root `api/production_bundle.py`（`release_gate_enforced=True` 装配点 L183-L184） | verified |
| S-10（双库契约） | FAIL: `ModuleNotFoundError: No module named 'fluxion.repositories.approval_store'`（durable store 不存在——fail-fast 守卫前提缺失） | PASS: `test_durable_stores.py` 24 项（sqlite 12 + postgres 12 双库各跑全量契约：TraceStore append/get/query/tenant 隔离/upsert 覆盖；ApprovalStore create/decide/consume + DB 级 CAS 双拒 + tenant 隔离；EvalRunStore put/get/list + 重复拒 + tenant 隔离） | `backend/tests/contract/test_durable_stores.py`（TestTraceStoreContract / TestApprovalStoreContract / TestEvalRunStoreContract） | 真实 SQLite 文件库 + 真实 PostgreSQL fluxion_test（FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控，规则 7 双库同 DDL） | verified |
| S-10（守卫） | FAIL: `ModuleNotFoundError: No module named 'fluxion.services.production_profile'`（production profile 守卫不存在） | PASS: `test_production_profile.py` 4 项（全 InMemory 拒绝并列出全部违规 / 部分 InMemory 拒绝 / Local secret 拒绝 / durable 放行） | `backend/tests/unit/test_production_profile.py`（TestProductionProfileGuard） | 真实类装配路径（InMemory/Local 与 durable 替身实例直接传入 `verify_production_assembly`）；守卫在生产 bundle 构造期调用（production_bundle.py L146-L152） | verified |
| S-10（k8s Pod 部署级） | FAIL（首轮部署）：API Pod CrashLoopBackOff（k8s 注入 `FLUXION_PORT=tcp://…` 污染端口约定）+ worker Pod argparse 报错（顶层参数须在子命令前）——部署基建真实缺陷 | PASS: `test_k8s_deployment.py` 3 项（API Deployment 2/2 Ready / worker Deployment 2/2 Ready / 逐 Pod 内 /healthz 200）；集群稳定运行 28min 0 重启；`/readyz` 200（production + PG 连通）；Operations 端点集群内可达且返回真实 queue 数据（fluxion-workflow，worker_concurrency=4） | `backend/tests/integration/test_k8s_deployment.py`（TestS10K8sDeployment.test_api_replicas_ready / .test_worker_replicas_ready / .test_pod_healthz） | OrbStack 本地 k8s 真实集群（v1.35.6）：镜像 fluxion-harness/fluxion:0.2.1 docker build 载入 + Helm 部署（namespace fluxion，API 2 副本 + workflow-worker 2 副本，共享宿主 PG mmuser@host.internal fluxion 库 registry+DBOS sysdb 同库，MinIO S3 装配） | verified |

### Log
- [2026-08-29] created (draft)
- [2026-08-30] started (in-progress)：Start Gate；实现 14 项 verifier（tests/dod）+ 四类静态扫描（scripts/static_scan/scan_legacy.py）+ fluxion-dod CLI 编排门禁
- [2026-08-30] legacy 整改（LEGACY=0）：①resolver.py raw spec_json.get ×2 → `_SkillSpecView`（SkillDefinition extra=ignore 读取视图）类型化读取；②删除 agents/definitions.py `_strip_legacy_status_keys` permanent 兼容读（P1C-01 存量兼容策略——项目未上线无生产存量数据，legacy 键改为 extra=forbid 显式拒绝；对应 8 个测试 fixture/断言同步更新）；③扫描白名单：PluginType.HOOK（ADR-EXT-001 保留类型，经 HookRegistryProtocol）+ 迁移工具文件（agents/migration.py、migration_rollover.py——one-time 语义非 permanent 兼容路径）
- [2026-08-30] UX journey 边界决策：5 个旧 Console playwright spec 指向已删除 UI（存量漂移，非本次回归）——显式缺口登记；DoD-9 门禁 = 现行维护套件（pnpm test 全绿 + chat-nfr 真浏览器）
- [2026-08-30] GREEN：dod 套件 9/9（77.33s，含 UX journey）+ `fluxion-dod verify` 全门禁 exit 0（23 passed + 1 skipped，95.81s）→ completed (done)：phase5 review 遗留「生产装配」登记（Secret/Artifact/Operations 无生产装配点 + release_gate_enforced 无处开启）；cf-task:plan 拆解后归位 TASK-006
- [2026-08-29] k8s 部署基建补强：本地 k8s（OrbStack）确认可用；扩 Helm Chart（worker Deployment + ≥2 副本 + 共享 PG/Redis）+ S-10 升级为 k8s Pod 部署级验证
- [2026-08-30] 核心装配落地：`api/production_bundle.py`（composition root：PG registry + PostgresEncryptedSecretStore + S3 + Operations sysdb + release_gate_enforced=True + 守卫）+ `repositories/`（PostgresTraceStore/PostgresApprovalStore/PostgresEvalRunStore，P0-5 durable adapter，范围决策用户已确认）+ `services/production_profile.py`（fail-fast 守卫）+ `runtime/workflow_worker_bootstrap.py`（生产 worker 装配）+ CLI `serve --production` + Helm Chart 扩展（worker Deployment + ≥2 副本 + DBOS sysdb DSN 推导）。顺手修正 `workflow_dbos.py` releaser 类型注解（声明 Awaitable 但两处调用点均不 await——按实际 sync 契约改 `Callable[..., None]`，行为不变）
- [2026-08-30] 回归：chunk1（unit/services/api/contract/resources/runtime/plugins/memory/users/agents/channel/architecture）313 passed + 1 deselected（`plugins/secret/postgres.py=591` 超行数为 phase5 存量债务，非本次引入）；chunk2（integration+e2e）269 passed / 4 skipped；ruff/mypy 新代码干净（workflow_dbos 存量 7 个 mypy 错误与本次无关，stash 验证）
- [2026-08-30] k8s 部署（OrbStack）：首轮两处真实缺陷——k8s service-link 注入 `FLUXION_PORT=tcp://…` 污染端口约定（改 `FLUXION_HTTP_PORT`）+ worker argparse 顶层参数须在子命令前（entrypoint 调序）；0.2.1 修复后 4 Pod 全就绪（API 2/2 + worker 2/2，`fluxion-workflow` queue worker_concurrency=4），集群稳定 28min 0 重启；`/readyz` PG 连通 + Operations 端点集群内返回真实 queue 数据
- [2026-08-30] GREEN：验收命令 1（`FLUXION_REQUIRE_POSTGRES_CONTRACT=1` assembly+contract+guard）34 passed；验收命令 2（`FLUXION_K8S_TEST=1` k8s 部署级）3 passed；S-10 全证据 verified → completed (done)
- [2026-08-29] started (in-progress)：Start Gate 通过（refresh ok / active marker / session spec）；Acceptance Contract 已填测试文件与命令
- [2026-08-29] RED 记录：三个测试文件全部 collection error（production_bundle / repositories.approval_store / services.production_profile 模块不存在）——生产装配点缺失缺陷复现。范围决策（用户确认）：补 PostgresTraceStore/PostgresApprovalStore/PostgresEvalRunStore durable adapter，否则 fail-fast 守卫与 k8s 生产启动互斥
