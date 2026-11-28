---
{"version":1,"requirement":"tool-error-evidence"}
---
# 工具错误事实完整修复

代码核对基于本地 main 的 4dd92b29 及当时未提交的工作树。性能对照以 TASK-001 冻结的工作树为准。全部 Spec Delta 为空，本次落实已有规则。新增测试文件由对应 TASK 创建；测试路径属于该 TASK 的修改范围。

## TASK-001: 冻结性能基线与对照样本

- **Status**: done
- **Depends**: 
- **Source**: tool-error-evidence.backend.design.md#性能验收与验证
- **Scenarios**: S-01, E-01, B-02
- **Spec-Refs**: test/harness-test
- **Paths**: tests/agent_runtime/test_tool_error_performance.py
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
      "tests/agent_runtime/test_tool_error_performance.py"
    ],
    "timeout": 900
  }
]
```

### Description

在任何产品改动前，保存可复原的工作树快照及指纹、构建和迁移版本。基线包含未提交实现。

### Checklist

- [x] 固定隔离库、输入、并发、模型响应及预热，覆盖成功、拒绝、门禁恢复、长 Run 收尾与详情/导出；记录各场景负载规模。
- [x] 新增小型测试验证样本可重复和计数采集；完整测量另行在隔离环境执行。保存原始样本、请求/SQL 次数和查询计划，记录产物位置。

- [x] 先固定工作树与依赖锁文件，再引入测量夹具；同一份夹具分别驱动基线与候选，确认恢复后的基线可以复跑。
- [x] 分别计数 Worker 请求、模型回合、SQL 往返、审计写入；测长 Run 时固定审计行数与失败比例。对照样本带相同 workload ID、随机种子和执行顺序。
- [x] 交付可复原源码标识、运行命令、环境参数、逐次样本和一次基线对基线的噪声报告。在隔离验收库执行，保留当前开发库及工作树。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- [2026-11-27T16:39:06.937Z] started tests=fef5ecf7fb89

### Verification

- 基线 3fa98353，源码归档已逐文件核验。原始样本、运行日志、SQL 计划及 noise.txt 保存在 .git/tool-error-evidence/baseline-3fa98353。两轮各 64 次/场景，串行、预热一次，长 Run 为 256 行、1/4 失败。命令使用 TOOL_ERROR_SAMPLES、TOOL_ERROR_ITERATIONS=64 执行声明测试。
- 2026-11-27T16:40:52.269Z PASS commands=1/1 executed=1 deduped=0 elapsed=5447ms tests=fef5ecf7fb89 head=3fa9835389a7 dirty=1
  - regression PASS exit=0
## TASK-002: 透传安全事实并统一双语提示

- **Status**: done
- **Depends**: TASK-001
- **Source**: tool-error-evidence.backend.design.md#所有权与数据流
- **Scenarios**: S-01, S-02, E-02, B-01, B-02
- **Spec-Refs**: api/harness-api, i18n/harness-i18n, logging/harness-log
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/task_client.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/worker_errors.py, packages/agent-core/src/muad_agent_core/tools/**, packages/agent-core/src/muad_agent_core/failures.py, config/api-messages.yaml, tests/agent_core/test_tool_contract.py, tests/agent_core/test_failure_hints.py, tests/agent_runtime/test_schedule_authoring_compiler.py, tests/agent_runtime/test_worker_error_contract.py
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
      "tests/agent_core/test_tool_contract.py",
      "tests/agent_core/test_failure_hints.py",
      "tests/agent_runtime/test_schedule_authoring_compiler.py",
      "tests/agent_runtime/test_worker_error_contract.py"
    ],
    "timeout": 900
  }
]
```

### Description

复用 Worker 封套和 ToolError，补齐 HTTP 边界、工具消息和 outcome 的同源证据。

### Checklist

- [x] 按错误契约约束公开键、值和总大小；验证消息来源。覆盖畸形正文、超时、未知码和敏感标记；成功路径不构造诊断。
- [x] 从 HTTP 测试 transport 走实际注册工具到模型输入；断言 missing/invalid、repair、owner、显式 false 和未知。消除“有 data 即可重试”的误判；CI 枚举已知工具码和双语提示。

- [x] 在 WorkerTaskClient._request 保留已验证消息与公开 detail；跟随 BackgroundTaskToolSet._draft → _submit_draft → _wrap，覆盖远端拒绝与本地 TaskToolError 两条入口。
- [x] 对同一个 code 分别输入 CONTRACT_MISSING、CONTRACT_INVALID、无 detail；前两项在 role=tool 中保留各自 reason 和 REIMPORT_SKILL，第三项不得补造 repair。
- [x] 在允许键的值和 msg 中注入唯一测试标记，逐一检查 tool content、outcome、日志。断开 Worker 连接时，可能已发送的写操作不得误报确认失败。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- [2026-11-27T16:41:00.116Z] started tests=1a75482cd004
- 2026-11-28 完成 Worker 错误边界、ToolError 同源证据、失败提示与回归测试。

### Verification

- Worker 边界新增 `WorkerResponseError`：只保留已登记码或符合诊断标识规则的安全码；消息仅采用对应 locale 的目录词条，否则标记 `CATALOG`；detail 按码白名单校验并限制条目数/长度；未知结果一律 `outcome_known=false`。`TaskToolError` 承接 `retryable`/`sent`/`outcome_known`/`diagnostic`，本地错误只补充由 `failure_hint` 推出的 owner。
- `ToolError` 序列化显式 `retryable=false`，未知结果不输出允许重试；`ExecutionEvidence.diagnostic` 与 role=tool 正文来自同一份事实。`tool_exception_outcome` 对未知结果强制不可重试。
- `failure_hint` 增加 `SCHEDULE_*`、`COMMON_*` 与 `TOOL_RETRY_UNSAFE` 的双语规则和 USER/ADMIN owner；仅有错误码时不推断 missing/invalid，也不补造 repair；CI 参数化覆盖全部工具可达码。
- `uv run pytest -q tests/agent_core/test_tool_contract.py tests/agent_core/test_failure_hints.py tests/agent_runtime/test_schedule_authoring_compiler.py tests/agent_runtime/test_worker_error_contract.py`：96 passed。
- 相关回归：`tests/agent_runtime/test_scheduled_v2_intent_tools.py tests/agent_runtime/test_schedule_promotion.py` 25 passed；`tests/agent_runtime/test_finalization_render.py tests/agent_runtime/test_finalization_facts.py tests/agent_worker/test_scheduled_v2_drafts.py` 60 passed；`tests/agent_core tests/agent_runtime/test_schedule_authoring_compiler.py tests/agent_runtime/test_task_handoff.py` 557 passed。`ruff check` 与 `mypy` 对改动文件通过。
- 性能对照与整链验收留给 TASK-007；本 TASK 未声明性能结论。
- 2026-11-27T17:23:48.380Z PASS commands=1/1 executed=1 deduped=0 elapsed=3989ms tests=2b535a726649 head=3fa9835389a7 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-003: 记录门禁来源与冻结终止触发调用

- **Status**: done
- **Depends**: TASK-002
- **Source**: tool-error-evidence.backend.design.md#调用归因和持久化
- **Scenarios**: E-01, E-02
- **Spec-Refs**: arch/harness-arch, snapshot/harness-snapshot
- **Paths**: packages/agent-core/src/muad_agent_core/agent/**, packages/agent-core/src/muad_agent_core/tools/**, packages/contracts/src/muad_contracts/tool_events.py, apps/agent-runtime/src/muad_agent_runtime/application/execution_checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, tests/agent_core/test_failure_state.py, tests/agent_core/test_recovery_states.py, tests/agent_core/test_tool_events.py, tests/agent_core/test_runner.py
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
      "tests/agent_core/test_recovery_states.py",
      "tests/agent_core/test_tool_events.py",
      "tests/agent_core/test_runner.py"
    ],
    "timeout": 900
  }
]
```

### Description

复用现有 attempt、write_operation_key、目标冲突判定与 FailureState。

### Checklist

- [x] 区分已禁止原样调用和待核对写入；冻结触发 ID 及唯一可信来源，恢复或清除时同步更新；保留安全核对入口。
- [x] 测试并行同名异目标、多个候选、状态已清除、跨 Run 和 checkpoint 恢复；核对派发次数与原门禁语义。

- [x] 从 runner._dispatch_gate 的两个实际条件分别取样：同 attempt 已禁止、目标上存在待核对写入。两者均拒绝派发，保留各自依据。
- [x] 测试调用 A 失败、同操作 B 受保护、无关 C 更晚完成；交错顺序与 checkpoint 重启后均应还原 A/B。另测 A 已解除、相同工具有两个候选来源时不选择 A。
- [x] 对比事件、检查点和恢复后的来源身份；已有 WAIT、STOP、只读核对及合法幂等重试的派发结果保持一致。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- [2026-11-27T17:24:01.649Z] started tests=309e82a50a47
- 2026-11-28 完成门禁归因、no-progress 触发冻结与恢复来源。

### Verification

- `FailureState` 新增 `blocked_attempt_sources`、`unknown_target_sources`、`unknown_operation_sources` 与 no-progress 触发/来源字段；同一 attempt/目标/operation 保留首个未解决来源，operation 成功清理时同步删除来源，payload 读取丢弃无对应状态的孤儿来源。
- `_dispatch_gate` 区分 `BLOCKED_ATTEMPT`、`UNVERIFIED_WRITE` 与两者同时命中的 `RETRY_GUARDED`；唯一来源写入 tool detail 与 outcome diagnostic，多候选/无证据时不选择来源。`ToolEventOutcome` 增加 `gate_reason`/`source_tool_call_id`，executor 只写出枚举内值。
- 恢复链路：`replay_failure_state` 从 completed outcome 读取来源；checkpoint 跨 Run 继承未知写入时记录声明调用 ID；`FailureState.as_payload/from_payload` 保留并可核对。
- 声明测试 `uv run pytest -q tests/agent_core/test_failure_state.py tests/agent_core/test_recovery_states.py tests/agent_core/test_tool_events.py tests/agent_core/test_runner.py`：95 passed。
- 相关回归：`tests/agent_core` 509 passed；`tests/agent_runtime/test_runner_executor.py tests/agent_runtime/test_interrupt_checkpoint.py tests/agent_runtime/test_tool_result_history.py tests/agent_runtime/test_tool_audit_business.py tests/agent_runtime/test_runtime_audit_write.py` 23 passed。`ruff check` 通过。
- 2026-11-27T18:12:06.134Z PASS commands=1/1 executed=1 deduped=0 elapsed=3814ms tests=309e82a50a47 head=3fa9835389a7 dirty=1
  - regression PASS exit=0
## TASK-004: 落库失败证据并重建收尾事实

- **Status**: done
- **Depends**: TASK-003
- **Source**: tool-error-evidence.backend.design.md#调用归因和持久化
- **Scenarios**: E-01, S-02, B-02
- **Spec-Refs**: persistence/harness-data, snapshot/harness-snapshot, arch/harness-arch
- **Paths**: migrations/versions/**, apps/agent-runtime/src/muad_agent_runtime/infrastructure/**, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/finalization/facts.py, tests/agent_runtime/test_tool_audit_business.py, tests/agent_runtime/test_runtime_audit_write.py, tests/agent_runtime/test_finalization_facts.py, tests/agent_runtime/test_finalization_funnel.py, tests/agent_runtime/test_tool_audit_control_projection.py, tests/migrations/test_tool_audit_failure_control_migration.py
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
      "tests/agent_runtime/test_tool_audit_business.py",
      "tests/agent_runtime/test_runtime_audit_write.py",
      "tests/agent_runtime/test_finalization_facts.py",
      "tests/agent_runtime/test_finalization_funnel.py",
      "tests/agent_runtime/test_tool_audit_control_projection.py"
    ],
    "timeout": 900
  }
]
```

### Description

一次审计写入保存可空三项控制量与有界诊断；复用安全摘要。

### Checklist

- [x] 在现有迁移头上增加可空列；检查迁移前记录、成功记录、实时与重放，默认控制值不能覆盖未知；不回填或双写。
- [x] 在原 tenant/run 查询中合并取列，用触发及来源 ID 关联，去掉最后一条失败及同工具同码猜测。验证查询失败、缺失来源和重放幂等。

- [x] 沿 executor 完成事件 → RuntimeAuditWriter.record_tool_call → _insert_tool_call → ORM → _finalization_facts 验证三项控制量及公开证据，覆盖即时与恢复写入。
- [x] 在隔离 PostgreSQL 的前一迁移版本插入旧行再升级；旧行 NULL、新行 false 都可区分，重复消费同一完成事件的审计结果与原有幂等契约一致。
- [x] 将触发 ID、来源 ID、业务失败及真实 TerminationReason 分别断言；混入另一租户和另一 Run 的记录仍只返回当前 Run 有权读取的事实。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- [2026-11-27T18:12:11.072Z] started tests=b9b675ab4f6c
- 2026-11-28 完成失败控制落库、收尾按冻结来源归因与迁移验证。

### Verification

- 迁移 0063 在 `runtime.tool_call_audit` 增加可空 `retryable`、`retry_disposition`、`agent_disposition`、`diagnostic_json`；无默认值、无回填。隔离 PostgreSQL 从 0062 插入旧行后升级：旧行四列为 NULL，新行 `false` 与 `"BLOCK"`/`"STOP"`/诊断可区分。
- executor 从完成事件投影 `retryable`（未知结果不写许可）、两项 disposition 与有界公开诊断；RuntimeAuditWriter 再次脱敏并在超限时写 `{"truncated": true}`，同一 `(run_id, tool_call_id)` 重放保留首次行。
- `_finalization_facts` 一次查询合并取列；按冻结的 trigger/source ID 选择 decisive，不再用最后一条失败或同工具同码覆盖。审计读取失败标记 `evidence_incomplete` 并使用同一冻结来源；历史 NULL 保持未知。混入同租户其他 Run 的审计行不进入当前 Run 统计。
- 声明测试 `uv run pytest -q tests/agent_runtime/test_tool_audit_business.py tests/agent_runtime/test_runtime_audit_write.py tests/agent_runtime/test_finalization_facts.py tests/agent_runtime/test_finalization_funnel.py tests/agent_runtime/test_tool_audit_control_projection.py`：35 passed。
- 迁移用例单独执行 `uv run pytest -q tests/migrations/test_tool_audit_failure_control_migration.py`：2 passed（该目录 `own_datastore` 标记会覆盖同轮其他套件的隔离库，故不并入声明命令）。
- `ruff check` 通过。
- 2026-11-27T18:37:39.687Z PASS commands=1/1 executed=1 deduped=0 elapsed=14854ms tests=aa53446446de head=3fa9835389a7 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-005: 以已确认事实交付用户可见回复

- **Status**: done
- **Depends**: TASK-004
- **Source**: tool-error-evidence.backend.design.md#提示模型和用户文案
- **Scenarios**: S-01, E-01, E-02, B-01
- **Spec-Refs**: arch/harness-arch, i18n/harness-i18n, api/harness-api
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/finalization/**, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, packages/agent-core/src/muad_agent_core/agent/**, packages/contracts/src/muad_contracts/tool_events.py, tests/agent_runtime/test_finalization_render.py, tests/agent_runtime/test_finalization_funnel.py, tests/agent_runtime/test_finalization_facts.py, tests/agent_core/test_runner.py, tests/agent_core/test_tool_events.py
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
      "tests/agent_runtime/test_finalization_render.py",
      "tests/agent_runtime/test_finalization_funnel.py",
      "tests/agent_runtime/test_finalization_facts.py",
      "tests/agent_core/test_runner.py",
      "tests/agent_core/test_tool_events.py"
    ],
    "timeout": 900
  }
]
```

### Description

归因、停止原因、用户下一步分别渲染；统一普通、硬停止、维护和流式分支。

### Checklist

- [x] 在首个相关 delta 或替代派发前约束只有码的未解决诊断；用可控模型提出错误脚本解释及替代执行，断言最终正文、事件与实际调用。
- [x] 验证两种语言、真实等待和停止状态、用户追问、写入结果未知、管理员重新导入；记录承诺和影响声明必须有依据。

- [x] 从 test_finalization_funnel 的持久化消息用例扩展，覆盖 executor 提供 failure_reply、服务端 render、已有 partial 回复和维护收尾。
- [x] 针对事实不足样本，捕获首个诊断 delta、完整事件串、数据库助手正文及真实派发清单：均无脚本猜测或 AGENT_TURN 绕行；保留先前已发送的进度。
- [x] 针对确认预检失败与写后超时分别验收影响范围；只有前者且其他相关写入已排除才可声称“已有任务不受影响”。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- [2026-11-27T18:37:45.550Z] started tests=7f6a7bfe3732
- 2026-11-28 完成按冻结事实收尾、只有码失败保护与影响范围文案。

### Verification

- `render_finalization` 的 NO_PROGRESS 只使用 decisive failure；无 source 或来源未命中审计时说明“具体原因尚无法确认”，不再取最后一条失败。用户正文不含错误码；Console 技术详情区分终止原因、触发/来源调用、错误码/工具、原调用可重放、两项处置、owner、消息来源、field/reason/repair、Run ID 与平台返回/诊断摘要。
- 管理员修复场景（CONTRACT_MISSING/CONTRACT_INVALID + REIMPORT_SKILL）分别说明缺少/无效配置与管理员重新导入；只有 `has_unverified_write=false` 且预检 detail 已确认时才说“已有任务不受影响”。写后未知结果改为先核对，不声称已停止或未生效。
- 只有码/未知码失败不再交给模型解释：runner 的 `_explain_no_progress` 依据 `diagnostic_complete` 跳过模型回合；`run_service._finalize_failed` 对事实不足的 Run 忽略 `failure_reply`，在首个失败 delta 前选定确定性正文，保留先前 partial 进度。
- 验证覆盖中英文、预检 missing/invalid、无 detail、未核对写入、未知码、无 trigger 快照、failure_reply 覆盖、审计读取失败与 partial 消息；`tests/agent_runtime/test_finalization_render.py` 21 passed，声明套件 115 passed。
- 声明测试 `uv run pytest -q tests/agent_runtime/test_finalization_render.py tests/agent_runtime/test_finalization_funnel.py tests/agent_runtime/test_finalization_facts.py tests/agent_core/test_runner.py tests/agent_core/test_tool_events.py`：115 passed。
- 相关回归：`tests/agent_core` 510 passed；`tests/agent_runtime/test_runner_executor.py` 单独执行通过（混合目录时受既有 `own_datastore` 标记影响，见 TASK-004 记录）。`ruff check` 与 `mypy` 对改动文件通过。
- 2026-11-28T01:24:44.452Z PASS commands=1/1 executed=1 deduped=0 elapsed=7634ms tests=66b7f285ad01 head=3fa9835389a7 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-006: 贯通运行详情、导出和 WebChat 展示

- **Status**: done
- **Depends**: TASK-004
- **Source**: tool-error-evidence.frontend.design.md#console-运行详情与导出
- **Scenarios**: S-01, S-02, E-01, B-02
- **Spec-Refs**: frontend/harness-frontend, i18n/harness-i18n, auth/harness-auth, logging/harness-log
- **Paths**: apps/console-platform/backend/src/muad_console_platform/**, apps/console-platform/frontend/src/modules/run-observability/**, apps/console-platform/frontend/src/modules/webchat/**, apps/console-platform/frontend/src/locales/**, tests/console_platform/test_execution_export_business_fields.py, tests/console_platform/test_run_event_details.py, tests/frontend/test_run_observability_contract.py, tests/frontend/test_capacity_webchat_message_history_ui.py
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
      "tests/console_platform/test_execution_export_business_fields.py",
      "tests/console_platform/test_run_event_details.py",
      "tests/frontend/test_run_observability_contract.py",
      "tests/frontend/test_capacity_webchat_message_history_ui.py"
    ],
    "timeout": 900
  }
]
```

### Description

扩展现有查询与展示字段集合，复用 MessageThread 的折叠详情。

### Checklist

- [x] 核对新旧审计、来源标签、false 与未知、导出白名单及租户隔离；可键盘展开，刷新后仅有一份正文。
- [x] 验证展开无额外请求、普通消息无诊断开销；业务文案由服务端统一输出。

- [x] 沿 Run 时间线查询 → runs.ts → RunEventDetails 检查字段白名单；沿 execution_export_repository → execution_export_view 检查导出。以同一 Run ID 和调用 ID 比对两处来源与修复动作。
- [x] 中文与英文各覆盖有详情/无详情、允许/禁止/未知。WebChat 曾使用技术详情控件；TASK-007 用户复查后移除，改为只返回内置描述/模型总结，Console 保留完整事实。
- [x] 用另一租户账号分别访问详情和导出。记录可见性与脱敏结果；前端静态契约测试以外的交互按 S-02、B-02 人工验证。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- [2026-11-28T01:24:50.245Z] started tests=592518bdc1fe
- 2026-11-28 完成审计诊断字段投影、导出字段与前端 i18n 展示。

### Verification

- Run 事件详情查询的 tool lateral 增加 `retryable`/`retry_disposition`/`agent_disposition`/`diagnostic`；`RunEventDetail` 白名单新增 retry 许可、两项处置、消息来源、owner、门禁原因、触发/来源调用及 detail 的 field/fields/reason/repair/allowed_values/entry。旧行 `retryable` 显示 UNKNOWN，诊断截断显示可见标记；未列入白名单的 diagnostic 键不输出。
- 导出 view 的 `tool_calls` 与 failures/calls CSV 增加同一组字段；diagnosis 候选保留 retryable、两项 disposition 与 diagnostic，仍按原租户/Run 查询。前端 `EVENT_DETAIL_FIELDS`、`runs.ts` 与 `RunEventDetails` 同步；中英文词条覆盖新字段和枚举。
- `npm run typecheck` 通过；`tests/console_platform/test_run_event_details.py` 覆盖新字段、旧行未知、跨 Run/租户/删除审计隔离与 canary 不泄露。
- 声明测试 `uv run pytest -q tests/console_platform/test_execution_export_business_fields.py tests/console_platform/test_run_event_details.py tests/frontend/test_run_observability_contract.py tests/frontend/test_capacity_webchat_message_history_ui.py`：85 passed；TASK-007 用户复查后 WebChat 移除技术详情控件；Console 保留完整事实。
- 浏览器键盘展开/收起、刷新后单份正文与另一租户账号的可见性按 S-02、B-02 人工验收，不在本次自动化内。`ruff check` 与后端 `mypy` 通过。
- 2026-11-28T02:14:05.380Z PASS commands=1/1 executed=1 deduped=0 elapsed=36649ms tests=ce00aa673c93 head=3fa9835389a7 dirty=1 tests-changed
  - regression PASS exit=0
## TASK-007: 验证整条错误链与性能不退化

- **Status**: done
- **Depends**: TASK-005, TASK-006
- **Source**: tool-error-evidence.backend.design.md#性能验收与验证
- **Scenarios**: S-01, S-02, E-01, E-02, B-01, B-02, N-01, C-01
- **Spec-Refs**: test/harness-test, e2e/harness-e2e
- **Paths**: tests/agent_runtime/test_tool_error_performance.py, tests/agent_runtime/test_tool_error_evidence_flow.py
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
      "tests/agent_runtime/test_tool_error_evidence_flow.py",
      "tests/agent_runtime/test_tool_error_performance.py"
    ],
    "timeout": 900
  }
]
```

### Description

贯穿 HTTP 拒绝、真实工具消息、门禁、持久化、收尾和管理员输出；把共同 Run 与调用身份作为核对依据。

### Checklist

- [x] 用 TASK-001 的基线做同机交替配对；分场景报告吞吐、p50/p95/p99、CPU/Run、峰值内存、调用及数据库往返。附审计行宽、WAL、缓冲读取、输入 token 和原始样本。
- [x] 按设计的单侧置信上界逐项判定；上界不能证明不退化则增加样本或修复重测。用户接受 p50 证据与 rejected p95 噪声说明后执行 finish。人工按 scenarios 在真实构建验收，单独记录，不进 CI（见 `tool-error-evidence.browser-test-report.md`）。

- [x] 串接 TASK-002 的测试封套与可控模型、真实 Runtime 持久化路径；按同一 Run 对齐下一轮 role=tool、首个诊断 delta、终态正文、审计和导出。
- [x] 对照样本分别包括成功、同码双 reason、保护事件覆盖风险、仅有码和结果未知；记录每个样本的派发数、额外查询数和最终文案。
- [x] 正常成功（N-01）与“结构化错误→模型自行修正→成功”（C-01）均在真实 WebChat 验证：C-01 用户只给客户名 ACME，第一次 `UAT_CUSTOMER_UNKNOWN` 的错误列出 1001，模型未收到用户提示的正确值，仍自行改为 1001 后成功。
- [x] Verification 中按 workload ID 关联基线与候选的完整运行记录和配对统计；逐项注明通过、退化或证据不足。后两者不得标为性能验收通过。

### Spec Delta

```json
[]
```

### Log

- 2026-11-27 创建。
- 2026-11-28 完成整链测试；性能候选 512 次/场景，p50 在基线 A/B 范围内，p95 拒绝路径出现不稳定尾部，性能验收未签字。
- [2026-11-28T02:34:24.189Z] started tests=3a23b9edd27b
- 2026-11-28 完成 kimi-webbridge 浏览器验收：S-01/S-02/E-01/B-01/B-02 通过，E-02 部分通过；发现并修复 5 个真实问题（无效契约 owner、宿主失败仍流式输出、TOOL_FAILURE 无根因、RETRY_GUARDED 丢来源、字段列表 dict 展示），详见 `tool-error-evidence.browser-test-report.md`。
- 2026-11-28 用户复查后：WebChat 移除技术详情，只保留内置描述/模型总结；补充 N-01 正常成功 Run `479acaad-…` 与 C-01 自修复 Run `98453a74-…`（用户只给客户名 ACME，第一次 `UAT_CUSTOMER_UNKNOWN` 的错误列出 1001，模型自行改为 1001 后成功）。
- [2026-11-28T06:25:56.706Z] reopen: user review: remove WebChat technical details; add normal and correction E2E

### Verification

- 功能整链 `tests/agent_runtime/test_tool_error_evidence_flow.py` 6 passed：同一 Run/调用覆盖成功、CONTRACT_MISSING/INVALID、门禁覆盖原始业务来源、仅有码、写结果未知；工具 content、审计行、`RunService._finalization_facts`、终态正文与导出字段按同一调用 ID 对齐。
- 性能：TASK-001 基线 `samples-a/b`（各 64 次/场景）与候选 512 次/场景（`/tmp/tool-error-candidate6.jsonl`，另存 `.git/tool-error-evidence/candidate-3fa98353-dirty/`）。p50（µs，基线 A/B → 候选）：
  - success 54.42/54.08 → 47.83（通过）
  - rejected 55.33/54.46 → 54.04（通过）
  - recovery 11.77/12.06 → 12.04（通过，差异在基线 A/B 内）
  - long-run 2425.98/2510.35 → 2491.77（通过，差异在基线 A/B 内）
  - export 689.40/702.10 → 685.52（通过）
  p95 的 success/recovery/export/long-run 在基线范围或更低；rejected 的 512 次候选出现 89–113µs 尾部（基线 76.6–84.9µs），属于不稳定尾部，未签性能不降。审计查询计划 buffers 同为 8，SQL 执行时间 0.215ms vs 基线 0.166ms（+0.049ms，来自新增 4 列）；审计行宽/WAL/输入 token 未独立采样，证据不足。
- 结论：功能链路通过；性能 p50 通过，p95（rejected）与审计行宽/WAL 证据不足，按设计不签署“性能不降”，TASK-007 保持未完成，等待复测或评审接受该证据缺口。人工 scenarios 未在本轮执行。
- 2026-11-28T05:51:00.196Z PASS commands=1/1 executed=1 deduped=0 elapsed=7245ms tests=3a23b9edd27b head=3fa9835389a7 dirty=1
  - regression PASS exit=0
- 2026-11-28T06:26:03.892Z PASS commands=1/1 executed=1 deduped=0 elapsed=7084ms tests=3a23b9edd27b head=3fa9835389a7 dirty=1
  - regression PASS exit=0

### Review 修复补充（2026-11-28）

- 错误解释改为“配置优先”：`ToolError.content` 增加同源 `cause`/`next_step`/`action_owner`；`render` 不再用 API catalog 文案覆盖已配置 hint。CI 枚举全部 tool-facing code（含 `TOOL_*`、`SCHEDULE_*`、Common、Promotion）。
- 首次失败边界：`FailureState.failure_pending` 与 `hint_known/action_owner` 进入快照；未解决失败后阻止其他 effectful 替代工具（`FAILURE_UNRESOLVED`），只读核对、同操作修正和 `ask_user` 仍可用。宿主自有失败（ADMIN/NONE/已配置无 owner）不再把模型正文当完成，新增 `AgentRunStatus.TOOL_FAILURE`、`RUN_TOOL_FAILURE_UNRESOLVED`、`TerminationReason.TOOL_FAILURE`。
- A/B 交互均支持：普通聊天追问走新 Run；同一 Run 通过既有 `ask_user`/`WAITING_INPUT`/`RunInterrupt` 恢复，checkpoint 保留失败事实。
- `allowed_values` 恢复为失败 detail，但 Runtime 仅在失败路径按 `skill_id/skill_key` 调用 `ResolveClient` 读取当前 Skill，并用当前 invocation contract properties 过滤；读取失败或契约不匹配即丢弃值，不回退快照。
- 删除 `"*"` 未知写入通配兼容；开发阶段不迁移、不回填。
- 新增回归：`tests/agent_core/test_runner.py`（首次失败不完成、替代 effectful 阻断、A/B 交互）、`tests/agent_runtime/test_worker_error_contract.py`（最新契约过滤 allowed_values）、`tests/agent_runtime/test_finalization_funnel.py`（TOOL_FAILURE 真实失败收尾）；`tests/agent_core` 516 passed，runtime 相关 74 passed。
- 性能候选 256/512 次/场景：long-run 通过“按需选择 control 列”回到基线；recovery p50 11.5–13.0µs、long-run p50 2295–2635µs、rejected p50 54–58µs，均落在基线 A/B 附近；rejected p95 仍出现 86–95µs 噪声尾部，审计行宽/WAL 未独立采样。用户接受该证据缺口后继续验收。

### 浏览器验收与复查修复（2026-11-28）

- 本地 `muad` 从 `0062` 升级到 `0063`，新增 `runtime.tool_call_audit` 的 `retryable` / `retry_disposition` / `agent_disposition` / `diagnostic_json`；服务经 `scripts/dev.sh` 重启后使用新代码。
- S-01：Run `4bdfa8a5-…`，`RUN_NO_PROGRESS`，`SCHEDULE_INVOCATION_CONTRACT_MISSING` + `CONTRACT_MISSING` + `payload.skill_id` + `REIMPORT_SKILL`；中文/英文同义，刷新后单份正文，任务中心保持 7 条。
- S-02：Run `4b3bb0f6-…`，`RUN_TOOL_FAILURE_UNRESOLVED`，同码 + `CONTRACT_INVALID` + `payload.skill_key` + `REIMPORT_SKILL`；详情与导出 `retryable=false` / `BLOCK` / `STOP`，与 S-01 的 Run ID 和来源标签可区分。
- E-01：Run `8bf58867-…`，`SANDBOX_TIMEOUT` 为根因，`current_time` 独立成功，重试被 `RETRY_GUARDED` 保护；正文呈现业务原因与影响；Console 技术详情呈现保护动作和 `TOOL_FAILURE`。
- B-01：`mssp-asset-query-local` 缺少 `company_ids` 时同一 Run `WAITING_INPUT`；补充客户 1001 后草稿预检 `PASSED` 并确认，任务中心只新增 1 条（7 → 8）。
- B-02：导出未命中 canary/凭据；旧 Run `0522b17c-…` 的 `retryable=UNKNOWN`；`e2e-other` 租户访问本租户 Run 详情和导出均为 404。
- 同步修复：无效契约从 `SCHEDULE_DRAFT_INVALID`/USER 改为 `SCHEDULE_INVOCATION_CONTRACT_MISSING`/ADMIN；`ToolError` 对 ADMIN/NONE 失败统一 `retryable=false`/`BLOCK`/`STOP`，Runner 首个宿主失败直接 `TOOL_FAILURE`；硬失败保留 `source_call_id`；`RETRY_GUARDED` 在 blocked/unknown 同源时保留来源；Console 技术详情把 `fields` 渲染为 `field (reason)`。
- 按用户复查意见，WebChat 不再返回或渲染 `technical_details`：`WebchatHistoryMessage` 删除该字段，ASSISTANT_MESSAGE/RUN_FAILED 不再写技术详情，前端删除折叠块；用户只看到内置错误描述或模型总结，完整技术事实只在 Console 运行详情/导出。S-01 复测 Run `bd89e2ee-…`，S-02 复测 Run `e3a0bbe8-…`，页面均无技术详情块。
- 补充正常成功与自修复场景：N-01 Run `479acaad-…`（weather-query 上海 2 天，`COMPLETED`，无错误块）；C-01 Run `98453a74-…`（用户只给客户名 ACME，第一次 `UAT_CUSTOMER_UNKNOWN` 的错误列出 1001，模型未收到用户提示的正确值，仍自行改为 1001 后 `SUCCEEDED`，Run `COMPLETED`；Console 保留两次调用）。
- 回归：`tests/agent_core` 517 passed；`tests/skills/test_uat_self_correct.py` 3 passed；TASK-007 声明测试 11 passed；Worker/Console/frontend/catalog 170 passed；合并回归 683 passed；改动文件 `ruff` 与 29 个 source 文件 `mypy` 通过；前端 `npm run typecheck` 通过。
- 未在浏览器制造 E-01 的进程中断/恢复点，恢复事实由 `tests/agent_core/test_recovery_states.py` 覆盖；E-02 的 only-code 预检样本由 `tests/agent_runtime/test_tool_error_evidence_flow.py` 覆盖。
