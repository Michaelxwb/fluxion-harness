# 定时任务必须绑定 Skill，缺少自定义消息任务

- 记录日期：2026-10-03
- 状态：待设计 / 待实现
- 类型：功能缺口
- 涉及模块：Agent Runtime、Agent Worker、Console 定时任务、消息投递
- 来源：用户对话反馈与代码检查

## 用户场景与当前表现

用户在聊天中要求：“帮我创建一个定时任务，每五分钟给我发一个：你好，见到你很高兴”。

当前平台无法直接创建此类任务。用户提供的 Agent 回复显示，使用不存在的 `greeting` Skill 创建时返回 `SKILL_NOT_EFFECTIVE`；Agent 因缺少对应的有效 Skill，要求先创建、上传并授权 Skill 后再配置调度。

上述探测结果来自用户提供的对话，未在本次检查中重新调用线上接口。代码检查确认当前存在 Skill 强制依赖。

## 已确认的实现限制

- `apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py`：`create_schedule` 必填 `skill_key` 与 `schedule`；`_skill` 只接受当前 Run 的有效 Skill，否则返回 `SKILL_NOT_EFFECTIVE`。
- `packages/contracts/src/muad_contracts/tasks.py`：`CreateScheduleRequest.skill_id` 为必填字段。
- `apps/agent-worker/src/muad_agent_worker/infrastructure/models/task.py`：调度与任务记录的 `skill_id` 为非空字段。
- `apps/agent-worker/src/muad_agent_worker/scheduler/service.py`：每次触发重新查找有效 Skill；找不到时记录跳过原因，不创建执行任务。
- `apps/agent-worker/src/muad_agent_worker/worker/executor.py`：现有执行器按冻结的 Skill 制品执行，尚无直接生成固定消息结果的执行分支。
- `apps/agent-worker/src/muad_agent_worker/delivery/messages.py`：现有完成消息带任务完成说明，直接发送原文需要独立的格式处理。

因此这不是单纯放宽 `skill_key` 校验即可解决的问题，需要扩展任务类型、契约、持久化约束及执行路径。

## 建议方案（待设计确认）

新增“发送消息”任务类型，与现有“执行 Skill”并列：

- 执行 Skill：保留有效 Skill 绑定与检查。
- 发送消息：接受用户自定义文本，无需绑定 Skill；触发后直接投递原文，无需调用模型。
- 复用现有 Cron / 一次性调度、暂停与删除、任务记录、投递路由、投递重试及幂等机制。
- Agent 创建工具支持自然语言请求转换为消息调度；Console 列表与详情能明确展示任务类型及消息内容。
- 保留租户隔离、用户身份、投递目标和相应权限检查；消息任务取消 Skill 依赖不等于取消其他权限检查。
- 数据库迁移及按任务类型校验字段，确保历史 Skill 调度行为兼容。

“定时执行任意 Agent 指令”（例如每天总结新闻）属于更大的扩展方向，需要另行设计 Runtime 执行、运行上下文、权限、超时、重试及结果投递；与直接发送固定文本分别评估。

## 初步工作量

| 范围 | 初步估算 |
|---|---|
| 定时发送固定文本，含迁移、接口、调度执行、Console 展示及基本回归测试 | 3–5 个开发日 |
| 定时执行任意 Agent 指令，含 Runtime 接入与相关执行控制 | 约 1–2 周 |

以上为代码初查后的粗估，未经详细设计和任务拆分，不作为工期承诺。

## 验收与回归测试要求

- [ ] 用户无需创建或绑定 Skill，即可在聊天中创建每五分钟发送固定文本的任务。
- [ ] 支持周期调度与一次性调度，沿用时区处理。
- [ ] 到点投递用户指定原文，不添加任务完成包装文案、不调用模型。
- [ ] 列表与详情展示消息任务类型、内容及触发历史，支持暂停、恢复、修改和删除。
- [ ] 租户、用户与投递目标权限正确；无可用投递路由时明确报错。
- [ ] 调度并发、执行重试和投递重试沿用幂等控制，覆盖重复触发与重复投递场景。
- [ ] 历史 Skill 调度及有效性检查不受影响，数据库迁移通过验证。
- [ ] 增加创建校验、调度触发、原文投递、权限、重试及 Console 展示的回归测试。
