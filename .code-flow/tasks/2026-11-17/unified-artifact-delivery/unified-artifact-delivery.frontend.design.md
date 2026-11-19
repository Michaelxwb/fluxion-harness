---
{"design_template": "frontend"}
---

# 平台统一文件产物投递 前端设计

> **文档编号**: FE-ARTIFACT-DELIVERY-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-17
> **文档状态**: 设计评审中
> **设计输入**: `unified-artifact-delivery.prd.md` + `unified-artifact-delivery.backend.design.md`

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线。
- **设计评审**: 第 3 章 → 通过后锁定设计基线。
- **交接契约**: 2.4 验收条件；场景唯一源为 `unified-artifact-delivery.scenarios.md`。

**ID 体系**: US、FEAT、CMP、NFR；场景 S-/E-/B-。

**本版关键结论**:

1. **不新增页面、不新增路由**：文件与交付状态跟随既有任务/执行详情。
2. **本需求的唯一前端改动点**：既有 `TaskArtifactList` 按目标展示交付状态徽标与失败原因（`deliveries[]`）。后台任务中心分组由 `async-skill-background` 需求扩展，本需求只保证产物状态可供其复用。
3. **不建设全局投递看板/手工重试页**。
4. **下载能力不变**：仍按 `artifact_id + version + checksum` 走既有服务层；状态不替代下载。
5. **会话内产物由统一消息通道送达**：前端经既有通知流/会话历史获得，不新增 Run 级订阅，也不新增或改写在用的后台接口。
6. **失败/待核实状态只在执行记录展示**，不在聊天流伪造“已发送”。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | WebChat 任务中心 · 文件交付状态 |
| **需求类型** | 既有页面增强 |
| **业务背景** | 后端统一 Artifact/Delivery 后，用户需要在所属任务/执行记录看到文件是否已交付、降级或失败。 |
| **核心目标** | 在现有任务详情中展示文件与交付状态，不新增独立管理页面。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 说明 | 来源 |
|--------|---------|------|------|
| FEAT-08 | 交付状态随执行记录 | 执行详情产物列表按目标展示 `DELIVERED/DEGRADED/PENDING/UNKNOWN/FAILED`；未请求推送显示 `NONE` | US-04 |
| FEAT-05 | 已投递不重复 | 同一 artifact 在刷新/重进详情后仍只显示一条；无重复卡片 | US-02 |

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围** | 既有任务/执行详情产物列表；按目标的状态徽标；失败/待核实原因；下载不变；会话内产物仍由既有历史/通知接口读取 |
| **非范围** | 新页面/新路由；全局投递看板；投递重试按钮；聊天流伪造状态；消息卡片改版；Console 管理面 |
| **有意妥协** | 不提供跨任务检索；状态只在所属执行记录可见；失败重试由平台后端驱动，不由前端触发 |

### 2.4 验收条件

| 场景ID | 前端设计影响 |
|--------|-------------|
| S-05 | 任务执行详情显示文件与按目标的交付状态 |
| E-04 | 渠道降级显示 `已发送取件链接` |
| E-05 | 无路由/失败显示 `交付失败` + 原因 |
| E-10 | 结果未知显示 `待核实`，不显示成失败或成功 |
| E-11 | Run 终态后送达的会话产物经通知流可见 |
| B-04 | 无权限不显示产物（沿用现有 owner 过滤） |

#### 2.4.1 验证环境假设

| 维度 | 假设 |
|------|------|
| 容量 | 单执行 1–5 个文件；任务详情列表分页不变 |
| 硬件 | 现有浏览器/构建环境 |
| 依赖 | 真实任务中心 API；真实 i18n；真实 Semi；后端返回交付状态 |
| 测试预算 | component 静态渲染 + API contract；不新增浏览器 E2E |

---

## 3. 前端技术设计

### 3.1 技术选型

沿用现有 React 18 + TypeScript + Semi Design + 共享 `api`/service 层；不引入新状态库或请求库。

### 3.2 页面与路由结构

无新增页面/路由。既有 `/chat/tasks/:scheduleId` 任务详情继续承载。

### 3.3 组件设计

```text
<ScheduleDetailPanel>          # 既有容器
└─ <ExecutionPanel>           # 既有
   └─ <TaskArtifactList>      # 增强：状态徽标
      └─ <ArtifactStatusBadge> # 新增展示组件
```

| 组件ID | 组件名 | 类型 | 复用 | 职责 |
|---|---|---|---|---|
| CMP-01 | `TaskArtifactList` | 展示 | 既有 | 渲染文件、版本、下载、交付状态 |
| CMP-02 | `ArtifactStatusBadge` | 展示 | 新建 | 将状态映射为文案/色调，不请求 |

### 3.4 组件接口契约

**CMP-01 `TaskArtifactList`**

新增 `TaskArtifact` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `deliveries` | `DeliveryStatus[]` | 是 | 每个目标一项；空数组表示未请求推送（显示 `NONE`） |
| `deliveries[].target_key` | string | 是 | 渠道中立目标身份（会话或外部通道） |
| `deliveries[].status` | `DELIVERED \| DEGRADED \| PENDING \| UNKNOWN \| FAILED` | 是 | 后端统一状态；`DELIVERING` 折叠为 `PENDING` |
| `deliveries[].error_code` | string \| null | 否 | 失败/待核实原因稳定码 |
| `deliveries[].retryable` | boolean | 是 | 是否仍有自动重试 |

下载事件不变：

```text
onDownload(artifact: TaskArtifact): void
```

**CMP-02 `ArtifactStatusBadge`**

| Props | 类型 | 说明 |
|---|---|---|
| `targetLabel` | string | 目标展示名（会话/通道） |
| `status` | 上述状态或 `NONE` | 决定文案与色调 |
| `errorCode` | string \| null | 失败/待核实时展示本地化原因 |

不包含重试/跳转动作。

### 3.5 状态与数据流

```text
任务详情 API（既有）
  -> taskCenterApi.getExecution
  -> ScheduleExecutionDetail.result_artifacts[].deliveries[]
  -> TaskArtifactList
  -> ArtifactStatusBadge（每个目标一个）
```

- 不新增轮询；用户刷新/打开详情时读取当前状态。
- 下载仍走既有 `artifactDownload` 控制器；状态变化不自动触发下载。
- 不读 Cookie、不拼 URL、不直接请求后端。

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 执行详情产物区 | 沿用详情 loading | 沿用“暂无产物” | 产物列表读取失败沿用详情错误 | 文件 + 状态徽标 |
| 状态徽标 | 无独立 loading | `NONE`（未请求推送） | `FAILED`/`UNKNOWN` 显示原因 | `DELIVERED`/`DEGRADED`/`PENDING` |

文案键（新增）：

```text
chat.taskCenter.artifact.delivery.DELIVERED
chat.taskCenter.artifact.delivery.DEGRADED
chat.taskCenter.artifact.delivery.PENDING
chat.taskCenter.artifact.delivery.UNKNOWN
chat.taskCenter.artifact.delivery.FAILED
chat.taskCenter.artifact.delivery.NONE
chat.taskCenter.artifact.delivery.errorSuffix
```

### 3.7 样式方案

- 复用任务中心现有 CSS/tokens；状态徽标用现有 Tag/Badge 风格，不引入新视觉体系；
- 状态与文件元信息同一行或紧随其后，不改变列表主字段/操作列；
- 失败原因可换行，但不挤压下载按钮。

### 3.8 可访问性与兼容性

- 状态徽标有可读文本，不只靠颜色；
- `FAILED` 原因使用 `role="alert"` 或等价可读标记；
- 不改变键盘下载路径。

---

## 4. 风险与依赖

| 风险 | 应对 |
|---|---|
| 后端状态字段缺失 | 前端不猜状态；`deliveries` 缺失按隐藏徽标处理，需后端契约保证 |
| 状态与下载不一致 | 下载仍按不可变版本/checksum；状态只表达交付结果 |
| 多文件长时间列表 | 沿用分页/滚动，不新增聚合请求 |
| 用户误以为失败可点重试 | 前端不提供重试按钮；失败原因只读 |

---

## 5. 需求追溯矩阵

| 用户故事 | 功能ID | 组件 | 场景 | 测试层级 |
|---|---|---|---|---|
| US-02 | FEAT-05 | TaskArtifactList | S-05/E-01/E-10 | component/contract |
| US-04 | FEAT-08 | TaskArtifactList/StatusBadge | S-05/E-04/E-05 | component |
| US-03 | FEAT-08 | ScheduleDetailPanel | S-03/S-04 | contract |

## Spec Compliance Matrix

| Spec/Rule | 设计影响 | 落点 | 场景 |
|---|---|---|---|
| `im/harness-im#RULE-006` | 任务产物按执行 owner 核属 | 既有详情 API/下载 | B-04 |
| `frontend/harness-frontend` | 请求只走共享 client/service | §3.5 | S-05 |
| `ui/harness-ui` | 复用现有列表/标签样式，不新增页面 | §3.7 | S-05 |
| `i18n/harness-i18n` | 状态文案只走词条 | §3.6 | S-05/E-04/E-05 |

---

*文档结束*
