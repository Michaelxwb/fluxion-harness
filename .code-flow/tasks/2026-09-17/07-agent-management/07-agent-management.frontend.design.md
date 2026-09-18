# Agent 定义、授权与通道配置 前端模块需求与设计简报

> **文档编号**: FE-AGENT-V1.0  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | Agent 定义、授权与通道配置 |
| 前端目录 | `apps/console-platform/frontend/src/modules/agent-management/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `智能服务交付平台-V1.4-交互稿.html` |

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 产品经理 | 待指定 | 需求定义、业务验收 |
| 开发负责人 | muad-console-platform | 技术方案、代码实现 |
| 测试负责人 | 待指定 | 测试策略、质量保证 |
| 架构师 | 待指定 | 架构审核、技术决策 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-09-17 | muad-console-platform | 初始设计（对齐历史版本基线） |
| v1.1 | 2026-09-18 | muad-console-platform | 对齐 V1.4 决策（docs/17）：交互基线升 V1.4；基本信息补“最近运行”只读区块；列表操作列补软删除 Agent；明确无绑定级启停 UI；场景与 Spec Matrix 补全。 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Agent 定义、授权与通道配置 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 严格保留最新 Agent 交互：无全局保存；基本信息 revision 化并含最近运行；关系操作独立立即生效，无绑定级启停。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | Agent 列表/新增/删除 | 名称/标识/模型/资源数/通道数/授权数/状态/revision；操作列支持编辑与软删除。 | P0 | 需求描述 |
| FEAT-FE-02 | 基本信息 | 双列信息 + 系统 Prompt/模型 + 编辑基本信息 + 最近运行只读区块。 | P0 | 需求描述 |
| FEAT-FE-03 | 关系 Tabs | Skill/MCP/用户授权/IM 接入独立操作立即生效；不提供绑定级启停。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 列表、创建、软删除、详情5 Tabs、基本信息编辑与最近运行、Skill/MCP/User/Channel 关系。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加绑定级启停开关或授权到期时间；不增加交互稿未确认的重型能力 |
| 技术债 | 无 |

### 2.4 验收条件

正常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-02 | E2E | Browser→update API→Runtime resolve | 编辑系统 Prompt 保存 | 详情 revision+1，提示新请求生效；无全局保存 |
| S-FE-02 | FEAT-FE-03 | E2E | Browser→binding API→UI | Skill Tab 绑定 Skill | 当前 Tab 局部刷新立即出现；无保存按钮与启停开关 |
| S-FE-03 | FEAT-FE-02 | E2E | Browser→审计 API→UI | 打开基本信息 Tab | “最近运行”只读列表展示时间/用户/类型/目标/结果；无数据时显示空态 |
| S-FE-04 | FEAT-FE-01 | E2E | Browser→DELETE Agent API→列表 | 操作列删除 Agent 并 Popconfirm 确认 | 软删除成功，列表移除该行，详情关闭 |
| S-FE-05 | FEAT-FE-03 | E2E | Browser→unbind API→UI | MCP Tab 解除绑定后再次绑定 | 行立即移除；再次绑定恢复，全程无启停开关 |
| S-FE-06 | FEAT-FE-03 | E2E | Browser→channel API→IM Tab | IM 接入 Tab 新增第二个 bot | 两行均展示同一 Agent，无 Pod/replica 信息 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-02 | E2E | revision conflict→Modal | 保存旧 revision | Modal 保留并提示刷新重试 |
| E-FE-02 | FEAT-FE-03 | E2E | bot conflict→UI | 新增被占用 bot_id | 表单保留并本地化提示“bot_id 已被占用” |
| E-FE-03 | FEAT-FE-01 | E2E | key conflict→Modal | 新增 Agent 标识重复 | Modal 保留并本地化提示 |
| E-FE-04 | FEAT-FE-03 | E2E | 目标资源不存在→Toast | 绑定已被删除的 Skill/MCP | Toast 提示资源不存在，当前 Tab 状态不变 |

## 3. 前端技术设计

### 3.1 技术选型

- React 18.3.x + TypeScript + Vite；
- `@douyinfe/semi-ui` 2.84.x；
- react-router-dom 6.x；
- axios 统一 ApiClient；
- react-i18next，locale 仅 `zh-CN/en-US`；
- 不新增 Redux/Zustand；页面状态由 route/page local state + module hooks 管理；
- 列表统一 `Table` + controlled pagination；详情统一 `SideSheet` + `Tabs`；
- 新增/编辑统一 `Modal` + `Form`；危险操作统一 `Popconfirm`；
- loading/empty/error 使用 `Spin/Skeleton`、`Empty`、公共 ErrorState；
- 所有文案 `t(key)`；ApiClient 自动发送 `X-Locale`。

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| Agent 管理 | `/agents` | ConsoleShell | 列表 + 详情 SideSheet |

### 3.3 组件设计

```text
<Page>
├─ <ModuleToolbar/>
├─ <RemoteTable/>
└─ <DetailSideSheet/>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `AgentPage` | 容器 | 模块内 | 列表/搜索/分页/详情/软删除 |
| CMP-02 | `AgentDetailTabs` | 展示 | 模块内 | 基本信息（含最近运行）/Skill/MCP/用户授权/IM接入 |
| CMP-03 | `AgentBasicFormModal` | 容器 | 模块内 | 模型选择+系统Prompt+revision |
| CMP-04 | `RelationPickerModal` | 容器 | Skill/MCP/User | 单关系 add |
| CMP-05 | `ChannelFormModal` | 容器 | 模块内 | bot_id/secret/通道对象 enabled 配置（非绑定开关） |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

**基本信息 - 最近运行**：只读区块，取该 Agent 最近 6 条运行审计（`GET /api/v1/audits?resource_id={agent_id}&page=1&page_size=6`，见 docs/07 §10.11）；列顺序为 时间/用户/类型/目标/结果；无数据时空态显示“暂无运行记录”；不提供编辑/删除操作。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 新增 Agent | `Button` | primary | 打开创建 Modal | `POST /api/v1/agents` | 否 |
| 列表右上 | 搜索 | `Button` | secondary | 名称/标识/模型/bot_id 搜索 | `GET /api/v1/agents` | 否 |
| 列表右上 | 重置 | `Button` | secondary | 清空筛选 | `GET /api/v1/agents` | 否 |
| 列表右上 | 刷新 | `Button` | secondary | 刷新当前页 | `GET /api/v1/agents` | 否 |
| 操作列 | 复制 ID | `Button` | secondary | 复制 agent_id | `-` | 否 |
| 操作列 | 删除 | `Popconfirm + Button` | danger | 软删除 Agent | `DELETE /api/v1/agents/{id}` | 是 |
| 基本信息 | 编辑基本信息 | `Button` | secondary | 打开基本信息 Modal | `PUT /api/v1/agents/{id}` | 否 |
| Skill Tab | 绑定 Skill | `Button` | primary | 创建 Binding | `POST /api/v1/agents/{id}/skills/{skill_id}` | 否 |
| Skill 行 | 解除 | `Popconfirm + Button` | secondary | 删除 Binding（软删除） | `DELETE /api/v1/agents/{id}/skills/{skill_id}` | 是 |
| MCP Tab | 绑定 MCP | `Button` | primary | 创建 Binding | `POST /api/v1/agents/{id}/mcp-servers/{mcp_id}` | 否 |
| MCP 行 | 解除 | `Popconfirm + Button` | secondary | 删除 Binding（软删除） | `DELETE /api/v1/agents/{id}/mcp-servers/{mcp_id}` | 是 |
| 用户授权 Tab | 授权用户 | `Button` | primary | 创建 AgentAccessGrant | `POST /api/v1/agents/{id}/users/{user_id}` | 否 |
| 用户行 | 取消授权 | `Popconfirm + Button` | secondary | 删除 Grant（软删除） | `DELETE /api/v1/agents/{id}/users/{user_id}` | 是 |
| IM 接入 Tab | 新增 IM 通道 | `Button` | primary | 打开通道 Modal | `POST /api/v1/agents/{id}/channels` | 否 |
| IM 行 | 编辑 | `Button` | secondary | 编辑通道（含通道对象自身 enabled） | `PUT /api/v1/agents/{id}/channels/{channel_id}` | 否 |
| IM 行 | 移除 | `Popconfirm + Button` | danger | 移除通道 | `DELETE /api/v1/agents/{id}/channels/{channel_id}` | 是 |

统一规则：主创建/保存使用 `Button theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同一 Header 行靠右；Tab 内关系操作完成即生效，不需要“保存整个对象”。Skill/MCP/用户授权 Tab 不提供绑定级启停开关；`bot_account.enabled` 属于通道对象自身属性，不是绑定开关。

### 3.4 组件接口契约

```ts
export interface DetailSideSheetProps {
  visible: boolean;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  activeTab?: string;
  onCancel(): void;
}
```

展示组件 props-in/events-out；API、路由、提交状态由 Page/Hook 管理。

### 3.5 状态与数据流

```text
User Action
 -> Page/Hook
 -> modules/agent-management/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listAgents(params)` | `GET /api/v1/agents` | useAgentList |
| `deleteAgent(id)` | `DELETE /api/v1/agents/{id}` | AgentPage 操作列 |
| `updateAgent(id,input)` | `PUT /api/v1/agents/{id}` | AgentBasicFormModal |
| `bindSkill(agentId,skillId)` | `POST /api/v1/agents/{id}/skills/{skill_id}` | SkillTab |
| `saveChannel(agentId,input)` | `POST/PUT /api/v1/agents/{id}/channels` | ChannelFormModal |
| `listAudits(params)` | `GET /api/v1/audits` | BasicTab 最近运行 |

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Table loading/Skeleton | Empty + 创建/清筛选 | ErrorState + 重试 | Table + 右下 Pagination |
| 详情 | SideSheet Spin | Tab Empty | Banner/ErrorState | Descriptions/List/Table |
| 最近运行 | 区块 Skeleton | “暂无运行记录” | 区块内 ErrorState + 重试 | 只读列表，无操作列 |
| 表单 | 保存按钮 loading | - | Form 字段错误 + Toast | 关闭 Modal + 局部刷新 |
| 关系操作 | 当前按钮 loading | - | Toast，保持当前 Tab | 局部刷新 |

### 3.7 样式方案

- 保留交互稿信息架构和字段顺序，可用 Semi Token 重新美化；
- 列表页不增加重复标题/说明块；
- Toolbar 左主操作、右搜索筛选；
- 主展示字段点击打开详情；操作列只放真实动作；
- 详情双列基础信息，<900px 降单列；最近运行列顺序为 时间/用户/类型/目标/结果；
- 时间统一 `YYYY-MM-DD HH:mm:ss`；
- 禁止散落魔法颜色/间距。

### 3.8 可访问性与兼容性

Semi Form required/rules；Modal/SideSheet 焦点管理；图标按钮 aria-label；Chrome/Edge 企业当前版本为主，Safari 做开发兼容验证。

## 4. 风险与依赖

- 前置：02-user-identity, 03-model-management, 05-skill-management, 06-mcp-management；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1、误加绑定级启停控件；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段、Spec Matrix 逐条核对。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.3/§3.5 | S-FE-01, E-FE-02, E-FE-03 | applied |
| `harness-platform#RULE-ui-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.3/§3.5 | S-FE-01, S-FE-04 | applied |
| `harness-platform#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3/§3.5 | S-FE-01, S-FE-03 | applied |
| `harness-platform#RULE-rel-001` | required | 关系修改使用单关系 POST/DELETE 独立事务，不用全量 PUT。 | §3.3/§3.5 | S-FE-02, S-FE-05 | applied |
| `harness-platform#RULE-auth-001` | required | User→Agent；Agent→Skill/MCP；SELECTED 再叠加资源用户 Grant；不建三元授权；绑定无启停开关。 | §3.3（公式见后端 §3.3.1） | S-FE-02, E-FE-02, E-FE-04 | applied |
| `harness-platform#RULE-model-001` | required | ModelDefinition 无 is_default；Agent 显式选择 enabled model_id。 | §3.3/§3.5 | S-FE-01 | applied |
| `harness-platform#RULE-snapshot-001` | required | 新 Run/Task 冻结 Snapshot；配置/授权变更只影响后续新 Run/Task。 | §3.3/§3.5 | S-FE-01, S-FE-05 | applied |
| `harness-platform#RULE-im-001` | required | Agent 0..N bot_id；bot_id 只指向一个 Agent；不绑定 Runtime Pod。 | §3.3/§3.5 | S-FE-06, E-FE-02 | applied |
| `harness-platform#RULE-front-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.3/§3.5 | S-FE-01, S-FE-03, S-FE-04 | applied |
| `harness-platform#RULE-test-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §2.4 | S-FE-01~S-FE-06, E-FE-01~E-FE-04 | applied |
