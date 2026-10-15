# Tasks: memory-review-fixes

- **Source**: GPT 代码评审报告（2026-10-16，范围 TASK-024～033，含独立 PostgreSQL + 真实 HTTP 定向复现）；条目 2 已由本会话 TASK-003 修复，其余 7 条成立
- **Created**: 2026-10-16
- **Updated**: 2026-10-16
- **Plan status**: draft；阶段裁决以 CLI 输出为准

## Proposal

收口评审确认的 7 条缺口：CLEAR 不撤销 pending 提议（P1）、前端重试丢失命令号（P1）、Console 列表/总数非同一快照（P2）、删除后无法经界面重建与查看历史（P2×2）、dfx 探针按 token 过滤把提炼调用算成业务调用（P2）、E-03 未走真实用户确认（P2）。全部按真实边界修复并补确定性覆盖；评审条目 2（后台提炼顶回旧会话）已由 `e6fca795` 修复，本需求只做回归登记。

## TASK-001: CLEAR 撤销 pending 提议并推进撤销边界（P1）

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: GPT 评审条目 1；memory-redesign.design.md#3.7 API 与 Console（“清空撤销所有 pending 提议”）
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_service.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_mutations.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation_resume.py, tests/agent_runtime/test_memory_redesign_writes.py

### Description

`MemoryService._apply`（`memory_service.py:305`）只在 `plan.changes` 含 `USER_DELETE/ADMIN_DELETE` 时递增 `scope.forget_epoch`；空库（无已保存记忆、只有 pending 提议）执行 CLEAR 返回 `UNCHANGED`、epoch 不变 ⇒ 提议仍 `WAITING`，用户确认后成功写入 USER 事实（评审已复现）。修复：CLEAR 作为**显式删除边界**必须撤销该用户所有 pending 记忆确认提议（`run_interrupt` 的提议置终态），空库也推进撤销边界；**同命令重放保持原回执**（幂等不回退、不二次撤销）。

### Checklist

- [x] 先写 RED（真实 PG + 真实 Runtime HTTP）：空库（仅 pending 提议）执行 CLEAR → 提议必须被撤销（WAITING→EXPIRED/CANCELLED）、随后的确认必须被拒（`MEMORY_CONFIRMATION_*`）；断言清空响应与 `forget_epoch` 语义。
- [x] 实现：CLEAR 撤销 pending 提议（含空库）；同 `command_id` 重放返回首次回执且不重复副作用；有记忆的既有行为与历史/回执计数不变。
- [x] 确认路径复核：被撤销提议的确认（含重连/重复提交）一律拒绝，不产生 USER 事实。
- [x] GREEN + 回归：`tests/agent_runtime/test_memory_redesign_*.py`（隔离 head 库）+ `tests/acceptance/im_gateway/test_memory_flow.py` 连续两次全绿。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 PG（隔离 head 库）+ 真实 Runtime 管理/确认链路 | 空库 CLEAR 撤销 pending 提议且确认被拒；同命令重放同回执；有记忆时行为不回归 | tests/agent_runtime/test_memory_redesign_writes.py | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py"] | verified |

### Acceptance Evidence

**RED**（先于实现；命令与失败原文）：

- `DATABASE_URL=<隔离 head 库> REDIS_URL=redis://localhost:6379/15 uv run pytest -q tests/agent_runtime/test_memory_redesign_writes.py -k clear_revokes` → **1 failed**，`/tmp/reviewfix/task001/red.log`：
  `assert response.forget_epoch == 1, "空库清空也必须推进撤销边界（forget_epoch+1）"` → `AssertionError: 空库清空也必须推进撤销边界（forget_epoch+1）` / `assert 0 == 1` / 实测回执 `MemoryCommandResponse(command_id='clear-pending', scope_revision=0, forget_epoch=0, changed_count=0, status='UNCHANGED', items=[])` —— 空库 CLEAR 不推进 `forget_epoch`（评审条目 1 的同一缺口）。
- RED 依据（评审独立 PG 复现）：清空后 `run_interrupt` 提议仍 `WAITING`，用户随后确认成功写入 `author=USER` 事实。

**GREEN**（修复后；命令、断言位置、日志）：

- 新增用例 `tests/agent_runtime/test_memory_redesign_writes.py:350`（`test_b03_memory_redesign_clear_revokes_pending_proposal_and_rejects_late_confirmation`）：真实隔离 head 库 + 真实 `MemoryAdminService`（Console→Runtime 同一管理入口，`_admin_clear` :342）+ 真实 Runtime HTTP/SSE 确认链路（`confirmation_stack`）。断言位置：`forget_epoch == 1` 与 `changed_count == 0`（:366-367）、提议终态 `CANCELLED` 且 `resolution_json["reason"] == "MEMORY_CLEAR"`（:369-370）、迟到确认抛 `MEMORY_CONFIRMATION_EXPIRED` 且零 USER 事实/零历史/零模型调用（:373-378）、重连重复提交（新消息号）同样被拒且 `run_record` 仍 `WAITING_INPUT`（:380-386）、同 `command_id` 重放返回首次回执且 `resolved_at` 不变、回执数 = 1（:388-391）。
- 实现（断言背后的代码）：`memory_service.py:94`（`_lock_clear_revocations`：CLEAR 在 `memory_scope` 之前锁该用户全部 WAITING 提议，锁序对齐确认/过期路径，避免与 `interrupt → memory_scope` 成环）、`:118`（`_revoke_pending_proposals`：置 `CANCELLED` + `resolution_json={"reason":"MEMORY_CLEAR"}`，不删行）、`:127`（`_advance_scope`：CLEAR 即使无事实变更也 `forget_epoch+1`）、`:296-320`（回执复查提前到 scope 锁之前，重放不再二次撤销）；`memory_confirmation_resume.py:54-79`（`prepare_confirmation` 对终态提议的确认显式 `MEMORY_CONFIRMATION_EXPIRED`，不再静默降级为普通消息）；`run_resume.py:22`（`lock_human_resume` 返回该 Run 全部中断）+ `:93`（`resolve_generic_interrupts` 只推进 WAITING 行）。
- 契约命令：`uv run pytest -q tests/agent_runtime/test_memory_redesign_writes.py` → **17 passed**，`/tmp/reviewfix/task001/green_writes.log`。
- 回归（隔离库）`tests/agent_runtime/test_memory_redesign_*.py` 连续两次：**322 passed** ×2（410.04s / 422.29s），`/tmp/reviewfix/task001/green_redesign.log`、`green_redesign_run2.log`；含既有 CLEAR 有记忆语义 `test_b03_memory_redesign_delete_restore_and_clear_keep_identity_and_epoch_replay`（epoch 2、重放 `replayed=True`）与确认链路全套 `test_memory_redesign_confirmation.py`。
- 回归（真实 Gateway 栈）`uv run pytest -q tests/acceptance/im_gateway/test_memory_flow.py` 连续两次：**2 passed** ×2（30.25s / 31.50s），`/tmp/reviewfix/task001/acceptance_run1.log`、`acceptance_run2.log`。
- 静态：`uv run ruff check`（4 文件）All checks passed；`uv run mypy`（3 文件）Success: no issues found。

### Log

- [2026-10-16] created (draft)。
- [2026-10-16] started
- [2026-10-16] RED（空库 CLEAR `forget_epoch` 停在 0）→ 实现 CLEAR 撤销边界（锁序 interrupt→scope、终态 CANCELLED、空库也 `forget_epoch+1`、终态提议确认显式 EXPIRED）→ 契约 17 passed、`test_memory_redesign_*.py` 322 passed ×2、im_gateway 记忆流 2 passed ×2。
- [2026-10-16] completed (done)

---

## TASK-002: 前端重试持有命令号与输入（P1）

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: GPT 评审条目 3
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx, tests/frontend/test_memory_tab_contract.py

### Description

`MemoryTab.tsx:176` 起，创建/启禁用/删除/清空每次调用都新生成 `newRequestId()`：响应丢失后重试会换命令号 ⇒ 同一操作变成**第二条命令**（清空换 key 重试会把清空之后新建的记忆一并删除；复用原 key 则保持首次回执）。修复：组件为「一次操作」持有 `Idempotency-Key` 与输入快照，直到结果明确（成功或失败）才释放；重试复用同一 key。

### Checklist

- [x] 先写源码/行为契约 RED：同一次操作在结果未明确前的重试必须复用同一命令号与输入；成功后下一次操作才换新命令号。
- [x] 实现：四个写操作统一经「操作上下文」（key + 输入）收口；错误可见且可重试；版本冲突路径行为不变。
- [x] GREEN：`tests/frontend/test_memory_tab_contract.py` + `tests/frontend` 全绿 + typecheck；`make acceptance-e2e DOMAIN=agent` 通过（含重试路径的浏览器验证）。
- [x] 不新增 i18n 框架/HTTP 机制；不弱化既有断言。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit + E2E | 真实源码契约 + 真实 Console/Runtime/PG/Chrome（重试路径） | 同一次操作重试复用同一命令号与输入；成功后换新命令号；清空重试不误删新记忆 | tests/frontend/test_memory_tab_contract.py、e2e/tests/agent-management/memory-redesign.spec.ts | ["bash","-lc","uv run pytest -q tests/frontend/test_memory_tab_contract.py && make acceptance-e2e DOMAIN=agent"] | verified |

### Acceptance Evidence

**RED**（先于实现；命令与失败原文）：

- `uv run pytest -q tests/frontend/test_memory_tab_contract.py` → **4 failed / 7 passed**，`/tmp/reviewfix/task002/red.log`：
  - `test_memory_command_context_reuses_key_until_result_is_definite`：`AssertionError: esbuild 转译失败：✘ [ERROR] Could not resolve ".../user-identity/memoryCommands.ts"`（行为用例要求真实存在的操作上下文模块）；
  - `test_memory_tab_writes_hold_command_context_until_success`：`AssertionError: 命令上下文必须由组件持有`（`createMemoryCommandContext(newRequestId)` 不在源码）；
  - `test_memory_writes_carry_expected_version_and_caller_key` / `test_memory_create_form_field_order_and_writable_categories`：`(key)=>updateMemoryEnabled(props.userId,entry.memory_key,input,key)` / `(key)=>createMemory(props.userId,input,key)` 缺失（旧实现每次调用都 `newRequestId()`）。
- RED 依据（评审条目 3 的后端实测）：复用 key = 200 回执重放；换 key = 第二条命令（清空会把清空后新建的记忆一并删除）。

**GREEN**（修复后；命令、断言位置、日志）：

- 实现：新增纯模块 `apps/console-platform/frontend/src/modules/user-identity/memoryCommands.ts:27`（`createMemoryCommandContext(newKey)`：同 identity + 同输入快照复用同一命令号，`release` 后换新；key 生成器由调用方注入）；`MemoryTab.tsx:127` 组件持有上下文（`useRef(createMemoryCommandContext(newRequestId))`），`:163` `runAction` 为唯一收口（取 key → 成功才 `release` 并刷新；失败经 `onError` 落到可见错误），`:182` `runToolbarAction` 承载工具行错误；四个写操作（创建/启禁用/删除/清空，含过期重新启用）都经该收口取命令号，调用点不再各次 `newRequestId()`；identity 含用户与目标（`toggle:${userId}:${memoryKey}` 等），切换用户不串号。
- 单元契约：`tests/frontend/test_memory_tab_contract.py` 新增 `test_memory_tab_writes_hold_command_context_until_success`（:154，源码契约：上下文唯一持有、`newRequestId()` 零调用、`commands.current.release(identity)` 唯一收口、五个写调用形状）与 `test_memory_command_context_reuses_key_until_result_is_definite`（:301，真实 node + 仓库内 esbuild 执行 `memoryCommands.ts`：`retry == first`、输入快照变化换新、`changedRetry == changed`、跨目标不串号、`afterSuccess` 换新、`generated == 4`）。
- 契约命令：`uv run pytest -q tests/frontend/test_memory_tab_contract.py` → **11 passed**，`/tmp/reviewfix/task002/green_contract.log`；`uv run pytest -q tests/frontend` → **342 passed**，`green_frontend.log`；`npm --prefix apps/console-platform/frontend run typecheck` → EXIT=0，`green_typecheck.log`；RULE-front-001 链实跑全绿（api client 4 passed → api usage OK → i18n 1163 → typecheck），`rule_front_001.log`。
- 浏览器（真实构建产物 + 真实 Console/Runtime/PG）：新增用例 `e2e/tests/agent-management/memory-redesign.spec.ts:232`（响应丢失注入 = `route.fetch()` 真实送达后 `route.abort()` 丢弃回包；断言可见错误、重试后幸存记忆仍在、两次请求 `Idempotency-Key` 相同）；`AGENT_E2E_WEB_PORT=5174 make acceptance-e2e DOMAIN=agent` 连续两次 **25 passed**（46.2s / 41.9s），`/tmp/reviewfix/task002/agent_e2e_run1.log`、`agent_e2e_run2.log`。
- 负向对照（证明浏览器用例对旧实现为红）：临时还原旧实现（保留 `memory-clear` testid）后重跑该用例 → **1 failed**：`expect(locator).toHaveCount(expected) failed / Expected: 1 / Received: 0`，落在 `:284`「重试后幸存记忆仍在」—— 正是评审条目 3 的误删（`/tmp/reviewfix/task002/negative_control2.log`）；随后恢复修复版并重建。

### Log

- [2026-10-16] created (draft)。
- [2026-10-16] started
- [2026-10-16] RED（4 failed：上下文缺失 + 五个写调用仍是每次 `newRequestId()`）→ 新增 `memoryCommands.ts` + `MemoryTab` 统一收口 → 契约 11 passed、tests/frontend 342 passed、typecheck 0、agent 域 25 passed ×2（含重试路径）、负向对照复现误删。
- [2026-10-16] started
- [2026-10-16] completed (done)

---

## TASK-003: Console 列表与总数同一读取快照（P2）

- **Status**: draft
- **Priority**: P1
- **Depends**: 
- **Source**: GPT 评审条目 4
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/memory_repository.py, tests/console_platform/test_user_memory_api.py

### Description

`memory_repository.py:67` 用两条 SQL 分别取 count 与 items：READ COMMITTED 下并发写入/TTL 跨界会让两者看到不同集合（评审实测 total=0 而 items 有 1 条）。修复：单条 SQL 同时产出 total 与分页行（参考 Runtime `memory_admin_queries` 的子查询口径），并补并发与到期边界测试。

### Checklist

- [ ] 先写 RED：真实 PG 并发写入/到期跨界下 total 与 items 必须同快照（构造可复现的跨界断言）。
- [ ] 实现：count 与分页行同一语句（同谓词、同 `statement_timestamp()` 口径）。
- [ ] GREEN + 回归：`tests/console_platform/test_user_memory_api.py` + `tests/console_platform` 全绿。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 Console HTTP + 真实 PG（并发与到期边界） | total 与 items 来自同一快照；过滤谓词与状态口径不回归 | tests/console_platform/test_user_memory_api.py | ["uv","run","pytest","-q","tests/console_platform/test_user_memory_api.py"] | planned |

### Acceptance Evidence

计划阶段未执行；RED 依据：评审条目 4 实测（total=0/items=1）。

### Log

- [2026-10-16] created (draft)。

---

## TASK-004: 删除后的恢复与历史界面入口（P2）

- **Status**: draft
- **Priority**: P1
- **Depends**: 
- **Source**: GPT 评审条目 5、6；memory-redesign.design.md#3.7（RESTORE 语义、deleted 历史可查）
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx, apps/console-platform/frontend/src/modules/user-identity/MemoryHistorySideSheet.tsx, apps/console-platform/backend/src/muad_console_platform/api/users.py, e2e/tests/agent-management/memory-redesign.spec.ts

### Description

两条界面缺口：① 删除后无法经界面重建同一 key（创建不传 `expected_version`，tombstone 保留版本 ⇒ 409；RESTORE 未开路由）；② 删除后历史无入口（历史按钮只在存活行，浏览器用例经 API 绕开）。修复：提供明确的恢复动作（读取 tombstone 保留版本，管理员确认后走 RESTORE）与可发现的历史入口（按 key 查询/含已删除条目的历史视图）；浏览器用例改为**经界面**查看删除后的历史记录。

### Checklist

- [ ] 先写 RED：界面/契约测试断言「删除后可恢复同一 key」与「删除后历史可经界面查看」；浏览器域断言改为经 UI 入口（不得再直调 API 绕开）。
- [ ] 实现：恢复动作（RESTORE 路由 + 版本读取 + 确认）与历史入口（含 deleted）；错误码语义清晰（版本冲突可见、可重试）。
- [ ] GREEN：`tests/frontend` + typecheck + `make acceptance-e2e DOMAIN=agent` 连续两次全绿。
- [ ] 不弱化既有断言；不新增 i18n 框架（文案两语言齐平）。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | E2E | browser，真实 Console/Runtime/PG/Chrome | 删除后可经界面恢复同一 key（RESTORE）；删除后历史经界面入口可查；越权不可操作 | e2e/tests/agent-management/memory-redesign.spec.ts | ["make","acceptance-e2e","DOMAIN=agent"] | planned |

### Acceptance Evidence

计划阶段未执行；RED 依据：评审条目 5/6（409 实测与用例绕开）。

### Log

- [2026-10-16] created (draft)。

---

## TASK-005: dfx 探针按调用用途区分业务与提炼（P2）

- **Status**: draft
- **Priority**: P1
- **Depends**: 
- **Source**: GPT 评审条目 7
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: tests/acceptance/dfx/test_dfx_model_recovery.py

### Description

故障探针按「body 是否含目标 token」区分目标调用，但 consolidation 输入会保留来源用户文本（含同一 token）⇒ 提炼请求消费预设 500、`target_calls=1/background_calls=0`（评审定向复现）。修复：先按**请求用途**（consolidation system 指令标记，与 `tests/e2e/openai_probe_app.py` 同口径）区分业务/提炼调用，再在业务调用里匹配目标 token。

### Checklist

- [ ] 先写 RED：注入脚本后，提炼请求不得消费业务脚本/不得计入 `calls`；`target_calls` 与 `background_calls` 口径稳定。
- [ ] 实现：用途判定优先（consolidation 标记），再匹配目标 token；不改变故障注入语义与退避断言。
- [ ] GREEN：`tests/acceptance/dfx/test_dfx_model_recovery.py` 连续三次全绿。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 Gateway/Runtime/PG + 真实故障探针 | 提炼调用与业务调用按用途分离；重试次数与退避观测稳定 | tests/acceptance/dfx/test_dfx_model_recovery.py | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_model_recovery.py"] | planned |

### Acceptance Evidence

计划阶段未执行；RED 依据：评审条目 7 的定向复现（提炼请求消费 500）。

### Log

- [2026-10-16] created (draft)。

---

## TASK-006: E-03 经真实用户确认（P2）

- **Status**: draft
- **Priority**: P1
- **Depends**: 
- **Source**: GPT 评审条目 8
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: tests/acceptance/im_gateway/test_memory_redesign_e2e.py

### Description

E-03 直接经管理 API 写入偏好并断言 `author == ADMIN`，没有走真实用户确认 ⇒ 即使 USER 来源注入链路损坏该验收仍可能通过。修复：E-03 先经候选展示 + 真实确认得到 **USER** 记忆（`author=USER`、真实确认事件），再 `/new` 验证首轮请求可见；ADMIN 来源另设独立场景（或明确其覆盖目的）。

### Checklist

- [ ] 先写 RED：把确认链路断言改为「USER 来源 + 真实确认」后，若注入链路被人为破坏（临时负向注入）必须红。
- [ ] 实现：E-03 走候选→确认→`/new` 首轮可见；保留 ADMIN 场景为独立断言。
- [ ] GREEN：`tests/acceptance/im_gateway/test_memory_redesign_e2e.py` 连续两次全绿；`code-flow acceptance run`（E-03 所属 manifest）复验。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | E2E | acceptance，真实 Gateway → Runtime → resume | 已确认 USER 偏好在 `/new` 后首轮请求可见；ADMIN 来源单独断言 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | planned |

### Acceptance Evidence

计划阶段未执行；RED 依据：评审条目 8（现用例 author=ADMIN、未走确认）。

### Log

- [2026-10-16] created (draft)。
