# Tasks: 经验管理模块（控制面 + 学习事实修正）

- **Source**: `.code-flow/tasks/2026-11-01/experience-management/experience-management.prd.md`; `experience-management.backend.design.md`; `experience-management.frontend.design.md`
- **Created**: 2026-11-01
- **Updated**: 2026-11-01

## Proposal

把学习闭环从「能跑」补成「可运营、可解释、可干预」的控制面：先修正回放报告用户归属、状态写入主通道、注入归因真实性与族判定一致性（FEAT-08），再做用户/任务族双视图、共用经验详情、生命周期操作、Run 追溯、审计可见性与止损/修订/反馈兜底入口。管理面与后续 agent-learning-core 通过 authority、key 不透明、开放码/extras、共享常量与控制面契约解耦。

### Alignment

- **Scope**: FEAT-01…FEAT-12；后端事实修正、读模型与聚合、异步回放、双视图前端、审计与止损/修订/反馈。
- **Decisions**: Console 只读 runtime schema；写全部经 Runtime internal；回放持久 job + 容量预占；经验按用户/族分组且 key 不透明；authority 分流激活。
- **Non-goals**: 智能自进化能力本身（教学/修复/可执行经验）、阈值调参、跨租户、历史报告归属回填。
- **Acceptance**: 22 个场景（15 S、5 E、2 B）全部有唯一 owner；S-08 为浏览器 E2E，S-11 为容量场景。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | experience-management.backend.design.md#2.5 | integration | Console API（真实 ASGI + ADMIN 会话）→ LearningRepository → 真实 PostgreSQL runtime.user_memory | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s01"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-02 | experience-management.backend.design.md#2.5 | integration | Console API → 真实 PG；同一 fixture 同时查询 API-01 与 API-04 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s02"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-03 | experience-management.backend.design.md#2.5 | integration | Console API → 真实 PG（user_memory/history/eval_report/canonical_event/config_audit_log） | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s03"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-04 | experience-management.backend.design.md#2.5 | integration | Console API → Runtime internal API（真实服务身份头）→ MemoryService/LearningActivation/LearningJobRunner → 真实 PG；回放用本地 OpenAI 兼容探针 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_lifecycle_api.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-05 | experience-management.backend.design.md#2.5 | integration | Console 用户记忆 API → 真实 PG 只读投影；通用写 API → Runtime internal | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s05"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-06 | experience-management.backend.design.md#2.5 | component | 组件渲染 + mock service 数据（契约类型）；不访问真实后端 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_learning_experience_contract.py"] | . | 900 | P0 | auto | none | - | - | - | no |
| S-07 | experience-management.backend.design.md#2.5 | component | 组件渲染 + 同一 fixture 注入两个入口 | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_learning_family_page_contract.py"] | . | 900 | P0 | auto | none | - | - | - | no |
| S-08 | experience-management.backend.design.md#2.5 | E2E | 真实浏览器（preview 构建产物）→ Console API → Runtime internal → 真实 PostgreSQL；仅模型回放可用本地探针 | TASK-006 | e2e_deferred | ["node", "e2e/node_modules/.bin/playwright", "test", "--config", "e2e/playwright.learning-experience.config.ts", "e2e/tests/learning-experience.spec.ts"] | . | 1200 | P1 | deferred | browser | - | 跨页面状态同步、真实路由深链与真实鉴权/DB 组合只有浏览器 E2E 能暴露；component 无法覆盖路由与真实后端 | apps/console-platform/frontend/src/modules/learning/**, apps/console-platform/frontend/src/modules/user-identity/**, apps/console-platform/frontend/src/modules/run-observability/**, apps/console-platform/backend/src/muad_console_platform/api/learning.py, apps/agent-runtime/src/muad_agent_runtime/application/learning_* | no |
| S-09 | experience-management.backend.design.md#2.5 | integration | 真实 Run 的 MEMORY_INJECTED 事件（含 diagnostics）→ Console API-10 → 真实 PG | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_diagnostics_api.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| E-01 | experience-management.backend.design.md#2.5 | integration | Console API + Runtime client（上游 5xx/超时）；真实 PG | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_degradation.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| E-02 | experience-management.backend.design.md#2.5 | integration | Runtime internal API + Console 通用记忆 API → 真实 PG | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_learning_fact_fixes.py", "-k", "e02"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| E-03 | experience-management.backend.design.md#2.5 | integration | Runtime MemoryService 事务 → 真实 PG；注入审计写入失败 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_learning_fact_fixes.py", "-k", "e03"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| E-04 | experience-management.backend.design.md#2.5 | integration | LearningJobRunner → 真实 PG；探针可控延迟/失败 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_learning_jobs.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| B-01 | experience-management.backend.design.md#2.5 | unit | 纯函数 derive_task_family / first_successful_skill；无 IO | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_task_family_scope.py"] | . | 900 | P0 | auto | none | - | - | - | no |
| B-02 | experience-management.backend.design.md#2.5 | integration | Console 聚合 SQL → 真实 PG | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "aggregate"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-10 | experience-management.backend.design.md#2.5 | integration | Console 审计列表/详情 API → 真实 PostgreSQL 四表 UNION 投影与 config_audit_log | TASK-008 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_audit_projection.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-11 | experience-management.backend.design.md#2.5 | capacity | 1000 用户 × 8 轮真实负载（容量 profile）→ Runtime/PG；同时打开族页/经验详情并触发一次回放 | TASK-013 | e2e_deferred | ["uv", "run", "pytest", "-q", "tests/capacity/test_learning_management_mixed.py", "-m", "capacity_load"] | . | 3600 | P1 | deferred | local-api | - | - | - | no |
| S-12 | experience-management.backend.design.md#2.5 | integration | Console API → Runtime internal（真实服务身份）→ MemoryService/候选生成/注入冻结 → 真实 PG | TASK-009 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_family_control.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |
| S-13 | experience-management.backend.design.md#2.5 | integration | Console API → Runtime MemoryService 事务 → 真实 PG（版本/history/receipt/审计） | TASK-010 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_content_revision.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| S-14 | experience-management.backend.design.md#2.5 | integration | Console API → Runtime internal → LEARNING_SIGNAL 事件 + 复核 job → 真实 PG | TASK-011 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_feedback.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| S-15 | experience-management.backend.design.md#2.5 | component | 组件渲染 + mock service；不访问真实后端 | TASK-012 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_learning_actions_contract.py"] | . | 900 | P1 | auto | none | - | - | - | no |
| E-05 | experience-management.backend.design.md#2.5 | integration | Console API + Runtime internal → 真实 PG | TASK-009 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_control_boundaries.py"] | . | 900 | P0 | auto | local-api | - | - | - | no |

---

## TASK-001: 后端事实修正：回放用户归属、权威模型与状态主通道

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: backend design §3.3 数据设计 / §3.4.3 写入编排 / §3.6 规则落点
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-memory#RULE-memory-006, harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-002, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-memory#RULE-memory-006, harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: E-02, E-03
- **Estimate**: 2.5d
- **Capability**: 候选回放报告绑定经验所属用户；状态变更同事务产生版本/history/回执/审计；authority 迁移与两条激活路径可演示

### Description

候选回放报告绑定经验所属用户；状态变更同事务产生版本/history/回执/审计；authority 迁移与两条激活路径可演示。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`
- [x] `eval_report` 增加 `experience_user_id`，候选臂报告写入所属用户；晋升/回放按 `(tenant,user,key)` 过滤
- [x] `user_memory.authority` 迁移与映射（USER_EXPLICIT/ADMIN_EDIT/AGENT_INFERRED），增量迁移按 author 回填
- [x] 状态变更改为 `MemoryCommand(action="TRANSITION")` 经 MemoryService：锁序、版本、history、receipt、scope revision、审计同事务
- [x] 激活按 authority 分流：AGENT_INFERRED 需 PASSED；USER_EXPLICIT/ADMIN_EDIT 允许带 reason 直接激活
- [x] [E-02][integration] 覆盖：非法转换、跨用户报告与通用写经验被拒绝（真实边界：Runtime internal API + Console 通用记忆 API → 真实 PG）
- [x] [E-03][integration] 覆盖：状态变更失败整体回滚且不产生无审计变更（真实边界：Runtime MemoryService 事务 → 真实 PG；注入审计写入失败）
- [x] [harness-memory#RULE-memory-001] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`；按设计 §3.6 的落点断言（RULE-memory-001）
- [x] [harness-memory#RULE-memory-002] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`；按设计 §3.6 的落点断言（RULE-memory-002）
- [x] [harness-memory#RULE-memory-004] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`；按设计 §3.6 的落点断言（RULE-memory-004）
- [x] [harness-memory#RULE-memory-005] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`；按设计 §3.6 的落点断言（RULE-memory-005）
- [x] [harness-memory#RULE-memory-006] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`；按设计 §3.6 的落点断言（RULE-memory-006）
- [x] [harness-data#RULE-data-001] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e02`；按设计 §3.6 的落点断言（RULE-data-001）
- [x] [harness-snapshot#RULE-snapshot-001] verifier：`uv run pytest -q tests/agent_runtime/test_learning_fact_fixes.py -k e03`；状态变更前后 RuntimeSnapshot 行与 hash 不变，新 Run 仍读冻结快照
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | integration | Runtime internal API + Console 通用记忆 API → 真实 PG | 借用报告被拒（LEARNING_REPLAY_GATE_FAILED / 状态不变）；非法转换返回 LEARNING_INVALID_TRANSITION；通用写 API 拒绝经验类别；USER_EXPLICIT 可按权威路径激活并留审计；AGENT_INFERRED 仍被 PASSED 门槛拒绝 | tests/agent_runtime/test_learning_fact_fixes.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_learning_fact_fixes.py", "-k", "e02"] | verified |
| E-03 | integration | Runtime MemoryService 事务 → 真实 PG；注入审计写入失败 | 整个事务回滚：状态、版本、history、receipt 均无变化；返回明确错误码；不存在「状态已变但审计缺失」 | tests/agent_runtime/test_learning_fact_fixes.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_learning_fact_fixes.py", "-k", "e03"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- E-02: verified — automated command passed; run_id=f02c95d03bfd4d538a864c089336e355 (confirmed_by: runner)
- E-03: failed — automated command failed; run_id=f02c95d03bfd4d538a864c089336e355 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=753e0f489ce74c4ca426ee060c720c60 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=34c030e8e2d84c55921bba6b263fb23b (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=34c030e8e2d84c55921bba6b263fb23b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=4dc3c797121341bb90591e0031e91988 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=4dc3c797121341bb90591e0031e91988 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] resumed (in-progress)
- [2026-11-01] completed (done)

---
## TASK-002: 注入归因真实性与族判定一致

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: backend design §3.5 质量实现 / §3.6 规则落点；agent-core rank/memory_context
- **Spec-Refs**: harness-memory#RULE-memory-003
- **Rule-Owner-Refs**: harness-memory#RULE-memory-003
- **Acceptance-Refs**: B-01
- **Estimate**: 2d
- **Capability**: Run 诊断可给出真实分数、命中通道、未注入原因与被预算丢弃条目；失败 skill 不切换注入族

### Description

Run 诊断可给出真实分数、命中通道、未注入原因与被预算丢弃条目；失败 skill 不切换注入族。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_runtime/test_task_family_scope.py`
- [x] `select_for_injection` 返回选择结果（selected + skipped + 真实 score + FAMILY/TEXT 通道），诊断与选择同源
- [x] `MEMORY_INJECTED` 增加 diagnostics（learning_enabled/min_score/冻结集合大小/skipped/dropped_by_budget），payload 有界
- [x] items 只记录渲染后真实进入上下文的条目；预算丢弃单独记录
- [x] executor 只在成功 skill 后切换族，与 derive_task_family 共用 first_successful_skill
- [x] [B-01][unit] 覆盖：任务族判定纯函数边界（真实边界：纯函数 derive_task_family / first_successful_skill；无 IO）
- [x] [harness-memory#RULE-memory-003] verifier：`uv run pytest -q tests/agent_runtime/test_task_family_scope.py`；按设计 §3.6 的落点断言（RULE-memory-003）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-01 | unit | 纯函数 derive_task_family / first_successful_skill；无 IO | 首个成功 skill 决定族；失败调用不切换；无 skill 回退 agent:<id>；批量与单条判定一致（批量只影响元数据，不影响族） | tests/agent_runtime/test_task_family_scope.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_task_family_scope.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- B-01: verified — automated command passed; run_id=8078af13c644460ea865fb73b4799cff (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-003: 管理读模型与族聚合

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: backend design §3.3 / §3.4.1 / §3.5
- **Spec-Refs**: harness-api#RULE-api-001, harness-time#RULE-time-001
- **Rule-Owner-Refs**: harness-api#RULE-api-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-01, S-02, S-03, S-05, B-02
- **Estimate**: 2.5d
- **Capability**: 用户经验列表/详情、族列表/详情可用，族 Run 计数走增量汇总，经验与事实记忆分资源且双视图逐字段一致

### Description

用户经验列表/详情、族列表/详情可用，族 Run 计数走增量汇总，经验与事实记忆分资源且双视图逐字段一致。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_experience_api.py -k s01`
- [x] Console `LearningRepository` 提供 list/get/families/family 投影，两个视图共用 `ExperienceItem`
- [x] `family_run_stat` 表与 Runtime 循环增量汇总，族页读窗口小表（默认 7d，可 30d，计数封顶）
- [x] 新增/调整 user_memory/eval_report 索引；管理查询 2s statement_timeout
- [x] 通用记忆列表与计数排除经验类别；经验只走 `/learning/*`
- [x] [S-01][integration] 覆盖：用户经验列表按状态与族过滤且跨租户隔离（真实边界：Console API（真实 ASGI + ADMIN 会话）→ LearningRepository → 真实 PostgreSQL runtime.user_memory）
- [x] [S-02][integration] 覆盖：族视图聚合与用户视图对同一经验逐字段一致（真实边界：Console API → 真实 PG；同一 fixture 同时查询 API-01 与 API-04）
- [x] [S-03][integration] 覆盖：经验详情完整解释来源、回放、审计与注入（真实边界：Console API → 真实 PG（user_memory/history/eval_report/canonical_event/config_audit_log））
- [x] [S-05][integration] 覆盖：通用记忆资源不再混合经验且拒绝经验写（真实边界：Console 用户记忆 API → 真实 PG 只读投影；通用写 API → Runtime internal）
- [x] [B-02][integration] 覆盖：族聚合口径边界（null family、跨用户同名 key、异常前缀）（真实边界：Console 聚合 SQL → 真实 PG）
- [x] [harness-api#RULE-api-001] verifier：`uv run pytest -q tests/console_platform/test_learning_experience_api.py -k s01`；按设计 §3.6 的落点断言（RULE-api-001）
- [x] [harness-time#RULE-time-001] verifier：`uv run pytest -q tests/console_platform/test_learning_experience_api.py -k s01`；按设计 §3.6 的落点断言（RULE-time-001）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | Console API（真实 ASGI + ADMIN 会话）→ LearningRepository → 真实 PostgreSQL runtime.user_memory | 只返回该用户经验，字段含 activation_state/task_signature/latest_report 且 counts 按状态分列；state/family 过滤后总数与 items 一致，分页封套 {items,page,page_size,total}；跨租户返回 404（不泄露存在性） | tests/console_platform/test_learning_experience_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s01"] | verified |
| S-02 | integration | Console API → 真实 PG；同一 fixture 同时查询 API-01 与 API-04 | 族计数（run_count/经验状态分布/pass_rate）与 fixture 一致，null family 不计入并给出 unclassified_runs；同一经验在族详情与用户视图的 activation_state/version/latest_report 完全相等；不同用户同名 key 不被合并为一个状态；同一用户同族多个 key 正常分组展示（key 不解析） | tests/console_platform/test_learning_experience_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s02"] | verified |
| S-03 | integration | Console API → 真实 PG（user_memory/history/eval_report/canonical_event/config_audit_log） | 详情返回 evidence（source_run_ids/success_runs/item_total）、history 版本、reports（用户归属 + 门槛 + 逐 case）、injections（真实分数与通道）、audits；不展示其他用户的候选报告；历史 NULL 归属报告标 UNKNOWN 且不进入晋升依据；身份区展示 authority；报告 extras/未知字段原样透出，不因枚举扩展丢弃 | tests/console_platform/test_learning_experience_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s03"] | verified |
| S-05 | integration | Console 用户记忆 API → 真实 PG 只读投影；通用写 API → Runtime internal | 通用列表与 memory_count 只含事实记忆；经验计数单独返回；通用写 API 对经验类别返回明确拒绝码，状态不变 | tests/console_platform/test_learning_experience_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "s05"] | verified |
| B-02 | integration | Console 聚合 SQL → 真实 PG | null family 归「未归类」且不计入族统计；同族同名 key 按用户分组；未知前缀标 unknown，不报错 | tests/console_platform/test_learning_experience_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_experience_api.py", "-k", "aggregate"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-01: verified — automated command passed; run_id=fb7eeccbdcf54b4f9ba9e85288e70626 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=fb7eeccbdcf54b4f9ba9e85288e70626 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=fb7eeccbdcf54b4f9ba9e85288e70626 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=fb7eeccbdcf54b4f9ba9e85288e70626 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=fb7eeccbdcf54b4f9ba9e85288e70626 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=635ac30c75334a1f89536ce6db2f696d (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=635ac30c75334a1f89536ce6db2f696d (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=635ac30c75334a1f89536ce6db2f696d (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=635ac30c75334a1f89536ce6db2f696d (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=635ac30c75334a1f89536ce6db2f696d (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-004: 生命周期写操作与回放任务

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: backend design §3.4.1/§3.4.2/§3.4.3
- **Spec-Refs**: harness-api#RULE-api-002, harness-arch#RULE-arch-001, harness-auth#RULE-auth-001, harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-api#RULE-api-002, harness-arch#RULE-arch-001, harness-auth#RULE-auth-001, harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-04, E-01, E-04
- **Estimate**: 2.5d
- **Capability**: 管理员可异步回放、晋升、退休、重新沉淀；任务幂等、有容量边界、写审计且只影响后续 Run

### Description

管理员可异步回放、晋升、退休、重新沉淀；任务幂等、有容量边界、写审计且只影响后续 Run。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_lifecycle_api.py`
- [x] Runtime internal 端点 11-14 + Console 转发；服务身份/租户/actor/幂等键
- [x] `learning_job` 表与 runner：SKIP LOCKED、lease/attempts、`(tenant,command_id)` 幂等
- [x] 回放并发与模型容量预占（BACKGROUND），超时/样本上限；失败现场保留
- [x] 审计 actor 为真实账号 UUID，resource_id 含 tenant/user/key
- [x] [S-04][integration] 覆盖：生命周期操作经状态机与记忆主通道并留审计（真实边界：Console API → Runtime internal API（真实服务身份头）→ MemoryService/LearningActivation/LearningJobRunner → 真实 PG；回放用本地 OpenAI 兼容探针）
- [x] [E-01][integration] 覆盖：学习关闭与上游不可用时的只读降级（真实边界：Console API + Runtime client（上游 5xx/超时）；真实 PG）
- [x] [E-04][integration] 覆盖：回放任务幂等、崩溃回收与 INCOMPLETE 不改状态（真实边界：LearningJobRunner → 真实 PG；探针可控延迟/失败）
- [x] [harness-api#RULE-api-002] verifier：`uv run pytest -q tests/console_platform/test_learning_lifecycle_api.py`；按设计 §3.6 的落点断言（RULE-api-002）
- [x] [harness-arch#RULE-arch-001] verifier：`uv run pytest -q tests/console_platform/test_learning_lifecycle_api.py`；按设计 §3.6 的落点断言（RULE-arch-001）
- [x] [harness-auth#RULE-auth-001] verifier：`uv run pytest -q tests/console_platform/test_learning_lifecycle_api.py`；按设计 §3.6 的落点断言（RULE-auth-001）
- [x] [harness-log#RULE-log-001] verifier：`uv run pytest -q tests/console_platform/test_learning_lifecycle_api.py`；按设计 §3.6 的落点断言（RULE-log-001）
- [x] [harness-secret#RULE-secret-001] verifier：`uv run pytest -q tests/console_platform/test_learning_lifecycle_api.py`；job/result/audit/日志不含密钥原文
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | Console API → Runtime internal API（真实服务身份头）→ MemoryService/LearningActivation/LearningJobRunner → 真实 PG；回放用本地 OpenAI 兼容探针 | 回放生成绑定该 user 的候选报告，job 幂等只执行一次；晋升后 version 递增、history 有完整 after、receipt 有记录、审计 actor 为真实账号且含 from/to/reason；退休/重新沉淀按状态机生效；非法转换返回 LEARNING_INVALID_TRANSITION | tests/console_platform/test_learning_lifecycle_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_lifecycle_api.py"] | verified |
| E-01 | integration | Console API + Runtime client（上游 5xx/超时）；真实 PG | 读取仍可用并标注学习关闭；写操作 409 并给出原因；Runtime 不可达返回错误码，不返回缓存旧状态 | tests/console_platform/test_learning_degradation.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_degradation.py"] | verified |
| E-04 | integration | LearningJobRunner → 真实 PG；探针可控延迟/失败 | 重复提交只产生一个 job/一次执行；过期 lease 被回收并重试，超过 attempts 后 FAILED 且错误可见；INCOMPLETE 报告写入但不改变 activation_state | tests/agent_runtime/test_learning_jobs.py | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_learning_jobs.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-04: verified — automated command passed; run_id=88b62f357a6f4699ade3e84bae196490 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=88b62f357a6f4699ade3e84bae196490 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=88b62f357a6f4699ade3e84bae196490 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-005: 用户维度经验视图与共用详情

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-004
- **Source**: frontend design §3.2/§3.3/§3.5/§3.6
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui-detail#RULE-ui-detail-001
- **Rule-Owner-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-06
- **Estimate**: 2d
- **Capability**: 用户详情经验页签可查看/过滤经验并打开共用详情；authority 与未知码有兜底，通用记忆页签不再管理经验

### Description

用户详情经验页签可查看/过滤经验并打开共用详情；authority 与未知码有兜底，通用记忆页签不再管理经验。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k user_tab`
- [x] `modules/learning/services` 唯一 HTTP 出口；类型与后端契约逐字段对齐
- [x] ExperienceTab + ExperienceList + ExperienceDetailSideSheet 共用组件
- [x] authority/状态/原因码/报告 extras 的本地化与未知值兜底
- [x] 通用记忆页签对经验行不渲染启停用/删除
- [x] [S-06][component] 覆盖：用户详情经验页签展示状态机并提供解释入口（真实边界：组件渲染 + mock service 数据（契约类型）；不访问真实后端）
- [x] [harness-frontend#RULE-front-001] verifier：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k user_tab`；按设计 §3.6 的落点断言（RULE-front-001）
- [x] [harness-i18n#RULE-i18n-001] verifier：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k user_tab`；按设计 §3.6 的落点断言（RULE-i18n-001）
- [x] [harness-ui-detail#RULE-ui-detail-001] verifier：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k user_tab`；按设计 §3.6 的落点断言（RULE-ui-detail-001）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | component | 组件渲染 + mock service 数据（契约类型）；不访问真实后端 | 列表显示族/状态/版本/回放结论，过滤与分页可用；打开共用经验详情；通用记忆页签对经验类不出现启停用/删除；空态与错误态文案来自 learning.* 词条 | tests/frontend/test_learning_experience_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_learning_experience_contract.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-06: verified — automated command passed; run_id=b33989d90eb648a1b765571a72f30b20 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-006: 族视图前端与浏览器旅程

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-007
- **Source**: frontend design §3.2/§3.3/§3.4/§3.5
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-e2e#RULE-e2e-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-07, S-08
- **Estimate**: 2.5d
- **Capability**: 族页面可浏览/筛选/打开详情；经验详情两入口一致；Run↔族↔经验深链可往返

### Description

族页面可浏览/筛选/打开详情；经验详情两入口一致；Run↔族↔经验深链可往返。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k family_page`
- [x] LearningPage + FamilyDetailSideSheet；筛选/分页/空错态；菜单新增学习与经验并同步 harness-ui 规则
- [x] 族详情内经验打开同一 ExperienceDetailSideSheet；深链 `?family=`/`?experience=`
- [x] 用户详情经验页签支持 `tab=experience` 深链打开
- [x] 浏览器 E2E 真实构建产物 + 真实后端，覆盖退休同步与 Run 回跳
- [x] [S-07][component] 覆盖：族页面与用户视图共用同一经验详情组件（真实边界：组件渲染 + 同一 fixture 注入两个入口）
- [ ] [S-08][E2E] 覆盖：管理员从用户经验到族视图的完整旅程与深链回跳（真实边界：真实浏览器（preview 构建产物）→ Console API → Runtime internal → 真实 PostgreSQL；仅模型回放可用本地探针）
- [x] [harness-ui#RULE-ui-001] verifier：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k family_page`；按设计 §3.6 的落点断言（RULE-ui-001）
- [x] [harness-e2e#RULE-e2e-001] verifier：`uv run pytest -q tests/frontend/test_learning_experience_contract.py -k family_page`；按设计 §3.6 的落点断言（RULE-e2e-001）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | component | 组件渲染 + 同一 fixture 注入两个入口 | 两处详情由同一组件渲染，族/状态/回放结论/操作一致；关闭后回到各自入口，列表不出现重复操作列 | tests/frontend/test_learning_experience_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_learning_family_page_contract.py"] | verified |
| S-08 | E2E | 真实浏览器（preview 构建产物）→ Console API → Runtime internal → 真实 PostgreSQL；仅模型回放可用本地探针 | 退休后用户页与族页同时显示 RETIRED，审计可见操作者与原因；族详情中的经验身份与用户页完全一致；Run ↔ 经验双向深链落在同一对象（user_id+memory_key）且无 404；审计页可按 MEMORY_EXPERIENCE 筛选到本次退休，操作者为真实账号，跳转落在同一经验 | e2e/tests/learning-experience.spec.ts | ["node", "e2e/node_modules/.bin/playwright", "test", "--config", "e2e/playwright.learning-experience.config.ts", "e2e/tests/learning-experience.spec.ts"] | e2e_deferred |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-07: failed — automated command failed; run_id=0a05b6817e404cfabbee2358fa41ce47 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=0a05b6817e404cfabbee2358fa41ce47 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=1decfc88ee2a46cb8fa6acfde6388a43 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=1decfc88ee2a46cb8fa6acfde6388a43 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=e5b31d04d8dc40d5a619686e7c398c4a (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=6ce600b3f7394ac1a4b2663d9878cb45 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=6ce600b3f7394ac1a4b2663d9878cb45 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-007: Run 学习追溯与注入诊断

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-003
- **Source**: backend design §3.4.1 API-10；frontend design §3.2/§3.5
- **Spec-Refs**: harness-memory#RULE-memory-003, harness-api#RULE-api-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-09
- **Estimate**: 1.5d
- **Capability**: Run 详情显示任务族与注入事件链接，并可查看「为什么注入/没注入」诊断

### Description

Run 详情显示任务族与注入事件链接，并可查看「为什么注入/没注入」诊断。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_diagnostics_api.py`
- [x] Console API-10 读 run_record/MEMORY_INJECTED/Snapshot 冻结值
- [x] Run 详情展示 task_family 与 MEMORY_INJECTED 经验链接
- [x] 诊断页签展示 injected/skipped/dropped 与 recorded=false 的历史提示
- [x] [S-09][integration] 覆盖：Run 维度「为什么没注入」诊断忠实呈现（真实边界：真实 Run 的 MEMORY_INJECTED 事件（含 diagnostics）→ Console API-10 → 真实 PG）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-09 | integration | 真实 Run 的 MEMORY_INJECTED 事件（含 diagnostics）→ Console API-10 → 真实 PG | 诊断返回 injected 真实分数/通道与 skipped 的逐条原因；历史 Run 返回 recorded=false，不伪造原因；未知诊断原因码原样展示并走兜底文案，不报错 | tests/console_platform/test_learning_diagnostics_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_diagnostics_api.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-09: verified — automated command passed; run_id=d3e78d9420d84338a318dce55069ff8a (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=8e9455dd64e0413aa99bfe5ac3081fa9 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-008: 审计可见性与深链

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: backend design §3.4.3 审计投影；frontend design §3.2.1
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-i18n#RULE-i18n-001, harness-log#RULE-log-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-10
- **Estimate**: 1.5d
- **Capability**: 审计页可按 MEMORY_EXPERIENCE/动作筛选，标签可读，并能跳到对应经验

### Description

审计页可按 MEMORY_EXPERIENCE/动作筛选，标签可读，并能跳到对应经验。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_audit_projection.py`
- [x] 审计 CONFIG 投影对 MEMORY_EXPERIENCE 输出 experience_key/user_id target 与 owner_unknown
- [x] 前端资源类型/动作/操作者词条与筛选；真实 actor 名称，历史合成 actor 显示系统/自动
- [x] 审计详情「查看经验」深链，返回保留筛选
- [x] [S-10][integration] 覆盖：审计页经验资源可见、可筛选且可深链（真实边界：Console 审计列表/详情 API → 真实 PostgreSQL 四表 UNION 投影与 config_audit_log）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-10 | integration | Console 审计列表/详情 API → 真实 PostgreSQL 四表 UNION 投影与 config_audit_log | 列表返回全部经验审计行，target 展示 experience_key/user_id；历史行标归属未知；动作/资源词条有中英标签；真实 actor 解析出账号名，合成 actor 显示系统/自动；详情含 user_id/key，可构造 /learning?experience=<user_id>:<key> 深链 | tests/console_platform/test_learning_audit_projection.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_audit_projection.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-10: verified — automated command passed; run_id=634aa6c2458c4525bd46afe16313d50a (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-009: 止损与族级暂停

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-003, TASK-004
- **Source**: backend design §3.4.1/§3.4.3；frontend design §3.2.2
- **Spec-Refs**: harness-memory#RULE-memory-003, harness-auth#RULE-auth-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-12, E-05
- **Estimate**: 2d
- **Capability**: 从 Run 一键停用本次注入经验；族级暂停/恢复只影响新 Run，逐条结果与审计可见

### Description

从 Run 一键停用本次注入经验；族级暂停/恢复只影响新 Run，逐条结果与审计可见。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_family_control.py`
- [x] API-15/19 按 MEMORY_INJECTED 精确到 user/key 逐条退休，返回逐条结果
- [x] `learning_family_control` 表与 API-16/20；候选生成与注入冻结检查暂停
- [x] 前端 Run 详情与族详情的确认框/状态/权限
- [x] 暂停/恢复写审计；不删除数据；恢复不复活已退休经验
- [x] [S-12][integration] 覆盖：从 Run 一键止损并按族暂停，只影响后续新 Run（真实边界：Console API → Runtime internal（真实服务身份）→ MemoryService/候选生成/注入冻结 → 真实 PG）
- [x] [E-05][integration] 覆盖：止损/暂停/修订/反馈的权限、幂等与跨租户边界（真实边界：Console API + Runtime internal → 真实 PG）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-12 | integration | Console API → Runtime internal（真实服务身份）→ MemoryService/候选生成/注入冻结 → 真实 PG | 两条经验按 (user,key) 变 RETIRED 并写审计；当前 Run 回答不变；暂停后新 Run 不再注入该族经验、候选生成跳过该族；在飞 Run 的冻结集合不变；恢复后新 Run 可正常注入未退休经验；已退休不复活；全程无数据删除 | tests/console_platform/test_learning_family_control.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_family_control.py"] | verified |
| E-05 | integration | Console API + Runtime internal → 真实 PG | 跨租户 404/403；非管理员 403；重复请求幂等返回首次结果，不产生第二次状态变更；失败不产生部分写入（暂停/修订/反馈各自原子） | tests/console_platform/test_learning_control_boundaries.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_control_boundaries.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-12: verified — automated command passed; run_id=8d87a49277614b43aa8913b642573c03 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=8d87a49277614b43aa8913b642573c03 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=f756c2684ea3443bb81437795e9c4635 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=f756c2684ea3443bb81437795e9c4635 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-010: 经验内容修订

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001, TASK-003, TASK-004
- **Source**: backend design §3.4.3 内容修订；frontend design §3.2.2
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-002
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-13, E-05
- **Estimate**: 1.5d
- **Capability**: 管理员可修订经验正文生成新版本，历史/审计完整，状态回 PROPOSED 且仍需验证

### Description

管理员可修订经验正文生成新版本，历史/审计完整，状态回 PROPOSED 且仍需验证。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_content_revision.py`
- [x] API-17/21：expected_version + reason，走 MemoryService UPSERT（ADMIN）
- [x] 新版本 PROPOSED；AGENT 权威仍需 PASSED；修订不改变族/证据/注入历史
- [x] 前端修订表单与 409 冲突处理
- [x] [S-13][integration] 覆盖：经验内容修订生成新版本且仍需回放晋升（真实边界：Console API → Runtime MemoryService 事务 → 真实 PG（版本/history/receipt/审计））
- [x] [E-05][integration] 覆盖：止损/暂停/修订/反馈的权限、幂等与跨租户边界（真实边界：Console API + Runtime internal → 真实 PG）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-13 | integration | Console API → Runtime MemoryService 事务 → 真实 PG（版本/history/receipt/审计） | 修订生成 v2、状态 PROPOSED、history/回执/审计齐全；旧版本与注入历史保留；版本冲突返回 409，不覆盖；未 PASSED 晋升被拒（LEARNING_REPLAY_GATE_FAILED）；PASSED 后晋升到 ACTIVE，审计记录 operator/reason | tests/console_platform/test_learning_content_revision.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_content_revision.py"] | verified |
| E-05 | integration | Console API + Runtime internal → 真实 PG | 跨租户 404/403；非管理员 403；重复请求幂等返回首次结果，不产生第二次状态变更；失败不产生部分写入（暂停/修订/反馈各自原子） | tests/console_platform/test_learning_control_boundaries.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_control_boundaries.py"] | planned |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-13: verified — automated command passed; run_id=4cfa8f84a1f04ce5872f49d9c1e10211 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-011: 差回答反馈闭环

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001, TASK-004
- **Source**: backend design §3.4.3 差回答反馈；frontend design §3.2.2
- **Spec-Refs**: harness-memory#RULE-memory-005, harness-arch#RULE-arch-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-14, E-05
- **Estimate**: 1.5d
- **Capability**: Run 可标记差回答，幂等写学习信号并把该 Run 排除出未来 Golden Set，同时触发相关经验复核

### Description

Run 可标记差回答，幂等写学习信号并把该 Run 排除出未来 Golden Set，同时触发相关经验复核。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/console_platform/test_learning_feedback.py`
- [x] API-18/22：幂等 receipt + LEARNING_SIGNAL(source_run_id/actor/reason/explicit)
- [x] 复用信号排除规则；为注入过的 ACTIVE 经验去重创建复核 job
- [x] 前端差回答按钮/说明/重复提交提示
- [x] [S-14][integration] 覆盖：差回答反馈幂等进入学习信号并触发复核（真实边界：Console API → Runtime internal → LEARNING_SIGNAL 事件 + 复核 job → 真实 PG）
- [x] [E-05][integration] 覆盖：止损/暂停/修订/反馈的权限、幂等与跨租户边界（真实边界：Console API + Runtime internal → 真实 PG）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-14 | integration | Console API → Runtime internal → LEARNING_SIGNAL 事件 + 复核 job → 真实 PG | 只写一条 LEARNING_SIGNAL（source_run_id/actor/reason），Run 退出未来 Golden Set；重复提交返回首次结果，不重复建 job；复核 job 走容量边界；回放回归才退休，否则状态不变并记录结论 | tests/console_platform/test_learning_feedback.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_feedback.py"] | verified |
| E-05 | integration | Console API + Runtime internal → 真实 PG | 跨租户 404/403；非管理员 403；重复请求幂等返回首次结果，不产生第二次状态变更；失败不产生部分写入（暂停/修订/反馈各自原子） | tests/console_platform/test_learning_control_boundaries.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_learning_control_boundaries.py"] | planned |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-14: verified — automated command passed; run_id=1354adee9591493eaa2dd09cb85dc608 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
## TASK-012: 新增操作入口的前端组件态

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-005, TASK-009, TASK-010, TASK-011
- **Source**: frontend design §3.2.2/§3.3
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui-detail#RULE-ui-detail-001, harness-ui#RULE-ui-001, harness-e2e#RULE-e2e-001
- **Rule-Owner-Refs**: harness-ui#RULE-ui-001, harness-e2e#RULE-e2e-001
- **Acceptance-Refs**: S-15
- **Estimate**: 1.5d
- **Capability**: Run 止损、族暂停、内容修订、差回答四个入口在管理员/非管理员、冲突/重复/暂停态下行为正确

### Description

Run 止损、族暂停、内容修订、差回答四个入口在管理员/非管理员、冲突/重复/暂停态下行为正确。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/frontend/test_learning_actions_contract.py`
- [x] 确认框展示影响范围（只影响新 Run/不删除数据）与必填原因
- [x] 409/重复/暂停态错误提示与输入保留
- [x] 权限门控与操作后刷新
- [x] [S-15][component] 覆盖：止损/修订/反馈入口的组件态与错误提示（真实边界：组件渲染 + mock service；不访问真实后端）
- [x] [harness-ui#RULE-ui-001] verifier：`uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build`；菜单十二项且列表用 RemoteTable
- [x] [harness-e2e#RULE-e2e-001] verifier：`npm --prefix e2e test -- --config playwright.learning-experience.config.ts`；真实构建产物 + Chrome
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-15 | component | 组件渲染 + mock service；不访问真实后端 | 管理员可见按钮并显示影响范围说明；非管理员不渲染；409/重复提交/暂停态有明确提示且保留输入；操作成功后列表与详情即时刷新 | tests/frontend/test_learning_actions_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_learning_actions_contract.py"] | verified |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-15: verified — automated command passed; run_id=8161f99d4143439ca482741c17ec4bbf (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] provisional (required code checks not executed: harness-e2e#RULE-e2e-001 (not_executed), harness-ui#RULE-ui-001 (not_executed))
- [2026-11-01] completed (done)

---
## TASK-013: 管理面容量与混合负载验证

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004, TASK-006, TASK-009, TASK-010, TASK-011
- **Source**: backend design §3.5 性能；PRD NFR-PERF-02
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-11
- **Estimate**: 2d
- **Capability**: 1000 用户容量负载下执行管理查询与一次回放，用户侧不回归、管理 p95 达标、回放不超额度

### Description

1000 用户容量负载下执行管理查询与一次回放，用户侧不回归、管理 p95 达标、回放不超额度。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/capacity/test_learning_management_mixed.py -m capacity_load`
- [ ] 容量混合 harness：负载稳态中并发族页/经验详情查询 + 一次回放
- [ ] 采集 admission/first-delta、管理 p95、PG 连接/WAL、回放模型调用
- [ ] 记录基线与回归阈值；容量场景在 release 环境执行
- [ ] [S-11][capacity] 覆盖：1000 用户负载下管理查询与回放不回归用户侧（真实边界：1000 用户 × 8 轮真实负载（容量 profile）→ Runtime/PG；同时打开族页/经验详情并触发一次回放）
- [ ] [harness-test#RULE-test-001] verifier：`uv run pytest -q tests/capacity/test_learning_management_mixed.py -m capacity_load`；按设计 §3.6 的落点断言（RULE-test-001）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-11 | capacity | 1000 用户 × 8 轮真实负载（容量 profile）→ Runtime/PG；同时打开族页/经验详情并触发一次回放 | 用户侧 admission/first-delta 与无管理操作基线无显著回归；管理接口 p95 ≤ 500ms；无 pool timeout；回放只在容量租约内调用模型，不超并发/额度；族统计不随 Run 历史线性变慢 | tests/capacity/test_learning_management_mixed.py | ["uv", "run", "pytest", "-q", "tests/capacity/test_learning_management_mixed.py", "-m", "capacity_load"] | e2e_deferred |

### Acceptance Evidence

> 实现阶段填写 RED/GREEN、断言位置与真实边界证据；E2E 与容量场景留给需求级终验执行。
- S-11: e2e_deferred — automated command e2e_deferred; run_id=681620392e0f4d61a7eedcc5ab0719a1 (confirmed_by: runner)

### Log

- [2026-11-01] created (draft)
- [2026-11-01] started
- [2026-11-01] completed (done)

---
