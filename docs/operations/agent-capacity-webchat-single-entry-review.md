# 单 Console / 单 Gateway 容量复测（2026-10-21）

本文件是[800 并发突发复核](agent-capacity-webchat-burst-review.md)的更正数据：那一轮跑在
**4 个 Console + 4 个 Gateway** 上，违反目标架构（Console/Gateway 各 1 Pod）。本轮把集群固定为
**Console 1 / Gateway 1 / Runtime 4 / Worker 3**，先纠正因此暴露的容量缺陷，再按 200/400/800
三档复测。

## 测量条件

- 拓扑：`check_agent_capacity_deployment.py --live-namespace muad-cap` 每轮前核对期望/实际/就绪
  副本、Pod 与 HPA；四个单元的探针预算均为 5s/10s/3 次（见突发复核的探针时间线）。
- 数据：用重建后的专用测试库（scale 到 0 → `pg_dump --data-only --schema=control` → drop/create →
  `alembic upgrade head`（0027）→ 回灌 control 种子 → scale 回 4/3/1/1）。重建后先跑 200/400/800
  建立本拓扑基线，再按下面两项改动逐项复测（每档至少一轮，噪声约 ±10–20%）。
- 负载：每用户 1 会话 1 任务，零 ramp，8 个驱动进程；模型为受控 HTTP 探针（首块 500ms），工具
  调用真实 `execute_skill`（Skill 内睡眠 50ms）。入口走 ClusterIP。
- `ADMISSION_REJECTED` 里固定有 8 条是驱动进程各自的非法会话探测（预期边界拒绝），与正常用户轮次
  分开核对。

## 发现 1：单 Gateway 的 egress 池不足（874 次 PoolTimeout）

把 Gateway 从 4 Pod 收到 1 Pod 后，400/800 档出现大量 500：

| 800 用户（egress 池 64） | 数值 |
|---|---|
| `gateway_transport_failure` | 874 次，全部 `pool="egress"`、`error="PoolTimeout"` |
| 建会话失败（`/webchat/entries/*/conversations`） | 601 次 → `CONVERSATION_REJECTED` |
| 提交消息失败（`/webchat/entries/*/messages`） | 273 次 → `ADMISSION_REJECTED` |
| 400 档结果 | 344/400 受理（56 次会话创建被拒） |
| 800 档结果 | 255/800 受理，0 完成 |

原因：Gateway 的 `EGRESS_HTTP_MAX_CONNECTIONS` 只有 64，而单 Pod 下每个用户建会话、提交消息
各占一条 Gateway→Runtime 连接；池满后多出的请求按 `HTTP_POOL_TIMEOUT_SEC=5` 排队超时。4 Pod 时
每 Pod 只有 200 用户，64 条连接刚好没被打穿，这个缺陷因此被掩盖。

修正：egress 池按目标并发定容（`workload.users × active_runs_per_user`）→ **1024 连接 /
256 keepalive**（typed 默认同步改，四个清单一致）；发布预检新增 `outbound-concurrency`：
`EGRESS_HTTP_MAX_CONNECTIONS × max_replicas` 必须 ≥ 目标并发，否则阻断发布。修正后 400/800 的
PoolTimeout 归零。

## 发现 2：单 Console 进程的每请求固定开销

egress 修好后 800 档仍有 799/800 且准入 P50 16.1s，慢在 `materials_ms`（P50 4.5s）：运行时在
受理边界要打 3 次 Console（定义 ∥ 设置快照，再取凭据）。实测 Console 单次 `resolve-definition`
在 Pod 内是 **3.8–4.8ms**（其中 `SharedSettings()` 构造占 1.45ms：每次都要读环境与 `.env`），
800 并发下 Console CPU 贴着单进程上限（~940m，容器上限 2 核但 Python 单线程），pg 活跃会话只有
个位数——瓶颈是事件循环里的固定开销，不是数据库。

修正（两项）：
1. **启动设置复用**：`lifespan` 建一次 `SharedSettings` 放进 `app.state`，内部端点用它做服务身份
   校验，不再逐请求重建（Pod 内实测 4.8ms → 3.8ms/请求）。
2. **受理合并取数**：新增 `POST /internal/runtime/admission-materials`，一次请求返回定义、平台
   设置快照与当前认证值（与三个单点端点同一实现）；运行时 `RunService` 在一次调用里取齐材料，
   摘要模型（压缩启用时）仍按原路径单独解析。

## 发现 3：批量化事件写入的 `run.created` 事件类型回归

上一轮把受理事务里的 `run.created` 与 `run.stage` 合并为一次 `last_seq` 分配后，`run.created`
的业务事件类型被写成流类型 `"run.created"`（应为 `"RUN_CREATED"`）。执行段按业务类型判重
（`_start_segment_event`），找不到已有行又写了一条 ⇒ 同一 Run 出现两条 `run.created`
（`tests/agent_runtime/test_runs_api.py` 实测）。已改为取 `STREAM_BUSINESS_TYPES[RUN_CREATED_EVENT]`。

## 结果

三轮配置（同一拓扑、同一重建库，逐项叠加改动）：

| 负载 | 配置 A：egress 64（未修） | 配置 B：egress 1024 | 配置 C：+ 设置复用 + 合并取数 |
|---|---|---|---|
| 200 | — | 200/200，准入 5.09s，首帧 12.53s | **200/200，准入 4.30s，首帧 10.86s** |
| 400 | 344/400（56 建会话被拒） | 400/400，准入 8.51s，首帧 22.85s | 349–391/400，准入 7.6–11.4s，首帧 22.1–32.2s |
| 800 | 255/800，0 完成 | 799/800，准入 16.13s，首帧 51.21s | 700/800 受理、285–583 完成，准入 13.3–18.3s，首帧 35.7–44.1s |

（每格是准入 P50 / 首帧 P50；800 档的完成数波动很大，见下。）

## 新的硬约束：单进程 CPU

配置 B/C 的每一轮都用 `kubectl top` 采样所有 Pod。800 档峰值：

| 单元 | 峰值 CPU | 容器 CPU 上限 | 说明 |
|---|---|---|---|
| agent-runtime ×4 | 939–951m | 4 核 | 每个 Pod 都是单进程 uvicorn，只能用 1 核 |
| im-gateway ×1 | 991m | 2 核 | 同上 |
| console-platform ×1 | 932m | 2 核 | 同上 |
| agent-worker ×3 | 457–494m | 4 核 | 任务侧未饱和 |

400 档同样是 runtime 4×~940m、gateway 945m、console 670m。也就是说：**在 Console/Gateway 各 1 Pod
的目标拓扑下，四个运行中单元的每个 Pod 都跑在单进程 uvicorn 上（Dockerfile 的 CMD 没有 `--workers`），
每 Pod 至多 1 核；800 个并发 Run 的 CPU 需求超过这 6 个核能提供的量**。这解释了为什么完成数会随轮次
在 285–799 之间大幅波动（取决于 CPU 争用与驱动进程调度），而不再是 PoolTimeout 或连接池这类确定性错误。

已消除的确定性缺陷（本轮修复并复验）：
- egress 池 64 → 1024：`PoolTimeout` 归零；
- console DB 池 20+5 → 100+25：console 侧 `QueuePool ... timed out` 归零（800 档仍会因 CPU 排队，
  但不再有池超时 500）；
- 受理合并取数：空闲态单请求 6.44ms（原三次合计约 11.4ms），200 档准入/首帧分别改善约 15%。

## 下一步（未实施）

要在**不改拓扑**的前提下继续提升 800 档完成率，唯一剩下的杠杆是**每 Pod 多进程**（uvicorn
`--workers`），并同步重算预算：现在 runtime 每 Pod 400 条 PG 连接 × 4 Pod = 1600（PG 上限 2000），
如果每 Pod 改 2 个 worker，就得把每进程池降到 ~180（4×2×180=1440），Console 的 100+25 也要按
2 worker 折算。多进程与多 Pod 的安全前提相同（执行归属来自 PG claim、后台循环用 SKIP LOCKED /
幂等 CAS），现有架构支持；但 `process_counts` 是容量 profile 的声明项，需要按新配置重算
PG/FD 预算后再压测验证。

