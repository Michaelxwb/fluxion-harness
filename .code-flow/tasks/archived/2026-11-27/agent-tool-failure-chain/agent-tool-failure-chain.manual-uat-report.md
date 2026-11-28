# Agent 对话续行与运行设置生效 · 人工验收记录

- 日期：2026-11-27
- 环境：本地 dev，WebChat `http://localhost:5173/chat/2ebfbf9b-d7fd-46e9-be91-a867c4fb5494`
- Agent：`demo01`（`f069722a-3752-4316-9613-d1e4e606393c`）
- 浏览器：用户已登录浏览器，通过 Kimi WebBridge 操作
- 数据库：已清理旧 Run / 会话 / checkpoint / snapshot
- 平台设置：验收期间临时保存阈值 3，复验后已恢复为 2

## 结果总览

| 场景 | 结果 | 说明 |
|---|---|---|
| S-01 | 通过（修复后） | `load_skill('uat-missing-skill')` 返回结构化 `SKILL_NOT_EFFECTIVE`，随后 `weather-query` 查北京成功并给出实时天气与两天预报。 |
| S-02 | 通过（WebChat 自动） | 默认查询 1200 条只返回 20 条并标记 `truncated=true`；Agent 按 `10.60.0.0/24` 缩小后分页取全 254 条，明确说明其余 946 条未核实。 |
| S-03 | 通过（真实企微） | Run `7b633936-90ea-4e70-832b-3ad60e715022`：企微收到提问并 `HUMAN_QUESTION` DELIVERED；回复 `1001` 后同一 Run resume，返回 4 条资产，终态与终态投递均 DELIVERED。 |
| S-04 | 通过 | 12 轮短对话 + `history_budget_tokens=256` 触发 `CONTEXT_COMPACTED`（dropped_records=23）；最终请求带完整客户/平台/关键字，直接返回 10.10.1.11，未追问历史。 |
| S-05 | 通过 | 缺失 Skill、`smoke-platform-sample`、再缺失 Skill；两次失败被成功加载打断，Run 仍能继续。实际调用顺序与提示顺序不完全一致，但连续性验证成立。 |
| S-06 | 通过 | 草稿 `uat-draft-ok` 创建、v1/v2 试跑、提交 PENDING、管理员审批、默认入口 `draft-v2` 执行、workspace `printf workspace-ok` 全部通过。 |
| S-07 | 通过（修复后） | 原缺陷：提问已落库，但 WebChat 显示“停止生成”，回车只换行。修复后 `WAITING_INPUT` 主操作为发送、停止保留为次要按钮；Enter 提交答案并 resume 同一 Run；刷新重开会话后问题只显示一次。 |
| S-08 | 通过（含执行导出） | 旧 Run `815b3564` 导出 `max_identical_failures=3`、历史预算 65536、摘要关闭；新 Run `00c00a6f` 导出为 2；运行中改回 2 后旧 Run 仍保持 3，当前设置为 2。 |
| S-09 | 通过 | 在原会话要求“基于刚才的 zip 把 b.csv 整理成 Markdown 表格”，Agent 使用已交付产物内容，没有重新生成文件。 |
| S-10 | 通过（真实企微） | Run `dc799088-46ac-434a-9c49-7871a28bff83`：导出客户 9001 全量 1200 条资产为 CSV（175,975 B），`deliver_artifact` 成功，终态消息 DELIVERED。 |
| S-11 | 通过（含 TXT 刷新） | ZIP（a.txt / b.csv / c.json）与约 5000 字 TXT 均生成并交付；TXT Artifact `9d9ed9e0-6e4e-480d-ad48-3f8e508a494c`，12,664 字节；刷新会话后 `平台对接值守说明.txt` 仍可见。 |
| E-01 | 通过（修复后） | 超时返回 `outcome_known: false`；原命令重试被 `TOOL_RETRY_UNSAFE` 拦下；`read_workspace_file` 只读读取成功并核实到 `partial`；`deliver_artifact` 独立操作仍可执行。 |
| E-02 | 通过（契约自动化） | `test_execute_rejects_removed_script_parameter` 验证 `execute(script=...)` 返回明确拒绝和允许键；真实模型会主动省略旧参数，无法用自然语言稳定复现。 |
| E-03 | 通过 | 无效 Skill 返回不可用原因，随后 `smoke-platform-sample` 可加载，替代路径可继续。 |
| E-04 | 通过 | XDR 未配置时明确说明且不复用 MSSP 数据；客户 9002 分页异常导出 38/40 行，明确 `MISSING_ROWS=2`、`DUPLICATE_ROWS=2`，未声称完整。 |
| E-05 | 部分通过（真实注入） | `N=10` 下前 3 个 `deliver_artifact` 调用失败，第 4 个成功；用户截图确认只收到一份 CSV。Agent 文案把“前三次失败”写成“前两次失败”，计数口径需修正；一次性注入代码已回退。 |
| B-01 | 不适用 | 开发阶段明确不保留旧 checkpoint 兼容，数据库已清理；旧迁移场景不执行。 |
| B-02 | 通过（单项对照） | 阈值 2/3 同提示词 A/B、历史预算 256/1024/65536 同对话、摘要开/关同会话均已跑；默认值不变。详见 [B-02 评估](agent-tool-failure-chain.b02-evaluation.md)。 |
| B-03 | 通过 | 工具预算 3 时首轮完成资产查询后达限；终态文案保留“已取得 1 个工具结果”；恢复预算后回复“继续”，新 Run 只补天气查询并完成汇总，未重复资产查询。 |

## 原缺陷证据（已修复）

### S-07 WebChat 等待回答不可提交

- 原 Run：`39af1c6d-6849-4b5a-873b-02f9264ebf21`
- `runtime.run_interrupt.prompt_text`：
  `请告诉我你要查询的城市名称（例如：北京 / 上海 / 广州）。我不会自行猜测或默认取某个城市。`
- `runtime.canonical_event` seq 211 有 `ASSISTANT_MESSAGE` 正文，212 为 `RUN_STAGE WAITING_INPUT`。
- 原页面在 `WAITING_INPUT` 仍按“非终态 Run”切换成停止按钮，`canSubmit=false`，Enter 只换行。
- 原 `withoutPersistedReply` 用 `message.seq > replySeq` 判重；等待输入的提问与持久历史同 seq，刷新后会再次渲染流式副本。

### S-02 / E-01 workspace 未验证状态过度阻塞

- 原 S-02 Run：`5a49df7b-e125-4284-9629-5c097ce0af3f`（实际使用 workspace，不是资产查询夹具）
- 首个 workspace 调用返回 `SANDBOX_OUTPUT_LIMIT`，`outcome_known=false`。
- 后续写文件调用没有 `target`，返回 `SCHEMA_INVALID`。
- 之后连续 workspace 调用返回 `TOOL_RETRY_UNSAFE`，即使命令不同也无法执行。
- `deliver_artifact` 仍可执行，说明交付与外部写入使用不同门禁口径。

## 修复后复验

### S-07：通过

- 新 Run：`24d5367f-797b-4bf1-a1a0-350f4e37962e`（会话 `7c46f082-b154-4135-9b78-8dac96f707ff`）
- 提问落库：`ASSISTANT_MESSAGE` seq 11，`RUN_STAGE WAITING_INPUT` seq 12。
- 页面同时显示“停止生成”和“发送”；输入“北京”按 Enter 后提交成功。
- 服务端在原 Run 上继续执行：seq 15 `RUNNING`，seq 21 `load_skill`，seq 26 `weather-query`，seq 45 最终回答；Run 终态 `COMPLETED`，没有新建第二个 Run。
- 刷新并重新打开会话后，提问只显示一次，最终天气回答仍在。

### E-01：通过

- 新会话中执行 `execute(target=workspace, command="printf partial > uat-verify.txt; sleep 8", timeout_sec=1)`：
  - 返回 `SANDBOX_TIMEOUT`，`outcome_known=false`。
  - Agent 没有重试同一命令。
- Agent 调用新增的 `read_workspace_file` 读取 `uat-verify.txt`：
  - 读取成功，返回 7 个字符 `partial`。
  - 在不放开 workspace 写入门禁的前提下完成了只读核实。
- 独立交付动作仍可执行；同命令重试仍被 `TOOL_RETRY_UNSAFE` 拦截。

### S-01：通过

- 同一 Run 内先调用 `load_skill('uat-missing-skill')`，返回 `SKILL_NOT_EFFECTIVE`。
- Agent 未终止，继续调用 `weather-query` 查询北京，返回实时天气与未来两天预报。
- 失败与合法替代路径都出现在同一轮对话中。

### Skill 夹具复验

- `weather-query` 新包版本 `1.0.3`，当前 artifact `009386f0-871a-4e37-baba-a767df9105f1`。
  - 直接读取北京天气成功；Agent 调用 `weather-query` 返回实时天气与 3 天预报。
- `smoke-platform-sample` 新包版本 `0.1.1`，当前 artifact `b5551cbf-3c5b-4738-aa19-c75ebf7e81ae`。
  - `/v1/rtt`、`/api/v1/ping`、`/api/v1/targets` 全部通过；`ping` 与 `targets` 的 TraceId 均与实际请求头一致。
  - 原失败原因是本地导入的 artifact 仍是旧 `0.1.0`（用脚本自造 TraceId 对比平台回显），不是 Skill 代码需要改。

### 资产查询夹具自动化验证

- 新 Skill：`mssp-asset-query-local`，当前版本 `1.1.0`，Artifact `d124bab0-27af-475f-a81a-aebcbeb4d4c7`，已绑定 `demo01`。
- S-02/S-03/S-10 的浏览器验证在 `1.0.2`/`1.0.3` 上完成；`1.1.0` 增加 `platform` 校验与 `incomplete/issues`，E-04 在 `1.1.0` 上验证，旧查询行为不变。
- fake platform 新增客户 `9001`（批量资产客户），`asset_bulk` 确定性生成 1200 条资产；`1001` 仍保留 4 条小样本。
- S-02 Run `4b814aef-f1bc-4d83-bcf1-6a0acb06d8ea`：
  - 默认返回 20/1200，`truncated=true`；
  - 缩小到 `10.60.0.` 后分页取全 254 条，明确说明其余 946 条未核实；
  - 未交付未核实的全量文件。
- S-03 真实企微 Run `7b633936-90ea-4e70-832b-3ad60e715022`：
  - 首条“帮我拉一下资产 IP 明细”触发 `ask_user`，问题落库并等待；
  - `HUMAN_QUESTION` 投递 `DELIVERED`；
  - 回答 `1001` 后同一 Run resume，返回 4 条资产，终态 `COMPLETED`；
  - `TERMINAL` 投递 `DELIVERED`。
- S-03 重复发送 Run `1d8ed727-7816-4a92-a2c3-6403c0f87f99`：沿用上一轮客户 1001，直接返回 4 条资产并 DELIVERED。
- S-03 WebChat 等价 Run `c85b3b5b-1e90-4d7c-bedb-f925e3b18e4a` 也通过，问题与回答在同一 Run。
- S-10 真实企微 Run `dc799088-46ac-434a-9c49-7871a28bff83`：
  - 首条“查一下客户 1001 的资产记录”Run `66197898-9aa6-46a5-8356-8981324e35fe`，4 条资产，TERMINAL DELIVERED；
  - 第二条“把客户 9001 的全部资产 IP 明细导出成 CSV”触发 `export=true`；
  - Artifact `d90983d8-5297-4005-b031-cea6b1648b57`，`text/csv`，175,975 字节；
  - `deliver_artifact` 返回已交付，终态消息 DELIVERED；
  - CSV 实测 1200 行，首行 `10.60.0.1`，末行 `10.60.4.184`。
- S-10 重复发送 Run `75f65753-d192-4a77-9bfe-328bf728773e`：再次导出 1200 条，Artifact `be35dfe7-f7ca-4716-816d-4107d2b970d7`，TERMINAL DELIVERED。
- S-10 WebChat 等价 Run `d97e6488-3b45-4a87-9f3c-2011d6e3a95c` 同样通过，Artifact `00e4f608-c00c-40f7-9e30-5b8565adc169`。

### E-02：通过（契约自动化）

- `tests/agent_runtime/test_skill_tools.py::test_execute_rejects_removed_script_parameter` 通过。
- 调用 `execute(skill_key=..., script=...)` 返回明确错误，说明 `script` 不可用及允许的键；不会启动执行。
- 真实模型在自然语言下会主动省略 `script`，因此该场景只适合契约测试，不适合可重复的真实 E2E。

### E-05：部分通过（集成自动化）

以下 Gateway/Connector 契约测试通过：

- `test_channel_side_failure_releases_the_placeholder_so_a_retry_can_send`
- `test_e06_failed_delivery_then_retry_succeeds_and_the_user_gets_it_once`
- `test_b117_send_failure_is_not_accepted_and_allows_retry`
- `test_dedupe_failure_with_send_failure_is_not_faked_as_delivered`

覆盖首次发送失败、占位释放、重试成功且只送达一次、发送失败不伪造成 DELIVERED。

真实企微验证使用了一次性本地注入（验收后已回退，当前代码和 Connector 均不含注入开关）：

- 透明重试 Run `cca43e76-36f6-4c77-8a1d-a80a36544684`：
  - 首次产物命令 `89b3a426...` 在渠道前失败，Connector 内部重试后成功；
  - 用户截图确认只收到一份 `asset-query-9001-20261127T142833Z.csv`，没有重复。
- 用户可见失败变体 Run `00c00a6f-9a6e-4a08-996e-2cc9664844de`：
  - 前 3 个 `deliver_artifact` 命令各 3 次尝试失败，第 4 个命令成功后交付；
  - 用户截图确认只收到一份 `asset-query-9001-20261127T155208Z.csv`（172K，1200 行）；
  - Agent 文案把“前三次失败”写成“前两次失败”，属于模型概括偏差，服务端 canonical 事件和 Connector 日志正确。
- 当前 Connector 已按正常命令启动，无 `CONNECTOR_TEST_ARTIFACT_FAILURES` 环境变量；临时注入代码已回退。

### E-04：通过

- 跨平台：要求查客户 9001 在 XDR 的资产；Agent 先查 `session-manager list-platforms`，确认没有 XDR 平台，明确说明无凭据/无映射，没有用 MSSP 数据冒充。
- 缺页/重复：客户 9002 导出 Run `ca6cc568-928f-45ce-9461-3ec1bcc6a922`：
  - 平台 total=40，CSV 实际 38 行、38 个唯一 IP；
  - 明确报告 `MISSING_ROWS=2`、`DUPLICATE_ROWS=2`；
  - 指出缺失 `10.70.0.6` 与 `10.70.0.26`；
  - 交付 Artifact `f3c7ab68-d767-4046-9e37-e71e874f25bc`，未声称完整。
- fake platform 新增 `asset_page_fault`；Skill 新增 `incomplete`/`issues` 与平台参数校验。

### S-06：通过

- 草稿 `uat-draft-ok`：`draft_id=f8072b85-f175-48d9-8cd5-a11a8903a40f`。
- v1 试跑：`{"ok": true, "value": "draft-v1"}`。
- 修订到 v2 后试跑：`{"ok": true, "value": "draft-v2"}`。
- 提交 `version=1.0.0` 返回 `artifact_id=dbd986a2-7848-46c2-b192-fc5d5e7d940a`，`review_status=PENDING`。
- 管理员审批后 `review_status=APPROVED`，自动绑定 `demo01`。
- 新对话调用默认入口：`SUCCEEDED`，stdout `{"ok": true, "value": "draft-v2"}`。
- 同轮 workspace 命令 `printf workspace-ok`：stdout `workspace-ok`，`changed_files=[]`。
- 备注：首次提交尝试因 `muad.skill.json` 缺失被审查拦截，模型一度误写 smoke 样例内容；修正 manifest 后重新提交成功。该过程也验证了审查不通过时不会发布。

### S-04：通过

- 临时把 `compaction.history_budget_tokens` 调为 256，在同一 WebChat 会话发送 12 轮短消息。
- 最终 Run `df6c6862-b8c1-4b37-b3f4-fca9b590a6da`：
  - `runtime_snapshot.policy_json.compaction.history_budget_tokens=256`；
  - `CONTEXT_COMPACTED` 显示 `budget` 层触发，`dropped_records=23`；
  - 当前请求包含“客户 1001 / MSSP / 关键字 10.10.1.11”；
  - Agent 直接返回该 IP 的 1 条资产，没有追问或要求补齐历史。
- 验收后已恢复 `history_budget_tokens=65536`（revision 17）。

### B-03：通过

- 临时把平台设置改为 `max_turns=5`、`max_tool_calls=3`，新建对话请求“资产查询 + 天气查询”。
- 首轮 Run `35827af4-d4a7-4493-bd94-08a937051a5e`：
  - `load_skill` ×2 + 资产查询成功，天气调用因 `TOOL_BUDGET_EXHAUSTED` 未执行；
  - 终态 `FAILED`，但正文明确“已取得 1 个工具结果，但未形成最终结论”，并提示可回复“继续”。
- 恢复设置 `max_turns=20`、`max_tool_calls=30` 后回复“继续”：
  - 新 Run `bd739dfd-fd09-4a6d-a9f4-ee853814768e` 只调用 `weather-query`；
  - 资产结果直接复用上一轮 canonical 事实，没有重复查询；
  - 最终汇总 4 条资产 + 北京天气，终态 `COMPLETED`。

## 其他通过项证据

- S-11 交付产物：
  - ZIP：`delivery-test.zip`，Artifact `ea3bbbba-931f-4668-81eb-838de4379df4`
  - ZIP 内容：`a.txt`（5856 bytes）、`b.csv`（830 bytes）、`c.json`（215 bytes）
  - TXT：`平台对接值守说明.txt`，Artifact `9d9ed9e0-6e4e-480d-ad48-3f8e508a494c`，12,664 字节，约 5048 字
  - TXT 会话 `3ae16b1e-1656-43c5-864a-b8085664af6b` 刷新后仍显示 `平台对接值守说明.txt` 附件卡片和“text/plain · 12.4 KB”。
  - ZIP 会话刷新后也仍能看到下载入口。
- S-05：`uat-missing-a` 失败 → `smoke-platform-sample` 加载成功 → `uat-missing-b` 失败，之后 Agent 仍正常响应。
- S-08：
  - 设置页显示“连续同类失败停止阈值 默认 2 · 2 ~ 10”，说明文案正确。
  - 保存 3 后 Run `815b3564-c6bd-433a-ab37-cc574f73e4f0` 的 `runtime_snapshot.policy_json.max_identical_failures=3`。
  - 该 Run 停在 `WAITING_INPUT` 时把设置改回 2；回答 `continue` 后仍是同一 Run，终态 `COMPLETED`，快照仍为 3。
  - 执行导出 `GET /api/v1/runs/815b.../export?format=json`：`effective_budget.max_identical_failures=3`、`history_budget_tokens=65536`、`summary_enabled=false`。
  - 新 Run `00c00a6f-9a6e-4a08-996e-2cc9664844de` 同类导出为 `max_identical_failures=2`，历史预算和摘要状态相同。
  - 当前设置为 2（revision 17）。

## 环境限制

- 一次 TXT 产物请求遇到模型服务不可用，未生成文件。
- S-03 / S-10 已在真实企微通过；E-04 已通过。
- E-05 已用一次性本地注入验证首次失败/重试/只送达一次；发送结果未知变体未验证，注入代码已回退。
- B-02 已完成工具/历史预算、阈值 A/B 和摘要开关单项对照；样本仍有限，详情见专项报告。
- E-02 已由契约测试覆盖；模型主动省略了 `script`，未做真实 E2E。
- 一次回答提交遇到本地 Runtime/Gateway reload 卡住；重启 dev 服务后用同一消息号重试成功，不是 Run 状态机失败。

## 结论

S-01 至 S-11、E-01 至 E-04、B-02（单项对照）、B-03 均已通过；E-02 由契约测试覆盖，E-05 已用一次性本地注入在真实企微验证首次失败/重试/只送达一次（注入代码已回退），发送结果未知变体未验证。`weather-query`、`smoke-platform-sample`、`mssp-asset-query-local` 和 `uat-draft-ok` 已更新/绑定。S-03/S-10 已在真实企微通过，S-10 交付 1200 行 CSV；E-04 交付 38/40 行并明确缺口。

仍建议在 archive 前完成：
1. 在独立可控环境复验 E-05 发送结果未知变体；
2. 按需补 B-02 的真实业务失败样本、事实位于中部的长对话和摘要 token 记录；
3. 人工确认企微客户端里 CSV 附件可正常打开（服务端链路已显示交付成功）。
