# Tasks: 后台任务、定时任务与可靠投递

- **Source**: .code-flow/tasks/2026-09-17/09-task-schedule/（合并前后端 design）
- **Created**: 2026-09-20
- **Updated**: 2026-09-21
- **Plan-State**: plan-bound（Design Gate pass / Plan Gate pass，2026-09-21；N-01..N-06 已闭合，见 Design Corrections）

## Proposal

补齐已有 Worker 骨架的可靠执行、定时触发、批量聚合和最终投递，并将 Console 后台任务/定时任务占位页替换为真实查询及管理页面。PostgreSQL 保存唯一权威状态，Runtime 经内部 API 提交，Console 经 Worker Admin API 管理；沿用已冻结 Snapshot、四部署单元和现有公共基础设施。

本计划以修复和扩展现有实现为基线，已有行为先补回归证据。共 44 项：P0 43、P1 1；依赖深度 17 层；每项列出 1–3 个预计改动文件（含测试），目标 15–60 分钟。实际超出范围时先拆分任务，不在编码中静默扩大作用域。

## Design Corrections

2026-09-21 已确认并同步两份 design；原 N-01..N-06 全部闭合，Design Gate 可重检。

- **N-01 / Spec 漂移（已闭合）**：两份 Matrix 的 `harness-platform#` 前缀改为当前 Context 真实 spec_id（`harness-arch`/`harness-api`/`harness-data`/`harness-secret`/`harness-snapshot`/`harness-worker`/`harness-skill`/`harness-im`/`harness-test`/`harness-i18n`/`harness-ui`/`harness-ui-detail`/`harness-time`/`harness-frontend`）。新增 `harness-api#RULE-api-002` 覆盖：backend §3.3.8 `task.task_submission` + API-01/API-02 `Idempotency-Key` Header 契约，body `idempotency_key` 保持兼容且两者同时出现必须一致，Schedule 同样支持 Header 重放；指纹与表结构对齐既有 `runtime.run_submission`，独立增量 migration 不改写 0002。Worker 消费端不可变 Artifact/cache/清理回归已补入 backend Matrix `RULE-skill-001` 行。
- **N-02 / 旧密钥设计（已闭合）**：已改按当前 required `harness-secret#RULE-secret-001`——密钥明文只存各 Owner 表并以主键引用，不再有 SecretRef/SecretProvider；本模块 task schema 各表、Snapshot、事件、日志、审计、LLM、IM 与公开 API 均不携带密钥，对外只回 `*_configured`。落点 backend §2.3.2 / §3.3.1 表说明 / §3.5 安全 / RULE-04 / Matrix。未创建新的 SecretProvider。
- **N-03 / Worker verifier（已闭合，无需改动）**：实测 `harness-worker` 的 verifier 已是 `type: command`（`uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py`，timeout 300），并非 manual/project-owner，原记录前提已失效。本模块不修改平台共享 Spec 的 verifier。
- **N-04 / ONCE misfire（已闭合）**：新增 Schedule 终态 `MISSED`——ONCE 错过触发时 `status=MISSED`、`completed_at=NULL`、`next_fire_at=NULL`，记 SKIP 审计与原因，不补发、不可恢复（需重新创建）。未沿用 `PAUSED`：resume 契约定义为「CAS 后按当前时间重算 next_fire_at」，ONCE 无 cron 可重算，会产生恢复语义歧义。落点 backend RULE-11 / §3.2.3 / §3.3.1 / §3.3.5 / API-07 / API-14 / API-15 / E-04，frontend FEAT-FE-03 / §3.2 / §3.3.1 / S-FE-04。
- **N-05 / 截止时间过滤（已闭合）**：新增独立 `deadline_from`/`deadline_to` 作用于 `deadline_at`，保留 `start_time`/`end_time` 继续作用于 `create_time`；UTC 比较、起止含端点。落点 backend API-03 / API-09，frontend §3.5。
- **N-06 / 投递故障边界（已闭合）**：明确 `SET NX` 成功仅为「占位」而非「已送达」，发送失败或占位后崩溃必须允许重试且不得置 SENT；Redis 正常时重放保证不重复发送，Redis 不可用或外部发送结果不确定时只承诺 at-least-once（可能重复），不宣称外部渠道 exactly-once。E-05 补并发/发送失败/重启/Redis 故障断言。落点 backend RULE-13 / §3.1 / §3.2.5 / §3.3.6 / RISK-02。
- **接口完整性（已闭合）**：API-17 清单已补 `GET`/`DELETE /internal/admin/schedules/{schedule_id}`，承接既有 API-13/API-16；`task_event.task_id` 已按当前数据 Rule 声明同 schema 物理 FK；Header 幂等以独立 `task.task_submission` 增量 migration 承载，不改写 0002。
- **新增验证覆盖（已闭合）**：新增设计场景 E-07（提交幂等，owner TASK-006）；原 17 个 S/E 场景不删除、不降级，现为 18 个。本计划 B-101..B-215 的补充边界并入 design 验证说明；API 幂等 E2E 另映射到 B-141，前端硬编码/重复公共组件/N+1 风险映射到 B-130/B-131/S-FE-01/S-FE-03 与相关 Spec Rule。

## Baseline and External Dependencies

- 已核实 Worker 有模型、claimer、service、scheduler、delivery 与测试；executor 仍是 SkeletonTaskExecutor；Schedule GET 返回裸数组；Task 取消返回非设计枚举 CANCELLING；Gateway 存在 exists→send→mark 竞态；前端 /tasks、/schedules 为占位页。
- 复用模块 01/05/07/08 的公共底座、Artifact Store、Effective Capability、Skill 执行/凭据读取，不复制 Run/Conversation/授权公式。模块 08 正在推进：TASK-012/TASK-023/TASK-041 依赖其真实执行/Tool 注入契约可用；依赖未达成时记录外部阻塞，禁止用 placeholder/mock 代替 E2E。
- Gateway 实现按当前仓库 Python 路径复用；本模块只承担最终投递接口/去重所需改动，不扩展 IM 接入范围。
- 测试文件中的场景名包含对应 ID；Python ID 在函数名用小写加下划线，例如 test_s01_*；Playwright title 使用原 ID。所有命令从仓库根目录执行，下面文件与用例均为 planned，未声称测试已存在或通过。
- 浏览器使用真实 build 和 Chrome、生产业务路由；真实 PostgreSQL/Redis/HTTP/NFS 不得 mock。外部渠道采用本地真实 HTTP 探针；第三方实网不是本次自动验收的证据。E2E 缺依赖不能以 skip 充当通过。Redis hint 丢失与实际断线需可控故障注入，不能只返回固定结果。
- 所有新增/修复行为修改生产代码前先写用例并记录 RED；纯已有行为先跑回归，有明确缺口才修改。函数≤50行，强类型、显式异常；外部调用使用有界超时/退避，不放在无等待紧循环。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | Checklist |
|---|---|---|---|---|---|
| TASK-001 | P0 | 补齐 Task Schema 约束与迁移一致性 | 无 | B:3.3 数据设计；B:4. 部署与运维 | 5 |
| TASK-002 | P0 | 新增提交幂等记录的持久化模型 | 001 | B:3.3 数据设计；B:3.4 接口设计 | 4 |
| TASK-003 | P0 | 收紧 Task/Schedule 请求与响应契约 | 无 | B:3.3.5 状态枚举；B:3.4 接口设计 | 4 |
| TASK-004 | P0 | 使 TaskEvent 序号分配并发安全 | 001 | B:3.3.4 `task.task_event`；B:3.5 质量实现方案 | 4 |
| TASK-005 | P0 | 修正投递路由归一化和租户隔离 | 001, 003 | B:3.3.2 `task.delivery_route` | 4 |
| TASK-006 | P0 | 实现提交指纹校验与首次响应重放 | 002, 003 | B:3.4 接口设计；B:API-01 Internal 创建 Task；B:API-02 Internal 创建 Schedule | 4 |
| TASK-007 | P0 | 完善 Task 创建、快照校验与原子事件 | 004, 005, 006 | B:API-01 Internal 创建 Task；B:3.3.3 `task.task_execution` | 4 |
| TASK-008 | P0 | 补齐任务列表、详情与 Timeline 查询 | 003, 004 | B:API-03 Internal Task 列表；B:API-04 Internal Task 详情 | 4 |
| TASK-009 | P0 | 完善 Schedule 创建、更新与时区计算 | 003, 005, 006 | B:API-02 Internal 创建 Schedule；B:API-07 Internal 更新 Schedule | 4 |
| TASK-010 | P0 | 补齐 Schedule 分页、详情与管理状态转换 | 009 | B:API-06 Internal Schedule 列表；B:API-08 Internal 删除 Schedule；B:API-14 暂停 Schedule（Console）；B:API-15 恢复 Schedule（Console） | 4 |
| TASK-011 | P0 | 加固 claim、heartbeat 和 reclaim 租约 | 004, 007, 012 | B:3.2.1 执行主流程；B:3.2.2 Task 状态机 | 5 |
| TASK-012 | P0 | 替换占位执行器并接入冻结 Artifact | 003, 007 | B:3.3.3 `task.task_execution`；B:3.2.6 跨模块引用边界 | 4 |
| TASK-013 | P0 | 实现 WAITING、重试及受保护的完成状态 | 011, 012 | B:3.2.1 执行主流程；B:3.2.2 Task 状态机 | 4 |
| TASK-014 | P0 | 补齐协作取消与取消竞态 | 008, 013 | B:API-05 Internal 取消 Task；B:3.2.2 Task 状态机 | 5 |
| TASK-015 | P0 | 使 Schedule 触发原子化并冻结当前有效定义 | 007, 009, 011 | B:3.2.3 Schedule 触发、多副本与 Misfire；B:3.2.6 跨模块引用边界 | 5 |
| TASK-016 | P0 | 落实 Misfire SKIP、ONCE 和调度审计 | 015 | B:3.2.3 Schedule 触发、多副本与 Misfire；B:3.5 质量实现方案 | 5 |
| TASK-017 | P0 | 把 deadline sweep 接入独立 Scheduler 节拍 | 013, 020 | B:3.2.2 Task 状态机；B:3.5 质量实现方案 | 5 |
| TASK-018 | P0 | 实现 BATCH 幂等 fan-out 与并发上限 | 004, 007, 013 | B:3.2.4 Fan-out / Fan-in | 4 |
| TASK-019 | P0 | 实现原子 fan-in 与 Parent 终态 | 018, 014 | B:3.2.4 Fan-out / Fan-in | 4 |
| TASK-020 | P0 | 加固持久 Final Delivery 抢占与重试 | 005, 013 | B:3.2.5 Final Delivery；B:3.3.3 `task.task_execution` | 4 |
| TASK-021 | P0 | 修正 Gateway 并发投递去重和失败恢复 | 020 | B:3.2.5 Final Delivery；B:3.3.6 Redis Key 与边界 | 5 |
| TASK-022 | P0 | 装配 Worker/Scheduler/Delivery 生命周期 | 014, 016, 017, 019, 021 | B:3.1 技术选型与关键决策；B:4. 部署与运维 | 4 |
| TASK-023 | P0 | 接通 Runtime 后台任务与 Schedule Tool 客户端 | 007, 008, 009, 010, 014 | B:API-01 Internal 创建 Task；B:API-02 Internal 创建 Schedule；B:3.2.6 跨模块引用边界 | 4 |
| TASK-024 | P0 | 补 Task Admin 内部路由与权限域 | 008, 014 | B:API-17 Worker Admin API | 4 |
| TASK-025 | P0 | 补 Schedule Admin 详情、启停与删除路由 | 010 | B:API-17 Worker Admin API；B:API-13 Schedule 详情（Console）；B:API-16 删除 Schedule（Console） | 4 |
| TASK-026 | P0 | 实现 Console 到 Worker 的有界 HTTP 客户端 | 024, 025 | B:3.4 接口设计 | 4 |
| TASK-027 | P0 | 接入 Console Task 列表、详情与取消 API | 026 | B:API-09 任务列表（Console）；B:API-10 任务详情（Console）；B:API-11 取消任务（Console） | 4 |
| TASK-028 | P0 | 接入 Console Schedule 查询和管理 API | 026 | B:API-12 Schedule 列表（Console）；B:API-13 Schedule 详情（Console）；B:API-14 暂停 Schedule（Console）；B:API-15 恢复 Schedule（Console）；B:API-16 删除 Schedule（Console） | 4 |
| TASK-029 | P0 | 建立前端 Task/Schedule services 与强类型 DTO | 003, 027, 028 | F:3.5 状态与数据流 | 4 |
| TASK-030 | P0 | 补 Task/Schedule 中英文文案和帮助词条 | 无 | F:3.1 技术选型；F:3.3.1 每个按钮/操作的设计；F:3.7 样式方案 | 4 |
| TASK-031 | P0 | 实现后台任务列表、筛选和路由 | 029, 030, 040 | F:3.2 页面与路由结构；F:3.3 组件设计；F:3.7 样式方案 | 4 |
| TASK-032 | P0 | 实现 Task 详情、错误态与子任务展示 | 031 | F:3.3 组件设计；F:3.4 组件接口契约；F:3.6 UI 状态 | 7 |
| TASK-033 | P1 | 实现 TaskTimeline 与有界详情刷新 | 032 | F:3.3 组件设计；F:3.5 状态与数据流 | 4 |
| TASK-034 | P0 | 实现取消确认及终态刷新 | 032, 033, 014 | F:3.3.1 每个按钮/操作的设计；F:2.4 验收条件 | 5 |
| TASK-035 | P0 | 实现定时任务列表、完成筛选和帮助 | 029, 030, 031, 040 | F:3.2 页面与路由结构；F:3.3 组件设计；F:3.3.1 每个按钮/操作的设计 | 5 |
| TASK-036 | P0 | 实现 Schedule 详情基本信息与操作位置 | 035 | F:3.3 组件设计；F:3.4 组件接口契约；F:3.7 样式方案 | 4 |
| TASK-037 | P0 | 实现 Schedule 历史与 Task 详情跳转 | 036, 032 | F:3.3 组件设计；F:3.5 状态与数据流 | 6 |
| TASK-038 | P0 | 实现 Schedule 暂停、恢复与删除 | 036, 010 | F:3.3.1 每个按钮/操作的设计；F:3.6 UI 状态 | 5 |
| TASK-039 | P0 | 建立后端真实验收环境与数据清理 | 无 | B:2.5.2 功能验收场景；B:3.5 质量实现方案 | 4 |
| TASK-040 | P0 | 建立任务管理真实浏览器测试栈 | 027, 028, 039 | F:2.4 验收条件；F:3.1 技术选型 | 4 |
| TASK-041 | P0 | 验收 Runtime→Worker 执行及恢复全链路 | 022, 023, 039 | B:2.5.2 功能验收场景；B:Spec Compliance Matrix | 11 |
| TASK-042 | P0 | 验收调度授权、Snapshot 与多副本竞态 | 016, 022, 039 | B:2.5.2 功能验收场景；B:3.2.3 Schedule 触发、多副本与 Misfire；B:Spec Compliance Matrix | 6 |
| TASK-043 | P0 | 验收批量聚合和最终投递 | 019, 021, 022, 039 | B:2.5.2 功能验收场景；B:3.2.4 Fan-out / Fan-in；B:3.2.5 Final Delivery；B:Spec Compliance Matrix | 7 |
| TASK-044 | P0 | 完成跨语言、时间与全部验收收口 | 001, 002, 003, 004, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 018, 019, 020, 021, 022, 023, 024, 025, 026, 027, 028, 029, 030, 031, 032, 033, 034, 035, 036, 037, 038, 039, 040, 041, 042, 043 | B:Spec Compliance Matrix；F:Spec Compliance Matrix；F:2.4 验收条件 | 9 |

## Acceptance Coverage

> 原设计 17/17 场景（9 E2E、8 integration）均有唯一最终负责人；本轮新增设计场景 E-07（提交幂等，integration），合计 18/18；新增 44 个局部/补充边界；15/15 required Rule 均有唯一负责人。表中 planned 是计划状态，不代表 Design/Plan Gate 已通过。B-101..B-215 是计划补充 ID，未冒充原设计已有 ID。

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | manual_reason |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | Runtime→Worker API→PG→Worker | TASK-041 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_execution.py","-k","s01"] | . | 1200 | |
| S-02 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | Scheduler→真实 current grants/Binding/artifact→Task DB | TASK-042 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_schedules.py","-k","s02"] | . | 1200 | |
| S-03 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | Worker→真实 PG→IM Gateway HTTP/Redis→渠道探针 | TASK-043 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_delivery.py","-k","s03"] | . | 1200 | |
| S-04 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | Worker→真实 Parent/Child PG→IM Gateway | TASK-043 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_batch.py","-k","s04"] | . | 1200 | |
| E-01 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | 双 Worker→真实 PG lease/CAS→真实幂等 Skill 副作用 | TASK-011 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_worker_leases.py","-k","e01"] | . | 600 | |
| E-02 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | Scheduler→真实 Console resolve→PG grants/Binding | TASK-015 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_schedule_trigger.py","-k","e02"] | . | 600 | |
| E-03 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | 独立 Scheduler→真实 PG→IM Gateway HTTP/Redis | TASK-017 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_deadline.py","-k","e03"] | . | 600 | |
| E-04 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | Scheduler→真实 PG→审计/指标/终态 MISSED | TASK-016 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_scheduler_misfire.py","-k","e04"] | . | 600 | |
| E-05 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | Worker HTTP→真实 Gateway→Redis→渠道探针 | TASK-021 | planned | ["uv","run","pytest","-q","tests/gateway/test_delivery_api.py","-k","e05"] | . | 600 | |
| E-06 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | 取消 API→真实 PG CAS→Worker 检查点 | TASK-014 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_cancel.py","-k","e06"] | . | 600 | |
| E-07 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | Worker HTTP→task.task_submission partial unique | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_submission_idempotency.py","-k","e07"] | . | 600 | |
| S-201 | 09-task-schedule.frontend.design.md#2.4 验收条件 | E2E | Browser→schedules API→tasks API→真实 PG | TASK-037 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts --grep 'S-FE-01'"] | . | 1200 | |
| S-202 | 09-task-schedule.frontend.design.md#2.4 验收条件 | E2E | Browser→Console cancel API→真实 Worker/PG→UI | TASK-034 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-cancel.spec.ts --grep 'S-FE-02'"] | . | 1200 | |
| S-203 | 09-task-schedule.frontend.design.md#2.4 验收条件 | E2E | Browser→tasks API→真实 PG→Task 列表/详情 | TASK-032 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep 'S-FE-03'"] | . | 1200 | |
| S-204 | 09-task-schedule.frontend.design.md#2.4 验收条件 | E2E | Browser→schedules API→真实 PG | TASK-035 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-list.spec.ts --grep 'S-FE-04'"] | . | 1200 | |
| E-201 | 09-task-schedule.frontend.design.md#2.4 验收条件 | E2E | 真实 pause API 失败→Browser UI | TASK-038 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-actions.spec.ts --grep 'E-FE-01'"] | . | 1200 | |
| E-202 | 09-task-schedule.frontend.design.md#2.4 验收条件 | integration | 真实 Task detail API→SideSheet | TASK-032 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep 'E-FE-02'"] | . | 600 | |
| E-203 | 09-task-schedule.frontend.design.md#2.4 验收条件 | integration | 真实 tasks API→详情 UI | TASK-032 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep 'E-FE-03'"] | . | 600 | |
| B-101 | 09-task-schedule.backend.design.md#3.3 数据设计 | integration | Alembic→真实 PostgreSQL→SQLAlchemy ORM | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_task_schema_parity.py"] | . | 600 | |
| B-102 | 09-task-schedule.backend.design.md#3.3 数据设计 | integration | 迁移→真实 PostgreSQL partial unique | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_submission_schema_parity.py"] | . | 600 | |
| B-103 | 09-task-schedule.backend.design.md#3.3.5 状态枚举 | unit | Pydantic 公共契约与序列化 | TASK-003 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_task_contracts.py"] | . | 600 | |
| B-104 | 09-task-schedule.backend.design.md#3.3.4 `task.task_event` | integration | 并发 PG Session→Task 行锁→TaskEvent | TASK-004 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_task_events.py"] | . | 600 | |
| B-105 | 09-task-schedule.backend.design.md#3.3.2 `task.delivery_route` | integration | 真实 PG delivery_route partial unique | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_delivery_routes.py"] | . | 600 | |
| B-106 | 09-task-schedule.backend.design.md#3.4 接口设计 | integration | 真实 HTTP handler→PG 幂等记录/事务 | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_submission_idempotency.py"] | . | 600 | |
| B-107 | 09-task-schedule.backend.design.md#API-01 Internal 创建 Task | integration | Worker Task HTTP→PG Task/Submission/Event | TASK-007 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_service.py"] | . | 600 | |
| B-108 | 09-task-schedule.backend.design.md#API-03 Internal Task 列表 | integration | 真实 Worker HTTP→PG Task/Event/children | TASK-008 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_queries.py"] | . | 600 | |
| B-109 | 09-task-schedule.backend.design.md#API-02 Internal 创建 Schedule | integration | Schedule HTTP→PG→真实时区计算 | TASK-009 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_schedule_writes.py"] | . | 600 | |
| B-110 | 09-task-schedule.backend.design.md#API-06 Internal Schedule 列表 | integration | HTTP→ScheduleService→PG CAS | TASK-010 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_schedule_queries_actions.py"] | . | 600 | |
| B-111 | 09-task-schedule.backend.design.md#3.2.1 执行主流程 | integration | 双 Worker→真实 PG 行锁/CAS | TASK-011 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_worker_leases.py"] | . | 600 | |
| B-112 | 09-task-schedule.backend.design.md#3.3.3 `task.task_execution` | integration | Worker→真实 NFS Artifact→emptyDir cache→真实 Skill handler | TASK-012 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_executor.py"] | . | 600 | |
| B-113 | 09-task-schedule.backend.design.md#3.2.1 执行主流程 | integration | 真实 Skill 执行结果→Worker→PG CAS | TASK-013 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_worker_outcomes.py"] | . | 600 | |
| B-114 | 09-task-schedule.backend.design.md#API-05 Internal 取消 Task | integration | 取消 HTTP→PG 标记/真实 Redis hint→运行 Worker | TASK-014 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_cancel.py"] | . | 600 | |
| B-115 | 09-task-schedule.backend.design.md#3.2.3 Schedule 触发、多副本与 Misfire | integration | 双 Scheduler→真实 Console resolve→PG grants/Binding/Task | TASK-015 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_schedule_trigger.py"] | . | 600 | |
| B-116 | 09-task-schedule.backend.design.md#3.2.3 Schedule 触发、多副本与 Misfire | integration | Scheduler→真实 PG→审计记录/指标采集 | TASK-016 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_scheduler_misfire.py"] | . | 600 | |
| B-117 | 09-task-schedule.backend.design.md#3.2.2 Task 状态机 | integration | 真实 Scheduler→PG→Gateway HTTP/Redis | TASK-017 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_task_deadline.py"] | . | 600 | |
| B-118 | 09-task-schedule.backend.design.md#3.2.4 Fan-out / Fan-in | integration | 真实 BATCH executor→PG Parent/Child/unique | TASK-018 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_batch_fanout.py"] | . | 600 | |
| B-119 | 09-task-schedule.backend.design.md#3.2.4 Fan-out / Fan-in | integration | 并发 Child 终态事务→真实 PG→Parent CAS | TASK-019 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_batch_fanin.py"] | . | 600 | |
| B-120 | 09-task-schedule.backend.design.md#3.2.5 Final Delivery | integration | 双 DeliveryLoop→真实 PG→Gateway HTTP | TASK-020 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_delivery.py"] | . | 600 | |
| B-121 | 09-task-schedule.backend.design.md#3.2.5 Final Delivery | integration | 真实 Worker HTTP→Gateway→Redis→本地渠道 HTTP 探针 | TASK-021 | planned | ["uv","run","pytest","-q","tests/gateway/test_delivery_api.py"] | . | 600 | |
| B-122 | 09-task-schedule.backend.design.md#3.1 技术选型与关键决策 | integration | 真实 FastAPI lifespan→后台循环→PG/Redis/NFS probes | TASK-022 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_worker_lifecycle.py"] | . | 600 | |
| B-123 | 09-task-schedule.backend.design.md#API-01 Internal 创建 Task | integration | 真实 Runtime Tool→Worker HTTP→PG | TASK-023 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_task_handoff.py"] | . | 600 | |
| B-124 | 09-task-schedule.backend.design.md#API-17 Worker Admin API | integration | 真实内部 HTTP→Admin guard→TaskService→PG | TASK-024 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_admin_tasks.py"] | . | 600 | |
| B-125 | 09-task-schedule.backend.design.md#API-17 Worker Admin API | integration | 真实内部 HTTP→Admin guard→ScheduleService→PG | TASK-025 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_admin_schedules.py"] | . | 600 | |
| B-126 | 09-task-schedule.backend.design.md#3.4 接口设计 | integration | Console HTTP client→真实 Worker HTTP | TASK-026 | planned | ["uv","run","pytest","-q","tests/console_tasks/test_worker_client.py"] | . | 600 | |
| B-127 | 09-task-schedule.backend.design.md#API-09 任务列表（Console） | integration | 真实 Console HTTP/session→Worker HTTP→PG | TASK-027 | planned | ["uv","run","pytest","-q","tests/console_tasks/test_tasks_api.py"] | . | 600 | |
| B-128 | 09-task-schedule.backend.design.md#API-12 Schedule 列表（Console） | integration | 真实 Console HTTP→Worker Admin→PG | TASK-028 | planned | ["uv","run","pytest","-q","tests/console_tasks/test_schedules_api.py"] | . | 600 | |
| B-129 | 09-task-schedule.frontend.design.md#3.5 状态与数据流 | integration | TypeScript services 编译→真实 Console HTTP 契约 | TASK-029 | planned | ["uv","run","pytest","-q","tests/frontend/test_task_schedule_services.py"] | . | 600 | |
| B-130 | 09-task-schedule.frontend.design.md#3.1 技术选型 | unit | 两种 locale key 集合与错误目录 | TASK-030 | planned | ["uv","run","pytest","-q","tests/frontend/test_task_schedule_i18n.py"] | . | 600 | |
| B-131 | 09-task-schedule.frontend.design.md#3.2 页面与路由结构 | E2E | 真实 Chrome→Console/Worker HTTP→PG→列表渲染 | TASK-031 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-list.spec.ts"] | . | 1200 | |
| B-132 | 09-task-schedule.frontend.design.md#3.3 组件设计 | E2E | 真实 Browser→Task detail API→PG→SideSheet | TASK-032 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts"] | . | 1200 | |
| B-133 | 09-task-schedule.frontend.design.md#3.3 组件设计 | E2E | 真实 Worker 事件→PG→HTTP→浏览器 Timeline | TASK-033 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-timeline.spec.ts"] | . | 1200 | |
| B-134 | 09-task-schedule.frontend.design.md#3.3.1 每个按钮/操作的设计 | E2E | 真实 Chrome→Console cancel→Worker/PG→详情刷新 | TASK-034 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-cancel.spec.ts"] | . | 1200 | |
| B-135 | 09-task-schedule.frontend.design.md#3.2 页面与路由结构 | E2E | 真实 Chrome→Console schedules→Worker/PG | TASK-035 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-list.spec.ts"] | . | 1200 | |
| B-136 | 09-task-schedule.frontend.design.md#3.3 组件设计 | E2E | 真实 Chrome→Schedule detail HTTP→PG→SideSheet | TASK-036 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-detail.spec.ts"] | . | 1200 | |
| B-137 | 09-task-schedule.frontend.design.md#3.3 组件设计 | E2E | 真实 Browser→schedules API→tasks API→PG→详情 | TASK-037 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts"] | . | 1200 | |
| B-138 | 09-task-schedule.frontend.design.md#3.3.1 每个按钮/操作的设计 | E2E | 真实 Chrome→管理 API→Worker PG→UI | TASK-038 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-actions.spec.ts"] | . | 1200 | |
| B-139 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | integration | 真实服务进程/PG/Redis/NFS 挂载健康探针 | TASK-039 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_environment.py"] | . | 600 | |
| B-140 | 09-task-schedule.frontend.design.md#2.4 验收条件 | E2E | 真实 Chrome→静态 build→生产 Console 路由→Worker/PG | TASK-040 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/environment.spec.ts"] | . | 1200 | |
| B-141 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_execution.py","tests/acceptance/task_schedule/test_recovery.py","tests/acceptance/task_schedule/test_idempotency.py"] | . | 1200 | |
| B-142 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | 双 Scheduler→真实 Console resolve/grants/Artifact→PG→Worker | TASK-042 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_schedules.py"] | . | 1200 | |
| B-143 | 09-task-schedule.backend.design.md#2.5.2 功能验收场景 | E2E | Worker→PG Parent/Child→Gateway HTTP→真实 Redis→渠道 HTTP 探针 | TASK-043 | planned | ["uv","run","pytest","-q","tests/acceptance/task_schedule/test_batch.py","tests/acceptance/task_schedule/test_delivery.py"] | . | 1200 | |
| B-144 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | TASK-044 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts"] | . | 1200 | |
| B-201 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"] | . | 1200 | |
| B-202 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 | |
| B-203 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | TASK-044 | planned | ["bash","-lc","bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts' && bash -lc 'uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test'"] | . | 1200 | |
| B-204 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/architecture"] | . | 1200 | |
| B-205 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | integration | Alembic→真实 PostgreSQL→SQLAlchemy ORM | TASK-001 | verified | ["bash","-lc","uv run pytest -q tests/agent_worker/test_task_schema_parity.py && uv run pytest -q tests -k schema_parity"] | . | 600 | |
| B-206 | 09-task-schedule.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | TASK-044 | planned | ["bash","-lc","bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts' && bash -lc 'uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build'"] | . | 1200 | |
| B-207 | 09-task-schedule.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | TASK-044 | planned | ["bash","-lc","bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts' && bash -lc 'uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck'"] | . | 1200 | |
| B-208 | 09-task-schedule.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | TASK-044 | planned | ["bash","-lc","bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts' && bash -lc 'uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py'"] | . | 1200 | |
| B-209 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Worker→PG Parent/Child→Gateway HTTP→真实 Redis→渠道 HTTP 探针 | TASK-043 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_batch.py tests/acceptance/task_schedule/test_delivery.py && uv run pytest -q tests/console_channel tests/gateway"] | . | 1200 | |
| B-210 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"] | . | 1200 | |
| B-211 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/test_skill_artifact_cache.py"] | . | 1200 | |
| B-212 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | 双 Scheduler→真实 Console resolve/grants/Artifact→PG→Worker | TASK-042 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_schedules.py && bash -lc 'uv run pytest -q tests/agent_runtime -k \"executor or resolve\"'"] | . | 1200 | |
| B-213 | 09-task-schedule.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | TASK-044 | planned | ["bash","-lc","bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts' && bash -lc 'uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity'"] | . | 1200 | |
| B-214 | 09-task-schedule.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Browser→schedules API→tasks API→PG→详情 | TASK-037 | planned | ["bash","-lc","bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts' && bash -lc 'uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck'"] | . | 1200 | |
| B-215 | 09-task-schedule.backend.design.md#Spec Compliance Matrix | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | TASK-041 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/agent_worker tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py"] | . | 1200 | |

## Execution Aliases

当前 acceptance manifest 解析器只接受 S-/E-/B-数字 ID，不能直接识别 S-FE-/E-FE-/RULE-。保留原 ID 的 Source、Acceptance-Refs、用例名、层级与负责人，同时使用下表一对一执行别名；这些别名不新增场景、不降低验收要求。Contract/Coverage 使用执行 ID，便于后续工具回写证据。

| 原设计 / Rule ID | 执行 ID | 唯一 owner |
|---|---|---|
| S-FE-01 | S-201 | TASK-037 |
| S-FE-02 | S-202 | TASK-034 |
| S-FE-03 | S-203 | TASK-032 |
| S-FE-04 | S-204 | TASK-035 |
| E-FE-01 | E-201 | TASK-038 |
| E-FE-02 | E-202 | TASK-032 |
| E-FE-03 | E-203 | TASK-032 |
| RULE-api-002 | B-201 | TASK-041 |
| RULE-api-001 | B-202 | TASK-041 |
| RULE-test-001 | B-203 | TASK-044 |
| RULE-arch-001 | B-204 | TASK-041 |
| RULE-data-001 | B-205 | TASK-001 |
| RULE-ui-001 | B-206 | TASK-044 |
| RULE-front-001 | B-207 | TASK-044 |
| RULE-i18n-001 | B-208 | TASK-044 |
| RULE-im-001 | B-209 | TASK-043 |
| RULE-secret-001 | B-210 | TASK-041 |
| RULE-skill-001 | B-211 | TASK-041 |
| RULE-snapshot-001 | B-212 | TASK-042 |
| RULE-time-001 | B-213 | TASK-044 |
| RULE-ui-detail-001 | B-214 | TASK-037 |
| RULE-worker-001 | B-215 | TASK-041 |

## Business Rules and Risk Coverage

| 设计 Rule/Risk | 验证场景 | 验证责任 |
|---|---|---|
| RULE-01 | S-01 | TASK-041 |
| RULE-02 | S-01 / E-01 | TASK-041 |
| RULE-03 | S-01 + B-101 | TASK-001 |
| RULE-04 | S-01（按当前 Secret Rule 修订） | TASK-041 |
| RULE-05 | S-02 | TASK-042 |
| RULE-06 | S-01 / E-01 | TASK-041 |
| RULE-07 | S-01 + B-112 | TASK-041 |
| RULE-08 | S-03 | TASK-043 |
| RULE-09 | S-01 / E-01 | TASK-011 |
| RULE-10 | E-03 | TASK-017 |
| RULE-11 | E-04 | TASK-016 |
| RULE-12 | S-04 | TASK-043 |
| RULE-13 | S-03 / E-05 | TASK-043 |
| RULE-14 | S-01 + B-124/B-125/B-127/B-128 | TASK-041 |
| RISK-01 | E-01 | TASK-011 |
| RISK-02 | E-05 | TASK-021 |
| RISK-03 | E-04 | TASK-016 |
| RISK-04 | E-03 | TASK-017 |
| RISK-05 | S-04 | TASK-043 |
| RISK-06 | S-02 | TASK-042 |

设计业务 Rule 14/14、高影响 RISK 6/6 均已映射；多个场景共享一条业务规则时，各场景最终 owner 仍以 Acceptance Coverage 为准。前端 §4 风险：硬编码→B-130/RULE-i18n-001；重复公共组件→B-131/RULE-ui-detail-001；N+1→S-FE-01/B-131。

---

## TASK-001: 补齐 Task Schema 约束与迁移一致性

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: 09-task-schedule.backend.design.md#3.3 数据设计, 09-task-schedule.backend.design.md#4. 部署与运维
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-101, RULE-data-001, B-205
- **Files**: `apps/agent-worker/src/muad_agent_worker/infrastructure/models/task.py`, `migrations/versions/0010_task_event_execution_fk.py`, `tests/agent_worker/test_task_schema_parity.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

复用 0002 已建四表，补 task_event→task_execution 同 schema 物理外键并核对 partial unique、timestamptz、deadline 默认值；通过新 expand migration 修复，不重写已发布迁移。

### Checklist

- [x] [B-101][integration] 修改生产代码前先覆盖 Alembic→真实 PostgreSQL→SQLAlchemy ORM 并记录 RED：标准列、JSONB、同域 FK/跨域 UUID、两类幂等索引一致；deadline 非空默认 +24h；task_type 仅 SKILL/BATCH。
- [x] 实现：复用 0002 已建四表，补 task_event→task_execution 同 schema 物理外键并核对 partial unique、timestamptz、deadline 默认值；通过新 expand migration 修复，不重写已发布迁移。
- [x] [B-101][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [x] [RULE-data-001][integration] verifier_ref=`harness-data#RULE-data-001`；继承原 verifier `uv run pytest -q tests -k schema_parity`；结合本模块 B-101 的真实输入验证：四表及提交表标准列/JSONB/timestamptz/partial unique 一致，同 Owner 物理 FK，跨域只存 UUID。
- [x] 执行 `uv run pytest -q tests/agent_worker/test_task_schema_parity.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-101 | integration | Alembic→真实 PostgreSQL→SQLAlchemy ORM | 标准列、JSONB、同域 FK/跨域 UUID、两类幂等索引一致；deadline 非空默认 +24h；task_type 仅 SKILL/BATCH | tests/agent_worker/test_task_schema_parity.py / B-101（verified） | uv run pytest -q tests/agent_worker/test_task_schema_parity.py | verified |
| B-205 | integration | Alembic→真实 PostgreSQL→SQLAlchemy ORM | 四表及提交表标准列/JSONB/timestamptz/partial unique 一致，同 Owner 物理 FK，跨域只存 UUID | tests/agent_worker/test_task_schema_parity.py + 原 Spec verifier / RULE-data-001（verified） | bash -lc 'uv run pytest -q tests/agent_worker/test_task_schema_parity.py && uv run pytest -q tests -k schema_parity' | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-101 | FAIL: `task_event ORM foreign keys: set() != {('task_id', 'task.task_execution.id')}`（`test_same_schema_foreign_keys`） | PASS: 6 passed | `tests/agent_worker/test_task_schema_parity.py:246`（ORM/DB 双向 FK 核对）与 `test_task_execution_server_defaults` | 真实 PostgreSQL（`.env` DATABASE_URL）+ `inspect()` 反射 `task` schema；Alembic 0010 已 upgrade head | verified |
| B-205 | FAIL: 同 B-101（同一用例，实现前） | PASS: `uv run pytest -q tests -k schema_parity` → 29 passed；`tests/agent_worker` 43 passed 无回归 | 同上；另覆盖 `harness-data#RULE-data-001` 原 verifier 命令 | 真实 PG；Alembic head=0010；`pg_constraint` 实测存在 `task_event_task_id_fkey` | verified |

清理证据：迁移前 `task.task_event` 有 3 条 `test-*` 租户的悬空行（父 `task_execution` 已不存在），会让外键建立失败；已按 `tenant_id LIKE 'test-%' AND 父行不存在` 精确定位并删除，未触碰非 test 租户数据。迁移后全库孤儿计数为 0。

> 范围说明：本任务只补 `task_event → task_execution` 同 schema 物理外键（0002 遗漏）。`deadline_at` 的 +24h 非空默认与 `task_type` 默认值经核对本就正确，未改动；`task_submission` 表属 TASK-002，不在本任务范围。
- B-101: verified — automated command passed; run_id=461fc4025c914b2f8e88e77007e80660 (confirmed_by: runner)
- B-205: verified — automated command passed; run_id=461fc4025c914b2f8e88e77007e80660 (confirmed_by: runner)

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---
- [2026-09-21] started
- [2026-09-21] completed (done)
## TASK-002: 新增提交幂等记录的持久化模型

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 09-task-schedule.backend.design.md#3.3.8 `task.task_submission`, 09-task-schedule.backend.design.md#3.4 接口设计
- **Spec-Refs**: 
- **Acceptance-Refs**: B-102
- **Files**: `apps/agent-worker/src/muad_agent_worker/infrastructure/models/task_submission.py`, `apps/agent-worker/src/muad_agent_worker/infrastructure/models/__init__.py`, `migrations/versions/0011_task_submission.py`, `tests/agent_worker/test_submission_schema_parity.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

承接新增 API 幂等 Rule：在 task schema 保存 tenant/key/endpoint、指纹与首次响应，同业务提交一并提交或回滚；不复用 control 的 Skill 导入专用表。

### Checklist

- [x] [B-102][integration] 修改生产代码前先覆盖 迁移→真实 PostgreSQL partial unique 并记录 RED：同租户/键/端点只保留一条有效记录；不同租户和端点隔离；回滚无首次响应残留。
- [x] 实现：承接新增 API 幂等 Rule：在 task schema 保存 tenant/key/endpoint、指纹与首次响应，同业务提交一并提交或回滚；不复用 control 的 Skill 导入专用表。
- [x] [B-102][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [x] 执行 `uv run pytest -q tests/agent_worker/test_submission_schema_parity.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-102 | integration | 迁移→真实 PostgreSQL partial unique | 同租户/键/端点只保留一条有效记录；不同租户和端点隔离；回滚无首次响应残留 | tests/agent_worker/test_submission_schema_parity.py / B-102（verified） | uv run pytest -q tests/agent_worker/test_submission_schema_parity.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-102 | ERROR: `ModuleNotFoundError: No module named 'muad_agent_worker.infrastructure.models.task_submission'`（收集期失败：模型与表均不存在） | PASS: 6 passed | `tests/agent_worker/test_submission_schema_parity.py`：`test_task_submission_schema_parity`（标准列/JSONB/同 schema FK）、`test_task_submission_partial_unique_index`、`test_duplicate_submission_is_rejected`、`test_tenant_and_endpoint_are_isolated`、`test_soft_deleted_row_frees_the_key`、`test_rollback_leaves_no_submission` | 真实 PostgreSQL（`.env` DATABASE_URL）；Alembic 0011 upgrade head；partial unique 经 `inspect()` 实测 `unique=True` 且带 `postgresql_where` | verified |

回归：`tests/agent_worker` 49 passed（原 43 + 新 6）；`uv run pytest -q tests -k schema_parity` 35 passed。

> 说明：partial unique 行为用真实 PG 触发 `IntegrityError` 验证，未用 SQLite 或 mock 顶替。并发插入由该索引兜底属 TASK-006 提交路径的职责，本任务只落模型与约束。
- B-102: verified — automated command passed; run_id=07fadb73ad1a4bb28c906201748d119e (confirmed_by: runner)

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---
- [2026-09-21] started
- [2026-09-21] completed (done)
## TASK-003: 收紧 Task/Schedule 请求与响应契约

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: 09-task-schedule.backend.design.md#3.3.5 状态枚举, 09-task-schedule.backend.design.md#3.4 接口设计
- **Spec-Refs**: 
- **Acceptance-Refs**: B-103
- **Files**: `packages/contracts/src/muad_contracts/tasks.py`, `packages/contracts/src/muad_contracts/enums.py`, `packages/contracts/src/muad_contracts/__init__.py`, `tests/agent_worker/test_task_contracts.py`, `tests/test_contracts.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

补查询、详情、更新、分页与取消响应类型；快照用已存在强类型契约或受限 JSON 类型，拒绝 CANCELLING、EXTERNAL、AGENT_STEP 与 misfire_policy；ScheduleStatus 收紧为 ACTIVE/PAUSED/COMPLETED/MISSED（MISSED 为 ONCE 错过触发的终态）。

### Checklist

- [x] [B-103][unit] 修改生产代码前先覆盖 Pydantic 公共契约与序列化 并记录 RED：CRON/ONCE 互斥必填、IANA 校验、分页 1..100、非终态取消响应 RUNNING+cancel_requested；deadline 筛选独立参数；ScheduleStatus 含 MISSED 终态。
- [x] 实现：补查询、详情、更新、分页与取消响应类型；快照用已存在强类型契约或受限 JSON 类型，拒绝 CANCELLING、EXTERNAL、AGENT_STEP 与 misfire_policy；ScheduleStatus 收紧为 ACTIVE/PAUSED/COMPLETED/MISSED。
- [x] [B-103][unit] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [x] 执行 `uv run pytest -q tests/agent_worker/test_task_contracts.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-103 | unit | Pydantic 公共契约与序列化 | CRON/ONCE 互斥必填、IANA 校验、分页 1..100、非终态取消响应 RUNNING+cancel_requested；deadline 筛选独立参数；ScheduleStatus 含 MISSED 终态 | tests/agent_worker/test_task_contracts.py / B-103（verified） | uv run pytest -q tests/agent_worker/test_task_contracts.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-103 | ERROR: `ImportError: cannot import name 'CancelTaskResponse' from 'muad_contracts'`（收集期失败：新契约模型与 `ScheduleStatus.MISSED` 均不存在） | PASS: 21 passed（本文件）；全量 `uv run pytest -q tests` 976 passed | `tests/agent_worker/test_task_contracts.py`：`TestScheduleSpec`（CRON/ONCE 互斥必填、IANA 拒绝）、`TestStatusEnums`（MISSED 终态、无 CANCELLING、TaskType 仅 SKILL/BATCH）、`TestTaskListQuery`（page_size 1..100、page≥1、deadline 与 start/end 独立）、`TestCancelTaskResponse`（RUNNING+cancel_requested、拒绝 CANCELLING）、`TestCreateScheduleRequest`（拒绝 misfire_policy、extra forbid） | Pydantic 契约层（unit；无需 DB，未用 mock 冒充） | verified |

回归修复：`tests/test_contracts.py::test_enum_members_spot_check` 原本断言 ScheduleStatus 仅 3 值，随 N-04 决议加入 `MISSED` 后同步更新为 4 值。

> 说明：本任务只落契约类型与枚举；API 侧接线（deadline 参数透传、取消响应字段）属 TASK-008 / TASK-014。
- B-103: verified — automated command passed; run_id=a6d540c8dc7e4309b35d5bdac05b9852 (confirmed_by: runner)

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---
- [2026-09-21] started
- [2026-09-21] completed (done)
## TASK-004: 使 TaskEvent 序号分配并发安全

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 09-task-schedule.backend.design.md#3.3.4 `task.task_event`, 09-task-schedule.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-104
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/task_events.py`, `tests/agent_worker/test_task_events.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

用同一 Task 的数据库锁/序号分配保证 append-only 单调 seq，事件和状态同事务；补 WAITING/FAN_OUT/FAN_IN 事件与 trace。

### Checklist

- [x] [B-104][integration] 修改生产代码前先覆盖 并发 PG Session→Task 行锁→TaskEvent 并记录 RED：并发追加无重号；事务回滚无事件；跨重试 Timeline 单调；payload 已脱敏。
- [x] 实现：用同一 Task 的数据库锁/序号分配保证 append-only 单调 seq，事件和状态同事务；补 WAITING/FAN_OUT/FAN_IN 事件与 trace。
- [x] [B-104][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [x] 执行 `uv run pytest -q tests/agent_worker/test_task_events.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-104 | integration | 并发 PG Session→Task 行锁→TaskEvent | 并发追加无重号；事务回滚无事件；跨重试 Timeline 单调；payload 已脱敏 | tests/agent_worker/test_task_events.py / B-104（verified） | uv run pytest -q tests/agent_worker/test_task_events.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-104 | 5 FAILED / 4 passed：`test_same_task_appends_are_serialized`（后来者未被行锁阻塞）、`test_concurrent_appends_never_duplicate_seq`（seq 重号）、`test_payload_is_redacted`（payload 未脱敏）、`test_waiting_and_fan_events_are_supported`（缺 WAITING/FAN_OUT/FAN_IN）、`test_trace_id_is_persisted`（seed 无 trace_id） | PASS: 9 passed；全量 `uv run pytest -q tests` 985 passed | `tests/agent_worker/test_task_events.py`：`test_same_task_appends_are_serialized`（A 持锁期间 B 必须阻塞，A 提交后 seq=[1,2]）、`test_concurrent_appends_never_duplicate_seq`（5 个独立会话并发 → seq=1..5）、`test_timeline_is_monotonic_across_retries`、`test_rollback_leaves_no_event`、`test_batch_append_assigns_contiguous_seq`、`test_payload_is_redacted`、`test_waiting_and_fan_events_are_supported`、`test_trace_id_is_persisted`、`test_unknown_task_id_is_rejected` | 真实 PostgreSQL：两个及以上独立 `AsyncSession` 真正并发，靠 `SELECT ... FOR UPDATE` 父 Task 行串行化；脱敏复用 `muad_logging.redaction.redact_value`（未自造一套） | verified |

> 根因：原 `_seq_floor` 在无锁情况下读 `max(seq)`，并发事务会取到同一下界并写同一 seq，由 `UNIQUE (task_id, seq)` 在提交时抛出。修复为「先锁父 Task 行、再读下界、同事务写事件」，锁按 task_id 排序避免多 Task 批次死锁。事件与状态同事务的要求由调用方在同一 session 内完成，回滚不留事件已由本用例覆盖。
- B-104: verified — automated command passed; run_id=7d39a4587df940978e1df521adfc3b63 (confirmed_by: runner)

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---
- [2026-09-21] started
- [2026-09-21] completed (done)
## TASK-005: 修正投递路由归一化和租户隔离

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: 09-task-schedule.backend.design.md#3.3.2 `task.delivery_route`
- **Spec-Refs**: 
- **Acceptance-Refs**: B-105
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/delivery_routes.py`, `tests/agent_worker/test_delivery_routes.py`, `tests/agent_worker/test_task_service.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

复用 route_hash upsert，归一化元组包含 tenant/platform_user/channel/bot/接收方身份，防止跨租户复用；路由不得包含 Bot Secret 或 Pod 信息。

### Checklist

- [x] [B-105][integration] 修改生产代码前先覆盖 真实 PG delivery_route partial unique 并记录 RED：同租户同路由并发复用；跨租户及接收方不同不复用；软删除后可重建；无密钥字段。
- [x] 实现：复用 route_hash upsert，归一化元组包含 tenant/platform_user/channel/bot/接收方身份，防止跨租户复用；路由不得包含 Bot Secret 或 Pod 信息。
- [x] [B-105][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [x] 执行 `uv run pytest -q tests/agent_worker/test_delivery_routes.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-105 | integration | 真实 PG delivery_route partial unique | 同租户同路由并发复用；跨租户及接收方不同不复用；软删除后可重建；无密钥字段 | tests/agent_worker/test_delivery_routes.py / B-105（verified） | uv run pytest -q tests/agent_worker/test_delivery_routes.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-105 | FAIL: 1 failed / 6 passed —— `test_different_platform_user_does_not_reuse`：两个不同 `platform_user_id` 拿到同一个 route_id | PASS: 7 passed；全量 `uv run pytest -q tests` 992 passed | `tests/agent_worker/test_delivery_routes.py`：`test_same_route_is_reused`、`test_concurrent_same_route_yields_single_row`（4 并发返回同一 id）、`test_different_tenant_does_not_reuse`、`test_different_recipient_does_not_reuse`、`test_different_platform_user_does_not_reuse`、`test_soft_deleted_route_can_be_recreated`、`test_route_row_carries_no_secret_or_pod_fields` | 真实 PostgreSQL：`UNIQUE (route_hash) WHERE is_deleted=false` 的 partial unique；并发用 4 个独立 session 真实并发 | verified |

> 实测只有 1/7 断言是 RED。跨租户隔离、接收方差异、并发复用、软删除重建、无密钥/Pod 字段这 5 项经真实 PG 验证**本就正确，未改动**。真正的缺陷是 `canonical_route_tuple` 漏了 `platform_user_id`，使同一租户下不同平台用户折叠到同一条路由。
>
> 连带修复：`tests/agent_worker/test_task_service.py::test_create_reuses_delivery_route` 原本用两个不同 `actor_user_id` 断言路由复用（把缺陷当成预期），已改为同一 actor，保持该用例原有意图。
>
> 兼容性：route_hash 口径变化会让既有 `delivery_route` 行不再命中，新提交会新建路由行。本模块尚未上线，无需数据回填。
- B-105: verified — automated command passed; run_id=88c0a9fd2cc5482f8a5dd5cb20c196eb (confirmed_by: runner)

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---
- [2026-09-21] started
- [2026-09-21] completed (done)
## TASK-006: 实现提交指纹校验与首次响应重放

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: 09-task-schedule.backend.design.md#3.3.8 `task.task_submission`, 09-task-schedule.backend.design.md#3.4 接口设计, 09-task-schedule.backend.design.md#API-01 Internal 创建 Task, 09-task-schedule.backend.design.md#API-02 Internal 创建 Schedule
- **Spec-Refs**: 
- **Acceptance-Refs**: E-07, B-106
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/submissions.py`, `apps/agent-worker/src/muad_agent_worker/api/tasks.py`, `tests/agent_worker/test_submission_idempotency.py`, `tests/agent_worker/conftest.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

封装两类创建 POST 的 Idempotency-Key 处理；指纹为 endpoint、规范化关键参数及内容哈希，同键同指纹返回首次业务响应，异指纹返回 IDEMPOTENCY_MISMATCH。

### Checklist

- [x] [B-106][integration] 修改生产代码前先覆盖 真实 HTTP handler→PG 幂等记录/事务 并记录 RED：并发同键只执行一次；重放 200 原业务结果；异指纹返回 IDEMPOTENCY_MISMATCH；失败事务不占成功记录；Task Header 与已有 body key 一致。
- [x] 实现：封装两类创建 POST 的 Idempotency-Key 处理；指纹为 endpoint、规范化关键参数及内容哈希，同键同指纹返回首次业务响应，异指纹返回 IDEMPOTENCY_MISMATCH。
- [x] [B-106][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [x] [E-07][integration] 先记录 RED，再按 Worker HTTP→task.task_submission partial unique 验证：同 key 同指纹重放首次持久化结果且不重复建资源；同 key 不同指纹返回 IDEMPOTENCY_MISMATCH；并发同 key 只成功一次；命令 `uv run pytest -q tests/agent_worker/test_submission_idempotency.py -k e07`。
- [x] 执行 `uv run pytest -q tests/agent_worker/test_submission_idempotency.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-07 | integration | Worker HTTP→task.task_submission partial unique | 同 key 同指纹重放首次持久化结果且不重复建资源；同 key 不同指纹返回 IDEMPOTENCY_MISMATCH；并发同 key 只成功一次 | tests/agent_worker/test_submission_idempotency.py / e07（verified） | uv run pytest -q tests/agent_worker/test_submission_idempotency.py -k e07 | verified |
| B-106 | integration | 真实 HTTP handler→PG 幂等记录/事务 | 并发同键只执行一次；重放 200 原业务结果；异指纹返回 IDEMPOTENCY_MISMATCH；失败事务不占成功记录；Task Header 与已有 body key 一致 | tests/agent_worker/test_submission_idempotency.py / B-106（verified） | uv run pytest -q tests/agent_worker/test_submission_idempotency.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-07 | FAIL: 6 failed / 1 passed —— 同键异 payload 返回 200（静默复用首次 Task）而非 409；无 `task_submission` 记录；Header 未参与 | PASS: 7 passed | `test_e07_replay_and_mismatch_on_real_pg`、`test_same_key_same_payload_replays_first_response`、`test_mismatched_payload_returns_idempotency_mismatch`、`test_concurrent_same_key_creates_one_task`（4 并发只建 1 个 Task） | 真实 ASGI HTTP → 真实 PG `task.task_submission` partial unique；并发为真实并发请求 | verified |
| B-106 | FAIL: 同上 | PASS: 7 passed；全量 `uv run pytest -q tests` 999 passed | 另含 `test_header_key_must_agree_with_body_key`（Header≠body → 409 且不落库）、`test_matching_header_and_body_key_succeeds`、`test_failed_request_does_not_reserve_the_key` | 同上；校验层 422 不落库、失败不占记录由 `get_session` 成功才 commit 保证 | verified |

> 缺口来源：`TaskService.create` 原有的 body 幂等只按 key 命中、不比对指纹，因此「同键异 payload」会被静默复用首次结果。本次补齐指纹校验与 `task_submission` 记录；并发落败者用 `begin_nested()` savepoint 承接 `IntegrityError` 后回读首次结果重放（落地设计「并发插入由 partial unique 兜底，落败者读取首次提交结果」）。
>
> 连带改动：`tests/agent_worker/conftest.py` 必须先清理 `task_submission` 再删 `task_execution`——TASK-002 的同 schema 外键让清理顺序成为硬约束，同时把该表加入探活表清单。
>
> 范围：Schedule 侧（`create-schedule`）的 handler 接线属 TASK-009；本模块已提供 `ENDPOINT_CREATE_SCHEDULE` 与通用指纹函数，接线时直接复用。
- E-07: verified — automated command passed; run_id=bb890bffc4f84752afa67d2e36e6bec5 (confirmed_by: runner)
- B-106: verified — automated command passed; run_id=bb890bffc4f84752afa67d2e36e6bec5 (confirmed_by: runner)

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---
- [2026-09-21] started
- [2026-09-21] completed (done)
## TASK-007: 完善 Task 创建、快照校验与原子事件

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-005, TASK-006
- **Source**: 09-task-schedule.backend.design.md#API-01 Internal 创建 Task, 09-task-schedule.backend.design.md#3.3.3 `task.task_execution`
- **Spec-Refs**: 
- **Acceptance-Refs**: B-107
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/task_service.py`, `apps/agent-worker/src/muad_agent_worker/api/tasks.py`, `tests/agent_worker/test_task_service.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

接入提交幂等服务并校验 Snapshot/hash，原子保存 QUEUED、CREATED、delivery_key 与 +24h deadline；仅在提交后发 hint，失败仍可由 PG 扫描推进。

### Checklist

- [ ] [B-107][integration] 修改生产代码前先覆盖 Worker Task HTTP→PG Task/Submission/Event 并记录 RED：快照必需版本键冻结且不含密钥；同键异指纹返回 IDEMPOTENCY_MISMATCH 且不复用已有 Task；NONE 无投递；Redis hint 失败不丢任务。
- [ ] 实现：接入提交幂等服务并校验 Snapshot/hash，原子保存 QUEUED、CREATED、delivery_key 与 +24h deadline；仅在提交后发 hint，失败仍可由 PG 扫描推进。
- [ ] [B-107][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_task_service.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-107 | integration | Worker Task HTTP→PG Task/Submission/Event | 快照必需版本键冻结且不含密钥；同键异指纹返回 IDEMPOTENCY_MISMATCH 且不复用已有 Task；NONE 无投递；Redis hint 失败不丢任务 | tests/agent_worker/test_task_service.py / B-107（planned） | uv run pytest -q tests/agent_worker/test_task_service.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-008: 补齐任务列表、详情与 Timeline 查询

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-004
- **Source**: 09-task-schedule.backend.design.md#API-03 Internal Task 列表, 09-task-schedule.backend.design.md#API-04 Internal Task 详情
- **Spec-Refs**: 
- **Acceptance-Refs**: B-108
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/task_service.py`, `apps/agent-worker/src/muad_agent_worker/api/tasks.py`, `tests/agent_worker/test_task_queries.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

补截止时间筛选、完整摘要/详情、Timeline、子任务与 Snapshot 摘要；分页和聚合查询使用有界 SQL，避免按行 N+1。

### Checklist

- [ ] [B-108][integration] 修改生产代码前先覆盖 真实 Worker HTTP→PG Task/Event/children 并记录 RED：租户隔离且不存在 404；schedule_id 精确过滤；start_time/end_time 作用于 create_time、deadline_from/deadline_to 作用于 deadline_at（UTC、含端点）且不混用；seq 升序；page_size≤100；响应无秘密。
- [ ] 实现：补截止时间筛选、完整摘要/详情、Timeline、子任务与 Snapshot 摘要；分页和聚合查询使用有界 SQL，避免按行 N+1。
- [ ] [B-108][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_task_queries.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-108 | integration | 真实 Worker HTTP→PG Task/Event/children | 租户隔离且不存在 404；schedule_id 精确过滤；start_time/end_time 作用于 create_time、deadline_from/deadline_to 作用于 deadline_at（UTC、含端点）且不混用；seq 升序；page_size≤100；响应无秘密 | tests/agent_worker/test_task_queries.py / B-108（planned） | uv run pytest -q tests/agent_worker/test_task_queries.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-009: 完善 Schedule 创建、更新与时区计算

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-005, TASK-006
- **Source**: 09-task-schedule.backend.design.md#API-02 Internal 创建 Schedule, 09-task-schedule.backend.design.md#API-07 Internal 更新 Schedule
- **Spec-Refs**: 
- **Acceptance-Refs**: B-109
- **Files**: `apps/agent-worker/src/muad_agent_worker/scheduler/service.py`, `apps/agent-worker/src/muad_agent_worker/api/schedules.py`, `tests/agent_worker/test_schedule_writes.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

接入创建幂等和标准更新请求；校验 owner/管理权限、CRON/ONCE、IANA 时区；更新递增 revision 并只影响将来触发。

### Checklist

- [ ] [B-109][integration] 修改生产代码前先覆盖 Schedule HTTP→PG→真实时区计算 并记录 RED：同键创建仅一条；非法时区/规则失败；COMPLETED/MISSED 不可改；revision 递增且旧 Task Snapshot 不变；DST 边界计算明确。
- [ ] 实现：接入创建幂等和标准更新请求；校验 owner/管理权限、CRON/ONCE、IANA 时区；更新递增 revision 并只影响将来触发。
- [ ] [B-109][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_schedule_writes.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-109 | integration | Schedule HTTP→PG→真实时区计算 | 同键创建仅一条；非法时区/规则失败；COMPLETED/MISSED 不可改；revision 递增且旧 Task Snapshot 不变；DST 边界计算明确 | tests/agent_worker/test_schedule_writes.py / B-109（planned） | uv run pytest -q tests/agent_worker/test_schedule_writes.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-010: 补齐 Schedule 分页、详情与管理状态转换

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-009
- **Source**: 09-task-schedule.backend.design.md#API-06 Internal Schedule 列表, 09-task-schedule.backend.design.md#API-08 Internal 删除 Schedule, 09-task-schedule.backend.design.md#API-14 暂停 Schedule（Console）, 09-task-schedule.backend.design.md#API-15 恢复 Schedule（Console）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-110
- **Files**: `apps/agent-worker/src/muad_agent_worker/scheduler/service.py`, `apps/agent-worker/src/muad_agent_worker/api/schedules.py`, `tests/agent_worker/test_schedule_queries_actions.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

把裸数组改为分页封套，增加状态/Agent/用户过滤和详情；暂停/恢复 CAS、删除幂等，恢复按当前时间重算且不补发；COMPLETED/MISSED 为终态，暂停/恢复返回 REVISION_CONFLICT。

### Checklist

- [ ] [B-110][integration] 修改生产代码前先覆盖 HTTP→ScheduleService→PG CAS 并记录 RED：含 COMPLETED/MISSED 筛选；重复暂停/删除行为稳定；删除不影响已创建 Task；权限失败不改状态；COMPLETED/MISSED 终态拒绝暂停与恢复（REVISION_CONFLICT）；无误补发。
- [ ] 实现：把裸数组改为分页封套，增加状态/Agent/用户过滤和详情；暂停/恢复 CAS、删除幂等，恢复按当前时间重算且不补发；COMPLETED/MISSED 为终态，暂停/恢复返回 REVISION_CONFLICT。
- [ ] [B-110][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_schedule_queries_actions.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-110 | integration | HTTP→ScheduleService→PG CAS | 含 COMPLETED/MISSED 筛选；重复暂停/删除行为稳定；删除不影响已创建 Task；权限失败不改状态；COMPLETED/MISSED 终态拒绝暂停与恢复（REVISION_CONFLICT）；无误补发 | tests/agent_worker/test_schedule_queries_actions.py / B-110（planned） | uv run pytest -q tests/agent_worker/test_schedule_queries_actions.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-011: 加固 claim、heartbeat 和 reclaim 租约

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-007, TASK-012
- **Source**: 09-task-schedule.backend.design.md#3.2.1 执行主流程, 09-task-schedule.backend.design.md#3.2.2 Task 状态机
- **Spec-Refs**: 
- **Acceptance-Refs**: E-01, B-111
- **Files**: `apps/agent-worker/src/muad_agent_worker/worker/claimer.py`, `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `tests/agent_worker/test_worker_leases.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

保留 SKIP LOCKED，补并发 claim、lease fence 和失约停止；reclaim 只取 RUNNING 且未取消，保留 attempt，旧持有者不能再提交结果。

### Checklist

- [ ] [B-111][integration] 修改生产代码前先覆盖 双 Worker→真实 PG 行锁/CAS 并记录 RED：同一时刻单持有者；未到 not_before/已取消不可 claim；crash 后换 Worker；WAITING 不 reclaim；失约旧 Worker 写终态失败。
- [ ] 实现：保留 SKIP LOCKED，补并发 claim、lease fence 和失约停止；reclaim 只取 RUNNING 且未取消，保留 attempt，旧持有者不能再提交结果。
- [ ] [B-111][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-01][integration] 先记录 RED，再按 双 Worker→真实 PG lease/CAS→真实幂等 Skill 副作用 验证：crash 失约被其他实例 reclaim，attempt 保留，旧持有者不能覆写，无重复副作用；命令 `uv run pytest -q tests/agent_worker/test_worker_leases.py -k e01`。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_worker_leases.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | 双 Worker→真实 PG lease/CAS→真实幂等 Skill 副作用 | crash 失约被其他实例 reclaim，attempt 保留，旧持有者不能覆写，无重复副作用 | tests/agent_worker/test_worker_leases.py / e01（planned） | uv run pytest -q tests/agent_worker/test_worker_leases.py -k e01 | planned |
| B-111 | integration | 双 Worker→真实 PG 行锁/CAS | 同一时刻单持有者；未到 not_before/已取消不可 claim；crash 后换 Worker；WAITING 不 reclaim；失约旧 Worker 写终态失败 | tests/agent_worker/test_worker_leases.py / B-111（planned） | uv run pytest -q tests/agent_worker/test_worker_leases.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-012: 替换占位执行器并接入冻结 Artifact

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-007
- **Source**: 09-task-schedule.backend.design.md#3.3.3 `task.task_execution`, 09-task-schedule.backend.design.md#3.2.6 跨模块引用边界
- **Spec-Refs**: 
- **Acceptance-Refs**: B-112
- **Files**: `apps/agent-worker/src/muad_agent_worker/worker/executor.py`, `apps/agent-worker/src/muad_agent_worker/bootstrap/artifacts.py`, `tests/agent_worker/test_task_executor.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

复用模块 08 的 Skill 执行链/凭据读取边界，按 Task 冻结的 artifact/checksum 执行；不重新选择 current 定义，不从 NFS 直接执行。

### Checklist

- [ ] [B-112][integration] 修改生产代码前先覆盖 Worker→真实 NFS Artifact→emptyDir cache→真实 Skill handler 并记录 RED：checksum 失败拒绝；同 checksum singleflight；执行路径为本地 READY；模型/Skill/MCP 版本按快照；密钥仅内存使用。
- [ ] 实现：复用模块 08 的 Skill 执行链/凭据读取边界，按 Task 冻结的 artifact/checksum 执行；不重新选择 current 定义，不从 NFS 直接执行。
- [ ] [B-112][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_task_executor.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-112 | integration | Worker→真实 NFS Artifact→emptyDir cache→真实 Skill handler | checksum 失败拒绝；同 checksum singleflight；执行路径为本地 READY；模型/Skill/MCP 版本按快照；密钥仅内存使用 | tests/agent_worker/test_task_executor.py / B-112（planned） | uv run pytest -q tests/agent_worker/test_task_executor.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-013: 实现 WAITING、重试及受保护的完成状态

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-011, TASK-012
- **Source**: 09-task-schedule.backend.design.md#3.2.1 执行主流程, 09-task-schedule.backend.design.md#3.2.2 Task 状态机
- **Spec-Refs**: 
- **Acceptance-Refs**: B-113
- **Files**: `apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py`, `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `tests/agent_worker/test_worker_outcomes.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

使用明确的完成/外部等待/可重试失败结果类型；WAITING 持久化 external_ref 和 not_before 后释放 lease；重试退避，预算耗尽 FAILED。

### Checklist

- [ ] [B-113][integration] 修改生产代码前先覆盖 真实 Skill 执行结果→Worker→PG CAS 并记录 RED：WAITING 不占 lease；到期再 claim；重试上限准确；结果先落库；取消/失约/终态不被成功返回覆盖；大结果沿已有 Artifact 引用契约。
- [ ] 实现：使用明确的完成/外部等待/可重试失败结果类型；WAITING 持久化 external_ref 和 not_before 后释放 lease；重试退避，预算耗尽 FAILED。
- [ ] [B-113][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_worker_outcomes.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-113 | integration | 真实 Skill 执行结果→Worker→PG CAS | WAITING 不占 lease；到期再 claim；重试上限准确；结果先落库；取消/失约/终态不被成功返回覆盖；大结果沿已有 Artifact 引用契约 | tests/agent_worker/test_worker_outcomes.py / B-113（planned） | uv run pytest -q tests/agent_worker/test_worker_outcomes.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-014: 补齐协作取消与取消竞态

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-008, TASK-013
- **Source**: 09-task-schedule.backend.design.md#API-05 Internal 取消 Task, 09-task-schedule.backend.design.md#3.2.2 Task 状态机
- **Spec-Refs**: 
- **Acceptance-Refs**: E-06, B-114
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/task_service.py`, `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `tests/agent_worker/test_task_cancel.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

QUEUED/WAITING CAS 取消；RUNNING 写取消标记并在心跳及真实工具调用检查点停止，响应保持 RUNNING+cancel_requested；重复 CANCELLED 成功，其他终态冲突。

### Checklist

- [ ] [B-114][integration] 修改生产代码前先覆盖 取消 HTTP→PG 标记/真实 Redis hint→运行 Worker 并记录 RED：WAITING 清租约；Redis 故障仍读取 PG 取消；成功与取消竞态不覆写；COMPLETED/FAILED 返回冲突；CANCELLED 仍遵循 delivery_mode。
- [ ] 实现：QUEUED/WAITING CAS 取消；RUNNING 写取消标记并在心跳及真实工具调用检查点停止，响应保持 RUNNING+cancel_requested；重复 CANCELLED 成功，其他终态冲突。
- [ ] [B-114][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-06][integration] 先记录 RED，再按 取消 API→真实 PG CAS→Worker 检查点 验证：QUEUED/WAITING 直接 CANCELLED；RUNNING 协作取消；已取消幂等；其他终态冲突；命令 `uv run pytest -q tests/agent_worker/test_task_cancel.py -k e06`。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_task_cancel.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-06 | integration | 取消 API→真实 PG CAS→Worker 检查点 | QUEUED/WAITING 直接 CANCELLED；RUNNING 协作取消；已取消幂等；其他终态冲突 | tests/agent_worker/test_task_cancel.py / e06（planned） | uv run pytest -q tests/agent_worker/test_task_cancel.py -k e06 | planned |
| B-114 | integration | 取消 HTTP→PG 标记/真实 Redis hint→运行 Worker | WAITING 清租约；Redis 故障仍读取 PG 取消；成功与取消竞态不覆写；COMPLETED/FAILED 返回冲突；CANCELLED 仍遵循 delivery_mode | tests/agent_worker/test_task_cancel.py / B-114（planned） | uv run pytest -q tests/agent_worker/test_task_cancel.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-015: 使 Schedule 触发原子化并冻结当前有效定义

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007, TASK-009, TASK-011
- **Source**: 09-task-schedule.backend.design.md#3.2.3 Schedule 触发、多副本与 Misfire, 09-task-schedule.backend.design.md#3.2.6 跨模块引用边界
- **Spec-Refs**: 
- **Acceptance-Refs**: E-02, B-115
- **Files**: `apps/agent-worker/src/muad_agent_worker/scheduler/service.py`, `apps/agent-worker/src/muad_agent_worker/scheduler/client.py`, `tests/agent_worker/test_schedule_trigger.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

修复 claim 后释放锁导致的竞态；有界 resolve 后在事务内复核 ACTIVE/revision/fire_time，原子插 Task/Event 并推进 Schedule；每次重查授权、稳定 skill_id 与 current Artifact。

### Checklist

- [ ] [B-115][integration] 修改生产代码前先覆盖 双 Scheduler→真实 Console resolve→PG grants/Binding/Task 并记录 RED：同 fire_time 一次创建；授权撤销 fail closed 并留原因；暂停/删除/修改赢得竞态后不创建；新触发快照变化而旧 Task 不变。
- [ ] 实现：修复 claim 后释放锁导致的竞态；有界 resolve 后在事务内复核 ACTIVE/revision/fire_time，原子插 Task/Event 并推进 Schedule；每次重查授权、稳定 skill_id 与 current Artifact。
- [ ] [B-115][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-02][integration] 先记录 RED，再按 Scheduler→真实 Console resolve→PG grants/Binding 验证：撤权或 Binding 软删除后不创建可执行快照，持久记录失败/跳过原因；命令 `uv run pytest -q tests/agent_worker/test_schedule_trigger.py -k e02`。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_schedule_trigger.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | integration | Scheduler→真实 Console resolve→PG grants/Binding | 撤权或 Binding 软删除后不创建可执行快照，持久记录失败/跳过原因 | tests/agent_worker/test_schedule_trigger.py / e02（planned） | uv run pytest -q tests/agent_worker/test_schedule_trigger.py -k e02 | planned |
| B-115 | integration | 双 Scheduler→真实 Console resolve→PG grants/Binding/Task | 同 fire_time 一次创建；授权撤销 fail closed 并留原因；暂停/删除/修改赢得竞态后不创建；新触发快照变化而旧 Task 不变 | tests/agent_worker/test_schedule_trigger.py / B-115（planned） | uv run pytest -q tests/agent_worker/test_schedule_trigger.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-016: 落实 Misfire SKIP、ONCE 和调度审计

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-015
- **Source**: 09-task-schedule.backend.design.md#3.2.3 Schedule 触发、多副本与 Misfire, 09-task-schedule.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: E-04, B-116
- **Files**: `apps/agent-worker/src/muad_agent_worker/scheduler/service.py`, `tests/agent_worker/test_scheduler_misfire.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

补 SKIP 的持久审计和 scheduled_misfire_total；CRON 推进下一次而不补发；ONCE 仅成功创建 Task 时 COMPLETED；错过 ONCE 进入终态 MISSED（completed_at/next_fire_at 为 NULL），不补发亦不可恢复。

### Checklist

- [ ] [B-116][integration] 修改生产代码前先覆盖 Scheduler→真实 PG→审计记录/指标采集 并记录 RED：每次跳过有 schedule_id/fire_time/skipped_at；无 Task 补发；无 misfire_policy；ONCE 成功恰好一次且 completed_at 非空；错过 ONCE 置 MISSED 且 completed_at/next_fire_at 为 NULL，跳过不冒充完成。
- [ ] 实现：补 SKIP 的持久审计和 scheduled_misfire_total；CRON 推进下一次而不补发；ONCE 仅成功创建 Task 时 COMPLETED；错过 ONCE 置终态 MISSED（completed_at=NULL、next_fire_at=NULL），不补发、不提供恢复。
- [ ] [B-116][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-04][integration] 先记录 RED，再按 Scheduler→真实 PG→审计与指标采集 验证：错过触发不补发；记录 schedule_id/fire_time/skipped_at 并增加 scheduled_misfire_total；CRON 保持 ACTIVE 等下次触发，ONCE 置终态 MISSED（completed_at/next_fire_at 为 NULL）；命令 `uv run pytest -q tests/agent_worker/test_scheduler_misfire.py -k e04`。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_scheduler_misfire.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | Scheduler→真实 PG→审计/指标/终态 MISSED | 错过触发不补发；记录 schedule_id/fire_time/skipped_at 并增加 scheduled_misfire_total；CRON 保持 ACTIVE，ONCE 置 MISSED（completed_at/next_fire_at 为 NULL） | tests/agent_worker/test_scheduler_misfire.py / e04（planned） | uv run pytest -q tests/agent_worker/test_scheduler_misfire.py -k e04 | planned |
| B-116 | integration | Scheduler→真实 PG→审计记录/指标采集 | 每次跳过有 schedule_id/fire_time/skipped_at；无 Task 补发；无 misfire_policy；ONCE 成功恰好一次且 completed_at 非空；错过 ONCE 置 MISSED 且 completed_at/next_fire_at 为 NULL，跳过不冒充完成 | tests/agent_worker/test_scheduler_misfire.py / B-116（planned） | uv run pytest -q tests/agent_worker/test_scheduler_misfire.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-017: 把 deadline sweep 接入独立 Scheduler 节拍

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-013, TASK-020
- **Source**: 09-task-schedule.backend.design.md#3.2.2 Task 状态机, 09-task-schedule.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: E-03, B-117
- **Files**: `apps/agent-worker/src/muad_agent_worker/scheduler/service.py`, `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `tests/agent_worker/test_task_deadline.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

将与串行执行绑定的 sweep 拆出，Scheduler 每 30s 扫描非终态并 CAS FAILED，清租约、记录截止事件，交给持久投递循环处理。

### Checklist

- [ ] [B-117][integration] 修改生产代码前先覆盖 真实 Scheduler→PG→Gateway HTTP/Redis 并记录 RED：阻塞 Skill 不阻塞 sweep；QUEUED/RUNNING/WAITING 均失效；终态不改；TASK_DEADLINE_EXCEEDED；FINAL_ONLY 投递、NONE 不发送。
- [ ] 实现：将与串行执行绑定的 sweep 拆出，Scheduler 每 30s 扫描非终态并 CAS FAILED，清租约、记录截止事件，交给持久投递循环处理。
- [ ] [B-117][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-03][integration] 先记录 RED，再按 独立 Scheduler→真实 PG→IM Gateway HTTP/Redis 验证：30s sweep 将过期非终态 CAS FAILED(TASK_DEADLINE_EXCEEDED)，仍按 mode 投递，终态不可覆盖；命令 `uv run pytest -q tests/agent_worker/test_task_deadline.py -k e03`。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_task_deadline.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-03 | integration | 独立 Scheduler→真实 PG→IM Gateway HTTP/Redis | 30s sweep 将过期非终态 CAS FAILED(TASK_DEADLINE_EXCEEDED)，仍按 mode 投递，终态不可覆盖 | tests/agent_worker/test_task_deadline.py / e03（planned） | uv run pytest -q tests/agent_worker/test_task_deadline.py -k e03 | planned |
| B-117 | integration | 真实 Scheduler→PG→Gateway HTTP/Redis | 阻塞 Skill 不阻塞 sweep；QUEUED/RUNNING/WAITING 均失效；终态不改；TASK_DEADLINE_EXCEEDED；FINAL_ONLY 投递、NONE 不发送 | tests/agent_worker/test_task_deadline.py / B-117（planned） | uv run pytest -q tests/agent_worker/test_task_deadline.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-018: 实现 BATCH 幂等 fan-out 与并发上限

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-007, TASK-013
- **Source**: 09-task-schedule.backend.design.md#3.2.4 Fan-out / Fan-in
- **Spec-Refs**: 
- **Acceptance-Refs**: B-118
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/batch_fanout.py`, `apps/agent-worker/src/muad_agent_worker/worker/executor.py`, `tests/agent_worker/test_batch_fanout.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

新增批量分发服务并接入执行器，Child key 为 parent:{id}:{item_key}，同一事务创建；复用已创建子任务，Parent 进入 WAITING。

### Checklist

- [ ] [B-118][integration] 修改生产代码前先覆盖 真实 BATCH executor→PG Parent/Child/unique 并记录 RED：重复/并发 fan-out 不增 Child；root/intent 继承；并发≤min(plan/system/platform)；Parent 等待时无 lease；Child 默认 NONE 避免逐个主动投递。
- [ ] 实现：新增批量分发服务并接入执行器，Child key 为 parent:{id}:{item_key}，同一事务创建；复用已创建子任务，Parent 进入 WAITING。
- [ ] [B-118][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_batch_fanout.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-118 | integration | 真实 BATCH executor→PG Parent/Child/unique | 重复/并发 fan-out 不增 Child；root/intent 继承；并发≤min(plan/system/platform)；Parent 等待时无 lease；Child 默认 NONE 避免逐个主动投递 | tests/agent_worker/test_batch_fanout.py / B-118（planned） | uv run pytest -q tests/agent_worker/test_batch_fanout.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-019: 实现原子 fan-in 与 Parent 终态

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-018, TASK-014
- **Source**: 09-task-schedule.backend.design.md#3.2.4 Fan-out / Fan-in
- **Spec-Refs**: 
- **Acceptance-Refs**: B-119
- **Files**: `apps/agent-worker/src/muad_agent_worker/application/batch_fanin.py`, `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `tests/agent_worker/test_batch_fanin.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

在 Child 终态同事务检查兄弟并 CAS Parent；ALL/BEST_EFFORT 明确聚合失败及统计，reclaim 不重新 fan-out，不依赖 Redis 唤醒顺序。

### Checklist

- [ ] [B-119][integration] 修改生产代码前先覆盖 并发 Child 终态事务→真实 PG→Parent CAS 并记录 RED：未全部终态不提前完成；ALL 失败正确传播；BEST_EFFORT 记录成功/失败数；最后两个 Child 竞态只聚合一次；Parent 终态不被覆写。
- [ ] 实现：在 Child 终态同事务检查兄弟并 CAS Parent；ALL/BEST_EFFORT 明确聚合失败及统计，reclaim 不重新 fan-out，不依赖 Redis 唤醒顺序。
- [ ] [B-119][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_batch_fanin.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-119 | integration | 并发 Child 终态事务→真实 PG→Parent CAS | 未全部终态不提前完成；ALL 失败正确传播；BEST_EFFORT 记录成功/失败数；最后两个 Child 竞态只聚合一次；Parent 终态不被覆写 | tests/agent_worker/test_batch_fanin.py / B-119（planned） | uv run pytest -q tests/agent_worker/test_batch_fanin.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-020: 加固持久 Final Delivery 抢占与重试

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-013
- **Source**: 09-task-schedule.backend.design.md#3.2.5 Final Delivery, 09-task-schedule.backend.design.md#3.3.3 `task.task_execution`
- **Spec-Refs**: 
- **Acceptance-Refs**: B-120
- **Files**: `apps/agent-worker/src/muad_agent_worker/delivery/service.py`, `apps/agent-worker/src/muad_agent_worker/delivery/client.py`, `tests/agent_worker/test_delivery.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

修复选取后未持久预留导致的多副本重复尝试；持久化本次尝试及退避进度，结果提交后才调用 Gateway；最多 5 次后 FAILED，SENT/NONE 不再扫。

### Checklist

- [ ] [B-120][integration] 修改生产代码前先覆盖 双 DeliveryLoop→真实 PG→Gateway HTTP 并记录 RED：delivery_key 恒定；成功也计入尝试；重启保留退避/上限；禁止未提交结果先发；所有终态可投递；失败有审计/metric。
- [ ] 实现：修复选取后未持久预留导致的多副本重复尝试；持久化本次尝试及退避进度，结果提交后才调用 Gateway；最多 5 次后 FAILED，SENT/NONE 不再扫。
- [ ] [B-120][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_delivery.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-120 | integration | 双 DeliveryLoop→真实 PG→Gateway HTTP | delivery_key 恒定；成功也计入尝试；重启保留退避/上限；禁止未提交结果先发；所有终态可投递；失败有审计/metric | tests/agent_worker/test_delivery.py / B-120（planned） | uv run pytest -q tests/agent_worker/test_delivery.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-021: 修正 Gateway 并发投递去重和失败恢复

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-020
- **Source**: 09-task-schedule.backend.design.md#3.2.5 Final Delivery, 09-task-schedule.backend.design.md#3.3.6 Redis Key 与边界
- **Spec-Refs**: 
- **Acceptance-Refs**: E-05, B-121
- **Files**: `apps/im-gateway/src/muad_im_gateway/api/delivery.py`, `apps/im-gateway/src/muad_im_gateway/infrastructure/dedupe.py`, `tests/gateway/test_delivery_api.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

在已有 Gateway 投递端点采用真实 Redis 原子去重，移除 exists→send→mark 竞态；记录发送失败/崩溃窗口，不能让未发送的占位键永久冒充成功。

### Checklist

- [ ] [B-121][integration] 修改生产代码前先覆盖 真实 Worker HTTP→Gateway→Redis→本地渠道 HTTP 探针 并记录 RED：并发同 delivery_key 在 Redis 正常时发送一次、重复 200、TTL 7d；发送失败与占位后崩溃重启均可恢复重试且不置 SENT；Redis 故障按 at-least-once 明确可能重复、不宣称 exactly-once；最多 5 次由 Worker 约束。
- [ ] 实现：在已有 Gateway 投递端点采用真实 Redis 原子去重，移除 exists→send→mark 竞态；记录发送失败/崩溃窗口，不能让未发送的占位键永久冒充成功。
- [ ] [B-121][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-05][integration] 先记录 RED，再按 Worker HTTP→真实 Gateway→Redis→渠道探针 验证：同 key 原子去重，重复返回 200；失败恢复；退避最多 5 次后 FAILED；命令 `uv run pytest -q tests/gateway/test_delivery_api.py -k e05`。
- [ ] 执行 `uv run pytest -q tests/gateway/test_delivery_api.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-05 | integration | Worker HTTP→真实 Gateway→Redis→渠道探针 | 同 key 原子去重，重复返回 200；失败恢复；退避最多 5 次后 FAILED | tests/gateway/test_delivery_api.py / e05（planned） | uv run pytest -q tests/gateway/test_delivery_api.py -k e05 | planned |
| B-121 | integration | 真实 Worker HTTP→Gateway→Redis→本地渠道 HTTP 探针 | 并发同 delivery_key 在 Redis 正常时发送一次、重复 200、TTL 7d；发送失败与占位后崩溃重启均可恢复重试且不置 SENT；Redis 故障按 at-least-once 明确可能重复、不宣称 exactly-once；最多 5 次由 Worker 约束 | tests/gateway/test_delivery_api.py / B-121（planned） | uv run pytest -q tests/gateway/test_delivery_api.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-022: 装配 Worker/Scheduler/Delivery 生命周期

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-014, TASK-016, TASK-017, TASK-019, TASK-021
- **Source**: 09-task-schedule.backend.design.md#3.1 技术选型与关键决策, 09-task-schedule.backend.design.md#4. 部署与运维
- **Spec-Refs**: 
- **Acceptance-Refs**: B-122
- **Files**: `apps/agent-worker/src/muad_agent_worker/main.py`, `tests/agent_worker/test_worker_lifecycle.py`
  - 注：原先列出的 `apps/agent-worker/src/muad_agent_worker/api/health.py` 已由 01-platform-foundation 的 review 修复删除（探针统一走 api-kit `install_health_probes`，worker 的 `/healthz`+`/readyz` 在 `main.py` 注册）。若本任务需要新增就绪检查，改在 `main.py` 传 `readiness_checks`。
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

主入口接入真实执行器、独立调度扫描、受控后台循环和共享 api-kit 启动/探针；复用现有四部署单元，退出取消并等待后台任务。

### Checklist

- [ ] [B-122][integration] 修改生产代码前先覆盖 真实 FastAPI lifespan→后台循环→PG/Redis/NFS probes 并记录 RED：依赖未就绪显式失败；健康探针使用共享原语；执行任务不饿死调度；优雅停机无悬挂后台任务；无 Pod/用户绑定。
- [ ] 实现：主入口接入真实执行器、独立调度扫描、受控后台循环和共享 api-kit 启动/探针；复用现有四部署单元，退出取消并等待后台任务。
- [ ] [B-122][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_worker_lifecycle.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-122 | integration | 真实 FastAPI lifespan→后台循环→PG/Redis/NFS probes | 依赖未就绪显式失败；健康探针使用共享原语；执行任务不饿死调度；优雅停机无悬挂后台任务；无 Pod/用户绑定 | tests/agent_worker/test_worker_lifecycle.py / B-122（planned） | uv run pytest -q tests/agent_worker/test_worker_lifecycle.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-023: 接通 Runtime 后台任务与 Schedule Tool 客户端

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007, TASK-008, TASK-009, TASK-010, TASK-014
- **Source**: 09-task-schedule.backend.design.md#API-01 Internal 创建 Task, 09-task-schedule.backend.design.md#API-02 Internal 创建 Schedule, 09-task-schedule.backend.design.md#3.2.6 跨模块引用边界
- **Spec-Refs**: 
- **Acceptance-Refs**: B-123
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/task_client.py`, `apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py`, `tests/agent_runtime/test_task_handoff.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

在模块 08 现有 Tool/执行路由注入 Worker HTTP 客户端；覆盖 create/query/cancel Task 与 create/update/delete Schedule，传播租户、actor、trace、locale、幂等键和冻结快照。

### Checklist

- [ ] [B-123][integration] 修改生产代码前先覆盖 真实 Runtime Tool→Worker HTTP→PG 并记录 RED：ASYNC 从真实调用入口产生 Task；查询/取消可回读；创建 Schedule 重试不重复；用户不能伪造别人的 actor；不用直接写 task schema。
- [ ] 实现：在模块 08 现有 Tool/执行路由注入 Worker HTTP 客户端；覆盖 create/query/cancel Task 与 create/update/delete Schedule，传播租户、actor、trace、locale、幂等键和冻结快照。
- [ ] [B-123][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_runtime/test_task_handoff.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-123 | integration | 真实 Runtime Tool→Worker HTTP→PG | ASYNC 从真实调用入口产生 Task；查询/取消可回读；创建 Schedule 重试不重复；用户不能伪造别人的 actor；不用直接写 task schema | tests/agent_runtime/test_task_handoff.py / B-123（planned） | uv run pytest -q tests/agent_runtime/test_task_handoff.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-024: 补 Task Admin 内部路由与权限域

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-008, TASK-014
- **Source**: 09-task-schedule.backend.design.md#API-17 Worker Admin API
- **Spec-Refs**: 
- **Acceptance-Refs**: B-124
- **Files**: `apps/agent-worker/src/muad_agent_worker/api/admin_tasks.py`, `apps/agent-worker/src/muad_agent_worker/main.py`, `tests/agent_worker/test_admin_tasks.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

为 Console 暴露列表、详情和取消 Admin 路由，复用应用服务并校验内部可信身份与租户，不向浏览器开放 Worker。

### Checklist

- [ ] [B-124][integration] 修改生产代码前先覆盖 真实内部 HTTP→Admin guard→TaskService→PG 并记录 RED：缺内部身份拒绝；跨租户 404；列表/取消契约一致；取消响应不出现 CANCELLING。
- [ ] 实现：为 Console 暴露列表、详情和取消 Admin 路由，复用应用服务并校验内部可信身份与租户，不向浏览器开放 Worker。
- [ ] [B-124][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_admin_tasks.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-124 | integration | 真实内部 HTTP→Admin guard→TaskService→PG | 缺内部身份拒绝；跨租户 404；列表/取消契约一致；取消响应不出现 CANCELLING | tests/agent_worker/test_admin_tasks.py / B-124（planned） | uv run pytest -q tests/agent_worker/test_admin_tasks.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-025: 补 Schedule Admin 详情、启停与删除路由

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-010
- **Source**: 09-task-schedule.backend.design.md#API-17 Worker Admin API, 09-task-schedule.backend.design.md#API-13 Schedule 详情（Console）, 09-task-schedule.backend.design.md#API-16 删除 Schedule（Console）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-125
- **Files**: `apps/agent-worker/src/muad_agent_worker/api/admin_schedules.py`, `apps/agent-worker/src/muad_agent_worker/main.py`, `tests/agent_worker/test_admin_schedules.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

补设计清单遗漏的 GET/DELETE 单 Schedule Admin 路由，覆盖列表、详情、pause/resume/delete，复用 ScheduleService。

### Checklist

- [ ] [B-125][integration] 修改生产代码前先覆盖 真实内部 HTTP→Admin guard→ScheduleService→PG 并记录 RED：COMPLETED 列表过滤；权限和租户一致；删除幂等；启停 CAS；不存在清晰错误且不写状态。
- [ ] 实现：补设计清单遗漏的 GET/DELETE 单 Schedule Admin 路由，覆盖列表、详情、pause/resume/delete，复用 ScheduleService。
- [ ] [B-125][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/agent_worker/test_admin_schedules.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-125 | integration | 真实内部 HTTP→Admin guard→ScheduleService→PG | COMPLETED 列表过滤；权限和租户一致；删除幂等；启停 CAS；不存在清晰错误且不写状态 | tests/agent_worker/test_admin_schedules.py / B-125（planned） | uv run pytest -q tests/agent_worker/test_admin_schedules.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-026: 实现 Console 到 Worker 的有界 HTTP 客户端

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-024, TASK-025
- **Source**: 09-task-schedule.backend.design.md#3.4 接口设计
- **Spec-Refs**: 
- **Acceptance-Refs**: B-126
- **Files**: `apps/console-platform/backend/src/muad_console_platform/infrastructure/worker_client.py`, `tests/console_tasks/test_worker_client.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

共享 AsyncClient 调用 Admin API，传播已认证租户、关联头和 locale，显式 timeout/transport/envelope 错误映射；禁止无界重试。

### Checklist

- [ ] [B-126][integration] 修改生产代码前先覆盖 Console HTTP client→真实 Worker HTTP 并记录 RED：请求/响应封套和分页不变；保留业务码；超时与不可达显式处理；不把上游敏感正文泄露到 API/日志。
- [ ] 实现：共享 AsyncClient 调用 Admin API，传播已认证租户、关联头和 locale，显式 timeout/transport/envelope 错误映射；禁止无界重试。
- [ ] [B-126][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/console_tasks/test_worker_client.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-126 | integration | Console HTTP client→真实 Worker HTTP | 请求/响应封套和分页不变；保留业务码；超时与不可达显式处理；不把上游敏感正文泄露到 API/日志 | tests/console_tasks/test_worker_client.py / B-126（planned） | uv run pytest -q tests/console_tasks/test_worker_client.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-027: 接入 Console Task 列表、详情与取消 API

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-026
- **Source**: 09-task-schedule.backend.design.md#API-09 任务列表（Console）, 09-task-schedule.backend.design.md#API-10 任务详情（Console）, 09-task-schedule.backend.design.md#API-11 取消任务（Console）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-127
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/tasks.py`, `apps/console-platform/backend/src/muad_console_platform/api/router.py`, `tests/console_tasks/test_tasks_api.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

新增 /api/v1/tasks 三个入口并注册现有认证/CSRF 路由，透传验证后的过滤参数，不直连 task schema。

### Checklist

- [ ] [B-127][integration] 修改生产代码前先覆盖 真实 Console HTTP/session→Worker HTTP→PG 并记录 RED：401/403/CSRF/跨租户限制；截止时间/状态/历史过滤一致；取消冲突不伪造成功；外部封套六字段。
- [ ] 实现：新增 /api/v1/tasks 三个入口并注册现有认证/CSRF 路由，透传验证后的过滤参数，不直连 task schema。
- [ ] [B-127][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/console_tasks/test_tasks_api.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-127 | integration | 真实 Console HTTP/session→Worker HTTP→PG | 401/403/CSRF/跨租户限制；截止时间/状态/历史过滤一致；取消冲突不伪造成功；外部封套六字段 | tests/console_tasks/test_tasks_api.py / B-127（planned） | uv run pytest -q tests/console_tasks/test_tasks_api.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-028: 接入 Console Schedule 查询和管理 API

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-026
- **Source**: 09-task-schedule.backend.design.md#API-12 Schedule 列表（Console）, 09-task-schedule.backend.design.md#API-13 Schedule 详情（Console）, 09-task-schedule.backend.design.md#API-14 暂停 Schedule（Console）, 09-task-schedule.backend.design.md#API-15 恢复 Schedule（Console）, 09-task-schedule.backend.design.md#API-16 删除 Schedule（Console）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-128
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/schedules.py`, `apps/console-platform/backend/src/muad_console_platform/api/router.py`, `tests/console_tasks/test_schedules_api.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

新增 Schedule 列表、详情、pause/resume/delete 并注册；历史 Task 继续使用 tasks?schedule_id，状态改动后再返回成功。

### Checklist

- [ ] [B-128][integration] 修改生产代码前先覆盖 真实 Console HTTP→Worker Admin→PG 并记录 RED：COMPLETED 可筛选；详情存在性/权限正确；暂停失败原状态不变；删除不取消历史 Task。
- [ ] 实现：新增 Schedule 列表、详情、pause/resume/delete 并注册；历史 Task 继续使用 tasks?schedule_id，状态改动后再返回成功。
- [ ] [B-128][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/console_tasks/test_schedules_api.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-128 | integration | 真实 Console HTTP→Worker Admin→PG | COMPLETED 可筛选；详情存在性/权限正确；暂停失败原状态不变；删除不取消历史 Task | tests/console_tasks/test_schedules_api.py / B-128（planned） | uv run pytest -q tests/console_tasks/test_schedules_api.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-029: 建立前端 Task/Schedule services 与强类型 DTO

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-027, TASK-028
- **Source**: 09-task-schedule.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: 
- **Acceptance-Refs**: B-129
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/services/tasks.ts`, `apps/console-platform/frontend/src/modules/task-schedule/services/schedules.ts`, `tests/frontend/test_task_schedule_services.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

增加两个 services 文件，统一走共享 apiClient；类型覆盖列表/详情/Timeline/children/失败原因与分页；声明所有九类服务方法及截止时间参数。

### Checklist

- [ ] [B-129][integration] 修改生产代码前先覆盖 TypeScript services 编译→真实 Console HTTP 契约 并记录 RED：服务参数/响应与 OpenAPI 一致；page_size≤100；X-Locale/X-Request-Id 共享发送；无组件裸 fetch/axios。
- [ ] 实现：增加两个 services 文件，统一走共享 apiClient；类型覆盖列表/详情/Timeline/children/失败原因与分页；声明所有九类服务方法及截止时间参数。
- [ ] [B-129][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/frontend/test_task_schedule_services.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-129 | integration | TypeScript services 编译→真实 Console HTTP 契约 | 服务参数/响应与 OpenAPI 一致；page_size≤100；X-Locale/X-Request-Id 共享发送；无组件裸 fetch/axios | tests/frontend/test_task_schedule_services.py / B-129（planned） | uv run pytest -q tests/frontend/test_task_schedule_services.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-030: 补 Task/Schedule 中英文文案和帮助词条

- **Status**: draft
- **Priority**: P0
- **Depends**: 
- **Source**: 09-task-schedule.frontend.design.md#3.1 技术选型, 09-task-schedule.frontend.design.md#3.3.1 每个按钮/操作的设计, 09-task-schedule.frontend.design.md#3.7 样式方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-130
- **Files**: `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`, `tests/frontend/test_task_schedule_i18n.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

补两种语言的状态、截止时间、错误、动作、确认与帮助说明；沿已有目录和 MessageCatalog，不修改翻译框架。

### Checklist

- [ ] [B-130][unit] 修改生产代码前先覆盖 两种 locale key 集合与错误目录 并记录 RED：中英文键完全对应；无裸 schema key；FAILED/CANCELLED/COMPLETED 等文案准确；帮助不承诺 Console 编排能力。
- [ ] 实现：补两种语言的状态、截止时间、错误、动作、确认与帮助说明；沿已有目录和 MessageCatalog，不修改翻译框架。
- [ ] [B-130][unit] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/frontend/test_task_schedule_i18n.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-130 | unit | 两种 locale key 集合与错误目录 | 中英文键完全对应；无裸 schema key；FAILED/CANCELLED/COMPLETED 等文案准确；帮助不承诺 Console 编排能力 | tests/frontend/test_task_schedule_i18n.py / B-130（planned） | uv run pytest -q tests/frontend/test_task_schedule_i18n.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-031: 实现后台任务列表、筛选和路由

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-029, TASK-030, TASK-040
- **Source**: 09-task-schedule.frontend.design.md#3.2 页面与路由结构, 09-task-schedule.frontend.design.md#3.3 组件设计, 09-task-schedule.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-131
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/TaskPage.tsx`, `apps/console-platform/frontend/src/App.tsx`, `e2e/tests/task-schedule/task-list.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

用 ModuleToolbar/RemoteTable 替换 /tasks 占位页，添加状态/触发方式/截止时间筛选、分页和只读帮助；复用现有视觉骨架。

### Checklist

- [ ] [B-131][E2E] 修改生产代码前先覆盖 真实 Chrome→Console/Worker HTTP→PG→列表渲染 并记录 RED：失败原因/截止时间可见；过滤与 API 一致；左操作右筛选右下分页；loading/empty/error/retry；无 N+1 请求。
- [ ] 实现：用 ModuleToolbar/RemoteTable 替换 /tasks 占位页，添加状态/触发方式/截止时间筛选、分页和只读帮助；复用现有视觉骨架。
- [ ] [B-131][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-list.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-131 | E2E | 真实 Chrome→Console/Worker HTTP→PG→列表渲染 | 失败原因/截止时间可见；过滤与 API 一致；左操作右筛选右下分页；loading/empty/error/retry；无 N+1 请求 | e2e/tests/task-schedule/task-list.spec.ts / B-131（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-list.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-032: 实现 Task 详情、错误态与子任务展示

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-031
- **Source**: 09-task-schedule.frontend.design.md#3.3 组件设计, 09-task-schedule.frontend.design.md#3.4 组件接口契约, 09-task-schedule.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: 
- **Acceptance-Refs**: S-FE-03, E-FE-02, E-FE-03, B-132, S-203, E-202, E-203
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/TaskDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/task-schedule/TaskPage.tsx`, `e2e/tests/task-schedule/task-detail.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

主字段打开公共 SideSheet，展示截止时间、失败、投递、快照摘要和子任务；404 ErrorState 可关闭；完成前不暴露敏感快照字段。

### Checklist

- [ ] [B-132][E2E] 修改生产代码前先覆盖 真实 Browser→Task detail API→PG→SideSheet 并记录 RED：失败码和摘要/截止时间正确；子任务链接可切详情；404 可关闭；Header/Tab/DetailGrid 正确；小屏单列。
- [ ] 实现：主字段打开公共 SideSheet，展示截止时间、失败、投递、快照摘要和子任务；404 ErrorState 可关闭；完成前不暴露敏感快照字段。
- [ ] [B-132][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-FE-03][E2E] 先记录 RED，再按 Browser→tasks API→真实 PG→Task 列表/详情 验证：状态和截止时间筛选与 API 一致，失败原因和截止时间可见；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep '\''S-FE-03'\'''`。
- [ ] [E-FE-02][integration] 先记录 RED，再按 真实 Task detail API→SideSheet 验证：不存在 Task 显示 ErrorState，可关闭回列表；用浏览器执行更强的断言，保留设计 integration 标记；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep '\''E-FE-02'\'''`。
- [ ] [E-FE-03][integration] 先记录 RED，再按 真实 tasks API→详情 UI 验证：超时 Task 显示 FAILED、TASK_DEADLINE_EXCEEDED 对应原因及 deadline；不得在业务路由伪造响应；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep '\''E-FE-03'\'''`。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-203 | E2E | Browser→tasks API→真实 PG→Task 列表/详情 | 状态和截止时间筛选与 API 一致，失败原因和截止时间可见 | e2e/tests/task-schedule/task-detail.spec.ts / S-FE-03（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep '\''S-FE-03'\''' | planned |
| E-202 | integration | 真实 Task detail API→SideSheet | 不存在 Task 显示 ErrorState，可关闭回列表；用浏览器执行更强的断言，保留设计 integration 标记 | e2e/tests/task-schedule/task-detail.spec.ts / E-FE-02（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep '\''E-FE-02'\''' | planned |
| E-203 | integration | 真实 tasks API→详情 UI | 超时 Task 显示 FAILED、TASK_DEADLINE_EXCEEDED 对应原因及 deadline；不得在业务路由伪造响应 | e2e/tests/task-schedule/task-detail.spec.ts / E-FE-03（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts --grep '\''E-FE-03'\''' | planned |
| B-132 | E2E | 真实 Browser→Task detail API→PG→SideSheet | 失败码和摘要/截止时间正确；子任务链接可切详情；404 可关闭；Header/Tab/DetailGrid 正确；小屏单列 | e2e/tests/task-schedule/task-detail.spec.ts / B-132（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-detail.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-033: 实现 TaskTimeline 与有界详情刷新

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-032
- **Source**: 09-task-schedule.frontend.design.md#3.3 组件设计, 09-task-schedule.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: 
- **Acceptance-Refs**: B-133
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/TaskTimeline.tsx`, `apps/console-platform/frontend/src/modules/task-schedule/TaskDetailSideSheet.tsx`, `e2e/tests/task-schedule/task-timeline.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

独立 Timeline 展示真实 TaskEvent；详情仅可见且非终态时进行有界刷新，切换/关闭中止旧请求避免串写，不按事件逐条请求。

### Checklist

- [ ] [B-133][E2E] 修改生产代码前先覆盖 真实 Worker 事件→PG→HTTP→浏览器 Timeline 并记录 RED：seq 排序稳定；创建/等待/重试/取消/投递可见；关闭停止轮询；旧请求不覆盖新 Task；空态可理解。
- [ ] 实现：独立 Timeline 展示真实 TaskEvent；详情仅可见且非终态时进行有界刷新，切换/关闭中止旧请求避免串写，不按事件逐条请求。
- [ ] [B-133][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-timeline.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-133 | E2E | 真实 Worker 事件→PG→HTTP→浏览器 Timeline | seq 排序稳定；创建/等待/重试/取消/投递可见；关闭停止轮询；旧请求不覆盖新 Task；空态可理解 | e2e/tests/task-schedule/task-timeline.spec.ts / B-133（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-timeline.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-034: 实现取消确认及终态刷新

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-032, TASK-033, TASK-014
- **Source**: 09-task-schedule.frontend.design.md#3.3.1 每个按钮/操作的设计, 09-task-schedule.frontend.design.md#2.4 验收条件
- **Spec-Refs**: 
- **Acceptance-Refs**: S-FE-02, B-134, S-202
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/TaskDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/task-schedule/useTaskActions.ts`, `e2e/tests/task-schedule/task-cancel.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

仅 QUEUED/RUNNING/WAITING 显示 Popconfirm 取消；等待 Worker 真正终态后更新列表和详情，失败保留原状态。

### Checklist

- [ ] [B-134][E2E] 修改生产代码前先覆盖 真实 Chrome→Console cancel→Worker/PG→详情刷新 并记录 RED：确认后 RUNNING+cancel_requested 正确过渡；最终 CANCELLED 且按钮消失；取消确认前不发请求；冲突/网络失败不伪造终态。
- [ ] 实现：仅 QUEUED/RUNNING/WAITING 显示 Popconfirm 取消；等待 Worker 真正终态后更新列表和详情，失败保留原状态。
- [ ] [B-134][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-FE-02][E2E] 先记录 RED，再按 Browser→Console cancel API→真实 Worker/PG→UI 验证：取消确认后最终 CANCELLED，取消按钮消失且列表详情同步；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-cancel.spec.ts --grep '\''S-FE-02'\'''`。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-cancel.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-202 | E2E | Browser→Console cancel API→真实 Worker/PG→UI | 取消确认后最终 CANCELLED，取消按钮消失且列表详情同步 | e2e/tests/task-schedule/task-cancel.spec.ts / S-FE-02（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-cancel.spec.ts --grep '\''S-FE-02'\''' | planned |
| B-134 | E2E | 真实 Chrome→Console cancel→Worker/PG→详情刷新 | 确认后 RUNNING+cancel_requested 正确过渡；最终 CANCELLED 且按钮消失；取消确认前不发请求；冲突/网络失败不伪造终态 | e2e/tests/task-schedule/task-cancel.spec.ts / B-134（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/task-cancel.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-035: 实现定时任务列表、完成筛选和帮助

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-029, TASK-030, TASK-031, TASK-040
- **Source**: 09-task-schedule.frontend.design.md#3.2 页面与路由结构, 09-task-schedule.frontend.design.md#3.3 组件设计, 09-task-schedule.frontend.design.md#3.3.1 每个按钮/操作的设计
- **Spec-Refs**: 
- **Acceptance-Refs**: S-FE-04, B-135, S-204
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/SchedulePage.tsx`, `apps/console-platform/frontend/src/App.tsx`, `e2e/tests/task-schedule/schedule-list.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

替换 /schedules 占位页，RemoteTable 展示规则/时区/最近及下次触发，支持 ACTIVE/PAUSED/COMPLETED/MISSED 筛选（「已错过」）和 Agent 创建示例。

### Checklist

- [ ] [B-135][E2E] 修改生产代码前先覆盖 真实 Chrome→Console schedules→Worker/PG 并记录 RED：COMPLETED 筛选只返回完成调度；MISSED 筛选只返回错过触发的 ONCE；时间统一；分页/清筛选/重试与空态完整；不新增创建编排器。
- [ ] 实现：替换 /schedules 占位页，RemoteTable 展示规则/时区/最近及下次触发，支持 ACTIVE/PAUSED/COMPLETED/MISSED 筛选（「已错过」）和 Agent 创建示例。
- [ ] [B-135][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-FE-04][E2E] 先记录 RED，再按 Browser→schedules API→真实 PG 验证：已完成筛选仅 COMPLETED Schedule，「已错过」筛选仅 MISSED Schedule；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-list.spec.ts --grep '\''S-FE-04'\'''`。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-list.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-204 | E2E | Browser→schedules API→真实 PG | 已完成筛选仅 COMPLETED Schedule，「已错过」筛选仅 MISSED Schedule | e2e/tests/task-schedule/schedule-list.spec.ts / S-FE-04（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-list.spec.ts --grep '\''S-FE-04'\''' | planned |
| B-135 | E2E | 真实 Chrome→Console schedules→Worker/PG | COMPLETED 筛选只返回完成调度；MISSED 筛选只返回错过触发的 ONCE；时间统一；分页/清筛选/重试与空态完整；不新增创建编排器 | e2e/tests/task-schedule/schedule-list.spec.ts / B-135（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-list.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-036: 实现 Schedule 详情基本信息与操作位置

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-035
- **Source**: 09-task-schedule.frontend.design.md#3.3 组件设计, 09-task-schedule.frontend.design.md#3.4 组件接口契约, 09-task-schedule.frontend.design.md#3.7 样式方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-136
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/ScheduleDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/task-schedule/SchedulePage.tsx`, `e2e/tests/task-schedule/schedule-detail.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

名称打开公共 SideSheet，基础信息含 CRON/ONCE、timezone、revision、completed_at；提供正确 Header actions/Tab 布局和关闭焦点恢复。

### Checklist

- [ ] [B-136][E2E] 修改生产代码前先覆盖 真实 Chrome→Schedule detail HTTP→PG→SideSheet 并记录 RED：字段与后台一致；ONCE 无 next_fire 时正确显示；无裸 key；Header 操作与 X 同行；loading/error 可退出。
- [ ] 实现：名称打开公共 SideSheet，基础信息含 CRON/ONCE、timezone、revision、completed_at；提供正确 Header actions/Tab 布局和关闭焦点恢复。
- [ ] [B-136][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-detail.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-136 | E2E | 真实 Chrome→Schedule detail HTTP→PG→SideSheet | 字段与后台一致；ONCE 无 next_fire 时正确显示；无裸 key；Header 操作与 X 同行；loading/error 可退出 | e2e/tests/task-schedule/schedule-detail.spec.ts / B-136（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-detail.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-037: 实现 Schedule 历史与 Task 详情跳转

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-036, TASK-032
- **Source**: 09-task-schedule.frontend.design.md#3.3 组件设计, 09-task-schedule.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-FE-01, B-137, RULE-ui-detail-001, S-201, B-214
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/ScheduleHistoryTable.tsx`, `apps/console-platform/frontend/src/modules/task-schedule/ScheduleDetailSideSheet.tsx`, `e2e/tests/task-schedule/schedule-history.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

新增按 schedule_id 的独立历史分页 Tab，复用 Task 详情；切换 Schedule 清理旧请求和页码。

### Checklist

- [ ] [B-137][E2E] 修改生产代码前先覆盖 真实 Browser→schedules API→tasks API→PG→详情 并记录 RED：历史只含当前 schedule_id；跨租户不可见；分页不混入其他调度；Task ID 打开真实详情；标题/副标题/操作/Tab 符合规范。
- [ ] 实现：新增按 schedule_id 的独立历史分页 Tab，复用 Task 详情；切换 Schedule 清理旧请求和页码。
- [ ] [B-137][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-FE-01][E2E] 先记录 RED，再按 Browser→schedules API→tasks API→真实 PG 验证：历史仅当前 schedule_id，分页准确且 Task 链接可打开详情；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts --grep '\''S-FE-01'\'''`。
- [ ] [RULE-ui-detail-001][E2E] verifier_ref=`harness-ui-detail#RULE-ui-detail-001`；继承原 verifier `bash -lc 'uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck'`；结合本模块 B-137 的真实输入验证：SideSheet 标题/副标题左，actions/X 同行右，Tabs 在下，详情与关系结果刷新可见。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-201 | E2E | Browser→schedules API→tasks API→真实 PG | 历史仅当前 schedule_id，分页准确且 Task 链接可打开详情 | e2e/tests/task-schedule/schedule-history.spec.ts / S-FE-01（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts --grep '\''S-FE-01'\''' | planned |
| B-137 | E2E | 真实 Browser→schedules API→tasks API→PG→详情 | 历史只含当前 schedule_id；跨租户不可见；分页不混入其他调度；Task ID 打开真实详情；标题/副标题/操作/Tab 符合规范 | e2e/tests/task-schedule/schedule-history.spec.ts / B-137（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts' | planned |
| B-214 | E2E | 真实 Browser→schedules API→tasks API→PG→详情 | SideSheet 标题/副标题左，actions/X 同行右，Tabs 在下，详情与关系结果刷新可见 | e2e/tests/task-schedule/schedule-history.spec.ts + 原 Spec verifier / RULE-ui-detail-001（planned） | bash -lc 'bash -lc '\''npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-history.spec.ts'\'' && bash -lc '\''uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck'\''' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-038: 实现 Schedule 暂停、恢复与删除

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-036, TASK-010
- **Source**: 09-task-schedule.frontend.design.md#3.3.1 每个按钮/操作的设计, 09-task-schedule.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: 
- **Acceptance-Refs**: E-FE-01, B-138, E-201
- **Files**: `apps/console-platform/frontend/src/modules/task-schedule/ScheduleDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/task-schedule/useScheduleActions.ts`, `e2e/tests/task-schedule/schedule-actions.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

按状态展示动作，删除需二次确认；仅服务成功后刷新，按钮独立 loading，错误保持当前 Tab/数据；COMPLETED/MISSED 为终态，不显示暂停/恢复（MISSED 需重新创建 Schedule）。

### Checklist

- [ ] [B-138][E2E] 修改生产代码前先覆盖 真实 Chrome→管理 API→Worker PG→UI 并记录 RED：暂停失败仍 ACTIVE；成功暂停不再触发；恢复不补发；删除不影响历史 Task；COMPLETED/MISSED 动作受限（无暂停/恢复）；失败可重试。
- [ ] 实现：按状态展示动作，删除需二次确认；仅服务成功后刷新，按钮独立 loading，错误保持当前 Tab/数据；COMPLETED/MISSED 为终态，不显示暂停/恢复（MISSED 需重新创建 Schedule）。
- [ ] [B-138][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [E-FE-01][E2E] 先记录 RED，再按 真实 pause API 失败→Browser UI 验证：由真实后端状态/不可达故障触发失败，原 ACTIVE 状态保留，无错误乐观更新；命令 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-actions.spec.ts --grep '\''E-FE-01'\'''`。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-actions.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-201 | E2E | 真实 pause API 失败→Browser UI | 由真实后端状态/不可达故障触发失败，原 ACTIVE 状态保留，无错误乐观更新 | e2e/tests/task-schedule/schedule-actions.spec.ts / E-FE-01（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-actions.spec.ts --grep '\''E-FE-01'\''' | planned |
| B-138 | E2E | 真实 Chrome→管理 API→Worker PG→UI | 暂停失败仍 ACTIVE；成功暂停不再触发；恢复不补发；删除不影响历史 Task；COMPLETED/MISSED 动作受限（无暂停/恢复）；失败可重试 | e2e/tests/task-schedule/schedule-actions.spec.ts / B-138（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/schedule-actions.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-039: 建立后端真实验收环境与数据清理

- **Status**: draft
- **Priority**: P0
- **Depends**: 
- **Source**: 09-task-schedule.backend.design.md#2.5.2 功能验收场景, 09-task-schedule.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: 
- **Acceptance-Refs**: B-139
- **Files**: `tests/acceptance/task_schedule/conftest.py`, `tests/acceptance/task_schedule/environment.py`, `tests/acceptance/task_schedule/test_environment.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

建立真实 PG/Redis/NFS、双 Worker、Console/Runtime/Gateway HTTP 进程和渠道探针 fixture；测试数据 e2e- 前缀并按依赖清理；缺依赖 fail 而不是 skip 后冒充通过。

### Checklist

- [ ] [B-139][integration] 修改生产代码前先覆盖 真实服务进程/PG/Redis/NFS 挂载健康探针 并记录 RED：进程可访问；真实共享挂载可验证；fixture 不覆盖业务路由；测试结束 Task/Event/Schedule/Route/Submission 与探针数据清理。
- [ ] 实现：建立真实 PG/Redis/NFS、双 Worker、Console/Runtime/Gateway HTTP 进程和渠道探针 fixture；测试数据 e2e- 前缀并按依赖清理；缺依赖 fail 而不是 skip 后冒充通过。
- [ ] [B-139][integration] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `uv run pytest -q tests/acceptance/task_schedule/test_environment.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-139 | integration | 真实服务进程/PG/Redis/NFS 挂载健康探针 | 进程可访问；真实共享挂载可验证；fixture 不覆盖业务路由；测试结束 Task/Event/Schedule/Route/Submission 与探针数据清理 | tests/acceptance/task_schedule/test_environment.py / B-139（planned） | uv run pytest -q tests/acceptance/task_schedule/test_environment.py | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-040: 建立任务管理真实浏览器测试栈

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-027, TASK-028, TASK-039
- **Source**: 09-task-schedule.frontend.design.md#2.4 验收条件, 09-task-schedule.frontend.design.md#3.1 技术选型
- **Spec-Refs**: 
- **Acceptance-Refs**: B-140
- **Files**: `e2e/playwright.task-schedule.config.ts`, `tests/e2e/app.py`, `e2e/tests/task-schedule/environment.spec.ts`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

复用 tests/e2e/app.py 装载生产 Task/Schedule 路由与真实 Worker 客户端；Playwright 使用真实构建物和系统 Chrome，种子/清理由 TASK-039 的环境设施提供；先验证 Shell 和真实业务端点，再由页面任务扩展 UI 验收。

### Checklist

- [ ] [B-140][E2E] 修改生产代码前先覆盖 真实 Chrome→静态 build→生产 Console 路由→Worker/PG 并记录 RED：启动健康/认证/业务端点可达；业务 API 不 route.fulfill/mock；种子隔离且清理；异常由真实服务状态触发。
- [ ] 实现：复用 tests/e2e/app.py 装载生产 Task/Schedule 路由与真实 Worker 客户端；Playwright 使用真实构建物和系统 Chrome，种子/清理由 TASK-039 的环境设施提供；先验证 Shell 和真实业务端点，再由页面任务扩展 UI 验收。
- [ ] [B-140][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/environment.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-140 | E2E | 真实 Chrome→静态 build→生产 Console 路由→Worker/PG | 启动健康/认证/业务端点可达；业务 API 不 route.fulfill/mock；种子隔离且清理；异常由真实服务状态触发 | e2e/tests/task-schedule/environment.spec.ts / B-140（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/environment.spec.ts' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-041: 验收 Runtime→Worker 执行及恢复全链路

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-022, TASK-023, TASK-039
- **Source**: 09-task-schedule.backend.design.md#2.5.2 功能验收场景, 09-task-schedule.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-api#RULE-api-002, harness-api#RULE-api-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-skill#RULE-skill-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: S-01, B-141, RULE-api-002, RULE-api-001, RULE-arch-001, RULE-secret-001, RULE-skill-001, RULE-worker-001, B-201, B-202, B-204, B-210, B-211, B-215
- **Files**: `tests/acceptance/task_schedule/test_execution.py`, `tests/acceptance/task_schedule/test_recovery.py`, `tests/acceptance/task_schedule/test_idempotency.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

从真实 Runtime Tool 提交开始验收任意 Worker 执行、失约接管、幂等重放、秘密隔离、真实 cache 和 deadline；负责 Worker 自动 verifier 替代方案的闭环，原 manual 不代签。

### Checklist

- [ ] [B-141][E2E] 修改生产代码前先覆盖 Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 并记录 RED：QUEUED 到 COMPLETED；双 Worker 故障接管；同键异指纹冲突；Redis 关闭任务不丢；Snapshot/日志/审计/API 无秘密；失约不能写终态。
- [ ] 实现：从真实 Runtime Tool 提交开始验收任意 Worker 执行、失约接管、幂等重放、秘密隔离、真实 cache 和 deadline；负责 Worker 自动 verifier 替代方案的闭环，原 manual 不代签。
- [ ] [B-141][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-01][E2E] 先记录 RED，再按 Runtime→Worker API→PG→Worker 验证：Runtime 实际提交 ASYNC Skill，任意 Worker claim 后 COMPLETED，真实 Skill 副作用只发生一次；命令 `uv run pytest -q tests/acceptance/task_schedule/test_execution.py -k s01`。
- [ ] [RULE-api-002][E2E] verifier_ref=`harness-api#RULE-api-002`；继承原 verifier `uv run pytest -q tests/console_skill/test_import_idempotency.py`；结合本模块 B-141 的真实输入验证：真实创建 Task/Schedule POST 同 tenant/key/endpoint 同指纹重放 200 首次结果，异指纹冲突，事务失败不留成功记录。
- [ ] [RULE-api-001][E2E] verifier_ref=`harness-api#RULE-api-001`；继承原 verifier `uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`；结合本模块 B-141 的真实输入验证：公开/内部真实 HTTP 响应封套与分页一致，错误码来自配置，业务异常不拼接文案。
- [ ] [RULE-arch-001][E2E] verifier_ref=`harness-arch#RULE-arch-001`；继承原 verifier `uv run pytest -q tests/architecture`；结合本模块 B-141 的真实输入验证：仅四部署单元；双 Worker 可接管相同 Task，无 bot/actor→Pod 映射。
- [ ] [RULE-secret-001][E2E] verifier_ref=`harness-secret#RULE-secret-001`；继承原 verifier `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`；结合本模块 B-141 的真实输入验证：Owner 表按主键读取凭据，Task Snapshot/Event/审计/日志/LLM/IM/外部 API 不含秘密；不恢复 SecretRef/SecretProvider。
- [ ] [RULE-skill-001][E2E] verifier_ref=`harness-skill#RULE-skill-001`；继承原 verifier `uv run pytest -q tests/test_skill_artifact_cache.py`；结合本模块 B-141 的真实输入验证：NFS 相对 storage_key 不可变；checksum 校验+singleflight+本地 READY 执行；保留既有原子写、DB 失败清理和孤儿宽限期回归。
- [ ] [RULE-worker-001][E2E] verifier_ref=`harness-worker#RULE-worker-001`；原 verifier 是 manual，须先按 N-03 修订为 command，不得代签；拟用 `uv run pytest -q tests/agent_worker tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py`；结合本模块 B-141 的真实输入验证：真实 PG 是唯一权威；Redis 故障仍可推进；SKILL/BATCH；SKIP LOCKED；WAITING 释放 lease；30s Scheduler sweep。
- [ ] 执行 `uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Runtime→Worker API→PG→Worker | Runtime 实际提交 ASYNC Skill，任意 Worker claim 后 COMPLETED，真实 Skill 副作用只发生一次 | tests/acceptance/task_schedule/test_execution.py / s01（planned） | uv run pytest -q tests/acceptance/task_schedule/test_execution.py -k s01 | planned |
| B-141 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | QUEUED 到 COMPLETED；双 Worker 故障接管；同键异指纹冲突；Redis 关闭任务不丢；Snapshot/日志/审计/API 无秘密；失约不能写终态 | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py / B-141（planned） | uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py | planned |
| B-201 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | 真实创建 Task/Schedule POST 同 tenant/key/endpoint 同指纹重放 200 首次结果，异指纹冲突，事务失败不留成功记录 | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py + 原 Spec verifier / RULE-api-002（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/console_skill/test_import_idempotency.py' | planned |
| B-202 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | 公开/内部真实 HTTP 响应封套与分页一致，错误码来自配置，业务异常不拼接文案 | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py + 原 Spec verifier / RULE-api-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py' | planned |
| B-204 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | 仅四部署单元；双 Worker 可接管相同 Task，无 bot/actor→Pod 映射 | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py + 原 Spec verifier / RULE-arch-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/architecture' | planned |
| B-210 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | Owner 表按主键读取凭据，Task Snapshot/Event/审计/日志/LLM/IM/外部 API 不含秘密；不恢复 SecretRef/SecretProvider | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py + 原 Spec verifier / RULE-secret-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py' | planned |
| B-211 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | NFS 相对 storage_key 不可变；checksum 校验+singleflight+本地 READY 执行；保留既有原子写、DB 失败清理和孤儿宽限期回归 | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py + 原 Spec verifier / RULE-skill-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/test_skill_artifact_cache.py' | planned |
| B-215 | E2E | Runtime HTTP→Worker HTTP→真实 PG/Redis/NFS→Skill/渠道探针 | 真实 PG 是唯一权威；Redis 故障仍可推进；SKILL/BATCH；SKIP LOCKED；WAITING 释放 lease；30s Scheduler sweep | tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py + 原 Spec verifier / RULE-worker-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py tests/acceptance/task_schedule/test_idempotency.py && uv run pytest -q tests/agent_worker tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_recovery.py' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-042: 验收调度授权、Snapshot 与多副本竞态

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-016, TASK-022, TASK-039
- **Source**: 09-task-schedule.backend.design.md#2.5.2 功能验收场景, 09-task-schedule.backend.design.md#3.2.3 Schedule 触发、多副本与 Misfire, 09-task-schedule.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-02, B-142, RULE-snapshot-001, B-212
- **Files**: `tests/acceptance/task_schedule/test_schedules.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

以真实 Console 授权/Artifact 更新驱动 Scheduler；覆盖新快照、权限撤销、稳定 skill_id、ONCE、多副本和更新/暂停/删除竞态。

### Checklist

- [ ] [B-142][E2E] 修改生产代码前先覆盖 双 Scheduler→真实 Console resolve/grants/Artifact→PG→Worker 并记录 RED：新触发采当前版本；旧快照不变；撤权不创建；同 fire_time 一次；ONCE 成功完成且错过不补发；revision 竞态不运行失效配置。
- [ ] 实现：以真实 Console 授权/Artifact 更新驱动 Scheduler；覆盖新快照、权限撤销、稳定 skill_id、ONCE、多副本和更新/暂停/删除竞态。
- [ ] [B-142][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-02][E2E] 先记录 RED，再按 Scheduler→真实 current grants/Binding/artifact→Task DB 验证：CRON 到期重新鉴权及生成新快照，双 Scheduler 同 fire_time 仅一 Task，旧 Task 快照保持不变；命令 `uv run pytest -q tests/acceptance/task_schedule/test_schedules.py -k s02`。
- [ ] [RULE-snapshot-001][E2E] verifier_ref=`harness-snapshot#RULE-snapshot-001`；继承原 verifier `bash -lc 'uv run pytest -q tests/agent_runtime -k "executor or resolve"'`；结合本模块 B-142 的真实输入验证：Task 前冻结 Agent/Model/Skill/MCP/Prompt/catalog/budget，配置/授权更新只影响新触发，所有终态 CAS。
- [ ] 执行 `uv run pytest -q tests/acceptance/task_schedule/test_schedules.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | E2E | Scheduler→真实 current grants/Binding/artifact→Task DB | CRON 到期重新鉴权及生成新快照，双 Scheduler 同 fire_time 仅一 Task，旧 Task 快照保持不变 | tests/acceptance/task_schedule/test_schedules.py / s02（planned） | uv run pytest -q tests/acceptance/task_schedule/test_schedules.py -k s02 | planned |
| B-142 | E2E | 双 Scheduler→真实 Console resolve/grants/Artifact→PG→Worker | 新触发采当前版本；旧快照不变；撤权不创建；同 fire_time 一次；ONCE 成功完成且错过不补发；revision 竞态不运行失效配置 | tests/acceptance/task_schedule/test_schedules.py / B-142（planned） | uv run pytest -q tests/acceptance/task_schedule/test_schedules.py | planned |
| B-212 | E2E | 双 Scheduler→真实 Console resolve/grants/Artifact→PG→Worker | Task 前冻结 Agent/Model/Skill/MCP/Prompt/catalog/budget，配置/授权更新只影响新触发，所有终态 CAS | tests/acceptance/task_schedule/test_schedules.py + 原 Spec verifier / RULE-snapshot-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_schedules.py && bash -lc '\''uv run pytest -q tests/agent_runtime -k "executor or resolve"'\''' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-043: 验收批量聚合和最终投递

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-019, TASK-021, TASK-022, TASK-039
- **Source**: 09-task-schedule.backend.design.md#2.5.2 功能验收场景, 09-task-schedule.backend.design.md#3.2.4 Fan-out / Fan-in, 09-task-schedule.backend.design.md#3.2.5 Final Delivery, 09-task-schedule.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-im#RULE-im-001
- **Acceptance-Refs**: S-03, S-04, B-143, RULE-im-001, B-209
- **Files**: `tests/acceptance/task_schedule/test_batch.py`, `tests/acceptance/task_schedule/test_delivery.py`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

用真实 Worker、Gateway、Redis 和可观测渠道探针验收 fan-out/in 与 FINAL_ONLY；覆盖多 bot 路由、同键并发、外部失败和重启恢复。

### Checklist

- [ ] [B-143][E2E] 修改生产代码前先覆盖 Worker→PG Parent/Child→Gateway HTTP→真实 Redis→渠道 HTTP 探针 并记录 RED：Child 不重复；ALL/BEST_EFFORT 正确；Parent 一次最终结果；SENT/attempts/delivered_at 持久；bot 无 Pod 绑定；Redis 故障只承诺 at-least-once。
- [ ] 实现：用真实 Worker、Gateway、Redis 和可观测渠道探针验收 fan-out/in 与 FINAL_ONLY；覆盖多 bot 路由、同键并发、外部失败和重启恢复。
- [ ] [B-143][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [S-03][E2E] 先记录 RED，再按 Worker→真实 PG→IM Gateway HTTP/Redis→渠道探针 验证：FINAL_ONLY 结果先落库再投递，SENT/delivered_at 可回读，NONE 无发送；命令 `uv run pytest -q tests/acceptance/task_schedule/test_delivery.py -k s03`。
- [ ] [S-04][E2E] 先记录 RED，再按 Worker→真实 Parent/Child PG→IM Gateway 验证：Child 幂等创建；ALL/BEST_EFFORT 聚合正确；所有 Child 终态后 Parent CAS，最终结果按 mode 投递；命令 `uv run pytest -q tests/acceptance/task_schedule/test_batch.py -k s04`。
- [ ] [RULE-im-001][E2E] verifier_ref=`harness-im#RULE-im-001`；继承原 verifier `uv run pytest -q tests/console_channel tests/gateway`；结合本模块 B-143 的真实输入验证：一个 Agent 0..N bot，bot 唯一 Agent；路由只有 bot/接收方元数据；不同 Worker 都能投递，无 Pod 映射。
- [ ] 执行 `uv run pytest -q tests/acceptance/task_schedule/test_batch.py tests/acceptance/task_schedule/test_delivery.py`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | E2E | Worker→真实 PG→IM Gateway HTTP/Redis→渠道探针 | FINAL_ONLY 结果先落库再投递，SENT/delivered_at 可回读，NONE 无发送 | tests/acceptance/task_schedule/test_delivery.py / s03（planned） | uv run pytest -q tests/acceptance/task_schedule/test_delivery.py -k s03 | planned |
| S-04 | E2E | Worker→真实 Parent/Child PG→IM Gateway | Child 幂等创建；ALL/BEST_EFFORT 聚合正确；所有 Child 终态后 Parent CAS，最终结果按 mode 投递 | tests/acceptance/task_schedule/test_batch.py / s04（planned） | uv run pytest -q tests/acceptance/task_schedule/test_batch.py -k s04 | planned |
| B-143 | E2E | Worker→PG Parent/Child→Gateway HTTP→真实 Redis→渠道 HTTP 探针 | Child 不重复；ALL/BEST_EFFORT 正确；Parent 一次最终结果；SENT/attempts/delivered_at 持久；bot 无 Pod 绑定；Redis 故障只承诺 at-least-once | tests/acceptance/task_schedule/test_batch.py tests/acceptance/task_schedule/test_delivery.py / B-143（planned） | uv run pytest -q tests/acceptance/task_schedule/test_batch.py tests/acceptance/task_schedule/test_delivery.py | planned |
| B-209 | E2E | Worker→PG Parent/Child→Gateway HTTP→真实 Redis→渠道 HTTP 探针 | 一个 Agent 0..N bot，bot 唯一 Agent；路由只有 bot/接收方元数据；不同 Worker 都能投递，无 Pod 映射 | tests/acceptance/task_schedule/test_batch.py tests/acceptance/task_schedule/test_delivery.py + 原 Spec verifier / RULE-im-001（planned） | bash -lc 'uv run pytest -q tests/acceptance/task_schedule/test_batch.py tests/acceptance/task_schedule/test_delivery.py && uv run pytest -q tests/console_channel tests/gateway' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)

---

## TASK-044: 完成跨语言、时间与全部验收收口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011, TASK-012, TASK-013, TASK-014, TASK-015, TASK-016, TASK-017, TASK-018, TASK-019, TASK-020, TASK-021, TASK-022, TASK-023, TASK-024, TASK-025, TASK-026, TASK-027, TASK-028, TASK-029, TASK-030, TASK-031, TASK-032, TASK-033, TASK-034, TASK-035, TASK-036, TASK-037, TASK-038, TASK-039, TASK-040, TASK-041, TASK-042, TASK-043
- **Source**: 09-task-schedule.backend.design.md#Spec Compliance Matrix, 09-task-schedule.frontend.design.md#Spec Compliance Matrix, 09-task-schedule.frontend.design.md#2.4 验收条件
- **Spec-Refs**: harness-test#RULE-test-001, harness-ui#RULE-ui-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-time#RULE-time-001
- **Acceptance-Refs**: B-144, RULE-test-001, RULE-ui-001, RULE-front-001, RULE-i18n-001, RULE-time-001, B-203, B-206, B-207, B-208, B-213
- **Files**: `e2e/tests/task-schedule/locale-time.spec.ts`, `tests/acceptance/task_schedule/test_acceptance_environment.py`, `09-task-schedule.md`
- **Estimate**: 15–60 分钟；预计超过则先拆分

### Description

运行原 Spec verifiers 与本模块全部验收，补两语言/时区浏览器证据；核对每个场景唯一负责人、真实边界、RED/GREEN 和清理记录。

### Checklist

- [ ] [B-144][E2E] 修改生产代码前先覆盖 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI 并记录 RED：zh-CN/en-US 标签和后端错误一致；IANA 调度与显示 YYYY-MM-DD HH:mm:ss 正确；零漏验/无未说明 skip/无残留测试数据。
- [ ] 实现：运行原 Spec verifiers 与本模块全部验收，补两语言/时区浏览器证据；核对每个场景唯一负责人、真实边界、RED/GREEN 和清理记录。
- [ ] [B-144][E2E] 在上述真实边界复核关键断言；已有正确行为保留，禁止仅为制造 RED 改坏实现。
- [ ] [RULE-test-001][E2E] verifier_ref=`harness-test#RULE-test-001`；继承原 verifier `bash -lc 'uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test'`；结合本模块 B-144 的真实输入验证：原 17 场景与新增边界全覆盖，真实 PG/Redis/HTTP/浏览器/NFS，无业务 mock、无未说明 skip，清理证据完整。
- [ ] [RULE-i18n-001][E2E] verifier_ref=`harness-i18n#RULE-i18n-001`；继承原 verifier `bash -lc 'uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py'`；结合本模块 B-144 的真实输入验证：真实浏览器两种语言显示正确；X-Locale/Accept-Language 协商后后端错误与页面匹配，只增加业务词条。
- [ ] [RULE-time-001][E2E] verifier_ref=`harness-time#RULE-time-001`；继承原 verifier `bash -lc 'uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity'`；结合本模块 B-144 的真实输入验证：PG timestamptz、IANA 调度、Console YYYY-MM-DD HH:mm:ss 三端一致且覆盖 DST/跨日。
- [ ] 执行 `bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts'`，填写 Acceptance Evidence 的 RED/GREEN、每个关键断言位置、真实组件、清理证据与未通过项；全部 verified 后才可 done。

- [ ] [RULE-ui-001][E2E] verifier_ref=`harness-ui#RULE-ui-001`；继承原 verifier `bash -lc 'uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build'`；结合本模块 B-144 的真实输入验证：React/Semi 公共骨架，左操作右筛选右下分页，固定十项菜单，主字段进入详情。
- [ ] [RULE-front-001][E2E] verifier_ref=`harness-frontend#RULE-front-001`；继承原 verifier `bash -lc 'uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck'`；结合本模块 B-144 的真实输入验证：API 只经 services/共享 client，组件无裸 axios/fetch，所有文案 i18n key，列表与详情规范一致。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-144 | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | zh-CN/en-US 标签和后端错误一致；IANA 调度与显示 YYYY-MM-DD HH:mm:ss 正确；零漏验/无未说明 skip/无残留测试数据 | e2e/tests/task-schedule/locale-time.spec.ts / B-144（planned） | bash -lc 'npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts' | planned |
| B-203 | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | 原 17 场景与新增边界全覆盖，真实 PG/Redis/HTTP/浏览器/NFS，无业务 mock、无未说明 skip，清理证据完整 | e2e/tests/task-schedule/locale-time.spec.ts + 原 Spec verifier / RULE-test-001（planned） | bash -lc 'bash -lc '\''npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts'\'' && bash -lc '\''uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test'\''' | planned |
| B-208 | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | 真实浏览器两种语言显示正确；X-Locale/Accept-Language 协商后后端错误与页面匹配，只增加业务词条 | e2e/tests/task-schedule/locale-time.spec.ts + 原 Spec verifier / RULE-i18n-001（planned） | bash -lc 'bash -lc '\''npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts'\'' && bash -lc '\''uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py'\''' | planned |
| B-213 | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | PG timestamptz、IANA 调度、Console YYYY-MM-DD HH:mm:ss 三端一致且覆盖 DST/跨日 | e2e/tests/task-schedule/locale-time.spec.ts + 原 Spec verifier / RULE-time-001（planned） | bash -lc 'bash -lc '\''npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts'\'' && bash -lc '\''uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity'\''' | planned |

| B-206 | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | React/Semi 公共骨架，左操作右筛选右下分页，固定十项菜单，主字段进入详情 | e2e/tests/task-schedule/locale-time.spec.ts + 原 Spec verifier / RULE-ui-001（planned） | bash -lc 'bash -lc '\''npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts'\'' && bash -lc '\''uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build'\''' | planned |
| B-207 | E2E | 真实 Chrome/X-Locale→Console/Worker HTTP→PG timestamptz→UI | API 只经 services/共享 client，组件无裸 axios/fetch，所有文案 i18n key，列表与详情规范一致 | e2e/tests/task-schedule/locale-time.spec.ts + 原 Spec verifier / RULE-front-001（planned） | bash -lc 'bash -lc '\''npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.task-schedule.config.ts tests/task-schedule/locale-time.spec.ts'\'' && bash -lc '\''uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck'\''' | planned |

### Acceptance Evidence

> planned。编码时填写 RED/GREEN 执行记录、断言文件/用例/行号、真实组件与测试数据清理证据；不把当前规划结构检查当作功能验收结果。

### Log

- [2026-09-20] prepared (draft，待审阅与设计缺口解决)
