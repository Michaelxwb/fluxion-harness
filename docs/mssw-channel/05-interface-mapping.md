# MSSW 接口映射（我方视角）

> **需求基线**：`.code-flow/tasks/2026-11-13/mssw-external-im-channel/mssw-external-im-channel.backend.design.md`（API-01 ~ API-26）
> 关联：`02-requirements.md`、`04-user-journeys.md`

**前提**：BFF 是后端服务，**直接适配我方现有接口**。本文档定义"我方要暴露给 BFF 的接口"。

---

## 1. 设计原则

| 原则 | 说明 |
|---|---|
| 复用优先 | 现有 `/api/v1/webchat/*` 已构成完整能力集 |
| 身份独立 | 外部断言认证与 CHAT 身份依赖并列，互不影响 |
| 路径同构 | `/api/v1/msswwweb/*` 与 WebChat 路径形状一致，只换前缀 |
| 入口非边界 | `entry_id`/`channel` 只做来源标识；访问边界是 user + Agent |
| 通用化 | 平台差异只在配置（`identity_keys`、因子）与认证适配层，不复制 handler |

---

## 2. 对外接口清单（BFF 调用）

前缀 `/api/v1/msswwweb`，共 15 个。

| ID | 方法 | 路径 | 说明 |
|---|---|---|---|
| API-01 | GET | `/identity` | **永远查 PG**；返回 `allowed`/`reason`/`grants`/`agents`；成功后写 Redis |
| API-02 | GET | `/client-config` | 复用；装在 `bff-agent-integration.md` §4.2 对 BFF 有明确定义 |
| API-03 | GET | `/entries` | 当前用户可用的入口授权 + Agent |
| API-04 | POST | `/entries/{entry_id}/conversations` | 入口作为**本次会话的来源** |
| API-05 | GET | `/entries/{entry_id}/conversations` | `entry_id` 只用于鉴权与归属，**不是过滤条件**；按 user+Agent + `channel_types=WEBCHAT,MSSWWEB` 聚合（Web 面跨入口共享，企微不出现）；item 带 `entry_id`/`has_unread`（无 `channel`） |
| API-06 | POST | `/entries/{entry_id}/messages` | 入口作为**本次 Run 的来源** |
| API-07 | GET | `/conversations/{id}` | user + agent 核属；**不做** entry/channel 断言 |
| API-08 | DELETE | `/conversations/{id}` | user 级；沿用现有幂等 delete |
| API-09 | GET | `/conversations/{id}/messages` | user + agent；`page`+`page_size`+`through_seq` |
| API-10 | POST | `/conversations/{id}/read` | 带**通知水位**；只标已看到 |
| API-11 | POST | `/entries/{entry_id}/attachments` | 入口作为来源 |
| API-12 | POST | `/runs/{run_id}/cancel` | user 级 |
| API-13 | POST | `/runs/{run_id}/resume` | user + agent；入口不是边界 |
| API-14 | GET | `/runs/{run_id}/events` | user + agent；**每事件复核**到期与授权 |
| API-15 | GET | `/artifacts/{id}/content` | user + agent；不做 channel 断言 |

## 3. 内部接口

### 3.1 网关 → Console

| ID | 方法 | 路径 | 说明 |
|---|---|---|---|
| API-16 | POST | `/internal/channel/external-principal` | **验签 + 身份解析的唯一入口** |

### 3.2 Console → Runtime

| ID | 方法 | 路径 | 说明 |
|---|---|---|---|
| API-20 | POST | `/internal/webchat/conversations/{id}/read` | 标记已读（带通知水位） |
| API-25 | GET | `/internal/webchat/entries/{entry_id}/conversations` | 会话列表；`entry_agnostic=true` + `channel_types`（Web 面传 `WEBCHAT,MSSWWEB`）时按 user+Agent + 来源通道聚合（入口只作来源） |

> API-20 / API-25 之所以走内部接口而不是让 BFF 直连 Runtime：身份解析、缓存与授权判定全在 Console，Runtime 只接受已核属的调用。

## 4. 管理面接口（Console 前端）

| ID | 方法 | 路径 | 说明 |
|---|---|---|---|
| API-17 | GET | `/api/v1/external-platforms` | 返回 `platform_key`/`channel`/`identity_keys`（**不含因子**） |
| API-18 | PUT | `/api/v1/users/{user_id}/external-identities` | 新建用户级外部身份 |
| API-19 | GET | `/api/v1/external-platforms/{platform_key}/entries` | 该平台的可用入口 |
| API-21 | PUT | `/api/v1/users/{user_id}/external-identities/{identity_id}` | **编辑身份值**（重算 hash，历史归属不变） |
| API-22 | POST | `/api/v1/users/{user_id}/external-identities/{identity_id}/grants` | 授权入口 |
| API-23 | DELETE | `/api/v1/users/{user_id}/external-identities/{identity_id}/grants/{entry_id}` | **解绑入口**（只删 grant，身份保留） |
| API-24 | GET | `/api/v1/users/{user_id}/external-identities` | 身份列表 + 每个身份的 grants |
| API-26 | GET | `/api/v1/auth-cache-sync` | 当前租户授权缓存同步状态与可安全展示的操作摘要 |
| — | — | 创建 MSSWWEB 入口 | 复用现有入口创建，加 `SECRETLESS_CHANNELS` |

---

## 5. 统一身份处理

```
Cookie: soc-token=<JWT>
  → 网关原样转发（不验签、不持因子）
  → Console: 查平台配置 → 验 RS256 + AES(aud/iss) + claim
  → 按 identity_keys 取 claim → 排序 → 规范 JSON → sha256
  → 查 external_identity(tenant, platform_key, identity_hash)
  → 命中：platform_user_id + grants + agents + auth_revision
  → 身份命中但该入口无 grant：allowed=false + NO_ENTRY_GRANT（主体仍返回，不丢）
  → 未命中：allowed=false + UNBOUND（不伪造主体）
```

> **身份 ≠ 可访问**：`external_identity` 命中只说明「这个 `expert_id`+`tenant_id` 是我方某个用户」；能否在当前入口使用，取决于 `entry_access_grant` 是否有该 `(identity, entry)` 组合。管理员录入身份后必须再「添加入口」，否则该入口下为 `NO_ENTRY_GRANT`。

**各段职责**

| 段 | 职责 | 不做什么 |
|---|---|---|
| im-gateway | 转发 token、路由、SSE、6 处通道参数化 | 不验签、不持因子、不查库 |
| console-platform | 验签、身份解析、绑定、缓存写入 | 不处理会话/SSE |
| agent-runtime | 会话、消息、事件流、附件 | 不管身份 |

---

## 6. 逐条对照：现有 WebChat 接口 → MSSWWEB

`apps/im-gateway/src/muad_im_gateway/api/webchat.py`，前缀 `/api/v1/webchat`。

| 行 | 方法 | 路径 | MSSWWEB | 改造点 |
|---|---|---|---|---|
| :242 | GET | `/client-config` | ✅ 复用 | 换身份前缀 |
| :255 | GET | `/entries` | ✅ 复用 | ① ② 通道参数化 |
| :264 | POST | `/entries/{entry_id}/messages` | ✅ 复用 | ③ ④ ⑤ + ⑥ |
| :284 | POST | `/entries/{entry_id}/conversations` | ✅ 复用 | ⑤ + ⑥ |
| :297 | POST | `/entries/{entry_id}/attachments` | ✅ 复用 | 入口参数化 |
| :315 | GET | `/entries/{entry_id}/conversations` | ⚠️ **改为跨入口聚合** | 新模型下不再按入口过滤 |
| :329 | GET | `/conversations/{conversation_id}` | ✅ 复用 | 核属改为 user + agent |
| :341 | DELETE | `/conversations/{conversation_id}` | ✅ 复用 | 同上 |
| :353 | GET | `/conversations/{conversation_id}/messages` | ✅ 复用 | 核属 + 游标直传 |
| :378 | GET | `/notifications` | ❌ 不需要 | 通知并入会话列表 `has_unread` |
| :398 | POST | `/notifications/{id}/read` | ❌ 不需要 | 改为会话级 `/read` |
| :413 | POST | `/runs/{run_id}/cancel` | ✅ 复用 | — |
| :425 | POST | `/runs/{run_id}/resume` | ✅ 复用 | — |
| :438 | GET | `/runs/{run_id}/events` | ✅ 复用 | **换 Guard**（每事件复核） |
| :547 | GET | `/notifications/stream` | ❌ 不需要 | — |
| :575 | GET | `/artifacts/{artifact_id}/content` | ✅ 复用 | — |

### 6.1 6 处通道名写死

| # | 位置 | 现状 | 改动 |
|---|---|---|---|
| ① | `webchat_service.py:214` `list_entries` | 过滤 `channel == "WEBCHAT"` | 按传入通道名过滤 |
| ② | `webchat_service.py:489` `resolve_authorized_entry` | 同上 | 同上 |
| ③ | `webchat_service.py:237` `InboundEvent` | 写死 | 传入通道名 |
| ④ | `webchat_service.py:257` adapter | 写死 | 传入通道名 |
| ⑤ | `webchat_service.py:286` `ChannelContext` | 写死 | 传入通道名 |
| ⑥ | **`webchat_core.py:69` `_run_request`** | 写死 | **传入通道名（最隐蔽，决定最终落库）** |

> ①→② 有顺序依赖：① 产出的入口快照是 ② 的输入，只改 ② 仍会 404。

### 6.2 改造点细节

| 改造点 | 说明 |
|---|---|
| 会话列表跨入口聚合 | 保留 `GET /entries/{entry_id}/conversations`，新增 `entry_agnostic=true`：按 user+Agent 聚合，入口只作鉴权与来源，不再作为可见性过滤 |
| 核属改为 user + agent | Runtime `_load_run_in` 只按 `id + tenant_id` 查，**不带 user_id**，调用方必须补 |
| SSE 每事件复核 | 现有 `ChatStreamGuard` **不可复用**（`chat_principal.py:184-207` 只在建连校验） |
| 通知模型 | 旧 `/notifications` 系列不暴露；红点并入会话列表 `has_unread` + 会话级 `mark-read` |

---

## 7. 关键契约

### 7.1 幂等

| 接口 | 机制 |
|---|---|
| `POST /messages` | `Idempotency-Key` + 请求指纹（五键全为我方值） |
| `POST /conversations` | 同上；**BFF 不传 `conversation_id`** |

```python
# run_service.py:1751-1765
fingerprint = submission_fingerprint(
    endpoint=ENDPOINT_CREATE_CONVERSATION,
    key_payload={"tenant_id":..., "agent_id":..., "platform_user_id":...,
                 "entry_id":..., "external_thread_id":...},
)
if idempotency_key:
    replay = await self._find_conversation_replay(...)
    if replay is not None:
        return replay
```

### 7.2 游标

| 面 | 入参 | 出参 |
|---|---|---|
| SSE | `after_seq` + `Last-Event-ID`（取大值） | 帧 `id: <seq>` |
| 历史分页 | `page` + `page_size` + `through_seq` | `items[]` + `through_seq` |
| 会话已读 | `{through_seq}`（**通知水位**） | — |

**消息序号与通知序号是两套序列**，不能互相比较。

### 7.3 附件

| 接口 | 契约 |
|---|---|
| 上传 | `multipart/form-data`，返回 `upload_id` |
| 发消息 | 带 `upload_ids[]` |
| 下载 | `GET /artifacts/{id}/content`，**禁止 302**（网关流式代理） |

> **`external_user_id` 不需要 BFF 传**：`WebchatUploadRegistration` 只有 `user_id`/`agent_id`/`entry_id` 三件套。

### 7.4 错误码

| 码 | HTTP | 场景 |
|---|---|---|
| `EXTERNAL_ASSERTION_INVALID` | 401 | 缺失/签名错/格式错/`aud`/`iss`/身份 key 缺失 |
| `EXTERNAL_ASSERTION_EXPIRED` | 401 | 超出最大会话年龄 |
| `EXTERNAL_ASSERTION_UNBOUND` | 403 | 身份未绑定 |
| `EXTERNAL_ASSERTION_UNAVAILABLE` | 503 | Secret 未配置/损坏 |
| `CURSOR_EXPIRED` | 409 | 游标超出保留窗口 |
| `CURSOR_AHEAD` | 409 | 水位超前 |
| `IDENTITY_KEYS_MISMATCH` | 400 | 绑定 JSON key 集合不符 |
| `IDENTITY_ALREADY_BOUND` | 409 | 身份已绑他人 |
| `BOT_ID_EXISTS` | 409 | `bot_id` 重复 |
| `AUTH_CACHE_SYNC_PENDING` | 503 | 缓存同步未完成，失败关闭 |
| `COMMON_NOT_FOUND` | 404 | 非本主体/资源不存在（不泄露存在性） |

**`/identity` 的 `reason` 枚举**（`allowed=false` 时返回，HTTP 仍为 200）：

| reason | 含义 |
|---|---|
| `UNBOUND` | 身份未绑定 |
| `USER_DISABLED` | 平台用户停用 |
| `NO_ENTRY_GRANT` | 入口未授权**或**入口已停用（对外同一个原因） |
| `NO_AGENT_GRANT` | 入口绑定的 Agent 已被删除 |
| `AGENT_DISABLED` | 入口绑定的 Agent 已停用 |

### 7.5 通道能力分组

| 常量 | `MSSWWEB` 是否包含 | 理由 |
|---|---|---|
| `BUILTIN_CHANNEL_NAMES` | ✅ | 通道标识 |
| `LOCAL_STREAM_CHANNELS` | ✅ | 产物直接落 canonical 流；否则 `attachments/tools.py:739-744` 抛 `ATTACHMENT_WRITE_UNAVAILABLE` |
| `OUTBOUND_CHANNELS` | ❌ | MSSW 是 BFF 拉取模式，无主动投递能力 |
| `SECRETLESS_CHANNELS` | ✅ | 入口无 bot secret |
