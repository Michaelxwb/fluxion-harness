# Chat 瘦身前端模块需求与设计简报

> **文档编号**: FE-CHAT-SLIM-v0.1
> **文档版本**: v0.1
> **创建日期**: 2026-09-07
> **文档状态**: 草稿

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3 章（前端技术设计）→ 通过后锁定设计基线

**ID 体系**: FEAT（功能）、CMP（组件）、NFR（非功能指标）
场景编号：S-（正常）、E-（异常）、B-（边界）

---

## 目录

- [1. 文档控制](#1-文档控制)
- [2. 需求分析](#2-需求分析)
  - [2.1 需求概述](#21-需求概述)
  - [2.2 功能方案](#22-功能方案)
  - [2.3 范围与边界](#23-范围与边界)
  - [2.4 验收条件](#24-验收条件)
- [3. 前端技术设计](#3-前端技术设计)
  - [3.1 技术选型](#31-技术选型)
  - [3.2 页面与路由结构](#32-页面与路由结构)
  - [3.3 组件设计](#33-组件设计)
  - [3.4 组件接口契约](#34-组件接口契约)
  - [3.5 状态与数据流](#35-状态与数据流)
  - [3.6 UI 状态](#36-ui-状态)
  - [3.7 样式方案](#37-样式方案)
- [4. 风险与依赖](#4-风险与依赖)
- [Spec Compliance Matrix](#spec-compliance-matrix)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | 待定 | 技术方案、代码实现 |
| 设计/交互 | 待定 | 视觉与交互稿（如有） |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-09-07 | cf-task-align | 初始草稿 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | chat-slimdown |
| **需求类型** | 重构（前端瘦身） |
| **业务背景** | chat 应用现有 10 页面 + 12 组件（任务/审批/记忆/设置等），超出"用户对话"核心诉求，维护重、易白屏 |
| **核心目标** | 只留一个对话框：打开 console 下发的对话链接即聊；Agent 顶部下拉选；历史只读抽屉 |

---

### 2.2 功能方案

#### 2.2.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-01 | 对话框主体 | ChatPage 保留并改造：顶部只读显示当前 Agent（access 绑定单 Agent，不做切换） + 消息流 + 输入框；打开即聊 | P0 | 需求描述 |
| FEAT-02 | token 接入 | token 来自 console 下发的对话链接；无效/过期显示"链接失效"提示页；删 BindGate 界面；token 持久化 localStorage（刷新免重绑，401/403 清缓存） | P0 | 需求描述（刷新回绑定页反馈） |
| FEAT-03 | 历史抽屉 | 对话框内只读抽屉：会话列表 + 消息回看，不可编辑 | P1 | 需求描述 |
| FEAT-04 | 删除多余页面组件 | 删 9 页面 + 10 组件 + WorkspaceLayout 导航 + 路由收敛到单页 | P0 | 需求描述 |
| FEAT-05 | 测试同步 | `__tests__` 中引用被删页面的用例同步删/改；vitest 全过 | P1 | 需求描述 |
| FEAT-06 | 对话美化 | Markdown 渲染 + 删 kind Tag + 气泡排版 + 流式光标（见 §3.3/§3.7） | P1 | 需求描述（截图反馈） |
| FEAT-07 | 对话视觉重设计 | 去气泡扁平风 + 空态引导（见 §3.7.2，用户确认） | P1 | 需求描述（截图二轮反馈） |

### 2.2.2 组件清单

| 组件ID | 组件名 | 类型 | 说明 |
|--------|-------|------|------|
| CMP-01 | ChatPage（改造） | 容器 | 消息流 + 输入框 + Agent 下拉入口 + 历史抽屉入口 |
| CMP-02 | AgentBadge（改造/复用） | 展示 | 顶部只读显示当前 Agent 名（既有 agentDisplayName 逻辑；不做下拉，access 绑定单 Agent） |
| CMP-03 | HistoryDrawer（新增，Semi SideSheet） | 容器 | 只读会话列表 + 消息回看 |
| CMP-04 | InvalidLink（新增） | 展示 | token 无效/过期提示页 |
| CMP-05 | ErrorBanner（保留） | 展示 | 错误提示不变 |
| CMP-06 | MarkdownMessage（新增） | 展示 | 包 react-markdown + remark-gfm，只做渲染与样式映射 |

---

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | FEAT-01~FEAT-05；路由收敛 `/` 即对话框；tsc+lint+vitest 全过 |
| **非范围（Out of Scope）** | 后端改动（channel/workspace 接口不动）；多模态；`/bind` 服务端逻辑（仅删 chat 侧界面）；Console 侧改动 |
| **有意妥协 / 技术债** | 无 |

---

### 2.4 验收条件

#### 2.4.1 功能验收场景

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期结果 |
|--------|--------|--------|---------|-------------|---------|---------|
| S-01 | FEAT-01/02 | P0 | E2E | Chromium + 真实后端 channel SSE | 打开带有效 token 的对话链接 | 直接可聊，首轮有回复 |
| S-02 | FEAT-01 | P0 | component | vitest | 打开对话 | 顶部显示当前 Agent 名（只读，无切换） |
| S-03 | FEAT-02 | P0 | component | vitest | 用无效 token 打开 | 显示链接失效提示页，无白屏 |
| S-04 | FEAT-03 | P1 | component | vitest + 只读会话接口 | 打开历史抽屉 | 会话列表与消息可见，不可编辑 |
| S-05 | FEAT-06 | P1 | component | vitest + MarkdownMessage | 渲染含标题/粗体/列表/代码块的回复 | 无原文符号，层级正确 |
| S-06 | FEAT-06 | P1 | component | vitest | 普通对话展示 | 无 kind Tag，气泡宽度/间距正常 |
| S-07 | FEAT-07 | P1 | component | vitest | 助手消息展示 | 无气泡底、有头像+名字行 |
| S-08 | FEAT-07 | P1 | component | vitest | 空对话 | 问候+快捷 chips，点击发送 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | 系统行为 |
|--------|--------|---------|-------------|---------|---------|
| E-01 | FEAT-04 | integration | tsc + lint + 构建 | 删除后全量检查 | 零残留引用，构建通过，无死路由 |
| E-02 | FEAT-06 | component | vitest | 回复含恶意 HTML/脚本 | 转义不执行 |
| B-01 | FEAT-02 | component | vitest | 对话中 token 过期 | 错误提示，不崩溃，不断连复用旧状态 |
| B-02 | FEAT-02 | component | vitest + localStorage | 刷新页面（hash 只剩路由） | 凭缓存恢复会话，不回绑定页 |

#### 2.4.2 非功能指标

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-01 | 首屏可交互 | 待定 | 真机实测（本地无浏览器，CI 补） |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React 19 + TypeScript + Vite | 现状 | 沿用，不动 |
| UI | Semi Design（唯一） | 2.102.x | 规范要求；下拉/抽屉用 Semi Select/SideSheet，不自研 |
| 路由 | react-router（保留最小） | 现状 | 仅 `/` 单路由，可后续直渲染；本次保留 router 壳，删多余 Route |
| 数据 | 现有 services 层 | 现状 | Agent 列表/会话/消息接口复用，不新增 API |

---

### 3.2 页面与路由结构

```
/ → ChatPage（含 AgentSelect + HistoryDrawer + InvalidLink 分支）
```

- 删 9 页面：Home/Agents/AgentDetail/Approvals/Tasks/TaskDetail/History/Memory/Settings
- `App.tsx` 只留 `/` 路由（去 `/home` 重定向与 `*` 兜底保留指向 `/`）
- `WorkspaceLayout` 导航拆除：只剩 children 透传壳（后续可删，本次保留空壳降风险）

---

### 3.3 组件设计

```text
ChatPage（容器）
├── AgentBadge（展示：当前 Agent 名只读）
├── MessageList（既有消息流，不动）
│   └── MarkdownMessage（新增：包 react-markdown + remark-gfm，只做渲染与样式映射）
├── ChatInput（既有输入框，不动）
├── HistoryDrawer（容器：SessionList 只读 + MessageView 只读）
├── InvalidLink（展示：token 无效分支）
└── ErrorBanner（既有，不动）
```

- 删除 10 组件：AgentCardList、TaskList、TaskStatusTag、RecentTaskList、ApprovalList、MemoryList、ProfileForm、HistoryTimeline、QuickAgentList（BindGate 界面删除，逻辑不用）
- 新增只 3 个小组件（CMP-02/03/04），容器/展示分离按 PATTERN-frontend-001

---

### 3.4 组件接口契约

| 组件 | Props | Events |
|------|-------|--------|
| AgentBadge | `agentName: string` | 无（纯展示） |
| HistoryDrawer | `open: boolean`、`sessions: SessionSummary[]` | `onClose()`、`onSelect(sessionId)`（只读回看，不可编辑） |
| InvalidLink | 无 | 无（纯提示 + 联系管理员文案） |
| MarkdownMessage | `content: string` | 无（纯渲染） |
| ChatPage | `api`（既有注入） | 内部组装，不新增对外事件 |

---

### 3.5 状态与数据流

- token：沿用现有机制（链接参数/存储，不动）；缺失/无效 → InvalidLink 分支
- Agent 列表：现有 services 接口，ChatPage 拉取后传 AgentSelect；切换只改当前会话 agent_id
- 历史：现有会话/消息只读接口，HistoryDrawer 内拉取；不写、不删
- 消息发送：既有 channel SSE 链路不动

---

### 3.6 UI 状态

| 状态 | 处理 |
|------|------|
| loading | Agent 列表/历史拉取中骨架屏（Semi Skeleton） |
| empty | 无历史会话显示空态；无 Agent 显示不可用提示 |
| error | ErrorBanner（既有）；token 无效走 InvalidLink 整页 |
| success | 正常对话流 |

---

### 3.7 样式方案

- 沿用现有 Semi token 与主题，不新增样式体系；删除页面附带的样式文件一并删
- 对话框移动端可用性保持（既有响应式不动）

### 3.7.1 对话美化（FEAT-06）
- Markdown 渲染：新增 `react-markdown` + `remark-gfm` 依赖（渲染器，非组件库，不违反 Semi 唯一约束）；标题/粗体/列表/代码块/表格映射 Semi Typography；XSS 默认转义不执行脚本
- 删 `message-kind` Tag（debug 残留 `App.tsx`）
- 气泡排版：assistant 气泡 max-width 收敛、行高/间距按 4 倍数、用户/助手左右区分、流式光标（`▍` 跟随未完成消息）
- 头部收敛：单标题 + Agent 下拉 + 主题切换，双 badge 去重（配合瘦身后单页）

### 3.7.2 对话视觉重设计（FEAT-07）

- 助手消息去气泡：透明底纯文本 + 左侧头像（Semi Avatar）+ 名字行；用户保留右侧蓝色气泡
- 空态引导：问候语 + 3 个快捷提问 chips（点即发送），替代空白
- 输入框圆角胶囊化，发送按钮内嵌圆形
- 内容列 max-width 收敛到适合长文阅读的宽度（880px），居中
- 首 token 未到时显示打字机三圆点（替代光秃 ▍+底部转圈，转圈已删）；
  输入框默认 2 行（max 6 行）

---

## 4. 风险与依赖

### 4.1 项目依赖

| 依赖模块 | 依赖内容 | 风险等级 |
|---------|---------|---------|
| 后端 channel SSE | 对话链路 | 低（不动） |
| 现有 services 层 | Agent/会话/消息接口 | 低（复用） |

### 4.2 风险识别

| 风险ID | 描述 | 影响 | 应对措施 | 验证场景 |
|--------|------|------|---------|---------|
| RISK-01 | 删页面漏改引用致白屏/构建失败 | 打包失败 | E-01 全量检查 + tsc/vitest 门禁 | E-01 |
| RISK-02 | 本地无浏览器，S-01 只能 CI/真机验 | 验收延迟 | 组件测试先行，E2E 走 playwright（CI/有环境） | S-01 |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| `frontend-component-specs`#RULE-frontend-component-001 | required | 容器/展示分离，新组件守规 | §3.3 / CMP-02~04 | S-02 + S-04 + verifier | applied |
| `frontend-directory-structure`#RULE-frontend-directory-001 | required | 页面删除合规，路由收敛 | §3.2 | E-01 + verifier | applied |
| `frontend-quality-standards`#RULE-frontend-quality-001 | required | 类型/lint/测试全过 | §3.6 / FEAT-05 | E-01 + E-02 + S-02 ~ S-08 | applied |
| `frontend-semi-design`#RULE-frontend-semi-001 | required | 下拉/抽屉用 Semi，不自研 | §3.1 / §3.3 | S-02 + S-04 | applied |
| `fluxion-console-channel`#RULE-fluxion-console-001 | required | chat 只调 channel 接口，不直连 runtime | §3.5 | S-01 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| BindGate | 未绑定用户的前置绑定界面（本次删除界面，服务端逻辑保留） |
| token | console 下发的对话链接凭证（chat-access） |

---

*文档结束*
