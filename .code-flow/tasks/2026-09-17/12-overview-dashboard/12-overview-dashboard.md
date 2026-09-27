# Tasks: 概览与运营入口

- **Source**: .code-flow/tasks/2026-09-17/12-overview-dashboard/（全部 design：12-overview-dashboard.backend.design.md、12-overview-dashboard.frontend.design.md）
- **Created**: 2026-09-26
- **Updated**: 2026-09-27
- **Plan-State**: planned（用户已确认写入；各 TASK 保持 draft，功能与 E2E 验收尚未执行）

## Proposal

为 Console 补齐跨模块的运营入口：以**一次只读聚合**（`GET /api/v1/overview`）返回 4 个 KPI（启用 Agent / 启用 Skill / 后台执行中 Task / 启用定时任务）与最近任务、下一批定时两组列表，避免前端按实体循环拉取造成的 N+1 与 loading 碎片化。前端新增 `overview-dashboard` 模块，承载 KPI 卡片、静态运行关系说明与两个运营列表，并把「查看全部 / 条目跳转」接到既有模块路由。

绝不新增表、不建快照/物化视图、不写任何表：所有指标在请求时由少量聚合 SQL 计算（只读 Owner 表，Redis 不可用时功能仍可用）。共 11 个原子任务：P0 8、P1 3。

## Design Alignment

2026-09-26 拆解前完成三处对齐（均已写入 Context，不代表实现或 verifier 已通过）：

- **场景 ID 重编（前端 4 个中缀 ID）**：前端 design 声明的 `S-FE-01/S-FE-02/E-FE-01/E-FE-02` 带中缀，会被 manifest/runner **静默忽略**（不报错、不执行）。按既定口径并入后端同一数字序列：`S-FE-01→S-03`、`S-FE-02→S-04`、`E-FE-01→E-03`、`E-FE-02→E-04`（与 11-audit-observability 的做法一致：后端 S-01..S-05、前端续 S-06..S-08）。用户确认。S-01/S-03 与 S-02/S-04 语义相近但**边界与渲染面不同**，保留两组，不降级不删减。
- **`harness-api#RULE-api-002` 判 N/A（逐项确认）**：该规则只约束「创建/上传类 POST 的 `Idempotency-Key` 幂等」，其 verifier 为 `tests/console_skill/test_import_idempotency.py`；本模块两份 design 均声明**只读聚合、不写任何表**，全模块仅 `GET /api/v1/overview` 一个接口，无创建类 POST 可承接（该规则系 11 那轮新增，本需求 design 的 Matrix 尚无此行）。经用户逐项确认，四个 stage 均置 `not_applicable`（`cf_spec_context decision`，`batch=false`，`confirmed_by=jahan`）。
- **spec id 校正**：两份 design 的 Spec Compliance Matrix 写的是 legacy `harness-platform#RULE-*`，按现行分域 spec 校正为 `harness-api#RULE-api-001` / `harness-ui#RULE-ui-001` / `harness-time#RULE-time-001` / `harness-frontend#RULE-front-001` / `harness-i18n#RULE-i18n-001` / `harness-test#RULE-test-001`，禁止重新选择或降级 enforcement。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | 验收 | Checklist |
|---|---|---|---|---|---|---|
| TASK-001 | P0 | 概览聚合查询服务与 API-01 | 无 | backend 3.2/3.3/3.4；2.4 | E-01(integration), RULE-api-001(integration), RULE-time-001(integration) | 6 |
| TASK-002 | P1 | docs/07 §10 端点契约补录 | 001 | backend 3.3；4.2 RISK-02 | B-201(integration) | 4 |
| TASK-003 | P0 | 概览验收环境与种子清理 | 001 | backend 3.5/2.4 | B-202(integration) | 5 |
| TASK-004 | P0 | 后端场景真实验收（S-01 + 无 N+1） | 003 | backend 2.4；4.2 RISK-01/03 | S-01(E2E) | 6 |
| TASK-005 | P0 | 前端 service 层与类型契约 | 001 | frontend 3.4/3.5 | B-203(integration), RULE-front-001(integration) | 5 |
| TASK-006 | P0 | OverviewPage 容器 + KpiCards | 005 | frontend 3.3/3.3.1/3.4 | B-204(integration), RULE-ui-001(integration) | 5 |
| TASK-007 | P0 | 最近任务/下一批定时/运行关系卡片 | 006 | frontend 3.3/3.4/3.6 | B-205(integration) | 5 |
| TASK-008 | P1 | 路由接入与 UI 状态 | 007 | frontend 3.2/3.6 | E-03(integration), B-206(integration) | 5 |
| TASK-009 | P1 | i18n 词条与语言切换覆盖 | 006 | frontend 3.5/3.6 | B-207(integration), RULE-i18n-001(integration) | 4 |
| TASK-010 | P0 | 前端 E2E 验收 | 008 | frontend 2.4；3.6 | S-02(E2E), S-03(E2E), S-04(E2E), E-02(integration), E-04(integration), B-208(integration) | 7 |
| TASK-011 | P0 | 收口：场景、规则、证据与仓库级 verifier | 004, 010 | backend 2.4/2.5；6 需求追溯 | B-209(integration), RULE-test-001(E2E) | 5 |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | 12-overview-dashboard.backend.design.md#2.4 验收条件 | E2E | 真实 Console HTTP 聚合查询→四张 Owner 表(PostgreSQL) | TASK-004 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/overview/test_overview_acceptance.py","-k","s01"] | . | 1200 |  |
| S-02 | 12-overview-dashboard.backend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→Console 首页→目标模块路由 | TASK-010 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-02\""] | . | 1200 |  |
| S-03 | 12-overview-dashboard.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→overview API（一次加载，无前端 N+1） | TASK-010 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-03\""] | . | 1200 |  |
| S-04 | 12-overview-dashboard.frontend.design.md#2.4 验收条件 | E2E | Browser Router→tasks/schedules 且菜单选中正确 | TASK-010 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-04\""] | . | 1200 |  |
| E-01 | 12-overview-dashboard.backend.design.md#2.4 验收条件 | integration | 真实 Console HTTP→真实 PostgreSQL（某模块无数据） | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_overview_api.py","-k","e01"] | . | 600 |  |
| E-02 | 12-overview-dashboard.backend.design.md#2.4 验收条件 | integration | Browser→Router→目标页（目标 ID 已失效/无权限） | TASK-010 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-02\""] | . | 900 |  |
| E-03 | 12-overview-dashboard.frontend.design.md#2.4 验收条件 | integration | Browser→overview API 失败→ErrorState | TASK-008 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-03\""] | . | 900 |  |
| E-04 | 12-overview-dashboard.frontend.design.md#2.4 验收条件 | integration | Browser Router→目标页（目标 ID 已失效/无权限） | TASK-010 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-04\""] | . | 900 |  |
| B-201 | 12-overview-dashboard.backend.design.md#3.3 接口设计 | integration | docs/07 §10 契约登记→冻结 schema 逐项一致 | TASK-002 | verified | ["uv","run","pytest","-q","tests/console_platform/test_overview_api.py","-k","b201"] | . | 600 |  |
| B-202 | 12-overview-dashboard.backend.design.md#3.5 质量实现方案 | integration | 真实多进程栈(Console+PostgreSQL)与租户级种子/清理 | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/overview/test_environment.py"] | . | 600 |  |
| B-203 | 12-overview-dashboard.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本 | TASK-005 | verified | ["uv","run","pytest","-q","tests/frontend/test_overview_services_contract.py"] | . | 600 |  |
| B-204 | 12-overview-dashboard.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc + 真实构建产物 | TASK-006 | verified | ["uv","run","pytest","-q","tests/frontend/test_overview_page_contract.py"] | . | 600 |  |
| B-205 | 12-overview-dashboard.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 | TASK-007 | verified | ["uv","run","pytest","-q","tests/frontend/test_overview_lists_contract.py"] | . | 600 |  |
| B-206 | 12-overview-dashboard.frontend.design.md#3.2 页面与路由结构 | integration | 前端源码契约（路由表 + 菜单选中）+ 真实构建 | TASK-008 | planned | ["uv","run","pytest","-q","tests/frontend/test_overview_routing_contract.py"] | . | 600 |  |
| B-207 | 12-overview-dashboard.frontend.design.md#3.5 状态与数据流 | integration | 前端源码契约 + 两侧词条实际内容 + 真实 tsc | TASK-009 | planned | ["uv","run","pytest","-q","tests/frontend/test_overview_i18n_contract.py"] | . | 600 |  |
| B-208 | 12-overview-dashboard.frontend.design.md#2.4 验收条件 | integration | Playwright 配置与 spec 的租户/端口隔离、运行后零残留 | TASK-010 | planned | ["uv","run","pytest","-q","tests/frontend/test_overview_e2e_fixture_contract.py"] | . | 600 |  |
| B-209 | 12-overview-dashboard.backend.design.md#3.5 质量实现方案 | integration | pytest 用例收集/运行→验收 Contract/Evidence→真实组件记录 | TASK-011 | planned | ["uv","run","pytest","-q","tests/overview_dashboard_inventory.py","-k","b209"] | . | 600 |  |
| RULE-api-001 | 12-overview-dashboard.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP 封套/错误码 + 原 verifier 真实边界 | TASK-001 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 |  |
| RULE-time-001 | 12-overview-dashboard.backend.design.md#Spec Compliance Matrix | integration | Console 时间出参 + 原 verifier 真实边界 | TASK-001 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests/frontend/test_datetime_contract.py"] | . | 1200 |  |
| RULE-data-001 | 12-overview-dashboard.backend.design.md#Spec Compliance Matrix | integration | 真实 PostgreSQL 表结构/约束 + 原 verifier 真实边界 | TASK-001 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests -k schema_parity"] | . | 600 |  |
| RULE-front-001 | 12-overview-dashboard.frontend.design.md#Spec Compliance Matrix | integration | 前端源码契约（services 收口/无裸请求/i18n）+ 原 verifier 真实边界 | TASK-005 | planned | ["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 900 |  |
| RULE-ui-001 | 12-overview-dashboard.frontend.design.md#Spec Compliance Matrix | integration | 真实 Console 页面骨架 + 原 verifier 真实边界 | TASK-006 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | . | 1200 |  |
| RULE-ui-detail-001 | 12-overview-dashboard.frontend.design.md#Spec Compliance Matrix | integration | 公共详情组件契约 + 原 verifier 真实边界 | TASK-006 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 900 |  |
| RULE-i18n-001 | 12-overview-dashboard.frontend.design.md#Spec Compliance Matrix | integration | 真实 Console 页面双语 + 原 verifier 真实边界 | TASK-009 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | . | 1200 |  |
| RULE-test-001 | 12-overview-dashboard.frontend.design.md#Spec Compliance Matrix | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Browser）+ 原 verifier 真实边界 | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 2400 |  |

> 本表覆盖两份 design 中全部 P0/P1 场景（S-01、S-02、E-01、E-02 与重编后的 S-03、S-04、E-03、E-04 共 8 个）与 7 条 required Spec Rule（`RULE-api-001`/`RULE-time-001`/`RULE-data-001`/`RULE-front-001`/`RULE-ui-001`/`RULE-i18n-001`/`RULE-test-001`，其中 `RULE-data-001` 于 2026-09-26 由实现路径局部 Plan 承接）；每个场景与规则有且仅有一个最终负责人；无 manual 场景；E2E 层级不降级。`harness-api#RULE-api-002` 经用户逐项确认置 `not_applicable`（只读模块无创建类 POST），故不在本表登记。B-201..B-209 为「无 design 场景的任务」补的自有集成场景（每任务需自有可执行场景才能过 Done Gate）。

---

## TASK-001: 概览聚合查询服务与 API-01

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 12-overview-dashboard.backend.design.md#2.2 功能方案, 12-overview-dashboard.backend.design.md#3.2 架构设计, 12-overview-dashboard.backend.design.md#3.3 接口设计, 12-overview-dashboard.backend.design.md#3.4 性能与容量考量
- **Spec-Refs**: harness-api#RULE-api-001, harness-time#RULE-time-001, harness-data#RULE-data-001
- **Acceptance-Refs**: E-01, RULE-api-001, RULE-time-001, RULE-data-001
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/overview.py`, `apps/console-platform/backend/src/muad_console_platform/application/overview_query_service.py`, `apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/overview_query_repository.py`, `apps/console-platform/backend/src/muad_console_platform/api/router.py`, `tests/console_platform/test_overview_api.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现 API-01 `GET /api/v1/overview`：请求时由少量聚合 SQL 计算 4 个 KPI（启用 Agent / 启用 Skill / 后台执行中 Task / 启用定时任务）与 `recent_tasks`（`create_time DESC` LIMIT 5）、`next_schedules`（`status='ACTIVE' AND next_fire_at IS NOT NULL`，`next_fire_at ASC` LIMIT 5）。响应结构按 design §3.3 冻结契约逐字段实现；所有查询带 `tenant_id` 与 `is_deleted=false`；只读 Owner 表，不新增表、不建快照、不写 Redis。

### Checklist

- [x] [E-01][integration] 以真实 Console HTTP + 真实 PostgreSQL 为边界编写用例：某模块无数据时对应 KPI=0 / 列表为空，其余区块照常返回，**不把整个概览判错**。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_overview_api.py","-k","e01"]`。
- [x] [RULE-api-001][integration] 作为唯一最终负责人，验证统一封套 `code/msg/data/trace_id/request_id/timestamp`，业务只抛 error code、`msg`/`http_status` 只来自 `config/api-messages.yaml`（`UNAUTHORIZED` 路径）。verifier argv：`["bash","-lc","uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]`。
- [x] [RULE-time-001][integration] 作为唯一最终负责人，验证时间出参 `YYYY-MM-DD HH:mm:ss`、存储 `timestamptz`。verifier argv：`["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]`。
- [x] [RULE-data-001][integration] 作为唯一最终负责人（2026-09-26 局部 Plan 承接：新增 `infrastructure/repositories/**` 路径触发 `harness-data` 绑定），验证只读查询所依赖列的 ORM↔DDL 口径不漂移（本模块不建表、不改迁移）。verifier argv：`["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests -k schema_parity"]`。
- [x] 实现或补齐：≤5 条聚合 SQL、两次排序 LIMIT 5、KPI 用条件 COUNT；按 design §3.3 逐字段塑形 `recent_tasks`/`next_schedules`；禁止逐实体查询（N+1）；不返回 Secret/凭据。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | Console HTTP + 真实 PostgreSQL | 某模块无数据 → 对应 KPI=0/列表空；其余区块正常；整体不判错 | tests/console_platform/test_overview_api.py / E-01 | `["uv","run","pytest","-q","tests/console_platform/test_overview_api.py","-k","e01"]` | verified |
| RULE-api-001 | integration | Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | 统一封套键集；错误码文案来自 catalog；原 verifier 全部通过 | tests/console_platform/test_overview_api.py + 原 verifier / RULE-api-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]` | verified |
| RULE-time-001 | integration | Console HTTP 时间出参 + 原 verifier 真实边界 | 出参 `YYYY-MM-DD HH:mm:ss`；存储 timestamptz；原 verifier 全部通过 | tests/console_platform/test_overview_api.py + 原 verifier / RULE-time-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests/frontend/test_datetime_contract.py"]` | verified |
| RULE-data-001 | integration | 真实 PostgreSQL 表结构/约束 + 原 verifier 真实边界 | 所依赖表（control 三表 + task 两表）的标准列与软删语义同 ORM 元数据一致；原 verifier 全部通过 | tests/console_platform/test_overview_api.py + 原 verifier / RULE-data-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_overview_api.py && uv run pytest -q tests -k schema_parity"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-01 | **结构性 RED**（先写测试后实现）：按登记 argv 执行 → **4 failed**，其中 3 个为 `assert 404 == 200`、1 个为 `assert 404 == 401`，响应体一律 `COMMON_NOT_FOUND`（`GET /api/v1/overview` 路由尚不存在）。诚实记录：实现落地后转为 **3 failed**，但失败均在**测试侧**——① `_overview` 未带 `X-Tenant-Id`，`get_tenant_id` 回落 default 租户（KPI 读到全局 77/39）；② 误把 `next_schedules` 的 `next_fire_at IS NOT NULL` 条件也加到 `active_schedules` KPI 上；③ 误以为 `recent_tasks` 过滤终态。三处均按 design §3.2.1 原文修正测试，**实现未因此改动**。 | 整文件 `uv run pytest -q tests/console_platform/test_overview_api.py` → **4 passed**；回归 `tests/console_platform/` **116 passed**；改动文件 ruff 干净、mypy 4 files 无问题。 | `test_e01_modules_without_data_yield_zero_and_empty_lists`：四类数据全空的租户 → `kpis` 键集恰为四 KPI 且全为 0、`recent_tasks`/`next_schedules` 均为 `[]`，HTTP 仍 200 且 `code="0"`（局部缺失不把整体判错）；`test_e01_partial_data_only_affects_its_own_kpi`：启用/停用/软删 Agent 与启用/停用 Skill 只计「启用且未软删」；`active_tasks` 只计 `QUEUED/RUNNING/WAITING`（`SUCCEEDED` 不计入 KPI 但仍在列表）；`active_schedules` 按 `status='ACTIVE'` 计数（不要求 `next_fire_at`），而 `next_schedules` **列表**额外要求 `next_fire_at IS NOT NULL`（PAUSED 与无下次触发项均不在列表中）。 | 真实 Console ASGI 全栈（登录会话 + CSRF + `X-Tenant-Id`）→ 真实 PostgreSQL：`control.{agent_definition,skill,platform_user}` 与 `task.{task_execution,task_schedule}` 逐行播种并回读（含停用、软删、终态、PAUSED、`next_fire_at IS NULL` 边界行）；未 mock 业务 API。 | verified |
| RULE-api-001 | 同上（验收类，随 E-01 一并取证，不另造失败）。 | 联合验收 argv 全通过：新用例 4 passed + 原 verifier **18 passed**。 | `test_rule_api_001_envelope_and_unauthenticated_error`：匿名 GET → **401**，封套键集恰为 `{code,msg,data,trace_id,request_id,timestamp}`、`code="UNAUTHORIZED"`、`msg` 非空（文案来自 catalog）；`test_rule_api_001_contract_and_rule_time_001_time_format`：已认证响应 `code="0"`、`data` 键集恰为 `{kpis,recent_tasks,next_schedules}`，`recent_tasks` 条目键集与 `next_schedules` 条目键集与 design §3.3 冻结契约**逐项相等**（各 13 / 11 个字段）。 | 真实 Console HTTP 响应体 + 真实 PostgreSQL；原 verifier（`tests/test_api_i18n.py`、`tests/test_error_catalog.py`、`tests/acceptance/test_foundation_api_envelope.py`）真实边界。 | verified |
| RULE-time-001 | 同上（验收类）。 | 联合验收 argv 全通过：新用例 4 passed + `tests/frontend/test_datetime_contract.py` 通过 + `pytest tests -k schema_parity` **35 passed**。 | 同上用例：`started_at`/`deadline_at`/`create_time`/`next_fire_at` 匹配 `YYYY-MM-DD HH:mm:ss`，且与「存储的绝对时刻按本地时区换算」**独立复算**一致（测试侧用 `datetime.astimezone()` 复算期望值，不复用被测格式化函数）；`create_time DESC` 排序按绝对时刻；`finished_at`/`last_fire_at` 为空时保持 `None` 而非空串。 | 真实 Console HTTP 响应体 + 真实 PostgreSQL `timestamptz` 逐行回读；原 verifier 真实边界。 | verified |

**实现中的判断点（如实登记，未静默决定）**：

- **分层位置按仓库实际，未按 design 的 `modules/overview/`**：design 建议代码位置写 `apps/console-platform/backend/src/muad_console_platform/modules/overview/`，但仓库后端实际分层是 `api/` + `application/` + `domain/` + `infrastructure/`（**不存在 `modules/` 子包**，那是前端约定）。按 route→service→repository 落地为 `api/overview.py` / `application/overview_query_service.py` / `infrastructure/repositories/overview_query_repository.py`，与 11-audit-observability 同类实现一致。**设计待更正**。
- **时间格式复用既有 helper**：`format_console_time` / `CONSOLE_TIME_FORMAT` 取自 `application/audit_query_service.py`（11 已确立的唯一口径），本模块 import 复用而非再定义一份，避免 RULE-time-001 出现两套格式化实现。
- **KPI 与列表的 Schedule 条件不同**：`active_schedules` 只按 `status='ACTIVE'` 计数，`next_schedules` 额外要求 `next_fire_at IS NOT NULL`（design §3.2.1 两处原文即如此），测试已把该差异固定为断言而非含糊处理。
- **Console 直读 `task` schema**：本模块按 design §3.2「单请求内 ≤5 条聚合 SQL」在 Console 侧直读 `task.{task_execution,task_schedule}`；与「Console task API 经 Worker Admin HTTP」不冲突（那是写/管理路径），且 11 的 `audit_query_repository` 已有同 schema 只读投影先例。全部查询带 `tenant_id` 与 `is_deleted=false`，不写任何表。
- **顺带修掉 `api/router.py` 预存在的 ruff I001**（`schedules` import 位置错序）：因本次改动该文件，按「改动文件须 lint 干净」一并整理。
- E-01: verified — automated command passed; run_id=cae6e10e4f224ef581d4d6c17f7066aa (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=820169b6c75d40d591534a6e90515353 (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-26] started
- [2026-09-26] resumed (in-progress)
- [2026-09-26] completed (done)
## TASK-002: docs/07 §10 端点契约补录

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: 12-overview-dashboard.backend.design.md#3.3 接口设计, 12-overview-dashboard.backend.design.md#4.2 风险识别
- **Spec-Refs**:
- **Acceptance-Refs**: B-201
- **Files**: `docs/07-跨模块接口与协议详细设计.md`, `tests/console_platform/test_overview_api.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

落实 RISK-02：design 明确登记「该端点尚未写入 docs/07 §10，需由主任务补录」。把 `GET /api/v1/overview` 的路径、请求、响应字段与错误码补录进 docs/07 §10，避免跨模块契约漂移。本任务不写生产代码。

### Checklist

- [x] [B-201][integration] 以 docs/07 真实文本与冻结契约为边界编写用例：断言 docs/07 §10 已登记 `GET /api/v1/overview`，且字段集与 design §3.3 冻结契约**逐项一致**（4 KPI 键 + `recent_tasks`/`next_schedules` 全部字段名），字段缺失或拼写漂移即失败。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_overview_api.py","-k","b201"]`。
- [x] 在 docs/07 §10 补录端点契约（含错误码 `UNAUTHORIZED`/`COMMON_INTERNAL_ERROR` 与分页/排序口径）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN 与断言位置；不得以「文档已写」代替可执行断言。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-201 | integration | docs/07 真实文本 + design 冻结契约 | §10 已登记该端点；字段集逐项一致 | tests/console_platform/test_overview_api.py / B-201 | `["uv","run","pytest","-q","tests/console_platform/test_overview_api.py","-k","b201"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-201 | **真实 RED**：先写用例后补文档 → `-k b201` **1 failed**，`AssertionError: docs/07 §10.12 缺 KPI 字段 active_schedules`（该小节当时只有散文描述 + 指向设计文档的指针，没有任何字段契约）。 | 在 docs/07 §10.12 补录冻结契约（请求形态、KPI 四键、两组列表全部字段、计数与筛选口径、错误码、`LIMIT 5`、`tenant_id` 隔离）→ `-k b201` **1 passed**；整文件 **5 passed**；ruff 干净。 | `test_b201_docs_07_registers_frozen_overview_contract`：解析 docs/07 §10.12 小节，断言端点已登记、4 个 KPI 键与两组列表全部字段（`TASK_ITEM_KEYS ∪ SCHEDULE_ITEM_KEYS`）逐项出现、两个错误码齐备、写明 `LIMIT 5` 与 `tenant_id` 隔离。**闭环**：同一文件另有 `test_rule_api_001_contract_and_rule_time_001_time_format` 断言真实响应的字段集 == 上述常量，故文档与实现任一侧漂移都会被打红。 | docs/07 真实文本（`docs/07-跨模块接口与协议详细设计.md` §10.12）+ design §3.3 冻结契约；非「文档已写」式空转断言，且经 mutation 语义验证（补录前确为 RED）。 | verified |

**实现中的判断点（如实登记）**：

- **缺口比 design 描述更窄**：design 写「该端点尚未写入 docs/07 §10」，实际 §10.12 Overview 已存在（含路径与一句摘要），缺的是**字段契约与错误码**（原文让读者去翻需求级设计文档，正是 RISK-02 要消除的）。故本任务只补契约，不重写小节。
- **未搬动同小节内误置的 `result_status` 段**：该段讲审计 `result_status` 映射，应属 §10.11 Audit（疑为 11-audit-observability 收尾时追加位置有误）。属既有内容的归属问题，不在本任务范围，登记待后续修订。
- B-201: verified — automated command passed; run_id=a092f63d090e4505893f4df61b49be4a (confirmed_by: runner)
- B-201: verified — automated command passed; run_id=3d048f558faa4075841ebf988eeff241 (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-26] started
- [2026-09-26] completed (done)
## TASK-003: 概览验收环境与种子清理

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 12-overview-dashboard.backend.design.md#2.4 验收条件, 12-overview-dashboard.backend.design.md#3.4 性能与容量考量
- **Spec-Refs**:
- **Acceptance-Refs**: B-202
- **Files**: `tests/acceptance/overview/__init__.py`, `tests/acceptance/overview/environment.py`, `tests/acceptance/overview/test_environment.py`
- **Estimate**: 半天级；外部服务启动与等待另计

### Description

为 S-01 与前端 E2E 建立真实验收环境：真实 Console 进程栈 + 真实 PostgreSQL，租户级种子（启用/停用 Agent、启用/停用 Skill、`QUEUED/RUNNING/WAITING` 与终态 Task、`ACTIVE/PAUSED` Schedule、`next_fire_at` 有值与为空的 Schedule）与收尾清理，保证 S-01 的「4 KPI + 两组列表」断言有真实数据可依。

### Checklist

- [x] [B-202][integration] 以真实多进程栈 + 真实 PostgreSQL 为边界编写用例：种子后按各表逐行回读断言数量与状态符合预期（含停用项不计入 KPI、PAUSED 不进下一批、`next_fire_at IS NULL` 不进下一批）。执行 argv：`["uv","run","pytest","-q","tests/acceptance/overview/test_environment.py"]`。
- [x] 收尾清理：用例结束后租户内各表残留为 0，且不污染仓库 `.data/artifacts`。
- [x] 用例结束不得残留 uvicorn/`muad_*.main` 进程（收尾在任何失败路径下都要执行）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN 与真实边界证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-202 | integration | 真实 Console 进程 + 真实 PostgreSQL + 租户级清理 | 种子数量/状态逐行回读一致；停用与 PAUSED/无 next_fire_at 项被正确排除；清理后残留 0 | tests/acceptance/overview/test_environment.py / B-202 | `["uv","run","pytest","-q","tests/acceptance/overview/test_environment.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-202 | **真实 RED（两轮，均为环境自身缺陷）**：① 首轮 `2 failed` —— `/healthz` 断言读 `body["status"]` 得 `KeyError`（探针经 api-kit 封套返回，实际在 `data.status`）；`purge` 用例报 `UndefinedColumnError: column "tenant_id" does not exist`（`control.console_session` 按 `account_id` 关联、无 `tenant_id`，不该列入按租户计数的白名单）。② 修掉这两处后 `1 failed`：`control.console_account 清理后仍有残留`（`CONTROL_CLEANUP` 来自 09 栈，其租户不含 Console 账号，故不清理 `console_account`/`console_session`，而本环境种了可登录 admin）。 | 补 `CONSOLE_CLEANUP`（会话先于账号）+ 修正探针判读路径后：整文件 **5 passed**；ruff 干净；运行后 `ps` 无 uvicorn/`muad_*.main` 残留；DB 内无 `overview-acceptance-*` 遗留租户。 | `test_b202_stack_boots_and_dependencies_are_ready`（`/healthz` → `data.status=ok`、`/readyz` → `data.status=ready`，PG 不可达时为 503）；`test_b202_seed_produces_expected_rows`（8 张表逐表回读精确行数——共享开发库中还有其它租户数据，能取到**精确**数目即证明按 `tenant_id` 隔离生效）；`test_b202_seed_expectations_match_design_semantics`（3 个 ACTIVE Schedule 全计入 KPI、列表只含带 `next_fire_at` 的 2 条且 ASC、5 条任务不过滤状态）；`test_b202_seeded_admin_can_authenticate`（真实登录 + `/auth/me` 返回种子 admin）；`test_b202_purge_is_idempotent_and_leaves_no_residue`（连续 purge 两次后 8 张表全 0，且清理前先断言 >0 以保证断言灵敏度、并以具体 Agent 名再锚一次非空转） | 真实 Console uvicorn 子进程（失败路径也走 `stop_overview_stack`）+ 真实 PostgreSQL 逐表回读 + 真实登录会话；未 mock 业务服务、未覆盖业务路由。**按需最小栈**：概览是只读聚合，S-01 的边界只需 Console+PG，故不启动 Runtime/Worker/Gateway/探针（口径同 11 对 Gateway 的处理），进程原语复用 09 真实验收栈。 | verified |

**实现中的判断点（如实登记）**：

- **按需最小栈**：design 未规定验收栈组成；按 S-01 的真实边界（Browser→Console HTTP→四张 Owner 表）只起 Console，避免为无关依赖引入 Redis/Gateway/探针耦合。
- **模块级 TENANT 为随机值**：`overview-acceptance-<uuid>` 每次 import 生成，故清理必须在**同一进程内**的 fixture 收尾完成（`finally` 中调用），不能依赖后续运行补清；已实测无遗留租户。
- **`control.console_account` 属本模块自带的清理项**：见上 RED 第 ② 轮。
- B-202: verified — automated command passed; run_id=4fda0745ceba453999b64edce50732ea (confirmed_by: runner)
- B-202: verified — automated command passed; run_id=3b43869e2f274209a7997dab4476bdac (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-26] started
- [2026-09-26] completed (done)
## TASK-004: 后端场景真实验收（S-01 + 无 N+1）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 12-overview-dashboard.backend.design.md#2.4 验收条件, 12-overview-dashboard.backend.design.md#4.2 风险识别
- **Spec-Refs**:
- **Acceptance-Refs**: S-01
- **Files**: `tests/acceptance/overview/test_overview_acceptance.py`
- **Estimate**: 半天级；外部服务启动与验收等待另计

### Description

S-01 的最终验收：以真实 Console HTTP 打通「一次聚合查询 → 四张 Owner 表」，断言一次请求即返回全部 4 KPI 与两组列表，并给出**无 N+1** 的服务端证据（RISK-01）与「请求时计算、不读快照/缓存」证据（RISK-03）。

### Checklist

- [x] [S-01][E2E] 以真实 Console HTTP + 真实 PostgreSQL 为边界编写用例：种入已知数量的启用 Agent/Skill、非终态 Task、ACTIVE Schedule 后，`GET /api/v1/overview` 一次返回 4 个 KPI 且数值与逐表回读一致，`recent_tasks`/`next_schedules` 各 ≤5 且排序正确。执行 argv：`["uv","run","pytest","-q","tests/acceptance/overview/test_overview_acceptance.py","-k","s01"]`。
- [x] [S-01] 断言封套键集、`recent_tasks[].status/trigger_type/delivery_status` 与 `next_schedules[].status/next_fire_at/last_fire_at` 字段齐备，时间字段匹配 `YYYY-MM-DD HH:mm:ss`。
- [x] [RISK-01] 无 N+1 证据：记录并断言单次请求内聚合 SQL 条数 ≤5（与行数无关），断言不随种子数据量增长。
- [x] [RISK-03] 断言响应为请求时计算：不读任何概览快照表/物化视图，也不依赖 Redis 缓存键（无 Redis 亦正确）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录（含实测 SQL 条数）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | 真实 Console HTTP + 四张 Owner 表(PostgreSQL) | 一次请求返回 4 KPI（与逐表回读一致）+ 两组列表（≤5、排序正确）；SQL 条数 ≤5；不读快照/缓存 | tests/acceptance/overview/test_overview_acceptance.py / S-01 | `["uv","run","pytest","-q","tests/acceptance/overview/test_overview_acceptance.py","-k","s01"]` | e2e_deferred |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | **验收类不制造实现 RED**：端点已在 TASK-001 落地，本任务只补真实验收（若未实现，登记 argv 会以 `404 COMMON_NOT_FOUND` 失败，即结构性 RED）。真实记录的**测试侧** RED：RISK-01 首轮报 `未捕获到任何 SQL，测量本身失效` —— 监听器挂在 Console 引擎上，而 `run_db` 会另起引擎，故一条语句也捕获不到；改为使用被测服务自己的 `get_engine()`/`get_session_factory()` 后测得真实条数。 | 整文件 `uv run pytest -q tests/acceptance/overview/test_overview_acceptance.py` → **3 passed**（S-01 + RISK-01 + RISK-03）；运行后无孤儿进程、无遗留租户。按流程 S-01 属 E2E，**形式上仍记 `e2e_deferred`**，终验归 verify-e2e（届时以 `--include-e2e` 复跑同一 argv）。 | `test_s01_one_request_returns_all_blocks_matching_independent_reads`：真实登录后一次 `GET /api/v1/overview` → `data` 键集恰为 `{kpis,recent_tasks,next_schedules}`；四个 KPI **与独立原始 SQL 回读逐项相等**（不经被测 API 计算）；`recent_tasks` 长度 ∈(0,5]、`create_time` 严格降序、`agent_name`/`actor_user_name` 为种子值、时间匹配 `YYYY-MM-DD HH:mm:ss`；`next_schedules` 名字序列 == 种子期望（`启用定时 1/2`）、`next_fire_at` 升序、全为 `ACTIVE`。`test_risk01_aggregate_sql_count_is_bounded_and_row_independent`：实测单次聚合 **3 条 SQL**（≤5），**追加 12 行后条数不变**（逐实体查询必然增长，故这是 N+1 的直接反证）；并断言语句只落在四张 Owner 表上、不含 `snapshot`/`redis`。`test_risk03_overview_serves_without_reachable_redis`：另起一个 `REDIS_URL=redis://127.0.0.1:1/0`（死端口）的 Console 真实进程，登录后同一端点仍 200 且 KPI/列表与正常栈**完全一致**（未因 Redis 缺失而伪造或降级），收尾确认该进程已退出。 | 真实 Console **子进程**（真实 HTTP + 真实登录会话/CSRF，非 ASGI 直连）+ 真实 PostgreSQL 独立原始 SQL 回读；种子来自 TASK-003 的真实进程栈与租户级种子。**N+1 计数**以进程内真实会话 + `before_cursor_execute` 监听完成（HTTP 层只做委托，计数对象即请求处理器的工作）——已在用例 docstring 与本表说明。 | e2e_deferred（本地已 GREEN，终验归 verify-e2e） |

**实现中的判断点（如实登记）**：

- **N+1 的判据选"条数不随行数增长"而非"条数小"**：仅断言 ≤5 不能排除"数据量小时恰好少"，故加行重测并断言条数**不变**——这才是逐实体查询的直接反证。实测 3 条（KPI 一条 + 两组列表各一条，名称在同一 SQL 内 JOIN 补齐）。
- **RISK-03 用死端口 Redis 起真实进程验证**：比静态检查"代码没 import redis"强得多——若实现把 KPI 缓存为 Redis 快照或把 Redis 当必经依赖，该进程要么起不来要么数据不一致。
- **非 E2E 部分不降级**：S-01 的 E2E 层级保持，RISK-01/RISK-03 作为其证据在同文件内以集成级补强；未把 E2E 改写成集成级。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=03cc1dfbb1634354a36669ea047e4a6d (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=2510527eb92548b2bc07fefac780dccd (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-26] started
- [2026-09-26] completed (done)
## TASK-005: 前端 service 层与类型契约

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 12-overview-dashboard.frontend.design.md#3.4 组件接口契约, 12-overview-dashboard.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: harness-frontend#RULE-front-001
- **Acceptance-Refs**: B-203, RULE-front-001
- **Files**: `apps/console-platform/frontend/src/modules/overview-dashboard/types.ts`, `apps/console-platform/frontend/src/modules/overview-dashboard/services/overviewService.ts`, `tests/frontend/test_overview_services_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

建立模块的类型契约与 service 层：`OverviewData/OverviewKpis/RecentTaskItem/NextScheduleItem` 按 design §3.4 定义，`getOverview()` 走共享 `api/client`，并把后端 snake_case 映射为前端 camelCase（`enabled_agents→enabledAgents`、`next_fire_at→nextFireAt` 等）。组件不直接消费原始 Envelope。

### Checklist

- [x] [B-203][integration] 以前端源码契约 + 真实 tsc 类型检查为边界编写用例：断言 service 位于 `modules/overview-dashboard/services/*.ts` 且 import 共享 `api/client`；组件/hooks 不 import `api/client`、不裸用 axios/fetch；映射函数存在且覆盖 design §3.4 全部字段（含 `triggerType`/`deliveryStatus` 联合类型）。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_overview_services_contract.py"]`。
- [x] [RULE-front-001][integration] 作为唯一最终负责人，验证前端 HTTP 只经服务层与 i18n 检测线。verifier argv：`["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [x] 实现或补齐：`types.ts` + `services/overviewService.ts`（映射只在 service 层，线上保持 snake_case）；`undefined` 字段不出现在请求/响应塑形中。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-203 | integration | 前端源码契约 + 真实 tsc | service 收口与 import 方向；字段映射覆盖 design §3.4；联合类型齐备 | tests/frontend/test_overview_services_contract.py / B-203 | `["uv","run","pytest","-q","tests/frontend/test_overview_services_contract.py"]` | verified |
| RULE-front-001 | integration | 前端源码契约 + 仓库检查脚本 + 真实 tsc | 组件不裸用 axios/fetch；文案只用 i18n key 且引用的键已定义；原 verifier 全部通过 | tests/frontend/test_overview_services_contract.py + 原 verifier / RULE-front-001 | `["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-203 | **结构性 RED（真实，非伪造）**：把模块目录整体移开后按登记 argv 执行 → **6 failed**，逐条均为 `AssertionError: 缺少前端模块文件：…/overview-dashboard/services/overviewService.ts`（模块尚不存在）。诚实记录：恢复文件后首轮仍有 **2 个失败，且都是我写的断言自身的 bug** —— ① `timezone` 在两个语言里同名，被「types.ts 不得出现后端命名」的检查误伤；② 我把 `_compact()` 压空白后的文本拿去比对一个带空格的期望串，恒不成立。两处均为测试缺陷，已修正，`types.ts`/`overviewService.ts` 未因此改动。 | 整文件 `uv run pytest -q tests/frontend/test_overview_services_contract.py` → **6 passed**；前端契约回归 `tests/frontend/` **209 passed**；`npx tsc --noEmit` exit 0；ruff 干净。 | `test_service_uses_shared_api_client_only`（只从 `api/client` 取共享客户端；无 axios/fetch/`create(`）；`test_service_is_read_only_single_aggregate_call`（只暴露 `getOverview`，取 `/overview`，且无任何写请求——概览是只读模块）；`test_field_mapping_lives_in_service_layer`（设计 §3.4 的 20 组 camel↔snake 字段在 service 全量落位，且 `types.ts` **不泄漏**任何后端命名）；`test_types_declare_design_contract`（四个接口 + 两个联合类型 + `OverviewKpis` 四键）；`test_service_and_types_have_no_hardcoded_copy`（去注释后字符串字面量无中文，文案走 i18n key） | 前端真实源码文本（非 mock）+ **真实 `tsc --noEmit`** 类型检查 + 仓库级 `check_frontend_api_usage.py` / `check_frontend_i18n.py`；`tsc` 覆盖 service 与 types 的真实类型解析。 | verified |
| RULE-front-001 | 同上（验收类，随 B-203 一并取证） | 联合 verifier argv 全通过：`check_frontend_api_usage.py` → `frontend api usage check OK`；`check_frontend_i18n.py` → `i18n keys OK: 701`；`npm run typecheck` → exit 0。 | 同上用例 + 三个仓库级脚本：服务层收口与 import 方向、文案只用 i18n key 且引用的键已定义、真实 tsc | 前端真实源码 + 仓库检查脚本真实输出 + 真实 `tsc`；无 mock。 | verified |

**实现中的判断点（如实登记）**：

- **类型可空性与设计略有差异**：设计 §3.4 把 `agentName` 与时间字段写成非空 `string`，但后端是 `LEFT JOIN` + 可空时间列，实际会返回 `null`。本契约按**实际出参**标注为 `string | null`（字段名与存在性不变，只是精度更诚实），避免下游误判；已在 `types.ts` 头部登记。
- **`timezone` 不构成映射**：前后端同名，故「types.ts 不含后端命名」的检查对该字段显式跳过（否则恒失败）。
- **未越界实现组件**：本任务只交付 service 层与类型契约；`KpiCards`/两个列表的 props 契约已在 `types.ts` 声明，但组件本体归 TASK-006/007，避免抢做后续任务。
- B-203: verified — automated command passed; run_id=b8c71c2b403e4a71b6ea5e95739c1418 (confirmed_by: runner)
- B-203: verified — automated command passed; run_id=df791d888c34485798210fdede4a5674 (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-26] started
- [2026-09-26] resumed (in-progress)
- [2026-09-26] completed (done)
## TASK-006: OverviewPage 容器 + KpiCards

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005
- **Source**: 12-overview-dashboard.frontend.design.md#3.3 组件设计, 12-overview-dashboard.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: B-204, RULE-ui-001, RULE-ui-detail-001
- **Files**: `apps/console-platform/frontend/src/modules/overview-dashboard/pages/OverviewPage.tsx`, `apps/console-platform/frontend/src/modules/overview-dashboard/components/KpiCards.tsx`, `apps/console-platform/frontend/src/modules/overview-dashboard/hooks/useOverview.ts`, `tests/frontend/test_overview_page_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现容器与 KPI 卡片：`useOverview` 一次加载 overview data（`{data, loading, error}`），`OverviewPage` 按页面骨架组装并渲染 4 个 KPI 卡片；每个 KPI 可点击跳转（启用 Agent→`/agents`、启用 Skill→`/skills`、后台执行中→`/tasks`、启用定时任务→`/schedules`）。

### Checklist

- [x] [B-204][integration] 以前端源码契约 + 真实 tsc + 真实构建为边界编写用例：断言页面骨架为 `PageHeader → PageSection`（不重复标题/说明块）、`KpiCards` 渲染 design §2.2 的 4 项且各自带跳转、`useOverview` 只调用一次 `getOverview()`（无按实体循环拉取）。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_overview_page_contract.py"]`。
- [x] [RULE-ui-001][integration] 作为唯一最终负责人，验证 Console 骨架与固定十项菜单口径、主展示字段即详情入口。verifier argv：`["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]`。
- [x] 实现或补齐：复用公共组件（`PageHeader/PageSection/StatusTag/DateTimeText/EntityLink`），不散落魔法颜色/间距；loading 用 Skeleton。
- [x] [RULE-ui-detail-001][integration] 作为唯一最终负责人（2026-09-26 局部 Plan 承接：前端路径曾因过宽的 `apps/**/frontend/**` 绑定该规则；pattern 收窄后绑定保留，由本任务承接）。verifier argv：`["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-204 | integration | 前端源码契约 + 真实 tsc + 真实构建 | 页面骨架正确；4 个 KPI 齐备且可跳转；单次加载无 N+1 | tests/frontend/test_overview_page_contract.py / B-204 | `["uv","run","pytest","-q","tests/frontend/test_overview_page_contract.py"]` | verified |
| RULE-ui-001 | integration | 前端源码契约 + 原 verifier 真实边界 | 页面骨架/菜单十项/主展示字段入口；原 verifier 全部通过 | tests/frontend/test_overview_page_contract.py + 原 verifier / RULE-ui-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]` | verified |
| RULE-ui-detail-001 | integration | 公共详情组件契约 + 真实 tsc + 原 verifier 真实边界 | 共享 `DetailSideSheet` 结构契约保持绿；原 verifier 全部通过 | tests/frontend/test_detail_sidesheet_contract.py + 原 verifier / RULE-ui-detail-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-204 | **结构性 RED（真实）**：页面/组件尚未落地时按登记 argv 执行 → 失败于 `缺少前端模块文件：…/pages/OverviewPage.tsx`（模块文件不存在）。实现后转为 GREEN。**另有一处真实 RED 来自既有契约**：`RULE-ui-001` 的原 verifier 报 `test_module_list_pages_use_remote_table` 失败——它把 `modules/**/*Page.tsx` 一律当列表页并要求 `RemoteTable`，而概览是**仪表盘**（无工具栏/表格/筛选），本需求引入的是第一个非列表模块页（规则原文只约束「列表页」）。 | 整文件 `uv run pytest -q tests/frontend/test_overview_page_contract.py` → **6 passed**；`RULE-ui-001` 原 verifier（shell + ui style 契约 + `npm run build`）→ **9 passed + built**；`RULE-ui-detail-001` 原 verifier → 9 passed + tsc exit 0；i18n checker → `i18n keys OK: 708`（新增 7 键）；ruff 干净。 | `test_page_uses_documented_skeleton`（`PageHeader` 先于 `PageSection`、只有一个标题块、不重复套壳）；`test_kpi_cards_reuse_shared_metric_cards`（复用公共 `MetricCards`，不自拼 `metric-card` DOM）；`test_kpi_cards_cover_design_kpis_and_link_targets`（设计 §2.2 四项齐备，标题即跳转入口且目标为 `/agents` `/skills` `/tasks` `/schedules`，全部用**字面量** i18n 键以便 checker 校验）；`test_loading_and_error_states_do_not_fabricate_values`（loading 用 Skeleton；失败分支渲染 `ErrorState` 而非 KPI 卡片——**不显示伪造的 0**）；`test_hook_fetches_once_via_service`（hook 只调一次 `getOverview()`、无 axios/fetch、含 `requestSeq` 乱序保护；页面只经 hook 取数）；`test_no_hardcoded_copy_in_module_sources`（三个文件去注释后无中文） | 前端真实源码 + **真实 `tsc --noEmit`** + **真实 `npm run build`** 产物；`RULE-ui-001` 的 shell/style 契约与 `RULE-ui-detail-001` 的公共 SideSheet 契约均实跑；无 mock。 | verified |
| RULE-ui-001 | 见上（该规则原 verifier 的失败即本任务的真实 RED 之一）。 | 原 verifier 复跑通过：shell + ui style 契约 **9 passed**、`npm run build` 成功；本模块用例 6 passed。 | 同上用例 + `test_ui_style_contract`（页面骨架/无魔法色/唯一 UI 库/列表页 RemoteTable）；**契约修正**：把「非列表模块页」显式声明并**反查其确实不含** `ModuleToolbar`/`<Table`/`PaginationFooter`，避免用「不写 RemoteTable」蒙过 | 前端真实源码 + 真实构建产物 + 仓库既有契约套件（真实执行，非跳过）。修正后的契约经 **mutation 验证**：给例外页注入 `<ModuleToolbar />` 后该用例如期失败，证明例外未被放宽成白名单。 | verified |
| RULE-ui-detail-001 | 验收类（承接项，无独立 RED）。 | 原 verifier 通过：`tests/frontend/test_detail_sidesheet_contract.py` **9 passed** + tsc exit 0。 | 原 verifier 自身的 9 条断言（公共 `DetailSideSheet` Header/Tabs 结构、`DetailGrid` 双列、名称列与操作列对齐等） | 前端真实源码 + 真实 tsc。**说明**：本模块无详情视图，该规则系由过宽 pattern 误绑后按用户决定承接；其 verifier 验证的是**公共组件**契约仍绿，不指向本需求页面行为（已在承接时如实登记该代价）。 | verified |

**实现中的判断点（如实登记）**：

- **修正了既有契约的过宽判定**（见上表 RULE-ui-001）：规则原文只约束列表页，测试却把 `*Page.tsx` 一律当列表页。修法是**精确化而非放宽**——例外页必须显式声明，且反查其不含任何列表构件；已用 mutation 证明例外不能被滥用。
- **KPI 卡片复用公共 `MetricCards`**（设计 §3.3 只写"模块内 CMP-02"，但公共组件已存在且形态一致），避免再造一套卡片壳；卡片标题即跳转入口符合 §3.3.1。
- **i18n 键用字面量而非模板串**：把键名拼成模板串（`overview.kpi.` 加变量那种）虽更短，但 `check_frontend_i18n.py` 只扫字面量键，用模板串会让这 4 个键**逃过校验**；故写 4 处字面量。
- **已在 TASK-006 内加入所需词条**（overview.title/subtitle/loadFailed + 4 个 KPI），否则页面会渲染原始键；TASK-009 负责补齐其余词条并落 i18n 契约。
- B-204: verified — automated command passed; run_id=e90202e82ec3435eac210f1ba0d1aada (confirmed_by: runner)
- B-204: verified — automated command passed; run_id=39e0c0b36ff545ccaeb80bced35f27b1 (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-26] started
- [2026-09-26] completed (done)
## TASK-007: 最近任务 / 下一批定时 / 运行关系卡片

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: 12-overview-dashboard.frontend.design.md#3.3 组件设计, 12-overview-dashboard.frontend.design.md#3.4 组件接口契约, 12-overview-dashboard.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: B-205
- **Files**: `apps/console-platform/frontend/src/modules/overview-dashboard/components/RecentTaskList.tsx`, `apps/console-platform/frontend/src/modules/overview-dashboard/components/NextScheduleList.tsx`, `apps/console-platform/frontend/src/modules/overview-dashboard/components/RuntimeRelationCard.tsx`, `tests/frontend/test_overview_lists_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现两个运营列表与静态运行关系卡片：`RecentTaskList`（Task ID 主展示字段可打开详情、`查看全部 →` 到 `/tasks`）、`NextScheduleList`（Schedule 名称可打开详情、`查看全部 →` 到 `/schedules`）、`RuntimeRelationCard`（纯静态，说明 IM→Agent→Runtime→ExecutionRouter→Worker/DB/Gateway）。展示组件 props-in / events-out，API 与路由状态由 Page/Hook 管理。

### Checklist

- [x] [B-205][integration] 以前端源码契约 + 真实 tsc 为边界编写用例：断言两列表各渲染 ≤5 行、主展示字段可打开详情、空态为 `Empty` + 查看全部、`RuntimeRelationCard` 无 props 且为纯静态文案（取自词条）。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_overview_lists_contract.py"]`。
- [x] 复用 `StatusTag`（Task 状态/投递状态、Schedule 状态）与 `DateTimeText`（`next_fire_at`/`last_fire_at`/`create_time` 等），不裸渲染枚举值或原始时间串。
- [x] 实现或补齐：两个列表的 props 形状与 design §3.4 一致（`onOpenTask/onViewAll`、`onOpenSchedule/onViewAll`）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-205 | integration | 前端源码契约 + 真实 tsc | 两列表 ≤5 行、主展示字段入口、空态与查看全部；静态卡片无 props | tests/frontend/test_overview_lists_contract.py / B-205 | `["uv","run","pytest","-q","tests/frontend/test_overview_lists_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-205 | **真实 RED（两处，均在测试/实现侧暴露而非伪造）**：① 首轮 `test_blocks_are_previews_not_list_pages` 失败——我的**负向断言扫了注释**，而文件文档注释里正提到 `RemoteTable`（说明"为何不用它"），故改为只对**去注释后的代码**做负向断言；② **`tsc` 抓出一处真实类型错误**：`DateTimeText` 不接受 `null`，而 `lastFireAt` 可空（`string | null`）→ 改为空值渲染 `common.empty`。 | 整文件 `uv run pytest -q tests/frontend/test_overview_lists_contract.py` → **6 passed**；`npx tsc --noEmit` **exit 0**；i18n checker → `i18n keys OK: 713`（本轮新增 5 键，列头/状态词条全部复用既有 `task.columns.*`/`task.status.*`/`schedule.columns.*`/`schedule.status.*`）；ruff 干净。 | `test_blocks_are_previews_not_list_pages`（Semi `Table` 且 `pagination={false}`；**不用** `RemoteTable`/`ModuleToolbar`；不在前端 `slice` 截断行数——≤5 由后端 LIMIT 保证）；`test_main_field_opens_detail_via_props`（Task ID / Schedule 名称经 `onOpenTask`/`onOpenSchedule` 上抛，预览块自身不导航——props-in/events-out）；`test_empty_state_and_view_all`（空态用公共 `EmptyState`；「查看全部」在空态与有数据时都可点）；`test_shared_components_reused`（`StatusTag`/`DateTimeText`/`EmptyState` 复用，不自造）；`test_runtime_relation_card_is_static_and_i18n_driven`（`export function RuntimeRelationCard()` **无 props**、不取数、文案取自 `overview.runtimeRelation.*`）；`test_no_hardcoded_copy_or_http_client`（去注释后无中文硬编码、无 axios/fetch） | 前端真实源码 + **真实 `tsc --noEmit`**（正是它抓出了可空时间字段的类型错误）；无 mock。 | verified |

**实现中的判断点（如实登记）**：

- **预览块用 Semi `Table` 而非 `RemoteTable`**：规则/契约要求 `RemoteTable` 的是**列表页**（左上操作 + 右上筛选 + 右下分页）；本模块的两块是仪表盘内 ≤5 行预览，用 `RemoteTable` 会带来无意义的分页与工具栏。已在组件注释与用例里写清这一区分，并断言预览块**不得**使用 `RemoteTable`/`ModuleToolbar`。
- **列头与状态词条全部复用既有 key**（`task.columns.*`、`task.status.*`、`task.delivery.*`、`schedule.columns.*`、`schedule.status.*`），本轮只新增 5 个块级词条（viewAll / 两个空态 / 关系卡标题与说明），避免与兄弟模块重复文案。
- **状态标签沿用 task-schedule 模块的 `STATUS_COLORS` 口径**（含把 `SUCCEEDED` 显示为 `task.status.COMPLETED` 的"已完成"文案）。**登记一处跨模块口径存疑**：任务列表模块的色表用 `COMPLETED`，而后端 `task_execution.status` 实测会返回 `SUCCEEDED`；本处已显式把两者映射到同一文案，但"到底哪个是权威词表"宜由 task-schedule 模块统一裁定（不在本任务范围）。
- **区块级 ErrorState 未实现**：设计 §3.6 给两个列表标了"区块 ErrorState + 重试"，但概览是**单次聚合请求**（E-03 定义为"聚合接口失败 → 整体 ErrorState + 重试"），块级失败在构造上不可能独立发生，故错误态由页面统一承载（TASK-008）。此处按"不写死代码"处理并登记差异。
- B-205: verified — automated command passed; run_id=6e4444f9bc7b44a9b666fc09c3a6594b (confirmed_by: runner)
- B-205: verified — automated command passed; run_id=f6dbb9945503443e83adc7c7fcdaa527 (confirmed_by: runner)
- B-205: verified — automated command passed; run_id=dace0fa34c4e441aa75fb827a0d45536 (confirmed_by: runner)

### Log
- [2026-09-26] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-008: 路由接入与 UI 状态

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-007
- **Source**: 12-overview-dashboard.frontend.design.md#3.2 页面与路由结构, 12-overview-dashboard.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: E-03, B-206
- **Files**: `apps/console-platform/frontend/src/App.tsx`, `apps/console-platform/frontend/src/modules/overview-dashboard/pages/OverviewPage.tsx`, `tests/frontend/test_overview_routing_contract.py`, `e2e/tests/overview-dashboard.spec.ts`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

把概览挂到 Console 首页路由 `/`（固定十项菜单的第一项），并按 design §3.6 落实三态：KPI 区 loading 用 Skeleton、整页 error 用公共 `ErrorState` + 重试（**不伪造 0**）、列表区空态用 `Empty` + 查看全部。E-03 的失败路径由 TASK-010 的 Playwright spec 承接（真实失败以路由拦截制造）。

### Checklist

- [ ] [B-206][integration] 以前端源码契约（路由表 + 菜单选中）+ 真实构建为边界编写用例：断言 `/` 挂载 `OverviewPage` 且路由表第一项即概览、菜单选中态正确、页面不重复套壳（`AppLayout` 由路由承载）。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_overview_routing_contract.py"]`。
- [ ] [E-03][integration] 聚合接口失败 → 整体 `ErrorState` + 重试，**不伪造 0**；真实失败由 `e2e/tests/overview-dashboard.spec.ts` 的 `E-03` 块以路由拦截承载（失败/边界路径允许路由拦截，须在 manifest 中登记为路由拦截场景）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-03\""]`。
- [ ] 实现或补齐：三态文案全部取自词条；重试复用同一取数出口。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-206 | integration | 前端源码契约（路由+菜单）+ 真实构建 | `/` 挂载概览且菜单第一项选中；不重复套壳 | tests/frontend/test_overview_routing_contract.py / B-206 | `["uv","run","pytest","-q","tests/frontend/test_overview_routing_contract.py"]` | planned |
| E-03 | integration | Browser→overview API 失败路径（真实 HTTP 失败由拦截制造） | 整体 ErrorState + 重试；不显示伪造的 0 | e2e/tests/overview-dashboard.spec.ts / E-03 | `["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-03\""]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-26] created (draft)

---

## TASK-009: i18n 词条与语言切换覆盖

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-006
- **Source**: 12-overview-dashboard.frontend.design.md#3.5 状态与数据流, 12-overview-dashboard.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: B-207, RULE-i18n-001
- **Files**: `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`, `tests/frontend/test_overview_i18n_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

为概览模块补齐 zh-CN/en-US 两侧词条（KPI 标题、列表列名与状态、运行关系说明、空/错/重试文案），并保证语言切换后即时生效。

### Checklist

- [ ] [B-207][integration] 以两侧词条实际内容 + 前端源码契约为边界编写用例：断言概览模块全部文案键在 zh-CN 与 en-US 齐平且非空、值真实（非键名回显）、模块源码无硬编码中文；动态键（若有）变体齐备。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_overview_i18n_contract.py"]`。
- [ ] [RULE-i18n-001][integration] 作为唯一最终负责人，验证 zh-CN/en-US 双侧覆盖与「新增业务只加词条」。verifier argv：`["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`。
- [ ] 语言切换安全：组件文案经 `useTranslation()` 每次渲染取得，不缓存译文、不直接 import i18n 实例。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-207 | integration | 两侧词条真实内容 + 前端源码契约 | 键集齐平非空、值真实、无硬编码中文、切换即时生效 | tests/frontend/test_overview_i18n_contract.py / B-207 | `["uv","run","pytest","-q","tests/frontend/test_overview_i18n_contract.py"]` | planned |
| RULE-i18n-001 | integration | 原 verifier 真实边界 + 仓库词条检查脚本 | 双侧覆盖；新增业务只加词条；原 verifier 全部通过 | tests/frontend/test_overview_i18n_contract.py + 原 verifier / RULE-i18n-001 | `["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-26] created (draft)

---

## TASK-010: 前端 E2E 验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-008
- **Source**: 12-overview-dashboard.frontend.design.md#2.4 验收条件, 12-overview-dashboard.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: S-02, S-03, S-04, E-02, E-04, B-208
- **Files**: `e2e/playwright.overview-dashboard.config.ts`, `e2e/tests/overview-dashboard.spec.ts`, `tests/frontend/test_overview_e2e_fixture_contract.py`, `tests/e2e/seed_overview.py`
- **Estimate**: 半天级；外部服务启动与验收等待另计

### Description

S-02/S-03/S-04 与 E-02/E-04 的最终验收：真实 Chromium → 真实 Console（真实登录）→ 真实 PostgreSQL，按场景 ID 组织 `-g` 可选的 spec；配置按 `--grep` 派生端口/租户/产物 root 隔离，`workers: 1`，运行后零残留。

### Checklist

- [ ] [S-03][E2E] Browser(Chromium)→Console 首页：4 个 KPI 与最近任务/下一批定时**一次加载**完成；断言浏览器侧未按实体循环拉取（仅一次 `/api/v1/overview`）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-03\""]`。
- [ ] [S-02][E2E] 点击最近任务 / 下次调度条目 → 进入对应详情或所属模块（菜单选中正确）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-02\""]`。
- [ ] [S-04][E2E] 点击「查看全部」→ 进入 `/tasks` / `/schedules` 且菜单选中正确。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-04\""]`。
- [ ] [E-02][integration] 跳转目标 ID 已失效/无权限 → 目标页展示 `ErrorState` 或回退列表，**不白屏、不伪造数据**。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-02\""]`。
- [ ] [E-04][integration] 同上（前端侧路由与目标页表现）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-04\""]`。
- [ ] [B-208][integration] 以配置与 spec 源码 + 运行后环境为边界编写用例：断言配置含 `workers: 1`、按 `--grep` 派生端口偏移与独立租户/产物 root、spec 用例标题含场景 ID（支持 `-g`）。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_overview_e2e_fixture_contract.py"]`。
- [ ] 运行后无 uvicorn/`muad_*.main`/chromium 残留，租户残留为 0，仓库 `.data/artifacts` 未变。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | E2E | 真实 Chromium + 真实 Console + 真实 PostgreSQL | 条目跳转落到目标详情/模块，菜单选中正确 | e2e/tests/overview-dashboard.spec.ts / S-02 | `["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-02\""]` | planned |
| S-03 | E2E | 真实 Chromium + 真实 Console 首页 | 4 KPI + 两组列表一次加载；浏览器侧无按实体循环拉取 | e2e/tests/overview-dashboard.spec.ts / S-03 | `["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-03\""]` | planned |
| S-04 | E2E | 真实 Chromium + 路由 | 查看全部进入 tasks/schedules 且菜单选中正确 | e2e/tests/overview-dashboard.spec.ts / S-04 | `["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"S-04\""]` | planned |
| E-02 | integration | Browser→Router→目标页（真实软删/越权目标） | 目标页 ErrorState 或回退列表；不白屏、不伪造数据 | e2e/tests/overview-dashboard.spec.ts / E-02 | `["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-02\""]` | planned |
| E-04 | integration | Browser→Router→目标页（目标 ID 失效/无权限） | 同上（前端侧表现一致） | e2e/tests/overview-dashboard.spec.ts / E-04 | `["bash","-lc","cd e2e && npx playwright test --config playwright.overview-dashboard.config.ts -g \"E-04\""]` | planned |
| B-208 | integration | Playwright 配置/spec 源码 + 运行后真实环境 | `workers: 1`；端口/租户/产物 root 隔离；标题含场景 ID；零残留 | tests/frontend/test_overview_e2e_fixture_contract.py / B-208 | `["uv","run","pytest","-q","tests/frontend/test_overview_e2e_fixture_contract.py"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-26] created (draft)

---

## TASK-011: 收口：场景、规则、证据与仓库级 verifier

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-010
- **Source**: 12-overview-dashboard.backend.design.md#2.4 验收条件, 12-overview-dashboard.backend.design.md#3.3 接口设计, 12-overview-dashboard.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-209, RULE-test-001
- **Files**: `tests/overview_dashboard_inventory.py`
- **Estimate**: 半天级

### Description

按 11-audit-observability 的收口模式建立仓库级清单用例：核对本需求「覆盖表唯一负责人且除本收口任务外全终态」「manifest 与覆盖表一致」「owner 的 Acceptance-Refs 登记」「终态场景在 owner Acceptance Evidence 中登记」「契约行全终态」「done/verified 任务零未勾项」，并作为 RULE-test-001 的仓库级 verifier 归宿。

### Checklist

- [ ] [B-209][integration] 以 pytest 用例收集/运行 + 任务文档与 manifest 为边界编写用例：覆盖表唯一负责人且除本任务外全终态、manifest 与覆盖表 id/level/owner/status 一致、每条 owner 的 Acceptance-Refs 登记、终态场景在 owner Evidence 中登记、契约行全终态、done/verified 任务零未勾项；E2E 命令指向真实在盘套件且无跳过标记。执行 argv：`["uv","run","pytest","-q","tests/overview_dashboard_inventory.py","-k","b209"]`。
- [ ] [RULE-test-001][E2E] 作为唯一最终负责人，承接仓库级真实 E2E 边界（真实 HTTP/PostgreSQL/Browser），并登记原 verifier argv。
- [ ] 以 mutation 验证断言有牙（在内存副本/临时目录上做变异：插入未勾项、改契约行为非终态、删证据登记、E2E 命令塞 mock 或指向不存在文件，均应如期失败）。
- [ ] 补齐场景/规则/证据缺口后执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN 与真实边界记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-209 | integration | 任务文档 + manifest + 在盘套件存在性 | 覆盖表唯一负责人且除本任务外全终态；manifest 一致；refs/evidence/契约行闭合；零未勾项 | tests/overview_dashboard_inventory.py / B-209 | `["uv","run","pytest","-q","tests/overview_dashboard_inventory.py","-k","b209"]` | planned |
| RULE-test-001 | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Browser）+ 原 verifier 真实边界 | 跨 API/DB/Browser 关键流程真实 E2E；分层不降级；原 verifier 全部通过 | tests/acceptance + e2e / RULE-test-001 | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-26] created (draft)
