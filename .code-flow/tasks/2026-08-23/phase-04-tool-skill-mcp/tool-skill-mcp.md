# Tasks: Fluxion Tool Skill MCP

- **Source**: docs/design/fluxion-runtime-design-v1.7.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-23

## Proposal

让 Agent 具备调用 Tool、Declarative Skill、MCP 和 Workflow 的能力，但所有用户能力仍通过 Binding/Policy 动态解析，业务逻辑由 Capability 承载。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突时记录 `#NOTES` 并停止。
- **Acceptance**: 所有 Acceptance-Refs、required verifier、回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R04 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | User Binding → Policy → MCP Tool | TASK-004 | verified |
| S-R08 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Agent → Workflow Adapter → Workflow Engine Stub | TASK-004 | verified |
| E-R03 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Policy → Tool Runtime | TASK-004 | verified |
| E-R08 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | MCP Client → Credential Resolver | TASK-004 | verified |

| S-R11 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | Runtime → A2A Adapter → Stub Peer | TASK-004 | verified |
| E-R09 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | integration | LocalEncryptedSecretStore → Credential Resolver | TASK-004 | verified |
| S-R14 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Tool Runtime → 同步/异步/流式执行体 | TASK-004 | verified |
| S-R15 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Agent → Tool Runtime → 内置基础工具（time/calc/http.get/search_files/file） | TASK-004 | verified |
| S-R16 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Agent → run_command/code.exec → SandboxBackend | TASK-004 | verified |

---

## TASK-004: 实现 Tool、Skill、MCP 与 Workflow Adapter

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-workflow-capability#RULE-fluxion-workflow-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-R04, S-R08, S-R11, S-R14, S-R15, S-R16, E-R03, E-R08, E-R09

### Description

让 Agent 具备调用 Tool、Declarative Skill、MCP 和 Workflow 的能力，但所有用户能力仍通过 Binding/Policy 动态解析，业务逻辑由 Capability 承载。

> **Workflow 边界**：仅实现 WorkflowAdapter 接入协议（FEAT-13/S-R08，以 Engine Stub 验证）。Workflow Engine/DSL/durable state 与业务 WorkflowDefinition 归业务接入层，本任务不开发（见 Architecture Baseline §12 与 ADR-008）。

### Scope

- 实现 ToolDescriptor/ToolRuntime。
- 实现 Declarative Skill Runtime。
- 实现 MCPDefinition/Binding Resolver 和 MCP Client Pool Contract。
- 实现 SecretStore SPI，并提供 Dev `LocalEncryptedSecretStore`：AES-256-GCM、Master Key 仅来自环境/外部注入、Registry 只保存 SecretRef/密文元数据。
- 实现 WorkflowAdapter 与 Minimal A2A request/response/trace/auth Contract。
- 实现统一 Tool Result Contract（completed/started/streamed 三形态信封）。
- 实现内置基础工具集：time、calc、http.get（P0 零依赖）+ search_files/read_file/list_dir/write_file/run_command（P1，file.* 走 allowlist + 审批，run_command/code.exec 经 Sandbox Backend）。
- 实现 Sandbox Backend SPI 与平台键控后端注册：Linux bwrap 后端、dev 降级后端（显式非生产）、Windows 原生后端（AppContainer+Job Objects）标 P2 按需；fail-closed——无可用后端（含当前平台无原生后端）时 run_command/code.exec 拒绝。
- 接入 Tool/MCP 生命周期 Hook、Approval、Schema/Semantic Validation。

### Checklist

- [x] 先写授权交集、Credential revoke、Workflow adapter 验收测试并记录 RED。
- [x] User Grant ∩ Agent Allowlist ∩ Tenant Policy 不满足即拒绝。
- [x] MCP Client Pool Key 包含 credential_version。
- [x] Workflow durable state 不进入 Runtime。
- [x] Tool 调用写入 Policy Decision/Trace。
- [x] Tool Result Contract 三形态（同步/异步/流式）先写验收并记录 RED。
- [x] 内置基础工具（time/calc/http.get）走统一调用链，time/calc 无外部依赖。
- [x] file.* 越出 allowlist 路径拒绝、写操作走高危审批；run_command/code.exec 无沙箱时 fail-closed。
- [x] 平台矩阵断言：Linux 解析到 bwrap；无原生后端平台 run_command/code.exec fail-closed。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-R04 | E2E | `backend/tests/e2e/test_effective_capability.py` | `python3 -m pytest backend/tests/e2e/test_effective_capability.py -k S_R04` | 仅暴露交集内 Tool | verified |
| S-R08 | E2E | `backend/tests/e2e/test_workflow_adapter.py` | `python3 -m pytest backend/tests/e2e/test_workflow_adapter.py -k S_R08` | 返回 workflow_run_id 且 Runtime 无 durable workflow state | verified |
| S-R11 | integration | `backend/tests/integration/test_a2a_adapter.py` | `python3 -m pytest backend/tests/integration/test_a2a_adapter.py -k S_R11` | 最小 A2A request/response/trace/auth 可互操作 | verified |
| E-R09 | integration | `backend/tests/integration/test_local_secret_store.py` | `python3 -m pytest backend/tests/integration/test_local_secret_store.py -k E_R09` | AES-256-GCM 密文存储；Master Key 异常 fail closed；无明文泄漏 | verified |
| E-R03 | E2E | `backend/tests/e2e/test_tool_policy.py` | `python3 -m pytest backend/tests/e2e/test_tool_policy.py -k E_R03` | Agent Allowlist 未授权时拒绝 | verified |
| E-R08 | E2E | `backend/tests/e2e/test_mcp_credentials.py` | `python3 -m pytest backend/tests/e2e/test_mcp_credentials.py -k E_R08` | Credential revoke 后旧 Client 不复用 | verified |
| S-R14 | E2E | `backend/tests/e2e/test_tool_result_contract.py` | `python3 -m pytest backend/tests/e2e/test_tool_result_contract.py -k S_R14` | 三类执行体返回统一信封 completed/started/streamed | verified |
| S-R15 | E2E | `backend/tests/e2e/test_builtin_tools.py` | `python3 -m pytest backend/tests/e2e/test_builtin_tools.py -k S_R15` | time/calc 零依赖可用；file.* 越出 allowlist 拒绝、默认只读 | verified |
| S-R16 | E2E | `backend/tests/e2e/test_sandbox.py` | `python3 -m pytest backend/tests/e2e/test_sandbox.py -k S_R16` | 沙箱无网络/只读根/超时 kill；越权拒绝；平台矩阵：Linux→bwrap、无原生后端→fail-closed | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-R04 | FAIL: `ModuleNotFoundError: fluxion.runtime.capabilities` | PASS: `python3 -m pytest backend/tests/e2e/test_effective_capability.py -k S_R04 -q` | `backend/tests/e2e/test_effective_capability.py:93` | SQLite Registry ResourceDefinition + User MCP Binding + Tenant Policy Binding → EffectiveCapabilityResolver | verified |
| S-R08 | FAIL: `ModuleNotFoundError: fluxion.runtime.tools` | PASS: `python3 -m pytest backend/tests/e2e/test_workflow_adapter.py -k S_R08 -q` | `backend/tests/e2e/test_workflow_adapter.py:27` | AgentRuntime.start_execution → ToolRuntime → WorkflowAdapter → StubWorkflowEngine | verified |
| S-R11 | FAIL: `ModuleNotFoundError: fluxion.protocols.a2a` | PASS: `python3 -m pytest backend/tests/integration/test_a2a_adapter.py -k S_R11 -q` | `backend/tests/integration/test_a2a_adapter.py:17` | RuntimeContext trace/auth → A2AAdapter → StubA2APeer，含错误映射断言 | verified |
| E-R09 | FAIL: `ModuleNotFoundError: fluxion.runtime.secrets` | PASS: `python3 -m pytest backend/tests/integration/test_local_secret_store.py -k E_R09 -q` | `backend/tests/integration/test_local_secret_store.py:18` | LocalEncryptedSecretStore AES-256-GCM ciphertext + CredentialResolver + wrong master key fail-closed | verified |
| E-R03 | FAIL: `ModuleNotFoundError: fluxion.runtime.tools` | PASS: `python3 -m pytest backend/tests/e2e/test_tool_policy.py -k E_R03 -q` | `backend/tests/e2e/test_tool_policy.py:28` | AgentRuntime.start_execution → ToolRuntime policy intersection → policy decision trace | verified |
| E-R08 | FAIL: `ModuleNotFoundError: fluxion.runtime.mcp` | PASS: `python3 -m pytest backend/tests/e2e/test_mcp_credentials.py -k E_R08 -q` | `backend/tests/e2e/test_mcp_credentials.py:35` | MCPClientPool → CredentialResolver → LocalEncryptedSecretStore rotate/revoke | verified |
| S-R14 | FAIL: `ModuleNotFoundError: fluxion.runtime.tools` | PASS: `python3 -m pytest backend/tests/e2e/test_tool_result_contract.py -k S_R14 -q` | `backend/tests/e2e/test_tool_result_contract.py:37` | ToolRuntime 三执行体：同步 dict、WorkflowAdapter started、streamed MCP ToolResult | verified |
| S-R15 | FAIL: `ModuleNotFoundError: fluxion.runtime.builtin_tools` | PASS: `python3 -m pytest backend/tests/e2e/test_builtin_tools.py -k S_R15 -q` | `backend/tests/e2e/test_builtin_tools.py:51` | AgentRuntime.start_execution → ToolRuntime → Builtin tools；file allowlist/审批拒绝 | verified |
| S-R16 | FAIL: `ModuleNotFoundError: fluxion.runtime.builtin_tools` | PASS: `python3 -m pytest backend/tests/e2e/test_sandbox.py -k S_R16 -q` | `backend/tests/e2e/test_sandbox.py:40` | ToolRuntime → run_command/code.exec → RecordingSandboxBackend；SandboxBackendRegistry Linux bwrap / no native fail-closed | verified |

### Definition of Done

- Secret 不进入普通日志/Trace。
- Tool/Workflow 复用 Capability Contract。
- Tool Result Contract（completed/started/streamed）稳定，Workflow 走 started 异步。
- run_command/code.exec 全部经 Sandbox Backend，无后端时 fail-closed，不泄露沙箱外文件。
- required verifier、测试和 Stop Gate 全部通过。

### Log

- [2026-08-23] generated (draft)
- [2026-08-23] started (in-progress)
- [2026-08-23] completed (done)
