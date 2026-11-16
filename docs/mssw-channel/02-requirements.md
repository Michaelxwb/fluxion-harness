# MSSW 外部 IM 通道接入 — 需求梳理

> **需求基线**：`.code-flow/tasks/2026-11-13/mssw-external-im-channel/mssw-external-im-channel.prd.md`
> 配套：`01-jwt-spec.md`（协议事实）、`03-open-questions.md`（决策记录）、`04-user-journeys.md`、`05-interface-mapping.md`

## 本文档的职责边界

三份文档分工明确，**不要在这里重复实现细节**：

| 文档 | 回答什么 | 归属 |
|---|---|---|
| **本文（`02`）** | **要做什么、为什么要做、边界在哪** | 需求与约束 |
| `docs/operations/im-channel-playbook.md` | **具体怎么实现**（十项必改、13 条反模式、22 项 DoD、缓存状态机的完整细则） | 实现规范，需求方交付 |
| `docs/operations/bff-agent-integration.md` | **BFF 怎么调**（15 个接口的完整参数） | 对外契约，需求方交付 |

本文只保留**摘要与指向**：实现规范的具体条目以 playbook 为准，接口字段以 integration 文档为准。需要改动实现规范时改 playbook，不在这里改。

---

## 第一部分：这是什么

### 1.1 一句话

上游 MSSW 平台要加一个"智能客服"。它的 BFF 通过 **HTTP + SSE** 调用本系统，请求里带 **MSSW 签发的 JWT**。本系统**在 Console 验签 → 按平台配置的 `identity_keys` 解析身份 → 映射到平台用户 → 复用现有会话能力**。

### 1.2 三个参与方

```
用户浏览器 → MSSW BFF → fluxion im-gateway → Console（验签+身份） / Runtime
             (上游后端)   (本次新增的入口)      (我方内部)
```

**BFF 是后端服务，直接转发请求过来，不是浏览器跨站访问。** BFF 直接适配我方现有接口，我方不实现它那套 `/internal/agent-provider/v1/*`。

### 1.3 关键认知

**这不是"从零新建一套聊天服务"，而是"给现有的 WebChat 会话能力换一个身份入口"。**

现有 WebChat 已具备：202 受理、SSE 事件流、取消、历史分页、附件上传下载、会话管理。`MSSWWEB` 复用这一整套，**真正全新的只有三块**：

1. **外部断言认证**（Console 侧验签 + AES 因子编解码）
2. **用户级身份模型**（`external_identity` + `entry_access_grant`）
3. **Redis 认证缓存**（凭证/权限分层 + 版本失效）

---

## 第二部分：已经定死的事

### 2.1 方案边界

| # | 决策 | 后果 |
|---|---|---|
| 1 | **验签在 Console** | 网关不持因子、不持库；因子放 K8s Secret |
| 2 | 不能影响其它通道 | 独立适配器 + 独立路由前缀，不碰 WEBCHAT / WECOM |
| 3 | JWT **透传**，不换发 | 我方只验签，不签发 |
| 4 | 形态**类似新增一个 WebChat 通道** | 复用 `channels/webchat.py` 模式，不复制 handler |
| 5 | **身份字段平台可配**（`identity_keys`） | 新增同类平台只加配置，不改代码 |
| 6 | 无权限 → **直接拒绝**，不自动建档 | 绑定必须在访问前完成 |
| 7 | **权限查询语义是可用性** | `GET /identity` 返回 `allowed` + `reason` + `grants[]` + `agents[]` |
| 8 | 会话按 **user + Agent 跨入口共享** | `entry`/`channel` 只做来源标识，不做访问边界 |
| 9 | **密钥固定，不轮换** | 单公钥静态配置，不预留多密钥槽位 |
| 10 | **BFF 直接适配我方现有接口** | 不需要 BFF 对接文档 |
| 11 | **不是跨站**：BFF 后端直接转发 | `SameSite=Strict` 的 `soc-token` 能正常送达 |

### 2.2 JWT 的事实规格（已实测）

完整规格与实测证据见 `01-jwt-spec.md`。核心：

| 项 | 值 |
|---|---|
| 算法 | RS256（**必须加白名单**，签发方自己没做） |
| 身份字段 | 由平台 `identity_keys` 决定；**MSSW 用 `_id` + `tenant_id`** |
| `_id` | 专家 ID（24 位 ObjectId hex），本方案作为身份键之一 |
| `job_number` | 工号（5-7 位字母数字），明文；本方案不进身份键 |
| `exp` | 恒为 `-1`，服务端不校验；另有最大会话年龄兜底 |
| `aud` / `iss` | AES-256-ECB 密文，**加密期望值比对**；`iss` 是**单元素数组** |
| AES key 派生 | `salt` 右侧补 `\x00` 到 32 字节（`salt` 长度须 ≤32），PKCS#7 补位 |
| 加密字段 | 仅 `email` / `email_login` / `phone_login`；不解密、不落库 |
| 公钥格式 | PKCS#1（`BEGIN RSA PUBLIC KEY`） |
| token 位置 | Cookie `soc-token`，**唯一凭据**（不需要 `X-Chat-Context`） |

实测已验证：验签通过、`aud`/`iss` 密文比对一致、解出身份键 `_id=6a4ca2554a2a45d4b1ad5996`（与 `tenant_id` 共同构成身份）及 `job_number=14806`（工号，不参与身份）、`alg=none` 被拒。

### 2.3 身份模型：用户级身份 + 入口授权

**两张表，回答两个不同的问题。**

| 表 | 回答的问题 | 唯一约束 |
|---|---|---|
| `control.external_identity` | **这个人是谁** | `(tenant_id, platform_key, identity_hash)` |
| `control.entry_access_grant` | **这个人能用哪些入口** | `(tenant_id, external_identity_id, bot_account_id)` |

**为什么拆开**：

- **解绑不丢历史**：解绑只删 `entry_access_grant`，身份还在，历史会话/产物仍有归属主体
- **重绑能恢复**：重新绑回同一入口，`platform_user_id` 没变，历史会话立刻可见
- **多入口共享**：一条身份 + 多条授权，会话按 `platform_user_id` 聚合，天然跨入口共享

**身份解析（运行时，每次请求）**：

```
JWT
  → 按平台 identity_keys 取顶层 claim（如 _id、tenant_id）
  → 全部存在且非空（AND 语义）
  → 按 key 排序 → 规范 JSON → sha256
  → identity_hash
  → 查 external_identity(tenant, platform_key, identity_hash)
  → 命中 → platform_user_id；未命中 → UNBOUND（不伪造主体）
```

**绑定（管理员，一次性）**：

```
管理员在 Console 填写身份 JSON（key 集合必须与 identity_keys 完全一致）
  → 校验：JSON 对象、至少 1 键、值非空字符串、拒绝 exp/aud/iss/iat/nbf
  → 算 identity_hash → 查重（已绑他人 → IDENTITY_ALREADY_BOUND）
  → 建 external_identity
  → 再授权入口 → 建 entry_access_grant
```

> **`_id` 与 `job_number` 别混**：`_id` 是 MongoDB 主键（24 位 hex），本方案作身份键；`job_number` 是工号（明文，5-7 位），本方案**不进身份键**但仍是日志/审计的可读标识。旧版方案用 `job_number` 作映射键，已弃用。

---

## 第三部分：要做什么

### 3.1 需求点总览

| ID | 需求 | 工作量 | 类型 |
|---|---|---|---|
| R1 | 新增 `MSSWWEB` 通道标识与能力分组 | 小 | 配置 |
| R2 | 外部断言认证（Console 验签 + AES 因子） | **大** | 全新 |
| R3 | 用户级身份绑定与入口授权 | 中 | 全新 3 张表 + `platform_user.auth_revision` 列 |
| R3b | 通知未读（红点） | 小 | **复用** `runtime.channel_notification`（`read_at IS NULL` 按 `conversation_id` 聚合），只新增未读索引；会话级 `mark-read` 带通知水位 |
| R4 | 权限/入口可用接口 + Redis 缓存 | **大（占整个需求大头）** | 全新 |
| R5 | 15 个会话接口的门面 | 中 | 大量复用 |
| R6 | SSE 长连接策略（每事件复核） | 中 | 部分全新 |
| R6b | 确认与 resume | 小 | `confirmation.required` → `WAITING_INPUT` → `POST /runs/{id}/resume`；resume 要求 user ACTIVE + Agent 授权仍有效 |
| R7 | 错误码与运维降级 | 小 | 惯例工作 |
| R8 | 测试 | 中 | 惯例工作 |

### 3.1.1 未读与红点的边界（R3b，配套 R5）

> R3b 是通知未读红点，具体接口落在 §3.6 R5 会话门面里；这里只固化它的三条边界与索引要求。

> **三条边界**（来自需求方 FEAT-06）：
> 1. **不做消息级未读**——`has_unread` 按会话聚合，不按 entry 过滤
> 2. **产物发布本身不写通知**——只有 Run 终态/等待输入/任务结果产生通知时点亮红点
> 3. **mark-read 只标记不高于所给水位的通知**——新到达的保留未读；超前水位返回 `CURSOR_AHEAD`

**两个必须建的索引**（否则会话列表会退化成逐会话查询）：

| 用途 | 索引 |
|---|---|
| 会话列表 | `(tenant, user, agent, last_message_at DESC NULLS LAST, id DESC)` WHERE 未删除 |
| 会话未读 | `(tenant, user, conversation, seq)` WHERE `read_at IS NULL` |

**先分页再 EXISTS**：先按列表索引分页取出 N 条会话，再对这批会话 `EXISTS` 判未读，不要反过来。

**水位来源不能猜**：详情在**同一快照**返回 `canonical last_seq=C`、当前 Run 状态、`notification_through_seq=N`；历史第一页用 `C`，展示完成后 `read` 提交 `N`。**通知序号、canonical 序号、SSE id 三者独立**，不能从 `has_unread` 或 SSE id 反推 `N`。

### 3.2 R1：通道标识与能力分组

**必须同时做三件事**：

| 位置 | 改动 | 不改的后果 |
|---|---|---|
| `enums.py` `BUILTIN_CHANNEL_NAMES` | 加 `"MSSWWEB"` | 通道不被识别 |
| `enums.py` `LOCAL_STREAM_CHANNELS` | 加 `"MSSWWEB"` | 产物交付路由 `attachments/tools.py:739-744` 抛 `ATTACHMENT_WRITE_UNAVAILABLE` |
| `enums.py` `OUTBOUND_CHANNELS` | **不加** | MSSW 是 BFF 拉取模式，无主动投递；进错会让附件交付失败 |

**理由**：`tests/test_attachment_contract.py:235` 要求两组**完全划分** `BUILTIN_CHANNEL_NAMES`。这是硬断言，不是约定。

**硬断言要同步改**：

- `tests/test_attachment_contract.py:118`、`:228-235`
- `tests/agent_worker/test_capacity_webchat_worker_notifications.py:824`
- `tests/architecture/test_channel_neutrality.py:25` 的 `CHANNEL_TOKENS` **必须加 `"msswwweb"`**——否则核心域出现渠道字样也不会被发现（假绿灯），规则形同虚设

**Contracts 层还有三处必改（易漏）**：

| 文件 | 变更 |
|---|---|
| `packages/contracts/.../webchat.py` | 新增认证方式枚举 `EXTERNAL_ASSERTION`（与 `CHAT_SESSION` 并列）；新增公开契约**仅增量**，不改既有字段 |
| `packages/api-kit/src/muad_api/error_codes.py` | 登记 10 个新错误码（见 §3.8） |
| `config/api-messages.yaml` | 对应文案；与 `error_codes.py` 有**双向断言**，缺一即测试失败 |

> `SECRETLESS_CHANNELS` 也要加 `MSSWWEB`——入口无 bot secret。

### 3.3 R2：外部断言认证

**位置**：Console。网关不持因子，只把 `soc-token` 原样转发。

**校验链**（顺序固定）：

| 步 | 动作 | 失败处理 |
|---|---|---|
| 0 | 查平台配置（`platform_key` → 公钥/issuer/audience/salt/identity_keys） | 503 `EXTERNAL_ASSERTION_UNAVAILABLE` |
| 1 | `soc-token` 长度上限检查（与 nginx header buffer 对齐） | 401，不解析 |
| 2 | 验签：`algorithms=["RS256"]`、`audience=<AES(audience)>`、`issuer=[<AES(issuer)>]`、`exp=-1` 时 `verify_exp=False` | 401 `EXTERNAL_ASSERTION_INVALID` |
| 3 | 最大会话年龄：`expires_at = min(now+max_age, login_time+max_age, 正数 exp)`；`login_time`/`time` 缺失 → 拒绝 | 401 `EXTERNAL_ASSERTION_EXPIRED` |
| 4 | 身份解析（见 §2.3） | 无命中 → `UNBOUND` |
| 5 | 查 `entry_access_grant` + `bot_account` + Agent 授权 | — |

**关键约束**：

- **`alg` 白名单必加**——签发方自己没做，不加等于允许 `alg=none`
- **`iss` 按数组比对**，不是字符串
- **AES key 派生必须按 `01` §3 的算法**，不要猜；用测试向量驱动
- **不主动踢人**：最大会话年龄只限制 SSE 续订的绝对上限，不是下线策略。token 有效性/吊销由 BFF 在转发前判定

### 3.4 R3：用户级身份绑定

见 §2.3。补充约束：

| 约束 | 说明 |
|---|---|
| 绑定前必须已有平台用户 | 不做 JIT 建档（Q13） |
| key 集合必须与 `identity_keys` 完全一致 | 否则 `IDENTITY_KEYS_MISMATCH` |
| 一个身份只映射一个 `platform_user` | 已绑他人 → `IDENTITY_ALREADY_BOUND`（409） |
| 编辑身份不影响历史归属 | 重算 hash，历史会话仍归原 `platform_user` |
| 解绑只删 grant | 身份保留，可重绑 |

**第一期实现形态（已定）**：

- 管理员在 Console **用户详情页手工逐条录入**身份（`ExternalIdentitySection` + `MsswwebIdentityModal`），字段按平台 `identity_keys` 动态渲染、标签取 `identity_field_labels`。
- **不提供批量导入、不依赖 MSSW 名单文件**；批量能力推迟到后续需求，按届时实际规模评估。
- 录入后在同一身份下「添加入口」建立 `entry_access_grant`。

> **管理员可读性问题**：前端会展示 `identity_keys` 作为输入框标签，但 `expert_id`、`tenant_id` 这类 claim 名管理员看不懂。需在平台配置里补 `identity_field_labels`（claim 名 → 中文名），前端按 label 渲染、按 claim 名提交。

### 3.5 R4：权限与缓存

`GET /identity` **永远查 Console/PG**（不读缓存返回），成功后写 Redis。后续资源请求走缓存。

| 层 | 维度 | 内容 |
|---|---|---|
| 外部断言凭证 | tenant + platform + policy_epoch + token hash | 验签后的主体映射、`external_identity_id`、绝对到期 |
| 用户当前权限 | tenant + user | ACTIVE、可用 Agent、按外部身份分组的入口 grants、`auth_revision` |
| 校验与同步状态 | tenant 屏障 / tenant+user 版本 / tenant+platform 配置状态 | 独立于缓存记录读取 |

**必须记住的两条**：

1. **不能把缓存记录里的 revision 与它自己比较**——必须拿它和数据库/fence 的当前版本比
2. **TTL 只负责回收数据，不提供撤权保证**——撤权靠同步失效 + 版本校验

**R4 是必做项，且工作量占整个需求的大头。** 它是「撤权秒级生效」的保证机制，不是简单的缓存加速。

三条不变量（细则见 `playbook` §3.5.1–3.5.3，实现严格按那里做）：

| # | 不变量 | 违反的后果 |
|---|---|---|
| 1 | **撤权走两阶段协调**（PG 状态机 + Redis 屏障 + fence CAS），不是"删个 key" | 在途回源会把旧权限写回缓存，撤权实际不生效 |
| 2 | **版本比对必须对"外部权威值"**，不能拿缓存记录自比 | 失效检测永远通过，等于没有 |
| 3 | **TTL 与进程重启都不提供撤权保证** | 误以为过期=撤权，实际留下窗口 |

四条实现红线（**做错任一条都会导致"看起来能撤权、实际撤不掉"**）：

- Redis 等待不能在打开的 PG 事务内
- 失败按 PG 阶段恢复，不靠超时自动开屏障
- 撤权路径禁止全库 `SCAN`/`KEYS`，用反向索引 + 有界批量
- Redis 重启/切主后先核对同步状态并换 fence，再接流量

> **影响面**：这套机制**同时作用于现有 WebChat 和 MSSWWEB**（见 DoD）。改动的是两条通道共用的身份层，不是只给新通道加的。

#### 3.5.1 状态机与恢复要点（详见 playbook）

```
PG 写 PREPARING（advisory lock 串行化同租户权限写）
  → Redis 关屏障 + 定向删权限缓存
  → PG 事务提交：变更 + 审计 + auth_revision + COMMITTED
  → Redis 发布新版本，以操作标识 CAS 开屏障 → READY
```

| 场景 | 要求 |
|---|---|
| Redis 不可用 | 有界回 PG；PG 协调状态非 `READY` → **不放行** |
| PG 已提交但同步失败 | 返回 `AUTH_CACHE_SYNC_PENDING`，不假报成功、不继续用旧权限 |
| Redis 重启/切主/快照恢复 | 先关认证入口 → 按 PG 状态恢复并换 fence → 再接流量 |
| 多 key 原子操作 | key 由 Contracts 工厂产生，同一 Lua 用租户 hash tag **同槽**（否则 CROSSSLOT） |

#### 3.5.2 配置变更与重启

**Redis TTL 到期和进程重启都不是撤权机制。** 因子、`identity_keys`、有效期策略变化或平台停用，走**平台级失效**：

```
关闭认证入口 → 删平台凭证缓存 → 更新配置/重建全部 Console 副本
  → 核对各副本版本一致 → 激活新 policy_epoch → 开放
```

三条推论：新旧配置副本**不混发**可缓存结果；配置缺失/损坏在接流量前登记平台不可用（Gateway 缓存命中也要查状态/epoch）；**无配置变化的重启可复用同版本缓存**。

> 与「密钥不轮换」（Q5）不冲突：Q5 说"不主动换"，这里说的是"真要换时按什么流程换"。

### 3.6 R5：会话接口门面

15 个接口，路径 `/api/v1/msswwweb/*`。清单见 `05` §2。

**参数化方式：`ChatSurface` 抽象 + 单 router 多挂载**

不要为 MSSWWEB 复制一份 `api/webchat.py` 的 15 个 handler。做法是把入口面抽象成 `ChatSurface`：

```
ChatSurface(channel, auth_kind, entry_resolver)
```

同一套 router 与 service 按 `channel` 挂载多次，身份依赖按 `auth_kind` 分支（`CHAT_SESSION` / `EXTERNAL_ASSERTION`），执行面（Runtime Owner 接口、上传、SSE、产物）原样复用。

这是 US-09「新增同类平台只加配置和入口，不复制 15 个 handler」的落地方式——加一个平台只需新增一个 `ChatSurface` 配置 + 一条平台因子配置，不改 handler。

涉及 6 个文件（不是只有 `webchat.py` 一个）：

| 文件 | 变更 |
|---|---|
| `channels/base.py` | 复用 `ChannelAdapter` + 可选能力 Protocol，不改 |
| `channels/webchat.py` | **`WebchatChannelAdapter(name=...)` 参数化**——现在是写死的 |
| `channels/registry.py` | 注册新 adapter；`snapshot_names()` 自动纳入快照 |
| `api/webchat.py` | **去掉固定 prefix**，保留共享 handler |
| `api/deps.py` | `ChatSurface`；按 surface 分支的 `get_chat_identity` / `get_chat_stream_guard` |
| `application/chat_principal.py` | 加 `ChatIdentity.allowed_entries`；长流复核抽象 |
| `main.py` | 同一 router 多挂载；**通知路由只挂 WebChat**（BFF 不暴露通知中心） |

> 关键约束：`ChatSurface` 必须留在**装配层**（`api/deps.py`），核心域不得出现 `MSSWWEB` 分支（否则违反 RULE-im-002）。

**6 处通道名写死必须参数化**（这是 R5 最容易漏的地方）：

| # | 位置 | 不改的后果 |
|---|---|---|
| ① | `webchat_service.py:214` `list_entries` | MSSWWEB 入口被过滤 |
| ② | `webchat_service.py:489` `resolve_authorized_entry` | MSSWWEB 入口 404 |
| ③ | `webchat_service.py:237` `InboundEvent` | 事件标错通道 |
| ④ | `webchat_service.py:257` adapter 选择 | 走 WebChat 适配器 |
| ⑤ | `webchat_service.py:286` `ChannelContext` | 会话记成 WebChat |
| ⑥ | **`webchat_core.py:69` `_run_request`** | **Run 真正落库的通道**；漏改则 ③④⑤ 全部无效 |

**测试兜底**：断言 `RunRequest.channel == "MSSWWEB"`，不能只断言 HTTP 202。

### 3.7 R6：SSE 策略

| 时机 | 校验 |
|---|---|
| 开流时 | user + Agent |
| 复核时 | 同一 user、目标 Agent、`expires_at`、token/会话仍有效 |
| **每个事件 yield 前** | 检查到期/复核截止，**不能因事件持续 ready 跳过** |
| 复核调用 | 加 `asyncio.wait_for` 绝对期限 |

`CURSOR_EXPIRED` 是正常情况（用户关页面再回来），BFF 应当作"重新拉历史"信号。

**两套序号不能混**：消息序号（canonical seq）与通知序号。前者用于 SSE `after_seq` 与历史 `through_seq`，后者用于会话已读水位。

### 3.7.1 R6b：确认与 resume

Agent 需要用户确认时，Run 进入 `WAITING_INPUT` 并携带 `confirmation.required`；BFF 展示确认项，用户点击后调 `POST /runs/{id}/resume` 继续。

| 校验 | 要求 |
|---|---|
| 会话归属 | user + Agent 核属，入口不是边界 |
| 用户状态 | `platform_user.status` 必须为 `ACTIVE` |
| Agent 授权 | 该 Agent 授权仍有效 |
| Run 状态 | 只接受 `WAITING_INPUT`，其它状态返回冲突 |

resume 与 SSE 一样按当前授权复核；授权在等待期间被撤销时拒绝 resume，不继续旧 Run。

### 3.8 R7：错误码与运维降级

| 码 | HTTP | 场景 |
|---|---|---|
| `EXTERNAL_ASSERTION_INVALID` | 401 | 缺失/签名错/格式错/`aud`/`iss`/身份 key 缺失 |
| `EXTERNAL_ASSERTION_EXPIRED` | 401 | 超出最大会话年龄 |
| `EXTERNAL_ASSERTION_UNBOUND` | 403 | 身份未绑定（验签通过但无 `external_identity`） |
| `EXTERNAL_ASSERTION_UNAVAILABLE` | **503** | Secret 未配置/损坏 |
| `CURSOR_EXPIRED` | 409 | 游标超出保留窗口 |
| `CURSOR_AHEAD` | 409 | 水位超前（如 mark-read 提交了未来水位） |
| `IDENTITY_KEYS_MISMATCH` | 400 | 绑定 JSON 的 key 集合不符 |
| `IDENTITY_ALREADY_BOUND` | 409 | 身份已绑他人 |
| `BOT_ID_EXISTS` | 409 | `bot_id` 重复 |
| `AUTH_CACHE_SYNC_PENDING` | 503 | 授权缓存同步未完成，失败关闭 |

**`/identity` 的 `reason` 枚举**（`allowed=false` 时返回，用于 BFF 决定提示文案）：

| reason | 含义 | HTTP |
|---|---|---|
| `UNBOUND` | 身份未绑定到任何平台用户 | 200 |
| `USER_DISABLED` | 平台用户已停用 | 200 |
| `NO_ENTRY_GRANT` | 身份存在，但无该入口的授权（**不丢弃主体**） | 200 |
| `ENTRY_DISABLED` | 入口已禁用/删除 | 200 |
| `NO_AGENT_GRANT` | 无该 Agent 的授权 | 200 |
| `AGENT_DISABLED` | Agent 已禁用/删除 | 200 |

**运维降级语义**：Secret 缺失/损坏**只让 MSSWWEB 返回 503**，不影响启动与其他通道。新错误码必须同时登记 `packages/api-kit/.../error_codes.py` 与 `config/api-messages.yaml`（双向断言）。

### 3.9 R8：测试覆盖

| 层级 | 覆盖 |
|---|---|
| **contract** | 公开契约字段与错误码；`error_codes.py` ↔ `api-messages.yaml` 双向断言 |
| unit | 验签四分支（合法/签名错/`alg=none`/缺 key）、AES 派生、身份 hash、同槽 Lua 原子性 |
| **component** | 认证缓存状态机四种故障点；PG 核验分支（用真实 PG + 内存 fake Redis）；不含排序轮询、不跳过冻结 |
| integration | 6 处通道参数化（断言 `RunRequest.channel`）、`/identity` 六种 `reason`、缓存版本校验 |
| **隔离矩阵** | 跨用户隔离：A 的凭证不能读 B；同一 `platform_user` 多 `external_identity` 一致收敛；会话/产物/通知可共享，**凭证不能跨 tenant/platform/entry 复用** |
| e2e | `/identity` → 建会话 → 发消息 202 → SSE 订阅 → 历史分页 → 已读 → 删除 |

必须覆盖的**失败路径**：Redis 故障回 PG、屏障未开、版本不一致、写在途 Redis 短时失败、恢复数据疑似陈旧。

### 3.9.1 交付验收（22 项 DoD）

**权威清单在 `playbook` §6**，任务拆解与收尾验收以那里为准。分组数量：通道与装配 4、降级 1、身份 6、缓存与配置 2、索引 1、功能闭环 4、数据一致性 1、能力组与文档 3。

其中最容易漏、必须在任务里显式体现的五条：

- **重名 adapter 启动失败**（不是静默覆盖）
- **缓存机制同时覆盖 WebChat 与 MSSWWEB**，旧回源不能回填
- **`EXPLAIN` 验证索引**（小表 Seq Scan 不直接判失败）
- **snapshot 半组、重复、EOF 及过期恢复**用例
- **完整回归走隔离的 `make acceptance` 或 CI，不得共用 dev 库**

### 3.10 规范沉淀（Spec Delta）

本次设计需沉淀成规范，否则下个平台还得重新推导：

| Spec | 内容 |
|---|---|
| `harness-im` | Channel / Platform / Entry / Identity 四概念；Secretless 通道；单 router 多挂载的 Surface 扩展方式；同来源多身份的凭证失效 |
| `harness-auth` | Redis 认证缓存：两层 key、版本、屏障、Lua 同槽、不 SCAN、故障关闭、恢复与重启语义 |
| `harness-secret` | 平台因子放 Secret（不进 `bot_account.config_json`、不回显）；启动可选、请求时关闭；替换流程 |

> 这三条是 playbook §7 给出的建议，属于**需求方交付物的一部分**，不是可选项。

---

## 第四部分：约束与风险

### 4.1 必须遵守的约束

1. **验签只在 Console**——网关不持因子
2. **归属按 user（+ Agent）**——不能只凭 `run_id`/`conversation_id`；Runtime 的 `_load_run_in` 不带 user_id
3. **SSE 每事件复核**——不能只校验建连
4. **游标只透传**——`after_seq`/`Last-Event-ID` 取大值，不重置为 0
5. **失败关闭**——配置错误尽早暴露，不降级放行

### 4.1.1 数据边界的三条细则（playbook §3.7）

| # | 规则 | 说明 |
|---|---|---|
| 1 | **显式资源访问不加 entry/channel 断言** | 列表聚合仍需 `(tenant, user, agent)` 核属——用"查得到"表示"属于你"，不额外比入口 |
| 2 | **删除/取消/已读不要求入口 enabled，也不回查入口快照** | 入口停用后用户仍要能删自己的会话、停正在跑的 Run |
| 3 | **会话列表接口隐藏入口可用性** | 按 user + agent 聚合，不因 entry 停用隐藏历史；单入口的 `entry_id != null` 是兼容字段 |

> **通知 writer 的回退是已知缺陷**：现有实现查不到 `conversation.channel` 时会回退到 `bot_account.channel`。若入口有多条身份，会共用错误 UUID。要改成**按实际 Run 来源精度回退**，不能丢来源。这是本期要修的既有 bug。

### 4.1.2 十三条反模式

**权威清单在 `playbook` §5**（架构 2 条、安全 5 条、功能语义 6 条），实现时逐条对照。

最容易犯的三条，单独强调：

| ❌ | ✅ |
|---|---|
| 复制 `api/webchat.py` 的 15 个 handler 给新通道 | 一个 router + `ChatSurface` 装配参数 |
| 把 JWT 因子写进 `bot_account.config_json` 或回显给前端 | 因子在 Secret；`config_json` 不存密钥、不整体回显 |
| 用未验签的 `login-source` 选密钥或选平台 | 只用**验签通过后**的 claim 做选择 |

### 4.2 风险

| 风险 | 影响 | 处置 |
|---|---|---|
| `run_id` 越权 | Runtime 只按 `id + tenant_id` 查，同租户内拿到 `run_id` 即可越权 | 调用方按 `platform_user_id` + Agent 复核（含 SSE） |
| 历史 token 重放 | `exp` 恒为 -1，吊销在 MSSW 侧 | 责任前移 BFF；我方加最大会话年龄兜底 |
| Redis 缓存旧权限 | 撤权不生效 | 同步失效 + 独立版本校验 + fence CAS |
| `CURSOR_EXPIRED` 无补全数据 | 需自行编排 | 409 后 BFF 转历史接口 |
| 通道名遗漏参数化 | MSSW 请求以 WEBCHAT 落库 | 集成测试断言 `RunRequest.channel` |
| `CHANNEL_TOKENS` 未加词 | 架构机检假绿灯 | 通道注册表的每个通道名都必须在词表中 |
| AES 派生算法写错 | 全量 401 | 按 `01` §3 实现，测试向量驱动 |
| 生产密钥未取得 | 无法生产联调 | 开发环境自签；生产前索取（M5） |
| 密钥不轮换 | 泄露无法止损 | 换密钥 + 重新部署 |

### 4.3 明确不做

- **不做 token 有效性/吊销判定**——吊销状态在 MSSW 侧 Redis，我方无数据源；责任在 BFF 转发前
- **不主动踢人**——最大会话年龄只作 SSE 续订上限，不是下线策略
- 不把验签挪到网关（因子只在一处）
- 不引入第二套认证域（不复用 CHAT Cookie）；不改 WEBCHAT / WECOM 行为
- 不复刻签发方的免校验分支；不实现 AES 解密（改用加密期望值比对）
- **不做密钥轮换**——单密钥静态配置，不做多密钥并存/版本槽位
- 不做客户维度隔离（`customer_id` 全仓不存在）；不做用户自动建档
- **不实现平台专用 claim 提取器**（第一期）——保留扩展点，等第二个平台真来了再落地
