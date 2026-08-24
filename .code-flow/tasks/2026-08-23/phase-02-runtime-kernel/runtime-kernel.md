# Tasks: Fluxion 无状态 Runtime Kernel

- **Source**: docs/design/fluxion-runtime-design-v1.7.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

实现无状态 Runtime Kernel，让任意 Pod 都能根据 tenant/user/agent 解析同一执行上下文，并在一次 Execution 内冻结精确资源版本。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突时记录 `#NOTES` 并停止。
- **Acceptance**: 所有 Acceptance-Refs、required verifier、回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R03 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Resolver → Snapshot → Runtime → Trace | TASK-002 | verified |
| S-R05 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Registry/Session Store → 两个 Runtime 实例 | TASK-002 | verified |
| E-R01 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Runtime → Registry | TASK-002 | verified |
| E-R02 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Resolver → Snapshot | TASK-002 | verified |
| B-R03 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Runtime Pool 多实例 | TASK-002 | verified |

| B-R04 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | benchmark | Resource Resolver L1 cache hit | TASK-002 | verified |

| B-R07 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | benchmark | ExecutionSnapshot Builder | TASK-002 | verified |
| S-R17 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | AgentLoop → Memory SPI → L0/L1/L2 多层读写与触发落盘 | TASK-002 | verified |
| S-R18 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | AgentLoop → Context Compactor（临近上限摘要）→ L1/L2 落盘 | TASK-002 | verified |
| S-R19 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Scheduler → 到点触发 → AgentLoop（独立 Execution） | TASK-002 | verified |
| S-R20 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | AgentLoop → plan-then-execute（分解/执行/失败重规划） | TASK-002 | verified |

---

## TASK-002: 实现 ResourceResolver、ExecutionSnapshot 与无状态执行内核

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-R03, S-R05, S-R17, S-R18, S-R19, S-R20, E-R01, E-R02, B-R03, B-R04, B-R07

### Description

实现无状态 Runtime Kernel，让任意 Pod 都能根据 tenant/user/agent 解析同一执行上下文，并在一次 Execution 内冻结精确资源版本。

### Scope

- 实现 RequestContext、RuntimeContext、ResourceResolver。
- 实现 Effective Capability 交集计算。
- 实现 ExecutionSnapshot 构建和 immutable 使用。
- 实现 Session/Memory SPI 和 L1 Resource Cache Contract。
- 实现 Memory 默认实现（多层 L0/L1/L2）与触发式落盘（复用 Session/Memory SPI；向量索引为可选后端，默认不启用）。
- 实现上下文压缩（Context Compaction）：临近上限摘要旧轮次、摘要落入 L1/L2（区别于持久化落盘）。
- 实现定时/主动任务（Cron/Heartbeat）统一调度器：到点触发 AgentLoop，独立 Execution，配置走 Policy/Approval。
- 实现 AgentLoop 规划（plan-then-execute）：长任务分解、逐步执行、失败重规划。
- 实现统一 Runtime Error Taxonomy 与 Trace Context。

### Checklist

- [x] 先写 Snapshot、跨 Pod、一致性和依赖异常测试并记录 RED。
- [x] Cache Key 必须包含 tenant scope。
- [x] 当前 Execution 不得因热更新改变资源版本。
- [x] Pod 本地不得保存 durable fact。
- [x] 多层 Memory：L0 随执行结束即弃、L1 会话隔离、L2 跨会话且 tenant 隔离；临近上下文上限触发落盘。
- [x] 上下文压缩：最新 N 轮保留原文、旧轮次摘要；压缩后摘要入 L1/L2；不改 ExecutionSnapshot。
- [x] 定时/主动任务：每次触发独立 Execution，Runtime 无跨执行持久状态，触发走 Policy/Approval。
- [x] 规划：失败可重规划；规划状态仅限当前 Execution；全程 Trace。
- [x] 执行 benchmark 验证 Resolver/Snapshot 性能基线。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R03 | E2E | `backend/tests/e2e/test_snapshot_stability.py` | `python3 -m pytest backend/tests/e2e/test_snapshot_stability.py -k S_R03` | 执行中发布新版本不改变当前 Snapshot | verified |
| S-R05 | E2E | `backend/tests/e2e/test_stateless_runtime.py` | `python3 -m pytest backend/tests/e2e/test_stateless_runtime.py -k S_R05` | Pod1 删除后 Pod2 不丢事实状态 | verified |
| E-R01 | integration | `backend/tests/integration/test_registry_degraded.py` | `python3 -m pytest backend/tests/integration/test_registry_degraded.py -k E_R01` | Registry 不可用按安全等级降级/失败 | verified |
| E-R02 | integration | `backend/tests/integration/test_snapshot_resolution.py` | `python3 -m pytest backend/tests/integration/test_snapshot_resolution.py -k E_R02` | 不存在版本拒绝且不静默换版 | verified |
| B-R03 | E2E | `backend/tests/e2e/test_runtime_pool.py` | `python3 -m pytest backend/tests/e2e/test_runtime_pool.py -k B_R03` | 多实例同版本解析一致 | verified |

| B-R07 | benchmark | `backend/tests/benchmarks/test_snapshot_benchmark.py` | `python3 -m pytest backend/tests/benchmarks/test_snapshot_benchmark.py -k B_R07 --benchmark-only` | Snapshot 构建 P95≤20ms | verified |

| B-R04 | benchmark | `backend/tests/benchmarks/test_resolver_benchmark.py` | `python3 -m pytest backend/tests/benchmarks/test_resolver_benchmark.py -k B_R04 --benchmark-only` | Resolver L1 hit P95≤5ms | verified |
| S-R17 | E2E | `backend/tests/e2e/test_memory.py` | `python3 -m pytest backend/tests/e2e/test_memory.py -k S_R17` | L0 随执行结束即弃；L1 会话隔离；L2 跨会话 tenant 隔离；临近上限自动落盘 | verified |
| S-R18 | E2E | `backend/tests/e2e/test_context_compaction.py` | `python3 -m pytest backend/tests/e2e/test_context_compaction.py -k S_R18` | 最新 N 轮原文保留、旧轮摘要入 L1/L2；Snapshot 不变 | verified |
| S-R19 | E2E | `backend/tests/e2e/test_scheduler.py` | `python3 -m pytest backend/tests/e2e/test_scheduler.py -k S_R19` | 到点触发独立 Execution；无跨执行持久状态；走 Policy/Approval | verified |
| S-R20 | E2E | `backend/tests/e2e/test_planning.py` | `python3 -m pytest backend/tests/e2e/test_planning.py -k S_R20` | 步骤序列逐步执行；失败重规划；规划状态仅限当前 Execution | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-R03 | failed: missing `AgentRuntime`/`RequestContext` import | passed: 1/1 | `backend/tests/e2e/test_snapshot_stability.py::test_S_R03_execution_snapshot_is_fixed_during_hot_publish` | Resolver → Snapshot → Runtime → Trace | verified |
| S-R05 | failed: missing `AgentRuntime`/`RequestContext` import | passed: 1/1 | `backend/tests/e2e/test_stateless_runtime.py::test_S_R05_pod_replacement_keeps_facts_in_shared_memory_store` | Registry/Session Store → 两个 Runtime 实例 | verified |
| E-R01 | failed: missing `fluxion.runtime.resolver` module | passed: 2/2 | `backend/tests/integration/test_registry_degraded.py::test_E_R01_registry_unavailable_degrades_only_with_safe_stale_cache` | Runtime → Registry | verified |
| E-R02 | failed: missing `RequestContext` import | passed: 1/1 | `backend/tests/integration/test_snapshot_resolution.py::test_E_R02_missing_dependency_version_is_rejected_without_version_swap` | Resolver → Snapshot | verified |
| B-R03 | failed: missing `AgentRuntime`/`RequestContext` import | passed: 1/1 | `backend/tests/e2e/test_runtime_pool.py::test_B_R03_runtime_pool_resolves_same_versions` | Runtime Pool 多实例 | verified |
| B-R04 | failed: missing `fluxion.runtime.resolver` module | passed: benchmark p95≤5ms assertion | `backend/tests/benchmarks/test_resolver_benchmark.py::test_B_R04_resolver_l1_hit_p95_under_5ms` | Resource Resolver L1 cache hit | verified |
| S-R17 | failed: missing `AgentRuntime`/`RequestContext` import | passed: 1/1 | `backend/tests/e2e/test_memory.py::test_S_R17_multi_layer_memory_flush_and_isolation` | AgentLoop → Memory SPI → 多层读写与触发落盘 | verified |
| S-R18 | failed: missing `AgentRuntime`/`RequestContext` import | passed: 1/1 | `backend/tests/e2e/test_context_compaction.py::test_S_R18_context_compaction_preserves_latest_raw_and_snapshot` | AgentLoop → Context Compactor → L1/L2 落盘 | verified |
| S-R19 | failed: missing `AgentRuntime` import | passed: 1/1 | `backend/tests/e2e/test_scheduler.py::test_S_R19_scheduler_runs_independent_approved_executions` | Scheduler → AgentLoop（独立 Execution） | verified |
| S-R20 | failed: missing `AgentRuntime`/`RequestContext` import | passed: 1/1 | `backend/tests/e2e/test_planning.py::test_S_R20_plan_execute_replans_failed_step_in_current_execution` | AgentLoop → plan-then-execute | verified |

| B-R07 | failed: missing `RequestContext` import | passed: benchmark p95≤20ms assertion | `backend/tests/benchmarks/test_snapshot_benchmark.py::test_B_R07_snapshot_builder_p95_under_20ms` | ExecutionSnapshot Builder | verified |

### Definition of Done

- Snapshot 单次执行配置漂移为 0。
- Runtime 无 Pod 本地 durable fact。
- required verifier、测试、类型检查和 Stop Gate 全部通过。

### Log

- [2026-08-23] generated (draft)
- [2026-08-23] started (in-progress)
- [2026-08-23] RED: 新增 11 个验收/benchmark 测试，均因 Runtime Kernel API 缺失失败。
- [2026-08-23] GREEN: `pytest --tb=short -q` 24 passed；`mypy backend/src backend/tests` passed；`ruff check backend/src backend/tests` passed；`py_compile` passed；`scripts/run_registry_contract_tests.py` 16 passed（SQLite + PostgreSQL）。
- [2026-08-23] completed (done)
