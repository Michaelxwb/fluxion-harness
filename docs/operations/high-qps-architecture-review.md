# 单 Gateway / 单 Console 的高 QPS 架构复核

日期：2026-10-24  
状态：背景/证据文档。执行以 **`docs/operations/high-qps-remediation-plan.md`** 为准。  
目标：**Console 1 Pod、Gateway 1 Pod、Runtime/Worker 可横向扩展**的前提下，支撑 1000 个用户同时对话，且 Gateway/Console 不是瓶颈。

本文只写代码与实测能证明的事实；参数是起点建议，不是压测结论。当前没有 1000 用户 × 真实流式增量的验收证据，最终以真实负载为准。

---

## 0. 结论先行

目标可以做到，但当前实现有 **4 个硬瓶颈**，缓存只能解决其中一部分：

1. **单进程 + 每请求固定 CPU 开销**：两个单元都只跑一个 uvicorn 进程，容器给了 2/4 核但 GIL 下只能用 1 核；`SharedSettings()` 每构造一次实测约 **1.5–2.5ms**（要读环境与 `.env`），仍在多条热路径上逐请求构造。  
2. **通知轮询把 Console 放大成每用户 2 次内部请求**：默认 5s 轮询，1000 用户就是 **200 rps**；且前端轮询不带 `page_size`，Runtime 每次都回 Console 取平台设置。Console 单进程首先在这里被压满。  
3. **每个 SSE 长连接占住一条 PG 连接**：Runtime 的 `/v1/runs/{id}/events` 用了请求级 `SessionDep`，FastAPI 的 yield 依赖要到流结束才释放；1000 条流就是约 1000 条 `idle in transaction` 连接。`db_pool_size` 默认只有 10，Runtime 的 pool_size × 副本数还会继续叠加，所以“Runtime 副本无限”在当前连接模型下不成立。  
4. **每个 50ms 增量一次事务 + 行锁 + `last_seq` 更新 + Redis 唤醒**：1000 个在飞 Run 约等于 **2 万次提交/秒**、十万级语句/秒。这不是“PG CPU 不够”，是写入模型本身超过了单机 PG 的可持续提交率。

一句话：**先修连接生命周期与事件写放大，再做缓存与多 worker；只加缓存会把瓶颈推迟几百毫秒，但不会消失。**

预期量级（不是承诺，需压测校正）：

| 指标 | 现状 | Phase 1 后 | Phase 2/3 后 |
|---|---:|---:|---:|
| Console 认证/设置请求 | ~300 rps | ~30–70 rps（轮询+缓存） | 同左 |
| Console 固定 CPU（通知路径） | 每请求 2 次 `SharedSettings` | ~0 | ~0 |
| PG 连接（1000 SSE） | ~1000（idle in tx） | <100 | <100 |
| PG 提交率（1000 在飞 Run） | ~20k/s（50ms 增量） | ~20k/s（尚未改写入） | 2–4k/s（250–500ms）或 1–2k/s（Redis 实时） |
| 单 Pod 可用核 | 1（单进程） | 1（仍单进程） | 4–8（多 worker） |

---

## 1. 目标负载口径

本文按下面的口径做算术；如果业务口径不同，结论要重算：

| 项 | 取值 |
|---|---|
| 同时在线/对话用户 | 1000 |
| 每用户活跃 Run | 1 |
| 轮次 | 8 轮 / 600s（平均 13.3 轮/s；突发可达 1000 轮同时提交） |
| 模型流式 | 20–50 个 chunk/s，单轮 10s 左右 |
| 浏览器标签 | 每用户 1 个；每标签 1 条 SSE |
| 通知轮询 | 当前默认 5s；建议目标 15–30s 或长轮询 |
| SLO（容量 profile 锁定值） | 受理 P95 ≤ 500ms、框架首字 P95 ≤ 1200ms、系统错误率 < 1% |

> 注意：**“1000 用户同时对话”不等于 1000 消息/秒**。按 8 轮/600s 是 13.3 轮/s；但 ramp=0 时会出现 1000 个受理请求同时到达的瞬时突发，必须有队列/限流。

另外，Console/Gateway 各 1 Pod 是**性能目标**，不是高可用目标；单 Pod 仍是单点故障。

---

## 2. 当前请求链放大图

### 2.1 发一条消息

```text
浏览器
  POST /api/v1/webchat/entries/{id}/messages
    -> Gateway ChatIdentityDep
       -> Console POST /internal/channel/chat-principal
          -> chat_session 查询 + now() + chat_access_token 查询 + platform_user 查询 + 授权集合查询
    -> Runtime POST /v1/runs
       -> Console POST /internal/runtime/admission-materials
          -> agent + grant + model + credential + skills join + MCP join
          -> platform settings 读取 + 解析
          -> 模型凭据 / MCP 凭据读取
       -> 准入写事务（Run/Snapshot/Submission/事件/会话更新/计数）
    -> Runtime GET /v1/runs/{id}
       -> 读 Run + **完整 RuntimeSnapshot** + 等待聚合
    -> 202
```

### 2.2 打开回复流

```text
GET /api/v1/webchat/runs/{id}/events
  -> Gateway ChatIdentityDep -> Console chat-principal
  -> Gateway GET Runtime /v1/runs/{id}/scope
     -> Run + **完整 RuntimeSnapshot** + 等待聚合 + 会话 scope
  -> Gateway GET Runtime /v1/runs/{id}/events
     -> Run + 水位检查
     -> StreamingResponse（请求级 DB Session 直到流结束才释放）
  -> 每 15s：ChatStreamGuard -> Console chat-principal
```

### 2.3 通知轮询

```text
浏览器每 5s:
  GET /api/v1/webchat/notifications?after_seq=...
    -> Gateway ChatIdentityDep -> Console chat-principal
    -> Runtime GET /internal/webchat/notifications
       -> 因为前端没带 page_size，先 Console GET /internal/v1/platform-settings
       -> feed 水位 + count + 页 + unread count
```

### 2.4 负载算术

| 来源 | 1000 用户下的请求率 | 说明 |
|---|---:|---|
| 通知轮询（5s） | **200 rps** | 每请求 2 次 Console 内部调用（auth + settings） |
| SSE 重复核（15s） | **67 rps** | 每条活跃流一次 Console 认证 |
| 新消息受理 | ~13 rps（突发 1000） | 每轮 2 次 Console 调用（auth + materials） |
| SSE 打开 | ~13 rps | auth + scope + events |
| 页面初始化/切换 | 低 | 但 1000 个页面同时打开会形成 ramp 突发 |

**Console 在还没有做任何业务之前，先要处理约 280–300 次/秒的认证/设置请求。** 单进程 1 核，这是当前最先被打穿的地方。

### 已经做过且有效的改动（不在本文重复报）

- 去掉了 `run_admission_policy` 的行锁与 TENANT 共享计数行锁，准入不再被全局串行化（`run_admission.py` 头注与 `check_counts_in`）。
- Gateway egress 池从 64 提到 1024，单 Pod PoolTimeout 归零（2026-10-21 历史复核记录，已移除，见 git 历史）。
- Console 受理合并为一次 `admission-materials`，并复用启动 settings 做服务身份校验。
- 探针预算改为 5s/10s/3 次，避免健康检查重启打断 SSE。
- Continue/续跑沿用冻结的 pool/members，不退回直连。

这些改动解决了“确定性错误/全局锁/探针重启”，但**没有解决**下面的每请求固定 CPU、SSE 连接占用和事件写放大。

---

## 3. 关键发现

### P0-1：单进程 + 逐请求 `SharedSettings()`

**证据**

- 两个镜像的启动命令都没有 `--workers`：
  - `apps/im-gateway/Dockerfile`
  - `apps/console-platform/Dockerfile`
- 800 用户实测：Gateway 991m、Console 932m，容器上限 2/4 核但 Python 单线程只能吃 1 核（2026-10-21 历史复核记录，已移除，见 git 历史）。
- `SharedSettings()` 本机实测约 **2.5ms/次**（200 次平均；此前 Pod 内记录为 1.45ms）。每次构造都会读环境与 `.env`。
- 仍在热路径逐请求构造的位置：
  - `packages/api-kit/src/muad_api/security.py:26`：`require_internal_service` 每个内部请求构造一次；Console 的 `/internal/channel/chat-principal` 就用它。
  - `apps/console-platform/backend/src/muad_console_platform/application/platform_settings_service.py:109-111`：`_batch_platform_limit()` 每次读设置都构造一次；通知轮询的 settings 端点、受理 materials 都走这里。
  - `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:1007`：每个 Run 请求构造 `RunService` 时构造一次。
  - `apps/agent-runtime/src/muad_agent_runtime/api/runs.py:30-36`：每次打开/创建 Run 的 SSE 响应构造一次。
  - `apps/agent-runtime/src/muad_agent_runtime/api/deps.py:73-74`：每次 Run 请求构造 `ArtifactResultWriter` 时构造一次。
  - `apps/agent-runtime/src/muad_agent_runtime/api/deps.py:35-38`：memory 端点每次请求构造一次。

**影响**

- 通知轮询路径 Console 每请求两次固定 CPU（auth + settings），200 rps × 约 3–5ms = **0.6–1.0 核**，已经接近/超过单进程上限；这还没算 5 条认证 SQL、JSON 序列化和 HTTP 栈。
- 认证本身是 5 条串行 SQL（session、now()、token、user、grants）：`apps/console-platform/backend/src/muad_console_platform/application/chat_auth_service.py:99-123`。可以合并成“session+token+user 一次 JOIN（用 SQL 的 now() 判过期）+ grants 一次查询”，把 5 次往返降到 2 次；在 200 rps 下是 1000 → 400 查询/s。
- Runtime 的 Run 创建/打开流每请求也会多出 3–4 次构造，ramp 到 100/s 时是 0.4–1.0 核。
- 这与“pg 活跃会话只有个位数、CPU 却 932m”的现场完全一致：CPU 花在事件循环里的固定开销，不是 PG。

**建议**

- 进程级唯一 settings：启动时构造一次，放进 `app.state.settings`；所有请求路径注入/读取它，不逐请求构造。
- `api-kit.security.require_internal_service` 改成读 `Request` 上的 `app.state.settings`（或注入一个 `SettingsProvider`），并为未跑 lifespan 的测试保留显式 fallback。
- `PlatformSettingsService` 的 `batch_platform_limit` / 硬上限改为构造时注入，不在 `_snapshot()` 里构造 settings。
- `RunService`、`_sse_response`、`get_artifact_writer` 从依赖注入取同一个 settings。
- 给测试提供 `reset_settings_provider()`，避免 env 变更后缓存串味。

**预期**：Console/Runtime 每请求固定 CPU 去掉 1.5–2.5ms × N；通知路径的 Console CPU 至少降一半。

---

### P0-2：通知轮询的 3 次内部调用

**证据**

- 平台设置默认 `notification_poll_sec = 5.0`：`packages/contracts/src/muad_contracts/platform_settings.py:252-254`。
- 前端轮询只传 `page/after_seq`，不传 `page_size`：`apps/console-platform/frontend/src/modules/webchat/hooks/useNotifications.ts:406-409`。
- Runtime 只有在显式给了 `page_size` 时才不取设置：`apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py:64-71`；否则每次向 Console 取一次平台设置。
- Console settings 端点本身还会走 `_batch_platform_limit()`（见 P0-1）。

**影响**

- 1000 用户 × 1/5s = 200 rps；每次通知轮询产生：
  1. Gateway → Console chat-principal（约 5 条 SQL + 固定 CPU）
  2. Runtime → Console platform-settings（1 条 SQL + 固定 CPU + 设置解析）
  3. Runtime 自身 feed/count/page/unread 查询
- 这是“聊天已经结束、用户只是挂着页面”时仍在持续打 Console 的负载，和对话是否活跃无关。
- 容量驱动是 HTTP 脚本，不模拟浏览器通知轮询，所以 800/1000 用户验收**没有测到这部分**；真实浏览器负载会更高。

**建议**

1. 前端把 `history_page_size`（或专用通知页大小）随请求带上，直接消掉每次设置读取。
2. Runtime 在进程内按 `(tenant, settings_revision)` 缓存解析后的设置；通知页大小从缓存取，TTL 30s 或按修订号失效。
3. `notification_poll_sec` 默认改 15–30s；或在页面可见时用 25s 长轮询，服务端在 feed 变化时立即返回。
4. 空闲 fast path：`after_seq == through_seq` 且无未读变化时直接返回空页，不再做 count/page/unread 三条查询。
5. 未读数放 `channel_notification_feed.unread_count` 或 Redis，随通知写入/已读事务更新；轮询只读一个水位行。

**预期**：200 rps → 33–67 rps；每次轮询从 2 次 Console 调用降到 0 次（命中设置缓存）或 1 次（只认证）。

---

### P0-3：SSE 长连接占住 PG 连接（最被低估的一条）

**证据**

- Runtime 的 SSE 路由：`apps/agent-runtime/src/muad_agent_runtime/api/runs.py:102-111`，依赖 `RunServiceDep`。
- `RunServiceDep` 依赖 `SessionDep`：`apps/agent-runtime/src/muad_agent_runtime/api/deps.py:125-151`。
- `SessionDep` 的 `get_session` 在响应结束后才 `commit()`/`close()`：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/db.py:32-41`。
- FastAPI/Starlette 的 yield 依赖在 `StreamingResponse` 整个响应体发送完之后才退出；因此从 `tail_run_events` 返回 `StreamingResponse` 开始，到客户端断开/流结束，**这条 Session 一直占着连接**，并且处于 `idle in transaction`（SELECT 已开启事务）。

**影响**

- 1000 条 SSE = 约 1000 条 PG 连接；如果 pool_size 是 400，4 个 Pod 上限 1600，PG 上限 2000 时已经没什么余量。
- 默认 `db_pool_size=10`（`packages/common/src/muad_common/settings.py:48-50`）时，每进程只能同时挂 10 条流；第 11 条开始等池，5s 后 `QueuePool timeout`。
- 这也是“Runtime 副本无限”不成立的原因：连接数 ≈ 活跃流数 + pool_size × 副本数；加 Pod 会把空闲池也算进 PG 总连接。
- PgBouncer 的 transaction pooling 也救不了：连接被一个打开的事务占着，pooler 不能把它借给别人。
- `pg_stat_activity` 里这些连接是 `idle in transaction`，不是 `active`；只看 `state='active'` 会得出“PG 很闲”的错误结论。容量报告要同时看 total / idle / idle in transaction。
- 长时间 `idle in transaction` 还会阻止 vacuum 清理旧快照，放大表膨胀。

**建议**

- 为流式端点引入独立的 `RunStreamService`：用短生命周期 session 完成 `run` 加载、游标检查、scope 解析，**在返回 `StreamingResponse` 之前关闭 session**；流内只通过 `EventTailHub` 的短 session 读页。
- 或者给流式路由换一个不持有连接的 `SessionScope`，并在 `get_session` 里只在 `session.in_transaction()` 时 commit，避免“手动 close 后依赖再 commit”的边界。
- 加回归测试：`db_pool_size=2` 时同时打开 100 条 SSE，断言全部能打开、PG total 连接不随流数线性增长。
- PG 侧设置 `idle_in_transaction_session_timeout`（如 30s）作为兜底，但先修代码，不要靠超时杀流。

**预期**：PG 连接从“约等于流数”降到“按并发查询数”，1000 流时可以稳定在几十条；Runtime 才能真正按 Pod 扩展。

---

### P0-4：每个增量一次事务 + 行锁（真正的 PG 写瓶颈）

**证据**

- 增量窗口默认 50ms / 4KB：`packages/common/src/muad_common/settings.py:87-88`。
- 每个增量落库走 `_persist_event`：`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:2663-2684`：
  1. `database_now(session)`：一次 `SELECT now()`
  2. `lock_execution(...)`：`SELECT run` + `SELECT conversation FOR UPDATE` + `SELECT run FOR UPDATE` + `SELECT run_continuation FOR UPDATE`（`tool_result_receipts.py:28-57`）
  3. `EventWriter.append`：`UPDATE conversation SET last_seq = last_seq + 1` + `INSERT canonical_event`
  4. `COMMIT`
  5. Redis `PUBLISH` 唤醒
- 写入后 `EventTailHub._read_round` 每个唤醒还会做水位查询（含 `max(seq)` 子查询）+ 页查询：`event_tail_hub.py:588-625`。

**影响**

- 1000 个在飞 Run，每个 20 次增量/s（50ms 窗口的上界）= **20,000 次事务/s**、约 10 万–16 万条语句/s、2 万次 Redis publish/s。
- 单机 PG 在同步提交下的可持续提交率通常是千级/秒（取决于磁盘与 group commit）；2 万/s 不是调参能解决的量级。
- 每次事务还锁 conversation 行 + run 行；同一 Run 内串行，跨 Run 不冲突，但锁/解锁本身、WAL、4 个索引的写入、`conversation.last_seq` 的 dead tuple 都会叠加。
- 现有 800 用户验收探针只下发 **6 段增量**（`tests/acceptance/capacity/test_capacity.py:126-129`），根本没有压到这个路径；所以“800 用户通过”不能证明真实模型流式下的 PG 能力。

**建议（先做低成本、再做实时通道）**

**Phase 2（不改协议，先把写入降一个数量级）**

- 增量按 250–500ms 或 8–16KB 合并；**首段增量立即 flush**，保证首字延迟不变。
- 每个 Run 一个批量写事务：一次 `SELECT now()`、一次 lease 校验（`UPDATE ... WHERE lease_owner/epoch/lease_until` 返回行）、一次 `last_seq` 范围分配、一次批量 `INSERT`（`insert().values([...])` 或 `COPY`）、一次 commit。
- 去掉每增量的 `lock_run_source`（conversation + run 双行锁）；把“租约仍有效”校验放到批次边界，而不是每条事件。
- `EventTailHub` 被唤醒时先直接读页；页为空再查水位，省掉每次唤醒的 `max(seq)` 查询。
- 目标：每 Run 2–4 次提交/s，1000 Run 约 2k–4k 提交/s；再配合 PG 调参/PgBouncer 达到可持续区间。

**Phase 3（要“丝滑”就必须做）**

- 实时增量走 Redis（Pub/Sub 或 Stream），PG 只持久化：控制事件、周期性正文快照（如 1s/8KB）、终态消息。
- SSE 从 Redis 实时通道读，不再每个增量都读 PG；重连用 PG 的最后快照 + 后续实时消息补齐。
- Redis 是单线程：实时增量仍要按 100–250ms 合并，不要每 token 一次 PUBLISH；1000 流 × 4–10 msg/s ≈ 4k–10k ops/s，需要实测 Redis 延迟。
- 代价：需要明确“崩溃时最多丢 1s 正文”的产品语义；如果必须逐字持久，则保留 Phase 2 的批量落库，接受更高写入。
- 这一步把 PG 写入从“每 token 一次”降到“每秒/每快照一次”，是 1000 并发流式对话的根本解。

> 缓存不能替代这一步：缓存只减少读，不减少 2 万次/秒的 commit。

---

### P0-5：DB 连接池配置没有真正接线

**证据**

- 默认 `db_pool_size=10`、`db_max_overflow=0`、`db_pool_timeout_sec=5`：`packages/common/src/muad_common/settings.py:48-50`。
- `deploy/k8s/base` 的四个 Deployment 都没有 `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`；`configmap.yaml` 也没有。
- Runtime 的准入闸门是 64：`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:889`；执行槽是 8/进程：`deploy/k8s/base/agent-runtime.yaml:61-62`。池 10 与闸门 64 明显不匹配。
- 历史验收里 Runtime 每进程池是 400、Console 20+5 / 100+25（2026-10-21 历史复核记录，已移除，见 git 历史），但这些是集群里手工改的，**没有进清单**；仓库默认仍是 10。
- `pool_pre_ping=True`：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/db.py:17`、Console 同；每次 checkout 多一次 ping。
- 容量 profile 的 `pg_connection_budget` 是 200，但 runtime 400 × 4 + console 25 × 4 + worker 30 × 3 = 1790（burst evidence），预算口径与实测配置不一致。

**影响**

- 生产清单按默认 10 运行时，Runtime 8 个执行槽 + 准入 + 后台循环很容易把池打满；`pool_timeout=5s` 会把正常排队变成 5s 延迟/500。
- 如果为了 SSE 把 pool 调到 400，Runtime 扩到 8/16 个 Pod 就会撞 PG `max_connections`；这与“Runtime 副本不限”冲突。
- `pool_pre_ping` 在高 QPS 下是纯额外 RTT；PG 连接被中间件/NAT 断开时才会用到它。

**建议**

- 清单显式声明每进程池：Console 20–40、Runtime 16–32（修完 SSE 连接占用后再定）、Worker 8–16；`max_overflow` 留给突发但计入总预算。
- 准入闸门 ≤ 池上限（例如闸门 32、池 32），不要让 64 个请求抢 10 条连接。
- `pool_timeout` 降到 1s，快速失败并在 Gateway 侧转成 429/503，而不是 5s 挂死。
- 用 PgBouncer（transaction pooling）把“进程数 × pool_size”与 PG `max_connections` 解耦；asyncpg 需按 PgBouncer 版本设置 prepared statement 缓存策略。
- 去掉 `pool_pre_ping`，改用 `pool_recycle=300`、`pool_use_lifo=True`、PG/TCP keepalive；保留低频探活。
- 发布预检增加一条：`db_pool_size + max_overflow >= admission_gate`，并且总连接 ≤ 预算。

**预期**：没有池超时；PG 总连接可控；Runtime 扩 Pod 不再线性吃 PG 连接。

---

### P1-1：`get_run` / `get_run_scope` 读放大

**证据**

- `get_run` 每次读完整 `RuntimeSnapshot` 行，再算等待聚合：`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:1342-1353`。
- `get_run_scope` 直接调用 `get_run`，再查会话：`run_service.py:1355-1372`。
- `run_view` 把 `snapshot_summary(snapshot)` 放进响应：`apps/agent-runtime/src/muad_agent_runtime/application/dto.py:161-183`。
- Gateway 发消息后 `_owned_run` 又调一次 `get_run`：`apps/im-gateway/src/muad_im_gateway/application/webchat_service.py:433-437`；Gateway 只用其中的 `conversation_id`、`status`、`execution_status`，并不需要完整 snapshot。
- 打开 SSE 时 Gateway 只用 `user_id`、`conversation_id`、`conversation.agent_id`，却拿到了完整 run view + snapshot 摘要 + 等待聚合。

**影响**

- 每个新消息至少两次完整 snapshot 读（提交后一次、打开流一次），每次可能几十 KB 到 MB；1000 用户 × 8 轮 = 上万次，属于纯浪费。
- `_wait_summary` 每次 2 条聚合/查询；对新受理的 Run 根本不需要。

**建议**

- 新增 lean scope 查询：`run_record JOIN conversation` 只取 `user_id/conversation_id/agent_id/channel_account_id`，一条 SQL。
- 受理帧直接带 `status`/`execution_status`，Gateway 不再为 202 调 `get_run`。
- `get_run` 的 snapshot 摘要用列选择或 `mcp_catalog_json->0->>'catalog_revision'` 提取，不把整列 JSON 拉进 Python。
- 只有 Run 详情页/审计需要完整 snapshot；流打开和受理回执不需要。

---

### P1-2：Gateway 单进程 + 每帧指标锁

**证据**

- 每个 SSE 帧都会：
  - `SseBufferLedger.charge()` / `release()` 各调一次 `_publish_watermarks()`，每次两个 `set_gauge`；`retire()` 再一次：`apps/im-gateway/src/muad_im_gateway/application/webchat_streams.py:504-539`。
  - `note_sent()` 调 `record_sse_frame()`：同文件 `544-553`。
- `MetricsRegistry` 每次 `inc_counter/set_gauge` 都拿全局 `threading.Lock`、排序 label、写 dict：`packages/api-kit/src/muad_api/metrics.py:42-61`。
- 每帧还要 pydantic `model_validate` + `model_dump_json`（`webchat_streams.py:389-395`）和 `uuid.uuid5` 生成公开 event id（`webchat_streams.py:91-94`）。

**影响**

- 单帧成本不是主要问题（pydantic 约 6.8µs、uuid5 约 2.7µs、指标约 1µs），但 1000 流 × 20 帧/s = 2 万帧/s，三项叠加就是 0.2–0.3 核；再加 1000 条流、200+ rps 的普通请求、SSE 任务调度，单进程 1 核会贴顶。
- 每帧 3 次全局锁在 2 万帧/s 下会放大事件循环抖动。
- 多 worker 后 `/metrics` 每次只命中一个 worker，指标不完整；`SseBufferLedger` 的订阅上限也变成每 worker 一份。

**建议**

- 单 Pod 多 worker（`uvicorn --workers` 或 granian），把 CPU limit 提到 4–8 核；每 worker 独立事件循环。
- 每 worker 的 `GATEWAY_MAX_SSE_SUBSCRIBERS` 设为总预算/worker 数（例如 400 × 4 = 1600，留重连余量）；或者用 Redis 全局信号量保证精确总上限。还要有每用户/每会话的并发订阅上限，防止一个用户开很多标签页把全局预算吃光。
- 缓冲区水位指标改成每秒/每 N 帧发布一次；帧计数按流/秒聚合，不要每帧三次锁。
- 多 worker 的指标用 `prometheus_client` multiprocess 模式，或每 worker 单独暴露再由 sidecar 聚合。
- `message.delta` 走快速路径：已知公开字段直接构造 JSON；pydantic 只用于低频/结构复杂事件。`public_event_id` 可以改为更便宜的确定性 ID（或按 run 预计算命名空间）。
- 普通请求的响应模型不要重复 pydantic dump；可评估 `ORJSONResponse`。

---

### P1-3：静态资源由 Console 的 Python 进程提供

**证据**

- `install_spa` 用 Starlette `StaticFiles`/`FileResponse` 提供 `/assets` 和 SPA 回退：`apps/console-platform/backend/src/muad_console_platform/spa.py`。
- Ingress 把 `/assets`、`/chat`、`/` 都路由到 Console：`deploy/k8s/base/ingress.yaml`。

**影响**

- 1000 个页面在 ramp 期首次加载，每个几 MB 的 JS/CSS，单 Python 进程用线程池读文件/发字节；会与认证/API 抢 CPU 和事件循环，且单 Pod 带宽有限。
- 浏览器会缓存带 hash 的 assets，但 1000 个首次加载仍然集中。

**建议**

- 静态资源放 CDN 或对象存储；至少给 Console Pod 加 nginx sidecar 专门 serve `/app/static`，API 仍走 Python。
- `/assets` 保留 immutable 缓存；`index.html` no-cache 不变。
- Ingress 把静态路径直接指到 sidecar/CDN，不经过 FastAPI。

---

### P1-4：Runtime 的容量与 HPA 不支撑 1000 并发

**证据**

- Runtime HPA 上界 4：`deploy/k8s/base/capacity-autoscaling.yaml`；每进程执行槽 8：`deploy/k8s/base/agent-runtime.yaml:61-62`；总槽位 32。
- HPA 只看 CPU/内存；注释明确说“Runtime 的随负载在飞槽/队列 gauge 尚未发布”（`capacity-autoscaling.yaml` 顶部）。
- 容量验收里 Runtime 执行槽是 128/进程 × 2 = 256（`tests/acceptance/capacity/test_capacity.py`），仍然小于 1000。
- 模型物理在飞额度示例是 16（`MODEL_MAX_INFLIGHT`），1000 个并发模型调用会在这里排队。

**影响**

- 1000 个 Run 在 32 个槽前排 30 多轮，首字 P50 44s 的现场主要来自这里，不是 Gateway/Console。
- 这些 Run 大部分在等模型 I/O，CPU 不一定高；只按 CPU 扩容可能根本不触发，HPA 4 个 Pod 封顶。
- 模型额度 16 是外部硬约束；Runtime 再多也过不去。

**建议**

- 发布 Runtime 队列指标：`runtime_waiting_runs`、`runtime_oldest_queue_age_sec`、`runtime_execution_slots_in_use`；HPA 用队列年龄/深度 + CPU，而不是只看 CPU。
- 提高单进程执行槽（32–64）并配合多 worker/多 Pod，把总槽位做到 ≥1000；同步重算模型连接池、PG 池、FD。
- 模型侧按目标并发扩额度/多凭据/多部署；否则“1000 用户流畅”只能做到“排队但不报错”。
- 这部分不属于 Gateway/Console，但决定端到端 SLO，必须一起验收。

---

### P1-5：后台任务与聊天共用 PG

- Memory consolidation、retention sweep、delivery pump、audit export、worker task 都会写同一 PG。1000 用户 × 8 轮会触发大量 consolidation job。
- 建议：按优先级隔离（独立连接池/独立 PgBouncer pool/低峰限速），至少给后台 job 设并发上限和 `statement_timeout`；不要让它们和实时事件写抢同一批连接/WAL。
- 如果预算允许，长期把 control（Auth/Console）与 runtime 事件拆到不同 PG 集群/实例，避免事件写风暴拖慢登录/受理。

---

### P1-6：验收负载没有覆盖真实浏览器/真实流式

**证据**

- 受控探针每轮只发 6 段增量：`tests/acceptance/capacity/test_capacity.py:126-129`。
- 负载驱动是 HTTP/SSE 脚本，不模拟通知轮询、client-config、页面初始化、静态资源（`tests/capacity/driver.py`）。
- 800 用户复核是 4 Console/4 Gateway（2026-10-21 历史复核记录顶部已声明非目标拓扑；记录已移除，见 git 历史）；单入口复测的 200/400/800 数据里 800 档完成数仍有 285–583 波动。
- 容量报告采样 `connections_active`，但没有把 `idle in transaction`、提交率、按语句的 p95 单独报告。

**建议**

- 探针增加“每轮 N 段、间隔 50ms”的真实流式形状（例如 20/s × 10s = 200 段）。
- 驱动增加浏览器同形请求：通知轮询、client-config、conversations/messages、SSE 重连。
- 报告增加：`xact_commit/s`、`wal_bytes/s`、`pg_stat_activity` 按 state 分解、`idle in transaction` 峰值、`pg_stat_statements` top。
- 断言：`connections_total` 不随 SSE 流数线性增长；`commits/s` 在预算内；无 pool timeout。

---

## 4. 缓存设计清单

缓存只放在“读多写少、可按修订号/水位验证”的事实上；不要缓存 Run 状态/终态/凭据原文。

| 缓存 | 位置 | Key | 失效/TTL | 收益 | 风险 |
|---|---|---|---|---|---|
| 进程设置 | 每进程 `app.state` | 单例 | 启动固定 | 去掉 1.5–2.5ms/次构造 | 测试需 reset |
| Chat 身份/授权 | Gateway 进程 LRU（可选 Redis 共享） | `sha256(session_token)+context` | TTL 5–30s；登出/撤销可发失效事件 | 通知/SSE 复核从 200+67 rps 降到接近 0 | 撤销最多延迟 TTL；需单飞防击穿 |
| 平台设置快照 | Console 进程（Runtime 进程也可） | `(tenant, revision)` | 新 revision 自然失效 | 去掉设置读取/解析；通知页大小不再回源 | 无（revision 寻址） |
| Agent 定义（非密钥） | Console/Redis | `(tenant, agent, agent_revision, skill/mcp revision)` | 配置写时失效或全局 control revision | 每次受理少 4–6 条 join | 失效点较多；密钥不要进缓存 |
| 模型凭据 | 不建议共享缓存 | — | 每次受理实时解析 | 13 rps 不是瓶颈 | 明文密钥进 Redis 风险高 |
| client-config 投影 | Gateway | `(tenant, settings_revision)` | 30s 或 revision | 页面初始化少一次 Console 设置读 | 低 |
| 会话历史装配 | Runtime LRU | `(conversation_id, last_seq)` | 追加时增量更新 | 避免每轮重扫大量 delta 行 | 内存上界；压缩/摘要要失效 |
| Run scope | Runtime/Gateway | `run_id` | 60s（scope 不变） | 打开流少一次 run+snapshot 读 | Run 归属不变，安全 |
| 通知未读/水位 | Runtime Redis 或 feed 行 | `(tenant,user)` | 写入/已读时更新 | 轮询少 2–3 条查询 | 要与事务一致 |
| Bot 快照 | Gateway | 已有 30s | 已有 | 已解决 | — |
| 静态资源 | CDN/nginx | URL hash | immutable | 把页面加载移出 Python | 部署复杂度 |

### Chat 身份缓存的关键细节

- Key 用 token 的 SHA-256，不存原文；缓存值只含 `ChatIdentity`（principal、expires_at、agents），并强制 `now < expires_at`。
- TTL 与 `chat_auth_recheck_sec` 对齐（15s）或更长（30s）；如果产品要求“撤销立即生效”，在 Console 的 logout/token revoke/user disable/grant revoke 处发 Redis 失效事件，Gateway 订阅后删除对应 key。
- 每个 key 做 single-flight；TTL 加 ±20% 抖动，避免 1000 个会话同时过期造成回源尖峰。
- 多 worker 时本地缓存命中率会下降；如果回源仍高，放 Redis 共享（一次 GET 约 0.2ms，远低于 Console 的 5–10ms）。
- 更彻底的做法是 Console 在交换/复核时签发一个短 TTL 的签名身份断言（tenant/user/session/expires/授权集合），Gateway 本地验签、零回源；撤销仍靠 TTL + 失效事件。这个改动比缓存大，但能把 Console 从“每个聊天请求的必经同步依赖”变成“低频刷新依赖”。

---

## 5. PG 侧建议（按优先级）

### 5.1 连接与池

1. 先修 SSE 请求级 Session（P0-3）；这是“连接数 ≈ 流数”的根因。
2. 清单显式声明 `DB_POOL_SIZE/DB_MAX_OVERFLOW`；准入闸门 ≤ 池上限；`pool_timeout` 1s。
3. 去掉 `pool_pre_ping`，用 `pool_recycle` + keepalive；`pool_use_lifo=True`。
4. 引入 PgBouncer（transaction pooling）；按 PgBouncer 版本配置 asyncpg prepared statement cache。
5. 给 Console/Runtime/Worker 用不同 DB 用户/连接池上限；后台 job 单独池，避免抢实时连接。

### 5.2 写入模型

1. 增量批量写：250–500ms/8–16KB，一个事务、一次 lease 校验、一次 seq 范围、一次批量 INSERT；首段立即 flush。
2. 去掉每增量的 conversation + run 双行锁；租约校验放批次边界。
3. `conversation.last_seq` 每批一次更新，或改成全局序列/块分配；避免每 50ms 更新同一行造成 dead tuple。
4. 实时增量走 Redis（Pub/Sub/Stream），PG 只存控制事件 + 周期快照 + 终态；这是 1000 并发流式的根本解。
5. 终态/控制事件保持同步提交；增量批次可评估 `synchronous_commit=off`（最多丢最后一小段，需产品确认）。

### 5.3 索引与表

1. `canonical_event` 增加历史事件 partial index（`USER_MESSAGE/ASSISTANT_MESSAGE/TOOL_CALL/...`），避免历史装配扫描 delta 行；`latest_summary` 也加 partial index。
2. `run_record` 增加 `(tenant_id, admission_released_at)` partial index（`WHERE admission_released_at IS NULL`），让租户在飞计数可索引。
3. 评估删除/替换 `ix_canonical_event_conversation_create_time`：历史查询按 seq，retention 按 create_time + stream_type，可改成更小的 partial index。
4. 事件表主键用 UUIDv7/时间有序 ID，或 bigint identity；当前 `gen_random_uuid()` 随机 B-tree 写入会放大 WAL/页分裂。
5. 评估 `canonical_event.submission_id` 外键；每行 INSERT 的 FK 检查在 2 万行/s 下是纯开销，若应用层已保证可去掉。
6. 事件表按 hash(conversation_id) 或时间分区；配合 retention 和 per-partition autovacuum。
7. `conversation` 是高更新小表；关注 autovacuum，避免 `last_seq` 更新造成膨胀。

### 5.4 PG 参数与观测

- `max_connections` 与 PgBouncer 预算对齐；给迁移/维护留余量。
- `shared_buffers`、`effective_cache_size`、`max_wal_size`、`checkpoint_timeout`、`wal_compression` 按机器调；事件写是 WAL 密集型。
- `idle_in_transaction_session_timeout=30s`、`lock_timeout=2s`、Web 请求 `statement_timeout=5s`（后台 job 单独放宽）。
- 开启 `pg_stat_statements`，按 `calls、total_exec_time、rows、shared_blks_written` 排序；容量报告增加提交率、WAL/s、锁等待、按 state 的连接数。
- 如果事件写仍影响认证，考虑 control 与 runtime 分库/分实例；至少分连接池和 WAL 磁盘。

---

## 6. 目标架构（单 Pod，多进程）

```text
Browser
  -> Ingress (SSE: buffering off, timeout 45s; static -> CDN/nginx)
     -> Gateway Pod (1 Pod, 4 workers)
        - CHAT 认证缓存（本地 LRU + 可选 Redis 失效）
        - 公开 SSE 投影/背压（每 worker 独立账本）
        - 普通请求只做转发/投影，不持库
     -> Console Pod (1 Pod, 2–4 workers)
        - 认证/受理材料/设置（revision 缓存）
        - 管理面与聊天控制面隔离；静态资源移出 Python
     -> Runtime Pods (N, HPA by queue age/depth)
        - 执行槽 × worker；实时增量 -> Redis
        - PG 持久化快照/控制事件/终态
     -> PG (primary + PgBouncer)
     -> Redis (cache/hints/live stream/未读)
```

职责边界（这是“Gateway/Console 不成为瓶颈”的前提）：

- **Gateway**：只做公开协议、CHAT 门控/缓存、SSE 投影与背压；不做业务状态、不持库。
- **Console**：只做低频控制面（认证、授权、设置、受理材料）；不做每条消息的数据面。
- **Runtime**：执行与实时流；横向扩展；PG 只存持久事实，实时增量走 Redis。
- **PG**：权威事实 + 审计；不承担每 token 的实时广播。

要点：

- **一个 Pod ≠ 一个进程**。Gateway/Console 各 1 Pod 完全可以用 4 个 uvicorn worker；这才是用满 4 核的前提。需要同步改：每 worker 池预算、SSE 上限、Prometheus 多进程指标、DB 连接预算。
- Runtime/Worker 的“无限副本”只有在 PG 连接被 PgBouncer 解耦、SSE 不再占连接后才成立。
- PG 只做“持久事实”；实时正文走 Redis；否则 1000 并发流式对话的写入量一定先打穿 PG。
- Console 仍然是单 Pod 单点；如果要 HA，需要另立目标（本文不承诺）。

---

## 7. 参数起点（需实测校正）

| 单元 | 建议起点 | 说明 |
|---|---|---|
| Gateway Pod | 4 workers，4–8 vCPU，6–8Gi | 每 worker：stream ~400、egress ~256、control ~8–16；SSE 每 worker ~350–400（总 1400–1600）；按 4096 FD 预算重算 |
| Console Pod | 2–4 workers，4 vCPU，4–6Gi | DB pool 20–40/worker；总连接计入预算 |
| Runtime Pod | 2 workers，4 vCPU，8–16Gi | 每 worker 32–64 执行槽；DB pool 16–32/worker；按队列扩 Pod |
| Worker Pod | 2 workers，2–4 vCPU | 后台 job 独立池/限速，不与聊天争连接 |
| PG | 8–16 vCPU，32–64Gi，NVMe | `max_connections` 500–1000 或 PgBouncer 100–200 server 连接 |
| Redis | 2–4Gi，maxclients ≥ 10000 | hints + cache + live stream；按需 AOF/副本 |
| Ingress | SSE buffering off，read/send 45s | 静态资源走 CDN/nginx |

这些数字不是验收结论；先按 Phase 1/2 压测，再用 `pg_stat_statements`、提交率、P95 调整。

---

## 8. 分阶段路线图

### Phase 0：先让瓶颈可见（小）

- 加阶段指标：Gateway `auth_ms/upstream_ms/project_ms`；Console `db_ms/settings_ms/resolve_ms`；Runtime `precheck_ms/materials_ms/write_ms/history_ms`；事件写 `lock_ms/insert_ms/commit_ms`。
- 容量报告增加 `idle in transaction`、`xact_commit/s`、`wal_bytes/s`、`pg_stat_statements` top。
- 负载驱动增加真实流式（20/s × 10s）与浏览器同形轮询。
- 目标：在 1 Gateway/1 Console 下复现“Console CPU / PG 连接 / PG 提交率”三条曲线。

### Phase 1：低成本高收益（小–中）

- settings 单例/注入（P0-1）。
- 通知带 `page_size`、设置缓存、轮询 15–30s、空闲 fast path（P0-2）。
- Gateway chat identity 缓存 + single-flight（P0-2/缓存表）。
- SSE 请求级 Session 修复（P0-3）。
- 池显式配置、准入闸门 ≤ 池、`pool_timeout=1s`、去 pre_ping（P0-5）。
- lean `get_run_scope`、受理帧带 status、去掉提交后 `get_run`（P1-1）。

**验收**：先跑 200–400 用户 + 真实增量 + 浏览器同形轮询，确认 Console/Gateway CPU < 60%、PG total connections < 200、无 pool timeout；再用 1000 用户确认连接与 CPU 不再随用户数线性上涨（PG 提交率仍可能超预算，留给 Phase 2）。

### Phase 2：写入与进程模型（中）

- 增量批量写 + 去每事件行锁 + 批量 INSERT + 首段立即 flush（P0-4）。
- Gateway/Console 多 worker；Prometheus 多进程指标；SSE 每 worker 上限。
- 事件表 partial index、UUIDv7、去掉冗余索引/FK、`run_record` partial index。
- PgBouncer + 池预算 + 发布预检。
- Runtime 队列 gauge + HPA 队列指标；提高执行槽/副本上界。
- 静态资源 CDN/nginx sidecar。

**验收**：1000 用户全程；PG `xact_commit/s` 在预算内；锁等待为 0；首字 P95 ≤ 1.2s（模型额度足够时）。

### Phase 3：实时通道与隔离（大）

- Redis 实时增量 + PG 周期快照/终态；SSE 重连从快照 + 实时流补齐。
- 事件表分区/retention/autovacuum。
- control 与 runtime 分库/分实例（或至少分池/分盘）。
- Runtime/Worker 后台 job 限速与隔离。

**验收**：真实 1000 用户 × 8 轮 soak；无 PG 写尖峰拖慢认证；Gateway/Console 保持 < 60% CPU。

---

## 9. 风险与需要业务确认的点

1. **认证缓存允许的撤销延迟**：5s、15s 还是 30s？是否需要 Redis 失效事件做到“立即”？
2. **实时增量的持久性**：崩溃时最多丢 1s 正文是否可接受？如果不可以，Phase 3 只能保留批量 PG 写，不能完全移到 Redis。
3. **通知轮询间隔**：5s → 15/30s 是否影响产品体验？是否接受长轮询/推送？
4. **单 Pod 单点**：Gateway/Console 各 1 Pod 在发布/故障时会短暂不可用；本文不提供 HA。
5. **模型额度**：1000 并发模型调用需要相应额度；16 的示例额度会先于所有框架瓶颈排队。
6. **PgBouncer 兼容性**：asyncpg prepared statement 与 transaction pooling 需要按版本验证。
7. **多 worker 指标**：现有进程内 Prometheus registry 在 4 worker 下不完整；需要 multiprocess 或 sidecar 聚合。
8. **配置漂移**：历史验收的 400/100+25 池是手工值；必须进清单并由发布预检校验。

---

## 10. 代码索引（便于逐项落地）

| 主题 | 位置 |
|---|---|
| Gateway/Console 单进程 CMD | `apps/im-gateway/Dockerfile`、`apps/console-platform/Dockerfile` |
| `SharedSettings` 构造点 | `packages/api-kit/src/muad_api/security.py:26`；`platform_settings_service.py:109-111`；`run_service.py:1007`；`api/runs.py:30-36`；`api/deps.py:35-38,73-74` |
| 通知轮询默认/缺 page_size | `packages/contracts/src/muad_contracts/platform_settings.py:252-254`；`useNotifications.ts:406-409`；`webchat_history.py:64-71` |
| SSE 请求级 Session | `api/runs.py:102-111`；`api/deps.py:125-151`；`infrastructure/db.py:32-41` |
| 每增量事件写 | `run_service.py:2663-2684`；`tool_result_receipts.py:28-57`；`run_events.py:128-260`；`event_tail_hub.py:588-625` |
| 50ms 增量窗口 | `packages/common/src/muad_common/settings.py:87-88` |
| 池默认 | `packages/common/src/muad_common/settings.py:48-50`；`infrastructure/db.py:12-24` |
| 准入闸门 | `run_service.py:889`；`run_service.py:1031` |
| Runtime 执行槽/HPA | `deploy/k8s/base/agent-runtime.yaml:61-62`；`deploy/k8s/base/capacity-autoscaling.yaml` |
| Snapshot 读放大 | `run_service.py:1342-1372`；`dto.py:161-183` |
| Gateway SSE 每帧指标 | `webchat_streams.py:504-553`；`packages/api-kit/src/muad_api/metrics.py:42-61` |
| 静态资源 | `apps/console-platform/backend/src/muad_console_platform/spa.py`；`deploy/k8s/base/ingress.yaml` |
| 容量 profile | `tests/capacity/profiles/agent-capacity-webchat.example.yml` |
| 历史实测 | 2026-10-21 历史复核记录（已移除，见 git 历史） |

---

## 11. 本机测量记录

```text
SharedSettings() 构造：200 次平均 2.5ms（此前 Pod 内记录 1.45ms）
parse_platform_settings：0.035ms/次
WebchatDeltaEvent pydantic construct + model_dump_json：约 6.8µs/事件
uuid.uuid5(public_event_id)：约 2.7µs/事件
MetricsRegistry inc_counter / set_gauge：约 0.4µs/次（含全局锁）
```

这些是单机微基准，不是容量结论；只用于说明“固定开销 × 请求率”和“每帧成本 × 帧率”的量级。
