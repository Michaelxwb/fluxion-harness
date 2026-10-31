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
| S-01 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 consolidation job + 真实 PostgreSQL | TASK-003 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_signal.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-02 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Runtime + 真实 PostgreSQL；逐请求比对 | TASK-003 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_injection_attribution.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-03 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Snapshot 重放 + 真实 PostgreSQL | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-04 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 verifier 注册表与执行链 | TASK-005 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_verification_loop.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-05 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-06 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实回放门禁 + 真实 PostgreSQL + 真实审计 | TASK-008 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","gate"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-07 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯函数打分与选择，无 IO | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","gating"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-08 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 PostgreSQL + 真实审计写入 | TASK-008 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","audit"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-09 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Run/事件数据 + 真实 PostgreSQL；worker 离线抽取 | TASK-002 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-10 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实两臂回放 + 真实 PostgreSQL；脚本化 provider | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-11 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯函数打分与选择；标注集 + 固定语料 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_quality.py"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-12 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实状态机 + 真实 PostgreSQL + 真实审计 | TASK-008 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_trial_promotion.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-13 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Runtime + 真实 PostgreSQL；真实候选集 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_retrieval_lanes.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-14 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实批量 Run/逐项 tool 事件 + 真实 PostgreSQL | TASK-002 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py","-k","batch_items"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-15 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实两臂回放 + 真实 PostgreSQL；逐项结果判定 | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py","-k","batch_items"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-16 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","batch"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-01 | agent-self-evolution.design.md#2.5 验收条件 | integration | 学习链路故障注入，主执行链真实运行 | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_degradation.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| E-02 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 forget 事务 + 真实 PostgreSQL + 真实聚合 job | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","forget"] | . | 600 | P1 | auto | local | - | - | - | no |
| B-01 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择与装箱逻辑 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","budget"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-02 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择逻辑 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","versions"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-03 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择 + 缓存装载；token 化与外部调用计数 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","upper_bound"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-04 | agent-self-evolution.design.md#2.5 验收条件 | contract | 真实 PostgreSQL 迁移链 + ORM + 契约模型 | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_schema_parity.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| B-05 | agent-self-evolution.design.md#2.5 验收条件 | contract | 真实盘面：任务文件、manifest、context、测试文件 | TASK-009 | planned | ["uv","run","pytest","-q","tests/agent_evolution_inventory.py"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-06 | agent-self-evolution.design.md#2.5 验收条件 | contract | 源码导入图 + 模型池配额（架构断言） | TASK-004 | planned | ["uv","run","pytest","-q","tests/architecture/test_learning_isolation.py"] | . | 300 | P1 | auto | none | - | - | - | no |

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-09, S-14
- **Estimate**: 2d
- **Capability**: Run 完成后可查询到派生的任务族；worker 能从真实成功轨迹自动抽取脱敏、去重、限量的 Golden Set，批量 case 带 item_count 与逐项判据

### Description

实现纯函数 `derive_task_family(events, skills)`：取首个成功执行的 skill key，无 skill 回退 `agent_id`；Run 完成时写入 `run_record.task_family`。实现 Golden Set 抽取 job：筛选成功、无负向信号、未取消的 Run，脱敏后按 `(task_family, content_hash)` 去重并落 `eval_case`，每族受 `eval_max_cases` 限制并保留时间切分；批量 Run 记录 `item_count` 与逐项判据，超过 `eval_max_items_per_case` 标记 incomplete；抽取使用 claim/lease，不改变 Run 状态。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_eval_cases.py`
- [ ] 任务族派生覆盖 skill 与无 skill 两条路径，Run 完成时落列
- [ ] 抽取筛选、脱敏、去重、上限与时间切分可验证
- [ ] 批量 case 记录 item_count 与逐项判据，超限标记 incomplete
- [ ] 抽取在 worker 内执行且不改变 Run 状态
- [ ] [S-09][integration] 覆盖族归属、排除条件与限量
- [ ] [S-14][integration] 覆盖批量条目语义、上限与逐项脱敏
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-09 | integration | 真实 Run/事件数据 + 真实 PostgreSQL | 按 skill 推导族、失败/取消/负向信号不入选、canary 不落库、限量与时间切分 | tests/agent_runtime/test_learning_eval_cases.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py"] | planned |
| S-14 | integration | 真实批量 Run/逐项 tool 事件 + 真实 PostgreSQL | item_count 与逐项判据、超限 incomplete、逐项脱敏 | tests/agent_runtime/test_learning_eval_cases.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_eval_cases.py","-k","batch_items"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-003: 学习信号与注入归因

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-01, S-02
- **Estimate**: 1d
- **Capability**: 每次模型请求的注入可归因且按选择 hash 去重；用户重问/否定产生 LEARNING_SIGNAL 且不影响 Run 终态

### Description

由 consolidation job 在事件边界计算隐式信号（重问、否定、改写）并写 `LEARNING_SIGNAL`；在模型请求边界写 `MEMORY_INJECTED`（memory_key/version/score/decision、丢弃原因计数、selection_hash），选择未变化时以 hash 引用上一条而不重复写行；两类事件均不进入 `HISTORY_EVENT_TYPES`。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_signal.py tests/agent_runtime/test_memory_injection_attribution.py`
- [ ] 归因与模型请求逐条一致，丢弃原因按类别计数
- [ ] 选择未变化时以 selection_hash 去重，不重复写事件行
- [ ] 信号检测不改变 Run 终态与历史重建结果
- [ ] [S-01][integration] 覆盖信号落库与终态不变
- [ ] [S-02][integration] 覆盖归因一致与去重

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 真实 consolidation job + 真实 PostgreSQL | 产生 LEARNING_SIGNAL；Run 终态不变；事件不进模型历史 | tests/agent_runtime/test_learning_signal.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_signal.py"] | planned |
| S-02 | integration | 真实 Runtime + 真实 PostgreSQL | key/version 与请求一致；含 score/decision；按 hash 去重 | tests/agent_runtime/test_memory_injection_attribution.py | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_injection_attribution.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-004: 两臂回放与失败降级

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-data#RULE-data-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-03, S-10, S-15, E-01, B-06, RULE-06
- **Estimate**: 2d
- **Capability**: 同一 Golden Set 可做基线/候选两臂回放并产出可审计报告，批量 case 按逐项结果判定；学习链路失败时主 Run 照常完成

### Description

实现基于冻结 Snapshot 的两臂回放：读取 `eval_case`，分别以不带经验（基线臂）与带经验（候选臂）执行并计算成功率、纠错轮次、注入精度；批量 case 按逐项结果判定，缺项或单项失败即判失败，报告附 `item_count` 与逐项原因；基线臂结果按 `(case, base_config_hash)` 缓存复用；报告 append-only 落 `eval_report`（含 content_hash 与 decision）；回放在 worker 内执行并使用 BACKGROUND 模型配额；学习链路异常降级为无学习并写 warning 与失败计数。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_replay.py tests/agent_runtime/test_learning_replay_gate.py tests/agent_runtime/test_learning_degradation.py tests/architecture/test_learning_isolation.py`
- [ ] 两臂指标与判定可复现，基线臂按配置哈希复用
- [ ] 报告含每个 case 的通过/失败原因并以不可变行落库
- [ ] 回放不在 Run 请求路径，模型调用走 BACKGROUND 配额
- [ ] [S-03][integration] 覆盖回放可复现与报告不可变
- [ ] [S-10][integration] 覆盖两臂门禁判定与基线复用
- [ ] [S-15][integration] 覆盖批量 case 的逐项判定与缺项失败
- [ ] [E-01][integration] 覆盖学习链路故障降级、warning 与计数
- [ ] [B-06][contract] 覆盖架构隔离与后台配额断言
- [ ] [RULE-06] 学习链路失败不影响主 Run 终态

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实 Snapshot 重放 + 真实 PostgreSQL | 两次报告指标与内容哈希一致；报告含原因；不可变落库 | tests/agent_runtime/test_learning_replay.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay.py"] | planned |
| S-10 | integration | 真实两臂回放 + 真实 PostgreSQL | 提升达标才放行；基线臂复用；判定可审计 | tests/agent_runtime/test_learning_replay_gate.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py"] | planned |
| S-15 | integration | 真实两臂回放 + 真实 PostgreSQL | 缺项/单项失败即失败；成功按 case 计；报告含逐项结果 | tests/agent_runtime/test_learning_replay_gate.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay_gate.py","-k","batch_items"] | planned |
| E-01 | integration | 学习链路故障注入，主执行链真实运行 | 主 Run 终态不变；warning 与失败计数；业务事实不回滚 | tests/agent_runtime/test_learning_degradation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_degradation.py"] | planned |
| B-06 | contract | 源码导入图 + 模型池配额 | 请求路径不导入回放模块；回放走 BACKGROUND | tests/architecture/test_learning_isolation.py | ["uv","run","pytest","-q","tests/architecture/test_learning_isolation.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-005: 任务内自检与择优

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: agent-self-evolution.design.md#3.4 接口设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-04
- **Estimate**: 1.5d
- **Capability**: 通过注册表接入 verifier 后，Run 只输出通过验证的候选；全部不通过时显式失败

### Description

实现 verifier 注册表与有界择优：`register_verifier(task_family, verifier)` 支持从配置 / Skill 断言装载，重复注册抛错；`run_verification(candidates, verifier, max_attempts)` 保证拒绝的候选不成为最终答案，超限返回 `VERIFICATION_EXHAUSTED`。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_verification_loop.py`
- [ ] 重复注册抛错；尝试次数受 `verification_max_attempts` 约束
- [ ] 全部候选不通过时返回显式失败状态
- [ ] [S-04][integration] 覆盖注册来源、择优、上限与失败出口
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 verifier 注册表与执行链 | 只输出通过验证的候选；尝试次数不超上限；全失败返回显式失败 | tests/agent_runtime/test_verification_loop.py | ["uv","run","pytest","-q","tests/agent_runtime/test_verification_loop.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-006: 跨 Run 聚合与经验候选生成

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.3 数据设计
- **Spec-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Rule-Owner-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Acceptance-Refs**: S-05, S-16, E-02, RULE-03
- **Estimate**: 2d
- **Capability**: 同族成功轨迹生成 PROCEDURE/FAILURE_PATTERN/VERIFICATION 候选，状态 PROPOSED 且不参与注入；批量轨迹带批量触发文本；删除后不复活

### Description

新增跨 Run 聚合并复用 consolidation job 的 claim/lease 语义：按任务族聚合成功且无纠正的轨迹，达到 `experience_min_success_runs` 后生成经验候选，写 `task_signature`、`trigger_text` 与 `evidence_json`（来源 run_id、成功次数、触发证据）；批量轨迹的 `trigger_text` 必须包含批量语义（多个目标、逐项处理或核对数量）；复用记忆写入锁序、幂等回执与 forget_epoch 校验；候选状态 PROPOSED，不进入注入。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_experience_candidates.py`
- [ ] 跨 Run 聚合遵守最小成功轨迹数与任务族边界
- [ ] forget_epoch 之后不生成、不复活
- [ ] [S-05][integration] 覆盖候选生成、证据与不注入
- [ ] [S-16][integration] 覆盖批量轨迹的 PROCEDURE 与批量触发文本
- [ ] [E-02][integration] 覆盖删除后不复活
- [ ] [harness-memory#RULE-memory-002] verifier：同命令；Runtime 单一写入方、锁序与回执不变
- [ ] [harness-memory#RULE-memory-004] verifier：同命令；tombstone/forget_epoch 优先，停用不删历史
- [ ] [harness-memory#RULE-memory-005] verifier：同命令；复用 claim/lease 与最多一次语义
- [ ] [RULE-03] 学习物可溯源到来源 Run 与事件

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | 生成 PROCEDURE/FAILURE_PATTERN；evidence 含 run_id 与次数；不进入注入 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py"] | planned |
| S-16 | integration | 真实跨 Run 聚合 job + 真实 PostgreSQL | 批量轨迹生成 PROCEDURE；trigger_text 含批量语义；不参与注入 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","batch"] | planned |
| E-02 | integration | 真实 forget 事务 + 真实聚合 job | 删除后不注入；forget_epoch 递增；候选不复活 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","forget"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-007: 记忆与经验检索门控

- **Status**: draft
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

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_core/test_memory_selection_gating.py tests/agent_core/test_memory_selection_quality.py tests/agent_runtime/test_experience_retrieval_lanes.py`
- [ ] 低于阈值不注入；空查询只注入偏好类；偏好独立限额
- [ ] 经验两通道命中、去重，且 ACTIVE 经验不受 inject_inferred 影响
- [ ] 条数/字节双上限与丢弃计数；同键只留最新版本
- [ ] token 集在缓存装载时预计算，请求期不重新 token 化
- [ ] [S-07][unit] 覆盖阈值、空查询、偏好限额与确定性
- [ ] [S-11][unit] 覆盖标注集校准与噪音下降
- [ ] [S-13][integration] 覆盖非标命中与 skill 后族切换
- [ ] [B-01][unit] 覆盖预算与丢弃计数
- [ ] [B-02][unit] 覆盖同键多版本
- [ ] [B-03][unit] 覆盖上界规模、无外部 IO 与预计算
- [ ] [harness-memory#RULE-memory-003] verifier：同命令；完整有界集合、无 latest-N 截断、revision 缓存语义不变
- [ ] [RULE-09] 请求期零新增 DB 查询与网络调用
- [ ] 把检索规范的判定文本同步为阈值门控口径并重跑 spec gate

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | unit | 纯函数打分与选择 | 阈值以下不注入；空查询只注入偏好；两次结果一致 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","gating"] | planned |
| S-11 | unit | 纯函数 + 标注集 | 应注入全召回、不应注入全过滤、噪音下降 | tests/agent_core/test_memory_selection_quality.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_quality.py"] | planned |
| S-13 | integration | 真实 Runtime + 真实 PostgreSQL | 目标文本命中、skill 后族切换、两路不重复注入 | tests/agent_runtime/test_experience_retrieval_lanes.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_retrieval_lanes.py"] | planned |
| B-01 | unit | 纯选择与装箱 | 不超 max_items/max_bytes；丢弃计数正确 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","budget"] | planned |
| B-02 | unit | 纯选择 | 同键只注入最新有效版本 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","versions"] | planned |
| B-03 | unit | 纯选择 + 缓存装载 | 上界规模正确、无外部 IO、请求期不重新 token 化 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","upper_bound"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-008: 激活门禁、自动晋升与回滚

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-006, TASK-007
- **Source**: agent-self-evolution.design.md#3.4 接口设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-06, S-08, S-12, RULE-01, RULE-04, RULE-05, RULE-07, RULE-08
- **Estimate**: 2d
- **Capability**: 经验经两臂门禁自动进入 TRIAL，TRIAL 期信号不劣化自动晋升、劣化自动退休；激活/停用写审计；新 Run 冻结激活集

### Description

实现经验状态机 PROPOSED → TRIAL → ACTIVE → RETIRED；回放达标自动进入 TRIAL 并写审计；TRIAL 期按 `experience_trial_runs` 统计信号，超过 `experience_trial_max_regression` 自动 RETIRED，否则自动 ACTIVE；白名单外类别保持待人工（`learning_admin activate`）；`learning_admin` 提供 list/replay/activate/retire/explain；平台设置新增 `learning` 分组并把设置与 ACTIVE 经验 id/version 集合冻结进 Snapshot；激活只影响后续 Run。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_experience_activation.py tests/agent_runtime/test_experience_trial_promotion.py`
- [ ] 状态机与门禁：不达标不可激活；白名单外类别不自动晋升
- [ ] TRIAL 期自动晋升与自动退休按信号判定并写审计
- [ ] 激活/停用写审计；停用保留历史行（RETIRED）
- [ ] learning 设置与 ACTIVE 经验集合随 Run 冻结，激活只影响后续 Run
- [ ] [S-06][integration] 覆盖门禁、激活、后续 Run 注入与 inject_inferred 无关
- [ ] [S-08][integration] 覆盖审计与停用回滚
- [ ] [S-12][integration] 覆盖 TRIAL 自动晋升/退休与白名单
- [ ] [harness-memory#RULE-memory-001] verifier：同命令；自然语言记忆仍需用户确认，白名单外激活需人工
- [ ] [harness-memory#RULE-memory-006] verifier：同命令；激活集与 learning 设置随 Snapshot 冻结
- [ ] [harness-snapshot#RULE-snapshot-001] verifier：同命令；激活只影响后续 Run，终态写入保持 CAS
- [ ] [RULE-01][RULE-05][RULE-07][RULE-08] 门禁、冻结、两通道与自动晋升规则逐条落点

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | integration | 真实回放门禁 + 真实 PostgreSQL + 真实审计 | 不达标拒绝；达标激活并写审计；只影响后续 Run；inject_inferred=false 仍注入 | tests/agent_runtime/test_experience_activation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","gate"] | planned |
| S-08 | integration | 真实 PostgreSQL + 真实审计 | 激活/停用各写审计；停用后不注入；历史行保留 | tests/agent_runtime/test_experience_activation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","audit"] | planned |
| S-12 | integration | 真实状态机 + 真实 PostgreSQL + 真实审计 | 自动进 TRIAL；信号不劣化晋升；劣化退休；白名单外不自动晋升 | tests/agent_runtime/test_experience_trial_promotion.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_trial_promotion.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

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
