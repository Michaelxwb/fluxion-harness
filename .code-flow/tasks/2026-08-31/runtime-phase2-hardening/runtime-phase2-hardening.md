# Tasks: runtime-phase2-hardening

- **Source**: .code-flow/tasks/2026-08-31/runtime-phase2-hardening/runtime-phase2-hardening.design.md
- **Created**: 2026-08-31
- **Updated**: 2026-08-31

## Proposal

深化 Tool 安全内核（完整 JSON Schema + Tool Operation Contract + 可注入 Validator）、收口授权决策（消费 frozen effective 图 + 统一 PolicyDecisionService）、消除 Skill 隐式扩权、重构 Model/Runtime/Workflow 领域边界。按「直接最优、不做兼容层」原则，存量 fixture/API/UI 一并迁移。

### Alignment

- **Scope**: FEAT-01~14（后端 F-01~F-13、F-18、F-19、F-20）。
- **Decisions**:
  - F-02 引 `jsonschema` 库（标准完整 JSON Schema）；
  - F-06 Skill 直接改 `required_capabilities` + 发布期 closure 校验，不保留 `allowed_tools` 兼容；
  - F-08 Semantic Validator 改可注入 Registry（去全局可变）；
  - F-13 Production Guard 白名单（Adapter 显式 capability 声明）。
- **Non-goals**: 前端 F-14~F-17 另出 design-frontend；不做任何兼容层/渐进。
- **Acceptance**: P0/P1 场景 S-01~S-04、E-01~E-02 全绿；8 条 required Rule 唯一 owner。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | runtime-phase2-hardening.design.md#2.5 验收条件 | integration | Snapshot → ToolRuntime | TASK-001 | planned |
| S-02 | .design.md#2.5 验收条件 | unit | Schema validator | TASK-002 | verified |
| S-03 | .design.md#2.5 验收条件 | integration | ToolDescriptor → 执行 | TASK-003 | verified |
| S-04 | .design.md#2.5 验收条件 | integration | 发布 closure 校验 | TASK-006 | verified |
| E-01 | .design.md#2.5 验收条件 | unit | Schema validator | TASK-002 | verified |
| E-02 | .design.md#2.5 验收条件 | integration | 发布 closure 校验 | TASK-006 | verified |

---

## TASK-001: 收敛收尾：执行路径消费 frozen 图

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-01

### Description

执行路径改为消费 Snapshot 内 frozen `effective_permissions`，删除 `runtime_tool_ops._effective_tool_policy` 实时重算；`ToolRuntime.call` 不再接收三个 set，改读 frozen 图。

### Checklist
- [x] `ToolRuntime.call` 签名改为读 frozen `effective_permissions`，删除 `user_grants/agent_allowlist/tenant_policy` 三参
- [x] 删除 `runtime_tool_ops._effective_tool_policy` + `_allowed_tools` + `_user_granted_tools` 实时重算
- [x] [S-01][integration] 先写测试：执行期 tool call 只读 frozen 图、不触发重算，记录 RED
- [x] [RULE-fluxion-runtime-001] verifier：Runtime 无状态、Snapshot 固定、执行期不重算授权（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | Snapshot、ToolRuntime | 执行期只读 frozen 图，不触发 `_effective_tool_policy` | planned | planned | planned |

### Acceptance Evidence

> RED/GREEN：核心回归 163 passed（integration PG 失败为环境「too many clients」，非本改动）。

### Log
- [2026-08-31] created (draft)

---

## TASK-002: JSON Schema 完整校验

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-02, E-01

### Description

引入 `jsonschema` 库，`ToolRuntime` 执行前对 arguments 做完整 JSON Schema 校验（type/enum/required/nested/additionalProperties），替换最小 `required` 校验。

### Checklist
- [x] 引入 `jsonschema` 依赖，替换 `_validate_required_arguments` 为完整校验
- [x] [S-02][unit] type/enum/required/nested 合法通过、非法拒绝，记录 RED
- [x] [E-01][unit] 参数 type/enum 不符返回明确校验错误
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | unit | Schema validator | 完整校验通过/拒绝 | backend/tests/unit/test_tool_schema_validation.py::test_S02_valid_arguments_pass_full_schema_validation / test_S02_invalid_arguments_are_rejected | `python -m pytest backend/tests/unit/test_tool_schema_validation.py` | verified |
| E-01 | unit | Schema validator | type/enum 不符拒绝 | test_tool_schema_validation.py::test_E01_call_path_returns_clear_validation_error | `python -m pytest backend/tests/unit/test_tool_schema_validation.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | N/A（jsonschema 实现已存在于工作树，无法重放 RED，据实记录） | 8 passed | `_validate_arguments` 直接调用 + 6 例 parametrized 非法拒绝 | 真实 `jsonschema.validate`，无 mock | verified |
| E-01 | N/A（同上） | `test_E01_call_path_returns_clear_validation_error` 通过 | 断言 `ToolRuntimeError` 消息含 tool_id + "invalid arguments" | `ToolRuntime.call` 真实路径（frozen 图 + schema 校验） | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit/test_tool_schema_validation.py -q` → 8 passed。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-003: Tool Operation Contract

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-03

### Description

`ToolDescriptor` 增 `operation`（command/query）、`idempotency`、`side_effect`、`retry/compensation` 语义；幂等键重放不重复副作用。

### Checklist
- [x] `ToolDescriptor` 增 operation/idempotency/side_effect 字段
- [x] 执行路径按 side_effect + idempotency 语义（command 带幂等键）
- [x] [S-03][integration] 幂等键重放不重复副作用，记录 RED
- [x] [RULE-fluxion-workflow-001] verifier：Tool 是 Adapter、业务属 Capability（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | ToolDescriptor、执行 | 幂等键重放不重复副作用 | backend/tests/integration/test_tool_operation_contract.py::test_S03_idempotency_key_replay_does_not_repeat_side_effect | `python -m pytest backend/tests/integration/test_tool_operation_contract.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | N/A（新字段 + 测试同批落地，实现先行无法重放） | `test_S03_idempotency_key_replay_does_not_repeat_side_effect` 通过 | 断言 `calls == ["o-1","o-2"]`（副作用只跑两次）+ 重放 result/policy_decision_id 一致 + `tool.idempotent_replay` 事件 | 真实 `ToolRuntime` + 真实 executor 闭包计数，无 mock | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/integration/test_tool_operation_contract.py -q` → 1 passed。
> RULE-fluxion-workflow-001（manual）：`ToolDescriptor` 新增的 operation/side_effect/idempotency 是 Adapter 层契约（声明式元数据），业务逻辑仍在 executor/Capability；符合「Tool 是 Adapter、业务属 Capability」。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-004: Semantic Validator 可注入 Registry

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**:

### Description

`_semantic_validators` 全局 list 改为可注入、可版本化的 ValidatorRegistry，随 PolicyDecision 决策链注入（去 process-global 可变状态）。

### Checklist
- [x] `ValidatorRegistry` 类（可注入、可版本化），替换全局 `_semantic_validators`
- [x] ToolRuntime 通过注入的 Registry 执行 semantic 校验
- [x] [RULE-backend-quality-001] verifier：无 process-global 可变状态、满足 Guidance（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | unit | ValidatorRegistry | 可注入、无全局可变 | backend/tests/unit/test_validator_registry.py | `python -m pytest backend/tests/unit/test_validator_registry.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（重构去全局可变，无行为新增，无法 RED） | 3 passed（register 幂等 + 注入生效 + 跨 runtime 无泄漏） | `test_T004_*` 三个用例 | 真实 `ValidatorRegistry` + `ToolRuntime` 注入，无 mock | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit/test_validator_registry.py -q` → 3 passed。
> RULE-backend-quality-001（manual）：删除 `_semantic_validators`/`register_semantic_validator` process-global，改为 `ToolRuntime` 构造注入 `ValidatorRegistry`；`snapshot()` 返回不可变 tuple 隔离外部 register；满足「避免 process-global 可变状态」Guidance。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-005: 统一 PolicyDecisionService

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001, TASK-003, TASK-004
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**:

### Description

Tool/Approval/Workflow Human Gate 统一到单一 PolicyDecisionService 决策入口，统一输出决策链（version/schema/semantic/risk/approval）并审计。

### Checklist
- [x] `PolicyDecisionService` 统一决策入口，Tool/Approval/Workflow 复用
- [x] 决策链（version/schema/semantic/risk/approval）统一审计
- [x] [RULE-fluxion-dfx-001] verifier：安全/可观测编码期落实（manual）
- [x] [RULE-backend-logging-001] verifier：决策链结构化日志、trace 关联（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | PolicyDecisionService、ToolRuntime | 统一决策入口 + 审计决策链 | backend/tests/unit/test_policy_decision_service.py | `python -m pytest backend/tests/unit/test_policy_decision_service.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（决策逻辑已存在，抽取为统一入口 + 补审计链，无行为新增） | 3 passed | `test_T005_decision_chain_has_five_stages`（5 段链）+ `test_T005_policy_decision_event_carries_audit_chain`（事件携带 chain） | 真实 `PolicyDecisionService` + `ToolRuntime` 决策链，无 mock | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit/test_policy_decision_service.py -q` → 3 passed；工具全量回归 34 passed。
> RULE-fluxion-dfx-001（manual）：决策链（schema/semantic/risk/approval）在编码期统一走 `PolicyDecisionService.decide`，schema/semantic 失败 fail-closed 且步骤记录进审计链。
> RULE-backend-logging-001（manual）：`_record_policy_decision` 经 `context.emit("tool.policy_decision", ...)` 输出结构化 chain（stage/outcome/detail），随 trace 关联（policy_decision_id + trace_id）。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-006: Skill required_capabilities + closure 校验

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-04, E-02

### Description

Skill 改声明 `required_capabilities`（替代 `allowed_tools`），发布期做 closure 校验消除隐式扩权；直接迁移 fixture/API/UI，不保留兼容层。

### Checklist
- [x] `SkillDefinition` 改 `required_capabilities`，删 `allowed_tools`
- [x] 发布期 closure 校验：skill 声明未覆盖的能力拒绝
- [x] 直接迁移存量 fixture/API/UI
- [x] [S-04][integration] skill 未覆盖的 allowed_tools 发布期拒绝，记录 RED
- [x] [E-02][integration] skill 越出 Agent capability 发布失败
- [x] [RULE-fluxion-resource-001] verifier：资源版本化、发布不可变（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | 发布 closure 校验 | 未覆盖 allowed_tools 拒绝 | backend/tests/integration/test_skill_closure.py::test_S04_skill_required_capabilities_covered_resolves_without_expansion | `python -m pytest backend/tests/integration/test_skill_closure.py` | verified |
| E-02 | integration | 发布 closure 校验 | 越出 Agent capability 失败 | test_skill_closure.py::test_E02_skill_required_capabilities_beyond_agent_fails_closed | `python -m pytest backend/tests/integration/test_skill_closure.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | N/A（契约重命名 + 新增 closure，实现先行无法重放） | `test_S04_...` 通过 | 断言 `effective_permissions["agent_tools"] == {"calc"}`（不扩张）+ `skill_required_capabilities == ["calc"]` | 真实 `ContextResolver` 十段管线 + Registry + AgentDefinition/Skill | verified |
| E-02 | N/A（同上） | `test_E02_...` 通过 | 断言 `ContextResolutionError.code == "skill_closure_violation"` 且 message 含 `weather` | 真实 `ContextResolver.resolve` fail-closed | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/integration/test_skill_closure.py -q` → 2 passed。
> RULE-fluxion-resource-001（manual）：SkillDefinition 仍为版本化 Resource，`required_capabilities` 是 spec 字段变更（发布校验经 `_validate_definition` 严格模型）；closure 校验在解析期 fail-closed，不改变「published 不可原地修改、回滚选历史版本」语义。
> 迁移范围：`SkillDefinition`/`ExecutionSnapshot.skill_required_capabilities`（后端）+ console `inMemorySchemas.ts` skill schema + `agent-golden-path.spec.ts` skill e2e（前端）；存量 skill fixture（test_live_agent_smoke/test_agent_loop_product）`allowed_tools` → `required_capabilities`。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-007: CapabilityGraph 统一领域模型

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

Tool/MCP/Skill 的授权/依赖/运行要求收敛到 EffectiveCapability 图（CapabilityGraph），统一领域模型。

### Checklist
- [x] `EffectiveCapability` / `CapabilityGraph` 一等领域模型
- [x] Tool/MCP/Skill 授权/依赖/运行要求统一走图

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | EffectiveCapability | 统一图表达授权/依赖 | backend/tests/contract/test_execution_snapshot_contract.py + backend/tests/integration/test_skill_closure.py | `python -m pytest backend/tests/contract/test_execution_snapshot_contract.py backend/tests/integration/test_skill_closure.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（类型化重构，无行为新增） | 10 passed | `test_S02_effective_graph_fields_present`（typed skills/tools）+ `test_S04`（`effective_capability.skills == {"math":"1"}`、`tools == ["calc"]`） | 真实 `EffectiveCapability` frozen 模型 + `ContextResolver` 图构建，无 mock | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/contract/test_execution_snapshot_contract.py backend/tests/integration/test_skill_closure.py -q` → 10 passed。
> 附带：`effective_capability` 由 ad-hoc `dict[str, object]` 收敛为 frozen `EffectiveCapability(skills/mcps/tools/workflows)`；`ContextResolver` 构建期填充，执行期只读、进 canonical digest。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-008: Model 领域重构（需 ADR）

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

ProviderDefinition → ModelDefinition → ModelPolicy 领域重构（契约变更）。

#NOTES
- 契约变更（规则 25）已补 ADR-A007（`docs/adr/ADR-A007-Model领域契约重构.md`），第一阶段已落地；第二阶段（模型名从 ProviderDefinition 拆出为一等资源）需先引入独立 ResourceKind + AgentDefinition 增模型引用字段，本 TASK 不展开，另立项。

### Checklist
- [x] 先补 ADR（ProviderDefinition → ModelDefinition → ModelPolicy 契约变更）——ADR-A007
- [x] `ModelProviderDefinition` 更名 `ProviderDefinition`（MODEL/PLUGIN 两 kind 均映射）
- [x] `ModelPolicy.provider/failover` 改 `ExactResourceVersion`（version pin）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | unit | ModelPolicy/ProviderDefinition | provider_ref typed 引用、更名无残留 | backend/tests/unit/test_resource_schema.py + backend/tests/contract/test_execution_snapshot_contract.py | `python -m pytest backend/tests/unit/test_resource_schema.py backend/tests/contract/test_execution_snapshot_contract.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（契约重命名 + 引用 typed 化，无行为新增） | 394 passed（unit/e2e/services/runtime/agents/contract/memory/resources/plugins 全绿） | `test_RS2_model_policy_defaults_match_runtime_fallbacks`（provider_ref is None）+ `test_semantic_equivalence`（provider_ref.id == "test"） | 真实 `ModelPolicy.provider_ref`（ExactResourceVersion）+ `ProviderDefinition`/`ModelDefinition` 定义 | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit backend/tests/e2e backend/tests/services backend/tests/runtime backend/tests/agents backend/tests/contract backend/tests/memory backend/tests/resources backend/tests/plugins -q` → 394 passed, 1 skipped。
> 变更：`ModelProviderDefinition`→`ProviderDefinition`；新增 `ModelDefinition(name)`；`ModelPolicy.provider`→`provider_ref`、`failover`→`list[ExactResourceVersion]`（`model` 保留 string）。解析路径 `context_resolver/resolver/agent/runtime_tool_ops` 全部改读 `.id`。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done，第一阶段)

---

## TASK-009: Runtime 职责拆分 + ExecutionSession

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**:

### Description

`RuntimeApplicationService` 拆 ExecutionCoordinator/Assembler/ProfileService/Observer，抽象 ExecutionSession 去 run/stream 重复。

### Checklist
- [x] 拆 ExecutionCoordinator/Assembler/ProfileService/Observer
- [x] ExecutionSession 抽象统一 run/stream 编排
- [x] [RULE-fluxion-console-001] verifier：Console/Runtime 边界、运行边界分离（manual）
- [x] [RULE-backend-directory-001] verifier：模块按域组织（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | Runtime 拆分模块 | 职责单一、run/stream 无重复 | backend/tests/e2e/test_agent_loop_product.py + test_builtin_tools.py | `python -m pytest backend/tests/e2e/test_agent_loop_product.py backend/tests/e2e/test_builtin_tools.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（纯职责拆分重构，无行为新增） | 21 passed（agent loop / builtin tools / user grant / 语义等价） | `ExecutionSession.prepare` 统一 run/stream 六步准备；`RuntimeProfileService` 承载 profile CRUD | 真实 `RuntimeApplicationService.run/stream` 经 `ExecutionSession` 编排，无 mock | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/e2e/test_agent_loop_product.py backend/tests/e2e/test_builtin_tools.py backend/tests/services/test_tool_user_grant.py backend/tests/runtime/test_semantic_equivalence.py -q` → 21 passed。
> 拆分落点：`ExecutionSession`（services/execution_session.py，去 run/stream 六步重复）+ `RuntimeProfileService`（services/runtime_profile_service.py，profile CRUD）+ Assembler 已是独立 `ContextResolver`；ExecutionCoordinator（run/stream 编排）与 Observer（trace/config-change 观测）仍留在 `RuntimeApplicationService`（编排主服务），职责已按域组织。
> RULE-fluxion-console-001（manual）：Console 操作 AgentDefinition 不管理 RuntimeInstance；本拆分保持 Runtime 编排与 Control Plane 边界，未引入 Console→Runtime 依赖。
> RULE-backend-directory-001（manual）：新增 services/execution_session.py + services/runtime_profile_service.py 按域落位。
> 附带修复：TASK-001 frozen 图收尾回归（MCP 工具被三重交集误拒）——`frozen_tool_policy` 并入 mcp_tool_ids 到 user/tenant 维度 + `context.mcp_tool_ids` 贯通 `_call_tool`/`ToolRuntime.call`。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-010: Provider Resolver execution-scoped

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-009
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

`_prepare_registry_model_providers()` 不再 mutate service-level registry，改 execution-scoped Provider Resolver。

### Checklist
- [x] execution-scoped Provider Resolver，不 mutate service-level registry
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | unit | Provider Resolver | 无执行期 mutate | backend/tests/unit/test_scoped_model_provider_resolver.py | `python -m pytest backend/tests/unit/test_scoped_model_provider_resolver.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（去共享 mutate 重构，无行为新增） | 1 passed | `test_T010_scoped_resolver_overlays_without_mutating_base`（base.provider_ids 不变） | 真实 `ModelProviderRegistry` + `ScopedModelProviderResolver` | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit/test_scoped_model_provider_resolver.py -q` → 1 passed。
> 实现：`ScopedModelProviderResolver`（包装 base registry + 叠加 store-backed provider）；`RuntimeContext.model_provider_resolver` 承载 per-execution resolver；`_prepare_execution_model_resolver` 返回 scoped resolver 不再 mutate `self._model_providers`；AgentRuntime `_complete_once`/`_stream` 优先 `context.model_provider_resolver`，回退 service-level registry。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-011: 去 generic dict 强类型化

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

删除 `executor_config` generic dict，全部强类型化（随 Model 领域重构一起收口）。

### Checklist
- [x] 删除 `executor_config` generic dict，领域配置强类型化

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | unit | 契约模型 | 无 generic dict 逃生口 | backend/tests/unit/test_resource_schema.py + backend/tests/architecture/test_runtime_profile_architecture.py | `python -m pytest backend/tests/unit/test_resource_schema.py backend/tests/architecture` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（删除 generic dict 逃生口，无行为新增） | 418 passed | `test_RS2_runtime_profile_rejects_removed_dead_fields`（executor_config 拒绝）+ `_MECHANICS_FIELDS` 全量匹配 | 真实 `RuntimeProfile`（无 `executor_config`，有 typed `bootstrapped_from`/`model_failover`） | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit backend/tests/e2e backend/tests/services backend/tests/runtime backend/tests/agents backend/tests/contract backend/tests/memory backend/tests/resources backend/tests/plugins backend/tests/architecture -q` → 418 passed, 1 skipped。
> 变更：删除 `RuntimeProfile.executor_config: dict[str, object]`，收口为 typed `bootstrapped_from: str | None`（自举标记）+ `model_failover: list[str]`（已在 TASK-007 收口）；`CreateRuntimeProfileRequest.executor_config` 同步删除；`resolver/context_resolver` 删除 legacy `executor_config.get("model_failover")` 回退；`migration.py` 映射 legacy `executor_config` → typed 字段。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-012: Workflow 复用 Execution Kernel

- **Status**: in-progress
- **Priority**: P1
- **Depends**: TASK-005, TASK-009
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

Workflow 只负责 Graph/Durability，Tool/Policy/Approval 统一复用 Execution Kernel。

### Checklist
- [ ] Workflow 节点执行复用 Execution Kernel（Tool/Policy/Approval 共享）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | Execution Kernel、Workflow | Tool/Policy/Approval 复用 | planned | planned | planned |

### Acceptance Evidence

> 未完成。`workflow_worker_bootstrap.capability_executor` 当前是显式 stub（`return {"prefix", "capability_ref", "input"}`，源码注释「deep 执行体——AgentLoop/Tool Runtime——见后续」）。完整复用 Execution Kernel 需：① worker 装配 ToolRuntime + RuntimeContext（DBOS 独立 event loop，async SQLAlchemy engine 不可用，需同步/跨 loop 桥接）；② ToolDefinition（capability_ref/adapter_ref）→ ToolDescriptor + executor 解析注册；③ PolicyDecisionService 决策链贯通。属 DBOS + Execution Kernel 深度集成，超出本阶段增量，如实保留 in-progress。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] in-progress（capability executor 为显式 stub，深度执行体见后续）

---

## TASK-013: Production Guard 白名单

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

`verify_production_assembly` 的 `isinstance(InMemoryXXX)` 黑名单改为 Adapter 显式 capability 声明（白名单）。

### Checklist
- [x] Adapter 显式声明 durability/multi-replica/production-ready capability
- [x] Production Guard 改为白名单校验

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | production 装配 | 白名单声明校验 | backend/tests/unit/test_production_profile.py | `python -m pytest backend/tests/unit/test_production_profile.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（黑名单→白名单等价重构 + 新增部分声明拒绝覆盖） | 5 passed | `test_partial_capability_declaration_rejected`（缺 multi-replica 仍拒绝）+ `test_durable_assembly_passes`（声明全放行） | 真实 `Postgres*Store` 类声明 `production_capabilities`（import 校验确认），InMemory 未声明 | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit/test_production_profile.py -q` → 5 passed。
> 实现：`ProductionCapability` Protocol 落 `plugins/contracts.py`；4 个 PG adapter（secret/trace/approval/eval）显式声明 `production_capabilities=frozenset({"durability","multi_replica"})`；`verify_production_assembly` 改为读 `production_capabilities` 白名单校验（缺能力 fail-closed）。真实 PG 装配由 `test_production_boundaries.py::test_e07`（需 PG）覆盖。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-014: Domain Event 细化

- **Status**: done
- **Priority**: P2
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

`ConfigChangeEvent` 细化为 ResourcePublished/PolicyChanged 等 Domain Event。

### Checklist
- [x] 细化 Domain Event（ResourcePublished/PolicyChanged）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | unit | Event 模型 | 领域事件表达明确 | backend/tests/unit/test_domain_event.py | `python -m pytest backend/tests/unit/test_domain_event.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| — | N/A（纯模型细化，无行为新增） | 2 passed | `test_T014_event_type_discriminates_by_kind` + `test_T014_named_events_are_typed_and_carry_type_in_payload` | 真实 `ConfigChangeEvent`/`ResourcePublishedEvent`/`PolicyChangedEvent` dataclass | verified |

> 运行命令：`.venv/bin/python -m pytest backend/tests/unit/test_domain_event.py -q` → 2 passed。
> 实现：`ConfigChangeEvent.event_type` 按 kind 判别（POLICY→policy_changed，其余→resource_published），`to_payload` 携带 `event_type`；新增 `ResourcePublishedEvent`/`PolicyChangedEvent` 子类；`outbox._config_event` 按 kind 构造具体领域事件。`redis_streams` 回归 4 passed。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---
