---
{"design_template": "frontend"}
---

# 运行审计列表操作粒度 前端设计

> **文档编号**: FE-AUDIT-OPVIEW-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-17
> **文档状态**: 设计评审中
> **设计输入**: `.code-flow/tasks/2026-11-17/audit-operation-view/audit-operation-view.prd.md`、`audit-operation-view.backend.design.md`

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线。
- **设计评审**: 第 3 章 → 通过后锁定设计基线。

**ID 体系**: US、FEAT、CMP、NFR；场景使用 S-/E-/B-。

**本版关键结论（先读）**:

1. **不新增页面与路由**：改动全部落在既有 `/audits` 页面（`audit-observability` 模块）。
2. **视图是筛选的产物**：`auditType ∈ {TOOL, MODEL}` → 明细视图，其余 → 操作视图；前端始终向后端显式传 `view`，不依赖后端推断。
3. **Run 行新增用户提问摘要**（`summary`）；步骤计数与失败原因归运行详情的「运行概览」页签，列表不重复展示。
4. **两个入口分工**：对象列「运行 …」打开运行详情；Trace ID 下钻该 Trace 的全部审计记录（明细视图 + Trace 提示条）。事件行的 Trace ID 仍打开该条审计详情。
5. **失败只在 Run 失败时呈现**：重试次数单独以中性文案展示，不渲染成失败。
6. **明细视图必须有可见提示**：顶部提示条说明当前粒度，并提供「返回操作视图」出口。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | Console 运行审计（`apps/console-platform/frontend/src/modules/audit-observability`） |
| **需求类型** | 交互优化（列表粒度） |
| **业务背景** | 一次对话在列表占 6~10 行，全部指向同一个运行详情 |
| **核心目标** | 默认一次对话一行，失败可见，明细仍可查 |

### 2.2 功能方案

| 功能ID | 功能名称 | 前端实现要点 | 优先级 | 来源 |
|--------|---------|-------------|--------|------|
| FEAT-01 | 操作视图默认粒度 | 列表列与行渲染改造；`view=operation` | P0 | US-01, US-06 |
| FEAT-02 | 步骤统计随运行详情 | 运行概览页签展示模型/工具/出网/重试计数 | P0 | US-02 |
| FEAT-03 | 步骤明细视图 | 类型选工具/模型调用时切换视图 + 提示条 | P0 | US-03 |
| FEAT-04 | 运行详情入口 | Run 行首列与对象列打开运行详情 | P0 | US-04 |
| FEAT-05 | 导出不受影响 | 导出按钮与请求不变 | P0 | US-05 |

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `types.ts`、`services/auditService.ts`、`hooks/useAuditList.ts`、`components/AuditTable.tsx`、`components/AuditFilterBar.tsx`、`pages/AuditPage.tsx`、`auditLabels.ts`、i18n 词条。 |
| **非范围（Out of Scope）** | 审计详情侧栏结构（仅 Run 行不再进入）；运行详情侧栏；导出对话框；新增页面/路由。 |
| **有意妥协 / 技术债** | 无组件级单测基建，前端通过 `typecheck` + 构建 + 人工场景验收；接口契约由后端集成测试保证。 |

### 2.4 验收条件

| 场景ID | 前端设计影响 / 备注 |
|--------|--------------------|
| S-01 | 默认列表一次对话一行 |
| S-02 | 失败 Run 行：行首标记 + 结果标红；失败步骤在运行详情 |
| S-03 | 切换明细：提示条出现、类型下拉显示「工具调用」、表格列变化 |
| S-04 | 点运行 ID 打开运行详情，链路完整；点 Trace ID 下钻该 Trace 的记录 |
| S-05 | 配置变更与出网行样式/字段不变 |
| S-06 | 导出结果与视图无关 |
| E-01 | 列表请求失败：整页 ErrorState + 重试保留筛选 |
| E-02 | 运行详情步骤聚合降级：计数显示 `-`，不显示 0 |
| B-01 | 两个视图的分页页码与总数各自正确 |
| B-02 | 任务产生的出网记录作为出网操作行可见 |

#### 2.4.1 验证环境假设

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|------|------|----------------|---------|
| 容量 | 列表页常规数据量；至少 1 个含重试的 Run、1 个失败 Run | 造数脚本或手工执行 | 记录缺失项与 owner |
| 硬件 | 桌面 1440×900 与 400px 宽视口 | 人工检查 | 记录缺失项 |
| 依赖 | Console 前端可构建；Console 后端可用；运行详情接口可用 | `npm run build` + 真实栈 | 记录缺失项 |
| 测试预算 | `typecheck` + `vite build`；浏览器场景人工执行 | 人工核对任务文档 | 记录未覆盖风险 |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React + TypeScript | 18 / 5.7 | 既有 Console 栈 |
| 组件库 | Semi Design | 既有版本 | 列表、标签、下拉一致 |
| 数据请求 | 共享 axios 实例 + 模块 service | 既有 | RULE-front-001 |
| 文案 | i18next（`zh-CN` / `en-US`） | 既有 | RULE-i18n-001 |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| 运行审计 | `/audits` | 既有一级页 | 不改路由、不加页签；列表与提示条在同一面板内 |

### 3.3 组件设计

**组件树**

```
<AuditPage>                       # 容器：筛选、视图派生、分页、详情侧栏
├─ <AuditFilterBar>               # 展示：筛选控件（新增「运行」选项）
├─ <AuditViewNotice>              # 展示：明细视图提示条（新）
├─ <RemoteTable>                  # 既有：PaginationFooter
│   └─ columns from AuditTable    # 展示：列定义与行渲染
└─ <AuditDetailSideSheet>         # 既有：仅非 RUN 行使用
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | `AuditPage` | 容器 | 改造 | 从 `auditType` 派生 `view`；持有提示条显隐与返回动作 |
| CMP-02 | `AuditFilterBar` | 展示 | 改造 | 类型下拉新增「运行」；工具/模型标注为明细 |
| CMP-03 | `AuditViewNotice` | 展示 | 新建（`components/`） | 说明当前粒度 + 返回操作视图 |
| CMP-04 | `AuditTable`（列工厂） | 展示 | 改造 | RUN 行渲染、时间列层次、失败行标记、Trace 下钻入口 |
| CMP-05 | `auditActionLabel` / `auditActorLabel` / `auditTargetLabel` | 纯函数 | 改造 | RUN 类型与 Run 行文案 |

**分层纪律**：视图派生与提示条状态放页面容器；表格只接收已成型的数据与回调。

### 3.4 组件接口契约

```ts
// types.ts
export type AuditType = 'RUN' | 'CONFIG' | 'TOOL' | 'EGRESS' | 'MODEL';
export type AuditView = 'operation' | 'detail';

export interface AuditStepSummary {
  model: number;
  tool: number;
  egress: number;
  retries: number;           // 模型重试次数（attempt 级）
}

export interface AuditFailureSummary {
  kind: 'MODEL' | 'TOOL' | 'EGRESS';
  name: string | null;
  code: string | null;
}

export interface AuditListItem {
  // …既有字段…
  auditType: AuditType;
  summary?: string | null;      // RUN 行的用户提问摘要
  steps?: AuditStepSummary | null;
  failure?: AuditFailureSummary | null;
}

export interface AuditListQuery {
  auditType?: AuditType;
  view: AuditView;              // 由页面派生，始终显式传
  // …其余不变…
}
```

`AuditTableOptions` 不变；列定义内部按 `record.auditType === 'RUN'` 分支渲染。

**视图派生函数**（放 `pages/AuditPage.tsx` 或 `auditLabels.ts`）：

```ts
export function auditViewOf(auditType?: AuditType): AuditView {
  return auditType === 'TOOL' || auditType === 'MODEL' ? 'detail' : 'operation';
}
```

### 3.5 状态与数据流

```
筛选变更 → setQuery({...patch, view: auditViewOf(patch.auditType ?? query.auditType), page: 1})
        → useAuditList(query) → auditService.list(query)  // 查询串含 view
        → 行数据（含 steps/failure）
        → AuditTable 渲染
```

- `view` 由 `auditType` 派生后写回 query，避免「筛选与视图不一致」；
- Trace 下钻 = `setQuery({auditType: undefined, traceId, view: 'detail', page: 1})`，提示条显示 Trace 并提供「清除 Trace」；
- 「返回操作视图」= 同时清 `auditType` 与 `traceId`，回到 `view: 'operation'`；
- 刷新/重试沿用当前 query（含 view），行为与现有失败重试一致；
- 导出请求不带 `view`（后端固定明细）。

### 3.6 UI 状态

| 状态 | 表现 |
|------|------|
| 操作视图（默认） | 8 列：Trace ID / 时间（日期 + 时刻 + 耗时） / 审计类型 / 操作人 / Agent / 对象 / 动作 / 结果 |
| 明细视图 | 列同操作视图；顶部提示条：`步骤明细视图 · 一行 = 一次调用。完整链路见运行详情。 [返回操作视图]` |
| Run 行失败 | 行首红色标记 + 结果标签「失败」；失败步骤与错误文案在运行详情的「失败原因」与事件轮廓中 |
| Run 行有重试但成功 | 列表不区分；重试次数在运行概览展示 |
| 步骤降级（`steps=null`） | 运行概览的计数显示 `-`；不显示 `0` |
| 进行中 / 已取消 | 结果标签新增「进行中」（蓝）/「已取消」（灰）；排序仍按开始时间倒序 |
| 空态 / 失败态 | 沿用 `EmptyState` / `ErrorState`，不新增状态 |

**时间列与失败行渲染规则**

```
2026-11-17  16:50:15      ← 日期弱化（text-2/12px），时刻常规（text-0）
            7.6s          ← 耗时胶囊（fill-0 底、text-2），仅 RUN 行
```

- 单元格垂直居中：两行的时间单元格与单行内容对齐；
- 失败 Run 行加 `audit-row-failed`，行首红色标记（`onRow` 透传，不改共享表格结构）；
- 状态标签支持成功 / 失败 / 已拒绝 / 进行中 / 已取消。

### 3.7 样式方案

- 沿用 `.app-table-fit .app-table-nowrap`、`scroll.x: '100%'`；
- 列宽：Trace 206 / 时间 176 / 类型 110 / 操作人 96 / Agent 96 / 对象 auto / 动作 auto / 结果 88，最小表宽 1080；
- 颜色全部走 Semi token（`--semi-color-*`），红色只用于失败，不新增颜色变量；
- 长文本单行省略，沿用 `TableText` / `CopyableText` 的 title 全文；
- 提示条用主色浅底 + 主色文字，与 Column/Form 提示一致。

### 3.8 可访问性与兼容性

- 提示条 `role="status"`，切换视图时读屏可感知；
- 运行入口是 `button`，保留可见焦点环；
- 断词与省略不改变 DOM 顺序；400px 宽时表格横向滚动，页面不横向滚动。

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 缓解 |
|--------|------|------|------|
| FRISK-01 | 视图派生逻辑与后端解析规则不一致 | 请求 400 或语义错位 | 前端始终传显式 `view`；契约测试覆盖组合矩阵 |
| FRISK-02 | 旧深链只带 `audit_type=TOOL` | 后端自动走 detail，前端切换后显示提示条 | 页面启动时按派生规则修正 query 并回写 |
| FRISK-03 | 新列挤压时间/结果列 | 小屏出现截断 | 240px 以下列宽由 `.app-table-fit` 统一分配；横向滚动兜底 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 操作视图 | 一行 = 一次业务操作 |
| 明细视图 | 一行 = 一次模型/工具/出网调用 |
| 步骤降级 | 后端聚合失败时 `steps=null`，前端显示 `-` |

---

*文档结束*
