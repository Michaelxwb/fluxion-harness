# Tasks: 附件往返（出站交付 + 读材料增强 + 入站回执 + 生命周期）

- **Source**: `.code-flow/tasks/2026-10-02/attachment-round-trip/attachment-round-trip.design.md`
- **Created**: 2026-10-03
- **Updated**: 2026-10-03

## Proposal

上一需求 `wecom-inbound-media` 只闭合了**入站**：用户能发图片/文件，Agent 能看、能读、能被门控与审计。另一半是断的——Agent 写出的文件**没有任何人交付给用户**（全仓 `AGENT_OUTPUT` 只有写入方、零消费方；后台任务甚至给用户贴一串 UUID）；长文档只能读前 20 000 字符且无分页；收到附件后除模型回答没有任何回执；产物只增不减。本次让附件**走得通往返**：进得来（已具备）→ 读得完 → **交得回** → 有回执、有生命周期。核心是把"写出"与"交付"分离，并让渠道差异继续只活在适配器层。

### Alignment

- **Scope**：四个包——读材料增强（FEAT-01..04）、入站回执（FEAT-05）、出站交付（FEAT-06..11、14）、产物生命周期（FEAT-12、13）；P0 先交付，P1 随批。
- **Decisions**：
  - **探针先行**（FEAT-06 是 P0 前置）：企微能否发文件/图片、上传流程（media_id vs URL）、发送 ack 与幂等——**外部协议事实**，猜错要重写整条链（与上期 R-01 同源）。
  - **写与交付分离**：`write_artifact` 只写（不再因缺交付路由而拒绝），新增 `append_artifact`（分段追加长文档）与**显式交付工具** `deliver_artifact`。依据是五条代码事实（设计 §3.6）：现有守卫把"写"与"可交付"耦合错了方向、中间产物会被误发、"写了但没发"的语义无法表达、幂等键必须在交付那一步可派生等。**否决** `write_artifact(deliver=true)` 参数式合并。
  - **追加写换新键**：`RULE-skill-001` 要求同 `storage_key` 二次写入必须抛错 ⇒ 分段追加写**新 key**，artifact 行指向最新版本，历史版本记在既有 `metadata_json`（不改 schema）。
  - **出站渠道差异走可选能力协议**（与入站 `AttachmentSource` 同构）：核心域只给"产物引用 + 路由"，适配器决定直发/链接/降级。
  - **交付审计表从第一天起渠道中立**：`channel + route_key`（适配器产出的可读不透明串），**不照抄**上期入站审计表的 `bot_id + external_user_id` 企微形状——未来上 web chat 时那两列会成为"填不出真值"的必填字段。
  - **取件是渠道无关能力**：签名短 TTL 直链 + 鉴权端点，Console／未来 web chat／降级链接**复用同一套**。
  - **前端页面后置**：本期只做取件端点与签名直链（IM 终端用户是 `platform_user`，Console 登录主体是 `console_account`，两套身份——给终端用户 Console 页面等于给一扇打不开的门）。
  - **产物取件端点不加 Console 页面**，`harness-frontend` / `harness-ui` 两条 required 规则在本需求标 N/A（已由用户逐条确认）。
- **Non-goals**：企微以外的 IM 通道（只做企微，但接缝按渠道中立设计）；附件在线编辑/协作；文档结构化理解；病毒扫描/内容审核；跨租户产物共享；出站消息的富文本排版（卡片模板只作为链接载体）。
- **Acceptance**：见下方 Acceptance Coverage（19 条场景；S-05 为 manual，原因是需要真实外部机器人与会话，无法在 CI 复现）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 | cwd | 超时 | 依赖 |
|--------|---------|---------|-------------|---------|------|------|-----|------|------|
| S-01 | design#2.5.2 | integration | 真实文件系统 + 真实解析库（pypdf/docx） | TASK-002 | planned | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| S-02 | design#2.5.2 | integration | 真实 PG（runtime.artifact 逐行回读） | TASK-003 | planned | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| S-03 | design#2.5.2 | E2E | 真实回调桩 → 真实落盘 → 真实工具 → 真实模型请求体 | TASK-010 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| S-04 | design#2.5.2 | E2E | 真实 WS 探针 → 真实网关 → 真实渠道帧 | TASK-004 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| S-05 | design#2.5.2 | manual | 真实企微机器人（外部条件，无法在 CI 自动化） | TASK-001 | planned | - | . | 60 |  |
| S-06 | design#2.5.2 | E2E | 真实会话 → 真实产物 → 真实交付 → 渠道帧/链接 | TASK-010 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| S-07 | design#2.5.2 | E2E | 真实 Worker 进程 → 真实网关 /internal/deliver → 渠道帧 | TASK-010 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| S-08 | design#2.5.2 | integration | 真实文件系统 + 真实 PG | TASK-009 | planned | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | . | 120 |  |
| S-09 | design#2.5.2 | E2E | 真实 HTTP 取件端点 + 真实鉴权（非 mock） | TASK-008 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| S-10 | design#2.5.2 | E2E | 真实渠道帧 + 真实 PG（审计逐行回读） | TASK-010 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| E-01 | design#2.5.2 | integration | 真实文件系统 | TASK-002 | planned | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| E-02 | design#2.5.2 | E2E | 真实 WS 探针 → 真实网关 | TASK-004 | planned | uv run pytest -q tests/acceptance/attachment_round_trip | . | 300 |  |
| E-03 | design#2.5.2 | integration | 真实 PG + 真实存储 + 鉴权层 | TASK-008 | planned | uv run pytest -q tests/console_channel/test_artifact_fetch.py | . | 120 |  |
| E-04 | design#2.5.2 | integration | 真实 HTTP（渠道发送端点） | TASK-007 | planned | uv run pytest -q tests/console_channel/test_artifact_delivery_audit.py | . | 120 |  |
| E-05 | design#2.5.2 | integration | 真实文件系统 + 真实 PG | TASK-009 | planned | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | . | 120 |  |
| E-06 | design#2.5.2 | integration | 真实 HTTP（渠道发送端点） | TASK-006 | planned | uv run pytest -q tests/gateway/test_artifact_delivery.py | . | 120 |  |
| B-01 | design#2.5.2 | unit | 分段纯函数 | TASK-002 | planned | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| B-02 | design#2.5.2 | unit | 枚举分页 | TASK-003 | planned | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |
| B-03 | design#2.5.2 | unit | 出站产物大小 | TASK-005 | planned | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 120 |  |

> 覆盖自检：design 全部 P0/P1 场景 **19/19** 已分配唯一负责人（S-01..S-10、E-01..E-06、B-01..B-03）；RULE-01..07 与高影响 R-01/R-06 均有映射场景；E2E 场景 **7 个**（S-03、S-04、S-06、S-07、S-09、S-10、E-02）层级未降级；`manual` 仅 S-05，原因是需要真实外部机器人与会话（CI 无法复现，设计 R-06）。

---

## TASK-001: 企微出站能力真机探针

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.1 功能清单`, `#3.6 出站交付：写与发的分离（硬需求落点）`, `#5.1 项目依赖`
- **Spec-Refs**: harness-im#RULE-im-002
- **Acceptance-Refs**: S-05

### Description

出站交付的**形态**取决于一个尚未验证的外部事实：企微 aibot 能否发文件/图片。现有代码只证明会话内回复体支持 `stream`/`template_card`、主动发送体支持 `markdown`/`template_card`（`apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py:444-457`）；文件上传与发送**完全没有结论**。本任务用真实机器人给出**事实表**，它是 TASK-006 形态分支（直发 vs 链接降级）的输入——不先做，整条出站链只能悬空。

**探针代码只能落在适配器层**（`channels/wecom/`）：核心域与网关应用层不得出现任何渠道专有字样或上传凭据，`tests/architecture/test_channel_neutrality.py` 会当场判红。

### Checklist

- [ ] 按探针清单逐项实测并记录**原始帧与 ack**：① 会话内回复（`aibot_respond_msg`）能否发文件、发图片；② 主动发送（`aibot_send_msg`）能否发文件、发图片；③ 若能发，文件从哪来（先上传拿 `media_id` 还是直接给 URL/字节）；④ 发送是否有 ack、失败长什么样；⑤ 重投同一条发送请求是否重复发出文件
- [ ] 断言必须落在**真实响应**上（errmsg/errcode/帧体），不得只看"没抛异常"——上期踩过"本地探针不校验故套件测不出"的坑
- [ ] 产出**事实表**（可否直发文件/图片、上传流程、ack 语义、失败形态、幂等性），并写回设计 §3.6 的"跳 4"与 §5.1 的状态列
- [ ] 真机环境注意：绕系统 SOCKS 代理（否则缺 python-socks 报错）、可上 TLS 中继做真实断链、看 `wecom_ws_connected` 与 Reply ack、**不要起 Worker**
- [ ] [S-05][manual] 登记探针步骤与记录位置（真实边界：真实企微机器人 + 真实会话；无法在 CI 复现，原因是外部凭据与会话，设计 R-06）
- [ ] 运行 verifier：`uv run pytest -q tests/architecture/test_channel_neutrality.py`（`harness-im#RULE-im-002`）；记录输出

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | manual | 真实企微机器人 + 真实会话（外部条件） | 产出事实表：可否直发文件/图片、上传流程、ack 语义、失败形态；结论写回设计并决定 S-06 的形态分支 | 人工执行（探针清单见 Checklist） | - | planned |

### Acceptance Evidence

> manual 场景只登记原因/边界/验收方式；人工确认与 E2E 执行统一留给 verify-e2e。

### Log
- [2026-10-03] created (draft)

---

## TASK-002: 读材料工具面：分段读 + 文档内定位 + 大文件策略

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.2 字段约束`, `#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: —
- **Acceptance-Refs**: S-01, E-01, B-01

### Description

`read_attachment` 现在只能返回前 `MAX_TEXT_CHARS = 20 000` 字符且 schema 只有 `artifact_id`——长文档实际不可用。本任务给它加 `offset`/`limit` 分段，并补 `search_attachment`（在已抽取文本里按关键词返回命中片段与偏移，避免模型盲目翻页），以及超长文档的"目录 + 分段指引"策略。**抽取一次、切片多次**：单次工具调用内缓存全量文本，不因 offset 变化重复解析同一文档。

### Checklist

- [ ] `read_attachment` schema 增 `offset`（≥0，缺省 0）与 `limit`（1..`MAX_TEXT_CHARS`，缺省 `MAX_TEXT_CHARS`）；返回值标注**片段区间与全文总长**，使多次调用可拼接还原
- [ ] 新增 `search_attachment(artifact_id, query, limit?)`：返回命中片段 + 偏移；未命中明确说"未命中"，不返回空内容冒充成功
- [ ] 大文件策略：超过上限的文档给出"目录/摘要 + 分段读指引"，而不是只丢一句截断提示
- [ ] 拒绝路径保持既有口径：不支持的 MIME、损坏文档、越界 offset 各自返回**明确错误码**，绝不返回乱码或空内容
- [ ] [S-01][integration] 真实边界：真实文件系统 + 真实解析库；断言三次片段（`offset=0/20000/40000`）拼接**逐字符等于**全文，且每段标注区间与总长
- [ ] [E-01][integration] 真实边界：真实文件系统；断言超上限/损坏文档/越界 offset 各自返回明确错误（指明文件与原因），不返回乱码或空内容
- [ ] [B-01][unit] 真实边界：分段纯函数；断言 `offset` 取 0 / 恰好等于总长 / 超出总长 / 负值 四种边界的行为（首段 / 空段+标注 / 空段+标注 / 参数错误）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实文件系统 + 真实解析库（pypdf/docx） | 三次片段拼接逐字符等于全文；每段标注区间与总长 | tests/agent_runtime/test_attachment_tools.py | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | planned |
| E-01 | integration | 真实文件系统 | 超上限/损坏/越界 offset → 明确错误码（含文件与原因），不返回乱码或空内容 | tests/agent_runtime/test_attachment_tools.py | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | planned |
| B-01 | unit | 分段纯函数 | offset=0/等于总长/超总长/负值 四边界行为分别正确 | tests/agent_runtime/test_attachment_tools.py | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-003: 附件枚举工具 `list_attachments`

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.2 字段约束`, `#3.4 接口设计`
- **Spec-Refs**: —
- **Acceptance-Refs**: S-02, B-02

### Description

模型现在只能从上下文的文本引用里拿 `artifact_id`——而历史引用会被上下文预算裁掉，那个附件就**永久失联**。本任务新增枚举工具：按 `run`/`conversation` 范围列出可寻址的附件（入站 + Agent 自产），带 id/文件名/MIME/大小/方向/时间，使"跨多轮继续用某个附件"成为可能。

### Checklist

- [ ] 新增工具 `list_attachments`，入参 `scope`（`run`/`conversation`，缺省 `conversation`）、`direction`（`inbound`/`outbound`，缺省不限）、`limit`（1..50，缺省 20）、`offset`（≥0）
- [ ] 返回紧凑行文本（id、文件名、MIME、大小、方向、时间），入站与自产可区分；空集返回空列表说明而非错误
- [ ] 租户与归属过滤从 Run 上下文取，**不进工具 schema**；跨租户一律不可见
- [ ] 单条 SQL 完成（走既有 `ix_artifact_run` / `ix_artifact_conversation`），不做"先查全量再内存过滤"
- [ ] [S-02][integration] 真实边界：真实 PG（`runtime.artifact` 逐行回读）；断言一次 Run 内的 1 个入站 pdf 与 1 个自产 markdown **都在**，字段齐全且方向可区分
- [ ] [B-02][unit] 真实边界：枚举分页；断言 `limit` 取 1/50/51/空集的行为（上限内正常；超上限被拒绝或夹紧——**实现需二选一并固定**；空集返回空列表而非错误）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 PG（runtime.artifact 逐行回读） | 入站与自产两条都在，含 id/文件名/MIME/大小/方向；两者可区分 | tests/agent_runtime/test_attachment_tools.py | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | planned |
| B-02 | unit | 枚举分页 | limit=1/50/51/空集行为正确；空集返回空列表而非错误 | tests/agent_runtime/test_attachment_tools.py | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-004: 入站回执（与拒绝反馈合并为至多一条）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#2.3.1 功能清单`, `#2.5.1 业务规则与约束`
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-04, E-02

### Description

用户发完附件后，除了模型回答**没有任何回执**：全收下时静默；部分拒绝时用户不知道哪些收下了。本任务在网关侧补回执，并与既有"拒绝反馈"**合并为至多一条**消息（不得对同一条入站消息产生两条用户可见反馈）。

### Checklist

- [ ] 收到附件后按接收结果生成回执：全部接收 / 部分接收（含被拒数量与原因）/ 全部拒绝（只发拒绝说明，不叠加回执）
- [ ] 与既有拒绝反馈路径合并：同一条入站消息**最多一条**附件相关反馈
- [ ] 文案经消息目录取，`config/api-messages.yaml` 补 zh-CN 与 en-US 词条；数值（如上限）仍只有一处来源
- [ ] [S-04][E2E] 真实边界：真实 WS 探针 → 真实网关 → 真实渠道帧；断言用户收到**一条**"已收到 2 个、1 个未接收及原因"的回执，且仅此一条附件相关反馈
- [ ] [E-02][E2E] 真实边界：真实 WS 探针 → 真实网关；断言全部附件被拒时只发一条拒绝说明（不出现两条消息）
- [ ] 运行 verifier：`uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`（`harness-i18n#RULE-i18n-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 WS 探针 → 真实网关 → 真实渠道帧 | 用户收到一条含"已收到 N 个、M 个未接收及原因"的回执；仅此一条附件相关反馈 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |
| E-02 | E2E | 真实 WS 探针 → 真实网关 | 全部被拒时只有一条拒绝说明，不叠加回执 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-005: 写与交付语义分离 + `append_artifact`

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.6 出站交付：写与发的分离（硬需求落点）`, `#3.1 方案选型`
- **Spec-Refs**: harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-03

### Description

`write_artifact` 现在既"写"又隐含"可交付"：没有交付路由时它**直接拒绝**（`attachment_tools.py:210-221`），于是后台任务、控制台触发的 Run 连"写"都做不了——而"写"本身只需要 Run 上下文（产物落共享 store + DB 行）。本任务把两者**在实现上拆开**：`write_artifact` 只写；新增 `append_artifact` 供分段写长文档（模型单轮输出有 token 上限，没有追加就写不出长文档）；交付动作留给 TASK-006 的 `deliver_artifact`。

**不可变约束**（`harness-skill#RULE-skill-001`）直接决定方案：同一 `storage_key` 二次写入必须抛错 ⇒ 追加写**新 key**，artifact 行指向最新版本，历史版本键记在既有 `metadata_json`，**不改 schema**。

### Checklist

- [ ] `write_artifact` 去掉"缺交付路由即拒绝"的守卫：只依赖 Run 上下文；返回文案明确提示"产物已写出（id=…）；如需交给用户请调用 `deliver_artifact`"
- [ ] 新增 `append_artifact(artifact_id, content)`：只能追加**本 Run 自产**的产物；每次追加写**新 `storage_key`**（原子写：临时文件 + `os.replace`），artifact 行指向最新版本并把历史版本键写入 `metadata_json`；**同 key 二次写入必须抛 `FileExistsError`**
- [ ] 产物大小上限（既有 `MAX_READ_BYTES`）对写出与追加一视同仁；超限**明确拒绝**且**不破坏已有内容**
- [ ] 产物与追加写**不进入 Run 快照冻结范围**（快照只冻 Agent/Model/Skill/MCP/Prompt/catalog），冻结语义不变
- [ ] [B-03][unit] 真实边界：出站产物大小；断言等于上限接收、上限 + 1 B **明确拒绝**且已有内容不被破坏
- [ ] 运行 verifier：`uv run pytest -q tests/test_skill_artifact_cache.py`（`harness-skill#RULE-skill-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`（`harness-snapshot#RULE-snapshot-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | unit | 出站产物大小（真实文件系统） | 等于上限接收；超过上限明确拒绝且已写内容不被破坏 | tests/agent_runtime/test_attachment_tools.py | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-006: 出站交付链：契约形态 + 显式交付 + 会话内/后台两条投递路径

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-005
- **Source**: `attachment-round-trip.design.md#3.4 接口设计`, `#3.6 出站交付：写与发的分离（硬需求落点）`, `#3.2 架构设计`
- **Spec-Refs**: harness-im#RULE-im-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: E-06

### Description

这是硬需求的落点：**写完文件之后，Agent 必须能把它发送给用户**。四段工作——① 契约 `DeliveryMessage` 增加产物形态（渠道中立，复用 `AttachmentRef` 同型引用）；② 新增显式交付工具 `deliver_artifact(artifact_id, note?)`，Runtime 发**渠道中立**的 `artifact.delivery` SSE 事件（只带引用，不阻塞模型回答）；③ 网关消费事件，调适配器的**可选出站能力**（直发文件/图片，或降级为 TASK-008 的取件链接）；④ 后台任务路径复用既有投递链（`task:{task_id}:final` 幂等键不变），新增 `message.type=artifact` 形态，**发送失败必须释放投递占位**（否则任务被误判已送达）。

设计顺序上**依赖 TASK-001 的探针结论**决定"直发"分支是否成立；但两条分支的实现骨架可以并行准备（能力协议对两者都成立）。

### Checklist

- [ ] `DeliveryMessage` 增 `type` 取值 `artifact`/`image`（缺省仍 `text`，向后兼容）；`type != text` 时 `artifact: AttachmentRef` 必填，**不含任何渠道私有发送凭据**
- [ ] 新增工具 `deliver_artifact(artifact_id, note?)`：校验归属（本 Run/会话 + 租户）与交付路由存在；schema 只有这两个字段，**没有渠道字段**
- [ ] 工具结果明确回传三种结论：已交付（含文件名）/ 降级为链接（含链接）/ 失败（含原因码 + "产物已保留，可重试"）
- [ ] Runtime 发 `artifact.delivery` 事件（只带 `AttachmentRef` + note）；网关回"已受理"不阻塞模型回答
- [ ] 适配器侧新增**可选出站能力协议**（与入站 `AttachmentSource` 同构）：核心域只给"产物引用 + 路由"，适配器决定直发/链接/降级
- [ ] 图片出站（FEAT-10）：`type=image` 走同一交付链；`view_image` 是入站方向的重看，**不是**同一件事
- [ ] 后台路径：`/internal/deliver` 支持产物形态；沿用既有 `reserve → 发送 → mark`，**失败释放占位**
- [ ] 交付链全程**不见渠道形状**：核心域与网关应用层零渠道字样与发送凭据（守卫会判红）
- [ ] [E-06][integration] 真实边界：真实 HTTP（渠道发送端点）；断言首次交付失败时工具结果显式报失败与原因、**产物保留**、按同一幂等键重试后成功且用户恰好收到一次
- [ ] 运行 verifier：`uv run pytest -q tests/console_channel tests/gateway`（`harness-im#RULE-im-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py`（`harness-worker#RULE-worker-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-06 | integration | 真实 HTTP（渠道发送端点） | 首次失败 → 显式报失败与原因 + 产物保留 + 审计 FAILED；同幂等键重试成功且用户恰好收到一次 | tests/gateway/test_artifact_delivery.py | uv run pytest -q tests/gateway/test_artifact_delivery.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-007: 交付审计落点（表 + console 内部端点）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.3 数据设计`, `#3.4 接口设计`
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-api#RULE-api-002, harness-data#RULE-data-001
- **Acceptance-Refs**: E-04

### Description

交付必须有留痕："谁在何时把哪个产物交付给哪个路由、结果如何"。**网关不持库**（架构测试守住）⇒ 与上期入站审计同路：新增 `control.artifact_delivery_audit` 表 + console 内部端点写入。

**本表的形状与上期入站审计表刻意不一致**：用 `channel + route_key`（适配器产出的可读不透明串）而不是 `channel + bot_id + external_user_id`。理由是需求明确"Console 不放开给终端用户、未来做 web chat"——那时交付路由是 session/WebSocket，照抄企微形状会让那两列**填不出真值**（上期 B-09 ② 的坑）。上期已归档的表不动；不一致是有意的，设计 §3.3 已记录。

### Checklist

- [ ] 新增 `control.artifact_delivery_audit`（`StandardColumnsMixin` + `timestamptz`）：`tenant_id`/`artifact_id`/`channel`/`route_key`/`delivery_key`/`outcome`(`DELIVERED`/`FAILED`/`DEGRADED`)/`reason_code`/`trace_id`；**无自由 JSON 列**（凭据与令牌在类型上无处可放）
- [ ] 索引：`(tenant_id, create_time DESC)`、`(artifact_id)`、`(tenant_id, channel, route_key)`、partial unique `(tenant_id, delivery_key, route_key, outcome) WHERE is_deleted = false`——**含 `route_key` 是必需的**：否则同一产物交付到两个不同路由会被误判为重复
- [ ] alembic 迁移：单链接在当前 head 之后，`upgrade` / `downgrade` 双跑可用；**无回填**（历史产物无交付记录——不存在的事实不伪造）
- [ ] `POST /internal/channel/artifact-delivery`（`InternalServiceDep` + `HeaderTenantId` + `ok(catalog,…)` 封套）；写入走 `ON CONFLICT DO NOTHING` + 回查，重投不产生第二行
- [ ] 不新增部署单元、网关不持库（审计经 console 内部端点写）
- [ ] [E-04][integration] 真实边界：真实 HTTP（渠道发送端点）；断言渠道上传/发送失败或超时时交付显式失败、审计记 `FAILED`、调用方**不**收到"已交付"、按幂等键可重试
- [ ] 运行 verifier：`uv run pytest -q tests/architecture`（`harness-arch#RULE-arch-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/console_skill/test_import_idempotency.py`（`harness-api#RULE-api-002`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests -k schema_parity`（`harness-data#RULE-data-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-04 | integration | 真实 HTTP（渠道发送端点）+ 真实 PG | 渠道失败 → 交付显式失败 + 审计记 FAILED + 调用方不收到已交付；按幂等键可重试 | tests/console_channel/test_artifact_delivery_audit.py | uv run pytest -q tests/console_channel/test_artifact_delivery_audit.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-008: 产物取件能力（签名短 TTL 直链 + 鉴权端点）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-api#RULE-api-001, harness-secret#RULE-secret-001, harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-09, E-03

### Description

**渠道无关的取件能力**：Console、未来 web chat、以及"渠道不能直发文件"时的降级链接，**复用同一个鉴权端点与同一套令牌模型**。终端用户取件**只能是签名短 TTL 直链**——IM 终端用户是 `platform_user`，Console 登录主体是 `console_account`（ADMIN/BUILDER），两套身份，给终端用户 Console 页面等于给一扇打不开的门。

### Checklist

- [ ] `GET /api/v1/artifacts/{artifact_id}/content`：鉴权二选一（Console 会话态 / `?token=…` 签名令牌：单产物 + 短 TTL + 可撤销，仅存内存或短 TTL 存储）
- [ ] 响应为二进制流（`Content-Type` 取产物 `media_type`、`Content-Disposition` 带原文件名）；**不得把 `storage_key` 或存储路径暴露给客户端**
- [ ] 无权限 / 令牌失效 / 不存在**一律 404**（与不存在同样响应，不泄露存在性）
- [ ] 签名令牌生成与校验：**令牌不进日志、不进审计字段**（`harness-secret#RULE-secret-001`）
- [ ] 降级链接由 TASK-006 的适配器在"不能直发"时使用；本任务只提供能力，不含渠道判断
- [ ] [S-09][E2E] 真实边界：真实 HTTP 取件端点 + 真实鉴权（非 mock）；断言签名令牌与 Console 会话两条路径都拿到**字节与原文件一致**的内容；令牌过期/跨租户一律 404
- [ ] [E-03][integration] 真实边界：真实 PG + 真实存储 + 鉴权层；断言以租户 B 请求租户 A 的产物 id 被拒且**不泄露存在性**
- [ ] 运行 verifier：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`（`harness-api#RULE-api-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`（`harness-secret#RULE-secret-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity`（`harness-auth#RULE-auth-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | E2E | 真实 HTTP 取件端点 + 真实鉴权（非 mock） | 两条鉴权路径都拿到字节与原文件一致的内容；令牌过期/跨租户一律 404 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |
| E-03 | integration | 真实 PG + 真实存储 + 鉴权层 | 跨租户请求被拒且与不存在同样响应（不泄露存在性） | tests/console_channel/test_artifact_fetch.py | uv run pytest -q tests/console_channel/test_artifact_fetch.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-009: 产物保留期与清理

- **Status**: draft
- **Priority**: P1
- **Depends**:
- **Source**: `attachment-round-trip.design.md#3.4 接口设计`, `#3.3 数据设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-log#RULE-log-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-08, E-05

### Description

入站/出站产物现在**只增不减**，共享 PVC 会无限增长，且没有任何地方能证明"某个文件存在过"。本任务加保留期与清理：按既有 `runtime.artifact.create_time` + 配置项判定，**不新增列**；与既有 `cleanup-skill-orphans` 同口径（宽限期保护进行中事务、dry-run 先出清单、stdout 逐条可对账）。

### Checklist

- [ ] `python -m muad_console_platform.cli cleanup-artifacts`：`--grace-seconds`（默认 3600）、`--retention-days`（默认取配置）、`--dry-run`（只报告）、`--limit`
- [ ] 保留期判定用既有 `runtime.artifact.create_time`（`timestamptz`）与配置项，**不新增列**
- [ ] 删除保持可对账：文件与 DB 行同时消失，不留孤儿文件、不留悬空行；**宽限期内零删除**
- [ ] 单次扫描 + `--limit` 批量删除（不做逐文件 stat 的 N+1）；结果经 CLI stdout 逐条输出便于运维核对
- [ ] [S-08][integration] 真实边界：真实文件系统 + 真实 PG；断言三个产物（早于保留期 / 宽限期内 / 在用）中**只**清理过期项，文件与 DB 行同时消失，另两项原地不动
- [ ] [E-05][integration] 真实边界：真实文件系统 + 真实 PG；断言宽限期内文件被跳过；有 DB 行但文件缺失按"孤儿"处理且不误删在用；结果可对账
- [ ] 运行 verifier：`uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py`（`harness-log#RULE-log-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity`（`harness-time#RULE-time-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | integration | 真实文件系统 + 真实 PG | 只清理过期项；文件与 DB 行同时消失；宽限期内与在用项原地不动 | tests/console_platform/test_artifact_cleanup.py | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | planned |
| E-05 | integration | 真实文件系统 + 真实 PG | 宽限期内跳过；行缺失按孤儿处理且不误删在用；结果可对账 | tests/console_platform/test_artifact_cleanup.py | uv run pytest -q tests/console_platform/test_artifact_cleanup.py | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)

---

## TASK-010: 端到端验收基线

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009
- **Source**: `attachment-round-trip.design.md#2.5.2 功能验收场景`, `#3.6 出站交付：写与发的分离（硬需求落点）`
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-03, S-06, S-07, S-10

### Description

本需求跨回调、工具、PG、共享存储、渠道帧五个边界，必须有一条**端到端**信号把它们连起来，而不是各任务自证——上一需求的经验：四个前置任务各自都绿，而"图片从未进过模型请求体"这个 P0 只有端到端才照得出来。本任务在真实栈上跑通三条主链：长文档读完再回答（S-03）、会话内交付（S-06）、后台任务交付（S-07）、交付幂等（S-10），并登记可复现命令。

### Checklist

- [ ] 确认真实边界未被降级：这些 E2E 里未 mock 业务 API / DB / 落盘 / 渠道帧
- [ ] 端到端跑通「长文档 → 分段读 → 回答体现**末尾**事实」（证明不是只读了开头）
- [ ] 端到端跑通「会话内写出 → 显式交付 → 用户收到文件或链接 → 审计有记录」
- [ ] 端到端跑通「后台任务完成 → 投递链 → 用户收到产物（不再是一串 UUID）」
- [ ] [S-03][E2E] 真实边界：真实回调桩 → 真实落盘 → 真实工具 → 真实模型请求体；断言模型请求体里出现**后续片段**正文且最终回答含该事实
- [ ] [S-06][E2E] 真实边界：真实会话 → 真实产物 → 真实交付 → 渠道帧/链接；断言用户收到文件（分支 1）或可下载链接（分支 2），审计有对应记录，且**交付失败时模型不被告知"已交付"**
- [ ] [S-07][E2E] 真实边界：真实 Worker 进程 → 真实网关 `/internal/deliver` → 渠道帧；断言用户收到文件或链接、投递恰好一次（重投幂等）、审计有记录
- [ ] [S-10][E2E] 真实边界：真实渠道帧 + 真实 PG（审计逐行回读）；断言同一产物对同一路由交付两次时用户**只**收到一次、第二次工具结果回"此前已交付"、审计仍只有一行
- [ ] 运行 verifier：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（`harness-test#RULE-test-001`）；记录输出
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | 真实回调桩 → 真实落盘 → 真实工具 → 真实模型请求体 | 模型请求体出现后续片段正文；回答含文档末尾的事实 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |
| S-06 | E2E | 真实会话 → 真实产物 → 真实交付 → 渠道帧/链接 | 用户收到文件或链接；审计有记录；失败时不谎报已交付 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |
| S-07 | E2E | 真实 Worker 进程 → 真实网关 /internal/deliver → 渠道帧 | 用户收到文件或链接；投递恰好一次；审计有记录 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |
| S-10 | E2E | 真实渠道帧 + 真实 PG（审计逐行回读） | 同一产物同路由只交付一次；第二次回"此前已交付"；审计仍一行 | tests/acceptance/attachment_round_trip | uv run pytest -q tests/acceptance/attachment_round_trip | planned |

### Acceptance Evidence

### Log
- [2026-10-03] created (draft)
