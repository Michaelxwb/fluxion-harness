---
id: agent-self-evolution-scenarios
description: Agent 自进化（任务族与 Golden Set、信号、评测、经验资产、检索质量、激活控制）验收场景唯一源
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
  boundary: 真实 consolidation job + 真实 PostgreSQL；模型为脚本化 provider
  preconditions:
  - 本地 PostgreSQL 测试库可用且已迁移到 head
  - 同一用户存在连续两个 Run，第二个对第一个答案提出否定或重问
  steps:
  - 触发该用户的 consolidation job
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
  - design §3.2 / §3.5
- id: S-02
  title: 每次模型请求的注入可归因且按选择结果去重
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Runtime + 真实 PostgreSQL；注入条目与各次模型请求逐条比对
  preconditions:
  - 测试用户存在可注入的记忆；单次 Run 内发生多轮模型请求
  steps:
  - 执行一个会注入记忆并且进行多轮模型请求的 Run
  - 查询 MEMORY_INJECTED 事件与各次模型请求
  - 比对注入条目的 key/version、score 与丢弃计数
  expected:
  - 每次请求的 memory_key/version 集合与请求中注入的条目完全一致
  - 每条记录含 score 与 decision，未入选条目按 reason 汇总计数
  - 连续请求的选择未变化时以 selection_hash 引用而不重复写行，事件行数少于模型请求数
  expected_refs:
  - expected: 1
    steps: [3]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [2]
  features: [FEAT-01]
  design_refs:
  - design §3.2 / §3.5
- id: S-03
  title: 回放可复现且报告不可变
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Snapshot 重放 + 真实 PostgreSQL；模型为脚本化 provider；报告带内容哈希
  preconditions:
  - Golden Set 中的 Run 与 Snapshot 存在
  steps:
  - 用同一 Golden Set 与 Snapshot 连续回放两次
  - 比对两次报告的指标与内容哈希
  - 检查报告是否以不可变行落库
  expected:
  - 脚本化 provider 下两次报告的指标与内容哈希逐项一致
  - 报告含每个 case 的通过/失败原因
  - 报告写入 eval_report 且重复写入产生新版本而不是覆盖
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [1]
  - expected: 3
    steps: [3]
  features: [FEAT-02]
  design_refs:
  - design §3.2 / §3.3
- id: S-04
  title: 任务内自检择优只输出通过验证的候选
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend]
  boundary: 真实 verifier 注册表与执行链；模型为脚本化 provider
  preconditions:
  - 通过平台 verifier 注册表注册一个会拒绝首个候选的 verifier
  steps:
  - 执行该任务
  - 检查最终答案、尝试次数与状态
  - 让所有候选都不通过
  expected:
  - 最终答案来自通过 verifier 的候选
  - 尝试次数不超过设置的 verification_max_attempts
  - 全部候选不通过时返回 VERIFICATION_EXHAUSTED，不输出未验证答案
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-03]
  design_refs:
  - design §3.4 / §3.5
- id: S-05
  title: 成功轨迹生成经验候选但不自动生效
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实跨 Run 聚合 job + 真实 PostgreSQL
  preconditions:
  - 同一任务族存在至少最小证据数的成功且无纠正 Run
  steps:
  - 触发该用户的任务族聚合 job
  - 查询 runtime.user_memory
  - 执行后续新 Run 并检查注入
  expected:
  - 生成 category 为 PROCEDURE 或 FAILURE_PATTERN 的记录，activation_state=PROPOSED
  - evidence_json 含来源 run_id、成功次数与任务签名
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
  - design §3.2 / §3.3
- id: S-06
  title: 经验激活受回放门禁约束且只影响后续 Run
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实回放门禁 + 真实 PostgreSQL + 真实审计
  preconditions:
  - 存在一条回放不达标的 PROPOSED 经验与一条回放达标的 PROPOSED 经验
  - 租户设置 inject_inferred=false
  steps:
  - 对不达标经验请求激活
  - 对达标经验请求激活
  - 观察激活前已创建的 Run 与后续新 Run 的注入
  expected:
  - 不达标请求返回 EXPERIENCE_EVAL_BELOW_THRESHOLD，状态保持 PROPOSED
  - 达标经验状态变 ACTIVE 并写审计
  - 激活前已创建的 Run 不受影响；后续新 Run 注入该经验，且 inject_inferred=false 不影响 ACTIVE 经验注入
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-06]
  design_refs:
  - design §3.4 / §3.5
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
  - design §3.2 / §3.5
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
  - design §3.4 / §3.5
- id: S-09
  title: Golden Set 自动抽取、脱敏与任务族归属
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Run 与事件数据 + 真实 PostgreSQL；抽取在 worker 内离线执行
  preconditions:
  - 存在成功、无负向信号、未取消、带工具调用的 Run
  - 存在失败、取消与带负向信号的 Run
  steps:
  - 触发 Golden Set 抽取
  - 查询 runtime.eval_case
  - 检查任务族归属、脱敏、去重、上限与时间切分
  expected:
  - 用例按执行的 skill 推导任务族，无 skill 的 Run 归入 agent 族
  - 失败、取消或带负向信号的 Run 不入选，canary 文本不落库
  - 每族用例数量受上限约束并保留时间切分，抽取不改变任何 Run 状态
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1, 3]
  features: [FEAT-07]
  design_refs:
  - design §3.2 / §3.3
- id: S-10
  title: 两臂回放达标才允许激活且基线臂可复用
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实两臂回放（冻结 Snapshot + 真实 PostgreSQL）；模型为脚本化 provider
  preconditions:
  - 同一任务族存在可比较的 Golden Set 与一条 PROPOSED 经验
  steps:
  - 分别以不带经验（基线臂）与带经验（候选臂）回放同一 Golden Set
  - 比较两臂指标并读取门禁判定
  - 重复请求同一经验的门禁并检查基线臂复用
  expected:
  - 候选臂相对基线臂的提升达到 eval_min_improvement 时放行，否则拒绝
  - 基线臂结果按用例与配置哈希复用，重复门禁不重跑基线臂
  - 判定依据写入报告的 metrics 与 decision 字段，可审计
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [2]
  features: [FEAT-02]
  design_refs:
  - design §3.2 / §3.5
- id: S-11
  title: 检索质量校准集上阈值门控不劣于现状
  priority: P0
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 纯函数打分与选择；标注集（应注入/不应注入）+ 固定语料
  preconditions:
  - 标注集覆盖同义改写、字面重叠但无关、空查询与偏好类
  steps:
  - 在标注集上运行阈值门控
  - 比较应注入召回与注入噪音量
  - 重复运行确认确定性
  expected:
  - 标注集中的应注入条目全部注入，不应注入条目全部被过滤
  - 注入条目数相对“有重叠即注入”基线下降，且不丢标注集中的应注入条目
  - 同一标注集两次选择结果一致
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-05]
  design_refs:
  - design §3.5
- id: S-12
  title: 回放达标自动进入 TRIAL 并按生产信号自动晋升或退休
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实状态机 + 真实 PostgreSQL + 真实审计
  preconditions:
  - 存在一条通过回放门禁的 PROPOSED 经验，且其类别在 experience_auto_activate 白名单内
  steps:
  - 让该经验通过回放门禁并观察状态
  - 执行 TRIAL 期 Run 并产生信号
  - 分别模拟 TRIAL 期信号不劣化与劣化，观察自动晋升与自动退休
  expected:
  - 达标后状态自动变为 TRIAL 并写审计
  - TRIAL 期信号不劣化时自动晋升 ACTIVE，劣化时自动 RETIRED 且不再注入
  - 白名单之外的类别不自动晋升，保持待人工确认
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1]
  features: [FEAT-06]
  design_refs:
  - design §3.2 / §3.4
- id: S-13
  title: 非标任务的经验命中与任务族切换
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Runtime + 真实 PostgreSQL；真实 user_memory 候选集
  preconditions:
  - 用户已有 ACTIVE 的 PROCEDURE 经验，其中一条目标文本与被测问题相似，另一条不相似
  steps:
  - 用与旧经验目标文本相似但未执行 skill 的问题执行 Run
  - 用会命中 skill 的问题执行 Run 并观察 skill 执行后的模型请求
  - 检查两路注入结果与去重
  expected:
  - 无 skill 的 Run 中，目标文本过阈值的 ACTIVE 经验被注入，未过阈值的不注入
  - 执行 skill 后的下一个模型请求注入该 skill 族的 ACTIVE 经验
  - 两路命中同一经验时不重复注入
  expected_refs:
  - expected: 1
    steps: [1, 3]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-05]
  design_refs:
  - design §3.2 / §3.4
- id: E-01
  title: 学习链路失败降级为无学习且主 Run 不受影响
  priority: P1
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 学习链路注入故障（consolidation/回放），主执行链真实运行
  preconditions:
  - 可注入 consolidation 或评测失败
  steps:
  - 让学习链路抛错
  - 执行主 Run
  - 检查终态、日志与指标
  expected:
  - 主 Run 正常到达终态
  - 记录 warning 日志与 learning_pipeline_failures_total
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
  - design §3.5
- id: E-02
  title: 用户删除记忆后已激活经验不再注入且不被复活的提议带回
  priority: P1
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 forget 事务 + 真实 PostgreSQL + 真实聚合 job
  preconditions:
  - 存在一条已激活且可被注入的经验
  steps:
  - 用户删除该 memory_key
  - 再次执行 Run
  - 再次触发任务族聚合 job
  expected:
  - 删除后该记忆不再注入
  - forget_epoch 递增，既有提议被撤销
  - 后续聚合不复活该 key
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [1]
  - expected: 3
    steps: [3]
  features: [FEAT-04]
  design_refs:
  - design §3.3 / §3.5
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
  - design §3.5
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
  - design §3.5
- id: B-03
  title: 上界规模下选择仍正确、无外部 IO 且 token 集预计算
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: 纯函数选择与缓存装载；候选规模取 max_entries_per_user 的可配置上界；统计 token 化与外部调用
  preconditions:
  - 可构造达到上界规模的候选集
  steps:
  - 以默认规模与上界规模各装载并执行多次选择
  - 统计外部调用与候选内容 token 化次数
  - 记录耗时数据到验收证据
  expected:
  - 两种规模下预算与排序规则一致
  - 选择过程不产生网络调用或数据库查询
  - 候选内容 token 化只发生在缓存装载，请求期选择不重新 token 化
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [2, 3]
  features: [FEAT-05]
  design_refs:
  - design §3.5
- id: B-04
  title: 迁移与 ORM、契约模型同构
  priority: P1
  level: contract
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 PostgreSQL 迁移链 + ORM + 契约模型
  preconditions:
  - 测试库可迁移到当前链尾
  steps:
  - 迁移到 head
  - 比对表结构、索引与默认值
  - 执行 downgrade 再 upgrade
  expected:
  - 新列、唯一键与 partial index 与 ORM 定义一致
  - 既有记忆行默认 activation_state=ACTIVE，原有类别行为不变
  - downgrade 回收新对象且不破坏既有记忆行
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-04, FEAT-07]
  design_refs:
  - design §3.3 / §4.4
- id: B-05
  title: 需求盘点与覆盖表、manifest、context 一致
  priority: P1
  level: contract
  exec: auto
  env: none
  components: [backend]
  boundary: 真实盘面：任务文件、manifest、spec-context、测试文件
  preconditions:
  - 需求目录四个工件均已生成
  steps:
  - 运行盘点测试
  - 扰动一项覆盖或证据行，确认可定位失败
  expected:
  - 覆盖表、manifest、context 与测试路径逐项一致
  - 缺失或漂移时失败并指名条目
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  features: [FEAT-02]
  design_refs:
  - design §3.5
- id: B-06
  title: 评测与回放不在 Run 请求路径且走后台模型配额
  priority: P1
  level: contract
  exec: auto
  env: none
  components: [backend]
  boundary: 源码导入图 + 模型池配额（架构断言）
  preconditions:
  - 评测与回放模块已实现
  steps:
  - 断言 runtime 请求路径不导入评测与回放模块
  - 断言回放模型调用使用 BACKGROUND 工作负载与独立预算
  expected:
  - runtime 请求路径不导入评测与回放模块
  - 回放模型调用携带 BACKGROUND 工作负载，不占用前台 Run 的模型预算
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  features: [FEAT-02]
  design_refs:
  - design §3.2 / §3.5
- id: S-14
  title: 批量 Run 抽取为带条目语义的 Golden Set
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实批量 Run 与逐项 tool 事件 + 真实 PostgreSQL；抽取在 worker 内离线执行
  preconditions:
  - 一条用户消息包含 10 个客户，脚本化 provider 逐项调用同一个 skill
  - eval_max_items_per_case 为 10
  steps:
  - 执行该批量 Run 并记录逐项 tool 结果
  - 触发 Golden Set 抽取
  - 检查 eval_case 的条目语义、上限与脱敏
  expected:
  - eval_case 记录 item_count=10 与逐项标识，逐项结果可追溯到来源 Run
  - 超过 eval_max_items_per_case 的 case 标记 incomplete，不按成功计入
  - 抽取不改变 Run 状态，脱敏对逐项结果同样生效
  expected_refs:
  - expected: 1
    steps: [2, 3]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1, 3]
  features: [FEAT-07]
  design_refs:
  - design §3.2 / §3.3
- id: S-15
  title: 批量用例按逐项结果判定
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实两臂回放 + 真实 PostgreSQL；脚本化 provider 按 case 的逐项结果执行
  preconditions:
  - 同一任务族存在含 N 项的批量 eval_case
  steps:
  - 对批量 case 执行两臂回放
  - 构造候选臂缺项与单项失败的用例
  - 检查门禁判定与报告
  expected:
  - 任一缺项或单项失败时该 case 判失败
  - 全部成功时判成功，指标按 case 计并附 item_count
  - 报告含逐项结果与失败原因
  expected_refs:
  - expected: 1
    steps: [2, 3]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [3]
  features: [FEAT-02]
  design_refs:
  - design §3.5
- id: S-16
  title: 批量成功轨迹沉淀带批量语义的 PROCEDURE
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实跨 Run 聚合 job + 真实 PostgreSQL
  preconditions:
  - 同一任务族存在达到最小成功轨迹数的批量成功 Run
  steps:
  - 触发任务族聚合
  - 查询 runtime.user_memory
  - 检查候选的触发文本与状态
  expected:
  - 生成 PROCEDURE 候选，activation_state=PROPOSED
  - trigger_text 含批量语义（多个目标、逐项处理或核对数量）
  - 候选不参与注入，直到被激活
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-04]
  design_refs:
  - design §3.3 / §3.5
---
