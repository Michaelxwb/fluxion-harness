# PRD: Skill 调度与对话调用可调用性

> **文档编号**: PRD-2026-11-20-02
> **文档版本**: v2.0
> **创建日期**: 2026-11-20
> **产品负责人**:
> **状态**: 评审中

**评审边界说明**:

- **需求基线**: 第 2-8 章 → 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、Spec/Rule（已有规范引用）

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 产品经理 | | 需求定义、业务验收 |
| 开发负责人 | | 技术方案确认 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v0.1 | 2026-11-20 | code-flow | 初始草稿（结构化 TaskSpec / Run Ledger / 真实试跑方向） |
| v1.0 | 2026-11-20 | code-flow | 纳入评审 |
| v2.0 | 2026-11-20 | code-flow | 方向调整：试跑改为调度预检；不执行 Skill、不投递；契约只保留调用通道与参数 |
| v2.1 | 2026-11-20 | code-flow | 同一 `invocation_contract` 覆盖对话直接调用；定时任务与对话共用通道/参数校验 |

---

## 2. 背景与目标

### 2.1 问题陈述 [必填]

| 维度 | 内容 |
|---|---|
| **问题描述** | 用户用自然语言描述任务，agent 生成结构化配置：可能是定时任务，也可能是在对话中直接调用 Skill。平台当前没有机器可读的 Skill 调用契约，agent 可能选错调用通道或漏掉必填参数；定时任务照常保存并按 cron 执行，对话调用直接进入执行，直到运行时才报错。 |
| **典型事故** | 2026-11-18：任务以 SKILL 模式保存了 `input_template`（`company_id`/`date`），但 Skill 入口 `run.py` 只读 argv，缺少 `--company-id`，运行期报 `the following arguments are required: --company-id`。agent 随后改成 AGENTIC，又出现 `TASK_NO_PROGRESS`，用户被迫参与多轮参数调试。同样的通道错配也会发生在对话直接调用。 |
| **当前替代方案** | 反复观察失败记录、手工改 prompt/argv、把确定任务改成 AGENTIC。没有创建时校验、没有结构化修复信息、没有通道契约。 |
| **触发原因** | 内网 Skill 既要稳定定时运行，也要在对话中直接调用；当前无法在运行前区分“配置错了”和“业务失败了”。 |

### 2.2 目标与价值 [必填]

| 维度 | 内容 |
|---|---|
| **核心目标** | 把用户自然语言稳定转成正确的 Skill 调用参数（定时任务或对话直接调用）；定时任务在确认前验证，对话调用在启动 Skill 前验证；技术参数错误由 agent 自己修复，用户只处理业务缺口。 |
| **预期价值** | 参数/通道错误从“运行期失败”前移到“创建 Draft / 对话工具调用时发现”；不再用真实试跑做门禁；不再把确定任务降级成 AGENTIC。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|---|---|---|---|
| 有契约 Skill 的调用参数错误在 Draft/Confirm 拦截率 | 未知 | 100% | `schedule_preflight_total{status="FAILED"}` |
| 有契约 Skill 的对话调用参数错误在进程启动前拦截率 | 未知 | 100% | `skill_invocation_guard_total{status="FAILED"}` |
| 新任务首次 Fire admission 成功率 | 未知 | ≥ 95% | Fire `accepted` / 总 Fire（不是业务成功率） |
| 确认前执行 Skill / 产生投递次数 | 未知 | 0 | `task_execution` / `message_delivery` 增量 |
| 参数错误平均修复轮次 | 约 3 次人工调试 | ≤ 1 次 agent 自动修复 | 同一对话内 Draft / tool 重试次数 |
| 用户参与技术参数修复的次数 | 多次 | 0 | 对话中不出现 `--flag` / schema 字段名 |

**明确不承诺**

- Skill 业务是否正确、数据是否准确、输出是否为空、是否超时；这些由 Skill 自己的测试与运行负责。
- 对话直接调用的业务结果仍由 Skill 负责；平台只保证调用参数在进程启动前被校验。
- 确认后 Skill 版本变化仍能成功；版本不兼容会在 Fire 明确失败。

---

## 3. 用户与场景

### 3.1 目标用户 [必填]

| 维度 | 内容 |
|---|---|
| **用户画像** | 平台使用者（通过 agent/CLI 创建定时任务或在对话中直接调用 Skill）、Skill 作者、平台 on-call。 |
| **使用场景** | 用户说“每天 9 点给客户 22921947 生成 MSSP 值守日报”；agent 生成 `SKILL_PLAN`，平台预检通道与参数，通过后出确认卡片。用户也可以直接说“现在给客户 22921947 生成日报”，agent 在对话中直接调用 Skill。 |

### 3.2 用户故事 [必填]

| 编号 | 用户故事 | 优先级 |
|---|---|---|
| US-01 | 作为任务创建者，我希望平台把自然语言转换成正确的 Skill 调用参数，并在确认前校验，避免上线后才报缺参数。 | P0 |
| US-02 | 作为任务创建者，我希望平台不要在确认前真实执行 Skill、不要发送试跑结果或产物。 | P0 |
| US-03 | 作为 Skill 作者，我希望在 Skill 里声明调用通道和参数 schema，让平台在定时任务和对话直接调用时都能校验。 | P0 |
| US-04 | 作为任务创建者，我希望 agent 自己修复参数错误，不要让我看 `--company-id` 这类技术细节。 | P0 |
| US-05 | 作为平台 on-call，我希望 Skill 版本变化或契约变化时，任务在 Fire 明确失败并给出修复提示，而不是静默跑错。 | P0 |
| US-06 | 作为 Skill 作者，我希望 self-creator 指导我写出可执行的 Skill 契约，而不是等我上线后才发现漏了。 | P1 |
| US-07 | 作为对话用户，我希望 agent 在对话中直接调用 Skill 时也按同一契约校验参数，错了让它自己修，不要等脚本报错。 | P0 |

---

## 4. 功能需求

### 4.1 功能清单 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|---|---|---|---|---|
| FEAT-01 | Skill 调用契约 | 在 `SKILL.md` frontmatter 声明 `invocation_contract`：版本、调用通道、参数 schema；定时任务与对话调用共用 | P0 | US-03 |
| FEAT-02 | 导入保留与校验 | 导入时保留 contract 并严格校验；非法 contract 显式拒绝 | P0 | US-03 |
| FEAT-03 | Authoring 契约感知 | agent 按 contract 生成 `SKILL_PLAN`；缺必填项反问用户，不静默降级 AGENTIC | P0 | US-01/04 |
| FEAT-04 | Draft 调度预检 | 创建/更新 Draft 时解析、渲染、schema 校验、构建 Snapshot；不执行 Skill | P0 | US-01/02 |
| FEAT-05 | Confirm 预检门禁 | Confirm 用当前依赖与下一次触发重跑预检；失败不建 Schedule | P0 | US-01/05 |
| FEAT-06 | Fire 重校验 | 每次 Fire 用当前 Skill 版本重新校验；不兼容明确失败，不建 Execution | P0 | US-05 |
| FEAT-07 | 结构化修复 | 技术错误回 agent，业务缺口问用户；错误带 field/reason/repair/allowed_values | P0 | US-04 |
| FEAT-08 | self-creator 契约指导 | self-creator 生成可执行 Skill 时自动写 contract 并自检；定时任务额外要求 contract | P1 | US-06 |
| FEAT-09 | 工具通配拒绝 | `SKILL:<uuid>:*` 明确拒绝并返回 allowed_values；裸 `*` 保持合法 | P1 | US-04 |
| FEAT-10 | 删除真实试跑 | 删除 `/test-run`、trial 执行/投递、trial 卡片语义 | P0 | US-02 |
| FEAT-11 | 对话直接调用校验 | 对话中 `execute`/`execute_skill`/`run_skill_script` 启动前按同一 contract 校验；失败回模型修复，不启动进程 | P0 | US-07 |

### 4.2 功能明细

#### FEAT-01: Skill 调用契约

- **描述**: Skill 在 `SKILL.md` frontmatter 声明 `invocation_contract`；定时任务 preflight 与对话直接调用共用同一份契约：
  ```yaml
  invocation_contract:
    version: 1
    invocation: argv        # argv | stdin
    schema:
      type: object
      properties: {...}
      required: [...]
      additionalProperties: false
  ```
- **验收标准**:
  - [ ] `invocation=argv` 时 schema 描述 CLI 长选项（不含 `--`）；P0 只支持长选项，位置参数/短选项拒绝。
  - [ ] `invocation=stdin` 时 schema 描述 stdin JSON 对象。
  - [ ] `type: boolean` 是存在型开关：true 传裸 flag，false 省略；拒绝 `--flag=true|false`。
  - [ ] 契约绑定默认入口；辅助脚本（如 `check_status.py`）不套用主入口契约。
  - [ ] `additionalProperties: false`；零参数由 schema 的 `required` 决定，`argv=[]` / `input={}` 可以合法。
  - [ ] 契约只描述调用形状，不描述业务结果（不包含 `allow_empty` / `output_format`）。
  - [ ] 契约随不可变 artifact 版本冻结；修改契约必须发新版本。

#### FEAT-02: 导入保留与校验

- **描述**: 导入链路必须保留 `invocation_contract`，不能因为字段白名单把它丢掉。
- **验收标准**:
  - [ ] `SkillManifest` 严格解析 contract；非法结构导入失败。
  - [ ] Console `_frontmatter()` 保留 contract，持久化到 `frontmatter_json`。
  - [ ] 真实 zip 导入 → resolve → preflight 能读到同一份 contract。
  - [ ] 没有 contract 的 Skill 仍可导入；定时任务新 Draft 拒绝，对话调用保持现有行为。

#### FEAT-03: Authoring 契约感知

- **描述**: authoring compiler 用 contract 决定调用通道与参数，不靠 description 猜。
- **验收标准**:
  - [ ] `invocation=argv` 时生成 `argv`；`input` 必须为空。
  - [ ] `invocation=stdin` 时生成 `input`；`argv` 必须为空。
  - [ ] 缺必填业务值 → 一个业务问题（如“哪个客户？”），不问技术字段。
  - [ ] 已知 Skill + 确定参数 → 生成 `SKILL_PLAN`，不生成 `AGENT_TURN`。
  - [ ] 不允许因为 SKILL_PLAN 校验失败而静默改成 `AGENT_TURN`。

#### FEAT-04: Draft 调度预检

- **描述**: Draft 创建/更新时同步预检；失败不落 Draft。
- **验收标准**:
  - [ ] 校验 Skill/artifact/grant、通道、模板变量、渲染后的 schema、Snapshot 大小。
  - [ ] 预检不创建 `TaskExecution` / Fire / Revision / 投递。
  - [ ] 成功响应包含 `preview.dispatch_check`（通道、契约版本、渲染后的参数、依赖、snapshot hash）。
  - [ ] 失败返回稳定 `code` + `data`，agent 可据此修复。

#### FEAT-05: Confirm 预检门禁

- **描述**: Confirm 不信任 Draft 创建时的结果，用当前依赖和下一次触发重跑预检。
- **验收标准**:
  - [ ] 依赖撤销、Skill 版本变化、参数不再满足契约 → 明确失败，不建 Schedule。
  - [ ] 失败原因是可行动的（repair / allowed_values）。
  - [ ] `REMINDER` 不参与 Skill 契约预检。
  - [ ] 并发 confirm 仍只成功一次。

#### FEAT-06: Fire 重校验

- **描述**: 每次 Fire 用当前依赖重新解析并校验；不兼容明确失败。
- **验收标准**:
  - [ ] 用当前 artifact 的 contract 校验渲染后的参数。
  - [ ] 失败是永久 admission 失败：`NEEDS_ATTENTION`（CRON）或 `ENDED`（ONCE），不建 Execution。
  - [ ] 错误码/修复提示可查；不把 `*` 或非法参数带进执行。

#### FEAT-07: 结构化修复

- **描述**: 错误分层，技术错误不暴露给最终用户。
- **验收标准**:
  - [ ] `data` 至少包含 `field/reason/repair`，必要时带 `allowed_values/invocation/required/entry`。
  - [ ] agent 能在一轮内修正参数并重新提交。
  - [ ] 用户只看到业务问题或最终卡片，不看到 CLI flag/schema 字段。
  - [ ] 契约缺失/非法属于 Skill 作者/平台问题，不要求用户改参数。

#### FEAT-08: self-creator 契约指导

- **描述**: self-creator 在生成可执行 Skill 时写 contract，并自检；定时任务额外要求 contract。
- **验收标准**:
  - [ ] 创作流程确认脚本入口通道（argv/stdin），并据此写 contract。
  - [ ] 契约只描述默认入口；辅助脚本（如 `check_status.py`）不写主入口契约。
  - [ ] 纯指令/MCP-only Skill 不必写 contract。
  - [ ] 生成的可执行 Skill contract 能通过真实导入 + preflight。
  - [ ] 不可调度的 Skill 不强制加 contract；但可执行 Skill 建议写 contract，以便对话调用校验。

#### FEAT-09: 工具通配拒绝

- **描述**: `SKILL:<uuid>:*` 是未知工具名，不能静默冻结进 Snapshot。
- **验收标准**:
  - [ ] Draft/Authoring 返回 `SCHEDULE_TOOL_WILDCARD_UNSUPPORTED`，带 `allowed_values`。
  - [ ] 裸 `tools_allow=["*"]` 仍表示跟随 owner，保持合法。
  - [ ] Fire 侧对存量 `*` 明确 admission 失败，不带进执行。

#### FEAT-10: 删除真实试跑

- **描述**: 删除确认前真实执行/投递路径。
- **验收标准**:
  - [ ] 删除 `POST/GET /test-run`、`ScheduleTrialRunService`、trial 执行/投递分支。
  - [ ] 删除 `ScheduleTrialFact` / `preview.trial`；改为 `preview.dispatch_check`。
  - [ ] 前端卡片从“真实试跑”改为“调度预检”。
  - [ ] 验收中不再出现确认前执行真实 Skill。

#### FEAT-11: 对话直接调用校验

- **描述**: 对话中 agent 通过 `execute` / `execute_skill` / `run_skill_script` 调 Skill 时，在启动子进程或提交异步操作前读取默认入口的 `invocation_contract`，按同一通道/schema 校验；辅助脚本保持现有规则。
- **验收标准**:
  - [ ] 契约只绑默认入口；`execute_skill` 总是校验；`run_skill_script` 只有选中默认入口才校验，辅助脚本不受影响。
  - [ ] 有 contract 时，通道错配、缺必填、格式错误返回 `SKILL_INVOCATION_INVALID`，`error.detail` 带 `field/reason/repair/allowed_values`，**不启动 Skill 进程**。
  - [ ] `SKILL_INVOCATION_INVALID` 标记 `retryable=true`；模型改参后有下一轮机会，复用 `max_identical_failures`。
  - [ ] 只有可修复参数错误 retryable；skill key/权限/契约缺失等保持不可重试。
  - [ ] `load_skill.manifest` 返回 `entrypoint` + `invocation_contract`，模型能按需读取调用依据；不扩技能目录。
  - [ ] 没有 contract 的 Skill 保持现有行为，不阻断对话调用。
  - [ ] 对话调用不接受 `{{fire_date}}` / `{{previous_day}}` / `{{fire_time}}` 调度模板；模型必须给具体值（可用 `current_time` 等平台工具）。
  - [ ] 参数守卫阶段不写 Draft/Schedule/Fire/Execution/Snapshot；校验通过后继续现有执行链路，ASYNC 仍提交 operation/Execution。

---

## 5. 边缘情况 [可选]

| 场景 | 预期行为 |
|---|---|
| `invocation=argv`，只给 `input`，argv 为空 | Draft 拒绝：`SCHEDULE_ARGV_INVALID`，agent 改成 argv 后重试 |
| `invocation=stdin`，只给 argv，input 为空 | Draft 拒绝：`SCHEDULE_INPUT_INVALID` |
| 对话调用：`invocation=argv` 但只给 `input` | `SKILL_INVOCATION_INVALID`（不启动进程），`retryable=true`，模型改成 argv 后重试 |
| 对话调用：Skill 没有 contract | 保持现有行为，不阻断对话；定时任务新 Draft 仍拒绝 |
| 对话调用：参数含 `{{previous_day}}` | 不接受调度模板；模型必须给具体值 |
| 对话调用：辅助脚本（`check_status.py`） | 不套用主入口契约，保持现有行为 |
| 零参数：`argv=[]` 或 stdin `{}` | schema 没有 required 时合法；不因“通道为空”拒绝 |
| 布尔参数：`--flag=true` / `--flag=false` | 拒绝；true 传裸 `--flag`，false 省略 |
| 对话调用：第一次参数错误 | `retryable=true`；模型改参后第二次成功，不直接 NO_PROGRESS |
| 必填项是业务值但用户没说 | agent 问一个业务问题（“哪个客户？”），不暴露 `--company-id` |
| 模板 `{{previous_day}}` 在创建时不是合法日期 | 创建时只校验模板结构；preflight/Fire 渲染后校验值 |
| Skill 缺 contract | 新建 Draft 拒绝：`SCHEDULE_INVOCATION_CONTRACT_MISSING` |
| contract 非法（schema 不是 object / 未知 invocation） | 导入失败，不进入目录 |
| `SKILL:<uuid>:*` | Draft/Authoring 拒绝并返回 `allowed_values` |
| 裸 `tools_allow=["*"]` | 保持合法（跟随 owner） |
| 确认后 Skill 版本变化，新契约不兼容 | Fire `FAILED_ADMISSION` + `NEEDS_ATTENTION`，不建 Execution |
| `REMINDER` | 不参与 Skill 预检，按现有规则确认 |
| 存量 ACTIVE Schedule | 不重建；补 contract 后按同一 Fire 规则校验，不满足则显式失败 |
| 并发 confirm | 只有一个成功，另一个幂等重放或冲突 |
| agent 自动修复超过 2 次 | 停止重试，给用户一个产品级说明，不要求用户调参数 |

---

## 6. 非功能需求 [可选]

| 指标ID | 类型 | 要求 |
|---|---|---|
| NFR-PERF-01 | 性能 | Draft 预检同步完成，不执行 Skill；一次依赖解析 + 纯函数校验 |
| NFR-PERF-02 | 性能 | 对话直接调用校验在进程启动前完成；纯函数校验，不新增依赖解析 |
| NFR-REL-01 | 可用性 | 预检失败不留下半个 Draft/Schedule；Confirm 原子 |
| NFR-REL-02 | 一致性 | 并发 confirm 只成功一次；Draft hash/PENDING/expiry 校验保持 |
| NFR-SEC-01 | 安全 | contract/argv 不落密钥；日志不记录完整 argv 或业务正文 |
| NFR-OBS-01 | 可观测性 | 每次预检/确认/Fire 有 trace_id、code、repair、contract version、artifact checksum |
| NFR-COMPAT-01 | 兼容性 | `REMINDER` 不受影响；存量 Schedule 不重建 |

---

## 7. 范围与边界 [必填]

| 类别 | 内容 |
|---|---|
| **范围（In Scope）** | `invocation_contract`（版本/通道/schema）、导入保留、authoring 契约感知、Draft 预检、Confirm 预检门禁、Fire 重校验、对话直接调用校验、结构化修复、self-creator 指导、工具通配拒绝、删除真实试跑。 |
| **非范围（Out of Scope）** | 确认前真实试跑/手动诊断执行；Skill 业务数据/产物校验；新 TaskSpec / Run Ledger / auto-disable / legacy 迁移；Console-side contract overlay；artifact 版本锁定；`both` 双通道；未知显式工具名的完整注册表；多脚本契约；位置参数/短选项；default-true boolean；自由格式 stdin（`additionalProperties: true`）；发布时批量影响预览；新前端页面。 |
| **前置假设** | scheduled-task-v2 已上线；Skill artifact 可携带 frontmatter；self-creator 可更新；需要定时调度的存量 Skill 可以改造。 |

---

## 8. 依赖与风险 [可选]

### 8.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|---|---|---|---|
| Skill 作者 | `invocation_contract`（通道 + schema） | TASK-001 后 | 高 |
| self-creator | 契约写作指导与模板 | TASK-006 | 中 |
| 存量 MSSP Skill | 补 contract 并发新版本 | 上线前 | 中 |
| 调度器/Worker | 现有 Fire/Admission 链路 | 已具备 | 低 |

### 8.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|---|---|---|---|
| RISK-01 | 契约与真实脚本不一致 | 预检通过但 Fire/执行失败 | Skill 自己的 CI 用真实入口测试；Fire 失败显式进入待处理 |
| RISK-02 | 存量 Skill 没有 contract | 新 Draft 无法创建 | 上线前给需要调度的 Skill 补 contract；不可调度的 Skill 不强制 |
| RISK-03 | 存量 ACTIVE Schedule 参数不满足新契约 | Fire 进入 NEEDS_ATTENTION | 上线前扫描/预检；用 UPDATE Draft 修复 |
| RISK-04 | agent 修复循环 | 多轮重试、用户等待 | 技术/业务错误分层；最多自动修 2 次 |
| RISK-05 | Skill 版本升级改变通道/参数 | 旧 Schedule 失败 | 每次 Fire 重新校验，显式失败；不静默跑错 |
| RISK-06 | 工具通配/未知工具名 | 运行期工具缺失 | P0 拒绝 `*`；未知显式工具名作为同类 follow-up |

---

## 9. Existing Spec Constraints

| Spec/Rule | 约束 | 对范围/验收的影响 | 状态 |
|---|---|---|---|
| `api/harness-api#RULE-api-001` | 统一 ApiResponse 封套、错误码与幂等 | Draft/Confirm 错误必须走封套，诊断放 `data` | applied |
| `artifact/harness-skill#RULE-artifact-001` | Skill 产物不可变、随版本冻结 | contract 必须随 artifact 冻结 | applied |
| `worker/harness-worker#RULE-worker-001` | Worker 真实执行与状态 | Fire 重校验后仍由现有 Worker 执行 | applied |
| `snapshot/harness-snapshot#RULE-snapshot-001` | Snapshot 冻结执行事实 | 预检构建 Snapshot 但不落 Execution | applied |
| `time/harness-time#RULE-time-001` | IANA 时区渲染 | `argv`/`input` 模板按 Schedule 时区渲染 | applied |
| `test/harness-test#RULE-test-001` | 真实边界、隔离测试 | 预检不执行 Skill；真实执行由现有 Fire 测试覆盖 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|---|---|
| PRD | Product Requirements Document，产品需求文档 |
| Dispatch Preflight | 调度准入预检：确认前验证 Skill 能否生成合法 Execution 输入，不执行 Skill |
| invocation_contract | Skill 调用契约：版本、通道、参数 schema；定时任务与对话直接调用共用 |
| invocation | 调用通道：`argv`（CLI 参数）或 `stdin`（JSON 输入） |
| 对话直接调用 | 对话中 agent 通过 `execute`/`execute_skill`/`run_skill_script` 调 Skill；不是调度预检，不写 Draft/Schedule |
| SKILL_PLAN | 固定 Skill + 固定参数的定时 payload |
| AGENT_TURN | 需要模型推理时使用的定时 payload |
| Fire | 调度器的一次实际触发 |
| Admission | Fire 前的依赖解析/校验/快照冻结 |

---

*文档结束*
