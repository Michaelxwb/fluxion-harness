# Tasks: Fluxion Plugin 与 Hook

- **Source**: docs/design/fluxion-runtime-design-v1.7.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

建立 Everything is a Plugin 的可扩展内核，并用类型化 Hook/Event 承载安全、审批、观测等横切能力，同时显式区分可信与隔离扩展。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突时记录 `#NOTES` 并停止。
- **Acceptance**: 所有 Acceptance-Refs、required verifier、回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R06 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Event Bus → 多 Hook | TASK-003 | verified |
| S-R13 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | AgentLoop → Model Provider 插件 → LLM（Stub/真实 Provider） | TASK-003 | verified |
| E-R05 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Plugin Loader → Trust Policy | TASK-003 | verified |
| E-R06 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Hook → Timeout/Fail policy | TASK-003 | verified |
| B-R02 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | unit | Hook Scheduler | TASK-003 | verified |

| B-R05 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | benchmark | Hook Framework 调度 | TASK-003 | verified |

---

## TASK-003: 实现 Plugin Runtime 与类型化生命周期 Hook

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-R06, S-R13, E-R05, E-R06, B-R02, B-R05

### Description

建立 Everything is a Plugin 的可扩展内核，并用类型化 Hook/Event 承载安全、审批、观测等横切能力，同时显式区分可信与隔离扩展。

### Scope

- 实现 PluginManifest、CapabilityDescriptor、PluginContext。
- 实现 setup/shutdown 生命周期。
- 实现 Typed Event Bus 和 HookRegistration。
- 实现 priority/timeout/fail_policy/scope。
- 实现 trusted in-process 与 isolated extension policy boundary。
- 实现 Model Provider 插件 Contract（stream/non-stream、tool calling、timeout/failover）与默认实现（OpenAI-compatible HTTP），供 AgentLoop 完成推理（FEAT-19）。

### Checklist

- [x] 先写 Hook 顺序、timeout、fail policy、Plugin trust 测试并记录 RED。
- [x] 同 priority 执行顺序必须稳定。
- [x] fail_closed 超时必须阻断；fail_open 不得阻断主流程。
- [x] untrusted Plugin 默认不得进入 Runtime 进程。
- [x] 记录 Hook latency/结果 Trace。
- [x] Model Provider 作为第一个真实插件验证 Manifest/Loader/trust 体系；AgentLoop 经插件完成含 Tool Calling 的推理。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R06 | integration | `backend/tests/integration/test_hooks.py` | `python3 -m pytest backend/tests/integration/test_hooks.py -k S_R06` | Hook 按 priority 稳定执行 | verified |
| S-R13 | E2E | `backend/tests/e2e/test_model_provider.py` | `python3 -m pytest backend/tests/e2e/test_model_provider.py -k S_R13` | AgentLoop 经 Model Provider 插件完成含 Tool Calling 的推理；timeout/failover 按 agent policy 生效 | verified |
| E-R05 | integration | `backend/tests/integration/test_plugin_trust.py` | `python3 -m pytest backend/tests/integration/test_plugin_trust.py -k E_R05` | untrusted in-process 被拒绝 | verified |
| E-R06 | integration | `backend/tests/integration/test_hooks.py` | `python3 -m pytest backend/tests/integration/test_hooks.py -k E_R06` | fail_closed timeout 阻断后续执行 | verified |
| B-R02 | unit | `backend/tests/unit/test_hook_scheduler.py` | `python3 -m pytest backend/tests/unit/test_hook_scheduler.py -k B_R02` | 相同 priority 次序可重复 | verified |

| B-R05 | benchmark | `backend/tests/benchmarks/test_hook_benchmark.py` | `python3 -m pytest backend/tests/benchmarks/test_hook_benchmark.py -k B_R05 --benchmark-only` | Hook Framework P95≤10ms（不含外部 I/O） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-R06 | failed: missing `fluxion.kernel.events` module | passed: 1/1 | `backend/tests/integration/test_hooks.py::test_S_R06_hook_priority_order_and_trace_are_recorded` | Event Bus → 多 Hook；`TypedEventBus` + 两个真实 handler + RuntimeContext Trace | verified |
| S-R13 | failed: missing `fluxion.plugins.contracts` module | passed: 1/1 | `backend/tests/e2e/test_model_provider.py::test_S_R13_agentloop_uses_model_provider_plugin_tool_calling_and_failover` | AgentLoop → PluginLoader → ModelProviderRegistry → Stub/slow Provider | verified |
| E-R05 | failed: missing `fluxion.plugins.contracts` module | passed: 1/1 | `backend/tests/integration/test_plugin_trust.py::test_E_R05_untrusted_plugin_cannot_load_in_process` | Plugin Loader → Trust Policy | verified |
| E-R06 | failed: missing `fluxion.kernel.events` module | passed: 1/1 | `backend/tests/integration/test_hooks.py::test_E_R06_timeout_fail_policy_controls_dispatch_flow` | Hook → Timeout/Fail policy；fail_open 与 fail_closed 均断言 | verified |
| B-R02 | failed: missing `fluxion.kernel.events` module | passed: 1/1 | `backend/tests/unit/test_hook_scheduler.py::test_B_R02_same_priority_hook_order_is_stable` | Hook Scheduler | verified |
| B-R05 | failed: missing `fluxion.kernel.events` module | passed: benchmark p95≤10ms assertion | `backend/tests/benchmarks/test_hook_benchmark.py::test_B_R05_hook_dispatch_p95_under_10ms` | Hook Framework 调度 | verified |

### Definition of Done

- Hook 调度框架开销 P95 ≤10ms（不含外部 I/O）。
- Kernel 不反向依赖具体 Plugin。
- required verifier 和 Stop Gate 全部通过。

### Log

- [2026-08-23] generated (draft)
- [2026-08-23] started (in-progress)
- [2026-08-23] RED: 新增 Hook/Plugin/Model Provider 验收测试，均因 Plugin/Hook Contract 尚未实现失败。
- [2026-08-23] GREEN: 6 条 Acceptance Contract 单独命令通过；`pytest --tb=short -q` 31 passed；`mypy backend/src backend/tests` passed；`ruff check backend/src backend/tests` passed；`py_compile` passed；`scripts/run_registry_contract_tests.py` 16 passed（SQLite + PostgreSQL）。
- [2026-08-23] completed (done)
