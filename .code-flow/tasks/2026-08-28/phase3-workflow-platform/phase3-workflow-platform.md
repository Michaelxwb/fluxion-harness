# Tasks: Phase 3 Workflow Platform（未落地部分）

- **Source**: `.code-flow/tasks/2026-08-28/phase3-workflow-platform/phase3-workflow-platform.design.md`
- **Created**: 2026-08-28
- **Updated**: 2026-08-28

## Proposal

以 ADR-013 锁定的 DBOS 生产化替换 `StubWorkflowEngine`：实现 `DbosWorkflowEngine`（`WorkflowEngine` Protocol 7 成员 + `pinned refs`），将 WorkflowDefinition 扩展为 9 节点判别联合（V1 零迁移兼容），落地通用 durable graph 解释器 `_run_graph`、HumanTask/Wait/Timer durable 原语、Version pin/active ref/GC 守卫与 `workflow_run` 投影 API，并以独立 `fluxion-workflow-worker` 承载执行。最终闭合 Phase 3 Gate：durable start P95≤1s、worker 崩溃恢复 P95≤60s、committed 副作用重复=0、timer/wait/approval 跨重启存活、pinned 版本 hard-delete 被拒。

依据 design §2.4 前置：ADR-013 vendor pick + PoC 证据（`tests/workflow_poc/` 11/11）已落地不重复设计；本文档只做生产化。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | Adapter → DbosWorkflowEngine → DBOS → 真实 PG | TASK-005 | planned |
| S-02 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | 独立 worker 进程 + PG（SIGKILL → 新进程 recovery） | TASK-005 | planned |
| S-03 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | ExecutionSnapshot + Registry + active_references | TASK-007 | planned |
| S-04 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | DBOS step timeout 配置（真实运行） | TASK-004 | planned |
| S-05 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | DBOS SetWorkflowID 幂等（真实二次 start） | TASK-005 | planned |
| S-06 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | database-backed queue + 2 worker 进程 | TASK-005 | planned |
| S-07 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | active_references ref_type=workflow（运行中 hard-delete） | TASK-007 | planned |
| S-08 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | DBOS recv_async/send + worker 重启 | TASK-006 | planned |
| S-09 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | DBOS.sleep_async + kill/重启 | TASK-006 | planned |
| S-10 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | E2E | 解释器遍历 8 节点类型混合图 | TASK-004 | planned |
| S-11 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | workflow_run 投影表 + status API（真实 ASGI 栈） | TASK-008 | planned |
| E-01 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | ResilientWorkflowEngine + circuit breaker（真实 DBOS 宕机） | TASK-001 | planned |
| E-02 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | tenant scope（他租户查询） | TASK-008 | planned |
| E-03 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | DBOS durable retry（step 首次抛异常） | TASK-004 | planned |
| B-01 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | unit | `runtime/workflow.py` WorkflowAdapter 不变量 | TASK-001 | planned |
| B-02 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | integration | tenant scope（tenant A run / tenant B 查询） | TASK-008 | planned |
| B-03 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | unit | WorkflowDefinition V2 validator（V1 spec 输入） | TASK-002 | planned |
| B-04 | phase3-workflow-platform.design.md#2.5.2 功能验收场景 | unit | 条件表达式求值器（注入输入） | TASK-003 | planned |
| RULE-P3-01 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | unit | 同 B-01 | TASK-001 | planned |
| RULE-P3-02 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | integration | 同 S-03 | TASK-007 | planned |
| RULE-P3-03 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | E2E | 同 S-07 | TASK-007 | planned |
| RULE-P3-04 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | integration | 同 S-04 / E-03 | TASK-004 | planned |
| RULE-P3-05 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | E2E | 同 S-01 / S-02 | TASK-005 | planned |
| RULE-P3-06 | phase3-workflow-platform.design.md#2.5.1 业务规则与约束 | integration | 同 B-02 | TASK-008 | planned |

> NFR-PERF-01/02/03（SLO-WF-01/02/03）由 S-01/S-02/S-05 承载（TASK-005）；NFR-REL-01/02 由 S-02/S-08/S-09 承载；NFR-SEC-01 由 E-02/B-02 承载（TASK-008）；NFR-SEC-02 由 B-04 承载（TASK-003）；NFR-OBS-01 由 TASK-005 logging verifier 承载。design §6 追溯矩阵闭合无断点。

---

## TASK-001: DbosWorkflowEngine 生产实现 + Protocol 扩展

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#2.3.2 字段约束, phase3-workflow-platform.design.md#3.1 方案选型, phase3-workflow-platform.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001, fluxion-runtime-core#RULE-fluxion-runtime-001, backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: B-01, E-01, RULE-P3-01

### Description

`dbos` 依赖从 PoC `.venv` 直装正式声明进 `pyproject.toml` + `uv.lock`（2.31）。新建 `runtime/workflow_dbos.py`（与 Protocol 同包，镜像 RegistryStore adapter 同包模式）：`DbosWorkflowEngine` 实现 `WorkflowEngine` Protocol 全部成员——既有 `start`/`resume`/`signal`/`cancel(timeout)`/`get_status`，新增 `await_result(run_id, *, timeout)`（有限等待，超时 `TimeoutError`）与 `get_execution_history(run_id)`。`WorkflowStartRequest` 增加 `pinned: WorkflowPinnedRefs`（`{kind, id, version}` 快照）。start 用 SetWorkflowID 幂等；查询/信号类 DBOS API 统一 `asyncio.to_thread`（RISK-P3-04）；全成员定义 timeout + fail policy（规则 18），禁 double retry。B-01 恒等断言 `WorkflowAdapter.local_durable_state_count==0`（Runtime 无 durable state，rule 13）。

### Checklist

- [ ] `dbos` 声明进 `pyproject.toml` + `uv.lock`；`WorkflowStartRequest` 增加 `pinned: WorkflowPinnedRefs`
- [ ] 实现 `DbosWorkflowEngine` 7 成员（含 `await_result`/`get_execution_history`）：SetWorkflowID 幂等、查询/信号类走 `asyncio.to_thread`、全成员 timeout + fail policy
- [ ] [B-01][unit] 修改生产代码前，编写验收测试并记录 RED：断言 `WorkflowAdapter.local_durable_state_count` 恒等 0、`runtime/` 包无 DBOS durable state 落地（RULE-P3-01）
- [ ] [E-01][integration] 修改生产代码前，编写验收测试并记录 RED：真实 DBOS backend 宕机 → ResilientWorkflowEngine 熔断快速失败（非 hang），N 次失败后 open、cooldown 后试探，返回明确错误码
- [ ] 架构测试：`runtime/workflow_dbos.py` 归属 runtime 包、Kernel 不依赖 DBOS 具体 SDK（Kernel 只依赖 Contract）、`services/workflow_projection.py`/`api/workflow.py` 包边界（directory rule 落点）
- [ ] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 `python -m pytest backend/tests/runtime/ -k workflow`（planned）：断言 Tool=Adapter 边界（Agent 经 WorkflowAdapter 而非直连引擎）、durable state 归 Workflow Engine、capability 节点复用 Capability Contract
- [ ] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 B-01 + E-01 verifier 套件（planned）：断言 Runtime 无状态（B-01 恒等 0）、durable state 不进 Agent Runtime、Kernel 只依赖 Contract
- [ ] **Spec verifier**：`RULE-backend-directory-001` — 运行 `python -m pytest backend/tests/architecture/ -k workflow`（planned，AST 守护）：断言 `runtime/workflow_dbos.py`/`services/workflow_projection.py`/`api/workflow.py` 包边界符合依赖方向
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | WorkflowAdapter 真实实例与源码扫描（不 mock） | `local_durable_state_count` 恒等 0；runtime 包无 durable state | planned | planned | planned |
| E-01 | integration | 真实 DBOS backend 停机 + ResilientWorkflowEngine | 熔断 open→快速失败；cooldown 后试探恢复；明确错误码非无限等待 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-002: WorkflowDefinition V2 节点契约 + validator

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase3-workflow-platform.design.md#2.3.2 字段约束, phase3-workflow-platform.design.md#3.2 架构设计, phase3-workflow-platform.design.md#4.4 数据迁移
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: B-03

### Description

扩展 `WorkflowDefinition`（`resources/contracts.py`）为节点判别联合（`type` 为 discriminator）：`capability`（=V1 step 兼容，`capability_ref` 必填，前缀 `skill|tool|mcp`——`tool:` 解析 `ResourceKind.TOOL`，Phase 1 Closure 统一）/`agent`（**`agent_ref`（`agent:<id>@<version>`）必填**，经 Agent exact version → Phase 2 ContextResolver（agent_id 主坐标）→ RuntimeProfile → pinned ExecutionSnapshot → AgentRuntime；DSL 不感知 Runtime mechanics，remediation §14.1）/`condition`（`expression` + `then`/`else`）/`switch`（`cases ≥1` + `default`）/`parallel`（`branches ≥2` + `join_policy: all|any`）/`transform`/`wait`（`duration_seconds >0`）/`human_task`（`assignee` 必填 + `message` + `timeout_seconds`）/`subworkflow`（`workflow_ref` 必填）。公共字段：`id`（唯一）、`depends_on`（无环校验）、`timeout_ms`、`retry_policy`（max_attempts/delay，仅表达业务意愿）、`output_schema`（可选）。**无 `engine_ref` 字段**（remediation §14.3：durable backend 选择属 Platform Configuration `WorkflowBackendSettings`，不进 Product DSL）。`model_validator(mode="before")` 对无 `type` 字段且有 `capability_ref` 的 V1 spec 注入 `type="capability"`——现网定义零迁移（B-03）。`services/workflow_app.py` validator V2 扩展（环检测、分支/聚合约束）。

### Checklist

- [ ] 实现 9 节点 typed model（判别联合）+ 公共字段约束（depends_on 无环、branches ≥2、cases ≥1、duration_seconds >0）
- [ ] 实现 V1 兼容注入 `model_validator(mode="before")`；validator V2 扩展（环检测/分支约束）
- [ ] [B-03][unit] 修改生产代码前，编写验收测试并记录 RED：V1 spec（无 type、纯 capability step）经 validator 兼容通过且注入 `type="capability"`，现网 spec 不需迁移
- [ ] 断言非法定义被拒：未知节点类型、环依赖、`branches <2`、缺 capability_ref/agent_ref/assignee/workflow_ref；断言 V2 模型无 `engine_ref` 字段（backend 属 Platform Configuration，remediation §14.3）
- [ ] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/resources/ -k workflow_definition`（planned）：断言 WorkflowDefinition V2 仍走 resource_definitions 版本化生命周期（published 不可变、修改产生新 Draft/Version）、SQLite/PG 同契约、pinned_refs 表达版本快照语义
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-03 | unit | WorkflowDefinition validator 纯函数（真实 V1 spec fixture） | V1 兼容通过；`type="capability"` 注入；非法定义被拒 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-003: 条件表达式白名单求值器

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase3-workflow-platform.design.md#3.4 接口设计, phase3-workflow-platform.design.md#2.5.2 功能验收场景
- **Acceptance-Refs**: B-04, NFR-SEC-02

### Description

实现文档化子集的条件表达式求值器（condition/switch 节点共用）：引用插值 `{{ node_id.output }}`、比较符 `==`/`!=`/`>`/`<`/`>=`/`<=`/`in`、布尔组合 `and`/`or`/`not`、白名单函数 `len()`/`lower()`/`upper()`/`is_empty()`。用 Python `ast` 解析 + 白名单节点校验，非 `eval`；非白名单形态（函数调用、属性访问、下标赋值等）拒绝求值并抛明确错误（NFR-SEC-02 / RISK-P3-05）。

### Checklist

- [ ] 实现 AST 白名单求值器：插值/比较/布尔/白名单函数四类形态，其余一律拒绝
- [ ] [B-04][unit] 修改生产代码前，编写验收测试并记录 RED：注入向量（`__import__`、属性链、任意调用、`eval` 字符串）全部被拒；白名单表达式求值正确
- [ ] 断言非法/非白名单表达式返回明确错误（非静默 fallback）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-04 | unit | 求值器纯函数（真实 AST 解析） | 注入向量全拒；白名单子集求值正确 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-004: durable graph 解释器 `_run_graph`/`_run_node`

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: phase3-workflow-platform.design.md#3.2 架构设计, phase3-workflow-platform.design.md#3.4 接口设计, phase3-workflow-platform.design.md#3.1 方案选型
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-04, S-10, E-03, RULE-P3-04

### Description

单注册 DBOS workflow `_run_graph(definition, input, run_meta)`：数据驱动遍历 V2 定义图（免按定义 codegen/动态注册）；每节点类型一个 `@DBOS.step()` executor（`_run_node(kind, node_def, inputs, scope)`，按类型 dispatch）。`node_id → value` 输出映射 + `{{ node_id.output }}` 引用插值（TASK-003 求值器）；Condition/Switch 路由、Parallel 分支 `asyncio.gather` + Join（`join_policy: all|any`）、Transform 变换、SubWorkflow 嵌套；Agent 节点经 `agent_ref` → Phase 2 ContextResolver（`agent_id` 主坐标）取 pinned ExecutionSnapshot 跑 AgentRuntime（DSL 不感知 Runtime mechanics，remediation §14.1）；capability executor 按 `skill|tool|mcp` 前缀 dispatch（`tool:` → `ResourceKind.TOOL`）。Retry 边界：step 级 durable retry 归 DBOS（step 首次抛异常自动重试且副作用不重复），`retry_policy` 字段只表达业务意愿；节点 `timeout_ms` < 实际耗时 → 有界转 ERROR（禁无限等待）。节点失败 → DBOS step retry → fail policy 终态。

### Checklist

- [ ] 实现 `_run_graph` 数据驱动遍历 + 8 类节点 `@DBOS.step()` executor + 输出映射/插值
- [ ] 实现 Condition/Switch 路由、Parallel/Join（`asyncio.gather` + join_policy）、Transform、SubWorkflow 嵌套
- [ ] [S-10][E2E] 修改生产代码前，编写验收测试并记录 RED：condition/switch/parallel/transform/subworkflow 混合图真实运行，图执行结果正确、并行分支并发完成
- [ ] [S-04][integration] 修改生产代码前，编写验收测试并记录 RED：`timeout_ms` < 实际耗时的节点运行 → 有界转 ERROR，不无限等待（RULE-P3-04）
- [ ] [E-03][integration] 修改生产代码前，编写验收测试并记录 RED：step 首次执行抛异常 → DBOS step retry 生效、业务写不重复（副作用恰 1 条）、最终成功或按 fail policy 终态
- [ ] 架构测试：step executor 内无 Fluxion 层重试（RISK-P3-02 禁 double retry）
- [ ] **Spec verifier**：`RULE-backend-quality-001` — 运行 S-04/E-03 verifier 套件（`python -m pytest backend/tests/runtime/ -k "graph or interpreter"`，planned）：断言所有 backend/DBOS 调用有 timeout+fail policy、step 级 durable retry 与 Fluxion backend 调用重试互不叠加、公共函数类型注解完整、异常不吞
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | DBOS + 真实 PG + 混合图 Definition（不 mock 引擎/存储） | 8 节点类型执行结果正确；并行分支并发完成 | planned | planned | planned |
| S-04 | integration | 真实 DBOS step timeout 配置运行 | 超时节点有界转 ERROR；无无限等待 | planned | planned | planned |
| E-03 | integration | 真实 DBOS durable retry + 业务 Store | retry 生效；业务记录恰 1 条；fail policy 终态 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-005: 独立 worker 部署 + durable start / crash recovery E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#4.1 部署架构, phase3-workflow-platform.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-01, S-02, S-05, S-06, RULE-P3-05

### Description

落地 `fluxion-workflow-worker` 独立进程（Deployment 形态，≥2 副本）：`DBOS.launch()` + `listen_queues` + startup recovery；`worker_concurrency` 有界（PoC 4）防单 worker 全认领；`DBOS.register_queue` 非 async 上下文 → 后台线程注册（PoC 已验证）。API/Console 进程持 DbosWorkflowEngine 做 client 侧 start/signal/cancel/status（stateless）。Phase 3 Gate 自动化证据：S-01 durable start 同步持久化（start 返回后 DBOS 可查状态）P95≤1s；S-02 SIGKILL worker → 新进程 startup recovery 续跑、已完成 step 不重跑，P95≤60s；S-05 同 execution 二次 start 返回既有 run、业务记录恰 1 条；S-06 database-backed queue 2 worker 分摊。指标：durable start P95 / recovery P95 / step 重试次数 / circuit-breaker 状态（PATTERN-backend-004）。

### Checklist

- [ ] 实现 worker 入口：`DBOS.launch()` + queue listen + startup recovery + 后台线程 register_queue + `worker_concurrency` 有界
- [ ] [S-01][E2E] 修改生产代码前，编写验收测试并记录 RED：已发布含 capability+wait+human_task 节点的 WorkflowDefinition，`WorkflowAdapter.execute` → 返回 run_id 且 start 返回后 DBOS 可查状态；连续 start 计时 P95≤1s
- [ ] [S-02][E2E] 修改生产代码前，编写验收测试并记录 RED：workflow 运行中 SIGKILL worker → 新进程启动 → startup recovery 续跑、已完成 step 不重跑；恢复计时 P95≤60s
- [ ] [S-05][E2E] 修改生产代码前，编写验收测试并记录 RED：同 execution 二次 start → 返回既有 run、step 不重跑、业务记录恰 1 条（SLO-WF-03）
- [ ] [S-06][integration] 修改生产代码前，编写验收测试并记录 RED：database-backed queue 排队任务 → 2nd worker 拉取、任务分摊
- [ ] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 Phase 3 Gate 套件（S-01/S-02/S-05 + S-07/S-08/S-09 引用，`python -m pytest backend/tests/integration/ -k workflow_gate`，planned）：断言 crash/timer/idempotency/approval/pinned/GC 证据全部为编码期自动化产出，非事后补
- [ ] **Spec verifier**：`RULE-backend-logging-001` — 运行 S-01 verifier 用例（planned）：断言 run_id/execution_id/trace_id/tenant_id 全链路关联（WorkflowStartRequest 透传 + 投影）、structlog JSON（`emit_workflow_event_log` 复用）、敏感字段脱敏
- [ ] **Spec verifier**：`RULE-backend-platform-001` — 运行 S-01/S-02 计时断言（planned）：断言 SLO-WF-01（start P95≤1s）/ SLO-WF-02（recovery P95≤60s）在测试中可断言、错误码走命名空间、配置经 `WorkflowBackendSettings`（环境变量 > 配置文件 > 默认值）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Adapter → 真实 DbosWorkflowEngine → DBOS → 真实 PG（不 mock） | run_id 返回；start 后 DBOS 可查；P95≤1s | planned | planned | planned |
| S-02 | E2E | 真实独立 worker 进程 + SIGKILL + PG | recovery 续跑；已完成 step 不重跑；P95≤60s | planned | planned | planned |
| S-05 | E2E | 真实 SetWorkflowID 幂等 + 二次 start | 既有 run 返回；step 不重跑；业务记录恰 1 条 | planned | planned | planned |
| S-06 | integration | 2 个真实 worker 进程 + database-backed queue | 任务分摊；水平扩展生效 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-006: HumanTask / Wait / Timer / Resume

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#2.5.2 功能验收场景
- **Acceptance-Refs**: S-08, S-09, NFR-REL-02

### Description

durable 等待原语：`wait` 节点用 `DBOS.sleep_async`（durable timer）；`human_task` 节点用 `DBOS.recv_async`/`send`（durable signal），assignee 解析（user ref / role）+ `message` + `timeout_seconds` 超时处理；`signal(run_id, name, payload)` 唤醒。跨重启存活：worker kill + 重启后审批信号仍可唤醒（S-08）、timer 按原始 deadline 触发且 elapsed 落在窗口内（S-09）。

### Checklist

- [ ] 实现 `wait`（`DBOS.sleep_async`）与 `human_task`（`recv_async`/`send` + assignee 解析 + timeout_seconds）节点 executor
- [ ] [S-08][E2E] 修改生产代码前，编写验收测试并记录 RED：审批节点运行中 + worker 重启 → `send(approve signal)` 唤醒并继续，跨重启存活
- [ ] [S-09][E2E] 修改生产代码前，编写验收测试并记录 RED：wait 节点运行中 kill + 重启 worker → 按原始 deadline 触发，elapsed 落在窗口内
- [ ] 断言 `human_task` 超时路径：超时后按 fail policy 终态（非永久挂起）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实 recv_async/send + 独立 worker 重启 + PG | 审批信号跨重启唤醒；流程继续 | planned | planned | planned |
| S-09 | E2E | 真实 sleep_async + kill/重启 worker | 原始 deadline 触发；elapsed 在窗口内 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-007: Version pin / active ref / GC

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-005
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#3.3 数据设计, phase3-workflow-platform.design.md#2.5.2 功能验收场景
- **Acceptance-Refs**: S-03, S-07, RULE-P3-02, RULE-P3-03

### Description

复用 ADR-SNAPSHOT-001 `active_references`（不建 workflow 专用 pin 表）：start 时对 `pinned_refs`（workflow + 依赖资源版本快照）逐项 acquire（`ref_type=workflow`）；terminal（succeeded/failed/cancelled）时释放。hard-delete guard：存在 active ref → 拒绝删除（RISK-P3-03 + tombstone）。resume 语义：始终使用 pinned version（ExecutionSnapshot），不 resolve latest（RULE-P3-02）；deprecated-but-resumable——pinned deprecated 版本仍可恢复。

### Checklist

- [ ] 实现 start acquire / terminal release（`ref_type=workflow`，逐项 `pinned_refs`）
- [ ] hard-delete guard：存在 active ref → 拒绝删除并返回明确错误码
- [ ] [S-03][integration] 修改生产代码前，编写验收测试并记录 RED：start 时 workflow v1 pinned、v2 已发布 → resume 长 workflow 使用 v1，不 resolve latest（断言解析到的版本坐标）
- [ ] [S-07][E2E] 修改生产代码前，编写验收测试并记录 RED：workflow 运行中引用 v1 → hard-delete v1 被拒（RULE-P3-03）；run 结束后（ref 释放）可删
- [ ] 断言 terminal 释放后 `active_references` 无残留行（GC 正确性）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 真实 ExecutionSnapshot + Registry + active_references（双库契约） | resume 用 pinned v1 非 latest；版本坐标正确 | planned | planned | planned |
| S-07 | E2E | 运行中 workflow + 真实 hard-delete API + active_references | 删除被拒；terminal 释放后可删；无残留 ref | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-008: workflow_run 投影 + status / execution-history API

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-004, TASK-005
- **Source**: phase3-workflow-platform.design.md#2.3.1 功能清单, phase3-workflow-platform.design.md#3.3 数据设计, phase3-workflow-platform.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-11, E-02, B-02, RULE-P3-06

### Description

Fluxion 域 `workflow_run` 投影表（与 DBOS sysdb 同库不同表，DBOS sys 表由 DBOS 管理、Fluxion 不直写）：run_id（=`{workflow_id}:{execution_id}`，PK）、tenant_id（强制，`idx_wf_run_tenant`）、workflow_id/version（pinned）、execution_id（`idx_wf_run_exec`）、trace_id、status（running/succeeded/failed/cancelled/paused）、node_states JSON（`{node_id: {status, output_ref, error}}`，分批写入——PATTERN-backend-003）、pinned_refs JSON。`services/workflow_projection.py`：`get_run(tenant_id, run_id)`（不存在/跨租户→NotFound）/`list_runs(tenant_id, workflow_id)`。`api/workflow.py`：`GET /workflows/runs/{run_id}`、`GET /workflows/{workflow_id}/runs`（统一 envelope，Handler 不手写响应结构）。`get_execution_history` 关联 execution→run（Console/Workflow Studio Phase 4 数据源）。

> 优先级说明：FEAT-P3-06 为 P1；其中 tenant scope（E-02/B-02，RULE-P3-06 / 架构规则 16）是该任务内不可协商的硬约束，实现时不因 P1 降级。

### Checklist

- [ ] 建 `workflow_run` 投影表（幂等 DDL，CREATE IF NOT EXISTS）+ 索引；解释器节点状态/结果写投影
- [ ] 实现 `WorkflowProjectionService.get_run/list_runs`（tenant 强制 scope）+ `api/workflow.py` 路由（统一 envelope）
- [ ] [S-11][integration] 修改生产代码前，编写验收测试并记录 RED：运行中/终态 run 查询 `GET /workflows/runs/{run_id}` → 返回 node 级状态 + pinned refs + execution history
- [ ] [E-02][integration] 修改生产代码前，编写验收测试并记录 RED：查询他租户 run → 404 NotFound + 统一 envelope 错误（不可见）
- [ ] [B-02][integration] 修改生产代码前，编写验收测试并记录 RED：tenant A run 对 tenant B 全链路不可见（列表/详情/投影均隔离，NFR-SEC-01）
- [ ] **Spec verifier**：`RULE-fluxion-console-001` — 运行 S-11 verifier 用例（planned）：断言 status API 供 Console（Workflow Studio Phase 4）消费、Runtime 边界不内侵（Console 只读投影）
- [ ] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 S-11/E-02 verifier 用例（planned）：断言统一 envelope `{code, message, data, request_id}`、Handler 无手写响应结构、错误码命名空间
- [ ] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/contract/ -k workflow_run`（planned）：断言投影表 schema/索引/tenant scope 双库契约、node_states 分批写入、无 N+1
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-11 | integration | 真实投影表 + ASGI 栈 + 运行中/终态 run | node 级状态 + pinned refs + execution history 返回 | planned | planned | planned |
| E-02 | integration | 真实跨租户查询路径 | 404 NotFound + 统一 envelope | planned | planned | planned |
| B-02 | integration | tenant A/B 双租户真实数据 | 全链路隔离（列表/详情/投影） | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)
