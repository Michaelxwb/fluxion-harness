# Tasks: DFX、测试与验收

- **Source**: .code-flow/tasks/2026-09-17/14-dfx-acceptance/（唯一 design：14-dfx-acceptance.backend.design.md）
- **Created**: 2026-09-28
- **Updated**: 2026-09-28
- **Plan-State**: planned（用户已确认写入；manual 两例 S-13/E-10 与 S-06 的「Browser 臂不可执行」边界均已确认；各 TASK 保持 draft，功能与 E2E 验收尚未执行）

## Proposal

以 docs/09 为唯一 DFX 基线，把「测试分层与选择器、跨模块黄金旅程、契约一致性、故障与恢复矩阵、安全与脱敏验收、模型恢复、上线门禁清单」收敛为一套可执行、可追溯的验收体系：分层 marker 与执行基线（S-01）、契约与一致性聚合（S-02/S-03/B-01/B-02/B-04）、真实 PG+Redis 与真实服务链路的可靠性矩阵（S-04/S-11/E-01..E-06/B-03）、安全与脱敏全链路（E-07/E-08）、模型恢复（E-09）、跨模块黄金旅程（S-05..S-10/S-12）、上线门禁清单与企业微信真机（S-13/E-10）。本模块是跨模块测试与验收的唯一 owner：**只聚合与引用各模块场景，不复制、不重定义领域契约**（design §2.3/RULE-10），并补齐 design 明示的缺口（分层 marker 未登记、幂等指纹判别键存量漂移、门禁证据无机器清单）。

**基线现状（拆解时核对，影响任务形态）**：本仓已存在大量真实边界套件，DFX 的多数场景**先按引用承接**——`tests/acceptance/{runtime,task_schedule,im_gateway,audit_observability}/`（真实进程/真实 PG+Redis/真实 SSE，`runtime/test_multipod_recovery.py` 已做 SIGKILL→Reaper 接管）、`tests/agent_worker/`（`test_worker_leases.py`/`test_task_deadline.py`/`test_scheduler_misfire.py`/`test_tenant_guard.py`）、`tests/agent_runtime/`（`test_sse.py`/`test_snapshot_freeze.py`/`test_run_reaper.py`/`test_model_recovery.py`）、`tests/architecture/`、`tests/test_error_catalog.py`、`tests/test_api_i18n.py`、`tests/test_logging_redaction.py`、`tests/test_contracts.py`、`tests/console_platform/test_user_side_relations.py`。**缺口**：`pyproject.toml` 的 `[tool.pytest.ini_options]` **没有任何 markers 登记**（design 技术债①）；design §3.4.3 登记的幂等指纹存量漂移（console 侧 `agent_service`/`mcp_service`/`skill_service` 三处 `_fingerprint` 仍以 `|` 拼接且不含 `endpoint`/`tenant_id`）是**真实 RED**；`tests/dfx_inventory.py` 不存在。共 **13 个原子任务**：P0 13（无 P1/P2）；10 条 required 规则各有唯一责任 TASK。

## Design Alignment

本次拆解作出并登记以下对齐决定（均为「按 design 原文与仓库事实落位」，不代表实现或 verifier 已通过）：

- **场景执行主体：引用 vs 自有**。design §3.4.3 已给出「现有落点」的场景（S-02 错误码/双语、B-01 分页边界、B-04 前端 i18n、B-02 SSE 解析器、E-02 Redis 降级、B-03 misfire、E-04 多 Pod 回收、S-11 投递去重、E-06 投递重试、E-09 模型恢复、E-07 脱敏、E-08 租户/越权、S-05 绑定、S-06 流式、S-09 恢复/取消）**以既有套件的真实 argv 承接**（RULE-10「引用而非复制」）；只有 design 断言无任何在盘承载者、或 DFX 必须作为跨模块唯一 owner 的场景，才新建 DFX 自有聚合用例（S-01、S-03、E-01、E-03、E-05、S-07、S-08、S-10、S-12、S-13）。两类都在 Acceptance Contract 的「测试文件 / 用例」列显式登记，不混淆。
- **S-06「Browser」臂记录为显式边界（需用户裁决）**：design §2.4.2 给 S-06 的真实边界是 `Gateway → Runtime SSE → Browser`，但本仓前端**不存在 SSE 消费面**（`apps/console-platform/frontend/src` 无 `EventSource`/`ReadableStream`），自建浏览器流式页面属「实现业务功能」、在 design §2.3 Out of Scope 内。故本草案只以真实 Gateway→Runtime HTTP/SSE 链路（`tests/acceptance/im_gateway/test_runtime_stream.py` + 自有聚合用例）取证，**不降级为 unit/integration**（层级仍为 E2E），但在该场景的 Acceptance Evidence 中必须登记「Browser 臂不可执行」这一事实，不冒充完整验证。
- **design §2.4.1 的 RULE-01..RULE-10 不建 RULE 行**：它们是 design 级约束，不是分域 spec 规则。按 design 自注（「验证场景」列），它们映射进对应场景的「关键真实边界」：RULE-01/RULE-02 → 全部场景的层级与不得 mock 列；RULE-03 → S-02/S-03；RULE-04/RULE-05 → E-04/E-05/S-11/E-06；RULE-06/RULE-07 → E-07/E-08；RULE-08 → E-09；RULE-09 → S-13；RULE-10 → 本文件全篇。Coverage 表的 RULE 行**只**放 10 条 required 分域 spec 规则（短 ref 为行键，同 13-console-auth）。
- **design §2.3 已登记的 9 条有意妥协/技术债不在此修复，逐条写成显式边界**：① pytest 分层 marker 未登记（TASK-001 只登记 marker 与执行基线，**不**为全仓既有用例补 marker，该存量按边界登记）；② Redis 部分既有单测仍用替身（DFX 只要求集成/E2E 层接真实 Redis）；③ NFS 慢故障注入依赖环境能力（E-03 的「NFS 高延迟」部分按人工/环境边界登记）；④ WeCom 真机维持 manual；⑤ 租户隔离已全量收敛（2026-09-28）——**是已解决项而非缺口**，TASK-008 只断言该口径，不重复改动；⑥ IM Gateway 零 `declare_metric`、四个 k8s 清单探针均指 `/healthz`（TASK-002 只断言现状口径与 `tests/architecture` verifier，**不**改部署清单）；⑦ snapshot「配置/授权变更只影响后续新 Run」代码证实但指定 verifier 缺该断言（TASK-012 补该断言并登记原 verifier 缺位）；⑧ `harness-secret` 的「不得进入」面只有一部分被 canary 机检（TASK-007 按 canary 实际覆盖范围登记，不宣称全覆盖）；⑨ 门禁命令输出不得接入 `head` 一类提前关闭的管道（TASK-013 只在收口清单里做人工纪律登记——本条 design 自注**无机器约束**，不得写成已机检）。
- **`manual` 两例保持 manual（需用户明确确认）**：S-13（docs/09 §14 门禁清单核对）与 E-10（企业微信真机重连，需真实凭据）。E-10 在任何情况下都**不得**写成「通过」——必须在有真实凭据的环境复验后由用户确认，环境受限时记录原因；S-13 是人工门禁核对，不产出自动 argv。
- **规则责任落位中的两处判断**（供用户复核）：`harness-arch#RULE-arch-001` 归 TASK-002（design §3.4.3 把架构门禁放在 contract 层的 `tests/architecture/`），其 design 声明的另一验证场景 E-01 由 TASK-004 拥有；`harness-auth#RULE-auth-001` 归 TASK-008（design §3.4.5 的凭据/CSRF/租户谓词落点与 verifier 均在此，其 design 声明的另一验证场景 S-10 由 TASK-011 拥有）。规则 owner 与场景 owner 分离是允许的（场景可有多个引用者，但只有唯一最终负责人）。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | 验收 | Checklist |
|---|---|---|---|---|---|---|
| TASK-001 | P0 | 测试分层基线与契约执行聚合（unit/错误码/分页/i18n） | 无 | backend 2.4.1；3.4.1；3.4.3；3.3 形态 B | S-01(unit), S-02(contract), B-01(contract), B-04(contract), RULE-api-001(contract) | 7 |
| TASK-002 | P0 | 契约一致性、关系形态与架构门禁（parity/幂等指纹） | 无 | backend 3.4.1；3.4.3；Spec Compliance Matrix | S-03(contract), B-02(contract), RULE-api-002(contract), RULE-rel-001(contract), RULE-arch-001(contract) | 8 |
| TASK-003 | P0 | 可靠性验收基座与 Worker lease/claim | 无 | backend 3.4.1；3.4.4；3.1 | S-04(integration), RULE-worker-001(integration) | 7 |
| TASK-004 | P0 | 依赖故障矩阵（PG/Redis/Artifact Store） | 003 | backend 3.4.4；2.4.2 | E-01(integration), E-02(integration), E-03(integration) | 7 |
| TASK-005 | P0 | 租约回收、deadline sweep 与 Schedule 边界 | 003 | backend 3.4.4；2.4.2 | E-04(integration), E-05(integration), B-03(integration) | 8 |
| TASK-006 | P0 | 最终投递去重与重试（Worker→Gateway→Redis） | 003 | backend 3.4.4；3.4.3 | S-11(integration), E-06(integration) | 7 |
| TASK-007 | P0 | 安全验收：Egress 拒绝、Secret 全链路与日志脱敏 | 003 | backend 3.4.5；3.4.3 日志脱敏；Spec Compliance Matrix | E-07(integration), RULE-secret-001(integration), RULE-log-001(integration) | 8 |
| TASK-008 | P0 | API 安全与租户隔离（RBAC/CSRF/ADMIN 门控/租户谓词） | 003 | backend 3.4.5；Spec Compliance Matrix | E-08(integration), RULE-auth-001(integration) | 8 |
| TASK-009 | P0 | 模型恢复验收（429/5xx/超时/deadline/cancel） | 003 | backend 3.4.6；2.4.2 | E-09(integration) | 6 |
| TASK-010 | P0 | 黄金旅程：绑定、流式与中断恢复/取消 | 001, 003 | backend 3.4.2；2.4.2 | S-05(E2E), S-06(E2E), S-09(E2E) | 7 |
| TASK-011 | P0 | 执行路由、批量 fan-out/fan-in 与授权可见性/多 IM 路由 | 003, 010 | backend 3.4.2；3.4.5；2.4.2 | S-07(E2E), S-08(E2E), S-10(E2E), E-10(manual) | 8 |
| TASK-012 | P0 | 无状态与 Snapshot 确定性（A/B Pod） | 003, 010 | backend 3.4.2；3.4.4；Spec Compliance Matrix | S-12(E2E), RULE-snapshot-001(integration) | 6 |
| TASK-013 | P0 | 上线门禁清单、收口清单与仓库级 verifier | 001..012 | backend 3.4.7；3.4.8；4.2；Spec Compliance Matrix | S-13(manual), RULE-test-001(E2E) | 7 |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | unit | 纯逻辑/状态机（无 IO） | TASK-001 | verified | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_layer_baseline.py","-k","s01"] | . | 600 |  |
| S-02 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | Enum ↔ YAML ↔ 源码扫描 | TASK-001 | verified | ["uv","run","pytest","-q","tests/test_error_catalog.py","tests/test_api_i18n.py"] | . | 600 |  |
| S-03 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | ORM ↔ 迁移 ↔ OpenAPI | TASK-002 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/architecture"] | . | 1200 |  |
| S-04 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | 真实 PostgreSQL + Redis | TASK-003 | planned | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_reliability.py","-k","s04"] | . | 900 |  |
| S-05 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Browser/HTTP → Console → PG → IM Gateway | TASK-010 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s05 && uv run pytest -q tests/acceptance/im_gateway/test_binding.py"] | . | 1200 |  |
| S-06 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Gateway → Runtime SSE → Browser | TASK-010 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s06 && uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py"] | . | 1200 |  |
| S-07 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Runtime → Worker → Schedule | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s07 && uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_schedules.py"] | . | 1200 |  |
| S-08 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Worker Parent/Child → fan-in | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s08 && uv run pytest -q tests/acceptance/task_schedule/test_batch.py"] | . | 1200 |  |
| S-09 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Runtime interrupt → resume/cancel | TASK-010 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s09 && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"] | . | 1200 |  |
| S-10 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | 授权解析 → Prompt/ToolRegistry → IM 路由 | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_authorization_scope.py -k s10 && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"] | . | 1200 |  |
| S-11 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Worker → Gateway `/internal/deliveries` → Redis | TASK-006 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k s11 && uv run pytest -q tests/acceptance/task_schedule/test_delivery.py"] | . | 900 |  |
| S-12 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Runtime A/B Pod → PostgreSQL + Artifact Store | TASK-012 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_stateless.py -k s12 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"] | . | 1200 |  |
| S-13 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | manual | CI/环境全链路 | TASK-013 | planned | - | . | 60 |  |
| E-01 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Service → PostgreSQL | TASK-004 | planned | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_fault_matrix.py","-k","e01"] | . | 900 |  |
| E-02 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Redis → PG | TASK-004 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e02 && uv run pytest -q tests/acceptance/im_gateway/test_redis_degradation.py"] | . | 900 |  |
| E-03 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | emptyDir cache → NFS | TASK-004 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e03 && uv run pytest -q tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py"] | . | 900 |  |
| E-04 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | lease → Reaper → CAS | TASK-005 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e04 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py"] | . | 1200 |  |
| E-05 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Scheduler sweep → PG | TASK-005 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e05 && uv run pytest -q tests/agent_worker/test_task_deadline.py"] | . | 900 |  |
| E-06 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Worker → Gateway → Redis | TASK-006 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k e06 && uv run pytest -q tests/acceptance/im_gateway/test_worker_delivery.py"] | . | 900 |  |
| E-07 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Egress Boundary → Audit/日志/Snapshot | TASK-007 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_security.py -k e07 && uv run pytest -q tests/acceptance/test_secret_consumers.py tests/acceptance/im_gateway/test_secrets_and_readiness.py"] | . | 1200 |  |
| E-08 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | API → RBAC/CSRF/租户谓词 | TASK-008 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_api_security.py -k e08 && uv run pytest -q tests/console_auth/test_rbac.py tests/console_platform/test_credentials_api.py tests/agent_worker/test_tenant_guard.py"] | . | 900 |  |
| E-09 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | ModelGateway → Provider | TASK-009 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_model_recovery.py -k e09 && uv run pytest -q tests/agent_runtime/test_model_recovery.py"] | . | 900 |  |
| E-10 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | manual | Gateway WS → 企业微信 | TASK-011 | planned | - | . | 60 |  |
| B-01 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | api-kit paginate → API Query | TASK-001 | verified | ["uv","run","pytest","-q","tests/test_error_catalog.py","-k","paginate"] | . | 300 |  |
| B-02 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | SSE 解析器 → Runtime | TASK-002 | planned | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py"] | . | 300 |  |
| B-03 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Scheduler → Schedule | TASK-005 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k b03 && uv run pytest -q tests/agent_worker/test_scheduler_misfire.py"] | . | 900 |  |
| B-04 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | locale 资源 ↔ API catalog | TASK-001 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | . | 600 |  |
| RULE-api-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | contract | 统一封套/分页边界/catalog 错误码 + 原 verifier 真实边界 | TASK-001 | verified | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | . | 300 |  |
| RULE-api-002 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP → 幂等表(PostgreSQL) + 原 verifier 真实边界 | TASK-002 | planned | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | . | 300 |  |
| RULE-arch-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 四部署单元/无状态/依赖方向 + 原 verifier 真实边界 | TASK-002 | planned | ["uv","run","pytest","-q","tests/architecture"] | . | 300 |  |
| RULE-rel-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP 单端点原子变更 + 原 verifier 真实边界 | TASK-002 | planned | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] | . | 300 |  |
| RULE-worker-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 PG 权威源 + Redis 降级 + 原 verifier 真实边界 | TASK-003 | planned | ["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | . | 600 |  |
| RULE-secret-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 密钥明文只存 Owner 表/三处受控出口 + 原 verifier 真实边界 | TASK-007 | planned | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | . | 300 |  |
| RULE-log-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | logging-kit 唯一出口与双通道脱敏 + 原 verifier 真实边界 | TASK-007 | planned | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | . | 300 |  |
| RULE-auth-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | TASK-008 | planned | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | . | 600 |  |
| RULE-snapshot-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 冻结快照/终态 CAS + 原 verifier 真实边界 | TASK-012 | planned | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | . | 600 |  |
| RULE-test-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | E2E | 仓库级真实 E2E（HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | TASK-013 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 |  |

> 本表覆盖 design §2.4.2 的全部 **27 个场景**（S-01..S-13、E-01..E-10、B-01..B-04）与 **10 条 required Spec Rule**，共 **37 行**；每个场景与每条规则有且仅有一个最终负责人（规则行以短 ref 为行键，与 13-console-auth 一致）。**规则行的「测试层级」列沿用 13-console-auth 的口径填 `integration`（`RULE-test-001` 为 `E2E`）**——它表达的是「verifier 命令的执行层级」，规则的验证力度以 argv 指向的 verifier 套件为准，不因此把场景行的 `contract`/`unit` 层级改写。design §2.4.1 的 RULE-01..RULE-10 是 design 级约束，**不建 RULE 行**（映射见 Design Alignment）。测试层级与 design 一致、**不降级**；`manual` 两行（S-13、E-10）保持 manual 且无 argv，**必须经用户明确确认**，E-10 未经真实凭据复验不得写成通过。`depends_on` 列统一留空（场景间顺序由 TASK 依赖表达，manifest 亦按空数组锁定）。E2E 行不接 `head` 一类提前关闭的管道（design §3.4.8 已登记该纪律**无机器约束**）。

---

## TASK-001: 测试分层基线与契约执行聚合（unit/错误码/分页/i18n）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 14-dfx-acceptance.backend.design.md#2.4.1 业务规则与约束, 14-dfx-acceptance.backend.design.md#3.4.1 测试分层矩阵, 14-dfx-acceptance.backend.design.md#3.4.3 契约与一致性测试清单, 14-dfx-acceptance.backend.design.md#3.3 接口设计
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: S-01, S-02, B-01, B-04, RULE-api-001
- **Files**: `pyproject.toml`, `tests/acceptance/dfx/__init__.py`, `tests/acceptance/dfx/test_dfx_layer_baseline.py`
- **Estimate**: 半天级（含 marker 登记与分层选择器的真实边界取证）

### Description

建立 FEAT-01 的执行基线：在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 登记 `unit/contract/integration/e2e` 四类 marker，使分层选择器可执行；以 DFX 自有聚合用例断言「单元层在无 `DATABASE_URL`/无网络依赖下可全绿」「契约层聚合（错误码双向一致、分页默认形状、双语完整）来自既有落点而非复制」。design 技术债①（既有全量用例未逐条打 marker）**不在本任务修复**，只登记为题面边界。

### Checklist

- [x] [S-01][unit] 先写用例后改配置：以「纯逻辑/状态机、无 IO」为真实边界，断言分层选择器在没有 `DATABASE_URL`、且未起 PG/Redis 时**仍能收集并通过**（无 DB 连接尝试、无网络调用）；首跑必须先记录 RED（当前 `pyproject.toml` 无 marker 登记，`-m unit` 触发 unknown-marker 警告/空选择）。执行 argv：`["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_layer_baseline.py","-k","s01"]`。
- [x] [S-02][contract] 按 `Enum ↔ YAML ↔ 源码扫描` 真实边界断言错误码双向一致、无未登记码、每码双语与合法 `http_status`、`paginate` 默认形状正确；**断言直接引用既有落点**（`tests/test_error_catalog.py`、`tests/test_api_i18n.py`）并登记其 argv，不在本任务复制其断言。执行 argv：`["uv","run","pytest","-q","tests/test_error_catalog.py","tests/test_api_i18n.py"]`。
- [x] [B-01][contract] 以 `api-kit paginate → API Query` 为边界断言 `page=0`/`page_size=101` 返回 `COMMON_VALIDATION_ERROR`、`1`/`100` 通过、默认 `{items,page,page_size,total}`。执行 argv：`["uv","run","pytest","-q","tests/test_error_catalog.py","-k","paginate"]`。
- [x] [B-04][contract] 以 `locale 资源 ↔ API catalog` 为边界断言后端每码 zh-CN/en-US 非空、前端 locale key 双语一致（`make i18n-check` 通过）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`。
- [x] [RULE-api-001][contract] 作为唯一最终负责人：统一封套 `{code,msg,data,trace_id,request_id,timestamp}`、列表 `{items,page,page_size,total}`（`page>=1`、`1<=page_size<=100`）、`msg`/`http_status` 只来自 `config/api-messages.yaml`。verifier argv：`["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`。
- [x] 显式边界（不修，只登记）：既有全量用例未逐条打 marker，`-m unit` 只覆盖已登记 marker 的选择范围；本次不改全仓用例、不改 `Makefile` 既有目标语义。禁止在真实边界未配置时静默放行——未配置必须显式记录原因与命令提示。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | unit | 纯逻辑/状态机（无 IO） | 无 `DATABASE_URL` 时单元层选择可收集且全绿；无 DB/网络调用 | tests/acceptance/dfx/test_dfx_layer_baseline.py / S-01 | `["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_layer_baseline.py","-k","s01"]` | verified |
| S-02 | contract | Enum ↔ YAML ↔ 源码扫描 | 双向一致；无未登记码；每码双语与合法 http_status | tests/test_error_catalog.py + tests/test_api_i18n.py / S-02 | `["uv","run","pytest","-q","tests/test_error_catalog.py","tests/test_api_i18n.py"]` | verified |
| B-01 | contract | api-kit paginate → API Query | 0/101 拒绝；1/100 通过；默认三键封套 | tests/test_error_catalog.py / B-01 | `["uv","run","pytest","-q","tests/test_error_catalog.py","-k","paginate"]` | verified |
| B-04 | contract | locale 资源 ↔ API catalog | 后端每码双语非空；前端 key 双语一致 | tests/acceptance/test_foundation_i18n.py + scripts/check_frontend_i18n.py / B-04 | `["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]` | verified |
| RULE-api-001 | contract | 统一封套/分页边界/catalog 错误码 + 原 verifier 真实边界 | 封套字段与分页边界；错误码只来自 catalog；原 verifier 全部通过 | 原 verifier / RULE-api-001 | `["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | **真实 RED**：`AssertionError: pyproject.toml 未登记分层 marker：['unit','contract','integration','e2e']`，并伴随 `PytestUnknownMarkWarning: Unknown pytest.mark.unit`——与 design 技术债① 预告的「unknown-marker 警告/空选择」一致，非伪造 | `-k s01` → **1 passed（0.52s）**；探针子进程在移除 `DATABASE_URL`/`REDIS_URL` 后 `-m unit` 可收集且全绿 | `test_s01_unit_layer_is_selectable_and_io_free`：① 四类 marker 齐备（tomllib 解析真实 `pyproject.toml`）；② `-m unit --collect-only` exit 0、无 unknown-mark 警告、选中 ≥1 用例；③ `-m unit` 实跑 exit 0（环境已移除 DB/Redis 变量） | 真实 pytest 选择器 + 真实 `pyproject.toml` + 真实子进程；`tests/acceptance/` 无父级 conftest，收集期不连库；未 mock | verified |
| S-02 | 承接既有落点，无独立 RED | `tests/test_error_catalog.py tests/test_api_i18n.py` → **15 passed** | 既有用例的断言：`ErrorCode` ↔ catalog 双向一致、无未登记码、每码双语与合法 `http_status` | 真实 `config/api-messages.yaml` + `ErrorCode` 枚举；未 mock | verified |
| B-01 | 承接既有落点，无独立 RED | `tests/test_error_catalog.py -k paginate` → **3 passed, 7 deselected** | 既有 paginate 边界断言（`page=0`/`page_size=101` 拒绝、`1`/`100` 通过、默认三键封套） | 真实 api-kit `paginate`；未 mock | verified |
| B-04 | 承接既有落点，无独立 RED | `tests/acceptance/test_foundation_i18n.py` → **6 passed**；`scripts/check_frontend_i18n.py` → `i18n keys OK: 714` | 既有断言：后端每码 zh-CN/en-US 非空；前端 key 双语一致 | 真实 catalog + 真实 `src/locales/*.json`；未 mock | verified |
| RULE-api-001 | 承接（原 verifier），无独立 RED | verifier argv 全过：`tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py` → **18 passed** | 原 verifier 自身断言（统一封套键集、列表分页边界、`msg`/`http_status` 只来自 catalog） | 真实 api-kit + 真实 catalog + 真实 Console HTTP（envelope 套件）；未 mock | verified |
- S-01: verified — automated command passed; run_id=dbfe43dd807b4298b731bc0a372198d8 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=dbfe43dd807b4298b731bc0a372198d8 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=dbfe43dd807b4298b731bc0a372198d8 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=dbfe43dd807b4298b731bc0a372198d8 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-28] started
- [2026-09-28] completed (done)
## TASK-002: 契约一致性、关系形态与架构门禁（parity/幂等指纹）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.1 测试分层矩阵, 14-dfx-acceptance.backend.design.md#3.4.3 契约与一致性测试清单, 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-api#RULE-api-002, harness-rel#RULE-rel-001, harness-arch#RULE-arch-001
- **Acceptance-Refs**: S-03, B-02, RULE-api-002, RULE-rel-001, RULE-arch-001
- **Files**: `tests/acceptance/dfx/test_dfx_contract_parity.py`
- **Estimate**: 半天级（含幂等指纹存量漂移的真实 RED）

### Description

FEAT-03 的契约层聚合：ORM↔迁移 parity、契约模型形状、关系接口形态、架构门禁四项**引用既有落点**；本任务新增的断言是 design §3.4.3 登记的**存量漂移**——console 侧 `agent_service`/`mcp_service`/`skill_service` 三处 `_fingerprint` 仍以竖线拼接且不含 `endpoint`/`tenant_id` 判别键，须按 `channel_service`/`auth_service`/`audit_export_service`/`run_submission` 的口径对齐（这是本任务唯一的实现缺口，可产出真实 RED）。另聚合 SSE 封套/seq 契约（B-02）。

### Checklist

- [ ] [S-03][contract] 先写用例后改实现：以 `ORM ↔ 迁移 ↔ OpenAPI` 为真实边界，对三处 `_fingerprint` 断言「指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256 且**含 `endpoint` 与 `tenant_id` 判别键**」「同 key 异指纹 → `IDEMPOTENCY_MISMATCH`」「并发由 `pg_advisory_xact_lock` 串行化、partial unique 兜底」；首跑必须先记录 RED（现状竖线拼接、无判别键）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/architecture"]`。
- [ ] [S-03][contract] 引用既有 parity 与形状断言并登记 argv（不复制）：`tests/test_contracts.py`（拒绝额外字段、`snapshot_hash`/`delivery_key` pattern）、各域 `test_*_schema_parity.py`（列/类型/可空/PK/FK/索引/partial predicate）、`tests/console_platform/test_user_side_relations.py`（关系接口只允许单关系 POST/DELETE）、`tests/architecture/`（依赖方向、IM Gateway 边界、Pod 标记文本扫描、指标 label 卫生）。
- [ ] [B-02][contract] 以 `SSE 解析器 → Runtime` 为真实边界断言 `: heartbeat` 注释帧不计 seq、事件按 seq 单调有序、未知事件类型 unknown-safe、封套公共字段 `{run_id,seq,timestamp,type,data}` 完整。执行 argv：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py"]`。
- [ ] [RULE-api-002][contract] 作为唯一最终负责人：创建/上传类 POST 的 `Idempotency-Key` 幂等（判别键、`IDEMPOTENCY_MISMATCH`、advisory lock 串行化、partial unique 兜底），执行面复用各域既有幂等用例与其 verifier。verifier argv：`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`。
- [ ] [RULE-rel-001][contract] 作为唯一最终负责人：关系类修改只用单关系 POST/DELETE 且由独立事务完成，禁止全量 PUT 覆盖关系集合；重新绑定复用软删原行（partial unique `WHERE is_deleted=false`）而非插新行；解除关系的幂等语义按端点显式声明（Agent 侧幂等成功 vs User 侧 `COMMON_NOT_FOUND`）。verifier argv：`["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]`。
- [ ] [RULE-arch-001][contract] 作为唯一最终负责人：四个部署单元固定、Runtime/Worker 无状态可横向扩展、不绑定 Pod/bot_id/用户；同一会话可被任意 Pod 执行。verifier argv：`["uv","run","pytest","-q","tests/architecture"]`。
- [ ] 显式边界（不修，只登记）：`harness-arch` 已登记的两处现状差异——IM Gateway 只有 `install_metrics`、零 `declare_metric`（`/metrics` 目录为空）；四个 k8s 清单的 `readinessProbe`/`livenessProbe` 均指向 `/healthz`，api-kit 的 `/readyz` 未被部署消费。本任务只断言 `tests/architecture` verifier 的现行口径，**不改部署清单、不补 gateway catalog**。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | contract | ORM ↔ 迁移 ↔ OpenAPI | 指纹含 `endpoint`/`tenant_id`；异指纹 `IDEMPOTENCY_MISMATCH`；列/类型/索引/partial predicate 一致；关系只单端点变更 | tests/acceptance/dfx/test_dfx_contract_parity.py / S-03 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/architecture"]` | planned |
| B-02 | contract | SSE 解析器 → Runtime | heartbeat 不计 seq；seq 单调；未知事件 unknown-safe；封套字段完整 | tests/agent_runtime/test_sse.py + tests/gateway/test_sse_parser.py / B-02 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py"]` | planned |
| RULE-api-002 | integration | 真实 Console HTTP → 幂等表(PostgreSQL) + 原 verifier 真实边界 | 判别键齐备；异指纹 409；advisory lock 串行化；原 verifier 全部通过 | 原 verifier / RULE-api-002 | `["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]` | planned |
| RULE-rel-001 | integration | 真实 Console HTTP 单端点原子变更 + 原 verifier 真实边界 | 无全量 PUT；单关系 POST/DELETE；复活原行；原 verifier 全部通过 | 原 verifier / RULE-rel-001 | `["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]` | planned |
| RULE-arch-001 | integration | 四部署单元/无状态/依赖方向 + 原 verifier 真实边界 | 部署单元与无状态约束；依赖方向单向；原 verifier 全部通过 | 原 verifier / RULE-arch-001 | `["uv","run","pytest","-q","tests/architecture"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写；S-03 的幂等指纹判别键属存量漂移，必须记录真实 RED） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-003: 可靠性验收基座与 Worker lease/claim

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.1 测试分层矩阵, 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#3.1 技术选型与关键决策
- **Spec-Refs**: harness-worker#RULE-worker-001
- **Acceptance-Refs**: S-04, RULE-worker-001
- **Files**: `tests/acceptance/dfx/environment.py`, `tests/acceptance/dfx/conftest.py`, `tests/acceptance/dfx/test_dfx_reliability.py`
- **Estimate**: 半天级；外部服务启动与等待另计

### Description

为后续可靠性/故障/投递任务建立 DFX 自己的真实验收基座（真实 PostgreSQL 迁移到 head + 真实 Redis + 真实 Worker/Runtime 子进程 + 租户级种子与清理，复用 08/09/10 既有验收栈原语而非另造进程管理），并以它取证 S-04：同一 Task 只被一个 Worker claim（`FOR UPDATE SKIP LOCKED`）、heartbeat 续租、租户隔离查询通过。

### Checklist

- [ ] [S-04][integration] 以 `真实 PostgreSQL + Redis` 为真实边界编写用例：同一 Task 并发提交两次只被一个 Worker claim（`FOR UPDATE SKIP LOCKED`，落败者不重复执行）；执行中 heartbeat 续租使 `lease_until` 前移；跨租户查询不可见。执行 argv：`["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_reliability.py","-k","s04"]`。
- [ ] [RULE-worker-001][integration] 作为唯一最终负责人：PG 是 Task/Schedule/lease 唯一权威源、Redis 仅 wake-up/cancel hint、`task_type` 仅 `SKILL/BATCH`、claim 用 `FOR UPDATE SKIP LOCKED`、进入 WAITING 释放 lease、deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`（后三条的**场景**归 TASK-005，规则责任在本任务）。verifier argv：`["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]`。
- [ ] 种子与清理：唯一租户 + 可识别 key 前缀；收尾清理本租户残留为 0；任何失败路径都要执行清理，运行后不得残留 `uvicorn`/`muad_*.main` 进程。
- [ ] 真实边界断言取材于真实库：claim/租约状态一律从 PG 逐行回读（`lease_owner`/`lease_until`/`status`），不以日志或返回值代替。
- [ ] 复用既有验收栈原语（`ServiceProcess`/`free_port`/迁移到 head）与 `tests/acceptance/task_schedule|runtime` 的环境口径，不新建第二套进程管理；租户与产物根钉在系统临时目录。
- [ ] 显式边界（不修，只登记）：Redis 的既有部分用例仍使用替身（design 技术债②）——本任务与 TASK-004 只在集成/E2E 层接真实 Redis，不去逐个改造模块单测。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 PostgreSQL + Redis | 单一 claim；heartbeat 续租；租户隔离查询通过 | tests/acceptance/dfx/test_dfx_reliability.py / S-04 | `["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_reliability.py","-k","s04"]` | planned |
| RULE-worker-001 | integration | 真实 PG 权威源 + Redis 降级 + 原 verifier 真实边界 | PG 为唯一权威源；Redis 仅 hint；claim/WAITING/deadline 口径；原 verifier 全部通过 | 原 verifier / RULE-worker-001 | `["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-004: 依赖故障矩阵（PG/Redis/Artifact Store）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#3.4.8 不得 mock 的真实边界清单, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-01, E-02, E-03
- **Files**: `tests/acceptance/dfx/test_dfx_fault_matrix.py`
- **Estimate**: 半天级

### Description

FEAT-04 的依赖故障面：PG 不可用 fail closed（不本地落状态、`/readyz` 失败、恢复后事实完整）、Redis 不可用降级 at-least-once（PG 事实不丢、恢复后去重生效）、Artifact Store/NFS 不可用或慢时已有 READY 可继续、cache miss 与新写入明确失败且不返回假成功。

### Checklist

- [ ] [E-01][integration] 以 `Service → PostgreSQL` 为真实边界编写故障注入用例：停 PG/连接失败 → fail closed（不本地落状态）、`/readyz` 返回失败（503 语义）、恢复后业务事实完整。执行 argv：`["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_fault_matrix.py","-k","e01"]`。
- [ ] [E-02][integration] 以 `Redis → PG` 为真实边界断言 Redis 停止时 cache miss / dedupe 降级为 at-least-once、PG 数据完整、恢复后去重重新生效；既有真实降级用例一并登记 argv（引用不复制）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e02 && uv run pytest -q tests/acceptance/im_gateway/test_redis_degradation.py"]`。
- [ ] [E-03][integration] 以 `emptyDir cache → NFS` 为真实边界断言已有 READY 可继续执行；cache miss 与新 Artifact 写入明确失败为 `SKILL_ARTIFACT_UNAVAILABLE`，**不返回假成功**。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e03 && uv run pytest -q tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py"]`。
- [ ] 故障注入必须真实可复位：注入前后各取一次真实盘面（PG/Redis/Artifact 目录），并断言复位后无残留（无孤儿进程、无残留 key、无残留对象）。
- [ ] 显式边界（不修，只登记）：E-03 的「NFS 高延迟」部分依赖环境能力（design 技术债③），环境不具备时按 manual 口径记录原因，**不得**用本地替身冒充 NFS 慢故障。
- [ ] 每条失败路径都要断言「不返回假成功」：失败必须显式（错误码/状态），不得静默吞错或返回空结果。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | Service → PostgreSQL | fail closed 不落本地状态；`/readyz` 失败；恢复后事实完整 | tests/acceptance/dfx/test_dfx_fault_matrix.py / E-01 | `["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_fault_matrix.py","-k","e01"]` | planned |
| E-02 | integration | Redis → PG | cache miss/dedupe 降级 at-least-once；PG 完整；恢复后去重生效 | tests/acceptance/dfx/test_dfx_fault_matrix.py + tests/acceptance/im_gateway/test_redis_degradation.py / E-02 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e02 && uv run pytest -q tests/acceptance/im_gateway/test_redis_degradation.py"]` | planned |
| E-03 | integration | emptyDir cache → NFS | 已有 READY 可继续；新写入明确 `SKILL_ARTIFACT_UNAVAILABLE`；不假成功 | tests/acceptance/dfx/test_dfx_fault_matrix.py + tests/acceptance/test_foundation_artifact.py + tests/test_skill_artifact_cache.py / E-03 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e03 && uv run pytest -q tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-005: 租约回收、deadline sweep 与 Schedule 边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-04, E-05, B-03
- **Files**: `tests/acceptance/dfx/test_dfx_recovery.py`
- **Estimate**: 半天级

### Description

崩溃恢复面：kill Worker/Runtime Pod 且 lease 过期后 Task 被其他 Worker reclaim、过期 RUNNING 的 Run 被 CAS 置 `FAILED(RUN_ABANDONED)` 并释放会话；Task 超过 `deadline_at` 后 30s 内 CAS 置 `FAILED(TASK_DEADLINE_EXCEEDED)` 且仍按 `delivery_mode` 投递；Schedule 错过触发只 SKIP 不补发并计数、ONCE 成功后 `COMPLETED` 且 `next_fire_at` 为空。

### Checklist

- [ ] [E-04][integration] 以 `lease → Reaper → CAS` 为真实边界编写用例：真实子进程 `SIGKILL` 后等 lease 过期，断言 Task 被其他 Worker reclaim、过期 RUNNING Run 被 CAS 置 `FAILED(RUN_ABANDONED)` 且会话被释放；既有真实多 Pod 回收用例一并登记 argv（引用不复制）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e04 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py"]`。
- [ ] [E-05][integration] 以 `Scheduler sweep → PG` 为真实边界断言超过 `deadline_at` 后 30s 内 CAS 置 `FAILED(TASK_DEADLINE_EXCEEDED)`，**且终态仍按 `delivery_mode` 投递**（投递事实落库）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e05 && uv run pytest -q tests/agent_worker/test_task_deadline.py"]`。
- [ ] [B-03][integration] 以 `Scheduler → Schedule` 为真实边界断言 misfire 只 SKIP 不补发且计数可见、ONCE 成功后 `COMPLETED`、`completed_at` 非空、`next_fire_at` 为空。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k b03 && uv run pytest -q tests/agent_worker/test_scheduler_misfire.py"]`。
- [ ] 崩溃用真实进程终止取证：`SIGKILL` 后按 PG 真实盘面判定（租约行、Run 状态、会话释放），不用「进程退出码」或日志推断；等待窗口用绝对时刻比较，不做无界 `sleep` 轮询。
- [ ] 断言 CAS 语义：终态写入必须带预期状态条件（租约守卫式 CAS），构造「过期执行者晚到」的对照样本证明其不能覆盖新终态。
- [ ] 真实边界未配置（无 PG/Redis/子进程能力）时必须显式记录原因，不得静默放过真实边界。
- [ ] 收尾清理：本租户 Task/Schedule/Run/审计残留为 0；运行后无残留 `uvicorn`/`muad_*.main`/worker 进程。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | lease → Reaper → CAS | 其他 Worker reclaim；`FAILED(RUN_ABANDONED)`；会话释放 | tests/acceptance/dfx/test_dfx_recovery.py + tests/acceptance/runtime/test_multipod_recovery.py + tests/acceptance/task_schedule/test_recovery.py / E-04 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e04 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py"]` | planned |
| E-05 | integration | Scheduler sweep → PG | 30s 内 CAS `FAILED(TASK_DEADLINE_EXCEEDED)`；仍按 `delivery_mode` 投递 | tests/acceptance/dfx/test_dfx_recovery.py + tests/agent_worker/test_task_deadline.py / E-05 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e05 && uv run pytest -q tests/agent_worker/test_task_deadline.py"]` | planned |
| B-03 | integration | Scheduler → Schedule | 仅 SKIP 不补发并计数；ONCE `COMPLETED` 且 `next_fire_at` 空 | tests/acceptance/dfx/test_dfx_recovery.py + tests/agent_worker/test_scheduler_misfire.py / B-03 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k b03 && uv run pytest -q tests/agent_worker/test_scheduler_misfire.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-006: 最终投递去重与重试（Worker→Gateway→Redis）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#3.4.3 契约与一致性测试清单, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-11, E-06
- **Files**: `tests/acceptance/dfx/test_dfx_delivery.py`
- **Estimate**: 半天级

### Description

FEAT-04 的最终投递面：终态投递以 `delivery_key`（`task:{task_id}:final`，Child 为 `:{child_id}:final`）去重、重复投递返回 200 且 `delivery_status` 最终 `SENT`、结果**先持久化后投递**；前 4 次失败第 5 次成功走指数退避、仅 HTTP 2xx 且 `delivered=true` 才置 `SENT`、Gateway 占位（`delivered=false`）按可重试失败退避重投、超限置 `FAILED` 并写审计。

### Checklist

- [ ] [S-11][integration] 以 `Worker → Gateway /internal/deliveries → Redis` 为真实边界编写用例：同一 `delivery_key` 投递两次 → 第二次返回 200 且不重复发送、`delivery_status` 最终 `SENT`；并断言结果**先持久化后投递**（投递发生前库内已有终态事实）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k s11 && uv run pytest -q tests/acceptance/task_schedule/test_delivery.py"]`。
- [ ] [E-06][integration] 以 `Worker → Gateway → Redis` 为真实边界断言：前 4 次失败后第 5 次成功的指数退避序列（`BACKOFF_BASE_SEC * 2**attempts`）；重复 `delivery_key` 不重复发送；**HTTP 200 但 `delivered=false` 不得置 `SENT`**，须按可重试失败退避重投；超过 5 次置 `delivery_status=FAILED` 并写审计。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k e06 && uv run pytest -q tests/acceptance/im_gateway/test_worker_delivery.py"]`。
- [ ] 退避断言用可注入时钟或真实间隔两者之一，并显式登记所选口径；`delivery_attempts` 必须在发起请求前自增并提交（崩溃重启不丢退避进度），以真实库回读取证。
- [ ] `delivery_mode` 仅 `FINAL_ONLY`/`NONE`：对 `NONE` 构造对照样本，断言不产生投递记录。
- [ ] 断言失败面不吞事实：超限后审计行存在且载荷含原因与重试次数，反查无明文密钥。
- [ ] 真实 Redis 未配置时显式记录原因，不得用替身冒充「真实 SET NX EX/TTL 去重」。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-11 | integration | Worker → Gateway `/internal/deliveries` → Redis | 重复 `delivery_key` 返回 200；最终 `SENT`；先持久化后投递 | tests/acceptance/dfx/test_dfx_delivery.py + tests/acceptance/task_schedule/test_delivery.py / S-11 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k s11 && uv run pytest -q tests/acceptance/task_schedule/test_delivery.py"]` | planned |
| E-06 | integration | Worker → Gateway → Redis | 退避 5 次；去重不重发；`delivered=false` 不置 SENT；超限 FAILED + 审计 | tests/acceptance/dfx/test_dfx_delivery.py + tests/acceptance/im_gateway/test_worker_delivery.py / E-06 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k e06 && uv run pytest -q tests/acceptance/im_gateway/test_worker_delivery.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-007: 安全验收：Egress 拒绝、Secret 全链路与日志脱敏

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.5 安全验收矩阵, 14-dfx-acceptance.backend.design.md#3.4.3 契约与一致性测试清单, 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-secret#RULE-secret-001, harness-log#RULE-log-001
- **Acceptance-Refs**: E-07, RULE-secret-001, RULE-log-001
- **Files**: `tests/acceptance/dfx/test_dfx_security.py`
- **Estimate**: 半天级

### Description

FEAT-05 的安全面：未命中 allowlist 的 `ctx.http` 不发出调用并按 `target_type=HTTP` 写 DENY 审计、响应 > 5 MiB 拒绝；canary 反查 RuntimeSnapshot/审计/日志/Prompt/IM 出站/API 响应均无密钥明文（**按 canary 实际覆盖范围登记，不宣称全覆盖**）；日志敏感字段经 logging-kit 双通道脱敏（审计侧丢键 vs 日志侧值置换 `***`，语义不同不可混用）。

### Checklist

- [ ] [E-07][integration] 先写用例后改实现（若发现缺口）：以 `Egress Boundary → Audit/日志/Snapshot` 为真实边界编写用例：未命中 allowlist 的 `ctx.http` 调用**不发出**且写 DENY 审计；载荷含 canary 密钥时反查 `runtime_snapshot`/`egress_audit`/`tool_call_audit`/`model_invocation_audit` 四表、IM 出站文本与 bot 快照两侧**均无明文**；`>5 MiB` 响应被拒绝并审计。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_security.py -k e07 && uv run pytest -q tests/acceptance/test_secret_consumers.py tests/acceptance/im_gateway/test_secrets_and_readiness.py"]`。
- [ ] [RULE-secret-001][integration] 作为唯一最终负责人：密钥明文只存各 Owner 表（模型 `api_key`、Bot `secret`、MCP `auth_secret`、凭据 `credential_json`；平台侧无自有密钥列、`project_platform.auth_secret` 为休眠列不得读写）、跨表以主键引用（无 `secret_ref`/SecretProvider）、明文出口仅三处受控例外（bot 快照、`resolve-credentials`、`resolve-definition`）且统一 `require_service_identity` 门控。verifier argv：`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`。
- [ ] [RULE-log-001][integration] 作为唯一最终负责人：所有服务统一 logging-kit、仅配置 `LOG_DIR`（另读 `LOG_LEVEL`）、JSON 输出、自动携带 `trace_id/request_id/tenant_id`、敏感字段脱敏；并断言**两侧语义不同**——审计侧键在 before/after 中整个消失，日志侧值置换为 `***`。verifier argv：`["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]`。
- [ ] 断言三处 internal 端点（`resolve-definition`/`resolve-credentials`/`resolve-egress-access`）的**匿名与越权访问必须 403**，且调用方（Worker/Runtime）带 `X-Internal-Service` 才可见明文——受控出口的两侧都要断言。
- [ ] 显式边界（不修，只登记）：canary 机检**不含** `runtime.canonical_event`、`control.config_audit_log`、Skill package 与 `SKILL.md`；「不得进入」是约定而非全覆盖，新增落库面必须自查，不得以「canary 全绿」代替覆盖结论。
- [ ] 断言「不返回假成功」：Egress 拒绝与 5 MiB 拒绝都必须显式失败（错误码/审计），不得静默降级为成功。
- [ ] 收尾清理：canary 值一次性使用，运行后库内/日志目录/产物目录无该明文与残留对象；运行后无孤儿进程。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-07 | integration | Egress Boundary → Audit/日志/Snapshot | DENY 审计且调用不发出；canary 四表 + IM 出站 + bot 快照无明文；>5 MiB 拒绝 | tests/acceptance/dfx/test_dfx_security.py + tests/acceptance/test_secret_consumers.py + tests/acceptance/im_gateway/test_secrets_and_readiness.py / E-07 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_security.py -k e07 && uv run pytest -q tests/acceptance/test_secret_consumers.py tests/acceptance/im_gateway/test_secrets_and_readiness.py"]` | planned |
| RULE-secret-001 | integration | 密钥明文只存 Owner 表/三处受控出口 + 原 verifier 真实边界 | 无 `secret_ref`；休眠列不读写；受控出口门控；原 verifier 全部通过 | 原 verifier / RULE-secret-001 | `["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]` | planned |
| RULE-log-001 | integration | logging-kit 唯一出口与双通道脱敏 + 原 verifier 真实边界 | JSON 结构/关联字段/脱敏；审计丢键 vs 日志 `***`；原 verifier 全部通过 | 原 verifier / RULE-log-001 | `["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-008: API 安全与租户隔离（RBAC/CSRF/ADMIN 门控/租户谓词）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.5 安全验收矩阵, 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: E-08, RULE-auth-001
- **Files**: `tests/acceptance/dfx/test_dfx_api_security.py`
- **Estimate**: 半天级

### Description

FEAT-05 的 API 面：缺失/伪造 CSRF → 403 `FORBIDDEN` 且不落业务变更；BUILDER 访问 ADMIN 端点（含凭据路由 `accounts`/`users`/`credentials`）→ 403；跨租户读取不可见/不可写且不泄露存在性；伪造 `X-Tenant-Id` 不能切换租户（租户取自登录账号）。

### Checklist

- [ ] [E-08][integration] 以 `API → RBAC/CSRF/租户谓词` 为真实边界编写用例：非安全方法缺失与伪造 `X-CSRF-Token` → 403 且审计计数不变；BUILDER 访问 ADMIN 端点（含 `credentials`）→ 403；跨租户读取/写入不可见且不泄露存在性；**带伪造 `X-Tenant-Id` 时列表仍只含本账号租户数据**。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_api_security.py -k e08 && uv run pytest -q tests/console_auth/test_rbac.py tests/console_platform/test_credentials_api.py tests/agent_worker/test_tenant_guard.py"]`。
- [ ] [RULE-auth-001][integration] 作为唯一最终负责人：三层授权关系与 Effective Capability（含 `is_deleted=false` 与资源/Agent `enabled`）、未授权资源不进 Prompt/ToolRegistry/Catalog；并覆盖 Console 侧的凭据路由 ADMIN + 账号派生租户、CSRF/会话/密码锁定/审计同事务口径。verifier argv：`["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`。
- [ ] 断言「ADMIN 门控是前端隐藏 + 后端 403 兜底双层」时只断言**本模块可断言的后端兜底层**；前端隐藏归各前端 owner，如发现缺失只登记缺口不改跨模块行为。
- [ ] 断言 CSRF 与会话 Cookie 属性：`muad_session` 为 httponly、CSRF Cookie 可读、两者 `samesite=strict`、`secure` 仅非 dev 打开。
- [ ] 断言密码与会话口径不被削弱的边界：`argon2id`（`$argon2id$` 前缀）+ 最短 12 字符、连续 5 次失败锁 15 分钟且锁定时清零计数、未知用户与禁用账号走 dummy hash 等化时序统一回 `INVALID_CREDENTIALS`。
- [ ] 收尾清理：跨租户对照样本自建自清（`finally` 删除），不污染共享开发库；运行后无孤儿进程。
- [ ] 显式边界（不修，只登记）：租户隔离已于 2026-09-28 全量收敛为「账号派生」，`X-Tenant-Id` 仅对内部服务与公开登录生效（内部路由另有服务身份门控）；本任务只断言该口径，不改任何路由的租户来源。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-08 | integration | API → RBAC/CSRF/租户谓词 | 403 `FORBIDDEN` 且不落变更；跨租户不可见/不可写不泄露存在性；伪造租户头无效 | tests/acceptance/dfx/test_dfx_api_security.py + tests/console_auth/test_rbac.py + tests/console_platform/test_credentials_api.py + tests/agent_worker/test_tenant_guard.py / E-08 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_api_security.py -k e08 && uv run pytest -q tests/console_auth/test_rbac.py tests/console_platform/test_credentials_api.py tests/agent_worker/test_tenant_guard.py"]` | planned |
| RULE-auth-001 | integration | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | 三层授权与 Effective Capability；未授权不进 Prompt/Catalog；凭据路由 ADMIN + 账号派生租户；原 verifier 全部通过 | 原 verifier / RULE-auth-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-009: 模型恢复验收（429/5xx/超时/deadline/cancel）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.6 模型恢复矩阵, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-09
- **Files**: `tests/acceptance/dfx/test_dfx_model_recovery.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

FEAT-06 的模型恢复面：429 优先按 `Retry-After` 等待后重试；5xx/529/连接重置/超时指数退避 + jitter；等待不超过剩余 deadline；cancel 优先于重试（每轮调用前检查取消）；`prompt too long` 触发一次 Context rebuild/compaction 后重试且不得无限循环；重试计数与原因入模型审计。

### Checklist

- [ ] [E-09][integration] 以 `ModelGateway → Provider` 为真实边界编写用例：429（含 `Retry-After`）、5xx、连接超时、deadline 不足、cancel 五类触发各自的预期行为；等待时长受剩余 deadline 约束、无无限等待；cancel 时立即停止。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_model_recovery.py -k e09 && uv run pytest -q tests/agent_runtime/test_model_recovery.py"]`。
- [ ] Provider 侧用**真实本地 HTTP 探针**承载外部端点（`tests/e2e/openai_probe_app.py` 口径），禁止在验收中伪造外部响应为「已通过」；探针需能按用例注入 429/`Retry-After`/5xx/超时。
- [ ] 断言 deadline 与 cancel 的优先级：构造「剩余 deadline 不足」与「等待中收到 cancel」两个对照样本，断言前者不重试、后者立即停止且重试计数不再增长。
- [ ] `prompt too long` 断言只触发**一次** Context rebuild/compaction 后重试，不得无限循环（以 rebuild 次数上限为断言）。
- [ ] 断言重试计数与原因写入模型审计（`model_invocation_audit`），且载荷不含密钥明文。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-09 | integration | ModelGateway → Provider | `Retry-After` 优先；退避受 deadline 约束；cancel 优先；无无限等待；计数与原因入审计 | tests/acceptance/dfx/test_dfx_model_recovery.py + tests/agent_runtime/test_model_recovery.py / E-09 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_model_recovery.py -k e09 && uv run pytest -q tests/agent_runtime/test_model_recovery.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-010: 黄金旅程：绑定、流式与中断恢复/取消

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.2 黄金旅程矩阵, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-05, S-06, S-09
- **Files**: `tests/acceptance/dfx/test_dfx_journeys.py`
- **Estimate**: 半天级

### Description

FEAT-02 的第一组黄金旅程：`/bind` 首次绑定后身份稳定映射 PlatformUser 且不自动授予 Agent；普通会话流式（SSE 首事件 `run.created`、`message.delta` 顺序到达、终态 `run.completed/run.failed`、封套 `{run_id,seq,timestamp,type,data}`）；Interrupt→`WAITING_INPUT`→resume 继续、取消 `cancel-active` 返回 `CANCELLING` 并协作终态、无活跃返回 `NO_ACTIVE_RUN`。

### Checklist

- [ ] [S-05][E2E] 以 `Browser/HTTP → Console → PG → IM Gateway` 为真实边界编写用例：未绑定用户发消息触发 `/bind` 后再次对话，断言身份稳定映射 PlatformUser、**不自动授予 Agent**（授权表无新增行）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s05 && uv run pytest -q tests/acceptance/im_gateway/test_binding.py"]`。
- [ ] [S-06][E2E] 以 `Gateway → Runtime SSE → Browser` 为真实边界编写用例：发起普通对话断言首事件 `run.created`、`message.delta` 按 seq 顺序到达、终态为 `run.completed`/`run.failed`、封套字段 `{run_id,seq,timestamp,type,data}` 完整。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s06 && uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py"]`。
- [ ] [S-06][E2E] **显式边界登记（不冒充）**：本仓前端无 SSE 消费面，「Browser」臂在本需求内不可执行（自建流式页面属 design Out of Scope 的业务功能）；必须在 Acceptance Evidence 中如实登记该边界并给出「真实 Gateway→Runtime HTTP/SSE 链路已达成的断言清单」，不得写成完整覆盖。
- [ ] [S-09][E2E] 以 `Runtime interrupt → resume/cancel` 为真实边界编写用例：触发澄清 → `WAITING_INPUT`；resume 后继续且 seq 不重排；另一 Run 执行 `cancel-active` → 返回 `CANCELLING` 并协作终态；无活跃 Run → `NO_ACTIVE_RUN`；`WAITING_INPUT` 直接 CAS 取消。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s09 && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"]`。
- [ ] 流式断言只认已落库事件：`seq`/`timestamp` 沿用 `canonical_event` 持久值（禁止按连接自增），`: heartbeat` 不计 seq；`STREAM_TIMEOUT_SEC` 的「有界失败」用例须注入小值，不得依赖默认 300s。
- [ ] 失败路径允许改写路由制造超时/错误，但**不得 fulfill 业务响应体**；成功路径（S-*）不得出现 `page.route(`（本任务为 pytest 链路，同口径适用于任何 HTTP 拦截）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | E2E | Browser/HTTP → Console → PG → IM Gateway | 身份稳定映射 PlatformUser；不自动授予 Agent | tests/acceptance/dfx/test_dfx_journeys.py + tests/acceptance/im_gateway/test_binding.py / S-05 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s05 && uv run pytest -q tests/acceptance/im_gateway/test_binding.py"]` | planned |
| S-06 | E2E | Gateway → Runtime SSE → Browser | 首事件 `run.created`；`message.delta` 顺序；终态事件；封套完整 | tests/acceptance/dfx/test_dfx_journeys.py + tests/acceptance/im_gateway/test_runtime_stream.py / S-06 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s06 && uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py"]` | planned |
| S-09 | E2E | Runtime interrupt → resume/cancel | `WAITING_INPUT`→resume 继续；`CANCELLING` 协作终态；`NO_ACTIVE_RUN` | tests/acceptance/dfx/test_dfx_journeys.py + tests/acceptance/runtime/test_run_lifecycle.py / S-09 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s09 && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写；S-06 必须登记「Browser 臂不可执行」边界） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-011: 执行路由、批量 fan-out/fan-in 与授权可见性/多 IM 路由

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-010
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.2 黄金旅程矩阵, 14-dfx-acceptance.backend.design.md#3.4.5 安全验收矩阵, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-07, S-08, S-10, E-10
- **Files**: `tests/acceptance/dfx/test_dfx_routing.py`, `tests/acceptance/dfx/test_dfx_authorization_scope.py`
- **Estimate**: 半天级

### Description

FEAT-02/FEAT-05 的第二组旅程：同步 Skill/异步 Task/定时 Schedule 的 `execution_mode` 路由与 Schedule 单次触发（ONCE 成功后 `COMPLETED` 且无 `next_fire_at`）；Parent/Child fan-out/fan-in（Child 幂等键 `parent:{parent_id}:{item_key}`、并发受限、fan-in 后 Parent CAS 完成且只推最终结果）；授权可见性（未授权 Skill/MCP 不进 Catalog/Prompt、撤销只影响后续新 Run、旧 Snapshot 不变）与多 bot_id 路由同一 Agent；企业微信真机重连维持 manual。

### Checklist

- [ ] [S-07][E2E] 以 `Runtime → Worker → Schedule` 为真实边界编写用例：分别触发同步 Skill、异步 Task、定时 Schedule，断言路由符合 `execution_mode`；Schedule 到点只创建一次 Task；ONCE 成功后 `COMPLETED` 且无 `next_fire_at`。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s07 && uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_schedules.py"]`。
- [ ] [S-08][E2E] 以 `Worker Parent/Child → fan-in` 为真实边界编写用例：批量意图产生 Parent/Child，断言 Child 幂等键为 `parent:{parent_id}:{item_key}`、并发受限、fan-in 后 Parent CAS 完成并**只推最终结果一次**。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s08 && uv run pytest -q tests/acceptance/task_schedule/test_batch.py"]`。
- [ ] [S-10][E2E] 以 `授权解析 → Prompt/ToolRegistry → IM 路由` 为真实边界编写用例：SELECTED/ALL 用户范围下未授权 Skill/MCP 不进入 Catalog/Prompt；撤销授权后新 Run 不可见而旧 Snapshot 不变；多 `bot_id` 路由同一 Agent。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_authorization_scope.py -k s10 && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"]`。
- [ ] [E-10][manual] **保持 manual，不降级**：企业微信真机重连（`Gateway WS → 企业微信`）需真实凭据；执行条件为有真实凭据的环境，记录 `reconnect` 指标可见与 SDK backoff 重连成功；**环境受限时只记录「需真实凭据 + 原因」，绝不写成通过**。
- [ ] 断言「撤销只影响后续新 Run」时，必须同时取旧 Run 的 Snapshot 前后哈希作对照，避免只断言新 Run 的行为就宣称该条目覆盖。
- [ ] 断言无授权泄漏：对未授权资源构造真实 MCP/Skill 探针，断言其工具**未被调用**（调用计数为 0），而不是只断言返回体为空。
- [ ] 收尾清理：批量/定时/多 bot 产生的 Task、Schedule、Binding、会话与审计行按租户清理为 0；运行后无残留 `uvicorn`/`muad_*.main` 进程。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；E-10 行按 manual 口径登记执行条件与确认人。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | E2E | Runtime → Worker → Schedule | `execution_mode` 路由；Schedule 只触发一次；ONCE `COMPLETED` 无 `next_fire_at` | tests/acceptance/dfx/test_dfx_routing.py + tests/acceptance/task_schedule/test_execution.py + test_schedules.py / S-07 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s07 && uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_schedules.py"]` | planned |
| S-08 | E2E | Worker Parent/Child → fan-in | Child 幂等键；并发受限；fan-in CAS 且只推最终结果 | tests/acceptance/dfx/test_dfx_routing.py + tests/acceptance/task_schedule/test_batch.py / S-08 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s08 && uv run pytest -q tests/acceptance/task_schedule/test_batch.py"]` | planned |
| S-10 | E2E | 授权解析 → Prompt/ToolRegistry → IM 路由 | 未授权不进 Catalog/Prompt；撤销只影响新 Run；多 bot_id 同一 Agent | tests/acceptance/dfx/test_dfx_authorization_scope.py + tests/acceptance/runtime/test_capability_snapshot.py / S-10 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_authorization_scope.py -k s10 && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"]` | planned |
| E-10 | manual | Gateway WS → 企业微信 | SDK backoff 重连成功、`reconnect` 指标可见；**标注「需真实凭据，环境受限时记录原因」** | 人工真机复验记录 / E-10 | - | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写；E-10 为 manual，须由用户确认人在有真实凭据的环境填写，agent 不得代填） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-012: 无状态与 Snapshot 确定性（A/B Pod）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-010
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.2 黄金旅程矩阵, 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-12, RULE-snapshot-001
- **Files**: `tests/acceptance/dfx/test_dfx_stateless.py`
- **Estimate**: 半天级

### Description

FEAT-02 的无状态面：Run R1 执行后更新配置，删除 Pod 后继续 Turn 2 —— R1 的 Snapshot 不漂移、Turn 2 在新 Pod 重建上下文、不依赖 sticky session（同一会话可被任意 Pod 执行）。

### Checklist

- [ ] [S-12][E2E] 以 `Runtime A/B Pod → PostgreSQL + Artifact Store` 为真实边界编写用例：Run R1 后更新配置（Agent/Model/Skill/MCP 任一版本或授权）；删除（真实终止）Pod；继续 Turn 2，断言 R1 Snapshot 的 `snapshot_hash`/内容**不漂移**、Turn 2 在新 Pod 重建上下文、无 sticky session 依赖。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_stateless.py -k s12 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"]`。
- [ ] [RULE-snapshot-001][integration] 作为唯一最终负责人：每个新 Run/Task 执行前冻结 Snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；预算只属 execution snapshot 的 `budget`，Run 侧 `RuntimeSnapshot` 无 budget 列、等价载体是 `policy_json`，默认 `{"max_model_retries": 3}`）；配置/授权变更只影响后续新 Run/Task；终态与非终态写入均 CAS；`skills` 恒为 1；`api_key` 从快照与 hash 中剥离。verifier argv：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`。
- [ ] 补 design 已登记的缺位断言：**「配置/授权变更只影响后续新 Run/Task」当前在指定 verifier 中缺该断言**（harness-snapshot 自注），本任务补齐「变更后旧 Run 仍用旧快照」的真实断言并登记该缺位已收敛；不得只依赖 resume 代码路径的间接证据。
- [ ] 删除 Pod 用真实进程终止（`SIGKILL`），并以真实库回读该 Run 的状态与快照行；不用日志推断，不引入 sticky session 假设。
- [ ] 断言 Snapshot 的确定性口径：hash 为 `"sha256:"` + canonical JSON（`sort_keys` + 紧凑分隔符 + `ensure_ascii=False`），两侧同口径比对。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-12 | E2E | Runtime A/B Pod → PostgreSQL + Artifact Store | R1 Snapshot 不漂移；Turn 2 新 Pod 重建上下文；无 sticky session | tests/acceptance/dfx/test_dfx_stateless.py + tests/acceptance/runtime/test_multipod_recovery.py / S-12 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_stateless.py -k s12 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"]` | planned |
| RULE-snapshot-001 | integration | 冻结快照/终态 CAS + 原 verifier 真实边界 | 冻结与不漂移；变更只影响新 Run；CAS；`skills` 恒 1；`api_key` 剥离；原 verifier 全部通过 | 原 verifier / RULE-snapshot-001 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写；「变更只影响后续新 Run」的断言属新增缺口补齐，需记录 RED 或登记原 verifier 缺位） | | | | | |

### Log
- [2026-09-28] created (draft)

---

## TASK-013: 上线门禁清单、收口清单与仓库级 verifier

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011, TASK-012
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.7 上线门禁映射, 14-dfx-acceptance.backend.design.md#3.4.8 不得 mock 的真实边界清单, 14-dfx-acceptance.backend.design.md#4.2 风险识别, 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-13, RULE-test-001
- **Files**: `tests/dfx_inventory.py`
- **Estimate**: 半天级

### Description

FEAT-07/FEAT-08 的收口：按 design §3.4.7 把 docs/09 §14 的每一项门禁映射到测试或证据路径（SCA/Mend、Image Scan、Secret Scan、DB Migration Dry Run、Golden Journey、Egress Deny、ctx.http allowlist、Snapshot Determinism、A/B Stateless、Runtime/Worker lease reclaim、WeCom reconnect、Model 429、Task deadline sweep、Schedule duplicate-fire、Schedule SKIP、Background final-delivery retry、Batch concurrency limit、Log redaction），未执行项不得记通过；建立 `tests/dfx_inventory.py` 以**真实盘面**交叉核对覆盖表/契约表/证据表、`.acceptance-manifest.json`、`spec-context.yml` 的 required 规则与 E2E 套件是否真的在盘；并承接 `RULE-test-001` 的仓库级真实 E2E verifier。

### Checklist

- [ ] [S-13][manual] **保持 manual，需用户明确确认**：逐项核对 docs/09 §14 门禁清单，每项登记「通过记录或证据路径」；WeCom 真机项标注「需真实凭据」；**不允许以「未执行」冒充通过**。执行条件与确认人由用户给出。
- [ ] [RULE-test-001][E2E] 作为唯一最终负责人：跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确不得 mock 的真实边界；verifier argv：`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`。
- [ ] 先写用例后建清单（结构性 RED）：`tests/dfx_inventory.py` 不存在时按登记命令执行必须先失败，再逐条补齐；核对内容包括：覆盖表每行（含 10 条 RULE 规则行）唯一负责人且除本收口任务外全部终态、manifest 与覆盖表 id/level/owner/command 逐项一致、终态场景与规则行在 owner 的 Acceptance Evidence 中各自登记、每个任务契约表每一行全终态、done/verified 任务零未勾项、10 条 required 规则各有唯一负责人与可执行命令、E2E 命令指向真实在盘套件。
- [ ] 以 mutation 验证清单有牙（改状态/删证据/塞路由拦截均应如期失败），并按字节还原后复跑全绿。执行 argv：`["uv","run","pytest","-q","tests/dfx_inventory.py"]`。
- [ ] 显式边界（不修，只登记）：门禁命令输出不得接入 `head` 一类会提前关闭的管道（SIGPIPE 打断 pytest 收尾并留下孤儿进程，导致后续随机失败）；本条 design 自注**当前无脚本约束**，只能在收口清单里做人工纪律登记，不得写成已机检。
- [ ] 显式边界（不修，只登记）：`docs/09 §14` 中的 SCA/Mend、Image Scan 为 CI 外部工具报告，本任务登记其证据路径而不在本仓复现扫描。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-13 | manual | CI/环境全链路 | 每项门禁有通过记录或证据路径；WeCom 真机标注「需真实凭据」；未执行不得记通过 | 人工门禁核对记录 + docs/09 §14 / S-13 | - | planned |
| RULE-test-001 | E2E | 仓库级真实 E2E（HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | 跨 API/DB/Runtime/Browser 关键流程真实 E2E；分层不降级；原 verifier 全部通过 | tests/acceptance + e2e / RULE-test-001 | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| （编码期填写；S-13 为 manual，须由用户确认人填写，agent 不得代填） | | | | | |

### Log
- [2026-09-28] created (draft)
