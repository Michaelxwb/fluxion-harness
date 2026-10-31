# Tasks: Agent 自进化

- **Source**: .code-flow/tasks/2026-10-31/agent-self-evolution/agent-self-evolution.design.md
- **Created**: 2026-10-31
- **Updated**: 2026-10-31

## Proposal

建立一条效果可度量的学习闭环：隐式信号与注入归因 → 基于 Snapshot 的回放评测 → 经验候选 → 回放达标后激活/回滚；同时把记忆召回从「有字面重叠就注入」改为「过阈值才注入」。所有学习物复用现有记忆体系与事件链路，失败降级为无学习，不改变主 Run 行为。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Runtime 事件写入 + 真实 PostgreSQL；脚本化 provider | TASK-002 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_signal.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-02 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Runtime + 真实 PostgreSQL；注入条目与模型请求逐条比对 | TASK-002 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_injection_attribution.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-03 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 Snapshot 重放 + 真实 PostgreSQL | TASK-003 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay.py"] | . | 900 | P0 | auto | local | - | - | - | no |
| S-04 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 verifier 注册与执行链；脚本化 provider | TASK-004 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_verification_loop.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-05 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 consolidation job + 真实 PostgreSQL | TASK-005 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-06 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实回放评测 + 真实 PostgreSQL + 真实审计 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","gate"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-07 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯函数打分与选择，无 IO | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","gating"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-08 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 PostgreSQL + 真实审计写入 | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","audit"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-01 | agent-self-evolution.design.md#2.5 验收条件 | unit | 学习链路注入故障，主执行链不注入 | TASK-003 | planned | ["uv","run","pytest","-q","tests/agent_core/test_learning_degradation.py"] | . | 300 | P1 | auto | none | - | - | - | no |
| E-02 | agent-self-evolution.design.md#2.5 验收条件 | integration | 真实 forget 事务 + 真实 PostgreSQL + 真实 consolidation | TASK-005 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","forget"] | . | 600 | P1 | auto | local | - | - | - | no |
| B-01 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择与装箱逻辑 | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","budget"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-02 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯选择逻辑 | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","versions"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-03 | agent-self-evolution.design.md#2.5 验收条件 | unit | 纯函数选择与缓存装载；候选规模取可配置上界 | TASK-006 | planned | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","upper_bound"] | . | 300 | P1 | auto | none | - | - | - | no |

> 覆盖设计中的全部 13 个场景（8 个 S、2 个 E、3 个 B）；无 E2E：本需求不新增浏览器旅程与公开 API 契约。

---

## TASK-001: 记忆与评测数据模型迁移

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: agent-self-evolution.design.md#3.3 数据设计, agent-self-evolution.design.md#4.4 数据迁移
- **Spec-Refs**: harness-data#RULE-data-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: RULE-03
- **Estimate**: 0.5d
- **Capability**: 迁移到 head 后 `user_memory` 具备激活状态/任务签名/证据列，且存在 `runtime.eval_case` 表

### Description

扩展 `runtime.user_memory`：`activation_state`（默认 ACTIVE，保持既有行为）、`task_signature`、`evidence_json`，扩展 category 约束，新增 ACTIVE 集合 partial index；新增 `runtime.eval_case` 表（租户、任务族、来源 Run、输入引用、判据、版本）。迁移单链、与 ORM 同构、不读环境变量。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py`
- [ ] 扩展列与约束，默认值保持既有记忆行为不变
- [ ] 新增 `eval_case` 表与唯一键、索引，标准列走 `StandardColumnsMixin`
- [ ] [harness-data#RULE-data-001] verifier：`uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py`；迁移与 ORM 同构、JSONB/时间/索引口径一致
- [ ] 记录 upgrade/downgrade 演练证据

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-03 | contract | 真实 PostgreSQL + 迁移链 + ORM | 新列与新表存在、默认值 ACTIVE、唯一键与索引齐全 | tests/agent_runtime/test_runtime_schema_parity.py | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_schema_parity.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-002: 学习信号与注入归因

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-01, S-02
- **Estimate**: 1.5d
- **Capability**: 每次 Run 产生可查询的注入归因；用户重问/否定产生 LEARNING_SIGNAL 事件且不影响 Run 终态

### Description

在 Run 结束或模型请求边界写 `MEMORY_INJECTED`（memory_key/version/score/decision + 丢弃原因计数）；按事件边界计算隐式信号（重问、否定、改写）写 `LEARNING_SIGNAL`；两类事件均不进入 `HISTORY_EVENT_TYPES`。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_signal.py tests/agent_runtime/test_memory_injection_attribution.py`
- [ ] 注入归因与模型请求逐条一致，丢弃原因按类别计数
- [ ] 信号检测不改变 Run 终态与历史重建结果
- [ ] [S-01][integration] 覆盖信号落库与终态不变
- [ ] [S-02][integration] 覆盖归因条目与请求一致

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 真实 Runtime + 真实 PostgreSQL | 产生 LEARNING_SIGNAL；Run 终态不变；事件不进模型历史 | tests/agent_runtime/test_learning_signal.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_signal.py"] | planned |
| S-02 | integration | 真实 Runtime + 真实 PostgreSQL | memory_key/version 与请求一致；含 score/decision；丢弃按 reason 计数 | tests/agent_runtime/test_memory_injection_attribution.py | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_injection_attribution.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-003: 评测回放与失败降级

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-03, E-01, RULE-06
- **Estimate**: 2d
- **Capability**: 同一 Golden Set 回放两次得到逐项一致的报告，学习链路失败时主 Run 照常完成

### Description

实现基于 Snapshot 的确定性回放：读取 `eval_case`，重放并计算成功率、纠错轮次、注入精度，报告落产物存储；学习链路任何异常降级为无学习，写 warning 与失败计数。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_replay.py tests/agent_core/test_learning_degradation.py`
- [ ] 回放使用冻结 Snapshot，两次报告指标逐项一致
- [ ] 报告含每个 case 的通过/失败原因并落 storage_key
- [ ] [S-03][integration] 覆盖回放可复现与报告落库
- [ ] [E-01][unit] 覆盖学习链路故障降级、warning 与计数

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实 Snapshot 重放 + 真实 PostgreSQL | 两次报告指标一致；报告含通过/失败原因；落 storage_key | tests/agent_runtime/test_learning_replay.py | ["uv","run","pytest","-q","tests/agent_runtime/test_learning_replay.py"] | planned |
| E-01 | unit | 学习链路故障注入，主执行链真实运行 | 主 Run 终态不变；warning 与失败计数；业务事实不回滚 | tests/agent_core/test_learning_degradation.py | ["uv","run","pytest","-q","tests/agent_core/test_learning_degradation.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-004: 任务内自检与择优

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.4 接口设计
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-04
- **Estimate**: 1.5d
- **Capability**: 注册 verifier 后，Run 只输出通过验证的候选；全部不通过时显式失败

### Description

实现 verifier 注册与有界择优：`register_verifier(task_family, verifier)`、`run_verification(candidates, verifier, max_attempts)`；拒绝的候选不得成为最终答案；超限返回 `VERIFICATION_EXHAUSTED`。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_verification_loop.py`
- [ ] 重复注册抛错；尝试次数受 `verification_max_attempts` 约束
- [ ] 全部候选不通过时返回显式失败状态
- [ ] [S-04][integration] 覆盖择优、上限与失败出口

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 verifier 注册与执行链 | 只输出通过验证的候选；尝试次数不超上限；全失败返回显式失败 | tests/agent_runtime/test_verification_loop.py | ["uv","run","pytest","-q","tests/agent_runtime/test_verification_loop.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-005: 经验候选生成

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: agent-self-evolution.design.md#3.3 数据设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Rule-Owner-Refs**: harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Acceptance-Refs**: S-05, E-02, RULE-03
- **Estimate**: 1.5d
- **Capability**: 成功且无纠正的轨迹生成 PROCEDURE/FAILURE_PATTERN/VERIFICATION 候选，状态 PROPOSED 且不参与注入；删除后不复活

### Description

扩展 consolidation job，按任务签名与最小成功轨迹数生成经验候选，写 `task_signature` 与 `evidence_json`；复用记忆写入的锁序、幂等回执与 forget_epoch 校验；候选状态 PROPOSED，不进入注入。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_experience_candidates.py`
- [ ] 候选生成遵守最小成功轨迹数与任务签名
- [ ] forget_epoch 之后不生成、不复活
- [ ] [S-05][integration] 覆盖候选生成、证据与不注入
- [ ] [E-02][integration] 覆盖删除后不复活
- [ ] [harness-memory#RULE-memory-002] verifier：`uv run pytest -q tests/agent_runtime/test_experience_candidates.py`；Runtime 单一写入方、锁序与回执不变
- [ ] [harness-memory#RULE-memory-004] verifier：同命令；tombstone/forget_epoch 优先，停用不删历史
- [ ] [harness-memory#RULE-memory-005] verifier：同命令；复用 consolidation job 的 claim/lease 与最多一次语义

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 真实 consolidation job + 真实 PostgreSQL | 生成 PROCEDURE/PROPOSED；evidence 含 run_id 与次数；不进入注入 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py"] | planned |
| E-02 | integration | 真实 forget 事务 + 真实 consolidation | 删除后不注入；forget_epoch 递增；候选不复活 | tests/agent_runtime/test_experience_candidates.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_candidates.py","-k","forget"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-006: 记忆检索打分与门控

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: agent-self-evolution.design.md#3.2 架构设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-003
- **Rule-Owner-Refs**: harness-memory#RULE-memory-003
- **Acceptance-Refs**: S-07, B-01, B-02, B-03, RULE-02
- **Estimate**: 1.5d
- **Capability**: 记忆注入只放行过阈值的条目；空查询不注入事实类；偏好独立限额；同键只注入最新版本

### Description

把选择改为阈值门控 + IDF 词法 + 重要度 + 时效 + 使用归因打分；偏好类单独条数/字节限额；同键取最新有效版本；按分数贪心装箱并记录丢弃原因；候选集读取保持用户完整有界集合与 revision 缓存，token 集在缓存装载时预计算。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_core/test_memory_selection_gating.py`
- [ ] 低于阈值不注入；空查询只注入偏好类
- [ ] 条数/字节双上限与丢弃计数；同键只留最新版本
- [ ] [S-07][unit] 覆盖阈值、空查询、偏好限额与确定性
- [ ] [B-01][unit] 覆盖预算与丢弃计数
- [ ] [B-02][unit] 覆盖同键多版本
- [ ] [B-03][unit] 覆盖可配置上界规模下的正确性、无外部 IO 与耗时记录
- [ ] [harness-memory#RULE-memory-003] verifier：`uv run pytest -q tests/agent_core/test_memory_selection_gating.py`；完整有界集合、无 latest-N 截断、revision 缓存语义不变

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | unit | 纯函数打分与选择 | 阈值以下不注入；空查询只注入偏好；两次结果一致 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","gating"] | planned |
| B-01 | unit | 纯选择与装箱 | 不超 max_items/max_bytes；丢弃计数正确 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","budget"] | planned |
| B-02 | unit | 纯选择 | 同键只注入最新有效版本 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","versions"] | planned |
| B-03 | unit | 纯选择 + 缓存装载 | 上界规模结果正确、无外部 IO、耗时入基准 | tests/agent_core/test_memory_selection_gating.py | ["uv","run","pytest","-q","tests/agent_core/test_memory_selection_gating.py","-k","upper_bound"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-007: 经验激活、审计与回滚

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-005
- **Source**: agent-self-evolution.design.md#3.4 接口设计, agent-self-evolution.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-06, S-08, RULE-01, RULE-04, RULE-05
- **Estimate**: 1.5d
- **Capability**: 经验只有回放达标才能激活；激活/停用写审计；停用即回滚；新 Run 冻结激活集

### Description

实现经验状态机 PROPOSED → TRIAL → ACTIVE → RETIRED；`learning_admin` CLI 提供 list/replay/activate/retire；激活门禁读取最近回放结果，不达标返回 `EXPERIENCE_EVAL_BELOW_THRESHOLD`；平台设置新增 `learning` 分组并随 Snapshot 冻结；激活/停用写审计。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_experience_activation.py`
- [ ] 状态机与门禁：不达标不可激活；高风险类别人工确认
- [ ] 激活/停用写审计；停用保留历史行（RETIRED）
- [ ] learning 设置随 Run 冻结，激活只影响后续 Run
- [ ] [S-06][integration] 覆盖门禁、激活与后续 Run 注入
- [ ] [S-08][integration] 覆盖审计与停用回滚
- [ ] [harness-memory#RULE-memory-001] verifier：同命令；自然语言记忆仍需用户确认，高风险激活需人工
- [ ] [harness-memory#RULE-memory-006] verifier：同命令；激活集与 learning 设置随 Snapshot 冻结
- [ ] [harness-snapshot#RULE-snapshot-001] verifier：同命令；激活只影响后续 Run，终态写入保持 CAS

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | integration | 真实回放评测 + 真实 PostgreSQL + 真实审计 | 不达标拒绝（EXPERIENCE_EVAL_BELOW_THRESHOLD）；达标激活并写审计；只影响后续 Run | tests/agent_runtime/test_experience_activation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","gate"] | planned |
| S-08 | integration | 真实 PostgreSQL + 真实审计 | 激活/停用各写审计；停用后不注入；历史行保留 | tests/agent_runtime/test_experience_activation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_experience_activation.py","-k","audit"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-008: 需求收口清单与测试分层核验

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007
- **Source**: agent-self-evolution.design.md#2.5 验收条件
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: RULE-03
- **Estimate**: 0.5d
- **Capability**: 执行 `tests/agent_evolution_inventory.py` 能核对覆盖表、manifest、spec-context 与真实盘面一致

### Description

新增 `tests/agent_evolution_inventory.py` 并在任务收口时运行；核对 13 个场景的 owner、命令、层级与证据是否与 manifest、spec-context 一致；确认 unit/integration/contract 分层符合设计（无新增浏览器旅程）。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_evolution_inventory.py`
- [ ] 覆盖表每行唯一负责人且终态，manifest 与覆盖表同 ID/owner/命令
- [ ] 终态场景在 owner 的 Acceptance Evidence 中登记
- [ ] [harness-test#RULE-test-001] verifier：`uv run pytest -q tests/agent_evolution_inventory.py tests/agent_core/test_memory_selection_gating.py tests/agent_runtime/test_learning_replay.py`；unit 覆盖纯逻辑、integration 覆盖真实 PostgreSQL、contract 覆盖迁移一致性

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-03 | contract | 真实盘面：任务文件、manifest、spec-context、测试文件 | 四者逐项一致；缺失时失败并指名条目 | tests/agent_evolution_inventory.py | ["uv","run","pytest","-q","tests/agent_evolution_inventory.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)
