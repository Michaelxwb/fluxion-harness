# Tasks: 企微入站附件接收与 Agent 附件工具面

- **Source**: `.code-flow/tasks/2026-10-02/wecom-inbound-media/wecom-inbound-media.design.md`
- **Created**: 2026-10-02
- **Updated**: 2026-10-02

## Proposal

企微用户发图片/文件后机器人毫无反应——入站非文本消息在渠道适配层被静默丢弃，且 Agent 侧没有任何消费附件的工具。本次把**入站附件底座**（下载解密→落共享存储→契约承载→门控反馈审计）与 **Agent 附件工具面**（图片直发模型、文档抽取工具）一并补齐，使截图/文档能被真正使用，且任何"收不了"的情况都有明确反馈与审计。同时把通道差异收敛到适配器内、通道枚举收口，为将来快速接入新 IM 通道留好边界。

### Alignment

- **Scope**: 入站附件全链路（P0）+ 产物写出/当前时间/门控配置化（P1）；一份需求，P0 先交付。
- **Decisions**:
  - AD-8：**取件机制完全渠道私有**，不设跨渠道通用取件接口；对外只统一"产出 `AttachmentRef`"。
  - AD-6-C：`AttachmentRef` 一处定义，`ChannelEnvelope` 与 `MessageInput` 同型使用（附件不穿透渠道边界）。
  - AD-7-B：通道枚举收口为 `ChannelName` 别名（产品确认纳入本期）。
  - AD-1-B：网关只落字节、`artifact` 行由 Runtime 写（不改 schema）。
  - plan 阶段补 `B-08`（FEAT-10 的时钟场景），design §6 缺口已闭合。
  - **代码期修订（2026-10-02，TASK-002 开工时）**：① 门控拆**两段**（预检数量 → 实检类型/大小）——企微回调不带 `size`/MIME/文件名（design §3.2.2），单段前置门控物理上不可实现；② `mixed` 图文混排纳入范围（补 `S-08`）；③ 契约新增 `ChannelEnvelope.unsupported_media` 作为"不支持类型"的传输通道（E-01 的前置，核心域回复由 TASK-004 接线）。
- **Non-goals**: 出站端到端文件发送；企微以外的渠道（含自建 web 对话页通道）；语音/视频专门处理；附件保留期策略；文档结构化理解。
- **Acceptance**: 见下方 Acceptance Coverage（25 条场景）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 | cwd | 超时 | 依赖 |
|--------|---------|---------|-------------|---------|------|------|-----|------|------|
| S-01 | design#2.5.2 | E2E | 真实回调桩 → 真实 PG/Redis → 真实落盘 → 真实模型请求体 | TASK-010 | e2e_deferred | uv run pytest -q tests/acceptance/wecom_attachments | . | 300 |  |
| S-02 | design#2.5.2 | E2E | 真实回调桩 → 真实 PG → 真实 artifact → 工具真实抽取 | TASK-010 | e2e_deferred | uv run pytest -q tests/acceptance/wecom_attachments | . | 300 |  |
| S-03 | design#2.5.2 | integration | 模型 provider 请求体（不 mock 组装层） | TASK-005 | verified | uv run pytest -q tests/agent_core/test_openai_provider.py | . | 60 |  |
| S-04 | design#2.5.2 | E2E | 回调 → 落盘 → 契约 → 工具 | TASK-004 | verified | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | . | 180 |  |
| S-05 | design#2.5.2 | E2E | 真实上下文组装 + 真实模型 | TASK-010 | e2e_deferred | uv run pytest -q tests/acceptance/wecom_attachments | . | 300 |  |
| S-06 | design#2.5.2 | integration | 工具 → artifact store → 读回 | TASK-008 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 60 |  |
| S-07 | design#2.5.2 | integration | FakeChannelAdapter → 门控 → 契约 → 落盘（不含企微路径） | TASK-004 | verified | uv run pytest -q tests/gateway/test_inbound_attachment_flow.py tests/gateway/test_inbound_attachment_store.py | . | 60 |  |
| S-08 | design#2.5.2 | integration | 回调帧分流解析层（不 mock 帧） | TASK-002 | verified | uv run pytest -q tests/gateway/test_wecom_media.py | . | 60 |  |
| E-01 | design#2.5.2 | E2E | 回调 → 反馈投递 → 审计表 | TASK-004 | verified | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | . | 180 |  |
| E-02 | design#2.5.2 | E2E | 同上 | TASK-004 | verified | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | . | 180 |  |
| E-03 | design#2.5.2 | E2E | 同上 | TASK-004 | verified | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | . | 180 |  |
| E-04 | design#2.5.2 | integration | 解密路径 + 日志输出（审计腿见 TASK-004） | TASK-002 | verified | uv run pytest -q tests/gateway/test_wecom_media.py tests/test_logging_redaction.py | . | 60 |  |
| E-05 | design#2.5.2 | integration | DB 查询 + 工具越权校验 | TASK-007 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 60 |  |
| E-06 | design#2.5.2 | integration | 真实解析库 | TASK-007 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 60 |  |
| E-07 | design#2.5.2 | integration | 幂等键 + 落盘 | TASK-004 | verified | uv run pytest -q tests/gateway/test_inbound_attachment_flow.py tests/gateway/test_inbound_attachment_store.py | . | 60 |  |
| B-01 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-02 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-03 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-04 | design#2.5.2 | unit | 路径解析函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-05 | design#2.5.2 | integration | 契约序列化 | TASK-001 | verified | uv run pytest -q tests/test_attachment_contract.py | . | 60 |  |
| B-06 | design#2.5.2 | integration | provider 组装 | TASK-005 | verified | uv run pytest -q tests/agent_core/test_openai_provider.py | . | 60 |  |
| B-07 | design#2.5.2 | unit | 源码静态检查 | TASK-001 | verified | uv run pytest -q tests/test_attachment_contract.py | . | 60 |  |
| B-08 | design#2.5.2 | unit | 注入固定时钟 | TASK-009 | verified | uv run pytest -q tests/agent_runtime/test_time_tools.py | . | 60 |  |
| B-09 | design#2.5.2 | unit | 源码静态检查（不 mock） | TASK-011 | verified | uv run pytest -q tests/architecture/test_channel_neutrality.py | . | 60 |  |
| E-08 | design#2.5.2 | integration | 内部端点 → 真实 PG 审计表 | TASK-012 | verified | uv run pytest -q tests/console_channel/test_inbound_audit.py | . | 60 |  |

> 覆盖自检：design 全部 P0/P1 场景 25/25 已分配唯一负责人；RULE-01..08 与高影响 R-01/R-04 均有映射场景；E2E 场景 6 个未降级。

---

## TASK-001: 契约层：附件位 + 通道枚举收口

- **Status**: done
- **Priority**: P0
- **Depends**: 无
- **Source**: `wecom-inbound-media.design.md#2.3.2 字段约束`, `#3.2.3 通道复用性`, `#3.4 接口设计`
- **Spec-Refs**: harness-im#RULE-im-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: B-05, B-07

### Description

在契约层加入附件位并收口通道枚举。`AttachmentRef` **一处定义**，同时用于渠道边界 `ChannelEnvelope.attachments` 与消息契约 `MessageInput.attachments`（同型，零转换）——这是"新增通道只写适配器"的前提。类型里**不得**出现任何取件凭据字段（url/aes_key/media_id/download_code，AD-8），也**不得**出现 `artifact_id`（AD-1-B 下渠道侧拿不到 DB 标识）。同时把散在 7 处的 `Literal["WECOM"]` 收口为 `ChannelName` 别名。

### Checklist

- [x] 在 `contracts/channel.py` 定义 `ChannelName = Literal["WECOM"]`，并定义 `AttachmentRef`（`storage_key/kind/media_type/size/filename/checksum/source_channel`）
- [x] `ChannelEnvelope` 增加 `attachments: list[AttachmentRef] = Field(default_factory=list)`；`channel` 改用 `ChannelName`
- [x] `MessageInput`（`contracts/runtime.py`）的 `attachments` 改为 `list[AttachmentRef]`，`type` 增加 `"attachment"` 取值且**缺省仍为 `"text"`**
- [x] 其余 5 处 `Literal["WECOM"]` 改为引用 `ChannelName`（`contracts/tasks.py`、`contracts/resolve.py`、`contracts/runtime.py#ChannelContext`、console `application/dto.py`）
- [x] [B-05][integration] 零附件消息的 `ChannelEnvelope` / `MessageInput` 序列化结果与改造前**逐字段一致**（真实边界：契约序列化，不 mock）；断言 `attachments == []` 且无新增必填字段
- [x] [B-07][unit] 静态检查：全仓 `Literal["WECOM"]` 字面量**只**出现在 `ChannelName` 定义处（真实边界：源码扫描，不 mock）；断言其余位置均为别名引用
- [x] [S-07][integration] 断言 `FakeChannelAdapter` 可产出带 `AttachmentRef` 的 `ChannelEnvelope`（真实边界：契约类型，不 mock）；断言该结构**不含**任何取件凭据字段
- [x] 运行 verifier：`uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py`（`harness-worker#RULE-worker-001`，路径映射自动绑定后的局部承接）；记录输出
- [x] 运行 verifier：`uv run pytest -q tests/console_channel tests/gateway`（`harness-im#RULE-im-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-05 | integration | 契约序列化 | 零附件序列化与基线逐字段一致 | `tests/test_attachment_contract.py::test_b05_zero_attachment_serialization_stays_backward_compatible` | uv run pytest -q tests/test_attachment_contract.py | verified |
| B-07 | unit | 源码扫描 | `Literal["WECOM"]` 字面量仅 1 处 | `tests/test_attachment_contract.py::test_b07_channel_enum_is_defined_in_exactly_one_place` | uv run pytest -q tests/test_attachment_contract.py | verified |
| S-07 | integration | 契约类型 | fake 适配器可产出含附件的 envelope；结构无取件凭据 | `tests/test_attachment_contract.py::test_s07_fake_adapter_carries_attachments_without_credentials` | uv run pytest -q tests/test_attachment_contract.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-05 | FAIL: `ImportError: cannot import name 'AttachmentRef' from 'muad_contracts'` | PASS: `3 passed` | `tests/test_attachment_contract.py:test_b05_zero_attachment_serialization_stays_backward_compatible`（既有字段取值不变 + `type` 缺省仍是 `text` + `attachments == []`） | 真实契约序列化（pydantic `model_dump`，未 mock） | verified |
| B-07 | FAIL: 同上（模块导入期即失败） | PASS: `3 passed` | `tests/test_attachment_contract.py:test_b07_channel_enum_is_defined_in_exactly_one_place`（扫描 `contracts/*.py` + console `dto.py`，字面量分布 == `{"enums.py": 1}`；别名确被 4 个模块引用） | 真实源码扫描（读文件计数，未 mock） | verified |
| S-07 | FAIL: 同上（模块导入期即失败） | PASS: `3 passed` | `tests/test_attachment_contract.py:test_s07_fake_adapter_carries_attachments_without_credentials`（`AttachmentRef` 字段集恰好等于设计集合；与凭据字段集交集为空） | 真实 `FakeChannelAdapter` 推入 → `iter_events` 取回（未 mock） | verified |

**本次回归**：
- `uv run pytest -q tests/console_channel tests/gateway` → **242 passed**（`harness-im#RULE-im-001` verifier）
- `uv run pytest -q tests/test_contracts.py tests/console_platform tests/console_tasks` → **165 passed**（契约消费方回归）
- `uv run mypy apps packages` → **Success: no issues found in 260 source files**
- `uv run ruff check packages/contracts/src ...` → **All checks passed**
- `uv run pytest -q tests/agent_worker` → **236 passed**；`uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py` → **177 passed**（`harness-worker#RULE-worker-001` 局部承接的自证）

> **偶发甄别（记录）**：`harness-worker` verifier 首跑出现 1 条失败（`tests/agent_worker/test_worker_metrics.py::test_b212_schedule_and_delivery_increment_counters`）。按"先怀疑环境/顺序、再改实现"处理：该用例**单跑通过**、**stash 掉本次改动后在基线上也通过**、**整域重跑 236 passed** ⇒ 判定为整跑顺序相关的偶发，与本次类型别名替换无关，未改任何实现。

**实现中的一处偏离（已记录）**：设计 §3.2.3 写"在 contracts 内定义一次 `ChannelName`"，落点选在 **`enums.py`** 而非 `channel.py` —— `channel.py` 依赖 `tasks.py` 取 `ContractModel`，把别名放 `channel.py` 会让 `tasks.py` 反向依赖它而形成**循环导入**。`enums.py` 是只依赖标准库的叶子模块，语义相同；机检（B-07）相应断言 `enums.py` 为唯一定义处。方向未变，仅落点更合理。
- B-05: not_configured — automated command not_configured; run_id=875f5933215b44d7b31a2444036134f8 (confirmed_by: runner)
- B-07: not_configured — automated command not_configured; run_id=875f5933215b44d7b31a2444036134f8 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=4095b5dd4d5641a2ac7b08e27b7943b2 (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=4095b5dd4d5641a2ac7b08e27b7943b2 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] resumed (in-progress)
- [2026-10-02] completed (done)
## TASK-002: 企微入站分流 + 媒体下载解密

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `wecom-inbound-media.design.md#3.2.1`, `#3.2.2`, `#3.4 接口设计`, `#3.1 方案选型`
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-08, E-04

### Description

渠道适配层不再对非文本消息一律 `return None`：按 `msgtype` 分流——`image` / `file` 各成一个媒体引用，`mixed` 内的**每个 `image` 项各成一个**（`text` 项并入消息文本）；`voice` / `video` **不进媒体管道、不下载**，改以 `ChannelEnvelope.unsupported_media` 送达核心域。取件方式（`url` + `aeskey`）是**企微私有**，不得提升为跨渠道通用接口（AD-8）。**下载由本仓自实现**（SDK 的 `download_file` 无字节上限、超时不可配、密钥缺失时返回密文），**只复用 SDK 的解密函数**以免算法分叉；`checksum` 必须在**解密之后**计算。

### Checklist

- [x] `_to_inbound_message` 按 `msgtype` 分流：`image`/`file` → 1 个媒体引用；`mixed` → 逐个 `image` 项各 1 个（`text` 项并入消息文本）；`voice`/`video`/未知 → 置 `unsupported_media` 且**不产出媒体引用、不下载**
- [x] `WeComSdkPort.download_media(url, aes_key, *, max_bytes)` 返回 `WeComMediaContent`（已解密字节 + `media_type` + `filename` + `checksum`；大小即 `len(data)`，不另存一份），且**不复用** SDK 的 `download_file`
- [x] 下载实现强制 30s 超时与**流式**字节上限（累计超限立即中止，不读完）；失败以明确异常类型上抛（区分超时/网络/解密失败），`aes_key` 缺失走**解密失败**路径而非返回密文
- [x] `media_type` 在解密后判定（魔术字节 → 文件名扩展名 → `application/octet-stream` 兜底）；**不采信**下载响应的 `Content-Type`
- [x] `checksum` 在解密后计算
- [x] 契约新增 `ChannelEnvelope.unsupported_media`（渠道中性取值）+ 核心域兜底：不进入 Run、记结构化日志、**不产生回复**（回复由 TASK-004 接线，E-01）
- [x] [S-08][integration] `mixed` 图文混排（2 个 image 项 + 1 个 text 项）（真实边界：分流解析层，不 mock 帧）；断言两项各成一个候选、文本并入消息文本、候选与同消息单图共享数量门控
- [x] [E-04][integration] 覆盖密钥缺失与密钥不匹配两种解密失败（真实边界：本地真实 HTTP 服务 + 真实 AES 密文 + 官方 `decrypt_file`，**解密不 mock**）；断言①失败**不返回任何内容**（绝不退化为返回密文）且异常文本不含凭据 ②**日志中**不出现 `aes_key` 与媒体明文 URL。**两条不在本任务范围**（不是"已闭合"）：**审计腿**依赖 TASK-004 的审计写入接线；**"无 artifact 行/无残留文件"**在 TASK-002 结构上无从断言——本任务不写盘也不写库（引用只描述"已到手的字节"，持久化在 TASK-004）
- [x] 回归：既有文本路径用例全绿
- [x] 运行 verifier：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`（`harness-secret#RULE-secret-001`）；**12 passed**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | integration | 回调帧分流解析层 | `mixed` 的两项各成一个候选；文本并入；与同消息单图共享数量门控 | `tests/gateway/test_wecom_media.py` | `uv run pytest -q tests/gateway/test_wecom_media.py` | verified |
| E-04 | integration | 解密路径、日志输出（审计腿见 TASK-004） | 密钥缺失/不匹配均明确失败且不返回密文；密钥与 URL 不出现在日志 | `tests/gateway/test_wecom_media.py`（+ 原 verifier `tests/test_logging_redaction.py`） | `uv run pytest -q tests/gateway/test_wecom_media.py tests/test_logging_redaction.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | 把源码改动移开（`git stash push -- apps packages`）后本文件整体 collection error：`ModuleNotFoundError: No module named 'muad_im_gateway.channels.wecom.media'` | PASS: `37 passed` | `tests/gateway/test_wecom_media.py`：`test_mixed_message_yields_one_ref_per_image_item_and_merges_text`（两项各成一个引用、文本并入）、`test_mixed_refs_are_the_same_shape_as_separate_image_messages`（**与两条单图消息的引用逐字段相等** ⇒ 数量门控不可能对两者计数不同）、`test_adapter_emits_envelope_with_merged_text_and_no_attachments` | 原始回调帧 → 真实 `_AibotClientPort` → 真实 `WeComAdapter` → `iter_events()` 取 `ChannelEnvelope`；**帧不 mock** | verified |
| E-04 | 同上 | PASS: `37 passed` | `::test_decrypt_failures_are_explicit_and_never_return_ciphertext[None]` / `[上界外的密钥]`、`::test_decrypt_failure_logs_reason_without_credentials`、`::test_success_logs_metadata_without_credentials`、`::test_oversized_body_is_aborted_before_the_whole_response_is_read` | 本地真实 HTTP 服务 + 真实 AES-256-CBC 密文 + 官方 `crypto_utils.decrypt_file`，**解密不 mock**。**对照实验**（同一份密文跑官方 SDK `WSClient.download_file`）：密钥缺失时它 `WARN` 一句后**把密文当文件返回**（实测 `bytes == ciphertext` 为真）、密钥不匹配时抛裸 `RuntimeError`；本实现两腿都抛**可区分**的 `WeComMediaDecryptError` 且不返回任何内容 | verified（**日志腿**；审计腿属 TASK-004，见其清单） |

**本任务的其余覆盖（同文件）**：
- `test_voice_and_video_are_marked_unsupported_without_media`：语音/视频**不进媒体管道、不产生取件**，以 `unsupported_media` 表达 —— 消息不再在渠道边界消失（RULE-01）
- `test_structurally_invalid_frames_are_still_ignored`：缺 `msgid`/`from`、图片缺 `url` 的**结构不合法**帧仍不产生消息 —— 与"收不了的消息"是两回事
- `test_checksum_is_computed_after_decryption`：**同一明文用两把不同密钥**加密，解密后校验和必须相同（若在密文上算校验和，这里必红）
- `test_slow_response_hits_the_timeout` / `test_missing_media_url_is_a_network_failure`：超时与 URL 失效可区分（E-03 的两条腿）
- `test_media_type_is_detected_after_decryption` / `test_filename_parsing_covers_both_disposition_forms`：类型判定顺序与 `Content-Disposition` 两种形式（含 RFC 5987 非 ASCII 名）

**本次回归**：
- `tests/gateway tests/architecture tests/test_attachment_contract.py` → **263 passed**（含本任务新增 37 条）
- `tests/acceptance/im_gateway` → **65 passed**（inbound 兜底与适配器改动的真实栈回归）
- verifier `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py` → **12 passed**
- `uv run mypy apps packages` → **Success: no issues found in 265 source files**
- `uv run ruff check apps packages tests/gateway/test_wecom_media.py` → **All checks passed**

**实现补充**：`channels/wecom/media.py` 自实现流式取件，三条理由都是可复现事实（SDK 无字节上限、超时不可配且写死 10s、密钥缺失返回密文——见上表对照实验），**只复用 SDK 的解密函数**。`max_bytes` 由调用方传入：产品策略常量的唯一定义处是门控的 `MAX_ATTACHMENT_BYTES`，传参避免了在下载器里复制一份数值、也避免 `channels/` 反向依赖 `application/`。`media_type` 一律在**解密后**判定，不采信下载响应的 `Content-Type`（那描述的是加密载荷）。`WeComMediaRef` 的 `url`/`aes_key` 都设了 `repr=False`——默认 repr 会随任何一次 f-string 把取件凭据写进日志。
- S-08: verified — automated command passed; run_id=586860170ba5421fb7f014475437aa20 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=586860170ba5421fb7f014475437aa20 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=415d5b7e6f32435c8d42c1403e40cb59 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=415d5b7e6f32435c8d42c1403e40cb59 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-003: 附件门控（纯函数）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `wecom-inbound-media.design.md#2.3.2 字段约束`, `#3.4 接口设计`
- **Spec-Refs**:
- **Acceptance-Refs**: B-01, B-02, B-03, B-04

### Description

门控判定为**纯函数、无 IO**，便于边界值单测。P0 以模块常量生效（20 MiB / 5 个 / 白名单），P1 再配置化。文件名不得参与路径拼接。

### Checklist

- [x] 实现门控纯函数：输入候选附件序列，输出 `GateDecision`（通过/拒绝 + 原因码）
- [x] P0 常量：单文件 20 MiB、单消息 5 个、类型白名单（图片 png/jpeg/gif/webp；文档 pdf/docx/txt/md/xlsx/pptx）
- [x] 路径安全：产物键由系统生成，原始文件名仅入元信息
- [x] [B-01][unit] 单文件大小 == 20 MiB → 接收（真实边界：门控纯函数）
- [x] [B-02][unit] 单文件大小 == 20 MiB + 1 B → 拒绝且原因码为"超大小上限"
- [x] [B-03][unit] 附件数 == 5 → 接收；== 6 → 拒绝
- [x] [B-04][unit] 文件名 `../../etc/passwd` 与空串 → 落盘路径不含用户输入，原文件名仅入元信息
- [x] 运行门控单测并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | 门控纯函数 | 恰好等于上限 → 接收 | `tests/gateway/test_attachment_gate.py::test_b01_size_exactly_at_limit_is_accepted` | uv run pytest -q tests/gateway/test_attachment_gate.py | verified |
| B-02 | unit | 门控纯函数 | 超 1 字节 → 拒绝 + 原因码 | `tests/gateway/test_attachment_gate.py::test_b02_size_one_byte_over_limit_is_rejected` | uv run pytest -q tests/gateway/test_attachment_gate.py | verified |
| B-03 | unit | 门控纯函数 | 5 接收 / 6 拒绝 | `tests/gateway/test_attachment_gate.py::test_b03_attachment_count_boundary` | uv run pytest -q tests/gateway/test_attachment_gate.py | verified |
| B-04 | unit | 路径解析函数 | 路径不含用户输入 | `tests/gateway/test_attachment_gate.py::test_b04_storage_key_never_contains_user_input` | uv run pytest -q tests/gateway/test_attachment_gate.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: `ModuleNotFoundError: No module named 'muad_im_gateway.application.attachment_gate'` | PASS: `7 passed` | `tests/gateway/test_attachment_gate.py::test_b01_size_exactly_at_limit_is_accepted`（`size == 20 MiB` → `decision.ok` 且 `accepted == 1`） | 门控纯函数，无 IO、无 mock | verified |
| B-02 | FAIL: 同上（模块导入期即失败） | PASS: `7 passed` | `::test_b02_size_one_byte_over_limit_is_rejected`（`20 MiB + 1` → 拒且 `code == ATTACHMENT_TOO_LARGE`） | 同上 | verified |
| B-03 | FAIL: 同上 | PASS: `7 passed` | `::test_b03_attachment_count_boundary`（5 → 全收；6 → 前 5 收、第 6 拒且 `code == ATTACHMENT_COUNT_EXCEEDED`） | 同上 | verified |
| B-04 | FAIL: 同上 | PASS: `7 passed` | `::test_b04_storage_key_never_contains_user_input`（文件名 `../../etc/passwd` 与空串下，产物键均不含其任何片段；原文件名仍作为元信息保留） | 路径解析为纯字符串拼接，无 IO | verified |

**本次回归**：
- `uv run pytest -q tests/console_channel tests/gateway` → **249 passed**（含本任务新增 7 条）
- `uv run mypy apps packages` → **Success: no issues found in 262 source files**；`ruff` → **All checks passed**

**补充覆盖（超出场景表，服务于清单项）**：`test_constants_match_the_designed_defaults`（常量与设计 §2.3.2 一致）、`test_type_whitelist_rejects_unknown_media_type`（白名单外拒绝，E-01 的判定来源）、`test_decision_is_pure_and_order_preserving`（纯函数 + 保序）。

**留给后续的**：原因码目前是模块内的字符串常量，**尚未**写进 `config/api-messages.yaml` —— 目录词条与用户可见反馈由 TASK-004 承接。此处只把码定死，避免两处各写一份。
- B-01: verified — automated command passed; run_id=6362dc61c2d2460bbb160a87a9b4a5dc (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=6362dc61c2d2460bbb160a87a9b4a5dc (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=6362dc61c2d2460bbb160a87a9b4a5dc (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=6362dc61c2d2460bbb160a87a9b4a5dc (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-004: 附件落盘 + 接收反馈与审计

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-012
- **Source**: `wecom-inbound-media.design.md#3.2.1`, `#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-skill#RULE-skill-001, harness-log#RULE-log-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-04, S-07, E-01, E-02, E-03, E-07

### Description

把通过的附件写入**共享 artifact store**（相对 `storage_key`，原子替换），网关**不写本地盘、不写 DB**（artifacts 行由 Runtime 写）。所有"收不了"的情况给出用户可见回复并写审计——这是本需求消除"静默丢弃"的落点。反馈文案**不硬编码**，走既有消息目录（`catalog.message(code, locale)`），在 `config/api-messages.yaml` 新增 zh-CN/en-US 词条。

### Checklist

- [x] **取件的跨层接缝（本任务最关键的设计点）**：新增可选能力协议 `AttachmentSource`（仿既有 `AdapterDegradation` / `StreamFinalizer` 写法）——`attachment_count(envelope)` + `fetch_attachment(envelope, index, *, max_bytes)`。**核心域只给 envelope、下标与上限，拿回已解密字节与元信息，全程看不见 `url`/`aes_key`**（AD-8）。适配器内部按 `message_id` 保留通道私有引用，**必须有驱逐**（TTL/容量）：命令消息、重复投递、空载荷兜底这几条路径**永远不会来取件**
- [x] 附件写入共享 store：新增 im-gateway 对 `muad-artifact-store` 的依赖；key 为**相对路径**；沿用"临时文件 + `os.replace`"原子替换。**部署同步**：`deploy/k8s/base/im-gateway.yaml` 补 artifacts 卷挂载（现在只有 `fsGroup` 与来自 configMap 的 `ARTIFACT_ROOT`，**没有 volumeMounts/volumes** ⇒ 字节会写进容器临时盘，runtime 在自己的 PVC 上按 key 找不到）
- [x] **取件顺序**：放在 `_handle_message` 的 `_resolve` **之后**、`RunRequest` 之前——未绑定/无权限是天然早退点，"能收才去拉"，把 AD-1-B 下的无主字节压到最小（设计 R-07）
- [x] 门控接线为**两段**（design §3.2.1 / API-07）：**预检** `evaluate_precheck(count)` 在取件之前判数量（纯函数，无 IO）→ 取件 → **实检** `evaluate_gate(candidates)` 用解密后的真实 `media_type`/`size` 判类型与大小；部分拒绝不拖累其余
- [x] `unsupported_media` 兜底升级为**用户可见回复 + 审计**（E-01）：TASK-002 只让它"不进入 Run"，本条负责把回复补上，并删除兜底注释里的过渡说明
- [x] 门控拒绝 / 取件失败（超时·网络·超限·解密） / 落盘失败 / 不支持类型 → 各自映射文案与审计码，**无静默路径**
- [x] 反馈文案经消息目录取；`config/api-messages.yaml` 补齐 zh-CN 与 en-US 词条
- [x] 审计记录含 `external_message_id`、附件数、拒绝原因码（供 PRD §2.2 指标度量）；**并闭合 E-04 的审计腿**——审计行中不得出现 `aes_key` 与媒体明文 URL（TASK-002 只覆盖日志腿，见其清单）；**写审计经 TASK-012 的 `POST /internal/channel/audit`——本任务只调用、不建表**
- [x] [S-04][E2E] 一条消息带 3 个附件（含图片与文档）（真实边界：回调 → 落盘 → 契约 → 工具，不 mock）；断言三个附件各自落盘、可分别读取、互不覆盖
- [x] [S-07][integration] `FakeChannelAdapter` 以**与企微不同的取件路径**（不经 url/aes_key）产出带附件的 envelope（真实边界：门控 → 契约 → 落盘，不含企微代码路径）；断言门控/契约/落盘行为与企微路径一致
- [x] [E-01][E2E] 不受支持类型 → 不落盘 + 审计 + 用户收到明确说明
- [x] [E-02][E2E] 超上限文件 → 回复中**包含上限数值** + 审计
- [x] [E-03][E2E] 下载失败 → 明确失败说明 + 审计
- [x] [E-07][integration] 同一消息重投 → 去重，不产生重复产物
- [x] 运行 verifier：`uv run pytest -q tests/architecture`（`harness-arch#RULE-arch-001`）；记录输出
- [x] 运行 verifier：`uv run pytest -q tests/test_skill_artifact_cache.py`（`harness-skill#RULE-skill-001`）；记录输出
- [x] 运行 verifier：`uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py`（`harness-log#RULE-log-001`）；记录输出
- [x] 运行 verifier：`uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`（`harness-i18n#RULE-i18n-001`）；记录输出并填写 Acceptance Evidence
- [x] 运行 verifier：`uv run pytest -q tests/gateway`（`harness-im#RULE-im-001`：`inbound.py` 属本 Rule 的路径映射，局部承接）；记录输出

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 回调、落盘、契约、工具 | 3 附件各自落盘且可分别读取 | `tests/acceptance/im_gateway/test_wecom_attachments.py::test_s04_three_attachments_land_and_are_readable_separately` | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | verified |
| S-07 | integration | 门控、契约、落盘 | 与企微路径行为一致；核心域无渠道分支 | `tests/gateway/test_inbound_attachment_flow.py::test_s07_two_fetch_paths_produce_identical_outcomes`（另三条同族：入口不拦纯媒体 / 数量上限文案 / 取件失败不静默） | uv run pytest -q tests/gateway/test_inbound_attachment_flow.py tests/gateway/test_inbound_attachment_store.py | verified |
| E-01 | E2E | 回调、反馈投递、审计表 | 不落盘 + 审计 + 明确回复 | `tests/acceptance/im_gateway/test_wecom_attachments.py::test_e01_unsupported_type_is_explained_and_audited` | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | verified |
| E-02 | E2E | 同上 | 回复含上限数值 | `tests/acceptance/im_gateway/test_wecom_attachments.py::test_e02_oversized_file_reply_carries_the_limit_and_an_audit` | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | verified |
| E-03 | E2E | 同上 | 明确失败说明 + 审计 | `tests/acceptance/im_gateway/test_wecom_attachments.py::test_e03_take_failure_is_explained_and_audited` | uv run pytest -q tests/acceptance/im_gateway/test_wecom_attachments.py | verified |
| E-07 | integration | 幂等键、落盘 | 重投不产生重复产物 | `tests/gateway/test_inbound_attachment_flow.py::test_e07_redelivery_creates_no_second_run_and_no_duplicate_artifact`（真栈腿：`tests/acceptance/im_gateway/test_wecom_attachments.py::test_e07_redelivered_message_produces_no_duplicate_artifact`） | uv run pytest -q tests/gateway/test_inbound_attachment_flow.py tests/gateway/test_inbound_attachment_store.py | verified |

> **完成说明（2026-10-02）**：接线与六条场景测试均已落地并跑绿（E2E 另在真实栈上单跑 5 passed）。
>
> **接线中发现并修掉的一处真问题**：入口护栏 `_carries_no_payload` 原先只看 `envelope.attachments`，
> 而该字段按定义只描述"**已经拿到手的**字节"（AD-8）——纯图片/文件消息在取件前它必然为空，
> 于是**所有纯媒体消息在入口就被拦掉，整条附件链路是死的**。修法：护栏改为"文本 / 附件 /
> `unsupported_media` / 适配器待取件数（`AttachmentSource.attachment_count`）皆空才算无载荷"，
> 并把"取件前引用消失"这一条罕见路径补成明确失败（反馈 + 审计），不留静默路径。
> S-07 的 `test_s07_media_only_envelope_is_not_dropped_at_the_entry` 就是钉这条的回归。
>
> **S-07 的验法**：同一条 `msgid` 喂两条适配器（`FakeChannelAdapter` 内存字节、无 url/aes_key；
> 生产 `WeComAdapter` 走真实 HTTP + 真实 AES 密文），断言门控结论、契约产出、反馈、审计与落盘
> 字节**逐项相同**。为此把媒体服务与官方加密算法抽到 `tests/e2e/wecom_media_server.py` 共用，
> `FakeChannelAdapter` 补上 `AttachmentSource` 能力（`FakeBlob`：**没有取件凭据**）。

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | 验收类，实现已就位，首跑即通过（不制造 RED） | PASS: `5 passed in 23.31s`；runner 单跑 exit_code=0 | `tests/acceptance/im_gateway/test_wecom_attachments.py::test_s04_three_attachments_land_and_are_readable_separately`（三份产物键 `inbound/<msgid>/{0,1,2}` 各自字节 == 各自明文；`runtime.artifact` 三行的 storage_key/media_type/size/checksum 与磁盘逐行一致；审计一行 `RECEIVED`，count/accepted/total 与落盘事实一致） | 真实 WS 探针 + 真实 Gateway/Runtime/Console/Worker 进程 + 真实 HTTP 媒体源（真实 AES-256-CBC 密文）+ 真实共享 artifact store + 真实 PG（`runtime.artifact` 逐行回读） | verified |
| S-07 | FAIL（真问题）：`test_s07_media_only_envelope_is_not_dropped_at_the_entry` —— 纯媒体消息在入口被 `_carries_no_payload` 拦掉，`run_requests == []`、无产物、无反馈 | PASS: `17 passed`；runner exit_code=0 | `tests/gateway/test_inbound_attachment_flow.py::test_s07_two_fetch_paths_produce_identical_outcomes`（同 msgid 两条路径的 refs/feedback/audits/落盘字节/进 Run 的消息**逐项相等**）；同文件另三条：入口不拦纯媒体、数量上限文案数值来自门控常量、取件失败有反馈+审计且不建 Run | `FakeChannelAdapter`（内存字节、无 url/aes_key）+ 生产 `WeComAdapter`（真实 HTTP + 真实解密）跑**同一段编排**；真实 Redis（E-07）；真实文件系统 | verified |
| E-01 | 验收类，首跑即通过 | PASS: `5 passed in 23.31s` | `::test_e01_unsupported_type_is_explained_and_audited`（回复 == 消息目录 `UNSUPPORTED_MEDIA` zh-CN 原文；`inbound/<msgid>/` 目录不存在；审计 `REJECTED`/`UNSUPPORTED_MEDIA` 且 count/accepted 均为 0） | 真实回调帧（voice）→ 真实 Gateway → 真实 Console 内部端点 → 真实 PG `control.im_inbound_audit` 回读 | verified |
| E-02 | FAIL（**断言口径错，非产品缺陷**）：首跑 `assert media_server.sent[path] < len(oversized)` 得 `22020112 < 22020105` —— 拿**明文**长度与**密文**写出字节比较（差 7 字节 PKCS#7 填充） | PASS: `5 passed in 23.31s` | `::test_e02_oversized_file_reply_carries_the_limit_and_an_audit`（回复 == 目录 `ATTACHMENT_TOO_LARGE` 且含 `str(MAX_ATTACHMENT_BYTES)`；无产物；审计 `FAILED`/`ATTACHMENT_TOO_LARGE`；服务端写出字节 < 它持有的密文长度 ⇒ **没读完就中止**） | 真实 32 MiB 级密文经真实 HTTP 分块下发（服务端 4ms 节流让中止可观测）+ 真实 PG 审计行 | verified |
| E-03 | 验收类，首跑即通过 | PASS: `5 passed in 23.31s` | `::test_e03_take_failure_is_explained_and_audited`（媒体 URL 404 → 回复 == 目录 `ATTACHMENT_FETCH_FAILED`；无产物；审计 `FAILED`/`ATTACHMENT_FETCH_FAILED`，attachment_count=1、accepted=0） | 真实 HTTP 404 响应 + 真实 PG 审计行 | verified |
| E-07 | 验收类，首跑即通过 | PASS: `17 passed`（integration 腿）+ `5 passed`（真栈腿） | `tests/gateway/test_inbound_attachment_flow.py::test_e07_redelivery_creates_no_second_run_and_no_duplicate_artifact`（真 Redis：重投后 run 请求数仍 1、产物摘要集合不变、审计仍 1 行、`im:dedupe:WECOM:<msgid>` 存在且 TTL ∈ (0, 600]）；`test_wecom_attachments.py::test_e07_redelivered_message_produces_no_duplicate_artifact`（真栈：直方图同上，`runtime.artifact` 仍 1 行） | 真实 Redis（幂等键）+ 真实文件系统 + 真栈 PG | verified |

**本次回归**：
- `uv run pytest -q tests/gateway` → **267 passed**（`harness-im#RULE-im-001` verifier；含本任务新增 17 条）
- `uv run pytest -q tests/acceptance/im_gateway` → **70 passed in 397.48s**（真实栈全量，含本任务新增 5 条）
- `uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py` → **18 passed**（`harness-api#RULE-api-001`）
- `uv run pytest -q tests/acceptance/test_foundation_i18n.py` → **6 passed**；`uv run python scripts/check_frontend_i18n.py` → **i18n keys OK: 717**（`harness-i18n#RULE-i18n-001`）
- `uv run mypy apps packages` → **Success: no issues found in 267 source files**；`uv run ruff check .` → **All checks passed**

**Done Gate 首跑 block，暴露并修掉一处真缺陷（记录）**：`harness-api#RULE-api-001` 的 verifier 断言"消息目录码 ↔ `ErrorCode` 双向一致"，而 7 条入站附件词条只加进了 `config/api-messages.yaml`、**没有同步 `ErrorCode`** ⇒ `tests/test_error_catalog.py` 直接红。修法是把 7 个码登记进 `ErrorCode`（`packages/api-kit/src/muad_api/error_codes.py`），而不是把词条从目录里拿掉——词条是 E-01/E-02/E-03 反馈文案的唯一来源。这条是"词条与枚举必须同步"的机制在起作用，不是接线本身写错。

**Done Gate 裁决**：`pass`（`cf_task_workflow.py finish --task TASK-004`，rc=0；E2E 场景另经 `cf_acceptance_runner --include-e2e --owner TASK-004` 实跑并写入证据，六条全 `passed`）。deferred：9 个 verifier（需求级 `verify-e2e` 收口）+ 1 个 heavy validator（归档 `cf_validation` 收口）。

- S-04: e2e_deferred — automated command e2e_deferred; run_id=10523afa3c104dd59f190c68b13138e3 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=10523afa3c104dd59f190c68b13138e3 (confirmed_by: runner)
- E-01: e2e_deferred — automated command e2e_deferred; run_id=10523afa3c104dd59f190c68b13138e3 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=10523afa3c104dd59f190c68b13138e3 (confirmed_by: runner)
- E-03: e2e_deferred — automated command e2e_deferred; run_id=10523afa3c104dd59f190c68b13138e3 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=10523afa3c104dd59f190c68b13138e3 (confirmed_by: runner)
- S-04: e2e_deferred — automated command e2e_deferred; run_id=24cfd56a879644e18a86312038950eb1 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=24cfd56a879644e18a86312038950eb1 (confirmed_by: runner)
- E-01: e2e_deferred — automated command e2e_deferred; run_id=24cfd56a879644e18a86312038950eb1 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=24cfd56a879644e18a86312038950eb1 (confirmed_by: runner)
- E-03: e2e_deferred — automated command e2e_deferred; run_id=24cfd56a879644e18a86312038950eb1 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=24cfd56a879644e18a86312038950eb1 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=00b33a3e8ee84240886bb29c91405a4b (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=00b33a3e8ee84240886bb29c91405a4b (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=00b33a3e8ee84240886bb29c91405a4b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=00b33a3e8ee84240886bb29c91405a4b (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=00b33a3e8ee84240886bb29c91405a4b (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=00b33a3e8ee84240886bb29c91405a4b (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-005: 模型多模态内容形态

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`, `#3.1 方案选型`
- **Spec-Refs**: harness-model#RULE-model-001
- **Acceptance-Refs**: S-03, B-06

### Description

把模型消息的内容类型从"只能是字符串"放宽为可承载多模态内容块，图片以图像块进入上下文。**必须走唯一的 OPENAI 兼容协议**（不引入第二协议）。兼容护栏：纯文本路径的请求体**逐字节不变**。

### Checklist

- [x] 定义 `ImagePart`；`ModelMessage.content` 放宽为 `str | tuple[str | ImagePart, ...]`
- [x] provider 组装：`str` → 原样输出（字段集不变）；元组 → `content` 数组
- [x] 不改 `model_id` 显式绑定口径、不引入第二协议
- [x] [S-03][integration] 纯文本会话（真实边界：模型 provider 请求体，不 mock 组装层）；断言请求体与改造前基线**逐字节相同**
- [x] [B-06][integration] 内容形态为纯字符串时（真实边界：provider 组装）；断言输出 `content` 为**字符串而非数组**，且字段集不变
- [x] 运行 verifier：`uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled`（`harness-model#RULE-model-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | provider 请求体 | 纯文本请求体逐字节不变 | `tests/agent_core/test_openai_provider.py::test_s03_plain_text_request_body_is_unchanged` | uv run pytest -q tests/agent_core/test_openai_provider.py | verified |
| B-06 | integration | provider 组装 | 纯文本 content 为字符串、字段集不变 | `tests/agent_core/test_openai_provider.py::test_b06_plain_text_content_stays_a_string` | uv run pytest -q tests/agent_core/test_openai_provider.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL: `ImportError: cannot import name 'ImagePart' from 'muad_agent_core.model'` | PASS: `18 passed` | `tests/agent_core/test_openai_provider.py::test_s03_plain_text_request_body_is_unchanged`（四种角色覆盖；`body["messages"]` 与冻结基线逐字段相等，且整份 body 的规范化 JSON 与基线一致） | 走**完整 provider 组装 + HTTP 序列化**，`MockTransport` 只拦传输、不 mock 组装层 | verified |
| B-06 | FAIL: 同上（模块导入期即失败） | PASS: `18 passed` | `::test_b06_plain_text_content_stays_a_string`（`content` 是 `str` 而非数组；文本消息字段集 == {role, content}，tool 消息 == {role, content, tool_call_id}） | 同上 | verified |

**本次回归**：
- `uv run pytest -q tests/console_platform/test_models_api.py` → **8 passed**；`tests/console_platform/test_agents_api.py -k disabled` → **1 passed**（`harness-model#RULE-model-001` verifier）
- `uv run pytest -q tests/agent_core tests/agent_runtime` → **258 passed**（executor 装配点 + provider 变更的回归；含本任务新增 3 条）
- `uv run mypy apps packages` → **Success: no issues found in 262 source files**；`ruff` → **All checks passed**

**设计里的一条待定项在此定下（R-09）**：设计 §3.4 API-05 写"`data_base64` 或 url，实现时二选一，见 §5 R-09"。选 **base64 data URL** —— 产物落在集群内共享卷（RWX PVC）上，模型供应商**访问不到**；走预签名 URL 需要额外的对外暴露与时效管理。data URL 是 OpenAI 兼容协议的原生形态，且与"本地不可达"这个部署事实相容。

**实现中的一处必要附带改动**：`executor.py` 把 assistant 回合落事件时用了 `message.content`，放宽类型后它可能是内容块元组。新增 `text_of(content)` 取其文本视图（纯文本原样返回），事件载荷因此仍只收文本。助手回合本就是文本，此处是类型安全的收口而非行为变化。
- S-03: verified — automated command passed; run_id=23692145c6c84cd6b34a9c5d8224b1c7 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=23692145c6c84cd6b34a9c5d8224b1c7 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-006: Runtime 附件落库 + 上下文组装

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-005
- **Source**: `wecom-inbound-media.design.md#3.2.1`, `#3.3 数据设计`
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001, harness-data#RULE-data-001
- **Acceptance-Refs**: S-01, S-05

### Description

Runtime 在 Run 建立后把消息里的 `AttachmentRef` 落成 `runtime.artifact` 行（`run_id` 非空 ⇒ 既有 `ck_artifact_run_task_xor` 继续成立，**不改 schema**），并按 AD-3-B 组装上下文：**当前消息**的图片进内容块，**历史轮次**附件只保留文本引用。附件属消息载荷，不进 Snapshot 冻结范围。

### Checklist

- [x] Run 建立时把 `AttachmentRef` 落成 `artifact` 行（`artifact_type=INBOUND_*`，`storage_key` 相对键，`metadata_json` 存 filename/source/external_message_id）
- [x] 组装上下文：当前消息图片 → `ImagePart` 内容块；历史附件 → 文本引用
- [x] 附件不进入 Snapshot 冻结范围（仅 Agent/Model/Skill/MCP 版本按既有口径冻结）
- [x] [S-01][E2E] **登记**（不在编码期执行）：`tests/acceptance/wecom_attachments/test_inbound_image_e2e.py`；真实边界＝真实回调桩 → 真实 PG/Redis → 真实落盘 → 真实模型请求体，不 mock。状态 `e2e_deferred` —— 其上游（TASK-002 网关下载、TASK-004 落盘/反馈）尚未落地，此刻**写不出可执行的 E2E**，由需求级终验（TASK-010）闭合
- [x] [S-05][E2E] **登记**（本任务仅引用；manifest 归属 TASK-007）：真实边界＝真实上下文组装 + 真实模型。状态 `e2e_deferred`
- [x] 运行 verifier：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`（`harness-snapshot#RULE-snapshot-001`）；记录输出
- [x] 运行 verifier：`uv run pytest -q tests -k schema_parity`（`harness-data#RULE-data-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 回调、PG/Redis、落盘、模型请求体 | 落盘可读回；请求体含图像块；回答体现图中内容 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |
| S-05 | E2E | 上下文组装、模型 | 历史图片作为新内容块重发 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 编码期不执行（E2E） | 编码期不执行（E2E） | 待需求级终验登记 | 上游 TASK-002/TASK-004 未落地，E2E 通路尚未连通 | e2e_deferred |
| S-05 | 编码期不执行（E2E） | 编码期不执行（E2E） | 待需求级终验登记 | 本任务仅引用；manifest 归属 TASK-007 | e2e_deferred |

**本任务的功能级证据**（`tests/agent_runtime/test_inbound_attachments.py`，6 passed；RED = `ModuleNotFoundError: ...inbound_attachments`）：

| 覆盖点 | 断言 |
|--------|------|
| 落库（真实 PG） | `artifact` 行的 `run_id` 非空、`task_id` 为空（`ck_artifact_run_task_xor` 继续成立）、`storage_key` 不被重写（AD-1-B）、`metadata_json` 带回 `kind`/`filename`/`source` |
| 类型映射 | IMAGE/DOCUMENT/OTHER → `INBOUND_*`；未知 kind 兜底 `INBOUND_OTHER` |
| 文本引用 | 带可得 `artifact_id`（模型/工具靠它寻址）、文件名与 media_type；空列表返回空串 |
| 当前消息组装 | 无读取器时**仍留引用**（不假装没收到附件）；图片内联为 `ImagePart`（base64 与源字节一致）；文档不内联只留引用；字节读不到时退回引用而非整轮失败 |

**装配（三处，均已接上）**：
- `run_service` Run 创建处：`persist_inbound_attachments` 与 Run/Snapshot/事件**同事务**（只 flush 不 commit —— 先提交会让"Run 建失败但附件行留下"成为可能），并把紧凑摘要写进 `USER_MESSAGE` 载荷。
- `run_service._build_executor`：按 `run_id` 查回入站产物 → `build_current_content` → `ExecutorRequest.input_content`（无附件时原样返回文本，故无额外判空分支；`ix_artifact_run` 上的一次索引查询）。
- `context_builder._to_messages`：历史 `USER_MESSAGE` 按载荷里的附件摘要渲染文本引用（不回查产物表 —— 历史是逐条回放的热路径）。

**本次回归**：
- `harness-snapshot#RULE-snapshot-001` verifier：`test_snapshot_freeze.py` + `test_run_reaper.py` → **4 passed**；`tests/agent_runtime -k "executor or resolve"` → **20 passed**
- `harness-data#RULE-data-001` verifier：`tests -k schema_parity` → **35 passed**
- `tests/agent_runtime` → **194 passed**（含本任务新增 6 条）
- `uv run mypy apps packages` → **Success: no issues found in 263 source files**；`ruff` → **All checks passed**

**一处需要记录的计划缺陷（不是本任务能修的）**：`S-01` 归属 TASK-006，但其 E2E 通路要求网关侧已能产出附件引用（TASK-002 下载解密、TASK-004 落盘/契约），而 TASK-006 的 `Depends` 只有 TASK-001/TASK-005。因此**编码期无法写出可执行的 S-01 E2E**，只能登记为 `e2e_deferred`；真正闭合依赖 TASK-010 的需求级终验。这个依赖缺口不影响本任务的实现正确性，但会让"每个 P0 任务都能自证其场景"这条预期在 TASK-006 上落空。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=0680a452741547c89ada3efee1878d49 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-007: 附件读取工具（文档抽取）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`, `#3.1 方案选型`
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: S-02, S-05, E-05, E-06

### Description

新增**类型无关**的附件读取工具：`read_attachment` 按产物标识取内容并在工具内抽取文档文本（pdf/docx/txt/md/xlsx/pptx）；`view_image` 触发运行时追加一条携带图像内容块的 user 消息，让模型可回看历史图片（OpenAI 协议的 tool 角色不能携带图像）。工具入口必须做**租户隔离与越权校验**。解析依赖落在 agent 侧，**不进网关**（AD-4-B）。

### Checklist

- [x] 新增 `read_attachment`：文档类返回抽取文本，纯文本类返回内容，图片类提示改用 `view_image`
- [x] 新增 `view_image`：仅限 `kind=IMAGE`，调用后追加携带图像内容块的 user 消息
- [x] 引入解析依赖（`pypdf`/`python-docx`/`openpyxl`/`python-pptx`）并按需导入
- [x] 工具入口按 `tenant_id` 过滤 + 越权校验；受大小上限保护
- [x] [S-02][E2E] 发送内容已知的 pdf 并提问其中事实（真实边界：真实回调桩 → 真实 PG → 真实 artifact → 工具**真实抽取**，不 mock 解析库）；断言抽取文本与文档实际内容一致且回答正确
- [x] [S-05][E2E] 模型调用重看后再提问（真实边界：真实上下文组装 + 真实模型）；断言历史图片作为新内容块被重发
- [x] [E-05][integration] 以租户 B 身份读取租户 A 的 `artifact_id`（真实边界：DB 查询 + 工具越权校验）；断言拒绝且不泄露存在性细节
- [x] [E-06][integration] 加密/损坏文档（真实边界：真实解析库）；断言返回明确错误而非乱码或空内容
- [x] 运行 verifier：`uv run pytest -q tests/console_mcp/test_mcp_rules.py`（`harness-mcp#RULE-mcp-001`，路径映射自动绑定后的局部承接）；记录输出 **3 passed**
- [x] 运行 verifier：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity`（`harness-auth#RULE-auth-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | 回调、PG、artifact、解析库 | 抽取内容与文档一致；回答正确 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |
| S-05 | E2E | 上下文组装、模型 | 历史图片重发为内容块 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |
| E-05 | integration | DB、越权校验 | 跨租户读取被拒 | `tests/agent_runtime/test_attachment_tools.py` | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |
| E-06 | integration | 真实解析库 | 明确错误而非乱码 | `tests/agent_runtime/test_attachment_tools.py` | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-05 | FAIL: `ModuleNotFoundError: No module named 'muad_agent_runtime.application.attachment_tools'` | PASS: `6 passed` | `tests/agent_runtime/test_attachment_tools.py::test_e05_cross_tenant_read_is_rejected_without_leaking_existence`（以另一租户身份读 → 抛 `ATTACHMENT_NOT_FOUND`；断言**错误里不含**对方文件名与标识本身） | 真实 PG（`artifact` 行）+ 工具入口越权校验，未 mock | verified |
| E-06 | FAIL: 同上（模块导入期即失败） | PASS: `6 passed` | `::test_e06_corrupt_document_yields_a_clear_error`（损坏 pdf → `ATTACHMENT_EXTRACT_FAILED` 且**指明是哪个文件**） | **真实解析库**（pypdf），未 mock；断言是「抛明确错误」而非返回乱码/空内容 | verified |
| S-02 | 编码期不执行（E2E） | 编码期不执行（E2E） | 待需求级终验登记 | 上游 TASK-002/TASK-004 未落地，回调通路尚未连通 | e2e_deferred |
| S-05 | 编码期不执行（E2E） | 编码期不执行（E2E） | 待需求级终验登记 | 同上 | e2e_deferred |

**本任务的功能级证据**（6 passed）另含三条支撑断言：
- `test_reads_text_from_a_real_pdf`：内容已知的 pdf 交真实解析库抽取，返回带文件名
- `test_image_read_hints_at_view_image`：图片不能当文本读，提示改用重看工具
- `test_view_image_rejects_non_image`：非图片调用重看必须拒绝
- `test_view_image_follow_up_carries_the_image_part`：**新机制的正面证据** —— 工具结果之后产出的一条 `user` 消息带 `ImagePart`，且 base64 与源字节一致（tool 角色不能携带图像块，这是唯一通道）

**本次回归**：
- `harness-auth#RULE-auth-001` verifier：`test_user_side_relations.py -k s04` → **2 passed**；`tests -k schema_parity` → **35 passed**
- `tests/agent_core tests/agent_runtime tests/gateway` → **483 passed**（含本任务新增 6 条）
- `uv run mypy apps packages` → **Success: no issues found in 264 source files**；`ruff` → **All checks passed**

**实现里新增的一处 agent-core 能力（需记录）**：`ToolDefinition.follow_up_messages` —— 工具结果之后追加消息。存在理由唯一：**OpenAI 协议的 tool 消息不能携带图像块**，所以「重看图片」只能由运行时补一条 user 消息。`runner._execute_tools` 在追加 tool 结果后调用它（入参用 `text_of(result.content)` 显式收窄 —— 工具结果恒为文本）。

**依赖引入**：`pypdf` / `python-docx` / `openpyxl` / `python-pptx` 落在 **agent-runtime**（AD-4-B：不进网关，渠道层不背内容理解）。
- S-02: e2e_deferred — automated command e2e_deferred; run_id=958559213b864daca2c8b7a154e1c2d6 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=958559213b864daca2c8b7a154e1c2d6 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=958559213b864daca2c8b7a154e1c2d6 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=958559213b864daca2c8b7a154e1c2d6 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] resumed (in-progress)
- [2026-10-02] completed (done)
## TASK-008: 产物写出工具

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`
- **Spec-Refs**:
- **Acceptance-Refs**: S-06

### Description

让 Agent 能把结果写为产物并拿到标识，与读取工具形成闭环。写出同样受大小上限与租户隔离约束；未配置交付路由时给出明确错误，不静默丢弃。

### Checklist

- [x] 新增 `write_artifact`：写产物并返回标识
- [x] 受大小上限与租户隔离约束；无交付路由时明确报错
- [x] [S-06][integration] 调用写产物工具后再读回（真实边界：工具 → artifact store → 读回）；断言内容一致（闭环）
- [x] 运行 `uv run pytest -q tests/test_skill_artifact_cache.py` 并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | 工具、artifact store | 写出后读回内容一致 | `tests/agent_runtime/test_attachment_tools.py::test_s06_write_then_read_back_closes_the_loop` | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | FAIL: `ImportError: cannot import name 'WRITE_ARTIFACT_TOOL'` | PASS: `9 passed` | `tests/agent_runtime/test_attachment_tools.py::test_s06_write_then_read_back_closes_the_loop`（写出 → 用**同一个读取工具**读回，断言内容逐字一致；并断言文件真的落到了 artifact store，而不是只写了 DB 行） | 工具 → 真实 artifact store（落盘 + 原子替换）→ 读回，全程不 mock | verified |

**本任务的其余覆盖（同文件）**：
- `test_write_artifact_without_delivery_route_errors_explicitly`：**没有交付路由必须显式报错** —— 写出来没人收，静默成功等于骗模型说"已经交付"（设计 FEAT-09 验收）
- `test_written_artifact_is_tenant_scoped`：写出的产物同样受租户隔离，别的租户读不到

**本次回归**：
- verifier `uv run pytest -q tests/test_skill_artifact_cache.py` → **4 passed**
- `tests/agent_core tests/agent_runtime` → **273 passed**（含本任务新增 3 条）
- `uv run mypy apps packages` → **Success: no issues found in 264 source files**

**实现补充**：`write_artifact` 落在 TASK-007 建立的 `AttachmentToolSet` 上（同一套租户/存储口径），新增构造参数 `run_id` / `conversation_id` / `has_delivery_route` —— 前两者决定产物行挂在哪（`run_id` 非空 ⇒ `ck_artifact_run_task_xor` 继续成立），后者是上面那道显式拒绝的依据。写出用「临时文件 + `os.replace`」原子替换，不留半成品。
- S-06: verified — automated command passed; run_id=743a771afe0f4967aaf7ce3bef6a7b72 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-009: 当前时间工具

- **Status**: done
- **Priority**: P1
- **Depends**: 无
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`
- **Spec-Refs**: harness-time#RULE-time-001
- **Acceptance-Refs**: B-08

### Description

给 Agent 一个可信时间基准，供定时与相对时间（"明天早上 9 点"）判断。时间必须带 **IANA 时区标识**，不得返回裸 UTC 字符串；不因该能力改变纯文本会话的请求体。

### Checklist

- [x] 新增时间能力，返回带 IANA 时区标识的时间
- [x] 时钟可注入（便于测试断言固定时刻）
- [x] [B-08][unit] 注入已知固定时刻（真实边界：注入固定时钟，不 mock 被测函数本身）；断言返回该时刻且**带 IANA 时区标识**（非裸 UTC 字符串）
- [x] 运行 verifier：`uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity`（`harness-time#RULE-time-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-08 | unit | 注入固定时钟 | 返回注入时刻且带 IANA 时区标识 | `tests/agent_runtime/test_time_tools.py::test_b08_current_time_returns_injected_instant_with_iana_zone` | uv run pytest -q tests/agent_runtime/test_time_tools.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-08 | FAIL: `ModuleNotFoundError: No module named 'muad_agent_runtime.application.time_tools'` | PASS: `3 passed` | `tests/agent_runtime/test_time_tools.py::test_b08_current_time_returns_injected_instant_with_iana_zone`（`2026-10-02T09:30:00+08:00 (Asia/Shanghai)`：按注入时刻真实换算 + 带 IANA 名 + 不出现 UTC 时刻）、`::test_b08_same_instant_renders_per_zone`（同一时刻在 Asia/Shanghai 与 UTC 下渲染不同 ⇒ 时区真参与换算）、`::test_b08_invalid_zone_is_rejected` | 注入固定时钟（**不 mock 被测函数**，换算与格式化走真实代码路径） | verified |

**本次回归**：
- `uv run pytest -q tests/frontend/test_datetime_contract.py` → **2 passed**；`uv run pytest -q tests -k schema_parity` → **35 passed**（`harness-time#RULE-time-001` verifier）
- `uv run pytest -q tests/agent_runtime` → **188 passed**（executor 装配点变更的回归）
- `uv run mypy apps packages` → **Success: no issues found in 261 source files**；`ruff` → **All checks passed**

**设计落点的补充**：设计 §3.4 API-09 只写"`current_time` 工具"，未指定时区来源。实现取**平台级默认** `SharedSettings.default_timezone`（默认 `Asia/Shanghai`）—— 因为调度时区是 per-schedule 必填项，而"现在几点"没有 per-run 来源；非法值在 `resolve_zone()` 显式 `ValueError`，与 `harness-time#RULE-time-001` 对调度时区的口径一致（不静默回退）。
- B-08: verified — automated command passed; run_id=ee1b545bde6646828c268dbf52df1b30 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-010: 端到端验收基线

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-006, TASK-007
- **Source**: `wecom-inbound-media.design.md#2.5 验收条件`, `#3.2.3 通道复用性`
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-02, S-05（**本任务为负责人**：三条 E2E 的归属已从 TASK-006/007 移来——它们当年写不出可执行用例，真正闭合就在这里）, S-07（TASK-004 交付，本任务只复跑）

### Description

本需求跨渠道回调、数据库、共享存储、模型请求体四个边界，必须有一条**端到端**信号把它们连起来，而不是各任务自证。本任务负责跑通并登记需求级验收基线：确认真实边界未被降级（不得 mock 业务 API）、E2E 场景确实覆盖跨边界路径，并留下可复现的命令。

### Checklist

- [x] 确认真实边界未被降级：E2E 场景中未 mock 业务 API / DB / 落盘 / 模型组装（三条用例的边界是真实 WS 探针 → 真实四进程 → 真实 HTTP 媒体源 → 真实共享 store → 真实 PG → **真实模型请求体**）
- [x] 端到端跑通「图片 → 落盘 → 上下文 → 模型请求体含图像块」与「文档 → 落盘 → 工具抽取 → 回答」两条主链
- [x] [S-01/S-02][E2E] 登记可单独执行的 `pytest` 命令与真实边界：`uv run pytest -q tests/acceptance/wecom_attachments`
- [x] 验证 S-07 的通道中性用例在**不含企微代码路径**下通过（`tests/gateway/test_inbound_attachment_flow.py`，TASK-004 已交付；本次复跑）
- [x] 运行 verifier：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（`harness-test#RULE-test-001`）；记录输出并填写 Acceptance Evidence

> **这两条 E2E 一跑就抓到一处 P0 缺陷（本任务的核心产出）**：图片**从来没有**进过模型请求体。
> 根因不在附件链路，而在消息组装：`RunExecutor._build_run_request` 写的是
> `messages = history if history else (当前消息,)`，而消息序列来自**会话事件回放**——本轮
> `USER_MESSAGE` 在 Run 建立时就已写入事件表，历史必然含它 ⇒ `history` 永不为空 ⇒ 那份带
> 图片内容块的当前消息**永远被丢掉**，模型只看到文本引用。修法两处：`executor` 把当前轮
> 显式放进序列（带内联内容时替换末尾那条本轮用户消息，纯文本轮次行为不变）；`run_service`
> 的续跑路径传本轮补充输入且不重放入站附件，避免把上一轮的图再内联一次。
>
> **探针（测试替身）同时修了两处会掩盖问题的行为**：① `tool_arguments` 里 `$last_artifact_id`
> 改为取**最近一条**附件引用（正着找会拿到上一轮那个，工具于是读错文件）；② "本轮是否已拿到
> 工具结果"改为只看**最后一条 user 消息之后**的消息（看整份 messages 时，历史里的 tool 消息
> 会让脚本工具永远不被调用）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 回调、PG/Redis、落盘、模型请求体 | 附件落盘可读回；模型请求体含图像内容块且 base64 与源字节一致；回答经真实链路回到渠道 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py::test_s01_image_is_persisted_and_sent_to_the_model_as_a_content_block` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |
| S-02 | E2E | 回调、PG、artifact、解析库 | 文档落盘；模型按**上下文里的附件 ID** 调真工具；工具结果含文档正文 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py::test_s02_document_is_extracted_by_the_real_tool_and_answered` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |
| S-05 | E2E | 上下文组装、模型 | 历史图片**不进**本轮上下文；模型重看后它作为新内容块被重发 | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py::test_s05_historical_image_is_resent_as_a_content_block_after_view_image` | uv run pytest -q tests/acceptance/wecom_attachments | e2e_deferred |
| S-07 | integration | 门控、契约、落盘 | 通道中性用例不含企微路径 | `tests/gateway/test_inbound_attachment_flow.py`（TASK-004 交付，此处复跑） | uv run pytest -q tests/gateway/test_inbound_attachment_flow.py tests/gateway/test_inbound_attachment_store.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL（**首跑即红，抓到 P0**）：`模型请求体里始终没有出现图像内容块`——跑到的请求体里图片只是文本引用。根因不在附件链路，而在消息组装：`RunExecutor._build_run_request` 的 `messages = history if history else (当前消息,)`，而消息序列来自**会话事件回放**，本轮 `USER_MESSAGE` 在 Run 建立时就已入表 ⇒ `history` 永不为空 ⇒ 带图像块的当前消息被丢掉 | PASS: `3 passed in 18.56s`（runner exit_code=0） | `tests/acceptance/wecom_attachments/test_inbound_image_e2e.py::test_s01_image_is_persisted_and_sent_to_the_model_as_a_content_block`（① 共享 store 上 `inbound/<msgid>/0` 的字节 == 发送的 PNG；② `runtime.artifact` 行的 checksum 与 `INBOUND_IMAGE` 与磁盘一致；③ 模型请求体里 `image_url` 的 base64 == 发送字节；④ 回答经真实链路回到渠道） | 真实 WS 探针 → 真实 Gateway/Runtime/Console/Worker 进程 → 真实 HTTP 媒体源（真实 AES 密文）→ 真实共享 store → 真实 PG → **真实模型请求体**（HTTP 探针逐次记录 body） | verified |
| S-02 | FAIL（首跑）：`call_arguments[0]["artifact_id"] == artifact_id` 不等——探针的 `$last_artifact_id` 取的是**第一个**附件引用，同一会话先跑过别的轮次时拿到上一轮那个，工具于是读错文件 | PASS: `3 passed` | `::test_s02_document_is_extracted_by_the_real_tool_and_answered`（① pdf 落盘；② 模型调用参数里的 id **等于**本轮 PG 里那一行的 id；③ 工具结果里的正文含文档里的 `DEVICE XL-900`（真实解析库抽出）；④ `runtime.tool_call_audit` 有 `read_attachment` 留痕） | 真实 pdf（手写最小合法文件）+ 真实 `pypdf` + 真实 PG 逐行回读 | verified |
| S-05 | FAIL（首跑）：`重看后重发的不是那张历史图片；观察到的图像块：[]`——探针把"本轮是否已拿到工具结果"判在**整份 messages** 上，历史轮次的 tool 消息让它直接返回 `final_text`，脚本里的 `view_image` **永远不被调用**（修复前同一文件单跑能过、整跑必红） | PASS: `3 passed` | `::test_s05_historical_image_is_resent_as_a_content_block_after_view_image`（① 第二轮**首个**请求里没有图像块，历史附件只留带 `artifact_id` 的文本引用（RULE-03）；② 重看之后的请求里，图像块的 base64 == 第一轮那张图**本身**） | 真实上下文组装（历史事件回放）+ 真实共享 store 读回字节 + 真实模型请求体 | verified |
| S-07 | 编码期已由 TASK-004 交付（本任务只复跑，未改断言） | PASS: `17 passed` | `tests/gateway/test_inbound_attachment_flow.py`（同一 `msgid` 喂两条适配器，结论逐项相同） | `FakeChannelAdapter`（内存字节、无 url/aes_key）与生产 `WeComAdapter`（真实 HTTP + 真实解密）跑**同一段编排** | verified |

**修复清单（本次改动全在这三处，均为"让两条主链真的通"所必需）**：
1. `executor._build_run_request`：当前轮**显式进消息序列**——带内联内容（图片块）时替换末尾那条本轮用户消息；纯文本/文档轮次行为逐字节不变（零回归面）。
2. `run_service._build_executor` / `_stream_run`：新增 `current_text` / `with_attachments`；**续跑路径**传本轮补充输入且**不重放**原消息的入站附件（否则会把上一轮的图再内联一次）。原先 `input_content` 用 `run.input_text` 组装，续跑时会拿旧文本。
3. `tests/e2e/openai_probe_app.py`（测试替身）：`$last_artifact_id` 取**最近一条**引用；"本轮是否已拿到工具结果"只看**最后一条 user 消息之后**的消息。这两处不修，E2E 会给出**假绿/假红**：前者让工具读错文件，后者让脚本工具静默不被调用。

**本次回归**：
- `uv run pytest -q tests/acceptance`（`harness-test#RULE-test-001` 第一条腿，全量真实栈）→ **272 passed, 1 failed in 811s**
- `uv run pytest -q tests/agent_runtime --ignore=test_runner_executor.py` → **195 passed**；`tests/agent_runtime/test_runner_executor.py` → **8 passed**；`tests/agent_core` → **70 passed**
- `tests/acceptance/im_gateway/test_memory_flow.py`（探针回归：同样依赖脚本化工具调用）→ **2 passed**；`tests/acceptance/wecom_attachments` → **3 passed**
- `uv run mypy apps packages` → **Success: no issues found in 267 source files**；`uv run ruff check .` → **All checks passed**

> **那 1 条失败的甄别（未改实现）**：`tests/acceptance/im_gateway/test_redis_degradation.py::test_e06_dedupe_recovers_after_redis_available`，报错是 `AssertionError: e2e-im-bot 的连接不可用，无法推送`——**推送时探针侧没有活连接**，是 WS 重连时序，不是去重逻辑的断言。该文件单跑 **2 passed in 42.74s**；本次改动不触及 WS/去重路径（改的是消息组装与探针脚本判据）。按项目口径「单跑通过、整跑偶发 ⇒ 先怀疑环境残留再改实现」判定为整跑偶发。

**两处残余（明确不声称绿）**：
1. **模型腿**：S-01/S-02 的"模型"是 HTTP 探针（脚本化）。**请求体这一跨边界事实**已验；"真实多模态供应商模型看图答得对"需要外部凭据，本仓 dev 环境没有（`.env` 只有基础设施，无模型密钥）⇒ 设计 `R-05` 的真机腿仍开放。
2. **`harness-test` verifier 的后两条腿**（`npm --prefix apps/console-platform/frontend run build` + `npm --prefix e2e test`）**本次未执行**：Done Gate 记为 heavy deferred（与全量 Pytest 同一口径），留待归档 `cf_validation`。

**Done Gate 裁决**：`pass`（`cf_task_workflow.py finish --task TASK-010`，rc=0）。deferred：16 个 verifier（需求级 `verify-e2e` 收口）+ 1 个 heavy validator（归档 `cf_validation` 收口）。

- S-01: e2e_deferred — automated command e2e_deferred; run_id=eda0676585c14ca0be12df83b92024c9 (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=eda0676585c14ca0be12df83b92024c9 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=eda0676585c14ca0be12df83b92024c9 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-011: 通道取值收口 + 通道中立性静态守卫

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: `wecom-inbound-media.design.md#3.2.3`, `#2.5.2 B-09`
- **Spec-Refs**: harness-worker#RULE-worker-001, harness-time#RULE-time-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-09

### Description

B-07 只钉住了**类型定义处**（`Literal["WECOM"]` 只在 `enums.py` 一处），**取值填充处**没收：字符串 `"WECOM"` 全仓仍有 10 处，其中 4 处在渠道层之外——console 的通道管理面 2 处（合理，它本身就是通道管理界面），**`agent-runtime/.../run_service.py:408` 与 `agent-worker/.../scheduler/client.py:53` 各 1 处（核心域）**。

那两处填的是 `ResolveDefinitionRequest.channel`——一个**必填、但全仓没有任何消费方读它**的字段（console `resolve_service.py` / `internal_runtime.py` / runtime `console_client.py` / `ports.py` 逐个 grep，零引用）⇒ 每个调用点只能凭空编一个通道名。**现在不咬人，但它是"接新通道时声称的通道与实际不符"的埋伏**：一旦有人开始读它（例如做按通道的授权），两处编造的值立刻就是错的。

本任务把取值收干净，并把"核心域零渠道字样 / 零取件形状"钉成**会红的静态守卫**。守卫与被它判红的代码**必须同一批落地**（先加守卫则任务自身为红），故合为一个任务。

### Checklist

- [x] `ResolveDefinitionRequest.channel` 改为 `ChannelName | None = None`：**有真值给真值，没有就显式省略**，不再编造
- [x] `run_service.py` 的两处 resolve 调用分别收口：**建 Run 那条**传 `request.channel.type`（运行请求里有真值，零额外 IO——核对后确认它**原本就是对的**）；**建会话那条**（`create_conversation`）调用点根本没有通道（入参只有 agent/用户，`CreateConversationRequest` 里也没有）⇒ 显式省略，不编造
- [x] worker 的 `scheduler/client.py` 省略该字段，并注明理由：**定时触发不经渠道**，交付通道属于 Schedule 的 `delivery_route`，不是 resolve 的输入（为填这个没人读的字段去热路径上多打一次库不划算）
- [x] console 侧确认无消费方依赖该字段非空（`resolve_service.py` 只用 agent/actor；`internal_runtime.py`、runtime `console_client.py`/`ports.py` 均不读）——改为可选后 `tests/console_platform` 136 passed
- [x] [B-09][unit] 静态守卫：核心域（`agent-runtime`/`agent-worker`）零渠道字样（`WECOM`/`aeskey`/`url_private`/`download_code`…）与取件形状；通道专有字样只允许出现在 `channels/` 适配器层与 console 通道管理面
- [x] 回归：既有文本路径与调度链路全绿（改动了 worker 的 resolve 入参与核心域的填入值）

> **清单第二条的落实与偏差（记录）**：原条目写"`run_service.py` 的 resolve 调用改传 `request.channel.type`"，那是按"冒烟点在建 Run 路径"写的。实际读代码后：建 Run 那条**早就是**真值，写死的是**建会话**那条，而它没有通道可传 ⇒ 按 B-09 ② 的"没有就显式省略"处理。方向不变（不再编造），只是落点与手段与清单原文不同。
>
> **两条断言的分工（写给后来者）**：① 与 ② 用的是**全文**扫描（连注释都不许提）——核心域零容忍；③ 用的是 **AST 代码扫描**（标识符 + 关键字参数名 + 非文档字符串字面量），因为别处的注释常需要点名"这个字段必须无处可放"（`AttachmentRef` 的文档串就逐个点名了 `aes_key`/`media_id`/`download_code`），把说明当泄漏会让守卫变成噪音。允许面写成 `ALLOWED_SURFACES` 常量，放宽时改清单、不改断言，多出来的那处在 review 里可见。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-09 | unit | 源码静态检查（不 mock） | 核心域零渠道字样与取件形状；`channel` 取值无编造 | `tests/architecture/test_channel_neutrality.py`（三条：核心域零字样 / 不传字面量 / 允许面之外零泄漏） | uv run pytest -q tests/architecture/test_channel_neutrality.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-09 | FAIL（**把写回的字面量放回去即可复现**）：在 `apps/agent-worker/.../scheduler/client.py` 把 `channel="WECOM"` 加回后，三条断言**同时**红，且各自指名文件与行号——①`{'apps/agent-worker/src/muad_agent_worker/scheduler/client.py': ['wecom']}`；②`['apps/agent-worker/src/muad_agent_worker/scheduler/client.py:53']`；③同 ①。这就是"接线前"的真实状态 | PASS: `3 passed`（runner exit_code=0） | `tests/architecture/test_channel_neutrality.py::test_b09_core_domain_carries_no_channel_vocabulary`（核心域全文零渠道字样）/ `::test_b09_core_domain_never_fabricates_a_channel_value`（AST 取 `ResolveDefinitionRequest(channel=<字面量>)`，`DINGTALK` 这类换名字也照样红）/ `::test_b09_channel_vocabulary_only_lives_on_the_allowed_surfaces`（全仓允许面之外零泄漏） | **真实源码扫描**：读盘全文匹配（①②）与 `ast.parse` 遍历（③），无 mock、无桩；允许面是 `ALLOWED_SURFACES` 常量，放宽必须改清单而非改断言 | verified |

**本次回归**：
- `harness-snapshot#RULE-snapshot-001` verifier：`test_snapshot_freeze.py` + `test_run_reaper.py` → **4 passed**；`tests/agent_runtime -k "executor or resolve"` → **20 passed**
- `harness-worker#RULE-worker-001` verifier：`tests/agent_worker` → **236 passed**；`tests/agent_runtime --ignore=test_runner_executor.py` → **195 passed**
- `harness-time#RULE-time-001` verifier：`tests/frontend/test_datetime_contract.py` → **2 passed**；`tests -k schema_parity` → **35 passed**
- `tests/architecture` → **13 passed**（含本任务新增 3 条）；`tests/test_contracts.py tests/console_channel tests/gateway` → **317 passed**；`tests/console_platform` → **136 passed**（`channel` 改可选后的契约消费方）
- `uv run mypy apps packages` → **Success: no issues found in 267 source files**；`uv run ruff check .` → **All checks passed**

> **偶发甄别（记录，未改实现）**：本次两处首跑失败都在**整跑/组合跑**里出现、**单跑通过、复跑全绿**——① `tests/agent_worker` 首跑的 `test_b124_admin_list_and_cancel_contract`（单跑 1 passed；整域复跑 236 passed）；② `tests/test_contracts.py tests/console_channel tests/gateway` 组合首跑的 `tests/gateway/test_bind_command.py::test_b110_bind_success_replies_and_persists_identity`（单跑 1 passed；整组合复跑 317 passed）。按项目口径先怀疑环境残留（本机 dev 服务在跑、与验收共用 PG/Redis）再怀疑实现，两者复跑均绿且与本次改动无交集（一是 admin 任务列表、二是绑定回执），故判定为整跑偶发。

**Done Gate 裁决**：`pass`（`cf_task_workflow.py finish --task TASK-011`，rc=0）。deferred：15 个 verifier（需求级 `verify-e2e` 收口）+ 1 个 heavy validator（归档 `cf_validation` 收口）。

 verified — automated command passed; run_id=8cda806567e24a5f9e242b4341b718df (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)

---
- [2026-10-02] started
- [2026-10-02] completed (done)
## TASK-012: 入站审计落点（表 + console 内部端点）

- **Status**: done
- **Priority**: P0
- **Depends**: —
- **Source**: `wecom-inbound-media.design.md#3.3 数据设计`, `#3.4 API-10`
- **Spec-Refs**: harness-api#RULE-api-001, harness-data#RULE-data-001, harness-time#RULE-time-001, harness-im#RULE-im-001
- **Acceptance-Refs**: E-08

### Description

设计原写"复用既有审计写入路径（`audit_writer`），不新增表"——**照做不了**：那个 writer 在 runtime 侧、写 `runtime.{tool_call,egress,model_invocation}_audit` 三张表，语义是工具/出站/模型调用；而入站接收/拒绝发生在网关，**网关不持库**（架构测试扫 `sqlalchemy`）；console 的 `internal_channel` 只有 `resolve`/`bind`/`bots`/`skills`，没有审计入口。权威写入方只能是 console。

本任务先把落点建起来（表 + 内部端点），TASK-004 才有地方写"接收/拒绝/失败各一条"。**它是 TASK-004 的前置**。

### Checklist

- [x] 契约新增 `InboundAuditRequest`（渠道中立；字段**全部枚举化/结构化** ⇒ 结构上无法承载 `aes_key` 或媒体 URL，RULE-06 的审计腿靠类型保证而非运行时脱敏）
- [x] `control.im_inbound_audit` 表：`StandardColumnsMixin`（`id/is_deleted/create_time/update_time`）、时间 `timestamptz`、索引 `(tenant_id, create_time DESC)` 与 `(external_message_id)`；**关键查询字段不藏 JSON，且不设自由 JSON 列**
- [x] alembic 迁移：单链，接在当前 head 之后；`uv run alembic upgrade head` 可升可降
- [x] `POST /internal/channel/audit`：`InternalServiceDep`（服务令牌门控）+ `HeaderTenantId`（调用方显式声明租户），响应走 `ok(request.app.state.message_catalog, ...)` 封套
- [x] 服务层 `InboundAuditService.record`：只写白名单字段；**幂等键 `(tenant_id, channel, external_message_id, outcome)`，同键重投不产生第二行**（企微会重投，与 E-07 同源）
- [x] [E-08][integration] 三种 `outcome`（RECEIVED/REJECTED/FAILED）各落一行、字段齐全（含 `external_message_id`/附件数/通过数/原因码）；同键重投幂等；**审计行内不含任何取件凭据**（真实边界：内部端点 → 真实 PG 审计表）
- [x] 运行 verifier：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`（`harness-api#RULE-api-001`）；记录输出并填写 Acceptance Evidence
- [x] 运行 verifier：`uv run pytest -q tests -k schema_parity`（`harness-data#RULE-data-001`）；记录输出并填写 Acceptance Evidence
- [x] 运行 verifier：`uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity`（`harness-time#RULE-time-001`）；记录输出并填写 Acceptance Evidence
- [x] 运行 verifier：`uv run pytest -q tests/console_channel tests/gateway`（`harness-im#RULE-im-001`：新增契约类型与既有通道契约同族，局部承接）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-08 | integration | 内部端点 → 真实 PG 审计表 | 三种 outcome 各落一行且字段齐全；同键重投幂等；行内无取件凭据 | `tests/console_channel/test_inbound_audit.py` | `uv run pytest -q tests/console_channel/test_inbound_audit.py` | verified |


### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-08 | 把源码改动移开（`git stash push -- apps packages migrations`）后本文件整体 collection error：`ImportError: cannot import name 'InboundAudit' from ...infrastructure.models.control` | PASS: `4 passed` | `tests/console_channel/test_inbound_audit.py`：`test_three_outcomes_each_land_one_row_with_named_fields`（三种结局各落一行，拒绝原因/附件计数/通过数/字节数都能直接查出）、`test_redelivery_of_the_same_outcome_does_not_write_a_second_row`（重投返回**同一个 id** 且只有一行）、`test_the_same_message_with_a_different_outcome_keeps_both_rows`（幂等键粒度=消息×结局）、`test_audit_storage_has_no_place_for_credentials`（结构 + 行为两层） | 内部端点 → 真实 PostgreSQL：迁移 **0015** 真实建表（`control.im_inbound_audit`，15 列 + 3 索引），测试经真实 ASGI 客户端与真实 DB 断言，**无 mock** | verified |

**本任务的其余覆盖（同文件）**：见上表第三列——四条各自钉死一件事，"幂等键粒度"那条尤其重要：把 `outcome` 放进键意味着"接收过之后又失败"是**两条事实**，不会被去重吃掉。

**实现补充**：
- **`ON CONFLICT DO NOTHING` + 回查**，而不是"先查再插"——并发重投时后者会两个都查不到、插两条再被唯一索引抛错；幂等的最终保证是那条 **partial unique 索引**（`... WHERE is_deleted = false`）。
- **契约 `extra="forbid"`**（`ContractModel` 既有配置）让"夹带取件凭据"的请求被**指名 422 拒绝**（`{"aes_key","url"}`），比"忽略未知字段"更强：既进不了库，也不会被谁日后加个 catch-all 字段悄悄收下。
- 表**刻意不设 JSON 列**：`RULE-secret-001` 的审计腿因此是**结构保证**而不是运行时脱敏。

**本次回归**：
- `uv run pytest -q tests/console_channel tests/gateway` → **290 passed**（`harness-im#RULE-im-001` verifier）
- `uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py` → **18 passed**（`harness-api#RULE-api-001` verifier）
- `uv run pytest -q tests -k schema_parity` → **35 passed**（`harness-data#RULE-data-001` / `harness-time#RULE-time-001` verifier）
- `uv run pytest -q tests/frontend/test_datetime_contract.py` → **2 passed**（`harness-time` verifier）
- `uv run mypy apps packages` → **Success: no issues found in 266 source files**
- `uv run ruff check .` → **All checks passed**
- E-08: verified — automated command passed; run_id=c98aa554df9046ce8205a7c8f241ccf7 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=9f7b4a5144ba4d0fa496cf18e8c9eab7 (confirmed_by: runner)

### Log
- [2026-10-02] created (draft)
- [2026-10-02] started
- [2026-10-02] completed (done)
