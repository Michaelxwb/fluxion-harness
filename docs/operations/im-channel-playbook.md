# 新增 IM 通道接入 Playbook

> **适用对象**：我方研发/运维
> **目的**：新增一个 IM 通道或外部业务平台时，按同一套抽象接入，不复制 WebChat 的 handler，不把渠道差异带进核心域。
> **参考实现**：WebChat（Cookie 会话）、MSSWWEB（外部断言）
> **更新日期**：2026-11-13
> **状态**：设计评审中
> **文档修订**：v1.2；本轮为设计修订，相关实现、迁移与验收尚未执行。

---

## 1. 先分清四个概念

| 概念 | 含义 | 落点 |
|---|---|---|
| **Channel** | 传输/协议形态（WebChat、MSSWWEB、WECOM…） | `control.bot_account.channel`、`ChannelContext.type` |
| **Platform** | 身份断言签发方（mssw、mssp…） | 部署 Secret 的 `platform_key`；与 channel 1:1 |
| **Entry** | 一个 Agent 的 IM 入口 | `control.bot_account` 一行；`entry_id` = 该行 id |
| **Identity** | 外部用户 → 我方平台用户 | 外部断言：`external_identity` + `entry_access_grant`；旧 IM：`channel_identity` |

**关键判断**：

- 传输/协议相同、只是 JWT 因子不同 → **新增平台**：加 channel + Secret 配置 + 入口 + 绑定，不改 handler。
- 传输/协议不同（长连接、Webhook、Cookie 会话） → **新增通道**：加 adapter + surface + 能力组，执行面仍然复用。

---

## 2. 决策树

```text
新接入方怎么连进来？
├─ 浏览器 Cookie 会话（我们自己的登录）
│    └─ 参考 WebChat：CHAT Token → Cookie → X-Chat-Context
├─ 外部平台 JWT（BFF 转发）
│    └─ 参考 MSSWWEB：soc-token → Console 验签 → 绑定 → ChatIdentity
├─ 平台 Webhook（HMAC）
│    └─ 参考 WebhookChannelAdapter：时间戳 + HMAC + 重放窗口
└─ 长连接 Bot（企微等）
     └─ 参考 WeCom Connector：bot secret + lease + 出站命令

出站怎么交付？
├─ 站内持久流（Web/BFF） → LOCAL_STREAM_CHANNELS
└─ 外部主动推送（IM 机器人） → OUTBOUND_CHANNELS

需要附件吗？
├─ 用户上传 → 复用 WebchatUploadService + ChannelUpload
├─ Agent 产物 → LOCAL_STREAM 发布 / OUTBOUND 投递
└─ 平台入站媒体 → 适配器 AttachmentSource（取件/解密不出适配器）
```

---

## 3. 必改清单

### 3.1 Contracts / enums

| 文件 | 变更 |
|---|---|
| `packages/contracts/src/muad_contracts/enums.py` | `BUILTIN_CHANNEL_NAMES`、`LOCAL_STREAM_CHANNELS` 或 `OUTBOUND_CHANNELS` |
| `packages/contracts/src/muad_contracts/webchat.py` | 新认证方式（如 `EXTERNAL_ASSERTION`）、新公开契约（仅增量） |
| `packages/api-kit/src/muad_api/error_codes.py` | 新稳定错误码 |
| `config/api-messages.yaml` | 错误码/文案（双向断言） |

> 通道名、能力组、`SECRETLESS_CHANNELS` 必须同步；只加 adapter 不加能力组，产物交付会显式失败。

### 3.2 Adapter / registry

| 文件 | 变更 |
|---|---|
| `apps/im-gateway/src/muad_im_gateway/channels/base.py` | 复用 `ChannelAdapter` + 可选能力 Protocol |
| `apps/im-gateway/src/muad_im_gateway/channels/webchat.py` | 参数化 `WebchatChannelAdapter(name=...)` |
| `apps/im-gateway/src/muad_im_gateway/channels/registry.py` | 注册新 adapter；`snapshot_names()` 自动纳入快照 |
| `apps/im-connector/...` | 只有长连接/媒体取件渠道才需要 |

### 3.3 Surface / router

| 文件 | 变更 |
|---|---|
| `apps/im-gateway/src/muad_im_gateway/api/webchat.py` | 去掉固定 prefix，保留共享 handler |
| `apps/im-gateway/src/muad_im_gateway/api/deps.py` | `ChatSurface`、按 surface 分支的 `get_chat_identity` / `get_chat_stream_guard` |
| `apps/im-gateway/src/muad_im_gateway/application/chat_principal.py` | `ChatIdentity.allowed_entries`；长流复核抽象 |
| `apps/im-gateway/src/muad_im_gateway/main.py` | 同一 router 多挂载；通知路由只挂 WebChat |

**不要**复制 15 个 handler。新 surface = channel + auth kind + entry resolver；handler/service 共用。

### 3.4 认证

| 认证形态 | 实现 |
|---|---|
| Cookie 会话 | Console `ChatSessionService` + Gateway `ChatPrincipalGate` |
| 外部断言 | Console `external_assertion/`（配置、AES、验签、principal）+ Gateway `ExternalPrincipalGate` |
| HMAC Webhook | `WebhookChannelAdapter.verify` |
| Bot secret | Connector 持有，Gateway 不接触 |

外部断言要点：

- 因子只放 Console 的部署 Secret；Gateway 只转发 token。
- Secret 用一个 map：key = `platform_key`，值包含 `channel/public_key/issuer/audience/salt/identity_keys` 及可选 login_source；不包数组，不配算法/allowlist/enabled。
- 算法固定 `RS256`；拒绝 `none`/HS*。
- `aud`/`iss` 用 AES 因子比对；`exp=-1` 用全局最大会话年龄兜底。
- 身份字段集合来自平台 identity_keys；绑定 JSON 只提交对应字段的值，多字段 AND；**不要把 claim 名硬编码在代码里**。
- 一个平台一套字段集合：平台 Secret/ConfigMap 的 `identity_keys`；绑定/编辑必须一致，不按入口拆分。
- 绑定 JSON 算规范 JSON + `sha256` 存 `identity_hash`；认证按 `(tenant,platform_key,identity_hash)` 索引查，不扫用户表。
- `login_source` 可选：配置了才校验；不配置不校验。
- 只取绑定 JSON 的 key；不解密 email/phone。
- 缺配置 → 503，失败关闭；不影响启动与其他通道。

### 3.5 身份绑定

| 场景 | 方式 |
|---|---|
| IM 内绑定 | 绑定码 + `POST /internal/channel/bind`（现有，旧通道） |
| 业务平台 BFF | 用户级 `external_identity` + `entry_access_grant` |
| 平台元数据 | 平台 Secret/ConfigMap：platform_key、channel、`identity_keys`（不建表） |
| 入口授权 | `entry_access_grant`：identity → entry；解绑只删 grant |

- **一个外部身份 → 一个 platform_user**；不按入口复制身份。
- 身份 JSON 的 key 是 JWT 顶层 claim，value 是期望值；多字段 AND；平台级 `identity_keys` 固定。
- 认证：平台 `identity_keys` → JWT 子集 → canonical JSON → `sha256` → `(tenant, platform_key, identity_hash)`；一次索引查询。
- `bot_id` 只是入口标识；不参与身份唯一性；BFF 不感知。
- 用户详情页编辑用户级身份 JSON；影响其名下所有 grants；历史不丢。
- 解绑只删 grant；身份保留，清理/取消仍有主体；重绑同一入口历史恢复。
- 绑定/编辑/解绑/授权写 `config_audit_log`。

#### 3.5.1 认证缓存

- `/identity` 永远验签并查 PG/Console；未绑定结果第一版不缓存。凭证与当前权限分层，WebChat 的 SessionRecord 不再存 Agent 授权。
- 资源 Redis-first：原子核对同步屏障、独立用户版本、平台激活 epoch、绝对到期与所需缓存；版本不能只从同一条缓存里自比。命中不查 PG；miss 合并回源且受并发/速率预算限制。
- 绑定/编辑/解绑、用户状态、Agent 授权及 Agent/入口启停删除统一经过同步失效；成功前删对应权限缓存，必要时删凭证缓存，并让所有旧的并发回源失效。TTL 只回收数据，不作为撤权兜底。
- 第一版：PG 持久协调状态 PREPARING → Redis 关屏障并定向删除 → PG 事务提交变更/审计/用户版本与 COMMITTED → Redis 发布新版本并以操作标识 CAS 开屏障 → READY。PG session advisory lock 串行化同租户权限写；Redis 等待不放在打开的 PG 事务内；失败恢复按 PG 阶段处理，不靠超时自动开屏障。
- cache key 由 Contracts 工厂产生，同一 Lua 的 key 用租户 hash tag 同槽；user/source token/platform 反向索引与缓存写入原子维护，不在撤权路径全库 SCAN/KEYS。批量 Agent/入口变更用集合 SQL 和有界批量缓存命令。
- 认证只读主节点，版本/屏障不在 Gateway 跨请求缓存；Redis 连接重建、切主或数据恢复先经 Console/PG 核对并换 fence。缺少恢复握手时走当前 PG 核验，不直接相信恢复的缓存授权。
- WebChat 源 Token 撤销/退出清理相关 session；Agent 撤权只删权限，不强制有效登录退出其他 Agent。凭证缓存 miss 可经 Console 核验 PG session/source token 后重建，不能恢复已撤销凭证。
- Redis 不可用时有界回 PG；PG 协调状态不确定或非 READY 则不放行。PG 已提交但同步失败明确返回待恢复状态，管理页面查同步状态及实际资源；不假报成功，不继续用旧权限。
- SSE 每次 yield 前检查到期/复核截止；失效唤醒提前触发重查，丢信号仍有截止保证。关流不自动取消已受理 Run；显式 resume 另做当前准入核验，不重写冻结快照。

#### 3.5.2 配置与重启

- Redis TTL 到期与进程重启都不是主动撤权机制。Console 重建会加载 Secret 环境变量；Redis 数据仍可能保留，Gateway 缓存命中也不会自动验新公钥。
- 外部因子、身份字段集合、登录来源校验、有效期策略变化或平台停用，均需平台级失效：关闭认证并删平台凭证缓存 → 更新配置/重建全部 Console 副本 → 核对加载版本一致 → 激活新的非密 policy_epoch → 开放。
- 新旧配置副本不混发可缓存认证结果。配置缺失/损坏在接流量前登记平台不可用；Gateway cache hit 必须检查状态/epoch。普通无配置变化重启可用同版本缓存，不无条件清空 Redis。
- Redis 重启/快照恢复先关闭认证入口，按 PG 同步状态恢复并换 fence 后接流量；不能把恢复的 READY 标记直接当最新状态。完整恢复语义见本需求后端设计 §3.4.3。

### 3.6 能力组与入口

| 项 | 规则 |
|---|---|
| `SECRETLESS_CHANNELS` | Web/BFF 类入口无 bot secret，加入该集合 |
| `LOCAL_STREAM_CHANNELS` | 站内持久流交付（Web/BFF） |
| `OUTBOUND_CHANNELS` | 有真实主动推送适配器的机器人通道 |
| `bot_id` | 入口标识，管理员填写；BFF 不感知；全局唯一冲突 `BOT_ID_EXISTS` |
| 入口 | 一个 Agent 可 0..N 个入口；每个入口一个 `bot_account` 行 |

### 3.7 数据边界

- 同一 `platform_user + Agent` 的会话/产物/通知跨入口共享；entry/channel 只做来源标识。
- 不同用户隔离：按 `tenant + user (+ agent)` 核属；不同用户 404/403。
- 显式资源访问不添加来源 `entry/channel` 相等断言；新建/发送/上传仍检查本次实际入口，隐式机器人 entry/thread 路由保留；删除/取消/已读需有效用户，不要求来源入口 enabled 或当前 Agent 授权。
- 会话列表按 user+agent 跨入口聚合；item 带 `entry_id`/`channel` 来源。
- 通知用户级共享；WebChat 可展示同用户全部通知；BFF 类可选择不展示通知中心。
- 产物跨入口复用允许（同 user+agent）。
- 会话创建来源不可变；本次 Run、其 Task、通知和交付跟随实际发送来源。修复通知 writer 的“Run 与会话入口不同时回退到会话入口”逻辑；页面来源标记可选。
- Runtime 的显式会话受理前检查、事务内复查和最终解析都要按 user+Agent 适配；WebChat 的列表 service、初始化/URL 恢复及会话选择同步移除来源相等判断。

### 3.8 未读

- 复用 `channel_notification.read_at`，不要为单个通道另造消息级未读。
- 会话列表加 `has_unread`（按会话 EXISTS + 未读索引）。
- `POST /conversations/{id}/read` 带 `through_seq`，只标已看到的水位；新通知保留红点。
- 水位来源明确：详情在同一快照返回 canonical `last_seq=C`、当前 Run 状态与 `notification_through_seq=N`；历史从第一页用 C，展示完成后 read 提交 N。通知、canonical 和 SSE 序号独立；不从 has_unread 或 SSE id 猜 N。
- 超前通知水位明确拒绝，重复 read 幂等；SSE 终态/待确认后重新获取并展示对应快照再标读。
- 列表用匹配 `(tenant,user,agent,last_message_at DESC NULLS LAST,id DESC)` 的未删除部分索引；会话未读用 `(tenant,user,conversation,seq)` 的未读部分索引，先分页再 EXISTS，避免逐会话请求。
- 产物发布本身不写通知；Run 终态通知点亮红点。

### 3.9 测试

| 层级 | 用例 |
|---|---|
| contract | 新公开契约/错误码/认证方式 |
| unit | 验签向量（有效/坏签名/坏 aud/iss/exp 哨兵/超龄/缺 `_id`） |
| integration | 绑定、`/identity`、会话/消息/SSE/上传/产物、降级 503 |
| integration | user+Agent 跨入口共享 / 不同用户隔离矩阵（详情/消息/删除/取消/恢复/SSE/产物） |
| component | Console 入口表单/直绑表单 |
| e2e | 一条完整对话（真实 BFF/真实存储；CI） |

### 3.10 文档

- 外部：`docs/operations/bff-agent-integration.md`（相同协议共用指南，新增平台只补入口配置说明）。
- 内部：本文。
- Spec Delta：`harness-im` 新规则、`harness-auth` 认证面澄清、`harness-secret` 部署 Secret 例外。

---

## 4. 参考实现：MSSWWEB

| 关注点 | 落点 |
|---|---|
| Channel | `MSSWWEB`；`BUILTIN_CHANNEL_NAMES` + `LOCAL_STREAM_CHANNELS` |
| Adapter | `WebchatChannelAdapter(name="MSSWWEB")`；`channels/registry.py` |
| Surface | `/api/v1/msswwweb`；`ChatSurface(auth_kind="EXTERNAL_ASSERTION")` |
| 认证 | Console `application/external_assertion/`；Secret `EXTERNAL_ASSERTION_PLATFORMS_JSON`（`optional: true`） |
| 认证缓存 | 凭证/权限分层；独立用户版本、同步屏障、policy_epoch；失效成功后才返回管理成功 |
| 绑定 | 用户级 `external_identity` + `entry_access_grant`；管理员 API/UI |
| 权限接口 | `GET /api/v1/msswwweb/identity`；未绑定 `platform_user_id=null` |
| 数据共享 | user+agent 跨入口；entry/channel 只做来源 |
| 未读 | 通知用户级 + 会话 `has_unread` + 水位 mark-read |
| 确认 | `confirmation.required` + `POST /runs/{id}/resume`；Agent 授权边界 |
| 入口键 | 管理员填写 `bot_id`；BFF 只见 `entry_id` |
| 入口代理 | `muad-nginx` → Gateway；`build/nginx-build/nginx.conf` |

---

## 5. 反模式（不要做）

- ❌ 复制 `api/webchat.py` 的 15 个 handler 到新文件。
- ❌ 在 `agent-runtime` / `agent-worker` 写 `if channel == "..."`。
- ❌ 把 JWT 因子放 `bot_account.config_json`（会被 Console 响应回显）或 `platform_setting`（租户级、历史全量）。
- ❌ 信任未验签的 `login-source`/`user_id` 头做授权或选钥。
- ❌ 在显式资源访问上强求来源入口相同；同用户同 Agent 应共享。本次发送入口授权及隐式 IM 路由仍须检查。
- ❌ 为单个通道另造 `last_message_seq` 消息级未读。
- ❌ 把 `bot_id` 暴露给业务平台。
- ❌ 在同一个 URL 上混用 Cookie 与 `soc-token` 两种认证面。
- ❌ 把 resume 当清理例外；Agent 撤权后仍继续执行。
- ❌ 把 token 原文写进 Redis；只存校验后的 principal。
- ❌ 只 DEL 后允许旧请求无条件回填；或失效失败后继续使用缓存直到 TTL。
- ❌ 把重启 Console 等同于清 Redis，或轮换因子后只验证 /identity 而漏测资源缓存命中。
- ❌ 通知水位使用 SSE id；收到半组快照就推进重连游标；把普通 EOF 当终态。

---

## 6. DoD 清单

- [ ] channel 名进入 `BUILTIN_CHANNEL_NAMES` + 正确能力组。
- [ ] adapter 注册且快照可拉取；重名启动失败。
- [ ] surface 挂载；handler/service 未复制。
- [ ] 认证方式明确；因子不在 Gateway/DB/页面回显。
- [ ] 缺配置只降级该通道，不影响启动/其他通道。
- [ ] 用户级 `external_identity` + `entry_access_grant`；一个身份 → 一个 platform_user。
- [ ] 绑定/编辑/解绑/授权 API + 审计；`/identity` 返回 `allowed/reason`。
- [ ] 用户详情页支持编辑外部身份 JSON；平台 `identity_keys` 校验；历史/产物保留。
- [ ] 绑定 JSON 支持多字段 AND；平台级 `identity_keys`；不硬编码 claim。
- [ ] 身份/共享列表/会话未读/入口反查有匹配索引；隔离库 EXPLAIN 验证实际扫描与排序，小表 Seq Scan 不直接判失败。
- [ ] 解绑只删 grant；重绑同一入口后历史/产物恢复用例通过。
- [ ] 同用户同 Agent 跨入口共享；不同用户 404。
- [ ] `/identity` 查 PG；凭证/权限分层、同步失效与持久恢复；WebChat/MSSW 同时覆盖，旧回源不能回填。
- [ ] 因子/身份字段/有效期变更按平台 epoch 激活；暖缓存、重启、混合副本与 Secret 缺失用例覆盖。
- [ ] 现有 WebChat 列表、初始化/URL 恢复、会话选择与发送/上传均能跨来源使用。
- [ ] resume 要求 Agent 授权；撤权默认拒绝；确认动作可继续同一 Run。
- [ ] 未读复用通知口径；详情提供 C/N，展示对应状态/历史后提交 N；新通知不误清。
- [ ] BFF 与 WebChat 以完整应用游标重连，snapshot 半组、重复、EOF 及过期恢复用例覆盖。
- [ ] 停旧 writer 后回填尾差；未知来源如实保留，新会话来源完整。
- [ ] 上传/产物能力组正确；`LOCAL_STREAM` 或 `OUTBOUND`。
- [ ] 外部接入文档 + 本 Playbook + Spec Delta。
- [ ] 各 Task 声明的相关测试通过；完整回归通过隔离的 make acceptance 或 CI，浏览器验收按项目 runbook；不得共用 dev 库。

---

## 7. Spec Delta 建议

1. `harness-im#RULE-im-006`：外部断言通道的验签在 Console、Gateway 只转发；外部身份是用户级、入口授权独立；同用户同 Agent 跨入口共享；未读复用 `channel_notification`；`MSSWWEB` 属 `LOCAL_STREAM_CHANNELS`。
2. `harness-secret`：不可变的外部断言因子可以放部署 Secret（不落 DB、不回显），与“密钥明文存 Owner 表”并列。
3. `harness-auth`：外部断言验签是“验证别人的断言”，不产生我们的会话/JWT；与“禁止无状态 JWT”不冲突。
4. 替换 `im/harness-im` 现有“三份入口同一事实”约定：conversation 保留创建来源，Run/通知/交付按实际来源；并替换“WebChat 不回源”的绝对约定，凭证缓存 miss 可核验 PG 源会话恢复。
5. `auth/harness-auth` 增补凭证/权限分层、同步失效与失败关闭；`snapshot/harness-snapshot` 澄清当前 resume 准入不重写旧执行快照，也不承诺撤权回滚副作用。
6. 前端、API、持久化的来源/水位/流协议与索引约束按实现进入 Task Spec Delta；具体值留在配置/契约，新增 RULE 编号读取各 spec 的 next_rule。

---

*文档结束*
