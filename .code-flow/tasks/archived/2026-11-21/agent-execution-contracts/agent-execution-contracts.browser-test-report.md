# Agent 执行契约 — 浏览器场景测试报告

- **日期**：2026-11-21 20:39–20:58（Asia/Shanghai）
- **执行方式**：kimi-webbridge 控制真实 Chrome，操作真实前端产物；真实 Console / Runtime / Worker / Gateway / PostgreSQL / Redis
- **入口**：`http://localhost:5173/chat/2ebfbf9b-d7fd-46e9-be91-a867c4fb5494`（demo01，admin）
- **测试对象**：`.code-flow/tasks/2026-11-21/agent-execution-contracts/agent-execution-contracts.scenarios.md`
- **结论**：核心工具/事件路径可用；发现 **4 个需要处理的问题**，其中 2 个为真实功能缺陷。

## 1. 测试结果总览

| 场景 | 结果 | 说明 |
| --- | --- | --- |
| S-01 MCP JSON 数组按数据处理 | ✅ 通过（代理用例） | `platform.echo` 返回 `[1,2,3]`，最终回复原样包含，工具事件 `OK` |
| S-03 定时 Agent 读取 Skill 正文 | ⚠️ 部分通过 | `load_skill` 成功；`execute(target=skill)` 已调用；因 fake platform 未回显 Traceid 失败（环境夹具问题） |
| S-05 确定性失败后不执行后续写工具 | ✅ 通过 | 第二个 `platform.targets.delete` 为 `TOOL_NOT_EXECUTED`，无 `STARTED`/operation |
| S-07 / B-05 隐藏别名不可执行 | ✅ 通过（目录检查） | 模型实际工具目录只有 `execute`，无 `execute_skill` / `run_skill_script` / `workspace_shell` / `skill_draft_shell` |
| B-04 typed 非重试失败确定性停止 | ✅ 通过（代理用例） | `platform.fail` 非重试失败后立即 `RUN_NO_PROGRESS`，未按阈值重试 |
| E-01 MCP `isError` 分类一致 | ❌ 失败 | 对话与定时 Agent 错误码不一致：`COMMON_INTERNAL_ERROR` vs `MCP_TOOL_FAILED` |
| E-13 cutover 删除旧事件 | ✅ 通过 | 新 Run 正常产生 v2 事件，旧表保持清空 |
| 其余场景 | ⏸ 未执行 | 缺少故障注入 / 图片与资源 MCP / 会话过期 / 超限 / 长历史 / 双 Task 附件夹具，详见第 4 节 |

## 2. 实际执行的 Prompt 与证据

### 2.1 S-01：MCP 返回 JSON 数组仍按数据处理

Prompt（第一轮）：

```text
请只调用一次 MCP 工具 platform.echo，参数 message 必须是字符串 [1,2,3]（包含方括号）。
然后把工具返回的内容原样告诉我。不要调用其他工具。
```

实际行为：存在两个同名 MCP 工具（`fake-mcp-headers` / `fake-mcp-bearer`），模型先要求指定服务。第二轮指定 `fake-mcp-bearer` 后执行。

Prompt（第二轮）：

```text
用 fake-mcp-bearer 那个，message=[1,2,3]，只调用一次。
```

结果：

- Run `d7137111-fd37-4773-83c1-f830d4084961`，`COMPLETED`
- `TOOL_CALL`：`tool_name=mcp::fake-mcp-bearer::platform.echo`，`state=COMPLETED`，`status=OK`，`content="[1,2,3]"`
- 最终回复包含 `[1,2,3]`

判定：通过。**注意**：两个 MCP server 暴露完全相同的工具名，模型需要额外一轮确认；不是错误，但影响自动化稳定性。

### 2.2 S-05：确定性失败后不再执行后续写工具

Prompt：

```text
请在同一回合内按顺序调用 fake-mcp-bearer 的两个工具：
先调用 platform_fail（无参数），再调用 platform_targets_delete（参数 id="target-1"）。
两个都必须调用，不要只调用第一个，也不要中途向我提问。调用完把两个结果都告诉我。
```

结果：

- Run `24af2c72-d42f-420e-bfe3-e85c6bf86148`，`FAILED` / `RUN_NO_PROGRESS`
- `ASSISTANT_TURN` 同时声明两个调用
- 第一个 `platform.fail`：`TOOL_CALL`，`status=ERROR`，`error_code=COMMON_INTERNAL_ERROR`
- 第二个 `platform.targets.delete`：`TOOL_CALL`，`status=ERROR`，`error_code=TOOL_NOT_EXECUTED`
- 第二个调用**没有** `TOOL_CALL_STARTED`，没有 operation 行；用量 `tools=2/30`（声明计数）但没有实际执行

判定：通过。失败止损、配对结果、not-executed 语义均符合预期。

### 2.3 E-01 / B-04：MCP `isError` 与 typed 非重试失败

对话 Prompt：

```text
请只调用一次 fake-mcp-bearer 的失败工具 platform.fail（模型工具名可能是
mcp__fake-mcp-bearer__platform_fail），不要调用其他工具。把工具返回的错误信息原样告诉我。
```

对话结果：

- Run `fc3026c5-9f6e-4cff-8c83-14519c8798ec`，`FAILED` / `RUN_NO_PROGRESS`
- `TOOL_CALL.outcome`：
  - `retryable=false`
  - `error_code=COMMON_INTERNAL_ERROR`
  - `failure_layer=TOOL`
  - `business_status=FAILED`
- 用户可见收尾为确定性文案，包含终止码、工具名、Run ID、`MCP tool_error: platform failure`

定时 Agent Prompt：

```text
创建一个 1 分钟后执行的一次性定时任务：
只调用 fake-mcp-headers 的 platform.fail 一次，把工具错误结果留在任务中心，不推送。
draft_schedule 的 payload.tools_allow 必须精确写成 "MCP:fake-mcp-headers:platform.fail"。
```

定时结果：

- Schedule `17f76ebc-d57f-4c2f-be47-a6fccb1504e3`
- Task Execution `4129c4bb-540e-461e-9c48-f31288b1e223`，`FAILED` / `TASK_NO_PROGRESS`
- 错误：`mcp::fake-mcp-headers::platform.fail / MCP_TOOL_FAILED: MCP MCP_TOOL_FAILED: platform failure`

说明：B-04 这里用的是 MCP `isError` 的 typed outcome 代理，不是专门的 `ToolError(retryable=False)` 测试 handler；确定性停止行为一致，但 B-04 的精确夹具仍需 pytest 覆盖。

判定：**失败**。两边都正确失败且不可重试，但错误码不一致：

| 路径 | 错误码 | 实现 |
| --- | --- | --- |
| 对话（Runtime） | `COMMON_INTERNAL_ERROR` | `mcp_runtime_adapter.py` 自有 `_McpSession` |
| 定时（Worker） | `MCP_TOOL_FAILED` | 共享 `McpClient` |

### 2.4 S-03：定时 Agent 读取 Skill 正文并执行

Prompt：

```text
创建一个 1 分钟后执行的一次性定时任务（AGENT_TURN）：
让 Agent 先加载 smoke-platform-sample 技能并读取它的 SKILL.md，再按正文步骤调用技能执行；
结果留在任务中心，不推送。请根据技能目录选择 tools_allow，创建后把确认卡片给我。
```

结果（Task Execution `0cce81df-93e5-458f-b10f-326d715590da`）：

- `load_skill`：`OK`，返回完整 SKILL.md
- 模型调用 `execute(target=skill, skill_key=smoke-platform-sample)`，成功进入技能执行
- 技能失败：`SKILL_EXECUTION_FAILED: smoke_platform check failed / Traceid was not echoed by the platform`
- `read_skill_resource("smoke-platform-result.json")` 返回 `SKILL_RESOURCE_NOT_FOUND`（符合预期，该文件不存在）
- 技能可重试（`retryable=true`），模型重试一次后仍失败，任务 `TASK_NO_PROGRESS`

判定：部分通过。`load_skill` 与 `execute` 路径正常；失败原因是 fake platform 当前为 `mssw` 模式，未按 `smoke_platform` 期望回显 Traceid，属于本地夹具配置问题。

**文档问题**：`skills/smoke-platform-sample/SKILL.md` 仍写：

```text
模型只调用 `execute_skill`
```

但模型目录已无 `execute_skill`。本次模型正确改用了 `execute`，但旧文档会误导后续模型/用户。

### 2.5 B-05：隐藏别名目录检查

Prompt：

```text
不要调用任何工具，只把你当前可用的所有工具名称逐字列出来（每行一个，不要解释）。
如果看到 execute_skill、run_skill_script、workspace_shell、skill_draft_shell 之类的旧名称，也请如实列出。
```

结果：模型列出的工具目录包含：

- `execute`
- `load_skill` / `read_skill_resource` / `search_skills`
- `read_attachment` / `search_attachment` / `list_attachments` / `view_image` / `write_artifact` / `append_artifact` / `deliver_artifact` / `create_archive`
- 定时任务工具、记忆工具、平台时间工具
- `mcp__fake-mcp-headers__*`、`mcp__fake-mcp-bearer__*`

**没有** `execute_skill` / `run_skill_script` / `workspace_shell` / `skill_draft_shell`。

判定：通过（模型可见目录）。但 `skills/` 下多个 SKILL.md / 模板仍引用旧名称，见问题 F-04。

## 3. 发现的问题

### F-01（高）Worker 共享 MCP Client 不发送 Bearer 认证

- **现象**：定时 Agent 绑定 `fake-mcp-bearer` 调用 `platform.fail`，稳定返回 `MCP_AUTH_FAILED: http 401`；同一 server 在对话中调用成功。
- **证据**：
  - Task `e15995c4-ef4a-4ffc-a6d9-46bb28e99f7c`、`c216023b-367d-4ae6-bb8b-131dbba4aa59` 均 `MCP_AUTH_FAILED`
  - `.data/fake-business-platform/mcp.log` 中 Worker 请求只有 `Accept: */*`，**没有 `Authorization`**；Runtime 请求有 `Authorization: ***`
- **根因**：`packages/platform-sdk/src/muad_platform_sdk/mcp_client.py:220-221` 只在 `secret_header` 存在时发送密钥；`secret_header=None` 的 Bearer 认证完全没发 `Authorization: Bearer <token>`。
- **影响**：所有通过共享 `McpClient` 的 Bearer MCP 调用（Worker 定时/Agentic 路径）都会 401；自定义 header 认证正常。
- **建议修复**：`_post` 中当 `auth_secret` 存在且 `secret_header` 为空时发送 `Authorization: Bearer <secret>`；补单测覆盖 bearer / none / custom header 三态。

### F-02（高）对话与定时 Agent 的 MCP 错误码不一致

- **现象**：同一个 `isError=true`，对话返回 `COMMON_INTERNAL_ERROR`，定时 Agent 返回 `MCP_TOOL_FAILED`。
- **证据**：Run `fc3026c5...` vs Task `4129c4bb...`（见 §2.3）。
- **根因**：
  - Runtime `McpToolError` 默认 `code="COMMON_INTERNAL_ERROR"`，`isError` 分支未传 `MCP_TOOL_FAILED`
  - Worker 使用共享 `McpClient`，`isError` 明确抛 `MCP_TOOL_FAILED`
- **影响**：E-01 明确要求“错误码与可重试性一致”；模型/用户看到的错误码取决于入口，审计与告警口径分裂。
- **建议修复**：优先把 Runtime 切到共享 `McpClient`（本需求的原始目标），消除两套实现；若暂不切换，至少把 `tool_error` 映射为 `MCP_TOOL_FAILED`，并统一 transport/protocol 错误码。

### F-03（中）可修复的 Draft 字段错误被标记为不可重试，模型被立即终止

- **现象**：模型提交 `tools_allow=["MCP:fake-mcp-bearer:platform_fail"]`，校验返回结构化 `TOOL_NOT_OWNED`，并给出 `allowed_values=["platform.echo", ...]`；但工具结果 `retryable=false`，Run 直接 `RUN_NO_PROGRESS`，模型没有机会按 `allowed_values` 修正。
- **证据**：Run `750a9c99-2e4c-446a-8325-1a778fd2851c`，`TOOL_CALL` 内容含完整字段修复建议，`outcome.retryable=false`，第 2 轮即终止。
- **根因**：`apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py:280-291`，`TaskToolError` 在有 `data` 时一律 `retryable=False`。
- **影响**：与 RULE-api-005“技术参数错误必须作为结构化字段错误返回给 agent”不一致；模型看得见修复建议却无法使用。
- **建议修复**：为 `SCHEDULE_DRAFT_INVALID` / `COMMON_VALIDATION_ERROR` 等字段错误显式 `retryable=True`（或按 `data.fields` 推断），仅确定性业务拒绝保持 `False`；补“第一次字段错误 → 模型修正 → 第二次成功”的测试。

### F-04（低）Skill 文档与模板仍宣称已删除的别名

- **现象**：`skills/smoke-platform-sample/SKILL.md`、`skills/_templates/business-skill-python/SKILL.md`、`skills/self-skill-*`、`skills/_templates/README.md` 仍写“模型只调用 `execute_skill` / `run_skill_script`”。
- **证据**：`grep -rn "execute_skill" skills`。
- **影响**：模型目录已无这些工具；旧文档会诱导模型调用不存在的工具，尤其影响弱模型和技能作者。
- **建议**：把这些文档改为 `execute(target="skill", skill_key=..., ...)`，并删除对 `run_skill_script` 的引用；若要保留历史说明，明确标注“已移除，仅供迁移参考”。

### F-05（中）Worker MCP 调用没有进入 `egress_audit`

- **现象**：对话 MCP 调用在 `runtime.egress_audit` 有 `OK` / `ERROR` 记录；定时 Agent 的 MCP 调用（`fake-mcp-headers` / `fake-mcp-bearer`）没有对应审计行。
- **证据**：`egress_audit` 最近记录只有对话 `mcp://fake-mcp-bearer/...` 和技能 HTTP；Task `4129c4bb...`、`e15995c4...` 无 MCP 审计。
- **根因**：`apps/agent-worker/src/muad_agent_worker/application/agentic/mcp.py:75-77` 构造 `McpClient` 时没有传 `audit` 端口；Runtime 适配器有 `_audit`。
- **影响**：S-09 要求 MCP 审计可查；Worker 路径缺失会让定时/Agentic 的 MCP 出站不可审计，违反 secret/egress 口径。
- **建议**：Worker 传入 `McpAuditPort` 实现（写 `egress_audit`，只记 code/status/target，不记认证头与正文），补一条“定时 MCP 调用产生 OK/ERROR 审计”的测试。

## 4. 未执行场景与原因

这些场景需要故障注入或测试夹具，当前浏览器 + demo01 + fake MCP 组合无法真实触发：

| 场景 | 阻塞原因 |
| --- | --- |
| S-02 | `compaction.summary.enabled=false`、`micro.enabled=false`；需要长任务夹具与摘要模型 |
| S-04 | `big-output` Skill 已软删除；没有“中部标记”大结果夹具 |
| S-06 / E-07 / E-08 / E-10 / E-11 / E-12 | 需要 Worker 崩溃/杀进程/断点注入，浏览器无法制造“prepared 后未收口”状态 |
| S-08 | fake MCP 不返回图片/嵌入资源 |
| S-09 | fake MCP 不支持会话过期（首次 404、重新 initialize） |
| B-01 | demo01 绑定了 9 个 Skill + 2 个 MCP；没有“零工具 Agent”入口 |
| B-02 / E-06 | 需要 40+ 消息长历史、两次摘要、protected reload；未构造 |
| B-03 | fake MCP 不返回 `null` / `42` / `"ok"` / `{"error":...}` 四类原生 JSON |
| B-06 / E-09 | fake MCP 不支持响应/SSE/媒体超限与“已发送结果未知”超时 |
| E-02 | 摘要压缩关闭，且没有“非法摘要形状”注入开关 |
| E-03 | 没有两个同时可用的 Task Execution + artifact 组合 |
| E-04 | 需要移除 `load_skill` 的测试装配开关 |
| E-05 | 模型不会主动调用目录外工具；需要 Hook/测试入口注入未知工具名 |

建议这些场景继续由现有 pytest 集成/契约测试覆盖；浏览器侧只作为用户可见链路的补充。

## 5. 日志与监控

- `.data/dev/runtime.log`、`.data/dev/worker.log`：本次测试期间没有未捕获异常；失败均以 Run/Task 终态和结构化事件落库。
- `.data/fake-business-platform/mcp.log`：确认 Worker Bearer 请求缺少 `Authorization`，是 F-01 的直接证据。
- `runtime.egress_audit`：对话 MCP 有 OK/ERROR 审计；Worker MCP 缺失（F-05）。
- `runtime.canonical_event` / `task.task_event`：新事件均为 `schema_version=2`；E-13 cutover 后旧数据保持 0。
- `learning_replay_failed` warning 仍会周期出现（旧运行记录 + 模型配置不可达），与本次执行契约无关。

## 6. 复跑建议

1. 先修 F-01（Bearer）和 F-02（错误码统一），否则 E-01 无法通过。
2. 修 F-03 后重跑 `draft_schedule` 错误修正链路，确认模型能在同一 Run 内按 `allowed_values` 修复。
3. 需要覆盖 S-04 / S-08 / S-09 / B-03 / B-06 时，扩展 `tools/fake-business-platform` 的 MCP 工具与 `/__control` 注入开关，而不是在浏览器里造数据。
4. 故障注入场景继续用 pytest；浏览器报告只保留用户可见断言（回复内容、任务中心状态、错误码）。

## 7. 修复与复测（2026-11-21 21:10–21:37）

### 7.1 已修复

| 问题 | 修复 |
| --- | --- |
| F-01 Worker Bearer 认证 | `mcp_client._post` 在 `auth_secret` 存在且无 `secret_header` 时发送 `Authorization: Bearer <secret>`；补 shared client 单测 |
| F-02 MCP 错误码不一致 | Runtime `McpToolError` 按 reason/HTTP 状态映射到共享 client 的稳定码：`MCP_TOOL_FAILED` / `MCP_PROTOCOL_FAILED` / `MCP_TRANSPORT_FAILED` / `MCP_AUTH_FAILED` / `MCP_SESSION_EXPIRED`；404 标记可重试 |
| F-03 可修复字段错误不可重试 | `TaskToolError` 对带结构化字段的业务错误默认 `retryable=true`（内部/平台环境错误仍 false）；MCP/Skill 的 `TOOL_NOT_OWNED` 返回完整的 `MCP:<server>:<tool>` / `SKILL:<id>:<tool>`，模型可直接复制修正 |
| F-04 Skill 文档旧别名 | `skills/**` 与模板统一改为 `execute(target="skill"|"draft"|"workspace", ...)`；`docs/04`、`docs/08` 同步；legacy/sandbox 文档加历史说明 |
| F-05 Worker MCP 缺审计 | 新增 `DatabaseMcpAudit`，Worker 的共享 `McpClient` 写入 `runtime.egress_audit`（target_type=MCP，只记 code/status/target，不记认证头/正文）；审计失败 warning 不阻断业务 |
| F-06 空 allowlist 冻结成“无工具” | `tools_allow` 为空/`*` 时 Console 展开 owner 全部生效 Skill/MCP 工具；Snapshot allowlist 从展开材料生成，Worker 预检同样展开；补 Console/Worker 测试 |

### 7.2 复测结果

| 复测项 | 结果 |
| --- | --- |
| 对话 MCP `isError` | ✅ Run `8dc57bd0...` / `62aa67bd...`：`MCP_TOOL_FAILED`，用户文案与定时路径一致 |
| 定时 MCP Bearer 调用 | ✅ Task `127251a6-bc65-4192-8b86-8398f2c3e2c4`：`MCP_TOOL_FAILED: platform failure`，不再是 `MCP_AUTH_FAILED: http 401`；fake MCP 日志出现 `Authorization: ***` |
| Worker MCP 审计 | ✅ 同一 Task 在 `runtime.egress_audit` 有 `MCP / mcp://fake-mcp-bearer/platform.fail / ERROR / MCP_TOOL_FAILED` |
| 定时任务字段错误修正 | ✅ 自然语言任务首次提交 `platform_fail` 被 `TOOL_NOT_OWNED` 拒绝，模型在同一 Run 内按完整 `allowed_values` 改为 `MCP:fake-mcp-bearer:platform.fail` 并生成确认卡片，未触发 `RUN_NO_PROGRESS` |
| 阈值文案 | ✅ 达到 `max_identical_failures` 时显示“同一失败重复达到上限”且标注“可重试：是”，不再误报“失败不可重试” |
| 空 allowlist 跟随 owner | ✅ Console/Worker 单测覆盖：展开全部生效 Skill/MCP 工具并写入 Snapshot allowlist |

### 7.3 复测使用的自然业务 Prompt

```text
请只调用一次 fake-mcp-bearer 里那个必定失败的工具，把错误信息原样告诉我。
```

```text
创建一个 3 分钟后执行的一次性任务：用 fake-mcp-bearer 里那个必定失败的工具执行一次，
把错误结果留在任务中心，不要推送。
```

这两条没有出现 `tools_allow`、`mcp__...`、`platform_fail` 等内部字段；模型自行选择工具、生成草稿并在首次工具名错误后自行修正。`fake-mcp-bearer` 是测试夹具名称，不是平台内部 API。

### 7.4 回归

```text
234 passed  (Console admission materials + Worker scheduled v2 + Runtime MCP/finalization + platform-sdk)
346 passed  (Worker scheduled v2 + task executor, 修复后)
70 passed   (Runtime schedule authoring + intent tools)
```

> 说明：`tests/agent_worker/test_scheduled_v2_drafts.py` 中原用例预期空 allowlist 的 dependencies 为 `[]`；修复后跟随 owner 会展开真实工具，新增用例覆盖展开结果，原用例只保留“无工具材料时为空”的语义。

## 8. 第二轮修复（TASK-015～TASK-017）

### 8.1 已落地

- **ContextPlan 真实接入 Runner**：`ContextCompactor` 只保留 `compact_plan(plan)`；Runner 用显式 `run_input`/活跃 Skill 构建计划并 pin，压缩不再丢当前目标或 Skill 正文。
- **恢复正确性**：异步 operation 按 `{input, argv}` 归一化指纹比较；`DECLARED_ONLY` 不重提交；无 checkpoint 时从事件重建 `turns/tool_calls/failure_state`，不可重试失败恢复后不再调用模型。
- **MCP 统一**：Runtime 删除 `_McpSession`，统一共享 `McpClient`；补齐 Accept/Protocol-Version/initialized、流式有界读取、Bearer、retryable、isError 会话丢弃；Worker 注入审计与 Task artifact writer，图片返回 artifact ID。
- **工具结果**：`ToolResult.externalizable` 从定义传播，`read_attachment`/`load_skill` 20KB 正文保持内联；`TaskArtifactRoundWriter` 跳过不可外置结果。
- **Skill 输入**：省略 input 仍执行 schema 校验，必填缺失返回 `SKILL_ARGUMENTS_INVALID`。
- **压缩性能**：micro → snip 顺序执行，micro 结果不再被原始历史覆盖。
- **附件性能**：Runtime 抽取移到线程；Worker 同一附件一次执行内复用解码结果；Skill 正文/资源读取移到线程。
- **异步提交**：首次提交不再 `get_recovery`，直接用已掌握参数补子 Task；恢复路径仍使用冻结参数。
- **图片恢复**：`completed_from_result` 按媒体类型标记 `IMAGE`；恢复 fold 保留 artifacts/follow-ups。
- **DraftShellError**：结构化可修复错误默认可重试。

### 8.2 验证

```text
TASK-015 declared: 112 passed
TASK-016 declared: 74 passed
TASK-017 declared: 167 passed
tests/agent_runtime: 1648 passed, 1 known flaky teardown (test_b262)
tests/agent_core + tests/agent_worker: 1261 passed, 4 order-dependent flaky
  (单跑均通过：test_async_dispatch / scheduled_agent_turn_e2e / revisions / worker_lifecycle)
```

> `tests/agent_runtime` 的 `test_b262_committed_tool_operation_business_key_survives_model_retry` 仍是既有 teardown 超时（单跑通过），与本次契约修复无关。

## 9. 第三轮修正（GPT 复查 7 项）

### 9.1 修正内容

1. **摘要只消费 ContextPlan**：删除 `split_summary_scopes` 与 Runtime 内按最后 USER 重划边界的逻辑；摘要适配器直接使用 `plan.older` / `coverage_seq` / `protected_event_ids`。新增 `with_summary`，同一摘要位置替换旧摘要，不再叠加两份。
2. **执行指纹唯一来源**：`prepare` 在 PRE_TOOL_USE 改写后重算 `args_hash`，prepared 事件与 operation `input_hash` 共用该值；恢复只比较 `prepared_args_hash`，不再从 assistant 参数重算。删除未使用的 `reconcile_operations` 旧恢复路径。
3. **checkpoint 不遮新事实**：事件 fold 从 seq 0 重放，有事件时 failure_state 完全以 fold 为准；checkpoint 只在没有任何事件时兜底。
4. **effect 与 retry_safety 分离**：`McpToolDefinition.retry_safety` 来自冻结声明；`effect=READ` 不再自动推导 `READ_ONLY`。500/429 在 `NON_RETRYABLE` 下返回 `retryable=false`。
5. **附件有界读取**：Runtime 纯文本与 Worker 共享读取都按 offset/limit 增量读；移除无界文本缓存。仅读到 EOF 时给出总字符数，未读完不再为求总数重读整文件。
6. **整轮 tool 结果优先**：恢复 fold 先输出该回合全部 tool 消息，再追加 follow-ups；`ImagePart` 新增 `artifact_id`，`completed_from_result` 写入 `ToolFollowUp.artifact_id`，恢复消息保留明确引用。
7. **删除旧 MCP 接口**：Runtime 移除 `execute_tool()` 字符串入口、`reason` 回填与 `_reason_for_code`；生产与测试统一 typed `execute_tool_result()`。Worker `McpToolError` 同步收敛为 code-only。

### 9.2 回归测试

```text
TASK-015 declared: 158 passed
TASK-016 declared: 102 passed
TASK-017 declared: 225 passed
tests/agent_core + tests/agent_worker: 1267 passed, 1 order-dependent (单跑通过)
tests/console_internal + tests/platform_sdk + tests/contracts + tests/sdk: 2025 passed, 11 skipped
tests/agent_runtime: 1650 passed, 1 failed（test_b262，见 9.3）
```

新增反例覆盖：

- 摘要 `covers_up_to_seq=2`，工具图片 follow-up 不再把当前输入挤出保护范围。
- Hook 把 assistant A 改为 B：prepared/operation 记录 B，恢复不报 `TOOL_OPERATION_INPUT_MISMATCH`。
- checkpoint 内 `no_progress=false`，后续不可重试失败仍恢复为 `no_progress=true`。
- `effect=READ` + `retry_safety=NON_RETRYABLE`，429/500 均 `retryable=false`。
- 8 MiB 文本按 offset/limit 读取，禁止调用整文件 `read_bytes`。
- 同一回合两个 tool 结果连续，图片 follow-up 在其后且带 artifact_id。
- Runtime typed 入口错误码 `MCP_TOOL_FAILED` / `MCP_TRANSPORT_FAILED` / `MCP_SESSION_EXPIRED`。

浏览器自然语言复测（Chat，Run `7edba8b4-47e2-4b9b-88a3-c44241c7b461`）：

```text
错误码：MCP_TOOL_FAILED
平台返回：MCP_TOOL_FAILED: platform failure
```

不再出现 `MCP MCP_TOOL_FAILED` 的重复前缀。

### 9.3 残留

`tests/agent_runtime/test_capacity_webchat_reply_generations.py::test_b262_committed_tool_operation_business_key_survives_model_retry`
仍会在真实 uvicorn 关闭阶段超时：执行器在 `_ExecutorEventEmitter.push` 等 `queue.join()`，而 SSE consumer 已退出。该用例在第二轮记录为既有 teardown flaky；第四轮确认根因是探针仍发送已删除的 `execute_skill`。迁移到 `execute` 契约后已关闭（见 §10）。

## 10. 第四轮修正（GPT 复查 4 P1 + 2 P2）

### 10.1 修正内容

1. **失败状态共享纯函数**：新增 `agent/failure.py`，成功清零、可重试累计、不可重试止损、阈值停损只实现一次；Runner 实时执行与 Worker 事件 fold 都按调用顺序调用它，恢复使用冻结 `max_identical_failures`。
2. **摘要先覆盖完整 older**：`compact_plan(trim_older=False)` 不再先裁 older；Runtime/Worker 先对完整区间摘要。只有摘要未落库时才调用 `trim_older_history`，并作为 budget 降级层记录。
3. **宿主显式 RunInput**：Worker 用 `task-input:<execution_id>` 绑定原始 goal；Runtime 使用持久化的 `run.input_message_id`，历史 USER_MESSAGE 写入同一 ID。删除 `_last_user_input` 与 `plan_context` 的 role/同文兜底匹配。
4. **Worker MCP 预算端口**：`McpToolBox`/共享 `McpClient` 接受 `McpBudgetScope`；Worker 从冻结 Task egress budget 建 controller，MCP initialize/initialized/tools/call 全部消耗预算，超限返回 `LOCAL_BUDGET_EXHAUSTED`。
5. **附件可续读游标**：`read_attachment` 返回 `cursor`，下一段用文本 IO cookie 直接续读，不再从文件头重扫；offset 仍用于随机首读。Runtime 与 Worker 共用同一编码与 `read_text_segment`。
6. **test_b262 迁移**：模型探针改发当前 `execute` + `target=skill` 契约；真实 operation 提交重新成立。迁移后单跑与整文件均通过，uvicorn 关闭不再超时。

### 10.2 验证结果

```text
TASK-018 failure-state-recovery: PASS
TASK-019 summary-before-trim: PASS
TASK-020 explicit-run-input: PASS
TASK-021 worker-mcp-budget: PASS
TASK-022 attachment-cursor: PASS
TASK-023 b262-execute-contract: PASS

tests/agent_runtime: 1653 passed
tests/agent_core + tests/agent_worker: 1277 passed, 2 order-dependent (单跑通过)
tests/contracts + tests/sdk + tests/console_internal + tests/platform_sdk:
  2025 passed, 11 skipped
```

新增反例覆盖：

- 失败 → 成功 → 再失败：`no_progress=false`，计数从 1 重新开始。
- 两次同因失败达到阈值：恢复后 `model_calls=0`，直接 `TASK_NO_PROGRESS`。
- 60 条 older 摘要：摘要模型看到全部 60 条，`covers_up_to_seq=60`，不再先裁成 40。
- Worker 原始 goal 的 message_id 为 `task-input:<execution_id>`，ContextPlan 只按稳定 ID 命中。
- Worker MCP 第 4 个 JSON-RPC 请求被本地预算拦截，未发出 HTTP。
- 纯文本游标续读返回第二页且不重扫前文。
- test_b262 整文件 6 passed，关闭无超时。


## 11. 执行契约复核修复与自动化验证（2026-11-22）

本节记录代码修复和自动化验证，不替代浏览器人工验收记录。

### 11.1 最终实现

- 止损一旦触发，同批成功结果不再撤销；恢复时跳过未执行占位结果，保留真正的失败证据。失败提示的可空语言类型与现有运行行为保持一致。
- Runtime 从当前 Run 的成功完成事实恢复活跃 Skill 和完整指令组；近期窗口内的预览也替换为完整正文，重复重建不重复插入组。无活跃 Skill 时直接保留原历史，避免无关重排。
- 记忆确认仅更新消息内容，保留输入 ID、事件 ID、序号与来源。
- Worker MCP 和同步 Skill 共用已有 HostEgressExecution、Execution 持久额度与当前 claim 执行段；MCP 借用进程连接池，Skill 返回不关闭共享宿主。删除 Worker 的临时本地预算 controller 装配；定时 SnapshotBudget 正式冻结 Agent 的 egress budget。
- 就绪任务夹具使用 PostgreSQL 时间。本轮曾出现两个用例未领取任务，确认无残留进程、原样复跑通过，并测得宿主时钟领先 PG；改用与 claim 相同的时钟作为明确的就绪前置条件，不增加等待或改变生产调度。

### 11.2 正向验证

```text
TASK-018 task finish: PASS — 83 passed
TASK-020 task finish: PASS — 141 passed
TASK-021 task finish: PASS — 628 passed
TASK-021 mypy apps packages: PASS — 713 source files
TASK-021 ruff（apps/packages/agent_core/agent_worker/本次 Runtime 测试）: PASS
补充 test_capacity_webchat_reply_generations.py 整文件: 6 passed
configuration inventory: PASS（包含在 TASK-021 回归中）
git diff --check: PASS
```

各 TASK 的套件存在重叠，不累计为独立测试总数。结果基于当前未提交工作区；TASK-018/020/021 的验证来源均为 `dirty=true`、`tests_changed=true`。

预算回归使用真实 PG claim/重新 claim、真实 Redis 与真实 HTTP：首次 MCP 调用后正常挂起，恢复时额度不足的初始化在发送前被拦截；MCP 用完额度后同步 Skill 同样被拦截；额度充足时 MCP → Skill → MCP 全链路完成，连接池保持可用。

性能验证覆盖连接池复用、会话复用、无活跃 Skill 时不排序；本轮未进行负载压测。

### 11.3 负向对照

- 恢复旧的成功清除止损行为：不可重试失败、达到重试阈值两个 Runner 用例均失败。
- 恢复旧的确认合并：输入 ID 保留用例失败。
- 保留窗口内的 Skill 预览：完整指令恢复用例失败。
- 绕过受控 MCP transport：挂起恢复和跨 MCP/Skill 额度两个集成用例均失败。
- 恢复将未执行占位结果折入失败状态：两个原始失败证据保留用例均失败。

上述故障注入均已撤销，最终声明验证通过。

### 11.4 全仓静态检查的范围外问题

全仓 `ruff check .` 仍有 71 条本轮未修改文件中的问题：

- `docs/01-总体设计图/tools/生成配图.py`：66 条。
- `docs/01-总体设计图/tools/生成预览.py`：3 条。
- `tests/gateway/test_inbound_batch_failure.py`：1 条。
- `tests/im_connector/test_platform_settings_client.py`：1 条。

本次声明的相关范围 Ruff 已通过，未将全仓结果记为通过。
