# MSSW 接口文档（我方视角）

> 关联：`02-requirements.md`、`04-user-journeys.md`、`03-open-questions.md`
>
> **前提（已确认）**：BFF 是后端服务，**直接适配我方现有接口**。我方不实现 `/internal/agent-provider/v1/*` 这套 URL，也不需要 BFF 对接文档。
>
> **因此本文档定义的是"我方要暴露给 BFF 的接口"，而不是"要照抄 BFF 的路径"。**

---

## 1. 设计原则

**以我方现有接口为基础，只做必要的最小增补。** BFF 侧负责把它的外部契约映射到我们的接口上。

三条原则：

| 原则 | 说明 |
|---|---|
| 复用优先 | 现有 `/api/v1/webchat/*` 的 16 个接口已构成完整能力集，能不改就不改 |
| 身份独立 | 新增 MSSW 身份依赖，与 CHAT 身份依赖**并列**，互不影响 |
| 路径同构 | MSSW 接口与 WebChat 路径形状一致，只换前缀；`entry_id` 保留在路径中（单入口下值固定，但保留可省掉 service 层改造，也多一道显式校验） |

---

## 2. 现有网关接口全貌

`apps/im-gateway/src/muad_im_gateway/api/webchat.py`，前缀 `/api/v1/webchat`，共 16 个。

> **两套数字别混淆**：这里是**现有 16 个接口**的全貌；BFF 需要的**8 个能力**见 §4（`/permissions` + 7 个会话/附件能力）。8 个能力覆盖 16 个接口里的一部分，不是一一对应。

| 行 | 方法 | 路径 | MSSW 是否可用 |
|---|---|---|---|
| :242 | GET | `/client-config` | 不需要 |
| :255 | GET | `/entries` | **不改**；MSSW 另开 `/permissions`（见 4.1） |
| :264 | POST | `/entries/{entry_id}/messages` | **需改造** |
| :284 | POST | `/entries/{entry_id}/conversations` | **需改造** |
| :297 | POST | `/entries/{entry_id}/attachments` | **需改造** |
| :315 | GET | `/entries/{entry_id}/conversations` | 可复用 |
| :329 | GET | `/conversations/{conversation_id}` | 可复用 |
| :341 | DELETE | `/conversations/{conversation_id}` | 可复用 |
| :353 | GET | `/conversations/{conversation_id}/messages` | **换依赖即可**（游标直接透传） |
| :378 | GET | `/notifications` | 不需要 |
| :398 | POST | `/notifications/{notification_id}/read` | 不需要 |
| :413 | POST | `/runs/{run_id}/cancel` | **直接复用** |
| :425 | POST | `/runs/{run_id}/resume` | 可复用 |
| :438 | GET | `/runs/{run_id}/events` | **换 Guard 后复用** |
| :547 | GET | `/notifications/stream` | 不需要 |
| :575 | GET | `/artifacts/{artifact_id}/content` | **直接复用** |

**核心判断**：现有 16 个接口一个都不改签名，也不改 service 层；MSSW 侧新增一套同构路由（只换前缀 `/api/v1/mssw`）+ 1 个 `/permissions`，其余可用或不需要。

---

## 3. 统一身份处理

**所有 MSSW 接口共用的前置链路**：

```
取 soc-token Cookie（BFF 服务端转发，非跨站，可正常送达）
  → MsswJwtVerifier 本地验签（RS256 + 算法白名单 + 必需 job_number）
  → claims["job_number"]
  → Console /internal/channel/resolve
  → MsswPrincipal { tenant_id, platform_user_id, job_number, agent_id, entry_id }
```

**新增独立依赖 `MsswPrincipalDep`，与 `ChatIdentityDep` 并列**：

| 项 | CHAT（现有，`deps.py:109-147`） | MSSW（新增） |
|---|---|---|
| 凭据载体 | `muad_chat_session` Cookie | `soc-token` Cookie |
| 校验位置 | Console `/internal/channel/chat-principal` | 网关**本地验签** + Console resolve |
| 身份标识 | `platform_user_id` | `job_number` → 映射为 `platform_user_id` |
| 流内复核 | 有（`ChatStreamGuard`） | **无** |
| 失败响应 | 401 | 401 |

两者读的 Cookie 名不同（`CHAT_SESSION_COOKIE` vs `soc-token`），因此**天然隔离**，不会互相干扰。

---

## 4. 接口定义

### 4.1 `GET /api/v1/mssw/permissions` —— 新增（判定逻辑复用 `/entries`）

**业务语义**：回答"当前登录用户能否使用本平台的智能客服"，即 `Q14` 确认的**布尔值**。用于 MSSW 侧"探索版"Tab 的可见性。

**同时承担第二个职责：下发 `entry_id`。** 这是 BFF 拿到 `entry_id` 的**唯一途径**——`entry_id` 由 Console `resolve` 返回，但 `resolve` 是网关↔Console 的内部接口，BFF 看不到；BFF 第一个调用的对外接口就是 `/permissions`，后续发消息/建会话/传附件的路径段都要用它。所以响应不能只回布尔。

**为什么不直接复用 `GET /api/v1/webchat/entries`**：

| 差异 | 说明 |
|---|---|
| 通道过滤写死 | `WebchatService.list_entries`（`webchat_service.py:206-215`）硬编码 `item.channel == WEBCHAT_CHANNEL`，MSSW 入口永远被过滤掉，返回空列表 |
| 身份类型不同 | 入参是 `ChatIdentity`，含 Console 会话的权威 `expires_at`；MSSW 的 JWT `exp` 恒为 `-1`，无此语义 |
| 响应形状不同 | `/entries` 返回 `WebchatEntryPage` 列表投影，BFF 要的是单个布尔 + 入口标识 |

**因此**：新增路由壳子，**判定谓词不重写**，从 `list_entries` 抽成共享函数。

**响应**：

```json
{ "visible": true, "entry_id": "<uuid>", "agent_id": "<uuid>" }
```

- `entry_id`：入口账号 UUID（`bot_account.id`），单入口下值固定，BFF 拿它拼后续路径
- `agent_id`：可选，便于 BFF 展示或调试；不参与它侧鉴权
- 不可见时：`{"visible": false, "entry_id": "<uuid>", "agent_id": "<uuid>"}`——**`entry_id` 仍然返回**

**为什么不可见也返回 `entry_id`**：它不是"该用户有权限的入口"，而是"本网关对接的那个入口"——由配置的 `bot_id` 决定，与该工号是否有权限无关。`ChannelResolveResponse`（`channel.py:150-157`）在 `bound=False` 时同样返回 `entry_id=bot.id`，口径一致。**提前下发不会泄露权限信息**（`visible` 已经说了有没有权限），反而省掉 BFF 一次往返。

**调用链**：

```
soc-token Cookie
  → MsswPrincipalDep（验签 + 工号映射 → resolve 拿到 entry_id/agent_id）
  → list_authorized_entries(entries, MSSW_CHANNEL)   ← 从 list_entries 抽出的共享判定
  → visible = bool(items)；entry_id/agent_id 取自 resolve 响应
```

**分支与结果**：

| 分支 | HTTP | 响应 |
|---|---|---|
| 验签失败 / 缺 `job_number` | 401 | `MSSW_JWT_INVALID` |
| **入口未配置或已停用**（`resolve` 抛 `BOT_NOT_FOUND`） | 404 | `MSSW_CHANNEL_NOT_CONFIGURED`——`entry_id` 拿不到，因为入口本身不存在 |
| 工号无映射（`bound=False`） | 200 | `{"visible": false, "entry_id": ..., "agent_id": ...}` |
| 有映射无授权 | 200 | `{"visible": false, "entry_id": ..., "agent_id": ...}` |
| 有映射有授权 | 200 | `{"visible": true, "entry_id": ..., "agent_id": ...}` |

**注意 1**：两种"可见性为 false"的分支（工号无映射 / 无授权）在 `visible` 上**不区分**，排查信息落日志、不进响应体（不泄露"该工号是否存在"）。

**注意 2**：**入口停用不能回 200 `visible:false`**。`resolve` 对未知/禁用的 bot 直接抛 `BOT_NOT_FOUND`（`channel_service.py:103-105`，注释「未知/禁用/已删除 bot 不是"未绑定用户"」），此时 `entry_id` 根本不存在，硬造一个假值下发会让 BFF 后续调用全部 404。所以单独给 404 + `MSSW_CHANNEL_NOT_CONFIGURED`，属于**配置故障**而非"用户不可见"，两类问题不该混在同一个响应里。

**约束**：不缓存；仅作 UI 可见性判断，**不作运行时授权依据**（发消息等真实接口每次都独立校验）。

**`ChatIdentity` 里的四样东西，MSSW 能给几样**（这决定了每个接口能改多深）：

| `ChatIdentity` 字段 | 用途 | MSSW 来源 |
|---|---|---|
| `tenant_id` | 所有查询的租户作用域 | 配置项（单入口）或 JWT 的 `tenant_id` |
| `principal.platform_user_id` | 归属校验、填 Runtime 入参 | Console `resolve` |
| `agents` | 越权判定（Agent 是否在授权集合内） | Console `resolve` 的 `list_authorized_agents` |
| `expires_at` | `ChatStreamGuard` 的长流停止点 | **无**（JWT `exp` 恒为 -1） |

**结论**：前三样都有，所以 8 个接口里 7 个能原样复用 service；只有第四样缺失，且只影响 SSE 一处（见 §4.3）。**不要给 `expires_at` 填假值**——那会让 Guard 的到期校验看起来还在生效，实际是失效的；正确做法是新 Guard 完全不读这个字段。

---

### 4.1.1 几个 ID 的来源与稳定性（最容易混淆，务必分清）

MSSW 通道会用到四类 ID，它们的产生时机、是否稳定、能不能被 BFF 复用各不相同：

| ID | 是什么 | 谁生成 | 稳定性 | BFF 怎么拿到 |
|---|---|---|---|---|
| `job_number` | MSSW 工号（5-7 位字母数字） | MSSW/mss-auth-go 签发 token 时写入 | 随人固定 | JWT payload（明文） |
| `entry_id` | `control.bot_account.id`，入口账号主键 | 管理员建 MSSW 通道时 `gen_random_uuid()` 生成一次 | **永不变**（除非删了通道重建） | `/permissions` 响应 |
| `conversation_id` | `runtime.conversation.id`，会话主键 | 每次新建会话时 `gen_random_uuid()` | **永不变**，创建后写死在会话行里 | 建会话/发消息的响应 |
| `run_id` | 一次受理的执行 ID | 每次提交消息时生成 | 永不变 | 发消息的 202 响应 |

**`entry_id` 为什么稳定**：它是 `StandardColumnsMixin` 的主键（`channel.py:17`、`base.py` 的 `server_default=text("gen_random_uuid()")`），建记录时生成一次；网关配置里的 `bot_id` 也是不变的，`resolve` 每次按同一个 `bot_id` 查同一条 `bot_account` 记录，返回同一个 `id`。**同一用户第 N 次进来，拿到的 `entry_id` 永远是同一个**——所以前文「`entry_id` 不透明显著降低凭空新建风险」的论证成立，也正是这个稳定性撑着。

唯一会让 `entry_id` 变的操作是：**删掉那条 `bot_account` 记录再新建**（即使 `bot_id` 填成一样，新记录的 `id` 是新的 UUID）。这属配置运维层级，正常不发生（`uq_bot_account_bot_id` 未删除时不允许同 `bot_id` 重复）。

**`conversation_id` 和 `entry_id` 的关系**：两者不是同一层级。`entry_id` 是「哪个入口」，全局就那一个；`conversation_id` 是「这个入口下、这个用户、这个 Agent 的一次具体对话」，每开一个新会话就有一个新值。`Conversation` 表里同时存了 `channel_account_id`（即 `entry_id`）、`user_id`、`agent_id`（`runtime.py:37-47`），三者共同界定会话的归属。

**历史会话为什么不依赖 `entry_id` 稳定**：历史读取按 `(tenant_id, user_id, conversation_id)` 定位（`_resolve_scope`，`webchat_service.py:445-457`），`conversation_id` 一旦创建就写在会话行里、永不改变，且该行还记录着创建当时的 `channel_account_id`。所以即便极端情况下 `entry_id` 变了，旧会话的读取路径也不会错。

### 4.2 `POST /api/v1/mssw/entries/{entry_id}/messages` —— 加路由

**现状**：`POST /api/v1/webchat/entries/{entry_id}/messages`（`webchat.py:264`，202）→ `WebchatService.submit_message`（`webchat_service.py:224-271`）。

**现有请求契约**（`WebchatMessageRequest`，`contracts/webchat.py:199-211`）：

```json
{
  "conversation_id": "uuid | null",
  "message_id": "string",
  "text": "string",
  "upload_ids": ["uuid"]
}
```

校验：`text` 与 `upload_ids` 至少有一个；`upload_ids` 不得重复。

**现有响应 202**（`WebchatMessageAccepted`）：

```json
{
  "conversation_id": "uuid",
  "run_id": "uuid",
  "status": "CREATED",
  "execution_status": "..."
}
```

**关键事实**：这个方法**已经是 202 受理语义**，注释原话是"受理事务已给出状态与阶段投影：202/幂等重放/自动续跑都走这一条，不再 get_run"。**Runtime 不需要改。**

**改造点**：

1. 新增路由 `POST /api/v1/mssw/entries/{entry_id}/messages`（与 WebChat 同构，**保留 `entry_id` 路径段**）
2. 身份依赖 `ChatIdentityDep` → `MsswPrincipalDep`
3. 内部调用不变：`self._messages.admit_sync(..., principal_context=SyncPrincipal(...))`

**`entry_id` 从哪来**（Q15 已定）：Console `resolve` 响应直接返回 `entry_id=bot.id`，随 `platform_user_id` 一起进入 `MsswPrincipal`；**BFF 侧则由 `/permissions` 响应拿到**（见 §4.1）。`bot_id` 来自网关配置项（MSSW BFF 只有一个上游服务，单入口）。

**为什么 service 一行不用改**：`submit_message`（`webchat_service.py:224-271`）用到 `ChatIdentity` 的只有三处——`_resolve_entry(identity, entry_id)` 取入口与 Agent、`identity.agents` 做越权判定、`identity.principal.platform_user_id` 填 `InboundEvent`。这三样 `MsswPrincipal` 都能提供。

**被挡住的点（这条链上有 3 处，别只改第一处）**：

| 次序 | 位置 | 写死的内容 |
|---|---|---|
| 1 | `submit_message`（`webchat_service.py:235-246`） | `InboundEvent(channel=WEBCHAT_CHANNEL)` |
| 2 | `submit_message`（`:257`） | `adapter=WEBCHAT_CHANNEL_IDENTITY` |
| 3 | **`_run_request`（`webchat_core.py:69`）** | `ChannelContext(type=WEBCHAT_CHANNEL)` ← **真正写进 Runtime 的那一处** |

若不参数化，MSSW 的消息会被记成 WebChat 通道——**能落库但通道类型错**，属于隐蔽错误。而只改 1、2 不改 3 是完全无效的：`admit_sync` 最终由 `_run_request` 组装 `RunRequest`，通道取自那里。

---

### 4.3 `GET /api/v1/mssw/runs/{run_id}/events` —— 复用（必须换 Guard）

**现状**：`webchat.py:438`。

**现有行为**：

- 游标：`_resume_cursor(after_seq, last_event_id)` 取大值（`webchat.py:208-219`）
- SSE 帧：`id: <seq>`、`event: <type>`
- 支持 `Last-Event-ID` 头

**唯一改造**：`ChatStreamGuard` → MSSW 版本。

| 项 | CHAT Guard | MSSW Guard |
|---|---|---|
| 建连校验 | 一次 | 一次（验签 + 映射） |
| 流内复核 | 每 `chat_auth_recheck_sec` 秒向 Console 复核 | **不做**（无可依据的到期时刻） |
| 断流条件 | `expires_at` 到点 | **单轮时长上限** + 客户端断开 |
| 重连 | 前端自动续连 | **BFF 重连即重新验签** |

**为什么不能直接复用**：`ChatStreamGuard.rechecked()`（`chat_principal.py:184-207`）依赖 `identity.expires_at`，而 MSSW 的 `exp` 恒为 -1。

**签发方的过期语义（`mss-auth-go`）**：真正判断在 Redis key 存在性上，`exp` 不承载任何信息。

| 环节 | 行为 | 位置 |
|---|---|---|
| 登录写入 | `SetLoginJwt(redisKey, token, LoginJwtTokenExpire)` | `commonlogic.go:271` |
| 每次校验 | `CheckLoginJwt` 查 Redis，key 不在即失效 | `authchecklogic.go:305-311` |
| **滑动续期** | 校验通过后按概率 `RefreshLoginJwt(LoginJwtTokenExpire)` 重置 TTL | `authchecklogic.go:313-322` |
| 注销 | `ClearAgentJwt` 删 Redis key | `types.go:14-18` |

**推论**：会话是**滑动有效**的，只要用户活跃就永不到期；过期状态是活数据，存在 MSSW 的 Redis 里，**我方查不到**。因此：

- 无法判断“这枚 token 是否已被注销”；
- 无法做到与签发方**等价**的到期判定，只能做**近似**。

**MSSW Guard 的判定策略（只有资源层，鉴权前移给 BFF）**：

| 层 | 判据 | 作用 |
|---|---|---|
| **连接级到期** | 单轮流有一个时长上限，到期正常收尾断流 | **纯资源安全策略**；与 token 是否有效无关 |

**为什么鉴权不在网关做**：上游只有 BFF 一个服务，`soc-token` 由 MSSW 签发，**token 是否仍有效/未吊销由 BFF 在转发前判定**（`02` §3.3.1）。BFF 挡掉失效 token，到达网关的都是有效请求；网关只负责单连接不吃满资源。

**不做的事**：

- 不设绝对时间窗——`time` 是**签发**时刻，同一枚 token 每次请求都一样，活跃用户超 2 小时会被误杀，且分不开“活跃用户”与“旧 token 重放”
- 不向上游查 token 有效性（责任在 BFF）
- 不在网关维护本地续期状态（那会把无状态验签变成有状态会话管理，与网关零数据库依赖冲突，最终等于复制一套 MSSW 的 Redis 会话）

**代价要说清**：WebChat 能做到“会话被踢就立刻断流”，MSSW 做不到——但这不落在我方：BFF 在转发/重连时已挡掉失效 token。我方负责单个连接的资源寿命。这是 `exp = -1` 的直接后果，不是实现缺陷。

---

### 4.4 `POST /api/v1/mssw/runs/{run_id}/cancel` —— 直接复用

**现状**：`webchat.py:413` → `WebchatService.cancel_run`。

语义完全匹配：幂等、已终态返回当前终态、归属不符 404。

**为什么能零改动**：`cancel_run(identity, run_id)`（`webchat_service.py:305-309`）只做两件事——`self._owned_run(identity, run_id)` 比对 `platform_user_id` 核归属，再转 Runtime 取消。**不读 `expires_at`，不用 `entry_id`**。

**改造**：仅换身份依赖。

---

### 4.5 `GET /api/v1/mssw/conversations/{conversation_id}/messages` —— 直接复用（无游标适配）

**现状**：`webchat.py:353`。

**现有参数**：`page=1&page_size=20&through_seq=100`

**现有约束**（`WebchatHistoryQuery`）：第 2 页起**必须**带 `through_seq`，否则 pydantic 校验失败。

**现有响应**：`items[]` + `through_seq`。

**适配结论**（Q20 已定）：**不做任何游标转换**。BFF 直接使用我方的 `page` + `page_size` + `through_seq`；若它的对外契约需要 `cursor`，由 BFF 自己编解码。

| 方向 | 约定 |
|---|---|
| 入 | BFF 透传 `page` / `page_size` / `through_seq` |
| 出 | 网关返回 `items[]` + `through_seq`，BFF 自行封装成它需要的形状 |

**为什么能零改动**：`list_messages`（`webchat_service.py:380`）只做归属校验与分页，入参是 `page` / `page_size` / `through_seq`，不依赖 `expires_at` / `entry_id`。

**本节复用度 95%**——网关侧只换身份依赖与路由前缀，分页逻辑一行不改。

> 注意与**事件流游标**区分：这里说的是历史分页（`page`+`through_seq`），无异常分支。事件流的三类游标异常（`CURSOR_EXPIRED` / `CURSOR_AHEAD` / `CURSOR_RUN_MISMATCH`）是另一套，处理要求见 §4.3 与 `02` 的 R7。

---

### 4.6 `POST /api/v1/mssw/entries/{entry_id}/conversations` —— 加路由

**现状**：`webchat.py:284`，返回 201。

**改造**：新增路由 `POST /api/v1/mssw/entries/{entry_id}/conversations`（保留 `entry_id`），身份换 `MsswPrincipalDep`。

**必须改 service 的地方**：`create_conversation`（`webchat_service.py:274-303`）里写死了

```
channel=ChannelContext(type=WEBCHAT_CHANNEL, entry_id=ref.entry_id, bot_id=ref.bot_id, ...)
```

MSSW 建的会话若不改这里，会被记为 WebChat 通道。需把通道名参数化（新适配器传入或作为参数）。

**幂等**（Q19 已定）：**以我方为准，不由 BFF 传 `conversation_id`**。

Runtime `_create_conversation`（`run_service.py:1751-1765`）用 `Idempotency-Key` + 指纹去重，指纹的五个判别键全是我方值：

| 指纹键 | 来源 |
|---|---|
| `tenant_id` | `SharedSettings` 租户 |
| `agent_id` | 入口快照（`bot_account.agent_id`） |
| `platform_user_id` | `MsswPrincipal`（工号映射后的用户 UUID） |
| `entry_id` | 路径段 |
| `external_thread_id` | `ChannelContext.external_thread_id` |

命中重放则直接返回既有会话（`:1762-1765`），不建第二个。**BFF 无需提供任何 ID**，只需重试时原样回传 `Idempotency-Key`；成功响应里拿我方 `conversation_id` 存下来即可。

> 原设计"按平台 `conversation_id` 去重"**已作废**：那个 ID 是我方 `gen_random_uuid()` 生成的（§4.1.1），BFF 手上本就没有，绕一圈反而需要它维护映射。

---

### 4.7 `POST /api/v1/mssw/entries/{entry_id}/attachments` —— 加路由

**现状**：`webchat.py:297` + `WebchatUploadService`。

**现有返回**（`_upload_view`，`webchat_history.py:102-114`）：`upload_id` / `expires_at` / `checksum`。

**改造**：新增路由 `POST /api/v1/mssw/entries/{entry_id}/attachments`（保留 `entry_id`）。

**字段名**：`upload_id` 是否需要改名为 `provider_upload_id` 由 BFF 适配层处理，**我方不改**——保持现有返回不变。

**为什么能复用**：上传走 `resolve_authorized_entry` 做入口校验后转 Runtime，不依赖 `expires_at`。

**不存在"`external_user_id` 填哪个"的问题**（已核实）：上传链路**根本不传 `external_user_id`**。`WebchatUploadService.upload`（`webchat_uploads.py:121-152`）构造的 `WebchatUploadRegistration` 只有身份三件套 `user_id`（= `platform_user_id`）/ `agent_id` / `entry_id`（契约见 `contracts/webchat.py:236-244`，`extra="forbid"`，没有该字段位）。所以 MSSW 上传与 WebChat 完全同构，**不需要任何口径决策**。

> `external_user_id` 只出现在发消息/建会话的 `ChannelContext` 里（`webchat_service.py:243`、`:289`），两处都填 `platform_user_id`——MSSW 同样照填即可，无需用工号。工号只在 `channel_identity.identity_key` 里作为映射键存在，不进入 Runtime 入参。

---

### 4.8 `GET /api/v1/mssw/artifacts/{artifact_id}/content` —— 直接复用

**现状**：`webchat.py:575` → `download_artifact`。

**关键约束**（概设 :1063）：**禁止 302 重定向**，必须流式代理。现有实现已满足。

**归属校验**：Runtime 的 `resolve_artifact`（`webchat_history.py:117-151`）已按 `user_id` + `conversation_id` + `checksum` 定位，跨用户/跨 Agent 一律 404。**可直接复用**。

**为什么能零改动**：按 `artifact_id` 定位，不用 `entry_id`，不读 `expires_at`。

---

## 5. 需要新增的代码

| 文件 | 内容 | 规模 |
|---|---|---|
| `application/mssw_jwt.py` | JWT 验签器 | 中 |
| `application/mssw_principal.py` | `MsswPrincipalDep`（验签 + 工号映射） | 小 |
| `application/mssw_stream_guard.py` | 只做单轮连接寿命的 Guard（不复核、不查上游） | 小 |
| `channels/mssw.py` | MSSW 通道适配器（参照 `webchat.py`） | 小 |
| `api/mssw.py`（或 `api/webchat.py` 增补） | `/permissions` + 7 个同构路由（3 个需改 service，4 个纯复用）+ 身份依赖 | 中 |
| `channels/registry.py` | 注册适配器 | 一行 |
| `packages/contracts/.../enums.py` | 通道标识 | 一行 |
| `config/api-messages.yaml` | 错误码文案 | 数条 |

**不需要改动**：Runtime 全部接口、Console `ChannelService.resolve`、现有 WEBCHAT / WECOM 代码路径。

**注意 ① 必须改**：`resolve_authorized_entry`（`webchat_service.py:475-497`）里写死了 `candidate.channel == WEBCHAT_CHANNEL`，MSSW 入口会被判 404。MSSW 路由需要走自己的通道校验（新适配器或参数化通道名）。

**但它不是唯一一处**。`WEBCHAT_CHANNEL` 的写死点共 **6 处**（第 ⑥ 处最隐蔽）：

| # | 位置 | 影响 |
|---|---|---|
| ① | `list_entries`（`webchat_service.py:214`） | MSSW 入口被过滤，快照为空 |
| ② | `resolve_authorized_entry`（`:489`） | MSSW 入口一律 404 |
| ③ | `submit_message` 的 `InboundEvent(channel=...)`（`:237`） | 事件标错通道 |
| ④ | `submit_message` 的 `adapter=WEBCHAT_CHANNEL_IDENTITY`（`:257`） | 受理走 WebChat 适配器身份 |
| ⑤ | `create_conversation` 的 `ChannelContext(type=...)`（`:286`） | 会话记成 WebChat |
| **⑥** | **`_run_request`（`webchat_core.py:69`）** | **消息真正写进 Runtime 的通道**；`submit_message` 只是构造 `InboundEvent` 交给 `admit_sync`，组装 `RunRequest.channel` 在 `_run_request`。**漏改 ⑥，③④ 白改** |

**①② 有依赖**：① 产出的入口快照是 ② 的输入，只改 ② 仍会 404。详见 `02` §3.6。

---

## 6. 复用度总结

| 能力 | 复用度 | 说明 |
|---|---|---|
| Run 受理（202） | **100%** | `submit_message` 语义完全一致，只加同构路由 |
| 取消 | **100%** | 仅换身份依赖 |
| 附件下载 | **100%** | 直接可用 |
| 新建会话 | **100%** | 只加同构路由 |
| 附件上传 | **100%** | 只加同构路由，返回不变 |
| SSE 事件流 | **90%** | 仅换 Guard |
| 入口查询 | **90%** | 新增 `GET /api/v1/mssw/permissions`（布尔），判定谓词从 `list_entries` 抽出复用，仅通道参数与响应投影不同 |
| 历史分页 | **95%** | 直接透传 `page`+`through_seq`，BFF 自行封装 |

**结论**：8 个能力里 3 个零改动（取消、附件下载、历史分页），3 个只加同构路由（发消息、建会话、传附件，service 层一行不改），1 个换 Guard（SSE），1 个新增但判定复用（权限）。**真正的工作量集中在 JWT 验签（R2）**——8 个接口本身没有需要重写的能力。
