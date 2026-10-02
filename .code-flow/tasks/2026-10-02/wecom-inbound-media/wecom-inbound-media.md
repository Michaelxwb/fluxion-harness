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
| S-01 | design#2.5.2 | E2E | 真实回调桩 → 真实 PG/Redis → 真实落盘 → 真实模型请求体 | TASK-006 | planned | - | . | 60 |  |
| S-02 | design#2.5.2 | E2E | 真实回调桩 → 真实 PG → 真实 artifact → 工具真实抽取 | TASK-007 | planned | - | . | 60 |  |
| S-03 | design#2.5.2 | integration | 模型 provider 请求体（不 mock 组装层） | TASK-005 | planned | - | . | 60 |  |
| S-04 | design#2.5.2 | E2E | 回调 → 落盘 → 契约 → 工具 | TASK-004 | planned | - | . | 60 |  |
| S-05 | design#2.5.2 | E2E | 真实上下文组装 + 真实模型 | TASK-007 | planned | - | . | 60 |  |
| S-06 | design#2.5.2 | integration | 工具 → artifact store → 读回 | TASK-008 | planned | - | . | 60 |  |
| S-07 | design#2.5.2 | integration | FakeChannelAdapter → 门控 → 契约 → 落盘（不含企微路径） | TASK-004 | planned | - | . | 60 |  |
| E-01 | design#2.5.2 | E2E | 回调 → 反馈投递 → 审计表 | TASK-004 | planned | - | . | 60 |  |
| E-02 | design#2.5.2 | E2E | 同上 | TASK-004 | planned | - | . | 60 |  |
| E-03 | design#2.5.2 | E2E | 同上 | TASK-004 | planned | - | . | 60 |  |
| E-04 | design#2.5.2 | integration | 解密路径 + 日志/审计输出 | TASK-002 | planned | - | . | 60 |  |
| E-05 | design#2.5.2 | integration | DB 查询 + 工具越权校验 | TASK-007 | planned | - | . | 60 |  |
| E-06 | design#2.5.2 | integration | 真实解析库 | TASK-007 | planned | - | . | 60 |  |
| E-07 | design#2.5.2 | integration | 幂等键 + 落盘 | TASK-004 | planned | - | . | 60 |  |
| B-01 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | planned | - | . | 60 |  |
| B-02 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | planned | - | . | 60 |  |
| B-03 | design#2.5.2 | unit | 门控纯函数 | TASK-003 | planned | - | . | 60 |  |
| B-04 | design#2.5.2 | unit | 路径解析函数 | TASK-003 | planned | - | . | 60 |  |
| B-05 | design#2.5.2 | integration | 契约序列化 | TASK-001 | verified | uv run pytest -q tests/test_attachment_contract.py | . | 60 |  |
| B-06 | design#2.5.2 | integration | provider 组装 | TASK-005 | planned | - | . | 60 |  |
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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `wecom-inbound-media.design.md#2.3.2 字段约束`, `#3.4 接口设计`
- **Spec-Refs**:
- **Acceptance-Refs**: B-01, B-02, B-03, B-04

### Description

门控判定为**纯函数、无 IO**，便于边界值单测。P0 以模块常量生效（20 MiB / 5 个 / 白名单），P1 再配置化。文件名不得参与路径拼接。

### Checklist

- [ ] 实现门控纯函数：输入候选附件序列，输出 `GateDecision`（通过/拒绝 + 原因码）
- [ ] P0 常量：单文件 20 MiB、单消息 5 个、类型白名单（图片 png/jpeg/gif/webp；文档 pdf/docx/txt/md/xlsx/pptx）
- [ ] 路径安全：产物键由系统生成，原始文件名仅入元信息
- [ ] [B-01][unit] 单文件大小 == 20 MiB → 接收（真实边界：门控纯函数）
- [ ] [B-02][unit] 单文件大小 == 20 MiB + 1 B → 拒绝且原因码为"超大小上限"
- [ ] [B-03][unit] 附件数 == 5 → 接收；== 6 → 拒绝
- [ ] [B-04][unit] 文件名 `../../etc/passwd` 与空串 → 落盘路径不含用户输入，原文件名仅入元信息
- [ ] 运行门控单测并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | 门控纯函数 | 恰好等于上限 → 接收 | planned | planned | planned |
| B-02 | unit | 门控纯函数 | 超 1 字节 → 拒绝 + 原因码 | planned | planned | planned |
| B-03 | unit | 门控纯函数 | 5 接收 / 6 拒绝 | planned | planned | planned |
| B-04 | unit | 路径解析函数 | 路径不含用户输入 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`, `#3.1 方案选型`
- **Spec-Refs**: harness-model#RULE-model-001
- **Acceptance-Refs**: S-03, B-06

### Description

把模型消息的内容类型从"只能是字符串"放宽为可承载多模态内容块，图片以图像块进入上下文。**必须走唯一的 OPENAI 兼容协议**（不引入第二协议）。兼容护栏：纯文本路径的请求体**逐字节不变**。

### Checklist

- [ ] 定义 `ImagePart`；`ModelMessage.content` 放宽为 `str | tuple[str | ImagePart, ...]`
- [ ] provider 组装：`str` → 原样输出（字段集不变）；元组 → `content` 数组
- [ ] 不改 `model_id` 显式绑定口径、不引入第二协议
- [ ] [S-03][integration] 纯文本会话（真实边界：模型 provider 请求体，不 mock 组装层）；断言请求体与改造前基线**逐字节相同**
- [ ] [B-06][integration] 内容形态为纯字符串时（真实边界：provider 组装）；断言输出 `content` 为**字符串而非数组**，且字段集不变
- [ ] 运行 verifier：`uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled`（`harness-model#RULE-model-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | provider 请求体 | 纯文本请求体逐字节不变 | planned | planned | planned |
| B-06 | integration | provider 组装 | 纯文本 content 为字符串、字段集不变 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

## TASK-006: Runtime 附件落库 + 上下文组装

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-005
- **Source**: `wecom-inbound-media.design.md#3.2.1`, `#3.3 数据设计`
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001, harness-data#RULE-data-001
- **Acceptance-Refs**: S-01, S-05

### Description

Runtime 在 Run 建立后把消息里的 `AttachmentRef` 落成 `runtime.artifact` 行（`run_id` 非空 ⇒ 既有 `ck_artifact_run_task_xor` 继续成立，**不改 schema**），并按 AD-3-B 组装上下文：**当前消息**的图片进内容块，**历史轮次**附件只保留文本引用。附件属消息载荷，不进 Snapshot 冻结范围。

### Checklist

- [ ] Run 建立时把 `AttachmentRef` 落成 `artifact` 行（`artifact_type=INBOUND_*`，`storage_key` 相对键，`metadata_json` 存 filename/source/external_message_id）
- [ ] 组装上下文：当前消息图片 → `ImagePart` 内容块；历史附件 → 文本引用
- [ ] 附件不进入 Snapshot 冻结范围（仅 Agent/Model/Skill/MCP 版本按既有口径冻结）
- [ ] [S-01][E2E] 发送含已知文字的图片并提问（真实边界：真实回调桩 → 真实 PG/Redis → 真实落盘 → 真实模型请求体，不 mock）；断言①附件落盘且可读回②发给模型的请求体**含图像内容块**③模型回答体现图中内容
- [ ] [S-05][E2E] 前一轮发过图片后，模型重看再提问（真实边界：真实上下文组装 + 真实模型）；断言历史图片作为新的内容块被重发
- [ ] 运行 verifier：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`（`harness-snapshot#RULE-snapshot-001`）；记录输出
- [ ] 运行 verifier：`uv run pytest -q tests -k schema_parity`（`harness-data#RULE-data-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 回调、PG/Redis、落盘、模型请求体 | 落盘可读回；请求体含图像块；回答体现图中内容 | planned | planned | planned |
| S-05 | E2E | 上下文组装、模型 | 历史图片作为新内容块重发 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

## TASK-007: 附件读取工具（文档抽取）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`, `#3.1 方案选型`
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-02, S-05, E-05, E-06

### Description

新增**类型无关**的附件读取工具：`read_attachment` 按产物标识取内容并在工具内抽取文档文本（pdf/docx/txt/md/xlsx/pptx）；`view_image` 触发运行时追加一条携带图像内容块的 user 消息，让模型可回看历史图片（OpenAI 协议的 tool 角色不能携带图像）。工具入口必须做**租户隔离与越权校验**。解析依赖落在 agent 侧，**不进网关**（AD-4-B）。

### Checklist

- [ ] 新增 `read_attachment`：文档类返回抽取文本，纯文本类返回内容，图片类提示改用 `view_image`
- [ ] 新增 `view_image`：仅限 `kind=IMAGE`，调用后追加携带图像内容块的 user 消息
- [ ] 引入解析依赖（`pypdf`/`python-docx`/`openpyxl`/`python-pptx`）并按需导入
- [ ] 工具入口按 `tenant_id` 过滤 + 越权校验；受大小上限保护
- [ ] [S-02][E2E] 发送内容已知的 pdf 并提问其中事实（真实边界：真实回调桩 → 真实 PG → 真实 artifact → 工具**真实抽取**，不 mock 解析库）；断言抽取文本与文档实际内容一致且回答正确
- [ ] [S-05][E2E] 模型调用重看后再提问（真实边界：真实上下文组装 + 真实模型）；断言历史图片作为新内容块被重发
- [ ] [E-05][integration] 以租户 B 身份读取租户 A 的 `artifact_id`（真实边界：DB 查询 + 工具越权校验）；断言拒绝且不泄露存在性细节
- [ ] [E-06][integration] 加密/损坏文档（真实边界：真实解析库）；断言返回明确错误而非乱码或空内容
- [ ] 运行 verifier：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity`（`harness-auth#RULE-auth-001`）；记录输出并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | 回调、PG、artifact、解析库 | 抽取内容与文档一致；回答正确 | planned | planned | planned |
| S-05 | E2E | 上下文组装、模型 | 历史图片重发为内容块 | planned | planned | planned |
| E-05 | integration | DB、越权校验 | 跨租户读取被拒 | planned | planned | planned |
| E-06 | integration | 真实解析库 | 明确错误而非乱码 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

## TASK-008: 产物写出工具

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-007
- **Source**: `wecom-inbound-media.design.md#3.4 接口设计`
- **Spec-Refs**:
- **Acceptance-Refs**: S-06

### Description

让 Agent 能把结果写为产物并拿到标识，与读取工具形成闭环。写出同样受大小上限与租户隔离约束；未配置交付路由时给出明确错误，不静默丢弃。

### Checklist

- [ ] 新增 `write_artifact`：写产物并返回标识
- [ ] 受大小上限与租户隔离约束；无交付路由时明确报错
- [ ] [S-06][integration] 调用写产物工具后再读回（真实边界：工具 → artifact store → 读回）；断言内容一致（闭环）
- [ ] 运行 `uv run pytest -q tests/test_skill_artifact_cache.py` 并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | 工具、artifact store | 写出后读回内容一致 | planned | planned | planned |

### Acceptance Evidence

### Log
- [2026-10-02] created (draft)

---

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
