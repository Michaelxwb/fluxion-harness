# PRD: 平台统一文件产物投递

> **文档编号**: PRD-2026-11-17-01
> **文档版本**: v2.0
> **创建日期**: 2026-11-17
> **产品负责人**:
> **状态**: 草稿

**评审边界说明**:
- **需求基线**: 第 2-8 章（背景/用户/功能/非功能/范围/依赖/已有规范约束）→ 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、R-（风险）、OQ-（开放问题）、Spec/Rule（已有规范引用）

**本版关键结论（先读）**:
1. **统一单元是消息，不是单个文件**：一条消息 = 正文 + `attachments[]`；多文件是一条消息多个附件，不是多条文件消息。
2. **WebChat / MSSWWEB 是 pull**：它们共享同一个用户-Agent 的 Web 数据（会话、任务列表、文件、产物），没有投递意图、没有投递泵。
3. **只有 WECOM 是 push**：push 走单一的消息级投递状态机；用户没有绑定企微就不产生 push。
4. **定时任务的 Web 可见性与企微 push 是两件事**：定时任务始终在 Web 任务中心可读（有 Web 入口时）；push 目标由 Schedule 显式配置，独立于创建入口。
5. **直接对话只属于来源会话**：WebChat 直接对话只进 Web，企微直接对话只进企微；不跨入口复制。
6. **未来功能只产出消息/附件，不写投递代码**：平台按用户-Agent 的可用入口自动选择 pull / push / 两者 / 都没有。
7. **参考 OpenClaw 的结构，不照搬文本指令**：`ReplyPayload {text, mediaUrls/attachments}`、pull 读 transcript、push 才入队；`MEDIA:` 只是最终助手文本的兼容解析，不是主契约。

---

## 2. 背景与目标

### 2.1 问题陈述

| 维度 | 内容 |
|------|------|
| **问题描述** | 文件产物没有统一为平台能力：Skill 作者被要求写 `SKILL_OUTPUT_DIR`，但“落盘/登记”和“用户收到”是两件事；不同执行路径各自决定是否、如何交付；定时任务、直接对话、Web 与企微的行为不一致；未来新增入口/通道需要重复实现文件投递。 |
| **当前替代方案** | 有的路径依赖模型调用 `deliver_artifact`，有的依赖 Worker 的单文件 `result_artifact_id`，有的只在 Runtime 内联登记；WebChat 直接发布、企微直接调 Gateway，失败没有统一持久重试。 |
| **触发原因** | 定时任务测试误用 `big-output`、模型调用 `execute` 漏 `target`、本地 `WORKSPACE_ROOT` 为相对路径；同时暴露出文件生成/投递不应由某个 Skill 或模型记忆承担。 |

### 2.2 目标与价值

| 维度 | 内容 |
|------|------|
| **核心目标** | 建立全平台唯一的**消息附件契约**与**入口能力解析**：任何执行只产出“正文 + 附件”，平台按用户-Agent 的 Web 入口/企微绑定决定 pull / push；文件登记、附件冻结、push 重试与任务中心读取只有一套语义。 |
| **预期价值** | 文件输出不再依赖具体 Skill 或模型是否记得调用工具；Web 与企微行为一致可解释；定时任务和直接对话共用底层；未来新增入口/通道路由即可接入。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 附件消息契约 | 多套（直接发布/单文件/工具） | 1 套 `MessagePayload {text, attachments}` | 代码与契约只保留一种附件消息形状 |
| push 投递状态机 | Run 与 Task 两套 | 1 套消息级 `message_delivery` | 代码与测试只保留一个 push 状态机 |
| 定时任务 Web 可见性 | 依赖执行路径 | 100% 在 Web 任务中心可读（有 Web 入口时） | 集成测试覆盖企微/Web 创建 |
| 重复用户可见发送 | 多路径各自实现 | 同一条消息意图 + target 至多一次 | 稳定 message key + Gateway 幂等 |
| 发送结果未知 | 超时/无回执被当失败重发 | 进入 UNKNOWN 待核实、不自动重发 | 持久状态 + 集成测试 |
| 未来入口/通道接入 | 需要改交付代码 | 只接收入口/适配器，不改业务功能 | 新入口只实现 pull/push 适配 |

---

## 3. 用户与场景

### 3.1 目标用户

| 维度 | 内容 |
|------|------|
| **用户画像** | 业务 Skill 作者、WebChat/MSSWWEB 用户、企微用户、任务/执行记录查看者。当前约 200 人。 |
| **使用场景** | Skill 生成报告/图片/JSON；Agentic 任务生成文件；WebChat 用户接收站内文件；企微用户接收附件；定时任务周期生成文件；同一用户-Agent 同时拥有 Web 与企微入口。 |

### 3.2 用户故事

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为业务 Skill 作者，我只把交付物写进 `SKILL_OUTPUT_DIR`，不需要实现投递逻辑。 | P0 |
| US-02 | 作为 Web 用户，我在 WebChat 或 MSSWWEB 的历史/任务中心看到同一份文件与任务结果，不因刷新/入口不同收到多份。 | P0 |
| US-03 | 作为企微用户，我在企微对话或定时任务结果中收到正文与附件；没有绑定企微时不产生 push。 | P0 |
| US-04 | 作为定时任务使用者，无论从 Web 还是企微创建，任务结果都在 Web 任务中心可读；配置了企微 push 时企微也收到。 | P0 |
| US-05 | 作为直接对话用户，Web 对话的产物只在 Web，企微对话的产物只在企微；不跨入口复制。 | P0 |
| US-06 | 作为平台/未来功能作者，我只需要产出消息和附件；平台根据用户-Agent 的入口自动 pull/push，不需要我判断底层。 | P0 |
| US-07 | 作为平台使用者，我希望 `execute` 契约明确、`WORKSPACE_ROOT` 在启动时归一，避免平台能力被参数歧义或本地配置阻断。 | P0 |

### 3.3 关键场景

| 编号 | 场景 | Web（pull） | 企微（push） |
|------|------|-------------|--------------|
| S-01 | 企微创建定时任务，有附件产物 | Web 任务中心显示任务结果 + 附件（同一用户-Agent 有 Web 入口时） | 企微收到任务结果 + 附件 |
| S-02 | 企微直接对话执行 Skill，有附件 | 无（直接对话不属于任务中心） | 企微收到附件 |
| S-03 | WebChat 创建定时任务，有附件 | Web 任务中心显示任务结果 + 附件 | 配置企微路由时，企微收到 |
| S-04 | WebChat 直接对话执行 Skill，有附件 | WebChat 历史显示附件 | 无 |
| S-05 | MSSWWEB 作为又一个 Web 入口 | 与 WebChat 共享会话、任务列表、文件、产物 | 无 |
| S-06 | 用户只有 Web 入口、没有企微绑定 | 正常 pull | 不产生 push，不报错 |
| S-07 | 用户只有企微绑定、没有 Web 入口 | 无 pull | 正常 push |
| S-08 | 同一执行多个文件 | 一条消息多个附件 | 一条消息多个附件（或通道能力允许的批量） |
| S-09 | 用户要求“再发一次” | 新消息/新附件事件，允许重复可见 | 新消息意图，允许再次 push |
| S-10 | 发送结果未知（超时/无回执） | 不适用 | UNKNOWN 待核实，不自动重发 |

---

## 4. 功能需求

### 4.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-01 | 统一输出目录契约 | `SKILL_OUTPUT_DIR` 是用户产物目录；平台登记成功执行的输出并挂到结果消息；`SKILL_DATA_DIR`/`/tmp` 放中间文件。 | P0 | US-01 |
| FEAT-02 | 统一 Artifact 模型 | 一个不可变版本一个 `artifact_id`；逻辑文件由 `logical_file_id + version` 关联；用户可见产物统一 `OUTPUT`，入站/内部类型不变。 | P0 | US-01/02/03 |
| FEAT-03 | 统一消息附件契约 | 一条消息 = 正文 + `attachments[]`；多文件一条消息；附件冻结版本/checksum；pull/push 共用。 | P0 | US-02/03/06 |
| FEAT-04 | 入口能力解析 | 按用户-Agent 的 Web 入口/企微绑定与消息来源决定 pull / push / 两者 / 都没有；不要求功能代码判断。 | P0 | US-06 |
| FEAT-05 | 单一 push 投递状态机 | 消息级 `message_delivery`：稳定 message key、持久尝试、租约/fencing、UNKNOWN、DEGRADED、FAILED；替代 Run/Task 两套 push 分支。 | P0 | US-02/03 |
| FEAT-06 | 定时任务双表面 | 定时任务始终 Web 任务中心可读；push 目标由 Schedule 显式配置，独立于创建入口；没有企微绑定则 NONE。 | P0 | US-04 |
| FEAT-07 | 直接对话按来源入口 | WebChat/MSSWWEB 直接对话只 pull；企微直接对话只 push；不跨入口复制。 | P0 | US-05 |
| FEAT-08 | 任务中心状态 | 任务详情显示附件与 push 状态；没有 push 时明确“仅 Web 可查看”，不把“未推送”当成“不可用”。 | P0 | US-02/03/04 |
| FEAT-09 | 平台文件工具 | `write_artifact` 只写；`deliver_artifact` 把附件加入当前消息并按入口 pull/push；Agentic 与 Run 共用。 | P0 | US-01/06 |
| FEAT-10 | `MEDIA:` 兼容层 | 仅最终助手文本的独立 `MEDIA:` 行可解析为附件；结构化 attachments 与 `SKILL_OUTPUT_DIR` 是主路径；不解析工具 stdout。 | P1 | US-06 |
| FEAT-11 | 文档与模板统一 | 文档只教“写输出目录/结构化附件”；`MEDIA:` 标记为 legacy 兼容；不再要求 Skill 自己发送。 | P1 | US-01 |
| FEAT-12 | `execute` 契约修复 | `target` 必填；漏传/非法返回可行动字段错误；别名关系清楚。 | P0 | US-07 |
| FEAT-13 | `WORKSPACE_ROOT` 绝对化 | 启动时归一为绝对路径或显式失败；相对路径不得进入 `SandboxSpec`。 | P0 | US-07 |
| FEAT-14 | MSSWWEB Web 入口 | MSSWWEB 作为 Web pull 入口，与 WebChat 共享用户-Agent 的会话、任务、文件、产物；不新增 push 路由。 | P1 | US-02/06 |

#### FEAT-01 统一输出目录契约

- **描述**: `SKILL_OUTPUT_DIR` 是本次执行唯一用户产物目录；平台负责登记、挂到结果消息、按入口 pull/push。中间文件必须放 `SKILL_DATA_DIR` 或 `/tmp`。
- **验收标准**:
  - [ ] Skill 只写 `SKILL_OUTPUT_DIR`，不写包根/`/tmp`/`SKILL_DATA_DIR` 作为交付物。
  - [ ] 平台只登记成功 Skill 调用的输出；失败/超时/取消的 partial 输出不挂到结果消息。
  - [ ] 一次执行多个文件是一条结果消息的多个 attachments，不是多条文件消息。
  - [ ] 输出目录为空时不创建空消息/空附件。
  - [ ] 输出数量/大小超限显式可见，不静默少发。

#### FEAT-02 统一 Artifact 模型

- **描述**: 所有执行形态使用同一 Artifact 表和同一附件引用；一个不可变版本一行、一个 `artifact_id`；逻辑文件用 `logical_file_id + version` 关联；用户可见产物统一 `OUTPUT`。
- **验收标准**:
  - [ ] Run 内联 Skill、ASYNC Task、定时 Skill/Agentic 的产物产生同形 Artifact，owner 用 `run_id`/`task_id` 表达。
  - [ ] `artifact_id` 指向的字节/checksum/存储键永不改变；追加版本产生新的 `artifact_id`。
  - [ ] `UNIQUE (tenant_id, logical_file_id, version)`；并发追加由约束/行锁兜底。
  - [ ] `AGENT_OUTPUT`/`TASK_OUTPUT` 统一为 `OUTPUT`（开发期旧数据可清空）；`TOOL_RESULT`/`TRANSCRIPT`/入站类型不变。
  - [ ] 下载/取件按 `artifact_id + checksum` 定位不可变版本，不返回“最新版”冒充。
  - [ ] 同一执行重复登记同路径同内容复用原版本，不产生第二份。

#### FEAT-03 统一消息附件契约

- **描述**: 所有用户可见产物以“消息 + attachments”表达。消息是投递/展示单元；附件是消息内的列表；pull/push 使用同一附件引用。
- **验收标准**:
  - [ ] `MessagePayload` 至少包含 `text` 与 `attachments[]`；附件含 `artifact_id`、`filename`、`media_type`、`size`、`checksum`。
  - [ ] 多文件保持顺序；一条消息的附件列表是唯一事实，不拆成多个独立附件消息。
  - [ ] 附件版本在消息创建时冻结；后续追加版本不改变已创建消息的附件内容。
  - [ ] Web 历史/任务中心与企微 push 读取同一附件引用，不各自复制一套元数据。
  - [ ] 已显式 `deliver_artifact` 的附件不因终态自动挂载在同一执行里重复出现（按消息身份去重）。

#### FEAT-04 入口能力解析

- **描述**: 平台按用户-Agent 的可用入口与消息来源决定交付表面；功能只产出消息，不判断 pull/push。
- **验收标准**:
  - [ ] Web 入口（WebChat/MSSWWEB）存在时，任务/Web 会话产物可 pull；不存在时不产生 Web 可见性。
  - [ ] 企微绑定与有效路由存在时才 push；没有绑定不产生 push，也不报错。
  - [ ] 同一任务可同时有 Web pull 与企微 push；两者独立，不互相代替。
  - [ ] 直接对话只按来源会话：Web 来源只 pull，企微来源只 push。
  - [ ] 新增 Web 入口只接读投影；新增 push 通道只接适配器/路由，不要求业务功能改交付代码。
  - [ ] 没有可用表面时，附件保留在任务/审计记录中，不伪造已送达。

#### FEAT-05 单一 push 投递状态机

- **描述**: push 使用消息级 `message_delivery`，一条消息对一个目标一条意图；Run 与 Task 共用同一状态机与投递泵。
- **验收标准**:
  - [ ] 稳定 `message_key`（如 `run:{id}:terminal`、`execution:{id}:final`、`run:{id}:message:{message_id}`）保证重试/重放不产生第二次用户可见发送；显式重发使用新 `message_id`。
  - [ ] 只有明确送达回执才 `DELIVERED`；降级为取件链接是 `DEGRADED`；确定未发送回 `PENDING` 退避；无结论进 `UNKNOWN`，不自动重发。
  - [ ] 发送前复核路由/授权；路由撤销/删除显式 `FAILED`，不猜替代目标。
  - [ ] 多副本用租约/epoch fencing；旧 epoch 的迟到回执不得落库。
  - [ ] 终态 `FAILED` 保留消息与附件，不改写业务执行终态。
  - [ ] 显式“再发一次”创建新的消息意图，允许再次发送；自动重试复用原 key。
  - [ ] 定时任务路由永久失效时，来源 Schedule 进入 `NEEDS_ATTENTION`，不无限重试。

#### FEAT-06 定时任务双表面

- **描述**: 定时任务的结果与附件始终在 Web 任务中心可读；企微 push 是 Schedule 的显式配置，与创建入口无关。
- **验收标准**:
  - [ ] 企微创建的定时任务：企微收到结果 + 附件；同一用户-Agent 有 Web 入口时，Web 任务中心也显示。
  - [ ] WebChat 创建的定时任务可选择企微路由：Web 任务中心显示；企微收到结果 + 附件。
  - [ ] 没有企微绑定/路由时 `delivery_intent=NONE`：不产生 push，Web 任务中心仍可读。
  - [ ] 没有 Web 入口时，任务中心不可读；企微 push 仍按配置执行。
  - [ ] Schedule 的 push 目标独立于创建入口；路由必须属于同一用户-Agent 且有效。
  - [ ] 任务中心的附件按任务 owner 核属，不要求来源会话。

#### FEAT-07 直接对话按来源入口

- **描述**: 直接对话（Run）的产物只属于来源会话，不跨入口复制。
- **验收标准**:
  - [ ] WebChat/MSSWWEB 直接对话：附件进入 Web 历史；不 push 企微。
  - [ ] 企微直接对话：附件 push 到来源企微会话；不进入 Web 历史/任务中心。
  - [ ] 用户同时有两个入口时，直接对话仍按来源入口处理。
  - [ ] 直接对话的 `deliver_artifact` 即时消息也遵循同一规则。

#### FEAT-08 任务中心状态

- **描述**: 任务详情显示附件与 push 状态；没有 push 时明确是“仅 Web 可查看”，不是“不可用”。
- **验收标准**:
  - [ ] 有 Web 入口时，任务详情始终返回附件与结果；没有 push 时 `deliveries=[]` 且显示“仅 Web 可查看”。
  - [ ] 有 push 时按目标显示 `PENDING/DELIVERED/DEGRADED/UNKNOWN/FAILED`、错误码与可重试性。
  - [ ] 一个消息多个附件时，每个附件可展示该消息的 push 状态；不把消息级成功拆成互相矛盾的每文件成功。
  - [ ] 不新增独立投递管理页；状态跟随所属任务/执行记录。
  - [ ] 不显示原始 target_key/chatid；只显示渠道安全标签。
  - [ ] 投递失败不改变业务执行终态。

#### FEAT-09 平台文件工具

- **描述**: `write_artifact` 只写；`deliver_artifact` 把附件加入当前消息，并按当前执行入口 pull/push；Run 与 Agentic 共用。
- **验收标准**:
  - [ ] `write_artifact` 产出附件但不自动推送给用户；最终消息可通过终态挂载或 `deliver_artifact` 交付。
  - [ ] `deliver_artifact` 复用同一附件/消息服务；不直接调 Gateway、不复制发送逻辑。
  - [ ] Web 入口 `deliver_artifact` 写入会话历史；企微入口创建消息级 push 意图并返回状态。
  - [ ] Agentic 任务可用平台工具生成并交付测试文件。
  - [ ] 工具不可用/无可用表面时返回明确结果，不表现为“模型不会用”。

#### FEAT-10 `MEDIA:` 兼容层

- **描述**: `MEDIA:` 只作为最终助手文本的兼容语法；主契约是结构化 attachments 与 `SKILL_OUTPUT_DIR`。
- **验收标准**:
  - [ ] 只识别独立 `MEDIA:` 行，Markdown/代码块/普通行内文本不生效。
  - [ ] 只解析当前执行 workspace/输出目录内的本地路径；拒绝绝对越界、`..`、symlink 逃逸、无权限路径。
  - [ ] 无效引用产生可见的附件失败说明，不静默丢弃。
  - [ ] 不解析工具 stdout、Skill stdout、插件输出或流式块；这些必须使用结构化字段。
  - [ ] `MEDIA:` 与 `SKILL_OUTPUT_DIR`/`deliver_artifact` 指向同一文件时按附件身份去重。

#### FEAT-11 文档与模板统一

- **验收标准**:
  - [ ] 文档只教“写 `SKILL_OUTPUT_DIR` / 结构化 attachments”；平台负责 pull/push。
  - [ ] `MEDIA:` 标记为 legacy 兼容，不作为 Skill/工具契约。
  - [ ] 业务模板、`self-skill-creator`、迁移文档口径一致。

#### FEAT-12 `execute` 契约修复

- **验收标准**:
  - [ ] `target` 必填；漏传返回可行动错误（指名缺失字段），不执行任何 Skill。
  - [ ] `execute_skill` / `execute target=skill` 关系清楚；旧别名仍可用。
  - [ ] 回归覆盖 `execute`/`execute_skill`/`run_skill_script`/workspace。

#### FEAT-13 `WORKSPACE_ROOT` 绝对化

- **验收标准**:
  - [ ] 启动时归一为绝对路径或显式启动失败；相对路径不得进入 `SandboxSpec`。
  - [ ] 本地 `target=workspace` 可执行 `pwd`/写文件；生产绝对路径行为不变。
  - [ ] Runtime/Worker 使用同一 resolved root；启动日志可见。

#### FEAT-14 MSSWWEB Web 入口

- **验收标准**:
  - [ ] MSSWWEB 归入 Web pull 入口集合，不进入 push 通道集合。
  - [ ] 与 WebChat 共享同一用户-Agent 的 Web 会话、任务列表、文件、产物读投影。
  - [ ] 不新增 MSSWWEB 专用投递表/队列/页面；只新增入口路由复用现有服务。

---

### 4.2 边缘情况

| 场景 | 预期行为 |
|------|----------|
| 同一任务同时有 Web 与企微 | Web 任务中心 pull；企微 push；两者独立，互不代替 |
| 用户没有企微绑定 | 不产生 push，不报错；Web 任务中心仍可读 |
| 用户没有 Web 入口 | 不产生 pull；企微 push 仍按配置执行 |
| 企微路由创建后被撤销 | push `FAILED`；来源 Schedule `NEEDS_ATTENTION`；不猜替代会话 |
| 同一执行多文件 | 一条消息多个附件；顺序保留 |
| 用户要求再发一次 | 新消息意图/新附件事件；允许再次可见/发送 |
| 同一 tool call 重试 | 同一 message key；不重复 |
| 发送超时/无回执 | `UNKNOWN` 待核实；不自动重发 |
| 渠道不支持直发 | `DEGRADED` + 取件链接；不标 `DELIVERED` |
| 失败/取消的执行 | 不挂载半成品；附件可留作诊断 |
| 输出目录超限 | 显式失败/可见原因；不静默少发 |
| 附件在 push 前被清理 | 禁止：pending 消息引用的附件受保留保护 |
| 直接对话跨入口 | 不复制；Web 来源只 Web，企微来源只企微 |
| MSSWWEB 与 WebChat | 同一 Web 数据；不重复、不新建投递 |

---

## 5. 非功能需求

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-REL-01 | 可靠性 | 同一条消息意图 + target 至多一次用户可见发送；自动重试/崩溃恢复复用同一 key；显式重发是新消息 |
| NFR-REL-02 | 可恢复性 | push 意图持久化；进程崩溃后由投递泵继续；pull 由 canonical 历史重建 |
| NFR-SEC-01 | 安全 | Artifact/Attachment 按 tenant/owner/agent 核属；下载/交付不跨租户；`MEDIA:` 路径受 workspace 限制 |
| NFR-OBS-01 | 可观测性 | 每次 push 有状态、尝试、错误码、trace；UNKNOWN 可核实；指标按 channel/status，不用 target_key 高基数标签 |
| NFR-PERF-01 | 性能 | pull 不新增投递/轮询；push 异步，不阻塞终态；附件数量/大小受平台预算 |
| NFR-CAP-01 | 容量 | 附件列表有界；pending 消息保护附件不被清理；失败只保留引用/摘要 |

---

## 6. 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | 消息附件契约；Artifact 不可变版本；Web pull（WebChat/MSSWWEB）与企微 push 分流；单一消息级 push 状态机；定时任务 Web+push 双表面；直接对话按来源；`SKILL_OUTPUT_DIR` 自动挂载；`write_artifact`/`deliver_artifact`；任务中心状态；`MEDIA:` 兼容层；`execute`/`WORKSPACE_ROOT` 修复 |
| **非范围（Out of Scope）** | Web 收件箱/未读/通知读取的 UX 重构（保留现有）；入站附件链路；独立投递管理页/全局看板；新增 push 通道（本期只 WECOM）；`big-output` 演示；调度配额；历史数据回填（开发期可清空）；Console 大规模改版。文本终态/人工问题作为 `message_delivery` 的 message kind 进入同一 push 状态机，但 Web 收件箱读取不改 |
| **前置假设** | Artifact Store 可用；Runtime/Worker/Gateway 同一租户；Web 入口可读 canonical 历史/任务中心；企微路由可解析；当前是开发节点，旧产物/旧投递数据可清空 |
| **对外契约约束** | Web 历史/任务中心/下载的既有路径与字段不变；只新增附件/状态附加字段；MSSWWEB 复用同一读契约；企微适配器只新增多附件能力，不把渠道形状带回核心域 |

---

## 7. 依赖与风险

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 风险 |
|--------|----------|------|
| Runtime | Artifact 版本、消息附件、`message_delivery`、投递泵、Web 发布 | 高 |
| Worker | Task 输出登记、结果附件、`task.message_outbox`、任务中心读投影 | 高 |
| Gateway | 多附件 `DeliveryMessage`、企微发送、回执/降级 | 中 |
| Database | Artifact 版本列/约束、`message_delivery`、outbox、旧表清理 | 中 |
| Console/Web | 任务中心附件与 push 状态、Schedule 路由选择 | 低 |

### 7.2 风险

| 风险ID | 描述 | 影响 | 缓解 |
|--------|------|------|------|
| R-01 | 把 pull 当 push，给 Web 建投递意图 | 多一套泵/重复状态 | 明确 Web 无投递意图；只读写投影 |
| R-02 | 每个附件一条消息 | 多文件变成多条消息、重复状态 | 消息级附件列表；一条消息一个 push 意图 |
| R-03 | 定时任务 push 仍绑定创建入口 | Web 创建的任务无法推企微 | Schedule 路由独立；Spec Delta |
| R-04 | 无绑定/无入口被当失败 | 误报、任务失败 | 能力缺失是正常 NONE；只有显式配置失效才 FAILED |
| R-05 | `artifact_id` 可变 | 附件版本被后续追加顶替 | 版本即行；消息冻结版本/checksum |
| R-06 | 附件在 push 前被清理 | 发送失败/死链 | pending 消息保护附件 |
| R-07 | `MEDIA:` 解析过宽 | 安全/误投递 | 仅最终助手文本、独立行、workspace 路径、无效可见 |
| R-08 | 两个 push 状态机继续存在 | 底层不一致 | 单一 `message_delivery`；删除旧 push 分支 |

---

## 8. Existing Spec Constraints

| Spec/Rule | 约束 | 影响 |
|-----------|------|------|
| `worker/harness-worker#RULE-003` | 投递意图与业务状态分离；稳定键、持久尝试、发送租约；只有明确送达才成功 | push 状态机与幂等 |
| `im/harness-im#RULE-im-002` | 渠道差异只在适配器层 | 消息/附件契约渠道中立 |
| `im/harness-im#RULE-im-005` | 会话删除后列表/详情/消息/下载一致不可见 | Web pull 可见性 |
| `im/harness-im#RULE-006` | 任务产物按执行 owner 核属，指定不可变版本/checksum | 任务中心下载 |
| `artifact/harness-skill#RULE-skill-001` | Artifact Store RWX；相对 storage_key；不可变写入；禁止 NFS 执行 | Artifact 版本 |
| `api/harness-api#RULE-api-002` | 创建/上传类 POST 幂等；重放首次结果 | 消息/附件登记 |
| `secret/harness-secret#RULE-secret-001` | 密钥不得进入日志/审计/响应 | 附件/日志脱敏 |
| `test/harness-test#RULE-test-001` | 跨 API/DB/Runtime 关键流程真实集成测试 | pull/push/恢复 |
| `persistence/harness-data#RULE-001` | Runtime 持有产物写入权；Worker 经内部 API；跨 Owner 只读 | Artifact/message_delivery owner |
| `frontend/harness-frontend` | 请求只走共享 client/service | 任务中心状态 |
| `ui/harness-ui` | 复用现有列表/标签 | 不新增页面 |
| `i18n/harness-i18n` | 文案只走词条 | 状态/标签 |

---

## 9. 开放问题

| 编号 | 问题 | 结论 |
|------|------|------|
| OQ-01 | 文本终态/人工问题是否也并入消息投递？ | **是**：push 状态机按“消息”统一，文本终态/人工问题作为 `message_delivery` 的 message kind；Web 收件箱/未读读取不变 |
| OQ-02 | 已交付后用户要求再发一次？ | 允许；新消息意图/新附件事件；自动重试仍用原 key |
| OQ-03 | 多文件一条消息还是多条？ | 一条消息多个附件；通道适配器决定批量/逐条发送 |
| OQ-04 | 旧数据迁移 | 开发期清空旧 Artifact/投递数据；不做回填/双读 |
| OQ-05 | `MEDIA:` 是否保留？ | 保留为 P1 最终助手文本兼容；结构化附件/`SKILL_OUTPUT_DIR` 是主路径 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| Artifact | 一次写入产生的不可变文件版本；一个版本一行、一个 `artifact_id` |
| 逻辑文件 | 同一 owner + 产出身份 + 规范路径下的版本集合；`logical_file_id` 关联 |
| AttachmentRef | 消息里的附件引用：artifact_id、文件名、媒体类型、大小、checksum |
| MessagePayload | 正文 + attachments；pull/push 的唯一交付单元 |
| Pull | WebChat/MSSWWEB 读 canonical 历史/任务中心；没有投递意图 |
| Push | 企微目标的消息级投递意图；只有 WECOM |
| MessageDelivery | “某条消息要投递到某个 push 目标”的持久意图与状态 |
| UNKNOWN | 已发出但没拿到结论：不自动重发、等待核实 |
| Surface | 用户-Agent 可用的入口能力：Web pull / 企微 push；可以同时存在或都不存在 |

---

*文档结束*
