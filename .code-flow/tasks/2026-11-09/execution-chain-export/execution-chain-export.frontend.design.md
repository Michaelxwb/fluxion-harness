---
design_template: frontend
---

# 单次执行链路分析包（Execution Chain Export）前端模块需求与设计简报

> **文档编号**: FE-EXPORT-001
> **文档版本**: v1.0
> **创建日期**: 2026-11-09
> **文档状态**: 设计评审中

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定需求基线
- **设计评审**: 第 3 章（前端技术设计）→ 通过后锁定设计基线

**ID 体系**: FEAT（功能）、CMP（组件）、NFR（非功能指标）
场景编号：S-（正常）、E-（异常）、B-（边界）

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | 待定 | 技术方案、代码实现 |
| 设计/交互 | 待定 | 视觉与交互稿（如有） |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-09 | Agent | 初始前端设计：Run/Task 详情“导出链路” |

---

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|------|------|
| **模块名称** | 执行链路导出入口 |
| **需求类型** | 组件 / 交互增强 |
| **业务背景** | Run/Task 详情是排障主入口；管理员需要一个常驻、短路径的“导出链路”动作，把单次执行带到线下分析 |
| **核心目标** | 在 Run/Task 详情头部提供“导出链路”按钮，成功/失败都可导出，加载与失败状态清晰，不新增页面和导航项 |

### 2.2 功能方案 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-06 | 详情页导出入口 | 仅 ADMIN 可见：Run/Task 详情头部常驻“导出链路”按钮；点击下载 ZIP 分析包；tooltip 说明包含完整诊断数据 | P0 | US-01/02 |
| FEAT-07 | 下载状态与错误处理 | 提交中禁用并显示进度；成功触发浏览器下载；404/网络失败给出 i18n 文案并恢复按钮，可重试 | P0 | US-03 |

### 2.3 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `ExportExecutionButton`、`useExecutionExport`、`execution-export` service、Run/Task 详情头部接线、zh-CN/en-US 词条、加载/成功/失败态 |
| **非范围（Out of Scope）** | 新页面/新路由/新菜单；批量导出；导出历史列表；服务端报告结构；审计列表导出改造；成功 Toast 的持久化 |
| **有意妥协 / 技术债** | 只提供默认 ZIP 下载，不提供格式下拉（JSON 可通过后续菜单扩展）；下载错误只按 HTTP 状态映射，不解析 Blob 错误体（与现有审计下载同口径） |

### 2.4 验收条件 [必填]

> 验收场景唯一源：`execution-chain-export.scenarios.md`。

| 场景ID | 前端设计影响 / 备注 |
|--------|--------------------|
| S-04 | 涉及 FEAT-05, FEAT-06, FEAT-07；按钮常驻、tooltip、loading、成功下载、404/网络失败、重试、id 置空不渲染、非 ADMIN 不渲染 |
| S-05 | 涉及 FEAT-01, FEAT-06, FEAT-07；真实浏览器点击下载 ZIP 并解包 |
| S-01 | 失败 Run 导出后按钮进入成功态；组件测试不覆盖 ZIP 内容，由集成测试覆盖 |
| S-02 | 失败 Task 导出后按钮进入成功态；同上 |
| E-01 | 404 文案“该执行不存在或不可读”；不触发下载 |
| E-05 | 非 ADMIN 不渲染“导出链路”入口；API 403 只作对照 |

#### 2.4.1 验证环境假设 [必填]

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|------|------|----------------|---------|
| 容量 | 单次点击一次请求；无并发导出队列 | 组件测试 mock service | 标记 `incomplete` |
| 硬件 | CI 可运行 Vitest/React Testing Library | 现有前端测试命令 | 返回 `env_blocked` |
| 依赖 | 无真实后端；service 可 mock；浏览器支持 Blob/object URL | `tests/frontend` + jsdom | 返回 `env_blocked` |
| profile 参数 | 组件层测试；不引入 E2E | `pytest tests/frontend/...` | 显式报 `incomplete` |

---

## 3. 前端技术设计

### 3.1 技术选型 [必填]

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React + TypeScript | 现有版本 | 与 Console 一致 |
| 状态管理 | 组件局部 state + 自定义 hook | 现有 | 单按钮状态，无需全局 store |
| 路由 | 不新增路由 | 现有 | 入口挂在现有详情 SideSheet |
| 样式方案 | 现有 CSS + `.detail-actions` | 现有 | 复用详情头部操作区 |
| 数据请求 | 共享 `api` 实例 + module service | 现有 | 遵循 RULE-front-001 |

### 3.2 页面与路由结构 [必填]

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| Run 详情 | 无独立路由（SideSheet） | 详情头部操作区 | 在“刷新”旁增加“导出链路” |
| Task 详情 | 无独立路由（SideSheet） | 详情头部操作区 | 在详情头部增加“导出链路” |

> 不新增页面、路由或菜单项；菜单固定项不变。

### 3.3 组件设计 [必填]

**组件树**

```
RunDetailSideSheet（现有容器）
└─ DetailSideSheet actions
   ├─ ExportExecutionButton（新，通用）
   └─ Button 刷新（现有）

TaskDetailSideSheet（现有容器）
└─ DetailSideSheet actions
   └─ ExportExecutionButton（新，通用）

ExportExecutionButton
└─ useExecutionExport（新 hook）
   └─ executionExport service（新）
      └─ api（共享实例）
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | `ExportExecutionButton` | 容器/交互 | 新建，Run/Task 共用 | 渲染按钮、tooltip、loading、错误提示；调用 hook |
| CMP-02 | `useExecutionExport` | hook | 新建 | 管理 busy/error、调用 service、触发 Blob 下载 |
| CMP-03 | `executionExport` service | 服务 | 新建 | 发起 `GET /runs/{id}/export` 或 `/tasks/{id}/export`，返回 Blob + 文件名 |
| CMP-04 | `saveBlob` | 工具 | 新建共享工具 | object URL + anchor 下载；延迟 revoke |

> 分层纪律：`ExportExecutionButton` 不裸用 axios/fetch；所有 HTTP 只经 `modules/execution-export/services/executionExport.ts`。Run/Task 详情只负责传 `kind` 与 `id`，不复制导出逻辑。

### 3.4 组件接口契约 [必填]

**CMP-01 `ExportExecutionButton`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `kind` | `'RUN' \| 'TASK'` | 是 | — | 执行类型 |
| `id` | `string \| null` | 是 | — | Run/Task ID；null 时不渲染 |
| `format` | `'ZIP' \| 'JSON'` | 否 | `'ZIP'` | 下载格式；首版固定 ZIP，预留 JSON |
| `disabled` | `boolean` | 否 | `false` | 外部禁用（如详情尚未就绪） |
| `adminOnly` | `boolean` | 否 | `true` | 仅 ADMIN 渲染；非 ADMIN 直接返回 null |
| `size` | `'default' \| 'small'` | 否 | `'default'` | 与详情头部按钮尺寸一致 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onExported?` | `{filename: string \| null}` | 下载触发后（可选，首版不消费） |
| `onError?` | `{code: string}` | 导出失败（可选，首版由内部 Tips 提示） |

**CMP-02 `useExecutionExport`**

| 返回值 | 类型 | 说明 |
|--------|------|------|
| `busy` | `boolean` | 请求/下载中 |
| `errorCode` | `string \| null` | `NOT_FOUND`/`FAILED`，用于文案映射 |
| `start()` | `() => void` | 发起一次导出；busy 中重入忽略 |

**CMP-03 service**

| 函数签名 | 入参 | 返回 | 错误处理 |
|---------|------|------|---------|
| `downloadExecutionExport(kind, id, format)` | `kind: 'RUN'\|'TASK'`, `id: string`, `format: 'ZIP'\|'JSON'` | `{blob: Blob, filename: string \| null}` | 404 -> `ExecutionExportError('NOT_FOUND')`；其他 -> `('FAILED')` |
| `parseDownloadFilename(header)` | `string \| null` | `string \| null` | 解析 `filename="..."`；非法返回 null |

### 3.5 状态与数据流 [必填]

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|------|--------|------|--------|
| `busy` | `useExecutionExport` local | boolean | `start` 置 true/false |
| `errorCode` | `useExecutionExport` local | `'NOT_FOUND' \| 'FAILED' \| null` | `start` 置位/清空 |

> 首版用 `busy` 防重入：提交中再次点击直接忽略，不引入额外请求代次；按钮只有当前详情一个实例，无需跨提交竞态守卫。

**数据流**

```
点击“导出链路”
  → useExecutionExport.start
  → executionExport.downloadExecutionExport(kind,id,'ZIP')
  → api.get(`/${kind === 'RUN' ? 'runs' : 'tasks'}/${id}/export`, { params:{format:'zip'}, responseType:'blob' })
  → 解析 Content-Disposition 文件名
  → saveBlob(blob, filename ?? `run|task-${id}.zip`)
  → busy=false；成功不弹持久 Toast
```

**数据获取层**

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|-------------|-------------|----------------|
| `downloadExecutionExport('RUN', id, 'ZIP')` | `GET /api/v1/runs/{id}/export?format=zip` | `useExecutionExport` |
| `downloadExecutionExport('TASK', id, 'ZIP')` | `GET /api/v1/tasks/{id}/export?format=zip` | `useExecutionExport` |

### 3.6 UI 状态 [必填]

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| Run/Task 详情头部 | 按钮 `loading`，文案“正在导出…”；禁用 | `id` 为空不渲染按钮 | `404`：Toast “该执行不存在或不可读”；其他：Toast “导出失败，请重试”；按钮恢复可点 | 浏览器开始下载；按钮恢复“导出链路” |
| Tooltip | — | — | — | 文案“导出本次执行的完整链路、失败节点、涉及服务和日志检索键；包含完整诊断数据，请按内部规范保管” |

- 错误提示使用 Semi `Tips.error`，`dedupeKey` 取 `errorCode + id`，避免重复点击刷屏。
- 不展示未完成的 Blob；失败时不会创建下载链接。
- 成功下载不阻塞详情刷新；按钮与“刷新”互不影响。

### 3.7 样式方案 [必填]

| 维度 | 约定 |
|------|------|
| **样式与逻辑分离** | 按钮复用 Semi `Button`；布局复用 `.detail-actions` 的 flex gap，不写业务逻辑到样式 |
| **设计 tokens** | 间距/颜色/字号使用现有 token；不新增魔法值 |
| **字段布局排版** | 详情头部操作区：`导出链路` 在左、`刷新` 在右（或与现有顺序一致），不挤压标题 |
| **响应式断点** | 沿用详情 SideSheet 920px 宽度；窄屏下按钮文案保持完整，tooltip 补充说明 |

### 3.8 可访问性与兼容性 [按需]

| 维度 | 要求 |
|------|------|
| 可访问性 | 按钮有可读文案与 `aria-label`；tooltip 可通过键盘聚焦触发；loading 用 `aria-busy` |
| 浏览器/设备兼容 | 支持现有 Console 目标浏览器；`Blob`/`URL.createObjectURL` 为基线能力 |

---

## 4. 风险与依赖 [按需]

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| RISK-09 | 浏览器拦截多文件下载/弹窗 | 下载未触发 | 单文件、用户点击触发；失败提示 | S-04 |
| RISK-10 | 大文件下载耗时 | 用户以为卡住 | loading 文案；服务端有上限 | S-04 |
| RISK-11 | Run/Task 模块重复实现导出逻辑 | 维护分叉 | 统一 `modules/execution-export`，详情只传 kind/id | S-04 |
| RISK-12 | 非 ADMIN 通过直接 URL/请求导出诊断数据 | 数据泄漏 | 服务端 `AdminAccount` 强制 403；前端隐藏只作 UX | E-05 |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| `harness-frontend#RULE-front-001` | required | HTTP 只经 service；组件不裸用 axios/fetch | §3.3 组件树 / §3.5 数据获取层 | S-04 | applied |
| `harness-i18n#RULE-i18n-001` | required | 按钮、tooltip、错误提示全部走 zh-CN/en-US 词条 | §3.6 UI 状态 / §3.4 契约 | S-04 | applied |
| `harness-ui#RULE-ui-001` | required | 不新增页面/导航；入口在详情头部操作区 | §3.2 页面与路由结构 | S-04 | applied |
| `harness-ui-detail#RULE-ui-detail-001` | required | 复用 DetailSideSheet 的 actions，不重复渲染详情操作 | §3.3 组件树 / §3.4 Props | S-04 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 执行 | 一条 Run 或一条 Task |
| 导出链路 | 下载该执行的 ZIP/JSON 分析包 |
| CMP | Component，组件 |
