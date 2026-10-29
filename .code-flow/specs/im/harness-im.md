---
id: harness-im
description: IM 网关、通道与 WebChat
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-im-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_channel
    - tests/gateway
    cwd: .
    timeout: 600
- rule: RULE-im-002
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/architecture/test_channel_neutrality.py
    cwd: .
    timeout: 300
---

# harness-im

## Rules

- [RULE-im-001] 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。
- [RULE-im-002] **渠道差异只活在适配器层**：核心域（`agent-runtime` / `agent-worker`）零渠道专有字样与取件形状；`im-gateway` 的 web 角色除 `channels/` 与装配根 `main.py` 外同样为零；`im-connector` 的协议/回复/媒体实现属于通道适配器表面，不得把渠道形状带回核心域。取件、下载、解密、媒体 URL/密钥**一律不出适配器**——上层只看到「已解密字节 + 元信息」。机检见 `tests/architecture/test_channel_neutrality.py`（三条：核心域全文零字样 / 核心域不得给 `ResolveDefinitionRequest.channel` 传字面量 / 全仓代码扫描只允许出现在 `ALLOWED_SURFACES`）。

## Conventions

**记忆命令与确认事件**：

- **记忆命令只在 Gateway 分类，权威意图由 Runtime 验证**：`/memory` 使用独立命令容量、不等待普通流 route 锁；完整 message/身份仍原样送 Runtime，RUNNING/WAITING_TOOL 返回 `RUN_BUSY`，WAITING_INPUT 只走合法 resume。✅ Runtime 把当前 canonical `USER_MESSAGE` 的 `/memory remember ...` 与工具全部参数精确比对后，经同一事务 writer 保存 USER；❌ Gateway 解析 value、模型自报来源或只匹配部分参数即授权。
- **待确认事件完整投递到本条 ReplySession**：SSE 白名单、StreamRenderer 与 Progress 都识别 Runtime 持久化的 `memory.confirmation_required`，直接显示完整内容/候选编号并结束本条等待输入的回复；重放仍读 canonical 事件。✅ 该事件停止执行计时；❌ 当未知帧丢弃再把 EOF 当断流，或把确认结果写回旧回复会话。续接 options 由 Runtime 的 `message.delta` 原文投递，Gateway 不重新编号。

**入站、绑定与幂等**：

- **入站按 `message_id` 去重，且只有「已受理」才落终值**：键 `im:dedupe:{channel}:{message_id}`、TTL 600s。入口先 `reserve` 占位（值带所有者 token），**受理之后**才升级终值；受理前的依赖故障（设置拉取/授权解析/附件落盘/Runtime 提交被拒）一律**释放占位**让平台重投。受理后的重复由下游幂等键兜底（`message_id` 作 `Idempotency-Key` 透传）。去重存储不可用时降级 at-least-once（`NullDedupeStore` 放行 + 告警），不阻断投递（`application/inbound.py`、`infrastructure/dedupe.py`）。
- **Bot 快照按固定节拍轮询并整批发布**：默认 30s、分页上限 100；跨页 `revision`/`total` 不一致、页数超上限、实收条目数 ≠ `total` 三种不完整读取都整批丢弃、保留旧快照；拉取失败保留最近一次完整快照。**适配器应用成功后才认定 revision 生效**（回调抛错保留待应用状态、下一轮重试）。共享实现在 `packages/im-core/src/muad_im_core/snapshot.py` 的 `BotSnapshotCache`。
- **投递确认语义 `reserve` → 发送 → `mark`，且占位「有主」**：`reserve` 是 in-flight 占位（TTL 30s），只有 `mark` 的送达标记（TTL 604800s）代表成功；占位冲突最多等 2s，超时回可重试 500；发送失败释放占位。占位值带 owner token，释放/升级/续租都是「比较值再动作」（Lua 脚本）；长发送按 `DELIVERY_IN_FLIGHT_RENEW_SEC` 续租。✅ 成功键存首次交付结果（`outcome`/`fallback_url`）供重放；❌ 把「占位存在」当「已送达」，或无条件 `DEL` 误删新主人的标记。
- **幂等指纹含 `endpoint` 与 `tenant_id`，但不含正文文案**：Worker 每次尝试按当前 locale 重渲染正文，把文案算进指纹会让无害重试变成不可重试的 409（`RULE-api-002`）。
- **`/internal/deliveries` 是受信内部端点，产物归属由网关自己核**：要求 `X-Internal-Service`；投递 `tenant_id` 必须等于本网关部署租户；产物形态必须按 `artifact_id` 经 Runtime `/internal/artifacts/{id}` 在本租户内解析出权威引用，调用方 `storage_key` 只用于比对。❌ 相信调用方 `storage_key` 直接读共享存储。
- **通道注册由 contracts/registry 驱动**：`ChannelName` 是开放 string，内建名只在 `packages/contracts/src/muad_contracts/enums.py` 声明一次（当前 `("WECOM","WEBCHAT")`），路由由 `ChannelRegistry` 的适配器名决定；新增通道必须同步内建名/适配器/DB 列/能力清单，不能只加 adapter。重名直接抛 `DuplicateChannelAdapterError`（`channels/base.py`；Gateway 装配根 `channels/registry.py`，Connector 适配器在 `apps/im-connector/.../channels/`）。
- **绑定码一次性 + 绑定请求幂等**：`USED`/`REVOKED` → `BIND_CODE_INVALID`，`EXPIRED`/已过期 → `BIND_CODE_EXPIRED`；带 `Idempotency-Key` 时用共享幂等表 + `pg_advisory_xact_lock`；`identity_key = (channel, bot_id, external_user_id)` 每租户唯一，身份已属于其他平台用户时抛 `IDENTITY_ALREADY_BOUND` 且不消费绑定码（Console `application/channel_service.py`）。
- **`bot_id` 跨租户全局唯一**：`uq_bot_account_bot_id` 只含 `bot_id`，冲突统一 `BOT_ID_EXISTS`（`application/channel_admin_service.py`）。A 租户占用的 `bot_id` 会让 B 租户创建失败，这是有意的全局唯一（一个物理 bot 只路由一个 Agent）。
- **解析顺序固定，错误码不泄露跨租户存在性**：先向 Console `resolve`（未知/禁用/已删/跨租户 bot 一律 `BOT_NOT_FOUND`），再判未绑定 `UNBOUND`、未授权 `NO_PERMISSION`（`application/channel_service.py`、Gateway `application/inbound.py`）。机检：`tests/console_channel/test_channel_resolve_api.py::test_tenant_isolation_hides_other_tenant_bot`。
- **入站消费读循环永不阻塞**：非命令消息按 route 串行（等待在任务内部）、在飞处理由信号量封顶、命令走独立容量；排队上限分全局与每路由两级，队满显式拒绝（`CHANNEL_BUSY` + 指标 + 日志）。✅ `/stop` 在同路由积压时仍即时可用；❌ 把「排队等锁」算进活跃名额。

**统一内核、WebChat 与 Connector**：

- **统一消息内核在 `packages/im-core`，Gateway/Connector 只做适配**：`MessageGateway.admit_sync` / `admit_async` 是两条受理路径；`ChannelAdapter` / `InboundPublisher` / `BotSnapshotCache` / stream keys 共用同一实现。新增通道只实现 SPI，不在应用层按通道分叉。
- **WebChat 是 SYNC 通道，不走队列也不回源解析身份**：网页提交在请求内等 Runtime 受理裁决，返回 202 + 受理投影；`SyncPrincipal` 来自 Console 已核验的 CHAT 主体与入口解析，Gateway 不再回调 Console 解析身份（WEBCHAT 的 `iter_events()` 刻意为空）。✅ 只把渠道中立事件映射成 `RunRequest`；❌ 另建 webchat 身份解析，或把 SYNC 提交塞进 ASYNC 队列。
- **会话数量与保留期是平台设置**：单个 `(tenant,user,agent,入口)` 活跃会话上限默认 50，达限自动归档最旧且无活跃 Run 的会话；保留期默认 30 天，`0` 表示永不过期；超期历史与续聊统一 `CONVERSATION_EXPIRED`（`contracts.platform_settings.WebchatSettings`、`application/conversation_policy.py`）。
- **WebChat 历史消息时间与入口表单**：`WebchatHistoryMessage.created_at` 取 canonical 事件落库时间（`public_events` 的 `row.create_time`），前端只展示不推算；IM 接入表单支持 WEBCHAT，网页入口 `bot_id` 是入口键、不要求/不提交 Secret，编辑已有入口时通道不可改；入口列表展示同源 `/chat/<channel_account_id>` 链接。✅ 时间来自事件、WEBCHAT 无 secret；❌ 用浏览器时钟补时间、网页入口提交 secret 或允许改通道（`test_agent_module_contract.py`、`test_capacity_webchat_message_history_ui.py`）。
- **每用户通知 SSE 只承载提交后的信号**：Gateway 每 Pod 一条 Redis pubsub，按 tenant/user 路由；通知事实在 Runtime 的 PG，断流按持久 seq 回填；队列满表示慢订阅，流显式结束让客户端带 `Last-Event-ID` 重连（`application/notification_streams.py`）。
- **SSE 订阅有进程级上限**：`GATEWAY_MAX_SSE_SUBSCRIBERS` 到界抛 `RUN_CAPACITY_EXCEEDED`（不排队、不静默丢）；缓冲记账在 `SseBufferLedger`（`application/webchat_streams.py`）。
- **Connector 只持有 bot 传输所有权，租约带单调 fencing epoch**：同一 bot 任意时刻只有一个 Connector 建连；epoch 独立计数、过期不删除，旧 owner 的 token 永远小于新 owner；续租用 owner+epoch 比较，失配立即停连接；时间取 Redis 服务端 TIME（`apps/im-connector/src/muad_im_connector/lease.py`）。
- **入站流是 at-least-once，去重标记必须早于 XADD**：先写 Redis 去重/崩溃标记再发布；收到后、发布前崩溃停在 `RECEIVED`，平台重投可再发布，不重投则扫描置 `LOST` 并记 ERROR/指标——不假装成功，也不补造 Run；`PUBLISHED` 的重复回调直接跳过（`packages/im-core/src/muad_im_core/ingress.py`）。

**入站附件**：

- **取件是适配器的可选能力，不是核心域接口**：适配器实现 `AttachmentSource`（`attachment_count` + `fetch_attachment`），核心域只给 envelope/下标/上限，拿回已解密字节 + MIME + 文件名 + checksum；`url`/`aes_key`/`media_id` 一步都不出去。新通道实现这两个方法即可，预检/取件/实检/落盘/反馈/审计一行不用改（对照用例 `tests/gateway/test_inbound_attachment_flow.py`）。❌ 应用层写 `if channel == "WECOM"` 或直接摸适配器凭据。
- **门控分两段：预检判数量 → 取件 → 实检判类型与大小**：渠道回调常不带 `size`/MIME/文件名，"下载前按类型大小门控"物理上不可实现（`application/attachment_gate.py` 的 `evaluate_precheck` / `evaluate_gate`）。部分拒绝不拖累其余；单文件超限在流式下载途中中止。**尺寸口径只有明文**：流式读取允许 `ENCRYPTION_OVERHEAD_BYTES` 固定增量，解密后再按明文严格判一次（`channels/wecom/media.py`）。
- **原因码是渠道中立词汇表，适配器负责翻译**：`ATTACHMENT_TOO_LARGE` / `FETCH_FAILED` / `FETCH_TIMEOUT` / `DECRYPT_FAILED` 定义在 `channels/base.py`，适配器把私有异常翻成 `AttachmentFetchError(code)`；应用层取文案 + 写审计，不 import 渠道异常。文案经消息目录，**上限数值只有一个来源**（`application/inbound.py` 的 `_feedback_text`）。
- **入口护栏判「有没有内容」必须问适配器**：`ChannelEnvelope.attachments` 只描述已拿到手的字节，纯媒体消息取件前必然为空；判空条件要含 `unsupported_media` 与待取件数（`_carries_no_payload`）。
- **`AttachmentRef` 一处定义、两个方向共用**：入站只描述已拿到手的字节、不设 `artifact_id`（行由 Runtime 在 Run 建立后写，AD-1-B）；出站必填 `artifact_id`（交付审计幂等键与签名直链都要它）。产物键只由系统生成（`inbound/{token}/{index}`），用户文件名只作元信息；写入原子且只发布一次（临时文件 + `os.link`），同键同内容重放复用、不同内容抛 `AttachmentConflictError`。
- **网关不持库 ⇒ 入站审计经 Console 内网端点写控制面表**：`POST /internal/channel/audit`（服务身份 + `HeaderTenantId`）写 `control.im_inbound_audit`；审计 DTO 字段全枚举、无自由 JSON 列 ⇒ 取件凭据在类型上无处可放；幂等键 `(tenant_id, channel, external_message_id, outcome)` 承载重投。审计抖动不阻断用户请求，但失败必须留 ERROR 日志。

**回复会话与执行状态**：

- **执行事件是事实，动画是渠道能力**：Runner 在真实模型调用前后发 `model.started` / `model.completed`；Gateway 从模型/工具事件推导阶段，经可选 `ReplySessionFactory` 展示；原生动画只由适配器生成。❌ 按计时器伪造思考/执行状态。
- **每条消息拥有独立回复会话，入站那一刻固定**：`open_reply(route, message_id)` 绑定该消息回调，状态全量替换、正文追加、`finish()` 幂等；命令回执、附件回执、错误文案都走本条消息自己的会话。✅ 终态文本按稳定键 `run:{run_id}:terminal` 协调：会话在就确认送达，失联才按同一键主动投递（`application/channel_delivery.py`）。❌ 用「本路由最新回调」回复旧消息或挂产物。
- **计时不创造执行状态**：当前 submission 的 `run.created` 恢复本段起点，monotonic 计秒；等待确认、后台受理、完成/失败/取消/断流停止当前段；后台 Task 沿既有 Worker 投递通知结果。
- **正文由有界计时事件冲出**：读取循环醒来节拍 = `min(计时节拍, 正文合并窗口)`；每个 tick 先 `renderer.flush()` 再更新计时（节拍按 `im.progress_interval_sec` 节流）。✅ 静默 1 秒内正文已出去；❌ 正文只在下一条 delta 到来才可能被冲出。
- **状态发送有界**：默认 5 秒一拍（平台设置 `im.progress_interval_sec`），每机器人共享令牌预算；额度不足跳过当前刷新，不积压 tick。正文与收尾绕过状态预算；状态帧与正文帧共用一条 session 流（任何时刻一个写者）；一字不差的状态帧不重发。❌ 每秒排队等上一拍，或状态帧与正文帧并发写同一条 stream。
- **节拍按客户端重排成本定**：1 秒一拍会让企微对话框滚动发涩，产品口径 5 秒；`已执行 mm:ss` 跳秒变粗是有意取舍。节拍做成平台设置（生产默认由单测钉住，验收栈按租户种设置行），❌ 用环境变量当节拍来源。
- **等待是执行事实，不是终态**：`run.waiting_tool` 只停本段计时，`run.resumed` 开新执行段；等待期不发完成帧、不伪造活动。断流不等于取消，重连按最后确认 `seq` 经 `open_events(after_seq)` 续读，绝不重复 POST 建 Run。`WAITING_TOOL` 不允许显式 human resume（只服务 `WAITING_INPUT`），`/stop` 走独立命令容量。ReplySession 失效走渠道中立的主动投递，无能力或失败必须显式失败、不得回「已送达」。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
