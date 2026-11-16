# MSSW 外部 IM 通道接入

上游 MSSW 平台新增智能客服能力，通过 BFF 调用本系统。本目录是**协议事实与历史决策记录**的归档。

> **需求基线已迁移**：本期需求以 `.code-flow/tasks/2026-11-13/mssw-external-im-channel/` 的 PRD 与设计为准。本目录 6 份文档已全部按新方案校对；`01-jwt-spec.md` 是 JWT 协议事实的唯一权威（身份键按新方案为 `_id` + `tenant_id`，`job_number` 仅供人读）。

## 一分钟了解

**要做什么**：新增 `MSSWWEB` 通道，BFF 是**后端服务**，带 JWT 直接转发请求过来。**验签在 Console**（网关不持因子、不持库），验签后按平台配置的 `identity_keys` 解析身份，映射到我方平台用户，然后复用现有会话能力。

**和上一版的关键差别**：

| 维度 | 上一版（已废弃） | 现行方案 |
|---|---|---|
| 通道名 | `MSSW` | **`MSSWWEB`**（另有一层 `platform_key=mssw`） |
| 验签位置 | 网关 | **Console** |
| 身份模型 | `channel_identity`（按入口建） | **`external_identity`（用户级）+ `entry_access_grant`（入口授权）** |
| 身份字段 | `job_number` | **平台可配 `identity_keys`**（MSSW 用 `_id` + `tenant_id`） |
| 未配置 | 404 `MSSW_CHANNEL_NOT_CONFIGURED` | **503 `EXTERNAL_ASSERTION_UNAVAILABLE`** |
| 会话可见性 | 按入口隔离 | **同一 user + 同一 Agent 跨入口共享** |
| 缓存 | 无 | **Redis 凭证/权限分层缓存 + auth_revision + policy_epoch** |

**通用化的边界**：这是「JWT 类外部平台」的通用方案。新增同类平台（如 MSSP）只加平台配置与入口，不复制 handler。若将来接非 JWT 平台（OAuth2、mTLS），身份模型与会话层可复用，认证适配层需另写。

## 文档地图

| 文档 | 内容 |
|---|---|
| [01-jwt-spec.md](./01-jwt-spec.md) | **协议事实权威**：JWT 结构、AES 细节、claim 清单、真实 token 实测报告 |
| [02-requirements.md](./02-requirements.md) | 需求定位、能力边界、R1-R8 分解、约束与风险 |
| [03-open-questions.md](./03-open-questions.md) | 历史决策记录（**部分结论已被新方案取代，逐条标注**） |
| [04-user-journeys.md](./04-user-journeys.md) | 端到端旅程（BFF 视角 + 管理员视角） |
| [05-interface-mapping.md](./05-interface-mapping.md) | 接口逐条对照与请求/响应契约 |

---

## BFF 视角：一次完整对话的调用链

所有请求由 BFF 服务端携带 `soc-token` Cookie 发出（非浏览器跨站，Cookie 可正常送达）。**BFF 需在转发前自行校验 token 有效性**——我方只做签名与 claim 校验，不判吊销（吊销状态在 MSSW 侧，我方无数据源）。

```mermaid
sequenceDiagram
    autonumber
    participant U as MSSW 用户<br/>（浏览器）
    participant BFF as MSSW BFF<br/>（后端服务）
    participant GW as im-gateway<br/>/api/v1/msswwweb
    participant CON as console-platform<br/>（身份权威 + 验签）
    participant RT as agent-runtime

    rect rgb(238, 245, 255)
    Note over BFF,CON: 阶段 1｜身份与可见性（决定 Tab 是否可见）
    BFF->>GW: GET /api/v1/msswwweb/identity<br/>Cookie: soc-token=[JWT]
    GW->>CON: POST /internal/channel/external-principal<br/>{channel: MSSWWEB, soc_token}
    Note over CON: 验 RS256 + AES(aud/iss) + claim<br/>按 identity_keys 取 claim → canonical JSON → sha256<br/>查 external_identity
    CON->>CON: 查 entry_access_grant + bot_account + Agent 授权
    CON-->>GW: {authenticated, platform_user_id, grants[], agents[], auth_revision, policy_epoch}
    GW-->>BFF: 200 {allowed, reason, platform_user_id, grants[], agents[]}
    Note over BFF: 未绑定 → allowed=false + UNBOUND<br/>取消 Tab 或提示联系管理员
    end

    rect rgb(245, 255, 245)
    Note over BFF,GW: 阶段 2｜入口列表与建会话
    BFF->>GW: GET /api/v1/msswwweb/entries<br/>Cookie: soc-token=[JWT]
    GW-->>BFF: 200 {items:[{entry_id, name, agent_id, allowed}]}
    BFF->>GW: POST /api/v1/msswwweb/entries/{entry_id}/conversations<br/>Idempotency-Key: [BFF 生成]
    GW-->>BFF: 201 {conversation_id}
    end

    rect rgb(255, 250, 235)
    Note over BFF,RT: 阶段 3｜发消息 202 + 订阅 SSE
    BFF->>GW: POST /api/v1/msswwweb/entries/{entry_id}/messages<br/>Cookie: soc-token=[JWT]<br/>Idempotency-Key: [BFF 生成]<br/>{conversation_id|null, message_id, text, upload_ids[]}
    GW->>RT: admit（复用现有 202 受理）
    RT-->>GW: {run_id, conversation_id}
    GW-->>BFF: 202 {run_id, conversation_id}
    BFF->>GW: GET /api/v1/msswwweb/runs/{run_id}/events?after_seq=0<br/>Cookie: soc-token=[JWT]
    GW->>CON: 复核 user + agent + expires_at
    GW->>RT: 订阅 canonical 事件流
    loop 每个事件
        RT-->>GW: delta / confirmation / terminal
        Note over GW: yield 前复核到期与授权
        GW-->>BFF: event: delta  id: [seq]<br/>data: {text}
    end
    GW-->>BFF: event: terminal
    opt 断线重连
        BFF->>GW: GET .../events?after_seq=[最后收到的 seq]
        Note over GW: 与 Last-Event-ID 取大值，不重置为 0
    end
    opt 用户点停止
        BFF->>GW: POST /api/v1/msswwweb/runs/{run_id}/cancel
        GW-->>BFF: 200
    end
    end

    rect rgb(252, 240, 250)
    Note over BFF,RT: 阶段 4｜会话列表、详情与历史
    BFF->>GW: GET /api/v1/msswwweb/conversations?agent_id=<br/>Cookie: soc-token=[JWT]
    GW-->>BFF: 200 {items:[{conversation_id, entry_id, channel, has_unread, ...}]}
    BFF->>GW: GET /api/v1/msswwweb/conversations/{id}<br/>?through_seq=[通知水位]
    GW-->>BFF: 200 {messages[], confirmation, through_seq}
    BFF->>GW: GET /api/v1/msswwweb/conversations/{id}/messages<br/>?page=1&page_size=20&through_seq=[冻结水位]
    GW-->>BFF: 200 {items[], through_seq}
    BFF->>GW: POST /api/v1/msswwweb/conversations/{id}/read<br/>{through_seq: [通知水位]}
    GW-->>BFF: 200
    end

    rect rgb(245, 245, 245)
    Note over BFF,GW: 阶段 5｜附件
    BFF->>GW: POST /api/v1/msswwweb/entries/{entry_id}/attachments<br/>multipart/form-data: file
    GW-->>BFF: 200 {upload_id}
    BFF->>GW: GET /api/v1/msswwweb/artifacts/{artifact_id}/content
    GW-->>BFF: 200 字节流（禁止 302，网关流式代理）
    end
```

### 图上几个 BFF 必须知道的点

**身份只有一条路**：所有请求都是 `Cookie: soc-token=<JWT>`。网关把 token 原样交给 Console，由 Console 验签并解析身份。BFF 不需要传任何身份参数，也**不需要** `X-Chat-Context` 之类的自定义头。

**`entry_id` 从 `/entries` 拿**：入口授权是用户级的（一个用户可能被授权多个入口），所以 `/identity` 和 `/entries` 返回的是**授权列表**，不是一个固定值。发消息、建会话、传附件的路径段用列表里的 `entry_id`。

**身份字段由平台配置决定**：MSSW 的 `identity_keys` 是 `["expert_id", "tenant_id"]`（`expert_id` 即 JWT 的 `_id`）。取出来按 key 排序后算 `sha256`，命中 `external_identity` 才算已绑定。**管理员绑定时填的 JSON key 集合必须与 `identity_keys` 完全一致**。

**幂等靠 `Idempotency-Key`**：BFF 重试时原样回传同一个 key，保证不重复执行/不建第二个会话。`conversation_id` 由我方生成，BFF 存下来复用即可，**不需要维护映射表**。

**两套序号不要混**：

| 面 | 入参 | 出参 |
|---|---|---|
| 事件流（SSE） | `after_seq`（query）+ `Last-Event-ID`（header），**取大值** | 帧 `id: <seq>` |
| 历史分页 | `page` + `page_size` + `through_seq` | `items[]` + `through_seq` |
| 会话已读 | `{through_seq}`（**通知水位**） | — |

消息序号与通知序号是**两套不同的序列**，只作为同一次详情视图的配对水位使用，**不能互相比较**。

**`CURSOR_EXPIRED` 是正常情况**：用户关页面一整天再回来，游标过期是预期行为，BFF 应当作"重新拉历史"的信号，不要弹错误框。

**错误码**（以新文档为准）：

| 码 | HTTP | 场景 |
|---|---|---|
| `EXTERNAL_ASSERTION_INVALID` | 401 | 缺失/签名错/格式错/`aud`/`iss`/身份 key 缺失 |
| `EXTERNAL_ASSERTION_EXPIRED` | 401 | 超出最大会话年龄 |
| `EXTERNAL_ASSERTION_UNBOUND` | 403 | 工号未绑定到我方用户 |
| `EXTERNAL_ASSERTION_UNAVAILABLE` | 503 | Secret 未配置/损坏（不影响其它通道） |
| `CURSOR_EXPIRED` | 409 | 事件流游标超出保留窗口 |

---

## 已实测确认的事实

2026-11-13 用 MSSW 开发环境真实 token 验证通过（详见 [01](./01-jwt-spec.md) §5）：

| 项 | 结论 |
|---|---|
| 算法 | RS256，**必须加白名单**（签发方自己没做） |
| `exp` | **恒为 -1，服务端不校验**，无过期语义；新方案另加最大会话年龄兜底 |
| `aud`/`iss` | AES-256-ECB 密文；**加密期望值比对**；`iss` 是**单元素数组** |
| AES key 派生 | **`salt` 右侧补 `\x00` 到 32 字节**（`salt` 长度须 ≤32），PKCS#7 补位 |
| 身份字段 | `_id`（专家 ID，24 位 ObjectId）+ `tenant_id`；`job_number` 是工号，明文 |
| 加密字段 | 仅 `email`/`email_login`/`phone_login`，不填不落库 |
| 公钥 | PKCS#1（`BEGIN RSA PUBLIC KEY`），PyJWT 可直接加载 |
| token 位置 | Cookie `soc-token`，是**唯一凭据** |

实测解出 `job_number=14806`、`_id=6a4ca2554a2a45d4b1ad5996`，`alg=none` 攻击被拒。

> **注意**：`easm-exposure-service-mssw` 的 `py_jwt_cipher.py` 是 **CBC 模式、key 由三段拼接**，与 MSSW token 的 ECB 无关，不要照抄。

## 工作量的真实分布

| 类别 | 内容 |
|---|---|
| **全新** | JWT 验签器 + AES 因子编解码（Console 侧）、外部身份模型（3 张表）、Redis 认证缓存、`/identity` 接口 |
| **直接复用** | 取消、附件下载、历史分页、会话详情/删除 |
| **同构路由** | 建会话、发消息、上传附件（路径形状同 WebChat） |
| **换身份依赖** | SSE 事件流（每事件复核到期与授权） |
| **Runtime 改动** | `conversation.channel` 来源列、跨入口会话聚合、通知已读水位 |
| **Console 前端** | 入口表单支持 MSSWWEB、用户详情页外部身份编辑 |

**「service 层一行不改」不成立**——`WEBCHAT` 有 **6 处写死必须参数化**：

| # | 位置 | 不改的后果 |
|---|---|---|
| ① | `list_entries`（`webchat_service.py:214`） | MSSWWEB 入口被过滤，入口列表恒空 |
| ② | `resolve_authorized_entry`（`:489`） | MSSWWEB 入口一律 404 |
| ③ | `submit_message` 的 `InboundEvent(channel=...)`（`:237`） | 事件标错通道 |
| ④ | 同方法 `adapter=WEBCHAT_CHANNEL_IDENTITY`（`:257`） | 受理走 WebChat 适配器身份 |
| ⑤ | `create_conversation` 的 `ChannelContext(type=...)`（`:286`） | 会话记成 WebChat |
| **⑥** | **`_run_request`（`webchat_core.py:69`）** | **消息真正写进 Runtime 的通道**；`submit_message` 只构造 `InboundEvent` 交给 `admit_sync`，组装 `RunRequest.channel` 在 `_run_request`。**漏改 ⑥，③④ 无效** |

> **①→② 有顺序依赖**：① 产出的入口快照是 ② 的输入，只改 ② 仍会 404，**必须一起改**。
>
> **测试兜底**：集成测试必须断言 `RunRequest.channel == "MSSWWEB"`，而不是只断言 HTTP 202——202 只表示受理成功，通道错了也一样返回 202。

## 通道集合与架构机检

| 位置 | 改动 |
|---|---|
| `packages/contracts/.../enums.py:7` `BUILTIN_CHANNEL_NAMES` | 加 `"MSSWWEB"` |
| 同文件 `LOCAL_STREAM_CHANNELS` | 加 `"MSSWWEB"`（站内流，产物直接落 canonical 流） |
| 同文件 `OUTBOUND_CHANNELS` | **不加**——MSSW 是 BFF 拉取模式，无主动投递能力；进错会让附件交付抛 `ATTACHMENT_WRITE_UNAVAILABLE` |
| `tests/test_attachment_contract.py:118`、`:228-235` | 断言两组**完全划分** `BUILTIN_CHANNEL_NAMES`，需同步 |
| `tests/agent_worker/test_capacity_webchat_worker_notifications.py:824` | 同上 |
| `tests/architecture/test_channel_neutrality.py:25` | **`CHANNEL_TOKENS` 必须加 `"msswwweb"` 等词**，否则核心域出现渠道字样也不会被发现（假绿灯），规则形同虚设 |

## 必须记住的五条约束

1. **验签只在 Console**——网关不持因子、不持库；因子放 K8s Secret
2. **归属按 user（+ Agent）校验**——不能只凭 `run_id`/`conversation_id`；Runtime 的 `_load_run_in` 不带 user_id，必须调用方补
3. **SSE 每事件复核**——开流时校验一次，之后每个事件 yield 前再查到期与授权，不能因事件持续 ready 就跳过
4. **游标只透传不改写**——`after_seq`/`Last-Event-ID` 取大值，不得重置为 0；消息序号与通知序号不能互相比较
5. **失败关闭**——Secret 缺失/损坏只让 MSSWWEB 返回 503，不影响启动与其它通道；`IDENTITY_KEYS_MISMATCH` 等配置错误在启动期暴露

## 当前状态

**需求基线**：`.code-flow/tasks/2026-11-13/mssw-external-im-channel/mssw-external-im-channel.prd.md`（US-01 ~ US-12、FEAT-01 ~ FEAT-12）

**唯一待答项**：

| # | 问题 | 影响 | 阻塞 |
|---|---|---|---|
| **N4** | MSSWWEB 入口的 `bot_id` 是否必要 | 必填且全局唯一，但不参与身份、BFF 不可见，可用「`id` + 显示名」替代 | **不阻塞**（无意见则保持现状） |

**已定案**：

| # | 事项 | 结论 |
|---|---|---|
| N1 | 用户规模与录入方式 | 第一期管理员在 Console 用户详情页逐条手工录入 `expert_id`（`ExternalIdentitySection` + `MsswwebIdentityModal`，字段按 `identity_keys` 动态渲染）；不做批量导入、不依赖名单文件 |
| N2 | 名单字段 | 不依赖名单；必填项即身份键 `expert_id` + `tenant_id` |
| N3 | `tenant_id` 稳定性 | **相对固定**，`identity_hash` 含 `tenant_id` 可接受 |

> **已确认**：`_id`（= `expert_id`）在员工离职/重新入职时不变，可长期作身份键。

**已内部确认**：

| # | 结论 |
|---|---|
| I1 | `identity_keys` 中的 `expert_id` **就是 JWT 的 `_id`**（24 位 ObjectId）。不是别名，无需映射层 |
| I3 | 会话**保留来源**（`entry_id`/`channel`），Run/通知按实际发送来源记录。接口必须返回来源字段；**派生页面不强制展示来源**（按需求方 FEAT-01 验收口径） |
| I4 | 路由保持**固定前缀** `/api/v1/msswwweb`，不改为平台变量 |

**外部依赖**：生产环境 `issue`/`audience`/`salt`/公钥——需求方明确"后续会给"。只阻塞生产联调，开发环境值已实测确定。

## 结构化风险

| 风险 | 后果 | 处置 |
|---|---|---|
| `run_id` 单独可达 | 越权读 | 网关按 `platform_user_id` + Agent 复核（含 SSE 建连与每事件） |
| 历史 token 重放 | `exp` 无意义、吊销不可见 | **责任前移给 BFF**（转发前校验）；我方加最大会话年龄兜底 |
| Redis 缓存旧权限/回填竞态 | 越权 | 同步失效 + 独立版本校验 + fence CAS；未完成则保持关闭 |
| 通道名遗漏参数化 | MSSW 请求以 WEBCHAT 落库 | 集成测试断言 `RunRequest.channel` |
| `CHANNEL_TOKENS` 未加词 | 架构机检假绿灯 | 通道注册表中的每个通道名都必须出现在词表中 |
| AES key 派生算法写错 | 全量 401 | 以 `01` §3 的算法为准，用测试向量驱动 |

## 关键源码位置

| 位置 | 用途 |
|---|---|
| `apps/im-gateway/.../api/webchat.py` | 现成接口原型（16 个接口中，12 个可同构复用、3 个不需要、1 个需改语义） |
| `apps/im-gateway/.../application/webchat_service.py:224-271` | 202 受理语义 |
| `apps/im-gateway/.../application/webchat_core.py:69` | **`_run_request` 组装 `RunRequest.channel`（第 ⑥ 处）** |
| `apps/im-gateway/.../channels/registry.py` | 通道装配入口 |
| `apps/im-gateway/.../application/chat_principal.py:184-207` | CHAT StreamGuard（**不可复用**，MSSWWEB 需每事件复核） |
| `apps/console-platform/backend/.../application/channel_service.py` | 入口与映射判定 |
| `apps/console-platform/backend/.../infrastructure/models/channel.py` | `BotAccount` / `ChannelIdentity` 表 |
| `apps/agent-runtime/.../application/run_service.py:1872-1882` | `_load_run_in`（**不带 user_id**，必须调用方补） |
| `apps/agent-runtime/.../application/event_tail_hub.py:652-675` | 游标 AHEAD/EXPIRED 判定 |
| `packages/contracts/.../enums.py:7` | 通道标识声明处 |
