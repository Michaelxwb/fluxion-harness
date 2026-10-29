# 高 QPS 整改方案（整合版）

日期：2026-10-24  
状态：执行草案（Draft for execution）  
输入：

- `docs/operations/high-qps-architecture-review.md`（性能瓶颈与缓存复核）
- `docs/operations/high-qps-target-architecture.md`（目标架构：多 Gateway / Connector / 前端 / PG 集群）
- 2026-10-24 四路代码审计：Gateway/通道、Runtime/事件写、Console/前端、测试/容量/CI

本文是**执行口径**：旧文档保留为证据与背景；出现冲突时，以本文的“现状事实 + 设计决策 + 验收矩阵”为准。

已细化的设计文档（2026-10-24；v0.2 已按 review 修正 E2E 分层、spec amendment、NFR registry 与追溯 ID）：

- `.code-flow/tasks/2026-10-24/high-qps-remediation/high-qps-remediation.backend.design.md`
- `.code-flow/tasks/2026-10-24/high-qps-remediation/high-qps-remediation.frontend.design.md`
- `.code-flow/tasks/2026-10-24/high-qps-remediation/high-qps-remediation.scenarios.md`

---

## 0. 执行摘要

### 0.1 一句话

当前瓶颈不是“PG 不够大”，而是四件事叠加：

1. **每个 SSE 回复流占一条 PG 连接**（请求级 Session 到流结束才释放）；
2. **每个 50ms 增量一次事务 + 两把行锁 + last_seq 更新**（1000 在飞 Run ≈ 2 万 commits/s）；
3. **Console/Gateway 单进程 + 每请求固定 CPU**（`SharedSettings()` 1.45–2.5ms/次；通知轮询 200 rps）；
4. **企微连接与回复上下文在当前 Gateway 进程内**（多副本会重复连接、丢 reply_ref、投递落错 Pod）。

### 0.2 整改顺序（不要反过来）

1. **先让瓶颈可见**：指标、真实流式负载、SLO 断言、容量报告（WP-00）。
2. **低风险高收益，不加服务**：settings 单例、auth 查询合并、通知 page_size/轮询、SSE 短 Session、连接池/预检、读放大（WP-01/02/07）。
3. **架构拆分与写入模型**：事件批量写、保留/索引、Connector 独立镜像、Gateway 无状态、HPA/模型容量（WP-03/04/05/06）。
4. **可选扩展**：前端静态 overlay、Redis 实时流、PgBouncer/读副本/分区/Citus（WP-08/09/10，按实测触发）。

### 0.3 最终形态（开源友好）

| 部署单元 | WebChat-only | 启用企微 | 说明 |
|---|---:|---:|---|
| `console` | 1 | 1 | API + 默认静态资源 |
| `gateway` | N | N | 统一消息网关 + WebChat HTTP/SSE + webhook |
| `connector` | — | 1+ | 有状态长连接；**独立镜像**；按 bot 分片/lease |
| `agent-runtime` | N | N | Run 执行面 |
| `agent-worker` | N | N | 后台任务 |
| `console-web`（可选） | 可选 | 可选 | 静态容器/CDN；默认不启用 |

中间件：**PostgreSQL + Redis**（对象存储/NFS 已有）。默认不引入 Kafka/NATS/Citus/Service Mesh；PgBouncer 是可选部署。

### 0.4 明确不做

- 不为每个 IM 建微服务；新增 IM = `channels/` 下 adapter 包 + contract tests。
- 不把所有协议塞进 `im-connector`；Connector 只做有状态传输。
- 不在没有实测证据前上 Kafka/Citus/分库分表。
- 不先加 Gateway 身份缓存；先修掉 `SharedSettings`、auth SQL、通知放大（实测更大头）。
- 不先拆 `console-web`；先做前端 code splitting，再按静态流量决定是否加静态层。
- 不在修好 in-process 状态前上多 worker（`ArtifactFetchTokens`、metrics registry 会先坏）。

---

## 1. 现状事实（代码审计）

### 1.1 当前部署形态

- `deploy/k8s/base` 固定四个 Deployment，Console/Gateway 各 1 副本、单进程 uvicorn；Dockerfile 没有 `--workers`。
- Console/Gateway CPU limit 2 核，但 Python 单进程最多吃 1 核；800 用户实测 Console 932m、Gateway 991m（2026-10-21 历史复核记录，已移除，见 git 历史）。
- 容量 profile 的 `process_counts=1`、`max_replica_counts.im-gateway=1`、`pg_connection_budget=200`；预检固定四单元、固定单 Pod（`scripts/check_agent_capacity_deployment.py:72-75,325-347`）。
- Runtime HPA 上界 4、执行槽 8/进程 = **32 并发 Run**；HPA 只看 CPU/内存（`deploy/k8s/base/capacity-autoscaling.yaml`）。

### 1.2 关键发现与证据

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| 1 | **SSE 每个流占一条 PG 连接** | `/v1/runs/{id}/events` 用 `RunServiceDep` → `SessionDep`（`api/runs.py:101-111`、`api/deps.py:112,125-151`）；FastAPI 在响应体发送完才退出 yield 依赖（`.venv/.../fastapi/routing.py:140-145`）；`get_session` 到那时才 commit/close（`infrastructure/db.py:32-41`） | 1000 条流 = 约 1000 条 `idle in transaction`；默认 pool 10 时每 Pod 只能挂 10 条流，4 Pod 也只有 40 |
| 2 | **每增量一次事务 + 两把行锁** | `_persist_event`：`database_now` + `lock_execution`（Run/Conversation/Continuation 三处 `FOR UPDATE`）+ `UPDATE conversation.last_seq` + INSERT + COMMIT + Redis publish（`run_service.py:2663-2684`、`tool_result_receipts.py:28-57`、`run_events.py:238-251`） | 1000 在飞 Run × 20 次/s = **2 万 commits/s**、约 14–16 万 statements/s；写延迟直接阻塞模型流（`executor.py:340-343` 等 `queue.join()`） |
| 3 | **事件存储/保留不可持续** | 20k rows/s ≈ **1.7B rows/day**；`cleanup_stream_replay` 每 30s 只删 200 行（`channel_retention.py:77,269-301`、`main.py:337-343`）；容量 profile 日均 5GiB（`profiles/...example.yml:69`） | 表和索引无界增长；retention 比写入慢 1000 倍；磁盘/WAL/autovacuum 很快失效 |
| 4 | **准入闸门包住了网络材料调用** | `start` 的 `process_admission_gate()` 覆盖整个 `_admit`（`run_service.py:889,897,1031`），包括 Console `admission-materials`（`run_service.py:1086`）；闸门 64，但 DB 池默认 10 | 闸门名义 64，实际 64 个请求抢 10 条连接；materials P50 4.5s 时单 Pod 约 14 次受理/s，1000 ramp ≈18s |
| 5 | **租户在飞计数无索引** | `count_open_runs` 过滤 `admission_released_at IS NULL`（`run_admission_store.py:100-114`）；`run_record` 索引列表无该谓词（`models/runtime.py:250-279`） | 每次受理对 `run_record` 做全表扫描；随着历史 Run 增长线性变慢 |
| 6 | **Console 每请求固定 CPU** | `require_internal_service` 每请求 `SharedSettings()`（`api-kit/security.py:26`）；`_batch_platform_limit` 每次设置读取构造（`platform_settings_service.py:109-111`）；实测 1.45ms（Pod）/2.54ms（本机） | 通知轮询每请求 2 次构造；1000 用户 5s 轮询 = 200 rps → 约 1 核仅花在 settings 对象上 |
| 7 | **通知轮询放大 Console** | 默认 5s（`contracts/platform_settings.py:252-254`）；前端不带 `page_size`（`useNotifications.ts:406-409`）；Runtime 每次回源 Console 设置（`webchat_history.py:64-71`） | 200 poll rps → 400 Console 内部调用/s、约 1400 SQL/s；现有 800/1000 用户验收没模拟这部分 |
| 8 | **企微多副本会坏** | 每个 Gateway `apply_snapshot` 为每个 bot 建连接（`wecom/adapter.py:787-806`）；`_message_refs/_reply_refs/_run_reply_refs/_streams` 进程内（`adapter.py:683-704`）；`open_reply` 从本进程取 `req_id`（`adapter.py:982-1004`）；Runtime 投递经 Service 落任意 Pod（`configmap.yaml:20`、`delivery.py`） | N 副本 = N 条 bot 连接；入站可能重复；回复/产物落到非 owner Pod 只能降级主动发送；重启丢 reply_ref/stream |
| 9 | **WebChat 没有走统一核心** | submit 直接调 Runtime（`webchat_service.py:216-259`），不建 `ChannelEnvelope`、不走 `InboundPipeline`/dedupe/audit；`WebchatChannelAdapter` 队列在 registry 未装配 sink（`channels/registry.py:57`、`channels/webchat.py:139-143`） | 统一消息网关的核心路径对 WebChat 是“旁路”；dead queue 会误导后续扩展 |
| 10 | **Runtime 容量/HPA 不足** | 执行槽 8 × HPA 4 = 32（`agent-runtime.yaml:61-62`、`capacity-autoscaling.yaml:30-33`）；HPA 无队列指标；模型 `max_inflight=16`（`test_capacity.py:111`） | 1000 并发 Run 在 32 槽前排 30 多轮；只按 CPU 扩可能不触发；模型额度先于框架排队 |
| 11 | **读放大** | `get_run` 读完整 `RuntimeSnapshot` + wait 聚合（`run_service.py:1342-1353`）；`get_run_scope` 调 `get_run`（`:1355-1372`）；提交后 Gateway 再 `get_run`（`webchat_service.py:433-437`）；只用到 6 字段摘要（`dto.py:133-146`） | 每轮多次完整 snapshot JSONB；长会话/大 MCP catalog 时浪费明显 |
| 12 | **验证是“假绿”** | 1000 用户 acceptance 不断言 SLO；`steady_sec` 只记录不约束；模型探针每轮 6 段增量（`test_capacity.py:125-129`）；驱动不模拟通知轮询/client-config；PG 只采 `state='active'`（`metrics.py:375-377`） | 800 用户 admission p95≈20s 仍可能“通过”；真实流式和浏览器负载没测到 |

### 1.3 已经做对、保留的部分

- 准入策略行锁/共享 TENANT 计数行锁已移除；用户级计数仍严格（`run_admission.py`）。
- Gateway egress 池 64→1024，单 Pod PoolTimeout 归零。
- 受理材料合并为一次 `admission-materials`；Bot 快照有 revision/分页/不完整不发布。
- `ChannelEnvelope`、`DeliveryRouteInput`、`DeliveryMessage` 已是渠道中立契约；`channels/base.py` 的能力端口（附件/回复/主动投递/快照）可复用。
- Gateway SSE 公开投影/分片/背压/游标是纯无状态逻辑；Runtime `EventTailHub` 的共享追尾会释放读 Session（但请求级 Session 没释放）。
- Redis CAS 去重（owner token）已跨 Pod 安全；Runtime 的 reserve/ack + lease epoch 是可复用的 fencing 模式。

---

## 2. 目标架构（定稿）

### 2.1 服务边界

```text
                    ┌─────────────────────────────┐
Browser ──HTTP/SSE─>│ gateway (stateless, N)      │
Webhook ──HTTP─────>│ - unified message core      │
                    │ - WebChat SSE projection    │
                    │ - webhook adapters          │
                    │ - delivery facade           │
                    └────────────┬────────────────┘
                                 │ im.inbound / im.outbound (Redis Stream)
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
┌───────────────┐        ┌───────────────┐        ┌───────────────┐
│ connector     │        │ agent-runtime │        │ console-api   │
│ (stateful, N) │        │ (N)           │        │ (1-2)         │
│ WeCom/Slack/  │        │ Run execution │        │ auth/config   │
│ Telegram poll │        │ event produce │        │ admin         │
└───────┬───────┘        └───────┬───────┘        └───────┬───────┘
        │                        │                        │
        └──────────────┬─────────┴────────────┬───────────┘
                       ▼                      ▼
                 PostgreSQL              Redis
                 (durable facts)         (cache/stream/lease/dedupe)
```

### 2.2 统一消息网关内核

内核是一个**共享库 + 薄宿主**，不是新的大服务：

- `InboundEvent`：channel/bot/user/conversation/message_id/text/attachments/reply_target/received_at/transport_meta。
- `DeliveryCommand`：command_id/route/reply_target/kind(text|stream|finish|artifact)/payload/idempotency_key/connection_epoch。
- `ChannelCapabilities`：stream/edit/thread/media/read_receipt/typing/max_bytes/rate_limit。
- `ConnectorLease`：bot_id/owner/epoch/lease_until。
- 内核职责：路由键、去重、幂等、命令（/bind、/new、/stop、/skills）、准入、能力路由、出站投递、审计/指标。
- 两种入口模式：
  - **SYNC**：WebChat HTTP 提交在请求内调用 `admit_sync`，保持 202 低延迟；不进队列。
  - **ASYNC**：Connector/Webhook 写 `im.inbound`，由 gateway 的后台消费者/独立 `im-ingress` 调 `admit_async`；先持久化再 ACK。

> 统一的是内核与契约；不是所有协议都走同一条网络路径。

### 2.3 Connector

- 独立镜像 `muad-connector`，不包含 FastAPI 公开路由/WebChat/SSE；只做有状态传输。
- 每个 bot 一个 Redis lease + fencing epoch；同一 bot 同时只有一个 active connector。
- 入站：规范化 → `XADD im.inbound:{shard}`；出站：消费 `im.outbound:{bot_shard}` → 协议发送。
- 回复状态：内存热态 + Redis 镜像（`message_id→reply_ref` TTL 300s、`run_id→reply_target` TTL 3600s、stream 快照）；重连/epoch 不匹配时降级主动发送，绝不回落到“本路由最新回调”。
- 启用条件：**存在有状态通道**（企微/WS/poll），不是“多 Gateway”。
- WebChat-only 部署不启用 Connector；Webhook 默认作为 `gateway` 路由，不单独建服务。

### 2.4 部署矩阵

| 场景 | console | gateway | connector | runtime | worker | console-web |
|---|---:|---:|---:|---:|---:|---:|
| WebChat-only 默认 | 1 | N | — | N | N | 可选 |
| 启用企微 | 1 | N | 1+ | N | N | 可选 |
| webhook 高安全/高扩缩 | 1 | N | 1+ | N | N | 可选 + 可选 `channel-webhook` |

- 默认 compose：4 个容器（WebChat-only）或 5 个（企微）。
- `console-web` 是静态容器/CDN，不是业务微服务；默认仍由 `console` 镜像提供，静态流量大时用 overlay 切换。

### 2.5 中间件策略

| 组件 | 默认 | 何时升级 |
|---|---|---|
| PostgreSQL | 单主 + 显式池 | 读多 → 只读副本；连接多 → PgBouncer；写入超预算 → 分区/分集群/Citus |
| Redis | 去重/缓存/hint/Stream | Stream 容量/回放/多消费组不足 → Kafka/NATS |
| 对象存储/NFS | 已有 | 共享卷吞吐不足 → S3/对象存储 |
| PgBouncer | 可选 | Runtime/SSE 连接数成为瓶颈时 |
| Kafka/Citus/Service Mesh | **不默认** | 仅在实测触发后评估 |

---

## 3. 设计决策（ADR）

| ID | 决策 | 理由 | 代价/边界 |
|---|---|---|---|
| D-01 | Connector 从第一天独立部署（企微场景） | 长连接/回复状态有状态，晚拆会固化耦合 | 多一个镜像/Deployment |
| D-02 | Connector 与 Gateway **分开镜像**，同仓库/同 release/共享包 | 依赖/攻击面/扩缩/故障隔离 | CI 多一个构建目标 |
| D-03 | Gateway 请求面无状态；不持 DB、不持 socket | 可多副本/无 sticky/任意重连 | 需要 im.inbound/outbound 与短 Session |
| D-04 | WebChat 走 SYNC 核心，不进异步队列 | 202 延迟敏感；已有 auth/idempotency | 核心需要 resolved principal 上下文 |
| D-05 | IM 走 ASYNC 队列，先持久化再 ACK | 连接面与请求面解耦、可重放/背压 | 多一跳；at-least-once + 幂等 |
| D-06 | PG 只做持久事实；实时正文走 Redis Stream + 周期 PG 快照 | 2 万 commits/s 不可能靠调参解决 | 崩溃最多丢 ~1s 正文，需产品确认 |
| D-07 | 单主 PG 默认；分区/读副本/PgBouncer 按实测 | 开源部署简单；1000 用户不等于必须 Citus | 大租户/超高写入需 scale profile |
| D-08 | Console 先做 settings 单例 + auth SQL 合并 + page_size；身份缓存延后 | 实测固定 CPU 是主因，不是缓存命中 | 撤销语义不变；缓存后加 |
| D-09 | 前端先 code splitting；静态独立为可选 overlay | 4.2MB bundle/1000 冷加载会打 Python；先减体积 | 默认部署仍是 4 单元 |
| D-10 | 不按 IM 拆服务；adapter 是包 | 开源维护成本/边界清晰 | 需要 SPI + contract tests |
| D-11 | 验证是交付物：SLO 断言、真实流式、浏览器轮询、PG 状态 | 现有 1000 测试“假绿” | CI 时间增加；用分层/冒烟控制 |

---

## 4. 工作包（WP）

### WP-00 基线、指标与负载真实性（P0 / Phase 0）

**目标**：在改代码前，让“瓶颈在哪”可复现、可断言。

**改动**

- `tests/capacity/metrics.py`：PG 采样增加按 state 分解（idle / idle in transaction / active / waiting）、`idle_in_tx` 峰值/最大年龄、`xact_commit/s`、`wal_bytes/s`、rollback/deadlock/temp 速率；可选 `pg_stat_statements` top-N。
- `tests/capacity/driver.py`：支持真实流式形状（chunks/s × duration）、通知轮询（5/15s）、client-config/页面初始化、`steady_sec` 真正约束新提交、`attempts == users × turns`。
- `tests/acceptance/capacity/test_capacity.py`：在 acceptance 自身断言 SLO（admission p95/p99、first-delta p95、错误率、PG total/idle-in-tx/commits/s），不再只依赖 `report.py` 的 6 用户证据包。
- `tests/capacity/profile.py` + profile：增加 `stream`（chunks_per_sec/duration）与 `browser`（poll_sec/client_config）声明；`ACCEPTANCE` 缺这些即拒绝。
- `.github/workflows/check.yml`：加 `capacity_smoke`（100 用户 × 1 轮 × 20 chunks/s，3–4 分钟），只对 runtime/gateway/console/event-write 路径触发；完整 1000 用户仍为 release/nightly。
- 新增 `tests/capacity/test_capacity_webchat_postgres_state.py`（纯 SQL/解析单元测试，不连库）。

**验收**

- 当前代码在 100 用户 × 真实流式下能稳定复现：SSE 连接随流数增长、commits/s 超预算、idle-in-tx 可见。
- 报告字段能区分 steady/burst，且 800 用户历史数据若 p95 20s 会被判失败。
- `steady_sec`、`turns_per_user`、通知轮询不再是“只记录不判断”。

**依赖/风险**：无；这是后续所有验收的尺子。

---

### WP-01 Console 热路径：settings、auth、通知（P0 / Phase 1）

**目标**：把 Console 从“每条消息/每次轮询的同步依赖”降为低频控制面。

**改动**

1. **进程级 settings 单例/注入**
   - `packages/common/src/muad_common/settings.py`：增加 `get_settings()`（缓存）+ `reset_settings()`（测试）。
   - `packages/api-kit/src/muad_api/security.py:18-28`：`require_internal_service` 改为读 `Request.app.state.settings`（无 lifespan 的测试保留 fallback）。
   - `apps/console-platform/backend/src/muad_console_platform/api/deps.py:44-52,62-78`：`ConsoleSessionVerifier`、`get_header_tenant_id`、`service_token` 用注入 settings。
   - `platform_settings_service.py:109-111`：`batch_platform_limit` 从构造参数/注入 settings 取，不在 `_snapshot` 构造。
   - `agent-runtime` 的 `RunService`、`api/runs.py:30-36`、`api/deps.py:35-38,73-74` 同样改为注入。

2. **Chat auth 查询合并**
   - `chat_auth_service.py:99-123`：一条 JOIN（chat_session + chat_access_token + platform_user，`now()` 作为列返回）替代 4 条 SELECT；`_pg_now` 合并；保留错误优先级。
   - `channel_identity_service.py:61` 的 grants 查询保留为第二条。
   - 目标：5 SELECT + COMMIT → 2 SELECT + COMMIT（≤3 RTT）。

3. **通知轮询**
   - `apps/console-platform/frontend/src/modules/webchat/hooks/useNotifications.ts:406-409`：带 `page_size`（来自 `client-config` 的 `history_page_size`），不再让 Runtime 回源设置。
   - `webchat_history.py:64-71`：`page_size` 已给时零设置读取；未给时用进程设置缓存。
   - `channel_notification_queries.py`：`after_seq == through_seq` 时空页 fast path；把 `last_seq + unread_count` 合并为一次查询。
   - 轮询间隔：文档推荐 15s；不要只改 dataclass 默认（已保存的 `settings_json` 仍是 5.0），通过平台设置/运维文档生效。

4. **admission-materials 去重**
   - `internal_runtime.py:68-91` 与 `runtime_credentials.py:127-133` 重复解析 model/credential；合并成一次解析，避免每轮多 2–3 条查询。
   - 不要缓存模型 Key/凭据。

**验收**

- auth/settings 热路径 `SharedSettings()` 构造数 = 0（计数器/单测断言）。
- chat-principal ≤3 SQL（含 COMMIT），p95 <25ms（本地/目标环境）。
- 通知轮询不再触发 Console settings 调用（Runtime stub 断言 0 次）。
- 1000 用户浏览器同形负载下 Console CPU <60%，无 pool timeout。
- 前端测试更新（`tests/frontend/test_capacity_webchat_activity_ui.py` 的 query 形状）。

**依赖/风险**：WP-00 的浏览器轮询负载；settings 注入需要测试 reset；不要顺手加身份缓存（D-08）。

---

### WP-02 Runtime SSE Session 与连接池（P0 / Phase 1）

**目标**：1000 条 SSE 不再占 1000 条 PG 连接；池与闸门匹配。

**改动**

- 拆分流式路由与 `RunServiceDep/SessionDep`：新增 `RunStreamService`/短 Session 路径；`tail_run_events`/`get_run_scope` 在返回 `StreamingResponse` **之前** commit/close。
- `api/runs.py:101-111`：不再从请求级 `RunServiceDep` 取 session；用 `get_session_factory()` 短事务完成 run/tenant/cursor 校验。
- `infrastructure/db.py:32-41`：`get_session` 只在 `session.in_transaction()` 时 commit；避免手动 close 后二次 commit。
- 显式池：`DB_POOL_SIZE=20–32`、`DB_MAX_OVERFLOW=8`、`pool_timeout=1s`、`pool_pre_ping=False`、`pool_recycle=300`、`pool_use_lifo=True`。
- 准入闸门与写事务池匹配：材料阶段与写事务阶段分离；`gate ≤ pool + overflow`。
- 发布预检：允许每单元 DB 池键（当前要求清单值等于 typed 默认，导致无法按单元调池）；增加 `pool+overflow ≥ gate`、SSE 连接不随流数线性增长断言。
- PG 兜底：`idle_in_transaction_session_timeout=30s`（先修代码，再靠超时）。

**验收**

- `pool_size=2` 时同时打开 100 条 SSE：全部成功，PG total 不随流数线性增长，稳态 idle-in-tx ≈0。
- 1000 用户稳态 PG total <200；无 `QueuePool timeout`。
- 现有 replay/SSE 测试全绿；新增 endpoint 级连接预算测试。

**依赖/风险**：WP-00 指标；FastAPI 依赖语义；拆分 DI 时 tenant/auth 回归。

---

### WP-03 事件写入、序列、保留与索引（P0 / Phase 2）

**目标**：把 2 万 commits/s 降到可承受的 2–4k/s，并让 retention 追得上。

**改动**

1. **批量写**
   - 新增 per-Run `RunEventWriter`：有界队列；首段 delta 立即 flush；之后 250–500ms 或 8–16KB 合并。
   - 每批一次事务：
     - lease fence：一条 `UPDATE run_record ... WHERE lease_owner/epoch/lease_until ... RETURNING id`；
     - seq 块：一次 `UPDATE conversation SET last_seq=last_seq+N RETURNING last_seq`；
     - 多行 `INSERT canonical_event`（`insert().values([...])`/COPY）；
     - commit。
   - 去掉每事件的 `lock_execution`/`lock_run_source`（Run/Conversation/Continuation 三锁）与 `database_now`。
   - 写事件带 `execution_epoch`（或按最新 epoch 过滤），防僵尸写者。
2. **序列语义**
   - `last_seq` 视为“已分配高水位”；checkpoint/watermark 用 `MAX(canonical_event.seq)` 或新增 `last_committed_seq`，避免保留块造成 `CURSOR_AHEAD`。
   - 不对 `conversation.update_time` 做每批更新（它带索引，阻止 HOT）；保留给真实会话活动。
3. **保留/存储**
   - `channel_retention.py` 批量/循环吞吐提升到 ≥ 写入速率；retention 用 `create_time` 索引（partial/BRIN），不是 `(conversation_id, create_time)`。
   - 事件表按 `HASH(conversation_id)` 分区（16–32）或时间分区；先做 index/partition 迁移，再上量。
   - 事件主键 UUIDv7/时间有序（或 bigint identity）；评估去掉 `submission_id` FK（每行 FK 检查是纯开销）。
   - 删/替换未使用索引；历史事件 partial index（USER_MESSAGE/ASSISTANT_MESSAGE/TOOL_CALL/...）；`CONTEXT_SUMMARY` 单独 partial index。
4. **可选 Phase 3：Redis Stream 实时**
   - `XADD runtime:run:{run_id}`；PG 只存控制事件 + 1s/8KB 快照 + 终态。
   - SSE 从 Stream 读；重连从最后快照 + Stream 偏移；Redis 故障回退 PG 有界轮询。
   - 明确崩溃丢 ≤1s 正文的产品语义。

**验收**

- 100 Run × 20 delta/s × 10s：≤4 commits/s/Run、≤3 statements/event、无丢/重 seq、terminal 最后。
- 1000 Run 稳态：commits/s 在 2–4k（Phase 2）或 1–2k（Phase 3）；WAL/s 在磁盘预算内。
- retention 吞吐 ≥ 写入；表大小/索引在 24h 内不无界增长。
- SIGKILL：已提交前缀可 replay，无半行；终态 CAS 仍权威。

**依赖/风险**：WP-02 的连接缓解；序列块/水位语义；分区迁移成本；崩溃尾部丢失策略需产品确认。

---

### WP-04 Runtime 容量、HPA、模型额度（P1 / Phase 2）

**目标**：1000 个 Run 不因 32 个槽位/CPU-only HPA/模型 16 额度排队到超时。

**改动**

- 发布真实 gauge：`runtime_waiting_runs`、`runtime_oldest_queue_age_seconds`、`runtime_running_runs`、`runtime_execution_slots_in_use`、`runtime_execution_slots_configured`、`runtime_event_flush_lag_seconds`、`db_pool_waiting/timeout`。
- HPA：队列深度/最老年龄 + CPU/内存；提高 `maxReplicas` 与单进程执行槽（如 96–128/进程，按实测）；总槽位 ≥1000。
- 模型：`MODEL_HTTP_MAX_CONNECTIONS`/credential `max_inflight` 与执行槽同量级；否则 HPA 只是把队列搬到模型池。
- 把 `_load_history` 从受理响应路径移到后台 launch；受理只持久化并返回。
- 后台 memory consolidation：独立并发/队列/池，按用户消息阈值限速（当前 `min_user_messages=1` 会让每个 Run 入队）。

**验收**

- 1000 Run soak：队列在 SLO 内排空，无 queue deadline 大面积超时；HPA 按队列年龄触发。
- 模型探针/真实额度能承接目标并发；`max_inflight` 不是隐藏瓶颈。
- 受理 P95 不被 history load 拖累。

**依赖/风险**：WP-03 写入批量化；模型额度是外部硬约束；自定义指标适配器。

---

### WP-05 Gateway / Connector 拆分（P0 / Phase 2）

**目标**：Gateway 无状态多副本；企微连接/回复状态进独立 connector；不再重复连接/落错 Pod。

建议拆成 5 个子包，按顺序做：

#### WP-05a 契约与共享核心

- 新增 `packages/contracts/src/muad_contracts/im.py`：
  - `InboundEvent`（含 reply_target/transport_meta/received_at）
  - `DeliveryCommand`（command_id/route/reply_target/kind/payload/idempotency_key/connection_epoch）
  - `CommandResult`、`ChannelCapabilities`、`ConnectorLease`
- 新增 `packages/im-core`：SPI（InboundAdapter/OutboundSender/ConnectorHost）、Redis Streams 助手、lease/epoch、reply store 契约、route/shard key、全局/每路由 admission。
- 打开通道注册：`ChannelName` 不再硬编码 `WECOM/WEBCHAT`；`OUTBOUND_CHANNELS` 改为 capability/registry 驱动；DB channel 字符串接受新值。

#### WP-05b 新 `apps/im-connector`

- 新包/新镜像 `muad-connector`；移动 `channels/wecom/**`、`probe.py`（如仅测试用则保留在测试侧）、bot 快照（只 WECOM）。
- 不包含 FastAPI 公开路由/WebChat/SSE；只暴露内部 health/metrics。
- 依赖集只含 IM SDK/Redis/contracts/common；Gateway 不再依赖 WeCom SDK。

#### WP-05c Lease/Fencing/Reply Store

- `connector:bot:{bot_id}` `SET NX PX` + renew；命令携带 epoch，旧 owner 拒发。
- Redis 镜像 reply 状态：`message_id→reply_ref`（TTL 300s）、`run_id→reply_target`（3600s）、stream 快照；重连/epoch 不匹配走主动发送 + `DEGRADED` 审计。
- 入站 `XADD im.inbound:{shard}`；`XACK` 在受理裁决后；失败重试 + DLQ。
- 出站 `im.outbound:{bot_shard}`；`command_id` 幂等；结果写回。

#### WP-05d Gateway 改造

- `channels/registry.py`：只注册 WebChat/Webhook adapter；删除/移走 WeCom。
- `api/delivery.py`：保留 `/internal/deliveries` 契约，但变成 facade：校验租户/产物 → 发 `DeliveryCommand` → 有界等待结果/返回 accepted；WEBCHAT 走 Runtime inbox/SSE，不进 connector。
- `bot_snapshot`：按 channel 过滤；Gateway 不再拿 WECOM secret；Connector 只拿 WECOM。
- `channels/webchat.py`：删除 dead queue 或真正接入统一核心；不要保留“看起来有队列但生产不走”的假象。

#### WP-05e 部署/预检

- 新增 `deploy/k8s/base/im-connector.yaml`；Gateway `replicas: 2+`、HPA；Connector PDB/lease/资源。
- 改预检：从“固定四单元”改为“profile 声明的单元集合”；允许 connector/console-web 可选；`process_counts` 乘 FD/SSE 缓冲；每单元 DB 池键；移除 Gateway 单 Pod 限制。
- 更新 `max_replica_counts`、`pg_connection_budget`、FD 预算；保持 Console 1–2。

**验收**

- 两个 connector 进程 + 一个 bot：恰好一个 CONNECTED；kill owner → lease TTL 内接管；无重复发送。
- 两个 Gateway 副本 + 一个 bot：任意 Pod 收到投递都能路由到 owner；reply_ref 正确；重启 → 主动发送 + `DEGRADED`，不串消息。
- 同 route 不并发处理（shard/partition 或分布式锁）。
- WebChat 多副本任意 Pod 可服务/重连；SSE 不受 connector 重启影响。
- 预检在 5 单元/多 worker 下通过；旧 4 单元基线保留为兼容 profile。

**依赖/风险**：WeCom 是否允许多连接需真机/探针确认；req_id 跨连接可能失效；入站持久性（WS 收到但未 XADD 就崩）需平台重投或 PG inbox 兜底。不要用“pod_id/agent_pod”映射，保持架构测试诚实。

---

### WP-06 WebChat 接入统一核心（P0 / Phase 2）

**目标**：统一消息网关不只服务 IM；WebChat 也走同一路由/去重/准入/能力模型，但保持 SYNC 低延迟。

**改动**

- 内核提供 `admit_sync(event, principal_context)`；WebChat `submit_message` 用它，而不是直接 `runtime.submit_run`。
- 复用已验证的 `ChatIdentity` 作为 `principal_context`，避免再次 Console resolve；保留 `Idempotency-Key`。
- WebChat 的 attachments/upload、SSE、notifications 仍走现有无状态路径；只统一“入站→Run”核心。
- 明确 WebChat 是否需要 inbound audit/dedupe；如不需要，文档写清例外，不要留 dead queue。
- 未来 webhook：`POST /channels/{channel}/events` → 验签 → `XADD im.inbound` → 快速 2xx；adapter 包，不是新服务。

**验收**

- 同一 message_id 重试 → 只建一个 Run；SSE/202 延迟不回归。
- WebChat 与 IM 共用 route/dedupe/capability 测试；新增 fake channel 不改 `ChannelName` 字面量。
- 第三方 webhook 通道只需 adapter + route + contract tests。

**依赖/风险**：WP-05a 契约；WebChat 已有 auth 上下文，避免重复 Console 调用；不要在请求内引入队列跳数。

---

### WP-07 读放大清理（P1 / Phase 1–2）

**目标**：打开流/受理回执不再读完整 snapshot + 等待聚合。

**改动**

- `get_run_scope`：`run_record JOIN conversation` 一条 SQL 只取 user_id/conversation_id/agent_id/channel_account_id；不读 snapshot/wait。
- 受理帧带 `status`/`execution_status`；Gateway 不再在 202 后 `get_run`。
- `get_run`：snapshot 摘要用列选择 / `mcp_catalog_json->0->>'catalog_revision'`，不把整列 JSONB 拉进 Python；只有详情/审计读完整 snapshot。
- `_load_history` 移到后台 launch（WP-04）；历史 partial index（WP-03）。
- 可选：按会话缓存装配历史（append-only，last_seq 失效），先测量再决定。

**验收**

- 打开流 scope ≤2 SQL；202 路径不再 `get_run`；无完整 snapshot JSONB 传输。
- 受理/首字延迟下降可测；现有 API 形状不变。

**依赖/风险**：WP-02/03；响应字段兼容性。

---

### WP-08 前端静态与 bundle（P2 / Phase 3，按实测触发）

**目标**：1000 个页面冷加载不压 Console Python；聊天首屏更小。

**改动**

- 先做 code splitting：`frontend/src/App.tsx:8-21` 的 12 个 admin 页面改为 lazy；`ChatPage` 已 lazy。目标聊天入口不再下载 3.5MB JS。
- 可选 `console-web` overlay：复用现有 Vite build stage，产出 nginx 静态镜像；`deploy/k8s/overlays/console-web/`；Ingress `/assets`、`/chat`、`/` 指向它；Console API 镜像不再 `COPY --from=frontend`，`install_spa` 返回 False。
- 同源：不要换域名；Cookie/CSRF/Origin 不变。
- 默认 base 仍保持四单元；静态层是 opt-in。

**验收**

- 聊天首屏 JS 体积显著下降（目标 <1MB raw/gzip 视路由）；1000 冷加载不经过 Python。
- Playwright/e2e 路径不变；`/api/v1/webchat` 仍走 Gateway；SSE buffering off。

**依赖/风险**：预检/Ingress 测试；CDN 域名会引入 CORS/Cookie 问题，不要做。

---

### WP-09 后台任务隔离与背压（P1 / Phase 2）

- Memory consolidation 当前每个完成 Run 都入队（`min_user_messages=1`），1000 Run/10min 会打爆 worker/DB；改为阈值/批量/独立池/限速。
- Retention、delivery pump、audit export 使用独立连接池/并发；不与聊天抢 WAL/连接。
- 发布队列年龄/ backlog 指标；worker HPA 已有 `task_queue_depth`，补齐 runtime/console 侧。

**验收**：聊天延迟不受后台任务影响；backlog 有界；独立池不超 PG 预算。

---

### WP-10 PG 规模档（条件触发 / Phase 3+）

- 先做 WP-02/03：短 Session、批量写、分区、retention。单主 PG 能承接 1000 并发会话时，不上集群。
- 读多：只读副本（历史/报表；认证/撤销仍走主库）。
- 连接多：PgBouncer（transaction pooling；asyncpg prepared statement 兼容性验证）。
- 写仍超：控制面/事件面分集群；最后才 Citus/按 tenant/conversation 分片。
- 迁移：物理备份/逻辑复制/`pg_upgrade`；旧主只读保留；校验行数/序列/checksum。

**验收**：failover 不丢已受理 Run；副本 lag 在 SLO；写吞吐达标；运维成本可接受。

---

## 5. 阶段计划与依赖

| 阶段 | 工作包 | 产出 | 依赖 | 验收规模 |
|---|---|---|---|---|
| Phase 0 | WP-00 | 真实指标、SLO 断言、冒烟负载、失败基线 | — | 100 用户流式 smoke |
| Phase 1 | WP-01/02/07 | settings/auth/通知/SSE/池/读放大；**不加服务** | WP-00 | 200–400 用户 + 浏览器同形轮询 |
| Phase 2 | WP-03/04/05/06/09 | 批量事件/保留/索引、Connector 拆分、Gateway 无状态、HPA/模型、后台隔离 | WP-00/01/02 | 1000 用户 × 真实流式，SLO 全过 |
| Phase 3 | WP-08/10 + Redis 实时 | 静态 overlay、PG 规模档、Redis Stream 实时 | Phase 2 实测 | 1000 soak/chaos/故障切换 |

**关键路径**：WP-00 → WP-02（SSE 连接）→ WP-03（写入）→ WP-05（Connector）→ 1000 验收。WP-01/07 可并行，先拿低风险收益。

**发布顺序建议**

1. WP-00 先合入（只测试/指标，无行为变化）。
2. WP-01/02/07 小步发布；每步有回滚开关（settings 注入、短 Session 路由）。
3. WP-03 事件批量/索引迁移（维护窗口；先备份）。
4. WP-05 Connector 切换：先部署 connector，确认 bot lease/只连一次，再把 Gateway 的 WeCom 移除；切换窗口内不接受新企微消息或允许短时重连。
5. WP-04/HPA/模型扩容。
6. Phase 3 按实测。

---

## 6. 验收矩阵

### 6.1 必过阈值

| 指标 | 目标 |
|---|---|
| 受理 P95 / P99 | ≤500ms / ≤1500ms |
| 框架首字 P95 | ≤1200ms（模型额度足够） |
| 系统错误率 | <1% |
| Gateway/Console CPU | <60% limit（1000 用户稳态） |
| PG total connections | <200（1 Console + N Gateway + 4 Runtime + 8 Worker 预算内） |
| idle in transaction（稳态） | ≈0；峰值有界 |
| commits/s（Phase 2） | ≤4k/s；Phase 3 ≤2k/s |
| WAL/s | 在磁盘/保留预算内 |
| SSE 拒绝/缺终帧 | 0（正常负载） |
| pool timeout | 0 |
| retention | 删除速率 ≥ 写入速率 |

### 6.2 测试分层

| 层 | 内容 | 频率 |
|---|---|---|
| L0 单元/契约 | settings 构造计数、auth SQL 数、notification query 形状、connector lease/reply store、event batch 语义 | 每 PR |
| L1 架构/预检 | 5 单元/多 worker/FD/PG/SSE 预算、Gateway 不依赖 WeCom SDK、ChannelName 开放 | 每 PR |
| L2 冒烟 | 100 用户 × 1 轮 × 20 chunks/s + 通知轮询；3–4 分钟 | 触达关键路径的 PR |
| L2b 受限验证 | 200 用户 × 1 轮（现有 `make capacity-load`） | nightly/预合并 |
| L3 全量 | 1000 用户 × 8 轮 / 600s，真实流式 + 浏览器同形轮询；断言 SLO/PG/保留 | release/staging |
| L4 混沌 | kill connector/owner、Gateway Pod、Runtime Pod、PG failover、Redis 抖动 | nightly/staging |
| L5 soak | 400 用户 30–60 分钟，1 轮/30s + 通知轮询 | weekly |

### 6.3 测试改动清单

| 文件 | 改动 |
|---|---|
| `tests/capacity/metrics.py` + 新 PG state test | state 分解、idle-in-tx、commits/s、WAL/s、pg_stat_statements |
| `tests/capacity/driver.py` | 流式形状、通知轮询、steady 窗口、attachments、`active_runs_per_user` |
| `tests/capacity/profile.py` / profiles | `stream`/`browser` 声明；ACCEPTANCE 必填 |
| `tests/acceptance/capacity/test_capacity.py` | 在 acceptance 内断言 SLO、attempts=users×turns、PG/保留预算 |
| `tests/acceptance/capacity/test_stream_delivery.py` | 100 SSE + pool=2 连接预算；idle-in-tx |
| `tests/architecture/test_capacity_webchat_hpa_budget.py` | 要求 runtime 队列 gauge；不再允许 CPU-only |
| `scripts/check_agent_capacity_deployment.py` + tests | 5 单元/多 worker/可选 static-web；process_counts 乘预算；每单元 DB 池 |
| `tests/architecture/test_capacity_webchat_configmap_ingress.py` | 静态路径 overlay；SSE 配置保持 |
| `tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py` | 新指标目录/标签 |
| `tests/frontend/test_capacity_webchat_activity_ui.py` | `page_size` query 形状；轮询间隔 |
| 新 connector tests | lease/fencing、reply store、outbound routing、restart fallback |
| 新 WebChat core tests | sync admit、幂等、resolved principal、不重复 Console resolve |

### 6.4 当前测试的“假绿”必须修

- 1000 acceptance 不断言 SLO → 在 acceptance 内断言。
- `steady_sec` 只记录 → 真正约束窗口。
- `turns_per_user=8` 但只要求 attempts≥users → 断言 8000。
- 6 段增量 → 20/s × 10s。
- 无通知轮询/client-config → 加驱动。
- PG 只采 active → 加 state 分解/idle-in-tx/commits/s。
- 容量报告 6 用户 verdict 冒充 1000 → 明确区分 harness 与 acceptance。

---

## 7. 风险与未决

1. **企微多连接语义未验证**：同一 bot 多 WebSocket 是踢旧、分发还是拒绝？先用探针/真机验证，再确定 lease 行为；不要假设 active-active。
2. **req_id 跨重连寿命**：Redis 持久化 reply_ref 不一定能让新连接继续回复；安全行为是主动发送降级，需产品接受“可能换一条消息/重复”。
3. **入站持久性**：Connector WS 收到但未 XADD 就崩，是否会丢？需要 WeCom 重投语义或 PG inbox 兜底；不要承诺 exactly-once。
4. **实时正文崩溃丢 ≤1s**：Redis Stream + 快照的代价；若不能接受，保留批量 PG 写并把 commits 预算提高。
5. **模型额度**：`max_inflight=16` 是外部硬约束；1000 并发需要相应额度/多凭据，否则框架再快也排队。
6. **预检/部署形状**：固定四单元/单 Pod/typed 默认值会阻止 connector、多 worker、static overlay；必须先改预检，再改清单。
7. **多 worker 的进程内状态**：`ArtifactFetchTokens`、metrics registry、SSE ledger 都是进程级；多 worker 前要改共享存储/多进程指标，否则功能/观测会坏。
8. **每单元 DB 池**：当前预检要求清单值等于 typed 默认，无法按单元调池；要么改预检支持 profile 值，要么统一默认并接受全局预算。
9. **Mattermost 历史**：当前仓库无运行时代码；旧历史应只读归档/独立读模型/ETL，不要塞进 Run 模型（`public_events.py:137-146` 对 `run_id is None` 直接丢弃）。
10. **文档漂移**：旧 review 文档曾把 Console Dockerfile 写成 `apps/console-platform/backend/Dockerfile`（实际 `apps/console-platform/Dockerfile`，已修正）；旧 target 文档的 Stage/待确认已回收到本文。执行以本文和代码事实为准，发现新漂移先修文档再实施。

---

## 8. 每个 WP 的 Definition of Done

- 代码：改动落在上述路径；不引入 per-channel 服务；不引入默认中间件。
- 测试：对应单元/契约/架构/冒烟；新增指标在 `/metrics` 无流量也可见。
- 文档：更新 `docs/01/04/05/07/08`、`deploy/k8s/README`、capacity profile/validation 手册。
- 观测：关键阶段耗时、队列、PG state/rate、connector lease/reply 路由。
- 回滚：每个 WP 有明确开关/迁移回退；DB 迁移先备份、先 dry-run。
- 验收：按第 6 节阈值；未过不得写“已达标”。

---

## 9. 附录：代码索引

| 主题 | 位置 |
|---|---|
| Gateway/Console 单进程 CMD | `apps/im-gateway/Dockerfile`；`apps/console-platform/Dockerfile` |
| `SharedSettings` 热路径 | `api-kit/security.py:26`；`platform_settings_service.py:109-111`；`run_service.py:1007`；`api/runs.py:30-36`；`agent-runtime/api/deps.py:35-38,73-74` |
| Chat auth SQL | `chat_auth_service.py:99-123`；`channel_identity_service.py:61` |
| 通知轮询 | `useNotifications.ts:406-409`；`webchat_history.py:64-71`；`channel_notification_queries.py:116-137` |
| SSE Session | `api/runs.py:101-111`；`api/deps.py:112,125-151`；`infrastructure/db.py:32-41` |
| 事件写 | `run_service.py:2663-2684`；`run_events.py:128-260`；`tool_result_receipts.py:28-57` |
| 保留/索引 | `channel_retention.py:269-301`；`models/runtime.py:250-369` |
| 准入闸门/计数 | `run_service.py:889,1031`；`run_admission_store.py:100-114` |
| WeCom 状态 | `wecom/adapter.py:683-704,787-806,982-1004,1084-1105` |
| WebChat 旁路 | `webchat_service.py:216-259`；`channels/registry.py:57`；`channels/webchat.py:139-143` |
| 投递 | `api/delivery.py`；`channel_delivery_pump.py`；`configmap.yaml:20` |
| 静态前端 | `spa.py`；`apps/console-platform/Dockerfile`；`ingress.yaml` |
| HPA/执行槽 | `agent-runtime.yaml:61-62`；`capacity-autoscaling.yaml` |
| 预检 | `scripts/check_agent_capacity_deployment.py:72-75,325-347,432-459` |
| 容量测试 | `tests/capacity/*`；`tests/acceptance/capacity/test_capacity.py`；`.github/workflows/check.yml` |

---

## 10. 结论

这份整改方案的核心不是“加缓存/加 Pod”，而是：

1. **把 SSE 从 PG 连接生命周期里拿出来**；
2. **把增量写入从“每 50ms 一次事务”改成“批量 + 实时通道”**；
3. **把 Console 从每条消息的同步依赖降为低频控制面**；
4. **把企微连接/回复状态从公共 Gateway 移到独立 Connector**；
5. **用真实的 1000 用户流式负载和 SLO 断言验收，不再用 6 段增量的假绿**。

按 WP-00 → WP-01/02/07 → WP-03/04/05/06 → WP-08/10 的顺序执行，可以在保持开源部署简单（PG + Redis、4–5 个部署单元、无 per-IM 微服务）的前提下，把 1000 并发对话的瓶颈从 Gateway/Console/PG 的同步路径上移走。
