---
{"description":"人工验收场景","requirement":"agent-output-and-platform-defaults"}
---
# Agent 输出语言、失败文案与平台默认时区 验收场景

> 这是给**人工测试者**使用的验收清单，不是 E2E 代码或测试注册表。
> 每个场景保留 S-/E-/B- 编号，供 design 和 Task 的 `Scenarios` 字段引用。
> 步骤用用户能执行的界面动作、CLI 命令或 API 请求描述；期望写用户可观察的结果，避免选择器和内部实现细节。

## S-01 平台默认语言驱动 Agent 回复

- **负责**：待 `cf-task:plan` 分配（FEAT-01）
- **前置**：
  - 在「系统设置 → 默认区域」把默认语言设为 `en-US` 并保存。
  - 有一个可正常对话的 Agent，模型可用。
- **步骤**：
  1. 新建一个会话，不提及任何语言要求，用任意语言发一句普通问题。
  2. 观察 Agent 的回复语言。
  3. 回到系统设置把默认语言改为 `zh-CN`，再新建一个会话重复一次。
- **期望**：
  - 第一次回复为英文；改为 `zh-CN` 后的新会话回复为中文。
  - 修改设置不影响已创建会话中正在进行的 Run。
- **实际结果**：**PASS** — `en-US`：新会话用中文问“你好，请用一句话介绍你自己。”，回复为英文；`zh-CN`：删除测试 `language` memory 后用英文问“What is the capital of France?”，回复为中文“法国的首都是巴黎（Paris）”。设置变更只影响后续新会话。

## S-02 用户显式要求覆盖默认语言

- **负责**：待 `cf-task:plan` 分配（FEAT-02）
- **前置**：
  - 平台默认语言为 `en-US`。
  - 当前用户的 memory 写入已启用，且没有 `language` 偏好。
- **步骤**：
  1. 在对话中发送“请用中文回答这个问题”。
  2. 观察本轮回复语言。
  3. 再发送“以后都用中文回答我”。
  4. 按页面提示确认 memory 候选（`language=zh-CN`）。
  5. 新建一个会话，用任意语言提问。
- **期望**：
  - 第 2 步本轮回复为中文，但平台默认设置不变。
  - 第 4 步出现可确认的长期偏好候选；确认后，第 5 步新会话默认中文。
- **实际结果**：**PASS** — `en-US` 下发送“请用中文回答这个问题”，本轮回复中文；随后“以后都用中文回答我”出现可确认的 `language=zh-CN` memory 候选，确认后 DB 生成 `v1.slot.preference.language.44136fa355b3678a`（version 1，ACTIVE）；新建会话在 `en-US` 下用中文回复。架构统一为 memory > 平台默认后，又以 `en-US` + memory `zh-CN` 用英文提问复测，仍回复中文。验收后已删除该测试 memory。

## S-03 平台鉴权失败时给出直白描述与错误码

- **负责**：待 `cf-task:plan` 分配（FEAT-03）
- **前置**：
  - 准备一个会返回平台鉴权失败的 Skill / 探针，例如使用失效凭据调用 `list-platforms`。
- **步骤**：
  1. 在 WebChat 中触发该 Skill。
  2. 等待 Run 结束，阅读助手消息。
- **期望**：
  - 正文先说明“平台登录失败/凭据或签名问题”，并给出下一步（如联系管理员检查平台凭据）。
  - 末尾「技术信息」包含终止码、`PLATFORM_AUTH_REJECTED`、工具名和 Run ID。
  - 正文不以 `platform_sessions: ...` 内部证据串为主句；不出现凭据、Cookie、headers。
- **实际结果**：**PASS** — 失败 Run `acd4f8b4-1dda-4537-a0e0-2bad5ffba0b5` 正文为 “Platform sign-in failed…” + “Ask an administrator…”；技术信息含 `RUN_NO_PROGRESS`、`PLATFORM_AUTH_REJECTED`、`execute`、Run ID；正文未以 `platform_sessions` 为主句，未出现凭据、Cookie、headers。

## S-04 新建定时任务使用平台默认时区

- **负责**：待 `cf-task:plan` 分配（FEAT-05）
- **前置**：
  - 在「系统设置 → 默认区域」把默认时区设为 `Asia/Shanghai`。
  - 当前用户可创建定时任务。
- **步骤**：
  1. 在对话中说“每天 9 点提醒我检查日报”，不指定时区。
  2. 等待 Agent 返回 Draft 确认卡片。
  3. 查看卡片上的时区与触发时间。
- **期望**：
  - 卡片显示 `Asia/Shanghai`，触发时间为该时区的 09:00。
  - 确认后 Schedule 详情保留该时区。
- **实际结果**：**PASS** — 平台默认 `Asia/Shanghai` 时新建 Draft 卡片显示 `CRON 0 9 * * * (Asia/Shanghai)`、首次触发 `2026-11-22 09:00:00`；确认后 Schedule `5617fd51-2b7e-4803-80e7-053ff04eac75` 当前 revision 时区仍为 `Asia/Shanghai`，`next_fire_at=2026-11-22 01:00Z`（= 当地 09:00）。

## S-05 用户显式指定时区覆盖平台默认

- **负责**：待 `cf-task:plan` 分配（FEAT-05）
- **前置**：
  - 平台默认时区为 `Asia/Shanghai`。
- **步骤**：
  1. 在对话中说“每天纽约时间 9 点提醒我检查日报”。
  2. 查看 Draft 确认卡片。
- **期望**：
  - 卡片显示 `America/New_York`，而不是平台默认 `Asia/Shanghai`。
  - 确认后 Schedule 详情保留 `America/New_York`。
- **实际结果**：**PASS** — 平台默认 `Asia/Shanghai` 时说“每天纽约时间 9 点提醒我检查日报”，Draft 显示 `CRON 0 9 * * * (America/New_York)`；确认后 Schedule `c652c09b-64cf-495f-80d0-5efdaa346f68` 时区为 `America/New_York`，`next_fire_at=2026-11-21 14:00Z`（= 当地 09:00）。

## S-06 定时 Agentic 任务默认语言

- **负责**：待 `cf-task:plan` 分配（FEAT-01）
- **前置**：
  - 平台默认语言为 `en-US`。
  - 已创建一个 AGENTIC 定时任务（会在触发时调用模型并产出回复）。
- **步骤**：
  1. 手动触发该定时任务，或等待下一次触发。
  2. 查看任务中心/投递结果中的 Agent 回复。
- **期望**：
  - 回复为英文；不因任务由定时触发而回落中文。
  - 如果该用户 memory 有 `language=zh-CN` 偏好，则回复为中文。
- **实际结果**：**PASS（修复后复测）** — 平台 `en-US`、用户 memory `language=zh-CN`（version 1，ACTIVE）时创建并触发 AGENTIC 任务 `71867b4b-f2e7-4edd-b45f-882fd3af4350`；执行快照 `memory.block.language="zh-CN"`，`result_json.text` 为中文“周末午后适合把本周遗留的排查记录整理收尾…”。同一轮 WebChat 也用英文提问复测，memory 覆盖后回复中文。

## E-01 平台设置源不可读时 Draft 明确失败

- **负责**：待 `cf-task:plan` 分配（FEAT-05）
- **前置**：
  - 让平台设置读取失败（测试环境可停掉 Console 设置端点或注入不可达依赖）。
- **步骤**：
  1. 在对话中说“每天 9 点提醒我检查日报”，不指定时区。
  2. 观察 Agent 回复与任务中心。
- **期望**：
  - 明确提示“平台设置不可用/无法读取默认时区”，不产生 Draft，也不静默使用服务器本地时区或 UTC。
  - 不出现部分写入或伪成功。
- **实际结果**：**PASS** — 通过故障代理让 Runtime 的 `/internal/v1/platform-settings`（无 `X-Trace-Id` 的调用）返回 500，WebChat 说“每天 9 点提醒我检查日报”；`draft_schedule` 返回 `PLATFORM_SETTINGS_UNAVAILABLE`，Run `3a4c32e8-9c25-4ec3-b397-7231c4fafd6b` 收尾为 “Platform settings are temporarily unavailable.” + 下一步；DB 中该时间窗内 `task_schedule_draft=0`、`task_schedule=0`，无部分写入。

## E-02 未知错误码仍给出通用描述

- **负责**：待 `cf-task:plan` 分配（FEAT-03）
- **前置**：
  - 准备一个返回未登记错误码的工具/Skill（例如 `SOME_NEW_TOOL_ERROR`）。
- **步骤**：
  1. 在 WebChat 中触发该工具。
  2. 阅读失败消息。
- **期望**：
  - 正文给出通用直白描述（例如“工具执行失败，本轮无法继续”）和下一步。
  - 技术信息保留原始错误码，便于开发排查。
- **实际结果**：**PASS** — 导入 Skill `e2e-unknown-error`，其 stderr 返回 `SOME_NEW_TOOL_ERROR`；WebChat 失败 Run `3093877c-be64-465c-a8c3-db10aa40d4b3` 正文为通用 “The run did not complete… Tool execute failed.” + 下一步；技术信息保留原始码（`Platform response: {"error":{"code":"SOME_NEW_TOOL_ERROR"…}}`）。注：该 CONTENT 层失败的 audit `error_code` 为空，原始码只在平台返回行可见；正文未泄露原始码。

## E-03 en-US 租户的收尾与 IM 固定文案为英文

- **负责**：待 `cf-task:plan` 分配（FEAT-04）
- **前置**：
  - 平台默认语言为 `en-US`。
  - 已配置一个可用的 IM 入口（或使用测试通道）。
- **步骤**：
  1. 通过 IM 发送一条会失败的消息，观察失败/停止/断流文案。
  2. 通过 WebChat 触发一次失败 Run，观察收尾文案。
- **期望**：
  - IM 的“任务已受理/已停止/服务中断”等固定文案为英文。
  - WebChat 失败收尾的标题、原因、下一步为英文；技术信息中的错误码保持原样。
- **实际结果**：**PASS（修复后复测）** — 通过默认租户 WECOM 测试入口（`e2e-no-connector-bot`，无 Connector 连接，避免真实外发）注入入站消息：状态帧 “Preparing/Thinking/Executing/Handed off to a background task” 为英文；`DETACH` 受理触发固定文案 “Task accepted. You will be notified when it finishes.”；`/stop` 回执为 “Stopping the current task…”；取消终态为 “The run was stopped by the user.”。WebChat 失败收尾（S-03/E-02）仍为英文。

## B-01 平台时区变更后已建任务不跟随

- **负责**：待 `cf-task:plan` 分配（FEAT-05）
- **前置**：
  - 已用平台默认时区 `Asia/Shanghai` 创建一个每日 9 点的 Schedule。
- **步骤**：
  1. 把平台默认时区改为 `America/New_York`。
  2. 查看已建 Schedule 的详情与下次触发时间。
  3. 再创建一个不指定时区的新 Draft。
- **期望**：
  - 已建 Schedule 仍为 `Asia/Shanghai`，触发时间不变。
  - 新 Draft 使用 `America/New_York`。
- **实际结果**：**PASS（顺序镜像）** — 已建 Schedule `bb186441-5dd3-4b63-815c-4876227f1eb5` 在平台默认切到 `America/New_York` 时创建，时区为 `America/New_York`、`next_fire_at=2026-11-21 14:00Z`；之后平台默认改回 `Asia/Shanghai`，该 Schedule 的时区与下次触发均未变。随后新建 Draft 使用当时的 `Asia/Shanghai`，确认后 Schedule `5617fd51-…` 保留 `Asia/Shanghai`。验证了“配置变化只影响后续新执行”。

## B-02 memory 写入禁用时的语言覆盖

- **负责**：待 `cf-task:plan` 分配（FEAT-02）
- **前置**：
  - 平台默认语言为 `en-US`。
  - 当前用户的 memory 写入被禁用。
- **步骤**：
  1. 发送“请用中文回答这个问题”。
  2. 再发送“以后都用中文回答我”。
  3. 新建会话提问。
- **期望**：
  - 第 1 步本轮中文回复。
  - 第 2 步不会产生长期语言偏好（或明确提示无法保存）。
  - 第 3 步仍按平台默认英文回复。
- **实际结果**：**PASS** — `en-US`、Agent `runtime_config.memory_write=false`：第 1 步中文请求本轮中文；第 2 步“以后都用中文回答我”明确说明无法写入长期偏好（DB 无新 `language` memory）；第 3 步新会话英文提问回复英文。

## B-03 显式非法时区被拒绝

- **负责**：待 `cf-task:plan` 分配（FEAT-05）
- **前置**：
  - 平台默认时区正常。
- **步骤**：
  1. 在对话中说“每天 Mars/Olympus 时间 9 点提醒我”。
  2. 观察 Draft 卡片或字段错误。
- **期望**：
  - 明确提示时区非法，要求提供合法 IANA 时区；不产生 Draft，不静默改用平台默认。
- **实际结果**：**PASS** — 发送“每天 Mars/Olympus 时间 9 点提醒我检查日报”，Agent 明确回复“Mars/Olympus 时间不是可识别的 IANA 时区名…不能凭猜测设置定时”，并给出 `Asia/Shanghai` / `America/New_York` 候选；未生成 Draft，未静默使用平台默认。

## 场景编写约定

- 一个场景只验证一个用户可理解的目标。
- 步骤编号从 1 开始，按真实操作顺序编写。
- 期望写“用户看到什么/数据变成什么”，不要写内部函数名或测试断言。
- 场景不生成 manifest、不登记执行结果、不阻断 `task finish` 或 `task archive`。
- 需要机器执行的快速回归放在 Task 的 `### Tests`；E2E 和全量回归留在 CI。
