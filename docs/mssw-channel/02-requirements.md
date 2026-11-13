# MSSW 智能客服通道接入 — 需求梳理

> 状态：需求梳理完成，未进入 `cf-task:prd` 正式流程
> 上游依据：`智能客服AI工作台-概要设计说明书 - 1111claw对接版本.md`
> 配套：`01-jwt-spec.md`、`03-open-questions.md`、`04-user-journeys.md`、`05-interface-mapping.md`

---

## 第一部分：这是什么

### 1.1 一句话

上游 MSSW 平台要加一个"智能客服"。它的 BFF 通过 **HTTP + SSE** 调用本系统（fluxion），请求里带 **MSSW 签发的 JWT**。本系统**验签 → 用工号匹配本地用户 → 复用现有会话能力**。

### 1.2 三个参与方

```
用户浏览器 → MSSW BFF → fluxion im-gateway → Console / Runtime
             (上游后端)   (本次新增的入口)      (我方内部)
```

**BFF 是后端服务，直接转发请求过来，不是浏览器跨站访问。**

BFF 那一侧的 `/internal/agent-provider/v1/*` 是**它自己的对外接口定义**。我方**不实现那套 URL**——BFF 直接适配我方现有接口，由它自己完成外部契约到内部接口的转换。

由此消解了原有两个大风险：

- **跨站 Cookie 风险解除**：`SameSite=Strict` 的 `soc-token` 由后端转发，不受浏览器跨站限制
- **无需 BFF 对接文档**：接口形态由我方现有实现决定

### 1.3 关键认知（这条决定了整体工作量）

**这不是"从零新建一套聊天服务"，而是"给现有的 WebChat 会话能力换一个身份入口"。**

现有 WebChat 已经具备：202 受理、SSE 事件流、取消、历史分页、附件上传下载、会话管理。BFF 要的 8 个能力里，**真正全新的只有 JWT 验签器**；`/permissions` 是新增路由壳子（判定谓词复用 `/entries`），其余 7 个复用现有接口。

---

## 第二部分：已经定死的事

### 2.1 需求方确认的边界

| # | 决策 | 后果 |
|---|---|---|
| 1 | JWT 校验在 **im-gateway** 统一做 | 网关需本地验签；网关零数据库依赖，用户映射走 HTTP 到 Console |
| 2 | 不能影响其它通道 | 独立适配器 + 独立路由前缀，不碰 WEBCHAT / WECOM |
| 3 | JWT **透传**，不换发 | 我方只验签，不签发 |
| 4 | 形态**类似新增一个 WebChat 通道** | 复用 `channels/webchat.py` 模式 |
| 5 | 用户校验**做简单**：标识比对 | 工号匹配即可 |
| 6 | 无权限 → **直接拒绝**，不自动建档 | 映射必须在访问前建好（见 §3.4） |
| 7 | 权限查询的**语义**是布尔 | 我方新增 `GET /api/v1/mssw/permissions` 返回 `{"visible": bool, "entry_id": uuid, "agent_id": uuid}`，`entry_id` 顺带下发给 BFF |
| 8 | 工号**全局唯一** | `identity_key` 无需加租户维度 |
| 9 | 工号变更走**人为删除账户** | 不做自动同步 |
| 10 | **BFF 直接适配我方现有接口** | 无需我方实现 `/internal/agent-provider/v1/*` 这套 URL；**不需要 BFF 对接文档** |
| 11 | **不是跨站**：BFF 是后端服务，直接转发请求 | `SameSite=Strict` 的 `soc-token` Cookie **能正常送达**；跨站 Cookie 风险解除 |

### 2.2 JWT 的事实规格（已实测）

完整规格与实测证据见 `01-jwt-spec.md`。核心：

| 项 | 值 |
|---|---|
| 算法 | RS256 |
| **身份字段** | **`job_number`**（工号，5-7 位字母数字），**不是 `sub` 也不是 `_id`** |
| `_id` | 专家 ID（24 位 ObjectId hex），**不作映射键** |
| `exp` | 恒为 `-1`，服务端不校验，**无过期语义** |
| `aud` / `iss` | AES-256-ECB 密文，校验时加密期望值比对，**无需解密** |
| 加密字段 | 仅 `email` / `email_login` / `phone_login` / `aud` / `iss`；`job_number` 是明文 |
| 公钥格式 | PKCS#1（`BEGIN RSA PUBLIC KEY`），PyJWT 可直接加载 |
| token 位置 | 请求头内的 Cookie，键名 **`soc-token`** |

实测已验证：验签通过、`aud`/`iss` 密文比对一致、解出 `job_number=14806`、`alg=none` 被拒。

### 2.3 用户映射链路

**建映射时**（一次性）：

```
JWT 的 job_number  ──匹配──>  platform_user.user_code   （人的业务工号）
                              → 取出 platform_user.id（UUID，这才是 platform_user_id）
                              → 写入 channel_identity(external_user_id=job_number, platform_user_id=<UUID>)
```

**运行时**（每次请求）：

```
JWT 的 job_number
  → channel_identity.external_user_id     （直接查映射行，**不比对 user_code**）
  → platform_user_id（UUID）
```

表已存在，`identity_key = {channel}:{bot_id}:{external_user_id}`，唯一约束 `(tenant_id, identity_key)` 且未软删。查询入口 `ChannelService.resolve` 现成。

> **四个名字别混**（R3 的详细说明见 §3.4）：
>
> | 名字 | 是什么 | 什么时候用 |
> |---|---|---|
> | `platform_user.user_code` | 工号字符串（如 `14806`），业务编号；唯一约束 `(tenant_id, user_code)` | **只在建立映射时**用来定位"这个人是谁" |
> | `platform_user.id` | 数据库主键 UUID | 这就是 `platform_user_id`，运行时全程用这个 |
> | `channel_identity.external_user_id` | 外部平台的用户标识；MSSW 场景下**值 = 工号** | 运行时 `resolve` 按它查映射行 |
> | `channel_identity` 表 | 存 `external_user_id` / `platform_user_id` / `bot_account_id` / `identity_key` | **没有 `user_code` 列** |

**WEBCHAT 的对比**：WEBCHAT 不写这张表，直接拿 `platform_user_id` 当 `external_user_id`，因为用户本身就是本地用户。MSSW 必须有映射行——这是本次唯一需要新增数据准备的地方。

---

## 第三部分：要做什么

### 3.1 需求点总览

| ID | 需求 | 工作量 | 类型 |
|---|---|---|---|
| R1 | 新增 MSSW 通道标识 | 小 | 配置 |
| R2 | JWT 验签器 | **大** | 全新 |
| R3 | 工号 → 用户映射 | 中 | 复用 + 数据准备 |
| R4 | 权限查询 | 小 | 新增路由 + 判定复用 `/entries` |
| R5 | 八个会话接口的门面 | 中 | 大量复用 |
| R6 | SSE 长连接策略 | 小 | 全新（但简单） |
| R7 | 错误码与文案 | 小 | 惯例工作 |
| R8 | 测试 | 中 | 惯例工作 |

### 3.2 R1 通道标识

- `BUILTIN_CHANNEL_NAMES` 现在是 `("WECOM", "WEBCHAT")`（`enums.py:7`），加入固定标识。
- **必须同时归入 `LOCAL_STREAM_CHANNELS`**（`enums.py:175`，出站靠 SSE 订阅面，无 IM 主动推送）。两条理由：
  - `tests/test_attachment_contract.py:235` 断言 `set(BUILTIN_CHANNEL_NAMES) == set(OUTBOUND_CHANNELS) | set(LOCAL_STREAM_CHANNELS)`——内建通道必须被两组**完全划分**，只加 `BUILTIN` 不动分组会让该断言失败。
  - 产物交付按能力组路由（`apps/agent-runtime/.../attachments/tools.py:739-744`）：`LOCAL_STREAM_CHANNELS` → 发布到会话流，`OUTBOUND_CHANNELS` → 网关投递，**都不在则抛 `ATTACHMENT_WRITE_UNAVAILABLE`**——MSSW 会话里生成产物会直接失败。
- 机检：通道名只能一处声明（`tests/test_attachment_contract.py::test_b07`）。
- **两处硬断言要同步改**：`tests/test_attachment_contract.py:118`、`tests/agent_worker/test_capacity_webchat_worker_notifications.py:824` 都把 `BUILTIN_CHANNEL_NAMES` 钉死为二元组。
- 契约字段 `ChannelName` 本身是**开放 string**，不改类型。

### 3.3 R2 JWT 验签器（最重的一块）

**位置**：`im-gateway`，新增 `application/mssw_jwt.py`。

**为什么不在 Console 验签**（已决策）：

| 候选位置 | 否决理由 |
|---|---|
| Console（网关转发 `soc-token`） | ① **边缘职责内推**：无效 token 会穿透到有数据库的内部服务，垃圾流量直达 Console ② Console 已有 `chat_auth_service` 的会话/Token 生命周期，再叠一套 MSSW 验签易混乱 ③ SSE 建连校验要每个连接向 Console 发一次内部请求，与现有 `ChatStreamGuard` 已证明的"不每 token 查控制面"约束冲突 |
| 独立鉴权侧车/中间件 | 为一个通道、一枚公钥**新增部署单元**，成本远大于收益；且与"独立适配器 + 独立身份依赖"的进程内隔离策略冲突 |

**核心判断**：验签是"在网络边缘拒绝无效请求"的职责，本就属边缘。留在网关的代价只是两个 Python 包。

**依赖取舍**：优先 `PyJWT[crypto]`（RSA 走 `cryptography`，**已确认在依赖树中**——`wecom-aibot-python-sdk` 传递引入，见 `requirements.txt`；测试代码也已在用）。`aud`/`iss` 校验只需**加密期望值后比对**，AES-ECB 用 `cryptography` 即可，**不必引入 `pycryptodome`**。

**必须做的**：

| 项 | 说明 |
|---|---|
| 验签 | RS256 + 公钥 |
| **算法白名单** | **仅 `RS256`**。签发方自己没做（`jwt.go:65-67` 的回调不检查 `token.Method`），我方必须补 |
| 必需 claim | **`job_number`** |
| `exp` 处理 | **关闭校验**（值恒为 -1，校验必失败） |
| **不做签发时间窗** | 读 `time`/`login_time` 但**不据此拒绝**——它是签发时刻而非最后使用时刻，用它做绝对窗口会误杀活跃用户（见 3.3.1） |
| `aud`/`iss` | 用同一 `salt` 加密期望值后比对密文 |
| 失败行为 | **fail-closed 401**，不降级 |

**明确不做的**：

- 不解密 `email`/`email_login`/`phone_login`（业务只用工号）
- 不实现 AES 解密（`aud`/`iss` 只加密比对，避开非标准 unpad 兼容问题）
- 不复刻签发方的免校验分支（`X-From-Src != external` 即跳过）——我方入口只服务外部平台

**配置驱动**：公钥、`issue`、`audience`、`salt` 四项全部从配置读，便于联调期替换。

**密钥不轮换（Q5 已定）**：需求方明确"密钥固定，我们只要用就行"。因此：

- 按**静态单密钥**配置读取，**不实现轮换逻辑、不做多密钥并存、不预留密钥版本槽位**——避免做没人会用的复杂度。
- 代价要记清：**公钥若泄露无法靠轮换止损**，只能换密钥 + 重新部署。这是上游选择，我方不额外兜底。
- 开发环境四项值已实测确定（A 组 Q2-Q4）；生产环境待 M5（后续会给）。

**职责单一**：验签器只输出"已验签的 claims"，不做用户映射，便于单测。

### 3.3.1 重放防护（责任前移到 BFF）

**问题**：攻击者拿一枚历史 `soc-token` 直接打我方接口，本地验签**会通过**——签名真实、`job_number` 真实、映射成功、授权有效。因为签发方把 `exp` 写死为 `-1`，且吊销靠 MSSW 侧 Redis 清 key，**我方看不见**。

**处置结论：这件事不由我方解决，由 BFF 解决。** 理由：

| 事实 | 推论 |
|---|---|
| 上游只有 **BFF 一个服务**（Q15） | 我方所有外部流量都经 BFF，没有旁路 |
| `soc-token` 由 **MSSW/mss-auth-go 签发**，BFF 与签发方同侧 | 判断"token 是否仍有效/未吊销"，BFF 天然具备条件（它就在 MSSW 生态内） |
| 吊销状态在 MSSW 侧 Redis，我方不可见 | 我方**无论如何都做不到**等价判定，勉强做只会是假动作 |
| BFF 是**后端服务**，每次转发都是一次服务端调用 | 它在转发前校验一次，成本与职责都合理 |

**因此**：BFF 在转发前完成"这枚 token 当前是否仍有效"的校验，**无效或已登出的请求在 BFF 侧就被挡掉**，根本不会到达我方网关。我方不做上游校验、不要校验接口（**Q30 撤销**）。

**被排除的手段——本地绝对时间窗**：不可行。`time` / `login_time` 是**签发**时刻写死的，同一枚 token 每次请求都一样，BFF 复用它转发（`RefreshLoginJwt` 只重置 MSSW 侧 Redis TTL，**不重签 JWT、不回写 Cookie**）。所以：

- 9:00 登录、连续用到 11:30 的**合法活跃用户**，`time` 仍是 9:00，落在 2 小时窗外 → 被误杀；
- 9:00 签发、11:00 才被拿来重放的**旧 token**，与上一种在 JWT 字段上**完全相同**。

本地做任何基于 `time` 的判定，都分不开"活跃用户"与"旧 token 重放"。**设了只会误杀合法用户，不解决重放**，故不设。

**我方保留的两层（都是资源与边界，不是鉴权）**：

| 层 | 措施 | 作用 |
|---|---|---|
| **1. 限制连接寿命** | SSE 单轮时长上限 | 纯资源安全；重放无法无限占连接（R6） |
| **2. 责任边界写进对接约定** | 明文约定：BFF 保证只有"当次活跃会话、用户未登出"的 `soc-token` 才会被转发 | 这是主防线，缺口责任归属明确 |

**残留风险如实记录**：若 BFF 侧实现不到位（例如登出后仍复用旧 Cookie 转发），我方**无法察觉也无法拦截**——这是 `exp = -1` + 吊销不可见的必然结果。不设本地时间窗来制造"已处理"的假象。

**为什么把责任交给 BFF 是可接受的——重放的实际门槛**：光有旧 `soc-token` 还不够，攻击者要能真正造成影响，还要拿到调用所需的不透明 ID。这些 ID 都不在 JWT 里。但要分清两类接口：

| 接口类别 | 定位依据 | 重放者的实际门槛 |
|---|---|---|
| **需 `entry_id`**：发消息、建会话、传附件 | `entry_id`（`bot_account.id` 的 UUID），且必须 `enabled` + 通道匹配 + Agent 在授权集合内（`resolve_authorized_entry`，`webchat_service.py:475-497`） | **高**：`entry_id` 既不进 JWT 也不由工号推导，只在 Console `resolve` 响应里下发。不知道就调不通（一律 404）；即便知道，渠道与授权也得对得上 |
| **不需 `entry_id`**：历史、事件流、取消、附件下载 | `conversation_id` / `run_id` / `artifact_id`（均为 UUID） | **中**：这些 ID 一旦从日志、URL、BFF 转发记录泄漏，配旧 token 即可访问 |

**必须点明**：后一类的归属校验（`_resolve_scope`：`webchat_service.py:445-457`、`owned_run_state`：`:184-188`）按 `platform_user_id` 比对，而同一工号映射出的 `platform_user_id` 恒定不变——所以它**挡不住"同一用户的旧 token 重放"**，只能挡"拿 A 的 token 访问 B 的数据"。

结论：`entry_id` 不透明显著降低了"凭空发起新对话"这条最易滥用的路径，但"读取/操作已有会话"靠的是 ID 本身不公开。这不是本地任何手段能解决的——只有掌握 token 真实状态的 BFF 能从源头挡住。

**`time` / `login_time` 的用途**：仅用于日志可观测性——记录 token 签发时刻，便于排查异常长寿命 token；**不参与任何拒绝决策**。


### 3.4 R3 工号映射

**要解决的问题**：JWT 里只有 `job_number`（工号，如 `14806`），而我方系统内部认的是 `platform_user_id`（UUID）。需要把工号翻译成 UUID。

**为什么不能直接查人**：工号存在 `platform_user.user_code`，理论上可以 `WHERE user_code = '14806'` 直接查。但网关**不持数据库**（架构约束），而且这样做等于"每次请求都做一次身份匹配"，把授权逻辑散落在查询里。

**所以我方用的是映射表**：`channel_identity` 一行记录，把"外部平台的用户标识"和"我方用户主键"绑死。

```
一行 channel_identity 记录：
  external_user_id = "14806"          ← 工号，MSSW 侧的用户标识
  platform_user_id = 7f3a...（UUID）  ← 我方用户主键
  bot_account_id   = a1b2...（UUID）  ← 属于哪个入口
  identity_key     = "MSSW:mssw-bot-01:14806"
```

**运行时只做一次查表**，不做比对：

```
JWT 解出 job_number = "14806"
  → Console /internal/channel/resolve（网关发 HTTP，不持库）
  → 按 (tenant, channel=MSSW, bot_account_id, external_user_id="14806") 查 channel_identity
  → 查到了 → 返回 platform_user_id + 该用户被授权的 Agent 列表
  → 查不到 → bound=false，请求拒绝（Q13：不自动建档）
```

**最容易误会的点**：`user_code` **在运行时完全不参与**。"工号 ↔ user_code"的比对只发生在建映射那一刻；之后每次请求都是拿工号直接查映射行，命中就用、不命中就拒。四个名字的区别见 `04` 阶段 0-C 的对照表。

**映射怎么建出来（D5 已定）**：**管理员在 Console 新建用户时，`user_code` 字段直接填 MSSW 工号。**

```
第 1 步：管理员在 Console 新建平台用户
  POST /api/v1/users  { user_code: "14806", display_name: "...", status: "ACTIVE" }
  → create_user（user_service.py:62-82）
  → 写 platform_user 一行：user_code = "14806"
  → 顺带查重（find_by_code），冲突抛 USER_CODE_EXISTS；唯一约束 uq_platform_user_tenant_user_code

第 2 步：为该用户建 MSSW 通道映射
  → 写 channel_identity 一行：
      external_user_id = "14806"        ← 与上一步的 user_code 同值
      platform_user_id = 该用户的 id
      bot_account_id   = 单入口的 bot
      identity_key     = "MSSW:<bot_id>:14806"
```

**两步不能省其一**：`user_code` 回答"这人是谁"，`channel_identity` 回答"这个外部标识对应哪个平台用户"。运行时 `resolve` 查的是**后者**——只建用户不建映射，仍然是 `bound=false`。Q13 排除了"首次访问自动建档"（不做 JIT provisioning），所以映射必须在访问前就绪。

> **实现方式待定**（`04` 阶段 0-C 有对照）：① 建用户时顺带建映射（最省事，工号天然一致）；② 复用绑定码流程（`bind()`，`channel_service.py:215-261`，但 `external_user_id` 来自请求参数，需保证用户输入的正是工号）。

**网关侧代码形态**：复用 `InboundPipeline._resolve`（`inbound.py:836-849`）的写法——组装 `ChannelResolveRequest`，HTTP 打到 Console。这样网关不需要引数据库驱动，天然满足"网关不持库"。

**与 WebChat 的差异**：WebChat 的用户本来就是平台本地用户，`external_user_id` 直接放 `platform_user_id` 即可，不用建映射。MSSW 的用户是外部平台的，**必须有这一行映射**，否则 `resolve` 必然 `bound=false`。这是本次唯一需要新增的数据准备工作。

### 3.5 R4 权限查询

**语义是布尔，且顺带下发 `entry_id`；需要新增一个独立接口。**

- 上游口径：进入工作台查一次，决定 Tab 可见性
- **语义为布尔**（Q14 已确认），响应 `{"visible": bool, "entry_id": "<uuid>", "agent_id": "<uuid>"}`
- **`entry_id` 只能从这里给 BFF**：它由 Console `resolve` 产生，而 `resolve` 是网关内部接口，BFF 看不到；BFF 后续发消息/建会话/传附件都要在路径里带 `entry_id`
- **不可见时仍返回 `entry_id`**：它标识"网关对接的那个入口"（由配置 `bot_id` 决定），与工号有无权限无关；`resolve` 在 `bound=False` 时也照样返回（`channel_service.py:115-121`）。提前给不泄露权限（`visible` 已表达），可省 BFF 一次往返
- **入口未配置/已停用 → 404 `MSSW_CHANNEL_NOT_CONFIGURED`**：此时 `resolve` 抛 `BOT_NOT_FOUND`，`entry_id` 不存在，不能造假值；这属配置故障，不与"用户不可见"混为一谈
- **不缓存、不作运行时授权依据**（概设 :1072）
- 调用时**无会话上下文**，JWT 中不含 `conv`

**为什么不能直接复用 `GET /api/v1/webchat/entries`**：该接口在 `WebchatService.list_entries`（`webchat_service.py:206-215`）里硬编码 `item.channel == WEBCHAT_CHANNEL`，MSSW 入口会被全部过滤掉；且入参 `ChatIdentity` 携带 Console 会话的 `expires_at`，MSSW 的 JWT `exp` 恒为 -1，无此语义。

**方案**：新增 `GET /api/v1/mssw/permissions`。判定谓词（入口启用 + 通道匹配 + Agent 授权）从 `list_entries` 抽成共享函数，`/entries` 与 `/permissions` 各传自己的通道，判定口径只有一份。

**原料**：`ChannelService.resolve` 的 `authorized` 字段 + `list_authorized_agents`。

**入口来源（Q15 已定）**：MSSW BFF 只有**一个**上游服务，`bot_id` 由网关配置项指定（不传路径参数、不做多入口区分）。`entry_id` **不需要推导**——Console `resolve` 响应本就返回 `entry_id=bot.id`（`channel_service.py:99-127`、`contracts/channel.py:152-154`），随 `platform_user_id` 一起拿到。

### 3.6 R5 八个会话接口

**统一模式**：验 JWT → 映射 `platform_user_id` → 调网关现有 `WebchatService` → 返回现有契约。

**因为 BFF 适配我方，接口形态就是我们现有的样子。** 详细定义见 `05-interface-mapping.md`。

| 能力 | 我方接口 | 处置 | 复用度 |
|---|---|---|---|
| 发消息 | `POST /api/v1/mssw/entries/{entry_id}/messages`（新增路由） | 加路由 | 100% |
| 事件流 | `GET /api/v1/mssw/runs/{run_id}/events` | 换 Guard | 90% |
| 取消 | `POST /api/v1/mssw/runs/{run_id}/cancel` | 换依赖 | 100% |
| 历史 | `GET /api/v1/mssw/conversations/{conversation_id}/messages` | 换依赖 | 95% |
| 建会话 | `POST /api/v1/mssw/entries/{entry_id}/conversations`（新增路由） | 加路由 | 100% |
| 传附件 | `POST /api/v1/mssw/entries/{entry_id}/attachments`（新增路由） | 加路由 | 100% |
| 下附件 | `GET /api/v1/mssw/artifacts/{artifact_id}/content` | 直接复用 | 100% |
| 权限 | `GET /api/v1/mssw/permissions`（新增，布尔 + `entry_id`） | 判定复用 | 90% |

**关键澄清**：Runtime 的 `POST /v1/runs` 确实直接返回 SSE，但**网关层早有 202 受理语义**（`WebchatService.submit_message`，`webchat_service.py:224-271`），所以 **Runtime 一行都不用改**。

**service 层要动的只有通道名参数化**（`WEBCHAT_CHANNEL` 的写死点共 **6 处**；另有 `webchat_core.py:53` 的 dataclass 默认值属类定义，MSSW 用另一个身份对象传入即可，不改原类）：

| 位置 | 硬编码 | 不参数化的后果 |
|---|---|---|
| `submit_message` 的 `InboundEvent(channel=...)`（`webchat_service.py:237`） | `WEBCHAT_CHANNEL` | 事件被标成 WebChat 通道 |
| `submit_message` 的 `adapter=WEBCHAT_CHANNEL_IDENTITY`（`:257`） | 通道身份对象 | 受理走 WebChat 适配器身份 |
| `_run_request` 的 `ChannelContext(type=...)`（`webchat_core.py:69`） | `WEBCHAT_CHANNEL` | **真正写进 Runtime 的通道**；这是 `submit_message` 的实际落库路径，漏改则前两处白改 |
| `create_conversation` 的 `ChannelContext(type=...)`（`webchat_service.py:286`） | `WEBCHAT_CHANNEL` | 会话记为 WebChat 通道 |
| `resolve_authorized_entry`（`:489`） | `candidate.channel == WEBCHAT_CHANNEL` | MSSW 入口一律 404 |
| `list_entries`（`:214`） | `item.channel == WEBCHAT_CHANNEL` | `/permissions` 恒为 false（入口快照为空） |

> **`webchat_core.py:69` 最容易漏**：`submit_message` 只负责构造 `InboundEvent` 交给 `admit_sync`，而 `admit_sync`（`webchat_core.py:87-88`）第一行就是 `_run_request(event, principal)`，真正组装 `RunRequest.channel` 的是 `_run_request`。**只改 `webchat_service.py` 里那几处，消息仍会以 `WEBCHAT` 通道落库。**

除此之外 8 个接口的 service 逻辑一行不改——`ChatIdentity` 用到的四个字段里，MSSW 能提供三个（`tenant_id` / `platform_user_id` / `agents`），缺的只有 `expires_at`，且只影响 SSE（见 §3.7）。

### 3.7 R6 SSE 长连接策略

**必须新写，不能复用 `ChatStreamGuard`。**

理由：现有 Guard 依赖 token 的 `expires_at` 到点断流（`chat_principal.py:184-207`），而 MSSW 的 `exp` 恒为 -1，前提不成立。

新策略：

| 项 | 现有 CHAT | MSSW |
|---|---|---|
| 建连校验 | 一次 | 一次（验签 + 映射） |
| 流内周期复核 | 每 N 秒向 Console 复核 | **不做**（无可依据的到期时刻，也不向上游查询——鉴权前移给 BFF，见 3.3.1） |
| 断流条件 | 凭证过期 | **单轮时长上限** + 客户端断开 |
| 重连 | 前端自动续连 | **BFF 重连即重新验签**（重连的合法性仍由 BFF 保证） |

**为什么不做流内复核**：签发方的过期判断在 MSSW 自己的 Redis key 上（`01` §2.1），是**滑动续期**——只要用户活跃就永不到期。我方看不到那个 Redis，无从复核；而上游只有 BFF，token 有效性由 BFF 在转发前判定（3.3.1），网关无需再查一遍。

**判定策略（只有一层，且不是鉴权）**：

| 层 | 判据 | 作用 |
|---|---|---|
| 连接级到期 | 单轮流有硬性时长上限（新增一项启动资源预算配置，与现有预算项同风格） | **纯资源安全策略**：单连接不无限占用；与 token 是否有效无关 |

**明确不做**：

- 不做绝对时间窗（`time` 是签发时刻，会误杀活跃用户，见 3.3.1）
- 不做流内上游校验（鉴权前移给 BFF）
- 不在网关维护本地续期状态——那会退化成一整套有状态会话管理，与网关零数据库依赖冲突

**代价**：WebChat 能做到"会话被踢立即断流"，MSSW 做不到——但这不落在我方：BFF 在转发/重连时已把失效 token 挡在外面。我方负责的是单个连接的资源寿命。这是 `exp = -1` 的直接后果，需在详细设计里记录。

### 3.8 R7 错误码

新增场景：JWT 无效、用户未映射、通道无权限、游标异常。

游标三类（概设 :1056-1060）：

| 码 | 行为 |
|---|---|
| `CURSOR_EXPIRED` | 409 + 真实水位，**网关按历史补全** |
| `CURSOR_AHEAD` | 409，不静默从头重放 |
| `CURSOR_RUN_MISMATCH` | 400，拒绝跨 run 订阅 |

**两处需在适配层补齐**（代码现状不足）：

1. `CURSOR_RUN_MISMATCH` 在 Runtime 侧**从不抛出**（grep `apps/agent-runtime/` 零命中，只在契约层 `validate_run_cursor` 有）。需网关自己比对 `run_id`。
2. `CURSOR_EXPIRED` 时"按历史补全"是概设要求，现有实现只抛错不补全。
   **前提已成立**：`event_tail_hub.py:669-674` 的 `earliest_seq` 与历史接口的 `through_seq` 同属一个 canonical seq 空间（`webchat_history.py:375`），网关可直接用 `earliest_seq` 之前的区间调历史分页补全，无需做水位换算。

需同步 `ErrorCode` + `config/api-messages.yaml` 双语，否则 `tests/test_error_catalog.py` 会红。

### 3.9 R8 测试

至少覆盖：

- **验签**：正确 / 错签名 / `aud` 不匹配 / `iss` 不匹配 / `alg=none` / HS256 / 缺 `job_number`
- **工号映射**：`/permissions` 上未映射 → 200 `visible:false`；其余需身份的接口上未映射 → 403 `MSSW_USER_NOT_MAPPED`
- **权限查询四分支**：有授权（`visible:true`）/ 无授权（`visible:false`）/ 工号未映射（`visible:false`）/ **入口未配置（404 `MSSW_CHANNEL_NOT_CONFIGURED`）**——前三种均 200 且都带 `entry_id`/`agent_id`
- **SSE**：长连接不断流、`after_seq`/`Last-Event-ID` 取大值、`CURSOR_EXPIRED` 转历史补全
- **通道隔离**：MSSW 路由不得以 WEBCHAT 身份落库（覆盖 6 处通道名参数化）

---

## 第四部分：约束与风险

### 4.1 必须遵守的约束（来自概设）

| 约束 | 出处 |
|---|---|
| 平台侧不直连 IM/WS，只暴露 HTTP + SSE | :1069 |
| 两层鉴权是 AND 关系，我们只做 Claw 侧 | :1071 |
| permissions 查一次，不缓存，不作运行时依据 | :1072 |
| SSE 建连后不因 token 过期断流 | :983 |
| 归属校验必须带用户身份，不能只凭 `run_id` | :1062 |
| 附件禁止 302，由网关流式代理 | :1063 |
| **游标只透传不改写，取大值，不得重置为 0** | :1054、:1083 |
| `provider_seq` 与对外 `seq` 分离 | :1038 |
| `customer_id` 为空，不携带客户数据 | :1067 |

### 4.2 风险清单

| 风险 | 影响 | 处置 |
|---|---|---|
| `run_id` 越权 | Runtime 的 `_load_run_in` 只按 `id + tenant_id` 查，不带 user_id，同租户内拿到 `run_id` 即可越权读 | 调用方（网关）必须按 `platform_user_id` 复核归属（Q24） |
| `CURSOR_EXPIRED` 无补全数据 | `check_cursor` 对过期游标只抛错、不返回补全数据（`event_tail_hub.py:669-674`） | R6 自行编排"过期 → 转历史接口"；两者同属一个 canonical seq 空间（Q20-c），无需水位换算 |
| **历史 token 重放** | `exp` 恒为 -1、吊销在 MSSW 侧 Redis，我方不可见；若 BFF 侧实现不到位，已登出用户可用旧 token 继续访问 | **责任前移给 BFF**（§3.3.1）：转发前校验 token 有效性；我方只保留连接寿命 |
| 生产密钥未取得 | 无法**生产**联调（开发环境值已实测确定，不阻塞开发） | M5，需求方"后续会给" |

> 已解除的两条（跨站 Cookie、游标水位不可换算）不再列于此：前者由 Q27 确认非跨站，后者由 Q20-c 代码事实确认是同一套水位。

### 4.3 明确不做的事

- **不做 token 有效性/吊销判定**——吊销状态在 MSSW 侧 Redis，我方无数据源；责任在 BFF 转发前（§3.3.1），因此不索取 token 校验接口（Q30 已撤销）、不建本地黑名单、不设签发时间窗
- 不把验签挪出网关（边缘职责内推，见 3.3）
- 不引入第二套认证域（不复用 CHAT Cookie）；不改动 WEBCHAT / WECOM 任何行为
- 不复刻签发方的免校验分支；不实现 AES 解密
- **不做密钥轮换**——密钥固定（Q5 已定），单密钥静态配置，不做多密钥并存/版本槽位
- 不做客户维度隔离（`customer_id` 全仓不存在）；不做用户自动建档
