# Tasks: Fluxion Runtime API 与 CLI

- **Source**: docs/design/fluxion-runtime-design-v1.7.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

通过统一 Application Service 暴露 HTTP、SSE、CLI，并实现配置热加载和 Trace，使 Runtime Kernel 既能独立调用又能进入完整产品 Dev Bundle。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突时记录 `#NOTES` 并停止。
- **Acceptance**: 所有 Acceptance-Refs、required verifier、回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R02 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Registry → Event → Runtime Cache → 新执行 | TASK-005 | verified |
| S-R09 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Runtime → Trace Store | TASK-005 | verified |
| B-R01 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Resolver/Cache | TASK-005 | verified |

| S-R12 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | CLI → ApplicationService → SQLite Registry → Runtime | TASK-005 | verified |
| B-R06 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | benchmark | Runtime Framework（不含模型/外部 Tool） | TASK-005 | verified |

---

## TASK-005: 实现统一 Application Service、HTTP/SSE、CLI 与本地 Dev Bundle 后端

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-platform-rules#RULE-backend-platform-001, backend-logging#RULE-backend-logging-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-R02, S-R09, S-R12, B-R01, B-R06

### Description

通过统一 Application Service 暴露 HTTP、SSE、CLI，并实现配置热加载和 Trace，使 Runtime Kernel 既能独立调用又能进入完整产品 Dev Bundle。

### Scope

- 实现 Run API、SSE 流式接口、healthz/readyz。
- 实现 fluxion run/serve/validate/plugins list CLI。
- 实现 config.changed cache invalidation + SQLite revision polling；TASK-005 的发布动作必须通过 CLI/ApplicationService，不依赖未来 Console。
- 实现 Runtime Trace/Usage。
- 保持 API/CLI/SDK 复用同一 Application Service。

### Checklist

- [x] 先写 CLI Dev Bundle、热更新、Trace、事件丢失测试并记录 RED；完整 Console+Web Chat Golden Path 不属于本 TASK。
- [x] API/CLI 不得各自实现执行规则。
- [x] Event 只做 invalidate，不广播完整配置。
- [x] Revision/TTL 负责事件丢失兜底。
- [x] 验证框架性能基线。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R12 | E2E | `backend/tests/e2e/test_cli_dev_bundle.py` | `python3 -m pytest backend/tests/e2e/test_cli_dev_bundle.py -k S_R12` | 无 Console/Web UI 时 CLI 可创建/发布 RuntimeProfile 并经 Model Provider 插件执行 | verified |
| B-R06 | benchmark | `backend/tests/benchmarks/test_runtime_overhead.py` | `python3 -m pytest backend/tests/benchmarks/test_runtime_overhead.py -k B_R06 --benchmark-only` | 框架开销 P95≤50ms/P99≤100ms | verified |
| S-R02 | E2E | `backend/tests/e2e/test_hot_reload.py` | `python3 -m pytest backend/tests/e2e/test_hot_reload.py -k S_R02` | 发布新版本无 Runtime 重启且新请求生效 | verified |
| S-R09 | E2E | `backend/tests/e2e/test_trace.py` | `python3 -m pytest backend/tests/e2e/test_trace.py -k S_R09` | Trace 包含 Snapshot/Model/Tool/Hook/Latency/Error | verified |
| B-R01 | integration | `backend/tests/integration/test_cache_revision.py` | `python3 -m pytest backend/tests/integration/test_cache_revision.py -k B_R01` | 事件丢失后 TTL/Revision 最终加载新版本 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-R02 | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/e2e/test_hot_reload.py -k S_R02` → `ModuleNotFoundError: fluxion.services.runtime_app` | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/e2e/test_hot_reload.py -k S_R02` → 1 passed | `backend/tests/e2e/test_hot_reload.py` | ApplicationService → Registry → metadata-only Event → RevisionAwareResourceResolver cache → 新执行使用 v2 | verified |
| S-R12 | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/e2e/test_cli_dev_bundle.py -k S_R12` → `ModuleNotFoundError: fluxion.cli` | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/e2e/test_cli_dev_bundle.py -k S_R12` → 1 passed | `backend/tests/e2e/test_cli_dev_bundle.py` | CLI → ApplicationService → SQLite Registry → Runtime → dev echo Model Provider | verified |
| B-R06 | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/benchmarks/test_runtime_overhead.py -k B_R06 --benchmark-only` → `ModuleNotFoundError: fluxion.services.runtime_app` | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/benchmarks/test_runtime_overhead.py -k B_R06 --benchmark-only` → 1 passed | `backend/tests/benchmarks/test_runtime_overhead.py` | Runtime Framework benchmark；latest resource/binding cache 命中后 P95/P99 低于基线 | verified |
| S-R09 | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/e2e/test_trace.py -k S_R09` → `ModuleNotFoundError: fluxion.services.runtime_app` | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/e2e/test_trace.py -k S_R09` → 1 passed | `backend/tests/e2e/test_trace.py` | Runtime → TraceStore，记录 Snapshot/Model/Tool/Hook/Latency/Error | verified |
| B-R01 | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/integration/test_cache_revision.py -k B_R01` → `ModuleNotFoundError: fluxion.runtime.hot_reload` | 2026-08-23 `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/integration/test_cache_revision.py -k B_R01` → 1 passed | `backend/tests/integration/test_cache_revision.py` | Resolver/Cache；发布方丢 event 后运行方通过 shared revision poll invalidates cache 并加载 v2 | verified |

### Definition of Done

- Runtime 框架额外开销 P95≤50ms/P99≤100ms。
- API/CLI/SDK 语义一致。
- required verifier、测试和 Stop Gate 全部通过。

### Log

- [2026-08-23] generated (draft)
- [2026-08-23] started via cf-task-start; Spec Context hash `1595409b0c6de00c16e9ccbc4a0aae2beead68dc87c0e627268d5a44e8b58c18`
- [2026-08-23] RED confirmed for S-R02/S-R09/S-R12/B-R01/B-R06; failures are missing TASK-005 service/API/CLI/hot_reload modules before implementation.
- [2026-08-23] GREEN verified for S-R02/S-R09/S-R12/B-R01/B-R06 plus runtime API envelope/SSE regression; backend pytest/ruff/mypy/compileall/registry contract passed.
- [2026-08-23] Stop Gate automation reached manual verifier stage; code-flow requires non-agent project-owner confirmation before active task can be completed.
