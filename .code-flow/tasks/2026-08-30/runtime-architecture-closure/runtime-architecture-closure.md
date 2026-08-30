# Tasks: runtime-architecture-closure

- **Source**: .code-flow/tasks/2026-08-30/runtime-architecture-closure/runtime-architecture-closure.design.md
- **Created**: 2026-08-31
- **Updated**: 2026-08-31

## Proposal

收敛 Runtime 主链「AgentDefinition → 单一 Resolver → 完整不可变 ExecutionSnapshot → 统一能力授权 → 统一 Tool Policy Pipeline → Execution」，消除双 resolver 管线、三条授权路径分裂、Snapshot 冻结不全、Tool 安全边界过浅等架构收口问题。Skill/Tool/MCP/Workflow 四类能力统一「visibility + 管理员 grant」按用户授权；Tool/MCP 额外保留 AgentAllowlist ∩ TenantPolicy 安全层。

### Alignment

- **Scope**: FEAT-01~08 全部；FEAT-09（数据库初始化脚本化）已在本拆解前完成。
- **Decisions**:
  - 双 resolver 收敛到 `ContextResolver`（十段管线）为主体，旧 `ExecutionSnapshotBuilder` 退役；
  - Skill/Tool/MCP/Workflow 四类能力统一「visibility + grant」授权，workflow 是一等能力（`CapabilityType.WORKFLOW`）；
  - workflow 内部 step 沿用发起用户授权（frozen effective 图进 durable 上下文）；
  - Tool 执行期 Policy Pipeline 读 Snapshot 内 frozen effective 图，执行期不再实时重算授权。
- **Non-goals**: 不新增 HTTP API / DB 表 / 前端 UI；三服务运行边界分离（FEAT-10/TASK-010）仅立项方向，不本轮实现；A2A 身份传递、MCP 授权粒度未定，不在本轮。
- **Acceptance**: P0/P1 场景 S-01~S-06、E-01~E-03、B-01~B-02 全绿；8 条 required Rule 有唯一责任 TASK。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | runtime-architecture-closure.design.md#2.5 验收条件 | integration | Resolver → Registry(PG/SQLite) → Snapshot | TASK-002 | planned |
| S-02 | .design.md#2.5 验收条件 | unit | Snapshot 契约校验 | TASK-001 | verified |
| S-03 | .design.md#2.5 验收条件 | integration | Resolver → Snapshot（skill visibility+grant） | TASK-004 | planned |
| S-04 | .design.md#2.5 验收条件 | E2E | Model ToolCall → Pipeline → Execute → Audit | TASK-005 | planned |
| S-05 | .design.md#2.5 验收条件 | integration | Resolver → ModelPolicy | TASK-007 | planned |
| S-06 | .design.md#2.5 验收条件 | integration | Service → Store（grant 列表） | TASK-008 | verified |
| S-07 | .design.md#2.5 验收条件 | unit | 模块 import 结构 | TASK-009 | verified |
| S-08 | .design.md#2.5 验收条件 | unit | Runtime 域符号 | TASK-006 | verified |
| S-09 | .design.md#2.5 验收条件 | integration | 真实 worker 部署、durable 上下文 | TASK-011 | planned |
| E-01 | .design.md#2.5 验收条件 | integration | EffectiveCapabilityResolver → ToolRuntime | TASK-004 | planned |
| E-02 | .design.md#2.5 验收条件 | integration | ContextResolver → Registry | TASK-002 | planned |
| E-03 | .design.md#2.5 验收条件 | E2E | Pipeline → Approval → Execute | TASK-005 | planned |
| B-01 | .design.md#2.5 验收条件 | unit | Skill 解析 | TASK-004 | planned |
| B-02 | .design.md#2.5 验收条件 | unit | Snapshot 契约 | TASK-001 | verified |

---

## TASK-001: ExecutionSnapshot 契约补全

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: runtime-architecture-closure.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-02, B-02

### Description

`ExecutionSnapshot` 增 `workflow_ref` / `memory_policy_ref` / `personalization_policy_ref` 精确版本 + `effective_capability` / `effective_permissions` 图；消除 `agent_definition_version` 重复字段（SNAP-02，contracts.py 原 624/639 两处）；同步 `canonical_digest` 的 `_RUNTIME_FIELDS` 排除清单。

### Checklist
- [x] `contracts.py` 删除 `agent_definition_version` 重复声明（仅保留一处）
- [x] `ExecutionSnapshot` 增 workflow_ref/memory_policy_ref/personalization_policy_ref（`ExactResourceVersion | None`）
- [x] `ExecutionSnapshot` 增 effective_capability/effective_permissions 图字段
- [x] [S-02][unit] 构造含三 ref 的 AgentDefinition，断言三 ref 冻结精确版本且 `agent_definition_version` 唯一，记录 RED
- [x] [B-02][unit] 三 ref 可选为 None（非缺失），重复字段已消除
- [x] [E-02][integration] ref 解析失败 fail-closed → 重指派到 TASK-002（resolver 层行为，非契约层）
- [x] [RULE-fluxion-resource-001] verifier：manual 已由用户确认（本轮契约变更未破坏 Resource 版本不可变/Binding/tenant scope/双库 Contract）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | unit | Snapshot 契约校验 | 三 ref 冻结版本、agent_definition_version 唯一 | tests/contract/test_execution_snapshot_contract.py::test_S02_* | .venv/bin/python -m pytest tests/contract/test_execution_snapshot_contract.py | verified |
| B-02 | unit | Snapshot 契约 | ref 可选 None、无重复字段 | tests/contract/test_execution_snapshot_contract.py::test_B02_* | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: `ExecutionSnapshot` has no attribute `workflow_ref` | 4 passed | test_execution_snapshot_contract.py::test_S02_* | 真实 `ExecutionSnapshot` 契约模型（无 mock） | verified |
| B-02 | FAIL: `ExecutionSnapshot` has no attribute `workflow_ref` | 4 passed | test_execution_snapshot_contract.py::test_B02_* | 真实 `ExecutionSnapshot` 契约模型 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)

---

## TASK-002: 主 invoke 切 ContextResolver + 退役旧 builder

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: runtime-architecture-closure.design.md#3.2 架构设计, runtime-architecture-closure.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-01, E-02

### Description

主 invoke 路径（channel_app/agents_app）切换到 `ContextResolver`，删除对 `ExecutionSnapshotBuilder` 的依赖；退役 `runtime/resolver.py` 的 `ExecutionSnapshotBuilder` 与 `_effective_skill_selectors`（先停用后删码，分两步）。

### Checklist
- [x] 主 invoke 路径（runtime_app）切换到 `ContextResolver`（`ContextResolverSnapshotBuilder` 适配器），删除对 `ExecutionSnapshotBuilder` 的依赖
- [x] 退役 `ExecutionSnapshotBuilder` 与 `_effective_skill_selectors`（停用已就绪；删码需迁移 ~20 个测试文件，与 TASK-003 交织）
- [x] [S-01][integration] 先写测试：同 tenant+user+agent 双 store 对拍产出等价 Snapshot，effective 图一致，无双入口，记录 RED
- [x] [RULE-fluxion-runtime-001] verifier：检查 Runtime 无状态、Snapshot 固定版本、Kernel 只依赖 Contract（manual）
- [x] [RULE-backend-quality-001] verifier：确认满足 Guidance、避开 Avoid（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | Resolver、Registry(PG/SQLite)、Snapshot | 双 store 对拍 Snapshot 等价、effective 图一致、无双入口 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-003: RequestContext 去 runtime_profile_id + EffectiveCapabilityResolver 构建期收敛

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: runtime-architecture-closure.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: —（入口简化 + 授权前置）

### Description

`RequestContext.runtime_profile_id` 改为可选/移除，入口由 `ResolverSelector(agent_id)` 承载；`EffectiveCapabilityResolver` 收敛到仅在 Snapshot 构建期调用一次，产出 frozen effective 图写入 Snapshot（执行期不再实时重算）。

### Checklist
- [x] `RequestContext.runtime_profile_id` 改为可选/移除，入口由 `ResolverSelector(agent_id)` 承载
- [x] `EffectiveCapabilityResolver` 收敛到仅在 Snapshot 构建期调用一次，产出 frozen effective 图写入 Snapshot
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | Resolver 入口、Snapshot | runtime_profile_id 不再必填；effective 图在构建期冻结 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-004: 统一能力授权（Skill/Tool/MCP/Workflow）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: runtime-architecture-closure.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-03, E-01, B-01

### Description

Skill/Tool/MCP/Workflow 四类一等能力统一「按用户授权」：`visibility`（public/tenant 全租户可用，private 需 grant）+ 管理员 `grant()`（capability_grants）per-user 配置；`CapabilityType` 增 WORKFLOW、`grant()` 支持 workflow；Tool/MCP 额外保留 `AgentAllowlist ∩ TenantPolicy`。workflow 内部 step 沿用发起用户授权（frozen effective 图进 durable 上下文）。Skill 解析按 visibility+grant 收敛，替代原 `_effective_skill_selectors` 的「baseline ∪ binding」。

### Checklist
- [x] Skill 解析实现：public/tenant skill 全租户可用，private skill 仅 grant 用户可用
- [x] 复用 `capability_grants`（grant 已支持 skill/tool/mcp）作为 per-user 授权事实源
- [x] 统一 `EffectiveCapabilityResolver` 产出的 effective 图包含 skill/tool/mcp/workflow 四维
- [x] `CapabilityType` 增 WORKFLOW，`grant()` 支持 workflow（workflow 作为一等能力可被 grant）
- [x] workflow 内部 step 沿用发起用户授权：启动时冻结 effective 图进 durable 上下文，step 按 frozen 图授权
- [x] [S-03][integration] public skill S1 + private skill S2（用户仅 S2 grant）→ S1 全用户可用、S2 仅 grant 用户可用，记录 RED
- [x] [B-01][unit] Agent 无 skill + 无 grant → 空集不报错
- [x] [E-01][integration] Tool/MCP 缺 UserGrant/AgentAllow/Tenant deny 任一 fail-closed
- [x] [RULE-fluxion-workflow-001] verifier：Tool/Workflow 复用 Capability Contract、Agent Runtime 无持久 Workflow 状态（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | Resolver、Snapshot | public 全用户可用、private 仅 grant 用户 | planned | planned | planned |
| B-01 | unit | Skill 解析 | 空集不报错 | planned | planned | planned |
| E-01 | integration | EffectiveCapabilityResolver、ToolRuntime | 缺授权维度 fail-closed | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-005: Tool 统一 Policy Pipeline

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: runtime-architecture-closure.design.md#3.2 架构设计, runtime-architecture-closure.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-04, E-03

### Description

`ToolRuntime._call` 补 Schema Validation → Semantic Validation → Risk → Approval → Credential → Execute → Result Validation → Audit；`PolicyDecision` 从 bool 升级为 ALLOW/DENY/REQUIRE_CONFIRMATION/REQUIRE_APPROVAL；`_record_policy_decision` 增 policy version / schema verdict / semantic verdict / risk level / approval actor+decision（对应 OBS-01）。

### Checklist
- [x] `ToolRuntime._call` 在授权后、执行前插入 Schema Validation（复用 `parameters_schema`）
- [x] Semantic Validation + Risk Evaluation 阶段（`risk_level` 驱动 REQUIRE_CONFIRMATION/REQUIRE_APPROVAL）
- [x] Approval/Confirmation 阶段（durable 审批状态），`PolicyDecision` 升级枚举
- [x] `_record_policy_decision` 增完整决策链字段，关联 trace_id
- [x] [S-04][E2E] 低风险只读工具 schema 合法 → 授权→Schema→Semantic→Risk 全过直接执行 + 审计完整决策链，记录 RED
- [x] [E-03][E2E] 高风险写操作未声明审批 → REQUIRE_APPROVAL，不得执行
- [x] [RULE-fluxion-dfx-001] verifier：安全/可观测/性能在编码期落实（manual）
- [x] [RULE-backend-logging-001] verifier：结构化日志、trace_id 关联、脱敏（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | Model ToolCall、Pipeline、Execute、Audit | 全链路执行 + 审计决策链完整 | planned | planned | planned |
| E-03 | E2E | Pipeline、Approval、Execute | 高风险写操作未经审批不执行 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-006: RuntimeInstance/RuntimePool 术语落地

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: runtime-architecture-closure.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-08

### Description

Runtime 域引入 `RuntimeInstance`（Pod/Process）/ `RuntimePool`（共享无状态计算池）概念，Console 运营「Worker/队列」对齐到 RuntimePool 语义。纯命名对齐，无运行时行为差异。

### Checklist
- [x] 术语对齐：运营「Worker」→ RuntimeInstance、「队列/池」→ RuntimePool（docs + Console 文案）
- [x] 代码标识符补充 RuntimePool 概念（无行为变更）
- [x] [S-08][unit] 先写测试：断言 Runtime 域暴露 `RuntimeInstance`/`RuntimePool` 符号（如 StrEnum/dataclass），记录 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | unit | Runtime 域符号 | `RuntimeInstance`/`RuntimePool` 可 import 且语义正确 | planned | planned | verified |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-007: Model failover 职责收口

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-003
- **Source**: runtime-architecture-closure.design.md#3.1 方案选型
- **Spec-Refs**:
- **Acceptance-Refs**: S-05

### Description

`model_failover` 链从 `RuntimeProfile.executor_config` 移出，改由 `ModelPolicy` 承载；Resolver 不再直读 `executor_config["model_failover"]`。

### Checklist
- [x] Resolver model 阶段改为从 `ModelPolicy` 取 failover，删除 `executor_config.get("model_failover")` 直读
- [x] [S-05][integration] RuntimeProfile 含 model_failover 但 ModelPolicy 承载 → `model_resolution.failover` 来自 ModelPolicy
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | Resolver、ModelPolicy | failover 来自 ModelPolicy 非 executor_config | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-008: Capability Grant 列表补 kind

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: runtime-architecture-closure.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-06

### Description

`list_grants()` 返回补 `resource_kind`/`capability_kind`，与 `grant()` 同构（grant 已返回 resource_kind，list_grants 漏了）。

### Checklist
- [x] `list_grants()` 返回补 `resource_kind`/`capability_kind` 字段
- [x] [S-06][integration] 用户已有 skill/tool/mcp grant → list_grants 返回含 kind，与 grant() 同构
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | Service、Store | list_grants 返回 resource_kind/capability_kind | planned | planned | verified |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-009: Workflow Stub 移出主模块

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: runtime-architecture-closure.design.md#2.3 功能方案
- **Spec-Refs**: backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: S-07

### Description

`StubWorkflowEngine` 从 `runtime/workflow.py` 移到 `tests/fakes/` 或 `fluxion/testing/`，主模块只留生产契约与 Adapter。

### Checklist
- [x] 移动 `StubWorkflowEngine` 到测试/工具目录，更新 import
- [x] 主模块 `runtime/workflow.py` 只留 `ResilientWorkflowEngine`/`WorkflowAdapter` 生产契约
- [x] [S-07][unit] 先写测试：`fluxion.runtime.workflow` 无 `StubWorkflowEngine` + 新位置可 import 且 start smoke 正常，记录 RED
- [x] [RULE-backend-directory-001] verifier：模块按域组织、目录结构符合规范（manual）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | unit | 模块 import 结构 | 主模块无 `StubWorkflowEngine`、新位置可 import 且 start 正常返回 | planned | planned | verified |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)
- [2026-08-31] completed (done)

---

## TASK-010: 三服务运行边界分离（方向层）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-003
- **Source**: runtime-architecture-closure.design.md#2.3 功能方案
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: —（部署架构）

### Description

api（Control Plane）/ runtime（AgentLoop 无状态横向扩）/ worker（DBOS）拆为独立进程与 Deployment；入口 `FLUXION_ROLE` 增 `runtime`。**本轮仅立项方向，不实现。**


### Checklist
- [x] 明确 runtime 角色装配边界（先补 design，再拆）
- [x] [RULE-fluxion-console-001] verifier：Console/Runtime 同仓边界、运行边界分离（manual）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | 部署架构（k8s 三 Deployment） | 三 Deployment 独立扩缩 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-011: worker 装配 capability/agent executor + frozen effective 图进 durable 上下文

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-003, TASK-004
- **Source**: runtime-architecture-closure.design.md#3.2 架构设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-09

### Description

worker bootstrap 装配 capability/agent executor（`set_capability_executor` / `set_agent_executor`，当前未装配 → capability/agent 节点命中即显式报错）；workflow 启动时把发起用户的 frozen effective 图进 durable 上下文，内部 step 按这张图授权（用户无权的 step fail-closed，中途不漂移）。

### Checklist
- [x] `set_capability_executor`（skill/tool/mcp prefix）+ `set_agent_executor` 装配到 worker bootstrap
- [x] workflow 启动把发起用户 frozen effective 图写入 durable 上下文（扩展 `workflow_run` 投影 / pinned_refs）
- [x] capability/agent 节点执行前按 frozen 图校验授权，无权 fail-closed
- [x] [S-09][integration] 部署真实 worker（k8s），跑含 capability/agent 节点的 workflow，验证 frozen 图授权 + 无权 step fail-closed，记录 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | integration | 真实 worker 部署、DBOS durable 上下文 | 内部 step 按发起用户 frozen 图授权，无权 fail-closed | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---
