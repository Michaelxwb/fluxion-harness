# Skill 调用 / 定时任务契约

适用范围：Skill 的调用契约（对话直接调用与定时任务共用）、`run-failure-reporting`
的 Job/payload 执行模型，以及与 `unified-artifact-delivery` v2.0 共享的消息/附件/投递契约。

本文只写稳定契约与操作边界；具体超时、重试次数、容量与端口以 `SharedSettings`、
`PlatformSettings` 和部署配置为准，不在此复制。

## 1. Job 模型

一个 Job 是五件事的组合，任何一项都不能由创建入口隐式决定：

```text
Job = schedule + payload + session + delivery + policy
```

- `schedule`：cron/once + IANA 时区。
- `payload`：`REMINDER` / `SKILL_PLAN` / `AGENT_TURN`，是执行的唯一配置来源。
- `session`：`isolated` / `current` / `session:<id>` / `main`，决定上下文。
- `delivery`：`NONE` 或 `PUSH`（本期 WECOM），决定结果是否外发。
- `policy`：工具面与预算引用。

`mode` 只是内部派生字段（`REMINDER`/`SKILL`/`AGENTIC`），不是用户或模型可选的输入。
外部只提交 payload；提交 `mode` 或旧 `execution_spec` 会被拒绝。

### Delivery 与 Web pull

- Web pull 不是 `delivery_intent`：只要有 Web 入口，任务中心/详情始终可读。
- `delivery=NONE` 时 `deliveries=[]`，界面显示“仅 Web 可查看”。
- `delivery=PUSH` 必须带显式、有效、属于当前 tenant/user 的出站路由；路由独立于创建入口。
- 没有有效路由时不得创建 PUSH；无绑定不是失败，也不伪造送达。

### Session

| 目标 | 上下文 | 典型 |
|---|---|---|
| `isolated` | 每次新会话 | 日报、后台杂活 |
| `current` | 创建会话的有界尾部 | 需要对话上下文的任务 |
| `session:<id>` | 持久命名会话 | 跨次积累的摘要 |
| `main` | 同会话 | 提醒、系统事件 |

会话删除/归档/重置时任务暂停或失败，不猜测替代会话。

## 2. Payload 契约

### REMINDER

```json
{"kind": "REMINDER", "text": "每天 9 点开站会"}
```

无模型、无工具；结果就是固定文本。

### SKILL_PLAN

```json
{
  "kind": "SKILL_PLAN",
  "skill_id": "f314c19f-...",
  "argv": ["--company-id", "22921947", "--date", "{{previous_day}}", "--no-wecom-push"],
  "window": {"kind": "DAY", "offset": -1}
}
```

`invocation=stdin` 的 Skill 改为只给 `input`（例如
`{"company_id": "22921947", "date": "{{previous_day}}"}`），`argv` 为空。

- `input` 只写 stdin；`argv` 追加到脚本参数，二者不能互相冒充。
- 可执行 Skill 建议声明 `invocation_contract`：`version`、`invocation`（`argv` 或
  `stdin`）、以及对应通道的参数 `schema`；定时任务新 Draft 必须声明，对话调用有声明时校验。
- `invocation=argv` 时只接受 `argv`，`input` 必须为空；`invocation=stdin` 时只接受
  `input`，`argv` 必须为空。能否为空由 schema 的 `required` 决定（零参数合法）。
  P0 一个 Skill 只支持一条调用通道。
- 时间变量在 fire 时按 Schedule 时区渲染并冻结；重试读取同一份冻结值。
- 无模型；失败沿用统一的 `ExecutionOutcome` 业务失败契约。
- Skill 输出目录 `SKILL_OUTPUT_DIR` 是本次执行的唯一用户产物目录；中间文件进
  `SKILL_DATA_DIR` 或 `/tmp`。

#### 调用契约

```yaml
invocation_contract:
  version: 1
  invocation: argv
  schema:
    type: object
    properties:
      company-id: { type: string, pattern: "^[0-9]+$" }
      date: { type: string, format: date }
      no-wecom-push: { type: boolean }
    required: [company-id]
    additionalProperties: false
```

- 契约只描述调用形状，不描述业务结果；不包含 `allow_empty` / `output_format`。
- 契约绑定默认入口；辅助脚本（如 `check_status.py`）不套用主入口契约。
- `argv` 只支持长选项（不含 `--`），`help` / `version` 为保留名；值以 `-` 开头时用 `--key=value`。
- `type: boolean` 是存在型开关：true 传裸 `--flag`，false 省略；拒绝 `--flag=true|false`。
- `additionalProperties: false`；零参数由 `required` 决定，`argv=[]` / `input={}` 可以合法。
- 两段式校验：创建时校验结构与模板变量，预检/Fire 校验渲染后的具体值。
- 契约随 artifact 版本冻结；修改契约必须发新版本。

#### 对话直接调用

- 对话中 `execute(target=skill)`（模型唯一入口；`execute_skill` / `run_skill_script` 只是保留的兼容别名）在启动子进程或提交异步操作前校验**默认入口**的 `invocation_contract`；`run_skill_script` 不出现在 `model_tools` 中，`execute(target=skill, script=...)` 仅在 `enable_run_skill_script` 调试开关打开时可用，辅助脚本不套用主入口契约。
- 有契约：通道/参数错误返回 `SKILL_INVOCATION_INVALID`（`retryable=true`，仅可修复参数错误），不启动进程；模型修复后重试。
- 无契约：保持现有行为，不阻断对话；定时任务新 Draft 仍拒绝。
- `load_skill.manifest` 返回 `entrypoint` + `invocation_contract`，模型可先读契约再调用。
- 参数守卫阶段不写 Draft/Schedule/Fire/Execution/Snapshot；校验通过后继续现有执行链路，ASYNC 仍提交 operation/Execution。
- 对话调用不接受 `{{fire_date}}` / `{{previous_day}}` / `{{fire_time}}` 调度模板；模型必须给具体值（可用 `current_time` 等工具）。

### AGENT_TURN

```json
{
  "kind": "AGENT_TURN",
  "prompt": "汇总 {{previous_day}} 的定时任务执行...",
  "model_ref": null,
  "tools_allow": ["SKILL:<skill_id>:execute", "PLATFORM:current_time", "PLATFORM:list_tasks"],
  "budget_ref": "platform"
}
```

运行时注入时间行、窗口、无人值守契约、上次 meaningful result 与 scratch。工具面默认与
交互式 Run 同一套注册器，`tools_allow` 只做收紧，不能超出创建者可用工具。

## 3. Authoring

```text
用户意图 → agent 输出 SchedulePlan（结构化）
  → 平台校验（skill/entrypoint/invocation/input/argv/cron/timezone/session/delivery/tools）
  → 低置信度/缺参数 → 一个业务问题
  → 调度预检（不执行 Skill）
  → 用户确认
  → 创建 Job
```

意图决策树（只问业务问题，不暴露 mode）：

```text
纯通知？ → REMINDER
固定 Skill + 固定参数？ → SKILL_PLAN
需要运行时判断/多工具/上下文？ → AGENT_TURN
不确定？ → 问一个业务问题；仍不确定 → 预检后按结果提议
```

校验失败返回结构化字段错误（`field` / `reason` / `repair` / 可选 `allowed_values`）。
agent 自动修正（最多 2 次）；仍失败时按错误类型处理：业务缺口问一个业务问题，
契约/依赖问题给出产品级说明，不静默降级成另一种 payload。

确认卡片必须展示：做什么、何时、时区、首次触发、在哪看（Web / 推送目标）、
依赖、预算来源、工具面、resolved payload。`confidence`/`reason` 只作 authoring 元数据，
不进入冻结的 Job 配置。

## 4. 调度预检与提升

- 确认前只做调度预检：解析依赖、校验调用通道与参数、构建 Snapshot；**不执行 Skill**，
  不创建 Execution/Fire/Revision，不产生投递或产物。
- 预检失败不得落 Draft；Confirm 失败不创建 Schedule。agent 拿到结构化
  `field/reason/repair/allowed_values` 后自行修复。缺业务值只问用户一个业务问题，
  不让用户调试 CLI flag。
- Confirm 用当前依赖与下一次触发重跑预检；失败不创建 Schedule，Draft 保持 PENDING。
- 从成功对话提升时复用对话中真实的 `execute(target=skill)` 的 skill/input/argv/platform
  事实，只补 schedule/session/delivery/window，不重新猜参数。
- 用户在卡片上显式确认后才产生 ACTIVE Schedule；同轮自确认不可达。

## 5. 无人值守与静默结果

- 没有人可澄清/批准；最终回复就是交付物。
- 无变化使用结构化 `record_result(no_change)`；平台抑制文本消息，任务中心记录
  “本次无变化”。附件仍进入消息并参与 pull/push，文本静默不抑制附件。
- 用户可见文本必须直白；不输出 `NO_REPLY`、错误码或内部 token。
- 失败说明阻塞点与已尝试动作；不向用户索要平台已知事实（日期/客户/路径）。
- 上次 meaningful result 有界注入下一次执行；scratch 有界、revision CAS、可清空。

## 6. 工具与预算

- 预算唯一来源是 `PlatformSettings.agent`：`max_turns`、`max_tool_calls`、
  `deadline_ms`、`max_model_retries`、`max_identical_failures`。
- `max_tokens` 是执行级累计上限；`0` 表示不限制，单次请求仍取模型参数。
- admission 解析并冻结预算；冻结后的执行不再读 agent runtime config。
- 旧 `budget` 字段只读、不消费；新 Job 只存 `budget_ref`。
- 平台只读工具（`current_time`、`list_tasks`、`get_task` 等）是一等工具；撤权/策略变更在
  每次执行前复核。

## 7. 时间

- 所有 recurrence 必须显式提供 IANA 时区；禁止服务器本地时区兜底。
- 按本地计划槽计算：不存在的墙钟时刻按 DST gap 策略留调度事件；重复墙钟时刻只采用策略
  允许的映射；不伪造 UTC Fire。
- 结束条件、错过区间计数与聚合额外次数必须一致；已终止的单次计划只能重新创建。

## 8. 与 `unified-artifact-delivery` 的共享契约

| 事实 | 所有者 |
|---|---|
| `MessagePayload {text, attachments[]}` / `AttachmentRef` / `SurfacePlan` / `MessageDelivery` | unified-artifact-delivery |
| Artifact 版本、`OUTPUT`、`logical_file_id + version` | unified-artifact-delivery |
| Web pull（WebChat/MSSWWEB） | unified-artifact-delivery；没有投递意图 |
| 企微 push | unified-artifact-delivery；消息级状态机 |
| Job 的 `delivery` / 冻结路由 | 本契约声明；投递侧校验/冻结/实现 |
| 任务中心 `deliveries[]` / `delivery_intent` / `target_label` | unified-artifact-delivery |

一条任务结果消息可以有多个附件；Web 历史与任务中心读同一份 `AttachmentRef`。
推送失败不得改写业务执行终态；附件保留诊断。调度预检不产生产物或投递；正式执行
沿用同一条消息/附件链路。

## 9. 验收

自动化：

- 契约：Job/payload schema、旧 mode 迁移、session/delivery 分离；
- authoring：黄金集与结构化字段错误；优先级 SKILL_PLAN，不静默降级 AGENT_TURN；
- SKILL_PLAN：`invocation_contract` 通道/参数、argv/input/time、预检零执行/零投递、重试幂等；
- 调度预检：Draft 创建、Confirm 重跑、Fire 重校验；契约缺失、通道错配、版本漂移显式失败；
- 对话调用：默认入口有契约时通道/参数在进程启动前校验；`SKILL_INVOCATION_INVALID` retryable；辅助脚本不被主入口契约拦截；无契约保持现有行为；异步非法参数不创建 operation；
- 契约细节：boolean 存在型开关、零参数由 schema 决定、只支持长选项；
- AGENT_TURN：prompt 时间行/窗口/无人值守、工具策略、session、`record_result(no_change)`；
- 预算：设置映射/冻结/旧 budget 不消费；
- 导出：TASK 事件、业务失败字段、failure evidence；
- Fake MSSP：全资产/非全资产/空数据/401/500/威胁实体/事件/XDR。

人工浏览器场景见 `.code-flow/tasks/2026-11-18/execution-and-delivery/` 下的 scenarios 文件，
必须使用真实构建产物、真实浏览器和真实服务，并在验收记录中写明任务 id、产物与投递结果。
