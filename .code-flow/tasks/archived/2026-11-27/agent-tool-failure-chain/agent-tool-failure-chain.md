---
{"version":1,"requirement":"agent-tool-failure-chain"}
---
# Agent 对话续行与运行设置生效

本任务文档关联 [PRD v0.8](agent-tool-failure-chain.prd.md)、[后端设计](agent-tool-failure-chain.backend.design.md)、[前端设计](agent-tool-failure-chain.frontend.design.md)和[人工验收场景](agent-tool-failure-chain.scenarios.md)。原始 Run 导出只作为本地复盘证据，不随仓库提交。

TASK-007 负责普通正文与超限产物交付。原 TASK-008 的提前发送部分文字已按用户决定取消。默认值以真实复测和评审为准。

## TASK-001: 统一工具发送事实与实时失败判定

- **Status**: done
- **Depends**: 
- **Source**: agent-tool-failure-chain.backend.design.md#31-工具事实进展及重试
- **Scenarios**: S-01, S-02, S-05, E-01
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, mcp/harness-mcp, artifact/harness-skill, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/**, packages/agent-core/src/muad_agent_core/tools/**, packages/agent-core/src/muad_agent_core/skill/**, packages/contracts/src/muad_contracts/tool_events.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/draft_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, apps/agent-runtime/src/muad_agent_runtime/application/mcp_runtime_adapter.py, apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/application/agentic/events.py, tests/agent_core/test_failure_state.py, tests/agent_core/test_tool_contract.py, tests/agent_core/test_outcome_parser.py, tests/agent_core/test_runner.py, tests/agent_runtime/test_mcp_error_classification.py, tests/agent_runtime/test_mcp_execution.py, tests/agent_runtime/test_attachment_tools.py, tests/agent_worker/test_agentic_mcp_parity.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_core/test_failure_state.py",
      "tests/agent_core/test_tool_contract.py",
      "tests/agent_core/test_outcome_parser.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_sandboxed_skill_executor.py",
      "tests/agent_runtime/test_mcp_error_classification.py",
      "tests/agent_runtime/test_mcp_execution.py",
      "tests/agent_runtime/test_attachment_tools.py",
      "tests/agent_worker/test_agentic_mcp_parity.py"
    ],
    "timeout": 900
  }
]
```

### Description

让已知拒绝可以续行，并按受信事实约束未知副作用。写身份、未知写入生命周期和派发门禁由共享内核统一定义；为后续恢复任务提供规范工具事件和共享判定。

### Checklist

- [x] 贯通 MCP 发送事实并校验 Skill、workspace、draft、异步提交及后处理边界，区分准备、未派发、已派发和结果未知。
- [x] 按工具归属清零连续失败计数，包括成功的 load_skill。实时派发与自动重试使用共享判定，保留宿主硬停止。
- [x] 分别保存相同调用形状和待核实操作的身份。受信业务身份优先，缺失时用冻结授权资源界定重叠，覆盖只改超时、变参和换入口。
- [x] 2026-11-27 review：effectful 工具按调用声明 `CallSemantics`（effect/retry_safety/命名空间 targets/operation_key），真实 MCP、execute、deliver、附件、Task、Memory 定义全部接线；禁止隐式全局 `*` fallback。
- [x] 2026-11-27 review：`blocked_attempts`、未知写入和 policy deny 统一在 policy 之前的 pre-dispatch gate 判定；`IDEMPOTENT` 同 operation key 可按契约重试，`NON_RETRYABLE` 直接阻断原形状。
- [x] 2026-11-27 review：unknown write 具备解除路径——同 operation key 的受信完成结果清除；无 key 的 target 只能由宿主/人工处置清除；不依赖普通成功调用。
- [x] 2026-11-27 review：`execution_started` / `outcome_known` 从 Sandbox、Skill、workspace、draft、Worker 全链路传播；post-tool hook 失败保留 content/artifacts/follow-ups。
- [x] 2026-11-27 UAT：E-01 增加 `read_workspace_file`（READ、无 shell、workspace 路径校验与分段读取），未知写入后可用真实文件内容核实，不放开原 workspace 写入门禁。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "外部工具的准备、派发与结果须由受信执行事实分别判定。明确不可重试的已知失败约束原操作，未知副作用只约束可能重复该操作的调用。独立的获授权操作仍可继续。自动重试与正常派发使用同一门禁。普通工具或技能作用域的成功须清除该作用域的连续失败计数。",
    "evidence": "实时工具状态、自动重试、派发前拒绝、未知写入与成功加载的契约回归。"
  }
]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T03:55:16.589Z] started tests=5c566586e77f
- [2026-11-27T10:44:16.692Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:53.762Z] reopen: final verification after review remediation
- [2026-11-27T13:33:48.571Z] reopen: 2026-11-27 UAT: E-01 safe read-only workspace verification

### Verification

- 尚未执行。
- 2026-11-27T07:16:14.385Z PASS commands=1/1 executed=1 deduped=0 elapsed=4546ms tests=5c566586e77f head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T11:36:20.601Z PASS commands=1/1 executed=1 deduped=0 elapsed=4161ms tests=5c566586e77f head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T12:01:05.135Z PASS commands=1/1 executed=1 deduped=0 elapsed=5210ms tests=fe53a7340e12 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T13:35:20.872Z PASS commands=1/1 executed=1 deduped=0 elapsed=6611ms tests=571968865c30 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-002: 恢复与跨 Run 继承重试门禁

- **Status**: done
- **Depends**: TASK-001
- **Source**: agent-tool-failure-chain.backend.design.md#31-工具事实进展及重试, agent-tool-failure-chain.backend.design.md#36-质量与发布
- **Scenarios**: E-01, B-01
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, worker/harness-worker, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/recovery.py, packages/agent-core/src/muad_agent_core/agent/termination.py, packages/agent-core/src/muad_agent_core/agent/runner.py, apps/agent-runtime/src/muad_agent_runtime/application/execution_checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/**, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/application/agentic/checkpoints.py, apps/agent-worker/src/muad_agent_worker/application/agentic/events.py, tests/agent_core/test_recovery_states.py, tests/agent_core/test_runner.py, tests/agent_runtime/test_runner_executor.py, tests/agent_runtime/test_scheduled_v2_runtime_adapter.py, tests/agent_worker/test_scheduled_v2_async_resume.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_runtime/test_runner_executor.py",
      "tests/agent_runtime/test_scheduled_v2_runtime_adapter.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py"
    ],
    "timeout": 900
  }
]
```

### Description

从持久事件复现 TASK-001 的运行时决策。让继续执行、恢复和同会话后续 Run 共享一套操作身份与送达事实。

### Checklist

- [x] 覆盖派发前完成事件、准备后崩溃、结果未知、检查点水位与异步 operation 挂接，杜绝重复派发。
- [x] 替换跨 Run 的全局判断，按租户会话取未核实事实。数据库集成覆盖已知拒绝、未知写入、受信解除和租户隔离。
- [x] 验证部署前能清点历史未知写入和活跃检查点。基于可信证据处置无法安全恢复的个案。
- [x] 2026-11-27 review：`TOOL_TASK_ACCEPTED` / `BACKGROUND_RESULT` 带 `source_tool_call_id`，`fold_events` 填充 `CallFacts.operation`；prepared-only 调用挂接 operation 时不再记 unknown。
- [x] 2026-11-27 review：开发阶段不保留旧 `external_blocked` 映射。旧 active Run/checkpoint 直接清理；恢复路径遇到旧形状 fail-closed，不静默恢复为空状态。
- [x] 2026-11-27 review：已知拒绝与未知写入分开处理，不把已知拒绝升级成跨 Run 的全局未知；跨 Run 只继承仍待核实的外部操作。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "重放和同会话后续执行须沿用与实时派发一致的外部操作身份及重试判定。规范事件必须区分派发前拒绝、结果已知和待核实副作用。受信操作身份缺失时，以冻结授权资源判断潜在重叠；只有可关联的受信事实可以解除待核实状态。",
    "evidence": "Runtime 数据库跨 Run 查询、Worker 检查点、异步操作及历史执行恢复回归。"
  }
]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T07:27:56.444Z] started tests=0ee316b27854
- [2026-11-27T10:44:16.737Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:53.804Z] reopen: final verification after review remediation

### Verification

- 尚未执行。
- 2026-11-27T07:51:53.516Z FAIL commands=0/1 executed=1 deduped=0 elapsed=11407ms tests=0ee316b27854 head=4dd92b29c100 dirty=1
  - regression FAIL exit=1
- 2026-11-27T07:53:17.396Z PASS commands=1/1 executed=1 deduped=0 elapsed=12028ms tests=0ee316b27854 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T11:36:35.796Z PASS commands=1/1 executed=1 deduped=0 elapsed=10650ms tests=0ee316b27854 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T12:01:15.718Z PASS commands=1/1 executed=1 deduped=0 elapsed=10471ms tests=0ee316b27854 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
## TASK-003: 收敛 execute 和冻结 Skill 能力

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-tool-failure-chain.backend.design.md#32-execute-与-skill-可见性
- **Scenarios**: S-06, E-02, E-03
- **Spec-Refs**: artifact/harness-skill, snapshot/harness-snapshot, i18n/harness-i18n, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, packages/agent-core/src/muad_agent_core/failures.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/application/agentic/skill_router.py, docs/operations/scheduled-skill-contract.md, docs/operations/sandbox-solution.md, packages/platform-sdk/MSSP-INTEGRATION.md, tests/agent_runtime/test_skill_tools.py, tests/agent_runtime/test_skill_argv.py, tests/agent_runtime/test_workspace_shell_tool.py, tests/agent_core/test_failure_hints.py, tests/agent_worker/test_worker_execute_router.py, tests/console_platform/test_run_event_details.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/agent_runtime/test_skill_argv.py",
      "tests/agent_runtime/test_workspace_shell_tool.py",
      "tests/agent_core/test_failure_hints.py",
      "tests/agent_worker/test_worker_execute_router.py",
      "tests/console_platform/test_run_event_details.py"
    ],
    "timeout": 900
  }
]
```

### Description

模型只看到宿主实际支持的 execute 参数和目标。明确未生效 Skill 的反馈，并保留授权草稿试跑、正式 Skill 与工作区命令。

### Checklist

- [x] 删除生产禁用的 script 参数、开关和专用分支。校验跨目标字段、正常默认入口、异步提交及旧审计可查。
- [x] 统一冻结策略与 handler 的 SKILL_NOT_EFFECTIVE，包含请求 key 和有界能力发现路径，验证拒绝事件可恢复。
- [x] 查运行说明及已授权的部署 Skill 引用，随实现修正文档，并准备规范 Conventions 的正反示例。
- [x] 2026-11-27 review：删除 `execute_skill` / `run_skill_script` 的所有活跃执行别名和文档承诺；Spec 用 `modify` 替换“统一执行入口”旧规则，不再追加后让 `script` 继续生效。
- [x] 2026-11-27 review：核对 `packages/platform-sdk/MSSP-INTEGRATION.md`、`docs/operations/sandbox-solution.md` 等活跃引用，只保留明确标注的历史迁移说明。
- [x] 2026-11-27 S-06：草稿 `uat-draft-ok` 创建、v1/v2 试跑、提交 PENDING、管理员审批、默认入口执行与 workspace 命令均通过；审查缺 manifest 时未发布。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "artifact/harness-skill",
    "ref": "RULE-005",
    "before": "对话直接调用 Skill 时，若默认入口声明 invocation_contract，必须在启动子进程或提交异步操作前按同一通道/参数 schema 校验；失败返回 SKILL_INVOCATION_INVALID（retryable=true）且不启动执行；辅助脚本不套用主入口契约；load_skill.manifest 返回 entrypoint 与 invocation_contract；无契约时保持现有行为。",
    "rule": "对话直接调用 Skill 时，若默认入口声明 invocation_contract，必须在启动子进程或提交异步操作前按同一通道/参数 schema 校验；失败返回 SKILL_INVOCATION_INVALID（retryable=true）且不启动执行；load_skill.manifest 返回 entrypoint 与 invocation_contract；无契约时保持现有行为。不存在辅助脚本绕过入口。",
    "evidence": "Runtime execute 三目标和 Worker skill 契约，以及脚本参数移除回归。"
  },
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "模型侧执行入口只声明目标对应的受支持能力与参数。正式 Skill 通过冻结的默认入口执行，草稿通过受权的独立试跑能力执行。未在冻结集合中生效的 Skill 须以可解释的错误反馈指引发现可用能力。`execute_skill` / `run_skill_script` 不是可执行别名，`target=skill` 不接受 `script`。",
    "evidence": "Runtime execute 三目标和 Worker skill 契约，以及未生效 Skill 与历史审计回归。"
  }
]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T07:53:38.249Z] started tests=66347aca0b13
- [2026-11-27T10:44:16.778Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:53.844Z] reopen: final verification after review remediation
- [2026-11-27T15:15:28.752Z] reopen: S-06 draft publish acceptance evidence

### Verification

- 尚未执行。
- 2026-11-27T07:56:31.234Z FAIL commands=0/1 executed=1 deduped=0 elapsed=10005ms tests=b0a10946e5cc head=4dd92b29c100 dirty=1 tests-changed
  - regression FAIL exit=1
- 2026-11-27T07:57:15.173Z PASS commands=1/1 executed=1 deduped=0 elapsed=10085ms tests=b0a10946e5cc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T11:36:51.139Z PASS commands=1/1 executed=1 deduped=0 elapsed=10236ms tests=b0a10946e5cc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T12:01:27.651Z PASS commands=1/1 executed=1 deduped=0 elapsed=11825ms tests=b0a10946e5cc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T15:15:38.718Z PASS commands=1/1 executed=1 deduped=0 elapsed=9857ms tests=b0a10946e5cc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-004: 修复企微提问交付与原 Run 续接

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-tool-failure-chain.backend.design.md#33-当前输入与企微澄清, agent-tool-failure-chain.frontend.design.md#3-前端技术设计
- **Scenarios**: S-03, S-07
- **Spec-Refs**: im/harness-im, snapshot/harness-snapshot, i18n/harness-i18n, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/human_question.py, apps/agent-runtime/src/muad_agent_runtime/application/checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_notifications.py, apps/agent-runtime/src/muad_agent_runtime/application/message_delivery.py, apps/agent-runtime/src/muad_agent_runtime/application/message_delivery_pump.py, apps/agent-runtime/src/muad_agent_runtime/application/run_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/im-gateway/src/muad_im_gateway/application/**, apps/im-connector/src/muad_im_connector/channels/wecom/**, apps/console-platform/frontend/src/modules/webchat/ChatPage.tsx, apps/console-platform/frontend/src/modules/webchat/components/ChatComposer.tsx, apps/console-platform/frontend/src/modules/webchat/runState.ts, apps/console-platform/frontend/src/modules/webchat/webchat.css, tests/agent_runtime/test_interrupt_checkpoint.py, tests/agent_runtime/test_message_delivery.py, tests/agent_runtime/test_resume_transactions.py, tests/gateway/test_waiting_resume.py, tests/gateway/test_inbound.py, tests/frontend/test_capacity_webchat_composer_uploads.py, tests/frontend/test_capacity_webchat_message_history_ui.py, tests/frontend/test_capacity_webchat_webchat_layout.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_runtime/test_interrupt_checkpoint.py",
      "tests/agent_runtime/test_message_delivery.py",
      "tests/agent_runtime/test_resume_transactions.py",
      "tests/gateway/test_waiting_resume.py",
      "tests/gateway/test_inbound.py"
    ],
    "timeout": 900
  },
  {
    "id": "webchat-ui",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/frontend/test_capacity_webchat_composer_uploads.py",
      "tests/frontend/test_capacity_webchat_message_history_ui.py",
      "tests/frontend/test_capacity_webchat_webchat_layout.py"
    ],
    "timeout": 900
  }
]
```

### Description

用一次明确提问和一次用户回答继续原任务。通过真实现有交付链排查提问落库后丢失的断点。

### Checklist

- [x] 关联 interrupt、消息意图、路由与 Gateway/Connector 回执，覆盖正常、回复句柄失效与同一提问重复事件。
- [x] 正常企微等待事务产生可发送意图。有明确断点时在现有链路修复，分别记录派发、送达和回复关联。
- [x] 验证同一会话有效回复只推进原 Run 一次，WebChat 刷新后的等待恢复保持可用。
- [x] 2026-11-27 review：等待提问期间会话被删除时沿用“删除即收口”，不在通知事务里抛错；补删除会话、重复提问和晚到回执回归。
- [x] 2026-11-27 UAT：WebChat `WAITING_INPUT` 不再显示为只能停止；输入框可提交答案并沿同一 Run resume，停止保留为次要动作；assistant 提问与持久历史同 seq 时只渲染一次。

### Spec Delta

```json
[]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T07:58:09.629Z] started tests=328cfbebb7d8
- [2026-11-27T10:44:16.818Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:53.886Z] reopen: final verification after review remediation
- [2026-11-27T13:33:48.613Z] reopen: 2026-11-27 UAT: WebChat waiting-input send and duplicate question

### Verification

- 尚未执行。
- 2026-11-27T08:00:49.286Z PASS commands=1/1 executed=1 deduped=0 elapsed=27006ms tests=328cfbebb7d8 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T11:37:20.907Z PASS commands=1/1 executed=1 deduped=0 elapsed=26100ms tests=328cfbebb7d8 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T12:01:55.185Z PASS commands=1/1 executed=1 deduped=0 elapsed=27409ms tests=328cfbebb7d8 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T13:36:14.433Z PASS commands=2/2 executed=2 deduped=0 elapsed=49727ms tests=a0832f01a630 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
  - webchat-ui PASS exit=0
## TASK-005: 保护上下文及达限后的跨 Run 续行

- **Status**: done
- **Depends**: TASK-002
- **Source**: agent-tool-failure-chain.backend.design.md#33-当前输入与企微澄清
- **Scenarios**: S-04, S-09, B-03
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, memory/harness-memory, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/context/**, packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/agent/termination.py, apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/finalization/**, tests/agent_core/test_context_compactor.py, tests/agent_core/test_context_budget.py, tests/agent_core/test_runner.py, tests/agent_runtime/test_context_builder_retention.py, tests/agent_runtime/test_context_compaction_artifacts.py, tests/agent_runtime/test_runner_executor.py, tests/agent_runtime/test_finalization_render.py, tests/agent_runtime/test_finalization_facts.py, tests/agent_runtime/test_finalization_funnel.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_core/test_context_compactor.py",
      "tests/agent_core/test_context_budget.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_runtime/test_context_builder_retention.py",
      "tests/agent_runtime/test_context_compaction_artifacts.py",
      "tests/agent_runtime/test_runner_executor.py",
      "tests/agent_runtime/test_finalization_render.py",
      "tests/agent_runtime/test_finalization_facts.py",
      "tests/agent_runtime/test_finalization_funnel.py"
    ],
    "timeout": 900
  }
]
```

### Description

沿实际加载、裁剪和请求组装定位丢目标边界。后一 Run 能复用确认事实，继续上一条请求的未完成事项。

### Checklist

- [x] 验证同一个实际模型请求里既有当前输入也有准确省略标记，压缩失败走可观测降级。
- [x] 复现工具密集的两次对话与轮次、工具预算各自达限后的‘继续’，定点修复目标与未完成事实投影。
- [x] 当前显式目标优先，旧 Run 结果可追溯，续行保留已有结果及未知写入门禁。
- [x] 2026-11-27 review：prev exchange 投影包含用户目标、最终回答和有界的工具往返/失败/未完成事实；summary 已覆盖时不重复注入原文；补工具密集型上一轮与 summary 覆盖回归。
- [x] 2026-11-27 B-03 补充：预算/轮次停止的确定性收尾保留“已取得 N 个工具结果”，并提示已完成结果可回复“继续”；跨 Run 续行复用原工具事实，不重复资产查询。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "同会话续行应基于可核验的用户目标、已确认条件和已完成执行事实。历史省略标记应对应实际保留的当前输入，续行不可把未知操作或未核实结果当作已完成。",
    "evidence": "短对话目标延续、压缩后请求投影和两种预算达限后的续行回归。"
  }
]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T08:01:09.171Z] started tests=412f14b45403
- [2026-11-27T10:44:16.858Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:53.928Z] reopen: final verification after review remediation
- [2026-11-27T14:45:42.395Z] reopen: B-03 supplement: finalization preserves partial tool results
- [2026-11-27T14:50:31.349Z] reopen: finalization copy formatting after B-03

### Verification

- 尚未执行。
- 2026-11-27T08:03:00.922Z FAIL commands=0/1 executed=1 deduped=0 elapsed=5867ms tests=412f14b45403 head=4dd92b29c100 dirty=1
  - regression FAIL exit=1
- 2026-11-27T08:03:42.794Z PASS commands=1/1 executed=1 deduped=0 elapsed=6423ms tests=412f14b45403 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T11:37:31.096Z PASS commands=1/1 executed=1 deduped=0 elapsed=6238ms tests=412f14b45403 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T12:02:01.115Z PASS commands=1/1 executed=1 deduped=0 elapsed=5816ms tests=412f14b45403 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T14:46:10.962Z PASS commands=1/1 executed=1 deduped=0 elapsed=9371ms tests=78a577b5f46b head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T14:50:40.516Z PASS commands=1/1 executed=1 deduped=0 elapsed=9052ms tests=78a577b5f46b head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-006: 修复设置冻结并评估候选默认值

- **Status**: done
- **Depends**: TASK-001, TASK-005
- **Source**: agent-tool-failure-chain.backend.design.md#34-设置诊断与默认值评估, agent-tool-failure-chain.frontend.design.md#3-前端技术设计
- **Scenarios**: S-08, B-02
- **Spec-Refs**: snapshot/harness-snapshot, model/harness-model, frontend/harness-frontend, i18n/harness-i18n, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/platform_settings.py, packages/contracts/src/muad_contracts/scheduling.py, packages/agent-core/src/muad_agent_core/agent/runner.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/execution_checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py, apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py, apps/console-platform/backend/src/muad_console_platform/application/execution_export_view.py, apps/console-platform/frontend/src/modules/settings/**, apps/console-platform/frontend/src/locales/**, skills/uat-fail-retryable/**, agent-tool-failure-chain.b02-evaluation.md, tests/test_platform_settings_schema.py, tests/agent_runtime/test_platform_settings_source.py, tests/agent_runtime/test_runner_executor.py, tests/agent_worker/test_schedule_budget_from_settings.py, tests/console_platform/test_platform_settings_api.py, tests/console_platform/test_execution_export_business_fields.py, tests/frontend/test_platform_settings_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/test_platform_settings_schema.py",
      "tests/agent_runtime/test_platform_settings_source.py",
      "tests/agent_runtime/test_runner_executor.py",
      "tests/agent_worker/test_schedule_budget_from_settings.py",
      "tests/console_platform/test_platform_settings_api.py",
      "tests/console_platform/test_execution_export_business_fields.py",
      "tests/frontend/test_platform_settings_contract.py",
      "tests/skills/test_uat_fail_retryable.py"
    ],
    "timeout": 900
  }
]
```

### Description

让失败阈值按设置生效并可排查，真实复测内网候选配置与各单项影响。

### Checklist

- [x] 统一阈值范围、语义和设置文案，修复非默认值从保存到 Run 快照、实时执行和重放的链路。
- [x] 按授权在现有执行导出显示实际冻结预算、摘要及失败阈值。旧缺项呈现为未记录。
- [x] 记录发布前已有低阈值修订和活跃执行清点。用内网组合做对照，默认值变更依据经评审证据。
- [x] 2026-11-27 review：最小值 2 在 `AgentPolicy`、runtime_config、Task `SnapshotBudget`、页面/后端校验和共享内核一致；旧 revision 不做兼容迁移，开发数据重置。
- [x] 2026-11-27 review：执行导出对 RUN 与 TASK 使用同一冻结预算投影；Task 读 `execution_snapshot_json.budget`，缺失字段保留未记录。
- [x] 2026-11-28 B-02 补充：阈值 2/3 同提示词 A/B、历史预算 256/1024/65536 同对话、摘要开关同会话完成；默认值不变，详见专项报告。

### Spec Delta

```json
[]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T08:04:16.500Z] started tests=4df3424a3ff8
- [2026-11-27T10:44:16.898Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:53.971Z] reopen: final verification after review remediation
- [2026-11-27T17:11:36.986Z] reopen: B-02 threshold/history/summary evaluation

### Verification

- 尚未执行。
- 2026-11-27T08:06:54.907Z PASS commands=1/1 executed=1 deduped=0 elapsed=63024ms tests=4df3424a3ff8 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T11:38:36.235Z PASS commands=1/1 executed=1 deduped=0 elapsed=60189ms tests=4df3424a3ff8 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T12:03:10.073Z PASS commands=1/1 executed=1 deduped=0 elapsed=62664ms tests=4df3424a3ff8 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T17:12:37.698Z PASS commands=1/1 executed=1 deduped=0 elapsed=60604ms tests=ad7654494c31 head=3fa9835389a7 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-007: 按结果大小交付正文或产物

- **Status**: done
- **Depends**: TASK-003
- **Source**: agent-tool-failure-chain.prd.md#3-功能需求与验收, agent-tool-failure-chain.backend.design.md#35-正文与产物交付
- **Scenarios**: S-10, S-11, E-04, E-05
- **Spec-Refs**: artifact/harness-skill, arch/harness-arch, platform/harness-project-platform, auth/harness-auth, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, packages/agent-core/src/muad_agent_core/prompt/**, docs/configuration-inventory.csv, tests/test_configuration_inventory.py, tests/agent_runtime/test_skill_tools.py, tests/agent_runtime/test_workspace_shell_tool.py, tests/agent_runtime/test_attachment_tools.py, tests/agent_runtime/test_output_artifacts.py, tests/agent_runtime/test_artifact_read_scope.py, tests/gateway/test_artifact_delivery.py, tests/gateway/test_delivery_api.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/agent_runtime/test_workspace_shell_tool.py",
      "tests/agent_runtime/test_attachment_tools.py",
      "tests/agent_runtime/test_output_artifacts.py",
      "tests/agent_runtime/test_artifact_read_scope.py",
      "tests/test_configuration_inventory.py",
      "tests/gateway/test_artifact_delivery.py",
      "tests/gateway/test_delivery_api.py"
    ],
    "timeout": 900
  }
]
```

### Description

适用于任意数据类型。Agent 优先满足用户指定的产物类型，未指定时按数据、用途和现有能力自主选择。一次性原始输出或中间文件留在授权工作区，交付时复用现有附件登记与归属核验。

### Checklist

- [x] 按用户指定类型或 Agent 选择准备产物。复用 `write_artifact` 所支持的格式、Skill 产物 `artifact_id` 或授权工作区相对路径。沿用同文件登记、媒体类型和租户、用户、Agent 归属校验。
- [x] 按实际类型核验产物可打开或可解析、与来源及请求范围一致。可用正文承载且未要求文件时直接回复。超限时交付产物，再用一条最终回复说明内容、范围和实际送达状态。缺少证据时如实标记部分结果。恢复完整数据使用已有产物或安全读取。
- [x] 自动回归覆盖工具描述中的正文与超限附件策略、长文本、列表、图片和结构化数据的原样交付、工作区越界、同文件复用及未确认回执。用户指定格式、Agent 自主选择及客户端呈现按 S-10、S-11 和 E-05 人工验收。
- [x] 2026-11-27 review：`deliver_artifact` 声明 `IDEMPOTENT` 和 delivery operation key；送达未知不写全局 unknown，重试复用同一交付键；workspace/skill 的未知写入不会连带阻塞交付。
- [x] 2026-11-27 review：post-tool hook 失败保留 content、artifacts 与 follow-ups；补长命令截断后仍能交付产物和 post-hook 失败回归。
- [x] 2026-11-27 E-05 集成：首次产物发送失败释放占位、重试成功、发送失败不伪造 DELIVERED；真实企微用一次性本地注入验证前 3 个交付调用失败、第 4 个成功且只收到一份 CSV，注入代码已回退。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "Agent 根据用户指定的类型、数据性质和用途准备产物，按类型核验成品及其覆盖范围。仅有截断预览时不得推断缺失内容。程序产物通过既有工作区或产物入口登记，沿用其归属和不可变版本。",
    "evidence": "用户指定格式、自主选择类型、不同类型产物的内容核验，以及同文件登记和归属回归。"
  }
]
```

### Log

- [2026-11-27] 基于 v0.5 设计拆分。
- [2026-11-27T08:06:43.921Z] started tests=4baa79409807
- [2026-11-27] 表格专用原型曾通过 63 项声明回归，原型已撤回。按 PRD v0.6 重写任务草稿。
- [2026-11-27] 按 PRD v0.7 明确 Agent 自主选择产物类型。
- [2026-11-27] 按 PRD v0.8 调整为直接正文或附件加说明，取消原 TASK-008 的分步送达。
- [2026-11-27T08:46:22.613Z] started tests=5d9c310be5d3
- [2026-11-27T08:53:18.768Z] reopen: v0.8 改为正文或超限附件加说明
- [2026-11-27T10:44:16.938Z] reopen: 2026-11-27 review remediation: write identity, unknown lifecycle, outcome propagation, no-compat cleanup
- [2026-11-27T12:00:54.012Z] reopen: final verification after review remediation
- [2026-11-27T15:15:51.119Z] reopen: E-05 integration evidence
- [2026-11-27T15:24:58.477Z] reopen: E-05 Connector artifact failure injection hook
- [2026-11-27T15:54:27.431Z] reopen: E-05 real WeCom injection evidence
- [2026-11-27] temporary Connector artifact-failure hook removed after acceptance.
- [2026-11-27T16:13:38.948Z] reopen: remove temporary Connector injection hook

### Verification

- 待新版设计和实现复验。
- 2026-11-27T08:49:14.716Z PASS commands=1/1 executed=1 deduped=0 elapsed=44578ms tests=5d9c310be5d3 head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T08:54:53.545Z PASS commands=1/1 executed=1 deduped=0 elapsed=49793ms tests=6c388350e3dc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T11:39:28.867Z PASS commands=1/1 executed=1 deduped=0 elapsed=48661ms tests=6c388350e3dc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T12:04:01.845Z PASS commands=1/1 executed=1 deduped=0 elapsed=51650ms tests=6c388350e3dc head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T15:17:07.568Z FAIL commands=0/1 executed=1 deduped=0 elapsed=76345ms tests=0ad4db4523c2 head=4dd92b29c100 dirty=1 tests-changed
  - regression FAIL exit=1
- 2026-11-27T15:19:01.033Z PASS commands=1/1 executed=1 deduped=0 elapsed=76933ms tests=0ad4db4523c2 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T15:26:17.016Z PASS commands=1/1 executed=1 deduped=0 elapsed=78432ms tests=e5e99688cd14 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T15:55:46.643Z PASS commands=1/1 executed=1 deduped=0 elapsed=79099ms tests=e5e99688cd14 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
- 2026-11-27T16:14:55.395Z PASS commands=1/1 executed=1 deduped=0 elapsed=76338ms tests=0ad4db4523c2 head=4dd92b29c100 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-008: MSSP 资产查询夹具与超限产物

- **Status**: done
- **Depends**: TASK-004, TASK-007
- **Source**: agent-tool-failure-chain.prd.md#3-功能需求与验收, agent-tool-failure-chain.backend.design.md#35-正文与产物交付
- **Scenarios**: S-02, S-03, S-10, E-04
- **Spec-Refs**: artifact/harness-skill, test/harness-test
- **Paths**: skills/mssp-asset-query-local/**, skills/README.md, tools/fake-business-platform/mssp.py, tools/fake-business-platform/fixtures/mssp.json, tools/fake-business-platform/README.md, tests/skills/test_mssp_asset_query.py, tests/tools/test_fake_mssp_platform.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "regression",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/skills/test_mssp_asset_query.py",
      "tests/tools/test_fake_mssp_platform.py"
    ],
    "timeout": 900
  }
]
```

### Description

为 S-02、S-03、S-10 提供确定性的 MSSP 资产查询夹具：按客户查询资产 IP、分页/关键字缩小、完整 CSV 导出，并让 fake platform 提供可截断的批量资产客户。

### Checklist

- [x] 新增 `mssp-asset-query-local` Skill：`company_ids` 必填、默认 20 条、`truncated`/`next_offset` 可见、`export=true` 生成完整 CSV。
- [x] fake platform 新增 `9001` 批量资产客户和 `asset_bulk` 展开；保留 `1001` 等既有小样本，不影响日报夹具。
- [x] 自动化验证默认截断后按 IP 段缩小、`ask_user` 回答后同一 Run 续跑、完整 CSV 交付与 1200 行核对。
- [x] 补 Skill 包校验、契约参数、CSV 导出和 fake platform 分页回归。
- [x] 2026-11-27 E-04：新增 `9002` 缺页/重复样本与 `asset_page_fault`；Skill 返回 `incomplete`/`issues`，平台参数拒绝非 MSSP；浏览器验证缺口说明和 38/40 行交付。

### Spec Delta

```json
[]
```

### Log

- [2026-11-27] 按 S-02/S-03/S-10 的资产查询与超限产物验收新增。
- [2026-11-27T14:12:24.609Z] reopen: 1.0.3 package after UTC alias cleanup
- [2026-11-27T15:14:57.741Z] reopen: E-04 page fault sample and skill 1.1.0

### Verification

- 尚未执行。
- 2026-11-27T14:11:24.637Z PASS commands=1/1 executed=1 deduped=0 elapsed=12834ms tests=c8cf7016b69a head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T14:12:36.546Z PASS commands=1/1 executed=1 deduped=0 elapsed=11828ms tests=c8cf7016b69a head=4dd92b29c100 dirty=1
  - regression PASS exit=0
- 2026-11-27T15:15:10.746Z PASS commands=1/1 executed=1 deduped=0 elapsed=12893ms tests=c8cf7016b69a head=4dd92b29c100 dirty=1
  - regression PASS exit=0
