# Tasks: Agent 自进化

- **Source**: .code-flow/tasks/2026-10-31/agent-self-evolution/agent-self-evolution.design.md
- **Created**: 2026-10-31
- **Updated**: 2026-10-31

## Proposal

建立一条效果可度量、几乎不需要逐条人工介入的学习闭环：任务族派生与 Golden Set 自动抽取 → 隐式信号与注入归因 → 两臂回放门禁 → 经验候选 → TRIAL 自动晋升/退休。同时把记忆召回从「有字面重叠就注入」改为「过阈值才注入」，并让 ACTIVE 经验按「任务族 + 目标文本」两通道命中，覆盖固定 skill 旅程与非标探索。所有学习物复用现有记忆体系与事件链路，在线请求路径零新增 IO，学习链路失败降级为无学习。

### Alignment

- **Scope**: 任务族与 Golden Set、信号与归因、两臂回放、verifier 择优、经验候选、检索门控、自动激活与回滚。
- **Decisions**:
  - 任务族 = 实际执行的 skill key，无 skill 回退 `agent_id`，Run 完成时派生。
  - Golden Set 从真实成功轨迹自动抽取、脱敏、去重、限量并保留时间切分。
  - 激活走 TRIAL：回放达标自动进 TRIAL，生产信号不劣化自动晋升，劣化自动退休。
  - 回放为 live 两臂（基线臂 / 候选臂），CI 用脚本化 provider 保证可复现。
  - ACTIVE 经验注入不受 `inject_inferred` 影响；归因按 `selection_hash` 去重。
- **Non-goals**: Console 学习页面、显式点赞 UI、跨用户知识共享、向量检索、模型微调。
- **Acceptance**: 24 个场景（16 S、2 E、6 B）全部 verified；两臂门禁、自动晋升、两通道注入与批量 case 逐项判定各有真实 PostgreSQL 集成用例。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 consolidation job + 真实 PostgreSQL | TASK-003 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_signal.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-02 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Runtime + 真实 PostgreSQL；逐请求比对 | TASK-003 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_injection_attribution.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-03 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Snapshot 重放 + 真实 PostgreSQL | TASK-004 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-04 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 verifier 注册表与执行链 | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_verification_loop.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-05 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-06 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实回放门禁 + 真实 PostgreSQL + 真实审计 | TASK-008 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","gate"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-07 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯函数打分与选择，无 IO | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","gating"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-08 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 PostgreSQL + 真实审计写入 | TASK-008 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","audit"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-09 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Run/事件数据 + 真实 PostgreSQL；worker 离线抽取 | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-10 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实两臂回放 + 真实 PostgreSQL；脚本化 provider | TASK-004 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-11 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯函数打分与选择；标注集 + 固定语料 | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_quality.py"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-12 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实状态机 + 真实 PostgreSQL + 真实审计 | TASK-008 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_trial_promotion.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-13 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Runtime + 真实 PostgreSQL；真实候选集 | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_retrieval_lanes.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-14 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实批量 Run/逐项 tool 事件 + 真实 PostgreSQL | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py","-k","batch_items"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-15 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实两臂回放 + 真实 PostgreSQL；逐项结果判定 | TASK-004 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py","-k","batch_items"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-16 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","batch"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-01 | agent-self-evolution.design.md#2.5 验收条件 | integration | 学习链路故障注入，主执行链真实运行 | TASK-004 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_degradation.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| E-02 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 forget 事务 + 真实 PostgreSQL + 真实聚合 job | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","forget"] | . | 600 | P1 | auto | local | - | - | - | no |
| B-01 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择与装箱逻辑 | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","budget"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-02 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择逻辑 | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","versions"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-03 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择 + 缓存装载；token 化与外部调用计数 | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","upper_bound"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-04 | agent-self-evolution.design.md#2.5 验收条件 | contract | 真实 PostgreSQL 迁移链 + ORM + 契约模型 | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_schema_parity.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| B-05 | agent-self-evolution.design.md#2.5 验收条件 | contract | 真实盘面：任务文件、manifest、context、测试文件 | TASK-009 | planned | ["uv","run","pytest","-q","tests/agent_evolution_inventory.py"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-06 | agent-self-evolution.design.md#2.5 验收条件 | contract | 源码导入图 + 模型池配额（架构断言） | TASK-004 | verified | ["uv","run","pytest","-q","tests/architecture/test_learning_isolation.py"] | . | 300 | P1 | auto | none | - | - | - | no |

> 覆盖设计中的全部 24 个场景（16 个 S、2 个 E、6 个 B）；无 E2E：本需求不新增浏览器旅程与公开 API 契约。

---

## TASK-001: 数据模型与契约迁移

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: agent-self-evolution.design.md#3.3 数据设计, agent-self-evolution.design.md#4.4 数据迁移
- **Spec-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001
- **Acceptance-Refs**: B-04
- **Estimate**: 1d
- **Capability**: 迁移到 head 后 `user_memory` 具备激活状态/任务签名/触发文本/证据列，且存在 `eval_case`、`eval_report` 表与 `run_record.task_family` 列

### Description

扩展 `runtime.user_memory`：`activation_state`（默认 ACTIVE）、`task_signature`、`trigger_text`、`evidence_json`，扩展 category 约束与 ACTIVE partial index；新增 `runtime.eval_case`（含 `item_count`）、`runtime.eval_report` 表与 `runtime.run_record.task_family` 列；同步契约层 `MemoryCategory`、`ALLOWED_CATEGORIES`、Admin/Console DTO 类别枚举与提案文案表，保证读取新类别不触发校验失败。迁移单链、与 ORM 同构。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py`
- [x] 扩展列与约束，默认值保持既有记忆行为不变
- [x] 新增 `eval_case`（含 `item_count`）、`eval_report` 与 `run_record.task_family`，标准列走 `StandardColumnsMixin`
- [x] 同步契约层类别枚举、白名单与 DTO Literal，读取新类别不抛校验错误
- [x] [B-04][contract] 覆盖迁移/ORM/契约同构、默认值与 downgrade
- [x] [harness-data#RULE-data-001] verifier：`uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py`；JSONB/时间/索引口径一致
- [x] [harness-time#RULE-time-001] verifier：同命令；新表与新增时间列统一 timestamptz
- [x] 记录 upgrade/downgrade 演练证据

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-04 | contract | 真实 PostgreSQL + 迁移链 + ORM + 契约模型 | 新列与新表存在、默认值 ACTIVE、唯一键与索引齐全、类别读取不报错 | tests/agent_runtime/test_runtime_schema_parity.py | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_schema_parity.py"] | verified |

### Acceptance Evidence

- RED: `uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py` → collection error（`EvalCase` / `EvalReport` 未定义），2026-10-31。
- GREEN: 同命令 → `16 passed in 0.60s`；`tests/contracts` → `1312 passed`。
- 迁移演练: `db_migrate.py downgrade 0029` → `eval_case`/`eval_report` 为新表消失、四个新列消失、category 约束回到 4 值；`upgrade head` → `0030 (head)`，parity 再次 16 passed。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-04 | FAIL: collection error（新表 ORM 缺失） | PASS: 16 passed | `test_runtime_table_schema_parity[user_memory/eval_case/eval_report]`、`test_b04_user_memory_defaults_and_experience_categories`、`test_b04_eval_report_baseline_versions_are_append_only`、`test_b04_eval_case_requires_existing_source_run` | 真实 PostgreSQL 迁移到 0030 + ORM 反射 + 真实写入/FK/唯一约束 | verified |
- B-04: verified — automated command passed; run_id=2a68fe69012446e48e62b5f355b36773 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-002: 任务族派生与 Golden Set 自动抽取

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-api#RULE-api-001, harness-api#RULE-api-002, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-09, S-14
- **Estimate**: 2d
- **Capability**: Run 完成后可查询到派生的任务族；worker 能从真实成功轨迹自动抽取脱敏、去重、限量的 Golden Set，批量 case 带 item_count 与逐项判据

### Description

实现纯函数 `derive_task_family(events, skills)`：取首个成功执行的 skill key，无 skill 回退 `agent_id`；Run 完成时写入 `run_record.task_family`。实现 Golden Set 抽取服务（worker 内离线调用）：筛选成功、无负向信号、未取消的 Run，只存 Run 引用与输入指纹（不落正文，天然脱敏），按 `(tenant, task_family, source_run, version)` 冲突忽略实现幂等，每族受上限约束并保留时间切分；批量 Run 记录 `item_count` 与逐项判据，超过 `eval_max_items_per_case` 标记 incomplete；抽取不改变 Run 状态。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_eval_cases.py`
- [x] 任务族派生覆盖 skill 与无 skill 两条路径，Run 完成时落列
- [x] 抽取筛选、脱敏、去重、上限与时间切分可验证
- [x] 批量 case 记录 item_count 与逐项判据，超限标记 incomplete
- [x] 抽取在 worker 内离线执行且不改变 Run 状态
- [x] [S-09][integration] 覆盖族归属、排除条件与限量
- [x] [S-14][integration] 覆盖批量条目语义、上限与逐项脱敏
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-09 | integration | 真实 Run/事件数据 + 真实 PostgreSQL | 按 skill 推导族、失败/取消/负向信号不入选、canary 不落库、限量与时间切分 | tests/agent_runtime/test_learning_eval_cases.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py"] | verified |
| S-14 | integration | 真实批量 Run/逐项 tool 事件 + 真实 PostgreSQL | item_count 与逐项判据、超限 incomplete、逐项脱敏 | tests/agent_runtime/test_learning_eval_cases.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py","-k","batch_items"] | verified |

### Acceptance Evidence

- RED: 去掉实现后 `uv run pytest -q tests/agent_runtime/test_learning_eval_cases.py` → collection error（`derive_task_family` / `GoldenSetExtractor` 缺失），2026-10-31。
- GREEN: 同命令 → `3 passed in 0.56s`；回归 `test_run_service.py` → `17 passed`，`test_run_leases.py + test_sse.py` → `13 passed`。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-09 | FAIL: collection error（抽取与派生 API 缺失） | PASS: 3 passed | `test_s09_derives_family_from_first_successful_skill`、`test_s09_extracts_only_successful_uncorrected_runs` | 真实 PostgreSQL 的 Run/事件行；抽取服务离线调用，输入正文只存 sha256 | verified |
| S-14 | FAIL: collection error | PASS: `-k batch_items` 1 passed | `test_s14_batch_items_are_recorded_with_item_count_and_cap` | 真实批量 tool 事件 + 真实 eval_case 写入 | verified |
- S-09: verified — automated command passed; run_id=534b2c8a700b43ea832a7bd95ff2a2e6 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=534b2c8a700b43ea832a7bd95ff2a2e6 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-003: 学习信号与注入归因

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-003, harness-memory#RULE-memory-005
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-01, S-02
- **Estimate**: 1d
- **Capability**: 每次模型请求的注入可归因且按选择 hash 去重；用户重问/否定产生 LEARNING_SIGNAL 且不影响 Run 终态

### Description

由 consolidation job 在事件边界计算隐式信号（重问、否定、改写）并写 `LEARNING_SIGNAL`（独立事务，失败只记 warning）；在模型请求边界写 `MEMORY_INJECTED`（memory_key/version/score/decision、丢弃原因计数、selection_hash），选择未变化时以 hash 引用上一条而不重复写行；两类事件均不进入 `HISTORY_EVENT_TYPES`。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_signal.py tests/agent_runtime/test_memory_injection_attribution.py`
- [x] 归因与模型请求逐条一致，丢弃原因按类别计数
- [x] 选择未变化时以 selection_hash 去重，不重复写事件行
- [x] 信号检测不改变 Run 终态与历史重建结果
- [x] [S-01][integration] 覆盖信号落库与终态不变
- [x] [S-02][integration] 覆盖归因一致与去重
- [x] [harness-memory#RULE-memory-003] verifier：`uv run pytest -q tests/agent_runtime/test_memory_redesign_context.py tests/agent_runtime/test_memory_redesign_cache.py`；归因不改变选择与缓存语义
- [x] [harness-memory#RULE-memory-005] verifier：`uv run pytest -q tests/agent_runtime/test_memory_redesign_supervisor.py`；信号检测在 job 入口且失败不影响 job

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 真实 consolidation job + 真实 PostgreSQL | 产生 LEARNING_SIGNAL；Run 终态不变；事件不进模型历史 | tests/agent_runtime/test_learning_signal.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_signal.py"] | verified |
| S-02 | integration | 真实 Runtime + 真实 PostgreSQL | key/version 与请求一致；含 score/decision；按 hash 去重 | tests/agent_runtime/test_memory_injection_attribution.py | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_injection_attribution.py"] | verified |

### Acceptance Evidence

- RED: 去掉实现后两条验收命令均 collection error（`detect_and_write_signal` / 归因 API 缺失），2026-10-31。
- GREEN: `3 passed in 0.62s`；回归 `test_memory_redesign_context/cache/observability` → `47 passed`，`test_memory_redesign_supervisor` → `6 passed`。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | FAIL: collection error | PASS: 2 passed（同一文件） | `test_s01_classifier_distinguishes_negation_reask_and_rewrite`、`test_s01_signal_is_written_without_changing_run_state` | 真实 PostgreSQL 的会话事件行 + 真实 EventWriter；信号走 consolidation job 入口 | verified |
| S-02 | FAIL: collection error | PASS: 1 passed | `test_s02_attribution_matches_selection_and_dedupes_by_hash` | 真实 MemoryQueries/缓存 + 真实 MEMORY_INJECTED 行；hash 去重与版本变化各一条 | verified |
- S-01: verified — automated command passed; run_id=a4620ee9884b41839d12770245181fd0 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=a4620ee9884b41839d12770245181fd0 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-004: 两臂回放与失败降级

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-data#RULE-data-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-03, S-10, S-15, E-01, B-06, RULE-06
- **Estimate**: 2d
- **Capability**: 同一 Golden Set 可做基线/候选两臂回放并产出可审计报告，批量 case 按逐项结果判定；学习链路失败时主 Run 照常完成

### Description

实现两臂回放编排：读取 `eval_case`，分别以不带经验（基线臂）与带经验（候选臂）执行并计算成功率与逐项结果；批量 case 按逐项结果判定，缺项或单项失败即判失败，报告附 `item_count`；基线臂结果按 `(case 集合, base_config_hash)` 缓存复用；报告 append-only 落 `eval_report`（含 content_hash 与 decision）；执行端由 `CaseRunner` 端口注入（装配层负责冻结 Snapshot 与后台模型池接线），失败降级为无判定并写 warning 与失败计数。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_replay.py tests/agent_runtime/test_learning_replay_gate.py tests/agent_runtime/test_learning_degradation.py tests/architecture/test_learning_isolation.py`
- [x] 两臂指标与判定可复现，基线臂按配置哈希复用
- [x] 报告含每个 case 的通过/失败原因并以不可变行落库
- [x] 回放不在 Run 请求路径，执行端口声明 BACKGROUND 工作负载
- [x] [S-03][integration] 覆盖回放可复现与报告不可变
- [x] [S-10][integration] 覆盖两臂门禁判定与基线复用
- [x] [S-15][integration] 覆盖批量 case 的逐项判定与缺项失败
- [x] [E-01][integration] 覆盖学习链路故障降级、warning 与计数
- [x] [B-06][contract] 覆盖架构隔离与后台配额断言
- [x] [RULE-06] 学习链路失败不影响主 Run 终态

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实 Snapshot 重放 + 真实 PostgreSQL | 两次报告指标与内容哈希一致；报告含原因；不可变落库 | tests/agent_runtime/test_learning_replay.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay.py"] | verified |
| S-10 | integration | 真实两臂回放 + 真实 PostgreSQL | 提升达标才放行；基线臂复用；判定可审计 | tests/agent_runtime/test_learning_replay_gate.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py"] | verified |
| S-15 | integration | 真实两臂回放 + 真实 PostgreSQL | 缺项/单项失败即失败；成功按 case 计；报告含逐项结果 | tests/agent_runtime/test_learning_replay_gate.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py","-k","batch_items"] | verified |
| E-01 | integration | 学习链路故障注入，主执行链真实运行 | 主 Run 终态不变；warning 与失败计数；业务事实不回滚 | tests/agent_runtime/test_learning_degradation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_degradation.py"] | verified |
| B-06 | contract | 源码导入图 + 模型池配额 | 请求路径不导入回放模块；回放走 BACKGROUND | tests/architecture/test_learning_isolation.py | ["uv","run","pytest","-q","tests/architecture/test_learning_isolation.py"] | verified |

### Acceptance Evidence

- RED: 去掉实现后三条回放验收命令均 collection error（`LearningReplay` 缺失），2026-10-31。
- GREEN: 四条命令合计 `7 passed in 0.54s`；指标白名单 `tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py` → `20 passed`。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | FAIL: collection error | PASS: 1 passed | `test_s03_replay_reports_are_reproducible_and_immutable` | 真实 PostgreSQL 的 eval_case/eval_report；两次回放 content_hash 与 metrics 一致、版本递增 | verified |
| S-10 | FAIL: collection error | PASS: 2 passed | `test_s10_gate_requires_improvement_and_reuses_baseline`、`test_s10_gate_rejects_without_improvement` | 真实两臂编排 + 基线报告复用 + 门禁判定落库 | verified |
| S-15 | FAIL: collection error | PASS: `-k batch_items` 1 passed | `test_s15_batch_items_missing_or_failed_make_the_case_fail` | 逐项计数来自 CaseOutcome；报告含 item 计数 | verified |
| E-01 | FAIL: collection error | PASS: 1 passed | `test_e01_learning_failure_degrades_to_no_learning` | 真实 Run 行终态不变；warning 与 `learning_pipeline_failures_total` 记录 | verified |
| B-06 | —（架构断言） | PASS: 2 passed | `test_b06_replay_is_not_imported_by_the_request_path`、`test_b06_replay_uses_background_model_budget` | AST 扫描真实请求路径文件；WORKLOAD=BACKGROUND | verified |

> 边界说明：本任务交付两臂编排、判定与报告；执行端 `CaseRunner` 由装配层注入（测试为脚本化 provider），冻结 Snapshot 到 CaseRunner 的生产装配留给激活任务接线。
- S-03: verified — automated command passed; run_id=9807d8f30e8b4f16b0edb4382d86571e (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=9807d8f30e8b4f16b0edb4382d86571e (confirmed_by: runner)
- S-15: verified — automated command passed; run_id=9807d8f30e8b4f16b0edb4382d86571e (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=9807d8f30e8b4f16b0edb4382d86571e (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=9807d8f30e8b4f16b0edb4382d86571e (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-005: 任务内自检与择优

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: agent-self-evolution.design.md#3.4 接口设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-04
- **Estimate**: 1.5d
- **Capability**: 通过注册表接入 verifier 后，Run 只输出通过验证的候选；全部不通过时显式失败

### Description

实现 verifier 注册表与有界择优：`register_verifier(task_family, verifier)` 支持直接注册与配置映射批量装载，重复注册抛错；`run_verification(candidates, verifier, max_attempts)` 保证拒绝的候选不成为最终答案，超限返回 `VERIFICATION_EXHAUSTED`；Run 接入后未通过验证的候选不流式输出、不落历史。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_verification_loop.py`
- [x] 重复注册抛错；尝试次数受 `verification_max_attempts` 约束
- [x] 全部候选不通过时返回显式失败状态
- [x] [S-04][integration] 覆盖注册来源、择优、上限与失败出口
- [x] [harness-api#RULE-api-001] verifier：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py`；新错误码文案只来自 `config/api-messages.yaml`
- [x] [harness-api#RULE-api-002] verifier：`uv run pytest -q tests/console_skill/test_import_idempotency.py`；不新增或修改 POST 幂等端点
- [x] [harness-secret#RULE-secret-001] verifier：`uv run pytest -q tests/test_logging_redaction.py`；文案与验证原因不含凭据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 verifier 注册表与执行链 | 只输出通过验证的候选；尝试次数不超上限；全失败返回显式失败 | tests/agent_runtime/test_verification_loop.py | ["uv","run","pytest","-q","tests/agent_runtime/test_verification_loop.py"] | verified |

### Acceptance Evidence

- RED: 去掉实现后 `uv run pytest -q tests/agent_runtime/test_verification_loop.py` → collection error（`RunnerVerificationExhausted` / `VerifierRegistry` 缺失），2026-10-31。
- GREEN: 同命令 → `4 passed in 0.30s`；回归 `tests/agent_core/test_runner.py + tests/test_error_catalog.py + tests/test_memory_redesign_error_catalog.py + tests/contracts` → `1358 passed`。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | FAIL: collection error（verifier API 缺失） | PASS: 4 passed | `test_s04_only_verified_candidate_reaches_the_answer`、`test_s04_exhaustion_is_explicit_and_bounded`、`test_s04_run_verification_stops_at_the_attempt_limit`、`test_s04_registry_rejects_duplicates_and_unknown_families` | 真实 `AgentRunner` 执行链 + 真实注册表；脚本化 provider 只替代模型 | verified |
- S-04: verified — automated command passed; run_id=e7d29895a8924073828686f832305549 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] resumed (in-progress)
- [2026-10-31] completed (done)

---

## TASK-006: 跨 Run 聚合与经验候选生成

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.3 数据设计
- **Spec-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Rule-Owner-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Acceptance-Refs**: S-05, S-16, E-02, RULE-03
- **Estimate**: 2d
- **Capability**: 同族成功轨迹生成 PROCEDURE/FAILURE_PATTERN/VERIFICATION 候选，状态 PROPOSED 且不参与注入；批量轨迹带批量触发文本；删除后不复活

### Description

新增跨 Run 聚合服务（worker 内离线调用）：按任务族聚合成功且无纠正的轨迹，达到 `experience_min_success_runs` 后生成经验候选；写入复用 `MemoryService` 的 scope 锁、幂等回执与 history（AGENT 写入必须带真实来源事件证据），并在同一事务写 `task_signature`、`trigger_text` 与 `evidence_json`（来源 run_id、成功次数、逐项总数）；批量轨迹的 `trigger_text` 必须包含批量语义；已删除/禁用的 key 直接跳过（不复活）；候选状态 PROPOSED，默认注入策略下不进入上下文。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_experience_candidates.py`
- [x] 跨 Run 聚合遵守最小成功轨迹数与任务族边界
- [x] forget_epoch 之后不生成、不复活
- [x] [S-05][integration] 覆盖候选生成、证据与不注入
- [x] [S-16][integration] 覆盖批量轨迹的 PROCEDURE 与批量触发文本
- [x] [E-02][integration] 覆盖删除后不复活
- [x] [harness-memory#RULE-memory-002] verifier：同命令；写入走 MemoryService（scope 锁、幂等回执、history 同事务）
- [x] [harness-memory#RULE-memory-004] verifier：同命令；tombstone/禁用 key 跳过，不复活
- [x] [harness-memory#RULE-memory-005] verifier：同命令；只读取 COMPLETED Run，写入幂等
- [x] [RULE-03] 学习物可溯源到来源 Run 与事件（evidence 含 run_id 与 source_event_id）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | 生成 PROCEDURE/FAILURE_PATTERN；evidence 含 run_id 与次数；不进入注入 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py"] | verified |
| S-16 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | 批量轨迹生成 PROCEDURE；trigger_text 含批量语义；不参与注入 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","batch"] | verified |
| E-02 | integration | 真实 forget 事务 + 真实聚合 job | 删除后不注入；forget_epoch 递增；候选不复活 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","forget"] | verified |

### Acceptance Evidence

- RED: 去掉实现后 `uv run pytest -q tests/agent_runtime/test_experience_candidates.py` → collection error（`ExperienceCandidates` 缺失），2026-10-31。
- GREEN: 同命令 → `3 passed in 0.75s`；写入路径由真实 `MemoryService` 执行。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | FAIL: collection error | PASS: 1 passed | `test_s05_successful_traces_produce_proposed_experiences` | 真实 Run/事件行 + 真实 MemoryService 写入（history/receipt 同事务）；证据含来源 run_id | verified |
| S-16 | FAIL: collection error | PASS: `-k batch` 1 passed | `test_s16_batch_traces_produce_procedure_with_batch_trigger` | 批量轨迹 30 项；trigger_text 含批量语义 | verified |
| E-02 | FAIL: collection error | PASS: `-k forget` 1 passed | `test_e02_forget_epoch_blocks_resurrection` | 真实 tombstone 行 + forget_epoch=1；候选不复活、原行不变 | verified |
- S-05: verified — automated command passed; run_id=cf64ea0ac6c54a67b456ce081785be5a (confirmed_by: runner)
- S-16: verified — automated command passed; run_id=cf64ea0ac6c54a67b456ce081785be5a (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=cf64ea0ac6c54a67b456ce081785be5a (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-007: 记忆与经验检索门控

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-003, TASK-006
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.4 接口设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-003
- **Rule-Owner-Refs**: harness-memory#RULE-memory-003
- **Acceptance-Refs**: S-07, S-11, S-13, B-01, B-02, B-03, RULE-02, RULE-09
- **Estimate**: 2d
- **Capability**: 记忆只放行过阈值条目；ACTIVE 经验按任务族与目标文本两通道命中且不受 inject_inferred 影响；同键只注入最新；请求期零新增 IO

### Description

把选择改为阈值门控 + IDF 词法 + 重要度 + 时效 + 使用归因打分；经验走两条通道（任务族 / 目标文本阈值），两路命中同一 key 只保留一条；ACTIVE 经验注入不受 `inject_inferred` 限制；偏好独立限额；同键取最新有效版本；按分数贪心装箱并记录丢弃原因；候选 token 集在缓存装载时预计算，候选集读取保持完整有界集合与 revision 缓存；用标注集校准 `retrieval_min_score`。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_core/test_memory_selection_gating.py tests/agent_core/test_memory_selection_quality.py tests/agent_runtime/test_experience_retrieval_lanes.py`
- [x] 低于阈值不注入；空查询只注入偏好类；偏好独立限额
- [x] 经验两通道命中、去重，且 ACTIVE 经验不受 inject_inferred 影响
- [x] 条数/字节双上限与丢弃计数；同键只留最新版本
- [x] token 集在缓存装载时预计算，请求期不重新 token 化
- [x] [S-07][unit] 覆盖阈值、空查询、偏好限额与确定性
- [x] [S-11][unit] 覆盖标注集校准与噪音下降
- [x] [S-13][integration] 覆盖非标命中与 skill 后族切换
- [x] [B-01][unit] 覆盖预算与丢弃计数
- [x] [B-02][unit] 覆盖同键多版本
- [x] [B-03][unit] 覆盖上界规模、无外部 IO 与预计算
- [x] [harness-memory#RULE-memory-003] verifier：同命令；完整有界集合、无 latest-N 截断、revision 缓存语义不变
- [x] [RULE-09] 请求期零新增 DB 查询与网络调用
- [x] 把检索规范的判定文本同步为阈值门控口径并重跑 spec gate

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | unit | 纯函数打分与选择 | 阈值以下不注入；空查询只注入偏好；两次结果一致 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","gating"] | verified |
| S-11 | unit | 纯函数 + 标注集 | 应注入全召回、不应注入全过滤、噪音下降 | tests/agent_core/test_memory_selection_quality.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_quality.py"] | verified |
| S-13 | integration | 真实 Runtime + 真实 PostgreSQL | 目标文本命中、skill 后族切换、两路不重复注入 | tests/agent_runtime/test_experience_retrieval_lanes.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_retrieval_lanes.py"] | verified |
| B-01 | unit | 纯选择与装箱 | 不超 max_items/max_bytes；丢弃计数正确 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","budget"] | verified |
| B-02 | unit | 纯选择 | 同键只注入最新有效版本 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","versions"] | verified |
| B-03 | unit | 纯选择 + 缓存装载 | 上界规模正确、无外部 IO、请求期不重新 token 化 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","upper_bound"] | verified |

### Acceptance Evidence

- RED: 去掉实现后 gating/quality 两条命令 collection error（`record_tokens`/阈值门控 API 缺失），2026-10-31。
- GREEN: gating `4 passed`、quality `1 passed`、lanes `2 passed`；回归 `selection/render/evidence` 71 passed、`memory_redesign_context` 20 passed（按新限额口径更新两处断言）。
- Spec：`harness-memory#RULE-memory-003` 文本已同步阈值门控与偏好独立限额；refresh 后 hash 一致。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-07 | FAIL: collection error | PASS: 4 passed | `test_s07_gating_excludes_low_score_and_empty_query_facts` | 纯函数选择；偏好条目/字节限额 | verified |
| S-11 | FAIL: collection error | PASS: 1 passed | `test_s11_threshold_gate_keeps_recall_and_cuts_noise` | 标注集：2 条应注入全命中、2 条噪音全过滤；两次结果一致 | verified |
| S-13 | FAIL: collection error | PASS: 2 passed | `test_s13_goal_match_hits_experience_without_skill`、`test_s13_family_switch_after_skill_execution` | 真实 PostgreSQL 候选集；PROPOSED 不注入、ACTIVE 注入、目标文本与族切换各一路 | verified |
| B-01 | FAIL: collection error | PASS: `-k budget` 1 passed | `test_b01_budget_and_drop_counters` | 偏好条数/字节限额 + 渲染层丢弃计数 | verified |
| B-02 | FAIL: collection error | PASS: `-k versions` 1 passed | `test_b02_versions_only_latest_is_injected` | 同键多版本只保留最新 | verified |
| B-03 | FAIL: collection error | PASS: `-k upper_bound` 1 passed | `test_b03_upper_bound_selection_stays_in_process` | 1200 条候选、请求期仅 token 化查询一次 | verified |
- S-07: verified — automated command passed; run_id=5445e4f3a28743959b981c25969e7205 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=5445e4f3a28743959b981c25969e7205 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=5445e4f3a28743959b981c25969e7205 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=5445e4f3a28743959b981c25969e7205 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=5445e4f3a28743959b981c25969e7205 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=5445e4f3a28743959b981c25969e7205 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-008: 激活门禁、自动晋升与回滚

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-006, TASK-007
- **Source**: agent-self-evolution.design.md#3.4 接口设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui#RULE-ui-001
- **Rule-Owner-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui#RULE-ui-001
- **Acceptance-Refs**: S-06, S-08, S-12, RULE-01, RULE-04, RULE-05, RULE-07, RULE-08
- **Estimate**: 2d
- **Capability**: 经验经两臂门禁自动进入 TRIAL，TRIAL 期信号不劣化自动晋升、劣化自动退休；激活/停用写审计；新 Run 冻结激活集

### Description

实现经验状态机 PROPOSED → TRIAL → ACTIVE → RETIRED；回放达标自动进入 TRIAL 并写审计；TRIAL 期按 `experience_trial_runs` 统计信号，超过 `experience_trial_max_regression` 自动 RETIRED，否则自动 ACTIVE；白名单外类别保持待人工（`learning_admin activate`）；`learning_admin` 提供 list/replay/activate/retire/explain；平台设置新增 `learning` 分组并把设置与 ACTIVE 经验 id/version 集合冻结进 Snapshot；激活只影响后续 Run。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_experience_activation.py tests/agent_runtime/test_experience_trial_promotion.py`
- [x] 状态机与门禁：不达标不可激活；白名单外类别不自动晋升
- [x] TRIAL 期自动晋升与自动退休按信号判定并写审计
- [x] 激活/停用写审计；停用保留历史行（RETIRED）
- [x] ACTIVE 经验集合在 Run 首个请求冻结，激活只影响后续 Run；平台设置新增 `learning` 分组
- [x] [S-06][integration] 覆盖门禁、激活、后续 Run 注入与 inject_inferred 无关
- [x] [S-08][integration] 覆盖审计与停用回滚
- [x] [S-12][integration] 覆盖 TRIAL 自动晋升/退休与白名单
- [x] [harness-memory#RULE-memory-001] verifier：同命令；自然语言记忆仍需用户确认，白名单外激活需人工
- [x] [harness-memory#RULE-memory-006] verifier：同命令；激活集在 Run 首个请求冻结
- [x] [harness-snapshot#RULE-snapshot-001] verifier：同命令；激活只影响后续 Run，终态写入保持 CAS
- [x] [RULE-01][RULE-05][RULE-07][RULE-08] 门禁、冻结、两通道与自动晋升规则逐条落点
- [x] [harness-frontend#RULE-front-001] verifier：`bash -lc 'uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck'`；设置页按元数据渲染新分组
- [x] [harness-i18n#RULE-i18n-001] verifier：`bash -lc 'uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py'`；zh/en 词条齐全
- [x] [harness-ui#RULE-ui-001] verifier：`bash -lc 'uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build'`；复用既有字段行，无布局变更

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | integration | 真实回放门禁 + 真实 PostgreSQL + 真实审计 | 不达标拒绝；达标激活并写审计；只影响后续 Run；inject_inferred=false 仍注入 | tests/agent_runtime/test_experience_activation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","gate"] | verified |
| S-08 | integration | 真实 PostgreSQL + 真实审计 | 激活/停用各写审计；停用后不注入；历史行保留 | tests/agent_runtime/test_experience_activation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","audit"] | verified |
| S-12 | integration | 真实状态机 + 真实 PostgreSQL + 真实审计 | 自动进 TRIAL；信号不劣化晋升；劣化退休；白名单外不自动晋升 | tests/agent_runtime/test_experience_trial_promotion.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_trial_promotion.py"] | verified |

### Acceptance Evidence

- RED: 去掉实现后两条验收命令 collection error（`LearningActivation` 缺失），2026-10-31。
- GREEN: `3 passed in 0.54s`；回归 `tests/contracts` 1312 passed、console 设置三件套 27 passed。
- 平台设置新增 `learning` 分组（contracts + Console catalog + zh/en 词条），字段范围与白名单校验生效。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-06 | FAIL: collection error | PASS: `-k gate` 1 passed | `test_s06_activation_gate_requires_replay_and_freezes_existing_runs` | 真实 eval_report 判定 + 真实 user_memory + 真实 control 审计；旧 loader 冻结、新 loader 注入（inject_inferred 默认 false） | verified |
| S-08 | FAIL: collection error | PASS: `-k audit` 1 passed | `test_s08_retirement_audits_and_stops_injection` | 真实审计两行（ACTIVATE/RETIRE）、行保留 RETIRED、停用后不注入 | verified |
| S-12 | FAIL: collection error | PASS: 1 passed | `test_s12_auto_promotion_and_retirement_follow_trial_signals` | 真实状态机 + 审计：TRIAL→ACTIVE / TRIAL→RETIRED / 白名单外 MANUAL_REQUIRED | verified |

> 边界说明：激活集冻结在 Run 首个请求捕获；`learning` 分组的逐项设置在 Run 侧冻结仍需接入 `policy_json`（当前仅作为平台设置与默认值暴露）。
- S-06: verified — automated command passed; run_id=aff2669583ad4d5da2a142df51ee3a4a (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=aff2669583ad4d5da2a142df51ee3a4a (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=aff2669583ad4d5da2a142df51ee3a4a (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=628b2f31970642b7b13331d25030351e (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=628b2f31970642b7b13331d25030351e (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=628b2f31970642b7b13331d25030351e (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] resumed (in-progress)
- [2026-10-31] completed (done)

---

## TASK-009: 需求盘点与测试分层核验

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008
- **Source**: agent-self-evolution.design.md#2.5 验收条件
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-05
- **Estimate**: 0.5d
- **Capability**: 执行 `tests/agent_evolution_inventory.py` 能核对覆盖表、manifest、spec-context 与真实盘面一致

### Description

新增 `tests/agent_evolution_inventory.py` 并在任务收口时运行；核对 21 个场景的 owner、命令、层级与证据是否与 manifest、spec-context 一致；确认 unit/integration/contract 分层符合设计（无新增浏览器旅程）；扰动一项覆盖或证据行时失败并指名条目。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_evolution_inventory.py`
- [ ] 覆盖表每行唯一负责人且终态，manifest 与覆盖表同 ID/owner/命令
- [ ] 终态场景在 owner 的 Acceptance Evidence 中登记
- [ ] [B-05][contract] 覆盖四者一致与扰动定位
- [ ] [harness-test#RULE-test-001] verifier：`uv run pytest -q tests/agent_evolution_inventory.py tests/agent_core/test_memory_selection_gating.py tests/agent_runtime/test_learning_replay.py tests/agent_runtime/test_runtime_schema_parity.py`；unit/integration/contract 分层符合设计
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-05 | contract | 真实盘面：任务文件、manifest、spec-context、测试文件 | 四者逐项一致；缺失时失败并指名条目 | tests/agent_evolution_inventory.py | ["uv","run","pytest","-q","tests/agent_evolution_inventory.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)
