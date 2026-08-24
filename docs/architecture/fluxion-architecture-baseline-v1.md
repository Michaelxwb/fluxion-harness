# Fluxion Harness Architecture Baseline V1

> Status: coding baseline. This document supersedes earlier Muad/OpenClaw discussion artifacts for implementation purposes.

## 1. Product Definition

Fluxion is an open-source, stateless, plugin-oriented Agent Harness consisting of:

```text
Fluxion Repository
├── Runtime Kernel / Agent Runtime
├── Console API
├── Console Web (admin)
├── Chat Web (user channel)
├── Plugin / Skill / MCP runtime
├── Registry / Resource model
├── CLI / SDK
└── deploy / shared contracts
```

The **Runtime Kernel can be called independently through SDK/CLI**, but the **Fluxion product is distributed with Console by default** and the Console is the standard configuration entry: dev mode configures over SQLite, production over PostgreSQL. Config does not live in YAML/File — YAML is only an import/export format (see §4). Console and Runtime live in one repository and one contract/version governance model, but can run as separate processes/Deployments.

## 2. Runtime and Control Plane

```text
Console Web / API
       |
       v
Resource Registry
(SQLite dev / PostgreSQL prod)
       |
       v
Stateless Runtime Pool
       |
       +--> Tool / MCP / A2A
       +--> Workflow Adapter
       v
Capability / Workflow / Enterprise Systems
```

- Console manages resources; it does not create ordinary Agent Pods.
- Runtime reads Registry; it does not use Console API as source of truth.
- Kubernetes manages compute instances, HPA and isolation policy, not logical Agent lifecycle.
- Console outage must not prevent execution of already-published resources.

## 3. Resource Model

Everything configurable is a versioned Resource:

```text
RuntimeProfile
SkillDefinition + SkillBinding
MCPDefinition + MCPBinding
PluginDefinition + PluginBinding
WorkflowDefinition
PolicyDefinition
CredentialBinding / SecretRef
```

Naming mapping vs the V4.1 baseline（V4.1 的术语在 Fluxion 中做了重命名，功能语义一致）：

| V4.1 概念 | Fluxion 概念 |
|---|---|
| AgentDefinition（逻辑 Agent，Console 创建的对象） | RuntimeProfile（Console 创建/发布的运行态配置） |
| Agent Runtime / Executor（无状态执行面） | Agent（实际运行的 Runtime Service / Pod） |
| Agent 直接持有的 Skill/MCP | SkillBinding / MCPBinding（归 User/Tenant，Agent 只声明 Allowlist） |
| Workflow Engine / DSL | 业务接入层组件，不在开源 V1 范围（见 §12） |

Rules:
- Published versions are immutable.
- User/Tenant-specific configuration belongs to Binding, not RuntimeProfile.
- `EffectiveCapability = UserGrant ∩ AgentAllowlist ∩ TenantPolicy`.
- Tenant scope is mandatory for resources, bindings, caches, sessions, traces and auth decisions.
- Secrets never live in Definition/Binding plaintext; use SecretStore.

## 4. SQLite Development / PostgreSQL Production

```text
Dev:  Console + Runtime -> SQLite
Prod: Console + Runtime -> PostgreSQL
```

Both use the same domain schema, migrations, repository interfaces and RegistryStore contract tests. YAML is only an import/export format and is never the runtime source of truth.

Local dev must not require PostgreSQL or Redis. Production uses PostgreSQL as source of truth and Redis Streams for config notifications; PostgreSQL Transactional Outbox guarantees reliable event publication. Dev hot reload can use SQLite revision polling.

## 5. Execution Consistency

Every run builds an immutable `ExecutionSnapshot` containing exact versions of Agent/Skill/MCP/Plugin/Policy/model resolution.

```text
Request -> ResourceResolver -> ExecutionSnapshot -> RuntimeContext -> AgentLoop
```

A publish during execution affects only new executions. Mid-execution config mutation is forbidden.

## 6. Microkernel / Plugin / Hook

Everything extensible in Runtime should be represented by stable contracts:

- Agent Loop
- Model Provider
- Tool Runtime
- Skill Runtime
- MCP Runtime
- Memory/Context
- Storage
- Sandbox
- Guardrail/Approval
- Observability

Kernel only owns context, lifecycle, typed events, plugin registry, execution and contracts.

`Everything is a Plugin` does not mean every plugin runs in-process:
- trusted infrastructure plugin -> may run in-process;
- untrusted/business extension -> MCP/RPC/sandbox/isolated worker.

Hooks are typed lifecycle interception points with mandatory priority, timeout, fail policy and scope.

## 7. Tool / Capability / Workflow

```text
                   Capability
                      ^
             +--------+--------+
             |                 |
         Agent Tool       Workflow Step
```

Tool and Workflow Step are adapters. Business logic is implemented once behind Capability contracts.

Agent handles intent/reasoning/decision. Workflow Engine owns durable state, retries, compensation, timeouts, approvals and crash recovery. A Workflow may be exposed as a coarse-grained Agent Tool. Runtime never stores Workflow durable state.

Workflow **Engine / DSL / 执行**与 Capability 实现归**业务接入层**，不在开源 V1 范围：开源项目是业务无关的 Agent + Console harness，业务接入时才构建对应 Workflow。Agent Runtime 侧的 **Workflow Tool Adapter 接入协议**属于开源 V1（FEAT-13 / S-R08，见 §12）：Agent 通过 Adapter 调用 Workflow，获得 `workflow_run_id`，Runtime 不保存 durable state。

## 8. Identity and Channels

All identities resolve to one `PlatformUser` model. Internal IAM, Web, Mattermost and WeCom are identity sources/channels, not separate user types.

Web Chat is a first-class channel. An unbound Web identity cannot enter Agent Runtime and may only execute:

```text
/bind <code>
```

Bind code rules:
- single use;
- expires in 10 minutes;
- hash stored at rest;
- tenant-bound;
- 5 failed attempts freezes the code;
- full code never appears in log/audit;
- after bind, all Agent executions use the same PlatformUser resource context as other channels.

All IM channels share one **Channel Adapter Contract（统一 IM Gateway）**: inbound event normalization (signature/decryption → unified message), outbound push (channel-native message), and an identity-mapping hook. Web Chat is the first implementation (FEAT-26 / S-C119); Feishu/QQ/WeCom are added by implementing the contract only, never by modifying the Runtime's channel-agnostic core.

## 9. DFX Baseline

Required during coding:
- Availability: runtime availability target >= 99.95%.
- Performance: framework overhead P95 <=50ms/P99 <=100ms excluding model/tool; resolver L1 P95 <=5ms; snapshot P95 <=20ms; hook dispatch P95 <=10ms excluding hook I/O.
- Console: resource list P95 <=300ms; publish API P95 <=500ms.
- Chat: `/bind` P95 <=300ms; pre-model chat framework first-byte P95 <=200ms.
- Reliability: config publish interruptions = 0; in-execution version drift = 0.
- Security: tenant isolation, SecretRef, plugin trust boundary, policy intersection.
- Testability: SQLite/PostgreSQL same Store contract suite; P0/P1 automated acceptance >=95%.
- Observability: execution_id + trace_id + exact snapshot versions on every critical path.
- Maintainability: forbidden dependency directions enforced by architecture tests.

## 10. Coding Dependency Direction

```text
API / CLI / SDK
       -> Application Services
       -> Domain Contracts
       -> Repositories / Provider Contracts
       -> Concrete Adapters
```

Forbidden:
- Kernel -> concrete plugin/provider.
- Service -> direct ORM query.
- Runtime -> Console API for config truth.
- Console -> Runtime internal implementation.
- Frontend -> database.

## 11. Problem-driven rule

A core architecture change must identify the design driver it solves, document trade-offs in an ADR, and provide a validation strategy. See `docs/problems/design-drivers.md`.

## 12. Layer Boundary and Open-source Scope

Fluxion 的开源范围是**与业务无关的 Agent Harness**：换一家公司仍能基本原样复用的组件进入开源层；换一家公司就必须重写的属于业务层。**Workflow Engine 不属于开源范围**，它和业务 SOP 一样在业务接入时构建。

```text
Open-source（业务无关，开源 V1）
├── Agent Runtime（Kernel / Microkernel / Plugin / Hook）
├── Console / Control Plane
├── Resource Registry（SQLite dev / PostgreSQL prod，同一 Contract）
├── Skill / MCP / Plugin runtime
├── 内置基础工具（time / calc / http / search_files / file / run_command）
├── Sandbox Backend（隔离执行；Linux bwrap、macOS sandbox-exec，dev 降级非生产）
├── CLI / SDK
├── Web Chat（用户 Channel）
├── Channel Adapter Contract（统一 IM Gateway；Web Chat 为首个实现）
├── Workflow Tool Adapter（Agent 侧接入协议；Engine 由业务接入层提供）
└── 共享 Contract / Schema / 版本治理

Business layer（业务接入时构建，不进入开源仓库）
├── Workflow Engine / DSL / Durable State
├── Capability 业务实现
├── 企业 Connector（HR / CRM / ERP ...）
├── 企业身份与 Credential
├── 业务 Policy / 审批规则
└── 业务 UI
```

Rules:

- Workflow **Engine / DSL / 执行**与业务 Capability 实现属于业务层；开源 V1 不开发。**Workflow Tool Adapter 接入协议**在开源 V1 实现（FEAT-13 / S-R08），业务接入时以真实 Engine 替换 Stub。
- **Channel Adapter Contract（统一 IM Gateway）**在开源 V1 实现（FEAT-26 / S-C119），Web Chat 为首个实现；具体 IM 通道 Adapter（飞书/QQ/企微）复用度高且与业务无关，作为独立通道包按需补充，V1 不开发。
- Agent + Console 在不绑定业务时独立运行。
- 业务层通过稳定 Contract（Capability / MCP / HTTP / Workflow Tool Adapter）接入开源层，不得向 Kernel/Core 塞业务逻辑。
- 开源层代码不得出现企业特定业务（HR/CRM/ERP、企业身份、业务 SOP）。
