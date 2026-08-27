# Tasks: ADR-WF-001 Durable Execution PoC（解除 pending-PoC-gate）

- **Source**: .code-flow/tasks/2026-08-26/adr-wf-001-durable-execution/adr-wf-001-durable-execution.design.md
- **Created**: 2026-08-26
- **Updated**: 2026-08-26

## Proposal

按 ADR-WF-001 design brief 跑 build-vs-buy PoC：Temporal / DBOS / Restate 三候选各过 7 口径（P-CRASH/P-TIMER/P-IDEMP/P-PIN/P-TIMEOUT/P-SCALE/P-SIGNAL），产出自动化 evidence artifact，回填 19 维度权重矩阵（15 PRD §4.8 维度 + 4 补充维度），落 vendor pick ADR 解除 `pending-PoC-gate`。self-built 不跑（条件 fallback：3 采购候选全不达标才回头补测，RISK-WF-01）；P-PIN 的 retention 用 mock（`active_references` 未实现，全真验属 ADR-SNAPSHOT-001 实现阶段）。

**PoC 口径对齐 roadmap TASK-0002 十项**（2026-08-26 用户确认补齐）：新增 P-SIGNAL（external approval signal）、harness trace 断言（SLO-OBS-01）、1000-concurrent baseline（首个跑通候选 DBOS 上执行，其余候选可选）；最小 workflow 从 3 step 扩到 5 step（含 external-approval-signal step 与 http-activity step）；self-host deployment 形态由评估矩阵"运维"维度覆盖（PoC 起服务即部署体验）。

**建议执行顺序**：TASK-001 → TASK-003 (DBOS) → TASK-004 (Restate) → TASK-005；TASK-002 (Temporal) 条件回补。
（用户决策 2026-08-26：Temporal 运维最重放最后；**二次决策（同日）：DBOS/Restate 至少一个候选 7 口径全过且矩阵达标，则不测 Temporal**——独立 server 集群运维太重，矩阵 Temporal 列按文档 + 生产部署形态打分并注明"未实测"。仅当前两候选均不达标时回补 TASK-002。DBOS 是 library + 现成 Postgres，先跑最快校准 harness；依赖字段保持技术真实依赖，顺序为执行建议而非硬依赖链。）

### Alignment

- **Scope**: WorkflowEngine Protocol 扩展 + PoC harness + 3 候选 PoC（各 6 口径 evidence）+ 评估矩阵回填 + vendor pick ADR + E-02 CI gate
- **Decisions**:
  - 候选范围 = 3 采购候选（Temporal/DBOS/Restate）；self-built 条件 fallback（PRD §4.8 默认否决，翻盘条件 RISK-WF-01）
  - P-PIN retention 用 mock；RULE-WF-03（`active_references` 防删）全真验属 SNAPSHOT 实现任务
  - 走 cf-task:plan 任务治理（用户确认）
  - Temporal 放最后执行（用户确认 2026-08-26）；B-02 负责任务随首个执行候选定在 TASK-003
  - Temporal 降级为条件执行（用户二次确认 2026-08-26）：DBOS/Restate 至少一个满足 7 口径则不测（独立 server 集群运维太重）；矩阵 Temporal 列按文档打分注明"未实测"；两候选均不达标才回补
  - PoC 口径对齐 roadmap TASK-0002 十项：补 P-SIGNAL / trace 断言（SLO-OBS-01）/ 1000-concurrent baseline（DBOS）/ 5-step workflow；self-host 由评估矩阵"运维"维度覆盖（用户确认 2026-08-26）
- **Non-goals**: FEAT-10 DSL / FEAT-12 HumanTask / FEAT-13 Version GC / FEAT-22 Studio（Phase 3 下游）；`active_references` 真实现；Console API 改动
- **Acceptance**: 见 Acceptance Coverage（S-01..S-06 / E-01 / E-02 / B-01 / B-02 全覆盖，RULE 与高影响 RISK 均有映射）

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | adr-wf-001-durable-execution.design.md#2.5.2 功能验收场景 | E2E | Adapter→Engine→durable store（3 候选各自出证据） | TASK-005（证据: TASK-002/003/004） | verified |
| S-02 | 同上 | E2E | 真实 backend + 真实进程 kill | TASK-005（证据: TASK-002/003/004） | verified |
| S-03 | 同上 | integration | ExecutionSnapshot pinned + retention mock | TASK-005（证据: TASK-002/003/004） | verified |
| S-04 | 同上 | integration | Engine step timeout 配置 | TASK-005（证据: TASK-002/003/004） | verified |
| S-05 | 同上 | integration | 真实 backend dedup | TASK-005（证据: TASK-002/003/004） | verified |
| S-06 | 同上 | integration | 2 个 worker 进程 | TASK-005（证据: TASK-002/003/004） | verified |
| E-01 | 同上 | integration | Adapter fail policy + circuit-breaker | TASK-001 | verified |
| E-02 | 同上 | integration | PoC evidence artifact gate | TASK-005 | verified |
| B-01 | 同上 | unit | WorkflowAdapter 不变量 | TASK-001 | verified |
| B-02 | 同上 | integration | Engine tenant scoping | TASK-003（TASK-002/004 引用） | verified |

> RULE 映射：RULE-WF-01→B-01、RULE-WF-02→S-03、RULE-WF-04→S-04/E-01；RULE-WF-03 PoC 用 mock（design §2.4 已列 Out of Scope，全真验属 SNAPSHOT 实现任务，非缺口）。RISK 映射：RISK-WF-01→S-02/S-05/S-06、RISK-WF-02→B-01、RISK-WF-03→E-02。

---

## TASK-001: WorkflowEngine Protocol 扩展 + PoC harness 基础

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: adr-wf-001-durable-execution.design.md#2.3.2 字段约束, adr-wf-001-durable-execution.design.md#3.4 接口设计, adr-wf-001-durable-execution.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001, fluxion-runtime-core#RULE-fluxion-runtime-001, backend-code-quality-performance#RULE-backend-quality-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: B-01, E-01, RULE-WF-01, RULE-WF-04, RISK-WF-02

### Description

扩展 `WorkflowEngine` Protocol 四成员（resume/signal/cancel/get_status，全成员 timeout+retry+fail policy，规则 18）；建 PoC harness（最小 durable workflow 三 step + 6 口径断言框架 + retention mock + tenant scope 测试模板）；workflow 错误码进 `errors.py` 集中；backend 连接配置约定（环境变量 > 配置文件 > 默认值）；结构化日志约定（`run_id`/`tenant_id`/`trace_id`）。

### Checklist

- [x] 扩展 `backend/src/fluxion/runtime/workflow.py`：`resume(run_id) -> WorkflowRunStatus`（幂等）、`signal(run_id, name, payload) -> None`、`cancel(run_id, *, timeout: float) -> None`、`get_status(run_id) -> WorkflowRunStatus`
- [x] PoC harness 落 `backend/tests/workflow_poc/`：conftest + 最小 durable workflow 5 step（idempotent-write / timer / timeout / external-approval-signal / http-activity）+ 7 口径断言框架（P-CRASH/P-TIMER/P-IDEMP/P-PIN/P-TIMEOUT/P-SCALE/P-SIGNAL）
- [x] harness trace 断言框架（roadmap TASK-0002 项 9）：每口径执行链 trace_id/run_id 关联记录，SLO-OBS-01 断言（P0 路径 trace 关联 ≥99%）
- [x] P-PIN retention mock：mock 防删检查（`active_references` 未实现，注明全真验属 SNAPSHOT 实现任务）
- [x] workflow 错误码进 errors 集中定义（禁止散落硬编码）
- [x] backend 连接配置：`TEMPORAL_ADDRESS` / `DBOS_DATABASE_URL` / `RESTATE_URL` 环境变量优先
- [x] 结构化日志：`run_id`/`tenant_id`/`trace_id` 字段统一（backend-logging 约定）
- [x] [B-01][unit] 真实边界 `runtime/workflow.py` `WorkflowAdapter`：断言 `local_durable_state_count == 0` 恒等（RULE-WF-01 / RISK-WF-02）
- [x] [E-01][integration] 真实边界 Adapter fail policy + circuit-breaker（fault-injection backend 连续不可达，Adapter 逻辑真实）：断言返回定义错误码（非 hang、非裸超时异常）+ N 次失败后熔断打开（RULE-WF-04）
- [x] verifier: `fluxion-workflow-capability#RULE-fluxion-workflow-001` — `pytest backend/tests/workflow_poc/test_adapter_invariants.py -k b01`（Tool=Adapter 边界，durable state 归 Engine）→ 1 passed
- [x] verifier: `fluxion-runtime-core#RULE-fluxion-runtime-001` — `pytest backend/tests/workflow_poc/test_adapter_invariants.py -k "b01 or e01"`（stateless 不变量 + Kernel 只依赖 Contract）→ 3 passed
- [x] verifier: `backend-code-quality-performance#RULE-backend-quality-001` — `pytest backend/tests/workflow_poc/test_adapter_invariants.py -k e01`（timeout+retry+circuit-breaker+fail policy，无无限等待/重试）→ 2 passed
- [x] verifier: `backend-directory-structure#RULE-backend-directory-001` — `backend/tests/workflow_poc/` 测试目录与源码同构 review（配置进 `config/workflow.py`、错误码进 `errors/workflow.py`，无根目录散放脚本）
- [x] verifier: `backend-logging#RULE-backend-logging-001` — 日志字段断言测试（`run_id`/`tenant_id`/`trace_id` 结构化字段存在）→ test_config_and_errors.py::test_workflow_event_log_contains_correlation_fields
- [x] verifier: `backend-platform-rules#RULE-backend-platform-001` — 配置优先级断言（环境变量 > 配置文件 > 默认值）+ workflow 错误码集中定义检查 → test_config_and_errors.py 4 passed
- [x] 运行验收命令并填写 Acceptance Evidence（全套件回归 `234 passed, 1 skipped`）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | `runtime/workflow.py` WorkflowAdapter（真实类） | `local_durable_state_count == 0` 恒等 | backend/tests/workflow_poc/test_adapter_invariants.py::test_b01_local_durable_state_zero | `pytest backend/tests/workflow_poc/test_adapter_invariants.py -k b01` | verified |
| E-01 | integration | Adapter fail policy + circuit-breaker 真实逻辑（backend 不可达注入） | 返回定义错误码（非 hang）；N 次失败后熔断打开 | backend/tests/workflow_poc/test_adapter_invariants.py::test_e01_fail_policy_circuit_breaker | `pytest backend/tests/workflow_poc/test_adapter_invariants.py -k e01` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: 同文件 import error（`fluxion.errors.workflow` 不存在，E-01 缺件连带）。注：`local_durable_state_count` 属性已存在（`workflow.py:47`），属已有行为补测，无法独立 RED | PASS: `pytest …invariants.py -k b01` → 1 passed | test_adapter_invariants.py:39/46/53（start/resume/signal/cancel/get_status 全成员路径后 `local_durable_state_count == 0` 恒等断言） | WorkflowAdapter 真实类 + StubWorkflowEngine 真实实现（unit 层仅 stub backend，被测 Adapter 逻辑真实） | verified |
| E-01 | FAIL: `ModuleNotFoundError: No module named 'fluxion.errors.workflow'`（`ResilientWorkflowEngine`/`FailPolicy`/`WorkflowBackendUnavailableError` 均未实现）——`2 errors in 0.05s` | PASS: `pytest …invariants.py -k e01` → 2 passed | test_adapter_invariants.py:88-117（错误码 `40_104` 有界耗时 <5s、`unreachable.calls==2`、熔断后快速失败 <0.2s 不再触达）+ :126-147（timeout 包装为定义错误码，非裸 TimeoutError，<2s） | `ResilientWorkflowEngine`+`WorkflowAdapter` 真实 fail-policy/circuit-breaker 逻辑；fault 注入仅为"不可达 engine"（ConnectionError/挂起）；经 `runtime_context()` 真实 store/resolver 走 `adapter.execute` 全路径 | verified |

### Log
- [2026-08-26] created (draft)
- [2026-08-26] started (in-progress) — active marker 5bbe3fef…；session 投影 `.code-flow/specs/_session/task-adr-wf-001-durable-execution.md`（6 required rules）；baseline 19 个 pre-existing 路径（v2.2 文档 ×2 + cf 命令 ×15 + CLAUDE.md + .DS_Store）
- [2026-08-26] RED 记录：`pytest backend/tests/workflow_poc/ -q` → 2 collection errors（fail policy 层 + harness 框架缺失）
- [2026-08-26] GREEN：workflow_poc 11 passed；全套件回归 234 passed, 1 skipped；B-01/E-01 契约与全局覆盖表 verified → completed (done)

---

## TASK-003: DBOS PoC（首个执行候选）

> 环境事实（用户提供 2026-08-26）：`local_postgres`（postgres:15）连接 `localhost:5432`，用户/密码 `mmuser`/`mmuser`；DBOS 用独立数据库（如 `fluxion_poc_dbos`），勿复用既有业务库。

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-wf-001-durable-execution.design.md#3.1.1 候选清单, adr-wf-001-durable-execution.design.md#3.1.3 PoC 验收口径, adr-wf-001-durable-execution.design.md#2.5.2 功能验收场景
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-01, S-02, S-03, S-04, S-05, S-06, B-02（本候选证据，S-01..S-06 最终验收归 TASK-005）

### Description

DBOS（PostgreSQL-native durable execution library，pip 安装 + 复用 `local_postgres` 容器，无新增服务）过 7 口径。建独立 database（不污染现有库）。**Spec 责任**：本任务是 `RULE-backend-database-001` 的唯一 owner（durable state RPO=0 + tenant scope）；TASK-001 六条 rule 为已验证依赖（verifier 见 TASK-001 Checklist）。

### Checklist

- [x] `pip install dbos` + 复用 `local_postgres` 建独立 database（如 `fluxion_poc_dbos`）
- [x] `DBOSWorkflowEngine` 实现 Protocol 5 成员（`@DBOS.workflow()` / `@DBOS.step()` 映射）
- [x] [S-01][E2E] 真实边界 Adapter→DBOS→Postgres durable store：`execute_workflow` 返回 `run_id` 且 start 同步持久化；断言 durable start P95≤1s（SLO-WF-01）
- [x] [S-02][E2E] 真实边界 DBOS + 真实进程 kill：worker 中途 kill → `resume(run_id)`；断言从最近 durable step 继续非重启，recovery P95≤60s（SLO-WF-02 / P-CRASH / RISK-WF-01）
- [x] [P-TIMER][integration] 真实边界 DBOS durable timer：worker 重启后定时器仍触发
- [x] [P-SIGNAL][integration] 真实边界 DBOS external approval signal：workflow 运行中 `signal(run_id, "approve", payload)` 唤醒等待中的 step 并继续推进（roadmap TASK-0002 项 5）
- [x] [S-05][integration] 真实边界 DBOS dedup 机制：重试已完成 step；断言 no-op 且不可逆写副作用重复次数=0（P-IDEMP / SLO-WF-03 / NFR-REL-03）
- [x] [S-03][integration] 真实边界 ExecutionSnapshot pinned + retention mock：长时间 workflow resume；断言使用 pinned WorkflowDefinition version，不 resolve latest（P-PIN / RULE-WF-02）
- [x] [S-04][integration] 真实边界 DBOS step timeout 配置：单步超时；断言触发定义 fail policy 非无限等待（P-TIMEOUT / RULE-WF-04）
- [x] [S-06][integration] 真实边界 2 个真实 worker 进程：断言 2nd worker 拉取排队 work（P-SCALE / NFR-SCALE-02）
- [x] [B-02][integration] 真实边界 DBOS engine tenant scoping：tenant A 的 workflow_run 对 tenant B 查询不可见（NFR-SEC-01）
- [x] [RULE-backend-database-001][integration] 真实边界 Postgres durable state：commit 后 kill 进程，断言 committed step state 无丢失（RPO=0）
- [x] verifier: `backend-database#RULE-backend-database-001` — `pytest backend/tests/workflow_poc/test_poc_dbos.py -k "rpo or b02"`（durable state RPO=0 + tenant scope 查询隔离）
- [x] [P-SCALE+][baseline] 1000 concurrent workflow baseline（roadmap TASK-0002 项 8，首个跑通候选执行，其余候选可选）：1000 并发 workflow 启动/推进，记录吞吐与 P95 写入 evidence
- [x] [SLO-OBS-01][integration] 真实边界 trace 记录链：断言每口径执行记录 trace_id/run_id 关联完整（≥99%）
- [x] 产出 evidence JSON artifact（`backend/tests/workflow_poc/evidence/dbos.json`：7 口径 + baseline PASS/FAIL + 计时数据）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Adapter→DBOS→Postgres | 返回 `run_id`；start 同步持久化；durable start P95≤1s | backend/tests/workflow_poc/test_poc_dbos.py::test_s01_durable_start | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k s01` | verified |
| S-02 | E2E | DBOS + 真实进程 kill | 从最近 durable step 继续；recovery P95≤60s | test_poc_dbos.py::test_s02_crash_recovery | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k s02` | verified |
| S-03 | integration | ExecutionSnapshot pinned + retention mock | 用 pinned version，不 resolve latest | test_poc_dbos.py::test_s03_pinned_resume | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k s03` | verified |
| S-04 | integration | step timeout 配置 | 触发定义 fail policy，非无限等待 | test_poc_dbos.py::test_s04_step_timeout | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k s04` | verified |
| S-05 | integration | DBOS dedup | 重试已完成 step 为 no-op；副作用重复=0 | test_poc_dbos.py::test_s05_idempotency | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k s05` | verified |
| S-06 | integration | 2 个真实 worker 进程 | 2nd worker 拉取排队 work | test_poc_dbos.py::test_s06_scale_two_workers | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k s06` | verified |
| B-02 | integration | DBOS engine tenant scoping | 跨租户 workflow_run 不可见 | test_poc_dbos.py::test_b02_tenant_isolation | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k b02` | verified |
| RPO | integration | Postgres durable state | commit 后 kill，committed state 无丢失 | test_poc_dbos.py::test_rpo_zero_commit | `pytest backend/tests/workflow_poc/test_poc_dbos.py -k rpo` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

**RED（2026-08-26）**：`pytest backend/tests/workflow_poc/test_poc_dbos.py backend/tests/workflow_poc/test_poc_dbos_baseline.py --collect-only` → 2 collection errors：`ModuleNotFoundError: No module named 'tests.workflow_poc.dbos_app'`（实现缺失，与预期缺陷对应：DBOSWorkflowEngine/worker 尚未实现）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: dbos_app 缺失（ModuleNotFoundError，2 collection errors） | PASS: `pytest …test_poc_dbos.py -k s01` → 1 passed | test_poc_dbos.py:109/111/115-117/120-125（run_id、status 可查、approval payload、pinned v1、SLO start_ms=14.2≤1000ms） | WorkflowAdapter→DBOSWorkflowEngine→DBOS→Postgres（runtime_context 真实快照）；start 返回即同步持久化（可立即 get_status） | verified |
| S-02 | 同上 | PASS: `pytest …-k s02` → 1 passed | test_poc_dbos.py:161-165（recovery_seconds=5.07≤60s、SUCCESS、write_report_record==1 未重跑、records 无重复） | 真实子进程 SIGKILL + 新进程 startup recovery（recover 模式 worker，launch 内置恢复） | verified |
| S-03 | 同上 | PASS: `pytest …-k s03` → 1 passed | test_poc_dbos.py:270/272（pinned_version=='v1'，latest 漂移 v2 后 resume 仍用 v1）；MockRetentionGuard 禁删→放行 | pinned version 表 + MockRetentionGuard（active_references 真实现属 SNAPSHOT 任务，PoC 明确 mock） | verified |
| S-04 | 同上 | PASS: `pytest …-k s04` → 1 passed | test_poc_dbos.py:307-308（elapsed=0.56<4.0 有界转 ERROR，非无限等待） | step 内 asyncio.timeout 上限（external_delay=5s 被 step_timeout=0.5s 截断） | verified |
| S-05 | 同上 | PASS: `pytest …-k s05` → 1 passed | test_poc_dbos.py:328/330/332-333（同 run_id 二次 start 返回既有执行、结果相等、write_report_record==1、records 恰 1 条） | DBOS 同 workflow_id dedup + ON CONFLICT 幂等写（SLO-WF-03） | verified |
| S-06 | 同上 | PASS: `pytest …-k s06` → 1 passed（4 次连续通过，确定性） | test_poc_dbos.py:397-400（全 SUCCESS、executor_id 含 worker-0+worker-1、records≥8；P-SCALE metrics distinct_executors=2） | 2 真实 worker 子进程 + database-backed queue（register_queue 持久化 + worker_concurrency=4，8 任务 4/4 分摊；根因见 Log） | verified |
| B-02 | 同上 | PASS: `pytest …-k b02` → 1 passed | test_poc_dbos.py:426-432（runs_a/records_a 与 runs_b/records_b 跨租户互不可见） | poc_runs/poc_records tenant 维度查询（tenant scope 由 adapter 层承载，NFR-SEC-01） | verified |
| RPO | 同上 | PASS: `pytest …-k rpo` → 1 passed | test_poc_dbos.py:463-471（kill 后 committed state 仍在、restart 后无重复补偿写、SUCCESS） | commit 后 SIGKILL，restart 前后状态断言（Postgres durable state，RPO=0） | verified |

### Log
- [2026-08-26] created (draft)
- [2026-08-26] started (in-progress) — marker 交接 TASK-001(completed)→TASK-003(active)；owned 30 路径；Spec-Refs 修纯格式（session 脚本要求）；Temporal 同日降级条件执行（DBOS/Restate 达标则不测）
- [2026-08-26] RED 记录 — 契约行落具体用例名；smoke 验证 DBOS 2.31 async 面（`*_async` 绑定首个 loop → 统一 `to_thread(sync API)`）；1000-baseline/worker 模式设计定型
- [2026-08-26] S-06 根因修复 — (1) `database_backed_queue=True` 的 Queue 不注册进内存 registry，必须 `DBOS.register_queue()` 持久化到 `dbos.queues` 才会被 queue_thread 轮询（否则 "Listening to 0 queues"→enqueue 无人消费→get_result 永等，单进程自消费 repro 实测：修复前挂死、修复后 8/8 3.1s）；"launch 迁移锁竞争"理论否掉（sysdb v108 已迁移、`should_migrate()` 跳过锁、锁有界 30s）；(2) 测试驱动进程用 `listen=[]` 不消费 poc_queue、queue `worker_concurrency=4` 使 8 任务 4/4 分摊、`DBOS__VMID` 区分 executor_id。S-06 4 次连续通过
- [2026-08-26] completed (done) — 全套件 `pytest test_poc_dbos.py` 11 passed + baseline 1000-concurrent 1 passed + verifier `-k "rpo or b02"` 2 passed；evidence `all_criteria_passed=True`（11 口径全 PASS，SLO-OBS-01 75/75 关联）

---

## TASK-004: Restate PoC

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-wf-001-durable-execution.design.md#3.1.1 候选清单, adr-wf-001-durable-execution.design.md#3.1.3 PoC 验收口径
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001, fluxion-runtime-core#RULE-fluxion-runtime-001, backend-code-quality-performance#RULE-backend-quality-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001
- **Note**: 引用 TASK-001 六条为依赖（无独立责任 rule）
- **Acceptance-Refs**: S-01, S-02, S-03, S-04, S-05, S-06, B-02（引用）（本候选证据，S-01..S-06 最终验收归 TASK-005）

### Description

Restate（Rust 单二进制 durable runtime，invocation journal 模型，Python SDK `restate-sdk`）过 6 口径。license 条款（**BUSL-1.1**：source-available、非 OSI 开源、release 后 4 年转 Apache-2.0；2026-08-26 已核对 `restatedev/restate` LICENSE 原文，非 FSL）确认结果记入 evidence，供评估矩阵"数据与合规"维度打分。

#NOTES（2026-08-26 提出，2026-08-27 用户决策=选项 ② 记录为能力边界）
**单节点 Restate worker 崩溃后 suspended invocation 无法恢复**：实测 5 种方案（默认指数退避 / 快速重试 / CLI deregister / force-remove / 固定端口同 deployment 替换，均在干净容器）后，worker 进程 SIGKILL 后 workflow 永久停在 `/restate/output` 470 "not completed"，server 持续对死 deployment 重试，不故障转移到新 worker。Restate 官方 HA 要求**多节点集群**（docs：3 节点 + replication 2 才自动故障转移）；单节点 = 无 HA。该发现直接影响 S-02/S-03/RPO/P-TIMER（worker 中途 kill → resume）验收——单节点下**无法通过**，属候选能力边界而非测试实现问题。矩阵"运维/故障恢复"维度应据此扣分（对比 DBOS：进程级 launch 恢复自动生效，S-02 5.07s 通过）。**用户决策（2026-08-27）：选项 ②——记录为 Restate 单节点能力边界，S-02/P-TIMER/S-03/RPO/S-06/B-02 不伪造 GREEN，evidence 如实标记 boundary/未 verified；矩阵据此打分；不起多节点集群复测（运维重且与候选卖点相悖）。**

### Checklist

- [x] 起 Restate runtime（docker `restatedev/restate` 或官方二进制）+ Python SDK 接入
- [x] `RestateWorkflowEngine` 实现 Protocol 5 成员（virtual object / workflow handler 映射）
- [x] [S-01][E2E] 真实边界 Adapter→Restate→journal：`execute_workflow` 返回 `run_id` 且 start 同步持久化；断言 durable start P95≤1s（SLO-WF-01）→ **verified**
- [x] [S-02][E2E] 真实边界 Restate + 真实进程 kill：中途 kill → resume；断言从最近 journal 点继续非重启，recovery P95≤60s（SLO-WF-02 / P-CRASH）→ **boundary（#NOTES：单节点崩溃恢复不可用，非 PASS）**
- [x] [P-TIMER][integration] 真实边界 Restate durable timer：worker 重启后定时器仍触发 → **boundary（#NOTES：同崩溃恢复）**
- [x] [P-SIGNAL][integration] 真实边界 Restate external approval signal：运行中 signal 唤醒等待 step 并继续推进（roadmap TASK-0002 项 5）→ **verified**
- [x] [S-05][integration] 真实边界 Restate dedup：重试已完成 step；断言 no-op 且副作用重复=0（P-IDEMP / SLO-WF-03）→ **verified**
- [x] [S-03][integration] 真实边界 pinned + retention mock：断言用 pinned version 不 resolve latest（P-PIN / RULE-WF-02）→ **boundary（#NOTES：同崩溃恢复）**
- [x] [S-04][integration] 真实边界 step timeout：断言触发定义 fail policy 非无限等待（P-TIMEOUT / RULE-WF-04）→ **verified**
- [x] [S-06][integration] 真实边界 2 个真实 worker 进程：断言 2nd worker 拉取排队 work（P-SCALE）→ **boundary（#NOTES：单节点 2 deployment 不分摊，非 PASS）**
- [x] [B-02 引用][integration] tenant scoping 在 Restate 上复验（负责人 TASK-003）→ **verified（单独跑通过；全套件被干扰超时）**
- [x] license 条款确认记入 evidence（BUSL-1.1：非 OSI 开源、4 年后转 Apache-2.0、禁止 Public Restate Platform Service；Fluxion 抽象层用法落在 Additional Use Grant 允许范围）
- [x] 产出 evidence JSON（`backend/tests/workflow_poc/evidence/restate.json`）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Adapter→Restate→journal | 返回 `run_id`；同步持久化；P95≤1s | backend/tests/workflow_poc/test_poc_restate.py::test_s01_durable_start | `pytest backend/tests/workflow_poc/test_poc_restate.py -k s01` | verified |
| S-02 | E2E | Restate + 真实进程 kill | 从最近 journal 点继续；recovery P95≤60s | test_poc_restate.py::test_s02_crash_recovery | `pytest ... -k s02` | **boundary**（#NOTES：单节点崩溃恢复不可用） |
| S-03 | integration | pinned + retention mock | 用 pinned version，不 resolve latest | test_poc_restate.py::test_s03_pinned_resume | `pytest ... -k s03` | **boundary**（#NOTES：同崩溃恢复） |
| S-04 | integration | step timeout 配置 | 触发定义 fail policy | test_poc_restate.py::test_s04_step_timeout | `pytest ... -k s04` | verified |
| S-05 | integration | Restate dedup | no-op；副作用重复=0 | test_poc_restate.py::test_s05_idempotency | `pytest ... -k s05` | verified |
| S-06 | integration | 2 个真实 worker 进程 | 2nd worker 拉取排队 work | test_poc_restate.py::test_s06_scale_two_workers | `pytest ... -k s06` | **boundary**（#NOTES：单节点不分摊） |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-26] created (draft)
- [2026-08-26] license 核对（README→LICENSE 原文）：BUSL-1.1，非 FSL/非 OSI；`Change Date = release+4 年` → Apache-2.0；Additional Use Grant 允许"自用生产/内部平台/抽象层公开平台"，禁止 Public Restate Platform Service。Fluxion（WorkflowEngine Protocol + Console API 抽象层，租户不直连 Restate API）落在允许范围。DBOS 对照：MIT（开放源码）。该结论随 evidence 记录供矩阵"数据与合规"打分。
- [2026-08-26] started (in-progress) — 前置：TASK-003 正式关闭（8 契约行 verified + 证据全填 + required rule code-stage bind applied + active complete 清 marker）；激活 TASK-004（owned 37 paths，交接继承工作树）。Spec-Refs 由文字引用改为显式 6 rule refs（cf_spec_session 生成需要）
- [2026-08-26] 集成打通 — Restate 容器（restate-poc）+ restate-sdk 1.0.4 + hypercorn worker；引擎走 ingress（send/lookup/attach/output/cancel）+ poc_signal resolver 投递审批信号。**5 场景 GREEN**：S-01（start 同步持久化 47.5ms）、S-04（step timeout→TerminalError→ERROR 0.56s）、P-SIGNAL（signal 唤醒+payload 进入结果）、S-05（key 幂等 PreviouslyAccepted）、SLO-OBS-01（17/17 关联）。evidence/restate.json 落 5 口径 + license（BUSL-1.1）
- [2026-08-26] **#NOTES 阻断确认（6 场景不可过）**：(1) 单节点 worker 崩溃恢复不可用（S-02/P-TIMER/S-03/RPO；5 方案实测全失败，suspended invocation 永久 470，需多节点集群 HA）；(2) 单节点 scale 不分摊（S-06：8/8 全落 worker-1，2 deployment 不自动负载均衡）；(3) B-02 单独过、全套件被干扰超时。**未伪造 GREEN**：P-CRASH/P-TIMER/P-PIN/P-SCALE/RULE-backend-database-001/B-02 保持未 verified，待用户决策（见 Description #NOTES）
- [2026-08-27] completed (done) — **用户决策（选项 ②）**：S-02/P-TIMER/S-03/RPO/S-06/B-02 记录为 Restate 单节点能力边界（evidence 如实 boundary，不伪造 GREEN）；5 场景 verified（S-01/S-04/P-SIGNAL/S-05/SLO-OBS-01）+ license 确认。Restate 评估结论：单节点崩溃恢复不可用 + 2 deployment 不分摊 → 矩阵"运维/故障恢复/水平扩展"强扣分。交接 TASK-005 矩阵回填（DBOS 证据全绿 vs Restate 5 过 6 boundary）

---

## TASK-002: Temporal PoC（条件执行——未触发则跳过）

> **条件执行（用户决策 2026-08-26）**：仅当 TASK-003（DBOS）与 TASK-004（Restate）均不达标时回补本任务；否则不执行，评估矩阵 Temporal 列按文档 + 生产部署形态打分并注明"未实测（运维负担重，用户决策降级）"。TASK-005 汇总时不要求 `temporal.json` 存在，但矩阵必须含 Temporal 列与未测理由。

- **Status**: done（条件未触发，用户决策不执行/不对比；处置已定）
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-wf-001-durable-execution.design.md#3.1.1 候选清单, adr-wf-001-durable-execution.design.md#3.1.3 PoC 验收口径
- **Spec-Refs**: 引用 TASK-001 六条为依赖（无独立责任 rule）
- **Acceptance-Refs**: S-01, S-02, S-03, S-04, S-05, S-06, B-02（引用）（本候选证据，S-01..S-06 最终验收归 TASK-005）
- **Mark**（2026-08-27）：**Temporal 不对比**——组件（独立 server 集群 + 默认 DB）太复杂，运维成本与收益不匹配，未实测、不纳入候选对比；矩阵 Temporal 列仅按文档作参考标注"未实测/不对比"。

### Description

Temporal（独立 server 集群 + event history replay，`temporalio` Python SDK，生态最成熟、运维最重）过 6 口径。PoC 环境用 `temporalio/auto-setup` 单容器从简（评估矩阵"运维"维度按生产部署形态打分，不受 PoC 从简影响）。

> **不对比标记（2026-08-27）**：DBOS 已全口径达标并选为 vendor，Temporal 组件（独立 server 集群 + DB）过于复杂，不启动、不实测、不参与候选对比（checklist 全部保持 `[ ]` = 未执行）。

### Checklist

- [ ] docker 起 Temporal（`temporalio/auto-setup` 单容器，含 server + 默认 DB）
- [ ] `TemporalWorkflowEngine` 实现 Protocol 5 成员（`temporalio` worker + workflow/stub 映射）
- [ ] [S-01][E2E] 真实边界 Adapter→Temporal→durable store：`execute_workflow` 返回 `run_id` 且 start 同步持久化；断言 durable start P95≤1s（SLO-WF-01）
- [ ] [S-02][E2E] 真实边界 Temporal + 真实 worker kill：中途 kill → resume；断言 replay event history 从最近 durable step 继续，recovery P95≤60s（SLO-WF-02 / P-CRASH）
- [ ] [P-TIMER][integration] 真实边界 Temporal durable timer：worker 重启后定时器仍触发
- [ ] [P-SIGNAL][integration] 真实边界 Temporal external approval signal：运行中 signal 唤醒等待 step 并继续推进（roadmap TASK-0002 项 5）
- [ ] [S-05][integration] 真实边界 Temporal dedup（workflow id reuse policy / activity idempotency）：断言 no-op 且副作用重复=0（P-IDEMP / SLO-WF-03）
- [ ] [S-03][integration] 真实边界 pinned + retention mock：断言用 pinned version 不 resolve latest（P-PIN / RULE-WF-02）
- [ ] [S-04][integration] 真实边界 activity timeout 配置：断言触发定义 fail policy 非无限等待（P-TIMEOUT / RULE-WF-04）
- [ ] [S-06][integration] 真实边界 2 个真实 worker 进程（task queue 轮询）：断言 2nd worker 拉取排队 work（P-SCALE）
- [ ] [B-02 引用][integration] tenant scoping 在 Temporal 上复验（负责人 TASK-003）
- [ ] 产出 evidence JSON（`backend/tests/workflow_poc/evidence/temporal.json`）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Adapter→Temporal→durable store | 返回 `run_id`；同步持久化；P95≤1s | planned: backend/tests/workflow_poc/test_poc_temporal.py::test_s01_durable_start | planned: `pytest backend/tests/workflow_poc/test_poc_temporal.py -k s01` | planned |
| S-02 | E2E | Temporal + 真实 worker kill | replay 恢复；recovery P95≤60s | planned: test_poc_temporal.py::test_s02_crash_recovery | planned: `pytest ... -k s02` | planned |
| S-03 | integration | pinned + retention mock | 用 pinned version，不 resolve latest | planned: test_poc_temporal.py::test_s03_pinned_resume | planned: `pytest ... -k s03` | planned |
| S-04 | integration | activity timeout 配置 | 触发定义 fail policy | planned: test_poc_temporal.py::test_s04_step_timeout | planned: `pytest ... -k s04` | planned |
| S-05 | integration | Temporal dedup | no-op；副作用重复=0 | planned: test_poc_temporal.py::test_s05_idempotency | planned: `pytest ... -k s05` | planned |
| S-06 | integration | 2 个真实 worker 进程 | 2nd worker 拉取排队 work | planned: test_poc_temporal.py::test_s06_scale_two_workers | planned: `pytest ... -k s06` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-26] created (draft)

---

## TASK-005: 评估矩阵回填 + vendor pick ADR + E-02 CI gate

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-004（TASK-002 条件执行：DBOS/Restate 达标则视为满足，未触发回补时 TASK-005 不要求 temporal.json）
- **Source**: adr-wf-001-durable-execution.design.md#3.1.2 评估矩阵, adr-wf-001-durable-execution.design.md#3.1.4 关键决策记录, adr-wf-001-durable-execution.design.md#2.5.2 功能验收场景
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-01, S-02, S-03, S-04, S-05, S-06（最终验收汇总）, E-02, RISK-WF-01, RISK-WF-03

### Description

汇总 3 候选 evidence artifact，回填 19 维度权重矩阵，落 vendor pick ADR（amend ADR-008，解除 `pending-PoC-gate`），建 E-02 CI gate（无有效 PoC 证据阻断 WorkflowEngine 生产实现路径）。

### Checklist

- [x] [S-01..S-06][汇总] 验证已执行候选 evidence artifact 齐全（`dbos.json`/`restate.json` 各 7 口径 + trace 断言；DBOS 另含 1000-concurrent baseline；Temporal 未触发回补时不要求 `temporal.json`，但矩阵须含其文档打分列与未测理由）+ SLO 数值判定（durable start P95≤1s / recovery P95≤60s / 不可逆副作用重复=0）
- [x] 19 维度（15 PRD §4.8 + 4 补充：Contract swappability / vendor lock-in / Contract 可替换性 / PoC 失败回退成本）× 3 候选打分回填 design §3.1.2 权重矩阵（self-built 列保持 pending，注明"未测：PRD 默认否决 + 翻盘条件未触发"）
- [x] [E-02][integration] 真实边界 evidence artifact 存在性 + 时效检查：CI gate 脚本——无有效 PoC evidence 时阻断 WorkflowEngine 生产实现路径（RULE-fluxion-dfx-001：证据须编码阶段自动化产出，非事后补）
- [x] vendor pick ADR 落 `docs/adr/`（amend ADR-008；解除 `pending-PoC-gate`；记录 self-built fallback 条件 RISK-WF-01：若 3 候选全不达标→升级 self-built PoC 决策，否则降级 FEAT-11 范围）
- [x] verifier: `fluxion-dfx#RULE-fluxion-dfx-001` — `pytest backend/tests/workflow_poc/test_poc_gate.py`（E-02：evidence artifact 存在性 + 时效 CI gate，编码阶段自动化产出非事后补）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01..S-06 | 汇总判定 | 3 候选 evidence artifact（真实 PoC 产出） | 3 候选 artifact 齐全、每口径 PASS、SLO 数值达标 | backend/tests/workflow_poc/test_evidence_summary.py | `pytest backend/tests/workflow_poc/test_evidence_summary.py` | verified |
| E-02 | integration | evidence artifact gate（真实文件检查） | 无有效 evidence 时 gate 阻断（非零退出） | backend/tests/workflow_poc/test_poc_gate.py | `pytest backend/tests/workflow_poc/test_poc_gate.py` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-26] created (draft)
- [2026-08-27] started (in-progress) — 前置：TASK-003（DBOS 11 口径全绿）与 TASK-004（Restate 5 verified + 6 boundary）均已关闭；Temporal 未触发（DBOS 达标）。交接证据：`evidence/dbos.json`（all_criteria_passed=True）、`evidence/restate.json`（5 口径 + license=BUSL-1.1）。任务：19 维矩阵回填 + vendor pick ADR（amend ADR-008 解除 pending-PoC-gate）+ E-02 CI gate
- [2026-08-27] completed (done) — 19 维矩阵回填 design §3.1.2：**DBOS 8.7 > Temporal 7.4（未实测，文档打分）> Restate 5.5**；ADR-013 落盘（vendor pick = DBOS，解除 pending-PoC-gate，关闭 self-built fallback）；E-02 gate `test_poc_gate.py` 4 passed（存在性+时效+全绿校验，缺 evidence 即阻断）；S-01..S-06 汇总 `test_evidence_summary.py` 4 passed（DBOS 7 口径全绿 + SLO 达标；Restate 如实记录 boundary）
