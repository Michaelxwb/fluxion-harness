---
{"design_template": "full"}
---

# 平台统一文件产物投递 后端设计

> **文档编号**: MOD-ARTIFACT-DELIVERY-2.0
> **文档版本**: v2.0
> **创建日期**: 2026-11-17
> **文档状态**: 设计评审中
> **设计输入**: `.code-flow/tasks/2026-11-17/unified-artifact-delivery/unified-artifact-delivery.prd.md` v2.0 + 现有 Runtime/Worker/Gateway 代码 + OpenClaw 的 `ReplyPayload`/MEDIA 实现参考

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线。
- **设计评审**: 第 3-4 章 → 通过后锁定设计基线。
- **交接契约**: 2.5 验收条件；场景唯一源为 `unified-artifact-delivery.scenarios.md`。

**ID 体系**: US、FEAT、API、RULE、NFR、RISK；场景使用 S-/E-/B-。

**本版关键结论（先读）**:

1. **统一单元是消息**：`MessagePayload { text, attachments[] }`；一条消息多个附件；pull 与 push 读同一份附件引用。
2. **WebChat / MSSWWEB 是 pull**：没有投递意图、没有投递泵；读写 canonical 历史/任务中心/附件下载。两者共享同一用户-Agent 的 Web 数据。
3. **只有 WECOM 是 push**：单一消息级 `message_delivery`，一个消息对一个目标一条意图；替代 `task_delivery` 与 `channel_notification` 的 push 分支。
4. **定时任务双表面**：任务中心 pull 始终存在（有 Web 入口时）；企微 push 由 Schedule 显式路由决定，**独立于创建入口**。
5. **直接对话按来源入口**：Web 来源只 pull，企微来源只 push；不跨入口复制。
6. **无绑定/无入口不是失败**：没有企微绑定不产生 push；没有 Web 入口不产生 pull；只有显式配置的 push 路由失效才 `FAILED`/`NEEDS_ATTENTION`。
7. **功能不感知底层**：业务/Skill 只产出消息与附件；入口能力解析与投递由平台完成。
8. **不新增第三套 push**：建立 `message_delivery` 后删除 `task_delivery` 与 channel push 分支；`channel_delivery` 的租约/ack 协议抽成通用实现，只保留一套。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | 平台统一文件产物投递 |
| **模块ID** | MOD-ARTIFACT-DELIVERY |
| **需求类型** | 技术重构 + 能力补齐 + Bug 修复 |
| **业务背景** | 文件产物分散在 Runtime 内联、Worker 单文件、模型工具三条路径；Web 与企微行为不一致；定时任务与直接对话各自实现；未来入口/通道需要重复写交付代码。 |
| **核心目标** | 所有执行形态只产出“消息 + 附件”；Web 入口做 pull，用户绑定的出站 IM 身份做 push（不按创建入口）；push 只有一套消息级状态机；定时任务与直接对话共用底层。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|------|------|
| **当前问题** | `deliver_artifact` 同步直发、无持久重试；Worker 只回单文件 `result_artifact_id`；`SKILL_OUTPUT_DIR` 在 Worker 登记但不投递；Web 与企微各自实现；`task_delivery` 与 `channel_notification` 两套 push。 |
| **业务影响** | 后台/定时文件可能不交付；重试/并发可能重复；作者无法只靠输出目录；新增入口需要改交付代码。 |
| **预期价值** | 文件交付由平台保证；Web/企微行为一致；同一消息+target 只出现一次；未来入口只接读投影/适配器。 |

### 2.3 功能方案

| 功能ID | 设计落点 |
|--------|----------|
| FEAT-01 | `SKILL_OUTPUT_DIR` 成功输出登记 + 终态消息挂载 |
| FEAT-02 | Artifact 版本即行 + `logical_file_id` + `OUTPUT` |
| FEAT-03 | `MessagePayload`/`AttachmentRef`；`DeliveryMessage.attachments[]` |
| FEAT-04 | `SurfaceResolver`：Web pull / 企微 push / 两者 / 都没有 |
| FEAT-05 | `runtime.message_delivery` + `MessageDeliveryPump` |
| FEAT-06 | Schedule 路由独立于入口；Task 终态写 outbox；任务中心 pull |
| FEAT-07 | 直接 Run 按来源入口；Web 发布 / 企微 push |
| FEAT-08 | `TaskArtifactRef.deliveries` + `delivery_intent`/安全标签 |
| FEAT-09 | `write_artifact` / `deliver_artifact` 统一消息附件服务 |
| FEAT-10 | 最终助手文本 `MEDIA:` 兼容解析（P1） |
| FEAT-11/12/13 | 文档、`execute`、`WORKSPACE_ROOT` |
| FEAT-14 | MSSWWEB 加入 Web pull 入口集合 |

### 2.4 范围与边界

| 类别 | 内容 |
|------|------|
| **范围** | 消息附件契约；Artifact 不可变版本；Web pull/企微 push 分流；单一 message_delivery；定时任务 Web+push；直接对话；`SKILL_OUTPUT_DIR`；平台工具；任务中心状态；`MEDIA:` 兼容；`execute`/`WORKSPACE_ROOT` |
| **非范围** | 文本终态/通知未读重构；入站附件；独立投递管理页；新增 push 通道；调度配额；历史数据回填；Console 大规模改版 |
| **前置假设** | Artifact Store 可用；Runtime/Worker/Gateway 同一租户；Web 入口可读历史/任务中心；企微路由可解析；开发期旧数据可清空 |

### 2.5 验收条件

#### 2.5.1 业务规则

| ID | 类型 | 描述 |
|----|------|------|
| RULE-01 | 消息 | 一条消息 = 正文 + attachments；多文件一条消息；附件冻结版本/checksum |
| RULE-02 | Pull | WebChat/MSSWWEB 只读历史/任务中心；不创建投递意图 |
| RULE-03 | Push | 只有 WECOM；消息级 `message_delivery`；同一 message key + target 至多一次用户可见发送 |
| RULE-04 | 缺失能力 | 无企微绑定不 push；无 Web 入口不 pull；不报错、不伪造送达 |
| RULE-05 | 定时 | Task 终态始终 Web 可读（有入口）；push 路由独立于创建入口 |
| RULE-06 | 直接对话 | Web 来源只 pull；企微来源只 push；不跨入口复制 |
| RULE-07 | 未知 | 发送无结论 → `UNKNOWN`；不自动重发；显式核实/处置 |
| RULE-08 | 版本 | 消息/附件引用不可变；追加版本不改旧消息 |
| RULE-09 | 重发 | 自动重试复用 message key；用户显式“再发一次”创建新消息 |
| RULE-10 | 安全 | 附件/消息按 tenant/owner/agent 核属；路由发送前复核 |
| RULE-11 | 保留 | pending 消息引用的附件不被清理；失败终态保留引用 |

#### 2.5.2 验证环境

| 维度 | 假设 |
|------|------|
| 容量 | 200 人；单消息 1–5 个附件；单文件/总量受平台预算 |
| 依赖 | 真实 PG/Redis、真实 Gateway、企微测试探针、真实 Web 历史/任务中心 |
| 测试 | 单元 + contract + integration；浏览器 E2E 人工场景 |

---

## 3. 现状与根因

### 3.1 当前代码事实

| 事实 | 位置 |
|---|---|
| Run 终态有持久通知 + `ChannelDeliveryPump`，但只发文本；`artifact_ids` 只是通知引用 | `channel_notification.py`、`channel_delivery_pump.py` |
| Task 投递有 `task_delivery`/`DeliveryLoop`，只支持单个 `result_artifact_id` | `delivery/service.py`、`delivery/records.py` |
| WebChat 任务结果由 `runtime_result_outbox` → Worker → `/internal/webchat/task-results`；消费方是 Worker | `results/dispatcher.py` |
| WebChat 附件由 `ARTIFACT_CREATED` canonical 事件投影到历史 | `attachments/publication.py`、`public_events.py` |
| `deliver_artifact` 同步直发：Web 发布，企微直接调 Gateway；无持久重试 | `attachments/tools.py` |
| Task Artifact 是单行 + `metadata_json.versions`；`artifact_id` 可被追加改写 | `attachments/task_artifacts.py` |
| Worker 只登记成功 Skill 的输出；Runtime `_register_outputs` 未检查状态 | `worker/executor.py`、`skill_tools.py` |
| Schedule 的投递路由被强制等于创建入口 | `schedule_drafts.py:176-218` |
| 任务中心已有 `TaskArtifactRef.deliveries` 与前端徽标，但后端未填充 | `scheduling.py`、`TaskArtifactList.tsx` |

### 3.2 根因

1. **把“文件”当成投递单元**：多文件、重发、消息顺序都变复杂。
2. **把 WebChat 当 push 目标**：统一文档要求 Web 走 Gateway/投递泵，与代码的 pull 事实相反。
3. **两套 push 状态机**：Run 与 Task 各一套，未来功能必须判断自己属于哪套。
4. **定时任务路由绑定入口**：Web 创建的任务无法推企微。
5. **附件版本可变**：`artifact_id` 可能指向最新版，消息/取件无法冻结。
6. **缺失能力被当失败**：没有 Web/企微绑定不应产生错误投递。

---

## 4. 设计

### 4.1 分层与数据流

```text
Execution（Run / Task / Skill / Agentic）
   │  只产出消息与附件
   ▼
MessagePayload { text, attachments[] }
   │
   ├─ SurfaceResolver
   │     ├─ Web pull（WEBCHAT/MSSWWEB）：canonical history / task center
   │     └─ 企微 push（WECOM）：message_delivery（消息级）
   │
   ├─ pull：无 DeliveryIntent；历史/任务中心读同一附件
   └─ push：MessageDeliveryPump → Gateway → WECOM
```

职责：

| 层 | 只负责 |
|---|---|
| Skill/Tool/Agentic | 写 `SKILL_OUTPUT_DIR` 或返回结构化 attachments；不判断 pull/push |
| Runtime | 登记 artifact；构建消息；SurfaceResolver；Run push 意图；Web 发布；投递泵 |
| Worker | Task 输出登记；Task 终态写 `task.message_outbox`；任务中心读投影；不直发 |
| Gateway | 渠道适配；多附件发送；回执/降级；不持有业务状态 |
| Console/Web | 读历史/任务中心；不重新判断投递 |

### 4.2 Artifact：版本即行 + `OUTPUT`

`runtime.artifact` 保留现有 owner XOR（`run_id`/`task_id`），改变版本身份：

| 字段 | 变化 |
|---|---|
| `artifact_type` | `AGENT_OUTPUT`/`TASK_OUTPUT` → `OUTPUT`；`TOOL_RESULT`/`TRANSCRIPT`/入站不变 |
| `logical_file_id` | 新增 UUID；同一逻辑文件各版本共享 |
| `version` | 新增 int；`UNIQUE (tenant_id, logical_file_id, version)` |

规则：

- 每次写入产生**新行、新 `artifact_id`**；字节/checksum/storage_key 不再改变。
- 逻辑身份 = owner + 产出身份 + 规范路径；`append_artifact` 新建版本行并返回新 `artifact_id`，旧行保留。
- 同一执行重复登记同路径同内容复用原版本；并发由行锁/唯一约束兜底。
- 存储键按 `(tenant, owner, artifact_id)`；不再用行内 `metadata_json.versions` 承载历史。
- 下载/取件按 `artifact_id + checksum` 定位版本；`TaskArtifactRef` 按 `logical_file_id` 取最新版本展示。
- 开发期旧数据可清空；`UPDATE artifact_type` 仅用于保留旧行的开发环境。

**必须同步的读路径/常量**：

- 写入：`attachments/output_service.py`、`attachments/task_artifacts.py`、`attachments/tools.py`（`write_artifact`/`append_artifact`）。
- 读/下载：`attachments/task_artifacts.py::resolve_task_artifact_version`、`webchat_artifacts.py`、`attachments/reference.py`。
- 类型/方向：`AGENT_OUTPUT_ARTIFACT_TYPE`、`TASK_OUTPUT_ARTIFACT_TYPE`、`DOWNLOADABLE_TASK_ARTIFACT_TYPES`、`PUBLISHABLE_ARTIFACT_TYPES`、`_delivery_refusal`、`list_attachments`。
- 任务中心：`schedule_read.py` 的 `_artifacts_with_children`/`_child_artifact_counts`、`task_service.py` 的计数。
- 发布：`attachments/publication.py` 的 owner-aware 发布、`public_events.py` 的历史投影。
- 清理：artifact retention 的 pending message 保护。
- `append_artifact`：新建版本行、复制 `logical_file_id`/`skill_output`/`relative_path` 元数据、返回新 `artifact_id`；旧行不变。
- `TaskArtifactRef`：按 `logical_file_id` 分组取最新版本；`version` 继续用于展示/下载校验。

### 4.3 消息与附件契约

```text
MessagePayload
  text: string
  attachments: AttachmentRef[]

AttachmentRef
  artifact_id, filename, media_type, size, checksum
```

- 一条消息一条 `MessagePayload`；多文件是数组，不是多条消息。
- 附件引用在消息创建时冻结；后续追加版本不改变旧消息。
- Pull 与 push 读同一附件引用；`WebchatAttachment` 只是公开投影，不复制元数据。
- **最终 Run 消息**：`ASSISTANT_MESSAGE`/`RUN_COMPLETED` payload 直接携带 `attachments`；Web 历史把它投影成**一条**带多个附件的助手消息；企微 push 用同一 payload。**不再为最终输出逐个写 `ARTIFACT_CREATED`**，避免 Web 出现多条空正文文件卡。
- **显式中途交付**：`deliver_artifact` 写一条 `ARTIFACT_CREATED`（Web pull）或 `message_delivery`（企微 push）；该附件不再重复挂到最终消息（按附件身份去重）。
- **Task 结果**：任务中心按 `TaskArtifactRef` 展示多附件；企微 push 用同一 attachments 列表。
- `MEDIA:` 解析出的附件属于最终消息 attachments，不另发 `ARTIFACT_CREATED`。
- `deliver_artifact` 把附件加入当前消息；终态自动挂载只处理尚未显式交付的附件。

**契约扩展**：

- `DeliveryMessage` 增加 `attachments: list[AttachmentRef]`（默认空）；`type` 增加 `message`（正文 + 多附件）或复用 `artifact`（单附件）；`text` 可空但 `text`/`attachments` 至少一个非空。
- `DeliveryRequest.artifact_ids` 继续作为审计/归属列表；`DeliveryRequest.delivery_key` 使用 `message_delivery.message_key`；`route` 仍只用于 push 渠道（当前 WECOM）。
- `DeliveryResponse.outcome` 的 `DEGRADED` 必须映射为 `message_delivery.DEGRADED` + `fallback_url`，不能折叠为 `DELIVERED`。
- Gateway `DeliveryCommand`/Connector 增加 `attachments[]`；企微适配器按通道能力批量/逐条发送；per-file 结果写 attempt receipt，消息级状态按“全部成功/部分降级/失败”归类。
- `TaskArtifactDeliveryRef` 增加 `target_label`（安全标签，如“企微”）；`target_key` 只用于后端定位，不展示。
- `TaskArtifactRef` 继续是任务产物的读投影；`deliveries` 来自 `message_delivery`，消息级状态权威。

### 4.4 入口能力解析（SurfaceResolver）

```text
SurfacePlan
  pull: bool                  # Web 入口可读（WEBCHAT/MSSWWEB）
  push_targets: [PushTarget]  # 有效企微路由；0..N（当前最多 1）
```

| 来源 | Pull | Push |
|---|---|---|
| 直接 Run（WebChat/MSSWWEB） | 来源 Web 会话历史 | 无 |
| 直接 Run（企微） | 无 | 来源企微路由 |
| 定时 Task（有 Web 入口） | Web 任务中心 | Schedule 显式企微路由（如有） |
| 定时 Task（无 Web 入口） | 无 | Schedule 路由 |
| Run 侧 ASYNC Task | Web 任务中心（有 Web 入口） | 来源 Run 的企微路由（如有） |
| Task 内部 JOIN 子任务 | 父任务详情聚合 | 不直接投递；由父任务 |
| DETACH | 任务中心（有 Web 入口） | 冻结路由（如有） |

规则：

- **没有入口不是错误**：`pull=false` 不写任何 pull 状态；`push_targets=[]` 不建 `message_delivery`。
- **显式配置的 push 路由失效**才 `FAILED`，并让来源 Schedule `NEEDS_ATTENTION`。
- Web 入口集合由 contracts 拥有：扩展现有 `LOCAL_STREAM_CHANNELS` 为 `{WEBCHAT, MSSWWEB}`（实现时也可由 `PULL_ENTRY_CHANNELS` 作为同一集合的别名）；push 集合仍是 `OUTBOUND_CHANNELS`（WECOM）。核心域只引用集合，不按渠道字面量分支。
- MSSWWEB 与 WebChat 共享同一用户-Agent 的 Web 会话、任务中心、文件、产物；不新增 MSSWWEB 投递路径。
- 用户-Agent 可以只有 Web、只有企微、或两者都有；`SurfaceResolver` 不假设两者同时存在。

### 4.5 单一 push 状态机：`message_delivery`

```text
runtime.message_delivery
  id, tenant_id
  owner_type RUN|TASK, owner_id
  message_key varchar(128)
  kind TERMINAL|ARTIFACT|HUMAN_QUESTION|TASK_RESULT
  target_key varchar(256)
  route_json jsonb              # 渠道中立路由（企微）
  payload_json jsonb            # {text, attachments:[...]} 有界快照
  status PENDING|DELIVERING|DELIVERED|DEGRADED|UNKNOWN|FAILED
  attempt_count, next_attempt_at
  lease_owner, lease_epoch, lease_until
  last_error_code, last_error_message
  fallback_url, delivered_at
```

- `UNIQUE (tenant_id, message_key, target_key)`；`message_key` 是稳定消息身份（全部 UUID，长度 ≤128）：
  - `run:{run_id}:terminal`
  - `run:{run_id}:message:{message_id}`
  - `execution:{task_id}:final`
  - `execution:{task_id}:message:{message_id}`
  - `human:{interaction_id}`
- `deliver_artifact` 每次显式交付生成一个 `message_id`；同一 tool call 重试复用同一 `message_id`，新调用/显式重发生成新 `message_id`。
- `DeliveryRequest` 的 key 校验与 `run_id` 解析扩展 `run:{run_id}:message:{message_id}` 与 `execution:{task_id}:message:{message_id}`；不引入 `artifact:{artifact_id}:target_key` 这类超长键。
- 自动重试/崩溃恢复复用同一 key；显式“再发一次”使用新 `message_id`/新 key，允许再次发送。
- 只有 `DELIVERED` 是直发成功；`DEGRADED` 是降级成功（取件链接）；`NOT_SENT` 回 `PENDING` 退避；`UNKNOWN` 保留核实、不自动重发；`FAILED` 终态。
- 多副本：`FOR UPDATE SKIP LOCKED` + lease/epoch fencing；旧 epoch 的回执不得落库。
- `MessageDeliveryPump` 在 Runtime 进程内；每轮领取 `min(batch, free_slots)`；交互式 `deliver_artifact` 入队后立即领取一次。
- 租约/ack 语义复用现有 `channel_delivery` 协议，抽成通用 `DeliveryProtocolService`，不为 message_delivery 再抄一份。
- Gateway 响应 `DEGRADED` 必须映射为 `DEGRADED` + `fallback_url`，不能折叠成 `DELIVERED`。
- 消息级状态是权威；per-attachment 错误放 attempt receipt，任务中心可投影到附件。

### 4.6 Task 终态与 outbox

`task.message_outbox`（task schema，终态事务内写）：

```text
id, tenant_id, task_id
message_key, kind, target_key, route_json
payload_json                 # text + attachments（有界）
status PENDING|APPLIED|FAILED
attempts, next_attempt_at, lease_owner, lease_until, last_error_code
UNIQUE (tenant_id, task_id, message_key, target_key)
```

流程：

1. Task 成功终态：收集本执行（以及 JOIN/BATCH 父任务聚合）的 `OUTPUT` attachments；写 `task.message_outbox`；`runtime_result_outbox` **按既有 `async-skill-background` 规则**写（模型可见结果），本需求不改变其语义——它不承担 Web 附件投递，也不产生企微 push。Web 任务中心按 owner 读任务/附件，不依赖该 outbox。
2. **Worker 消费者**领取 outbox，调 Runtime `POST /internal/message-deliveries`；同 `message_key + target_key` 幂等。
3. Runtime 校验 task owner/route，写 `message_delivery`；outbox 置 `APPLIED`。
4. 没有 push 路由：不写 outbox；任务中心 pull 仍然可用。
5. 路由失效：Runtime 返回终态失败；Worker 消费者把来源 Schedule 置 `NEEDS_ATTENTION`（同事务写 `task_event`）。
6. 失败/取消/失约的 Task 不写 outbox；附件保留诊断。

> 注意：`runtime_result_outbox` 仍由 Worker 消费并回调 Runtime，不改变其 owner；`task.message_outbox` 同样由 Worker 消费，Runtime 不读 task schema。

**Human 问题**：等待事务写 `task.message_outbox`（`kind=HUMAN_QUESTION`，target=push 路由）；Worker 消费者走同一 Runtime API。Web 任务中心仍从 `task.task_human_interaction` 读，不需要投递意图。

**API：`POST /internal/message-deliveries`（Worker → Runtime）**

- 请求：`tenant_id`、`owner_type`、`owner_id`、`message_key`、`kind`、`target_key`、`route`、`payload{text, attachments[]}`。
- 校验：服务身份 + tenant；owner 存在且未软删；附件属于 owner；路由属于同一用户-Agent 且在 `OUTBOUND_CHANNELS`；payload 有界。
- 幂等：`(tenant_id, message_key, target_key)` 返回首次结果；不重复发送。
- 返回：`delivery_id`、`status`、`message_key`；outbox 据此置 `APPLIED`/`FAILED`。
- 不回传正文/凭据；错误码稳定（路由失效、owner 不存在、附件越权等）。

### 4.7 Schedule 路由独立于入口

- `ScheduleDraftRequest.delivery_route` 可选；PUSH 由“用户是否有已绑定出站身份”决定：有则默认选最近活跃/绑定的目标，没有则 NONE；不再由 `entry_channel` 决定，也不读历史 route 快照。
- 删除 `schedule_drafts.py` 的 `entry_delivery_capability`/`ensure_delivery_target` 入口相等限制；同步 `schedule_confirm.py` 的 route 解析（确认时也按显式目标，不按入口）。
- 校验：路由属于同一 tenant/user/agent、通道在 `OUTBOUND_CHANNELS`、绑定有效；不满足则 `SCHEDULE_DELIVERY_UNSUPPORTED`。
- 有已绑定出站身份 → 默认 PUSH 到最近活跃/绑定的目标；没有绑定 → NONE。创建入口只作来源元数据，不参与默认值。
- 新增只读目标列表（调度默认目标）：Console `GET /internal/channel/targets` + Worker `GET /internal/schedules/delivery-targets`，从用户绑定的出站 `channel_identity` + 启用 bot 投影，返回安全标签与 `DeliveryRouteInput`；通道集合由 `OUTBOUND_CHANNELS` 拥有，新增 IM 通道不在这里加分支。前端不展示 route 内部 ID/通道，也不拼接 URL。没有目标时返回空列表。
- Schedule Revision 冻结 `delivery_intent` + `delivery_route_id`；Task 执行按冻结路由 push。
- Web pull 不是 `delivery_intent`：只要有 Web 入口，任务中心/详情始终可读。
- 需要 Spec Delta：`worker/harness-worker#RULE-003` 明确“PUSH 目标默认来自用户已绑定的出站身份（按最近活跃/绑定选择），独立于创建入口；无绑定 NONE；任务中心 pull 始终可用”。

### 4.8 平台工具

**`write_artifact`**

- Run：写 `OUTPUT`（`logical_file_id` + 新 `artifact_id`），返回附件引用；不自动推送。
- Agentic：经 Runtime `/internal/artifacts` 写 Task-owned `OUTPUT`；返回引用；最终任务结果自动挂载。
- 同一调用重试复用同路径/同 checksum 版本。
- `append_artifact`：同一 `logical_file_id` 新建版本，返回新 `artifact_id`；旧版本不变。

**`deliver_artifact`**

- 解析 `artifact_id` 或 workspace `path`；校验 owner/tenant；`path` 只走登记服务。
- 解析当前执行 `SurfacePlan`：
  - Web pull：`publish_artifact_to_conversation`（或 Task-owned 的受信发布），返回 `PUBLISHED`。
  - 企微 push：写 `message_delivery`（`kind=ARTIFACT`，message_key 含 `message_id`），短等待返回 `DELIVERED/DEGRADED/PENDING/UNKNOWN/FAILED`。
  - 无表面：返回 `NOT_DELIVERED`，附件保留。
- Agentic/Task `deliver_artifact`：企微 push 写 `task.message_outbox`（Worker 消费者 → Runtime API）；任务中心在终态读取附件；不新增 Task 专用发送路径。
- 不直接调 Gateway；不复制发送逻辑；显式重发用新 `message_id`。

**`MEDIA:`（P1）**

- 只在最终助手文本边界解析：独立行、非代码块、workspace/输出目录内本地路径；最多 N 个；无效引用生成可见失败说明。
- 不解析工具 stdout、Skill stdout、插件输出、流式块。
- 解析后登记 `OUTPUT` 并挂到最终消息；与 `SKILL_OUTPUT_DIR`/`deliver_artifact` 同附件身份去重。

### 4.9 任务中心读投影

- `TaskArtifactRef` 继续按任务 owner 核属；`artifact_id + version + checksum` 下载。
- `_artifacts_with_children`：
  - 读 `runtime.artifact`（`OUTPUT`），按 `logical_file_id` 取最新版本；
  - 读 `task.message_outbox`（`PENDING`/`FAILED`）与 `runtime.message_delivery`（`owner_type=TASK`、`owner_id` 属于本任务树）填充 `deliveries[]`；
  - outbox PENDING 表示“push 已请求、正在激活”，返回 `PENDING`；outbox FAILED 返回 `FAILED`；都不能显示成“仅 Web”；
  - `target_key` 存储但 UI 只显示 `target_label`（如“企微”）。
- `deliveries=[]` 只在 `delivery_intent=NONE`（或没有 push 路由）时出现 → UI 显示“仅 Web 可查看”；`deliveries` 非空：显示 push 状态。
- 任务详情（含 BackgroundTaskDetail）返回 `delivery_intent` 与 `push_target_label`；没有 push 为 `NONE/null`，前端据此显示“未配置企微推送”。
- `web_available` 由任务详情可读性表达，不新增投递状态。
- Worker 只读 `runtime.artifact`/`runtime.message_delivery`（带 tenant/soft-delete），不写 Runtime 表；符合 `harness-data` 跨 Owner 只读聚合。
- Web 历史投影：`ASSISTANT_MESSAGE`/`RUN_COMPLETED` 的 `attachments` 直接投影为一条助手消息的附件列表；`ARTIFACT_CREATED` 只用于显式中途交付，二者按 `(artifact_id, checksum)` 去重。

### 4.10 保留、迁移与安全

**保留**：

- `artifact_retention_repository` 增加“被 `message_delivery` PENDING/DELIVERING/UNKNOWN 引用”的附件保护；否则 push 前清理会造成死链。
- Pull 历史引用的附件按现有保留期；`available=false` 明确不可用。

**迁移**：

1. Artifact 新增 `logical_file_id`/`version`；类型统一 `OUTPUT`；开发期清空旧产物/旧投递。
2. 新增 `message_delivery`/`message_delivery_attempt`、`task.message_outbox`。
3. 删除 `task_delivery`/`task_delivery_attempt` 与 `ChannelDeliveryPump` 装配；`channel_notification` 收窄为 Web 收件箱（push 列/泵移除）。企微 Run 的终态/等待通知不写 Web 收件箱，避免直接企微对话出现在 Web（场景 2）。
4. `channel_delivery` 协议泛化为 `DeliveryProtocolService`，供 message_delivery 使用。
5. `runtime_result_outbox` 保留。
6. 不做双写/双读/回填；回滚=清空新表+回退代码（开发期）。

**安全**：

- Artifact/Attachment 按 `tenant_id + owner + agent_id + user_id` 核属。
- Web 发布沿用目标会话/owner 校验；Task-owned 发布按 task execution owner 复核。
- push 发送前复核路由所有权/绑定；撤销/换人即不发。
- `deliver_artifact(path)` 只接受 workspace 相对路径，拒绝绝对路径/`..`/symlink 逃逸。
- `MEDIA:` 只接受最终助手文本 + workspace 路径；不读任意 host 文件。
- 日志/审计不记录文件正文、凭据；target_key 不进入用户 UI。

**可观测性**：

- `message_delivery_total{channel,status}`、`message_delivery_attempts_total{channel,result}`、`message_delivery_unknown_total{channel}`、`message_delivery_oldest_pending_seconds`。
- 日志字段：`message_key`、`delivery_id`、`owner_type/id`、`channel`、`status`、`error_code`；不打印 target_key 的敏感部分。
- 指标标签用 channel，不用 target_key（高基数）。

### 4.11 `execute` 与 `WORKSPACE_ROOT`

- `execute` 的 `target` 保持必填判别字段；漏传/非法时在 `ToolExecutionPipeline.prepare_execution` 产出可行动错误（只指出缺失/非法字段路径，不泄露参数值），不自动猜 `skill`。
- `execute_skill` / `run_skill_script` / `workspace_shell` / `skill_draft_shell` 仍是保留别名；模型主入口是 `execute`。
- `workspace_layout_from_settings` 归一为绝对路径（`expanduser().resolve()`）或对相对路径显式失败；Runtime/Worker 共用同一 resolved root，启动日志可见；`SandboxSpec` 继续严格校验绝对路径。
- 回归覆盖 `execute` 缺 `target`、`execute_skill`、`target=workspace` 的 `pwd`/写文件。

---

## 5. 关键流程

### 5.1 WebChat 直接对话

```text
Run 产出 Skill outputs / write_artifact / MEDIA
→ 登记 OUTPUT artifacts
→ 终态消息：ASSISTANT_MESSAGE / RUN_COMPLETED attachments
→ Web pull：历史投影为一条带多附件的助手消息
→ 无 message_delivery
→ 显式中途 deliver_artifact 才写 ARTIFACT_CREATED
```

### 5.2 企微直接对话

```text
Run 产出 OUTPUT artifacts
→ SurfaceResolver.push = 来源企微路由
→ message_delivery(owner=RUN, message_key=run:{id}:terminal/message:{message_id})
→ MessageDeliveryPump → Gateway → 企微
→ 无 Web 历史/任务中心
```

### 5.3 企微创建定时任务（双表面）

```text
Schedule(route=PUSH WeCom) → Task 成功终态
→ Web 任务中心按 owner 读 task + attachments（pull；不依赖投递）
→ runtime_result_outbox 按既有 async-skill-background 规则（模型可见结果）
→ task.message_outbox → Worker → Runtime message_delivery
→ 企微 push（结果 + attachments）
```

### 5.4 WebChat 创建定时任务（双表面）

```text
Schedule(route=WeCom, 由 Web 表单选择) → Task 成功终态
→ Web 任务中心按 owner 读 task + attachments（pull）
→ runtime_result_outbox 按既有 async-skill-background 规则
→ task.message_outbox → Runtime message_delivery → 企微 push
```

### 5.5 无绑定/无入口

```text
无企微绑定/路由：不写 task.message_outbox；Web pull 仍可用
无 Web 入口：不产生 pull 投影；企微 push 仍可用
两者都无：附件保留在任务/审计记录，不伪造送达
```

### 5.6 重试、未知、降级、重发

```text
自动重试：同 message_key + target；PENDING 退避
无结论：UNKNOWN；不自动重发
降级：DEGRADED + fallback_url
显式重发：新 message_id → 新 message_key；允许再次 push/发布
```

### 5.7 JOIN / BATCH

```text
JOIN 子任务：不直接投递；父任务终态聚合子产物到父结果消息
BATCH 父任务：聚合可见子产物；父结果消息 pull/push
BATCH 子任务：不单独向用户投递（除非父任务明确聚合）
```

---

## 6. 迁移与兼容

1. `artifact` 版本列/约束 + `OUTPUT`；开发期清空旧产物（或仅 UPDATE 类型，若保留旧行）。
2. 新 `message_delivery`/attempt、`task.message_outbox`。
3. 删除 `task_delivery`/attempt、`ChannelDeliveryPump` 装配、`channel_notification` push 列/泵。
4. `channel_delivery` 协议泛化；Run/Task 共用。
5. `runtime_result_outbox` 不变；`/internal/webchat/task-results` 不变。
6. 部署顺序：DB → Runtime（artifact/message_delivery/pump）→ Worker（outbox/read）→ Gateway（multi-attachment）→ 前端。
7. 不做双写/回填；开发期回滚=清空新表+回退代码。

---

## 7. 错误码、文案与安全

- 不新增 Run/Task 业务终态错误码；push 失败只写 `message_delivery` 状态与错误码，不改业务终态。
- `UNKNOWN` 需要可核实/可处置的受信入口：内部管理端点 `POST /internal/message-deliveries/{id}/resolve`（outcome=`DELIVERED|FAILED`，operator/note）或等价 CLI；只允许从 `UNKNOWN` 处置，写 attempt/audit，不自动重发。
- Web 任务中心没有 push 时显示“仅 Web 可查看”，不显示“未推送=不可用”。
- `deliver_artifact` 无表面返回 `NOT_DELIVERED`；显式 PUSH 路由失效返回 `FAILED`。
- 附件/消息/路由按 tenant/owner/agent 核属；日志/审计脱敏；`MEDIA:` 路径受限。
- 指标标签按 channel/status；target_key 不进 UI/高基数指标。

---

## 8. 测试与验收

### 单元

- Artifact 版本即行：追加产生新 `artifact_id`，旧版本字节/checksum 不变；并发唯一约束。
- MessagePayload/AttachmentRef：多附件顺序、冻结版本、去重。
- SurfaceResolver：Web pull、企微 push、两者、都没有；直接 vs 定时；JOIN 子任务。
- `message_delivery`：key 幂等、租约/epoch、PENDING/DELIVERING/DELIVERED/DEGRADED/UNKNOWN/FAILED、退避、路由失效。
- `task.message_outbox`：终态事务写、Worker 消费、幂等、无路由不写。
- Schedule 路由独立于入口：有绑定出站身份默认 push；无绑定 NONE。
- `deliver_artifact`：Web 发布 / 企微 push / 无表面 / 重发。
- `MEDIA:` 解析边界（若实现）。

### Runner/Worker

- Skill 成功输出自动挂载；失败/取消/超限不挂。
- Run 终态：Web 只 pull；企微只 push；两者按 resolver。
- Task 终态：Web 任务中心 + 企微 push；无绑定/无入口。
- 多文件一条消息；JOIN 父聚合；显式重发。
- 崩溃恢复：outbox 重放、message_delivery 重试、UNKNOWN 不重发。

### 集成/导出

- 场景 S-01..S-10、E-01..E-08 真实 PG/Redis/Gateway/企微探针。
- Web 历史/任务中心附件与 push 状态同源；无重复卡片。
- 附件保留保护 pending 消息。
- MSSWWEB 复用 Web 读投影（未来入口 seam）。

---

## 9. 备选方案与取舍

| 方案 | 结论 |
|---|---|
| 保留 `task_delivery` + `channel_notification` 两套 push | 拒绝：底层不一致，未来功能要判断归属 |
| 每个 artifact 一条 delivery | 拒绝：多文件多条消息、状态碎、重发难 |
| WebChat 走 Gateway/投递泵 | 拒绝：pull 面无需 push；现有历史已可用 |
| 新 `message_delivery` 消息级 + 现有 pull | **采用**：一套 push，一套附件契约，pull/push 分流 |
| `MEDIA:` 作为主契约 | 拒绝：OpenClaw 自身是 legacy；结构化附件/输出目录更安全 |
| 类型合并与投递一起做 | 拆分为 artifact-model 任务；避免投递设计被读路径拖累 |
| 复用现有 push 两套 | 只能作为过渡；不满足“底层一致/未来直接接入” |

---

## 10. PRD 决策映射

| PRD | 设计结论 |
|---|---|
| FEAT-01 | `SKILL_OUTPUT_DIR` 成功输出挂到结果消息 |
| FEAT-02 | 版本即行 + `logical_file_id` + `OUTPUT` |
| FEAT-03 | `MessagePayload`/`AttachmentRef`；`DeliveryMessage.attachments[]` |
| FEAT-04 | `SurfaceResolver`；无入口不是错误 |
| FEAT-05 | `message_delivery` + `MessageDeliveryPump`；替代两套 push |
| FEAT-06 | Schedule 路由独立；Task outbox；Web 任务中心 |
| FEAT-07 | 直接 Run 按来源入口 |
| FEAT-08 | `TaskArtifactRef.deliveries` + 安全标签 + “仅 Web”文案 |
| FEAT-09 | `write_artifact`/`deliver_artifact` 统一消息附件 |
| FEAT-10 | `MEDIA:` 最终助手文本兼容（P1） |
| FEAT-11 | 文档/模板 |
| FEAT-12/13 | `execute`/`WORKSPACE_ROOT` |
| FEAT-14 | MSSWWEB 加入 Web pull 集合 |

---

## 11. Spec Compliance Matrix

| Spec/Rule | 设计影响 | 落点 |
|---|---|---|
| `worker/harness-worker#RULE-003` | 消息级意图、稳定键、持久尝试、租约、明确送达 | §4.5/4.6；需 Spec Delta：Schedule 路由独立于入口 |
| `im/harness-im#RULE-im-002` | 渠道中立消息/附件 | §4.3/4.5 |
| `im/harness-im#RULE-im-005` | 会话删除后不可见 | §4.9 |
| `im/harness-im#RULE-006` | 任务产物 owner/版本/checksum | §4.2/4.9 |
| `artifact/harness-skill#RULE-skill-001` | 不可变 Artifact Store | §4.2 |
| `api/harness-api#RULE-api-002` | 幂等/重放 | §4.5/4.6 |
| `secret/harness-secret#RULE-secret-001` | 脱敏 | §4.10 |
| `test/harness-test#RULE-test-001` | 真实 PG/Redis/HTTP 集成 | §8 |
| `persistence/harness-data#RULE-001` | Runtime 写 artifact/message_delivery；Worker 只读/内部 API | §4.6/4.9 |
| `frontend/harness-frontend` | 共享 client/service | 前端设计 |
| `ui/harness-ui` | 复用现有列表/标签 | 前端设计 |
| `i18n/harness-i18n` | 词条 | 前端设计 |

---

*文档结束*
