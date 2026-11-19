---
design_template: frontend
---

# 任务详情页改版 前端设计

> **文档编号**: FE-TASKDETAIL-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-19
> **文档状态**: 设计评审中

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3 章（前端技术设计）→ 通过后锁定设计基线

**ID 体系**: FEAT、CMP、NFR；验收场景引用 `task-detail-redesign.scenarios.md` 的 S-/E-/B-。

**需求来源**: 用户 2026-11-19 对话 brief + 本目录 `README.md` / `prototype.html`（交互稿与细化说明）。

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | fluxion-harness | 前端技术方案、组件实现 |
| 设计/交互 | fluxion-harness | 交互稿、视觉与文案对齐 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v1.0 | 2026-11-19 | fluxion-harness | 初版：定时任务/后台任务详情信息分层、等高两栏、统一分页、无子任务、技术信息弹层 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | WebChat 任务中心 → 定时任务详情 / 后台任务详情 |
| **需求类型** | 交互优化 / 信息架构重构 |
| **业务背景** | 现有详情页把触发状态、Revision、Snapshot、校验值、UUID、原始 JSON 等技术事实直接铺在首屏，非技术用户难以快速判断任务是否正常、结果去了哪里。 |
| **核心目标** | 用户进入详情页 10 秒内看懂：下次何时运行、上次结果、发送去向、运行次数；技术信息收进任务名旁的弹层。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-01 | 定时任务详情信息分层 | 页头 + 四指标 + 最近运行 + 右侧运行详情；移除首屏技术字段与底部技术卡 | P0 | 用户 brief |
| FEAT-02 | 运行列表分页与联动 | 每页 6 条，复用 `PaginationFooter`；点击运行行加载对应 Execution，左右两栏等高 | P0 | 用户 brief |
| FEAT-03 | 后台任务结果摘要 | 有子任务时用环形图 + 进度条 + 失败项摘要替代原始 JSON 和四张计数卡 | P0 | 用户 brief |
| FEAT-04 | 无子任务布局 | `children.total === 0` 时隐藏子任务卡、文件卡全宽、单状态环，不显示 0 项计数 | P0 | 用户 brief |
| FEAT-05 | 技术信息弹层 | 任务名后的 `i` 打开技术信息；所有用户可见；点击外部/Esc 关闭 | P0 | 用户确认 |
| FEAT-06 | 状态与文案整合 | Fire + Execution 合成单一用户状态；技术词改为用户语言 | P0 | 用户 brief |

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `ScheduleDetailPanel.tsx`、`BackgroundTaskDetailPanel.tsx`、`TaskArtifactList.tsx`、`TaskResultSection.tsx`、`ChildTaskTable.tsx`、`taskCenterState.ts`、`task-center.css`、`zh-CN.json` / `en-US.json` 中任务详情相关部分。 |
| **非范围（Out of Scope）** | 任务中心列表页、待处理页、任务编辑流程、调度/执行/投递业务语义、ZIP 打包下载接口、Console 其他模块详情。 |
| **有意妥协 / 技术债** | 纯文本运行暂无 run-level 发送目标汇总，显示「推送到会话」；下载全部本期逐个触发；旧执行缺少 `finished_at` 时显示 `—`。 |

### 2.4 验收条件

> 场景唯一源：`task-detail-redesign.scenarios.md`。本节只保留场景引用与前端设计影响。

| 场景ID | 前端设计影响 / 备注 |
|--------|--------------------|
| S-01 | 定时任务首屏信息分层；四指标、单一状态；无技术字段 |
| S-02 | 运行列表 6 条/页、统一分页、选中行联动右侧详情 |
| S-03 | 技术信息弹层：任务名旁入口、点击外部/Esc 关闭、所有用户可见 |
| S-04 | 后台任务有子任务：环形图、进度条、文件双列、子任务卡 |
| S-05 | 后台任务无子任务：隐藏子任务卡、文件全宽、单状态环 |
| E-01/E-02 | 列表/执行详情加载失败时保留旧内容并可重试 |
| E-03 | 旧执行缺少耗时字段时显示 `—`，不伪造 0 秒 |
| E-04 | 纯文本运行无 `deliveries` 时显示「推送到会话」，不伪造机器人数量 |
| B-01/B-02/B-03 | 0 次、1 次、6/7 次运行的分页边界 |
| B-04/B-05/B-06 | 无子任务且无产物、长名称/多文件、跳过/准入失败 |

#### 2.4.1 验证环境假设

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|------|------|----------------|---------|
| 容量 | 单任务 24 次运行、5 个子任务、8 个结果文件；页面字号与布局在 1440×900 和 420×900 下检查 | 真实 seed 数据 + 组件静态渲染 + 人工浏览器 | 记录缺失项与 owner |
| 硬件 | macOS / Linux 桌面浏览器；窄屏用 420px 视口模拟 | Playwright/浏览器 DevTools | 记录缺失项与 owner |
| 依赖 | 已启动 Console/Runtime/Worker；任务中心 API 可读；测试账号有任务范围 | 本地 dev 或验收栈 | 记录缺失项与 owner |
| 测试预算 | 组件/契约测试覆盖布局、状态映射、分页、空态；浏览器人工场景覆盖真实交互 | Task 声明测试 + 人工场景 | 记录未覆盖风险与 owner |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React + TypeScript | React 18.3 / TS 5.6 | 复用现有 WebChat 任务中心 |
| 状态管理 | 现有任务中心 controller（`taskCenterState.ts` / `backgroundState.ts`） | 现网 | 不引入第二套 store；保留 generation/epoch 竞态守卫 |
| 路由 | 现有 WebChat 任务中心 view | 现网 | 不新增路由；详情仍在任务中心内切换 |
| 样式方案 | `task-center.css` + Semi tokens | Semi 2.84 | 复用现有视觉基线与主题变量 |
| 数据请求 | `modules/webchat/services/taskCenterApi.ts` → `api/client.ts` | 现网 | 遵守 RULE-front-001，组件不裸用 fetch/axios |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| 定时任务详情 | 现有 WebChat 任务中心详情视图 | 页头 + 四指标 + 两栏（运行列表 / 运行详情） | 不新增路由；沿用 `TaskCenterPage` 的 view 状态 |
| 后台任务详情 | 现有 WebChat 任务中心详情视图 | 页头 + 结果摘要 + 文件区 + 条件子任务区 | 无子任务时文件区全宽 |

### 3.3 组件设计

**组件树**

```
<ScheduleDetailPanel>                 # 容器：详情、Fire 分页、Execution 选择
├─ <TaskDetailHeader>                 # 标题 + 技术信息入口 + 状态 + 操作
│  └─ <TaskInfoPopover>
├─ <ScheduleMetrics>                  # 四张指标卡
├─ <div class="task-detail-split">
│  ├─ <RunHistoryList>                # 最近运行 + PaginationFooter
│  └─ <RunDetailCard>                 # 选中运行的结果/文件/发送结果
└─ <RunStatusPill>                    # 列表与详情共用

<BackgroundTaskDetailPanel>           # 容器：执行详情
├─ <TaskDetailHeader>
├─ <BackgroundResultSummary>          # 有/无子任务两种摘要
├─ <div class="task-detail-split">
│  ├─ <TaskArtifactGrid>              # 结果文件
│  └─ <ChildTaskTable>                # 仅 children.total > 0
└─ <TaskInfoPopover>
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | `ScheduleDetailPanel` | 容器 | 改现有 | 组织详情、运行列表、选中 Execution |
| CMP-02 | `ScheduleMetrics` | 展示 | 新建 | 四指标与空值文案 |
| CMP-03 | `RunHistoryList` | 展示 | 由 `FireHistory` 改造 | 运行行、选中态、分页 footer |
| CMP-04 | `RunDetailCard` | 展示 | 由 `ExecutionBody` 改造 | 结果、文件、发送结果、底部操作 |
| CMP-05 | `TaskInfoButton` / `TaskInfoPopover` | 展示 | 新建 | 任务名旁技术信息入口与内容容器 |
| CMP-06 | `RunStatusPill` | 展示 | 新建 | Fire + Execution 合成单一用户状态 |
| CMP-07 | `BackgroundTaskDetailPanel` | 容器 | 改现有 | 组织后台任务摘要、文件、子任务 |
| CMP-08 | `BackgroundResultSummary` | 展示 | 由 `TaskResultSection` 改造 | 有/无子任务摘要、失败项定位 |
| CMP-09 | `TaskArtifactGrid` | 展示 | 由 `TaskArtifactList` 改造 | 列表/网格两种文件布局 |
| CMP-10 | `ChildTaskTable` | 展示 | 现有 | 子任务行、分页、下钻 |

### 3.4 组件接口契约

**CMP-03 `RunHistoryList`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `items` | `readonly FireItem[]` | 是 | — | 当前页运行 |
| `page` / `pageSize` / `total` | `number` | 是 | — | 分页事实，来自服务端 |
| `selectedFireId` | `string \| null` | 否 | `null` | 高亮选中行 |
| `onPageChange` | `(page: number) => void` | 是 | — | 翻页 |
| `onSelect` | `(fire: FireItem) => void` | 是 | — | 选中运行 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onSelect` | `FireItem` | 点击或键盘激活运行行 |
| `onPageChange` | `number` | 上一页/下一页 |

**CMP-04 `RunDetailCard`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `fire` | `FireItem \| null` | 是 | — | 当前选中运行 |
| `execution` | `ExecutionState` | 是 | — | 现有 Execution 加载状态 |
| `downloads` | `ArtifactDownloadState` | 是 | — | 文件下载状态 |
| `onDownload` | `(artifact: TaskArtifact) => void` | 是 | — | 单文件下载 |
| `onOpenFull` | `(executionId: string) => void` | 否 | — | 打开完整执行详情 |
| `onRetry` | `() => void` | 是 | — | 执行详情重试 |

**CMP-05 `TaskInfoButton` / `TaskInfoPopover`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `title` | `string` | 是 | — | 弹层标题 |
| `hint` | `string` | 否 | — | 辅助说明 |
| `children` | `ReactNode` | 是 | — | 技术信息内容 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onOpenChange` | `(open: boolean) => void` | 弹层开合，供埋点/焦点管理 |

**CMP-08 `BackgroundResultSummary`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `detail` | `ExecutionDetail` | 是 | — | 结果、counts、children 事实 |
| `onLocateFailedChild` | `() => void` | 否 | — | 有失败子任务时定位 |
| `onOpenRaw` | `() => void` | 否 | — | 打开技术信息中的原始结果 |

**CMP-09 `TaskArtifactGrid`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `artifacts` | `readonly TaskArtifact[]` | 是 | — | 当前展示文件 |
| `layout` | `'list' \| 'grid'` | 否 | `'list'` | 定时任务详情用 list，后台任务用 grid |
| `downloads` | `ArtifactDownloadState` | 是 | — | 下载状态 |
| `onDownload` | `(artifact: TaskArtifact) => void` | 是 | — | 下载单文件 |

### 3.5 状态与数据流

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|------|--------|------|--------|
| `ScheduleDetailState` | controller（现有） | detail + fires + execution + error phases | ScheduleDetailPanel |
| `selectedFireId` | page local state | `string \| null` | RunHistoryList / RunDetailCard |
| `BackgroundTaskDetailState` | controller（现有） | detail + children + downloads | BackgroundTaskDetailPanel |
| `ArtifactDownloadState` | 现有 shared state | entries per artifact | TaskArtifactGrid |

**数据流**

```text
open(scheduleId)
  → taskCenterApi.getSchedule()
  → taskCenterApi.listFires({ page: 1, page_size: 6 })
  → 选中首条有 execution_id 的 Fire
  → taskCenterApi.getExecution(executionId)
  → RunDetailCard 渲染结果/文件/发送结果

翻页 → listFires(page) → 保留或重置选中 → getExecution()
```

- Fires 首屏固定 `page_size=6`；`PaginationFooter` 使用服务端返回的 `page/page_size/total`。
- 选中规则：目标页仍有当前选中 → 保留；否则选目标页第一条有 `execution_id` 的运行；无执行则不请求详情。
- 继续使用 controller 的 epoch/generation 守卫；旧响应不得覆盖新选择。
- 任务中心隐藏时停止刷新；本设计不新增轮询。

**Service 方法**

| Service 方法 | 对应后端接口 | 调用方 |
|-------------|-------------|--------|
| `getSchedule(scheduleId)` | `GET /webchat/task-center/schedules/{id}` | ScheduleDetailPanel controller |
| `listFires(scheduleId, { page, page_size: 6 })` | `GET /webchat/task-center/schedules/{id}/fires` | ScheduleDetailPanel controller |
| `getExecution(executionId)` | `GET /webchat/task-center/executions/{id}` | ScheduleDetailPanel / BackgroundTaskDetailPanel |
| `getTask(taskId)` | `GET /webchat/task-center/tasks` 详情路径（现有） | BackgroundTaskDetailPanel |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| 定时任务详情 | 页头 + 指标骨架 | 任务不存在 → 现有 notFound 文案 | 保留旧内容 + 错误提示 + 重试 | 四指标 + 两栏 |
| 运行列表 | 行骨架（保持卡片高度） | `暂无运行记录` | 保留旧列表 + 行内错误 + 重试 | 6 行 + 分页 |
| 运行详情 | 固定最小高度骨架 | `选择左侧一次运行查看结果` | 面板内错误 + 重试；列表不清空 | 结果/文件/发送结果 |
| 后台结果摘要 | 环图/计数骨架 | 无结果 → 现有空态 | 保留旧内容 + 重试 | 4/5 或单状态 |
| 文件区 | 文件卡片骨架 | `本次执行没有产物`（仅无产物时） | 下载失败行内提示 | 列表/网格 |
| 子任务 | 表格骨架 | `children.total=0` 时整卡不渲染 | 行内错误 + 重试 | 子任务行 + 分页 |
| 技术信息 | 无需异步 | 字段为空显示 `—` | 无 | 弹层内容 |

### 3.7 样式方案

| 维度 | 约定 |
|------|------|
| 样式与逻辑分离 | 布局类写入 `task-center.css`；组件不写内联颜色/间距魔法值 |
| 设计 tokens | 复用 `--semi-color-*` 与既有 `--app-*`；状态色语义：成功绿、等待琥珀、失败红、中性灰 |
| 等高两栏 | `display: grid; align-items: stretch; grid-template-columns: minmax(0, 1.42fr) minmax(360px, .98fr)`；卡片 `flex-direction: column`，分页/底部操作 `margin-top: auto` |
| 响应式断点 | ≥1080px 两栏；<1080px 单列堆叠；≤760px 收紧标题、隐藏运行列表的用时/文件数列 |
| 弹层 | 桌面锚定标题下方；移动端宽度 `calc(100vw - 32px)`；使用 Semi `Popover` 或等价浮层处理视口碰撞 |
| 文件布局 | 有子任务两列；无子任务四列（≤1080px 两列，≤760px 单列） |

### 3.8 可访问性与兼容性

| 维度 | 要求 |
|------|------|
| 可访问性 | 技术信息按钮 `aria-label`/`aria-expanded`；运行行可键盘激活；弹层焦点进入/返回；状态不只靠颜色 |
| 时间 | 全部经 `DateTimeText`；相对时间只作补充 |
| 浏览器/设备 | 现有 Console 支持范围；桌面 1280/1440，窄屏 420px 无横向滚动 |
| 文案 | 全部走 i18n；zh-CN/en-US 枚举词条齐全；不在组件里硬编码中文 |

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| RISK-01 | 后端尚未返回 `duration_ms` / `artifact_count` | 列表无法显示用时/文件数 | 先隐藏这两列；字段上线后展示 | S-01/S-02 |
| RISK-02 | 纯文本运行没有 run-level 发送目标 | 首屏机器人数量可能缺失 | 退化为「推送到会话」，不伪造数量 | E-04 |
| RISK-03 | 旧执行缺少 `finished_at` | 用时为空 | 显示 `—`，不显示 0 秒 | E-03 |
| RISK-04 | 长任务名/长文件名/多文件 | 布局溢出 | 省略 + title；文件网格自适应；窄屏单列 | B-05 |
| RISK-05 | 右侧详情比左侧高 | 等高两栏出现空白 | 底部操作贴底；分页贴底；保持最小高度 | S-02 |
| RISK-06 | 下载全部触发多文件被浏览器拦截 | 用户以为失败 | 逐个下载并提供行内状态；ZIP 另立需求 | E-05 |

**依赖**：后端 `FireItem` / `LastFireSummary` 增加 `duration_ms`、`artifact_count`（见后端设计）；现有 `ExecutionDetail`、`TaskArtifact.deliveries`、`TaskChildItem` 不变。

---

## Spec Compliance Matrix

| Spec/Rule | 设计影响 | 设计落点 | 验收场景 | 状态/N/A 理由 |
|-----------|---------|---------|---------|----------------|
| `frontend/harness-frontend#RULE-front-001` | 所有任务中心数据仍经 service 层 | §3.5 Service 方法 | S-01~S-05 | applied |
| `frontend/harness-frontend#RULE-004` | 任务中心保留当前对话控制器；请求按 generation 隔离；隐藏时停止刷新 | §3.5 数据流 | S-02/E-01 | applied |
| `i18n/harness-i18n#RULE-i18n-001` | 新增文案全部进入 zh-CN/en-US；状态枚举两侧齐全 | §3.8 / §7.3 | S-01~S-05 | applied |
| `ui/harness-ui#RULE-ui-001` | 复用 Semi 与共享分页；不新建视觉体系 | §3.1/§3.7 | S-02 | applied（列表页/菜单规则 N/A：本页是 WebChat 任务中心详情） |
| `ui-detail/harness-ui-detail#RULE-ui-detail-001` | 保留 WebChat 任务中心全页详情，不改为 Console SideSheet | §3.2 | — | confirmed N/A：该规则面向 Console 模块详情 SideSheet，本需求沿用现有全页视图 |
| `time/harness-time#RULE-time-001` | 时间统一 `DateTimeText`；后端 ISO8601 | §3.8 | S-01/S-02 | applied |
| `api/harness-api#RULE-api-001` | 运行列表沿用 `{items,page,page_size,total}`，page_size 6 | §3.5 | S-02 | applied |
| `test/harness-test#RULE-test-001` | 组件/契约测试覆盖布局与状态；浏览器场景人工执行 | §4 / 场景文件 | S-01~B-06 | applied |
| `e2e/harness-e2e#RULE-e2e-001` | 不新增 CI E2E；输出人工场景 | 场景文件 | S-01~B-06 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| Fire | 定时任务的一次计划触发记录 |
| Execution | Fire 对应的真实执行 |
| Run | 一次运行；本设计中列表行以 Fire 为入口，详情以 Execution 为事实 |
| 等高两栏 | 运行列表与右侧运行详情在桌面端同一行高度 |
| 技术信息 | 时区、版本、快照、依赖、原始 JSON 等排查字段 |
