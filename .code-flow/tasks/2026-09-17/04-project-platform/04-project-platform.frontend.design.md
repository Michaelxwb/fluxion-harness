# 项目平台与凭据 前端模块需求与设计简报

> **文档编号**: FE-PLATFORM-V1.0  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | 项目平台与凭据 |
| 前端目录 | `apps/console-platform/frontend/src/modules/project-platform/` |
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
| v1.1 | 2026-09-18 | muad-console-platform（frontend） | 对齐 V1.4 决策（docs/17）：交互基线切换 V1.4、调用测试改为配置校验/连通性探测、字段名按 docs/15、补充凭据失效与冲突场景 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 项目平台与凭据 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 字段完全按最新交互稿收敛，不再把 Base URL/服务发现混为一个输入。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | 平台列表/详情 | 平台适配器/接入方式/访问配置/凭据策略/已配置用户凭据数/共享凭据（已配置/未配置）/启用状态。 | P0 | 需求描述 |
| FEAT-FE-02 | 动态平台表单 | Base URL/服务发现字段 + adapter schema。 | P0 | 需求描述 |
| FEAT-FE-03 | 凭据管理 | 用户凭据 + 单套共享凭据；敏感字段不回显；用户详情「项目平台凭据」Tab 复用平台列表（按用户返回配置状态）与凭据表单。 | P0 | 需求描述 |
| FEAT-FE-04 | 配置校验与连通性探测 | 展示 Schema 校验、连通性与凭据引用状态；不在 Console 触发平台登录或业务调用。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 列表、新增/编辑、详情基本信息/凭据管理、动态 schema、配置校验与连通性探测。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力；不在 Console 执行平台登录/业务调用（真实鉴权调用归 Runtime/Worker Egress Boundary）。 |
| 技术债 | 无 |

### 2.4 验收条件

正常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-02 | E2E | Browser→adapter metadata→Form | 选择服务发现 + Adapter | 仅渲染对应 resolver/adapter 字段 |
| S-FE-02 | FEAT-FE-03 | E2E | Browser→Secret API | 更新用户凭据 | 保存后只显示已配置，不回显明文 |
| S-FE-03 | FEAT-FE-04 | E2E | Browser→API→网络探测→UI | 对平台执行配置校验与连通性探测 | 展示 config_valid/connectivity/credential_ref_status，不出现平台登录或业务调用 |
| S-FE-04 | FEAT-FE-03 | E2E | Browser→API→DB | 打开用户详情「项目平台凭据」Tab | 列出各平台与 `user_credential_status`（含未配置），不回显明文 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-01 | E2E | PUT API→UI | 更换 Adapter 且 `credential_reconfigure_required=true` | 提示凭据失效并引导凭据 Tab |
| E-FE-02 | FEAT-FE-04 | E2E | API→网络探测→UI | 连通性探测失败 | 阶段标记 UNREACHABLE/失败原因，不显示 Secret/Session |
| E-FE-03 | FEAT-FE-03 | integration | Schema 校验 API→Form | 凭据字段不满足 credential_schema | Form 字段级错误 + 本地化提示，不提交 |

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
| 项目平台 | `/platforms` | ConsoleShell | 列表 + 详情 SideSheet |

### 3.3 组件设计

```text
<Page>
├─ <ModuleToolbar/>
├─ <RemoteTable/>
└─ <DetailSideSheet/>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `PlatformPage` | 容器 | 模块内 | 列表/详情 |
| CMP-02 | `ProjectPlatformForm` | 容器 | 模块内 | resolver/adapter 动态字段 |
| CMP-03 | `CredentialTab` | 容器 | 模块内 | 用户/共享凭据 |
| CMP-04 | `PlatformTestModal` | 容器 | 模块内 | 配置校验/连通性结果 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 新增项目平台 | `Button` | primary | 打开新增 Modal | `POST /api/v1/project-platforms` | 否 |
| 列表操作列 | 配置校验 | `Button` | secondary | 打开校验 Modal | `POST /api/v1/project-platforms/{id}/test` | 否 |
| 详情 Header | 配置校验 | `Button` | secondary | 打开校验 Modal | `POST /api/v1/project-platforms/{id}/test` | 否 |
| 详情 Header | 编辑平台 | `Button` | primary | 打开编辑 Modal | `PUT /api/v1/project-platforms/{id}` | 否 |
| 详情 Header | 删除平台 | `Popconfirm + Button` | danger | 软删除平台 | `DELETE /api/v1/project-platforms/{id}` | 是 |
| 凭据 Tab | 配置用户凭据 | `Button` | primary | 用户选择+动态表单 | `PUT /api/v1/project-platforms/{id}/users/{user_id}/credential` | 否 |
| 用户凭据行 | 配置/更新 | `Button` | secondary | 打开凭据表单 | `PUT /api/v1/project-platforms/{id}/users/{user_id}/credential` | 否 |
| 共享凭据 | 更新 | `Button` | secondary | 打开共享凭据表单 | `PUT /api/v1/project-platforms/{id}/shared-credential` | 否 |

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
 -> modules/project-platform/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listPlatforms(params)` | `GET /api/v1/project-platforms` | usePlatformList |
| `getPlatform(id)` | `GET /api/v1/project-platforms/{id}` | usePlatformDetail |
| `getAdapters()` | `GET /api/v1/platform-adapters` | ProjectPlatformForm |
| `getAdapter(key)` | `GET /api/v1/platform-adapters/{adapter_key}` | ProjectPlatformForm/Detail |
| `savePlatform(input)` | `POST/PUT /api/v1/project-platforms` | ProjectPlatformForm |
| `deletePlatform(id)` | `DELETE /api/v1/project-platforms/{id}` | PlatformPage |
| `testPlatform(id,input)` | `POST /api/v1/project-platforms/{id}/test` | PlatformTestModal |
| `getUserCredential(id,userId)` | `GET /api/v1/project-platforms/{id}/users/{user_id}/credential` | CredentialTab |
| `saveUserCredential(id,userId,input)` | `PUT /api/v1/project-platforms/{id}/users/{user_id}/credential` | CredentialTab |
| `saveSharedCredential(id,input)` | `PUT /api/v1/project-platforms/{id}/shared-credential` | CredentialTab |

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
- 字段中文名使用 docs/15 词典：平台适配器/接入方式/访问配置/凭据策略/已配置用户凭据数/共享凭据（已配置/未配置）/启用状态；
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
| `harness-platform#RULE-i18n-001` | required | 错误与页面支持 zh-CN/en-US，仅通过 i18n key/配置扩展。 | §3.1、§3.3.1 | S-FE-01、E-FE-03 | applied |
| `harness-platform#RULE-ui-001` | required | React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.2、§3.3、§3.7 | S-FE-01 | applied |
| `harness-platform#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作与关闭 X 同行靠右，Tabs 在其下。 | §3.3.1、§3.4 | E-FE-01 | applied |
| `harness-platform#RULE-secret-001` | required | Secret 只保存引用，表单不回显明文；探测不读取 Secret Value。 | §2.4 S-FE-02、§3.3.1 | S-FE-02、S-FE-03 | applied |
| `harness-platform#RULE-platform-001` | required | 前端展示 credential_mode 选择策略与 credential_reconfigure_required 提示。 | §2.2 FEAT-FE-01/04、§2.4 | E-FE-01、S-FE-03 | applied |
| `harness-platform#RULE-front-001` | required | API 只经 services/，组件不裸用 axios/fetch，文案只用 i18n key。 | §3.5、§3.6 | S-FE-01、S-FE-02 | applied |
| `harness-platform#RULE-test-001` | required | 跨 Browser→API→网络探测/DB 的流程 E2E，明确不得 mock 的边界。 | §2.4 | S-FE-01、S-FE-03 | applied |
