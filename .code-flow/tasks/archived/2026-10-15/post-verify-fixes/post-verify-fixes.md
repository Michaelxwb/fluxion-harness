# Tasks: post-verify-fixes

- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.7 API 与 Console（记忆重设计终验遗留项收口）
- **Created**: 2026-10-15
- **Updated**: 2026-10-16
- **Plan status**: draft；阶段裁决以 CLI 输出为准

## Proposal

收口记忆重设计需求终验阶段发现的三个**非该需求范围**的遗留（当时按纪律只登记未修）：

1. 设置页版本历史面板缺 `activeTab` —— 与 `MemoryHistorySideSheet` 同型缺陷（Pane 恒 `inactive` ⇒ 面板打开是空壳），且无测试覆盖。
2. `e2e/tests/user-detail-layout.spec.ts` 仍按已删除的旧记忆表（`Memory`/`source_type`/`USER_EXPLICIT`）构造 mock 与断言 —— 跑 user-identity 浏览器域会红。
3. `/new` 后旧会话被后台 `MEMORY_CONSOLIDATED` append 顶回「最新会话」：WS 入站不带会话指针，Runtime 按 `Conversation.update_time` 最新解析；后台事件 bump 旧会话的 `update_time` ⇒ 下一条消息落回旧会话。

三项互相独立；2、3 是事实修正（测试/查询按新契约与真实语义），1 是产品缺陷修复 + 防同类回归的静态守卫。

## TASK-001: 设置版本历史面板 activeTab 修复与详情壳静态守卫

- **Status**: verified
- **Priority**: P1
- **Depends**: 
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/settings/components/VersionHistoryPanel.tsx, tests/frontend/test_detail_sidesheet_contract.py

### Description

`VersionHistoryPanel` 用 `DetailSideSheet` + 单个 `TabPane itemKey="history"` 但未传 `activeTab` ⇒ Semi Tabs 无 activeKey，Pane 恒 `inactive`（`display:none` + `aria-hidden`）⇒ 设置页版本历史面板打开是空壳（与 TASK-035 修的 `MemoryHistorySideSheet` 同型）。补 `activeTab="history"`，并在详情壳契约测试加**静态守卫**：`DetailSideSheet` 的 `TabPane` 调用点必须显式给 `activeTab`，防同类回归。

### Checklist

- [x] 先写静态守卫 RED（扫源码断言 `DetailSideSheet` 内 `TabPane` 必带 `activeTab`；当前 `VersionHistoryPanel` 命中失败）。
- [x] `VersionHistoryPanel` 补 `activeTab="history"`；不改其它行为。
- [x] GREEN：守卫测试 + `tests/frontend` 全绿 + `npm --prefix apps/console-platform/frontend run typecheck`。
- [x] 不引入新 i18n 键、不改共享组件语义。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit | 真实源码树（静态契约） | `DetailSideSheet` 的每个 `TabPane` 调用点都显式给 `activeTab`；`VersionHistoryPanel` 面板 Pane 可激活 | tests/frontend/test_detail_sidesheet_contract.py | ["uv","run","pytest","-q","tests/frontend/test_detail_sidesheet_contract.py"] | verified |

### Acceptance Evidence

**RED**（守卫先于修复）：`uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py::test_every_detail_sidesheet_usage_declares_active_tab` → **1 failed**，唯一命中 `apps/console-platform/frontend/src/modules/settings/components/VersionHistoryPanel.tsx: <DetailSideSheet visible={visible} title={t('settings.history.title')} onCancel={onClose}>`（全仓 16 个调用点中唯一缺 `activeTab` 的），日志 `/tmp/postverify/task001/red-guard.log`。

**GREEN**（修复后）：

- 守卫断言位置 `tests/frontend/test_detail_sidesheet_contract.py:71-82`（`_detail_sidesheet_usages` 扫全仓 `*.tsx` 的 `<DetailSideSheet` 开标签，`_jsx_opening_tag` 按花括号深度容忍箭头函数 props；每个开标签必须 `\bactiveTab\s*=`）：`uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py` → **10 passed**，`/tmp/postverify/task001/green-guard.log`。
- 修复：`VersionHistoryPanel.tsx` 的 `DetailSideSheet` 补 `activeTab="history"`（与 TASK-035 的 `MemoryHistorySideSheet` 同款修法，含原因注释）；未改共享组件语义、未新增 i18n 键。
- 回归：`uv run pytest -q tests/frontend` → **340 passed**（较上轮 339 + 本守卫 1），`/tmp/postverify/task001/green-frontend-tests.log`；`npm --prefix apps/console-platform/frontend run typecheck` → EXIT=0，`/tmp/postverify/task001/green-typecheck.log`。
- RULE-front-001 verifier 链实跑全绿（4 passed → api usage OK → i18n keys OK: 1163 → typecheck EXIT=0），`/tmp/postverify/task001/rule-front-001.log`；RULE-ui-001 verifier 链实跑全绿（9 passed → build ✓ built in 3.86s），`/tmp/postverify/task001/rule-ui-001.log`。

### Log

- [2026-10-15] created (draft)。
- [2026-10-15] started
- [2026-10-15] RED（守卫命中 VersionHistoryPanel）→ 补 `activeTab="history"` → 守卫 10 passed、tests/frontend 340 passed、typecheck 与 RULE-front-001/RULE-ui-001 verifier 链全绿。
- [2026-10-15] completed (done)

---

## TASK-002: user-identity 浏览器域 spec 存量漂移修复

- **Status**: verified
- **Priority**: P1
- **Depends**: 
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: e2e/tests/user-detail-layout.spec.ts, apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx

### Description

该 spec 仍 import 已删除的 `Memory` 类型、按 `source_type`（`USER_EXPLICIT`/`AGENT_INFERRED`/`FUTURE_SOURCE`）构造 4 列记忆表 mock 并断言来源列 —— 记忆 Tab 已在记忆重设计中拆到 `MemoryTab`（author/status/version/动作列），该 spec 属存量漂移。按现状修：记忆相关断言改为新契约，保持该 spec 对**布局**的覆盖意图。

执行期补充事实：① 该 spec 的**真实所属域是 `form-layout`**（`playwright.form-layout.config.ts` 的 testMatch 含 `user-detail-layout.spec.ts`，CI 13 域之一）；`playwright.user-identity.config.ts` 的 testDir 为 `./tests/user-identity`、**不收集**本 spec（其命令实跑 6 passed，见 Evidence）。② 改断言后本 spec 的 `expectContentFits` 实测暴露 `MemoryTab` 新表两处列宽缺陷（updated 152 装不下 142.2px 的日期、author 120 装不下 119px 的来源值），按「保持布局覆盖意图、不弱化断言」修组件列宽：author 120→144、status 152→140、updated 152→176。

### Checklist

- [x] 先跑 RED（该 spec 的真实所属域）：`npm --prefix e2e test -- --config playwright.form-layout.config.ts user-detail-layout.spec.ts` → **12 failed / 24 passed**（列数 4≠6、来源列断言错位；`playwright.user-identity.config.ts` 不收集本 spec，其 6 passed 见 Evidence）。
- [x] 按新契约改 mock 与断言（`MemoryItem`/author 列/6 列/逐按钮可见性）；不删覆盖意图、不引入 mock 之外的旁路。
- [x] 修复 spec 暴露的 `MemoryTab` 列宽缺陷（author 120→144、status 152→140、updated 152→176），内容列保持 ≥120；不改共享组件语义。
- [x] GREEN：form-layout 域连续两次全绿（209 passed ×2）+ 过滤 spec 两次 36 passed；`npm --prefix apps/console-platform/frontend run build` 通过；agent 域 E-04（MemoryTab 回归）11 passed。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | E2E | browser，真实构建产物（vite preview 跑 build 产物）；布局 fixture 经 `page.route` 改写 API（该 spec 既有形态） | 用户详情布局契约按新契约成立；无 `source_type`/旧 `Memory` 类型残留 | e2e/tests/user-detail-layout.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.form-layout.config.ts"] | verified |

### Acceptance Evidence

**RED**（真实失败原文；命令 = 该 spec 的真实所属域）：

- `npm --prefix e2e test -- --config playwright.form-layout.config.ts user-detail-layout.spec.ts` → **12 failed / 24 passed**（`/tmp/postverify/task002/red-user-detail-layout.log`），失败全部集中在记忆用例，两族原文：
  - `memory sources …`（4 例）：`Expected: "用户要求记住" / Received: "preference"` —— gridcell nth(1) 已从旧来源列变为新类别列；旧 mock 的 `source_type` 不在新 DTO 里。
  - `user memory table fits …`（8 例）：`getByRole('columnheader') Expected: 4 / Received: 6` —— 新表 6 列（内容/类别/来源/状态/更新时间/操作）。
- 域覆盖事实：`playwright.user-identity.config.ts` 的 testDir=`./tests/user-identity` 不收集本 spec；该命令实跑 **6 passed**（`/tmp/postverify/task002/user-identity-domain.log`），仅作该域自身回归证据。契约命令已按事实改登记为 form-layout 域（CI 13 域之一，testMatch 含本 spec）。
- 修 spec 过程中暴露的组件缺陷（几何实测，probe 日志 `/tmp/postverify/task002/probe-layout.log`、`/tmp/postverify/task002/probe-widths.log`）：updated 列 152 下 `2026-10-09 22:09:02` 实测 **142.2px**、溢出单元格内边距盒 6.2px（`rect=920.0..1062.2 | cell=912.0..1064.0 | pad=8px/8px`）；author 列 120 下 `FUTURE_SOURCE`/`Confirmed by user` 实测 **119px**（列内容宽 104）被截断。

**GREEN**（命令、断言位置、日志路径）：

- **组件修**（`MemoryTab.tsx:305/315/339`）：author 120→**144**（来源值 119px 完整显示，旧 source 列同为 144）、status 152→**140**（12px 有效期行实测 122.8px）、updated 152→**176**（与其余详情表同口径；`YYYY-MM-DD HH:mm:ss` 实测 142.2px）。内容列 = 880−(104+144+140+176+196) = **120**，满足 spec 的 `flexible ≥120`。
- **spec 修**（断言位置）：fixture 改 `MemoryItem` 新 DTO（author/status/version/explicit_request 等，`e2e/tests/user-detail-layout.spec.ts:32-41`）；来源断言改 author 列 `gridcell.nth(2)`（`:77-89`，本地化 label + `title` 保留原始码 + no-truncation 原样保留）；列数 6/操作列 196（`:47`）；`statusMax` 140（`:148`）；时间列 176（`:151`）；390px 固定操作列改逐按钮断言（`:163-167`，记忆表每格 3 个动作，单按钮表语义不变）。
- **过滤 spec 连续两次**：`… --config playwright.form-layout.config.ts user-detail-layout.spec.ts` → **36 passed**（`/tmp/postverify/task002/green-run1.log`、`green-run2.log`）。
- **全域连续两次**：`… --config playwright.form-layout.config.ts` → **209 passed**（`/tmp/postverify/task002/form-layout-full.log`、`form-layout-full2.log`）。
- **build**：`npm --prefix apps/console-platform/frontend run build` → `✓ built`（`build-final.log`）。
- **回归**：agent 域 E-04（MemoryTab 的真实数据域，端口经 env 偏移避让本机 5173 占用）→ **11 passed**（`/tmp/postverify/task002/agent-e04.log`）；`uv run pytest -q tests/frontend` → **340 passed**；`grep source_type\|Memory\b` 于 spec 无残留。

### Log

- [2026-10-15] created (draft)。
- [2026-10-15] started
- [2026-10-15] RED（form-layout 域 12 failed/24 passed）→ 按新契约改 mock/断言 → 修 MemoryTab 列宽（144/140/176）→ 全域两轮 209 passed、过滤 spec 两轮 36 passed、agent E-04 11 passed、tests/frontend 340 passed。
- [2026-10-15] 登记事实修正：本 spec 由 form-layout 域收集（user-identity 域 testDir 不含它）；契约命令据此改登记。
- [2026-10-15] completed (done)

---

## TASK-003: `/new` 后会话解析被后台事件顶回的产品侧收口

- **Status**: verified
- **Priority**: P0
- **Depends**: 
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.6 consolidation 生命周期
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, tests/agent_runtime/test_run_service.py

### Description

WS 入站不带会话指针，`RunService._resolve_conversation`（`run_service.py:934`）按 `Conversation.update_time DESC` 取「最新会话」；后台提炼收尾会向**旧会话** append `MEMORY_CONSOLIDATED`（`EventWriter._next_seq` bump `conversation.update_time`）⇒ `/new` 出的新会话被顶掉，下一条消息落回旧会话（终验期间在 `test_memory_flow`/`test_runtime_stream` 实测复现）。产品侧收口：会话解析按**用户最近发言**（`canonical_event.event_type='USER_MESSAGE'` 的最大时间）而非 `update_time`，无用户消息时回落 `create_time`；后台/系统事件不得改变解析结果。

### Checklist

- [x] 先写 RED（真实 PG）：会话 A 有用户消息 → `/new` 出会话 B → 向 A append 后台/系统事件（bump `update_time`）→ 解析新入站必须得到 B；另覆盖「A 无用户消息」与「B 也收到用户消息后仍取 B」。
- [x] 实现：`_resolve_conversation` 改按用户消息时间解析（一次查询，含无用户消息回落）；不改 `EventWriter`/`update_time` 的既有语义（Console 等其它读取不受影响）。
- [x] GREEN + 回归：`tests/agent_runtime`（隔离 head 库）+ `tests/acceptance/im_gateway/test_memory_flow.py tests/acceptance/im_gateway/test_runtime_stream.py` 连续两次全绿。
- [x] 不弱化既有断言；若发现其它按 `update_time` 解析会话的位置一并登记。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 PG（隔离 head 库）+ 真实事件写入 | 后台 append 旧会话后，新入站仍解析到用户最近发言的会话；无用户消息时回落 `create_time` | tests/agent_runtime/test_run_service.py | ["uv","run","pytest","-q","tests/agent_runtime/test_run_service.py"] | verified |

### Acceptance Evidence

**RED**（实现前；隔离 head 库 `muad_acc_7400b25ed1`，真实 PG + 真实 `EventWriter` 写入）：

- `uv run pytest -q tests/agent_runtime/test_run_service.py -k resolve_conversation` → **3 failed**（`/tmp/postverify/task003/red-resolve.log`），三条新用例（断言位置 `tests/agent_runtime/test_run_service.py:701/731/761`）全部命中旧 `update_time` 口径，失败原文：
  - `…background_event_bumps_old`：`assert resolved.id == b_id` → `AssertionError: assert UUID('62f233e5-e8be-41c6-b3a3-d4a286dbb5fe') == UUID('ea0d7152-c417-4339-8e1b-e91699ef955e')` —— 前置断言 `old.update_time > fresh.create_time`（`:701` 之前）先通过，证明 `MEMORY_CONSOLIDATED` 的 bump 真实发生、旧会话确实被算成「最新」。
  - `…falls_back_to_create_time_without_user_messages`：`assert resolved.id == b_id` 失败（A 无用户消息、被 `RUN_FAILED` bump 后顶掉更晚创建的新会话 B）。
  - `…sticks_to_conversation_that_received_user_message`：第二次 `assert resolved.id == b_id` 失败（B 已有用户消息，仍被 A 的后台事件 bump 顶回）。

**GREEN**（实现后；命令、断言位置、日志路径）：

- **实现**（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`）：新增模块级 `_conversation_recency_key()`（`:553-571`）—— 相关子查询取该会话 `canonical_event.event_type='USER_MESSAGE'` 的最大 `create_time`，`coalesce(…, Conversation.create_time)` 兜底；`_resolve_conversation` 的 `order_by` 改为该键（`:978`，同一 SQL 内，仍是单次查询）。**未改** `EventWriter`/`_next_seq`/`conversation.update_time` 既有语义（Console 展示、事件写入路径均不受影响）。
- **新用例**：`uv run pytest -q tests/agent_runtime/test_run_service.py -k resolve_conversation` → **3 passed**（`/tmp/postverify/task003/green-resolve.log`）；整文件 → **15 passed**（`/tmp/postverify/task003/green-run-service-file.log`）。测试边界：真实 PG（隔离 head 库）+ 真实 `EventWriter.append` 写事件（`_seed_conversation`/`_append_event`，`test_run_service.py:631/646`），不 mock 事件写入。
- **回归连续两次**：`uv run pytest -q tests/agent_runtime` → **764 passed ×2**（`/tmp/postverify/task003/green-agent-runtime-1.log`、`green-agent-runtime-2.log`）。
- **验收回归连续两次**：`uv run pytest -q tests/acceptance/im_gateway/test_memory_flow.py tests/acceptance/im_gateway/test_runtime_stream.py` → **7 passed ×2**（`/tmp/postverify/task003/green-im-gateway-1.log`、`green-im-gateway-2.log`）。测试侧既有规避 `wait_for_memory_background_idle` **原样保留、未弱化**（不再承载本缺陷的规避作用，但仍等记忆后台静止，对其它断言有意义）。
- **静态检查**：`uv run ruff check`（两改动文件）→ EXIT=0 All checks passed（`green-ruff.log`）；`uv run mypy run_service.py` → EXIT=0（`green-mypy.log`）。
- **其它 `update_time` 解析位置核查**：`grep -rn "Conversation.update_time" apps/` → 仅 `run_service.py` 本处（已改）；`cancel_active` 按活跃 Run 解析、不按会话 `update_time`；全仓无其它按会话 `update_time` 解析「当前会话」的位置，无新增登记项。会话索引 `ix_conversation_tenant_user_agent_update_time` 保留不动（无 schema 变更）。

### Log

- [2026-10-15] created (draft)。
- [2026-10-15] started
- [2026-10-15] RED（3 failed，旧 `update_time` 口径）→ 实现 `_conversation_recency_key()`（USER_MESSAGE 最大 `create_time` + `create_time` 回落，单查询）→ 新用例 3 passed、文件 15 passed；`tests/agent_runtime` 764 passed ×2、im_gateway 两文件 7 passed ×2；ruff/mypy 全绿。
- [2026-10-15] 登记：全仓无其它按会话 `update_time` 解析「当前会话」的位置（`cancel_active` 按活跃 Run）；未改 `EventWriter` 语义。
- [2026-10-15] completed (done)

---

## TASK-004: 归档与新提交引发的存量契约修复（inventory 路径回落 + 配置清单重生成）

- **Status**: verified
- **Priority**: P1
- **Depends**: TASK-003
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#6.1 Spec Compliance Matrix
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: tests/test_memory_redesign_spec_contract.py, tests/memory_redesign_inventory.py, docs/configuration-inventory.csv

### Description

本需求终验（verify-e2e）heavy 校验暴露两条由**收尾动作**引发的存量契约失效：① 记忆需求归档后，`tests/test_memory_redesign_spec_contract.py` 与 `tests/memory_redesign_inventory.py` 仍按未归档路径读任务文件/spec-context ⇒ `context_read_error`；② TASK-003 改动 `run_service.py` 造成模块级常量行号位移，`docs/configuration-inventory.csv` 的 python-constant 行陈旧。按仓库既有惯例修：inventory 类测试统一 `TASK_REL` + `ARCHIVED_REL` 双路径回落（见 `tests/async_tool_runtime_inventory.py:48-71`、`tests/agent_memory_inventory.py:25-41`）；CSV 按 AST 重生成。

### Checklist

- [x] 先跑两条失败用例记录真实 RED（`test_memory_redesign_spec_contract.py::test_memory_rule_has_exactly_one_owner_and_duplicate_is_blocked`、`tests/memory_redesign_inventory.py`、`tests/test_configuration_inventory.py`）。
- [x] 两个测试按既有惯例加 `ARCHIVED_REL` 回落（归档后仍能校验归档件的一致性）；不改校验口径。
- [x] `docs/configuration-inventory.csv` 按 AST 重生成 python-constant 行（沿用既有 category/reason 口径）。
- [x] GREEN：三条用例 + `tests/memory_redesign_inventory.py` 全绿；`tests/frontend` 与 `tests/agent_runtime/test_run_service.py` 不回归。
- [x] 不修无关文件；Evidence 记录命令与结果。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit | 真实仓库文件树（归档件 + 源码 AST） | 归档后契约测试按 `ARCHIVED_REL` 回落并全绿；CSV 与真实常量一致 | tests/test_memory_redesign_spec_contract.py、tests/memory_redesign_inventory.py、tests/test_configuration_inventory.py | ["uv","run","pytest","-q","tests/test_memory_redesign_spec_contract.py","tests/memory_redesign_inventory.py","tests/test_configuration_inventory.py"] | planned |

### Acceptance Evidence

- RED：`uv run pytest -q tests/test_configuration_inventory.py::test_configuration_inventory_covers_real_python_constants tests/test_memory_redesign_spec_contract.py::test_memory_rule_has_exactly_one_owner_and_duplicate_is_blocked` → **2 failed**：① 配置清单 python-constant 行陈旧（TASK-003 改动 `run_service.py` 造成常量行号位移）；② `cf_spec_context.ContextError: context_read_error`（按未归档路径 `.code-flow/tasks/2026-10-12/memory-redesign/spec-context.yml` 读取，归档后不存在）。RED 现场亦见 verify-e2e 的 validation 输出（`/tmp/fluxion-postverify-e2e.json`）。
- GREEN：同两条 + `tests/memory_redesign_inventory.py` → **18 passed**；`tests/test_configuration_inventory.py` → **4 passed**（CSV 按 AST 重生成，1240 行 / python-constant 1033，category/reason 沿用既有口径）。ruff 全绿。
- 实现：`tests/test_memory_redesign_spec_contract.py` 与 `tests/memory_redesign_inventory.py` 引入 `ARCHIVED_TASK_DIR` 回落（与 `tests/async_tool_runtime_inventory.py:48-71`、`tests/agent_memory_inventory.py:25-41` 同惯例），归档后仍以归档件为事实源校验；校验口径未变。

### Log

- [2026-10-15] created (draft)；由 post-verify-fixes 终验 heavy 校验缺口显式补任务。
- [2026-10-15] started
- [2026-10-15] resumed (in-progress)
- [2026-10-15] completed (done)

---

## TASK-005: dfx 终态断言对「终态后后台追加」的容忍

- **Status**: verified
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.3.5 Run 级审计
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: tests/acceptance/dfx/test_dfx_journeys.py

### Description

`test_s06_stream_reply_envelope_and_monotonic_seq` 偶发超时「Run 未落终态 run.completed（最后观测：[]）」（本机连续两次复跑 1 failed/1 passed 实测）。根因：`_canonical_events(run_id)` 取该 run **全部**事件，而记忆提炼 job 会在 Run 终态之后向**同一 run** 追加 `MEMORY_CONSOLIDATED`（design §3.3.5 run 级审计；TASK-033 已在 `test_run_lifecycle::b01` 修过同族断言）；`_completed_events` 要求「最后一条 == run.completed」⇒ 后台一追加即永远等不到（时序相关 ⇒ 偶发）。修法：把终态当**边界**（切到 `run.completed` 为止），并在 s06 内加**确定性**覆盖（终态后插入合成事件，仍能返回终态视图）。

### Checklist

- [x] 记录 RED：dfx 复跑实测失败原文（`/tmp/postverify/dfx-check.txt` run2）+ 机制说明。
- [x] `_completed_events` 改为按终态切界（不改封套/单调性/终态状态断言的覆盖意图）。
- [x] s06 增确定性断言：终态后向该 run 插入合成 canonical 事件（直接 SQL），`_completed_events` 仍返回以 `run.completed` 结尾的视图。
- [x] GREEN：`tests/acceptance/dfx/test_dfx_journeys.py` **连续三次全绿**。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 Gateway/Runtime/PG 栈 + 直接 DB 校验 | 终态后追加事件不影响 `run.completed` 视图；封套/seq 单调/终态状态断言不弱化 | tests/acceptance/dfx/test_dfx_journeys.py | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_journeys.py"] | planned |

### Acceptance Evidence

- RED：`uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py` 连续两次复跑 **run1 6 passed / run2 1 failed**（`/tmp/postverify/dfx-check.txt`），失败原文 `test_s06_stream_reply_envelope_and_monotonic_seq`：`AssertionError: Run 未落终态 run.completed（最后观测：[]）`（`test_dfx_journeys.py:124`）。机制：`_canonical_events(run_id)` 取该 run 全部事件，而后台提炼在终态后向同 run 追加 `MEMORY_CONSOLIDATED`（design §3.3.5），旧 `_completed_events` 要求「最后一条 == run.completed」⇒ 追加后永远等不到（时序相关 ⇒ 偶发）。
- 修复：抽出纯函数 `_terminal_slice`（切到 `run.completed` 为止），`_completed_events` 用它；s06 增**确定性**覆盖（终态视图 + 合成非流式追加事件 → `_terminal_slice` 仍以 `run.completed` 结尾）。
- GREEN：`uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py` **连续三次全绿**（6 passed / 6 passed / 6 passed，34.49s / 25.03s / 35.06s，`/tmp/postverify/dfx-fix2.txt`）；ruff 全绿。
- 过程说明：首版用「终态后插入合成 DB 事件」做覆盖，run3 撞 `uq_canonical_event_conversation_seq`（与真实后台写入的 TOCTOU 竞争，属自引入问题）→ 改为纯逻辑覆盖（不写库），三次全绿。

### Log

- [2026-10-15] created (draft)；由 dfx 复跑的偶发超时与机制定位显式补任务。
- [2026-10-16] started
- [2026-10-16] completed (done)
