# Tasks: 执行核心去框架化

- **Source**: .code-flow/tasks/2026-10-31/execution-core-deframework/execution-core-deframework.design.md
- **Created**: 2026-10-31
- **Updated**: 2026-10-31

## Proposal

在执行语义零变化的前提下，把 agent-core 执行核心从 LangGraph 解耦为显式阶段循环，移除依赖栈与空 schema，并让 specs 与交付文档与代码事实一致。完成后：干净安装不再携带 langgraph 栈、新建库只有 3 个 schema、当前文档不再声称 LangGraph 在执行链中，且现有测试断言零修改通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | execution-core-deframework.design.md#2.5 验收条件 | integration | 真实 RunService/Executor/AgentRunner + 真实 PostgreSQL；脚本化 provider | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_composition.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-02 | execution-core-deframework.design.md#2.5 验收条件 | contract | uv.lock / requirements.txt / 干净安装环境 | TASK-002 | verified | ["uv","run","pytest","-q","tests/contracts/test_dependency_surface.py"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-03 | execution-core-deframework.design.md#2.5 验收条件 | contract | 真实 PostgreSQL 的 information_schema + 迁移链 | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/test_schema_surface_contract.py","-k","s03"] | . | 300 | P0 | auto | local | - | - | - | no |
| E-02 | execution-core-deframework.design.md#2.5 验收条件 | contract | 迁移源文件的 upgrade/downgrade DDL 对称性 | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/test_schema_surface_contract.py","-k","e02"] | . | 300 | P1 | auto | local | - | - | - | no |
| S-04 | execution-core-deframework.design.md#2.5 验收条件 | unit | AgentRunner + 脚本化 provider（无网络、无数据库） | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_core/test_runner.py"] | . | 300 | P0 | auto | none | - | - | - | no |
| E-01 | execution-core-deframework.design.md#2.5 验收条件 | unit | AgentRunner + 脚本化 provider（无网络） | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_core/test_runner.py","-k","deadline or cancellation or incomplete_model_stream"] | . | 300 | P1 | auto | none | - | - | - | no |
| B-01 | execution-core-deframework.design.md#2.5 验收条件 | unit | AgentRunner + 持续返回 tool_calls 的脚本化 provider | TASK-005 | planned | ["uv","run","pytest","-q","tests/agent_core/test_runner.py","-k","max_turns_budget_finalizes"] | . | 300 | P1 | auto | none | - | - | - | no |
| S-05 | execution-core-deframework.design.md#2.5 验收条件 | contract | docs/、.code-flow/specs/、packages/、apps/、tests/ 的文本检索（排除历史记录） | TASK-004 | verified | ["uv","run","pytest","-q","tests/contracts/test_langgraph_residue.py"] | . | 300 | P1 | auto | none | - | - | - | no |
| S-06 | execution-core-deframework.design.md#2.5 验收条件 | contract | docs/configuration-inventory.csv 与源码 AST 比对 | TASK-005 | planned | ["uv","run","pytest","-q","tests/test_configuration_inventory.py"] | . | 300 | P1 | auto | none | - | - | - | no |

> 本表覆盖设计中的全部 9 个场景（6 个 S、2 个 E、1 个 B）；无 E2E：本需求不改变浏览器可见行为与 API 契约。

---

## TASK-001: 执行核心改为显式阶段循环

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: execution-core-deframework.design.md#3.2 架构设计, execution-core-deframework.design.md#3.4 接口设计, execution-core-deframework.design.md#2.5 验收条件
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-04, E-01, RULE-01
- **Estimate**: 1d
- **Capability**: 在不依赖 LangGraph 的情况下完成一轮含工具调用的 Run，终态、计数与落库关系与改动前一致

### Description

删除 `_build_graph()` 与 `ainvoke`，在 `AgentRunner` 内实现 `_execute` 显式阶段循环；`AgentGraphState` 改名 `AgentRunState`，保留 `next_node` 供 decide 阶段传路由。阶段方法、路由判断、预算/取消/挂起/事件注入/流式回调全部原样保留。公开 API `AgentRunner.run` 与回调/端口签名不变。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/agent_core/test_runner.py tests/agent_runtime/test_runtime_composition.py`
- [x] 删除 `from langgraph.graph import ...`、`_build_graph`、`self._graph` 与 `ainvoke` 调用
- [x] 实现 `_execute()` 循环，逐条对应既有条件边：预算耗尽 → finalize；有 tool_calls → tools；无 tool_calls 且存在 runtime_events → decide；decide 返回 prepare_context 或 finalize
- [x] 保留 `run()` 的 except 分支（STOP hook）与 `_stop_emitted` 语义
- [x] [S-04][unit] 运行既有 `tests/agent_core/test_runner.py` 20 条用例，断言测试文件除注释外零修改且全部通过
- [x] [E-01][unit] 覆盖取消、deadline、模型流无收尾信号三类路径的异常类型与 STOP hook 次数
- [x] [S-01][integration] 运行 `tests/agent_runtime/test_runtime_composition.py`，断言真实 PostgreSQL 上 assistant/tool 消息成对落库且终态一致
- [x] [harness-test#RULE-test-001] verifier：`uv run pytest -q tests/agent_core/test_runner.py tests/agent_runtime/test_runtime_composition.py`；unit 覆盖路由状态机，integration 覆盖真实 PostgreSQL，无新增浏览器旅程
- [x] 填写 Acceptance Evidence 并运行 `cf_validation`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 真实 RunService/Executor/AgentRunner + 真实 PostgreSQL | assistant 与 tool 消息成对落库、tool_call_id 非空、终态与计数一致 | tests/agent_runtime/test_runtime_composition.py | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_composition.py"] | verified |
| S-04 | unit | AgentRunner + 脚本化 provider | 20 条用例通过且测试文件未修改；纯文本回合不进工具阶段；tool_calls 回合回到模型 | tests/agent_core/test_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_runner.py"] | verified |
| E-01 | unit | AgentRunner + 脚本化 provider | 分别抛 RunnerModelError/RunnerCancelled/RunnerDeadlineExceeded；STOP hook 只触发一次 | tests/agent_core/test_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_runner.py","-k","deadline or cancellation or incomplete_model_stream"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | N/A：纯重构，改动前基线 2 passed，无法构造与缺陷对应的失败 | 2 passed（0.45s） | tests/agent_runtime/test_runtime_composition.py::test_b122_executor_full_turn_with_tool_and_audit、::test_b122_secret_isolation_in_executor_messages | 真实 PostgreSQL + 真实 RunService/Executor/AgentRunner | verified |
| S-04 | N/A：纯重构，改动前基线 20 passed | 20 passed（1.12s） | tests/agent_core/test_runner.py 的 20 条（含 test_tool_call_then_final_answer_and_hook_order、test_max_turns_budget_finalizes_without_extra_model_call） | 脚本化 provider，无网络 | verified |
| E-01 | N/A：纯重构，改动前基线 5 passed | 5 passed, 15 deselected（0.73s） | tests/agent_core/test_runner.py -k "deadline or cancellation or incomplete_model_stream" | 脚本化 provider，无网络 | verified |

回归：`tests/agent_runtime/test_tool_wait_state_machine.py` 12 passed（18.65s）。
- S-01: verified — automated command passed; run_id=da4bea3ba1f640798cd54c37f7a6c748 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=da4bea3ba1f640798cd54c37f7a6c748 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=da4bea3ba1f640798cd54c37f7a6c748 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] 实现完成：runner.py 图壳替换为显式阶段循环；unit 20 passed、integration 2 passed、回归 12 passed
- [2026-10-31] completed (done)

---

## TASK-002: 移除依赖栈并重生成锁与镜像需求清单

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: execution-core-deframework.design.md#3.1 方案选型, execution-core-deframework.design.md#4.1 部署架构
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-02, RULE-02
- **Estimate**: 0.5d
- **Capability**: 干净安装环境中 `import langgraph` 失败，五个服务镜像不再安装 langgraph 栈

### Description

从根 `pyproject.toml` 的 `agent` extra 与 `packages/agent-core/pyproject.toml` 移除 `langgraph`（及不再被引用的 `langchain-core`）；`uv lock` 后按 `build/docker-build/export-requirements.sh` 重生成 `requirements.txt`；干净环境验证 import 失败；重建镜像矩阵。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/contracts/test_dependency_surface.py`
- [x] 移除两处依赖声明并执行 `uv lock`
- [x] 用 `build/docker-build/export-requirements.sh` 重生成 `requirements.txt`（禁止手改）
- [x] 干净 `uv sync --frozen` 后断言 `import langgraph` 抛 ModuleNotFoundError
- [x] [S-02][contract] 断言 requirements.txt 与 uv.lock 中 langgraph/langchain/langsmith 相关行数为 0
- [x] 重建 images matrix 并记录 4/4 结果 —— 本任务环境不构建镜像，交由 CI `images` matrix 在合入后执行（deferred_to_review）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | contract | uv.lock、requirements.txt、干净安装环境 | 需求清单无 langgraph 栈；import 抛 ModuleNotFoundError；uv sync --frozen 退出码 0 | tests/contracts/test_dependency_surface.py | ["uv","run","pytest","-q","tests/contracts/test_dependency_surface.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-02 | FAIL: `test_langgraph_is_not_importable` —— 依赖已移出清单但 venv 仍安装 langgraph | 3 passed（0.49s） | tests/contracts/test_dependency_surface.py::test_requirements_txt_excludes_the_langgraph_stack、::test_uv_lock_excludes_the_langgraph_stack、::test_langgraph_is_not_importable | 干净 `uv sync --frozen --all-packages --extra dev --extra redis` 后 `import langgraph` → ModuleNotFoundError；requirements.txt 66 行 | verified |

回归：`tests/agent_core/test_runner.py` 20 passed（1.15s），确认移除依赖后执行核心无隐式依赖。
- S-02: verified — automated command passed; run_id=e5ea18529a0244a291337d67a346e9d8 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---

## TASK-003: 删除空 langgraph schema 的迁移

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: execution-core-deframework.design.md#3.3 数据设计, execution-core-deframework.design.md#4.4 数据迁移
- **Spec-Refs**: harness-data#RULE-data-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: S-03, E-02, RULE-03
- **Estimate**: 0.5d
- **Capability**: 新建库迁移到 head 后 schema 恰为 control/runtime/task；downgrade 重建空 schema

### Description

新增迁移 revision（链尾顺延），upgrade 执行 `DROP SCHEMA IF EXISTS langgraph`，downgrade 执行 `CREATE SCHEMA IF NOT EXISTS langgraph`。不改写已应用的 0001；迁移不读取环境变量。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/acceptance/test_schema_surface_contract.py`
- [x] 新增迁移文件，`down_revision` 指向当前链尾
- [x] 在验收库上执行 upgrade → 查询 information_schema → 执行 downgrade → 再 upgrade，并记录证据
- [x] [S-03][contract] 断言 schema 集合恰为 control/runtime/task，迁移链单头
- [x] [E-02][contract] 断言迁移源文件 upgrade 删除、downgrade 重建，DDL 对称
- [x] [harness-data#RULE-data-001] verifier：`uv run pytest -q tests/acceptance/test_schema_surface_contract.py`；迁移保持单链、标准列与 JSONB 口径，不新建业务表
- [x] 填写 Acceptance Evidence 并运行 `cf_validation`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | contract | 真实 PostgreSQL information_schema + 迁移链 | schema 名集合恰为 control/runtime/task；不含 langgraph；迁移单头 | tests/acceptance/test_schema_surface_contract.py | ["uv","run","pytest","-q","tests/acceptance/test_schema_surface_contract.py","-k","s03"] | verified |
| E-02 | contract | 迁移源文件 upgrade/downgrade DDL | upgrade 含 DROP SCHEMA IF EXISTS langgraph；downgrade 含 CREATE SCHEMA IF NOT EXISTS langgraph | tests/acceptance/test_schema_surface_contract.py | ["uv","run","pytest","-q","tests/acceptance/test_schema_surface_contract.py","-k","e02"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | N/A：迁移与测试同批引入；缺陷来源是 `0001` 建出的空 schema，head 后不存在 | 2 passed（1.88s，含 s03） | tests/acceptance/test_schema_surface_contract.py::test_s03_schema_surface_excludes_langgraph | 验收 conftest 建临时库并迁移到 head；information_schema 查询返回 control/runtime/task | verified |
| E-02 | FAIL: 首跑判定过宽（`0001` 的 downgrade 也含 DROP DDL，matches=2） | 2 passed（1.88s，含 e02） | tests/acceptance/test_schema_surface_contract.py::test_e02_langgraph_schema_drop_migration_is_symmetric | 独立临时库 round trip：upgrade head → control,runtime,task；downgrade 0028 → 含 langgraph；re-upgrade → 无 langgraph | verified |

回归：`tests/acceptance/test_foundation_schema_parity.py` 3 passed（4.18s）。
- S-03: verified — automated command passed; run_id=d63168e69bf94959819315df6e6e872b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=d63168e69bf94959819315df6e6e872b (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---
- [2026-10-31] 实现完成：新增 0029 drop migration；round trip 与契约测试通过

## TASK-004: spec 与交付文档对齐

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-003
- **Source**: execution-core-deframework.design.md#3.1 方案选型, execution-core-deframework.design.md#4.1 部署架构
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-05, RULE-04
- **Estimate**: 1d
- **Capability**: 当前文档与 specs 不再声称 LangGraph 在执行链中，ADR 记录被否方案与历史记录口径

### Description

改写 persistence spec 的 schema 名单规则（经 cf-spec 编辑流程）、docs/00、01、02 §9、04 §15、08 与基线 V2.0/V7；清理 tests 中两处提及 LangGraph 的注释/文档字符串；新增 ADR 说明移除原因、被否方案（接 PG Checkpointer）与历史记录（docs/17、capacity 分析）。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/contracts/test_langgraph_residue.py`
- [x] 通过 cf-spec 流程修改 `persistence/harness-data.md` 的 schema 名单规则并 refresh
- [x] 改写 docs/00/01/02/04/08 与基线 V2.0/V7 的 LangGraph 措辞
- [x] 清理 `tests/agent_core/test_hook_lifecycle.py` 与 `tests/agent_runtime/test_runtime_composition.py` 的注释/文档字符串
- [x] 新增 ADR：`docs/issues/2026-10-31-execution-core-deframework-adr.md`
- [x] [S-05][contract] 断言当前非归档文档、specs、代码无 LangGraph 残留，ADR 存在且含被否方案

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | contract | docs/、.code-flow/specs/、packages/、apps/、tests/ 的真实文本 | 残留命中数为 0；ADR 存在并记录被否方案；schema 名单为 3 个 | tests/contracts/test_langgraph_residue.py | ["uv","run","pytest","-q","tests/contracts/test_langgraph_residue.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | FAIL: 残留命中 `.code-flow/specs/_session/task-execution-core-deframework.md`（生成的 TASK 投影被误算为当前文档） | 2 passed（0.78s） | tests/contracts/test_langgraph_residue.py::test_s05_current_surfaces_do_not_claim_langgraph、::test_s05_adr_records_the_rejected_checkpointer_option | 真实文本检索 docs/、.code-flow/specs/（排除 _session）、packages/、apps/、tests/；ADR 文件存在且含 PG Checkpointer 与被否方案 | verified |

说明：spec 规则文本修改后执行 `cf_spec_context refresh`，Context 更新为 `a45f66d5…`，status gate pass。
- S-05: verified — automated command passed; run_id=c10f1380f3e14a9a8ffbb6faa73b2967 (confirmed_by: runner)

### Log

- [2026-10-31] created (draft)
- [2026-10-31] started
- [2026-10-31] completed (done)

---
- [2026-10-31] 实现完成：spec 规则改为 3 个 schema；docs/00/01/02/04/08 与基线 V2.0/V7 改写；ADR 落 docs/issues；残差检索通过

## TASK-005: 机检恢复与有界性回归复核

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-001, TASK-004
- **Source**: execution-core-deframework.design.md#3.5 质量实现方案, execution-core-deframework.design.md#2.5 验收条件
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: B-01, S-06, RULE-05
- **Estimate**: 0.5d
- **Capability**: 配置盘点机检恢复绿色，`max_turns` 有界性用例确认覆盖循环化后的行为

### Description

按 AST 口径重生成 `docs/configuration-inventory.csv` 的派生行（`runner.py` 行号随循环化变化）；复核既有 `test_max_turns_budget_finalizes_without_extra_model_call` 是否仍覆盖有界性，必要时补强。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/test_configuration_inventory.py tests/agent_core/test_runner.py -k "configuration or max_turns"`
- [ ] 按 AST 口径重生成派生行，删除已不存在的常量行，修正行号
- [ ] [B-01][unit] 运行 `tests/agent_core/test_runner.py -k max_turns_budget_finalizes`，断言终态 BUDGET_EXCEEDED 且模型调用次数不超过 max_turns
- [ ] [S-06][contract] 运行 `tests/test_configuration_inventory.py`，断言 CSV 与源码逐项一致

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-01 | unit | AgentRunner + 持续返回 tool_calls 的脚本化 provider | 终态 BUDGET_EXCEEDED；模型调用次数不超过 max_turns；无 RecursionError | tests/agent_core/test_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_runner.py","-k","max_turns_budget_finalizes"] | planned |
| S-06 | contract | docs/configuration-inventory.csv 与源码 AST | CSV 与源码逐项一致；runner.py 行号与常量对应 | tests/test_configuration_inventory.py | ["uv","run","pytest","-q","tests/test_configuration_inventory.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)

---

## TASK-006: 需求收口清单与盘面交叉核对

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005
- **Source**: execution-core-deframework.design.md#2.5 验收条件
- **Spec-Refs**: N/A
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: RULE-05
- **Estimate**: 0.5d
- **Capability**: 执行 `tests/execution_core_inventory.py` 能核对覆盖表、manifest、spec-context 与真实盘面一致

### Description

按 `harness-test` 的收口约定新增 `tests/execution_core_inventory.py`：以真实盘面为输入，交叉核对 Acceptance Coverage 表、`.acceptance-manifest.json`、`spec-context.yml` 的 required 规则与已登记的测试文件/命令是否一致；缺失时失败并指名条目。

### Checklist

- [ ] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/execution_core_inventory.py`
- [ ] 覆盖表每行唯一负责人且终态，manifest 与覆盖表同 ID/owner/命令，并比对 level/boundary/cwd
- [ ] 终态场景在 owner 的 Acceptance Evidence 中登记，非 manual 行必须有同 ID 的证据表行
- [ ] 契约表为每个 Acceptance-Refs ID 留一行，`-k` 令牌真实命中
- [ ] 至少扰动 4 类（改状态/删证据行/伪造用例名/改 manifest 字段）并记录变红与还原后复绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-05 | contract | 真实盘面：任务文件、manifest、spec-context、测试文件 | 四者逐项一致；缺失时失败并指名条目 | tests/execution_core_inventory.py | ["uv","run","pytest","-q","tests/execution_core_inventory.py"] | planned |

### Acceptance Evidence

### Log

- [2026-10-31] created (draft)
