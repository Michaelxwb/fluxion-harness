# scripts/capacity — WebChat 容量压测、诊断与判读总入口

本目录是容量压测的唯一入口：脚本、可复现手册、诊断结论、报告口径与验收映射都在这里。

## 文档地图

| 文件 | 用途 |
|---|---|
| `README.md` | 总入口：快速开始、脚本清单、基线、判读顺序、诚实边界 |
| `PLAYBOOK.md` | 可复现手册：前置纪律、分层矩阵、度量口径、A/B 配方、CPU 采样、已知坑 |
| `ANALYSIS.md` | 2026-10-27/28 诊断与修复结论（§0 实测 + §0.39 产品收口） |
| `REPORTING.md` | 证据包、容量报告、配置 hash 与副本建议公式 |
| `ACCEPTANCE.md` | `agent-capacity-webchat` 分层验收映射与未验证边界 |

## 30 秒快速开始

```bash
# 1) 停 dev 服务（必须：--reload 的 Worker 会抢走本租户任务）
scripts/dev.sh stop

# 2) 100 用户 smoke（独立临时库 + Redis 10–15 号，跑完自动 drop）
make capacity-smoke

# 3) 看结论：客户端 P50/P95、PG 速率、idle-in-tx；细分见 PLAYBOOK §4/§5
```

跑完把 dev 服务拉回来：`scripts/dev.sh start`。完整前置、分层矩阵与自定义规模见
`PLAYBOOK.md` §0–§2。

## 最新基线（2026-10-29，本地 15 核）

拓扑：MacBook Pro M5 Pro（15 核 / 48GB）+ 同机 PG/Redis/PgBouncer；Console 单 Pod × 4 worker、
8 个 runtime、2 个 gateway、4 个模型 Key（总 2000 槽）。这是**诊断拓扑，不是 staging**。
profile：`tests/capacity/profiles/local-macbook-1000x1-sse.yml` / `...1000x8-sse.yml`。

| 指标 | 1000×1 | 1000×8 |
|---|---:|---:|
| 拿到 Run 的用户 / 完成 | **1000 / 1000** | 910 / 7259 |
| 系统错误率 | **0%** | 1.52% |
| admission P50 / P95 | 146ms / **264ms** | 3578ms / 12399ms |
| first-delta P50 / P95 | 1328ms / **1612ms** | 12704ms / 27191ms |
| notification P50 / P95 | 42ms / **80ms** | 3862ms / 11736ms |
| PG 连接峰值 / idle-in-tx | 58 / 35 | 92 / 75 |
| 宿主峰值 | — | 13.43 / 15 |

- 1000×1：功能/错误率/admission 达标，**first-delta 1.61s 仍超 1.2s SLO**。
- 1000×8：宿主 13.43/15，runtime 稳态墙是 `persist ~2s + event_batch_wait ~0.5s + upstream ~1.5s`；
  **不得写成生产容量结论**。
- 复现命令与判读见 `PLAYBOOK.md` §10.4；逐项诊断过程见 `ANALYSIS.md`。

## loadgen.py — 对**已部署**栈发真实负载

`make capacity-smoke` 自己起四单元栈；本工具连已在跑的栈（staging / k8s / `scripts/dev.sh start`），
复用 `tests/capacity` 的真实驱动与度量口径，产出 admission/first-delta 分位与失败账目。

```bash
uv run python scripts/capacity/loadgen.py --config /tmp/load.json
uv run python scripts/capacity/loadgen.py --config /tmp/load.json --require-slo --json-out /tmp/report.json
```

配置示例（**凭据放仓库外**，不要提交）：

```json
{
  "base_url": "http://127.0.0.1:8003",
  "entry_id": "<uuid>",
  "runtime_url": "http://127.0.0.1:8001",
  "internal_token": "<internal service token>",
  "database_url": "postgresql+asyncpg://muad:***@127.0.0.1:5432/muad",
  "tenant_id": "<tenant>",
  "users": [{"user_id": "<uuid>", "token": "<chat token>"}],
  "scale": {"users": 100, "ramp_sec": 10, "steady_sec": 120, "turns_per_user": 1}
}
```

- `users`（给 Token，工具自己换取会话）与 `sessions`（直接给 cookie+context）二选一；
  每个虚拟用户必须一份主体，数量不得少于 `scale.users`。
- `--require-slo` 时按 `--profile` 的 SLO 判定，任一失败退出码 3。
- 可选 `unit_targets` 给每个单元的 `/metrics` 地址；多副本写成数组，驱动逐副本抓取后汇总，
  不能只填一个副本（否则计数器增量会少算）。
- 本工具会写真实负载（真实 Run），只对测试栈使用。

## p1_1000_steady.py — 本地 1000 用户稳态诊断插件

本地 15 核 + 同机 PG/Redis 的 1000×1/×8 SSE 复现；**不是 staging 验收**。完整拓扑、结果与坑见
`scripts/capacity/PLAYBOOK.md` §10.4。

```bash
# 1000×1 SSE（profile 已入库；8 轮换 ...1000x8-sse.yml）
PYTHONPATH=scripts/capacity \
EVENT_BATCH_WINDOW_MS=10 MUAD_NOTIFICATION_TRANSPORT=sse \
CAPACITY_LOAD_USERS=1000 \
MUAD_CAPACITY_PROFILE=tests/capacity/profiles/local-macbook-1000x1-sse.yml \
MUAD_PGBOUNCER=1 MUAD_MODEL_SLOTS=2000 MUAD_MODEL_KEYS=4 \
MUAD_PGSS_RESET=1 MUAD_PROD_CADENCE=1 MUAD_LOOP_LAG=1 MUAD_GC_PROBE=1 \
MUAD_SMOKE_METRICS_OUT=/tmp/1000x1.json \
uv run pytest -q -s -p smoke_report -p p1_1000_steady \
  tests/acceptance/capacity/test_capacity.py::test_s01_thousand_users_admitted_visible_and_bounded
```

- `MUAD_MODEL_SLOTS=2000` 会把测试常量同步到 2000，最后一条“凭据 16 槽”断言在诊断口径下会红；
  正式 E-09 不加载该插件、保持 seed 16。
- `MUAD_PGBOUNCER=1` 走 6432；`MUAD_PROD_CADENCE=1` 用生产 lease/heartbeat/reaper 节拍；
  `MUAD_MODEL_KEYS=4` 是本机甜点；`MUAD_PGSS_RESET=1` 依赖本机 `local_postgres` 容器。
- SSE 驱动会在建流前做一次 REST 快照并发送 `snapshot=1`，与浏览器前端同形；不要用
  `after_seq=0` 直接建流去测“空回填”成本。

## diag.py — 诊断子命令

```bash
# 从累计桶读分位数（默认读本机 runtime；--family/--stage 可重复）
uv run python scripts/capacity/diag.py metrics --url http://127.0.0.1:8001/metrics
uv run python scripts/capacity/diag.py metrics --stage persist --family run_consume_seconds

# 采样本栈进程 CPU（Ctrl-C 结束打印 max/p90/mean；--out 保留原始行）
uv run python scripts/capacity/diag.py cpu --out /tmp/p0-cpu.log

# commits/s 与 idle-in-tx 的 query/年龄
uv run python scripts/capacity/diag.py pgsample --interval 0.5

# synchronous_commit A/B（改角色级设置，只影响新会话；跑完 reset）
uv run python scripts/capacity/diag.py sync-commit off
uv run python scripts/capacity/diag.py sync-commit reset
```

## chunk_bench.py — 每 chunk 管线 CPU 微基准（不启栈）

真实 `OpenAICompatibleProvider._parse_stream` → `DeltaWindow` → `_ExecutorEventEmitter` 有界队列 +
内存消费者，喂合成 OpenAI SSE 帧。输出 `parse_only` / `parse_window` / `emitter_realistic` /
`emitter_flush_each` 四个场景的 µs/chunk、ms/轮，以及目标拓扑（1000 用户 × 8 轮 / 600s、
2 个 runtime 进程）下的每进程 CPU 折算。

```bash
uv run python scripts/capacity/chunk_bench.py
uv run python scripts/capacity/chunk_bench.py --chunks 200 --repeats 20 --json-out /tmp/chunk.json
```

用途：判断"chunk 管线"与"每事件 DB 路径"谁先修。2026-10-27 基线：realistic 6.76µs/chunk、
1.35ms/轮（0.9% 单核/进程），结论是 DB 路径优先。

## smoke_report.py — smoke 阶梯取数（pytest 插件）

在测试函数执行前读取 `smoke_run`，打印一行 `SMOKE-METRICS`，并把完整 JSON（含
`unit_metric_deltas` 的 runtime 分段指标）写到 `MUAD_SMOKE_METRICS_OUT`；**SLO 断言失败也照样落盘**，
用于 25/50/100 阶梯。

```bash
PYTHONPATH=scripts/capacity MUAD_SMOKE_METRICS_OUT=/tmp/ladder-25.json \
  CAPACITY_LOAD_USERS=25 uv run pytest -q -s -p smoke_report \
  tests/acceptance/capacity/test_capacity_smoke.py
```

配套 CPU 采样（另开一个 shell 或同一命令后台）：

```bash
uv run python scripts/capacity/diag.py cpu --interval 1 --duration 180 --out /tmp/ladder-cpu.log
```

## profile_hook.py — CPU 归因采样钩子（临时）

sitecustomize 形态：复制进 venv 的 site-packages 后，对**目标服务的进程**做窗口化 profile
（pyinstrument / cProfile），跑完必须删除。完整配方与两个 profiler 的归因坑见 `PLAYBOOK.md` §7.1。

```bash
cp scripts/capacity/profile_hook.py .venv/lib/python3.12/site-packages/sitecustomize.py
CAPACITY_LOAD_USERS=25 MUAD_PROFILE_DIR=/tmp/muad-prof-out \
  MUAD_PROFILE_MODULE=muad_agent_runtime MUAD_PROFILE_DUMP_SEC=5 \
  uv run pytest -q -s tests/acceptance/capacity/test_capacity_smoke.py
rm .venv/lib/python3.12/site-packages/sitecustomize.py
```

## 判读顺序（不要跳步）

1. **CPU**：`diag.py cpu` 看是否单进程贴核；贴核时先做归因，不要先改 SQL/事务结构。
2. **连接池等待**：`diag.py metrics --family db_pool_checkout_seconds`；有排队才谈连接预算。
3. **提交成本**：`diag.py sync-commit` A/B；无变化则排除 fsync/提交。
4. **分段占比**：`diag.py metrics --stage ...` 找 admission / first-delta / persist 的实际墙。
5. **idle-in-tx 年龄**：`diag.py pgsample` 区分“事务里 await 网络”与“事件循环调度不上”。

## 诚实边界

- 本地 1000×8 是 15 核宿主的上限读数，**不是生产容量结论**；生产容量只认 staging E-09
  （16 核以上目标拓扑）。
- E-06（真机企微双 WS）与 E-09（staging 1000×8）仍是人工确认延期项，不是执行证据。
- 真实模型供方额度、探针吞吐、浏览器独立复核不在本目录的自动化覆盖内。
- 历史 800 用户数据是改造前、非目标拓扑，只能作诊断参考（见 `ANALYSIS.md`）。
