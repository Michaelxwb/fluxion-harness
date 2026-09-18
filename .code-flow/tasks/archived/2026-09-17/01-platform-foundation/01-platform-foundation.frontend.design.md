# 平台底座与公共框架 前端模块需求与设计简报

> **文档编号**: FE-FOUNDATION-V1.1  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | 平台底座与公共框架 |
| 前端目录 | `apps/console-platform/frontend/src/modules/platform-foundation/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `智能服务交付平台-V1.4-交互稿.html` |

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | 待定 | 技术方案、代码实现 |
| 设计/交互 | 待定 | 视觉与交互稿 |
| 测试负责人 | 待定 | 测试策略、质量保证 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v1.0 | 2026-09-17 | fluxion-harness | 初始设计 |
| v1.1 | 2026-09-18 | fluxion-harness | 对齐 V1.4 决策（docs/17）：交互基线升级 V1.4、ConsoleShell 固定 10 项菜单并声明无系统设置/中间件状态、401 跳转登录归属 13-console-auth、补场景与 Spec Matrix 落点 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 平台底座与公共框架 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 为所有业务页面提供唯一的 Semi Design 页面骨架和国际化/API 错误能力。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | ConsoleShell | Layout/Nav/Header/Outlet/语言切换；固定 10 项菜单。 | P0 | 需求描述 |
| FEAT-FE-02 | 公共列表组件 | Toolbar/Table/Pagination/EntityLink。 | P0 | 需求描述 |
| FEAT-FE-03 | 公共详情/表单 | SideSheet/Tabs/Modal/Form/Confirm/Toast。 | P0 | 需求描述 |
| FEAT-FE-04 | 前端 i18n/ApiClient | locale persistence + X-Locale + Envelope 错误处理 + 401 跳转登录。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | ConsoleShell（固定 10 项菜单：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计）、ModuleToolbar、RemoteTable、DetailSideSheet、FormModal、状态/空态/错误态、LocaleSwitch、ApiClient。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力；不做登录页、账号管理与 RBAC 业务（归属 13-console-auth，本模块只提供 401 跳转与角色菜单过滤原语）；无系统设置/中间件状态菜单。 |
| 技术债 | 无 |

### 2.4 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-01 | E2E | Browser Router→ConsoleShell | 切换任意模块路由 | Layout 不重建且菜单选中正确 |
| S-FE-02 | FEAT-FE-04 | E2E | Browser→LocalStorage→API | 切换 English 后刷新 | 语言保持且请求带 X-Locale=en-US |
| S-FE-03 | FEAT-FE-01 | unit | ConsoleShell 菜单 | 渲染 ConsoleShell | 菜单恰为固定 10 项，无系统设置/中间件状态 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-04 | integration | Axios interceptor→Toast | 后端 code!=0 | 统一展示本地化 msg |
| E-FE-02 | FEAT-FE-03 | unit | DetailSideSheet | actions 为空 | 不出现空操作区，X 仍在右上 |
| E-FE-03 | FEAT-FE-04 | E2E | ApiClient→401→Router | 会话失效访问业务页 | 跳转 13-console-auth 登录页并保留 returnUrl，不渲染业务数据 |

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
| 应用壳 | `/*` | ConsoleShell | 所有 Console 页面共用 |

ConsoleShell 菜单固定 10 项且顺序固定：

```text
概览 / Agent / Skill / MCP / 模型 / 用户 / 项目平台 / 后台任务 / 定时任务 / 运行审计
```

无“系统设置/中间件状态”菜单，不展示 PostgreSQL、Redis、NFS/PVC、Runtime Pod、Worker Pod 健康状态（由部署平台/OTel/监控系统负责，docs/00 §0.7）。

### 3.3 组件设计

```text
<App>
└─ <ConsoleShell>
   ├─ <Navigation/>          # 固定 10 项菜单，按 13-console-auth 角色上下文过滤
   ├─ <Header><LocaleSwitch/></Header>
   └─ <Outlet>
      └─ <ModulePage>
         ├─ <ModuleToolbar/>
         ├─ <RemoteTable/>
         ├─ <PaginationFooter/>
         ├─ <DetailSideSheet><DetailTabs/></DetailSideSheet>
         └─ <FormModal/>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `ConsoleShell` | 容器 | 模块内 | Semi Layout/Nav/Header + Outlet；固定 10 项菜单，无系统设置/中间件状态 |
| CMP-02 | `ModuleToolbar` | 展示 | 全模块 | 左操作、右搜索筛选 |
| CMP-03 | `RemoteTable` | 展示 | 全模块 | Semi Table 受控分页 |
| CMP-04 | `DetailSideSheet` | 展示 | 全模块 | 标题/副标题/操作/X 同行 + Tabs |
| CMP-05 | `FormModal` | 展示 | 全模块 | Semi Modal + Form + submit state |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| Header | 中文 / English | `Select` | locale | 切换 locale 并持久化 | `-` | 否 |
| ModuleToolbar | 刷新 | `Button` | secondary | 保留筛选刷新当前页 | `模块列表 API` | 否 |
| DetailSideSheet | 关闭 X | `SideSheet.onCancel` | close | 关闭并恢复焦点 | `-` | 否 |
| FormModal | 取消 | `Button` | secondary | 关闭且不提交 | `-` | 否 |
| FormModal | 保存 | `Button` | primary | validate→service→刷新→Toast | `模块 create/update API` | 否 |

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
 -> modules/platform-foundation/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

- 401：ApiClient 统一跳转 13-console-auth 登录页并携带 returnUrl，不渲染业务数据；
- 403：统一 Toast `FORBIDDEN` 本地化文案；
- 角色菜单过滤：菜单可见性由 13-console-auth 提供的角色上下文驱动，01 只提供过滤原语。

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `apiClient.request()` | 统一 Envelope 处理（无独立后端接口） | 全模块 services |
| `authContext.getRole()` | 13-console-auth 提供 | Navigation 菜单过滤 |

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

- 前置：无（登录/RBAC 业务依赖 13-console-auth 提供角色上下文）；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1、菜单随页面实现漂移；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段、菜单固定 10 项由 S-FE-03 守护。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.1 / §3.5 | S-FE-02, E-FE-01（verifier: project-owner） | applied |
| `harness-platform#RULE-ui-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情；菜单固定十项。 | §3.2 / §3.3 CMP-01/CMP-02 | S-FE-01, S-FE-03（verifier: project-owner 确认 10 项菜单与无系统设置） | applied |
| `harness-platform#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3 CMP-04 / §3.4 | S-FE-01, E-FE-02（verifier: project-owner） | applied |
| `harness-platform#RULE-time-001` | required | Console 时间统一 YYYY-MM-DD HH:mm:ss。 | §3.7 / DateTimeText | S-FE-01（verifier: project-owner） | applied |
| `harness-platform#RULE-front-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.5 / §3.6 | S-FE-02, E-FE-01, E-FE-03（verifier: project-owner） | applied |
| `harness-platform#RULE-test-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §2.4 / §3.5 | S-FE-01~S-FE-03, E-FE-01~E-FE-03（verifier: project-owner） | applied |
