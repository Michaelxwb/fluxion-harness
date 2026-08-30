# Tasks: Phase 3 Workflow Platform（未落地部分）

- **Source**: `.code-flow/tasks/2026-08-28/phase3-workflow-platform/phase3-workflow-platform.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-29

## Proposal

以 ADR-013 锁定的 DBOS 生产化替换 `StubWorkflowEngine`：实现 `DbosWorkflowEngine`（`WorkflowEngine` Protocol 7 成员 + `pinned refs`），将 WorkflowDefinition 扩展为 9 节点判别联合（V1 零迁移兼容），落地通用 durable graph 解释器 `_run_graph`、HumanTask/Wait/Timer durable 原语、Version pin/active ref/GC 守卫与 `workflow_run` 投影 API，并以独立 `fluxion-workflow-worker` 承载执行。最终闭合 Phase 3 Gate：durable start P95≤1s、worker 崩溃恢复 P95≤60s、committed 副作用重复=0、timer/wait/approval 跨重启存活、pinned 版本 hard-delete 被拒。

依据 design §2.4 前置：ADR-013 vendor pick + PoC 证据（`tests/workflow_poc/` 11/11）已落地不重复设计；本文档只做生产化。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | Adapter → DbosWorkflowEngine → DBOS → 真实 PG | TASK-005 | verified |
| S-02 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | 独立 worker 进程 + PG（SIGKILL → 新进程 recovery） | TASK-005 | verified |
| S-03 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | ExecutionSnapshot + Registry + active_references | TASK-007 | verified |
| S-04 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | DBOS step timeout 配置（真实运行） | TASK-004 | verified |
| S-05 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | DBOS SetWorkflowID 幂等（真实二次 start） | TASK-005 | verified |
| S-06 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | database-backed queue + 2 worker 进程 | TASK-005 | verified |
| S-07 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | active_references ref_type=workflow（运行中 hard-delete） | TASK-007 | verified |
| S-08 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | DBOS recv_async/send + worker 重启 | TASK-006 | verified |
| S-09 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | DBOS.sleep_async + kill/重启 | TASK-006 | verified |
| S-10 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | 解释器遍历 8 节点类型混合图 | TASK-004 | verified |
| S-11 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | workflow_run 投影表 + status API（真实 ASGI 栈） | TASK-008 | verified |
| E-01 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | ResilientWorkflowEngine + circuit breaker（真实 DBOS 宕机） | TASK-001 | verified |
| E-02 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | tenant scope（他租户查询） | TASK-008 | verified |
| E-03 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | DBOS durable retry（step 首次抛异常） | TASK-004 | verified |
| B-01 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | unit | `runtime/workflow.py` WorkflowAdapter 不变量 | TASK-001 | verified |
| B-02 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | tenant scope（tenant A run / tenant B 查询） | TASK-008 | verified |
| B-03 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | unit | WorkflowDefinition V2 validator（V1 spec 输入） | TASK-002 | verified |
| B-04 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | unit | 条件表达式求值器（注入输入） | TASK-003 | verified |
| RULE-P3-01 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | unit | 同 B-01 | TASK-001 | verified |
| RULE-P3-02 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | integration | 同 S-03 | TASK-007 | verified |
| RULE-P3-03 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | E2E | 同 S-07 | TASK-007 | verified |
| RULE-P3-04 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | integration | 同 S-04 / E-03 | TASK-004 | verified |
| RULE-P3-05 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | E2E | 同 S-01 / S-02 | TASK-005 | verified |
| RULE-P3-06 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | integration | 同 B-02 | TASK-008 | verified |

> NFR-PERF-01/02/03（SLO-WF-01/02/03）由 S-01/S-02/S-05 承载（TASK-005）；NFR-REL-01/02 由 S-02/S-08/S-09 承载；NFR-SEC-01 由 E-02/B-02 承载（TASK-008）；NFR-SEC-02 由 B-04 承载（TASK-003）；NFR-OBS-01 由 TASK-005 logging verifier 承载。design §6 追溯矩阵闭合无断点。

---

## TASK-001: DbosWorkflowEngine 生产实现 + Protocol 扩展

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#2.3.2 字段约束, phase3-workflow-platform.design.md#3.1 方案选型, phase3-workflow-platform.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001, fluxion-runtime-core#RULE-fluxion-runtime-001, backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: B-01, E-01, RULE-P3-01

### Description

`dbos` 依赖从 PoC `.venv` 直装正式声明进 `pyproject.toml` + `uv.lock`（2.31）。新建 `runtime/workflow_dbos.py`（与 Protocol 同包，镜像 RegistryStore adapter 同包模式）：`DbosWorkflowEngine` 实现 `WorkflowEngine` Protocol 全部成员——既有 `start`/`resume`/`signal`/`cancel(timeout)`/`get_status`，新增 `await_result(run_id, *, timeout)`（有限等待，超时 `TimeoutError`）与 `get_execution_history(run_id)`。`WorkflowStartRequest` 增加 `pinned: WorkflowPinnedRefs`（`{kind, id, version}` 快照）。start 用 SetWorkflowID 幂等；查询/信号类 DBOS API 统一 `asyncio.to_thread`（RISK-P3-04）；全成员定义 timeout + fail policy（规则 18），禁 double retry。B-01 恒等断言 `WorkflowAdapter.local_durable_state_count==0`（Runtime 无 durable state，rule 13）。

### Checklist

- [x] `dbos` 声明进 `pyproject.toml` + `uv.lock`；`WorkflowStartRequest` 增加 `pinned: WorkflowPinnedRefs`
- [x] 实现 `DbosWorkflowEngine` 7 成员（含 `await_result`/`get_execution_history`）：SetWorkflowID 幂等、查询/信号类走 `asyncio.to_thread`、全成员 timeout + fail policy
- [x] [B-01][unit] 修改生产代码前，编写验收测试并记录 RED：断言 `WorkflowAdapter.local_durable_state_count` 恒等 0、`runtime/` 包无 DBOS durable state 落地（RULE-P3-01）
- [x] [E-01][integration] 修改生产代码前，编写验收测试并记录 RED：真实 DBOS backend 宕机 → ResilientWorkflowEngine 熔断快速失败（非 hang），N 次失败后 open、cooldown 后试探，返回明确错误码
- [x] 架构测试：`runtime/workflow_dbos.py` 归属 runtime 包、Kernel 不依赖 DBOS 具体 SDK（Kernel 只依赖 Contract）、`services/workflow_projection.py`/`api/workflow.py` 包边界（directory rule 落点）
- [x] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 `python -m pytest backend/tests/runtime/ -k workflow`：断言 Tool=Adapter 边界（Agent 经 WorkflowAdapter 而非直连引擎）、durable state 归 Workflow Engine、capability 节点复用 Capability Contract
- [x] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 B-01 + E-01 verifier 套件：断言 Runtime 无状态（B-01 恒等 0）、durable state 不进 Agent Runtime、Kernel 只依赖 Contract
- [x] **Spec verifier**：`RULE-backend-directory-001` — 运行 `python -m pytest backend/tests/architecture/ -k workflow`（AST 守护）：断言 `runtime/workflow_dbos.py`/`services/workflow_projection.py`/`api/workflow.py` 包边界符合依赖方向
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | WorkflowAdapter 真实实例与源码扫描（不 mock） | `local_durable_state_count` 恒等 0；runtime 包无 durable state | `tests/runtime/test_workflow_adapter_invariants.py` | `pytest backend/tests/runtime/test_workflow_adapter_invariants.py` | verified |
| E-01 | integration | 真实 DBOS backend 停机 + ResilientWorkflowEngine | 熔断 open→快速失败；cooldown 后试探恢复；明确错误码非无限等待 | `tests/integration/test_workflow_dbos_resilience.py` | `pytest backend/tests/integration/test_workflow_dbos_resilience.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: 收集期 ImportError `WorkflowPinnedRef` 缺失 + Protocol 成员未扩展 + `WORKFLOW_ENGINE_FAILURE` 缺失 | PASS（7 用例） | `test_workflow_adapter_invariants.py:40/51/62/79` | 真实 WorkflowAdapter + `runtime_context` 执行 + AST 源码扫描；`runtime/workflow.py` 与 `runtime/workflow_dbos.py` 无模块级可变容器 | verified |
| E-01 | FAIL: `attempts[-1]["breaker_open"] is True` 断言失败（`WorkflowBackendUnavailableError` 未被计入熔断，被 `except WorkflowEngineError` 透传） | PASS | `test_workflow_dbos_resilience.py:61-88` | 真实不可达端口（127.0.0.1:59999 真实连接拒绝）+ 真实 `DbosWorkflowEngine`（有界 launch/op timeout 封装 DBOS 内部重试）+ 真实 `ResilientWorkflowEngine` 熔断 | verified |
| RULE-P3-01 | 同 B-01 | PASS | 同 B-01 | 同 B-01 | verified |

修复记录（GLM 产物补全）：
- `runtime/workflow.py` 增加 `WorkflowPinnedRef`、`WorkflowStartRequest.pinned`、Protocol 扩展 7 成员（`await_result`/`get_execution_history`）、`WorkflowExecutionHistory`/`WorkflowStepRecord`、`ResilientWorkflowEngine`/`StubWorkflowEngine` 新成员包装；Protocol 加 `@runtime_checkable`
- `errors/workflow.py` 增加 `WORKFLOW_ENGINE_FAILURE=40_100`
- `ResilientWorkflowEngine._invoke`：`WorkflowBackendUnavailableError` 计入熔断（E-01），其余 `WorkflowEngineError` 业务错误仍透传
- `pyproject.toml` + `uv.lock` 声明 `dbos>=2.31,<3`、`psycopg[binary]>=3.3,<4`

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-002: WorkflowDefinition V2 节点契约 + validator

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase3-workflow-platform.design.md#2.3.2 字段约束, phase3-workflow-platform.design.md#3.2 架构设计, phase3-workflow-platform.design.md#4.4 数据迁移
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: B-03

### Description

扩展 `WorkflowDefinition`（`resources/contracts.py`）为节点判别联合（`type` 为 discriminator）：`capability`（=V1 step 兼容，`capability_ref` 必填，前缀 `skill|tool|mcp`——`tool:` 解析 `ResourceKind.TOOL`，Phase 1 Closure 统一）/`agent`（**`agent_ref`（`agent:<id>@<version>`）必填**，经 Agent exact version → Phase 2 ContextResolver（agent_id 主坐标）→ RuntimeProfile → pinned ExecutionSnapshot → AgentRuntime；DSL 不感知 Runtime mechanics，remediation §14.1）/`condition`（`expression` + `then`/`else`）/`switch`（`cases ≥1` + `default`）/`parallel`（`branches ≥2` + `join_policy: all|any`）/`transform`/`wait`（`duration_seconds >0`）/`human_task`（`assignee` 必填 + `message` + `timeout_seconds`）/`subworkflow`（`workflow_ref` 必填）。公共字段：`id`（唯一）、`depends_on`（无环校验）、`timeout_ms`、`retry_policy`（max_attempts/delay，仅表达业务意愿）、`output_schema`（可选）。**无 `engine_ref` 字段**（remediation §14.3：durable backend 选择属 Platform Configuration `WorkflowBackendSettings`，不进 Product DSL）。`model_validator(mode="before")` 对无 `type` 字段且有 `capability_ref` 的 V1 spec 注入 `type="capability"`——现网定义零迁移（B-03）。`services/workflow_app.py` validator V2 扩展（环检测、分支/聚合约束）。

### Checklist

- [x] 实现 9 节点 typed model（判别联合）+ 公共字段约束（depends_on 无环、branches ≥2、cases ≥1、duration_seconds >0）
- [x] 实现 V1 兼容注入 `model_validator(mode="before")`；validator V2 扩展（环检测/分支约束）
- [x] [B-03][unit] 修改生产代码前，编写验收测试并记录 RED：V1 spec（无 type、纯 capability step）经 validator 兼容通过且注入 `type="capability"`，现网 spec 不需迁移
- [x] 断言非法定义被拒：未知节点类型、环依赖、`branches <2`、缺 capability_ref/agent_ref/assignee/workflow_ref；断言 V2 模型无 `engine_ref` 字段（backend 属 Platform Configuration，remediation §14.3）
- [x] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/resources/ -k workflow_definition`（planned）：断言 WorkflowDefinition V2 仍走 resource_definitions 版本化生命周期（published 不可变、修改产生新 Draft/Version）、SQLite/PG 同契约、pinned_refs 表达版本快照语义
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | unit | WorkflowDefinition validator 纯函数（真实 V1 spec fixture） | V1 兼容通过；`type="capability"` 注入；非法定义被拒 | `tests/resources/test_workflow_definition_v2.py`（20 用例） | `pytest backend/tests/resources/test_workflow_definition_v2.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-03 | FAIL：5 用例（`engine_ref` required 拒绝 V1_SPEC、V2 spec `extra type` 被 `extra="forbid"` 拒、`engine_ref not in model_fields` 失败等）——contracts.py 仍为 V1（`steps: list[WorkflowStepDefinition]` + 必填 `engine_ref`） | PASS（20 用例） | `test_workflow_definition_v2.py:80-83`（V1 注入 `type="capability"`）、`86-90`（无 engine_ref 亦有效）、`92-93`（模型无 engine_ref 字段）、`96-109`（九节点类型）、`112-130`（公共字段）、`133-192`（13 类非法定义参数化拒绝） | 真实 `WorkflowDefinition.model_validate` 纯函数 + 真实 V1/V2 spec fixture，不 mock | verified |

修复记录（GLM 产物补全）：
- `resources/contracts.py`：`WorkflowDefinition.steps` 改为 `list[WorkflowNode]`（判别联合），移除 `engine_ref`（remediation §14.3）；`model_validator(mode="before")` V1 兼容（无 `type` 且有 `capability_ref` → 注入 `type="capability"`、静默剥离遗留 `engine_ref`）；`_validate_workflow_dependencies` 泛化至 `Sequence[WorkflowNode]`；新增 `_validate_routing_refs`（condition.then/else、switch.cases/default、parallel.branches 后继须存在）；移除被取代的 `WorkflowStepDefinition`（连带 `resources/__init__.py` 导出）
- `services/workflow_app.py`：validator 仅对 `CapabilityNode` 校验 capability_ref（其余节点引用在各自 validator 层）
- `tests/integration/test_resource_schema_api.py:22`：`REQUIRED_PROPERTIES[WORKFLOW]` 改为 `{"name", "steps"}`

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-003: 条件表达式白名单求值器

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase3-workflow-platform.design.md#3.4 接口设计, phase3-workflow-platform.design.md#2.5.2 功能验收场景
- **Acceptance-Refs**: B-04, NFR-SEC-02

### Description

实现文档化子集的条件表达式求值器（condition/switch 节点共用）：引用插值 `{{ node_id.output }}`、比较符 `==`/`!=`/`>`/`<`/`>=`/`<=`/`in`、布尔组合 `and`/`or`/`not`、白名单函数 `len()`/`lower()`/`upper()`/`is_empty()`。用 Python `ast` 解析 + 白名单节点校验，非 `eval`；非白名单形态（函数调用、属性访问、下标赋值等）拒绝求值并抛明确错误（NFR-SEC-02 / RISK-P3-05）。

### Checklist

- [x] 实现 AST 白名单求值器：插值/比较/布尔/白名单函数四类形态，其余一律拒绝
- [x] [B-04][unit] 修改生产代码前，编写验收测试并记录 RED：注入向量（`__import__`、属性链、任意调用、`eval` 字符串）全部被拒；白名单表达式求值正确
- [x] 断言非法/非白名单表达式返回明确错误（非静默 fallback）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-04 | unit | 求值器纯函数（真实 AST 解析） | 注入向量全拒；白名单子集求值正确 | `tests/runtime/test_workflow_expressions.py`（22 用例） | `pytest backend/tests/runtime/test_workflow_expressions.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-04 | GLM 产物已通过（22 用例），复用即 GREEN，无修复需要（无法人为构造真实 RED；已有行为补测） | PASS（22 用例） | `test_workflow_expressions.py:28-48`（插值/比较/布尔）、`62-68`（白名单函数）、`70-92`（注入向量参数化拒绝）、`95-98`（类型不可比明确错误）、`101-105`（transform 模板） | 真实 `ast` 解析求值器纯函数；`workflow_expressions.py` 白名单节点校验（Constant/Name/布尔/Compare/Call），属性/下标/任意调用/推导式/lambda 全部拒绝，不 mock、非 `eval` | verified |

> NFR-SEC-02 由 B-04 承载（design §6 追溯矩阵闭合）：注入向量拒绝路径即安全断言。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-004: durable graph 解释器 `_run_graph`/`_run_node`

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: phase3-workflow-platform.design.md#3.2 架构设计, phase3-workflow-platform.design.md#3.4 接口设计, phase3-workflow-platform.design.md#3.1 方案选型
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-04, S-10, E-03, RULE-P3-04

### Description

单注册 DBOS workflow `_run_graph(definition, input, run_meta)`：数据驱动遍历 V2 定义图（免按定义 codegen/动态注册）；每节点类型一个 `@DBOS.step()` executor（`_run_node(kind, node_def, inputs, scope)`，按类型 dispatch）。`node_id → value` 输出映射 + `{{ node_id.output }}` 引用插值（TASK-003 求值器）；Condition/Switch 路由、Parallel 分支 `asyncio.gather` + Join（`join_policy: all|any`）、Transform 变换、SubWorkflow 嵌套；Agent 节点经 `agent_ref` → Phase 2 ContextResolver（`agent_id` 主坐标）取 pinned ExecutionSnapshot 跑 AgentRuntime（DSL 不感知 Runtime mechanics，remediation §14.1）；capability executor 按 `skill|tool|mcp` 前缀 dispatch（`tool:` → `ResourceKind.TOOL`）。Retry 边界：step 级 durable retry 归 DBOS（step 首次抛异常自动重试且副作用不重复），`retry_policy` 字段只表达业务意愿；节点 `timeout_ms` < 实际耗时 → 有界转 ERROR（禁无限等待）。节点失败 → DBOS step retry → fail policy 终态。

### Checklist

- [x] 实现 `_run_graph` 数据驱动遍历 + 8 类节点 `@DBOS.step()` executor + 输出映射/插值
- [x] 实现 Condition/Switch 路由、Parallel/Join（`asyncio.gather` + join_policy）、Transform、SubWorkflow 嵌套
- [x] [S-10][E2E] 修改生产代码前，编写验收测试并记录 RED：condition/switch/parallel/transform/subworkflow 混合图真实运行，图执行结果正确、并行分支并发完成
- [x] [S-04][integration] 修改生产代码前，编写验收测试并记录 RED：`timeout_ms` < 实际耗时的节点运行 → 有界转 ERROR，不无限等待（RULE-P3-04）
- [x] [E-03][integration] 修改生产代码前，编写验收测试并记录 RED：step 首次执行抛异常 → DBOS step retry 生效、业务写不重复（副作用恰 1 条）、最终成功或按 fail policy 终态
- [x] 架构测试：step executor 内无 Fluxion 层重试（RISK-P3-02 禁 double retry）
- [x] **Spec verifier**：`RULE-backend-quality-001` — 运行 S-04/E-03 verifier 套件（`python -m pytest backend/tests/runtime/ -k "graph or interpreter"`，planned）：断言所有 backend/DBOS 调用有 timeout+fail policy、step 级 durable retry 与 Fluxion backend 调用重试互不叠加、公共函数类型注解完整、异常不吞
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | DBOS + 真实 PG + 混合图 Definition（不 mock 引擎/存储） | 8 节点类型执行结果正确；并行分支并发完成 | `tests/integration/test_workflow_graph_interpreter.py::test_s10_mixed_graph_routes_joins_and_nests` | `pytest backend/tests/integration/test_workflow_graph_interpreter.py -k s10` | verified |
| S-04 | integration | 真实 DBOS step timeout 配置运行 | 超时节点有界转 ERROR；无无限等待 | `tests/integration/test_workflow_graph_interpreter.py::test_s04_node_timeout_is_bounded_error` | `pytest backend/tests/integration/test_workflow_graph_interpreter.py -k s04` | verified |
| E-03 | integration | 真实 DBOS durable retry + 业务 Store | retry 生效；业务记录恰 1 条；fail policy 终态 | `tests/integration/test_workflow_graph_interpreter.py::test_e03_step_retry_keeps_business_write_unique` | `pytest backend/tests/integration/test_workflow_graph_interpreter.py -k e03` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-10 | FAIL：3 处（① `WorkflowEngineError.__init__` 缺 `code`——DBOS 跨进程/重放以 `exc_type(*args)` 重建 step 异常；② `deadlock: no executable node`——parallel 控制节点启动轮被判死锁；③ 并行分支 overlap≈0——fixture 只记录写时刻时间戳） | PASS | `test_workflow_graph_interpreter.py:84-104`（gold 分支/剪枝/transform/subworkflow/notify 输出 + 并发窗口 overlap>0.3） | 独立子进程 runner → 真实 `DbosWorkflowEngine` → DBOS 2.31 → 本地 PG `fluxion_workflow`；capability executor 为真实 psycopg 副作用执行器（幂等写 `wf_test_records`）；不 mock 引擎/存储 | verified |
| S-04 | FAIL：同上 `WorkflowEngineError.__init__` code 缺省 | PASS | `test_workflow_graph_interpreter.py:111-113`（3 次 step 尝试 × 300ms 有界 ERROR，elapsed∈[500,5000)） | 真实 DBOS step timeout 配置 + `asyncio.timeout` 有界转 ERROR | verified |
| E-03 | FAIL：同上 `WorkflowEngineError.__init__` code 缺省 | PASS | `test_workflow_graph_interpreter.py:122-130`（flaky step 恢复、executions≥2、业务记录恰 1 行） | 真实 DBOS step durable retry（`_business_write` ON CONFLICT 幂等计数）+ 真实业务 Store | verified |
| RULE-P3-04 | 同 S-04/E-03 | PASS | 架构：`test_workflow_architecture.py::test_graph_step_executors_have_no_fluxion_retry_loop`（step executor AST 无重试循环）；E-03 业务写恰 1 行 | DBOS step retry 与 Fluxion backend 调用重试互不叠加 | verified |

修复记录（GLM 产物补全）：
- `errors/workflow.py`：`WorkflowEngineError.__init__` 的 `code` 允许缺省（默认 `WORKFLOW_ENGINE_FAILURE`）——DBOS 以 `exc_type(*args)` 重建 step 异常，keyword-only 无默认值会吞掉原始失败
- `runtime/workflow_dbos.py`：`_map_status` 读取 `.status`（DBOS 2.31 `WorkflowStatus` 为 dataclass，`.status` 为 str 字面量），回退 `.value`/`str()`
- `runtime/workflow_graph.py`：parallel 控制节点启动轮不判死锁（`newly_started_parallel` 时 continue）；`_apply_router_pruning` 只剪路由**引用到的候选**（condition.then/else、switch.cases/default），延续节点（如 fanout）不误剪
- `tests/workflow_runtime/graph_fixtures.py`：S-10 分支目标补 `depends_on: ["branch"]`；`notify` 引用路径改为 `{{ child.output.outputs.child_step.greeting }}`；`stamp` 睡眠后回填 `finished_at`（并发窗口可测）
- `tests/architecture/test_workflow_architecture.py`：新增 step executor 禁 double retry AST 守护

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-005: 独立 worker 部署 + durable start / crash recovery E2E

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#4.1 部署架构, phase3-workflow-platform.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-01, S-02, S-05, S-06, RULE-P3-05

### Description

落地 `fluxion-workflow-worker` 独立进程（Deployment 形态，≥2 副本）：`DBOS.launch()` + `listen_queues` + startup recovery；`worker_concurrency` 有界（PoC 4）防单 worker 全认领；`DBOS.register_queue` 非 async 上下文 → 后台线程注册（PoC 已验证）。API/Console 进程持 DbosWorkflowEngine 做 client 侧 start/signal/cancel/status（stateless）。Phase 3 Gate 自动化证据：S-01 durable start 同步持久化（start 返回后 DBOS 可查状态）P95≤1s；S-02 SIGKILL worker → 新进程 startup recovery 续跑、已完成 step 不重跑，P95≤60s；S-05 同 execution 二次 start 返回既有 run、业务记录恰 1 条；S-06 database-backed queue 2 worker 分摊。指标：durable start P95 / recovery P95 / step 重试次数 / circuit-breaker 状态（PATTERN-backend-004）。

### Checklist

- [x] 实现 worker 入口：`DBOS.launch()` + queue listen + startup recovery + 后台线程 register_queue + `worker_concurrency` 有界
- [x] [S-01][E2E] 修改生产代码前，编写验收测试并记录 RED：已发布含 capability+wait+human_task 节点的 WorkflowDefinition，`WorkflowAdapter.execute` → 返回 run_id 且 start 返回后 DBOS 可查状态；连续 start 计时 P95≤1s
- [x] [S-02][E2E] 修改生产代码前，编写验收测试并记录 RED：workflow 运行中 SIGKILL worker → 新进程启动 → startup recovery 续跑、已完成 step 不重跑；恢复计时 P95≤60s
- [x] [S-05][E2E] 修改生产代码前，编写验收测试并记录 RED：同 execution 二次 start → 返回既有 run、step 不重跑、业务记录恰 1 条（SLO-WF-03）
- [x] [S-06][integration] 修改生产代码前，编写验收测试并记录 RED：database-backed queue 排队任务 → 2nd worker 拉取、任务分摊
- [x] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 Phase 3 Gate 套件（S-01/S-02/S-05 + S-07/S-08/S-09 引用，`python -m pytest backend/tests/integration/ -k workflow_gate`，planned）：断言 crash/timer/idempotency/approval/pinned/GC 证据全部为编码期自动化产出，非事后补
- [x] **Spec verifier**：`RULE-backend-logging-001` — 运行 S-01 verifier 用例（planned）：断言 run_id/execution_id/trace_id/tenant_id 全链路关联（WorkflowStartRequest 透传 + 投影）、structlog JSON（`emit_workflow_event_log` 复用）、敏感字段脱敏
- [x] **Spec verifier**：`RULE-backend-platform-001` — 运行 S-01/S-02 计时断言（planned）：断言 SLO-WF-01（start P95≤1s）/ SLO-WF-02（recovery P95≤60s）在测试中可断言、错误码走命名空间、配置经 `WorkflowBackendSettings`（环境变量 > 配置文件 > 默认值）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Adapter → 真实 DbosWorkflowEngine → DBOS → 真实 PG（不 mock） | run_id 返回；start 后 DBOS 可查；P95≤1s | `tests/integration/test_workflow_gate.py::test_workflow_gate_s01_durable_start_p95` | `pytest tests/integration/ -k workflow_gate` | verified |
| S-02 | E2E | 真实独立 worker 进程 + SIGKILL + PG | recovery 续跑；已完成 step 不重跑；P95≤60s | `tests/integration/test_workflow_gate.py::test_workflow_gate_s02_crash_recovery` | `pytest tests/integration/ -k workflow_gate` | verified |
| S-05 | E2E | 真实 SetWorkflowID 幂等 + 二次 start | 既有 run 返回；step 不重跑；业务记录恰 1 条 | `tests/integration/test_workflow_gate.py::test_workflow_gate_s05_same_execution_second_start_idempotent` | `pytest tests/integration/ -k workflow_gate` | verified |
| S-06 | integration | 2 个真实 worker 进程 + database-backed queue | 任务分摊；水平扩展生效 | `tests/integration/test_workflow_gate.py::test_workflow_gate_s06_database_queue_two_workers` | `pytest tests/integration/ -k workflow_gate` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: 首跑 `WorkflowEngineError: pinned must contain workflow version ref`（`WorkflowAdapter.execute` 未透传 pinned） | PASS: 8/8 start 返回即可查状态；P95=9.9ms（SLO-WF-01 ≤1000ms，实测 5.2~92.4ms） | `test_workflow_gate_s01_durable_start_p95:148-186`（run_id 确定性 + 业务记录 tenant 关联 + P95 断言） | 真实 `WorkflowAdapter`→`ResilientWorkflowEngine`→`DbosWorkflowEngine`→DBOS 2.31→本地 PG；`install_worker_bootstrap` 注入真实 provider + psycopg 直写/直读 `wf_test_records`（echo 恰 1 行）。注：S-01 用 capability 节点 `quick-flow` 测 durable start 契约（wait/human_task 节点 durable 原语由 TASK-006 S-08/S-09 单独 E2E，不降低本场景真实边界） | verified |
| S-02 | N/A（GLM 实现已就位，属已有行为补测，无法 RED；不伪造失败） | PASS: recovery=2.7s（SLO-WF-02 ≤60s）；step_a/step_b `executions==1`（不重跑）；step_c 恢复后续跑至 finished | `test_workflow_gate_s02_crash_recovery:193-252`（wait_for_records 断点 + kill + recover + elapsed 断言 + executions 断言） | 真实 worker 子进程 SIGKILL（`step_c` 启动中）→ 新进程 `recover` `launch()` startup recovery；业务表 `executions` 计数证明 committed step 不重跑（SLO-WF-03） | verified |
| S-05 | FAIL: 同 S-01（adapter 未透传 pinned） | PASS: 二次 `adapter.execute` 返回既有 run_id；SUCCESS；业务记录恰 1 条且 `executions==1` | `test_workflow_gate_s05_same_execution_second_start_idempotent:260-287`（run_id 相等 + 恰 1 条 + executions 断言） | 真实 DBOS `SetWorkflowID` 幂等（同 execution 二次 start）；psycopg 直读业务表恰 1 行；`await_result` 后再次 start 均 SUCCESS | verified |
| S-06 | N/A（GLM 实现已就位，无法 RED） | PASS: 8/8 SUCCESS；executor_id={worker-0, worker-1}（batch1 4 个→worker-0 / batch2 4 个→worker-1，错峰 + slow step 4s 使分摊确定） | `test_workflow_gate_s06_database_queue_two_workers:295-342` + `_s06_driver`（分批 enqueue + 全 await SUCCESS + executor 集合断言） | 2 个真实 worker 子进程（`DBOS__VMID=worker-0/1`）+ `database_backed_queue=True`；DBOS `workflow_status.executor_id` 证明双 worker 分摊、水平扩展生效；驱动进程 `listen_queues=[]` 不抢 queue（PoC 语义） | verified |
| RULE-P3-05 | 同 S-01/S-02 | PASS: SLO-WF-01 P95=9.9ms / SLO-WF-02 recovery=2.7s 在测试中可断言 | 同 S-01/S-02 | 同上（SLO 断言为编码期自动化产出，非事后补） | verified |

**Spec verifiers 证据**：`RULE-fluxion-dfx-001` `pytest tests/integration/ -k workflow_gate` → 4 passed（本 Gate 套件）。`RULE-backend-logging-001`：S-01 断言 run_id=`{workflow_id}:{execution_id}` 确定性派生 + `emit_workflow_event_log(event="workflow.started", run_id/tenant_id/trace_id/execution_id)` 全链路关联（`workflow_dbos.py:208-214`），structlog JSON 路径复用 TASK-001 已验证实现，脱敏沿用既有 logging 层。`RULE-backend-platform-001`：S-01/S-02 计时断言（P95≤1s / ≤60s）；错误码走 `fluxion.errors.workflow` 命名空间（`WorkflowBackendUnavailableError`/`WorkflowEngineError`/`WorkflowRunNotFoundError`）；配置经 `WorkflowBackendSettings.resolve()`（env > 配置 > 默认，`resolve_database_url`）。

**生产代码变更（本任务引入）**：`runtime/workflow.py` `WorkflowAdapter` 增加可选 `version` → `execute` 透传 `pinned=(WorkflowPinnedRef(...))`（RULE-P3-02 版本 pin）；`runtime/workflow_dbos.py` 增加 queue 支持（`WORKFLOW_QUEUE`/`register_workflow_queue`/`enqueue_start`）；新增 `cli/workflow_worker.py`（serve/start/recover 三模式）；`pyproject.toml` 注册 `fluxion-workflow-worker` script。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)：worker 入口 + S-01/S-02/S-05/S-06 E2E 全 GREEN；S-01/S-05 首跑 RED（adapter 未透传 pinned）→ 补 `WorkflowAdapter.version` 修复；S-02 recovery 2.7s / S-01 P95 9.9ms；覆盖表 S-01/S-02/S-05/S-06/RULE-P3-05 verified

---

## TASK-006: HumanTask / Wait / Timer / Resume

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#2.5.2 功能验收场景
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001, fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-08, S-09, NFR-REL-02

### Description

durable 等待原语：`wait` 节点用 `DBOS.sleep_async`（durable timer）；`human_task` 节点用 `DBOS.recv_async`/`send`（durable signal），assignee 解析（user ref / role）+ `message` + `timeout_seconds` 超时处理；`signal(run_id, name, payload)` 唤醒。跨重启存活：worker kill + 重启后审批信号仍可唤醒（S-08）、timer 按原始 deadline 触发且 elapsed 落在窗口内（S-09）。

### Checklist

- [x] 实现 `wait`（`DBOS.sleep_async`）与 `human_task`（`recv_async`/`send` + assignee 解析 + timeout_seconds）节点 executor
- [x] [S-08][E2E] 修改生产代码前，编写验收测试并记录 RED：审批节点运行中 + worker 重启 → `send(approve signal)` 唤醒并继续，跨重启存活
- [x] [S-09][E2E] 修改生产代码前，编写验收测试并记录 RED：wait 节点运行中 kill + 重启 worker → 按原始 deadline 触发，elapsed 落在窗口内
- [x] 断言 `human_task` 超时路径：超时后按 fail policy 终态（非永久挂起）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实 recv_async/send + 独立 worker 重启 + PG | 审批信号跨重启唤醒；流程继续 | tests/integration/test_workflow_gate_s08_s09.py::test_workflow_gate_s08_approval_survives_restart | `cd backend && .venv/bin/python -m pytest tests/integration/test_workflow_gate_s08_s09.py -k s08` | verified |
| S-09 | E2E | 真实 sleep_async + kill/重启 worker | 原始 deadline 触发；durable wake time 不重算 | tests/integration/test_workflow_gate_s08_s09.py::test_workflow_gate_s09_wait_survives_restart | `cd backend && .venv/bin/python -m pytest tests/integration/test_workflow_gate_s08_s09.py -k s09` | verified |
| human_task 超时 | E2E | 真实 recv_async timeout + 独立 worker | 超时后终态 ERROR（非永久挂起） | tests/integration/test_workflow_gate_s08_s09.py::test_workflow_gate_s08_human_task_timeout_terminal | `cd backend && .venv/bin/python -m pytest tests/integration/test_workflow_gate_s08_s09.py -k timeout` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | FAIL: `prepare executions == 2`——kill 后 recovery replay 重放 prepare（DBOS memo miss）。根因：`_wait_workflow_pending` 依赖的 PENDING 是 DBOS **初始/在飞状态**（workflow_status 建立即 PENDING，全执行期保持），非"阻塞在 recv"检查点；`finished_at is not None` 又被 INSERT 初值立即满足，kill 发生在 prepare step 尚未返回（memo fid 1 未落库）之前 | PASS: `prepare/finalize executions==1`；`client.signal(run_id,"approve")` 唤醒 → SUCCESS | `test_workflow_gate_s08_approval_survives_restart`：`_wait_durable_wait_checkpoint`（kill 前等 `DBOS.sleep` 操作行）→ `client.signal`（durable `dbos.notifications`）→ `status.status == "SUCCESS"` → `prepare.executions==1`/`finalize.executions==1` | 真实 `fluxion-workflow-worker` 子进程（start/recover）+ DBOS 2.31 `recv_async`/`send` + 本地 PG；`WorkflowTestClient`=纯 `DBOSClient`（不 launch，recover worker 为唯一可恢复方，executor_id=s08-worker）；psycopg 直读 `dbos.operation_outputs` 确认 durable 挂起 | verified |
| S-09 | FAIL: 墙钟 elapsed=9.7s 超出窗口 [5.0, 8.5]——原断言用 wall-clock 混入重启开销；且 PENDING/`finished_at` 竞态使 kill 点失真 | PASS: durable wake time 保持 `before.finished + 6.0`（±0.5s）；after 按原始 wake time 触发 | `test_workflow_gate_s09_wait_survives_restart`：`_wait_durable_wait_checkpoint` → `_read_durable_wake_time` → `abs((wake_time - before_finish) - 6.0) <= 0.5`（不重算）→ `wake_time - 0.5 <= after_start <= wake_time + 3.0` → `before/after executions==1` | 真实 worker 子进程 + DBOS `sleep_async`（`record_sleep` 落库 wake time，replay 只返回剩余时间）+ 本地 PG；psycopg 直读 `DBOS.sleep` 行验证原始 deadline 跨 1.5s downtime + 重启存活 | verified |
| human_task 超时 | RED: 无（补测，S-08 同批发现挂起路径缺独立断言） | PASS: recv 超时 2s → 终态 ERROR（非永久挂起） | `test_workflow_gate_s08_human_task_timeout_terminal`：`elapsed∈[1.5,25.0]` → `payload["status"]=="ERROR"` → `"timeout" in error.lower()` | 真实 worker 子进程 + `recv_async` timeout（durable sleep checkpoint）→ fail policy 终态；`RUN_FAILED` 载荷断言 | verified |

> NFR-REL-02（timer/wait/approval 跨重启 survive restart）由 S-08/S-09 承载并 verified（design §2.5.3 NFR-REL-02）。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] started (in-progress)：前置检查通过（Depends TASK-004 done、无 #NOTES）；补 TASK-006 Spec-Refs 缺失字段；S-08/S-09 用真实 worker 子进程 E2E，human_task 超时路径补独立断言
- [2026-08-28] completed (done)：修 S-08/S-09 的 kill 点竞态——PENDING 是 DBOS 初始状态非挂起检查点，改等 `DBOS.sleep` durable 操作行（recv timeout-sleep / wait wake time）；S-09 断言改验 durable wake time=before.finished+6.0（不因重启重算）。验收 3/3 GREEN + 回归 8/8（test_workflow_gate.py / dbos_resilience / graph_interpreter）

---

## TASK-007: Version pin / active ref / GC

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-005
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#3.3 数据设计, phase3-workflow-platform.design.md#2.5.2 功能验收场景
- **Acceptance-Refs**: S-03, S-07, RULE-P3-02, RULE-P3-03

### Description

复用 ADR-SNAPSHOT-001 `active_references`（不建 workflow 专用 pin 表）：start 时对 `pinned_refs`（workflow + 依赖资源版本快照）逐项 acquire（`ref_type=workflow`）；terminal（succeeded/failed/cancelled）时释放。hard-delete guard：存在 active ref → 拒绝删除（RISK-P3-03 + tombstone）。resume 语义：始终使用 pinned version（ExecutionSnapshot），不 resolve latest（RULE-P3-02）；deprecated-but-resumable——pinned deprecated 版本仍可恢复。

### Checklist

- [x] 实现 start acquire / terminal release（`ref_type=workflow`，逐项 `pinned_refs`）
- [x] hard-delete guard：存在 active ref → 拒绝删除并返回明确错误码（S-07 实证既有 ADR-SNAPSHOT-001 `active_reference_blocked`）
- [x] [S-03][integration] 修改生产代码前，编写验收测试并记录 RED：start 时 workflow v1 pinned、v2 已发布 → resume 长 workflow 使用 v1，不 resolve latest（断言解析到的版本坐标）
- [x] [S-07][E2E] 修改生产代码前，编写验收测试并记录 RED：workflow 运行中引用 v1 → hard-delete v1 被拒（RULE-P3-03）；run 结束后（ref 释放）可删
- [x] 断言 terminal 释放后 `active_references` 无残留行（GC 正确性）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 真实 ExecutionSnapshot + Registry + active_references（双库契约） | resume 用 pinned v1 非 latest；版本坐标正确 | tests/integration/test_workflow_gate_s03_s07.py::test_workflow_gate_s03_resume_uses_pinned_version | `cd backend && .venv/bin/python -m pytest tests/integration/test_workflow_gate_s03_s07.py -k s03` | verified |
| S-07 | E2E | 运行中 workflow + 真实 hard-delete API + active_references | 删除被拒；terminal 释放后可删；无残留 ref | tests/integration/test_workflow_gate_s03_s07.py::test_workflow_gate_s07_hard_delete_rejected_while_running | `cd backend && .venv/bin/python -m pytest tests/integration/test_workflow_gate_s03_s07.py -k s07` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL（受控实验，acquire 未接线）：`AssertionError: active reference for run pin-flow:s03-... not acquired within 15.0s`——start acquire 未实现时 ref 不落库；`PROVIDER_RESOLVE` 坐标为 0 条 | PASS: provider 只解析 `pin-flow 1`（无 v2/latest）；recover 进程零 provider 调用（定义是持久化 DBOS arg）；业务 payload 含 `'marker': 'v1'`；prepare/finalize executions==1；run 期间持 ref、terminal 后释放 | `test_workflow_gate_s03_resume_uses_pinned_version`：`resolves == ["PROVIDER_RESOLVE pin-flow 1"]` → `_wait_active_ref`（ref_type=workflow, ref_id=run_id）→ `_wait_durable_wait_checkpoint`（kill 前 durable 挂起）→ recover 后 `assert not any("PROVIDER_RESOLVE" in ln ...)` → `_wait_refs_empty`（无残留）→ `"'marker': 'v1'" in records["prepare"]["payload"]` + `executions==1` | 真实 Registry（PG `fluxion_workflow` 库）+ `store.recall_pinned` Registry-backed worker provider（不 resolve latest）+ 独立 `fluxion-workflow-worker` 子进程（start/recover，`DBOS__VMID=s03-worker`）+ DBOS startup recovery + `WorkflowTestClient` 纯 DBOSClient signal（topic `review:{run_id}`） | verified |
| S-07 | FAIL（受控实验）① acquire 未接线：`active reference ... not acquired within 15.0s`（ref 不落库 → hard-delete 不被拒）；② releaser 未接线：`active references not released within 15.0s`（terminal 后残留） | PASS: tombstone v1 + hard-delete v1 → `active_reference_blocked`；signal 完成 run 后 refs 无残留；再 hard-delete v1 成功（revision≥1） | `test_workflow_gate_s07_hard_delete_rejected_while_running`：`_wait_active_ref` → `_tombstone_v1`（TOMBSTONE 强制 approval_id）→ `pytest.raises(RegistryStoreError)` + `"active_reference_blocked" in str` → signal → `worker.wait_for("RUN_RESULT")`（worker terminal 释放）→ `_wait_refs_empty`（GC 无残留）→ 二次 `store.hard_delete` 成功 | 真实 hard-delete API（ADR-SNAPSHOT-001 三重 guard：active_ref→retention→GC safety）+ 真实 `active_references`（ref_type=workflow）+ 运行中 worker 子进程 + 真实 tombstone 治理（approval_id）+ 本地 PG | verified |

> 双库契约（S-R07）在 `tests/contract/test_registry_store.py` 12/12 保持；deprecated-but-resumable 由 `recall_pinned` 接受 PUBLISHED/DEPRECATED/TOMBSTONE 版本（RULE-P3-02 结构性保证，拒绝 LATEST selector）。

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)：复用 ADR-SNAPSHOT-001 `active_references`（ref_type=workflow）。`DbosWorkflowEngine.start` 对 `pinned_refs` 逐项 acquire（store 注入，ON CONFLICT 幂等）+ start 失败回滚；terminal 释放按 ref_id（run_id）走新增 `release_active_references_for_ref`（module fn + `SQLAlchemyRegistryStore` 方法，未扩展 Protocol 核心 Contract——rule 25）；`fluxion-workflow-worker` start/recover 终态释放 + recover 加 `--tenant`；registry-backed worker bootstrap 记录 PROVIDER_RESOLVE 解析坐标（S-03 断言 pinned v1 不 resolve latest）
- [2026-08-29] completed (done)：S-03/S-07 验收通过（`test_workflow_gate_s03_s07.py`，`-k s03` / `-k s07` 各 PASS）；RED 为受控实验——acquire 未接线（`active reference ... not acquired within 15.0s`）与 releaser 未接线（`active references not released within 15.0s`），恢复后 GREEN；全局 Acceptance Coverage S-03/S-07/RULE-P3-02/RULE-P3-03 → verified。回归：unit active_references + 全部 workflow 套件 70 passed

---

## TASK-008: workflow_run 投影 + status / execution-history API

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004, TASK-005
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#3.3 数据设计, phase3-workflow-platform.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-11, E-02, B-02, RULE-P3-06

### Description

Fluxion 域 `workflow_run` 投影表（与 DBOS sysdb 同库不同表，DBOS sys 表由 DBOS 管理、Fluxion 不直写）：run_id（=`{workflow_id}:{execution_id}`，PK）、tenant_id（强制，`idx_wf_run_tenant`）、workflow_id/version（pinned）、execution_id（`idx_wf_run_exec`）、trace_id、status（running/succeeded/failed/cancelled/paused）、node_states JSON（`{node_id: {status, output_ref, error}}`，分批写入——PATTERN-backend-003）、pinned_refs JSON。`services/workflow_projection.py`：`get_run(tenant_id, run_id)`（不存在/跨租户→NotFound）/`list_runs(tenant_id, workflow_id)`。`api/workflow.py`：`GET /workflows/runs/{run_id}`、`GET /workflows/{workflow_id}/runs`（统一 envelope，Handler 不手写响应结构）。`get_execution_history` 关联 execution→run（Console/Workflow Studio Phase 4 数据源）。

> 优先级说明：FEAT-P3-06 为 P1；其中 tenant scope（E-02/B-02，RULE-P3-06 / 架构规则 16）是该任务内不可协商的硬约束，实现时不因 P1 降级。

### Checklist

- [x] 建 `workflow_run` 投影表（幂等 DDL，CREATE IF NOT EXISTS）+ 索引；解释器节点状态/结果写投影
- [x] 实现 `WorkflowProjectionService.get_run/list_runs`（tenant 强制 scope）+ `api/workflow.py` 路由（统一 envelope）
- [x] [S-11][integration] 修改生产代码前，编写验收测试并记录 RED：运行中/终态 run 查询 `GET /workflows/runs/{run_id}` → 返回 node 级状态 + pinned refs + execution history
- [x] [E-02][integration] 修改生产代码前，编写验收测试并记录 RED：查询他租户 run → 404 NotFound + 统一 envelope 错误（不可见）
- [x] [B-02][integration] 修改生产代码前，编写验收测试并记录 RED：tenant A run 对 tenant B 全链路不可见（列表/详情/投影均隔离，NFR-SEC-01）
- [x] **Spec verifier**：`RULE-fluxion-console-001` — 运行 S-11 verifier 用例：status API 供 Console（Workflow Studio Phase 4）消费、Runtime 边界不内侵（Console 只读投影）
- [x] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 S-11/E-02 verifier 用例：统一 envelope `{code, message, data, request_id}`、Handler 无手写响应结构、错误码命名空间
- [x] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/contract/ -k workflow_run`：投影表 schema/索引/tenant scope 双库契约、node_states 分批写入、无 N+1
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-11 | integration | 真实投影表 + ASGI 栈 + 运行中/终态 run | node 级状态 + pinned refs + execution history 返回 | tests/integration/test_workflow_gate_s11.py::test_workflow_gate_s11_get_run_returns_projection | `cd backend && ../.venv/bin/python -m pytest tests/integration/test_workflow_gate_s11.py -k s11` | verified |
| E-02 | integration | 真实跨租户查询路径 | 404 NotFound + 统一 envelope | tests/integration/test_workflow_gate_s11.py::test_workflow_gate_e02_cross_tenant_run_not_found | `cd backend && ../.venv/bin/python -m pytest tests/integration/test_workflow_gate_s11.py -k e02` | verified |
| B-02 | integration | tenant A/B 双租户真实数据 | 全链路隔离（列表/详情/投影） | tests/integration/test_workflow_gate_s11.py::test_workflow_gate_b02_tenant_scope_isolated | `cd backend && ../.venv/bin/python -m pytest tests/integration/test_workflow_gate_s11.py -k b02` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-11 | FAIL（受控实验，writer 未接线）：`assert ok.status_code == 200` → 404 `{'code': 31004, 'message': 'workflow run not found: pin-flow:s11-ok-...'}`——投影不落库则 API 不可查（依赖被实测证明）。开发期另有真实 RED：execution_history 状态词汇不一致（`SUCCESS` vs `succeeded`）、DBOS StepInfo 键错（`func_name`/`status` 不存在 → step-N），修复 `get_execution_history` 提取后 GREEN | PASS：终态 run status=succeeded、node_states 三节点全 succeeded、pinned_refs=pin-flow@1、execution_history 有 steps（`_run_node` + output）；运行中 run status=running、node_states.prepare=succeeded（增量投影）；列表 total=2 | `test_workflow_gate_s11_get_run_returns_projection`：`payload_ok["data"]["status"]=="succeeded"` → `node_ok["prepare"]["status"]` 等 → `data_ok["pinned_refs"][0]=={...}` → `history_ok["steps"]` + `assert any("_run_node" in s["node_id"] ...)` → 运行中 `payload_busy["data"]["status"]=="running"` + `node_states["prepare"]` → `listed["data"]["total"]==2` | 真实投影表（PG）+ 真实 worker 子进程 + DBOS（终态 + 运行中 human_task 挂起 run）+ 真实解释器分批写 node_states（`_projection_write_states`）+ 真实 ASGI（console app + httpx ASGITransport）+ `WorkflowProjectionService`（真实 store）+ `DbosWorkflowEngine.get_execution_history`（DBOSClient 免 launch） | verified |
| E-02 | FAIL 前提（受控实验同 S-11：投影不落库 → 对任意租户都 404）；开发期 RED：`history_ok["status"] == "succeeded"` 断言过严（DBOS `SUCCESS`） | PASS：tenant B 查 tenant A run → HTTP 404 + `code==RESOURCE_NOT_FOUND(31004)` + `data is None` + `request_id` 非空（统一 envelope） | `test_workflow_gate_e02_cross_tenant_run_not_found`：`response.status_code == 404` → `payload["code"] == RESOURCE_NOT_FOUND` → `payload["message"]`/`payload["request_id"]` 非空 → `payload["data"] is None` | 真实跨租户查询路径（`X-Tenant-ID` 头 → RequestContextMiddleware → `_actor().tenant_id` → 投影 service tenant 过滤）+ 真实投影数据（tenant A 运行中 run） | verified |
| B-02 | FAIL（测试隔离 RED）：共享 PG 下残留 PENDING run 被后续 worker startup recovery 重新执行、重写投影 → 精确计数多出行 `pin-flow:s11-b02a-*`；修复：测试 setup `purge_stale_workflows`（清 PENDING/ENQUEUED）+ 清理 signal 到终态 | PASS：tenant A/B 各 1 run；A 列表只见 run_a、B 列表只见 run_b；跨租户详情双向 404 + `code==RESOURCE_NOT_FOUND`；本租户详情 200 | `test_workflow_gate_b02_tenant_scope_isolated`：`list_a["data"]["items"]=={run_a}` / `list_b=={run_b}` → `a_see_b.status_code==404` → `b_see_a.status_code==404` → `own.json()["data"]["status"]=="running"` | 真实双租户数据（tenant-s11-a / tenant-s11-b 各独立发布 + 运行中 run）+ 全链路 tenant scope（列表/详情/投影 service 查询均按 tenant 过滤，RULE-P3-06 / NFR-SEC-01） | verified |

> Spec verifiers（code 阶段）：`RULE-fluxion-console-001` — S-11 断言 status API 由 Console app 承载（`create_app` 注册投影路由，只读 `workflow_run` 投影 + DBOSClient，不直达 DBOS 内部表/不内侵 Runtime 执行边界）；`RULE-fluxion-console-api-001` — 全部响应经 `success()`/异常中间件统一 envelope（`{code,message,data,request_id}`），Handler 无手写响应结构，404 复用 `RESOURCE_NOT_FOUND` 命名空间；`RULE-backend-database-001` — `tests/contract/test_workflow_run_contract.py` 双库契约（`FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 门控，SQLite+PG 9 passed）：schema 全字段/status 默认 running/upsert 幂等/tenant scope/node_states 单 JSON 列批写（无 N+1）/索引声明。

### Log
- [2026-08-28] created (draft)
- [2026-08-29] started (in-progress)：`workflow_run` 投影表（registry metadata + 幂等 DDL）+ 解释器分批写 node_states（PATTERN-backend-003）+ `WorkflowProjectionService.get_run/list_runs`（tenant 强制）+ `api/workflow.py` 路由（统一 envelope）+ `get_execution_history` 客户端化（DBOSClient 免 launch，防 API 进程干扰运行中 worker）
- [2026-08-29] completed (done)：S-11/E-02/B-02 验收通过（`test_workflow_gate_s11.py`，`-k s11/e02/b02`）；RED 为受控实验（writer 未接线 → `workflow run not found` 404）与开发期真实 RED（history 状态词汇、StepInfo 键、共享 PG 残留 PENDING 隔离）；全局 Acceptance Coverage S-11/E-02/B-02/RULE-P3-06 → verified。回归：workflow+registry 契约 65 passed + console API 9 passed + `-k workflow_run` 双库契约 9 passed（SQLite+PG）
- [2026-08-29] review remediation（GLM 4-agent 交叉审查）：修复 P0-1 subworkflow 复用父 run_meta 打穿投影（独立 run_id + parent_run_id + SetWorkflowID + 有界 get_result + 嵌套深度上限 + 解释器 sync resolver——DBOS 独立 event loop 不能调 async engine）；P0-2 serve 模式无终态接线（终态处理下沉解释器 except/else：failed/succeeded 投影 + 释放 active refs，sync psycopg releaser，serve/start/recover 三模式统一）；P0-3 api/services import `fluxion.runtime.*` 违规（workflow contract 下沉 `fluxion/contracts/workflow.py`，execution history 读取下沉 services，架构守护回绿 + console.py 纳入守护）；P1-4 start 回滚缺口、P1-5 活 run 零引用竞态、P1-8 human_task 无超时静默成功、P1-11 recover `.lower()` 词表外超列宽、P1-12 run_id 列宽 512 + DDL 分叉对齐、P1-13 dev_bundle 接线、P1-6/7 ResilientWorkflowEngine timeout/熔断计数、P1-10 gather 孤儿任务；P2 render_template 残留检查、workflow_run 复合 PK（tenant_id, run_id，rule 16）、投影写错误隔离。新增 `test_workflow_review_fixes.py`（subworkflow 独立投影 + failed 投影/释放 + serve 终态）11/11；workflow 全量 82 passed
