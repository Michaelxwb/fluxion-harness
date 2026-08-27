# PRD: Fluxion V2.2 企业级 Agent 平台

> **文档编号**: PRD-20260826-04  
> **文档版本**: v2.2  
> **创建日期**: 2026-08-26  
> **状态**: 评审修订版  
> **基线**: Fluxion Harness 当前源码 + V2 PRD + GLM Review + 二次源码核验  
> **原则**: 源码事实优先；开发期采用最佳设计，不为未稳定内部接口保留永久兼容层；真实外部依赖采用一次性迁移/rollover，而不是长期双模型。

---

## 1. 文档控制

### 1.1 V2.2 修订摘要

| Review | V2.2 处理 |
|---|---|
| A1 Phase 编号三套体系冲突 | PRD 与整改计划统一为 **Phase 0–6 七阶段** |
| A2 Redis/pgvector/User/Runtime 时序冲突 | 基础设施按“能力首次需要前落地”统一时序 |
| B1 Workflow build-vs-buy 未评估 | Phase 0 强制完成 **ADR-WF-001 Durable Execution Backend**，未通过不得开发 Engine |
| B2 Snapshot 与长暂停 resume 冲突 | 增加 **Pinned Resource Retention / Tombstone / GC** 规则 |
| B3 无兼容债与真实外部面冲突 | 改为 **No Permanent Compatibility Debt**；真实已部署外部面做一次性 rollover |
| B4 Memory 模型无映射、假摘要 | 增加现状→V2 Memory 映射；现有 `_summarize` 明确替换 |
| C1 Runtime 无状态问题被夸大 | 修正为：生产 Session Memory 已 DB 外置，V2 重点是 **语义等价性与新增 User Context** |
| C2 UserRuntimeState 是稻草人 | 删除该整改项；保持现有 Snapshot + RuntimeContext 解耦思想 |
| C3 contracts.py 描述不精确 | 改为新建 agents/users domain；迁移现有 spec；密钥扫描拆 shared/security |
| C4 Plugin/SPI 与 ADR-012 缺席 | 新增 **Extension Model ADR** 与 **Spec Model SoT 强制约束** |
| D1 DoD 无量化阈值 | 增加 V2.2 初始 SLO/验收阈值 |
| D2 Channel Identity 基线不准 | 修正：已有 `channel_identities → platform_user_id` 基础；缺的是 Profile/Personalization/跨渠道一致授权闭环 |

### 1.2 V2.2 新增闭环

V2.2 在 V2.1 基础上进一步锁定五项工程治理机制：

1. **Evidence-based Surface Classification**：breaking migration 不允许由开发者主观判断是否存在外部依赖。
2. **Extension Contract Precedence**：ADR-EXT-001 是 SemanticStore/ArtifactStore/SecretProvider SPI 形状的前置决策。
3. **SessionContextSummary 单一语义**：Context Summary 只服务 session context compaction，不再进入 user-level Personal Memory 读取路径。
4. **Phase 1 Sub-Gates**：Phase 1 内设置 Gate 1A/1B/1C，降低单 Phase 过载风险。
5. **Rolling-wave Planning**：V2.2 Roadmap 是架构整改路线图，不冒充 Sprint Plan；每个 Phase 在其前置 ADR/Gate 完成后再生成 Detailed Implementation Plan。

### 1.3 责任人

| 角色 | 职责 |
|---|---|
| 产品/架构负责人 | 产品边界、架构 ADR、需求验收 |
| Runtime Owner | Agent Runtime、Resolver、Snapshot、Tool/MCP/Skill |
| User/Memory Owner | PlatformUser、Profile、Memory、Context |
| Workflow Owner | Workflow DSL 与 Durable Execution Backend 集成 |
| Console Owner | User Workspace、Build/Admin Journey |
| Governance/Security Owner | Policy、Approval、Secret、Audit、多租户 |
| QA/SRE Owner | SLO、E2E、故障注入、OTel、容量基线 |

---

## 2. 背景与目标

### 2.1 经源码核验的 V1 事实基线

1. Agent Runtime、Agent Loop、ExecutionSnapshot、Tool、MCP、Skill、Registry 已有真实实现。
2. 生产路径的 Session Memory 已通过 SQL Store 外置；L0 为 execution-local transient state，不构成 durable correctness 单点。
3. 当前 Memory 代码是：
   - L0：当前 execution working messages；
   - L1：session-scoped raw messages；
   - L2：user-scoped raw messages；
   - summary：独立记录，但当前实现只是字符串拼接，不是真正语义摘要。
4. PlatformUser 与 Channel Identity 映射基础已经存在。
5. UserProfile/Preference/Personal Memory/Context Personalization 尚未形成完整领域与运行链路。
6. Workflow 当前主要是 Definition/Validator + Engine Protocol/Adapter，不是完整 durable workflow platform。
7. RuntimeProfile 在产品面承担过多 Agent persona/入口语义，`runtime_profile_id` 已进入 chat/channel/API surface。
8. PluginType 已声明多种类型，但实际加载能力与声明并不完全一致，需要在 V2.2 统一扩展模型。
9. Spec Model 必须遵循单一真相源：typed model validation / runtime typed instance / generated JSON schema，不允许业务运行路径继续依赖散乱 `spec_json.get()`。

### 2.2 核心目标

Fluxion V2.2 建设为：

> **Stateless Compute + Externalized Durable State + Snapshot-driven Resolution + Durable Workflow Capability + User-context Aware + Observable by Default 的企业级 Agent Platform。**

V2.2 不把“Runtime 无状态”理解为重新外置已经外置的 Session Memory，而重点解决：

- 任意 Runtime Pod 的执行语义等价；
- AgentDefinition 与 RuntimeProfile 分离；
- UserProfile/Personal Memory/Capability Grant 进入统一 Context Resolution；
- 同一 Execution 固定 Tool/MCP/Skill/Policy/User Context；
- durable workflow 不重复发明未经验证的分布式执行机制；
- 产品从 Resource-centric 转向 Journey-centric。

### 2.3 成功指标 / 初始 SLO

| ID | 指标 | V2.2 目标 |
|---|---|---|
| SLO-RUN-01 | Snapshot 语义等价 | 同一输入条件下不同 Pod 的规范化 Snapshot digest **100% 一致** |
| SLO-RUN-02 | Tool/MCP/Skill 等价 | 同一 Snapshot 下 capability IDs + exact versions + policy scope **100% 一致** |
| SLO-RUN-03 | Durable state RPO | 已提交 PostgreSQL durable state **RPO = 0** |
| SLO-RUN-04 | Runtime Pod 故障恢复 | 单 Pod 故障后合法新请求恢复服务 **P95 ≤ 30s** |
| SLO-CTX-01 | Context Resolve | 不含外部模型调用，典型规模下 **P95 ≤ 300ms** |
| SLO-API-01 | Control/Workspace 读 API | 典型规模下 **P95 ≤ 500ms** |
| SLO-WF-01 | Workflow durable start | start 确认持久化 **P95 ≤ 1s** |
| SLO-WF-02 | Workflow backend/worker 故障恢复 | durable workflow 可继续推进 **P95 ≤ 60s** |
| SLO-WF-03 | Committed Step duplication | 不可逆副作用重复次数 **0** |
| SLO-OBS-01 | Trace 关联完整率 | P0 Agent/Workflow/Tool/MCP 路径 **≥ 99%** |
| SLO-SEC-01 | Tenant isolation | 自动化 negative test 跨租户越权成功数 **0** |
| SLO-UX-01 | 普通用户核心旅程 | 标准任务成功率 **≥ 95%** |
| SLO-UX-02 | Build/Admin 核心旅程 | 标准任务成功率 **≥ 95%** |

> 上述为 V2.2 初始工程下限，Phase 6 容量压测后允许收紧；若要放宽必须重新评审。

---

## 3. 用户与场景

### 3.1 Persona

#### Persona A：普通用户

企业内部用户、Web 用户、渠道用户和外部协作用户统一映射为 PlatformUser。

当前已有 Channel Identity → PlatformUser 基础；V2.2 新增的是：
- Profile；
- Preference；
- Personal Memory；
- Capability/Policy 一致解析；
- 跨渠道统一 Context。

#### Persona B：平台管理/构建用户

同一个后台 Persona，拆成两条 Journey。

**Build Journey**
- Agents
- Capabilities
- Workflows
- Test / Trace / Eval
- Publish

**Admin Journey**
- Users
- Governance
- Operations
- Platform

### 3.2 用户故事

| ID | 用户故事 | 优先级 |
|---|---|---|
| US-01 | 普通用户可直接选择 Agent/业务能力开始任务，而不是选择 RuntimeProfile | P0 |
| US-02 | 同一 PlatformUser 跨渠道获得一致 Agent、能力、权限、Profile/Memory Context | P0 |
| US-03 | 用户可查看、纠正、删除 Profile/Personal Memory 并关闭自动学习 | P0 |
| US-04 | 用户可查看长期任务及待确认事项 | P0 |
| US-05 | Builder 以 AgentDefinition 为中心完成 Agent 构建、测试、发布 | P0 |
| US-06 | Builder 可编排 durable Workflow，不自行实现恢复/定时器/等待语义 | P0 |
| US-07 | Admin 可通过 User 360 管理 Identity/Profile/Capability/Policy/Activity | P0 |
| US-08 | 平台可任意水平扩展 Runtime Pod，用户无 sticky-session 感知 | P0 |
| US-09 | 同一 Execution 内 Tool/MCP/Skill/Policy/User Context 不发生版本漂移 | P0 |
| US-10 | 长时间 Workflow resume 时仍能取得启动时 pinned 的不可变定义与执行依赖 | P0 |
| US-11 | 平台扩展机制只有一套明确模型，不并存死 PluginType 与新 SPI | P0 |
| US-12 | 所有 Resource Spec 以 typed model 为 SoT，禁止运行路径散乱读取 raw spec JSON | P0 |

---

## 4. 功能需求

### 4.1 功能总表

| FEAT | 名称 | 优先级 |
|---|---|---|
| FEAT-01 | AgentDefinition 产品模型 | P0 |
| FEAT-02 | Runtime Semantic Equivalence | P0 |
| FEAT-03 | ExecutionSnapshot V2 | P0 |
| FEAT-04 | Pinned Resource Retention | P0 |
| FEAT-05 | PlatformUser/Profile Domain | P0 |
| FEAT-06 | Memory V2 | P0 |
| FEAT-07 | Real Context Compaction | P0 |
| FEAT-08 | Context Resolver | P0 |
| FEAT-09 | Tool/MCP/Skill exact-version capability resolution | P0 |
| FEAT-10 | Workflow DSL | P0 |
| FEAT-11 | Durable Execution Backend | P0 |
| FEAT-12 | Workflow HumanTask/Wait/Resume | P0 |
| FEAT-13 | Workflow Version Lifecycle/GC | P0 |
| FEAT-14 | Unified Extension/Plugin Model | P0 |
| FEAT-15 | Spec Model Single Source of Truth | P0 |
| FEAT-16 | Redis Cache/Coordination | P0 |
| FEAT-17 | SemanticStore / pgvector | P0 |
| FEAT-18 | ArtifactStore / Object Store | P0 |
| FEAT-19 | SecretProvider | P0 |
| FEAT-20 | OpenTelemetry | P0 |
| FEAT-21 | User Workspace | P0 |
| FEAT-22 | Agent/Workflow Studio | P0 |
| FEAT-23 | User 360 / Governance / Operations | P0 |
| FEAT-24 | Eval / Release Gate | P0 |
| FEAT-25 | Durable Async Task | P1 |
| FEAT-26 | Event Bus Extension Point | P2 |

### 4.2 AgentDefinition

AgentDefinition 是产品/逻辑实体；RuntimeProfile 是执行配置。

```text
AgentDefinition
  ├─ identity / presentation
  ├─ owner / visibility / lifecycle
  ├─ runtime_profile_ref
  ├─ default capability/workflow presentation
  └─ memory/personalization policy refs
```

普通用户产品面不再以 RuntimeProfile 为 Agent 标识。

### 4.3 ExecutionSnapshot V2

Snapshot 冻结：
- AgentDefinition exact version
- RuntimeProfile exact version
- Model policy / instruction digest
- Skill exact versions
- Tool/Capability exact versions
- MCP exact versions
- Binding versions
- Credential refs + versions（不含 secret）
- UserProfile version
- selected Personal Memory refs / retrieval manifest
- User/Tenant policy versions
- Workflow ref/version（如适用）

### 4.4 Pinned Resource Retention

1. Published version immutable。
2. `deprecated` 只阻止新解析，不影响已有 Execution/Workflow。
3. 被 active workflow/execution 引用的版本不得 hard delete。
4. Registry/Artifact 层保存可恢复 immutable payload / snapshot manifest。
5. Hard delete 只允许：
   - active reference = 0；
   - 超过 retention period；
   - GC safety check 通过。
6. Workflow resume 始终使用 pinned version，不 resolve latest。
7. Plugin/运行包卸载必须先通过 active-reference 检查。

### 4.5 Memory V2 现状映射

| 当前代码 | V2.2 定义 | 动作 |
|---|---|---|
| L0 `_l0` | Working Memory | 保留语义 |
| L1 `session_memory(level=L1)` | Session Raw Message Store | 保留 DB durable 路径 |
| L2 `session_memory(level=L2)` | Legacy user-scoped raw history | 停止作为 Personal/Semantic Memory 解释；停止无脑双写 |
| summary | Legacy compaction record | 数据模型可复用，算法替换 |
| summary 同时被 L1/L2 读取 | Session summary 泄漏到 user-level retrieval | **删除 L2/user-level summary 读取；重命名为 SessionContextSummary** |
| `_summarize()` 字符串拼接 | 不是真摘要 | 替换为 Summarizer SPI/Model-based summarization |
| 新增 Episodic | Personal Memory | 新建模型/表 |
| 新增 Semantic | Personal Memory + SemanticStore | 新建模型/索引 |

Memory pipeline：

```text
Conversation Raw History
  ↓
Candidate Extraction
  ↓
Episodic / Semantic / Preference Candidate
  ↓
Policy + User Control
  ↓
Commit
  ↓
Semantic Index
```

### 4.6 Real Context Compaction

Compaction 与 Personal Memory 分开。

要求：
- Summarizer SPI；
- Model-based summary；
- deterministic truncation fallback；
- summary 记录 source message range/hash；
- summary 不自动写入 UserProfile；
- 有 token budget 和质量测试。

### 4.7 SessionContextSummary 边界

`SessionContextSummary` 只属于 session-scoped context compaction：

- `ContextAssembler` 可以读取；
- `PersonalMemoryRetriever` 禁止读取；
- 不直接成为 Episodic/Semantic/Profile；
- 若其中信息值得长期记忆，必须重新经过 MemoryCandidate → Policy/Consent → Commit；
- summary 必须记录 source message range/hash。

### 4.8 Workflow Durable Execution Backend

Fluxion 自己拥有：
- WorkflowDefinition / DSL
- Builder UX
- Agent/Capability node model
- Policy/Governance integration
- Execution projection / status API

Fluxion 不预设必须自研：
- distributed scheduler
- durable timers
- exactly-once/replay engine
- crash recovery kernel

Phase 0 必须完成 ADR-WF-001，比较：
- Temporal
- DBOS
- Restate
- Self-built PostgreSQL engine

评估维度：
- Python SDK/生态
- self-host
- dynamic DSL 适配
- durable timer/wait/signal
- retry/idempotency
- replay/recovery model
- horizontal scaling
- operational complexity
- observability
- data ownership
- license
- upgrade/versioning
- workflow retention
- enterprise maturity
- local development complexity

**默认倾向：优先成熟 durable execution backend；Fluxion 自研 DSL 与 adapter，而不是默认自研 durable kernel。最终以 ADR 为 Gate。**

### 4.9 Unified Extension Model

不允许已有死 PluginType 与新 SPI 长期并存。

Phase 0 完成 ADR-EXT-001。

推荐模型：
- Provider SPI 定义能力；
- Plugin System 管 discovery/lifecycle/isolation；
- PluginType 与 Provider Contract 一一对应；
- 未实现且无计划的死 PluginType 删除。

建议显式类型：
- MODEL_PROVIDER
- TOOL_PROVIDER
- MEMORY_PROVIDER（仅确有 provider 时保留）
- ARTIFACT_STORE
- SEMANTIC_STORE
- SECRET_PROVIDER
- HOOK

### 4.10 Spec Model Single Source of Truth

1. `spec_json` 进入应用后必须 `Model.model_validate()`。
2. Runtime/Resolver 只读取 typed model 实例。
3. 前端 Schema 来源为 `model_json_schema()`。
4. 禁止业务运行路径新增 `spec_json.get(...)`。
5. 现有违规点清零。
6. 加 architecture/static test。

### 4.11 Evidence-based Surface Classification

所有 breaking surface 必须先形成可审计的 `SurfaceEvidence`，至少包含：

```text
surface_id / surface_type / code_reference / db_reference
production_deployment_count
active_record_count
active_token_count
enabled_integration_count
traffic_30d / last_used_at
known_external_consumer
public_stable_contract
evidence_source / collected_at
classification
```

分类规则：

- 任一生产 active record、有效 token、启用渠道集成、近 30 天生产流量、已知外部 consumer、公开稳定 contract 存在 → `EXTERNAL_ACTIVE`。
- 只有所有生产使用证据均为 0/false 且证据完整时 → `RESET_ALLOWED`。
- 无法取得充分证据 → `UNKNOWN`。
- `UNKNOWN` 必须按 `EXTERNAL_ACTIVE` 处理，禁止 destructive reset。

Surface Inventory 的结论必须来自生产部署审计、数据库/日志/配置证据，而不是开发者主观标注。

### 4.12 外部面迁移原则

将“Best Design Over Compatibility”修改为：

> **No Permanent Compatibility Debt**

- internal/dev surface：允许直接破坏性修改并 reset/migrate dev data。
- 真实 externally-deployed contract：允许一次性 rollover/migration window，但禁止长期双模型。

`runtime_profile_id → agent_id`：
- 无真实生产依赖：直接 schema/API reset。
- 有真实已签发 token/渠道集成：一次性 token/config rollover，之后删除旧字段与旧路径。
- 不保留永久 legacy 双字段。

---

## 5. 非功能需求

| ID | 类型 | 要求 |
|---|---|---|
| NFR-ARCH-01 | Stateless | V2 重点保证新增 User Context/Capability Resolution 无 Pod-local SoT |
| NFR-ARCH-02 | Snapshot | 同一 execution 内 exact-version pinning |
| NFR-ARCH-03 | Retention | active execution 引用资源不可 hard delete |
| NFR-ARCH-04 | SoT | Spec typed model 是唯一运行时真相源 |
| NFR-ARCH-05 | Extension | Plugin/SPI 统一扩展模型 |
| NFR-REL-01 | RPO | committed durable state RPO=0 |
| NFR-REL-02 | Recovery | Workflow backend 恢复 P95≤60s |
| NFR-REL-03 | Idempotency | 不可逆写副作用重复=0 |
| NFR-SCALE-01 | Runtime | Runtime Pod 不依赖 sticky session |
| NFR-SCALE-02 | Workflow | Durable backend/worker 可水平扩展 |
| NFR-PERF-01 | Context | Context resolve P95≤300ms |
| NFR-PERF-02 | API | Control read API P95≤500ms |
| NFR-SEC-01 | Tenant | DB/Redis/Semantic/Artifact/Secret 跨租户越权=0 |
| NFR-PRIV-01 | Memory/Profile | 用户可查看/纠正/删除/停止自动学习 |
| NFR-OBS-01 | OTel | P0 Trace 关联完整率≥99% |
| NFR-UX-01 | Workspace | 普通用户核心任务成功率≥95% |
| NFR-UX-02 | Console | Build/Admin 标准任务成功率≥95% |

---

## 6. 范围与边界

### In Scope
- AgentDefinition
- UserProfile
- Personal Memory + Real Compaction
- ExecutionSnapshot V2
- Runtime semantic equivalence
- Pinned version retention
- Workflow DSL + Durable Execution Backend integration
- Plugin/SPI unified model
- Spec Model SoT cleanup
- Redis
- pgvector/SemanticStore
- Object Store/ArtifactStore
- SecretProvider
- OTel
- User Workspace
- Build/Admin Console
- Eval/Release Gate

### Out of Scope
- 未做 ADR 就直接自研完整 durable kernel
- Event Bus 强制部署
- BPMN 全兼容
- 完整低代码平台
- 永久 legacy compatibility layer
- 将业务领域事务迁进 Fluxion Workflow
- 把 Redis 当 durable SoT

---

## 7. 依赖与风险

| 风险 | 缓解 |
|---|---|
| Durable Workflow 自研复杂度 | ADR-WF-001 build-vs-buy Gate |
| Workflow pinned version 被删除 | active reference protection + tombstone + GC |
| Plugin/SPI 双体系 | ADR-EXT-001，一套 Loader/Provider 模型 |
| Profile/Memory 污染 Context | Candidate + policy + retrieval manifest |
| Compaction 质量不足 | Real Summarizer + fallback + quality tests |
| runtime_profile_id 外部依赖 | surface inventory + one-time rollover |
| 渠道统一个性化工作量大 | 复用已有 ChannelIdentity→PlatformUser，增量补 Profile/Context |
| SLO 容量假设不足 | Phase 6 容量压测后收紧 |

---

## 8. Existing Spec Constraints

正式 align 时加载仓库实际 spec-context；至少必须包含：
- Architecture Baseline
- Runtime Design
- Console Design
- Spec Model Single Source of Truth（ADR-012 或实际对应 ADR）
- Plugin/Extension ADR（新增）
- Workflow Durable Backend ADR（新增）

不得自行将 required rule 设为 N/A。

---

## 附录：唯一 Phase 编号

```text
Phase 0  Architecture Decisions & Baseline Cleanup
Phase 1  Domain + Storage Foundations
  Gate 1A Architecture Skeleton
  Gate 1B User Domain
  Gate 1C Storage Foundation
Phase 2  User Context + Runtime + Memory
Phase 3  Workflow Platform
Phase 4  Product Experience
Phase 5  Governance + Observability + Eval
Phase 6  Hardening + Scale + Release
```

*文档结束*


---

## 9. V2.2 工程规划层级

V2.2 明确采用四层工程文档体系：

```text
L1 Product Baseline
  └─ PRD V2.2

L2 Architecture Decision Baseline
  ├─ ADR-WF-001
  ├─ ADR-EXT-001
  ├─ ADR-SNAPSHOT-001
  └─ ADR-MEM-001

L3 Architecture Remediation Roadmap
  └─ Phase / Sub-Gate / Dependency / SLO

L4 Detailed Implementation Plan
  └─ TASK / RED / GREEN / files / command / DoD
```

V2.2 不提前虚构尚受 ADR 影响的 Sprint 级任务。采用 Rolling-wave Planning：Phase 0 决策完成后生成 Phase 1 Detailed Plan；每个 Phase 完成并重新对齐源码后，再生成下一 Phase 的 Detailed Plan。
