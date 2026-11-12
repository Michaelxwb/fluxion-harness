---
{
  "description": "IM 网关、通道与 WebChat",
  "next_rule": 6
}
---

# harness-im

## Rules

- [RULE-im-001] 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。
- [RULE-im-002] **渠道差异只活在适配器层**：核心域（`agent-runtime` / `agent-worker`）零渠道专有字样与取件形状；`im-gateway` 的 web 角色除 `channels/` 与装配根 `main.py` 外同样为零；`im-connector` 的协议/回复/媒体实现属于通道适配器表面，不得把渠道形状带回核心域。取件、下载、解密、媒体 URL/密钥**一律不出适配器**——上层只看到「已解密字节 + 元信息」。机检见 `tests/architecture/test_channel_neutrality.py`（三条：核心域全文零字样 / 核心域不得给 `ResolveDefinitionRequest.channel` 传字面量 / 全仓代码扫描只允许出现在 `ALLOWED_SURFACES`）。

- [RULE-im-003] 附件格式清单是单一事实：允许的媒体类型、展示扩展名与压缩包/文本分类谓词只在 `muad_contracts.attachments` 定义；网关门控、Runtime 抽取与客户端配置投影共同引用，任何一方不得另立一份。zip 以 `application/zip` 与 `application/x-zip-compressed` 准入，未登记类型仍显式拒绝。

- [RULE-im-004] 压缩包读取有界且只投影文本：内存解压不落盘；成员清单先行，文本成员可分段读取，二进制成员只列不读；加密、损坏、成员路径不合法与超出成员数/单成员/解压总量/压缩比上限都必须显式拒绝并说明原因，禁止先解完再判。

- [RULE-im-005] 会话删除是 Owner 侧的软删：置 `is_deleted` 即可让列表、详情、消息、续聊与产物下载一致不可见；删除请求内先按既有取消语义处理该会话的活跃 Run 再软删，不等 Run 终态；删除幂等，重复删除返回成功；删除同时软删该会话的通知，且通知写入侧对已软删会话不再产出入箱行。

## Conventions

**记忆命令与确认事件**：

- **记忆命令只在 Gateway 分类，权威意图由 Runtime 验证**：`/memory` 使用独立命令容量、不等待普通流 route 锁；完整 message/身份仍原样送 Runtime，RUNNING/WAITING_TOOL 返回 `RUN_BUSY`，WAITING_INPUT 只走合法 resume。✅ Runtime 把当前 canonical `USER_MESSAGE` 的 `/memory remember ...` 与工具全部参数精确比对后，经同一事务 writer 保存 USER；❌ Gateway 解析 value、模型自报来源或只匹配部分参数即授权。
- **待确认事件完整投递到本条 ReplySession**：SSE 白名单、StreamRenderer 与 Progress 都识别 Runtime 持久化的 `memory.confirmation_required`，直接显示完整内容/候选编号并结束本条等待输入的回复；重放仍读 canonical 事件。✅ 该事件停止执行计时；❌ 当未知帧丢弃再把 EOF 当断流，或把确认结果写回旧回复会话。续接 options 由 Runtime 的 `message.delta` 原文投递，Gateway 不重新编号。

**入站、绑定与幂等**：

- **入站按 `message_id` 去重，且只有「已受理」才落终值**：键由去重实现定义、TTL 按实现配置。入口先 `reserve` 占位（值带所有者 token），**受理之后**才升级终值；受理前的依赖故障（设置拉取/授权解析/附件落盘/Runtime 提交被拒）一律**释放占位**让平台重投。受理后的重复由下游幂等键兜底（`message_id` 作 `Idempotency-Key` 透传）。去重存储不可用时降级 at-least-once（`NullDedupeStore` 放行 + 告警），不阻断投递（`application/inbound.py`、`infrastructure/dedupe.py`）。
- **Bot 快照按固定节拍轮询并整批发布**：固定节拍与分页上限由快照实现定义；跨页 `revision`/`total` 不一致、页数超上限、实收条目数 ≠ `total` 三种不完整读取都整批丢弃、保留旧快照；拉取失败保留最近一次完整快照。**适配器应用成功后才认定 revision 生效**（回调抛错保留待应用状态、下一轮重试）。共享实现在 `packages/im-core/src/muad_im_core/snapshot.py` 的 `BotSnapshotCache`。
- **投递确认语义 `reserve` → 发送 → `mark`，且占位「有主」**：`reserve` 是带 TTL 的 in-flight 占位，只有 `mark` 的送达标记代表成功；占位冲突有界等待，超时回可重试 500；发送失败释放占位。占位值带 owner token，释放/升级/续租都是「比较值再动作」（Lua 脚本）；长发送按 `DELIVERY_IN_FLIGHT_RENEW_SEC` 续租。✅ 成功键存首次交付结果（`outcome`/`fallback_url`）供重放；❌ 把「占位存在」当「已送达」，或无条件 `DEL` 误删新主人的标记。
- **幂等指纹含 `endpoint` 与 `tenant_id`，但不含正文文案**：Worker 每次尝试按当前 locale 重渲染正文，把文案算进指纹会让无害重试变成不可重试的 409（`RULE-api-002`）。
- **`/internal/deliveries` 是受信内部端点，产物归属由网关自己核**：要求 `X-Internal-Service`；投递 `tenant_id` 必须等于本网关部署租户；产物形态必须按 `artifact_id` 经 Runtime `/internal/artifacts/{id}` 在本租户内解析出权威引用，调用方 `storage_key` 只用于比对。❌ 相信调用方 `storage_key` 直接读共享存储。
- **通道注册由 contracts/registry 驱动**：`ChannelName` 是开放 string，内建名只在 `packages/contracts/src/muad_contracts/enums.py` 声明一次（当前 `("WECOM","WEBCHAT")`），路由由 `ChannelRegistry` 的适配器名决定；新增通道必须同步内建名/适配器/DB 列/能力清单，不能只加 adapter。重名直接抛 `DuplicateChannelAdapterError`（`channels/base.py`；Gateway 装配根 `channels/registry.py`，Connector 适配器在 `apps/im-connector/.../channels/`）。
- **绑定码一次性 + 绑定请求幂等**：`USED`/`REVOKED` → `BIND_CODE_INVALID`，`EXPIRED`/已过期 → `BIND_CODE_EXPIRED`；带 `Idempotency-Key` 时用共享幂等表 + `pg_advisory_xact_lock`；`identity_key = (channel, bot_id, external_user_id)` 每租户唯一，身份已属于其他平台用户时抛 `IDENTITY_ALREADY_BOUND` 且不消费绑定码（Console `application/channel_service.py`）。
- **`bot_id` 跨租户全局唯一**：`uq_bot_account_bot_id` 只含 `bot_id`，冲突统一 `BOT_ID_EXISTS`（`application/channel_admin_service.py`）。A 租户占用的 `bot_id` 会让 B 租户创建失败，这是有意的全局唯一（一个物理 bot 只路由一个 Agent）。
- **解析顺序固定，错误码不泄露跨租户存在性**：先向 Console `resolve`（未知/禁用/已删/跨租户 bot 一律 `BOT_NOT_FOUND`），再判未绑定 `UNBOUND`、未授权 `NO_PERMISSION`（`application/channel_service.py`、Gateway `application/inbound.py`）。机检：`tests/console_channel/test_channel_resolve_api.py::test_tenant_isolation_hides_other_tenant_bot`。
- **入口身份显式、必填，拒绝字符串形状推断**：`ChannelContext.entry_id`（= `control.bot_account.id`）随消息、`/new`、`/stop` 全链传递，是会话归属、入口指针与终态通知的身份；`bot_id` 只保留平台原值，用于 Connector 连接、租约与出站路由。Console `resolve` 必须返回真实入口 UUID，WebChat 通过 `SyncPrincipal.entry_id` 显式携带。✅ 企微 `bot_id=aibln...`（非 UUID）仍得到正确归属与会话指针；❌ 用 `uuid.UUID(bot_id)` 猜入口、非 UUID 时按 unattributed 解析，或让 WebChat 把入口 UUID 塞进 `bot_id`。
- **终态通知与入口归属是同一事实的三份载体**：`run.channel_json.entry_id`、`conversation.channel_account_id`、`channel_notification.entry_id` 必须一致；有主动投递能力的入口写 `PENDING`，WebChat 写 `NOT_REQUIRED`。✅ 机器人 Run 找不到持久出站意图是内部一致性错误（ERROR + 不降级直发、不确认送达）；❌ 把缺意图当正常情况降级直发或返回已确认。
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
- **Redis 单机/集群双模式，客户端与 key 布局只有一个入口**：所有 Redis 客户端必须经 `muad_common.redis_client` 的工厂构造，业务代码禁止直接实例化单机/集群客户端；集群下同族 key 必须通过 hash tag 落到同一 slot，key 布局由 contracts 统一提供，业务层不得硬编码前缀。集群模式只支持 db 0；集群客户端没有 async pub/sub 能力，通知/唤醒必须使用工厂提供的派生连接。流保留只裁剪已确认条目并保护 pending；任何裁剪或积压触顶都必须通过指标与日志显式可见，不得静默丢弃事件。

**入站附件**：

- **取件是适配器的可选能力，不是核心域接口**：适配器实现 `AttachmentSource`（`attachment_count` + `fetch_attachment`），核心域只给 envelope/下标/上限，拿回已解密字节 + MIME + 文件名 + checksum；`url`/`aes_key`/`media_id` 一步都不出去。新通道实现这两个方法即可，预检/取件/实检/落盘/反馈/审计一行不用改（对照用例 `tests/gateway/test_inbound_attachment_flow.py`）。❌ 应用层写 `if channel == "WECOM"` 或直接摸适配器凭据。
- **门控分两段：预检判数量 → 取件 → 实检判类型与大小**：渠道回调常不带 `size`/MIME/文件名，"下载前按类型大小门控"物理上不可实现（`application/attachment_gate.py` 的 `evaluate_precheck` / `evaluate_gate`）。部分拒绝不拖累其余；单文件超限在流式下载途中中止。**尺寸口径只有明文**：流式读取允许 `ENCRYPTION_OVERHEAD_BYTES` 固定增量，解密后再按明文严格判一次（`channels/wecom/media.py`）。
- **原因码是渠道中立词汇表，适配器负责翻译**：`ATTACHMENT_TOO_LARGE` / `FETCH_FAILED` / `FETCH_TIMEOUT` / `DECRYPT_FAILED` 定义在 `channels/base.py`，适配器把私有异常翻成 `AttachmentFetchError(code)`；应用层取文案 + 写审计，不 import 渠道异常。文案经消息目录，**上限数值只有一个来源**（`application/inbound.py` 的 `_feedback_text`）。
- **入口护栏判「有没有内容」必须问适配器**：`ChannelEnvelope.attachments` 只描述已拿到手的字节，纯媒体消息取件前必然为空；判空条件要含 `unsupported_media` 与待取件数（`_carries_no_payload`）。
- **`AttachmentRef` 一处定义、两个方向共用**：入站只描述已拿到手的字节、不设 `artifact_id`（行由 Runtime 在 Run 建立后写，AD-1-B）；出站必填 `artifact_id`（交付审计幂等键与签名直链都要它）。产物键只由系统生成（`inbound/{token}/{index}`），用户文件名只作元信息；写入原子且只发布一次（临时文件 + `os.link`），同键同内容重放复用、不同内容抛 `AttachmentConflictError`。
- **网关不持库 ⇒ 入站审计经 Console 内网端点写控制面表**：`POST /internal/channel/audit`（服务身份 + `HeaderTenantId`）写 `control.im_inbound_audit`；审计 DTO 字段全枚举、无自由 JSON 列 ⇒ 取件凭据在类型上无处可放；幂等键 `(tenant_id, channel, external_message_id, outcome)` 承载重投。审计抖动不阻断用户请求，但失败必须留 ERROR 日志。

**产物交付（站内入口与外部通道）**：

- **交付方式按入口能力显式选择，未知通道显式拒绝**：外部推送通道（`OUTBOUND_CHANNELS`）走网关投递；站内入口（`LOCAL_STREAM_CHANNELS`，其出站面就是会话自己的持久流）在**目标会话**写一条发布事实后即视为已交付。两组之外通道不猜、不降级。✅ `channel in LOCAL_STREAM_CHANNELS` → 发布；❌ 用「不在 OUTBOUND 里」推断站内入口。
- **发布事实是唯一凭据，载荷就是公开附件 DTO**：发布 = 目标会话内一条 canonical `ARTIFACT_CREATED`（**完整 `WebchatAttachment` 线格式**，含发布那一刻的 `size`/`checksum`）；实时投影与历史读取读同一份。幂等靠 `source_event_id` 的 partial unique（同会话/同 Run/同产物/同版本的重试拿回原 seq），提交后唤醒追尾订阅者，唤醒失败只降级为 PG 补查。✅ 载荷可被网关严格校验通过；❌ 只写 `artifact_id`（实时投影会静默过滤掉）。
- **下载按（目标会话, 产物, 版本）裁决**：跨会话取件要求目标会话里存在该产物**该版本**的发布事实，且源产物与本会话同租户、同用户、同 Agent；字节按 `checksum` 定位发布时的版本（追加写后不得把新内容当成发布的那一份）；版本历史必须保住键/校验和/长度，清理只能随产物行整体清理，不得单独回收仍被发布引用的版本。✅ 同一个 `artifact_id` 的两版是两张卡、各自可下载；❌ 只凭 `artifact_id` 判权或服务"当前内容"。
- **历史里发布事实参加分页**：发布行与可见消息**同一谓词同一水位**查询（`total` 自洽）；同一轮既有正文又有发布时卡片挂在该 Run 的助手消息上，Run 失败/取消没有正文时它作为空正文助手条目出现——已发布的文件不因无正文而消失。❌ 页外按 Run 二次回捞、或临时补条目导致 `items/total` 漂移。

**回复会话与执行状态**：

- **执行事件是事实，动画是渠道能力**：Runner 在真实模型调用前后发 `model.started` / `model.completed`；Gateway 从模型/工具事件推导阶段，经可选 `ReplySessionFactory` 展示；原生动画只由适配器生成。❌ 按计时器伪造思考/执行状态。
- **每条消息拥有独立回复会话，入站那一刻固定**：`open_reply(route, message_id)` 绑定该消息回调，状态全量替换、正文追加、`finish()` 幂等；命令回执、附件回执、错误文案都走本条消息自己的会话。❌ 用「本路由最新回调」回复旧消息或挂产物。
- **Run 终态单一发送者**：有持久出站意图的入口，终态正文只由 Runtime 投递泵按稳定键 `run:{run_id}:terminal` 发送；Gateway 的 SSE 只做增量/状态，不再发送终态正文、收尾帧或自行兜底。终态命令携带原入站消息身份与确定性 stream id（`(bot_id, message_id)` 派生），Connector 在原消息的流上**完整替换**并 `finish`；流关闭后迟到的 delta/status/终态一律拒绝；实际发送按 `(tenant, delivery_key)` 持久去重并用 delivery epoch fencing。✅ 企微成功 Run 只出现一个气泡；❌ 会话路径发完再 confirm、或兜底复用「本路由最新回调」再发一条。
- **计时不创造执行状态**：当前 submission 的 `run.created` 恢复本段起点，monotonic 计秒；等待确认、后台受理、完成/失败/取消/断流停止当前段；后台 Task 沿既有 Worker 投递通知结果。
- **正文由有界计时事件冲出**：读取循环醒来节拍 = `min(计时节拍, 正文合并窗口)`；每个 tick 先 `renderer.flush()` 再更新计时（节拍按 `im.progress_interval_sec` 节流）。✅ 静默 1 秒内正文已出去；❌ 正文只在下一条 delta 到来才可能被冲出。
- **状态发送有界**：默认 5 秒一拍（平台设置 `im.progress_interval_sec`），每机器人共享令牌预算；额度不足跳过当前刷新，不积压 tick。正文与收尾绕过状态预算；状态帧与正文帧共用一条 session 流（任何时刻一个写者）；一字不差的状态帧不重发。❌ 每秒排队等上一拍，或状态帧与正文帧并发写同一条 stream。
- **节拍按客户端重排成本定**：节拍过快会让企微对话框滚动发涩；具体值由平台设置定义；`已执行 mm:ss` 跳秒变粗是有意取舍。节拍做成平台设置（生产默认由单测钉住，验收栈按租户种设置行），❌ 用环境变量当节拍来源。
- **等待是执行事实，不是终态**：`run.waiting_tool` 只停本段计时，`run.resumed` 开新执行段；等待期不发完成帧、不伪造活动。断流不等于取消，重连按最后确认 `seq` 经 `open_events(after_seq)` 续读，绝不重复 POST 建 Run。`WAITING_TOOL` 不允许显式 human resume（只服务 `WAITING_INPUT`），`/stop` 走独立命令容量。ReplySession 失效走渠道中立的主动投递，无能力或失败必须显式失败、不得回「已送达」。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。

## Applied Deltas

- {"source":"2026-11-12/webchat-conversation-delete-and-attachment-formats/webchat-conversation-delete-and-attachment-formats.md#TASK-001#DELTA-001","op":"add","rule":"RULE-001","payload_sha256":"c08c858f77bbb78851e7f77b50eeee4778d3fe470d5d5c07397614651b01149e","at":"2026-11-12T16:21:37.940Z"}

- {"source":"2026-11-12/webchat-conversation-delete-and-attachment-formats/webchat-conversation-delete-and-attachment-formats.md#TASK-002#DELTA-001","op":"add","rule":"RULE-002","payload_sha256":"2dd6c8f15a64d46e21213c9dc11c14d0f6888e115eb8973a2001fcbb13a84ecf","at":"2026-11-12T16:21:37.941Z"}

- {"source":"2026-11-12/webchat-conversation-delete-and-attachment-formats/webchat-conversation-delete-and-attachment-formats.md#TASK-005#DELTA-001","op":"add","rule":"RULE-003","payload_sha256":"4f383632fabc8102f4db853af10a64812f26d3549e9e5c933a9090d913590a67","at":"2026-11-12T16:21:37.942Z"}
