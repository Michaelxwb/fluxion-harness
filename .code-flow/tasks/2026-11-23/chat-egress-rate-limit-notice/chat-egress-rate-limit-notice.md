---
{"version":1,"requirement":"chat-egress-rate-limit-notice"}
---
# 聊天中的平台限流提示实施任务

## TASK-001: 传递本次调用的窗口限流原因

- **Status**: done
- **Depends**:
- **Source**: chat-egress-rate-limit-notice.design.md §1 在出站与脚本边界确认原因
- **Scenarios**: S-01, E-01, B-02
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, secret/harness-secret, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/egress_ipc*.py, packages/platform-sdk/src/muad_platform_sdk/host_egress_execution.py, packages/platform-sdk/src/muad_egress_launch.py, packages/agent-core/src/muad_agent_core/skill/contracts.py, packages/agent-core/src/muad_agent_core/skill/sandboxed.py, skills/smoke-platform-sample/scripts/run.py, tests/sdk/test_egress_ipc.py, tests/agent_core/test_sandboxed_skill_executor.py, tests/skills/test_smoke_platform.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "correlated-window-limit",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/sdk/test_egress_ipc.py", "tests/agent_core/test_sandboxed_skill_executor.py", "tests/skills/test_smoke_platform.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在 Broker 确认拦截受控平台请求后，将本次拒绝标识经 IPC、Python 启动器传到沙箱执行器。执行器核对保留退出状态、结构化记录和本次 Broker 的匹配拒绝，附加类型化限流原因。更新 Smoke Skill，让窗口限流异常沿现有执行入口向上冒泡。

### Checklist

- [x] 在真实 Redis 计量和 Unix IPC 集成用例中验证窗口拒绝关联、上游未收到该请求，以及出站拒绝仍有审计记录。
- [x] 覆盖脚本输出冒用、跨调用标识、捕获后成功、捕获后触发独立错误，以及超时、取消、累计预算和计量依赖故障的优先级。
- [x] 验证 Smoke Skill 透传窗口限流，正常查询与其他故障的行为符合原契约。

### Spec Delta

```json
[]
```

### Log

- [2026-11-23] 起草。
- [2026-11-23T01:34:04.373Z] started tests=1c1d724b3f11
- [2026-11-23T01:42:38.597Z] reopen: 复验聊天专用的限流原因捕获开关

### Verification

- 尚未执行。
- 2026-11-23T01:36:22.958Z PASS commands=1/1 executed=1 deduped=0 elapsed=7619ms tests=1c1d724b3f11 head=1e4c8c9119c7 dirty=1
  - correlated-window-limit PASS exit=0
- 2026-11-23T01:43:45.665Z PASS commands=1/1 executed=1 deduped=0 elapsed=8648ms tests=1c1d724b3f11 head=1e4c8c9119c7 dirty=1
  - correlated-window-limit PASS exit=0
## TASK-002: 用普通助手消息结束受限的聊天轮次

- **Status**: done
- **Depends**: TASK-001
- **Source**: chat-egress-rate-limit-notice.design.md §2 按普通助手回复结束聊天轮次
- **Scenarios**: S-01, E-01, B-01, B-02
- **Spec-Refs**: arch/harness-arch, artifact/harness-skill, secret/harness-secret, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/**, packages/agent-core/src/muad_agent_core/agent/**, packages/agent-core/src/muad_agent_core/tools/**, packages/contracts/src/muad_contracts/tool_events.py, apps/console-platform/backend/src/muad_console_platform/application/execution_*.py, tests/agent_core/test_runner.py, tests/agent_core/test_recovery_states.py, tests/agent_core/test_tool_contract.py, tests/agent_runtime/test_skill_tools.py, tests/agent_runtime/test_runner_executor.py, tests/console_platform/test_execution_diagnosis.py, tests/console_platform/test_execution_export_render.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "chat-window-limit-completion",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_runner.py", "tests/agent_core/test_recovery_states.py", "tests/agent_core/test_tool_contract.py", "tests/agent_runtime/test_skill_tools.py", "tests/agent_runtime/test_runner_executor.py", "tests/console_platform/test_execution_diagnosis.py", "tests/console_platform/test_execution_export_render.py"],
    "timeout": 420,
    "parallel": false
  }
]
```

### Description

在聊天的 Skill 工具结果上明确记录受限状态。结算已启动的工具后，确认仅因窗口限流结束的聊天轮次保留已产生的正常正文，并追加本地化提示。沿用现有附件交付和结果恢复路径，保留运行记录、审计和诊断中的业务事实。

### Checklist

- [x] 受限工具的结构化状态可在完成事件、审计和导出中核对。普通查询、上游限流和真实故障沿各自现有路径收尾。
- [x] 仅因窗口限流结束时，保留已输出的正文，在剩余轮次内使用不带工具的模型补答，再增量追加提示。不派发后续工具，不重放已结算的查询。
- [x] 验证中断恢复后的回复和查询次数、同批真实失败优先级，以及仅交付已登记且可验证的部分结果。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "聊天 Skill 因本次调用中经出站准入确认的平台接口窗口限流而中断时，超限请求必须继续在外发前拦截并审计，工具结果明确记录查询受限。仅由该限流结束的聊天轮次保留已有正文，在正常助手回复末尾追加受限提示并停止派发后续工具；仅展示已验证可交付的部分结果。独立故障继续按其真实原因收尾。",
    "evidence": "FEAT-01；TASK-001 拒绝关联与因果回归，TASK-002 聊天收尾、恢复与交付回归；人工场景 E-01、B-01、B-02。"
  }
]
```

### Log

- [2026-11-23] 起草。
- [2026-11-23T01:36:38.723Z] started tests=4ef47368ddf8
- [2026-11-23T01:57:16.226Z] reopen: 人工复测发现固定提示覆盖正文，改为保留正文并追加提示
- [2026-11-23T01:59:17.034Z] reopen: 复验多轮正文保留与追加提示

### Verification

- 尚未执行。
- 2026-11-23T01:42:12.460Z FAIL commands=0/1 executed=1 deduped=0 elapsed=10301ms tests=4ef47368ddf8 head=1e4c8c9119c7 dirty=1
  - chat-window-limit-completion FAIL exit=1
- 2026-11-23T01:42:29.054Z FAIL commands=0/1 executed=1 deduped=0 elapsed=7935ms tests=4ef47368ddf8 head=1e4c8c9119c7 dirty=1
  - chat-window-limit-completion FAIL exit=1
- 2026-11-23T01:43:57.686Z PASS commands=1/1 executed=1 deduped=0 elapsed=8253ms tests=4ef47368ddf8 head=1e4c8c9119c7 dirty=1
  - chat-window-limit-completion PASS exit=0
- 2026-11-23T01:58:41.388Z PASS commands=1/1 executed=1 deduped=0 elapsed=10211ms tests=4ef47368ddf8 head=1e4c8c9119c7 dirty=1
  - chat-window-limit-completion PASS exit=0
- 2026-11-23T01:59:28.863Z PASS commands=1/1 executed=1 deduped=0 elapsed=11631ms tests=4ef47368ddf8 head=1e4c8c9119c7 dirty=1
  - chat-window-limit-completion PASS exit=0
