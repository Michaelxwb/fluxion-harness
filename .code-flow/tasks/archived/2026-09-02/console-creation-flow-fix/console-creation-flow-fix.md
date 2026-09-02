# Tasks: console-creation-flow-fix

- **Source**: 2026-09-01 console-productization 全面 review 新发现 P0（memory: console-productization-rework-done）+ 2026-09-02 归档前抽查证据
- **Created**: 2026-09-02
- **Updated**: 2026-09-02

## Proposal

修复 Console 创建流断链的**列表半边**：真实后端 `GET /api/v1/resources` 只返回 PUBLISHED，新建 DRAFT 资源在管理界面不可见，而编辑入口仅存在于列表行按钮 → 新建 Agent（及其他新建资源）在 UI 不可达。detail 半边（无 version 的 get 返回任意状态最新行）已修复，勿重复。

修复方向：RegistryStore 契约新增 `list_current_resources`（每资源一行、任意状态最新版本），Console 列表链路切换到该语义；resolver/runtime 消费的 published-only `list_resources` 保持不变（workspace_app / eval_app / migration 三个消费方语义不动）。前端创建成功后携带 `resourceId` 直达编辑器。以真实 HTTP 边界测试钉住契约，消除「inMemory 全绿、真实后端断链」的测试假象（inMemoryConsoleApi 本就是「任意状态最新版」语义——本任务让真实后端向意图契约收敛，而非改 inMemory）。

### Alignment

- **Scope**: RegistryStore 新方法 + Console 列表语义切换 + 前端创建后跳编辑器 + 真实边界测试（CF-S-01/CF-E-01/CF-S-02）
- **Non-goals**: 发布确认弹窗、AgentEditor 发布后 stale draft 引用、rollback UI 缺失、`:test-connection` SSRF/stdio 门、tenant policy 存量迁移（均为另行立项的遗留项）
- **Acceptance**: 3 个场景全过；Console 列表对 draft-only 资源可见且发布后状态翻转；旧 published-only 语义有回归钉

### 根因证据（2026-09-02 抽查确认）

- `backend/src/fluxion/registry/resource_sqlalchemy.py:175-183`：`list_resources` / `list_all_resources` 均委托 `_list_published_resources`，`status == PUBLISHED` 过滤在 `:240`
- `backend/src/fluxion/services/console_resources.py:169-182`：Console 服务直接透传 published-only
- `frontend/apps/console/src/services/httpConsoleApi.ts:76-84`：列表请求无任何状态参数
- `frontend/apps/console/src/pages/agents/AgentsPage.tsx:64-67`：`onCreated` 忽略 `resourceId`，仅关弹窗 + 刷新列表
- detail 半边已修：`backend/src/fluxion/services/console_resources.py:126-150`（无 version 的 get 经 `list_versions` 取任意状态最新行）
- inMemory 意图契约：`frontend/apps/console/src/services/inMemoryConsoleApi.ts:112-125`（`latestResource` 任意状态）

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| CF-S-01 | 本文件 Proposal | E2E | Console HTTP API → Registry Store | TASK-001 | verified |
| CF-E-01 | 本文件 Proposal | contract | Registry Store（SQLite/PG） | TASK-001 | verified |
| CF-S-02 | 本文件 Proposal | E2E | Browser → Router → Editor | TASK-001 | verified |

---

## TASK-001: Console 列表「当前版本（任意状态）」语义 + 创建直达编辑器

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 本文件 Proposal + 根因证据
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: CF-S-01, CF-E-01, CF-S-02

### Description

RegistryStore 新增 `list_current_resources`（`kind: ResourceKind | None`、tenant 必带、分页；每资源取任意状态最新版本一行），Console 服务 `list_resources` / `list_all_resources` 切换到它；旧 `list_resources` 的 published-only 语义保留给 runtime/resolver 链路。前端 `AgentsPage` 的 `onCreated` 携带 `resourceId` 跳转 `/build/agents/:resourceId/edit`（编辑器 getResource 任意状态 + `:working-draft` 复用 draft 均已就绪，创建即编辑闭环）。studio 列表经同一 `ConsoleApplicationService` 自动获得新语义，无需单独改动。

### Checklist

- [x] [CF-S-01][RED] 修改生产代码前，按 Console HTTP API → Registry Store 真实边界编写 `backend/tests/integration/test_console_creation_flow.py` 并记录 RED：真实 console stack（ASGITransport + SQLiteRegistryStore）`POST /api/v1/resources` 建 agent_definition DRAFT → `GET /api/v1/resources?resource_type=agent_definition` 断言包含该资源且 `status=DRAFT`（当前缺失即 RED）；fixture 需 `runtime_helpers.seed_model_definition` 预置 MODEL_PROVIDER + MODEL_DEFINITION
- [x] RegistryStore 契约新增 `list_current_resources`：`backend/src/fluxion/registry/store.py` Protocol + sqlalchemy 实现（复用 `_list_published_resources` 窗口模式：去 PUBLISHED 过滤、partition by (kind, resource_id)、**版本语义排序**防 `"v10" < "v9"` 字典序陷阱——参照前端 VersionHistory 的语义排序实现）
- [x] [CF-E-01] 契约测试（`backend/tests/contract/test_registry_store.py`，SQLite/PG 参数化）：draft-only 资源出现在 `list_current_resources`；published v3 + draft v4 → 当前行 v4(DRAFT)；v9/v10 取 v10；旧 `list_resources` 保持 published-only（回归钉，保护 workspace_app / eval_app / migration 消费方）
- [x] `console_resources.py` 的 `list_resources` / `list_all_resources` 切换 `list_current_resources`；`api/console.py` 响应形状不变（envelope / 分页字段不动）
- [x] [verifier] RULE-fluxion-console-api-001：列表仍走统一 envelope + request_id，业务 Handler 无手写响应
- [x] [verifier] RULE-backend-database-001：新 store 方法进 Contract Test，`scripts/run_registry_contract_tests.py` 通过（SQLite + 本地真实 PG 契约 26 passed）
- [x] 前端：`AgentsPage` 的 `onCreated` 改为 `(resourceId) => navigate(\`/build/agents/${resourceId}/edit\`)`；列表 DRAFT 行经既有 StatusTag 状态列呈现，不新增组件
- [x] [CF-S-02][E2E] vitest：创建弹窗提交后断言路由跳转 `/build/agents/:id/edit`（inMemory 已列 draft，补 navigate 断言）
- [x] 回归：`test_working_draft` / `test_validate_publish` / `test_console_http_product_contract`（total 2→3 按新语义更新）/ studio / workspace / plugin_publish_validation / production_boundaries / secret_governance / console_resource_benchmark 全过 + console vitest 96 + 变更文件 ruff/mypy 清零
- [x] [verifier] RULE-frontend-component-001：容器/展示分层不变，创建跳转复用既有路由与组件，无重复实现

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| CF-S-01 | E2E | Console HTTP API、Registry Store | draft 建后列表可见（status=DRAFT）；`:working-draft` 复用 draft v1 不重复 fork；发布后列表 status=PUBLISHED | `backend/tests/integration/test_console_creation_flow.py` | `.venv/bin/python -m pytest backend/tests/integration/test_console_creation_flow.py` | verified |
| CF-E-01 | contract | Registry Store（SQLite/PG） | draft-only 可见；published v3 + draft v4 → 当前行 v4(DRAFT)；v9/v10 语义排序；旧 `list_resources` 仍 published-only | `backend/tests/contract/test_registry_store.py` | `.venv/bin/python scripts/run_registry_contract_tests.py` | verified |
| CF-S-02 | E2E | Browser、Router、Editor | 创建提交后跳转 `/build/agents/:id/edit`；编辑器可保存/发布 | `src/pages/agents/__tests__/agents-page.e2e.test.tsx` | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| CF-S-01 | FAIL: `AssertionError: 新建 draft Agent 必须出现在 Console 列表（CF-S-01 修复点）`（1 failed in 0.16s） | PASS: 1 passed | `test_CF_S01_draft_agent_visible_in_list_until_published` L121-138：`draft_row["status"] == "draft"`；`:working-draft` 返回 v1(draft) 复用不 fork；发布后 `published_row["status"] == "published"` | real `console_stack`（ASGITransport + SQLiteRegistryStore + 真实 HTTP 请求链），ADR-A008 三层链（SECRET/MODEL_PROVIDER/MODEL_DEFINITION）全发布 | verified |
| CF-E-01 | FAIL: `AttributeError: 'SQLiteRegistryStore' object has no attribute 'list_current_resources'`（1 failed, 12 passed） | PASS: 26 passed（SQLite + PostgreSQL 双跑） | `test_CF_E01_list_current_resources_current_version_any_status`：current 含 solo(1)/multi(4)/ver(10) 全 DRAFT、跨 kind kind=None 4 条；`list_resources` published-only 回归钉仅 multi(3)/ver(9) PUBLISHED | store 契约参数化 `_store_params()`：SQLite 恒跑 + `run_registry_contract_tests.py` 在本地 Docker 跑真实 PostgreSQL | verified |
| CF-S-02 | FAIL: `Unable to find a label with the text of: 智能体编辑器`（2 failed, 11 passed——更新后的 F-S-03 用例 + 新增 CF-S-02 用例） | PASS: 96 passed（console vitest 全套） | `CF-S-02 创建直达编辑器`：提交后 `findByLabelText("智能体编辑器")` + `getByDisplayValue("数据分析助手")` + 保存/发布按钮；F-S-03 用例 2 同断言 | real Router(MemoryRouter `/build/agents`) → `AgentsPage.onCreated(resourceId)` → `navigate` → `AgentEditorPage`（in-memory ConsoleApi createResource 返回 resourceId，无 mock 绕过 Router/Editor） | verified |

### Log

- [2026-09-02] created (draft)：源自 2026-09-01 全面 review 新发现 P0（创建流断链）+ 2026-09-02 console-productization 归档前抽查确认列表半边未修（detail 半边已修）。起草时已核实：store 层 `list_resources` 另有 workspace_app / eval_app / migration 三个消费方，语义不可原地修改，故走新增方法；inMemoryConsoleApi 任意状态语义为意图契约，修复方向为后端收敛。
- [2026-09-02] started (in-progress)：bind 3 specs + plan applications → refresh → Start Gate pass → active start（baseline HEAD=e0ab6b4，console-productization 已于 13:40 提交、工作区干净、owned_paths 空）→ spec session 写入 _session/task-console-creation-flow-fix.md
- [2026-09-02] RED 记录（3/3 命中预期缺陷）：CF-S-01 列表不可见；CF-E-01 `list_current_resources` 缺失；CF-S-02 无跳转编辑器
- [2026-09-02] 实现 + GREEN：store 层共享窗口函数参数化 `published_only`（新增 `list_current_resources`，版本语义排序防 `v10<v9`）；console_resources 两个列表入口切新语义；前端 `onCreated(resourceId)` → navigate 编辑器。CF-E-01 契约测试 SQLite+PG 双过（26 passed）；`test_console_http_product_contract` 的 total 2→3 按新语义更新（invalid-mcp draft 进列表属有意变更）
- [2026-09-02] completed (done)
