# MSSW 通道 — 用户旅程

> **需求基线**：`.code-flow/tasks/2026-11-13/mssw-external-im-channel/`
> 配套：`02-requirements.md`（R1-R8）、`05-interface-mapping.md`（接口契约）

---

## 0. 角色与信任边界

| 角色 | 是谁 | 能做什么 |
|---|---|---|
| MSSW 用户 | 持有工号的内部员工 | 在 MSSW 页面里和 Agent 对话 |
| MSSW BFF | 上游后端服务 | 转发请求，**转发前校验 token 有效性** |
| Console 管理员 | 我方运维/管理员 | 创建入口、绑定外部身份、授权 Agent |
| 我方系统 | im-gateway + Console + Runtime | 验签、解析身份、执行对话 |

**三个信任边界**：

1. **BFF → 我方**：BFF 是唯一上游，`soc-token` 由它转发。我方**不判吊销**，只验签名与 claim
2. **网关 → Console**：网关只转发 token，不持因子。**验签与身份解析全在 Console**
3. **身份 → 数据**：`entry`/`channel` 只做来源标识，**访问边界是 user + Agent**

---

## 1. 身份校验的核心约定

| 约定 | 内容 |
|---|---|
| 凭据 | 只有 `Cookie: soc-token=<JWT>`，**不需要** `X-Chat-Context` |
| 验签位置 | Console（网关不持因子） |
| 身份键 | 平台配置 `identity_keys`，MSSW 为 `["expert_id","tenant_id"]`（`expert_id` 即 `_id`） |
| 哈希 | 按 key 排序 → 规范 JSON → `sha256` → `identity_hash` |
| 查询 | `external_identity(tenant, platform_key, identity_hash)` |
| 未命中 | `UNBOUND`，**不伪造主体** |
| 不做 | 不自动建档、不实现 AES 解密、不判吊销 |

---

## 2. 旅程 0：通道开通（管理员一次性动作）

### 阶段 0-A：通道上线（研发/部署）

| 步骤 | 动作 | 验证 |
|---|---|---|
| 1 | `MSSWWEB` 加入 `BUILTIN_CHANNEL_NAMES` + `LOCAL_STREAM_CHANNELS`（不进 `OUTBOUND_CHANNELS`） | 硬断言测试通过 |
| 2 | `CHANNEL_TOKENS` 加 `"msswwweb"` | 架构机检能扫到新通道 |
| 3 | 6 处通道名参数化 | 集成测试断言 `RunRequest.channel` |
| 4 | Console 配置平台因子（Secret 挂载） | `/identity` 可返回 200 |
| 5 | nginx 加 `location /api/v1/msswwweb`，关 SSE 缓冲 | SSE 不被缓冲 |

**fail-closed 要求**：Secret 缺失/损坏只让 MSSWWEB 返回 503，**不影响启动与其他通道**。

### 阶段 0-B：创建入口（Console 管理员）

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 打开「通道管理」→「新建入口」 | — |
| 2 | 选择通道 `MSSWWEB` | 表单自动隐藏 bot secret 字段（`SECRETLESS_CHANNELS`） |
| 3 | 填写 `bot_id`（**全局唯一**） | 重复 → `BOT_ID_EXISTS` |
| 4 | 绑定 Agent | — |
| 5 | 保存 | 生成 `entry_id` = `bot_account.id` |

> **与上一版的差别**：`bot_id` 不再由网关配置项指定，改由管理员在 Console 填写。

### 阶段 0-C：绑定用户身份（关键）

**这一步决定用户首访能否通过。**

#### 前置：从哪里拿到身份值

管理员手上需要 `expert_id`（24 位 ObjectId）和 `tenant_id`。

- **第一期做法**：管理员从 MSSW 侧查到该员工的 `expert_id`（24 位 ObjectId）与 `tenant_id`，在 Console 手工逐条录入；不做名单导入。
- **录入提示**：`_id` 是 24 位 hex，容易抄错；填错只表现为「未绑定」（`UNBOUND`），不报错。Console 表单可按 `identity_field_labels` 给出格式提示（如「专家 ID（24 位）」），并在保存前做长度/hex 校验。

#### 操作步骤

| 步骤 | 动作 | 校验 |
|---|---|---|
| 1 | 在 Console 建平台用户（现有功能） | 常规用户创建 |
| 2 | 打开用户详情 →「外部身份」→「新建」 | — |
| 3 | 填写身份 JSON（key 集合须与 `identity_keys` 一致） | `IDENTITY_KEYS_MISMATCH` |
| 4 | 提交 | 已绑他人 → `IDENTITY_ALREADY_BOUND` |
| 5 | 在该身份下「添加入口」→ 选 MSSWWEB 入口 | — |
| 6 | 授权 Agent | — |

#### 两张表的分工

| 表 | 这一步产生什么 |
|---|---|
| `external_identity` | 「这个 `_id`+`tenant_id` 对应哪个 `platform_user`」 |
| `entry_access_grant` | 「这个身份能用哪个入口」 |

**解绑只删 grant**，身份保留 → 重绑后历史会话仍在。

#### 管理员可读性问题

`expert_id`、`tenant_id` 是 claim 名，管理员看不懂。需在平台配置里补：

```json
"identity_field_labels": { "expert_id": "专家 ID（24 位）", "tenant_id": "租户 ID" }
```

前端按 label 渲染输入框，提交时用 claim 名。**这是通用化的必要配套**——否则每接一个平台，管理员都得先学一套字段名。

### 阶段 0-D：联调验证

| 项 | 验证方式 |
|---|---|
| 验签通过 | 用真实 token 调 `/identity`，返回 200 |
| 身份命中 | `platform_user_id` 非空 |
| 入口授权 | `grants[].allowed == true` |
| SSE 不被缓冲 | 观察事件逐条到达 |

---

## 3. 旅程 A：首次进入 — 权限探测与客户端配置

```
BFF → GET /api/v1/msswwweb/identity
      Cookie: soc-token=<JWT>
   → GW → Console: POST /internal/channel/external-principal
   → Console 验签 → 身份解析 → 查 grants + Agent 授权
   → 返回 {allowed, reason, platform_user_id, grants[], agents[]}

BFF → GET /api/v1/msswwweb/client-config
      Cookie: soc-token=<JWT>
   ← 200 {上传大小上限、允许的附件类型、分页上限等}
```

`client-config` 与 `identity` 同属"进入时准备"：BFF 拿到配置后才能正确限制上传大小、渲染分页。它**不返回任何机密**（公钥、salt、issuer 都不在里面）。字段定义见 `bff-agent-integration.md` §4.2。

**分支表**

| 状态 | 响应 | BFF 行为 |
|---|---|---|
| 200 | `{allowed: true, grants: [...], agents: [...]}` | 显示 Tab |
| 200 | `{allowed: false, reason: "UNBOUND"}` | 隐藏 Tab 或提示联系管理员 |
| 200 | `{allowed: false, reason: "USER_DISABLED"}` | 提示账号停用 |
| 401 | `EXTERNAL_ASSERTION_INVALID` | 重新登录 MSSW |
| 401 | `EXTERNAL_ASSERTION_EXPIRED` | 重新登录 MSSW |
| 503 | `EXTERNAL_ASSERTION_UNAVAILABLE` | 提示服务暂不可用 |

> **注意**：`USER_DISABLED` 在 `/identity` 上返回 **200 + reason**（便于 BFF 展示），但**资源接口仍返回 403**。这是刻意的不对称。

---

## 4. 旅程 B：入口列表与建会话

```
BFF → GET /api/v1/msswwweb/entries
   ← 200 {items: [{entry_id, entry_name, agent_id, channel, allowed, reason}]}

BFF → POST /api/v1/msswwweb/entries/{entry_id}/conversations
      Idempotency-Key: <BFF 生成>
   ← 201 {conversation_id}
```

**要点**：

- **列表里出现的入口 = 该用户已被授权**。未授权的入口不会出现在 `items` 里，BFF 不需要再做过滤。
- **`allowed` 表示"授权了但当前是否真的可用"**：入口停用、Agent 被撤权时为 `false`，并给出 `reason`。这样 BFF 能区分「本就没授权」（不在列表里）和「授权了但暂时不可用」（在列表里但 `allowed=false`）。
- 入口是**列表**不是单值（一个用户可被授权多个入口）
- `entry_id` 作为**本次会话的来源**记录，不是访问边界
- 同一 `Idempotency-Key` + 同一指纹 → 返回同一 `conversation_id`

---

## 5. 旅程 C：发消息与接收流式回复

### C1 发消息（202 受理）

```
BFF → POST /api/v1/msswwweb/entries/{entry_id}/messages
      Idempotency-Key: <BFF 生成>
      {conversation_id|null, message_id, text, upload_ids[]}
   ← 202 {run_id, conversation_id}
```

202 只表示**受理成功**，不代表正文就绪。

### C2 订阅事件流

```
BFF → GET /api/v1/msswwweb/runs/{run_id}/events?after_seq=0
   ← event: delta      id: <seq>   data: {text}
   ← event: delta      id: <seq>   data: {text}
   ← event: confirmation ...       （需用户确认时）
   ← event: terminal
```

**SSE 复核**：

| 时机 | 校验 |
|---|---|
| 建连 | user + Agent |
| **每个事件 yield 前** | 到期 / 复核截止 / 授权仍有效 |

### C3 断线重连

```
BFF → GET .../events?after_seq=<最后收到的 seq>
      Last-Event-ID: <同上>
   ← 从该 seq 之后续传
```

`after_seq` 与 `Last-Event-ID` **取大值**，不重置为 0。

### C4 停止

```
BFF → POST /api/v1/msswwweb/runs/{run_id}/cancel
   ← 200
```

### C5 确认与 resume

```
← event: confirmation  data: {required: true, options: [...]}
BFF → POST /api/v1/msswwweb/runs/{run_id}/resume  {choice}
   ← 200（同一 Run 继续）
```

`resume` 要求用户 ACTIVE + Agent 授权仍有效。

### C6 游标过期

```
← 409 CURSOR_EXPIRED
```

**这是正常情况**（用户关页面较久）。BFF 应当作"重新拉历史"信号，不要弹错误框。

---

## 6. 旅程 D：会话列表、详情与历史

### D1 会话列表（跨入口聚合）

```
BFF → GET /api/v1/msswwweb/conversations?agent_id=<uuid>
   ← 200 {items: [{conversation_id, entry_id, channel, has_unread, ...}]}
```

**关键行为**：按 `user + Agent` 聚合，**不按入口过滤**。同一用户在不同入口的会话都会出现，`entry_id`/`channel` 只标明来源。

### D2 会话详情（进入对话页时先调这个）

```
BFF → GET /api/v1/msswwweb/conversations/{id}?through_seq=<通知水位>
   ← 200 {has_unread, confirmation, through_seq, messages[]}
```

**和 `/messages` 历史分页的区别**：

| 接口 | 回答什么 |
|---|---|
| `GET /conversations/{id}`（本接口） | **会话当前状态**：有没有未读、Agent 是不是在等用户确认、从哪个水位开始拉历史 |
| `GET /conversations/{id}/messages` | **消息列表**，按 `page` + `page_size` 翻页 |

进对话页的顺序是：先调本接口拿状态 → 再按返回的 `through_seq` 去调历史分页。这三个信息（`has_unread`、`confirmation`、`through_seq`）都不在消息分页接口里，所以不能省。

### D3 历史分页

```
BFF → GET /api/v1/msswwweb/conversations/{id}/messages
      ?page=1&page_size=20&through_seq=<冻结水位>
   ← 200 {items[], through_seq}
```

**两套序号别混**：

| 序号 | 用途 |
|---|---|
| 消息序号（canonical seq） | SSE `after_seq`、历史 `through_seq` |
| 通知序号 | 会话已读水位 |

它们只作为同一次详情视图的**配对水位**，不能互相比较。

### D4 标记已读

```
BFF → POST /api/v1/msswwweb/conversations/{id}/read
      {through_seq: <通知水位>}
   ← 200
```

超前的水位返回 `CURSOR_AHEAD`——不能保存一个会自动吃掉未来通知的上限。

### D5 删除会话

```
BFF → DELETE /api/v1/msswwweb/conversations/{id}
   ← 200（幂等）
```

user 级软删，沿用现有幂等 delete。

---

## 7. 旅程 E：附件

```
BFF → POST /api/v1/msswwweb/entries/{entry_id}/attachments
      multipart/form-data: file
   ← 200 {upload_id}

BFF → 发消息时带 upload_ids: [upload_id]

BFF → GET /api/v1/msswwweb/artifacts/{artifact_id}/content
   ← 200 字节流（禁止 302）
```

产物按 `user + Agent` 跨入口共享。

---

## 8. 旅程 F：解绑与重绑

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 管理员删除该身份的某个入口授权 | 只删 `entry_access_grant`，身份保留 |
| 2 | 用户此时访问该入口 | `/identity` 返回 `allowed=false`（无该入口 grant） |
| 3 | 管理员重新授权同一入口 | 建新 grant |
| 4 | 用户再次访问 | **历史会话与产物立即可见**（`platform_user_id` 未变） |

这条旅程是「两张表」设计价值的直接体现。

---

## 9. 旅程 G：编辑身份值

| 步骤 | 动作 | 注意 |
|---|---|---|
| 1 | 管理员编辑 `identity_json` | key 集合仍须与 `identity_keys` 一致 |
| 2 | 系统重算 `identity_hash` | 与已有身份冲突 → `IDENTITY_ALREADY_BOUND` |
| 3 | 保存 | **历史会话仍归原 `platform_user`** |

**为什么需要这条**：`_id` 是 24 位 hex，管理员第一次填错的概率不低。没有编辑功能，只能删了重建，历史会话的归属链路会断。

---

## 10. 横切关注点

### 10.1 错误处理

| 场景 | 期望 |
|---|---|
| 签名错 / 格式错 / `alg=none` | 401 `EXTERNAL_ASSERTION_INVALID` |
| 缺身份 key | 401 `EXTERNAL_ASSERTION_INVALID` |
| 超出最大会话年龄 | 401 `EXTERNAL_ASSERTION_EXPIRED` |
| 身份未绑定 | `/identity` 200 + `UNBOUND`；资源接口 403 `EXTERNAL_ASSERTION_UNBOUND` |
| 用户停用 | `/identity` 200 + `USER_DISABLED`；资源接口 403 |
| Secret 未配置 | 503 `EXTERNAL_ASSERTION_UNAVAILABLE` |
| 越权访问他人资源 | 404 `COMMON_NOT_FOUND`（不泄露存在性） |

### 10.2 幂等

| 接口 | 机制 |
|---|---|
| `POST /messages` | `Idempotency-Key` + 指纹，重复返回同一 `run_id` |
| `POST /conversations` | `Idempotency-Key` + 指纹，BFF **不传** `conversation_id` |
| `POST /runs/{id}/cancel` | 天然幂等 |
| `DELETE /conversations/{id}` | 沿用现有幂等 delete |
| `POST /conversations/{id}/read` | 幂等，零更新成功 |

### 10.3 审计

| 动作 | 审计记录 |
|---|---|
| 创建/编辑/删除 `external_identity` | `config_audit_log`，action=CREATE/UPDATE/DELETE |
| 添加/移除 `entry_access_grant` | 同上 |
| 创建 MSSWWEB 入口 | 同上 |

### 10.4 通道隔离

- 独立路由前缀 `/api/v1/msswwweb`
- 独立通道名 `MSSWWEB`
- WEBCHAT / WECOM 行为零变化

---

## 附录 A：四个 ID 的定义

| ID | 谁生成 | 是什么 | BFF 要不要管 |
|---|---|---|---|
| `platform_user_id` | 我方 Console | 平台用户 UUID，**访问边界** | 不需要，只在我方内部流转 |
| `entry_id` | 我方 Console（`bot_account.id`） | 入口 ID，**来源标识** | 需要，从 `/entries` 拿 |
| `conversation_id` | 我方 Runtime | 会话 ID | 需要，存下复用 |
| `run_id` | 我方 Runtime | 一次执行 ID | 需要，用于 SSE/取消/resume |

**归属校验**：`conversation_id`/`run_id` 都必须按 `(tenant, user, agent)` 核属。Runtime 的 `_load_run_in` 只按 `id + tenant_id` 查，**不带 user_id**，所以调用方必须补。

**为什么 `entry_id`/`channel` 不是边界**：同一用户在多个入口的会话共享。若按入口隔离，用户换个入口就看不到之前的对话，「对话连续」无从谈起。

---

## 附录 B：游标语义

### B.1 两套序号

| 序号 | 定义 | 使用场景 |
|---|---|---|
| 消息序号（canonical seq） | `conversation.last_seq` 递增 | SSE `after_seq`、历史 `through_seq` |
| 通知序号 | 通知事件的序列 | 会话已读 `through_seq` |

**历史分页与 SSE 是同一套**（消息序号）：

- 历史：`watermark = conversation.last_seq`（`webchat_history.py:375`）
- SSE：`through = watermark()`，取「会话行 `last_seq` 与已持久行最大 seq」的较大者（`event_tail_hub.py:652-675`）

所以 `through_seq` 与 `after_seq` 可以直接续接。

**但通知序号是另一套**，不能与消息序号比较。

### B.2 游标异常

| 异常 | 含义 | 处理 |
|---|---|---|
| `CURSOR_AHEAD` | 游标超前 | 拒绝；不能吃掉未来通知 |
| `CURSOR_EXPIRED` | 超出保留窗口 | 409；BFF 转历史接口 |
| `CURSOR_RUN_MISMATCH` | 非本 Run 的游标 | Runtime 零命中，需网关自行判定 |

### B.3 对 BFF 的约定

| 项 | 约定 |
|---|---|
| SSE 续订 | `after_seq` 与 `Last-Event-ID` **取大值** |
| 历史分页 | `page` + `page_size` + `through_seq`（首屏返回的值） |
| 已读 | 提交**通知水位**，不是 SSE 收到的 seq |
| `CURSOR_EXPIRED` | 当作"重新拉历史"，不弹错误框 |
