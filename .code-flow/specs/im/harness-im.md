---
id: harness-im
description: Agent Harness 通用平台规则：im
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
- [RULE-im-002] **渠道差异只活在适配器层**：核心域（`agent-runtime` / `agent-worker`）零渠道专有字样与取件形状；`im-gateway` 除 `channels/` 与装配根 `main.py` 外同样为零。取件、下载、解密、媒体 URL/密钥**一律不出适配器**——上层只看到「已解密字节 + 元信息」。机检见 `tests/architecture/test_channel_neutrality.py`（三条：核心域全文零字样 / 核心域不得给 `ResolveDefinitionRequest.channel` 传字面量 / 全仓代码扫描只允许出现在 `ALLOWED_SURFACES`）。

## Conventions

入站、绑定的错误语义与幂等口径：

- **入站消息按 `message_id` 去重，且只有「已受理」才落终值**：键 `im:dedupe:{channel}:{message_id}`、TTL 600s。入口先 `reserve` **占位**（值带所有者 token），**受理之后**才升级为终值；受理前的依赖故障（平台设置拉取失败、授权解析失败、附件落盘失败、Runtime 提交被拒）一律**释放占位**，让平台重投能真正重试（`application/inbound.py` 的 `handle` / `_reserve_seen` / `_mark_accepted` / `_release_seen`）。受理之后的重复由下游幂等键兜底（`message_id` 作为 `Idempotency-Key` 沿链路透传）。去重存储不可用时**降级为 at-least-once（fail-open）且不得阻断投递**——`NullDedupeStore` 一律放行并只记告警（`infrastructure/dedupe.py`）。丢消息比重复消息更严重：宁可重复处理，不可因 Redis 抖动静默吞掉用户消息。
  - ✅ 未受理就释放占位（重投能重跑）；❌ 看一眼就写 10 分钟终值 —— 依赖抖动会把消息永久标成「处理过」，用户既没有 Run 也收不到任何反馈
  - ✅ 依赖故障也让用户看见（设置读不到时用目录默认语言回一条错误文案）；❌ 只落一条 unexpected 日志
- **Bot 快照按固定节拍轮询并整批发布，且「收不齐」与「没应用成功」都不发布**：默认 30s、分页上限 100；跨页 `revision`/`total` 不一致、**页数超过上限（截断）**、**实收条目数 ≠ 声明的 `total`** —— 三种不完整读取都整批丢弃、保留旧快照等下个节拍；拉取失败保留最近一次完整快照且不清空（`application/bot_snapshot.py` 的 `refresh` / `_collect_snapshot`）。
  - ✅ 失败保留上一份（`revision` 不变）；❌ 失败清空 → 全量 bot 短时不可路由
  - ✅ **适配器应用成功后才认定该 revision 生效**（回调抛错 → 保留待应用状态、下一轮重试）；❌ 先提交 revision 再 await 回调 —— 回调一失败就永远跳过这个 revision，连接管理器停在旧配置
  - ✅ 不完整就丢弃（少读一页等于把那些 bot 当已删除而**断连**）；❌ 把截断的分页结果当完整配置发布
- **投递确认语义 `reserve` → 发送 → `mark`，且占位「有主」**：`reserve` 是 in-flight 占位（TTL 30s，短于送达标记），只有 `mark` 的送达标记才代表成功（TTL 604800s）；占位冲突最多等 2s，超时回可重试错误（`500`）而非假装成功；发送失败必须释放占位让 Worker 重试。**占位值里带所有者 token，释放/升级/续租都是「比较值再动作」**（Redis Lua：`_RELEASE_SCRIPT` / `_MARK_SCRIPT` / `_RENEW_SCRIPT`，`infrastructure/dedupe.py`）；长发送期间按 `DELIVERY_IN_FLIGHT_RENEW_SEC` 续租（`api/delivery.py`）。
  - ✅ 未送达 → 释放占位 + 报错；❌ 把「占位存在」当成「已送达」回报 `delivered=true`
  - ✅ 过期占位的旧主人**动不了新主人的键**；❌ 无条件 `DEL`/`SET` —— 占位过期后旧主人会删掉新主人的成功标记，同一 delivery_key 被真发两次
  - ✅ **成功键里存首次交付结果**（`outcome`/`fallback_url`），重放原样回放；❌ 只回四个布尔字段 —— 首投响应一丢，降级取件链接就永远查不到了
  - ✅ 幂等指纹含 `endpoint` 与 `tenant_id`，同 key 异指纹 → `IDEMPOTENCY_MISMATCH`（`RULE-api-002`）；指纹取的是**交付身份**（租户/交付键/路由/载荷形态/产物），**不含正文文案** —— Worker 每次尝试都按当下的默认语言重渲染正文（`build_delivery_message(task, locale)`），把文案算进指纹会让一次无害的重试变成**不可重试的 409**
- **`/internal/deliveries` 是受信内部端点，且产物归属由网关自己核**：端点要求服务身份 `X-Internal-Service`（`InternalServiceDep`，与 resolve-credentials 同门控）；投递声明的 `tenant_id` 必须等于**本网关部署的租户**（网关按单租户部署）；产物形态投递必须按 `artifact_id` 经 Runtime 的 `/internal/artifacts/{id}` **在本租户内解析**出权威引用，调用方给的 `storage_key` 只用于比对、不一致即拒（`api/delivery.py`、`infrastructure/artifact_resolver.py`）。
  - ✅ 三个调用方（Runtime/Worker 的交付客户端、直投的测试、故障注入代理）都带头；❌ 不带身份裸投 —— 任何能连上内网的人都能借它把任意存储键指向的文件发给用户
  - ❌ 相信调用方自带的 `storage_key` 并直接按它读共享存储（可以指到别的租户的文件）
- **`channel` 白名单仅 `WECOM`**：契约用 `Literal["WECOM"]` 钉死（`packages/contracts/src/muad_contracts/channel.py:23-29`），Gateway 侧适配器按 `name` 注册、重名直接抛 `DuplicateChannelAdapterError`（`apps/im-gateway/src/muad_im_gateway/channels/probe.py:22-23`、`channels/base.py:65-79`）。
  - ❌ 只加新通道实现而不改契约 Literal（请求在入口就被拒，或绕过校验后无处路由）
- **绑定码一次性 + 绑定请求幂等**：绑定码状态 `USED`/`REVOKED` → `BIND_CODE_INVALID`，`EXPIRED`/已过期 → `BIND_CODE_EXPIRED`（`application/channel_service.py:219-227`，Console 侧）；带 `Idempotency-Key` 时用共享幂等表 + `pg_advisory_xact_lock` 串行化，同 key 异指纹 → `IDEMPOTENCY_MISMATCH`（`:135-159,171-172`）；`identity_key = (channel, bot_id, external_user_id)` 每租户唯一（`infrastructure/models/channel.py:60-66`），该身份已属于其他平台用户时抛 `IDENTITY_ALREADY_BOUND` 且**不消费绑定码**（`:250-252`）。
- **`bot_id` 是跨租户全局唯一**：`uq_bot_account_bot_id` 只含 `bot_id`、**不含 `tenant_id`**（`migrations/versions/0002_initial_schema.py:379-386`、`infrastructure/models/channel.py:19-25`），冲突统一报 `BOT_ID_EXISTS`（`application/channel_admin_service.py:117,164`）。因此 A 租户已占用的 `bot_id` 会让 B 租户创建失败——这是**有意的全局唯一**（一个物理 bot 只能路由到一个 Agent，避免跨租户抢占同一 bot）。
- **解析顺序固定，错误码不得泄露跨租户存在性**：先向 Console `resolve`（未知/禁用/已删 bot → `BOT_NOT_FOUND`；跨租户 bot 同样按 `BOT_NOT_FOUND`，不泄露其存在性），再依次判未绑定 → `UNBOUND`、未授权 → `NO_PERMISSION`（`apps/console-platform/backend/src/muad_console_platform/application/channel_service.py:94-126`；`apps/im-gateway/src/muad_im_gateway/application/inbound.py:414-428`）。
  - 机检：`tests/console_channel/test_channel_resolve_api.py:210-220`
- **幂等键沿链路透传**：Gateway 以原通道 `message_id` 作为 `Idempotency-Key` 传给 Console bind 与 Runtime run/conversation，使可重试提交不重复建 Run（`apps/im-gateway/src/muad_im_gateway/application/runtime_client.py:98-101`、`application/console_client.py:198-200`）。

- **入站消费：读循环永不阻塞，排队有界且可见**：`consume` 只做计数与转交——非命令消息按 route 串行（等待发生在任务内部）、在飞处理由信号量封顶、命令走独立容量；排队上限分全局与每路由两级，队满**显式拒绝**（`CHANNEL_BUSY` 文案 + 指标 + 日志），绝不静默丢（`application/inbound.py` 的 `_Backlog` / `_refuse_overloaded`）。
  - ✅ `/stop` 在同路由积压时仍然即时可用；❌ 把「排队等锁」也算进活跃名额 —— 一条长流 + 7 条排队消息就能占满名额，读取循环卡在 `asyncio.wait` 上再也读不到 `/stop`

入站附件的取件与落盘（wecom-inbound-media，2026-10-02 归档）：

- **取件是适配器的「可选能力」，不是核心域的接口**：适配器实现 `AttachmentSource`（`attachment_count(envelope)` + `fetch_attachment(envelope, index, *, max_bytes)`），核心域只给 envelope/下标/上限，拿回 `FetchedAttachment`（已解密字节 + MIME + 文件名 + checksum）——`url`/`aes_key`/`media_id` 这类渠道私有形状**一步都不出去**（`apps/im-gateway/src/muad_im_gateway/channels/base.py:55-78`、`application/inbound.py:543-575`）。
  - 新通道只要实现这两个方法，预检/取件/实检/落盘/反馈/审计那一整段**一行都不用改**（对照用例：`tests/gateway/test_inbound_attachment_flow.py` 用同一条 `msgid` 喂两条不同取件路径，契约/反馈/审计/落盘字节逐项相同）。
  - ❌ 在应用层写 `if channel == "WECOM"` 分支或直接摸适配器的取件凭据
- **门控分两段：预检判数量（取件前）→ 取件 → 实检判类型与大小（解密后）**：渠道回调常**不带** `size`/MIME/文件名（企微只有 `{url, aeskey?}`），"下载前按类型与大小门控"物理上不可实现（`application/attachment_gate.py:120-140`）。部分拒绝**不拖累其余**；单文件超限必须在**流式下载途中**中止，不读完再判（`channels/wecom/media.py`）。
  - **尺寸口径只有一个：明文**。流里流的是密文，所以流式读取允许 `ENCRYPTION_OVERHEAD_BYTES`（一个 AES 分组）的固定增量，**解密后再按明文严格判一次**。两处口径不一致时，"恰好等于上限"的合法附件会被自己的加密开销挤掉。
  - ✅ 流式上限 = 明文上限 + 加密开销，异常里报**产品上限**；❌ 读的时候比密文、解密后又比明文
- **原因码是渠道中立的词汇表，适配器负责翻译**：`ATTACHMENT_TOO_LARGE` / `ATTACHMENT_FETCH_FAILED` / `ATTACHMENT_FETCH_TIMEOUT` / `ATTACHMENT_DECRYPT_FAILED` 定义在渠道边界（`channels/base.py:47-64`），适配器把自己的私有异常翻成 `AttachmentFetchError(code)`；应用层据此取文案 + 写审计，**不 import 任何渠道异常**。
  - 反馈文案一律经消息目录（`config/api-messages.yaml`），**上限数值只有一个来源**：门控常量经 `args` 注入（`application/inbound.py:647-655`）——文案里的数字与判定用的常量绝不各写一份。
- **入口护栏判「有没有内容」时必须问适配器**：`ChannelEnvelope.attachments` 只描述"**已经拿到手的**字节"，纯图片/文件消息在取件前它必然为空 ⇒ 只判它会把整条媒体消息在入口拦掉，取件/反馈/审计一行都跑不到（**实测 P0**）。判空条件要含 `unsupported_media` 与适配器待取件数（`application/inbound.py:145-160`）。
- **`AttachmentRef` 一处定义、两处同型**：渠道边界 `ChannelEnvelope.attachments` 与消息契约 `MessageInput.attachments` 同型零转换；只描述已拿到手的字节，**不含 `artifact_id`**——网关只写字节、`artifact` 行由 Runtime 在 Run 建立后写（`packages/contracts/src/muad_contracts/channel.py:33-48`，设计 AD-1-B）。
  - 产物键只由系统生成（`inbound/{token}/{index}`），**用户文件名只作元信息、永不参与路径拼接**；写入**原子且只发布一次**（临时文件 + `os.link`：目标已存在即失败。`os.replace` 会静默覆盖，"不可变"就只写在注释里）。同键**同内容**重放复用已有产物（消息重投必须能走到 Runtime 的幂等提交），同键**不同内容**抛 `AttachmentConflictError`（既不覆盖别人的字节，也不把那些字节当成自己的）。
- **网关不持库 ⇒ 入站审计经 Console 内网端点写控制面表**：`POST /internal/channel/audit`（`InternalServiceDep` + `HeaderTenantId`）写 `control.im_inbound_audit`（`apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`）。审计契约 `InboundAuditRequest` 字段**全部枚举化、刻意没有自由 JSON 列** ⇒ 取件凭据在**类型上**无处可放（比写入前运行时脱敏更强）；幂等键 `(tenant_id, channel, external_message_id, outcome)` 承载重投（`ON CONFLICT DO NOTHING` + 回查）。
  - 审计面抖动**不阻断用户请求**（不能因为审计写不进去就让用户收不到回答），但失败必须留 ERROR 日志。

执行状态展示（im-execution-progress，2026-10-04）：

- **执行事件是事实，动画是渠道能力**：Runner 在真实模型调用前后发 `model.started` / `model.completed`；Executor 经现有 Run 持久事件链输出。Gateway 从模型/工具事件推导阶段，经可选 `ReplySessionFactory` 展示；原生动画标签仅由适配器生成。
  - ✅ `tool.started` → 执行中，所有并行工具完成后再切换；❌ 按计时器轮流伪造思考/执行状态
- **每条消息拥有独立回复会话，且在入站那一刻就固定**：`open_reply(route, message_id)` 绑定该消息的回调，状态全量替换、正文追加、`finish()` 幂等；**命令回执、附件回执、错误文案一律走本条消息自己的会话**（`_REPLY_SESSION` + `reply_once`：一次收到完整内容），新入站消息不能覆盖旧消息的回复目标。
  - ✅ 正文与最终结果复用同一 stream；❌ 用用户/会话级最新回调回复仍在运行的旧消息
  - ✅ 产物交付按交付键里的 `run:{run_id}:{artifact_id}` 找回**起这个 Run 的那条消息**的回调（`bind_run` 写进 Run→回调映射，保留 `RUN_REPLY_TTL_SEC`）；键里没有 Run（后台任务投递）才退回路由级最新回调
  - ❌ 产物也回「本路由最新回调」——同会话后来的消息会把仍在运行的老任务的产物挂到自己头上；**Run 已收尾也不例外**（映射过期就明确降级为主动投递，不冒充某条消息的回调）
  - ✅ **终帧有界重试**（失败不关闭会话、留 ERROR 痕迹）；❌ 先置 `_closed` 再发终帧 —— 一次瞬时断线让这条回复永远停在未收尾状态
  - ✅ 取件异常的**所有**失败形态（引用失效、断线/退避、SDK 原始异常）都翻成渠道中立的 `AttachmentFetchError`；❌ 让适配器私有异常越过渠道边界 —— 它会跳过附件反馈与审计，把整条有效请求（含文本）带走
- **计时不创造执行状态**：当前 submission 的 `run.created` 时间恢复本段起点，monotonic 计算秒数；等待确认、后台受理、完成/失败/取消/断流停止当前段。后台 Task 沿既有 Worker 可靠投递流程通知结果，本段计时不冒充后台任务进度。
- **正文由有界计时事件冲出去，不等下一条模型增量**：读取循环的醒来节拍 = `min(计时节拍, 正文合并窗口)`；每个 tick 先 `renderer.flush()` 再更新计时（计时节拍仍按 `im.progress_interval_sec` 节流，客户端看到的计时粒度不变）。模型一思考、一跑工具，用户不该看到"什么都没发生"。
  - ✅ 静默 1 秒内正文已经出去；❌ 正文只在下一条 delta 到来时才可能被冲出去（实测：一个 delta + 1.2 秒静默 = 零个正文帧）
- **状态发送有界**：计时默认 **5 秒**一拍（平台设置 `im.progress_interval_sec`，>=1s），每机器人共享可配置令牌预算；额度不足跳过当前刷新，不积压 tick、不每秒查库/写事件/调用模型。正文与收尾绕过状态预算；状态发送错误显式记录，不阻断执行。新 IM 渠道实现回复会话能力并扩展契约白名单，无须改模型/工具状态机。
  - ✅ 计时 tick 交给后台单飞任务：发送者忙就整条丢弃（下一拍按最新阶段重算），正文/收尾写入前先让在飞的 tick 落地，读取循环不被计时拖住
  - ✅ 状态帧与正文帧共用一条 session 流 ⇒ **任何时刻只有一个写者**；慢/挂住的状态发送由超时兜底后放弃并留日志
  - ✅ **一字不差的状态帧不重发**（同内容比对含当时正文）：客户端每帧都会整帧重排 + 滚动到底，重复帧纯属白花
  - ❌ 每秒排队等上一拍发送返回，或让状态帧与正文帧并发写同一条 stream（正文会被占位盖回去）
- **节拍要按客户端重排成本定，不能按"看起来更实时"定**：1 秒一拍的计时在真机上让企微对话框滚动发涩（每帧都要整帧重排并滚到底），产品口径改为 5 秒；`已执行 mm:ss` 的跳秒粒度随之变粗是**有意**的取舍。
  - ✅ 节拍做成设置项（平台设置 `im.progress_interval_sec`）：生产默认值由毫秒级单测钉住（`tests/gateway/test_progress_settings.py`），验收栈按该栈租户种一行 `control.platform_setting` 压缩等待（`tests/acceptance/im_gateway/environment.py`）——**不再经 `IM_PROGRESS_INTERVAL_SEC` 环境变量**（该键已从启动 settings 移除）
  - ❌ 在验收里等生产默认的 5s 才敢断言"计时在走"，或为了跑得快而把那 5 秒写死进用例；❌ 用 `IM_PROGRESS_INTERVAL_SEC` 一类环境变量当节拍来源（平台设置是唯一权威源）

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
