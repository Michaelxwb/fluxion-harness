---
id: experience-management-scenarios
description: 经验管理模块验收场景唯一源
stages:
- design
enforcement: advisory
schema: 1
requirement: experience-management
scenarios:
- id: S-01
  title: 用户经验列表按状态与族过滤且跨租户隔离
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console API（真实 ASGI + ADMIN 会话）→ LearningRepository → 真实 PostgreSQL runtime.user_memory
  preconditions:
  - 同租户两个用户各有多条经验，覆盖四种状态与两个族；另一租户有同 key 经验
  steps:
  - 以管理员请求 GET /api/v1/learning/experiences?user_id=<A>
  - 按 state=TRIAL、family=skill:demo 分别过滤
  - 以另一租户账号请求同一 user_id
  expected:
  - 只返回该用户经验，字段含 activation_state/task_signature/latest_report 且 counts 按状态分列
  - state/family 过滤后总数与 items 一致，分页封套 {items,page,page_size,total}
  - 跨租户返回 404（不泄露存在性）
  design_refs:
  - backend design §3.4.1（API-01）
  - backend design §3.6（RULE-10）
  rules:
  - RULE-07
  - RULE-10
  checks: []
  features:
  - FEAT-01
  expected_refs:
  - expected: 1
    steps: &id001
    - 1
    - 2
    - 3
  - expected: 2
    steps: *id001
  - expected: 3
    steps: *id001
- id: S-02
  title: 族视图聚合与用户视图对同一经验逐字段一致
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console API → 真实 PG；同一 fixture 同时查询 API-01 与 API-04
  preconditions:
  - 造两个族（skill:demo、agent:<id>），每族两个用户，经验状态不同
  - 存在 task_family 为 null 的历史 Run 与跨用户同名 key
  steps:
  - 请求族列表与族详情
  - 请求两个用户的经验列表
  - 对同一 (user_id,memory_key,version) 逐字段比对两处返回
  expected:
  - 族计数（run_count/经验状态分布/pass_rate）与 fixture 一致，null family 不计入并给出 unclassified_runs
  - 同一经验在族详情与用户视图的 activation_state/version/latest_report 完全相等
  - 不同用户同名 key 不被合并为一个状态；同一用户同族多个 key 正常分组展示（key 不解析）
  design_refs:
  - backend design §3.4.1（API-03/API-04）
  - backend design §3.6（RULE-01/RULE-11）
  rules:
  - RULE-01
  - RULE-11
  checks: []
  features:
  - FEAT-02
  - FEAT-06
  expected_refs:
  - expected: 1
    steps: &id002
    - 1
    - 2
    - 3
  - expected: 2
    steps: *id002
  - expected: 3
    steps: *id002
- id: S-03
  title: 经验详情完整解释来源、回放、审计与注入
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console API → 真实 PG（user_memory/history/eval_report/canonical_event/config_audit_log）
  preconditions:
  - 一条 ACTIVE 经验，含来源 Run 证据、PASSED 候选报告（带 limits 与逐 case）、TRIAL_START/ACTIVATE 审计
  - 两个 Run 的 MEMORY_INJECTED 含该经验
  steps:
  - 请求经验详情
  - 校验 reports 的 user_id 归属与 limits/case 明细
  - 校验 injections 的 score/channel，以及 audits 的 from/to/operator/reason
  expected:
  - 详情返回 evidence（source_run_ids/success_runs/item_total）、history 版本、reports（用户归属
    + 门槛 + 逐 case）、injections（真实分数与通道）、audits
  - 不展示其他用户的候选报告；历史 NULL 归属报告标 UNKNOWN 且不进入晋升依据
  - 身份区展示 authority；报告 extras/未知字段原样透出，不因枚举扩展丢弃
  design_refs:
  - backend design §3.4.1（API-02）
  - backend design §3.6（RULE-03/RULE-05/RULE-09）
  rules:
  - RULE-03
  - RULE-05
  - RULE-09
  checks: []
  features:
  - FEAT-03
  - FEAT-08
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
- id: S-04
  title: 生命周期操作经状态机与记忆主通道并留审计
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: Console API → Runtime internal API（真实服务身份头）→ MemoryService/LearningActivation/LearningJobRunner
    → 真实 PG；回放用本地 OpenAI 兼容探针
  preconditions:
  - 一条 PROPOSED 经验，Golden Set ≥ min_cases，探针使候选臂达标
  - 学习开启，租户门槛已知
  steps:
  - 提交回放并轮询 job 到终态
  - 晋升到 ACTIVE；重复提交同一 Idempotency-Key
  - 退休并填写原因；对 RETIRED 执行重新沉淀
  - 查询 user_memory_history、memory_command_receipt、config_audit_log
  expected:
  - 回放生成绑定该 user 的候选报告，job 幂等只执行一次
  - 晋升后 version 递增、history 有完整 after、receipt 有记录、审计 actor 为真实账号且含 from/to/reason
  - 退休/重新沉淀按状态机生效；非法转换返回 LEARNING_INVALID_TRANSITION
  design_refs:
  - backend design §3.4.3
  - backend design §3.6（RULE-02/RULE-04/RULE-08/RULE-09）
  rules:
  - RULE-02
  - RULE-03
  - RULE-04
  - RULE-08
  - RULE-09
  - RULE-18
  checks: []
  features:
  - FEAT-04
  - FEAT-07
  - FEAT-08
  expected_refs:
  - expected: 1
    steps: &id003
    - 1
    - 2
    - 3
    - 4
  - expected: 2
    steps: *id003
  - expected: 3
    steps: *id003
- id: S-05
  title: 通用记忆资源不再混合经验且拒绝经验写
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console 用户记忆 API → 真实 PG 只读投影；通用写 API → Runtime internal
  preconditions:
  - 用户同时有 PREFERENCE 记忆与 PROCEDURE 经验
  steps:
  - 请求通用记忆列表与用户详情计数
  - 对经验 key 调用通用新增/启停用接口
  expected:
  - 通用列表与 memory_count 只含事实记忆；经验计数单独返回
  - 通用写 API 对经验类别返回明确拒绝码，状态不变
  design_refs:
  - backend design §3.6（RULE-10）
  rules:
  - RULE-10
  checks: []
  features:
  - FEAT-07
  expected_refs:
  - expected: 1
    steps: &id004
    - 1
    - 2
  - expected: 2
    steps: *id004
- id: S-06
  title: 用户详情经验页签展示状态机并提供解释入口
  priority: P0
  level: component
  exec: auto
  env: none
  components:
  - frontend
  boundary: 组件渲染 + mock service 数据（契约类型）；不访问真实后端
  preconditions:
  - 四种状态与空态/错误态 fixture
  steps:
  - 打开用户详情经验页签
  - 点击一条经验
  - 切换到通用记忆页签
  expected:
  - 列表显示族/状态/版本/回放结论，过滤与分页可用
  - 打开共用经验详情；通用记忆页签对经验类不出现启停用/删除
  - 空态与错误态文案来自 learning.* 词条
  design_refs:
  - frontend design §3.3 / §3.6
  - frontend design §2.4.1（RULE-15）
  rules:
  - RULE-20
  - RULE-22
  - RULE-23
  checks: []
  features:
  - FEAT-01
  - FEAT-03
  expected_refs:
  - expected: 1
    steps: &id005
    - 1
    - 2
    - 3
  - expected: 2
    steps: *id005
  - expected: 3
    steps: *id005
- id: S-07
  title: 族页面与用户视图共用同一经验详情组件
  priority: P0
  level: component
  exec: auto
  env: none
  components:
  - frontend
  boundary: 组件渲染 + 同一 fixture 注入两个入口
  preconditions:
  - 族详情 fixture 含经验条目；用户详情 fixture 含同一经验
  steps:
  - 渲染族页并打开经验详情
  - 渲染用户页并打开同一经验
  - 对比两处详情关键字段
  expected:
  - 两处详情由同一组件渲染，族/状态/回放结论/操作一致
  - 关闭后回到各自入口，列表不出现重复操作列
  design_refs:
  - frontend design §3.3 / §3.4
  - frontend design §2.4.1（RULE-14）
  rules:
  - RULE-20
  - RULE-21
  - RULE-22
  checks: []
  features:
  - FEAT-02
  - FEAT-03
  - FEAT-06
  expected_refs:
  - expected: 1
    steps: &id006
    - 1
    - 2
    - 3
  - expected: 2
    steps: *id006
- id: S-08
  title: 管理员从用户经验到族视图的完整旅程与深链回跳
  priority: P1
  level: E2E
  exec: deferred
  env: browser
  components:
  - frontend
  - backend
  - browser
  boundary: 真实浏览器（preview 构建产物）→ Console API → Runtime internal → 真实 PostgreSQL；仅模型回放可用本地探针
  preconditions:
  - 管理员账号；一条 TRIAL 经验及其来源 Run/注入记录
  - 学习开启；族页面与用户页可访问
  steps:
  - 登录 Console，打开用户详情经验页签并打开该经验
  - 执行退休并填写原因
  - 打开族页面，按族定位同一经验并打开详情
  - 从 Run 详情的注入事件跳到经验详情，再跳回 Run
  - 打开审计页筛选 MEMORY_EXPERIENCE，从对应审计行进入同一经验详情
  expected:
  - 退休后用户页与族页同时显示 RETIRED，审计可见操作者与原因
  - 族详情中的经验身份与用户页完全一致
  - Run ↔ 经验双向深链落在同一对象（user_id+memory_key）且无 404
  - 审计页可按 MEMORY_EXPERIENCE 筛选到本次退休，操作者为真实账号，跳转落在同一经验
  expected_refs:
  - expected: 1
    steps:
    - 2
  - expected: 2
    steps:
    - 3
  - expected: 3
    steps:
    - 4
  - expected: 4
    steps:
    - 5
  why_e2e: 跨页面状态同步、真实路由深链与真实鉴权/DB 组合只有浏览器 E2E 能暴露；component 无法覆盖路由与真实后端
  trigger_paths:
  - apps/console-platform/frontend/src/modules/learning/**
  - apps/console-platform/frontend/src/modules/user-identity/**
  - apps/console-platform/frontend/src/modules/run-observability/**
  - apps/console-platform/backend/src/muad_console_platform/api/learning.py
  - apps/agent-runtime/src/muad_agent_runtime/application/learning_*
  smoke: false
  design_refs:
  - frontend design §3.2 / §3.5
  - frontend design §2.4.1（RULE-17）
  rules:
  - RULE-19
  - RULE-24
  - RULE-25
  checks: []
  features:
  - FEAT-01
  - FEAT-02
  - FEAT-05
  owner: TASK-004
  target_phase: review
  defer_reason: 浏览器 E2E 在需求级 verify-e2e 统一执行；owner 由计划阶段任务拆分确认
- id: S-09
  title: Run 维度「为什么没注入」诊断忠实呈现
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: 真实 Run 的 MEMORY_INJECTED 事件（含 diagnostics）→ Console API-10 → 真实 PG
  preconditions:
  - 一个 Run：学习开启，经验 A ACTIVE 且族命中，经验 B 未 ACTIVE，经验 C 低于阈值，经验 D 被冻结集合排除
  - 另一个历史 Run：事件无 diagnostics 字段
  steps:
  - 请求 Run 学习诊断
  - 校验 skipped 原因与 score/channel
  - 请求历史 Run 的诊断
  expected:
  - 诊断返回 injected 真实分数/通道与 skipped 的逐条原因
  - 历史 Run 返回 recorded=false，不伪造原因
  - 未知诊断原因码原样展示并走兜底文案，不报错
  design_refs:
  - backend design §3.4.1（API-10）
  - backend design §3.6（RULE-05）
  rules:
  - RULE-05
  checks: []
  features:
  - FEAT-05
  - FEAT-08
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
- id: E-01
  title: 学习关闭与上游不可用时的只读降级
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console API + Runtime client（上游 5xx/超时）；真实 PG
  preconditions:
  - learning_enabled=false 的租户；以及 Runtime 不可达注入
  steps:
  - 读取经验列表与详情
  - 提交回放/晋升
  - 在 Runtime 不可达时读取详情
  expected:
  - 读取仍可用并标注学习关闭；写操作 409 并给出原因
  - Runtime 不可达返回错误码，不返回缓存旧状态
  design_refs:
  - backend design §3.6（RULE-12）
  rules:
  - RULE-12
  checks: []
  features:
  - FEAT-04
  - FEAT-08
  expected_refs:
  - expected: 1
    steps: &id007
    - 1
    - 2
    - 3
  - expected: 2
    steps: *id007
- id: E-02
  title: 非法转换、跨用户报告与通用写经验被拒绝
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: Runtime internal API + Console 通用记忆 API → 真实 PG
  preconditions:
  - 用户 U1 经验只有 U2 的 PASSED 报告
  - 一条 ACTIVE 经验；一条 RETIRED 经验
  steps:
  - 用 U2 报告尝试晋升 U1 经验
  - 对 RETIRED 直接 activate；对 ACTIVE 执行 start-trial
  - 通过通用写 API 修改经验内容
  - 对 USER_EXPLICIT 与 AGENT_INFERRED 两条经验分别执行无 PASSED 晋升
  expected:
  - 借用报告被拒（LEARNING_REPLAY_GATE_FAILED / 状态不变）
  - 非法转换返回 LEARNING_INVALID_TRANSITION
  - 通用写 API 拒绝经验类别
  - USER_EXPLICIT 可按权威路径激活并留审计；AGENT_INFERRED 仍被 PASSED 门槛拒绝
  design_refs:
  - backend design §3.6（RULE-02/RULE-03/RULE-10）
  rules:
  - RULE-02
  - RULE-03
  - RULE-10
  checks: []
  features:
  - FEAT-07
  - FEAT-08
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 2
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 3
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 4
    steps:
    - 1
    - 2
    - 3
    - 4
- id: E-03
  title: 状态变更失败整体回滚且不产生无审计变更
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: Runtime MemoryService 事务 → 真实 PG；注入审计写入失败
  preconditions:
  - 一条 PROPOSED 经验；审计写入被注入失败
  steps:
  - 执行晋升（回放已 PASSED）
  - 查询 user_memory/version/history/receipt/审计
  expected:
  - 整个事务回滚：状态、版本、history、receipt 均无变化
  - 返回明确错误码；不存在「状态已变但审计缺失」
  design_refs:
  - backend design §3.6（RULE-04）
  rules:
  - RULE-04
  checks: []
  features:
  - FEAT-04
  - FEAT-08
  expected_refs:
  - expected: 1
    steps: &id008
    - 1
    - 2
  - expected: 2
    steps: *id008
- id: E-04
  title: 回放任务幂等、崩溃回收与 INCOMPLETE 不改状态
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: LearningJobRunner → 真实 PG；探针可控延迟/失败
  preconditions:
  - 同一 Idempotency-Key 两次提交；一个 RUNNING job 的 lease 过期；样本不足的经验
  steps:
  - 重复提交回放
  - 模拟 worker 崩溃（lease 过期）后重跑循环
  - 对样本不足经验回放
  expected:
  - 重复提交只产生一个 job/一次执行
  - 过期 lease 被回收并重试，超过 attempts 后 FAILED 且错误可见
  - INCOMPLETE 报告写入但不改变 activation_state
  design_refs:
  - backend design §3.6（RULE-08）
  rules:
  - RULE-08
  checks: []
  features:
  - FEAT-04
  expected_refs:
  - expected: 1
    steps: &id009
    - 1
    - 2
    - 3
  - expected: 2
    steps: *id009
  - expected: 3
    steps: *id009
- id: B-01
  title: 任务族判定纯函数边界
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - backend
  - runtime
  boundary: 纯函数 derive_task_family / first_successful_skill；无 IO
  preconditions: []
  steps:
  - 构造多 skill、失败 skill、无 skill、批量 skill 的事件序列
  expected:
  - 首个成功 skill 决定族；失败调用不切换；无 skill 回退 agent:<id>
  - 批量与单条判定一致（批量只影响元数据，不影响族）
  design_refs:
  - backend design §3.6（RULE-06）
  rules:
  - RULE-06
  checks: []
  features:
  - FEAT-08
  expected_refs:
  - expected: 1
    steps: &id010
    - 1
  - expected: 2
    steps: *id010
- id: B-02
  title: 族聚合口径边界（null family、跨用户同名 key、异常前缀）
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console 聚合 SQL → 真实 PG
  preconditions:
  - null family Run；两用户同族同 key；family 带未知前缀
  steps:
  - 请求族列表与族详情
  expected:
  - null family 归「未归类」且不计入族统计
  - 同族同名 key 按用户分组；未知前缀标 unknown，不报错
  design_refs:
  - backend design §3.6（RULE-11）
  rules:
  - RULE-11
  checks: []
  features:
  - FEAT-02
  - FEAT-06
  expected_refs:
  - expected: 1
    steps: &id011
    - 1
  - expected: 2
    steps: *id011
- id: S-10
  title: 审计页经验资源可见、可筛选且可深链
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - postgres
  boundary: Console 审计列表/详情 API → 真实 PostgreSQL 四表 UNION 投影与 config_audit_log
  preconditions:
  - 存在 MEMORY_EXPERIENCE 的 TRIAL_START/TRIAL_PROMOTE/RETIRE 审计行（含真实 actor 与历史合成 actor）
  - 存在其他 MODEL/TOOL 审计行淹没列表
  steps:
  - 按 resource_type=MEMORY_EXPERIENCE 请求审计列表
  - 按 action=RETIRE 过滤并打开详情
  - 校验详情中的 target/after_json 可推出 user_id 与 experience_key
  expected:
  - 列表返回全部经验审计行，target 展示 experience_key/user_id；历史行标归属未知
  - 动作/资源词条有中英标签；真实 actor 解析出账号名，合成 actor 显示系统/自动
  - 详情含 user_id/key，可构造 /learning?experience=<user_id>:<key> 深链
  features:
  - FEAT-09
  design_refs:
  - backend design §3.4.3（审计投影）
  - frontend design §3.2.1
  rules:
  - RULE-19
  - RULE-25
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
- id: S-11
  title: 1000 用户负载下管理查询与回放不回归用户侧
  priority: P1
  level: capacity
  exec: deferred
  owner: TASK-004
  target_phase: release
  defer_reason: 需要 1000 用户容量档与模型额度证据，在容量环境执行；本地只跑逻辑与索引断言
  env: local-api
  components:
  - backend
  - console
  - runtime
  - postgres
  - capacity
  boundary: 1000 用户 × 8 轮真实负载（容量 profile）→ Runtime/PG；同时打开族页/经验详情并触发一次回放
  preconditions:
  - 1000 用户容量 profile 与受控模型探针
  - 族数与经验规模符合 NFR-PERF-01 假设
  steps:
  - 启动 1000 用户负载并进入稳态
  - 并发执行族列表/经验详情查询
  - 提交一次回放并观察 job 与用户侧指标
  - 采集 admission/first-delta、管理接口 p95、PG 连接与 WAL 增量、回放模型调用数
  expected:
  - 用户侧 admission/first-delta 与无管理操作基线无显著回归
  - 管理接口 p95 ≤ 500ms；无 pool timeout
  - 回放只在容量租约内调用模型，不超并发/额度；族统计不随 Run 历史线性变慢
  features:
  - FEAT-02
  - FEAT-04
  design_refs:
  - backend design §3.5（性能）
  - backend design §3.6（RULE-11/RULE-18）
  rules:
  - RULE-11
  - RULE-18
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 2
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 3
    steps:
    - 1
    - 2
    - 3
    - 4
- id: S-12
  title: 从 Run 一键止损并按族暂停，只影响后续新 Run
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - console
  - runtime
  - postgres
  boundary: Console API → Runtime internal（真实服务身份）→ MemoryService/候选生成/注入冻结 → 真实 PG
  preconditions:
  - 一个 Run 注入了两条 ACTIVE 经验
  - 同族还有另一个用户的 ACTIVE 经验
  - 已有在飞 Run 冻结集合
  steps:
  - 从 Run 详情停用本次注入的两条经验
  - 暂停该族，再发起新 Run 与旧 Run 的后续模型请求
  - 恢复族并再次发起新 Run
  expected:
  - 两条经验按 (user,key) 变 RETIRED 并写审计；当前 Run 回答不变
  - 暂停后新 Run 不再注入该族经验、候选生成跳过该族；在飞 Run 的冻结集合不变
  - 恢复后新 Run 可正常注入未退休经验；已退休不复活；全程无数据删除
  features:
  - FEAT-10
  design_refs:
  - backend design §3.4.3（Run 止损/族级暂停）
  - frontend design §3.2.2
  rules:
  - RULE-30
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
- id: S-13
  title: 经验内容修订生成新版本且仍需回放晋升
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: Console API → Runtime MemoryService 事务 → 真实 PG（版本/history/receipt/审计）
  preconditions:
  - 一条 ACTIVE 经验，当前版本 v1
  - 该用户没有新的 PASSED 报告
  steps:
  - 带 expected_version 修订内容
  - 不带 expected_version 或旧版本再提交
  - 尝试直接晋升修订后的版本
  - 完成回放 PASSED 后晋升
  expected:
  - 修订生成 v2、状态 PROPOSED、history/回执/审计齐全；旧版本与注入历史保留
  - 版本冲突返回 409，不覆盖
  - 未 PASSED 晋升被拒（LEARNING_REPLAY_GATE_FAILED）
  - PASSED 后晋升到 ACTIVE，审计记录 operator/reason
  features:
  - FEAT-11
  design_refs:
  - backend design §3.4.3（内容修订）
  - frontend design §3.2.2
  rules:
  - RULE-31
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 2
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 3
    steps:
    - 1
    - 2
    - 3
    - 4
  - expected: 4
    steps:
    - 1
    - 2
    - 3
    - 4
- id: S-14
  title: 差回答反馈幂等进入学习信号并触发复核
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: Console API → Runtime internal → LEARNING_SIGNAL 事件 + 复核 job → 真实 PG
  preconditions:
  - 一个 Run 注入了 ACTIVE 经验
  - 同 Run 重复提交两次
  steps:
  - 标记差回答并填写原因
  - 用同一幂等键重试
  - 等待复核回放完成
  expected:
  - 只写一条 LEARNING_SIGNAL（source_run_id/actor/reason），Run 退出未来 Golden Set
  - 重复提交返回首次结果，不重复建 job
  - 复核 job 走容量边界；回放回归才退休，否则状态不变并记录结论
  features:
  - FEAT-12
  design_refs:
  - backend design §3.4.3（差回答反馈）
  - frontend design §3.2.2
  rules:
  - RULE-32
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
- id: S-15
  title: 止损/修订/反馈入口的组件态与错误提示
  priority: P1
  level: component
  exec: auto
  env: none
  components:
  - frontend
  boundary: 组件渲染 + mock service；不访问真实后端
  preconditions:
  - 管理员与非管理员视图；修订冲突/反馈重复/暂停中的 fixture
  steps:
  - 渲染 Run 详情与经验详情的新入口
  - 触发确认框与错误分支
  - 切换权限
  expected:
  - 管理员可见按钮并显示影响范围说明；非管理员不渲染
  - 409/重复提交/暂停态有明确提示且保留输入
  - 操作成功后列表与详情即时刷新
  features:
  - FEAT-10
  - FEAT-11
  - FEAT-12
  design_refs:
  - frontend design §3.2.2
  - frontend design §2.4.1（RULE-26/27/28）
  rules:
  - RULE-26
  - RULE-27
  - RULE-28
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
- id: E-05
  title: 止损/暂停/修订/反馈的权限、幂等与跨租户边界
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - postgres
  boundary: Console API + Runtime internal → 真实 PG
  preconditions:
  - 另一租户的同名 family/key；非管理员账号；重复请求
  steps:
  - 跨租户调用四个接口
  - 非管理员调用
  - 重复提交暂停/修订/反馈
  expected:
  - 跨租户 404/403；非管理员 403
  - 重复请求幂等返回首次结果，不产生第二次状态变更
  - 失败不产生部分写入（暂停/修订/反馈各自原子）
  features:
  - FEAT-10
  - FEAT-11
  - FEAT-12
  design_refs:
  - backend design §3.6（RULE-30/31/32）
  rules:
  - RULE-30
  - RULE-31
  - RULE-32
  checks: []
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
---
