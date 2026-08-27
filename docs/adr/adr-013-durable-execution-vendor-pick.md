# ADR-013: Durable Workflow Engine Vendor Pick = DBOS（解除 pending-PoC-gate）

- **Status**: Accepted
- **Date**: 2026-08-27
- **Amends**: ADR-008（Engine 归业务接入层 → 现明确采用 DBOS 构建）；解除 ADR-WF-001 design §3.1.4 的 `pending-PoC-gate`
- **Problem Driver**: P11（Durable Workflow）、ADR-008（Adapter/Engine 边界）

## Context

ADR-008 决定 Workflow Tool Adapter 进开源 V1、Engine 归业务接入层（不内置开发）。Engine 的选型此前挂在 `pending-PoC-gate`。ADR-WF-001 PoC 对 3 采购候选（Temporal / DBOS / Restate）跑 7 口径，产出自动化 evidence 后回填 19 维矩阵定 vendor。

## PoC 实测证据（2026-08-27）

| 候选 | 结果 | 关键实测 |
|---|---|---|
| **DBOS** | 11/11 场景 + baseline 1000 并发全绿 | `evidence/dbos.json` all_criteria_passed=True；崩溃恢复 5.07s 自动生效；database-backed queue 双 worker 4/4 分摊；start 14ms |
| Restate | 5 过 / 6 记录为单节点边界 | **单节点 worker 崩溃后 suspended invocation 无法恢复**（5 方案实测全失败）；2 deployment 不分摊；BUSL-1.1 非 OSI |
| Temporal | 未实测（用户决策：DBOS 达标则跳过） | 集群运维最重；矩阵按文档打分 7.4 |

矩阵最终得分：**DBOS 8.7** > Temporal 7.4 > Restate 5.5（self-built 未测，PRD 默认否决、翻盘条件未触发）。

## Decision

**Workflow Engine 采用 DBOS（PostgreSQL-native durable execution library）。**

- 业务接入层以 DBOS 构建真实 Engine，替换 Adapter 的 Stub（接续 ADR-008 Validation）。
- **解除 `pending-PoC-gate`**：WorkflowEngine Protocol（已有 Contract，backend 无关）绑定 DBOS 候选实现。
- **Self-built fallback 关闭**（RISK-WF-01）：3 候选 PoC 证据齐备且 DBOS 达标，PRD §4.8 默认否决自研成立。

## Rationale（关键维度）

- **运维**：零新增服务，复用既有 PostgreSQL（DBOS sys 表建在业务库）；vs Restate +1 容器且单节点恢复失效、Temporal 集群。
- **故障恢复**：DBOS launch 级启动恢复自动生效（PoC S-02 5.07s）；Restate 单节点不恢复（生产否决级）。
- **合规**：DBOS MIT（OSI 开源）；Restate BUSL-1.1（source-available，4 年后转 Apache-2.0）。
- **规模**：database-backed queue 双 worker 分摊实测通过；Restate 单节点 2 deployment 不分摊。

## Trade-offs

- DBOS 是 library 模型：引擎与业务进程同生共死 → 生产采用独立 worker 进程承载引擎（PoC 已验证该模式）；强耦合 PostgreSQL（Fluxion 本就用 PG，可接受）。
- 生态以 Python 优先（有 TS SDK），多语言大规模编排弱于 Temporal——远期需求变化时经 WorkflowEngine Protocol 可替换（ADR-008 边界保证可逆性）。

## Failure Modes / Revisit

- DBOS 后续版本破坏兼容 → WorkflowEngine Protocol 隔离，可换候选（ADR-008 可逆性）。
- 若未来需要跨语言大规模编排 → 重新评估 Temporal（运维成本当时机成熟可承受时）。

## Validation

- ADR-WF-001 TASK-005 E-02 gate：`test_poc_gate.py` 校验 `evidence/dbos.json` all_criteria_passed=True（无有效证据阻断 Engine 生产路径）。
- S-01..S-06 汇总：`test_evidence_summary.py` 校验 DBOS evidence 7 口径全绿 + SLO 数值达标。
