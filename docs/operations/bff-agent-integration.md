# 外部 Agent 接入指南（BFF）

> **适用对象**：业务平台 BFF / 外部 Agent 接入方
> **协议版本**：`v1`
> **更新日期**：2026-11-13
> **文档修订**：v1.2，设计草案；本轮修订不表示接口已实现或发布。

这份文档只回答一件事：**BFF 应该怎么调用我们的外部 Agent 接口**。

你不需要了解我们的内部服务、配置或管理后台；只需要：

1. 把业务平台的用户 JWT 原样放进 `soc-token` 请求头；
2. 按顺序调用下面的接口；
3. 把 SSE 流原样转发给前端，不要缓冲。

---

## 1. 调用总览

### 1.1 请求怎么走

```text
业务平台前端
  └─ 业务平台 BFF
       └─ muad-nginx（内网统一入口）
            └─ /api/v1/msswwweb/*
```

- BFF 统一走 `muad-nginx`，不直连后端服务。
- 上传、下载、SSE 都走同一个入口，不需要额外开通域名。
- 当前平台的 Base URL 形如：`https://<内网站点>/api/v1/msswwweb`，具体域名/端口由部署方提供。
- 后续同类平台只换 Base URL；下面的路径、字段、错误码保持不变。

### 1.2 一次对话的调用顺序

```text
1. GET  /identity                         确认用户能不能用、拿到入口授权
2. GET  /entries                          拿到 entry_id
3. GET  /conversations?agent_id=...       最近会话（同用户+Agent 跨入口）
4. POST /entries/{entry_id}/conversations 创建会话
5. POST /entries/{entry_id}/messages      发送消息
6. GET  /runs/{run_id}/events             接收流式回答（SSE）
7. GET  /conversations/{id}/messages      查看历史
```

可选能力：

- 上传文件：`POST /entries/{entry_id}/attachments`，把返回的 `upload_id` 放进消息；
- 下载文件：`GET /artifacts/{artifact_id}/content`；
- 停止生成：`POST /runs/{run_id}/cancel`；
- 确认后继续：SSE `confirmation.required` / `active_run.confirmation` → `POST /runs/{run_id}/resume`（例如保存记忆）；
- 最近会话红点：`GET /conversations?agent_id=...` 的 `has_unread` + `POST /conversations/{id}/read`。

---

## 2. 认证：只需要一个请求头

每个请求都带：

```http
soc-token: <业务平台签发的 JWT>
```

规则：

| 项 | 说明 |
|---|---|
| 不要加 `Bearer` | 原样放 JWT 字符串 |
| 不要用 Cookie | BFF 不要把浏览器的 `muad_*` Cookie 转发过来 |
| 不要自报用户/租户 | 不要传 `user_id`、`tenant_id` 作为身份依据 |
| 不要解析 JWT | 由我们校验；BFF 只负责转发 |

可能的认证结果：

| HTTP | 含义 | BFF 怎么做 |
|---|---|---|
| 401 | JWT 缺失、无效、过期 | 让用户重新登录业务平台，刷新 JWT 后重试 |
| 503 | 我们这边该平台的接入尚未就绪 | 不要跳登录页；提示“服务暂不可用”，稍后重试或联系我方 |

> 401 和 503 要分开处理：401 是用户身份问题，503 是我方配置/服务问题。

授权变更成功后直接对资源生效，BFF 不需要通过刷新身份、等缓存到期或要求用户重新登录来触发生效。解绑入口只影响从该入口新建/发送/上传；用户仍有效且有 Agent 授权时，同用户同 Agent 的旧资源继续按共享规则访问。Agent 撤权后禁止读/发送/续流/resume；用户仍有效时可取消、删除及标读，用户停用不保留这些例外。

---

## 3. 通用规则

### 3.1 响应格式

所有 JSON 响应：

```json
{
  "code": "0",
  "msg": "success",
  "data": {},
  "trace_id": "...",
  "request_id": "...",
  "timestamp": "2026-11-13T10:00:00Z"
}
```

- `code="0"` 表示成功；失败看 HTTP 状态 + `code`。
- 所有聊天响应都带 `Cache-Control: no-store`，BFF 不要缓存。
- 时间都是 UTC（ISO-8601）。

### 3.2 幂等

| 操作 | 要求 |
|---|---|
| 创建会话 | 带 `Idempotency-Key`（建议 UUID） |
| 发消息 | 带 `Idempotency-Key`，并带客户端生成的 `message_id` |
| 重试 | 只重试网络错误/5xx；4xx 不重试（429 按提示等待） |

同一个 `Idempotency-Key` + 同一个请求 = 最多执行一次；换操作必须换 key。

### 3.3 分页

| 参数 | 含义 |
|---|---|
| `page` | 第几页，从 1 开始 |
| `page_size` | 每页多少条，有上限 |
| `through_seq` | 历史消息第 2 页起必须带，沿用第一页返回的值 |

---

## 4. 接口说明

> 以下路径都省略 Base URL；所有请求都带 `soc-token`。

### 4.1 `GET /identity` — 这个用户能不能用？

BFF 在展示入口前调用一次。

**Query**

| 字段 | 必填 | 含义 |
|---|---|---|
| `entry_id` | 否 | 指定入口；不传则返回该用户所有可用入口 |

**返回 `data`**

| 字段 | 类型 | 含义 |
|---|---|---|
| `authenticated` | boolean | JWT 是否有效 |
| `platform_user_id` | string/null | 我方平台用户 id；未绑定时为 null |
| `external_identity_id` | string/null | 用户级外部身份 id |
| `allowed` | boolean | 当前入口（或任一入口）是否可用 |
| `reason` | string/null | 不可用原因，见下 |
| `grants` | array | 该用户可用的入口授权列表 |

`grants[]`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `entry_id` | string | 入口 id，后续接口都用它 |
| `agent_id` | string | Agent id |
| `entry_name` | string | 入口展示名，可直接给前端 |
| `allowed` | boolean | 该入口是否可用 |
| `reason` | string/null | 不可用原因 |

`reason` 取值：

| 值 | 含义 | BFF 怎么做 |
|---|---|---|
| `UNBOUND` | 用户没有外部身份/入口授权 | 不展示入口，提示联系管理员 |
| `ENTRY_DISABLED` | 入口已停用 | 不展示入口 |
| `AGENT_DISABLED` | Agent 已停用 | 不展示入口 |
| `NO_AGENT_GRANT` | 用户没有该 Agent 权限 | 不展示入口 |
| `USER_DISABLED` | 用户已停用 | 不展示入口 |

**示例**

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "authenticated": true,
    "platform_user_id": "2f1c...",
    "external_identity_id": "8b2e...",
    "allowed": true,
    "reason": null,
    "grants": [
      {
        "entry_id": "9a3e...",
        "agent_id": "7b2d...",
        "entry_name": "MSS 智能机器人",
        "allowed": true,
        "reason": null
      }
    ]
  }
}
```

---

### 4.2 `GET /client-config` — 前端参数

页面打开时取一次，用于输入长度、上传格式、SSE 重连参数。

**返回 `data` 主要字段**

| 字段 | 含义 |
|---|---|
| `webchat.text_max_bytes` | 单条文本最大字节数 |
| `webchat.upload_max_bytes` | 单文件最大字节数 |
| `webchat.upload_max_per_message` | 单条消息最多几个附件 |
| `webchat.upload_formats` | 允许的格式（`media_type` + 扩展名） |
| `transport.sse_heartbeat_sec` | SSE 心跳间隔 |
| `transport.reconnect_*` | 断线重连参数 |

> 数值以实际返回为准，不要在前端写死。

---

### 4.3 `GET /entries` — 我有哪些入口？

**返回 `data`**

```json
{
  "items": [
    {"entry_id": "9a3e...", "agent_id": "7b2d...", "display_name": "MSS 智能机器人"}
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

| 字段 | 含义 |
|---|---|
| `entry_id` | 入口 id，后续所有接口都用它 |
| `agent_id` | Agent id |
| `display_name` | 展示名 |

---

### 4.4 `POST /entries/{entry_id}/conversations` — 新建会话

**Header**：`Idempotency-Key`

**Body**：空

**返回 201 `data`**

```json
{
  "conversation_id": "c1a2...",
  "entry_id": "9a3e...",
  "title": "新会话",
  "status": "ACTIVE",
  "last_seq": 0,
  "active_run": null
}
```

| 字段 | 含义 |
|---|---|
| `conversation_id` | 会话 id，发消息时必须带 |
| `title` | 会话标题（服务端生成） |
| `status` | `ACTIVE` / `ARCHIVED` |
| `active_run` | 当前活跃 Run，`null` 表示空闲 |

**建议**：用户首次发送前创建一次并缓存 `conversation_id`，不要每次发送都创建。

---

### 4.5 `GET /conversations?agent_id=...` — 用户+Agent 最近会话

同一用户 + 同一 Agent 的会话跨入口共享；BFF 用这个列表作为“最近会话”。

**Query**：`agent_id`、`page`、`page_size`

**返回 `items[]`**

```json
{
  "conversation_id": "c1a2...",
  "entry_id": "9a3e...",
  "channel": "MSSWWEB",
  "title": "异常 IP 分析",
  "create_time": "2026-11-13T09:00:00Z",
  "last_message_at": "2026-11-13T09:30:00Z",
  "last_run_id": "r1...",
  "status": "ACTIVE",
  "active_run_status": null,
  "has_unread": true
}
```

| 字段 | 含义 |
|---|---|
| `entry_id` / `channel` | 会话创建来源；可空；不影响共享资源访问，页面不强制显示标签 |
| `last_message_at` | 最近一条可见消息时间；列表按它倒序 |
| `active_run_status` | `CREATED`/`RUNNING`/`WAITING_TOOL`/`WAITING_INPUT`/`null` |
| `has_unread` | 是否有未读终态通知（用于红点） |

> 新建但未发消息的会话也会出现在列表；BFF 可自行决定是否展示。
> WebChat 创建的同一 Agent 会话也会出现在这里（同用户共享）。

---

### 4.6 `POST /entries/{entry_id}/messages` — 发消息

**Header**：`Idempotency-Key`

**Body**

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `conversation_id` | string | 是 | 会话 id |
| `message_id` | string | 是 | 客户端生成，会话内唯一；重试保持不变 |
| `text` | string | 否 | 文本；与 `upload_ids` 至少一个非空 |
| `upload_ids` | string[] | 否 | 附件上传返回的 id；不能重复 |

**返回 202 `data`**

```json
{
  "conversation_id": "c1a2...",
  "run_id": "r1...",
  "status": "RUNNING",
  "execution_status": {
    "stage": "RUNNING",
    "reason": null,
    "queued_at": null,
    "deadline_at": null,
    "started_at": "2026-11-13T09:31:00Z"
  }
}
```

| 字段 | 含义 |
|---|---|
| `run_id` | 本次回答的 Run id，用来打开 SSE |
| `status` | Run 状态 |
| `execution_status.stage` | 当前阶段：`WAITING_RUNTIME`/`WAITING_MODEL`/`RUNNING`/`WAITING_TOOL`/`WAITING_INPUT`/`COMPLETED`/`FAILED`/`CANCELLED` |
| `execution_status.reason` | 阶段原因（可选） |
| `execution_status.queued_at` | 开始排队时间（等待阶段才有） |
| `execution_status.deadline_at` | 排队截止时间（等待阶段才有） |
| `execution_status.started_at` | 开始执行时间 |

**常见错误**：

| HTTP/code | 含义 | BFF 怎么做 |
|---|---|---|
| 409 `RUN_BUSY` | 该会话已有回答在进行 | 等当前 Run 结束或先取消 |
| 404 | 会话不存在、已删除或不属于该用户/目标 Agent | 刷新会话列表；不要按来源入口过滤历史 |
| 422 | 参数不合法 | 检查字段 |

---

### 4.7 `GET /conversations/{conversation_id}` — 会话详情

**返回 `data` 主要字段**

| 字段 | 含义 |
|---|---|
| `conversation_id` | 会话 id |
| `entry_id` / `channel` | 不可变的会话创建来源；可为空，继续对话不修改它 |
| `agent_id` | 该会话所属 Agent，必须与本次选择的 Agent 相同 |
| `title` | 标题 |
| `status` | `ACTIVE` / `ARCHIVED` |
| `last_seq` | 本次详情快照的会话 canonical 水位 C，用于获取对应历史 |
| `notification_through_seq` | 同次快照的该会话通知水位 N；无未读时为 0；仅用于 §4.10 标读 |
| `active_run` | 当前活跃 Run；`null` 表示空闲 |

`active_run` 里有 `confirmation` 时，表示等待用户确认；按 §4.13 恢复。

详情的状态、C/N 来自同一个快照。打开会话并准备清红点时，从历史第一页开始就传 `through_seq=C`；确认正文、状态/确认和结果提示已经展示后，read 提交 N。不要取一份更新后的 N 去标读仍停留在旧状态的页面。

从当前入口 B 继续 A 创建的会话时，仍向 `/entries/B/messages` 发送并携带该 `conversation_id`；不要把 A 当作本次发送入口，也不要因来源不同而隐藏会话。新 Run/任务通知和交付跟随本次实际来源，创建来源保持 A。

---

### 4.8 `DELETE /conversations/{conversation_id}` — 删除会话

删除后列表、详情、消息都不再可见；重复删除也返回成功。

**返回 `data`**：`{"conversation_id": "...", "deleted": true}`

---

### 4.9 `GET /conversations/{conversation_id}/messages` — 历史消息

**Query**：`page`、`page_size`、`through_seq`（第 2 页起必填）

**返回 `items[]`**

```json
{
  "event_id": "e1...",
  "seq": 41,
  "run_id": "r1...",
  "role": "USER",
  "text": "帮我分析这些 IP",
  "attachments": [],
  "created_at": "2026-11-13T09:30:00Z"
}
```

| 字段 | 含义 |
|---|---|
| `seq` | 会话内序号，可用于定位/排序 |
| `role` | `USER` / `ASSISTANT` |
| `text` | 正文 |
| `attachments` | 附件列表（字段见 §4.15） |
| `created_at` | 消息时间 |

---

### 4.10 `POST /conversations/{conversation_id}/read` — 清除红点

**Body**

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `through_seq` | int | 是 | 已展示的那次会话详情中的 `notification_through_seq`（N）；不是 `last_seq`、历史 through_seq 或 SSE id |

**返回 `data`**

```json
{"conversation_id": "c1a2...", "read_count": 2, "read_at": "2026-11-13T09:35:00Z"}
```

| 字段 | 含义 |
|---|---|
| `read_count` | 本次标记的未读通知数；0 也正常 |
| `read_at` | 标记时间 |

只清除已看到范围内的红点；新到达的通知保持未读。不会修改消息本身。

调用顺序：详情取 C/N → 历史用 C → 展示正文、当前状态与提示 → read 提交 N。加载失败、页面隐藏或已切换会话时不提交。SSE 终态/待确认后如需标读，重新完成上述步骤，不从 SSE 序号推算 N。N 必须为非负整数；超出当前用户已提交通知水位返回 `CURSOR_AHEAD`。服务端只更新当时存在的通知，不保存可自动消费未来通知的上限。

---

### 4.11 `POST /entries/{entry_id}/attachments` — 上传附件

**Content-Type**：`multipart/form-data`，文件字段名 `file`。

**约束**：格式/大小/数量以 `client-config` 为准。

**返回 201 `data`**

```json
{
  "upload_id": "u1...",
  "kind": "DOCUMENT",
  "media_type": "application/pdf",
  "size": 102400,
  "filename": "report.pdf",
  "checksum": "sha256:...",
  "expires_at": "2026-11-14T09:30:00Z"
}
```

| 字段 | 含义 |
|---|---|
| `upload_id` | 发消息时放进 `upload_ids` |
| `kind` | `IMAGE` / `DOCUMENT` / `OTHER` |
| `media_type` | MIME 类型 |
| `filename` | 原文件名（仅展示） |
| `checksum` | 内容校验和 |
| `expires_at` | 暂存过期时间；过期未使用会清理 |

---

### 4.12 `POST /runs/{run_id}/cancel` — 停止生成

**Body**：无

**返回 `data`**

```json
{"run_id": "r1...", "status": "CANCELLING", "cancel_requested": true}
```

| 字段 | 含义 |
|---|---|
| `status` | 当前真实状态（可能是取消中/已取消/已完成） |
| `cancel_requested` | 是否已收到取消请求 |

取消是协作式的，SSE 会在下一个检查点停止。

---

### 4.13 `POST /runs/{run_id}/resume` — 继续等待输入

当 SSE 收到 `confirmation.required`，或会话详情 `active_run.confirmation` 非空时使用。典型场景：Agent 请求“保存记忆”，BFF 展示选项，用户选择后 resume 继续同一个 Run。

**Body**

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `submission_id` | string | 是 | 确认事件里的 `submission_id` |
| `source_event_id` | string | 是 | 确认事件里的 `source_event_id` |
| `message_id` | string | 是 | 本次恢复的客户端消息 id |
| `selection` | string | 是 | 用户选择的选项 `value` |

**返回 `data`**：`{run_id, submission_id, status, execution_status}`

**错误**：409 `NO_ACTIVE_RUN`/`RUN_BUSY`；422 选项非法；Agent 授权被撤销时 403/409（取消仍可用）。

---

### 4.14 `GET /runs/{run_id}/events` — 流式回答（SSE）

**Query/Header**：`after_seq` 或 `Last-Event-ID`；首次可省略。

**帧格式**

```text
id: 42
event: message.delta
data: {"event_id":"...","conversation_id":"...","run_id":"...","seq":42,"timestamp":"...","type":"message.delta","data":{"reply_generation":1,"text":"..."}}
```

**事件类型**

| event | `data` 字段 | 含义 | BFF 怎么做 |
|---|---|---|---|
| `message.delta` | `reply_generation`, `text` | 正文增量 | 追加到当前回答 |
| `message.snapshot` | `reply_generation`, `text`, `part_index`, `part_count` | 正文分片快照 | 同组收齐并校验后原子替换，不逐片追加 |
| `run.stage` | `stage`, `reason`, `queued_at`, `deadline_at`, `started_at` | 阶段变化（排队/执行/等待工具等） | 更新状态提示 |
| `run.tool` | `name`, `phase`, `status` | 工具开始/结束 | 显示“正在执行 X” |
| `run.terminal` | `status` | 回答结束（完成/失败/取消） | 关闭流，刷新历史 |
| `run.error` | `code`, `retryable`, `has_reply` | 失败 | 按 `code` 提示；`retryable` 决定是否可重试 |
| `confirmation.required` | `submission_id`, `source_event_id`, `prompt`, `options[]`, `expires_at` | 需要用户确认 | 展示选项，按 §4.13 恢复 |
| `artifact.created` | 附件对象（见 §4.15） | 产生文件 | 展示附件卡片，按 §4.15 下载 |
| `checkpoint` | 无 | 已扫描的安全水位 | 前面没有未完整应用的事件/分片时才推进应用游标 |

**重连**：

- 客户端保存 `last_applied_seq`，只在事件完整应用成功后推进；重连发送 `Last-Event-ID: <last_applied_seq>` 或对应 `after_seq`。接收帧 id 不能直接覆盖它；BFF 转发的恢复游标必须来自应用层，不能盲用原生 EventSource 按“收到 id”自动维护的值。
- snapshot 同组的所有片具有相同 `event_id/seq/reply_generation`；按组收集并验证 `part_index/part_count`，全部到齐后原子替换正文并推进游标。中途断线丢弃半组，以上一完整游标重连；重放的完整事件/重复片幂等处理，旧 generation 不能覆盖新正文。
- 安全 checkpoint 只能在此前公开事件完整应用后推进；正文应用失败不能跳过它去认领更高 checkpoint。普通 EOF/网络断开不表示 Run 已完成；只有终态事件或重新查询确认的终态可以显示完成。
- `CURSOR_EXPIRED`/`CURSOR_AHEAD`：丢弃旧半组及旧流应用状态，取新详情 C 与 `through_seq=C` 的历史重建页面；若详情仍有 active_run，从 C 订阅同一 Run 的后续事件，禁止把全量历史与旧增量再次拼接。若已终态则展示历史。
- BFF 必须流式转发，不要缓冲、不要合并；客户端需能控制恢复游标并在成功应用后保存它。

**跨入口行为**：

- 会话数据共享，但 Run 的 SSE **不会自动广播到其他入口**；发起方拿到 `run_id` 并订阅，其他入口不会自动知道新 Run。
- 另一个入口刷新/重新打开会话时，如果 Run 仍处于活跃状态，详情里的 `active_run` 会给出同一个 `run_id`，它可以订阅并补流（从自己的游标/起点重放）；如果 Run 已终态，则读历史，不再有流。
- 如果产品要求“两个入口无需刷新就同时实时”，需要额外的会话级活动信号；本期不做。

---

### 4.15 `GET /artifacts/{artifact_id}/content` — 下载文件

**Query**

| 字段 | 必填 | 含义 |
|---|---|---|
| `conversation_id` | 是 | 当前会话 id（用于权限/归属校验） |
| `checksum` | 是 | 附件卡片上的 `checksum`（版本） |

**返回**：文件字节流。图片可能直接预览，其他类型按附件下载。

附件对象字段（历史消息/SSE 里）：

| 字段 | 含义 |
|---|---|
| `artifact_id` | 文件 id |
| `kind` | `IMAGE` / `DOCUMENT` / `OTHER` |
| `media_type` | MIME 类型 |
| `size` | 字节数 |
| `filename` | 文件名 |
| `checksum` | 版本校验和；下载时必须带 |
| `available` | 文件是否仍可下载 |

---

## 5. 最近会话与红点

- 最近会话：`GET /conversations?agent_id=...`，同一用户+Agent 跨入口聚合；按 `last_message_at` 倒序；要最新一条用 `page_size=1`。
- 红点：`has_unread=true` 表示该会话有未读的“回答结束/等待输入/任务结果”通知。
- 用户打开会话后调用 `POST /conversations/{id}/read`，红点消失；重复调用没问题。
- 本期不提供通知中心；红点 + 最近会话足够。

---

## 6. 错误处理

| HTTP | 含义 | BFF 怎么做 |
|---|---|---|
| 401 | JWT 无效/过期 | 让用户重新登录，刷新 JWT |
| 403 | 用户未绑定/无权限/已停用 | 不展示入口或提示无权限 |
| 404 | 资源不存在/不属于该用户 | 刷新列表，不要重试同一 id |
| 409 | 状态冲突（如已有回答在进行） | 按 `code` 处理；`RUN_BUSY` 等当前 Run |
| 422 | 参数不合法 | 检查字段，不要盲目重试 |
| 429 | 容量/限流 | 按 `Retry-After` 等待后重试 |
| 503 | 我方服务/平台未就绪 | 提示暂不可用，稍后重试 |

处理原则：**按 `code` 处理，不要按 `msg` 文本处理**；`msg` 可能随语言变化。

---

## 7. 限制与重试

| 项 | 说明 |
|---|---|
| 文本长度 | 以 `client-config.webchat.text_max_bytes` 为准 |
| 上传 | 以 `client-config.webchat.upload_*` 为准 |
| SSE 重连 | 以 `client-config.transport.reconnect_*` 为准 |
| 重试 | 只重试网络错误/5xx；4xx 不重试（429 按提示等待） |
| 超时 | BFF 的 SSE 读超时要大于心跳间隔；不要整段缓冲 |

---

## 8. 联调清单

- [ ] `GET /identity` 返回 `allowed=true`。
- [ ] `GET /entries` 能拿到入口。
- [ ] 创建会话 → 发消息 → SSE 有增量 → 终态 → 历史可见。
- [ ] 停止生成后 SSE 结束，历史显示取消。
- [ ] 最近会话（`GET /conversations?agent_id=...`）顺序正确，带来源 `entry_id`/`channel`。
- [ ] 同一用户同一 Agent 的 WebChat 会话在 MSSWWEB 可见，反之亦然；不同用户不可见。
- [ ] 确认事件出现后，BFF 能展示选项并调用 resume 继续同一个 Run；Agent 撤权后 resume 被拒绝。
- [ ] 上传图片/PDF/Excel 成功；不支持类型/超限有明确错误。
- [ ] Agent 产出的文件能下载；`checksum` 错误时 404。
- [ ] BFF 断线重连带 `Last-Event-ID` 能续读，不重复/不丢正文。
- [ ] 401 与 503 分开处理，不把 503 当登录失效。

---

*文档结束*
