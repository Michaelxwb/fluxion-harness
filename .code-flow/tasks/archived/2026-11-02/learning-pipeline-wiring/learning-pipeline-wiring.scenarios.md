---
id: learning-pipeline-wiring-scenarios
description: 自进化学习链路运行时接线（族传播、冻结设置、调度、生产回放、CLI、E2E）验收场景唯一源
stages:
- design
enforcement: advisory
schema: 1
requirement: learning-pipeline-wiring
scenarios:
- id: S-01
  title: 执行层任务族传播到记忆注入
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 executor 组合 + 真实 PostgreSQL；脚本化 provider
  preconditions:
  - 用户存在 ACTIVE 的 skill 族与 agent 族经验
  steps:
  - 执行一个会调用 execute_skill 的 Run
  - 观察 skill 执行后的下一个模型请求
  - 再执行一个不调用 skill 的 Run
  expected:
  - skill 成功执行后的下一个模型请求注入该 skill 族经验
  - 无 skill 的 Run 使用 agent 族并在同一会话连续生效
  - 两路命中同一经验只注入一次
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [2, 3]
  features: [FEAT-01]
  design_refs:
  - design §3.2 / §3.4
- id: S-02
  title: 新 Run 冻结 learning 设置
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Snapshot policy_json + 真实 PostgreSQL
  preconditions:
  - 平台设置 learning 分组可修改
  steps:
  - 创建 Run 并读取其 policy_json
  - 修改平台的 retrieval_min_score 与 learning_enabled
  - 观察既有 Run 与后续新 Run 的选择行为
  expected:
  - policy_json 冻结 learning_enabled、retrieval_min_score 与偏好限额
  - 修改设置不影响已创建的 Run
  - 新 Run 使用新值
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [3]
  features: [FEAT-02]
  design_refs:
  - design §3.2 / §3.4
- id: S-03
  title: 调度器自动抽取 Golden Set 与经验候选
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 worker/supervisor 循环 + 真实 PostgreSQL
  preconditions:
  - 同一任务族存在最小成功轨迹数且 learning_enabled=true
  steps:
  - 触发学习调度器一轮
  - 查询 eval_case 与 user_memory
  - 再触发一轮
  expected:
  - 生成 eval_case 与 PROPOSED 经验候选
  - 重复触发幂等，不产生重复行
  - learning_enabled=false 的租户被跳过
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1]
  features: [FEAT-03]
  design_refs:
  - design §3.2 / §3.3
- id: S-04
  title: 生产 CaseRunner 执行两臂回放
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 Snapshot 与 Run 输入重建 + 真实 PostgreSQL；模型传输用本地假上游
  preconditions:
  - eval_case 与来源 Snapshot 存在
  steps:
  - 用生产 CaseRunner 跑基线臂与候选臂
  - 检查两臂请求内容与判定
  - 检查 eval_report
  expected:
  - 两臂均从冻结 Snapshot 与来源 Run 输入重建请求
  - 候选臂请求携带经验文本，基线臂不携带
  - 逐项判定与报告按 case 落库
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-04]
  design_refs:
  - design §3.4 / §3.5
- id: S-05
  title: 回放达标自动进入 TRIAL 并按观察期晋升
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实调度 + 真实回放结果 + 真实审计
  preconditions:
  - 存在 PROPOSED 经验且回放判定 PASSED
  steps:
  - 调度一轮并观察状态
  - 模拟观察期纠正信号不劣化
  - 模拟劣化
  expected:
  - 回放达标后自动进入 TRIAL 并写审计
  - 观察期不劣化自动晋升 ACTIVE
  - 劣化自动 RETIRED
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-05]
  design_refs:
  - design §3.2 / §3.4
- id: S-06
  title: learning_admin 命令面
  priority: P1
  level: contract
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 CLI 进程 + 真实 PostgreSQL + 真实审计
  preconditions:
  - 存在 PROPOSED/ACTIVE 经验
  steps:
  - 执行 list/replay/activate/retire/explain
  - 检查 stdout JSON 与退出码
  - 检查审计
  expected:
  - 命令输出机器可读 JSON，失败走 stderr 与退出码
  - activate 读取最近回放结果，未达标拒绝
  - 每个状态变化写审计并带 operator
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [1]
  - expected: 3
    steps: [1, 3]
  features: [FEAT-06]
  design_refs:
  - design §3.4
- id: S-07
  title: 端到端：同类任务沉淀后影响下一次 Run
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 supervisor + 真实 executor + 真实 PostgreSQL；模型为脚本化 provider
  preconditions:
  - learning_enabled=true 的租户
  steps:
  - 执行 3 次同族成功任务
  - 触发学习调度一轮并等待回放与 TRIAL
  - 执行第 4 次同族任务
  expected:
  - 生成 eval_case、经验候选与回放报告
  - 经验进入 TRIAL/ACTIVE 且有审计
  - 第 4 次 Run 的注入归因包含该经验
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-07]
  design_refs:
  - design §3.2 / §3.5
- id: E-01
  title: 学习调度失败不影响主 Run
  priority: P1
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 调度注入故障，主执行链真实运行
  preconditions:
  - 可注入抽取或回放失败
  steps:
  - 让调度链路抛错
  - 执行主 Run
  - 检查终态与指标
  expected:
  - 主 Run 正常完成
  - warning 与 learning_pipeline_failures_total 可见
  - 不产生半成品学习物
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1]
  features: [FEAT-08]
  design_refs:
  - design §3.5
- id: E-02
  title: 关闭 learning_enabled 后不再学习与注入
  priority: P1
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实设置冻结 + 真实调度 + 真实 PostgreSQL
  preconditions:
  - 租户已关闭 learning_enabled
  steps:
  - 执行成功任务
  - 触发调度
  - 执行新 Run
  expected:
  - 不生成新的 eval_case/候选
  - 新 Run 不注入经验
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  features: [FEAT-09]
  design_refs:
  - design §3.4 / §3.5
- id: B-01
  title: 每个学习能力都有生产调用方
  priority: P1
  level: contract
  exec: auto
  env: none
  components: [backend]
  boundary: 真实源码调用图（AST/导入扫描）
  preconditions:
  - 学习链路实现文件已存在
  steps:
  - 扫描生产代码的导入与调用点
  - 断言每个能力至少一个非测试调用方
  expected:
  - 抽取、候选、回放、激活、族传播、设置读取均有生产调用方
  - 仅测试调用或仅端口注入视为失败
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  features: [FEAT-10]
  design_refs:
  - design §3.5
---
