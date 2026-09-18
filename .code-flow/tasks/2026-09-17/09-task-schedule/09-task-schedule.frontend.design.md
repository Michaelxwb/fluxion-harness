# 后台任务、调度与可靠执行 前端模块需求与设计简报

> **文档编号**: FE-TASK-V1.1  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 开发负责人 | muad-agent-worker | 技术方案、代码实现 |
| 设计/交互 | 待定 | 视觉与交互稿 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-09-17 | muad-agent-worker | 初始设计 |
| v1.1 | 2026-09-18 | muad-agent-worker | 对齐 V1.4 决策（docs/17）：交互基线升级 V1.4；任务列表/详情补任务截止时间、失败原因；定时任务筛选补「已完成」；补全 Service 方法与场景 |

### 1.3 模块信息

| 项目 | 内容 |
|---|---|
| 模块 | 后台任务、调度与可靠执行 |
| 前端目录 | `apps/console-platform/frontend/src/modules/task-schedule/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `智能服务交付平台-V1.4-交互稿.html` |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 后台任务、调度与可靠执行 |
| 需求类型 | 页面/组件/交互实现 |
| 核心目标 | 把 Task/Schedule 作为运营观察和管理对象，而不是让 Console 成为任务编排器。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | 后台任务列表/详情 | 任务状态、进度、任务截止时间、投递状态、失败原因、Timeline、子任务。 | P0 | 需求描述 |
| FEAT-FE-02 | 任务取消 | 仅非终态显示取消操作。 | P0 | 需求描述 |
| FEAT-FE-03 | 定时任务列表/详情 | 调度规则、下次/最近触发、状态筛选（含「已完成」）、按 schedule_id 查询历史 Task。 | P0 | 需求描述 |
| FEAT-FE-04 | Schedule 管理 | 暂停/恢复/删除。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 后台任务页/详情/取消；定时任务页/详情/历史/暂停/恢复/删除/帮助说明。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不增加交互稿未确认的重型能力 |
| 技术债 | 无 |

### 2.4 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-03 | E2E | Browser→schedules API→tasks API | 打开 Schedule 详情 | 历史只显示该 schedule_id 的 Task |
| S-FE-02 | FEAT-FE-02 | E2E | Browser→cancel API→Worker state | 运行中任务取消并确认 | 最终 CANCELLED，取消按钮消失 |
| S-FE-03 | FEAT-FE-01 | E2E | Browser→tasks API→Task 详情 | 按任务状态/截止时间筛选，打开失败 Task | 列表展示任务截止时间与失败原因；筛选结果与 API 一致 |
| S-FE-04 | FEAT-FE-03 | E2E | Browser→schedules API | 定时任务状态筛选「已完成」 | 只显示 COMPLETED 的 Schedule |

异常：

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-04 | E2E | pause API→UI | 暂停失败 | 保持 ACTIVE，不做错误乐观更新 |
| E-FE-02 | FEAT-FE-01 | integration | Task detail API | Task 不存在 | SideSheet ErrorState 可关闭返回列表 |
| E-FE-03 | FEAT-FE-01 | integration | tasks API→UI | 失败/超时 Task（`TASK_DEADLINE_EXCEEDED`） | 任务状态 FAILED，失败原因展示 error_code/error_message，截止时间可见 |

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
| 后台任务 | `/tasks` | ConsoleShell | TaskExecution 列表 + 详情 SideSheet；筛选任务状态/触发方式/截止时间 |
| 定时任务 | `/schedules` | ConsoleShell | TaskSchedule 列表 + 详情 SideSheet；筛选调度状态（含「已完成」） |

### 3.3 组件设计

```text
<Page>
├─ <ModuleToolbar/>
├─ <RemoteTable/>
└─ <DetailSideSheet/>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `TaskPage` | 容器 | 模块内 | 任务过滤（状态/触发方式/截止时间）/分页/详情/取消；展示任务截止时间、失败原因 |
| CMP-02 | `TaskTimeline` | 展示 | 模块内 | TaskEvent Timeline |
| CMP-03 | `SchedulePage` | 容器 | 模块内 | Schedule 查询（含「已完成」筛选）/动作 |
| CMP-04 | `ScheduleHistoryTable` | 展示 | 模块内 | 按 schedule_id 查询 Task 历史 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 后台任务列表 | 后台任务如何产生 | `Button` | secondary | 打开只读帮助 Modal | `-` | 否 |
| 任务详情 Header | 取消任务 | `Popconfirm + Button` | danger | 仅 QUEUED/RUNNING/WAITING 可见 | `POST /api/v1/tasks/{id}/cancel` | 是 |
| 定时任务列表 | 如何创建 | `Button` | secondary | 打开通过 Agent 创建定时任务的聊天示例 | `-` | 否 |
| Schedule 详情 Header | 暂停 | `Button` | secondary | ACTIVE→PAUSED | `PUT /api/v1/schedules/{id}/pause` | 否 |
| Schedule 详情 Header | 恢复 | `Button` | secondary | PAUSED→ACTIVE | `PUT /api/v1/schedules/{id}/resume` | 否 |
| Schedule 详情 Header | 删除 | `Popconfirm + Button` | danger | 软删除 Schedule | `DELETE /api/v1/schedules/{id}` | 是 |
| Schedule 历史 | 任务 ID 链接 | `Typography.Text link` | secondary | 打开 Task 详情 | `GET /api/v1/tasks/{task_id}` | 否 |

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
 -> modules/task-schedule/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listTasks(params)` | `GET /api/v1/tasks` | useTaskList |
| `getTask(id)` | `GET /api/v1/tasks/{id}` | TaskDetail |
| `cancelTask(id)` | `POST /api/v1/tasks/{id}/cancel` | TaskDetail |
| `listSchedules(params)` | `GET /api/v1/schedules` | useScheduleList |
| `getSchedule(id)` | `GET /api/v1/schedules/{id}` | ScheduleDetail |
| `pauseSchedule(id)` | `PUT /api/v1/schedules/{id}/pause` | ScheduleDetail |
| `resumeSchedule(id)` | `PUT /api/v1/schedules/{id}/resume` | ScheduleDetail |
| `deleteSchedule(id)` | `DELETE /api/v1/schedules/{id}` | ScheduleDetail |
| `listScheduleTasks(id)` | `GET /api/v1/tasks?schedule_id={id}` | ScheduleHistoryTable |

所有 Service 经共享 `apiClient`（自动带 `X-Locale/X-Request-Id`）；列表参数含 `status/trigger_type/start_time/end_time/page/page_size`，`page_size<=100`。

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

- 前置：01-platform-foundation, 05-skill-management, 07-agent-management, 08-runtime-execution；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.1 / §3.5 | S-FE-03 + verifier | applied |
| `harness-platform#RULE-ui-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.2 / §3.3 / §3.7 | S-FE-03 + verifier | applied |
| `harness-platform#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3 / §3.4 | S-FE-01 + verifier | applied |
| `harness-platform#RULE-time-001` | required | Console 时间统一 YYYY-MM-DD HH:mm:ss；任务截止时间按此时区展示。 | §3.3 / §3.7 | S-FE-03 + verifier | applied |
| `harness-platform#RULE-worker-001` | required | PG 是 Task/Schedule/lease 权威源；Redis 仅 hint；TaskType V1=SKILL/BATCH。 | §2.2 / §3.5 | S-FE-02 + verifier | applied |
| `harness-platform#RULE-front-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.3 / §3.5 | S-FE-03 + verifier | applied |
| `harness-platform#RULE-test-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §2.4 | S-FE-01, S-FE-03 + verifier | applied |
