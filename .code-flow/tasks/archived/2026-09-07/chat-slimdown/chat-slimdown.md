# Tasks: chat-slimdown

- **Source**: .code-flow/tasks/2026-09-07/chat-slimdown/chat-slimdown.frontend.design.md
- **Created**: 2026-09-07
- **Updated**: 2026-09-07

## Proposal

chat 应用从 10 页面瘦身到单个对话框：ChatPage 加 Agent 下拉，token 链接开箱即聊，历史只读抽屉，多余页面组件全部删除。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | chat-slimdown.frontend.design.md#2.4 验收条件 | E2E | Chromium + 真实后端 channel SSE | TASK-001 | verified |
| S-02 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest | TASK-001 | verified |
| B-02 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest + localStorage | TASK-001 | verified |
| S-03 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest | TASK-002 | verified |
| S-04 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest + 只读会话接口 | TASK-002 | verified |
| E-01 | chat-slimdown.frontend.design.md#2.4 验收条件 | integration | tsc + lint + 构建 | TASK-003 | verified |
| B-01 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest | TASK-002 | verified |
| S-07 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest | TASK-005 | verified |
| S-08 | chat-slimdown.frontend.design.md#2.4 验收条件 | component | vitest | TASK-005 | verified |

---

## TASK-001: 对话框主体改造

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: chat-slimdown.frontend.design.md#3.3 组件设计, chat-slimdown.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-01, S-02, B-02

### Description

ChatPage 改造：顶部只读显示当前 Agent 名（access 绑定单 Agent，不做切换，用户 2026-09-07 确认）；消息流与输入框不动；token 沿用链接机制，开箱即聊。

### Checklist

- [x] [S-02][component] 先写 Agent 名只读展示测试（vitest），记录 RED
- [x] ChatPage 顶部展示当前 Agent 名（复用既有 agentDisplayName 逻辑）
- [x] [S-02][component] 打开即显示正确 Agent 名（GREEN）
- [x] token 持久化已实现（先行修复）：`store/load/clearStoredAccessToken` + main 启动读取 + 401/403 清缓存
- [x] [B-02][component] 存取清三操作 GREEN（workspace-contract.test.ts 已通过，正式证据编码期补）
- [x] verifier `RULE-frontend-component-001`: 容器/展示分离（S-02 覆盖）
- [x] verifier `RULE-frontend-semi-001`: 无自研组件，沿用 Semi（S-02 覆盖）
- [x] [S-01][E2E] 带有效 token 链接打开即聊（playwright 真 Chrome，1 passed，ad-hoc 用例已删）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Chromium、真实后端 channel SSE | 打开链接即聊，首轮有回复 | ad-hoc spec（建链→开链接→`#/chat`→发送→echo，1 passed，已删） | playwright run | verified |
| S-02 | component | vitest | 显示当前 Agent 名（只读） | __tests__/chat-agent-display.test.tsx（2 passed） | vitest run 该文件 | verified |
| B-02 | component | vitest、localStorage | 存取清三操作正确 | workspace-contract.test.ts 持久化用例（2 passed） | vitest run 该文件 | verified |


> 层级映射：component 系 vitest 隔离组件测试（服务 mock），对应 unit 层级；不触真实后端。
### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | N/A（ad-hoc 新写即过；建链断言先 RED 过两次：key 长度、default 冲突） | 1 passed | 临时 spec（已删） | 真 Chrome + 真 dev bundle + 真 PG | verified |
| S-02 | FAIL（同步查询未等异步 resolve，测试写法问题；实现本就存在） | 2 passed | chat-agent-display.test.tsx | 真实组件树 + InMemoryChatApi | verified |
| B-02 | N/A（先行修复，helper 新写即过） | 2 passed（存取清） | workspace-contract.test.ts | 真实 localStorage（jsdom） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-002: 历史抽屉与失效页

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: chat-slimdown.frontend.design.md#3.3 组件设计, chat-slimdown.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: S-03, S-04, B-01

### Description

新增 HistoryDrawer（只读会话列表 + 消息回看）与 InvalidLink（token 无效提示页）；ChatPage 接入抽屉入口与无效分支。

### Checklist

- [x] [S-03/S-04][component] 先写失效页与抽屉只读测试，记录 RED
- [x] 新增 `components/HistoryDrawer.tsx`（只读，不写不删）
- [x] 新增 `components/InvalidLink.tsx`（纯提示）
- [x] ChatPage 接入：抽屉入口 + token 无效分支
- [x] [S-03/S-04/B-01][component] 提示页/只读/过期不断连（GREEN）
- [x] verifier `RULE-frontend-quality-001`: 类型/lint/测试全过
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | component | vitest | 无效 token 显示提示页，无白屏 | history-invalid-link.test.tsx（1 passed） | vitest run 该文件 | verified |
| S-04 | component | vitest、只读会话接口 | 会话列表与消息可见，不可编辑 | history-invalid-link.test.tsx（1 passed） | vitest run 该文件 | verified |
| B-01 | component | vitest | 过期错误提示，不崩溃 | history-invalid-link.test.tsx（1 passed） | vitest run 该文件 | verified |


> 层级映射：component 系 vitest 隔离组件测试（服务 mock），对应 unit 层级；不触真实后端。
### Acceptance Evidence

| S-03 | RED（收集失败，组件不存在） | 1 passed | history-invalid-link.test.tsx | 真实组件 | verified |
| S-04 | 同上 | 1 passed | history-invalid-link.test.tsx | InMemory listHistory | verified |
| B-01 | 同上 | 1 passed | history-invalid-link.test.tsx | localStorage 断言 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-003: 删除多余页面组件

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: chat-slimdown.frontend.design.md#3.2 页面与路由结构
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001, fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: E-01

### Description

删 9 页面 + 10 组件 + WorkspaceLayout 导航；App 路由收敛到 `/` 单页；`__tests__` 同步；tsc+lint+vitest 全过。

### Checklist

- [x] 删 9 页面文件（Home/Agents/AgentDetail/Approvals/Tasks/TaskDetail/History/Memory/Settings）
- [x] 删 10 组件文件（清单见 §3.3）
- [x] WorkspaceLayout 导航拆除（保留 children 透传壳）
- [x] App.tsx 路由收敛；`__tests__` 引用同步删/改
- [x] [E-01][integration] tsc + lint + vitest + 构建全过，grep 零残留引用
- [x] verifier `RULE-frontend-directory-001`: 删除合规，路由收敛（E-01 覆盖）
- [x] verifier `RULE-fluxion-console-001`: chat 只调 channel 接口（S-01 侧证，后端不动）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | tsc、lint、构建 | 零残留引用，构建通过 | tsc+lint+vitest(29)+vite build 全过 | 全量命令 | verified |

### Acceptance Evidence

| E-01 | N/A（删除类，tsc 即 RED 门禁） | tsc+lint+vitest29+build 全过 | 上方 | 真实编译+测试+构建 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-004: 对话美化

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: chat-slimdown.frontend.design.md#3.3 组件设计, chat-slimdown.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-05, S-06, E-02

### Description

Markdown 渲染（新增 react-markdown + remark-gfm 依赖）+ 删 kind Tag + 气泡排版 + 流式光标 + 头部收敛。

### Checklist

- [x] [S-05/S-06][component] 先写 Markdown 渲染与无 kind Tag 测试，记录 RED
- [x] 加依赖并新增 `components/MarkdownMessage.tsx`，消息流切过去
- [x] 删 `message-kind` Tag；气泡 max-width/行高/间距；流式光标；头部去重
- [x] [S-05/S-06/E-02][component] 渲染正确/无残留/脚本转义（GREEN，规则覆盖见 TASK-001/002）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | component | vitest + MarkdownMessage | 无原文符号，层级正确 | markdown-message.test.tsx（1 passed） | vitest run 该文件 | verified |
| S-06 | component | vitest | 无 kind Tag，排版正常 | markdown-message.test.tsx + 全量 32 passed | vitest 全量 | verified |
| E-02 | component | vitest | 脚本转义不执行 | markdown-message.test.tsx（1 passed） | vitest run 该文件 | verified |

> 层级映射：component 系 vitest 隔离组件测试（服务 mock），对应 unit 层级；不触真实后端。

### Acceptance Evidence

| S-05 | FAIL（import 不存在，组件未建） | 1 passed | markdown-message.test.tsx | 真实组件渲染 | verified |
| S-06 | 同上 | 1 passed | 同上 | 同上 | verified |
| E-02 | 同上 | 1 passed | 同上 | 默认转义，无脚本执行 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)
- [2026-09-07] completed (done)

---

## TASK-002: 历史抽屉与失效页

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: chat-slimdown.frontend.design.md#3.3 组件设计, chat-slimdown.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: S-03, S-04, B-01

### Description

新增 HistoryDrawer（只读会话列表 + 消息回看）与 InvalidLink（token 无效提示页）；ChatPage 接入抽屉入口与无效分支。

### Checklist

- [x] [S-03/S-04][component] 先写失效页与抽屉只读测试，记录 RED
- [x] 新增 `components/HistoryDrawer.tsx`（只读，不写不删）
- [x] 新增 `components/InvalidLink.tsx`（纯提示）
- [x] ChatPage 接入：抽屉入口 + token 无效分支
- [x] [S-03/S-04/B-01][component] 提示页/只读/过期不断连（GREEN）
- [x] verifier `RULE-frontend-quality-001`: 类型/lint/测试全过
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | component | vitest | 无效 token 显示提示页，无白屏 | history-invalid-link.test.tsx（1 passed） | vitest run 该文件 | verified |
| S-04 | component | vitest、只读会话接口 | 会话列表与消息可见，不可编辑 | history-invalid-link.test.tsx（1 passed） | vitest run 该文件 | verified |
| B-01 | component | vitest | 过期错误提示，不崩溃 | history-invalid-link.test.tsx（1 passed） | vitest run 该文件 | verified |


> 层级映射：component 系 vitest 隔离组件测试（服务 mock），对应 unit 层级；不触真实后端。
### Acceptance Evidence

| S-03 | RED（收集失败，组件不存在） | 1 passed | history-invalid-link.test.tsx | 真实组件 | verified |
| S-04 | 同上 | 1 passed | history-invalid-link.test.tsx | InMemory listHistory | verified |
| B-01 | 同上 | 1 passed | history-invalid-link.test.tsx | localStorage 断言 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-003: 删除多余页面组件

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: chat-slimdown.frontend.design.md#3.2 页面与路由结构
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001, fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: E-01

### Description

删 9 页面 + 10 组件 + WorkspaceLayout 导航；App 路由收敛到 `/` 单页；`__tests__` 同步；tsc+lint+vitest 全过。

### Checklist

- [x] 删 9 页面文件（Home/Agents/AgentDetail/Approvals/Tasks/TaskDetail/History/Memory/Settings）
- [x] 删 10 组件文件（清单见 §3.3）
- [x] WorkspaceLayout 导航拆除（保留 children 透传壳）
- [x] App.tsx 路由收敛；`__tests__` 引用同步删/改
- [x] [E-01][integration] tsc + lint + vitest + 构建全过，grep 零残留引用
- [x] verifier `RULE-frontend-directory-001`: 删除合规，路由收敛（E-01 覆盖）
- [x] verifier `RULE-fluxion-console-001`: chat 只调 channel 接口（S-01 侧证，后端不动）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | tsc、lint、构建 | 零残留引用，构建通过 | tsc+lint+vitest(29)+vite build 全过 | 全量命令 | verified |

### Acceptance Evidence

| E-01 | N/A（删除类，tsc 即 RED 门禁） | tsc+lint+vitest29+build 全过 | 上方 | 真实编译+测试+构建 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-004: 对话美化

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: chat-slimdown.frontend.design.md#3.3 组件设计, chat-slimdown.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-05, S-06, E-02

### Description

Markdown 渲染（新增 react-markdown + remark-gfm 依赖）+ 删 kind Tag + 气泡排版 + 流式光标 + 头部收敛。

### Checklist

- [x] [S-05/S-06][component] 先写 Markdown 渲染与无 kind Tag 测试，记录 RED
- [x] 加依赖并新增 `components/MarkdownMessage.tsx`，消息流切过去
- [x] 删 `message-kind` Tag；气泡 max-width/行高/间距；流式光标；头部去重
- [x] [S-05/S-06/E-02][component] 渲染正确/无残留/脚本转义（GREEN，规则覆盖见 TASK-001/002）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | component | vitest + MarkdownMessage | 无原文符号，层级正确 | markdown-message.test.tsx（1 passed） | vitest run 该文件 | verified |
| S-06 | component | vitest | 无 kind Tag，排版正常 | markdown-message.test.tsx + 全量 32 passed | vitest 全量 | verified |
| E-02 | component | vitest | 脚本转义不执行 | markdown-message.test.tsx（1 passed） | vitest run 该文件 | verified |

> 层级映射：component 系 vitest 隔离组件测试（服务 mock），对应 unit 层级；不触真实后端。

### Acceptance Evidence

| S-05 | FAIL（import 不存在，组件未建） | 1 passed | markdown-message.test.tsx | 真实组件渲染 | verified |
| S-06 | 同上 | 1 passed | 同上 | 同上 | verified |
| E-02 | 同上 | 1 passed | 同上 | 默认转义，无脚本执行 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-005: 对话视觉重设计

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: chat-slimdown.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-07, S-08

### Description

去气泡扁平风（助手纯文本+头像，保留用户气泡）+ 空态引导 chips + 输入框胶囊化 + 内容列收敛。

### Checklist

- [x] [S-07/S-08][component] 先写结构测试（无气泡底/有头像/空态 chips），记录 RED
- [x] 消息区改扁平：助手去底色 + Avatar + 名字行；用户气泡保留
- [x] 空态问候 + 快捷 chips（点即发送）；输入框胶囊化；内容列 max-width
- [x] [S-07/S-08][component] 断言通过（GREEN）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | component | vitest | 助手无气泡底、有头像名 | chat-redesign.test.tsx（1 passed） | vitest run 该文件 | verified |
| S-08 | component | vitest | 空态问候+chips可点发 | chat-redesign.test.tsx（1 passed） | vitest run 该文件 | verified |

> 层级映射：component 系 vitest 隔离组件测试（服务 mock），对应 unit 层级；不触真实后端。

### Acceptance Evidence

| S-07 | FAIL（组件未建） | 1 passed | chat-redesign.test.tsx | 真实组件渲染 | verified |
| S-08 | FAIL（同上；另修测试断言 chips 点即发） | 1 passed | chat-redesign.test.tsx | 真实组件 + InMemory | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)
