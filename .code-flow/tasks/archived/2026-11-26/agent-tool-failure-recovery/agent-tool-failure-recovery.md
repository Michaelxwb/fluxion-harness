---
{"version":1,"requirement":"agent-tool-failure-recovery"}
---
# Tasks: agent-tool-failure-recovery

## 需求范围

将工具失败后的处理从“失败即终止”收敛为共享的 `CONTINUE / WAIT / STOP` 控制语义：普通工具失败进入 Agent 恢复回合；频率限流在 Run 预算内等待/退避；异步工具按 `ToolOperation`/`RunContinuation` 续接；已发送且结果未知的副作用禁止原调用重做；业务执行与消息交付分离；身份、事实持久化、模型、deadline、租约和总预算等硬故障确定性收尾。

不引入 MSSClaw 的 `longtask:<id>` 会话模型，不新增独立重试服务，不保留旧事件双读/双写或宿主专用兼容层。

## TASK-001: 统一失败控制与持久化契约

- **Status**: done
- **Depends**:
- **Source**: agent-tool-failure-recovery.design.md#2.3.2、#3.2.2、#3.3、#3.4.2
- **Scenarios**: E-03, E-06, B-01
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/**, packages/agent-core/src/muad_agent_core/agent/termination.py, packages/contracts/src/muad_contracts/tool_events.py, packages/contracts/src/muad_contracts/tool_runtime.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-worker/src/muad_agent_worker/application/agentic/events.py, tests/agent_core/test_tool_contract.py, tests/agent_core/test_tool_events.py, tests/contracts/test_capacity_webchat_event_contracts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "failure-control-contract",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_tool_contract.py",
      "tests/agent_core/test_tool_events.py",
      "tests/contracts/test_capacity_webchat_event_contracts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

建立共享 FailureControl 投影，至少区分：Agent 是否继续、原调用是否重试、等待/重试提示、外部请求是否已发送、结果是否确定、失败作用域、证据完整性和安全处置。将该投影接入 `ToolResult`、`ExecutionOutcome`、v2 tool event 和 checkpoint/recovery payload，保证 Runtime/Worker 可从持久事实重建同一控制结论。

### Checklist

- [x] `retryable=false` 不再单独推出 Run 终止。
- [x] `sent=true && outcome_known=false` 能阻止原调用重做，同时允许安全查询/替代路径。
- [x] 失败控制、证据状态、operation/delivery 关联和硬停止原因可序列化、反序列化并保持版本契约。
- [x] 多工具批次的成功、失败和未执行结果可以逐 call 对账。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "工具失败控制必须区分 Agent 是否继续、原调用是否允许重试、是否需要等待、失败作用域以及是否必须由宿主硬停止。retryable=false 禁止原调用重做，不单独推出 Run 终止；身份或能力完整性受损、关键事实不可持久化、模型不可用、执行权丢失、截止时间到期、总预算耗尽或停止未确认时，由宿主安全收尾。",
    "evidence": "agent-tool-failure-recovery.design.md §2.3.2、§3.2.2；E-01、E-03、E-08"
  },
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "工具完成事件记录失败控制事实；检查点保留失败状态、待完成操作和调用去重所需事实。实时执行与检查点恢复使用同一控制判定，不能通过重解析当前工具定义或更换入口重做未知外部操作。",
    "evidence": "agent-tool-failure-recovery.design.md §3.2.2、§3.3；E-02、E-03、S-04"
  }
]
```

### Log

- [2026-11-26] created
- [2026-11-26T02:33:21.496Z] started tests=a5d0aa36f683

### Verification

- 尚未执行
- 2026-11-26T02:51:06.114Z PASS commands=1/1 executed=1 deduped=0 elapsed=6331ms tests=a5d0aa36f683 head=cc748f89c6e5 dirty=1
  - failure-control-contract PASS exit=0
## TASK-002: Runner 恢复回合与频率限流等待

- **Status**: done
- **Depends**: TASK-001
- **Source**: agent-tool-failure-recovery.design.md#3.2.2、#3.2.3、#3.5
- **Scenarios**: S-01, S-02, S-03, E-04, B-02
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, mcp/harness-mcp, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/agent/failure.py, packages/agent-core/src/muad_agent_core/model/budget.py, packages/platform-sdk/src/muad_platform_sdk/mcp_client.py, packages/agent-core/src/muad_agent_core/skill/sandboxed.py, tests/agent_core/test_runner.py, tests/agent_core/test_failure_state.py, tests/agent_core/test_outcome_parser.py, tests/agent_runtime/test_mcp_budget_transport.py, tests/agent_runtime/test_mcp_error_classification.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runner-recovery-and-rate-limit",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_failure_state.py",
      "tests/agent_core/test_outcome_parser.py",
      "tests/agent_runtime/test_mcp_budget_transport.py",
      "tests/agent_runtime/test_mcp_error_classification.py",
      "tests/agent_runtime/test_mcp_headers.py",
      "tests/agent_runtime/test_execution_audit.py",
      "tests/agent_worker/test_agentic_mcp_budget.py",
      "tests/agent_worker/test_agentic_mcp_parity.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

调整 Runner 路由：普通工具失败在回合收口后进入下一次模型决策，不直接转 `NO_PROGRESS`；相同失败和无进展仍使用既有预算/止损机制。将 `LOCAL_RATE_LIMITED`、平台 429 和 `Retry-After` 转换为有界等待/退避重试，等待期间检查取消、deadline、租约和总预算；`LOCAL_BUDGET_EXHAUSTED`、停止确认失败和安全故障保持硬停止。

### Checklist

- [x] 首次工具失败至少有一次正常 Agent 恢复回合。
- [x] Agent 可改参数、换工具、查询状态或直接给出诚实结论。
- [x] 相同失败重复时不会无限循环，原调用身份进入阻止集合。
- [x] 频率限流在剩余 Run 预算内等待/退避，超过 deadline 不再发请求。
- [x] 普通限流不直接产生 `run.failed`；总预算耗尽仍确定性收尾。

### Spec Delta

```json
[
  {
    "id": "DELTA-003",
    "op": "update",
    "target": "arch/harness-arch",
    "ref": "RULE-004",
    "before": "聊天 Skill 因本次调用中经出站准入确认的平台接口窗口限流而中断时，超限请求必须继续在外发前拦截并审计，工具结果明确记录查询受限。仅由该限流结束的聊天轮次保留已有正文，在正常助手回复末尾追加受限提示并停止派发后续工具；仅展示已验证可交付的部分结果。独立故障继续按其真实原因收尾。",
    "rule": "超出平台出站准入窗口的请求必须在外发前拦截并审计。对被判定为可等待且可安全重试的工具调用，在冻结 Run 预算、截止时间、取消和租约约束内按 Retry-After 或退避等待，沿用同一调用身份重试；不满足门禁时不得重发。聊天 Skill 仅因出站窗口限流结束时，保留已有正文和经核实可交付的部分结果，在回复末尾追加受限说明并停止派发后续工具；独立故障按其真实原因收尾。",
    "evidence": "agent-tool-failure-recovery.design.md §3.2.3；S-03、B-02；当前 MCP retry_after 与 LOCAL_RATE_LIMITED 实现"
  }
]
```

### Log

- [2026-11-26] created
- [2026-11-26T02:52:08.785Z] started tests=058b03d88f54
- [2026-11-26T08:21:21.948Z] reopen: 浏览器验收发现安全 MCP 429 未进入重试分支，补回归并复验
- [2026-11-26T08:25:57.108Z] reopen: Docker WebChat 实际复验发现同一调用重试后的审计缓冲错误，补生产链路回归

### Verification

- 尚未执行
- 2026-11-26T03:02:49.062Z FAIL commands=0/1 executed=1 deduped=0 elapsed=6943ms tests=058b03d88f54 head=cc748f89c6e5 dirty=1
  - runner-recovery-and-rate-limit FAIL exit=1
- 2026-11-26T03:04:30.869Z PASS commands=1/1 executed=1 deduped=0 elapsed=6954ms tests=058b03d88f54 head=cc748f89c6e5 dirty=1
  - runner-recovery-and-rate-limit PASS exit=0
- 2026-11-26T08:23:37.506Z PASS commands=1/1 executed=1 deduped=0 elapsed=13559ms tests=543e6bb51cb0 head=cc748f89c6e5 dirty=1 tests-changed
  - runner-recovery-and-rate-limit PASS exit=0
- 2026-11-26T08:26:09.679Z PASS commands=1/1 executed=1 deduped=0 elapsed=12278ms tests=53568f972d3a head=cc748f89c6e5 dirty=1 tests-changed
  - runner-recovery-and-rate-limit PASS exit=0
## TASK-003: 异步 Operation、Run Continuation 与取消对账

- **Status**: done
- **Depends**: TASK-001, TASK-002
- **Source**: agent-tool-failure-recovery.design.md#3.2.4、#3.3、#3.5
- **Scenarios**: S-04, E-05, B-03
- **Spec-Refs**: api/harness-api, snapshot/harness-snapshot, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/async_tools/**, apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/async_tools.py, apps/agent-worker/src/muad_agent_worker/infrastructure/task_operation_client.py, apps/agent-worker/src/muad_agent_worker/worker/async_sweep.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_executor.py, tests/agent_runtime/test_scheduled_v2_task_operations.py, tests/agent_runtime/test_scheduled_v2_async_resume.py, tests/agent_runtime/test_tool_wait_state_machine.py, tests/agent_worker/test_runtime_operation_races.py, tests/agent_worker/test_task_operation_identity.py, tests/contracts/test_async_background_contracts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "async-operation-recovery",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_scheduled_v2_task_operations.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/agent_runtime/test_tool_wait_state_machine.py",
      "tests/agent_worker/test_runtime_operation_races.py",
      "tests/agent_worker/test_task_operation_identity.py",
      "tests/contracts/test_async_background_contracts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

将异步工具和后台 Task 的恢复统一到现有 `ToolOperation`、`RunContinuation`、`WAITING_TOOL`、`JOIN/DETACH` 和结果 Inbox。取消 operation 后只发送幂等取消控制，不再提交该 operation 的业务调用；迟到结果标记并保留对账事实，不改写已终态 Run/Task，也不重新触发 Agent。

### Checklist

- [x] `JOIN` 进入 `WAITING_TOOL` 并在结果到达后恢复。
- [x] `DETACH` 不阻塞父 Run，但子 Task/Operation 仍可追踪和交付。
- [x] 同一 operation 的提交、结果和取消具有稳定身份与幂等行为。
- [x] 取消后拒绝新业务调用；迟到结果不改写终态。
- [x] 新连接/新 Worker 恢复使用持久事实，不从当前配置重新解释 operation。

### Spec Delta

```json
[]
```

### Log

- [2026-11-26] created
- [2026-11-26T03:05:42.753Z] started tests=2e3b52ae319b

### Verification

- 尚未执行
- 2026-11-26T03:07:27.152Z FAIL commands=0/1 executed=1 deduped=0 elapsed=2159ms tests=2e3b52ae319b head=cc748f89c6e5 dirty=1
  - async-operation-recovery FAIL exit=4
- 2026-11-26T03:10:05.397Z PASS commands=1/1 executed=1 deduped=0 elapsed=30931ms tests=b832003d2b39 head=cc748f89c6e5 dirty=1 tests-changed
  - async-operation-recovery PASS exit=0
## TASK-004: 业务结果与 Delivery Outbox 分离

- **Status**: done
- **Depends**: TASK-001
- **Source**: agent-tool-failure-recovery.design.md#3.2.5、#3.3、#3.4.2、#3.5
- **Scenarios**: S-05, E-07
- **Spec-Refs**: im/harness-im, artifact/harness-skill, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, apps/agent-runtime/src/muad_agent_runtime/application/message_delivery.py, apps/agent-runtime/src/muad_agent_runtime/application/message_delivery_registration.py, apps/agent-worker/src/muad_agent_worker/results/dispatcher.py, apps/agent-worker/src/muad_agent_worker/delivery/**, apps/im-gateway/src/muad_im_gateway/channels/base.py, tests/agent_runtime/test_webchat_task_artifact_delivery.py, tests/agent_runtime/test_output_artifacts.py, tests/agent_worker/test_async_background_outbox.py, tests/agent_worker/test_delivery.py, tests/gateway/test_artifact_delivery.py, tests/gateway/test_delivery_protocol.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "business-delivery-separation",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_webchat_task_artifact_delivery.py",
      "tests/agent_runtime/test_output_artifacts.py",
      "tests/agent_worker/test_async_background_outbox.py",
      "tests/agent_worker/test_delivery.py",
      "tests/gateway/test_artifact_delivery.py",
      "tests/gateway/test_delivery_protocol.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

分离 `BUSINESS_RESULT`、`ARTIFACT_CREATED`、`DELIVERY_PENDING`、`DELIVERED`、`DEGRADED` 和 `DELIVERY_FAILED`。交付失败只通过稳定 delivery identity 和 Outbox 重试，不重跑业务工具、不重建外部 Task；只有用户确实收到降级链接或替代形式时才记录 `DEGRADED`。

### Checklist

- [x] Artifact 已生成但投递失败时，不重复生成业务结果。
- [x] Delivery Outbox 可重试并正确区分可重试传输错误和确定性拒绝。
- [x] `DEGRADED` 仅在用户实际收到替代交付物时成立。
- [x] 未交付的 Artifact 不被用户正文宣称为“已交付”。
- [x] 投递终态和业务执行终态可以独立查询和审计。

### Spec Delta

```json
[
  {
    "id": "DELTA-004",
    "op": "add",
    "target": "im/harness-im",
    "rule": "业务执行、产物生成和渠道交付必须是可独立对账的事实。交付可重试时沿用稳定交付身份，不得重跑业务；发送结果未知时不得自动重发。DEGRADED 只有在实际交付替代形式时成立，未交付不得渲染为已送达。",
    "evidence": "agent-tool-failure-recovery.design.md §3.2.5；S-05、E-07；现有 Delivery Outbox 与 ArtifactDeliveryOutcome"
  }
]
```

### Log

- [2026-11-26] created
- [2026-11-26T03:11:07.519Z] started tests=d28456fb9e28

### Verification

- 尚未执行
- 2026-11-26T03:12:26.013Z PASS commands=1/1 executed=1 deduped=0 elapsed=18251ms tests=d28456fb9e28 head=cc748f89c6e5 dirty=1
  - business-delivery-separation PASS exit=0
## TASK-005: 硬停止、用户收尾与全链路回归

- **Status**: done
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004
- **Source**: agent-tool-failure-recovery.design.md#2.5、#3.5、#4、#5
- **Scenarios**: E-01, E-02, E-04, E-06, E-08, B-01
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot, artifact/harness-skill, mcp/harness-mcp, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/finalization/**, apps/agent-worker/src/muad_agent_worker/worker/executor.py, apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py, apps/agent-runtime/src/muad_agent_runtime/metrics.py, apps/agent-worker/src/muad_agent_worker/metrics.py, tests/agent_runtime/test_finalization_facts.py, tests/agent_runtime/test_finalization_funnel.py, tests/agent_runtime/test_runner_executor.py, tests/agent_runtime/test_execution_audit.py, tests/agent_worker/test_execution_outcomes.py, tests/agent_worker/test_worker_outcomes.py, docs/**, .code-flow/specs/**
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "hard-stop-and-finalization",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_failure_state.py",
      "tests/agent_runtime/test_finalization_facts.py",
      "tests/agent_runtime/test_finalization_funnel.py",
      "tests/agent_runtime/test_runner_executor.py",
      "tests/agent_runtime/test_execution_audit.py",
      "tests/agent_worker/test_execution_outcomes.py",
      "tests/agent_worker/test_agentic_no_progress_message.py",
      "tests/agent_worker/test_task_service.py",
      "tests/sdk/test_egress_source_authority.py",
      "tests/sdk/test_egress_segment_supervisor.py",
      "tests/agent_runtime/test_run_reaper.py",
      "tests/agent_runtime/test_run_deadline_deleted_conversation.py",
      "tests/agent_worker/test_scheduled_v2_async_resume.py",
      "tests/frontend/test_capacity_webchat_message_history_ui.py",
      "tests/agent_worker/test_worker_outcomes.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

收敛宿主硬停止和用户态收尾：身份/租户/快照不一致、关键事件或 checkpoint 持久化失败、模型不可用、deadline、租约丢失、总预算耗尽，以及执行边界异常中无法确认停止或无法保证执行权/事实完整性的情况，不再交给 Agent 假装恢复；普通工具失败和证据不完整则由 Agent 形成诚实结论。补齐结构化事件、审计、指标、最终文案以及 Spec/docs，完成 Runtime/Worker/MCP/Skill/Delivery 的一致性回归。

### Checklist

- [x] 硬停止只覆盖安全、事实、执行权和总预算不可保证的情况。
- [x] 用户正文不暴露原始堆栈；技术详情保留错误码、Run/Task/tool/operation/delivery 关联。
- [x] 普通重复失败止损后，剩余预算允许时由 Agent 生成一次解释，终态保留失败和技术详情。
- [x] `PARTIAL`/`UNKNOWN` 证据不渲染成零结果或成功。
- [x] 时间、输出、资源、子进程、网络响应或观测缺失等执行边界异常按统一事实归类，不为每个底层错误码复制恢复流程。
- [x] Runtime/Worker 使用同一失败控制语义和事件字段。
- [x] 规范、文档、指标和测试名称与实现事实一致。
- [x] 完成全部 TASK 后停下等待完整功能验收与代码审查，不在本 TASK 内 archive。

### Spec Delta

```json
[]
```

### Log

- [2026-11-26] created
- [2026-11-26T03:13:48.492Z] started tests=eec2392e3b4d
- [2026-11-26T08:44:01.847Z] reopen: 补齐普通工具止损后的 Agent 解释和失败正文持久化
- [2026-11-26T08:48:12.791Z] reopen: 修复现场验收发现的 DeepSeek 工具标记误显示
- [2026-11-26T08:52:43.300Z] reopen: 修正已保存失败消息的技术详情字号和列表标记
- [2026-11-26T09:43:11.598Z] reopen: 浏览器验收复现取消误报及后台任务快照绑定失败
- [2026-11-26T10:14:24.934Z] reopen: 浏览器补验确认期限分类和租约回收用户提示缺失
- [2026-11-26T10:24:33.895Z] reopen: 浏览器复验发现租约安全窗口耗尽仍误报通用错误
- [2026-11-26T11:34:07.753Z] reopen: 浏览器验收补齐关键事件写入失败诊断和 JOIN 等待正文重连去重

### Verification

- 尚未执行
- 2026-11-26T03:14:09.234Z PASS commands=1/1 executed=1 deduped=0 elapsed=20555ms tests=eec2392e3b4d head=cc748f89c6e5 dirty=1
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T08:44:14.630Z PASS commands=1/1 executed=1 deduped=0 elapsed=12468ms tests=8ba7be87aaa7 head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T08:48:27.334Z PASS commands=1/1 executed=1 deduped=0 elapsed=14225ms tests=8ba7be87aaa7 head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T08:53:00.396Z PASS commands=1/1 executed=1 deduped=0 elapsed=16679ms tests=adb1f37d0eb3 head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T09:46:45.464Z PASS commands=1/1 executed=1 deduped=0 elapsed=17379ms tests=150cd9518316 head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T10:19:48.391Z FAIL commands=0/1 executed=1 deduped=0 elapsed=18563ms tests=c5961df5a81b head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization FAIL exit=1
- 2026-11-26T10:21:03.670Z PASS commands=1/1 executed=1 deduped=0 elapsed=18462ms tests=c5961df5a81b head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T10:25:40.924Z PASS commands=1/1 executed=1 deduped=0 elapsed=19332ms tests=c5961df5a81b head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
- 2026-11-26T11:37:21.511Z PASS commands=1/1 executed=1 deduped=0 elapsed=20453ms tests=c5961df5a81b head=cc748f89c6e5 dirty=1 tests-changed
  - hard-stop-and-finalization PASS exit=0
## TASK-006: 执行边界异常事实归一化

- **Status**: done
- **Depends**: TASK-001, TASK-002, TASK-005
- **Source**: agent-tool-failure-recovery.design.md#2.5.1.1、#3.2.2、#3.5
- **Scenarios**: E-02, E-06, E-08
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, snapshot/harness-snapshot, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/tools/outcome.py, packages/agent-core/src/muad_agent_core/tools/result.py, packages/agent-core/src/muad_agent_core/agent/runner.py, packages/contracts/src/muad_contracts/tool_events.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-worker/src/muad_agent_worker/application/agentic/events.py, tests/agent_core/test_outcome_parser.py, tests/agent_core/test_tool_contract.py, tests/agent_core/test_tool_events.py, tests/agent_runtime/test_finalization_facts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "execution-boundary-contract",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_outcome_parser.py",
      "tests/agent_core/test_tool_contract.py",
      "tests/agent_core/test_tool_events.py",
      "tests/agent_runtime/test_finalization_facts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

补齐通用执行边界事实，不绑定某个沙箱或底层错误码：`boundary_kind` 表示时间/输出/资源/终止/上下文/观测等来源，`stop_confirmed` 表示当前执行是否已安全停止。代表性 Skill 结果映射为：边界受限但停止已确认时保留 `PARTIAL/UNKNOWN` 并交给 Agent；停止未确认或执行权/关键事实不可保证时升级为 Run 级 `STOP`。事件、FailureEvidence 和终态事实都能保留这两个字段，避免 Agent 把“只看到一小段输出”误判成完整结果。

### Checklist

- [x] FailureControl 能表达执行边界来源和停止确认事实。
- [x] 边界异常按统一事实映射到 `CONTINUE`、`WAIT` 或 `STOP`，不为每个底层错误码复制流程。
- [x] 部分/未知证据继续禁止伪造成功或零结果。
- [x] Runtime/Worker 事件和恢复快照保留边界事实。
- [x] 代表性边界契约测试通过，不新增沙箱参数专项行为。

### Spec Delta

```json
[
  {
    "id": "DELTA-005",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "执行边界异常必须以通用事实归一化：边界来源、停止是否确认、发送/结果事实和证据完整性共同决定 CONTINUE/WAIT/STOP。底层错误码只作为诊断来源，不得让不同适配器复制不同恢复流程；停止未确认或执行权/关键事实不可保证时不得继续派发可能产生副作用的调用。",
    "evidence": "agent-tool-failure-recovery.design.md §2.5.1.1、§3.2.2；E-02、E-06、E-08"
  }
]
```

### Log

- [2026-11-26] created
- [2026-11-26T07:13:32.656Z] started tests=b71a3c0c37a8

### Verification

- 尚未执行
- 2026-11-26T07:21:56.904Z PASS commands=1/1 executed=1 deduped=0 elapsed=5747ms tests=b71a3c0c37a8 head=cc748f89c6e5 dirty=1
  - execution-boundary-contract PASS exit=0
