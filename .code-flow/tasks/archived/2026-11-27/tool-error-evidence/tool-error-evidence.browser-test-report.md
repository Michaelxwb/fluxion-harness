# 工具错误事实 — 浏览器场景测试报告

- **日期**：2026-11-28 12:57–13:42（Asia/Shanghai）
- **执行方式**：kimi-webbridge 控制真实 Chrome，操作真实前端产物；真实 Console / Runtime / Worker / Gateway / Connector / PostgreSQL / Redis
- **入口**：`http://localhost:5173/chat/2ebfbf9b-d7fd-46e9-be91-a867c4fb5494`（demo01 / Jahan）与 `http://localhost:5173/audits`（admin）
- **数据库**：本地 `muad` 从 `0062` 升级到 `0063`（`runtime.tool_call_audit` 新增 `retryable` / `retry_disposition` / `agent_disposition` / `diagnostic_json`）
- **测试对象**：`.code-flow/tasks/2026-11-27/tool-error-evidence/tool-error-evidence.scenarios.md`
- **结论**：S-01、S-02、E-01、B-01、B-02 通过；E-02 的“结果未确认”样本通过，“只有错误码”样本由自动化整链用例覆盖（浏览器侧无对应夹具）；补充的正常成功与“错误→模型修正→成功”场景均通过。WebChat 不再返回技术详情，技术事实只在 Console 运行详情/导出中保留。

## 1. 测试结果总览

| 场景 | 结果 | 说明 |
| --- | --- | --- |
| S-01 用户遇到技能配置问题 | ✅ 通过 | 缺少契约的 `uat-draft-ok`；WebChat 只显示过程提示和“本次草稿未通过预检 / 管理员重新导入”，无技术详情；已有 7 条定时任务未变 |
| S-02 管理员核对证据 | ✅ 通过 | 无效契约的 `uat-contract-invalid`；WebChat 只显示内置描述，Console 详情/导出仍能区分 `CONTRACT_MISSING` 与 `CONTRACT_INVALID` |
| E-01 保护事件发生在业务失败之后 | ✅ 通过 | `SANDBOX_TIMEOUT` → `RETRY_GUARDED`；独立只读 `current_time` 成功且没有成为根因；保护动作在 Console 可见 |
| E-02 只有错误码或结果待核对 | ⚠️ 部分通过 | `SANDBOX_TIMEOUT` 的 `outcome_known=false` 正确提示先核对；只有错误码的预检拒绝由 `test_tool_error_evidence_flow.py` 覆盖，未单独造浏览器夹具 |
| B-01 可纠正的业务值 | ✅ 通过 | 缺少 `company_ids` 时同一 Run 追问；补充客户 1001 后生成草稿并确认，任务中心只新增 1 条 |
| B-02 诊断可见性和旧记录 | ✅ 通过 | 导出无 canary/凭据原文；旧行 `retryable` 显示 `UNKNOWN`；另一租户访问 Run 详情与导出均 404 |
| N-01 正常成功 | ✅ 通过 | `weather-query` 查上海 2 天天气，Run `479acaad-…`；`load_skill` / `execute` 均 OK，无错误块、无技术详情 |
| C-01 错误→模型自行修正→成功 | ✅ 通过 | 用户只给客户名 `ACME`，未告知正确参数；第一次 `UAT_CUSTOMER_UNKNOWN` 的错误列出 1001，模型自行改为 1001 后成功，Run `98453a74-…` |

## 2. 实际执行的 Prompt 与证据

### 2.1 S-01：缺少定时调用契约

Prompt（中文）：

```text
请帮我给 uat-draft-ok 创建一个定时任务，每天早上 9 点执行一次。
```

结果（最终构建）：

- Run `bd89e2ee-e025-4b07-8286-13f2d2550084`，`FAILED` / `RUN_TOOL_FAILURE_UNRESOLVED`
- WebChat 正文包含一句过程提示（“我先看一下这个技能的调用方式和当前时区。”）和内置描述：`执行未完成。原因：技能缺少定时执行配置。影响：本次草稿未通过预检。下一步：这是平台侧配置问题。管理员重新导入技能后可再试。`
- 页面没有“技术详情”折叠块；完整技术事实（`SCHEDULE_INVOCATION_CONTRACT_MISSING` / `payload.skill_id` / `CONTRACT_MISSING` / `REIMPORT_SKILL` / `ADMIN`）保留在 Console 运行详情和导出中
- 刷新后仍只有 1 条助手正文；任务中心保持 7 条
- 英文 UI 会话得到相同含义的确定性正文；服务端业务文案按平台默认 `zh-CN`（当前平台设置），不是逐会话语言

### 2.2 S-02：无效契约与证据比对

Prompt：

```text
请只调用 draft_schedule 工具，为 uat-contract-invalid 创建每天 10:00 执行的定时任务草稿；不要试跑、不要调用其他工具。
```

结果：

- WebChat 最终 Run `e3a0bbe8-fe49-4a82-95a4-eac5d34e21ef`，`FAILED` / `RUN_TOOL_FAILURE_UNRESOLVED`
- 正文只有：`执行未完成。原因：技能的定时执行配置无效。影响：本次草稿未通过预检。下一步：这是平台侧配置问题。管理员重新导入技能后可再试。`
- 页面无技术详情块；Console 运行详情仍显示同一错误码 `SCHEDULE_INVOCATION_CONTRACT_MISSING` / `draft_schedule` / `payload.skill_key (CONTRACT_INVALID)` / `REIMPORT_SKILL` / `ADMIN` / `LOCAL`
- 审计行与导出（对比用 Run `4b3bb0f6-…`）：`retryable=False`、`retry_disposition=BLOCK`、`agent_disposition=STOP`
- 与 S-01 的导出对比：

| 项 | S-01 | S-02 |
| --- | --- | --- |
| 错误码 | `SCHEDULE_INVOCATION_CONTRACT_MISSING` | 同 |
| 工具 | `draft_schedule` | 同 |
| 字段 | `payload.skill_id` | `payload.skill_key` |
| 原因 | `CONTRACT_MISSING` | `CONTRACT_INVALID` |
| 修复动作 | `REIMPORT_SKILL` | 同 |
| Run ID | `4bdfa8a5-…` | `4b3bb0f6-…` |
| 消息来源 | `PLATFORM` | `LOCAL` |

### 2.3 E-01：业务失败、保护动作、独立调用

Prompt：

```text
请按顺序做三步：1) execute target=workspace command='printf partial > uat-e01b.txt; sleep 10' timeout_sec=2；
2) 调用 current_time 读取当前时间（独立只读核对）；3) 用完全相同参数重试第 1 步。
不要改用其他执行方式，不要猜写入结果。
```

结果：

- Run `8bf58867-81a0-435a-93b7-58f5e2ad54a3`，`FAILED` / `RUN_TOOL_FAILURE_UNRESOLVED`
- 调用序列：`execute` → `SANDBOX_TIMEOUT`（`outcome_known=false`）；`current_time` → `OK`；`execute` → `TOOL_RETRY_UNSAFE` / `RETRY_GUARDED`
- WebChat 正文：`原因：执行环境不可用、超时或超出限制。影响：上一次操作是否已经生效尚未确认，请先核对结果。`，没有技术详情块
- Console 运行详情：`触发调用` 为被保护的重试，`来源调用` 为第一次超时调用；`保护动作：已阻止未核对的原样重试（RETRY_GUARDED）`
- 导出同时保留 `SANDBOX_TIMEOUT` 与 `TOOL_RETRY_UNSAFE`，首个失败节点为 `SANDBOX_TIMEOUT`；`current_time` 为成功节点，没有成为根因
- 恢复：自动化 `tests/agent_core/test_recovery_states.py` 覆盖 checkpoint 恢复；本轮未在浏览器制造进程中断/恢复点

### 2.4 E-02：结果未确认

Prompt：

```text
请调用 execute 工具，target=workspace，command="printf partial > uat-e02.txt; sleep 10"，timeout_sec=2。
只调用这一次，然后如实报告结果，不要猜测是否已写入。
```

结果：

- Run `b79115cf-8bf7-4352-9776-b6ee53f2943b`（首次样本），`SANDBOX_TIMEOUT`，`outcome_known=false`
- 回复没有断言写入成功或失败，明确要求先核对；后续 E-01 复测同样路径
- “只有错误码”的预检拒绝没有独立浏览器夹具；`tests/agent_runtime/test_tool_error_evidence_flow.py` 覆盖了只有码、无 detail 的确定性收尾

### 2.5 B-01：缺少参数 → 追问 → 确认

Prompt：

```text
请创建每天 9:00 用 mssp-asset-query-local 查询资产明细的定时任务。不要猜客户 ID；如果需要客户 ID，先问我。
```

结果：

- Run 进入 `WAITING_INPUT`，同一会话显示“等待你的确认”
- 补充 `客户 ID 1001；不需要导出，默认查询即可。` 后同一 Run 恢复，生成确认卡片
- 卡片：`CRON 0 9 * * * (Asia/Shanghai)`、`company_ids=["1001"]`、`platform=mssp`、`limit=20`、`export=false`、预检 `PASSED`
- 点击“确认创建”后任务生效：`task.task_schedule` 新增 1 条 `每日9点-MSSP资产明细查询-客户1001`，default 租户总数 7 → 8

### 2.6 B-02：诊断可见性与租户隔离

- 对 S-01/S-02/E-01 的导出包执行 `grep -Ei "canary|bearer |api[_-]?key|password|cookie|secret"`，只命中 README 的说明文字，没有实际标记或凭据
- 旧 Run `0522b17c-e123-4752-9eef-bb31a89a1c72` 的 `TOOL_CALL` 详情：`retryable=UNKNOWN`，没有新增 disposition/diagnostic 字段（保持未知，不伪造 false）
- 新建 `e2e-other` 租户管理员并登录；访问本租户 Run `4b3bb0f6-…` 的详情与导出均返回 `404 COMMON_NOT_FOUND`

### 2.7 N-01：正常成功

Prompt：

```text
请用 weather-query 查上海未来 2 天天气并总结。
```

结果：

- Run `479acaad-bd68-4e23-8184-18a644fcb9f6`，`COMPLETED`
- `load_skill` / `execute` 均 `OK`，没有错误块和技术详情
- 用户看到正常的天气表格与总结

### 2.8 C-01：错误信息给到模型后自行修正成功

Prompt（没有告诉模型正确参数，也没有告诉它如何修正）：

```text
请用 uat-self-correct 查询客户 ACME 的资产概况。
请直接调用该技能，不要先问我；最后报告你实际使用的客户编号和资产列表。
```

结果：

- Run `98453a74-7944-48c2-8eca-c53024f8b0ee`，`COMPLETED`
- 第一次 `execute`：`customer=ACME` → `UAT_CUSTOMER_UNKNOWN`，错误正文 `unknown customer ACME; known customers: 1001`，`retryable=true`
- 模型根据工具错误自行改为 `customer=1001` 重试
- 第二次 `execute`：`SUCCEEDED`，返回资产 `10.0.0.1`、`10.0.0.2`
- Console 运行记录保留两次调用：第一次 `FAILED` / `UAT_CUSTOMER_UNKNOWN` / `ALLOW` / `CONTINUE`，第二次 `SUCCEEDED`
- WebChat 没有技术详情块

说明：用户没有在 Prompt 中给出 `1001`，也没有要求“改成 1001”；正确值只来自第一次工具错误。该技能已作为 `skills/uat-self-correct` 夹具加入仓库，并有 `tests/skills/test_uat_self_correct.py` 覆盖。

## 3. 本轮发现并修复的问题

| 问题 | 现象 | 修复 |
| --- | --- | --- |
| 无效契约被当成用户输入错误 | 授权阶段返回 `SCHEDULE_DRAFT_INVALID` / `USER`，模型可继续解释并完成 | `schedule_authoring` 将 `CONTRACT_INVALID` 映射为 `SCHEDULE_INVOCATION_CONTRACT_MISSING`（ADMIN），补编译器用例 |
| 宿主自有失败仍允许模型正文 | `retryable=true` 与 `BLOCK/STOP` 矛盾，模型正文先流出 | `ToolError` 对 ADMIN/NONE 失败统一 `retryable=false` / `BLOCK` / `STOP`；Runner 优先判 `TOOL_FAILURE`，首个失败即硬停止 |
| TOOL_FAILURE 无根因 | 只有门禁错误时 `decisive_failure=None`，正文“具体原因尚无法确认” | 硬失败保存 `source_call_id`；`RETRY_GUARDED` 在 blocked/unknown 指向同一调用时保留来源 |
| 保护动作不可见 | Console 技术详情只有业务错误码 | Console 技术详情新增“保护动作”，按 gate reason 输出 |
| 字段列表显示 Python dict | Console 技术详情出现 `{'field': ...}` | Runtime renderer 与 Console `run_event_view` 统一输出 `payload.skill_key (CONTRACT_INVALID)` |
| WebChat 技术详情过长 | 用户看到触发/来源调用、处置、Run ID、用量等内部字段 | WebChat 不再返回/渲染 `technical_details`；只保留内置错误描述或模型总结，Console 仍保留完整证据 |

## 4. 回归

```text
tests/agent_core: 517 passed
tests/skills/test_uat_self_correct.py: 3 passed
TASK-007 声明测试（test_tool_error_evidence_flow + test_tool_error_performance）: 11 passed
Runtime finalization / executor / worker contract / audit / schedule authoring: 74 passed（分套件）
Worker scheduled drafts / agent turn + Console export/detail + frontend contract + catalog: 170 passed
最终合并回归（agent_core + runtime finalization + console/frontend）: 683 passed
ruff（全部改动 py）: PASS
mypy（29 个改动 source files）: PASS
前端 npm run typecheck: PASS
```

性能：用户已接受 TASK-007 的 p50 证据与 rejected p95 噪声说明，不再重复采样；原始样本保留在 `.git/tool-error-evidence/`。

## 5. 未执行/限制

- E-01 的“恢复点后恢复运行”没有在浏览器制造进程中断；恢复事实由 `test_recovery_states.py` 等自动化用例覆盖
- E-02 的“只有错误码的预检拒绝”没有独立浏览器夹具；由 `test_tool_error_evidence_flow.py` 的 only-code 样本覆盖
- 服务端业务文案当前按平台默认 locale（`zh-CN`）渲染；英文 UI 会话得到相同含义的中文正文。若要求逐会话语言，需要另立需求把 conversation locale 纳入 Run 快照
