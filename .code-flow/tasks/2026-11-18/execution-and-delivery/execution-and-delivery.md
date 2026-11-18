---
{"version":1,"requirement":"execution-and-delivery"}
---

# Tasks: execution-and-delivery

<!--
同一需求程序下的两个工作流合并为一个 TASK 文件：
- unified-artifact-delivery：TASK-001..007（消息附件、pull/push、message_delivery、任务中心、Schedule 路由、execute/WORKSPACE_ROOT、MEDIA:）
- run-failure-reporting：TASK-008..024（失败收尾、定时任务执行模型、Fake MSSP/本地回归）
PRD/design/scenarios 仍分目录保存，不合并：
- unified-artifact-delivery/
- run-failure-reporting/
TASK-005 是 `schedule_drafts.py`/`schedule_confirm.py` 的唯一 owner；run-failure-reporting 的 Job/Authoring 只消费 `delivery_intent`/`delivery_route_id`。
-->

## TASK-001: Artifact 版本即行 + OUTPUT + 读路径

- **Status**: draft
- **Depends**:
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.2, #4.9, #4.10
- **Scenarios**: unified-artifact-delivery:S-01, unified-artifact-delivery:S-05, unified-artifact-delivery:B-01
- **Spec-Refs**: artifact/harness-skill#RULE-skill-001, persistence/harness-data#RULE-001, im/harness-im#RULE-006, artifact/harness-skill#RULE-003, persistence/harness-data#RULE-002
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/repositories/**, apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, apps/agent-worker/src/muad_agent_worker/**/artifact*.py, apps/console-platform/backend/src/muad_console_platform/application/task_view.py, migrations/versions/**, tests/agent_runtime/test_artifact_versions.py, tests/console_platform/test_task_artifacts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "artifact-version",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_artifact_versions.py", "tests/console_platform/test_task_artifacts.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

Artifact 版本即行：一个不可变版本一行、一个 `artifact_id`；`logical_file_id + version` 关联同一 attempt 的版本；`AGENT_OUTPUT`/`TASK_OUTPUT` 统一为 `OUTPUT`；下载/取件按 `artifact_id + checksum` 定位。`logical_file_id` 作用域为 `tenant + owner + attempt + 产出调用身份 + 规范路径`，跨天/跨执行/跨重试不聚合。

### Checklist

- [ ] `runtime.artifact` 增加 `logical_file_id`/`version`；唯一约束 `(tenant_id, logical_file_id, version)`。
- [ ] 版本即行；旧版本字节/checksum/storage_key 不变。
- [ ] `AGENT_OUTPUT`/`TASK_OUTPUT` 统一为 `OUTPUT`；开发期旧数据可清空，不做回填/双读。
- [ ] 下载/取件/任务中心按 `artifact_id + checksum` 读版本，不返回“最新版”冒充。
- [ ] 同一 attempt 重复登记同路径同内容复用原版本；`append_artifact` 产生新版本。
- [ ] 跨 attempt/执行/重试产生新逻辑文件，不聚合成版本链。
- [ ] 测试：版本、下载、并发追加、旧数据清理。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-002: MessagePayload + Run 终态消息 + Web 历史

- **Status**: draft
- **Depends**: TASK-001
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.3, #5.1
- **Scenarios**: unified-artifact-delivery:S-04, unified-artifact-delivery:S-05
- **Spec-Refs**: im/harness-im#RULE-im-002, im/harness-im#RULE-im-005, artifact/harness-skill#RULE-skill-001, im/harness-im#RULE-008
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/**, apps/agent-runtime/src/muad_agent_runtime/infrastructure/**, apps/console-platform/frontend/src/modules/chat/**, packages/contracts/src/muad_contracts/delivery.py, tests/agent_runtime/test_message_attachments.py, tests/frontend/test_webchat_attachments_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "message-attachments",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_message_attachments.py", "tests/frontend/test_webchat_attachments_contract.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

`MessagePayload { text, attachments[] }` 成为唯一交付单元；Run 终态 `ASSISTANT_MESSAGE`/`RUN_COMPLETED` 直接携带 attachments；Web 历史投影为一条带多附件的助手消息；`ARTIFACT_CREATED` 只用于显式中途交付；`SKILL_OUTPUT_DIR` 只挂成功输出。

### Checklist

- [ ] `MessagePayload`/`AttachmentRef` 契约；`DeliveryMessage.attachments[]`。
- [ ] Run 终态消息携带 attachments；Web 历史投影一条消息多附件。
- [ ] `ARTIFACT_CREATED` 只用于 `deliver_artifact` 显式中途交付；按附件身份去重。
- [ ] `SKILL_OUTPUT_DIR` 只在成功执行挂载；失败/取消不挂半成品。
- [ ] Web 历史/任务中心读同一附件引用，不复制元数据。
- [ ] 测试：单文件、多文件、显式交付去重、失败不挂载。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-003: message_delivery + 通用租约 + 投递泵

- **Status**: draft
- **Depends**: TASK-002
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.5, #5.6
- **Scenarios**: unified-artifact-delivery:E-01, unified-artifact-delivery:E-03, unified-artifact-delivery:B-03
- **Spec-Refs**: worker/harness-worker#RULE-003, im/harness-im#RULE-im-002, im/harness-im#RULE-007
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/**, apps/agent-runtime/src/muad_agent_runtime/application/delivery/**, apps/agent-runtime/src/muad_agent_runtime/application/channel_delivery/**, apps/agent-gateway/**, packages/contracts/src/muad_contracts/delivery.py, migrations/versions/**, tests/agent_runtime/test_message_delivery.py, tests/gateway/test_delivery_protocol.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "message-delivery",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_message_delivery.py", "tests/gateway/test_delivery_protocol.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

只有 WECOM push；新增 `runtime.message_delivery`（消息级），替代 `task_delivery` 与 `channel_notification` 的 push 分支；`channel_delivery` 的租约/ack 抽成通用实现；`UNKNOWN` 不自动重发，显式重发用新 `message_id`。

### Checklist

- [ ] `message_delivery` 表与状态机：PENDING/DELIVERING/DELIVERED/DEGRADED/UNKNOWN/FAILED。
- [ ] 稳定 `message_key`（`run:{id}:terminal`、`execution:{task_id}:final`、`...:message:{message_id}`）。
- [ ] 租约/epoch fencing；旧回执不落库；`UNKNOWN` 不自动重发。
- [ ] 通用 `DeliveryProtocolService`；Gateway `DEGRADED` 不折叠为 DELIVERED。
- [ ] 删除 `task_delivery`/`channel_notification` push 分支；不做双写。
- [ ] 测试：重复/崩溃/超时/降级/重发/并发。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-004: task.message_outbox + API + 任务中心 deliveries

- **Status**: draft
- **Depends**: TASK-003
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.6, #4.9
- **Scenarios**: unified-artifact-delivery:S-01, unified-artifact-delivery:S-03, unified-artifact-delivery:S-08
- **Spec-Refs**: worker/harness-worker#RULE-003, persistence/harness-data#RULE-001, persistence/harness-data#RULE-002
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/**, apps/agent-worker/src/muad_agent_worker/infrastructure/models/task.py, apps/agent-runtime/src/muad_agent_runtime/api/**, apps/console-platform/backend/src/muad_console_platform/application/**, apps/console-platform/frontend/src/modules/task-schedule/**, migrations/versions/**, tests/agent_worker/test_task_message_outbox.py, tests/console_platform/test_task_center_deliveries.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "task-outbox",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_task_message_outbox.py", "tests/console_platform/test_task_center_deliveries.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

Task 成功终态写 `task.message_outbox`（消息 + attachments + push 路由）；Worker 消费者调 Runtime `POST /internal/message-deliveries`；任务中心 `TaskArtifactRef.deliveries[]` 填充 `target_label`/状态；`deliveries=[]` 只在 `delivery_intent=NONE`；outbox PENDING/FAILED 不能显示成“仅 Web”。

### Checklist

- [ ] `task.message_outbox` 表与终态事务写入。
- [ ] Worker 消费者 + Runtime 幂等 API；同 `message_key + target` 不重复。
- [ ] 任务中心读 attachments + deliveries；`target_label` 不暴露内部 ID。
- [ ] `delivery_intent=NONE` → `deliveries=[]` + “仅 Web 可查看”。
- [ ] outbox PENDING → `PENDING`；FAILED → `FAILED`；不显示“仅 Web”。
- [ ] 失败/取消不写 outbox；附件保留诊断。
- [ ] 测试：多附件、无绑定、路由失效、outbox 重放。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-005: Schedule 路由独立 + 目标选择

- **Status**: draft
- **Depends**: TASK-003, TASK-004
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.7
- **Scenarios**: unified-artifact-delivery:S-01, unified-artifact-delivery:S-03
- **Spec-Refs**: worker/harness-worker#RULE-003, time/harness-time#RULE-time-001, im/harness-im#RULE-008
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, apps/agent-worker/src/muad_agent_worker/application/schedule_confirm.py, apps/agent-worker/src/muad_agent_worker/application/schedule_read.py, apps/console-platform/backend/src/muad_console_platform/application/schedule_view.py, apps/console-platform/frontend/src/modules/task-schedule/**, packages/contracts/src/muad_contracts/scheduling.py, tests/agent_worker/test_schedule_delivery_target.py, tests/console_platform/test_schedule_delivery_targets.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "schedule-route",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_schedule_delivery_target.py", "tests/console_platform/test_schedule_delivery_targets.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

移除 `schedule_drafts.py` 的“投递路由必须等于创建入口”限制；PUSH 由是否选择有效企微路由决定；新增只读目标列表；Web 创建默认 NONE；企微创建默认来源路由；Revision 冻结 `delivery_intent` + `delivery_route_id`；任务中心 pull 始终可用。本 TASK 是本合并任务文件中唯一修改 `schedule_drafts.py`/`schedule_confirm.py` 的 owner。

### Checklist

- [ ] 删除入口相等限制；确认时按显式目标解析。
- [ ] 只读目标列表 API；前端不展示内部 ID，只显示安全标签。
- [ ] 路由校验：tenant/user/agent、`OUTBOUND_CHANNELS`、绑定有效；失败 `SCHEDULE_DELIVERY_UNSUPPORTED`。
- [ ] Revision 冻结 `delivery_intent`/`delivery_route_id`；执行按冻结路由 push。
- [ ] Web pull 不是 `delivery_intent`；有 Web 入口任务中心始终可读。
- [ ] Spec Delta：`worker/harness-worker#RULE-003` 明确“Schedule push 目标独立于创建入口；无绑定 NONE；任务中心 pull 始终可用”。
- [ ] 测试：Web 创建推企微、企微创建 Web 可读、无绑定 NONE、路由失效 NEEDS_ATTENTION。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-006: execute target + WORKSPACE_ROOT

- **Status**: draft
- **Depends**:
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.11
- **Scenarios**: unified-artifact-delivery:E-08, unified-artifact-delivery:E-09
- **Spec-Refs**: api/harness-api#RULE-api-001, artifact/harness-skill#RULE-skill-001, artifact/harness-skill#RULE-002, artifact/harness-skill#RULE-003
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, packages/agent-core/src/muad_agent_core/tools/**, packages/common/src/muad_common/settings.py, packages/agent-core/src/muad_agent_core/sandbox/**, tests/agent_runtime/test_execute_target_contract.py, tests/agent_worker/test_workspace_root_absolute.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "execute-workspace",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_execute_target_contract.py", "tests/agent_worker/test_workspace_root_absolute.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

`execute` 的 `target` 必填，漏传返回指名缺失字段的可行动错误；`execute_skill` 别名关系清楚；`WORKSPACE_ROOT` 启动归一为绝对路径或显式失败，相对路径不进入 `SandboxSpec`。

### Checklist

- [ ] `execute` schema/描述明确 `target`；漏传不执行任何 Skill。
- [ ] 错误体指名缺失/非法字段，不泄露参数值。
- [ ] `execute_skill`/`execute target=skill` 关系与回归。
- [ ] `WORKSPACE_ROOT` 绝对化/启动失败；Runtime/Worker 同一 resolved root。
- [ ] 测试：漏 target、别名、相对路径、sandbox cwd。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-007: MEDIA: 兼容层（P1）

- **Status**: draft
- **Depends**: TASK-002, TASK-003
- **Source**: unified-artifact-delivery/unified-artifact-delivery.backend.design.md#4.8
- **Scenarios**: unified-artifact-delivery:S-04
- **Spec-Refs**: artifact/harness-skill#RULE-skill-001, secret/harness-secret#RULE-secret-001, secret/harness-secret#RULE-002
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/**, packages/agent-core/src/muad_agent_core/**, tests/agent_runtime/test_media_compat.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "media",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_media_compat.py"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

只解析最终助手文本的独立 `MEDIA:` 行；不解析工具 stdout/Skill stdout/流式块；路径限制在 workspace/输出目录；无效引用可见；与 `SKILL_OUTPUT_DIR`/`deliver_artifact` 按附件身份去重。

### Checklist

- [ ] 独立行、非代码块、workspace 路径。
- [ ] 拒绝绝对越界/`..`/symlink/无权限。
- [ ] 无效引用可见，不静默丢弃。
- [ ] 不解析工具 stdout/流式块。
- [ ] 与输出目录/显式交付去重。
- [ ] 测试：合法/非法/代码块/工具输出。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-008: 业务执行结果契约与失败跟踪

- **Status**: blocked
- **Depends**: TASK-006, TASK-002
- **Source**: run-failure-reporting/run-failure-reporting.design.md#4.2, #4.3
- **Scenarios**: run-failure-reporting:S-01, run-failure-reporting:S-02, run-failure-reporting:S-03, run-failure-reporting:S-10
- **Spec-Refs**: artifact/harness-skill#RULE-skill-001, snapshot/harness-snapshot#RULE-snapshot-001, worker/harness-worker#RULE-worker-001
- **Paths**: packages/agent-core/src/muad_agent_core/tools/**, packages/agent-core/src/muad_agent_core/agent/runner.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py, tests/agent_core/test_outcome_parser.py, tests/agent_worker/test_execution_outcomes.py, tests/agent_runtime/test_tool_audit_business.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "unit",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_outcome_parser.py", "tests/agent_worker/test_execution_outcomes.py", "tests/agent_runtime/test_tool_audit_business.py"],
    "timeout": 600,
    "parallel": true
  }
]
```

### Description

把 Skill/业务执行的 `status/exit_code/结构化 error` 收敛成 `ExecutionOutcome`，由 `ToolDefinition.outcome_parser` 只判定一次；只有 `target=skill` 的业务执行产生 `CONTENT` 失败，`workspace_shell`/`skill_draft_shell` 的非零退出只作证据。失败跟踪改为结构化 `last_failure` + `identical_failures`，不再由 runner 解析任意文本。

### Checklist

- [ ] 定义 `ExecutionOutcome`（business_status/failure_layer/error/retryable/exit_code/evidence）。
- [ ] `execute(target=skill)`/`execute_skill`/`run_skill_script` 设置 `outcome_parser`；draft/workspace 不设置。
- [ ] Skill 失败信封解析 `code/retryable`；缺失按确定性；鉴权/凭据立即停止。
- [ ] `tool_call_audit` 写入 business_status/failure_layer/exit_code/output_truncated/evidence_preview。
- [ ] runner 失败跟踪消费 outcome；`workspace_shell`/`skill_draft_shell` 退出 1 不触发 `no_progress`。
- [ ] 证据 preview 有界、脱敏；`output_truncated` 可见。
- [ ] 补单元/回归用例，覆盖分类矩阵与诊断命令非零退出。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created
- [2026-11-18T12:57:02.425Z] started tests=de6fd0cfc10b
- [2026-11-18T12:58:38.966Z] block: 等待 unified-artifact-delivery 定稿与联合契约评审后再统一开工

### Verification

- 尚未执行

## TASK-009: 终止快照与统一收尾漏斗

- **Status**: draft
- **Depends**: TASK-008
- **Source**: run-failure-reporting/run-failure-reporting.design.md#4.3, #4.4
- **Scenarios**: run-failure-reporting:S-04, run-failure-reporting:S-05, run-failure-reporting:S-08
- **Spec-Refs**: snapshot/harness-snapshot#RULE-snapshot-001, worker/harness-worker#RULE-worker-001, api/harness-api#RULE-api-001
- **Paths**: packages/agent-core/src/muad_agent_core/agent/runner.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py, tests/agent_core/test_termination_snapshot.py, tests/agent_runtime/test_finalization_funnel.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runner",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_termination_snapshot.py", "tests/agent_runtime/test_finalization_funnel.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

引入 `TerminationSnapshot`/`TerminationReason`，把 turn/tool budget、deadline、no_progress、cancel、model/validation/empty/shutdown 全部收敛到同一个 finalization funnel；`decisive_failure` 只在 `NO_PROGRESS` 时取 `last_failure`，取消写 `RUN_COMPLETED(CANCELLED)`。

### Checklist

- [ ] `AgentRunState` 记录结构化失败状态与 pending calls。
- [ ] `TerminationSnapshot` 覆盖计数、budget kind、失败状态、pending。
- [ ] 所有异常/终止出口挂同一 snapshot；挂起不是终止。
- [ ] `TerminationReason` 映射到现有错误码；budget subtype 从计数器推导。
- [ ] 取消不写 `RUN_FAILED`；未执行调用不进失败计数。
- [ ] 补 Runner 用例覆盖所有出口。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-010: 收尾事实与平台直出

- **Status**: draft
- **Depends**: TASK-009
- **Source**: run-failure-reporting/run-failure-reporting.design.md#4.4, #4.5, #4.6
- **Scenarios**: run-failure-reporting:S-01, run-failure-reporting:S-04, run-failure-reporting:S-05, run-failure-reporting:S-06, run-failure-reporting:S-07, run-failure-reporting:S-08, run-failure-reporting:E-01, run-failure-reporting:E-02
- **Spec-Refs**: snapshot/harness-snapshot#RULE-snapshot-001, logging/harness-log#RULE-log-001, api/harness-api#RULE-api-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/finalization/**, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py, packages/agent-core/src/muad_agent_core/agent/runner.py, tests/agent_runtime/test_finalization_facts.py, tests/agent_runtime/test_finalization_render.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "facts",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_finalization_facts.py", "tests/agent_runtime/test_finalization_render.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

失败收尾由平台直出：`FinalizationFacts` 生成终止原因、用量、工具结果、业务失败证据、证据状态、待执行调用与完成状态；`FinalizationRenderer` 生成 `ASSISTANT_MESSAGE` + `RUN_FAILED`。失败后不追加 USER 指令、不调用收尾模型、不依赖压缩历史。

### Checklist

- [ ] `FinalizationFacts` 结构与 `evidence_status` 判定。
- [ ] 各 `TerminationReason` 的确定性文案；失败+预算以失败为主。
- [ ] 平台直出；删除 `_conclude` USER 指令与失败后模型收尾。
- [ ] 完成状态只用现有 `output_contract`；无依据写 UNKNOWN。
- [ ] 事实块不受压缩影响；压缩失败回退原历史。
- [ ] 持久化 `RUN_FAILED.finalization` 与 `ASSISTANT_MESSAGE`。
- [ ] 补 S-01/S-04/S-06/S-08 回归。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-011: 导出与诊断业务字段

- **Status**: draft
- **Depends**: TASK-008, TASK-010
- **Source**: run-failure-reporting/run-failure-reporting.design.md#4.7
- **Scenarios**: run-failure-reporting:E-01, run-failure-reporting:S-16
- **Spec-Refs**: logging/harness-log#RULE-log-001, test/harness-test
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/execution_export_view.py, apps/console-platform/backend/src/muad_console_platform/application/execution_diagnosis.py, apps/console-platform/backend/src/muad_console_platform/application/execution_export_render.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/execution_export_repository.py, tests/console_platform/test_execution_export_business_fields.py, tests/console_platform/test_execution_diagnosis.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "export",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_execution_export_business_fields.py", "tests/console_platform/test_execution_diagnosis.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

让链路分析包能区分工具层/内容层失败：`calls.csv` 暴露 business_status/exit_code/failure_layer/output_truncated/evidence_preview；`failures.csv` 只列 confirmed failure；`execution_diagnosis` 能发现 transport OK + business FAILED；timeline 截断不丢 failure evidence。

### Checklist

- [ ] `calls.csv`/report.json 暴露业务字段。
- [ ] `execution_diagnosis` 收内容层失败候选。
- [ ] `failures.csv` 过滤 UNKNOWN/NOT_APPLICABLE。
- [ ] failure evidence 在截断时保留首个/最后/confirmed 摘要。
- [ ] `data_quality` 区分 DB 完整与包截断。
- [ ] 补导出/诊断用例。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-012: Job/Payload 模型与旧模式迁移

- **Status**: draft
- **Depends**:
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.2, #10.3, #10.12
- **Scenarios**: run-failure-reporting:S-11, run-failure-reporting:S-14, run-failure-reporting:S-16
- **Spec-Refs**: worker/harness-worker#RULE-worker-001, snapshot/harness-snapshot#RULE-snapshot-001, time/harness-time#RULE-time-001, worker/harness-worker#RULE-005, persistence/harness-data#RULE-003
- **Paths**: packages/contracts/src/muad_contracts/scheduling.py, packages/contracts/src/muad_contracts/snapshot.py, apps/agent-worker/src/muad_agent_worker/infrastructure/models/task.py, apps/agent-worker/src/muad_agent_worker/application/schedule_*.py, apps/agent-worker/src/muad_agent_worker/scheduler/fire.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, tests/contracts/test_scheduled_job_payload.py, tests/agent_worker/test_scheduled_v2_fire.py, tests/agent_worker/test_scheduled_v2_contracts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "contracts",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/contracts/test_scheduled_job_payload.py", "tests/agent_worker/test_scheduled_v2_fire.py", "tests/agent_worker/test_scheduled_v2_contracts.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

把 Job 从“三个半成品 mode”升级为 `schedule + payload + session + delivery + policy`：payload 为 `REMINDER`/`SKILL_PLAN`/`AGENT_TURN`；`mode` 成为内部派生字段。`delivery_intent` 只有 `NONE | PUSH`（本期 WECOM），Web pull 由任务中心读投影天然存在；Schedule 路由独立于创建入口（该路由 TASK 归 `unified-artifact-delivery`）。开发期直接迁移旧 `SKILL`/`AGENTIC`，不保留双读。

### Checklist

- [ ] 契约定义 `ScheduleJob`/`PayloadKind`/`SessionTarget`/`DeliveryPlan`/`ToolPolicy`。
- [ ] DB 迁移：payload kind、session target、`delivery_intent`/`delivery_route_id`、tools policy、last result/scratch 字段。
- [ ] 旧 `mode=SKILL/AGENTIC/REMINDER` 开发期直接迁移为 `SKILL_PLAN/AGENT_TURN/REMINDER`。
- [ ] `draft_schedule` 接受 `SchedulePlan`，不再把 mode 作为用户选项。
- [ ] `get_schedule` 返回 resolved payload/plan/session/delivery_intent/tools/budget source；Web pull 不是 delivery_intent。
- [ ] 测试：旧数据迁移、payload 形状、session/delivery 分离。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-013: Authoring 编译器与意图识别

- **Status**: draft
- **Depends**: TASK-012, TASK-005
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.4
- **Scenarios**: run-failure-reporting:S-11, run-failure-reporting:S-15
- **Spec-Refs**: artifact/harness-skill#RULE-skill-001, time/harness-time#RULE-time-001, snapshot/harness-snapshot#RULE-snapshot-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/schedule_authoring.py, apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, packages/contracts/src/muad_contracts/scheduling.py, tests/agent_runtime/test_schedule_authoring_compiler.py, tests/contracts/test_schedule_plan.py, tests/agent_worker/test_schedule_intent_golden.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "authoring",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_schedule_authoring_compiler.py", "tests/contracts/test_schedule_plan.py", "tests/agent_worker/test_schedule_intent_golden.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

Agent 输出结构化 `SchedulePlan`（kind/skill/input/argv/window/session/delivery_intent/confidence/reason）；平台校验 Skill 契约、entrypoint、cron、时区、路由、工具策略；低置信度问业务问题，不暴露 mode 下拉。Schedule 路由的入口相等限制与目标列表由 `unified-artifact-delivery` 的 Schedule TASK 拥有。

### Checklist

- [ ] `SchedulePlan` 契约与校验器（skill/entrypoint/input/argv/cron/timezone/delivery_intent/tools）。
- [ ] 意图决策树：通知/固定 Skill/开放任务；歧义问业务问题。
- [ ] 黄金集：日报→SKILL_PLAN；汇总→AGENT_TURN；提醒→REMINDER；歧义→问题。
- [ ] 校验失败返回结构化字段错误，模型可修正一次；仍失败降级或让用户确认。
- [ ] 卡片返回 resolved plan、confidence、reason、budget source。
- [ ] 不要求用户/模型选 `mode`。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-014: 真实试跑与从成功执行提升

- **Status**: draft
- **Depends**: TASK-013, TASK-002, TASK-004, TASK-005
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.5, #10.15
- **Scenarios**: run-failure-reporting:S-12, run-failure-reporting:S-13
- **Spec-Refs**: worker/harness-worker#RULE-worker-001, artifact/harness-skill#RULE-skill-001, test/harness-test#RULE-001, test/harness-test#RULE-002
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/schedule_confirm.py, apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/console-platform/backend/src/muad_console_platform/application/schedule_view.py, tests/agent_worker/test_schedule_test_run.py, tests/agent_runtime/test_schedule_promotion.py, tests/frontend/test_schedule_preview_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "test-run",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_schedule_test_run.py", "tests/agent_runtime/test_schedule_promotion.py", "tests/frontend/test_schedule_preview_contract.py"],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

创建前执行一次真实运行并投递；从一次成功对话调用提升为定时任务（复用 skill/input/argv/platform，绑定时间变量）；试跑失败不创建 Job。

### Checklist

- [ ] 试跑走真实 Skill/Agent、真实产物、真实投递；不是渲染预览。
- [ ] 试跑失败不建 Job；展示失败事实与修复建议。
- [ ] promotion 从成功 Run 的 `execute(target=skill)` 复用真实参数。
- [ ] 日期参数绑定 `{{previous_day}}` 等；用户确认后创建。
- [ ] Job 创建后默认 enabled；失败由通知/退避/自动停用兜底。
- [ ] 卡片展示试跑结果、计划、预算来源、下次触发。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-015: 草稿诊断、版本与作用域

- **Status**: draft
- **Depends**: TASK-013
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.4
- **Scenarios**: run-failure-reporting:S-12, run-failure-reporting:S-13
- **Spec-Refs**: artifact/harness-skill#RULE-skill-001, api/harness-api#RULE-api-001
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/skill_draft_service.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_draft_store.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/skill_draft_repository.py, apps/agent-runtime/src/muad_agent_runtime/application/draft_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_draft_client.py, tests/console_platform/test_skill_draft_diagnostics.py, tests/agent_runtime/test_draft_tools.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "draft",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_skill_draft_diagnostics.py", "tests/agent_runtime/test_draft_tools.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

拆分 `DRAFT_NOT_FOUND`/`DRAFT_FILE_NOT_FOUND`/`DRAFT_STORAGE_MISSING`；`read(path="")` 返回根目录列表；`get/status` 返回未提交变更与生效 artifact；`get` 按 agent 作用域；目录缺失可恢复；`get/read` 刷新活动标记。

### Checklist

- [ ] 新错误码与 data（path/draft_id/repair）。
- [ ] 根目录列表与工具描述（路径基准）。
- [ ] `has_unsubmitted_changes`/`effective_artifact_version`/`effective_artifact_id`。
- [ ] `get`/`get_for_update` 按 tenant+owner+agent；key 解析不一致显式报错。
- [ ] 目录缺失恢复动作（import artifact）；`get/read` 刷新活动标记。
- [ ] 工具描述写明 submit 不释放草稿。
- [ ] 测试：get/read 区分、根列表、agent 作用域、目录缺失、版本字段。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-016: SKILL_PLAN 确定性运行时

- **Status**: draft
- **Depends**: TASK-012, TASK-013, TASK-001, TASK-002, TASK-004
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.3, #10.6
- **Scenarios**: run-failure-reporting:S-16, run-failure-reporting:E-03
- **Spec-Refs**: snapshot/harness-snapshot#RULE-snapshot-001, artifact/harness-skill#RULE-skill-001, time/harness-time#RULE-time-001
- **Paths**: apps/agent-worker/src/muad_agent_worker/scheduler/fire.py, apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, apps/agent-worker/src/muad_agent_worker/worker/executor.py, packages/contracts/src/muad_contracts/scheduling.py, tests/agent_worker/test_scheduled_skill_plan.py, tests/agent_worker/test_skill_argv.py, tests/contracts/test_scheduled_v2_contracts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "skill-plan",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_scheduled_skill_plan.py", "tests/agent_worker/test_skill_argv.py", "tests/contracts/test_scheduled_v2_contracts.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

SKILL_PLAN 在 fire 时渲染 `input`/`argv`/window 并冻结进 execution snapshot；无模型；stdin 只含业务 input；重试幂等；失败沿用 TASK-008 的 ExecutionOutcome 契约。

### Checklist

- [ ] `SkillExecutionSpec`/payload 增加 `argv_template` 与 window。
- [ ] fire 渲染并写 `execution_snapshot_json["argv"]`，纳入 hash。
- [ ] `_skill_request` 只传 `input_json["input"]`。
- [ ] 重试复用同一 argv/input；时间窗口不变。
- [ ] 旧 Revision 无 argv 视为空；旧包走 stdin。
- [ ] 测试：argv、stdin、时间窗口、重试、真实 Fire 形状。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-017: AGENT_TURN prompt 模式

- **Status**: draft
- **Depends**: TASK-012, TASK-013, TASK-002, TASK-003, TASK-004
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.3, #10.7, #10.8
- **Scenarios**: run-failure-reporting:S-14, run-failure-reporting:S-17, run-failure-reporting:E-03
- **Spec-Refs**: worker/harness-worker#RULE-worker-001, snapshot/harness-snapshot#RULE-snapshot-001, time/harness-time#RULE-time-001
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-worker/src/muad_agent_worker/application/agentic/snapshot.py, apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py, packages/agent-core/src/muad_agent_core/agent/runner.py, tests/agent_worker/test_scheduled_agent_turn.py, tests/agent_worker/test_agentic_skill_argv.py, tests/agent_core/test_prompt_unattended_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "agent-turn",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_scheduled_agent_turn.py", "tests/agent_worker/test_agentic_skill_argv.py", "tests/agent_core/test_prompt_unattended_contract.py"],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

AGENT_TURN 是完整的 prompt 模式：prompt + 时间行 + 窗口 + 无人值守契约 + 上次结果/scratch；完整工具面；session 目标；`record_result(no_change)` 结构化静默；用户可见文本必须直白；文件产物独立于文本静默。

### Checklist

- [ ] prompt 构造：时间行、窗口、无人值守契约、上次结果、scratch。
- [ ] `current_time` 注册；调度时区；重试使用冻结窗口。
- [ ] skill 工具 `{input, argv}`；平台只读工具可注册。
- [ ] session 目标 isolated/current/session/main；current 读取有界上下文。
- [ ] 无变化用 `record_result(no_change)`；平台抑制文本，不向用户发送内部 token；任务中心仍记录。
- [ ] 附件仍进入消息，Web 任务中心可读，企微 push 按 delivery_intent；用户可见文本必须直白。
- [ ] 失败沿用 finalization funnel。
- [ ] 测试：prompt、时间、工具、session、静默、失败。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-018: 预算与工具策略来自平台设置

- **Status**: draft
- **Depends**: TASK-012, TASK-017, TASK-002
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.9
- **Scenarios**: run-failure-reporting:S-18
- **Spec-Refs**: snapshot/harness-snapshot#RULE-snapshot-001, worker/harness-worker#RULE-worker-001
- **Paths**: packages/contracts/src/muad_contracts/scheduling.py, packages/contracts/src/muad_contracts/snapshot.py, apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/console-platform/backend/src/muad_console_platform/application/schedule_view.py, apps/console-platform/frontend/src/modules/task-schedule/**, tests/agent_worker/test_schedule_budget_from_settings.py, tests/agent_worker/test_schedule_tool_policy.py, tests/console_platform/test_schedule_view_budget.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "budget-tools",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_schedule_budget_from_settings.py", "tests/agent_worker/test_schedule_tool_policy.py", "tests/console_platform/test_schedule_view_budget.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

预算唯一来源 `PlatformSettings.agent`；admission 解析并冻结；`max_tokens=0` 显示“不限制”；旧 budget 只读不消费；`tools_allow` per-job，平台只读工具是一等工具。

### Checklist

- [ ] 预算映射 `max_turns`/`max_tool_calls`/`deadline_ms`/`max_model_retries`/`max_identical_failures`。
- [ ] `agent_policy(frozen)` 只读快照；旧 budget 不消费。
- [ ] `tools_allow` 默认 `*` 跟随 owner；agent 创建任务不越权。
- [ ] `current_time`/任务查询等平台只读工具可注册。
- [ ] 卡片显示系统默认来源；`max_tokens=0` 显示“不限制”。
- [ ] 测试：设置映射、冻结、旧 budget、工具收紧、撤权。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-019: 定时任务导出与失败诊断

- **Status**: draft
- **Depends**: TASK-008, TASK-011, TASK-017, TASK-004
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.10
- **Scenarios**: run-failure-reporting:S-19
- **Spec-Refs**: logging/harness-log#RULE-log-001, test/harness-test
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/execution_export_view.py, apps/console-platform/backend/src/muad_console_platform/application/execution_export_render.py, apps/console-platform/backend/src/muad_console_platform/application/execution_diagnosis.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/execution_export_repository.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_executor.py, tests/console_platform/test_execution_export_task_events.py, tests/agent_worker/test_agentic_no_progress_message.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "task-export",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_execution_export_task_events.py", "tests/agent_worker/test_agentic_no_progress_message.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

TASK 导出的 timeline/events 来自 task_event；`tasks[].events` 按 entity_id 归组；TASK 用 started_at/finished_at；有 task_id 就采集 audits；NO_PROGRESS 带 last_failure；UNVERIFIED 保留 cause；CANCELLED 文案修正。

### Checklist

- [ ] `_timeline` 合并 task_event；`_tasks` 用 entity_id。
- [ ] `_execution` 对 TASK 用 started_at/finished_at。
- [ ] TASK audits 采集不依赖 source_run_id。
- [ ] NO_PROGRESS/UNVERIFIED 带原始 cause。
- [ ] CANCELLED 文案修正。
- [ ] 测试：TASK 导出事件/调用/开始时间、NO_PROGRESS 原因、CANCELLED 文案。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-020: Fake MSSP 平台模式与只读端点

- **Status**: draft
- **Depends**:
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.14
- **Scenarios**: run-failure-reporting:E-03
- **Spec-Refs**: platform/harness-project-platform, test/harness-test
- **Paths**: tools/fake-business-platform/server.py, tools/fake-business-platform/mssp.py, tools/fake-business-platform/README.md, tests/tools/test_fake_mssp_platform.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "fake-mssp",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/tools/test_fake_mssp_platform.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

在 fake business platform 增加 `--platform mssp`：Host 路由、固定 CSRF、`X-MSSW-Company-Id`；实现 `daily-duty-report-mssp` 使用的只读端点与 XDR `alert_table`/`alert_count`。

### Checklist

- [ ] `--platform mssp` 启动与就绪信息。
- [ ] 只读端点：company/service/ck/event/asset/threat/wecom query。
- [ ] XDR 代理端点：alert_table/alert_count。
- [ ] 认证与 Host/CSRF/Company-Id 校验；业务码与 HTTP 状态分离。
- [ ] README 更新启动示例与端点表。
- [ ] 测试：真实 HTTP 请求覆盖端点与认证失败。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-021: Fake MSSP 业务场景与 fixtures

- **Status**: draft
- **Depends**: TASK-020
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.14
- **Scenarios**: run-failure-reporting:E-03
- **Spec-Refs**: platform/harness-project-platform, test/harness-test
- **Paths**: tools/fake-business-platform/mssp.py, tools/fake-business-platform/fixtures/**, tests/tools/test_fake_mssp_scenarios.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "scenarios",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/tools/test_fake_mssp_scenarios.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

为 fake MSSP 增加确定性 fixtures：全资产/非全资产/空数据、场景 1-11、威胁实体、事件、XDR 告警；注入 401/500/空数据；写操作默认关闭。

### Checklist

- [ ] 全资产客户：service_info 命中 codes；计数/场景/实体/事件有值。
- [ ] 非全资产客户：无全资产服务项；事件/实体有值。
- [ ] 空数据客户：计数 0；报告仍可生成。
- [ ] 失败注入：401/500/超时；业务码与 HTTP 状态分离。
- [ ] 写操作默认关闭；开启后按幂等键只发一次。
- [ ] 测试：fixtures 可重复、场景矩阵断言。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-022: 本地回归 Skill（argv + stdin 双入口）

- **Status**: draft
- **Depends**: TASK-016, TASK-021
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.14
- **Scenarios**: run-failure-reporting:E-03
- **Spec-Refs**: artifact/harness-skill#RULE-skill-001, time/harness-time#RULE-time-001, artifact/harness-skill#RULE-002, artifact/harness-skill#RULE-003
- **Paths**: tests/agent_worker/test_local_report_skill.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "local-skill",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_local_report_skill.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

写一个与 `daily-duty-report-mssp` 同形态的本地回归 Skill：`scripts/run.py` 保留 argparse，`scripts/cron_entry.py` 读 stdin 并兼容 Fire 信封与 input；两者共用报告逻辑，调用 fake MSSP 生成简化报告。

### Checklist

- [ ] `SKILL.md`/`muad.skill.json`/`scripts/run.py`/`scripts/cron_entry.py`。
- [ ] `run.py` 支持 `--company-id/--date/--no-wecom-push`。
- [ ] `cron_entry.py` 读 `payload["input"]`，兼容顶层字段。
- [ ] 报告结构含头三项、场景判定、实体、事件、附件占位。
- [ ] 测试：argv 入口、stdin 入口、空数据、401/500。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-023: 定时路径端到端回归

- **Status**: draft
- **Depends**: TASK-013, TASK-014, TASK-016, TASK-017, TASK-018, TASK-022, TASK-005, TASK-007
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.14, #10.15
- **Scenarios**: run-failure-reporting:S-11, run-failure-reporting:S-12, run-failure-reporting:S-13, run-failure-reporting:S-14, run-failure-reporting:S-16, run-failure-reporting:S-17, run-failure-reporting:S-18, run-failure-reporting:E-03
- **Spec-Refs**: worker/harness-worker#RULE-worker-001, snapshot/harness-snapshot#RULE-snapshot-001, time/harness-time#RULE-time-001, test/harness-test, test/harness-test#RULE-001, test/harness-test#RULE-002
- **Paths**: tests/agent_worker/test_scheduled_skill_plan_e2e.py, tests/agent_worker/test_scheduled_agent_turn_e2e.py, tests/agent_worker/test_schedule_authoring_e2e.py, tests/agent_worker/helpers.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "e2e",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_scheduled_skill_plan_e2e.py", "tests/agent_worker/test_scheduled_agent_turn_e2e.py", "tests/agent_worker/test_schedule_authoring_e2e.py"],
    "timeout": 1200,
    "parallel": false
  }
]
```

### Description

用本地回归 Skill + fake MSSP 证明四条路径：authoring 推断/试跑、SKILL_PLAN（argv/stdin）、AGENT_TURN（prompt+tools+time+session）、promotion；并验证 09:00 窗口、重试/延迟、预算来源。

### Checklist

- [ ] 从业务意图生成计划，不选 mode。
- [ ] 试跑真实产出；失败不建 Job。
- [ ] SKILL_PLAN argv/stdin 成功；重试幂等。
- [ ] AGENT_TURN 时间/工具/session/静默成功。
- [ ] promotion 复用成功 Run。
- [ ] 09:00 窗口、延迟/重试、DST 边界。
- [ ] 预算/工具策略来自设置。
- [ ] 测试可重复，不访问内网。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

## TASK-024: 文档、验收与跨需求契约

- **Status**: draft
- **Depends**: TASK-015, TASK-019, TASK-023, TASK-005, TASK-007
- **Source**: run-failure-reporting/run-failure-reporting.design.md#10.13, #10.17
- **Scenarios**: run-failure-reporting:S-19, run-failure-reporting:E-04
- **Spec-Refs**: test/harness-test
- **Paths**: docs/operations/scheduled-skill-contract.md, docs/operations/legacy-skill-migration.md, .code-flow/tasks/2026-11-18/run-failure-reporting/**, .code-flow/tasks/2026-11-17/unified-artifact-delivery/**, tests/test_docs_scheduled_skill_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "docs-contract",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_docs_scheduled_skill_contract.py"],
    "timeout": 300,
    "parallel": true
  }
]
```

### Description

把 Job/payload、authoring、试跑/提升、SKILL_PLAN/AGENT_TURN、session/tools/budget/quiet 写成运维文档；与 `unified-artifact-delivery` v2.0 对齐共享契约（MessagePayload/AttachmentRef/SurfacePlan/MessageDelivery/TaskArtifactRef.deliveries/JobDelivery）与联合 E2E；补人工验收记录。

### Checklist

- [ ] `scheduled-skill-contract.md`：payload/authoring/试跑/提升/时间/工具/预算/静默。
- [ ] `legacy-skill-migration.md` 增加定时任务章节。
- [ ] 共享契约文档：MessagePayload/AttachmentRef/SurfacePlan/MessageDelivery/TaskArtifactRef.deliveries/JobDelivery/测试运行。
- [ ] 联合 E2E：Web 创建定时任务推企微；企微创建任务 Web 可读；直接对话按来源；无绑定 NONE；无变化静默 + 附件。
- [ ] 人工验收记录 S-11..S-19、E-03/E-04。
- [ ] 用户内网任务成功证据（报告/文件/任务 id）。

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行
