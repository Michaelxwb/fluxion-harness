# Tasks: Phase 4 Product Experience（前端）

- **Source**: `.code-flow/tasks/2026-08-28/phase4-product-experience/phase4-product-experience.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-28（v0.2： remediation §15 修订 + TASK-017）

## Proposal

落地 Phase 4 前端 Product Experience：Chat Web 普通用户 Workspace（X401 shell 审计对齐 + X402–X408 七个页面，React Router 深链路由）与 Console Builder/Admin 完整旅程（C401 导航 IA 核对 + C403 Workflow Studio V2 节点编辑 + C405 User 360 升级 + C407 Operations 升级）。两 App 从 state 导航迁移到 `react-router-dom`；Phase 2/3 后端未就绪部分以 in-memory service 同契约先行（`/workspace/*` 等 ⛳依赖缺口端点契约冻结在 TS 接口）；以固定术语 denylist 断言（普通用户核心页底层术语=0）与三组 Journey E2E（成功率≥95%）闭合 Phase 4 Gate。

依据 design 对齐项（用户 2026-08-28 确认）：X401/C401 审计不重做；Eval 页占位；denylist 固定清单；journey 成功率测量；页面+E2E 用 in-memory service。

**v0.2 修订**（按 `fluxion-phase1-closure-detailed-remediation.md` §15，历史文档 git 历史可查，2026-08-28）：X401 由「审计对齐」改为完整 WorkspaceLayout 实现 + Settings 页（§15.1，TASK-003）；Agent Studio 保存链修复归 **phase1-closure** TASK-007/008，本阶段新增 TASK-017 做 C402 UX 深化（§15.2）；C401 继承 Closure 的 Console IA 修正断言（默认 Overview/Build 单一 Agents 菜单/Binding 下沉，§15.3–15.5，TASK-004）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-003 | planned |
| S-02 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-005 | planned |
| S-03 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-006 | planned |
| S-04 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-007 | planned |
| S-05 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-008 | planned |
| S-06 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-009 | planned |
| S-07 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-010 | planned |
| S-08 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-011 | planned |
| S-09 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-004 | planned |
| S-10 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-012 | planned |
| S-11 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-012 | planned |
| S-12 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-013 | planned |
| S-13 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-014 | planned |
| E-01 | phase4-product-experience.design.md#2.4 验收条件 | integration | Service → UI（真实组件树） | TASK-010 | planned |
| E-02 | phase4-product-experience.design.md#2.4 验收条件 | integration | Service → UI（校验诊断） | TASK-012 | planned |
| E-03 | phase4-product-experience.design.md#2.4 验收条件 | integration | Service → UI（真实组件树） | TASK-008 | planned |
| E-04 | phase4-product-experience.design.md#2.4 验收条件 | integration | Service → UI（流式通道） | TASK-011 | planned |
| B-01 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-003 | planned |
| B-02 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Router → Service → UI（文案断言遍历） | TASK-015 | planned |
| B-03 | phase4-product-experience.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI（三组 journey 套件） | TASK-016 | planned |
| B-04 | phase4-product-experience.design.md#2.4 验收条件 | integration | Service → UI（空态渲染） | TASK-007 | planned |
| S-14 | phase4-product-experience.design.md#2.2 功能方案（FEAT-P4-15） | E2E | Browser → Router → Service → UI | TASK-017 | planned |

> NFR-PERF-01（首屏 P95≤500ms 或基线不劣化）、NFR-A11Y-01（axe + 键盘遍历）、NFR-ACC-01（denylist=0）分别由 TASK-016、TASK-016、TASK-015 承载。⛳依赖缺口端点（`/workspace/*` × 6、queues/workers）在 TASK-001/002 契约冻结、in-memory 先行，后端 Phase 2/3 就绪后同契约切 HTTP。

---

## TASK-001: Chat in-memory service 契约扩展

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase4-product-experience.design.md#3.5 状态与数据流, phase4-product-experience.design.md#4 风险与依赖
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: S-02~S-08（数据源前置）

### Description

扩展 `ChatApi` TS 接口契约并实现 in-memory 版本：`listAgents()`、`listRecentTasks()`/`listTasks()`/`getTask(id)`、`listApprovals()`/`decideApproval(id, decision, comment?)`、`listHistory()`（⛳依赖缺口 `/workspace/*` 六端点）；`getProfile()`/`updateProfile()`、`listMemory()`/`correctMemory()`/`deleteMemory()`、`setAutoLearn(enabled)`（Phase 2 契约对齐）。in-memory 与 http 双实现共享同一 TS 接口契约；envelope `{code, message, data, request_id}` 解包逻辑统一在 httpClient/services 层；契约冻结供 Phase 2/3 后端对齐。

### Checklist

- [ ] 定义 workspace/profile/memory 全部方法的 TS 接口类型（冻结契约）
- [ ] 实现 in-memory 版本（含审批状态机、学习开关→不再新增 Memory 的模拟语义）
- [ ] 契约测试：in-memory 与 http 双实现对同一接口契约的类型/返回形状一致
- [ ] envelope 解包统一走现有 httpClient 封装，services 层无手写响应结构
- [ ] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 `pnpm --filter chat test -- services`（planned）：断言全部 JSON API 经统一 envelope 消费（`code=0` 成功 / 非 0 走错误路径）、组件层零裸 `fetch`、错误路径携带 `message` 与 `request_id`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| 契约一致性 | integration | in-memory/http 双实现 vs 同一 TS 契约 | 方法集合一致；返回形状一致；envelope 解包路径唯一 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-002: Console in-memory service 契约扩展

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase4-product-experience.design.md#3.5 状态与数据流
- **Acceptance-Refs**: S-10~S-14（数据源前置）

### Description

扩展 `ConsoleApi`/shared `productClient`：`getWorkflowSchema()`/`validateWorkflow(draft)`（V2 九节点判别联合校验，诊断逐字段）、`listWorkflowRuns()`（Phase 3 workflow_run 投影契约）、`listQueues()`/`listWorkers()`（⛳依赖缺口，Phase 3 运营视图）。in-memory 先行，V2 schema 校验随 Phase 3 升级同契约切换。

### Checklist

- [ ] 定义 `WorkflowDraftV2`/节点 schema/诊断结构/runs/queues/workers 的 TS 接口类型（冻结契约）
- [ ] 实现 in-memory 校验（判别联合字段完整性 + `{{ node_id.output }}` 插值存在性检查）与三个列表数据源
- [ ] 契约测试：in-memory 校验诊断与 V2 schema 字段一一对应
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| 契约一致性 | integration | in-memory 校验器 vs V2 schema 契约 | 9 种节点判别联合校验；诊断定位到字段 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-003: Chat Router 接入 + X401 WorkspaceLayout 实现

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase4-product-experience.design.md#3.1 技术选型, phase4-product-experience.design.md#3.2 页面与路由结构, phase4-product-experience.design.md#2.2 功能方案
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-01, B-01

### Description

Chat Web 接入 `react-router-dom`：`/` 重定向 `/home`，路由表含 `/home`/`/agents`/`/agents/:agentId`/`/tasks`/`/tasks/:taskId`/`/approvals`/`/history`/`/memory`/`/chat`/`/settings`（页面后续任务填充，先占位路由）。X401 实现为完整 **WorkspaceLayout**（非纯审计，remediation §15.1）：侧边导航含 首页/智能体/任务/审批/历史/记忆/对话/**设置**，顶栏绑定状态 + 主题切换，Router Outlet；新增 Settings 页（主题/语言/通知偏好，UserPreference 契约）。未绑定用户仅 `/bind` 流程可见、其余导航不显示（B-01，正式 Channel 规则）。`main.tsx` 第一条 UI 导入仍为 `@douyinfe/semi-ui/react19-adapter`。

### Checklist

- [ ] 接入 Router（路由表 + Outlet + 重定向），X401 WorkspaceLayout 实现八项导航（含设置）
- [ ] Settings 页（`/settings`）：主题/语言/通知偏好（UserPreference 契约，in-memory 先行）
- [ ] 未绑定分支：仅 `/bind` 可达，导航隐藏（resolveAccess 驱动）
- [ ] [S-01][E2E] 修改生产代码前，按 Browser → Router → Service → UI 编写验收测试并记录 RED：绑定用户打开 chat → 侧边导航含 首页/智能体/任务/审批/历史/记忆/对话/设置，顶栏显示已绑定用户与主题切换
- [ ] [B-01][E2E] 修改生产代码前，编写验收测试并记录 RED：未绑定用户打开 chat → 仅 `/bind` 流程可见，其余导航不显示
- [ ] **Spec verifier**：`RULE-fluxion-console-001` — 运行 S-01/B-01 verifier 套件（`pnpm --filter chat test -- e2e`，planned）：断言 Web Chat 正式 Channel 语义（未绑定仅 `/bind`、绑定后映射 PlatformUser）在路由层成立
- [ ] **Spec verifier**：`RULE-frontend-semi-001` — 运行 `pnpm --filter chat test -- ui-rules`（planned）：断言 `main.tsx` 首条 UI 导入为 react19-adapter、新代码仅用 `@douyinfe/semi-ui`/`semi-icons`、无 antd/MUI 等第二套组件库依赖
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 真实 Router + Layout + in-memory resolveAccess | 导航八项齐全（含设置）；顶栏绑定状态 + 主题切换 | planned | planned | planned |
| B-01 | E2E | 真实 Router 未绑定分支 | 仅 `/bind` 可达；其余导航隐藏 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-004: Console Router 迁移 + C401 IA 核对 + Eval 占位

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase4-product-experience.design.md#3.2 页面与路由结构, phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#4 风险与依赖
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: S-09

### Description

Console `App.tsx` 的 `renderView` state 导航拆分为路由表（`ConsoleView` 映射到路径，行为不变）：`/overview`、`/build/agents`、`/build/agent-studio`、`/build/workflows`（新）、`/build/capabilities`、`/build/eval`（占位）、`/users`、`/governance/*`、`/operations/*`（queues/workers 新）、`/platform/*`。C401 IA 核对：导航树对齐 roadmap §6（Overview/Build/Users/Governance/Operations/Platform），**并继承 Phase 1 Closure 的 IA 修正断言**（默认视图 Overview、Build 下 Agents 单一一级入口 + 页内新建 CTA、Binding 非一级导航，remediation §15.3–15.5，由 `phase1-closure` TASK-011 先行落地）；Eval 入口置灰占位空态页。已落地页只做导航路径对齐审计，不重做。现有 E2E 全量回归（RISK-P4-03）。

### Checklist

- [ ] `renderView` 拆路由表（`ConsoleView` 映射保持），导航树对齐 IA，Eval 置灰占位
- [ ] 继承断言（Closure IA 修正落地后）：默认 Overview、Build 单一 Agents 入口、Binding 非一级（remediation §15.3–15.5）
- [ ] [S-09][E2E] 修改生产代码前，按 Browser → Router → Service → UI 编写验收测试并记录 RED：导航含 Overview/Build/Users/Governance/Operations/Platform，Eval 入口置灰
- [ ] 两 App 目录纪律检查：新页面入 `src/pages/`、通用组件入 `src/components/` 或 shared
- [ ] 现有 Console E2E 全量回归通过（state→Router 迁移无行为回归）
- [ ] **Spec verifier**：`RULE-frontend-directory-001` — 运行 `pnpm --filter console test -- directory` + chat 同套件（planned，静态扫描断言）：断言页面在 `src/pages/`、通用组件在 `src/components/`/shared、测试目录与源码同构、组件无越界 import
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | E2E | 真实 Router + Console 导航树 | IA 七组齐全；Eval 置灰；旧视图映射无回归 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-005: X402 Home

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计, phase4-product-experience.design.md#3.6 UI 状态
- **Acceptance-Refs**: S-02

### Description

`/home` 页面：`HomePage` 容器 + `RecentTaskList`/`QuickAgentList` 展示组件；最近任务 + 常用 Agent 卡片，一键发起对话/任务并跳转。四态齐全（loading Skeleton / empty 空态 / error ErrorBanner+重试 / success 列表）。

### Checklist

- [ ] 实现 `HomePage`/`RecentTaskList`/`QuickAgentList`（容器/展示分离，事件上抛）
- [ ] [S-02][E2E] 修改生产代码前，按真实组件树 + in-memory service 编写验收测试并记录 RED：首页展示最近任务列表 + 常用 Agent 卡片，点击可跳转对话/任务详情
- [ ] 四态断言：loading/empty/error/success 全覆盖
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | 真实组件树 + in-memory service + Router | 最近任务 + 常用 Agent；点击跳转正确 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-006: X403 Agents 目录 + 详情发起

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计, phase4-product-experience.design.md#3.6 UI 状态
- **Acceptance-Refs**: S-03

### Description

`/agents` + `/agents/:agentId`：`AgentsPage` 容器 + `AgentCardList`/`AgentCard` 展示组件；按 AgentDefinition 产品模型展示（名称/描述/能力/可用性），不暴露 RuntimeProfile 等底层字段（§2.2 FEAT-P4-03 + runtime-core 术语约束）。选中 Agent 发起任务跳转对话页。四态齐全（empty「暂无可用智能体」）。

### Checklist

- [ ] 实现 `AgentsPage`/`AgentCardList`/`AgentCard` + Agent 详情发起路由
- [ ] [S-03][E2E] 修改生产代码前，编写验收测试并记录 RED：目录按产品模型展示（无 RuntimeProfile 字样），选中发起后跳转对话页
- [ ] 四态断言（含 empty/error）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | 真实组件树 + in-memory service + Router | 产品模型字段展示；发起跳转对话页 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-007: X404 Tasks 列表 + 详情

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计, phase4-product-experience.design.md#2.4 验收条件
- **Acceptance-Refs**: S-04, B-04

### Description

`/tasks` + `/tasks/:taskId`：`TasksPage`/`TaskDetailPage` 容器 + `TaskList`/`TaskStatusTag` 展示组件；对话/Workflow 运行统一展示状态、进度、结果；详情页展示启动信息。空态「暂无任务」+ 引导入口（B-04）。四态齐全。

### Checklist

- [ ] 实现 `TasksPage`/`TaskDetailPage`/`TaskList`/`TaskStatusTag`
- [ ] [S-04][E2E] 修改生产代码前，编写验收测试并记录 RED：任务列表（含 workflow 运行）状态/进度/结果正确；详情页展示启动信息
- [ ] [B-04][integration] 修改生产代码前，编写验收测试并记录 RED：任务列表为空 → 空态文案 + 引导入口
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实组件树 + in-memory service + Router | 状态/进度/结果正确；详情可达 | planned | planned | planned |
| B-04 | integration | 真实组件树空数据渲染 | 空态文案 + 引导入口 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-008: X405 Approvals 审批

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计, phase4-product-experience.design.md#4 风险与依赖
- **Acceptance-Refs**: S-05, E-03

### Description

`/approvals`：`ApprovalsPage` 容器 + `ApprovalList`/`ApprovalRow` 展示组件；HumanTask 审批队列，通过/拒绝/留言（契约按 Phase 3 HumanTask recv_async/send 语义设计，in-memory 模拟状态机）。操作后该项从待确认消失并出现成功提示；审批接口失败 → 错误提示 + 列表保持待确认（E-03）。`{ pending: Map<id, 'submitting'> }` 局部状态防重复提交。

### Checklist

- [ ] 实现 `ApprovalsPage`/`ApprovalList`/`ApprovalRow`（通过/拒绝/留言 + submitting 状态）
- [ ] [S-05][E2E] 修改生产代码前，编写验收测试并记录 RED：对一条 HumanTask 通过 → 该项消失 + 成功提示；拒绝/留言同样生效
- [ ] [E-03][integration] 修改生产代码前，编写验收测试并记录 RED：审批通过接口失败 → 错误提示，列表保持待确认
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实组件树 + in-memory 审批状态机 | 通过/拒绝/留言生效；列表即时更新 | planned | planned | planned |
| E-03 | integration | 真实组件树 + 失败注入 service | 错误提示；列表保持待确认 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-009: X406 History 统一时间线

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计
- **Acceptance-Refs**: S-06

### Description

`/history`：`HistoryPage` 容器 + `HistoryTimeline` 展示组件；对话 + 任务统一时间线（时间倒序），详情可展开（关联 trace 入口）。四态齐全（empty「暂无历史记录」）。

### Checklist

- [ ] 实现 `HistoryPage`/`HistoryTimeline`（统一时间线 + 详情展开）
- [ ] [S-06][E2E] 修改生产代码前，编写验收测试并记录 RED：对话 + 任务统一列表、时间倒序、详情可展开
- [ ] 四态断言
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实组件树 + in-memory service | 统一时间线倒序；详情展开 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-010: X407 Memory & Profile

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计, phase4-product-experience.design.md#3.4 组件接口契约
- **Spec-Refs**: （复用 TASK-015 的 runtime-core 断言；本任务无独立 rule owner）
- **Acceptance-Refs**: S-07, E-01

### Description

`/memory`：`MemoryProfilePage` 容器 + `ProfileForm`/`MemoryList`/`MemoryRow`/`LearningSwitch` 展示组件（§3.4 契约：`items`/`learningEnabled` props 只读，`onCorrect`/`onDelete`/`onToggleLearning` 事件上抛，删除须二次确认）。Profile 查看/编辑保存；Personal Memory 列表/纠正/删除；自动学习开关（US-03 全闭环，对应 Phase 2 learning control 契约）。纠正/删除接口失败 → 字段级错误提示 + 重试按钮、列表保持原状（E-01）。开关关闭后不再新增 Memory。

### Checklist

- [ ] 实现 `MemoryProfilePage`/`ProfileForm`/`MemoryList`/`MemoryRow`/`LearningSwitch`（props 只读 + 回调上抛）
- [ ] 删除走二次确认 Modal（焦点管理：打开落入、关闭归还）
- [ ] [S-07][E2E] 修改生产代码前，编写验收测试并记录 RED：Profile 编辑保存成功提示；Memory 纠正/删除生效；自动学习关闭后不再新增 Memory
- [ ] [E-01][integration] 修改生产代码前，编写验收测试并记录 RED：Memory 删除/纠正接口失败 → 错误提示 + 重试按钮，列表保持原状
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实组件树 + in-memory service（含学习开关语义） | Profile 保存；纠正/删除生效；停学后无新增 | planned | planned | planned |
| E-01 | integration | 真实组件树 + 失败注入 service | 错误提示 + 重试；列表保持原状 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-011: X408 Chat 集成迁移

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-006
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.2 页面与路由结构, phase4-product-experience.design.md#2.4 验收条件
- **Acceptance-Refs**: S-08, E-04

### Description

`/chat`：`ChatPage` 容器迁移现有 ChatApp 对话能力（流式 `sendMessageStream` 复用）；从 Agent 目录（`/agents/:agentId`）选择 Agent 后携带上下文进入对话；流式回复渲染，完成后显示 kind 标签；绑定状态保持（Workspace Layout 内）。流式发送中断 → error 帧 + 可重试入口，已收内容保留（E-04）。未绑定语义由 TASK-003 路由层保证。

### Checklist

- [ ] 迁移 ChatApp 能力到 `/chat` 路由（现有流式能力复用，不重写）
- [ ] Agent 目录 → 对话页上下文衔接（agentId 透传）
- [ ] [S-08][E2E] 修改生产代码前，编写验收测试并记录 RED：选择 Agent → 发送消息 → 流式渲染，完成显示 kind 标签，绑定状态保持
- [ ] [E-04][integration] 修改生产代码前，编写验收测试并记录 RED：流式中断 → error 帧 + 可重试入口，已收内容保留
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实组件树 + in-memory 流式 service | 流式渲染；kind 标签；绑定状态保持 | planned | planned | planned |
| E-04 | integration | 真实组件树 + 中断注入流式通道 | error 帧 + 重试入口；已收内容保留 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-012: C403 Workflow Studio

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-004
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.4 组件接口契约, phase4-product-experience.design.md#3.6 UI 状态
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-10, S-11, E-02

### Description

`/build/workflows`（WorkflowsPage 升级）：`WorkflowStudioPage` 容器 + `WorkflowNodeList`/`NodeConfigForm`/`JsonEditorTab`/`StudioToolbar`。SchemaForm 按 9 种节点判别联合（capability/agent/condition/switch/parallel/transform/wait/human_task/subworkflow）渲染字段集，切换节点类型表单随之切换；`{{ node_id.output }}` 插值校验；JSON 高级模式 tab（现有 DSL textarea 迁移）；校验（诊断逐条定位到字段，E-02）/发布/版本管理（复用现有动作）；草稿状态 `WorkflowDraftV2` 容器持有。表单驱动非画布（有意妥协）。

### Checklist

- [ ] 实现 `WorkflowStudioPage` + `WorkflowNodeList`/`NodeConfigForm`（SchemaForm 扩展 kind）/`JsonEditorTab`/`StudioToolbar`
- [ ] 节点类型切换 → 字段集切换；生成 JSON 符合 V2 判别联合；插值校验
- [ ] [S-10][E2E] 修改生产代码前，编写验收测试并记录 RED：新建草稿 → 添加 capability 节点填配置 → 校验 → 发布 → 版本列表出现新版本
- [ ] [S-11][E2E] 修改生产代码前，编写验收测试并记录 RED：切换节点类型（capability→condition→parallel→human_task）→ 字段集切换 + 生成 JSON 符合 V2 判别联合 + 插值校验
- [ ] [E-02][integration] 修改生产代码前，编写验收测试并记录 RED：节点配置不合法（判别联合字段缺失）→ 校验诊断逐条列出并定位到字段
- [ ] **Spec verifier**：`RULE-frontend-component-001` — 运行组件契约套件（`pnpm -r test -- component-contracts`，planned）：断言全部新增组件（`NodeConfigForm`/`WorkflowNodeList`/`MemoryList`/`ApprovalRow` 等）props 只读、事件回调上抛、容器/展示分离（容器持数据、展示纯 UI）、§3.4 接口契约类型完整
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | 真实组件树 + in-memory 校验/发布 | 节点表单按 V2 schema 渲染；发布可用；版本出现 | planned | planned | planned |
| 组件契约（S-05/S-07 上抛口径） | integration | 真实组件实例 props/回调断言 | props 不可变；操作经回调上抛 | planned | planned | planned |
| S-11 | E2E | 真实组件树类型切换 | 字段集随类型切换；JSON 符合判别联合；插值校验 | planned | planned | planned |
| E-02 | integration | 真实组件树 + 非法草稿 | 诊断逐条定位字段 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-013: C405 User 360 升级

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计, phase4-product-experience.design.md#3.5 状态与数据流
- **Acceptance-Refs**: S-12

### Description

`/users`（UsersChannelsPage 升级为用户列表 + 360 详情）：`User360Page` 容器 + `User360Header`/`User360Tabs` 展示组件（shared `User360View` 复用，`getUser360(userId)` 现有契约）。五维度：Identity / Profile / Capability / Policy / Activity。四态齐全（empty「该用户暂无数据」）。

### Checklist

- [ ] 实现 `User360Page`/`User360Header`/`User360Tabs`（五维度 Tab，shared `User360View` 复用）
- [ ] [S-12][E2E] 修改生产代码前，编写验收测试并记录 RED：用户列表 → 选择用户 → 360 详情含五维度
- [ ] 四态断言
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-12 | E2E | 真实组件树 + in-memory/console service | 五维度 Tab 齐全；列表→详情可达 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-014: C407 Operations 升级

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-004
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#3.3 组件设计
- **Acceptance-Refs**: S-13

### Description

`/operations/runs`（RunsPage 升级，含 trace 关联）+ `/operations/queues`（`QueuesPanel`，workflow 队列）+ `/operations/workers`（`WorkersPanel`，运行 Worker 状态）新建；`OperationsPage` 容器 + 三展示面板。数据源：runs 走 Phase 3 workflow_run 投影契约、queues/workers 为 ⛳依赖缺口（in-memory 展示形态先行）。四态齐全。

### Checklist

- [ ] 实现 `OperationsPage`/`RunsTable`（trace 关联）/`QueuesPanel`/`WorkersPanel`
- [ ] [S-13][E2E] 修改生产代码前，编写验收测试并记录 RED：执行记录含 trace 关联；切换队列/Worker 视图展示状态与数量
- [ ] 四态断言（含「无运行中队列/Worker」空态）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-13 | E2E | 真实组件树 + in-memory runs/queues/workers | trace 关联；三视图切换正确 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-015: 术语隐藏 denylist 统一断言

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#2.4 验收条件, phase4-product-experience.design.md#4 风险与依赖
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: B-02

### Description

固定 denylist（`RuntimeProfile`/`Registry`/`Resource`/`Binding`/`Plugin`/`Workflow 底层态`）；普通用户核心页（chat 全部页面 + console 普通用户可见面）断言页面文案中 denylist 术语出现次数 = 0。console 沿用 Phase 1 terminology 测试模式，chat 补齐同套件；只覆盖普通用户可见面，Admin/Builder 视图不受限（RISK-P4-05：只覆盖范围固定，避免误伤）。

### Checklist

- [ ] 定义固定 denylist 清单（单一事实源，双端引用）
- [ ] [B-02][E2E] 修改生产代码前，编写验收测试并记录 RED：遍历普通用户核心页 → denylist 术语出现次数 = 0
- [ ] chat 术语套件与 console terminology 测试模式统一（同一清单、同一断言方式）
- [ ] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 B-02 套件 + S-03 断言（planned）：断言普通用户面不暴露 Runtime 内部（denylist=0）、Agent 目录按产品模型展示
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-02 | E2E | 真实页面渲染文案遍历（chat 全部 + console 普通用户面） | denylist 术语出现次数 = 0 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-016: 三组 Journey E2E + Phase 4 Gate

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-012, TASK-013, TASK-014, TASK-015
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#2.4 验收条件
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: B-03, NFR-PERF-01, NFR-A11Y-01

### Description

三组 E2E journey 套件：Workspace task（绑定→发起→对话→审批→记忆管理）、Build（Studio 建工作流→校验→发布）、Admin（用户 360→治理→运营）。成功率 = 通过数/总数 ≥95%（Phase 4 Gate 可观测测量），失败项有可定位诊断。Gate 证据聚合：四态覆盖（E-01~E-04 已在各页任务 verified）、NFR-A11Y-01（axe + 键盘遍历，审批/Memory 删除等操作键盘可达、焦点管理）、NFR-PERF-01（首屏 P95≤500ms in-memory 实测；无实测前现有页面基线不劣化）、组件质量扫描（无裸 fetch、TS 无 `any`/`@ts-ignore` 滥用、容器/展示分离）。

### Checklist

- [ ] 搭建三组 journey 套件骨架（Workspace/Build/Admin persona 路径串联各页 E2E）
- [ ] [B-03][E2E] 修改生产代码前，编写验收测试并记录 RED：运行三组 journey 套件 → 成功率 ≥95%（通过数/总数），失败项有可定位诊断
- [ ] 无裸 `fetch` 扫描 + `any`/`@ts-ignore` 检查 + 容器/展示分离断言（全部新页面）
- [ ] axe 扫描 + 键盘遍历测试（审批通过/拒绝、Memory 删除等键盘可达、焦点管理）
- [ ] 首屏可交互基线记录（NFR-PERF-01：实测或基线不劣化说明）
- [ ] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 journey 套件 + 四态用例聚合（planned）：断言 E-01~E-04 error 态证据、B-03 成功率、a11y/perf 证据全部为编码期自动化产出
- [ ] **Spec verifier**：`RULE-frontend-quality-001` — 运行质量扫描套件（`pnpm -r test -- quality`，planned）：断言组件不裸 fetch、目录/命名规范、全部新页面测试覆盖、B-03 journey 在套件内
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | E2E | 真实浏览器流程（三组 journey，全页面串联） | 成功率 ≥95%；失败项诊断可定位 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-017: C402 Agent Studio UX 深化

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-004, phase1-closure TASK-007, phase1-closure TASK-008
- **Source**: phase4-product-experience.design.md#2.2 功能方案, phase4-product-experience.design.md#4 风险与依赖
- **Acceptance-Refs**: S-14

### Description

在 Phase 1 Closure 修复 Studio 保存链（`phase1-closure` TASK-007 round-trip + TASK-008 Typed CapabilityPicker）基础上补齐 C402 UX（remediation §15.2「在 Phase 1 Closure 修数据模型，在 Phase 4 做完整 UX」）：版本管理（版本列表/对比/回滚入口）、试跑结果面板、能力资产引用展示（typed binding 可视化）。四态齐全。

### Checklist

- [ ] Studio 版本管理视图 + 试跑结果面板 + 能力资产引用展示（typed binding 可视化）
- [ ] [S-14][E2E] 修改生产代码前，编写验收测试并记录 RED：打开 Agent Studio → 版本列表可见、试跑产出结果面板、能力引用展示 type/ref/version 三元组
- [ ] 四态断言（loading/empty/error/success）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-14 | E2E | 真实组件树 + in-memory/console service（Closure 修复后的保存链） | 版本/试跑/资产引用三视图可用；round-trip 保持 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)（v0.2 新增）
