# Tasks: Fluxion Control Plane 基础

- **Source**: docs/design/fluxion-console-design-v1.6.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

建立 Control Plane 后端基础设施与核心管理 API；负责共享 Contract、RuntimeProfile/Resource、Binding、Policy、CredentialRef metadata、多租户与统一响应/日志。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突使用 `#NOTES` 停止并重新对齐。
- **Acceptance**: Acceptance-Refs、required verifier、NFR Gate 与回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C101 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Browser/API → Registry，不触发 Pod 创建 | TASK-101 | verified |
| S-C104 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | API → Binding Store → Runtime-visible user state | TASK-101 | verified |
| S-C109 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Shared Schema → Console API → Runtime | TASK-101 | verified |
| S-C111 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | FastAPI → Response Factory → Client | TASK-101 | verified |
| S-C112 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Middleware → Logger → Log Capture | TASK-101 | verified |
| E-C101 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | API → Registry immutable version | TASK-101 | verified |
| E-C102 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Tenant scope → Binding validation | TASK-101 | verified |
| E-C103 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | API → CredentialRef metadata | TASK-101 | verified |
| E-C105 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | AuthZ → Tenant Registry | TASK-101 | verified |
| E-C110 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Exception → Global Handler | TASK-101 | verified |
| E-C111 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Logger → Redaction | TASK-101 | verified |
| B-C101 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Version service concurrent publish | TASK-101 | verified |
| B-C102 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Visibility resolver | TASK-101 | verified |

---

## TASK-101: 实现统一 Console API、Resource/Binding/Policy/CredentialRef 基础

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: docs/design/fluxion-console-design-v1.6.md#3.3.1, docs/design/fluxion-console-design-v1.6.md#3.4, docs/design/fluxion-console-design-v1.6.md#2.5.2
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-platform-rules#RULE-backend-platform-001, backend-logging#RULE-backend-logging-001, backend-database#RULE-backend-database-001, backend-code-quality-performance#RULE-backend-quality-001, backend-directory-structure#RULE-backend-directory-001, fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-C101, S-C104, S-C109, S-C111, S-C112, E-C101, E-C102, E-C103, E-C105, E-C110, E-C111, B-C101, B-C102

### Description

建立 Control Plane 后端基础设施与核心管理 API；负责共享 Contract、RuntimeProfile/Resource、Binding、Policy、CredentialRef metadata、多租户与统一响应/日志。

### Scope

- 统一 Response/Exception/RequestContext/structlog/Redaction。
- RuntimeProfile/Skill/MCP/Plugin/Policy/Binding/CredentialRef Draft CRUD 与 Validate 基础。
- Tenant visibility、Binding 校验、Published immutable、并发 version conflict。
- Console/Runtime 共享 Schema/Contract compatibility。

### Checklist

- [x] 先写全部 Acceptance Contract 并记录 RED。
- [x] Handler 禁止手写响应字典。
- [x] CredentialRef 只保存引用元数据，不保存/回显 Secret。
- [x] 跨 tenant private Resource/Binding 必须 fail closed。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C101 | E2E | `backend/tests/e2e/test_console_resource.py` | `python3 -m pytest backend/tests/e2e/test_console_resource.py -k S_C101` | 创建 RuntimeProfile 且无 K8s Pod 动作 | verified |
| S-C104 | E2E | `backend/tests/e2e/test_user_binding.py` | `python3 -m pytest backend/tests/e2e/test_user_binding.py -k S_C104` | 同用户 Binding 可被多 Registry 实例等价解析 | verified |
| S-C109 | integration | `backend/tests/contract/test_shared_contracts.py` | `python3 -m pytest backend/tests/contract/test_shared_contracts.py -k S_C109` | Console/Runtime Contract 兼容 | verified |
| S-C111 | integration | `backend/tests/integration/test_api_response.py` | `python3 -m pytest backend/tests/integration/test_api_response.py -k S_C111` | 统一响应与 X-Request-ID 一致 | verified |
| S-C112 | integration | `backend/tests/integration/test_logging.py` | `python3 -m pytest backend/tests/integration/test_logging.py -k S_C112` | 结构化日志字段完整 | verified |
| E-C101 | integration | `backend/tests/integration/test_resource_version.py` | `python3 -m pytest backend/tests/integration/test_resource_version.py -k E_C101` | Published 不可原地修改 | verified |
| E-C102 | E2E | `backend/tests/e2e/test_binding_authz.py` | `python3 -m pytest backend/tests/e2e/test_binding_authz.py -k E_C102` | 跨 tenant Binding 拒绝 | verified |
| E-C103 | E2E | `backend/tests/e2e/test_secret_ref.py` | `python3 -m pytest backend/tests/e2e/test_secret_ref.py -k E_C103` | Secret 明文不存不回显 | verified |
| E-C105 | E2E | `backend/tests/e2e/test_console_authz.py` | `python3 -m pytest backend/tests/e2e/test_console_authz.py -k E_C105` | 跨 tenant private Resource 不泄露 | verified |
| E-C110 | integration | `backend/tests/integration/test_api_response.py` | `python3 -m pytest backend/tests/integration/test_api_response.py -k E_C110` | 异常统一映射且不泄露堆栈 | verified |
| E-C111 | integration | `backend/tests/integration/test_logging.py` | `python3 -m pytest backend/tests/integration/test_logging.py -k E_C111` | 敏感字段全部脱敏 | verified |
| B-C101 | integration | `backend/tests/integration/test_resource_version.py` | `python3 -m pytest backend/tests/integration/test_resource_version.py -k B_C101` | 同 base 并发仅一个发布成功 | verified |
| B-C102 | E2E | `backend/tests/e2e/test_visibility.py` | `python3 -m pytest backend/tests/e2e/test_visibility.py -k B_C102` | 同名不同 scope 按 resource_id 正确解析 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-C101 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/e2e/test_console_resource.py:10`, `:42` | Browser/API → Registry；`deployment_actions == ()` 证明无 Pod 动作 | verified |
| S-C104 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/e2e/test_user_binding.py:19`, `:62` | API 写 Binding，第二个 SQLite Registry 实例经 Runtime `ResourceResolver` 读取 | verified |
| S-C109 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/contract/test_shared_contracts.py:12`, `:39` | Console 写 ResourceDefinition，RuntimeApplicationService 使用同一 Store 执行 | verified |
| S-C111 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/integration/test_api_response.py:10`, `:25` | FastAPI → Response Factory → HTTP Client，Header/body request_id 一致 | verified |
| S-C112 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/integration/test_logging.py:22`, `:49` | RequestContextMiddleware → structlog JSON Renderer → caplog 捕获 | verified |
| E-C101 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/integration/test_resource_version.py:18`, `:42` | API → Registry exact version；Published update 返回 409 | verified |
| E-C102 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/e2e/test_binding_authz.py:16`, `:62` | tenant A Binding 校验真实查询 tenant A Registry，tenant B private 不可见 | verified |
| E-C103 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/e2e/test_secret_ref.py:16`, `:46` | ResourceBinding 校验 SecretRef，响应不回显明文 Secret | verified |
| E-C105 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/e2e/test_console_authz.py:10`, `:40` | AuthZ 通过 tenant-scoped Registry fail closed，不泄露 private 内容 | verified |
| E-C110 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/integration/test_api_response.py:34`, `:43` | ConsoleError → 全局 Exception Handler → 统一错误 envelope | verified |
| E-C111 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/integration/test_logging.py:74`, `:93` | Logger → Redaction Processor，Authorization/token/bind_code/api_key 均脱敏 | verified |
| B-C101 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/integration/test_resource_version.py:46`, `:70` | Console Version service per-resource publish lock + Registry 状态校验 | verified |
| B-C102 | FAIL: `ModuleNotFoundError: fluxion.api.console` | PASS: 单场景命令 + 验收合集 13 passed | `backend/tests/e2e/test_visibility.py:16`, `:53` | Visibility resolver 按 resource_id 读取，display_name 不覆盖 | verified |

### Definition of Done

- P0 API Contract 全部 verified。
- SQLite/PostgreSQL Repository Contract 保持一致。
- pytest/mypy/ruff/Stop Gate 全部通过。

### Log

- [2026-08-23] DeepSeek 评审修订：补依赖图、验收覆盖与任务内聚性。
- [2026-08-23] started (in-progress, Context-SHA256: f244060faff6adeec0bd00249bc4ab4c51648074d2e9fb6ffebd767b186f2f01)
- [2026-08-23] RED: 13 个 Acceptance-Refs 对应测试已写入；合集命令 collection 失败，缺失 `fluxion.api.console`/`ConsoleApplicationService`，符合先测后实现。
- [2026-08-23] GREEN: 13 个单场景 `-k` 命令全部通过；验收合集 `13 passed`；`python3 -m pytest backend/tests -q` → `62 passed`；`ruff check backend/src backend/tests`、`mypy backend/src backend/tests`、`compileall` 均通过；`scripts/run_registry_contract_tests.py` → `16 passed`（SQLite + PostgreSQL）。
- [2026-08-23] Stop Gate scope expansion: 补入 `backend-directory-structure`、`fluxion-runtime-core`、`fluxion-workflow-capability` required Spec-Refs 并应用到 TASK-101。
- [2026-08-23] Stop Gate blocked: 11 条 required manual verifier 待 project-owner 确认后生成 evidence。
- [2026-08-23] manual verifier: project-owner 确认 11 条 required manual verifier；Stop Gate evidence 已生成并通过。
- [2026-08-23] completed (done)
