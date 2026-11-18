---
{"design_template": "frontend"}
---

# 平台统一文件产物投递 前端设计

> **文档编号**: FE-ARTIFACT-DELIVERY-2.0
> **文档版本**: v2.0
> **创建日期**: 2026-11-17
> **文档状态**: 设计评审中
> **设计输入**: `unified-artifact-delivery.prd.md` v2.0 + `unified-artifact-delivery.backend.design.md` v2.0

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线。
- **设计评审**: 第 3 章 → 通过后锁定设计基线。
- **交接契约**: 2.4 验收条件；场景唯一源为 `unified-artifact-delivery.scenarios.md`。

**ID 体系**: US、FEAT、CMP、NFR；场景 S-/E-/B-。

**本版关键结论**:

1. **不新增页面、不新增路由**：文件与 push 状态跟随既有会话历史、任务中心详情、Schedule 详情。
2. **复用现有组件**：`MessageThread` 已渲染 `attachments`；`TaskArtifactList` + `DeliveryBadge` 已渲染 `deliveries[]`；本需求只补齐数据与文案。
3. **WebChat/MSSWWEB 是 pull**：只显示“Web 可查看/可下载”，不伪造“已推送”；MSSWWEB 复用同一套组件与读契约。
4. **企微是 push**：只在有企微路由时展示 `PENDING/DELIVERED/DEGRADED/UNKNOWN/FAILED`；没有绑定显示“未配置企微推送”，不是失败。
5. **Schedule 的 push 路由独立于创建入口**：WebChat 创建 Schedule 时可选择企微目标；没有可用目标时选“仅 Web”。
6. **不显示原始 target_key/chatid**：只显示安全标签（如“企微”）。
7. **不新增轮询**：刷新/打开详情读取当前状态；会话走既有历史/SSE。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | Web 会话/任务中心 · 附件与 push 状态 |
| **需求类型** | 既有页面增强 |
| **业务背景** | 后端统一消息附件与 pull/push 后，用户需要在 Web 历史/任务中心看到附件是否可用、企微是否已推送或失败。 |
| **核心目标** | 复用现有组件展示附件与 push 状态；不新增独立投递管理页。 |

### 2.2 功能方案

| 功能ID | 说明 | 来源 |
|--------|------|------|
| FEAT-08 | 任务详情附件 + push 状态；无 push 显示“仅 Web 可查看” | US-02/03/04 |
| FEAT-06 | Schedule 草稿/详情选择企微 push 目标（或仅 Web） | US-04 |
| FEAT-07 | Web 直接对话附件只在会话历史；不显示企微状态 | US-05 |
| FEAT-14 | MSSWWEB 复用同一 Web 组件/读契约 | US-02/06 |

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围** | 既有 `MessageThread` 附件卡；`TaskArtifactList`/`DeliveryBadge` 状态；Schedule 草稿/详情的 push 目标选择；安全目标标签；i18n；组件测试 |
| **非范围** | 新页面/新路由；全局投递看板；手工重试管理；聊天流伪造“已推送”；MSSWWEB 专用组件；Console 管理面 |
| **有意妥协** | 不提供跨任务投递检索；push 状态只在所属任务/执行记录；失败重试由平台后端驱动，前端不提供重试按钮 |

### 2.4 验收条件

| 场景ID | 前端影响 |
|--------|----------|
| S-01 | 企微创建定时任务：Web 任务中心显示附件；“企微已推送/待推送/失败” |
| S-02 | 企微直接对话：Web 无该会话附件；不显示 |
| S-03 | WebChat 创建定时任务：草稿可选企微目标；任务中心显示附件与企微状态 |
| S-04 | WebChat 直接对话：会话历史显示附件；不显示企微状态 |
| S-05 | MSSWWEB：复用同一历史/任务中心组件，不重复 |
| S-06 | 无企微绑定：显示“仅 Web 可查看”，不显示失败 |
| S-07 | 无 Web 入口：不出现 Web 页面 |
| S-08 | 多文件：一条消息多个附件卡/列表项，顺序保留 |
| S-09 | 显式重发：出现新的消息/附件条目，允许重复可见 |
| S-10 | UNKNOWN：显示“待核实”，不显示成功/失败 |

#### 2.4.1 验证环境

| 维度 | 假设 |
|------|------|
| 容量 | 单消息 1–5 个附件；任务详情分页不变 |
| 依赖 | 真实任务中心 API、真实历史/SSE、真实 i18n、Semi；后端返回 attachments/deliveries |
| 测试 | component 静态渲染 + API contract；不新增浏览器 E2E |

---

## 3. 前端技术设计

### 3.1 技术选型

沿用 React 18 + TypeScript + Semi Design + 共享 `api`/service 层；不引入新状态库/请求库。

### 3.2 页面与路由

无新增页面/路由：

- WebChat/MSSWWEB 会话：既有历史/SSE 渲染附件。
- 任务中心：既有 `ScheduleDetailPanel` / `BackgroundTaskDetailPanel`。
- Schedule 草稿：既有 `DraftCard`/创建表单，新增 push 目标选择。

### 3.3 组件设计

```text
<MessageThread>                     # 既有：消息 attachments
└─ <MessageAttachmentCard>          # 既有

<ScheduleDetailPanel>               # 既有
└─ <ExecutionPanel>
   └─ <TaskArtifactList>            # 既有：附件 + deliveries
      └─ <DeliveryBadge>            # 既有：push 状态

<DraftCard> / Schedule 创建表单     # 既有：新增 push 目标选择
```

| 组件ID | 组件名 | 类型 | 复用 | 职责 |
|---|---|---|---|---|
| CMP-01 | `MessageThread` | 展示 | 既有 | 渲染消息正文 + 附件卡；Web pull |
| CMP-02 | `TaskArtifactList` | 展示 | 既有 | 渲染附件、版本、下载、push 状态 |
| CMP-03 | `DeliveryBadge` | 展示 | 既有 | 状态 → 文案/色调；不请求 |
| CMP-04 | `ScheduleDeliveryTarget` | 表单 | 新建（小） | 选择“仅 Web”或可用企微目标；只读展示 |

### 3.4 组件接口契约

**`TaskArtifactDeliveryRef`（契约新增字段）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `target_key` | string | 后端稳定目标身份；不直接展示 |
| `target_label` | string | 安全标签（如“企微”）；UI 展示 |
| `status` | `PENDING \| DELIVERED \| DEGRADED \| UNKNOWN \| FAILED` | push 状态 |
| `error_code` | string \| null | 失败/待核实原因 |
| `retryable` | boolean | 是否仍有自动重试 |
| `fallback_url` | string \| null | DEGRADED 取件链接 |

**`TaskArtifact`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `deliveries` | `TaskArtifactDeliveryRef[]` | push 目标；空数组表示没有 push |
| `artifact_id` / `name` / `media_type` / `size` / `checksum` / `version` | 既有 | 下载仍按不可变版本 |

**任务详情**

| 字段 | 类型 | 说明 |
|---|---|---|
| `delivery_intent` | `PUSH \| NONE` | Schedule/Task 的 push 配置；NONE 表示仅 Web |
| `push_target_label` | string \| null | 目标安全标签；无 push 为 null |

**Schedule 草稿/创建**

| 字段 | 类型 | 说明 |
|---|---|---|
| `delivery_route` | `DeliveryRouteInput \| null` | 可选企微目标；null = 仅 Web |
| `available_push_targets` | `PushTargetOption[]` | 当前用户-Agent 可用企微目标 |
| `delivery_intent` | `PUSH \| NONE` | 由是否选择目标派生，不由入口决定 |

**`PushTargetOption`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `target_label` | string | 安全标签（如“企微 · 销售群”），不含 chatid/凭据 |
| `route` | `DeliveryRouteInput` | 受信目标；前端只回传，不展示内部 ID |
| `channel` | string | 渠道安全名（当前 `WECOM`） |

**`ScheduleDeliveryTarget` Props**

| Props | 类型 | 说明 |
|---|---|---|
| `value` | `DeliveryRouteInput \| null` | 当前选择；null = 仅 Web |
| `options` | `PushTargetOption[]` | 可用目标；空时禁用 PUSH |
| `onChange` | `(value: DeliveryRouteInput \| null) => void` | 选择/清空 |
| `disabled` | boolean | 无权限/加载失败 |

不包含“立即发送/重试”动作；只配置目标。提交时沿用现有 `delivery_intent`/`delivery_route` 契约；前端不展示 route 中的内部 ID，也不拼 URL。

### 3.5 状态与数据流

```text
Web 会话：history/SSE -> MessageThread -> MessageAttachmentCard
         最终 ASSISTANT_MESSAGE 的 attachments 也走同一组件
任务中心：taskCenterApi.getExecution -> TaskArtifactList -> DeliveryBadge
Schedule：draft/schedule API + delivery-targets -> ScheduleDeliveryTarget -> delivery_route
```

- 不新增轮询；刷新/打开详情读取当前状态。
- Web 附件下载仍走既有 `artifactDownload`；push 状态不替代下载。
- `deliveries=[]` 只在 `delivery_intent=NONE`/没有 push 路由时出现；push 已请求但 outbox 尚未激活时后端返回 `PENDING`，前端不自行猜“仅 Web”。
- 不读 Cookie、不拼 URL、不直接请求后端。

### 3.6 UI 状态

| 视图 | 状态 | 展示 |
|---|---|---|
| Web 会话附件 | 可用 | 附件卡/下载 |
| Web 会话附件 | 不可用 | 既有 `available=false` 卡片 |
| 任务中心 · 仅 Web | `delivery_intent=NONE` / `deliveries=[]` | “仅 Web 可查看” |
| 任务中心 · push 待投递 | `PENDING` | “企微待推送” |
| 任务中心 · 已推送 | `DELIVERED` | “企微已推送” |
| 任务中心 · 降级 | `DEGRADED` | “企微已发送取件链接” + 链接 |
| 任务中心 · 待核实 | `UNKNOWN` | “企微待核实” |
| 任务中心 · 失败 | `FAILED` | “企微推送失败” + 原因 |
| Schedule 草稿 | 无可用目标 | “仅 Web 可查看”；PUSH 禁用 |
| Schedule 草稿 | 有目标 | 选择“仅 Web”或企微目标 |

文案键（新增/复用）：

```text
chat.taskCenter.delivery.webOnly
chat.taskCenter.delivery.pushPending
chat.taskCenter.delivery.pushDelivered
chat.taskCenter.delivery.pushDegraded
chat.taskCenter.delivery.pushUnknown
chat.taskCenter.delivery.pushFailed
chat.taskCenter.delivery.targetLabel
chat.taskCenter.delivery.notConfigured
chat.draft.deliveryTarget.label
chat.draft.deliveryTarget.webOnly
chat.draft.deliveryTarget.unavailable
```

状态色调沿用 `backgroundTaskLabels.tsx`：成功绿、降级琥珀、待投递蓝、待核实灰、失败红；不新增视觉体系。

### 3.7 样式与可访问性

- 复用任务中心现有 CSS/tokens；附件卡与状态同一行/紧随其后，不挤压下载按钮。
- 状态徽标有可读文本，不只靠颜色；失败原因 `role="alert"` 或等价可读标记。
- 不改变键盘下载路径；target_label 不包含 chatid/凭据。
- 多附件按后端顺序渲染；不自行排序/去重。

### 3.8 MSSWWEB

- 复用同一 `MessageThread`、`TaskArtifactList`、`DeliveryBadge`、i18n。
- 只新增入口路由；不新增组件/样式/投递状态。
- 与 WebChat 共享同一用户-Agent 的会话、任务、文件、产物；前端不复制数据。

---

## 4. 风险与依赖

| 风险 | 应对 |
|---|---|
| 后端未返回 `target_label` | 前端只显示通用“推送目标”，不显示 raw target_key |
| `deliveries=[]` 被误读为不可用 | 用 `delivery_intent` 区分“仅 Web”与“无 push”；文案明确 |
| 无企微绑定被显示成失败 | `delivery_intent=NONE` / `deliveries=[]` → “未配置企微推送”，不是失败 |
| 多附件状态不一致 | 消息级状态权威；同一消息附件共享状态；per-attachment 错误单独展示 |
| MSSWWEB 路由另立需求 | 本需求只复用读契约；不新增 MSSWWEB 页面/路由 |

---

## 5. 需求追溯矩阵

| 用户故事 | 功能ID | 组件 | 场景 | 测试层级 |
|---|---|---|---|---|
| US-02 | FEAT-03/08 | MessageThread/TaskArtifactList | S-03/S-04/S-05 | component/contract |
| US-03 | FEAT-05/08 | TaskArtifactList/DeliveryBadge | S-01/S-02/S-10 | component/contract |
| US-04 | FEAT-06 | ScheduleDeliveryTarget/TaskArtifactList | S-01/S-03 | component/contract |
| US-05 | FEAT-07 | MessageThread | S-02/S-04 | contract |
| US-06 | FEAT-04/14 | 无新增 | S-05 | contract |

## Spec Compliance Matrix

| Spec/Rule | 设计影响 | 落点 | 场景 |
|---|---|---|---|
| `frontend/harness-frontend` | 请求只走共享 client/service | §3.5 | S-01..S-10 |
| `ui/harness-ui` | 复用现有列表/标签样式 | §3.7 | S-01..S-10 |
| `i18n/harness-i18n` | 状态文案只走词条 | §3.6 | S-01..S-10 |
| `im/harness-im#RULE-006` | 任务产物按执行 owner 核属 | 既有详情/下载 | S-01/S-03 |
| `im/harness-im#RULE-im-002` | 目标标签渠道中立 | §3.4 | S-01/S-03 |

---

*文档结束*
