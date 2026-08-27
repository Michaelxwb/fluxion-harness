# Fluxion V2.2 Architecture Remediation Roadmap（架构整改路线图）

> **计划编号**: PLAN-20260826-04  
> **版本**: v2.2  
> **日期**: 2026-08-26  
> **对应 PRD**: PRD-20260826-04  
> **文档定位**: 本文是 Architecture Remediation Roadmap，不是 Sprint 级 Detailed Implementation Plan。  
> **原则**: 与 PRD 使用唯一 Phase 0–6；源码事实优先；先决策高风险架构，再编码；采用 Rolling-wave Planning。

---

# 1. 统一实施路线

```text
Phase 0  Architecture Decisions & Baseline Cleanup
Phase 1  Domain + Storage Foundations
Phase 2  User Context + Runtime + Memory
Phase 3  Workflow Platform
Phase 4  Product Experience
Phase 5  Governance + Observability + Eval
Phase 6  Hardening + Scale + Release
```

任何其他文档不得重新定义 Phase 编号。

---

# 2. Phase 0 — Architecture Decisions & Baseline Cleanup

## TASK-0001：Production Surface Evidence & Migration Classification

禁止主观给 surface 标记 externally-deployed。必须采集客观证据：

- production deployment count
- active DB record count
- active token count
- enabled channel/integration count
- production traffic in last 30 days
- last_used_at
- known external consumer
- public stable contract
- tenant/customer configuration

分类：
- 任一真实生产使用证据存在 → `EXTERNAL_ACTIVE`
- 全部证据明确为 0/false 且证据完整 → `RESET_ALLOWED`
- 证据缺失/无法证明 → `UNKNOWN`
- `UNKNOWN` 按 `EXTERNAL_ACTIVE` 处理

产物：
- `surface-inventory.yaml`
- `surface-evidence-report.md`

每个 breaking change 必须引用 classification 和 evidence。


## TASK-0002：ADR-WF-001 Durable Execution Build-vs-Buy

PoC/比较：
- Temporal
- DBOS
- Restate
- Self-built PostgreSQL

统一 PoC：
1. 5-step workflow
2. Agent/HTTP activity
3. retry
4. durable timer
5. external approval signal
6. worker kill/restart
7. workflow version change
8. 1000 concurrent workflow baseline
9. Trace integration
10. self-host deployment

Gate：
- ADR 结论批准前，不启动自研 scheduler/lease/recovery 大规模开发。

## TASK-0003：ADR-EXT-001 Extension Model

该 ADR 是所有可插拔 Provider Contract 的前置决策，不只负责 Phase 5 Plugin Loader。Phase 1 的 SemanticStore SPI 也必须等待该 ADR 定义接口形状。

依赖：

```text
ADR-EXT-001
  ├─→ Phase 1 SemanticStore Provider Contract
  ├─→ Phase 1 PgVector Provider
  ├─→ Phase 5 ArtifactStore Provider
  ├─→ Phase 5 SecretProvider
  └─→ Phase 5 Plugin discovery/lifecycle/isolation
```


核实：
- PluginType
- PluginLoader
- ModelProvider registration
- TOOL/MEMORY/STORAGE/HOOK 实际引用

决定：
- Provider SPI 与 Plugin Loader 的关系；
- 激活哪些类型；
- 删除哪些死类型；
- ArtifactStore/SemanticStore/SecretProvider 的统一 lifecycle。

## TASK-0004：ADR-SNAPSHOT-001 Pinning / Retention / GC

冻结：
- published immutable
- deprecated semantics
- tombstone
- active reference
- hard delete
- plugin/package uninstall guard
- resume 永不 resolve latest

## TASK-0005：ADR-MEM-001 Memory Taxonomy

```text
L0 -> Working Memory
L1 -> Session Raw History
L2 -> Legacy User Raw History（不等于 Semantic）
summary -> Legacy Context Compaction Record
new Episodic -> Personal Memory
new Semantic -> Personal Memory + Semantic Index
```

## TASK-0006：Spec Model SoT Cleanup

规则：
- deserialize → model_validate
- runtime → typed instance
- frontend schema → model_json_schema
- architecture test 禁止新增 raw spec_json.get

## TASK-0007：FEAT/ADR/TASK/Test/SLO Matrix

### Phase 0 Gate

- [ ] ADR-WF-001 批准
- [ ] ADR-EXT-001 批准
- [ ] ADR-SNAPSHOT-001 批准
- [ ] ADR-MEM-001 批准
- [ ] Surface inventory 完成
- [ ] Spec SoT cleanup 清单完成
- [ ] 全文 Phase 0–6 唯一

---

# 3. Phase 1 — Domain + Storage Foundations

## Agent

- TASK-A101 AgentDefinition Model
- TASK-A102 Agent Repository
- TASK-A103 Agent Service/API
- TASK-A104 移除 RuntimeProfile 产品语义
- TASK-A105 agent_id 产品路由

对 runtime_profile_id：
- internal-dev：直接迁移/reset；
- externally-deployed：一次性 rollover；
- 迁移完成删除旧路径。

## User

复用已有：
`channel_identities → platform_user_id`

新增：
- TASK-U101 PlatformUser aggregate service
- TASK-U102 UserProfile schema
- TASK-U103 Profile Repository
- TASK-U104 Preference/PersonalizationPolicy
- TASK-U105 Capability Grant

## Definition / State

- TASK-D101 domain package skeleton
- TASK-D102 migrate existing spec models
- TASK-D103 split secret scanning to shared/security
- TASK-D104 repository boundaries
- TASK-D105 Registry limited to Definition Plane

注意：不是从 contracts.py “抽出”不存在的 Agent/User，而是新建 Agent/User domain，并迁移已有 spec models。

## PostgreSQL V2 Schema

新增/调整：
- agents
- profiles/preferences
- capability grants
- personal memory metadata
- artifact metadata
- active resource references
- workflow projection/refs（依 ADR）

## Redis

Phase 1 落地，因为 Phase 2 会使用。

要求：
- tenant-scoped key
- cache adapter
- cache bypass fallback
- clear-all correctness test

## Gate 1A — Architecture Skeleton

必须先完成：
- AgentDefinition
- Definition/State code boundary
- Repository boundary
- Spec Model SoT enforcement
- ADR-EXT 定义的 Provider Contract

Gate 1A 通过后允许 User Domain / Storage 实现并行推进。

## Gate 1B — User Domain

必须完成：
- PlatformUser aggregate
- Profile/Preference
- CapabilityGrant
- existing ChannelIdentity integration

## Gate 1C — Storage Foundation

必须完成：
- Redis
- SemanticStore Provider Contract
- pgvector provider
- tenant-scoped isolation

逻辑 Gate 定义完成条件，不禁止工程并行。

## SemanticStore Foundation

- PostgreSQL pgvector
- SemanticStore SPI
- tenant/user/agent filter contract

### Phase 1 Gate

- [ ] AgentDefinition 可发布
- [ ] Profile 可持久化
- [ ] 复用现有 ChannelIdentity mapping
- [ ] Definition/State 落代码边界
- [ ] Runtime raw spec_json access 清零或被 architecture test 阻断
- [ ] Redis 清空不损坏 correctness
- [ ] SemanticStore tenant filter 通过

---

# 4. Phase 2 — User Context + Runtime + Memory

## ExecutionSnapshot V2

- TASK-R201 typed model
- TASK-R202 canonical serialization/digest
- TASK-R203 AgentDefinition resolution
- TASK-R204 Skill/Tool/MCP exact pinning
- TASK-R205 Credential ref/version
- TASK-R206 Profile/retrieval manifest
- TASK-R207 Policy pinning

## Context Resolver

- TASK-R208 Identity→User→Agent pipeline
- TASK-R209 Profile relevance
- TASK-R210 Personal Memory retrieval
- TASK-R211 policy filter
- TASK-R212 context budget
- TASK-R213 resolution trace manifest

## Memory V2

保留：
- L0 working memory
- SQLSessionMemoryStore 的 durable session 路径

整改：
- TASK-M201 停止 raw L1+L2 双写
- TASK-M202 Legacy L2 migration/delete
- TASK-M203 MemoryCandidate
- TASK-M204 EpisodicMemory
- TASK-M205 SemanticMemory
- TASK-M206 pgvector indexing
- TASK-M207 delete/correct/reindex
- TASK-M208 user learning control

## Context Compaction

现有 `_summarize()` 字符串拼接必须删除。

- TASK-M209 Summarizer SPI
- TASK-M210 ModelSummarizer
- TASK-M211 deterministic truncation fallback
- TASK-M212 provenance/range hash
- TASK-M213 compaction quality test
- TASK-M214 删除 `read_l2` 对 session summary 的读取
- TASK-M215 将 summary 语义收紧/重命名为 `SessionContextSummary`
- TASK-M216 architecture test：PersonalMemoryRetriever 禁止读取 SessionContextSummary

## Multi-Pod Verification

- TASK-R214 3-pod routing E2E
- TASK-R215 local cache clear
- TASK-R216 runtime kill -9
- TASK-R217 Redis restart/degrade

### Phase 2 Gate

- [ ] Snapshot digest 跨 Pod 100% 一致
- [ ] Tool/MCP/Skill exact set 100% 一致
- [ ] committed durable state RPO=0
- [ ] Pod failure 新请求恢复 P95≤30s
- [ ] ContextResolver P95≤300ms
- [ ] raw L2 不再作为 Personal Memory
- [ ] pseudo `_summarize` 已删除

---

# 5. Phase 3 — Workflow Platform

## Fluxion-owned Domain

- TASK-W301 WorkflowDefinition V2
- TASK-W302 Workflow Version Lifecycle
- TASK-W303 Node contracts
- TASK-W304 Input/output mapping
- TASK-W305 Policy/Capability metadata
- TASK-W306 Workflow status projection

P0 Nodes：
- Agent
- Tool/MCP/Capability
- Condition/Switch
- Parallel/Join
- Transform
- Approval/HumanTask
- Wait/Timer
- SubWorkflow

## DurableExecutionBackend

接口：
- start
- signal
- cancel
- query
- timer/wait
- retry
- recover
- execution history ref

生产默认实现只选一个，由 ADR 决定：
- Temporal / DBOS / Restate / Self-built

测试可保留 fake backend。

## Resume / Pinning

- TASK-W307 active reference tracking
- TASK-W308 deprecated-but-resumable E2E
- TASK-W309 GC safety
- TASK-W310 plugin/package uninstall guard

标准场景：
1. Workflow v1 启动
2. Skill v3 pinned
3. wait 24h
4. Skill v4 发布，v3 deprecated
5. resume 仍使用 v3
6. active ref>0 时删除 v3 必须拒绝

## HumanTask

- TASK-W311 approval signal
- TASK-W312 assignee resolution
- TASK-W313 timeout
- TASK-W314 user/admin projection

### Phase 3 Gate

- [ ] durable start P95≤1s
- [ ] recovery P95≤60s
- [ ] timer/wait survive restart
- [ ] approval survive restart
- [ ] irreversible duplicate side effect=0
- [ ] pinned deprecated version可恢复
- [ ] active resource hard-delete 被拒绝
- [ ] Agent/Tool/MCP trace linked

---

# 6. Phase 4 — Product Experience

## User Workspace

- TASK-X401 shell
- TASK-X402 Home
- TASK-X403 Agents
- TASK-X404 Tasks
- TASK-X405 Approvals
- TASK-X406 History
- TASK-X407 Memory & Profile
- TASK-X408 Chat integration

普通用户不显示：
- RuntimeProfile
- Registry
- Resource Binding
- Plugin internals

## Console

同一个后台 Persona：

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

- TASK-C401 IA restructure
- TASK-C402 Agent Studio
- TASK-C403 Workflow Studio
- TASK-C404 Capabilities
- TASK-C405 User 360
- TASK-C406 Governance
- TASK-C407 Operations
- TASK-C408 Platform Advanced

### Phase 4 Gate

- [ ] Workspace task success≥95%
- [ ] Build Journey success≥95%
- [ ] Admin Journey success≥95%
- [ ] 普通用户核心页底层术语暴露=0

---

# 7. Phase 5 — Governance + Observability + Eval

## Extension Model

按 ADR-EXT-001：
- TASK-E501 generalized loader
- TASK-E502 dead PluginType removal
- TASK-E503 ArtifactStore provider
- TASK-E504 SemanticStore provider
- TASK-E505 SecretProvider
- TASK-E506 lifecycle/isolation tests

## Artifact Store

- TASK-I501 SPI
- TASK-I502 S3-compatible provider
- TASK-I503 tenant namespace
- TASK-I504 metadata/ref model

## Secret

- TASK-I505 SecretProvider SPI
- TASK-I506 production provider
- TASK-I507 leakage tests

## OTel

- TASK-O501 HTTP
- TASK-O502 Runtime
- TASK-O503 Model
- TASK-O504 Tool/MCP
- TASK-O505 Workflow
- TASK-O506 DB/Redis
- TASK-O507 Collector deployment

## Eval

- TASK-Q501 Agent Eval
- TASK-Q502 Workflow cases
- TASK-Q503 Capability contracts
- TASK-Q504 dataset lifecycle
- TASK-Q505 Release Gate

## Async Task

P1：明确有耗时后台逻辑时实现：
`PostgreSQL durable_task + stateless worker`

V2.2 不引入 Event Bus。

### Phase 5 Gate

- [ ] Trace correlation≥99%
- [ ] Secret plaintext leakage=0
- [ ] tenant escape=0
- [ ] Eval Gate 可阻断 P0 回归

---

# 8. Phase 6 — Hardening + Scale + Release

## Capacity Profile

必须锁定：
- tenant count
- users/tenant
- concurrent sessions
- Runtime replicas
- workflow concurrency
- MCP servers/user
- memories/user

然后重新验证并收紧 SLO。

## Chaos

Runtime：
- pod kill
- rolling restart
- Redis loss
- cache flush

Workflow：
- backend/worker restart
- external activity timeout
- duplicate delivery
- approval long wait

Storage：
- PostgreSQL failover
- Object Store unavailable
- Semantic search degraded

## One-time Migration/Rollover

仅真实外部依赖：
- token rollover
- channel config rollover
- one-time data transform

完成后：
- 删除旧 API
- 删除旧字段
- 删除 compatibility code

## Final DoD

- [ ] Snapshot digest cross-pod=100%
- [ ] Capability equivalence=100%
- [ ] committed durable state RPO=0
- [ ] Runtime failure recovery P95≤30s
- [ ] Workflow recovery P95≤60s
- [ ] irreversible duplicate side effect=0
- [ ] trace completeness≥99%
- [ ] tenant escape=0
- [ ] UX journey success≥95%
- [ ] dead PluginType=0
- [ ] runtime raw spec_json.get violation=0
- [ ] pseudo `_summarize`=0
- [ ] permanent legacy product compatibility path=0
- [ ] active pinned resource hard-delete=0

---

# 9. Critical Dependency DAG

```text
ADR-WF ───────────────→ Phase 3 Workflow
ADR-EXT ───────→ Phase 1 SemanticStore Contract
       ├───────→ Phase 1 PgVector Provider
       └───────→ Phase 5 Extension Providers
ADR-SNAPSHOT ─────────→ Phase 2 Snapshot + Phase 3 Resume
ADR-MEM ──────────────→ Phase 2 Memory

AgentDefinition ──────→ Context Resolver ─→ Agent Studio
UserProfile ──────────→ Context Resolver ─→ User 360
Redis ────────────────→ Phase 2 cache/coordination
SemanticStore ────────→ Semantic Memory

Snapshot ─────────────→ Workflow Agent node
Memory/Context ───────→ Workspace
Workflow ─────────────→ Workflow Studio
```

---

# 10. Review 闭环

| Issue | Closed by |
|---|---|
| A1 | 唯一 Phase 0–6 |
| A2 | Redis/pgvector Phase 1，首次使用前完成 |
| B1 | ADR-WF-001 |
| B2 | ADR-SNAPSHOT + W307–310 |
| B3 | Surface inventory + one-time rollover |
| B4 | ADR-MEM + M201–213 |
| C1 | Runtime 目标改为 semantic equivalence |
| C2 | 删除 UserRuntimeState 整改 |
| C3 | D101–105 精确拆分 |
| C4 | ADR-EXT + Spec SoT cleanup |
| D1 | 明确 SLO/Gates |
| D2 | 复用现有 ChannelIdentity→PlatformUser，补 Profile/Context |

*计划结束*


---

# 11. Rolling-wave Planning

本 Roadmap 不作为直接 Sprint backlog。

流程固定为：

```text
Phase 0 ADR/Gate
   ↓
Phase 1 Detailed Implementation Plan
   ↓
实施 + RED/GREEN + Gate
   ↓
源码重新 Align
   ↓
Phase 2 Detailed Implementation Plan
   ↓
...
```

每份 Detailed Implementation Plan 必须包含：
- precise dependency DAG
- module/file impact
- schema/API impact
- RED test
- GREEN implementation
- single verification command
- measurable acceptance
- rollback/migration requirement
- FEAT/ADR/SLO traceability

禁止在关键 ADR 未完成前提前枚举受其影响的伪精确 Sprint tasks。
