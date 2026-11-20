---
id: scheduled-skill-invocation-scenarios
description: Skill 调度与对话调用可调用性人工验收场景（Dispatch Preflight 方向）
---

# Skill 调度与对话调用可调用性 验收场景

> 本文件是设计、计划与人工验收共同引用的场景清单。场景不生成集中验收清单、不登记执行结果，也不阻断 `task finish` 或 `task archive`。`cf-task:plan` 只把场景 ID 写入 Task 的 `Scenarios` 字段。
>
> 对话入口的验收一律使用业务语言（例如「给客户 22921947 每天 9 点出一份日报，站内看就行」），不得把 `argv`、`input`、`schema` 等实现词写进用户话术；agent 必须从 `load_skill.manifest` 的 `invocation_contract` 和 SKILL.md 自行映射参数，缺必填值时只问一个业务问题。

```yaml
schema: 1
requirement: scheduled-skill-invocation
scenarios:
  - id: S-01
    title: 自然语言创建 SKILL_PLAN，预检通过后出卡片并确认
    level: integration
    boundary: 真实 Worker API、真实 PostgreSQL、契约 fixture
    preconditions:
      - Skill 声明 invocation=argv 与 schema
      - draft 只提供业务值与 argv，不执行真实业务 Skill
    steps:
      - POST /internal/schedule-drafts，提交 SKILL_PLAN、cron、Asia/Shanghai
      - 检查 preview.dispatch_check 与返回的 draft_hash
      - POST /internal/schedule-drafts/{id}/confirm
    expected:
      - Draft 创建成功，preview.dispatch_check.status=PASSED
      - 不产生 TaskExecution/Fire/Revision/message_delivery
      - Confirm 后创建 Schedule + Revision，status=ACTIVE
      - next_fire_at 为下一个计划槽
    exec: manual
    owner: TASK-003
  - id: S-02
    title: 修改执行参数后 Confirm 重跑预检
    level: integration
    boundary: 真实 UPDATE Draft、config_version、预检门禁
    preconditions:
      - 已存在 ACTIVE 的 SKILL_PLAN Schedule
      - 当前 config_version 已知
    steps:
      - POST /internal/schedule-drafts，kind=UPDATE，修改合法 argv
      - POST /confirm
    expected:
      - Draft 绑定 base_config_version
      - Confirm 用当前依赖与下一次触发重跑预检
      - 成功后生成新 Revision，config_version 递增
    exec: manual
    owner: TASK-004
  - id: S-03
    title: 正式 Fire 使用确认后的 Revision 执行
    level: integration
    boundary: 真实 Scheduler/Fire/Execution
    preconditions:
      - 已确认的 ACTIVE Schedule，next_fire_at 已到期
    steps:
      - 触发 scheduler run_once
      - 查询 Fire/Execution 与任务中心
    expected:
      - Fire admission 用当前 artifact 重新校验参数并通过
      - 同一 (schedule_id, scheduled_at) 只有一条 Fire
      - Execution 使用确认时的 Revision 与 Fire 冻结的 Snapshot
    exec: manual
    owner: TASK-005
  - id: S-04
    title: Authoring 优先 SKILL_PLAN，不静默降级 AGENTIC
    level: contract
    boundary: 真实 agent-runtime authoring compiler
    preconditions:
      - 用户意图可由已知 Skill + 固定参数表达
    steps:
      - 调用 schedule authoring tool/compiler
      - 检查返回的 plan payload 与错误（若有）
    expected:
      - 生成 SKILL_PLAN，而不是 AGENT_TURN
      - invocation=argv 时生成 argv；invocation=stdin 时生成 input
      - 缺必填业务值返回一个业务问题，不改模式
      - 返回预检提示，不直接创建 Schedule
    exec: manual
    owner: TASK-006
  - id: S-05
    title: 参数错误回 agent 修复，不把用户卷入
    level: integration
    boundary: Draft 预检 + agent 修复循环
    preconditions:
      - Skill 声明 invocation=argv，required 包含 company-id
      - 首次提交只给 input，argv 为空
    steps:
      - POST /internal/schedule-drafts
      - 读取 422 的 data.field/reason/repair/allowed_values
      - agent 改成 argv 后重新提交
    expected:
      - 第一次返回 SCHEDULE_ARGV_INVALID，field=payload.argv，reason=MISSING_REQUIRED
      - 不落 Draft，不产生 Execution/Fire/投递
      - agent 不需要询问用户 --company-id 的含义
      - 第二次提交通过预检
    exec: manual
    owner: TASK-003
  - id: S-06
    title: 预检与 Confirm 不执行 Skill、不投递
    level: integration
    boundary: 真实 PostgreSQL、Worker API
    preconditions:
      - 契约 fixture Skill 可用
    steps:
      - 创建 Draft
      - Confirm
      - 对比创建前后 task_execution/fire/task_schedule_revision/message_delivery 计数
    expected:
      - 只有 Confirm 成功后才出现 Schedule/Revision
      - 预检阶段没有 TaskExecution/Fire/message_delivery
      - 没有 Skill 子进程启动
    exec: manual
    owner: TASK-003
  - id: S-07
    title: 对话中直接调用 Skill，契约校验通过后执行
    level: integration
    boundary: 真实对话工具调用、真实 Skill 子进程
    preconditions:
      - Skill 声明 invocation=argv 与 schema
    steps:
      - 在对话中先调用 load_skill，检查 manifest
      - 让 agent 调用 execute(target=skill, skill_key, argv)
      - 检查工具结果与 Skill 输出
    expected:
      - load_skill.manifest 返回 entrypoint + invocation_contract
      - 参数通过 contract 校验
      - Skill 正常执行并返回结果
      - 不需要创建 Draft/Schedule
    exec: manual
    owner: TASK-008
  - id: E-01
    title: argv 含 --help / 未知参数时 Draft 拒绝
    level: contract
    boundary: invocation_contract.schema 与 argv 解析
    preconditions:
      - Skill 声明 invocation=argv
    steps:
      - POST /internal/schedule-drafts，argv 含 --help 或未知参数
    expected:
      - 422 SCHEDULE_ARGV_INVALID
      - field=payload.argv，reason=UNKNOWN_OPTION
      - 不落 Draft
    exec: manual
    owner: TASK-002
  - id: E-02
    title: 缺少必填参数时 Draft 拒绝并给出 allowed_values
    level: contract
    boundary: 契约 required
    preconditions:
      - Skill 的 argv schema required 包含 company-id
    steps:
      - POST /internal/schedule-drafts，argv 只给 --date
    expected:
      - 422 SCHEDULE_ARGV_INVALID，reason=MISSING_REQUIRED
      - data.required 包含 company-id
      - data.repair=PROVIDE_SKILL_ARGV
    exec: manual
    owner: TASK-002
  - id: E-03
    title: Skill 缺少 invocation_contract
    level: contract
    boundary: Skill artifact frontmatter
    preconditions:
      - 选择没有 invocation_contract 的 Skill
    steps:
      - POST /internal/schedule-drafts，提交 SKILL_PLAN
    expected:
      - 422 SCHEDULE_INVOCATION_CONTRACT_MISSING
      - 提示为该 Skill 补 contract 或重新导入可调度版本
      - 不落 Draft
    exec: manual
    owner: TASK-001
  - id: E-04
    title: 通道错配（invocation=argv 但只给 input）
    level: contract
    boundary: invocation 与 payload 通道
    preconditions:
      - Skill 声明 invocation=argv
    steps:
      - POST /internal/schedule-drafts，input 非空、argv 为空
    expected:
      - 422 SCHEDULE_ARGV_INVALID，reason=CHANNEL_MISMATCH
      - 不落 Draft
      - 修复提示要求提供 argv
    exec: manual
    owner: TASK-002
  - id: E-05
    title: 模板渲染后值非法（日期越界）
    level: contract
    boundary: 两段式校验
    preconditions:
      - Skill schema date: {type: string, format: date}
    steps:
      - 创建 Draft，argv 使用 --date {{previous_day}}
      - 再创建 Draft，使用一个渲染后非法的固定日期
    expected:
      - 模板形式在创建时通过结构校验
      - 渲染后非法值在预检阶段返回 SCHEDULE_ARGV_INVALID / INVALID_FORMAT
      - 不落第二个 Draft
    exec: manual
    owner: TASK-002
  - id: E-06
    title: SKILL:<uuid>:* 被拒绝并返回 allowed_values
    level: contract
    boundary: AgentTurn tools_allow / 工具名校验
    preconditions:
      - Skill 可用，运行时注册 execute（execute_skill 为兼容别名）
    steps:
      - 提交 AGENT_TURN，tools_allow=["SKILL:<uuid>:*"]
    expected:
      - 422 SCHEDULE_TOOL_WILDCARD_UNSUPPORTED
      - data.admission_code=WILDCARD_NOT_SUPPORTED
      - data.allowed_values 包含具体工具条目（如 SKILL:<uuid>:execute）
      - 不落 Draft
    exec: manual
    owner: TASK-002
  - id: E-07
    title: Confirm 时依赖撤销/版本变化导致预检失败
    level: integration
    boundary: Confirm 重跑预检
    preconditions:
      - Draft 已通过创建时预检
      - 随后撤销 Agent 对 Skill 的授权或发布不兼容的新 artifact
    steps:
      - POST /confirm
    expected:
      - 返回 SCHEDULE_DEPENDENCY_MISSING / SCHEDULE_GRANT_DENIED / SCHEDULE_ARGV_INVALID
      - 不创建 Schedule/Revision
      - Draft 保持 PENDING，可修正后重试
    exec: manual
    owner: TASK-004
  - id: E-08
    title: Fire 时 Skill 版本变化导致参数不兼容
    level: integration
    boundary: Fire admission 重校验
    preconditions:
      - Schedule 已确认，参数满足 A 版本契约
      - 发布 B 版本，契约不再接受该参数
    steps:
      - 触发一次 Fire
    expected:
      - Fire status=FAILED_ADMISSION，error_code=SKILL_ARGS_INVALID
      - 不建 Execution
      - CRON 进入 NEEDS_ATTENTION，ONCE 进入 ENDED
    exec: manual
    owner: TASK-005
  - id: E-09
    title: REMINDER 不参与 Skill 预检
    level: contract
    boundary: payload 类型分支
    preconditions:
      - Agent 没有可用模型或 Skill 契约
    steps:
      - 创建 REMINDER Draft
      - POST /confirm
    expected:
      - 创建/确认不因 Skill contract 被阻断
      - 只校验 recurrence/delivery
    exec: manual
    owner: TASK-004
  - id: E-10
    title: 对话调用通道错配时进程不启动
    level: integration
    boundary: SkillToolSet.execute_skill 参数守卫
    preconditions:
      - Skill 声明 invocation=argv
    steps:
      - 在对话中调用 execute(target=skill)，只给 input，argv 为空
    expected:
      - 返回 `SKILL_INVOCATION_INVALID`，`error.detail` 包含 field/reason/repair/allowed_values
      - "retryable=true，模型有下一轮机会"
      - 不启动 Skill 子进程
      - 模型修正为 argv 后第二次调用成功
    exec: manual
    owner: TASK-008
  - id: E-11
    title: 辅助脚本入口仅调试开关可用，且不套用主入口契约
    level: integration
    boundary: execute(target=skill, script=...) 默认入口判定
    preconditions:
      - Skill 主入口声明 invocation=argv，required 包含 company-id
      - Skill 包内有 no-arg 的 scripts/check_status.py
      - 生产默认关闭 enable_run_skill_script
    steps:
      - 在对话中调用 execute(target=skill, script=scripts/check_status.py)
      - 打开 enable_run_skill_script 后重复一次（由集成测试覆盖）
    expected:
      - 开关关闭时返回 FORBIDDEN（skill script debug execution is unavailable），不启动脚本
      - 开关打开时辅助脚本不套用主入口契约，正常执行
      - 主入口 execute(target=skill) 缺少 company-id 仍被拒绝
      - run_skill_script 只是兼容别名，不出现在 model_tools 中
    exec: manual
    owner: TASK-008
  - id: B-01
    title: 裸 tools_allow=["*"] 仍合法
    level: contract
    boundary: AgentTurn 工具面
    preconditions:
      - AGENT_TURN Draft
    steps:
      - 提交 tools_allow=["*"]
    expected:
      - 表示跟随 owner，不触发通配拒绝
      - 预检通过（依赖可用时）
    exec: manual
    owner: TASK-002
  - id: B-02
    title: 存量 ACTIVE Schedule 补契约后的 Fire 行为
    level: integration
    boundary: 存量 Schedule 兼容
    preconditions:
      - 存在 scheduled-task-v2 已确认的 ACTIVE Schedule
      - 对应 Skill 已补 contract
    steps:
      - 触发一次 Fire
    expected:
      - 参数满足新契约则正常执行
      - 不满足则 FAILED_ADMISSION + NEEDS_ATTENTION，不静默执行
    exec: manual
    owner: TASK-005
  - id: B-03
    title: 并发 Confirm 只成功一次
    level: integration
    boundary: Draft 行锁/幂等
    preconditions:
      - 两个请求同时 confirm 同一 Draft
    steps:
      - 并发 POST /confirm
    expected:
      - 只有一个成功创建/更新
      - 另一个返回幂等重放或冲突，不产生第二个 Schedule
    exec: manual
    owner: TASK-004
  - id: B-04
    title: 契约真实导入 round-trip
    level: integration
    boundary: 真实 zip 导入 → frontmatter_json → resolve → preflight
    preconditions:
      - 构造带 invocation_contract 的真实 Skill 包
    steps:
      - 通过 Console 导入
      - resolve 读取 ResolvedSkill.frontmatter
      - 用该 Skill 创建 Draft
    expected:
      - frontmatter_json.invocation_contract 与源包一致
      - preflight 能读到同一份契约
      - 修改契约必须发新 artifact 版本
    exec: manual
    owner: TASK-001
  - id: B-05
    title: 对话调用无契约 Skill 保持现有行为
    level: integration
    boundary: SkillToolSet
    preconditions:
      - Skill 没有 invocation_contract
    steps:
      - 在对话中调用 execute(target=skill)
    expected:
      - 不因缺少契约被阻断
      - 记录 contract_missing 观测
      - 定时任务新 Draft 仍拒绝
    exec: manual
    owner: TASK-008
  - id: B-06
    title: 零参数 Skill 合法
    level: contract
    boundary: schema.required 决定能否为空
    preconditions:
      - Skill 声明 invocation=argv 或 stdin，schema 没有 required 字段
    steps:
      - 提交 argv=[] 或 input={}
    expected:
      - 声明通道为空时通过 schema 校验
      - 不因通道为空被拒绝
      - 另一条通道仍必须为空
    exec: manual
    owner: TASK-002
  - id: B-07
    title: 布尔参数是存在型开关
    level: contract
    boundary: argv 解析 / CLI 语义
    preconditions:
      - Schema 声明 no-wecom-push: { type: boolean }
    steps:
      - 提交 argv 含 --no-wecom-push
      - 提交 argv 含 --no-wecom-push=true
    expected:
      - 裸 flag 通过并原样传给脚本
      - =true / =false 被拒绝（reason=BOOLEAN_MUST_BE_FLAG）
      - 预检通过的命令行与脚本实际接受的命令行一致
    exec: manual
    owner: TASK-002
```
