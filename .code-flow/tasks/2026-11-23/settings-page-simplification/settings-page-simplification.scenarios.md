---
{"description":"人工验收场景","requirement":"settings-page-simplification"}
---
# 系统设置页精简验收

**真实用户场景验收在内网/UAT 执行**：接真实 MSSP/MSSW、真实模型、真实企微和真实用户数据。dev/local 只用 `tools/fake-business-platform` 做契约、迁移和 UI 回归，**不作为“真实感受”验收依据**。

每个场景使用独立测试租户；涉及迁移的场景在独立测试库执行。执行后记录租户、设置 revision、字段数、页面截图/字段清单、配置渲染结果、run id、平台请求 id 与迁移日志；不得记录 Secret 明文。

## S-01 设置页只剩租户策略字段

- **负责**：管理员 + 功能验收人。
- **前置**：已完成本需求与 `conversation-context-retention` 的迁移/构建。
- **步骤**
  1. 以管理员登录 Console，打开「系统设置」。
  2. 逐个分组核对字段：上下文压缩 4、agent 5、task 4、memory 11、learning 1、artifact 1、auth 4、locale 2、im 2、capacity 3、model_pool 5、webchat 8、egress 2、schedule 6。
  3. 确认不存在 `mcp` 分组。
  4. 搜索页面中是否出现 `consolidation_*`、`retrieval_*`、`eval_*`、`reconnect_*`、`inbound_batch_quiet_ms`、`inbound_batch_max_wait_ms`、`max_snapshot_bytes`、`confirmation_ttl_sec`、`max_archive_files`、`cleanup_batch_size`、`max_tools_per_server`、`model_wait_timeout_sec`。
- **期望**：总字段数为 58；14 个分组；每个分组字段集与设计完全一致；上述内部调参字段均不出现；没有“高级设置”里藏着这些字段。
- **实际结果**：待执行。

## S-02 Agent 执行 5 项全部保留并可调

- **负责**：管理员 + 功能验收人。
- **步骤**
  1. 打开 `agent` 分组，确认 `max_turns`、`max_tool_calls`、`deadline_ms`、`max_model_retries`、`max_identical_failures` 5 项都在。
  2. 把 `max_turns` 与 `max_tool_calls` 调高到内网真实任务需要的值，保存。
  3. 新建一个交互式 Run，回读 `policy_json`。
  4. 再新建一个后台 Agentic 任务，回读任务 `execution_snapshot.budget`。
- **期望**：5 项都可保存；新 Run 的 `policy_json` 冻结全部 5 项（尤其 `max_identical_failures`）；新后台任务的 `budget` 与 Run 使用同一组值；旧 Run/旧任务仍用各自冻结值。
- **实际结果**：待执行。

## S-03 后台任务与 Agent 执行共用预算

- **负责**：开发/运维 + 功能验收人。
- **前置**：同一租户下 Agent `runtime_config.budget` 可选择性覆盖部分 Agent 叶子。
- **步骤**
  1. 设置租户级 Agent 5 项，并在 Agent 配置中覆盖其中一项（如 `max_turns`）。
  2. 触发一个后台 Agentic 任务。
  3. 对比交互式 Run 的 `policy_json` 与后台任务 `execution_snapshot.budget`。
  4. 在后台 Agentic 任务中执行一个 Skill 工具，观察超时。
- **期望**：两边 5 项完全一致，Agent 覆盖优先于平台设置；后台任务没有第二套默认；Agentic Skill 工具超时不超过 Agent `deadline_ms`；直接 Skill 任务仍按 `task.execution_timeout_sec`。
- **实际结果**：待执行。

## S-04 完整部署配置与内网/生产取值

- **负责**：运维 + 功能验收人。
- **步骤**
  1. 渲染 `deploy/k8s/overlays/intranet` 与 `deploy/k8s/overlays/production`。
  2. 比对渲染后的 `muad-common` 键集与 `SharedSettings` 非敏感字段集。
  3. 用两套渲染值分别构造 `SharedSettings`，检查启动校验。
  4. 对照 `docs/configuration-profiles.md`，抽查 5 个新增键的默认/内网/生产值。
  5. 检查 Secret 键是否只出现在 Secret 模板。
- **期望**：两套渲染都覆盖全部非敏感键；没有未知键；Secret 键不在 ConfigMap；两套值都通过跨字段校验；生产容量键与锁定 capacity profile 一致；配置档案与渲染结果一致。
- **实际结果**：待执行。

## S-05 IM 进度只作用于 IM 渠道

- **负责**：功能验收人。
- **步骤**
  1. 打开 `im` 分组，确认只有「IM 回复进度节拍」与「单批附件上限」。
  2. 阅读进度节拍说明，确认写明“仅 IM 渠道；WebChat 不受影响”。
  3. 把进度节拍改为 1 秒，分别用企微和 WebChat 发起一次对话。
  4. 观察企微状态帧节拍与 WebChat SSE 流式输出。
- **期望**：企微状态帧按 1 秒节拍；WebChat 的流式输出/心跳不因该设置变化；设置页没有暗示 WebChat 受此字段控制。
- **实际结果**：待执行。

## S-06 压缩分组只剩 4 项

- **负责**：功能验收人。
- **前置**：`conversation-context-retention` 已落地。
- **步骤**
  1. 打开「上下文压缩」分组。
  2. 核对字段。
- **期望**：只有 `history_budget_tokens`、`summary.enabled`、`summary.model_ref`、`tool_result.persist_threshold_bytes`；不再出现 snip/micro/条数/回合数/摘要阈值/单轮预算/预览头尾。
- **实际结果**：待执行。

## E-01 旧设置文档迁移后设置页与新 Run/任务正常

- **负责**：开发/运维 + 管理员。
- **前置**：测试库中存在一份旧 `settings_json`（含全部被删字段）与一条 Agent `runtime_config_json`（含旧 `memory`/`learning`/`task`/`webchat` 覆盖键）。
- **步骤**
  1. 执行本需求迁移（`compaction` 已在归档需求中稳定为 4 项）。
  2. 打开设置页。
  3. 保存一个保留字段。
  4. 新建 Run 与后台 Agentic 任务，回读 `policy_json` / `execution_snapshot.budget`。
- **期望**：设置读取不因未知键失败；设置页只显示新字段；保存成功；新 Run/新任务正常准入；快照中不再出现被删字段；迁移审计可追溯；其他 Agent runtime_config 键逐字保留。
- **实际结果**：待执行。

## E-02 旧字段被提交或旧 revision 被恢复时显式拒绝

- **负责**：开发/运维。
- **步骤**
  1. 用 API 工具向设置保存接口提交一个已删除字段（如 `task.misfire_grace_sec`）。
  2. 打开 Console 版本历史，尝试恢复一个含旧字段的旧 revision。
  3. 尝试恢复一个旧 Task/Run 快照继续执行。
- **期望**：都返回明确的校验/“schema 过旧”错误（统一封套）；不会静默忽略，也不会部分保存；设置页仍正常。
- **实际结果**：待执行。

## E-03 记忆总开关与调度历史保留期迁移

- **负责**：开发/运维。
- **前置**：两个租户：A 的记忆注入上限均 >0，B 的任一上限为 0；A/B 的 Fire 历史保留期与执行历史保留期分别为 (90, 30) 与 (30, 90)。
- **步骤**
  1. 执行迁移。
  2. 回读两个租户的 `settings_json`。
  3. 分别触发一次记忆自动注入与一次调度历史清理。
- **期望**：A 的 `memory.injection_enabled=true`，B 为 false；A/B 的 `schedule.history_retention_days` 都为较大值（只延长不缩短）；原上限/保留值在审计中可见；自动注入与清理行为符合新语义。
- **实际结果**：待执行。

## E-04 迁移幂等

- **负责**：开发/运维。
- **步骤**
  1. 连续执行两次迁移脚本。
  2. 检查 `control.platform_setting` 的 revision 与 `changed_keys`。
  3. 再打开设置页并新建 Run/任务。
- **期望**：第二次不产生重复删除/重复字段；revision 不出现无意义的空追加（或明确记录为 no-op）；设置页与新 Run/任务正常。
- **实际结果**：待执行。

## E-05 本迁移不改写已稳定的压缩分组

- **负责**：开发/运维。
- **前置**：当前 `settings_json` 已满足 `compaction` 4 字段 schema。
- **步骤**
  1. 记录迁移前的 `compaction` 字段与值。
  2. 执行本需求迁移。
  3. 回读最终 `settings_json`，新建 Run 与后台任务。
- **期望**：`compaction` 4 字段与值逐字不变；非压缩旧键被清理；新 Run/任务正常准入；压缩预算语义仍由 `conversation-context-retention` 的测试覆盖。
- **实际结果**：待执行。

## B-01 learning 关闭时只显示总开关

- **负责**：管理员 + 功能验收人。
- **步骤**
  1. 关闭 `learning_enabled` 并保存。
  2. 查看 learning 分组。
  3. 开启 `learning_enabled`，再查看。
- **期望**：无论开关状态，分组都只有 `learning_enabled`；不再出现检索/试验/评估/验证调参；关闭时后台仍按部署配置默认值安全工作。
- **实际结果**：待执行。

## B-02 WebChat 分页/轮询/重连仍正常

- **负责**：功能验收人。
- **前置**：设置页已不再显示 `history_page_size`、`notification_poll_sec`、`reconnect_*`。
- **步骤**
  1. 打开 WebChat，加载一个有 60 条以上消息的会话，确认历史分页正常。
  2. 在弱网/断网后恢复，确认自动重连按配置工作。
  3. 停留一段时间，确认通知轮询正常。
- **期望**：client-config 仍返回这些字段（值来自后端 `SharedSettings`）；前端历史分页、通知轮询、重连行为与迁移前一致；设置页不显示它们。
- **实际结果**：待执行。

## B-03 Task 批次并发仍按“租户设置 ≤ 部署上限”生效

- **负责**：开发/运维。
- **前置**：`task.batch_max_concurrency` 保留；部署 `BATCH_PLATFORM_LIMIT` 设为 N。
- **步骤**
  1. 把租户的批次并发设为小于 N 的值，触发一次批量子任务（或 fan-out）。
  2. 观察实际并发。
  3. 把租户值设为大于 N，重跑一次。
- **期望**：实际并发取租户值与部署上限的较小值；设置页仍显示租户级批次并发；修改部署上限重启后新执行生效；既有执行不被改写。
- **实际结果**：待执行。

## B-04 新增环境键在启动期生效并可排障

- **负责**：运维 + 开发。
- **步骤**
  1. 在测试部署中把 `MEMORY_CONSOLIDATION_TIMEOUT_SEC`、`TASK_DELIVERY_MAX_ATTEMPTS`、`WEBCHAT_HISTORY_PAGE_SIZE` 改为非默认值，滚动重启。
  2. 分别触发记忆提炼、Task 投递、WebChat 历史分页。
  3. 查看启动日志与生效值。
- **期望**：新值在重启后生效；日志打印非敏感值；未配置时使用 `SharedSettings` 默认并记录 warning；不读取平台设置旧字段。
- **实际结果**：待执行。

## R-01 真实 MSSP/MSSW 用户旅程

- **负责**：功能验收人 + 业务平台联调人 + 运维。
- **前置**
  - 内网/UAT 已按 `docs/operations/mssw-channel-deploy.md` 写入真实 MSSP/MSSW 的 `EXTERNAL_ASSERTION_PLATFORMS_JSON`（Secret），并重启 Console / Gateway；`/api/v1/external-platforms` 显示 `mssp`、`mssw` 且 `configured=true`。
  - Console 已创建 MSSP/MSSW 入口，管理员已绑定真实用户并授权 Agent。
  - 真实模型、真实企微 Connector 可用；用户面配置与生产一致。
- **步骤**
  1. 从 MSSP/MSSW 业务页面打开对话入口，完成一次流式问答。
  2. 执行停止、上传附件、下载产物、查看历史与未读通知。
  3. 触发一次后台 Agentic 任务和一次直接 Skill 任务，确认结果与产物。
  4. 把 `agent.max_turns` / `max_tool_calls` 调高，分别新建 Run 与后台任务，确认新值生效。
  5. 观察企微回复进度与 WebChat/MSSP/MSSW 的 SSE 流式输出。
- **期望**：真实平台登录/入口/流式/停止/上传/下载/历史/通知全部正常；后台任务与交互式 Run 使用同一套 Agent 预算；企微进度受 `im.progress_interval_sec` 影响，WebChat/MSSP/MSSW 的 SSE 不受影响；记录 run id、平台请求 id 与截图。
- **实际结果**：待执行。

## R-02 内网/UAT 与生产用户面配置一致性

- **负责**：运维 + 开发。
- **前置**：`deploy/k8s/overlays/intranet` 与 `production` 已渲染；配置档案已标注 `user-facing` / `infrastructure`。
- **步骤**
  1. 比对两套渲染结果的 `user-facing` 键。
  2. 比对内网/UAT 与生产的租户平台设置（`agent`、`memory`、`learning`、`im`、`webchat`、`capacity`、`schedule`）与 `platform-settings.production-like.json` 基线。
  3. 故意修改一个 `user-facing` 键（如 `WEBCHAT_NOTIFICATION_POLL_SEC`）只改内网，运行配置契约测试。
  4. 恢复后再次运行测试。
- **期望**：`user-facing` 键逐键相等；租户平台设置与基线一致，差异有原因记录；故意制造的差异被 parity 测试指出；恢复后通过。`infrastructure` 键允许不同，但必须在档案中写明依据。
- **实际结果**：待执行。
