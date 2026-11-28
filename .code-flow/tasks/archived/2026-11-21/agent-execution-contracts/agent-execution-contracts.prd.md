# PRD: Agent 执行契约统一（工具身份、结果、上下文与 MCP）

> **文档编号**: PRD-2026-11-21-02
> **文档版本**: v0.2
> **创建日期**: 2026-11-21
> **产品负责人**:
> **状态**: 评审中

**评审边界说明**:

- **需求基线**: 第 2-8 章（背景/用户/功能/非功能/范围/依赖/已有规范约束）→ 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs
- **本需求是内部执行契约的破坏性统一**：不保留执行别名、执行双协议/双写/双读、v1 运行时读取分支或过渡适配器；开发阶段 cutover 直接删除旧事件，旧会话不续接。升级边界按第 5 章 NFR-COMPAT 处理。

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、Spec/Rule（已有规范引用）

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
| v0.1 | 2026-11-21 | code-flow | 初始草稿：基于 8 项执行链路缺陷与本地最小复现，统一 Runtime/Worker 的工具身份、结果、上下文与 MCP 契约 |
| v0.2 | 2026-11-21 | code-flow | 按架构复查补齐：真实执行调用持久化与恢复状态机、来源化 ContextPlan 与受保护预算、摘要覆盖水位、工具完成事件、Worker 统一执行路由、MCP 重放/审计/有界性、升级后旧历史读取契约 |

---

## 2. 背景与目标

### 2.1 问题陈述 [必填]

| 维度 | 内容 |
|------|------|
| **问题描述** | Runtime 与 Worker 共用 `AgentRunner` 循环，却没有完整共享工具身份、结果语义、上下文边界和恢复规则。已确认 8 个真实缺陷：① 关闭 Skill 脚本调试后，隐藏工具名 `run_skill_script` 仍可执行，能力开关可被绕过；② MCP 正常返回 JSON 数组/`null`/数字/字符串时，`tool_failure_outcome` 在非对象上调用 `.get()`，成功结果被改判成 `AttributeError` 失败，且显式 `retryable:false` 会被翻回可重试；③ 默认 `history_budget_messages=40` 下，14 轮/27 次工具调用即可让 `trim_history` 丢掉唯一的 USER 消息与早先 `load_skill` 正文，同时 `compact_history` 的元组求值顺序让 snip 覆盖 micro 的真实效果，摘要层又基于已裁剪历史工作；④ Worker 在提示词里承诺 `load_skill`/`read_skill_resource`/`search_skills`/`read_attachment`，Registry 却没有这些工具，定时 Agent 无法读取 Skill 正文，外置的大结果也无法回读；⑤ assistant 事件持久化模型工具名（`skill__…`），Worker 恢复却用内部名（`skill::…`）比较，提交后、回合收口前崩溃的调用既不会重提交也不会取回结果；⑥ 首次确定性失败只设置 `no_progress`，批次循环在下一批开始前不检查该状态，后续串行写工具仍会执行；⑦ Runtime 与 Worker 各有一套 MCP 实现，Worker 在 404 后复用过期会话、没有 Egress 策略/审计，且同一个 `isError` 在两侧得到不同的错误码与可重试性；⑧ 两侧 `_content_text` 只保留 `type=text`，MCP 图片与带正文的嵌入资源被静默变成空串。 |
| **当前替代方案** | 逐点修补：保留隐藏别名、保留字符串结果上的 JSON 形状推断、Runtime/Worker 各自维护 MCP 与部分工具、恢复时按宿主事件自行拼历史。单点能过测试，但共享契约继续分叉，同一类缺陷会在另一个宿主或另一条入口复发；现有测试分别覆盖 micro/snip、单工具失败、内部工具名恢复和 Runtime MCP，组合链路没有覆盖。 |
| **触发原因** | 2026-11-21 对执行链路的代码审查与本地最小复现（micro 实际未生效、44 条消息丢 USER、MCP 数组触发 `AttributeError`、Worker 三次调用只 initialize 一次、图片/资源变空串、Runtime/Worker `McpToolError` 分叉）。这些问题已经影响对话、定时任务和平台审计，不能继续按“兼容层 + 单点补丁”演进。 |

**架构复查补充（v0.2）**：仅统一名称与类型仍不足以安全恢复或避免重复副作用。必须同时解决：

- **真实执行调用持久化**：外部执行前落库经过默认值归一化、schema 校验、`PRE_TOOL_USE` Hook 和授权后的调用；恢复以该事实为准，不以 assistant 原始参数重放。
- **恢复状态机**：operation 已登记但子 Task 未创建、首轮尚无 checkpoint、同步工具执行后结果未知，都必须有明确处置；operation 与执行载体必须幂等同时存在。
- **MCP 重放语义**：`retryable` 不能只看错误码；必须区分请求是否已发送、结果是否确定、Owner 是否声明 `retry_safety`；重新初始化会话与重新执行业务调用分别决策。
- **来源化上下文**：当前 Run 的用户输入、消息来源、活跃 Skill 集合必须显式传入；不能用“首条/最后一条 USER”推断；受保护上下文必须有字节预算和超限行为。
- **摘要覆盖水位**：摘要只覆盖可摘要历史，`covers_up_to_seq` 必须是该历史的边界；连续摘要要合并旧摘要事实，不能跳过 pinned/working 事件；pinned 通过 protected event ids 回载。
- **工具完成事件**：outcome、多个产物角色、追加图片消息和失败/止损事实都必须持久化并可重建；进程内 typed、落库后字符串会再次分叉。
- **Worker 统一执行路由**：`execute` 必须把 `skill_key + tool` 映射回冻结 allowlist、每个 Skill 的 schema 与 `retry_safety`，并区分“只有说明”的技能。
- **MCP 审计事实**：HTTP 200 + `isError`/RPC error/ID 错配必须在解码校验后记为逻辑失败；HTTP attempt observer 不能替代逻辑审计。
- **开发阶段 cutover**：本需求上线是破坏性切换；cutover 时删除旧 `runtime.canonical_event` / `task.task_event` 数据（开发库可整体重置），不迁移、不保留 v1 读取分支；旧会话不再续接。

---

### 2.2 目标与价值 [必填]

| 维度 | 内容 |
|------|------|
| **核心目标** | 用三份共享契约（能力、调用与结果、恢复与上下文）统一 Runtime/Worker 的工具执行链路；删除隐藏入口、字符串成败推断和重复 MCP 实现，使每个事实只在一个层判定。 |
| **预期价值** | 消除工具成功/失败的误判与重复副作用；长任务与定时任务不再丢失用户目标、Skill 指令和可回读结果；关闭的能力不能被绕过；MCP 会话、Egress 审计与错误分类在两个宿主一致；后续新增工具或宿主只需装配端口，不再复制执行链路。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 已确认执行链路缺陷 | 8 项 | 0 项 | 对应回归用例全绿 |
| 隐藏可执行工具名 | 至少 `run_skill_script` 1 个 | 0 个 | Registry 只暴露声明的 `ToolDefinition.name` |
| 工具结果 JSON 形状推断 | 全局 `tool_failure_outcome` | 0 处 | 代码检索 + 契约测试 |
| 默认预算下目标/Skill 正文保留 | 会丢失 | 100% 保留 | 44 条消息/14 轮/27 次调用夹具 |
| 崩溃恢复重复业务调用 | 存在窗口 | 0 次 | “提交后、收口前崩溃”集成用例 |
| 恢复覆盖窗口 | argv/Hook 归一化、operation/子 Task、无 checkpoint 3 个未覆盖窗口 | 0 个未覆盖 | 3 条隔离恢复用例 |
| 连续两次摘要后恢复 | 可能跳过 pinned/当前工作 | 0 次跳过 | 两次摘要 + 新 Run/恢复用例 |
| cutover 后旧事件残留 | 未定义 | 0 条旧格式事件 | 删表/删除校验 |
| MCP 逻辑审计 | HTTP 200 + isError/RPC/ID 错配可能记成功 | 0 次伪成功 | 审计探针用例 |
| 提示词承诺但未注册的能力 | Worker 4 个 | 0 个 | 装配期能力校验 + 测试 |
| Runtime/Worker MCP 行为差异 | 会话、Egress、错误码、内容 4 类 | 0 类 | 同一套共享 MCP 契约测试 |

---

## 3. 用户与场景

### 3.1 目标用户 [必填]

| 维度 | 内容 |
|------|------|
| **用户画像** | 对话 Agent 使用者；定时/后台 Agent 使用者；Skill 作者与运营；平台运维与安全审计；Runtime/Worker 开发者。 |
| **使用场景** | 用户在对话或定时任务中让 Agent 调用 Skill、MCP、附件与工作区工具；任务可能运行 10+ 轮、产生大结果或在提交后崩溃；平台需要关闭 Skill 脚本调试、审计 MCP 出站并保证租户/owner 隔离。 |

---

### 3.2 用户故事 [必填]

> 格式: As a [角色], I want to [操作], so that [价值]

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为对话用户，我希望工具成功不会被本地误判为失败、失败也不会被当成功，以便不重复执行外部动作、不丢失结果 | P0 |
| US-02 | 作为长任务用户，我希望 Agent 始终保留我的原始目标、当前指令和正在使用的 Skill 说明，以便压缩后仍能正确继续 | P0 |
| US-03 | 作为定时 Agent 用户，我希望后台任务能读取 Skill 正文/资源和已外置的大结果，以便依赖 SKILL.md 步骤的技能真正可执行 | P0 |
| US-04 | 作为平台运维/安全，我希望关闭的 Skill 脚本调试不能被隐藏工具名绕过，MCP 调用有统一会话恢复、Egress 审计和错误分类 | P0 |
| US-05 | 作为 Runtime/Worker 开发者，我希望两个宿主共用同一套工具身份、结果、上下文和恢复契约，以便新增能力时不再复制执行链路 | P0 |
| US-06 | 作为 Skill 作者，我希望调度 Agent 按 SKILL.md 和 references 执行，而不是只看到名称和描述 | P1 |

---

## 4. 功能需求

### 4.1 功能清单 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|--------|---------|---------|--------|-------------|
| FEAT-01 | 统一调用身份 | `ToolCall` 同时携带内部名与模型名；事件、审计、checkpoint、恢复只用内部名，provider 只用模型名 | P0 | US-01、US-05 |
| FEAT-02 | 类型化工具结果与失败 | handler 返回 `ToolResult`，失败用 `ToolError`/`outcome` 表达；pipeline 不再解析正文 | P0 | US-01、US-05 |
| FEAT-03 | 批次止损 | 确定性失败后在批次边界停止后续串行调用，并显式记录未执行结果 | P0 | US-01 |
| FEAT-04 | 统一上下文计划 | 显式当前输入/来源/活跃 Skill；`ContextPlan` 统一 prefix/pinned/older/working 与受保护预算；摘要覆盖水位可重建 | P0 | US-02 |
| FEAT-05 | 能力装配与唯一执行入口 | 能力声明 + 装配校验；删除隐藏别名；Worker 补齐 Skill 指令/资源/检索、附件读取与 `execute` 路由 | P0 | US-03、US-04、US-06 |
| FEAT-06 | 共享 MCP 会话、治理与内容 | Runtime/Worker 共用 MCP 协议/会话/Egress/重放语义/逻辑审计/有界内容；图片与嵌入资源转 artifact 或显式标记 | P0 | US-01、US-03、US-04 |
| FEAT-07 | 规范与文档收敛 | Spec/docs 按新契约重写；删除别名、兼容层、重复实现的描述 | P1 | US-05 |

#### FEAT-01: 统一调用身份

- **描述**: 工具调用在模型边界完成一次身份解析，之后所有持久化与执行只使用稳定内部标识。
- **验收标准**:
  - [ ] `ToolCall` 同时包含 `name`（registry/audit/checkpoint）与 `model_name`（provider 回放）；二者不互相替代。
  - [ ] `on_assistant_turn` 收到已解析调用；assistant 事件同时持久化两个名称。
  - [ ] **外部执行前**持久化经过默认值归一化、schema 校验、`PRE_TOOL_USE` Hook 与授权后的调用事实（`call_id`、内部名、`args_hash`、脱敏 `args_preview`、来源/技能/工具/`retry_safety`）；原始归一化参数只进入 operation 行；恢复以该事实与 operation 行为准，不重放 assistant 原始参数。
  - [ ] 没有 operation 的 prepared 调用说明尚未外部执行；恢复返回 `NOT_EXECUTED`，不重放原始参数。
  - [ ] 参数归一化在 `ToolDefinition` 声明（如省略 `argv` → `[]`），handler 不再隐式补默认值；Hook 改参后再次归一化并落库。
  - [ ] Worker 与 Runtime 的恢复都按 `call_id + name` 重提交/取回，不再按宿主事件自行猜名称。
  - [ ] “operation 已登记、子 Task 未创建”恢复时，通过 operation owner 的恢复视图取得冻结 `arguments`/`input_hash`，幂等补齐执行载体；不会等待一个没人执行的操作。
  - [ ] 恢复输入包含 assistant 声明；对每个 `call_id` 对账：声明未 prepared、prepared 未完成、completed 无 prepared（仅限未知工具/schema/授权拒绝）都有明确终态，不把“有声明”当 `NO_FACTS`。
  - [ ] 异步 handler 的第一持久步骤是登记 operation+执行载体；没有 prepared 事实或 operation 失败时不得先做外部副作用。
  - [ ] 首轮尚无 checkpoint 但已存在 assistant 声明/prepared/operation/结果事件时，仍进入恢复核对，不直接当作首次执行。
  - [ ] 同步工具在外部执行后、结果落库前崩溃时，恢复标记 `OUTCOME_UNKNOWN`，**不承诺原调用重放**；模型可后续发起新调用，但 recovery 不自动重做。
  - [ ] “提交后、回合收口前崩溃”用例：恢复正常，不创建第二个 operation，历史中每个 tool_call 都有配对结果。
  - [ ] provider 历史仍使用模型名，请求不被供应商拒绝。

#### FEAT-02: 类型化工具结果与失败

- **描述**: 工具结果不再靠 JSON 形状推断成败；成功正文是数据，失败是类型化事实。
- **验收标准**:
  - [ ] `ToolHandler` 返回 `ToolResult`（content + outcome + follow_ups + artifacts）；失败由 `ToolError` 或 `outcome.is_failure` 表达。
  - [ ] pipeline 不再调用 `tool_failure_outcome`/正文字段推断；`outcome_parser` 机制删除。
  - [ ] MCP 返回 `[1,2,3]`、`null`、`42`、`"ok"`、`{"error":"data"}` 时均按成功数据处理，模型收到原文。
  - [ ] MCP `isError` 得到稳定错误码与显式 `retryable`；显式 `retryable:false` 不被翻转。
  - [ ] 模型侧仍收到统一 `{"error": {code,message,detail,retryable}}` 形状，但由 typed outcome 生成。
  - [ ] 工具完成事件使用版本化共享契约，持久化 `status`、`outcome`、`content`/预览、`artifacts[]`（含 role）、`follow_ups[]`（含图片引用）；恢复能重建同样的 tool 消息、失败事实和止损状态。
  - [ ] 外置正文的 artifact role=`RESULT`；图片/报告等业务附件使用独立 role，不能用一个 `artifact_id` 混表。
  - [ ] 崩溃发生在完成事件之前时，按 prepared 事实判定“未知/可重放/待等待”，不把缺失结果当成功。

#### FEAT-03: 批次止损

- **描述**: 确定性失败是 Run 级停止条件，必须在下一个批次开始前生效。
- **验收标准**:
  - [ ] 第一个工具返回 `PLATFORM_AUTH_REJECTED` 后，后续串行写工具 handler 零调用。
  - [ ] 未执行调用收到明确的 `TOOL_NOT_EXECUTED` tool 结果，与 `TOOL_BUDGET_EXHAUSTED` 区分。
  - [ ] 已经发出的并行只读调用正常收口，不因 stop 丢失结果。
  - [ ] 终态仍为 `NO_PROGRESS`，`last_failure` 保留首次确定性失败。

#### FEAT-04: 统一上下文计划

- **描述**: 压缩边界由一个 `ContextPlan` 统一计算，所有压缩层消费同一份计划。
- **验收标准**:
  - [ ] `ContextPlan` 接收**显式的当前 Run 输入**（消息 id、内容、来源）与**活跃 Skill 集合**，不以“首条/最后一条 USER”推断目标；历史 USER、工具追加图片、后台结果等 USER 角色消息按 `origin` 区分。
  - [ ] 历史消息携带 `source_event_seq`；`coverage_seq` 由 `older` 组内事件 seq 的最大值计算，工具组包含 assistant 声明与其全部 tool 结果的事件 seq。
  - [ ] 恢复时 `active_skills` 从已完成的 `load_skill` 结果重建，不只依赖 runner 内存；pinned 的活跃 Skill 在新 Run 中仍成立。
  - [ ] `ContextPlan` 明确 `prefix`（system/memory/summary）、`pinned`（当前 Run 输入 + 每个活跃 Skill 最近一次 `load_skill`）、`older`（当前输入之前的可摘要历史）、`working`（当前输入之后的工具过程）；不再把当前输入后的整个工具过程设为不可压缩的 `current_turn`。
  - [ ] `trim_history`、`snip`、`micro` 只处理 `working`；`summary` 只处理 `older`；不丢 `prefix`/`pinned`；最近 tail 由各层按预算保留。
  - [ ] 44 条消息/14 轮/27 次调用的默认预算夹具中，当前 Run 输入与每个活跃 Skill 的最近正文均保留；多轮会话历史 USER 不会替代当前输入。
  - [ ] `compact_history` 按 micro → snip 顺序基于上一层输出计算；micro 报告节省字节时，最终历史必须真的出现占位符。
  - [ ] 受保护上下文有 UTF-8 字节总预算：当前输入始终保留；活跃 Skill 按最近优先纳入，超预算时被省略的 Skill 有可见提示/指标，不静默丢失。
  - [ ] 摘要层基于当前 Run 输入之前的 `older` 生成；`pinned` 不受压缩；摘要只替换 `older` 段。
  - [ ] 摘要 `covers_up_to_seq` 等于 `older` 的实际边界（剔除 pinned 后），不是会话最大 seq；摘要事件同时持久化 `protected_event_ids`，重建时即使 `seq <= covers` 也按 id 回载 pinned。
  - [ ] 连续两次摘要后，新 Run/恢复不会跳过 pinned、当前输入或未进入摘要的 `working`；新摘要合并旧摘要事实，覆盖水位单调不回退，`protected_event_ids` 替换为当前活跃集合。
  - [ ] 摘要未启用/失败时，`older` 仍按条数预算做尾部兜底裁剪；不丢 pinned/working，丢历史有可见指标。
  - [ ] 任何层失败/未触发时保留原历史，降级有 warning + 指标，不产生孤儿 tool 消息。

#### FEAT-05: 能力装配与唯一执行入口

- **描述**: 模型看到的能力、可执行的能力、能读取/交付的能力由同一份声明驱动，装配期校验。
- **验收标准**:
  - [ ] 删除 `ToolRegistry` 兼容命名空间与 `execute_skill`/`run_skill_script`/`workspace_shell`/`skill_draft_shell` 隐藏别名；模型唯一入口为 `execute(target=skill|draft|workspace)`。
  - [ ] `enable_run_skill_script=false` 时，任何名为 `run_skill_script` 的调用都是 `TOOL_NOT_FOUND`；不存在绕过路径。
  - [ ] `ToolDefinition.capabilities` + 装配校验：提示词中的目录/正文/资源/回读承诺必须有对应工具。
  - [ ] Worker 注册 `load_skill`、`read_skill_resource`、`search_skills`、`read_attachment`，与 Runtime 同形、同错误码。
  - [ ] Worker 的 `execute(target=skill)` 通过冻结 `SnapshotSkill.key` + `tool` 映射回 allowlist；`SnapshotSkill.key` 是正式冻结字段（不从 `frontmatter.key` 推断），catalog/提示词/路由共用。
  - [ ] 保留每个 Skill 工具的 input schema、`retry_safety` 和调用级授权；只有说明、没有可执行工具的 Skill 返回 `SKILL_NOT_EXECUTABLE`。
  - [ ] Worker 的 Skill 执行结果统一转换为 typed `ToolResult`（status/outcome/evidence），不再压成裸 `result/stdout` 字符串；ASYNC 子 Task 冻结所选工具的 `retry_safety`。
  - [ ] `read_attachment` 只读本 tenant + 本 execution 的产物；跨 Task/跨租户返回 `COMMON_NOT_FOUND`。
  - [ ] 没有生效 Skill 时不注册任何 Skill 工具；没有外置端口时不承诺回读。

#### FEAT-06: 共享 MCP 会话、治理与内容

- **描述**: MCP 协议、会话、Egress、错误分类和内容转换只保留一份实现，两个宿主注入不同端口。
- **验收标准**:
  - [ ] Runtime/Worker 共用同一 `McpClient`/`McpSession`；删除两份 `_McpSession`/`_content_text`。
  - [ ] 任何 MCP 失败都丢弃并关闭该 server 会话；404/会话失效后下一次调用重新 `initialize`。
  - [ ] MCP 调用经共享 Egress transport，`egress_audit` 覆盖 ALLOW/DENY/ERROR；预算/限流生效。
  - [ ] 失败事实分别表达：错误是否可恢复、请求是否已发送、结果是否确定、Owner 是否声明 `retry_safety`；`retryable = recoverable and (not sent or replay_safe)`，`outcome_known` 只用于提示，不自动放行非重放安全工具。
  - [ ] `isError` 不被自动视为可重试；`timeout/connect/5xx/429` 按发送阶段与 `retry_safety` 决策；重新初始化会话语重新执行业务调用分开。
  - [ ] 逻辑 MCP 结果在解码/校验后写审计：HTTP 200 + `isError`/RPC error/ID 错配记为 ERROR；关闭预算时仍写审计。
  - [ ] MCP 响应、SSE 帧、解码媒体与合计内容有界；超限显式失败，不在接收整包后才判断。
  - [ ] MCP 图片/嵌入资源转 artifact 并返回引用/描述；暂不支持的类型有显式标记，绝不返回空串。
  - [ ] MCP 工具 effect 来自冻结 catalog，不再由宿主硬编码。

#### FEAT-07: 规范与文档收敛

- **描述**: Spec/docs 以新代码事实为准，删除兼容层和重复实现描述。
- **验收标准**:
  - [ ] `harness-arch`、`harness-skill`、`harness-mcp`、`harness-snapshot`、`harness-worker` 更新到新契约。
  - [ ] 文档不再出现“保留别名”“默认不注册但可调用”等自相矛盾表述。
  - [ ] 代码检索无隐藏别名、无 `tool_failure_outcome`、无第二份 MCP 会话实现。

---

### 4.2 边缘情况 [可选]

| 场景 | 预期行为 |
|------|----------|
| MCP 返回 list/null/number/string，或对象内带 `error` 数据键 | 按成功数据交给模型；不触发 pipeline 异常或失败分类 |
| 工具显式返回 typed 非重试失败 | 确定性失败，首次即停；不被推断逻辑翻转 |
| 提交后、回合收口前崩溃 | 按 prepared 事实 + `call_id + 内部工具名` 重提交；operation 幂等；历史补齐 tool 结果 |
| prepared 存在但 operation 未登记 | 尚无外部动作；返回 `NOT_EXECUTED`，不重放原始参数 |
| assistant 已声明、尚未 prepared | 逐 call 补 `NOT_EXECUTED`；不把声明窗口当首次执行 |
| 未知工具/schema/授权拒绝 completed 无 prepared | 保留拒绝事实；不补 prepared、不执行 |
| operation 已登记、子 Task 未创建 | 恢复幂等补齐子 Task；不等待无人执行的操作 |
| 首轮尚无 checkpoint | 仍核对已发生的 prepared/operation/结果事实，不直接当首次执行 |
| 同步工具执行后、结果落库前崩溃 | 标记结果未知；按 `retry_safety` 决定是否可重试，不默认重做 |
| 确定性失败后还有串行写工具 | 写工具不执行；返回 `TOOL_NOT_EXECUTED`；终态 `NO_PROGRESS` |
| 44 条消息、无 USER 在尾部 | 当前 Run 输入与活跃 Skill 组仍保留；只裁 `working`/`older` |
| 多轮会话 + 当前输入后长工具过程 | 历史 USER 不替代当前输入；工具过程可压缩，最近 tail 保留 |
| 当前输入后追加图片/后台结果 | 按 `origin` 识别，不作为用户边界；不误 pin |
| 受保护上下文超预算 | 当前输入保留；活跃 Skill 最近优先，超限有可见提示/指标 |
| micro 触发、snip 不触发 | 最终历史真实出现 micro 占位符；字节节省与实际一致 |
| 连续两次摘要 | 旧摘要事实合并；`covers_up_to_seq` 单调且只覆盖被摘要历史；pinned/当前工作不被跳过 |
| Worker 只有 SKILL.md、没有脚本 | `load_skill` 可读正文；`execute` 返回 `SKILL_NOT_EXECUTABLE` |
| Worker 工具结果被外置 | `read_attachment` 可按 artifact_id 读回；非文本返回明确不支持 |
| MCP 404/session 过期 | 首次调用记为 session expired；下一次调用重新 initialize 后成功 |
| MCP 写工具 timeout/connect/5xx | 已发送且结果未知 + NON_RETRYABLE 不自动重试；READ_ONLY/IDEMPOTENT 才允许 |
| MCP 只返回图片或嵌入资源 | 生成 artifact/显式描述；不返回空串 |
| MCP 响应/SSE/媒体超限 | 显式失败，不整包读入后再判断 |
| 关闭 Skill 脚本调试 | 隐藏名称不可解析；`execute` 也不得执行 script |
| cutover 后旧会话 | 旧事件已删除；旧会话不再续接；用户从新会话开始 |
| cutover 后回滚 | 不做数据兼容；只能重置开发库并回退镜像；新 v2 数据不得交给旧代码 |
| 没有生效 Skill / 没有 MCP / 没有附件 | 不注册对应工具，不产生空能力承诺 |

---

## 5. 非功能需求 [可选]

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-REL-01 | 可靠性 | 压缩、工具结果解析、MCP 内容转换失败不得让 Run/Task 失败；降级必须保留原历史/原结果并留下 warning + 指标 |
| NFR-SEC-01 | 安全 | 关闭的能力不可被隐藏名称绕过；MCP 调用写 `egress_audit`；附件读取按 tenant + execution/owner 校验；密钥不得进入事件、审计、Prompt 或工具结果 |
| NFR-OBS-01 | 可观测性 | 未执行工具、压缩层触发/降级、MCP 会话重建、Egress 结果、能力装配失败都有稳定指标或日志；不记正文与凭据 |
| NFR-PERF-01 | 性能 | MCP 内容转换、pinned 上下文、工具结果外置/回读均有界；不引入无界内存或额外网络往返 |
| NFR-COMPAT-01 | 兼容性 | 开发阶段破坏性切换：不保留别名、双协议、双写、执行双读、v1 运行时读取分支；cutover 时删除旧 `runtime.canonical_event` / `task.task_event` 数据（开发库可整体重置），旧会话不再续接。cutover 后只向前修复；回滚只能重置开发库并回退镜像 |
| NFR-TEST-01 | 可验证性 | 覆盖“模型实际名称 → 执行 → 持久记录 → 崩溃恢复 → 结果回读”整条链路；Runtime/Worker 共用同一套 MCP 契约测试 |

---

## 6. 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `packages/agent-core` 的工具身份/结果/上下文契约；Runtime 与 Worker 的工具装配、prepared 调用持久化与恢复；共享 MCP 客户端、会话、Egress、逻辑审计、有界内容；Skill 指令/资源工具与附件回读；Worker `execute` 路由与结果转换；开发阶段破坏性 cutover；相关 Spec/docs 与测试 |
| **非范围（Out of Scope）** | Console/IM/前端 UI；MCP catalog 发现与缓存；Skill 导入/校验/审批；模型 provider 协议本身；新增压缩层；动态插件/工具发现；跨租户/跨用户 artifact 共享；Worker 的 PDF/图片抽取链路（`view_image` 仅图片直读）；旧事件的执行双读；旧 payload 迁移/兼容；旧会话续接 |
| **前置假设** | 允许内部破坏性契约变更；开发库旧事件可删除/整体重置；Artifact Store 仍是共享 RWX PVC；PostgreSQL/Redis 拓扑不变；发布前可暂停调度生产、停止 claim 并处置等待任务；Runtime/Worker 通过 HTTP 契约通信，不引入跨 schema 写 |

---

## 7. 依赖与风险 [可选]

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|--------|----------|--------------|---------|
| Agent Runtime | 工具装配、MCP 端口、附件读取端点、事件写入 | 与各 TASK 同步 | 高 |
| Agent Worker | Skill 指令/资源工具、附件读取、事件恢复 | 与各 TASK 同步 | 高 |
| Shared packages | `ToolCall`/`ToolResult`/`ContextPlan`/`McpClient` | 前置 TASK | 高 |
| Console | 无新增 UI；内部契约不变 | — | 低 |

### 7.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|----------|
| RISK-01 | `ToolResult` 迁移触及全部 handler，遗漏字符串返回 | 运行时类型错误或旧语义残留 | 协议类型 + mypy + 全量 handler 清单 + 契约测试 |
| RISK-02 | 删除隐藏别名破坏持久化 Hook/验收夹具 | 旧 Hook 或测试失败 | 同一变更内更新所有调用点与 fixture，不做别名兼容 |
| RISK-03 | 开发阶段 cutover 删除旧事件后旧会话不可续接 | 用户从新会话开始 | 发布说明明确；cutover 后只向前修复；回滚需重置开发库 |
| RISK-04 | `ContextPlan` 调整压缩行为导致 token 使用上升 | 成本/延迟上升 | 当前输入 + 每 Skill 最近正文；字节预算/超限提示；对比字节与层触发率 |
| RISK-05 | 共享 MCP 重构引入宿主差异 | 会话/审计行为回退 | 同一套契约测试在 Runtime/Worker 各跑一遍；先会话/Egress，后内容 |
| RISK-06 | Worker 附件读取扩大数据面 | 跨 Task/租户泄露 | 端点强制 tenant + execution/owner 校验；跨域 404 不泄露存在性 |
| RISK-07 | prepared 事实未落库就外部执行 | 恢复无法证明调用发生过 | 写失败中止；不允许无持久事实的外部调用 |
| RISK-08 | operation 与子 Task 只成功一半 | 永久等待 | `AsyncCallDispatcher.ensure` 幂等补齐；恢复状态机 |
| RISK-09 | 摘要覆盖水位超过被摘要历史 | pinned/当前工作被跳过 | `coverage_seq` + protected reload；连续摘要单调校验 |
| RISK-10 | MCP HTTP 200 逻辑失败被记成功 | 审计失真 | 解码/校验后逻辑审计；`McpAuditPort` |

---

## 8. Existing Spec Constraints

> 仅列出与本次范围直接相关的 Rule；最终以 `cf-task:align` 的 Spec refs 为准。

| Spec/Rule | 约束 | 对范围/验收的影响 | 状态 |
|-----------|------|------------------|------|
| `snapshot/harness-snapshot#RULE-001` | 共享 Agent 内核只依赖中立契约与端口，不得复制简化循环或按宿主分支；checkpoint 覆盖完整回合与异步挂起；压缩失败保留原历史；批量外置失败整轮回滚 | FEAT-01/02/03/04 的架构基线；跨宿主实现必须共用契约 | applied |
| `arch/harness-arch#RULE-001` | `packages/*` 不依赖 app；跨部署单元只走 HTTP 契约 | 共享契约/MCP 模块的落点；不得把 Runtime 逻辑反向依赖进 Worker | applied |
| `artifact/harness-skill#RULE-skill-001` | RWX PVC、不可变写入、缓存 singleflight、禁止从 NFS 直接执行 | `read_attachment` 从共享 PVC 读取；Skill 资源读取沿用缓存 | applied |
| `artifact/harness-skill#RULE-001` | 目录双上限、截断可见、`search_skills` 发现路径 | Worker 必须提供 `search_skills`，否则不得承诺该提示 | applied |
| `artifact/harness-skill#RULE-002` | 非 Skill 产物前缀不得复用 `skills/` | MCP 图片/资源 artifact 的前缀与清理策略 | applied |
| `artifact/harness-skill#RULE-003` | `NfsArtifactStore.resolve` 必须做越界防护 | Worker 附件读取必须经 `resolve`，不得拼路径 | applied |
| `artifact/harness-skill#RULE-004` | invocation_contract 冻结与校验 | 不改执行契约；`execute` 入口保持现有校验 | applied |
| `artifact/harness-skill#RULE-005` | 默认入口契约校验与 `SKILL_INVOCATION_INVALID` | typed result 必须保留该错误码与 retryable | applied |
| `mcp/harness-mcp#RULE-mcp-001` | 仅 Streamable HTTP；catalog 冻结；MCP 进入统一 ToolRegistry | 共享 MCP 不得运行期 `tools/list`；注册仍用内部名 | applied |
| `mcp/harness-mcp#RULE-mcp-002` | 认证三态；密钥不进 Snapshot/审计/日志 | 共享 MCP 的 header 组装与审计脱敏 | applied |
| `worker/harness-worker#RULE-001` | 异步调用按 tenant/owner/call 身份唯一；提交与结果可重放；恢复不丢不重 | FEAT-01/08 的 prepared 身份与恢复状态机；Worker 重提交语义 | applied |
| `worker/harness-worker#RULE-005` | ASYNC Skill 子 Task 不是 BATCH 子任务，父执行继续等待 | 不改 fan-in；恢复同时确保 operation 与子 Task | applied |
| `persistence/harness-data`（摘要/transcript） | 摘要文本是权威历史，transcript 逐字存档，覆盖边界用于重建 | 摘要 `covers_up_to_seq` 必须是可摘要历史边界；连续摘要合并 | applied |
| `persistence/harness-data#RULE-data-001` | 不可变历史/回执按身份完整唯一 | 开发阶段 cutover 直接删除旧事件；不迁移、不保留旧 payload 分支 | applied |
| `secret/harness-secret#RULE-001` | 密钥不得进入 CanonicalEvent、tool_call_audit、egress_audit、Prompt | 共享 MCP/事件/结果契约的脱敏边界 | applied |
| `secret/harness-secret#RULE-003` | 平台失败原因离开适配器前擦除 CSRF/Cookie | typed failure 的 message 仍走现有脱敏 | applied |
| `api/harness-api#RULE-api-001` | 内部 API 统一封套与错误码 | 附件读取端点的响应/错误形状 | applied |
| `test/harness-test#RULE-test-001` | 跨 API/DB/Runtime 关键流程必须集成/契约测试；浏览器流程人工执行 | FEAT-01/06 的恢复与 MCP 契约测试；无 UI 变更 | applied |

---

## 9. 开放问题（需确认后才能进入设计）

| 编号 | 问题 | 建议 | 影响 |
|------|------|------|------|
| Q1 | 是否接受内部破坏性变更：删除全部隐藏别名、事件 v2、prepared 调用落库、开发阶段直接删除旧事件？ | 接受。执行事实不双写/双读；旧会话不续接；cutover 后只向前修复 | 范围/部署 |
| Q2 | MCP 图片/嵌入资源的落点：写入 artifact + 提供 `read_attachment`/`view_image` 引用（推荐），还是本轮只做显式文本标记、暂不落 artifact？ | 落 artifact，复用现有附件通路 | FEAT-06 验收 |
| Q3 | `pinned` 的范围：显式当前 Run 输入 + 每个活跃 Skill 最近一次 `load_skill`（推荐）；是否还要 pin 最近 `read_skill_resource`？ | 当前输入 + 每个活跃 Skill 的最近 `load_skill`；资源按需重读 | FEAT-04 验收 |
| Q4 | Worker `read_attachment` 是否只支持文本类工具结果（推荐），还是本轮就做 PDF/图片抽取？ | 只支持文本；`view_image` 仅图片直读；文档抽取不做 | FEAT-05 范围 |
| Q5 | 共享工具边界：只共享 Skill 指令/资源读取 + Attachment 读取 + MCP，`execute` 保持宿主实现（推荐）；还是本轮把 `execute` 也共享？ | 共享读能力与 MCP；`execute` 保留宿主实现，但统一 schema/路由/结果契约 | FEAT-05 范围 |
| Q6 | 删除别名的范围：只删绕过能力开关的 `run_skill_script`，还是四个隐藏别名全部删除？ | 全部删除；模型只保留 `execute` 一个入口 | FEAT-05 验收 |
| Q7 | 开发阶段 cutover：直接删除旧 `ASSISTANT_TURN`/`TOOL_CALL` 事件（或整体重置开发库），旧会话不再续接；不做迁移/兼容。 | 已确认接受 | 范围/部署 |
| Q8 | MCP 写工具已发送但结果未知且 `retry_safety=NON_RETRYABLE` 时，模型是否允许重试？ | 不允许自动重试；返回“结果未知，请核实”的确定性失败 | FEAT-06 验收 |
| Q9 | 受保护上下文预算的所有者：平台设置（可运营调整）还是进程安全设置（部署统一）？ | 进程安全设置统一上限；当前输入始终保留，Skill 正文按最近优先 | FEAT-04 范围 |
| Q10 | prepared 事件是否只存 `args_hash` + 脱敏 `args_preview`，原始归一化参数只进入 operation 行？没有 operation 的 `PREPARED_ONLY` 返回 `NOT_EXECUTED`。 | 是；避免 canonical event 泄密，且无 operation 说明外部动作未发生 | FEAT-01 验收 |
| Q11 | MCP 响应/SSE/媒体上限的所有者：进程级 `SharedSettings` 还是平台设置？ | 进程级安全上限，统一部署；不在租户级配置 | FEAT-06 范围 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| PRD | Product Requirements Document，产品需求文档 |
| US | User Story，用户故事 |
| FEAT | Feature，功能项（US 的实现载体） |
| NFR | Non-Functional Requirement，非功能性需求 |
| 能力契约 | 模型可见、可执行、可读取、可交付的能力声明与装配校验 |
| 调用身份 | 一次工具调用的稳定内部标识（`call_id + name`），与 provider 的 model name 分离 |
| ContextPlan | 统一计算 prefix/pinned/older/working、覆盖水位与 protected reload 的上下文边界计划 |
| 兼容性设计 | 别名、双协议、双读、过渡适配器等让新旧实现共存的层；本需求明确禁止 |

---

*文档结束*
