# Tasks: post-verify-fixes

- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.7 API 与 Console（记忆重设计终验遗留项收口）
- **Created**: 2026-10-15
- **Updated**: 2026-10-15
- **Plan status**: draft；阶段裁决以 CLI 输出为准

## Proposal

收口记忆重设计需求终验阶段发现的三个**非该需求范围**的遗留（当时按纪律只登记未修）：

1. 设置页版本历史面板缺 `activeTab` —— 与 `MemoryHistorySideSheet` 同型缺陷（Pane 恒 `inactive` ⇒ 面板打开是空壳），且无测试覆盖。
2. `e2e/tests/user-detail-layout.spec.ts` 仍按已删除的旧记忆表（`Memory`/`source_type`/`USER_EXPLICIT`）构造 mock 与断言 —— 跑 user-identity 浏览器域会红。
3. `/new` 后旧会话被后台 `MEMORY_CONSOLIDATED` append 顶回「最新会话」：WS 入站不带会话指针，Runtime 按 `Conversation.update_time` 最新解析；后台事件 bump 旧会话的 `update_time` ⇒ 下一条消息落回旧会话。

三项互相独立；2、3 是事实修正（测试/查询按新契约与真实语义），1 是产品缺陷修复 + 防同类回归的静态守卫。

## TASK-001: 设置版本历史面板 activeTab 修复与详情壳静态守卫

- **Status**: done
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

- **Status**: draft
- **Priority**: P1
- **Depends**: 
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: e2e/tests/user-detail-layout.spec.ts

### Description

该 spec 仍 import 已删除的 `Memory` 类型、按 `source_type`（`USER_EXPLICIT`/`AGENT_INFERRED`/`FUTURE_SOURCE`）构造 4 列记忆表 mock 并断言来源列 —— 记忆 Tab 已在记忆重设计中拆到 `MemoryTab`（author/status/version/动作列），该 spec 属存量漂移。按现状修：记忆相关断言改为新契约（或按「记忆已不在 UserDetailTabs 内」调整布局断言），保持该 spec 对**布局**的覆盖意图。

### Checklist

- [ ] 先跑 `npm --prefix e2e test -- --config playwright.user-identity.config.ts` 记录 RED（或该 spec 相关用例的真实失败）。
- [ ] 按新契约改 mock 与断言；不删覆盖意图、不引入 mock 之外的旁路。
- [ ] GREEN：该域连续两次全绿；`npm --prefix apps/console-platform/frontend run build` 通过。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | E2E | browser，真实 Console（构建产物 + 真实会话） | 用户详情布局契约按新契约成立；无 `source_type`/旧 `Memory` 类型残留 | e2e/tests/user-detail-layout.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts"] | planned |

### Acceptance Evidence

计划阶段未执行。

### Log

- [2026-10-15] created (draft)。

---

## TASK-003: `/new` 后会话解析被后台事件顶回的产品侧收口

- **Status**: draft
- **Priority**: P0
- **Depends**: 
- **Source**: .code-flow/tasks/archived/2026-10-12/memory-redesign/memory-redesign.design.md#3.6 consolidation 生命周期
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, tests/agent_runtime/test_run_service.py

### Description

WS 入站不带会话指针，`RunService._resolve_conversation`（`run_service.py:934`）按 `Conversation.update_time DESC` 取「最新会话」；后台提炼收尾会向**旧会话** append `MEMORY_CONSOLIDATED`（`EventWriter._next_seq` bump `conversation.update_time`）⇒ `/new` 出的新会话被顶掉，下一条消息落回旧会话（终验期间在 `test_memory_flow`/`test_runtime_stream` 实测复现）。产品侧收口：会话解析按**用户最近发言**（`canonical_event.event_type='USER_MESSAGE'` 的最大时间）而非 `update_time`，无用户消息时回落 `create_time`；后台/系统事件不得改变解析结果。

### Checklist

- [ ] 先写 RED（真实 PG）：会话 A 有用户消息 → `/new` 出会话 B → 向 A append 后台/系统事件（bump `update_time`）→ 解析新入站必须得到 B；另覆盖「A 无用户消息」与「B 也收到用户消息后仍取 B」。
- [ ] 实现：`_resolve_conversation` 改按用户消息时间解析（一次查询，含无用户消息回落）；不改 `EventWriter`/`update_time` 的既有语义（Console 等其它读取不受影响）。
- [ ] GREEN + 回归：`tests/agent_runtime`（隔离 head 库）+ `tests/acceptance/im_gateway/test_memory_flow.py tests/acceptance/im_gateway/test_runtime_stream.py` 连续两次全绿。
- [ ] 不弱化既有断言；若发现其它按 `update_time` 解析会话的位置一并登记。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 PG（隔离 head 库）+ 真实事件写入 | 后台 append 旧会话后，新入站仍解析到用户最近发言的会话；无用户消息时回落 `create_time` | tests/agent_runtime/test_run_service.py | ["uv","run","pytest","-q","tests/agent_runtime/test_run_service.py"] | planned |

### Acceptance Evidence

计划阶段未执行。

### Log

- [2026-10-15] created (draft)。
