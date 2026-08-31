# Tasks: runtime-workflow-kernel

- **Source**: .code-flow/tasks/archived/2026-08-31/runtime-phase2-hardening/runtime-phase2-hardening.design.md#2.3 功能方案（F-12）
- **Created**: 2026-08-31
- **Updated**: 2026-08-31

## Proposal

Workflow 只负责 Graph/Durability，Tool/Policy/Approval 统一复用 Execution Kernel。当前 `workflow_worker_bootstrap.capability_executor` 是显式 stub（只回显 `prefix/capability_ref/input`，源码注释「deep 执行体——AgentLoop/Tool Runtime——见后续」）。本任务落地 deep 执行体：worker 装配 ToolRuntime + PolicyDecisionService，capability/tool/mcp/agent 节点执行复用 `ToolRuntime.call`（含授权决策链），消除 workflow 侧重复的执行/授权/审批逻辑。

### Alignment

- **Scope**: F-12 Workflow 复用 Execution Kernel（后端）。
- **Decisions**:
  - 授权沿用发起用户 frozen effective 图（ADR-A002：workflow 内部 step 沿用发起用户授权，frozen 图进 durable 上下文）；
  - capability_ref → ToolDefinition（Registry）→ ToolDescriptor + executor（deep 执行体主体）；
  - capability executor 复用 `ToolRuntime.call`（含 PolicyDecisionService 决策链，phase2 TASK-005 已收口）。
- **Non-goals**: 不改变 Workflow 的 Graph/Durability 语义；不引入 workflow 侧第二套授权逻辑。
- **Acceptance**: capability/tool/mcp 节点经真实 ToolRuntime 执行、授权决策经 PolicyDecisionService、审批 gate 复用。

---

## Acceptance Coverage

> F-12 无结构化 S-/E-/B- 验收场景（design 追溯矩阵 F-12 对应「—」）。验收以 integration 测试为准：workflow capability 节点真实执行 ToolRuntime、授权决策链贯通、未授权 fail-closed。

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| — | design F-12 | integration | capability 节点 → ToolRuntime → PolicyDecisionService | TASK-003 | planned |

---

## TASK-001: 授权上下文 frozen effective 图入 durable 上下文

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案（F-12）
- **Spec-Refs**:
- **Acceptance-Refs**: N/A

### Description

capability/tool 节点执行沿用发起用户 frozen `effective_permissions`（ADR-A002「内部 step 沿用发起用户授权」）。当前 `CapabilityNodeRequest` 未携带授权上下文，需把 frozen 授权快照纳入 workflow durable 上下文并在执行期传入节点 executor，避免 capability 节点绕过授权或在 DBOS durable 上下文里实时重算。

### Checklist
- [ ] 定义 workflow 授权上下文契约（frozen effective_permissions 进 durable 上下文）
- [ ] `CapabilityNodeRequest` 增授权上下文字段（frozen 三元组 + policy version）
- [ ] [integration] 节点执行只读 frozen 授权、不触发实时重算

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | CapabilityNodeRequest、frozen 图 | 授权只读 frozen 图、不重算 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-002: ToolDefinition → ToolDescriptor 执行体解析

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案（F-12）
- **Spec-Refs**:
- **Acceptance-Refs**: N/A

### Description

`capability_ref`（`tool:`/`skill:`/`mcp:` 前缀）解析为可执行的 ToolDescriptor + executor：ToolDefinition（Registry，`capability_ref` + `adapter_ref`）→ ToolDescriptor（tool_id/parameters_schema/operation/idempotency/risk_level，phase2 TASK-003 契约）→ 注册 executor（adapter_ref → Adapter/Capability 执行体）。当前 capability_executor 是纯回显 stub，无真实解析。

### Checklist
- [ ] capability_ref → ToolDefinition 解析（Registry 读取，PUBLISHED 校验）
- [ ] ToolDefinition → ToolDescriptor（含 operation/idempotency/side_effect/risk_level）
- [ ] adapter_ref → executor 注册（deep 执行体）
- [ ] [integration] 解析未命中/未发布 → fail-closed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | Registry ToolDefinition、executor | capability_ref 解析出真实 executor | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---

## TASK-003: capability_executor 复用 Execution Kernel

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: runtime-phase2-hardening.design.md#2.3 功能方案（F-12）
- **Spec-Refs**:
- **Acceptance-Refs**:

### Description

worker 装配 ToolRuntime；capability/tool/mcp/skill executor 改走 `ToolRuntime.call`（复用 PolicyDecisionService 决策链 + 授权 + 审批 gate），消除回显 stub。DBOS 独立 event loop 下需处理 async engine 桥接（subworkflow sync resolver 路径 async SQLAlchemy engine 不可用，需同步/跨 loop 桥接）。

### Checklist
- [ ] worker 装配 ToolRuntime（+ 注入 ValidatorRegistry / PolicyDecisionService）
- [ ] capability_executor 改走 `ToolRuntime.call`（tool/mcp/skill 前缀）
- [ ] [integration] capability 节点真实执行 ToolRuntime、决策链贯通、未授权 fail-closed
- [ ] DBOS event loop 桥接（async engine 在 worker 主循环可用、subworkflow sync 路径隔离）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| — | integration | capability 节点 → ToolRuntime → PolicyDecisionService | 复用 Execution Kernel 执行/授权/审批 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 填写。

### Log
- [2026-08-31] created (draft)

---
