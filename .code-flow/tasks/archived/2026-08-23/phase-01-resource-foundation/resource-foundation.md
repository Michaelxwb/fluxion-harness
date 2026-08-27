# Tasks: Fluxion Resource 与 Registry 基础

- **Source**: docs/design/fluxion-runtime-design-v1.7.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

建立所有后续 Runtime 与 Console 共享的 Resource Domain Model 和 RegistryStore Contract，确保 Dev SQLite 与 Prod PostgreSQL 只有 Adapter 差异，没有业务语义差异。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突时记录 `#NOTES` 并停止。
- **Acceptance**: 所有 Acceptance-Refs、required verifier、回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R07 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Runtime → SQLiteStore/PostgreSQLStore 两实现 | TASK-001 | verified |
| S-R10 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | SQLite + PostgreSQL 真实数据库 | TASK-001 | verified |
| E-R04 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | unit | Schema Validator | TASK-001 | verified |
| E-R07 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Tenant A/B Store + Cache | TASK-001 | verified |

---

## TASK-001: 实现 Resource Contract 与 SQLite/PostgreSQL RegistryStore

- **Status**: done
- **Priority**: P0
- **Depends**: 无
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-database#RULE-backend-database-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-R07, S-R10, E-R04, E-R07

### Description

建立所有后续 Runtime 与 Console 共享的 Resource Domain Model 和 RegistryStore Contract，确保 Dev SQLite 与 Prod PostgreSQL 只有 Adapter 差异，没有业务语义差异。

### Scope

- 定义 RuntimeProfile/Skill/MCP/Plugin/Policy/Binding/ExecutionSnapshot Pydantic Contract。
- 实现 RegistryStore Protocol。
- 实现 SQLiteRegistryStore 与 PostgreSQLRegistryStore。
- 建立共享 Migration 与 Repository Contract Test。
- 实现 Published Resource immutable、tenant scope 和 Secret 字段约束。

### Checklist

- [x] 先编写 S-R07/S-R10/E-R04/E-R07 的验收测试并记录 RED。
- [x] SQLite 与 PostgreSQL 使用完全相同的 Store Contract Suite。
- [x] 验证 Published Version 不可原地修改。
- [x] 验证 tenant A 不可读取 tenant B private Resource。
- [x] 验证 Definition 中出现明文 Credential 时失败。
- [x] 补齐 GREEN、断言位置和真实边界证据。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R07 | integration | `backend/tests/contract/test_registry_store.py` | `python3 -m pytest backend/tests/contract/test_registry_store.py -k S_R07` | 两种 Store 对同 Fixture 返回相同语义 | verified |
| S-R10 | integration | `backend/tests/contract/test_registry_store.py` | `FLUXION_REQUIRE_POSTGRES_CONTRACT=1 python3 scripts/run_registry_contract_tests.py` | CRUD/版本/Binding/并发冲突语义一致 | verified |
| E-R04 | unit | `backend/tests/unit/test_resource_schema.py` | `python3 -m pytest backend/tests/unit/test_resource_schema.py -k E_R04` | 明文 Credential 被拒绝 | verified |
| E-R07 | integration | `backend/tests/integration/test_tenant_registry.py` | `python3 -m pytest backend/tests/integration/test_tenant_registry.py -k E_R07` | 跨 tenant 资源无法读取 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-R07 | FAIL: temp pre-implementation index 缺 `fluxion.registry.sqlalchemy_store`，contract test collection error | PASS: `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/contract/test_registry_store.py -k S_R07 -q` → 8 passed；S-R10 同套件覆盖 PostgreSQL | `backend/tests/contract/test_registry_store.py:102,120,136,174,201,234` | `RegistryStore` fixture 参数化 SQLite；同一 contract suite 由 S-R10 在 PostgreSQL 容器再跑 | verified |
| S-R10 | FAIL: temp pre-implementation index 缺 Store 实现，`FLUXION_REQUIRE_POSTGRES_CONTRACT=1 python3 scripts/run_registry_contract_tests.py` collection error | PASS: `PATH="$PWD/.venv/bin:$PATH" FLUXION_REQUIRE_POSTGRES_CONTRACT=1 python3 scripts/run_registry_contract_tests.py` → 16 passed | `backend/tests/contract/test_registry_store.py:108-116,129-160,181-197,218-225,251-254` | Docker 启动真实 `postgres:16-alpine`；脚本注入 `FLUXION_POSTGRES_DSN`；SQLite/PostgreSQL 跑同一 test file | verified |
| E-R04 | FAIL: temp pre-implementation index 缺 `fluxion.resources.cache/contracts`，schema test collection error | PASS: `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/unit/test_resource_schema.py -k E_R04 -q` → 3 passed | `backend/tests/unit/test_resource_schema.py:8,23,34` | Pydantic model validator 直接拒绝 Definition 明文 secret；允许 `secret://` SecretRef | verified |
| E-R07 | FAIL: temp pre-implementation index 缺 Store 实现，tenant integration test collection error | PASS: `PATH="$PWD/.venv/bin:$PATH" python3 -m pytest backend/tests/integration/test_tenant_registry.py -k E_R07 -q` → 1 passed | `backend/tests/integration/test_tenant_registry.py:37,51-57` | SQLiteStore 真实 in-memory DB + `TenantResourceCache`，tenant A/B key scope 分离 | verified |

### Definition of Done

- 所有 Acceptance Contract verified。
- SQLite/PostgreSQL Contract Test 通过率 100%。
- required Spec verifier 全部通过。
- mypy/ruff/pytest/code-flow Stop Gate 全部通过。

### Log

- [2026-08-23] generated (draft)
- [2026-08-23] started (in-progress)
- [2026-08-23] completed (done)
