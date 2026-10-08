# Tasks: async-tool-runtime

- **Source**: async-tool-runtime.backend.design.md, async-tool-runtime.frontend.design.md
- **Created**: 2026-10-07
- **Updated**: 2026-10-08

## Proposal

实现异步工具结果回流、`WAITING_TOOL` Run 等待恢复和安全只读并发，把已有 ASYNC Skill 从"只提交不接入原推理"升级为 durable operation、可靠收件、任意实例接续和 Console 可观测轮廓。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 |
|--------|---------|---------|-------------|---------|------|---------|
| S-01 | backend#2.5.2 场景清单 | E2E | Runtime/Worker HTTP、PG、Redis、LLM 探针、SSE | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| S-02 | backend#2.5.2 场景清单 | E2E | 四服务 HTTP、真实 Task、渠道出站探针 | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] |
| S-03 | backend#2.5.2 场景清单 | E2E | 实际 provider 请求、canonical 历史、制品存储 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| S-04 | backend#2.5.2 场景清单 | integration | 真实 Runner、可控异步工具处理器 | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] |
| S-05 | backend#2.5.2 场景清单 | E2E | Console resolve/API-09、等待恢复、LLM/MCP HTTP | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| S-06 | backend#2.5.2 场景清单 | E2E | Gateway、Runtime SSE、Worker、原消息回复探针 | TASK-006 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] |
| S-07 | backend#2.5.2 场景清单 | integration | 真实 MCP HTTP 探针、Runtime adapter | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] |
| E-01 | backend#2.5.2 场景清单 | E2E | 冻结授权、Worker HTTP、PG、审计 | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] |
| E-02 | backend#2.5.2 场景清单 | E2E | Runtime 控制发件、Worker 创建事务、故障代理 | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] |
| E-03 | backend#2.5.2 场景清单 | E2E | Worker 真实进程、PG outbox、Runtime HTTP | TASK-002 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_delivery.py"] |
| E-04 | backend#2.5.2 场景清单 | E2E | Runtime 等待事务、结果回流 HTTP、PG | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| E-05 | backend#2.5.2 场景清单 | E2E | 两个 Runtime 真实进程、PG lease、LLM HTTP | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| E-06 | backend#2.5.2 场景清单 | E2E | 真实 deadline sweep、Worker、Runtime、PG | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| E-07 | backend#2.5.2 场景清单 | E2E | Runtime cancel、Worker operation 行锁、真实 HTTP | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] |
| E-08 | backend#2.5.2 场景清单 | E2E | ScriptSkillExecutor、真实子孙进程、管道、HTTP | TASK-002 | planned | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_skill_cancellation_real_subprocess.py"] |
| E-09 | backend#2.5.2 场景清单 | integration | ContextBuilder、真实 provider 消息序列化 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] |
| E-10 | backend#2.5.2 场景清单 | integration | 真实制品发布/DB 事务、压缩端口 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] |
| E-11 | backend#2.5.2 场景清单 | E2E | 内部服务门控、Runtime/Console HTTP、PG | TASK-007 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/security/test_internal_service_identity.py"] |
| E-12 | backend#2.5.2 场景清单 | E2E | API-09、日志、inbox/outbox/checkpoint/canonical、制品 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] |
| E-13 | backend#2.5.2 场景清单 | E2E | SSE socket、Runtime supervisor、Gateway/重连客户端 | TASK-006 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] |
| E-14 | backend#2.5.2 场景清单 | integration | MCP HTTP 探针、客户端关闭 | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] |
| E-15 | backend#2.5.2 场景清单 | E2E | Redis 故障代理、PG 队列和结果发件 | TASK-002 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_redis_unavailable.py"] |
| E-16 | backend#2.5.2 场景清单 | E2E | Worker HTTP 故障代理、控制发件、PG tombstone | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] |
| E-17 | backend#2.5.2 场景清单 | E2E | Gateway/Runtime HTTP、PG 活跃会话约束 | TASK-006 | planned | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] |
| B-01 | backend#2.5.2 场景清单 | integration | PG 行锁、并发提交、关闭屏障 | TASK-003 | planned | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] |
| B-02 | backend#2.5.2 场景清单 | integration | 严格 JSON DTO、幂等表、PG 唯一约束 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_runtime_strict_json.py"] |
| B-03 | backend#2.5.2 场景清单 | integration | UTF-8 字节预算、共享制品、历史重建 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] |
| B-04 | backend#2.5.2 场景清单 | integration | PG wait_generation、epoch、canonical seq | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] |
| B-05 | backend#2.5.2 场景清单 | integration | 持久检查点、注入时钟、真实 budget 判定 | TASK-004 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] |
| B-06 | backend#2.5.2 场景清单 | integration | 并行规划器、Runner、资源声明 | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] |
| B-07 | backend#2.5.2 场景清单 | integration | inbox/outbox 租约、PG、故障代理 | TASK-002 | planned | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] |
| B-08 | backend#2.5.2 场景清单 | integration | 真实 PG、Alembic、迁移前置检查 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/migrations/test_runtime_wait_parity.py"] |
| B-09 | backend#2.5.2 场景清单 | integration | 真实需求文件、manifest、inventory runner | TASK-009 | planned | ["uv", "run", "pytest", "-q", "tests/async_tool_runtime_inventory.py"] |
| S-20 | frontend#2.4 验收条件 | E2E | Browser→Console→PG；Runtime/Worker/LLM HTTP | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| S-21 | frontend#2.4 验收条件 | E2E | Browser→Router/面板控制→Console Run/Task API→PG | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| S-22 | frontend#2.4 验收条件 | E2E | Browser、真实 Console 分页、PG count | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| S-23 | frontend#2.4 验收条件 | E2E | Browser language/timezone、HTTP 请求头、真实 DTO | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| S-24 | frontend#2.4 验收条件 | E2E | Browser、Console 真实只读投影、PG | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| E-20 | frontend#2.4 验收条件 | E2E | 故障代理、Browser、真实 Console 重试 | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| E-21 | frontend#2.4 验收条件 | E2E | Browser、账号租户、Console API | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| E-22 | frontend#2.4 验收条件 | E2E | 延迟真实响应的代理、Browser、服务请求 | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| B-20 | frontend#2.4 验收条件 | E2E | 真实 PG 空列表/分页 | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |
| B-21 | frontend#2.4 验收条件 | E2E | Chrome 键盘、实际 Semi SideSheet/Tab/链接 | TASK-008 | planned | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] |

---

## TASK-001: 契约、迁移与状态枚举基线

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: async-tool-runtime.backend.design.md#3.3 数据设计, async-tool-runtime.backend.design.md#3.4 接口设计, async-tool-runtime.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002, harness-data#RULE-data-001, harness-time#RULE-time-001, harness-arch#RULE-arch-001
- **Acceptance-Refs**: B-02, B-08

### Description

为异步工具回流建立跨服务必须共享的契约、表结构、索引、状态枚举、严格 JSON 规则、错误码和迁移 parity。后续所有 Runtime/Worker/Gateway/Console 改动只消费这一层，不允许各自定义字符串或绕过门户。

### Checklist

- [x] 定义并钉死 contracts 层枚举：`RunStatus`、`CompletionMode`、`TerminalStatus`、`OperationStatus`、`ControlOutboxStatus`、`InboxMaterializationState`、`WaitReason`；禁止 string literal business-state
- [x] 新增 `runtime.tool_operation`、`runtime.run_continuation`、`runtime.tool_result_inbox`、`runtime.tool_control_outbox`、`task.runtime_operation`、`task.runtime_result_outbox` 六表与 partial unique 约束/索引
- [x] `RunRecord` 新增 `deadline_at`、`execution_epoch`、`WAITING_TOOL`；`CanonicalEvent` 新增 `source_event_id`；`TaskExecution` 新增 `source_operation_id/source_tool_call_id/completion_mode`
- [x] 所有本任务自由 JSON 入口跑 `ensure_strict_json`：`sort_keys`、紧凑 separators、`ensure_ascii=False`、`allow_nan=False`、禁止 `default=str`
- [x] 新增错误码统一登记到 `config/api-messages.yaml`，并保留 zh-CN/en-US fallback
- [x] [B-02][integration] 写严格 JSON、幂等指纹和冲突用例：同 key/同指纹重放；tenant/endpoint 区分幂等命名空间且指纹不同；同 scope 换 actor/input/mode/hash 返回 409；NaN/Infinity/未知类型 422
- [x] [B-08][integration] 写空库及旧终态 Run/旧 Task 盘面升级测试；存在 WAITING_TOOL 或 PENDING outbox 时回退检查失败，drain 后允许
- [x] 跑 Alembic 升级/回退 parity 与 inventory 前置检查，明确旧任务 history boundary
- [x] verifier harness-api#RULE-api-002：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-api#RULE-api-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-arch#RULE-arch-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-data#RULE-data-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-time#RULE-time-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-02 | integration | PG unique constraints、contracts DTO、typed API 错误 | 同 key 指纹重放首次结果；冲突分腿返回 409/422；NaN/Infinity/未知类型 422；不调用默认序列化 | `tests/contracts/test_runtime_strict_json.py` | ["uv", "run", "pytest", "-q", "tests/contracts/test_runtime_strict_json.py"] | verified |
| B-08 | integration | 真实 PG、Alembic migration、migration preflight | 空库/含旧终态 Run 盘面升级后 schema parity 一致；WAITING_TOOL/PENDING outbox 回退检查失败；drain后允许；旧 Task 不回填 Join/tool_call_id | `tests/migrations/test_runtime_wait_parity.py` | ["uv", "run", "pytest", "-q", "tests/migrations/test_runtime_wait_parity.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-02 | FAIL: CompletionMode 契约缺失，收集失败 | PASS: `uv run pytest -q tests/contracts/test_runtime_strict_json.py tests/migrations/test_runtime_wait_parity.py`，20 passed；与历史契约/JSON/interrupt 回归合跑 43 passed | `tests/contracts/test_runtime_strict_json.py::test_b02_business_state_enums_are_pinned`、`test_b02_canonical_json_is_injective_for_supported_values`、`test_b02_free_json_dtos_reject_non_json_before_serialization`、`test_b02_invalid_wire_json_returns_422`、`test_b02_result_identity_shape_and_size_are_strict`、`test_b02_pg_replay_conflicts_and_unique_scope` | 真实 contracts DTO + api-kit ASGI 错误封套；独立 PG 的 TaskSubmissionService + partial unique | verified |
| B-08 | FAIL: async_tools ORM 模块缺失，收集失败 | PASS: 同命令 20 passed；原始 schema_parity verifier 38 passed | `tests/migrations/test_runtime_wait_parity.py::test_b08_empty_database_schema_parity`、`test_b08_old_rows_upgrade_and_drain_before_downgrade` | 独立空库 head；真实 Alembic 回退0018/历史 Run、Task/升级；WAITING_TOOL、PENDING 控制和结果 outbox 分别阻断，drain 后回退成功 | verified |

- [B-02][integration] RED — `uv run pytest -q tests/contracts/test_runtime_strict_json.py tests/migrations/test_runtime_wait_parity.py` (2026-10-08): collection failed because contracts `CompletionMode` is absent. Cases: `test_b02_canonical_json_is_injective_for_supported_values`, `test_b02_free_json_dtos_reject_non_json_before_serialization`, `test_b02_invalid_wire_json_returns_422`, `test_b02_result_identity_shape_and_size_are_strict`, `test_b02_pg_replay_conflicts_and_unique_scope`. Real boundaries: contracts DTO → api-kit HTTP error handler; isolated PG → TaskSubmissionService/partial unique. GREEN pending.
- [B-08][integration] RED — same command (2026-10-08): collection failed because `infrastructure.models.async_tools` is absent. Cases: `test_b08_empty_database_schema_parity`, `test_b08_old_rows_upgrade_and_drain_before_downgrade`; command: `uv run pytest -q tests/migrations/test_runtime_wait_parity.py`. Real boundaries: isolated PG + real Alembic upgrade/downgrade and historical Run/Task rows. GREEN pending.
- B-02: verified — automated command passed; run_id=1333a2e3ea64472b884582f043044bc9 (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=1333a2e3ea64472b884582f043044bc9 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-08] TASK-001 functional GREEN：20 新增测试；canonical/contracts/interrupt 回归 43 passed；五条原始 Spec verifier 均通过（5/18/33/38，以及 DateTime 2 + parity 38）；全仓 mypy 315 source files 通过。按 RULE-api-002 明确 tenant/endpoint 为独立幂等命名空间；旧 Task 的新关联列保持 NULL。修正场景命令登记为 argv JSON 后锁定 manifest（原 manifest 尚无执行证据）。
- [2026-10-08] completed (done)

---

## TASK-002: Worker 终态原子发件与取消 Tombstone

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计, async-tool-runtime.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-worker#RULE-worker-001, harness-skill#RULE-skill-001
- **Acceptance-Refs**: E-03, E-08, E-15, B-07

### Description

把 Worker 所有根任务终态路径收口到同一个原子发件函数：状态 CAS、TaskEvent、`runtime_result_outbox` 必须同一事务。同时提供先于 Task 到达的 cancellation tombstone，和结果 dispatcher 的 durable retry/idempotency。

### Checklist

- [ ] 新增/收口根 Task 终态记录函数：success、deterministic failure、retry exhaust、deadline sweep、queued/waiting cancel、reclaim terminal、BATCH fan-in 均走同一事务路劲
- [ ] JOIN root task 在终态事务写入 `runtime_result_outbox`；Child 不重复发 root 结果；DETACH 保留现有 final delivery
- [ ] `event_id` 由 `tenant/task_id/terminal TaskEvent seq` 稳定派生；同 event_id 正文不同返回 409；persisted=true 才 ack
- [ ] Dispatcher 只有 `persisted=true` 且同 event_id 才认成功；2xx/Redis hint 不是入库证据
- [ ] `task.runtime_operation` 取消可先到：同 operation 行锁内允许空 submission_hash cancel；后续同可信来源 submit 只能恢复既定取消结果，不得绕过 tombstone
- [ ] [E-03][E2E] 在终态事务提交后、发件前 SIGKILL；另一 Worker 重投；Runtime canonical恰一条；响应丢失后的重投不重写
- [ ] [E-08][E2E] 运行中取消/超时回收真实子孙进程；直接子进程退出但孙进程持 stdout 时收尾有界；不声称远端副作用已停止
- [ ] [E-15][E2E] Redis wake-up 丢失或不可用时 PG 扫描仍使创建/取消/结果投递/恢复前进；有日志与指标
- [ ] [B-07][integration] inbox/outbox lease、PG fault injector：同事件改正文 409；ack only durable；重投耗尽保留 FAILED 出站告警，不产生第二 canonical
- [ ] verifier harness-worker#RULE-worker-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-skill#RULE-skill-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-03 | E2E | Worker 真实进程、PG outbox、Runtime HTTP | 终态事务提交后 SIGKILL，另一 Worker 重投；Runtime canonical 恰一条；响应丢失重投不重写 | `tests/acceptance/runtime/test_background_result_delivery.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_delivery.py"] | planned |
| E-08 | E2E | ScriptSkillExecutor、真实子孙进程、管道、HTTP | 取消/超时整组回收；直接子进程退出且孙进程持 stdout 仍有界；协作取消不声称强制停止所有远端副作用 | `tests/agent_worker/test_skill_cancellation_real_subprocess.py` | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_skill_cancellation_real_subprocess.py"] | planned |
| E-15 | E2E | Redis fault proxy、PG queue/outbox、真实 dispatch | Redis 丢失/不可用时提交、结果投递、等待恢复仍前进；异常有日志与指标 | `tests/acceptance/runtime/test_redis_unavailable.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_redis_unavailable.py"] | planned |
| B-07 | integration | inbox/outbox lease rows、PG、fault proxy | 同事件改正文 409；ack only durable；重投耗尽保留 FAILED/告警；可操作恢复不制造第二 canonical | `tests/agent_worker/test_runtime_operation_races.py` | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] | planned |

### Acceptance Evidence

- [E-03][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-08][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-15][E2E] planned — defer to verify-e2e after relevant TASK verified
- [B-07][integration] planned — RED/GREEN pending

### Log

- [2026-10-07] created (draft)

---

## TASK-003: Runtime 统一工具执行入口、提交取消与容量

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计, async-tool-runtime.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-02, E-01, E-02, E-07, E-16, B-01

### Description

收口现有 `ToolExecutionPipeline` 与 Runner 直接调用路径，成为唯一工具执行入口。ASYNC Skill 只负责提交意图，不允许模型等同真实知识。提交、取消、容量、关闭屏障都要和 durable operation 对齐。

### Checklist

- [ ] Runner 默认接入统一 PORT-02/pipeline；不可只提供未接线的可选 pipeline
- [ ] 最终参数经过校验 → PRE_TOOL_USE Hook → 对改写后参数复验 → 冻结 EffectiveCapabilities 授权 → Run 行锁预留额度 → 执行/operation → ToolResultRoundPort；授权不可被模型覆盖 tenant/actor/run/snapshot
- [ ] `execute_skill` 对 ASYNC 新增 `completion_mode: JOIN | DETACH`，默认 JOIN；SYNC 显式携带 completion_mode 明确 422；回执包含 operation_id/task_id/task_status/completion_mode
- [ ] 5 秒内未拿到 Worker durable admission 时返回 `SUBMISSION_PENDING`，task_id 为 null；不得伪造 QUEUED
- [ ] 提交成功前同事务写 `runtime.tool_operation(SUBMIT_PENDING)` 与 `tool_control_outbox(SUBMIT)`；operation_id 每次真实 tool_call 一个，重试用原 ID
- [ ] 提交幂等键 `runtime-op:{operation_id}:submit`；取消幂等键 `runtime-op:{operation_id}:cancel`；同 key 不同指纹返回 `IDEMPOTENCY_MISMATCH`
- [ ] Worker `runtime_operation` 先写取消 tombstone；Task 创建与取消在同 operation 行锁内复检，取消先到不得再启动 Task
- [ ] Run 取消级联 JOIN 和未受理提交；已受理 DETACH 不级联；晚到 submit/result 只记 LATE，不复活或唤醒终态 Run
- [ ] `pending_operation_limit` 包含 SUBMIT_PENDING(DETACH/JOIN)+未终态 JOIN，在 Run 行锁内原子预留；DETACH 受理后释放
- [ ] [S-02][E2E] 明确 DETACH受理后原Run完成；其后取消原Run不取消Task；DETACH Run routed final result when available
- [ ] [E-01][E2E] 未授权 Skill、非法参数、Hook改写后非法参数分别拒绝；业务 Task/operation/outbox 均不创建，DENY/ERROR 审计存在
- [ ] [E-02][E2E] Worker 创建后丢提交响应：模型收到 SUBMISSION_PENDING，不收 COMPLETED；重试得到同 task_id，只建一条 Task，最终可回流
- [ ] [E-07][E2E] Worker cancellation tombstone阻止新任务启动；控制取消与提交交错明确；BATCH取消与final fan-in同时到达无死锁/状态反转
- [ ] [E-16][E2E] 提交响应持续丢失至重试耗尽：operation明确 SUBMIT失败，不伪造Task失败；取消意图持久，晚到 submit/result不恢复依赖或启动另一 Task
- [ ] [B-01][integration] pending_limit=1 同时申请两次只接受一个；关闭与提交交错后无未登记本地任务，等待意图全部可查
- [ ] verifier harness-auth#RULE-auth-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | E2E | 四服务 HTTP、真实 Task、渠道出站探针 | DETACH 受理后原 Run 完成；取消原 Run 不取消 Task；Worker按 FINAL_ONLY/NONE delivery 一次；无路由时不重复 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | planned |
| E-01 | E2E | 冻结授权、Worker HTTP、PG、审计 | 未授权/非法参数/Hook改写后拒绝；业务任务不创建；DENY/ERROR 审计存在 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | planned |
| E-02 | E2E | Runtime 控制发件、Worker 创建事务、故障代理 | Worker 创建后丢提交响应；模型收到 SUBMISSION_PENDING；重试同 task_id，只建一条 Task；最终回流 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | planned |
| E-07 | E2E | Runtime cancel、Worker operation 行锁、真实 HTTP | 控制取消先到 tombstone阻止新任务；已创建 Task进入真实取消路径；BATCH取消与fan-in无死锁/反转 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | planned |
| E-16 | E2E | Worker HTTP 故障代理、控制发件、PG tombstone | 提交响应持续丢失直到重试耗尽；operation明确“提交未确认”failed；取消意图持久；晚到不复活 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | planned |
| B-01 | integration | PG 行锁、并发提交、关闭屏障 | pending_limit=1并发申请只接一个；关闭与提交交错后无未登记本地任务，等待意图可查 | `tests/agent_worker/test_runtime_operation_races.py` | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] | planned |

### Acceptance Evidence

- [S-02][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-01][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-02][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-07][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-16][E2E] planned — defer to verify-e2e after relevant TASK verified
- [B-01][integration] planned — RED/GREEN pending

### Log

- [2026-10-07] created (draft)

---

## TASK-004: WAITING_TOOL 检查点、接续泵与上下文物化

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计, async-tool-runtime.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001, harness-model#RULE-model-001, harness-secret#RULE-secret-001, harness-log#RULE-log-001
- **Acceptance-Refs**: S-01, S-03, S-05, E-04, E-05, E-06, E-09, E-10, E-12, B-03, B-04, B-05

### Description

实现 Runner 的等待/恢复循环。事件进入 inbox 后先 durable，再经 PORT-01 消费；WAITING_TOOL release lease and resources；ContinuationPump can claim from PG and reconstruct from snapshot/canonical history/API-09 credentials. Recent Waiting_input must not bypass human confirmation.

### Checklist

- [ ] Runner 接入 PORT-01：模型调用前非阻塞Drain，no tool call 后再次 drain，复检依赖后决定 CONTINUE/WAIT/FINISH
- [ ] RunnerOutcome 扩展判别字段：COMPLETED/WAITING_TOOL/WAITING_INPUT/BUDGET_EXCEEDED；WAITING_TOOL 只能持久化暂停，不能落入现有完成收尾
- [ ] waiting 事务按锁序复检 all ready events、unmaterialized inbox、unsubmitted/accepted operations、not-terminal JOIN，避免 check-then-commit lost wakeup
- [ ] 进入等待同事务落中间 assistant 文本、BEFORE_MODEL checkpoint、RUN_WAITING_TOOL 事件并释放 lease/close provider/MCP/HTTP clients
- [ ] checkpoint只能在完整工具回合之后；所有 assistant tool_calls已与原 tool receipt paired
- [ ] `TOOL_TASK_ACCEPTED` 和 `BACKGROUND_RESULT` 渲染为外部数据 USER消息；不新造同 tool_call_id 的 tool response，不变成 SYSTEM，不冒充用户新业务请求
- [ ] ContinuationPump claim INSTANCE: `FOR UPDATE SKIP LOCKED`、wait_generation/epoch CAS、status/ready/deadline/cancel recheck；所有心跳/事件/terminal writes verify epoch/owner/lease
- [ ] 接续从 snapshot/canonical/checkpoint重建，不重新resolve authorization/config；real-time API-09 credentials, no env fallback
- [ ] 事件到达 WAITING_INPUT时只持久化，不绕过 human confirmation；RUNNING中的普通事件到达不启动第二 Runner
- [ ] 多个等待轮次复用同一 Run；user/resume 进入事件后按 WAITING_INPUT semantics
- [ ] 新增 trace context scope in propagation,后台 resume 恢复 trace_id/request_id/tenant/run/call/task
- [ ] API-09 credential only memory; canary every new persistent/prompt/log surface no credential
- [ ] context/building: result events in可重建集合；externalized canonical artifact_id and payload共用 reference_payload；system,summary,memory prefix preserved；pending operation不依赖 compressed文本
- [ ] [S-01][E2E] ASYNC JOIN慢任务:先拿提交回执、执行独立工作、进入 WAITING_TOOL；Task完成后同 run_id恢复并用真实结果回答；waiting不占委 Runner/lease
- [ ] [S-03][E2E] 两个 JOIN结果回流，重bility前后出来模型内容一致；原 tool_call仍只有一个tool响应；结果按持久 seq进入后续模型请求
- [ ] [S-05][E2E] 分别修改Agent配置、授权、模型参数；旧Run恢复仍旧快照，新Run新值；独立轮换凭据后旧Run新凭但快照hash不变
- [ ] [E-04][E2E] &quot;已检查无结果&quot;和&quot;提交等待&quot;间完成Task；无失唤醒，Run最终完成而非永久 WAITING_TOOL
- [ ] [E-05][E2E] 杀死 WAITING_TOOL实例，另一实例恢复；同时两个实例claim只有一方有效；过期owner无法续租或提交终态
- [ ] [E-06][E2E] waiting跨absolute deadline: Run FAILED, JOIN发出cancel；晚到成功只留轮廓不复活；Task跨 deadline不能 COMPLETED
- [ ] [E-09][integration] 结果含“忽略原指令”文本；只出现外部结果数据消息，SYSTEM前缀与 call paired不被改写
- [ ] [E-10][integration] inject batch artifact failure and summary failure: whole batch inline/history retained, Run不因 compression failure终止，warning与metric increments可断言
- [ ] [E-12][E2E] canary秘密不出现在新增persistent surface、Prompt or outbound；model credential清空后恢复明确失败，不退回 env
- [ ] [B-03][integration] exact threshold inline,1 byte over externalized；Chinese，synthesized multi-result over round budget correct；externalization failure retains whole batch
- [ ] [B-04][integration] PG wait_generation/epoch/canonical seq：旧 wait generation late wake不接管新代；已消费事件不重复注入；未消费事件不丢失；剩余batch存在时不睡眠
- [ ] [B-05][integration] persistent checkpoint、injected clock、real budget judgement：多次waiting/resume不重置 turns/tool_calls/usage/deadline；budget耗尽明确 failed
- [ ] verifier harness-log#RULE-log-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-model#RULE-model-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-secret#RULE-secret-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-snapshot#RULE-snapshot-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Runtime/Worker HTTP、PG、Redis、LLM 探针、SSE | JOIN 慢任务回执/等待/恢复/真实结果回答；等待不占 Runner/lease | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| S-03 | E2E | 实际 provider 请求、canonical 历史、制品存储 | 两个 JOIN结果回流，重建前后模型可见内容一致；每个 tool_call只一个 tool响应 | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| S-05 | E2E | Console resolve/API-09、等待恢复、LLM/MCP HTTP | 旧Run保留旧配置/授权/模型；新Run新值；凭据轮换旧Run新凭据但快照hash不变 | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| E-04 | E2E | Runtime等待事务、结果回流HTTP、PG | waiting tx check/commit between no-results and task ready; Run最终不永久 WAITING_TOOL | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| E-05 | E2E | 两个Runtime真实进程、PG lease、LLM HTTP | kill waiting instance; another resume; two claims only one effective; expired owner cannot renew/write success | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| E-06 | E2E | 真实 deadline sweep、Worker、Runtime、PG | waiting跨absolute deadline Run FAILED, JOIN cancel; late success只留 LATE; Task跨 deadline不能COMPLETED | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| E-09 | integration | ContextBuilder、真实 provider消息序列化 | injection text“ignore instructions”does not become SYSTEM or rewriting tool call pairing; results external data only | `tests/agent_runtime/test_tool_result_materialization.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | planned |
| E-10 | integration | 真实制品发布/DB事务、压缩端口 | batch externalization mid-failure and summary failure：whole batch inline/history retained, Run not终止, warning+metric+increment | `tests/agent_runtime/test_tool_result_materialization.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | planned |
| E-12 | E2E | API-09、日志、inbox/outbox/checkpoint/canonical/制品 | canary不出现在新增持久化面、Prompt或出站；credential清空明确failed；no env fallback | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | planned |
| B-03 | integration | UTF-8 bytes budget、shared artifact、history重建 | exact threshold inline; strict >1 byte externalized；Chinese/multi-result over budget correct；failure retains whole batch | `tests/agent_runtime/test_tool_result_materialization.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | planned |
| B-04 | integration | PG wait_generation、epoch、canonical seq | old generation晚到 wake不接管；已消费不重复注入；未消费不丢失；剩余 batch 存在时不睡眠 | `tests/agent_runtime/test_tool_wait_state_machine.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] | planned |
| B-05 | integration | persistent checkpoint、injected clock、real budget judgement | 多次waiting/resume不重置 turns/tool_calls/usage/deadline；budget耗尽明确 failed | `tests/agent_runtime/test_tool_wait_state_machine.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] | planned |

### Acceptance Evidence

- [S-01][E2E] planned — defer to verify-e2e after relevant TASK verified
- [S-03][E2E] planned — defer to verify-e2e after relevant TASK verified
- [S-05][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-04][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-05][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-06][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-09][integration] planned — RED/GREEN pending
- [E-10][integration] planned — RED/GREEN pending
- [E-12][E2E] planned — defer to verify-e2e after relevant TASK verified
- [B-03][integration] planned — RED/GREEN pending
- [B-04][integration] planned — RED/GREEN pending
- [B-05][integration] planned — RED/GREEN pending

### Log

- [2026-10-07] created (draft)

---

## TASK-005: READ 并发与 MCP 请求正确性

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-003
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计
- **Spec-Refs**: harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: S-04, S-07, E-14, B-06

### Description

为显式声明安全 READ 的工具提供有界并发，并先修复 MCP adapter 的 initialize/request id correctness，再允许任何并发扩展。

### Checklist

- [ ] `ToolDefinition` 新增不可模型设置的 `concurrency` 声明：SERIAL default；PARALLEL_READ需要显式 handler 可重入、无共享可变副作用、明确 resource key 和独立性
- [ ] tool ademicer按原调用序连续 batch READ；资源冲突/未知依赖拆成串行；WRITE/EXTERNAL是前后屏障；每个 parallel batch bounded by `parallel_limit`
- [ ] first version only allow proved read-only tool：`search_skills`、`read_skill_resource`、isolated `get_task/list_tasks`；会写load_skill、memory、MCP default serial
- [ ] [S-04][integration] 两个允许并发independent READ均到启动屏障后才放行；WRITE在读批全部结束后才启动；返回消息顺序按原调用序
- [ ] [B-06][integration] READ未声明并发、共享有状态资源、同资源锁或未知依赖保持串行；并行度=1和上限边界均符合声明
- [ ] MCP每个冻结 server 会话使用 initialize singleflight; failure releases锁; no旧失败锁死
- [ ] request IDs per session counter/unique generator, no reuse; response id strict match; missing/mismatched is protocol error
- [ ] still Streamable HTTPonly, frozen catalog, unified registry, no in run tools/list, notool-level RBAC
- [ ] [S-07][integration] concurrent tools/call one initialize, IDs no reuse,responses严格 matched, no tools/list request
- [ ] [E-14][integration] errors response ID, initialize failure,isError, timeout, cancel明确 failed and关闭 connection；initialize失败后续调用可重试
- [ ] verifier harness-mcp#RULE-mcp-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 Runner、可控异步工具处理器 | independent READ both wait barrier before execute; WRITE starts after read batch; result order original | `tests/agent_runtime/test_tool_parallel_planner.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] | planned |
| S-07 | integration | 真实 MCP HTTP探针、Runtime adapter | concurrent tools/call only one initialize; IDs no response reuse; responses严格 matched; no tools/list | `tests/agent_runtime/test_mcp_request_correlation.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] | planned |
| E-14 | integration | MCP HTTP 探针、客户端关闭 | wrong response ID、init failure、isError、timeout、cancel all明确 failed and close connection; later call retry possible; no old failed lock死 | `tests/agent_runtime/test_mcp_request_correlation.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] | planned |
| B-06 | integration | 并行规划器、Runner、资源声明 | undeclared并行、shared mutable resource、same lock key or unknown dependency remains serial; parallel=1 and max boundary | `tests/agent_runtime/test_tool_parallel_planner.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] | planned |

### Acceptance Evidence

- [S-04][integration] planned — RED/GREEN pending
- [S-07][integration] planned — RED/GREEN pending
- [E-14][integration] planned — RED/GREEN pending
- [B-06][integration] planned — RED/GREEN pending

### Log

- [2026-10-07] created (draft)

---

## TASK-006: Gateway 等待态、重连与主动投递

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-004
- **Source**: async-tool-runtime.backend.design.md#3.4 接口设计, async-tool-runtime.backend.design.md#3.2 架构设计
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: S-06, E-13, E-17

### Description

Gateway 只订阅持久事件并执行等待态语义。原消息回复话题的 Runtime 只 tail canonical，SSE disconnect does not cancel. If original ReplySession invalid, use adapter-neutral active delivery with idempotency key.

### Checklist

- [ ] Runtime SSE只在 after事件入库后发出；等待期间 run.waiting non-terminal, no completion frame; run.resumed starts new timing segment; total Run time includes interrupted segments
- [ ] Client reconnect uses latest confirmed canonical seq, no duplicate POST create; same after_seq replaydoes not重复提交 Run/tool
- [ ] Gateway doesn't own business DB orexecution归属 mapping; background tasks continue during SSE disconnect; terminal only once
- [ ] hit WAITING_INPUT:explicit human resume same type; ordinary new message follows既有 route queue, no direct Runtime new Run; direct call returns RUN_BUSY
- [ ] /stop沿现有command容量可取消waiting；不能解释成会话可同时第二个 Run
- [ ] ReplySession失效 then channel-neutral port stable `run:{run_id}:final` active delivery key; adapter能力不存在时显式投递失败，不能回“已送达”
- [ ] [S-06][E2E] JOIN等待阶段不发完成帧；接续后在同消息回复输出最终文本，无额外 Worker FINAL_ONLY通知;/stop不被排队阻塞
- [ ] [E-13][E2E] 等待期间断开SSE，任务仍执行；新连接从已确认seq回放；无新Task/模型预算重置，终态只输出一次
- [ ] [E-17][E2E] WAITING_TOOL时explicit human resume被RUN_BUSY拒绝；普通新消息沿既有路由队列且不替换等待输入；/stop可取消，之后新Run可正常创建
- [ ] verifier harness-im#RULE-im-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-im#RULE-im-002：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | Gateway、Runtime SSE、Worker、原消息回复探针 | 等待期间不发完成帧；接续后同消息回复最终文本；无额外 Worker FINAL_ONLY通知；/stop不被排队阻塞 | `tests/acceptance/gateway/test_waiting_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | planned |
| E-13 | E2E | SSE socket、Runtime supervisor、Gateway/重连客户端 | 等待期间断开SSE任务仍执行；新连接从已确认seq回放；无新Task/模型预算重置，终态只输出一次 | `tests/acceptance/gateway/test_waiting_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | planned |
| E-17 | E2E | Gateway/Runtime HTTP、PG活跃会话约束 | WAITING_TOOL时explicit resume RUN_BUSY；普通新消息沿既有 route queue不替换等待输入；/stop可取消，之后新Run正常创建 | `tests/acceptance/gateway/test_waiting_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | planned |

### Acceptance Evidence

- [S-06][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-13][E2E] planned — defer to verify-e2e after relevant TASK verified
- [E-17][E2E] planned — defer to verify-e2e after relevant TASK verified

### Log

- [2026-10-07] created (draft)

---

## TASK-007: Console 只读安全投影与 operations API

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-004
- **Source**: async-tool-runtime.backend.design.md#3.4 接口设计, async-tool-runtime.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: 
- **Acceptance-Refs**: E-11

### Description

扩展 Runtime/Console 的只读查询，但不暴露 original input/result/credential. Tenant/actor/run/operation/task/snapshot binding verified before rendering or endpoint response.

### Checklist

- [ ] `GET /api/v1/runs/{id}` 增加 waiting_since、deadline_at、waiting_reason、pending_join_count、pending_submission_count、continuation_count且区分 WAITING_TOOL/WAITING_INPUT
- [ ] `GET /api/v1/runs/{id}/operations` items/page/page_size/total default 15, page_size≤100；only身份/状态/时间/关联Task，无 input/result
- [ ] operation SUBMIT_FAILED not equal Task FAILED; task status批量补齐，分页与 count共用条件
- [ ] Console租户只从登录账号，不取伪造 header；non-ADMIN no new credential input
- [ ] [E-11][E2E] 无服务身份、跨租户、actor/run/operation/task/hash不匹配拒绝；不写 inbox、不泄露另租户存在性，Console伪造租户头无效

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-11 | E2E | 内部服务门控、Runtime/Console HTTP、PG | no service identity/cross-tenant/actor/run/hash mismatch rejected; no inbox; no存在 leak; Consolespoofed tenant invalid | `tests/acceptance/security/test_internal_service_identity.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/security/test_internal_service_identity.py"] | planned |

### Acceptance Evidence

- [E-11][E2E] planned — defer to verify-e2e after relevant TASK verified

### Log

- [2026-10-07] created (draft)

---

## TASK-008: Console Run 详情与关联 operation UI

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: async-tool-runtime.frontend.design.md#3.2 页面与路由结构, async-tool-runtime.frontend.design.md#3.3 组件设计, async-tool-runtime.frontend.design.md#3.5 状态与数据流, async-tool-runtime.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-20, S-21, S-22, S-23, S-24, E-20, E-21, E-22, B-20, B-21

### Description

把等待状态、关联操作和事件轮廓接入现有只读 RunDetailSideSheet。第一版不新增 Run 列表/聊天页/主动调度/原文/结果明文/凭据入口。

### Checklist

- [ ] only `modules/run-observability/services/runs.ts` imports共享 api；components/hooks 不naked axios/fetch/api
- [ ] `RunStatus` union加入 WAITING_TOOL；RunDetail类型新增 waiting_since/deadline_at/waiting_reason/pending counts/continuation_count
- [ ] operations tab Page类型 `Page<RunOperationOutline>`，default page 15, page_size≤100, not裸数组；error_phase SUBMIT/EXECUTE/null, status精确 union, unknown task_status不显示作“任务失败”
- [ ] components no裸 HTTP; local requestSeq invalidation；关闭/切对象/卸载使过期响应失效；refresh只使当前对象数据失效
- [ ] `RunOperationTable` onOpenTask raises click intention, no导航; task_id null both cell and action unreachable click
- [ ] Run→Task closes relatedRun再打开Task；来源Run回调反向替换；最多来源详情+一层关联详情；不递归堆叠
- [ ] event dynamic keys：TOOL_SUBMISSION_PENDING、TOOL_TASK_ACCEPTED、TOOL_RESULT_RECEIVED、BACKGROUND_RESULT、BACKGROUND_RESULT_LATE、RUN_WAITING_TOOL、RUN_RESUMED；未知事件安全 fallback; unknown表示不渲染payload
- [ ] status/event/timestamp use locale/current language, DateTimeText unique; no cached translation at module state
- [ ] [S-20][E2E] 从审计打开真实JOIN等待Run，显示“等待任务结果”、等待时间和数量；结果已到尚未claim说明“等待接续”；完成Task后点刷新，接续/完成且数量归零
- [ ] [S-21][E2E] 点击关联 task_id 打开既有 Task 详情，来源Run返回同run_id; repeated switching no crash/no long stack
- [ ] [S-22][E2E] SUBMIT_PENDING task_id null no fake link; accepted DETACH明确独立模式; >15可翻页，total一致
- [ ] [S-23][E2E] switch zh-CN/en-US whileopen详情，status/event/tab即时切换；UTC time按Asia/Shanghai显示YYYY-MM-DD HH:mm:ss; invalid value“-”
- [ ] [S-24][E2E] seed真实 input/result盘; detail和network response不含原文/结果/凭据，只结构;timeline超200明确截断提示
- [ ] [E-20][E2E] GET失败显示ErrorState，恢复后重试实际数据; invalid session统一登录 redirect; no旧对象闪烁
- [ ] [E-21][E2E] cross-tenant/nonexistentTask链接404 returns原Run; spoofed X-Tenant-Id不改; non-ADMIN no新凭据入口
- [ ] [E-22][E2E] A Run慢响应切到B/关闭后才返回；当前详情不被A覆盖，卸载后无状态更新/无效链接
- [ ] [B-20][E2E] 无异步操作空态; 15/16条page boundary correct; after states delete/cancel current empty page returns legal page number; truncation提示 independent
- [ ] [B-21][E2E] Tab可达refresh、关联Task、返回和close; ESC只关当前面板，焦点回来源链接; status not颜色 only
- [ ] verifier harness-frontend#RULE-front-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-i18n#RULE-i18n-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-ui#RULE-ui-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [ ] verifier harness-ui-detail#RULE-ui-detail-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-20 | E2E | Browser→Console→PG；Runtime/Worker/LLM HTTP | 从审计打开真实JOIN等待Run，显示等待任务结果/时间/数量；结果到尚未claim说明等待接续；完成Task刷新后接续/完成且归零 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| S-21 | E2E | Browser→Router/面板控制→Console Run/Task API→PG | 点击task_id打开既有Task详情，来源Run返回同run_id; repeated switching no crash/no infinite堆叠 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| S-22 | E2E | Browser、真实 Console分页、PG count | SUBMIT_PENDING task_id null无假链接；accepted DETACH明确独立模式；>15可翻页，total与实际一致 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| S-23 | E2E | Browser language/timezone、HTTP请求头、真实 DTO | zh-CN/en-US切换当前详情即时生效；UTC times by Asia/ShanghaiYYYY-MM-DD HH:mm:ss；invalid“-”；请求头拦截器 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| S-24 | E2E | Browser、Console真实只读投影、PG | 输入/结果原文标记不进入详情/network响应；只显示结构；timeline超200明确截断提示 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| E-20 | E2E | 故障代理、Browser、真实 Console重试 | GET失败ErrorState可重试；恢复后真实数据；invalid session统一登录；旧对象不闪 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| E-21 | E2E | Browser、账号租户、Console API | cross-tenant/nonexistent Task404 returns原Run；伪造租户头无效；非ADMIN无新凭据入口 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| E-22 | E2E | 延迟真实响应的代理、Browser、服务请求 | A慢响应后切到B/关闭；当前详情不被A覆盖；卸载后无状态更新/无效链接 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| B-20 | E2E | Browser、Console、真实 PG空列表/分页 | 无异步操作空态；15/16条边界正确；delete/cancel后当前空页回到合法页码；截断提示独立 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |
| B-21 | E2E | Chrome键盘、实际Semi SideSheet/Tab/链接 | Tab可达刷新/关联Task/返回/关闭；ESC只关当前面板，焦点回来源链接；状态不只颜色 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | planned |

### Acceptance Evidence

- [S-20][E2E] planned — defer to verify-e2e after full stack available
- [S-21][E2E] planned — defer to verify-e2e after full stack available
- [S-22][E2E] planned — defer to verify-e2e after full stack available
- [S-23][E2E] planned — defer to verify-e2e after full stack available
- [S-24][E2E] planned — defer to verify-e2e after full stack available
- [E-20][E2E] planned — defer to verify-e2e after full stack available
- [E-21][E2E] planned — defer to verify-e2e after full stack available
- [E-22][E2E] planned — defer to verify-e2e after full stack available
- [B-20][E2E] planned — defer to verify-e2e after full stack available
- [B-21][E2E] planned — defer to verify-e2e after full stack available

### Log

- [2026-10-07] created (draft)

---

## TASK-009: 端到端清单、inventory 闭合与验收库隔离

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008
- **Source**: async-tool-runtime.backend.design.md#2.5 验收条件, async-tool-runtime.frontend.design.md#2.4 验收条件, async-tool-runtime.backend.design.md#3.5 质量实现方案, async-tool-runtime.frontend.design.md#附录：状态用语
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-09

### Description

建立本需求独立的 E2E acceptance域、功能场景命令注册、inventory扰动门禁、验收清单、空库/旧盘快验和 dev 库隔离。先保证编码期的 functional RED/GREEN 与 verify-e2e的真实live stack分层。

### Checklist

- [ ] creates requirement-specific acceptance files listed in QUALITY-04；当前不宣称已通过，Plan阶段固定路径
- [ ] integration/unit tests write for planners, state machine, repositories, adapters, artifact事务, migration parity
- [ ] E2E scenarios use live stack and isolation:独立DB、独立 Redis番号、真实PG、真实Redis、真实HTTP/process/browser; not共享 dev 库抢任务
- [ ] all S/E/B in Acceptance Coverage mapped to one owner and one executable command; functional层级有具体pytest, E2E可放 command or verify-e2e command; manual only external edge and confirmed
- [ ] four inventory perturbations change gate: changed state, removed evidence row, forged scenario name, changed manifest boundary, all red; byte-for-byte restore green; missing inventory file fails
- [ ] design E2E不得降级为 unit/integration；verify-e2e runs后补 verified evidence and LOG
- [ ] dev server与testing共享PG/Redis时 no dev --reload worker for isolated验收；stop残留进程前不跑 acceptance
- [ ] verifier harness-test#RULE-test-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-09 | integration | 真实需求文件、manifest、inventory runner | 改状态、删证据行、伪造用例名、改 manifest 边界 four perturbations分别red；byte-for-byte restore后green；missing inventory fails | `tests/async_tool_runtime_inventory.py` | ["uv", "run", "pytest", "-q", "tests/async_tool_runtime_inventory.py"] | planned |

### Acceptance Evidence

- [B-09][integration] planned — RED/GREEN pending

### Log

- [2026-10-07] created (draft)
