# WebChat 1000 用户容量瓶颈分析与压测方法

- **状态**：诊断记录 + 目标方案草案。**2026-10-27 已用 100 用户真实栈复测、修订排序，
  并完成首轮 CPU 归因（§0.6）**：以 §0（实测）与 §7（最终修复方案）为准；
  §1.2 / §2.6 / §4 / §5.4 保留作诊断过程记录。
- **目标**：1000 用户 × 8 轮 / 600s，20 chunks/s × 10s，通知轮询 15s；目标拓扑下的 SLO 达标
- **数据来源**：`tests/acceptance/capacity/`、`tests/capacity/`、2026-10-25～2026-10-27 本地实测、临时 run-timeline tracer
- **相关文档**：
  - `docs/operations/high-qps-target-architecture.md`
  - `docs/operations/high-qps-remediation-plan.md`
  - `docs/operations/high-qps-architecture-review.md`
  - `scripts/capacity/PLAYBOOK.md`（压测方法与度量口径手册，2026-10-27）
  - `.code-flow/tasks/2026-10-24/high-qps-remediation/`

> 本文把“已实测的事实”和“待验证的推断”分开写。后续 Agent 细化方案时，不要把推断直接写成结论。

---

## 0. 实测复测与修订（2026-10-27）

用 `make capacity-smoke`（100 用户 × 1 轮 × 20 chunks/s × 10s，真实四单元栈 + 本地 PG/Redis + 独立临时库）
做了四项测量（连接池等待/持有、idle-in-tx、执行分段计时、`synchronous_commit` A/B），并用 1s 间隔
`ps` 采样了各单元 CPU；随后又做了首轮 CPU 归因（§0.6）。**本节结论优先于下文 §1.2 / §2.6
的旧排序（§7 已按实测重写）**；旧文保留作诊断过程的记录。

### 0.1 一句话结论

> 这套负载下，**runtime 进程把一整个 CPU 核打满**（负载窗口内单副本连续 ~14s 99–100.6%）；
> 同一时刻 PG 基本空闲、连接池零等待、关闭 `synchronous_commit` 毫无变化。
> 下文的“事件逐事件事务是第一瓶颈”**不成立**——它是 CPU 饱和之后被放大出来的现象。
>
> 归因（§0.6）：这核 CPU 主要烧在**异步 DB 管道**上——SQLAlchemy 的 greenlet 桥接 / 会话 /
> 提交开销，加上每事件一次往返（`database_now` 是额外一次 `SELECT statement_timestamp()`）；
> 其次是 `SharedSettings()` 的重复构造（3.14ms/次 × 每轮约 10 处）。业务逻辑与新增计时打点
> 可忽略（≈0.5%）。

### 0.2 实测数据（同机 3 次运行，偏差 ~10%）

| 指标 | sync_commit=on | sync_commit=off | sync_commit=on（重复） |
|---|---:|---:|---:|
| admission P50 / P95 | 1220 / 2707 ms | 1304 / 2634 ms | 1235 / 2745 ms |
| first-delta P50 / P95 | 4168 / 7988 ms | 3937 / 7273 ms | 4268 / 8986 ms |
| 单事件持久化均值（n≈1700） | 129 ms | 139 ms | 125 ms |
| PG commits/s | 91.4 | 92.7 | 92.8 |
| PG 事件行/s | 38.7 | 39.2 | 39.0 |
| 取连接等待 P50 / P95 | ≤0.5 / ≤0.5 ms | ≤0.5 / ≤0.5 ms | ≤0.5 / ≤0.5 ms |
| 连接持有 P50 / P95 | ≤10 / ≤100 ms | ≤10 / ≤250 ms | ≤10 / ≤100 ms |
| idle-in-tx 峰值 | 31 | 37 | 38 |

分段计时（单次均值；`on` 两次运行的口径一致）：

| 分段 | 均值 |
|---|---:|
| 受理 gate / materials | 5 ms / 45 ms |
| 受理 precheck / claim / write | 294 ms / 385 ms / 320 ms |
| 模型 candidates / reserve / credentials | 236 ms / 411 ms / 27 ms |
| 单事件持久化 | 125 ms（P95 ≤ 1000 ms） |
| 回合 checkpoint / 终态 finalize | 71 ms / 89 ms |
| 每 Run 持久化合计（~30 事件） | 3.8 s |

CPU（负载窗口内，1s 采样）：一个 runtime 副本连续 14s 稳定 99.0–100.6%，另一副本峰值 96%；
gateway ≤41%，console ≤47%。

### 0.3 四条测量的裁决

1. **连接池等待/持有：不是瓶颈。** 6611 次取连接 98.6% ≤0.5ms，30 连接池从未排队。
2. **idle-in-tx：是症状不是泄漏。** 峰值 31–38，但年龄都短（`BEGIN` 0.03s、最差
   `UPDATE conversation` 0.19–0.91s），最后一条语句全是微秒级操作——应用在两条语句之间接不上，
   不是“事务里 await 网络”。
3. **分段计时：所有段被同比例放大。** 连一条单表 SELECT（`_candidates`）的 P50 都要 100–250ms，
   而池零等待、PG 提交毫秒级 ⇒ 成本在事件循环调度，不在 SQL。
4. **sync_commit A/B：提交成本可排除。** 关掉同步提交后 admission/first-delta/持久化全在噪声内；
   实测 commits/s 仅 ~92，离 §3.1 假设的 3.3k/s 差两个数量级。

### 0.4 由实测收紧的口径

- **单进程并发流上限**：本次一个 runtime 进程处理 ~57 条并发流（100 用户经 2 副本分摊）就满核。
  目标 profile 是 2 个 runtime 进程（`process_counts=1 × replica_counts=2`），1000 用户 × 8 轮 /
  600s、每轮 10s 流 ⇒ 稳态每进程要扛约 380–500 条并发流，是饱和点的 **6–8 倍**。
  多 worker/多副本不是“P1 优化”，而是 **1000 用户的准入前提**。
- **group commit 的收益来源要改写**：不是减少 fsync/提交次数（已证否），而是减少每条事件的
  **语句数、往返数与事件循环上的 Python 开销**；目标指标应是“每进程语句数/CPU”，不是 commits/s。
- **历史 800 用户结论**（2026-10-21）不受影响：那是专用集群 + 更大连接预算，与本地突发负载不可比。

### 0.5 本次到达的可观测面

应用侧已常驻打点（`apps/agent-runtime`，无行为变更，1084 条 runtime 测试全绿）：
`db_pool_checkout_seconds`、`db_connection_hold_seconds`、`run_persist_event_seconds`、
`run_checkpoint_seconds`、`run_consume_seconds{stage}`、`run_finalize_seconds`、
`run_admission_stage_seconds{stage}`、`model_call_seconds{stage}`。
读法（累计桶 `_le_` 复算分位数）与整套复现步骤见
`scripts/capacity/PLAYBOOK.md`；配套脚本在 `scripts/capacity/`
（`loadgen.py` 对已部署栈发压，`diag.py` 读分位数/采 CPU/采 idle-in-tx/切 `synchronous_commit`）。

### 0.6 CPU 归因首轮（2026-10-27，25 用户，pyinstrument + cProfile 交叉）

- 负载窗口内**一个** runtime 副本约占 73% 单核（1ms 采样：5s 窗口 3666/5000 样本）；
  另一个副本几乎空闲（smoke 的请求分布不均，读数只作单进程上限参考）。
- **pyinstrument**：76.6% 的样本落在事件循环最外层（`Runner.run [self]`）——这是
  SQLAlchemy greenlet 切换（C 层、无 Python 帧）的落点，不能读成"空闲"。可归因的 Python
  份额：`_persist_event` 3.3%、终态 finalize 3.4%（其中 memory enqueue 里的
  `SharedSettings()` 构造 ≈2.8%）、SSE 解析 2.4%、tail hub ≈2.4%；本次新增的计时指标 ≈0.5%。
- **cProfile**（同窗口 1.2M 次调用）：自身时间 top 全是 SQLAlchemy async 管道
  （`greenlet.switch`/`greenlet_spawn`/`Session.commit`/`_connection_for_bind`/
  `_execute_clauseelement`/cache_key/coercions/unitofwork，合计 ≈28%）；`database_now`
  累计 27%；`_persist_event` 每次 ≈0.57ms CPU（greenlet 会放大，只作量级参考）。
- **微基准**：`SharedSettings()` = **3.14ms/次**；runtime 在每请求/每轮路径上约有 10 处构造点。

按性价比的下一步：

1. `SharedSettings()` 进程级缓存/注入（最便宜：每轮省 ~30ms CPU，低风险）；
2. 每事件事务瘦身：把 `database_now` 的 `SELECT statement_timestamp()` 合并进
   `lock_run_source` 的 `SELECT … FOR UPDATE`（每事件少一次往返），再把事件写批量化
   （收益目标是摊薄 commit/ORM/greenlet 次数，**不是** fsync）；
3. 每 chunk 管线占比仍待证：固定用户数、改 chunk 率或最小模型 A/B 后再决定是否瘦身。

### 0.7 仍待探索（2026-10-27 CPU 归因之后的开放问题）

- [x] **chunk 管线 vs DB 路径的占比**：2026-10-27 微基准裁决——chunk 管线 1.35ms/轮
      （最坏 8.16ms/轮），DB 事件路径优先（§0.8）。
- [ ] **76% “无 Python 帧”的更细归因**：目前只能确认落在 SQLAlchemy greenlet 切换；需要
      greenlet tracing 或在 staging 上用 py-spy（本机 macOS 需 root）把 greenlet/asyncio 内部分开。
- [ ] **`database_now` 合并的语义边界**：现状是**加锁前**取时；并进 `lock_run_source` 的
      `RETURNING statement_timestamp()` 仍是语句起点（锁前），不能实现"锁后"。需先修取时顺序，
      再评估折进锁后写语句（§0.8 P1-1）。
- [ ] **事件写入批量化的设计**：批边界（按会话/按进程）、锁顺序、seq 分配、部分失败原子性；
      验收指标是"每进程语句数/CPU"，不是 commits/s。
- [ ] **`SharedSettings()` 缓存边界**：哪些调用点允许进程级缓存（`.env` 变更何时生效）、
      是否改为装配根注入冻结值；`api/deps.py` 每请求多处构造是首要目标。
- [x] **请求分布**：25/50/100 三档都复现"一个 runtime 副本忙、另一个几乎空闲"；容量按最忙
      副本算，先复核网关/连接层均衡（§0.8 P1-2）。
- [ ] **tail 唤醒合并的收益重估**：profiler 里只占 ~2.4%，低于原文档的预期优先级；
      批量写事件后应重新测 wakeup 次数与 CPU。
- [x] **SLO 拐点**：25/50/100 阶梯完成；单进程约 60–70 条并发流饱和，100 用户最忙副本
      p90 98.5%、连续 14s ≥90%（§0.8）。

### 0.8 P0-1/P0-2/P0-3 实测（2026-10-27 下午，100 用户真实栈）

三项探测的共同结论：**chunk 管线和轮询节拍都不是杠杆；单进程 CPU 饱和是硬约束，
而 CPU 主要花在每事件的 DB 往返/ORM/greenlet 管道上。**

#### P0-1 每 chunk 管线 CPU（微基准，不启栈）

`uv run python scripts/capacity/chunk_bench.py`（200 chunks/轮、15 轮、20 chunks/s 节拍；
真实 `_parse_stream` → `DeltaWindow` → `_ExecutorEventEmitter` 有界队列 + 内存消费者）：

| 场景 | µs/chunk | ms/轮（200 块） | 目标拓扑每进程 CPU* |
|---|---:|---:|---:|
| `parse_only` | 1.59–1.62 | 0.32 | 0.2% |
| `parse_window` | 1.68–1.74 | 0.34–0.35 | 0.2% |
| `emitter_realistic`（300ms/8192B） | **6.41–6.76** | **1.28–1.35** | **0.9%** |
| `emitter_flush_each`（每块到界，最坏） | 37.7–40.8 | 7.53–8.16 | 5.0–5.4% |

\* 1000 用户 × 8 轮 / 600s = 13.33 轮/s ⇒ 2667 chunks/s，2 个 runtime 进程 ⇒ 1333 chunks/s/进程。

（两次运行的偏差约 5%，区间即实测波动。）

**裁决**：chunk 管线每轮只花 1.3ms 左右，相对每轮约 3.8s 的事件持久化可以忽略；即使最坏
"每块都 flush"也只有 7.5–8.2ms。**第一刀不切 chunk 路径**，先切事件持久化路径。
顺带复测 `SharedSettings()` = **2.96ms/次**，与 §0.6 的 3.14ms 一致。

#### P0-2 25/50/100 用户阶梯（同一脚本，1s `ps` 采 CPU）

| 用户 | admission P95 | first-delta P95 | 通知 P95 | idle-in-tx | commits/s | event rows/s | 最忙 runtime 10s 峰值 | 最忙 p90 | 另一副本 p90 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 25 | **89ms** | **306ms** | 61ms | 4 | 48.8 | 11.2 | 46.5% | 36.0% | 1.8% |
| 50 | **408ms** | **1342ms** | 150ms | 27 | 64.6 | 21.5 | 88.2% | 67.3% | 2.7% |
| 100 | **2250ms** | **7805ms** | **1054ms** | 38 | 92.0 | 38.8 | **99.3%** | **98.5%（14s ≥90%）** | 37.7% |

100 用户复跑：admission 2367ms / first-delta 7533ms / 通知 960ms / idle 30 / commits 92.7，
偏差 ~5%；结论稳定。

**拐点在 50–100 用户之间**：

- 25→50：请求量 2×，admission P95 4.6×、first-delta 4.4×，最忙进程 p90 36%→67%。
- 50→100：请求量 2×，admission P95 5.5×、first-delta 5.8×，最忙进程 p90 67%→98.5%，
  出现连续 14s ≥90%。
- 连接池等待仍可忽略（`db_pool_checkout_seconds` 6470 次里 6369 次 ≤1ms），
  commits/s 只从 49 涨到 92 ⇒ **不是 PG 吞吐到顶，是单进程 CPU 到顶后排队**。
- 100 用户时通知 P95 也从 61ms 涨到 1054ms；Console p90 CPU 27.5%。1000 用户
  （通知量 10×）会先撞 Console/通知路径，不能只修 Runtime。

**裁决**：单进程约 **60–70 条并发流**即饱和；1000 用户按 2 个 runtime 进程要
380–500 条/进程，是饱和点的 **6–8 倍**。多 worker/多副本是准入前提；同时必须把
每事件 CPU 成本降一个量级，否则进程数要按 6–8 倍补。

#### P0-3 队列轮询 A/B（50 用户，`QUEUE_POLL_SEC` 0.05 → 0.5）

| 指标 | 基线 0.05s | A/B 0.5s |
|---|---:|---:|
| admission P95 | 408ms | 490ms |
| first-delta P95 | 1342ms | 1475ms |
| idle-in-tx 峰值 | 27 | 25 |
| 最忙 runtime 10s 峰值 | 88.2% | **88.3%** |

**裁决**：CPU 没有下降（88.2%→88.3%，噪声内），延迟反而略升。`wait_for(queue.get())`
的定时器 churn 不是可观测杠杆；**不要**为了 CPU 把轮询改成事件驱动。若以后为延迟做，
收益也应重新测，不能引用这次。

#### 100 用户饱和副本的分段账（`/metrics` 差量，57 个 Run）

| 指标 | 合计 | 每次/每 Run |
|---|---:|---:|
| `run_consume_seconds{stage=total}` | 724.4s | 12.7s（含并发重叠） |
| ├ `wait`（等模型流/队列） | 516.3s | 9.1s |
| ├ `persist` | 208.2s | 3.65s（1760 事件 × **118ms/事件**） |
| └ `first_event` | 30.9s | 542ms |
| `run_finalize_seconds` | 5.6s | 98ms |
| admission `precheck` / `claim` / `write` | 32.7 / 29.1 / 29.8s | 324 / 288 / 295ms |
| model `candidates` / `reserve` / `credentials` | 15.0 / 28.9 / 1.6s | 264 / 508 / 28ms |
| model `upstream`（探针本身 10s 流） | 611.2s | 10.7s |

解读：

- **persist 的 118ms/事件是墙钟，不是 CPU**。该副本负载窗口内总 CPU 约 14s；
  1760 事件即使按 cProfile 的 0.57ms/事件算也只有 ~1s。剩下的墙钟是事件在事件循环/
  连接池/行锁之间排队。
- 但 CPU 也确实是满的：1760 事件 × 约 7 条语句/往返，每往返 ~0.5ms 的
  SQLAlchemy async/greenlet 开销 ≈ 6s，加上 admission/model/finalize/SSE 才凑到 14s。
  **所以批量化的收益指标是"每进程语句数/往返数/CPU"，不是 commits/s。**
- `upstream` 10.7s 是探针 10s 流的本来形状；first-delta 7.5s 是平台开销叠加在流内，
  不能把 10s 算进平台成本。
- 通知 P95 1s、Console p90 27.5% 是 1000 用户的前置风险，需缓存/推送/多 worker，
  不能等 Runtime 修完再处理。

#### P1-1 `database_now` 能否并进 `lock_run_source`

读代码结论（未改实现）：

- `_persist_event` 的实际顺序是 `database_now` → `lock_execution`（Python 先求值参数），
  所以拿到的 PG 时间是**加锁前**的，与 `db_clock.py` 注释宣称的"锁后时刻"不符。
- `lock_execution` 每事件实际发 5 条语句：先无锁 SELECT Run、锁 Conversation、锁 Run、
  锁 RunContinuation；`database_now` 是第 5 条（在锁之前）。
- 把 `database_now` 并进 `SELECT … FOR UPDATE … RETURNING statement_timestamp()` 能省一次
  往返，但 `statement_timestamp()` 是**语句起点**，仍在行锁等待之前；只是把现在的
  "锁前时刻"固化，不能实现"锁后时刻"。
- 要真正做到锁后且省往返：把时间折进锁之后的写语句（如
  `UPDATE conversation … RETURNING last_seq, statement_timestamp()`），
  再把执行权/期限校验放到写之后、commit 之前，失败即回滚。这样语义是锁后，且少一次
  SELECT。落地前必须补"持锁跨租约边界"的竞争测试（B-235 口径）。
- 结论：**P1-1 不是"直接合并"就能做**；先修 `_persist_event` 的锁前取时，再评估
  折进写语句。收益是每事件少 1–2 次往返，属于批量化之后的第二刀。

#### P1-2 副本不均是实测事实，不是噪声

25/50/100 三档都复现：一个 runtime 副本忙（p90 36%/67%/98.5%），另一个几乎空闲
（p90 1.8%/2.7%/37.7%）。这意味着：

- 本文所有"单进程上限"数字按**最忙副本**读，已经是保守口径；
- 1000 用户按副本平均分摊会高估容量，必须先修网关/连接层的均衡（或确认 smoke 的
  会话分配是测试脚本特性，而非生产行为）；
- 在均衡修好前，容量规划按"最忙副本"算，不按平均值算。

#### 本轮对 §7 的修订

1. **事件批量写升为第一刀**（不是 chunk 管线，也不是轮询）：目标是每事件语句数/往返数
   和 CPU，跨 Run 按 conversation id 排序加锁；批边界与部分失败语义见 §0.7。
2. **`SharedSettings()` 进程级缓存**仍是最便宜的一刀（2.96ms × 每轮约 10 处）。
3. **多 worker/多副本**与批量化并行推进：单进程 60–70 流饱和，1000 用户必须 6–8×
   进程数或 6–8× CPU 降幅；两者都要，不能只做一项。
4. **chunk 管线瘦身**：本轮证否，除非后续出现"高 chunk 率 + 低用户数"的新证据。
5. **轮询改事件驱动**：证否（CPU 无收益）。
6. **`database_now` 合并**：降级为批量化之后的第二刀，且必须先解决锁前取时语义。
7. **通知/Console**：新增 1000 用户前置项（100 用户 P95 已 1s、Console p90 27.5%）。

复现数据：`/tmp/p0-2-metrics-{25,50,100}.json`、`/tmp/p0-2-cpu-{25,50,100}.log`、
`/tmp/p0-3-metrics-50.json`、`/tmp/p0-3-cpu-50.log`、`/tmp/p0-4-metrics-100.json`、
`/tmp/chunk-bench.json`。脚本：`scripts/capacity/chunk_bench.py`、`smoke_report.py`；
用法见 `scripts/capacity/PLAYBOOK.md`。

### 0.9 Settings 缓存落地与第二层根因（2026-10-27 傍晚）

#### 单因素 A/B：只改 Settings，100 用户 latency 降 2.5 倍

`SharedSettings()` 每次构造都会重新读环境与 `.env`（实测 1.5–3ms，57 个调用点）。先用
sitecustomize 做“进程级缓存”单因素 A/B，再落到生产代码（Runtime 热路径 + Console
security/internal_artifacts）：

| 指标（100 用户 smoke） | 修复前 | 全局缓存 A/B | 生产代码（两次） |
|---|---:|---:|---:|
| admission P95 | 2250–2367ms | **760ms** | **899–997ms** |
| first-delta P95 | 7533–7805ms | **3184ms** | **4064–4466ms** |
| 通知 P95 | 960–1054ms | 363ms | 295–384ms |
| 最忙 runtime p90 CPU | 98.5% | 94.7% | 91.8–94.9% |
| idle-in-tx 峰值 | 30–38 | 26 | 26 |

- admission 已回到 S-16 阈值内（<1000ms）；first-delta 仍超 2500ms；idle-in-tx 仍超 20。
- 全局 A/B 是单次运行，生产代码是两次；两者都远好于修复前，差异按运行波动 + 尚未覆盖的
  Console/Gateway 调用点解释，不把 3184ms 当成稳定目标。
- 全量 `tests/agent_runtime`：**1084 passed**；`process_settings` 契约测试 2 passed。

#### 带修复后的第二层 profile（100 用户，5s 窗口，最忙进程）

> profiler 会扰动绝对值（本次 profile 的 admission 被拖到 2.9s），只看**相对排序**。

| 热点 | 占窗口 CPU | 性质 |
|---|---:|---|
| SQLAlchemy `Session.scalar/execute/commit/close`（未归因分支） | ~18% | 每事件/每查询的 async/greenlet 管道 |
| 事件持久化路径（`_persist_event`/`lock_execution`/`EventWriter.append`/`database_now`） | ~5–8% | 每事件约 7 条语句/往返 |
| `EventTailHub._read_round`（watermark + page） | 4.4% | **每流**的追尾读循环 |
| `_poll_cancel`（250ms/流） | 2.8% | **每流**的取消探测定时器 |
| SSE `_pump` / `StreamingResponse` | 1.5% | **每流**的帧产出 |
| `StateGraph.compile`（每 Run 编译 LangGraph） | 1.8% | 每 Run 的框架固定开销 |
| `ModelDispatchStore.database_clock` | 3.4% | 每次模型调用一次 PG 时钟 SELECT |
| 模型调用路径（candidates/reserve/credentials） | 11.1% | 每次调用的容量/凭据控制面 |
| 受理（`_create_run` 等） | 9.7% | 每 Run 的写事务 |

#### 关键的 scaling 事实：100 用户不是 1000 用户的纯比例代理

| 维度 | 100 用户 smoke | 1000 用户目标 | 倍数 |
|---|---:|---:|---:|
| turns/s | 10 | 13.3 | 1.33× |
| chunks/s | ~2000 | ~2667 | 1.33× |
| 事件/s | ~340 | ~450 | 1.33× |
| **并发 SSE/追尾/取消探测** | **100** | **1000** | **10×** |

所以：**吞吐只涨 33%，但每流成本涨 10 倍**。100 用户时 tail 4.4% + cancel 2.8% +
SSE 1.5% ≈ 9%；到 1000 用户，仅这三项就接近 **90% 单核**，还没算事件写和模型调用。
这是第二层根因，也是“多副本必须”之外必须改代码的部分。

#### 代码裁决

已完成（2026-10-27）：

- `packages/common/src/muad_common/settings.py`：新增 `process_settings()` /
  `reset_settings_cache()`；`SharedSettings()` 保持“每次重读”，供测试/CLI。
- Runtime 热路径：`api/deps.py`、`api/runs.py`、`run_service.py`、`executor.py`、
  `event_tail_hub.py`、`memory_job_enqueue.py`、`model_capacity_service.py`、
  `skill_tools.py`、`materialization.py`、`infrastructure/db.py`。
- Console：`api/security.py`、`api/internal_artifacts.py`。
- `tests/conftest.py`：monkeypatch env 后自动失效缓存；新增
  `tests/contracts/test_shared_settings_cache.py`。

下一刀（按 1000 用户收益排序）：

1. **每流成本合并（连接扇出）**
   - 追尾：把“每流一个 read round”改成进程级共享轮询/按会话合并，Redis 唤醒命中时
     只读一次 watermark；目标 tail CPU 4.4% → <1%（1000 流时 <10%）。
   - 取消：去掉每 Run 250ms 的 `_poll_cancel` 定时器，改成共享取消订阅/Redis pubsub，
     或至少按进程批量探测；目标 2.8% → <0.5%。
   - SSE：帧只编码一次，避免每流重复的 Pydantic/JSON 投影。
2. **事件 group commit（吞吐）**
   - 进程级 batcher（10–20ms 窗口），一批事件一个事务；seq 仍按 conversation 单调；
     锁按 conversation id 排序；失败整批回滚并让等待的 Run 显式失败。
   - 目标：每事件语句数 7 → <1，事件路径 CPU 降 50%，同时减少 tail 唤醒次数。
3. **每 Run/每调用固定开销**
   - `StateGraph.compile` 按 Agent 快照缓存（1.8% → 接近 0）；
   - `ModelDispatchStore.database_clock` 合并/缓存（3.4%）；
   - 受理 `find_replay_in` / `_create_run` 的重复查询合并（9.7%）。
4. **多 worker/多副本 + leader election**（连接扇出的最终解，不能只靠单进程优化）。
5. 复测 100/200 → staging 1000；每步报告同口径五项。

### 0.10 1000 用户复测：Runtime 不再是墙，Console 单 Pod 是（2026-10-27 夜）

#### 先修验收栈：`replica_counts` 之前只是故障替换槽

本地栈的入口代理/`AGENT_RUNTIME_URL` 都钉在 0 号实例，8 个 runtime 实际只有 1 个接流量。
第一次 1000 复测因此出现 782 个 `CONVERSATION_REJECTED`——那是单进程准入队列溢出，不是
容量结论。已给 `tests/acceptance/capacity/stack.py` 加真实的 **runtime round-robin 池**
（`--default-unit agent-runtime`），Gateway/Console/Worker 统一经它访问 Runtime；入口代理
也改为对 gateway 副本轮询。**此前所有“副本不均”读数作废，按本次重测为准。**

#### 100 用户（2 runtime 副本真正分摊）

| 指标 | P50 | P95 | P99 |
|---|---:|---:|---:|
| admission | 68ms | **641ms** | **749ms** |
| first-delta | 277ms | **1879ms** | **2061ms** |
| 通知 | 45ms | 243ms | 298ms |

- CPU：两个 runtime p90 **59.2% / 74.7%**（此前一个 87.7%、一个空闲）。
- 唯一失败项：`max_idle_in_tx=39`（阈值 20）；admission/first-delta 均已进 smoke SLO。
- 结论：Settings 缓存 + 每流合并 + 真正分摊后，**100 用户档的 Runtime 吞吐已达标**。

#### 1000 用户（8 runtime + PgBouncer + 2000 模型槽 + 1 Console）

按 8 轮/600s 的 `test_capacity.py` 跑，driver 在 149s 内因失败提前结束：

| 指标 | 值 |
|---|---:|
| attempts | 4899（计划 8000，提前失败） |
| completed | 756 |
| admission P95 / P99 | **7519ms / 9469ms** |
| first-delta P95 / P99 | **8975ms / 11807ms** |
| 通知 P95 / P99 | **7314ms / 9062ms** |
| 系统错误率 | 19.6% |
| 失败账目 | ADMISSION_REJECTED 3185 / CONVERSATION_REJECTED 443 / SSE_REJECTED 516 |

各单元 CPU（负载窗口）：

| 单元 | max | p90 |
|---|---:|---:|
| **console-platform（1 副本）** | **98.4%** | **97.0%** |
| im-gateway（2 副本） | 97.0% / 96.8% | 47.8% / 48.5% |
| agent-runtime（8 副本） | 65–83% | **45–58%** |
| agent-worker（4 副本） | 2.7–64.6% | ≤1.5% |

**根因**：Runtime 已经分摊且未饱和（p90 ≤58%），Gateway 也还有余量（p90 ~48%），
**Console 单 Pod p90 97% 打满**。1000 用户需要 1000 次会话创建 + 持续鉴权/通知/平台设置
读取；Console 是单进程单副本，CPU 先到顶，随后准入/会话/通知一起超时，表现为
`ADMISSION_REJECTED`/`CONVERSATION_REJECTED`/`SSE_REJECTED`。

**口径**：这是一次**失败运行**的 P95/P99（幸存者偏差），只能用于定位瓶颈，不能写成
“1000 用户容量结论”。1000 的正向结论仍由 staging E-09 裁决。

### 0.11 Stage 1：Console 单 Pod × 4 worker 后的 1000 稳态（2026-10-27 夜）

配置：**Console 1 Pod × 4 worker**（uvicorn `--workers 4` + `PROMETHEUS_MULTIPROC_DIR`）、
8 runtime 副本（真实轮询）、PgBouncer、2000 模型槽；跑 8 轮/600s。driver 在 501s 结束。

| 指标 | Stage 1 | 1 Console 单 worker（§0.10） |
|---|---:|---:|
| attempts / admitted / completed | 5984 / 5696 / **5331** | 4899 / 4456 / 756 |
| admission P95 / P99 | 14592ms / 17848ms | 7519ms / 9469ms |
| first-delta P95 / P99 | 32550ms / 37057ms | 8975ms / 11807ms |
| 通知 P95 / P99 | 14507ms / 16613ms | 7314ms / 9062ms |
| 系统错误率 | **8.56%** | 19.6% |
| 失败账目 | ADMISSION 158 / CONVERSATION 288 / SSE 205 / RUN_FAILED 16 / SSE_TIMEOUT 3 | ADMISSION 3185 / CONVERSATION 443 / SSE 516 |

Console 请求账目（501s）：

| 端点 | 请求数 | QPS |
|---|---:|---:|
| `chat-principal` | 18880 | **37.7/s** |
| `admission-materials` | 5543 | 11.1/s |
| `resolve-credentials` | 5757 | 11.5/s |
| `platform-settings` | 1890 | 3.8/s |
| `resolve-definition` | 713 | 1.4/s |
| 合计 | ~32783 | **~65/s** |

- **Console 不再是墙**：4 worker 正常出数（`muad_process_up` 见到 4 个 worker），
  请求分布到 4 个进程；没有 Console 超时/500 主导的失败。
- **墙转移到 PG**：`max_connections_total=90`（本机上限 100）、`idle-in-tx=88`、
  commits/s **323.7**、event rows/s **105.9**；`run_persist_event_seconds_sum=873s`，
  `run_consume_seconds{stage=persist}` 是最大分段之一。
- Runtime 8 副本 p90 **65–67%**，Gateway 2 副本 p90 **45–47%**；CPU 不是唯一限制，
  但 PG 连接/事务排队已经把 first-delta 推到 32s。
- 失败里有 158 `RUN_BUSY`（上一轮未终态、下一轮已提交）和 288 `CONVERSATION_REJECTED`；
  这是“执行慢导致轮次重叠”的次生现象，不是 Console 计算不够。

**结论**：Stage 1 让完成数从 756 → **5331**、系统错误 19.6% → **8.56%**，方向正确；
但 1000 稳态仍未达标，**下一步不是继续加 Console worker，而是事件 group commit
（把每事件 7 条语句/往返压成批）+ 可选的通知/chat-principal 缓存降 QPS**。
Console 4 worker 保留，作为 Stage 2 的基线。

### 0.12 事件 group commit（实现完成，待 1000 复测）

`RunService._persist_event` 不再每事件开事务，改为提交给进程级 `EventBatcher`：

- **10ms / 32 条**窗口，跨 Run 合并；`submit` 返回 Future，COMMIT 后才完成；
- 按 conversation → run → continuation 排序加锁；锁后取一次 PG 时间校验 lease/epoch/deadline；
- **Plan B 故障隔离**：失效 Run/缺失会话只失败自己的 Future，其余事件照常提交；
- 每会话一次 `last_seq += N RETURNING` 分配 seq 区间；一次批量 INSERT + 一次 COMMIT；
- 提交成功后才执行 `on_commit`（tail 唤醒）；数据库错误才整批回滚；
- 有界队列背压；应用 shutdown drain；`source_event_id` 幂等仍生效。

预期：每事件语句数 7–8 → **<1（摊薄）**，commits 324/s → **50–100/s**，
idle-in-tx 88 → **<20**，PG 连接 90 → **30–50**。能否把 first-delta 从 32s 拉到
1.2s 取决于 admission/Console 是否成为下一墙，必须用同一 Stage 1 拓扑复测。

### 0.13 事件 group commit 复测（10ms/32，1000 稳态）

实现已落地并通过 **1085 个 runtime 用例**；修复了一个关键装配缺陷：batcher 之前直接取
`infrastructure.db.get_session_factory`，绕过测试/装配根注入的隔离工厂，导致
`test_memory_redesign_confirmation` 60s 挂起。现在 `RunService._persist_event` 把模块级
`get_session_factory` 传给 batcher。

1000 稳态（1 Console×4 worker + 8 runtime + PgBouncer + 2000 模型槽）：

| 指标 | Stage 1 | Stage 1 + 事件 batch（10ms/32） |
|---|---:|---:|
| attempts / completed | 5984 / 5331 | 6229 / **5708** |
| 系统错误率 | 8.56% | **7.26%** |
| admission P95 / P99 | 14592 / 17848ms | 14868 / 18396ms |
| first-delta P95 / P99 | 32550 / 37057ms | 33292 / 38055ms |
| 通知 P95 / P99 | 14507 / 16613ms | 15031 / 17143ms |
| PG connections / idle-in-tx | 90 / 88 | **90 / 87** |
| commits/s · event rows/s | 324 · 106 | **200 · 64** |
| 单事件持久化均值（单副本样本） | ~118ms | **~564ms** |

**裁决**：10ms/32 的 per-process batch **没有解决 PG 墙**：

- commits 324 → 200 是真实下降，但 idle-in-tx 仍 87、连接仍 90；
- 8 个 runtime 进程分摊后每进程约 50–60 events/s，10ms 窗口平均只能凑到 **<1 条**，
  批量退化为“单条事务 + 额外锁查询”，所以延迟没有改善，单事件墙钟反而更长；
- admission/first-delta 仍由 PG 排队主导；Console 4 worker 仍不是墙（gateway p90 33–34%）。

**下一刀（按这个结果）**：

1. **放大批窗口并保留首事件快路径**：后续事件 50–100ms 窗口（首批/首 delta 立即 flush），
   让每批稳定到 3–6 条；或者把 writer 提升到 **Pod 级**（同 Pod 多 worker 共享一个 writer），
   把每批事件率乘 worker 数。
2. **减少每事件锁语句**：批量锁查询已合并；下一步把 `database_now`/lease 校验折进批量写，
   或按 conversation 分组一次 `UPDATE ... FROM (VALUES)`。
3. **同时看非事件事务**：idle-in-tx 87 不一定全是事件写；admission/finalize/模型容量也在
   同一 PG 连接池里。需要按事务类型拆分 idle-in-tx 归因，再决定是否继续只改事件路径。

### 0.14 事件 batch v2：100ms + 首事件 fast path（已实现，待 1000 复测）

- 窗口 10ms → **100ms**；`RUN_CREATED` 与每个 segment 的**首条 `message.delta`** 带
  `flush_now=True`，入队即结束当前批，首字延迟不增加。
- 新增指标：`event_batch_size`（最近一批大小）、`event_batch_flush_seconds`、
  `event_batch_wait_seconds`（submit→commit）、`event_batch_events_total`。
- 目标单测 48 passed；全量 runtime 回归待跑；随后用同一 Stage 1 拓扑复测 1000 稳态，
  重点看 `event_batch_size` 是否稳定到 3–6、PG commits/idle-in-tx 是否下降。
- 若 100ms 后 batch size 仍 <2，再评估 Pod 级 writer 或按 conversation 分片的 writer；
  1 worker/Pod 拓扑下 Pod 级 writer 无收益。

### 0.15 事件 batch 分段测量：瓶颈是锁，不是 writer 数量（2026-10-27 深夜）

用同吞吐短场景（1000 用户 × 4 轮 / 300s ≈ 13.3 turns/s）做 10ms vs 100ms A/B：

| 指标 | 10ms | 100ms |
|---|---:|---:|
| completed / attempts | 2895 / 3253 | 2825 / 3220 |
| 系统错误率 | 9.07% | 10.84% |
| admission P95 / P99 | 12403 / 15798ms | 12311 / 16036ms |
| first-delta P95 / P99 | 27199 / 30679ms | 26226 / 30515ms |
| PG connections / idle-in-tx | 89 / 86 | 89 / 87 |
| commits/s · event rows/s | 328 · 107 | 326 · 110 |
| 平均 batch size（events/flush） | 2.9 | 3.5 |

flush 分段（单副本样本，均值）：

| 分段 | 10ms | 100ms | 占 flush |
|---|---:|---:|---:|
| **lock（会话/Run/continuation 行锁）** | **111.6ms** | **151.4ms** | **74–80%** |
| PG clock | 5.5ms | 4.0ms | 2–4% |
| seq 分配 | 16.2ms | 15.2ms | 8–11% |
| INSERT/flush | 12.6ms | 13.5ms | 7–8% |
| COMMIT | 3.9ms | 4.0ms | 2% |
| flush 合计 | 151.3ms | 189.8ms | 100% |
| 队列等待 | 379ms | 772ms | — |

lease renew：

| 指标 | 10ms | 100ms |
|---|---:|---:|
| renew 均值 | 225ms | **339ms** |
| renew 失败 | 6 | 5 |

**裁决**：

- `flush 265ms` 的主因是 **行锁等待（74–80%）**，不是 INSERT/COMMIT，也不是 writer CPU。
- 100ms 让 batch 从 2.9 → 3.5，但锁等待从 111.6 → 151.4ms、队列等待 379 → 772ms；
  **更大窗口在锁竞争下是负收益**。
- **sharded writer / Pod 级 writer 现在不是答案**：更多 writer 只会增加同一批
  conversation/run 行锁的并发竞争。此前的“单 writer 吞吐不足”假设被数据推翻。
- lease renew 225–339ms，说明 renew 与事件 batch 在 **run 行锁**上互相等待；
  100ms 把 renew 拉长 50%，与 8 轮/600s 的 `RUN_ABANDONED` 方向一致，但短场景未复现 407。

**锁分段补充（2026-10-28，1000×4/300，10ms）**：

| 锁 | 均值 | 占 lock |
|---|---:|---:|
| **conversation 行锁** | **125.0ms** | **90%** |
| run 行锁 | 6.9ms | 5% |
| continuation 行锁 | 6.6ms | 5% |
| lock 合计 | 138.5ms | 100% |

同期 `run_finalize_seconds` 均值 **378ms**（417 次），admission `write` 均值 **202ms**；
renew 均值 **389ms**。这些都是系统错误率 9–11%、轮次重叠的拥塞态墙钟，**不能直接当成
内在成本**。同会话占用率算术上不支持 121–297ms 的同会话锁等待；更可信的机制是
`EventBatcher._lock_sources` 在一个事务里锁住整批 conversation/run，等待最慢会话时
其余会话已被锁住（跨会话队头阻塞）。`event_batch_conversations` 只有 last gauge，
必须先补平均值/分布再归因。`SKIP LOCKED` 需要按“整会话 requeue 保序”设计，不是简单兜底。

**finalize 分段（2026-10-28，1000×4/300，10ms，408 次）**：

| 阶段 | 均值 | 说明 |
|---|---:|---|
| **lock（等待 conversation/run 锁 + PG 时钟）** | **297ms** | 占 finalize 70%，是**等待**不是持锁 |
| notification | 32ms | 持锁期内 |
| memory enqueue | 23ms | 持锁期内 |
| release admission | 23ms | 持锁期内 |
| events（ASSISTANT + RUN_COMPLETED） | 15ms | 持锁期内 |
| submission finalize | 9ms | 持锁期内 |
| run CAS / activity / commit | 7 / 6 / 5ms | 持锁期内 |
| finalize 合计 | **423ms** | 锁后实际持锁约 **126ms** |

事件 batch 的 conversation 锁等待同期 **121ms/次**。结论：

- finalize 的 423ms 里 **297ms 是在排队等锁**，不是通知/memory 写不动；
- 锁后持锁约 126ms，其中 notification+memory+activity ≈ 61ms，可以移出终态事务；
- 但只移副作用最多把持锁从 126ms 降到 ~65ms，**不能单独解释/消除 297ms 等待**；
- 另一个主要持锁者是 admission `write=202ms`，需要同样的分段。

**admission `_create_run` 分段（2026-10-28，1000×4/300，10ms，455 次）**：

| 阶段 | 均值 |
|---|---:|
| counts（USER 计数行 + `count_open_runs` O(租户 Run 数) 实时 count） | **41.5ms** |
| memory inbound | 33.7ms |
| canonical events（RUN_CREATED + stage） | 30.4ms |
| policy（upsert） | 12.6ms |
| Run/Snapshot inserts | 12.5ms |
| submission record | 12.3ms |
| first_stmt / commit | 5.2 / 5.2ms |
| wake tail | 4.3ms |
| attachments / conversation | 0.3 / 0.2ms |
| 合计 | **~159ms** |

**综合结论（2026-10-28 修订）**：这些墙钟数字来自系统错误率 9–11%、first-delta 26–30s
的拥塞/轮次重叠样本，**不能把每个分段直接当成内在 CPU 成本**；它们主要是排队时间。
因此“移走 61ms 就能改善 297ms 等待”不成立。

更可信的耦合机制是 **EventBatcher 的跨会话队头阻塞**：`_lock_sources` 在一个事务里锁住
一批的全部 conversation → run → continuation，持有到 COMMIT；一批 2.9–3.5 条事件，
flush 本身 ~150ms。它等最慢的会话时，同批其他会话已被锁住，落到这些会话上的
admission/finalize 只能排队。`event_batch_conversations` 目前只有 **last gauge**，
文档从未报告平均值/分布——必须先补这个口径。

**counts 标签修正**：2026-10-20 起租户计数行锁已删除（`run_admission.check_counts_in`
docstring 明确原因是“把 1000 个用户串成一条全局队列”）。现在 `reserve_counts_in` 是
**每用户计数行（1000 个独立用户，无跨用户竞争）+ `count_open_runs` 对 run_record 的
O(租户全部 Run) 实时 count**。41.5ms 更可能是后者随历史增长，不是“用户计数锁”。
“先锁计数行、后会话/事件”的重排会与 finalize 的 Conversation→Run→USER 计数锁序形成
ABBA 死锁，**不重排**。

**修正后的测量顺序**（先归因，不改结构）：

0. 归因收口：`event_batch_conversations` 平均值/分布、pg_stat_statements top20、
   pg_stat_activity wait_event 采样、200 用户非拥塞参照。
1. 事件 batch 按会话分组提交，或 `SKIP LOCKED` + 整会话 requeue（保序）；直击跨会话
   队头阻塞。
2. 收窄 admission 临界区：USER 计数改单条 upsert/条件更新；`count_open_runs` 改有界
   存在性检查 + 部分索引；去掉 append_many 对已持有会话锁的重复 `FOR UPDATE`。
3. 收窄 finalize：先砍语句（activity 并入 seq UPDATE、memory COUNT 换水位、通知语句合并），
   不先建 outbox。
4. 上述仍不足，才考虑 outbox dispatcher；验收取失败率 <2% 的窗口。



#### 对 1000 目标架构的修订

- **Console 不能再是单 Pod 容量口径**：profile 的
  `max_replica_counts.console-platform=1` 与 1000 用户目标冲突，必须改为多 worker/多副本，
  或把鉴权/通知/平台设置读路径拆出独立进程。先做哪条：
  1. Console 多 uvicorn worker（`process_counts>1`）验证 2/4 worker 是否近线性；
  2. 若 in-memory 缓存/后台循环阻碍多 worker，再拆读路径或加副本。
- Runtime 的下一刀仍是事件 group commit（100 用户 p90 59–75%，1000 用户 p90 45–58%，
  但事件吞吐只 1.33×；批量化是为 staging 留余量，不是当前 1000 的墙）。
- Gateway p90 ~48% 在 1000 下仍安全；2 副本暂够，但 SSE 连接数与 FD 预算要按 1000 重算。
- Console 扩完后再跑同一 1000 场景；只有 Console p90 <70% 且失败账目清零，才谈 SLO。



### 0.16 测量优先与决策记录（2026-10-27 起）

> 以后所有性能问题都按本节流程：**先测量、再结论、后方案**；没有 before/after 数字不写成
> 结论，负收益也要记录并回退默认值。一次只改一个变量；同场景、同拓扑、同 profile hash。

#### 已采纳

| 变更 | 证据 | 结论 |
|---|---|---|
| `process_settings()` 进程级缓存 | 100 用户 admission P95 2.3s→0.90–1.00s；runtime 1085 tests green | 采纳 |
| 每流成本合并 v1：tail 批量补查 + cancel 0.25s→1s | 100 用户真正 2 副本分摊：admission 641ms、first-delta 1879ms | 采纳 |
| Console 单 Pod × 4 worker | 1000 完成 756→5331，错误率 19.6%→8.56% | 采纳（生产清单待接线） |
| 验收栈 round-robin pool | 此前 8 副本只有 1 个接流量，出现 782 个 `CONVERSATION_REJECTED` | 采纳；旧“副本不均”读数作废 |
| event batcher 核心（跨 Run/Plan B/单 COMMIT/幂等） | 1085 tests green；batch 2.9–3.8 条/批 | 保留；默认窗口 10ms |

#### 已证否 / 回退

| 变更 | 证据 | 结论 |
|---|---|---|
| 100ms 事件窗口 | 同吞吐 A/B：batch 3.5，但 lock 111.6→151.4ms、queue 379→772ms；8 轮/600s 出现 407 `RUN_ABANDONED` | **回退 10ms 默认** |
| 更大 batch = 更好 | flush 中 lock 74–80%，INSERT/COMMIT 仅 ~10% | 锁竞争下更大 batch 负收益 |
| sharded / Pod 级 writer（当前阶段） | 锁等待主导；更多 writer 只会增加同一批行锁竞争 | **暂不实施** |
| 每 chunk 管线瘦身 | 1.3ms/轮，最坏 8ms/轮 | 不优先 |
| `QUEUE_POLL_SEC` 0.05→0.5 | CPU 88.2%→88.3%，延迟略升 | 不采纳 |
| 删 release 路径 `ensure_capacity_row`（Batch A） | 同 fast 300s A/B：容量行 SQL 60.0→88.1ms/次调用，完成 2583→2494 | **回退**；`transactionid` 等待是排队节流点，不是冗余 |
| renew 批量化（Batch C：500ms 窗口、7.4 行/批、一条 `UPDATE ... FROM VALUES`） | 同 fast 300s A/B：完成 2583→2010、错误 13.5%→22.5%、SSE 拒绝 89→727 | **回退**；多行 run 锁与 SSE/事件路径 convoy（见 §0.19） |
| 加大 pool / PgBouncer / 关 sync_commit | 100 用户 A/B 无改善；大池更差 | 不作为第一解 |

#### 待验证（不是方案）

| 假设 | 下一步测量 | 采纳条件 | 否决条件 |
|---|---|---|---|
| **EventBatcher 跨会话队头阻塞是主耦合** | 报 `event_batch_conversations`/runs 平均值与分布；200 用户非拥塞参照；pg_stat_statements top20、wait_event 采样 | 平均同批会话 >1.5 且 HOL 与 lock wait 相关 | 批内基本单会话，或 lock wait 与批大小无关 |
| 按会话分组提交 / `SKIP LOCKED` + 整会话 requeue（保序） | 同 1000×4/300 A/B | lock wait 降 ≥30%，无顺序/丢事件/重试风暴 | 顺序错乱/重试放大 |
| admission `count_open_runs` 是 41.5ms 的主因（O(租户 Run 数) 无索引 count） | 200 参照 + pg_stat_statements；有界存在性检查 + 部分索引 | count 语句时间显著下降 | 时间不在该 count |
| admission USER 计数改单条 upsert/条件更新 | 同场景 A/B | 少 2–3 条语句且原子性不变 | 语义/竞争回归 |
| finalize 先砍语句（activity 并入 seq UPDATE、memory COUNT 换水位、通知合并） | 同场景 A/B | hold 126ms 下降且无需新表 | 语义/一致性回归 |
| `SKIP LOCKED` 小 `lock_timeout`（兜底，非第一解） | 锁分段/批会话归因之后 | 队列等待下降，失败不增加 | 重试风暴/顺序问题 |
| lease renew 独立连接/优先通道 | 同场景 A/B | renew p95 与 `RUN_ABANDONED` 下降 | 无变化或连接预算恶化 |
| outbox dispatcher（最后） | 失败率 <2% 窗口 | 证明前面的语句削减仍不足 | 对 lock wait 无感 |

> **不要**把“移 61ms 副作用”当成能解释 297ms 等待的处方；先证明瓶颈是持锁时长还是
> 跨会话队头阻塞。`counts` 不是“先锁计数行后会话”的重排，租户计数行锁已删除；重排会
> 与 finalize 的 Conversation→Run→USER 锁序形成 ABBA 死锁。

**标准口径**：快速 A/B 用 `1000×4/300s ≈ 13.3 turns/s`；正式验收用 `1000×8/600s`。
必报 P50/P95/P99、每进程 CPU、PG connections/idle-in-tx/commits、batch 分段
（lock/clock/seq/insert/commit/queue）、`run_lease_renew_seconds`、`RUN_ABANDONED`、失败账目。

**当前一句话**：1000 稳态的绑定资源是 PG 行锁/事务，不是 writer 数量，也不是某个服务 Pod 数；
任何 writer 扩展都要等锁等待降下来后再评估。

**测量纪律（2026-10-28 追加）**：先怀疑测量，再怀疑代码；先测内在成本，再谈热行。
本轮所有“改配置”都是非单调的（1→4→8 keys、88→500 池、加 Pod）——真正的收益来自
去掉串行化（settings 缓存、Console 多 worker、事件合并）和修正测量（lease 节拍）。
任何容量结论同时报测试节拍与生产节拍（§0.20）。


### 0.17 pg_stat_statements 归因：真正的大头是 model capacity/dispatch（2026-10-28）

200 用户非拥塞参照（200 完成，PG 9 连接、idle-in-tx 4）：

| 语句（200 参照） | calls | mean |
|---|---:|---:|
| lease 续租 UPDATE | 106 | 4.08ms |
| canonical_event INSERT | 2064 | **0.04ms** |
| conversation last_seq UPDATE | 1860 | **0.03ms** |
| snapshot INSERT | 204 | 0.11ms |
| notification INSERT | 204 | 0.10ms |
| admission counter upsert | 408 | 0.04ms |
| conversation 锁查询 | 1025 | 0.02ms |

1000×8/600 稳态 `pg_stat_statements` top（wall 900s，负载窗口，fixture 后 reset）。
这是 **steady 口径**：绝对值不能与 fast 300s 的 pgss 直接对比，比例/排序才可跨口径参考：

| 语句 | calls | total | mean |
|---|---:|---:|---:|
| `UPDATE run_record SET lease_until...` | 15914 | 87.5s | 5.50ms |
| `INSERT model_capacity ... ON CONFLICT` | 6008 | 86.6s | **14.41ms** |
| model dispatch CTE（eligible/picked） | 3002 | 52.2s | **17.40ms** |
| `UPDATE model_capacity SET inflight +` | 3004 | 36.9s | 12.27ms |
| `UPDATE model_capacity SET inflight = GREATEST(...)` | 3004 | 31.1s | 10.37ms |
| canonical_event SELECT | 2991 | 4.8s | 1.61ms |
| **canonical_event INSERT** | 29936 | **2.65s** | **0.09ms** |
| `UPDATE conversation last_seq` | 26945 | 2.39s | 0.09ms |
| `count_open_runs`（O(租户 Run) count） | 2991 | 1.97s | **0.66ms** |
| `count canonical_event`（memory consolidation enqueue） | 853 | 3.71s | 4.34ms |

> 上表的 `count canonical_event` 是 **memory consolidation enqueue**
> （`memory_job_enqueue._insert`，finalize 路径），不是 §0.23 的 admission `memory`
> 阶段（`append_inbound`）；两者不要混为一谈。

`model_call_seconds`（单副本样本）：reserve 419ms、candidates 163ms、credentials 37ms、
upstream 1768ms；reserve/candidates 是模型调用固定开销的大头。

**裁决**：

- 200 参照证明：无拥塞时事件 INSERT/会话 UPDATE/计数 upsert 都是 **0.02–0.11ms**；
  1000 下的 41.5ms/32ms 是排队，不是内在成本（DeepSeek 的批评成立）。
- 事件 batch 跨会话队头阻塞是**真实**的：532 次 flush，平均 **2.77 个会话/批**，
  52% 的批次 >1 会话（1:254 / 2–3:171 / 4+:107）；但它主要是**放大器**。
- **真正占用 SQL 时间的是 model capacity/dispatch**：约 207s 总执行时间
  （upsert 86.6 + dispatch 52.2 + 两条 inflight 更新 68.0），是事件 INSERT+UPDATE
  总和的 ~40 倍；lease 续租 87.5s 次之。
- 因此第一刀是 **model_capacity 热行触碰次数**，不是先合并 `_select`/`reserve`。
  按风险递增、每步单独 A/B（**2026-10-28 修订：第 1 项实测负收益，已回退，见 §0.18；
  当前下一刀改为第 3 项 renew 批量化**）：
  1. ~~release 路径直接删 `ensure_capacity_row`~~（**已实测否证**：同 fast 300s A/B 下
     容量行 SQL 60.0→88.1ms/次调用、完成 2583→2494；删掉后 release 直接撞 tuple 锁，
     单次等待反而翻倍。`transactionid` 等待是排队节流点，不能当冗余删）。
  2. **reserve 的 lazy create**：先条件 UPDATE；0 行时再区分“行不存在（建行重试）”
     和“超限（拒绝）”。必须测“缺行 ≠ 超限”，不能把首次调用误报
     `MODEL_CAPACITY_UNAVAILABLE`/`WAITING_MODEL`。**注意**：它同样会删掉 reserve 的
     `transactionid` 排队点，必须先证明有替代节流或 A/B 无回归；否则不做。
  3. ~~**renew 批量化**~~（**已实测否证**，见 §0.19：多行 run 锁与 SSE/事件路径的
     run 源锁 convoy，完成 2583→2010、SSE 拒绝 89→727；逐 Run 续租保留）。
     若仍要批量化，只能用 `SKIP LOCKED` + 被跳过行立即重试，或把续租并进已持有该 run 锁
     的事件批事务；两者都必须单独 A/B。
  4. **release 批量化**：按“N 个租约的状态推进 + 计数递减，一个事务”实现，
     不能先提交 SETTLED 再延迟递减（会永久泄漏容量）；但它同样持有多行 capacity 热行锁，
     **优先级下调**，先证明不与 SSE/事件/finalize 的锁等待形成 convoy。
  5. **limits 缓存直接跳过**：`read_limits` 是索引行读、无竞争；缓存还会改变
     `MODEL_DISABLED` 立即生效语义。clock 不缓存（B-235）；SQL 内 `now()` 可最后再说。
  6. **合并 `_select` + `reserve` 往后放**：dispatch 行锁会跨到 capacity 行等待，
     可能加重队头阻塞；单独 A/B 并量 lock hold/拒绝率（`_pick_with_contention` 超时会
     抛 `MODEL_CAPACITY_UNAVAILABLE`）。
  7. **dispatch state 缓存慎重**：只缓存候选集合/权重等事实，计数语句保留；
     缓存 stale 会把调用打到已停用成员。
- 测量口径：`model_call_seconds{stage="reserve"}` 目前包含 `gate.hold` 排队；
  需加 `stage="gate"`（或 label）并同时报 `db_pool_checkout`，否则 A/B 可能只是 gate 队列变化。
- 每项 A/B 同时报 `MODEL_CAPACITY_METRIC` 的 outcome 分布
  （RESERVED/REUSED/MODEL_CAPACITY_UNAVAILABLE/MODEL_DISABLED），防止“延迟变好、拒绝率悄悄变”。
- renew 批量化必须加“批量 renew 与 EventBatcher 同锁同一组 run”的死锁测试。
- 每改一项都重新取 pgss top，不能拿这次的排序一次性排完整个计划。
- 以上仍在失败率 10–14% 的拥塞窗口，**只用于归因**；优化后必须在失败率 <2% 的窗口复测。


### 0.18 Batch A 实测：删 release ensure 是负收益（已回退，2026-10-28）

先修正上一轮的测量错误：`attr1000` 是 **steady 600s / 8 轮 / ramp 60s**（wall 900s），
而 Batch A 第一次跑的是 **fast 300s / 4 轮 / ramp 30s**（wall 248s）。拿两者比
“完成 2894 vs 2494、错误率 10.1% vs 14.35%”是无效对比；§0.17 表已改标 steady。
同口径 fast 基线必须重新跑（保留 release ensure 的 B 轮）。

**同 profile A/B（fast 300s/4 轮，8 runtime + PgBouncer + 2000 槽，pgss 单轮窗口）**：

| 指标 | B：保留 ensure | A：删 release ensure | 变化 |
|---|---:|---:|---:|
| `INSERT model_capacity ON CONFLICT` | 5388 次 / 89.4s / 16.6ms | 2595 / 75.0s / 28.9ms | 调用减半，单次 +74% |
| admit UPDATE | 2694 / 38.2s / 14.2ms | 2595 / 75.9s / 29.3ms | +106% |
| release UPDATE | 2694 / 34.1s / 12.7ms | 2595 / 77.6s / 29.9ms | +136% |
| dispatch CTE | 2693 / 12.6s / 4.68ms | 2593 / 23.7s / 9.14ms | +95% |
| 容量行 SQL 合计 / 次调用 | **60.0ms** | **88.1ms** | **+47%** |
| 完成 / 受理 | 2583 / 3046 | 2494 / 2989 | −3.4% / −1.9% |
| 系统错误率 | 13.49% | 14.35% | A 更差 |
| admission P95 / first-delta P95 | 11.75s / 25.83s | 11.45s / 26.40s | 基本持平 |

**机制（`pg_locks` 采样）**：等待类型是 **`transactionid/ShareLock`**，不是 `tuple`；
大多数采样点等待队列为 0（171 次 0 个等待者，1 个 10 次，3 个 5 次，最高一次 25）。
`INSERT ... ON CONFLICT DO NOTHING` 在拿到行锁之前先等 `transactionid`，相当于一个
**公平的排队/节流点**；删掉它以后 release 直接扑到 tuple 锁，单次等待从 12.7ms 涨到
29.9ms，容量行利用率约 65%→92%，整条链路进入 convoy。**“删语句 = 降等待”在单一热行上
不成立。**

**裁决**：

- Batch A 代码与行为测试**已回退**；容量测试 45 passed。
- Batch B（reserve lazy create）**降级为待重估**：它同样会删掉 reserve 的 `transactionid`
  排队点，按本轮证据大概率复现同一 convoy；除非先给出替代节流（例如显式队列/ advisory
  lock）并通过同口径 A/B，否则不做。
- **Batch C（renew 批量化）已试并回退**：多行 run 锁与 SSE/事件路径 convoy，
  完成 2583→2010、SSE 拒绝 89→727（§0.19）。逐 Run 续租保留。
- 下一刀回到 **capacity/dispatch**，并先按生产 lease 节拍 60/20 重跑基线（§0.20）；
  Batch D release 批量化同样持多行 capacity 行锁，优先级下调，先证不 convoy。

**测量口径补充**：

- runtime 的 `/metrics` 是**单 Pod 样本**：`model_call_seconds{reserve}` 323 次 vs pgss
  实际 2595 次 = 8.03×，正好 8 个 runtime 副本；runtime counter/histogram 总量要 ×8。
  Console 的 4 worker 是真正的多进程聚合，不需要乘。
- 失败账目里的 `CONVERSATION_REJECTED`/`RUN_BUSY` 是轮次重叠，不是容量拒绝：
  `model_capacity_total` 只有 `RESERVED`，`WAITING_MODEL` 深度为 0。
- 每项 A/B 仍要同时报 `MODEL_CAPACITY_METRIC` outcome 分布、`model_call_seconds{stage="gate"}`
  和 `db_pool_checkout`；只在延迟上比较会漏掉 convoy 造成的拒绝率变化。


### 0.19 Batch C 实测：续租批量化负收益（已回退，2026-10-28）

同 fast 1000×4/300，B（逐 Run 续租）vs C（500ms 窗口、7.4 行/批、一条
`UPDATE ... FROM (VALUES ...)`、先按 run id 升序 `FOR UPDATE`）：

| 指标 | B：逐 Run | C：批量 |
|---|---:|---:|
| 完成 / 受理 | 2583 / 3046 | 2010 / 3715 |
| 系统错误率 | 13.49% | **22.48%** |
| `ADMISSION_REJECTED` | 57 | **871（500 COMMON_INTERNAL_ERROR）** |
| `SSE_REJECTED` | 89 | **727** |
| 续租事务 | 2664 行/pod，逐条 | 118 批/pod × 7.4 行 |
| 续租批事务 / 入队 / 端到端 | — | 186ms / 475ms / 691ms |
| `run_lease_renew_failures_total` | 8/pod | 0 |
| `event_batch_lock_seconds` | 71.1s | 40.6s |

**根因**：批处理器先 `SELECT ... WHERE id IN (...) ORDER BY id FOR UPDATE` 锁住 7–8 条
run 行，再更新；PG 日志出现 `UPDATE runtime.run_record SET update_time = update_time
WHERE id = $1` 的 **statement timeout**（`while updating tuple ... in relation
"run_record"`）。多行锁把逐 Run 的“单行等待”放大成“持多行等一行”，与 SSE 打开/事件路径
的 run 源锁形成 convoy；871 个 500 与 727 个 SSE 拒绝是这条路径的可见失败。
批量本身不慢（186ms/批），但它破坏了 SSE 打开所需的短锁等待。

**裁决**：撤销 Batch C（代码/测试/指标全部回退）；逐 Run 续租保留。
**不要再对 run 行/capacity 行做“多行 `FOR UPDATE` 批处理”**；要批量化只能用
`SKIP LOCKED` 且被跳过行立即重试，或把续租并进已经持有该 run 锁的事件批事务，
两者都必须单独 A/B。

### 0.20 测量口径修正：容量测试的 lease 节拍是生产的 20 倍

`tests/acceptance/capacity/execution_support.py` 的 `EXECUTION_CADENCE_ENV` 给容量栈
`RUN_LEASE_SEC=5`、`RUN_HEARTBEAT_SEC=1`（生产默认 60/20）。所以 §0.17 的
“lease renew 19113 次 / 151.0s”是 **1s 心跳**下的数字；按 20s 心跳折算约 7.5s，
**不是生产瓶颈**。后续 1000 基线必须同时报两组节拍：

- 测试节拍 5/1：只用于回收/故障用例（S-15），不用于容量归因；
- 生产节拍 60/20：用于容量结论；在 1000 的 p1 plugin 里 patch
  `CapacityStack._service_env_for`，不改生产代码。实测只改 `test_capacity.SERVICE_ENV`
  **不会到达子进程**（200 用户进程 env 仍是 5/1）——必须改真正生成子进程 env 的那一层。

否则会把“压测加速节拍”误当成优化目标——Batch C 就是这么被排到前面的。


### 0.21 D2：生产节拍 60/20 的 1000 fast 基线（2026-10-28）

runtime env 已确认 `RUN_LEASE_SEC=60 RUN_HEARTBEAT_SEC=20`（§0.20 的 plumbing 修正后）。
同 fast 1000×4/300，与 B（5/1）对比：

| 指标 | B（5/1） | D2（60/20） |
|---|---:|---:|
| 完成 / 受理 | 2583 / 3046 | 2719 / 2980 |
| 系统错误率 | 13.49% | 13.42% |
| admission P95 | 11.75s | **9.92s** |
| first-delta P95 | 25.83s | **21.83s** |
| wall | 237.8s | **202.0s** |
| pgss lease renew | 19113 / 151.0s | **跌出 top20（<0.6s）** |
| capacity ensure | 5388 / 89.4s / 16.6ms | 5833 / 99.8s / 17.1ms |
| admit UPDATE | 2694 / 38.2s / 14.2ms | 2917 / 75.0s / 25.7ms |
| release UPDATE | 2694 / 34.1s / 12.7ms | 2916 / 48.8s / 16.7ms |
| dispatch CTE | 2693 / 12.6s / 4.7ms | 2915 / 19.5s / 6.7ms |

**裁决**：

- 151s 的 renew 是 1s 心跳 artifact；生产节拍下多数 Run 在首次续租前完成，renew 不是 top。
  §0.17 里“renew 87.5s 次之”的排序作废，不能拿它排优化顺序。
- 容量 SQL 合计 **243.1s**，是唯一主墙；**release 路径**（release ensure ~49.9s +
  release UPDATE 48.8s）约 **98.7s**，占 41%。admit/ensure 的单次均值随吞吐升高
  （wall 202s、完成 2719），但端到端反而更好——说明行在更高利用率下仍能服务，
  只是每次等待变长。
- 剩余失败仍是 gateway 502（ConnectError/ReadError）+ RUN_BUSY 轮次重叠，
  `MODEL_CAPACITY_METRIC` 只有 `RESERVED`，不是容量拒绝。

**下一刀候选（Batch D，release 批量化）**：

- 一个事务：按 `call_attempt_id` 升序锁 N 条 lease → 逐行 owner/epoch/幂等 →
  写状态 → 按资源聚合容量递减（group→key，先锁容量行读 `window_start`）→ 一次 commit。
- 容量递减按 `lease_create_time >= window_start` 分区求和；`GREATEST(...,0)` 的最终值与逐条等价。
- **不锁 run 行**（Batch C 的教训），不与 SSE/事件批共享锁；窗口 10–50ms，shutdown drain，
  失败显式回传。
- 先写行为测试：成功/失败/取消、逐行 owner/epoch、window 切换、group+key、重复释放幂等、
  kill -9 后不得出现 SETTLED 未递减；再 A/B。
- 备选（更小的测量）：把 `ensure + release` 合成一条 upsert，省一次往返；预期只省
  往返/索引探测，不省等待，先做单点 A/B 再决定是否值得。


### 0.22 多 Key 拓扑实测：4 keys 把容量行从 243s 降到 25s（2026-10-28）

同一 fast 1000×4/300、生产节拍 60/20、总槽 2000；给同一 model 追加 4 个
credential+deployment（每 key 500 槽，总槽不变），其余不变：

| 指标 | D2（1 key） | K4（4 keys） |
|---|---:|---:|
| 完成 / 受理 | 2719 / 2980 | **3311 / 3559** |
| 系统错误率 | 13.42% | **7.49%** |
| wall | 202s | 251s（吞下更多轮次） |
| admission P95 | 9.92s | 15.68s（队列更深） |
| first-delta P95 | 21.83s | 30.68s |
| ensure | 5833 / 99.8s / 17.1ms | 6996 / **18.7s / 2.67ms** |
| admit | 2917 / 75.0s / 25.7ms | 3498 / **4.9s / 1.40ms** |
| release | 2916 / 48.8s / 16.7ms | 3498 / **1.93s / 0.55ms** |
| capacity 行 | 1 | **4**；inflight 峰值 239（单行 64，limit 500） |

**裁决**：

- 容量行不再排队：admit 单次 25.7ms→1.40ms，release 16.7→0.55ms；总容量 SQL
  243s→~25s。每 key inflight 远低于 500，说明限流不是容量，而是上游/事件/队列。
- 端到端：完成 +22%、错误率近乎减半；admission/first-delta 变差是因为系统在同一窗口
  吞下更多请求（受理 +19%），队列更深，不是容量行回归。
- **“单 Key 2000 槽”是压测拓扑的瓶颈，不是服务代码的必然上限**；生产应按
  model deployment/key 数分摊容量行。4 keys 已足够让容量退出 top；8 keys 用来确认
  是否继续线性，或事件/准入/SSE 成为新墙。
- 验收 harness 的“凭据行 max_inflight == MODEL_MAX_INFLIGHT”断言只适用于单 key；
  多 key 需改为 total = Σ per-key，或按 key 断言（本次 K4 在该断言处失败，
  load 与 smoke metrics 已完成）。

**边界（必须显式记录）**：4 keys 是**拓扑前提**，不是代码修复。生产若一个 model 一个
credential（常见），1-key 的 1000×4 仍是 2719 完成 / 13.4% 错误；删冗余 upsert /
release 批量化两项代码改动**一行都没落地**（Batch A/C 已回退，`model_capacity_service.py`
只剩 settings 相关 diff）。这一缺口记为“被拓扑绕过，未修复”。

**K8（8 keys，250/key）**：容量更便宜（ensure 5138 / 5.7s / 1.12ms、admit 2569 /
1.19s / 0.46ms、release 2569 / 0.55s / 0.21ms），但 1000 用户下完成 **2102**、
错误 **22.52%**（`ADMISSION_REJECTED` 387 个 500 + `SSE_REJECTED` 460），
低于 K4。低负载 200/8 完全干净（200/200、0%），说明不是 8 成员的功能性错误，
而是高并发下的新级联；但**根因未闭合**（见下），不能当成“已解释”。runtime CPU
反而更低（均值 20%、p90 63%），不是 CPU 饱和。
K8 的 `cap=` 采样因脚本选到无 `runtime.model_capacity` 的旧库而无效（PG 日志已证），
但 pgss 证明 8 个 key 都在用。

**未闭合的故障面（阻塞多 key 生产口径）**：K8 的 387 个 500 + 460 SSE 只定位到
“高并发级联”级别，没有拿到根因；200/8 干净说明不是 8 成员的确定性错误。`cap=` 采样
因脚本选到无 `runtime.model_capacity` 的旧库而无效，8 个 key 在用仅由 pgss 推断。
在把多 key 写进生产口径前，必须先在 1000×1/1000×4 复现并定位这 387 个 500。

**结论**：4 keys 是当前甜点；8 keys 不再带来端到端收益，且引入 500 级联。
**容量行已不是墙**（1–4 key 的容量 SQL 从 243s 降到 25s/更少），下一堵是
**准入/SSE/gateway**（K4 的 502 ConnectError/ReadError、`RUN_BUSY`，以及 K8 的 500）。
下一步：在 4-key 拓扑下跑 200/400/600/800 阶梯（每用户 1 轮），找第一个饱和资源
和最大用户数，再决定加 runtime/gateway Pod 还是改 SSE/准入路径；不要继续加 key。


### 0.23 4-key 阶梯与连接池实验：SLO 上限约 500–600，更大池反而更差（2026-10-28）

4 keys（每 key 500 槽）、生产节拍 60/20、每用户 1 轮（消除轮次重叠），
本地 PG 100 / PgBouncer 88、应用默认池 10/进程：

| 用户 | 完成 | 错误 | admission P95 | first-delta P95 | PG 连接 / idle-in-tx |
|---:|---:|---:|---:|---:|---:|
| 400 | 400/400 | 0% | **110ms** | **1104ms** | 16 / 7 |
| 600 | 600/600 | 0% | **160ms** | 1272ms（略超） | 26 / 15 |
| 800 | 800/800 | 0% | **1998ms** | **12.7s** | 89 / 86 |

- 400 达标；600 first-delta 略超（1.27s vs 1.2s）；800 全部完成但延迟越线。
- 800 的 `idle-in-tx=86` 是突发期事务在途（`BEGIN;` + 事件/准入/续租），不是永久泄漏；
  ramp 30s 内 800 用户同时启动，超过准入/事件吞吐，事务在连接上排队。
- **连接池实验**：把 PG 升到 500、PgBouncer 400、应用池 290 后，800 变成
  **752/800**、admission 6.4s、first-delta 13.8s、容量 ensure 2.34→**8.98ms**；
  更大的池让更多事务同时扑向热行，比 88 池更差。**88 池是有益的并发闸门**，
  profile 的 `pg_connection_budget=500` 是上限，不是运行点。
- 4 keys 的容量 SQL 已降到 ~25s；400/600/800 的 pgss top 是事件/会话/准入，不是容量。
- **SLO 口径：400 已验证达标；600 first-delta 1.27s 超 1.2s；500 是保守插值，未实测**
  （当前 8 runtime / 4 worker / 2 gateway / 1 console×4 workers、4 model keys）。
  1000 目标需要把准入/事件吞吐从
  ~19/s 提到 ~33/s（1000 用户/30s ramp），或按准入饱和点加 runtime/console Pod；
  4 keys 是甜点，8 keys 不改善且引入 500。
- **1000×1 轮（S-01 契约，4 keys、88 门）**：受理 **994/1000**、完成 994、
  错误 **0.6%**（6 CONVERSATION_REJECTED + 1 故意）；admission P95 **2.23s**、
  first-delta P95 **7.70s**、wall 40.8s；PG 89/100、idle 82。
  **功能上限 ~1000（差 6 个用户），SLO 已验证 400、600 超标、500 为插值**；两者要分开报。
- **下一刀顺序（复核修订）**：
  1. `count_open_runs` 改 `(tenant_id) WHERE admission_released_at IS NULL` 部分索引 +
     `LIMIT max_open_runs+1` 存在性检查；它是护栏值、允许软超发，等价且更省。
  2. 把 `memory` 阶段按语句拆开测（会话行 `FOR UPDATE`、`pending_options` EXISTS、
     memory head、USER_MESSAGE/seq），不要按“COUNT → 水位”假设改。
  3. 定位 K8 的 387 个 500（多 key 是推荐拓扑，必须先闭）。
  4. 再跑 600/800/1000 同口径 A/B；Pod 配比放在这之后（当前 CPU 20–26%，
     加 Pod 会和加池一样把并发推到同一条路）。
  5. staging E-09（1000×8）仍是唯一权威结论；本地阶梯只定位。

**Admission 分段（per-pod mean，ms）**：

| 用户 | claim | materials | precheck | write | write/counts | write/memory | write/events |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 400 | 2.3 | 13.7 | 6.8 | 28.0 | 5.9 | 6.5 | 4.6 |
| 600 | 3.1 | 18.1 | 6.7 | 35.0 | 7.7 | 7.6 | 6.2 |
| 800 | 274.7 | 96.0 | 277.0 | 233.6 | 63.3 | 50.1 | 43.7 |
| 1000×1 | 105.6 | 92.4 | 126.8 | 201.9 | 50.7 | 34.8 | 34.6 |

- 400/600 的准入是 ~50–63ms；800 起 `claim/precheck` 跳到 275ms 级，1000 的 `write`
  仍是最大段（202ms），其中 `counts`（`count_open_runs`）50.7ms、`events` 34.6ms、
  `memory` 34.8ms。
- `counts` 是 §0.17 已列的护栏 count：改部分索引 + `LIMIT max+1` 存在性检查即可。
  **`memory` 不是 COUNT**：`mark("memory")` 包住的是 `append_inbound`（memory_resume.py:30）
  ——`owned_conversation(lock=True)` 对 conversation 行 `FOR UPDATE`、`pending_options`
  的 EXISTS、memory scope head 读、USER_MESSAGE/seq 写；34.8ms 更可能是会话行锁与往返，
  必须先按语句拆开测，不能按“水位计数”去改。§0.17 的 `count canonical_event` 是
  memory consolidation enqueue（finalize 路径），与本节的 admission `memory` 不是同一条。
- `write` 的 120ms 是 counts 50.7 + memory 34.8 + events 34.6；剩下 ~80ms 是
  submission/attachments/commit。真正的大杠杆是缩短持锁事务的语句数/往返，不是两条 count。
- 88 连接闸门保留；更大的 PG 池/更多 key 已证否。


### 0.24 复核修订（2026-10-28）：哪些是结论、哪些还没闭

- **4 keys 是拓扑前提，不是代码修复**：1-key 的 1000×4 仍是 2719 / 13.4%；
  删冗余 upsert / release 批量化未落地，缺口记为“被拓扑绕过，未修复”（§0.22）。
- **K8 的 387 个 500 + 460 SSE 未闭合**：200/8 干净，1000/8 复现；`cap=` 采样无效；
  多 key 进生产前必须定位（§0.22）。
- **SLO 口径**：400 已验证；600 first-delta 1.27s 超标；500 是插值，未实测（§0.23）。
- **`memory` 阶段不是 COUNT**：`mark("memory")` 包 `append_inbound`（会话行 FOR UPDATE +
  EXISTS + head + USER_MESSAGE/seq），按语句拆开再定，不按“水位计数”改（§0.23）。
- **Batch E 顺序**：1) `count_open_runs` 部分索引+有界存在性；2) 拆 memory 阶段；
  3) 定位 K8 500；4) 600/800/1000 A/B；5) Pod 配比；staging E-09 权威。
- **测量纪律**：先怀疑测量，再怀疑代码；改配置非单调，收益来自去串行化（§0.16）。


### 0.25 采集结果：gate 不是原因，reserve 拆成 select + capacity_reserve（2026-10-28）

1000×1（4 keys、88 门、生产节拍、PG 500）最近两轮都是 **1000/1000 受理、1000 完成、
0% 错误**；gate2：admission P50 345ms / P95 1114ms，first-delta P50 2174ms / P95 5792ms，
wall 33.9s。测试只在多 key 的 `max_inflight` 断言失败（每 key 500 vs 常量 2000）。

第二层 `model_call_seconds`（gate2，per-pod mean）：

| stage | mean | calls | 结论 |
|---|---:|---:|---|
| `select` | **133.6ms** | 133 | dispatch 事务：scan/seed/pick/commit |
| `capacity_reserve` | **94.9ms** | 133 | 容量事务：limits/clock/ensure/lease/admit/commit |
| `reserve` | **228.6ms** | 133 | 正好 = select + capacity_reserve |
| `gate_select` | **0.0ms** | 133 | 选择闸门不是瓶颈 |
| `gate_reserve` | **0.0ms** | 135 | 预占闸门不是瓶颈 |
| `project_running` | **0.0ms** | 133 | 阶段投影不是瓶颈 |
| `retry_wait` | **0 次** | 0 | 没有 0.25s 容量重试 |
| `candidates` | 67.7ms | 133 | reserve 之外，读候选 |
| `credentials` | 42.3ms | 133 | reserve 之外，Console 解析 |

- `model_capacity_total` 只有 `RESERVED`（133+2），没有 `UNAVAILABLE`；容量 SQL
  ensure 3.32ms / admit 0.76ms / release 0.42ms。**228ms 不是闸门、不是容量限流、
  不是重试**，而是两条事务（select 134 + capacity 95）的提交/排队。
- **PG 锁日志（gate3，50ms 阈值）**：日志里的锁等待是
  `INSERT model_capacity ... ON CONFLICT DO NOTHING` 等 transaction（50ms+，
  Wait queue 3–4），**不是 finalize**；finalize 的 57/117ms 因此不是 PG 行锁，
  更像连接/语句/CPU。`capacity_reserve 95ms` 有真实的容量行 ensure 等待。
- `select 134ms` 没有 PG 锁等待 → 最可能是 `_pick_with_contention` 的 `asyncio.sleep`
  在 `_select` 的 `session.begin()` 里重试（睡眠期间持连接/事务/闸门）；gate4 加
  `select_pick_wait`/`capacity_ensure` 确认。
- **gate4 确认（2026-10-28）**：`select_pick_wait` **0 次**——`select 221ms` 不是
  dispatch 重试睡眠；`capacity_ensure` **10.8ms**，而 `capacity_reserve` 191ms——
  大头不是容量行。剩余 ~180–215ms **尚未直接测量**；当前推断是 commit + 连接交接/
  event loop 调度，不是已证实的 PgBouncer 池等待（`candidates` 133 次单条只读 SQL
  68ms/次，按 3.6 次/s 的连接占用 ~0.24 条，池排队解释不了 68ms）。下一步：
  每进程 `SELECT 1` 的 P50/P95、`db_pool_checkout`、PgBouncer `SHOW POOLS.sv_wait`、
  select/capacity 的 commit 子 stage 决胜。
- **PgBouncer 200 A/B（app 10/进程）**：`max_connections_total` 只有 **95**——连接上限在
  应用池 10/进程（≈95），不是 server 200；`select`/`capacity_reserve` 略降（172/168 vs
  221/191），finalize lock 142ms、admission P95 1.55s、first-delta P95 9.72s 与 gate4
  同量级，1000/1000、0 错误。**server pool 200 没有收益**。
- 下一步：**12 runtime Pod**（app 10/pod → 120 客户端，server 200 才用得上），
  看 admission/first-delta 是否随 Pod 下降；若不动，再合并 select+capacity 为一个事务
  （一次 commit）或缓存 candidates/credentials。
- finalize `lock` 57.4ms（gate1 117.2ms，方差大）；`lock_run_source` 先锁 Conversation、
  再锁 Run/Continuation；事件批 `conversation_lock` 50ms、flush 82ms、run_lock 3.8ms
  ——锁在**会话行**上，不是 run 行。PG `log_lock_waits`（50ms）已开，下一轮抓具体 blocker。
- 下一步拆分：select 内部 `scan`/`seed`/`pick`/`commit`；capacity_reserve 内部
  `limits`/`clock`/`ensure`/`lease`/`admit`/`commit`；finalize 用锁日志确认
  blocker 是事件批还是另一条 finalize。


### 0.26 12 runtime Pod 反而更差：共享 DB 是墙（2026-10-28）

4 keys、app 10/进程、PgBouncer 250、PG 500、1000×1：

| 指标 | 8 Pod | 12 Pod |
|---|---:|---:|
| 受理 / 完成 / 错误 | **1000 / 1000 / 0%** | 982 / 1000 / **1.8%**（18 RUN_BUSY） |
| admission P50 / P95 | **641 / 1554ms** | 1021 / 2548ms |
| first-delta P50 / P95 | **4507 / 9724ms** | 6747 / 11039ms |
| wall | 36.5s | 39.1s |
| PG 连接 / idle-in-tx | 95 / 78 | 141 / 122 |
| runtime CPU mean / p90 | 25% / 65% | 20% / 42–53% |
| `select` / `capacity_reserve` | 172 / 168ms | 277 / 253ms |
| `capacity_ensure` | 5.3ms | 28.4ms |

- 12 Pod 的**每进程 CPU 更低**（20% vs 25%），但 DB 连接/事务更多（141/122 idle），
  容量 ensure/commit、admission、finalize 排队更重；**共享 DB 是墙，Pod 不是线性杠杆**。
- 8 Pod 是**当前本地实例**的甜点；12 Pod ×10 + PgBouncer 250（141<250）仍更差，
  说明不只是 server pool，还有应用池 10/进程、客户端并发、容量行/调度；下一步用
  **12×7（84 客户端）**分离“Pod 数”与“客户端连接数”两个变量。继续加 Pod 会把并发
  推到同一条 DB 路径，与 800/290 池实验同构。
- **下一步不是 Pod，是 DB 路径**：合并 select+capacity 为一个事务（一次 commit）、
  candidates Run memo、credentials 缓存、admission write 语句削减；改完再复测 8/12 Pod。
- 当前答案：**1000 功能上可达（8 Pod、4 keys、1000/1000、0 错误）；SLO 上限 400–500
  （400 已验证、600 first-delta 超标、500 插值）**；12 Pod 不提高上限。


### 0.27 复核修订：控制面归因待测、冗余 upsert 已证否、横扩共享点（2026-10-28）

- **“commit + PgBouncer 池等待”是推断，不是测量**：server pool 200 只解释 221→172 的
  ~20%；`candidates` 133 次单条只读 SQL 68ms/次，连接占用 ~0.24 条，池排队不可能解释
  68ms。更像**每次 await 的调度/连接交接/代理往返**。先做决胜测量：每进程 1000 负载下
  `SELECT 1` 的 P50/P95、`db_pool_checkout`、PgBouncer `SHOW POOLS.sv_wait`、select/
  capacity 的 commit 子 stage。SELECT 1 若 30ms 级 → 减 await/提单进程并发；若 1ms 级
  → 先 EXPLAIN 具体语句，合并事务可能白做。
- **“冗余 INSERT ... ON CONFLICT 是最便宜一刀”已实测否证**：§0.18 Batch A 删 release
  ensure 后容量 SQL 60→88ms/次调用、完成 2583→2494；`log_lock_waits` 的 50ms+ 等待
  正是它的**节流**，不是冗余。不要删；要试的是 `ensure+admit` 单条 upsert（保留
  ON CONFLICT 的排队语义）或 lazy create + 替代节流，单独 A/B。
- **“8 Pod = 连接数 ≤ server pool”未被数据支持**：12 Pod ×10 + PgBouncer 250
  （141<250）仍 982/1000、1.8%、admission 2.55s、first-delta 11s；不只是 server pool，
  还有应用池 10/进程、客户端并发、容量行/调度。12×7（84）可分离两个变量。
- **四项优化的真实预期**：模型控制面 ~340ms + 准入 ~500ms；乐观压到 ~200+350ms，
  端到端 1.3–2×，600 进绿区、可能 700–800；**不会把 1000 从 4–8× 拉到 1.2s**。
  1000@1.2s 需要把模型调用控制面移出 PG 临界路径（进程内租约+批量结算或 Redis
  计数+PG 结算），这是带精确性取舍的设计决策，单独进 spec。
- **DB 最优买不来“没有热点行”**：本地 88/250/8 Pod 结论是实例级；服务横扩的共享点是
  `count_open_runs`（成本增长，不是锁）、`model_capacity` 行（按 key 分片）、
  dispatch 计数、多个后台循环（`_start_loops`，每 Pod 一份，无 leader）、Console
  单 Pod 4 worker（横扩需副本）。这些与服务侧优化分开记录；K8 的 387 个 500 仍是
  multi-key 生产的前置。


### 0.28 生产等价 PG 基座与 8 Pod 首跑（2026-10-28）

`localdb/docker-compose.yaml` 重建 PG/PgBouncer（数据卷保留；compose 已备份）：

- PG：`shared_buffers=4GB`、`max_connections=500`、`effective_cache_size=10GB`、
  `work_mem=8MB`、`maintenance_work_mem=512MB`、`max_wal_size=4GB`、
  `random_page_cost=1.1`、`effective_io_concurrency=200`、`jit=off`、
  `pg_stat_statements`/`track=all`。
- PgBouncer：`max_client_conn=2000`、`default_pool_size=450`、
  `max_db_connections=450`、`pool_mode=transaction`、`admin_users=muad`。
- `isf` / `mattermost` / `muad` 数据完整，`muad` 有 CREATEDB。

8 Pod ×4 keys ×1000×1 首跑：**1000/1000、0% 错误**；admission P50 824 / P95 2932ms，
first-delta P50 5130 / P95 **8098ms**，wall 37.4s；CPU 26–29% / p90 62–72%；
PG 117 连接 / idle-in-tx 90。

与旧基座 8 Pod（641/1554、4507/9724、36.5s、95/78、CPU 25%/65%）对比：

- **改善**：model reserve 340→298ms、select 172→167、capacity 168→131、
  candidates 96→79、finalize lock 143→89ms、db_pool_checkout 8.6→6.2ms；
  first-delta P95 9724→8098ms。
- **变差**：admission P95 1554→2932ms、P50 641→824、CPU 25→28%。
- 首跑是 PG 重启后的冷缓存/计划，也可能是 `random_page_cost=1.1`/`jit=off`/
  4GB shared buffers 改变了 admission 路径；需要热缓存复跑与 12 Pod 对照，不能直接归因。

**热缓存 8 Pod（同基座）**：**1000/1000、0% 错误**；admission P50 408 / P95 **1125ms**，
first-delta P50 2355 / P95 **5827ms**，wall **33.9s**；model reserve **202ms**、
select 114、capacity 89、candidates 47、finalize lock **56ms**、db_pool 3.2ms；
CPU 28% / p90 65%；PG 95 连接 / idle 75。

→ **首跑 824/2932 是冷缓存**；热跑全面优于旧基座（641/1554、4507/9724、36.5s；
model 340、finalize 143、pool 8.6ms）。PG 重建是明确净收益。

**12 Pod（450 池）**：895/1000、**10.5% 错误**（105 RUN_BUSY）；admission P50 1210 /
P95 **5223ms**，first-delta P50 7551 / P95 **13204ms**，wall 43.8s；CPU **17%**
（每进程更低，总约 2.0 核）；PG 137 连接 / idle 123；select 257、capacity 254、
candidates 193、finalize lock 129、claim 288/precheck 296/write 292、db_pool 13.2ms。

→ 12 Pod 仍然更差，**不是 CPU，是 DB/queue**（容量 ensure 4.29ms、pool 13.2ms、
admission tail）；8 Pod 是当前甜点，12 Pod 不线性。

**结论（热基座）**：

- 最佳组合：**8 runtime / 4 worker / 2 gateway / 1 console×4 / 4 model keys（500/个）
  / PG 4GB shared buffers + 500 连接 / PgBouncer 450**。
- 1000 用户：**1000/1000、0%**；admission P95 1125ms、first-delta P95 5827ms——
  离 500ms/1.2s SLO 仍差 2.25×/4.9×。
- 12 Pod ×450 仍 895/1000、10.5%；继续加 Pod 会把并发推到同一条 DB 路径。
- SLO 上限在热基座上需要用 400/600 阶梯复测；1000@1.2s 仍不是这四项往返优化能到的。


### 0.29 热基座 400/600 阶梯：600 进入 SLO（2026-10-28）

8 Pod / 4 keys / 每用户 1 轮 / PG 500+4GB shared buffers / PgBouncer 450 / 生产节拍：

| 用户 | 完成/错误 | admission P95 | first-delta P95 | wall | CPU mean/p90 |
|---:|---|---:|---:|---:|---:|
| 400 | 400/400, 0% | **105.8ms** | **1093.4ms** | 31.5s | 12–18% / 26–40% |
| 600 | 600/600, 0% | **135.9ms** | **1176.1ms** | 31.4s | 18–23% / 41–54% |
| 800 | 800/800, 0% | **331.8ms** | **1851.6ms** | 31.5s | 21–29% / 49–65% |

旧基座：400 110/1104；600 160/1272（first-delta 超 1.2s）。
→ **600 在新基座进入 SLO**（p95 1.176s < 1.2s；p99 1.248s 略超），400 稳定绿。
→ **800**：admission P95 331.8ms 绿，first-delta P95 **1851.6ms 红**（1.54×），p50 1251.4ms
  也超 1.2s；model total 1002（upstream 895、reserve 64、candidates 13、credentials 29）、
  admission 153、finalize 17。**SLO 上限停在 600**；800 是功能绿、SLO 红。

- 模型控制面在 400/600 分别：reserve 18/27ms、select 10.7/15.6、capacity 7.2/11.5、
  candidates 3.6/5.2、finalize 5.2/7.6、admission 60/88ms；PG 14/28 连接、idle 7/11。
- 1000×1 热跑仍 1125/5827ms（0% 错误，SLO 差 2.25×/4.9×）；1000×4 过载 3311/7.5%；
  12 Pod ×450 仍 895/1000、10.5%。
- **阶段性结论**：
  - 生产等价 PG 基座是净收益：400/600 全绿，600 从 1272→1176ms；1000 功能 1000/1000、0%。
  - **SLO 上限：600 已验证；800 first-delta 1852ms 红（p50 1251 也超）；700 未测但按插值
    也会红。** 1000 仍是 2.25×/4.9×。
  - 最佳 Pod 配比仍是 **8 runtime / 4 worker / 2 gateway / 1 console×4 + 4 model keys**；
    12 Pod 因 DB/queue 更差。
  - 下一刀顺序：**800 新基座阶梯 → SELECT 1/commit/sv_wait → 12×7 → 服务侧
    `count_open_runs`/capacity upsert → E-09**。


### 0.30 阶段性复盘与下一阶段计划（2026-10-28）

**当前结论**：

- SLO 上限 **600**（400/600 绿；800 admission 332 绿、first-delta 1852 红；1000 1125/5827）。
- 功能上限 **1000**（1000/1000、0%）；1000×4 过载 3311/7.5%；12 Pod 895/1000、10.5%。
- 最佳：**8 runtime / 4 worker / 2 gateway / 1 console×4 + 4 model keys +
  PG 500 连接/4GB shared buffers + PgBouncer 450**；12 Pod/8 keys/大池均负收益。
- 墙是 **model control 106/291ms + admission 153/500ms + finalize 17/56ms + DB queue**，
  不是容量槽位、不是 Pod 数、不是 PgBouncer 服务端池。

**是否到极致**：没有。

- 四项固定成本（merge select+capacity、candidates memo、credentials TTL、admission 语句削减）
  还有 **1.3–2×** 空间；600 有机会推到 700–800，800 first-delta 1.85→1.2–1.4（接近/可能绿）。
- 1000×1 的 first-delta 5.83s → 乐观 3.5–4.5s，仍 3–4×；**1000@1.2s 需要把模型调用控制面
  移出 PG 临界路径**（进程内租约+批量结算或 Redis 计数+PG 结算），是 spec 决策，不是优化项。
- 12 Pod/8 keys/大池已证否；继续加 Pod 会把并发推到同一条 DB 路径。
- 服务横扩还有：后台循环 leader/分区、Console 副本、1-key 容量代码缺口。
- **架构调整的边界**：DB 最优、4 keys、8 Pod、Console 4 worker、事件 batch 这些“容易的
  架构调整”已经做完（600 绿、1000 功能绿）；剩余是控制面出 PG/Redis 的结构性调整
  （精确性取舍），不是配置项。四项是 1.3–2×，1000@1.2s 是 4–8×，必须单独立项。

**模型/key 数（单 key 2500）**：

- **槽位不是限制**：K4 的 inflight 峰值 239，4×500=2000 都远未打满；单 key 2500 的槽位
  同样不是限制。
- 关键是**行/key 数**：1 key 的 1000×4 是 2719/13.4%；4 keys 3311/7.5%；8 keys
  2102/22.5%（500 未闭）。**4 keys 是甜点**；单 key 2500 只是把槽位抬高，仍是一行，
  行锁/提交排队不变。
- **同一 key 多槽位** = 提高 `max_inflight`（如 2500），不增加容量行；行锁/提交仍是单行。
  要行分片必须多 provider key（4 keys），或服务内把同一 key 的容量拆成 N 个子行
  （需要精确性 spec）。如果业务只有一个 provider key，4-key 拓扑不可用，只能做后者
  或接受 1-key 的 13.4%。
- **同一 provider key 的 4 条配置（暂缓，2026-10-28）**：当前唯一约束
  `uq_model_credential_tenant_provider_digest` 不允许同一个 API key 建 4 个 credential
  （4-key 实验撞过，才改成 4 个不同 key）。生产若只有一个 provider key，需要在
  “放宽唯一约束 + 4 credential”与“容量子行（一 key N 子行）”之间做 schema/spec 决策；
  前者会破坏禁停/轮换/查找语义（同 key 不是 4 个 key，provider 的 2500 仍共享），
  不建议生产直接用。验收库可临时 drop 约束验证 4 行收益。
  **当前 4 个不同 key 已足够支撑 600 SLO；这项留到生产确认单 key 时再处理。**
- 模型数（业务模型）与 key 数是两个维度：同一模型的 1000 用户靠 key 分行；多模型只有在
  用户分布到不同模型时才分流行。验收应做 **1×2500 / 4×2500 / 8×625** 对照，并修 K8 500。

**下一阶段计划**：

1. **决胜测量**：每进程 `SELECT 1` P50/P95、commit 子 stage、PgBouncer `SHOW POOLS`
   （`cl_waiting`/`maxwait`/`sv_active`）。
2. **模型控制**：merge select+capacity（若 await 占主导）/ candidates memo / credentials TTL /
   capacity `ensure+admit` 单条 upsert（不删 ensure；Batch A 已证否）。
3. **准入**：`count_open_runs` 部分索引+LIMIT；memory 按语句拆；write 语句削减。
4. **finalize/event**：lock/notification/event batch。
5. **key/model 拓扑**：1×2500 / 4×2500 / 8×625；K8 500 根因。
6. **Pod/DB**：8 Pod 最佳；12×7；PG 基座；后台 loops/leader；Console 副本。
7. **1000@1.2s**：控制面出 PG/Redis 的 spec；staging E-09 收尾。

**预期**：四项 1.3–2×；SLO 600→700–800；800 first-delta 1.85→1.2–1.4；1000 5.83→3.5–4.5
（仍 3–4×）；1000@1.2s 结构。**最大 SLO 用户：600 已验证，目标 700–800；功能 1000。**

### 0.31 决胜采集：loop lag / GC / 连接池 / 控制面（2026-10-28）

**两轮口径必须分开看**：第一轮带 `DB_PROBE_INTERVAL_MS=50`（每进程 20 次/s），
第二轮只开 `LOOP_LAG_INTERVAL_MS=10` + `GC_PROBE=1`（两者回调/采样均无锁、开销 <1ms）。
第一轮的绝对值是**探针污染**，不能作为系统成本。

#### 第一轮（DB 探针，仅用于定位，绝对值作废）

| 观测 | mean | P50 | P95 | 样本（单 Pod） |
|---|---:|---:|---:|---:|
| `db_probe`（每进程 `SELECT 1`） | 21.5ms | 3–4ms | 100–200ms | 977 |
| `db_commit`（含 flush） | 8.2ms | — | 15–20ms | 2166 |
| `db_pool_checkout` | 6.4ms | — | — | 3300 |

PgBouncer `SHOW POOLS`：`cl_waiting=0`、`maxwait=0`、`sv_active=2–5`、`sv_idle=93–96`；
`model_capacity` INSERT 2.69ms / UPDATE 1.76ms，conversation/event 0.1ms 级。
**池/语句/commit 三个假设是否掉的；但 21.5ms 不是系统固有成本**：977 是单个被 scrape
Pod 的样本（20/s × 49s），全集群 8 Pod ≈ 160 次 rollback 事务/s，约等于 PG 侧
`commits_per_sec≈360` 的 44%，探针自己在制造队列。

#### 第二轮（800，只开 loop-lag + GC，探针开销可忽略）

| 观测 | mean | P50 | P95 | P99 | 样本 |
|---|---:|---:|---:|---:|---:|
| `event_loop_lag` | **0.6ms** | 0.5ms | **2ms** | 5ms | 4825 |
| `gc_pause` | **0.7ms** | 0.5ms | **2ms** | 2ms | 908 |
| `db_pool_checkout` | **0.0ms** | 0.5ms | **0.5ms** | 0.5ms | 4952 |
| `db_commit` | **1.7ms** | — | ≤5ms | — | 1948 |

GC 次数：gen0 828、gen1 75、gen2 5；**没有一次 ≥50ms 的 GC 停顿**，
**没有一次 ≥10ms 的事件循环 lag**（4825 个样本里 4818 个 ≤10ms）。

800 同轮业务数字：`completed 800/800`、0% 错误、admission P50/P95 **105.8/198.1ms**、
first-delta P50/P95 **1110.7/1383.9ms**、wall 31.4s；

| 控制面 stage | mean | P50 | P95 |
|---|---:|---:|---:|
| `model.total` | 932.6 | 1000 | 2000 |
| `model.upstream` | 867.6 | 1000 | 1000 |
| `model.allocate` | 38.3 | 50 | 100 |
| `model.select` | 23.6 | 20 | 100 |
| `model.reserve` | 38.4 | 50 | 100 |
| `model.candidates` | 8.9 | 10 | 20 |
| `model.credentials` | 17.7 | 20 | 50 |
| `admission.write` | 44.8 | 50 | 100 |
| `admission.materials` | 23.6 | 50 | 50 |
| `finalize.lock` | 10.1 | 10 | 20 |

**判读（修正前一轮的结论）**：

1. **事件循环与 GC 都不是瓶颈**：lag P95 2ms、GC P95 2ms，两个此前最可疑的机制同时否掉。
   账目对不上“20ms/await”不是测量错误，而是第一轮的探针把每次 await 推到了 ~20ms。
2. **真实每次 await ~1–3ms**：select 23.6ms（~9 段）、reserve 38.4ms（~9 段）、
   candidates 8.9ms（~9 段）—— 与 1–3ms/await 吻合。**P1b 合并事务的预期收益只有 ~20–40ms，
   不足以覆盖它把 dispatch 行锁持有从 ~70ms 拉到 ~200ms 的风险，暂停。**
3. **800 的 first-delta 由 upstream + 准入尾组成**：upstream P50/P95 = 1000/1000ms
   （profile p50 800ms、p95 2500ms，本轮只 1 轮所以集中在下半段），
   加 admission 198、allocate 100、write 100、finalize ~20，得到 P95 1384。
   **固定成本优化的上限：把 admission+allocate 各砍一半，800 也才 ~1280，仍在 1.2s 边缘。**
4. **run-to-run 方差必须计入结论**：同一 800 配置，上一轮 admission 331.8 / first-delta 1851.6，
   本轮 198.1 / 1383.9（差 25%）。**单轮数字不能直接当 SLO 边界；每点至少两轮。**
5. **1000 的 5827ms 是排队，不是固定成本**：600→800 的 overhead 从 ~376ms 涨到 ~584ms；
   到 1000 变成 ~4.5s，说明系统在 800–1000 之间过拐点。**SLO 边界要重测 600/700/800/900。**
6. **探针本身的工程教训**：第一版 `gc.callbacks` 在回调里调 `record_*`（取 metrics 注册表锁），
   与 stop-the-world 挂起的持锁线程互锁，**实测 4/8 runtime 卡死**（`sample` 栈：
   `invoke_gc_callback → inc_counter → Lock.acquire`）。已改为无锁定长环形缓冲 +
   `_gc_flush_loop` 转指标；回归测试禁止回调调用指标写入。GC/loop 探针可以留作常开诊断。

**修正后的下一步**：

- **P0 重画阶梯（先做）**：600/700/800/900 各跑两轮、探针全关（loop/GC 可留，开销 <1ms），
  用 P50/P95 与失败账目定拐点；800 若两轮都 <1.2s 则上限上移，否则确认 600–700。
- **P1a 只做便宜的扁平化**：`candidates`（8.9ms）与 `credentials`（17.7ms）按 Run memo/TTL、
  `_read_candidate_facts` 的 5 条读合并为 2 条；**预期仅 20–30ms，不作为 SLO 主杠杆**。
  合并 select+capacity（P1b）**暂缓**，除非阶梯显示 800 只差 20–30ms。
- **P2 准入尾**：`write` P95 100、`materials` 50、`claim` 20 —— 合计 ~170ms，是 800 进绿
  最现实的一块；`count_open_runs` 部分索引 + `LIMIT max+1`、precheck/materials 合并。
- **P3 finalize**：lock/notification 各 P95 20ms，尾延迟小；先不动。
- **P4 1000@1.2s**：仍要控制面出 PG/Redis（结构性），与本轮四项无关；
  在 800/900 阶梯确认拐点后再评估收益/风险。
- **P5 横扩证据**：阶梯稳定后跑 4/8/12/16 Pod（每 Pod 吞吐恒定），再谈快速横扩。

### 0.32 根因判定：本地 1000 是宿主 CPU 饱和 + 串行放大（2026-10-28）

**实验矩阵（8 Pod / 4 keys / 1000×1，除注明外）**：

| # | 配置 | 完成/错误 | admission P95 | first-delta P95 | 结论 |
|---|---|---|---:|---:|---|
| 1 | 700，10ms 窗口，run1 | 700/700, 0% | 139.0 | **1188.9** | 绿 |
| 2 | 700，10ms 窗口，run2 | 700/700, 0% | 132.1 | **1176.2** | 绿 |
| 3 | 800，10ms 窗口（loop/GC 探针） | 800/800, 0% | 198.1 | 1383.9 | 红（差 184ms） |
| 4 | 1000，10ms，干净 | 1000/1000, 0% | 951.5 | 5218.6 | 红 |
| 5 | 1000，10ms，复跑 | 999/1000 | 6335.5 | 11542.3 | 红（双稳态慢支） |
| 6 | 1000，50ms，run1 | 1000/1000, 0% | **273.6** | **1637.2** | 红（大幅改善） |
| 7 | 1000，50ms，run2 | 1000/1000, 0% | 674.8 | 3372.2 | 红（未复现） |
| 8 | 1000，12 Pod，50ms | 1000/1000, 0% | 1027.3 | 4837.7 | **加 Pod 更差** |

**否证矩阵**：

| 假设 | 证据 | 判定 |
|---|---|---|
| 模型槽位/KEY 不够 | inflight 峰值 239，4×500=2000 | **否** |
| PgBouncer 池排队 | `cl_waiting=0`、`maxwait=0`、`sv_active 2–67`、`sv_idle 23–96` | **否** |
| PG 行锁/事务等待 | 等待事件 **847×ClientRead、39×CPU、1×transactionid**；无未授予锁 | **否** |
| SQL 慢 | capacity INSERT 2.69ms、UPDATE 1.76ms、event 0.11ms | **否** |
| Runtime 事件循环/GC | lag P95 5ms、GC P95 5ms（1000 轮） | **否** |
| 单进程串行 batcher | flush 25–58ms、event 17–27/s；10ms 时利用率≈1，队列 max 23、wait 0.5–2s | **放大器，非首因** |
| 宿主 CPU 饱和 | 宿主峰值 **13.75/15 核**；后台进程峰值 8.6 核；栈自身 ≈4.5–7 核 + PG 1.8 核 | **首因（本地）** |

**硬件口径（2026-10-28 复核）**：MacBook Pro `Mac17,9`，Apple **M5 Pro，15 核**
（5 super + 10 performance）、**48GB 内存**；`sysctl hw.ncpu=15`、`hw.perflevel0.logicalcpu=5`、
`hw.perflevel1.logicalcpu=10`。**“48”是内存不是核数**；1000 时宿主峰值 13.75/15 核，
是真实接近饱和，不是“48 核只用 15 核”。

**根因链**：

1. **本地 15 核 Mac 不是隔离的容量环境**：后台（Virtualization/OrbStack/Terminal/WindowServer/IDE 等）
   峰值 8.6 核；栈自身 8 runtime ≈2.2–5.6 核、PG ≈1.8 核、gateway/worker/proxy/driver ≈0.5 核。
   两者叠加在峰值越过 15 核，所有进程的墙钟被拉长。
2. **DB 本身是空闲的**：PG 只等客户端（ClientRead 847），没有锁等待；所以“慢”不是 DB 造成的，
   而是应用在 CPU 竞争下每轮更多 await 被拉长。
3. **串行放大器**：每个 runtime 进程只有一个 event batcher；flush 25–58ms，而 1000 时
   每进程事件率 17–27/s。10ms 窗口下利用率≈1，队列把 0.1–2s 的等待直接加到 persist/first-delta；
   50ms 窗口把 flush 压到 25ms、10ms 的确定性饱和消失，所以 run1 从 5218 降到 1637，
   但宿主抖动仍能把系统推回慢支（run2 3372、复跑 11542）。
4. **加 Pod 不是解**：12 Pod 把连接数 92→129、idle-in-tx 74→99，宿主 CPU 更紧，
   first-delta 反而 4837；**本地加 Pod 只会把并发推回同一条宿主/DB 路径**。
5. **双稳态**：同一配置在 1000 下会在“快支 ~1.3–1.6s”和“慢支 ~3.9–11.5s”之间跳变；
   700 两轮稳定绿（1176/1189），800 稳定红（1384），拐点在 **700–800**。

**结论口径**：

- **本地可交付 SLO：700 用户**（两轮绿，余量 11–24ms）；800 红（差 184ms）；1000 红且不稳定。
- **这个 700 是宿主受限数字，不是服务容量上限**；服务自身在 1000 时只用 ~4.5–7 核，
  PG 1.8 核，模型槽位 239/2000，DB 无锁等待。**真值必须用 staging E-09（隔离 CPU）测。**
- **不能再用“加 Pod / 加 KEY / 调池”解释或解决本地 1000**；这三条已被 8/12 Pod、4/8 keys、
  PgBouncer 200/450 反复证否。

**可落地的服务侧改进（降放大，不承诺本地 1000 进绿）**：

1. **Event batcher 分片**：按 conversation hash 分 N 个 worker（保序），把单进程 flush 吞吐 ×N；
   或把 `_lock_sources` 的 conversation/run/continuation 三条 `FOR UPDATE` 合并、无 continuation
   时跳过，减少每 flush 的往返。
2. **准入合并**：`claim/materials/precheck/write` 4 段 ~275ms（1000），合并 precheck+materials、
   `count_open_runs` 部分索引 + `LIMIT max+1`。
3. **模型控制面合并**：`select 84 + reserve 156`（1000），合并 select+capacity 单次往返；
   candidates/credentials memo。**只做扁平化，不动 dispatch 行锁边界**（P1b 仍暂缓）。
4. **`EVENT_BATCH_WINDOW_MS=50` 作为临时缓解**：已验证可把 1000 快支从 5218 降到 1637，
   但慢支仍会复发；它不是根因修复，进生产前必须配合 1/2/3。
5. **验收口径**：本地只用于定位；E-09（1000×8/600s、隔离 CPU、生产等价 PG）是唯一权威。

### 0.33 红区优化路线与服务器配置（2026-10-28）

**目标不是找 SLO 边界，而是把红区（1000）的服务侧成本压到极限，再给配置。**

**成本模型（1000×1，burst ≈31 turns/s，8 Pod）**：

| 资源 | mean | p90 | 说明 |
|---|---:|---:|---|
| runtime（8 Pod 合计） | 2.17 核 | **5.64 核** | 每 Pod 125 用户；p90 0.70 核/Pod |
| gateway（2 Pod） | 0.36 核 | 0.77 核 | 每 Pod 500 条 SSE |
| worker（4 Pod） | ~0.05 核 | — | 该负载几乎不用异步工具 |
| console（4 worker） | ~0.05 核 | — | 仅会话/设置 |
| PG（容器） | **1.8 核** | ~2.0 核 | 434 commits/s、WAL 0.58MB/s |
| **服务合计（不含 harness）** | **~4.4 核** | **~8.3 核** | 15 核宿主上叠加后台 8.6 核峰值 → 饱和 |

**功能不变量（硬前提，任何优化不得改变）**：

1. **事件顺序**：同一 conversation 的 `seq` 严格单调、无空洞、无重复；提交顺序与落库顺序一致。
2. **幂等**：同一 `source_event_id` 重复提交只产生一行，返回同一结果；重试不重复计数。
3. **终态一次**：同一 Run 的终态事件恰好一次；取消原因/错误码不被优化改写。
4. **准入判定等价**：`max-1 / max / max+1` 三个边界下，准入/拒绝结果与优化前一致。
5. **容量不超发**：任何路径都不得让 `inflight` 超过 `max_inflight`；lease/release 恰好一次。
6. **SSE 语义**：单连接帧序不变、断线重连游标只前进、终态不丢。
7. **接口不变**：不改 schema、API、错误码、鉴权/租户语义；不引入跨请求的可变配置缓存。

**红区优化清单（只保留不改变上述不变量的项）**：

| 优先级 | 改动 | 现状（1000） | 目标 | 预期收益 | 功能风险 |
|---|---|---|---:|---:|---|
| ~~P1~~ | ~~event batcher 分片~~ **已于 2026-10-28 删除**：无法在无 48 核环境验证，且 env 旋钮可造成 700/800 的 60–80% 回退 | — | — | — | 删除代码/env/测试；未来有真实“每 Pod 事件管线饱和”证据再按自适应并发重做 |
| P2 | **准入**：`count_open_runs` 部分索引 + `LIMIT max+1`；precheck+materials 仅在两者都是纯读时合并 | 275ms 合计 | ≤170ms | **-100ms** | 低：判定边界用 max-1/max/max+1 行为测试锁死 |
| P3 | **模型控制面只做事务内读合并 + 单次 attempt memo**；**不合并 select+capacity 事务、不做跨请求 credentials TTL** | select 84 + reserve 156 + candidates 35 + credentials 39 | ≤150ms | **-60ms** | 低：不跨事务、不缓存可变配置 |
| P4 | SSE/网关按连接分片、减少每帧拷贝 | gateway p90 0.77 核 | 保持线性 | 防 1200+ 拐点 | 中：需帧序/重连测试 |
| P5 | worker/console 后台循环 leader/分区 | 该负载近 0 | — | 横扩前置 | 中：需 exactly-once 测试，暂缓 |

**P1/P2 实测（2026-10-28）**：

- **分片是容量档，不是全局默认**：1000 时 1→2 分片把 `queue 37.7→15.3ms`、
  `persist 390→230ms`、first-delta P95 `3657→1861ms`；4 分片反而 `2595ms`（DB 并发过高）。
  但 700 时 2 分片把 first-delta P95 从 `1205` 推到 `2167ms`，800 从 `1342` 推到 `1872ms`。
  → **默认 `EVENT_BATCH_SHARDS=1`；目标 1000 的部署设 2**，不设 4。
- **P2 行为等价已锁死**：`count_open_runs` 只在 LIMIT 处提前停止，`>= max` 判定不变；
  新增部分索引 `ix_run_record_open_tenant`（`admission_released_at IS NULL`）。
  本地 A/B 最后一轮宿主明显更忙（连接 111、idle-in-tx 101、wall 38.25s），
  `counts` 8.7ms（700）/41.9ms（1000）不能单独归因于 P2；其价值在大表生产环境，
  需在安静宿主或 staging 复核。

**明确剔除（会碰语义，硬前提下一律不做）**：

- **select+capacity 合并事务**：会改变重试/回滚与 dispatch 行锁持有，收益仅 ~40ms，不做。
- **跨请求 credentials TTL**：会引入凭据停用/轮换的陈旧窗口，不做。
- **合并 conversation/run/continuation 三条 `FOR UPDATE`**：锁序可能变化，先用“跳过无 continuation”这一等价删减；合并锁查询留到有锁序测试之后。
- **任何缓存/旁路导致超发、重复、乱序、丢终态的设计**。

**预期**：P1–P3 合计约 **-410ms**；1000 快支 1637ms → **~1230ms**，接近 1.2s；
慢支仍受宿主/上游约束。**停止条件**（“没有优化空间”）：

1. `first-delta - upstream - admission` 的服务侧开销 ≤300ms；
2. 加 CPU/Pod 后 first-delta 不再线性下降；
3. PG <50% CPU、无 `transactionid` 等待、PgBouncer `cl_waiting=0`；
4. 再往下只剩 upstream（模型 p50 800ms）和网络/SSE 的物理成本。

**生产服务器配置（业务服务，DB 单独算）**：

单机 **16 核 / 32GB worker 节点**，按当前 1000 用户 burst 31 turns/s：

| 单元 | 副本 | request / limit | 内存 request/limit | 说明 |
|---|---:|---|---:|---|
| agent-runtime | 8 | 1 / 2 核 | 2 / 4 GB | 125 用户/Pod；p90 0.70 核 |
| im-gateway | 2 | 1 / 2 核 | 1 / 2 GB | 500 SSE/Pod |
| agent-worker | 4 | 0.5 / 2 核 | 1 / 2 GB | 按异步工具另算 |
| console-platform | 1（4 worker） | 1 / 2 核 | 2 / 4 GB | UI/鉴权另算 |
| **合计 request** | — | **13 核** | **24 GB** | 单节点可放下，但无 HA/突发余量 |

**推荐 2 × 16c/32G**（HA + 滚动升级 + 突发余量）：runtime 4+4、gateway 1+1、
worker 2+2、console 放其中一台；每节点 request ≈6.5 核 / 12GB，p90 实际 ≈3.5 核，
留足头部空间。**单节点只放业务、不混部 PG。**

**容量估算（业务服务，线性、待 E-09 校准）**：

- 当前每 Pod 125 用户、p90 0.70 核 → **~180 用户/核**（burst）；
- 单 16c 节点按 70% 目标、留 2 核系统开销：可调度 ~9 核给 runtime → **~1600 用户**（CPU 口径）；
- 2 节点 HA（N+1）实际承诺：**~1000–1200 用户**；P1–P3 后按实测再上调；
- 内存口径：runtime 2GB/Pod，单节点 8 Pod 仅 16GB，不是约束；32GB 足够。
- **DB 单独**：当前 PG 1.8 核/1000 用户 burst，建议 **4–8 vCPU / 16–32GB**、500 连接、
  PgBouncer 450；不在 worker 节点上混部。

**2000/3000 的扩展原则：加 Pod，不加分片。**

分片不是扩展轴：它只补偿“单 Pod 塞太多用户”。用户数增长时保持 **每 Pod 125 用户、
每 Pod 2 分片**不变，线性加 Pod；每 Pod 的每 shard 事件率仍约 8/s，s2 利用率 ~0.2–0.4。
**不要用 s4 去扛 2000/3000**（1000 实测 s4 反而 2595ms，DB 并发过高）。

| 用户 | runtime Pods | 每 Pod 用户 | shards/Pod | 16c/32G 节点 | gateway Pods | 模型 keys | DB |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1000 | 8 | 125 | 2 | 2（HA） | 2 | 4 | 8 vCPU / 32GB |
| 2000 | 16 | 125 | 2 | 3 | 4 | 4–8 | 8–12 vCPU / 32–64GB |
| 3000 | 24 | 125 | 2 | 4 | 6 | 8 | 12–16 vCPU / 64GB |

- **节点打包**：16c 节点放 6–8 个 runtime Pod（1 核 request / 2 核 limit），
  p90 实际 0.7 核/Pod；再放 1–2 个 gateway、2 个 worker；console 单独或与 gateway 同节点。
- **DB 连接**：24 Pods × pool 10 = 240 client；PgBouncer 450 server / 2000 client 够用；
  PG 8–16 vCPU、500 连接；超过 3000 再按租户分库或抬 PgBouncer。
- **模型容量**：4 keys × 500 = 2000 槽位，3000 用户 inflight 约 700（线性外推），槽位仍够；
  但 4 行的锁速率 ×3，**3000 前必须补 8 keys 的 2000/3000 实测**，并先闭 K8 的 500。
- **本地 15 核不能验证 2000/3000**：12 Pods 在 15 核上已因宿主饱和变差；
  2000/3000 必须上 3–4 台 16c 节点（合计 48–64 核）或 staging E-09 的 2000/3000 阶梯。
- **横扩判据**：每 Pod 吞吐恒定（125 用户/Pod、s2、p90 ≤0.8 核），
  first-delta 不随 Pod 数上升；若上升，瓶颈已转到 DB/网关/模型行，按对应层扩。

**注意**：以上是 1000×1 burst 口径；1000×8/600s 稳态的 CPU/turns 不同，必须用 E-09 重算。
本地 15 核宿主只用于代码 A/B，不作为生产容量依据。

### 0.34 密度实验的负结果：决定项是“总负载 × 宿主容量”（2026-10-28）

**固定 600 用户、只变 runtime Pod 数**（profile `users` 仍 1000 以过正式校验；
`CAPACITY_LOAD_USERS=600`），5/6/8 Pod 对应 120/100/75 用户/Pod：

| Pods | 用户/Pod | first-delta P50 | first-delta P95 | admission P95 | persist |
|---:|---:|---:|---:|---:|---:|
| 8 | 75 | 1024.3 | **1089.8** | 102.0 | 79.8 |
| 6 | 100 | 1029.1 | **1116.0** | 106.6 | 82.9 |
| 5 | 120 | 1045.6 | **1118.6** | 112.1 | 85.0 |

**结论：密度不是一阶变量。** 75→120 用户/Pod，P95 只差 2.6%；model/admission/persist
的 stage 也几乎重合（upstream 854–862、persist 80–85、flush 9.3–9.9）。
**700/800/1000 的差异来自总负载从 600→800→1000 的增长与宿主/DB 的共享容量，不是每 Pod 密度。**

这同时否定了两件事：

- **“1000 加到 10–12 Pod 就能进 SLO”没有证据**：600×5（120/Pod）是绿的，但总量只有 600；
  1000×12 的瓶颈是总量（turns/s、DB、宿主），不是密度。
- **“按用户数调 shard/密度”是错轴**：P1 删除正确；6 个 Pod 的 100/Pod 与 5 个 Pod 的
  120/Pod 在 600 下没有差别，说明“每 Pod 用户数”本身不是可调的容量旋钮。

**正确的容量模型**：

- `服务侧 per-turn 成本 × 总 turns/s = 需要的聚合 CPU/DB`；宿主/DB 容量决定 knee。
- 本地 15 核 + 8.6 核后台的 knee 在 **600–800 总用户**；1000 需要更安静的宿主、
  更大 CPU，或把 PG 挪出宿主。**加 Pod 不改变聚合负载，除非同时加宿主/DB 容量。**
- 2000/3000 需要 **3–5× 聚合容量（节点 + DB）**，不是把 125 用户/Pod 调到 200/300，
  也不是加 shard。

**quiet-host 1000 实测（2026-10-28）**：起跑前后台仅 ~1.0 核，跑 **1000×8 Pod×s1**：
first-delta P50/P95 **1672.5 / 2179.9ms**、admission 263.5/478.2ms、0% 错误；
宿主峰值仍到 **13.78/15 核**。相比有后台尖峰时的 3657ms 改善 40%，但仍红。

**原因不是后台没关干净，而是测试拓扑本身**：入口代理（1000 线程）+ 负载驱动（1000 客户端）
+ 同机 PG（1.8 核）都在同一 15 核宿主上；生产不会有这两个 harness 进程。
→ **本地 1000 的结论只能作为上界；决定性实验必须把 PG 和/或驱动挪出宿主（第二台机器/云）。**

**预算必须拆成两条（DeepSeek 复核，认可）**：

| 预算 | 随 Pod 扩展？ | 1000 实测 | 2000/3000 含义 |
|---|---|---|---|
| **app CPU**（runtime/gateway/worker/console） | **是**，加 Pod/节点线性 | runtime p90 5.64 + 其他 0.8 ≈ **6.4 核** | 加节点有效；按每 Pod 成本算 |
| **DB**（PG/PgBouncer） | **否**，所有 Pod 共享 | PG ≈ **1.8 核**、434 commits/s | 加 Pod 只会把更大并发压到同一 DB；需独立扩 vCPU/连接 |
| 连接/模型行 | 部分共享 | 82 连接、4 个容量行 | PgBouncer/PG 连接、4 行锁速率是独立上限 |

`生产 knee = min(app 节点容量 ÷ per-turn CPU, DB 容量 ÷ per-turn DB 工作量, 连接/模型行上限)`。
只报一条聚合数，会在 2000 时把 DB 墙误判成 app 墙。

**P3 范围收紧（DeepSeek 复核，认可）**：

- **candidates / credentials 的 Run 级 memo 在 1000×1 下收益 ≈0**（每轮只一次模型调用；
  跨请求 TTL 有轮换/停用陈旧窗口，不做）。
- 真正能做的是**同事务内读合并**：
  1. `limits + database_clock` 合成一条；
  2. 候选扫描的 key/group inflight 两条查询合并（或 facts 合并）；
  3. `ensure_capacity_row` 惰性化（省 2–3 个 await；注意 Batch A 是“删 release ensure”被证否，
     这里是 reserve 侧惰性 ensure，需并发建行/唯一冲突测试）。
- 预期 **2–4 个 await ≈ 40–80ms/轮**，不是泛泛的 30–60ms；按 600 宿主常量 A/B。

**下一步（不依赖 48 核）**：

1. **P3 + finalize/admission 语句削减**：降低 per-turn 成本，宿主无关；在 600 下 A/B
   （宿主作为常量，stage ms 可比）。
2. **quiet-host 1000**：关后台/挪 PG/驱动分机，看 knee 是否从 600–800 右移到 1000。
3. **云 32 核 + 专用 PG**：跑 E-09 的 1000/2000/3000，验证聚合容量模型。

**“最优”的判据**（修正）：

- `first-delta - upstream` 的服务侧开销在目标总 turns/s 下 ≤300ms；
- 加节点/PG 容量后 first-delta 线性下降（聚合拐点之上）；
- PG <50% CPU、无 `transactionid` 等待、PgBouncer `cl_waiting=0`；
- 2000/3000 的 per-turn 成本与 1000 相同（聚合线性）。

### 0.35 P1/P2 总结与“取消分片旋钮”决策（2026-10-28）

**P1：event batcher 按 conversation 分片**

- 实现：`ShardedEventBatcher`，按 `conversation_id` 稳定分片；同会话同 shard，`seq` 保序；
  锁/事务/幂等/Plan B 隔离完全复用 `EventBatcher`；未改 schema/API/错误码。
- 测试：6 个行为用例（路由稳定、同会话跨片保序、`source_event_id` 幂等、
  跨片 lease 失败隔离、默认单分片、env 分片数）；事件/容量回归 41 passed。
- 实测：1000 时 1→2 分片 `queue 37.7→15.3ms`、`persist 390→230ms`、
  first-delta P95 `3657→1861ms`；4 分片反而 2595ms。
  **但 700 时 2 分片把 P95 从 1205 推到 2167ms，800 从 1342 推到 1872ms。**
- 结论：分片只在高密度（125 用户/Pod）有用，低密度是负收益。

**P2：租户在飞 Run 计数上限 + 部分索引**

- `count_open_runs` 增加 `limit`，在 LIMIT 处提前停止；`>= max` 判定不变。
- 迁移 `0028`：`ix_run_record_open_tenant (tenant_id) WHERE admission_released_at IS NULL`。
- 测试：计数边界 1 个用例 + 准入回归 35 passed；行为等价。
- 本地 A/B 最后一轮宿主明显更忙（连接 111、idle-in-tx 101、wall 38.25s），
  绝对值不可比；其价值在大表生产环境，需 staging 复核。

**执行结果（2026-10-28）**：P1 的 `ShardedEventBatcher`、`EVENT_BATCH_SHARDS`、两个 env 测试已删除；
batcher 核心与 P2 不受影响，回归 **42 passed**。P2 保留。

**决策：分片数不得成为随用户数调整的部署旋钮。**

- `EVENT_BATCH_SHARDS` 不作为生产配置项；**默认固定 1**，部署清单不暴露。
- 目标容量模型只保留一条轴：**聚合容量（节点 CPU + DB）**。Pod 是承载单元，但
  在总负载不变时加 Pod 不改变性能（600×5/6/8 实测）；分片恒为 1。
- **验证门（无 48 核替代）**：固定 **600 用户**、只变 Pod 数 **5/6/8**（120/100/75 用户/Pod），
  宿主背景作为常量；三个点直接画密度曲线。背景：DeepSeek 复核指出“缺的不是核，
  是不被 8.6 核后台抢走的宿主”；固定总用户可让宿主负载基本恒定。
- 原 48 核验证门：1000 × 10/12 Pod × s1（有隔离环境时再做）。
  - 若 ≤1.2s：**删除 P1 分片实现**（保留 P2），容量只靠 Pod；
  - 若仍红：做**自适应 flush 并发**（按队列深度自动 1→2，不暴露旋钮），
    或进入结构性 spec（finalize 锁/控制面出 PG），**不回到“按用户数设分片”**。

### 0.36 根因：每轮 ~478 条 SQL，成本在事务/往返而非 SQL（2026-10-28）

**600×8 干净基线 + PGSS 全量采样**（seed 后 reset，load-only）：

| 指标 | 数值 | 每轮 |
|---|---:|---:|
| PGSS calls | **287,044** | **478 条/轮** |
| PGSS total_exec_time | 6,922ms | **11.5ms/轮** |
| BEGIN | 69,250 | **115/轮** |
| ROLLBACK | 53,512 | **89/轮** |
| COMMIT | 15,738 | **26/轮** |
| `statement_timestamp()` | 7,712 | 13/轮 |
| canonical_event INSERT | 6,044 | 10/轮 |
| conversation last_seq SELECT/UPDATE | 11,455 | 19/轮 |
| `FOR KEY SHARE`（FK 检查，非 ORM 懒加载） | 13,283 | 22/轮 |
| chat_session / agent_definition / credential limits | 9,057 | 15/轮 |

**事务比与归属（同一轮 600 基线）**：

- BEGIN **69,250** / COMMIT **15,738** / ROLLBACK **53,512** → 每轮 **115.4 / 26.2 / 89.2**；
  **BEGIN/COMMIT = 4.4**，绝大多数是只读短会话被 ROLLBACK。
- runtime 自身 `db_pool_checkout`：单 Pod 3,064 / 75 轮 = **41/轮**；集群 8 Pod 同比例。
  其余 **~74/轮（65%）来自 Console / Gateway / Worker / 后台任务**。
- `FOR KEY SHARE` 是 PostgreSQL RI 触发器对带 FK 的 INSERT 发出的检查，
  **不是 ORM 懒加载**；`selectinload/joinedload` 无效，只能减 INSERT 或改 FK 策略（高风险）。

**判读**：

- SQL 单条执行 0.07–0.13ms，**不是 SQL 慢**；DB total_exec_time 仅 11.5ms/轮。
- 成本是 **478 次往返/轮 × 每轮 1 次模型调用**；600 用户（20 turns/s）≈ **9.6k 语句/s**，
  800（26.7/s）≈ 12.8k/s，1000（33/s）≈ **15.8k/s**。宿主/DB 在 10k+ 短语句/s 下
  每次往返的调度延迟上升，形成 1.5–2× 的超线性变慢。
- **一多半语句是事务控制**（BEGIN/ROLLBACK/COMMIT = 230/轮）：每个 helper 自己开
  `AsyncSession`/事务，读-only 也 BEGIN→ROLLBACK。这是架构问题，不是 Pod/用户密度。
- 22 条/轮的 `FOR KEY SHARE` 是 SQLAlchemy 关系懒加载；15 条/轮是重复读
  chat_session/agent_definition/credential；19 条/轮是会话 seq 读改写。

**结论：根因是 per-turn DB 访问模式（语句数），不是单锁、不是事件循环、不是 Pod 数。**
修复目标：**478 → ≤150 条/轮**（优先砍事务控制与懒加载），则 1000 时语句率从 15.8k/s
降到 ~5k/s，宿主/DB 的调度延迟回到线性区。

**计数器实测归因（600×8，两轮一致；runtime 单 Pod ×8 折成集群）**：

| service / stage | 语句/轮 | 事务/轮 |
|---|---:|---:|
| runtime admission | 40.0 | 7.2 |
| runtime turn | 33.6 | 8.8 |
| runtime finalize | 20.8 | 0.8 |
| runtime event | 19.2 | 3.2 |
| runtime model | 16.0 | 1.6 |
| runtime bg:tail | 15.2 | 8.0 |
| runtime api | 10.4 | 5.6 |
| runtime poll | 4.0 | 1.6 |
| runtime bg:unlabeled | 4.8 | 4.0 |
| runtime 其他 bg | 2.4 | 0.8 |
| **runtime 合计** | **~166** | **~42** |
| console-platform | **26.9** | **8.1** |
| agent-worker | 0.5 | 0.4 |
| **合计（业务语句）** | **~194** | **~50** |

- 计数器只数 **SQLAlchemy 业务语句**；PGSS 的 478 还含 BEGIN/COMMIT/ROLLBACK
  与未覆盖路径，所以 478 与 ~194 不是同一口径。
- **没有单一大头**：runtime admission 40、turn 34、finalize 21、event 19、model 16、
  tail 15，console 27。要降到 150，需要多刀，不是一处。
- `finalize` 0.8 事务/轮但 21 条语句：单个终态事务里语句多；`tail` 8 事务/轮、
  1.9 条/事务：按 channel 的小读会话；`console` 3.3 条/事务：每请求多次 session/agent 读。

**修复顺序（先归因，再按会话数砍）**：

1. **按服务+阶段归因**：`before_cursor_execute` 计数器，label 固定闭集
   `service × stage`（admission/model/event/finalize/poll/**bg:***），
   同时数 **事务（BEGIN）与语句**，按 **每秒 + 每轮** 报；后台任务必须单独 label，
   不能算进 turn 路径。
2. **会话/事务复用**（第一刀，按归属最大者）：同一事实的读（候选、limits、memory head）、
   同一次受理的写复用同一 session；读-only helper 不再独立 BEGIN/ROLLBACK。
   **不可合并**：审计/计费/通知不能随业务失败回滚，应走批量/异步 writer
   （如审计并入 event batcher 的提交批次），而不是塞进业务事务。
3. **减少 INSERT/重复读**：事件批量已做；chat_session/agent_definition/credential limits
   同事务读一次；seq 批量分配后少回读。
4. P3 的 limits+clock/候选扫描合并，作为补充。
5. `FOR KEY SHARE` 不作为优化项（RI 触发器；减 INSERT 才降）。

**目标口径**：先到 **≤200 条/轮**（事务 115→~40，控制语句 ~95；业务 210→~100），
再看 ≤150；验收用 **PGSS calls/turn + transactions/turn + first-delta**，
不再用“加 Pod/调密度”作为结论。

### 0.37 第一刀：admission 读合并 + 归因修正（2026-10-28）

**改动（行为等价）**：显式会话的 `conversation + active_run` 合并为一条 SQL
（`load_explicit_conversation_with_active_run`），precheck 与 write 各用一次；
指针会话保持原路径。advisory lock、Console materials 前 rollback、错误码均未变。
测试：合并读 4 个用例 + 准入回归 39 passed。

**归因修正**：`create_task(_launch_execution)` 继承 `admission` 上下文，领取/执行早期
DB 语句被算进 admission。给启动任务显式打 `turn` 后：

| stage | 修正前语句/run | 修正后语句/run |
|---|---:|---:|
| admission | 45.5 | **29.4** |
| turn | 38–40 | **51.9** |
| event | 22 | 19.4 |
| finalize | 24 | 20.7 |
| model | 18 | 18.2 |
| bg:tail | 14–17 | 12.0 |
| api | 10–12 | 10.6 |
| **合计** | **~189** | **174** |

- **admission 合并本身只省 2 条读/轮**；45.5→29.4 主要是把启动任务的语句归回 turn。
- 该轮宿主过载（admission P95 1658、first-delta 4630、connections 101），
  first-delta 不可比；**语句/事务口径仍有效**。
- 结论：admission 不是单点大头；**turn 52 / admission 29 / finalize 21 / event 19 /
  model 18 / console 27 / tail 12** 都在同一量级，必须多刀。

**下一刀候选（按收益/风险）**：

1. **finalize（21/run，0.8 事务）**：单事务内 notification 多 INSERT、memory 两个 COUNT、
   events/submission；合并 INSERT、去 COUNT，保持终态原子性。预期 -5~8。
2. **event（19/run，3.1 事务）**：每事件 conversation last_seq 读回 + max(seq) 子查询；
   批量分配后减少回读。预期 -4~6。
3. **turn（52/run，16.5 事务）**：需要 turn 内部子标签（claim/load/update/activity/memory）
   才能定位；先加子标签，再砍。
4. **console（27/turn）**：同请求 session/agent/settings 合并读。
5. **tail（12/run，6.6 事务）**：同轮多 channel 读合并。

### 0.38 微优化结算：185 条/轮，距 150 还差 35（2026-10-28）

**已落地（全部行为等价）**：

| 刀 | 改动 | 语句收益 | 测试 |
|---|---|---|---:|
| 1 | admission 显式会话 `conversation + active_run` 合并 | -2 读/轮 | 合并读 4 + 准入 39 |
| 2 | finalize 两条事件 `append_many`（一次 seq 区间） | -1 UPDATE/轮 | durable deltas 10 |
| 3 | model `limits + clock` 合并为一条 SQL | -1/轮 | capacity reserve 14 |
| 4 | 归因修正：launch 任务 `turn`、turn 子标签 | 诊断 | counter 4 |

**最新 600 归因（语句/事务每 run）**：

| stage | 语句/run | 事务/run |
|---|---:|---:|
| turn（基础段） | 39.8 | 10.8 |
| turn:claim | 8.5 | 1.9 |
| admission | 27.8 | 1.9 |
| finalize | 23.9 | 1.0 |
| event | 22.3 | 3.6 |
| model | 18.0 | 2.0 |
| bg:tail | 15.1 | 8.3 |
| api | 11.1 | 5.2 |
| console（每 turn） | 26.9 | 8.1 |
| **合计（runtime 业务）** | **~185** | **~46** |

**结论**：

- 四刀合计只降 **~4 条/轮（189→185）**；单条 ROI 已到 1–2 条。
- 要到 **150** 还差 **35 条**，且剩余大头是 **turn 基础段 40、admission 28、finalize 24、
  event 22、model 18、tail 15、console 27** —— 没有单点 30% 的切口。
- 继续安全的路径只剩 **写入批量**（event flush 的 clock/锁、finalize 的 notification/memory
  INSERT/COUNT）和 **claim 去重**，每刀 2–5 条；**跨请求 session 缓存、拆终态事务、共享并发
  session 已明确排除**。
- **本地 15 核不是验证 150 条目标的环境**：即使降到 150，1000 用户的 SLO 仍受宿主/同机 PG
  约束；生产 16c 节点 + 独立 DB 才是验收环境。

**下一步选择**：

1. **继续安全批量**（event + finalize + claim，预计 185→170–175），每刀单独测试/A/B；
2. **停止微优化，转 staging/16c**，用当前 185 的版本测 1000/2000，看生产容量是否已够；
3. 若必须本地 1000 进 SLO，则需要结构性 spec（claim/event 事务边界或控制面出 PG），
   不属于“行为等价”范畴。

### 0.39 产品收口：会话上限/保留期与每用户 SSE（2026-10-28）

**本轮目标**：在没有 16c/staging 机器的条件下，把 webchat 的产品面收口——多会话可设上限、
未失效历史可继续、通知走每用户 SSE；容量结论继续只按聚合资源记账，不用“加 Pod/调密度”解释。

#### 已落地语义

| 能力 | 事实 |
|---|---|
| 会话上限 | `webchat.max_conversations_per_entry`（默认 50，范围 1–1000），按 `(tenant,user,agent,入口)` 统计 **ACTIVE 且未过期** 的会话 |
| 达上限 | 自动归档最旧且**无活跃 Run** 的会话（`ARCHIVED`）；历史行保留、保留期内仍可查看/继续 |
| 无空闲可归档 | 新建返回 409 `CONVERSATION_LIMIT_REACHED`，不静默清掉正在执行的对话 |
| 继续历史 | ARCHIVED 会话被继续时恢复 ACTIVE；若因此超上限，尽力归档另一条最旧空闲会话；其余活跃会话都有 Run 时允许暂时超出并留 warning，下一次新建收敛 |
| 会话保留期 | `webchat.conversation_retention_days`（默认 30，0 = 永不过期），以 `last_message_at`（NULL 回落 `create_time`）为准 |
| 过期读取 | 列表过滤；详情/消息/续聊统一返回 410 `CONVERSATION_EXPIRED`（同一判定 `conversation_policy.assert_conversation_live`） |
| SSE 前端 | `useNotifications` 新增可选 `stream` 端口；`ChatPage` 注入同源 **fetch 流**（`EventSource` 不能带 `X-Chat-Context`），完整 DTO 直收；断流才回退有界轮询，回前台重开 SSE，登出/身份边界关闭流 |
| SSE 后端 | runtime 提交后发布完整 `WebchatNotification` + watermark；gateway 每 Pod 一条 pubsub、按 tenant/user 路由；客户端建流前先做一次 REST 权威快照，gateway 先订阅，按 watermark/`snapshot=1` 决定是否回填（水位未知且无快照时按页有界追平），live 不回源 runtime |

**没有做的事**（防止把优化做成新语义）：

- 没有改表结构、API 字段、错误码封套或租户/鉴权语义；新增设置字段走既有 contracts schema 与
  Console 设置目录，新增两个稳定错误码登记进共享错误目录。
- 没有引入跨请求可变配置缓存；上限/保留期在业务边界取一次设置快照。
- 没有为了“上限”删除历史行；归档是软状态，保留期才是失效边界。

#### 成本记账（相对 §0.38 的 185 条/轮）

- **新建会话**：多一次 Console 设置快照读取 + 一条 scope advisory lock + 一条 ACTIVE count；
  达上限时多一条 `FOR UPDATE SKIP LOCKED` 候选查询与一条归档 UPDATE。新建不是每轮热路径。
- **历史列表/详情/消息**：每次多一次设置快照读取；列表在保留期非 0 时多一条 PG 时钟读取，
  并在原 list/count 同语句里加 `coalesce(last_message_at, create_time) >= cutoff` 谓词。
- **续聊/新 Run**：设置解析约 **16µs/次**（本地微基准），不产生额外 SQL；只有
  `conversation.status == ARCHIVED` 时才多 count/归档语句。
- **SSE**：live 帧直接携带完整 DTO，gateway 不按事件回源 runtime；建流才可能回填。

#### 验证

- 会话上限/保留期：`uv run pytest -q tests/agent_runtime/test_conversation_capacity.py` → **14 passed**
  （真实 PG 行 + 真实内部历史路由 + 真实 `/v1/runs` 续聊拒绝/恢复 + 全部忙碌时 409 拒绝）。
- 相关回归：`test_capacity_webchat_history_queries.py` 12 passed；`test_runs_api.py` +
  准入创建 + 会话核属 62 passed；contracts/catalog 166 passed；SSE 通知单测 16 passed。
- 前端：`npm run build`（含 `tsc --noEmit`）通过；`test_capacity_webchat_activity_ui.py` +
  `test_capacity_webchat_frontend_wiring.py` 23 passed；通知 fetch 流 3 passed；真实浏览器
  E2E `webchat` S-105 1 passed（重开页面收通知、定位结果、下载附件、标记已读、隐藏页暂停）、
  S-110 2 passed（侧栏标题/活动分组、打开 A 续聊归属 A、刷新恢复、首屏 50 条与超 200 条分页）。
- 容量：1000×1 SSE 为 **1000/1000、0% 错误、admission P95 264ms、first-delta P95 1612ms、
  notification P95 80ms**（建流前 REST 快照 + `snapshot=1`，SSE 不再逐流回填）；
  1000×8 SSE 为 **910/1000 用户、7259 完成、system error 1.52%、admission P50 3578ms、
  first-delta P50 12704ms、宿主峰值 13.43/15**。后者是本地 15 核 + 同机 PG + 2000 条 SSE 流的
  宿主上限读数，**不得写成生产容量结论**。

**复现入口**：`scripts/capacity/PLAYBOOK.md` §10.4 +
`tests/capacity/profiles/local-macbook-1000x{1,8}-sse.yml` +
`scripts/capacity/p1_1000_steady.py`（诊断口径，不是 staging 验收）。

**结论**：产品面的“多会话 + 未失效历史 + 每用户 SSE”已闭环；生产 1000×8 SLO 仍必须由
16c/staging（或等价独立 DB 环境）的 E-09 裁决。本地剩余墙仍是 §0.38 的
`persist ~2s + event_batch_wait ~0.5s + upstream ~1.5s` 稳态吞吐，不是本轮会话策略引入的。

### 1.1 当前是否达标

**本地热基座：400/600 绿；1000 功能绿、SLO 红；staging E-09 仍未执行。**

- 本地 8 Pod/4 keys/1 轮：400（105.8/1093.4）、600（135.9/1176.1）**SLO 绿**；
  800（331.8/1851.6）功能绿、first-delta 红；1000×1（1125/5827）0% 错误但差 2.25×/4.9×；
  1000×4 过载 3311/7.5%。**SLO 上限 600。**
- 12 Pod/8 keys/大池：负收益，不作为扩容方案。
- staging E-09（1000×8/600s）仍是唯一权威结论；本地只用于定位。
- 历史 100 用户基线：100/100 受理、无系统错误，但 admission P95 约 2.1–2.3s、
  first-delta P95 约 6.7–7.2s、idle-in-tx 峰值约 27–32（旧基座/单副本口径）。
- 400 用户 soak 曾失败（3130/3200 attempts），未作为通过证据。
- 历史 800 用户数据（改造前、非目标拓扑）不能作为当前容量结论。

### 1.2 当前主要瓶颈

> **2026-10-27 修订**：下面这份排序已被 §0 的实测推翻——首要瓶颈是 **runtime 单进程 CPU 饱和**，
> 事件写/锁/提交只是被它放大的现象。保留原文仅作诊断过程记录，选型以 §0.3 与 §7 为准。

按当前证据，**不是 DB 连接数、不是 CPU 打满、也不是模型 200 个 delta**，而是**应用热路径的串行化与事务放大**：

1. **事件写逐事件事务**：每个 canonical event 一次 `lock_execution` + `UPDATE conversation.last_seq` + INSERT + COMMIT + Redis wake。
2. **SSE 打开两次串行 Runtime 调用**：`get_run_scope` + `open_event_stream`，P95 合计约 1.8s。
3. **受理重事务**：`_create_run` 单事务写 Run/Snapshot/Submission/附件/事件/计数，P95 约 0.5–1.2s。
4. **模型容量每次查库/行锁**：极简模型下 `PooledModelProvider._call` 仍 P95 约 0.65s。
5. **终态收尾同步副作用**：终态事件、通知、memory job 在同一事务，P95 约 0.7–1.2s。

**一句话**：

> 一次用户请求被拆成太多独立的 DB 事务和串行 HTTP 往返；每个步骤几十到几百毫秒，100 并发下排队/锁/提交叠加，最终 first-delta 约 5s。

### 1.3 不建议先做的事

- 先上 PG 多主/Citus：当前写瓶颈是事务放大，不是单主写能力不足；多主还会破坏 canonical seq/幂等/事务语义。
- 先加 app pool/PgBouncer：实测 PgBouncer `wait 0us`，加大池反而让 admission/finalize 更慢。
- 用 pyinstrument 1ms 全栈 profile 当 SLO 数据：会改变时序并制造失败。
- 多 worker 直接开：后台循环、进程级预算、metrics 未处理会放大问题。

---

## 2. 当前实测指标

### 2.1 分层稳定性

| 档位 | 环境 | 结果 | 用途 |
|---|---|---|---|
| 15 用户 | GitHub runner | 全绿 | 当前 CI 稳定回归档 |
| 30 用户 | 同一 runner | admission P95 1246ms、first-delta P95 3536ms、idle-in-tx 26 | 红；本机同档绿，机器差异 |
| 100 用户 | 本地单机栈 | 100/100 受理，无系统错误；admission P95 2.1–2.3s、first-delta 6.7–7.2s、idle 27–32 | 功能可跑，SLO 不达标 |
| 400 用户 | 本地 soak | 3130/3200 attempts，失败 | 未通过 |
| 800 用户 | 历史、非目标拓扑、改造前 | 700/800 受理，285–583 完成，P50 13–18s / 36–44s | 仅历史诊断 |
| 1000×8 | staging | 未执行（E-09 planned） | 无结论 |

### 2.2 PgBouncer A/B（100 用户）

| 指标 | 直连 PG | PgBouncer |
|---|---:|---:|
| 受理 | 100/100 | 100/100 |
| 系统错误 | 0 | 0 |
| admission P95 | 2126ms | 2256ms |
| first-delta P95 | 6712ms | 7144ms |
| idle-in-tx 峰值 | 30 | 32 |

PgBouncer 自身统计：

```text
62 xacts/s
160 queries/s
xact 1.3ms
query 282us
wait 0us
```

**结论**：PG 连接复用不是 100 用户档瓶颈；PgBouncer 对后续多 worker/连接预算仍有用，但不是当前解药。

### 2.3 极简模型测试（1 个 delta、0 延迟）

目的：把模型探针从测量中移除，判断平台自身是否有瓶颈。

| 指标 | 本地 100 用户 |
|---|---:|
| 受理 | 100/100 |
| 完成 | 100/100 |
| 系统错误 | 0 |
| admission P95 | 1.60s（另一次 1.48s） |
| first-delta P95 | 4.87s（另一次 5.09s） |
| idle-in-tx 峰值 | 26–31 |
| commits/s | 约 59 |
| event rows/s | 约 9.9 |

**结论**：模型几乎零耗时，first-delta 仍约 5s；瓶颈在平台自身，不在 200 个模型 delta。

### 2.4 Run 时间线（正常池 20+10，直连 PG，极简模型）

| 阶段 / 函数 | P50 | P95 |
|---|---:|---:|
| `RunService.start`（受理总） | 671ms | 1110ms |
| `RunService._admit` | 665ms | 1107ms |
| `RunService._create_run` | 299ms | 500ms |
| `RunService.tail_run_events` | 311ms | 768ms |
| `WebchatStreamService.open`（Gateway SSE） | 1072ms | 1828ms |
| ├ `RuntimeClient.get_run_scope` | 400ms | 770ms |
| └ `RuntimeClient.open_event_stream` 进入 | 612ms | 1102ms |
| `WebchatPublicStream.frames` 首帧 | 134ms | 283ms |
| `PooledModelProvider._call` | 114ms | 654ms |
| `RunService._finalize_run` | 68ms | 736ms |
| `RunService._consume_executor` | 923ms | 4290ms |

说明：

- 这些阶段有重叠，不能简单相加，但每一段都是真实成本。
- `get_run_scope` + `open_event_stream` 是**串行**的；SSE 首帧必须等两者完成。
- `tail_run_events` 受 `stream_scope_gate`（上限约为 `db_pool_size + db_max_overflow - 1`）限制。
- `gateway.frames.first` 本身只有 0.28s，主要成本在打开阶段。

### 2.5 加大 app pool + PgBouncer（反面验证）

Runtime 池 60+20、Console 20+10、Worker 10+5，经 PgBouncer：

| 指标 | 正常池 | 大池 + PgBouncer |
|---|---:|---:|
| admission P95（trace） | 1110ms | 1667ms |
| `_create_run` P95 | 500ms | 1165ms |
| Gateway open P95 | 1828ms | 2040ms |
| `get_run_scope` P95 | 770ms | 940ms |
| `open_event_stream` P95 | 1102ms | 1163ms |
| `tail_run_events` P95 | 768ms | 578ms |
| `model_call` P95 | 654ms | 800ms |
| `_finalize_run` P95 | 736ms | 1170ms |
| `_consume_executor` P95 | 4290ms | 5506ms |
| idle-in-tx 峰值 | 26 | 52 |

**结论**：更大连接池会增加并发写事务/锁竞争，不是线性收益；必须减少事务与锁本身。

### 2.6 CPU / 鉴权

> **2026-10-27 复核**：这条“多数时间 CPU <50%”不成立于本次负载——100 用户突发窗口内，
> 一个 runtime 副本连续 14s 打满单核（见 §0.2/§0.4）。旧读数可能来自更长的平均窗口或不同的
> 采样口径；容量判断必须按**负载窗口内的进程 CPU**看，不能看均值。

- 100 用户负载期：Runtime/Console/Gateway 多数时间 CPU <50%，只有启动/短突发接近 1 核。
- 历史 800 用户目标拓扑：Runtime 4×939–951m、Gateway 991m、Console 932m；每 Pod 单进程上限约 1 核。
- Chat principal：Gateway P95 约 20ms、Console P95 约 7ms；100 用户档不是瓶颈，1000 用户再评估缓存。

---

## 3. 代码级瓶颈定位

### 3.1 事件写：逐事件事务

代码：

- `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`
  - `_consume_executor`
  - `_persist_event`
- `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py`
  - `EventWriter.append` / `append_many`

现状：

```text
每个 event:
  lock_execution
  UPDATE conversation SET last_seq = last_seq + 1 ... RETURNING
  INSERT canonical_event
  COMMIT
  EventTailHub.publish
```

问题：

- 1000 并发、300ms 合并窗口下约 3.3k commits/s。
- 每事件持 Run/Conversation 锁。
- `append_many` 目前只在受理时批量使用，delta 路径没有跨 Run 批量。
- 极简模型下 `_consume_executor` P95 仍 4.3s，说明成本在事件写/锁/提交，不在模型。

目标：

- 跨 Run 的 group commit：一个事务写多条事件、更新多个 conversation 的 `last_seq`。
- 每批一次 Redis wake。
- commits/s 从 3.3k 降到 100–300/s。
- 事件仍按 Run 有序；锁按 conversation id 排序，避免死锁。
- 可选目标架构：Redis Stream 实时 + 独立 Event Persister 异步批量写 PG；PG 仍是权威源。

### 3.2 SSE 打开：两次串行 Runtime 调用

代码：

- `apps/im-gateway/src/muad_im_gateway/application/webchat_streams.py`
  - `WebchatStreamService._open`
- `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`
  - `get_run_scope`
  - `open_event_stream`
- `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`
  - `tail_run_events` / `stream_scope_gate`

现状：

```text
Gateway.open
  → Runtime.get_run_scope         P95 770ms
  → Runtime.open_event_stream     P95 1102ms（含 tail + stream gate）
```

问题：

- 1000 条 SSE 打开时，每条流两次 Runtime 调用。
- `stream_scope_gate` 只有约 29，100 条流同时打开直接排队。
- `get_run_scope` 的归属信息在受理时已基本确定，重复查询价值低。

目标：

- 受理时签发短期 **stream ticket**：`{run_id, tenant, user, agent, exp, signature}`。
- SSE 打开只校验 ticket + 当前 CHAT principal，直接订阅事件总线/Redis Stream。
- 不再每条流查 `get_run_scope`。
- 打开延迟目标 <100ms；1000 条流可分片/分副本打开。

### 3.3 受理：重事务

代码：

- `RunService.start`
- `RunService._admit`
- `RunService._create_run`

现状：

- `_admit` 预检 + `claim_in` + 再次 resolve/active；有重复查询。
- `_create_run` 单事务写 Run、Snapshot、Submission、附件、memory inbound、canonical events、conversation 更新、计数。
- P95 `_create_run` 约 500ms；大池下 1165ms。

目标：

- 受理只保留 Run + Snapshot 必要写。
- 事件、通知、memory job 移出受理事务。
- precheck 与写事务重复查询合并。
- 用户/租户计数用 Redis 快路径 + PG 最终结算。
- 单次受理目标 <200ms（100 并发下）。

### 3.4 模型容量：每次调用查库/行锁

代码：

- `apps/agent-runtime/src/muad_agent_runtime/application/model_capacity_service.py`
  - `PoolLockGate`（默认 64）
  - `ModelCapacityService`
- `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`
  - `PooledModelProvider._call`

现状：

- 极简模型下 `_call` P95 约 654ms。
- 容量限额、预占、租约、结算都走 PG；100 并发时排队/行锁。
- 加大池后反而升到约 800ms，说明锁/竞争不是连接数。

目标：

- 限额短 TTL 缓存。
- 预占走 Redis/进程快路径，PG 只做最终结算/审计。
- 单次模型调用固定开销目标 <20ms。

### 3.5 终态收尾

代码：

- `RunService._finalize_run`
- `_finalize_failed`
- `write_run_notification`
- memory job enqueue

现状：

- 终态事件、Submission finalize、通知、会话活动、memory job 在同一事务。
- P95 约 736ms；大池下 1170ms。

目标：

- 终态与用户可见事件一个短事务。
- 通知/memory job 提交后异步（可靠队列/outbox）。
- 不要让后台副作用拖长终态事务。

### 3.6 其他

- `stream_scope_gate` 与 admission gate 都是进程级；多 worker 后要按进程重算。
- `process_admission_gate` 上限 = `min(64, pool+overflow)`；会吃满池，后台 claim/continuation 可能被饿死。
- `SseBufferLedger`、`BotSnapshotCache`、后台循环都是进程级；多 worker 需 leader election / 预算重算。
- `/metrics` 多 worker 需要 Prometheus multiprocess 模式。

---

## 4. 目标方案（1000 用户，不做兼容性折中）

> **2026-10-27 修订**：本节的“Redis Stream 数据面 + stream ticket + 容量 Redis 预占”在实测后
> 不再作为首选——PG 未饱和、提交成本可排除、ticket 不解决排队。目标方案以 §7 为准，本节仅保留
> 原始设想。

### 4.1 数据面

```text
Runtime 产出事件
  → Redis Stream（per run/conversation，实时、有序）
  → Gateway SSE 直接消费
  → Event Persister 批量写 PG（canonical/审计/重放）
```

- PG 仍是权威事实源；Redis 只做实时总线。
- PG 批量写：100–300 commits/s，而不是 3.3k/s。
- 断线重连：Redis 有则续读；超出保留窗口回 PG 按 cursor 重放。
- Redis 需要 AOF/Sentinel/Cluster，不能是单点。
- 事件表按时间/租户分区 + retention。

### 4.2 SSE

- 受理时签发短期 stream ticket。
- Gateway 校验 ticket + 当前 CHAT principal。
- 直接订阅 Redis Stream/Runtime 事件总线。
- 删除每条流的 `get_run_scope` 调用。
- 打开延迟目标 <100ms。

### 4.3 受理

- Run + Snapshot 一次短事务。
- 事件、通知、memory job 移出。
- precheck 合并、计数快路径。
- 目标 <200ms。

### 4.4 模型容量

- 限额缓存 + Redis 预占 + PG 最终结算。
- 目标固定开销 <20ms。

### 4.5 计算与部署

- Gateway/Runtime 先单 Pod 多 worker，再多副本。
- 后台循环 leader election（PG advisory lock / Redis lease）。
- `process_counts` 按真实 worker 数；PG/FD/SSE 预算重算。
- Console 多 worker 或拆通知/鉴权读路径。
- PgBouncer 2 副本；PG 主库 + 1 只读副本；不做多主。

### 4.6 单进程目标线

按 2 Gateway Pod × 2 workers、4 Runtime Pod × 2 workers 计算：

| 指标 | 单进程目标 |
|---|---:|
| SSE 并发 | ≥300 |
| 事件吞吐 | ≥1000/s |
| 受理 | ≥30/s，P95 <200ms |
| 模型调用固定开销 | <20ms |
| 批量提交 | 每进程 20–50 commits/s |

这些是 1000 用户除以 8 个进程后的最低要求，不是最终实测结论。

---

## 5. 压测方法

> 可复现的完整手册（命令、度量口径、读取分位数、A/B 与 CPU 采样配方、判读顺序、已知坑）见
> `scripts/capacity/PLAYBOOK.md`；本节保留原始方法记录。

### 5.1 前置条件

- **停 dev 服务**：`scripts/dev.sh stop`；检查 `pytest|uvicorn|vite` 残留。
- 独立 PG/Redis：acceptance 会建 `muad_acc_<uuid>` 空库 + 10–15 号 Redis DB，跑完 drop。
- 角色需要 `CREATEDB`；缺权限时用超级用户 `ALTER ROLE muad CREATEDB;`。
- 不要用共享 dev 库跑验收；不要删除 dev 数据。

### 5.2 标准命令

```bash
# 100 用户 smoke（S-16，本地回归）
make capacity-smoke
# = CAPACITY_LOAD_USERS=100 uv run pytest -q tests/acceptance/capacity/test_capacity_smoke.py

# 200 用户（make 默认档，非 1000 结论）
make capacity-load
# = CAPACITY_LOAD_USERS=200 uv run pytest -q -m capacity_load tests/acceptance/capacity

# 400 用户 soak（显式门禁）
make capacity-soak
# = MUAD_CAPACITY_SOAK=1 uv run pytest -q tests/acceptance/capacity/test_capacity_soak.py

# 1000×8 staging 目标拓扑（权威 SLO，E-09）
CAPACITY_LOAD_USERS=1000 uv run pytest -q -m capacity_load \
  tests/acceptance/capacity/test_capacity.py
```

Profile：

```bash
MUAD_CAPACITY_PROFILE=tests/capacity/profiles/agent-capacity-webchat.example.yml \
CAPACITY_LOAD_USERS=1000 uv run pytest -q -m capacity_load \
  tests/acceptance/capacity/test_capacity.py
```

注意：

- `capacity-smoke` 的阈值是本地 S-16 的 1000ms/2500ms；**不是** profile 的生产 SLO（500ms/1200ms）。
- 本地 1000 用户不可执行：本机 PG `max_connections=100`、Runtime 池 20+10、准入闸门 30；1000 用户必须 staging 目标拓扑。
- 任何本地数字都不得写成“1000 用户达标”。

### 5.3 PgBouncer A/B

本地 PgBouncer 部署在 `/Users/jahan/workspace/localdb/`：

```text
应用：localhost:6432
直连 PG：localhost:5432
pool_mode=transaction
max_client_conn=1000
default_pool_size/max_db_connections=88
max_prepared_statements=100
```

A/B：

```bash
# 直连 PG
DATABASE_URL='postgresql+asyncpg://muad:***@127.0.0.1:5432/muad' \
REDIS_URL='redis://127.0.0.1:6379/0' \
CAPACITY_LOAD_USERS=100 uv run pytest -q tests/acceptance/capacity/test_capacity_smoke.py

# PgBouncer
DATABASE_URL='postgresql+asyncpg://muad:***@127.0.0.1:6432/muad' \
REDIS_URL='redis://127.0.0.1:6379/0' \
CAPACITY_LOAD_USERS=100 uv run pytest -q tests/acceptance/capacity/test_capacity_smoke.py
```

注意：

- 迁移 Job 仍应直连 PG，不走事务池。
- 验收会建临时库；PgBouncer 配置需要 wildcard `*` 数据库条目，或预先建库。
- asyncpg prepared statement 与 PgBouncer 兼容性需实测。

### 5.4 极简模型场景

目的：把模型探针从测量中移除，单独测平台。

当前代码没有官方开关；诊断时使用临时 harness：

- 导入 `tests.acceptance.capacity.test_capacity_smoke`
- 覆盖：
  - `SMOKE_CHUNKS = ("ok",)`
  - `SMOKE_CHUNK_INTERVAL_MS = 0`
  - `SMOKE_MODEL_LATENCY_MS = 0`
- 调用 `_baseline_run(users=100)`，读取 `LoadReport`

**待细化**：把“极简模型”做成正式场景，例如：

```bash
CAPACITY_MODEL_MODE=minimal CAPACITY_LOAD_USERS=100 \
uv run pytest -q tests/acceptance/capacity/test_capacity_minimal_model.py
```

该场景的用途是**定位平台瓶颈**，不作为 1000 用户容量结论。

### 5.5 指标采集

必须同时记录：

| 类别 | 指标 |
|---|---|
| 客户端 | admission P50/P95/P99、first-delta P50/P95/P99、系统错误率、失败账目 |
| 连接 | PG total/idle/idle-in-tx/active、SQLAlchemy pool wait、PgBouncer wait |
| 事务 | commits/s、WAL bytes/s、event rows/s、pg_stat_statements top |
| CPU | 每单元每进程 CPU、RSS、FD、event loop lag（建议新增） |
| 模型 | probe max_concurrency、真实模型额度、首块延迟 |
| 流 | SSE 打开 P50/P95、首帧延迟、订阅数、慢订阅关闭数 |
| 业务 | Run 状态分布、RUN_BUSY、CURSOR_EXPIRED/AHEAD、终态缺失 |

推荐 run timeline 事件：

```text
submit_at
admission_start / admission_end
create_run_start / create_run_end
claim_start / claim_end
consume_start / model_call_start / model_first_chunk
first_canonical_event
first_public_sse
finalize_start / finalize_end
```

### 5.6 不要使用的测量方式

- **pyinstrument 1ms 全栈 profile 当 SLO**：
  - 实测会制造 `SSE_REJECTED`/`TERMINAL_MISSING`/`ADMISSION_REJECTED`
  - 有一次 Runtime 在抓 `/metrics` 前退出
  - 只能用于定性热路径，且应单独跑、单服务、低采样率
- **本机 1000 用户**：本机 PG 连接预算不够，结论无效
- **共享 dev 库**：任务 claim/投递无租户谓词，跨套件污染
- **只看 average/CPU**：必须看 P95/P99、idle-in-tx、pool wait、commits/s

---

## 6. 1000 用户验收口径

Profile SLO（`agent-capacity-webchat.example.yml`）：

- admission P95 ≤ **500ms**
- admission P99 ≤ **1500ms**
- framework first-delta P95 ≤ **1200ms**
- 系统错误率 ≤ **1%**
- recovery ≤ **60s**
- PG total ≤ **200**

场景：

- 1000 用户 × 8 轮 / 600s
- 20 chunks/s × 10s
- 通知 15s
- 真实入口代理、真实 Gateway/Runtime/PG/Redis、真实模型协议探针或明确标注的受控探针
- 记录 profile hash、拓扑、副本数、进程数、PG 预算

必须区分：

| 层 | 证明什么 | 不证明什么 |
|---|---|---|
| 100 用户 smoke | 本地回归、错误/终态 | 1000 用户 |
| 200/400 | 阶梯趋势、soak | 1000 用户 |
| 1000 staging | 生产 SLO | 真实模型额度/企微实网 |
| E-09 manual | 发布门禁 | 本地 |

---

## 7. 最终修复方案（2026-10-27 实测修订版）

> 本节取代原“优化顺序”。依据见 §0：**单进程 CPU 先到顶，PG/连接池/提交成本都不是限制**；
> 连接池、PgBouncer、`synchronous_commit`、stream ticket、容量 Redis 预占、chunk 管线瘦身、
> 轮询改事件驱动在实测口径下都不成立或不值得先做（§0.8）。

### P0-1：CPU 归因（✅ 已完成，2026-10-27）

已完成两轮：25 用户 pyinstrument + cProfile 交叉（§0.6），100 用户阶梯 + 分段账（§0.8），
配方与 profiler 坑见 `PLAYBOOK.md` §7.1。执行前必须知道的三条：76% 样本落在 SQLAlchemy greenlet
切换（C 层，不是空闲）；cProfile 的 per-call 时间会被 greenlet 放大（只看排序）；只有单副本
吃满、另一副本空闲（请求分布不均）。

**chunk 管线 vs DB 路径已裁决**：chunk 管线每轮 1.35ms（0.9% 单核/进程），DB 事件路径是主成本。
剩余归因只差 staging 上的 greenlet/asyncio 内部分离（§0.7）。

### P0-2：修复顺序（2026-10-27 夜修订）

1. **`process_settings()` 进程级缓存（✅ 已完成，2026-10-27）**
   - 实测 `SharedSettings()` 1.5–3ms/次、57 个调用点；100 用户 profile 中
     Settings/dotenv/os.environ 合计约 30% CPU。
   - 已落地：`muad_common.process_settings()` + Runtime/Console 热路径替换；
     全量 runtime 测试 1084 passed（§0.9）。
2. **每流成本合并（✅ 第一版已完成，2026-10-27）**
   - tail：`EventTailHub` 改为进程级批量水位补查（一条 `IN (...)` 覆盖全部活跃会话），
     订阅者超时只产出空 caught_up 批、不再每流查库；
   - cancel：`CANCEL_POLL_INTERVAL_SEC` 0.25s → 1.0s（先降 4× 定时器/Redis 探测）；
   - SSE 每流重复投影尚未动（100 用户占比 1.5%，事件驱动、1000 下只 1.33×）。
   - 100 用户复测：admission P95 **641ms**、first-delta P95 **1879ms**（真正 2 副本分摊）。
3. **Console 单 Pod × 4 worker（✅ Stage 1 已完成，2026-10-27）**
   - 验收栈支持 `process_counts`（uvicorn `--workers`）与 `PROMETHEUS_MULTIPROC_DIR`；
     1000 稳态 8 轮/600s 下 Console 请求 ~65 rps（chat-principal 37.7 rps）由 4 worker 分担。
   - 效果：完成数 756 → **5331**，系统错误 19.6% → **8.56%**；Console 不再是墙。
   - 结果仍失败：PG `max_connections=90/100`、`idle-in-tx=88`、commits 324/s、
     persist 873s；first-delta P95 32.6s。墙转移到 PG/事件写（§0.11）。
4. **Model capacity/dispatch（2026-10-28 实测修订，§0.17/§0.18）**
   - pgss steady 窗口：`model_capacity` upsert 86.6s、dispatch CTE 52.2s、两条 inflight 更新
     68.0s，合计约 **207s**；事件 INSERT+conversation UPDATE 合计仅 ~5s。
   - **第一刀（删 release ensure）实测负收益，已回退**：同 fast 300s A/B 下容量行 SQL
     60.0→88.1ms/次调用，完成 2583→2494；`transactionid` 等待是排队节流点。见 §0.18。
   - **renew 批量化实测负收益，已回退**（§0.19：多行 run 锁与 SSE/事件路径 convoy，
     完成 2583→2010、SSE 拒绝 89→727）。
   - D2 已按生产节拍 60/20 重跑（§0.21）：renew 跌出 top20，容量 SQL **243s** 是唯一主墙；
     release 路径（ensure ~50s + UPDATE 48.8s）约 **99s**。
   - 多 Key 实测（§0.22）：4 keys 把容量 SQL 243s→25s、完成 2719→3311、
     错误 13.4%→7.5%；8 keys 不改善且引入 500。**4 keys 是甜点**；容量行已不是墙，
     Batch D release 批量化只对 1-key 有意义，优先级下调。
   - 阶梯（§0.23）：400 达标、600 超标、800 延迟越线；SLO 400 已验证、500 插值。
     下一刀顺序：`SELECT 1`/commit/`sv_wait` 决胜测量 →
     `count_open_runs` 部分索引+有界存在性 → capacity `ensure+admit` 单条 upsert
     （不是删 ensure，Batch A 已证否）→ 定位 K8 500 → 12×7 Pod/池 → staging E-09 权威。
   - reserve lazy create（Batch B）待重估：同样删排队点，必须先证明替代节流或 A/B 无回归。
   - limits 缓存不做（会破坏 `MODEL_DISABLED` 立即生效）；clock 不缓存（B-235）；
     合并 `_select`+`reserve`、dispatch state 缓存往后放（锁耦合/stale 成员风险）。
5. **事件 batch 解耦会话（第二刀）**
   - 实测平均 **2.77 个会话/批**，52% 批次 >1 会话；跨会话队头阻塞真实存在，但事件 SQL
     总时间远小于 model capacity。按会话分组提交，或 `SKIP LOCKED` + 整会话 requeue（保序）。
   - 100ms 窗口已回退；默认 10ms。
6. **Admission/finalize 语句削减（不先做 outbox）**
   - `counts` 不是租户计数锁：`count_open_runs` 是 O(租户 Run 数) 的实时 count
     （200 参照 0.66ms，但会随历史增长）；改有界存在性检查 + 部分索引。
   - USER 计数改单条 upsert/条件更新；finalize 先合并 activity/seq、memory COUNT 换水位、
     通知语句合并；outbox 放到最后。
7. **缓存/推送降 Console QPS（Stage 2，可选但推荐）**
   - 通知推送去掉 1000/15s ≈ 67 rps；chat-principal 短 TTL + 撤销版本可再压 37.7 rps 的大头。
   - 目标：Console 请求从 ~65/s 降到 ~30/s，给 4 worker 留出 2× 余量；不是当前第一墙。
8. **Runtime 多 worker / 多副本 + leader election（HA 与 staging 前置）**
   - 单进程约 60–70 条并发流即饱和；目标每进程 ~380–500 条，是饱和点的 6–8 倍。
   - 必须完成：进程级闸门与预算重算（`process_admission_gate`、`stream_scope_gate`、
     `SseBufferLedger`、`BotSnapshotCache`）、后台循环 leader election、Prometheus multiprocess、
     `process_counts` 与 PG/FD 预算重算（§3.6 清单仍然有效）。
   - 验收栈必须使用真实 round-robin 池（本次已修）；否则多副本数字无效。

**已证否、不要先做**：

- 每 chunk 管线瘦身（1.3ms/轮，最坏 7.5–8.2ms/轮）；
- `QUEUE_POLL_SEC` 0.05→0.5（CPU 88.2%→88.3%，延迟略升）；
- 加大连接池/PgBouncer/关闭 `synchronous_commit`（§0.3）；
- 把 stream ticket/Redis Stream 作为第一刀（未证收益，先做每流成本与事件批量）。

### P1：复测后的残余

- **受理**：materials 仅 45ms，precheck/claim/write 的 300ms 级是 CPU 排队后的表现；
  CPU 优化后复测再决定是否动结构。不得把 Console 调用塞回写事务（会破坏“网络不持连接”）。
- **SSE 打开**：先给 gateway 侧同口径分段计时后复测；若仍有独立成本，优先“合并端点/短 TTL
  缓存（scope 不可变）”，**不做**签票（ticket 解决不了排队，反而引入密钥/TTL/重连签发）。
- **容量路径**：1000 用户稳态 ~13 次模型调用/s，无 Redis 预占的必要；`candidates/reserve`
  的 200–400ms 随 CPU 优化一并复测。

### P2：数据面扩展（按需）

- PgBouncer/只读副本/事件表分区/retention：等进程数上去、按 `pg_connection_budget` 评估；
  本轮实测 PG 远未饱和，不做 Citus/分片。

### 每步的验收口径

- 一律在 100 用户 smoke 上做 before/after，报告同口径五项：admission P95、first-delta P95、
  负载窗口内**每进程 CPU**、`db_pool_checkout` P95、`run_persist_event_seconds`/tail wakeup 速率。
- CPU 目标：负载窗口内单进程 <70%。
- 1000 用户仍由 staging E-09 裁决；本地数字不得写成 1000 达标。

---

## 8. 待其他 Agent 细化的事项

> 以 §7 为准。⛔ = 已被 2026-10-27 实测降级/取消；🆕 = 实测新增的必做项。

- [x] 设计并实现跨 Run 事件 group commit（含锁顺序、幂等、seq 分配、测试）——2026-10-27 完成，1085 runtime 用例通过；10ms/32 复测未破墙，见 §0.13
- [x] 🆕 事件 batch 分段测量（10ms vs 100ms）——2026-10-27 完成：lock 74–80%，INSERT/COMMIT 仅 10%；100ms 负收益，见 §0.15
- [ ] 🆕 事件 batch 锁优化：`FOR UPDATE SKIP LOCKED` + 未锁条目回队列/短退避 + 小 `lock_timeout`；先拆 conversation/run/continuation 锁等待
- [ ] 🆕 lease renew 与事件 batch 的 run 行锁隔离（renew 225–339ms，100ms 下 +50%）
- [ ] ⛔ Pod 级/分片 writer——锁等待未降前不做，见 §0.15
- [x] 🆕 每流成本合并第一版：tail 进程级批量水位补查 + cancel 轮询 0.25s→1s——2026-10-27 完成，见 §0.10
- [ ] 🆕 每流成本合并剩余：cancel 改共享订阅（去掉每 Run 1s 定时器）、SSE 帧只编码一次
- [x] 🆕 Console 单 Pod × 4 worker（Stage 1）——2026-10-27 完成；完成数 756→5331、错误率 19.6%→8.56%，但 PG 成为新墙，见 §0.11
- [ ] 🆕 Stage 2：通知推送 + chat-principal 短 TTL 缓存，把 Console ~65/s 降到 ~30/s
- [ ] ⛔ 设计 stream ticket 契约（签名、TTL、撤销、跨副本校验）——收益未证，先合并端点/短 TTL 缓存
- [ ] ⛔ SSE 直连 Redis Stream 的游标/重放/保留窗口设计——PG 未饱和，不引入双源
- [ ] 受理事务瘦身与 snapshot 延迟/异步的边界——CPU 优化后复测再定
- [ ] ⛔ 模型容量 Redis 快路径与 PG 结算的一致性——13 次调用/s，无收益
- [ ] Runtime 多 worker 后台循环 leader election 方案——1000 staging 前置
- [x] 🆕 Prometheus multiprocess（Console 验收栈）——2026-10-27 完成（`PROMETHEUS_MULTIPROC_DIR`）；生产清单待接线
- [x] 🆕 CPU 归因 profile（单进程低用户）与 top-10 热点清单——2026-10-27 完成，见 §0.6
- [x] 🆕 每 chunk 管线成本基线（CPU/chunk）——2026-10-27 完成，1.35ms/轮，**证否**，见 §0.8
- [x] 🆕 25/50/100 阶梯与 SLO 拐点——2026-10-27 完成，单进程约 60–70 流饱和，见 §0.8
- [x] 🆕 `QUEUE_POLL_SEC` A/B——2026-10-27 完成，CPU 无收益，**证否**，见 §0.8
- [x] 🆕 `SharedSettings()` 进程级缓存——2026-10-27 完成；真正 2 副本分摊后 100 用户 admission P95 641ms、first-delta 1879ms，见 §0.10
- [x] 🆕 tail 唤醒合并——2026-10-27 完成，单条批量水位查询覆盖全部活跃会话，见 §0.10
- [ ] 🆕 `database_now` 锁前取时修正；折进锁后写语句（每事件少 1–2 次往返，须补 B-235 竞争测试）
- [ ] 🆕 `StateGraph.compile` 按 Agent 快照缓存（每 Run 1.8%）
- [ ] 🆕 `ModelDispatchStore.database_clock` 合并/缓存（每次模型调用 3.4%）
- [ ] 🆕 受理 `find_replay_in` / `_create_run` 重复查询合并（9.7%）
- [x] 🆕 副本负载均衡：验收栈已加真实 runtime round-robin 池；此前“副本不均”读数作废，见 §0.10
- [x] 🆕 1000 本地 Stage 1 复测（1 Console×4 worker + 8 runtime + PgBouncer）——2026-10-27 完成，PG 是新墙，见 §0.11
- [ ] 官方极简模型场景 `test_capacity_minimal_model.py`
- [ ] 永久 run timeline metrics（替代临时 tracer）——分段计时已常驻，剩余为 timeline 事件
- [ ] 1000 staging E-09 执行清单与证据模板（事件 batch + Stage 2 后重跑）
- [ ] 400 soak 失败原因复核（3130/3200 attempts）

---

## 9. 参考数据文件

诊断时临时数据（可能被清理）：

- `/tmp/muad-trace2/`：正常池 run timeline
- `/tmp/muad-trace3/`：PgBouncer + 大池 run timeline
- `/tmp/muad-minimal-trace2.log`、`/tmp/muad-minimal-trace3.log`
- `/tmp/muad-capacity-profile*.log`
- `/tmp/muad-cpu*.log`
- 2026-10-27 复测：`/tmp/p0-run{1,3,4,5}-synccommit-{on,off}.log`、`/tmp/p0-{on,on2,off}.json`
  （原始 metric 差量与 idle-in-tx 采样）、`/tmp/p0-cpu-{on,off}.log`（1s 进程 CPU 采样）、
  `/tmp/muad_smoke_report.py`（仓库外 pytest 取数插件，内容已固化进 `PLAYBOOK.md`）

临时 tracer 已从仓库删除；不要把 `sitecustomize.py` 留在仓库。永久方案应做成正式 metrics/log span。
