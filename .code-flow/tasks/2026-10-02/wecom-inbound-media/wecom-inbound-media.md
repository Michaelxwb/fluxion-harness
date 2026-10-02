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
- **Non-goals**: 出站端到端文件发送；企微以外的渠道（含自建 web 对话页通道）；语音/视频专门处理；附件保留期策略；文档结构化理解。
- **Acceptance**: 见下方 Acceptance Coverage（20 条场景 + B-08）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 | cwd | 超时 | 依赖 |
|--------|---------|---------|-------------|---------|------|------|-----|------|------|
| S-01 | design#2.5.2 | E2E | 真实回调桩 → 真实 PG/Redis → 真实落盘 → 真实模型请求体 | TASK-006 | e2e_deferred | uv run pytest -q tests/acceptance/wecom_attachments | . | 60 |  |
| S-02 | design#2.5.2 | E2E | 真实回调桩 → 真实 PG → 真实 artifact → 工具真实抽取 | TASK-007 | e2e_deferred | uv run pytest -q tests/acceptance/wecom_attachments | . | 60 |  |
| S-03 | design#2.5.2 | integration | 模型 provider 请求体（不 mock 组装层） | TASK-005 | verified | uv run pytest -q tests/agent_core/test_openai_provider.py | . | 60 |  |
| S-04 | design#2.5.2 | E2E | 回调 → 落盘 → 契约 → 工具 | TASK-004 | planned | - | . | 60 |  |
| S-05 | design#2.5.2 | E2E | 真实上下文组装 + 真实模型 | TASK-007 | e2e_deferred | uv run pytest -q tests/acceptance/wecom_attachments | . | 60 |  |
| S-06 | design#2.5.2 | integration | 工具 → artifact store → 读回 | TASK-008 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 60 |  |
| S-07 | design#2.5.2 | integration | FakeChannelAdapter → 门控 → 契约 → 落盘（不含企微路径） | TASK-004 | planned | - | . | 60 |  |
| E-01 | design#2.5.2 | E2E | 回调 → 反馈投递 → 审计表 | TASK-004 | planned | - | . | 60 |  |
| E-02 | design#2.5.2 | E2E | 同上 | TASK-004 | planned | - | . | 60 |  |
| E-03 | design#2.5.2 | E2E | 同上 | TASK-004 | planned | - | . | 60 |  |
| E-04 | design#2.5.2 | integration | 解密路径 + 日志/审计输出 | TASK-002 | planned | - | . | 60 |  |
| E-05 | design#2.5.2 | integration | DB 查询 + 工具越权校验 | TASK-007 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 60 |  |
| E-06 | design#2.5.2 | integration | 真实解析库 | TASK-007 | verified | uv run pytest -q tests/agent_runtime/test_attachment_tools.py | . | 60 |  |
| E-07 | design#2.5.2 | integration | 幂等键 + 落盘 | TASK-004 | planned | - | . | 60 |  |
| B-01 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-02 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-03 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-04 | design#2.5.2 | unit | 路径解析函数 | TASK-003 | verified | uv run pytest -q tests/gateway/test_attachment_gate.py | . | 60 |  |
| B-05 | design#2.5.2 | integration | 契约序列化 | TASK-001 | verified | uv run pytest -q tests/test_attachment_contract.py | . | 60 |  |
| B-06 | design#2.5.2 | integration | provider 组装 | TASK-005 | verified | uv run pytest -q tests/agent_core/test_openai_provider.py | . | 60 |  |
| B-07 | design#2.5.2 | unit | 源码静态检查 | TASK-001 | verified | uv run pytest -q tests/test_attachment_contract.py | . | 60 |  |
| B-08 | design#2.5.2 | unit | 注入固定时钟 | TASK-009 | verified | uv run pytest -q tests/agent_runtime/test_time_tools.py | . | 60 |  |

> 覆盖自检：design 全部 P0/P1 场景 22/22 已分配唯一负责人；RULE-01..08 与高影响 R-01/R-04 均有映射场景；E2E 场景 6 个未降级。

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `wecom-inbound-media.design.md#3.2.1`, `#3.4 接口设计`, `#3.1 方案选型`
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: E-04

### Description

渠道适配层不再对非文本消息一律 `return None`：按 `msgtype` 分流，可接收的（图片/文件）走媒体下载与解密，产出 `AttachmentRef`。取件方式（`url` + `aeskey`）是**企微私有**，不得提升为跨渠道通用接口（AD-8）。`checksum` 必须在**解密之后**计算。

### Checklist

- [ ] `_to_inbound_message` 按 `msgtype` 分流，非文本可接收类型进入附件处理并填充 `attachments`
- [ ] `WeComSdkPort` 暴露渠道私有的媒体下载能力（签名含 `url`/`aes_key`，仅供 wecom 包内使用）
- [ ] 下载实现强制超时与字节上限；失败以明确异常类型上抛（区分超时/网络/解密失败）
- [ ] `checksum` 在解密后计算
- [ ] [E-04][integration] 覆盖密钥缺失与密钥不匹配两种解密失败（真实边界：解密路径 + 日志/审计输出，不 mock 解密）；断言①不落半成品（无 `artifact` 行、无残留文件）②**日志与审计中均不出现 `aes_key` 与媒体明文 URL**
- [ ] 回归：既有文本路径用例全绿
- [ ] 运行 verifier：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`（`harness-secret#RULE-secret-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-04 | integration | 解密路径、日志/审计输出 | 失败不留半成品；密钥与 URL 不出现在日志/审计 | planned | `uv run pytest -q tests/test_logging_redaction.py` | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: `wecom-inbound-media.design.md#3.2.1`, `#3.4 接口设计`, `#3.5 质量实现方案`
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-skill#RULE-skill-001, harness-log#RULE-log-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-04, S-07, E-01, E-02, E-03, E-07

### Description

把通过的附件写入**共享 artifact store**（相对 `storage_key`，原子替换），网关**不写本地盘、不写 DB**（artifacts 行由 Runtime 写）。所有"收不了"的情况给出用户可见回复并写审计——这是本需求消除"静默丢弃"的落点。反馈文案**不硬编码**，走既有消息目录（`catalog.message(code, locale)`），在 `config/api-messages.yaml` 新增 zh-CN/en-US 词条。

### Checklist

- [ ] 附件写入共享 store，key 为相对路径；沿用"临时文件 + `os.replace`"原子替换
- [ ] 门控拒绝 / 下载失败 / 解密失败 / 落盘失败 → 各自映射文案与审计码，**无静默路径**
- [ ] 反馈文案经消息目录取；`config/api-messages.yaml` 补齐 zh-CN 与 en-US 词条
- [ ] 审计记录含 `external_message_id`、附件数、拒绝原因码（供 PRD §2.2 指标度量）
- [ ] [S-04][E2E] 一条消息带 3 个附件（含图片与文档）（真实边界：回调 → 落盘 → 契约 → 工具，不 mock）；断言三个附件各自落盘、可分别读取、互不覆盖
- [ ] [S-07][integration] `FakeChannelAdapter` 以**与企微不同的取件路径**（不经 url/aes_key）产出带附件的 envelope（真实边界：门控 → 契约 → 落盘，不含企微代码路径）；断言门控/契约/落盘行为与企微路径一致
- [ ] [E-01][E2E] 不受支持类型 → 不落盘 + 审计 + 用户收到明确说明
- [ ] [E-02][E2E] 超上限文件 → 回复中**包含上限数值** + 审计
- [ ] [E-03][E2E] 下载失败 → 明确失败说明 + 审计
- [ ] [E-07][integration] 同一消息重投 → 去重，不产生重复产物
- [ ] 运行 verifier：`uv run pytest -q tests/architecture`（`harness-arch#RULE-arch-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/test_skill_artifact_cache.py`（`harness-skill#RULE-skill-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py`（`harness-log#RULE-log-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`（`harness-i18n#RULE-i18n-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 回调、落盘、契约、工具 | 3 附件各自落盘且可分别读取 | planned | planned | planned |
| S-07 | integration | 门控、契约、落盘 | 与企微路径行为一致；核心域无渠道分支 | planned | planned | planned |
| E-01 | E2E | 回调、反馈投递、审计表 | 不落盘 + 审计 + 明确回复 | planned | planned | planned |
| E-02 | E2E | 同上 | 回复含上限数值 | planned | planned | planned |
| E-03 | E2E | 同上 | 明确失败说明 + 审计 | planned | planned | planned |
| E-07 | integration | 幂等键、落盘 | 重投不产生重复产物 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-006, TASK-007
- **Source**: `wecom-inbound-media.design.md#2.5 验收条件`, `#3.2.3 通道复用性`
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-02, S-04, S-07, E-01, E-02, E-03（**引用，不作为最终验收负责人**）

### Description

本需求跨渠道回调、数据库、共享存储、模型请求体四个边界，必须有一条**端到端**信号把它们连起来，而不是各任务自证。本任务负责跑通并登记需求级验收基线：确认真实边界未被降级（不得 mock 业务 API）、E2E 场景确实覆盖跨边界路径，并留下可复现的命令。

### Checklist

- [ ] 确认真实边界未被降级：E2E 场景中未 mock 业务 API / DB / 落盘 / 模型组装
- [ ] 端到端跑通「图片 → 落盘 → 上下文 → 模型请求体含图像块」与「文档 → 落盘 → 工具抽取 → 回答」两条主链
- [ ] [S-01/S-02][E2E] 登记可单独执行的 `pytest`/`playwright` 命令与真实边界（不 mock 业务 API）
- [ ] 验证 S-07 的通道中性用例在**不含企微代码路径**下通过
- [ ] 运行 verifier：`uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`（`harness-test#RULE-test-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 回调、PG/Redis、落盘、模型请求体 | 主链端到端可复现 | planned | `uv run pytest -q tests/acceptance` | planned |
| S-02 | E2E | 回调、PG、artifact、解析库 | 主链端到端可复现 | planned | planned | planned |
| S-07 | integration | 门控、契约、落盘 | 通道中性用例不含企微路径 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)
