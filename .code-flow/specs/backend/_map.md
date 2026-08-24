# Backend Retrieval Map

## Purpose

Fluxion 后端是 Python 3.12+ 的无状态、插件化 Agent Runtime 与 Control Plane 基础包。当前已落地 Resource Registry、ExecutionSnapshot、Runtime Kernel、Hook/Event 与 Model Provider Plugin 路径；HTTP API/Service/Model 包仍是后续阶段占位。

## Architecture

- Framework/typing：FastAPI、Pydantic v2、strict mypy、ruff。
- Registry：SQLAlchemy async，同一 `RegistryStore` Contract 覆盖 SQLite 与 PostgreSQL。
- Runtime：`RequestContext -> ResourceResolver -> ExecutionSnapshot -> RuntimeContext -> AgentRuntime`。
- Plugin/Hook：Kernel 只依赖 typed event/hook contract；Plugin 通过 trust policy 和 capability contract 接入。
- External I/O：`httpx` model provider 必须带 timeout、retry/failover 行为。

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python 依赖、pytest/mypy/ruff 配置 |
| `backend/src/fluxion/resources/contracts.py` | Resource/Binding/ExecutionSnapshot schema，拒绝明文 Secret |
| `backend/src/fluxion/registry/store.py` | `RegistryStore` Protocol 与错误契约 |
| `backend/src/fluxion/registry/sqlalchemy_store.py` | SQLite/PostgreSQL async Store 实现 |
| `backend/src/fluxion/runtime/resolver.py` | Resource 解析、L1 cache、ExecutionSnapshot 构建 |
| `backend/src/fluxion/runtime/agent.py` | 无状态 `AgentRuntime`、model provider failover |
| `backend/src/fluxion/kernel/events.py` | typed Hook/Event bus、priority、timeout、fail policy |
| `backend/src/fluxion/plugins/loader.py` | Plugin trust enforcement 与 provider 注册 |
| `backend/src/fluxion/plugins/model_provider.py` | Stub 与 OpenAI-compatible model provider |
| `scripts/run_registry_contract_tests.py` | SQLite/PostgreSQL Contract Test runner |

## Module Map

```text
backend/src/fluxion/
├── resources/   # 版本化 Resource/Binding contract + tenant cache
├── registry/    # Store Protocol、SQLAlchemy schema、SQLite/PostgreSQL adapter
├── runtime/     # Context、resolver/snapshot、AgentRuntime、memory、scheduler、planning
├── kernel/      # typed Event/Hook scheduler
└── plugins/     # Plugin manifest/contract、trust loader、model provider
```

`api/`、`services/`、`models/`、`repositories/`、`protocols/`、`config/`、`errors/` 当前只有包边界，进入相关任务时再补实现。

## Data Flow

```text
RequestContext -> ResourceResolver -> ExecutionSnapshot
  -> RuntimeContext -> AgentRuntime -> ModelProviderRegistry/Plugin -> Trace/Memory

EventPayload -> TypedEventBus -> HookScheduler -> HookHandler -> RuntimeContext trace
```

## Navigation Guide

- 改 Resource schema/Secret 规则：看 `resources/contracts.py` 与 `backend/tests/unit/test_resource_schema.py`。
- 改 Registry adapter：看 `registry/*`，并跑 `backend/tests/contract/test_registry_store.py`。
- 改 Snapshot 解析：看 `runtime/resolver.py`、`backend/tests/integration/test_snapshot_resolution.py`。
- 改 Runtime 执行/model 路径：看 `runtime/agent.py`、`plugins/model_provider.py`、`backend/tests/e2e/test_model_provider.py`。
- 改 Hook/Plugin：看 `kernel/events.py`、`plugins/loader.py`、`backend/tests/integration/test_hooks.py`。
- Console API 统一响应/日志/Audit 规则：先读 `console-api-contract.md`、`logging.md`、`platform-rules.md`。
