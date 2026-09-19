# MCP Server 与工具目录 前端模块需求与设计简报

> **文档编号**: FE-MCP-V1.0  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | MCP Server 与工具目录 |
| 前端目录 | `apps/console-platform/frontend/src/modules/mcp-management/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `智能服务交付平台-V1.4-交互稿.html` |

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 开发负责人 | muad-console-platform（frontend） | 技术方案、代码实现 |
| 设计/交互 | — | 视觉与交互稿 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-09-17 | muad-console-platform（frontend） | 初始设计 |
| v1.1 | 2026-09-18 | muad-console-platform（frontend） | 对齐 V1.4 决策（docs/17）：交互基线切换 V1.4、工具数按发现总数且无 Tool 级启停、补 MCP_CONFIG_INVALID/MCP_DISCOVERY_FAILED 交互、服务方法补全 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | MCP Server 与工具目录 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 让启用状态、连接状态、工具发现状态和用户范围在 UI 上没有歧义。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | MCP 列表 | 服务地址/用户范围/指定用户数/工具数（发现总数）/使用 Agent 数/启用状态/连接状态/最近工具发现时间。 | P0 | 需求描述 |
| FEAT-FE-02 | MCP 详情 | 基本信息/工具明细/使用 Agent/指定用户 Tabs。 | P0 | 需求描述 |
| FEAT-FE-03 | 测试与刷新 | 连接测试与刷新工具目录语义分离；失败保留上一成功 Catalog。 | P0 | 需求描述 |
| FEAT-FE-04 | 用户范围 | Server 级 ALL/SELECTED；无 Tool 级授权/启停。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 列表/注册/编辑/详情、工具详情、连接测试、刷新目录、范围与指定用户。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力；不提供 Tool 级启停/授权入口。 |
| 技术债 | 无 |

### 2.4 验收条件

正常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-03 | E2E | Browser→MCP→DB→UI | 点击刷新工具目录 | 按钮 loading，成功后工具数和最近工具发现时间刷新 |
| S-FE-02 | FEAT-FE-02 | E2E | Browser→tools API | 点击工具名 | 展示 input schema 和操作类型，无 Tool 级权限/启停 |
| S-FE-03 | FEAT-FE-01 | E2E | Browser→MCP Server→DB→UI | 注册 Server 并连接测试 | connection_status 与工具数正确展示 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-03 | E2E | MCP failure→API→UI | tools/list 失败 | 显示发现失败但仍显示上一成功 Catalog |
| E-FE-02 | FEAT-FE-04 | integration | Grant API | 移除指定用户失败 | 不先本地删行，Toast 提示 |
| E-FE-03 | FEAT-FE-01 | integration | PUT API→UI | 更新时 `MCP_CONFIG_INVALID` | Toast 展示字段错误，不覆盖本地表单 |
| E-FE-04 | FEAT-FE-01 | integration | API→Form | transport 非 Streamable HTTP 或配置非法 | Form 字段级错误 + 本地化提示，不提交 |

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
| MCP 管理 | `/mcp` | ConsoleShell | MCP Server 列表 + SideSheet |

### 3.3 组件设计

```text
<Page>
├─ <ModuleToolbar/>
├─ <RemoteTable/>
└─ <DetailSideSheet/>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `McpPage` | 容器 | 模块内 | 列表/详情/刷新 |
| CMP-02 | `McpToolTable` | 展示 | 模块内 | 工具/描述/input schema/操作类型 |
| CMP-03 | `McpTestModal` | 展示 | 模块内 | 连接步骤结果 |
| CMP-04 | `SelectedUserTable` | 展示 | 与 Skill 同模式 | 指定用户 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 注册 MCP | `Button` | primary | 打开注册 Modal | `POST /api/v1/mcp-servers` | 否 |
| 列表操作列 | 刷新工具 | `Button` | secondary | discover-tools | `POST /api/v1/mcp-servers/{id}/discover-tools` | 否 |
| 详情 Header | 编辑 MCP | `Button` | primary | 打开编辑 Modal（携带 revision） | `PUT /api/v1/mcp-servers/{id}` | 否 |
| 详情 Header | 删除 MCP | `Popconfirm + Button` | danger | 软删除 | `DELETE /api/v1/mcp-servers/{id}` | 是 |
| 详情 Header | 连接测试 | `Button` | secondary | 只测连接/initialize | `POST /api/v1/mcp-servers/{id}/test` | 否 |
| 详情 Header | 变更用户范围 | `Button` | secondary | 打开范围 Modal | `PUT /api/v1/mcp-servers/{id}/user-scope` | 否 |
| 详情 Header | 刷新工具目录 | `Button` | primary | tools/list 并持久化 | `POST /api/v1/mcp-servers/{id}/discover-tools` | 否 |
| 工具 Tab | 刷新工具目录 | `Button` | secondary | 复用同一 service | `POST /api/v1/mcp-servers/{id}/discover-tools` | 否 |
| 工具行 | 工具名称链接 | `Typography.Text link` | secondary | 打开工具详情 | `GET /api/v1/mcp-servers/{id}/tools/{tool_name}` | 否 |
| 指定用户 Tab | 添加指定用户 | `Button` | primary | 创建 McpUserGrant | `POST /api/v1/mcp-servers/{id}/users/{user_id}` | 否 |
| 指定用户行 | 移除 | `Popconfirm + Button` | secondary | 删除 Grant | `DELETE /api/v1/mcp-servers/{id}/users/{user_id}` | 是 |

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
 -> modules/mcp-management/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listMcpServers(params)` | `GET /api/v1/mcp-servers` | useMcpList |
| `getMcpServer(id)` | `GET /api/v1/mcp-servers/{id}` | useMcpDetail |
| `createMcpServer(input)` | `POST /api/v1/mcp-servers` | McpFormModal |
| `updateMcpServer(id,input)` | `PUT /api/v1/mcp-servers/{id}`（无需 `expected_revision`） | McpFormModal |
| `deleteMcpServer(id)` | `DELETE /api/v1/mcp-servers/{id}` | McpPage |
| `testMcp(id)` | `POST /api/v1/mcp-servers/{id}/test` | McpTestModal |
| `discoverTools(id)` | `POST /api/v1/mcp-servers/{id}/discover-tools` | McpPage/McpToolTab |
| `listTools(id,params)` | `GET /api/v1/mcp-servers/{id}/tools` | McpToolTable |
| `getTool(id,name)` | `GET /api/v1/mcp-servers/{id}/tools/{tool_name}` | McpToolDetail |
| `setUserScope(id,scope)` | `PUT /api/v1/mcp-servers/{id}/user-scope` | UserScopeModal |
| `listSelectedUsers(id,params)` | `GET /api/v1/mcp-servers/{id}/users` | SelectedUserTable |
| `addSelectedUser(id,userId)` | `POST /api/v1/mcp-servers/{id}/users/{user_id}` | SelectedUserTable |
| `removeSelectedUser(id,userId)` | `DELETE /api/v1/mcp-servers/{id}/users/{user_id}` | SelectedUserTable |

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Table loading/Skeleton | Empty + 创建/清筛选 | ErrorState + 重试 | Table + 右下 Pagination |
| 详情 | SideSheet Spin | Tab Empty | Banner/ErrorState | Descriptions/List/Table |
| 表单 | 保存按钮 loading | - | Form 字段错误 + Toast | 关闭 Modal + 局部刷新 |
| 关系操作 | 当前按钮 loading | - | Toast，保持当前 Tab | 局部刷新 |

### 3.7 样式方案

- 保留交互稿信息架构和字段顺序，可用 Semi Token 重新美化；
- **字段布局排版以交互稿为准**：新增/编辑表单使用双列栅格（`.form-grid`，同一栅格内控件 100% 同宽、按交互稿顺序成对排列，动态/配置类字段配分区标题与说明），详情基本信息用 `DetailGrid` 双列、关系表列名与操作列对齐交互稿；禁止宽窄混排与裸 schema key。
- 列表页不增加重复标题/说明块；
- Toolbar 左主操作、右搜索筛选；
- 主展示字段点击打开详情；操作列只放真实动作；
- 字段中文名使用 docs/15 词典：启用状态/连接状态/最近工具发现时间/工具数/操作类型；
- 详情双列基础信息，<900px 降单列；
- 时间统一 `YYYY-MM-DD HH:mm:ss`；
- 禁止散落魔法颜色/间距。

### 3.8 可访问性与兼容性

Semi Form required/rules；Modal/SideSheet 焦点管理；图标按钮 aria-label；Chrome/Edge 企业当前版本为主，Safari 做开发兼容验证。

## 4. 风险与依赖

- 前置：01-platform-foundation, 02-user-identity；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 错误与页面支持 zh-CN/en-US，仅通过 i18n key/配置扩展。 | §3.1、§3.3.1 | S-FE-03、E-FE-04 | applied |
| `harness-platform#RULE-ui-001` | required | React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.2、§3.3、§3.7 | S-FE-01 | applied |
| `harness-platform#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作与关闭 X 同行靠右，Tabs 在其下。 | §3.3.1、§3.4 | E-FE-03 | applied |
| `harness-platform#RULE-auth-001` | required | 指定用户 Tab 只管理 McpUserGrant，无 Tool 级授权。 | §2.2 FEAT-FE-04、§2.4 | S-FE-02、E-FE-02 | applied |
| `harness-platform#RULE-mcp-001` | required | 工具数/明细来自 catalog 快照；刷新语义独立；无 Tool 级启停。 | §2.2、§3.3.1 | S-FE-01、S-FE-02、E-FE-01 | applied |
| `harness-platform#RULE-front-001` | required | API 只经 services/，组件不裸用 axios/fetch，文案只用 i18n key。 | §3.5、§3.6 | S-FE-01、S-FE-03 | applied |
| `harness-platform#RULE-test-001` | required | 跨 Browser→MCP Server→DB→UI 的流程 E2E，明确不得 mock 的边界。 | §2.4 | S-FE-01、E-FE-01 | applied |
