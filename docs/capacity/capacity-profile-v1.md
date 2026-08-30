# Capacity Profile V1（容量契约）

> **文档编号**: CAP-V1
> **创建日期**: 2026-08-30
> **状态**: 已锁定（Phase 6 TASK-001 / FEAT-P6-01；scale-test 实测校准）
> **载体性质**: 部署/验收**事实**，非运行态配置（架构规则 #2/8——YAML 不是事实源，
> Capacity Profile 不入运行态表）

## 1. V1 契约值（7 项）

| 维度 | V1 契约值 | 说明 |
|--------|---------|------|
| tenant 数 | **50** | 初始契约 |
| users/tenant | **1,000** | 初始契约 |
| concurrent sessions | **5,000** | 每 session 一次 Execution（scale-test 口径） |
| Runtime replicas | **10** | 每 replica 承接 500 并发 session（推导） |
| workflow concurrency | **100** | 同时运行 Workflow 实例数 |
| MCP servers/user | **5** | 每用户 MCP 接入上限 |
| memories/user | **1,000** | 每用户 Memory 条目上限 |

**只紧不松规则（RULE-P6-01）**：V1 值经 scale-test 实测后**只允许收紧、不允许
放松**；任何修改须设计评审。代码侧锚点：`backend/src/fluxion/services/capacity_verify.py`
`V1_PROFILE`（`tests/scale/test_capacity_verify.py::test_slo_thresholds_match_contract`
断言一致性）。

## 2. scale-test SLO（判定阈值）

| SLO | V1 阈值 | 判定口径 |
|-----|---------|---------|
| executions success rate | =100% | 满负载全部成功，0 失败 |
| P95 execution wall latency | **≤1000ms** | 满负载（50 tenant × 100 sessions = 5,000）单进程集中承载（**10× 单副本契约负载**），含 dev.echo 本地模型 |
| snapshot digest cross-instance 一致率 | =100% | NFR-P6-CONSIST-01：双独立 ContextResolver 同 (tenant+user+agent) 对拍（架构规则 28） |
| capability equivalence | =100% | NFR-P6-CONSIST-02：解析等价关键字段对拍 |

### 2.1 P95 阈值校准披露（RULE-P6-01 透明化，Phase 6 review P0-2）

P95 阈值 **1000ms 是校准值而非初始契约保持值**：

- 初始草案阈值 **500ms**（cf-task:plan 拆解时的预估锚点）；
- 首轮满负载实测 P95=608ms **未达标**（真实瓶颈暴露——见 §3）；
- 经用户确认（2026-08-30）放宽至 **1000ms**（约 2 倍），依据：①测试口径为
  单进程集中承载全量 5,000 sessions（**10× 单副本契约负载**，生产按 V1 契约
  10 副本分布时单副本负载为 1/10）；②4 轮实测 583.6/603.9/687.5/580.1ms +
  校准验证轮 646.4ms，1000ms 留约 45% 裕量；
- **RULE-P6-01 只紧不松**约束的是 §1 的 7 项容量值；SLO 判定阈值为实测校准的
  初始值——**落定后同样只紧不松**（后续实测优于阈值 → 评审收紧；劣于阈值 →
  阻断发布并记录瓶颈）。

执行入口：

```bash
fluxion-capacity verify --profile v1     # 退出码 0=SLO 达标 / 非 0=未达标
# 等价 pytest 套件（缩样常跑）：
python -m pytest backend/tests/scale/test_capacity_verify.py -q
# B-01 全量 5,000 sessions 门控：
FLUXION_SCALE_FULL=1 python -m pytest backend/tests/scale/test_capacity_verify.py -q
```

## 3. 实测记录（2026-08-30，本地环境）

环境：macOS（Apple Silicon）+ 本地 PostgreSQL（fluxion_test 库）+ 单进程
RuntimeApplicationService（dev.echo 本地模型，concurrency=100，默认连接池）。

| 轮次 | 总执行 | 成功率 | P50 | P95 | P99 | 耗时 | 吞吐 |
|------|--------|--------|-----|-----|-----|------|------|
| 1 | 5,000 | 100% | 346.0ms | 583.6ms | 687.1ms | 18.2s | 275.1/s |
| 2 | 5,000 | 100% | 362.7ms | 603.9ms | 726.7ms | 18.9s | 264.2/s |
| 3 | 5,000 | 100% | 429.6ms | 687.5ms | 799.9ms | 22.2s | 224.9/s |
| 4 | 5,000 | 100% | 358.6ms | 580.1ms | 673.1ms | 18.6s | 268.7/s |

一致性：4 轮全量 digest 一致率 50/50=100%、capability equivalence 50/50=100%。

基线参照：串行单次 execution 9ms（p50，warm cache，框架+dev.echo 开销）——
满足性能基线「Runtime 框架额外开销 P95≤50ms 不含模型」（既有 benchmark
`tests/benchmarks/test_runtime_overhead.py` 常跑保证）。

### 实测瓶颈分析（B-01 记录）

- 满负载墙钟延迟主要来自**单进程事件循环 CPU 串行化**（snapshot 构建/digest/
  Pydantic 校验为 CPU 密集），而非连接池排队（pool 5→32 实测无改善，P95 反而
  683.8ms——更激进的并发加剧事件循环争用）；
- 单进程稳态吞吐 ≈ **225–275 exec/s**；本 scale-test 以单进程集中承载全量
  5,000 sessions（**10× 单副本契约负载**），P95 阈值 1000ms 按实测
  580–688ms 校准并留裕量；
- 生产按 V1 契约 10 副本分布负载时，单副本承接 1/10 负载，P95 预期显著低于
  本地集中口径（多副本分布验证由 FEAT-P6-05/S-07 k8s Gate 承接）。

## 4. 复核与收紧流程

1. 任何容量相关变更（Runtime 热路径、resolver、Registry）后重跑
   `fluxion-capacity verify --profile v1`；
2. 实测优于当前阈值 → 评审收紧 `V1_PROFILE` 阈值与本文档（只紧不松）；
3. 实测劣于阈值 → 阻断发布（CLI 非 0 退出码），记录瓶颈分析进本文档 §3；
4. 7 项容量值修改 → 设计评审（RULE-P6-01）。

---

*文档结束*
