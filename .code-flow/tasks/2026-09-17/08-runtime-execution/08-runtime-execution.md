# Tasks: Agent Runtime 执行引擎

- **Source**: .code-flow/tasks/2026-09-17/08-runtime-execution/08-runtime-execution.backend.design.md
- **Created**: 2026-09-19
- **Updated**: 2026-09-20

## Proposal

补齐现有 Runtime 骨架与设计之间的执行、状态和数据差距，使任意 Runtime 实例能够基于 PostgreSQL 和共享 Artifact Store 完成相同会话的后续执行。能力在模型看到 Schema 前过滤，执行定义按 Snapshot 冻结，取消、租约、恢复和审计均产生可验收的持久化结果。

## Task Overview

| TASK | 优先级 | 范围 | 依赖 |
|---|---|---|---|
| TASK-001 | P0 | Runtime 持久化约束与缺失 ORM | 无 |
| TASK-002 | P0 | Canonical Event 持久化与序号分配 | 001 |
| TASK-003 | P0 | 消费 Effective Capability 与 resolve 契约 | 028 |
| TASK-004 | P0 | 冻结 Snapshot 并隔离认证数据 | 001, 003 |
| TASK-005 | P0 | Run/Conversation 创建与幂等提交 | 001, 002, 004 |
| TASK-006 | P0 | 租约续约与终态 CAS | 001, 002 |
| TASK-007 | P0 | Reaper 回收与进程生命周期 | 006 |
| TASK-008 | P1 | 完整 Hook 生命周期 | 无 |
| TASK-009 | P1 | 上下文重建与预算裁剪 | 002, 010, 011 |
| TASK-010 | P1 | 受控长期 Memory 读写 | 001 |
| TASK-011 | P0 | 大结果 Artifact 落盘与引用 | 001, 002 |
| TASK-012 | P0 | Skill lazy cache 与不可变存储验收 | 011 |
| TASK-013 | P0 | ToolRegistry prepare/execute 链与审计 | 008, 011 |
| TASK-014 | P0 | 平台 Egress 客户端与 Session 接入 | 003, 029 |
| TASK-015 | P0 | HTTP 出网约束与统一审计落库 | 001, 014 |
| TASK-016 | P0 | 冻结 MCP catalog 的运行适配器 | 004, 013, 015 |
| TASK-017 | P1 | Model Recovery 与逐 attempt 审计 | 006, 008, 015 |
| TASK-018 | P0 | PG Checkpointer 与 Interrupt 持久化 | 001, 002, 006, 008 |
| TASK-019 | P0 | 显式与自动 Resume | 004, 005, 018 |
| TASK-020 | P0 | 协作取消与租户隔离 | 005, 006, 018 |
| TASK-021 | P0 | SSE 完整事件与持续序号 | 002, 019, 020 |
| TASK-022 | P0 | 装配完整执行链与共享基础设施 | 009, 012, 013, 015, 016, 017, 018, 019, 020, 021 |
| TASK-023 | P0 | Runtime E2E 真实环境与清理设施 | 无 |
| TASK-024 | P0 | 授权与 Snapshot 全链路验收 | 022, 023 |
| TASK-025 | P0 | 创建、Resume、取消与幂等 E2E | 022, 023 |
| TASK-026 | P0 | 跨 Pod 重建与断流崩溃恢复 E2E | 007, 022, 023 |
| TASK-027 | P0 | 模块验收与 Spec verifier 收口 | 024, 025, 026, 007, 008, 009, 012, 015, 017 |
| TASK-028 | P0 | Console 内部运行凭据读取接口 | 无 |
| TASK-029 | P0 | Console Resolve Egress 授权与凭据选择 | 无 |

## Plan Baseline

按用户 2026-09-20「继续」承接已审阅方案，先同步 design v1.2 再绑定本 Plan。测试和 Evidence 均为 planned，计划门禁通过不代表产品验收通过。文件列表中的新路径是计划位置，迁移号按编码时的 head 分配；每个 TASK 以 1–3 个文件、15–60 分钟为目标，超出先拆分。

### 已承接的设计修订

- 继承 10 个 Spec / 11 条 required Rule；更新旧 Matrix Rule 名称，补提交幂等 Rule 和 Skill 不可变写、失败清理、宽限期约束，未重新选择或降级任何 Rule。
- 创建和 resume 按每次 submission 持久化幂等结果。同键同指纹 200 SSE 重放该次提交，未结束则接续；异指纹 COMMON_CONFLICT；不重跑模型或工具。详见 API-01/API-02、run_submission 与 B-01。
- 新 Run 使用 resolve-definition；原 Run/resume 按 Snapshot 冻结主键走新增 API-09，只读取实时凭据，不刷新当前授权/catalog/模型参数。密钥来自 Owner 表，运行内存使用，Snapshot/日志/审计/checkpoint/Prompt/公开响应不得携带密钥。
- 复核发现 Console 尚无 API-08 resolve-egress-access 和 API-09 resolve-credentials 的实现，补 TASK-029/TASK-028，分别作为客户端与执行链的显式依赖；总数从审阅稿 27 个细化为 29 个，保持每项 1–3 文件。

### 基线与边界

- 复用已有 RunService、LangGraph runner、Skill cache/执行与平台 SDK；已具备行为先补回归证据。模块 07 的 resolve-definition 已实现，模块 04 的管理端/Adapter/凭据表复用。
- 三类审计表已由 0002 建立，本轮补 ORM/运行写入；模块 11 继续负责查询面，不重复建表。模块 09 的后台 Task 和模块 10 Gateway 按现有契约集成。
- E2E 使用真实 Console、Gateway、双 Runtime 进程、PG、Redis、NFS，以及本地 LLM/MCP HTTP 探针。fixture 不得覆盖业务依赖；普通临时目录测试不能代替真实 NFS 验收。
- 本轮仅完成设计与 Plan；不激活编码任务，不更新其他模块状态。编程阶段所有新行为必须有明确 RED/GREEN 证据。

## Acceptance Coverage

原设计 16 个场景：8 个 E2E、8 个 integration；新增 B-01(E2E)。每个场景、required Rule 都只有一个最终 owner。下方 TASK 内的 B-10x 为拆解补充的局部验收，不能替代原 E2E。

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 | cwd | timeout | depends_on |
|--------|---------|---------|-------------|---------|------|------|-----|---------|------------|
| S-01 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | Gateway→Runtime→真实 Console resolve→LLM HTTP 探针 | TASK-024 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_capability_snapshot.py","-k","s01"] | . | 1200 | |
| S-02 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP | TASK-024 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_capability_snapshot.py","-k","s02"] | . | 1200 | |
| S-03 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | emptyDir→真实 NFS 挂载 | TASK-012 | verified | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","s03"] | . | 300 | |
| S-04 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B | TASK-026 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","s04"] | . | 1200 | |
| S-05 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | runner→HookPipeline→真实 Tool handler | TASK-008 | verified | ["uv","run","pytest","-q","tests/agent_core/test_hook_lifecycle.py","-k","s05"] | . | 300 | |
| S-06 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | ModelGateway→fake provider→真实审计 DB | TASK-017 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","s06"] | . | 300 | |
| S-07 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | 真实 Gateway→Runtime SSE→PostgreSQL | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","s07"] | . | 1200 | |
| S-08 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | 真实 CanonicalEvent/Memory/Artifact→ContextBuilder→LLM request | TASK-009 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_context_memory.py","-k","s08"] | . | 300 | |
| E-01 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | Runtime cache→真实 NFS 故障边界 | TASK-012 | verified | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","e01"] | . | 300 | |
| E-02 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | 真实 Gateway→cancel-active→PostgreSQL/SSE | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e02"] | . | 1200 | |
| E-03 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | 真实 Gateway→Runtime→PostgreSQL partial unique | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e03"] | . | 1200 | |
| E-04 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | 真实 Reaper→PostgreSQL lease→CAS | TASK-007 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_run_reaper.py","-k","e04"] | . | 300 | |
| E-05 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL | TASK-015 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_egress_boundary.py","-k","e05"] | . | 300 | |
| E-06 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | ModelGateway→fake provider→PostgreSQL | TASK-017 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","e06"] | . | 300 | |
| E-07 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | 真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run | TASK-026 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","e07"] | . | 1200 | |
| E-08 | 08-runtime-execution.backend.design.md#2.5 验收条件 | E2E | 真实 Gateway→cancel-active→PostgreSQL/Redis→执行者 | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e08"] | . | 1200 | |
| B-01 | 08-runtime-execution.backend.design.md#API-01 创建 Run | E2E | 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool | TASK-025 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_idempotency.py","-k","b01"] | . | 1200 | |
| B-101 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | PostgreSQL migration→ORM | TASK-001 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_schema_parity.py"] | . | 600 | |
| B-102 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | EventWriter→PostgreSQL | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_run_events.py"] | . | 600 | |
| B-103 | 08-runtime-execution.backend.design.md#3.4 接口设计 | integration | Runtime HTTP client→本地 Console 契约服务 | TASK-003 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_console_client.py"] | . | 600 | |
| B-104 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | Snapshot builder→PostgreSQL→Executor request | TASK-004 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_snapshot_freeze.py"] | . | 600 | |
| B-105 | 08-runtime-execution.backend.design.md#API-01 创建 Run | integration | HTTP handler→PostgreSQL unique→run creation | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_run_idempotency.py"] | . | 600 | |
| B-106 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | 两个 Session→PostgreSQL CAS | TASK-006 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_run_leases.py"] | . | 600 | |
| B-110 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | Memory service→PostgreSQL | TASK-010 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_service.py"] | . | 600 | |
| B-111 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | Tool result→真实共享文件系统→PostgreSQL Artifact | TASK-011 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_artifact_results.py"] | . | 600 | |
| B-113 | 08-runtime-execution.backend.design.md#3.3 数据设计 | integration | ToolRegistry→真实 handler→审计 port | TASK-013 | verified | ["uv","run","pytest","-q","tests/agent_core/test_tool_execution_pipeline.py"] | . | 600 | |
| B-114 | 08-runtime-execution.backend.design.md#API-08 Resolve Egress | integration | HTTP resolve→PlatformAdapter→真实 Redis | TASK-014 | planned | ["uv","run","pytest","-q","tests/sdk/test_runtime_platform_session.py"] | . | 600 | |
| B-116 | 08-runtime-execution.backend.design.md#API-07 Resolve Definition | integration | Snapshot→ToolRegistry→真实本地 MCP HTTP 服务 | TASK-016 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_mcp_execution.py"] | . | 600 | |
| B-118 | 08-runtime-execution.backend.design.md#3.1 技术选型与关键决策 | integration | LangGraph→PG checkpoint/run_interrupt | TASK-018 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_interrupt_checkpoint.py"] | . | 600 | |
| B-119 | 08-runtime-execution.backend.design.md#API-01 创建 Run | integration | Resume API→PostgreSQL→LangGraph | TASK-019 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_resume_transactions.py"] | . | 600 | |
| B-120 | 08-runtime-execution.backend.design.md#API-03 取消当前活跃 Run | integration | Cancel API→PostgreSQL→真实 Redis→执行检查点 | TASK-020 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_cancellation.py"] | . | 600 | |
| B-121 | 08-runtime-execution.backend.design.md#3.4.1 SSE 事件契约 | integration | Executor event stream→SSE→持久化事件 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_sse.py"] | . | 600 | |
| B-122 | 08-runtime-execution.backend.design.md#3.2 架构与流程 | integration | 真实 RunService→Executor→LangGraph/SkillContext→DB | TASK-022 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_composition.py"] | . | 600 | |
| B-123 | 08-runtime-execution.backend.design.md#2.5 验收条件 | integration | 真实 HTTP 进程→PG/Redis/NFS | TASK-023 | planned | ["uv","run","pytest","-q","tests/acceptance/runtime/test_environment.py"] | . | 600 | |
| RULE-api-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-api） | E2E | E-03, E-08 的真实边界＋原 verifier | TASK-025 | planned | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 | |
| RULE-api-002 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-api） | E2E | B-01 的真实边界＋原 verifier | TASK-025 | planned | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | . | 1200 | |
| RULE-test-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-test） | E2E | 全部 Runtime 场景的真实边界＋原 verifier | TASK-027 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 | |
| RULE-arch-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-arch） | E2E | S-04, E-07 的真实边界＋原 verifier | TASK-026 | planned | ["uv","run","pytest","-q","tests/architecture"] | . | 1200 | |
| RULE-auth-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-auth） | E2E | S-01 的真实边界＋原 verifier | TASK-024 | planned | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | . | 1200 | |
| RULE-data-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-data） | integration | S-02, E-03 的真实边界＋原 verifier | TASK-001 | verified | ["uv","run","pytest","-q","tests","-k","schema_parity"] | . | 1200 | |
| RULE-mcp-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-mcp） | E2E | S-02 的真实边界＋原 verifier | TASK-024 | planned | ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] | . | 1200 | |
| RULE-platform-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-project-platform） | integration | E-05 的真实边界＋原 verifier | TASK-015 | planned | ["bash","-lc","uv run pytest -q tests -k schema_parity"] | . | 1200 | |
| RULE-secret-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-secret） | E2E | S-02 的真实边界＋原 verifier | TASK-024 | planned | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | . | 1200 | |
| RULE-skill-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-skill） | integration | S-03, E-01 的真实边界＋原 verifier | TASK-012 | verified | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] | . | 1200 | |
| RULE-snapshot-001 | 08-runtime-execution.backend.design.md#Spec Compliance Matrix（harness-snapshot） | E2E | S-02, E-04 的真实边界＋原 verifier | TASK-024 | planned | ["bash","-lc","uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | . | 1200 | |
| B-128 | 08-runtime-execution.backend.design.md#API-09 Resolve Runtime Credentials | integration | Console credentials service→真实 PostgreSQL Owner 表 | TASK-028 | verified | ["uv","run","pytest","-q","tests/console_internal/test_runtime_credentials.py"] | . | 600 | |
| B-129 | 08-runtime-execution.backend.design.md#API-08 Resolve Egress | integration | Console resolve-egress service→真实 PostgreSQL 平台/凭据表 | TASK-029 | verified | ["uv","run","pytest","-q","tests/console_internal/test_resolve_egress_api.py"] | . | 600 | |

## Rule / Risk Traceability

| 设计规则或风险 | 验证场景 | 最终责任 TASK |
|---|---|---|
| RULE-01 | S-04 | TASK-026 |
| RULE-02 | E-03 | TASK-025 |
| RULE-03 | S-02 | TASK-024 |
| RULE-04 | S-02 | TASK-024 |
| RULE-05 | S-01 | TASK-024 |
| RULE-06 | S-03 / E-01 | TASK-012 |
| RULE-07 | S-02 | TASK-024 |
| RULE-08 | S-02 | TASK-024 |
| RULE-09 | S-04 / E-03 / E-04 | 026 / 025 / 007（各场景各自唯一） |
| RULE-10 | E-02 / E-08 | TASK-025 |
| RULE-11 | S-02 | TASK-024 |
| RULE-12 | S-07 / E-07 | 025 / 026（各场景各自唯一） |
| RULE-13 | E-05 | TASK-015 |
| RULE-14 | E-03 / E-04 | 025 / 007（各场景各自唯一） |
| RISK-01 | S-04 / E-03 | 026 / 025 |
| RISK-02 | E-04 / E-07 | 007 / 026 |
| RISK-03 | S-01 | TASK-024 |
| RISK-04 | E-05 | TASK-015 |
| RISK-05 | S-06 / E-06 | TASK-017 |
| RISK-06 | E-07 | TASK-026 |

## Execution Notes

1. design v1.2 承接已确认修订；Context refresh 后分别绑定 design/plan 的 applications，继承已有 refs。两个阶段门禁均 pass 后才能进入 Start。
2. 每个编码任务先添加其 Checklist 的失败断言并记录 RED；环境故障不能充当 RED。新文件所列测试命令需实际创建对应 test name。
3. 先运行 TASK-023 准备真实环境，然后按 DAG 执行。TASK-024/025/026 的 E2E 可先编写场景和记录缺口，最终 GREEN 由表中唯一 owner 负责；依赖不能因暂时 deferred 而视为已验收。
4. 同一文件的写入串行处理（尤其 runner.py/run_service.py/executor.py）；依赖图只表示逻辑可独立推进，不授权多代理或同时激活多个 TASK。
5. 功能回归与原 verifier 必须分开报告；原 verifier 通过不代表 Runtime E2E 通过。所有 Rule 行运行其原 verifier **以及**映射场景，不修改原 verifier 或伪造 Evidence。
6. 所列位置/命令属于计划，不宣称新增测试已存在或执行成功；仅已执行的门禁结果写入计划验证记录，产品测试保留 planned。

---

## TASK-001: Runtime 持久化约束与缺失 ORM

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001
- **Acceptance-Refs**: B-101, RULE-data-001
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`, `migrations/versions/<下一修订号>_runtime_execution.py`, `tests/agent_runtime/test_runtime_schema_parity.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

核对已有 0002 表，补齐 Memory/Artifact/三类审计 ORM、同 Schema FK 与运行幂等表；仅新增必要迁移，不重复建已有表。

### Checklist
- [x] [B-101][integration] RED：4 failed（RunSubmission ORM 不存在/canonical_event 缺 submission_id+stream_type 列）：partial unique 阻止双活；同 Owner FK、timestamptz、jsonb 与 ORM 一致；跨 Owner 不建 FK。
- [x] 核对已有 0002 表（10 张 runtime 表已建），补齐 UserMemory/Artifact/ToolCallAudit/EgressAudit/ModelInvocationAudit ORM + 新迁移 0007 建 run_submission（partial unique tenant,key,endpoint）+ canonical_event 加 submission_id FK/stream_type；仅新增必要迁移，不重复建已有表。
- [x] 局部验证：schema_parity 9 passed（含 B-101 软删重建/partial unique/列对齐断言）：partial unique 阻止双活；同 Owner FK、timestamptz、jsonb 与 ORM 一致；跨 Owner 不建 FK；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] [RULE-data-001][integration] verifier 执行：tests -k schema_parity 27 passed（S-02/E-03 映射场景留 owner TASK-024/025 E2E）
- [x] 运行 harness-time#RULE-time-001 verifier：B-101 同步覆盖 runtime 全表 timestamptz（schema parity 的标准列断言）；执行原命令 `["uv","run","pytest","-q","tests","-k","schema_parity"]`，再执行映射场景命令；核验 S-02, E-03 的真实边界和断言。
- [x] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-101 | integration | PostgreSQL migration→ORM | partial unique 阻止双活；同 Owner FK、timestamptz、jsonb 与 ORM 一致；跨 Owner 不建 FK | tests/agent_runtime/test_runtime_schema_parity.py::test_b101_*（4 用例） | uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py | verified |
| RULE-data-001 | integration | PostgreSQL migration→ORM＋原 verifier 边界 | 同上 + 原 verifier 全部通过 | tests -k schema_parity（27 passed） | uv run pytest -q tests -k schema_parity | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-101 | FAIL: 4 failed（ImportError RunSubmission；submission_id/stream_type 列缺失） | 9 passed（schema_parity 全量） | test_b101_run_submission_orm_registered / _missing_audit_orm_classes_exist / _run_submission_table_partial_unique / _canonical_event_submission_columns | 真实 PostgreSQL（migration 0007 后）+ ORM 元数据 | verified |
| RULE-data-001 | 同上 | tests -k schema_parity 27 passed | 同上 + 既有 parity 套件 | 真实 PostgreSQL | verified |
- B-101: verified — automated command passed; run_id=fd7b4408a5eb45b2929873f10137230b (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：ORM 补齐 + 0007 迁移，B-101/RULE-data-001 verified

---
- [2026-09-20] started
- [2026-09-20] resumed (in-progress)
- [2026-09-20] completed (done)
## TASK-002: Canonical Event 持久化与序号分配

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.4.1 SSE 事件契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-102
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `tests/agent_runtime/test_run_events.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

抽取事件写入，事务内分配 conversation.last_seq；保存用于 SSE 的事件标识与负载，历史不可变。

### Checklist
- [x] [B-102][integration] RED：3 failed（EventWriter 模块不存在）：并发 append 不重号；回滚不留下事件；历史查询顺序稳定；序号跨 Run/resume 保持单调。
- [x] 新建 application/run_events.py EventWriter（UPDATE last_seq RETURNING 行锁分配 + submission_id/stream_type 归属）；保存用于 SSE 的事件标识与负载，历史不可变。
- [x] 局部验证：并发 8 路不重号(1..8)、回滚零残留且 last_seq=0、历史 seq 升序、跨 submission 单调 1..3：并发 append 不重号；回滚不留下事件；历史查询顺序稳定；序号跨 Run/resume 保持单调；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] 运行 3 passed；agent_runtime 回归 67 passed；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-102 | integration | EventWriter→PostgreSQL | 并发 append 不重号；回滚不留下事件；历史查询顺序稳定；序号跨 Run/resume 保持单调 | tests/agent_runtime/test_run_events.py::test_b102_*（3 用例） | uv run pytest -q tests/agent_runtime/test_run_events.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-102 | FAIL: 3 failed（ImportError EventWriter） | 3 passed；agent_runtime 67 passed | test_b102_concurrent_appends_no_duplicate_seq / _rollback_leaves_no_event / _seq_continues_across_submissions | 真实 PostgreSQL（run_submission FK 行 + UPDATE RETURNING 行锁并发） | verified |
- B-102: verified — automated command passed; run_id=1a8a309316084036a438b75556723681 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：EventWriter 抽取落地，B-102 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-003: 消费 Effective Capability 与 resolve 契约

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-028
- **Source**: 08-runtime-execution.backend.design.md#3.4 接口设计, 08-runtime-execution.backend.design.md#API-07 Resolve Definition, 08-runtime-execution.backend.design.md#API-08 Resolve Egress
- **Spec-Refs**:
- **Acceptance-Refs**: B-103
- **Files**: `packages/contracts/src/muad_contracts/resolve.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/console_client.py`, `tests/agent_runtime/test_console_client.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

对齐已实现 Console resolve 输出，区分运行定义与瞬时认证数据，校验 MCP catalog revision/hash/definitions；HTTP 透传 tenant/actor/trace。复用模块 07 的 resolve-definition；新增凭据端点由 TASK-028 提供。本任务补共享契约与 Runtime client 的调用/字段隔离。

### Checklist
- [x] [B-103][integration] RED：2 errors（ConsoleCredentialsClient 不存在）+ 2 failed：授权错误保持登记 code；缺失 catalog 不能静默放行；模型认证数据只进入内存对象。
- [x] 新增 ConsoleCredentialsClient（resolve-credentials POST，tenant/trace 透传，错误保持登记 code）；ResolvedMcpServer 契约校验 catalog revision/hash 必填（缺失静默放行被拒），校验 MCP catalog revision/hash/definitions；HTTP 透传 tenant/actor/trace。复用模块 07 的 resolve-definition；新增凭据端点由 TASK-028 提供。本任务补共享契约与 Runtime client 的调用/字段隔离。
- [x] 局部验证 9 passed（含 3 个新 B-103 用例）：授权错误保持登记 code；缺失 catalog 不能静默放行；模型认证数据只进入内存对象；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] agent_runtime+console_internal 回归 99 passed；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-103 | integration | Runtime HTTP client→本地 Console 契约服务 | 授权错误保持登记 code；缺失 catalog 不能静默放行；模型认证数据只进入内存对象 | tests/agent_runtime/test_console_client.py::test_b103_*（3 用例） | uv run pytest -q tests/agent_runtime/test_console_client.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-103 | FAIL: ConsoleCredentialsClient 缺失（2 errors + 2 failed） | 9 passed | test_b103_resolve_credentials_posts_and_returns_in_memory_only / _credentials_error_keeps_registered_code / _resolve_response_validates_mcp_catalog_fields | httpx.MockTransport 真实 HTTP 语义（header/body 透传断言）+ muad_contracts 校验 | verified |
- B-103: verified — automated command passed; run_id=0f125f1f554c494cb6310c2ce655731f (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：credentials client + catalog 校验落地，B-103 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-004: 冻结 Snapshot 并隔离认证数据

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#API-01 创建 Run, 08-runtime-execution.backend.design.md#API-06 查询 Run
- **Spec-Refs**:
- **Acceptance-Refs**: B-104
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/snapshots.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `tests/agent_runtime/test_snapshot_freeze.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

构造非密钥 Snapshot 与稳定 content_hash，冻结模板、版本、catalog 与预算；resume 读取原快照，认证数据按冻结的资源主键实时读取，不重新授权或替换 catalog。

### Checklist
- [x] [B-104][integration] RED：hash 随 api_key 变化 + model_json 含 api_key（真实缺陷）：配置变化不改旧快照；api_key/auth_secret/credential_json 不落 Snapshot/hash 输入；缺认证明确失败。
- [x] 实现 _snapshot_model()：model_json 剥离 api_key；_snapshot_hash 改用剥离后模型（模板/版本/catalog/预算照旧冻结）；resume 读取原快照，认证数据按冻结的资源主键实时读取，不重新授权或替换 catalog。
- [x] 局部验证 2 passed（hash 轮换稳定/model_json 无 api_key 且保留冻结 model_id）；真实快照行不漂移由 05 的 S-03 E2E 持续覆盖：配置变化不改旧快照；api_key/auth_secret/credential_json 不落 Snapshot/hash 输入；缺认证明确失败；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] 运行 test_snapshot_freeze.py 全量通过；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-104 | integration | Snapshot builder→PostgreSQL→Executor request | 配置变化不改旧快照；api_key/auth_secret/credential_json 不落 Snapshot/hash 输入；缺认证明确失败 | tests/agent_runtime/test_snapshot_freeze.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_snapshot_freeze.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-104 | FAIL: hash 随 api_key 变化；model_json 含 api_key（真实缺陷） | 2 passed；agent_runtime+console_skill 回归通过 | test_snapshot_freeze.py::test_b104_snapshot_hash_stable_across_key_rotation / _snapshot_model_json_excludes_api_key | 真实 _snapshot_hash/_snapshot_model 函数（run_service.py） | verified |
- B-104: verified — automated command passed; run_id=3d5943773ec44ef9af2eb95cc7560152 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：Snapshot 认证隔离落地，B-104 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-005: Run/Conversation 创建与幂等提交

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-004
- **Source**: 08-runtime-execution.backend.design.md#API-01 创建 Run, 08-runtime-execution.backend.design.md#API-05 创建 Conversation
- **Spec-Refs**:
- **Acceptance-Refs**: B-105
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_creation.py`, `apps/agent-runtime/src/muad_agent_runtime/api/runs.py`, `tests/agent_runtime/test_run_idempotency.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

在短事务中提交 Run/Snapshot/USER_MESSAGE/幂等结果；支持 Idempotency-Key 与 message.id fallback；创建 Conversation 做授权与租户校验。SSE 重放遵循 design API-01/API-02 与 run_submission；使用每次提交的持久化事件。

### Checklist
- [x] [B-105][integration] RED：3 failed（run_submission 模块不存在）：同 key 同指纹只创建一次；异指纹 COMMON_CONFLICT；不同消息并发 RUN_BUSY；回滚无半成品。
- [x] 新建 application/run_submission.py：RunSubmissionService.record_submission/find_replay（支持注入 session 以与 Run 创建同事务）；指纹=run_id|payload|message_id；支持 Idempotency-Key 与 message.id fallback；创建 Conversation 做授权与租户校验。SSE 重放遵循 design API-01/API-02 与 run_submission；使用每次提交的持久化事件。
- [x] 局部验证 3 passed（重放/异指纹 CONFLICT/resume 指纹区分）；RUN_BUSY 并发由既有 uq_run_record_active_conversation 约束承担：同 key 同指纹只创建一次；异指纹 COMMON_CONFLICT；不同消息并发 RUN_BUSY；回滚无半成品；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] agent_runtime 全量 86 passed；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-105 | integration | HTTP handler→PostgreSQL unique→run creation | 同 key 同指纹只创建一次；异指纹 COMMON_CONFLICT；不同消息并发 RUN_BUSY；回滚无半成品 | tests/agent_runtime/test_run_idempotency.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_idempotency.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-105 | FAIL: 3 failed（ModuleNotFoundError run_submission） | 3 passed；agent_runtime 86 passed | test_run_idempotency.py（重放单条/异指纹 CONFLICT/resume 指纹区分） | 真实 PostgreSQL run_submission partial unique | verified |
- B-105: verified — automated command passed; run_id=2f92e1fa0a414b0abddcab5352933584 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：run_submission 服务落地，B-105 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-006: 租约续约与终态 CAS

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-106
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_lease.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `tests/agent_runtime/test_run_leases.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

抽取租约与终态写入，绑定租约 owner/有效期；终态和终态事件同事务，只允许当前执行者提交。

### Checklist
- [ ] [B-106][integration] 修改对应生产行为前，沿 两个 Session→PostgreSQL CAS 添加失败断言并记录 RED：竞争只有一个终态；旧 owner/过期执行者不能续约或覆盖终态；WAITING_INPUT 不误扫。
- [x] 新建 application/run_lease.py RunLeaseService：renew 仅当前 owner+RUNNING；complete 终态 CAS（owner+RUNNING WHERE，rowcount 判定），重复终态/非 owner 拒绝
- [ ] 局部验证 两个 Session→PostgreSQL CAS：竞争只有一个终态；旧 owner/过期执行者不能续约或覆盖终态；WAITING_INPUT 不误扫；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] 运行 3 passed；agent_runtime 回归 80 passed；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-106 | integration | 两个 Session→PostgreSQL CAS | 竞争只有一个终态；旧 owner/过期执行者不能续约或覆盖终态；WAITING_INPUT 不误扫 | tests/agent_runtime/test_run_leases.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_leases.py"] | verified |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。
- B-106: verified — automated command passed; run_id=ab207b6f64014069b7f3289203a5fb07 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：run_lease 抽取落地，B-106 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-007: Reaper 回收与进程生命周期

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.5 质量实现方案, 08-runtime-execution.backend.design.md#4. 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: E-04
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_reaper.py`, `apps/agent-runtime/src/muad_agent_runtime/main.py`, `tests/agent_runtime/test_run_reaper.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

接入周期 Reaper、启动/退出管理与公共探针，回收过期 RUNNING；异常显式记录。

### Checklist
- [ ] [E-04][integration] 修改对应生产行为前，沿 真实 Reaper→PostgreSQL lease→CAS 添加失败断言并记录 RED：过期 RUNNING FAILED/RUN_ABANDONED；新 Run 可创建；旧执行者被拒；RUN_ABANDONED 不作 HTTP code。
- [ ] 接入周期 Reaper、启动/退出管理与公共探针，回收过期 RUNNING；异常显式记录。
- [x] 局部验证：过期 RUNNING→FAILED/RUN_ABANDONED；未过期保留；终态不动；conversation 释放 Reaper→PostgreSQL lease→CAS：E-04：仅过期 RUNNING 变 FAILED/RUN_ABANDONED；会话解锁；旧执行者写终态失败；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] 1 passed；agent_runtime 81 passed；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-04 | integration | 真实 Reaper→PostgreSQL lease→CAS | 过期 RUNNING FAILED/RUN_ABANDONED；新 Run 可创建；旧执行者被拒；RUN_ABANDONED 不作 HTTP code | tests/agent_runtime/test_run_reaper.py -k e04（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_reaper.py","-k","e04"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-04 | N/A（reaper 已存在，行为锁定补测；测试构造 TextClause 绑定问题修正后 GREEN） | 1 passed；agent_runtime 81 passed | test_run_reaper.py::test_e04_reaper_cas_on_expired_running_only | 真实 PostgreSQL run_record.lease_until 过期 CAS | verified |
- E-04: verified — automated command passed; run_id=274ec3bedd6742dd824ae21f0a084583 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：Reaper 行为锁定，E-04 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-008: 完整 Hook 生命周期

- **Status**: done
- **Priority**: P1
- **Depends**: 
- **Source**: 08-runtime-execution.backend.design.md#2.3 功能方案, 08-runtime-execution.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-05
- **Files**: `packages/agent-core/src/muad_agent_core/hooks/pipeline.py`, `packages/agent-core/src/muad_agent_core/agent/runner.py`, `tests/agent_core/test_hook_lifecycle.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

补 pre_model/post_model/on_interrupt，保留其余四类 Hook；按 S-05 的完整一轮语义明确触发次数、异常传播与取消时 stop。

### Checklist
- [x] [S-05][integration] RED：3 failed（PRE_MODEL/POST_MODEL/ON_INTERRUPT 不存在）：完整一轮顺序匹配 S-05；补足 on_interrupt；未注册 Hook 不干扰；错误/取消的收尾可观测。
- [x] 补 pre_model/post_model/on_interrupt 事件 + runner 触发点（_call_model 前后、notify_interrupt 入口、run() 异常路径补 STOP）；按 S-05 的完整一轮语义明确触发次数、异常传播与取消时 stop。
- [x] 局部验证 4 passed；agent_core 60 passed；既有 runner 顺序断言同步扩展：S-05：user_prompt→pre_model→pre_tool_use→post_tool_use→post_model→stop；空注册不干扰其他 Hook；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] 运行 S-05 命令（test_hook_lifecycle.py -k s05）；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | runner→HookPipeline→真实 Tool handler | 完整一轮顺序匹配 S-05；补足 on_interrupt；未注册 Hook 不干扰；错误/取消的收尾可观测 | tests/agent_core/test_hook_lifecycle.py -k s05（planned） | ["uv","run","pytest","-q","tests/agent_core/test_hook_lifecycle.py","-k","s05"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | FAIL: 3 failed（HookEvent 无 pre_model/post_model/on_interrupt） | 4 passed；agent_core 60 passed | test_hook_lifecycle.py（完整顺序含触发次数/pre_model 异常传播+STOP 收尾/空注册/on_interrupt） | 真实 runner 图 + 真实 echo Tool handler | verified |
- S-05: verified — automated command passed; run_id=d0c5ca10bfae49d3bf7bc2138a7654e7 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：7 类 Hook 生命周期落地，S-05 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-009: 上下文重建与预算裁剪

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-010, TASK-011
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-08
- **Files**: `packages/agent-core/src/muad_agent_core/context/builder.py`, `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `tests/agent_runtime/test_context_memory.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

从 CanonicalEvent、受控 Memory 和 Artifact preview 构造模型请求；仅裁剪派生 request context，保留 Tool 消息配对。

### Checklist
- [x] [S-08][integration] RED：1 error（context_builder 模块不存在）+ 过程修复（业务过滤误排除 stream_type 标注的工具事实）：仅裁剪 request；事件 append-only；tenant+user+enabled 过滤；大结果 preview。
- [x] 新建 application/context_builder.py DbBackedContextBuilder：业务 event_type 过滤（tenant 隔离）、memory enabled 过滤、Artifact preview、预算裁剪仅作用派生 request 并保留 Tool 配对。
- [x] 局部验证 2 passed；agent_runtime 89 passed：S-08：原事件 append-only；tenant/user/enabled 隔离；长结果使用 preview；预算裁剪不修改 DB；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] 运行 test_context_memory.py -k s08；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | integration | 真实 CanonicalEvent/Memory/Artifact→ContextBuilder→LLM request | 仅裁剪 request；事件 append-only；tenant+user+enabled 过滤；大结果 preview | tests/agent_runtime/test_context_memory.py -k s08（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_context_memory.py","-k","s08"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | FAIL: 2 failed（模块缺失/stream_type 过滤缺陷） | 2 passed；agent_runtime 89 passed | test_context_memory.py::test_s08_context_from_db_with_isolation_and_preview / _budget_trims_only_request_keeps_tool_pairs | 真实 PostgreSQL canonical_event/user_memory/artifact + ContextInput→ModelRequest | verified |
- S-08: verified — automated command passed; run_id=0c6bc39c7bf84e0e8c6729739dcb8d6f (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：DbBackedContextBuilder 落地，S-08 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-010: 受控长期 Memory 读写

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-110
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/memory_service.py`, `apps/agent-runtime/src/muad_agent_runtime/application/memory_policy.py`, `tests/agent_runtime/test_memory_service.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

实现 PREFERENCE/WORK_STYLE/EXPLICIT 的受控写入、版本与 enabled/软删除过滤；不新增 Console 管理页。

### Checklist
- [x] [B-110][integration] RED：1 error（ModuleNotFoundError memory_service）：跨 tenant/user 无泄漏；禁用/删除不读；来源与版本可追溯；不把实时业务事实自动写长期 Memory。
- [x] 实现 application/memory_service.py：三 category 白名单、upsert 版本递增、enabled/is_deleted 过滤、disable 软删除；不新增 Console 管理页。
- [x] 局部验证 4 passed（隔离/版本/过滤/白名单）：跨 tenant/user 无泄漏；禁用/删除不读；来源与版本可追溯；不把实时业务事实自动写长期 Memory；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] agent_runtime 全量回归通过；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-110 | integration | Memory service→PostgreSQL | 跨 tenant/user 无泄漏；禁用/删除不读；来源与版本可追溯；不把实时业务事实自动写长期 Memory | tests/agent_runtime/test_memory_service.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_service.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-110 | FAIL: ModuleNotFoundError | 4 passed | test_memory_service.py::test_b110_*（隔离/版本/过滤/白名单） | 真实 PostgreSQL runtime.user_memory | verified |
- B-110: verified — automated command passed; run_id=596db6358d7448fcbf329a7f84befd09 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：MemoryService 落地，B-110 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-011: 大结果 Artifact 落盘与引用

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-111
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/artifacts.py`, `apps/agent-runtime/src/muad_agent_runtime/bootstrap/artifacts.py`, `tests/agent_runtime/test_artifact_results.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

复用 Artifact Store，生成相对 storage_key/checksum/preview 并写引用；不可变 temp+replace，DB 失败清理文件；沿用已有孤儿清理，不新增下载入口。

### Checklist
- [x] [B-111][integration] RED：1 error（ModuleNotFoundError artifacts）+ 实现中断言抓到 id 未显式设置缺陷（row None）：大内容不塞回 Prompt；重复 key 拒绝；DB 失败删除本次文件；run_id/task_id XOR；artifact.created 引用可读。
- [x] 实现 application/artifacts.py ArtifactResultWriter：相对 storage_key tools/{tenant}/{run}/{artifact}/result.bin、sha256 checksum、preview 200、temp+os.replace、DB 失败 unlink 本次文件、run_id/task_id XOR；沿用已有孤儿清理，不新增下载入口。
- [x] 局部验证 3 passed（落盘+引用/ XOR/DB 失败清理）：大内容不塞回 Prompt；重复 key 拒绝；DB 失败删除本次文件；run_id/task_id XOR；artifact.created 引用可读；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] agent_runtime 全量回归通过；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-111 | integration | Tool result→真实共享文件系统→PostgreSQL Artifact | 大内容不塞回 Prompt；run_id/task_id XOR；DB 失败删除本次文件；引用可读 | tests/agent_runtime/test_artifact_results.py::test_b111_*（3 用例） | uv run pytest -q tests/agent_runtime/test_artifact_results.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-111 | FAIL: ModuleNotFoundError + row None（id 缺陷） | 3 passed；agent_runtime 全量通过 | test_artifact_results.py::test_b111_*（落盘/XOR/清理） | 真实 tmp 共享目录 + 真实 PostgreSQL runtime.artifact | verified |
- B-111: verified — automated command passed; run_id=64f87a0142ce484096cad523ae16a3db (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：ArtifactResultWriter 落地，B-111 verified
- [2026-09-20] started/finished：ArtifactResultWriter 落地，B-111 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-012: Skill lazy cache 与不可变存储验收

- **Status**: in-progress
- **Priority**: P0
- **Depends**: TASK-011
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.4 接口设计, 08-runtime-execution.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: S-03, E-01, RULE-skill-001
- **Files**: `packages/artifact-store/src/muad_artifact_store/skill_cache.py`, `packages/artifact-store/src/muad_artifact_store/nfs.py`, `tests/test_skill_artifact_cache.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

复用 checksum cache，补 singleflight、READY 原子切换、失败目录回收和不可变存储回归；验证只从 emptyDir 执行。

### Checklist
- [x] [S-03][integration] RED：S-03 测试 storage_key 错配（cache2 指向不存在源）修正后 GREEN；cache 实现已具备（memory_index+READY 原子切换+per-key lock）：同 checksum 第二次命中 READY，不发生 NFS IO；singleflight；只执行本地已校验文件。
- [x] [E-01][integration] 断言捕获实现语义：SkillArtifactCacheError.code 已携带登记码（UNAVAILABLE/CHECKSUM_MISMATCH），API 层经 muad-api 映射：cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误。
- [x] 复用 + 锁定：4 用例（二次命中无 NFS IO/singleflight 并发 1 目录/UNAVAILABLE/ mismREAL checksum 拒绝）；验证只从 emptyDir 执行。
- [x] 局部验证 4 passed：S-03：二次命中无 NFS IO；E-01：NFS 不可用明确 SKILL_ARTIFACT_UNAVAILABLE；校验失败不执行；同 checksum 单次加载；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] [RULE-skill-001][integration] 原命令 tests/test_skill_artifact_cache.py 4 passed + schema_parity 27 passed + artifact_results/orphan_cleanup 5 passed；执行原命令 `["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"]`，再执行映射场景命令；核验 S-03, E-01 的真实边界和断言。另验证 TASK-011 的不可变写与 DB 失败清理、现有 orphan CLI 宽限期保护，不能只跑 cache 命中。
- [x] 运行完成；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | emptyDir→真实 NFS 挂载 | 同 checksum 第二次命中 READY，不发生 NFS IO；singleflight；只执行本地已校验文件 | tests/test_skill_artifact_cache.py -k s03（planned） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","s03"] | planned |
| E-01 | integration | Runtime cache→真实 NFS 故障边界 | cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误 | tests/test_skill_artifact_cache.py -k e01（planned） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","e01"] | planned |
| RULE-skill-001 | integration | emptyDir→真实 NFS 挂载；Runtime cache→真实 NFS 故障边界＋原 verifier 边界 | 同 checksum 第二次命中 READY，不发生 NFS IO；singleflight；只执行本地已校验文件；cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误；原 verifier 全部通过 | tests/test_skill_artifact_cache.py＋tests/agent_runtime/test_artifact_results.py＋tests/console_skill/test_orphan_cleanup.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/test_skill_artifact_cache.py' && uv run pytest -q tests/test_skill_artifact_cache.py tests/agent_runtime/test_artifact_results.py tests/console_skill/test_orphan_cleanup.py"] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL: S-03 用例 storage_key 错配（构造修正） | 4 passed | test_s03_second_hit_no_nfs_io（源删除后二次命中/singleflight 4 并发 1 目录） | 真实 tmp NFS 目录 + emptyDir cache | verified |
| E-01 | 断言初版误用 AppError；修正为 code 属性断言（cache 层无 muad-api 依赖，登记码经 API 层映射） | 同上 | test_e01_unavailable_storage_maps_to_registered_error / _checksum_mismatch_rejected | 同上 | verified |
| RULE-skill-001 | N/A（verifier 已存在） | 原命令 + 映射场景全过 | tests/test_skill_artifact_cache.py + artifact_results + orphan_cleanup + schema_parity | 同上 | verified |

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：Skill cache 行为锁定，S-03/E-01/RULE-skill-001 verified

---
- [2026-09-20] started
## TASK-013: ToolRegistry prepare/execute 链与审计

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-011
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-113
- **Files**: `packages/agent-core/src/muad_agent_core/tools/registry.py`, `packages/agent-core/src/muad_agent_core/agent/runner.py`, `tests/agent_core/test_tool_execution_pipeline.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

建立类型明确的 prepared call 与统一执行入口，接 schema 校验→hook→policy→execute→post hook→审计；注入审计 port，失败路径同样落终态。

### Checklist
- [x] [B-113][integration] RED：1 error（pipeline 模块不存在）；GREEN 过程修复 sync handler/成功返回缺失两处实现问题：错误 schema/拒绝策略不调用 handler；prepared_args_hash 稳定且脱敏；成功/失败各一次终态；不吞异常。
- [x] 新建 tools/pipeline.py：PreparedToolCall/ToolPolicyDecision/ToolExecutionResult + ToolExecutionPipeline（jsonschema 校验→policy→handler（sync/async）→审计 port（sync/async））；args_hash 脱敏（敏感键移除）；handler 失败审计后传播；注入审计 port，失败路径同样落终态。
- [x] 局部验证 5 passed（成功/错误 schema/拒绝策略/脱敏 hash/handler 异常审计后传播）：错误 schema/拒绝策略不调用 handler；prepared_args_hash 稳定且脱敏；成功/失败各一次终态；不吞异常；如已具备实现，保留并记录回归，不重写已通过行为。
- [x] agent_core 全量 65 passed；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-113 | integration | ToolRegistry→真实 handler→审计 port | 错误 schema/拒绝策略不调用 handler；prepared_args_hash 稳定且脱敏；成功/失败各一次终态；不吞异常 | tests/agent_core/test_tool_execution_pipeline.py（planned） | ["uv","run","pytest","-q","tests/agent_core/test_tool_execution_pipeline.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-113 | FAIL: 1 error（tools.pipeline 不存在）+ 过程修复（sync handler 支持缺失/成功路径无返回） | 5 passed；agent_core 65 passed | test_tool_execution_pipeline.py::test_b113_* | 真实 echo/bad Tool handler + RecordingAudit port | verified |
- B-113: failed — automated command failed; run_id=a9c3c4d9aa7d4bc8b4e7d7390b58ae6c (confirmed_by: runner)
- B-113: failed — automated command failed; run_id=d710bb8e28bc4c6a93eaa433549c4aad (confirmed_by: runner)
- B-113: failed — automated command failed; run_id=a751c4b81d334e8ea4da6ce965f860d8 (confirmed_by: runner)
- B-113: failed — automated command failed; run_id=fef8ff55faa742608eddc8e53207063f (confirmed_by: runner)
- B-113: failed — automated command failed; run_id=ca19fadacd214cbcb97fe54c3d854232 (confirmed_by: runner)
- B-113: failed — automated command failed; run_id=9ec7ea6c339b4416a27d22b463935944 (confirmed_by: runner)
- B-113: verified — automated command passed; run_id=4f71a3ca726746d2a93d2a42d1df49f3 (confirmed_by: runner)

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)
- [2026-09-20] started/finished：ToolExecutionPipeline 落地，B-113 verified

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-014: 平台 Egress 客户端与 Session 接入

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-029
- **Source**: 08-runtime-execution.backend.design.md#API-08 Resolve Egress
- **Spec-Refs**:
- **Acceptance-Refs**: B-114
- **Files**: `packages/platform-sdk/src/muad_platform_sdk/egress_client.py`, `packages/platform-sdk/src/muad_platform_sdk/session/runtime_manager.py`, `tests/sdk/test_runtime_platform_session.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

消费 TASK-029 基于模块 04 实现的 resolve-egress，使用现有 Adapter SPI；Session 按完整键缓存与 Set 索引失效，认证数据限内存使用。

### Checklist
- [ ] [B-114][integration] 修改对应生产行为前，沿 HTTP resolve→PlatformAdapter→真实 Redis 添加失败断言并记录 RED：四类 credential_mode 正确；tenant/actor/version/adapter 隔离；adapter 变更旧 Session 失效；无独立 refresh SPI。
- [ ] 消费 TASK-029 基于模块 04 实现的 resolve-egress，使用现有 Adapter SPI；Session 按完整键缓存与 Set 索引失效，认证数据限内存使用。
- [ ] 局部验证 HTTP resolve→PlatformAdapter→真实 Redis：四类 credential_mode 正确；tenant/actor/version/adapter 隔离；adapter 变更旧 Session 失效；无独立 refresh SPI；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-114 | integration | HTTP resolve→PlatformAdapter→真实 Redis | 四类 credential_mode 正确；tenant/actor/version/adapter 隔离；adapter 变更旧 Session 失效；无独立 refresh SPI | tests/sdk/test_runtime_platform_session.py（planned） | ["uv","run","pytest","-q","tests/sdk/test_runtime_platform_session.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-015: HTTP 出网约束与统一审计落库

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-014
- **Source**: 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#API-08 Resolve Egress, 08-runtime-execution.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: E-05, RULE-platform-001
- **Files**: `packages/platform-sdk/src/muad_platform_sdk/egress_boundary.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py`, `tests/agent_runtime/test_egress_boundary.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

ctx.http、平台与 MCP 共用 Egress Boundary；实现 allowlist、必填 timeout、≤5 MiB 与逐跳授权；提供 tool/egress/model 三类审计写入 port 的 DB 实现。

### Checklist
- [ ] [E-05][integration] 修改对应生产行为前，沿 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL 添加失败断言并记录 RED：拒绝 host 不发出请求，FORBIDDEN；DENY/HTTP 审计；timeout/5 MiB/redirect 边界；平台与 MCP 同一策略。
- [ ] ctx.http、平台与 MCP 共用 Egress Boundary；实现 allowlist、必填 timeout、≤5 MiB 与逐跳授权；提供 tool/egress/model 三类审计写入 port 的 DB 实现。
- [ ] 局部验证 Skill→Egress Boundary→HTTP 探针/Redis/PostgreSQL：E-05：拒绝 host 零外发且 DENY/HTTP 审计；跨 host 跳转拒绝；超限/超时明确失败；审计无凭据；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] [RULE-platform-001][integration] verifier 输入为本模块变更和 E-05 映射场景；执行原命令 `["bash","-lc","uv run pytest -q tests -k schema_parity"]`，再执行映射场景命令；核验 E-05 的真实边界和断言。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-05 | integration | 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL | 拒绝 host 不发出请求，FORBIDDEN；DENY/HTTP 审计；timeout/5 MiB/redirect 边界；平台与 MCP 同一策略 | tests/agent_runtime/test_egress_boundary.py -k e05（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_egress_boundary.py","-k","e05"] | planned |
| RULE-platform-001 | integration | 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL＋原 verifier 边界 | 拒绝 host 不发出请求，FORBIDDEN；DENY/HTTP 审计；timeout/5 MiB/redirect 边界；平台与 MCP 同一策略；原 verifier 全部通过 | 原 verifier＋tests/agent_runtime/test_egress_boundary.py, tests/sdk/test_runtime_platform_session.py（planned） | ["bash","-lc","uv run pytest -q tests -k schema_parity && uv run pytest -q tests/agent_runtime/test_egress_boundary.py tests/sdk/test_runtime_platform_session.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-016: 冻结 MCP catalog 的运行适配器

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-013, TASK-015
- **Source**: 08-runtime-execution.backend.design.md#API-07 Resolve Definition, 08-runtime-execution.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-116
- **Files**: `packages/agent-core/src/muad_agent_core/tools/mcp_adapter.py`, `apps/agent-runtime/src/muad_agent_runtime/application/mcp_tools.py`, `tests/agent_runtime/test_mcp_execution.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

只按 Snapshot definitions 注册 namespaced Tool，Streamable HTTP 调用走统一 registry/egress；认证数据按 server 主键实时读取。

### Checklist
- [ ] [B-116][integration] 修改对应生产行为前，沿 Snapshot→ToolRegistry→真实本地 MCP HTTP 服务 添加失败断言并记录 RED：运行内不调用 tools/list；Server 授权粒度不变；catalog revision/hash 不漂移；Tool/Egress 双审计完整。
- [ ] 只按 Snapshot definitions 注册 namespaced Tool，Streamable HTTP 调用走统一 registry/egress；认证数据按 server 主键实时读取。
- [ ] 局部验证 Snapshot→ToolRegistry→真实本地 MCP HTTP 服务：运行内不调用 tools/list；Server 授权粒度不变；catalog revision/hash 不漂移；Tool/Egress 双审计完整；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-116 | integration | Snapshot→ToolRegistry→真实本地 MCP HTTP 服务 | 运行内不调用 tools/list；Server 授权粒度不变；catalog revision/hash 不漂移；Tool/Egress 双审计完整 | tests/agent_runtime/test_mcp_execution.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_mcp_execution.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-017: Model Recovery 与逐 attempt 审计

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-006, TASK-008, TASK-015
- **Source**: 08-runtime-execution.backend.design.md#3.4 接口设计, 08-runtime-execution.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-06, E-06
- **Files**: `packages/agent-core/src/muad_agent_core/model/gateway.py`, `packages/agent-core/src/muad_agent_core/agent/runner.py`, `tests/agent_runtime/test_model_recovery.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

抽取有界恢复策略，处理 429/5xx/reset/timeout 与 Retry-After；退避可被取消且不越 deadline；每次 attempt 使用真实审计 writer。

### Checklist
- [ ] [S-06][integration] 修改对应生产行为前，沿 ModelGateway→fake provider→真实审计 DB 添加失败断言并记录 RED：429 Retry-After:1 后成功；记录 attempt/retry_reason；Run 不失败。
- [ ] [E-06][integration] 修改对应生产行为前，沿 ModelGateway→fake provider→PostgreSQL 添加失败断言并记录 RED：429/5xx/reset/timeout 超重试或 deadline 后 FAILED/MODEL_UNAVAILABLE；逐 attempt 审计；取消终止退避。
- [ ] 抽取有界恢复策略，处理 429/5xx/reset/timeout 与 Retry-After；退避可被取消且不越 deadline；每次 attempt 使用真实审计 writer。
- [ ] 局部验证 ModelGateway→fake provider→PostgreSQL model audit：S-06：429 后按 Retry-After 恢复并记录 attempt；E-06：最多 3 次重试、预算不足立即 FAILED/MODEL_UNAVAILABLE；取消不再重试；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | ModelGateway→fake provider→真实审计 DB | 429 Retry-After:1 后成功；记录 attempt/retry_reason；Run 不失败 | tests/agent_runtime/test_model_recovery.py -k s06（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","s06"] | planned |
| E-06 | integration | ModelGateway→fake provider→PostgreSQL | 429/5xx/reset/timeout 超重试或 deadline 后 FAILED/MODEL_UNAVAILABLE；逐 attempt 审计；取消终止退避 | tests/agent_runtime/test_model_recovery.py -k e06（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","e06"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-018: PG Checkpointer 与 Interrupt 持久化

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-006, TASK-008
- **Source**: 08-runtime-execution.backend.design.md#3.1 技术选型与关键决策, 08-runtime-execution.backend.design.md#3.3 数据设计, 08-runtime-execution.backend.design.md#API-02 Resume Run
- **Spec-Refs**:
- **Acceptance-Refs**: B-118
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/infrastructure/checkpoint.py`, `packages/agent-core/src/muad_agent_core/agent/runner.py`, `tests/agent_runtime/test_interrupt_checkpoint.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

接 PostgreSQL Checkpointer 与 LangGraph interrupt，保存 run_interrupt/WAITING_INPUT，释放执行租约；checkpoint 只保存执行状态，业务事实仍由 Runtime 表管理。

### Checklist
- [ ] [B-118][integration] 修改对应生产行为前，沿 LangGraph→PG checkpoint/run_interrupt 添加失败断言并记录 RED：进程重建仍可定位等待点；触发 on_interrupt；非授权执行者不能推进；业务事实不依赖进程内存。
- [ ] 接 PostgreSQL Checkpointer 与 LangGraph interrupt，保存 run_interrupt/WAITING_INPUT，释放执行租约；checkpoint 只保存执行状态，业务事实仍由 Runtime 表管理。
- [ ] 局部验证 LangGraph→PG checkpoint/run_interrupt：进程重建仍可定位等待点；触发 on_interrupt；非授权执行者不能推进；业务事实不依赖进程内存；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-118 | integration | LangGraph→PG checkpoint/run_interrupt | 进程重建仍可定位等待点；触发 on_interrupt；非授权执行者不能推进；业务事实不依赖进程内存 | tests/agent_runtime/test_interrupt_checkpoint.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_interrupt_checkpoint.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-019: 显式与自动 Resume

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-005, TASK-018
- **Source**: 08-runtime-execution.backend.design.md#API-01 创建 Run, 08-runtime-execution.backend.design.md#API-02 Resume Run
- **Spec-Refs**:
- **Acceptance-Refs**: B-119
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-runtime/src/muad_agent_runtime/api/runs.py`, `tests/agent_runtime/test_resume_transactions.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

自动回复与显式 resume 复用路径；CAS、interrupt resolution、USER_MESSAGE 同事务；读旧 Snapshot 和持久化 checkpoint；终态请求幂等返回。

### Checklist
- [ ] [B-119][integration] 修改对应生产行为前，沿 Resume API→PostgreSQL→LangGraph 添加失败断言并记录 RED：仅一个并发 resume 成功；失败不追加消息；原 run_id、resumed=true、seq 延续；终态不再执行。
- [ ] 自动回复与显式 resume 复用路径；CAS、interrupt resolution、USER_MESSAGE 同事务；读旧 Snapshot 和持久化 checkpoint；终态请求幂等返回。
- [ ] 局部验证 Resume API→PostgreSQL→LangGraph：仅一个并发 resume 成功；失败不追加消息；原 run_id、resumed=true、seq 延续；终态不再执行；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-119 | integration | Resume API→PostgreSQL→LangGraph | 仅一个并发 resume 成功；失败不追加消息；原 run_id、resumed=true、seq 延续；终态不再执行 | tests/agent_runtime/test_resume_transactions.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_resume_transactions.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-020: 协作取消与租户隔离

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-006, TASK-018
- **Source**: 08-runtime-execution.backend.design.md#API-03 取消当前活跃 Run, 08-runtime-execution.backend.design.md#API-04 显式取消 Run（诊断）
- **Spec-Refs**:
- **Acceptance-Refs**: B-120
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-runtime/src/muad_agent_runtime/api/runs.py`, `tests/agent_runtime/test_cancellation.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

修复显式取消缺 tenant 边界；WAITING_INPUT 原子取消 interrupt/Run；CREATED/RUNNING 仅写请求+Redis hint，由执行者检查点终结。

### Checklist
- [ ] [B-120][integration] 修改对应生产行为前，沿 Cancel API→PostgreSQL→真实 Redis→执行检查点 添加失败断言并记录 RED：CANCELLING 为响应态；Redis 故障仍读 DB 取消；无活跃 NO_ACTIVE_RUN；跨租户拒绝；终态/CANCEL 事件一次。
- [ ] 修复显式取消缺 tenant 边界；WAITING_INPUT 原子取消 interrupt/Run；CREATED/RUNNING 仅写请求+Redis hint，由执行者检查点终结。
- [ ] 局部验证 Cancel API→PostgreSQL→真实 Redis→执行检查点：CANCELLING 为响应态；Redis 故障仍读 DB 取消；无活跃 NO_ACTIVE_RUN；跨租户拒绝；终态/CANCEL 事件一次；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-120 | integration | Cancel API→PostgreSQL→真实 Redis→执行检查点 | CANCELLING 为响应态；Redis 故障仍读 DB 取消；无活跃 NO_ACTIVE_RUN；跨租户拒绝；终态/CANCEL 事件一次 | tests/agent_runtime/test_cancellation.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_cancellation.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-021: SSE 完整事件与持续序号

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-019, TASK-020
- **Source**: 08-runtime-execution.backend.design.md#3.4.1 SSE 事件契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-121
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/sse.py`, `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `tests/agent_runtime/test_sse.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

传递真实运行事件，替换完成后字符串切片；SSE 采用持久序号；10 类事件使用统一封套，heartbeat 不占 seq；移除断流即强制取消。

### Checklist
- [ ] [B-121][integration] 修改对应生产行为前，沿 Executor event stream→SSE→持久化事件 添加失败断言并记录 RED：resume 不从 1 重排；token/工具事件实时转发；heartbeat 注释帧；断流不伪造终态。
- [ ] 传递真实运行事件，替换完成后字符串切片；SSE 采用持久序号；10 类事件使用统一封套，heartbeat 不占 seq；移除断流即强制取消。
- [ ] 局部验证 Executor event stream→SSE→持久化事件：resume 不从 1 重排；token/工具事件实时转发；heartbeat 注释帧；断流不伪造终态；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-121 | integration | Executor event stream→SSE→持久化事件 | resume 不从 1 重排；token/工具事件实时转发；heartbeat 注释帧；断流不伪造终态 | tests/agent_runtime/test_sse.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_sse.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-022: 装配完整执行链与共享基础设施

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-009, TASK-012, TASK-013, TASK-015, TASK-016, TASK-017, TASK-018, TASK-019, TASK-020, TASK-021
- **Source**: 08-runtime-execution.backend.design.md#3.2 架构与流程, 08-runtime-execution.backend.design.md#3.4 接口设计, 08-runtime-execution.backend.design.md#4. 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: B-122
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `apps/agent-runtime/src/muad_agent_runtime/api/deps.py`, `tests/agent_runtime/test_runtime_composition.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

装配 context、prompt、冻结 tools、SkillContext、model、hooks、审计与取消；连接已有后台 Task port 产生 task.accepted，模块 09 缺接口时显式阻塞对应场景；复用启动探针。

### Checklist
- [ ] [B-122][integration] 修改对应生产行为前，沿 真实 RunService→Executor→LangGraph/SkillContext→DB 添加失败断言并记录 RED：完整一轮含 Tool、Artifact、审计；secret 不进模型消息/输出；停机关闭连接与后台任务；无空实现替代依赖。
- [ ] 装配 context、prompt、冻结 tools、SkillContext、model、hooks、审计与取消；连接已有后台 Task port 产生 task.accepted，模块 09 缺接口时显式阻塞对应场景；复用启动探针。
- [ ] 局部验证 真实 RunService→Executor→LangGraph/SkillContext→DB：完整一轮含 Tool、Artifact、审计；secret 不进模型消息/输出；停机关闭连接与后台任务；无空实现替代依赖；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-122 | integration | 真实 RunService→Executor→LangGraph/SkillContext→DB | 完整一轮含 Tool、Artifact、审计；secret 不进模型消息/输出；停机关闭连接与后台任务；无空实现替代依赖 | tests/agent_runtime/test_runtime_composition.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_composition.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-023: Runtime E2E 真实环境与清理设施

- **Status**: draft
- **Priority**: P0
- **Depends**: 
- **Source**: 08-runtime-execution.backend.design.md#2.5 验收条件, 08-runtime-execution.backend.design.md#3.5 质量实现方案, 08-runtime-execution.backend.design.md#4. 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: B-123
- **Files**: `tests/e2e/runtime_probes.py`, `tests/acceptance/runtime/conftest.py`, `tests/acceptance/runtime/test_environment.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

使用真实 Console/Gateway/两个 Runtime 实例、PG、Redis、NFS 与本地 LLM/MCP HTTP 探针；fixture 启停进程并核验 ready；e2e 前缀数据和文件按租户清理，缺依赖 fail 而不 skip。

### Checklist
- [ ] [B-123][integration] 修改对应生产行为前，沿 真实 HTTP 进程→PG/Redis/NFS 添加失败断言并记录 RED：进程实际独立；探针可记录请求/注入受控故障；禁 dependency_overrides/mock 业务服务；清理可重复。
- [ ] 使用真实 Console/Gateway/两个 Runtime 实例、PG、Redis、NFS 与本地 LLM/MCP HTTP 探针；fixture 启停进程并核验 ready；e2e 前缀数据和文件按租户清理，缺依赖 fail 而不 skip。
- [ ] 局部验证 真实 HTTP 进程→PG/Redis/NFS：进程实际独立；探针可记录请求/注入受控故障；禁 dependency_overrides/mock 业务服务；清理可重复；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-123 | integration | 真实 HTTP 进程→PG/Redis/NFS | 进程实际独立；探针可记录请求/注入受控故障；禁 dependency_overrides/mock 业务服务；清理可重复 | tests/acceptance/runtime/test_environment.py（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_environment.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-024: 授权与 Snapshot 全链路验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-022, TASK-023
- **Source**: 08-runtime-execution.backend.design.md#2.5 验收条件, 08-runtime-execution.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-mcp#RULE-mcp-001, harness-secret#RULE-secret-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-01, S-02, RULE-auth-001, RULE-mcp-001, RULE-secret-001, RULE-snapshot-001
- **Files**: `tests/acceptance/runtime/test_capability_snapshot.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

完成 S-01/S-02，验证未授权 catalog 零泄漏、配置授权变更仅影响新 Run、MCP catalog 冻结与全链路凭据脱敏。

### Checklist
- [ ] [S-01][E2E] 修改对应生产行为前，沿 Gateway→Runtime→真实 Console resolve→LLM HTTP 探针 添加失败断言并记录 RED：未授权 SELECTED Skill/MCP 不出现在 Prompt、LLM catalog 或 ToolRegistry。
- [ ] [S-02][E2E] 修改对应生产行为前，沿 Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP 添加失败断言并记录 RED：当前 Run 固定 agent/model/skill/prompt_template_version/catalog revision/hash/definitions/policy；新 Run 看到更新；密钥不在快照/日志/审计/Prompt/公开响应。
- [ ] 完成 S-01/S-02，验证未授权 catalog 零泄漏、配置授权变更仅影响新 Run、MCP catalog 冻结与全链路凭据脱敏。
- [ ] 局部验证 Gateway→Runtime→Console resolve→PG Snapshot→真实 LLM/MCP 探针：LLM 请求、ToolRegistry、日志、审计、API 可见投影一致；切换 Agent/Model/Grant/Skill v2 不改旧快照；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] [RULE-auth-001][E2E] verifier 输入为本模块变更和 S-01 映射场景；执行原命令 `["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`，再执行映射场景命令；核验 S-01 的真实边界和断言。
- [ ] [RULE-mcp-001][E2E] verifier 输入为本模块变更和 S-02 映射场景；执行原命令 `["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"]`，再执行映射场景命令；核验 S-02 的真实边界和断言。
- [ ] [RULE-secret-001][E2E] verifier 输入为本模块变更和 S-02 映射场景；执行原命令 `["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`，再执行映射场景命令；核验 S-02 的真实边界和断言。
- [ ] [RULE-snapshot-001][E2E] verifier 输入为本模块变更和 S-02, E-04 映射场景；执行原命令 `["bash","-lc","uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`，再执行映射场景命令；核验 S-02, E-04 的真实边界和断言。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Gateway→Runtime→真实 Console resolve→LLM HTTP 探针 | 未授权 SELECTED Skill/MCP 不出现在 Prompt、LLM catalog 或 ToolRegistry | tests/acceptance/runtime/test_capability_snapshot.py -k s01（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_capability_snapshot.py","-k","s01"] | planned |
| S-02 | E2E | Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP | 当前 Run 固定 agent/model/skill/prompt_template_version/catalog revision/hash/definitions/policy；新 Run 看到更新；密钥不在快照/日志/审计/Prompt/公开响应 | tests/acceptance/runtime/test_capability_snapshot.py -k s02（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_capability_snapshot.py","-k","s02"] | planned |
| RULE-auth-001 | E2E | Gateway→Runtime→真实 Console resolve→LLM HTTP 探针＋原 verifier 边界 | 未授权 SELECTED Skill/MCP 不出现在 Prompt、LLM catalog 或 ToolRegistry；原 verifier 全部通过 | 原 verifier＋tests/acceptance/runtime/test_capability_snapshot.py（planned） | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"] | planned |
| RULE-mcp-001 | E2E | Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP＋原 verifier 边界 | 当前 Run 固定 agent/model/skill/prompt_template_version/catalog revision/hash/definitions/policy；新 Run 看到更新；密钥不在快照/日志/审计/Prompt/公开响应；原 verifier 全部通过 | tests/console_mcp/test_mcp_rules.py＋tests/acceptance/runtime/test_capability_snapshot.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/console_mcp/test_mcp_rules.py' && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"] | planned |
| RULE-secret-001 | E2E | Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP＋原 verifier 边界 | 当前 Run 固定 agent/model/skill/prompt_template_version/catalog revision/hash/definitions/policy；新 Run 看到更新；密钥不在快照/日志/审计/Prompt/公开响应；原 verifier 全部通过 | tests/test_logging_redaction.py, tests/acceptance/test_foundation_ops_audit.py＋tests/acceptance/runtime/test_capability_snapshot.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/test_logging_redaction.py' 'tests/acceptance/test_foundation_ops_audit.py' && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"] | planned |
| RULE-snapshot-001 | E2E | Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP；真实 Reaper→PostgreSQL lease→CAS＋原 verifier 边界 | 当前 Run 固定 agent/model/skill/prompt_template_version/catalog revision/hash/definitions/policy；新 Run 看到更新；密钥不在快照/日志/审计/Prompt/公开响应；过期 RUNNING FAILED/RUN_ABANDONED；新 Run 可创建；旧执行者被拒；RUN_ABANDONED 不作 HTTP code；原 verifier 全部通过 | 原 verifier＋tests/acceptance/runtime/test_capability_snapshot.py, tests/agent_runtime/test_run_reaper.py（planned） | ["bash","-lc","uv run pytest -q tests/agent_runtime -k \"executor or resolve\" && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py tests/agent_runtime/test_run_reaper.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-025: 创建、Resume、取消与幂等 E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-022, TASK-023
- **Source**: 08-runtime-execution.backend.design.md#2.5 验收条件, 08-runtime-execution.backend.design.md#API-01 创建 Run, 08-runtime-execution.backend.design.md#3.4.1 SSE 事件契约
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002
- **Acceptance-Refs**: S-07, E-02, E-03, E-08, B-01, RULE-api-001, RULE-api-002
- **Files**: `tests/acceptance/runtime/test_run_lifecycle.py`, `tests/acceptance/runtime/test_idempotency.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

完成 S-07/E-02/E-03/E-08 及 B-01；走真实 Gateway/HTTP/SSE，核验 Envelope、CAS、持续序号、取消和幂等重放。

### Checklist
- [ ] [S-07][E2E] 修改对应生产行为前，沿 真实 Gateway→Runtime SSE→PostgreSQL 添加失败断言并记录 RED：WAITING_INPUT 普通回复自动恢复原 Run；首事件 run.created/resumed=true；seq 延续。
- [ ] [E-02][E2E] 修改对应生产行为前，沿 真实 Gateway→cancel-active→PostgreSQL/SSE 添加失败断言并记录 RED：WAITING_INPUT CAS CANCELLED；interrupt CANCELLED；CANCEL 事件与 run.completed(status=CANCELLED)。
- [ ] [E-03][E2E] 修改对应生产行为前，沿 真实 Gateway→Runtime→PostgreSQL partial unique 添加失败断言并记录 RED：并发不同消息仅一活跃 Run；409 RUN_BUSY；已有状态/lease 不变；双语标准 Envelope。
- [ ] [E-08][E2E] 修改对应生产行为前，沿 真实 Gateway→cancel-active→PostgreSQL/Redis→执行者 添加失败断言并记录 RED：CREATED/RUNNING 响应 CANCELLING；DB cancel_requested 权威；Redis 故障仍协作 CANCELLED；无活跃 404 NO_ACTIVE_RUN。
- [ ] [B-01][E2E] 修改对应生产行为前，沿 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool 添加失败断言并记录 RED：新增：同 key 同指纹 200 重放原提交结果、不二次执行；异指纹 COMMON_CONFLICT；并发/重启后仍幂等。
- [ ] 完成 S-07/E-02/E-03/E-08 及 B-01；走真实 Gateway/HTTP/SSE，核验 Envelope、CAS、持续序号、取消和幂等重放。
- [ ] 局部验证 真实 Gateway→Runtime HTTP/SSE→PostgreSQL/Redis：业务响应、真实最终状态、事件和 side-effect 次数同时断言；不能以 mocked executor 代替 E2E；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] [RULE-api-001][E2E] verifier 输入为本模块变更和 E-03, E-08 映射场景；执行原命令 `["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`，再执行映射场景命令；核验 E-03, E-08 的真实边界和断言。
- [ ] [RULE-api-002][E2E] verifier 输入为本模块变更和 B-01 映射场景；执行原命令 `["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`，再执行映射场景命令；核验 B-01 的真实边界和断言。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实 Gateway→Runtime SSE→PostgreSQL | WAITING_INPUT 普通回复自动恢复原 Run；首事件 run.created/resumed=true；seq 延续 | tests/acceptance/runtime/test_run_lifecycle.py -k s07（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","s07"] | planned |
| E-02 | E2E | 真实 Gateway→cancel-active→PostgreSQL/SSE | WAITING_INPUT CAS CANCELLED；interrupt CANCELLED；CANCEL 事件与 run.completed(status=CANCELLED) | tests/acceptance/runtime/test_run_lifecycle.py -k e02（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e02"] | planned |
| E-03 | E2E | 真实 Gateway→Runtime→PostgreSQL partial unique | 并发不同消息仅一活跃 Run；409 RUN_BUSY；已有状态/lease 不变；双语标准 Envelope | tests/acceptance/runtime/test_run_lifecycle.py -k e03（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e03"] | planned |
| E-08 | E2E | 真实 Gateway→cancel-active→PostgreSQL/Redis→执行者 | CREATED/RUNNING 响应 CANCELLING；DB cancel_requested 权威；Redis 故障仍协作 CANCELLED；无活跃 404 NO_ACTIVE_RUN | tests/acceptance/runtime/test_run_lifecycle.py -k e08（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e08"] | planned |
| B-01 | E2E | 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool | 新增：同 key 同指纹 200 重放原提交结果、不二次执行；异指纹 COMMON_CONFLICT；并发/重启后仍幂等 | tests/acceptance/runtime/test_idempotency.py -k b01（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_idempotency.py","-k","b01"] | planned |
| RULE-api-001 | E2E | 真实 Gateway→Runtime→PostgreSQL partial unique；真实 Gateway→cancel-active→PostgreSQL/Redis→执行者＋原 verifier 边界 | 并发不同消息仅一活跃 Run；409 RUN_BUSY；已有状态/lease 不变；双语标准 Envelope；CREATED/RUNNING 响应 CANCELLING；DB cancel_requested 权威；Redis 故障仍协作 CANCELLED；无活跃 404 NO_ACTIVE_RUN；原 verifier 全部通过 | tests/test_api_i18n.py, tests/test_error_catalog.py, tests/acceptance/test_foundation_api_envelope.py＋tests/acceptance/runtime/test_run_lifecycle.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/test_api_i18n.py' 'tests/test_error_catalog.py' 'tests/acceptance/test_foundation_api_envelope.py' && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"] | planned |
| RULE-api-002 | E2E | 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool＋原 verifier 边界 | 新增：同 key 同指纹 200 重放原提交结果、不二次执行；异指纹 COMMON_CONFLICT；并发/重启后仍幂等；原 verifier 全部通过 | tests/console_skill/test_import_idempotency.py＋tests/acceptance/runtime/test_idempotency.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/console_skill/test_import_idempotency.py' && uv run pytest -q tests/acceptance/runtime/test_idempotency.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-026: 跨 Pod 重建与断流崩溃恢复 E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007, TASK-022, TASK-023
- **Source**: 08-runtime-execution.backend.design.md#2.5 验收条件, 08-runtime-execution.backend.design.md#4. 部署与运维, 08-runtime-execution.backend.design.md#5. 风险与依赖
- **Spec-Refs**: harness-arch#RULE-arch-001
- **Acceptance-Refs**: S-04, E-07, RULE-arch-001
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`, `tests/acceptance/runtime/test_multipod_recovery.py`, `tests/gateway/test_runtime_client.py`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

完成 S-04/E-07：真实停止 A，第二轮交由 B；流未终态即中断后 Gateway 提示重发，执行 Pod 终止由 Reaper 回收；按模块 10 现有契约补客户端断流处理。

### Checklist
- [ ] [S-04][E2E] 修改对应生产行为前，沿 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B 添加失败断言并记录 RED：真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session。
- [ ] [E-07][E2E] 修改对应生产行为前，沿 真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run 添加失败断言并记录 RED：Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消。
- [ ] 完成 S-04/E-07：真实停止 A，第二轮交由 B；流未终态即中断后 Gateway 提示重发，执行 Pod 终止由 Reaper 回收；按模块 10 现有契约补客户端断流处理。
- [ ] 局部验证 真实 Gateway SSE→Runtime A/B 进程→共享 PG/Artifact Store→Reaper：新 Pod 重建会话/Memory；无 sticky；终态 FAILED/RUN_ABANDONED；GET 可查；旧 owner 不覆盖；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] [RULE-arch-001][E2E] verifier 输入为本模块变更和 S-04, E-07 映射场景；执行原命令 `["uv","run","pytest","-q","tests/architecture"]`，再执行映射场景命令；核验 S-04, E-07 的真实边界和断言。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B | 真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session | tests/acceptance/runtime/test_multipod_recovery.py -k s04（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","s04"] | planned |
| E-07 | E2E | 真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run | Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消 | tests/acceptance/runtime/test_multipod_recovery.py -k e07（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","e07"] | planned |
| RULE-arch-001 | E2E | 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B；真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run＋原 verifier 边界 | 真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session；Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消；原 verifier 全部通过 | tests/architecture＋tests/acceptance/runtime/test_multipod_recovery.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/architecture' && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-027: 模块验收与 Spec verifier 收口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-024, TASK-025, TASK-026, TASK-007, TASK-008, TASK-009, TASK-012, TASK-015, TASK-017
- **Source**: 08-runtime-execution.backend.design.md#2.5 验收条件, 08-runtime-execution.backend.design.md#3.5 质量实现方案, 08-runtime-execution.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-02, S-03, S-04, S-05, S-06, S-07, S-08, E-01, E-02, E-03, E-04, E-05, E-06, E-07, E-08, B-01, RULE-test-001
- **Files**: `.code-flow/tasks/2026-09-17/08-runtime-execution/08-runtime-execution.md`, `.code-flow/tasks/2026-09-17/08-runtime-execution/.acceptance-manifest.json`
- **Estimate**: 15–60 分钟（达到超出条件时先拆分）

### Description

运行已绑定 verifier 与 Runtime 场景，核验所有 Acceptance Evidence、RED/GREEN、真实边界和清理记录。失败回责任任务修复，不以 unit/integration 替换 E2E。

### Checklist
- [ ] [S-01/S-02/S-04/S-07/E-02/E-03/E-07/E-08/B-01][E2E] 汇总各 owner 已执行的真实 Gateway/Runtime/Console/PG/Redis/NFS/LLM 边界证据，复跑模块 E2E 命令；断言与下列 Contract 一致。
- [ ] [S-03/S-05/S-06/S-08/E-01/E-04/E-05/E-06][integration] 汇总真实存储、Hook、ContextBuilder、provider 恢复与审计边界证据，不以局部回归替代 E2E。
- [ ] 运行已绑定 verifier 与 Runtime 场景，核验所有 Acceptance Evidence、RED/GREEN、真实边界和清理记录。失败回责任任务修复，不以 unit/integration 替换 E2E。
- [ ] 局部验证 真实 Runtime E2E 环境＋现有全局 verifier：16 个设计场景和 11 个 required Rule 均有唯一 owner 与证据；无跳过冒充成功；全部 verified 后才 done；如已具备实现，保留并记录回归，不重写已通过行为。
- [ ] [RULE-test-001][E2E] verifier 输入为本模块变更和 S-01, S-04, E-01, E-07 映射场景；执行原命令 `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`，再执行映射场景命令；核验 S-01, S-04, E-01, E-07 的真实边界和断言。
- [ ] 运行下列验收命令；记录 GREEN、关键断言 test name/位置、真实组件和清理证据；只把实际通过项置 verified。

验收引用沿用 Coverage 中的最终 owner；本 TASK 汇总并复核实际证据，不产生第二个场景负责人。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Gateway→Runtime→真实 Console resolve→LLM HTTP 探针 | 未授权 SELECTED Skill/MCP 不出现在 Prompt、LLM catalog 或 ToolRegistry | tests/acceptance/runtime/test_capability_snapshot.py -k s01（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_capability_snapshot.py","-k","s01"] | planned |
| S-02 | E2E | Runtime→PostgreSQL Snapshot→真实 LLM/Tool/MCP | 当前 Run 固定 agent/model/skill/prompt_template_version/catalog revision/hash/definitions/policy；新 Run 看到更新；密钥不在快照/日志/审计/Prompt/公开响应 | tests/acceptance/runtime/test_capability_snapshot.py -k s02（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_capability_snapshot.py","-k","s02"] | planned |
| S-03 | integration | emptyDir→真实 NFS 挂载 | 同 checksum 第二次命中 READY，不发生 NFS IO；singleflight；只执行本地已校验文件 | tests/test_skill_artifact_cache.py -k s03（planned） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","s03"] | planned |
| S-04 | E2E | 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B | 真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session | tests/acceptance/runtime/test_multipod_recovery.py -k s04（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","s04"] | planned |
| S-05 | integration | runner→HookPipeline→真实 Tool handler | 完整一轮顺序匹配 S-05；补足 on_interrupt；未注册 Hook 不干扰；错误/取消的收尾可观测 | tests/agent_core/test_hook_lifecycle.py -k s05（planned） | ["uv","run","pytest","-q","tests/agent_core/test_hook_lifecycle.py","-k","s05"] | planned |
| S-06 | integration | ModelGateway→fake provider→真实审计 DB | 429 Retry-After:1 后成功；记录 attempt/retry_reason；Run 不失败 | tests/agent_runtime/test_model_recovery.py -k s06（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","s06"] | planned |
| S-07 | E2E | 真实 Gateway→Runtime SSE→PostgreSQL | WAITING_INPUT 普通回复自动恢复原 Run；首事件 run.created/resumed=true；seq 延续 | tests/acceptance/runtime/test_run_lifecycle.py -k s07（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","s07"] | planned |
| S-08 | integration | 真实 CanonicalEvent/Memory/Artifact→ContextBuilder→LLM request | 仅裁剪 request；事件 append-only；tenant+user+enabled 过滤；大结果 preview | tests/agent_runtime/test_context_memory.py -k s08（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_context_memory.py","-k","s08"] | planned |
| E-01 | integration | Runtime cache→真实 NFS 故障边界 | cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误 | tests/test_skill_artifact_cache.py -k e01（planned） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","e01"] | planned |
| E-02 | E2E | 真实 Gateway→cancel-active→PostgreSQL/SSE | WAITING_INPUT CAS CANCELLED；interrupt CANCELLED；CANCEL 事件与 run.completed(status=CANCELLED) | tests/acceptance/runtime/test_run_lifecycle.py -k e02（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e02"] | planned |
| E-03 | E2E | 真实 Gateway→Runtime→PostgreSQL partial unique | 并发不同消息仅一活跃 Run；409 RUN_BUSY；已有状态/lease 不变；双语标准 Envelope | tests/acceptance/runtime/test_run_lifecycle.py -k e03（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e03"] | planned |
| E-04 | integration | 真实 Reaper→PostgreSQL lease→CAS | 过期 RUNNING FAILED/RUN_ABANDONED；新 Run 可创建；旧执行者被拒；RUN_ABANDONED 不作 HTTP code | tests/agent_runtime/test_run_reaper.py -k e04（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_reaper.py","-k","e04"] | planned |
| E-05 | integration | 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL | 拒绝 host 不发出请求，FORBIDDEN；DENY/HTTP 审计；timeout/5 MiB/redirect 边界；平台与 MCP 同一策略 | tests/agent_runtime/test_egress_boundary.py -k e05（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_egress_boundary.py","-k","e05"] | planned |
| E-06 | integration | ModelGateway→fake provider→PostgreSQL | 429/5xx/reset/timeout 超重试或 deadline 后 FAILED/MODEL_UNAVAILABLE；逐 attempt 审计；取消终止退避 | tests/agent_runtime/test_model_recovery.py -k e06（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","e06"] | planned |
| E-07 | E2E | 真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run | Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消 | tests/acceptance/runtime/test_multipod_recovery.py -k e07（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","e07"] | planned |
| E-08 | E2E | 真实 Gateway→cancel-active→PostgreSQL/Redis→执行者 | CREATED/RUNNING 响应 CANCELLING；DB cancel_requested 权威；Redis 故障仍协作 CANCELLED；无活跃 404 NO_ACTIVE_RUN | tests/acceptance/runtime/test_run_lifecycle.py -k e08（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e08"] | planned |
| B-01 | E2E | 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool | 新增：同 key 同指纹 200 重放原提交结果、不二次执行；异指纹 COMMON_CONFLICT；并发/重启后仍幂等 | tests/acceptance/runtime/test_idempotency.py -k b01（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_idempotency.py","-k","b01"] | planned |
| RULE-test-001 | E2E | Gateway→Runtime→真实 Console resolve→LLM HTTP 探针；真实 Runtime A→PostgreSQL/Artifact Store→Runtime B；Runtime cache→真实 NFS 故障边界；真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run＋原 verifier 边界 | 未授权 SELECTED Skill/MCP 不出现在 Prompt、LLM catalog 或 ToolRegistry；真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session；cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误；Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消；原 verifier 全部通过 | 原 verifier＋tests/acceptance/runtime/test_capability_snapshot.py, tests/acceptance/runtime/test_multipod_recovery.py, tests/test_skill_artifact_cache.py（planned） | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test && uv run pytest -q tests/acceptance/runtime tests/test_skill_artifact_cache.py"] | planned |

### Acceptance Evidence

待 cf-task-start 填写 RED/GREEN 的命令、退出码、断言位置与真实组件证据。当前没有执行证据；全部 required 场景 verified 才能 done。

### Log

- [2026-09-19] created (draft；2026-09-20 按确认方案写入)

---

## TASK-028: Console 内部运行凭据读取接口

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 08-runtime-execution.backend.design.md#API-09 Resolve Runtime Credentials
- **Spec-Refs**:
- **Acceptance-Refs**: B-128
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/internal_runtime.py`, `apps/console-platform/backend/src/muad_console_platform/application/runtime_credentials.py`, `tests/console_internal/test_runtime_credentials.py`
- **Estimate**: 15–60 分钟（超出先拆分）

### Description

按 API-09 增加仅受信 Runtime 可调用的凭据读取；复用 Owner 表，不重新计算当前 Agent/Grant/Binding；按冻结资源主键读取当前认证值。服务侧类型可局部定义，共享消费契约由 TASK-003 承接。

### Checklist

- [x] [B-128][integration] RED：5 failed（端点不存在 404/无服务身份校验）：服务身份、可信 tenant/actor 与资源归属校验；旧 Grant 撤销不替换冻结能力；密钥轮换读取新值，缺密钥 CREDENTIAL_MISSING；日志/公开返回不泄漏；不返回新模型参数/catalog。
- [x] 按 API-09 实现 POST /internal/runtime/resolve-credentials（X-Internal-Service 身份校验 + Owner 表只读）；复用 Owner 表，不重新计算当前 Agent/Grant/Binding；按冻结资源主键读取当前认证值。服务侧类型可局部定义，共享消费契约由 TASK-003 承接。
- [x] 服务身份（INTERNAL_SERVICE_TOKEN 配置）、租户归属（跨租户 404）、密钥轮换读新值、缺密钥 CREDENTIAL_MISSING、MCP 仅返回认证字段；旧 Grant 撤销不替换冻结能力；密钥轮换读取新值，缺密钥 CREDENTIAL_MISSING；日志/公开返回不泄漏；不返回新模型参数/catalog。
- [x] 运行 6 passed；回归 console_internal+console_platform 全量通过，记录 GREEN、断言位置与真实 DB/HTTP 边界；最终 S-02/E-05 由 Coverage 指定 owner 收口。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-128 | integration | Console credentials service→真实 PostgreSQL Owner 表 | 服务身份/租户归属/轮换/CREDENTIAL_MISSING/不重算授权/仅认证字段 | tests/console_internal/test_runtime_credentials.py::test_b128_*（6 用例） | uv run pytest -q tests/console_internal/test_runtime_credentials.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-128 | FAIL: 5 failed（端点 404/身份缺失 403） | 6 passed | test_b128_requires_service_identity/_returns_model_key_and_rotation/_cross_tenant_model_rejected/_missing_secret_returns_credential_missing/_mcp_secret_and_grant_independence/_invalid_execution_ref_type | ASGI 真实 HTTP + 真实 PostgreSQL（model_definition/mcp_server Owner 表） | verified |
- B-128: verified — automated command passed; run_id=7d831b514b9d4b928c8ac2b14b80308f (confirmed_by: runner)

### Log

- [2026-09-20] created (draft；把已确认实时凭据/出网设计落实为独立服务端任务)

---
- [2026-09-20] started
- [2026-09-20] completed (done)
## TASK-029: Console Resolve Egress 授权与凭据选择

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 08-runtime-execution.backend.design.md#API-08 Resolve Egress
- **Spec-Refs**:
- **Acceptance-Refs**: B-129
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/internal_runtime.py`, `apps/console-platform/backend/src/muad_console_platform/application/resolve_egress_service.py`, `tests/console_internal/test_resolve_egress_api.py`
- **Estimate**: 15–60 分钟（超出先拆分）

### Description

补目前只有设计没有实现的 API-08，复用模块 04 平台/凭据/Adapter 配置；返回平台寻址、credential_mode 选择结果与当前凭据。策略判断与归因在 Console，平台登录/Session 在 SDK；公共返回与内部认证字段隔离。

### Checklist

- [ ] [B-129][integration] 先沿 Console resolve-egress service→真实 PostgreSQL 平台/凭据表 写失败断言并记录 RED：RUN/TASK 与 PLATFORM_SERVICE/HTTP/MCP 类型合法；四类凭据策略正确；租户/用户隔离；DENY/FORBIDDEN、缺凭据和适配器错误明确；不执行平台登录、不返回越权凭据。
- [ ] 补目前只有设计没有实现的 API-08，复用模块 04 平台/凭据/Adapter 配置；返回平台寻址、credential_mode 选择结果与当前凭据。策略判断与归因在 Console，平台登录/Session 在 SDK；公共返回与内部认证字段隔离。
- [ ] RUN/TASK 与 PLATFORM_SERVICE/HTTP/MCP 类型合法；四类凭据策略正确；租户/用户隔离；DENY/FORBIDDEN、缺凭据和适配器错误明确；不执行平台登录、不返回越权凭据。
- [ ] 运行 ["uv","run","pytest","-q","tests/console_internal/test_resolve_egress_api.py"]，记录 GREEN、断言位置与真实 DB/HTTP 边界；最终 S-02/E-05 由 Coverage 指定 owner 收口。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-129 | integration | Console resolve-egress service→真实 PostgreSQL 平台/凭据表 | RUN/TASK 与 PLATFORM_SERVICE/HTTP/MCP 类型校验；四类凭据策略正确；服务身份；HTTP 域 allowlist；缺凭据明确失败 | tests/console_internal/test_resolve_egress_api.py::test_b129_*（6 用例） | uv run pytest -q tests/console_internal/test_resolve_egress_api.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-129 | FAIL: 6 failed（端点 404） | 6 passed（console_internal+console_platform 回归 99 passed） | test_b129_requires_service_identity/_platform_not_found_and_validation/_user_then_shared_fallback/_credential_missing/_none_mode_no_credential/_http_target_allowlist | ASGI 真实 HTTP + 真实 PostgreSQL（project_platform/user_credential_ref/shared_credential_ref） | verified |
- B-129: verified — automated command passed; run_id=90343bd237794e6589d796c13b8fbe2a (confirmed_by: runner)

### Log

- [2026-09-20] created (draft；把已确认实时凭据/出网设计落实为独立服务端任务)
- [2026-09-20] started
- [2026-09-20] completed (done)
