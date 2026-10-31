---
id: agent-self-evolution-scenarios
description: Agent 自进化（信号、评测、经验资产、检索质量、激活控制）验收场景唯一源
stages:
- design
enforcement: advisory
schema: 1
requirement: agent-self-evolution
scenarios:
- id: S-01
  title: 隐式纠正信号落库且不改变 Run 终态
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Runtime 事件写入 + 真实 PostgreSQL；模型为脚本化 provider
  preconditions:
  - 本地 PostgreSQL 测试库可用且已迁移到 head
  steps:
  - 构造同一用户连续两个 Run，第二个对第一个答案提出否定或重问
  - 查询 runtime.canonical_event
  - 校验两个 Run 的终态
  expected:
  - 产生一条 LEARNING_SIGNAL 事件，payload 含 tenant_id、user_id、source_run_id、signal_type
  - 两个 Run 的终态与未启用信号检测时一致
  - 该事件不进入模型历史（不在 HISTORY_EVENT_TYPES 中）
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [2]
  features: [FEAT-01]
  design_refs:
  - backend design §3.2 / §3.3
- id: S-02
  title: 每次 Run 的记忆注入可归因
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Runtime + 真实 PostgreSQL；注入条目与模型请求逐条比对
  preconditions:
  - 测试用户存在可注入的记忆
  steps:
  - 执行一个会注入记忆的 Run
  - 查询 MEMORY_INJECTED 事件与本次模型请求
  - 比对记忆条目与丢弃计数
  expected:
  - 事件的 memory_key/version 集合与请求中注入的条目完全一致
  - 每条记录含 score 与 decision
  - 未入选条目按 reason 汇总计数，不逐条写入事件
  expected_refs:
  - expected: 1
    steps: [3]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [2]
  features: [FEAT-01]
  design_refs:
  - backend design §3.2 / §3.5
- id: S-03
  title: Golden Set 回放可复现且报告可审计
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Snapshot 重放 + 真实 PostgreSQL；模型为脚本化 provider
  preconditions:
  - Golden Set 中的 Run 与 Snapshot 存在
  steps:
  - 用同一 Golden Set 与 Snapshot 连续回放两次
  - 比对两份报告的指标
  - 检查报告落产物存储
  expected:
  - 两次报告的成功率、纠错轮次、注入精度逐项一致
  - 报告含每个 case 的通过/失败原因
  - 报告以产物形式落库并记录 storage_key
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [1]
  - expected: 3
    steps: [3]
  features: [FEAT-02]
  design_refs:
  - backend design §3.2 / §3.5
- id: S-04
  title: 任务内自检择优只输出通过验证的候选
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend]
  boundary: 真实 verifier 注册与执行链；模型为脚本化 provider
  preconditions:
  - 已注册一个会拒绝首个候选的 verifier
  steps:
  - 执行该任务
  - 检查最终答案、尝试次数与状态
  - 让所有候选都不通过
  expected:
  - 最终答案来自通过 verifier 的候选
  - 尝试次数不超过设置的 verification_max_attempts
  - 全部候选不通过时返回明确失败状态，不输出未验证答案
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-03]
  design_refs:
  - backend design §3.2 / §3.5
- id: S-05
  title: 成功轨迹生成经验候选但不自动生效
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 consolidation job + 真实 PostgreSQL
  preconditions:
  - 同一任务族存在至少最小证据数的成功且无纠正 Run
  steps:
  - 触发该用户的 consolidation job
  - 查询 runtime.user_memory
  - 执行后续新 Run 并检查注入
  expected:
  - 生成 category=PROCEDURE 的记录，activation_state=PROPOSED
  - evidence_json 含来源 run_id 与成功次数
  - 该记录不进入任何 Run 的注入，直到被激活
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-04]
  design_refs:
  - backend design §3.3 / §3.5
- id: S-06
  title: 经验激活受回放评测门禁约束且只影响后续 Run
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实回放评测 + 真实 PostgreSQL + 真实审计
  preconditions:
  - 存在一条 PROPOSED 且回放结果不达标的经验
  - 存在一条 PROPOSED 且回放结果达标的经验
  steps:
  - 对不达标经验请求激活
  - 对达标经验请求激活
  - 观察既有 Run 与后续新 Run 的注入
  expected:
  - 不达标请求返回 EXPERIENCE_EVAL_BELOW_THRESHOLD，状态保持 PROPOSED
  - 达标经验状态变 ACTIVE 并写审计
  - 后续新 Run 可注入该经验，激活前已创建的 Run 不受影响
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-06]
  design_refs:
  - backend design §3.4 / §3.5
- id: S-07
  title: 记忆打分与门控只放行通过阈值的条目
  priority: P0
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 纯函数打分与选择，无 IO
  preconditions:
  - 打分器与选择器可独立调用
  steps:
  - 构造低于阈值的候选、空查询、偏好类记忆
  - 执行选择
  - 重复执行一次并比对
  expected:
  - 低于阈值的候选不进入注入列表
  - 空查询只注入偏好类，且受独立条数与字节限额
  - 同一输入两次选择结果完全一致
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-05]
  design_refs:
  - backend design §3.2 / §3.5
- id: S-08
  title: 激活与停用留审计且停用即回滚
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 PostgreSQL + 真实审计写入
  preconditions:
  - 存在一条可激活的经验
  steps:
  - 激活该经验
  - 停用该经验
  - 比较停用前后新 Run 的注入
  expected:
  - 激活与停用各写一条审计，含操作者、经验 id 与原因
  - 停用后该经验不再注入
  - 停用不删除历史行（is_deleted=false，activation_state=RETIRED）
  expected_refs:
  - expected: 1
    steps: [1, 2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [2]
  features: [FEAT-06]
  design_refs:
  - backend design §3.4 / §3.5
- id: E-01
  title: 学习链路失败降级为无学习且主 Run 不受影响
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 学习链路注入故障，主执行链不注入
  preconditions:
  - 可注入 consolidation 或评测失败
  steps:
  - 让学习链路抛错
  - 执行主 Run
  - 检查日志与指标
  expected:
  - 主 Run 正常到达终态
  - 记录 warning 日志与失败计数指标
  - 不因学习失败回滚已提交的业务事实
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [2]
  features: [FEAT-02]
  design_refs:
  - backend design §3.5
- id: E-02
  title: 用户删除记忆后已激活经验不再注入且不被复活的提议带回
  priority: P1
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 forget 事务 + 真实 PostgreSQL + 真实 consolidation
  preconditions:
  - 存在一条已激活且可被注入的经验
  steps:
  - 用户删除该 memory_key
  - 再次执行 Run
  - 再次触发 consolidation
  expected:
  - 删除后该记忆不再注入
  - forget_epoch 递增，既有提议被撤销
  - 后续 consolidation 不复活该 key
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [1]
  - expected: 3
    steps: [3]
  features: [FEAT-04]
  design_refs:
  - backend design §3.3 / §3.5
- id: B-01
  title: 注入预算与丢弃计数
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 纯选择与装箱逻辑
  preconditions:
  - 候选数量与总字节均超过限额
  steps:
  - 执行选择与装箱
  - 检查结果与丢弃计数
  expected:
  - 结果条数不超过 max_items，字节数不超过 max_bytes
  - 按分数排序贪心选择，未入选计入 dropped 指标
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  features: [FEAT-05]
  design_refs:
  - backend design §3.5
- id: B-02
  title: 同键多版本只注入最新有效版本
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 纯选择逻辑
  preconditions:
  - 同一 memory_key 存在多个历史版本
  steps:
  - 执行选择
  - 检查注入条目
  expected:
  - 只注入最新有效版本
  - 旧版本不占用预算
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  features: [FEAT-05]
  design_refs:
  - backend design §3.5
- id: B-03
  title: 可配置上界规模下选择仍正确且无新增外部 IO
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 纯函数选择与缓存装载；候选规模取 max_entries_per_user 的可配置上界
  preconditions:
  - 可构造达到上界规模的候选集
  steps:
  - 以默认规模与上界规模各执行一次选择
  - 检查结果、预算与外部调用
  - 记录两次执行的耗时
  expected:
  - 两种规模下预算与排序规则一致
  - 选择过程不产生网络调用或数据库查询
  - 耗时数据写入基准记录，作为 NFR-PERF-01 的回填依据
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-05]
  design_refs:
  - backend design §3.2 / §3.5
---
