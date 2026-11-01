# Tasks: 学习链路运行时接线

- **Source**: .code-flow/tasks/2026-11-01/learning-pipeline-wiring/learning-pipeline-wiring.design.md
- **Created**: 2026-11-01
- **Updated**: 2026-11-01

## Proposal

把上一需求交付但未接线的学习链路接到真实运行路径：执行层族传播与冻结设置读取、后台调度（抽取/候选/回放/TRIAL）、生产 CaseRunner（真实 Snapshot + 模型）、learning_admin CLI，以及一条真实执行链的端到端场景与接线盘点，确保"越用越智能"可被观察到。

### Alignment

- **Scope**: 族传播、learning 设置冻结、调度循环、生产两臂回放、自动 TRIAL、CLI、接线盘点与 E2E。
- **Decisions**: 调度用 runtime supervisor 内周期循环（幂等）；回放走真实 Snapshot + 模型池；判据为参考词法覆盖 + JSON 结构；关闭 `learning_enabled` 即回到基线。
- **Non-goals**: Console 新页面、跨租户学习、向量检索、分布式调度。
- **Acceptance**: 10 个场景（6 S、2 E、2 B）全部 verified；S-07 走真实 supervisor/executor，B-01 禁止仅端口注入。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实 executor 组合 + 真实 PostgreSQL | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_family_wiring.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-02 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实 Snapshot policy_json + 真实 PostgreSQL | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_family_wiring.py","-k","freeze"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-03 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实调度循环 + 真实 PostgreSQL | TASK-002 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_scheduler.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-04 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实 Snapshot + 真实 PostgreSQL；本地假上游 | TASK-003 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_case_runner.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-05 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实调度 + 回放结果 + 审计 | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_trial_wiring.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-06 | learning-pipeline-wiring.design.md#2.5 验收条件 | contract | 真实 CLI 进程 + 真实 PostgreSQL + 审计 | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_admin_cli.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| S-07 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实 supervisor + executor + 脚本化 provider | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_e2e.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| E-01 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 调度故障注入，主执行链真实运行 | TASK-005 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_scheduler_degradation.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| E-02 | learning-pipeline-wiring.design.md#2.5 验收条件 | integration | 真实设置冻结 + 调度 + PostgreSQL | TASK-002 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_scheduler.py","-k","disabled"] | . | 600 | P1 | auto | local | - | - | - | no |
| B-01 | learning-pipeline-wiring.design.md#2.5 验收条件 | contract | 真实源码调用图 | TASK-005 | planned | ["uv","run","pytest","-q","tests/architecture/test_learning_wiring_inventory.py"] | . | 300 | P1 | auto | none | - | - | - | no |

> 覆盖设计中的全部 10 个场景（6 S、2 E、2 B）；无 E2E 浏览器旅程。

---

## TASK-001: 执行层任务族传播与 learning 设置冻结

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: learning-pipeline-wiring.design.md#3.2 架构设计, learning-pipeline-wiring.design.md#3.4 接口设计
- **Spec-Refs**: harness-memory#RULE-memory-003, harness-memory#RULE-memory-006
- **Rule-Owner-Refs**: harness-memory#RULE-memory-003, harness-memory#RULE-memory-006
- **Acceptance-Refs**: S-01, S-02
- **Estimate**: 1d
- **Capability**: skill 执行后下一个模型请求注入该族经验；新 Run 的 policy_json 冻结 learning 设置并只对后续 Run 生效

### Description

executor 在 Run 启动时把会话上一 Run 的任务族（或 `agent:<id>`）传给 `MemoryContextLoader.set_task_family`；收到带 skill 工具调用的 assistant 回合时切换为 `skill:<key>`。`build_snapshot` 接收 `learning` 设置并写入 `policy_json`；executor 读取冻结值传给选择器（阈值、偏好限额、learning_enabled）。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_family_wiring.py`
- [x] Run 启动族兜底 + skill 调用后切换族
- [x] learning 设置写入 policy_json 并由 executor 读取
- [x] [S-01][integration] 覆盖 skill 族切换与 agent 族兜底
- [x] [S-02][integration] 覆盖设置冻结与只影响新 Run
- [x] [harness-memory#RULE-memory-003] verifier：同命令；选择与缓存语义不变，仅参数来自冻结设置
- [x] [harness-memory#RULE-memory-006] verifier：同命令；设置执行期不重读

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 真实 executor 组合 + PostgreSQL | skill 后注入 skill 族；无 skill 注入 agent 族 | tests/agent_runtime/test_learning_family_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_family_wiring.py"] | verified |
| S-02 | integration | 真实 Snapshot + PostgreSQL | policy_json 含 learning；旧 Run 不受影响 | tests/agent_runtime/test_learning_family_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_family_wiring.py","-k","freeze"] | verified |

### Acceptance Evidence

- RED: 去掉实现后 collection error（`frozen_learning_payload` / `skill_family_from_tool_calls` 缺失），2026-11-01。
- GREEN: `4 passed in 0.53s`；回归 `test_memory_redesign_context + test_snapshot_freeze + test_run_service` → 40 passed，`-k "executor or resolve"` → 39 passed。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | FAIL: collection error | PASS: 3 passed | `test_skill_family_from_tool_calls`、`test_emitter_switches_task_family_on_skill_turn`、`test_s01_family_lane_follows_skill_and_agent_fallback` | 真实 `_ExecutorEventEmitter.on_assistant_turn` 与真实 loader/PostgreSQL 注入 | verified |
| S-02 | FAIL: collection error | PASS: `-k freeze` 1 passed | `test_s02_freezes_learning_settings_into_snapshot` | 真实 `build_snapshot` 写入 policy_json；`learning_of` 读回；旧快照回落关闭 | verified |
- S-01: verified — automated command passed; run_id=7e1e49ef995541b699470fca0af59f94 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=7e1e49ef995541b699470fca0af59f94 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---

## TASK-002: 学习调度循环（抽取/候选/关闭开关）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: learning-pipeline-wiring.design.md#3.2 架构设计, learning-pipeline-wiring.design.md#3.3 数据设计
- **Spec-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-data#RULE-data-001, harness-arch#RULE-arch-001, harness-time#RULE-time-001
- **Rule-Owner-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-data#RULE-data-001, harness-arch#RULE-arch-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-03, E-02
- **Estimate**: 1.5d
- **Capability**: 后台调度一轮即可为开启学习的租户生成 eval_case 与 PROPOSED 经验，重复执行幂等；关闭开关的租户被跳过

### Description

在 runtime supervisor 中新增 `LearningSupervisor.run_once(now)`：只处理 `learning_enabled=true` 的租户，调用 `GoldenSetExtractor` 与 `ExperienceCandidates`；单实例周期循环，失败独立降级并计数；时间统一 timestamptz。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_scheduler.py`
- [ ] 调度幂等且跳过关闭学习的租户
- [ ] 循环随 supervisor 启动，失败降级
- [ ] [S-03][integration] 覆盖抽取/候选与幂等
- [ ] [E-02][integration] 覆盖 learning_enabled=false
- [ ] [harness-data#RULE-data-001] verifier：同命令；复用现有表与 jsonb
- [ ] [harness-arch#RULE-arch-001] verifier：同命令；不新增服务
- [ ] [harness-time#RULE-time-001] verifier：同命令；timestamptz
- [ ] [harness-memory#RULE-memory-002] verifier：同命令；候选写入走 MemoryService
- [ ] [harness-memory#RULE-memory-004] verifier：同命令；tombstone 不复活
- [ ] [harness-memory#RULE-memory-005] verifier：同命令；调度幂等、不新建 job 表

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实调度 + PostgreSQL | 生成 eval_case 与 PROPOSED；重复幂等 | tests/agent_runtime/test_learning_scheduler.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_scheduler.py"] | planned |
| E-02 | integration | 真实设置 + 调度 + PG | 关闭后不生成、不注入 | tests/agent_runtime/test_learning_scheduler.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_scheduler.py","-k","disabled"] | planned |

### Acceptance Evidence

### Log

- [2026-11-01] created (draft)

---

## TASK-003: 生产 CaseRunner 与两臂回放接线

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: learning-pipeline-wiring.design.md#3.4 接口设计, learning-pipeline-wiring.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001, harness-secret#RULE-secret-001, harness-model#RULE-model-001
- **Rule-Owner-Refs**: harness-snapshot#RULE-snapshot-001, harness-secret#RULE-secret-001, harness-model#RULE-model-001
- **Acceptance-Refs**: S-04
- **Estimate**: 2d
- **Capability**: 生产 CaseRunner 从来源 Run 与冻结 Snapshot 重建请求，真实执行基线/候选两臂并逐项判定，报告落 eval_report

### Description

实现 `ProductionCaseRunner`：读取 eval_case 的来源 Run（input_text）与 RuntimeSnapshot（agent 定义/model 定义/learning 设置），经模型池凭据调用模型；候选臂注入经验文本，基线臂不注入；按参考答案词法覆盖与 JSON 结构逐项判定；凭据只读 Owner 表，不写日志/报告。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_case_runner.py`
- [ ] 两臂请求差异（候选携带经验）可断言
- [ ] 逐项判定与报告落库
- [ ] [S-04][integration] 覆盖真实重建与两臂
- [ ] [harness-snapshot#RULE-snapshot-001] verifier：同命令；冻结 Snapshot
- [ ] [harness-secret#RULE-secret-001] verifier：同命令；凭据不落日志/报告
- [ ] [harness-model#RULE-model-001] verifier：同命令；模型池与实时凭据

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 Snapshot + PG；本地假上游 | 请求重建；两臂差异；逐项判定 | tests/agent_runtime/test_learning_case_runner.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_case_runner.py"] | planned |

### Acceptance Evidence

### Log

- [2026-11-01] created (draft)

---

## TASK-004: TRIAL 自动接线、CLI 与端到端

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: learning-pipeline-wiring.design.md#3.2 架构设计, learning-pipeline-wiring.design.md#3.4 接口设计
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-memory#RULE-memory-001
- **Rule-Owner-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-memory#RULE-memory-001
- **Acceptance-Refs**: S-05, S-06, S-07
- **Estimate**: 2d
- **Capability**: 回放 PASSED 自动进 TRIAL 并按观察期晋升/退休；`learning_admin` 提供 list/extract/candidates/replay/activate/retire/explain；真实执行链端到端可见注入

### Description

调度在回放后调用 `LearningActivation.start_trial`；观察期按族 LEARNING_SIGNAL 与基线比较自动晋升/退休。新增 `scripts/learning_admin.py`（JSON 输出、退出码、审计、operator 必填）。E2E：3 次同族任务 → 调度 → TRIAL → 第 4 次 Run 注入。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_trial_wiring.py tests/agent_runtime/test_learning_admin_cli.py tests/agent_runtime/test_learning_e2e.py`
- [ ] 回放 PASSED → TRIAL；观察期晋升/退休
- [ ] CLI 全命令与审计
- [ ] [S-05][integration] 覆盖 TRIAL 与晋升
- [ ] [S-06][contract] 覆盖 CLI 输出与退出码
- [ ] [S-07][integration] 覆盖真实执行链端到端
- [ ] [harness-api#RULE-api-001] verifier：同命令；CLI JSON 与错误出口
- [ ] [harness-api#RULE-api-002] verifier：同命令；不新增 POST 幂等端点
- [ ] [harness-memory#RULE-memory-001] verifier：同命令；人工激活需 operator，白名单外不自动

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 真实调度 + 回放 + 审计 | PASSED→TRIAL；晋升/退休 | tests/agent_runtime/test_learning_trial_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_trial_wiring.py"] | planned |
| S-06 | contract | 真实 CLI + PG + 审计 | JSON/退出码/审计 | tests/agent_runtime/test_learning_admin_cli.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_admin_cli.py"] | planned |
| S-07 | integration | 真实 supervisor/executor | 端到端可见注入 | tests/agent_runtime/test_learning_e2e.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_e2e.py"] | planned |

### Acceptance Evidence

### Log

- [2026-11-01] created (draft)

---

## TASK-005: 失败降级与接线盘点

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004
- **Source**: learning-pipeline-wiring.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: E-01, B-01
- **Estimate**: 1d
- **Capability**: 调度/回放故障只降级为无学习并计数；接线盘点断言每个学习能力都有生产调用方

### Description

调度循环对抽取/候选/回放/激活的异常独立捕获并写 warning 与 `learning_pipeline_failures_total`；不产生半成品学习物。新增 `tests/architecture/test_learning_wiring_inventory.py`：AST 扫描生产代码，断言抽取、候选、回放、激活、族传播、设置读取至少各有一个生产调用方；仅测试调用或端口注入判失败。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_scheduler_degradation.py tests/architecture/test_learning_wiring_inventory.py`
- [ ] 调度失败不影响主 Run 且无半成品
- [ ] 接线盘点覆盖六类能力
- [ ] [E-01][integration] 覆盖故障降级
- [ ] [B-01][contract] 覆盖生产调用方断言
- [ ] [harness-test#RULE-test-001] verifier：同命令；真实执行链 + 分层

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | 调度故障 + 主执行链真实运行 | 主 Run 完成；warning/计数；无半成品 | tests/agent_runtime/test_learning_scheduler_degradation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_scheduler_degradation.py"] | planned |
| B-01 | contract | 真实源码调用图 | 六类能力各有生产调用方 | tests/architecture/test_learning_wiring_inventory.py | ["uv","run","pytest","-q","tests/architecture/test_learning_wiring_inventory.py"] | planned |

### Acceptance Evidence

### Log

- [2026-11-01] created (draft)
