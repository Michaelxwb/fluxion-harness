---
{"version":1,"requirement":"webchat-conversation-delete-and-attachment-formats"}
---

# Tasks: webchat-conversation-delete-and-attachment-formats

## TASK-001: 附件格式单一事实与网络门控收口

- **Status**: done
- **Depends**:
- **Source**: webchat-conversation-delete-and-attachment-formats.backend.design.md#32-架构设计, #321-共同能力收口本次的硬要求, #34-接口设计
- **Scenarios**: S-04, E-03
- **Spec-Refs**: im/harness-im#RULE-im-002, test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/attachments.py, packages/contracts/src/muad_contracts/__init__.py, apps/im-gateway/src/muad_im_gateway/application/attachment_gate.py, apps/im-gateway/src/muad_im_gateway/application/webchat_uploads.py, apps/im-connector/src/muad_im_connector/channels/wecom/media.py, docs/configuration-inventory.csv, tests/gateway/**, tests/test_attachment_contract.py, tests/test_configuration_inventory.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "gateway-attachment-gate",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/gateway/test_attachment_gate.py", "tests/gateway/test_capacity_webchat_upload_gateway.py", "tests/gateway/test_inbound_attachment_flow.py", "tests/test_attachment_contract.py", "tests/test_configuration_inventory.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

新增 `muad_contracts.attachments`，作为「允许哪些附件格式」的唯一事实：

- `ALLOWED_ATTACHMENT_MEDIA_TYPES`（含 `application/zip` 与 `application/x-zip-compressed`）；
- `SUPPORTED_UPLOAD_FORMATS`（`media_type` + 展示扩展名，供投影使用）；
- `TEXT_MEDIA_TYPES`、`ARCHIVE_MEDIA_TYPES` 与 `is_text_media_type()` / `is_archive_media_type()`。

网关 `attachment_gate` 删除本地 `ALLOWED_MEDIA_TYPES` 常量，改为引用 contracts；`webchat_uploads._allowed_media_type` 同步。上传 zip 返回 `kind=DOCUMENT`，未登记类型仍 400 `ATTACHMENT_TYPE_NOT_ALLOWED`。新增的大写模块常量登记进配置清单。

### Checklist

- [x] `muad_contracts.attachments` 定义允许集合、展示清单与分类谓词，并在 `__init__` 导出。
- [x] 网关门控与 WebChat 上传引用 contracts，本地不再持有第二份白名单。
- [x] zip 两种 MIME 通过门控；可执行内容与未登记类型仍被拒。
- [x] 白名单与展示清单一一对应（契约测试钉死，防止只改一边）。
- [x] 企微入站适配器的扩展名→MIME 表改为从契约派生（不再有第三份副本），`.zip` 解析为 `application/zip`。
- [x] 新增常量写入 `docs/configuration-inventory.csv` 且清单测试通过。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "im/harness-im",
    "rule": "附件格式清单是单一事实：允许的媒体类型、展示扩展名与压缩包/文本分类谓词只在 `muad_contracts.attachments` 定义；网关门控、Runtime 抽取与客户端配置投影共同引用，任何一方不得另立一份。zip 以 `application/zip` 与 `application/x-zip-compressed` 准入，未登记类型仍显式拒绝。",
    "evidence": "放宽 zip 前，白名单只存在于网关常量，Runtime 抽取器另有能力分支，两侧已无法互证；格式清单必须可被投影给前端并随代码变化同步。"
  }
]
```

### Log

- [2026-11-12] created
- [2026-11-12T14:54:07.951Z] started tests=eeb7a94e14dc

### Verification

- 尚未执行
- 2026-11-12T15:06:05.267Z FAIL commands=0/1 executed=1 deduped=0 elapsed=11427ms tests=eeb7a94e14dc head=9e3d0534dfaa dirty=1
  - gateway-attachment-gate FAIL exit=1
- 2026-11-12T15:06:46.913Z FAIL commands=0/1 executed=1 deduped=0 elapsed=7970ms tests=eeb7a94e14dc head=9e3d0534dfaa dirty=1
  - gateway-attachment-gate FAIL exit=1
- 2026-11-12T15:07:47.577Z FAIL commands=0/1 executed=1 deduped=0 elapsed=8087ms tests=eeb7a94e14dc head=9e3d0534dfaa dirty=1
  - gateway-attachment-gate FAIL exit=1
- 2026-11-12T15:08:15.869Z PASS commands=1/1 executed=1 deduped=0 elapsed=8129ms tests=eeb7a94e14dc head=9e3d0534dfaa dirty=1
  - gateway-attachment-gate PASS exit=0
## TASK-002: Runtime 受限压缩包读取

- **Status**: done
- **Depends**: TASK-001
- **Source**: webchat-conversation-delete-and-attachment-formats.backend.design.md#34-接口设计, #35-质量实现方案
- **Scenarios**: S-04, E-02, E-03, B-02, B-03
- **Spec-Refs**: im/harness-im#RULE-im-002, snapshot/harness-snapshot#RULE-snapshot-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/attachments/**, docs/configuration-inventory.csv, tests/agent_runtime/test_attachment_tools.py, tests/agent_runtime/test_archive_tools.py, tests/test_configuration_inventory.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-attachment-read",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_attachment_tools.py", "tests/agent_runtime/test_archive_tools.py", "tests/test_configuration_inventory.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

把 `read_attachment` 的抽取扩展到压缩包，不新增工具、不改分段语义：

- 新增 `attachments/member_paths.py`，把成员路径规范化（NFC、拒绝绝对路径/`..`/反斜杠/控制与格式字符）收成一处，`create_archive` 与读取共用；
- 新增 `attachments/archive_reader.py`：内存内读取 zip，先产出成员清单（名称/原始大小/是否可读为文本），再拼接文本成员正文；
- `tools._extract_document` 按 contracts 的分类谓词分派：压缩包走 reader，文本类型沿用现状；
- 加密包/损坏包 → `ATTACHMENT_EXTRACT_FAILED`；成员数、单成员字节、解压总量、压缩比超限 → `ATTACHMENT_TOO_LARGE`，在读取途中中止；
- 非文本成员只进清单，不输出内容、不产生图像块。

### Checklist

- [x] 成员路径规范化只有一处实现，打包与读取共用，规则一致。
- [x] zip 读取返回「清单 + 文本正文」，可被 `offset/limit` 分段与 `search_attachment` 检索。
- [x] 加密、损坏、超限、嵌套、二进制成员各有显式结果（拒绝或标注），不含糊。
- [x] 解压全程不写盘。
- [x] 新增上限常量登记进配置清单。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "im/harness-im",
    "rule": "压缩包读取有界且只投影文本：内存解压不落盘；成员清单先行，文本成员可分段读取，二进制成员只列不读；加密、损坏、成员路径不合法与超出成员数/单成员/解压总量/压缩比上限都必须显式拒绝并说明原因，禁止先解完再判。",
    "evidence": "zip 准入后 Runtime 必须能安全读取；无上限的解压是典型资源耗尽面，静默跳过成员会让模型编造内容。"
  }
]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:11:27.341Z] started tests=0cee8bd5e824

### Verification

- 尚未执行
- 2026-11-12T15:11:42.545Z FAIL commands=0/1 executed=1 deduped=0 elapsed=10351ms tests=0cee8bd5e824 head=7373f6537fae dirty=1
  - runtime-attachment-read FAIL exit=1
- 2026-11-12T15:12:05.514Z PASS commands=1/1 executed=1 deduped=0 elapsed=6950ms tests=0cee8bd5e824 head=7373f6537fae dirty=1
  - runtime-attachment-read PASS exit=0
## TASK-003: 客户端配置投影支持格式清单

- **Status**: done
- **Depends**: TASK-001
- **Source**: webchat-conversation-delete-and-attachment-formats.backend.design.md#34-接口设计
- **Scenarios**: S-05
- **Spec-Refs**: api/harness-api#RULE-api-001, im/harness-im#RULE-im-002, test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/webchat.py, apps/im-gateway/src/muad_im_gateway/application/webchat_config.py, tests/gateway/test_capacity_webchat_client_config_api.py, tests/contracts/test_capacity_webchat_webchat_schema.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "client-config-projection",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/gateway/test_capacity_webchat_client_config_api.py", "tests/contracts/test_capacity_webchat_webchat_schema.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在 `WebchatClientLimits` 增加 `upload_formats`（`AttachmentFormat` 列表：`media_type` + `extensions`），值来自 TASK-001 的单一事实；`WebchatClientConfig.from_settings` 负责投影。投影只含浏览器需要的键，不暴露内部路径或存储信息。

### Checklist

- [x] `AttachmentFormat` 与 `upload_formats` 进入契约并在配置投影中赋值。
- [x] 投影内容与白名单一致（契约测试断言两者集合相等）。
- [x] 缺值/空值不会被静默补默认。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:10:10.254Z] started tests=62a0d8db1ab0

### Verification

- 尚未执行
- 2026-11-12T15:10:18.967Z FAIL commands=0/1 executed=1 deduped=0 elapsed=8476ms tests=62a0d8db1ab0 head=7373f6537fae dirty=1
  - client-config-projection FAIL exit=1
- 2026-11-12T15:10:34.109Z FAIL commands=0/1 executed=1 deduped=0 elapsed=4743ms tests=62a0d8db1ab0 head=7373f6537fae dirty=1
  - client-config-projection FAIL exit=1
- 2026-11-12T15:10:44.565Z PASS commands=1/1 executed=1 deduped=0 elapsed=6665ms tests=62a0d8db1ab0 head=7373f6537fae dirty=1
  - client-config-projection PASS exit=0
## TASK-004: 前端支持格式提示与选择器过滤

- **Status**: done
- **Depends**: TASK-003
- **Source**: webchat-conversation-delete-and-attachment-formats.frontend.design.md#33-数据与接口, #36-词条清单
- **Scenarios**: S-05
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, i18n/harness-i18n#RULE-i18n-001, ui/harness-ui#RULE-ui-001, test/harness-test#RULE-test-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/clientConfig.ts, apps/console-platform/frontend/src/modules/webchat/types.ts, apps/console-platform/frontend/src/modules/webchat/components/ChatComposer.tsx, apps/console-platform/frontend/src/modules/webchat/hooks/usePendingSend.ts, apps/console-platform/frontend/src/locales/**, tests/frontend/test_capacity_webchat_client_config_state.py, tests/frontend/test_capacity_webchat_composer_uploads.py, tests/frontend/test_capacity_webchat_webchat_i18n.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "frontend-format-hints",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_capacity_webchat_client_config_state.py", "tests/frontend/test_capacity_webchat_composer_uploads.py", "tests/frontend/test_capacity_webchat_webchat_i18n.py", "tests/frontend/test_capacity_webchat_webchat_services.py", "tests/frontend/test_capacity_webchat_webchat_layout.py", "tests/frontend/test_capacity_webchat_history_sidebar.py", "tests/frontend/test_capacity_webchat_run_actions_ui.py", "tests/frontend/test_capacity_webchat_chat_auth_state.py", "tests/frontend/test_capacity_webchat_conversation_state.py", "tests/frontend/test_capacity_webchat_message_history_ui.py", "tests/frontend/test_capacity_webchat_browser_sse_parser.py", "tests/frontend/test_capacity_webchat_frontend_wiring.py", "tests/frontend/test_capacity_webchat_chat_route.py", "tests/frontend/test_capacity_webchat_activity_ui.py", "tests/frontend/test_capacity_webchat_reply_stream_state.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

`parseClientConfig` 严格解析 `upload_formats`（非空数组、`media_type` 非空、扩展名以 `.` 开头、无重复），非法即 `CLIENT_CONFIG_INVALID`；新增纯函数 `acceptedExtensions(limits)` 作为提示与 `accept` 的唯一派生点。Composer 展示 `chat.composer.hint.formats`，并把 `accept` 设为同一扩展名列表。`addFiles` 在文件带扩展名且不在清单内时立即以 `ATTACHMENT_TYPE_NOT_ALLOWED` 拒绝该文件（其余继续处理）；无扩展名的文件放行，交服务端门控。词条 zh-CN/en-US 齐备。

### Checklist

- [x] `WebchatClientLimits` 类型包含 `upload_formats`，解析失败显式报配置非法。
- [x] `acceptedExtensions` 去重且保持投影顺序，提示与 `accept` 共用。
- [x] 选择不支持类型时附件不进入待发送列表，并有明确错误码。
- [x] 提示文案双语齐备且格式清单随投影变化。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "frontend/harness-frontend",
    "rule": "前端展示与文件选择器使用的「允许上传格式」必须来自客户端配置投影，禁止在前端硬编码格式清单；投影字段缺失或非法时按配置非法显式失败，不回落猜测值。",
    "evidence": "格式白名单已收口到契约并随配置投影下发；前端再存一份必然漂移，用户会看到与实际门控不一致的提示。"
  }
]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:13:46.035Z] started tests=ad145362efef

### Verification

- 尚未执行
- 2026-11-12T15:17:59.451Z FAIL commands=0/1 executed=1 deduped=0 elapsed=3336ms tests=6ec41064170f head=49af04388165 dirty=1 tests-changed
  - frontend-format-hints FAIL exit=1
- 2026-11-12T15:18:07.099Z FAIL commands=0/1 executed=1 deduped=0 elapsed=1171ms tests=6ec41064170f head=49af04388165 dirty=1 tests-changed
  - frontend-format-hints FAIL exit=1
- 2026-11-12T15:18:20.692Z PASS commands=1/1 executed=1 deduped=0 elapsed=1159ms tests=6ec41064170f head=49af04388165 dirty=1 tests-changed
  - frontend-format-hints PASS exit=0
## TASK-005: Runtime 会话删除编排与通知收口

- **Status**: done
- **Depends**:
- **Source**: webchat-conversation-delete-and-attachment-formats.backend.design.md#33-数据设计, #34-接口设计, #35-质量实现方案
- **Scenarios**: S-01, S-02, S-03, E-01, B-01
- **Spec-Refs**: im/harness-im#RULE-im-002, persistence/harness-data#RULE-data-001, api/harness-api#RULE-api-001, snapshot/harness-snapshot#RULE-snapshot-001, test/harness-test#RULE-test-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/conversation_deletion.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_notifications.py, apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py, tests/agent_runtime/test_capacity_webchat_conversation_delete.py, tests/agent_runtime/test_capacity_webchat_history_queries.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-conversation-delete",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_conversation_delete.py", "tests/agent_runtime/test_capacity_webchat_history_queries.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

新增 `ConversationDeletionService`（Session + 取消端口）与内部端点 `DELETE /internal/webchat/conversations/{id}?user_id=`：

1. 按 (tenant, user, conversation_id) 核属，**包含已软删行**（幂等分支）；
2. 有活跃 Run 时复用 `RunService.cancel_run`（`RUN_BUSY` 视为已终态继续）；
3. 软删 `conversation`、指向它的 `channel_conversation` 指针、该会话的 `channel_notification`。

通知写入侧收口：`_entry_id_of` 增加 `is_deleted=false` 判定，会话已删则不写通知并留可区分的日志。读取侧查询不动。

### Checklist

- [x] 删除成功后列表/详情/消息/续聊/产物下载一律不可见。
- [x] 重复删除返回成功且不再取消任何 Run。
- [x] 他人/不存在/无入口归属返回 404 同形错误。
- [x] 活跃 Run 先取消再软删；取消提交不与软删混在同一未提交事务。
- [x] 该会话历史通知被软删；收尾 Run 的终态通知不写入。
- [x] 未读数与其它会话的通知不受影响。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "im/harness-im",
    "rule": "会话删除是 Owner 侧的软删：置 `is_deleted` 即可让列表、详情、消息、续聊与产物下载一致不可见；删除请求内先按既有取消语义处理该会话的活跃 Run 再软删，不等 Run 终态；删除幂等，重复删除返回成功；删除同时软删该会话的通知，且通知写入侧对已软删会话不再产出入箱行。",
    "evidence": "用户需要主动清理历史；软删是既有全仓口径，通知是独立表不会随会话消失，且收尾中的 Run 仍可能写入终态通知。"
  }
]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:02:56.091Z] started tests=4c6f10226e94

### Verification

- 尚未执行
- 2026-11-12T15:06:43.723Z FAIL commands=0/1 executed=1 deduped=0 elapsed=7767ms tests=4c6f10226e94 head=9e3d0534dfaa dirty=1
  - runtime-conversation-delete FAIL exit=1
- 2026-11-12T15:06:58.731Z PASS commands=1/1 executed=1 deduped=0 elapsed=7367ms tests=4c6f10226e94 head=9e3d0534dfaa dirty=1
  - runtime-conversation-delete PASS exit=0
## TASK-006: Gateway 会话删除公开路径

- **Status**: done
- **Depends**: TASK-005
- **Source**: webchat-conversation-delete-and-attachment-formats.backend.design.md#34-接口设计
- **Scenarios**: S-01, E-01, B-01
- **Spec-Refs**: api/harness-api#RULE-api-001, auth/harness-auth#RULE-auth-001, im/harness-im#RULE-im-002, test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/webchat.py, apps/im-gateway/src/muad_im_gateway/api/webchat.py, apps/im-gateway/src/muad_im_gateway/application/webchat_service.py, apps/im-gateway/src/muad_im_gateway/application/runtime_client.py, tests/gateway/test_capacity_webchat_webchat_resource_api.py, tests/gateway/test_capacity_webchat_gateway_wiring.py, tests/contracts/test_capacity_webchat_webchat_schema.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "gateway-conversation-delete",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/gateway/test_capacity_webchat_webchat_resource_api.py", "tests/gateway/test_capacity_webchat_gateway_wiring.py", "tests/contracts/test_capacity_webchat_webchat_schema.py", "tests/test_configuration_inventory.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

新增 `WebchatConversationDeletion` 契约、`WebchatOwnerPort.delete_conversation`、`RuntimeClient.delete_conversation` 与公开路由 `DELETE /api/v1/webchat/conversations/{conversation_id}`。网关只做 CHAT 主体门控与转发，不自报入口/Agent、不持状态；响应走统一封套。失败沿用 `COMMON_NOT_FOUND`，不新增错误码与文案。

### Checklist

- [x] 契约 `WebchatConversationDeletion` 与内部/公开两端同形。
- [x] 路由经 CHAT 门控后转发，请求只带 user_id 与 conversation_id。
- [x] 幂等重放返回成功；他人会话与不存在会话返回 404。
- [x] 不新增错误码，响应符合统一封套。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:15:18.630Z] started tests=5995641b8d98
- [2026-11-12T15:16:35.721Z] reopen: declare configuration inventory check

### Verification

- 尚未执行
- 2026-11-12T15:15:33.574Z FAIL commands=0/1 executed=1 deduped=0 elapsed=14699ms tests=5995641b8d98 head=49af04388165 dirty=1
  - gateway-conversation-delete FAIL exit=1
- 2026-11-12T15:15:58.393Z PASS commands=1/1 executed=1 deduped=0 elapsed=12164ms tests=5995641b8d98 head=49af04388165 dirty=1
  - gateway-conversation-delete PASS exit=0
- 2026-11-12T15:16:48.940Z PASS commands=1/1 executed=1 deduped=0 elapsed=13061ms tests=16344ac99064 head=49af04388165 dirty=1 tests-changed
  - gateway-conversation-delete PASS exit=0
## TASK-007: 前端删除入口与状态收敛

- **Status**: done
- **Depends**: TASK-006
- **Source**: webchat-conversation-delete-and-attachment-formats.frontend.design.md#32-组件与状态, #34-交互与可访问性, #36-词条清单
- **Scenarios**: S-01, S-02, S-03, E-04, B-01
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, i18n/harness-i18n#RULE-i18n-001, ui/harness-ui#RULE-ui-001, test/harness-test#RULE-test-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/conversationState.ts, apps/console-platform/frontend/src/modules/webchat/hooks/useConversations.ts, apps/console-platform/frontend/src/modules/webchat/components/ConversationSidebar.tsx, apps/console-platform/frontend/src/modules/webchat/ChatPage.tsx, apps/console-platform/frontend/src/modules/webchat/services/webchatApi.ts, apps/console-platform/frontend/src/modules/webchat/types.ts, apps/console-platform/frontend/src/locales/**, tests/frontend/test_capacity_webchat_conversation_state.py, tests/frontend/test_capacity_webchat_history_sidebar.py, tests/frontend/test_capacity_webchat_webchat_i18n.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "frontend-conversation-delete",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_capacity_webchat_conversation_state.py", "tests/frontend/test_capacity_webchat_history_sidebar.py", "tests/frontend/test_capacity_webchat_webchat_i18n.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

会话状态控制器增加 `remove(conversationId)`：单飞、成功移除条目并清理该会话的草稿/游标/滚动锚点/历史缓存，若为当前选中则 `select(null)`（不自动新建）；失败保留条目并记录 `deleteErrorCode`。侧栏条目改为「选择按钮 + 操作区」，操作区用 Semi `Dropdown` + 受控 `Modal` 做二次确认，删除中条目禁用并显示进度；确认文案与 aria 走词条。ChatPage 接线，并在被删会话正是 URL `conversation` 参数时以 `replace` 去掉该参数。

### Checklist

- [x] `remove` 成功/失败/单飞/身份边界四种路径都有确定快照。
- [x] 删除当前会话回到空态且不新建；其它会话与进行中 Run 不受影响。
- [x] 条目结构改动保留既有 data-testid 与 aria 语义。
- [x] 确认弹窗可键盘操作，删除后焦点回到可用控件。
- [x] 全部新增词条双语齐备。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:29:43.035Z] started tests=1f30188b75de

### Verification

- 尚未执行
- 2026-11-12T15:29:44.948Z PASS commands=1/1 executed=1 deduped=0 elapsed=1796ms tests=1f30188b75de head=4f73dcd8c888 dirty=1
  - frontend-conversation-delete PASS exit=0
## TASK-008: 浏览器端到端验收与规范同步

- **Status**: done
- **Depends**: TASK-002, TASK-004, TASK-007
- **Source**: webchat-conversation-delete-and-attachment-formats.backend.design.md#35-质量实现方案, webchat-conversation-delete-and-attachment-formats.frontend.design.md#37-测试建议
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, E-01, E-02, E-04, B-01, B-02
- **Spec-Refs**: test/harness-test#RULE-test-001, e2e/harness-e2e#RULE-e2e-001, im/harness-im#RULE-im-002, frontend/harness-frontend#RULE-front-001
- **Paths**: e2e/tests/webchat/**, e2e/support/**, e2e/playwright.webchat-e2e.config.ts, e2e/playwright.webchat-regression.config.ts, tests/acceptance/webchat/**
- **E2E**: ci:e2e
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "webchat-e2e-contract",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_capacity_webchat_browser_harness.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在真实构建产物与真实浏览器上补三条关键链路：删除普通会话（含刷新后不可见）、删除执行中的会话（确认后立即返回且通知收口）、上传 zip 并被 Agent 读取；加上输入区格式提示与选择器过滤。按该域既有端口偏移与隔离数据存储约定运行，不 mock 业务 API。

### Checklist

- [x] 删除场景覆盖「删除 → 列表消失 → 刷新仍不可见」。
- [x] 删除执行中会话不阻塞用户、通知收口可见。
- [x] zip 上传成功并以 DOCUMENT 收下（界面可选、提示含 ZIP）；**包内读取**由 Runtime 读取器单测覆盖——脚本化模型探针观察不到工具结果，E2E 不据此伪造结论。
- [x] 格式提示与文件选择器过滤在真实浏览器可见。
- [x] 场景编号（S-115/S-116/S-117）与 e2e/regression 两份配置的 grep 互补登记，端口/时区/隔离沿用该域约定。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created
- [2026-11-12T15:32:14.082Z] started tests=786322a8e988

### Verification

- 尚未执行
- 2026-11-12T15:32:16.045Z PASS commands=1/1 executed=1 deduped=0 elapsed=1469ms tests=786322a8e988 head=cd33c64d9297 dirty=1
  - webchat-e2e-contract PASS exit=0