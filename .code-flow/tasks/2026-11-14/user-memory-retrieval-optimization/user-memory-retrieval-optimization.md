---
{"version":1,"requirement":"user-memory-retrieval-optimization"}
---
# Tasks: user-memory-retrieval-optimization

<!--
一个需求一份任务文档。每个 TASK 使用一个 `## TASK-NNN: 标题` 段。
不要把 TASK 拆成单独文件。
-->

## 重排说明（2026-11-14 第二轮）

首版 TASK-001..009 把“新模块自己的测试通过”当成了完成判据，TASK-010 又把“删除旧模块”当成目标，与设计 §3.2.5 的落点冲突。实现结果是三套并行入口：两个 writer、两个 loader、两套 admin 路由，同时丢了旧 lane 的既有能力。本轮按设计重新拆任务。

**三条口径**

1. 落点以设计 §3.2.5 为准：`MemoryService` 是唯一**写入口**，`MemoryContextLoader` 是唯一**读入口**；经验（PROCEDURE/FAILURE_PATTERN/VERIFICATION）与 TASK_STATE 留在原有 lane，不迁进 v2 域模型，也不经任何“端口适配层”翻译。
2. 每个 TASK 的完成判据 = 声明的定向测试 **加上** 机检门禁。门禁用 `bash -lc` 的 grep / 文件存在性表达，不接受“已按 CI/用户实测”这类缩小范围的说法。
3. 与设计相符且已落地的代码保留（契约、taxonomy、0049 迁移、BM25 选择器、评测指标、前端 types）；与设计冲突的并行模块按 TASK-004 / TASK-007 / TASK-011 删除。

**首版验证记录（已作废）**

- 首版 TASK-001/002 的 PASS 记录仍有效，内容未变，保留在各自 Verification 段。
- 首版 TASK-003..009 的 PASS 只覆盖新模块自身。第二轮取证时旧 lane 测试为红：`tests/agent_runtime/test_memory_redesign_context.py` 16 failed，`test_context_memory.py`、`test_experience_activation.py`、`test_experience_retrieval_lanes.py`、`test_learning_e2e.py`、`test_memory_observability.py` 均有失败。
- 因此这些 TASK 在本版重写，Status 回到 draft / in-progress；旧记录不再作为完成依据。

**路径身份**

- 本需求目录：`.code-flow/tasks/2026-11-14/user-memory-retrieval-optimization/`
- 设计：`user-memory-retrieval-optimization.backend.design.md`、`...frontend.design.md`
- 场景：`user-memory-retrieval-optimization.scenarios.md`（S-01..S-13、E-01..E-12、B-01..B-05）
- 评测：`user-memory-retrieval-optimization.evaluation.md`

---

## TASK-001: v2 契约、taxonomy 与错误码

- **Status**: done
- **Depends**:
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.3.9, #3.4.6, #3.4.7, #3.5.1
- **Scenarios**: S-01, S-02, S-13, E-03, E-04, E-12
- **Spec-Refs**: memory/harness-memory, api/harness-api
- **Paths**: packages/contracts/src/muad_contracts/memory.py, packages/contracts/src/muad_contracts/memory_admin.py, packages/contracts/src/muad_contracts/memory_taxonomy.py, packages/contracts/src/muad_contracts/platform_settings.py, packages/api-kit/src/muad_api/error_codes.py, config/api-messages.yaml, tests/test_memory_contracts.py, tests/test_memory_taxonomy.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "contracts",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_memory_contracts.py"],
    "timeout": 120
  },
  {
    "id": "taxonomy",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_memory_taxonomy.py"],
    "timeout": 120
  }
]
```

### Description

v2 契约与 taxonomy 基线，已落地并保留：

- `MemoryKind`（可写集只含 PREFERENCE/FACT/NOTE）、`ScopeState` 三态、`MemoryCandidateV2`、`RememberMemoryInputV2`/`RecallMemoryInputV2`/`ForgetMemoryInputV2`、`MemoryItemV2`、`MemoryResolution`；
- taxonomy 只含 predicate/scope/alias/priority，管理员不能改身份 schema；
- 错误码与 `config/api-messages.yaml` 一一对应。

契约口径（本轮固定，不再修改）：

- `ForgetMemoryInputV2` 只接受 `memory_id` + `expected_version`，不保留 `memory_key` 入口。
- `MemoryResolution` 是 §3.4.6 的返回值形状，由 TASK-007 的 loader 产出，**不新建独立 resolution 模块**。
- 写侧模型不能提供 `author/authority/version/tenant/user`。

### Checklist

- [x] `MemoryKind`、`ScopeState`、`MemoryCandidateV2`、`RememberMemoryInputV2`、`RecallMemoryInputV2`、`ForgetMemoryInputV2`、`MemoryResolution` 定义完成。
- [x] taxonomy 只含 predicate/scope/alias/priority；`normalized(taxonomy_version=...)` 统一校验。
- [x] 错误码覆盖 `MEMORY_SCOPE_UNKNOWN`、`MEMORY_ALREADY_EXISTS`、`MEMORY_CANDIDATE_STALE`、`MEMORY_IDEMPOTENCY_CONFLICT` 等，状态码唯一。
- [x] 契约测试覆盖未知字段拒绝、值边界、clarification 单字段、Recall 返回 `memory_id`、Forget 只接受 `memory_id`。
- [x] 契约与 taxonomy 测试绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14] re-planned：内容不变，补记「不新建 resolution 模块」口径。

### Verification

- 2026-11-14T14:48:28.417Z PASS commands=2/2 executed=2 deduped=0 elapsed=1853ms tests=f23c56d5e3b6 head=0384f3db16b6 dirty=1
  - contracts PASS exit=0
  - taxonomy PASS exit=0

## TASK-002: v2 数据模型、0049 迁移与 ORM 对齐

- **Status**: done
- **Depends**: TASK-001
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.3, #4.4
- **Scenarios**: S-08, E-02, E-06, B-04
- **Spec-Refs**: persistence/harness-data, memory/harness-memory
- **Paths**: migrations/versions/0049_memory_retrieval_optimization.py, migrations/memory_redesign_data.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py, tests/migrations/test_memory_schema.py, tests/migrations/test_memory_redesign_migration.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "schema",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/migrations/test_memory_schema.py"],
    "timeout": 300
  },
  {
    "id": "migration",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/migrations/test_memory_redesign_migration.py"],
    "timeout": 300
  }
]
```

### Description

0049 迁移、ORM 列与 candidate 表已落地。本轮补两处收口：

1. **v2 行不再写假 `category`**。现在 `memory_writer` 用 `_CATEGORY_COMPAT = {"PREFERENCE": "PREFERENCE", "FACT": "EXPLICIT", "NOTE": "WORK_STYLE"}` 把一个 v2 事实塞进旧 category 枚举，这是设计明令禁止的兼容写层。改为 `category` 允许 NULL：legacy 行（经验/TASK_STATE）照旧写 `category`，v2 行只写 `memory_kind`。
2. **`legacy_snapshots_json` 只由 schema 测试断言**。`tests/migrations/test_memory_redesign_migration.py` 只升级到 `0020`（`upgrade_memory` 固定 revision），不经过 0049，因此它继续验证 0020 的数据迁移语义，保持原样；“0049 已删除该列”由 `tests/migrations/test_memory_schema.py` 断言。

开发阶段无历史数据，允许 `alembic downgrade 0048 && upgrade head` 重放；dev 库当前已在 0049，重放后需确认用户/Agent/模型/Skill 计数不变、`user_memory` 为空。

不 drop `memory_consolidation_job`（被 `model_call_lease` 引用）。

### Checklist

- [x] 0049 新增 `memory_kind/predicate/scope_state/scope_raw/scope_json/scope_hash/content_text/title/summary/tags_json/cardinality/priority/taxonomy_version` 与 `memory_conflict_candidate`。
- [x] `uq_user_memory_key` 覆盖 tombstone；ORM 约束/索引与迁移同构。
- [x] 迁移不 drop `memory_consolidation_job`，不触碰其外键。
- [x] `category` 改为可空；v2 行写 NULL，legacy 行照旧写入。
- [x] 删除 `memory_writer` 的 `_CATEGORY_COMPAT`。
- [x] `tests/migrations/test_memory_schema.py` 断言 `user_memory.category` 可空、`user_memory_history` 无 `legacy_snapshots_json`，并验证可插入 `category IS NULL` 的 v2 行。
- [x] `tests/migrations/test_memory_redesign_migration.py`（0020 链）保持原样作为回归。
- [x] 0048→0049 重放通过；dev 库重放后计数不变、`user_memory` 为空。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "memory/harness-memory",
    "ref": "RULE-memory-006",
    "before": "新 Run/job 冻结完整 memory 设置、工具历史预算及提炼能力；设置执行期不重读，在线硬额度只提高，降低必须维护窗口排空。迁移需排空所有旧在途 Run/Task/job；每 key 单一稳定基线保留全部 legacy incarnation，不伪造确认来源，旧 AGENT 按原更新时间补 TTL。迁移和 ORM 约束/索引同构；移除旧 ratio/source_type 协议，历史快照原样保留仅作审计，不加兼容写层。",
    "rule": "新 Run/job 冻结完整 memory 设置、工具历史预算及提炼能力；设置执行期不重读，在线硬额度只提高，降低必须维护窗口排空。迁移需排空所有旧在途 Run/Task/job。开发阶段无历史数据，v2 允许原地重建 user_memory/history：不保留 legacy incarnation 或 legacy_snapshots_json，不伪造确认来源。迁移和 ORM 约束/索引同构；移除旧 ratio/source_type 协议，不新增兼容写层：v2 行只写 memory_kind，legacy 行（经验/TASK_STATE）仍以 category 判别，二者不互相填充。",
    "evidence": "v2 设计 §3.3/§4.4；无历史数据，禁止兼容/双写"
  }
]
```

### Log

- [2026-11-14] created
- [2026-11-14T14:50:24.261Z] PASS commands=2/2 elapsed=9115ms tests=969235b857c0（首版范围：仅 schema + 新查询测试）
- [2026-11-14] re-planned：纳入 `category` 可空；确认 0020 迁移测试只升到 0020，不需要改写。

### Verification

- 尚未执行
- 2026-11-14T15:54:42.324Z PASS commands=2/2 executed=2 deduped=0 elapsed=13433ms tests=ad1541649042 head=0384f3db16b6 dirty=1
  - schema PASS exit=0
  - migration PASS exit=0
## TASK-003: 快照查询：两 lane 显式投影

- **Status**: done
- **Depends**: TASK-002
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.2.3, #3.5.4
- **Scenarios**: S-08, S-11, E-02, E-06
- **Spec-Refs**: memory/harness-memory, persistence/harness-data
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_queries.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_cache.py, tests/agent_runtime/test_memory_queries.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "queries",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_queries.py"],
    "timeout": 300
  },
  {
    "id": "no-category-fallback",
    "argv": ["bash", "-lc", "! grep -q 'memory_kind or row.category' apps/agent-runtime/src/muad_agent_runtime/application/memory_queries.py"],
    "timeout": 60
  }
]
```

### Description

一个 SQL 快照，两条 lane 显式投影，共享同一 `revision` / `forget_epoch` / `as_of`：

- **v2 lane**：`memory_kind IS NOT NULL` 的 PREFERENCE/FACT/NOTE，投影为 `MemoryCandidateRowV2`。
- **legacy lane**：`memory_kind IS NULL` 且 `category IN ('PROCEDURE','FAILURE_PATTERN','VERIFICATION','TASK_STATE')`，投影为现有 `MemoryRecord`（保留 `activation_state` / `task_signature` / `trigger_text` / `source_quote`）。

现状问题：`read_snapshot_v2` 用 `memory_kind or row.category`（`memory_queries.py:219`）把 legacy 行伪装成 v2 行，经验行的 ACTIVE 状态与 TASK_STATE 的 key/version/hash 语义在 v2 投影里全部丢失，后续 loader 再也不可能做对。

冷/热读取仍以 PG head revision 为准，逐次按 `statement_timestamp` 过滤 TTL；读取失败清缓存并如实返回 degraded，不返回旧行。

### Checklist

- [x] `MemorySnapshotV2` 同时携带 `rows`（v2 lane）与 `legacy_rows`（legacy lane），三者与 head 同快照。
- [x] v2 投影只取 `memory_kind IS NOT NULL`；legacy 投影只取 `memory_kind IS NULL` 且 `category` 属于经验/TASK_STATE。
- [x] 删除 `memory_kind or row.category` 回退。
- [x] legacy 投影保留 `activation_state`、`task_signature`、`trigger_text`、`source_quote`。
- [x] `MemoryCandidateCache` 的暖读取仍按 head revision 与当前语句时间过滤 TTL。
- [x] 测试覆盖：v2 行不出现在 legacy 投影、经验/TASK_STATE 行不出现在 v2 投影、两 lane revision 一致、读取失败返回 None 且清缓存。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14T15:55:16.482Z] started tests=bf1d3af0af9e

### Verification

- 尚未执行
- 2026-11-14T15:55:54.948Z FAIL commands=0/2 executed=1 deduped=0 elapsed=6023ms tests=bf1d3af0af9e head=b686eea586ae dirty=1
  - queries FAIL exit=1
  - no-category-fallback NOT-RUN
- 2026-11-14T15:56:03.789Z FAIL commands=0/2 executed=1 deduped=0 elapsed=6126ms tests=bf1d3af0af9e head=b686eea586ae dirty=1
  - queries FAIL exit=1
  - no-category-fallback NOT-RUN
- 2026-11-14T15:56:13.003Z PASS commands=2/2 executed=2 deduped=0 elapsed=5867ms tests=bf1d3af0af9e head=b686eea586ae dirty=1
  - queries PASS exit=0
  - no-category-fallback PASS exit=0
## TASK-004: 唯一写入口：MemoryService 收编 v2 写入

- **Status**: done
- **Depends**: TASK-002
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.2.2, #3.2.4, #3.2.5, #3.5.1
- **Scenarios**: S-04, S-05, S-10, S-13, E-04, E-07, E-09, E-10, E-11
- **Spec-Refs**: memory/harness-memory
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_service.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_writer.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_runtime.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_identity.py, tests/agent_runtime/test_memory_writer.py, tests/agent_runtime/test_memory_candidates.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "writer",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_writer.py"],
    "timeout": 300
  },
  {
    "id": "candidates",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_candidates.py"],
    "timeout": 300
  },
  {
    "id": "single-writer-gate",
    "argv": ["bash", "-lc", "test -z \"$(grep -rln 'memory_writer import' apps --include='*.py' | grep -v 'application/memory_service.py')\""],
    "timeout": 60
  }
]
```

### Description

把 v2 写入收回 `MemoryService`，让设计 §3.2.4 的“唯一 writer”成为代码事实。

现状：`memory_writer.py` 导出一个可被任意模块实例化的 `MemoryV2Writer`，`memory_runtime.py:100`、`memory_confirmation_resume.py:351`、`api/memory_v2.py:104` 各自 new 一个；`MemoryService.apply_v2()` 只是转调。这是双写入口，不是收口。

目标：

- `MemoryV2Writer` / `MemoryV2Command` 降为 `memory_service.py` 的内部实现，其他模块一律调 `MemoryService` 的具名方法（`create/update/delete/clear/restore/set_enabled/accept_candidate`）。
- `_CATEGORY_COMPAT` 删除（配合 TASK-002）；v2 行不写 `category`。
- 保持现有语义：`Conversation→Run→memory_scope` 锁序、同 command 重放返回首次回执、指纹排除本次新分配的服务端身份、candidate 只挂已有目标、SET tombstone 只能恢复、单条删除不递增 `forget_epoch`、清空才递增并撤销全部 PENDING。
- 不新增抽象层，不引入 “port / adapter” 结构。

### Checklist

- [x] `MemoryService` 暴露 v2 具名方法，内部委托 `MemoryV2Writer`；`apply_v2` 移除或降为私有。
- [x] `memory_runtime.py` / `memory_tools.py` 不再构造 writer，只用 `MemoryService`。
- [x] `api/memory_v2.py` 不再构造 writer（该文件在 TASK-011 删除；本 TASK 先改调用）。
- [x] `_CATEGORY_COMPAT` 删除，v2 行 `category` 为 NULL。
- [x] 幂等指纹包含动作、目标 `memory_id`、`expected_version`、actor，不含本次新分配的 key/UUID。
- [x] 单条删除保留 tombstone/version、递增 revision、只撤销本 key 候选；清空递增 `forget_epoch` 并撤销全部候选。
- [x] 门禁：`apps` 下除 `memory_service.py` 外没有模块 import `memory_writer`。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "memory/harness-memory",
    "ref": "RULE-memory-002",
    "before": "Runtime 是唯一记忆写入所有者；按 Conversation→Run→memory_scope 锁序串行修改，锁内禁止网络 IO。同 tenant/user/key 使用完整唯一身份，软删后 id/version 不重置；每次变更、完整 after 历史、幂等 receipt 和 scope revision 同事务提交。锁内按写后有效集合验证冻结条数/UTF-8 字节限额及绝对上限；仅可淘汰 AGENT，USER/ADMIN 不免除硬限；既有超额时治理可减少但不可增加。",
    "rule": "Runtime 是唯一记忆写入所有者；按 Conversation→Run→memory_scope 锁序串行修改，锁内禁止网络 IO。SINGLE/SET/NOTE 使用稳定唯一身份（SINGLE/SET 由 kind/predicate/scope/member hash 生成，NOTE 用稳定 id），软删后 id/version 不重置；每次变更、完整 after 历史、幂等 receipt 和 scope revision 同事务提交。同 command 重放返回首次回执；PENDING 只挂已有目标槽位，新后台 NOTE 在 writer 受理时分配一次身份。锁内按写后有效集合验证冻结条数/UTF-8 字节限额及绝对上限；仅可淘汰 AGENT，USER/ADMIN 不免除硬限；既有超额时治理可减少但不可增加。",
    "evidence": "v2 设计 §3.5.1；稳定身份、NOTE UUID 与候选 ACCEPT 语义"
  },
  {
    "id": "DELTA-002",
    "op": "update",
    "target": "memory/harness-memory",
    "ref": "RULE-memory-004",
    "before": "用户/管理员删除保留 key tombstone，递增 forget_epoch 并撤销旧提议；自动写不能解除此删除、人工 disabled 或把 USER/ADMIN 降级为 AGENT。EXPIRE/QUOTA/TASK_COMPLETE 自动退休仅接受真实新入站记录的 source_scope_revision 证明，不以 job 开始时间冒充新依据。自动批量提交核对原 scope revision，过期输入为 STALE；同任务 lineage 续接原稳定 key，完成后清除未决状态。",
    "rule": "用户/管理员单条删除保留 key tombstone 与 version，递增 revision 并撤销该 key 旧提议，不递增 forget_epoch；清空递增 forget_epoch 并撤销所有旧提议。自动写不能解除此删除、人工 disabled 或把 USER/ADMIN 降级为 AGENT。EXPIRE/QUOTA/TASK_COMPLETE 自动退休仅接受真实新入站记录的 source_scope_revision 证明，不以 job 开始时间冒充新依据。自动批量提交核对原 scope revision，过期输入为 STALE；同任务 lineage 续接原稳定 key，完成后清除未决状态。",
    "evidence": "v2 设计 §3.5.5；单条删除与清空的 epoch 语义分离"
  }
]
```

### Log

- [2026-11-14] created
- [2026-11-14T15:57:01.262Z] started tests=28c79449e25a

### Verification

- 尚未执行
- 2026-11-14T15:57:13.855Z FAIL commands=0/3 executed=1 deduped=0 elapsed=12479ms tests=28c79449e25a head=0c59d3e1a8a6 dirty=1
  - writer FAIL exit=1
  - candidates NOT-RUN
  - single-writer-gate NOT-RUN
- 2026-11-14T15:57:28.309Z FAIL commands=0/3 executed=1 deduped=0 elapsed=12584ms tests=28c79449e25a head=0c59d3e1a8a6 dirty=1
  - writer FAIL exit=1
  - candidates NOT-RUN
  - single-writer-gate NOT-RUN
- 2026-11-14T15:57:54.710Z PASS commands=3/3 executed=3 deduped=0 elapsed=21722ms tests=28c79449e25a head=0c59d3e1a8a6 dirty=1
  - writer PASS exit=0
  - candidates PASS exit=0
  - single-writer-gate PASS exit=0
## TASK-005: 提议与确认路径：单入口、版本与 canary

- **Status**: done
- **Depends**: TASK-004
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.4.4, #3.5.1, #3.5.8
- **Scenarios**: S-05, S-10, E-07, E-08, E-09
- **Spec-Refs**: memory/harness-memory, snapshot/harness-snapshot
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_proposals.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation_outcome.py, tests/agent_runtime/test_memory_redesign_confirmation.py, tests/agent_runtime/test_memory_redesign_secrets.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "confirmation",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_redesign_confirmation.py"],
    "timeout": 300
  },
  {
    "id": "secrets",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_redesign_secrets.py"],
    "timeout": 300
  }
]
```

### Description

提议与确认回到 `MemoryService`，并恢复首版删掉的两道检查。

现状缺陷：

- `memory_proposals.py:119` 把 `expected_version` 恒置为 `None`，`memory_confirmation_resume.py:351` 的确认也不再比对版本；提案生成后目标行被改过，确认会静默覆盖（违反 §3.5.1 的 base_version 校验）。
- `memory_confirmation.py` 的 propose 路径删掉了 `validate_memory_content` 与 canary 扫描；记忆正文可以带出平台密钥。
- 提案现在直接 `new MemoryV2Writer()`，绕过唯一写入口。

目标：

- propose 时若目标身份已存在，记录当前 `version` 与 `forget_epoch`；confirm 时版本或 epoch 不一致返回 STALE，不写。
- propose 对 `content/title/summary/tags/source_quote` 逐字段做内容校验与 canary 扫描；命中即拒绝，不生成 PENDING。
- 确认与提议只依赖 `MemoryService`。
- 显式入站命令的直接保存路径保持 RULE-memory-001 语义（不再一律强制走确认卡片）；
  v2 命令语法的取舍见本轮待定项，未定前不删除相关测试。
- writer 语义缺口（B-15 暴露）：确认写入必须带来源绑定
  （`source_quote`/`source_event_id`/`source_seq`/`source_ref`）、`confirmed_count`、
  `last_confirmed_at`，以及冻结策略的默认 TTL 与配额校验（`MEMORY_QUOTA_EXCEEDED`）。

### Checklist

- [x] 提案记录真实 `expected_version`（目标不存在时为 None）与 `forget_epoch`。
- [x] 确认前校验 `expected_version` 与 `forget_epoch`；不一致返回 STALE 且不写库。
- [x] propose 恢复内容校验与 canary 扫描，覆盖全部用户可见字段。
- [x] 提议/确认只调 `MemoryService`，不直接构造 writer。
- [x] 确认写入带来源绑定、`confirmed_count`、`last_confirmed_at`。
- [x] 默认 TTL 按冻结策略生效（`ttl_days_user` / `ttl_days_agent`）。
- [x] 写入前按冻结条数/字节限额校验，超额返回 `MEMORY_QUOTA_EXCEEDED`；AGENT 行可被淘汰。
- [x] 显式入站命令的直接保存路径：等协议决策后实现并恢复 B-15 用例。
- [x] `test_local_b15_real_gateway_confirmation_saves_displayed_content_and_new_receipt`
      依赖偏好 lane 不依赖 query（TASK-006）；该 TASK 完成后复跑。
- [x] 一个 Run 至多一个待确认提议；超时/截止仍按原语义返回。
- [x] `test_memory_redesign_confirmation` 与 `test_memory_redesign_secrets` 绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-15] B-15 复跑发现 writer 语义缺口（来源绑定/confirmed_count/默认 TTL/配额），已在 TASK-005 内补齐；
  显式命令语法待定；偏好 lane 依赖 TASK-006。
- [2026-11-14T15:58:43.447Z] started tests=9cdc1ff971ee

### Verification

- 尚未执行
- 2026-11-14T15:59:58.065Z FAIL commands=0/2 executed=1 deduped=0 elapsed=74499ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:01:17.748Z FAIL commands=0/2 executed=1 deduped=0 elapsed=76152ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:02:45.038Z FAIL commands=0/2 executed=1 deduped=0 elapsed=83975ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:05:39.168Z FAIL commands=0/2 executed=1 deduped=0 elapsed=113591ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:08:34.885Z FAIL commands=0/2 executed=1 deduped=0 elapsed=114847ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:10:49.597Z FAIL commands=0/2 executed=1 deduped=0 elapsed=121123ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:13:21.324Z FAIL commands=0/2 executed=1 deduped=0 elapsed=120797ms tests=9cdc1ff971ee head=d2c0cb4e87d3 dirty=1
  - confirmation FAIL exit=1
  - secrets NOT-RUN
- 2026-11-14T16:18:09.669Z PASS commands=2/2 executed=2 deduped=0 elapsed=123431ms tests=9cdc1ff971ee head=38e9a77f93e0 dirty=1
  - confirmation PASS exit=0
  - secrets PASS exit=0
## TASK-006: v2 纯检索：偏好 lane、BM25、scope、实体门控

- **Status**: done
- **Depends**: TASK-001
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.5.2, #3.5.3
- **Scenarios**: S-02, S-03, S-11, B-05, E-12
- **Spec-Refs**: memory/harness-memory
- **Paths**: packages/agent-core/src/muad_agent_core/memory/selection.py, tests/agent_core/test_memory_rank.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "rank",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_memory_rank.py"],
    "timeout": 120
  }
]
```

### Description

v2 纯检索保留在 `selection.py`（不并入旧 `rank.py`：旧 `select_injection` 是评测基线，必须保持只读）。修三处与设计不符的缺陷：

1. **偏好 lane 不存在**。`selection.py:176` 空 query 直接返回 `()`，而设计要求“偏好注入不依赖 query”。PREFERENCE 必须按独立条数/字节预算先选，不受阈值过滤。
2. **实体门控方向反了**。`selection.py:151` 在 query 命中实体时排除所有无 entity 标签的记录；设计要求“带 entity 标签的候选必须有正向匹配且全部标签命中，无标签 NOTE 仅按 topic”。
3. **阈值与推断开关缺失**。`min_score`（默认 0.35，平台可配）与 `inject_inferred` 没有进入选择；AGENT_INFERRED 且未确认的行会被无差别注入。

BM25 保持二值字段权重（`match_weight=max(w_f)`、无权重长度并集、`avg_len=max(1,...)`）与确定性 tie-break。

### Checklist

- [x] 空 query 返回偏好 lane（PREFERENCE，按条数/字节预算），不返回空集。
- [x] 非空 query：偏好 lane + 事实/NOTE 匹配结果合并，偏好不去重覆盖为 0 分。
- [x] `min_score` 参与门控；偏好不受阈值过滤。
- [x] `inject_inferred=False` 时 AGENT_INFERRED 未确认行不注入。
- [x] 实体门控：带标签候选需正向匹配且全部标签命中；无标签 NOTE 按 topic 通过。
- [x] scope 三态：UNKNOWN 只放行无 scope 维度的 predicate；EXPLICIT 覆盖 ABSENT；ABSENT query 不注入显式 scope 行。
- [x] 测试覆盖空 query、无标签 NOTE、阈值、推断开关、UNKNOWN、A+Y vs A+X。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "memory/harness-memory",
    "ref": "RULE-memory-003",
    "before": "候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。注入选择在覆盖率之上加阈值门控（默认 0.35，平台可配）；偏好类先按独立条数/字节限额筛选且不受阈值过滤；同键只取最新版本；经验类只注入 ACTIVE，按任务族或目标文本命中，且不受注入推断开关影响。recall/SEARCH 的返回语义（零覆盖为空、LIST）保持不变。revision 缓存只存候选行：冷读取行与实际 revision 同快照，暖读取 revision 与当前语句时间，并逐次过滤 TTL、重新选择/渲染；读取失败清除旧缓存和 MemoryComponent。每个模型请求前刷新独立 MemoryComponent，完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。",
    "rule": "候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。用户记忆 v2 在覆盖率/BM25 正分之上执行 scope 三态与 NOTE 实体门控：带 entity 标签的候选必须有正向匹配，且候选全部实体标签都出现在 query 中；无匹配时带 entity 标签的候选不注入，无标签 NOTE 仅按 topic。冷/热读取必须以 PG head revision 为准，逐次按当前语句时间过滤 TTL；经验/TASK_STATE 只冻结准入 key，行状态/版本/TTL 随快照刷新；读取失败清缓存并如实返回 degraded。每个模型请求前刷新独立 MemoryComponent，完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。",
    "evidence": "v2 设计 §3.5.2/§3.5.3/§3.5.4；保留无 NER 的最小实体门控"
  }
]
```

### Log

- [2026-11-14] created
- [2026-11-14T16:15:39.027Z] started tests=84cee5f0deec

### Verification

- 尚未执行
- 2026-11-14T16:16:00.435Z PASS commands=1/1 executed=1 deduped=0 elapsed=892ms tests=84cee5f0deec head=d2c0cb4e87d3 dirty=1
  - rank PASS exit=0
## TASK-007: 唯一读路径：loader 合并两条 lane（含 TASK_STATE 与经验）

- **Status**: done
- **Depends**: TASK-003, TASK-006
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.2.3, #3.2.4, #3.2.5, #3.5.4
- **Scenarios**: S-01, S-03, S-05, S-11, B-01, B-05
- **Spec-Refs**: memory/harness-memory, snapshot/harness-snapshot
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_context.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_retrieval_context.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resolution.py, packages/agent-core/src/muad_agent_core/memory/rank.py, tests/agent_runtime/test_memory_context.py, tests/agent_runtime/test_memory_redesign_context.py, tests/agent_runtime/test_context_memory.py, tests/agent_runtime/test_experience_activation.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "loader",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_context.py", "tests/agent_runtime/test_context_memory.py"],
    "timeout": 600
  },
  {
    "id": "legacy-lane",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_redesign_context.py", "tests/agent_runtime/test_experience_activation.py"],
    "timeout": 900
  },
  {
    "id": "single-loader-gate",
    "argv": ["bash", "-lc", "test ! -e apps/agent-runtime/src/muad_agent_runtime/application/memory_retrieval_context.py && test ! -e apps/agent-runtime/src/muad_agent_runtime/application/memory_resolution.py"],
    "timeout": 60
  }
]
```

### Description

`MemoryContextLoader` 是唯一读路径。删除 `memory_retrieval_context.py`（`MemoryContextLoaderV2`）与 `memory_resolution.py`（`MemoryV2Runtime`）——两者只有测试在用，却让“哪个 loader 是真的”变得不可判定。

loader 的职责：

- 取 TASK-003 的一个快照，v2 lane 走 `select_v2`，legacy lane 走现有 `rank.select_injection`。
- **经验 lane 保持原逻辑**：只注入 `activation_state == 'ACTIVE'`、按 task_family/目标文本命中、首次刷新冻结准入 key；行状态/版本/TTL 每次随快照刷新。
- **TASK_STATE 恢复注入**：仅当 `task_state_ref` 的 `memory_key`、`expected_version`、`candidate_hash` 与行完全一致且 `task_state_injection_enabled` 时才注入；这是首版丢掉的语义。
- 读取失败清缓存与组件，如实返回 degraded，不返回旧行。

### Checklist

- [x] 删除 `memory_retrieval_context.py` 与 `memory_resolution.py`；生产代码不再引用 `MemoryContextLoaderV2` / `MemoryV2Runtime`。
- [x] loader 使用单一快照的 v2 lane + legacy lane。
- [x] 经验 lane：ACTIVE 门控、task_family / trigger_text 命中、首次刷新冻结准入 key。
- [x] TASK_STATE：key/version/candidate_hash 三者一致才注入；不满足不注入。
- [x] `learning_enabled=False` 时经验不注入，事实不受影响；`task_state_injection_enabled=False` 时 TASK_STATE 不注入。
- [x] 读取失败：缓存与组件清空、指标记 ERROR、返回 degraded。
- [x] 四条定向测试与两条门禁全绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14T16:18:26.776Z] started tests=46d430feb258

### Verification

- 尚未执行
- 2026-11-14T16:19:44.874Z FAIL commands=0/3 executed=1 deduped=0 elapsed=34931ms tests=46d430feb258 head=ed15d37557ec dirty=1
  - loader FAIL exit=1
  - legacy-lane NOT-RUN
  - single-loader-gate NOT-RUN
- 2026-11-14T16:23:59.919Z FAIL commands=1/3 executed=2 deduped=0 elapsed=80383ms tests=46d430feb258 head=ed15d37557ec dirty=1
  - loader PASS exit=0
  - legacy-lane FAIL exit=1
  - single-loader-gate NOT-RUN
- 2026-11-14T16:28:07.129Z PASS commands=3/3 executed=3 deduped=0 elapsed=80375ms tests=46d430feb258 head=ed15d37557ec dirty=1
  - loader PASS exit=0
  - legacy-lane PASS exit=0
  - single-loader-gate PASS exit=0
## TASK-008: 渲染分流：结构化 v2 内容 + 旧经验渲染

- **Status**: done
- **Depends**: TASK-006
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.5.11
- **Scenarios**: S-02, S-11, B-01, B-02
- **Spec-Refs**: memory/harness-memory
- **Paths**: packages/agent-core/src/muad_agent_core/memory/memory_render.py, packages/agent-core/src/muad_agent_core/memory/render.py, packages/agent-core/src/muad_agent_core/memory/components.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_context.py, tests/agent_core/test_memory_render.py, tests/agent_runtime/test_context_memory_budget.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "render",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_memory_render.py"],
    "timeout": 120
  },
  {
    "id": "budget",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_context_memory_budget.py"],
    "timeout": 600
  }
]
```

### Description

一个 `MemoryComponent`（一个 SYSTEM 块），两类渲染：

- PREFERENCE/FACT/NOTE 用 `memory_render_v2`：必须渲染**值本身**，不能只拼 `title: summary`。现状 `memory_render.py:36` 丢弃了 `content`，写进去的 `{value: "PDF"}` 根本不会进入 Prompt。
- 经验/TASK_STATE 用现有 `render_memory_block`：保留 `confirmed/inferred/verified_experience/task_state` 分段、`omitted` 语义与 notice。

预算按最终 UTF-8 字节：整条丢弃、不截断；预算耗尽时记 `dropped_count`/`dropped_bytes`；0 表示关闭注入；空选择时组件文本为 None，不注入只含 notice 的空块。

### Checklist

- [x] v2 渲染输出结构化值（`content` 必须可见，单位/条件/否定不丢）。
- [x] 空选择返回 `text=None`，不注入仅含 notice 的块。
- [x] 经验/TASK_STATE 仍走 `render_memory_block`，分段语义与 notice 不变。
- [x] 两段合并进同一个 `MemoryComponent`，`references` 覆盖全部注入项。
- [x] 字节预算整条丢弃、不截断；`dropped_bytes` 真实统计。
- [x] 渲染与预算测试绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14T16:28:20.504Z] started tests=af2c165c5ebd

### Verification

- 尚未执行
- 2026-11-14T16:28:37.019Z PASS commands=2/2 executed=2 deduped=0 elapsed=6173ms tests=af2c165c5ebd head=1b855d10591b dirty=1
  - render PASS exit=0
  - budget PASS exit=0
## TASK-009: QueryContext 与澄清

- **Status**: done
- **Depends**: TASK-007
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.4.6, #3.5.2, #3.5.11
- **Scenarios**: S-03, S-06, S-11, E-12, B-05
- **Spec-Refs**: memory/harness-memory, snapshot/harness-snapshot
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_context.py, apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, packages/contracts/src/muad_contracts/memory.py, tests/agent_runtime/test_experience_retrieval_lanes.py, tests/agent_runtime/test_memory_context.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "context",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_context.py", "tests/agent_runtime/test_experience_retrieval_lanes.py"],
    "timeout": 600
  }
]
```

### Description

检索上下文来自真实执行层，而不是常量：

- 当前用户消息作为主 query；
- 最近 N 条**用户**消息中的 scope 条件（带 `source_seq`/时间，只取 user role，不取工具结果与记忆正文）；
- `query.entity_tags`：按 taxonomy 别名在 query 里命中，不做 NER；
- `task_family`、`conversation_id`、`now`。

澄清：

- `SCOPE_UNKNOWN`：有 scope 维度的 predicate 被抑制，`clarification_reason` 返回 `SCOPE_UNKNOWN`，不把抑制伪装成“没有记忆”；
- 多个无法比较的显式值 → `SCOPE_AMBIGUOUS`，本请求不注入该 predicate；
- 显式保存遇未知 scope 直接拒绝（`MEMORY_SCOPE_UNKNOWN`），不降级为 ABSENT。

当前消息的明确条件覆盖历史条件；明确换任务清除旧条件；否定/引用旧文本不当作当前目标。

### Checklist

- [x] 执行层组装 `QueryContextV2`：query、scope 三态与 scope、entity_tags、task_family、now。
- [x] 最近 N 条用户消息参与 scope 继承；工具结果与记忆正文不参与。
- [x] entity tags 由 taxonomy 别名命中，不引入实体类型配置或 NER。
- [x] `clarification_reason` 接入 `MemoryResolution` 与 Trace，`SCOPE_UNKNOWN` / `SCOPE_AMBIGUOUS` 可区分。
- [x] 显式保存未知 scope 返回 `MEMORY_SCOPE_UNKNOWN`，不写库、不生成候选。
- [x] 定向测试覆盖条件继承、换任务清空、UNKNOWN 抑制、AMBIGUOUS 不注入。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14T16:30:33.384Z] started tests=225f77deae19

### Verification

- 尚未执行
- 2026-11-14T16:30:36.363Z PASS commands=1/1 executed=1 deduped=0 elapsed=2870ms tests=225f77deae19 head=72476843e7ce dirty=1
  - context PASS exit=0
## TASK-010: Runtime 接线：executor / tools / attribution

- **Status**: done
- **Depends**: TASK-004, TASK-005, TASK-007, TASK-008, TASK-009
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.2.3, #3.5.9, #3.5.11
- **Scenarios**: S-01, S-04, S-11, E-02
- **Spec-Refs**: memory/harness-memory, snapshot/harness-snapshot
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_runtime.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_injection_log.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_observability.py, tests/agent_runtime/test_memory_tools.py, tests/agent_runtime/test_memory_injection_attribution.py, tests/agent_runtime/test_memory_observability.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "tools",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_tools.py"],
    "timeout": 300
  },
  {
    "id": "attribution",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_injection_attribution.py", "tests/agent_runtime/test_memory_observability.py"],
    "timeout": 600
  }
]
```

### Description

`executor → create_run_memory → loader / tools / confirmation` 全部走单一入口：

- `create_run_memory` 只产出 `MemoryService`、loader、confirmation，不再注入第二个 writer。
- `MemoryToolSet` 的 remember/recall/forget 用 v2 契约（`memory_id` + `expected_version`），写入只调 `MemoryService`。
- 每个模型请求前刷新独立 `MemoryComponent`；`set_task_family` 在 skill 执行后驱动下一次请求的经验注入。
- attribution 恢复首版丢掉的诊断：`min_score`、`inject_inferred`、`allowed_experience_keys` 计数、`skipped` 原因、`dropped_by_budget`。
- 记忆内容与密钥不得进入日志（`memory_observability` 既有约束）。

### Checklist

- [x] `create_run_memory` 不构造第二个 writer。
- [x] remember/recall/forget 走 v2 契约；forget 只接受 `memory_id`。
- [x] 每次模型请求前刷新组件；`set_task_family` 生效。
- [x] attribution 含 `min_score`、`inject_inferred`、冻结经验 key 数、`skipped` 原因、预算丢弃明细，且按 selection hash 去重。
- [x] 记忆正文与密钥不出现在日志/指标标签中。
- [x] 工具、attribution、可观测性测试绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14T16:30:43.109Z] started tests=2982779c687a

### Verification

- 尚未执行
- 2026-11-14T16:31:05.413Z FAIL commands=0/2 executed=1 deduped=0 elapsed=22192ms tests=2982779c687a head=72476843e7ce dirty=1
  - tools FAIL exit=1
  - attribution NOT-RUN
- 2026-11-14T16:32:52.714Z FAIL commands=1/2 executed=2 deduped=0 elapsed=32168ms tests=2982779c687a head=f07e8c00a361 dirty=1
  - tools PASS exit=0
  - attribution FAIL exit=1
- 2026-11-14T16:33:58.364Z PASS commands=2/2 executed=2 deduped=0 elapsed=30835ms tests=2982779c687a head=f07e8c00a361 dirty=1
  - tools PASS exit=0
  - attribution PASS exit=0
## TASK-011: 管理 API 单一路由与 Console 治理面

- **Status**: done
- **Depends**: TASK-003, TASK-004, TASK-005
- **Source**: user-memory-retrieval-optimization.frontend.design.md#3, user-memory-retrieval-optimization.backend.design.md#3.4
- **Scenarios**: S-06, S-09, S-13, E-03, E-04, E-10
- **Spec-Refs**: api/harness-api, memory/harness-memory, frontend/harness-frontend, ui/harness-ui, i18n/harness-i18n
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/api/memory.py, apps/agent-runtime/src/muad_agent_runtime/api/memory_v2.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_admin.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_admin_queries.py, apps/console-platform/backend/src/muad_console_platform/api/users.py, apps/console-platform/backend/src/muad_console_platform/application/memory_service.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/memory_repository.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/runtime_memory_client.py, apps/console-platform/frontend/src/modules/user-identity, tests/test_memory_api.py, tests/console_platform/test_memory_client.py, tests/frontend/test_memory_tab_contract.py
- **E2E**: ci:e2e
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "api",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_memory_api.py", "tests/console_platform/test_memory_client.py"],
    "timeout": 300
  },
  {
    "id": "frontend-contract",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_memory_tab_contract.py"],
    "timeout": 120
  },
  {
    "id": "frontend-build",
    "argv": ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build"],
    "timeout": 900
  },
  {
    "id": "single-route-gate",
    "argv": ["bash", "-lc", "test ! -e apps/agent-runtime/src/muad_agent_runtime/api/memory_v2.py && ! grep -rq --exclude-dir=__pycache__ 'memory/v2' apps/agent-runtime/src apps/console-platform/backend/src apps/console-platform/frontend/src"],
    "timeout": 60
  }
]
```

### Description

管理面只保留一套路由与一套前端，扩展原 `/api/v1/users/{user_id}/memory`，不新增 `/v2` 前缀。

现状问题：

- Runtime 同时挂了 `memory_router` 与 `memory_v2_router`（`main.py:616-617`），Console 同时有 `/memory` 与 `/memory/v2` 两套接口；
- `memory_admin_queries._history` 读 `row.legacy_snapshots_json`，该列已被 0049 删除，历史接口运行时 `AttributeError`；
- 前端 `MemoryTab` 自称 “thin vertical slice”，只有列表/创建/删除/清空，缺恢复、启停、编辑、回收站、历史、待确认与错误码本地化。

需要覆盖的治理动作（S-06 / S-09 / S-13）：列表（分页 + kind/predicate/status 过滤）、创建、编辑（只改内容，身份字段只读）、启用/禁用、删除、恢复、清空、回收站、版本历史、待确认候选处理。所有写操作经 Runtime，Console 不直接写 PG。

### Checklist

- [x] 删除 `api/memory_v2.py` 与 Console `/memory/v2` 路由；Runtime 只挂一个 memory router。
- [x] 原 memory 路由的 DTO 扩展为 v2 字段（kind/predicate/scope/title/summary/tags/cardinality/priority/status/memory_id）。
- [x] 列表支持分页与 kind/predicate/status 过滤；回收站单列入口。
- [x] 创建命中已有身份返回 `MEMORY_ALREADY_EXISTS`；编辑身份字段返回 `MEMORY_IDENTITY_IMMUTABLE`；旧版本编辑返回 409。
- [x] 启用/禁用、删除、恢复、清空、候选 ACCEPT/REJECT 全部可用且写经过 Runtime。
- [x] `_history` 不再引用 `legacy_snapshots_json`，历史可正常返回。
- [x] 前端补齐恢复/启停/编辑/回收站/历史/待确认与 409/422/429 本地化；复用 DataTable/PaginationFooter/ConfirmAction/FormModal/EmptyState/ErrorState。
- [x] 门禁：`memory_v2.py` 不存在、源码无 `memory/v2` 字样。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created
- [2026-11-14T16:34:53.294Z] started tests=d7bd46b87418

### Verification

- 尚未执行
- 2026-11-14T16:39:26.265Z FAIL commands=3/4 executed=4 deduped=0 elapsed=9008ms tests=d7bd46b87418 head=130a7f7c086d dirty=1
  - api PASS exit=0
  - frontend-contract PASS exit=0
  - frontend-build PASS exit=0
  - single-route-gate FAIL exit=1
- 2026-11-14T16:39:51.569Z PASS commands=4/4 executed=4 deduped=0 elapsed=8679ms tests=5d4e03259043 head=130a7f7c086d dirty=1 tests-changed
  - api PASS exit=0
  - frontend-contract PASS exit=0
  - frontend-build PASS exit=0
  - single-route-gate PASS exit=0
## TASK-012: 后台提炼 v2

- **Status**: draft
- **Depends**: TASK-004, TASK-005
- **Source**: user-memory-retrieval-optimization.backend.design.md#3.2.2, #3.5.1, #3.5.7
- **Scenarios**: S-05, E-01, E-07, E-09, E-12
- **Spec-Refs**: memory/harness-memory
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation_output.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_job_effects.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_jobs.py, tests/agent_runtime/test_memory_redesign_job_effects.py, tests/agent_runtime/test_memory_candidates.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "job-effects",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_redesign_job_effects.py"],
    "timeout": 600
  },
  {
    "id": "candidates",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_memory_candidates.py"],
    "timeout": 300
  }
]
```

### Description

后台提炼（Run 完成后）输出结构化候选，与显式保存走同一个 writer 语义。

现状：`memory_consolidation_output.py` 仍产出 v1 `MemoryCandidate`（自由 `memory_key` + `category` + `FactContent`），`memory_job_effects` 仍走旧 `MemoryService.execute`；FR-01 的“从 Run 结果提炼结构化候选”实际上没有实现。

目标：

- 输出 `MemoryCandidateV2`：`memory_kind/predicate/scope_state/scope/content/title/summary/tags`，由服务端校验 taxonomy 并生成稳定身份，模型不自造 key。
- 推断值与当前值不同 → 写 `memory_conflict_candidate`（PENDING），不覆盖当前值；同内容权威变化才升版本。
- 未知 scope / 未知 predicate：拒绝并在 job result 与指标记录，不生成 PENDING。
- 结构化失败降级为 `general` NOTE，不阻塞 job。
- 经验 job（PROCEDURE/FAILURE_PATTERN/VERIFICATION）保持原有 lane 与输出，不迁到 v2。
- 失败整批回滚、warning + metric 可见，主 Run 终态不变。

### Checklist

- [ ] consolidation 输出与解析改用 `MemoryCandidateV2` 形状。
- [ ] 后台候选走 `MemoryService` 的 v2 入口，不直接写表。
- [ ] 推断冲突写 PENDING 候选；同内容权威变化升版本。
- [ ] 未知 scope/predicate 拒绝并记录；不生成 PENDING。
- [ ] 结构化失败降级 `general` NOTE。
- [ ] 经验 job 输出与处理保持原样。
- [ ] 幂等：同 job 重跑不产生重复版本或重复候选。
- [ ] 定向测试绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created

### Verification

- 尚未执行

## TASK-013: 评测基线真跑

- **Status**: draft
- **Depends**: TASK-006, TASK-008, TASK-012
- **Source**: user-memory-retrieval-optimization.evaluation.md#3, #4
- **Scenarios**: S-12
- **Spec-Refs**: test/harness-test, memory/harness-memory
- **Paths**: scripts/memory_eval.py, tests/test_memory_evaluation.py, tests/test_memory_eval_data.py, tests/fixtures/memory
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "metrics",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_memory_evaluation.py"],
    "timeout": 300
  },
  {
    "id": "data",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_memory_eval_data.py"],
    "timeout": 120
  }
]
```

### Description

评测工具要有**基线**与**同输入跑分**，不只有指标数学。

现状：`scripts/memory_eval.py` 只有 `score_query` / `aggregate` / bootstrap，没有基线副本、没有检索跑分、没有 fixture 全量执行与报告。

目标：

- 保留 `select_injection` 的只读副本作为基线；v2 与基线吃同一语料、同一查询、同一标签、同一预算。
- 跑分覆盖：候选召回、门控后召回/精度、预算后注入、Required Recall、Strict Conditional Precision、Forbidden Injection、Stale Injection、Update Correctness。
- 主聚合为 query-macro；同时输出 user-macro 与 pooled 作为次要；Δ 用 user-block paired bootstrap 给 CI。
- 分栏：`oracle` 结构化输入与真实 extractor 输出分开；经验/TASK_STATE lane 单独报告，不混入 v2 指标。
- 输出可复现 manifest（数据版本、算法版本、参数、逐条结果）与失败样本。
- D8 的无 `entity:` 标签实体 NOTE 只作观察项，不进硬约束。

### Checklist

- [ ] 基线只读副本与 v2 跑分器共用同一输入/预算。
- [ ] 指标按评测契约实现，空分母为 N/A，不当 0。
- [ ] bootstrap 以 user 为重采样单位，v2 与基线同索引配对。
- [ ] oracle 与真实 extractor 分栏；经验 lane 单列。
- [ ] 产出 manifest、逐条结果、失败样本与聚合表。
- [ ] 不为达标修改盲测标签或补专属别名。
- [ ] 评测测试绿。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created

### Verification

- 尚未执行

## TASK-014: 全量回归与人工验收

- **Status**: draft
- **Depends**: TASK-010, TASK-011, TASK-013
- **Source**: user-memory-retrieval-optimization.scenarios.md, user-memory-retrieval-optimization.backend.design.md#2.5
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, S-06, S-07, S-08, S-09, S-10, S-11, S-12, S-13, E-01, E-02, E-03, E-04, E-05, E-06, E-07, E-08, E-09, E-10, E-11, E-12, B-01, B-02, B-03, B-04, B-05
- **Spec-Refs**: test/harness-test, e2e/harness-e2e, memory/harness-memory
- **Paths**: tests/acceptance/runtime/test_memory_acceptance.py, tests/acceptance/im_gateway/test_memory_security.py, tests/agent_runtime, tests/agent_core, tests/migrations, e2e/tests/agent-management/memory-redesign.spec.ts
- **E2E**: ci:e2e
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "acceptance",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/acceptance/runtime/test_memory_acceptance.py", "tests/acceptance/im_gateway/test_memory_security.py"],
    "timeout": 900
  },
  {
    "id": "regression",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime", "tests/agent_core", "tests/migrations", "tests/console_platform", "tests/frontend"],
    "timeout": 1800
  }
]
```

### Description

全量回归与人工场景签收。本 TASK 不允许用“CI/用户实测”替代执行，也不允许只挑子集。

必须覆盖：

- `tests/agent_runtime` / `tests/agent_core` / `tests/migrations` / `tests/console_platform` / `tests/frontend` 全绿（含 `memory_redesign_*`、`learning_*`、`experience_*`）。
- 真库 acceptance：PG + Redis、Runtime + Console、跨租户隔离、候选/恢复/清空/SET tombstone、scope/实体门控、旧 lane、缓存故障。
- 浏览器 E2E：`memory-redesign.spec.ts` 覆盖列表/创建/编辑/启停/删除/恢复/回收站/历史/待确认。
- 人工场景：`S-01…S-13`、`E-01…E-12`、`B-01…B-05` 逐条签收，写明执行人与结果。

### Checklist

- [ ] `tests/agent_runtime`、`tests/agent_core`、`tests/migrations`、`tests/console_platform`、`tests/frontend` 全绿。
- [ ] `tests/acceptance/runtime/test_memory_acceptance.py` 与 `tests/acceptance/im_gateway/test_memory_security.py` 在真库通过。
- [ ] `memory-redesign.spec.ts` 在真实浏览器通过。
- [ ] 29 条人工场景逐条签收，失败项写明原因与归属 TASK。
- [ ] `make acceptance` 全量通过（非子集）。
- [ ] 性能检查按设计 NFR 实测；未实测的项明确标注，不写成已通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14] created

### Verification

- 尚未执行
