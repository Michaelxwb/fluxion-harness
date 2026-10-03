# Tasks: 附件往返（出站交付 + 读材料增强 + 入站回执 + 生命周期）

- **Source**: `.code-flow/tasks/2026-10-02/attachment-round-trip/attachment-round-trip.design.md`
- **Created**: 2026-10-03
- **Updated**: 2026-10-03

## Proposal

上一需求 `wecom-inbound-media` 只闭合了**入站**：用户能发图片/文件，Agent 能看、能读、能被门控与审计。另一半是断的——Agent 写出的文件**没有任何人交付给用户**（全仓 `AGENT_OUTPUT` 只有写入方、零消费方；后台任务甚至给用户贴一串 UUID）；长文档只能读前 20 000 字符且无分页；收到附件后除模型回答没有任何回执；产物只增不减。本次让附件**走得通往返**：进得来（已具备）→ 读得完 → **交得回** → 有回执、有生命周期。核心是把"写出"与"交付"分离，并让渠道差异继续只活在适配器层。

### Alignment

- **Scope**：四个包——读材料增强（FEAT-01..04）、入站回执（FEAT-05）、出站交付（FEAT-06..11、14）、产物生命周期（FEAT-12、13）；P0 先交付，P1 随批。
- **Decisions**：
  - **探针先行**（FEAT-06 是 P0 前置）：企微能否发文件/图片、上传流程（media_id vs URL）、发送 ack 与幂等——**外部协议事实**，猜错要重写整条链（与上期 R-01 同源）。**已于 2026-10-03 完成**：可直发，唯一通路 `media_id`（来自三步分片上传），走**直发分支**；事实表见设计 §3.6「真机探针结论」。
  - **写与交付分离**：`write_artifact` 只写（不再因缺交付路由而拒绝），新增 `append_artifact`（分段追加长文档）与**显式交付工具** `deliver_artifact`。依据是五条代码事实（设计 §3.6）：现有守卫把"写"与"可交付"耦合错了方向、中间产物会被误发、"写了但没发"的语义无法表达、幂等键必须在交付那一步可派生等。**否决** `write_artifact(deliver=true)` 参数式合并。
  - **会话内交付走同步调用**（**2026-10-03 修订**）：runtime **同步 POST 网关既有 `/internal/deliveries`**，拿到真实交付结论；**否决**原稿的"发 SSE 事件 + 网关异步消费"。理由：网关是 SSE 的**纯消费方**（runtime 只有 `/runs`、`/resume`、`/cancel`、`GET /runs/{id}`，没有回执通道），异步化会让"失败可重试/降级为链接"无法诚实表达（RULE-03），E-06 按原设计不可实现。代价是 Run 多一次渠道往返——由独立超时兜住，**超时按失败（未知），不得算成功**。交付端点因此**一条契约两个调用方**（worker 已在用）；`DeliveryRequest` 需最小扩展（`task_id` 可省 + `run:{run_id}:{artifact_id}` 形态），对既有调用向后兼容。
  - **追加写换新键**：`RULE-skill-001` 要求同 `storage_key` 二次写入必须抛错 ⇒ 分段追加写**新 key**，artifact 行指向最新版本，历史版本记在既有 `metadata_json`（不改 schema）。
  - **出站渠道差异走可选能力协议**（与入站 `AttachmentSource` 同构）：核心域只给"产物引用 + 路由"，适配器决定直发/链接/降级。
  - **交付审计表从第一天起渠道中立**：`channel + route_key`（适配器产出的可读不透明串），**不照抄**上期入站审计表的 `bot_id + external_user_id` 企微形状——未来上 web chat 时那两列会成为"填不出真值"的必填字段。**幂等键唯一权威定义 = `(tenant_id, artifact_id, route_key)`**（artifact 级），`outcome` 是该行当前状态（`DELIVERED` 为终态，失败重试成功更新同一行）——这样 S-10"审计仍只有一行"与 E-06"失败→重试成功"同时成立。
  - **取件是渠道无关能力**：签名短 TTL 直链 + 鉴权端点，**降级链接与未来 web chat 复用同一套**。
  - **前端页面后置**（用户已确认）：本期**不做**任何 Console 页面（IM 终端用户是 `platform_user`，Console 登录主体是 `console_account`，两套身份——给终端用户 Console 页面等于给一扇打不开的门）；`harness-frontend` / `harness-ui` 两条 required 规则在本需求标 **N/A**（用户逐条确认）。
- **Non-goals**：企微以外的 IM 通道（只做企微，但接缝按渠道中立设计）；附件在线编辑/协作；文档结构化理解；病毒扫描/内容审核；跨租户产物共享；出站消息的富文本排版（卡片模板只作为链接载体）。
- **Acceptance**：见下方 Acceptance Coverage（19 条场景；S-05 为 manual，原因是需要真实外部机器人与会话，无法在 CI 复现）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 | cwd | 超时 | 依赖 |
|--------|---------|---------|-------------|---------|------|------|-----|------|------|
| S-01 | design#2.5.2 | integration | 真实文件系统 + 真实解析库（pypdf/docx） | TASK-002 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| S-02 | design#2.5.2 | integration | 真实 PG（runtime.artifact 逐行回读） | TASK-003 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| S-03 | design#2.5.2 | E2E | 真实回调桩 → 真实落盘 → 真实工具 → 真实模型请求体 | TASK-010 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | . | 300 |  |
| S-04 | design#2.5.2 | E2E | 真实 WS 探针 → 真实网关 → 真实渠道帧 | TASK-004 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py | . | 300 |  |
| S-05 | design#2.5.2 | manual | 真实企微机器人（外部条件，无法在 CI 自动化） | TASK-001 | verified | - | . | 60 |  |
| S-06 | design#2.5.2 | E2E | 真实会话 → 真实产物 → 真实 HTTP 交付调用 → 渠道帧/链接 | TASK-010 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | . | 300 |  |
| S-07 | design#2.5.2 | E2E | 真实 Worker 进程 → 真实网关 /internal/deliveries → 渠道帧 | TASK-010 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | . | 300 |  |
| S-08 | design#2.5.2 | integration | 真实文件系统 + 真实 PG | TASK-009 | verified | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | . | 120 |  |
| S-09 | design#2.5.2 | E2E | 真实 HTTP 取件端点 + 真实鉴权（非 mock） | TASK-008 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_artifact_fetch_e2e.py | . | 300 |  |
| S-10 | design#2.5.2 | E2E | 真实渠道帧 + 真实 PG（审计逐行回读） | TASK-010 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | . | 300 |  |
| E-01 | design#2.5.2 | integration | 真实文件系统 | TASK-002 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| E-02 | design#2.5.2 | E2E | 真实 WS 探针 → 真实网关 | TASK-004 | verified | uv run pytest -q tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py | . | 300 |  |
| E-03 | design#2.5.2 | integration | 真实 PG + 真实存储 + 鉴权层 | TASK-008 | verified | uv run pytest -q tests/console_channel/test_artifact_fetch.py | . | 120 |  |
| E-04 | design#2.5.2 | integration | 真实 HTTP（console 内部端点）+ 真实 PG | TASK-007 | verified | uv run pytest -q tests/console_channel/test_artifact_delivery_audit.py | . | 120 |  |
| E-05 | design#2.5.2 | integration | 真实文件系统 + 真实 PG | TASK-009 | verified | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | . | 120 |  |
| E-06 | design#2.5.2 | integration | 真实 HTTP（网关交付端点 + 渠道侧失败注入） | TASK-006 | verified | uv run pytest -q tests/gateway/test_artifact_delivery.py | . | 120 |  |
| B-01 | design#2.5.2 | unit | 分段纯函数 | TASK-002 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| B-02 | design#2.5.2 | unit | 枚举分页 | TASK-003 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| B-03 | design#2.5.2 | unit | 出站产物大小 | TASK-005 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |

> 覆盖自检：design 全部 P0/P1 场景 **19/19** 已分配唯一负责人（S-01..S-10、E-01..E-06、B-01..B-03）；RULE-01..07 与高影响 R-01/R-06 均有映射场景；E2E 场景 **7 个**（S-03、S-04、S-06、S-07、S-09、S-10、E-02）层级未降级；`manual` 仅 S-05，原因是需要真实外部机器人与会话（CI 无法复现，设计 R-06）。

---

## TASK-001: 企微出站能力真机探针

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.1 功能清单`, `#3.6 出站交付：写与发的分离（硬需求落点）`, `#5.1 项目依赖`
- **Spec-Refs**: harness-im#RULE-im-002
- **Acceptance-Refs**: S-05

### Description

出站交付的**形态**取决于一个尚未验证的外部事实：企微 aibot 能否发文件/图片。现有代码只证明会话内回复体支持 `stream`/`template_card`、主动发送体支持 `markdown`/`template_card`（`apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py:444-457`）；文件上传与发送**完全没有结论**。本任务用真实机器人给出**事实表**，它是 TASK-006 形态分支（直发 vs 链接降级）的输入——不先做，整条出站链只能悬空。

**探针代码只能落在适配器层**（`channels/wecom/`）：核心域与网关应用层不得出现任何渠道专有字样或上传凭据，`tests/architecture/test_channel_neutrality.py` 会当场判红。

**探针结论（2026-10-03，已完成）**：企微 aibot **可以直发文件与图片**，唯一通路是 `media_id`（`url` 与内联 `base64` 均被 `40058` 拒收）；上传为**三步分片**（`aibot_upload_media_{init,chunk,finish}`，分片 ≤512 KiB、≤100 片），**Python SDK 未实现、需在适配器层自实现**；渠道**不做幂等**。完整事实表见设计 §3.6「真机探针结论」，逐条原始回执见下方 Acceptance Evidence。

### Checklist

- [x] 按探针清单逐项实测并记录**原始帧与 ack**：① 会话内回复（`aibot_respond_msg`）能否发文件、发图片；② 主动发送（`aibot_send_msg`）能否发文件、发图片；③ 若能发，文件从哪来（先上传拿 `media_id` 还是直接给 URL/字节）；④ 发送是否有 ack、失败长什么样；⑤ 重投同一条发送请求是否重复发出文件 —— **六项全部有结论**：①② 两条路径都能发文件与图片；③ 只能走 `media_id`（`url`/内联 `base64` 被 `40058` 拒），`media_id` 来自三步分片上传；④ ack 分层（详见下）；⑤ 渠道**不去重**，同 `media_id` 连发两次用户侧出现两条
- [x] 断言必须落在**真实响应**上（errmsg/errcode/帧体），不得只看"没抛异常"——上期踩过"本地探针不校验故套件测不出"的坑 —— 每条变体记录完整回执帧；并设**两个对照**（`markdown` 合法 / `text` 非法），分别验证探针能测出"通过"与"拒绝"。对照当场抓出过一次探针自身缺陷（绕过 `WSClient.send_message` 致 `chatid` 丢失，**所有** msgtype 误报 `86201`）
- [x] 产出**事实表**（可否直发文件/图片、上传流程、ack 语义、失败形态、幂等性），并写回设计 §3.6 的"跳 4"与 §5.1 的状态列 —— 事实表见设计 §3.6「真机探针结论」；跳 4 改为**直发（`media_id`）**；§5.1 风险等级高→低；同步回填 §2.4 前置假设/妥协、§3.2 外部依赖、§5.2（R-01 消解）
- [x] 真机环境注意：绕系统 SOCKS 代理（否则缺 python-socks 报错）、可上 TLS 中继做真实断链、看 `wecom_ws_connected` 与 Reply ack、**不要起 Worker** —— `no_proxy` 已设且生效；全程监控 `wecom_ws_connected`（探针连接期间 dev 网关始终为 1，未被踢下线）；**未起 Worker**。*未做 TLS 中继断链*：断链/退避属上期 E-10 的验收范围，本任务验收项是"发送能力"，不重复覆盖
- [x] [S-05][manual] 登记探针步骤与记录位置（真实边界：真实企微机器人 + 真实会话；无法在 CI 复现，原因是外部凭据与会话，设计 R-06）—— 见下方 Acceptance Evidence「探针步骤」与「记录位置」
- [x] 运行 verifier：`uv run pytest -q tests/architecture/test_channel_neutrality.py`（`harness-im#RULE-im-002`）；记录输出 —— `3 passed in 0.55s`（探针代码不入库，只改文档，守卫未被触犯）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | manual | 真实企微机器人 + 真实会话（外部条件） | 产出事实表：可否直发文件/图片、上传流程、ack 语义、失败形态；结论写回设计并决定 S-06 的形态分支 | 人工执行（探针清单见 Checklist） | - | verified |

### Acceptance Evidence

> manual 场景只登记原因/边界/验收方式；人工确认与 E2E 执行统一留给 verify-e2e。

- S-05: manual_pending —— 探针已真实执行并记录逐条回执帧（见下）；**用户已在企微内目视确认渲染结果**。正式的"人工验收确认"（`confirm-manual` → `verified`）按设计约定留到需求级 verify-e2e 一次性落，不由 Agent 代确认。

**真实边界**：真实企微 aibot 长连接（`wss://openws.work.weixin.qq.com`）+ 真实会话 + 真人目视确认渲染。CI 无法复现（外部凭据与会话，设计 R-06）。

**探针步骤**（探针代码**不入库**，只存在于会话期 `/tmp`——渠道专有形状与上传凭据不得进核心域）

1. 从 `control.bot_account` 取 enabled 的 WECOM bot（`bot_id` + `secret`），从 `control.channel_identity` 取单聊 chatid（单聊 chatid 即 `from.userid`）。凭据只经环境/DB 读取，**不打印、不入库、不进证据**。
2. `no_proxy=openws.work.weixin.qq.com,127.0.0.1,localhost` 绕 macOS 系统 SOCKS 代理（否则缺 `python-socks` 直连报错）；`aibot.WSClient(...max_reconnect_attempts=0)` 直连真机；tee `_ws_manager._handle_frame` 落**每一条**收到的原始帧。
3. 主动发送路径：`client.send_message(chatid, body)` 跑能力矩阵（含 `markdown`/`text` 两个对照）。
4. 上传：按 Node SDK 参考实现自实现 `aibot_upload_media_{init,chunk,finish}` 三步分片，再发真 `media_id`。
5. 会话内回复：探针常驻等待**真实用户发消息**，取入站帧 `headers.req_id` 后逐条回复（该 req_id 只能由真实回调产生，无法构造）。
6. **渲染结果由用户在企微内目视确认**——ack 只证明服务端受理，不证明用户看见。

**原始回执帧（逐条，`errcode`/`errmsg` 原样；`upload_id`/`media_id` 为不透明串，此处截断）**

主动发送路径：
```
CTL-markdown      {"headers":{"req_id":"aibot_send_msg_…"},"errcode":0,"errmsg":"ok"}
CTL-text          {"headers":{"req_id":"aibot_send_msg_…"},"errcode":40008,"errmsg":"invalid message type, hint: […], from ip: 163.125.147.69"}
image-url         {"errcode":40058,"errmsg":"missing field `body.image.media_id`. invalid Request Parameter, …"}   ← 帧无 headers.req_id
file-url          {"errcode":40058,"errmsg":"missing field `body.file.media_id`. invalid Request Parameter, …"}    ← 帧无 headers.req_id
image-mixed-url   {"headers":{"req_id":"…"},"errcode":40008,"errmsg":"invalid message type, …"}
image-media-id    {"headers":{"req_id":"…"},"errcode":40007,"errmsg":"invalid media_id, …"}
image-base64      {"errcode":40058,"errmsg":"missing field `body.image.media_id`. …"}                             ← 帧无 headers.req_id
```

上传三步（1 片与 3 片各跑一遍）：
```
aibot_upload_media_init   → {"headers":{"req_id":"aibot_upload_media_init_…"},"body":{"upload_id":"a23cff8e…"},"errcode":0,"errmsg":"ok"}
aibot_upload_media_chunk  → {"headers":{"req_id":"aibot_upload_media_chunk_…"},"errcode":0,"errmsg":"ok"}        ×1（15.8 KB）/ ×3（1.25 MB）
aibot_upload_media_finish → {"headers":{…},"body":{"type":"image","media_id":"32KoGhL5…"(87 字符),"created_at":1790959832},"errcode":0,"errmsg":"ok"}
```

发送真 `media_id`（`chatid` = 单聊 userid）：
```
send image        {"headers":{"req_id":"aibot_send_msg_…"},"errcode":0,"errmsg":"ok"}  ← 用户侧：图片渲染 ✅
send file         {"headers":{"req_id":"aibot_send_msg_…"},"errcode":0,"errmsg":"ok"}  ← 用户侧：文件渲染 ✅
send image-repeat {"headers":{"req_id":"aibot_send_msg_…"},"errcode":0,"errmsg":"ok"}  ← 同一 media_id 重发：用户侧出现**第二张**（渠道不去重）
```

会话内回复路径（**五条共用同一条入站回调的 `req_id`**；入站帧：`cmd=aibot_msg_callback`、`body.from.userid=XuWenBin`、`body.chattype=single`、`body.msgtype=text`）：
```
R0 stream finish   {"headers":{"req_id":"zYiT5A1AQVaghhPBWxgzAwAA"},"errcode":0,"errmsg":"ok"}  ← 用户侧：文本渲染 ✅
R1 text            {"headers":{"req_id":"zYiT5A1AQVaghhPBWxgzAwAA"},"errcode":40008,"errmsg":"invalid message type, …"}
R2 image           {"headers":{"req_id":"zYiT5A1AQVaghhPBWxgzAwAA"},"errcode":0,"errmsg":"ok"}  ← 用户侧：图片渲染 ✅
R3 file            {"headers":{"req_id":"zYiT5A1AQVaghhPBWxgzAwAA"},"errcode":0,"errmsg":"ok"}  ← 用户侧：文件渲染 ✅
R4 stream+msg_item {"headers":{"req_id":"zYiT5A1AQVaghhPBWxgzAwAA"},"errcode":0,"errmsg":"ok"}  ← 用户侧：**无图**
```

`msg_item` 追加验证（**6 种形状全部 `errcode 0` 且用户侧均无图**）：`{media_id}`、`{base64,md5}`（Node SDK 类型定义所载形状，PNG 15.8 KB）、大写 md5、无 md5、1×1 与 64×64——与官方《回复消息》文档「`body.stream` **暂不支持 `msg_item` 字段**」一致。

**用户侧目视确认**（探针自证不了送达）：主动路径的图片、文件、重复图均渲染；会话内路径 R0/R2/R3 渲染、R4 无图。

> **记录位置**：完整原始 JSON（含全部帧）为会话期 `/tmp/wecom_probe_outbound.json`、`/tmp/wecom_probe_media2.json`、`/tmp/wecom_probe_reply.json`、`/tmp/wecom_probe_msgitem_size.json` 等，**随会话结束即失**；本文件与设计 §3.6 保留逐条回执帧与结论，为持久记录。测试用 `media_id` 3 天后失效，非机密。
- S-05: verified — 用户在会话中本人确认（2026-10-03）：真机探针结论（可直发文件/图片、唯一通路 media_id、三步分片上传、渠道不做幂等）采纳；渲染结果此前已在企微内由用户目视确认 (confirmed_by: user)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 主动发送路径探针完成：可发 `markdown`；`text` 40008；`image`/`file` 收 `url`/`base64` 均 40058 ⇒ 定位到唯一通路 `media_id`
- [2026-10-03] 自实现三步分片上传并实测通过（1 片 / 3 片）；发真 `media_id` 成功，用户侧确认图片与文件渲染
- [2026-10-03] 会话内回复路径探针完成（需真实入站 `req_id`）：`image`/`file` 直发渲染；`msg_item` 六种形状均"收下不出图"
- [2026-10-03] 查证官方《回复消息》文档（101836）：`msg_item` **官方暂不支持**；补齐合法 msgtype 全集与 20480 字节 / 10 分钟 finish 等硬边界
- [2026-10-03] 回填设计 §3.6「真机探针结论」事实表 + 跳 4 + §2.4/§3.2/§5.1/§5.2；verifier `tests/architecture/test_channel_neutrality.py` 3 passed
- [2026-10-03] completed (done)

---

## TASK-002: 读材料工具面：分段读 + 文档内定位 + 大文件策略

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.2 字段约束`, `#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**:
- **Acceptance-Refs**: S-01, E-01, B-01

### Description

`read_attachment` 现在只能返回前 `MAX_TEXT_CHARS = 20 000` 字符且 schema 只有 `artifact_id`——长文档实际不可用。本任务给它加 `offset`/`limit` 分段，并补 `search_attachment`（在已抽取文本里按关键词返回命中片段与偏移，避免模型盲目翻页），以及**长文档的翻页指引**。**抽取一次、切片多次**：单次工具调用内缓存全量文本，不因 offset 变化重复解析同一文档。

> **范围收窄（2026-10-03，用户确认）**：需求稿写的「目录/摘要」**不做**——抽取结果是纯文本、没有真实文档结构可依，"行首偏移表"语义模糊且会挤占模型上下文。FEAT-03 降级为**可行动的翻页指引**（全文总长 + 本段区间 + 后续各段 `offset` + 单次上限）。真需要"目录"时另开需求。
> 口径澄清：这里说的"超长"是**正文超过 `MAX_TEXT_CHARS`（字符）**，与字节门控 `MAX_ATTACHMENT_BYTES` **无关**——60 000 字符的文档通常只有几十到一两百 KB。
> **附带改动（2026-10-03，用户要求）**：单文件上限 **20 MiB → 50 MiB**，门控与 Runtime 镜像两个常量同步改（不同步会产生"门控收下了、Runtime 读不了"的悬空文件）。依据：企微官方入站回调上限 100 MB、出站上传天花板 ≈50 MB。**取代**归档需求 wecom-inbound-media 的 RULE-05 数值（数量上限仍为 5）。落点：门控常量 + Runtime 常量 + `tests/gateway/test_attachment_gate.py` 里钉死数值的那条断言。

### Checklist

- [x] `read_attachment` schema 增 `offset`（≥0，缺省 0）与 `limit`（1..`MAX_TEXT_CHARS`，缺省 `MAX_TEXT_CHARS`）；返回值标注**片段区间与全文总长**，使多次调用可拼接还原
- [x] 新增 `search_attachment(artifact_id, query, limit?)`：返回命中片段 + 偏移；未命中明确说"未命中"，不返回空内容冒充成功
- [x] 长文档翻页指引：正文超过 `MAX_TEXT_CHARS` 时给出**可行动**的翻页指引（全文总长 + 本段区间 + **下一段的 `offset`** + 单次上限），而不是只丢一句"内容过长，已截断"；**不做"目录/摘要"**（见 Description 的范围收窄）
- [x] 拒绝路径保持既有口径：不支持的 MIME、损坏文档、**非法 offset（负值）/ 非法 limit** 各自返回**明确错误码**，绝不返回乱码或空内容。**注**：`offset` **超出全文长度不是错误** —— 返回空片段并注明（设计 §2.3.2 + B-01 口径；原清单把两种"越界"混为一谈，已按设计澄清）
- [x] [S-01][integration] 真实边界：真实文件系统 + 真实解析库；断言三次片段（`offset=0/20000/40000`）拼接**逐字符等于**全文，且每段标注区间与总长
- [x] [E-01][integration] 真实边界：真实文件系统；断言超上限/损坏文档/非法 offset（负值）各自返回明确错误（指明文件与原因），不返回乱码或空内容；并断言 `offset` **超出全文长度**返回空段+标注而**非报错**
- [x] [B-01][unit] 真实边界：分段纯函数；断言 `offset` 取 0 / 恰好等于总长 / 超出总长 / 负值 四种边界的行为（首段 / 空段+标注 / 空段+标注 / 参数错误）；另断言 `limit` 的上下界（0 与 >`MAX_TEXT_CHARS` 为参数错误）
- [x] **（附带改动）单文件上限 20 MiB → 50 MiB**：门控 `MAX_ATTACHMENT_BYTES` 与 Runtime 镜像 `MAX_READ_BYTES` **已同步**；`tests/gateway/test_attachment_gate.py` 里钉死数值的那条断言同步更新；`tests/acceptance/im_gateway/test_wecom_attachments.py` 的超限夹具按常数自适应（随之上到 62 MiB，耗时与内存约翻倍，属预期）。验收：`uv run pytest -q tests/gateway/test_attachment_gate.py` → **7 passed**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实文件系统 + 真实解析库（pypdf/docx） | 三次片段拼接逐字符等于全文；每段标注区间与总长 | tests/agent_runtime/test_attachment_tools.py::test_s01_paged_read_reassembles_the_whole_document | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |
| E-01 | integration | 真实文件系统 | 超上限/损坏/非法 offset（负值）→ 明确错误码（含文件与原因），不返回乱码或空内容；offset 超全文长度 → **空段+标注（非错误）** | tests/agent_runtime/test_attachment_tools.py::test_e01_rejection_paths_are_explicit_and_labelled | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |
| B-01 | unit | 分段纯函数 | offset=0/等于总长/超总长/负值 四边界行为分别正确；limit 上下界非参数错误 | tests/agent_runtime/test_attachment_tools.py::test_b01_segment_boundaries | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |

### Acceptance Evidence

**执行（2026-10-03）**，登记命令 `uv run pytest -q tests/agent_runtime/test_attachment_tools.py`。

**RED（先写测试再实现，按先红后绿留证）**：`uv run pytest -q tests/agent_runtime/test_attachment_tools.py -k "b01 or s01 or e01 or document_search"`
→ `ImportError: cannot import name 'ATTACHMENT_LIMIT_INVALID' from ...application.attachment_tools`（新符号尚不存在，正是预期的失败原因）。

**GREEN**：同一文件 **14 passed in 0.56s**（原有 9 条 + 本任务 5 条，无回归）。
相邻套件 **49 passed**（`tests/gateway/test_attachment_gate.py`、`test_wecom_media.py`、`test_inbound_attachment_flow.py` —— 上限常量改动的直接下游）。
`ruff check` 覆盖 5 个改动文件：**All checks passed**。

**实现中发现并修掉的真实缺口**：`_read_bytes` 抛 `ATTACHMENT_TOO_LARGE` 时**不带文件名**（只有 `_extract_document` 那条路径加了前缀），而 E-01 要求"含文件与原因" ⇒ 抽成 `_extract()` 统一前缀（读字节与解析两条路都覆盖）。

- S-01: verified —— 60 000 字符的真 docx（单段）经真实 python-docx 抽取；`offset=0/20000/40000` 三段拼接**逐字符等于**全文；区间标注实测 `(0,20000,60000) / (20000,40000,60000) / (40000,60000,60000)`。另有一条覆盖"未读完给翻页指引、读到最后一段不再提示"。
- E-01: verified —— 超上限（**真实落盘** 50 MiB + 1 字节）→ `ATTACHMENT_TOO_LARGE` 且含文件名；损坏 docx → `ATTACHMENT_EXTRACT_FAILED` 且含文件名；负 offset → `ATTACHMENT_OFFSET_INVALID` 且含文件名；**offset 超全文长度 → 空段 + "共 3 字符"标注，不报错**。
- B-01: verified —— 纯函数 `slice_text` 四边界：`0`→首段、**等于**总长→空串、**超出**总长→空串、负值→`ATTACHMENT_OFFSET_INVALID`；另 `limit` 取 `0` 与 `MAX_TEXT_CHARS+1` → `ATTACHMENT_LIMIT_INVALID`。

**附带改动（上限 20 MiB → 50 MiB）**：`tests/gateway/test_attachment_gate.py` **7 passed**（B-01/B-02 的边界语义不变，只换数值；钉死数值的那条断言已同步）。代码内已无残留的 20 MiB 字面量（仅变更注释里作为历史提及）。
- S-01: verified — automated command passed; run_id=ae252815e2ce4c41b5af3fa26bb1f10d (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=ae252815e2ce4c41b5af3fa26bb1f10d (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=ae252815e2ce4c41b5af3fa26bb1f10d (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=09886fc31f424b2cbc5c79f570598044 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=09886fc31f424b2cbc5c79f570598044 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=09886fc31f424b2cbc5c79f570598044 (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 范围收窄（用户确认）：FEAT-03 不做"目录/摘要"，降级为可行动的翻页指引；offset 超全文长度定为「空段+标注」而非报错
- [2026-10-03] 附带改动（用户要求）：单文件上限 20 MiB → 50 MiB，门控常量与 Runtime 镜像常量同步
- [2026-10-03] 先写测试拿 RED（`ImportError: ATTACHMENT_LIMIT_INVALID`），再实现；`test_attachment_tools.py` 14 passed、相邻 49 passed、ruff 全绿
- [2026-10-03] 实现期修掉一处真实缺口：`_read_bytes` 的超限错误不带文件名
- [2026-10-03] completed (done)

---

## TASK-003: 附件枚举工具 `list_attachments`

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.2 字段约束`, `#3.4 接口设计`
- **Spec-Refs**:
- **Acceptance-Refs**: S-02, B-02

### Description

模型现在只能从上下文的文本引用里拿 `artifact_id`——而历史引用会被上下文预算裁掉，那个附件就**永久失联**。本任务新增枚举工具：按 `run`/`conversation` 范围列出可寻址的附件（入站 + Agent 自产），带 id/文件名/MIME/大小/方向/时间，使"跨多轮继续用某个附件"成为可能。

### Checklist

- [x] 新增工具 `list_attachments`，入参 `scope`（`run`/`conversation`，缺省 `conversation`）、`direction`（`inbound`/`outbound`，缺省不限）、`limit`（1..50，缺省 20）、`offset`（≥0）
- [x] 返回紧凑行文本（id、文件名、MIME、大小、方向、时间），入站与自产可区分；空集返回空列表说明而非错误
- [x] 租户与归属过滤从 Run 上下文取，**不进工具 schema**；跨租户一律不可见
- [x] 单条 SQL 完成（走既有 `ix_artifact_run` / `ix_artifact_conversation`），不做"先查全量再内存过滤"
- [x] [S-02][integration] 真实边界：真实 PG（`runtime.artifact` 逐行回读）；断言一次 Run 内的 1 个入站 pdf 与 1 个自产 markdown **都在**，字段齐全且方向可区分
- [x] [B-02][unit] 真实边界：枚举分页；断言 `limit` 取 1/50/51/空集的行为。**二选一已定：超上限一律拒绝，不夹紧**（与 `read_attachment` 的 `limit` 同口径——同一个概念两套行为，模型学不会）；分页窗口抽成纯函数 `list_window` 供直测；空集返回空列表而非错误
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 PG（runtime.artifact 逐行回读） | 入站与自产两条都在，含 id/文件名/MIME/大小/方向；两者可区分 | tests/agent_runtime/test_attachment_tools.py::test_s02_lists_inbound_and_self_produced_attachments | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |
| B-02 | unit | 枚举分页 | limit=1/50/51/空集行为正确；空集返回空列表而非错误 | tests/agent_runtime/test_attachment_tools.py::test_b02_listing_paging_boundaries + ::test_b02_empty_scope_returns_an_empty_list_not_an_error | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |

### Acceptance Evidence

**执行（2026-10-03）**，登记命令 `uv run pytest -q tests/agent_runtime/test_attachment_tools.py`。

**RED（先写测试再实现）**：`uv run pytest -q tests/agent_runtime/test_attachment_tools.py -k "b02 or s02"`
→ `ImportError: cannot import name 'LIST_ATTACHMENTS_TOOL'`（新符号尚不存在，预期失败）。

**GREEN**：同一文件 **18 passed in 0.39s**（TASK-002 的 14 条 + 本任务 4 条，无回归）。`ruff check` 全绿；`mypy` 干净。

**两个设计选择（二选一已固定，写进 Checklist）**：
1. **`limit` 超上限一律拒绝**，不夹紧 —— 与 `read_attachment` 的 `limit` 同口径。
2. **方向按 `artifact_type` 判定**（出站只有 `AGENT_OUTPUT` 一种），**不按"有没有 `run_id`"** —— 后台任务的自产产物 `run_id` 为空，按 run 判会把它误报成入站。

- S-02: verified —— 同一会话内 1 个入站 pdf + 1 个自产 markdown，两条都在且 id/文件名/MIME/大小/方向齐全；方向分别为「入站」/「自产」。**并做了闭环断言**：列出的 id 原样喂回 `read_attachment` 能读回内容（否则"跨多轮继续用某个附件"仍然断链）。
- B-02: verified —— 纯函数 `list_window`：缺省 `(0,20)`、`limit=1`→`(0,1)`、`limit=50&offset=7`→`(7,50)`、`limit=51`/`limit=0`→`ATTACHMENT_LIMIT_INVALID`、`offset=-1`→`ATTACHMENT_OFFSET_INVALID`；空集查询返回「0 条」且**不编造条目**、不报错。

**超出场景、另行覆盖**（与 `search_attachment` 同样的做法——设计 §3.4 要求"参数非法 → 明确错误码"，但无对应验收场景）：新增 `test_listing_rejects_unknown_filters`，断言未知 `scope`/`direction` 报 `ATTACHMENT_SCOPE_INVALID`/`ATTACHMENT_DIRECTION_INVALID`，**不静默当成空集**（静默会让模型以为"确实没有附件"而转为凭记忆编）。
- S-02: verified — automated command passed; run_id=79c07fb4e7024e2393cd085eee9d077e (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=79c07fb4e7024e2393cd085eee9d077e (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 二选一固定：limit 超上限一律拒绝（与 read_attachment 同口径）；方向按 artifact_type 判定而非按 run_id 有无
- [2026-10-03] 先写测试拿 RED（`ImportError: LIST_ATTACHMENTS_TOOL`），再实现；18 passed、ruff/mypy 全绿
- [2026-10-03] completed (done)

---

## TASK-004: 入站回执（与拒绝反馈合并为至多一条）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.1 功能清单`, `#2.5.1 业务规则与约束`
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-04, E-02

### Description

用户发完附件后，除了模型回答**没有任何回执**：全收下时静默；部分拒绝时用户不知道哪些收下了。本任务在网关侧补回执，并与既有"拒绝反馈"**合并为至多一条**消息（不得对同一条入站消息产生两条用户可见反馈）。

### Checklist

- [x] 收到附件后按接收结果生成回执：全部接收 / 部分接收（含被拒数量与原因）/ 全部拒绝（只发拒绝说明，不叠加回执）
- [x] 与既有拒绝反馈路径合并：同一条入站消息**最多一条**附件相关反馈
- [x] 文案经消息目录取，`config/api-messages.yaml` 补 zh-CN 与 en-US 词条；数值（如上限）仍只有一处来源
- [x] [S-04][E2E] 真实边界：真实 WS 探针 → 真实网关 → 真实渠道帧；断言用户收到**一条**"已收到 2 个、1 个未接收及原因"的回执，且仅此一条附件相关反馈 —— **已编写并登记为 `e2e_deferred`，按协议不在本阶段执行**
- [x] [E-02][E2E] 真实边界：真实 WS 探针 → 真实网关；断言全部附件被拒时只发一条拒绝说明（不出现两条消息）—— 同上
- [x] 新增 `tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py`（**本任务自己建立该目录与文件**，不依赖 TASK-010；命令只指向本文件，指向目录会在别处文件尚未存在时报错）—— 目录 + `conftest.py`（复用 `im_gateway.environment` 原语）+ 本文件
- [x] 运行 verifier：`uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`（`harness-i18n#RULE-i18n-001`）；记录输出 —— **6 passed + `i18n keys OK: 717`**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 WS 探针 → 真实网关 → 真实渠道帧 | 用户收到一条含"已收到 N 个、M 个未接收及原因"的回执；仅此一条附件相关反馈 | tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py::test_s04_partial_acceptance_sends_exactly_one_merged_receipt | uv run pytest -q tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py | verified |
| E-02 | E2E | 真实 WS 探针 → 真实网关 | 全部被拒时只有一条拒绝说明，不叠加回执 | tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py::test_e02_all_rejected_sends_only_the_rejection_not_a_receipt | uv run pytest -q tests/acceptance/attachment_round_trip/test_inbound_receipt_e2e.py | verified |

### Acceptance Evidence

**执行（2026-10-03）**。S-04/E-02 是 E2E，按协议**只编写不执行**（登记 `e2e_deferred`，由需求级 `cf-task:verify-e2e` 统一跑）。

**实现**：`inbound.py` 的 `_materialize` 里，原先是「有拒绝码才发一条拒绝说明、否则静默」，现在三种形态共用一个出口 `_receipt_text`：
- 全收下 → `ATTACHMENT_RECEIPT_ALL`（**原先静默**，用户不知道东西到没到）；
- 部分接收 → `ATTACHMENT_RECEIPT_PARTIAL`，原因经 `{reason}` 注入**取**自拒绝码自己的文案（不另写一份，数值仍只有一处来源）；
- 全拒 → 仍只发拒绝说明，**不叠加**回执。

**必须同时改的两处（否则改动不生效或不合法）**：
1. `packages/api-kit/.../error_codes.py` —— 目录码与 `ErrorCode` 枚举**双向一一对应**（`tests/test_error_catalog.py` 断言），新增词条必须同时登记；
2. `tests/gateway/test_inbound_attachment_flow.py` 的两条既有断言 —— 它们钉的正是**旧行为**（部分接收只回拒绝原因），RULE-04 要求合并，期望值必须跟着变成合并回执。这不是"改测试凑实现"：新期望里仍然断言原因原文与上限数值出现，只是外面套了回执。

**验证**：`tests/test_error_catalog.py` + `tests/gateway/test_inbound_attachment_flow.py` + `tests/gateway/test_attachment_gate.py` 共 **23 passed**；i18n verifier **6 passed + `i18n keys OK: 717`**；`ruff` 全绿；`mypy` 干净。新增网关层用例 `test_s04_all_accepted_sends_exactly_one_receipt` 覆盖"全收下"这条**此前无任何覆盖**的新分支。E2E 两文件 `--collect-only` 通过（imports/conftest 可解析）。

**E2E 与设计稿的一处偏离（如实登记）**：设计 S-04 前置条件写「1 个**超限**」，E2E 改用**类型不在白名单**来制造"部分接收"——超限要造 50 MiB 夹具并真的下载到中止，而该路径已由兄弟用例 `tests/acceptance/im_gateway/test_wecom_attachments.py::test_e02_oversized_file_reply_carries_the_limit_and_an_audit` 覆盖并断言上限数值；本用例要验的是**回执的合并语义**，与"因为什么被拒"无关。

**踩到的坑**：en-US 词条里 `accepted: {reason}` 的 ASCII 冒号+空格被 YAML 当成映射（`ScannerError`）⇒ 该行必须加引号。
- S-04: e2e_deferred — automated command e2e_deferred; run_id=f2eac4ea720149fbaa6ee0069af0e882 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=f2eac4ea720149fbaa6ee0069af0e882 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 实现合并回执（`_receipt_text` 三形态共用一个出口）+ 消息目录新增两码 + 枚举登记
- [2026-10-03] 更新两条既有断言：部分接收的期望由"只回拒绝原因"改为合并回执（RULE-04 的直接后果）
- [2026-10-03] 新增 `test_s04_all_accepted_sends_exactly_one_receipt` 覆盖"全收下"新分支
- [2026-10-03] 新建 `tests/acceptance/attachment_round_trip/`（conftest + E2E）；23 passed / i18n 6 passed / ruff / mypy 全绿
- [2026-10-03] completed (done)

---

## TASK-005: 写与交付语义分离 + `append_artifact`

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.6 出站交付：写与发的分离（硬需求落点）`, `#3.1 方案选型`
- **Spec-Refs**: harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-03

### Description

`write_artifact` 现在既"写"又隐含"可交付"：没有交付路由时它**直接拒绝**（`apps/agent-runtime/src/muad_agent_runtime/application/attachment_tools.py:214-221`），于是后台任务、控制台触发的 Run 连"写"都做不了——而"写"本身只需要 Run 上下文（产物落共享 store + DB 行）。本任务把两者**在实现上拆开**：`write_artifact` 只写；新增 `append_artifact` 供分段写长文档（模型单轮输出有 token 上限，没有追加就写不出长文档）；交付动作留给 TASK-006 的 `deliver_artifact`。

**不可变约束**（`harness-skill#RULE-skill-001`）直接决定方案：同一 `storage_key` 二次写入必须抛错 ⇒ 追加写**新 key**，artifact 行指向最新版本，历史版本键记在既有 `metadata_json`，**不改 schema**。

### Checklist

- [x] `write_artifact` 去掉"缺交付路由即拒绝"的守卫：只依赖 Run 上下文；返回文案明确提示"产物已写出（id=…）；如需交给用户请调用 `deliver_artifact`"
- [x] 新增 `append_artifact(artifact_id, content)`：只能追加**本 Run 自产**的产物；每次追加写**新 `storage_key`**（原子写：临时文件 + `os.replace`），artifact 行指向最新版本并把历史版本键写入 `metadata_json`；**同 key 二次写入必须抛 `FileExistsError`**
- [x] 产物大小上限（既有 `MAX_READ_BYTES`）对写出与追加一视同仁；超限**明确拒绝**且**不破坏已有内容** —— 追加超限在**落任何字节之前**就拒绝（先合并计算、再写）
- [x] 产物与追加写**不进入 Run 快照冻结范围**（快照只冻 Agent/Model/Skill/MCP/Prompt/catalog），冻结语义不变
- [x] [B-03][unit] 真实边界：出站产物大小 + **不可变性**（真实文件系统）；断言等于上限接收、上限 + 1 B **明确拒绝**且已有内容不被破坏、**同一 `storage_key` 二次写入抛 `FileExistsError`**（RULE-01）
- [x] 运行 verifier：`uv run pytest -q tests/test_skill_artifact_cache.py`（`harness-skill#RULE-skill-001`）；记录输出 —— **4 passed**
- [x] 运行 verifier：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`（`harness-snapshot#RULE-snapshot-001`）；记录输出 —— **4 passed + 20 passed**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|-------------------|---------|----------------|---------|------|
| B-03 | unit | 出站产物大小 + 不可变性（真实文件系统） | 等于上限接收；超过上限明确拒绝且已写内容不被破坏；**同 key 二次写入抛 `FileExistsError`** | tests/agent_runtime/test_attachment_tools.py::test_b03_store_write_is_immutable + ::test_b03_write_artifact_respects_the_size_limit + ::test_b03_append_versions_the_key_and_never_breaks_existing_content | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |

### Acceptance Evidence

**执行（2026-10-03）**，登记命令 `uv run pytest -q tests/agent_runtime/test_attachment_tools.py`。

**RED（先写测试再实现）**：`uv run pytest -q tests/agent_runtime/test_attachment_tools.py -k "b03 or append or delivery_route"`
→ `ImportError: cannot import name 'APPEND_ARTIFACT_TOOL'`（预期失败）。

**GREEN**：同一文件 **22 passed**；`tests/agent_runtime` 全量 + `tests/gateway/test_inbound_attachment_store.py` + `tests/acceptance/test_foundation_artifact.py` 共 **231 passed**（无回归）；`ruff` 全绿；`mypy` 干净。

**一处超出任务原文的实现决定（已记录）**：不可变写入原语放在**共享包** `packages/artifact-store/src/muad_artifact_store/nfs.py` 的 `NfsArtifactStore.write()`，而不是在 `attachment_tools.py` 里再造一份。理由：`RULE-skill-001` 把「写入不可变」定为**存储层契约**，而 console 侧 `skill_artifact_store.write_artifact` 已经实现过一份同样的语义 —— 再抄第三份就是三处会各自漂移的定义。只**新增方法**，不改既有行为（console/worker/gateway 的调用点未动）。**残留**：console 那份仍独立存在，收敛它不在本任务范围。

- B-03: verified —— 三部分：① `NfsArtifactStore.write` 同一 key 二次写入抛 `FileExistsError` 且首份内容**原样**（真实文件系统）；② `write_artifact` 恰好等于上限**接收**、上限 + 1 字节 → `ATTACHMENT_TOO_LARGE`；③ 追加写**换新 key**（行指向 v2、v1 字节原样留存、最新版是全文），超限追加 → `ATTACHMENT_TOO_LARGE` 且**已有内容与 DB 行都不动**。

**行为反转的一处既有测试**：`test_write_artifact_without_delivery_route_errors_explicitly` 钉的正是本任务要**拆掉**的守卫（缺交付路由即拒绝），已改写为 `test_write_artifact_succeeds_without_a_delivery_route`（断言无路由也能写、且返回值提示 `deliver_artifact`）。这是需求的直接后果，不是为了凑实现。

**另加一条超出场景的覆盖**：`test_append_rejects_artifacts_this_run_did_not_produce` —— 入站附件的字节属于用户原始文件，被 Agent 改写就再也回不到原件了。
- B-03: verified — automated command passed; run_id=a54e55aeeb664ed88a4ccbb9ab1483e3 (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 不可变写入原语加到共享 `NfsArtifactStore.write()`（只新增方法，不动既有调用点）
- [2026-10-03] `write_artifact` 去掉交付路由守卫；新增 `append_artifact`（版本化 key + metadata_json 记历史）
- [2026-10-03] 改写一条既有断言（行为反转：无交付路由也能写）
- [2026-10-03] 22 passed / 231 passed（含 agent_runtime 全量）/ 两条 required verifier 全绿 / ruff / mypy
- [2026-10-03] completed (done)

---

## TASK-006: 出站交付链：契约形态 + 显式交付 + 会话内/后台两条投递路径

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-005, TASK-007
- **Source**: `attachment-round-trip.design.md#3.4 接口设计`, `#3.6 出站交付：写与发的分离（硬需求落点）`, `#3.2 架构设计`
- **Spec-Refs**: harness-im#RULE-im-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: E-06

### Description

这是硬需求的落点：**写完文件之后，Agent 必须能把它发送给用户**。五段工作——① 契约扩展：`DeliveryMessage` 增加产物形态（渠道中立，复用 `AttachmentRef` 同型引用），`DeliveryRequest` 支持**会话形态**（`task_id` 可省、`delivery_key = run:{run_id}:{artifact_id}`），对既有 worker 调用向后兼容；② 新增显式交付工具 `deliver_artifact(artifact_id, note?)`，runtime **同步 POST 网关 `/internal/deliveries`** 并拿到真实结论；③ 网关侧既有 `reserve → 发送 → mark` 不变，`type=artifact/image` 时调适配器的**可选出站能力**（直发，或降级为 TASK-008 的签名取件链接）；④ 后台任务路径复用同一端点（`task:{task_id}:final` 幂等键不变），**发送失败必须释放投递占位**（否则任务被误判已送达）；⑤ 交付结果经 TASK-007 的内部端点写审计。

**为什么是同步调用而不是异步事件**（设计 §3.1 ADR）：网关是 SSE 的**纯消费方**——runtime 只有 `/runs`、`/resume`、`/cancel`、`GET /runs/{id}`，**没有回执通道**；异步化会让模型拿不到交付结论，"失败可重试/降级为链接"都无法诚实表达，E-06 按原设计不可实现。同步调用复用网关既有的幂等与失败释放占位，**一条交付契约两个调用方**（worker 已在用）。**代价**：Run 多一次渠道往返——必须定义独立超时，且**超时按失败（未知）处理，绝不拆成成功**。

**依赖 TASK-007 的原因**：E-06 断言"审计记 FAILED"需要审计表与内部端点先存在；反向地，TASK-007 的 E-04 只验审计写入本身（见该任务）。

**TASK-001 探针结论（2026-10-03，已落定）**：**直发分支成立**——企微可直发文件与图片，唯一通路 `media_id`（`url` 与内联 `base64` 均被 `40058` 拒），`media_id` 来自**三步分片上传** `aibot_upload_media_{init,chunk,finish}`（分片 ≤512 KiB、≤100 片，约 50 MB 上限；`chunk_index` 0-based；`media_id` 有效 3 天）。**Python SDK（`aibot` 1.0.2，PyPI 最新）没有上传能力**（只有 `download_file`）⇒ 三步上传需在 `channels/wecom/` 内自行实现。降级为签名链接的条件因此收窄为「>≈50 MB」或类型不受支持，不再是"企微可能不支持"。另：**渠道不做幂等**（同 `media_id` 重发两次，用户侧出现两条），去重只能靠 `(tenant_id, artifact_id, route_key)` 唯一键。事实表见设计 §3.6「真机探针结论」。

### Checklist

- [x] `DeliveryMessage` 增 `type` 取值 `artifact`/`image`（缺省仍 `text`，向后兼容）；`type != text` 时 `artifact: AttachmentRef` 必填，**不含任何渠道私有发送凭据**
- [x] `DeliveryRequest` 支持会话形态：`task_id` 改为 `UUID | None = None`，`delivery_key` 两种形态互斥校验（有 `task_id` → 仍是 `task:{task_id}:final`；无 → `run:{run_id}:{artifact_id}`）；**既有 worker 调用零改动**
- [x] `DeliveryResponse` 增 `outcome`（`DELIVERED`/`DEGRADED`）与 `fallback_url`（仅降级时）；`delivered=false` 仍表示"未确认送达"，不得当成功
- [x] 新增工具 `deliver_artifact(artifact_id, note?)`：校验归属（本 Run/会话 + 租户）与交付路由存在；schema **没有渠道字段** —— **偏离已登记**：实际只落 `artifact_id`，**没有 `note`**。设计把它列进了请求字段，却没定义它在媒体路径上怎么被消费，而企微的图片/文件消息**没有文本槽**；给模型一个按了没反应的旋钮比不给更糟（它会以为那句话附上去了）。要支持"文件 + 一句话"应由适配器另发一条跟随文本，那是独立的一次改动。设计稿与本行原措辞已按代码事实订正
- [x] 工具结果明确回传三种结论：已交付（含文件名）/ 降级为签名链接（含链接）/ 失败（含原因码 + "产物已保留，可重试"）；**超时按失败**
- [x] runtime 侧新增交付客户端：**同步 POST `/internal/deliveries`**（用既有 `settings.im_gateway_url`），**独立超时**配置；超时/传输错误 → 报失败并保留产物，**不得**当成成功
- [x] 适配器侧新增**可选出站能力协议**（与入站 `AttachmentSource` 同构）：核心域只给"产物引用 + 路由"，适配器决定直发/链接/降级；降级时由适配器调用 TASK-008 的取件能力生成签名链接 —— **本次收口**：协议 → `channels/base.py` 的 `ArtifactLinkIssuer` + `OutboundArtifactDelivery.deliver_artifact(..., tenant_id=)`；实现 → `ConsoleClient.issue_fetch_link`（真 HTTP 打 TASK-008 的签发端点）；触发 → 企微侧**唯一**不成立的条件是渠道硬上限（`WeComMediaUploadTooLargeError`），适配器降级后**把链接作为文本发给用户**并返回 `DEGRADED` + `fallback_url`。签发口缺席或签不出来 ⇒ **显式失败**，绝不自己拼链接（用户会点开 404）
- [x] `channels/wecom/` 内实现**三步分片上传**（SDK 无此能力，见上方探针结论）：`init → chunk ×N → finish` 拿 `media_id`，再以 `image`/`file` 体发出（会话内 `aibot_respond_msg`、主动 `aibot_send_msg`）；分片 ≤512 KiB、≤100 片，超出走降级；`media_id` 3 天失效 ⇒ **跨 3 天的重试必须重新上传**，不能只重发
- [x] 图片出站（FEAT-10，P0）：`type=image` 走同一交付链；`view_image` 是入站方向的重看，**不是**同一件事
- [x] 后台路径：`/internal/deliveries` 支持产物形态；沿用既有 `reserve → 发送 → mark`，**失败释放占位**
- [x] 交付链全程**不见渠道形状**：核心域与网关应用层零渠道字样与发送凭据（守卫会判红）
- [x] [E-06][integration] 真实边界：真实 HTTP（网关交付端点 + 渠道侧失败注入）；断言首次交付失败时工具结果显式报失败与原因、**产物保留**、审计记 FAILED；按同一幂等键重试后成功（审计同一行转 DELIVERED）且用户恰好收到一次
- [x] 运行 verifier：`uv run pytest -q tests/console_channel tests/gateway`（`harness-im#RULE-im-001`）；记录输出 —— **340 passed**
- [x] 运行 verifier：`uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py`（`harness-worker#RULE-worker-001`）；记录输出 —— **239 passed**（首次 3 failed，见下方说明，复跑全绿）/ **217 passed**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-06 | integration | 真实 HTTP（网关交付端点 + 渠道侧失败注入） | 首次失败 → 显式报失败与原因 + 产物保留 + 审计 FAILED；同幂等键重试成功、审计同一行转 DELIVERED、用户恰好收到一次 | tests/gateway/test_artifact_delivery.py::test_e06_failed_delivery_then_retry_succeeds_and_the_user_gets_it_once + ::test_channel_side_failure_releases_the_placeholder_so_a_retry_can_send + ::test_failed_delivery_is_audited_as_failed_with_a_reason_code + ::test_successful_delivery_writes_one_audit_row + ::test_degraded_delivery_is_reported_as_delivered_with_the_link + ::test_degraded_delivery_is_audited_as_degraded_not_delivered + ::test_artifact_message_goes_through_the_optional_capability + ::test_adapter_without_the_capability_fails_explicitly + ::test_audit_write_failure_does_not_undo_a_completed_delivery | uv run pytest -q tests/gateway/test_artifact_delivery.py | verified |

### Acceptance Evidence

**阻塞已解除（2026-10-03）**：清单第 7 条原先依赖 TASK-008 的取件能力，先做 008 后回来收口。TASK-008 已
Done Gate pass（取件端点 + 签发面），降级链本次落地。

**本次收口做了什么**：

- `channels/base.py` 新增 **`ArtifactLinkIssuer`** 端口（`issue_fetch_link(artifact_id, *, tenant_id) -> str | None`），
  并把 `OutboundArtifactDelivery.deliver_artifact` 扩成 `(route, artifact, *, tenant_id)`。
  租户是**签发链接的作用域**，不是渠道形状——设计 §3.6 那句"核心域只给产物引用 + 路由"挡的是
  url/aes_key/media_id 这类**渠道发送体**上行，租户不在其中；交付链的审计写入本来就显式带着它。
- 实现落在 `ConsoleClient.issue_fetch_link`（真 HTTP POST 到 TASK-008 的 `/internal/artifacts/{id}/fetch-link`），
  **签不出来返回 `None` 而不抛** —— 让适配器去认 Console 的错误码形状正是 `RULE-im-002` 要挡的耦合。
  签名与端口逐字一致，`ConsoleClient` 因此**直接满足**适配器要的端口，中间不套转接层。
- 企微适配器里**唯一**会触发降级的条件是渠道硬上限（`WeComMediaUploadTooLargeError`，512 KiB × 100 片 ≈50 MB，
  TASK-001 真机实测）。降级后**把链接作为文本发给用户**（`send_text`／会话内 `reply_text`，与文本同一条 40008 规矩），
  并返回 `DEGRADED` + `fallback_url`。链接文本只有 URL 本身：渠道层没有 locale、拿不到消息目录，
  在这里拼一句中文等于把用户可见文案钉死在一个语言上；"这是什么"由模型的回复交代。
- **签发口缺席 / 签不出来 ⇒ 显式失败**，不自己拼一条像链接的串——拼出来的后果是用户点开 404，
  而且它看起来"成功了"。

**执行（2026-10-03）**：

- E-06 登记命令 `uv run pytest -q tests/gateway/test_artifact_delivery.py` → **9 passed**
- `tests/gateway` 全量 → **288 passed**（含降级新增 4 条：降级成功 / 降级审计记 `DEGRADED` / 无签发口失败 / 签不出来失败）
- `tests/gateway/test_gateway_console_client.py` 新增签发客户端 2 条（解析成功 + 三种失败返回 `None`）→ 通过

**RED（先写测试再实现）**：`git stash push -u` 暂存网关侧实现后跑降级用例 →
`TypeError: WeComAdapter.__init__() got an unexpected keyword argument 'fetch_links'`（预期失败）；
`git stash pop` 复原后转 GREEN。

**两条 required verifier**（清单要求）：`harness-im` `tests/console_channel tests/gateway` → **341 passed**；
`harness-worker` `tests/agent_worker` → **239 passed**、`tests/agent_runtime --ignore=test_runner_executor.py` → **217 passed**。

> **首次跑 agent_worker 时有 3 条失败**（`test_b115_two_schedulers_create_exactly_one_task`、
> `test_b115_repeated_run_for_same_fire_time_creates_nothing`、`test_running_worker_stops_on_cancel_request`），
> **复跑全绿**。这三条都是**等真实时间**的租约/调度用例，且失败发生在第一轮（耗时 34.4s，复跑 10.6s）——
> 本机当时有一个 **dev 网关服务（`--reload`，:8003）在跑**，与单测共用同一个 dev 库（`tests/agent_worker`
> 不走验收的隔离库）。按项目约定"单跑通过、整跑偶发失败先怀疑环境残留"，复跑一次取证；**不是本次改动引入**
> （改动全在 im-gateway，这三条不经过它）。

- E-06: verified — automated command passed; run_id=34e07511176c49fc9a02e65c702ed90d (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] blocked (降级链依赖 TASK-008 的取件能力（清单第 7 条：不能直发时由适配器调用取件能力生成签名直链）。006 其余各项已完成并通过验证；先做 008，完成后 resume 006 收口。)
- [2026-10-03] resumed (draft)
- [2026-10-03] 降级链落地：ArtifactLinkIssuer 端口 + ConsoleClient 签发 + 企微适配器降级发链接；E-06 9 passed / gateway 288 passed / harness-im 341 passed / harness-worker 239+217 passed
- [2026-10-03] completed (done)

---

## TASK-007: 交付审计落点（表 + console 内部端点）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.3 数据设计`, `#3.4 接口设计`
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-api#RULE-api-002, harness-data#RULE-data-001
- **Acceptance-Refs**: E-04

### Description

交付必须有留痕："谁在何时把哪个产物交付给哪个路由、结果如何"。**网关不持库**（架构测试守住）⇒ 与上期入站审计同路：新增 `control.artifact_delivery_audit` 表 + console 内部端点写入。

**本表的形状与上期入站审计表刻意不一致**：用 `channel + route_key`（适配器产出的可读不透明串）而不是 `channel + bot_id + external_user_id`。理由是需求明确"Console 不放开给终端用户、未来做 web chat"——那时交付路由是 session/WebSocket，照抄企微形状会让那两列**填不出真值**（上期 B-09 ② 的坑）。上期已归档的表不动；不一致是有意的，设计 §3.3 已记录。

### Checklist

- [x] 新增 `control.artifact_delivery_audit`（`StandardColumnsMixin` + `timestamptz`）：`tenant_id`/`artifact_id`/`channel`/`route_key`/`delivery_key`/`outcome`(`DELIVERED`/`FAILED`/`DEGRADED`)/`reason_code`/`trace_id`；**无自由 JSON 列**（凭据与令牌在类型上无处可放）
- [x] 索引：`(tenant_id, create_time DESC)`、`(artifact_id)`、`(tenant_id, channel, route_key)`、`(delivery_key)`，以及 **`uq_artifact_delivery_audit_target` = partial UNIQUE `(tenant_id, artifact_id, route_key) WHERE is_deleted = false`**
- [x] **幂等语义（唯一键的权威定义，设计 §3.3）**：键是 `(tenant_id, artifact_id, route_key)`——对齐 RULE-07/S-10"同一**产物**对同一**路由**只交付一次"；**`outcome` 不在键里**，它是该行的**当前状态**（`DELIVERED` 为终态、不被覆盖）；**失败重试成功 = 更新同一行**而不是新增行。`delivery_key` 只作传输层留痕，不参与唯一约束
- [x] alembic 迁移：单链接在当前 head 之后，`upgrade` / `downgrade` 双跑可用；**无回填**（历史产物无交付记录——不存在的事实不伪造）—— `0016_artifact_delivery_audit.py`，实测 `0015 → 0016 → 0015 → 0016`
- [x] `POST /internal/channel/artifact-delivery`（`InternalServiceDep` + `HeaderTenantId` + `ok(catalog,…)` 封套）；写入走 `ON CONFLICT (tenant_id, artifact_id, route_key) DO UPDATE ... WHERE outcome <> 'DELIVERED'` + 回查
- [x] 不新增部署单元、网关不持库（审计经 console 内部端点写）
- [x] [E-04][integration] 真实边界：真实 HTTP（console 内部端点）+ 真实 PG；断言以 `outcome=FAILED` 写入落一行 + 原因码、同键重写不产生第二行、`DELIVERED` 为终态不可被覆盖、凭据/令牌不在字段里
- [x] 运行 verifier：`uv run pytest -q tests/architecture`（`harness-arch#RULE-arch-001`）；记录输出 —— **13 passed**
- [x] 运行 verifier：`uv run pytest -q tests/console_skill/test_import_idempotency.py`（`harness-api#RULE-api-002`）；记录输出 —— **5 passed**
- [x] 运行 verifier：`uv run pytest -q tests -k schema_parity`（`harness-data#RULE-data-001`）；记录输出 —— **35 passed**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-04 | integration | 真实 HTTP（console 内部端点）+ 真实 PG | `FAILED` 落一行 + 原因码；同键重写不产生第二行；`DELIVERED` 为终态；字段里没有凭据/令牌 | tests/console_channel/test_artifact_delivery_audit.py::test_failed_delivery_lands_one_row_with_a_reason_code + ::test_rewriting_the_same_target_updates_the_same_row + ::test_delivered_is_terminal_and_cannot_be_overwritten + ::test_delivery_audit_has_no_place_for_credentials | uv run pytest -q tests/console_channel/test_artifact_delivery_audit.py | verified |

> **E-04 的边界为何收窄到"审计写入本身"**（设计已记录）：它的归属是**审计落点**（本任务只建表 + 端点），而"渠道失败 → 交付显式失败 → 调用方不收到已交付"的完整链路断言需要交付链存在，那属 TASK-006 的 E-06。原稿把 E-04 的边界写成"真实 HTTP（渠道发送端点）"会让本任务的 Done Gate 依赖尚未实现的上游；**收窄是把场景归位到它真正的主体（审计写入），不是降级真实边界**——E-04 仍然打真实 HTTP 与真实 PG。

### Acceptance Evidence

**执行（2026-10-03）**，登记命令 `uv run pytest -q tests/console_channel/test_artifact_delivery_audit.py` → **4 passed**。

**RED（先写测试再实现）**：用 `git stash push -u` 把实现（契约/模型/迁移/服务/端点）暂存起来跑一次
→ `ImportError: cannot import name 'ArtifactDeliveryAudit' from ...infrastructure.models.control`（预期失败）；
`git stash pop` 复原后转 GREEN。

**GREEN**：E-04 **4 passed**；相邻 `tests/console_channel` **44 passed**；三条 required verifier 全绿
（`tests/architecture` **13 passed** / `test_import_idempotency.py` **5 passed** / `-k schema_parity` **35 passed**）。

**迁移双跑**（清单要求）：`0015 → 0016 → 0015 → 0016`，`upgrade` 与 `downgrade` 都可用，结束停在 head。
迁移是**纯新增表**、无回填——历史产物没有交付记录，"补一批行"是把不存在的事实伪造成数据。

**落点**：契约 `ArtifactDeliveryAuditRequest` + `DeliveryAuditOutcome`（`packages/contracts`）；模型
`ArtifactDeliveryAudit`（console `control.py`）；服务 `artifact_delivery_audit_service.py`；端点
`POST /internal/channel/artifact-delivery`（与上期 `/internal/channel/audit` 同门控口径）。

**为什么这张表与上期入站审计表刻意不同形**（设计 §3.3 已记录）：路由用 `(channel, route_key)`
而不是 `channel + bot_id + external_user_id`。`route_key` 是适配器产出的**可读不透明串**，
接 web chat 时那一列仍填得出真值，而渠道私有的两列会当场填不出。
- E-04: verified — automated command passed; run_id=557f25c0fc8e407799b1ae41d4e540b1 (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 契约 + 模型 + 迁移 0016 + 服务 + 内部端点；迁移 upgrade/downgrade 双跑验证
- [2026-10-03] E-04 4 passed；三条 required verifier 全绿；相邻 console_channel 44 passed
- [2026-10-03] completed (done)

---

## TASK-008: 产物取件能力（签名短 TTL 直链 + 鉴权端点）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-api#RULE-api-001, harness-secret#RULE-secret-001, harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-09, E-03

### Description

**渠道无关的取件能力**：Console、未来 web chat、以及"渠道不能直发文件"时的降级链接，**复用同一个鉴权端点与同一套令牌模型**。终端用户取件**只能是签名短 TTL 直链**——IM 终端用户是 `platform_user`，Console 登录主体是 `console_account`（ADMIN/BUILDER），两套身份，给终端用户 Console 页面等于给一扇打不开的门。

### Checklist

- [x] `GET /api/v1/artifacts/{artifact_id}/content`：鉴权二选一（Console 会话态 / `?token=…` 签名令牌：单产物 + 短 TTL + 可撤销，仅存内存或短 TTL 存储）
- [x] 响应为二进制流（`Content-Type` 取产物 `media_type`、`Content-Disposition` 带原文件名）；**不得把 `storage_key` 或存储路径暴露给客户端**
- [x] 无权限 / 令牌失效 / 不存在**一律 404**（与不存在同样响应，不泄露存在性）
- [x] 签名令牌生成与校验：**令牌不进日志、不进审计字段**（`harness-secret#RULE-secret-001`）
- [x] 降级链接由 TASK-006 的适配器在"不能直发"时使用；本任务只提供能力，不含渠道判断
- [x] [S-09][E2E] 真实边界：真实 HTTP 取件端点 + 真实鉴权（非 mock）；断言签名令牌与 Console 会话两条路径都拿到**字节与原文件一致**的内容；令牌过期/跨租户一律 404
- [x] [E-03][integration] 真实边界：真实 PG + 真实存储 + 鉴权层；断言以租户 B 请求租户 A 的产物 id 被拒且**不泄露存在性**
- [x] 新增 `tests/acceptance/attachment_round_trip/test_artifact_fetch_e2e.py`（**本任务自己建立该目录与文件**，不依赖 TASK-010）
- [x] 运行 verifier：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`（`harness-api#RULE-api-001`）；记录输出 —— **18 passed**
- [x] 运行 verifier：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`（`harness-secret#RULE-secret-001`）；记录输出 —— **12 passed**
- [x] 运行 verifier：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity`（`harness-auth#RULE-auth-001`）；记录输出 —— **2 passed (1 deselected) / 35 passed (1838 deselected)**
- [x] 运行验收命令并填写 Acceptance Evidence

### 补充：签发侧的 HTTP 面（设计稿的缺口，本任务补上）

设计 §3.4 只写了**取件**端点（API-05），但清单第 5 条要求"降级链接由 TASK-006 的适配器…**调用 TASK-008 的取件能力生成签名链接**"——而适配器在 **im-gateway 进程**里，令牌的权威在 **Console 进程内存**里，两者之间没有任何共享存储。少一个 HTTP 面，这句话无法落地：适配器只能造假令牌，或让用户收一条注定 404 的死链。

因此本任务补一个内部端点 **`POST /internal/artifacts/{artifact_id}/fetch-link`**（`InternalServiceDep` + `HeaderTenantId`），返回 `{"url": …}`：

- **签发前先真解析一遍**（与取件端点**同一条** `ArtifactFetchService.fetch`）：否则会为取不到的产物签出一条注定 404 的链接，再被当成"降级成功"回给用户——那正是 RULE-03 要禁的谎报。
- **`url` 的基址取 `console_platform_url`**，不新增配置项：这个值本来就是"别人怎么找到 Console"。
- 这条路径**同时是 S-09 令牌分支的唯一合法入口**：验收里 Console 是独立进程，测试拿不到它的内存，没有这个端点就只能造令牌。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | E2E | 真实 HTTP 取件端点 + 真实鉴权（非 mock） | 两条鉴权路径都拿到字节与原文件一致的内容；令牌过期/跨租户一律 404 | tests/acceptance/attachment_round_trip/test_artifact_fetch_e2e.py::test_s09_console_session_fetch_returns_the_original_bytes + ::test_s09_signed_token_fetch_returns_the_original_bytes + ::test_s09_cross_tenant_is_indistinguishable_from_missing + ::test_s09_expired_token_is_indistinguishable_from_missing | uv run pytest -q tests/acceptance/attachment_round_trip/test_artifact_fetch_e2e.py | e2e_deferred（本地已 GREEN，终验归 verify-e2e） | verified |
| E-03 | integration | 真实 PG + 真实存储 + 鉴权层 | 跨租户请求被拒且与不存在同样响应（不泄露存在性） | tests/console_channel/test_artifact_fetch.py::test_cross_tenant_looks_exactly_like_missing + ::test_issuing_a_link_is_scoped_to_the_calling_tenant + ::test_no_credential_at_all_is_also_a_404 + ::test_expired_token_is_refused_like_a_missing_one + ::test_a_token_only_opens_the_artifact_it_was_issued_for + ::test_console_session_path_returns_the_original_bytes + ::test_signed_token_path_returns_the_original_bytes + ::test_response_never_carries_the_storage_key + ::test_the_plaintext_token_never_reaches_the_logs | uv run pytest -q tests/console_channel/test_artifact_fetch.py | verified |

### Acceptance Evidence

**执行（2026-10-03）**，登记命令：

- `uv run pytest -q tests/console_channel/test_artifact_fetch.py` → **9 passed**
- `uv run pytest -q tests/acceptance/attachment_round_trip/test_artifact_fetch_e2e.py` → **4 passed（26.8s，含 11s 真实过期等待）**

**RED（先写测试再实现）**：`git stash push -u` 暂存实现（取件路由 / 签发路由 / 两个依赖 / 路由注册）
后跑 E-03 → `ImportError: cannot import name 'get_artifact_fetch_service' from
'muad_console_platform.api.deps'`（预期失败）；`git stash pop` 复原后转 GREEN。

**GREEN 的相邻回归**：`tests/console_channel + tests/console_auth + tests/console_platform +
tests/architecture + tests/gateway` 合计 **518 passed**；`uv run mypy apps/console-platform` **Success: no issues found in 96 source files**；全仓 `ruff check` 全过。

**三条 required verifier**（清单要求）：`harness-api` **18 passed** / `harness-secret` **12 passed** /
`harness-auth` **2 passed (1 deselected) + 35 passed (1838 deselected)**。

**E-03 的归属校验没有打桩**：`ArtifactResolvePort` 指向 **runtime 真 app** 的 ASGI 传输
（`/internal/artifacts/{id}`，与 worker 后台路径**同一个解析单点**）。用假解析会让"跨租户被拒"
变成在测自己写的 if。

**S-09 里两处刻意的真实等待与真实进程**：

① 令牌过期**真的等** 11 秒（栈的 TTL 由 `ARTIFACT_FETCH_TTL_SEC` 压到 10s，手法与
`delivery_backoff_base_sec` 同：让"等真实时间"的代价可承受，而代码路径一字不改）；用假时钟就
等于把要验的东西验掉了。

② 栈里新加了两个 Console 侧下游地址（`AGENT_RUNTIME_URL` / `CONSOLE_PLATFORM_URL`）——
这是 **console → runtime 的第一条调用边**，在此之前那两个默认值从没被真正用到过，不设置会
**安静地**打 `.env` 里的 8000/8001：表现为 502，或"所有产物都取不到"。这条排查代价直接换来了
一条提前断言（取件链接必须以本栈 console 地址开头），把 502 换成一句能读懂的话。

**顺带修掉的真漏洞**：`muad_logging.redaction` 的敏感键名覆盖了 `access_token`/`refresh_token` 等，
**却没有裸的 `token`** —— 而本端点要求 S-09 令牌落在 `?token=` 上，那会**原样进访问日志**。
修的是**策略**（补键名）而不是"把参数改名叫 `access_token`"（那只是让下一个人换个名字再踩一次）。
`redact_text('…?token=SECRET123')` → `'…?token=***'`，`csrf_token_count: 5` 不受影响（边界正确）。

- S-09: e2e_deferred — automated command e2e_deferred; run_id=dba4b875820a4d1aad317c82bbc15d86 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=dba4b875820a4d1aad317c82bbc15d86 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 取件端点 + 签发端点 + 令牌模型接入 + redaction 补裸 token；E-03 9 passed / S-09 4 passed
- [2026-10-03] 三条 required verifier 全绿；相邻 518 passed；mypy / ruff 全过
- [2026-10-03] completed (done)

---

## TASK-009: 产物保留期与清理

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.4 接口设计`, `#3.3 数据设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-log#RULE-log-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-08, E-05

### Description

入站/出站产物现在**只增不减**，共享 PVC 会无限增长，且没有任何地方能证明"某个文件存在过"。本任务加保留期与清理：按既有 `runtime.artifact.create_time` + 配置项判定，**不新增列**；与既有 `cleanup-skill-orphans` 同口径（宽限期保护进行中事务、dry-run 先出清单、stdout 逐条可对账）。

### Checklist

- [x] `python -m muad_console_platform.cli cleanup-artifacts`：`--grace-seconds`（默认 3600）、`--retention-days`（默认取配置，新增 `ARTIFACT_RETENTION_DAYS=30`）、`--dry-run`（只报告）、`--limit`（默认 500）—— **另加 `--tenant`（登记为对设计清单的补充，见下）**；CLI 真机演练：dry-run 出清单 → 真跑删文件与行 → 再跑为空 → 盘上该文件确实没了
- [x] 保留期判定用既有 `runtime.artifact.create_time`（`timestamptz`）与配置项，**不新增列** —— 判定拆成**两个不同的时间**：保留期取行的 `create_time`，宽限期取**文件的 `st_mtime`**（挡的是进行中的写入，口径同 `cleanup-skill-orphans`）。两者合并成一个会让"在宽限期内"与"在用"变成同一类，S-08 的三个产物就退化成两类
- [x] 删除保持可对账：文件与 DB 行同时消失，不留孤儿文件、不留悬空行；**宽限期内零删除** —— 删除顺序刻意是**先文件后行**：中途崩了留的是**悬空行**（下一轮自愈），反过来留的是**孤儿文件**（行没了就再没记录指向那个 key，谁也认不出该不该删）
- [x] 单次扫描 + `--limit` 批量删除（不做逐文件 stat 的 N+1）；结果经 CLI stdout 逐条输出便于运维核对 —— 扫描**一条 SQL** + 删行**一次批量 DELETE**；候选被 `--limit` 卡上界，全程没有"每个文件一次 DB 往返"。**偏离登记**：宽限期判定不可避免要对**候选**（≤ limit）stat 一次——那是 mtime 的来源，不是 N+1
- [x] [S-08][integration] 真实边界：真实文件系统 + 真实 PG；断言三个产物（早于保留期 / 宽限期内 / 在用）中**只**清理过期项，文件与 DB 行同时消失，另两项原地不动
- [x] [E-05][integration] 真实边界：真实文件系统 + 真实 PG；断言宽限期内文件被跳过；有 DB 行但文件缺失按"孤儿"处理且不误删在用；结果可对账
- [x] 运行 verifier：`uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py`（`harness-log#RULE-log-001`）；记录输出 —— **11 passed**
- [x] 运行 verifier：`uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity`（`harness-time#RULE-time-001`）；记录输出 —— **2 passed** / **35 passed（1849 deselected）**
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | integration | 真实文件系统 + 真实 PG | 只清理过期项；文件与 DB 行同时消失；宽限期内与在用项原地不动 | tests/console_platform/test_artifact_cleanup.py::test_s08_only_the_expired_artifact_is_removed + ::test_s08_dry_run_reports_without_deleting_anything + ::test_s08_limit_bounds_one_run_and_the_rest_is_picked_up_next_time | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | verified |
| E-05 | integration | 真实文件系统 + 真实 PG | 宽限期内跳过；行缺失按孤儿处理且不误删在用；结果可对账 | tests/console_platform/test_artifact_cleanup.py::test_e05_dangling_row_is_cleaned_without_touching_the_disk + ::test_e05_result_is_reconcilable_line_by_line + ::test_e05_a_second_run_finds_nothing_left | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | verified |

### 补充：`--tenant`（对设计 §3.4 CLI 签名的一处扩张，登记理由）

设计给的参数是 `--grace-seconds / --retention-days / --dry-run / --limit`。实现时**多加了 `--tenant`**，理由是写这条用例时被逼出来的：

`cleanup` 默认是**全库扫描**，而它是一条**破坏性**命令。S-08/E-05 是集成场景，跑在共享的开发库上——**没有租户谓词时，这条命令根本无法被安全地集成验收**：测试要么会删掉不属于本次用例的历史产物，要么就得造一个假 store 让所有别的行都被判成悬空行然后一起删掉（更糟）。

所以 `--tenant` 不是可选的锦上添花，是这条命令**能被真实验收的前提**；对运维同样成立（按租户清、按租户对账）。代价是两段 SQL 而不是一段带 `(:tenant IS NULL OR ...)` 的——后者会让优化器放弃 `create_time` 上的索引有序扫描，而这个表的量级正是靠那条索引撑住的。

### Acceptance Evidence

**执行（2026-10-03）**，登记命令 `uv run pytest -q tests/console_platform/test_artifact_cleanup.py` → **6 passed**。

**RED（先写测试再实现）**：`git stash push -u` 暂存实现（服务 / 仓储 / CLI / 配置项）后跑 →
`ModuleNotFoundError: No module named 'muad_console_platform.application.artifact_cleanup_service'`（预期失败）；
`git stash pop` 复原后转 GREEN。

**CLI 真机演练**（不是只跑单测——设计交付的是**命令**）：

```
$ ... cleanup-artifacts --tenant cli-smoke-<id> --dry-run
REMOVED artifact_id=… tenant_id=cli-smoke-<id> key=cli-smoke/<id>.txt size=6
cleanup-artifacts: scanned=1 removed=1 dangling=0 skipped=0 dry_run=true
$ ... cleanup-artifacts --tenant cli-smoke-<id>
REMOVED ... dry_run=false          # 文件与行同时消失
$ ... cleanup-artifacts --tenant cli-smoke-<id>
cleanup-artifacts: scanned=0 removed=0 dangling=0 skipped=0 dry_run=false
$ ls .data/artifacts/cli-smoke/    # 空 —— 盘上那份字节确实没了
```

**两条 required verifier**（清单要求）：`harness-log` **11 passed**；`harness-time` `tests/frontend/test_datetime_contract.py` **2 passed** + `tests -k schema_parity` **35 passed（1849 deselected）**。

**相邻回归**：`tests/console_platform + tests/console_channel + tests/test_settings.py` **198 passed**（新增配置项 `ARTIFACT_RETENTION_DAYS` 未破坏既有配置用例）；`ruff` / `mypy` 全绿。

- S-08: verified — automated command passed; run_id=b47359ae84d84cb89ff6e49343a1aa73 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=b47359ae84d84cb89ff6e49343a1aa73 (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 清理服务 + 只读仓储 + CLI（含 `--tenant`）+ 配置项；S-08/E-05 6 passed；CLI 真机演练通过；两条 required verifier 全绿
- [2026-10-03] completed (done)

---

## TASK-010: 端到端验收基线

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009
- **Source**: `attachment-round-trip.design.md#2.5.2 功能验收场景`, `#3.6 出站交付：写与发的分离（硬需求落点）`
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-03, S-06, S-07, S-10

### Description

本需求跨回调、工具、PG、共享存储、渠道帧五个边界，必须有一条**端到端**信号把它们连起来，而不是各任务自证——上一需求的经验：四个前置任务各自都绿，而"图片从未进过模型请求体"这个 P0 只有端到端才照得出来。本任务在真实栈上跑通三条主链：长文档读完再回答（S-03）、会话内交付（S-06）、后台任务交付（S-07）、交付幂等（S-10），并登记可复现命令。

### Checklist

- [x] 确认真实边界未被降级：这些 E2E 里未 mock 业务 API / DB / 落盘 / 渠道帧 —— 四条用例跑在**真实五进程栈**上（console / runtime / runtime2 / worker / gateway + 真 WS 探针 + 真 PG / Redis + 真共享 artifact store）；模型由 `tests/e2e/openai_probe_app` 承载，它既是**真实 HTTP 端点**、又是「模型实际收到了什么」的观测点
- [x] 端到端跑通「长文档 → 分段读 → 回答体现**末尾**事实」（证明不是只读了开头）
- [x] 端到端跑通「会话内写出 → 显式交付（**同步调用**）→ 用户收到文件/图片或签名链接 → 审计有记录」
- [x] 端到端跑通「后台任务完成 → 投递链 → 用户收到产物（不再是一串 UUID）」
- [x] [S-03][E2E] 真实边界：真实回调桩 → 真实落盘 → 真实工具 → 真实模型请求体；断言模型请求体里出现**后续片段**正文且最终回答含该事实 —— 夹具把「只读开头」做成**拿不到答案**（前 20 000 字符里没有任何事实），第二轮用**工具给出的翻页指引里的 offset** 续读（而不是测试自己算一个塞进去：那只能证明我算得对）。**这条用例当场照出一个真缺陷**，见下方 Evidence
- [x] [S-06][E2E] 真实边界：真实会话 → 真实产物 → **真实 HTTP 交付调用** → 渠道帧/链接；断言用户收到文件/图片（分支 1）或签名取件链接（分支 2），审计有对应记录，且**交付失败时工具结果必须报失败、不得出现「已交付」** —— 走**分支 1（直发）**：断言落在用户**实际收到的媒体帧**（`msgtype=file` + `media_id`）上，而不是「工具返回了一段成功文案」；并断言那个 `media_id` 是**三步分片上传真的走完**换来的
- [x] [S-07][E2E] 真实边界：真实 Worker 进程 → 真实网关 `/internal/deliveries` → 渠道帧；断言用户收到文件/图片或链接、投递恰好一次（重投幂等）、审计有记录 —— 真产物行 + 真字节 + 真 Task 行（`delivery_status=PENDING` + `result_artifact_id`），等 Worker 投递循环取走；Worker 只持有**不透明 id**，要经 runtime 的解析单点才发得出去，这条调用边只在真实栈上验得了
- [x] [S-10][E2E] 真实边界：真实渠道帧 + 真实 PG（审计逐行回读）；断言同一产物对同一路由交付两次时用户**只**收到一次、审计**仍只有一行** —— 一次 Run 内连发两次交付。**偏离登记**：设计还要求「第二次工具结果回『此前已交付』」，实现里**没有这个分支**（第二次命中的是传输层占位键，工具按 `DELIVERED` 文案回——**事是真的**，只是没区分「这次发的」与「此前已发」）；本次不断言那一句，记在这里供后续决定是否补这个区分
- [x] 新增 `tests/acceptance/attachment_round_trip/test_round_trip_e2e.py`（**本任务自己的文件**；目录由最先落地的 E2E 任务建立，本任务不假定它已存在） —— 四条用例 + 共用原语（等订阅、等 Run 落终态、脚本设定、模型请求体回读、审计差集）
- [x] 运行 verifier：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（`harness-test#RULE-test-001`）；记录输出 —— **acceptance 283 passed（894s）+ 前端 build 成功 + Playwright 4 passed，exit 0**（首次整跑 7 failed，修复后全绿）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | 真实回调桩 → 真实落盘 → 真实工具 → 真实模型请求体 | 模型请求体出现后续片段正文；回答含文档末尾的事实 | tests/acceptance/attachment_round_trip/test_round_trip_e2e.py::test_s03_long_document_is_read_past_the_first_segment | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | e2e_deferred（本地已 GREEN，终验归 verify-e2e） | verified |
| S-06 | E2E | 真实会话 → 真实产物 → 真实 HTTP 交付调用 → 渠道帧/链接 | 用户收到文件/图片或签名链接；审计有记录；失败时不谎报已交付 | tests/acceptance/attachment_round_trip/test_round_trip_e2e.py::test_s06_agent_writes_an_artifact_and_delivers_it_in_session | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | e2e_deferred（本地已 GREEN，终验归 verify-e2e） | verified |
| S-07 | E2E | 真实 Worker 进程 → 真实网关 /internal/deliveries → 渠道帧 | 用户收到文件/图片或链接；投递恰好一次；审计有记录 | tests/acceptance/attachment_round_trip/test_round_trip_e2e.py::test_s07_background_task_delivers_its_artifact_through_the_worker | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | e2e_deferred（本地已 GREEN，终验归 verify-e2e） | verified |
| S-10 | E2E | 真实渠道帧 + 真实 PG（审计逐行回读） | 同一产物同路由只交付一次；审计仍一行（「第二次回『此前已交付』」未实现，见清单偏离登记） | tests/acceptance/attachment_round_trip/test_round_trip_e2e.py::test_s10_delivering_the_same_artifact_twice_lands_one_frame_and_one_audit_row | uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py | e2e_deferred（本地已 GREEN，终验归 verify-e2e） | verified |

### Acceptance Evidence

**执行（2026-10-03）**，登记命令 `uv run pytest -q tests/acceptance/attachment_round_trip/test_round_trip_e2e.py` → **4 passed（21.3s）**。

**清单登记的 verifier**：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`
→ `tests/acceptance` **283 passed（894s）**、前端 build 成功、Playwright **4 passed**，**exit 0**。

> 首次整跑是 **7 failed / 276 passed**——那 7 条就是下面 ⑤⑥ 两条发现的现场。修完后 283 全绿。

#### 这条端到端基线**照出的真问题**（这正是本任务存在的理由）

> 本任务 Description 写的是："四个前置任务各自都绿，而只有端到端才照得出来"。这一轮**又一次印证了**——
> TASK-002/005/006 的单测全是绿的，而下面四条里没有一条能靠单测发现。

**① `read_attachment` 的返回被大结果外置截成了 200 字符预览，分页指引连同正文一起消失（功能实际不存在，且不报错）。**

`executor._should_externalize` 会把超过 8KB 的工具结果外置成 Artifact、只给模型留 `PREVIEW_DEFAULT_LIMIT = 200` 字符的预览。
`read_attachment` 一次默认返回 20 000 字符 ⇒ 必然被外置 ⇒ 模型只看到片段开头 200 字符，而**"继续读请用 offset=…"这句在结果末尾**，
被一并截掉。于是"长文档可读完"在真实链路上等于不存在，而且**没有任何报错**。

这与仓库里已经记录过的一次事故是**同一个机制、同一种后果**（`load_skill` 的 8320 字节返回值被外置，
见 `executor._should_externalize` 的文档串），当时给 `load_skill` / `read_skill_resource` 加了
`externalizable_result=False`；**`read_attachment` / `search_attachment` 这两把"内容投递"工具漏了**。
已按同一口径修（`attachment_tools.py`），`tests/agent_runtime/test_attachment_tools.py` 27 passed 无回归。

> TASK-002 的单测全绿，是因为外置发生在 **executor 层**、不在工具里——工具返回的东西完全正确，
> 是"交给模型之前"被换掉的。

**② 验收栈把 runtime 的交付调用打到了开发者本机的 dev 网关上。**

`start_gateway_stack` 给 worker 显式设了 `IM_GATEWAY_URL`，**runtime / runtime2 漏了** ⇒ 它们回落到 `.env` 的
`http://127.0.0.1:8003` = 本机 dev 网关。后果：① 会话内交付**跨出了本栈的隔离库**；② 那个实例跑的是它自己启动时的旧代码，
回一个**与本次改动毫无关系**的 422；③ 排查方向全错（错误码看着像契约不匹配，实际是"打错了机器"）。
已修（与 TASK-008 补 console 的 `AGENT_RUNTIME_URL` / `CONSOLE_PLATFORM_URL` 是同一类缺口）。

**③ WS 探针不实现三步分片上传 ⇒ 出站媒体在验收里根本走不通；而补它的时候又发现官方 SDK 把「缺失的 `errcode`」当成失败。**

探针作为"企微服务端"的替身，此前只应答 subscribe / reply / send。补 `init → chunk × N → finish` 时，
第一版回执只带 `headers + body`，结果真实 SDK 报 `Reply ack error` —— 因为它的回执处理是
`errcode = frame.get("errcode")` 然后 `if errcode != 0: raise`，**缺字段等于失败**。
单元测试里那个帧级替身是直接返回 dict 的，走不到这条检查，**测不出来**（又一例"假东西没照着真东西的形状做"）。已修。

**④ 探针脚本一次只能挂一个工具 ⇒ 表达不了「先写、再交付」这条硬需求链路。**

`write_artifact` 与 `deliver_artifact` 必须在**同一次 Run** 内（交付工具只允许交付本次 Run 自己写出的产物），
而单工具脚本每轮只给一个工具调用。已给探针加**工具序列**（`tools: [...]`，第 n 轮返回第 n 个，走完才给 `final_text`），向后兼容。

**⑤ TASK-004 的入站回执 E2E **从没跑过，而且跑不起来**。**

`uv run pytest -q tests/acceptance` 首次整跑时，`attachment_round_trip/test_inbound_receipt_e2e.py` 的 S-04 / E-02 两条**双双失败**（回执 150s 内没到）。这个文件在本需求里的状态是 `e2e_deferred`——**只写不跑**，所以下面这个问题从没被执行过。

根因在**测试自己的帧形状**：它把 **MIME 当 `msgtype`** 写进回调帧（`{"msgtype": "image/png", "image/png": {...}}`），而企微回调的 `msgtype` 只有 **`image` / `file`** 两个取值——真正的 MIME 由文件的 `Content-Disposition` 文件名推出来（网关按扩展名判类型、再按白名单门控）。`msgtype` 不认识 ⇒ 适配器**整项丢弃**（不认识的形态不产出媒体引用）⇒ 一个附件都不落盘、一条回执都不发，**而服务端不报错**。

兄弟套件 `tests/acceptance/im_gateway/test_wecom_attachments.py` 一直是绿的，因为它用的是 `image` / `file`。已按同一口径修（新增 `_msgtype_for(mime)`），两条转绿。

**⑥ `DeliveryRequest.tenant_id` 是 TASK-006 加的必填字段，而 5 条既有验收用例没带它。**

`dfx` ×2 / `redis_degradation` / `worker_delivery` / `task_schedule` 都直接 POST `/internal/deliveries` 并手搓请求体，全部回 `422 missing tenant_id`。这些套件**不在 TASK-006/007 的 verifier 范围内**（那两个任务的 verifier 是 `tests/console_channel tests/gateway` 与 `tests/agent_worker` + `tests/agent_runtime`），所以回归一直没被发现——**这正是"各任务自证"的盲区**。已补齐，并注明该字段是交付审计幂等键的一部分。

> **这两条加起来说明一件事**：本需求的 verifier 口径如果不包含 `tests/acceptance`，前面九个任务就算各自全绿，也可能带着两个"从来没人跑过"的窟窿收尾。TASK-010 的 verifier 命令正是为此登记的。

#### 顺带改掉的一处排查成本

`GatewayDeliveryClient` 拿到 422 时只留下一个 `COMMON_VALIDATION_ERROR` 码、丢掉字段名，本次为一个字段名翻了半天网关日志。
已让它把**契约字段名**带上（只有我们自己的字段名，不含渠道形状与凭据）；上面 ② 的定位正是靠这一改动才从"契约不匹配"收敛到"打错了机器"。

- S-03: e2e_deferred — automated command e2e_deferred; run_id=15d51f716ffd4a1f8bff084e12f6e6b7 (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=15d51f716ffd4a1f8bff084e12f6e6b7 (confirmed_by: runner)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=15d51f716ffd4a1f8bff084e12f6e6b7 (confirmed_by: runner)
- S-10: e2e_deferred — automated command e2e_deferred; run_id=15d51f716ffd4a1f8bff084e12f6e6b7 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=80bcb88adee0468bb3a04e052ef3592e (confirmed_by: runner)

### Log
- [2026-10-03] created (draft)
- [2026-10-03] started
- [2026-10-03] 四条 E2E 落地（真实五进程栈）：4 passed（21.3s）
- [2026-10-03] 照出并修掉四个真问题：`read_attachment` 被大结果外置（分页指引连同正文一起消失）/ 验收栈 runtime 的 `IM_GATEWAY_URL` 指向 dev 网关 / WS 探针缺上传协议（且官方 SDK 把缺失的 `errcode` 当失败）/ 探针脚本只能挂一个工具
- [2026-10-03] completed (done)
