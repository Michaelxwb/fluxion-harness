# Fluxion 最新 main 前后端源码深度审查与最终整改计划

> **文档编号**: REVIEW-PLAN-20260827-01  
> **文档版本**: v1.0  
> **审查日期**: 2026-08-27  
> **源码基线**: `main` / commit `bbe96f9`（“重构后端能力”）  
> **目标基线**: Fluxion V2.2 PRD + Architecture Remediation Roadmap  
> **审查方式**: 以 GitHub 最新 `main` 原始源码为事实源，逐文件/逐调用链静态审查前后端；当前执行环境 DNS 无法解析 github.com，未能本地 clone，因此本轮**不声称已执行全量 pytest / Playwright / 压测**。所有“已实现/未实现”判断均基于源码结构、调用链和实际代码路径。

---

# 1. 执行摘要

当前 `main` 并没有整体偏离 V2.2，但已经出现一个明显的结构不平衡：

> **后端 Phase 0 的架构修复和一部分 Phase 2 Memory 能力已经提前落地，而 Phase 1 的核心产品领域模型（AgentDefinition、UserProfile、Definition/State 代码边界）仍未完成；前端则基本仍停留在 V1 的 Resource-centric Console + Chat Channel。**

因此，下一阶段**不应该继续优先堆 Memory、Plugin 或 Workflow 内核细节**，也不应该只做前端视觉美化；应立即把开发重心拉回到：

1. AgentDefinition；
2. UserProfile / Preference / Capability Grant；
3. Definition Plane / State Plane 的代码边界；
4. ExecutionSnapshot V2 + Context Resolver；
5. 前端 Product Architecture / Journey / Product API；
6. DBOS Workflow 生产接入；
7. 最后再进行 Workspace / Agent Studio / Workflow Studio / User 360 的正式 UI 实现。

---

# 2. 最新 main 代码事实基线

## 2.1 已经实现且方向正确的核心能力

### Agent Runtime

`backend/src/fluxion/runtime/agent.py` 已经是真实 Agent Loop，而非单轮模型封装：

- ExecutionSnapshot 构建；
- Session Context 读取；
- Context Compaction；
- 多轮 Model → Tool → Model Loop；
- Tool duplicate guard；
- Model provider failover；
- per-call timeout；
- execution deadline；
- streaming；
- finally 中清理 execution-local L0。

结论：

> **Runtime Kernel 是现有项目最值得保留的资产之一，不应重写。**

---

### Session Memory 已经外置

`backend/src/fluxion/runtime/memory_sql.py` 的 `SQLSessionMemoryStore` 把 Session Memory 写入共享 SQL 存储。

当前语义：

```text
L0
= execution-local Working Memory

L1
= session raw history

SessionContextSummary
= session-scoped context compaction summary

L2
= legacy user-scoped raw history
```

生产路径的 Session correctness state 已不依赖某个 Pod 本地内存。

因此 V2.2 的 Stateless 目标应该继续定义为：

> **新增的 Profile / Personal Memory / Capability / Policy Resolution 同样不能依赖 Pod-local unique state，并且不同 Runtime Pod 必须得到等价 Snapshot。**

而不是重新做“把 Session 搬到 DB”。

---

### Memory hard issues 已经修复

最新代码已经处理了昨天讨论的多个关键问题：

1. 不再把 raw L1 message 无脑同步写入长期 L2；
2. SessionContextSummary 不再进入 `read_l2()`；
3. 原字符串拼接 `_summarize()` 已被真正 Summarizer SPI 替代；
4. Agent Runtime 已真正调用 `maybe_compact()`；
5. 支持 `ModelSummarizer`；
6. 支持 deterministic fallback；
7. summary 带 source range hash。

这部分和 V2.2 的 Memory taxonomy 已高度一致。

---

### Plugin / SPI 已明显收敛

`backend/src/fluxion/plugins/contracts.py` 已把 PluginType 收敛为：

```text
MODEL_PROVIDER
TOOL_PROVIDER
ARTIFACT_STORE
SEMANTIC_STORE
SECRET_PROVIDER
HOOK
```

已经删除旧的模糊 `TOOL/MEMORY/STORAGE` 语义。

同时定义了：

- SemanticStoreProvider；
- ArtifactStoreProvider；
- SecretProvider；
- typed Registry Protocol。

`PluginLoader` 也开始根据类型注册 Provider。

结论：

> ADR-EXT-001 的方向已经真实进入代码，不再只是文档。

---

### Pinned Resource / Retention 基础已形成

Registry 已增加：

- tombstone 状态；
- active reference；
- hard delete guard；
- retention；
- pinned recall；
- 并发保护。

这为长时间 Workflow 的：

```text
start
→ pin Skill@v3
→ wait 24h
→ Skill v4 发布
→ v3 deprecated
→ resume 仍使用 v3
```

提供了正确底座。

---

### Workflow build-vs-buy 已决策

`docs/adr/adr-013-durable-execution-vendor-pick.md` 已正式选定：

> **DBOS**

原因包括：

- Python-first；
- PostgreSQL-native；
- 无新增独立 workflow server；
- durable recovery；
- queue worker；
- self-host；
- 与现有 PostgreSQL 体系契合。

因此 V2.2 后续不再讨论“Temporal / DBOS / Restate / self-built 谁做默认生产 backend”，默认生产方向已经收敛为 DBOS。

但注意：

> **ADR Accepted ≠ DBOS 已进入生产代码。**

当前仍未看到正式 `DBOSWorkflowEngine` / DurableExecutionBackend 的生产实现链闭环。

---

# 3. 后端源码深度审查结论

# 3.1 AgentDefinition：当前最大 P0 缺口

当前 Runtime 主链仍然是：

```text
RequestContext
   ↓
runtime_profile_id
   ↓
RuntimeProfile
   ↓
Skill / MCP / Plugin / Policy
   ↓
ExecutionSnapshot
```

`ExecutionSnapshotBuilder.build()` 直接：

```text
request.runtime_profile_id
→ ResourceKind.RUNTIME_PROFILE
```

没有 AgentDefinition 产品实体。

当前 `RuntimeProfile` 仍包含：

- display_name；
- prompt；
- model_policy；
- allowed_skills；
- allowed_mcps；
- allowed_tools；
- plugin_bindings；
- guardrail_policy。

也就是说：

> **RuntimeProfile 仍同时承担 persona + runtime config + capability config。**

这是前后端产品模型无法升级的根因。

## 最终整改要求

新建：

```text
agents/
├── domain/
│   └── AgentDefinition
├── application/
├── repository/
└── api/
```

建议 AgentDefinition 表达：

```text
agent_id
tenant_id
version
name
display_name
description
icon
owner
visibility
status
runtime_profile_ref
default_workflow_refs
memory_policy_ref
capability_presentation
```

RuntimeProfile 只表达：

```text
model runtime
prompt runtime material
tool/mcp/skill runtime policy
timeout/deadline
guardrail
```

### 必须同步修改

- Chat Access；
- Channel routing；
- Runtime Request；
- Console；
- Workspace；
- Snapshot；
- Audit / Trace field。

---

# 3.2 ExecutionSnapshot V2：当前仍是 V1 Snapshot

现在 Snapshot 主要冻结：

```text
runtime_profile_id/version
model_resolution
skill_instructions
skill_allowed_tools
skill_versions
mcp_versions
plugin_versions
policy_version
binding_versions
```

仍缺：

```text
agent_definition_version
tool/capability exact versions
credential refs/version
user_profile_version
personal memory retrieval manifest
personalization policy version
user policy version
```

## 最终整改

Snapshot V2 应成为真正跨 Pod 等价性的可验证载体。

必须新增：

```text
canonical serialization
snapshot_digest
```

E2E：

```text
same tenant
same user
same agent
same requested execution inputs

Pod-A snapshot digest
==
Pod-B snapshot digest
==
Pod-C snapshot digest
```

要求：

> **100% 一致。**

不要求最终 LLM 文本 byte-for-byte 一致。

---

# 3.3 ADR-012 Spec Model SoT：仍有 residual

RuntimeProfile 已经：

```python
RuntimeProfile.model_validate(resource.spec_json)
```

这是正确的。

但 resolver 里 Skill 的部分辅助逻辑仍存在 raw：

```text
skill.spec_json.get(...)
```

这会继续造成：

```text
Pydantic validation rules
≠
runtime consumed fields
```

未来 AgentDefinition / WorkflowDefinition 如果照此复制，会重新产生两套 SoT。

## 最终整改

### 代码

统一：

```python
skill_model = SkillDefinition.model_validate(skill.spec_json)
```

只读：

```text
skill_model.instructions
skill_model.allowed_tools
```

### CI Architecture Rule

禁止以下目录新增 raw spec access：

```text
runtime/
services/
agents/
workflow/
capabilities/
```

禁止：

```text
.spec_json.get(
.spec_json[
```

允许位置仅限：

- deserialization boundary；
- migration；
- security scanner；
- registry low-level persistence。

---

# 3.4 Personal Memory：语义正确，但包归属错误

现在存在：

```text
runtime/personal_memory.py
```

里面已经开始承担：

- MemoryCandidate；
- Episodic/Semantic；
- PersonalMemoryStore；
- MemoryLearner；
- PersonalMemoryRetriever；
- Policy/Consent。

这些是：

> **长期用户 State Plane / Domain**

不是 Runtime 内部实现细节。

如果继续沿此方向，会出现：

```text
runtime/
├── personal_memory
├── user_profile
├── preference
├── consent
├── retention
└── personalization_policy
```

Runtime 再次变成 God Domain。

## 最终整改

迁移到：

```text
memory/
├── domain/
├── application/
├── repository/
├── retrieval/
└── policy/
```

Runtime 只保留：

```text
runtime/memory
= execution/session context support

runtime/summarizer
= session context compaction
```

`PersonalMemoryRetriever` 由 Context Resolver 调用，不由 AgentRuntime 自己持久化长期用户事实。

---

# 3.5 UserProfile：仍未真正实现

现有 PlatformUser 基础可复用。

已有：

```text
platform_users
channel_identities
```

说明 ChannelIdentity → PlatformUser 不需要重建。

真正缺的是：

```text
UserProfile
ProfileAttribute
Preference
PersonalizationPolicy
CapabilityGrant
```

## 新建 User Domain

```text
users/
├── domain/
│   ├── PlatformUser
│   ├── UserProfile
│   ├── ProfileAttribute
│   ├── Preference
│   └── CapabilityGrant
├── application/
├── repository/
└── api/
```

Profile 属性必须带：

```text
source
source_ref
confidence
is_explicit
visibility
user_editable
valid_from
valid_until
superseded_by
```

---

# 3.6 Context Resolver：V2.2 的关键桥梁仍缺

现有 ExecutionSnapshotBuilder 是：

> Resource Resolver + Snapshot Builder

它不是完整 User-aware Context Resolver。

最终应该形成：

```text
Request
 ↓
Identity Resolution
 ↓
PlatformUser
 ↓
AgentDefinition
 ↓
RuntimeProfile
 ↓
UserProfile
 ↓
Relevant Personal Memory
 ↓
Capability Grant
 ↓
Skill / Tool / MCP
 ↓
Credential refs
 ↓
User/Tenant Policy
 ↓
Context Resolver
 ↓
ExecutionSnapshot V2
```

## 设计边界

Context Resolver 输出：

```text
ResolvedUserContext
```

作为 execution-time projection。

它不是新的 durable UserRuntimeState。

---

# 3.7 Registry / Persistence：Definition-State 分离尚未落到代码组织

当前 `registry/schema.py` 仍同时容纳：

- Resource definition；
- PlatformUser；
- Channel Identity；
- Session Memory；
- Personal Memory；
- Chat Access；
- Active References；
- 其他平台 State。

即使共用 PostgreSQL 是正确的，代码包仍然没有真正体现：

```text
Definition Plane
≠
State Plane
```

## 最终整改

数据库不必拆实例，但 Python persistence module 要分域。

建议：

```text
persistence/
├── registry/
├── users/
├── memory/
├── workflow/
├── governance/
└── shared/
```

或由各 domain 自己拥有 repository/schema。

Registry 不应该继续成为全平台数据库模块的名字。

---

# 3.8 Workflow：当前已决策 DBOS，但实现链仍未闭环

`runtime/workflow.py` 已扩展 Protocol/Adapter/Resilient Wrapper。

但后续要避免：

```text
Fluxion retry
×
DBOS durable retry
×
Activity retry
```

造成 double retry。

## 职责边界

### Fluxion

负责：

- Workflow DSL；
- Node model；
- Business failure policy；
- Policy；
- Capability binding；
- Product status projection；
- Workflow Studio；
- API contract。

### DBOS

负责：

- durable execution；
- checkpoint；
- recovery；
- durable queue；
- durable wait/sleep；
- workflow/step durability。

## 最终代码建议

```text
workflow/
├── domain/
├── application/
├── backend/
│   ├── contract.py
│   └── dbos_backend.py
├── nodes/
├── projection/
└── api/
```

不要把 DBOS Engine 实现塞回：

```text
runtime/workflow.py
```

Runtime 只把 Published Workflow 暴露为 Capability。

---

# 3.9 Tool / MCP / Skill：继续作为 P0 保留能力

当前 Tool/MCP/Skill 基础健康。

整改原则：

- 不重新合并三者；
- Tool = executable atomic ability；
- MCP = protocol/provider；
- Skill = agent procedure / instructions；
- Workflow = durable orchestration；
- Capability = 产品/调用层统一抽象。

必须保证：

> 同一 Snapshot 在不同 Runtime Pod 中得到相同 Tool/MCP/Skill exact versions 与授权范围。

---

# 3.10 Plugin / Provider：基本正确，继续完成 lifecycle

现在扩展模型已经比 V1 清晰很多。

下一步重点：

- pgvector provider；
- ArtifactStore provider；
- SecretProvider production provider；
- HOOK lifecycle；
- tenant isolation；
- health；
- observability；
- unload / active-reference guard。

不要重新发明第二套 SPI registry。

---

# 4. 前端源码深度审查结论

前端是当前项目最大的产品化欠账。

不是“样式不好看”，而是：

> **前端仍然直接把后端内部 Resource 当产品领域对象。**

---

# 4.1 Console 默认入口就是 Resource

当前：

```tsx
initialView = "resources"
```

也就是说后台用户打开 Console 第一眼是：

```text
运行资产
```

而不是：

```text
Overview
```

这说明前端核心思维仍然是：

> backend resource administration

不是：

> user task / journey completion

---

# 4.2 当前 Console IA 是后端资源映射

实际一级导航：

```text
概览与运行
  运行时态
  执行记录

定义与编排
  运行资产
  流程编排

访问与授权
  用户管理
  资源绑定

治理与质量
  插件钩子
  能力注册
  能力评测
  操作审计
```

问题不是每个菜单单独不合理，而是组合起来没有：

```text
Build Journey
Admin Journey
```

---

# 4.3 ResourcesPage 证明了 Resource-driven 产品模型

当前资源列表把：

```text
runtime_profile
skill
mcp
plugin
policy
workflow
```

全部放在同一个“运行资产”页面。

用户创建资源需要理解：

```text
类型
资源 ID
版本
Spec
```

虽然 ADR-012 已让 SpecForm 根据 JSON Schema 自动生成表单，这是工程进步，但仍然只是：

> **Resource CRUD 变好用了**

并没有变成：

> **Agent 产品构建体验**

---

# 4.4 UsersChannelsPage：最典型的产品抽象泄漏

当前用户管理页会加载：

```text
PlatformUser
+
runtime_profile resources
```

管理员选择：

```text
运行态
```

然后给用户签发：

```text
runtimeProfileId
```

的 Chat Access。

这说明：

> RuntimeProfile 仍然是用户产品入口。

这和 AgentDefinition V2.2 目标直接冲突。

最终应变成：

```text
User
 ↓
Agent Access / Capability Access
 ↓
Chat/Workspace
```

不是：

```text
User
 ↓
Runtime Profile
 ↓
Chat Token
```

---

# 4.5 Chat 仍然只是 Channel，不是 Workspace

当前 Chat 状态只有：

- content；
- messages；
- access；
- platformUserId；
- conversationId；
- sending。

页面：

```text
Fluxion 对话
runtimeProfileId
已绑定 platformUserId
消息列表
输入框
```

没有：

- Home；
- Agents；
- Tasks；
- Approvals；
- History；
- Profile；
- Memory；
- Settings。

## 结论

`apps/chat` 作为 Chat component 合格。

但不能继续把它当：

> Fluxion 普通用户产品。

应该逐步演进：

```text
apps/workspace
└── features/chat
```

---

# 4.6 Workflow Studio 目前本质是 JSON Editor

当前 Workflow 页面虽然支持：

- list；
- version；
- draft；
- save；
- validate；
- publish。

这是 Control Plane 基础。

但编辑主体是：

```text
TextArea
= Workflow DSL JSON
```

Builder 需要手写/阅读 JSON。

这不符合 V2.2 的 Builder Journey。

最终应该：

```text
Workflow Studio
├── Node Palette
├── Flow / Structure
├── Properties
├── Input/Output Mapping
├── Test
├── Trace
├── Version
└── Advanced DSL
```

其中 DSL 应该是：

> Advanced View

而不是默认 Builder UI。

---

# 4.7 当前缺 Agent Studio

因为后端 AgentDefinition 尚未建立，前端自然没有：

```text
Agents
└── Agent Detail
```

当前 Builder 需要自己在：

```text
Resources
Bindings
Workflow
```

之间拼 Agent。

这是当前 Builder Journey 最严重的问题。

---

# 4.8 当前缺 User 360

当前用户页主要做：

- create user；
- issue chat access；
- revoke access。

没有用户聚合详情：

```text
Overview
Identity
Profile
Memory
Capabilities
Policies
Channels
Activity
Approvals
Security
```

这意味着 Admin 必须跨：

```text
Users
Bindings
Runs
Audit
```

自己拼一个用户的状态。

---

# 4.9 当前缺真正 Overview / Operations Center

Console 默认不是 Dashboard。

也没有统一：

```text
failed runs
stuck workflows
MCP health
runtime health
pending approvals
recent releases
```

运营视角仍然是：

> 查询底层记录

而不是：

> 看平台风险和待处理事项。

---

# 4.10 Frontend API 仍是 Resource API，而非 Product API

前端大量直接调用：

```text
listResources
getResource
listVersions
createDraft
updateDraft
publishVersion
listPlatformUsers
issueChatAccess(runtimeProfileId)
```

这是当前 IA 无法产品化的深层原因。

如果未来 Workspace 首页自己分别调用：

```text
resources
bindings
runs
approvals
memory
```

在浏览器拼数据，会继续和 Control Plane 内部模型强耦合。

## 必须增加 Product API / BFF Projection

例如：

```text
GET /workspace/home
GET /workspace/agents
GET /workspace/tasks
GET /workspace/profile
GET /studio/agents/{id}
GET /admin/users/{id}/overview
GET /operations/overview
```

这些是 Product ViewModel API。

底层仍然可以调用 Registry / Run / Profile / Workflow Service。

---

# 5. 三条用户旅程最终目标

# 5.1 普通用户 Journey

```text
Workspace Home
 ↓
我能做什么
 ↓
选择 Agent / Capability
 ↓
Chat 或启动 Task
 ↓
执行
 ↓
需要时 Waiting for me / Approval
 ↓
完成
 ↓
History / Result
 ↓
Profile & Memory feedback
```

一级导航：

```text
Home
Agents
Tasks
Approvals
History
Memory & Profile
Settings
```

普通用户不得看到：

- RuntimeProfile；
- Registry；
- Binding；
- PluginType；
- ExecutionSnapshot；
- SemanticStore；
- DBOS。

---

# 5.2 Builder Journey

```text
Build / Agents
 ↓
Create Agent
 ↓
Instructions
 ↓
Capabilities
 ↓
Workflow
 ↓
Memory Policy
 ↓
Test
 ↓
Trace / Eval
 ↓
Publish
```

Agent Detail：

```text
Overview
Instructions
Capabilities
Workflow
Memory Policy
Test
Trace
Eval
Versions
Advanced Runtime
```

`Advanced Runtime` 才允许看到 RuntimeProfile。

---

# 5.3 Admin Journey

同一个后台 Persona。

```text
Overview
 ↓
Users
Governance
Operations
Platform
```

User 360：

```text
Overview
Identity & Channels
Profile
Memory
Capabilities
Policies
Activity
Approvals
Security
```

---

# 6. 最终整改原则

## P-01 Runtime 不重写

保留 Agent Loop / Tool/MCP/Skill / Snapshot 思想。

---

## P-02 AgentDefinition 必须先于 Workspace / Agent Studio

不能先写漂亮 Agent UI，再继续把 RuntimeProfile 当 Agent。

---

## P-03 UserProfile 必须先于“个性化 UI”

没有真实 User Domain 时，不做假 Profile 页面。

---

## P-04 Personal Memory 从 Runtime Domain 移出

Session Context 属 Runtime。

Personal Memory 属 User/Memory State Plane。

---

## P-05 Context Resolver 是无状态横向扩展的核心

Stateless 的验收不是“没本地变量”，而是：

> 任意 Pod 从共享事实源解析出的执行语义等价。

---

## P-06 Frontend IA 不允许按 Resource 自动增长

强制规则：

> 后端新增一个 Resource，不构成前端新增一级菜单的理由。

---

## P-07 Product API 与 Control API 分离

Control API 服务底层定义/状态管理。

Product API 服务 Journey。

---

## P-08 Workflow 产品归 Fluxion，Durability 归 DBOS Backend

不要重复实现 DBOS 已解决的 durable kernel。

---

# 7. 最终整改路线

采用 7 个阶段。

```text
Phase 0  Baseline Cleanup
Phase 1  Agent/User Domain + Frontend Product Architecture
Phase 2  Context/Snapshot/Memory + Product API
Phase 3  DBOS Workflow Platform
Phase 4  Workspace + Agent/Workflow Studio + User 360
Phase 5  Governance / Infra / OTel / Eval
Phase 6  Scale / Chaos / UX / Release
```

---

# 8. Phase 0 — Baseline Cleanup

## 目标

先消除继续污染后续开发的代码和文档偏差。

### TASK-B001 ADR-012 residual cleanup

修改 resolver 中所有 raw `spec_json.get()`。

建立 architecture test。

**DoD**
- runtime/service/application 层 raw spec access = 0。

---

### TASK-B002 Personal Memory package move

从：

```text
runtime/personal_memory.py
```

迁至：

```text
memory/
```

不改业务语义，只修 ownership。

**DoD**
- runtime package 不再拥有 durable personal-memory repository/domain。

---

### TASK-B003 Persistence boundary

停止继续向 `registry/schema.py` 添加 User/Memory/Workflow State。

建立 domain repository/schema module。

---

### TASK-B004 README / ADR Index 更新

README 当前仍写：

```text
Agent = Runtime Pod
UserRuntimeState = ...
```

必须修正。

ADR-008 标记：

```text
Amended by ADR-013
```

---

### TASK-B005 V2.2 docs 成为仓库正式事实源

将 V2.2 PRD / Roadmap 放入 repo docs tree，并从 README 链接。

---

# 9. Phase 1 — Agent/User Domain + Frontend Product Architecture

Phase 1 是接下来真正最高优先级。

## Gate 1A — Agent Domain

### TASK-A101 AgentDefinition
### TASK-A102 Agent repository
### TASK-A103 Agent service/API
### TASK-A104 RuntimeProfile semantics shrink
### TASK-A105 runtime_profile_id → agent_id product routing

如果 surface inventory 证明有真实外部 token/channel：
- rollover；
否则：
- 开发期直接 reset。

不保留永久双字段。

---

## Gate 1B — User Domain

### TASK-U101 UserProfile
### TASK-U102 ProfileAttribute
### TASK-U103 Preference
### TASK-U104 PersonalizationPolicy
### TASK-U105 CapabilityGrant
### TASK-U106 User repository/API

复用已有：

```text
channel_identity → platform_user
```

---

## Gate 1C — Frontend Product Architecture

这是新的 P0 Gate，不等 Phase 4 才做。

产物必须有：

```text
Persona
Journey
IA
Page Map
Product Domain Model
ViewModel
Product API contract
UX acceptance scenario
```

冻结：

### Workspace

```text
Home
Agents
Tasks
Approvals
History
Memory & Profile
Settings
```

### Console

```text
Overview

Build
  Agents
  Workflows
  Capabilities
  Eval

Users

Governance

Operations

Platform
```

### Gate Rule

禁止因为 backend Resource 存在而新增一级页面。

---

# 10. Phase 2 — Context / Snapshot / Memory / Product API

## TASK-R201 ExecutionSnapshot V2

新增：

```text
agent_definition_version
tool/capability versions
credential refs/versions
user_profile_version
memory retrieval manifest
user/personalization policy versions
snapshot_digest
```

---

## TASK-R202 Context Resolver

实现：

```text
Identity
→ User
→ Agent
→ Runtime
→ Profile
→ Memory
→ Capability
→ Credential
→ Policy
→ Snapshot
```

---

## TASK-M201 Personal Memory Repository 正式化
## TASK-M202 MemoryCandidate pipeline
## TASK-M203 Episodic
## TASK-M204 Semantic
## TASK-M205 SemanticStore provider / pgvector
## TASK-M206 correct/delete/re-index
## TASK-M207 learning control

---

## TASK-R203 Multi-Pod Equivalence

3 个 Runtime：

```text
A / B / C
```

验证：

```text
canonical snapshot digest
capability exact versions
profile version
memory manifest
policy version
credential refs
```

**必须 100% 一致。**

---

## TASK-P201 Product API / BFF Projection

Workspace：

```text
GET /workspace/home
GET /workspace/agents
GET /workspace/tasks
GET /workspace/approvals
GET /workspace/history
GET /workspace/profile
GET /workspace/memory
```

Studio：

```text
GET /studio/agents/{id}
GET /studio/workflows/{id}
```

Admin：

```text
GET /admin/users/{id}/overview
GET /operations/overview
```

前端禁止在页面层自己拼多个 Registry API 来构造 Product View。

---

# 11. Phase 3 — DBOS Workflow Platform

## TASK-W301 DurableExecutionBackend interface
## TASK-W302 DBOS production backend
## TASK-W303 WorkflowDefinition V2
## TASK-W304 Agent node
## TASK-W305 Tool/MCP/Capability node
## TASK-W306 Condition/Switch
## TASK-W307 Parallel/Join
## TASK-W308 Wait/Timer
## TASK-W309 HumanTask/Approval
## TASK-W310 SubWorkflow
## TASK-W311 Version pin / active ref
## TASK-W312 status projection

## Retry boundary

Fluxion：
- business policy；
- idempotency requirements；
- backend availability circuit breaker。

DBOS：
- durable scheduling；
- recovery；
- durable retry；
- queue。

禁止 double retry。

---

# 12. Phase 4 — Product Experience

现在才正式大规模写 Journey UI。

# 12.1 Workspace

### TASK-X401 Workspace Shell
### TASK-X402 Home
### TASK-X403 Agents
### TASK-X404 Chat Feature
### TASK-X405 Tasks
### TASK-X406 Approvals
### TASK-X407 History
### TASK-X408 Memory & Profile
### TASK-X409 Settings

Chat 不再是 App 本身，而是 Workspace feature。

---

# 12.2 Agent Studio

### TASK-C401 Agents List
### TASK-C402 Agent Overview
### TASK-C403 Instructions
### TASK-C404 Capabilities
### TASK-C405 Workflow
### TASK-C406 Memory Policy
### TASK-C407 Test
### TASK-C408 Trace
### TASK-C409 Eval
### TASK-C410 Version / Publish
### TASK-C411 Advanced Runtime

---

# 12.3 Workflow Studio

默认 Builder UI 不再是 JSON TextArea。

### TASK-WUI401 Node Palette
### TASK-WUI402 Flow/Structure
### TASK-WUI403 Properties Panel
### TASK-WUI404 Mapping
### TASK-WUI405 Test
### TASK-WUI406 Trace
### TASK-WUI407 Version/Publish
### TASK-WUI408 Advanced DSL

---

# 12.4 User 360

### TASK-UUI401 Overview
### TASK-UUI402 Identity/Channels
### TASK-UUI403 Profile
### TASK-UUI404 Memory
### TASK-UUI405 Capabilities
### TASK-UUI406 Policy
### TASK-UUI407 Activity
### TASK-UUI408 Approval/Security

---

# 12.5 Operations

### TASK-OUI401 Overview Dashboard
### TASK-OUI402 Failed Runs
### TASK-OUI403 Workflow Instances
### TASK-OUI404 Traces
### TASK-OUI405 Runtime/MCP Health

---

# 13. Phase 5 — Enterprise Infrastructure / Governance / Observability

## Redis

完成：

- shared cache adapter；
- tenant-scoped key；
- degraded fallback；
- clear-all correctness test；
- rate limit / coordination only when needed。

Redis 不作 SoT。

---

## SemanticStore

默认：

```text
PostgreSQL + pgvector
```

Provider contract 已存在，补 production provider。

---

## ArtifactStore

实现 S3-compatible provider。

用于：

- attachments；
- reports；
- workflow artifact；
- large trace payload。

---

## SecretProvider

完成：

- production provider；
- tenant scope；
- version；
- secret leakage test。

---

## OTel

覆盖：

```text
HTTP
Agent
Model
Tool
MCP
Workflow
DB
Redis
```

P0 trace correlation ≥ 99%。

---

## Governance

完善：

- Policy；
- Approval；
- Audit actor；
- credential；
- resource delete audit；
- profile/memory control。

---

# 14. Phase 6 — Hardening / Scale / UX / Release

## Runtime

- pod kill；
- rolling restart；
- cache clear；
- Redis unavailable；
- multi-Pod routing。

## Workflow

- worker kill；
- DBOS restart；
- wait/resume；
- duplicate execution；
- long approval。

## Tenant

negative tests：

```text
DB
Redis
Semantic
Artifact
Secret
Workflow
Trace
```

跨租户成功数 = 0。

## UX

普通用户 Journey：
- ≥95% 标准任务成功率。

Build Journey：
- ≥95%。

Admin Journey：
- ≥95%。

---

# 15. 优先级重新排序

当前不要继续随机补功能。

## P0 — 马上做

1. ADR-012 raw spec cleanup；
2. PersonalMemory package/domain correction；
3. README/ADR facts correction；
4. AgentDefinition；
5. UserProfile；
6. Definition/State repository boundary；
7. Frontend Product Architecture Gate；
8. ExecutionSnapshot V2；
9. Context Resolver；
10. Product API contracts。

## P0 — 下一批

11. Multi-Pod semantic equivalence；
12. DBOS production backend；
13. WorkflowDefinition V2；
14. Workspace shell；
15. Agent Studio；
16. User 360；
17. Workflow Studio。

## P1

- Async durable task；
- advanced governance；
- artifact production provider；
- workflow advanced nodes；
- UX refinement。

## P2

- Event Bus；
- complex analytics；
- multi-backend Workflow support；
- BPMN-like features。

---

# 16. 建议删除/禁止的实现模式

以下模式应在 V2 开发中明确禁止：

- `Agent = Runtime Pod` 产品语义；
- 普通用户使用 `runtimeProfileId`；
- Chat Access 以 RuntimeProfile 作为长期产品绑定；
- 新 domain state 继续写进 registry package；
- raw `spec_json.get()`；
- Personal Memory 长在 runtime package；
- SessionContextSummary 进入 Personal Memory retrieval；
- Workspace 页面直接拼 Registry API；
- 后端每增加 Resource，前端就增加菜单；
- Workflow Studio 默认要求手写 JSON；
- Redis 作为事实源；
- Fluxion retry + DBOS durable retry 双重叠加；
- 永久 legacy `agent_id + runtime_profile_id` 双模型。

---

# 17. 最终目标代码结构

```text
backend/src/fluxion/
├── agents/
│   ├── domain/
│   ├── application/
│   ├── repository/
│   └── api/
├── users/
│   ├── domain/
│   ├── application/
│   ├── repository/
│   └── api/
├── memory/
│   ├── domain/
│   ├── application/
│   ├── repository/
│   ├── retrieval/
│   └── policy/
├── runtime/
│   ├── agent/
│   ├── context/
│   ├── tools/
│   ├── mcp/
│   ├── skills/
│   └── compaction/
├── workflow/
│   ├── domain/
│   ├── application/
│   ├── backend/
│   ├── nodes/
│   └── projection/
├── capabilities/
├── governance/
├── registry/
├── artifacts/
├── observability/
├── persistence/
└── shared/
```

前端：

```text
frontend/apps/
├── workspace/
│   ├── home/
│   ├── agents/
│   ├── chat/
│   ├── tasks/
│   ├── approvals/
│   ├── history/
│   └── profile-memory/
│
└── console/
    ├── overview/
    ├── build/
    │   ├── agents/
    │   ├── workflows/
    │   ├── capabilities/
    │   └── eval/
    ├── users/
    ├── governance/
    ├── operations/
    └── platform/
```

---

# 18. 最终依赖 DAG

```text
Baseline Cleanup
   ↓
AgentDefinition ─────────────┐
                             ├─→ Context Resolver ─→ Snapshot V2 ─→ Multi-Pod Test
UserProfile ─────────────────┤
                             │
Personal Memory Domain ──────┘

Frontend Product Architecture
   │
   ├─→ Product API Contract
   │       ↓
   │   Workspace
   │
   ├─→ Agent Studio
   └─→ User 360

DBOS ADR
   ↓
DBOS Backend
   ↓
Workflow V2
   ↓
Workflow Studio

Extension Contract
   ├─→ SemanticStore/pgvector
   ├─→ ArtifactStore
   └─→ SecretProvider
```

---

# 19. 最终 DoD

Fluxion V2 只有满足以下条件，才可以称为企业级、可横向扩展的 Agent Platform：

## Runtime

- [ ] 任意 Runtime Pod 可处理任意合法用户请求；
- [ ] 不要求 sticky session；
- [ ] Snapshot digest 跨 Pod 100% 一致；
- [ ] Tool/MCP/Skill exact set 跨 Pod 100% 一致；
- [ ] Profile/Memory/Policy resolution 跨 Pod 一致；
- [ ] committed durable state RPO=0。

## Agent / User

- [ ] AgentDefinition 成为产品入口；
- [ ] RuntimeProfile 只作为 Advanced Runtime；
- [ ] UserProfile 成为真实 Domain；
- [ ] Personal Memory 不属于 runtime domain；
- [ ] 用户可查看/纠正/删除/关闭学习。

## Workflow

- [ ] DBOS production backend；
- [ ] durable wait/resume；
- [ ] HumanTask；
- [ ] retry/idempotency 无重复副作用；
- [ ] pinned deprecated resource 可恢复；
- [ ] active resource 不可误删。

## Frontend

- [ ] 普通用户有 Workspace；
- [ ] Chat 不再等于完整用户产品；
- [ ] 普通用户不看到 RuntimeProfile/Binding/Registry；
- [ ] Builder 有 Agent Studio；
- [ ] Builder 默认不写 Workflow JSON；
- [ ] Admin 有 User 360；
- [ ] Console 默认是 Overview；
- [ ] Resource CRUD 下沉 Platform/Advanced；
- [ ] 三条 Journey 成功率 ≥95%。

## Architecture

- [ ] raw runtime `spec_json.get()` = 0；
- [ ] Definition Plane / State Plane 在代码中真实分离；
- [ ] Product API 与底层 Control API 分层；
- [ ] Redis 不作为 SoT；
- [ ] tenant escape = 0；
- [ ] P0 Trace correlation ≥99%。

---

# 20. 最终判断

最新 `main` 的后端重构总体方向正确，尤其：

- Memory taxonomy；
- Context Compaction；
- Plugin/SPI；
- Pinned Resource；
- Workflow DBOS ADR。

但项目当前已经出现：

> **Runtime/基础设施能力继续领先，而 Agent/User/Product Domain 和前端 Journey 明显落后。**

下一阶段如果继续优先做 Memory、Plugin、Workflow 内核细节，而不先补 AgentDefinition、UserProfile、ContextResolver、Product API 和前端 Product Architecture，Fluxion 会再次向“强 Runtime + 弱产品平台”倾斜。

因此本计划把下一阶段第一优先级明确锁定为：

> **Agent Domain + User Domain + Stateless Context Resolution + Frontend Product Architecture**

而不是继续做后端功能数量增长或前端视觉美化。

---

*文档结束*
