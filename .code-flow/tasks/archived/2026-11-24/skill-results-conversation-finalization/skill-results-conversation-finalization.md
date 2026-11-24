---
{"version":1,"requirement":"skill-results-conversation-finalization"}
---

# Skill 执行结果与对话收尾实施任务

需求来自已确认的设计。开发基线为 `origin/main@1cb8ce31`，在当前独立工作树推进。实现先建立可信结果及同步审查，再接通 Worker、恢复与 Agent 决策。任务按 Depends 调度。各项定向测试通过后仍需完整功能验收和代码审查。

[PRD](./skill-results-conversation-finalization.prd.md) · [后端设计](./skill-results-conversation-finalization.backend.design.md) · [前端设计](./skill-results-conversation-finalization.frontend.design.md) · [14 个人工验收场景](./skill-results-conversation-finalization.scenarios.md)

Tests 声明是实施目标。新建测试文件在相应 TASK 内补齐。跨服务测试使用项目的隔离验收基座和真实 PostgreSQL、Redis、HTTP，不使用 dev 数据存储。组件用例与人工浏览器验收分开。Spec Delta 草稿在实现后按真实行为复核，规则变更配套整理 Conventions 中过时的例子。

## TASK-001: 定义并冻结 Skill 结果契约

- **Status**: done
- **Depends**:
- **Source**: skill-results-conversation-finalization.backend.design.md §3.1、§3.2、§3.4
- **Scenarios**: S-01, S-05, E-01
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, snapshot/harness-snapshot, worker/harness-worker, secret/harness-secret, test/harness-test
- **Paths**: packages/contracts/**, packages/skill-sdk/**, apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py, apps/console-platform/backend/src/muad_console_platform/application/skill_service.py, apps/console-platform/backend/src/muad_console_platform/application/skill_draft_service.py, apps/console-platform/backend/src/muad_console_platform/application/resolve_service.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-worker/src/muad_agent_worker/application/task_service.py, apps/agent-worker/src/muad_agent_worker/application/agentic/snapshot.py, tests/contracts/**, tests/sdk/test_skill_package.py, tests/console_skill/**, tests/agent_runtime/test_snapshot_freeze.py, tests/agent_worker/test_scheduled_v2_snapshot.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "contract-and-snapshot",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/contracts/test_skill_invocation.py",
      "tests/sdk/test_skill_package.py",
      "tests/console_skill/test_import_api.py",
      "tests/console_skill/test_snapshot_freeze.py",
      "tests/agent_runtime/test_snapshot_freeze.py",
      "tests/agent_worker/test_scheduled_v2_snapshot.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

统一结果 profile、执行状态、业务结论和原始退出事实的共享类型。解析及校验 result_contract，贯通导入、Draft 发布和 Run/Task 冻结。以发布方控制的 artifact checksum 与默认入口授权审查 profile。

### Checklist

- [x] 验证导入和草稿校验共用严格解析，拒绝未支持的版本、profile、额外键和不确定的默认入口。
- [x] 测试相同名称、author 或未经授权的 checksum 无法获得审查 profile 的非零完成资格，冻结的执行不随当前包或策略变化。
- [x] 验证无声明包、辅助入口、调用参数校验与历史字段缺失的原有行为，以及结果字段不携带秘密。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "可执行 Skill 的结果契约绑定不可变 artifact 及确定的默认入口，在导入时校验并随执行冻结。共享执行器独立记录真实退出事实、执行判定与业务结论。仅当受信 profile 允许且本次结果通过校验，才能将非零退出判为已完成的业务结果。宿主停止事实优先，结果声明不授予工具权限或重试安全性。",
    "evidence": "FEAT-01。导入及快照契约测试、TASK-002 的执行器验证，人工场景 S-01 和 E-01。"
  }
]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T07:40:21.831Z] started tests=89663e87543a
- [2026-11-24T07:48:56.255Z] reopen: 在已迁移的隔离测试库中执行此前跳过的数据库用例

### Verification

- 尚未执行。
- 2026-11-24T07:43:00.408Z FAIL commands=0/1 executed=1 deduped=0 elapsed=6691ms tests=89663e87543a head=1cb8ce31aa69 dirty=1
  - contract-and-snapshot FAIL exit=1
- 2026-11-24T07:43:48.585Z PASS commands=1/1 executed=1 deduped=0 elapsed=8481ms tests=89663e87543a head=1cb8ce31aa69 dirty=1
  - contract-and-snapshot PASS exit=0
- 2026-11-24T07:49:19.789Z PASS commands=1/1 executed=1 deduped=0 elapsed=23302ms tests=89663e87543a head=1cb8ce31aa69 dirty=1
  - contract-and-snapshot PASS exit=0
## TASK-002: 验证真实审查报告并接通同步 execute

- **Status**: done
- **Depends**: TASK-001
- **Source**: skill-results-conversation-finalization.backend.design.md §3.2 审查入口、报告验证、产物与控制信号
- **Scenarios**: S-01, E-01, B-02, B-05
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, snapshot/harness-snapshot, secret/harness-secret, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/skill/**, packages/agent-core/src/muad_agent_core/tools/outcome.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/draft_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, apps/console-platform/backend/src/muad_console_platform/application/skill_draft_service.py, tests/agent_core/test_sandboxed_skill_executor.py, tests/agent_core/test_outcome_parser.py, tests/agent_runtime/test_skill_tools.py, tests/skills/self_skill_review/test_check.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "review-execution",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_sandboxed_skill_executor.py",
      "tests/agent_core/test_outcome_parser.py",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/skills/self_skill_review/test_check.py"
    ],
    "timeout": 240,
    "parallel": false
  }
]
```

### Description

在共享执行器组织审查 profile 的只读目标和报告校验。Runtime 由工作区相对路径解析授权草稿，使用固定版本及同一内容证据完成真实执行。结构化结果进入同步工具回合。

### Checklist

- [x] 用真实 check.py 验证 exit 0 与有效报告、exit 1 与真实 findings，以及 exit 2 或崩溃各自的分类，保留报告与实际退出码。
- [x] 拒绝无效/越界路径、其他 owner、报告缺失/截断/过期、错误计数或指纹不符。取消、超时、预算和限流优先。
- [x] 产物授权、脱敏及失败部分结果边界可验证。草稿与 workspace Shell 继续按原语义执行，报告数据不能触发控制动作。

### Spec Delta

```json
[]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T07:44:12.827Z] started tests=aa04eb7bd054
- [2026-11-24T08:39:43.675Z] reopen: 复测修复平台审查临时目录名导致的 SS-R003 误报

### Verification

- 尚未执行。
- 2026-11-24T07:49:46.972Z PASS commands=1/1 executed=1 deduped=0 elapsed=8914ms tests=aa04eb7bd054 head=1cb8ce31aa69 dirty=1
  - review-execution PASS exit=0
- 2026-11-24T08:39:53.591Z FAIL commands=0/1 executed=1 deduped=0 elapsed=9685ms tests=aa04eb7bd054 head=1cb8ce31aa69 dirty=1
  - review-execution FAIL exit=1
- 2026-11-24T08:41:13.764Z PASS commands=1/1 executed=1 deduped=0 elapsed=7756ms tests=aa04eb7bd054 head=1cb8ce31aa69 dirty=1
  - review-execution PASS exit=0
## TASK-003: 接通 Worker 结算、报告和异步回执

- **Status**: done
- **Depends**: TASK-002
- **Source**: skill-results-conversation-finalization.backend.design.md §3.2 后台目标物化、§3.4 落库与接口
- **Scenarios**: S-05, E-01, B-04, B-05
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, worker/harness-worker, snapshot/harness-snapshot, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/worker/**, apps/agent-worker/src/muad_agent_worker/application/agentic/**, apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/**, apps/agent-runtime/src/muad_agent_runtime/application/tool_result_receipts.py, packages/contracts/src/muad_contracts/tool_runtime.py, tests/agent_worker/**, tests/agent_runtime/test_scheduled_v2_task_inbox.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "worker-results",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_execution_outcomes.py",
      "tests/agent_worker/test_worker_outcomes.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_worker/test_runtime_operation_races.py",
      "tests/agent_runtime/test_scheduled_v2_task_inbox.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

Worker 从已授权引用加载固定审查目标，调用同一验证器。将共享事实接入普通 Skill Task、Agentic、后台回执及产物。保持任务完成、业务结论与投递状态的独立性。

### Checklist

- [x] 有效 findings 结算为执行已完成并可读取报告。崩溃、协议不匹配和停止事实保持真实状态。
- [x] 后台恢复始终使用原 artifact 与目标版本，拒绝校验不符和越权访问。报告字段不能触发 wait、fan-out 或额外投递。
- [x] 保持租约、绝对期限和 CAS 检查。重复回执处置、乱序、迟到结果及终态状态按原契约验证。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "Worker 按共享结果契约完成结算并持久化原始退出事实、执行判定、业务结论及可访问结果引用。后台回执传递已验证事实。报告和业务数据不能作为等待、扇出或投递指令，投递状态不改写已经确定的执行或业务结论。",
    "evidence": "FEAT-05。Worker 结算与回执测试，人工场景 S-05、B-04。"
  }
]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T07:50:12.721Z] started tests=ad7bca31f23c

### Verification

- 尚未执行。
- 2026-11-24T07:53:39.245Z PASS commands=1/1 executed=1 deduped=0 elapsed=18384ms tests=ad7bca31f23c head=1cb8ce31aa69 dirty=1
  - worker-results PASS exit=0
## TASK-004: 让 Agent 安全继续并保持恢复一致

- **Status**: done
- **Depends**: TASK-002, TASK-003
- **Source**: skill-results-conversation-finalization.backend.design.md §3.3 重试与进展、§3.4 检查点
- **Scenarios**: S-01, S-04, E-02, E-03, B-01, B-02, B-04, B-05
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, worker/harness-worker, api/harness-api, secret/harness-secret, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/**, packages/agent-core/src/muad_agent_core/tools/**, packages/contracts/src/muad_contracts/tool_events.py, packages/contracts/src/muad_contracts/tool_runtime.py, apps/agent-runtime/src/muad_agent_runtime/application/execution_checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/**, apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py, apps/agent-worker/src/muad_agent_worker/application/agentic/**, migrations/versions/**, tests/agent_core/**, tests/agent_runtime/**, tests/agent_worker/test_agentic_no_progress_message.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runner-and-recovery",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_failure_state.py",
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_core/test_tool_events.py",
      "tests/agent_runtime/test_interrupt_checkpoint.py",
      "tests/agent_runtime/test_tool_wait_state_machine.py",
      "tests/agent_runtime/test_run_deadline_deleted_conversation.py",
      "tests/agent_worker/test_agentic_no_progress_message.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在共享 Runner 和持久化宿主中分离可重试性与能否继续。普通失败留下工具应答，执行前按可信执行阶段与副作用事实限制重做。实时与恢复复用处理规则，补齐审计、事件与检查点字段。

### Checklist

- [x] 首次不可重试的普通失败仍交给正常 Agent 回合。参数纠正及独立替代步骤受原准入约束，已开始的不可安全操作禁止换入口、变参、换 call ID 或同一会话下一轮重做。幂等重试复用原键。
- [x] 持久化调用事实、门禁持有范围和消费水位。仅有 prepared 时按未知处置。恢复与实时分类一致，重复事件不再次派发或重置计数。
- [x] 覆盖脱敏结果、未改善 findings、无关成功、同类失败拦截，以及取消、预算、期限、权限和出站限制的优先级。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "已完成工具调用以持久化的规范结果作为恢复事实。实时与恢复使用同一进展判定，并按调用与消费水位去重。已准备但没有可核实完成或未派发事实的调用视为结果未知。重试门禁基于冻结安全性、受信执行阶段和幂等身份，恢复及同一会话后续运行不能靠变参或换能力入口重做未核实的外部操作。",
    "evidence": "FEAT-02、FEAT-05、FEAT-06。Runner、回执、检查点和恢复回归，人工场景 B-01、B-02、B-04。"
  }
]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T07:53:54.839Z] started tests=0d796b97f2fc

### Verification

- 尚未执行。
- 2026-11-24T08:04:00.462Z FAIL commands=0/1 executed=1 deduped=0 elapsed=8897ms tests=0d796b97f2fc head=1cb8ce31aa69 dirty=1
  - runner-and-recovery FAIL exit=1
- 2026-11-24T08:06:37.324Z PASS commands=1/1 executed=1 deduped=0 elapsed=9158ms tests=0d796b97f2fc head=1cb8ce31aa69 dirty=1
  - runner-and-recovery PASS exit=0
## TASK-005: 识别空结果并支持询问和续接

- **Status**: done
- **Depends**: TASK-002, TASK-004
- **Source**: skill-results-conversation-finalization.backend.design.md §3.2 业务结果验证、§3.3 提问与续接
- **Scenarios**: S-02, S-03, E-03, B-03
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, snapshot/harness-snapshot, worker/harness-worker, api/harness-api, secret/harness-secret, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/skill/**, packages/contracts/**, apps/agent-runtime/src/muad_agent_runtime/application/checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/run_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/*suspension*.py, apps/agent-worker/src/muad_agent_worker/application/agentic/**, tests/agent_core/test_sandboxed_skill_executor.py, tests/agent_runtime/test_resume_transactions.py, tests/agent_runtime/test_scheduled_v2_human_routing.py, tests/agent_worker/test_scheduled_v2_human_races.py, tests/acceptance/runtime/**
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "business-result-and-input",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_sandboxed_skill_executor.py",
      "tests/agent_runtime/test_resume_transactions.py",
      "tests/agent_runtime/test_scheduled_v2_human_routing.py",
      "tests/agent_worker/test_scheduled_v2_human_races.py"
    ],
    "timeout": 240,
    "parallel": false
  }
]
```

### Description

实现 customer-devices profile 的已查无设备、需选择和正常结果。在已有中立挂起端口和鉴权链路复用普通问答。将可选范围随问题绑定，按当前访问权限续接。

### Checklist

- [x] 用真实的隔离查询 Skill 输出两类降级业务结果。缺失结果、越权候选和查询失败不能映射成零设备。
- [x] 问题和完整工具回合同事务保存。回答命中当前等待、owner、期限、授权和合法候选，重复回答幂等。
- [x] 对照取消、过期、无答复入口、直接后台 Task 和确认面。用户回答后目标范围正确，旧问题不能复活。

### Spec Delta

```json
[]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T08:07:05.294Z] started tests=386dffb2ce60

### Verification

- 尚未执行。
- 2026-11-24T08:15:45.342Z PASS commands=1/1 executed=1 deduped=0 elapsed=6589ms tests=386dffb2ce60 head=1cb8ce31aa69 dirty=1
  - business-result-and-input PASS exit=0
## TASK-006: 持久化自然收尾与技术详情

- **Status**: done
- **Depends**: TASK-004, TASK-005
- **Source**: skill-results-conversation-finalization.backend.design.md §3.3 决策、§3.4 对话输出与接口
- **Scenarios**: S-04, E-02, E-03, B-02, B-04, B-05
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, worker/harness-worker, api/harness-api, secret/harness-secret, i18n/harness-i18n, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/finalization/**, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/webchat_history.py, apps/agent-worker/src/muad_agent_worker/application/agentic/**, apps/agent-worker/src/muad_agent_worker/application/task_message_outbox.py, apps/im-gateway/src/muad_im_gateway/application/webchat*.py, packages/contracts/src/muad_contracts/webchat*.py, tests/agent_runtime/test_finalization*.py, tests/agent_runtime/test_run_events.py, tests/gateway/**
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "terminal-history-and-stream",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_finalization_facts.py",
      "tests/agent_runtime/test_finalization_render.py",
      "tests/agent_runtime/test_finalization_funnel.py",
      "tests/agent_runtime/test_run_events.py",
      "tests/agent_worker/test_agentic_no_progress_message.py"
    ],
    "timeout": 240,
    "parallel": false
  }
]
```

### Description

正常 Agent 回合可基于当前事实解释、交付部分或提出下一步。达到硬边界或模型不可用时，按照已确认、脱敏的事实确定性收尾。持久化正文、附件引用与独立诊断，使回复能随当前会话重连与后续提问继续使用。

### Checklist

- [x] 验证触发不同故障时现有正文和可交付部分保留，真实终态和技术原因一致，错误码和 Run ID 位于安全技术详情。
- [x] 硬停止不补发模型调用，不保存推理、未确认内容或原始输出。维持窗口限流独立路径及同批真实故障优先级。
- [x] 终态消息、事件、通知和状态遵守原子边界。历史、SSE 续接和下一轮对话不重复正文或擅自重做任务。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "arch/harness-arch",
    "ref": "RULE-003",
    "before": "失败收尾由 Runtime 确定性渲染，不新增模型调用；用户可见正文先给直白原因与下一步，终止码、工具/平台码、Run ID 与脱敏平台原文只进技术信息；首次确定性失败不得渲染成「重复失败」。本条覆盖 Conventions 中「收尾再发一次模型调用」的旧口径。",
    "rule": "单次工具失败应作为结构化事实交给正常 Agent 回合。不可重试约束原操作重做，能否继续由剩余权限、预算、期限、副作用门禁和进展决定。执行进入硬停止或模型不可用时，由宿主依据已持久化事实确定性收尾，不补发模型调用。正文说明已完成范围、真实障碍和可行下一步，保留可交付结果。技术码、Run ID 与脱敏诊断只进技术详情。本条覆盖 Conventions 中确定性失败第一次即停和追加模型收尾的旧口径。",
    "evidence": "FEAT-02、FEAT-04、FEAT-06。共享 Runner 与终态回归，人工场景 E-02、E-03、B-02、B-05。"
  }
]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T08:16:25.078Z] started tests=0a5e51f5442f

### Verification

- 尚未执行。
- 2026-11-24T08:18:36.382Z PASS commands=1/1 executed=1 deduped=0 elapsed=7851ms tests=0a5e51f5442f head=1cb8ce31aa69 dirty=1
  - terminal-history-and-stream PASS exit=0
## TASK-007: 适配审查、创建和上传三件套

- **Status**: done
- **Depends**: TASK-002, TASK-003, TASK-004
- **Source**: skill-results-conversation-finalization.backend.design.md §3.2 自建 Skill 三件套适配、§4
- **Scenarios**: S-01, S-05, E-01, B-02, B-06
- **Spec-Refs**: artifact/harness-skill, snapshot/harness-snapshot, api/harness-api, secret/harness-secret, test/harness-test
- **Paths**: skills/self-skill-review/**, skills/self-skill-creator/**, skills/self-skill-upload/**, skills/_templates/business-skill-python/**, skills/README.md, skills/package.sh, tests/skills/**, tests/console_skill/test_import_api.py, tests/agent_runtime/test_draft_tools.py, tests/console_platform/test_skill_draft_diagnostics.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "built-in-skill-packages",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/skills/self_skill_review/test_check.py",
      "tests/skills/test_self_skill_execute_contract.py",
      "tests/console_skill/test_import_api.py",
      "tests/agent_runtime/test_draft_tools.py",
      "tests/console_platform/test_skill_draft_diagnostics.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

把三件套包发布作为一个可验证变更，统一托管调用示例和审查结论消费。仅审查可执行包声明结果 profile，创建包和上传包通过现有工具使用结果。

### Checklist

- [x] 递增三个 manifest 版本。用真实打包及 Console 校验核对契约、入口和 release checksum。验证同名或自声明包不能获得受信分类。
- [x] 指导创建根据 verified findings 修订目标版本并重审，对未过或审查失败分别反馈。更新共用模板及规则说明，保持真实异常写 stderr。
- [x] 上传仅在明确请求下执行。核对报告与 expected_revision，冲突时重做规范审查和结构校验。有 findings 时沿用风险确认。缺报告或实际故障时不产生审批记录。

### Spec Delta

```json
[]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T08:19:18.470Z] started tests=efdde15edef0

### Verification

- 尚未执行。
- 2026-11-24T08:23:25.977Z PASS commands=1/1 executed=1 deduped=0 elapsed=20458ms tests=efdde15edef0 head=1cb8ce31aa69 dirty=1
  - built-in-skill-packages PASS exit=0
## TASK-008: 在聊天、Console 与导出展示共享结果

- **Status**: done
- **Depends**: TASK-003, TASK-005, TASK-006
- **Source**: skill-results-conversation-finalization.backend.design.md §3.4 接口、skill-results-conversation-finalization.frontend.design.md §3.1–3.3
- **Scenarios**: S-01, S-02, S-03, S-05, E-01, E-02, B-03, B-04, B-05, B-06
- **Spec-Refs**: arch/harness-arch, worker/harness-worker, api/harness-api, ui/harness-ui, ui-detail/harness-ui-detail, frontend/harness-frontend, i18n/harness-i18n, secret/harness-secret, test/harness-test
- **Paths**: apps/console-platform/backend/src/muad_console_platform/api/**, apps/console-platform/backend/src/muad_console_platform/application/**, apps/console-platform/frontend/src/modules/run-observability/**, apps/console-platform/frontend/src/modules/task-schedule/**, apps/console-platform/frontend/src/modules/webchat/**, apps/console-platform/frontend/src/locales/**, tests/console_platform/test_run_event_details.py, tests/console_platform/test_execution_export_business_fields.py, tests/frontend/**, e2e/tests/audit-observability-ui.spec.ts
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "console-result-contract",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/console_platform/test_run_event_details.py",
      "tests/console_platform/test_execution_export_business_fields.py",
      "tests/frontend/test_async_skill_background_task_center_ui.py",
      "tests/frontend/test_execution_export_ui_contract.py"
    ],
    "timeout": 240,
    "parallel": false
  },
  {
    "id": "frontend-build-and-component",
    "argv": ["make", "acceptance-e2e", "DOMAIN=audit-observability-ui"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

对 Run/Task 结果、WebChat 历史和 SSE、后台任务中心、诊断及导出做白名单投影。沿用原有权限、请求和界面结构，在现有详情区分执行判定、业务结论、退出码与报告。

### Checklist

- [x] 覆盖执行完成且 findings、真实失败、缺历史字段、退出码零、报告过期和权限不符。DTO 查询限定租户和原始调用事实。
- [x] WebChat 可续接普通问题和读取独立的技术详情。重连维持已有消息和附件，仅按持久化消息合并。
- [x] 以组件测试验证 zh-CN/en-US、键盘展开、请求代次、失败和空态。导出与实时明细对同一事实含义一致。

### Spec Delta

```json
[]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T08:23:51.166Z] started tests=cdbc32868bd5

### Verification

- 尚未执行。
- 2026-11-24T08:26:51.179Z PASS commands=2/2 executed=2 deduped=0 elapsed=42663ms tests=cdbc32868bd5 head=1cb8ce31aa69 dirty=1
  - console-result-contract PASS exit=0
  - frontend-build-and-component PASS exit=0
## TASK-009: 验证同步、后台、恢复与继续对话的完整链路

- **Status**: done
- **Depends**: TASK-007, TASK-008
- **Source**: skill-results-conversation-finalization.backend.design.md §2、§3.5、§4，skill-results-conversation-finalization.frontend.design.md §3.3
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, E-01, E-02, E-03, B-01, B-02, B-03, B-04, B-05, B-06
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, snapshot/harness-snapshot, worker/harness-worker, api/harness-api, secret/harness-secret, test/harness-test
- **Paths**: tests/acceptance/runtime/test_skill_results_conversation.py, tests/acceptance/task_schedule/test_skill_results_worker.py, tests/acceptance/runtime/**, tests/acceptance/task_schedule/**, tests/skills/**
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "isolated-result-flow",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "-m",
      "not capacity_load",
      "tests/acceptance/runtime/test_skill_results_conversation.py",
      "tests/acceptance/task_schedule/test_skill_results_worker.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

增加两组定向集成用例，使用隔离 PostgreSQL、Redis、HTTP 服务、真实 Skill 和现有验收基座，核对从调用到已持久化用户结果的因果链。人工验收单独按场景执行。

### Checklist

- [x] 用确定性模型输入验证真实审查脚本两次执行、草稿版本变动、实际退出码和最终报告。实际模型及浏览器验收按 S-01 独立记录。
- [x] 后台和同步以相同输入核对结果。重启及重复交付对实际调用次数与持久回执做断言。覆盖未知外部效果没有重做、失效问题不复活、用户下一轮可受理。
- [x] 真实副作用、生产部署与人工浏览器验证分别记录。结束后提交完整功能验收和代码审查，不据集成测试推断人工结果。

### Spec Delta

```json
[]
```

### Log

- [2026-11-24] 起草，待复核。
- [2026-11-24T08:27:22.780Z] started tests=4f02006a2ccc

### Verification

- 尚未执行。
- 2026-11-24T08:50:27.009Z FAIL commands=0/1 executed=1 deduped=0 elapsed=20418ms tests=4f02006a2ccc head=1cb8ce31aa69 dirty=1
  - isolated-result-flow FAIL exit=1
- 2026-11-24T08:52:10.352Z PASS commands=1/1 executed=1 deduped=0 elapsed=19635ms tests=4f02006a2ccc head=1cb8ce31aa69 dirty=1
  - isolated-result-flow PASS exit=0
