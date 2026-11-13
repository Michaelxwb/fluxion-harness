# MSSW 通道 — 用户旅程梳理

> 关联：`01-jwt-spec.md`（JWT 事实）、`02-requirements.md`（需求分解）、`03-open-questions.md`（决策与风险清单）
> 说明：本文描述**端到端旅程**，每段标注「谁做什么 → 打到哪个接口 → 落到我方哪个模块」。

## 0. 角色与信任边界

```
用户 ──浏览器──> MSSW BFF ──HTTP+JWT──> fluxion im-gateway ──HTTP──> Console / Runtime
      (平台侧)          (上游)             (我方入口，验签)         (我方内部)
```

| 角色 | 职责 | 信任级别 |
|---|---|---|
| 用户 | 在 MSSW 工作台使用智能客服 | 未受信 |
| MSSW BFF | 已登录用户的会话代理，透传 JWT | 半受信（JWT 可信，请求内容需校验） |
| im-gateway（MSSW 通道） | 验签、映射用户、转发 | 边界执行点 |
| Console | 用户映射与授权判定 | 内部受信 |
| Runtime | Run / 事件 / 历史 | 内部受信 |

**关键原则**：只有 JWT 签名能证明"调用方是 MSSW"；请求体内的 `user_id`、`tenant_id` 等一律不可信，必须以 JWT claims 为准。

## 1. 身份校验的核心约定（已确认）

| 项 | 值 | 依据 |
|---|---|---|
| 身份字段 | JWT 的 **`job_number`**（工号，5-7 位字母数字） | 需求方确认：MSSW 工号 = 平台账户字段 |
| 唯一性 | 活跃用户内唯一（`FindOne` + `is_deleted: 0`） | `expertmodel.go:165-179` |
| 映射目标 | `control.channel_identity.external_user_id`（值=工号）→ `platform_user_id` | `channel_service.py:81-82` |
| 校验方式 | 简单比对：工号能映射到本地平台用户即通过 | 需求方："做简单点，用户ID或标识比较即可" |
| `_id` 的用途 | 仅日志/审计参考，**不作映射键** | — |

> 为什么不选 `_id`：`_id` 是 MongoDB ObjectId，对运维不可读，且与平台账户页面展示的工号不一致；工号可读、唯一、且是业务方的账户标识。
> **工号全局唯一（已确认，Q8）**；工号变更由人为控制账户删除处理（已确认，Q6/Q25）。

## 2. 旅程 0：通道开通（前置一次性动作，不是用户触发的）

这是**任何用户旅程开始前的准备阶段**，由管理员/运维在 Console 侧完成。没有这一步，后面所有旅程都无法进行。

### 阶段 0-A：通道上线（研发/部署）

| 步 | 动作 | 落点 |
|---|---|---|
| 0A1 | 在 `BUILTIN_CHANNEL_NAMES` 中新增 `MSSW`（当前为 `("WECOM", "WEBCHAT")`） | `packages/contracts/src/muad_contracts/enums.py:7` |
| 0A2 | **必须**同时归入 `LOCAL_STREAM_CHANNELS`（MSSW 出站靠 SSE 订阅面，无 IM 主动推送）——见下方"为什么是必须" | `enums.py:175` |
| 0A3 | 同步改两处硬断言：`BUILTIN_CHANNEL_NAMES == ("WECOM", "WEBCHAT")` 必须改成三元组 | `tests/test_attachment_contract.py:118`、`tests/agent_worker/test_capacity_webchat_worker_notifications.py:824` |
| 0A4 | 确认机检通过：通道名只能在一处声明 + 内建通道被两个能力组完全划分 | `tests/test_attachment_contract.py::test_b07_*`（`:113`、`:228`） |
| 0A5 | 注册通道适配器：在 `build_channel_registry` 里注册 MSSW 适配器 | `channels/registry.py:41-60` |
| 0A6 | 注入 JWT 配置：公钥（PKCS#1 PEM，PyJWT 可直接加载）、`issue`、`audience`、`salt`。**静态单密钥，不轮换、不做版本槽位**（Q5 已定） | 配置/环境变量 |
| 0A7 | 部署网关，启动自检通过 | — |

> **为什么 0A2 是"必须"而不是"判断"**：`tests/test_attachment_contract.py:234-235` 断言
> ```python
> assert OUTBOUND_CHANNELS.isdisjoint(LOCAL_STREAM_CHANNELS)
> assert set(BUILTIN_CHANNEL_NAMES) == set(OUTBOUND_CHANNELS) | set(LOCAL_STREAM_CHANNELS)
> ```
> 即**内建通道必须被这两组完全划分**。只加 `BUILTIN_CHANNEL_NAMES` 而不加 `LOCAL_STREAM_CHANNELS`，第二个断言立刻失败。
>
> **不加分组还会造成运行期错误**：`AttachmentTool` 的交付路由在 `apps/agent-runtime/.../attachments/tools.py:739-744` 先查 `LOCAL_STREAM_CHANNELS`→发布到会话流，再查 `OUTBOUND_CHANNELS`→走网关投递，都不在则抛 `ATTACHMENT_WRITE_UNAVAILABLE`（"当前通道不支持交付产物"）。MSSW 会话里生成产物会直接失败。

> **注意**：`BUILTIN_CHANNEL_NAMES` 只是"内建通道名"的声明列表，而契约字段 `ChannelName` 本身是**开放 string**（`enums.py:7-9`）。新增通道**不改契约类型**，只需要在常量里登记名字供默认值/文档/机检使用。

### 阶段 0-B：租户与机器人配置（Console 管理员）

| 步 | 动作 | 落点 |
|---|---|---|
| 0B1 | 在 Console 为 MSSW 租户创建一个 bot 记录：`channel=MSSW`、`bot_id=<固定标识>`、`agent_id=<绑定的 Agent>`、`enabled=true` | `control.bot_account`（`infrastructure/models/channel.py:17-54`） |
| 0B2 | 上传/生成 bot 的 `secret`（若需要） | 同上 `secret` 列 |
| 0B3 | 可选：`config_json` 挂通道级扩展配置（如允许来源、能力开关） | 同上 `config_json` JSONB |
| 0B4 | 确认该 Agent 已对目标用户授过 `AgentAccessGrant` | `control.agent_access_grant`（`control.py:104-125`） |

> **入口约定（Q15 已定）**：MSSW BFF 只有**一个**上游服务，因此网关配置一个固定的 `bot_id`；Console `resolve` 按其定位 bot 记录并返回 `entry_id`（= `bot_account.id`）。`/permissions` 判定该 bot 挂的 Agent 是否在用户授权集合内；**`entry_id` 由 `/permissions` 响应一并发给 BFF**（`resolve` 是网关内部接口，BFF 看不到），后续消息接口直接用它，**无需推导**。

### 阶段 0-C：用户身份映射（关键，决定首访能否通过）

| 步 | 动作 | 落点 |
|---|---|---|
| 0C1 | 把"工号"与"本地平台用户"建立映射 | `control.channel_identity`（`channel.py:57-90`） |
| 0C2 | `identity_key = f"{channel}:{bot_id}:{external_user_id}"`，其中 `external_user_id` = 工号 | `channel_service.py:81-82` |
| 0C3 | 写入时 `tenant_id`、`bot_account_id`、`platform_user_id`、`bound_at` 必填 | `channel.py:71-89` |
| 0C4 | 唯一性：(tenant_id, identity_key) 且未软删 | 唯一索引 `uq_channel_identity_tenant_identity_key`（`channel.py:60-66`） |

**映射怎么建（D5 已定）**：**管理员在 Console 新建平台用户时，`user_code` 直接填 MSSW 工号。**

```
第 1 步 建人：POST /api/v1/users { user_code: "14806", display_name, status: "ACTIVE" }
  → create_user（user_service.py:62-82），写 platform_user，user_code = "14806"
  → 查重 find_by_code，冲突抛 USER_CODE_EXISTS；唯一约束 uq_platform_user_tenant_user_code
第 2 步 建映射：为该用户写一行 channel_identity
  → external_user_id = "14806"（与 user_code 同值）
```

**两步都不能省**：`platform_user.user_code` 回答"这人是谁"，`channel_identity` 回答"这个外部标识对应哪个平台用户"；运行时 `resolve` 查的是**后者**，只建人不建映射仍然 `bound=false`。

**`channel_identity` 由谁写入**（第二步的落地方式，待实现时定）：

| 方式 | 说明 | 注意 |
|---|---|---|
| ① 建用户时顺带建映射 | 扩展 `create_user` 或加一步 Console 操作，直接用 `user_code` 作 `external_user_id` | 最省事，口径最一致（工号天然一致） |
| ② 复用绑定码 | 用户用一次性绑定码自助绑定（现有流程，`BindCode` + `/bind`） | `bind()` 的 `external_user_id` 来自**请求参数**（`channel_service.py:215-261`），不自动取 `user_code`，需保证用户输入的正是工号 |

**已确认（Q13）**：**没有权限**的用户直接拒绝，不自动建档（不做 JIT provisioning），所以映射必须在访问前就绪。

> **`user_code` 与 `platform_user_id` 的关系（易混淆，必须分清）**：
>
> | 概念 | 是什么 | 何时用 |
> |---|---|---|
> | `platform_user.user_code` | 人的**业务工号**（字符串，如 `14806`）。唯一约束是 `(tenant_id, user_code)` | **只在建立映射时**用来找到对应的人 |
> | `platform_user.id` | 数据库**主键 UUID**。这就是 `platform_user_id` | 映射写入 `channel_identity.platform_user_id`，运行时全程使用 |
> | `channel_identity.external_user_id` | 外部平台的用户标识，MSSW 场景下**值 = 工号** | `resolve` 按它查映射行 |
>
> **运行时 `resolve` 不比对 `user_code`**——它按 `(tenant_id, channel, bot_account_id, external_user_id)` 直接查 `channel_identity` 行，取该行的 `platform_user_id`（`channel_service.py:99-127`）。"工号 ↔ user_code"的比对只发生在映射建立那一刻。映射行不存在就返回 `bound=false`，不会回退去找 `user_code`。

> **WEBCHAT 的对比**：WEBCHAT 通道**不写 `channel_identity`**。它直接以 `external_user_id = str(principal.platform_user_id)` 组装消息（`webchat_service.py:243`），因为用户本身就是本地 `platform_user`，不需要外部映射。MSSW 不同——必须有工号→用户的映射行。

### 阶段 0-D：联调验证（与 MSSW 侧）

| 步 | 动作 |
|---|---|
| 0D1 | 用 MSSW 提供的真实 token 验证验签通过 |
| 0D2 | 验证工号能映射到本地用户 |
| 0D3 | 验证 `/permissions` 返回正确 |
| 0D4 | 端到端跑通一次对话 |

> 开发环境实测已通过（2026-11-13）：用真实 token + 提供的公钥/`issue`/`audience`/`salt`，验签成功，`aud`/`iss` 密文比对一致，`job_number=14806`、`_id=6a4ca2554a2a45d4b1ad5996`。详见 `01-jwt-spec.md` §5。

## 3. 旅程 A：首次进入 — 权限探测（决定探索版 Tab 是否可见）

**触发**：用户打开 MSSW 工作台，BFF 判断是否展示"智能客服"能力入口。

| 步 | 动作 | 接口 | 我方落点 |
|---|---|---|---|
| A1 | BFF 携带用户 JWT 请求权限 | `GET /api/v1/mssw/permissions` | **新增路由**，判定复用 `/entries` |
| A2 | 网关验签（RS256 + 算法白名单 + 必需 claim） | — | 新增 `MsswJwtVerifier` |
| A3 | 校验 `job_number` 存在 | — | 同上 |
| A4 | 网关向 Console 查询用户映射与授权 | `POST /internal/channel/resolve` | 复用 `ChannelService.resolve` |
| A5 | Console 查 `channel_identity` → `platform_user_id`，再判授权 | — | 复用现有（`channel_service.py:95-129`） |
| A6 | 网关按 MSSW 通道过滤入口，判定 `bool(items)` | — | 抽取 `list_authorized_entries` 共享函数 |
| A7 | 网关返回 `{"visible": bool, "entry_id": uuid, "agent_id": uuid}` | — | 新增响应契约 |
| A8 | BFF 据 `visible` 决定 Tab 可见性，并**缓存 `entry_id`** 用于后续路径段 | — | — |

**分支与结果**（与 `05` §4.1 完全一致）：

| 分支 | HTTP | 响应 |
|---|---|---|
| 验签失败 / 缺 `job_number` | 401 | `MSSW_JWT_INVALID`（fail-closed，不降级） |
| **入口未配置或已停用** | **404** | `MSSW_CHANNEL_NOT_CONFIGURED`——入口不存在，`entry_id` 拿不到 |
| 工号无映射 | 200 | `{"visible": false, "entry_id": ..., "agent_id": ...}` |
| 有映射但无授权 | 200 | `{"visible": false, "entry_id": ..., "agent_id": ...}` |
| 有映射有授权 | 200 | `{"visible": true, "entry_id": ..., "agent_id": ...}` |

> **注意**：两种"不可见"（工号无映射 / 无授权）在响应体上**不作区分**——BFF 只需展示或隐藏 Tab，不需要知道原因；差异落日志，不泄露"该工号是否存在"。**但"入口未配置"是另一回事**，走 404 `MSSW_CHANNEL_NOT_CONFIGURED`，因为此时 `entry_id` 不存在，属于配置故障而非用户不可见。
>
> **`entry_id` 的两条来源**：网关内部由 Console `resolve` 响应返回、随 `platform_user_id` 进入 `MsswPrincipal`（Q15 已定）；**BFF 侧只能从 `/permissions` 响应拿到**（`resolve` 是内部接口）。

**约束**：
- 不缓存（上游明确要求"不得长期缓存或持久化"）
- 此接口调用时**无会话上下文**，JWT 中不应有 `conv`
- 此结果**不可作为运行时授权依据**，每次 Run 必须复核

## 4. 旅程 B：发起对话

**触发**：用户在工作台输入消息并发送。

| 步 | 动作 | 接口 | 我方落点 |
|---|---|---|---|
| B1 | BFF 携带 JWT + 消息体请求 | `POST /api/v1/mssw/entries/{entry_id}/messages` | **新增路由**，路径与 WebChat 同构 |
| B2 | 网关验签 + 工号映射（同旅程 A） | — | 复用 |
| B3 | 校验该用户对目标 Agent 有权限 | — | 复用 `resolve` |
| B4 | 归属校验：`platform_user_id` 比对会话/run 的 `user_id` | — | 复用现有模式 |
| B5 | 网关调 Runtime 受理 Run | `WebchatService.submit_message`（**已是 202 受理语义**） | 复用，不改 Runtime |
| B6 | 立刻返回 `202 + run_id`，**不含正文** | — | 复用现有 `WebchatMessageAccepted` |
| B7 | 幂等：同 `Idempotency-Key` 返回同一 `run_id` | — | 复用 Runtime 幂等键机制 |

**与现有实现的差异（已澄清）**：

Runtime 的 `POST /v1/runs` 确实**直接返回 SSE 流**，但**网关层早已提供 202 受理语义**——`WebchatService.submit_message`（`webchat_service.py:224-251`）是"受理持久化后才返回 202，长流不占本处理器"，正合 BFF 要求。

所以改造点只有两处：**换路由前缀为 `/api/v1/mssw`（`entry_id` 路径段保留，与 WebChat 同构）**、**把身份来源从 `ChatIdentity` 换成 `MsswPrincipal`**。内部仍是 `runtime_client.submit_run(...)`，**不需要改动 Runtime**。详见 `05-interface-mapping.md` §4.2。

## 5. 旅程 C：接收流式回复

**触发**：BFF 拿到 `run_id` 后立即建立 SSE 连接。

| 步 | 动作 | 接口 | 我方落点 |
|---|---|---|---|
| C1 | BFF 携带 JWT 订阅事件 | `GET /api/v1/mssw/runs/{run_id}/events?after_seq=N` | **复用**，换 Guard |
| C2 | 网关验签 + 归属校验（该 `run_id` 属于该用户） | — | **新增** |
| C3 | 网关打开 Runtime 事件流 | Runtime `GET /v1/runs/{run_id}/events` | 复用 `open_events` |
| C4 | 事件投影：补 `conversation_id`（**游标不转换**，`after_seq` 原样透传） | — | **新增**投影层 |
| C5 | 推送 SSE 帧 | — | 复用现有 SSE 机制 |
| C6 | 断线重连：`after_seq` 续传 | — | 复用 Runtime `after_seq` 能力 |

**关键约束**：
- **建连时校验一次**（验签 + 工号映射），连接内不做周期复核
- **token 是否仍有效/未吊销由 BFF 在转发前判定**——上游只有 BFF，它挡掉失效请求，到达网关的都是有效请求（`02` §3.3.1）
- 单轮 run 时长上限由启动资源预算决定，作为**纯资源**安全策略（与 token 是否有效无关）
- 重连由 BFF 重新发起，其合法性仍由 BFF 保证
- 游标异常映射为稳定码：`CURSOR_EXPIRED`/`CURSOR_AHEAD`/`CURSOR_RUN_MISMATCH`

**为何本地不做过期判定**：`exp` 恒为 -1（`01` §2.1）；`time` 是签发时刻，做时间窗会误杀活跃用户且分不开旧 token 重放。鉴权前移到 BFF，我方只负责单个连接的资源寿命。见 Q11/Q12。

## 6. 旅程 D：取消对话

**触发**：用户点击"停止"。

| 步 | 动作 | 接口 | 我方落点 |
|---|---|---|---|
| D1 | BFF 携带 JWT 请求取消 | `POST /api/v1/mssw/runs/{run_id}/cancel` | **直接复用** |
| D2 | 网关验签 + 归属校验 | — | **新增** |
| D3 | 网关调 Runtime 取消 | Runtime `POST /v1/runs/{run_id}/cancel` | 复用 `cancel_run` |
| D4 | 幂等：已终态直接返回当前终态 | — | 复用 Runtime 语义 |

## 7. 旅程 E：查看历史

**触发**：用户重新打开某个会话。

| 步 | 动作 | 接口 | 我方落点 |
|---|---|---|---|
| E1 | BFF 携带 JWT 拉取历史 | `GET /api/v1/mssw/conversations/{conversation_id}/messages` | **复用**，游标直接透传 |
| E2 | 网关验签 + 会话归属校验 | — | **新增** |
| E3 | 网关调 Runtime | Runtime `GET /internal/webchat/conversations/{id}/messages` | 复用 `list_messages` |
| E4 | 分页：BFF 直接使用我方 `page` + `page_size` + `through_seq`，**网关零适配** | — | 无需改动 |

**注**：上游规定 **MSS-Claw 历史是唯一展示来源**，BFF 不缓存正文。

## 8. 旅程 F：会话管理（选配）

| 步 | 动作 | 接口 |
|---|---|---|
| F1 | 新建会话 | `POST /api/v1/mssw/entries/{entry_id}/conversations` |
| F2 | 幂等：**以我方 `Idempotency-Key` 为准**（Q19 已定，不由 BFF 传 `conversation_id`） | 同 F1，带 `Idempotency-Key` 头 |

**F2 已定（Q19）**：幂等身份完全由我方构造。Runtime 侧 `_create_conversation`（`run_service.py:1751-1765`）用 `Idempotency-Key` + 请求指纹去重，指纹里的 `tenant_id`/`agent_id`/`platform_user_id`/`entry_id`/`external_thread_id` **五个键全是我方的值**；命中则原样重放、不建第二个会话（`:1762-1765`）。

所以：
- BFF **不需要**提供 `conversation_id`，也**不需要**维护映射；
- BFF 只需在重试时**原样回传上次用的 `Idempotency-Key`**；
- 新建成功后在响应里拿我方 `conversation_id`（`webchat_service.py:296-303`）存下来用于后续接口。

**未配置即关闭**：若该能力未开启，网关返回 **404 `MSSW_CHANNEL_NOT_CONFIGURED`**（Q18 已定；不用 501——501 表示"服务器不支持该功能"，而这里是**配置未开**，404 更准确且与路由层一致）。

## 9. 旅程 G：附件（选配）

| 步 | 动作 | 接口 |
|---|---|---|
| G1 | 上传附件 | `POST /api/v1/mssw/entries/{entry_id}/attachments` |
| G2 | 返回 `upload_id` / `expires_at` / `checksum`（**我方字段名不改**；若 BFF 对外叫 `provider_upload_id`，由它自己映射） | — |
| G3 | 下载附件内容 | `GET /api/v1/mssw/artifacts/{artifact_id}/content` |

**约束**：下载必须做归属校验（只能取自己的附件）；上游要求 `provider_upload_id` 与 `checksum` 对齐——**对齐发生在 BFF 侧**，我方只管 `upload_id`/`checksum` 原值返回。

## 10. 横切关注点

### 10.1 错误处理

| 场景 | 期望 |
|---|---|
| JWT 签名错误 / 格式错 | 401 `MSSW_JWT_INVALID`，fail-closed |
| 缺 `job_number` claim | 401 `MSSW_JWT_INVALID` |
| 入口未配置或已停用 | 404 `MSSW_CHANNEL_NOT_CONFIGURED`（所有需入口的接口） |
| 工号未映射 | `/permissions` 返回 `200 {"visible": false, "entry_id": ..., "agent_id": ...}`；其余需身份的接口 → `403 MSSW_USER_NOT_MAPPED` |
| 有映射但无授权 | `/permissions` 返回 `200 {"visible": false, ...}`；其余接口 → `403 MSSW_CHANNEL_FORBIDDEN` |
| 无 Agent 权限 | 403 |
| 会话/Run 不属于该用户 | 404（不泄露存在性） |
| 游标异常 | 409/400（按类型） |
| Runtime 不可用 | 502/503 + 可重试标记 |

### 10.2 幂等

- `POST /messages`：`Idempotency-Key` 必填，重复返回同一 `run_id`
- `POST /cancel`：天然幂等
- `POST /conversations`：`Idempotency-Key` + 请求指纹去重（Q19），**不由 BFF 传 `conversation_id`**

### 10.3 审计

- 每次验签失败记录：`job_number`（若可解析）、失败原因、来源 IP
- 每次 Run 记录：`job_number` → `platform_user_id` 的映射结果

### 10.4 通道隔离

- 独立身份依赖 `MsswPrincipalDep`（与 `ChatIdentityDep` 并列，Cookie 名不同天然隔离）
- 新增路由挂在独立前缀 `/api/v1/mssw/` 下，与 `/api/v1/webchat/` 并列，互不影响
- 独立适配器，不改动 `channels/webchat.py` / `webhook.py`
- 独立验签器，不改动现有 HMAC 验签
- 测试需覆盖"MSSW 请求不影响 WEBCHAT / WECOM 行为"

---

## 附录 A：conv / run_id / user_id 的定义与现有 IM 归属做法

> 回答 Q23 / Q24。全部为代码事实，带 file:line。

### A.1 四者的定义与关系

| 概念 | 定义 | 位置 |
|---|---|---|
| `conversation.id` | `runtime.conversation` 表主键，**UUID**，DB 生成 | `infrastructure/models/runtime.py:14` |
| `run.id` | `uuid.uuid4()`，提交时生成 | `application/run_service.py:1384` |
| `user_id` | `runtime.conversation.user_id` / `runtime.run.user_id`，均为 UUID，指向 `platform_user` | `runtime.py:37-47`、`runtime.py:294-299` |
| `platform_user_id` | Runtime 入参名，**直接落为 `conversation.user_id`**——与 `user_id` 是同一个 UUID | `application/channel_conversations.py:83` |

会话的归因维度是五元组：`(tenant_id, user_id, agent_id, channel_account_id, external_thread_id)`（`runtime.py:37-47`）。

**一个会话可以有多个 run**；同一时刻至多 1 个活跃 run（CREATED/RUNNING/WAITING_TOOL/WAITING_INPUT 的部分唯一索引，`runtime.py:247-254`）。会话记录最近一次 run 的 id（`run_service.py:2150`）。

### A.2 现有 IM 通道怎么校验会话归属

**结论：Runtime 侧一律按 `(tenant_id, user_id)` 查库比对，不查 `channel_identity`；Gateway 侧用主体里的 `platform_user_id` 比对 run 的 `user_id`。不符统一返回 `COMMON_NOT_FOUND`（不泄露存在性）。**

| 场景 | 做法 | 位置 |
|---|---|---|
| 会话解析 | `Conversation.id + tenant_id + user_id + is_deleted=false`，缺失即 `COMMON_NOT_FOUND` | `application/webchat_history.py:340-349` |
| 消息列表 | 先 `load_explicit_conversation(_conversation_scope(scope))`，scope 含 tenant/user | `application/webchat_history.py:371-374` |
| 会话列表 | 条件含 tenant/user/agent/channel_account_id | `application/webchat_history.py:114-120` |
| Gateway 侧 Run 归属 | 比对 `state.user_id` 与 `identity.principal.platform_user_id` | `apps/im-gateway/.../application/webchat_service.py:184-188` |
| 幂等重放归属 | 比对 `submission.actor_user_id` 与 `conversation_id` | `application/run_service.py:1864-1867` |
| 会话删除 | 按 `(tenant, user, conversation)` | `application/conversation_deletion.py:77` |

**注意一处隐性依赖**：`_load_run_in` 只按 `id + tenant_id + is_deleted` 查（`run_service.py:1873-1879`），**不带 user_id**——run 级用户鉴权依赖调用方上层先做。MSSW 通道必须自己在网关或 service 层补这一步，不能指望 Runtime 兜底。

### A.3 对 MSSW 通道的直接结论

1. **`conv` 就是我方的 `conversation_id`（UUID），不做映射**（Q28）。上游若对外叫别的名字，由 BFF 自己映射，我方既不新增字段也不借用 `external_thread_id`。
2. **归属校验不需要新建表**：JWT 的 `job_number` → Console 解析出 `platform_user_id` → 拿这个 UUID 去比对 `conversation.user_id` / `run.user_id` 即可。与 WEBCHAT 完全同构。
3. **`run_id` 归属必须显式查**：不能只凭 `run_id` 放行。要么查 Runtime 拿 `user_id`，要么网关侧维护 `run_id → platform_user_id` 的短期缓存（幂等窗口内）。

**`entry_id` 与 `conversation_id` 稳定性（回答"第二次进来 ID 会不会变"）**：

| ID | 层级 | 生成时机 | 是否稳定 |
|---|---|---|---|
| `entry_id` | 入口（`control.bot_account.id`） | 管理员建通道时一次 | **永不变**，单入口下全租户就一个值 |
| `conversation_id` | 会话（`runtime.conversation.id`） | 每建一个新会话时 | **永不变**，创建后写死在会话行 |

所以：同一用户第二次进来，`entry_id` 还是同一个（同一个 `bot_id` → 同一条 `bot_account` → 同一个 `id`）；而他**新开**的会话会拿到**新的** `conversation_id`，旧会话的 `conversation_id` 保持不变。历史会话读取按 `(tenant_id, user_id, conversation_id)` 定位，与会话行里记录的 `channel_account_id`（= 创建时的 `entry_id`）一起构成归属，因此不受任何 ID 变动影响。详见 `05` §4.1.1。

---

## 附录 B：游标（cursor）语义

> 回答 Q20。分三层：概设口径（必守）、我方内部机制（现状）、对 BFF 的接口约定（已定）。

### B.0 概设口径（最高优先级，必须遵守）

概设在两处重复了同一原则（`:1054`、`:1083`）：

> **游标只透传不改写**：重连以 `after_seq` / `Last-Event-ID` 中更大值为准；适配器不得把游标重置为 0，也不得静默全量重放。

推论（对本实现的硬约束）：

| 约束 | 含义 |
|---|---|
| 取大值 | `after_seq` 与 `Last-Event-ID` 同时存在时取更大者，绝不回退重投 |
| 不得重置为 0 | 不能"忘了游标就从头来" |
| 不得静默重放 | 游标无效时显式报错，不偷偷全量重发 |
| 不做换算 | `provider_seq` 与对外 `seq` 分离（`:1038`），中间不转换 |

**这两套游标是分开的**，不要统一命名：

| 用途 | 我方入参 | 我方出参 |
|---|---|---|
| 事件流（SSE） | `after_seq` + `Last-Event-ID` | 事件帧的 `id` |
| 历史分页 | `page` + `page_size` + `through_seq` | `items[]` + `through_seq` |

> 概设 `:633` 写的是**平台侧 BFF 对前端**的分页契约（`cursor` 入参、`items[]` + `next_cursor` 出参），不是我方网关的契约。BFF 适配我方接口，`cursor` 编解码由 BFF 自己做。

### B.1 我方内部机制：事件流游标

**游标是会话级单调递增的整数 `seq`**，不是不透明串。

| 项 | 说明 | 位置 |
|---|---|---|
| 存储 | `seq` 列，会话内单调递增 | `runtime.py:361` |
| 唯一约束 | `(conversation_id, seq)` | `runtime.py:391` |
| 分配 | 每会话 `last_seq += N RETURNING`，同事务内按偏移赋值 | `application/event_batcher.py:420-429` |
| 续传 | `tail_run_events(run_id, tenant_id, after_seq=0)` | `application/run_service.py:1498-1518` |

**网关侧现有实现**（`api/webchat.py:208-219`，与概设口径完全一致）：

```python
cursor = after_seq or 0
if last_event_id is None:
    return cursor
value = last_event_id.strip()
if not (value.isascii() and value.isdigit()):
    raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
return max(cursor, int(value))
```

注意：`Last-Event-ID` 里放的就是 **seq 数字的字符串形式**（前端 `stream.ts:883` 是 `String(cursor)`）。这是对 SSE 标准"id 可为任意字符串"的务实简化。

### B.2 我方内部机制：游标异常（注意有两套实现）

**代码里存在两套并行判定**，能力范围不同，这点必须分辨清楚：

**实现一：契约层 `validate_run_cursor`**（`packages/contracts/src/muad_contracts/webchat_events.py:190-206`）——纯函数，对"已取到的窗口快照"校验，**三种都判**：

```python
if request.run_id != window.run_id:                 code = "CURSOR_RUN_MISMATCH"
elif request.after_seq < window.minimum_after_seq:  code = "CURSOR_EXPIRED"
elif request.after_seq > window.last_seq:           code = "CURSOR_AHEAD"
```

**实现二：Runtime 实时判定 `check_cursor`**（`application/event_tail_hub.py:659-675`）——直接查库拿 `through` 与 `floor`，**只抛两种**：

| 异常 | 触发条件 | 抛出处 |
|---|---|---|
| `CURSOR_AHEAD` | `after_seq > through` | `event_tail_hub.py:660-665` |
| `CURSOR_EXPIRED` | `after_seq + 1 < floor`（floor 非 None） | `event_tail_hub.py:669-674` |
| `CURSOR_RUN_MISMATCH` | 游标与 run 不匹配 | **仅契约层**；`grep CURSOR_RUN_MISMATCH apps/agent-runtime/` **零命中** |

概设要求的行为映射（`:1056-1060`）：

| 异常 | 概设要求 |
|---|---|
| `CURSOR_EXPIRED` | 409 + **真实水位**，由网关**按历史补全** |
| `CURSOR_AHEAD` | 409，不静默从头重放 |
| `CURSOR_RUN_MISMATCH` | 400，拒绝跨 run 订阅 |

**关键设计**：**不静默夹取**——过期就显式报错，而不是"从最早的给你"。上游必须能处理这三个码。

> **两个需要在适配层补的行为**：
> 1. **`CURSOR_RUN_MISMATCH`**：Runtime 侧不抛。因为 `tail_run_events` 第一步按 `run_id` 加载 run（`run_service.py:1498-1518`），run 不对直接 404。要按概设返回 400 `RUN_MISMATCH`，网关需自己比对 `cursor` 中的 `run_id` 与路径参数的 `run_id`。
> 2. **`CURSOR_EXPIRED` 时按历史补全**：概设要求网关**主动**回源历史接口补齐，我方现有实现只是抛错给客户端。适配层需新增这一动作。

### B.3 我方内部机制：历史消息分页

| 项 | 说明 | 位置 |
|---|---|---|
| 分页参数 | `page` + `page_size` | `api/webchat_history.py:281-284` |
| 快照冻结 | 首屏把 `conversation.last_seq` 冻结为 `through_seq` | `application/webchat_history.py:366-385` |
| 后续页 | 必须带 `through_seq`，超过 `last_seq` 报 `CURSOR_AHEAD` | `webchat.py:171-179`（pydantic 强校验） |

**语义**：`through_seq` 是"这一轮浏览看到哪儿"的快照边界。新消息不会挤进正在翻的列表，避免翻页时重复/跳条。

### B.4 通路示意

```
BFF（后端服务）              网关                         Runtime
   |                          |                             |
   |-- GET events?after_seq=42 -->|                        |
   |                          |-- 透传 after_seq ---------->| 按 seq 取事件
   |<-- SSE 帧 id=43,44... ---|<-- 事件 -------------------|
   |                          |                             |
   |  断线重连（BFF 内部处理）  |                             |
   |-- GET events?after_seq=44                          |
   |   Last-Event-ID: 44      |                             |
   |                          |-- max(44,44)=44 透传 ------>|
```

### B.5 对 BFF 的接口约定

**BFF 直接适配我方接口，因此不存在"两套 cursor 格式"问题。**

约定如下（BFF 侧按其对外契约自行转换）：

| 场景 | 我方参数 | 我方出参 |
|---|---|---|
| 事件流 | `after_seq`（query）+ `Last-Event-ID`（header），取大值 | SSE 帧 `id: <seq>` |
| 历史分页 | `page` + `page_size` + `through_seq` | `items[]` + `through_seq` |

**历史分页的用法**：首屏不传 `through_seq`，响应会带回；第 2 页起**必须**带上次返回的 `through_seq`，否则 422。

**Q20-c 已定：是同一套水位**。历史分页的 `through_seq` 取自 `conversation.last_seq`（`webchat_history.py:375`），事件流 `check_cursor` 的 `through` 取 `watermark()`（`event_tail_hub.py:652-675`），两者同属一个 canonical seq 空间，可以直接互相续接。

因此"`CURSOR_EXPIRED` 时按历史补全"在**数据层面可行**：网关拿 `earliest_seq`（`event_tail_hub.py:673`）当上界、调历史分页取回缺失区间即可，不需要水位换算。剩下要实现的只是编排本身（现有 `check_cursor` 只抛错不补全）。

### B.6 两个必须告知 BFF 的约定

1. **`CURSOR_EXPIRED` 是正常情况，不是故障**。用户关页面一整天再回来，游标过期是预期行为。BFF 应把它当成"重新拉历史"的信号，而不是弹错误框。
2. **断线重连必须带新游标**。我们不会替调用方记住"上次看到哪儿"——同一用户可能同时开多个窗口。
