# 概览与运营入口 前端模块需求与设计简报

> **文档编号**: FE-OVERVIEW-V1.1  
> **文档版本**: v1.1  
> **创建日期**: 2026-09-17  
> **文档状态**: 设计评审中  
> **模板**: design-frontend.md

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 开发负责人 | 待定 | 技术方案、代码实现 |
| 设计/交互 | 待定 | 视觉与交互稿 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-09-17 | — | 前端需求与设计初稿 |
| v1.1 | 2026-09-18 | — | 对齐 V1.4 决策（docs/17）：明确 4 个 KPI、固定 10 项菜单引用、补组件契约与状态划分/异常场景、交互基线更新为 V1.4 |

**模块信息**

| 项目 | 内容 |
|---|---|
| 模块 | 概览与运营入口 |
| 前端目录 | `apps/console-platform/frontend/src/modules/overview-dashboard/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `智能服务交付平台-V1.4-交互稿.html` |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 概览与运营入口 |
| 需求类型 | 页面/组件/交互实现 |
| 业务背景 | Builder/Admin 需要一个跨模块的运营入口，但不能在前端循环调用各实体接口。 |
| 核心目标 | 保留交互稿概览的信息层级，一次加载 4 个 KPI 与最近任务/调度，作为业务运营入口而不是运维监控。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | KPI 卡片 | 4 个 KPI：启用 Agent、启用 Skill、后台执行中（非终态 Task）、启用定时任务。 | P0 | 需求描述 |
| FEAT-FE-02 | 运行关系说明 | 静态说明 IM→Agent→Runtime→ExecutionRouter→Worker/DB/Gateway。 | P0 | 需求描述 |
| FEAT-FE-03 | 运营快捷入口 | 最近任务、下一批定时、查看全部。 | P0 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 在范围内（In Scope） | 首页 KPI ×4、架构关系静态说明、最近 Task/Schedule、导航链接。 |
| 非范围（Out of Scope） | 不改领域语义；组件不裸用 axios/fetch；不建概览快照表；不展示中间件/Pod 健康；不增加交互稿未确认的重型能力；不新增菜单——Console 菜单固定 10 项（概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计，与 docs/00 §0.7 / docs/01 一致）。 |
| 技术债 | 无 |

### 2.4 验收条件

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-01 | P0 | E2E | Browser→overview API | 打开首页 | 4 KPI + 最近任务/调度一次加载，无前端 N+1 |
| S-FE-02 | FEAT-FE-03 | P0 | E2E | Browser Router | 点击查看全部 | 进入 tasks/schedules 且菜单选中正确 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-01 | integration | overview API→ErrorState | 聚合接口失败 | 整体 ErrorState + 重试，不伪造 0 |
| E-FE-02 | FEAT-FE-02/03 | integration | Router→目标页 | 跳转目标 ID 已失效/无权限 | 目标页 ErrorState 或回退列表，不白屏 |

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
| 概览 | `/` | ConsoleShell | Console 首页；固定 10 项菜单中的第一项 |

### 3.3 组件设计

```text
<OverviewPage>                 # 容器：一次加载 overview data
├─ <KpiCards>                  # 展示：4 个指标卡
├─ <RuntimeRelationCard>       # 展示：静态运行关系
├─ <RecentTaskList>            # 展示：最近任务 + 查看全部
└─ <NextScheduleList>          # 展示：下一批定时 + 查看全部
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `OverviewPage` | 容器 | 模块内 | 一次加载 overview data |
| CMP-02 | `KpiCards` | 展示 | 模块内 | 4 个指标卡 |
| CMP-03 | `RuntimeRelationCard` | 展示 | 模块内 | 静态运行关系 |
| CMP-04 | `RecentTaskList` | 展示 | 模块内 | 最近任务 |
| CMP-05 | `NextScheduleList` | 展示 | 模块内 | 下一批定时 |

**必须复用公共组件**：`ConsoleShell / EntityLink / StatusTag / DateTimeText / EmptyState / ErrorState / LocaleSwitch`。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| KPI 卡片 | 启用 Agent/Skill | `Typography.Text link` | secondary | navigate('/agents' / '/skills') | `-` | 否 |
| KPI 卡片 | 后台执行中/启用定时任务 | `Typography.Text link` | secondary | navigate('/tasks' / '/schedules') | `-` | 否 |
| 最近后台任务 | Task ID 链接 | `Typography.Text link` | secondary | 打开 Task 详情/进入 tasks | `GET /api/v1/tasks/{id}` | 否 |
| 最近后台任务 | 查看全部 → | `Button type=tertiary` | secondary | navigate('/tasks') | `-` | 否 |
| 下一批定时 | Schedule 名称 | `Typography.Text link` | secondary | 打开 Schedule 详情/进入 schedules | `GET /api/v1/schedules/{id}` | 否 |
| 下一批定时 | 查看全部 → | `Button type=tertiary` | secondary | navigate('/schedules') | `-` | 否 |

统一规则：主创建/保存使用 `Button theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同一 Header 行靠右；Tab 内关系操作完成即生效，不需要“保存整个对象”。

### 3.4 组件接口契约

```ts
export interface OverviewKpis {
  enabledAgents: number;    // 启用 Agent
  enabledSkills: number;    // 启用 Skill
  activeTasks: number;      // 后台执行中
  activeSchedules: number;  // 启用定时任务
}

export interface RecentTaskItem {
  taskId: string;
  intentKey: string;
  agentId: string;
  agentName: string;
  actorUserId: string;
  actorUserName: string;
  status: string;          // 任务状态
  triggerType: 'IMMEDIATE' | 'SCHEDULED';
  deliveryStatus: 'PENDING' | 'SENT' | 'FAILED' | 'NONE';
  startedAt?: string;      // YYYY-MM-DD HH:mm:ss
  finishedAt?: string;
  deadlineAt?: string;
  createTime: string;
}

export interface NextScheduleItem {
  scheduleId: string;
  name: string;
  agentId: string;
  agentName: string;
  actorUserId: string;
  actorUserName: string;
  intentKey: string;
  status: string;          // 调度状态（列表仅 ACTIVE）
  nextFireAt: string;      // 下次触发时间
  lastFireAt?: string;     // 最近触发时间
  timezone: string;
}

export interface OverviewData {
  kpis: OverviewKpis;
  recentTasks: RecentTaskItem[];
  nextSchedules: NextScheduleItem[];
}

export interface KpiCardsProps { kpis: OverviewKpis; loading: boolean; }
export interface RecentTaskListProps {
  items: RecentTaskItem[];
  loading: boolean;
  onOpenTask(taskId: string): void;
  onViewAll(): void;
}
export interface NextScheduleListProps {
  items: NextScheduleItem[];
  loading: boolean;
  onOpenSchedule(scheduleId: string): void;
  onViewAll(): void;
}
export interface RuntimeRelationCardProps { /* 纯静态，无 props */ }
```

展示组件 props-in/events-out；API、路由、提交状态由 Page/Hook 管理。

### 3.5 状态与数据流

**状态划分**

| 状态 | 作用域（local / shared store） | 形状（shape） | 读写方 |
|---|---|---|---|
| 概览数据 | hook local | `{data, loading, error}` | `useOverview` → `OverviewPage` |
| 跳转 | 路由 | `navigate(path)` | 各列表/卡片回调 |

```text
User Action
 -> Page/Hook
 -> modules/overview-dashboard/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `getOverview()` | `GET /api/v1/overview` | useOverview |

Service 层负责后端 snake_case → 前端 camelCase 的字段映射（如 `enabled_agents`→`enabledAgents`、`next_fire_at`→`nextFireAt`），组件不直接消费原始 Envelope。

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 概览 KPI | Skeleton 卡片 | - | 整页 ErrorState + 重试 | 4 个 KPI 数值 |
| 最近任务 | List loading/Skeleton | Empty + 查看全部 | 区块 ErrorState + 重试 | 列表 + 查看全部 |
| 下一批定时 | List loading/Skeleton | Empty + 查看全部 | 区块 ErrorState + 重试 | 列表 + 查看全部 |

### 3.7 样式方案

- 保留交互稿信息架构和字段顺序，可用 Semi Token 重新美化；
- **字段布局排版以交互稿为准**：新增/编辑表单使用双列栅格（`.form-grid`，同一栅格内控件 100% 同宽、按交互稿顺序成对排列，动态/配置类字段配分区标题与说明），详情基本信息用 `DetailGrid` 双列、关系表列名与操作列对齐交互稿；禁止宽窄混排与裸 schema key。
- 列表页不增加重复标题/说明块；
- Toolbar 左主操作、右搜索筛选；
- 主展示字段点击打开详情；操作列只放真实动作；
- 详情双列基础信息，<900px 降单列；
- 时间统一 `YYYY-MM-DD HH:mm:ss`；
- 禁止散落魔法颜色/间距。

### 3.8 可访问性与兼容性

Semi Form required/rules；Modal/SideSheet 焦点管理；图标按钮 aria-label；Chrome/Edge 企业当前版本为主，Safari 做开发兼容验证。

## 4. 风险与依赖

- 前置：01-platform-foundation, 05-skill-management, 07-agent-management, 09-task-schedule；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.3/§3.5 | S-FE-01 + verifier | applied |
| `harness-platform#RULE-ui-001` | required | Console 使用 React + Semi；菜单固定 10 项；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.2/§3.3 | S-FE-02 + verifier | applied |
| `harness-platform#RULE-time-001` | required | Console 时间统一 YYYY-MM-DD HH:mm:ss。 | §3.4/§3.6 | S-FE-01 + verifier | applied |
| `harness-platform#RULE-front-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.5 | S-FE-01 + verifier | applied |
| `harness-platform#RULE-test-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §2.4/§3.5 | S-FE-01, E-FE-01 + verifier | applied |
