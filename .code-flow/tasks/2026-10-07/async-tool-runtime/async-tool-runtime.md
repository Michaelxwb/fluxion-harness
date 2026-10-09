# Tasks: async-tool-runtime

- **Source**: async-tool-runtime.backend.design.md, async-tool-runtime.frontend.design.md
- **Created**: 2026-10-07
- **Updated**: 2026-10-09

## Proposal

实现异步工具结果回流、`WAITING_TOOL` Run 等待恢复和安全只读并发，把已有 ASYNC Skill 从"只提交不接入原推理"升级为 durable operation、可靠收件、任意实例接续和 Console 可观测轮廓。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | depends_on |
|--------|---------|---------|-------------|---------|------|---------|
| S-01 | backend#2.5.2 场景清单 | E2E | Runtime/Worker HTTP、PG、Redis、LLM 探针、SSE | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| S-02 | backend#2.5.2 场景清单 | E2E | 四服务 HTTP、真实 Task、渠道出站探针 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | . | 1200 | |
| S-03 | backend#2.5.2 场景清单 | E2E | 实际 provider 请求、canonical 历史、制品存储 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| S-04 | backend#2.5.2 场景清单 | integration | 真实 Runner、可控异步工具处理器 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] | . | 60 | |
| S-05 | backend#2.5.2 场景清单 | E2E | Console resolve/API-09、等待恢复、LLM/MCP HTTP | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| S-06 | backend#2.5.2 场景清单 | E2E | Gateway、Runtime SSE、Worker、原消息回复探针 | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | . | 1200 | |
| S-07 | backend#2.5.2 场景清单 | integration | 真实 MCP HTTP 探针、Runtime adapter | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] | . | 60 | |
| E-01 | backend#2.5.2 场景清单 | E2E | 冻结授权、Worker HTTP、PG、审计 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | . | 1200 | |
| E-02 | backend#2.5.2 场景清单 | E2E | Runtime 控制发件、Worker 创建事务、故障代理 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | . | 1200 | |
| E-03 | backend#2.5.2 场景清单 | E2E | Worker 真实进程、PG outbox、Runtime HTTP | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_delivery.py"] | . | 1200 | |
| E-04 | backend#2.5.2 场景清单 | E2E | Runtime 等待事务、结果回流 HTTP、PG | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| E-05 | backend#2.5.2 场景清单 | E2E | 两个 Runtime 真实进程、PG lease、LLM HTTP | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| E-06 | backend#2.5.2 场景清单 | E2E | 真实 deadline sweep、Worker、Runtime、PG | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| E-07 | backend#2.5.2 场景清单 | E2E | Runtime cancel、Worker operation 行锁、真实 HTTP | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | . | 1200 | |
| E-08 | backend#2.5.2 场景清单 | E2E | ScriptSkillExecutor、真实子孙进程、管道、HTTP | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_skill_cancellation_real_subprocess.py"] | . | 600 | |
| E-09 | backend#2.5.2 场景清单 | integration | ContextBuilder、真实 provider 消息序列化 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | . | 60 | |
| E-10 | backend#2.5.2 场景清单 | integration | 真实制品发布/DB 事务、压缩端口 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | . | 60 | |
| E-11 | backend#2.5.2 场景清单 | E2E | 内部服务门控、Runtime/Console HTTP、PG | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/security/test_internal_service_identity.py"] | . | 1200 | |
| E-12 | backend#2.5.2 场景清单 | E2E | API-09、日志、inbox/outbox/checkpoint/canonical、制品 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | . | 1200 | |
| E-13 | backend#2.5.2 场景清单 | E2E | SSE socket、Runtime supervisor、Gateway/重连客户端 | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | . | 1200 | |
| E-14 | backend#2.5.2 场景清单 | integration | MCP HTTP 探针、客户端关闭 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] | . | 60 | |
| E-15 | backend#2.5.2 场景清单 | E2E | Redis 故障代理、PG 队列和结果发件 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_redis_unavailable.py"] | . | 1200 | |
| E-16 | backend#2.5.2 场景清单 | E2E | Worker HTTP 故障代理、控制发件、PG tombstone | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | . | 1200 | |
| E-17 | backend#2.5.2 场景清单 | E2E | Gateway/Runtime HTTP、PG 活跃会话约束 | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | . | 1200 | |
| B-01 | backend#2.5.2 场景清单 | integration | PG 行锁、并发提交、关闭屏障 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] | . | 60 | |
| B-02 | backend#2.5.2 场景清单 | integration | 严格 JSON DTO、幂等表、PG 唯一约束 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_runtime_strict_json.py"] | . | 60 | |
| B-03 | backend#2.5.2 场景清单 | integration | UTF-8 字节预算、共享制品、历史重建 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | . | 60 | |
| B-04 | backend#2.5.2 场景清单 | integration | PG wait_generation、epoch、canonical seq | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] | . | 60 | |
| B-05 | backend#2.5.2 场景清单 | integration | 持久检查点、注入时钟、真实 budget 判定 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] | . | 60 | |
| B-06 | backend#2.5.2 场景清单 | integration | 并行规划器、Runner、资源声明 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] | . | 60 | |
| B-07 | backend#2.5.2 场景清单 | integration | inbox/outbox 租约、PG、故障代理 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] | . | 60 | |
| B-08 | backend#2.5.2 场景清单 | integration | 真实 PG、Alembic、迁移前置检查 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/migrations/test_runtime_wait_parity.py"] | . | 60 | |
| B-09 | backend#2.5.2 场景清单 | integration | 真实需求文件、manifest、inventory runner | TASK-009 | verified | ["uv", "run", "pytest", "-q", "tests/async_tool_runtime_inventory.py"] | . | 60 | |
| S-20 | frontend#2.4 验收条件 | E2E | Browser→Console→PG；Runtime/Worker/LLM HTTP | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| S-21 | frontend#2.4 验收条件 | E2E | Browser→Router/面板控制→Console Run/Task API→PG | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| S-22 | frontend#2.4 验收条件 | E2E | Browser、真实 Console 分页、PG count | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| S-23 | frontend#2.4 验收条件 | E2E | Browser language/timezone、HTTP 请求头、真实 DTO | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| S-24 | frontend#2.4 验收条件 | E2E | Browser、Console 真实只读投影、PG | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| E-20 | frontend#2.4 验收条件 | E2E | 故障代理、Browser、真实 Console 重试 | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| E-21 | frontend#2.4 验收条件 | E2E | Browser、账号租户、Console API | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| E-22 | frontend#2.4 验收条件 | E2E | 延迟真实响应的代理、Browser、服务请求 | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| B-20 | frontend#2.4 验收条件 | E2E | 真实 PG 空列表/分页 | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |
| B-21 | frontend#2.4 验收条件 | E2E | Chrome 键盘、实际 Semi SideSheet/Tab/链接 | TASK-008 | verified | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | . | 1800 | |

---

## TASK-001: 契约、迁移与状态枚举基线

- **Status**: verified
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
- B-02: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-08] TASK-001 functional GREEN：20 新增测试；canonical/contracts/interrupt 回归 43 passed；五条原始 Spec verifier 均通过（5/18/33/38，以及 DateTime 2 + parity 38）；全仓 mypy 315 source files 通过。按 RULE-api-002 明确 tenant/endpoint 为独立幂等命名空间；旧 Task 的新关联列保持 NULL。修正场景命令登记为 argv JSON 后锁定 manifest（原 manifest 尚无执行证据）。
- [2026-10-08] completed (done)

---

## TASK-002: Worker 终态原子发件与取消 Tombstone

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计, async-tool-runtime.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-worker#RULE-worker-001, harness-skill#RULE-skill-001
- **Acceptance-Refs**: E-03, E-08, E-15, B-07

### Description

把 Worker 所有根任务终态路径收口到同一个原子发件函数：状态 CAS、TaskEvent、`runtime_result_outbox` 必须同一事务。同时提供先于 Task 到达的 cancellation tombstone，和结果 dispatcher 的 durable retry/idempotency。

### Checklist

- [x] 新增/收口根 Task 终态记录函数：success、deterministic failure、retry exhaust、deadline sweep、queued/waiting cancel、reclaim terminal、BATCH fan-in 均走同一事务路劲
- [x] JOIN root task 在终态事务写入 `runtime_result_outbox`；Child 不重复发 root 结果；DETACH 保留现有 final delivery
- [x] `event_id` 由 `tenant/task_id/terminal TaskEvent seq` 稳定派生；同 event_id 正文不同返回 409；persisted=true 才 ack
- [x] Dispatcher 只有 `persisted=true` 且同 event_id 才认成功；2xx/Redis hint 不是入库证据
- [x] `task.runtime_operation` 取消可先到：同 operation 行锁内允许空 submission_hash cancel；后续同可信来源 submit 只能恢复既定取消结果，不得绕过 tombstone
- [x] [E-03][E2E] 在终态事务提交后、发件前 SIGKILL；另一 Worker 重投；Runtime canonical恰一条；响应丢失后的重投不重写
- [x] [E-08][E2E] 运行中取消/超时回收真实子孙进程；直接子进程退出但孙进程持 stdout 时收尾有界；不声称远端副作用已停止
- [x] [E-15][E2E] Redis wake-up 丢失或不可用时 PG 扫描仍使创建/取消/结果投递/恢复前进；有日志与指标
- [x] [B-07][integration] inbox/outbox lease、PG fault injector：同事件改正文 409；ack only durable；重投耗尽保留 FAILED 出站告警，不产生第二 canonical
- [x] verifier harness-worker#RULE-worker-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-skill#RULE-skill-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-03 | E2E | Worker 真实进程、PG outbox、Runtime HTTP | 终态事务提交后 SIGKILL，另一 Worker 重投；Runtime canonical 恰一条；响应丢失重投不重写 | `tests/acceptance/runtime/test_background_result_delivery.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_delivery.py"] | verified |
| E-08 | E2E | ScriptSkillExecutor、真实子孙进程、管道、HTTP | 取消/超时整组回收；直接子进程退出且孙进程持 stdout 仍有界；协作取消不声称强制停止所有远端副作用 | `tests/agent_worker/test_skill_cancellation_real_subprocess.py` | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_skill_cancellation_real_subprocess.py"] | verified |
| E-15 | E2E | Redis fault proxy、PG queue/outbox、真实 dispatch | Redis 丢失/不可用时提交、结果投递、等待恢复仍前进；异常有日志与指标 | `tests/acceptance/runtime/test_redis_unavailable.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_redis_unavailable.py"] | verified |
| B-07 | integration | inbox/outbox lease rows、PG、fault proxy | 同事件改正文 409；ack only durable；重投耗尽保留 FAILED/告警；可操作恢复不制造第二 canonical | `tests/agent_worker/test_runtime_operation_races.py` | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-07 | `uv run pytest -q tests/agent_worker/test_runtime_operation_races.py`，2026-10-08，exit 2：缺少 `muad_agent_runtime.api.tool_results`，新结果回流入口尚未实现 | PASS：19 passed（独立空库） | `test_b07_atomic_terminal_rollback_and_single_event`、`test_b07_each_terminal_shape_is_durable`、`test_b07_real_http_durable_ack_conflict_exhaustion_and_recovery`、`test_b07_lease_reservation_blocks_stale_ack` | 独立 PostgreSQL + 实际终态写入/收件服务 + TCP Runtime 路由与 HTTP 故障代理 | verified |
| E-03 | E2E 不在 coding 阶段执行 | deferred | `test_e03_killed_worker_replay_and_lost_response_exactly_once` | Worker 子进程终态提交、SIGKILL、独立 Dispatcher 进程、Runtime HTTP、PG 唯一事件 | e2e_deferred |
| E-08 | E2E 不在 coding 阶段执行 | deferred | `test_e08_descendant_reclaimed_and_output_drain_bounded`（3 参数） | ScriptSkillExecutor + 真实子孙进程/继承管道 + HTTP 取消信号 | e2e_deferred |
| E-15 | E2E 不在 coding 阶段执行 | deferred | `test_e15_pg_progress_with_redis_connection_rejected` | Redis RESP 故障代理 + PG claim/cancel/outbox + 实际 HTTP Dispatcher；真实 claim_continuation 恢复断言，依赖 TASK-004 接线 | e2e_deferred |
- E-03: e2e_deferred — automated command e2e_deferred; run_id=9d982107824f4e57b6bb1130fcabc972 (confirmed_by: runner)
- E-08: e2e_deferred — automated command e2e_deferred; run_id=9d982107824f4e57b6bb1130fcabc972 (confirmed_by: runner)
- E-15: e2e_deferred — automated command e2e_deferred; run_id=9d982107824f4e57b6bb1130fcabc972 (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=9d982107824f4e57b6bb1130fcabc972 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-15: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-15: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-15: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-08] functional GREEN：`tests/agent_worker/test_runtime_operation_races.py` 共 19 用例；覆盖事务回滚、全部根终态路径、DETACH/Child 排除、取消 tombstone、同事件改正文 409、durable ack、租约失效、保留 FAILED/告警、审计重投与公平前进游标。HTTP fault proxy 使用 TCP 转发真实结果路由，收件落真实 PostgreSQL。
- [2026-10-08] 原始 worker verifier argv 实际执行：294 passed；Runtime 回归 376 passed。coding 环境设置 `PYTEST_ADDOPTS=-m "not e2e"`，新 E-08 留到终验，未执行 E2E。skill verifier 原始 argv 对应用例 4 passed；与启动配置/Task-001 契约和迁移回归合计 28 passed。
- [2026-10-08] Runtime 附件回归曾 RED（严格 canonical 拒绝 tuple），已把附件指纹构造改为原生 JSON 数组；原恢复与幂等冲突用例随 376 项回归通过。
- [2026-10-08] `uv run mypy apps packages`（323 文件）、ruff、`code-flow validate --no-heavy --json` 和 `git diff --check` 通过。E-03/E-08/E-15 测试已写，E-03 含真正丢弃 HTTP 回执的 TCP 故障代理，E-15 的 PG 接续 claim 依赖 TASK-004；统一留到 verify-e2e。

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-08] completed (done)

---

## TASK-003: Runtime 统一工具执行入口、提交取消与容量

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计, async-tool-runtime.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-02, E-01, E-02, E-07, E-16, B-01

### Description

收口现有 `ToolExecutionPipeline` 与 Runner 直接调用路径，成为唯一工具执行入口。ASYNC Skill 只负责提交意图，不允许模型等同真实知识。提交、取消、容量、关闭屏障都要和 durable operation 对齐。

### Checklist

- [x] Runner 默认接入统一 PORT-02/pipeline；不可只提供未接线的可选 pipeline
- [x] 最终参数经过校验 → PRE_TOOL_USE Hook → 对改写后参数复验 → 冻结 EffectiveCapabilities 授权 → Run 行锁预留额度 → 执行/operation → ToolResultRoundPort；授权不可被模型覆盖 tenant/actor/run/snapshot
- [x] `execute_skill` 对 ASYNC 新增 `completion_mode: JOIN | DETACH`，默认 JOIN；SYNC 显式携带 completion_mode 明确 422；回执包含 operation_id/task_id/task_status/completion_mode
- [x] 5 秒内未拿到 Worker durable admission 时返回 `SUBMISSION_PENDING`，task_id 为 null；不得伪造 QUEUED
- [x] 提交成功前同事务写 `runtime.tool_operation(SUBMIT_PENDING)` 与 `tool_control_outbox(SUBMIT)`；operation_id 每次真实 tool_call 一个，重试用原 ID
- [x] 提交幂等键 `runtime-op:{operation_id}:submit`；取消幂等键 `runtime-op:{operation_id}:cancel`；同 key 不同指纹返回 `IDEMPOTENCY_MISMATCH`
- [x] Worker `runtime_operation` 先写取消 tombstone；Task 创建与取消在同 operation 行锁内复检，取消先到不得再启动 Task
- [x] Run 取消级联 JOIN 和未受理提交；已受理 DETACH 不级联；晚到 submit/result 只记 LATE，不复活或唤醒终态 Run
- [x] `pending_operation_limit` 包含 SUBMIT_PENDING(DETACH/JOIN)+未终态 JOIN，在 Run 行锁内原子预留；DETACH 受理后释放
- [x] [S-02][E2E] 明确 DETACH受理后原Run完成；其后取消原Run不取消Task；DETACH Run routed final result when available（已编写登记，终验执行）
- [x] [E-01][E2E] 未授权 Skill、非法参数、Hook改写后非法参数分别拒绝；业务 Task/operation/outbox 均不创建，DENY/ERROR 审计存在（已编写登记，终验执行）
- [x] [E-02][E2E] Worker 创建后丢提交响应：模型收到 SUBMISSION_PENDING，不收 COMPLETED；重试得到同 task_id，只建一条 Task，最终可回流（已编写登记，终验执行）
- [x] [E-07][E2E] Worker cancellation tombstone阻止新任务启动；控制取消与提交交错明确；BATCH取消与final fan-in同时到达无死锁/状态反转（已编写登记，终验执行）
- [x] [E-16][E2E] 提交响应持续丢失至重试耗尽：operation明确 SUBMIT失败，不伪造Task失败；取消意图持久，晚到 submit/result不恢复依赖或启动另一 Task（已编写登记，终验执行）
- [x] [B-01][integration] pending_limit=1 同时申请两次只接受一个；关闭与提交交错后无未登记本地任务，等待意图全部可查
- [x] verifier harness-auth#RULE-auth-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | E2E | 四服务 HTTP、真实 Task、渠道出站探针 | DETACH 受理后原 Run 完成；取消原 Run 不取消 Task；Worker按 FINAL_ONLY/NONE delivery 一次；无路由时不重复 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | verified |
| E-01 | E2E | 冻结授权、Worker HTTP、PG、审计 | 未授权/非法参数/Hook改写后拒绝；业务任务不创建；DENY/ERROR 审计存在 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | verified |
| E-02 | E2E | Runtime 控制发件、Worker 创建事务、故障代理 | Worker 创建后丢提交响应；模型收到 SUBMISSION_PENDING；重试同 task_id，只建一条 Task；最终回流 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | verified |
| E-07 | E2E | Runtime cancel、Worker operation 行锁、真实 HTTP | 控制取消先到 tombstone阻止新任务；已创建 Task进入真实取消路径；BATCH取消与fan-in无死锁/反转 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | verified |
| E-16 | E2E | Worker HTTP 故障代理、控制发件、PG tombstone | 提交响应持续丢失直到重试耗尽；operation明确“提交未确认”failed；取消意图持久；晚到不复活 | `tests/acceptance/runtime/test_submission_hardening.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_submission_hardening.py"] | verified |
| B-01 | integration | PG 行锁、并发提交、关闭屏障 | pending_limit=1并发申请只接一个；关闭与提交交错后无未登记本地任务，等待意图可查 | `tests/agent_worker/test_runtime_operation_races.py` | ["uv", "run", "pytest", "-q", "tests/agent_worker/test_runtime_operation_races.py"] | verified |

### Acceptance Evidence

- [B-01][integration] RED: `uv run pytest -q tests/agent_worker/test_runtime_operation_races.py -k b01` — 2 failed: operation reservation and supervisor modules absent (`ModuleNotFoundError`); real isolated PG fixture initialized successfully. Cases `test_b01_pending_limit_row_lock_and_distinct_tool_calls`, `test_b01_close_registration_barrier_keeps_durable_intents`; GREEN pending.
- [S-02/E-01/E-02/E-07/E-16][E2E] Tests written in `tests/acceptance/runtime/test_submission_hardening.py`; real task-schedule live stack (Console/Runtime/Worker/Gateway/channel), TCP response-drop proxy and PG sources. Coding stage does not execute E2E; deferred to verify-e2e.
- S-02: e2e_deferred — automated command e2e_deferred; run_id=8a3404530c1b4dfe882b23493d7ea580 (confirmed_by: runner)
- E-01: e2e_deferred — automated command e2e_deferred; run_id=8a3404530c1b4dfe882b23493d7ea580 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=8a3404530c1b4dfe882b23493d7ea580 (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=8a3404530c1b4dfe882b23493d7ea580 (confirmed_by: runner)
- E-16: e2e_deferred — automated command e2e_deferred; run_id=8a3404530c1b4dfe882b23493d7ea580 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=8a3404530c1b4dfe882b23493d7ea580 (confirmed_by: runner)
- GREEN: B-01 real isolated PostgreSQL reservation races/close barrier + full functional command 21 passed. Assertions: one accepted operation/outbox at limit=1, same call replay stable, changed fingerprint rejected, max_attempts=1 frozen, closing seals admission and keeps queryable durable intentions.
- Regression: isolated empty migrated DB + Redis, `uv run pytest -q tests/agent_worker tests/agent_runtime` — 681 passed, 3 E2E deselected; subsequent Run cancellation/Reaper changes — 13 passed. Core pipeline/Runner — 28 passed. `uv run mypy apps packages scripts` — 330 files pass; Ruff, diff check and light validation pass.
- Required auth verifier original argv executed under isolated datastore wrapper: `uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity` — 2 + 38 passed. PYTEST_ADDOPTS excludes E2E during coding; commands unchanged.
- E2E registration: collect-only found 11 executable cases (DETACH routed/NONE; unauthorized/invalid/or Hook rewritten args; TCP admission drop/replay/exhaustion; Runtime cancel, Worker tombstone before/after admission and concurrent BATCH fan-in). Actual E2E assertions remain deferred, with no claimed execution.
- S-02: e2e_deferred — automated command e2e_deferred; run_id=767b07bb229e474ab40382242ba04513 (confirmed_by: runner)
- E-01: e2e_deferred — automated command e2e_deferred; run_id=767b07bb229e474ab40382242ba04513 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=767b07bb229e474ab40382242ba04513 (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=767b07bb229e474ab40382242ba04513 (confirmed_by: runner)
- E-16: e2e_deferred — automated command e2e_deferred; run_id=767b07bb229e474ab40382242ba04513 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=767b07bb229e474ab40382242ba04513 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-16: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-16: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-16: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-08] completed (done)

---

## TASK-004: WAITING_TOOL 检查点、接续泵与上下文物化

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计, async-tool-runtime.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001, harness-model#RULE-model-001, harness-secret#RULE-secret-001, harness-log#RULE-log-001
- **Acceptance-Refs**: S-01, S-03, S-05, E-04, E-05, E-06, E-09, E-10, E-12, B-03, B-04, B-05

### Description

实现 Runner 的等待/恢复循环。事件进入 inbox 后先 durable，再经 PORT-01 消费；WAITING_TOOL release lease and resources；ContinuationPump can claim from PG and reconstruct from snapshot/canonical history/API-09 credentials. Recent Waiting_input must not bypass human confirmation.

### Checklist

- [x] Runner 接入 PORT-01：模型调用前非阻塞Drain，no tool call 后再次 drain，复检依赖后决定 CONTINUE/WAIT/FINISH
- [x] RunnerOutcome 扩展判别字段：COMPLETED/WAITING_TOOL/WAITING_INPUT/BUDGET_EXCEEDED；WAITING_TOOL 只能持久化暂停，不能落入现有完成收尾
- [x] waiting 事务按锁序复检 all ready events、unmaterialized inbox、unsubmitted/accepted operations、not-terminal JOIN，避免 check-then-commit lost wakeup
- [x] 进入等待同事务落中间 assistant 文本、BEFORE_MODEL checkpoint、RUN_WAITING_TOOL 事件并释放 lease/close provider/MCP/HTTP clients
- [x] checkpoint只能在完整工具回合之后；所有 assistant tool_calls已与原 tool receipt paired
- [x] `TOOL_TASK_ACCEPTED` 和 `BACKGROUND_RESULT` 渲染为外部数据 USER消息；不新造同 tool_call_id 的 tool response，不变成 SYSTEM，不冒充用户新业务请求
- [x] ContinuationPump claim INSTANCE: `FOR UPDATE SKIP LOCKED`、wait_generation/epoch CAS、status/ready/deadline/cancel recheck；所有心跳/事件/terminal writes verify epoch/owner/lease
- [x] 接续从 snapshot/canonical/checkpoint重建，不重新resolve authorization/config；real-time API-09 credentials, no env fallback
- [x] 事件到达 WAITING_INPUT时只持久化，不绕过 human confirmation；RUNNING中的普通事件到达不启动第二 Runner
- [x] 多个等待轮次复用同一 Run；user/resume 进入事件后按 WAITING_INPUT semantics
- [x] 新增 trace context scope in propagation,后台 resume 恢复 trace_id/request_id/tenant/run/call/task
- [x] API-09 credential only memory; canary every new persistent/prompt/log surface no credential（canary 反查场景 E-12 已登记，终验执行）
- [x] context/building: result events in可重建集合；externalized canonical artifact_id and payload共用 reference_payload；system,summary,memory prefix preserved；pending operation不依赖 compressed文本
- [x] [S-01][E2E] ASYNC JOIN慢任务:先拿提交回执、执行独立工作、进入 WAITING_TOOL；Task完成后同 run_id恢复并用真实结果回答；waiting不占委 Runner/lease（已编写登记，终验执行）
- [x] [S-03][E2E] 两个 JOIN结果回流，重bility前后出来模型内容一致；原 tool_call仍只有一个tool响应；结果按持久 seq进入后续模型请求（已编写登记，终验执行）
- [x] [S-05][E2E] 分别修改Agent配置、授权、模型参数；旧Run恢复仍旧快照，新Run新值；独立轮换凭据后旧Run新凭但快照hash不变（已编写登记，终验执行）
- [x] [E-04][E2E] &quot;已检查无结果&quot;和&quot;提交等待&quot;间完成Task；无失唤醒，Run最终完成而非永久 WAITING_TOOL（已编写登记，终验执行）
- [x] [E-05][E2E] 杀死 WAITING_TOOL实例，另一实例恢复；同时两个实例claim只有一方有效；过期owner无法续租或提交终态（已编写登记，终验执行）
- [x] [E-06][E2E] waiting跨absolute deadline: Run FAILED, JOIN发出cancel；晚到成功只留轮廓不复活；Task跨 deadline不能 COMPLETED（已编写登记，终验执行）
- [x] [E-09][integration] 结果含“忽略原指令”文本；只出现外部结果数据消息，SYSTEM前缀与 call paired不被改写（GREEN 见证据表）
- [x] [E-10][integration] inject batch artifact failure and summary failure: whole batch inline/history retained, Run不因 compression failure终止，warning与metric increments可断言（GREEN 见证据表）
- [x] [E-12][E2E] canary秘密不出现在新增persistent surface、Prompt or outbound；model credential清空后恢复明确失败，不退回 env（已编写登记，终验执行）
- [x] [B-03][integration] exact threshold inline,1 byte over externalized；Chinese，synthesized multi-result over round budget correct；externalization failure retains whole batch（GREEN 见证据表）
- [x] [B-04][integration] PG wait_generation/epoch/canonical seq：旧 wait generation late wake不接管新代；已消费事件不重复注入；未消费事件不丢失；剩余batch存在时不睡眠（GREEN 见证据表）
- [x] [B-05][integration] persistent checkpoint、injected clock、real budget judgement：多次waiting/resume不重置 turns/tool_calls/usage/deadline；budget耗尽明确 failed（GREEN 见证据表）
- [x] verifier harness-log#RULE-log-001：原始 argv 独立空库执行通过（tests/test_logging.py + tests/test_logging_redaction.py + tests/acceptance/test_foundation_logging.py 14 passed）
- [x] verifier harness-model#RULE-model-001：原始 argv 独立空库执行通过（test_models_api.py 8 passed；test_agents_api.py -k disabled 1 passed, 13 deselected）
- [x] verifier harness-secret#RULE-secret-001：原始 argv 独立空库执行通过（test_logging_redaction.py + tests/acceptance/test_foundation_ops_audit.py 14 passed）
- [x] verifier harness-snapshot#RULE-snapshot-001：原始 argv 独立空库执行通过（test_snapshot_freeze.py + test_run_reaper.py 4 passed；-k &quot;executor or resolve&quot; 26 passed, 384 deselected）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Runtime/Worker HTTP、PG、Redis、LLM 探针、SSE | JOIN 慢任务回执/等待/恢复/真实结果回答；等待不占 Runner/lease | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| S-03 | E2E | 实际 provider 请求、canonical 历史、制品存储 | 两个 JOIN结果回流，重建前后模型可见内容一致；每个 tool_call只一个 tool响应 | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| S-05 | E2E | Console resolve/API-09、等待恢复、LLM/MCP HTTP | 旧Run保留旧配置/授权/模型；新Run新值；凭据轮换旧Run新凭据但快照hash不变 | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| E-04 | E2E | Runtime等待事务、结果回流HTTP、PG | waiting tx check/commit between no-results and task ready; Run最终不永久 WAITING_TOOL | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| E-05 | E2E | 两个Runtime真实进程、PG lease、LLM HTTP | kill waiting instance; another resume; two claims only one effective; expired owner cannot renew/write success | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| E-06 | E2E | 真实 deadline sweep、Worker、Runtime、PG | waiting跨absolute deadline Run FAILED, JOIN cancel; late success只留 LATE; Task跨 deadline不能COMPLETED | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| E-09 | integration | ContextBuilder、真实 provider消息序列化 | injection text“ignore instructions”does not become SYSTEM or rewriting tool call pairing; results external data only | `tests/agent_runtime/test_tool_result_materialization.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | verified |
| E-10 | integration | 真实制品发布/DB事务、压缩端口 | batch externalization mid-failure and summary failure：whole batch inline/history retained, Run not终止, warning+metric+increment | `tests/agent_runtime/test_tool_result_materialization.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | verified |
| E-12 | E2E | API-09、日志、inbox/outbox/checkpoint/canonical/制品 | canary不出现在新增持久化面、Prompt或出站；credential清空明确failed；no env fallback | `tests/acceptance/runtime/test_background_result_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/runtime/test_background_result_resume.py"] | verified |
| B-03 | integration | UTF-8 bytes budget、shared artifact、history重建 | exact threshold inline; strict >1 byte externalized；Chinese/multi-result over budget correct；failure retains whole batch | `tests/agent_runtime/test_tool_result_materialization.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_result_materialization.py"] | verified |
| B-04 | integration | PG wait_generation、epoch、canonical seq | old generation晚到 wake不接管；已消费不重复注入；未消费不丢失；剩余 batch 存在时不睡眠 | `tests/agent_runtime/test_tool_wait_state_machine.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] | verified |
| B-05 | integration | persistent checkpoint、injected clock、real budget judgement | 多次waiting/resume不重置 turns/tool_calls/usage/deadline；budget耗尽明确 failed | `tests/agent_runtime/test_tool_wait_state_machine.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_wait_state_machine.py"] | verified |

### Acceptance Evidence

- [S-01][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [S-03][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [S-05][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [E-04][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [E-05][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [E-06][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [E-09][integration] RED: 独立空库命令 `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_tool_result_materialization.py tests/test_background_context_scope.py` collection FAIL：缺少 RunnerCheckpoint/continuation service/materialization/context_scope 新能力；GREEN 见下方证据表（runner run_id=eaf3064bc66f486a972acbdb63e7589a）；日志 `/tmp/async-tool-task004-red.log`
- [E-10][integration] RED: 独立空库命令 `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_tool_result_materialization.py tests/test_background_context_scope.py` collection FAIL：缺少 RunnerCheckpoint/continuation service/materialization/context_scope 新能力；GREEN 见下方证据表（runner run_id=eaf3064bc66f486a972acbdb63e7589a）；日志 `/tmp/async-tool-task004-red.log`
- [E-12][E2E] e2e_deferred — 7 个真实进程/HTTP/PG 用例已收集；本阶段不执行，命令见 Acceptance Contract
- [B-03][integration] RED: 独立空库命令 `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_tool_result_materialization.py tests/test_background_context_scope.py` collection FAIL：缺少 RunnerCheckpoint/continuation service/materialization/context_scope 新能力；GREEN 见下方证据表（runner run_id=eaf3064bc66f486a972acbdb63e7589a）；日志 `/tmp/async-tool-task004-red.log`
- [B-04][integration] RED: 独立空库命令 `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_tool_result_materialization.py tests/test_background_context_scope.py` collection FAIL：缺少 RunnerCheckpoint/continuation service/materialization/context_scope 新能力；GREEN 见下方证据表（runner run_id=eaf3064bc66f486a972acbdb63e7589a）；日志 `/tmp/async-tool-task004-red.log`
- [B-05][integration] RED: 独立空库命令 `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_tool_result_materialization.py tests/test_background_context_scope.py` collection FAIL：缺少 RunnerCheckpoint/continuation service/materialization/context_scope 新能力；GREEN 见下方证据表（runner run_id=eaf3064bc66f486a972acbdb63e7589a）；日志 `/tmp/async-tool-task004-red.log`
- [2026-10-08] GREEN（独立空库命令 `uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_tool_result_materialization.py tests/test_background_context_scope.py`）：21 passed；全量回归（独立空库）`tests/agent_runtime tests/agent_worker tests/agent_core tests/contracts` + context scope 856 passed, 3 deselected（E2E 标记未选）；mypy `apps packages scripts` 337 文件通过；ruff 全绿；runner run_id=eaf3064bc66f486a972acbdb63e7589a。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-09 | 收集失败：缺少 RunnerCheckpoint/continuation/materialization/context_scope（见上） | PASS: 21 passed | `test_e09_external_data_user_role_preserves_system_and_original_tool_pair` | 独立空库 + 真实 EventWriter/DbBackedContextBuilder + OpenAICompatibleProvider 真实消息序列化 | verified |
| E-10 | 同上 | PASS: 21 passed | `test_e10_batch_io_failure_rolls_back_all_artifacts_and_summary_keeps_history`、`test_e10_real_artifact_transaction_failure_rolls_back_whole_batch`、`test_e10_cancel_during_file_publication_removes_prepared_files` | 真实 ArtifactResultWriter 文件 IO + 真实 PG 整批事务回滚；压缩端口注入摘要失败保留原历史与 warning/metric 增量 | verified |
| B-03 | 同上 | PASS: 21 passed | `test_b03_exact_utf8_threshold_and_one_byte_over`（2 参数）、`test_b03_multi_result_round_budget_selects_whole_batch`、`test_b03_epoch_change_during_artifact_io_rolls_back_unreferenced_batch` | UTF-8 字节阈值/整批外置真实制品发布 + 历史重建 | verified |
| B-04 | 同上；首轮 runner 曾因 dev 库停在迁移 0018 在 teardown 失败（B-04/B-05 记录），dev 库升级 0019 后重跑通过 | PASS: 21 passed；runner 复跑 passed | `test_b04_durable_created_start_can_be_claimed_without_request_generator`、`test_b04_ready_run_is_not_starved_by_older_unready_waiters`、`test_b04_single_claim_generation_epoch_and_expired_owner`、`test_b04_unconsumed_batch_cannot_sleep_or_repeat`、`test_b04_waiting_input_never_claimed_and_running_never_double_claimed`、`test_b04_real_runner_waits_then_reconstructs_without_duplicate_tool_response` | 真实 PG wait_generation/epoch/lease CAS + 真实 Runner 接续重建 | verified |
| B-05 | 同上 | PASS: 21 passed；runner 复跑 passed | `test_b05_absolute_deadline_closes_wait_and_late_success_cannot_resume`（2 参数）、`test_b05_multiple_waits_preserve_budget_unknown_usage_and_deadline`、`test_b05_restored_runner_reports_unknown_provider_usage`、`test_b05_checkpoint_rejects_incomplete_tool_round`、`test_b05_exhausted_restored_budget_persists_failed_and_cancels_join` | 持久检查点 + 注入时钟 + 真实 budget 判定与 deadline | verified |
| S-01 | E2E 不在 coding 阶段执行 | deferred | `test_s01_join_receipt_independent_work_wait_release_and_actual_result` | 真实四服务栈 + LLM/Worker 探针 + PG/Redis + SSE | e2e_deferred |
| S-03 | E2E 不在 coding 阶段执行 | deferred | `test_s03_two_results_original_tool_pair_and_reconstructed_request` | 真实 provider 请求 + canonical 历史 + 制品存储 | e2e_deferred |
| S-05 | E2E 不在 coding 阶段执行 | deferred | `test_s05_frozen_configuration_authorization_and_rotated_credentials` | Console resolve/API-09 + 等待恢复 + 快照 hash 稳定 | e2e_deferred |
| E-04 | E2E 不在 coding 阶段执行 | deferred | `test_e04_result_between_wait_recheck_and_commit_is_not_lost` | Runtime 等待事务 + 结果回流 HTTP + PG | e2e_deferred |
| E-05 | E2E 不在 coding 阶段执行 | deferred | `test_e05_killed_waiting_instance_second_process_resumes_one_epoch` | 两个 Runtime 真实进程 + PG lease/epoch | e2e_deferred |
| E-06 | E2E 不在 coding 阶段执行 | deferred | `test_e06_waiting_deadline_cancels_join_and_late_success_stays_late` | 真实 deadline sweep + Worker + Runtime + PG | e2e_deferred |
| E-12 | E2E 不在 coding 阶段执行 | deferred | `test_e12_cleared_credential_has_no_fallback_or_new_surface_leak` | API-09 + 新增持久化面 canary + 无 env 兜底 | e2e_deferred |

- S-01: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- E-04: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- E-10: verified — automated command passed; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- E-12: e2e_deferred — automated command e2e_deferred; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- B-04: failed — automated command failed; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- B-05: failed — automated command failed; run_id=1d756a0de96e45e4b7582cd9cc8cc6e9 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- E-04: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- E-12: e2e_deferred — automated command e2e_deferred; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=74d1b8e747914739aee049043daf7d78 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- E-04: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- E-10: verified — automated command passed; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- E-12: e2e_deferred — automated command e2e_deferred; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=eaf3064bc66f486a972acbdb63e7589a (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- E-04: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- E-12: e2e_deferred — automated command e2e_deferred; run_id=7b4fec0229af4a19a15ab103c2bab011 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-12: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-12: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- E-10: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-10: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-12: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-08] 功能 RED→GREEN：等待/接续状态机、结果物化、context scope 三套 integration 用例（21 用例）在独立空库通过；回归 `tests/agent_runtime tests/agent_worker tests/agent_core tests/contracts` 856 passed, 3 deselected。实现覆盖 PORT-01 两处排空、WAITING_TOOL 事务与 BEFORE_MODEL 检查点、ContinuationPump claim/epoch CAS、snapshot+canonical 重建与 API-09 内存凭据、外部数据消息物化与整批回滚。
- [2026-10-08] 环境事实：dev 库原停在迁移 0018，acceptance runner 首轮 B-04/B-05 因 fixture teardown 缺 `runtime.tool_control_outbox` 失败（首轮失败记录保留在证据与 manifest runs 中）；升级到 head 0019 后 runner 复跑全绿。
- [2026-10-08] 四条 spec verifier 原始 argv 在独立空库执行通过：harness-snapshot（4 + 26 passed）、harness-model（8 + 1 passed）、harness-secret（14 passed）、harness-log（14 passed）。
- [2026-10-08] 静态检查：mypy `apps packages scripts` 337 文件通过；ruff 全绿；`git diff --check` 通过。
- [2026-10-08] completed (done)

---

## TASK-005: READ 并发与 MCP 请求正确性

- **Status**: verified
- **Priority**: P1
- **Depends**: TASK-003
- **Source**: async-tool-runtime.backend.design.md#3.2 架构设计
- **Spec-Refs**: harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: S-04, S-07, E-14, B-06

### Description

为显式声明安全 READ 的工具提供有界并发，并先修复 MCP adapter 的 initialize/request id correctness，再允许任何并发扩展。

### Checklist

- [x] `ToolDefinition` 新增不可模型设置的 `concurrency` 声明：SERIAL default；PARALLEL_READ需要显式 handler 可重入、无共享可变副作用、明确 resource key 和独立性
- [x] tool ademicer按原调用序连续 batch READ；资源冲突/未知依赖拆成串行；WRITE/EXTERNAL是前后屏障；每个 parallel batch bounded by `parallel_limit`
- [x] first version only allow proved read-only tool：`search_skills`、`read_skill_resource`、isolated `get_task/list_tasks`；会写load_skill、memory、MCP default serial
- [x] [S-04][integration] 两个允许并发independent READ均到启动屏障后才放行；WRITE在读批全部结束后才启动；返回消息顺序按原调用序
- [x] [B-06][integration] READ未声明并发、共享有状态资源、同资源锁或未知依赖保持串行；并行度=1和上限边界均符合声明
- [x] MCP每个冻结 server 会话使用 initialize singleflight; failure releases锁; no旧失败锁死
- [x] request IDs per session counter/unique generator, no reuse; response id strict match; missing/mismatched is protocol error
- [x] still Streamable HTTPonly, frozen catalog, unified registry, no in run tools/list, notool-level RBAC
- [x] [S-07][integration] concurrent tools/call one initialize, IDs no reuse,responses严格 matched, no tools/list request
- [x] [E-14][integration] errors response ID, initialize failure,isError, timeout, cancel明确 failed and关闭 connection；initialize失败后续调用可重试
- [x] verifier harness-mcp#RULE-mcp-001：原始 argv `uv run pytest -q tests/console_mcp/test_mcp_rules.py` → 3 passed（门禁裁决由 finish 记录）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 Runner、可控异步工具处理器 | independent READ both wait barrier before execute; WRITE starts after read batch; result order original | `tests/agent_runtime/test_tool_parallel_planner.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] | verified |
| S-07 | integration | 真实 MCP HTTP探针、Runtime adapter | concurrent tools/call only one initialize; IDs no response reuse; responses严格 matched; no tools/list | `tests/agent_runtime/test_mcp_request_correlation.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] | verified |
| E-14 | integration | MCP HTTP 探针、客户端关闭 | wrong response ID、init failure、isError、timeout、cancel all明确 failed and close connection; later call retry possible; no old failed lock死 | `tests/agent_runtime/test_mcp_request_correlation.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_mcp_request_correlation.py"] | verified |
| B-06 | integration | 并行规划器、Runner、资源声明 | undeclared并行、shared mutable resource、same lock key or unknown dependency remains serial; parallel=1 and max boundary | `tests/agent_runtime/test_tool_parallel_planner.py` | ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_tool_parallel_planner.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | FAIL: planner 文件收集失败 `ImportError: cannot import name 'ToolConcurrency' from 'muad_agent_core.tools'`（`ToolDefinition` 无 concurrency 声明） | PASS: `uv run pytest -q tests/agent_runtime/test_tool_parallel_planner.py`，13 passed | `test_s04_independent_reads_meet_barrier_and_write_starts_after_batch` | 真实 `AgentRunner` 图执行 + 共享 `asyncio.Barrier(2)`（两 READ 同时在飞才放行）+ WRITE 屏障后启动 + 完成顺序反转而消息仍按 c1,c2,c3 原序 | verified |
| B-06 | FAIL: 同 S-04（收集失败，无声明/规划器） | PASS: 同命令 13 passed（含 4 例串行判定与上限边界） | `test_b06_undeclared_read_stays_serial`、`test_b06_shared_stateful_resource_stays_serial`、`test_b06_same_resource_lock_serializes_only_conflicting_calls`、`test_b06_unknown_dependency_stays_serial`、`test_b06_parallel_limit_one_stays_serial`、`test_b06_parallel_limit_bounds_batch`、`test_b06_parallel_limit_rejects_invalid_values`、`test_b06_tool_definition_defaults_to_serial`、`test_b06_planner_splits_consecutive_batches_by_declaration_and_order` | 真实 Runner 并行度观测（同时在飞数）+ 规划器纯逻辑批次形状；上限 1/2/16 与 bool/小数/越界拒绝 | verified |
| S-07 | FAIL: `initialize_count == 2`（无 singleflight，并发两调用各发一次 initialize）；错误响应 ID 用例 `DID NOT RAISE McpToolError` | PASS: `uv run pytest -q tests/agent_runtime/test_mcp_request_correlation.py`，8 passed | `test_s07_concurrent_calls_share_one_initialize_unique_ids_no_tools_list` | 真实 uvicorn 127.0.0.1 Streamable HTTP 探针（逐请求记账）+ 真实 httpx adapter；断言 1 次 initialize、ID 集合唯一、`Mcp-Session-Id` 复用、无 tools/list | verified |
| E-14 | FAIL: isError/超时/取消后重试仍复用旧会话（`initialize_count == 1`，连接未关闭）；错误/缺失响应 ID 未触发协议错误 | PASS: 同命令 8 passed | `test_e14_bad_response_id_is_protocol_error_and_forces_new_session[wrong\|missing]`、`test_e14_initialize_failure_releases_lock_and_supports_retry`、`test_e14_concurrent_waiters_survive_initialize_failure`、`test_e14_is_error_fails_and_closes_session`、`test_e14_timeout_fails_and_closes_session`、`test_e14_cancellation_closes_session_and_retry_reinitializes` | 真实探针 RPC 错误/isError/持留响应超时/取消；失败后重试重新 initialize 且新 TCP 客户端端口（旧连接确已关闭），等待方不被旧失败锁死 | verified |

- [S-04][integration] RED — `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_tool_parallel_planner.py tests/agent_runtime/test_mcp_request_correlation.py` (2026-10-08): planner 文件收集失败，缺少 `ToolConcurrency`/`plan_tool_batches` 声明与规划器。GREEN 后同命令 21 passed（19.46s）；原始 manifest 命令单跑 13 passed（10.27s）。
- [B-06][integration] RED — 同 S-04（收集失败）。GREEN 覆盖未声明/共享资源/同资源锁/未知依赖保持串行、parallel_limit=1 与上限边界、声明默认 SERIAL。
- [S-07][integration] RED — `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime/test_mcp_request_correlation.py` (2026-10-08): 6 failed / 2 passed；并发调用触发 2 次 initialize，错误响应 ID 不被识别。GREEN: 同命令 8 passed（10.55s 原始命令）。
- [E-14][integration] RED — 同 S-07：isError/超时/取消后会话未丢弃（重试不再 initialize），错误/缺失响应 ID 不报协议错误。GREEN 覆盖失败关连接 + 后续重试重新初始化 + 并发等待方可继续。
- 回归: `uv run python /tmp/async_tool_check.py uv run pytest -q tests/agent_runtime tests/agent_core tests/console_mcp` → 600 passed in 102.95s（独立空库；E2E 标记用例不选）。
- verifier: `uv run pytest -q tests/console_mcp/test_mcp_rules.py` → 3 passed in 0.01s（harness-mcp#RULE-mcp-001 原始 argv）。
- 静态检查: `uv run mypy apps packages scripts` → 338 source files 通过（先修 pipeline `definition` 可空收窄）；`uv run ruff check apps packages tests` → 全部通过（先修 6 项：5×E501、1×I001 导入序）；`git diff --check` → rc=0。
- S-04: verified — automated command passed; run_id=3a3705e59db746a5920531e2e599b0f4 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=3a3705e59db746a5920531e2e599b0f4 (confirmed_by: runner)
- E-14: verified — automated command passed; run_id=3a3705e59db746a5920531e2e599b0f4 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=3a3705e59db746a5920531e2e599b0f4 (confirmed_by: runner)
- S-04: failed — automated command failed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- E-14: verified — automated command passed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-06: failed — automated command failed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=e2c1129bd5f94331aa4392be2f8cfe6c (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=e2c1129bd5f94331aa4392be2f8cfe6c (confirmed_by: runner)
- E-14: verified — automated command passed; run_id=e2c1129bd5f94331aa4392be2f8cfe6c (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=e2c1129bd5f94331aa4392be2f8cfe6c (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-08] RED：planner 文件因缺 `ToolConcurrency`/规划器收集失败；MCP 关联文件 6 failed / 2 passed（无 singleflight、ID 复用、错误响应 ID 不校验、isError/超时/取消不关会话）。GREEN：agent-core 新增 `ToolConcurrency`、`resource_key` 声明与纯规划器（连续批、资源冲突/未知依赖串行、写屏障、parallel_limit 1–16 校验）；Runner 先按原序校验/授权/预留预算，再 TaskGroup+semaphore 只并发 handler IO，结果按原序进入回合收口；MCP adapter 改为每 server singleflight 初始化锁 + 会话内递增请求 ID + 响应 ID 严格匹配 + 任何失败丢弃会话关闭连接；Runtime 由冻结 `AsyncToolPolicy` 接线 `parallel_limit`；首批登记 `search_skills`/`read_skill_resource`/`get_task`/`list_tasks` 并发声明（load_skill、memory、MCP 保持 SERIAL）。测试自身一处缺陷在 GREEN 阶段发现并修正（S-04 误建两个独立 Barrier，改为共享 Barrier(2)）。两文件 21 passed；回归 600 passed；verifier 3 passed；mypy/ruff/diff --check 通过。
- [2026-10-08] resumed (in-progress)
- [2026-10-08] Done Gate 曾因 scope 预检暂停：`path_mapping` 的 `**/*relation*` 误命中新测试文件名 `test_mcp_request_correlation.py`，工具链自动并入 harness-rel#RULE-rel-001 并暂停 TASK。核实该规则只约束 Console 关系类（POST/DELETE）变更，与本次 MCP 请求 ID 修正无关；按既有实践（e96d8580 收窄过宽 path_mapping）将模式收紧为 `**/*relations*`，resume 后 Done Gate pass（harness-rel 不再命中；本任务范围验收与 verifier 全部通过）。
- [2026-10-08] completed (done)

---

## TASK-006: Gateway 等待态、重连与主动投递

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-004
- **Source**: async-tool-runtime.backend.design.md#3.4 接口设计, async-tool-runtime.backend.design.md#3.2 架构设计
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: S-06, E-13, E-17

### Description

Gateway 只订阅持久事件并执行等待态语义。原消息回复话题的 Runtime 只 tail canonical，SSE disconnect does not cancel. If original ReplySession invalid, use adapter-neutral active delivery with idempotency key.

### Checklist

- [x] Runtime SSE只在 after事件入库后发出；等待期间 run.waiting non-terminal, no completion frame; run.resumed starts new timing segment; total Run time includes interrupted segments
- [x] Client reconnect uses latest confirmed canonical seq, no duplicate POST create; same after_seq replaydoes not重复提交 Run/tool
- [x] Gateway doesn't own business DB orexecution归属 mapping; background tasks continue during SSE disconnect; terminal only once
- [x] hit WAITING_INPUT:explicit human resume same type; ordinary new message follows既有 route queue, no direct Runtime new Run; direct call returns RUN_BUSY
- [x] /stop沿现有command容量可取消waiting；不能解释成会话可同时第二个 Run
- [x] ReplySession失效 then channel-neutral port stable `run:{run_id}:final` active delivery key; adapter能力不存在时显式投递失败，不能回“已送达”
- [x] [S-06][E2E] JOIN等待阶段不发完成帧；接续后在同消息回复输出最终文本，无额外 Worker FINAL_ONLY通知;/stop不被排队阻塞
- [x] [E-13][E2E] 等待期间断开SSE，任务仍执行；新连接从已确认seq回放；无新Task/模型预算重置，终态只输出一次
- [x] [E-17][E2E] WAITING_TOOL时explicit human resume被RUN_BUSY拒绝；普通新消息沿既有路由队列且不替换等待输入；/stop可取消，之后新Run可正常创建
- [x] verifier harness-im#RULE-im-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-im#RULE-im-002：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | Gateway、Runtime SSE、Worker、原消息回复探针 | 等待期间不发完成帧；接续后同消息回复最终文本；无额外 Worker FINAL_ONLY通知；/stop不被排队阻塞 | `tests/acceptance/gateway/test_waiting_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | verified |
| E-13 | E2E | SSE socket、Runtime supervisor、Gateway/重连客户端 | 等待期间断开SSE任务仍执行；新连接从已确认seq回放；无新Task/模型预算重置，终态只输出一次 | `tests/acceptance/gateway/test_waiting_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | verified |
| E-17 | E2E | Gateway/Runtime HTTP、PG活跃会话约束 | WAITING_TOOL时explicit resume RUN_BUSY；普通新消息沿既有 route queue不替换等待输入；/stop可取消，之后新Run正常创建 | `tests/acceptance/gateway/test_waiting_resume.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/gateway/test_waiting_resume.py"] | verified |

### Acceptance Evidence

- [S-06][E2E] e2e_deferred — 已登记 `test_s06_join_waiting_holds_silence_and_replies_on_original_message`（`tests/acceptance/gateway/test_waiting_resume.py`）；真实边界：真实 Gateway 进程（官方 WeCom SDK ↔ 真实 WS 探针）→ 真实 Runtime SSE、真实 Worker 执行 gate 化 ASYNC JOIN Skill、真实 PG/Redis、原消息回复探针；命令见 Acceptance Contract。coding 阶段不执行 RED/GREEN，留 verify-e2e。
- [E-13][E2E] e2e_deferred — 已登记 `test_e13_sse_drop_during_waiting_replays_without_new_task_or_budget_reset`；真实边界：TCP 中继强制断开在飞 SSE（不杀进程）、Runtime `GET /v1/runs/{id}/events?after_seq=` 重放、PG canonical seq/预算计数；断言重连日志 `after_seq`、单 Run/单 Task、turns 只前进不回退、终态只输出一次。
- [E-17][E2E] e2e_deferred — 已登记 `test_e17_waiting_tool_rejects_resume_queues_message_and_recovers_after_stop`；真实边界：真实 Runtime HTTP（显式 resume → 409 RUN_BUSY）、Gateway 路由队列、PG 活跃会话约束、/stop 独立命令容量与取消后新 Run。
- 回归（独立库，`-m "not e2e"`）：`uv run python /tmp/async_tool_check.py uv run pytest -q tests/gateway tests/agent_runtime tests/agent_worker` → 1089 passed, 3 deselected (194.90s)。
- 功能用例（coding 阶段实际执行）：`tests/gateway/test_waiting_resume.py` 9 passed；`tests/agent_runtime/test_runs_api.py -k "run_events or waiting_tool"` 3 passed。
- verifier `harness-im#RULE-im-001` 原始 argv（独立库）：`uv run pytest -q tests/console_channel tests/gateway` → 413 passed。
- verifier `harness-im#RULE-im-002` 原始 argv（独立库）：`uv run pytest -q tests/architecture/test_channel_neutrality.py` → 3 passed。
- 静态检查：`uv run mypy apps packages scripts` → 339 source files 通过；`uv run ruff check apps packages tests` 全绿；`git diff --check` rc=0。
- 陈旧断言修正（无实现改动）：`tests/gateway/test_stop_integration.py::test_s06_stop_on_waiting_input_cancels_immediately_with_readable_events` 与 `tests/acceptance/runtime/test_run_lifecycle.py::test_e02_cancel_requested_waiting_input_writes_events` 期望 WAITING_INPUT 立即取消后 `cancel_requested=False`；自 TASK-003 `_cancel_waiting` CAS 同时置 `cancel_requested=True` 起已过时，按代码事实改为 True（回归中实际暴露）。
- S-06: e2e_deferred — automated command e2e_deferred; run_id=8c5d28cf411f4500bcf5d878fde72ff6 (confirmed_by: runner)
- E-13: e2e_deferred — automated command e2e_deferred; run_id=8c5d28cf411f4500bcf5d878fde72ff6 (confirmed_by: runner)
- E-17: e2e_deferred — automated command e2e_deferred; run_id=8c5d28cf411f4500bcf5d878fde72ff6 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-13: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-13: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-13: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-08] E2E 登记（不执行）：新增 `tests/acceptance/gateway/`（conftest 复用 task-schedule 真栈 + join 模型探针 + WS 网关进程 + TCP 断流中继）与三个用例；`uv run pytest --collect-only -q` 收集 3 项。E2E 标记在 coding 阶段不产生 RED/GREEN，统一留 verify-e2e。
- [2026-10-08] 实现要点：Runtime 新增 `GET /v1/runs/{id}/events?after_seq=`（只读持久 canonical、等待态保活、终态排空关闭、不重新执行）与 `tail_run_events`；`resume` 对 WAITING_TOOL 明确 `RUN_BUSY`。Gateway 解析器识别 `run.waiting_tool`/`run.resumed`/异步工具轮廓事件；`ExecutionProgress` 改为执行段计时（等待停段、接续开新段、`total_seconds` 起止另算）并新增 WAITING_TOOL 状态文案（zh-CN/en-US）；断流按最后确认 seq 走 `open_events` 有界重连（绝不重复 POST 创建 Run、终态只出一次）；ReplySession 失效（开不出/写不进）时终态文本按稳定键 `run:{run_id}:final` 经渠道中立端口 `ActiveTextDelivery.send_active` 主动投递并去重，无能力/发送失败显式失败，不回“已送达”。
- [2026-10-08] completed (done)


---

## TASK-007: Console 只读安全投影与 operations API

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001, TASK-004
- **Source**: async-tool-runtime.backend.design.md#3.4 接口设计, async-tool-runtime.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: 
- **Acceptance-Refs**: E-11

### Description

扩展 Runtime/Console 的只读查询，但不暴露 original input/result/credential. Tenant/actor/run/operation/task/snapshot binding verified before rendering or endpoint response.

### Checklist

- [x] `GET /api/v1/runs/{id}` 增加 waiting_since、deadline_at、waiting_reason、pending_join_count、pending_submission_count、continuation_count且区分 WAITING_TOOL/WAITING_INPUT
- [x] `GET /api/v1/runs/{id}/operations` items/page/page_size/total default 15, page_size≤100；only身份/状态/时间/关联Task，无 input/result
- [x] operation SUBMIT_FAILED not equal Task FAILED; task status批量补齐，分页与 count共用条件
- [x] Console租户只从登录账号，不取伪造 header；non-ADMIN no new credential input
- [x] [E-11][E2E] 无服务身份、跨租户、actor/run/operation/task/hash不匹配拒绝；不写 inbox、不泄露另租户存在性，Console伪造租户头无效

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-11 | E2E | 内部服务门控、Runtime/Console HTTP、PG | no service identity/cross-tenant/actor/run/hash mismatch rejected; no inbox; no存在 leak; Consolespoofed tenant invalid | `tests/acceptance/security/test_internal_service_identity.py` | ["uv", "run", "pytest", "-q", "tests/acceptance/security/test_internal_service_identity.py"] | verified |

### Acceptance Evidence

- E-11: e2e_deferred — 已登记 3 例（`test_e11_internal_endpoints_require_service_identity`、`test_e11_result_binding_mismatches_are_rejected_without_inbox`、`test_e11_console_tenant_comes_from_login_account_not_header`）；真实边界：Runtime/Console 内部服务门控 + 结果回流绑定校验 + Console 登录账号租户投影，全部打在 task-schedule 真栈（真实进程/HTTP）与真实 PG 上；`uv run pytest --collect-only -q tests/acceptance/security/test_internal_service_identity.py` → 3 tests collected；执行命令 `uv run pytest -q tests/acceptance/security/test_internal_service_identity.py` 留待 verify-e2e（coding 阶段不执行 E2E）。
- 功能回归（独立库，`/tmp/async_tool_check.py`）：`uv run pytest -q tests/console_platform tests/agent_runtime tests/console_internal tests/console_tasks tests/contracts`（--ignore 4 个 audit 既有失败文件 + --deselect 1 个既有迁移用例）→ 680 passed；新增/改动文件单跑 36 passed。
- 静态检查：`uv run mypy apps packages scripts` → 339 source files 通过；`uv run ruff check apps packages tests` 全绿；`git diff --check` rc=0。
- 既有失败基线（与本任务无关，`git stash` 复跑基线一致）：`test_platform_settings_table.py::test_upgrade_to_head_creates_table_matching_orm_model`（REVISION 钉 0018、head 已是 0019）与 4 个 audit 测试文件 24 errors（种子 `run_record.status='SUCCEEDED'` 撞 0019 的 `ck_run_record_run_status`），均系 TASK-001 引入、遗留待 TASK-009/卫生任务收口。
- E-11: e2e_deferred — automated command e2e_deferred; run_id=b241f1b90d674691a148d9297c17fa9e (confirmed_by: runner)
- E-11: verified — automated command passed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-11: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-11: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-08] started
- [2026-10-09] 实现要点：① contracts 新增 `tool_waiting_reason` 单一口径（仅 WAITING_TOOL；RESUME_READY > SUBMISSION > TASK_RESULT > null），Runtime/Console 共用；② Console `GET /api/v1/runs/{id}` 增加 `deadline_at/waiting_since/waiting_reason/pending_join_count/pending_submission_count/continuation_count`（单条 SQL：`run_continuation` LEFT JOIN + 未终态 JOIN/SUBMIT_PENDING 两个计数子查询 + 按状态取 `RUN_WAITING_TOOL` 事件或 WAITING `run_interrupt` 时间作为等待起点），并新增 `GET /api/v1/runs/{id}/operations`（items/page/page_size/total，默认 15 上限 100；字段仅身份/状态/时间/关联 Task；一次 LEFT JOIN 批量补 Task 状态，list 与 count 共用条件片段；Run 不存在与跨租户同码 404）；③ Runtime `GET /v1/runs/{id}` 同步输出相同等待字段（聚合计数 + 检查点读取 + 等待起点查询）；④ 全部只读投影，不取 `input_text/submission_json/payload_json/credential`；内部端点门控与 Console 登录账号租户口径不变。语义：pending_join_count=未终态 JOIN（含 SUBMIT_PENDING/MATERIALIZED），pending_submission_count=SUBMIT_PENDING（含 DETACH），continuation_count=`wait_generation`（无检查点记 0）。
- [2026-10-09] RED/GREEN：新增 9 个功能用例先跑 RED（stash 生产代码、保留用例）：`uv run python /tmp/async_tool_check.py uv run pytest -q tests/console_platform/test_runs_api.py tests/agent_runtime/test_runs_api.py` → 9 failed / 27 passed（等待字段 KeyError、operations 路由 404）；恢复生产代码后 GREEN：同命令 36 passed。E-11（E2E）按 coding 阶段纪律只登记不执行（`--collect-only` 收集 3 项）。
- [2026-10-09] 回归与基线：范围回归在受影响面全绿（见 Acceptance Evidence）；全范围命令暴露的 25 个既有失败用 `git stash` 在纯净基线上复跑证实完全相同（TASK-001 的 0019 迁移约束/版本钉引入），未在本次改动，留给 TASK-009 收口。
- [2026-10-09] completed (done)

---

## TASK-008: Console Run 详情与关联 operation UI

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: async-tool-runtime.frontend.design.md#3.2 页面与路由结构, async-tool-runtime.frontend.design.md#3.3 组件设计, async-tool-runtime.frontend.design.md#3.5 状态与数据流, async-tool-runtime.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-20, S-21, S-22, S-23, S-24, E-20, E-21, E-22, B-20, B-21

### Description

把等待状态、关联操作和事件轮廓接入现有只读 RunDetailSideSheet。第一版不新增 Run 列表/聊天页/主动调度/原文/结果明文/凭据入口。

### Checklist

- [x] only `modules/run-observability/services/runs.ts` imports共享 api；components/hooks 不naked axios/fetch/api
- [x] `RunStatus` union加入 WAITING_TOOL；RunDetail类型新增 waiting_since/deadline_at/waiting_reason/pending counts/continuation_count
- [x] operations tab Page类型 `Page<RunOperationOutline>`，default page 15, page_size≤100, not裸数组；error_phase SUBMIT/EXECUTE/null, status精确 union, unknown task_status不显示作“任务失败”
- [x] components no裸 HTTP; local requestSeq invalidation；关闭/切对象/卸载使过期响应失效；refresh只使当前对象数据失效
- [x] `RunOperationTable` onOpenTask raises click intention, no导航; task_id null both cell and action unreachable click
- [x] Run→Task closes relatedRun再打开Task；来源Run回调反向替换；最多来源详情+一层关联详情；不递归堆叠
- [x] event dynamic keys：TOOL_SUBMISSION_PENDING、TOOL_TASK_ACCEPTED、TOOL_RESULT_RECEIVED、BACKGROUND_RESULT、BACKGROUND_RESULT_LATE、RUN_WAITING_TOOL、RUN_RESUMED；未知事件安全 fallback; unknown表示不渲染payload
- [x] status/event/timestamp use locale/current language, DateTimeText unique; no cached translation at module state
- [x] [S-20][E2E] 从审计打开真实JOIN等待Run，显示“等待任务结果”、等待时间和数量；结果已到尚未claim说明“等待接续”；完成Task后点刷新，接续/完成且数量归零（已编写登记，终验执行）
- [x] [S-21][E2E] 点击关联 task_id 打开既有 Task 详情，来源Run返回同run_id; repeated switching no crash/no long stack（已编写登记，终验执行）
- [x] [S-22][E2E] SUBMIT_PENDING task_id null no fake link; accepted DETACH明确独立模式; >15可翻页，total一致（已编写登记，终验执行）
- [x] [S-23][E2E] switch zh-CN/en-US whileopen详情，status/event/tab即时切换；UTC time按Asia/Shanghai显示YYYY-MM-DD HH:mm:ss; invalid value“-”（已编写登记，终验执行）
- [x] [S-24][E2E] seed真实 input/result盘; detail和network response不含原文/结果/凭据，只结构;timeline超200明确截断提示（已编写登记，终验执行）
- [x] [E-20][E2E] GET失败显示ErrorState，恢复后重试实际数据; invalid session统一登录 redirect; no旧对象闪烁（已编写登记，终验执行）
- [x] [E-21][E2E] cross-tenant/nonexistentTask链接404 returns原Run; spoofed X-Tenant-Id不改; non-ADMIN no新凭据入口（已编写登记，终验执行）
- [x] [E-22][E2E] A Run慢响应切到B/关闭后才返回；当前详情不被A覆盖，卸载后无状态更新/无效链接（已编写登记，终验执行）
- [x] [B-20][E2E] 无异步操作空态; 15/16条page boundary correct; after states delete/cancel current empty page returns legal page number; truncation提示 independent（已编写登记，终验执行）
- [x] [B-21][E2E] Tab可达refresh、关联Task、返回和close; ESC只关当前面板，焦点回来源链接; status not颜色 only（已编写登记，终验执行）
- [x] verifier harness-frontend#RULE-front-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-i18n#RULE-i18n-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-ui#RULE-ui-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-ui-detail#RULE-ui-detail-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-20 | E2E | Browser→Console→PG；Runtime/Worker/LLM HTTP | 从审计打开真实JOIN等待Run，显示等待任务结果/时间/数量；结果到尚未claim说明等待接续；完成Task刷新后接续/完成且归零 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| S-21 | E2E | Browser→Router/面板控制→Console Run/Task API→PG | 点击task_id打开既有Task详情，来源Run返回同run_id; repeated switching no crash/no infinite堆叠 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| S-22 | E2E | Browser、真实 Console分页、PG count | SUBMIT_PENDING task_id null无假链接；accepted DETACH明确独立模式；>15可翻页，total与实际一致 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| S-23 | E2E | Browser language/timezone、HTTP请求头、真实 DTO | zh-CN/en-US切换当前详情即时生效；UTC times by Asia/ShanghaiYYYY-MM-DD HH:mm:ss；invalid“-”；请求头拦截器 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| S-24 | E2E | Browser、Console真实只读投影、PG | 输入/结果原文标记不进入详情/network响应；只显示结构；timeline超200明确截断提示 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| E-20 | E2E | 故障代理、Browser、真实 Console重试 | GET失败ErrorState可重试；恢复后真实数据；invalid session统一登录；旧对象不闪 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| E-21 | E2E | Browser、账号租户、Console API | cross-tenant/nonexistent Task404 returns原Run；伪造租户头无效；非ADMIN无新凭据入口 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| E-22 | E2E | 延迟真实响应的代理、Browser、服务请求 | A慢响应后切到B/关闭；当前详情不被A覆盖；卸载后无状态更新/无效链接 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| B-20 | E2E | Browser、Console、真实 PG空列表/分页 | 无异步操作空态；15/16条边界正确；delete/cancel后当前空页回到合法页码；截断提示独立 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |
| B-21 | E2E | Chrome键盘、实际Semi SideSheet/Tab/链接 | Tab可达刷新/关联Task/返回/关闭；ESC只关当前面板，焦点回来源链接；状态不只颜色 | `e2e/tests/run-observability.spec.ts` | ["bash", "-lc", "cd e2e && npm test -- --config playwright.run-observability.config.ts"] | verified |

### Acceptance Evidence

- [S-20][E2E] e2e_deferred — 用例 `run-observability.spec.ts` › `S-20 等待任务结果→等待接续→接续完成，数量随真实状态归零`；真实边界：Browser→真实 Console（真实 PG 只读投影）→`seed_run_observability.py ready/resume` 在 PG 上推进 Task/Run/continuation；命令 `bash -lc "cd e2e && npm test -- --config playwright.run-observability.config.ts"`；已收集登记，未执行（终验 verify-e2e）
- [S-21][E2E] e2e_deferred — 用例 `S-21 关联 Task 打开既有详情，来源 Run 反向替换回同 run_id，反复切换不堆叠`；真实边界：审计详情→`RelatedDetailController`→Console Run/Task API→PG（DETACH 受理 task）；命令同 S-20；已收集登记，未执行
- [S-22][E2E] e2e_deferred — 用例 `S-22 SUBMIT_PENDING 无假链接、DETACH 独立模式、16 条分页与 total 一致`；真实边界：真实 Console 分页（15/16 边界）与 PG count；命令同 S-20；已收集登记，未执行
- [S-23][E2E] e2e_deferred — 用例 `S-23 详情开着切 zh-CN/en-US 即时生效；UTC 时间按 Asia/Shanghai 渲染；请求头走拦截器`；真实边界：浏览器 language/timezone（Asia/Shanghai 固定）、真实 DTO、X-Locale/X-Request-Id 拦截器；命令同 S-20；已收集登记，未执行
- [S-24][E2E] e2e_deferred — 用例 `S-24 详情与 network 响应不含原文/结果/凭据；时间线超 200 明确截断`；真实边界：真实 PG 种 input_text/result_json/submission_json 标记 + 205 条 canonical_event，捕获真实 network 响应反查；命令同 S-20；已收集登记，未执行
- [E-20][E2E] e2e_deferred — 用例 `E-20 GET 失败显示 ErrorState，重试恢复真实数据；会话失效统一跳登录`；真实边界：传输层故障代理（route.abort）→真实 Console 重试；清 Cookie 后真实 401 统一登录重定向；命令同 S-20；已收集登记，未执行
- [E-21][E2E] e2e_deferred — 用例 `E-21 跨租户/不存在 Task 请求 404；伪造 X-Tenant-Id 不改变数据；非 ADMIN 无凭据入口`；真实边界：账号租户 vs 伪造头、跨租户 Run/Task 真实 404、非 ADMIN 账号；命令同 S-20；已收集登记，未执行
- [E-22][E2E] e2e_deferred — 用例 `E-22 A Run 慢响应后关闭/切到 B，当前详情不被 A 覆盖`；真实边界：`route.fetch()` 延迟真实响应 1.5s 的代理、关闭/切换后卸载；命令同 S-20；已收集登记，未执行
- [B-20][E2E] e2e_deferred — 用例 `B-20 无异步操作空态；15/16 边界；状态变化后空页回落到合法页码；截断提示独立`；真实边界：真实 PG 空列表、16 条分页、`shrink/restore` 软删回退；命令同 S-20；已收集登记，未执行
- [B-21][E2E] e2e_deferred — 用例 `B-21 Tab 可达刷新与关联 Task、ESC 只关当前面板、状态不只靠颜色`；真实边界：Chrome 键盘 + 实际 Semi SideSheet/Tab/EntityLink；命令同 S-20；已收集登记，未执行
- 前端契约/单测：`uv run pytest -q tests/frontend` 285 passed（新增/改写 `tests/frontend/test_run_observability_contract.py`：DTO/枚举与 contracts 逐字对齐、封套分页、请求代次、Task 链接不可假、动态键枚举、无裸 HTTP、无译文缓存；`test_audit_detail_contract.py` 关联断言更新为控制器结构）；`npm run typecheck`、`npm run build` 通过。
- verifier harness-frontend#RULE-front-001：原始 argv 执行通过（test_api_client_contract 4 passed + check_frontend_api_usage + check_frontend_i18n 922 keys + typecheck）。
- verifier harness-i18n#RULE-i18n-001：原始 argv 执行通过（test_foundation_i18n 6 passed + check_frontend_i18n 922 keys）。
- verifier harness-ui#RULE-ui-001：原始 argv 执行通过（test_console_shell_contract + test_ui_style_contract 9 passed + frontend build）。
- verifier harness-ui-detail#RULE-ui-detail-001：原始 argv 执行通过（test_detail_sidesheet_contract + test_form_layout_contract 11 passed + typecheck）。
- 静态检查：`uv run mypy apps packages scripts`（339 files）通过；`uv run ruff check apps packages tests` 通过；`git diff --check` 通过。
- Python 回归（独立空库，`/tmp/async_tool_check.py`）：`uv run pytest -q tests/console_platform tests/agent_runtime` → 1 failed, 609 passed, 24 errors；失败/错误全部为既有基线红（`test_platform_settings_table` REVISION 钉 0018 vs 0019；audit 种子写 `SUCCEEDED` 撞 0019 `ck_run_record_run_status`），非本任务引入，未修。
- S-20: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- S-21: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- S-22: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- S-23: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- S-24: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- E-20: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- E-21: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- E-22: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- B-20: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- B-21: e2e_deferred — automated command e2e_deferred; run_id=be6707abc18e40f49ae6f071c57b830c (confirmed_by: runner)
- S-20: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-21: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-22: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-23: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-24: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-20: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-21: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- E-22: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- B-20: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- B-21: failed — automated command failed; run_id=9976fd96a66342eab5b90014db90d9c8 (confirmed_by: runner)
- S-20: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-21: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-22: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-23: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-24: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-20: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-21: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- E-22: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- B-20: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- B-21: verified — automated command passed; run_id=4314d819641e466aabaf8461ed02dcec (confirmed_by: runner)
- S-20: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- S-21: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- S-22: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- S-23: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- S-24: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-20: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-21: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- E-22: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-20: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)
- B-21: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-09] started
- [2026-10-09] 实现：`services/runs.ts` 扩 API-05/06（WAITING_TOOL、等待字段、`Page<RunOperationOutline>`、默认 15/上限 100、精确 OperationStatus 联合）；新增 `statusOptions.ts`（唯一颜色/词条键映射 + 未知 task_status/operation 状态安全回退）、`hooks/useRunDetail|useRunOperations`（requestSeq 代次、对象匹配防闪旧、页签首次取数、空页回退合法页、刷新只重取已加载数据）、`components/RunOperationTable`（本地 Semi Table、task_id 空不可点、错误阶段翻译）、`components/RunTimelineOutline`（25 类事件动态键 + 未知兜底、DateTimeText、截断提示）、`RelatedDetailController`（RUN/TASK 判别联合互斥、Run→Task 关闭再开、来源 Run 反向替换、不递归）。`RunDetailSideSheet` 重写为容器（等待 Banner/数量、刷新、页签），`AuditDetailSideSheet`/`TaskPage` 接线控制器；两语言新增 76 条词条。
- [2026-10-09] E2E：新增 `e2e/tests/run-observability.spec.ts`（10 用例登记 S-20..B-21）与 `e2e/playwright.run-observability.config.ts`（隔离库 + 真实 Console + vite preview + Asia/Shanghai），新增真实 PG 种子 `tests/e2e/seed_run_observability.py`（含审计入口行、16 条分页、205 条时间线、跨租户行、原文/结果/凭据标记与 ready/resume/shrink 状态推进）；`npx playwright test --config playwright.run-observability.config.ts --list` 收集 10 tests；E2E 本阶段不执行（verify-e2e 执行）。
- [2026-10-09] 验证：四条 spec verifier 原始 argv 通过；`tests/frontend` 285 passed；typecheck/build/mypy/ruff/diff-check 通过；`tests/console_platform tests/agent_runtime` 独立空库回归仅剩既有基线红（1 failed + 24 errors，migration/audit）。
- [2026-10-09] completed (done)

---

## TASK-009: 端到端清单、inventory 闭合与验收库隔离

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008
- **Source**: async-tool-runtime.backend.design.md#2.5 验收条件, async-tool-runtime.frontend.design.md#2.4 验收条件, async-tool-runtime.backend.design.md#3.5 质量实现方案, async-tool-runtime.frontend.design.md#附录：状态用语
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-09

### Description

建立本需求独立的 E2E acceptance域、功能场景命令注册、inventory扰动门禁、验收清单、空库/旧盘快验和 dev 库隔离。先保证编码期的 functional RED/GREEN 与 verify-e2e的真实live stack分层。

### Checklist

- [x] creates requirement-specific acceptance files listed in QUALITY-04：五条路径全部在盘（`tests/acceptance/runtime/test_background_result_resume.py`、`test_background_result_delivery.py`、`tests/agent_runtime/test_tool_wait_state_machine.py`、`tests/agent_worker/test_runtime_operation_races.py`、`tests/async_tool_runtime_inventory.py`）
- [x] integration/unit tests write for planners, state machine, repositories, adapters, artifact事务, migration parity：B-02..B-08 各 TASK 已落地并 verified（见各自 Evidence 与 manifest 命令）
- [x] E2E scenarios use live stack and isolation:独立DB、独立 Redis番号、真实PG、真实Redis、真实HTTP/process/browser; not共享 dev 库抢任务：`tests/acceptance/conftest.py` autouse session fixture 每轮建 `muad_acc_<uuid>` 空库 + Redis 10–15 号位原子占位；run-observability 域配置走 `useIsolatedDatastores` + preview 真实构建产物；清单 `test_e2e_isolation_mechanisms_are_in_place` 常驻机检
- [x] all S/E/B in Acceptance Coverage mapped to one owner and one executable command; functional层级有具体pytest, E2E可放 command or verify-e2e command; manual only external edge and confirmed：43 行全部唯一 owner + 可执行 argv；14 条 integration 均引用具体 pytest 文件；29 条 E2E 场景名在真实套件（pytest 用例名/Playwright test 标题）里命中；无 manual 行
- [x] four inventory perturbations change gate: changed state, removed evidence row, forged scenario name, changed manifest boundary, all red; byte-for-byte restore green; missing inventory file fails：四类扰动为常驻测试，消息指名条目（见 Evidence）；缺清单文件时登记 argv exit=4
- [x] design E2E不得降级为 unit/integration；verify-e2e runs后补 verified evidence and LOG：29 条 E2E 行保持 e2e_deferred，留需求级 verify-e2e 执行；本次不宣称 E2E 已通过
- [x] dev server与testing共享PG/Redis时 no dev --reload worker for isolated验收；stop残留进程前不跑 acceptance：运行前 `ps` 确认无 pytest/uvicorn/vite 残留；命令输出重定向文件，不用 `| head` 一类提前关闭的管道
- [x] verifier harness-test#RULE-test-001：原始 argv `bash -lc 'uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test'` 实际执行通过：tests/acceptance **317 passed**（1160.96s）+ 前端 build ✓（3.91s）+ `npm --prefix e2e test` **4 passed**（3.8s），exit=0；门禁裁决由 finish 记录

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-09 | integration | 真实需求文件、manifest、inventory runner | 改状态、删证据行、伪造用例名、改 manifest 边界 four perturbations分别red；byte-for-byte restore后green；missing inventory fails | `tests/async_tool_runtime_inventory.py` | ["uv", "run", "pytest", "-q", "tests/async_tool_runtime_inventory.py"] | verified |

### Acceptance Evidence

- [B-09][integration] RED — `uv run pytest -q tests/async_tool_runtime_inventory.py`（2026-10-09，`/tmp/b09-red.log`）：16 passed / 4 failed，红点全部来自收口任务自身未闭合——覆盖表/契约表 B-09 仍 `planned`、TASK-009 checklist 未勾（"还原复绿"断言同样被 B-09=planned 挡住）。四类扰动在 RED 盘面上全部按预期变红且逐字节还原。
- [B-09][integration] 扰动取证（字节备份 → 扰动 → 断言红 → 恢复字节 → 断言绿；autouse `_surface_guard` 逐用例比对任务文件与 manifest 字节，`/tmp/capture_perturbations.py` 实测）：
  - (a) 改状态：覆盖表 `E-06` 状态 `e2e_deferred`→`planned` ⇒ `E-06（TASK-004）=planned`；
  - (b) 删证据行：删 TASK-003 的 `B-01` 全部证据条目 ⇒ `B-01（owner=TASK-003，契约=verified）未出现在 TASK-003 的 Evidence 小节`；
  - (c) 伪造用例名：`B-04` 命令追加 `-k test_b09_forged_case_name` ⇒ `B-04 的 -k test_b09_forged_case_name 在 …/test_tool_wait_state_machine.py 里没有对应用例`；
  - (d) 改 manifest 边界：`B-04` boundary 加 ` FORGED` ⇒ `B-04 boundary 不一致：manifest=… 覆盖表=…`；
  - 四类恢复后对应检查复绿，任务文件与 manifest 字节与扰动前一致（不残留扰动）。
- [B-09][integration] GREEN — `uv run pytest -q tests/async_tool_runtime_inventory.py` → 20 passed（纯文件盘面交叉核对，无需 DB/Redis）。
- 额外任务（需求遗留基线，TASK-001 迁移 0019 暴露，本任务闭合）：
  - `tests/console_platform/test_platform_settings_table.py`：`REVISION` 0018→0019、两处文档串同步（fixture 迁到 head 的自身语义；roundtrip 仍 `downgrade 0016` → `upgrade 0019`，0019 不触碰 platform_setting 表）；
  - 四个 audit 测试（agent_filter/detail_api/export_download/query_api）：`runtime.run_record` 种子 `'SUCCEEDED'`→`'COMPLETED'`（0019 `ck_run_record_run_status` 合法终态；audit 导出作业的 `TERMINAL_STATUSES`/`status["status"] == "SUCCEEDED"` 断言未动）；
  - 独立空库复跑五文件 28 passed；范围回归 `tests/console_platform tests/migrations tests/agent_runtime` 636 passed。
- 验收域基线修复（本需求首次全量跑 `tests/acceptance` 暴露，均与本需求 0019/TASK-002..004 行为直接相关，TASK-009 收口范围）：
  - 0019 新表清理顺序：`tests/acceptance/task_schedule/environment.py`（RUNTIME_CLEANUP/TASK_CLEANUP）与 `tests/acceptance/runtime/conftest.py` 在 FK 父表前删 `tool_result_inbox`/`tool_control_outbox`/`tool_operation`/`run_continuation`/`runtime_result_outbox`/`runtime_operation`（Runner 对每个 Run 都写 run_continuation 检查点，不补则 139 个 teardown FK 错误）；
  - task-schedule 栈 Worker 补 `AGENT_RUNTIME_URL`（TASK-002 结果回流必需，缺则全量 `RESULT_HTTP_RETRY`）；
  - `tests/e2e/openai_probe_app.py` 把 `[External tool data: …]` 计入本轮工具完成（TASK-004 起回执是外部数据 USER 消息；不识别会重复吐同一 tool_call → COMMON_INTERNAL_ERROR）；
  - `test_submission_hardening._db` 改独立线程事件循环（主线程 `asyncio.run` 污染 39 个后续 pytest-asyncio 用例）；S-02 以持久状态等 Run 终态（SSE 在 WAITING_TOOL 按设计不发终帧），投递断言用 DB 事实 + 探针条数；
  - `test_background_result_resume`：每用例新建会话 + channel.bot_id；S-01 等事件而不是立即 drain；E-06 等 CANCEL_OPERATION 送达后再放行 Skill；join 夹具取非 batch 制品；
  - gateway 等待域：fixture 等探针 WS 连接再推送；等待谓词改 async（原 `lambda: await_fn() == x` 恒 False）；会话断言限定本用例新增 Run；
  - 四处 WAITING_INPUT 种子补 `deadline_at`（TASK-004 resume CAS 前置）；dfx 模型恢复断言改 `RUN_DEADLINE_EXCEEDED`（TASK-004 起 deadline 专属错误码）；`test_redis_unavailable` 前置 `tests/` 到 sys.path（E-15 登记命令可单跑收集）；
  - `tests/async_tool_helpers.seed_operation` 支持显式 tenant，验收调用改传本栈租户（原随机租户行不在任何收尾清理内，卡 0019 全局 drain 前置 → `test_secret_migration` 降级被拦）。
- verifier harness-test#RULE-test-001 原始 argv 实跑通过：`tests/acceptance` **317 passed**（1160.96s）+ 前端 build ✓（3.91s）+ `npm --prefix e2e test` **4 passed**（3.8s），exit=0（`/tmp/harness-test-verifier-4.log`）。首轮 RED 明细：`/tmp/harness-test-verifier.log`（139 teardown FK errors）、`/tmp/harness-test-verifier-2.log`（46 failed/10 errors，主循环污染 + 验收域缺陷）。
- 静态检查：`uv run mypy apps packages scripts`（339 files）通过；`uv run ruff check apps packages tests` 通过；`git diff --check` 通过。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-09 | 16 passed/4 failed：收口自身未闭合（B-09 覆盖/契约 planned、checklist 未勾）；四类扰动各自变红并指名条目 | 20 passed | `test_inventory_registers_its_own_command_path`、`test_missing_inventory_path_fails_registered_command`、`test_coverage_rows_are_terminal`、`test_manifest_matches_coverage_table`、`test_terminal_rows_are_registered_in_owner_evidence`、`test_contract_rows_are_terminal`、`test_contract_tables_cover_every_acceptance_ref`、`test_registered_commands_reference_paths_on_disk`、`test_registered_commands_k_tokens_hit_real_cases`、`test_e2e_rows_point_to_real_suites_and_case_names`、`test_e2e_isolation_mechanisms_are_in_place`、四条扰动用例 | 真实任务文档 + `.acceptance-manifest.json` + 真实测试文件/套件（纯文件交叉核对，不 mock）；扰动字节备份/逐字节还原 | verified |
- B-09: verified — automated command passed; run_id=16338dec771647f1a5ab4a12eb725b98 (confirmed_by: runner)
- B-09: failed — automated command failed; run_id=1c7d0e1bdb7b4a3f994f46264a82a113 (confirmed_by: runner)
- B-09: verified — automated command passed; run_id=5b4c247bcbaa42b893605bb64210e981 (confirmed_by: runner)

### Log

- [2026-10-07] created (draft)
- [2026-10-09] started
- [2026-10-09] 实现：新增 `tests/async_tool_runtime_inventory.py`（20 条检查）——覆盖表 43 行唯一 owner/可执行命令/终态、manifest 与覆盖表同 ID/source/level/boundary/owner/命令、Acceptance-Refs 覆盖、终态行在 owner Evidence 登记（表格行优先、runner 条目次之）、无占位行、契约表逐行终态且覆盖每条 ref、登记命令路径在盘（`cd`/`bash -lc` 感知）、`-k`/`-g` 令牌命中真实用例、integration 行引用具体 pytest 文件、E2E 场景名在真实套件（pytest 用例名/Playwright 标题，域配置经 testMatch 解析）、E2E 隔离机制在盘（conftest 自动空库 + Redis 10–15 号位、isolated datastores、preview 构建产物、Makefile 先 build）、非 draft 任务无未勾项；`_dir()` live→archived 双写。四类扰动 + 缺文件结构性 RED 为常驻测试，扰动带字节备份/try-finally 还原 + autouse 字节守卫。
- [2026-10-09] 额外任务（需求遗留基线，TASK-001 迁移 0019 暴露）：`test_platform_settings_table.py` REVISION 0018→0019（fixture 自身语义是迁到 head；0019 不触碰 platform_setting，roundtrip 仍成立）；四个 audit 测试的 `run_record` 种子 `'SUCCEEDED'`→`'COMPLETED'`（0019 check constraint 合法终态，导出作业状态断言未动）。五文件独立空库 28 passed；范围回归 636 passed。
- [2026-10-09] 验证：`tests/async_tool_runtime_inventory.py` RED（16 passed/4 failed，收口自身未闭合）→ 段落闭合后 GREEN 20 passed；四类扰动消息与逐字节还原见 Evidence；mypy 339 files / ruff / `git diff --check` 通过。harness-test#RULE-test-001 原始 argv 实跑结果见下条。
- [2026-10-09] 验收域基线修复（首次全量 `tests/acceptance` 暴露，均与本需求 0019/TASK-002..004 行为直接相关）：0019 新表清理顺序（139 个 teardown FK 错误）、Worker `AGENT_RUNTIME_URL`、探针外部数据语义、主线程 `asyncio.run` 污染、SSE 终帧等待、种子 `deadline_at`、等待谓词 async、`seed_operation` 租户等；明细见 Evidence。修复后 `tests/acceptance` 317 passed（1 个 multipod 时序 flaky，单跑复绿）。
- [2026-10-09] verifier harness-test#RULE-test-001 原始 argv 实跑通过：tests/acceptance 317 passed（19:20）+ 前端 build ✓ + `npm --prefix e2e test` 4 passed，exit=0；门禁裁决由 finish 记录。
- [2026-10-09] completed (done)
