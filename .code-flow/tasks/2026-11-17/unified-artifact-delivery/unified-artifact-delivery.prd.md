# PRD: 平台统一文件产物投递

> **文档编号**: PRD-2026-11-17-01
> **文档版本**: v1.0
> **创建日期**: 2026-11-17
> **产品负责人**:
> **状态**: 草稿

**评审边界说明**:
- **需求基线**: 第 2-8 章（背景/用户/功能/非功能/范围/依赖/已有规范约束）→ 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、R-（风险）、OQ-（开放问题）、Spec/Rule（已有规范引用）

---

## 2. 背景与目标

### 2.1 问题陈述 [必填]

| 维度 | 内容 |
|------|------|
| **问题描述** | 文件产物目前没有统一为平台能力：Skill 作者被要求在 `SKILL_OUTPUT_DIR` 落盘，但“落盘/登记”和“投递到用户”是两件事；后台 ASYNC、DETACH、定时任务没有模型在环时无法可靠调用 `deliver_artifact`；不同执行路径的交付状态、重试和幂等各自实现，存在重复投递风险。 |
| **当前替代方案** | 有的路径依赖模型主动调用 `deliver_artifact`，有的依赖 Worker 的 `result_artifact_id` + `task_delivery`，有的只在 Runtime 内联 Skill 登记产物；文档和模板只写了“放到输出目录”，没有说明最终如何交付。 |
| **触发原因** | 定时任务测试中误用 `big-output`（只打印 20KB JSON，不写文件），模型调用 `execute` 又漏了 `target`；同时本地 `WORKSPACE_ROOT` 为相对路径触发沙箱错误。问题暴露出：文件生成/投递不应由某个 Skill 或模型记忆承担，必须是平台能力。 |

### 2.2 目标与价值 [必填]

| 维度 | 内容 |
|------|------|
| **核心目标** | 建立全平台唯一的文件产物登记与投递链路：Skill、Agentic、Run、Task、定时任务都通过同一套 Artifact + DeliveryIntent + DeliveryPump 交付文件；会话与外部通道共用同一消息通道；同时修复 `execute` 工具契约、`WORKSPACE_ROOT` 绝对化和 `AGENT_OUTPUT`/`TASK_OUTPUT` 类型统一。 |
| **预期价值** | 文件输出不再依赖具体 Skill 或模型是否记得调用工具；同一产物同一目标只投递一次；后台/定时任务也能稳定交付；文档、模板和实现只有一套口径。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 确认成功的重复投递 | 多路径各自实现，无法统一衡量 | 0 次重复用户可见投递 | 同一不可变版本 + target 的 delivery key 唯一；重复请求返回首次结果 |
| 发送结果未知的处置 | 超时/无回执被当成失败重发，用户收到两份 | 100% 进入待核实、不自动重发 | 未知态独立记账并可观测 |
| 无模型在环的文件交付 | ASYNC DETACH/定时 SKILL 不可靠 | 100% 由平台完成登记与投递 | 集成测试覆盖 DETACH、定时 SKILL、多文件 |
| 文件交付路径 | Runtime inline / Worker task / 模型工具多套 | 1 套 DeliveryIntent + DeliveryPump | 代码与测试只保留一个投递状态机 |
| 站内与外部通道 | 站内走 Runtime 内部发布，外部走 Gateway | 1 套消息通道 | 会话产物与外部产物共用同一投递接口 |

---

## 3. 用户与场景

### 3.1 目标用户 [必填]

| 维度 | 内容 |
|------|------|
| **用户画像** | 业务 Skill 作者、平台最终用户、任务或 Skill 执行记录的查看者。当前规模约 200 人，后台任务会排队。 |
| **使用场景** | Skill 生成报告/图片/JSON；Agentic 任务生成临时文件；WebChat 用户接收站内文件；企业微信等外部通道接收附件或降级取件链接；定时任务周期生成文件并投递。 |

### 3.2 用户故事 [必填]

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为业务 Skill 作者，我希望只把交付物写进平台输出目录，不需要实现投递逻辑，以便 Skill 可独立测试和复用。 | P0 |
| US-02 | 作为最终用户，我希望文件在 WebChat 或外部通道只出现一次，不因重试、刷新或重复调用收到多份。 | P0 |
| US-03 | 作为后台任务使用者，我希望 ASYNC/DETACH/定时任务完成后自动收到文件，不要求模型在后台继续调用工具。 | P0 |
| US-04 | 作为任务或 Skill 执行记录的查看者，我希望在该执行记录上看到所属文件的投递状态与失败原因，不需要单独的投递管理页面。 | P0 |
| US-05 | 作为 Agentic 任务使用者，我希望模型能在执行中生成文件并交付，且与普通 Run 使用同一套能力。 | P1 |
| US-06 | 作为平台/Agent 使用者，我希望 `execute` 工具契约明确、`WORKSPACE_ROOT` 在启动时归一为绝对路径，避免平台能力被参数歧义或本地配置阻断。 | P0 |

---

## 4. 功能需求

### 4.1 功能清单 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|--------|---------|---------|--------|-------------|
| FEAT-01 | 统一输出目录契约 | `SKILL_OUTPUT_DIR` 按现有设计就是用户产物交付目录：平台负责登记并投递其中全部合法文件，不引入 `deliverables` 白名单；中间文件不得放入。 | P0 | US-01 |
| FEAT-02 | 统一 Artifact 模型 | Skill、Agentic、Run、Task 的产物统一登记为 Artifact；`AGENT_OUTPUT` / `TASK_OUTPUT` 统一为 `OUTPUT`；每个不可变版本一个 `artifact_id`，具备 owner、租户、文件名、大小、checksum、版本。 | P0 | US-01/03/05 |
| FEAT-03 | 统一 DeliveryIntent | 每次“产物 → 目标”生成一条持久 DeliveryIntent，使用稳定 delivery key；Runtime/Task/Gateway 共用状态机。 | P0 | US-02/04 |
| FEAT-04 | 统一 DeliveryPump | 由单一投递泵消费 DeliveryIntent：站内写会话发布事实，外部走 Gateway；支持重试、降级、失败终态。 | P0 | US-02/03/04 |
| FEAT-05 | 已投递不可重复 | 同一 artifact + target 已 `DELIVERED` 时，重复请求/重试/并发只返回首次结果，不再发送第二份。 | P0 | US-02 |
| FEAT-06 | 后台/定时自动交付 | Run 侧 ASYNC 后台任务（DETACH）、任务内部 JOIN、定时 SKILL、定时 AGENTIC 的产物由平台自动登记和投递，不依赖模型在环。 | P0 | US-03 |
| FEAT-07 | 平台内置文件工具 | `write_artifact`、`deliver_artifact` 作为平台工具在 Run 与 Agentic 执行环境可用；与 Skill 输出目录共用同一 Artifact/Delivery 服务。 | P0 | US-05 |
| FEAT-08 | 交付状态随执行记录 | 交付状态 `PENDING/DELIVERED/DEGRADED/FAILED` 跟随所属 Run/Task/Skill 执行记录与工具结果；不新增独立投递管理页。 | P0 | US-04 |
| FEAT-09 | 文档与模板统一 | 模板、self-skill-creator、迁移文档只保留一套“写输出目录 → 平台登记 → 平台投递”口径；移除 `MEDIA:` 和 Skill 内投递说明。 | P1 | US-01 |
| FEAT-10 | 旧 Skill 迁移 | 旧 `MEDIA:`、写包根/`/tmp`、模型依赖 `deliver_artifact` 的 Skill 有明确迁移规则与检查项。 | P1 | US-01 |
| FEAT-11 | `execute` 工具契约修复 | `execute` 缺少 `target` 时不再表现为通用 `invalid tool arguments`；工具 schema/描述/别名给出明确 target 或可行动错误。 | P0 | US-06 |
| FEAT-12 | `WORKSPACE_ROOT` 绝对化 | 启动时把 `WORKSPACE_ROOT` 归一为绝对路径，或对相对路径显式启动失败；相对路径不得进入 `SandboxSpec`。 | P0 | US-06 |

#### FEAT-01: 统一输出目录契约

- **描述**: `SKILL_OUTPUT_DIR` 按现有设计就是本次执行的用户产物交付目录；`SKILL_DATA_DIR` 只放状态/缓存；`/tmp` 只放临时文件。平台登记并投递输出目录中的全部合法文件，不要求 `deliverables` 白名单。
- **验收标准**:
  - [ ] Skill 只写 `SKILL_OUTPUT_DIR`，不写包根、`/tmp`、`SKILL_DATA_DIR` 作为交付物。
  - [ ] 平台扫描该目录并登记全部合法文件，全部作为用户交付物投递。
  - [ ] 不引入 `deliverables` 白名单；中间文件必须放 `SKILL_DATA_DIR` 或 `/tmp`。
  - [ ] 文档明确“写入输出目录 = 用户交付物”，不需要 Skill 自己发送。

#### FEAT-02: 统一 Artifact 模型

- **描述**: 所有执行形态使用同一 Artifact 服务与同一输出产物模型登记产物；`AGENT_OUTPUT` 与 `TASK_OUTPUT` 只做类型统一（`OUTPUT`），不能按来源维护两套交付语义；版本不可变，追加版本不改旧版本。
- **验收标准**:
  - [ ] Run 内联 Skill、Worker ASYNC Skill、定时 Skill、Agentic 产物都能产生同形 Artifact。
  - [ ] `AGENT_OUTPUT` / `TASK_OUTPUT` 合并为统一输出产物；owner 用 `RUN`/`TASK` 显式表达，入站附件、`TRANSCRIPT` 等内部/入站类型不变。
  - [ ] Artifact 记录 owner、租户、文件名、media type、size、checksum 与不可变版本；**每个不可变版本一个 `artifact_id`**，追加版本不改动旧版本的字节、checksum 与存储键。
  - [ ] 同一逻辑文件用稳定身份（owner + 产出调用身份 + 规范路径）关联各版本；同一执行重复登记同路径同内容复用原版本，不产生第二份；并发登记由数据库约束兜底。
  - [ ] `AGENT_OUTPUT` / `TASK_OUTPUT` 只做类型统一（单条 UPDATE）；开发期旧数据可直接清空，不做历史回填、不保留双读兼容分支。

#### FEAT-03: 统一 DeliveryIntent

- **描述**: 由平台创建持久投递意图，而不是模型或 Skill 直接调用通道。
- **验收标准**:
  - [ ] 一条 artifact 版本对一个目标只有一条有效 DeliveryIntent。
  - [ ] delivery key 稳定且唯一，覆盖 Run、Task、定时任务；键 = tenant + 不可变 artifact_id + target，追加版本天然是新键。
  - [ ] 执行期间只登记候选产物；成功终态 CAS 的同一事务写最终文件清单与投递 outbox，Runtime 幂等消费后原子确认产物并创建意图，不出现“有文件无投递意图”。
  - [ ] 取消、失败、失约的 attempt 不激活自动交付。

#### FEAT-04: 统一 DeliveryPump

- **描述**: 一个投递泵消费 DeliveryIntent，按路由分发：会话与外部通道走同一条消息通道。
- **验收标准**:
  - [ ] 会话产物与外部通道共用同一消息通道：目标是一类路由（含 WebChat 会话），不保留 Runtime 侧的站内专属发布路径。
  - [ ] 会话面向浏览器的接口保持不变；新通道只新增适配器（实现产物出站），不新增或改写在用的浏览器接口。
  - [ ] 只有明确送达回执才标记 `DELIVERED`；降级返回 `DEGRADED` 与取件链接，且取件链接绑定不可变版本（追加版本后旧链接内容不变）。
  - [ ] 明确区分“确定未发送”和“发送结果未知”：前者回 `PENDING` 按退避重试，后者进入 `UNKNOWN` 待核实、不自动重发。
  - [ ] 后台、DETACH、定时任务的文件交付使用**主动发送**，不查找或借用入站回调/最新 reply_ref。
  - [ ] 发送前复核授权与连接所有权：路由撤销直接失败；连接暂不可用且确定未发送时允许重试。
  - [ ] 终态失败 `FAILED` 保留产物；文件只有一个发送者，Worker 不再有文件发送分支。
  - [ ] 多副本投递使用租约/fencing，不并发重复发送。

#### FEAT-05: 已投递不可重复

- **描述**: 已成功投递的 artifact + target 不再发送第二份。
- **验收标准**:
  - [ ] 重复 `deliver_artifact` 调用返回首次状态，不产生新的用户可见文件。
  - [ ] 同一文件分别用 `artifact_id` 和 `path` 引用时，仍按 artifact + target 去重，不生成第二张卡/第二条发送。
  - [ ] 同一 delivery key 的并发请求最多一次真实发送。
  - [ ] 进程崩溃或超时发生在“已发送、未标记”之间时进入 `UNKNOWN` 待核实，不盲目重发，也不补发取件链接。
  - [ ] 已确认成功的发送永久约束该业务键；新租约/新 epoch 不构成重新发送的理由。
  - [ ] 同一 artifact 发往不同目标时，按目标各一条意图，互不误判。

#### FEAT-06: 后台/定时自动交付

- **描述**: 后台执行没有模型时也能完成文件交付。
- **验收标准**:
  - [ ] Run 侧 ASYNC 后台任务、任务内部 JOIN、定时 SKILL 均能自动交付输出目录文件。
  - [ ] 不需要模型调用 `deliver_artifact`。
  - [ ] 没有交付路由时显式失败，不静默丢失文件。
  - [ ] 只有成功终态提交后才激活自动交付；执行失败/取消/失约时不投递半成品，候选产物不激活。
  - [ ] 重试产生同路径不同内容时产生新的不可变版本（新 `artifact_id`）；旧版本不重发，新版本按新键投递。

#### FEAT-07: 平台内置文件工具

- **描述**: 文件生成/交付是平台能力，不依赖某个业务 Skill。
- **验收标准**:
  - [ ] Agentic 执行环境注册 `write_artifact` / `deliver_artifact`，与 Run 使用同一后端。
  - [ ] 定时 AGENTIC 可以只靠平台工具生成并交付测试文件，不依赖 `big-output` 等演示 Skill。
  - [ ] 工具不可用时返回明确错误，不表现为“模型不会用”。

#### FEAT-08: 交付状态随执行记录

- **描述**: 状态在模型工具结果与所属 Run/Task/Skill 执行记录中可见；产物归属和执行记录绑定，不做跨任务统一管理面。
- **验收标准**:
  - [ ] `deliver_artifact` 返回 `DELIVERED/DEGRADED/PENDING/UNKNOWN/FAILED` 之一。
  - [ ] 一个文件发往多个目标时返回 `deliveries[]`：目标、状态、错误与可重试性分别给出；一个目标成功、另一个失败分别展示。
  - [ ] 任务/执行记录能查看所属文件及交付状态；未请求推送的文件显示“未推送”（可见即可下载），不把记录可见当成已送达。
  - [ ] 任务详情只新增 `deliveries[]` 附加字段；既有字段、下载契约与路径不变。
  - [ ] 失败原因与可重试性可见。
  - [ ] 投递失败不改变业务执行终态；任务可以 `COMPLETED` 而文件交付为 `FAILED/PENDING`。
  - [ ] 不新增独立投递跟踪/管理页面，也不要求跨任务全局检索投递。

#### FEAT-09: 文档与模板统一

- **描述**: 文档只教一套规则。
- **验收标准**:
  - [ ] `skill-output-dir.md`、业务模板、self-skill-creator、迁移文档口径一致。
  - [ ] 明确“落盘 ≠ 交付”，但交付由平台完成，不由 Skill/模型补调。
  - [ ] `MEDIA:` 被标记为不支持。

#### FEAT-10: 旧 Skill 迁移

- **描述**: 旧 Skill 有明确迁移路径。
- **验收标准**:
  - [ ] 扫描出使用 `MEDIA:`、写包根/`/tmp`、依赖模型 `deliver_artifact` 的 Skill。
  - [ ] 迁移后只写 `SKILL_OUTPUT_DIR`，不改变业务逻辑。
  - [ ] `longTask` → `execution: ASYNC` 与文件交付是两件独立的事，分别迁移。

#### FEAT-11: `execute` 工具契约修复

- **描述**: `execute` 的 `target` 是必填判别字段；模型漏传时不能只得到笼统的 `invalid tool arguments`。
- **验收标准**:
  - [ ] 工具 schema/描述明确 `target` 必填，且 `execute_skill` 别名与 `execute target=skill` 的关系清楚。
  - [ ] 漏传 `target` 返回可行动错误（指名缺失字段），不再表现为通用 `SCHEMA_INVALID`。
  - [ ] 用 `execute_skill` 或 `execute(target=skill)` 调用 `big-output` 等已有 Skill 不再出现该错误。
  - [ ] 增加模型工具契约回归测试，覆盖 `execute`/`execute_skill`/`run_skill_script`/`workspace` 的必填字段。

#### FEAT-12: `WORKSPACE_ROOT` 绝对化

- **描述**: `WORKSPACE_ROOT` 是宿主绝对路径；相对路径不得流入 `SandboxSpec`。
- **验收标准**:
  - [ ] 启动时把 `WORKSPACE_ROOT` 归一为绝对路径，或对相对路径显式启动失败并给出明确原因。
  - [ ] `target=workspace` 在本地 dev 环境可正常执行 `pwd`/写文件，不再报 `workspace_dir must be absolute`。
  - [ ] 生产 `/mnt/muad-workspaces` 行为不变；sandbox 内 cwd 仍限制在 `/workspace`。
  - [ ] 增加配置校验/路径归一回归测试，覆盖相对路径与绝对路径。

---

### 4.2 边缘情况 [可选]

| 场景 | 预期行为 |
|------|----------|
| 同一产物发往多个目标 | 每个目标一条 DeliveryIntent；每个目标最多一次。 |
| 同一 artifact 有新版本 | 新版本是新的不可变 `artifact_id`；已送达的旧版本记录与取件链接保持不变，不得把新内容当旧版本交付。 |
| 执行失败/取消 | 不自动投递半成品；文件可保留用于诊断，但不得当成功交付。 |
| 重试产生同路径不同内容 | 新 checksum/新版本；旧版本不重发，最终只交付最终版本（或按明确策略）。 |
| 同一文件用 artifact_id 与 path 两个入口引用 | 按 artifact + target 去重，不重复发送。 |
| 交互式 `deliver_artifact` 无交付路由 | 显式失败，不猜替代目标、不标记已交付。 |
| 渠道不支持直发文件 | `DEGRADED` + 取件链接；不得标记 `DELIVERED`。 |
| 目标路由被撤销/删除 | 显式 `FAILED`，不猜替代目标。 |
| 多文件部分失败 | 每个 artifact 独立状态；已成功的不重发，失败的重试。 |
| 发送后进程崩溃、未标记 | 依靠 delivery key/持久回执/租约 fencing 确认；结果未知不自动重发，进入待核实。 |
| 发送超时、拿不到回执 | 记 `UNKNOWN` 待核实；不立即重发文件，也不补发取件链接。 |
| Run 终态后产物才发布到会话 | 由统一消息通道送达（会话通知/历史可见），不依赖 Run 流生命周期。 |
| 成功终态提交后、激活前崩溃 | 终态事务内的 outbox 可重放；不漏投，也不投半成品。 |
| 输出目录为空 | 不创建空交付；明确记录无产物。 |
| 输出文件超限/类型不支持 | 显式拒绝或标记失败，不静默丢弃。 |

---

## 5. 非功能需求 [可选]

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-REL-01 | 可靠性 | 已确认送达的版本不重复发送；发送结果未知时停止自动重发并显式待核实——渠道不提供发送幂等/回执查询时，不承诺所有异常下的绝对零重复。 |
| NFR-REL-02 | 可恢复性 | 投递意图持久化；进程崩溃后可继续，不依赖内存或模型。 |
| NFR-SEC-01 | 安全 | Artifact 与 DeliveryIntent 按 tenant/owner/agent 核属；下载/交付不得跨租户或越权。 |
| NFR-OBS-01 | 可观测性 | 每次投递有状态、尝试次数、错误码、trace/audit，并跟随所属执行记录；每次尝试与回执独立记账，未知结果可被人工核实；重复请求有明确重放标记；不要求全局投递管理面。 |
| NFR-PERF-01 | 性能 | 投递异步化，不阻塞 Skill/Task 终态；文件大小与数量受平台预算限制。 |

---

## 6. 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | 文件/产物的生成、登记、投递、去重、状态与审计，且状态/审计跟随所属 Run/Task/Skill 执行记录；Run/Task/Skill/Agentic/定时任务；站内 WebChat 与外部通道共用同一消息通道；主动发送（后台/DETACH/定时）；版本化取件链接；`write_artifact`/`deliver_artifact` 与 `SKILL_OUTPUT_DIR`；全场景覆盖（SYNC/Run 侧 ASYNC 后台任务/任务内部 JOIN/定时/多文件/降级/失败重试）；`AGENT_OUTPUT`/`TASK_OUTPUT` 类型统一（开发期旧数据可清空）；`execute` 工具契约修复；`WORKSPACE_ROOT` 启动绝对化 |
| **非范围（Out of Scope）** | 文本终态、Human 问题、通知未读等非文件投递；MCP/Skill 业务执行逻辑；模型能力；独立投递跟踪/管理页面、跨任务全局投递看板或手工重试管理；前端任务中心大规模改版；`big-output` 演示 Skill、2 分钟调度配额、`MEDIA:`/`muad-progress`/longTask 迁移、session-manager、入站附件、产物保留期/清理 |
| **前置假设** | Artifact Store 可用；Runtime/Worker/Gateway 能访问同一租户与交付路由；平台设置可冻结/读取投递预算；现有 `task_delivery` 文本投递不被本需求破坏；已确认 `SKILL_OUTPUT_DIR` 中全部合法文件都是用户产物（无白名单），中间文件必须放 `SKILL_DATA_DIR` 或 `/tmp`；当前是开发节点，旧产物数据可直接清空，不承担历史回填与旧投递回执承接 |
| **对外契约约束** | 会话面面向浏览器的后台接口（会话历史、Run SSE、通知流、产物下载、任务中心详情）保持路径与语义不变；任务详情只允许新增 `deliveries[]` 这类附加字段。新通道（含未来 IM 通道）只能作为投递目标接入，不得要求会话面新增或改写接口 |

> 本次“统一投递”聚焦文件/产物。文本终态、Human 问题是否也并入同一 DeliveryPump，作为 OQ-01 由用户确认；未确认前不纳入验收。

### 6.1 相关但不纳入本需求验收

| 场景 | 为什么不作为本需求核心 | 处理建议 |
|------|------------------------|----------|
| `big-output` 大结果外置 | 它是工具结果外置演示，不是文件生成/投递场景 | 不作为验收对象；测试文件改用平台文件能力或真实文件 Skill |
| 2 分钟定时触发与每日 Fire 配额 | 调度配额问题，不是文件投递 | 调整间隔或配额，单独验收 |
| `MEDIA:`、`muad-progress`、旧 `longTask` | 旧 Skill 格式/执行模式迁移问题 | 作为独立迁移任务，不阻塞投递核心 |
| session-manager 在 workspace_shell 的注入 | 平台登录态能力，不是文件投递 | 按既有平台会话能力单独验证 |
| Console 独立投递跟踪页/全局看板 | 明确非目标；状态跟随执行记录即可 | 不建设 |
| 文本终态、Human 问题、通知未读 | 非文件投递；是否统一由 OQ-01 决定 | 未确认前保持现状 |
| 入站附件 | 反方向链路，已有独立契约 | 不在本需求内改动 |
| 产物保留期、清理策略、下载页面改版 | 生命周期/展示能力，不是投递正确性 | 保持现状，除非导致交付链接失效 |

> `execute` 缺 `target`、`WORKSPACE_ROOT` 相对路径、`AGENT_OUTPUT`/`TASK_OUTPUT` 类型统一已纳入本需求 FEAT-11 / FEAT-12 / FEAT-02，不再作为旁支处理。

---

## 7. 依赖与风险 [可选]

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|--------|----------|--------------|---------|
| Runtime Artifact/Delivery | Artifact 登记、DeliveryIntent、DeliveryPump、会话通道投递 | 设计确认后 | 高 |
| Worker | ASYNC Skill 输出登记、任务终态触发、多产物支持 | 设计确认后 | 高 |
| Gateway | `/internal/deliveries` 幂等/回执/降级；多产物能力 | 设计确认后 | 中 |
| Database migration | 新增 `artifact_delivery`/attempt 表；`AGENT_OUTPUT`/`TASK_OUTPUT` 类型统一（开发期旧数据可清空，不做回填） | 设计确认后 | 低 |
| Console/任务中心 | 仅在既有执行/任务详情中展示所属文件与状态；不新增独立投递管理页面 | 设计确认后 | 低 |

### 7.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|----------|
| R-01 | 版本即行改动触及下载、取件与 Gateway 校验 | 旧版本定位口径不一致，历史文件读不到 | 统一“`artifact_id` ⇒ 不可变字节”一个口径；开发期可清空旧数据；改动点逐处登记 |
| R-02 | 发送超时/无回执后重复发送 | 用户收到两份文件 | 区分“确定未发送”与“结果未知”；`UNKNOWN` 待核实 + 持久 attempt 回执；集成测试覆盖崩溃与超时窗口 |
| R-03 | 多文件/大文件/渠道能力差异 | 部分失败、降级不一致 | 每 artifact 独立意图；平台统一大小/类型预算；DEGRADED 显式状态 |
| R-04 | 输出目录被中间文件污染 | 误投递垃圾文件 | 文档与模板明确 `SKILL_OUTPUT_DIR` 只放交付物；`SKILL_DATA_DIR`/`/tmp` 放中间文件 |
| R-05 | 旧 Skill 依赖 `deliver_artifact` 或 `MEDIA:` | 迁移后不交付 | 迁移清单 + 文档 + 回归测试；不保留旧模式 |
| R-06 | 会话产物改走统一消息通道需要 WebChat 通道实现产物出站 | 会话收不到文件或投递报“本通道不会发产物” | 在通道适配器内实现产物出站与会话发布事实；未装配即显式失败，不静默降级 |

---

## 8. Existing Spec Constraints

| Spec/Rule | 约束 | 对范围/验收的影响 | 状态 |
|-----------|------|------------------|------|
| `im/harness-im#RULE-im-002` | 渠道差异只在适配器层，核心域不得出现渠道私有形状 | FEAT-04/07 的投递契约与 ArtifactRef 必须渠道中立 | applied |
| `im/harness-im#RULE-im-005` | 会话删除软删后列表/详情/消息/下载一致不可见 | FEAT-04/08 的会话产物投递与下载需遵守会话可见性 | applied |
| `im/harness-im#RULE-006` | 任务产物按执行 owner 核属，指定不可变版本/checksum；未授权/软删不暴露 | FEAT-02/08 的 Artifact 归属与下载验收 | applied |
| `worker/harness-worker#RULE-003` | 投递意图与业务状态分离；稳定键、持久尝试、发送租约；只有明确送达才成功 | FEAT-03/04/05 的状态机与幂等验收 | applied |
| `artifact/harness-skill#RULE-skill-001` | Artifact Store RWX；相对 storage_key；不可变写入；禁止从 NFS 执行 | FEAT-02 的存储与版本验收 | applied |
| `api/harness-api#RULE-api-002` | 创建/上传类 POST 幂等；指纹含 endpoint/tenant；重放首次结果 | FEAT-03/05 的幂等键与重放验收 | applied |
| `secret/harness-secret#RULE-secret-001` | 密钥不得进入日志、审计、Snapshot、Prompt、API 响应 | 文件内容/元数据不得泄露凭据；交付日志脱敏 | applied |
| `test/harness-test#RULE-test-001` | 跨 API/DB/Runtime 关键流程必须有自动化集成/契约测试；真实 PG/Redis/HTTP | FEAT-05/06 的重复投递与后台交付验收 | applied |
| `persistence/harness-data#RULE-001` | 共享执行基础设施显式 owner 联合与互斥归属；Runtime 持有产物写入权，Worker 经内部 API | FEAT-02/03 的 Artifact/DeliveryIntent owner 设计 | applied |

---

## 9. 开放问题

| 编号 | 问题 | 影响 |
|------|------|------|
| OQ-01 | “投递统一”是否只覆盖文件/产物，还是文本终态、Human 问题、通知未读也并入同一 DeliveryPump？ | 决定范围与迁移成本 |
| OQ-02 | 已交付后用户要求“再发一次”是否允许？如果允许，是新建投递动作还是复用原 artifact 但新 key？ | 影响“不可重复投递”的边界 |
| OQ-03 | 多文件是一个消息多个附件，还是每个文件一条消息？ | 影响 Gateway/DeliveryMessage 契约 |
| OQ-04 | ~~合并的迁移窗口与回填策略~~ **已定**：当前是开发节点，旧数据可清除，不做回填/回滚兼容，只保留类型统一与新增结构 | — |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| Artifact | 一次写入产生的不可变文件事实；**一个版本一行、一个 `artifact_id`**，含 owner、storage_key、checksum、媒体类型 |
| 逻辑文件 | 同一 owner + 产出调用身份 + 规范路径下的版本集合；不是投递或下载的身份 |
| DeliveryIntent | “某个 Artifact 版本要投递到某个目标”的持久意图与状态 |
| DeliveryPump | 消费 DeliveryIntent、经统一消息通道投递的执行器；会话与外部通道同一条路径 |
| Target | 渠道中立的一条路由（含会话身份）；由来源 Run/任务冻结路由解析 |
| UNKNOWN | 已发出但没拿到结论：不自动重发、不补发链接，等待核实 |
| Idempotent delivery | 同一不可变版本 + target 的重复请求不产生第二次用户可见发送 |

---

*文档结束*
