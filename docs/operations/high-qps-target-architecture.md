# 面向 1000 并发对话的目标架构

日期：2026-10-24  
状态：背景/目标文档。执行以 **`docs/operations/high-qps-remediation-plan.md`** 为准。  
范围：多 Gateway、企微长连接、前端独立部署、PG 集群、历史数据保存。  
本文是架构决策文档，不是压测报告；参数与组件选型都要用真实负载复核。

---

## 0. 结论

1. **WebChat Gateway 可以做无状态多副本；企微长连接不能 active-active。**  
   正确做法不是“让长连接无状态”，而是把长连接隔离到一个 **Channel Connector**（每个 bot 同时只有一个 owner），Gateway 只做无状态请求/SSE。连接状态、`req_id`、回复流都留在 Connector，通过队列与 Gateway/Runtime 解耦。

2. **前端必须独立部署。**  
   `console-web`（nginx/CDN）与 `console-api`（Python）拆成两个工作负载；静态资源不经过 Python 进程。这样“单 Console”只约束 API，不约束页面/静态资源。

3. **真正的架构优化是控制面/数据面/连接面分离 + 事件驱动 + CQRS + 分区。**  
   调大连接池、加 worker、加缓存只是这些架构决定的后果，不是架构本身。

4. **PG 集群不是“写 QPS 自动变高”。**  
   HA 集群解决故障切换，读副本解决读扩展，PgBouncer 解决连接数；**写吞吐**要靠减少写放大、分区/分片，或把实时事件移出 PG。先做事件批量和实时通道，再决定 Citus/分库。

5. **Mattermost 历史数据不要硬塞进 Run 模型。**  
   当前仓库没有 Mattermost 运行时代码；旧历史应“只读归档 + 独立历史读模型/ETL”，不要伪造 Run。当前历史投影对 `run_id is null` 直接丢弃（`public_events.py:137-146`），这是必须尊重的代码事实。

---

## 1. “高 QPS”到底是什么

1000 个用户同时聊天，通常不是 1000 消息/秒：

| 口径 | 量级 |
|---|---:|
| 1000 用户 × 8 轮 / 600s | ~13 轮/s |
| 每轮 1 次受理 + 1 次 SSE 打开 | ~26 请求/s |
| 每轮 10s、20 个增量/s | **~20,000 事件/s**（在飞 1000 Run 时） |
| 每用户一个 5s 通知轮询 | 200 请求/s（这是控制面噪声） |

所以这个系统的主要矛盾不是“HTTP QPS”，而是：

- **连接密度**：1000 条 SSE + 多个 bot WebSocket；
- **事件扇出**：1 条模型流要经过 Runtime → 事件通道 → Gateway → 浏览器；
- **写放大**：每个增量一次事务会放大成 2 万 commits/s；
- **控制面同步依赖**：每个聊天请求都同步打 Console。

如果业务口径是“1000 用户每秒各发一条”，那是 1000 受理/s，需要另一套容量模型（队列、分片、限流），不能用本文的 13 轮/s 假设。

---

## 2. 多 Gateway 与企微长连接

### 2.1 当前代码为什么不能直接多副本

企微适配器是**进程内有状态**的：

- 每个 Gateway 进程有自己的 `WeComAdapter`，`apply_snapshot` 会为每个启用的 bot 建一条连接（`channels/wecom/adapter.py:787-806`）。
- 连接、`_message_refs`、`_reply_refs`、`_run_reply_refs`、`_streams`、`_pending_media` 都在进程内（`adapter.py:683-704`）。
- 入站消息只进入“收到它的那个进程”的队列；每个 Gateway 都启动自己的 `inbound.consume(adapter)`（`main.py:239-248`）。
- `open_reply` 从本进程的 `_message_refs` 取 `req_id`（`adapter.py:982-1004`）；换一个 Gateway 就找不到。
- Runtime 的投递泵通过 Gateway Service 调 `/internal/deliveries`，请求会被负载均衡到任意 Pod；非连接 owner 的 Pod 没有对应 `reply_ref`，只能走主动发送或失败。

所以直接加 Gateway 副本会出现：

- 同一个 bot 被多个进程重复连接（可能被服务端踢掉/重复投递）；
- 同一条入站消息可能被重复处理；
- 流式回复/回执落到没有 `req_id` 的 Pod，退回主动发送或失败；
- 进程重启后内存中的回复映射丢失。

**这不是“加锁/加 sticky session”能优雅解决的，因为连接与回复上下文本身就是有状态资源。**

### 2.2 原则：把状态赶到连接面

把系统拆成三个面：

| 面 | 职责 | 状态 |
|---|---|---|
| **连接面** | 企微/其他 IM 的 WebSocket、`req_id`、回复流、bot 重连 | 有状态，按 bot 单 owner |
| **控制面** | Console：认证、授权、配置、管理 | 低频、强一致、可 1–2 Pod |
| **数据面** | Gateway（HTTP/SSE）、Runtime（执行）、事件通道 | 无状态/可水平扩展 |

“Gateway 无状态”准确的说法是：**请求面 Gateway 无状态**；连接面由独立的 Connector 承担。

### 2.3 目标拓扑

```text
WeCom ──WS──> im-connector (StatefulSet / 按 bot 分片)
                 │
                 │ inbound stream（Redis Stream / Kafka，按 route 分区）
                 ▼
          im-gateway / im-ingress (N 副本，无状态)
                 │  POST /v1/runs
                 ▼
              agent-runtime (N 副本)
                 │  outbound commands（Redis Stream / 队列，按 bot 分区）
                 ▼
              im-connector ──WS──> WeCom

WebChat 浏览器 ──HTTP/SSE──> gateway-web (N 副本，无状态)
                              │
                              ├── Console API（低频认证/配置）
                              └── Runtime（Run/事件流）
```

两个 Gateway 角色可以共用代码，但部署上分开：

- `gateway-web`：只服务 WebChat HTTP/SSE，无 DB、无 IM socket。
- `im-connector`：只服务 IM 长连接和出站命令，不暴露公开 HTTP；可以有内部健康/指标端口。
- 如果希望统一，也可以让 `im-connector` 作为独立 Deployment，`gateway` 只保留 HTTP 面。

### 2.4 Connector 设计要点

**Bot ownership / lease**

- 每个 bot（`bot_id` + secret）同一时刻只有一个 active connector。
- Redis lease：`SET connector:bot:{bot_id} owner NX PX ttl`，定期续租；续租失败立即停止接单并断开。
- 用 fencing token/epoch：命令携带 owner epoch，旧 owner 即使短暂复活也不能发消息。
- bot 按 `hash(bot_id)` 分片到 connector Pod；一个 Pod 可以持有多个 bot 的连接。
- Pod 故障后 lease 过期，另一个 Pod 接管并重连；这是 active-passive per bot，不是 active-active。

**Inbound**

- Connector 收到消息后，先持久化到 `im_inbound` stream（包含 `message_id`、`bot_id`、`chatid`、`userid`、`reply_ref/req_id`、原始 envelope、received_at），再投递。
- 消费端按 `message_id` 去重；处理失败可重试；`message_id` 是幂等键。
- **按 route 分区**：`(channel, bot_id, external_user_id, external_conversation_id)` 作为 stream partition key，保证同一会话顺序；或者消费端用分布式 route lock。当前 `InboundPipeline` 只有进程内 `_route_lock`，多副本消费同一队列时不够（`inbound.py:515-577`）。

**Outbound**

- Runtime/Gateway 的发送请求不直接调用 socket，而是写入 `im_outbound` stream，按 bot 分区。
- Connector 消费命令：`send_text`/`reply_text`/`stream`/`finish`/`send_media`，用 `req_id`/`reply_ref` 执行；执行结果写回 command status/ack。
- 所有命令带 `command_id`/`delivery_key` 幂等键；重试不重复发。
- 流式回复：Gateway 不再持有 `ReplySession`；`open_reply` 返回一个代理，把 `stream/finish` 命令发到 connector；connector 持有 `stream_id`、`req_id`、buffer，按原节拍发 `reply_stream`。
- 连接中途断开：`req_id` 可能失效；终态文本降级为主动发送（现有 `ActiveTextDelivery` 语义），不要假装还能续流。

**Reply state**

- Connector 内存里保留热状态（性能），同时把关键映射写 Redis/DB：
  - `reply_ref`（req_id）TTL；
  - `run_id -> reply_ref` 映射（交付窗口，现有 `RUN_REPLY_TTL_SEC` 语义）；
  - `stream_id`/buffer 最新快照（可选，用于短重连）。
- Connector 重启后：能恢复的恢复；不能恢复的走主动发送 + 明确留痕。不要用“本路由最新回调”冒充老消息回调（当前代码已经明确拒绝这种降级，`_reply_target` 注释）。

**Latency**

- Inbound/outbound 各多一跳队列；本地 Redis Stream 通常亚毫秒到几毫秒，远小于企微 API/模型时延。
- 流式消息本身有 0.5s flush 节拍；队列跳数不是首字延迟的主因。

**Exactly-once 的边界**

- 队列是 at-least-once；用 `message_id`/`delivery_key` 做幂等副作用。
- 企微侧没有幂等键时，只能做到“至少一次 + 可追溯”；不要承诺严格 exactly-once。

### 2.5 WebChat Gateway 无状态化

WebChat 侧可以真正做到无状态：

- Gateway 不持 DB；状态只在 Runtime/Redis/PG。
- 同一用户的重连可以落到任意 Gateway Pod；SSE 从 Runtime 持久事件流/事件通道按 `after_seq` 续上。
- `BotSnapshotCache` 每 Pod 一份，30s 轮询；只影响入口可见性，不影响正确性。
- SSE 缓冲账本每 Pod 一份；用“每 Pod 预算 + 全局 Redis 计数”控制总订阅数。
- Chat 身份缓存每 Pod 或 Redis；TTL + 失效事件；任意 Pod 都能服务。
- 负载均衡不需要 sticky session；SSE 长连接天然粘在某个 Pod，但断线后可换 Pod。

### 2.6 部署形态

| 工作负载 | 形态 | 说明 |
|---|---|---|
| `gateway-web` | Deployment，N 副本 | 无状态；HPA/多副本；只服务 WebChat/HTTP/SSE |
| `im-connector` | StatefulSet 或带 lease 的 Deployment | 每 bot 单 owner；按 bot 分片；PDB；优雅断开 |
| `console-api` | Deployment，1–2 副本 | 控制面；不做聊天数据面 |
| `console-web` | Deployment/CDN | 纯静态；可 N 副本 |
| `agent-runtime` | Deployment，N 副本 | 执行面；按队列扩缩 |
| `agent-worker` | Deployment，N 副本 | 后台任务；独立池/限速 |

**Connector 的启用条件不是“多 Gateway”，而是“是否存在有状态通道”。**  
只要部署了企微（或其他 WS/poll 通道），即使 Gateway 只有 1 个副本，也应该把 `im-connector` 作为独立工作负载部署。这样以后从 1 个 Gateway 扩到 N 个，只是改副本数，不需要再做一次架构迁移。WebChat-only 部署可以不启用 connector。

**镜像边界：建议 connector 与 gateway 分开镜像。**  
同一个仓库、同一套共享包、同一个 release tag，但两个构建目标：

- `muad-gateway`：FastAPI/HTTP/SSE/webhook；不包含企微 SDK 和连接管理。
- `muad-connector`：企微/Slack Socket/Telegram poll 等有状态传输；不包含 WebChat/SSE/公开 HTTP 路由。

好处是依赖面、攻击面、资源上限、发布节奏都独立；坏处是多一个 Dockerfile/CI 目标。对开源项目来说，这个成本远小于“把第三方 SDK 和连接状态塞进公共网关”带来的耦合。若极端追求单镜像，可以保留同一镜像 + `ROLE` 入口作为兼容模式，但不应作为推荐形态。

### 2.7 统一 IM 接入：一个内部入口，多种 transport host

“所有 IM 从一个入口进来”在网络层做不到字面统一：企微是**服务端主动连出去**的 WebSocket，WebChat 是浏览器进来的 HTTP/SSE，Slack/Teams 可能是 webhook，Telegram 可能是 long poll。把它们硬塞进一个进程/一个端口，反而会把连接状态和 HTTP 请求面耦合在一起。

能做到、也应该做到的是：**所有通道在内部收敛到同一个 canonical 入口**。

```text
                 ┌─ WeCom WS ─────── im-connector（有状态，按 bot 分片）
                 ├─ Slack Socket ─── im-connector
外部协议 ────────┤
                 ├─ Webhook（Slack/Teams/Mattermost）─ channel-webhook（无状态 HTTP）
                 ├─ Long poll（Telegram 等）───────── im-connector / poller
                 └─ WebChat HTTP/SSE ──────────────── gateway-web（无状态）

                              │ 全部发布同一个 InboundEvent
                              ▼
                    im.inbound（按 route 分区）
                              │
                              ▼
                   统一的 im-ingress / InboundPipeline
                   （去重、身份、会话、命令、Run 受理）
                              │
                              ▼
                         agent-runtime
                              │
                              ▼
                    im.outbound（按 bot 分区）
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
           im-connector               gateway-web
           （WS/webhook 回发）         （WebChat SSE/HTTP 回发）
```

统一的是**契约与管线**，不是传输进程：

| 层 | 统一 | 每通道实现 |
|---|---|---|
| 入站事件 | `ChannelEnvelope`：channel/bot/user/conversation/message_id/text/attachments | 协议解析、签名、ACK、媒体下载 |
| 出站命令 | `DeliveryCommand`：route/reply_target/message/delivery_key | 发送 API、流式/编辑/线程/媒体上传 |
| 身份 | 外部用户/入口 → 平台用户/Agent | 各通道的认证与绑定方式 |
| 会话 | route key + 显式会话/线程 | `req_id`、`thread_ts`、`post_id`、`run_id` |
| 去重 | `message_id`/`delivery_key` | 通道侧幂等语义 |
| 重试/审计 | 统一策略与表 | 通道错误码映射 |
| 能力 | capability flags：stream/edit/thread/media/read_receipt/typing | 实际支持情况 |

**当前代码已经有很好的起点**：`channels/base.py` 定义了 `ChannelAdapter`、`SnapshotAware`、`AttachmentSource`、`OutboundArtifactDelivery`、`ReplySessionFactory`、`ActiveTextDelivery`、`ExplicitConversationSource` 等端口；`ChannelEnvelope`/`DeliveryRouteInput`/`DeliveryMessage` 已经是渠道中立契约。WebChat 适配器也已经把 HTTP 输入规范化为 `ChannelEnvelope`（`channels/webchat.py`）。

需要补的是“**adapter 协议** 与 **运行宿主** 分离”：

- `ChannelAdapter`：只做协议转换与通道能力，不关心自己跑在哪个进程。
- `ChannelRuntime`：负责生命周期与传输，分为：
  - `LongConnectionRuntime`（WebSocket/Socket Mode，lease/shard）；
  - `WebhookRuntime`（HTTP 回调，验签、快速 ACK、入队）；
  - `PollingRuntime`（long poll/定时拉取）；
  - `RequestRuntime`（WebChat 这类请求/响应 + SSE）。
- `im.inbound` / `im.outbound`：统一的队列契约；adapter 不直接调 Runtime，Runtime 也不直接调 socket。

这样新增一个 IM 时，唯一要写的是：

1. 一个 adapter 包：解析该协议入站、映射到 `ChannelEnvelope`；
2. 一个 sender：把 `DeliveryCommand` 映射到该协议发送；
3. 一个 capability manifest：是否支持流式/编辑/线程/媒体/已读；
4. 一个 auth/verification 实现；
5. 一组 contract tests（用录制帧/假服务端跑统一套件）。

平台侧的去重、身份绑定、会话、Run、审计、重试、限流、指标全部复用。**“唯一实现不同 IM 的接入协议”这个目标是可达的，前提是把能力差异显式建模，而不是假装所有通道都是文本+发送。**

**WebChat 应该放哪？**

- WebChat 是 first-party 通道，不需要连第三方 socket；它的“长连接”是浏览器 SSE，属于 `gateway-web` 的请求面。
- 推荐：`WebchatChannelAdapter` 继续跑在 `gateway-web`，把 HTTP 输入发布到 `im.inbound`；出站由 `gateway-web` 的 SSE sink 消费 `im.outbound` 里 `channel=WEBCHAT` 的命令。
- 不要为了“统一”把 WebChat 塞进 `im-connector`：那会把 1000 条浏览器 SSE 变成 connector 的负担，也会给 HTTP 请求多加一跳。
- 统一发生在 `im.inbound`/`im.outbound` 和 `InboundPipeline`，不是发生在进程里。

**Webhook 类通道**可以有一个统一的 `channel-webhook` HTTP 入口：`POST /channels/{channel}/events`，按 channel 分发到 adapter 做验签/解析，然后入队。这样对外只有一个 HTTP 端点，对内仍是每通道 adapter。

**长连接类通道**由 `im-connector` 主动连出去，不存在“公网入口”这回事；它们的统一入口就是 `im.inbound`。

### 2.8 验收

- 杀掉一个 connector：该 shard 的 bot 在 lease TTL 内被接管；无重复发送；用户看到明确的重连/最终消息。
- 同一 bot 永远不会有两个 active owner（Redis lease + fencing 断言）。
- 同一 route 的消息不并发处理（partition key 或分布式锁断言）。
- connector 重启时，已受理 Run 的终态仍能通过主动发送送达；流式中断有可见记录。
- 队列 lag、连接状态、重连次数、发送失败率有指标。

---

## 3. 前端独立部署

> 执行顺序调整：先做前端 code splitting；`console-web` 独立静态层作为**可选 overlay/CDN**，不是默认四单元的硬依赖。详见整改方案 WP-08。

### 3.1 拆分

当前 Console 镜像把 Vite 构建产物放进 Python 镜像，由 FastAPI `install_spa` 提供（`apps/console-platform/backend/src/muad_console_platform/spa.py`）。这会把 1000 个页面的首次静态资源加载压到单 Python 进程上。

目标：

- `console-web`：nginx（或 CDN/对象存储）只提供 `/assets`、`/chat`、`/` 的静态文件与 SPA 回退。
- `console-api`：只提供 `/api`、`/internal`，不再挂 `install_spa`（或只在本地 dev 保留）。
- Ingress 按路径分流（沿用 `deploy/k8s/base/ingress.yaml` 的思路）：
  - `/assets`、`/chat`、`/` → `console-web`
  - `/api/v1/webchat/auth`、`/api` → `console-api`
  - `/api/v1/webchat`（其余）→ `gateway-web`
  - `/api/v1/webchat/runs` SSE → `gateway-web`（buffering off）

### 3.2 关键约束

- **同源**：Cookie（`muad_chat_session`）、CSRF、`X-Chat-Context` 都按同源设计；不要让静态资源换域名导致 CORS/Cookie 问题。用同一个 Ingress 域名按路径分流即可。
- **缓存**：`/assets` 带 hash，`immutable` 长缓存；`index.html` no-cache。
- **构建**：CI 一次构建，产出 `console-web` 镜像/静态包；前端发布不重启 Python。
- **安全**：静态 Pod 不需要 DB/Redis/Secret；只读文件系统；独立 ServiceAccount。
- **可观测**：静态 404/5xx、缓存命中、带宽独立监控；不要混在 Console API 指标里。

### 3.3 收益

- 1000 个页面加载的静态字节不再占用 Console 的事件循环/线程池。
- 静态层可以 N 副本/CDN，不受“Console 1 Pod”约束。
- API 与前端独立发布、独立回滚。
- Console 镜像更小，启动更快，攻击面更小。

---

## 4. 真正的架构优化

### 4.1 问题 → 架构手段

| 问题 | 架构手段 | 不是手段 |
|---|---|---|
| Gateway/Console 单进程 CPU | 请求面无状态多副本；控制面/数据面分离 | 只调 CPU limit/worker 数 |
| 每个聊天请求同步打 Console | 短 TTL 签名身份/缓存 + 控制面低频化 | 只加缓存但不定义失效 |
| 每增量一次 PG 事务 | 实时事件通道 + 异步批量持久化 | 只加索引/连接池 |
| 1000 条 SSE 占 PG 连接 | 流式端点短 Session；事件从日志/通道读 | 只加 `max_connections` |
| 企微只能单连接 | 独立 Connector + lease + 队列 | sticky session/多副本硬连 |
| 单 PG 写瓶颈 | 分区/分片/分离事件存储 | 只上 HA 集群 |
| 前端静态压 Console | 独立静态层/CDN | 给 Console 加核 |
| 过载时雪崩 | 有界队列、准入、429、隔离池 | 无限加 Pod |

### 4.2 控制面/数据面分离

- **控制面**：认证、授权、配置、管理、审计查询。低频、强一致、可 1–2 Pod。
- **数据面**：消息提交、Run 执行、事件流、SSE。高频、可分片、无状态。
- Console 不应该在每个聊天请求的同步路径上；只在新 Run 受理/身份刷新时参与。
- 聊天身份用短 TTL 签名断言或缓存；撤销用 TTL + 失效事件；把“每个请求查 PG”变成“低频刷新”。

### 4.3 事件驱动 + CQRS

**写模型**：

- Run 状态、会话、提交、终态、通知是权威事实，写 PG。
- 正文增量不直接写 PG 热路径；写事件日志（Redis Stream/Kafka/NATS）。
- 异步持久化器按批把控制事件/周期快照/终态写 PG。

**读模型**：

- SSE 从事件日志读实时增量；
- 历史/会话列表/未读从读模型（PG 物化视图/Redis/搜索存储）读；
- 重连从最后快照 + 日志偏移恢复；
- 历史导入（Mattermost）走独立读模型，不伪造 Run。

**事件日志选择**：

| 方案 | 适用 |
|---|---|
| Redis Stream | 低延迟、部署简单；需要接受 Redis 持久性/容量边界 |
| Kafka | 高吞吐、可回放、多消费者；运维重 |
| NATS JetStream | 轻量、低延迟；生态/运维看团队 |

对 1000 并发、20k events/s，Redis Stream 或 Kafka 都能做；关键不是选型，而是**不要用 PG 当实时广播总线**。

### 4.4 分区/分片

- `canonical_event` 按 `hash(conversation_id)` 分区（单 PG）或按 tenant/conversation 分片（Citus/多库）。
- 每个分区独立索引/autovacuum；retention 按分区 drop/attach。
- 读模型按 tenant 分区；控制面 DB 与事件 DB 分开，避免事件风暴拖慢登录/受理。
- 单租户部署时，按 conversation 分片；多租户时先按 tenant，再按 conversation。
- 不要过早 Citus：先批量写 + 分区 + 分离集群；写吞吐仍不够再考虑分布式 PG。

### 4.5 背压与隔离

- Gateway：每租户/用户/路由的有界队列 + token bucket；过载返回 429 + Retry-After；不无限 `create_task`。
- Runtime：执行槽、模型池、队列年龄；队列满时拒绝新 Run，而不是无限排队。
- Worker：后台任务独立队列/并发/DB 池；不跟聊天抢连接/WAL。
- 外部渠道：每 bot 的发送速率/并发上限；企微 40008/限流要可退避。
- 数据库：每服务独立连接池/用户；PG `statement_timeout`/`lock_timeout`/`idle_in_transaction_session_timeout`。

### 4.6 幂等与去重

- 入站：`message_id` 唯一；重复投递不建第二个 Run。
- 出站：`delivery_key` 唯一；至少一次发送 + 已送达回执。
- 事件：`(conversation_id, seq)` 唯一；重放按 seq 去重。
- Connector：`command_id` 幂等；lease epoch 防止旧 owner 发送。
- 队列：at-least-once 是常态；不要用“队列不重复”做业务假设。

### 4.7 观测与 SLO

必须能回答：

- Gateway：每请求 auth/upstream/projection 耗时；SSE 活跃订阅/缓冲/关闭原因；每 Pod CPU/事件循环 lag。
- Connector：每 bot 连接状态、重连次数、inbound/outbound lag、发送失败率。
- Runtime：执行槽使用、队列深度/最老等待、模型在飞、事件写批次/提交耗时。
- PG：`xact_commit/s`、WAL bytes/s、锁等待、`idle in transaction`、按语句 p95。
- 端到端：受理 p95、首字 p95、终态完整率、重连恢复时间。

---

## 5. PG 集群

### 5.1 三种“集群”不是一回事

| 目标 | 方案 | 是否提升写 QPS |
|---|---|---|
| 高可用/故障切换 | Patroni / CloudNativePG / 托管 PG，primary + sync standby | 否 |
| 读扩展 | 只读副本 | 否（只提升读） |
| 连接数 | PgBouncer | 否（只复用连接） |
| 写扩展 | 分区/分片（Citus/多库） | 是（有限/复杂） |

**结论：如果单实例 PG 的瓶颈是每增量一次事务，换 HA 集群不会让写入变快。** 先把写放大降下来；集群用于 HA/读扩展/隔离。

### 5.2 建议演进

1. **单主 + PgBouncer + 只读副本**：控制面/历史/报表走副本；认证/撤销走主；PgBouncer 限制总连接。
2. **事件表分区**：按 conversation hash 或时间分区；独立 autovacuum/retention；控制面与事件面分 schema/池。
3. **控制面/事件面分集群**：Console/认证/配置一个 PG；Runtime 事件/历史一个 PG。这样事件写风暴不会拖慢登录/受理。
4. **Citus/分片**：只有在批量写 + 分区 + 分集群后，单主写仍超预算时再做。按 tenant/conversation 分片；接受跨分片查询/运维复杂度。
5. **事件分析外置**：Kafka/ClickHouse 做审计/分析；PG 保留权威状态。

### 5.3 “更真实地模拟”

- Staging 拓扑要与生产一致：PG 版本/扩展、磁盘 IOPS、PgBouncer、副本数、Redis 集群、Ingress、模型探针速率。
- 负载生成器不要和被测服务同节点；否则测到的是 CPU 争用。
- 跑真实流式（20/s × 10s），不是 6 段增量；带浏览器同形通知轮询。
- 观测 `xact_commit/s`、WAL/s、`idle in transaction`、锁等待、副本 lag；不是只看 `active` 会话。
- 做故障演练：PG failover、PgBouncer 重启、Redis 抖动、connector 重启；验证不丢消息/不重复。

### 5.4 迁移历史数据（PG）

- 同大版本：物理备份/恢复或流复制；切换时旧主只读保留。
- 跨版本：`pg_upgrade` 或逻辑复制；先 dry-run，校验行数/校验和/序列。
- 分片/Citus：按 tenant/conversation 导出导入；建立 source→target 映射；双跑校验；不要直接双写。
- 迁移期间冻结写入或进入维护窗口；保留旧库备份至少一个保留周期。

---

## 6. Mattermost 历史数据

### 6.1 先确认数据在哪里

当前仓库**没有 Mattermost 运行时代码**；历史提交里只有旧的 token 认证痕迹，没有可复用的历史迁移实现。所以要先确认：

- 历史在 Mattermost 自己的 PostgreSQL/文件存储里？
- 还是在旧平台的业务库里，只是接入了 Mattermost 通道？
- 是否还需要在 Mattermost 里继续使用/可读？

不同答案对应不同方案。

### 6.2 三种保存策略

**A. 只读归档（推荐先做）**

- Mattermost 保留只读实例/备份；新系统不导入历史，只在需要时通过 Mattermost API/导出文件查看。
- 优点：零迁移风险；缺点：用户体验割裂，跨系统搜索/合并历史麻烦。

**B. 一次性 ETL 到独立历史读模型（推荐长期）**

- 用 Mattermost export（JSONL）或只读副本导出 posts/channels/users/files。
- 映射：
  - team/channel → tenant/conversation/entry；
  - user → platform_user（外部身份映射表）；
  - post → message；thread root/replies → 会话线程；
  - edit/delete → 历史标记；
  - file → artifact（对象存储 + checksum + source_file_id）。
- 落 `legacy_message`/`conversation_message` 读模型，**不要伪造 Run**。
- 统一历史 API 合并 native + legacy：按时间/seq 排序，保留 `source`/`source_id`。
- 幂等：`(source, source_id)` 唯一；分批、断点续跑、校验和、抽样对账。
- 附件：复制到对象存储，保留原文件 ID/哈希；失败可重试。

**C. 双读/联合查询**

- 新系统读 native，旧 Mattermost 读 legacy；API 层合并。
- 适合过渡期，不适合长期（两套权限/搜索/分页语义）。

### 6.3 当前代码的硬约束

- `public_events.py:137-146`：`_project_message` 对 `run_id is None` 直接返回 `None`。
- 也就是说，把 Mattermost 消息写成 `canonical_event` 但没有 `run_id`，历史接口不会显示。
- 因此历史导入必须：
  - 新增独立读模型/表（推荐）；或
  - 明确扩展投影，允许“非 Run 来源”的消息，但不要伪造 Run ID/快照。
- 不要把旧消息硬塞进 `run_record`；那会污染运行审计、重跑、配额和终态语义。

### 6.4 迁移步骤

1. 冻结/只读旧 Mattermost；记录版本、行数、文件数、时间范围。
2. 导出/读取：users、teams、channels、posts、threads、files、reactions（可选）。
3. 建立 ID 映射表；先导入小租户/小频道做 dry-run。
4. 分批导入历史读模型 + 附件；每批记录游标/校验和。
5. 对账：行数、min/max timestamp、抽样正文哈希、附件数量/字节。
6. 切换读路径；保留旧库/导出包至少一个保留周期。
7. 如需回滚：读模型可重建；旧 Mattermost 保持只读。

### 6.5 如果只是 PG 集群迁移

- Mattermost 历史在它自己的库/文件里，不随我们的 PG 集群迁移而丢失。
- 如果旧平台也把 Mattermost 消息写进了我们的 PG，按 5.4 的备份/逻辑复制迁移；迁移后仍按 6.2 建读模型，不要直接依赖 Run 表。

---

## 7. 路线图与验收

### Stage A：连接面/前端拆分（架构前提）

- `im-connector` 独立部署 + bot lease + inbound/outbound stream。
- `gateway-web` 无状态多副本。
- `console-web` 作为可选静态 overlay/CDN；默认仍可由 `console` 镜像提供（先做前端 code splitting）。
- 验收：kill connector 不重复发送；WebChat 任意 Pod 可服务/重连；启用静态层时资源不经过 Python。

### Stage B：控制面低频化

- 短 TTL 签名身份/缓存；通知轮询 15–30s + page_size；设置/定义 revision 缓存。
- 验收：1000 用户下 Console 认证/设置请求降到 30–70 rps；CPU < 60%。

### Stage C：事件驱动/CQRS

- 实时增量走 Redis Stream/Kafka；PG 异步批量持久化；SSE 从事件日志读。
- 事件表分区/读模型；历史/通知走读模型。
- 验收：1000 用户真实流式；PG `xact_commit/s` 在预算内；无 `idle in transaction` 长连接。

### Stage D：PG 集群/分片

- HA + PgBouncer + 读副本；必要时控制/事件分集群；再评估 Citus。
- 验收：failover 不丢已受理 Run；副本 lag 在 SLO 内；写吞吐满足目标。

---

## 8. 待确认

1. 多 Gateway 的会话一致性/SSE 路由已按“请求面无状态、断线可换 Pod、游标续流”定稿；具体工作包见整改方案 WP-05/WP-06。
2. 企微智能机器人是否允许同一 bot 多个 WebSocket 同时在线？当前代码按“不假设”设计；如果官方支持多连接，也要确认消息投递/`req_id` 语义。
3. Mattermost 历史数据在 Mattermost 库还是旧平台库？是否还需要保留 Mattermost 只读实例？
4. 业务口径是“1000 并发会话”还是“1000 消息/秒”？两者容量设计不同。
5. 是否接受实时正文在崩溃时最多丢最后 1s（换取 Redis 实时通道）？如果不接受，事件持久化只能保留批量 PG 写，PG 写入预算要更高。
