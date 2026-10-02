# 设计简报: 企微入站附件接收与 Agent 附件工具面

> **文档编号**: DESIGN-2026-10-02-01
> **文档版本**: v1.0
> **创建日期**: 2026-10-02
> **状态**: 草稿

**评审边界说明**:
- **需求评审**: 第 2 章（需求分析）→ 通过后锁定为需求基线 v1.0
- **设计评审**: 第 3-4 章（技术设计 + 部署运维）→ 通过后锁定设计基线 v1.x
- **交接契约**: 2.5 验收条件 — 需求定义 What，设计实现 How

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | | 需求定义、业务验收 |
| 开发负责人 | | 技术方案确认 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v1.0 | 2026-10-02 | Claude | 初稿。由 PRD `wecom-inbound-media.prd.md` v0.2 派生；P0/P1 一份需求、P0 先交付 |

**模块信息**

| 项目 | 内容 |
|---|---|
| 模块 | IM Gateway 入站附件 + Agent 附件工具面 |
| 域 | **纯后端**（本期不含任何前端改动）——注意：这只对**本需求范围**成立；自建 web 对话页通道会打破该前提，见 §3.2.3 |
| Owner | muad-im-gateway（入站侧）/ muad-agent-runtime（工具与上下文侧） |
| 数据 Owner | 复用 runtime Owner 的 `artifact` 表；**本模块不新增表** |
| 前置模块 | 08-runtime-execution, 10-im-gateway |
| 建议代码位置 | `apps/im-gateway/src/muad_im_gateway/`、`apps/agent-runtime/src/muad_agent_runtime/`、`packages/agent-core/src/muad_agent_core/model/`、`packages/contracts/src/muad_contracts/` |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 企微入站附件接收与 Agent 附件工具面 |
| 模块 ID | MOD-IM-ATTACH |
| 需求类型 | 中大型功能开发（跨系统集成） |
| 业务背景 | 企微用户发图片/文件后机器人**完全无响应**：入站非文本消息在适配层被静默丢弃；即使收下来，Agent 侧也没有任何消费附件的工具（唯一的 `read_skill_resource` 被 skill 包边界锁死）。 |
| 核心目标 | 让企微入站的图片与文件被真实接收、落盘、并在对话中**被真正用起来**（图片可被模型看到、文档内容可被读取）；任何"收不了"的情况都给出明确反馈与审计，不再静默丢弃。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|---|---|
| 目标用户/调用方 | 企微中与 Agent 对话的业务用户；配置 Agent 的运营者 |
| 当前问题 | 非文本消息 100% 静默丢弃（无回复/无日志/无审计）；Agent 无附件消费通道 |
| 业务影响 | 用户无法用截图/文档表达诉求；无响应被误判为服务故障；截图信息只能用文字复述，费时且失真 |
| 预期价值 | 截图/文档可直接使用；消除"机器人坏了"的误判；沉淀**与入口无关**的附件底座与工具面（企微、Console 上传、skill 产出共用） |

**用户故事（继承 PRD §3.2）**

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 发出的图片/文件能被机器人真实接收并保存，以便用截图和文档直接表达诉求 | P0 |
| US-02 | 发过去的图片能被 Agent **真正看到**，以便就问图里的内容得到回答 | P0 |
| US-03 | 发过去的文档能被 Agent **读出内容**并据此作答 | P0 |
| US-04 | 内容超限/类型不支持/接收失败时立刻收到明确说明 | P0 |
| US-05 | Agent 能把处理结果整理成文件交回 | P1 |
| US-06 | Agent 知道当前日期时间，"明天早上 9 点"算得对 | P1 |
| US-07 | 每一次接收/拒绝/失败都有审计可查 | P1 |

### 2.3 功能方案

#### 2.3.1 功能清单

| 功能ID | 功能名称 | 优先级 | 来源 | 设计落点 |
|--------|---------|--------|------|---------|
| FEAT-01 | 非文本入站消息识别与承接 | P0 | US-01 | §3.2.2 / §3.4.2 API-02 |
| FEAT-02 | 媒体下载与解密 | P0 | US-01 | §3.4.2 API-03 |
| FEAT-03 | 附件落 artifact | P0 | US-01/02/03 | §3.3 / §3.4.2 API-04 |
| FEAT-04 | 契约承载附件 | P0 | US-02/03 | §3.4.2 API-01 |
| FEAT-05 | 图片直发模型（多模态） | P0 | US-02 | §3.4.2 API-05 |
| FEAT-06 | 附件读取工具（文档抽取） | P0 | US-02/03 | §3.4.2 API-06 |
| FEAT-07 | 接收反馈与审计 | P0 | US-04/07 | §3.2.2 / §3.5.4 |
| FEAT-08 | 类型与大小门控 | P1 | US-04 | §3.4.2 API-07 |
| FEAT-09 | 产物写出工具 | P1 | US-05 | §3.4.2 API-08 |
| FEAT-10 | 当前时间可用 | P1 | US-06 | §3.4.2 API-09 |

#### 2.3.2 字段约束 [按需]

**AttachmentRef（契约新增，**同时**用于 `ChannelEnvelope.attachments` 与 `MessageInput.attachments`）**

> **一处定义、两处使用**：这是本设计保证"换通道能复用"的关键——渠道适配器产出的附件引用与 Runtime 消费的附件引用**是同一个类型**，中间层不必做转换。
> **类型里没有 `artifact_id`**：AD-1-B 下 artifact 行由 Runtime 在 Run 建立后才写，**渠道侧此刻拿不到任何 DB 标识**；契约若带 `artifact_id` 就是在撒谎。`artifact_id` 由 Runtime 落库后产生，仅用于工具面寻址。

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `storage_key` | string | 是 | 附件字节在共享 artifact store 中的**相对键**（`harness-skill#RULE-skill-001`：DB 只存相对 key）；由渠道适配器落盘后生成，渠道无关 |
| `kind` | `IMAGE` \| `DOCUMENT` \| `OTHER` | 是 | 由 media_type 派生，决定注入形态（图片→内容块；文档→工具抽取） |
| `media_type` | string | 是 | IANA media type，如 `image/png` |
| `size` | int | 是 | 字节数，> 0 |
| `filename` | string \| null | 否 | 用户原始文件名，仅作元信息，**不得用于拼路径** |
| `checksum` | string | 是 | 内容 SHA256，用于校验与幂等 |
| `source_channel` | `WECOM` | 是 | 来源渠道，用于审计与后续溯源（取值集合见 §3.2.3 的通道枚举口径） |

**门控默认值（P0 常量，P1 配置化）**

| 项 | 默认值 | 依据 |
|----|--------|------|
| 单文件大小上限 | 20 MiB | 覆盖常见截图与办公文档；避免单条消息拖垮下载与上下文 |
| 单消息附件数上限 | 5 | 与"只内联当前消息图片"的成本口径匹配 |
| 允许类型 | 图片：png/jpeg/gif/webp；文档：pdf/docx/txt/md/xlsx/pptx | 与 FEAT-06 抽取能力对齐 |

### 2.4 范围与边界

| 类别 | 内容 |
|------|------|
| **In Scope** | ① 企微入站图片/文件的识别与承接；② 媒体下载与解密；③ 附件落 artifact（租户隔离）；④ 契约承载附件；⑤ 图片作为多模态内容块直发模型；⑥ 类型无关的附件读取工具（pdf/docx/txt/xlsx/pptx 文本抽取）；⑦ 拒绝/失败的明确反馈与全程审计；⑧ 类型与大小门控；⑨ 产物写出工具（P1）；⑩ 当前时间可用（P1） |
| **Out of Scope** | ① 出站方向的端到端文件发送（写产物只交付引用）；② 企微以外的 IM 渠道，**含自建 web 对话页通道（带附件上传）**——其复用边界与"不复用的四块"见 §3.2.3，本期不实现；③ 语音/视频的专门处理（回调结构同构则顺带纳入，否则明确拒绝并反馈）；④ 附件保留期与回收策略（复用既有产物清理口径）；⑤ 文档的结构化理解（表格还原、版面分析） |
| **前置假设** | ① 企微回调对图片/文件携带可下载的媒体引用与解密密钥——**现仅有 SDK docstring 依据，未以真实回调验证**（见 R-01）；② 当前配置的模型支持多模态图像输入；③ 既有 artifact store（RWX PVC）与 `runtime.artifact` 表可复用 |

---

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|----|------|------|---------|
| RULE-01 | 业务规则 | 企微入站的非文本消息**不得静默丢弃**：要么成功接收并落盘，要么产生用户可见反馈 + 审计记录 | E-01 / E-02 / E-03 / E-04 |
| RULE-02 | 业务规则 | 附件按租户隔离，跨租户读取必须失败 | E-05 |
| RULE-03 | 系统约束 | 只有**当前消息**的图片进入模型上下文；历史轮次附件仅保留文本引用，需模型主动调工具重看 | S-05 |
| RULE-04 | 系统约束 | 纯文本消息发给模型的请求体与改造前**逐字节一致** | S-03 / B-06 |
| RULE-05 | 系统约束 | 单文件 ≤20 MiB、单消息附件 ≤5 个；恰好等于上限应接收，超过 1 字节/1 个应拒绝 | B-01 / B-02 / B-03 |
| RULE-06 | 安全 | 解密密钥、媒体下载 URL 不得进入日志、审计、Prompt 或 API 响应 | E-04 / §3.5.3 |
| RULE-07 | 系统约束 | 通道差异只允许存在于渠道适配器内：核心域不得出现渠道分支，也**不得依赖任何渠道特有的取件形状**（`url`/`aes_key`/`media_id`/`download_code`） | S-07 |
| RULE-08 | 系统约束 | 通道枚举只在一处定义（`ChannelName`），其余位置引用该别名 | B-07 |

#### 2.5.2 功能验收场景

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 归属 | 前置条件 | 操作步骤 | 预期结果 |
|--------|--------|--------|---------|-------------|------|---------|---------|---------|
| S-01 | FEAT-01/02/03/05 | P0 | E2E | 真实企微回调桩 → 真实 PG/Redis → 真实 artifact 落盘 → 真实模型请求体 | 本模块 | 已配置 bot 与 Agent | 发送一张含已知文字的图片并提问 | 附件落盘且可读回；发给模型的请求体含图像内容块；模型回答体现图中内容 |
| S-02 | FEAT-01/02/03/06 | P0 | E2E | 真实企微回调桩 → 真实 PG → 真实 artifact → 工具真实抽取 | 本模块 | 同上 | 发送一份内容已知的 pdf（或 docx/xlsx/pptx）并提问其中事实 | 文档落盘；工具抽取文本与文档实际内容一致；回答正确 |
| S-03 | FEAT-05 | P0 | integration | 模型 provider 请求体（不 mock 组装层） | 本模块 | 纯文本会话 | 发送一条纯文本消息 | 请求体与改造前基线**逐字节相同** |
| S-04 | FEAT-03/04/06 | P0 | E2E | 回调 → 落盘 → 契约 → 工具 | 本模块 | — | 一条消息带 3 个附件（含图片与文档） | 三个附件各自落盘、可分别读取，互不覆盖 |
| S-05 | FEAT-06 | P0 | E2E | 真实上下文组装 + 真实模型 | 本模块 | 前一轮发过图片 | 模型调用重看工具后再提问 | 历史图片作为新的内容块被重发；模型能描述其中内容 |
| S-06 | FEAT-09 | P1 | integration | 工具 → artifact store → 读回 | 本模块 | — | Agent 调用写产物工具 | 产物落盘并可被读取工具读回（闭环） |
| S-07 | FEAT-01/03/04 | P0 | integration | `FakeChannelAdapter` → 门控 → 契约 → 落盘（**不含任何企微代码路径**） | 本模块 | 已注册 fake 适配器 | 用既有 `FakeChannelAdapter` 以**与企微不同的取件路径**（不经 `url`/`aeskey`）推一条带 `AttachmentRef` 的 `ChannelEnvelope` | 门控/契约/落盘/工具面行为与企微路径一致；核心域既不假设渠道，也不假设取件形状（守住"换通道能复用"） |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 归属 | 触发条件 | 系统行为 | 用户感知 |
|--------|--------|---------|-------------|------|---------|---------|---------|
| E-01 | FEAT-07 | E2E | 回调 → 反馈投递 → 审计表 | 本模块 | 发送不受支持的类型（如语音） | 不落盘；写审计；经消息目录取文案 | 收到一句明确说明，且措辞不泄露内部细节 |
| E-02 | FEAT-07/08 | E2E | 同上 | 本模块 | 发送超大小上限的文件 | 不落盘；写审计 | 说明中**包含上限数值** |
| E-03 | FEAT-07 | E2E | 同上 | 本模块 | 媒体地址失效/下载超时 | 不落盘；写审计 | 收到明确失败说明 |
| E-04 | FEAT-02/07 | integration | 解密路径 + 日志/审计输出 | 本模块 | 密钥缺失或不匹配 | 不落半成品；写审计 | 收到失败说明；**日志与审计中无密钥与明文 URL** |
| E-05 | FEAT-03/06 | integration | DB 查询 + 工具越权校验 | 本模块 | 以租户 B 的身份读取租户 A 的 artifact_id | 拒绝并返回明确错误 | 读取失败，不泄露存在性细节 |
| E-06 | FEAT-06 | integration | 真实解析库 | 本模块 | 文档加密/损坏 | 返回明确错误 | Agent 能如实转述"这份文档读不了"，而非乱码 |
| E-07 | FEAT-01/03 | integration | 幂等键 + 落盘 | 本模块 | 同一消息被企微重投 | 去重；不产生重复产物 | 无重复响应 |

**边界场景**

| 场景ID | 测试层级 | 关键真实边界 | 归属 | 字段/条件 | 边界值 | 预期行为 |
|--------|---------|-------------|------|----------|--------|---------|
| B-01 | unit | 门控纯函数 | 本模块 | 单文件大小 | == 20 MiB | 接收 |
| B-02 | unit | 门控纯函数 | 本模块 | 单文件大小 | 20 MiB + 1 B | 拒绝 + 反馈 |
| B-03 | unit | 门控纯函数 | 本模块 | 附件数 | 5 个 / 6 个 | 接收 / 拒绝 |
| B-04 | unit | 路径解析函数 | 本模块 | 文件名 | `../../etc/passwd`、空串 | 使用安全产物键落盘；原文件名仅存元信息，不参与路径拼接 |
| B-05 | integration | 契约序列化 | 本模块 | 附件数 | 0 | 契约与请求体与现状一致 |
| B-06 | integration | provider 组装 | 本模块 | 内容形态 | 纯字符串 | 输出 `content` 为字符串（非数组），字段集不变 |
| B-07 | unit | 源码静态检查（不 mock） | 本模块 | `Literal["WECOM"]` 字面量出现处 | 全仓 | 字面量**只**出现在 `ChannelName` 定义处；其余位置均为别名引用（RULE-08） |
| B-08 | unit | 注入固定时钟（不 mock 被测函数本身） | 本模块 | 时间工具 | 注入已知固定时刻 | 工具返回该时刻，且带 IANA 时区标识（非裸 UTC 字符串，`harness-time#RULE-time-001`） |

#### 2.5.3 非功能指标 [按需]

**性能指标**

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-PERF-01 | 附件下载耗时上限 | 单文件 ≤30s（超时即失败并反馈） | 集成测试注入慢响应桩 |
| NFR-PERF-02 | 文档抽取耗时上限 | 单文件 ≤10s | 用 20 MiB 上限内的样本实测 |

**可靠性指标**

| 指标ID | 指标名称 | 目标值 |
|--------|---------|-------|
| NFR-REL-01 | 静默失败数 | 0（任何失败路径都必须落到反馈 + 审计） |

**安全性要求**

| 指标ID | 安全域 | 验收标准 |
|--------|--------|---------|
| NFR-SEC-01 | 租户隔离 | 跨租户读取附件必然失败（E-05） |
| NFR-SEC-02 | 敏感信息 | 解密密钥与媒体 URL 不出现在日志/审计/Prompt/响应（E-04） |

**兼容性要求**

| 指标ID | 兼容域 | 验收标准 |
|--------|--------|---------|
| NFR-COMPAT-01 | 既有行为 | 纯文本路径的模型请求体逐字节不变（S-03/B-06）；既有 im-gateway 与 agent-runtime 套件全绿 |

---

## 3. 技术设计

### 3.1 方案选型 [必填]

#### 备选方案对比 [多方案时必填]

**AD-1 附件字节由谁落盘、artifact 行由谁写**

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 网关写字节 + 网关写 `runtime.artifact` 行 | 一步到位 | 跨部署单元写 runtime schema；附件到达时**尚无 run_id**，与现有 `ck_artifact_run_task_xor`（run/task 恰有其一非空）冲突，需放宽约束 | 否决 |
| **B** | **网关只写字节到共享 artifact store（选相对 storage_key），`artifact` 行仍由 runtime 的 `ArtifactResultWriter` 在 Run 建立后写** | 每张表单一写入方；网关保持无状态；**无需改 schema**（run_id 非空即满足 XOR） | 极端情况下（消息通过门控但 Run 未建立）会留下无主字节 | **采纳** |
| C | 字节随消息契约内联（base64） | 无共享存储依赖 | 大文件把消息体撑爆；与"DB 只存相对 storage_key"口径冲突 | 否决 |

**AD-2 模型消息的内容形态**

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 新增独立字段 `parts`，`content` 保持 `str` | 纯文本路径零改动 | 两个字段语义重叠，provider 需处理"二选一"分支 | 否决 |
| **B** | **`content` 放宽为 `str \| tuple[ContentPart, ...]`**；provider 组装时字符串原样输出、元组输出为内容块数组 | 单一字段；纯文本路径字节不变（B-06 可断言） | 类型放宽后需靠测试守住既有行为 | **采纳** |
| C | 把图片转成文字描述再发 | 不改协议 | 需另配 vision/OCR；丢失细节，与"模型支持多模态"冲突 | 否决 |

**AD-3 历史图片如何被模型重看**

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 历史图片每轮全部重发 | 实现最简单 | token 随轮次线性增长 | 否决 |
| **B** | **历史轮次只保留文本引用；新增"重看"工具，工具调用后由运行时追加一条携带该图片内容块的 user 消息** | 成本可控；模型可按需取用 | 需在工具结果后注入消息（OpenAI 协议的 tool 角色不能携带图像，必须补一条 user 消息） | **采纳** |
| C | 不提供重看，历史图永久不可达 | 最省 | 多轮追问场景直接失效 | 否决 |

**AD-4 文档抽取放在哪一层**

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 网关侧抽取后把文本塞进消息 | 模型直接可用 | 渠道层背内容理解；其它入口（Console 上传、skill 产出）无法复用；网关镜像变重 | 否决 |
| **B** | **做成 Agent 工具，抽取在工具内，依赖落在 agent 侧** | 与入口无关、可复用、可单测；网关保持薄 | 多一次工具调用往返 | **采纳** |

**AD-5 出站文件发送**

本期不做（PRD §6 非范围①）。理由：企微 aibot 的文件发送需另一套上传/素材接口与协议（见 [[wecom-reply-protocol-40008]] 的经验——同一渠道的"会话内回复"与"主动投递"就是两套体），且与入站验收无耦合；单独立项风险更低。

**AD-6 附件引用放在哪一层（决定换通道能否复用）**

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 附件只挂在 `MessageInput`（Runtime 侧契约） | 改动面最小 | 适配器到核心域之间的 `ChannelEnvelope` 没有附件位 ⇒ **WeCom 媒体类型必须渗透过边界**，违反 `harness-im#RULE-im-001`；新通道要各自想办法把附件塞过去 | 否决（**v1.0 初稿即此方案，是缺陷**） |
| B | 附件只挂在 `ChannelEnvelope`，再转成 Runtime 侧另一种类型 | 边界干净 | 多一层转换与两套类型，转换处易漂移 | 否决 |
| **C** | **`AttachmentRef` 一处定义，`ChannelEnvelope` 与 `MessageInput` 同型使用** | 边界干净 + 零转换；新通道只写适配器 | 契约类型要放在渠道无关的公共位置 | **采纳** |

**AD-7 通道枚举是否收口**

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 维持现状（7 处 `Literal["WECOM"]` 各自写死） | 零改动 | 新增通道逐处放宽，漏一处运行期才炸 | 否决 |
| **B** | **在 contracts 定义一次 `ChannelName = Literal["WECOM"]`，其余 6 处引用** | 新增通道只改一行；类型错误编译期暴露 | 一次跨 7 文件的机械改动 | **采纳（产品已确认纳入本期：目标就是"未来能快速接入新 IM 通道"）** |

**AD-8 取件机制是否抽象成通用接口**

各 IM 的附件获取方式**本质不同**：企微 aibot 是「回调带下载 URL + `aeskey`」，钉钉是「`downloadCode` 换下载地址」（两次往返），飞书是「`message_id` + `file_key` 调资源接口」，Slack 是「`url_private` + Bearer token」。共同点只是"先拿凭据、再换字节"。

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A | 抽象一个跨渠道通用取件接口（如 `fetch(credential) -> bytes`） | 表面上统一 | 凭据形态无法统一（url? id? code?），抽象会退化成 `dict[str, Any]` 万能袋；且**必然有渠道要假装自己有 url/aeskey** | 否决 |
| B | 把企微的 `download_media(url, aes_key)` 当成事实标准 | 省事 | 新通道被套上企微形状 | 否决 |
| **C** | **取件完全渠道私有**：各适配器在自己的包内实现自己的取件流程；对外只统一"产出 `AttachmentRef`"这一结果 | 差异被彻底关在适配器内；边界类型只描述**已到手的字节**，不描述**怎么拿到**；新增通道无需任何核心域改动 | 每个通道各写一遍取件（本就无法复用，这是本质复杂度而非重复） | **采纳** |

**推论（已落进本设计）**：
- `AttachmentRef` **不得**出现任何取件凭据字段（url / media_id / download_code / aes_key）——这是本设计刻意留白的地方。
- 解密、多步换件、分片拼接、渠道特有鉴权，全部发生在适配器内部，外溢为零。
- 各渠道大小/类型限制不同：**渠道适配器可先用自身限制提前拒绝**（省流量），**核心域另有一道统一上限**（`AttachmentGate`）兜底，两者不冲突。
- `checksum` 在**解密后**计算，保证同一文件在不同渠道/密钥下校验值一致。

#### 关键决策记录

| 决策 | 内容 | 依据 |
|------|------|------|
| AD-1-B | 网关只落字节、runtime 落 `artifact` 行 | 单一写入方 + 网关无状态（`harness-arch#RULE-arch-001`）+ 不改 schema |
| AD-2-B | `content` 放宽为 `str \| tuple[ContentPart,...]` | 保持单字段；用"纯文本字节不变"作为兼容护栏（RULE-04） |
| AD-3-B | 历史图片按需重看（工具 + 追加 user 消息） | 成本与能力折中；用户选定 |
| AD-4-B | 抽取做成 Agent 工具 | 与入口无关，避免渠道层背内容理解 |
| AD-5 | 出站发送不做 | 与入站无耦合，单独立项 |
| AD-6-C | `AttachmentRef` 一处定义、`ChannelEnvelope` 与 `MessageInput` 同型使用 | 让"新增通道只写适配器"成立；附件不穿透渠道边界（`harness-im#RULE-im-001`） |
| AD-7-B | 通道枚举收口为 `ChannelName = Literal["WECOM"]`，其余 6 处引用（**纳入本期**） | 产品明确目标：未来能快速接入新 IM 通道；此为复用成本的主要杠杆 |
| AD-8-C | **取件机制完全渠道私有**，不设跨渠道通用取件接口；对外只统一"产出 `AttachmentRef`" | 各 IM 取件方式本质不同（url+aeskey / downloadCode / file_key / Bearer），强行统一会退化成万能袋并逼新通道假装有 url/aeskey |

#### 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 渠道下载/解密 | 复用既有 `aibot` SDK 的下载能力（`WeComSdkPort` 扩展暴露） | 已在依赖树内；解密算法由 SDK 承担 |
| 多模态 | 沿用唯一协议 `OPENAI`（`harness-model#RULE-model-001`） | 不引入第二协议 |
| 文档抽取 | `pypdf`（pdf）、`python-docx`（docx）、`openpyxl`（xlsx）、`python-pptx`（pptx）、txt/md 原生 | 纯 Python、无系统级依赖，便于镜像与测试；按需导入避免拖慢启动 |
| 存储 | 既有 `NfsArtifactStore`（RWX PVC） | `harness-skill#RULE-skill-001` 指定口径 |

### 3.2 架构设计 [必填]

#### 3.2.1 数据流

```
企微 → [im-gateway] 回调
        ├─ ① 分流入站消息（文本 / 图片 / 文件 / 其他）
        ├─ ② 门控校验（类型、大小、数量）      ── 不通过 → 反馈 + 审计（FEAT-07）
        ├─ ③ SDK 下载并解密                    ── 失败   → 反馈 + 审计
        ├─ ④ 写共享 artifact store（相对 storage_key）
        └─ ⑤ ChannelEnvelope 携带 AttachmentRef（**渠道边界，渠道无关**）
                                          ↓
                             [核心域] 门控 → 反馈/审计 → RunRequest
                                          ↓
                             [agent-runtime] Run 建立
        ├─ ⑥ 落 `artifact` 行（run_id 非空）→ 得到 artifact_id
        ├─ ⑦ 组装上下文：当前消息图片 → 内容块；历史附件 → 文本引用
        └─ ⑧ 工具面：read_attachment / view_image / write_artifact
```

#### 技术分层

| 层 | 职责 | 位置 |
|----|------|------|
| 渠道适配 | 入站分流、下载解密、媒体引用标准化（**SDK 类型不渗透核心域**） | `apps/im-gateway/.../channels/wecom/` |
| 应用编排 | 门控、落盘、反馈与审计、契约构造 | `apps/im-gateway/.../application/` |
| 契约 | `MessageInput.attachments` 结构化承载 | `packages/contracts/` |
| 模型协议 | 内容形态放宽与请求体组装 | `packages/agent-core/.../model/` |
| 运行时 | artifact 行落库、上下文组装、工具注册 | `apps/agent-runtime/.../application/` |

#### 3.2.3 通道复用性（新增通道时哪些要动、哪些不用动）

**现状盘点**：`ChannelAdapter`（Protocol）与 `ChannelRegistry`、`FakeChannelAdapter` 已经是渠道无关的，边界类型是契约 `ChannelEnvelope`。也就是说**骨架早就支持多通道**，缺的是附件位与枚举收口。

| 层 | 新增一个 IM 通道时要动吗 | 说明 |
|----|------------------------|------|
| 渠道适配器（`channels/<new>/`） | **要** | 实现 `ChannelAdapter`：连接、`iter_events`、`send`/`stream`；把该渠道的媒体下载/解密后**填成 `AttachmentRef`** |
| 渠道边界契约（`ChannelEnvelope` / `AttachmentRef`） | **不要** | 本设计已把附件位放上去；新通道复用同一结构 |
| 门控 / 反馈 / 审计（`application/`） | **不要** | 全部基于 `ChannelEnvelope`，不含渠道分支 |
| Runtime 侧（上下文组装、`artifact` 行、工具面） | **不要** | 工具面本就与入口无关（AD-4） |
| 通道枚举 `Literal["WECOM"]` | **要** | 见下 |

**通道枚举的现状与代价**：`Literal["WECOM"]` 目前**逐处写死 7 处**——`contracts/channel.py:24/33/47`、`contracts/tasks.py:18`、`contracts/runtime.py:12`、`contracts/resolve.py:15`、`console dto.py:482`。新增通道必须逐处放宽，漏一处就是运行期校验失败。

- **本期实施**：在 contracts 内定义一次 `ChannelName = Literal["WECOM"]`，其余 6 处引用它。新增通道时只改一行，且类型错误会在编译期暴露而不是运行期。
- 这是一处**纯机械改动、不改变任何行为**，但是"换通道要动几处"从 7 变 1 的直接杠杆。产品已确认纳入本期（目标即"未来能快速接入新 IM 通道"）。

**取件机制的差异不外溢**：各 IM 的附件获取方式本质不同（企微 `url`+`aeskey`、钉钉 `downloadCode` 换地址、飞书 `message_id`+`file_key`、Slack `url_private`+Bearer）。本设计**不抽象通用取件接口**——取件是各适配器的内部实现（AD-8），对外只统一"产出 `AttachmentRef`"这一**结果**。因此"新通道取件方式不同"不需要任何核心域改动。

**复用性的可验证形式**：用既有的 `FakeChannelAdapter`，以**与企微完全不同的取件路径**（不经 `url`/`aeskey`，直接给出字节与元信息）产出一条带附件的 `ChannelEnvelope`，断言门控/契约/落盘/工具面行为与企微路径**完全一致**（S-07）。这条用例不需要实现任何新通道，却能真实守住"核心域不含渠道分支、也不假设取件形状"。若将来某处代码偷偷依赖了企微形状，S-07 会红。

**复用边界：到哪里为止（重要，防止误读）**

本设计让**核心域**对通道中立，但**不等于"加任何新通道都近乎零成本"**。明确划清：

| 新通道自带的东西 | 复用吗 | 说明 |
|----------------|--------|------|
| 契约 / 落盘 / 门控 / 反馈 / 审计 / Runtime / 工具面 | ✅ **复用** | 本设计的全部收益都在这一列 |
| 适配器（取件、发送、连接） | ❌ 自写 | 本质复杂度，AD-8 已说明 |
| **自带前端 / 自带上传入口** | ❌ **自写，且不在本设计内** | 见下 |

**以"自建 web 对话页通道"为例**（本期**不做**，此处仅界定边界）：它虽然是"再加一个渠道"，但会额外带进来四块**本设计完全没有覆盖**的东西——

1. **上传入口**：企微是「平台给 URL、我们出站去拉」；web 是「浏览器 multipart **入站推给我们**」。本设计**不含任何 HTTP 入口**——流式写盘、实际上限与 `Content-Length` 的关系、上传中断、上传完成与消息到达的时序，均未定义。
2. **一整个前端**：chat 页面、上传组件、会话状态。本设计是**纯后端**，`harness-front` / `harness-ui` / `harness-ui-detail` / `harness-i18n`（前端侧）这些在本需求里被标为「不适用」的 required Spec，**在该通道下会全部变成适用**——N/A 的判断是**针对本需求范围**的，不是永久结论。
3. **身份模型不同**：web 用户若是登录用户，本就有 `platform_user_id`；而企微走的是「外部身份 + `/bind` 绑定」。两套模型不可互相套用。
4. **`bot_id` 语义需重新定义**：本需求沿用既有口径（一个 `bot_id` 路由到一个 Agent）；web 通道的"bot"指什么（某 Agent 的前端入口？）需要单独设计。

**结论**：先做企微再做 web，省下的是**地基**（第 1 行），省不掉**上述四块**。本设计对 web 通道的价值是"不设障"，而不是"已实现"。

#### 外部依赖清单 [按需]

| 依赖 | 用途 | 状态 |
|------|------|------|
| `aibot` SDK 下载能力 | 媒体下载（解密由 SDK 承担） | 已在依赖树；**接口形态待真机确认（R-01）** |
| `pypdf` / `python-docx` / `openpyxl` / `python-pptx` | 文档文本抽取 | 新增 |

### 3.3 数据设计 [必填]

**本模块不新增表**。复用 `runtime.artifact`（`apps/agent-runtime/.../infrastructure/models/runtime.py:214`）：

| 列 | 本需求的用法 |
|----|-------------|
| `tenant_id` / `conversation_id` | 由 Run 上下文带入，支撑租户隔离与按会话取回 |
| `run_id` | 非空（消息触发的 Run）；因此既有 `ck_artifact_run_task_xor` 继续成立，**无需迁移** |
| `artifact_type` | `INBOUND_IMAGE` / `INBOUND_DOCUMENT` / `INBOUND_OTHER` |
| `storage_key` | **相对键**（`harness-skill#RULE-skill-001`：DB 只存相对 key） |
| `media_type` / `size` / `checksum` | 下载后计算，供校验与幂等 |
| `metadata_json` | `{"filename": ..., "source": "wecom", "external_message_id": ...}`（关键查询字段不藏 JSON，遵循 `harness-data#RULE-data-001`） |
| 标准列 | `id/is_deleted/create_time/update_time` 由 `StandardColumnsMixin` 提供；时间 `timestamptz` |

**审计**：接收/拒绝/失败复用既有审计写入路径（`audit_writer`），不新增表。

### 3.4 接口设计 [必填]

#### 形态 C：函数 / 库接口

#### 接口清单

| 接口ID | 名称 | 形态 | 覆盖 FEAT | 位置 |
|--------|------|------|----------|------|
| API-01 | `AttachmentRef` + `ChannelEnvelope.attachments` + `MessageInput.attachments` | 契约 | FEAT-04 | `packages/contracts/.../channel.py`、`runtime.py` |
| API-02 | `WeComAdapter._to_inbound_message` 分流 | 函数 | FEAT-01 | `channels/wecom/adapter.py` |
| API-03 | `WeComSdkPort.download_media`（**渠道私有，非公共接口**） | 端口 | FEAT-02 | `channels/wecom/sdk_port.py` |
| API-04 | `InboundAttachmentService.persist` | 服务 | FEAT-03 | `application/` |
| API-05 | `ModelMessage.content` 内容形态 + provider 组装 | 库接口 | FEAT-05 | `packages/agent-core/.../model/` |
| API-06 | `read_attachment` / `view_image` 工具 | 工具 | FEAT-06 | `apps/agent-runtime/.../application/` |
| API-07 | `AttachmentGate` 门控 | 函数 | FEAT-08 | `apps/im-gateway/.../application/` |
| API-08 | `write_artifact` 工具 | 工具 | FEAT-09 | `apps/agent-runtime/.../application/` |
| API-09 | `current_time` 工具 | 工具 | FEAT-10 | `apps/agent-runtime/.../application/` |

#### API-01: 附件契约（渠道边界 + 消息契约）

```python
# packages/contracts/.../channel.py —— 渠道无关，**渠道适配器产出即此类型**
class AttachmentRef(ContractModel):
    storage_key: str = Field(min_length=1)
    kind: Literal["IMAGE", "DOCUMENT", "OTHER"]
    media_type: str = Field(min_length=1)
    size: int = Field(gt=0)
    filename: str | None = None
    checksum: str = Field(min_length=1)
    source_channel: ChannelName                      # Literal["WECOM"]（见 §3.2.3）

class ChannelEnvelope(ContractModel):
    channel: ChannelName
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None
    message_id: str = Field(min_length=1)
    text: str = ""
    attachments: list[AttachmentRef] = Field(default_factory=list)   # 新增

# packages/contracts/.../runtime.py —— 沿用同一类型，不做转换
class MessageInput(ContractModel):
    id: str = Field(min_length=1)
    type: Literal["text", "attachment"] = "text"   # 向后兼容：缺省仍为 text
    text: str = ""
    attachments: list[AttachmentRef] = Field(default_factory=list)
```

- **为什么两处同型**：`ChannelEnvelope` 是渠道适配器与核心域之间**唯一的边界类型**（`ChannelAdapter.iter_events() -> AsyncIterator[ChannelEnvelope]`）。附件如果只挂在 `MessageInput` 上，WeCom 的媒体类型就必须穿过适配器边界渗透到核心域，违反 `harness-im#RULE-im-001`。同型还让"新增通道"只需适配器填同一结构，核心域零改动。
- 错误处理：`attachments` 非空而 `kind` 与 `media_type` 不匹配时由门控拒绝，不进入契约。
- 兼容性：`type` 新增取值但默认不变；零附件时序列化结果与现状一致（B-05），既有 `ChannelEnvelope` 生产者不填 `attachments` 即可继续工作。

#### API-03: `WeComSdkPort.download_media` —— **渠道私有，不是公共接口**

```python
# apps/im-gateway/.../channels/wecom/sdk_port.py —— 只在 wecom 包内使用
async def download_media(self, url: str, aes_key: str | None) -> bytes: ...
```

- **刻意的形状差异**：这个签名（`url` + `aes_key`）是**企微 aibot 专属**的取件方式。**不得**把它提升为跨渠道通用接口，也**不得**让任何核心域代码依赖它——否则新通道会被迫假装自己有 url/aeskey（AD-8）。
- 超时与字节上限由实现层强制（NFR-PERF-01 / RULE-05）。
- 失败以明确异常类型上抛（区分超时 / 网络 / 解密失败），由上层转为反馈 + 审计。
- **不得**把 `url` 与 `aes_key` 写入日志（RULE-06）。
- **checksum 必须在解密之后计算**：否则同一份文件在不同密钥下得到不同校验值，幂等与校验都会失准。

#### API-05: 模型内容形态

```python
class ImagePart(ContractModel):
    media_type: str
    data_base64: str          # 或 url（实现时按供应商支持的形态二选一，见 §5 R-09）

@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: str | tuple[str | ImagePart, ...]
    ...
```

- provider 组装：`str` → 原样（字段集不变）；元组 → `content` 数组。
- 兼容护栏：S-03 / B-06 以"逐字节一致"断言既有纯文本行为。

#### API-06: 附件工具

| 工具 | 入参 | 返回 | 说明 |
|------|------|------|------|
| `read_attachment` | `artifact_id` | 文本（文档抽取结果 / 文本文件内容） | 图片类返回提示改用 `view_image`；不可解析返回明确错误（E-06） |
| `view_image` | `artifact_id` | 确认文本 + 触发运行时追加一条携带该图片内容块的 user 消息 | 仅限 `kind=IMAGE`；跨租户拒绝（E-05） |

#### API-07: 门控

```python
def evaluate_gate(candidates: Sequence[AttachmentCandidate]) -> GateDecision: ...
```

- 纯函数，无 IO —— 便于 B-01/B-02/B-03 单测。
- P0 读模块常量；P1 改为读配置。

### 3.5 质量实现方案 [必填]

#### 性能设计 [按需]

| 热点路径 | 量级估计 | 策略 | 放弃的较慢方案 |
|---------|---------|------|---------------|
| 入站下载（每附件 1 次外部 HTTP） | 单消息 ≤5 附件 | 串行 + 单文件 30s 超时 + 20 MiB 上限；超限在下载**之前**用 `size` 元信息拒绝（避免白下载） | 先下载再判大小（浪费带宽与时间） |
| 文档抽取（工具内） | 单文件 ≤20 MiB | 按需导入解析库（不拖慢服务启动）；抽取在工具调用内同步完成，10s 上限 | 后台任务化（引入 Worker 协调，收益不抵复杂度） |
| 上下文组装（每轮） | 每轮 1 次 | 只内联**当前消息**图片（AD-3-B），历史仅文本引用 ⇒ 上下文长度不随轮次线性增长 | 历史图片全量重发（token 随轮次线性涨） |

#### 可靠性设计 [按需]

- 失败分级：门控拒绝 / 下载失败 / 解密失败 / 落盘失败 —— 各自映射到明确的用户可见文案与审计码（RULE-01）。
- 幂等：复用既有消息幂等键，重投不产生重复产物（E-07）。
- 原子落盘：沿用 `ArtifactResultWriter._write_immutable` 的"临时文件 + `os.replace`"手法（先写 `.tmp-*` 再原子替换），避免半成品（E-04）。

#### 安全性设计 [按需]

- 租户隔离：artifact 取回按 `tenant_id` 过滤；工具入口二次校验（E-05，`harness-auth#RULE-auth-001`）。
- 敏感信息：`aes_key` 与媒体 `url` 不入日志/审计/Prompt/响应（RULE-06，`harness-secret#RULE-secret-001`）。
- 路径安全：文件名不参与路径拼接，使用产物键（B-04）。

#### 可观测性设计 [按需]

- 日志：复用 logging-kit（`harness-log#RULE-log-001`），自动脱敏敏感字段。
- 审计：接收 / 拒绝 / 失败各一条，含 `external_message_id`、附件数、拒绝原因码 —— 支撑 `PRD §2.2` 的成功指标度量。
- 指标：复用既有 `artifact_bytes_total`，附件类型作为标签维度。

---

## 4. 部署与运维

### 4.1 部署架构

无新增部署单元（`harness-arch#RULE-arch-001` 固定四单元）。网关新增的依赖为纯 Python 库；文档解析依赖落在 **agent-runtime** 侧，网关镜像不因此变重。

### 4.2 发布与回滚 [按需]

- 契约放宽（`type` 新增取值、`attachments` 结构化）为**向后兼容**变更：旧调用方行为不变，可先行发布。
- 回滚点：契约与 provider 改动独立于渠道改动，可分别回滚。

### 4.4 数据迁移 [按需]

**无迁移**（AD-1-B 复用既有 `artifact` 表且不改约束）。

---

## 5. 风险与依赖

### 5.1 项目依赖

| 依赖方 | 依赖内容 | 风险等级 |
|--------|---------|---------|
| 企微 aibot SDK / 开放平台 | 媒体引用与密钥字段的真实结构、可下载 URL 时效 | **高** |
| 模型供应商 | 多模态内容块在其 OpenAI 兼容接口上的具体形态（`image_url` vs base64） | 中 |
| 文档解析库 | pdf/docx/xlsx/pptx 抽取质量 | 中 |

### 5.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 | 验证场景 |
|--------|------|------|---------|---------|
| R-01 | 回调中媒体字段结构未经验证（唯一依据是 SDK docstring） | 契约可能定偏、返工 | **编码前先做真机探针**抓一条真实图片与文件回调样本落档，再定契约字段 | 探针产物（人工） |
| R-02 | 下载/抽取拖慢消息链路 | 响应变慢或超时 | 上限 + 超时（NFR-PERF-01/02）；超限前置拒绝 | E-02 / E-03 |
| R-03 | 解密失败（密钥缺失/算法不符） | 附件不可用 | 明确失败 + 审计，不落半成品 | E-04 |
| R-04 | 放宽 `content` 类型误伤纯文本路径 | 既有对话回归 | "纯文本请求体逐字节不变"硬断言 | S-03 / B-06 |
| R-05 | 多模态内容块形态与供应商实现不符 | 图片发不出去 | 真机探针先验证一次真实图片请求；不可达时降级为文本引用 | S-01 |
| R-06 | 文档解析库引入供应链与镜像体积成本 | 镜像变大、漏洞面增加 | 纯 Python 选型；按需导入；依赖落在 agent 侧 | — |
| R-07 | 网关写字节但 Run 未建立 ⇒ 无主产物 | 存储泄漏 | 仅在通过门控后落盘；纳入既有产物清理口径；记为已知缺口 | — |
| R-08 | 范围横跨两模块，单批偏大 | 评审与验收周期拉长 | P0 先交付并独立验收，P1 紧随 | — |
| R-09 | 图片在请求体中的承载形态（base64 内联 vs URL）未定 | 实现返工 | 设计阶段用 R-01/R-05 的探针一并确定 | S-01 |

---

## 6. 需求追溯矩阵

| US | FEAT | 接口 | 验收场景 |
|----|------|------|---------|
| US-01 | FEAT-01 / FEAT-02 / FEAT-03 | API-01 / API-02 / API-03 / API-04 | S-01 / S-07 / E-07 |
| US-02 | FEAT-03 / FEAT-04 / FEAT-05 / FEAT-06 | API-01 / API-04 / API-05 / API-06 | S-01 / S-04 / S-05 / S-07 / E-05 / B-06 |
| US-03 | FEAT-03 / FEAT-04 / FEAT-06 | API-01 / API-06 | S-02 / S-04 / E-06 |
| US-04 | FEAT-07 / FEAT-08 | API-07 | E-01 / E-02 / E-03 / B-01 / B-02 / B-03 |
| US-05 | FEAT-09 | API-08 | S-06 |
| US-06 | FEAT-10 | API-09 | B-08 |
| US-07 | FEAT-07 | API-04 / API-07 | E-01 / E-02 / E-03 / E-04 |

> 缺口自检：**无未闭合缺口**。US-06 原先缺场景，已在本阶段补 `B-08`（注入固定时钟断言返回值与时区标识）。
> 另：AD-7/AD-8（通道枚举收口、取件渠道私有）不是用户故事派生的功能，而是系统约束，由 RULE-07/RULE-08 经 S-07/B-07 覆盖，故不占 US→FEAT 行。

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | verifier_ref | 状态/N/A 理由 |
|---|---|---|---|---|---|---|
| `harness-arch#RULE-arch-001` | required | 固定四部署单元；**网关保持无状态** ⇒ 附件字节落共享 artifact store 而非网关本地盘，网关不写 DB。 | §3.1 AD-1-B、§3.2.1 | S-01 / S-02 + 原 verifier | harness-arch#RULE-arch-001 | applied |
| `harness-im#RULE-im-001` | required | 不改变 Agent↔bot 路由；**SDK 类型不渗透核心域** ⇒ 媒体引用在适配层标准化为 `AttachmentRef`，并挂在**渠道边界类型** `ChannelEnvelope` 上（而非只挂 Runtime 侧契约），核心域因此不含渠道分支。 | §3.2.2、§3.2.3、API-01/API-02 | S-01 / S-02 / S-07 + 原 verifier | harness-im#RULE-im-001 | applied |
| `harness-model#RULE-model-001` | required | 多模态**必须走唯一的 OPENAI 兼容协议**，不引入第二协议；不改 `model_id` 显式绑定口径。 | §3.1 技术栈、API-05 | S-01 / S-03 + 原 verifier | harness-model#RULE-model-001 | applied |
| `harness-snapshot#RULE-snapshot-001` | required | 附件属**消息载荷**而非配置/授权，不进 Snapshot 冻结范围；Run 仍按既有口径冻结 Agent/Model/Skill/MCP 版本。 | §3.2.1（第⑥步在 Run 建立后落 `artifact` 行）、§3.3 | S-01 + 原 verifier | harness-snapshot#RULE-snapshot-001 | applied |
| `harness-skill#RULE-skill-001` | required | Artifact Store 为 RWX PVC；**DB 只存相对 `storage_key`**；落盘沿用"临时文件 + 原子替换"手法。 | §3.3、§3.5.2 | S-01 / E-04 + 原 verifier | harness-skill#RULE-skill-001 | applied |
| `harness-auth#RULE-auth-001` | required | 附件取回按 `tenant_id` 过滤，工具入口二次校验；未授权资源不得进入 Prompt/ToolRegistry——`view_image`/`read_attachment` 对越权 artifact_id 必须拒绝。 | §3.5.3、API-06 | E-05 + 原 verifier | harness-auth#RULE-auth-001 | applied |
| `harness-secret#RULE-secret-001` | required | 解密密钥与媒体 URL **不得进入日志、审计、Snapshot、Prompt、API 响应**；密钥仅在下载调用内使用，不落任何持久化面。 | §3.5.3、API-03 | E-04 + 原 verifier | harness-secret#RULE-secret-001 | applied |
| `harness-log#RULE-log-001` | required | 新增的接收/拒绝/失败日志统一走 logging-kit，仅配置 `LOG_DIR`，依赖其自动脱敏（含密钥类字段）。 | §3.5.4 | E-01 / E-04 + 原 verifier | harness-log#RULE-log-001 | applied |
| `harness-i18n#RULE-i18n-001` | required | 用户可见反馈文案**不硬编码**，经既有消息目录取（`inbound.py:377/478/591` 的 `catalog.message(code, locale)`），在 `config/api-messages.yaml` 新增 zh-CN/en-US 词条；不改框架。 | §3.2.2（反馈）、API-07 | E-01 / E-02 / E-03 + 原 verifier | harness-i18n#RULE-i18n-001 | applied |
| `harness-data#RULE-data-001` | required | **不新增表**，复用 `runtime.artifact`：标准四列由 `StandardColumnsMixin` 提供、时间 `timestamptz`、`metadata_json` 用 `jsonb` 且关键查询字段不藏 JSON。 | §3.3 | S-01 / E-07 + 原 verifier | harness-data#RULE-data-001 | applied |
| `harness-test#RULE-test-001` | required | 跨渠道回调/DB/落盘/模型请求体的流程必须 E2E 且不 mock 真实边界；抽取纯逻辑（门控、路径安全）下沉 unit；契约序列化用 integration。 | §2.5.2 全表 | S-01..S-06 / E-01..E-07 / B-01..B-06 + 原 verifier | harness-test#RULE-test-001 | applied |
| `harness-time#RULE-time-001` | required | 附件时间戳统一 `timestamptz`；FEAT-10 的时间基准必须带 IANA 时区口径（与调度时区一致），不得返回裸 UTC 字符串。 | §3.3、API-09 | B-08 + 原 verifier | harness-time#RULE-time-001 | applied |
| `harness-worker#RULE-worker-001` | required | 本次改动只把 `DeliveryRouteInput.channel` 的 `Literal["WECOM"]` 换成**等价**的 `ChannelName` 别名（AD-7 枚举收口）——**行为等价**，未触及 claim/lease、权威源、Redis hint 或 Task 状态机；附件能力不进入 Worker 链路（同步工具调用，不落后台任务）。该 Rule 由路径映射（改动 `contracts/tasks.py`）**自动绑定**，此处为局部承接。 | §3.2.3（枚举收口，等价替换） | B-07 + 原 verifier（`uv run pytest -q tests/agent_worker`） | harness-worker#RULE-worker-001 | applied |
---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 附件（attachment） | 用户在 IM 会话中发送的图片或文件（非文本消息的载荷） |
| 产物（artifact） | 落盘并由 `runtime.artifact` 行引用的文件实体 |
| 多模态内容块 | 模型消息内容的非文本形态（图像等），与文本并列 |
| 自动重看 | 工具调用后由运行时追加一条携带图像内容块的 user 消息，使模型可回看历史附件 |
| 静默丢弃 | 消息被处理链路忽略且不产生任何用户可见反馈或审计痕迹 |

---

*文档结束*
