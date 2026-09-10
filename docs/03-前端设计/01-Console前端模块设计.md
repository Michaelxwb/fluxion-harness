# Console 前端模块需求与设计简报

> **文档编号**: FE-CONSOLE-1.0  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-10  
> **文档状态**: 设计草稿（V1.7 整改对齐）  
> **设计基线**: 《通用智能服务执行框架 V1.6 总体设计说明书》+《05-变更记录/V1.6-to-V1.7-整改对齐说明》（D02/D03/D04）  
> **模板类型**: design-frontend  
> **UI 组件库硬约束**: Semi Design

**评审边界说明**：

- 需求评审：第 2 章，确认 Console 页面范围与管理员/Builder 旅程；
- 设计评审：第 3 章，确认 Semi Design、统一列表、API/状态/组件边界；
- Console 是低频 Control Plane，不进入终端用户实时执行主链。

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 开发负责人 | 待定 | React/TypeScript 架构、组件封装、页面实现 |
| 设计/交互 | 待定 | 页面结构、交互一致性 |
| 后端接口负责人 | 待定 | Platform API Contract |
| 测试负责人 | 待定 | E2E/组件测试 |

### 1.2 修订历史

| 版本 | 日期 | 变更描述 |
|---|---|---|
| v0.1 | 2026-09-10 | 基于总体设计 V1.6 和前端模板形成首版详细设计 |
| v1.1 | 2026-09-10 | V1.7 整改：Agent 页删发布（D04）、WAITING_HUMAN 仅继续/终止（D02）、Execution 与 Delivery 状态分开展示（D03） |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Console 前端 |
| 需求类型 | 新框架前端 / 组件规范化 |
| 业务背景 | Admin/Builder 需要配置 Agent、Service、Capability、Knowledge、Channel、用户授权并查看 Execution；历史项目中各页面列表/新增/分页/详情交互不一致 |
| 核心目标 | 用统一 Semi Design 组件和公共页面骨架形成一致、低学习成本、可复用的控制台 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-01 | 全局布局与导航 | Console Shell、侧栏、Breadcrumb、页面标题、路由 | P0 | 总体设计 V1.6 |
| FEAT-02 | 标准列表页 | 统一按钮、筛选、搜索、Table、Pagination、行操作 | P0 | 用户明确工程约束 |
| FEAT-03 | Agent 管理 | 列表、详情、编辑/保存、启用-禁用（V1.7 D04：无草稿/发布）；Service 发布时冻结 Agent 配置快照 | P0 | 总体设计 V1.7 |
| FEAT-04 | Service 管理 | 列表、详情、草稿编辑、测试、发布、停用 | P0 | 总体设计 V1.6 |
| FEAT-05 | Capability/Knowledge/Model/Skill | 通用资源 CRUD，除不可变 artifact 外配置保存直接生效 | P0 | 总体设计 V1.6 |
| FEAT-06 | Channel/Integration | Channel Account、Adapter/Integration 注册信息、启停/健康 | P0 | 总体设计 V1.6 |
| FEAT-07 | 用户与授权 | PlatformUser、Channel Binding、Service/Capability Grant、AuthProfile Ref | P0 | 总体设计 V1.6 |
| FEAT-08 | Execution/Trace | 运行记录、Step、Progress、Artifact、命令/取消/重试；V1.7：WAITING_HUMAN 仅[继续][终止]（D02），Execution 成功与 Delivery 失败分开展示 + DELIVERY_DEAD_LETTER（D03），HUMAN_TIMEOUT 按 USER_INACTION 单独归类 | P0 | 总体设计 V1.7 |
| FEAT-09 | 统一 API/错误处理 | ApiEnvelope、request_id、Toast/Notification | P0 | 后端统一 Response |
| FEAT-10 | 四态与可访问性 | loading/empty/error/success、键盘/焦点/aria | P1 | 模板/工程规范 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | Console 管理页、公共布局、公共列表、Drawer/Modal/Form、统一 API Client、状态/错误处理 |
| 非范围（Out of Scope） | 业务平台 WebChat；终端用户 Chat UI；可视化 Workflow Designer；动态 Plugin Marketplace；第二套 UI 组件库 |
| 有意妥协 / 技术债 | V1 不先引入 Redux/Zustand；优先局部 state + hooks，只有跨页面共享状态出现真实需求后再引入全局 store |

### 2.4 验收条件

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|---|
| S-01 | FEAT-02 | P0 | E2E | Browser → Router → Platform API → Table | 打开任意标准资源列表 | 左上主按钮、右上筛选/搜索、中部表格、右下分页位置一致 |
| S-02 | FEAT-03 | P0 | E2E | Browser → API → Store → UI | 打开 Agent，点击编辑，保存草稿，再发布 | Draft 变化不影响 Published；发布成功后状态更新 |
| S-03 | FEAT-05 | P0 | E2E | Browser → API → UI | 修改 Knowledge/Model/Capability Implementation 配置并保存 | 校验成功后直接生效，不出现无意义“版本激活”流程 |
| S-04 | FEAT-08 | P0 | E2E | Browser → API → Execution Store → UI | 查看 Execution 详情 | 展示状态、Steps、Progress、Artifact、Trace；允许合法命令 |
| S-05 | FEAT-09 | P0 | integration | Axios Client → ApiEnvelope → Notification | 后端返回 AppError Envelope | 页面统一展示 message/request_id，不解析自定义错误结构 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-01 | FEAT-02 | E2E | Browser → API → UI | 列表请求失败 | Table 区域显示统一错误态并提供重试；分页不显示错误总数 |
| E-02 | FEAT-03 | E2E | Browser → API → UI | 发布校验失败 | 保留 Draft；展示字段/依赖错误；不得错误标记为已发布 |
| E-03 | FEAT-07 | E2E | Browser → API → UI | 管理员无权限执行危险操作 | 操作被拒并显示统一错误，不隐藏 request_id |
| E-04 | FEAT-10 | integration | Component | 空数据 | 使用统一 Empty，不渲染空白 Table/异常分页 |

**非功能指标**

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|---|---|---|---|
| NFR-PERF-01 | 首屏/交互响应 | 待定 | Lighthouse + 真实部署实测 |
| NFR-UX-01 | 标准列表布局一致性 | 所有普通 CRUD 列表 100% 复用 StandardListPage，例外必须 ADR | Architecture Test + Review |
| NFR-A11Y-01 | 基础可访问性 | Icon-only 操作具备 aria-label；Modal/Drawer 焦点可恢复 | 组件/E2E |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 框架 | React | 18.3.x | 生态成熟、当前骨架已采用 |
| 语言 | TypeScript | 5.7.x | 强类型 API/组件 Contract |
| 构建 | Vite | 6.x | 开发体验与静态构建 |
| UI 组件 | `@douyinfe/semi-ui` | 2.103.0 | 项目硬约束，Console 唯一通用组件库 |
| Icons | `@douyinfe/semi-icons` | 2.103.x | 与 Semi Design 配套 |
| 路由 | react-router-dom | 7.x | 页面路由 |
| 数据请求 | Axios + 统一 API Client | 1.7.x | 拦截 Envelope/request_id/error |
| 状态管理 | Local state + custom hooks | V1 | 不提前引入额外全局状态框架 |
| 样式 | Semi Token + 全局布局 CSS/模块化 CSS | V1 | 避免页面魔法值和第二套 Design System |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| 首页/概览 | `/` | ConsoleLayout | 运行健康、快捷入口，V1 可简化 |
| Agent 列表 | `/agents` | StandardListPage | 管理 Agent Definition |
| Agent 编辑 | `/agents/:id/edit` | EditPage | Draft/Test/Publish |
| Service 列表 | `/services` | StandardListPage | 管理 Service |
| Service 编辑 | `/services/:id/edit` | EditPage | Draft/Test/Publish |
| Capability | `/capabilities` | StandardListPage | Contract/Implementation |
| Knowledge | `/knowledge` | StandardListPage | Knowledge Source |
| Skills | `/skills` | StandardListPage | Immutable Artifact |
| Models | `/models` | StandardListPage | Model Config |
| Channels | `/channels` | StandardListPage | Channel Account/Adapter |
| Integrations | `/integrations` | StandardListPage | 部署装配状态，V1 只读+enable 按后端能力 |
| Users | `/users` | StandardListPage | PlatformUser/Binding/Auth |
| Executions | `/executions` | StandardListPage | 运行记录 |
| Execution 详情 | `/executions/:id` | DetailPage | Step/Progress/Artifact/Trace |
| 系统设置 | `/settings` | SettingsPage | 非业务运行配置 |

导航按照**业务对象**组织，不新增 Tool/MCP/Plugin 等并列一级菜单。MCP/Sandbox/HTTP 作为 Capability Implementation 技术类型展示。

### 3.3 组件设计

**公共组件树**

```text
<App>
└─ <ConsoleLayout>
   ├─ <SideNavigation>
   ├─ <TopHeader>
   └─ <Outlet>
      ├─ <StandardListPage>
      │  ├─ <ListToolbar>
      │  │  ├─ <PrimaryActions>
      │  │  └─ <FiltersSearchActions>
      │  ├─ <SemiTable>
      │  └─ <ListFooter>
      │     └─ <SemiPagination>
      ├─ <ReadonlyDetailDrawer>
      ├─ <StandardFormModal>
      ├─ <PublishPanel>
      └─ <AsyncStateBoundary>
```

**标准列表布局是强约束**

```text
┌──────────────────────────────────────────────────────────────┐
│ 页面标题                                                      │
│                                                              │
│ [新增/主要操作]                         [过滤][筛选][搜索][刷新] │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                         Semi Table                           │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                      Pagination（右下角）     │
└──────────────────────────────────────────────────────────────┘
```

禁止页面自行改变：

- 新增按钮到右侧；
- Pagination 放 Table 内或左下；
- 过滤/搜索散落在内容区；
- 每个页面自写一套 Toolbar；
- Table 自带 pagination 与公共 Footer 同时出现。

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `ConsoleLayout` | 容器 | 全站 | 导航、Header、Outlet |
| CMP-02 | `StandardListPage<T>` | 展示壳 | 所有普通列表 | 固定 Toolbar/Table/Footer |
| CMP-03 | `ListToolbar` | 展示 | StandardListPage | 左主操作、右筛选搜索 |
| CMP-04 | `ListFooter` | 展示 | StandardListPage | 右下 Pagination |
| CMP-05 | `ReadonlyDetailDrawer` | 展示 | 资源详情 | 只读详情，不混编辑 |
| CMP-06 | `StandardFormModal` | 容器/展示 | 简单新增/编辑 | Form/校验/submit |
| CMP-07 | `PublishPanel` | 容器 | Agent/Service | Draft/Test/Publish |
| CMP-08 | `AsyncStateBoundary` | 展示 | 异步视图 | loading/empty/error/success |
| CMP-09 | `ExecutionTimeline` | 展示 | Execution Detail | Step/Progress 状态 |
| CMP-10 | `ApiErrorNotice` | 展示 | 全站 | message + request_id + retry |

### 3.4 组件接口契约

**CMP-02 `StandardListPage<T>`**

| Props | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `rowKey` | `string \| (record:T)=>string` | 是 | - | 行唯一键 |
| `columns` | `ColumnProps<T>[]` | 是 | - | Semi Table 列 |
| `dataSource` | `T[]` | 是 | `[]` | 当前页数据 |
| `loading` | `boolean` | 否 | `false` | 加载状态 |
| `primaryActions` | `ReactNode` | 否 | - | 左上主操作 |
| `filters` | `ReactNode` | 否 | - | 右上筛选/搜索 |
| `pagination` | `ListPagination` | 是 | - | 服务端分页状态 |
| `empty` | `ReactNode` | 否 | 公共 Empty | 空态 |

| Events / 回调 | 载荷类型 | 触发时机 |
|---|---|---|
| `onPageChange` | `(page:number)=>void` | 页码改变 |
| `onPageSizeChange` | `(pageSize:number)=>void` | 页大小改变 |

`StandardListPage` 内部强制 Semi `Table pagination={false}`。

**CMP-05 `ReadonlyDetailDrawer`**

| Props | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `visible` | boolean | 是 | - | 是否显示 |
| `title` | ReactNode | 是 | - | 标题 |
| `loading` | boolean | 否 | false | 加载 |
| `children` | ReactNode | 是 | - | 只读详情 |
| `actions` | ReactNode | 否 | - | 编辑/停用等明确动作 |

详情内容禁止直接切换为可编辑 input；点击编辑后进入明确 Form/Modal/Page。

### 3.5 状态与数据流

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|---|---|---|---|
| 路由状态 | URL | `page/pageSize/search/filter/sort` | Router + page hook |
| 列表数据 | local hook | `items/total/loading/error` | `usePagedResource` |
| Drawer | local | `selectedId/visible` | List Page |
| Form Draft | local/form | typed form data | Form |
| 发布状态 | local hook | `draft/published/validation` | Agent/Service edit page |
| 当前用户/权限 | app shared | minimal auth context | Layout/Guard |
| 全局 API Error | 通用 handler | ApiEnvelope error | API Client/Notification |

**数据流**

```text
用户操作
→ Page Event
→ Hook
→ services/*
→ Axios API Client
→ Platform API
→ ApiEnvelope<T>
→ Hook State
→ Semi UI 重渲染
```

组件层禁止裸 `axios/fetch`。

**Service 方法基线**

| Service 方法 | 后端接口 | 调用方 |
|---|---|---|
| `agentService.list()` | `GET /api/v1/agents` | AgentListPage |
| `agentService.get(id)` | `GET /api/v1/agents/:id` | Drawer/Edit |
| `agentService.saveDraft()` | `PUT /api/v1/agents/:id/draft` | AgentEditPage |
| `agentService.publish()` | `POST /api/v1/agents/:id/publish` | PublishPanel |
| `serviceService.list()` | `GET /api/v1/services` | ServiceListPage |
| `executionService.list()` | `GET /api/v1/executions` | ExecutionListPage |
| `executionService.command()` | `POST /api/v1/executions/:id/commands` | ExecutionDetailPage |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 标准列表 | Table loading | 公共 Empty + 可选新增入口 | ErrorState + Retry | Table |
| 详情 Drawer | Spin/Skeleton | 查无资源提示 | Error Notice | Descriptions/Sections |
| Form | Submit loading | N/A | 字段错误 + 顶部错误 | Toast + close/refresh |
| Publish | Button loading | 无 Draft 时说明 | Validate Error 列表 | Published 状态与时间 |
| Execution | Skeleton | 无 step 时说明 | 状态查询失败 + Retry | Timeline/Artifact |

### 3.7 样式方案

| 维度 | 约定 |
|---|---|
| 样式与逻辑分离 | 业务 Page 不在数据请求逻辑中拼 style；公共布局组件统一 CSS |
| 设计 Token | 优先 Semi Design Token；公共间距通过变量/组件 props |
| 列表布局 | `StandardListPage` 固定 Card padding/toolbar/table/footer |
| 响应式 | Console 主要面向桌面；窄屏允许 Toolbar 换行但不改变左右语义顺序 |
| 表格 | 常用列宽/ellipsis/操作列统一 helper |
| 危险操作 | Semi `Popconfirm`/`Modal.confirm`，按钮使用 danger 语义 |

### 3.8 可访问性与兼容性

| 维度 | 要求 |
|---|---|
| 可访问性 | 语义标签、aria-label、表单错误关联、键盘可达、焦点恢复 |
| 浏览器 | 当前企业桌面 Chrome/Edge；精确版本范围待部署环境确认 |
| 颜色 | 状态不能只依赖颜色，配合 Tag 文案/Icon |
| 动画 | 非关键动画可关闭/简化 |

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|---|---|---|---|---|
| RISK-01 | 页面绕开 StandardListPage | 列表布局再次漂移 | Architecture test + code review | S-01 |
| RISK-02 | 引入第二套 UI Library | 视觉/包体/维护成本增加 | package.json Gate | S-01 |
| RISK-03 | 页面直接解析不同错误结构 | 错误体验和代码分叉 | 统一 API Client | S-05 |
| RISK-04 | Draft/Published UI 误用于所有资源 | 重新形成重型版本管理 | 仅 Agent/Service PublishPanel | S-03 |
| RISK-05 | 详情 Drawer 混入编辑 | 用户操作不清晰 | Drawer 只读，编辑独立入口 | S-02 |

依赖：

```text
Platform API
Semi Design
React Router
统一 ApiEnvelope
后端权限/分页 Contract
```

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | verifier | 状态 |
|---|---|---|---|---|---|---|
| framework#RULE-FE-001 | required | Console 仅使用 Semi Design | §3.1 | S-01 | `test_ui_conventions.py` | applied |
| framework#RULE-FE-002 | required | CRUD 列表固定布局并复用 StandardListPage | §3.3/§3.4 | S-01/E-01 | `test_ui_conventions.py` + E2E | applied |
| framework#RULE-BE-001 | required | API Client 统一解析 ApiEnvelope | §3.5 | S-05 | frontend integration | applied |

---

## 附录：术语表

| 术语 | 定义 |
|---|---|
| FEAT | 前端功能项 |
| CMP | 前端组件 |
| StandardListPage | Console 标准列表页面壳 |
| Container Component | 数据/状态容器 |
| Presentational Component | props in / events out 的展示组件 |
| ApiEnvelope | 统一后端 JSON Response Contract |
| Draft/Published | 仅 Service/关键 Agent 的两态发布模型 |

---
