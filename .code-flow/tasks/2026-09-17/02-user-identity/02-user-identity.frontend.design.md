# 用户、身份与绑定 前端模块需求与设计简报

> **文档编号**: FE-USER-V1.0  
> **文档版本**: v1.0  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | 用户、身份与绑定 |
| 前端目录 | `apps/console-platform/frontend/src/modules/user-identity/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `MSS智能服务交付平台-V1.3-交互稿.html` |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 用户、身份与绑定 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 按最新交互稿实现用户详情，同时让跨模块 Tab 只消费对应 Service Contract。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | 用户列表 | 姓名/账号/授权数/凭据数/身份数/Memory数/状态/更新时间。 | P0 | 需求描述 |
| FEAT-FE-02 | 用户详情 | 基本信息/Agent授权/项目平台凭据/IM身份/用户记忆 Tabs。 | P0 | 需求描述 |
| FEAT-FE-03 | 绑定码与身份 | 生成绑定码、查看/解绑身份。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 用户列表、新增/编辑、详情 SideSheet、IM 身份；Agent授权/凭据/Memory Tab 由对应模块接口闭合。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力 |
| 技术债 | 无 |

### 2.4 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-01 | E2E | Browser→users API→DB→Table | 新增用户后返回列表 | 新用户出现且字段格式统一 |
| S-FE-02 | FEAT-FE-03 | E2E | Browser→bind-code API | 点击生成绑定码 | Modal 展示 code/过期时间 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-03 | integration | API→Toast | 生成绑定码失败 | SideSheet 保留且显示本地化 msg |
| E-FE-02 | FEAT-FE-02 | E2E | Browser→后置模块 API | Memory/Agent API 暂不可用 | 对应 Tab 独立 ErrorState，不影响其他 Tab |

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
| 用户管理 | `/users` | ConsoleShell | 列表 + 详情 SideSheet |

### 3.3 组件设计

```text\n<Page>\n├─ <ModuleToolbar/>\n├─ <RemoteTable/>\n└─ <DetailSideSheet/>\n```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `UserPage` | 容器 | 模块内 | 列表/详情/表单状态 |
| CMP-02 | `UserDetailTabs` | 展示 | 模块内 | 5 个详情 Tab |
| CMP-03 | `IdentityTable` | 展示 | 模块内 | 通道/外部用户ID/bot_id/绑定/活动/状态 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 新增用户 | `Button` | primary | 打开新增 Modal | `POST /api/v1/users` | 否 |
| 列表右上 | 搜索 | `Button` | secondary | 姓名/账号查询并回第1页 | `GET /api/v1/users` | 否 |
| 列表右上 | 重置 | `Button` | secondary | 清筛选刷新 | `GET /api/v1/users` | 否 |
| 列表右上 | 刷新 | `Button` | secondary | 刷新当前页 | `GET /api/v1/users` | 否 |
| 基本信息 | 编辑基本信息 | `Button` | secondary | 打开编辑 Modal | `PUT /api/v1/users/{id}` | 否 |
| Agent 授权 Tab | 授权 Agent | `Button` | primary | 选择 Agent 创建 Grant | `POST /api/v1/users/{id}/agents/{agent_id}` | 否 |
| Agent 授权行 | 取消授权 | `Popconfirm + Button` | secondary | 删除 Grant | `DELETE /api/v1/users/{id}/agents/{agent_id}` | 是 |
| IM 身份 Tab | 生成绑定码 | `Button` | primary | 生成一次性绑定码 | `POST /api/v1/users/{id}/bind-codes` | 否 |
| IM 身份行 | 解绑 | `Popconfirm + Button` | secondary | 解绑身份 | `DELETE /api/v1/users/{id}/identities/{identity_id}` | 是 |
| 用户记忆 Tab | 清理全部 | `Popconfirm + Button` | danger | 清理 Memory | `DELETE /api/v1/users/{id}/memory` | 是 |

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
 -> modules/user-identity/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listUsers(params)` | `GET /api/v1/users` | useUserList |
| `getUser(id)` | `GET /api/v1/users/{id}` | useUserDetail |
| `createBindCode(id)` | `POST /api/v1/users/{id}/bind-codes` | IdentityTab |
| `listAgentGrants(id)` | `GET /api/v1/users/{id}/agents` | AgentGrantTab（模块07） |
| `listMemory(id)` | `GET /api/v1/users/{id}/memory` | MemoryTab（模块08） |

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

- 前置：01-platform-foundation；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `mss-platform#RULE-I18N-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-UI-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-UI-DETAIL-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-TIME-001` | required | Console 时间统一 YYYY-MM-DD HH:mm:ss。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-FRONT-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-IM-001` | required | Agent 0..N bot_id；bot_id 只指向一个 Agent；不绑定 Runtime Pod。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `mss-platform#RULE-TEST-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
