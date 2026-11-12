# Tasks: 单次执行链路分析包导出

- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/`（backend/frontend design + scenarios 合并）
- **Created**: 2026-11-09
- **Updated**: 2026-11-09

## Proposal

为单条 Run/Task 提供 ADMIN 专属的「导出链路」分析包：在同一只读快照内采集执行主体、全量事件、模型/工具/Egress 审计、Operation、Task 树和补充段，生成带失败诊断、服务归属、日志检索键的 ZIP/JSON。诊断字段原样保留，仅凭据字段掩码；成功/失败同结构，用于单点离线排障和样本对比。现有审计列表导出保持不变。

## Execution Contract

- 每个 TASK 是 1–2 天可独立演示能力；编码前先写局部行为测试并记录 RED，再实现并记录 GREEN。
- 单 worktree 一次只激活一个 TASK；无依赖且文件不相交的任务可按 `cf-task-start` 批次并行。
- E2E 只编写/登记，编码期不执行；统一由 `verify-e2e` 终验。
- 每个 required Rule 只有一个最终责任 TASK；其他任务通过 `Spec-Refs` 引用，不重复认领。
- 验收测试使用隔离验收库与独立 Redis 槽；pytest 前先停 dev 服务，避免 `--reload` Worker 抢任务。
- 诊断字段原样、凭据字段掩码；任何新增字段先确认是否命中凭据键名/模式。

## Alignment

- **Scope**: Run/Task 单条执行导出；全量非 delta 事件；Task 事件与子任务；模型/工具/Egress/Operation；补充段；失败诊断；服务归属；日志键；ZIP/JSON；ADMIN 专属；一致快照；有界截断。
- **Decisions**:
  - 同步 GET 下载，不建导出任务表/不写 artifact store。
  - 诊断字段原样，仅凭据键名/模式掩码；不做逐字段字符截断。
  - 仅 ADMIN 可导出；服务端 `AdminAccount` 强制，前端 `useAuth` 隐藏入口。
  - 核心段失败返回 500；可选段失败返回 200 + `warnings` + `data_quality.complete=false`。
  - 新增 `task.task_execution (tenant_id, source_run_id)` partial index（迁移 0043）。
- **Non-goals**: 批量/时间范围导出；日志正文；跨执行聚合；真正 span 树；原始全行/软删除行导出；修改 Run/Task 状态机；现有审计导出改造。
- **Acceptance**: 13 个场景（S-01..S-05、E-01..E-05、B-01..B-03）；E2E 1 条（S-05，smoke，真实浏览器 + 真实 Console/PG）。

## Task Index

| 任务 | 阶段 | 能力 | 优先级 | 预估 | 依赖 | 场景数 |
|---|---|---|---|---|---|---|
| TASK-001 | P1 | 导出仓储在只读一致快照内按 Run/Task 采集核心段+补充段，可选段失败不中止，任务查询命中新索引 | P0 | 1.5d | - | 4 |
| TASK-002 | P1 | 生成失败诊断、服务归属、日志键、summary/timing/insights 与报告视图；成功不伪造失败 | P0 | 1.5d | TASK-001 | 3 |
| TASK-003 | P2 | 报告渲染为 JSON/Markdown/CSV/ZIP；诊断字段原样、凭据掩码、截断显式 | P0 | 1.5d | TASK-002 | 2 |
| TASK-004 | P2 | ADMIN 通过 GET 下载 Run/Task 分析包；404/403/400/500 与可选段降级符合契约 | P0 | 1.5d | TASK-001, TASK-002, TASK-003 | 5 |
| TASK-005 | P2 | ADMIN 在 Run/Task 详情点击「导出链路」下载 ZIP；loading/404/网络失败/重试；非 ADMIN 不渲染 | P0 | 1.5d | TASK-004 | 1 |
| TASK-006 | P3 | 真实浏览器完成「打开失败 Run → 导出链路 → 下载 ZIP → 解包校验」；全量 functional 通过 | P0 | 1.5d | TASK-005 | 1 |

## Plan Compliance Matrix

| Rule | Owner TASK | Verifier |
|---|---|---|
| `harness-api#RULE-api-001` | TASK-004 | `uv run pytest -q tests/console_platform/test_execution_export_api.py tests/contracts/test_execution_export_api_contract.py` |
| `harness-api#RULE-api-002` | TASK-004 | `uv run pytest -q tests/contracts/test_execution_export_api_contract.py -k idempotency`（验证只读 GET 不消费 Idempotency-Key；设计决策为 N/A） |
| `harness-arch#RULE-arch-001` | TASK-004 | `uv run pytest -q tests/contracts/test_execution_export_api_contract.py -k deployment_unit` |
| `harness-data#RULE-data-001` | TASK-001 | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata tests/migrations/test_execution_export_index.py` |
| `harness-e2e#RULE-e2e-001` | TASK-006 | `e2e/node_modules/.bin/playwright test -c e2e/playwright.execution-export.config.ts e2e/tests/execution-export.spec.ts` |
| `harness-frontend#RULE-front-001` | TASK-005 | `uv run pytest -q tests/frontend/test_execution_export_ui_contract.py` |
| `harness-i18n#RULE-i18n-001` | TASK-005 | `uv run pytest -q tests/frontend/test_execution_export_ui_contract.py -k i18n` |
| `harness-log#RULE-log-001` | TASK-004 | `uv run pytest -q tests/console_platform/test_execution_export_api.py -k export_writes_audit_log` |
| `harness-secret#RULE-secret-001` | TASK-003 | `uv run pytest -q tests/console_platform/test_execution_export_e03.py` |
| `harness-snapshot#RULE-snapshot-001` | TASK-001 | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata` |
| `harness-test#RULE-test-001` | TASK-006 | `uv run pytest -q tests/console_platform/test_execution_export_*.py tests/frontend/test_execution_export_ui_contract.py` |
| `harness-time#RULE-time-001` | TASK-002 | `uv run pytest -q tests/console_platform/test_execution_export_contract.py -k time` |
| `harness-ui#RULE-ui-001` | TASK-005 | `uv run pytest -q tests/frontend/test_execution_export_ui_contract.py -k layout` |
| `harness-ui-detail#RULE-ui-detail-001` | TASK-005 | `uv run pytest -q tests/frontend/test_execution_export_ui_contract.py -k detail` |
| `harness-worker#RULE-worker-001` | TASK-001 | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b02_batch_children_are_collected` |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP → PostgreSQL → ZIP 解包 | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_api.py::test_s01_failed_run_export_zip"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-02 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP → PostgreSQL → Task/TaskEvent/来源 Run | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_api.py::test_s02_failed_task_export_zip"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-03 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP → PostgreSQL → JSON 报告 | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_api.py::test_s03_successful_run_export_json"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-04 | execution-chain-export.frontend.design.md#2.4 验收条件 | component | React 组件 + mocked service；不启动真实后端 | TASK-005 | verified | ["uv","run","pytest","-q","tests/frontend/test_execution_export_ui_contract.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-05 | execution-chain-export.backend.design.md#2.5 验收条件 | E2E | 真实浏览器 → 真实 Console → 真实 PostgreSQL → 下载 ZIP | TASK-006 | verified | ["e2e/node_modules/.bin/playwright","test","-c","e2e/playwright.execution-export.config.ts","e2e/tests/execution-export.spec.ts"] | . | 1800 | P0 | auto | browser | - | 组件测试 mock service，无法证明真实浏览器下载、Content-Disposition、登录 Cookie 与真实 Console/DB 的接线；只有真实浏览器 + 真实后端能覆盖 click→HTTP→Blob→download 的端到端 | ["apps/console-platform/frontend/src/modules/execution-export/**","apps/console-platform/frontend/src/modules/run-observability/RunDetailSideSheet.tsx","apps/console-platform/backend/src/muad_console_platform/api/runs.py","apps/console-platform/backend/src/muad_console_platform/application/execution_export_service.py"] | yes |
| E-01 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；跨租户/不存在 | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_api.py::test_e01_cross_tenant_run_export_is_not_found"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-02 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；测试配置调小上限 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_e02.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-03 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；诊断标记与 Secret 同时 seed | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_e03.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-04 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；仓储层注入一个可选段异常 | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_repository.py::test_e04_optional_section_failure_is_visible_not_silent"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-05 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；BUILDER 账号直连 API | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_api.py::test_e05_builder_cannot_export"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| B-01 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；250 条非 delta 事件 | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_repository.py::test_b01_exports_all_non_delta_events"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| B-02 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；BATCH 父/子 Task | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_repository.py::test_b02_batch_children_are_collected"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| B-03 | execution-chain-export.backend.design.md#2.5 验收条件 | integration | 真实 Console HTTP + 真实 PostgreSQL；事务隔离/只读元信息 | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata"] | . | 600 | P0 | auto | local-api | - | - | - | no |

---

## TASK-001: 快照采集与任务索引

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.backend.design.md#3.3 数据设计`, `#3.1 方案选型`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: B-01, B-02, B-03, E-04, S-01, S-02
- **Estimate**: 1.5d
- **Capability**: 给定 Run/Task ID，仓储在只读一致快照内返回核心段+补充段+section status；可选段失败不中止；任务按 `source_run_id` 命中新索引。

### Description

实现 `ExecutionExportRepository` 与 `ExecutionExportSettings`：Run/Task 主体、全量非 delta 事件、task_event、模型/工具/Egress 审计、Operation、递归 Task 树、补充段（continuation/inbox/submission/interrupt/control_outbox/artifact/conversation），并输出 `database_snapshot` 与 `data_quality.section_status`。新增 `task.task_execution (tenant_id, source_run_id)` partial index 与迁移 0043。

### Checklist

- [x] `[RULE-data-001][RULE-snapshot-001][RULE-worker-001] verifier: uv run pytest -q tests/migrations/test_execution_export_index.py tests/console_platform/test_execution_export_repository.py::test_b02_batch_children_are_collected tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata`
- [x] 交付上述 Capability，并提供可重复的集成测试命令
- [x] 新增迁移 `0043_task_execution_source_run_index.py` 与索引存在性测试
- [x] 新增 `ExecutionExportSettings`（事件/字节/子任务上限、补充段开关）并接入 `SharedSettings`/进程设置
- [x] 实现仓储：Run 主体 + runtime_snapshot；Task 主体 + execution_snapshot_json；事件/task_event 批量读；审计三表；Operation；递归 Task 树；补充段
- [x] 所有段在同一 `REPEATABLE READ, READ ONLY` 事务内读取，`TimeZone=UTC`，记录 `database_snapshot`
- [x] 段读取失败只影响该段：核心段失败抛出明确异常；可选段返回 `status=error` + warning
- [x] 查询数保持常数上界，不逐事件/逐任务回查
- [x] [B-03][integration] 断言 `database_snapshot` 为 repeatable read/on，导出后新增事件不出现在本次报告
- [x] [E-04][integration] 注入可选段异常，断言核心段与 `section_status/warnings` 正确
- [x] [B-01][integration] 250 条非 delta 事件全部返回，`ASSISTANT_DELTA` 不导出
- [x] [B-02][integration] BATCH 父子任务全部返回，失败子任务事件优先详列
- [x] [S-01][S-02] 支撑集成：本任务采集段为最终导出提供数据；最终验收由 TASK-004 执行
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | integration | Console 服务 → 真实 PostgreSQL 事务 | 隔离级别 repeatable read、只读 on、快照后新事件不可见 | `tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata` | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata` | verified |
| E-04 | integration | Console 服务 → 真实 PostgreSQL + 仓储层故障注入 | 200/核心段保留/可选段 warnings/complete=false | `tests/console_platform/test_execution_export_repository.py::test_e04_optional_section_failure_is_visible_not_silent` | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_e04_optional_section_failure_is_visible_not_silent` | verified |
| B-01 | integration | Console 服务 → 真实 PostgreSQL | 250 条非 delta、delta 剔除、truncated=false | `tests/console_platform/test_execution_export_repository.py::test_b01_exports_all_non_delta_events` | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b01_exports_all_non_delta_events` | verified |
| B-02 | integration | Console 服务 → 真实 PostgreSQL | 父子任务齐全、失败子任务详列、成功子任务汇总 | `tests/console_platform/test_execution_export_repository.py::test_b02_batch_children_are_collected` | `uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b02_batch_children_are_collected` | verified |

### Acceptance Evidence

RED/GREEN：
- RED：首次跑 B-02 时递归 CTE 的 `ANY(:ids::uuid[])` 被 SQLAlchemy 当成未绑定参数，`task.task_execution` 段返回 `ProgrammingError`；改为 `ANY(CAST(:ids AS uuid[]))` 后恢复。
- GREEN：`uv run pytest -q tests/console_platform/test_execution_export_repository.py::test_b01_exports_all_non_delta_events tests/console_platform/test_execution_export_repository.py::test_b02_batch_children_are_collected tests/console_platform/test_execution_export_repository.py::test_b03_reports_snapshot_metadata tests/console_platform/test_execution_export_repository.py::test_e04_optional_section_failure_is_visible_not_silent tests/migrations/test_execution_export_index.py` → 6 passed。
- 真实边界：以上用例全部落真实 PostgreSQL；`database_snapshot` 用例在只读事务中并发插入新事件，断言本次报告不包含它。
- E-04: verified — automated command passed; run_id=cfa5b9423e6b42f0b4671a1bd4d95ae4 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=cfa5b9423e6b42f0b4671a1bd4d95ae4 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=cfa5b9423e6b42f0b4671a1bd4d95ae4 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=cfa5b9423e6b42f0b4671a1bd4d95ae4 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=7b411155d71c4ef0bb1765c4cb5a8a2a (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=7b411155d71c4ef0bb1765c4cb5a8a2a (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=7b411155d71c4ef0bb1765c4cb5a8a2a (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=7b411155d71c4ef0bb1765c4cb5a8a2a (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=c77428322ed144589896254532818246 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=c77428322ed144589896254532818246 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=c77428322ed144589896254532818246 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=c77428322ed144589896254532818246 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)

---

## TASK-002: 失败诊断与报告模型

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.backend.design.md#2.3.2 字段约束`, `#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-time#RULE-time-001, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-time#RULE-time-001
- **Acceptance-Refs**: S-01, S-02, S-03（支撑，最终验收负责人 TASK-004）
- **Estimate**: 1.5d
- **Capability**: 给定采集结果，生成 `primary_failure`/`contributing_failures`、服务归属、日志检索键、`summary/timing/insights` 与报告视图；成功执行不伪造失败。

### Description

实现 `ExecutionDiagnosis` 纯函数与 `ExecutionExportView` 模型：失败候选提取、`basis` 判定、服务映射、日志路径/命令、慢步骤/重试/截止时间 insight、时间出参 offset。成功执行 `primary_failure=null`，仍输出性能洞察。

### Checklist

- [x] `[RULE-time-001] verifier: uv run pytest -q tests/console_platform/test_execution_export_contract.py -k time`
- [x] 交付上述 Capability
- [x] 失败候选覆盖 RUN/EVENT/MODEL/TOOL/EGRESS/OPERATION/TASK/TASK_EVENT，并按时间排序
- [x] `primary_failure` 规则：失败 Run/Task 的权威 error_code 优先；否则最早终态失败；`basis` 明确
- [x] 服务映射覆盖 runtime/worker/gateway；未知错误码按节点类型回退，不猜测外部服务
- [x] 日志检索键 `run_id/trace_id/task_id/tool_call_id/operation_id/audit_id` 与路径/命令
- [x] `summary.timing` 输出 elapsed/deadline gap；所有时间带 offset
- [x] 报告视图模型覆盖 design §2.3.2 全部顶层字段
- [x] [S-03][integration] 成功 Run 导出：outcome=SUCCEEDED、primary_failure=null、insights 有慢步骤/重试
- [x] [S-01/S-02][integration] 失败 Run/Task 的 primary_failure 与权威 error_code 对齐
- [x] [contract] 报告字段/枚举/时间格式契约测试
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实 PG 采集结果 → 诊断/视图 | primary_failure/服务/日志键存在且与 run error_code 对齐 | `tests/console_platform/test_execution_diagnosis.py` | `uv run pytest -q tests/console_platform/test_execution_diagnosis.py` | verified |
| S-02 | integration | 真实 PG 采集结果 → 诊断/视图 | Task 诊断/attempt/来源 Run；worker 主责服务 | `tests/console_platform/test_execution_diagnosis.py` | `uv run pytest -q tests/console_platform/test_execution_diagnosis.py` | verified |
| S-03 | integration | 真实 PG 采集结果 → 诊断/视图 | 成功不伪造失败；insights 有性能提示 | `tests/console_platform/test_execution_export_contract.py` | `uv run pytest -q tests/console_platform/test_execution_export_contract.py` | verified |

### Acceptance Evidence

GREEN：
- `uv run pytest -q tests/console_platform/test_execution_diagnosis.py tests/console_platform/test_execution_export_contract.py` → 9 passed。
- 覆盖：失败 Run 的最早终态节点、deadline 运行级结论优先、成功不伪造失败、Task 失败主责 worker、日志键、原始诊断字段保留、data_policy、时间 offset。
- 本次为纯新增诊断/视图模块，未单独制造 RED；行为契约由上述用例钉住。

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)

---

## TASK-003: 报告渲染与凭据掩码

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.backend.design.md#2.3.2 字段约束`, `#3.5 质量实现方案`, `#3.1 方案选型`
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: E-02, E-03, S-01, S-02（支撑）
- **Estimate**: 1.5d
- **Capability**: 把报告渲染为 JSON/Markdown/CSV/ZIP；诊断字段原样、仅凭据掩码；整体上限/截断显式；ZIP 固定内容。

### Description

实现 `ExecutionExportRender`：JSON、Markdown、events/failures/calls/tasks CSV、README、ZIP；`SecretMasker` 只替换凭据键名/模式并保留 present/length/hash 前缀；`data_policy` 声明 raw/masked/included/admin_only；`truncation` 保留失败窗口与首尾摘要。

### Checklist

- [x] `[RULE-secret-001] verifier: uv run pytest -q tests/console_platform/test_execution_export_e03.py`
- [x] 交付上述 Capability
- [x] 凭据掩码器覆盖 Authorization/Cookie/api_key/access_token/password/secret/私钥/DSN，递归 JSON/文本
- [x] 诊断字段不做逐字段截断；只受 `max_events/max_bytes/max_task_events` 约束
- [x] 截断时保留 `primary_failure` 与失败窗口，写入 `omitted_*`
- [x] ZIP 固定包含 `report.json/report.md/events.csv/failures.csv/calls.csv/tasks.csv/README.md`
- [x] `data_policy` 与 `database_snapshot` 进入报告
- [x] [E-03][integration] 诊断标记原样出现，Secret 原文不出现，data_policy 正确
- [x] [E-02][integration] 超限时 truncated=true、primary_failure 保留、报告可解包
- [x] [unit] Markdown/CSV/ZIP 结构与掩码器纯函数测试
- [x] [S-01][S-02] 支撑集成：本任务渲染产物为最终导出提供内容；最终验收由 TASK-004 执行
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-03 | integration | Console 服务 → 真实 PG → ZIP/JSON | 诊断字段原样、凭据掩码、Secret 不出现 | `tests/console_platform/test_execution_export_e03.py` | `uv run pytest -q tests/console_platform/test_execution_export_e03.py` | verified |
| E-02 | integration | Console 服务 → 真实 PG → ZIP | truncated/失败窗口/primary_failure 保留 | `tests/console_platform/test_execution_export_e02.py` | `uv run pytest -q tests/console_platform/test_execution_export_e02.py` | verified |

### Acceptance Evidence

GREEN：
- `uv run pytest -q tests/console_platform/test_execution_export_render.py tests/console_platform/test_execution_export_e02.py tests/console_platform/test_execution_export_e03.py` → 7 passed。
- 覆盖：凭据键/文本掩码、诊断 token 计数保留、ZIP 固定文件、Markdown 摘要、截断保留 primary_failure、诊断原文保留。
- 过程中修正两处断言/RED：README 字段改中文；`tokens` 子串误伤改为唯一 token 值。
- E-02: verified — automated command passed; run_id=2200a52b28ea4676a1fbfd913796f3ae (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=2200a52b28ea4676a1fbfd913796f3ae (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)

---

## TASK-004: 导出 API 与权限编排

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.backend.design.md#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-arch#RULE-arch-001, harness-log#RULE-log-001
- **Rule-Owner-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-arch#RULE-arch-001, harness-log#RULE-log-001
- **Acceptance-Refs**: S-01, S-02, S-03, E-01, E-05
- **Estimate**: 1.5d
- **Capability**: ADMIN 通过 `GET /api/v1/runs/{id}/export` 与 `/tasks/{id}/export` 下载 ZIP/JSON；404/403/400/500 与可选段降级符合契约；导出日志/指标可查。

### Description

实现 `ExecutionExportService` 编排与两个路由：`AdminAccount` 强制、租户隔离、格式校验、核心段失败 500、可选段失败 200+`X-Export-Complete=false`、`X-Export-Truncated`/`X-Export-Event-Count`、`Cache-Control: no-store`、导出日志/指标。

### Checklist

- [x] `[RULE-api-001][RULE-api-002][RULE-arch-001][RULE-log-001] verifier: uv run pytest -q tests/console_platform/test_execution_export_api.py tests/contracts/test_execution_export_api_contract.py`
- [x] 交付上述 Capability
- [x] `api/runs.py` / `api/tasks.py` 新增 export 路由，声明顺序不与详情/events 冲突
- [x] `AdminAccount` + 租户；非 ADMIN 403 `FORBIDDEN`；跨租户/不存在 404 `COMMON_NOT_FOUND`
- [x] 格式非法 400；核心段失败 500；可选段失败 200 + warnings + `X-Export-Complete=false`
- [x] 响应头 `Content-Type`/`Content-Disposition`/`Cache-Control: no-store`/`X-Export-*`
- [x] 导出日志字段（tenant/account/role/kind/id/format/event_count/size/truncated/duration）不含内容
- [x] [S-01][integration] 失败 Run ZIP 内容/失败诊断/固定文件齐全
- [x] [S-02][integration] 失败 Task ZIP 内容/TaskTree/来源 Run 齐全
- [x] [S-03][integration] 成功 Run JSON 不伪造失败
- [x] [E-01][integration] 跨租户/不存在 404，不泄漏存在性
- [x] [E-05][integration] BUILDER 403，ADMIN 对照 200
- [x] [contract] 错误码/响应头/封套契约测试
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | Console HTTP → 真实 PG → ZIP | 200/ZIP/固定文件/诊断/诊断字段原样 | `tests/console_platform/test_execution_export_api.py::test_s01_failed_run_export_zip` | `uv run pytest -q tests/console_platform/test_execution_export_api.py::test_s01_failed_run_export_zip` | verified |
| S-02 | integration | Console HTTP → 真实 PG → ZIP | Task 摘要/事件/子任务/来源 Run/worker 日志键 | `tests/console_platform/test_execution_export_api.py::test_s02_failed_task_export_zip` | `uv run pytest -q tests/console_platform/test_execution_export_api.py::test_s02_failed_task_export_zip` | verified |
| S-03 | integration | Console HTTP → 真实 PG → JSON | SUCCEEDED/primary_failure=null/性能 insights | `tests/console_platform/test_execution_export_api.py::test_s03_successful_run_export_json` | `uv run pytest -q tests/console_platform/test_execution_export_api.py::test_s03_successful_run_export_json` | verified |
| E-01 | integration | Console HTTP → 真实 PG | 跨租户/不存在 404，无数据 | `tests/console_platform/test_execution_export_api.py::test_e01_cross_tenant_run_export_is_not_found` | `uv run pytest -q tests/console_platform/test_execution_export_api.py::test_e01_cross_tenant_run_export_is_not_found` | verified |
| E-05 | integration | Console HTTP → 真实 PG | BUILDER 403；ADMIN 200 | `tests/console_platform/test_execution_export_api.py::test_e05_builder_cannot_export` | `uv run pytest -q tests/console_platform/test_execution_export_api.py::test_e05_builder_cannot_export` | verified |

### Acceptance Evidence

GREEN：
- `uv run pytest -q tests/console_platform/test_execution_export_api.py tests/contracts/test_execution_export_api_contract.py` → 10 passed。
- 覆盖：失败 Run/Task ZIP、成功 Run JSON、跨租户 404、BUILDER 403、响应头 no-store/X-Export-*、导出日志不含内容。
- 过程中修正：`RUN_NO_PROGRESS` 的 primary_failure 先选最早失败节点，而不是 Run 级记录（否则服务归属误报 runtime）。
- S-01: failed — automated command failed; run_id=c2482dfa99c14463ad821d477f6f1c07 (confirmed_by: runner)
- S-02: failed — automated command failed; run_id=c2482dfa99c14463ad821d477f6f1c07 (confirmed_by: runner)
- S-03: failed — automated command failed; run_id=c2482dfa99c14463ad821d477f6f1c07 (confirmed_by: runner)
- E-01: failed — automated command failed; run_id=c2482dfa99c14463ad821d477f6f1c07 (confirmed_by: runner)
- E-05: failed — automated command failed; run_id=c2482dfa99c14463ad821d477f6f1c07 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=be473cddf76e47a1b2c23cbc2c38e7d9 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=be473cddf76e47a1b2c23cbc2c38e7d9 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=be473cddf76e47a1b2c23cbc2c38e7d9 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=be473cddf76e47a1b2c23cbc2c38e7d9 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=be473cddf76e47a1b2c23cbc2c38e7d9 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=6d2c10d9466a4533b86307af440c4768 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=6d2c10d9466a4533b86307af440c4768 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=6d2c10d9466a4533b86307af440c4768 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=6d2c10d9466a4533b86307af440c4768 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=6d2c10d9466a4533b86307af440c4768 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)

---

## TASK-005: 前端导出入口与状态

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.frontend.design.md#3.2 页面与路由结构`, `#3.3 组件设计`, `#3.4 组件接口契约`, `#3.5 状态与数据流`, `#3.6 UI 状态`
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001
- **Rule-Owner-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-04, E-05（UI 隐藏部分）
- **Estimate**: 1.5d
- **Capability**: ADMIN 在 Run/Task 详情看到「导出链路」，点击下载 ZIP；loading/404/网络失败/重试；非 ADMIN 不渲染。

### Description

新增 `modules/execution-export`：service（`GET /runs|tasks/{id}/export`，Blob + filename）、hook、`ExportExecutionButton`、`saveBlob`；接入 `RunDetailSideSheet` 与 `TaskDetailSideSheet` 的 `DetailSideSheet` actions；使用 `useAuth` 做 ADMIN 可见性；补 zh-CN/en-US 词条。

### Checklist

- [x] `[RULE-front-001][RULE-i18n-001][RULE-ui-001][RULE-ui-detail-001] verifier: uv run pytest -q tests/frontend/test_execution_export_ui_contract.py`
- [x] 交付上述 Capability
- [x] service 只经共享 `api`，不裸 axios/fetch；解析 `Content-Disposition`
- [x] `useExecutionExport` busy/errorCode/start；busy 防重入；失败恢复可重试
- [x] `ExportExecutionButton` props/kind/id/format/adminOnly；tooltip 说明包含完整诊断数据
- [x] Run/Task 详情接线，不新增路由/菜单；不重复详情操作
- [x] i18n zh-CN/en-US 全部词条；错误文案来自词条
- [x] [S-04][component] 按钮常驻/loading/成功下载/404/网络失败/重试/id 空不渲染/非 ADMIN 不渲染
- [x] [contract] 组件不裸调 HTTP、service 契约、i18n 键齐全
- [x] [E-05] 支撑：非 ADMIN 不渲染入口；API 403 由 TASK-004 集成测试最终验收
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | component | React 组件 + mocked service | 按钮/加载/下载/错误/重试/ADMIN 可见性 | `tests/frontend/test_execution_export_ui_contract.py` | `uv run pytest -q tests/frontend/test_execution_export_ui_contract.py` | verified |

### Acceptance Evidence

GREEN：
- `uv run pytest -q tests/frontend/test_execution_export_ui_contract.py` → 5 passed。
- `npm --prefix apps/console-platform/frontend run typecheck` → passed。
- 覆盖：ADMIN 可见性、按钮/tooltip/busy、service 仅经共享 api + Blob 下载、hook 错误/重试、Run/Task 详情接线、zh/en 词条。
- S-04: verified — automated command passed; run_id=515646415dc0497abf47c9b37539c49c (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=3c469d2d1bba4584b0951cf212913706 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)

---

## TASK-006: 浏览器 E2E 与全量验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005
- **Source**: `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.backend.design.md#2.5.4 验证环境假设`, `.code-flow/tasks/2026-11-09/execution-chain-export/execution-chain-export.frontend.design.md#2.4.1 验证环境假设`
- **Spec-Refs**: harness-e2e#RULE-e2e-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-e2e#RULE-e2e-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-05
- **Estimate**: 1.5d
- **Capability**: 真实浏览器登录 → 打开失败 Run → 点击「导出链路」→ 下载 ZIP → 解包校验失败节点；全量 functional 检查通过。

### Description

新增 Playwright 配置/spec 与隔离种子：真实 Console/前端构建产物/PG/登录会话；捕获 download 并解包 `report.json`/`report.md`；登记 browser check；最后运行全量 functional 与需求验收命令，补齐 README/文档。

### Checklist

- [x] `[RULE-e2e-001][RULE-test-001] verifier: npm --prefix e2e exec playwright test -c playwright.execution-export.config.ts && uv run pytest -q tests/console_platform/test_execution_export_*.py`
- [x] 交付上述 Capability
- [x] 新增 `e2e/playwright.execution-export.config.ts` 与 `e2e/tests/execution-export.spec.ts`，真实构建产物/真实浏览器/真实 PG
- [x] 独立隔离库 + `useIsolatedDatastores` + spec 的 `beforeAll` 调 seed CLI（失败 Run + TOOL 审计 + ADMIN 账号）；下载文件落盘后解包
- [x] 断言按钮可见、download 触发、文件名、ZIP 可解包、primary_failure/服务/日志键
- [x] E2E 登记 `why_e2e`/`trigger_paths`/`smoke`；按 `acceptance-e2e` 域运行
- [x] 全量 functional：`uv run pytest -q tests/console_platform/test_execution_export_*.py tests/frontend/test_execution_export_ui_contract.py`
- [x] 文档：导出字段、ADMIN 边界、凭据掩码、日志检索方式
- [x] [S-05][E2E] 真实浏览器下载与解包校验
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实浏览器 → 真实 Console → 真实 PG → ZIP | 下载触发/文件名/ZIP 可解包/primary_failure/服务/日志键 | `e2e/tests/execution-export.spec.ts` | `e2e/node_modules/.bin/playwright test -c e2e/playwright.execution-export.config.ts e2e/tests/execution-export.spec.ts` | verified |

### Acceptance Evidence

GREEN：
- `uv run pytest -q tests/console_platform/test_execution_export_api.py tests/console_platform/test_execution_export_repository.py tests/console_platform/test_execution_diagnosis.py tests/console_platform/test_execution_export_render.py tests/console_platform/test_execution_export_e02.py tests/console_platform/test_execution_export_e03.py tests/console_platform/test_execution_export_contract.py tests/frontend/test_execution_export_ui_contract.py tests/contracts/test_execution_export_api_contract.py tests/migrations/test_execution_export_index.py` → 37 passed。
- `npm --prefix apps/console-platform/frontend run build` → passed（typecheck + build）。
- E2E（实际执行）：`e2e/node_modules/.bin/playwright test -c e2e/playwright.execution-export.config.ts e2e/tests/execution-export.spec.ts` → 1 passed (4.5s)。真实 Chrome/Console/PG/构建产物，登录→审计筛选→关联 Run→下载 ZIP→解包 `report.json`/`report.md`；断言 FAILED、primary_failure、log_lookup.run_id、诊断标记存在、Secret 标记不存在。
- 文档：`docs/03-Console-Platform详细设计.md` §11.7 已补充 ADMIN 专属导出、快照、原始诊断字段/凭据掩码与日志定位边界。
- S-05: verified — `e2e/node_modules/.bin/playwright test -c e2e/playwright.execution-export.config.ts e2e/tests/execution-export.spec.ts` 1 passed (4.5s)；automated command passed; run_id=a273c94f6b5241c9962c4c521bc7bf31 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)
