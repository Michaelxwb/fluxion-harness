---
{"version":1,"requirement":"agent-execution-contracts"}
---
# Tasks: Agent 执行契约统一

- **Created**: 2026-11-21
- **Workflow**: full
- **Inputs**: [PRD](agent-execution-contracts.prd.md)、[设计](agent-execution-contracts.design.md)、[人工验收场景](agent-execution-contracts.scenarios.md)
- **Plan Status**: 待任务边界、Spec-Refs、Scenarios、Tests 复核；所有 TASK 为 draft，未开始实现。

## 目标与范围

把 Runtime 与 Worker 的工具执行链路收敛到三份共享契约：

- 调用身份与结果：`ToolCall` / `ToolResult` / `ToolError` / v2 工具事件；
- 恢复：prepared 事实、逐 call 对账、operation 恢复视图、子 Task 幂等补齐；
- 上下文：`ContextPlan` 的 `older`/`working`/pinned/受保护预算、摘要覆盖水位与 protected reload。

同时删除隐藏别名、字符串成败推断、两套 MCP 会话和旧事件运行时读取分支。

**明确不做**：

- 不做旧事件迁移/兼容；开发阶段 cutover 直接删除旧 `runtime.canonical_event` / `task.task_event` 数据（或整体重置开发库），旧会话不续接。
- 不新增服务、表、中间件、动态插件/发现。
- 不把 `execute` 的宿主内部执行抽成共享层；只统一模型入口、路由、授权、结果与 `retry_safety`。
- 不做 Worker PDF/docx 抽取；`view_image` 只做图片直读。
- 不保留 v1 运行时读取分支、双写/双读或过渡适配器。

## 执行与验证约定

- 一个需求一份任务文档；按本文件路径 + 明确 TASK ID 绑定，不从状态猜当前任务。
- 每个 TASK 增加有意义的行为/契约回归；Tests 只跑相关 pytest，不跑全仓/acceptance/E2E。
- 数据库/Worker 测试沿用现有临时 PG、Redis 与真实 Skill 子进程夹具；不共享 dev 库。
- `task finish` 只执行本 TASK 声明的测试；失败时不写完成。
- `Spec-Refs` 相对 `.code-flow/specs`，不带扩展名；实施前读当前 Spec 与本 TASK 的 Delta。
- 最后一个 TASK finish 后停下，等待用户完整功能验收与代码审查；确认前不 archive。

## Task Index

| TASK | 可验证能力 | Depends |
|---|---|---|
| [TASK-001](#task-001) | typed 工具结果/失败 + 全 handler 迁移 | — |
| [TASK-002](#task-002) | 调用身份 + v2 事件 + prepared 落库 + 批次止损 | TASK-001 |
| [TASK-003](#task-003) | 恢复 fold + prepared 前状态对账 | TASK-002 |
| [TASK-004](#task-004) | operation 恢复视图 + 异步执行载体补齐 | TASK-003 |
| [TASK-005](#task-005) | ContextPlan + 压缩层 older/working/pinned | TASK-002 |
| [TASK-006](#task-006) | 摘要覆盖水位 + protected reload + active skills | TASK-005 |
| [TASK-007](#task-007) | 能力装配 + 唯一 execute + 删除兼容别名 | TASK-002 |
| [TASK-008](#task-008) | SnapshotSkill.key + 快照/提示词/子 Task 统一 | TASK-007 |
| [TASK-009](#task-009) | Worker execute 路由 + 共享 Skill 指令工具 | TASK-008 |
| [TASK-010](#task-010) | Attachment read/view + scoped 读取 | TASK-009 |
| [TASK-011](#task-011) | 共享 MCP client/session/content/retry/audit/limits | TASK-002 |
| [TASK-012](#task-012) | MCP 宿主接线 + 逻辑审计/egress parity | TASK-011, TASK-007 |
| [TASK-013](#task-013) | 开发阶段 cutover + 文档/盘点/回归 | TASK-001..TASK-012 |

<a id="task-001"></a>

## TASK-001: Typed 工具结果/失败与全 handler 迁移

- **Status**: done
- **Depends**:
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-02、§3.3 进程内契约、§3.4 函数接口、§3.5 测试策略
- **Scenarios**: S-01, E-01, B-03, B-04
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/identity.py, packages/agent-core/src/muad_agent_core/tools/result.py, packages/agent-core/src/muad_agent_core/tools/pipeline.py, packages/agent-core/src/muad_agent_core/tools/registry.py, packages/agent-core/src/muad_agent_core/tools/planner.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/draft_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/time_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/archive_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/mcp_runtime_adapter.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/application/agentic/turn_result.py, apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py, tests/agent_core/test_tool_contract.py, tests/agent_core/test_tool_execution_pipeline.py, tests/agent_runtime/test_skill_tools.py, tests/agent_runtime/test_attachment_tools.py, tests/agent_runtime/test_memory_tools.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "typed-tool-contract",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_tool_contract.py",
      "tests/agent_core/test_tool_execution_pipeline.py",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/agent_runtime/test_attachment_tools.py",
      "tests/agent_runtime/test_memory_tools.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增 `ToolCall` / `ToolResult` / `ToolError` / `ArtifactRef` / `ToolExecutionResult`；`ToolHandler` 返回 `ToolResult`。
- 失败由 `ToolError` 或 `ToolResult.outcome` 表达；pipeline 只补调用身份/transport status，不再解析正文 JSON。
- 删除 `ToolDefinition.outcome_parser` / `follow_up_messages`、`tool_failure_outcome`、`diagnostic_evidence_outcome`、`skill_execution_outcome` 的正文推断职责。
- 模型侧 `{"error": {...}}` 由 pipeline 从 typed outcome 生成；MCP 数组/null/数字/字符串按成功数据处理。
- 迁移全部 handler：技能、任务、草稿、记忆、时间、附件、MCP、Worker 平台工具；AS-DECLARED 错误类统一为 `ToolError`。
- 不保留字符串 handler 兼容层；一次迁移完成。

### Checklist

- [x] `ToolResult`/`ToolError` 类型与 pipeline 的 typed 判定落地。
- [x] 全 handler 返回 `ToolResult`；不存在 `_error_result` 正文推断。
- [x] MCP 返回 `[1,2,3]`/`null`/`42`/`"ok"`/`{"error":"data"}` 时按数据处理。
- [x] 显式 typed non-retryable 失败不被翻转。
- [x] Skill/附件/记忆/任务/Worker 工具的现有行为回归通过。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "工具结果在 Kernel 统一为 typed ToolResult/ToolError：成功正文是数据，失败由 outcome/异常表达；pipeline 不得解析正文 JSON 判断成败；outcome_parser、follow_up_messages 与 tool_failure_outcome 的正文推断删除。",
    "evidence": "agent-execution-contracts design v1.1 §3.3/§3.4；S-01/E-01/B-03/B-04；tests test_tool_contract.py、test_tool_execution_pipeline.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T08:32:11.077Z] started tests=095df3ad4e26

### Verification

- 尚未执行

<a id="task-002"></a>
- 2026-11-21T09:59:36.968Z PASS commands=1/1 executed=1 deduped=0 elapsed=18450ms tests=095df3ad4e26 head=0f6e25966afd dirty=1
  - typed-tool-contract PASS exit=0
## TASK-002: 调用身份、v2 事件、prepared 落库与批次止损

- **Status**: done
- **Depends**: TASK-001
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-01/03、§3.3 事件契约/Checkpoint、§3.4 结果与追加消息
- **Scenarios**: S-05, S-06, E-05, E-07, E-10, E-11
- **Spec-Refs**: snapshot/harness-snapshot, worker/harness-worker, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/agent/continuation.py, packages/agent-core/src/muad_agent_core/tools/identity.py, packages/agent-core/src/muad_agent_core/tools/planner.py, packages/contracts/src/muad_contracts/tool_events.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_events.py, apps/agent-worker/src/muad_agent_worker/application/agentic/events.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, tests/agent_core/test_runner.py, tests/agent_core/test_tool_events.py, tests/agent_runtime/test_tool_parallel_planner.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "identity-events-stop",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_tool_events.py",
      "tests/agent_runtime/test_tool_parallel_planner.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `ModelToolCall` 解析为 `ToolCall`（内部 `name` + `model_name`）；`on_assistant_turn` 使用 `AssistantTurn`。
- `on_tool_prepared` 在 handler IO 前写 v2 `TOOL_CALL state=STARTED`：`args_hash`、脱敏 `args_preview`、来源/技能/工具/执行模式/`retry_safety`；写失败中止调用。
- `on_tool_completed` 写 v2 `COMPLETED`：`outcome`、content/预览、`artifacts[]` role、`follow_ups[]`；typed 外置端口。
- 批次循环在每批开始前检查 `no_progress`；后续串行调用补 `TOOL_NOT_EXECUTED`，不执行。
- 事件 payload 版本化；Runtime canonical event 与 Worker task_event 共用契约。

### Checklist

- [x] 模型名 → 内部名解析在 assistant 回合前完成；provider 历史仍用模型名。
- [x] prepared 事件先于 handler IO；写失败不执行。
- [x] v2 完成事件可重建 outcome/artifacts/follow-ups。
- [x] 确定性失败后后续串行写工具零调用，未执行有显式结果。
- [x] 并行只读批正常收口。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "Run/Task 工具事件使用版本化 v2 契约：assistant turn 同时保存模型名与内部名；外部执行前写 prepared/STARTED 事实（args_hash、脱敏预览、来源/技能/工具/retry_safety）；完成事件保存 outcome、artifacts role 与 follow-ups；恢复身份只用内部名。",
    "evidence": "design v1.1 §3.3 事件契约/Checkpoint；S-05/S-06/E-05/E-07/E-10/E-11；tests test_runner.py、test_tool_events.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T09:59:39.532Z] started tests=b23d221f75e7

### Verification

- 尚未执行

<a id="task-003"></a>
- 2026-11-21T10:12:54.357Z PASS commands=1/1 executed=1 deduped=0 elapsed=8616ms tests=8c4780072b52 head=0f6e25966afd dirty=1 tests-changed
  - identity-events-stop PASS exit=0
## TASK-003: 恢复 fold 与 prepared 前状态对账

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-01、§3.3 恢复状态机
- **Scenarios**: E-07, E-11, E-12
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/recovery.py, packages/agent-core/src/muad_agent_core/agent/continuation.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/application/agentic/events.py, apps/agent-worker/src/muad_agent_worker/application/agentic/async_results.py, tests/agent_core/test_recovery_states.py, tests/agent_worker/test_scheduled_v2_async_resume.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "recovery-fold",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 恢复输入包含 assistant 声明、prepared、completed、operation、子 Task、可选 checkpoint；按 `call_id` 逐个对账。
- 覆盖 `DECLARED_ONLY`、`PREPARED_PARTIAL`、`PREPARED_ONLY`、`DENIED_BEFORE_PREPARED`、`SYNC_UNKNOWN`、`COMPLETED`。
- `NO_FACTS` 必须同时无 assistant 声明；声明窗口不得当首次执行。
- `completed` 无 `prepared` 只允许未知工具/schema/授权拒绝；其他情况一致性错误。
- 恢复 fold 保留所有声明；`context_builder` 的“仅已配对调用”投影只用于最终请求。
- 无 checkpoint 仍核对已发生事实并派生计数。

### Checklist

- [x] 声明未 prepared → 补 `NOT_EXECUTED`，不重跑模型。
- [x] 部分 prepared → 已执行/待执行分别处理。
- [x] 执行前拒绝 completed 无 prepared → 保留拒绝事实。
- [x] 无 checkpoint 但有声明/operation → 进入恢复对账。
- [x] 恢复 fold 不丢声明、不产生孤儿 tool_call。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "Agentic 恢复必须按 assistant 声明逐个 call 对账，覆盖 declared-only、prepared-partial、prepared-only、operation pending/without-child、denied-before-prepared、sync-unknown；不得把声明窗口当首次执行，不得从原始 assistant 参数重放。",
    "evidence": "design v1.1 §3.3 恢复状态机；E-07/E-11/E-12；tests test_recovery_states.py、test_scheduled_v2_async_resume.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:12:57.438Z] started tests=ecebca27804e

### Verification

- 尚未执行

<a id="task-004"></a>
- 2026-11-21T10:20:05.973Z PASS commands=1/1 executed=1 deduped=0 elapsed=6106ms tests=ecebca27804e head=0f6e25966afd dirty=1
  - recovery-fold PASS exit=0
## TASK-004: Operation 恢复视图与异步执行载体补齐

- **Status**: done
- **Depends**: TASK-003
- **Source**: agent-execution-contracts.design.md §3.3 恢复状态机、§3.4 API-02
- **Scenarios**: S-06, E-08, E-10
- **Spec-Refs**: api/harness-api, worker/harness-worker, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/api/tool_operations.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/task_operations.py, packages/contracts/src/muad_contracts/scheduling.py, apps/agent-worker/src/muad_agent_worker/infrastructure/task_operation_client.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, tests/agent_runtime/test_scheduled_v2_task_operations.py, tests/agent_runtime/test_skill_operation_identity.py, tests/agent_worker/test_scheduled_v2_async_resume.py, tests/agent_worker/test_async_dispatch.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "operation-recovery",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_scheduled_v2_task_operations.py",
      "tests/agent_runtime/test_skill_operation_identity.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_async_dispatch.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增受信 `TaskToolOperationRecoveryView`：按 tenant + execution + call_id 返回冻结 `arguments`/`input_hash`/`status`/`task_id`。
- 新增内部 `GET /internal/tool-operations/recovery`；越权/不存在 404。
- Worker `TaskOperationClient.get_recovery` 与 `AsyncCallDispatcher.ensure` 使用恢复视图补齐子 Task。
- `operation` 是锚，子 Task 用 `task-operation:{operation_id}:submit` 幂等创建。
- `PREPARED_ONLY` 返回 `NOT_EXECUTED`；`operation without child` 补齐；`operation ready` 直接替换结果。
- `SYNC_UNKNOWN` 返回 `OUTCOME_UNKNOWN`/`retryable=false`，不承诺原调用重放。

### Checklist

- [x] 恢复视图只对 tenant/owner/execution 返回冻结参数。
- [x] operation 已登记、子 Task 缺失时幂等补齐，不产生第二个 operation。
- [x] prepared-only 不重放、不产生 operation。
- [x] operation ready 直接取回结果，不重执行。
- [x] 同步未知不自动重做。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "api/harness-api",
    "rule": "Operation owner 恢复视图按 tenant + execution + call_id 返回冻结 arguments/input_hash/status/task_id；只对受信内部调用方开放；缺失/越权 404；用于幂等补齐执行载体。",
    "evidence": "design v1.1 §3.3 恢复状态机、§3.4 API-02；S-06/E-08/E-10；tests test_tool_operation_recovery.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:20:09.332Z] started tests=01a0b5680c82

### Verification

- 尚未执行

<a id="task-005"></a>
- 2026-11-21T10:26:19.352Z FAIL commands=0/1 executed=1 deduped=0 elapsed=13380ms tests=29e56d7e3248 head=0f6e25966afd dirty=1 tests-changed
  - operation-recovery FAIL exit=1
- 2026-11-21T10:27:05.396Z PASS commands=1/1 executed=1 deduped=0 elapsed=23457ms tests=19e2552b6d05 head=0f6e25966afd dirty=1 tests-changed
  - operation-recovery PASS exit=0
## TASK-005: ContextPlan 与压缩层 older/working/pinned

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-04、§3.3 ContextPlan
- **Scenarios**: S-02, E-02, B-02
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/context/plan.py, packages/agent-core/src/muad_agent_core/context/compactor.py, packages/agent-core/src/muad_agent_core/context/summary.py, apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py, apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-worker/src/muad_agent_worker/application/agentic/compaction.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, tests/agent_core/test_context_plan.py, tests/agent_core/test_context_compactor.py, tests/agent_runtime/test_context_compaction_config.py, tests/agent_worker/test_context_compaction_absent.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "context-plan",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_context_plan.py",
      "tests/agent_core/test_context_compactor.py",
      "tests/agent_runtime/test_context_compaction_config.py",
      "tests/agent_worker/test_context_compaction_absent.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增显式 `RunInput` / `MessageOrigin` / `active_skills`；`ContextPlan` 分为 `prefix/pinned/older/working`，移除“首条 USER/current_turn”推断。
- `pinned` = 当前 Run 输入 + 每个活跃 Skill 最近一次 `load_skill`；受保护预算按 UTF-8 字节，超限 Skill 有可见提示。
- `trim`/`snip`/`micro` 只处理 `working`；`summary` 只处理 `older`；摘要未启用/失败时 `older` 走条数兜底。
- `compact_history` 按 micro → snip 顺序基于上一层输出计算；修复 micro 被 snip 覆盖。
- Runtime/Worker 适配器消费同一计划；`older`/`working`/pinned 不产生孤儿 tool。

### Checklist

- [x] 多轮会话历史 USER 不替代当前 Run 输入。
- [x] 工具 follow-up/后台结果按 origin 区分，不误 pin。
- [x] 每个活跃 Skill 最近正文保留；超限有可见提示。
- [x] micro 触发后最终历史真实出现占位符。
- [x] 压缩失败保留原历史。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "上下文压缩边界统一由 ContextPlan 计算：显式当前 Run 输入/来源/活跃 Skill；pinned 不受压缩；older 可摘要；working 可 tail 压缩；受保护预算按 UTF-8 字节且超限可见。",
    "evidence": "design v1.1 §3.3 ContextPlan；S-02/E-02/B-02；tests test_context_plan.py、test_context_compactor.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:27:10.326Z] started tests=7f6accac39c3

### Verification

- 尚未执行

<a id="task-006"></a>
- 2026-11-21T10:30:03.960Z PASS commands=1/1 executed=1 deduped=0 elapsed=4896ms tests=7f6accac39c3 head=0f6e25966afd dirty=1
  - context-plan PASS exit=0
## TASK-006: 摘要覆盖、protected reload、source seq 与 active skills

- **Status**: done
- **Depends**: TASK-005
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-04、§3.3 ContextPlan 与摘要覆盖
- **Scenarios**: S-02, E-06
- **Spec-Refs**: arch/harness-arch, persistence/harness-data, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/context/plan.py, packages/agent-core/src/muad_agent_core/context/compactor.py, packages/agent-core/src/muad_agent_core/context/summary.py, apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py, apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/context_events.py, apps/agent-worker/src/muad_agent_worker/application/agentic/compaction.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, tests/agent_core/test_context_coverage.py, tests/agent_runtime/test_context_summary.py, tests/agent_runtime/test_context_rebuild.py, tests/agent_runtime/test_context_rebuild.py, tests/agent_runtime/test_context_events.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "context-coverage",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_context_coverage.py",
      "tests/agent_runtime/test_context_summary.py",
      "tests/agent_runtime/test_context_rebuild.py",
      "tests/agent_runtime/test_context_rebuild.py",
      "tests/agent_runtime/test_context_events.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `ModelMessage` 增加 `source_event_seq`；`coverage_seq` 取 `older` 组内 seq 最大值，工具组含 assistant 声明与全部 tool 结果。
- 摘要 `covers_up_to_seq = coverage_seq`，不再用会话最大 seq；摘要事件保存 `protected_event_ids`。
- `context_builder` 读取 `seq > coverage_seq` 或 protected id，回载 pinned；连续摘要合并旧摘要事实，水位单调。
- `active_skills` 从已完成 `load_skill` 结果重建，注入恢复后的 `AgentRunState`。
- 摘要 transcript 只存档被替换的 `older`；摘要失败保留原历史。

### Checklist

- [x] 摘要覆盖边界只到 `older`，不跳过 pinned/working。
- [x] 连续两次摘要 → 新 Run/恢复保留旧事实与 pinned。
- [x] protected event id 即使 `seq <= coverage` 也回载。
- [x] active_skills 恢复后仍 pin 正确 Skill。
- [x] 摘要阈值按 `older` 字节计算。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "persistence/harness-data",
    "rule": "摘要 coverage 只覆盖被摘要 older 的实际 seq 边界；摘要事件保存 protected_event_ids，重建时按 id 回载 pinned；连续摘要合并旧事实且水位单调；不得用会话最大 seq。",
    "evidence": "design v1.1 §3.3 ContextPlan 与摘要覆盖；S-02/E-06；tests test_context_coverage.py、test_context_summary.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:30:07.320Z] started tests=6264e1170ab3

### Verification

- 尚未执行

<a id="task-007"></a>
- 2026-11-21T10:32:33.484Z PASS commands=1/1 executed=1 deduped=0 elapsed=4279ms tests=b58d3cf131f9 head=0f6e25966afd dirty=1 tests-changed
  - context-coverage PASS exit=0
## TASK-007: 能力装配、唯一 execute 与删除兼容别名

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-05、§3.4 能力键
- **Scenarios**: S-07, E-04, B-05
- **Spec-Refs**: artifact/harness-skill, arch/harness-arch, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/capabilities.py, packages/agent-core/src/muad_agent_core/tools/registry.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/schedule_authoring.py, apps/agent-runtime/src/muad_agent_runtime/application/task_family.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, tests/agent_runtime/test_skill_tools.py, tests/agent_worker/test_agentic_tool_capabilities.py, tests/agent_worker/test_schedule_tool_policy.py, tests/sdk/test_tool_registry.py, tests/acceptance/runtime/test_submission_hardening.py, tests/acceptance/dfx/test_dfx_routing.py, tests/capacity/test_capacity_webchat_asgi_probe.py, tests/capacity/test_capacity_webchat_http_probes.py, tests/sdk/test_platform_sliding_session.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "capability-execute",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/agent_worker/test_agentic_tool_capabilities.py",
      "tests/agent_worker/test_schedule_tool_policy.py",
      "tests/sdk/test_tool_registry.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增 `ToolCapability` + `ToolDefinition.capabilities` + `ToolAssembly.require`；装配期校验提示词承诺。
- 删除 `ToolRegistry._compatibility` / `register_compatibility` 与四个隐藏别名。
- 模型唯一入口 `execute(target=...)`；`run_skill_script` 关闭时不可解析。
- 更新 `schedule_authoring._platform_tools` 与 `task_family` 分类。
- 能力装配覆盖 Skill 指令/资源/检索、Attachment 读取、MCP、workspace/draft。

### Checklist

- [x] `registry.get("run_skill_script")` 不存在；模型目录只有 `execute`。
- [x] 关闭脚本调试时旧名称 `TOOL_NOT_FOUND`，脚本不执行。
- [x] 提示词承诺 ⊆ 已注册能力；缺失装配失败。
- [x] 所有隐藏别名调用点已更新，无兼容命名空间。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "模型唯一执行入口是 execute(target=...)；删除 ToolRegistry 兼容命名空间与 execute_skill/run_skill_script/workspace_shell/skill_draft_shell 隐藏别名；能力声明与注册在装配期校验，提示词承诺不得无工具。",
    "evidence": "design v1.1 §2.3 FEAT-05、§3.4 能力键；S-07/E-04/B-05；tests test_skill_tools.py、test_agentic_tool_capabilities.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:32:36.805Z] started tests=08125f4257cd

### Verification

- 尚未执行

<a id="task-008"></a>
- 2026-11-21T10:39:49.688Z PASS commands=1/1 executed=1 deduped=0 elapsed=26810ms tests=08125f4257cd head=0f6e25966afd dirty=1
  - capability-execute PASS exit=0
## TASK-008: SnapshotSkill.key 与快照/提示词/子 Task 统一

- **Status**: done
- **Depends**: TASK-007
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-05、§3.3 SnapshotSkill.key
- **Scenarios**: S-03, B-01
- **Spec-Refs**: snapshot/harness-snapshot, artifact/harness-skill, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/scheduling.py, apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, tests/agent_worker/test_scheduled_v2_snapshot.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py, tests/agent_worker/test_agentic_tool_capabilities.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "snapshot-skill-key",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_snapshot.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/agent_worker/test_agentic_tool_capabilities.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `SnapshotSkill` 增加正式 `key` 字段；`_snapshot_skills` 从 `ResolvedSkill.key` 复制。
- `prompt_skills`、`AgenticSkillTools._skill_key`、子 Task 快照、`SkillExecutionRouter` 统一使用 `entry.key`。
- 删除从 `frontmatter.key` 推断的运行路径。
- 测试使用真实导入/Snapshot 链路（frontmatter 无 key），不手工塞 key。

### Checklist

- [x] 真实导入 Skill（frontmatter 无 key）快照包含正确 key。
- [x] 目录/提示词与执行路由使用同一 key。
- [x] 子 Task 快照与 operation 提交身份一致。
- [x] 不依赖 frontmatter.key 的测试手柄。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "SnapshotSkill 必须冻结正式 key 字段（来自 ResolvedSkill.key），catalog/提示词/执行路由/子 Task 快照统一使用；不得从 frontmatter.key 推断。",
    "evidence": "design v1.1 §3.3 SnapshotSkill.key；S-03/B-01；tests test_scheduled_v2_snapshot.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:39:54.435Z] started tests=73a829ad6221

### Verification

- 尚未执行

<a id="task-009"></a>
- 2026-11-21T10:42:51.604Z PASS commands=1/1 executed=1 deduped=0 elapsed=19553ms tests=73a829ad6221 head=0f6e25966afd dirty=1
  - snapshot-skill-key PASS exit=0
## TASK-009: Worker execute 路由与共享 Skill 指令工具

- **Status**: done
- **Depends**: TASK-008
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-05、§3.3 Worker execute 路由
- **Scenarios**: S-03, B-01
- **Spec-Refs**: worker/harness-worker, artifact/harness-skill, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/agentic/skill_router.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py, packages/agent-core/src/muad_agent_core/tools/skill_instructions.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, tests/agent_worker/test_worker_execute_router.py, tests/agent_worker/test_agentic_tool_capabilities.py, tests/agent_runtime/test_skill_tools.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "worker-execute-router",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_worker_execute_router.py",
      "tests/agent_worker/test_agentic_tool_capabilities.py",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 新增 `SkillExecutionRouter`：`skill_key + tool` → 冻结 `SnapshotSkill`/`SnapshotTool` → allowlist/schema/retry_safety。
- `tool` 省略只在单工具 Skill 自动选择；多工具 `SKILL_TOOL_REQUIRED`；说明型 Skill `SKILL_NOT_EXECUTABLE`。
- 共享 `SkillInstructionTools`（load/read/search）注册到 Worker/Runtime。
- Worker Skill 结果统一映射 typed `ToolResult`；ASYNC 子 Task 冻结所选工具 `retry_safety`。
- 参数级授权在 `execute` 内完成，不再注册 `skill::<id>::<tool>` 模型工具。

### Checklist

- [x] 正常导入 Skill（无 frontmatter key）可路由执行。
- [x] allowlist/schema/retry_safety 按所选工具生效。
- [x] 说明型 Skill 拒绝；多工具缺 tool 拒绝。
- [x] `load_skill`/`read_skill_resource`/`search_skills` 在 Worker 可用。
- [x] ASYNC 子 Task 使用所选工具 retry_safety。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "Worker 的 execute(target=skill) 必须通过 SkillExecutionRouter 映射 SnapshotSkill.key + tool → 冻结 allowlist/schema/retry_safety；只有说明的 Skill 返回 SKILL_NOT_EXECUTABLE；结果映射 typed ToolResult，ASYNC 子 Task 冻结所选工具 retry_safety。",
    "evidence": "design v1.1 §3.3 Worker execute 路由；S-03/B-01；tests test_worker_execute_router.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:48:19.036Z] started tests=ce3c61098898

### Verification

- 尚未执行

<a id="task-010"></a>
- 2026-11-21T10:48:52.986Z PASS commands=1/1 executed=1 deduped=0 elapsed=33844ms tests=ce3c61098898 head=0f6e25966afd dirty=1
  - worker-execute-router PASS exit=0
## TASK-010: Attachment read/view 与 scoped 读取

- **Status**: done
- **Depends**: TASK-009
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-05、§3.4 API-01
- **Scenarios**: S-04, E-03
- **Spec-Refs**: artifact/harness-skill, api/harness-api, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/attachments.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, apps/agent-runtime/src/muad_agent_runtime/api/artifacts.py, apps/agent-worker/src/muad_agent_worker/infrastructure/task_artifact_client.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, tests/agent_runtime/test_attachment_tools.py, tests/agent_runtime/test_artifact_read_scope.py, tests/agent_runtime/test_scheduled_v2_task_artifacts.py, tests/agent_runtime/test_artifact_results.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "attachment-read",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_attachment_tools.py",
      "tests/agent_runtime/test_artifact_read_scope.py",
      "tests/agent_runtime/test_scheduled_v2_task_artifacts.py",
      "tests/agent_runtime/test_artifact_results.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 共享 `AttachmentReadTools`：`read_attachment` 文本分段、`view_image` 图片直读；Worker 注册。
- 扩展现有 artifact 引用端点，增加可选 `task_execution_id`；tenant + task 归属校验，跨域 404。
- Worker `TaskArtifactClient.read` 与 `NfsArtifactStore.resolve` 读取共享 PVC。
- 非文本 `read_attachment` 明确不支持；图片走 `view_image`。

### Checklist

- [x] 外置工具结果可 `read_attachment` 回读，标记在预览之外。
- [x] 图片可 `view_image`，非文本 read 明确不支持。
- [x] 跨 Task/租户读取 404，不泄露存在性。
- [x] 文本分段/offset/truncated 形状与 Runtime 一致。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "read_attachment/view_image 由共享工具实现；文本/图片读取按 tenant + task execution 限定；跨 Task/租户 404；非文本明确不支持；不得用租户级引用端点替代 owner scope。",
    "evidence": "design v1.1 §3.4 API-01、§3.3 Artifact 读取；S-04/E-03；tests test_artifact_read_scope.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:48:58.136Z] started tests=e26aa3cc1a4e

### Verification

- 尚未执行

<a id="task-011"></a>
- 2026-11-21T10:52:27.968Z PASS commands=1/1 executed=1 deduped=0 elapsed=30040ms tests=2de67161fcd1 head=0f6e25966afd dirty=1 tests-changed
  - attachment-read PASS exit=0
## TASK-011: 共享 MCP client/session/content/retry/audit/limits

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-06、§3.3 MCP 逻辑审计/有界、§3.4 MCP 错误分类
- **Scenarios**: S-09, E-09, B-06
- **Spec-Refs**: mcp/harness-mcp, secret/harness-secret, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/mcp_client.py, packages/platform-sdk/src/muad_platform_sdk/mcp_content.py, packages/platform-sdk/src/muad_platform_sdk/mcp_audit.py, packages/platform-sdk/src/muad_platform_sdk/mcp_limits.py, tests/platform_sdk/test_mcp_client.py, tests/platform_sdk/test_mcp_audit.py, tests/platform_sdk/test_mcp_limits.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "mcp-core",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/platform_sdk/test_mcp_client.py",
      "tests/platform_sdk/test_mcp_audit.py",
      "tests/platform_sdk/test_mcp_limits.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 共享 `McpClient` / `McpSession`：singleflight initialize、请求 ID 严格匹配、SSE 解码、会话失效丢弃。
- 失败事实分开 `recoverable/sent/outcome_known/replay_safe`；`retryable = recoverable and (not sent or replay_safe)`。
- 逻辑审计 `McpAuditPort` 在解码/校验后记录；HTTP 200+isError/RPC/ID 错配记 ERROR。
- `McpLimits` 在 HTTP/SSE/媒体/合计阶段检查；不在整包读入后判断。
- 内容转换：text/resource.text/resource.blob/image/unsupported；artifact 端口注入。

### Checklist

- [x] 404 本次报 session expired，下一次 initialize 成功。
- [x] 写工具已发送未知 + NON_RETRYABLE 不自动重试；READ_ONLY 可重试。
- [x] HTTP 200 + isError/RPC/ID 错配逻辑审计为 ERROR。
- [x] 响应/SSE/媒体/合计超限显式失败。
- [x] 图片/资源不返回空串。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "mcp/harness-mcp",
    "rule": "MCP 协议/会话/内容转换只保留共享实现：singleflight initialize、请求 ID 严格匹配、任何失败丢弃会话、404 重建；失败事实分开 recoverable/sent/outcome_known/replay_safe；retryable = recoverable and (not sent or replay_safe)；响应/SSE/媒体有界。",
    "evidence": "design v1.1 §3.3/§3.4 MCP；S-09/E-09/B-06；tests test_mcp_client.py、test_mcp_audit.py、test_mcp_limits.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:52:37.403Z] started tests=f10290c3e829

### Verification

- 尚未执行

<a id="task-012"></a>
- 2026-11-21T10:54:25.366Z PASS commands=1/1 executed=1 deduped=0 elapsed=7185ms tests=f10290c3e829 head=0f6e25966afd dirty=1
  - mcp-core PASS exit=0
## TASK-012: MCP 宿主接线与逻辑审计/egress parity

- **Status**: done
- **Depends**: TASK-011, TASK-007
- **Source**: agent-execution-contracts.design.md §2.3 FEAT-06、§3.3 MCP 逻辑审计/有界
- **Scenarios**: S-01, S-08, S-09, E-01, E-09
- **Spec-Refs**: mcp/harness-mcp, arch/harness-arch, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/mcp_runtime_adapter.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py, apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/main.py, apps/agent-worker/src/muad_agent_worker/worker/egress_execution.py, tests/agent_runtime/test_mcp_request_correlation.py, tests/agent_runtime/test_mcp_execution.py, tests/agent_runtime/test_mcp_headers.py, tests/agent_worker/test_agentic_mcp_parity.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "mcp-host-parity",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_mcp_request_correlation.py",
      "tests/agent_runtime/test_mcp_execution.py",
      "tests/agent_runtime/test_mcp_headers.py",
      "tests/agent_worker/test_agentic_mcp_parity.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- Runtime/Worker 共用 `McpClient`，注入各自 transport/auth/audit/artifact；删除两份 `_McpSession`/`_content_text`。
- Worker MCP 接 Egress transport 与逻辑审计；Runtime 保留 egress/audit。
- `McpToolError`/`McpError` 映射统一；MCP effect 来自冻结 catalog，缺失 fail-closed WRITE。
- MCP 图片/资源转 artifact/描述；多产物 role 与 follow-up 可由宿主重建。

### Checklist

- [x] Runtime/Worker 同一 fake server 行为一致（数组/isError/404/内容）。
- [x] HTTP 200 + isError/RPC/ID 错配审计为 ERROR。
- [x] Worker MCP 走 Egress/审计；无第二份会话实现。
- [x] effect 来自冻结 catalog，不硬编码 READ。
- [x] 图片/资源不丢失。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "mcp/harness-mcp",
    "rule": "Runtime/Worker 共用 MCP client 但注入各自 transport/auth/audit/artifact；逻辑审计在解码校验后记录，HTTP 200 + isError/RPC/ID 错配记 ERROR；MCP effect 来自冻结 catalog；两宿主行为一致。",
    "evidence": "design v1.1 §3.3/§3.4 MCP；S-01/S-08/S-09/E-01/E-09；tests test_agentic_mcp_parity.py"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:54:46.261Z] started tests=083739c50276

### Verification

- 尚未执行

<a id="task-013"></a>
- 2026-11-21T10:56:54.579Z PASS commands=1/1 executed=1 deduped=0 elapsed=14975ms tests=083739c50276 head=0f6e25966afd dirty=1
  - mcp-host-parity PASS exit=0
## TASK-013: 开发阶段 cutover、文档/盘点与回归

- **Status**: done
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011, TASK-012
- **Source**: agent-execution-contracts.design.md §4.2 发布与回滚、§4.4 数据迁移；§2.3 FEAT-07
- **Scenarios**: E-13
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, mcp/harness-mcp, snapshot/harness-snapshot, worker/harness-worker, persistence/harness-data, test/harness-test
- **Paths**: scripts/cutover_agent_execution_contracts.sql, docs/configuration-inventory.csv, docs/configuration-inventory.md, docs/06-Skill-SDK与Egress-Boundary详细设计.md, .code-flow/specs, tests/test_configuration_inventory.py, tests/architecture/test_import_direction.py, tests/contracts/test_platform_settings.py
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
      "tests/architecture/test_import_direction.py",
      "tests/contracts/test_platform_settings.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 提供 cutover SQL：校验无存活执行、按顺序删除旧事件/checkpoint/等待与 operation 事实（或整体重置开发库）。
- 不迁移旧 payload、不保留 v1 运行时分支；cutover 后只向前修复，回滚 = 重置开发库 + 回退镜像。
- 更新 `docs/configuration-inventory.csv`/`.md`、Skill 设计文档与相关 Spec Delta；删除别名/旧路径描述。
- 运行盘点/架构/contracts 回归；全量 pytest 由 CI 独立执行，浏览器 E2E 按 scenarios.md 人工执行。

### Checklist

- [x] cutover SQL 在存活执行存在时拒绝执行；单事务删除旧数据，验证计数为 0。
- [x] 文档不再出现保留别名/旧历史读取/v1 分支。
- [x] 配置盘点与当前代码一致。
- [x] 架构导入方向、contracts 回归通过。
- [x] 回滚边界写清：cutover 后只能重置开发库。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "开发阶段 cutover 是破坏性切换：暂停调度生产/停止 claim/收口 RUNNING/处置 WAITING_* 后删除旧 canonical_event/task_event 或重置开发库；不迁移、不保留 v1 分支；cutover 后只向前修复，回滚需重置开发库。",
    "evidence": "design v1.1 §4.2/§4.4；E-13；scripts/cutover_agent_execution_contracts.sql"
  }
]
```

### Log

- [2026-11-21] created
- [2026-11-21T10:57:44.158Z] started tests=1da9a1904a92

### Verification

- 尚未执行
- 2026-11-21T11:34:19.001Z PASS commands=1/1 executed=1 deduped=0 elapsed=8368ms tests=1da9a1904a92 head=0f6e25966afd dirty=1
  - cutover-inventory PASS exit=0

<a id="task-014"></a>
## TASK-014: 浏览器验收问题修复与复测

- **Status**: done
- **Depends**: TASK-013
- **Source**: `agent-execution-contracts.browser-test-report.md` §3 F-01..F-06
- **Scenarios**: S-01, S-03, S-05, E-01, E-13
- **Spec-Refs**: arch/harness-arch, mcp/harness-mcp, secret/harness-secret, worker/harness-worker, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/mcp_client.py, apps/agent-runtime/src/muad_agent_runtime/application/mcp_runtime_adapter.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/schedule_authoring.py, apps/agent-runtime/src/muad_agent_runtime/application/finalization/facts.py, apps/agent-worker/src/muad_agent_worker/infrastructure/mcp_audit.py, apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, apps/console-platform/backend/src/muad_console_platform/application/resolve_service.py, packages/contracts/src/muad_contracts/resolve.py, skills, docs/04-Agent-Runtime详细设计.md, docs/08-关键流程时序与状态机详细设计.md
- **E2E**: 浏览器人工复测见 browser-test-report §7
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "browser-acceptance-fixes",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/platform_sdk/test_mcp_client.py",
      "tests/agent_worker/test_agentic_mcp_parity.py",
      "tests/agent_worker/test_scheduled_v2_snapshot.py",
      "tests/agent_worker/test_scheduled_v2_drafts.py",
      "tests/console_internal/test_scheduled_v2_admission_materials.py",
      "tests/agent_runtime/test_mcp_error_classification.py",
      "tests/agent_runtime/test_mcp_headers.py",
      "tests/agent_runtime/test_mcp_request_correlation.py",
      "tests/agent_runtime/test_schedule_authoring_compiler.py",
      "tests/agent_runtime/test_scheduled_v2_intent_tools.py",
      "tests/agent_runtime/test_finalization_facts.py",
      "tests/agent_runtime/test_finalization_render.py"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- 修复共享 MCP client 的 Bearer 认证缺失；对话与 Worker 统一 MCP 错误码。
- Worker MCP 调用写 `runtime.egress_audit`，审计失败降级为 warning，不阻断工具。
- 可修复的 Draft 字段错误改为可重试，并返回完整的 `MCP:<server>:<tool>` / `SKILL:<id>:<tool>` 修复值。
- `tools_allow` 为空/`*` 时按 owner 展开全部生效工具并冻结到 Snapshot；Draft 预检与实际 Fire 使用同一展开。
- 更新 Skill 文档/模板与活跃设计文档中的旧别名。
- 达到 `max_identical_failures` 时用“同一失败重复达到上限”，不再误报“不可重试”。

### Checklist

- [x] 共享 MCP client 发送 Bearer；自定义 header 行为不变。
- [x] 对话/定时 MCP `isError` 均返回 `MCP_TOOL_FAILED`。
- [x] Worker MCP 调用产生 `egress_audit` 行，且不含认证头/正文。
- [x] 首次 `TOOL_NOT_OWNED` 后模型可在同一 Run 内按完整 `allowed_values` 修正。
- [x] 空 `tools_allow` 的 Snapshot allowlist 包含 owner 全部生效工具。
- [x] Skill 文档与活跃设计文档不再指导模型调用已删除别名。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-21T13:43:40.128Z] started tests=5c3d665f7cc5

### Verification

- 尚未执行
- 2026-11-21T13:44:40.862Z PASS commands=1/1 executed=1 deduped=0 elapsed=57299ms tests=5c3d665f7cc5 head=0f6e25966afd dirty=1
  - browser-acceptance-fixes PASS exit=0

<a id="task-015"></a>
## TASK-015: ContextPlan 接入与恢复正确性

- **Status**: done
- **Depends**: TASK-014
- **Source**: browser-test-report §8；review P1-1/P1-2/P1-3
- **Scenarios**: S-02, E-07, E-08, E-10, E-11
- **Spec-Refs**: snapshot/harness-snapshot, worker/harness-worker, arch/harness-arch, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/context/compactor.py, apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py, apps/agent-worker/src/muad_agent_worker/application/agentic/compaction.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/application/agentic/async_results.py, apps/agent-worker/src/muad_agent_worker/application/agentic/async_dispatch.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "context-recovery",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_context_plan.py",
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_core/test_tool_execution_pipeline.py",
      "tests/agent_core/test_scheduled_v2_kernel_ports.py",
      "tests/agent_worker/test_scheduled_v2_snapshot.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/agent_runtime/test_context_metrics.py",
      "tests/agent_runtime/test_context_memory_budget.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- Runner 改为构建 `ContextPlan` 并调用 `compact_plan`，当前 `run_input` 与活跃 Skill 必须 pinned。
- 删除 `ContextCompactor.compact(messages)` 旧入口，不保留回退。
- 摘要适配器直接消费 `ContextPlan` 的 older/coverage/protected，不再按最后 USER 重划边界；旧摘要替换不叠加。
- prepare 阶段以 Hook 改写后的参数生成唯一 `args_hash`，prepared 与 operation 共用；恢复只比较 prepared hash。
- `DECLARED_ONLY` 不提交 operation；无 checkpoint 恢复从事件重建 turns/tool_calls/failure_state。
- checkpoint 只在没有任何事件时作为 failure_state 兜底；事件 fold 覆盖后续失败事实，已确认不可重试失败不再调用模型。

### Checklist

- [x] `compact_plan` 是唯一压缩入口，`run_input`/活跃 Skill 不被裁掉。
- [x] 异步 operation 恢复不再误报 `TOOL_OPERATION_INPUT_MISMATCH`。
- [x] Hook 改写参数后 prepared/operation 共用同一执行指纹，恢复不重算 assistant 参数。
- [x] checkpoint 空状态不遮住后续不可重试失败；无 checkpoint 恢复保留预算计数与止损状态。
- [x] 摘要边界来自 `plan.older`，当前输入/受保护事件不被摘要吞掉，旧摘要不叠加。
- [x] 新增宿主链路测试覆盖 42 条消息、崩溃恢复窗口。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-21T14:14:41.081Z] started tests=5ccc68236e1a
- [2026-11-21T15:07:46.630Z] reopen: 压缩顺序/恢复测试补齐后复验
- [2026-11-21T16:24:09.594Z] reopen: GPT review: 摘要边界/执行指纹/checkpoint 失败事实修正后复验
- [2026-11-21T17:06:38.613Z] reopen: 最终代码复验（移除死恢复路径/摘要仅落实际触发层）

### Verification

- 尚未执行

<a id="task-016"></a>
- 2026-11-21T14:45:19.634Z PASS commands=1/1 executed=1 deduped=0 elapsed=10610ms tests=5ccc68236e1a head=0f6e25966afd dirty=1
  - context-recovery PASS exit=0
- 2026-11-21T15:07:57.003Z PASS commands=1/1 executed=1 deduped=0 elapsed=10196ms tests=5ccc68236e1a head=0f6e25966afd dirty=1
  - context-recovery PASS exit=0
- 2026-11-21T16:24:31.070Z PASS commands=1/1 executed=1 deduped=0 elapsed=11726ms tests=1ea341e28355 head=0f6e25966afd dirty=1 tests-changed
  - context-recovery PASS exit=0
- 2026-11-21T17:06:53.071Z PASS commands=1/1 executed=1 deduped=0 elapsed=11409ms tests=1ea341e28355 head=0f6e25966afd dirty=1 tests-changed
  - context-recovery PASS exit=0
## TASK-016: MCP 统一客户端、认证、媒体与接收上限

- **Status**: done
- **Depends**: TASK-015
- **Source**: browser-test-report §8；review P1-4/P1-5/P1-6、性能 P1
- **Scenarios**: S-01, S-08, S-09, E-01, E-14, B-06
- **Spec-Refs**: mcp/harness-mcp, secret/harness-secret, arch/harness-arch, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/mcp_client.py, mcp_content.py, mcp_audit.py, apps/agent-runtime/src/muad_agent_runtime/application/mcp_runtime_adapter.py, apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "mcp-unified",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/platform_sdk",
      "tests/agent_worker/test_agentic_mcp_parity.py",
      "tests/agent_runtime/test_mcp_headers.py",
      "tests/agent_runtime/test_mcp_request_correlation.py",
      "tests/agent_runtime/test_mcp_execution.py",
      "tests/agent_runtime/test_mcp_budget_transport.py",
      "tests/agent_runtime/test_mcp_error_classification.py",
      "tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py",
      "tests/sdk/test_shared_host_transport.py",
      "tests/sdk/test_egress_budget_lifecycle.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- 共享 `McpClient` 补齐 Bearer、`Accept`、协议版本头、`notifications/initialized`。
- Runtime 删除自有 `_McpSession`，统一走共享 client；Worker 注入 artifact writer 与审计。
- `McpToolError` 保留 `retryable`；404 可重试，`isError` 会话丢弃两端一致。
- effect 只做效果分类；`retry_safety` 来自 Owner 冻结声明，不再用 READ 推导 READ_ONLY。
- 删除 Runtime `execute_tool()` 字符串包装，生产与测试统一 typed `execute_tool_result()`。
- MCP 接收改为流式有界读取，超限立即关闭；图片/资源返回 artifact ID。

### Checklist

- [x] 严格 MCP server 验证 Accept/Protocol-Version/initialized/Bearer。
- [x] 定时与对话 MCP 错误码、retryable、审计一致。
- [x] effect=READ 且 retry_safety=NON_RETRYABLE 时，500/429 不得返回 retryable=true。
- [x] Runtime 只保留 typed 工具入口，不再维护 code/旧 reason 的转换逻辑。
- [x] 图片有 artifact ID，不再空串或仅字节描述。
- [x] 4 MiB 响应在 64 KiB 上限下不被整包消费。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-21T14:45:26.840Z] started tests=c6a19680d5ae
- [2026-11-21T14:52:49.794Z] reopen: MCP 统一实现与协议/流式/媒体测试补齐后复验
- [2026-11-21T16:24:12.627Z] reopen: GPT review: retry_safety 与旧接口清理后复验
- [2026-11-21T17:06:56.002Z] reopen: 最终代码复验（retry_safety 与 MCP code-only 错误）

### Verification

- 尚未执行

<a id="task-017"></a>
- 2026-11-21T14:45:49.331Z PASS commands=1/1 executed=1 deduped=0 elapsed=22377ms tests=c6a19680d5ae head=0f6e25966afd dirty=1
  - mcp-unified PASS exit=0
- 2026-11-21T14:53:10.828Z PASS commands=1/1 executed=1 deduped=0 elapsed=20855ms tests=c6a19680d5ae head=0f6e25966afd dirty=1
  - mcp-unified PASS exit=0
- 2026-11-21T16:25:10.873Z PASS commands=1/1 executed=1 deduped=0 elapsed=36993ms tests=c4a6eff1f681 head=0f6e25966afd dirty=1 tests-changed
  - mcp-unified PASS exit=0
- 2026-11-21T17:07:34.892Z PASS commands=1/1 executed=1 deduped=0 elapsed=36558ms tests=c4a6eff1f681 head=0f6e25966afd dirty=1 tests-changed
  - mcp-unified PASS exit=0
## TASK-017: 工具结果、附件读取与压缩性能

- **Status**: done
- **Depends**: TASK-016
- **Source**: browser-test-report §8；review P1-7/P1-8、性能 P2、补充分支
- **Scenarios**: S-03, S-04, B-02, E-06
- **Spec-Refs**: artifact/harness-skill, arch/harness-arch, worker/harness-worker, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/result.py, pipeline.py, attachments.py, context/compactor.py, apps/agent-worker/src/muad_agent_worker/application/agentic/artifacts.py, skill_router.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-worker/src/muad_agent_worker/application/agentic/async_dispatch.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "tools-attachments-performance",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_core/test_tool_contract.py",
      "tests/agent_core/test_tool_events.py",
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_core/test_context_compactor.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/agent_worker/test_scheduled_v2_drafts.py",
      "tests/agent_worker/test_async_dispatch.py",
      "tests/agent_worker/test_worker_execute_router.py",
      "tests/agent_worker/test_agentic_skill_argv.py",
      "tests/agent_runtime/test_tool_result_materialization.py",
      "tests/agent_runtime/test_artifact_read_scope.py",
      "tests/agent_runtime/test_attachment_tools.py",
      "tests/agent_runtime/test_context_metrics.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- `read_attachment`/`load_skill` 不再被整轮外置；`ToolResult` 携带 `externalizable`。
- Skill input 缺省也执行 schema 校验；`DraftShellError` 对结构化可修复错误可重试。
- micro → snip 顺序执行；附件按请求 offset/limit 增量读取纯文本，不再先整文件读取，也不保留无限文本缓存。
- 恢复 fold 先输出整轮全部 tool 结果，再追加 follow-ups；图片 follow-up 保留 artifact_id。
- 首次异步提交不再额外 `get_recovery`。

### Checklist

- [x] 20KB `read_attachment`/`load_skill` 保持内联。
- [x] 省略必填 input 被 `SKILL_ARGUMENTS_INVALID` 拒绝。
- [x] micro 节省量真实进入 snip 输入，最终字节下降。
- [x] 8 MiB 文本的两个小段不会调用整文件 `read_bytes`；首次异步提交往返减少。
- [x] 并行 tool 结果不被 follow-up 插断；图片 follow-up 带 artifact 引用，恢复不丢第二条结果。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-21T14:45:53.899Z] started tests=571559d97d3f
- [2026-11-21T14:53:15.199Z] reopen: 附件/外置/异步提交性能与测试补齐后复验
- [2026-11-21T16:24:15.256Z] reopen: GPT review: 有界读取/follow-up 顺序与 artifact 引用后复验
- [2026-11-21T17:07:37.415Z] reopen: 最终代码复验（分块跳过与 follow-up 引用）

### Verification

- 尚未执行
- 2026-11-21T14:46:06.894Z PASS commands=1/1 executed=1 deduped=0 elapsed=12892ms tests=571559d97d3f head=0f6e25966afd dirty=1
  - tools-attachments-performance PASS exit=0
- 2026-11-21T14:53:26.965Z PASS commands=1/1 executed=1 deduped=0 elapsed=11587ms tests=c8dff1f6f4eb head=0f6e25966afd dirty=1 tests-changed
  - tools-attachments-performance PASS exit=0
- 2026-11-21T16:25:26.295Z PASS commands=1/1 executed=1 deduped=0 elapsed=12996ms tests=85979dc072b5 head=0f6e25966afd dirty=1 tests-changed
  - tools-attachments-performance PASS exit=0
- 2026-11-21T17:07:54.887Z PASS commands=1/1 executed=1 deduped=0 elapsed=12998ms tests=85979dc072b5 head=0f6e25966afd dirty=1 tests-changed
  - tools-attachments-performance PASS exit=0

<a id="task-018"></a>
## TASK-018: 失败状态共享纯函数与恢复一致性

- **Status**: done
- **Depends**: TASK-017
- **Source**: GPT review round 3；失败状态恢复不一致
- **Scenarios**: S-02, E-10
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/failure.py, termination.py, runner.py, recovery.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "failure-state-recovery",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_core/test_failure_state.py",
      "tests/agent_core/test_failure_hints.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- 把成功清零、可重试失败累计、不可重试立即止损、阈值停损提为共享纯函数。
- 实时执行与恢复 fold 按同一顺序调用；恢复使用冻结的 `max_identical_failures`。
- `失败 → 成功 → 再失败` 不再误判 NO_PROGRESS；达到阈值的历史在恢复后不再调用模型。

### Checklist

- [x] 共享函数覆盖成功/取消/可重试/不可重试/阈值。
- [x] 恢复 fold 逐调用应用成功清零。
- [x] 阈值停损状态随 checkpoint 恢复后仍能阻止新模型回合。
- [x] 同批成功不能撤销确定性失败或达到阈值后的止损，后续写批次零执行。
- [x] 恢复跳过未执行占位结果，不覆盖真正的失败证据。

### Spec Delta

```json
[]
```

### Log

- [2026-11-22] created
- [2026-11-22T03:19:26.846Z] started tests=def4e98e8226
- [2026-11-22T03:56:30.531Z] reopen: 修复并行批次成功结果撤销确定性止损；用户已授权直接优化
- [2026-11-22T07:38:41.153Z] reopen: 补齐止损后未执行占位结果的恢复对照，以及失败提示的可空语言类型契约

### Verification

- 尚未执行

<a id="task-019"></a>
- 2026-11-22T03:19:36.422Z PASS commands=1/1 executed=1 deduped=0 elapsed=5420ms tests=def4e98e8226 head=0f6e25966afd dirty=1
  - failure-state-recovery PASS exit=0
- 2026-11-22T03:57:17.452Z FAIL commands=0/1 executed=1 deduped=0 elapsed=5331ms tests=def4e98e8226 head=0f6e25966afd dirty=1
  - failure-state-recovery FAIL exit=1
- 2026-11-22T03:57:51.385Z PASS commands=1/1 executed=1 deduped=0 elapsed=4974ms tests=def4e98e8226 head=0f6e25966afd dirty=1
  - failure-state-recovery PASS exit=0
- 2026-11-22T07:39:12.073Z FAIL commands=0/1 executed=1 deduped=0 elapsed=5470ms tests=fc81ae11245f head=0f6e25966afd dirty=1 tests-changed
  - failure-state-recovery FAIL exit=1
- 2026-11-22T07:40:18.026Z FAIL commands=0/1 executed=1 deduped=0 elapsed=4973ms tests=fc81ae11245f head=0f6e25966afd dirty=1 tests-changed
  - failure-state-recovery FAIL exit=1
- 2026-11-22T07:41:40.549Z PASS commands=1/1 executed=1 deduped=0 elapsed=4959ms tests=fc81ae11245f head=0f6e25966afd dirty=1 tests-changed
  - failure-state-recovery PASS exit=0
## TASK-019: 摘要先覆盖完整区间，裁剪只作降级

- **Status**: done
- **Depends**: TASK-018
- **Source**: GPT review round 3；摘要水位包含被裁剪消息
- **Scenarios**: E-05, E-07
- **Spec-Refs**: snapshot/harness-snapshot, worker/harness-worker, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/context/compactor.py, apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py, apps/agent-worker/src/muad_agent_worker/application/agentic/compaction.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "summary-before-trim",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_core/test_context_compactor.py",
      "tests/agent_core/test_context_plan.py",
      "tests/agent_runtime/test_context_metrics.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- `compact_plan` 默认不再先裁 older；Runtime/Worker 先对完整 older 生成摘要。
- 摘要未落库时才执行 `trim_history`，并作为 budget 层记录。
- 摘要输入、transcript 与 `covers_up_to_seq` 覆盖同一区间。

### Checklist

- [x] 60 条 older 摘要时模型看到全部 60 条。
- [x] 摘要成功时 tail trim 不运行，coverage 与实际覆盖一致。
- [x] 摘要失败/关闭时才裁剪 older 并记录 budget 层。

### Spec Delta

```json
[]
```

### Log

- [2026-11-22] created
- [2026-11-22T03:19:40.918Z] started tests=97f0806b0baa

### Verification

- 尚未执行

<a id="task-020"></a>
- 2026-11-22T03:19:50.889Z PASS commands=1/1 executed=1 deduped=0 elapsed=6931ms tests=97f0806b0baa head=0f6e25966afd dirty=1
  - summary-before-trim PASS exit=0
## TASK-020: 宿主显式 RunInput 与稳定消息 ID

- **Status**: done
- **Depends**: TASK-019
- **Source**: GPT review round 3；Worker RunInput 缺失、内核 return 推断
- **Scenarios**: S-02, E-07, E-10
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/runner.py, context/plan.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, context_builder.py, run_service.py, apps/agent-worker/src/muad_agent_worker/application/agentic/scheduled_turn.py, run.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "explicit-run-input",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_core/test_context_plan.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_scheduled_v2_kernel_ports.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_runtime/test_context_memory_budget.py",
      "tests/agent_runtime/test_context_protection.py",
      "tests/agent_runtime/test_context_rebuild.py",
      "tests/agent_runtime/test_memory_redesign_snapshot.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- Worker 从冻结 goal 生成显式 `RunInput`，消息 ID 绑定 Execution。
- Runtime 从 `run.input_message_id` 传入原始输入；历史 USER_MESSAGE 保留同一 message_id。
- 删除 `_last_user_input` 与 `plan_context` 的 role/同文兜底匹配。

### Checklist

- [x] Worker 恢复含图片 follow-up 时原始任务目标仍 pinned。
- [x] Runtime 当前输入按稳定 message_id 命中，不靠文本相等。
- [x] 内核不再猜测最后一条 USER。
- [x] 记忆确认合并保留当前输入身份和事件来源。
- [x] Runtime 按当前 Run 的完成事实恢复活跃 Skill 与完整指令组，不受近期窗口裁剪。

### Spec Delta

```json
[]
```

### Log

- [2026-11-22] created
- [2026-11-22T03:19:55.720Z] started tests=4708ddf69bb9
- [2026-11-22T03:59:09.918Z] reopen: 修复 Runtime 活跃 Skill 恢复与确认合并的身份丢失
- [2026-11-22T07:34:49.386Z] reopen: 补齐近期窗口内的 Skill 完整正文替换及负向回归证据
- [2026-11-22T07:56:28.905Z] reopen: 收尾自查：无活跃 Skill 时直接保留历史顺序，避免无关重排和额外排序

### Verification

- 尚未执行

<a id="task-021"></a>
- 2026-11-22T03:20:09.390Z PASS commands=1/1 executed=1 deduped=0 elapsed=9109ms tests=4708ddf69bb9 head=0f6e25966afd dirty=1
  - explicit-run-input PASS exit=0
- 2026-11-22T04:01:58.957Z FAIL commands=0/1 executed=1 deduped=0 elapsed=12674ms tests=5cadb4e38d55 head=0f6e25966afd dirty=1 tests-changed
  - explicit-run-input FAIL exit=1
- 2026-11-22T04:02:51.469Z PASS commands=1/1 executed=1 deduped=0 elapsed=12959ms tests=5cadb4e38d55 head=0f6e25966afd dirty=1 tests-changed
  - explicit-run-input PASS exit=0
- 2026-11-22T07:35:29.976Z FAIL commands=0/1 executed=1 deduped=0 elapsed=10842ms tests=5cadb4e38d55 head=0f6e25966afd dirty=1 tests-changed
  - explicit-run-input FAIL exit=1
- 2026-11-22T07:36:39.320Z FAIL commands=0/1 executed=1 deduped=0 elapsed=11122ms tests=5cadb4e38d55 head=0f6e25966afd dirty=1 tests-changed
  - explicit-run-input FAIL exit=1
- 2026-11-22T07:37:33.277Z PASS commands=1/1 executed=1 deduped=0 elapsed=10802ms tests=5cadb4e38d55 head=0f6e25966afd dirty=1 tests-changed
  - explicit-run-input PASS exit=0
- 2026-11-22T07:56:41.567Z PASS commands=1/1 executed=1 deduped=0 elapsed=11611ms tests=5cadb4e38d55 head=0f6e25966afd dirty=1 tests-changed
  - explicit-run-input PASS exit=0
## TASK-021: Worker MCP 预算端口接入

- **Status**: done
- **Depends**: TASK-020
- **Source**: GPT review round 3；Worker MCP 绕过预算治理
- **Scenarios**: S-08, E-01
- **Spec-Refs**: mcp/harness-mcp, worker/harness-worker, snapshot/harness-snapshot, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py, run.py, egress_budget.py, worker/egress_execution.py, worker/assembly.py, worker/agentic_executor.py, worker/agentic_skills.py, worker/executor.py, packages/contracts/src/muad_contracts/scheduling.py, snapshot.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "worker-mcp-budget",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_agentic_mcp_parity.py",
      "tests/agent_worker/test_scheduled_v2_agentic_adapter.py",
      "tests/platform_sdk/test_mcp_limits.py",
      "tests/agent_worker/test_agentic_mcp_budget.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_agentic_skill_argv.py",
      "tests/agent_worker/test_egress_budget_lifecycle.py",
      "tests/agent_worker/test_task_egress_authority.py",
      "tests/contracts/test_scheduled_v2_contracts.py",
      "tests/sdk/test_shared_host_transport.py",
      "tests/agent_worker/test_scheduled_agent_turn.py",
      "tests/agent_worker/test_scheduled_agent_turn_e2e.py",
      "tests/agent_core",
      "tests/test_configuration_inventory.py",
      "tests/agent_runtime/test_context_protection.py"
    ],
    "timeout": 1800,
    "parallel": false
  },
  {
    "id": "execution-types",
    "argv": [
      "uv",
      "run",
      "mypy",
      "apps",
      "packages"
    ],
    "timeout": 900,
    "parallel": false
  },
  {
    "id": "execution-lint",
    "argv": [
      "uv",
      "run",
      "ruff",
      "check",
      "apps",
      "packages",
      "tests/agent_core",
      "tests/agent_worker",
      "tests/agent_runtime/test_context_protection.py"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- Worker MCP 与同步 Skill 借用同一个现有 `HostEgressExecution`，共享当前 Execution 的持久计量。
- 定时快照正式冻结 Agent 的 egress budget；恢复沿用同一 Execution 的额度，执行段只随 claim 更换。
- MCP initialize/initialized/tools/call 都经过既有受控 transport；超限显式 `LOCAL_BUDGET_EXHAUSTED`，不发送 HTTP。删除临时本地 controller 装配。

### Checklist

- [x] Worker MCP 每次 JSON-RPC 请求都消耗现有执行级预算。
- [x] 预算耗尽返回稳定错误码，不继续发 HTTP。
- [x] 未启用受控 Egress 的任务直接执行并记录审计。
- [x] 正常挂起恢复与重新 claim 不重置额度，MCP 与同步 Skill 共用计量且不提前关闭执行段。

### Spec Delta

```json
[]
```

### Log

- [2026-11-22] created
- [2026-11-22T03:20:12.532Z] started tests=199254ab75cc
- [2026-11-22T04:03:24.366Z] reopen: 用现有受控 Egress 统一当前 Execution 的 MCP 与 Skill 计量生命周期，删除临时内存预算装配
- [2026-11-22T07:58:55.121Z] reopen: 对最终上下文快路径和新增用例重新执行声明回归、全量类型检查与相关范围 lint

### Verification

- 尚未执行

<a id="task-022"></a>
- 2026-11-22T03:20:32.689Z PASS commands=1/1 executed=1 deduped=0 elapsed=16536ms tests=199254ab75cc head=0f6e25966afd dirty=1
  - worker-mcp-budget PASS exit=0
- 2026-11-22T07:30:19.758Z FAIL commands=0/1 executed=1 deduped=0 elapsed=63128ms tests=881998b8c93a head=0f6e25966afd dirty=1 tests-changed
  - worker-mcp-budget FAIL exit=1
- 2026-11-22T07:32:05.242Z FAIL commands=0/1 executed=1 deduped=0 elapsed=63271ms tests=881998b8c93a head=0f6e25966afd dirty=1 tests-changed
  - worker-mcp-budget FAIL exit=1
- 2026-11-22T07:46:37.308Z FAIL commands=2/3 executed=3 deduped=0 elapsed=27990ms tests=3f3784bab5c3 head=0f6e25966afd dirty=1 tests-changed
  - worker-mcp-budget PASS exit=0
  - execution-types PASS exit=0
  - execution-lint FAIL exit=1
- 2026-11-22T07:49:18.182Z PASS commands=3/3 executed=3 deduped=0 elapsed=27609ms tests=02b617b393e0 head=0f6e25966afd dirty=1 tests-changed
  - worker-mcp-budget PASS exit=0
  - execution-types PASS exit=0
  - execution-lint PASS exit=0
- 2026-11-22T07:59:23.475Z PASS commands=3/3 executed=3 deduped=0 elapsed=28084ms tests=02b617b393e0 head=0f6e25966afd dirty=1 tests-changed
  - worker-mcp-budget PASS exit=0
  - execution-types PASS exit=0
  - execution-lint PASS exit=0
## TASK-022: 附件顺序分页使用可续读游标

- **Status**: done
- **Depends**: TASK-021
- **Source**: GPT review round 3；分页总扫描量平方级
- **Scenarios**: S-03, E-06
- **Spec-Refs**: arch/harness-arch, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/attachments.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "attachment-cursor",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_runtime/test_artifact_read_scope.py",
      "tests/agent_runtime/test_attachment_tools.py"
    ],
    "timeout": 1800,
    "parallel": false
  }
]
```

### Description

- `read_attachment` 增加 `cursor`，返回下一页游标；游标封装文本 IO cookie 与字符偏移。
- 顺序分页从游标续读，不再从文件头 skip 前文；offset 仍用于随机首读。
- Runtime 与 Worker 共用同一游标编码/解析，保持字符区间语义。

### Checklist

- [x] 20 页顺序读取不重复扫描前文。
- [x] 游标非法时显式参数错误。
- [x] 读到 EOF 时给出总字符数且不再返回游标。

### Spec Delta

```json
[]
```

### Log

- [2026-11-22] created
- [2026-11-22T03:20:36.629Z] started tests=41a91c2b6da1

### Verification

- 尚未执行

<a id="task-023"></a>
- 2026-11-22T03:21:02.852Z PASS commands=1/1 executed=1 deduped=0 elapsed=22999ms tests=41a91c2b6da1 head=0f6e25966afd dirty=1
  - attachment-cursor PASS exit=0
## TASK-023: 迁移 test_b262 到 execute 契约并关闭超时

- **Status**: done
- **Depends**: TASK-022
- **Source**: GPT review round 3；fixture 仍发已删除 execute_skill
- **Scenarios**: E-01
- **Spec-Refs**: test/harness-test, worker/harness-worker
- **Paths**: tests/agent_runtime/test_capacity_webchat_reply_generations.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "b262-execute-contract",
    "argv": [
      "uv", "run", "python", "-m", "pytest", "-q",
      "tests/agent_runtime/test_capacity_webchat_reply_generations.py::test_b262_committed_tool_operation_business_key_survives_model_retry"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- 模型探针改发 `execute` + `target=skill` 参数。
- 等待 operation 的前提恢复为当前契约；复测真实 uvicorn 关闭。
- 只有迁移后仍复现超时才继续排查，不再按既有 flaky 豁免。

### Checklist

- [x] 探针请求与当前 `execute` schema 一致。
- [x] operation 行真实提交，业务键断言仍成立。
- [x] 单跑通过且关闭无超时。

### Spec Delta

```json
[]
```

### Log

- [2026-11-22] created
- [2026-11-22T03:21:05.876Z] started tests=6dc70e0b002c

### Verification

- 尚未执行
- 2026-11-22T03:21:15.014Z PASS commands=1/1 executed=1 deduped=0 elapsed=5935ms tests=6dc70e0b002c head=0f6e25966afd dirty=1
  - b262-execute-contract PASS exit=0
