# Agent 容量报告与 Runtime/Worker 副本建议（Webchat 容量验收）

本文是 TASK-117 的运营口径：怎么跑真实容量负载、证据包是什么、报告包含什么、以及怎么读
Runtime/Worker 的副本建议公式。设计来源：`agent-capacity-webchat.backend.design.md` §4.1
部署与容量、§4.3 分层验收与报告、§3.7 配置契约与生效边界。

一句话口径：**报告只消费实际运行证据**。没有样本就没有分位数，没有执行事实就不给 Pod 数，
缺 SLO/资源声明或只跑了受限规模时只给诊断结论 —— 不输出生产达标，也不编造数字。

---

## 1. 怎么跑负载

### 1.1 受限规模（日常/回归，本仓库可直接跑）

```bash
# 四单元独立栈（真实 Console/Runtime/Worker/Gateway 子进程 + 真实 PG/Redis + 入口代理 + 模型协议探针）
# 自动建空库（muad_acc_<uuid>，alembic upgrade head）并在收尾 drop；不需要额外环境变量。
uv run pytest -q tests/capacity/test_capacity_webchat_capacity_report.py
```

- 负载形状来自锁定的 typed profile：`tests/capacity/profile.py` +
  `tests/capacity/profiles/agent-capacity-webchat.example.yml`；
- 本套件用显式登记的受限规模（`RunKind.VERIFICATION` + `LoadScale`）真实跑一轮负载，
  把 `LoadReport`、真实进程执行槽（进程 env）、真实 engine 工厂连接上限、真实渲染清单事实
  采集为证据包，落盘后再读回生成报告（`tests/capacity/report.py`）；
- 受限规模**不会**被当成 1000 用户结论：报告结论为 `DIAGNOSTIC`/`REJECTED`，阻塞项里显式写明。

跑前检查（与根 `CLAUDE.md` 一致）：先停本地 `dev.sh` 起的服务（同一套 PG/Redis，Worker 会领走
任务），确认没有残留 `pytest`/`uvicorn muad_*` 进程；跑完确认没有残留 `muad_acc_*` 库。

### 1.2 全量验收（1000 用户，需求级）

```bash
uv run pytest -q tests/acceptance/capacity/test_capacity.py     # S-01/S-14/S-15（含 1000×8 全量负载）
```

全量运行由需求级 verify-e2e 执行并复核（本任务不替代它）。全量结论要以 `ACCEPTANCE` 运行
（驱动强制不得夹带缩放）产出的证据包为准：用同一采集/渲染 API 把该轮的 `LoadReport` 落成
证据包（见 §2），再渲染报告；只有 `ACCEPTANCE` + SLO 全过 + 预算全过才可能得到 `ACCEPTED`。

### 1.3 浏览器容量面

```bash
make acceptance-e2e DOMAIN=agent
```

浏览器栈的容量/恢复断言（重连抖动、单 Pod 重启、预算可核对）由 TASK-115/116 的 spec 承担，
不在本报告的生成链路上。

---

## 2. 证据包（Evidence Bundle）

`tests/capacity/report.py` 的 `CapacityEvidence` 是 typed、可 JSON 往返、严格校验的结构；
`capture_evidence(...)` 从一次真实运行采集，`write_evidence(path, evidence)` / `load_evidence(path)`
落盘与读回。缺任何必需事实（并发采样、受理延迟样本、模型探针回执、真实执行槽、每进程 PG
连接上限）都抛 `EvidenceError` —— 不补默认值。

| 证据块 | 内容 | 真实来源 |
|---|---|---|
| `workload` | 锁定 profile 的目标（users/ramp/steady/turns）与本次真实展开规模、尝试数 | profile + `LoadReport` |
| `latencies` | `admission_ms` / `first_delta_ms` 的**逐条样本** | 真实 HTTP/SSE 轮次（驱动） |
| `failure_counts` | 显式失败账目（系统错误 / 明确过载分类计数） | 驱动的不做选择性排除的失败分类 |
| `execution` | 真实进程执行槽、测量副本数、并发采样峰值（在飞/等待/受理/在飞订阅） | 进程 env + PG 并发采样 |
| `postgres` | 连接峰值、锁等待、WAL 增长、库大小 | 真实 PG 采样 |
| `model` | 边界模式（`CONTROLLED_PROBE`/`REAL`）、额度引用（只允许 `env:`）、观测调用数与最大并发、故障计数 | 模型协议探针控制面 + profile 额度引用 |
| `deployment` | 四单元渲染清单事实：HPA 上界、requests/limits、终止宽限、执行槽 env、出站池上限 | `deploy/k8s/base` 经 TASK-104 预检同一渲染路径 |
| `resources` | profile 的资源声明 + **真实 engine 工厂**的每进程 PG 连接上限 | profile + `pool_size + max_overflow` |
| `slo` | 锁定 SLO 阈值（无默认值） | profile（缺则只给诊断结论） |
| `config_hash` | profile 内容 hash + 部署事实摘要 | 见 §4 |

证据包不含凭据原文、DSN、服务地址或原始内部内容：模型额度只以 `env:NAME` 引用出现。

---

## 3. 渲染报告

```bash
uv run python -m tests.capacity.report \
  --evidence <evidence.json> \
  --pg-maintenance-reserve 1 \
  --runtime-target 16 --worker-target 8 \
  --utilization 0.8 --redundancy 1
```

- `--pg-maintenance-reserve`：PG 维护/迁移保留连接（必填；发布预检同一口径的最低值是 1，
  更保守的保留量由业务确认后显式传入）；
- `--runtime-target/--worker-target`：业务确认的**目标执行并发**（同时给出或都不给）；
- `--utilization`（(0,1]）与 `--redundancy`（≥0）：业务确认的目标利用率与冗余副本。
  没有这些参数就不产出 Pod 数（报告里记一条"未提供业务参数"）。

报告（Markdown）包含：

1. **结论**：`ACCEPTED`（生产达标）/ `REJECTED`（SLO 违约、预算超限或 PG 连接实测越界）/
   `DIAGNOSTIC`（证据不足：受限规模、缺 SLO/资源声明等）；
2. **延迟分位数**：P50/P95/P99/max（最近秩 `rank = ceil(q × n)`，可由证据包逐条复算）；
3. **SLO 判定**：受理 P95/P99、框架首字 P95、系统错误率逐项判定；系统错误与明确过载分开计数；
4. **真实触到的瓶颈**：只有本次运行真的触到上限才产出（执行槽、模型在飞额度、排队、PG 连接），
   每项带 observed/ceiling 与证据说明；
5. **资源与连接预算**：四单元 requests/limits（渲染清单事实）、PG 总连接（进程 × 最大副本 ×
   真实每进程上限）、每进程 FD（出站池 + PG + Redis + 入站订阅）与预算核对；
6. **副本建议**：见 §5；
7. **阻塞项与备注**：不给生产结论的理由、模型边界声明、容量口径、存储预算与真实观测。

---

## 4. 配置 hash（锁定"测的是哪一份配置"）

`config_hash = sha256("profile:" + profile_hash + "\ndeployment:" + deployment_digest)`：

- `profile_hash`：typed profile 的内容 hash（`tests/capacity/profile.py:profile_hash`，与注释/格式无关）；
- `deployment_digest`：渲染清单事实的规范 JSON 摘要（四单元的副本上界、requests/limits、
  终止宽限、执行槽、出站池上限）。

证据包自带 `config_hash`，报告生成时**重新计算并核对**：不一致（证据被改动或混用两份配置）
直接抛 `EvidenceError`。任何一侧变化（改 profile、改清单资源/上界）都会改变 hash。

---

## 5. 怎么读副本建议公式

```
N = ceil(目标执行并发 / 单 Pod 容量 / 目标利用率) + 冗余副本
```

- **目标执行并发**：业务确认（Runtime 是并发 Run 数，Worker 是并发 Task 数）；
- **单 Pod 容量** = 真实执行槽 × 每 Pod 进程数：执行槽来自**真实进程 env**
  （`RUNTIME_EXECUTION_CONCURRENCY` / `WORKER_EXECUTION_CONCURRENCY`），进程数来自 profile
  `resources.process_counts`；
- **目标利用率**：业务确认的留白（如 0.8 表示按 80% 负载规划）；
- **冗余副本**：业务确认的高可用余量；
- 报告的 `formula` 字段给出可逐字复算的算式，例如 `ceil(16 / 8 / 0.8) + 1 = 4`。

读数要点：

1. `capacity_basis` 是**容量口径**：`MEASURED` 表示本次运行把执行槽真的压满（观测峰值 ≥
   单 Pod 容量 × 测量副本数），`DECLARED` 表示只按真实声明配置计（槽位未被压满）——
   受限规模运行通常是 `DECLARED`，不会冒充"实测压满"；
2. `within_max_replicas`：建议副本必须落在部署上界（HPA `maxReplicas`）内；超出即为真实发现
   （需调整 HPA 上界与 PG/下游预算后复测），报告把它记进阻塞项；
3. 副本建议不替代 PG/下游总预算核算：报告同时给出 PG 总连接与每进程 FD 的核对行。

---

## 6. 诚实边界

- **协议探针 ≠ 真实模型容量**：`CONTROLLED_PROBE` 模式的吞吐不冒充真实模型吞吐，
  报告显式标注"真实供方额度未验证"；`REAL` 模式的额度证据只允许 `env:NAME` 引用；
- **受限规模 ≠ 全量结论**：`VERIFICATION` 运行只给诊断/不达标结论，`ACCEPTED` 只可能来自
  `ACCEPTANCE` 运行 + SLO 全过 + 预算全过；
- **没证据就没有数字**：样本为空、缺执行事实、缺 SLO/资源声明时显式失败或记阻塞项，
  不回落默认值、不输出虚构 Pod 数；
- 存储/磁盘预算按事件率 × 保留期 + 制品字节声明，另计索引/WAL/备份，不由本报告给出实测 IO 结论。
