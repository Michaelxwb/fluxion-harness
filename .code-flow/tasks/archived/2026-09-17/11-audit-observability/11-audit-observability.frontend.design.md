# 运行审计与可观测 前端模块需求与设计简报

> **文档编号**: FE-AUDIT-V1.5  
> **文档版本**: v1.5  
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
| v1.1 | 2026-09-18 | — | 对齐 V1.4 决策（docs/17）：字段名对齐 docs/15、补执行结果筛选、组件契约与状态分区、交互基线更新为 V1.4 |
| v1.2 | 2026-09-25 | Claude | 对齐现行 required 规则文本：secret 语义由"只存 SecretRef"改为"密钥明文存于各 Owner 表（无 `secret_ref`/SecretProvider），不进审计/日志/Snapshot/LLM Prompt/API 响应（对外以 `*_configured` 表达）"；矩阵 ref 由 legacy `harness-platform#` 校正为 `harness-secret#`。 |
| v1.3 | 2026-09-25 | Claude | 承接 required `harness-api#RULE-api-002`（后端新增 FEAT-04 审计导出与 API-05/API-06）：新增 FEAT-FE-03 导出按钮、`useAuditExport` 与 service 方法（同一次用户提交复用同一 `Idempotency-Key`，异指纹走 catalog→i18n 文案）、场景 S-08 / E-08 / E-09；矩阵 ref 统一为现行分域 spec id。 |
| v1.4 | 2026-09-25 | Claude | 场景 ID 归一：前端场景由 `S-FE-01..03` / `E-FE-01..04` 改为同一数字序列 `S-06..S-08` / `E-06..E-09`（验收工具链的场景行匹配为 `[SEB]-\d+`，仅数字序号可进入 Acceptance Coverage 与 manifest；与 09-task-schedule 的 `S-2xx`/`E-2xx` 惯例一致）。场景内容、层级与真实边界不变。 |
| v1.5 | 2026-09-25 | Claude | 实施口径对齐（不改需求）：布局组件实名（`ConsoleShell` → `layout/AppLayout.tsx`；`DetailTabs` 由共享 `DetailSideSheet` 内建 Tabs 承载）；补「Agent」筛选需后端 `agent_id` 参数、`resource_type` 值域开放须覆盖实际取值 + i18n 兜底、以及刷新失败保持已加载行并给非破坏性提示（首次加载失败才用 `ErrorState`）。 |

**模块信息**

| 项目 | 内容 |
|---|---|
| 模块 | 运行审计与可观测 |
| 前端目录 | `apps/console-platform/frontend/src/modules/audit-observability/` |
| 公共组件 | `src/components/common/`，由 01-platform-foundation 提供 |
| 交互基线 | 最新 `智能服务交付平台-V1.4-交互稿.html` |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 运行审计与可观测 |
| 需求类型 | 页面/组件/交互实现 |
| 业务背景 | 运行审计需要跨 config/tool/egress/model 统一检索，且字段口径必须与 docs/15 一致。 |
| 核心目标 | 提供轻量可检索运行审计，不把运维监控大盘搬进业务 Console。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | 审计列表 | 时间/审计类型（`resource_type`）/操作用户（`actor_user_id`）/Agent/操作目标（`resource_id`）/动作（`action`）/执行结果（`result_status`）/Trace ID（`trace_id`）。 | P0 | 需求描述 |
| FEAT-FE-02 | 审计详情 | 只读 SideSheet + 关联链接（Run/Task/Trace）。 | P0 | 需求描述 |
| FEAT-FE-03 | 审计导出 | 工具栏"导出"按当前筛选条件创建导出任务（同一业务提交复用同一 `Idempotency-Key`，重试不重复建任务），轮询状态并在完成后下载。 | P1 | 需求描述 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 审计列表、筛选（含执行结果）、详情，只读。 |
| Out of Scope | 不改领域语义；组件不裸用 axios/fetch；不展示中间件/Pod 健康；不增加交互稿未确认的重型能力。 |
| 技术债 | 无 |

### 2.4 验收条件

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|---|
| S-06 | FEAT-FE-01 | P0 | E2E | Browser→audits aggregate API | Trace ID 搜索 | 仅显示相关记录且字段与 docs/15 口径一致 |
| S-07 | FEAT-FE-02 | P0 | E2E | Browser→detail API | 点击 Trace ID/主展示字段 | 只读详情，无操作按钮 |
| S-08 | FEAT-FE-03 | P1 | E2E | Browser→export create API→Console/PostgreSQL（幂等表与导出任务） | 按筛选条件点击"导出"，同一 `Idempotency-Key` 重试 | 两次提交返回同一导出任务（不重复创建），轮询至完成后可下载；按钮在提交中禁用并展示进度 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-06 | FEAT-FE-01 | integration | API→ErrorState | 查询失败 | 保留筛选并可重试 |
| E-07 | FEAT-FE-02 | integration | API→SideSheet | 审计已归档/不可读 | SideSheet 内 ErrorState，不伪造关联数据 |
| E-08 | FEAT-FE-03 | integration | API→idempotency | 同一 `Idempotency-Key` 但筛选条件不同 | 展示 `IDEMPOTENCY_MISMATCH` 文案（i18n key，来自 catalog 映射），保留筛选且不重复创建任务 |
| E-09 | FEAT-FE-03 | integration | API→export status | 导出任务 `FAILED` | 展示 `error_code` 对应文案并提供重试入口（复用新 `Idempotency-Key`），不展示未完成产物 |

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
| 运行审计 | `/audits` | ConsoleShell（实施组件：`layout/AppLayout.tsx`） | 聚合列表 + 只读 SideSheet |

### 3.3 组件设计

```text
<AuditPage>                         # 容器：筛选/分页/详情状态
├─ <ModuleToolbar>                  # 公共组件：搜索/筛选/重置/刷新
├─ <AuditTable>                     # 展示：字段列 + 主展示字段入口
└─ <AuditDetailSideSheet>           # 容器：按 audit_type + audit_id 拉详情
   └─ <共享 DetailSideSheet 内建 Tabs>（仓库无独立 DetailTabs 组件）                  # 公共组件：基础信息/关联
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `AuditPage` | 容器 | 模块内 | 筛选/分页/详情状态 |
| CMP-02 | `AuditTable` | 展示 | 模块内 | 标准字段列，主展示字段打开详情 |
| CMP-03 | `AuditDetailSideSheet` | 容器 | 模块内 | 按 `audit_type` 取详情，展示 trace/run/task 关联 |
| CMP-04 | `AuditFilterBar` | 展示 | 模块内 | 时间/类型/用户/Agent/目标/动作/结果/Trace 筛选 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / EntityLink / DetailSideSheet / DetailTabs / StatusTag / DateTimeText / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。
**实施口径对齐（2026-09-25）**：

- **布局组件实名**：`ConsoleShell` 在仓库中的实际组件是 `layout/AppLayout.tsx`（路由层嵌套）；`DetailTabs` 无独立组件，由共享 `DetailSideSheet` 内建的 Semi `Tabs`（子 `Tabs.TabPane`）承载。
- **「Agent」筛选**：聚合投影的运行类记录带 `agent_id`/`agent_name`（config 类为空），该筛选项需后端提供 `agent_id` 查询参数；未提供前不得用「审计类型（`resource_type`）」冒充。
- **`resource_type` 取值域是开放的**（config 侧如 `AGENT/SKILL/MCP/MODEL/PROJECT_PLATFORM/USER/GRANT`，运行侧各异且大小写不一）：筛选项须覆盖实际取值，详情「资源类型」行须有 i18n 兜底（未知值原样展示、不得空白）。
- **查询失败呈现**：首次加载失败 → `ErrorState` + 保留筛选 + 可重试；**刷新失败**（已有行）→ 保留已加载行并给出非破坏性错误提示，不整页替换为 `ErrorState`。


#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| 列表右上 | 搜索/筛选 | `Input + Select + DatePicker` | secondary | 按 trace/type/result/time 查询 | `GET /api/v1/audits` | 否 |
| 列表右上 | 重置 | `Button` | secondary | 清空筛选 | `GET /api/v1/audits` | 否 |
| 列表右上 | 刷新 | `Button` | secondary | 刷新当前页 | `GET /api/v1/audits` | 否 |
| Trace ID/主展示字段 | 打开详情 | `Typography.Text link` | secondary | 打开只读 SideSheet | `GET /api/v1/audits/{id}?audit_type=` | 否 |
| 列表左主操作 | 导出 | `Button theme="solid" type="primary"` | primary | 按当前筛选创建导出任务（同一次业务提交复用同一 `Idempotency-Key`；提交中禁用） | `POST /api/v1/audits/exports`、`GET /api/v1/audits/exports/{id}` | 否 |

统一规则：主创建/保存使用 `Button theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同一 Header 行靠右；Tab 内关系操作完成即生效，不需要“保存整个对象”。

### 3.4 组件接口契约

```ts
export interface AuditListQuery {
  auditType?: 'CONFIG' | 'TOOL' | 'EGRESS' | 'MODEL';
  resourceType?: string;
  resourceId?: string;
  actorUserId?: string;
  action?: string;
  resultStatus?: string;
  traceId?: string;
  startTime?: string;
  endTime?: string;
  page: number;      // >=1
  pageSize: number;  // <=100，默认 20
}

export interface AuditListItem {
  auditId: string;
  auditType: 'CONFIG' | 'TOOL' | 'EGRESS' | 'MODEL';
  resourceType: string;
  resourceId: string;
  actorUserId: string;
  actorName?: string;
  agentId?: string;
  agentName?: string;
  action: string;
  resultStatus: string;   // SUCCESS | FAILED | <领域错误码>
  traceId: string;
  target: string;
  occurredAt: string;     // YYYY-MM-DD HH:mm:ss
  latencyMs?: number;
}

export interface AuditTableProps {
  items: AuditListItem[];
  loading: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number, pageSize: number): void;
  onOpenDetail(item: AuditListItem): void;
}

export interface AuditDetailSideSheetProps {
  visible: boolean;
  auditType: AuditListItem['auditType'];
  auditId: string | null;
  onClose(): void;
}

export interface AuditFilterBarProps {
  value: AuditListQuery;
  onChange(patch: Partial<AuditListQuery>): void;
  onSearch(): void;
  onReset(): void;
}
```

展示组件 props-in/events-out；API、路由、提交状态由 Page/Hook 管理。详情必须同时传 `audit_type`，因为 4 张来源表的 UUID 不互通。

### 3.5 状态与数据流

**状态划分**

| 状态 | 作用域（local / shared store） | 形状（shape） | 读写方 |
|---|---|---|---|
| 筛选条件 | local（page） | `AuditListQuery` | `AuditFilterBar` 上抛 / `AuditPage` 持有 |
| 列表数据 | hook local | `{items,page,pageSize,total,loading,error}` | `useAuditList` → `AuditTable` |
| 详情选择 | local（page） | `{auditType, auditId（可空）}` | `AuditPage` ↔ `AuditDetailSideSheet` |

```text
User Action
 -> Page/Hook
 -> modules/audit-observability/services/*.ts
 -> shared apiClient(X-Locale/X-Request-Id)
 -> Backend Envelope
 -> Hook State
 -> Semi Components
```

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `listAudits(params)` | `GET /api/v1/audits` | useAuditList |
| `getAudit(auditType, id)` | `GET /api/v1/audits/{id}?audit_type=` | useAuditDetail |
| `createExport(req, idempotencyKey)` | `POST /api/v1/audits/exports`（Header `Idempotency-Key` 必填） | useAuditExport |
| `getExport(exportId)` | `GET /api/v1/audits/exports/{export_id}` | useAuditExport（轮询） |
| `downloadExport(exportId)` | `GET /api/v1/audits/exports/{export_id}/download` | useAuditExport |

**导出幂等约定（RULE-api-002）**：`Idempotency-Key` 由 service 层在**一次用户提交**内生成并复用——提交重试（网络超时/双击）必须复用同一 key；用户显式发起新导出时才生成新 key。`IDEMPOTENCY_MISMATCH` 与导出失败码均走 catalog → i18n key 映射，组件不硬编码文案。

```ts
export interface AuditExportCreateRequest {
  exportFormat: 'CSV' | 'JSON';
  filters: Omit<AuditListQuery, 'page' | 'pageSize'>; // 与列表筛选同源
}

export interface AuditExportJob {
  exportId: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  rowCount?: number;
  errorCode?: string;
  createTime: string; // YYYY-MM-DD HH:mm:ss
  updateTime: string;
}
```

Service 层负责后端 snake_case → 前端 camelCase 的字段映射（如 `actor_user_id`→`actorUserId`、`result_status`→`resultStatus`），组件不直接消费原始 Envelope。

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 审计列表 | Table loading/Skeleton | Empty + 清筛选 | ErrorState + 重试（保留筛选） | Table + 右下 Pagination |
| 审计详情 | SideSheet Spin | Tab Empty | SideSheet 内 ErrorState | Descriptions/List/Table（只读） |
| 关联缺失 | - | “关联 Run/Task 不可读”提示 | - | 详情其他字段仍展示 |

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

- 前置：01-platform-foundation, 08-runtime-execution, 09-task-schedule；
- 风险：硬编码中文、重复造公共 SideSheet/Toolbar、前端 N+1；
- 应对：i18n key 检查、公共组件依赖、列表 API 聚合字段。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-i18n#RULE-i18n-001` | required | 后端错误和前端页面支持 zh-CN/en-US；新增业务仅增加配置。 | §3.3/§3.5 | S-06 + verifier | applied |
| `harness-ui#RULE-ui-001` | required | Console 使用 React + Semi；左上操作、右上搜索筛选、右下分页；主展示字段打开详情。 | §3.3/§3.6 | S-06 + verifier | applied |
| `harness-ui-detail#RULE-ui-detail-001` | required | 详情 SideSheet 标题/副标题左侧，操作按钮与关闭 X 同行靠右，Tabs 在其下。 | §3.3/§3.4 | S-07 + verifier | applied |
| `harness-time#RULE-time-001` | required | Console 时间统一 YYYY-MM-DD HH:mm:ss。 | §3.4/§3.6 | S-06 + verifier | applied |
| `harness-secret#RULE-secret-001` | required | 密钥明文存于各 Owner 表（无 secret_ref/SecretProvider）；Secret Value 不进审计/日志/Snapshot/LLM Prompt/API 响应（对外以 `*_configured` 表达）。 | §3.4/§3.6 | E-07 + verifier | applied |
| `harness-frontend#RULE-front-001` | required | 前端 API 只经 services/；组件不裸用 axios/fetch；文案只用 i18n key。 | §3.5 | S-06 + verifier | applied |
| `harness-test#RULE-test-001` | required | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E，列出不得 mock 的真实边界。 | §2.4/§3.5 | S-06, S-07 + verifier | applied |
