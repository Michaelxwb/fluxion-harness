---
id: execution-core-deframework-scenarios
description: 执行核心去框架化验收场景唯一源
stages:
- design
enforcement: advisory
schema: 1
requirement: execution-core-deframework
scenarios:
- id: S-01
  title: 真实 Runtime 执行链完成一轮含工具调用的 Run 并落 canonical events
  priority: P0
  level: integration
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 RunService → Executor → AgentRunner + 真实 PostgreSQL；模型为脚本化 provider，不发网络请求
  preconditions:
  - 本地 PostgreSQL 测试库可用且已迁移到 head
  - uv 依赖已同步
  steps:
  - 运行 tests/agent_runtime 中覆盖一轮工具调用的既有用例
  - 查询该 Run 的 runtime.canonical_event
  - 校验 Run 终态与计数
  expected:
  - assistant 回合与其 tool 结果成对落库，tool 消息的 tool_call_id 非空
  - Run 终态为 COMPLETED，turns 与 tool_calls 计数与改动前基线一致
  - 该套件退出码为 0
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1]
  features: [FEAT-01]
  design_refs:
  - backend design §2.5.2 / §3.2
- id: S-02
  title: langgraph 依赖栈从锁文件、requirements 与安装环境消失
  priority: P0
  level: contract
  exec: auto
  env: none
  components: [backend, build]
  boundary: uv.lock / requirements.txt / 干净虚拟环境
  preconditions:
  - uv sync --frozen 可在干净环境执行
  steps:
  - 运行 build/docker-build/export-requirements.sh 重生成 requirements.txt
  - 在 requirements.txt 中检索 langgraph 与 langchain
  - 在干净环境执行 uv sync --frozen 后运行 python -c "import langgraph"
  expected:
  - requirements.txt 中 langgraph/langchain 相关行数为 0
  - python -c "import langgraph" 抛 ModuleNotFoundError
  - uv sync --frozen 退出码为 0
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [3]
  features: [FEAT-02]
  design_refs:
  - backend design §3.1 / §4.1
- id: S-03
  title: 新建库 schema 名单收缩为 control/runtime/task 且迁移单链单头
  priority: P0
  level: contract
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 PostgreSQL 新建库 + alembic 迁移到 head
  preconditions:
  - 可创建临时数据库
  steps:
  - 建空库并执行迁移到 head
  - 查询 information_schema.schemata
  - 校验 migrations/versions 的 revision 链
  expected:
  - schema 名集合恰为 control、runtime、task，不含 langgraph
  - alembic heads 数量为 1 且链尾为 0001
  - tests/acceptance/test_foundation_schema_parity.py 退出码为 0
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [3]
  - expected: 3
    steps: [1]
  features: [FEAT-03]
  design_refs:
  - backend design §3.3
- id: E-02
  title: 迁移回滚重建空 langgraph schema 且不触碰业务表
  priority: P1
  level: contract
  exec: auto
  env: local
  components: [backend, postgres]
  boundary: 真实 PostgreSQL + alembic downgrade
  preconditions:
  - 测试库已迁移到 head
  steps:
  - 执行 0029 的 downgrade
  - 查询 schema 与业务表数量
  expected:
  - langgraph schema 重新存在且其中表数量为 0
  - control/runtime/task 的业务表数量与 downgrade 前一致
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [2]
  features: [FEAT-03]
  design_refs:
  - backend design §3.3 / §4.4
- id: S-04
  title: 显式阶段循环的路由与既有图语义一致
  priority: P0
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: AgentRunner + 脚本化 provider（无网络、无数据库）
  preconditions:
  - uv 依赖已同步
  steps:
  - 运行 tests/agent_core/test_runner.py 全部用例
  - 执行仅返回纯文本的回合
  - 执行返回 tool_calls 的回合
  expected:
  - 20 条既有用例全部通过且测试文件未被修改
  - 纯文本回合不进入工具阶段，直接返回 COMPLETED
  - 带 tool_calls 的回合执行工具后再次调用模型
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-01]
  design_refs:
  - backend design §2.5.2 / §3.2
- id: E-01
  title: 异常、取消与期限路径保持既有语义
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: AgentRunner + 脚本化 provider（无网络）
  preconditions:
  - uv 依赖已同步
  steps:
  - 让模型流缺少收尾信号
  - 执行期间触发取消
  - 让执行超过 deadline
  - 运行对应既有用例
  expected:
  - 三种情况分别抛 RunnerModelError、RunnerCancelled、RunnerDeadlineExceeded
  - STOP hook 在异常路径只触发一次
  - 对应用例全部通过
  expected_refs:
  - expected: 1
    steps: [1, 2, 3]
  - expected: 2
    steps: [4]
  - expected: 3
    steps: [4]
  features: [FEAT-01]
  design_refs:
  - backend design §3.5
- id: B-01
  title: 阶段循环有界，max_turns 用尽必须终态返回
  priority: P1
  level: unit
  exec: auto
  env: none
  components: [backend]
  boundary: AgentRunner + 持续返回 tool_calls 的脚本化 provider
  preconditions:
  - uv 依赖已同步
  steps:
  - 运行既有 max_turns 有界用例
  - 检查返回状态与调用次数
  expected:
  - 用例通过，终态为 BUDGET_EXCEEDED
  - 模型调用次数不超过 max_turns，不出现 RecursionError
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  features: [FEAT-01]
  design_refs:
  - backend design §3.5
- id: S-05
  title: 文档与 spec 无 LangGraph 残留且 ADR 记录被否方案
  priority: P1
  level: contract
  exec: auto
  env: none
  components: [backend, docs]
  boundary: docs/（排除 docs/17 历史评审记录）、.code-flow/specs/、packages/、apps/ 的文本检索；排除本需求目录、archived 与 scripts/capacity 历史测量
  preconditions:
  - 文档改写完成
  steps:
  - 对 docs/（排除 docs/17）、.code-flow/specs/、packages/、apps/ 检索 langgraph
  - 检查 ADR 文件内容
  - 检查 persistence spec 的 schema 名单
  expected:
  - 检索命中数为 0
  - ADR 存在，列明被否方案为接 PG Checkpointer，并注明 docs/17 与 capacity 分析属历史记录
  - persistence spec 的 schema 名单为 3 个
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [2]
  - expected: 3
    steps: [3]
  features: [FEAT-04]
  design_refs:
  - backend design §3.1 / §4.1
- id: S-06
  title: 配置盘点清单与源码逐项一致
  priority: P1
  level: contract
  exec: auto
  env: none
  components: [backend, docs]
  boundary: docs/configuration-inventory.csv 与源码 AST 比对
  preconditions:
  - runner.py 改动完成
  steps:
  - 按 AST 口径重生成派生行
  - 运行 tests/test_configuration_inventory.py
  expected:
  - 机检退出码为 0
  - runner.py 相关行只保留仍然存在的常量，行号与源码一致
  expected_refs:
  - expected: 1
    steps: [2]
  - expected: 2
    steps: [1]
  features: [FEAT-05]
  design_refs:
  - backend design §3.5

- id: S-07
  title: 需求收口盘点与盘面一致
  priority: P1
  level: contract
  exec: auto
  env: none
  components: [backend, docs]
  boundary: 真实任务文件、manifest、spec-context 与测试盘面
  preconditions:
  - 任务文件与 acceptance manifest 已生成
  steps:
  - 运行收口盘点用例
  expected:
  - 覆盖表、manifest、spec-context 与测试盘面逐项一致
  - 缺失时用例失败并指名条目
  expected_refs:
  - expected: 1
    steps: [1]
  - expected: 2
    steps: [1]
  features: [FEAT-05]
  design_refs:
  - backend design §2.5.2 / §3.5
---
