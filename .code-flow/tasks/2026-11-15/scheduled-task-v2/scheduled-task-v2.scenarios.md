---
{"description":"定时任务与自然语言调度 v2 人工验收场景","requirement":"scheduled-task-v2"}
---
# 定时任务与自然语言调度 v2 验收场景

> **修订版本**：v1.0；状态为待实现/待人工执行，本文不代表验收已通过。

> 这是给**人工测试者**使用的验收清单，不是 E2E 代码或测试注册表。
> 每个场景保留 S-/E-/B- 编号，供 design 与 Task 的 `Scenarios` 字段引用。
> 步骤用用户能执行的界面动作、CLI 命令或 API 请求描述；期望写用户可观察的结果。
> 本需求以 WebChat/WeCom 对话和任务中心为主；Console 只做查看与运营操作。

## 公共前置

- Console、IM Gateway、Agent Runtime、Agent Worker 已启动，数据库迁移到 v2。
- 测试租户下已有：一个可用 Agent、一个可用 Skill、一个可用 MCP 工具、一个可用模型与凭据。
- 测试用户已绑定 WebChat 入口；如需 WeCom 场景，已绑定 WeCom 入口与测试会话。
- 用户 Profile 时区为 `Asia/Shanghai`；需要测试时区时在创建确认卡中显式选择。
- 测试用 Schedule 尽量使用“2–3 分钟后触发的一次性任务”，避免等待整点；周期任务用每分钟/每 5 分钟验证。
- 任务中心入口：WebChat 顶部“定时任务”Tab；Console：`/schedules`、`/tasks`。
- 测试账号可查看自己的 Schedule；Console 账号与测试用户同租户。
- 不在生产库执行；不把真实 Secret、Token、客户数据写入任务文本。

---

## S-01 通过 WeCom 创建固定提醒

- **负责**：待 plan 分配
- **前置**：WeCom 已绑定；用户可正常对话。
- **步骤**：
  1. 在 WeCom 对 Agent 说：“2 分钟后提醒我喝水。”
  2. 查看确认卡：模式、触发时间、时区、结果呈现方式。
  3. 确认创建。
  4. 等待触发。
  5. 在 WeCom 查看是否收到“喝水”提醒；在 Console 查看 Schedule/Fire/Execution。
- **期望**：
  - 模式为 `REMINDER`，不依赖 Skill/Model。
  - 确认卡显示 IANA 时区、首次触发时间、WeCom 投递。
  - 到点后 WeCom 收到固定文本“喝水”。
  - Console 能看到一条 Fire 和一条 Execution；Execution 为 `COMPLETED`。
  - 不出现模型调用或 Skill 执行。

## S-02 通过 WebChat 创建 Skill 定时任务

- **负责**：待 plan 分配
- **前置**：用户有可用 Skill；WebChat 已登录。
- **步骤**：
  1. 在 WebChat 对话中说：“2 分钟后用「周报 Skill」给 A 客户生成一份简报。”
  2. 确认卡选择/确认 Skill、参数、时区、首次触发时间。
  3. 确认创建。
  4. 打开 WebChat“定时任务”Tab，等待触发。
  5. 打开该任务详情，查看 Fire、Execution、结果/产物。
- **期望**：
  - 确认卡显示 Skill 名称、参数、首次触发、结果在任务中心查看。
  - WebChat 聊天流不出现定时任务结果；任务中心能查到结果。
  - Fire 为 `EXECUTION_CREATED`，Execution 为 `COMPLETED`。
  - 结果/产物在任务中心可见；刷新后仍在。
  - 没有为 WebChat 产生主动投递记录。

## S-03 创建 Agentic 定时任务

- **负责**：待 plan 分配
- **前置**：Agent 有可用模型、MCP 工具、用户授权；用户有长期记忆偏好。
- **步骤**：
  1. 在 WebChat 说：“2 分钟后查询深圳天气，并用一句话总结。”
  2. 确认卡查看模式 `AGENTIC`、工具 allowlist、预算、记忆只读标记、时区。
  3. 确认创建。
  4. 在任务中心等待触发并打开详情。
  5. 查看执行事件：模型调用、工具调用、结果。
- **期望**：
  - 确认卡显示具体工具 allowlist，不是“任意工具”。
  - 执行只调用 allowlist 内工具；事件中可见工具名和结果。
  - 任务读取用户记忆偏好（如输出语言）但不写入新记忆。
  - Execution 完成，结果在任务中心可见。
  - Snapshot 显示 `schema_version=4`、memory mode `READ_ONLY`。

## S-04 修改触发时间

- **负责**：待 plan 分配
- **前置**：已创建一个 5 分钟后触发的一次性任务。
- **步骤**：
  1. 在任务详情选择“修改”。
  2. 把触发时间改为 10 分钟后；查看差异。
  3. 确认修改。
  4. 查看任务详情的 Revision、下次触发、Fire 历史。
  5. 等到原时间点，确认没有执行。
  6. 等到新时间点，确认执行。
- **期望**：
  - 修改前展示“原时间 → 新时间”的差异。
  - 修改后 Revision +1，下次触发为新时间。
  - 原时间点没有执行；如果修改前已经生成 Fire/Execution，则显示 `CANCELLED`，不能要求一条尚未生成的 Fire 显示 CANCELLED。
  - 已在运行的旧 Execution 保留旧 Revision/Snapshot；未开始的 QUEUED 旧 Execution 被取消。
  - 新时间只执行一次；历史保留旧 Revision。

## S-05 暂停与恢复

- **负责**：待 plan 分配
- **前置**：已创建一个每 2 分钟触发的任务，且当前没有运行中的 Execution。
- **步骤**：
  1. 在任务详情点击“暂停”。
  2. 等待超过两个周期，查看任务状态与 Fire 历史。
  3. 点击“恢复”。
  4. 查看下次触发时间。
  5. 等待下一次触发。
- **期望**：
  - 暂停后状态为 `PAUSED`，没有新的 Fire。
  - 队列中未开始的 Execution 被取消；运行中的 Execution 继续（如无则无）。
  - 恢复后状态为 `ACTIVE`，下次触发从当前时间之后重算。
  - 暂停期间的周期不补发；历史中不出现“补发”Fire。
  - 用户能看到暂停/恢复的操作结果。

## S-06 取消任务

- **负责**：待 plan 分配
- **前置**：已创建任务；另有一个正在运行的 Agentic 任务。
- **步骤**：
  1. 在任务详情点击“取消”。
  2. 选择 `FUTURE_ONLY`（停止未来触发，保留进行中/待处理），确认。
  3. 查看任务状态、Fire 历史、运行中的 Execution、OPEN Human。
  4. 对另一个运行中的任务选择 `ALL_ACTIVE`（终止全部），确认。
  5. 查看运行中/等待中的 Execution 与 Human 状态。
- **期望**：
  - `FUTURE_ONLY`：不再产生新 Fire；QUEUED 且 `started_at IS NULL` 取消；RUNNING/WAITING_EXTERNAL/WAITING_HUMAN 保留；OPEN Human 保留，用户仍可回答。
  - `ALL_ACTIVE`：终止全部进行中；WAITING_HUMAN 关闭 interaction；WAITING_EXTERNAL 取消 operation；RUNNING 协作取消。
  - 不出现“Execution 还等待、interaction 已关闭”的卡死。
  - 已发生的外部副作用不伪称回滚；历史保留。

## S-07 查看完整调度历史

- **负责**：待 plan 分配
- **前置**：准备一条成功、一条并发跳过、一条依赖失败、一条取消的 Fire。
- **步骤**：
  1. 打开任务中心 → 某 Schedule 详情。
  2. 切换到“调度历史”。
  3. 逐条查看 Fire 状态、计划时间、Execution、跳过/失败原因。
  4. 打开一条 Execution 详情。
  5. 打开 Console `/schedules` 与 `/tasks`，查看同一数据。
- **期望**：
  - 每个真实处理区间都有 Fire 记录；DST gap/second-fold 以调度事件展示，不伪造 Fire。
  - 成功/跳过/准入失败/取消状态分开显示，不混用一个字段。
  - 跳过显示原因（如重叠）和 `missed_count`；失败显示修复动作。
  - Execution 详情显示结果/错误/耗时/版本摘要。
  - WebChat 与 Console 读取同一数据，不出现两份结果。

## S-08 多 Scheduler / 多 Worker 幂等

- **负责**：待 plan 分配
- **前置**：测试环境运行 2 个 Worker 副本；创建一个 2 分钟后触发的一次性任务。
- **步骤**：
  1. 等待触发。
  2. 在 Console 查看该 Schedule 的 Fire 历史。
  3. 查看 Execution 列表。
  4. 重复运行同一场景 3 次。
- **期望**：
  - 同一计划时刻只出现一条 Fire。
  - 只出现一个根 Execution。
  - 结果只产生一次；不重复执行业务。
  - 不出现同一 Fire 关联两个根 Execution。

## S-09 WebChat 待处理 Human 问题

- **负责**：待 plan 分配
- **前置**：创建一条 Agentic 任务，目标故意缺少“客户”参数，执行时会询问。
- **步骤**：
  1. 等待任务触发。
  2. 打开 WebChat“定时任务”Tab。
  3. 查看“待处理”徽标与列表。
  4. 打开该问题，阅读问题与截止时间。
  5. 填写客户并提交。
  6. 等待同一 Execution 继续并完成。
  7. 检查聊天流与投递记录。
- **期望**：
  - 任务中心出现待处理项；聊天流不出现问题卡片或结果。
  - 问题显示任务名、问题、截止时间、输入字段；模型未提供 `input_schema` 时显示自由文本回答框。
  - 提交后同一个 Execution 从断点继续，不新建 Fire。
  - 完成结果在任务中心可见。
  - 不产生 WebChat 投递/通知记录。

## S-10 WeCom Human 问题与回复

- **负责**：待 plan 分配
- **前置**：两个 WeCom 测试会话各创建一个会询问的 Agentic 任务。
- **步骤**：
  1. 等待两个任务触发。
  2. 在两个 WeCom 会话分别查看问题。
  3. 在会话 1 回复答案。
  4. 在会话 2 回复答案。
  5. 查看两个任务的 Execution 是否各自恢复并完成。
  6. 再在同一会话制造两个 OPEN 问题，回复时观察澄清。
- **期望**：
  - 问题推送到各自创建时的 WeCom 会话。
  - 回复绑定到对应任务，不串任务。
  - 同一会话多个 OPEN 时，系统列出任务要求指定，不猜。
  - 恢复后同一 Execution 完成，结果推送到对应会话。
  - 任务中心（如用户有 WebChat）也能看到同一状态。

## S-11 WebChat 完全不投递

- **负责**：待 plan 分配
- **前置**：WebChat 创建一条 2 分钟后触发的 Skill/Agentic 任务。
- **步骤**：
  1. 保持在 WebChat 对话页，等待触发。
  2. 观察聊天流是否出现结果/通知/红点。
  3. 打开“定时任务”Tab 查看结果。
  4. 在 Console/数据库检查 Delivery 记录（如可观察）。
- **期望**：
  - 聊天流没有定时任务结果、没有主动通知、没有红点。
  - 只有任务中心 Tab 的待处理/完成状态。
  - 不产生 WebChat Delivery 行；不写 channel notification。
  - 结果在任务中心刷新后仍可见。

## S-12 MSSWWEB 契约预留（任务中心另立需求，本需求不落地）

- **负责**：待 plan 分配（后端/契约测试）
- **前置**：WebChat 任务中心已实现；本需求不挂 MSSWWEB 任务中心路由与页面（另立需求）。
- **步骤**：
  1. 用 Fake MSSWWEB `ChatPrincipal` 调用 TaskCenterService 的契约测试。
  2. 对比 WebChat 与 Fake MSSWWEB 的列表/详情/待处理响应结构。
  3. 确认 `/api/v1/msswwweb/task-center/*` 未挂载。
  4. 确认 WebChat 页面不依赖 MSSWWEB 字段。
- **期望**：
  - 两个 surface 返回同一套 items/detail/pending 结构。
  - 数据范围是 user+agent，entry/channel 只做元数据。
  - 本需求没有 MSSWWEB 路由/页面；service/contracts 已可复用，另立需求落地时不需要改契约。
  - 后续只需加路由和 BFF 页面，不改表/service/contracts。

## S-13 Console 查看与运营操作

- **负责**：待 plan 分配
- **前置**：Console 已登录；同租户有 Schedule。
- **步骤**：
  1. 打开 `/schedules`，查看新状态/模式/时区/下次触发。
  2. 打开详情，查看 Revision、依赖、投递方式、Fire 历史。
  3. 执行暂停、恢复、取消。
  4. 打开 `/tasks`，查看 Execution 状态/结果。
  5. 检查页面是否有“创建定时任务”入口。
- **期望**：
  - 列表/详情显示新字段与新状态。
  - 暂停/恢复/取消可用，反馈明确。
  - 没有创建入口；不能从 Console 编辑用户任务意图。
  - 权限行为与当前一致（Builder 与 Admin 同一人，不新增 ADMIN-only 限制）。

## S-14 创建幂等与 Draft 重放

- **负责**：待 plan 分配
- **前置**：准备同一 Draft 与同一 `Idempotency-Key`。
- **步骤**：
  1. 确认创建一次，记录 Schedule。
  2. 用同一 Key、同一 payload 再次提交。
  3. 用同一 Key、不同 payload 再次提交。
  4. 让 Draft 过期后，用**不同** Key 提交。
  5. 让 Draft 过期后，用**同一** Key/同一 payload 重放。
- **期望**：
  - 同 Key 同 payload 返回同一 Schedule，不重复创建（即使 Draft 已 CONFIRMED/EXPIRED）。
  - 同 Key 不同 payload 返回冲突，不覆盖。
  - 过期 Draft + 不同 Key 返回“需重新预览/确认”，不自动创建。
  - 列表只有一条 Schedule。

## S-15 Agentic 工具越权

- **负责**：待 plan 分配
- **前置**：创建 Agentic 任务，allowlist 只包含工具 A；任务目标会尝试工具 B。
- **步骤**：
  1. 触发任务。
  2. 查看执行事件与结果。
  3. 检查是否调用工具 B。
  4. 检查任务是否需要用户补充/失败。
- **期望**：
  - 只调用 allowlist 内工具 A。
  - 对工具 B 的请求返回“工具未授权”，不产生外部调用。
  - 任务以可解释错误/待处理结束，不静默扩权。
  - 事件中可见 `TOOL_NOT_ALLOWED` 或等价用户可读原因。

## S-16 Agentic 记忆只读

- **负责**：待 plan 分配
- **前置**：用户已有一条“输出用中文”的长期记忆；创建一个 Agentic 任务。
- **步骤**：
  1. 触发任务，查看结果语言/偏好。
  2. 在任务中使用可能触发“记住……”的表达。
  3. 任务完成后打开用户记忆列表。
  4. 查看 Snapshot/Execution 详情中的 memory mode。
- **期望**：
  - 任务读取并使用“中文输出”偏好。
  - 任务没有新增/修改长期记忆。
  - Snapshot/详情显示 `READ_ONLY`。
  - 如 Agent 认为需要记忆，只能产生提议/提示，不能直接写入。

---

## S-17 Agentic 调用 ASYNC Skill

- **负责**：待 plan 分配
- **前置**：Agent 有 ASYNC Skill 工具；用户有授权；创建 Agentic 任务时 allowlist 包含该工具；测试工具可控制结果返回时机。
- **步骤**：
  1. 创建一条 2 分钟后触发的 Agentic 任务，同一模型回合调用两个 ASYNC Skill。
  2. 触发后打开任务中心详情。
  3. 观察 Execution 是否进入 `WAITING_EXTERNAL`、`wait_kind=ASYNC_OPERATION`。
  4. 让两个结果乱序返回；观察是否按 call_id 聚合。
  5. 构造“结果早到”：在 Worker 进入等待前结果已 ready。
  6. 构造“提交回执丢失”：提交成功但 Worker 未拿到 operation_id。
  7. 构造“checkpoint 前崩溃”：提交成功、Worker 崩溃后 reclaim。
  8. 重复投递同一结果回调。
- **期望**：
  - 结果早到时直接继续，不丢唤醒；不会先等待再错过结果。
  - 提交回执丢失时按 `call_id + input_hash` 找回 operation，不重复提交。
  - checkpoint 前崩溃后 reclaim 能恢复：ready 继续、pending 补等待。
  - 多 operation 乱序结果按 call_id 匹配；全部 terminal 后恢复；部分失败按策略失败/询问。
  - 每个 ASYNC 调用有一个真实 Skill 子 Task 作为 durable 执行载体；子 Task 崩溃后可被其他 Worker 重领。
  - 子 Task 终态经结果 outbox 写回父 Execution 的 Task-owned 收件箱；父 Execution 继续等待，不被 BATCH fan-in 聚合。
  - 重复回调按 `event_id` 幂等，不重复消费。
  - 迟到回调不能复活终态；不新建 Fire/Run；只新建 Skill 子 Execution 作为执行载体。
  - 取消/超时可终止 operation。

## E-01 缺少时区或参数

- **负责**：待 plan 分配
- **前置**：用户 Profile 没有时区；或任务缺少客户参数。
- **步骤**：
  1. 说“每天 8 点提醒我开会”。
  2. 查看确认卡/错误提示。
  3. 补充时区/参数后再确认。
- **期望**：
  - 系统询问时区，不使用服务器时区。
  - 缺少参数时明确列出缺什么，不创建 ACTIVE。
  - 补齐后可继续创建；原 Draft 不静默变更。

## E-02 Skill 删除/禁用

- **负责**：待 plan 分配
- **前置**：已创建一个 Skill 定时任务；该 Skill 可用。
- **步骤**：
  1. 在 Console 禁用/删除该 Skill。
  2. 等待下一次触发。
  3. 查看任务中心/Console 的 Fire/Execution/Schedule 状态。
  4. 恢复 Skill，点击“恢复任务”。
- **期望**：
  - Fire 记录 `FAILED_ADMISSION`，Schedule 进入 `NEEDS_ATTENTION`。
  - 不创建可执行 Execution；不重复刷屏。
  - 任务中心显示原因与修复动作。
  - 恢复后重新 Admission，成功后继续未来触发。

## E-03 重复 claim / 重复 Fire

- **负责**：待 plan 分配（集成）
- **前置**：两个 Scheduler/Worker 并发处理同一 due Schedule。
- **步骤**：
  1. 让两个副本同时扫描。
  2. 查看 Fire 历史与 Execution。
  3. 检查业务结果是否重复。
- **期望**：
  - 唯一约束下只有一条 Fire。
  - 只有一条根 Execution。
  - 业务只执行一次。
  - 冲突被记录为重复阻止，不报用户错误。

## E-04 Draft 幂等冲突

- **负责**：待 plan 分配
- **前置**：同一 Draft 已确认。
- **步骤**：
  1. 用同一 Draft 再次确认。
  2. 用同一幂等键提交不同参数。
  3. 取消 Draft 后再确认。
- **期望**：
  - 同 Draft/同指纹返回首次 Schedule。
  - 不同参数返回冲突，不覆盖。
  - 已取消/过期 Draft 不允许确认。

## E-05 更新竞态

- **负责**：待 plan 分配
- **前置**：两个页面/请求同时编辑同一 Schedule。
- **步骤**：
  1. 两个编辑都读取同一版本。
  2. 第一个提交成功。
  3. 第二个提交。
- **期望**：
  - 第二个返回版本冲突，不覆盖第一个修改。
  - 页面提示刷新/查看最新版本。
  - 历史 Revision 不丢失。

## E-06 暂停与触发竞态

- **负责**：待 plan 分配（集成）
- **前置**：Schedule 即将触发；用户同时点击暂停。
- **步骤**：
  1. 在触发窗口内暂停。
  2. 查看 Fire/Execution。
  3. 等待并查看是否复活。
- **期望**：
  - 若暂停赢得竞态，不创建新 Fire。
  - 已创建的 Fire/Execution 按策略处理：QUEUED 取消，RUNNING 继续。
  - 不会因暂停/恢复把已取消的执行复活。

## E-07 Human 超时

- **负责**：待 plan 分配
- **前置**：Agentic 任务触发并询问；用户不回答。
- **步骤**：
  1. 等到问题截止时间。
  2. 查看任务中心/Execution。
  3. 再尝试回答。
- **期望**：
  - Interaction 变为 `TIMED_OUT`。
  - Execution 变 `CANCELLED`，原因 `HUMAN_TIMEOUT`。
  - 再回答被拒绝，不恢复。
  - 任务中心显示超时原因。

## E-08 多个 OPEN Human 问题

- **负责**：待 plan 分配
- **前置**：同一用户同一会话有两个任务同时提问。
- **步骤**：
  1. 在 WeCom 回复一句答案。
  2. 查看系统是否要求指定任务。
  3. 在任务中心分别按任务回答。
- **期望**：
  - 不把答案随机绑定到其中一个任务。
  - 系统列出任务名/问题要求指定。
  - 任务中心按 `interaction_id` 回答无歧义。
  - 两个任务各自恢复。

## E-09 WeCom 路由失效

- **负责**：待 plan 分配
- **前置**：任务创建后解绑 WeCom 入口或删除 bot。
- **步骤**：
  1. 等待任务触发/提问。
  2. 查看投递失败记录。
  3. 查看任务中心/任务状态。
- **期望**：
  - 不猜测其他会话/用户。
  - Delivery 记录失败；Execution 不被误判为业务失败。
  - 任务中心仍可查看；有 WebChat 身份时可回答。
  - 超时后按 Human 超时处理。

## E-10 Fire 时工具缺失/撤权

- **负责**：待 plan 分配
- **前置**：Agentic 任务 allowlist 中某工具被禁用或用户 Grant 撤销。
- **步骤**：
  1. 等待下一次触发。
  2. 查看 Fire/Schedule 状态。
  3. 恢复工具/授权后点击恢复。
- **期望**：
  - Fire `FAILED_ADMISSION`，Schedule `NEEDS_ATTENTION`。
  - 不创建可执行 Execution。
  - 显示缺失工具/授权及修复动作。
  - 恢复后重新 Admission。

## E-11 WebChat 误投递

- **负责**：待 plan 分配
- **前置**：WebChat 创建任务并完成。
- **步骤**：
  1. 检查聊天流。
  2. 检查通知/红点。
  3. 检查任务中心。
  4. 检查后端 Delivery 记录（如可观察）。
- **期望**：
  - 聊天流无结果/通知。
  - 没有 WebChat Delivery 行/notification。
  - 结果只在任务中心。
  - 不出现“已发送”假状态。

## E-12 Console 创建入口

- **负责**：待 plan 分配
- **前置**：Console 已登录。
- **步骤**：
  1. 查看 Schedule 页面是否有“创建”按钮。
  2. 尝试直接调用 Console 创建接口（如存在）。
  3. 查看审计/错误。
- **期望**：
  - 页面没有创建入口。
  - 服务端不提供 Console 创建能力；直接调用被拒绝/不存在。
  - 不影响 Console 现有查看/暂停/恢复/取消。

---

## E-13 ONCE 准入失败

- **负责**：待 plan 分配
- **前置**：创建一个 2 分钟后触发的 ONCE Skill 任务；触发前禁用该 Skill。
- **步骤**：
  1. 等待触发。
  2. 查看 Schedule/Fire 状态。
  3. 尝试“恢复任务”或“修改任务”。
  4. 重新启用 Skill，新建一个 ONCE。
- **期望**：
  - Fire `FAILED_ADMISSION`；Schedule `ENDED(ADMISSION_FAILED)`，不进入 NEEDS_ATTENTION。
  - 不能靠 resume 复活原 ONCE；ENDED 不提供修改，只提供“重新创建”。
  - 新建 ONCE 后可正常执行。
  - 历史保留失败 Fire。

## E-14 模型同轮自动确认

- **负责**：待 plan 分配（集成/契约）
- **前置**：测试工具/脚本模拟模型在同一轮先调用 Draft、再调用创建（不经过用户确认端点）。
- **步骤**：
  1. 触发 Draft 创建。
  2. 不经过用户确认，直接调用创建/更新接口。
  3. 查看响应与任务列表。
- **期望**：
  - 创建/更新返回 `SCHEDULE_CONFIRMATION_REQUIRED` 或等价拒绝。
  - 不产生 Schedule/Revision。
  - 只有用户经鉴权确认端点确认过 Draft 才能创建/更新。
  - 审计记录拒绝原因，不伪造用户确认。

## E-15 Human 回答与取消/超时竞态

- **负责**：待 plan 分配（集成）
- **前置**：Agentic 任务处于 `WAITING_HUMAN`；另有一个 `WAITING_EXTERNAL` 任务；同时取消任务或等待 deadline。
- **步骤**：
  1. 对 WAITING_HUMAN 任务选择 `FUTURE_ONLY`，观察 OPEN interaction 是否保留。
  2. 再选择 `ALL_ACTIVE`，观察 interaction/Execution。
  3. 在 `ALL_ACTIVE` 取消/超时发生后再提交 Human 回答。
  4. 对 WAITING_EXTERNAL 任务选择 `ALL_ACTIVE`，观察 operation 取消。
  5. 在取消前抢答一次，观察哪个操作赢。
- **期望**：
  - `FUTURE_ONLY` 保留 OPEN interaction，用户仍可回答，不卡死。
  - `ALL_ACTIVE` 原子关闭 interaction、终止 Execution；回答被拒绝。
  - WAITING_EXTERNAL 的 operation 被取消/标记，Execution 终态可控。
  - 终态 Execution 不会被回答改回 QUEUED。
  - 抢答赢时 Execution 正常恢复；取消赢时回答无效。

## B-01 DST 边界

- **负责**：待 plan 分配（集成/可控时钟）
- **前置**：使用 `America/New_York`；准备 2026-03-08 春季跳变、2026-11-01 秋季回拨。
- **步骤**：
  1. 创建每天本地 `02:30` 的任务，观察 2026-03-08。
  2. 创建每天本地 `01:30` 的任务，观察 2026-11-01。
  3. 查看 Fire 列表与“调度事件”/DST 事件。
  4. 再创建一个每天本地 09:00 的任务做对照。
- **期望**：
  - 02:30 在春季跳变日不存在：按 `gap=SKIP` 不触发；**不生成 Fire**，只出现 `DST_GAP_SKIPPED` SCHEDULE 事件；不出现 03:30 的意外执行。
  - 01:30 在秋季回拨日出现两次：按 `fold=FIRST` 只生成第一次 Fire；第二次不生成 Fire，出现 `DST_FOLD_SECOND_SKIPPED` 事件。
  - 09:00 对照任务不受影响。
  - 真实 Fire 显示本地时间、UTC、offset；DST 事件显示 local_date/local_time。
  - 不出现“每天 2:30 实际 3:30 跑”或“秋季跑两次”的静默行为。

## B-02 错过区间

- **负责**：待 plan 分配（集成）
- **前置**：停止 Scheduler 超过两个周期，或把 `next_fire_at` 置于过去。
- **步骤**：
  1. 恢复 Scheduler。
  2. 查看 Fire 历史。
  3. 查看消息/结果数量。
- **期望**：
  - 不按每个错过周期刷屏。
  - 一条 Fire 记录跳过区间，`missed_count` 表示**额外**错过的 occurrence 数；`occurrence_count` 按 `1 + missed_count` 累计，不重复计一次。
  - 下一次正常触发从当前时间之后计算。

## B-03 结束条件

- **负责**：待 plan 分配
- **前置**：创建 ONCE、max_occurrences、until 三类任务。
- **步骤**：
  1. 让 ONCE 成功触发。
  2. 让 ONCE 错过触发。
  3. 让周期任务达到次数/截止时间。
- **期望**：
  - ONCE 成功 → `ENDED` + `COMPLETED`。
  - ONCE 错过 → `ENDED` + `MISSED`，不可复活。
  - 达到次数/截止 → `ENDED` + `EXHAUSTED`。
  - 历史保留，列表不再产生新 Fire。

## B-04 配额与最小间隔

- **负责**：待 plan 分配
- **前置**：配置较小的用户配额/最小间隔。
- **步骤**：
  1. 创建小于最小间隔的任务。
  2. 创建超过 Schedule 数量/每日 Fire 配额的任务。
  3. 查看提示。
- **期望**：
  - 明确拒绝并说明配额/最小间隔。
  - 不创建 ACTIVE；不静默截断。
  - 不影响已有任务。

## B-05 QUEUE 并发

- **负责**：待 plan 分配（集成）
- **前置**：创建 `QUEUE` 策略、每 1 分钟触发、执行耗时超过周期的任务。
- **步骤**：
  1. 等待多次触发。
  2. 查看 Execution 顺序与状态。
  3. 查看是否重叠。
- **期望**：
  - 同一 Schedule 不并行执行。
  - 后续 Fire/Execution 排队；前一个结束后按顺序执行。
  - 历史保留排队/执行状态。

## B-06 ALLOW_PARALLEL 上限

- **负责**：待 plan 分配（集成）
- **前置**：创建 `ALLOW_PARALLEL`、`max_concurrency=2` 的任务。
- **步骤**：
  1. 让三个周期同时到期。
  2. 查看运行中的 Execution 数。
  3. 等待释放后再查看。
- **期望**：
  - 同时最多 2 个 Execution 运行。
  - 第三个保持排队，不丢失。
  - 不出现无限并发。

## B-07 多客户拆分

- **负责**：待 plan 分配
- **前置**：无。
- **步骤**：
  1. 说：“A 客户每天 9 点、B 客户每天 10 点、C 客户每天 11 点各发一份周报。”
  2. 查看确认卡。
  3. 确认后查看任务列表。
- **期望**：
  - 系统引导拆成三个独立 Schedule。
  - 不创建一个混合三个客户的 Schedule。
  - 每个 Schedule 只含一个客户/一条规则。
  - 任务列表显示三条，时间分别正确。

## B-08 Agentic deadline / budget

- **负责**：待 plan 分配
- **前置**：创建一个预算很小/目标会多次调用工具的任务。
- **步骤**：
  1. 触发任务。
  2. 查看执行事件/步数/工具调用。
  3. 等待 deadline。
- **期望**：
  - 不超过 `max_steps/max_tool_calls`。
  - 达到 deadline 后以明确错误结束，不无限运行。
  - 任务中心显示失败原因/部分结果（如有）。
  - 不产生未受控的外部副作用。

## B-09 长结果与产物

- **负责**：待 plan 分配
- **前置**：创建会返回长文本/多个产物的任务。
- **步骤**：
  1. 打开任务中心详情。
  2. 查看长文本/产物列表。
  3. 下载/预览产物。
  4. 刷新页面。
- **期望**：
  - 长文本可折叠/滚动，不撑破布局。
  - 产物列表可下载/预览；刷新后仍在。
  - 不把完整长文塞进列表行。
  - 无 Secret/内部路径。

## B-10 历史保留与分页

- **负责**：待 plan 分配
- **前置**：创建高频任务，产生多页 Fire。
- **步骤**：
  1. 打开 Fire 历史，翻页。
  2. 查看最早记录。
  3. 等待/模拟保留清理。
- **期望**：
  - 分页正确，不重复/不丢页。
  - 保留期内记录可查；清理后显示“已按保留策略清理”，不伪造数据。
  - 列表/详情请求有界，不扫全表。

## B-11 无 Conversation 的任务产物下载

- **负责**：待 plan 分配
- **前置**：创建一个会产生产物的任务（无聊天会话/无 Run）；产物通过任务中心查看。
- **步骤**：
  1. 打开任务中心 → Execution 详情。
  2. 查看产物列表（name/media_type/size/checksum/version）。
  3. 点击下载（请求带 version + checksum）。
  4. 人为用错误 version/checksum 再下载。
  5. 用另一个用户/Agent 的会话尝试下载同一 artifact_id。
- **期望**：
  - 无 Conversation 也能按任务 owner+agent 下载成功。
  - DTO 有 checksum/version；下载内容与 checksum 一致。
  - 错误 version/checksum 返回 `ARTIFACT_VERSION_CONFLICT`，不返回最新版冒充。
  - 跨用户/跨 Agent 返回 404/403，不泄露存在性。
  - 下载失败显示明确错误，不伪造成功。

## B-12 排队旧 Execution 更新取消

- **负责**：待 plan 分配（集成）
- **前置**：创建 `QUEUE` 策略任务，已有 1 个运行中、1 个 QUEUED 且 `started_at IS NULL` 的旧 Revision Execution；另有一个已开始但被 reclaim 回 QUEUED 的 Execution。
- **步骤**：
  1. 修改任务目标/参数/时间。
  2. 查看旧 Revision 的 QUEUED Execution 与运行中 Execution。
  3. 查看已开始后回 QUEUED 的 Execution 是否被误取消。
  4. 等待运行中的旧 Execution 完成。
  5. 查看新 Revision 的下一次执行。
- **期望**：
  - 旧 Revision 且 `started_at IS NULL` 的 QUEUED Execution 被取消，不再执行旧业务。
  - 已开始（`started_at` 非空）但回 QUEUED 的 Execution 不被误取消，继续/重试。
  - 运行中的旧 Execution 保留旧 Snapshot，继续完成。
  - 新 Revision 的 Fire 使用新目标/参数。
  - 不出现“改了参数但旧排队任务仍跑旧内容”。
