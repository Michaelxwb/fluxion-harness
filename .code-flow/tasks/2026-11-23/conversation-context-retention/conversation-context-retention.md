---
{"version":1,"requirement":"conversation-context-retention"}
---
# Tasks: 会话上下文保留

- **Created**: 2026-11-25
- **Workflow**: full
- **Inputs**: [PRD](conversation-context-retention.prd.md)、[后端设计](conversation-context-retention.backend.design.md)、[前端设计](conversation-context-retention.frontend.design.md)、[人工验收场景](conversation-context-retention.scenarios.md)
- **Plan Status**: 待任务边界、Spec-Refs、Scenarios、Tests 复核；所有 TASK 为 draft，未开始实现。

## 目标与范围

- `compaction` 收敛为 4 个叶子；删除 `snip`、`micro`、条数、回合、预览头尾等固定选择/截断口径。
- 历史装配只读、按 `history_budget_tokens` 分页取数；只有请求边界做一次保头保尾裁剪。
- 摘要只处理 `covered_seq..material_end_seq` 的连续材料；替换只移除材料，保留头部/独立尾部/pinned，coverage 不覆盖当前输入。
- 整轮工具结果外置预算与预览由已有设置/历史预算派生，不新增配置叶子。
- Runtime 与 Worker 共用严格冻结解析和请求边界裁剪；“继续”不再短路或自动绑定单候选。
- 旧键、旧 policy、旧摘要、旧平台设置不保留兼容解析；开发阶段按 runbook 一次性重置。

**明确不做**：

- 不做旧格式读取、双写、迁移或反向迁移；
- 不新增模型级上下文窗口声明；
- 不保留 `snip`/`micro` 行为；
- 不新增 Console 会话视图裁剪横幅；
- 不在本需求内精简其他设置分组。

## 执行与验证约定

- 一个需求一份任务文档；按本文件路径 + 明确 TASK ID 绑定，不从状态猜当前任务。
- 每个 TASK 只声明有意义的 unit/integration 回归；`task finish` 只跑本 TASK 声明的测试，不跑全量 acceptance/E2E。
- 数据库相关测试使用项目隔离的临时 PostgreSQL/Redis；不共用 dev 库，不残留共享服务进程。
- 人工浏览器验收按 `conversation-context-retention.scenarios.md` 执行，E2E 不进 CI。
- `Spec-Refs` 相对 `.code-flow/specs`，不带扩展名；实施前读当前 Spec 与本 TASK 的 Delta。
- `snapshot/harness-snapshot` 中旧的 `max_history_tool_rounds` 是 Convention，不是 Rule；新 Rule 由 TASK-001 的 Spec Delta 沉淀，旧 Convention 由 TASK-001 人工同步改写。
- 最后一个 TASK finish 后停下，等待用户完整功能验收与代码审查；确认前不 archive，不声称完成。

## Task Index

| TASK | 可验证能力 | Depends |
|---|---|---|
| [TASK-001](#task-001) | 4 叶契约 + 严格冻结解析 | — |
| [TASK-002](#task-002) | Runtime 历史装配只读 + 索引 | TASK-001 |
| [TASK-003](#task-003) | 单次裁剪 + 摘要材料/替换 + 审计 | TASK-002 |
| [TASK-004](#task-004) | 整轮工具结果外置/预览派生 | TASK-001 |
| [TASK-005](#task-005) | Worker 压缩对齐 | TASK-001, TASK-003, TASK-004 |
| [TASK-006](#task-006) | “继续”进入模型回合 | TASK-002, TASK-003 |
| [TASK-007](#task-007) | Console 4 字段 + 裁剪审计 | TASK-001, TASK-003 |
| [TASK-008](#task-008) | 重置 runbook + 配置/需求 inventory | TASK-001..TASK-007 |

<a id="task-001"></a>

## TASK-001: 压缩配置收敛与严格冻结解析

- **Status**: done
- **Depends**:
- **Source**: conversation-context-retention.backend.design.md §1 D1/D2/D8、§2.1；conversation-context-retention.prd.md FEAT-01
- **Scenarios**: E-01, B-02
- **Spec-Refs**: snapshot/harness-snapshot, api/harness-api, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/platform_settings.py, apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/continuation_execution.py, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_service.py, .code-flow/specs/snapshot/harness-snapshot.md, tests/contracts/test_platform_settings.py, tests/agent_runtime/test_context_compaction_config.py, tests/agent_runtime/test_platform_settings_source.py, tests/test_platform_settings_schema.py, tests/console_platform/test_platform_settings_api.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "settings-contract",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/contracts/test_platform_settings.py",
      "tests/agent_runtime/test_context_compaction_config.py",
      "tests/agent_runtime/test_platform_settings_source.py",
      "tests/test_platform_settings_schema.py",
      "tests/console_platform/test_platform_settings_api.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `CompactionSettings` 只保留 `history_budget_tokens`、`summary.enabled`、`summary.model_ref`、`tool_result.persist_threshold_bytes`；从平台设置文档删除 `snip.*`、`micro.*`、`history_budget_messages`、`max_history_tool_rounds`、`summary.threshold_bytes`、`tool_result.round_budget_bytes`、`preview_head_bytes`、`preview_tail_bytes`。压缩流水线里的 `snip`/`micro` 函数与类由 TASK-003 删除。
- 新增 `history_budget_tokens` 正整数/上限校验；保存和 Run/Task 准入同时受 `HISTORY_BUDGET_TOKENS_MAX` 与部署最小窗口约束。
- 冻结解析严格：缺键、未知键、旧键显式 `CompactionConfigError`，不回落默认值；Runtime `compaction_settings_of` 不再返回 `None`，旧的 `history_budget_of` / `history_tool_rounds_of` 删除。
- 新 Run/Task 快照仍由平台快照 + Agent `runtime_config.budget.compaction` 合并后冻结；旧键不兼容。
- Console catalog 派生 4 个字段并删除旧字段；不新增配置叶子。
- 同步改写 `snapshot/harness-snapshot` 中 `max_history_tool_rounds` 旧 Convention；该条不是 Rule，由本 TASK 人工同步。

### Checklist

- [x] 4 个叶子的 schema、默认值、范围与跨字段校验落地。
- [x] `snip` / `micro` 字段从 `CompactionSettings` 与平台设置文档删除；压缩流水线类/函数由 TASK-003 删除，旧键解析显式失败。
- [x] `parse_frozen_compaction_settings`（或等价严格入口）被 Runtime 使用；TASK-005 让 Worker 复用同一入口。
- [x] Runtime 冻结路径不再使用旧 `history_budget_messages` / `max_history_tool_rounds` 辅助。
- [x] Console catalog 只剩 4 个字段，旧字段不再下发。
- [x] 部署最小窗口上限校验在保存和准入路径生效。
- [x] `snapshot/harness-snapshot` 的旧 `max_history_tool_rounds` Convention 同步改写为 token 预算口径。
- [x] 新增/更新测试覆盖旧键拒绝、缺键严格失败、范围校验和 Agent 覆盖。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "上下文压缩以 Run/Task 创建时冻结的 history_budget_tokens 作为唯一历史预算；装配只读且按预算分页读取，只有请求边界做一次保头保尾裁剪，任何未进入请求的历史必须带可见省略标记与审计。摘要只处理从 covered_seq 起的连续材料区间，替换只移除该材料区间并保留头部、独立尾部与 pinned；coverage 只推进到实际材料末条，不覆盖当前输入。冻结压缩配置严格解析，旧键、旧 policy、旧摘要和旧平台设置不保留兼容解析。",
    "evidence": "conversation-context-retention PRD v0.5 / backend design v0.5 §1 D1-D8、§2-§4；scenarios S-01/E-02/E-03/B-01/B-02；TASK-001..TASK-008 回归"
  }
]
```

### Log

- [2026-11-25] created；待任务边界复核。
- [2026-11-24T16:29:11.256Z] started tests=98172a3d1ae2

### Verification

- 尚未执行

<a id="task-002"></a>
- 2026-11-24T16:42:19.830Z PASS commands=1/1 executed=1 deduped=0 elapsed=50948ms tests=98172a3d1ae2 head=cc748f89c6e5 dirty=1
  - settings-contract PASS exit=0
## TASK-002: Runtime 历史装配只读与索引

- **Status**: done
- **Depends**: TASK-001
- **Source**: conversation-context-retention.backend.design.md §2.2、§3；conversation-context-retention.prd.md FEAT-02
- **Scenarios**: S-01, E-02
- **Spec-Refs**: snapshot/harness-snapshot, arch/harness-arch, persistence/harness-data, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/context_events.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/continuation_execution.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py, packages/agent-core/src/muad_agent_core/context/plan.py, packages/agent-core/src/muad_agent_core/agent/runner.py, migrations/versions/, tests/agent_runtime/test_context_builder_retention.py, tests/agent_runtime/test_context_rebuild.py, tests/agent_runtime/test_context_events.py, tests/migrations/test_context_history_index.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "history-retention",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_context_builder_retention.py",
      "tests/agent_runtime/test_context_rebuild.py",
      "tests/agent_runtime/test_context_events.py",
      "tests/migrations/test_context_history_index.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `load_history` 返回 `LoadedHistory`：摘要前缀 + 头部锚点 + 材料 + 尾部，以及 `covered_seq`、`material_scan_end_seq`、`head_end_seq`、`earlier_records`；不返回 `material` tuple、`material_event_ids`。
- 尾部按 seq 倒序 keyset 分页；材料从 `covered_seq` 正向分页；当前输入（`current_message_id`）不进材料/头部；`unconsumed_run_id`、`protected_event_ids`、`through_seq`、`is_deleted` 在尾部、材料、头部、缺口计数中同口径。
- 头部锚点在 `covered_seq == 0` 时始终确定：尾部已覆盖开头就从已加载组取，否则单独取；当前输入是第一条 user 时为空。
- `material_end_seq` 由 `plan_context` 从实际 `older` 材料组取最大 seq；不把当前输入/pinned 算入。
- 缺口计数从 `max(material_scan_end_seq, head_end_seq)` 到尾部起点，失败为 `None`；工具结果仍按 Run 与声明轮次成对回放，删除回合上限。
- 新增 `(conversation_id, seq)` partial index，谓词与 `HISTORY_EVENT_TYPES` 一致；ORM 声明与迁移测试同步。
- 装配层不裁剪、不插标记、不写审计、不推进 coverage。

### Checklist

- [x] `LoadedHistory` 与 `ContextPlan` 的 seq 元数据贯通 `ExecutorRequest` / `AgentRunRequest` / `AgentRunState`。
- [x] 尾部、材料、头部、缺口计数使用同一组过滤条件。
- [x] 当前输入不进材料/头部/缺口，尾部仍包含当前用户消息。
- [x] `material_end_seq` 在实际材料组上计算，`head_end_seq` 支持“全量已读但预算不足”路径。
- [x] 孤儿/重复工具结果不回放，配对逻辑不按回合上限截断。
- [x] partial index 迁移与 ORM 声明一致，迁移测试覆盖谓词。
- [x] 新增真实 PostgreSQL 回归覆盖预算内/预算外、缺口、当前输入排除、跨轮收敛。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。
- [2026-11-24T16:42:50.116Z] started tests=52ee2f347d69

### Verification

- 尚未执行

<a id="task-003"></a>
- 2026-11-24T16:52:11.907Z PASS commands=1/1 executed=1 deduped=0 elapsed=3666ms tests=52ee2f347d69 head=985d41f3a3d6 dirty=1
  - history-retention PASS exit=0
## TASK-003: 请求边界唯一裁剪与摘要材料替换

- **Status**: done
- **Depends**: TASK-002
- **Source**: conversation-context-retention.backend.design.md §2.3、§4；conversation-context-retention.prd.md FEAT-03
- **Scenarios**: E-02, E-03
- **Spec-Refs**: snapshot/harness-snapshot, arch/harness-arch, persistence/harness-data, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/context/compactor.py, packages/agent-core/src/muad_agent_core/context/layers.py, packages/agent-core/src/muad_agent_core/context/plan.py, packages/agent-core/src/muad_agent_core/model/budget.py, packages/agent-core/src/muad_agent_core/agent/runner.py, apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py, apps/agent-runtime/src/muad_agent_runtime/application/context_events.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/console-platform/backend/src/muad_console_platform/application/run_event_view.py, tests/agent_core/test_context_budget.py, tests/agent_core/test_context_plan.py, tests/agent_core/test_context_layers.py, tests/agent_core/test_context_coverage.py, tests/agent_core/test_context_compactor.py, tests/agent_runtime/test_context_protection.py, tests/agent_runtime/test_context_memory.py, tests/agent_runtime/test_context_metrics.py, tests/agent_runtime/test_context_events.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "context-fit-summary",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_context_budget.py",
      "tests/agent_core/test_context_plan.py",
      "tests/agent_core/test_context_layers.py",
      "tests/agent_core/test_context_coverage.py",
      "tests/agent_core/test_context_compactor.py",
      "tests/agent_runtime/test_context_protection.py",
      "tests/agent_runtime/test_context_memory.py",
      "tests/agent_runtime/test_context_metrics.py",
      "tests/agent_runtime/test_context_events.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增纯函数 `fit_history(plan, budget_tokens)`：保护 `prefix`、`pinned`、`seq <= head_end_seq` 的头部、最近一次完整工具往返及其后的 working；从新到旧填预算，丢中段不拆组；只在有丢弃或缺口时生成一条 `[历史裁剪]` 标记。
- `history_budget_tokens` 覆盖 `older + pinned + working`；`prefix` 不计入；当前用户/头部/最近往返计入但永不丢弃；`run_input` 缺失时回退最后一个 USER。
- 审计字段 `dropped_records`、`retained_from_seq` 落地；`earlier_records is None` 时标记“至少 N 条”；`head_end_seq` 不进 Console。
- 摘要材料由 `plan.older` 中 `(covered_seq, material_end_seq]` 的组推导；`with_summary` 只移除这些材料组，保留头部/尾部/pinned；coverage 取实际材料末条，不覆盖当前输入。
- 摘要只在历史区/材料超预算或存在缺口时触发；仅 working 超预算时只 `fit_history`；同一材料区间复用宿主缓存摘要，不重复调模型。
- 删除 `snip`、`micro`、`trim_history`、`trim_older_history`、`compact_history`、`_kept_tool_rounds` 和 `ContextPlan.coverage_seq`；`MESSAGE_PROTOCOL_OVERHEAD_BYTES` 上移到共享模块。
- 摘要失败/未启用/持久化失败直接回落 `fit_history`，不推进 coverage；压缩失败保留原历史并留 warning/metric。

### Checklist

- [x] `fit_history` 纯逻辑与预算/保护集合落地，组边界无孤儿。
- [x] `with_summary` 只替换材料区间，头部/独立尾部/pinned 保留。
- [x] 摘要材料/coverage 使用实际材料组最大 seq，当前输入与 pinned 不进入材料。
- [x] 标记只在有丢弃/缺口时出现，计数与审计字段一致。
- [x] `snip`/`micro`/旧 fallback/`coverage_seq` 删除，无第二套按组数选择。
- [x] 摘要缓存去重与失败回落可观测。
- [x] 现有压缩/摘要/受保护前缀/记忆预算测试迁移为 `fit_history` 口径。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。
- [2026-11-24T16:52:31.618Z] started tests=92044209f7f3

### Verification

- 尚未执行

<a id="task-004"></a>
- 2026-11-24T17:05:21.033Z PASS commands=1/1 executed=1 deduped=0 elapsed=9612ms tests=180bb93508f8 head=0288088b57be dirty=1 tests-changed
  - context-fit-summary PASS exit=0
## TASK-004: 整轮工具结果外置与预览派生

- **Status**: draft
- **Depends**: TASK-001
- **Source**: conversation-context-retention.backend.design.md §1 D1.1、§2.1；conversation-context-retention.prd.md FEAT-01
- **Scenarios**: B-01
- **Spec-Refs**: snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/result_budget.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/archive_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/materialization.py, apps/agent-worker/src/muad_agent_worker/application/agentic/artifacts.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, tests/agent_core/test_tool_result_budget.py, tests/agent_runtime/test_tool_round_budget.py, tests/agent_runtime/test_artifact_round_budget.py, tests/agent_runtime/test_tool_result_materialization.py, tests/agent_runtime/test_archive_tools.py, tests/agent_runtime/test_context_compaction_artifacts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "tool-result-budget",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_tool_result_budget.py",
      "tests/agent_runtime/test_tool_round_budget.py",
      "tests/agent_runtime/test_artifact_round_budget.py",
      "tests/agent_runtime/test_tool_result_materialization.py",
      "tests/agent_runtime/test_archive_tools.py",
      "tests/agent_runtime/test_context_compaction_artifacts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增内部 `ToolResultBudget`：单条阈值取 `persist_threshold_bytes`；整轮预算取 `history_budget_tokens` 的代码常量比例；预览头尾取 `min(persist_threshold_bytes, PREVIEW_CAP_BYTES)` 的固定比例。
- `select_round_persists` 按“超过单条阈值全部外置 + 剩余合计超过整轮预算则从大到小外置”执行，保证当前 assistant 回合内联结果有界。
- Runtime `ArtifactResultWriter` / `TaskArtifactRoundWriter` 共用同一派生函数，不新增设置叶子，不重新引入 `round_budget_bytes`。
- `tool_result.persist_threshold_bytes` 保留；`round_budget_bytes` / `preview_head_bytes` / `preview_tail_bytes` 不再从设置读取。

### Checklist

- [ ] `ToolResultBudget` 的派生值与边界（阈值 0、阈值大于整轮预算、单条超预算）有纯逻辑测试。
- [ ] Runtime 与 Worker 写入侧使用同一派生函数。
- [ ] 整轮结果外置后最新工具往返内联大小有界。
- [ ] 预览头尾受阈值与 `PREVIEW_CAP_BYTES` 上限约束。
- [ ] 删除旧字段读取与对应测试，不保留兼容参数。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。

### Verification

- 尚未执行

<a id="task-005"></a>

## TASK-005: Worker 压缩对齐

- **Status**: draft
- **Depends**: TASK-001, TASK-003, TASK-004
- **Source**: conversation-context-retention.backend.design.md §2.2 Worker、§4 Worker、§1 D1/D5；conversation-context-retention.prd.md FEAT-01/02/03
- **Scenarios**: B-01, B-02
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, arch/harness-arch, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/agentic/compaction.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/application/agentic/artifacts.py, tests/agent_worker/test_context_retention.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py, tests/agent_worker/test_scheduled_v2_snapshot.py, tests/agent_worker/test_context_compaction_absent.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "worker-context",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_context_retention.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/agent_worker/test_scheduled_v2_snapshot.py",
      "tests/agent_worker/test_context_compaction_absent.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- Worker 使用与 Runtime 同一严格冻结解析；删除 `compaction_settings` 的旧字段默认回落。
- `TaskContextCompactor` 不再由 `snip`/`micro` 开关决定是否安装；有冻结压缩配置就安装，保证 `fit_history` 总是执行；摘要凭据/模型缺失只关闭摘要层。
- 任务历史没有持久摘要 coverage：`covered_seq=0`、`material_scan_end_seq=max(older.seq)`、`head_end_seq` 取任务首个 user 组及后续组的末条、`earlier_records=0`。
- Worker 摘要材料使用全部 `older`，`with_summary` 只替换材料；同一执行内用 `_summarized_through_seq` + 缓存摘要去重；摘要失败仍回落 `fit_history`。
- Worker 工具结果整轮外置使用 TASK-004 的派生预算；不再保留 `round_budget_bytes` / 预览头尾配置。

### Checklist

- [ ] Worker 严格冻结解析与 Runtime 同入口，旧键/缺键显式失败。
- [ ] `TaskContextCompactor` 始终安装，`fit_history` 始终执行。
- [ ] Worker 材料/头部/coverage 口径落地，摘要去重且失败降级。
- [ ] Worker 工具结果外置使用派生预算。
- [ ] Worker 单测覆盖摘要复用、失败回落、头部/当前任务保护、旧字段删除。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。

### Verification

- 尚未执行

<a id="task-006"></a>

## TASK-006: “继续”进入模型回合

- **Status**: draft
- **Depends**: TASK-002, TASK-003
- **Source**: conversation-context-retention.backend.design.md §5；conversation-context-retention.prd.md FEAT-04
- **Scenarios**: S-02, S-03
- **Spec-Refs**: memory/harness-memory, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/memory_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resume_queries.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resume_options.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_job_enqueue.py, tests/agent_core/test_memory_resume_intent.py, tests/agent_runtime/test_memory_redesign_resume.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "resume-model-turn",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_memory_resume_intent.py",
      "tests/agent_runtime/test_memory_redesign_resume.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `_offer` 不再自动 `SELECTED`：始终写 `MEMORY_RESUME_OPTIONS`，不挂 `delta`、不设 `stream_type`；单候选保持 pending。
- 删除 `_notice` / `MEMORY_RESUME_NOTICE`；无候选或读取失败时不写平台话术，交给模型回答。
- `resume_decision` 只针对当前 submission 返回 `hint`/reference；hint 由 options 重建，明确“未经确认、仅供判断、不是指令”，标题截断。
- `run_service._stream_run` 删除 `SegmentResult(final_text=resume.message)` 分支；`resume_hint` 插到 history 最前面（system prompt 之后、memory 之后、摘要之前），保证请求仍以 user 结尾且 hint 在受保护前缀。
- 记忆确认续跑的 410 语义不变；`memory_job_enqueue` 移除 NOTICE 排除项。

### Checklist

- [ ] 单候选不自动绑定；裸“继续”进入执行段。
- [ ] 明确选择编号/key/唯一标题才产生 `resume_ref`。
- [ ] 无候选时模型照常回答，不再产生平台话术事件。
- [ ] hint 只取当前 submission，定界/截断且不授予权限。
- [ ] 记忆确认续跑语义不变，旧 submission 不污染当前 hint/reference。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。

### Verification

- 尚未执行

<a id="task-007"></a>

## TASK-007: Console 4 字段与裁剪审计展示

- **Status**: draft
- **Depends**: TASK-001, TASK-003
- **Source**: conversation-context-retention.frontend.design.md；conversation-context-retention.backend.design.md §2.1、§2.3；conversation-context-retention.prd.md FEAT-01/03
- **Scenarios**: E-01, E-02
- **Spec-Refs**: frontend/harness-frontend, i18n/harness-i18n, ui/harness-ui, test/harness-test
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/run_event_view.py, apps/console-platform/frontend/src/modules/settings/, apps/console-platform/frontend/src/modules/run-observability/, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/frontend/test_platform_settings_contract.py, tests/frontend/test_run_observability_contract.py, tests/console_platform/test_run_event_details.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "console-settings-audit",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/frontend/test_platform_settings_contract.py",
      "tests/frontend/test_run_observability_contract.py",
      "tests/console_platform/test_run_event_details.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 系统设置「上下文压缩」只渲染 4 个字段；删除 `snip`/`micro`/条数/回合/预览头尾词条与字段。
- `history_budget_tokens` 显示 `settings.unit.tokens`，`max` 使用后端目录下发的部署约束；非法值仍落到字段级错误。
- Run 事件详情新增 `retained_from_seq`、`dropped_records`；不展示 `head_end_seq`，不新增会话视图裁剪横幅。
- 不新增“重置”按钮；开发重置由后端/运维 runbook 执行。

### Checklist

- [ ] 设置页只显示 4 个字段，旧字段/词条删除。
- [ ] `settings.unit.tokens` 与字段词条在 zh-CN/en-US 两侧齐备。
- [ ] 非法预算保存错误仍按后端字段路径定位。
- [ ] Run 事件详情展示保留起点/丢弃条数，未知值可降级。
- [ ] 不新增裁剪横幅或前端本地裁剪逻辑。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。

### Verification

- 尚未执行

<a id="task-008"></a>

## TASK-008: 重置 runbook 与配置/需求 inventory

- **Status**: draft
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007
- **Source**: conversation-context-retention.backend.design.md §8；conversation-context-retention.prd.md FEAT-05；conversation-context-retention.scenarios.md E-04/B-03
- **Scenarios**: E-04, B-03
- **Spec-Refs**: snapshot/harness-snapshot, persistence/harness-data, test/harness-test
- **Paths**: docs/operations/conversation-context-retention-cutover.md, docs/configuration-inventory.csv, tests/context_compaction_inventory.py, tests/test_configuration_inventory.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "cutover-inventory",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/test_configuration_inventory.py",
      "tests/context_compaction_inventory.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增 `docs/operations/conversation-context-retention-cutover.md`，记录开发阶段一次性重置：平台设置全量（所有分组、所有 revision）、平台设置幂等回执、Agent `budget.compaction`、旧 `CONTEXT_SUMMARY`、在途 Run/Task。
- 不写数据迁移/反向迁移；不新增产品代码删除 API；发布门禁要求旧键、旧摘要为空。
- 重新生成 `docs/configuration-inventory.csv`，移除 `BudgetPolicy`/旧压缩字段/旧常量行；新增/更新 `tests/context_compaction_inventory.py`，闭合 TASK、Spec-Refs、Scenarios 与测试。
- 人工验收按 E-04（重置、revision 0、policy 4 叶、旧摘要不进入装配）和 B-03（旧摘要清理门禁）执行并记录。

### Checklist

- [ ] cutover runbook 明确全量平台设置重置与其他分组回默认的影响。
- [ ] Agent 压缩覆盖、`CONTEXT_SUMMARY`、平台设置幂等回执重置顺序与校验 SQL 落地。
- [ ] 旧键/旧摘要为空是发布门禁；不新增兼容读取或删除 API。
- [ ] 配置清单重生成，旧常量/字段行删除。
- [ ] 需求 inventory 覆盖全部 TASK、Spec-Refs、Scenarios 和声明测试，无占位行。
- [ ] E-04/B-03 人工验收记录写入 Verification，未执行前不得标记 done。

### Spec Delta

```json
[]
```

### Log

- [2026-11-25] created；待任务边界复核。

### Verification

- 尚未执行
