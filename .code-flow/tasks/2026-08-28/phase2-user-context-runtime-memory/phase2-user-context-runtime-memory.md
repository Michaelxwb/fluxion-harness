# Tasks: Phase 2 User Context + Runtime + Memory（未落地部分）

- **Source**: `.code-flow/tasks/2026-08-28/phase2-user-context-runtime-memory/phase2-user-context-runtime-memory.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-28（v0.2 remediation §13 修订；v0.3 Gate G2/G4 场景；v0.4 TASK-011 用户自助 tool）

## Proposal

Phase 2 补齐 v2.2 规划中尚未落地的 User Context / Runtime / Memory 能力：新建 ContextResolver 十段解析管线（services 应用服务，`agent_id` 主坐标），为 ExecutionSnapshot 扩展 V2 字段并引入 canonical digest 作为跨实例一致性判据；Personal Memory 从 `runtime/` 迁入**独立 `memory/` 域**（P-04 + remediation §13.4）并补齐 pgvector 向量检索、learning control、correct/delete/reindex 与 L2 legacy 迁移；落地 Redis tenant cache adapter（**P1 基础设施，正确性不依赖**，remediation §13.5）；最终以双实例 digest 一致性验证闭合架构规则 28（相同 `tenant_id + user_id + agent_id` 跨实例解析出一致 Snapshot；真实 k8s 多副本/rolling 移交 Phase 6）。

**v0.2 修订**（按 `fluxion-phase1-closure-detailed-remediation.md` §13，历史文档 git 历史可查，2026-08-28）：等价性主键 `runtime_profile_id` → `agent_id`；digest 覆盖补全 + normalize-defaults 语义（不忽略 None）；Personal Memory 目标域 `users/` → `memory/`；Redis 降 P1（TASK-006）；TASK-010 改 N 实例验证。

依据 design §2.4 前置：M201/M209-M216 已落地不重复设计；本文件只覆盖未落地部分。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | E2E | 双 Application 实例 → 真实 PG + Redis → Registry → Secret → Memory | TASK-008 | planned |
| S-02 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | ContextResolver → Store（真实数据库） | TASK-007 | verified |
| S-03 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | 架构测试扫描 imports（真实源码树） | TASK-002 | verified |
| S-04 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | E2E | API → UserPreference → MemoryLearner → Store | TASK-004 | verified |
| S-05 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | E2E | 真实 Redis（kill/重启）→ cache adapter → Store | TASK-006 | planned |
| S-06 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | E2E | N 个独立应用实例 → PG → Redis（kill 实例进程） | TASK-010 | planned |
| S-07 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | E2E | PG pgvector → SemanticStoreProvider → PersonalMemoryRetriever | TASK-003 | verified |
| E-01 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | Middleware → ContextResolver | TASK-007 | verified |
| E-02 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | ContextResolver → Secret store | TASK-007 | verified |
| E-03 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | MemoryLearner → Consent/Policy gate → Store | TASK-004 | verified |
| E-04 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | ContextResolver → Profile | TASK-007 | verified |
| E-05 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | integration | Redis（连接超时）→ cache adapter → Store | TASK-006 | planned |
| B-01 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | unit | context budget 计算（真实 manifest 构建） | TASK-007 | verified |
| B-02 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | unit | canonical 序列化纯函数 | TASK-001 | verified |
| B-03 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景 | unit | canonical 序列化纯函数 | TASK-001 | verified |
| S-08 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景（v0.3） | integration | 真实 Store + 执行中发布（Gate G4/ARCH-07） | TASK-007 | verified |
| S-09 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景（v0.3） | integration | ContextResolver Credential 段 + MCP prepare（Gate G2） | TASK-007 | verified |
| S-10 | phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景（v0.4） | E2E | AgentLoop + builtin user tools + UserDomainService + 真实 Store | TASK-011 | verified |
| RULE-P2-01 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | E2E | 同 S-01 | TASK-008 | planned |
| RULE-P2-02 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | integration | 同 S-02 | TASK-007 | verified |
| RULE-P2-03 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | integration | 同 S-03 | TASK-002 | verified |
| RULE-P2-04 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | integration | 同 E-02 | TASK-007 | verified |
| RULE-P2-05 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | E2E | 同 S-04 | TASK-004 | verified |
| RULE-P2-06 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | E2E | 同 S-05 | TASK-006 | planned |
| RULE-P2-07 | phase2-user-context-runtime-memory.design.md#2.5.1 业务规则与约束 | E2E | 同 S-06 | TASK-010 | planned |

> design §6 追溯矩阵中 FEAT-P2-07/FEAT-P2-08 标注的 manual 部分：dry-run 已自动化（TASK-009 integration），UI 依赖属 Phase 4 Out of Scope（design §2.4），不设 manual 项。TASK-005 的 NFR-PRIV-01 后端契约场景已 verified（memory user service 套件 8 passed）。NFR-PERF-01/02/03 分别由 S-02（TASK-007）、B-02 基准（TASK-001）、S-01（TASK-008）承载；NFR-SEC-01 由 E-02（TASK-007）承载；NFR-PRIV-01 由 TASK-004/TASK-005 后端契约承载。

---

## TASK-001: ExecutionSnapshot V2 字段 + canonical digest

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.2 字段约束, phase2-user-context-runtime-memory.design.md#3.1 方案选型, phase2-user-context-runtime-memory.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: B-02, B-03, RULE-fluxion-runtime-001

### Description

扩展现有 `ExecutionSnapshot`（`contracts.py:534`）增加 V2 字段：`user_profile_version: str | None`、`memory_manifest: MemoryManifest | None`、`snapshot_digest: str | None`、`credential_versions: dict[str, str]`（只存 ref→version）、`agent_definition_version: str | None`（等价性主键，remediation §13.1）、`policy_versions: dict[str, str]`（tenant/user/personalization），保持 `extra="forbid"`。新增 `MemoryManifest` 子模型（`entry_refs: list[MemoryEntryRef]`、`content_hash: str`、`truncated: bool`）。实现纯函数 `canonical_digest(snapshot) -> str`：**typed model → normalize defaults → canonical dump（递归排序键）→ 确定性 JSON → sha256**——不简单忽略 None（可选字段以规范形式参与序列化，remediation §13.3），仅排除 `created_at`/`execution_id`/`trace_id` 运行时字段。digest 覆盖版本图谱全集（remediation §13.2）：agent/profile/model/prompt-instruction digest/skill·tool·mcp exact versions/binding/credential/memory manifest/policy versions。

### Checklist

- [x] 定义 `MemoryManifest`/`MemoryEntryRef` 模型，扩展 `ExecutionSnapshot` V2 字段（含 `agent_definition_version`/`policy_versions`，`extra="forbid"` 不变）
- [x] 实现 `canonical_digest` 纯函数：typed model → normalize defaults → canonical dump（递归排序键），None 以规范形式参与（不丢弃），排除运行时字段，输出 64 字符 hex
- [x] [B-02][unit] 验收测试 RED：`snapshot_digest` 模块与 V2 字段缺失（ImportError + model_fields 断言失败）
- [x] [B-03][unit] GREEN：版本号变更（skill 3.1.0→3.2.0 / agent v3→v4）→ digest 必变
- [x] NFR-PERF-02：纯函数无 IO，远低于 20ms 预算
- [x] **Spec verifier**：`RULE-fluxion-runtime-001` — `pytest backend/tests/resources/test_snapshot_v2_digest.py` → 8 passed：`extra="forbid"` 保持、digest 覆盖 agent/policy/credential/memory manifest 版本、排除运行时字段
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-02 | unit | canonical 序列化纯函数（不 mock） | 键乱序/None/时区差异 → digest 相等 | backend/tests/resources/test_snapshot_v2_digest.py | `.venv/bin/python -m pytest backend/tests/resources/test_snapshot_v2_digest.py -q` | verified |
| B-03 | unit | canonical 序列化纯函数（不 mock） | 版本号变更 → digest 必变 | 同上::test_b03_version_change_changes_digest | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-02 | ImportError（snapshot_digest 模块缺失）+ model_fields 断言失败（V2 字段缺失） | 8 passed：确定性/时区归一/None 参与/extra=forbid/运行时字段排除 | test_snapshot_v2_digest.py:47-72 | 真实 ExecutionSnapshot typed model + 纯函数（无 IO 无 mock） | verified |
| B-03 | （同上模块缺失） | skill/agent 版本变更 → digest 必变 | test_snapshot_v2_digest.py:75-83 | 同上 | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-002: Personal Memory 迁出 runtime 域（P-04）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#3.2 架构设计
- **Spec-Refs**: backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: S-03, RULE-P2-03

### Description

迁移 `runtime/personal_memory.py` → 独立 `memory/` 域（domain/application/retrieval 分层骨架；`users/` 只留 Profile/Preference/Identity），满足 remediation-plan P-04「Personal Memory 从 Runtime Domain 移出」+ remediation §13.4。全仓 import 改写（含 summarizer 等依赖），全量测试回归。`runtime/` 只留 session-scoped。架构测试：`memory/` 域禁止读取 `SessionContextSummary`；`runtime/` 侧不得引用 personal_memory（新旧路径双向断言，RULE-P2-03 / ADR-MEM-001）。

### Checklist

- [x] 迁移 `runtime/personal_memory.py` → `memory/` 域（git mv 纯代码移动），全仓 import 改写
- [x] [S-03][integration] 架构测试：`memory/` 域不含 `session_context_summary` 读取；`runtime/` 无 personal_memory import（新旧路径双向）
- [x] [S-03] 断言包边界成立（summarizer/memory/memory_sql 零引用），测试目录与源码同构
- [x] 全量回归：现有 backend 测试套件全绿（RISK-02 迁包破坏 imports 未发生）
- [x] **Spec verifier**：`RULE-backend-directory-001` — 架构测试 11 passed：personal memory 位于独立 `memory/` 域（remediation §13.4）、`runtime/` 无 personal_memory import、`users/` 不承载 memory 检索

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 架构测试扫描真实源码树 imports（不 mock） | `memory/` 域无 `session_context_summary` 读取；`runtime/` 无 personal_memory import；`users/` 不承载 memory 检索 | backend/tests/integration/test_personal_memory_architecture.py（11 passed） | `.venv/bin/python -m pytest backend/tests/integration/test_personal_memory_architecture.py backend/tests/unit/test_personal_memory_model.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | 迁移后首轮：架构测试 3 failed（`_MEMORY_ROOT` 仍指 runtime、e03 残留旧路径断言）+ 既有 import 路径失效 | 11 passed：runtime/summarizer/memory/memory_sql 零 personal_memory 引用（新旧路径双向断言）、`_insert` 仅在 commit 内 | test_personal_memory_architecture.py:327-331（e03 隔离断言）、:49-50（路径守卫） | git mv 真实迁移 + 全仓 import 改写；full 回归 369 passed 无 import 断裂 | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-003: pgvector SemanticStore provider + embedding 双库分派

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#3.3 数据设计, phase2-user-context-runtime-memory.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-07

### Description

实现 `SemanticStoreProvider` 的生产 provider：`plugins/providers/pgvector_semantic.py`（personal_memory 表上的 store/recall/search）。embedding 双层：native pgvector（VECTOR 列 + `<=>`，探测到扩展时自动接管）/ 降级 JSON + Python cosine（本地 PG 扩展不可用，真实 PG 表仍可全链路验证）。`recall` 语义：cosine 排序 + memory_type 过滤 + tenant/user scope 隔离；失败抛 `SemanticStoreError`（调用方降级空 manifest）。双库契约：SQLite/PG 走同一 provider 契约测试。

### Checklist

- [x] Embedding 双层：native pgvector 探测 + 降级 JSON+Python cosine（真实 PG 可测）
- [x] 实现 `PgVectorSemanticStore.recall`：cosine 排序 + `memory_type` 过滤 + tenant/user scope 隔离
- [x] [S-07][E2E] 验收测试 RED：providers 包缺失（ModuleNotFoundError）
- [x] 双库契约：SQLite 恒跑 + PG 门控（FLUXION_REQUIRE_POSTGRES_CONTRACT=1，local-pg-test-env）4 passed
- [x] [S-07] 断言跨 tenant/user scope 无泄漏（tenant-b / user-b 均 0 命中）
- [x] **Spec verifier**：`RULE-fluxion-resource-001` + `RULE-backend-database-001` — 契约测试 4 passed（SQLite 2 + PG 2）：同一 provider 契约、cosine 排序、scope 隔离

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实 PG/SQLite personal_memory 表 + 真实 cosine | cosine 排序正确；memory_type 过滤；scope 隔离 | backend/tests/contract/test_semantic_store_provider.py（2 用例 × 双库） | `FLUXION_REQUIRE_POSTGRES_CONTRACT=1 ... pytest backend/tests/contract/test_semantic_store_provider.py -q`（4 passed） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | ModuleNotFoundError：`fluxion.plugins.providers` 缺失 | SQLite 2 passed + PG 门控 4 passed（含双库） | test_semantic_store_provider.py::S-07 两用例 | 真实 PG（fluxion_test）+ SQLite 内存库；personal_memory 表真实读写 | verified |

- **诚实约束记录**：本地 PG 无 pgvector 扩展（CREATE EXTENSION 失败）——native VECTOR 列路径无法真实验证，已按 S-P13-07 约束不伪造：降级 JSON+Python cosine 在真实 PG 全链路验证，native `<=>` 路径代码就位、由 initialize() 探测自动接管（扩展可用时零改动切换）。
- **回归**：backend 全量 363+ passed。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-004: MemoryCandidate pipeline 正式化 + learning control

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#3.4 接口设计
- **Acceptance-Refs**: S-04, E-03, RULE-P2-05

### Description

将 memory 域骨架固化为正式 pipeline：extraction → Consent gate → Policy gate → Commit。`MemoryLearnerService.commit_candidate` 为正式入口——learning_enabled 从 UserPreference 读取（用户停学 → commit 拒绝，RULE-P2-05）；Policy/Consent 拒绝携带可观测 reason（不抛错）；抽取失败（None 候选）跳过不落库；批次接口 `commit_batch`。模型 Provider 抽取失败跳过该候选，不阻塞 Execution。

### Checklist

- [x] 固化 pipeline（extraction → Consent → Policy → Commit），`commit_candidate`/`commit_batch` 正式入口
- [x] learning_enabled 接 UserPreference（M208）：停学 → 拒绝且 personal_memory 无新行
- [x] [E-03][integration] RED：learner_service 模块缺失（ImportError）；GREEN：Policy/Consent 拒绝 → reason 可观测（policy_rejected/consent_rejected），无新行
- [x] [S-04][E2E] RED/GREEN：停学 → commit 拒绝 + 表级核验无新行；开启 → 落库
- [x] 抽取失败候选跳过（commit_batch 语义）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | API → UserPreference → MemoryLearner → Store | 停学 commit 拒绝且无新行；开启正常提交 | backend/tests/memory/test_memory_candidate_pipeline.py（4 用例） | `.venv/bin/python -m pytest backend/tests/memory/ -q` | verified |
| E-03 | integration | MemoryLearner → Consent/Policy gate → Store | 拒绝 + reason 可观测；无新行 | 同上::test_e03_policy_and_consent_rejections_record_reason | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | ImportError（learner_service 缺失）→ 实现 → 首轮 adapter 返回形状错（int 无 .id）修正后 4 passed | 停学拒绝（reason=learning_disabled）+ 表级 count=0；开启正常提交 | test_memory_candidate_pipeline.py:60-79（停学+表级核验） | 真实 SQLite personal_memory + user_preferences 表；MemoryLearner gate 顺序不变 | verified |
| E-03 | 同上模块缺失 | policy_rejected / consent_rejected reason 断言 | test_memory_candidate_pipeline.py:82-96 | 真实 gate 链（不抛错、reason 可观测） | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-005: Personal Memory 查看/纠正/删除 + reindex

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#3.3 数据设计, phase2-user-context-runtime-memory.design.md#3.4 接口设计
- **Acceptance-Refs**: NFR-PRIV-01（后端契约）

### Description

用户级 Personal Memory 操作服务（`memory/application/memory_user_service.py`）：查看（list_entries）/纠正（correct：内容更新 + embedding 重算回写）/删除（delete）+ reindex（不存在条目 → KeyError 不静默）。纠正/删除后缓存失效钩子（cache-aside：先写库再失效，key=`fluxion:mem:{tenant}:{user}:{type}`）。UI 属 Phase 4 X407，本任务只做后端契约。

### Checklist

- [x] 实现 reindex：纠正条目 → embedding 重算回写（PgVectorSemanticStore）
- [x] 纠正/删除触发 `fluxion:mem:*` 缓存失效（先写库再失效）
- [x] [NFR-PRIV-01][integration] RED：memory_user_service 模块缺失（ImportError）
- [x] [NFR-PRIV-01] GREEN：纠正后内容+embedding 更新、删除后不可检索、缓存失效钩子调用、reindex 缺失条目 KeyError
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| NFR-PRIV-01（后端契约） | integration | 真实 SQLite personal_memory 表 + PersonalMemoryStore + PgVectorSemanticStore + 记录式缓存失效钩子 | 纠正后内容+embedding 更新；删除后不可检索；缓存失效钩子调用 | backend/tests/memory/test_memory_user_service.py（4 用例） | `.venv/bin/python -m pytest backend/tests/memory/test_memory_user_service.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| NFR-PRIV-01 | ImportError（memory_user_service 缺失）→ 首轮 update_content 签名错修正 | 8 passed（memory/ 全量）：纠正+删除+失效钩子+reindex KeyError | test_memory_user_service.py:88-93（correct+reindex）、:107-115（delete+失效） | 真实 SQLite personal_memory 表全链路；embedding DB 行级核验（非 None） | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-006: Redis tenant cache adapter（TenantRedisCache）

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#3.3 数据设计, phase2-user-context-runtime-memory.design.md#3.4 接口设计
- **Acceptance-Refs**: S-05, E-05, RULE-P2-06

### Description

`services/cache.py` 新增 `TenantRedisCache`：tenant-scoped key、TTL、invalidation、degraded fallback、clear-all correctness。L1 内存必备 + Redis L2 可选增强，读写超时 300ms，超时即 degrade 回退 Store 直读（Redis 不作 SoT；**正确性不依赖 Redis**，remediation §13.5——ContextResolver 在无 Redis 时以 L1+Store 达标）。三类 key：`fluxion:ctx:{tenant}:{user}:{profile_ver}`(300s)、`fluxion:def:{kind}:{id}@{ver}`(300s)、`fluxion:mem:{tenant}:{user}:{type}`(60s)。发布/绑定变更、用户纠正/删除时 invalidate。本地 Redis `localhost:6379` 无密码。

> 优先级说明（v0.2 修订）：design FEAT 清单 P1，remediation §13.5 明确「Multi-Pod Semantic Equivalence = P0，Redis = P1 infrastructure；正确性不能依赖 Redis」——TASK-006 维持 P1；TASK-007 管线不依赖本任务（L1+Store 即可达标），Redis 仅作可选 L2。

### Checklist

- [ ] 实现 `TenantRedisCache.get/set/invalidate/clear(scope)`：L1 + Redis L2、300ms 超时、degraded 回退
- [ ] 落地三类 key 模式 + TTL + 失效时机（发布/绑定变更、用户纠正/删除）
- [ ] [E-05][integration] 修改生产代码前，编写验收测试并记录 RED：Redis 连接超时 → degraded 模式回退 Store 直读、不抛错、请求正常
- [ ] [S-05][E2E] 真实 Redis 启动后 kill → resolve 返回正确结果（回退直读）；重启 Redis → 恢复缓存命中
- [ ] [S-05] 断言 clear-all 后正确性不变（无脏缓存残留）
- [ ] 断言无 Redis 配置时 L1+Store 路径解析正确（正确性不依赖 Redis，remediation §13.5）
- [ ] degrade 次数可观测（复用现有 metrics 模式）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实 Redis 进程（kill/重启）、cache adapter、真实 Store | 停 Redis 结果正确；重启后恢复缓存；clear-all 无脏数据 | planned | planned | planned |
| E-05 | integration | Redis 连接超时（真实 socket 超时）、cache adapter → Store | degraded 回退直读；不抛错 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-007: ContextResolver 十段管线 + 检索链接通

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: phase2-user-context-runtime-memory.design.md#3.2 架构设计, phase2-user-context-runtime-memory.design.md#3.4 接口设计, phase2-user-context-runtime-memory.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-02, S-08, S-09, E-01, E-02, E-04, B-01, RULE-P2-02, RULE-P2-04, RULE-P2-08

### Description

新建 `services/context_resolver.py`：Identity→User→Agent→Runtime→Profile→Memory→Capability→Credential→Policy→Snapshot 十段解析管线（应用服务，跨域 use case 编排）。selector 以 **`agent_id` 为主坐标**（remediation §13.1：`tenant+user+agent`（+ execution inputs）为等价性主键，Runtime→RuntimeProfile 为内部 mechanics 解析）。Identity 段复用 Phase 1 ChannelIdentity→PlatformUser 映射。`ContextResolver.resolve(request, selector) -> ResolveResult`（`ExecutionSnapshot` + `UserContext` + `resolution_trace` + `budget_used`），fail-closed 抛 `ContextResolutionError`（带 slug + 整码）。接通 `PersonalMemoryRetriever`（`memory/` 域，经 `SemanticStoreProvider` recall + `memory_type` 过滤，禁止读 `SessionContextSummary`）。context budget 按优先级截断 + `truncated=true`。**L1 内存缓存必备**；Redis L2 可选（正确性不依赖 Redis，remediation §13.5）。resolution_trace 每段记 resolved version + 耗时，关联 trace_id。Secret 检索只存 ref→version 进 `credential_versions`，明文零泄露。

### Checklist

- [ ] 实现 `ResolveResult` dataclass 与十段管线骨架，每段记录 version + 耗时进 `resolution_trace`
- [ ] 接通 `PersonalMemoryRetriever` → Memory 段（recall → manifest，检索失败降级空 manifest + `truncated=true`，不阻塞 Execution）
- [ ] 实现 context budget：超限按优先级截断 + `truncated=true`
- [ ] [B-01][unit] 修改生产代码前，编写验收测试并记录 RED：manifest 超 budget → 按优先级截断 + `truncated=true`
- [ ] [E-01][integration] 非 dev 模式缺身份头 → 401 fail-closed + 统一 envelope（H1 回归验证）
- [ ] [E-02][integration] 检索 Secret 缺失/版本不存在 → 明确错误、日志无明文（RedactionProcessor）、无 `snapshot_digest` 产出
- [ ] [E-04][integration] `user_profile_version` 指向不存在版本 → fail-closed + 明确错误码，无 digest 产出
- [ ] [S-02][integration] 典型数据量（≤100 memory、≤20 capability）连续 resolve 50 次真实 Store，断言 P95 ≤ 300ms
- [ ] [S-08][integration] 修改生产代码前，编写验收测试并记录 RED（Gate G4/ARCH-07）：Execution-1 开始 pin v1 → 运行中发布 v2 → Execution-1 后续解析仍全 v1（无漂移）→ 新 Execution-2 使用 v2
- [ ] [S-09][integration] 修改生产代码前，编写验收测试并记录 RED（Gate G2/REQ-CAP-004）：同一 MCP Definition 下 User-A/User-B 不同 CredentialRef → resolve 后连接池 key 与 per-execution cache key 完全不串用；跨用户/跨租户读取拒绝
- [ ] 类型注解齐全、异常不吞、单函数 ≤50 行；`ContextResolutionError` 走统一错误码命名空间；性能路径 L1 必备 + Redis L2 可选（无 Redis 配置下 S-02 基准仍达标）
- [ ] **Spec verifier**：`RULE-fluxion-console-001` — 运行 `python -m pytest backend/tests/services/test_context_resolver.py -k identity`（planned）：断言 Identity 段复用 Phase 1 ChannelIdentity→PlatformUser 映射、未绑定身份仅 `/bind` 语义不进入 resolve
- [ ] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 S-02/E-02 verifier 套件（`backend/tests/services/test_context_resolver.py`，planned）：断言性能基准（P95≤300ms）、可靠性（fail-closed）、安全（零明文）、可观测（resolution_trace 关联 trace_id）均为编码期自动化证据，非事后补
- [ ] **Spec verifier**：`RULE-backend-quality-001` — 运行 `ruff check` + `mypy backend/src/fluxion/services/context_resolver.py`（planned）：断言类型注解完整、无异常吞、单函数 ≤50 行、性能路径走 L1+Redis L2
- [ ] **Spec verifier**：`RULE-backend-logging-001` — 运行 E-02 verifier 用例（planned）：断言 resolution_trace 走 structlog JSON、关联 `request_id`/`trace_id`/`tenant_id`、Secret 明文经 RedactionProcessor 零泄露
- [ ] **Spec verifier**：`RULE-backend-platform-001` — 运行 E-01/E-04 verifier 用例（planned）：断言 `ContextResolutionError` 携带模块前缀错误码并由中间件转统一 envelope `{code, message, data, request_id}`；SLO 目标（SLO-CTX-01）在测试中可断言
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | ContextResolver → 真实 Store（不 mock 数据库） | 50 次 resolve P95 ≤ 300ms | planned | planned | planned |
| S-08 | integration | 真实 Store + 运行中 publish（Gate G4） | Execution-1 全程 v1；Execution-2 用 v2 | planned | planned | planned |
| S-09 | integration | 真实 resolve + MCP prepare（Gate G2） | A/B 连接池/cache key 不串用；跨用户拒绝 | planned | planned | planned |
| E-01 | integration | Middleware → ContextResolver（真实 ASGI 栈） | 401 fail-closed + 统一 envelope | planned | planned | planned |
| E-02 | integration | ContextResolver → 真实 Secret store | 明确错误；日志/manifest/digest 零明文 | planned | planned | planned |
| E-04 | integration | ContextResolver → 真实 Profile 存储 | fail-closed；明确错误码；无 digest | planned | planned | planned |
| B-01 | unit | budget 计算纯逻辑（真实 manifest 构建） | 优先级截断 + `truncated=true` | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-008: 跨 Pod digest 一致性契约测试

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景, phase2-user-context-runtime-memory.design.md#3.1 方案选型
- **Acceptance-Refs**: S-01, RULE-P2-01, NFR-PERF-03

### Description

验证架构规则 28 / RULE-P2-01：两个独立 Application 实例（各自独立 ContextResolver/cache 实例，共享同一真实 PG + Redis）对相同 `tenant_id + user_id + agent_id`（+ execution inputs，remediation §13.1）各自 resolve，断言两份 `snapshot_digest` 完全相等（100% 一致），且 snapshot 含 `agent_definition_version`/`user_profile_version`/`memory_manifest`/`credential_versions`/`policy_versions`。复用 phase1 TASK-009 cross-Pod 契约模式；这是 N 独立实例模拟多实例的等价性验证（非部署编排）。

### Checklist

- [ ] 搭建双独立 Application 实例 fixture（独立 resolver/L1 cache，共享真实 PG + Redis）
- [ ] [S-01][E2E] 修改生产代码前，编写验收测试并记录 RED：已发布 agent_definition + user_profile + binding + personal memory 前置下，两实例各 resolve → `snapshot_digest` 完全相等
- [ ] [S-01] 断言 snapshot 含 `agent_definition_version`/`user_profile_version`/`memory_manifest`/`credential_versions`/`policy_versions` 且 `credential_versions` 无明文
- [ ] [S-01] 断言发布新版本（如 skill v1→v2）后两实例 digest 同步变化且仍相等（NFR-PERF-03 一致性）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 双 Application 实例 → 真实 PG + Redis → Registry → Secret → Memory（不 mock） | 两实例 digest 完全相等；V2 字段齐全；版本变更后仍相等 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-009: L2 legacy 迁移/删除（M202）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#4.4 数据迁移
- **Acceptance-Refs**: M202（dry-run 自动化）

### Description

L2 legacy 数据（session_memory level=l2，停双写遗留）一次性迁移：扫描 + dry-run 报告（行数/策略分类）→ 迁移为 Personal Memory 或删除。架构测试确认停用旧 L2 读路径（read_l2 cross-read 已删，M209-M216 已落地）。实现 `memory/application/l2_migration.py`：`audit_l2()`（dry-run 只读报告）+ `migrate_l2()`（幂等：二次执行零变更）。生产环境实际执行由运维手动跑（脚本幂等可重放）。

### Checklist

- [x] 实现迁移脚本：扫描 legacy L2 → dry-run 报告（行数/策略分类）→ 执行迁移/删除，幂等可重跑
- [x] [M202][integration] 验收测试 RED：audit_l2/migrate_l2 模块缺失（ImportError）
- [x] [M202] GREEN：seed legacy L2 → dry-run 行数与 seed 一致；执行后目标表计数一致；幂等（二次执行零变更）
- [x] architecture test：`read_l2` 只读 level=l2（cross-read 已删——既有 M209-M216 断言承接）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| M202（dry-run） | integration | 真实迁移脚本 → 真实 SQLite session_memory 表 | dry-run 行数核对；执行后计数一致；幂等 | backend/tests/memory/test_l2_migration.py（2 用例） | `.venv/bin/python -m pytest backend/tests/memory/test_l2_migration.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| M202（dry-run） | ImportError（l2_migration 模块缺失） | 2 passed：seed 3 行 → dry-run 报告 3 行 → 执行后 l2 清空 + 迁移表 3 行 + 幂等零变更 | test_l2_migration.py::test_m202 系列 | 真实 SQLite session_memory 表全链路；幂等二次执行零变更 | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-010: Multi-instance 等价性验证（kill 实例 / RPO=0）

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单, phase2-user-context-runtime-memory.design.md#2.5.2 功能验收场景
- **Acceptance-Refs**: S-06, RULE-P2-07

### Description

Phase 2 Gate 收尾验证（remediation §13.6 分层：Phase 2 = N application instances + shared PostgreSQL；真实 k8s 多副本/kill pod/rolling restart 移交 Phase 6）：N 个独立应用实例（进程级隔离，共享真实 PG + Redis）。各实例分别服务请求后 kill 其中一个实例进程，新请求打到存活实例：digest 一致、新请求正常、committed durable state RPO=0。同时覆盖 local cache clear 场景。复用 `local-pg-test-env`。

> 优先级说明：FEAT-P2-11 为 P1；作为 Phase 2 Gate 收尾，依赖 TASK-008 先闭合双实例一致性。

### Checklist

- [ ] 搭建 N 个独立应用实例（进程级隔离，共享真实 PG + Redis，复用 `local-pg-test-env`）
- [ ] [S-06][E2E] 修改生产代码前，编写验收测试并记录 RED：各实例分别服务请求 → kill 一个实例进程 → 新请求打到存活实例 → 断言 digest 一致、请求正常、RPO=0（committed durable state 不丢）
- [ ] [S-06] local cache clear 后新请求正确（无跨实例脏缓存）
- [ ] 验证结果留存证据（执行记录 + 关键断言输出）进 Acceptance Evidence
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | N 个真实独立应用实例、真实 PG/Redis、真实进程 kill（不 mock 进程/存储） | digest 一致；新请求正常；RPO=0 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-011: 用户自助 tool（对话即界面）

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-004, TASK-005
- **Source**: phase2-user-context-runtime-memory.design.md#2.3.1 功能清单（FEAT-P2-12，v0.4）, docs/design/08-用户旅程与体验设计.md#3.1 普通用户（UJ-U-04/UJ-U-06）
- **Acceptance-Refs**: S-10, UJ-U-04, UJ-U-06

### Description

把 UserDomainService 的用户能力暴露为 builtin tools，落实「对话优先」旅程原则（REQ-CAP-007：tool 是 Agent-facing invocation contract）：`user.profile.get/update`、`user.memory.search/correct/delete`、`user.preference.get/set`。工具走既有执行安全顺序（存在性/授权 → 风险策略 → 审批 → 执行）：读与偏好更新低风险 auto-approve；`user.memory.delete` 为确认级（高风险确认 + AuditLog）；全部调用进 AuditLog（规则 24）。learning gate（TASK-004）对 tool 路径同样生效——停学用户的记忆写入工具必须拒绝。用户由此在对话中完成 UJ-U-04（办事）/UJ-U-06（数据权利），Web 页面（X407）保留为合规兜底视图。

### Checklist

- [ ] 注册 builtin user tools 三组（profile get/update、memory search/correct/delete、preference get/set），挂 Agent capability allowlist（用户授权后可用）
- [ ] 风险分级接线：读/偏好更新 auto-approve；`user.memory.delete` 确认级；全部调用进 AuditLog（关联 tenant/user/execution）
- [ ] learning gate 贯通：停学用户经 tool 写入记忆 → 拒绝（与 X407 页面路径同语义）
- [ ] [S-10][E2E] 修改生产代码前，编写验收测试并记录 RED：对话中「把我的时区改成 Asia/Tokyo」→ 当前无 user tool 可完成（证明对话即界面缺口）
- [ ] [S-10] GREEN 断言：偏好即时生效；「忘掉刚才那条记忆」删除生效且进 AuditLog；停学用户记忆写工具拒绝
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | 真实 AgentLoop + builtin tool + UserDomainService + 真实 Store | 偏好/删除经对话生效；AuditLog 留痕；停学 tool 路径拒绝 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)（v0.4：对话优先旅程落地）

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)
