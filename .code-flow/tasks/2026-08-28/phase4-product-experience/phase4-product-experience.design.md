# Phase 4 Product Experience 前端模块需求与设计简报

> **文档编号**: FE-P4-01
> **文档版本**: v0.1
> **创建日期**: 2026-08-28
> **文档状态**: 评审中

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3 章（前端技术设计）→ 通过后锁定设计基线

**ID 体系**: US（来自 PRD）、FEAT（功能）、CMP（组件）、NFR（非功能指标）
场景编号：S-（正常）、E-（异常）、B-（边界）

**范围声明**: Phase 4 为**前端 Product Experience**（design-frontend 模板）。后端领域契约与 API 由 Phase 2（User Context/Profile/Memory）与 Phase 3（Workflow 后端）设计简报覆盖；本简报在 §3.5「数据获取层」列出页面消费的 API 契约，并将 Phase 2/3 未定义的工作区侧新端点登记为**依赖缺口**（§4），不重复设计后端。

---

## 目录

- [1. 文档控制](#1-文档控制)
- [2. 需求分析](#2-需求分析)
  - [2.1 需求概述](#21-需求概述-必填)
  - [2.2 功能方案](#22-功能方案-必填)
  - [2.3 范围与边界](#23-范围与边界-必填)
  - [2.4 验收条件](#24-验收条件-必填)
- [3. 前端技术设计](#3-前端技术设计)
  - [3.1 技术选型](#31-技术选型-必填)
  - [3.2 页面与路由结构](#32-页面与路由结构-必填)
  - [3.3 组件设计](#33-组件设计-必填)
  - [3.4 组件接口契约](#34-组件接口契约-必填)
  - [3.5 状态与数据流](#35-状态与数据流-必填)
  - [3.6 UI 状态](#36-ui-状态-必填)
  - [3.7 样式方案](#37-样式方案-必填)
  - [3.8 可访问性与兼容性](#38-可访问性与兼容性-按需)
- [4. 风险与依赖](#4-风险与依赖)
- [Spec Compliance Matrix](#spec-compliance-matrix)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | Fluxion 团队 | 前端方案、代码实现 |
| 设计/交互 | Fluxion 团队 | 信息架构与交互稿（以 roadmap §6 + console-design v1.6 §3.2.2 为基线） |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-08-28 | Fluxion 团队 | 初始草稿（评审中） |
| v0.2 | 2026-08-28 | Fluxion 团队 | 按 `fluxion-phase1-closure-detailed-remediation.md` §15（历史文档，git 历史可查）修订：X401 由「审计对齐」改为完整 WorkspaceLayout 实现 + Settings（§15.1）；C402 Agent Studio 保存链修复归 Phase 1 Closure、本阶段做 UX 深化（§15.2）；C401 继承 Closure 的 Console IA 修正（默认 Overview/Build 单菜单/Binding 下沉，§15.3–15.5） |

---

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|------|------|
| **模块名称** | Phase 4 Product Experience（User Workspace + Agent/Workflow Studio + User 360 / Governance / Operations） |
| **需求类型** | 新页面 + 组件 + 交互优化 + 信息架构核对（多页面落地） |
| **业务背景** | v2.2 roadmap §6；PRD FEAT-21（User Workspace）/ FEAT-22（Agent/Workflow Studio）/ FEAT-23（User 360 / Governance / Operations）。Phase 1 已落地 Console 主体页与 Chat 绑定流程；本阶段补齐**普通用户工作区**与 **Build/Admin 完整旅程**，达成 Phase 4 Gate（三组 Journey 成功率≥95%、普通用户核心页底层术语暴露=0）。后端契约由 Phase 2/3 设计简报提供。 |
| **核心目标** | 让普通用户只面向 Agent/业务能力（不接触 RuntimeProfile/Registry/Binding/Plugin 等底层术语）即可完成任务；让 Builder 与 Admin 在 Console 中走通 构建→测试→发布→治理 完整旅程。 |

**已确认的设计对齐项（用户 2026-08-28 确认）**：

| # | 对齐点 | 结论 |
|---|--------|------|
| A | Workspace 范围 | 设计**完整目标体验** X401–X409（含 Settings）；X401 shell 结构已在 Phase 1 落地（TASK-019），本阶段实现为完整 **WorkspaceLayout**（Router 化 + 八项导航 + Settings 页），**非纯审计**（remediation §15.1）。 |
| B | Console IA 最终树 | 采用 roadmap §6 IA；Eval 页归 Phase 5，本阶段**占位**（导航入口 + 空态页）。C401 仅做导航结构核对，不重做已落地页。 |
| C | 术语隐藏 denylist | 固定 denylist（`RuntimeProfile`/`Registry`/`Resource`/`Binding`/`Plugin`/`Workflow` 底层态）；沿用 console 的 terminology 测试模式，chat 也加断言。 |
| D | Journey 成功率测量 | 三组 E2E journey 套件（Workspace task / Build / Admin），成功率 = 通过数/总数 ≥95%。 |
| E | 测试与后端依赖 | 页面 + E2E 用 **in-memory service**（`inMemoryChatApi`/`inMemoryConsoleApi` 扩展），真 HTTP client 后端就绪后同契约切换。 |
| F | Closure 继承 | Agent Studio **保存链修复**（数据 bug）与 Console IA 修正（默认 Overview/Build 单一 Agents 菜单/Binding 下沉）由 **Phase 1 Closure**（`.code-flow/tasks/2026-08-28/phase1-closure/`）先行落地；本阶段 C402 做 UX 深化、C401 做继承核对（remediation §15.2–15.5）。 |

---

### 2.2 功能方案 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-P4-01 | X401 Workspace Shell | 普通用户侧边导航（首页/智能体/任务/审批/历史/记忆/对话/**设置**）+ 顶栏绑定状态 + 主题切换。Phase 1 已落地的 shell 本阶段实现为完整 WorkspaceLayout（Router 化）并新增 Settings 页（remediation §15.1）。 | P0 | US-02 |
| FEAT-P4-02 | X402 Home | 最近任务、常用 Agent 快捷入口、一键发起对话/任务。 | P0 | US-01, US-04 |
| FEAT-P4-03 | X403 Agents | Agent 目录：按 AgentDefinition 产品模型展示（名称/描述/能力/可用性），用户选中后发起任务，不暴露 RuntimeProfile。 | P0 | US-01 |
| FEAT-P4-04 | X404 Tasks | 长期任务列表：对话/Workflow 运行统一展示状态、进度、结果；支持详情页。 | P0 | US-04 |
| FEAT-P4-05 | X405 Approvals | 待确认事项：HumanTask 审批队列，支持通过/拒绝/留言，操作后状态即时更新。 | P0 | US-04 |
| FEAT-P4-06 | X406 History | 运行历史：对话 + 任务统一时间线，可查看详情（关联 trace）。 | P0 | US-02, US-04 |
| FEAT-P4-07 | X407 Memory & Profile | Profile 查看/编辑；Personal Memory 列表/纠正/删除；自动学习开关（US-03 全闭环）。 | P0 | US-03 |
| FEAT-P4-08 | X408 Chat 集成 | 对话页作为正式 Channel 嵌入 Workspace；选择 Agent 后对话，流式回复；未绑定用户仅 `/bind`。 | P0 | US-02, US-04 |
| FEAT-P4-09 | C401 IA 核对 | Console 导航树与 roadmap §6 IA 对齐（Overview/Build/Users/Governance/Operations/Platform）；Eval 占位置灰。 | P0 | US-05, US-07 |
| FEAT-P4-10 | C403 Workflow Studio | WorkflowDefinition V2 节点可视化编辑：SchemaForm 驱动节点列表 + 逐节点配置表单（9 种节点判别联合），JSON 高级模式 tab，校验/发布/版本管理。 | P0 | US-06 |
| FEAT-P4-11 | C405 User 360 升级 | 用户全维度视图：Identity / Profile / Capability / Policy / Activity；从 UsersChannelsPage 升级为独立页面 + 详情。 | P0 | US-07 |
| FEAT-P4-12 | C407 Operations | 运营视图：Runs 升级（含 trace 关联）+ Queues（workflow 队列）+ Workers（运行 Worker 状态）。 | P0 | US-08, US-04 |
| FEAT-P4-13 | 术语隐藏 denylist | 普通用户核心页（chat 全部页面 + console 普通用户可见面）断言不出现底层术语；denylist 固定并统一测试。 | P0 | US-01 |
| FEAT-P4-14 | 三组 Journey E2E | Workspace task / Build / Admin 三组 E2E journey 套件，成功率 ≥95%（Phase 4 Gate 的可观测测量）。 | P0 | roadmap §6 Gate |
| FEAT-P4-15 | C402 Agent Studio UX 深化 | 在 Phase 1 Closure 修复保存链（`phase1-closure` TASK-007/008）基础上补齐 Studio UX：版本管理、试跑结果面板、能力资产引用展示（remediation §15.2「在 Phase 4 做完整 UX」）。 | P1 | US-06 |

---

### 2.3 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | Chat Web：X401 WorkspaceLayout 实现 + Settings + X402–X408 全部页面。Console：C401 导航核对（继承 Closure IA 修正）+ C402 Studio UX 深化 + C403 Workflow Studio + C405 User 360 升级 + C407 Operations 升级；导航路由从 state 迁移到 React Router。前端 in-memory service 扩展（覆盖新增页面 API 契约）。 |
| **非范围（Out of Scope）** | 后端领域/API 实现（Phase 2/3 负责）；Eval 实际页面（Phase 5）；术语隐藏影响 Admin/Builder 视图（他们需要底层术语）；Workflow V2 运行时语义（Phase 3）；移动端 App（仅响应式适配）。 |
| **有意妥协 / 技术债** | Workflow Studio 采用表单驱动而非拖拽画布（画布交互/测试成本高，表单严格贴合 V2 schema）；前端 E2E 暂以 in-memory 契约先行，后端就绪后同契约切 HTTP；Operations Queues/Workers 数据源依赖 Phase 3 workflow_run 投影表（未实现前 in-memory 展示形态）。 |

---

### 2.4 验收条件 [必填]

> 交互层面的可验证场景，写到可转 E2E / 组件交互测试断言的粒度。测试层级：`unit` / `integration` / `E2E`；跨路由、服务请求、状态更新和最终 UI 的用户流程必须标为 `E2E`。

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|--------|--------|--------|---------|-------------|---------|-------------|
| S-01 | FEAT-P4-01 | P0 | E2E | Browser → Router → Service → UI | 1. 绑定用户打开 chat 2. 查看导航与顶栏 | 侧边导航含 首页/智能体/任务/审批/历史/记忆/对话/设置，顶栏显示已绑定用户与主题切换 |
| S-02 | FEAT-P4-02 | P0 | E2E | Browser → Router → Service → UI | 1. 打开首页 2. 查看最近任务与常用 Agent | 最近任务列表 + 常用 Agent 卡片，点击可跳转对话/任务详情 |
| S-03 | FEAT-P4-03 | P0 | E2E | Browser → Router → Service → UI | 1. 打开智能体页 2. 浏览目录 3. 选中 Agent 发起任务 | Agent 目录按产品模型展示（无 RuntimeProfile 字样），发起后跳转对话页 |
| S-04 | FEAT-P4-04 | P0 | E2E | Browser → Router → Service → UI | 1. 打开任务页 2. 查看列表 3. 进入详情 | 长期任务列表（含 workflow 运行）状态/进度/结果正确；详情页展示启动信息 |
| S-05 | FEAT-P4-05 | P0 | E2E | Browser → Router → Service → UI | 1. 打开审批页 2. 对一条 HumanTask 选择通过 3. 查看列表刷新 | 审批操作后该项从待确认消失并出现成功提示；拒绝/留言同样生效 |
| S-06 | FEAT-P4-06 | P0 | E2E | Browser → Router → Service → UI | 1. 打开历史页 2. 查看统一时间线 3. 展开详情 | 对话 + 任务统一列表，时间倒序，详情可展开 |
| S-07 | FEAT-P4-07 | P0 | E2E | Browser → Router → Service → UI | 1. 打开记忆页 2. 查看 Profile 并编辑保存 3. 纠正/删除一条 Memory 4. 关闭自动学习 | Profile 保存成功提示；Memory 纠正/删除生效；自动学习开关关闭后不再新增 Memory |
| S-08 | FEAT-P4-08 | P0 | E2E | Browser → Router → Service → UI | 1. 选择 Agent 2. 发送消息 3. 接收流式回复 | 消息流式渲染，完成后显示 kind 标签，绑定状态保持 |
| S-09 | FEAT-P4-09 | P0 | E2E | Browser → Router → Service → UI | 1. 打开 Console 2. 核对导航树 | 导航含 Overview/Build/Users/Governance/Operations/Platform；默认视图 Overview、Build 单一 Agents 入口、Binding 非一级（继承 Closure IA 修正）；Eval 入口置灰占位 |
| S-10 | FEAT-P4-10 | P0 | E2E | Browser → Router → Service → UI | 1. 打开工作流 2. 新建草稿 3. 添加 capability 节点并填配置 4. 校验 5. 发布 6. 查看版本 | 节点表单按 V2 schema 渲染；校验通过后发布可用；版本列表出现新版本 |
| S-11 | FEAT-P4-10 | P0 | E2E | Browser → Router → Service → UI | 1. 切换节点类型（capability→condition→parallel→human_task） | 表单随类型切换字段集；生成 JSON 符合 V2 判别联合；含 `{{ node_id.output }}` 插值校验 |
| S-12 | FEAT-P4-11 | P0 | E2E | Browser → Router → Service → UI | 1. 打开用户页 2. 选择用户 3. 查看 360 详情 | 360 视图含 Identity/Profile/Capability/Policy/Activity 五个维度 |
| S-13 | FEAT-P4-12 | P0 | E2E | Browser → Router → Service → UI | 1. 打开运营页 2. 查看执行记录 3. 切换队列/Worker 视图 | 执行记录含 trace 关联；队列/Worker 面板展示状态与数量 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|--------|--------|---------|-------------|---------|---------|
| E-01 | FEAT-P4-07 | integration | Service → UI | Memory 删除/纠正接口失败 | 错误提示 + 重试按钮，列表保持原状 |
| E-02 | FEAT-P4-10 | integration | Service → UI | 节点配置不合法（判别联合字段缺失） | 校验诊断逐条列出并定位到字段 |
| E-03 | FEAT-P4-05 | integration | Service → UI | 审批通过接口失败 | 错误提示，列表保持待确认 |
| E-04 | FEAT-P4-08 | integration | Service → UI | 流式发送中断 | 出现 error 帧 + 可重试入口，已收内容保留 |

**边界场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|--------|--------|---------|-------------|---------|---------|
| B-01 | FEAT-P4-01/08 | E2E | Browser → Router → Service → UI | 未绑定用户打开 chat | 仅 `/bind` 流程可见，其余导航不显示（正式 Channel 规则） |
| B-02 | FEAT-P4-13 | E2E | Router → Service → UI（文案断言） | 遍历普通用户核心页 | 页面文案中 denylist 术语出现次数 = 0 |
| B-03 | FEAT-P4-14 | E2E | Browser → Router → Service → UI | 运行三组 journey 套件 | 成功率 ≥95%（通过数/总数），失败项有可定位诊断 |
| B-04 | FEAT-P4-04 | integration | Service → UI | 任务列表为空 | 空态文案 + 引导入口 |

**非功能指标**

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-PERF-01 | 页面首屏可交互 | P95 ≤ 500ms（in-memory 数据源，真浏览器实测） | Playwright/Lighthouse 实测；无实测前以现有页面基线不劣化 |
| NFR-A11Y-01 | 键盘可达 | 全部可交互元素 Tab 可达，焦点可见 | axe + 键盘遍历测试 |
| NFR-ACC-01 | 术语暴露 | 普通用户核心页 denylist 术语 = 0 | terminology 测试套件（jest 文案断言） |

---

## 3. 前端技术设计

### 3.1 技术选型 [必填]

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React + TypeScript | 19.1 / 5.9 | 已锁定（项目前端强制规范） |
| 构建 | Vite | 7.1 | 已锁定 |
| UI 组件 | `@douyinfe/semi-ui` + `@douyinfe/semi-icons` | 2.102.x | 唯一通用组件体系（RULE-frontend-semi-001） |
| 路由 | `react-router-dom` | 7.8 | 已安装未启用；Phase 4 两 App 从 state 导航迁移到 Router，支持 `/agents/:agentId`、`/tasks/:taskId` 深链与浏览器前进/后退 |
| 状态管理 | 局部状态 + service hook | — | 沿用现有模式，不引入状态库；跨页状态经 services 数据层 |
| 样式方案 | Semi Design Token + CSS Modules/类名 | — | 现有 `styles.css` + 类名约定延续 |
| 数据请求 | 现有 `services/`（in-memory + http 双实现同契约） | — | 页面禁止裸 `fetch`（RULE-frontend-quality-001）；Phase 2/3 后端未实现，in-memory 契约先行 |

---

### 3.2 页面与路由结构 [必填]

> 两 App 接入 `react-router-dom`；`main.tsx` 第一条 UI 导入仍为 `@douyinfe/semi-ui/react19-adapter`。

**Chat Web（普通用户 Workspace）**

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| Workspace Layout | `/`（重定向 `/home`） | Sider + Content | 侧边导航 + 顶栏；未绑定走 `/bind` 引导（B-01） |
| Home（X402） | `/home` | Content | 最近任务 + 常用 Agent |
| Agents（X403） | `/agents` | Content | Agent 目录（AgentDefinition 产品模型） |
| Agent 详情/发起 | `/agents/:agentId` | Content | 能力展示 + 发起对话/任务 |
| Tasks（X404） | `/tasks` | Content | 长期任务列表 |
| Task 详情 | `/tasks/:taskId` | Content | 运行状态/结果详情 |
| Approvals（X405） | `/approvals` | Content | HumanTask 审批队列 |
| History（X406） | `/history` | Content | 对话 + 任务统一时间线 |
| Memory & Profile（X407） | `/memory` | Content | Profile 编辑 + Personal Memory 管理 + 自动学习开关 |
| Chat（X408） | `/chat` | Content | 正式 Channel 对话（迁移现有 ChatApp 能力） |
| Settings（X409） | `/settings` | Content | 主题/语言/通知偏好（UserPreference 契约；remediation §15.1） |

**Console（Builder/Admin）**

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| Console Layout | `/`（重定向 `/overview`） | Sider + Content | 现有 App shell 迁移到 Router |
| Overview | `/overview` | Content | 平台概览（已落地，审计） |
| Agents 目录 | `/build/agents` | Content | 智能体目录（现有 resources 视图对齐产品模型） |
| Agent Studio | `/build/agent-studio` | Content | C402 UX 深化（保存链修复由 Phase 1 Closure 先行，remediation §15.2） |
| **Workflow Studio** | `/build/workflows` | Content | C403：WorkflowDefinition V2 节点编辑（本次新建/升级） |
| Capabilities | `/build/capabilities` | Content | C404 已落地（审计） |
| Eval | `/build/eval` | Content | 占位空态页（Phase 5 实页） |
| Users / User 360 | `/users` | Content | C405：用户列表 + 360 详情（升级 UsersChannelsPage） |
| Governance | `/governance/policies` `/governance/audit` `/governance/bindings` | Content | 已落地（审计；导航路径对齐） |
| Operations | `/operations/runs` `/operations/queues` `/operations/workers` | Content | C407：Runs 升级 + Queues + Workers（新建 Queues/Workers） |
| Platform | `/platform/runtime-profiles` `/platform/secrets` `/platform/models` `/platform/assets` | Content | C408 已落地（审计） |

> 新增页面 → 路由配置 + 入口；Console 现有 `App.tsx` 的 `renderView` 拆分为路由表，`ConsoleView` 映射到路径。

---

### 3.3 组件设计 [必填]

**组件树（容器/展示分离）**

```
Chat Workspace
<WorkspaceLayout>                    # 容器：导航 + 绑定状态（Router Outlet）
├─ <HomePage>                        # 容器
│  ├─ <RecentTaskList>               # 展示：最近任务
│  └─ <QuickAgentList>               # 展示：常用 Agent 入口
├─ <AgentsPage>                      # 容器
│  └─ <AgentCardList> / <AgentCard>  # 展示：Agent 目录卡片
├─ <TasksPage> / <TaskDetailPage>    # 容器
│  ├─ <TaskList>                     # 展示
│  └─ <TaskStatusTag>                # 展示（复用 StatusTag 语义）
├─ <ApprovalsPage>                   # 容器
│  └─ <ApprovalList> / <ApprovalRow> # 展示：通过/拒绝/留言
├─ <HistoryPage>                     # 容器
│  └─ <HistoryTimeline>              # 展示：对话+任务统一时间线
├─ <MemoryProfilePage>               # 容器
│  ├─ <ProfileForm>                  # 展示：Profile 编辑
│  ├─ <MemoryList> / <MemoryRow>     # 展示：纠正/删除
│  └─ <LearningSwitch>               # 展示：自动学习开关
└─ <ChatPage>                        # 容器：迁移现有对话能力（流式）

Console
<WorkflowStudioPage>                 # 容器
├─ <WorkflowNodeList>                # 展示：节点列表（增/删/排序）
├─ <NodeConfigForm>                  # 容器→SchemaForm：按节点 type 渲染 V2 判别联合字段
├─ <JsonEditorTab>                   # 展示：JSON 高级模式（现有 DSL textarea 迁移）
└─ <StudioToolbar>                   # 展示：校验/发布/版本（复用现有工作流动作）
<User360Page>                        # 容器
├─ <User360Header>                   # 展示：用户身份概要
└─ <User360Tabs>                     # 展示：Identity/Profile/Capability/Policy/Activity 五 Tab
<OperationsPage>                     # 容器
├─ <RunsTable>                       # 展示：执行记录（升级含 trace 关联）
├─ <QueuesPanel>                     # 展示：workflow 队列状态
└─ <WorkersPanel>                    # 展示：运行 Worker 状态
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | `WorkspaceLayout` | 容器 | 新建（TASK-019 shell 审计对齐） | 导航、绑定状态、主题切换、Router Outlet |
| CMP-02 | `HomePage` | 容器 | 新建 | 聚合最近任务 + 常用 Agent |
| CMP-03 | `AgentsPage` / `AgentCard` | 容器/展示 | 新建 | Agent 目录展示 + 发起任务 |
| CMP-04 | `TasksPage` / `TaskList` | 容器/展示 | 新建 | 长期任务列表/详情 |
| CMP-05 | `ApprovalsPage` / `ApprovalRow` | 容器/展示 | 新建 | HumanTask 审批（通过/拒绝/留言） |
| CMP-06 | `HistoryPage` / `HistoryTimeline` | 容器/展示 | 新建 | 统一时间线 |
| CMP-07 | `MemoryProfilePage` / `ProfileForm` / `MemoryList` / `LearningSwitch` | 容器/展示 | 新建 | Profile + Personal Memory 管理 |
| CMP-08 | `ChatPage` | 容器 | 迁移现有 ChatApp | 正式 Channel 对话 |
| CMP-09 | `WorkflowStudioPage` / `WorkflowNodeList` / `NodeConfigForm` | 容器/展示 | WorkflowsPage 升级 + SchemaForm | V2 节点编辑/校验/发布 |
| CMP-10 | `User360Page` / `User360Tabs` | 容器/展示 | UsersChannelsPage 升级 + shared `User360View` | 全维度用户视图 |
| CMP-11 | `OperationsPage` / `QueuesPanel` / `WorkersPanel` | 容器/展示 | RunsPage 升级 | 执行 + 队列 + Worker 运维 |

> **分层纪律**：容器组件负责数据与状态，展示组件纯 UI（props in / events out）；复用逻辑提取为 hook；`SchemaForm`/`SpecForm`/`PageHeader`/`ErrorBanner`/`ListPager`/`StatusTag` 复用现有实现，禁止重复造轮子。

---

### 3.4 组件接口契约 [必填]

**CMP-09 `<WorkflowStudioPage>`（关键新组件）**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `api` | `ConsoleApi` | ✓ | — | 数据层（in-memory/http 同契约） |
| `initialWorkflowId?` | `string` | — | — | 深链定位的工作流 |
| `initialAgentId?` | `string` | — | — | 从 Agent Studio 跳转的工作流目标 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onNodeChange` | `WorkflowV2Node` | 节点配置变更 |
| `onValidate` | `{ valid: boolean; diagnostics: string[] }` | 校验请求 |
| `onPublish` | `{ version: string }` | 发布确认 |

**CMP-07 `<MemoryList>`（关键新组件）**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `items` | `readonly PersonalMemoryItem[]` | ✓ | — | Memory 列表（只读展示） |
| `learningEnabled` | `boolean` | ✓ | — | 自动学习开关状态 |
| `onCorrect` | `(id: string, corrected: string) => void` | ✓ | — | 纠正提交 |
| `onDelete` | `(id: string) => void` | ✓ | — | 删除请求 |
| `onToggleLearning` | `(enabled: boolean) => void` | ✓ | — | 自动学习切换 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onCorrect` | `{ id, corrected }` | 用户提交纠正 |
| `onDelete` | `{ id }` | 用户点击删除（须二次确认） |
| `onToggleLearning` | `{ enabled }` | 开关变化 |

> 组件内**禁止直接修改 props**，改值通过事件/回调上抛。

---

### 3.5 状态与数据流 [必填]

**状态划分**

| 状态 | 作用域（local / shared store） | 形状（shape） | 读写方 |
|------|------------------------------|--------------|--------|
| 绑定/会话状态 | local（WorkspaceLayout） | `{ platformUserId?, agentId? }` | resolveAccess → Layout → 子页面 |
| 各页列表/详情 | local（各页面容器） | 各自列表/详情 | 容器 → services |
| 工作流草稿 | local（WorkflowStudioPage） | `WorkflowDraftV2` | 容器 ↔ NodeConfigForm |
| 审批操作 | local（ApprovalsPage） | `{ pending: Map<id, 'submitting'> }` | 容器 |

**数据流**

```
用户操作 → 事件处理 → Service(API 封装) → 状态更新 → Semi 组件重渲染
```

**数据获取层**：API 调用必须经 `services/` 或共享 `productClient`，组件/展示层不出现裸 `fetch`。**in-memory 与 http 双实现共享同一 TS 接口契约**（RULE-frontend-quality-001）。

**Chat Web Service 契约（`ChatApi` 扩展，in-memory 先行）**

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|-------------|-------------|----------------|
| `resolveAccess()` | 现有 | WorkspaceLayout |
| `sendMessage` / `sendMessageStream` | 现有 | ChatPage |
| `listAgents()` | `GET /workspace/agents` ⛳依赖缺口 | AgentsPage |
| `listRecentTasks()` / `listTasks()` | `GET /workspace/tasks` ⛳依赖缺口 | HomePage / TasksPage |
| `getTask(id)` | `GET /workspace/tasks/{id}` ⛳依赖缺口 | TaskDetailPage |
| `listApprovals()` | `GET /workspace/approvals` ⛳依赖缺口 | ApprovalsPage |
| `decideApproval(id, decision, comment?)` | `POST /workspace/approvals/{id}/decision` ⛳依赖缺口 | ApprovalsPage |
| `listHistory()` | `GET /workspace/history` ⛳依赖缺口 | HistoryPage |
| `getProfile()` / `updateProfile()` | Phase 2 后端（Profile 域） | MemoryProfilePage |
| `listMemory()` / `correctMemory()` / `deleteMemory()` | Phase 2 后端（Personal Memory） | MemoryProfilePage |
| `setAutoLearn(enabled)` | Phase 2 后端（learning control） | MemoryProfilePage |

> `⛳依赖缺口` = Phase 2/3 未定义的工作区侧新端点，见 §4 依赖登记；in-memory 先实现契约供页面/E2E 使用。

**Console Service 契约（`ConsoleApi` 扩展 / shared `productClient`）**

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|-------------|-------------|----------------|
| `getWorkflowSchema()` / `validateWorkflow(draft)` | 现有 validateDraft（V2 schema 校验随 Phase 3 升级） | WorkflowStudioPage |
| `listWorkflowRuns()` | Phase 3 status API（workflow_run 投影） | OperationsPage |
| `listQueues()` / `listWorkers()` | Phase 3 运营视图 ⛳依赖缺口 | OperationsPage |
| `getUser360(userId)` | 现有 `/admin/users/{id}/360`（shared `User360View`） | User360Page |

---

### 3.6 UI 状态 [必填]

> 每个涉及异步/交互的视图列出四态，禁止只处理成功路径。

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| Agents 目录 | Skeleton 卡片 | 空态「暂无可用智能体」 | ErrorBanner + 重试 | Agent 卡片列表 |
| Tasks 列表 | Skeleton 行 | 空态「暂无任务」+ 引导 | ErrorBanner + 重试 | 任务表格 |
| Approvals 审批 | Skeleton 行 | 空态「没有待确认事项」 | ErrorBanner + 重试（列表保持） | 待确认行 + 操作反馈 |
| History 时间线 | Skeleton 行 | 空态「暂无历史记录」 | ErrorBanner + 重试 | 时间线 |
| Memory & Profile | Skeleton 表单 | 空态「暂无记忆」 | 字段级错误提示 | Profile 表单 + Memory 列表 + 开关 |
| Workflow Studio 节点 | Skeleton 节点列表 | 空态「暂无节点，点击添加」 | 校验诊断定位字段 | 节点表单 + 版本列表 |
| User 360 详情 | Skeleton 面板 | 空态「该用户暂无数据」 | ErrorBanner + 重试 | 五维度 Tab |
| Operations 队列/Worker | Skeleton 面板 | 空态「无运行中队列/Worker」 | ErrorBanner + 重试 | 状态表格 |

---

### 3.7 样式方案 [必填]

| 维度 | 约定 |
|------|------|
| **样式与逻辑分离** | 样式走现有 CSS（`styles.css` 类名约定）/ Semi Design Token，不与数据获取/业务逻辑混在同一处；容器组件不写页面样式 |
| **设计 tokens** | 间距/颜色/字号引用 Semi Token（现有 `theme.ts` 主题切换延续），不散落魔法值 |
| **响应式断点** | 桌面优先；Sider 折叠为抽屉（<768px）；表格容器横向滚动；不引入第二套布局框架 |

---

### 3.8 可访问性与兼容性 [按需]

| 维度 | 要求 |
|------|------|
| 可访问性 | 语义标签 + `aria-label`（Semi 组件默认支持）；键盘可达（审批通过/拒绝、Memory 删除等操作均可用键盘完成）；焦点管理（Modal/SideSheet 打开时焦点落入、关闭后归还） |
| 浏览器/设备兼容 | 现代 Chromium/Firefox/Safari；桌面优先，平板/手机响应式可用（NFR-A11Y-01） |

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| RISK-P4-01 | Phase 2/3 后端未实现，页面无法接真数据 | 页面/旅程无法真实验收 | in-memory service 同契约先行，真 HTTP 后端就绪后切换；契约冻结在 TS 接口 | S-02~S-08, S-12, S-13 |
| RISK-P4-02 | 工作区侧新端点依赖缺口（`/workspace/*`） | 后端无实现时页面缺数据源 | §3.5 显式登记 `⛳依赖缺口`，纳入 Phase 2/3 实施任务依赖；本阶段 in-memory 实现 | S-02~S-06 |
| RISK-P4-03 | state 导航 → React Router 迁移回归 | 现有 Console 页面/测试受影响 | 迁移保持 `ConsoleView` 映射不变，现有 E2E 全量回归；路由表单点变更 | S-09, S-12, S-13 |
| RISK-P4-04 | Workflow V2 判别联合表单复杂度 | 节点配置表单字段多、易错 | SchemaForm 扩展 kind（复用既有 SchemaForm 全 kind 能力），逐节点类型校验 + JSON 高级模式兜底 | S-10, S-11, E-02 |
| RISK-P4-05 | 术语隐藏 denylist 误伤 | 误隐藏影响可用性或漏暴露 | denylist 固定清单 + 双端（chat/console）统一断言；只覆盖普通用户核心页，Admin/Builder 视图不受限 | B-02 |
| RISK-P4-06 | HumanTask 审批语义依赖 Phase 3 | 审批交互与后端语义不一致 | 审批视图契约按 Phase 3 HumanTask（recv_async/send）设计；in-memory 模拟状态机 | S-05, E-03 |

---

## Spec Compliance Matrix

> 继承自需求目录 `spec-context.yml`（8 条 required Rule），每条已有具体设计落点与 verifier。

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| `fluxion-console-channel#RULE-fluxion-console-001` | required | Web Chat 是正式 Channel；未绑定用户仅 `/bind` | §2.2 FEAT-P4-08 + §3.2 chat 路由 + §3.6 四态 | B-01（未绑定仅 bind）、S-01、S-08 | applied |
| `fluxion-console-api-contract#RULE-fluxion-console-api-001` | required | 所有 JSON API 统一 `{code,message,data,request_id}`；前端经 services 消费 envelope | §3.5 数据获取层（in-memory/http 同契约 + 现有 httpClient envelope 解包） | S-10（校验/发布成功）、E-01~E-04（错误提示） | applied |
| `fluxion-dfx#RULE-fluxion-dfx-001` | required | DFX 在编码阶段落实：可用性/可靠性/可观测性/可维护性 | §3.6 四态（loading/empty/error/success）+ NFR-PERF-01 + §3.8 可访问性 | E-01~E-04、B-03（Journey 成功率） | applied |
| `fluxion-runtime-core#RULE-fluxion-runtime-001` | required | Runtime 必须无状态；普通用户面不暴露 Runtime 内部（术语隐藏） | §2.2 FEAT-P4-03（Agent 产品模型，非 RuntimeProfile）+ FEAT-P4-13（denylist） | B-02（术语暴露=0）、S-03 | applied |
| `frontend-semi-design#RULE-frontend-semi-001` | required | Semi Design 唯一通用组件体系；React 19 adapter 入口 | §3.1 技术选型 + §3.3 组件（全 Semi） | S-01（导航渲染）、S-07~S-13 全页面 | applied |
| `frontend-quality-standards#RULE-frontend-quality-001` | required | 质量 Guidance：组件不裸 fetch、目录/命名规范、测试覆盖 | §3.5 数据获取层 + §3.3 分层纪律 + §2.4 场景（E2E/integration 分层） | B-03（Journey 成功率 ≥95%）、S-02~S-13 | applied |
| `frontend-directory-structure#RULE-frontend-directory-001` | required | 新页面 `src/pages/`，通用组件 `src/components/` 或 shared | §3.2 页面与路由结构 + §3.3 组件表 | S-01、S-09（路由可达） | applied |
| `frontend-component-specs#RULE-frontend-component-001` | required | 容器/展示分离；props 不可改，事件上抛 | §3.3 组件树 + §3.4 组件接口契约 | S-05（审批操作上抛）、S-07（Memory 回调上抛） | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| US / FEAT / NFR | 用户故事 / 功能项 / 非功能需求 |
| CMP | Component，组件 |
| 容器组件 | 负责数据获取与状态的组件 |
| 展示组件 | 纯 UI、props 驱动、事件上抛的组件 |
| 术语隐藏 denylist | 普通用户核心页禁止出现的底层术语清单（RuntimeProfile/Registry/Resource/Binding/Plugin/Workflow 底层态） |
| Journey | 面向一类 Persona 的完整用户旅程（Workspace/Build/Admin），用 E2E 套件测量成功率 |
| 依赖缺口 | Phase 2/3 未定义、由 Phase 4 页面消费的工作区侧后端端点，登记为实施依赖 |

---

*文档结束*
