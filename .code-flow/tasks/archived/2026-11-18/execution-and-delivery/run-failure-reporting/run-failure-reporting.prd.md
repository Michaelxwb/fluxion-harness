# PRD: 执行失败收尾与定时任务执行模型

> **文档编号**: PRD-2026-11-18-01
> **文档版本**: v0.6
> **创建日期**: 2026-11-18
> **产品负责人**: fluxion-harness
> **状态**: 草稿 / 评审中

**评审边界说明**:
- **需求基线**: 第 2-8 章 → 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs
- **设计文档**: `run-failure-reporting.design.md`

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | fluxion-harness | 需求定义、业务验收 |
| 开发负责人 | fluxion-harness | 技术方案确认 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-18 | fluxion-harness | 初始草稿：以内网 Run `70c31545` 为实例 |
| v0.2 | 2026-11-18 | fluxion-harness | 吸收复查结论：内容层失败契约、多维事实、5xx 有界重试、平台确定性收尾、压缩边界、统一终止漏斗、恢复事实与未完成交付物来源 |
| v0.3 | 2026-11-18 | fluxion-harness | 补充 stdout/stderr 采集与证据边界：文本只作证据、output_truncated 与工具边界持久化 |
| v0.4 | 2026-11-18 | fluxion-harness | 复审收敛：平台直出（无模型前言）、仅 `target=skill` 有内容层失败、结构化 retryable、preview-only 证据、completion 只用现有契约、统一所有终止出口 |
| v0.5 | 2026-11-18 | fluxion-harness | 纳入定时任务执行链路：Skill 的 argv/input 契约、触发时刻与窗口、预算读平台设置、草稿诊断、TASK 导出补齐、Fake MSSP 场景与本地回归 Skill；系统提示词另行需求 |
| v0.6 | 2026-11-18 | fluxion-harness | 定时任务重构为 Job = Schedule + Payload + Session + Delivery + Policy；payload 为 REMINDER/SKILL_PLAN/AGENT_TURN；authoring 推断/校验/试跑/提升；用户不选 mode；与 unified-artifact-delivery 定义契约边界 |

---

## 2. 背景与目标

### 2.1 问题陈述

| 维度 | 内容 |
|------|------|
| **问题描述** | 内网一次报告生成 Run（`70c31545-a9ac-40ec-b1be-92f5f00103ac`，2026-11-18 06:11:09–06:12:49 UTC）在轮次预算打满后，收尾消息对用户说“我这边没有收到具体的失败信息——当前对话里只有可用的 Skill 列表，没有任何工具调用记录、错误码或平台返回内容”，要求用户自己贴报错；而同一 Run 的审计事实是：20 次工具调用全部执行、其中包含 XDR 接口 `HTTP 500（openresty）` 与 `mssw` 会话 `401 PLATFORM_AUTH_REJECTED`。**平台自己产出的结论与平台自己记录的事实相反。** |
| **当前替代方案** | 用户只能自己重跑、翻审计导出包或贴日志；排障人员也只能按 run_id 去 grep 日志。现有导出包只看得到 transport `OK` 与 `RUN_BUDGET_EXCEEDED`，无法解释内容层失败。 |
| **触发原因** | 四个可分离缺陷叠加：① 内容层失败（skill 失败 status/退出码、结构化业务错误）不进入失败判定，自动止损永远不会触发，模型把 20 轮预算烧在手工排查上；② 预算耗尽与失败停止复用同一段收尾提示词（“工具执行已因失败停止，请根据上面的失败信息…”），在没有任何工具失败时给模型一个假前提；③ 收尾指令作为普通 USER 追加后，消息条数兜底从收尾指令起切，整个工具历史被裁掉；④ 审计/导出只记录 transport 状态，内容层失败不可见，且 timeline 截断会丢掉原始失败调用。 |

**实例账目**（来自 Console 导出的链路分析包）：

| 事实 | 数值 |
|---|---|
| 模型调用 / 工具调用 | 21 / 20（另 1 次工具调用因轮次打满未执行） |
| 真正执行任务的轮次 | 1（第 3 轮执行 `daily-duty-report-mssp`） |
| 失败后手工排查轮次 | 16（10 轮通读 skill 源码 + 4 轮手拼请求 + 1 轮查会话 + 1 轮换客户号探测） |
| 工具层失败数 | 0（审计全部 `OK`） |
| 内容层失败证据 | XDR `500 Internal Server Error`（openresty）、`mssw` `401`；原始报告调用的结果不在导出包可见范围内 |
| 终止原因 | `RUN_BUDGET_EXCEEDED`（轮次 20/20，工具 20/30，耗时 100s/3600s） |
| 收尾前压缩 | 3 次，均 `budget` 层，最后一次 108KB；最后一次紧邻收尾模型调用 |

> 证据边界：本次导出包能证明 20 次工具调用、模型推理中的 500/401，以及收尾前的压缩；原始 `daily-duty-report-mssp` 的结果正文不在包内，需在实现后补齐工具审计业务字段。

---

### 2.2 目标与价值

| 维度 | 内容 |
|------|------|
| **核心目标** | 一次执行失败时，平台必须以**自身事实**为依据向用户交代：哪一步失败、平台返回了什么、哪些部分无法确认；同时不在同一个确定性失败上反复消耗预算。 |
| **预期价值** | 用户不必再猜“到底哪里错了”；排障从“翻日志”变成“看结论”；长任务不再把预算耗在无效重试上；审计结论与用户结论同源。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 收尾结论与审计事实矛盾（如否认已存在的工具调用/业务失败） | 实测 1 次 | 0 | 回归用例：构造“工具全 OK + 内容层失败 + 轮次打满”场景断言收尾文本 |
| 确定性失败后继续重试的轮次 | 实测 16 轮 | 0（确定性失败第一次即停；明确可重试失败按冻结策略有界重试） | 回归用例：skill 失败 status/退出码/retryable、鉴权错误、5xx/429 分类 |
| 收尾事实项完整度 | 0/6 | 6/6（终止原因、用量、工具结果、业务失败证据、证据采集状态、完成状态） | 回归用例断言事实块与用户文案 |
| 工具预算/deadline/取消/model 错误/验证失败/空回复/失败+预算出口 | 未覆盖 | 全部有确定性收尾 | 回归用例覆盖所有终止出口 |
| `workspace_shell`/`skill_draft_shell` 非零退出误停 Run | 未覆盖 | 0 | 回归用例：`grep`/`test` 退出 1 不触发止损 |
| 审计导出可区分工具层/内容层失败 | 否 | 是，且 timeline 截断不影响失败证据 | 导出契约/集成用例 |

---

## 3. 用户与场景

### 3.1 目标用户

| 维度 | 内容 |
|------|------|
| **用户画像** | 终端用户（在 WebChat/企微里让 Agent 产出报告、查询类任务的值守与运营人员）；平台排障人员（从 Console 审计与链路分析包定位问题）；Skill 作者。 |
| **使用场景** | 用户发起一个依赖外部平台数据的任务；外部平台不可用或会话失效；Agent 反复重试直到预算耗尽；用户收到一条与实际情况不符的结论。 |

### 3.2 用户故事

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为终端用户，任务失败时我希望直接看到**哪一步失败、平台返回了什么、哪些内容没取到/无法确认**，而不是被要求自己贴报错。 | P0 |
| US-02 | 作为终端用户，外部平台已经明确不可用时，我希望任务尽快停下并告诉我原因；瞬时故障可以按策略有限重试。 | P0 |
| US-03 | 作为终端用户，长任务即使被预算截断，收尾也应当基于本回合真实发生过的事情。 | P0 |
| US-04 | 作为排障人员，我能从 Run 终态与审计导出包直接读到终止原因、用量、工具结果与失败证据，不用自己 grep 日志。 | P1 |
| US-05 | 作为排障人员，我需要区分“确认没有失败”“未采集到业务失败证据”“无法判断”，避免把缺证据当成成功。 | P1 |

---

## 4. 功能需求

### 4.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|--------|---------|---------|--------|-------------|
| FEAT-01 | 内容层失败纳入失败判定 | 业务执行适配器输出统一执行结果（status/exit_code/error/failure_layer/retryable/evidence），仅 `target=skill` 的内容层失败进入失败跟踪；5xx/429/网络按冻结策略有界重试，鉴权/凭据错误立即停止。 | P0 | US-02 |
| FEAT-02 | 终止原因感知的收尾 | 收尾按终止原因分流：turn/tool budget、deadline、no_progress、取消、model/验证/空回复/shutdown、失败+预算各自拿到准确说明；所有出口统一收尾。 | P0 | US-01 |
| FEAT-03 | 收尾事实块 | 平台生成多维事实：终止原因、轮次/工具/时间用量、工具结果、业务失败证据、证据采集状态、完成状态。 | P0 | US-01/03/05 |
| FEAT-04 | 收尾一致性约束 | 平台确定性渲染关键事实；失败后不调用模型，事实块不可删除/改写，不依赖否定措辞检查。 | P0 | US-01 |
| FEAT-05 | 收尾不依赖压缩历史 | 失败收尾不进入模型请求；普通压缩逻辑不变；事实由审计/快照重建，不追加 USER 指令、不做 protected suffix。 | P0 | US-03 |
| FEAT-06 | 审计与导出可见 | Run 终态与链路分析包展示终止原因、用量、工具结果与失败证据（含内容层）；timeline 截断不影响 failure evidence。 | P1 | US-04/05 |

#### FEAT-01: 内容层失败纳入失败判定

- **描述**: 由业务执行适配器通过 `ToolDefinition.outcome_parser` 输出 `ExecutionOutcome`，pipeline 只消费该契约；内容层失败不得被 transport `OK` 掩盖。判定口径不在模型提示词里兜，也不扫描任意 stdout 的 4xx/5xx；stdout/stderr 只作为有界、脱敏的证据预览。
- **验收标准**:
  - [ ] `target=skill` / `execute_skill` / `run_skill_script` 的 `status in (FAILED, TIMED_OUT)` / `exit_code != 0` 被识别为失败，并给出 `failure_layer=CONTENT`；`CANCELLED` 不算失败。
  - [ ] `retryable` 来自 skill 失败信封（stderr 最后一个 JSON）；缺失时按确定性处理，不得静默伪装可重试。
  - [ ] 工具 handler 异常/错误体被识别为 `failure_layer=TOOL`。
  - [ ] 鉴权/凭据错误（401/403、`PLATFORM_AUTH_REJECTED`）立即停止；明确标记为可重试的 5xx/429/网络失败按冻结策略有界重试。
  - [ ] `workspace_shell` / `skill_draft_shell` 的非零退出是结果证据，不判失败、不触发止损；`grep`/`test` 退出 1 不能终止 Run。
  - [ ] 证据在工具边界有界落库（`evidence_preview`/`TOOL_RESULT` 引用），且写入前脱敏；不能依赖导出时从原始 stdout 补采。
  - [ ] 无法判断业务结果时标记 `UNKNOWN`，不得伪装成功。
  - [ ] 一次真正的成功执行仍按现状清零失败计数；历史 `last_failure` 不作为本次停止原因。

#### FEAT-02: 终止原因感知的收尾

- **描述**: 所有终止出口统一进入 finalization funnel，按 `termination_reason` 生成准确说明；turn budget、tool budget、deadline、no_progress、取消、model/verification/empty/shutdown 分别处理。
- **验收标准**:
  - [ ] 轮次/工具/时间预算耗尽时，说明具体预算类型与用量，不含“工具执行已因失败停止”这类假前提。
  - [ ] 工具预算耗尽、deadline、取消均有确定性收尾，不出现空正文。
  - [ ] model 错误、验证失败、空回复、runtime shutdown 也走同一收尾漏斗，不出现“只有错误码、没有正文”。
  - [ ] 失败+turn/tool budget 同时发生时，以失败为主、预算为辅；事实块记录 secondary reason。
  - [ ] deadline/取消以实际停止条件为主；此前失败只作次要事实，不伪装成主因。
  - [ ] 取消写 `RUN_COMPLETED(CANCELLED)`，不写 `RUN_FAILED`；挂起 `WAITING_*` 不生成失败事实。
  - [ ] 收尾不占用工具预算；未执行工具调用记录为 pending，不进入失败计数。

#### FEAT-03: 收尾事实块

- **描述**: 收尾前由平台生成结构化事实，不依赖模型记忆或压缩后的历史；事实按“终止、工具结果、业务失败、证据状态、待执行、完成状态”分组。
- **验收标准**:
  - [ ] 事实块至少包含：终止原因；轮次/工具/时间用量；工具结果汇总；业务失败证据（工具名、status/退出码、脱敏摘要）；证据采集状态；待执行调用；完成状态。
  - [ ] 不得把“工具层全 OK”写成“没有失败/仅有预算耗尽”；必须分别表达工具结果与业务失败证据。
  - [ ] 必须区分 `CONFIRMED_FAILURE`（确认失败）、`CONFIRMED_NONE`（确认无失败）、`NOT_OBSERVED`（未采集到业务结果）、`UNKNOWN`（无法判断）。
  - [ ] `output_truncated=true` 时不得写 `CONFIRMED_NONE`；除非已有 confirmed failure，否则标 `UNKNOWN` 并说明证据可能不完整。
  - [ ] `last_failure` 只作为最近证据；本次停止原因由 `termination_reason` + `decisive_failure` 表达，`decisive_failure` 只在 `NO_PROGRESS` 时取 `last_failure`。
  - [ ] 完成状态只使用已有契约：普通 Run 没有交付物/输出契约时写 `UNKNOWN`（“完成情况无法确认”）；只有已有 `output_contract` 验证结果时才写 `CONTRACT_VERIFIED/CONTRACT_FAILED`。
  - [ ] 事实块不受压缩层影响。

#### FEAT-04: 收尾一致性约束

- **描述**: 平台确定性地渲染关键事实；失败收尾不调用模型。事实块始终输出，模型空输出/失败/编造不影响事实正确性。
- **验收标准**:
  - [ ] 收尾文案包含终止原因、用量、工具结果、业务失败证据/证据状态、完成状态；模型不能删除或改写事实块。
  - [ ] 失败后不发送收尾模型请求、不追加 USER 收尾指令、不依赖模型前言；模型此前输出的矛盾内容不能覆盖平台事实块。
  - [ ] 不依赖否定措辞黑名单或人工判断；关键事实由平台模板回归验证。
  - [ ] 所有失败/取消出口都由平台直出；不保留“可选模型前言”分支。

#### FEAT-05: 收尾不依赖压缩历史

- **描述**: 失败收尾不进入模型请求，因此不向历史追加 USER 指令、不引入 protected suffix、不改变原执行回合边界；普通模型回合的压缩逻辑保持不变。
- **验收标准**:
  - [ ] 失败后不追加收尾 USER 消息，不调用收尾模型；压缩器不会因收尾指令裁掉历史。
  - [ ] 构造超过消息预算的长回合，失败事实仍能由审计/快照重建，不依赖压缩后的模型上下文。
  - [ ] 普通模型回合的工具历史仍按完整 assistant/tool 调用组处理，不得留下孤儿 tool 消息。
  - [ ] 失败证据有容量上限，保留脱敏预览/引用，不保护所有失败原文。
  - [ ] 压缩失败仍退回原历史，不得让 Run 失败；降级可见。

#### FEAT-06: 审计与导出可见

- **描述**: 终态与链路分析包直接呈现终止原因、用量、工具结果与失败证据；内容层失败与工具层失败可区分；失败证据不随 timeline 截断丢失。
- **验收标准**:
  - [ ] 工具审计包含 `business_status`/`failure_layer`/`exit_code`（可空）/`output_truncated`/`evidence_preview` 等业务字段；复用现有 `artifact_id`，不新增 evidence artifact。
  - [ ] 老审计行回填为 `business_status=UNKNOWN`、`failure_layer=NULL`、`output_truncated=NULL`，不得把历史 transport `OK` 伪造成业务成功或“未截断”。
  - [ ] `execution_diagnosis` 能发现 `status=OK + business_status in (FAILED, TIMED_OUT, DENIED)` 的内容层失败。
  - [ ] `report.md`/`failures.csv` 能区分“工具层失败”与“内容层失败”；`failures.csv` 只列 confirmed failure，不列 `UNKNOWN`/`NOT_APPLICABLE`。
  - [ ] `calls.csv` 能读到工具业务状态/退出码/结果预览/截断标记，而不是只有 transport `OK`。
  - [ ] 终止原因与用量无需再 grep 日志即可读到。
  - [ ] timeline 可截断，但 `failure_evidence` 必须保留首个失败、最后失败、所有 confirmed 失败摘要与收尾事实；完整记录仍在审计表。
  - [ ] `output_truncated=true` 时明确说明证据可能不完整，不能把截断后的“无异常”当成确认无失败。
  - [ ] `data_quality` 区分 DB 读取完整与导出包截断，避免“完整: True + 已截断”的矛盾。

---

### 4.2 边缘情况

| 场景 | 预期行为 |
|------|----------|
| 工具层全 OK、存在内容层失败、预算耗尽 | 分别表达终止原因、工具结果、业务失败证据；不得写“无工具失败/仅预算耗尽” |
| 工具层全 OK、有业务执行且全部确认成功、纯预算耗尽 | 明写“确认无业务失败，预算耗尽”；不得索要报错 |
| 工具层全 OK、但未采集到业务结果 | 明写“未采集到业务失败证据”，不冒充成功或失败 |
| 诊断命令 stdout 含 500/401 | 执行成功；只作脱敏预览，不据此判失败 |
| 执行成功但输出疑似错误、无结构化依据 | `evidence_status=UNKNOWN`，展示预览；不写“全部完成” |
| 输出超过采集上限被截断 | `output_truncated=true` 可见；说明证据可能不完整，不把“无异常”当确认无失败 |
| `workspace_shell` / `skill_draft_shell` 退出码非零 | 结果证据，不触发止损；由模型决定是否调整命令 |
| skill 非零退出但未提供 retryable | 按确定性处理；事实标注 retryability unknown |
| model 错误/验证失败/空回复/runtime shutdown | 同一收尾漏斗，写现有错误码 + 平台事实 |
| 工具层失败 + 预算同时打满 | 以失败为主、预算为辅；事实块引用 decisive failure |
| 工具预算耗尽 | 确定性收尾；pending 调用不算失败 |
| deadline 硬超时 | 平台确定性说明；不得等模型；未执行调用记录为 pending |
| 取消 | 写 `RUN_COMPLETED(CANCELLED)`，不写 `RUN_FAILED`；说明用户停止，不编造失败原因；预算/失败原因只作次要事实 |
| 模型此前输出矛盾/编造/空输出 | 平台事实块覆盖，不采用模型结论；失败收尾不再调用模型 |
| 压缩层启用摘要/条数兜底 | 事实块独立存在；工具历史按完整调用组处理 |
| 恢复（resume）后的收尾 | 从 checkpoint 失败状态 + 审计/事件重建；不依赖进程内存 |
| 没有交付物依据 | 明确“完成情况无法确认”，不从工具日志猜测 |
| 导出 timeline 截断 | failure evidence 仍保留；报告明确说明截断范围 |

---

## 5. 非功能需求

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-PERF-01 | 性能 | 正常成功路径不增加额外模型调用；所有失败/取消收尾都由平台直出，不调用模型；不增加工具轮次。 |
| NFR-REL-01 | 可靠性 | 收尾事实可由 checkpoint/审计重建；事实/渲染任一步失败都不得让 Run 卡死；降级可见。 |
| NFR-TEST-01 | 可测性 | 内容失败、瞬时失败、工具预算、deadline、取消、model/验证/空回复、resume、模型编造均可由自动化用例复现。 |
| NFR-SEC-01 | 安全 | 失败证据在写入审计/渲染前脱敏；不输出凭据、Cookie、Token、内部 URL。 |
| NFR-CAP-01 | 容量 | 证据预览有界，按完整调用组保留；失败证据只保留摘要/引用，不保护所有原文。 |

---

## 6. 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | 业务执行结果契约与内容层失败分类；失败跟踪与终止原因；收尾事实/平台渲染；Runtime 终态事件持久化；工具审计/导出；checkpoint 最小失败状态；Skill 失败结果契约与模板 |
| **非范围（Out of Scope）** | ① skill 自身业务实现与平台可用性修复；② 调整轮次/工具/时间预算默认值（见 OPEN-04）；③ 新增 Console 页面或事实表/消息队列；④ 新增“部分完成”终态；⑤ 历史 Run 的事实回填；⑥ 模型供应商与模型能力评估；⑦ 模型收尾前言 / protected suffix；⑧ stdout/stderr 模式扫描（advisory）；⑨ 交付物计划器 / expected_deliverables |
| **前置假设** | 业务执行适配器能输出结构化执行结果；Runtime 可在收尾时读取本 Run 的 `tool_call_audit`；现有 `FAILED` 终态与 `RUN_NO_PROGRESS`/`RUN_BUDGET_EXCEEDED`/`RUN_DEADLINE_EXCEEDED` 等错误码保留 |

---

## 7. 依赖与风险

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|--------|----------|--------------|---------|
| 执行内核（agent-core） | `ExecutionOutcome`/`outcome_parser`、`TerminationSnapshot`、失败状态 | 与实现同批 | 中 |
| Runtime / 工具适配器 | `execute` outcome、Skill 结果契约、事实渲染/持久化 | 与实现同批 | 中 |
| 审计导出（Console） | 业务字段、`finalization`、failure evidence 保留 | 与实现同批 | 低 |
| Skill 作者/文档 | 失败 status/退出码 + 结构化 `code/retryable` 契约 | 与实现同批 | 中 |

### 7.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|---------|
| R-01 | 把 `exit_code != 0` 一律当确定性失败，会误伤既有 skill/诊断命令 | 正常任务被提前终止 | 仅 `target=skill` 的业务执行按 skill 失败契约判定；workspace/draft 退出码只作证据；回归覆盖 |
| R-02 | 收尾一致性判定过严或依赖措辞 | 正常措辞被误判或伪造通过 | 平台确定性渲染事实，失败后不调用模型；不做否定措辞黑名单 |
| R-03 | 事实块含平台原文，可能夹带敏感信息 | 泄密 | 写入前脱敏；证据预览有界；skill stdout 不默认可信 |
| R-04 | 保护所有失败原文导致上下文超限 | 收尾失败 | 只保留预览/引用与完整调用组；证据容量上限 |
| R-05 | 未完成交付物缺少来源 | 误报“完成/未完成” | 只用已有 `output_contract` 结果；普通 Run 一律写“完成情况无法确认”，不新增交付物计划 |
| R-06 | 5xx/429 误判为不可重试 | 瞬时故障被提前终止 | skill 失败信封显式 `retryable`；鉴权错误才立即停止；缺省按确定性 |
| R-07 | 诊断命令非零退出被当内容失败 | `grep`/`test` 正常退出 1 误停 Run | 仅 `target=skill` 有内容层失败；workspace/draft 退出码只进证据 |

---

## 8. Existing Spec Constraints

> 仅列与范围相关的 Rule；落位方式已在设计阶段逐条确认，不由 Agent 自行填写 N/A。

| Spec/Rule | 约束 | 对范围/验收的影响 | 状态 |
|-----------|------|------------------|------|
| `snapshot/harness-snapshot#RULE-001` | 检查点必须覆盖预算状态；压缩失败保留原历史、降级可观测、不得让主执行失败 | FEAT-03/05 的边界（事实可重建、压缩不得破坏主执行） | 设计已覆盖（design §4.4/4.5/4.6） |
| `snapshot/harness-snapshot#RULE-snapshot-001` | 任务预算归 execution snapshot，配置变化只影响新执行 | FEAT-01/02 不得改变已冻结预算语义 | 设计已覆盖（design §4.3） |
| `api/harness-api#RULE-api-001` | 业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml` | 终止原因文案不新增错误码；详情在 facts | 设计已覆盖（design §7） |
| `logging/harness-log#RULE-log-001` | 统一日志口径与脱敏字段 | 失败证据落日志/导出的格式与脱敏 | 设计已覆盖（design §7） |

---

## 9. 已决策问题

| 编号 | 问题 | 结论 |
|------|------|------|
| OPEN-01 | 收尾档位：平台事实+模型措辞，还是平台直出？ | **B：平台确定性渲染事实；失败后不调用收尾模型，不做可选前言** |
| OPEN-02 | 预算耗尽是否仍作为 `FAILED` 终态？ | 保持 `FAILED`，增加 `termination_reason` 和完成状态；不新增“部分完成”终态 |
| OPEN-03 | 内容层失败判定口径？ | 只有 `target=skill` 的业务执行按结构化 `status/exit_code/error/retryable` 判定；workspace/draft 退出码只作证据 |
| OPEN-04 | 是否调整 `agent.max_turns` 等预算默认值？ | 不调整；默认值不是本需求的解法 |
| OPEN-05 | 压缩保护边界？ | 失败收尾不进入模型请求、不追加 USER 指令、不做 protected suffix；普通压缩不变 |

---

## 10. 评审结论（v0.4）

2026-11-18 两轮复查确认以下修订已纳入：

1. 内容层失败识别必须由业务执行适配器输出统一契约，不能只补执行 status，也不能以裸 stdout 扫描判失败；stdout/stderr 只作有界证据。
2. “无工具层失败”不等于“没有失败”；事实必须拆分为终止原因、工具结果、业务失败证据、证据采集状态。
3. 只有 `target=skill` 的业务执行有内容层失败；`workspace_shell`/`skill_draft_shell` 的退出码只作证据，避免 `grep`/`test` 退出 1 误停 Run。
4. skill 的 `retryable` 必须由失败信封显式提供；缺省按确定性处理；5xx/429 只有明确标记后才按冻结策略重试。
5. 收尾一致性由平台确定性事实保证；失败后不调用模型、不做可选前言、不依赖否定措辞检查。
6. 失败收尾不进入模型请求、不追加 USER 指令、不做 protected suffix；普通压缩逻辑不变。
7. 工具预算、deadline、取消、model/验证/空回复/shutdown、失败+预算都纳入统一 finalization funnel；未执行调用不算失败；取消写 `RUN_COMPLETED(CANCELLED)`。
8. 恢复需最小失败状态；未完成交付物只用已有 `output_contract`，无依据时写“完成情况无法确认”；不新增交付物计划。
9. 审计导出必须补工具业务字段与 failure evidence 保留；老行回填 `UNKNOWN`/`NULL`，不得伪造成业务成功；timeline 截断不得丢原始失败。
10. stdout/stderr 默认采集但审计不保存全文；证据必须在工具边界有界落库并脱敏；不做 advisory 模式扫描。

---

## 11. 定时任务执行模型（v0.6）

### 11.1 问题陈述

当前定时任务不是"一个完整的执行模型"，而是"定时器 + 两个半成品模式"：

| 现状 | 问题 |
|---|---|
| `SKILL` 模式 | 纯执行，但没有 argv/input 双通道；旧 CLI Skill 无法运行 |
| `AGENTIC` 模式 | 有 agent，但工具面被裁剪：没有 argv、没有 `current_time`、没有任务查询；预算由模型估 |
| `REMINDER` 模式 | 纯通知，没问题 |
| Authoring | 模型直接选 mode；没有从 Skill 契约编译参数；没有试跑；卡片不展示"下次执行什么" |
| 运行时 | 没有 session 目标、没有 per-job 工具策略、没有静默结果/上次结果/scratch |
| 失败 | `TASK_NO_PROGRESS` 只有一句话；导出包看不到 task_event |

**事实边界**：`daily-duty-report-mssp` 在对话中能成功执行，说明 Skill、平台登录、数据都通；当前失败是平台契约问题，不是 Skill 业务问题。因此不应要求 Skill 作者改包，也不应把 `cron_entry.py` 当成长期方案。

### 11.2 目标

把定时任务从"定时 shell"升级为 **Job = Schedule + Payload + Session + Delivery + Policy**：

- **Payload 三选一**：`REMINDER`（通知）、`SKILL_PLAN`（确定性 Skill 调用，无模型）、`AGENT_TURN`（prompt 模式，完整 agent）。
- **Authoring 由 agent 参与**：识别意图 → 读 Skill 契约 → 编译计划 → 校验 → 试跑 → 用户确认。**用户不选 mode。**
- **Runtime 确定性优先**：固定 Skill + 固定参数走 `SKILL_PLAN`，不消耗模型；需要判断/多工具/上下文才走 `AGENT_TURN`。
- **提升优先于猜测**：用户已经成功跑过一次的任务，直接把那次真实调用提升为定时任务（promotion），不重新猜意图。
- **运行时能力补齐**：时间窗口、session 目标、per-job 工具策略、平台设置预算、静默结果、上次结果/scratch、失败通知。
- **失败可解释**：定时任务的失败必须能读到工具、错误码、原始原因；不允许只有 `TASK_NO_PROGRESS`。

### 11.3 用户故事

| 编号 | 用户故事 | 优先级 |
|---|---|---|
| US-07 | 作为用户，我只用一句话描述"每天 9 点给某客户出昨天的日报"，agent 给我一张业务语言的卡片，我不需要理解 SKILL/AGENTIC 的区别。 | P0 |
| US-08 | 作为用户，创建前我能看到一次**真实试跑**的输出；试跑失败就不创建任务。 | P0 |
| US-09 | 作为用户，我已经在对话里成功跑过一次的任务，可以一键"做成定时任务"，平台复用那次真实调用。 | P0 |
| US-10 | 作为用户，固定日报不消耗模型、结果稳定；开放性任务每次由 agent 判断并调用工具。 | P0 |
| US-11 | 作为排障人员，我能看到任务的 payload、计划、工具策略、预算来源、时间窗口和失败原因。 | P0 |
| US-12 | 作为用户，没有变化时任务可以静默，不刷屏；有文件产物时仍按投递策略交付。 | P1 |

### 11.4 功能清单（定时任务）

| 功能ID | 功能名称 | 描述 | 优先级 |
|---|---|---|---|
| FEAT-13 | Job/Payload 模型 | Job 由 schedule + payload + session + delivery + policy 组成；payload 为 `REMINDER` / `SKILL_PLAN` / `AGENT_TURN`；模式是内部派生字段，不是用户选项 | P0 |
| FEAT-14 | Authoring 编译器与意图识别 | agent 输出结构化计划（kind/skill/input/argv/window/confidence/reason）；平台校验 Skill 契约、cron、时区、路由；不确定时问业务问题，不选 mode | P0 |
| FEAT-15 | 试跑与提升 | 创建前真实执行一次并投递；从一次成功对话调用直接提升为定时任务；试跑失败不建 Job | P0 |
| FEAT-16 | SKILL_PLAN 确定性运行时 | 渲染 `input`/`argv`/时间窗口并冻结；无模型；重试幂等；stdin 只含业务 input | P0 |
| FEAT-17 | AGENT_TURN prompt 模式 | 完整 agent turn：prompt + 时间行 + 窗口 + 无人值守契约 + 上次结果/scratch；完整工具面；session 目标；静默结果 | P0 |
| FEAT-18 | Session 目标 | `isolated`（默认）/ `current`（绑定创建会话）/ `session:<id>`（持久）/ `main`（同会话） | P1 |
| FEAT-19 | 工具策略与预算 | per-job `tools_allow`（MCP/SKILL/平台只读工具）；预算从 `PlatformSettings.agent` 解析并冻结；`max_tokens=0` 显示"不限制" | P0 |
| FEAT-20 | 静默结果与失败通知 | `record_result(no_change)` 结构化静默；用户可见文本必须是直白语言；上次 meaningful result 有界注入；失败通知与退避；文件产物独立于文本静默 | P1 |
| FEAT-21 | 定时任务诊断与测试资产 | TASK 导出包含 task_event/工具/模型/开始时间；NO_PROGRESS 带 last_failure；Fake MSSP + 本地回归 Skill 覆盖 SKILL_PLAN/AGENT_TURN | P1 |

### 11.5 验收标准（定时任务）

#### FEAT-13 Job/Payload 模型

- [ ] Job 持久化 payload kind、session target、delivery、tool policy；旧 `SKILL`/`AGENTIC` 模式在开发期直接迁移为 `SKILL_PLAN`/`AGENT_TURN`，不保留双读。
- [ ] `mode` 不再是模型工具的用户选项；由 agent/平台从计划推导，卡片只展示"执行方式"说明。
- [ ] 卡片展示 payload、计划、时间窗口、工具策略、预算来源、下次触发。
- [ ] 同一个 Job 的 payload kind 不可在执行中漂移；修改需新 Revision。

#### FEAT-14 Authoring 编译器与意图识别

- [ ] agent 输出结构化 `SchedulePlan`（kind/skill/input/argv/window/session/delivery/confidence/reason）。
- [ ] 平台校验：skill 已批准、entrypoint 可执行、input/argv 与契约匹配、cron/timezone 合法、delivery 路由存在。
- [ ] 低置信度/缺参数时问业务问题（"客户固定还是每次判断？"），不暴露 mode 选项。
- [ ] 提供黄金集回归：固定日报 → SKILL_PLAN；开放汇总 → AGENT_TURN；提醒 → REMINDER。
- [ ] 计划校验失败时返回结构化字段错误，模型可修正一次；仍失败则降级为 AGENT_TURN 或让用户确认。

#### FEAT-15 试跑与提升

- [ ] 创建前执行一次真实运行，产出真实文件/报告并投递到用户指定位置。
- [ ] 试跑失败不创建 Job；展示失败事实与下一步。
- [ ] 从一次成功对话调用提升：复用 skill_key/input/argv/platform，把日期参数绑定为 `{{previous_day}}` 等。
- [ ] 试跑与正式运行走同一条执行/产物/投递链路，不能用渲染预览冒充试跑。
- [ ] Job 创建后默认 enabled；失败由通知/退避/自动停用兜底，不创建"待确认的 disabled job"。

#### FEAT-16 SKILL_PLAN 确定性运行时

- [ ] `input` 写 stdin，`argv` 追加到脚本；控制字段不混入业务 input。
- [ ] 时间变量在 fire 时渲染并冻结；重试复用同一 argv/input。
- [ ] 无模型调用；Skill 退出码/结果按现有失败契约处理。
- [ ] 旧 Revision 无 argv_template 视为空；旧包走 stdin。

#### FEAT-17 AGENT_TURN prompt 模式

- [ ] prompt = job prompt + 时间行（`scheduled_at`/timezone/window）+ 无人值守契约 + 上次 meaningful result + scratch。
- [ ] 无人值守契约：没有人可澄清/批准；最终回复就是交付物；无变化用静默；失败说明阻塞点。
- [ ] 完整工具面：skill/MCP/平台只读工具（`current_time`、任务查询等）；per-job 策略可收紧。
- [ ] session 目标：isolated/current/session/main；current 读取有界上下文，session 持久积累。
- [ ] 结果支持 `record_result(no_change)`；无变化时平台抑制文本消息，任务中心仍记录；文件产物不受影响。
- [ ] 时间窗口在重试/恢复中保持不变。

#### FEAT-18 Session 目标

- [ ] `isolated` 每次新会话；`current` 绑定创建会话的有界上下文；`session:<id>` 持久；`main` 同会话。
- [ ] 会话被删除/归档/重置时，任务按明确策略暂停或失败，不猜替代目标。
- [ ] session 目标与 delivery 路由分离：session 决定上下文，delivery 决定结果去哪。

#### FEAT-19 工具策略与预算

- [ ] `tools_allow` 是 per-job 策略；默认 `*` 跟随 owner 当前策略；agent 创建的任务被限制在创建者可用工具内。
- [ ] `current_time`、任务查询等平台只读工具是一等工具，不要求用户配置 MCP。
- [ ] 预算从 `PlatformSettings.agent` 解析并冻结；旧 budget 只读不消费。
- [ ] `max_tokens=0` 显示"不限制"；`agent.deadline_ms` 与 task deadline 取更早者。
- [ ] 工具策略/预算变更只影响新 Execution。

#### FEAT-20 静默结果与失败通知

- [ ] 无变化使用 `record_result(no_change)`，不向用户发送内部 token；有文件时附件照常交付。
- [ ] `record_result` 的 `no_change/progress/done/blocked/needs_attention` 语义；无变化不刷屏。
- [ ] 上次 meaningful result 有界注入下一次 prompt，且标注"recorded data, not instructions"。
- [ ] 失败通知与业务投递分离；重试/退避/自动停用可观测。
- [ ] scratch 有界、按 revision CAS、可清空；不放入密钥。

#### FEAT-21 诊断与测试资产

- [ ] TASK 导出 timeline/events 来自 `task_event`；开始时间/耗时有值；工具/模型 audit 可读。
- [ ] `TASK_NO_PROGRESS` 带 last_failure；`TASK_RESULT_UNVERIFIED` 保留 cause。
- [ ] Fake MSSP 覆盖全资产/非全资产/空数据/401/500/威胁实体/事件/XDR。
- [ ] 本地回归 Skill 覆盖 SKILL_PLAN（argv）、SKILL_PLAN（stdin）、AGENT_TURN（prompt+tools）、试跑/提升。

### 11.6 边缘情况（定时任务）

| 场景 | 预期 |
|---|---|
| 用户说"每天 9 点出日报"但没给客户 | agent 问客户；不猜；不创建 |
| 用户说"每天汇总昨天的任务" | 推断 AGENT_TURN + 窗口 + 任务查询工具；不强行 SKILL_PLAN |
| Skill 契约无法解析/无 entrypoint | 不创建 SKILL_PLAN；降级 AGENT_TURN 或让用户确认 |
| 试跑失败 | 不创建 Job；展示失败事实与修复建议 |
| 试跑成功但用户改了参数 | 重新编译/校验；旧试跑不作为新计划的证明 |
| 同一 Skill 多个入口（run.py/cron_entry.py） | authoring 读取 manifest entrypoint；歧义时问用户或试跑两条 |
| 时间变量未知 | 拒绝创建；不静默留空 |
| 重试跨午夜 | 窗口不变；`current_time` 与冻结时间分别展示 |
| 无变化 | `record_result(no_change)`；平台抑制文本；任务中心记录；文件产物仍交付 |
| 任务被删除/归档会话 | `current` 任务暂停/失败；不猜新会话 |
| 工具被撤权 | 任务失败并说明；不静默换工具 |
| 旧 Job 无 payload kind | 开发期迁移；不保留双读 |

### 11.7 非功能需求（定时任务）

| 指标ID | 要求 |
|---|---|
| NFR-DET-01 | SKILL_PLAN 的 input/argv/window 由平台冻结；重试/恢复不漂移 |
| NFR-COST-01 | 固定任务默认不消耗模型；AGENT_TURN 的成本在卡片可见 |
| NFR-INTENT-01 | 意图→计划有黄金集回归；低置信度必须问业务问题或试跑 |
| NFR-OBS-01 | Job 详情可读 payload/计划/工具/预算来源/窗口/失败原因 |
| NFR-SEC-01 | 工具策略按 tenant/owner/agent 收紧；不因定时任务扩大权限 |
| NFR-TEST-01 | Fake MSSP + 本地 Skill 可复现 SKILL_PLAN/AGENT_TURN/试跑/提升 |

### 11.8 范围与边界（定时任务）

| 类别 | 内容 |
|---|---|
| **范围** | Job/Payload 模型；authoring 编译器与意图识别；试跑/提升；SKILL_PLAN；AGENT_TURN；session 目标；工具策略/预算；静默结果/上次结果/scratch；定时任务诊断；Fake MSSP/本地回归 |
| **非范围** | 系统提示词/平台 guide（另行需求）；文件产物投递的存储/状态机（`unified-artifact-delivery` 负责）；脚本 payload（后续）；模型能力评估；内网真实 MSSP 平台改造；历史 Job 回填 |
| **前置假设** | 平台设置可冻结；Skill manifest 可读；调度时区可用；Runtime/Worker 能访问统一 Artifact/Delivery 契约 |

### 11.9 与 `unified-artifact-delivery` 的耦合

`unified-artifact-delivery` v2.0 已定稿为「消息附件 + Web pull / 企微 push + 单一消息级 push 状态机」。耦合是**契约耦合**，不是同一个需求。

| 交界 | 归属 | 约定 |
|---|---|---|
| 产物登记、`OUTPUT` 版本、`logical_file_id + version` | `unified-artifact-delivery` | `SKILL_OUTPUT_DIR` 只登记成功输出；`SKILL_PLAN`/`AGENT_TURN` 不自己发送 |
| 消息与附件契约 | `unified-artifact-delivery` | `MessagePayload { text, attachments[] }`；多文件一条消息多个附件 |
| Web pull | `unified-artifact-delivery` | WebChat/MSSWWEB 只读 canonical 历史/任务中心；没有投递意图 |
| 企微 push | `unified-artifact-delivery` | 只有 WECOM；消息级 `message_delivery`；`UNKNOWN` 不自动重发 |
| Job 的推送目标 | 本需求声明、对方实现路由校验 | `delivery_intent = NONE | PUSH`，`delivery_route_id` 冻结；**独立于创建入口** |
| `write_artifact`/`deliver_artifact` | `unified-artifact-delivery` | `AGENT_TURN` 的 `tools_allow` 包含它们；附件进入当前消息 |
| 任务中心 `deliveries[]` | `unified-artifact-delivery` | `deliveries=[]` 只在 `delivery_intent=NONE`；push 已请求但 outbox 未激活是 `PENDING`，不能显示“仅 Web” |
| 试跑产物 | 两者 | 试跑走同一条消息/附件/push 链路，用户看到真实文件 |
| `MEDIA:` | `unified-artifact-delivery` | 仅 P1 兼容层；本需求的 `SKILL_PLAN` 要求写 `SKILL_OUTPUT_DIR`，不依赖 `MEDIA:` |

**直接重叠点**：`unified-artifact-delivery` 的 TASK「Schedule 路由独立 + 目标选择」会改 `schedule_drafts.py`/`schedule_confirm.py`/Schedule 表单；本需求的 Job/Payload/Authoring 也读 `delivery_intent`/`delivery_route_id`。**这两个文件只能有一个 owner**。建议：

- `unified-artifact-delivery` 拥有 `schedule_drafts.py`/`schedule_confirm.py` 的入口相等限制移除、目标列表 API、Spec Delta；
- 本需求拥有 `SchedulePlan`/payload/authoring/test run，只消费 `delivery_intent`/`delivery_route_id` 契约；
- 本需求的 TASK-005/007 依赖对方的 Schedule 路由 TASK；不要在两边各改一遍。

**是否合并需求**：

- **不建议合并 PRD**：本需求解决“任务怎么推断/执行/调度”，对方解决“消息/附件怎么 pull/push”；owner、迁移面、验收不同。
- **必须合并集成点**：一份共享契约（`MessagePayload`/`AttachmentRef`/`SurfacePlan`/`MessageDelivery`/`TaskArtifactRef.deliveries`/`JobDelivery`）+ 一个联合 E2E（Web 创建定时任务推企微；企微创建任务 Web 可读；直接对话按来源；无绑定 NONE；无变化静默 + 附件）。
- **排期**：先冻结共享契约；对方先落 Artifact/Message/Push 和 Schedule 路由；本需求在其上实现 Job/Payload/Authoring/试跑/提升。
- **合并条件**：如果同一 owner、同一发布、共享 migration 和测试 harness，可以合成一个 program；否则保持两个需求 + 一个集成 TASK。

### 11.9.1 已确认的跨需求契约决策

以下 5 条按“同一 owner、统一发布、两个需求分开拆任务、最后一起做”确认：

| # | 决策 | 结论 |
|---|---|---|
| 1 | `logical_file_id` 作用域 | `tenant + owner(run/task id) + attempt + 产出调用身份 + 规范路径`。**每一次执行尝试都是新的逻辑文件**；只有同一 attempt 内显式 `append_artifact` 才产生 version；跨天/跨执行/跨重试都不聚合成版本链。日报每天、每次重跑都是新产物。 |
| 2 | 无变化静默 + 附件 | 用 `record_result(no_change)` 表达“本轮无变化”，由平台抑制文本；不使用 `NO_REPLY` 这类内部 token。允许 `text` 为空、`attachments` 非空；附件照常 pull/push。用户可见文本必须是直白语言。 |
| 3 | Schedule 路由 TASK owner | `unified-artifact-delivery` 拥有 `schedule_drafts.py`/`schedule_confirm.py` 的入口相等限制移除、目标列表 API、`worker/harness-worker#RULE-003` Spec Delta；本需求拥有 `SchedulePlan`/payload/authoring，只消费 `delivery_intent`/`delivery_route_id`。本需求 TASK-005/007/011 依赖对方的 Schedule 路由 TASK。 |
| 4 | `deliveries[]` 语义 | `deliveries=[]` 只在 `delivery_intent=NONE`（或没有 push 路由）时出现；push 已请求但 outbox 未激活 = `PENDING`；outbox 失败 = `FAILED`；UI 不能把 PENDING/FAILED 显示成“仅 Web”。 |
| 5 | 两个需求的交付方式 | 不合并 PRD；两个目录、两份任务文档、统一发布；共享 `MessagePayload`/`AttachmentRef`/`SurfacePlan`/`MessageDelivery`/`TaskArtifactRef.deliveries`/`JobDelivery`；一个联合 E2E。 |

### 11.10 已决策问题### 11.10 已决策问题

| 编号 | 问题 | 结论 |
|---|---|---|
| OPEN-06 | 用户是否显式选 mode？ | 否；agent 推断，卡片用业务语言；高级可看 payload |
| OPEN-07 | 固定任务默认走 SKILL_PLAN 还是 AGENT_TURN？ | 默认 SKILL_PLAN；无法编译/需要判断才 AGENT_TURN |
| OPEN-08 | 是否保留旧 SKILL/AGENTIC 双读？ | 否；开发期直接迁移为 SKILL_PLAN/AGENT_TURN |
| OPEN-09 | 是否本次做 session 目标？ | 做 isolated/current/session/main 的最小闭环；复杂会话策略后续 |
| OPEN-10 | 是否本次做静默/scratch？ | 做 record_result(no_change) 与上次结果；scratch 可 P2；用户可见文本必须直白 |
| OPEN-11 | 与 unified-artifact-delivery 合并？ | 不合并 PRD；共享契约 + 联合 E2E |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 工具层失败 | 工具调用本身抛异常、被策略拒绝或返回错误封套；transport/operation 失败 |
| 内容层失败 | 工具调用 transport 成功，但执行结果的 `business_status` 表达业务失败（如 skill FAILED/非零退出/结构化业务错误） |
| 证据采集状态 | `CONFIRMED_FAILURE`（确认失败）/ `CONFIRMED_NONE`（确认无失败）/ `NOT_OBSERVED`（未采集到业务结果）/ `UNKNOWN`（无法判断） |
| NOT_APPLICABLE | 诊断/读取工具没有业务失败语义；退出码/输出只是结果证据 |
| 事实块 | 平台在收尾前生成的确定性结构化事实，不依赖模型记忆 |
| TerminationSnapshot | runner 交给 runtime 的终止快照：计数、失败状态、待执行调用、budget 类型 |
| decisive failure | 与本次终止原因关联的失败；区别于历史上最近一次 `last_failure` |
| 收尾回合 | 终止后禁用工具、向用户给出结论的平台/模型步骤 |

---

*文档结束*
