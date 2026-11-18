---
{"version":1,"requirement":"async-skill-background"}
---
# Tasks: async-skill-background

> **需求基线**: `async-skill-background.design.md`（v0.5）、`async-skill-background.frontend.design.md`（v0.4）、`async-skill-background.scenarios.md`（v1.1）。
> **交互稿**: `async-skill-background.mock.html`，用于前端实现比对，不是生产代码。
> **外部前置**: 任务中心产物读取/交付状态依赖 `unified-artifact-delivery` 的 Worker 输出登记、`OUTPUT` 与 `TaskArtifactRef.deliveries[]`。未合入时，TASK-004/005 的产物字段按契约返回空，相关场景标记为依赖，不伪造数据。

## Task Index

| Task | 能力 | Depends | 主要场景 |
|------|------|---------|----------|
| TASK-001 | 错误原样、脱敏/有界投影，长错误/大结果不回滚终态 | - | S-03 / B-01 / E-04 |
| TASK-002 | Run 根任务发件全量化、DETACH 终态识别、终态通知不重复 | TASK-001 | S-02 / S-04 / E-02 |
| TASK-003 | 迟到结果独立物化为 `BACKGROUND_RESULT` | TASK-002 | S-03 / B-02 |
| TASK-004 | 后台任务读契约与现有任务中心读面扩展 | TASK-001 | S-02 / S-03 / S-05 / S-06 / S-07 / E-03 |
| TASK-005 | WebChat 后台任务分组、列表、详情、子任务、产物状态 | TASK-004 | S-01 / S-02 / S-03 / S-05 / S-06 / S-07 / E-03 / E-04 |
| TASK-006 | Run 侧后台语义、容量口径、等待就绪过滤 | TASK-002, TASK-003 | S-01 / E-01 / B-03 |
| TASK-007 | 回归、配置清单与需求收口 | TASK-001..TASK-006 | 全部 |

---

## TASK-001: 错误原样与发件有界

- **Status**: done
- **Depends**:
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.design.md#3.4.1 ToolResultRequest`, `#3.5 质量实现方案`
- **Scenarios**: S-03, B-01, E-04
- **Spec-Refs**: worker/harness-worker#RULE-001, api/harness-api#RULE-api-001, secret/harness-secret#RULE-secret-001, logging/harness-log#RULE-log-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py, apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py, apps/agent-worker/src/muad_agent_worker/delivery/messages.py, packages/contracts/src/muad_contracts/tool_runtime.py, tests/agent_worker/test_async_background_error_delivery.py, tests/contracts/test_async_background_contracts.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "error-delivery",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_worker/test_async_background_error_delivery.py",
      "tests/agent_worker/test_runtime_operation_races.py::test_b07_big_result_does_not_rollback_verified_terminal",
      "tests/contracts/test_async_background_contracts.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

让 ASYNC 失败时的 `error_message` 保持脱敏后的原始 stderr，不再被 `TASK_RESULT_UNVERIFIED` 等通用文案替换；在进入终态事务前完成脱敏、截断与大结果外置，保证长错误/大结果不会回滚任务终态。任务记录保存较完整的脱敏原文；`ToolResultRequest` 使用有界投影；外部投递正文使用独立展示上限。

### Checklist

- [x] `classify_failure` 对 `NON_RETRYABLE` 只改 `error_code`，不改写原始 `error_message`
- [x] 在 `write_terminal` 前完成 `redact_text`/`redact_value`；`TaskExecution.error_message` 保存较完整的脱敏原文
- [x] `ToolResultRequest.error_message` 上限提升，并由 Worker 在构造前截断；截断只记录长度/task_id，不记录正文
- [x] 大 `result` 超过协议字节上限时外置为 `TOOL_RESULT` Artifact，outbox 发送引用/预览
- [x] 终态事务内只组装确定性字段；非预期失败用 savepoint 只回滚 outbox，保留终态并记录 `RESULT_PUBLISH_FAILED`
- [x] `build_delivery_message` 的错误摘要展示上限与任务记录/模型投影分层；截断可见
- [x] 修改 `MAX_ERROR_CHARS` / `MAX_RESULT_CHARS` 等模块级常量后重生成 `docs/configuration-inventory.csv`
- [x] [S-03] 失败任务在任务记录/模型中看到原始错误，而不是通用文案
- [x] [B-01] 超长 stderr 在任务记录、模型投影、外部正文中按各自上限截断并留痕
- [x] [E-04] 长 stderr/大结果不导致任务停在 RUNNING 或丢失原文

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created
- [2026-11-18T00:01:28.814Z] started tests=ee9e455090c8

### Verification

- 尚未执行

---
- 2026-11-18T00:12:43.234Z PASS commands=1/1 executed=1 deduped=0 elapsed=6776ms tests=844743bea96b head=00edc6fe85d9 dirty=1 tests-changed
  - error-delivery PASS exit=0
## TASK-002: 发件全量化与 DETACH 终态识别

- **Status**: draft
- **Depends**: TASK-001
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.design.md#3.4.1 Worker 发件条件`, `#3.4.3 DETACH 终态识别`, `#3.5 质量实现方案`
- **Scenarios**: S-02, S-04, E-02
- **Spec-Refs**: worker/harness-worker#RULE-001, worker/harness-worker#RULE-003, im/harness-im#RULE-006, test/harness-test#RULE-test-001
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, tests/agent_worker/test_async_background_outbox.py, tests/agent_runtime/test_detached_terminal_notification.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "outbox-and-dedup",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_worker/test_async_background_outbox.py",
      "tests/agent_runtime/test_detached_terminal_notification.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

让拥有 runtime operation 的 Run 根任务在终态时无论 JOIN/DETACH 都写 `RuntimeResultOutbox`，使网页投递的 `WEBCHAT_RESULT_NOT_PUBLISHED` 前置自然满足；没有 operation 的 Run 任务与定时任务不写。把 `_submitted_detached_task` 从扫描工具参数改为查询本 Run 已受理的 DETACH operation，避免删除 `completion_mode` 后重复发终态通知。

### Checklist

- [ ] `_enqueue_result` 对 `parent_id IS NULL AND source_run_id IS NOT NULL AND source_operation_id IS NOT NULL` 的根任务，不区分 completion_mode 都写 outbox
- [ ] 无 operation 的 Run 任务不写 outbox，记录 `RESULT_PUBLISH_NOT_APPLICABLE`；定时任务不写
- [ ] `_submitted_detached_task` 改为查询 `tool_operation`（tenant/run/completion_mode=DETACH/task_id not null/未删除）
- [ ] Run 完成通知在已提交 DETACH 任务时按 `NOT_REQUIRED`/不重复外发处理
- [ ] 网页入箱前置满足：outbox 从 PENDING 到 SENT 后 `DeliveryLoop` 可提交任务结果
- [ ] [S-02] 成功结果经网页收件箱送达，且只一份
- [ ] [S-04] 企微成功/失败各一份，不额外重复 Run 完成结果
- [ ] [E-02] 投递失败按既有重试与终态处置，任务事实不丢

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

---

## TASK-003: 迟到结果物化

- **Status**: draft
- **Depends**: TASK-002
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.design.md#3.5 质量实现方案（LateBackgroundResultPump）`, `#3.3 数据设计`
- **Scenarios**: S-03, B-02
- **Spec-Refs**: worker/harness-worker#RULE-001, logging/harness-log#RULE-log-001, snapshot/harness-snapshot#RULE-snapshot-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/tool_result_receipts.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/materialization.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/late_results.py, apps/agent-runtime/src/muad_agent_runtime/main.py, tests/agent_runtime/test_late_background_result.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "late-result",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_runtime/test_late_background_result.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

Run 已终态后到达的结果，仍要成为下一轮可读的会话事实。`receive_result` 保持只写数据库；新增有界 `LateBackgroundResultPump`，领取 `late=true AND materialized=false` 的收件箱行，事务外准备大结果外置，事务内追加 `BACKGROUND_RESULT` 并置 `materialized=true`。不恢复 Run、不续 lease、不复活执行；会话已删除时跳过并留痕。

### Checklist

- [ ] `receive_result` 不新增文件 IO/Artifact 外置，仍只做 DB 收件与回执
- [ ] `LateBackgroundResultPump` 有界领取 `late=true AND materialized=false`，使用 `SKIP LOCKED`/行锁，避免并发重复
- [ ] 事务外准备 Artifact/预览；事务内追加 `BACKGROUND_RESULT`，`source_event_id` 与普通物化同形，保证幂等
- [ ] 更新 `inbox.materialized=true` 与 `canonical_event_id`；不恢复 Run、不续 lease
- [ ] 会话已删除/不存在时跳过，记录 `background_result_late_skipped_total{reason}`；不写不可见事件
- [ ] 接入 Runtime 受监督循环，不新增部署单元
- [ ] `worker/harness-worker#RULE-001` 的 Spec Delta 已随任务沉淀
- [ ] [S-03] 迟到失败原文下一轮可读
- [ ] [B-02] Run 已终态后结果仍进入会话事实，且不重复

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "worker/harness-worker",
    "ref": "RULE-001",
    "before": "异步工具调用以租户、owner 与真实工具调用身份唯一，输入指纹用于同身份冲突判断，不能参与唯一身份以放行变参重试。提交与结果必须可持久重放；等待前检查结果就绪，按每个调用保存独立 receipt 与消费水位，结果乱序或重复不得丢失或重复消费。持久收件箱与数据库补扫保证恢复正确性，独立唤醒 hint 仅降低延迟；终态收到迟到结果只留审计，不得恢复执行。",
    "rule": "异步工具调用以租户、owner 与真实工具调用身份唯一，输入指纹用于同身份冲突判断，不能参与唯一身份以放行变参重试。提交与结果必须可持久重放；等待前检查结果就绪，按每个调用保存独立 receipt 与消费水位，结果乱序或重复不得丢失或重复消费。持久收件箱与数据库补扫保证恢复正确性，独立唤醒 hint 仅降低延迟；终态收到迟到结果不得恢复执行，但可幂等物化为会话事实（`BACKGROUND_RESULT`）供下一轮读取；物化失败、会话已删除或跳过必须显式可观测。",
    "evidence": "async-skill-background 设计要求迟到结果下一轮可读且不复活 Run；实现见 LateBackgroundResultPump，测试见 tests/agent_runtime/test_late_background_result.py"
  }
]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

---

## TASK-004: 任务中心后台读契约与接口

- **Status**: draft
- **Depends**: TASK-001
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.design.md#3.4.4 后台任务读面`, `#3.4.5 任务中心读面`; `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.frontend.design.md#3.4 组件接口契约`
- **Scenarios**: S-02, S-03, S-05, S-06, S-07, E-03
- **Spec-Refs**: im/harness-im#RULE-006, persistence/harness-data#RULE-001, api/harness-api#RULE-api-001, test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/scheduling.py, apps/agent-worker/src/muad_agent_worker/application/task_service.py, apps/agent-worker/src/muad_agent_worker/application/schedule_read.py, apps/agent-worker/src/muad_agent_worker/api/tasks.py, apps/agent-worker/src/muad_agent_worker/api/executions.py, apps/im-gateway/src/muad_im_gateway/application/task_center.py, apps/im-gateway/src/muad_im_gateway/application/worker_client.py, apps/im-gateway/src/muad_im_gateway/api/task_center.py, tests/contracts/test_async_task_center_contracts.py, tests/agent_worker/test_async_task_center_read.py, tests/gateway/test_async_task_center.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "task-center-read",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/contracts/test_async_task_center_contracts.py",
      "tests/agent_worker/test_async_task_center_read.py",
      "tests/gateway/test_async_task_center.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在现有任务中心 service/router 上增加后台任务列表与详情能力，不新增后台专用读服务。扩展共享 `ExecutionDetail`（放宽 snapshot schema 到 `>=3`，补 `agent_id/task_type/skill_name/parent_id/item_key/counts/partial_failure/artifact_total/children`），扩展 Worker `TaskService.list` 的 `agent_ids/background_only` 过滤与执行详情 `children_page`，Gateway 新增 `TaskCenterService.list_tasks` 并在 `get_execution` 校验 Agent 归属。产物读取依赖统一 Artifact 的 `OUTPUT` 与 `deliveries[]`；未合入时返回空产物字段，不伪造。

### Checklist

- [ ] `ExecutionDetail.snapshot_schema_version` 放宽为 `>=3`；定时任务仍写 4、立即任务写 3
- [ ] `ExecutionDetail` 补 `agent_id/task_type/skill_name/parent_id/item_key/counts/partial_failure/artifact_total/children`
- [ ] 新增共享小契约 `TaskResultCounts`、`TaskChildItem`、`TaskCenterTaskItem`，不新增后台专用详情 DTO
- [ ] Worker `TaskService.list` 支持 `agent_ids`、`background_only`（`IMMEDIATE`/`schedule_id IS NULL`/`parent_id IS NULL`/`source_run_id IS NOT NULL`），list/count 同条件
- [ ] `TaskService`/`ScheduleReadService.execution_detail` 返回子任务分页、`counts`、`partial_failure`、`artifact_total`、产物聚合
- [ ] 后台 `result_artifacts` 聚合父 + 直接子任务 `OUTPUT`，按逻辑文件最新版本，含 `deliveries[]`
- [ ] Gateway `TaskCenterService.list_tasks` 走现有 `build_task_center_router(surface)`；详情复用 `get_execution`，按 `ExecutionDetail.agent_id` 核属
- [ ] Worker 跨 Schema 只读聚合遵守 `tenant_id`/`is_deleted`/批量查询，不写 `runtime.*`
- [ ] [S-02] 后台列表/详情可取结果与产物（产物依赖统一 Artifact）
- [ ] [S-03] 详情返回失败原文
- [ ] [S-05] 定时/后台详情共用执行详情，分组不混淆
- [ ] [S-06] 子任务标签可辨认、错误摘要可读
- [ ] [S-07] `BEST_EFFORT` 部分失败返回计数与 `partial_failure=true`
- [ ] [E-03] 撤销 Agent 后列表/详情/产物 404，不泄露存在性

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

---

## TASK-005: WebChat 后台任务分组与详情

- **Status**: draft
- **Depends**: TASK-004
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.frontend.design.md#2.2 功能方案`, `#3.2 页面与路由结构`, `#3.4 组件接口契约`, `#3.6 UI 状态`, `#6 交互稿`; `async-skill-background.mock.html`
- **Scenarios**: S-01, S-02, S-03, S-05, S-06, S-07, E-03, E-04
- **Spec-Refs**: frontend/harness-frontend, ui/harness-ui, i18n/harness-i18n, im/harness-im#RULE-006, test/harness-test#RULE-test-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/task-center/**, apps/console-platform/frontend/src/modules/webchat/types.ts, apps/console-platform/frontend/src/modules/webchat/services/taskCenterApi.ts, apps/console-platform/frontend/src/modules/webchat/components/WebchatWorkspaceTabs.tsx, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/frontend/test_async_skill_background_task_center_ui.py, tests/frontend/test_scheduled_v2_task_center_ui.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "background-task-center-ui",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/frontend/test_async_skill_background_task_center_ui.py",
      "tests/frontend/test_scheduled_v2_task_center_ui.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在现有 WebChat 任务中心内增加「后台任务」分组，不新增独立页面或路由。工作区 Tab 文案改为「任务」，页面标题改为「任务中心」；后台列表/详情通过现有 `/chat/tasks` 查询参数 `view=background&taskId=` 表达。列表展示 Skill 名、状态、部分失败计数、子任务进度、含产物；详情展示结果/失败原文/产物/子任务；产物按 `deliveries[]` 展示交付状态；长错误可展开；子任务标签可辨认。视觉复用现有 `task-center.css`、`StatusTag`、`TaskArtifactList`、`PaginationFooter`、`DateTimeText`，以交互稿为交互参照，不照搬其独立样式。

### Checklist

- [ ] 工作区 Tab `chat.tabs.tasks` →「任务」；页面标题 `chat.taskCenter.label` →「任务中心」；中英文词条同步
- [ ] `TaskCenterPage` 增加「定时任务 / 后台任务」分组；定时任务列表/详情组件原样
- [ ] `TaskCenterClient` 增加 `listTasks`；`getExecution` 增加可选 `childrenPage`，不新增详情 client
- [ ] `BackgroundTaskListPanel` 消费 `TaskCenterTaskItem`：Skill 名、状态、部分失败计数、子任务进度、含产物
- [ ] `BackgroundTaskDetailPanel` 消费扩展后的 `ExecutionDetail`：结果、失败原文、产物、子任务
- [ ] `TaskArtifactList` 展示 `deliveries[]`：已交付/已降级/待投递/失败；字段缺失隐藏徽标，空数组显示未请求推送
- [ ] 子任务表分页、标签可辨认、失败摘要首行 ≤200，点开看完整原文
- [ ] 长错误默认折叠、可展开/复制；产物超 100 个显示总数与截断提示
- [ ] `?view=background&taskId=` 刷新/重进保持同一详情；`/chat/tasks/:scheduleId` 路径优先，不改 `parseChatTabs`
- [ ] 无轮询、无 SSE；切 Tab 读取一次，翻页/开详情/子任务翻页才再读
- [ ] [S-01] 本轮回复结束后会话可继续；后台任务在任务中心可见
- [ ] [S-02] 成功结果/产物在后台详情可取；聊天时间线不注入结果
- [ ] [S-03] 失败原文在后台详情/子任务可读
- [ ] [S-05] 两个分组互不混排，深链正确
- [ ] [S-06] 批量失败项可定位，标签不是哈希
- [ ] [S-07] 部分失败计数在列表与详情一致
- [ ] [E-03] 撤销 Agent 后列表/详情/下载按不存在处理
- [ ] [E-04] 长错误/大结果不显示为一直运行，正文有截断提示

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

---

## TASK-006: Run 侧后台语义与容量

- **Status**: draft
- **Depends**: TASK-002, TASK-003
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.design.md#3.2 架构设计`, `#3.4.2 execute 工具契约`, `#3.5 质量实现方案`, `#2.4.2 Run 取消与后台任务`
- **Scenarios**: S-01, E-01, B-03
- **Spec-Refs**: worker/harness-worker#RULE-001, snapshot/harness-snapshot#RULE-snapshot-001, api/harness-api#RULE-api-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/operations.py, apps/agent-runtime/src/muad_agent_runtime/application/async_tools/continuation_service.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, tests/agent_runtime/test_async_background_semantics.py, tests/agent_runtime/test_tool_wait_state_machine.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "background-semantics",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/agent_runtime/test_async_background_semantics.py",
      "tests/agent_runtime/test_tool_wait_state_machine.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

Run 侧 ASYNC 提交恒为后台任务：`execute_skill` handler 固定 `CompletionMode.DETACH`，模型工具不再暴露/接受 `completion_mode`；`_capacity` 只统计仍在执行/排队的 operation；`has_ready`/`_ready_predicate` 只把 JOIN operation 的未物化结果视为 WAITING_TOOL 就绪，避免 DETACH 结果提前唤醒 Run。Run 取消不级联取消 DETACH 任务，保持已提交任务独立执行。

### Checklist

- [ ] `execute`/legacy `execute_skill` 删除 `completion_mode` 参数；`execute_skill` handler 固定 DETACH，SYNC 拒绝分支清理
- [ ] 工具描述更新为「ASYNC 提交后台执行，结果在任务中心/送达会话」
- [ ] `_capacity` 统计 `SUBMIT_PENDING/SUBMITTED/TASK_ACCEPTED/RUNNING`；`RESULT_RECEIVED/MATERIALIZED`/终态不占
- [ ] `continuation_service.has_ready` / `_ready_predicate` 只把 JOIN operation 的未物化 inbox 视为等待就绪
- [ ] Run 侧不再因 ASYNC 任务进入 `WAITING_TOOL`；当前回合结束前仍按单 Run 准入
- [ ] Run 取消不级联取消 DETACH；任务中心只读；文档/场景保持该边界
- [ ] [S-01] 提交后不再因后台任务长时间占用会话；本轮回复结束可继续
- [ ] [E-01] 提交未获受理时明确失败，不产生后台任务
- [ ] [B-03] 停止当前会话不取消已提交的后台任务，任务继续并可读

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行

---

## TASK-007: 回归、配置清单与需求收口

- **Status**: draft
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006
- **Source**: `.code-flow/tasks/2026-11-17/async-skill-background/async-skill-background.design.md#4.5 实施批次`, `#6 需求追溯矩阵`; `async-skill-background.scenarios.md`; `.code-flow/specs/test/harness-test.md`
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, S-06, S-07, E-01, E-02, E-03, E-04, B-01, B-02, B-03
- **Spec-Refs**: test/harness-test#RULE-test-001
- **Paths**: tests/async_skill_background_inventory.py, docs/configuration-inventory.csv, .code-flow/tasks/2026-11-17/async-skill-background/**, tests/test_configuration_inventory.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "inventory",
    "argv": [
      "uv",
      "run",
      "pytest",
      "-q",
      "tests/async_skill_background_inventory.py",
      "tests/test_configuration_inventory.py"
    ],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

收口本需求的实现盘面：建立 `tests/async_skill_background_inventory.py`，交叉核对任务文档的 TASK、Spec-Refs、Scenarios、声明测试与真实文件是否一致；重生成 `docs/configuration-inventory.csv`（若 TASK-001 修改模块级常量）；复核交互稿、设计、场景与代码事实一致。该任务不新增产品能力，只保证可回归、可归档。

### Checklist

- [ ] 新增 `tests/async_skill_background_inventory.py`，按真实盘面核对 TASK/测试/Spec-Refs/Scenarios，不依赖自查断言
- [ ] 为每个 TASK、Spec-Ref、Scenario 留下可失败的行；`-k`/路径必须真实命中
- [ ] 若修改了模块级常量，重生成 `docs/configuration-inventory.csv` 并跑 `tests/test_configuration_inventory.py`
- [ ] 复核 `async-skill-background.design.md`、`frontend.design.md`、`scenarios.md` 与实现一致
- [ ] 复核 `unified-artifact-delivery` 的 `OUTPUT`/`deliveries[]` 依赖已满足或明确标记未合入
- [ ] 复核交互稿路径和前端设计 §6 引用仍有效
- [ ] 汇总本需求声明的测试命令与真实执行结果

### Spec Delta

```json
[]
```

### Log

- [2026-11-18] created

### Verification

- 尚未执行
