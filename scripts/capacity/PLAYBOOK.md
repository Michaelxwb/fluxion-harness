# WebChat 容量压测手册（可复现）

- **状态**：2026-10-27 定稿，2026-10-29 增补 SSE/会话收口后的本地 1000×1/×8 复现；**方法本身稳定**
- **适用**：任何会改动 Runtime/Gateway/Console 热路径、事件写入、SSE、模型池、连接预算的功能
  —— 改完都按下文的同一口径做 before/after，不需要重新探索测量方式
- **配套**：`scripts/capacity/ANALYSIS.md`（诊断与修复方案）、
  `docs/operations/high-qps-target-architecture.md`
- **一句话**：先测**每进程 CPU / 连接池等待 / 提交成本**这三样，再谈“事务放大”；
  实测表明前三者没排除之前，结构改造的收益无法判定。

---

## 0. 30 秒快速开始

```bash
# 1) 停 dev 服务（必须：--reload 的 Worker 会抢走本租户任务）
scripts/dev.sh stop

# 2) 100 用户 smoke（独立临时库 + Redis 10–15 号，跑完自动 drop）
make capacity-smoke

# 3) 看结论：客户端 P50/P95、PG 速率、idle-in-tx；细分看 §4/§5
```

> 跑完把 dev 服务拉回来：`scripts/dev.sh start`。

---

## 1. 前置与纪律（不做会得到假数据）

1. **停 dev 服务**：`scripts/dev.sh stop`，并确认无残留：
   `ps -eo pid,etime,command | grep -E "pytest|uvicorn|vite"`。
   项目内 Worker 的 claim 不带租户谓词 —— 残留 Worker 会领走本租户任务并用它自己的
   `ARTIFACT_ROOT` 执行，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类“莫名失败”。
2. **不要用共享 dev 库**：`tests/acceptance/conftest.py` 会自动建 `muad_acc_<uuid>` 空库并
   `alembic upgrade head`；共享库上跨套件会互相污染（“单跑绿、串跑红”）。
3. **角色需要 CREATEDB**：缺权限时用超级用户执行 `ALTER ROLE muad CREATEDB;`。
4. **输出不要接 `head`**：SIGPIPE 会留下孤儿 pytest/uvicorn 进程，后续用例随机失败。
5. **本地数字不得写成 1000 用户结论**：1000 必须是 staging 目标拓扑（E-09）。

---

## 2. 分层矩阵

| 档位 | 命令 | 证明什么 | 不证明什么 |
|---|---|---|---|
| 100 用户 smoke | `make capacity-smoke` | 功能、错误账目、分段计时、池/提交/CPU | 1000 用户 |
| 200 用户 | `make capacity-load` | 阶梯趋势 | 容量结论 |
| 400 soak | `make capacity-soak`（`MUAD_CAPACITY_SOAK=1`） | 长时间稳定性 | 1000 用户 |
| 1000 staging | `CAPACITY_LOAD_USERS=1000 uv run pytest -q -m capacity_load tests/acceptance/capacity/test_capacity.py` | 生产 SLO | 真实模型额度 |

自定义规模/拓扑：

```bash
CAPACITY_LOAD_USERS=100 MUAD_SMOKE_DEBUG_ROOT=/tmp/muad-smoke-debug \
  uv run pytest -q -s tests/acceptance/capacity/test_capacity_smoke.py
MUAD_CAPACITY_PROFILE=tests/capacity/profiles/agent-capacity-webchat.example.yml ...
```

smoke 的阈值是 S-16 的 1000ms/2500ms；**不是** profile 的生产 SLO（500ms/1200ms）。

---

## 3. 常驻度量目录（应用侧，无需临时 tracer）

Runtime `/metrics` 里的分段计时（`apps/agent-runtime/src/muad_agent_runtime/metrics.py`）。
线格式：累计桶 `<族名>_le_<上界>` + `<族名>_sum` + `<族名>_count`；上界编在指标名里
（`le` 不能进 label —— 架构机检的白名单只收低基数 label 名）。

| 族 | label | 含义 | 判读 |
|---|---|---|---|
| `db_pool_checkout_seconds` | — | 从池取连接的等待 | P95 ≫1ms 才是连接池排队 |
| `db_connection_hold_seconds` | — | checkout→checkin 持有时间 | 远大于语句时间 ⇒ 连接在手但没跑 SQL |
| `run_persist_event_seconds` | — | 单条 canonical 事件写入（含 commit） | 单个事件 P95 与均值 |
| `run_checkpoint_seconds` | — | 完整回合 checkpoint | |
| `run_finalize_seconds` | — | 终态收尾事务 | |
| `run_consume_seconds` | `stage=first_event/total/persist/wait` | 执行片段分解（wait = total − persist） | persist 是事件写合计；wait 含模型流 |
| `run_admission_stage_seconds` | `stage=gate/precheck/materials/claim/write` | 受理分解 | materials 是 Console 往返；其余是 DB/CPU |
| `model_call_seconds` | `stage=candidates/reserve/credentials/upstream/total` | 模型调用分解 | upstream 是真实模型时长 |

PG 侧（`tests/capacity/metrics.py` 的 `PostgresMetrics`，driver 每 0.5s 采样）：
连接 total/idle/idle-in-tx/waiting、锁等待、`xact_commit`、WAL、事件行数，以及
**idle-in-tx 的 query + 事务年龄**。

---

## 4. 读法：累计桶 → 分位数

桶是累计的（样本 ≤ 上界即计入），所以 `P50/P95` = 使累计桶 ≥ q×count 的最小上界。
任意一次抓取的 `/metrics` 文本都能这样读（不需要测试代码）：

```bash
python3 - <<'PY'
import re, sys, urllib.request
url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001/metrics"
text = urllib.request.urlopen(url, timeout=10).read().decode()
FAMILIES = ["db_pool_checkout_seconds", "run_persist_event_seconds",
            "run_consume_seconds", "run_admission_stage_seconds", "model_call_seconds"]
SUF = {"us": 1e-6, "ms": 1e-3, "s": 1.0}
def suffix(n):
    for unit, scale in SUF.items():
        if n.endswith(unit):
            return float(n[: -len(unit)]) * scale
    raise ValueError(n)
samples, counts = {}, {}
for line in text.splitlines():
    if not line or line.startswith("#"):
        continue
    m = re.match(r'^([A-Za-z_:][\w:]*)(?:\{([^}]*)\})?\s+([\d.eE+-]+)$', line)
    if not m: continue
    name, labels, value = m.group(1), m.group(2) or "", float(m.group(3))
    for fam in FAMILIES:
        if name == fam + "_count":
            counts[labels] = counts.get(labels, 0) + value
        elif name.startswith(fam + "_le_"):
            bound = suffix(name[len(fam) + 4:])
            samples.setdefault(fam, {}).setdefault(bound, {})
            samples[fam][bound][labels] = samples[fam][bound].get(labels, 0) + value
for fam in FAMILIES:
    buckets = samples.get(fam, {})
    dims = sorted({k for b in buckets.values() for k in b})
    for dim in dims:
        # 该维度没有 count 行时跳过（维度按 label 字符串精确匹配，够用）
        n = counts.get(dim, 0)
        if not n: continue
        ordered = sorted((b, v[dim]) for b, v in buckets.items() if dim in v)
        def q(p):
            target = p * n
            return next((b for b, c in ordered if c >= target - 1e-9), ordered[-1][0])
        print(f"{fam}{{{dim}}} n={int(n)} p50<={q(.5)*1e3:.1f}ms p95<={q(.95)*1e3:.1f}ms")
PY
```

> 分位数是**桶粒度**的（上界即近似值）：够定位量级，不要拿来当 SLO 证据。

---

## 5. 配套脚本（`scripts/capacity/`）

仓库里已有两件工具，日常复测优先用它们，不再临时拼命令：

| 脚本 | 用途 |
|---|---|
| `scripts/capacity/loadgen.py` | 对**已部署**栈（staging / k8s / dev.sh）发真实负载，复用 `tests/capacity` 驱动与口径，输出 admission/first-delta 分位、失败账目、PG 速率；`--require-slo` 按 profile 判定 |
| `scripts/capacity/diag.py` | 子命令：`metrics`（累计桶读分位数）、`cpu`（进程 CPU 采样）、`pgsample`（commits/s 与 idle-in-tx）、`sync-commit`（A/B 开关） |
| `scripts/capacity/chunk_bench.py` | 每 chunk 管线 CPU 微基准（真实 `_parse_stream`/`DeltaWindow`/emitter；不启栈、不连 DB），输出 µs/chunk 与目标拓扑 CPU 折算 |
| `scripts/capacity/smoke_report.py` | pytest 插件：把 `smoke_run` 的 `LoadReport` 打成一行 JSON + 落盘（含 runtime 分段指标），断言失败也照样出数 |
| `scripts/capacity/profile_hook.py` | CPU 归因的 sitecustomize 采样钩子（复制进 venv 使用、跑完删除）；配方见 §7.1 |
| `scripts/capacity/p1_1000_steady.py` | 本地 1000 用户稳态诊断插件：PgBouncer/生产节拍/模型槽与多 Key 注入；**不是 staging 验收**，配方见 §10.4 |

```bash
uv run python scripts/capacity/diag.py metrics --url http://127.0.0.1:8001/metrics
uv run python scripts/capacity/diag.py metrics --family run_consume_seconds --stage persist
uv run python scripts/capacity/diag.py cpu --out /tmp/p0-cpu.log
uv run python scripts/capacity/diag.py pgsample --interval 0.5
uv run python scripts/capacity/diag.py sync-commit off   # 跑完 sync-commit reset
uv run python scripts/capacity/loadgen.py --config /tmp/load.json --json-out /tmp/report.json
uv run python scripts/capacity/chunk_bench.py --json-out /tmp/chunk.json
```

`loadgen.py` 的配置（含凭据）放在仓库外，示例见 `scripts/capacity/README.md`。

### smoke 阶梯取数（`smoke_report.py`）

`LoadReport` 里有 `unit_metric_deltas`（负载前后的真实 `/metrics` 差量）与逐 0.5s 的 PG 采样。
`scripts/capacity/smoke_report.py` 是 pytest 插件：在测试函数执行前读取 `smoke_run`，
打印一行 `SMOKE-METRICS` 并把完整 JSON（含 runtime 分段指标）写到 `MUAD_SMOKE_METRICS_OUT`；
**断言失败也照样落盘**，适合 25/50/100 阶梯。

```bash
PYTHONPATH=scripts/capacity MUAD_SMOKE_METRICS_OUT=/tmp/ladder-25.json \
  CAPACITY_LOAD_USERS=25 uv run pytest -q -s -p smoke_report \
  tests/acceptance/capacity/test_capacity_smoke.py
```

100 用户时同时采 CPU：

```bash
(uv run python scripts/capacity/diag.py cpu --interval 1 --duration 180 \
   --out /tmp/ladder-100-cpu.log > /tmp/ladder-100-cpu.txt 2>&1) &
PYTHONPATH=scripts/capacity MUAD_SMOKE_METRICS_OUT=/tmp/ladder-100.json \
  CAPACITY_LOAD_USERS=100 uv run pytest -q -s -p smoke_report \
  tests/acceptance/capacity/test_capacity_smoke.py
wait
```

> 插件已入库；单测见 `tests/capacity/test_capacity_chunk_bench.py`。`tests/**` 的 spec
> 注入预算已按单文件规格上调（`.code-flow/config.yml` 的 `budget.l1_max/total`），
> 不再需要先开 TASK Context 才能编辑测试。

---

## 6. A/B 配方

### 6.1 `synchronous_commit`（判断提交/fsync 是否吃成本）

```bash
uv run python scripts/capacity/diag.py sync-commit off     # 只影响之后新建的会话
# ... 跑压测 ...
uv run python scripts/capacity/diag.py sync-commit reset
```

判读：若 admission/first-delta/持久化都在噪声内 ⇒ 提交不是瓶颈，**不要**用“减少 commit 次数”
作为改造目标（2026-10-27 实测就是如此）。

### 6.2 连接池 / PgBouncer

见诊断文档 §5.3。注意 `DATABASE_URL` 走 pgbouncer 时迁移仍直连 PG；临时库需要 wildcard。

### 6.3 极简模型

把 `SMOKE_CHUNKS` 压到 1 个、间隔 0（临时覆盖即可），用于把模型探针从测量中移除。

---

## 7. CPU / 进程采样（必测项，不是可选项）

```bash
uv run python scripts/capacity/diag.py cpu --out /tmp/p0-cpu.log   # Ctrl-C 结束并打印 max/p90/mean
```

等价的手工做法（没有仓库脚本时的兜底）：

```bash
( while true; do
    ps -eo pid,pcpu,rss,etime,command | grep -E "uvicorn" | grep -v grep
    echo "---"
    sleep 1
  done ) > /tmp/p0-cpu.log &
SAMPLER=$!
# ... 跑压测 ...
kill $SAMPLER
```

判读：按 `command` 里的 `muad_*.main` 分单元统计**负载窗口内**的 CPU（不要用整轮平均）；
单进程贴近 100% 时，所有应用侧计时都会一起膨胀，此时不要先改 SQL/事务结构。

归因（仅在需要时、单独跑、低用户）：

- pyinstrument / 采样 profiler 挂在**单个** runtime 进程上，20–30 用户；
- 它**不能**当 SLO 数据（会改变时序并制造失败，见诊断文档 §5.6）。

### 7.1 已实测可用的做法（2026-10-27）

脚本已入库：`scripts/capacity/profile_hook.py`（sitecustomize 形态，两种引擎，无 env 时零影响）。

```bash
cp scripts/capacity/profile_hook.py .venv/lib/python3.12/site-packages/sitecustomize.py
CAPACITY_LOAD_USERS=25 MUAD_PROFILE_DIR=/tmp/muad-prof-out \
  MUAD_PROFILE_MODULE=muad_agent_runtime MUAD_PROFILE_DUMP_SEC=5 \
  uv run pytest -q -s tests/acceptance/capacity/test_capacity_smoke.py
rm .venv/lib/python3.12/site-packages/sitecustomize.py   # 每次采完必须删
```

要点：

1. 用 `builtins.__import__` 包装 + `sys.modules` 判定目标包（`-m` 启动时 sitecustomize 的
   `sys.argv` 形如 `['-m', '--version']`，**没有模块名**）；目标命中后启动 profiler。
2. **不要用 atexit 落盘**：uvicorn 收 SIGTERM 时进程直接被信号终止（实测 atexit 不执行）。
   改用 `signal.setitimer(SIGALRM, N)` 周期快照：每次 `stop()` → 写文件 → **新建 Profiler**
   再 `start()`（同一个 Profiler `start()` 会续上上一段，占比失去时间边界）。
3. `py-spy` 在 macOS 需要 root（attach 被拒），本机不可用。
4. 两个 profiler 都要跑、交叉看（`MUAD_PROFILE_ENGINE=pyinstrument|cprofile`）：
   - pyinstrument（async 归因）：**SQLAlchemy 的 greenlet 切换（C 层）会被记到最外层
     `Runner.run [self]`**（实测 76%），不能当成"事件循环空闲"；
   - cProfile：能看到 greenlet/ORM 明细（`greenlet.switch`/`greenlet_spawn`/`Session.commit`/
     `_connection_for_bind`），但 greenlet 会让 per-call 时间失真——只看**相对排序**与调用次数。
5. 只看**负载窗口**的快照（samples 高的那几段），不要看整个 run 的累计。

---

## 8. 判读顺序（决策树）

1. **每进程 CPU** 是否在负载窗口内贴近 100%？
   - 是 ⇒ 先做 CPU 归因（§7），再谈其他；此时 DB 指标基本没有解释力。
   - 否 ⇒ 进入 2。
2. **`db_pool_checkout_seconds` P95** 是否 ≫1ms（或出现 pool timeout）？是 ⇒ 连接预算/闸门问题。
3. **`synchronous_commit=off` A/B** 是否有差异？有 ⇒ 提交/fsync 才值得优化。
4. 以上都排除后，再看 `run_persist_event_seconds`、`run_admission_stage_seconds`、
   `model_call_seconds` 的分段占比，决定改哪一段。
5. `idle-in-tx` 只看**年龄分布 + 最后一条语句**：年龄短且成批出现 ⇒ 应用没接上（CPU/调度）；
   年龄长且停在网络调用 ⇒ 事务跨 await。

---

## 9. 已知坑

- **`/metrics` 只命中一个副本**：未设 `PROMETHEUS_MULTIPROC_DIR` 时，`unit_metric_deltas`
  只覆盖被路由到的那一个进程（2026-10-27 实测 100 用户只看到 ~57 个 Run）。比较进程间差异时
  必须开 multiproc 或锁定单副本。
- **smoke 是突发**：100 用户同时流式 ~10s（负载窗口 ~25s），不是 10 分钟稳态；不要把突发
  饱和直接外推到 profile 的 8 轮/600s，但“单进程并发上限”是可用的。
- **跑前停 dev、跑后检查残留进程**（见 §1）。
- **同一时刻只能跑一套**：单测与 E2E 共用同一套 PG/Redis（隔离靠租户与产物根）。
- **`make acceptance` 不需要环境变量**；共享库只在排查时用 `MUAD_ACCEPTANCE_SHARED_DB=1`。
- **指标 label 不得含 UUID/正文/凭据**；histogram 桶上界编进指标名，不要加 `le` label。

---

## 10. 当前基线（2026-10-27，本机）

### 10.1 25/50/100 用户阶梯（smoke，1 轮 × 20 chunks/s × 10s）

| 用户 | admission P95 | first-delta P95 | 通知 P95 | idle-in-tx | 最忙 runtime 10s 峰值 / p90 |
|---:|---:|---:|---:|---:|---:|
| 25 | 89ms | 306ms | 61ms | 4 | 46.5% / 36.0% |
| 50 | 408ms | 1342ms | 150ms | 27 | 88.2% / 67.3% |
| 100 | 2250–2367ms | 7533–7805ms | 960–1054ms | 30–38 | 99.3% / 98.5%（14s ≥90%） |

单进程约 60–70 条并发流饱和；另一副本在三档都几乎空闲（p90 1.8%/2.7%/37.7%），
容量按最忙副本算。测试失败项：50 用户仅 `max_idle_in_tx`；100 用户 admission、
first-delta、idle-in-tx 三项。

### 10.2 100 用户 smoke 分段基线（2026-10-27 夜，Settings+每流+2 副本真正分摊）

| 指标 | 当前基线 | 修复前 |
|---|---:|---:|
| admission P50 / P95 / P99 | **~68ms / ~641ms / ~749ms** | ~1.2s / ~2.3–2.7s |
| first-delta P50 / P95 / P99 | **~277ms / ~1879ms / ~2061ms** | ~3.9–4.2s / ~7.5–8.0s |
| 通知 P95 / P99 | **~243ms / ~298ms** | ~0.96–1.05s |
| 单事件持久化均值 | ~118–125 ms | 同左 |
| PG commits/s · 事件行/s | ~92–94 · ~40–42 | 同左 |
| 连接池等待 P95 | ≤0.5 ms | 同左 |
| idle-in-tx 峰值 | 39（仍超 smoke 阈值 20） | 30–38 |
| 负载窗口内 runtime p90 CPU | 59.2% / 74.7%（两副本） | 单副本 98.5–100% |
| 每 chunk 管线 CPU（微基准） | 6.4–6.8 µs；1.28–1.35 ms/轮 | 同左 |

> 关键口径：**必须让验收栈真正轮询 runtime 副本**。此前的本地读数把全部流量钉在 0 号实例，
> 100 用户 first-delta 3.9–4.5s 是“单副本扛 100 流”的假象；真正 2 副本分摊后是 1.88s。

### 10.3 1000 用户当前结果（2026-10-27 夜，失败运行，仅用于定位）

8 runtime 副本（真实轮询）+ PgBouncer + 2000 模型槽；**Console 单 Pod × 4 worker**：

| 指标 | Stage 1（4 worker） | + 事件 batch（10ms/32） | 单 worker |
|---|---:|---:|---:|
| admission P95 / P99 | 14592ms / 17848ms | 14868ms / 18396ms | 7519ms / 9469ms |
| first-delta P95 / P99 | 32550ms / 37057ms | 33292ms / 38055ms | 8975ms / 11807ms |
| 通知 P95 / P99 | 14507ms / 16613ms | 15031ms / 17143ms | 7314ms / 9062ms |
| completed / attempts | 5331 / 5984 | **5708 / 6229** | 756 / 4899 |
| 系统错误率 | 8.56% | **7.26%** | 19.6% |
| PG connections / idle-in-tx | 90 / 88 | 90 / 87 | 75 / 44 |
| PG commits/s · event rows/s | 324 · 106 | **200 · 64** | 211 · 75 |

结论：4 worker 让 Console 不再是墙（完成数 756 → 5331）；事件 batch 把 commits 324→200、
完成数 5331→5708，但 **idle-in-tx 仍 87、连接仍 90、first-delta 仍 33s**。10ms/32 在 8 个
runtime 进程分摊后平均每批不到 1 条，无法摊薄事务。下一版需要 50–100ms 窗口 + 首事件立即
flush，或 Pod 级共享 writer；同时按事务类型拆 idle-in-tx 归因。
此表是失败运行的幸存者 P95/P99，不得写成“1000 用户容量结论”；正向结论仍由 staging E-09 裁决。

---

### 10.4 1000 用户 SSE 收口后复现（2026-10-29，本地 15 核）

**拓扑**：MacBook Pro M5 Pro（15 核 / 48GB）+ 同机 PG/Redis/PgBouncer；Console 单 Pod × 4 worker、
8 个 runtime 进程、2 个 gateway、4 个模型 Key（总 2000 槽）。这是**诊断拓扑**，不是 staging。

**profile**（已入库，不再依赖 `/tmp`）：

- `tests/capacity/profiles/local-macbook-1000x1-sse.yml`（1000 用户 × 1 轮 / 150s steady）
- `tests/capacity/profiles/local-macbook-1000x8-sse.yml`（1000 用户 × 8 轮 / 600s steady）

**复现命令**（1000×1；8 轮把 profile 换成 `...1000x8-sse.yml`）：

```bash
scripts/dev.sh stop
rm -f /tmp/1000x1.log /tmp/1000x1.json
PYTHONUNBUFFERED=1 \
EVENT_BATCH_WINDOW_MS=10 \
MUAD_NOTIFICATION_TRANSPORT=sse \
CAPACITY_LOAD_USERS=1000 \
MUAD_CAPACITY_PROFILE=tests/capacity/profiles/local-macbook-1000x1-sse.yml \
MUAD_PGBOUNCER=1 MUAD_MODEL_SLOTS=2000 MUAD_MODEL_KEYS=4 \
MUAD_PGSS_RESET=1 MUAD_PROD_CADENCE=1 MUAD_LOOP_LAG=1 MUAD_GC_PROBE=1 \
PYTHONPATH="scripts/capacity:$PYTHONPATH" \
MUAD_SMOKE_METRICS_OUT=/tmp/1000x1.json \
uv run pytest -q -s -p smoke_report -p p1_1000_steady \
  tests/acceptance/capacity/test_capacity.py::test_s01_thousand_users_admitted_visible_and_bounded \
  > /tmp/1000x1.log 2>&1
grep -a SMOKE-METRICS /tmp/1000x1.log
```

同时采宿主 CPU（另开 shell 或同命令后台）：

```bash
uv run python scripts/capacity/diag.py cpu --interval 1 --duration 180 --out /tmp/1000x1-cpu.log
```

**结果（2026-10-29，最终 SSE 代码）**：

| 指标 | 1000×1 | 1000×8 |
|---|---:|---:|
| 拿到 Run 的用户 / 完成 | **1000 / 1000** | 910 / 7259 |
| 系统错误率 | **0%** | 1.52% |
| admission P50 / P95 | 146ms / **264ms** | 3578ms / 12399ms |
| first-delta P50 / P95 | 1328ms / **1612ms** | 12704ms / 27191ms |
| notification P50 / P95 | 42ms / **80ms** | 3862ms / 11736ms |
| PG 连接峰值 / idle-in-tx | 58 / 35 | 92 / 75 |
| 宿主峰值 | — | 13.43 / 15 |

判读：

- 1000×1：功能/错误率/acceptance admission 达标，**first-delta 1.61s 仍超 1.2s SLO**。
- 1000×8：宿主 13.43/15，runtime 稳态墙是 `persist ~2s + event_batch_wait ~0.5s +
  upstream ~1.5s`；**不得写成生产容量结论**。
- 该轮 SSE 已是「建流前 REST 快照 + `snapshot=1`」：1000 条流不再逐流回填；driver 的初始
  `GET /notifications` 与前端 `startWithStream` 同形。
- `MUAD_MODEL_SLOTS=2000` 会被 `p1_1000_steady.py` 同步到测试常量；因此最后一条
  “凭据 `max_inflight` 必须等于 `MODEL_MAX_INFLIGHT`”断言在诊断口径下会红（实际 2000 vs
  seed 16）。这是**插件的诊断覆盖**，不是产品失败；正式 E-09 不加载该插件、保持 16 槽。
- 没有 16c/staging 机器时，生产容量仍以 staging E-09 为准。

---

## 11. 沉淀待办

- [x] 备选插件固化：`scripts/capacity/smoke_report.py`（`-p smoke_report` + `MUAD_SMOKE_METRICS_OUT`）
- [x] `smoke_report`/`chunk_bench` 单测：`tests/capacity/test_capacity_chunk_bench.py`
- [x] 本地 1000 SSE 复现固化：`local-macbook-1000x{1,8}-sse.yml` + `scripts/capacity/p1_1000_steady.py`（§10.4）
- [x] SSE 通知传输接入驱动：`MUAD_NOTIFICATION_TRANSPORT=sse`（driver 建流前 REST 快照 + `snapshot=1`）
- [ ] `scripts/capacity/loadgen.py` 在 staging 实跑一次并把数字写进 §10 基线
- [ ] 给 gateway 侧 SSE 打开（`get_run_scope` + `open_event_stream`）加同口径分段计时
- [ ] 把“极简模型”做成正式场景 `test_capacity_minimal_model.py`
- [ ] run timeline 事件（submit/admission/claim/first_chunk/first_sse/finalize）常驻化
- [ ] 副本负载不均的根因（网关连接复用 / 驱动分配 / 长连接粘滞）
