---
{"version":1,"requirement":"im-attachment-integrity"}
---

# Tasks: im-attachment-integrity

<!--
一个需求一份任务文档。每个 TASK 使用一个 `## TASK-NNN: 标题` 段。
Source 引用 `im-attachment-integrity.backend.design.md` / PRD 章节；场景引用 `im-attachment-integrity.scenarios.md`。
-->

## TASK-001: 新增批次配置与 Connector 设置读取

- **Status**: done
- **Depends**:
- **Source**: im-attachment-integrity.backend.design.md#3.3, #3.4
- **Scenarios**: im-attachment-integrity:B-05
- **Spec-Refs**: test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/platform_settings.py, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py, apps/im-connector/src/muad_im_connector/settings.py, apps/im-connector/src/muad_im_connector/platform_settings.py, apps/im-connector/src/muad_im_connector/main.py, tests/contracts/test_platform_settings.py, tests/test_platform_settings_schema.py, tests/console_platform/test_platform_settings_api.py, tests/im_connector/test_platform_settings_client.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "batch-settings",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/contracts/test_platform_settings.py", "tests/test_platform_settings_schema.py", "tests/console_platform/test_platform_settings_api.py", "tests/im_connector/test_platform_settings_client.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

在系统设置页 `im` 配置中新增三个字段，并让 Connector 读取/缓存：

- `im.inbound_batch_quiet_ms`：默认 300，范围 100–2000；
- `im.inbound_batch_max_wait_ms`：默认 5000，范围 500–30000；
- `im.inbound_batch_max_attachments`：默认 5，范围 1–20。

Connector 通过 Console 内部 `platform-settings` 快照读取，按租户缓存；成功快照优先，失败用最近一次成功快照，从未成功用代码默认值并记 WARNING。页面保存后最多延迟一个缓存周期对新批次生效。

### Checklist

- [x] `ImSettings` 增加三个字段、范围与默认值；旧配置反序列化兼容。
- [x] `platform_settings_catalog` 增加三项，系统设置页按既有 catalog 渲染。
- [x] Connector 设置客户端按租户缓存，失败降级顺序正确且不阻断入站。
- [x] 页面修改上限后，新批次使用新值；旧批次不追溯。
- [x] 测试覆盖默认值、范围、缓存命中/失败/空快照。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T11:22:32.306Z] started tests=4bc6ceb75553

### Verification

- 尚未执行
- 2026-11-20T11:27:06.775Z FAIL commands=0/1 executed=1 deduped=0 elapsed=51068ms tests=4bc6ceb75553 head=deb93f4e7368 dirty=1
  - batch-settings FAIL exit=1
- 2026-11-20T11:28:05.872Z PASS commands=1/1 executed=1 deduped=0 elapsed=51298ms tests=4bc6ceb75553 head=deb93f4e7368 dirty=1
  - batch-settings PASS exit=0
## TASK-002: Connector 批次、在途成员与 journal

- **Status**: done
- **Depends**: TASK-001
- **Source**: im-attachment-integrity.backend.design.md#3.2.1, #3.2.2, #3.5
- **Scenarios**: im-attachment-integrity:S-01, im-attachment-integrity:S-05, im-attachment-integrity:B-01, im-attachment-integrity:B-02, im-attachment-integrity:B-03, im-attachment-integrity:B-04, im-attachment-integrity:E-03, im-attachment-integrity:E-04
- **Spec-Refs**: im/harness-im#RULE-im-002, test/harness-test#RULE-test-001, test/harness-test#RULE-002
- **Paths**: packages/contracts/src/muad_contracts/im.py, packages/im-core/src/muad_im_core/inbound_batch.py, packages/im-core/src/muad_im_core/ingress.py, apps/im-connector/src/muad_im_connector/runtime.py, apps/im-connector/src/muad_im_connector/main.py, tests/im_core/test_inbound_batch.py, tests/im_core/test_inbound_publisher.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "inbound-batch",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/im_core/test_inbound_batch.py", "tests/im_core/test_inbound_publisher.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

实现 Connector 入站批次与最小 Redis journal：

- 原始帧到达、取件前登记 `PENDING` 成员；文字立即 `READY`，媒体成功/失败后更新；
- 按 route 维护批次、静默窗口、最大等待、附件上限；
- 第二段文字先 flush 前一批；命令/不支持媒体不参与聚合；
- 达到附件上限后停止接收，超限追加 `ATTACHMENT_COUNT_EXCEEDED` 并整批失败；
- 最大等待仍有 `PENDING` 媒体：追加 `ATTACHMENT_FETCH_TIMEOUT`，整批失败，不允许文字单独执行；
- `InboundEvent.require_complete_attachments` 标记多成员批次；
- `InboundPublisher` 支持全部成员 `dedupe_ids`，`InboundBatchJournal` 保存主消息/成员/合并事件/状态，重投不另起主消息；
- 指标/日志：合并、超时、数量、失败、journal 恢复。

### Checklist

- [x] 下载前登记成员；同 route 其他用户流量不影响本批计时。
- [x] 文字+单附件、文字+多附件、仅附件、文件先到均只形成一个合并事件。
- [x] 等待期重复文字/文件被忽略；发布后任一成员重投不产生第二个 Run。
- [x] XADD 后标记未完成时，Journal 能按原主消息 id 重发/确认。
- [x] 超时、超限、附件失败整批失败；不产生部分成功 Run。
- [x] 命令/不支持媒体按既有语义独立处理，不静默丢。
- [x] 测试覆盖 fake clock/真实 Redis journal、重投、崩溃恢复、数量/超时边界。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "im/harness-im",
    "rule": "同一逻辑发送的文字与 1..N 个媒体帧必须在 Connector 按 route 有界聚合：下载前登记在途成员，全部附件成功物化后发布一个 InboundEvent；批次成员共享去重；窗口与上限由系统设置 im.inbound_batch_* 定义；批次失败整批不执行，不产生部分成功结果。",
    "evidence": "im-attachment-integrity design v1.1 §3.2.1/§3.2.2；S-01/B-01/B-02/E-04"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T11:38:54.654Z] started tests=c45adf898420

### Verification

- 尚未执行
- 2026-11-20T11:39:02.743Z PASS commands=1/1 executed=1 deduped=0 elapsed=3742ms tests=c45adf898420 head=d43e92e4b1a9 dirty=1
  - inbound-batch PASS exit=0
## TASK-003: Gateway 批次失败语义与友好提示

- **Status**: done
- **Depends**: TASK-002
- **Source**: im-attachment-integrity.backend.design.md#3.2.3
- **Scenarios**: im-attachment-integrity:E-01, im-attachment-integrity:E-04, im-attachment-integrity:B-01
- **Spec-Refs**: im/harness-im#RULE-im-002, test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/channel.py, apps/im-gateway/src/muad_im_gateway/application/inbound_streams.py, apps/im-gateway/src/muad_im_gateway/application/inbound.py, config/api-messages.yaml, tests/gateway/test_inbound_batch_failure.py, tests/gateway/test_inbound_attachment_flow.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "gateway-batch-failure",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/gateway/test_inbound_batch_failure.py", "tests/gateway/test_inbound_attachment_flow.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

把 Connector 的批次失败转成 Gateway 的明确失败面：

- `ChannelEnvelope` 增加 `require_complete_attachments`，`_envelope` 从 `InboundEvent` 映射；
- `_handle_message` 在 `_collect_attachments` 后，若批次要求完整且 `audit_outcome=FAILED`：不创建 Run，回执/审计已由附件链路完成；
- `ATTACHMENT_COUNT_EXCEEDED` 文案改为“最多 N 个，请分批重新发送；本次未处理”，N 来自租户配置；
- 新增 `ATTACHMENT_BATCH_INCOMPLETE` 文案：“本次附件未全部收到，未处理；请连同说明重新发送整批附件”，原因复用现有附件原因码；
- 单帧消息保持原行为。

### Checklist

- [x] `require_complete_attachments` 契约默认 false，旧生产者/旧测试兼容。
- [x] 批次失败不建 Run、不产生假成功回复；用户看到明确整批重发提示。
- [x] 超限文案带当前租户上限并引导分批。
- [x] 单帧附件/纯文本行为不变。
- [x] 测试覆盖 FAILED/RECEIVED、超限、超时、单帧回归。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T11:44:21.667Z] started tests=a9658911f0ed

### Verification

- 尚未执行
- 2026-11-20T11:44:35.848Z PASS commands=1/1 executed=1 deduped=0 elapsed=10220ms tests=a9658911f0ed head=7374346adca5 dirty=1
  - gateway-batch-failure PASS exit=0
## TASK-004: 任务产物 owner 读取与列表一致

- **Status**: done
- **Depends**:
- **Source**: im-attachment-integrity.backend.design.md#3.2.4
- **Scenarios**: im-attachment-integrity:S-02, im-attachment-integrity:E-02
- **Spec-Refs**: im/harness-im#RULE-006, auth/harness-auth#RULE-auth-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/task_artifacts.py, tests/agent_runtime/test_attachment_tools.py, tests/agent_runtime/test_attachment_task_owner.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "task-artifact-owner",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_attachment_tools.py", "tests/agent_runtime/test_attachment_task_owner.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

修复任务产物“列得出、读不到”：

- `_owned_by_actor` 增加 `task_id IS NOT NULL + OUTPUT + user_id + agent_id` 精确分支；
- `_load` / `_load_for_update` 复用 `task_artifacts` owner helper 复核 live `task_execution`；
- `list_attachments(scope="agent")` 对 task 行追加同一 live task 条件；
- 不放行 `agent_id=NULL`、`user_id=NULL`、软删或 task 不匹配；
- 历史空 owner 行不回填，明确不可访问。

### Checklist

- [x] 任务产物 `run_id/conversation_id` 为空但 owner 完整时可列出/读取。
- [x] 列表与读取使用同一 owner 口径，不再“列得出、读不到”。
- [x] 跨用户/跨租户/空 owner/软删/live task 不存在均拒绝且不泄露。
- [x] 普通 Run/入站产物归属行为不变。
- [x] 测试使用真实 PostgreSQL + 真实 owner 行。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T11:28:20.347Z] started tests=9514260a8adb

### Verification

- 尚未执行
- 2026-11-20T11:32:16.785Z PASS commands=1/1 executed=1 deduped=0 elapsed=23223ms tests=9514260a8adb head=9f9c093d4b83 dirty=1
  - task-artifact-owner PASS exit=0
## TASK-005: 附件来源标注与内部产物显式访问

- **Status**: done
- **Depends**: TASK-004
- **Source**: im-attachment-integrity.backend.design.md#3.2.5
- **Scenarios**: im-attachment-integrity:S-03
- **Spec-Refs**: im/harness-im#RULE-006, auth/harness-auth#RULE-auth-001, secret/harness-secret#RULE-secret-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, tests/agent_runtime/test_attachment_tools.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "attachment-provenance",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_attachment_tools.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

修正附件枚举语义：

- `direction` 增加 `internal`；
- `_list_line`：`INBOUND_*`→入站、`OUTPUT`→自产、`TOOL_RESULT`→工具结果、其他→内部产物；
- `direction=inbound` 仍只查 `INBOUND_*`；默认保留全量；
- `read_attachment` 对 owner 的 `TOOL_RESULT` 保持可读；
- `deliver_artifact` 增加 `allow_internal`（默认 false）；用户明确要求时允许交付 `TOOL_RESULT`，审计保留参数；
- 工具说明/模型口径：工具结果不是用户附件；当前消息没有附件时不得用历史附件代替，必须让用户重发。

### Checklist

- [x] `TOOL_RESULT` 不再标“入站”，显式访问仍可用。
- [x] `direction=inbound` 只返回用户入站附件。
- [x] `allow_internal` 默认拒绝、显式允许；不带 owner 的调用仍拒绝。
- [x] 工具描述/模型口径更新，覆盖“当前消息权威”测试。
- [x] 无敏感值进入列表/日志。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "im/harness-im",
    "rule": "附件枚举必须按 artifact_type 标注来源：INBOUND_* 为入站、OUTPUT 为自产、TOOL_RESULT 为工具结果、其他为内部产物；TOOL_RESULT 不得标成入站。显式访问保留，内部产物默认不交付，用户明确要求时按 owner 校验交付。",
    "evidence": "im-attachment-integrity design v1.1 §3.2.5；S-03"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T11:47:47.871Z] started tests=a74a9ce72422

### Verification

- 尚未执行
- 2026-11-20T11:48:15.609Z FAIL commands=0/1 executed=1 deduped=0 elapsed=22826ms tests=a74a9ce72422 head=4b9a6b4c0e05 dirty=1
  - attachment-provenance FAIL exit=1
- 2026-11-20T11:48:57.304Z PASS commands=1/1 executed=1 deduped=0 elapsed=21947ms tests=a74a9ce72422 head=4b9a6b4c0e05 dirty=1
  - attachment-provenance PASS exit=0
## TASK-006: WebChat/MSSWWEB 任务产物发布与下载

- **Status**: done
- **Depends**: TASK-004, TASK-005
- **Source**: im-attachment-integrity.backend.design.md#3.2.4.1
- **Scenarios**: im-attachment-integrity:S-02, im-attachment-integrity:S-04, im-attachment-integrity:S-06, im-attachment-integrity:E-02
- **Spec-Refs**: im/harness-im#RULE-006, im/harness-im#RULE-007, auth/harness-auth#RULE-auth-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/attachments/publication.py, apps/agent-runtime/src/muad_agent_runtime/application/webchat_artifacts.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, apps/agent-runtime/src/muad_agent_runtime/application/attachments/task_artifacts.py, tests/agent_runtime/test_attachment_tools.py, tests/agent_runtime/test_capacity_webchat_artifact_resolve.py, tests/agent_runtime/test_webchat_task_artifact_delivery.py, tests/gateway/test_msswwweb_surface.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "web-artifact-delivery",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_attachment_tools.py", "tests/agent_runtime/test_capacity_webchat_artifact_resolve.py", "tests/agent_runtime/test_webchat_task_artifact_delivery.py", "tests/gateway/test_msswwweb_surface.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

让 WebChat 与 MSSWWEB 能真正交付/下载任务产物：

- `publish_artifact_to_conversation(..., allow_internal=False)` 允许无会话 `OUTPUT + task_id`；`TOOL_RESULT` 仅显式允许；
- `_source_owner` 增加 task owner（复用 `task_artifacts` owner helper）；`_assert_same_owner` 要求目标 Run 与 task owner 一致；
- `webchat_artifacts` 允许无会话任务产物，跨会话仍要求目标会话发布事实 + checksum；下载原版本，不回退最新版；
- `deliver_artifact` 走 `_publish_to_conversation` 时透传 `allow_internal`；IM 仍走 Gateway 内部 artifact 解析，不直读存储；
- 两个 Web surface 共用同一套逻辑，不按通道分叉。

### Checklist

- [x] WebChat 与 MSSWWEB 都能交付任务 report/xlsx。
- [x] 下载返回任务当时不可变版本，不是 404/最新版顶替。
- [x] `TOOL_RESULT` 只有显式 `allow_internal` 才能发布；默认拒绝。
- [x] 跨用户/跨 Agent/软删/无发布事实均拒绝且不泄露。
- [x] IM 交付路径保持既有 Gateway 解析，不新增直读存储。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T11:52:07.707Z] started tests=c5d62b757e5b

### Verification

- 尚未执行
- 2026-11-20T11:52:44.554Z FAIL commands=0/1 executed=1 deduped=0 elapsed=31988ms tests=c5d62b757e5b head=a15dd564f4a6 dirty=1
  - web-artifact-delivery FAIL exit=1
- 2026-11-20T11:53:36.466Z PASS commands=1/1 executed=1 deduped=0 elapsed=32076ms tests=c5d62b757e5b head=a15dd564f4a6 dirty=1
  - web-artifact-delivery PASS exit=0

## TASK-007: 定时任务产物的按任务发现与交付闭环

- **Status**: done
- **Depends**: TASK-006
- **Source**: im-attachment-integrity.backend.design.md#3.2.4
- **Scenarios**: im-attachment-integrity:S-02, im-attachment-integrity:S-06
- **Spec-Refs**: im/harness-im#RULE-006, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py, tests/agent_runtime/test_attachment_task_owner.py, tests/agent_runtime/test_webchat_task_artifact_delivery.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "task-artifact-discovery",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_attachment_task_owner.py", "tests/agent_runtime/test_webchat_task_artifact_delivery.py", "tests/agent_runtime/test_attachment_tools.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

让“把某次定时任务/后台执行的产物发给我”在真实对话里可发现、可交付：

- `list_attachments` 增加 `scope=task`，要求 `task_id`；只列该 `task_execution` 的产物，归属继续复用 `task_owner_predicate`（同租户 + user/agent 精确匹配 + live execution），不引入新的授权口径；
- 工具说明明确：当前消息附件仍是该消息的唯一输入依据；但用户明确要求某次任务执行的文件时，应使用 `scope=task` 列出并逐个 `deliver_artifact`，不能直接回答“不可寻址”；
- 真实 PostgreSQL 回归：只列目标任务、跨用户/跨 Agent/软删为空；WebChat 走真实工具列出任务产物 → 交付 → 下载。

### Checklist

- [x] `list_attachments(scope=task, task_id=...)` 只返回该 execution 的产物。
- [x] 缺 `task_id`、非法 UUID、把 `task_id` 用在其他 scope 都显式拒绝。
- [x] 跨用户、跨 Agent、软删 execution 均返回空，不泄露存在性。
- [x] WebChat 能从任务产物列表走到 `deliver_artifact` 并成功下载。
- [x] 工具说明不再让模型把“当前消息没附件”误判成“任务产物不可访问”。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T13:25:29.180Z] started tests=541667c637c9

### Verification

- 尚未执行
- 2026-11-20T13:27:42.735Z PASS commands=1/1 executed=1 deduped=0 elapsed=26314ms tests=541667c637c9 head=5f64c02e0842 dirty=1
  - task-artifact-discovery PASS exit=0

## TASK-008: 定时任务结果转发默认全文

- **Status**: done
- **Depends**: TASK-007
- **Source**: 2026-11-20 实测反馈（转发定时任务结果时正文被摘要）
- **Scenarios**: im-attachment-integrity:S-02, im-attachment-integrity:S-06
- **Spec-Refs**: im/harness-im#RULE-006, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, tests/agent_runtime/test_task_handoff.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "task-result-verbatim",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_task_handoff.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

用户要求转发定时/后台任务执行结果时，正文默认**全文照录**，不能擅自摘要：

- `get_task` 的工具说明增加硬约束：用户要求转发已完成任务的结果时，原样复制 `result.text`，不得摘要、压缩或改写；只有用户明确要求摘要时才总结；
- 同时提示 `list_attachments(scope=task, task_id=...)`，把“正文全文 + 任务产物”放在同一条可执行路径上；
- 用工具契约测试锁住这条提示，避免后续改描述时退化。

### Checklist

- [x] `get_task` 说明明确要求 `result.text` 原文照录。
- [x] 说明保留“用户明确要求摘要时才摘要”的例外。
- [x] 说明包含任务产物列表入口。
- [x] 既有任务工具测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T13:37:54.786Z] started tests=1ebbb296b715

### Verification

- 尚未执行
- 2026-11-20T13:38:30.735Z PASS commands=1/1 executed=1 deduped=0 elapsed=8859ms tests=1ebbb296b715 head=65fd35c9237b dirty=1
  - task-result-verbatim PASS exit=0

## TASK-009: Connector 入站循环故障隔离

- **Status**: done
- **Depends**: TASK-002
- **Source**: 2026-11-20 实测反馈（企微发消息无回复）
- **Scenarios**: im-attachment-integrity:S-01, im-attachment-integrity:S-05
- **Spec-Refs**: test/harness-test#RULE-test-001
- **Paths**: apps/im-connector/src/muad_im_connector/runtime.py, tests/im_connector/test_runtime_inbound.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "connector-inbound-loop",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/im_connector/test_runtime_inbound.py", "tests/im_core/test_inbound_batch.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

修复实测中的企微“发消息没回”：

- **根因**：`_batch_frame` 声明为 `async def`，但 `_publish_inbound` 直接 `frame = self._batch_frame(message)`，把 coroutine 当 `BatchFrame` 使用；首条命令消息在 `flush_route` 触发 `AttributeError: 'coroutine' object has no attribute 'route'`，`connector.inbound` 任务退出，后续消息滞留在队列里。
- 将 `_batch_frame` 改为同步函数（无 `await` 逻辑），从源头消除 coroutine 泄漏。
- `_inbound_loop` 对单条消息处理加故障隔离：记录 message_id/bot_id + 异常类型并继续消费，不能因为一条坏消息永久停掉整个入站循环。
- 回归覆盖：命令消息直接发布、普通文本进入批次后发布、单条消息抛异常后下一条仍被处理。

### Checklist

- [x] `_batch_frame` 返回 `BatchFrame`，不再返回 coroutine。
- [x] 命令消息不经过批次也能发布。
- [x] 普通文本经批次窗口后发布。
- [x] 单条消息异常只记日志/指标，不终止 `connector.inbound` 循环。
- [x] 既有批次/发布测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T14:02:07.393Z] started tests=c6dbac996e09

### Verification

- 尚未执行
- 2026-11-20T14:02:57.162Z PASS commands=1/1 executed=1 deduped=0 elapsed=3253ms tests=c6dbac996e09 head=aebc2ebf41ed dirty=1
  - connector-inbound-loop PASS exit=0
