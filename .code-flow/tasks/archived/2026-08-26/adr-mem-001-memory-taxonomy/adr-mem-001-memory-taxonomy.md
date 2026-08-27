# Tasks: Memory Taxonomy（ADR-MEM-001）

- **Source**: adr-mem-001-memory-taxonomy.design.md
- **Created**: 2026-08-27
- **Updated**: 2026-08-27

## Proposal

固定 memory taxonomy（L0 working / L1 session raw / L2 legacy-user-raw 停双写 / SessionContextSummary session-scoped compaction / Episodic+Semantic user-scoped personal），修复三个已核实缺陷（双写、summary cross-read、假摘要），用 Summarizer SPI 替换字符串拼接，新建 personal_memory 表 + MemoryLearner.commit pipeline shape，落地 architecture-test 规则（PersonalMemoryRetriever 禁止读 SessionContextSummary + 写侧 commit enforcement），并收口 ADR-EXT-001 的 MEMORY enum pending（决议为 delete——memory 由 SessionMemoryStore + SemanticStore 分治）。本任务文件是 Phase 0 契约塑形 + 缺陷修复 + architecture-test 落地，不枚举 M201-M216 Sprint 实现（Rolling-wave）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | `SQLSessionMemoryStore` append/read L1 | TASK-001 | verified |
| S-02 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | `memory_sql.py` read_l2/read_l1 | TASK-001 | verified |
| S-03 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | `compact_context` + Summarizer SPI | TASK-002 | verified |
| S-04 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | `PersonalMemoryRetriever` → `SemanticStoreProvider` | TASK-004 | verified |
| E-01 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | import/dependency 静态 architecture test | TASK-004 | verified |
| E-02 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | Summarizer SPI 错误处理 | TASK-002 | verified |
| E-03 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | integration | commit pipeline enforcement test | TASK-004 | verified |
| B-01 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | unit | `personal_memory` delete + `learning_enabled` | TASK-003 | verified |
| B-02 | adr-mem-001-memory-taxonomy.design.md#2.4.1 功能验收场景 | unit | `PluginType` enum | TASK-005 | verified |

> 本表覆盖 design 全部 P0/P1 场景（9/9）；RULE（4 required）→ 场景全映射；RISK-01→S-01+B-02、RISK-02→S-03+E-02、RISK-03→E-01 全有验证场景。

---

## TASK-001: 存储层缺陷修复——双写 + cross-read + summary 重命名

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: adr-mem-001-memory-taxonomy.design.md#2.2.2 字段约束, adr-mem-001-memory-taxonomy.design.md#3.3 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-01, S-02

### Description

修复 memory 三缺陷中的存储层两个：(1) 双写——`_flush_new_records`（`memory.py:190-191`）对同一批 records 既 `append_l1` 又 `append_l2`，raw 会话翻倍进 user-level L2；`InMemory.append_summary`（`memory.py:71-75`）同时写 `_summaries`+`_l1`+`_l2`，summary 泄漏进 user-level L2 写侧。(2) cross-read——`read_l2`（`memory_sql.py:54`）用 `level.in_(L2, summary)`，session 摘要泄漏进 user-level retrieval。修复：`_flush_new_records` 只写 L1；`append_summary` 不交叉写 `_l2`；`read_l2` 只读 level==L2；summary 重命名 SessionContextSummary（level 值 `summary`→`session_context_summary`），`read_l1` 保留含 SessionContextSummary。（ADR-EXT-001 MEMORY enum 收口 green-before 补测见 TASK-005，本任务不承载契约层项。）

### Checklist
- [x] 双写修复：`memory.py:190-191` `_flush_new_records` 只 `append_l1`，删 `append_l2`；`memory.py:71-75` `InMemory.append_summary` 只写 `_summaries`+`_l1`，不交叉写 `_l2`
- [x] cross-read 修复：`memory_sql.py:54` `read_l2` 改 `level==L2` only，删 `level.in_(L2, summary)`；`read_l1`（L46）保留含 SessionContextSummary
- [x] summary 重命名：`_LEVEL_SUMMARY`→`_LEVEL_SESSION_CONTEXT_SUMMARY`=`"session_context_summary"`，`memory_sql.py` + `memory.py` InMemory 一致；`read_summaries` 同步
- [x] [S-01][integration] 真实 `SQLSessionMemoryStore`（sqlite+aiosqlite，非 mock）flush 一批 records → 断言只写 L1（`session_memory.level=l1`），不写 L2；`read_l1` 返回 session raw。先写测试记录 RED（双写缺陷真实存在）
- [x] [S-02][integration] 真实 `memory_sql.py` read_l2/read_l1 → 断言 `read_l2` 不含 SessionContextSummary（level=l2 only）；`read_l1` 含 SessionContextSummary。先写测试记录 RED（cross-read 缺陷真实存在）
- [x] [fluxion-runtime-core#RULE-fluxion-runtime-001] verifier：session-memory-externalized——memory externalized 是无状态核心；taxonomy 保留 `SQLSessionMemoryStore` durable 路径（S-01 双写只写 L1 + S-02 cross-read 删 summary）；session memory 外置到 SQLite(dev)/PostgreSQL(prod)，Pod 重启记忆不丢
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | `SQLSessionMemoryStore`（sqlite+aiosqlite）append_l1/read_l1 | flush 一批 records 只写 L1（level=l1），不写 L2；read_l1 返回 session raw | `backend/tests/integration/test_memory_taxonomy_storage.py::test_s01_flush_writes_only_l1_not_l2` | `uv run pytest backend/tests/integration/test_memory_taxonomy_storage.py -xvs` | verified |
| S-02 | integration | `memory_sql.py` read_l2/read_l1（真实 SQL level 过滤） | read_l2 不含 SessionContextSummary（level=l2 only）；read_l1 含 SessionContextSummary | `backend/tests/integration/test_memory_taxonomy_storage.py::test_s02_read_l2_excludes_session_context_summary` | `uv run pytest backend/tests/integration/test_memory_taxonomy_storage.py -xvs` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: `_level_counts` 返回 `{'l1': 1, 'l2': 1}`——双写缺陷真实存在（flush 既写 L1 又写 L2，`memory.py:190-191`）；`assert counts.get("l2",0)==0` → `1==0` | PASS: flush 后 `counts={'l1':1}`，无 `l2` 行；`read_l1` 返回 session raw | `test_memory_taxonomy_storage.py:78` `assert counts.get("l2",0)==0` + `:84` read_l1 含 alpha..epsilon | 真实 `SQLSessionMemoryStore`(sqlite+aiosqlite) + 真实 `MemoryManager._flush_new_records` flush 路径 + 裸 SQL `level` 聚合（绕过 read 侧） | verified |
| S-02 | FAIL: `read_l2` 返回 `['session compaction summary', 'legacy user raw']`——cross-read 缺陷真实存在（summary 泄漏进 user-level L2，`memory_sql.py:54` `level.in_(L2,summary)`）；`assert == ['legacy user raw']` 失败（index 0 为 summary） | PASS: `read_l2==['legacy user raw']`（不含 summary）；`read_l1` 含 SessionContextSummary | `test_memory_taxonomy_storage.py:121` `assert [r.content...]==['legacy user raw']` + `:128` not any summary + `:139` read_l1 含 summary | 真实 `SQLSessionMemoryStore` read_l2/read_l1 真实 SQL level 过滤（非 mock） | verified |

GREEN 命令：`uv run pytest backend/tests/integration/test_memory_taxonomy_storage.py -xvs` → 2 passed。
回归：`test_S_R17`/`test_S_R18` L2 断言同步改为停双写/cross-read 后的正确语义（[]）；全套件 284 passed，6 failed 全在 `workflow_poc/test_poc_restate.py`（Restate 单节点 PoC 已知受限，与 memory 改动零依赖，隔离单跑 PASS）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] RED recorded：S-01 双写（l1+l2）、S-02 cross-read（summary 进 read_l2）双缺陷真实失败
- [2026-08-27] GREEN：双写/cross-read/summary 重命名三修复落地；S-01+S-02 verified；S-R17/S-R18 回归断言同步修正
- [2026-08-27] completed (done)

---

## TASK-002: Summarizer SPI——替换假 _summarize + Model + 确定性 fallback + token_budget + source range/hash

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-mem-001-memory-taxonomy.design.md#2.2.1 功能清单, adr-mem-001-memory-taxonomy.design.md#3.3 接口设计, adr-mem-001-memory-taxonomy.design.md#2.4.1 S-03/E-02
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-03, E-02

### Description

替换假 `_summarize`（`memory.py:254-257`，只是 `"summary: " + " | ".join(content)`，无模型调用）为 Summarizer SPI：`Summarizer` Protocol + `SummaryResult(content, source_range_hash)` + `token_budget` 硬要求（§4.6 行 277）；`ModelSummarizer`（调 model）+ `DeterministicTruncationSummarizer`（fallback，确定性截断）+ `SummarizerRegistryProtocol`（镜像 ModelProviderRegistry 模式）；`compact_context`（`memory.py:132-143`）改经 registry resolve 调 Summarizer SPI，删 `_summarize`。model 不可用降级 fallback，不静默吞（带日志+trace）。

### Checklist
- [x] 新 `Summarizer` Protocol：`summarize(records, *, token_budget) -> SummaryResult`，`SummaryResult(content, source_range_hash)`（contracts，新模块或 contracts.py 追加）
- [x] `ModelSummarizer`（调 model provider）+ `DeterministicTruncationSummarizer`（fallback，确定性截断，带 source_range_hash）+ `SummarizerRegistryProtocol`（register/resolve，镜像 ModelProviderRegistry）
- [x] `compact_context`（`memory.py:132-143`）改经 Summarizer registry resolve 调 SPI，传 `token_budget`；删 `_summarize`（`memory.py:254-257`）
- [x] [S-03][integration] 真实 `compact_context` + Summarizer SPI（非 mock） → 断言调用 Summarizer SPI（非 `_summarize` 拼接）；summary 带 `source_range_hash` + `token_budget`；model 不可用走 `DeterministicTruncationSummarizer` fallback。先写测试记录 RED（假 `_summarize` 真实存在）
- [x] [E-02][integration] Summarizer SPI 错误处理 → model summarizer 超时/异常降级确定性截断 fallback，不静默吞（带日志+trace），不阻断主对话。先写测试记录 RED
- [x] [backend-code-quality-performance#RULE-backend-quality-001] verifier：doublewrite-summarizer-spi——双写修复（S-01 在 TASK-001 引用）+ Summarizer SPI 替换假 `_summarize`，不静默吞（fallback 带日志+trace）（S-03 + E-02）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | `compact_context` + Summarizer SPI（真实 registry resolve + 真实 fallback 路径） | 调 Summarizer SPI 非 `_summarize` 拼接；summary 带 source_range_hash + token_budget；model 不可用走 DeterministicTruncationSummarizer | `backend/tests/integration/test_summarizer_spi.py::test_s03_*` | `uv run pytest backend/tests/integration/test_summarizer_spi.py -xvs` | verified |
| E-02 | integration | Summarizer SPI 错误处理（真实 model 超时/异常 + 真实 fallback） | model 超时/异常降级确定性截断 fallback；不静默吞（带日志+trace）；不阻断主对话 | `backend/tests/integration/test_summarizer_spi.py::test_e02_*` | `uv run pytest backend/tests/integration/test_summarizer_spi.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL: `ModuleNotFoundError: No module named 'fluxion.runtime.summarizer'`（collect error，`uv run pytest backend/tests/integration/test_summarizer_spi.py -xvs`）——SPI 不存在，假 `_summarize` 仍在 | PASS: 3 passed（test_s03_compact_uses_summarizer_spi_not_string_concat / test_s03_model_summarizer_via_registry_uses_model_output / test_s03_model_unavailable_falls_back_to_deterministic_truncation） | SPI 分派+token_budget L131；非拼接格式 L133-L134；source_range_hash L136/L155-L156（64 hex）；model 输出 L154；降级内容一致 L173-L174 | 真实 AgentRuntime→MemoryManager→compact_context + 真实 SummarizerRegistry register/resolve；RecordingSummarizer/FixedModelProvider 为 SPI/Provider 真实实现（非 unittest.mock），经 registry 分派被真实调用 | verified |
| E-02 | 同上（同一 RED collect error 覆盖两场景） | PASS: 2 passed（test_e02_model_timeout_falls_back_with_log_and_trace_without_blocking / test_e02_model_error_falls_back_not_silent） | 不阻断（elapsed<1.0s vs provider 5s）L202；trace 关联 trace_id + error_type + fallback L210-L212/L241-L242；结构化 warning 携带 trace_id/error_type L220/L248 | Offline/Timeout ModelProvider 为真实 Provider 实现（complete 真抛 ModelProviderError / 真超 asyncio.wait_for(timeout_ms=50)）；降级走真实 DeterministicTruncationSummarizer 路径 + 真实 RuntimeContext.trace 事件 + caplog 捕获 fluxion.runtime.memory 结构化日志 | verified |

实现落点：新模块 `backend/src/fluxion/runtime/summarizer.py`（SummaryResult / Summarizer / SummarizerRegistryProtocol / SummarizerRegistry / DeterministicTruncationSummarizer / ModelSummarizer / compute_source_range_hash / default_summarizer_registry）；`memory.py` compact_context 改经 registry resolve（token_budget=max_context_tokens）+ `_summarize_records` 降级路径（trace 事件 + `emit_memory_event_log` warning）+ 删 `_summarize`；`CompactionResult` 增 `source_range_hash`；`observability/logging.py` 增 `emit_memory_event_log`（镜像 emit_workflow_event_log）；`agent.py` 增 `summarizer_registry` 透传。循环依赖处理：summarizer 顶层不 import memory（MemoryRecord 仅 TYPE_CHECKING；`_cut_to_token_budget` 内 function-level import `_estimate_tokens`）。

回归：`uv run pytest backend/tests/ --ignore=backend/tests/workflow_poc -q` → 260 passed, 1 skipped（S-P13-07 planned smoke）。受影响 e2e `test_S_R18_repeated_compaction_does_not_summarize_summaries` 断言从旧假格式 `"summary: turn-0"` 更新为确定性截断输出 `"turn-0 | turn-1 | turn-2"`（语义不变：第二次压缩不重摘要 summary）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)

---

## TASK-003: Personal Memory 模型 + MemoryLearner.commit pipeline shape + personal_memory 表 + tenant 隔离

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-mem-001-memory-taxonomy.design.md#2.2.2 字段约束, adr-mem-001-memory-taxonomy.design.md#3.3 接口设计, adr-mem-001-memory-taxonomy.design.md#2.4.1 B-01/E-03
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: B-01

### Description

新建 user-scoped `personal_memory` 表（id/tenant_id/user_id/memory_type/content/embedding/source_session_id/source_range_hash/learning_enabled + tenant 隔离索引，NFR-SEC-01）；`MemoryLearner.commit(candidate, *, policy_decision, consent, learning_enabled)` shape——`learning_enabled=false` 拒绝写入（user control NFR-PRIV-01），必经 MemoryCandidate→Policy/Consent→Commit pipeline（§4.7 行 286）。**口径声明（review 修复）**：commit shape **含最小 `learning_enabled` gate 实现**（`learning_enabled=false` 拒写，B-01 可验证的真实行为）；完整 candidate extraction / Policy/Consent pipeline 细节（Phase 2）不在本任务范围。E-03 写侧 enforcement 的 commit 侧由本 TASK 提供 shape，architecture-test 落地在 TASK-004。

### Checklist
- [x] 新 `personal_memory` 表 model（`schema.py`）：id/tenant_id/user_id/memory_type(`episodic`/`semantic`)/content/embedding(vector,nullable)/source_session_id/source_range_hash/learning_enabled/created_at/updated_at + tenant_id+user_id 索引（NFR-SEC-01 tenant 隔离强制）
- [x] `MemoryLearner.commit(candidate, *, policy_decision, consent, learning_enabled)` shape：`learning_enabled=false` 拒写（user control）；唯一 personal memory 写入入口（Phase 0 shape，pipeline 实现细节 Phase 2）
- [x] [B-01][unit] 真实 `personal_memory` model（非 mock） → 断言 `learning_enabled=false` 不再写入新 personal memory；已有可查看/纠正/删除（NFR-PRIV-01）。先写测试记录 RED（表/commit 未实现）
- [x] [backend-database#RULE-backend-database-001] verifier：memory-level-semantics-personal-schema——`session_memory` level 语义收紧（l1/l2/session_context_summary，S-02 在 TASK-001 引用）+ 新 `personal_memory` 表 + tenant 隔离索引（B-01）；SQLite/PostgreSQL 共享 schema
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | `personal_memory` model + `MemoryLearner.commit`（真实表 schema + 真实 commit shape） | learning_enabled=false 不再写入新 personal memory；已有可查看/纠正/删除 | `backend/tests/unit/test_personal_memory_model.py::test_b01_*` | `uv run pytest backend/tests/unit/test_personal_memory_model.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: `ModuleNotFoundError: No module named 'fluxion.runtime.personal_memory'`（collect error，模块/表未实现） | PASS: `uv run pytest backend/tests/unit/test_personal_memory_model.py` 2 passed | learning_enabled=false 拒写+真实表零行：test_personal_memory_model.py L64-L68；已有可查看（含 provenance/tenant 隔离）：L81-L97；纠正：L103-L112；删除+重复删除 False：L115-L117 | 真实 SQLAlchemy async SQLite engine（`sqlite+aiosqlite:///:memory:` + `metadata.create_all` 建真实 `personal_memory` 表，fixture L34-L40）；`PersonalMemoryStore`/`MemoryLearner.commit` 全部走真实表查询（select/insert/update/delete），无 mock | verified |

实现落地：`backend/src/fluxion/registry/schema.py` 新增 `personal_memory` 表（id/tenant_id/user_id/memory_type/content/embedding-JSON-nullable/source_session_id/source_range_hash/learning_enabled/created_at/updated_at + `idx_personal_memory_user`(tenant_id,user_id,id)，列序避开 F8 教训）；`backend/src/fluxion/runtime/personal_memory.py`（MemoryType episodic/semantic、MemoryCandidate、PolicyDecision/ConsentDecision shape、CommitResult、PersonalMemoryStore 公开面仅 list_entries/update_content/delete + 私有 `_insert`（E-03 锚点）、MemoryLearner gate 顺序 learning_enabled→policy→consent）。embedding 存 JSON（Phase 0 SQLite/PostgreSQL 共享 schema；pgvector ivfflat 属 Phase 1 FEAT-17）。回归：`uv run pytest backend/tests/ --ignore=backend/tests/workflow_poc -q` → 262 passed, 1 skipped（skip 为 S-P13-07 planned smoke）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)

---

## TASK-004: PersonalMemoryRetriever + architecture-test 规则（读侧 + 写侧 enforcement）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: adr-mem-001-memory-taxonomy.design.md#3.2 架构设计, adr-mem-001-memory-taxonomy.design.md#3.3 接口设计, adr-mem-001-memory-taxonomy.design.md#2.4.1 S-04/E-01/E-03
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-04, E-01, E-03

### Description

新 `PersonalMemoryRetriever.recall(tenant_id, user_id, query, top_k)` shape——经 `SemanticStoreProvider` SPI（已有形状 contracts.py:190-202）取 Episodic/Semantic，禁止读 `session_memory` 的 SessionContextSummary（硬边界读侧）。落地 architecture-test 规则：读侧（E-01，PersonalMemoryRetriever 不得 import/read SessionContextSummary）+ 写侧（E-03，绕过 `MemoryLearner.commit` 直写 `personal_memory` 含 summarizer 直写被阻断；SessionContextSummary 不得 auto-commit 进 UserProfile §4.6 行 276）。

### Checklist
- [x] 新 `PersonalMemoryRetriever.recall(tenant_id, user_id, query, top_k)` shape：经 `SemanticStoreProvider` SPI（已有形状 contracts.py:190-202）取 Episodic/Semantic；不读 `session_memory` SessionContextSummary
- [x] architecture-test 读侧（E-01）：`PersonalMemoryRetriever` import 或 read `SessionContextSummary` → 测试失败 CI 阻断（import/dependency 静态测试）
- [x] architecture-test 写侧（E-03）：绕过 `MemoryLearner.commit` 直写 `personal_memory`（含 summarizer 直写）→ 测试失败；`SessionContextSummary` 不得 auto-commit 进 `UserProfile`（§4.6 行 276）
- [x] [S-04][integration] 真实 `PersonalMemoryRetriever` → `SemanticStoreProvider`（非 mock） → 断言经 SemanticStore 取 Episodic/Semantic；不读 session_memory SessionContextSummary。先写测试记录 RED（Retriever 未实现）
- [x] [E-01][integration] import/dependency 静态 architecture test → 断言 PersonalMemoryRetriever import/read SessionContextSummary 失败。先写测试记录 RED
- [x] [E-03][integration] commit pipeline enforcement test → 断言绕过 commit 直写 personal_memory architecture test 失败；SessionContextSummary 不 auto-commit UserProfile。先写测试记录 RED
- [x] [fluxion-dfx#RULE-fluxion-dfx-001] verifier：personal-memory-architecture-test——PersonalMemoryRetriever 不读 SessionContextSummary 须 architecture test 自动化证据（S-04 + E-01 读侧 + E-03 写侧 enforcement）；CI 阻断
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | `PersonalMemoryRetriever` → `SemanticStoreProvider`（真实 SPI 调用链） | 经 SemanticStore 取 Episodic/Semantic；不读 session_memory SessionContextSummary | `backend/tests/integration/test_personal_memory_architecture.py::test_s04_*` | `uv run pytest backend/tests/integration/test_personal_memory_architecture.py -xvs` | verified |
| E-01 | integration | import/dependency 静态 architecture test（真实模块依赖扫描） | PersonalMemoryRetriever import/read SessionContextSummary → 失败 | `backend/tests/integration/test_personal_memory_architecture.py::test_e01_*` | `uv run pytest backend/tests/integration/test_personal_memory_architecture.py -xvs` | verified |
| E-03 | integration | commit pipeline enforcement test（真实写入路径拦截） | 绕过 commit 直写 personal_memory → 失败；SessionContextSummary 不 auto-commit UserProfile | `backend/tests/integration/test_personal_memory_architecture.py::test_e03_*` | `uv run pytest backend/tests/integration/test_personal_memory_architecture.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | FAIL: `ImportError: cannot import name 'PersonalMemoryRetriever'`（test_s04_* 两例，Retriever 未实现） | PASS: 9 passed（含 S-04 两例） | SPI 契约符合性+只命中 personal Episodic/Semantic：test_personal_memory_architecture.py L198/L204-L214；top_k/tenant 隔离：L226-L228 | 真实 `PersonalMemoryRetriever` → `TableBackedSemanticStore`（真实 `SemanticStoreProvider` 实现，`isinstance` Protocol L198）→ `PersonalMemoryStore` 真实表查询；写入经真实 `MemoryLearner.commit`；同表 seed 含查询词的 session_context_summary 行，L211 断言其内容不泄漏 | verified |
| E-01 | green-before（记录原因）：静态守卫落地时读侧边界已由 TASK-002/003 实现（memory 侧无 personal_memory 依赖），无真实缺陷可 RED；守卫以 teeth-proof 证明可捕获违规（L272-L274 synthetic violating source 三断言全命中），非 vacuous | PASS | personal_memory.py 不得 import memory/memory_sql（L257-L258）、不得 import session_memory 表（L260）、不得出现 level 字面量（L262）；teeth-proof L272-L274 | 真实模块源码 AST 扫描（`_imported_modules`/`_schema_imported_names`，镜像 test_plugin_architecture 惯例） | verified |
| E-03 | green-before（记录原因）：写侧 `_insert` 私有 + gate 已由 TASK-003 实现，静态/结构守卫落地即绿；teeth-proof（L321 synthetic bypass 调用被 `_insert_callers` 捕获）证明非 vacuous | PASS | 公开面 == {list_entries,update_content,delete}（L287）；`._insert(` 调用只在 (MemoryLearner, commit)（L311）；summarizer/memory/memory_sql 无 personal_memory 引用（L328-L329）；行为断言——真实 compaction 后 personal_memory 零行（L361-L367） | 真实 inspect/AST 结构检查 + 真实行为链：AgentRuntime + SQLSessionMemoryStore（真实 session_memory 表）+ PersonalMemoryStore（真实 personal_memory 表）compaction 后 `list_entries == []` | verified |

GREEN 命令：`uv run pytest backend/tests/integration/test_personal_memory_architecture.py -xvs` → 9 passed。回归：`uv run pytest backend/tests/ --ignore=backend/tests/workflow_poc -q` → 271 passed, 1 skipped（skip 为 S-P13-07 planned smoke）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] RED recorded：S-04 两例 ImportError（Retriever 未实现）；E-01/E-03 green-before（边界已由 TASK-002/003 实现，teeth-proof 防守卫空转）
- [2026-08-27] GREEN：PersonalMemoryRetriever + E-01/E-03 architecture-test 落地；S-04/E-01/E-03 verified
- [2026-08-27] completed (done)

---

## TASK-005: PluginType MEMORY 收口 green-before（ADR-EXT-001 决议验证）

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: adr-mem-001-memory-taxonomy.design.md#2.4.1 B-02
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: B-02

### Description

收口 ADR-EXT-001 MEMORY enum pending 决议（delete）——green-before 补测：`PluginType` 无 `MEMORY` 成员，memory 由 `SessionMemoryStore` SPI + `SemanticStoreProvider` SPI 分治。本任务为纯契约层验证（无存储/缺陷修复内容），与 TASK-001 存储缺陷修复解耦（review 修复：B-02 原误归 TASK-001，逻辑为 ADR-EXT-001 收口项）。

### Checklist
- [x] [B-02][unit] 真实 `PluginType` enum → 断言无 `MEMORY` 成员；`PluginType.MEMORY` 访问 AttributeError；memory 由 `SessionMemoryStore` SPI + `SemanticStoreProvider` SPI 分治。green-before 补测（MEMORY 已由 ADR-EXT-001 删除，本 ADR 收口决议）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-02 | unit | `PluginType` enum（contracts.py 真实定义） | 无 MEMORY 成员；`PluginType.MEMORY` AttributeError；分治承接 | `backend/tests/unit/test_plugin_type_memory_enum.py::test_b02_*` | `uv run pytest backend/tests/unit/test_plugin_type_memory_enum.py -xvs` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-02 | green-before 补测（无法 RED）：MEMORY 成员已由 ADR-EXT-001 从 contracts.py 删除（enum 注释明示"MEMORY 由 ADR-MEM-001 删除"），本 ADR 只做收口决议验证，不伪造失败（cf-task:start 规则 #7） | PASS: 3 passed（`uv run pytest backend/tests/unit/test_plugin_type_memory_enum.py -xvs`） | 无 MEMORY 成员 L26；AttributeError L31；分治承接 L44-L59（Protocol 双侧 + SEMANTIC_STORE L55 + 方法面互不重叠 L58） | 直接 import 真实 `contracts.py`（PluginType/SemanticStoreProvider）与 `memory.py`（SessionMemoryStore/MemoryRecord），无 mock 无副本枚举 | verified |

回归：`uv run pytest backend/tests/unit/` → 54 passed（含既有 test_provider_contracts.py 12 项，无冲突）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)
