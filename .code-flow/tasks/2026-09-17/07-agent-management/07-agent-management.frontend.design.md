# Agent 定义、授权与通道配置 前端模块需求与设计简报

> **文档编号**: FE-AGENT-V1.0  
> **文档版本**: v1.0  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | Agent 定义、授权与通道配置 |
| 前端目录 | `apps/console-platform/frontend/src/modules/agent-management/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `MSS智能服务交付平台-V1.3-交互稿.html` |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Agent 定义、授权与通道配置 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 严格保留最新 Agent 交互：无全局保存；基本信息 revision 化；关系操作独立立即生效。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | Agent 列表/新增 | 名称/标识/模型/资源数/通道数/授权数/状态/revision。 | P0 | 需求描述 |
| FEAT-FE-02 | 基本信息 | 双列信息 + 系统 Prompt/模型 + 编辑基本信息。 | P0 | 需求描述 |
| FEAT-FE-03 | 关系 Tabs | Skill/MCP/用户授权/IM 接入独立操作立即生效。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 列表、创建、详情5 Tabs、基本信息编辑、Skill/MCP/User/Channel 关系。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力 |
| 技术债 | 无 |

### 2.4 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-02 | E2E | Browser→update API→Runtime resolve | 编辑系统 Prompt 保存 | 详情 revision+1，提示新请求生效；无全局保存 |
| S-FE-02 | FEAT-FE-03 | E2E | Browser→binding API→UI | Skill Tab 绑定 Skill | 当前 Tab 局部刷新立即出现 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-02 | E2E | revision conflict→Modal | 保存旧 revision | Modal 保留并提示刷新重试 |
| E-FE-02 | FEAT-FE-03 | E2E | bot conflict→UI | 新增被占用 bot_id | 表单保留并本地化提示 |

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

```text\n<Page>\n├─ <ModuleToolbar/>\n├─ <RemoteTable/>\n└─ <DetailSideSheet/>\n```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `AgentPage` | 容器 | 模块内 | 列表/搜索/分页/详情 |
| CMP-02 | `AgentDetailTabs` | 展示 | 模块内 | 基本信息/Skill/MCP/用户授权/IM接入 |
| CMP-03 | `AgentBasicFormModal` | 容器 | 模块内 | 模型选择+系统Prompt+revision |
| CMP-04 | `RelationPickerModal` | 容器 | Skill/MCP/User | 单关系 add |
| CMP-05 | `ChannelFormModal` | 容器 | 模块内 | bot_id/secret 配置 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 新增 Agent | `Button` | primary | 打开创建 Modal | `POST /api/v1/agents` | 否 |
| 列表右上 | 搜索 | `Button` | secondary | 名称/标识/模型/bot_id 搜索 | `GET /api/v1/agents` | 否 |
| 列表右上 | 重置 | `Button` | secondary | 清空筛选 | `GET /api/v1/agents` | 否 |
| 列表右上 | 刷新 | `Button` | secondary | 刷新当前页 | `GET /api/v1/agents` | 否 |
| 操作列 | 复制 ID | `Button` | secondary | 复制 agent_id | `-` | 否 |
| 基本信息 | 编辑基本信息 | `Button` | secondary | 打开基本信息 Modal | `PUT /api/v1/agents/{id}` | 否 |
| Skill Tab | 绑定 Skill | `Button` | primary | 创建 Binding | `POST /api/v1/agents/{id}/skills/{skill_id}` | 否 |
| Skill 行 | 解除 | `Popconfirm + Button` | secondary | 删除 Binding | `DELETE /api/v1/agents/{id}/skills/{skill_id}` | 是 |
| MCP Tab | 绑定 MCP | `Button` | primary | 创建 Binding | `POST /api/v1/agents/{id}/mcp-servers/{mcp_id}` | 否 |
| MCP 行 | 解除 | `Popconfirm + Button` | secondary | 删除 Binding | `DELETE /api/v1/agents/{id}/mcp-servers/{mcp_id}` | 是 |
| 用户授权 Tab | 授权用户 | `Button` | primary | 创建 AgentAccessGrant | `POST /api/v1/agents/{id}/users/{user_id}` | 否 |
| 用户行 | 取消授权 | `Popconfirm + Button` | secondary | 删除 Grant | `DELETE /api/v1/agents/{id}/users/{user_id}` | 是 |
| IM 接入 Tab | 新增 IM 通道 | `Button` | primary | 打开通道 Modal | `POST /api/v1/agents/{id}/channels` | 否 |
| IM 行 | 编辑 | `Button` | secondary | 编辑通道 | `PUT /api/v1/agents/{id}/channels/{channel_id}` | 否 |
| IM 行 | 移除 | `Popconfirm + Button` | danger | 移除通道 | `DELETE /api/v1/agents/{id}/channels/{channel_id}` | 是 |

统一规则：主创建/保存使用 `Button theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同一 Header 行靠右；Tab 内关系操作完成即生效，不需要“保存整个对象”。

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
| `updateAgent(id,input)` | `PUT /api/v1/agents/{id}` | AgentBasicFormModal |
| `bindSkill(agentId,skillId)` | `POST /api/v1/agents/{id}/skills/{skill_id}` | SkillTab |
| `saveChannel(agentId,input)` | `POST/PUT /api/v1/agents/{id}/channels` | ChannelFormModal |

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Table loading/Skeleton | Empty + 创建/清筛选 | ErrorState + 重试 | Table + 右下 Pagination |
| 详情 | SideSheet Spin | Tab Empty | Banner/ErrorState | Descriptions/List/Table |
| 表单 | 保存按钮 loading | - | Form 字段错误 + Toast | 关闭 Modal + 局部刷新 |
| 关系操作 | 当前按钮 loading | - | Toast，保持当前 Tab | 局部刷新 |

### 3.7 样式方案

- 保留交互稿信息架构和字段顺序，可用 Semi Token 重新美化；
- 列表页不增加重复标题/说明块；
- Toolbar 左主操作、右搜索筛选；
- 主展示字段点击打开详情；操作列只放真实动作；
- 详情双列基础信息，<900px 降单列；
- 时间统一 `YYYY-MM-DD HH:mm:ss`；
- 禁止散落魔法颜色/间距。

### 3.8 可访问性与兼容性

Semi Form required/rules；Modal/SideSheet 焦点管理；图标按钮 aria-label；Chrome/Edge 企业当前版本为主，Safari 做开发兼容验证。

## 4. 风险与依赖

- 前置：02-user-identity, 03-model-management, 05-skill-management, 06-mcp-management；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `mss-platform#RULE-I18N-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-UI-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-UI-DETAIL-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-REL-001` | required | 关系修改使用单关系 POST/DELETE 独立事务，不用全量 PUT。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-AUTH-001` | required | User→Agent；Agent→Skill/MCP；SELECTED 再叠加资源用户 Grant；不建三元授权。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-MODEL-001` | required | ModelDefinition 无 is_default；Agent 显式选择 enabled model_id。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-SNAPSHOT-001` | required | 新 Run/Task 冻结 Snapshot；配置/授权变更只影响后续新 Run/Task。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-IM-001` | required | Agent 0..N bot_id；bot_id 只指向一个 Agent；不绑定 Runtime Pod。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-FRONT-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-TEST-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
