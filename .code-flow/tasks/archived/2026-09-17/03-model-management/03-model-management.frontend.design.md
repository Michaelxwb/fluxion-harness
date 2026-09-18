# 模型管理 前端模块需求与设计简报

> **文档编号**: FE-MODEL-V1.1  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | 模型管理 |
| 前端目录 | `apps/console-platform/frontend/src/modules/model-management/` |
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
| v1.1 | 2026-09-18 | fluxion-harness | 对齐 V1.4 决策（docs/17）：交互基线升级 V1.4、补启用/停用与删除操作、`protocol=OPENAI` 创建固定编辑只读、列表分页与筛选、删除冲突 `COMMON_CONFLICT`（message_args）、补场景与 Spec Matrix 落点 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 模型管理 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 严格按最新交互稿实现模型页并完全移除“默认模型”。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | 模型列表 | 分页/筛选、多选、批量测试、内部标识/Base URL/模型 ID/协议/启用状态/测试状态/revision。 | P0 | 需求描述 |
| FEAT-FE-02 | 新增/编辑 | Modal Form；协议固定 OpenAI 且编辑只读；API Key 留空保持。 | P0 | 需求描述 |
| FEAT-FE-03 | 模型详情 | SideSheet 基本信息，无默认模型；提供启用/停用与删除操作。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 列表/分页/筛选/多选/详情/新增/编辑/启用停用/删除/批量测试结果。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力；不展示“默认模型”。 |
| 技术债 | 无 |

### 2.4 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-01 | E2E | Browser→models API | 选择2个模型批量测试 | 结果 Modal 2 条且列表刷新 |
| S-FE-02 | FEAT-FE-03 | E2E | Browser→detail API | 点击模型名称 | 详情无默认模型字段；编辑按钮和 X 同行 |
| S-FE-03 | FEAT-FE-01 | E2E | Browser→update/delete API | 列表切换启用状态；删除无引用模型 | 状态列即时更新；删除后列表不再出现 |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-02 | integration | Semi Form | Base URL 非 http/https | 字段校验阻止提交 |
| E-FE-02 | FEAT-FE-02 | E2E | API→Modal | revision 冲突 | 保留表单并显示本地化冲突 |
| E-FE-03 | FEAT-FE-03 | integration | API→COMMON_CONFLICT | 删除被 Agent 引用的模型 | Popconfirm 后 Toast 本地化 msg（含 agent_count），列表不变 |
| E-FE-04 | FEAT-FE-02 | unit | ModelFormModal | 编辑态查看协议字段 | 协议只读，无法修改 |

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
| 模型管理 | `/models` | ConsoleShell | 列表 + 详情 SideSheet |

### 3.3 组件设计

```text
<Page>
├─ <ModuleToolbar/>
├─ <RemoteTable/>
└─ <DetailSideSheet/>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `ModelPage` | 容器 | 模块内 | 列表/筛选/选择/测试 |
| CMP-02 | `ModelFormModal` | 容器 | 模块内 | 新增/编辑 Form；协议只读 |
| CMP-03 | `ModelTestResultModal` | 展示 | 模块内 | 逐模型测试结果 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表左上 | 新增模型 | `Button` | primary | 打开新增 Modal（协议固定 OpenAI） | `POST /api/v1/models` | 否 |
| 列表左上 | 批量测试（N） | `Button` | secondary | 测试已勾选模型，0 时 disabled | `POST /api/v1/models/batch-test` | 否 |
| 列表右上 | 搜索/筛选 | `Input + Select` | secondary | 关键词/启用状态/测试状态查询并回第1页 | `GET /api/v1/models` | 否 |
| 列表右上 | 刷新 | `Button` | secondary | 刷新列表 | `GET /api/v1/models` | 否 |
| 列表操作列 | 启用/停用 | `Switch` / `Button` | secondary | 切换 `enabled`（带 expected_revision） | `PUT /api/v1/models/{id}` | 否 |
| 列表操作列 | 删除 | `Popconfirm + Button` | danger | 删除无引用模型 | `DELETE /api/v1/models/{id}` | 是 |
| 列表操作列 | 编辑 | `Button` | secondary | 打开编辑 Modal | `PUT /api/v1/models/{id}` | 否 |
| 详情 Header | 编辑 | `Button` | primary | 打开编辑 Modal | `PUT /api/v1/models/{id}` | 否 |
| 详情 Header | 删除 | `Popconfirm + Button` | danger | 删除无引用模型 | `DELETE /api/v1/models/{id}` | 是 |
| 表单 | 保存 | `Button` | primary | 校验并创建/更新 | `POST/PUT /api/v1/models` | 否 |

统一规则：主创建/保存使用 `Button theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同一 Header 行靠右；Tab 内关系操作完成即生效，不需要“保存整个对象”。删除冲突（`COMMON_CONFLICT`）时展示 message_args 中的 `agent_count`。

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
 -> modules/model-management/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listModels(params)` | `GET /api/v1/models`（page/page_size/keyword/enabled/last_test_status） | useModelList |
| `saveModel(input)` | `POST /api/v1/models` | ModelFormModal |
| `updateModel(id, input)` | `PUT /api/v1/models/{id}`（含 expected_revision） | ModelFormModal / 启停操作 |
| `deleteModel(id)` | `DELETE /api/v1/models/{id}` | ModelPage / 详情 |
| `batchTestModels(ids)` | `POST /api/v1/models/batch-test` | ModelPage |

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
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1、编辑态误改协议、删除引用冲突未展示原因；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段、协议只读、冲突展示 message_args。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.5 / §3.6 | S-FE-01, E-FE-03（verifier: project-owner） | applied |
| `harness-platform#RULE-ui-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.2 / §3.3 CMP-01 | S-FE-01, S-FE-02（verifier: project-owner） | applied |
| `harness-platform#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3 CMP-01 / §3.4 | S-FE-02（verifier: project-owner） | applied |
| `harness-platform#RULE-model-001` | required | ModelDefinition 无 is_default；Agent 显式选择 enabled model_id。 | §2.2 / §2.3 / §3.3 CMP-02 | S-FE-02, E-FE-04（verifier: project-owner 确认无默认模型字段） | applied |
| `harness-platform#RULE-front-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.5 / §3.6 | S-FE-01~S-FE-03, E-FE-01~E-FE-04（verifier: project-owner） | applied |
| `harness-platform#RULE-test-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §2.4 / §3.5 | S-FE-01~S-FE-03, E-FE-02（verifier: project-owner 确认真实浏览器+API） | applied |
