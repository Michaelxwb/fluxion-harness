# Tasks: DFX、测试与验收

- **Source**: .code-flow/tasks/2026-09-17/14-dfx-acceptance/（唯一 design：14-dfx-acceptance.backend.design.md）
- **Created**: 2026-09-28
- **Updated**: 2026-10-01
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
| S-03 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | ORM ↔ 迁移 ↔ OpenAPI | TASK-002 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/architecture"] | . | 1200 |  |
| S-04 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | 真实 PostgreSQL + Redis | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_reliability.py","-k","s04"] | . | 900 |  |
| S-05 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Browser/HTTP → Console → PG → IM Gateway | TASK-010 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s05 && uv run pytest -q tests/acceptance/im_gateway/test_binding.py"] | . | 1200 |  |
| S-06 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Gateway → Runtime SSE → Browser | TASK-010 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s06 && uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py"] | . | 1200 |  |
| S-07 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Runtime → Worker → Schedule | TASK-011 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s07 && uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_schedules.py"] | . | 1200 |  |
| S-08 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Worker Parent/Child → fan-in | TASK-011 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s08 && uv run pytest -q tests/acceptance/task_schedule/test_batch.py"] | . | 1200 |  |
| S-09 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Runtime interrupt → resume/cancel | TASK-010 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s09 && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"] | . | 1200 |  |
| S-10 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | 授权解析 → Prompt/ToolRegistry → IM 路由 | TASK-011 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_authorization_scope.py -k s10 && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"] | . | 1200 |  |
| S-11 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Worker → Gateway `/internal/deliveries` → Redis | TASK-006 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k s11 && uv run pytest -q tests/acceptance/task_schedule/test_delivery.py"] | . | 900 |  |
| S-12 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | E2E | Runtime A/B Pod → PostgreSQL + Artifact Store | TASK-012 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_stateless.py -k s12 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"] | . | 1200 |  |
| S-13 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | manual | CI/环境全链路 | TASK-013 | verified | - | . | 60 |  |
| E-01 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Service → PostgreSQL | TASK-004 | verified | ["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_fault_matrix.py","-k","e01"] | . | 900 |  |
| E-02 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Redis → PG | TASK-004 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e02 && uv run pytest -q tests/acceptance/im_gateway/test_redis_degradation.py"] | . | 900 |  |
| E-03 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | emptyDir cache → NFS | TASK-004 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e03 && uv run pytest -q tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py"] | . | 900 |  |
| E-04 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | lease → Reaper → CAS | TASK-005 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e04 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py"] | . | 1200 |  |
| E-05 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Scheduler sweep → PG | TASK-005 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e05 && uv run pytest -q tests/agent_worker/test_task_deadline.py"] | . | 900 |  |
| E-06 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Worker → Gateway → Redis | TASK-006 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k e06 && uv run pytest -q tests/acceptance/im_gateway/test_worker_delivery.py"] | . | 900 |  |
| E-07 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Egress Boundary → Audit/日志/Snapshot | TASK-007 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_security.py -k e07 && uv run pytest -q tests/acceptance/test_secret_consumers.py tests/acceptance/im_gateway/test_secrets_and_readiness.py"] | . | 1200 |  |
| E-08 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | API → RBAC/CSRF/租户谓词 | TASK-008 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_api_security.py -k e08 && uv run pytest -q tests/console_auth/test_rbac.py tests/console_platform/test_credentials_api.py tests/agent_worker/test_tenant_guard.py"] | . | 900 |  |
| E-09 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Runtime 恢复链 → Provider（`AgentRunner._complete_with_recovery` + `AuditedModelProvider`） | TASK-009 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_model_recovery.py -k e09 && uv run pytest -q tests/agent_runtime/test_model_recovery.py"] | . | 900 |  |
| E-10 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | manual | Gateway WS → 企业微信 | TASK-011 | verified | - | . | 60 |  |
| B-01 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | api-kit paginate → API Query | TASK-001 | verified | ["uv","run","pytest","-q","tests/test_error_catalog.py","-k","paginate"] | . | 300 |  |
| B-02 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | SSE 解析器 → Runtime | TASK-002 | verified | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py"] | . | 300 |  |
| B-03 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | integration | Scheduler → Schedule | TASK-005 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k b03 && uv run pytest -q tests/agent_worker/test_scheduler_misfire.py"] | . | 900 |  |
| B-04 | 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景 | contract | locale 资源 ↔ API catalog | TASK-001 | verified | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | . | 600 |  |
| RULE-api-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | contract | 统一封套/分页边界/catalog 错误码 + 原 verifier 真实边界 | TASK-001 | verified | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | . | 300 |  |
| RULE-api-002 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP → 幂等表(PostgreSQL) + 原 verifier 真实边界 | TASK-002 | verified | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | . | 300 |  |
| RULE-arch-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 四部署单元/无状态/依赖方向 + 原 verifier 真实边界 | TASK-002 | verified | ["uv","run","pytest","-q","tests/architecture"] | . | 300 |  |
| RULE-rel-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP 单端点原子变更 + 原 verifier 真实边界 | TASK-002 | verified | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] | . | 300 |  |
| RULE-worker-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 PG 权威源 + Redis 降级 + 原 verifier 真实边界 | TASK-003 | verified | ["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | . | 600 |  |
| RULE-secret-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 密钥明文只存 Owner 表/三处受控出口 + 原 verifier 真实边界 | TASK-007 | verified | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | . | 300 |  |
| RULE-log-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | logging-kit 唯一出口与双通道脱敏 + 原 verifier 真实边界 | TASK-007 | verified | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | . | 300 |  |
| RULE-auth-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | TASK-008 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | . | 600 |  |
| RULE-snapshot-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | integration | 冻结快照/终态 CAS + 原 verifier 真实边界 | TASK-012 | verified | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | . | 600 |  |
| RULE-test-001 | 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix | E2E | 仓库级真实 E2E（HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | TASK-013 | verified | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 1200 |  |

> 本表覆盖 design §2.4.2 的全部 **27 个场景**（S-01..S-13、E-01..E-10、B-01..B-04）与 **10 条 required Spec Rule**，共 **37 行**；每个场景与每条规则有且仅有一个最终负责人（规则行以短 ref 为行键，与 13-console-auth 一致）。**规则行的「测试层级」列沿用 13-console-auth 的口径填 `integration`（`RULE-test-001` 为 `E2E`）**——它表达的是「verifier 命令的执行层级」，规则的验证力度以 argv 指向的 verifier 套件为准，不因此把场景行的 `contract`/`unit` 层级改写。design §2.4.1 的 RULE-01..RULE-10 是 design 级约束，**不建 RULE 行**（映射见 Design Alignment）。测试层级与 design 一致、**不降级**；`manual` 两行（S-13、E-10）保持 manual 且无 argv，**必须经用户明确确认**，E-10 未经真实凭据复验不得写成通过。`depends_on` 列统一留空（场景间顺序由 TASK 依赖表达，manifest 亦按空数组锁定）。E2E 行不接 `head` 一类提前关闭的管道（design §3.4.8 已登记该纪律**无机器约束**）。

---

## TASK-001: 测试分层基线与契约执行聚合（unit/错误码/分页/i18n）

- **Status**: verified
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
- S-01: verified — automated command passed; run_id=0c0d5d06d65944d8a74c482f883bae2c (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=0c0d5d06d65944d8a74c482f883bae2c (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=0c0d5d06d65944d8a74c482f883bae2c (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=0c0d5d06d65944d8a74c482f883bae2c (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-28] started
- [2026-09-28] completed (done)
## TASK-002: 契约一致性、关系形态与架构门禁（parity/幂等指纹）

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.1 测试分层矩阵, 14-dfx-acceptance.backend.design.md#3.4.3 契约与一致性测试清单, 14-dfx-acceptance.backend.design.md#Spec Compliance Matrix
- **Spec-Refs**: harness-api#RULE-api-002, harness-rel#RULE-rel-001, harness-arch#RULE-arch-001
- **Acceptance-Refs**: S-03, B-02, RULE-api-002, RULE-rel-001, RULE-arch-001
- **Files**: `tests/acceptance/dfx/test_dfx_contract_parity.py`, `apps/console-platform/backend/src/muad_console_platform/application/agent_service.py`, `apps/console-platform/backend/src/muad_console_platform/application/mcp_service.py`, `apps/console-platform/backend/src/muad_console_platform/application/skill_service.py`
- **Estimate**: 半天级（含幂等指纹存量漂移的真实 RED）

### Description

FEAT-03 的契约层聚合：ORM↔迁移 parity、契约模型形状、关系接口形态、架构门禁四项**引用既有落点**；本任务新增的断言是 design §3.4.3 登记的**存量漂移**——console 侧 `agent_service`/`mcp_service`/`skill_service` 三处 `_fingerprint` 仍以竖线拼接且不含 `endpoint`/`tenant_id` 判别键，须按 `channel_service`/`auth_service`/`audit_export_service`/`run_submission` 的口径对齐（这是本任务唯一的实现缺口，可产出真实 RED）。另聚合 SSE 封套/seq 契约（B-02）。

### Checklist

- [x] [S-03][contract] 先写用例后改实现：以 `ORM ↔ 迁移 ↔ OpenAPI` 为真实边界，对三处 `_fingerprint` 断言「指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256 且**含 `endpoint` 与 `tenant_id` 判别键**」「同 key 异指纹 → `IDEMPOTENCY_MISMATCH`」「并发由 `pg_advisory_xact_lock` 串行化、partial unique 兜底」；首跑必须先记录 RED（现状竖线拼接、无判别键）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/architecture"]`。**实测 RED：3 failed / 1 passed（指纹值不符 + `_fingerprint` 无 `tenant_id` 入参 TypeError），见 Acceptance Evidence。**
- [x] [S-03][contract] 引用既有 parity 与形状断言并登记 argv（不复制）：`tests/test_contracts.py`（拒绝额外字段、`snapshot_hash`/`delivery_key` pattern）、各域 `test_*_schema_parity.py`（列/类型/可空/PK/FK/索引/partial predicate）、`tests/console_platform/test_user_side_relations.py`（关系接口只允许单关系 POST/DELETE）、`tests/architecture/`（依赖方向、IM Gateway 边界、Pod 标记文本扫描、指标 label 卫生）。**实测：`-k schema_parity` 35 passed（1558 deselected）、`tests/architecture` 10 passed。**
- [x] [B-02][contract] 以 `SSE 解析器 → Runtime` 为真实边界断言 `: heartbeat` 注释帧不计 seq、事件按 seq 单调有序、未知事件类型 unknown-safe、封套公共字段 `{run_id,seq,timestamp,type,data}` 完整。执行 argv：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py"]`。**实测 14 passed。**
- [x] [RULE-api-002][contract] 作为唯一最终负责人：创建/上传类 POST 的 `Idempotency-Key` 幂等（判别键、`IDEMPOTENCY_MISMATCH`、advisory lock 串行化、partial unique 兜底），执行面复用各域既有幂等用例与其 verifier。verifier argv：`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`。**实测 verifier 5 passed；同规则另跑 agent/mcp 真实 HTTP 幂等面（`tests/console_platform/test_agent_idempotency.py` + `tests/console_mcp/test_mcp_idempotency.py`）4 passed。**
- [x] [RULE-rel-001][contract] 作为唯一最终负责人：关系类修改只用单关系 POST/DELETE 且由独立事务完成，禁止全量 PUT 覆盖关系集合；重新绑定复用软删原行（partial unique `WHERE is_deleted=false`）而非插新行；解除关系的幂等语义按端点显式声明（Agent 侧幂等成功 vs User 侧 `COMMON_NOT_FOUND`）。verifier argv：`["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]`。**实测 3 passed。**
- [x] [RULE-arch-001][contract] 作为唯一最终负责人：四个部署单元固定、Runtime/Worker 无状态可横向扩展、不绑定 Pod/bot_id/用户；同一会话可被任意 Pod 执行。verifier argv：`["uv","run","pytest","-q","tests/architecture"]`。**实测 10 passed。**
- [x] 显式边界（不修，只登记）：`harness-arch` 已登记的两处现状差异——IM Gateway 只有 `install_metrics`、零 `declare_metric`（`/metrics` 目录为空）；四个 k8s 清单的 `readinessProbe`/`livenessProbe` 均指向 `/healthz`，api-kit 的 `/readyz` 未被部署消费。本任务只断言 `tests/architecture` verifier 的现行口径，**不改部署清单、不补 gateway catalog**。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | contract | ORM ↔ 迁移 ↔ OpenAPI | 指纹含 `endpoint`/`tenant_id`；异指纹 `IDEMPOTENCY_MISMATCH`；列/类型/索引/partial predicate 一致；关系只单端点变更 | tests/acceptance/dfx/test_dfx_contract_parity.py / S-03 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03 && uv run pytest -q tests -k schema_parity && uv run pytest -q tests/architecture"]` | verified |
| B-02 | contract | SSE 解析器 → Runtime | heartbeat 不计 seq；seq 单调；未知事件 unknown-safe；封套字段完整 | tests/agent_runtime/test_sse.py + tests/gateway/test_sse_parser.py / B-02 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py"]` | verified |
| RULE-api-002 | integration | 真实 Console HTTP → 幂等表(PostgreSQL) + 原 verifier 真实边界 | 判别键齐备；异指纹 409；advisory lock 串行化；原 verifier 全部通过 | 原 verifier / RULE-api-002 | `["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]` | verified |
| RULE-rel-001 | integration | 真实 Console HTTP 单端点原子变更 + 原 verifier 真实边界 | 无全量 PUT；单关系 POST/DELETE；复活原行；原 verifier 全部通过 | 原 verifier / RULE-rel-001 | `["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]` | verified |
| RULE-arch-001 | integration | 四部署单元/无状态/依赖方向 + 原 verifier 真实边界 | 部署单元与无状态约束；依赖方向单向；原 verifier 全部通过 | 原 verifier / RULE-arch-001 | `["uv","run","pytest","-q","tests/architecture"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | **真实 RED**：`uv run pytest -q tests/acceptance/dfx/test_dfx_contract_parity.py -k s03` → 3 failed / 1 passed。mcp `actual=sha256:bfdabf53e1f37d7248a4cedd06fc3676644c2ee714f9bb0581e85bf5bd8c9d33` vs 期望 `sha256:4c15525f49ceeed4a6bca2efda1bb42d0f3dafe1aff635ab8cd65883f76ed5a1`；skill `actual=sha256:ab8f28b129fb50a97ee09a2295f30e8ce1fe04a832abeb61a1df0752c8a91714` vs 期望 `sha256:b7b924f2d795f2716d04ac0161be3ae72e9850481b3a3f7df6690afe09dc463f`；agent `TypeError: AgentService._fingerprint() takes 2 positional arguments but 3 were given`（`tenant_id` 根本不在入参里）。漂移输入实测为竖线拼接、无 `endpoint`/`tenant_id`、无 `sort_keys`/紧凑分隔符：`mcp-register\|{payload_json}\|{sha256(secret)}`、`import\|1.0.0\|skill-key\|None\|SELECTED\|sha256:5f68c099…`、`{payload_json}\|agent-key` | `-k s03` → **4 passed**；整条 argv 三段 → **4 passed / 35 passed（1558 deselected）/ 10 passed** | `tests/acceptance/dfx/test_dfx_contract_parity.py` 4 例：`test_s03_agent_create_fingerprint_is_canonical_json_with_discriminators`、`test_s03_mcp_register_fingerprint_is_canonical_json_with_discriminators`、`test_s03_skill_fingerprints_are_canonical_json_with_discriminators`、`test_s03_idempotency_lock_and_unique_index_are_endpoint_scoped` | 期望值由测试独立重算（`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` + `sha256:` 前缀），**不从实现取值**；判别键断言含「丢键 ⇒ 摘要变」「换租户/换端点名 ⇒ 换指纹」「换载荷/换包体 ⇒ 换指纹」「与 `json.dumps` 默认口径不同」；幂等表形态取自 ORM 元数据（`SkillImportIdempotency.__table__` 的 unique 索引列集 `{tenant_id,idempotency_key,endpoint}` + partial predicate `is_deleted = false`）；锁与重放口径取自实现源码（按 `(tenant_id, key, endpoint)` 派生 `pg_advisory_xact_lock`、异指纹抛 `IDEMPOTENCY_MISMATCH`）。全程未 mock、无 DB/网络。实现缺口已修：三处 `_fingerprint` 改为规范 JSON 口径（agent/mcp 新增 `tenant_id` 入参，skill 新增 `endpoint`/`tenant_id` 入参）；`_fingerprint` 仍嵌在服务类内（非模块级纯函数），测试以显式 `_UNUSED_SELF` 占位调用其最小可调用面 | verified |
| B-02 | 无独立 RED（本任务未改 SSE 面，按 design §3.4.3 引用既有落点） | `uv run pytest -q tests/agent_runtime/test_sse.py tests/gateway/test_sse_parser.py` → **14 passed** | 既有用例：`tests/agent_runtime/test_sse.py`、`tests/gateway/test_sse_parser.py` | SSE 解析器 ↔ Runtime 事件流（heartbeat 不计 seq、seq 单调、未知类型 unknown-safe、封套字段完整）；未 mock | verified |
| RULE-api-002 | 见 S-03 行（同规则的真实 RED 即三处指纹漂移）；原 verifier 侧无独立 RED | verifier argv `uv run pytest -q tests/console_skill/test_import_idempotency.py` → **5 passed**；同规则真实 HTTP 幂等面另跑 `tests/console_platform/test_agent_idempotency.py` + `tests/console_mcp/test_mcp_idempotency.py` → **4 passed** | 原 verifier 自身断言（同 key 重放返回首次结果、同 key 异载荷 `IDEMPOTENCY_MISMATCH`、并发同 key 只落一行）+ 新增契约用例的判别键/规范化 JSON/partial unique/锁派生断言 | 真实 Console HTTP（ASGI 上传）→ 真实 PostgreSQL `control.skill_import_idempotency`；未 mock | verified |
| RULE-rel-001 | 无独立 RED（本任务未改关系接口） | verifier argv `uv run pytest -q tests/console_platform/test_user_side_relations.py` → **3 passed** | 原 verifier 自身断言（关系只用单关系 POST/DELETE、无全量 PUT 覆盖、重新绑定复用软删原行） | 真实 Console HTTP 单端点原子变更 + 真实 PostgreSQL；未 mock | verified |
| RULE-arch-001 | 无独立 RED（未改部署清单与依赖方向） | verifier argv `uv run pytest -q tests/architecture` → **10 passed** | `tests/architecture/` 既有断言（依赖方向单向、IM Gateway 不持库且渠道 SDK 只在 `channels/`、Pod 标记文本扫描、指标 label 卫生） | 源码/清单静态扫描（真实 `apps/`+`packages/` 源码与 k8s 清单）。显式边界：IM Gateway 零 `declare_metric`、四份清单探针仍指 `/healthz`，本任务只断言现状口径，不改部署清单 | verified |
- S-03: verified — automated command passed; run_id=1f09357ded7543ddb63b2d063438d62a (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=1f09357ded7543ddb63b2d063438d62a (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=fcb8746c587e4007b920ab71d5d1a6bc (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=fcb8746c587e4007b920ab71d5d1a6bc (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-28] started
- [2026-09-28] completed (done)
## TASK-003: 可靠性验收基座与 Worker lease/claim

- **Status**: verified
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

- [x] [S-04][integration] 以 `真实 PostgreSQL + Redis` 为真实边界编写用例：同一 Task 并发提交两次只被一个 Worker claim（`FOR UPDATE SKIP LOCKED`，落败者不重复执行）；执行中 heartbeat 续租使 `lease_until` 前移；跨租户查询不可见。执行 argv：`["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_reliability.py","-k","s04"]`。**实测：5 passed in 25.31s**（租户 `dfx-reliability-<hex>`，Console/Worker 真实子进程 + 真实 PG/Redis）。
- [x] [RULE-worker-001][integration] 作为唯一最终负责人：PG 是 Task/Schedule/lease 唯一权威源、Redis 仅 wake-up/cancel hint、`task_type` 仅 `SKILL/BATCH`、claim 用 `FOR UPDATE SKIP LOCKED`、进入 WAITING 释放 lease、deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`（后三条的**场景**归 TASK-005，规则责任在本任务）。verifier argv：`["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]`。**实测 verifier 两段 = 234 passed（10.00s）+ 142 passed（4.89s）；本任务新增的 DFX 口径用例（PG 权威/Redis 仅 hint/`task_type` 枚举与 PG 实值/claim SKIP LOCKED）同批取证。**
- [x] 种子与清理：唯一租户 + 可识别 key 前缀；收尾清理本租户残留为 0；任何失败路径都要执行清理，运行后不得残留 `uvicorn`/`muad_*.main` 进程。**实测：租户前缀 `dfx-reliability-`（含跨租户对照 `dfx-reliability-cross-*`）、产物根在 `tmp_path_factory` 的系统临时目录；`cleanup()` 在 `finally` 中执行并对 14 张表逐一回读计数，全部为 0（含 `control.config_audit_log`）；运行后 `ps aux | grep -E "uvicorn|muad_.*main"` 计数为 0。**
- [x] 真实边界断言取材于真实库：claim/租约状态一律从 PG 逐行回读（`lease_owner`/`lease_until`/`status`），不以日志或返回值代替。**实测：`read_task_row()` 逐行回读 `status/task_type/attempt/lease_owner/lease_until/heartbeat_at/cancel_requested/result_json/finished_at`；「落败者未执行」以 PG 的 `attempt=1` + `task_event` 中 `CLAIMED` 计数=1 + `result_json IS NULL` 判定。**
- [x] 复用既有验收栈原语（`ServiceProcess`/`free_port`/迁移到 head）与 `tests/acceptance/task_schedule|runtime` 的环境口径，不新建第二套进程管理；租户与产物根钉在系统临时目录。**实测：`environment.py` 直接 import 08/09 栈的 `ServiceProcess`/`free_port`/`require`/`run_db`/`run_async`/`build_skill_zip` 与 `TASK_CLEANUP`/`CONTROL_CLEANUP`；未新写进程管理器。**
- [x] 显式边界（不修，只登记）：Redis 的既有部分用例仍使用替身（design 技术债②）——本任务与 TASK-004 只在集成/E2E 层接真实 Redis，不去逐个改造模块单测。**本任务实测口径：`SET NX EX`+TTL 往返（`ttl=5`）、Worker `/readyz` 的 `wakeup_hint=redis`；未改动任何模块单测的替身。**
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。**本任务无生产缺口，RED 行为「无 RED（新用例覆盖既有行为，如实登记）」并以三处抖动取证补足非空性（见 Evidence）。**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | 真实 PostgreSQL + Redis | 单一 claim；heartbeat 续租；租户隔离查询通过 | tests/acceptance/dfx/test_dfx_reliability.py / S-04 | `["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_reliability.py","-k","s04"]` | verified |
| RULE-worker-001 | integration | 真实 PG 权威源 + Redis 降级 + 原 verifier 真实边界 | PG 为唯一权威源；Redis 仅 hint；claim/WAITING/deadline 口径；原 verifier 全部通过 | 原 verifier / RULE-worker-001 | `["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | **无 RED（新用例覆盖既有行为，如实登记）**：本任务无生产缺口——claim/heartbeat/租户谓词均已按 design 口径实现，首跑即绿。为证明断言非空，做了三处**抖动取证**（改生产代码 → 对应断言失败 → 按字节还原 → 复绿，`git diff -- apps/` 为空）：① `apps/agent-worker/src/muad_agent_worker/worker/claimer.py` 去掉 `.with_for_update(skip_locked=True)` → `test_s04_claim_skips_row_locked_by_another_transaction` 失败 `AssertionError: 行被持锁时 claim 不该领到它（SKIP LOCKED 语义）` / `assert "ERROR:Error:<class 'asyncpg.exceptions.QueryCanceledError'>: canceling statement due to statement timeout" is None`；② `worker/service.py::_renew_lease` 去掉 `lease_until` 更新 → `test_s04_heartbeat_renews_lease_until_in_pg` 失败 `AssertionError: heartbeat 未续租：lease_until 2026-09-28 13:23:21.225248+00:00 → 2026-09-28 13:23:21.225248+00:00`；③ `claimer.py` 整段去掉 `.with_for_update(...)`（非原子 claim）→ `test_s04_single_claim_race_has_exactly_one_winner` 失败 `并发 claim 的赢家集合不符：[2985b44d-…, 2985b44d-…]`（同一 task id 出现两个赢家）。三次还原后逐一复跑均复绿。 | `-k s04` → **5 passed in 25.31s**（抖动还原后复跑同为 5 passed） | `tests/acceptance/dfx/test_dfx_reliability.py` 5 例：`test_s04_single_claim_race_has_exactly_one_winner`、`test_s04_heartbeat_renews_lease_until_in_pg`、`test_s04_cross_tenant_query_returns_nothing`、`test_s04_claim_skips_row_locked_by_another_transaction`、`test_s04_pg_is_authority_and_redis_is_hint_only`（均 `@pytest.mark.integration`） | 真实 PG 逐行回读：竞态 `claimers_returned=[null, <race_id>]`、PG `status=RUNNING`/`lease_owner=dfx-claimer-b`/`attempt=1`/`CLAIMED 事件=1`/`result_json IS NULL`；Worker 空闲后盘面**逐字段未变**且本租户内 `RUNNING` 行=1、`lease_owner IS NOT NULL` 行=1；heartbeat `lease_owner=hostname:pid`（真实 Worker 子进程，pid≠测试进程）、`lease_until` 13:25:48.125114+00:00 → 13:25:51.152236+00:00（Δ=3.027s）、`heartbeat_at` 同步前移、`status=RUNNING`、`attempt=1`，终态 `COMPLETED`+租约清空+`result_json={"probe":"hb","slept_sec":8.0}`；SKIP LOCKED 对照（另一事务 `FOR UPDATE` 持锁）被 statement_timeout 取消、claim `claimed=None`/`elapsed_sec=0.008`/`status` 仍 `QUEUED`/`CLAIMED 事件=0`；跨租户 `same_tenant=1 / other_tenant=0 / read_row_other=None`；真实 Redis `ping=True`、`SET NX` 首真次假、`ttl=5`、Worker `/readyz` `wakeup_hint=redis`、`/healthz`（Console/Worker）=200；收尾 14 张表残留计数全 0。全程未 mock 真实边界；租户 `dfx-reliability-<hex>`、产物根在系统临时目录。 | verified |
| RULE-worker-001 | 承接（原 verifier），无独立 RED：本任务未改 claim/lease 生产实现；同实现的抖动取证见 S-04 行（①②③）。更宽的扰动（去掉 claim 的状态谓词）会先撞上共享开发库的历史终态残留行、不构成干净取证，故未采用（该次扰动把既有残留行 `ignored-by-header` 由 `COMPLETED` 改为 `FAILED`，属其他套件的历史测试残留，如实登记）。 | verifier argv 两段全过：`tests/agent_worker` → **234 passed（10.00s）**；`tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py` → **142 passed（4.89s）**。本任务新增的口径用例（PG 唯一权威源/Redis 仅 hint/`task_type` 仅 `SKILL/BATCH`/claim SKIP LOCKED）随 S-04 一批 5 passed。blast radius 单跑：`tests/agent_worker` 234 passed、`tests/agent_runtime` 149 passed、`tests/console_tasks` 19 passed。 | 原 verifier 自身断言（`tests/agent_worker/test_worker_leases.py` 的单租约/回收/CAS 面等）+ 本任务新增断言：`TaskType` 枚举封闭 `{"SKILL","BATCH"}` 且 PG 实际取值 ⊆ 该集合；Redis `task:cancel:{uuid}`/`task:wakeup` hint 对不存在的 Task 既造不出行也改不了任何真实事实（PG 行数 before==after）；行被他人事务持锁时 claim 必须跳过而非等待（`FOR UPDATE SKIP LOCKED`）。 | 真实 PG（`task.task_execution`/`task.task_event`/`task.task_submission` 逐行回读与计数）+ 真实 Redis（`SET NX EX`/TTL/PING 往返）+ 真实 Console/Worker uvicorn 子进程；未 mock。本任务不宣称 TASK-005 拥有的场景（WAITING 释放 lease、deadline sweep、过期 reclaim）。 | verified |
- S-04: verified — automated command passed; run_id=280e260563b343178fea186511d5a0c9 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=c5d27f0b9f0541a6be6083bdd6ae6aed (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-28] started
- [2026-09-28] completed (done)
## TASK-004: 依赖故障矩阵（PG/Redis/Artifact Store）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#3.4.8 不得 mock 的真实边界清单, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-01, E-02, E-03
- **Files**: `tests/acceptance/dfx/test_dfx_fault_matrix.py`, `tests/acceptance/dfx/environment.py`
- **Estimate**: 半天级

### Description

FEAT-04 的依赖故障面：PG 不可用 fail closed（不本地落状态、`/readyz` 失败、恢复后事实完整）、Redis 不可用降级 at-least-once（PG 事实不丢、恢复后去重生效）、Artifact Store/NFS 不可用或慢时已有 READY 可继续、cache miss 与新写入明确失败且不返回假成功。

### Checklist

- [x] [E-01][integration] 以 `Service → PostgreSQL` 为真实边界编写故障注入用例：停 PG/连接失败 → fail closed（不本地落状态）、`/readyz` 返回失败（503 语义）、恢复后业务事实完整。执行 argv：`["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_fault_matrix.py","-k","e01"]`。**实测：`-k e01` → 1 passed in 14.12s。注入方式为「真实连接失败」而非停真实 PG（该 PG 是外部共享服务）：测试进程内起真实 TCP 转发器（`_TcpRelay`），被测 Worker 的 `DATABASE_URL` 指向转发端口，`fail()` 后新连接被拒、已建立连接被切断；窗口内以测试进程自身 `SELECT 1` 为正对照。**
- [x] [E-02][integration] 以 `Redis → PG` 为真实边界断言 Redis 停止时 cache miss / dedupe 降级为 at-least-once、PG 数据完整、恢复后去重重新生效；既有真实降级用例一并登记 argv（引用不复制）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e02 && uv run pytest -q tests/acceptance/im_gateway/test_redis_degradation.py"]`。**实测：本任务段 1 passed in 4.15s；既有降级套件 2 passed in 41.66s。真实 Redis 未被停：被测 Worker 的 `REDIS_URL` 指向不可达端点 `redis://127.0.0.1:1/0`（与既有套件同口径）。**
- [x] [E-03][integration] 以 `emptyDir cache → NFS` 为真实边界断言已有 READY 可继续执行；cache miss 与新 Artifact 写入明确失败为 `SKILL_ARTIFACT_UNAVAILABLE`，**不返回假成功**。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e03 && uv run pytest -q tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py"]`。**实测：本任务段 1 passed in 6.67s；既有套件 7 passed in 0.02s。注入方式：被测 Worker 的 Artifact 根先真实存在（真实 zip 拷贝 + PG 登记 checksum），随后 `rename` 成同名普通文件 → `is_dir()` 为假、`resolve/open` 得 `ENOTDIR`；复位时换回目录。**
- [x] 故障注入必须真实可复位：注入前后各取一次真实盘面（PG/Redis/Artifact 目录），并断言复位后无残留（无孤儿进程、无残留 key、无残留对象）。**实测：三例均以 `_assert_residue_free` 核对——本运行 task id 的 `task:cancel:*` = 0、本用例投递去重键（前缀 `delivery:dedupe:dfx-fault-matrix*`）= 0、本运行 Task 对应的 `delivery:dedupe:task:{id}:final` = 0（`delivery_mode=NONE` 不该产生）、真实 Artifact 根文件清单逐项未变、服务进程恰好 `muad_console_platform.main`×1 + `muad_agent_worker.main`×1（注入进程已在 `finally` 停掉并复位基座 Worker）。跑完全部扰动后复核：`ps -Aww | grep -E "uvicorn|muad_.*main"` = 0 行；本租户 12 张表残留 = 0；Redis 无 `task:cancel:*`/`task:wakeup`/`dfx-fault-matrix*` 残留键。**
- [x] 显式边界（不修，只登记）：E-03 的「NFS 高延迟」部分依赖环境能力（design 技术债③），环境不具备时按 manual 口径记录原因，**不得**用本地替身冒充 NFS 慢故障。**本次实测口径：本机（macOS 本地文件系统，无 NFS 挂载）无法产生可复现的真实 NFS 慢故障注入，故「NFS 高延迟」按 manual 口径登记（原因：环境不具备真实 NFS-backed 慢 IO 能力），未以本地替身冒充。E-03 的机检仅覆盖「Store 不可用」这一半（已有 READY 继续 + cache miss/新写入明确失败），不宣称已覆盖慢故障。**
- [x] 每条失败路径都要断言「不返回假成功」：失败必须显式（错误码/状态），不得静默吞错或返回空结果。**实测：E-01 提交路径断言 `status_code == 500` + 封套 `code == COMMON_INTERNAL_ERROR`（未落任何 PG 行、未写本地缓存）；E-02 断言运行期 `DedupeStoreError` 显式抛出 + 降级态 `is_duplicate == False`（不假称重复而丢发）+ `/readyz` 明确上报 `wakeup_hint=disabled`；E-03 断言 `error_code == SKILL_ARTIFACT_UNAVAILABLE`、`result_json IS NULL`、`FAILED` 事件=1 且 cache 目录零残留（无假 READY）。**
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。**本任务三场景均无生产缺口，RED 行统一为「无 RED（新用例覆盖既有行为，如实登记）」，并以 6 处生产代码抖动取证证明断言非空（6 处均在最终文件版本上复验，`git status --porcelain -- packages/ apps/` 为空；详见 Evidence）。**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | Service → PostgreSQL | fail closed 不落本地状态；`/readyz` 失败；恢复后事实完整 | tests/acceptance/dfx/test_dfx_fault_matrix.py / E-01 | `["uv","run","pytest","-q","tests/acceptance/dfx/test_dfx_fault_matrix.py","-k","e01"]` | verified |
| E-02 | integration | Redis → PG | cache miss/dedupe 降级 at-least-once；PG 完整；恢复后去重生效 | tests/acceptance/dfx/test_dfx_fault_matrix.py + tests/acceptance/im_gateway/test_redis_degradation.py / E-02 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e02 && uv run pytest -q tests/acceptance/im_gateway/test_redis_degradation.py"]` | verified |
| E-03 | integration | emptyDir cache → NFS | 已有 READY 可继续；新写入明确 `SKILL_ARTIFACT_UNAVAILABLE`；不假成功 | tests/acceptance/dfx/test_dfx_fault_matrix.py + tests/acceptance/test_foundation_artifact.py + tests/test_skill_artifact_cache.py / E-03 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_fault_matrix.py -k e03 && uv run pytest -q tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-01 | **无 RED（新用例覆盖既有行为，如实登记）**：本任务无生产缺口——PG 不可达时 `/readyz` 已由 `database_readiness` 返回 503、提交路径已 fail closed（未落 PG 行、未写本地状态），首跑即绿。为证明断言非空，做了两处**抖动取证**（改生产代码 → 对应断言失败 → 按字节还原 → 复绿，`git status --porcelain -- packages/ apps/` 为空）：① `packages/api-kit/src/muad_api/probes.py::database_readiness` 的 `except Exception: return False` 改为 `return True`（就绪检查 fail-open）→ 失败 `AssertionError: http://127.0.0.1:59903/readyz 未在 30.0s 内降级：200 {'status': 'ready', ...}`；② `packages/api-kit/src/muad_api/handlers.py` 通用异常处理器 `_json_response(body, 500)` 改为 `200`（把内部错误伪装成 HTTP 成功）→ 失败 `AssertionError: PG 不可用时提交必须显式失败：200 / assert 200 == 500`。两次还原后 `-k e01` 均复绿。 | `-k e01` → **1 passed in 14.12s**（六处抖动均在最终文件版本上复验：改生产代码 → 断言失败 → 按字节还原 → 复绿） | `tests/acceptance/dfx/test_dfx_fault_matrix.py::test_e01_postgres_unavailable_fails_closed_and_recovers`：`/readyz` 503 且 `failed` 含 `database`；`/healthz` 仍 200（存活与依赖分离）；窗口内测试进程 `SELECT 1` 成功（正对照：真实 PG 未被触碰）；`POST /internal/tasks` = 500 + 封套 `code=COMMON_INTERNAL_ERROR`；`task.task_execution`/`task.task_submission` 按该 idempotency_key 计数 = 0；本地 cache 根零写入；`relay.recover()` 后 `/readyz` 复 200、故障前任务行逐字段未变（`read_task_row(pre_fault_id) == pre_fault_row`）、新任务 COMPLETED 且 `result_json={"slept_sec": 0.0, "probe": "after-recovery"}` | **真实连接失败（不停真实 PG）**：测试进程内真实 TCP 转发器 `_TcpRelay` 承接被测 Worker 的 `DATABASE_URL`；`fail()` 拒绝新连接并切断已建立连接（asyncpg 得到真实网络错误），`recover()` 后同一进程重连成功；真实 PostgreSQL（外部共享服务）全程未被停止/重启/改配置。被注入的边界是「Service → PostgreSQL」这一段连接；Worker 是真实 uvicorn 子进程（`muad_agent_worker.main`，复用基座 Worker 端口），未 mock | verified |
| E-02 | **无 RED（新用例覆盖既有行为，如实登记）**：Redis 不可用时 Worker 已降级为 PG 扫描（`wakeup_hint=disabled`）、任务照常 claim/执行、网关去重已降级 `NullDedupeStore`（at-least-once），首跑即绿。为证明断言非空，做了两处**抖动取证**（按字节还原后 `git status --porcelain -- apps/` 为空）：① `apps/agent-worker/src/muad_agent_worker/main.py` 的降级上报 `"disabled"` 改为 `"redis"`（把降级伪装成正常）→ 失败 `AssertionError: 降级模式必须被明确上报：{'wakeup_hint': 'redis', ...} / assert 'redis' == 'disabled'`；② `apps/im-gateway/src/muad_im_gateway/infrastructure/dedupe.py::RedisDedupeStore.set_if_absent` 的 `nx=True` 改为 `nx=False`（去重永不生效）→ 失败 `AssertionError: 恢复后去重未重新生效：{... 'restored_second_duplicate': False} / assert False is True`。两次还原后 `-k e02` 均复绿；扰动期写入的探针键在 `finally` 中 `release` 删除，复核 Redis 无 `delivery:dedupe:dfx-fault-matrix*` 残留。 | `-k e02` → **1 passed in 4.15s**（复合 argv 首段 4.78s）；既有降级套件 `tests/acceptance/im_gateway/test_redis_degradation.py` → **2 passed in 41.66s**（复合 argv 两段全过） | `test_e02_redis_unavailable_degrades_to_at_least_once_and_recovers`：不可达端点真实 `ConnectionError` + 运行期 `DedupeStoreError`（生产 `RedisDedupeStore`）；启动期 `build_dedupe_store(DEAD)` → `NullDedupeStore` 且 `is_duplicate == False`（不假称重复而丢发）；降级 Worker `/readyz` 200 且 `wakeup_hint == "disabled"`、`/healthz` 200；经降级 Worker 提交的任务 COMPLETED 且 `result_json == {"slept_sec": 0.0, "probe": "no-redis"}`、`finished_at` 非空；恢复（真实 Redis）后 `RedisDedupeStore` 首次 `False`、二次 `True`（去重重新生效） | **真实 Redis 未被停**：被测 Worker 的 `REDIS_URL` 指向不可达端点 `redis://127.0.0.1:1/0`（与 `tests/acceptance/im_gateway/test_redis_degradation.py` 同口径）；去重口径直接复用生产类 `build_dedupe_store`/`is_duplicate`/`RedisDedupeStore`（网关启动与运行期同一实现），不手写替身。端到端 at-least-once（两次投递都真实发送）由既有降级套件承接，本用例只登记其 argv（引用不复制） | verified |
| E-03 | **无 RED（新用例覆盖既有行为，如实登记）**：Artifact 根由目录换成普通文件后，`SkillArtifactCache` 已按设计「先查本地 READY、miss 才回源」，miss 时显式 `SKILL_ARTIFACT_UNAVAILABLE`、Worker `/readyz` 的 `artifact_storage` 已为假，首跑即绿。为证明断言非空，做了两处**抖动取证**（按字节还原后 `git status --porcelain -- packages/artifact-store/` 为空）：① `packages/artifact-store/src/muad_artifact_store/skill_cache.py::ensure` 的两处 `if (final_dir / "READY").exists():` 短路改为 `if False:`（丢掉本地 READY 语义、每次都回源）→ 失败 `AssertionError: task 57daebb7-… 未在 60.0s 内到达 COMPLETED，最后盘面：{... 'status': 'FAILED', 'attempt': 3, 'error_code': 'SKILL_ARTIFACT_UNAVAILABLE'}`（既有 READY 不再能继续执行）；② `_prepare` 的 miss 分支 `raise SkillArtifactCacheError("SKILL_ARTIFACT_UNAVAILABLE")` 改为写一个假 READY 并 `return final_dir`（注入假成功）→ 失败 `AssertionError: {'attempt': 1, ..., 'error_code': 'SKILL_EXECUTION_FAILED', ...} / assert 'SKILL_EXECUTION_FAILED' == 'SKILL_ARTIFACT_UNAVAILABLE'`（失败被错标为普通执行失败）。两次还原后 `-k e03` 均复绿；扰动只落在本用例自建的 tmp 产物根/缓存上，未触碰真实 Artifact 根与共享租户数据。 | `-k e03` → **1 passed in 6.67s**（复合 argv 首段 9.16s）；既有套件 `tests/acceptance/test_foundation_artifact.py tests/test_skill_artifact_cache.py` → **7 passed in 0.02s**（复合 argv 两段全过） | `test_e03_artifact_store_unavailable_keeps_ready_and_fails_new_writes`：注入后 `not store_root.is_dir()`、`(detached / storage_key).is_file()`（包仍在、只是被换位）、`not (store_root / storage_key).exists()`、`/readyz` 503 且 `failed` 含 `artifact_storage` 且 `artifact_root` 指向注入路径；臂 A（本地已有 READY）COMPLETED 且 `result_json == {"slept_sec": 0.0, "probe": "cache-hit"}`、READY 文件内容逐字节未变；臂 B（同一 artifact、cache 为空、`TASK_MAX_ATTEMPTS=1`）FAILED、`error_code == "SKILL_ARTIFACT_UNAVAILABLE"`、`result_json IS NULL`、`finished_at` 非空、`FAILED` 事件 = 1、cache 目录零条目（无假 READY/半成品）；臂 C（存储复位后）COMPLETED 且新写入落下 READY | **真实不可读的存储**：注入用的 Artifact 根是本租户真实 zip 的逐字节拷贝（`_digest(target) == stack.checksum`，与 PG 登记 checksum 一致），先由生产 `SkillArtifactCache` 真实落 READY；随后把该根换成同名普通文件（`ENOTDIR`）——被测 Worker（真实 uvicorn 子进程）配置的源路径真实不可读。全流程未 mock；真实 Artifact 根文件清单在用例前后逐项一致 | verified |
- E-01: verified — automated command passed; run_id=d53ddeaebdc74018ade4f3458abbae87 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=d53ddeaebdc74018ade4f3458abbae87 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=d53ddeaebdc74018ade4f3458abbae87 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=0b03002f86d54960aae2f0504eb80c61 (confirmed_by: runner)
- E-02: failed — automated command failed; run_id=0b03002f86d54960aae2f0504eb80c61 (confirmed_by: runner)
- E-03: incomplete — automated command incomplete; run_id=0b03002f86d54960aae2f0504eb80c61 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=ed3f19f39aa04becb2a888473d62f83e (confirmed_by: runner)
- E-02: failed — automated command failed; run_id=ed3f19f39aa04becb2a888473d62f83e (confirmed_by: runner)
- E-03: failed — automated command failed; run_id=ed3f19f39aa04becb2a888473d62f83e (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=d305f3014b19408ba310c76588f5e5d0 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=d305f3014b19408ba310c76588f5e5d0 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=d305f3014b19408ba310c76588f5e5d0 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=f84de0ab660b477eb55fa5008911c0f5 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=f84de0ab660b477eb55fa5008911c0f5 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=f84de0ab660b477eb55fa5008911c0f5 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-28] started
- [2026-09-29] completed (done)
## TASK-005: 租约回收、deadline sweep 与 Schedule 边界

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-04, E-05, B-03
- **Files**: `tests/acceptance/dfx/test_dfx_recovery.py`, `tests/acceptance/dfx/environment.py`（扩展 TASK-003/004 的 DFX 基座：`submit_task`/`post_task_request` 增加 `delivery_mode`/`delivery_route` 入参、`read_task_row` 增读投递列——E-05 需要从真实 PG 读投递事实，故按本任务要求把该文件一并登记）, `tests/acceptance/dfx/test_dfx_fault_matrix.py`（**仅**把 `_service_commands` 的进程判据由「命令行里出现 `uvicorn` 且 `muad_`」改为「`ucomm` 是 Python 进程且命令行为 `-m uvicorn muad_*`」：旧口径会把调用方自己的 shell（命令行含该子串，如 `grep -E "uvicorn|muad_"`）算成第三个服务进程，使 E-01..E-03 假失败；E-01..E-03 的断言与本任务无关的部分一字未改）
- **Estimate**: 半天级

### Description

崩溃恢复面：kill Worker/Runtime Pod 且 lease 过期后 Task 被其他 Worker reclaim、过期 RUNNING 的 Run 被 CAS 置 `FAILED(RUN_ABANDONED)` 并释放会话；Task 超过 `deadline_at` 后 30s 内 CAS 置 `FAILED(TASK_DEADLINE_EXCEEDED)` 且仍按 `delivery_mode` 投递；Schedule 错过触发只 SKIP 不补发并计数、ONCE 成功后 `COMPLETED` 且 `next_fire_at` 为空。

### Checklist

- [x] [E-04][integration] 以 `lease → Reaper → CAS` 为真实边界编写用例：真实子进程 `SIGKILL` 后等 lease 过期，断言 Task 被其他 Worker reclaim、过期 RUNNING Run 被 CAS 置 `FAILED(RUN_ABANDONED)` 且会话被释放；既有真实多 Pod 回收用例一并登记 argv（引用不复制）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e04 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py"]`。**实测：本段 1 passed（44.24s）；引用段 3 passed（13.74s）。短租约由「基座 Worker 端口上换入 `TASK_LEASE_SEC=6`/`TASK_HEARTBEAT_SEC=1` 的 Worker」提供（基座默认 30s）；接手者用基座租约（30s 心跳 1s，30x 余量）。Task 侧由本用例取证；Run 侧（`FAILED(RUN_ABANDONED)` 与会话释放/接管）由被引用的 `tests/acceptance/runtime/test_multipod_recovery.py::test_s04_e07_process_kill_reaped_and_takeover` 承接，本任务只登记其 argv（RULE-10 引用不复制）。**
- [x] [E-05][integration] 以 `Scheduler sweep → PG` 为真实边界断言超过 `deadline_at` 后 30s 内 CAS 置 `FAILED(TASK_DEADLINE_EXCEEDED)`，**且终态仍按 `delivery_mode` 投递**（投递事实落库）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e05 && uv run pytest -q tests/agent_worker/test_task_deadline.py"]`。**实测：本段 1 passed（51.56s）；引用段 6 passed（0.31s）。真实 Scheduler 节拍（30s sweep 间隔 + 10s 轮询拍点，未注入小值）把两行 CAS 成失败终态；`FINAL_ONLY` 臂经真实 IM Gateway → 真实渠道探针置 `SENT`（投递事实落库 + 探针收到），`NONE` 臂在同一 12s 安静窗口内零投递事实。**
- [x] [B-03][integration] 以 `Scheduler → Schedule` 为真实边界断言 misfire 只 SKIP 不补发且计数可见、ONCE 成功后 `COMPLETED`、`completed_at` 非空、`next_fire_at` 为空。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k b03 && uv run pytest -q tests/agent_worker/test_scheduler_misfire.py"]`。**实测：本段 1 passed（37.16s）；引用段 6 passed（0.20s）。计数取自真实 Worker `GET /metrics`（Prometheus 文本）前后差值，不是进程内状态。**
- [x] 崩溃用真实进程终止取证：`SIGKILL` 后按 PG 真实盘面判定（租约行、Run 状态、会话释放），不用「进程退出码」或日志推断；等待窗口用绝对时刻比较，不做无界 `sleep` 轮询。**实测：判据全部是 PG 回读——`lease_owner`/`lease_until`/`heartbeat_at`/`status`/`attempt`/`finished_at` 逐行回读 + `task.task_event` 的 `CLAIMED`/`RECLAIMED` 事件载荷与 `create_time`；租约过期用 `lease_until < now()` 的绝对时刻比较，接手时刻与「被杀者最后 `heartbeat_at` + 租约」比较；所有等待都是 `_await_row` 的有界轮询（超时即带最后盘面失败）。进程只用于注入（`SIGKILL`）与就绪，不用于判定。**
- [x] 断言 CAS 语义：终态写入必须带预期状态条件（租约守卫式 CAS），构造「过期执行者晚到」的对照样本证明其不能覆盖新终态。**实测：对照样本用生产 `TaskClaimer` 单次领取两行（stale 2s 租约 / fresh 300s 租约，领先前断言全局可领行恰为本用例两行），终态写入走生产 `WorkerLoop.run_once`（注入式 claimer/executor，与执行结束后写终态同一条 CAS 守卫）：有效租约 → 写成功；租约过期但行仍 RUNNING 且仍归属过期执行者 → 行逐字段未变；接手者写下新终态后再次晚到写入 → 新终态不被覆盖。**
- [x] 真实边界未配置（无 PG/Redis/子进程能力）时必须显式记录原因，不得静默放过真实边界。**实测：`DATABASE_URL`/`REDIS_URL` 由基座 `require()` 缺失即 fail（不 skip）；换入 Worker 前用 `_await_port_free` 断言端口真的空出来——否则新进程绑定失败、旧进程仍应答 `/healthz`，会把「换入未生效」伪装成成功（E-05 整跑前实测到过一次该假象后加固）。**
- [x] 收尾清理：本租户 Task/Schedule/Run/审计残留为 0；运行后无残留 `uvicorn`/`muad_*.main`/worker 进程。**实测：每个用例 `finally` 停掉自起的服务（换入 Worker / IM Gateway / 渠道探针），模块收尾 `_stop_spawned_services` 再兜底，且每例末尾 `_await_no_extra_services` 断言只剩基座一对；进程判据最终定为「`ucomm` 是 Python 进程且命令行为 `-m uvicorn muad_*`」：旧口径「命令行里出现 uvicorn/muad_」会被调用方 shell 自身误伤（2026-09-29 整跑实测 5 例假失败——`/bin/zsh -c … grep -E "uvicorn|muad_" …` 被算成第三个服务进程），中间版本又因 macOS 把 `comm` 截断成 16 字符（`/Users/jahan/wor`）而漏掉真实服务；现场复核：调用方 shell 命令行提到 `-m uvicorn muad_` 时，识别结果仍只有运行中的真实服务。整跑后实测：孤儿服务进程 0；`dfx-reli%` 租户在 `task.task_execution/task_event/task_submission/task_schedule/delivery_route`、`control.*`（含 `config_audit_log`）、`runtime.run_record/conversation` 全为 0 行。**
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。**本任务三场景均无生产缺口，RED 行统一为「无 RED（新用例覆盖既有行为，如实登记）」，并以 4 处生产代码抖动取证证明断言非空（均在最终文件版本上复验，`git status --porcelain -- apps/ packages/` 为空），详见 Evidence。**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | lease → Reaper → CAS | 其他 Worker reclaim；`FAILED(RUN_ABANDONED)`；会话释放 | tests/acceptance/dfx/test_dfx_recovery.py + tests/acceptance/runtime/test_multipod_recovery.py + tests/acceptance/task_schedule/test_recovery.py / E-04 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e04 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py"]` | verified |
| E-05 | integration | Scheduler sweep → PG | 30s 内 CAS `FAILED(TASK_DEADLINE_EXCEEDED)`；仍按 `delivery_mode` 投递 | tests/acceptance/dfx/test_dfx_recovery.py + tests/agent_worker/test_task_deadline.py / E-05 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k e05 && uv run pytest -q tests/agent_worker/test_task_deadline.py"]` | verified |
| B-03 | integration | Scheduler → Schedule | 仅 SKIP 不补发并计数；ONCE `COMPLETED` 且 `next_fire_at` 空 | tests/acceptance/dfx/test_dfx_recovery.py + tests/agent_worker/test_scheduler_misfire.py / B-03 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_recovery.py -k b03 && uv run pytest -q tests/agent_worker/test_scheduler_misfire.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-04 | **无 RED（新用例覆盖既有行为，如实登记）**：三场景均无生产缺口，首跑即绿。为证明断言非空，做了 4 处**抖动取证**（改生产代码 → 对应断言失败 → 按字节还原 → 复绿，`git status --porcelain -- apps/ packages/` 为空）：① `worker/service.py::WorkerLoop._cas` 去掉 `TaskExecution.lease_until > moment`（租约守卫失效）→ 失败 `AssertionError: 租约守卫失效：过期执行者覆盖了盘面 {... 'status': 'RUNNING', 'lease_owner': 'dfx-stale-executor', 'lease_until': 已过期, 'result_json': None ...} → {... 'status': 'COMPLETED', 'result_json': {'probe': 'stale'} ...}`；② 同文件 `_requeue_expired` 的过期判定收窄为「过期 ≥ 1h」（被杀者的行不再被回收）→ 失败 `task … 未在 120.0s 内到达 COMPLETED`（最后盘面仍 RUNNING 且 `lease_owner` 为被杀进程）；③ `scheduler/service.py::DeadlineSweeper.sweep` 的 deadline 判定收窄为「超过 365 天」→ E-05 失败 `task … 未在 70.0s 内被 sweep CAS 成失败终态`（最后盘面 COMPLETED + `delivery_status=SENT`）；④ `scheduler/service.py::SchedulerLoop._process` 的 misfire 窗口收窄为 365 天 → B-03 失败 `schedule … 未在窗口内记下错过触发`（`last_fire_at` 被推进、无 skip 原因码）。四处扰动都**收窄**判定（不扩宽全局谓词），只影响本用例样本、不向共享库写垃圾行；还原后 `cmp` 与备份一致且逐项复跑复绿。 | `-k e04` → **1 passed in 44.24s**；复合 argv 两段全过：本段 1 passed + `tests/acceptance/runtime/test_multipod_recovery.py tests/acceptance/task_schedule/test_recovery.py` → **3 passed in 13.74s** | `tests/acceptance/dfx/test_dfx_recovery.py::test_e04_sigkill_worker_is_reclaimed_and_stale_write_is_refused`（`_sigkill_reclaim_arm`/`_assert_sigkill_reclaim`/`_stale_write_control_sample`/`_run_terminal_write`） | **真实 SIGKILL + 真实 PG 盘面**：基座 Worker 端口上换入显式短租约 Worker（`TASK_LEASE_SEC=6`/`TASK_HEARTBEAT_SEC=1`，真实 uvicorn 子进程）领走样本后被 `SIGKILL`；判定全部 PG 回读——首次 `CLAIMED` 事件载荷 `lease_owner` == 被杀进程的 `hostname:pid`；`CLAIMED` ≥ 2 且其后每次接手的 `lease_owner` 都不等于被杀者；首次接手事件的 `create_time` > 被杀者最后一次 `heartbeat_at` + 6s（绝对时刻，心跳随进程停止）；`attempt` ≥ 2；终态 `COMPLETED`、`result_json.probe == "killed"`、`lease_owner`/`lease_until` 为 NULL、`finished_at` 非空；`RECLAIMED` ≥ 1。**「过期执行者晚到写入」对照样本**：生产 `TaskClaimer.claim_one` 单次领取（领先前断言全局可领行恰为本用例两行——claim 无 tenant 谓词）、stale 行 2s 租约 / fresh 行 300s 租约、存活 Worker 被 20s blocker 占住；终态写走生产 `WorkerLoop.run_once`（注入式 claimer/executor）：有效租约 → COMPLETED `{"probe": "fresh"}`（正对照，证明该路径确实会写）；租约已过期但行仍 RUNNING 且仍归属过期执行者 → 行逐字段未变、`COMPLETED`/`CANCELLED` 事件为 0；存活 Worker reclaim 并接手写下新终态后再次晚到写入 → 行逐字段未变、结果仍为接手者的 `{"probe": "stale"}`。**Run 侧（`FAILED(RUN_ABANDONED)` 与会话释放）由引用的 `tests/acceptance/runtime/test_multipod_recovery.py::test_s04_e07_process_kill_reaped_and_takeover` 承接**（真实 Pod SIGKILL → Reaper → `RUN_ABANDONED` → 另一 Pod 在同一 conversation 建新 Run 并完成），本任务只登记其 argv、不复制断言（RULE-10）。**进程/残留**：用例末尾断言只剩基座 Console + Worker 一对（判据：`ucomm` 为 Python 进程且命令行为 `-m uvicorn muad_*`，见 Checklist 收尾清理项）；整跑后孤儿服务进程 0、`dfx-reli%` 租户在 task/control/runtime 相关表全为 0 行。 | verified |
| E-05 | **无 RED（新用例覆盖既有行为，如实登记）**：见 E-04 行的 4 处抖动取证，其中 ③（deadline 判定收窄）对应本场景；本场景无生产缺口。 | `-k e05` → **1 passed in 51.56s**；复合 argv 两段全过：本段 1 passed + `tests/agent_worker/test_task_deadline.py` → **6 passed in 0.31s** | `tests/acceptance/dfx/test_dfx_recovery.py::test_e05_deadline_sweep_cas_and_delivery_by_mode`（`_assert_deadline_swept`/`_assert_delivery_by_mode`/`_expire_deadline`/`_delivery_pair`） | **真实 Scheduler 节拍 → 真实 PG**：真实 HTTP 提交两行（`FINAL_ONLY` 带真实投递路由 / `NONE`），`deadline_at` 由真实列写入推到 `now()+2s` 并回读该时刻；生产 `task_deadline_sweep_interval_sec=30` / `scheduler_poll_interval_sec=10` **未改小**，两行被 CAS 成 `FAILED`、`error_code=TASK_DEADLINE_EXCEEDED`、`finished_at` 非空、`lease_owner`/`lease_until` 为 NULL、`DEADLINE_EXCEEDED` 事件 == 1，且 **DB 内 `finished_at - deadline_at` ∈ [0, 40s]**（sweep 间隔 30s + 10s 轮询拍点的可观测上界）。**终态仍按 delivery_mode 投递**：`FINAL_ONLY` 臂经真实 `muad_im_gateway.main` → 真实渠道探针（`tests.acceptance.task_schedule.channel_probe`，本地真实 HTTP，非替身；Worker 换入 `IM_GATEWAY_URL`），投递事实落库 `delivery_status=SENT`、`delivery_attempts ≥ 1`、`delivered_at` 非空、`DELIVERY_SENT` 事件 == 1、投递键 `task:{id}:final`，探针侧真实收到记录且正文为 deadline 文案（含「超过截止时间」）；`NONE` 臂同一 12s 安静窗口内 `delivery_status=NONE`、`delivery_attempts=0`、`delivered_at` 为 NULL、`DELIVERY_SENT/RETRY/FAILED` 事件全为 0（同窗口 A 臂确实投递成功 → 负例非空）。**显式边界（登记不掩盖）**：`FINAL_ONLY` 臂经真实 Gateway 会在真实 Redis 留下 `delivery:dedupe:task:{task_id}:final`（产品设计的 7 天 TTL，键含唯一 task id；整跑后实测该前缀下共 1039 个键，来自全仓各投递套件，非本用例新引入的一类残留）——本用例不删它（删产品投递键会掩盖投递事实），投递键生命周期与残留口径归 TASK-006。 | verified |
| B-03 | **无 RED（新用例覆盖既有行为，如实登记）**：见 E-04 行的 4 处抖动取证，其中 ④（misfire 窗口收窄）对应本场景；本场景无生产缺口。 | `-k b03` → **1 passed in 37.16s**；复合 argv 两段全过：本段 1 passed + `tests/agent_worker/test_scheduler_misfire.py` → **6 passed in 0.20s** | `tests/acceptance/dfx/test_dfx_recovery.py::test_b03_schedule_misfire_skip_and_once_terminal`（`_create_schedule`/`_miss_fire`/`_await_schedule`/`_schedule_tasks`/`_metric_value`） | **真实 Scheduler → 真实 PG / 真实 `/metrics`**：真实 HTTP `POST /internal/schedules`（Worker 内部接口，Runtime 同一入口）创建 CRON 与 ONCE，`next_fire_at` 由真实列写入推到一天前制造 misfire。**CRON 臂（只 SKIP 不补发）**：`task.task_schedule` 回读 `status=ACTIVE`、`last_error_code=SCHEDULE_MISFIRE_SKIPPED`、`last_error_message` 非空、`last_skipped_at` 非空、`next_fire_at > now()`、`last_fire_at` 为 NULL；`task.task_execution` 按 `schedule_id` 计数 = 0（无补发）；真实 Worker `GET /metrics` 的 `scheduled_misfire_total` 前后差 = +1（Prometheus 文本解析，不读进程内状态）。**ONCE 臂（成功终态）**：`run_at = now()-2s`（在 60s grace 内，非 misfire）→ `status=COMPLETED`、`completed_at` 非空、`next_fire_at` 为 NULL、`last_fire_at == run_at`、`last_error_code` 为 NULL；按 `schedule_id` **恰好 1 个** Task 且 `trigger_type=SCHEDULED`/`task_type=SKILL`；再等一个 Scheduler 拍点（10s+）仍为 1 个且 Schedule 行逐字段未变（不重复触发、不补发）。 | verified |
- E-04: verified — automated command passed; run_id=378093d797ed41e3a64a9d5ef7aeeaa0 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=378093d797ed41e3a64a9d5ef7aeeaa0 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=378093d797ed41e3a64a9d5ef7aeeaa0 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=bebfe15ea73a417683fc42964924b5bc (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=bebfe15ea73a417683fc42964924b5bc (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=bebfe15ea73a417683fc42964924b5bc (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-29] started
- [2026-09-29] completed (done)
## TASK-006: 最终投递去重与重试（Worker→Gateway→Redis）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.4 可靠性矩阵, 14-dfx-acceptance.backend.design.md#3.4.3 契约与一致性测试清单, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-11, E-06
- **Files**: `tests/acceptance/dfx/test_dfx_delivery.py`、`tests/acceptance/dfx/delivery_rewriter.py`（新增：Worker↔真实 Gateway 之间的**真实 HTTP 响应改写代理**，只在回程延迟/改写 `delivered`，请求仍打到真实 Gateway/Redis/探针）, `tests/acceptance/dfx/environment.py`（把 TASK-005 落在 `test_dfx_recovery.py` 里的**投递链/换入 Worker/孤儿进程判据**原语上移到基座公用，供本任务复用；新增 `instance_id()`——不另造第二套进程管理）, `tests/acceptance/dfx/test_dfx_recovery.py`（改为引用上移后的原语，行为一字未改；回归 3 passed）
- **Estimate**: 半天级

### Description

FEAT-04 的最终投递面：终态投递以 `delivery_key`（`task:{task_id}:final`，Child 为 `:{child_id}:final`）去重、重复投递返回 200 且 `delivery_status` 最终 `SENT`、结果**先持久化后投递**；前 4 次失败第 5 次成功走指数退避、仅 HTTP 2xx 且 `delivered=true` 才置 `SENT`、Gateway 占位（`delivered=false`）按可重试失败退避重投、超限置 `FAILED` 并写审计。

### Checklist

- [x] [S-11][integration] 以 `Worker → Gateway /internal/deliveries → Redis` 为真实边界编写用例：同一 `delivery_key` 投递两次 → 第二次返回 200 且不重复发送、`delivery_status` 最终 `SENT`；并断言结果**先持久化后投递**（投递发生前库内已有终态事实）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k s11 && uv run pytest -q tests/acceptance/task_schedule/test_delivery.py"]`。**实测：本段 1 passed（13.79s）；引用段 2 passed（24.38s）。「先持久化后投递」有两条真实库证据：①投递正文由库内已持久化结果构造——探针实收 `后台任务已完成：dfx_reliability_probe\n{"probe":"s11-persist","slept_sec":0}`，与 `result_json` 逐字段一致；②同库两列比较 `finished_at(06:19:18.793702) ≤ delivered_at(06:19:19.652829)`。重放走真实 Gateway：`{duplicate: True, delivered: True, deduplicated: True}`，探针 8s 安静窗口记录数不变，真实 Redis 上 `delivery:dedupe:task:{id}:final` TTL = 604792s（≈7d）。**
- [x] [E-06][integration] 以 `Worker → Gateway → Redis` 为真实边界断言：前 4 次失败后第 5 次成功的指数退避序列（`BACKOFF_BASE_SEC * 2**attempts`）；重复 `delivery_key` 不重复发送；**HTTP 200 但 `delivered=false` 不得置 `SENT`**，须按可重试失败退避重投；超过 5 次置 `delivery_status=FAILED` 并写审计。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k e06 && uv run pytest -q tests/acceptance/im_gateway/test_worker_delivery.py"]`。**实测：本段 3 passed（265.57s）；引用段 4 passed（118.91s）。退避臂实测间隔 `[10.08, 20.14, 40.25, 80.40]s` 对窗口 `[10, 20, 40, 80]s`（取自真实 `task_event.create_time` 差值），第 5 次成功且探针**恰好收到 1 条**成功投递（前 4 次被渠道 500 拒绝、不记录）。占位臂由真实 HTTP 响应改写代理注入 2xx+`delivered=false`：该行 `delivered_at` 始终 NULL、`DELIVERY_SENT` 事件 0，按可重试失败耗尽后置 `FAILED`（Task 自身仍 `COMPLETED`），审计事件 `DELIVERY_FAILED` 载荷 `{"error": "delivery accepted as in-flight placeholder, not delivered yet", "terminal": true, "delivery_attempts": 5}`。**
- [x] 退避断言用可注入时钟或真实间隔两者之一，并显式登记所选口径；`delivery_attempts` 必须在发起请求前自增并提交（崩溃重启不丢退避进度），以真实库回读取证。**口径登记：用**真实间隔**（不注入时钟）——真实 Worker 子进程按生产 `DeliveryLoop` 的窗口重试，窗口从真实事件时间测量；为避免默认 `delivery_poll_interval_sec=5` 把窗口放大到 5s 轮询粒度上，本模块把该**真实配置项**钉到 1s（生产环境变量，非替身）。「先自增后发送」以真实库回读取证：响应改写代理把回程延迟 3s，在探针**已收到请求**而 Worker 仍被挡住的窗口内回读 PG，得到 `delivery_status=PENDING`、`delivery_attempts=5`（把种入的 4 自增到 5）——即预留已提交才有请求。**
- [x] `delivery_mode` 仅 `FINAL_ONLY`/`NONE`：对 `NONE` 构造对照样本，断言不产生投递记录。**实测：同一投递链同一窗口内，`FINAL_ONLY` 正对照样本被真实探针收到（1 条、最终 `SENT`），而 `NONE` 样本回读为 `delivery_mode=NONE`、`delivery_status=NONE`、`delivery_attempts=0`、`delivered_at` 为 NULL，`DELIVERY_SENT/RETRY/FAILED` 事件全为 0，按该样本路由归因的探针记录为空——负例非空转。**
- [x] 断言失败面不吞事实：超限后审计行存在且载荷含原因与重试次数，反查无明文密钥。**实测：耗尽时写 `task.task_event` 的 `DELIVERY_FAILED`（该租户该 Task 的投递事件恰好 1 条），载荷含 `error`（原因）、`delivery_attempts=5`（重试次数）、`terminal=true`；载荷序列化串内不含 `INTERNAL_SERVICE_TOKEN` 与租户模型 `api_key` 明文（逐值反查）。Task 自身终态事实未被投递失败吞掉（`status=COMPLETED`）。**
- [x] 真实 Redis 未配置时显式记录原因，不得用替身冒充「真实 SET NX EX/TTL 去重」。**实测：`REDIS_URL` 缺失时基座 `require()` 直接 fail（不 skip），Gateway 侧 `settings.require_redis_url()` 亦直接抛错——不会退化成替身；去重语义本身以真实 Redis 键值与 TTL 取证（见 S-11 行）。**
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。**本任务两场景均无生产缺口，RED 行统一为「无 RED（新用例覆盖既有行为，如实登记）」，并以 4 处生产代码抖动取证证明断言非空（均在最终文件版本上复验，`git status --porcelain -- apps/ packages/` 为空），详见 Evidence。另新增两处环境前提守卫（见 Evidence）。**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-11 | integration | Worker → Gateway `/internal/deliveries` → Redis | 重复 `delivery_key` 返回 200；最终 `SENT`；先持久化后投递 | tests/acceptance/dfx/test_dfx_delivery.py + tests/acceptance/task_schedule/test_delivery.py / S-11 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k s11 && uv run pytest -q tests/acceptance/task_schedule/test_delivery.py"]` | verified |
| E-06 | integration | Worker → Gateway → Redis | 退避 5 次；去重不重发；`delivered=false` 不置 SENT；超限 FAILED + 审计 | tests/acceptance/dfx/test_dfx_delivery.py + tests/acceptance/im_gateway/test_worker_delivery.py / E-06 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_delivery.py -k e06 && uv run pytest -q tests/acceptance/im_gateway/test_worker_delivery.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-11 | **无 RED（新用例覆盖既有行为，如实登记）**：本场景无生产缺口，首跑即绿。为证明断言非空，做了 2 处**抖动取证**（改生产代码 → 对应断言失败 → 按字节还原 → 复绿，`git status --porcelain -- apps/` 为空）：① gateway `api/delivery.py::deliver` 的 `reserved = await dedupe.reserve(...)` 改为 `reserved = True`（去重占位失效）→ 失败 `AssertionError: {'accepted': True, 'deduplicated': False, 'delivered': True, 'duplicate': False}`（重放被当成首发、未去重）；② `delivery/messages.py::_completed_text` 去掉 `_result_summary` 摘要行 → 失败 `AssertionError: 投递正文不是由库内已持久化的结果构造：后台任务已完成：dfx_reliability_probe`（正文不再承载库内结果）。 | `-k s11` → **1 passed in 13.79s**；复合 argv 两段全过：本段 1 passed + `tests/acceptance/task_schedule/test_delivery.py` → **2 passed in 24.38s**（整文件单跑 4 passed in 277.73s） | `tests/acceptance/dfx/test_dfx_delivery.py::test_s11_final_delivery_dedupes_replay_and_persists_before_send`（`_worker_chain`/`_await_probe`/`_assert_quiet`/`_dedupe_ttl`/`_replay`/`_await_worker_claim`） | **真实 Worker 子进程 → 真实 IM Gateway → 真实 Redis → 真实渠道探针**（本地真实 HTTP 进程，非替身）。①先持久化后投递：投递正文 = `后台任务已完成：dfx_reliability_probe\n{"probe":"s11-persist","slept_sec":0}`（由库内 `result_json` 构造），且 `finished_at 06:19:18.793702 ≤ delivered_at 06:19:19.652829`（同库两列比较）；投递到达瞬间回读 PG 为 `status=COMPLETED`、`result_json` 非空。②终态 `delivery_status=SENT`、`delivery_attempts=1`、`DELIVERY_SENT` 事件 == 1、`delivery_key=task:{id}:final`。③重放（真实 Gateway HTTP）→ 200 `{duplicate: True, delivered: True, deduplicated: True}`；探针 8s 安静窗口记录数不变；真实 Redis `delivery:dedupe:task:{id}:final` TTL = 604792s（≈7d，产品设计的成功键）。 | verified |
| E-06 | **无 RED（新用例覆盖既有行为，如实登记）**：本场景无生产缺口，首跑即绿。4 处**抖动取证**（均在最终文件版本上复验）：① `delivery/service.py` 的 `BACKOFF_BASE_SEC * sa.func.power(2, delivery_attempts)` 改为常量 `BACKOFF_BASE_SEC`（去掉指数）→ 失败 `AssertionError: 第 1 次失败后的退避间隔 5.047744s 不在 [10.0, 15.0]`；② 同文件 `if 200 <= status_code < 300 and result.delivered:` 去掉 `and result.delivered` → 失败 `未在 90.0s 内占位响应按可重试失败处理并在耗尽后置 FAILED`，盘面 `delivery_status='SENT'`、`delivered_at` 非空（占位被当送达）；③ `messages.py::_completed_text` 去掉摘要（同 S-11 行）；④ gateway 去重占位失效（同 S-11 行）。**边界登记**：退避窗口以生产常量 `BACKOFF_BASE_SEC` 为基准断言「形状」（公式口径）——把 5 改成 1 时两侧同步缩放、用例仍通过（实测确认），即**调参不改公式不视为违规**；本用例钉住的是「指数序列 + 与生产公式一致」，不钉常量取值。 | `-k e06` → **3 passed in 265.57s**；复合 argv 两段全过：本段 3 passed + `tests/acceptance/im_gateway/test_worker_delivery.py` → **4 passed in 118.91s** | 退避：`test_e06_backoff_sequence_then_success`（`_delivery_events` 取真实 `create_time` 差值）；占位/耗尽/去重：`test_e06_placeholder_200_is_never_sent_and_exhaustion_writes_audit`（`_seed_delivery_attempts`/`_await_worker_claim`/`_replay`）；`NONE` 对照：`test_e06_none_mode_produces_no_delivery_fact` | **真实退避**：探针注入 4 次 500 后放行，实测间隔 `[10.082654, 20.135896, 40.248275, 80.397248]s` 对窗口 `[10, 20, 40, 80]s`；事件序列 `[DELIVERY_RETRY]×4 + [DELIVERY_SENT]`；第 5 次成功触达**恰好 1 条**（同一 `delivery_key` 不重复成功发送）。**先自增后发送**：响应改写代理把回程延迟 3s，在探针已收件、Worker 仍等待的窗口内回读 PG = `delivery_status=PENDING`、`delivery_attempts=5`。**2xx+`delivered=false` 不置 SENT**：注入后该行 `delivered_at` 始终 NULL、`DELIVERY_SENT`==0、终态 `FAILED`（Task 自身 `COMPLETED`），审计 `DELIVERY_FAILED` 载荷 `{"error": "delivery accepted as in-flight placeholder, not delivered yet", "terminal": true, "delivery_attempts": 5}` 且无明文密钥；重放同一 `delivery_key` 直连真实 Gateway → `{duplicate: True, deduplicated: True}`、探针记录数不变。**`NONE` 对照**：正对照样本同窗口成功投递 1 条（证明链路在跑），`NONE` 样本 `delivery_status=NONE`/`attempts=0`/`delivered_at` NULL/三类投递事件皆 0/探针无记录。**环境前提（本轮实测，复用时留意）**：claim 无 tenant 谓词，共享库上任何别的 Worker 都会领走本租户的行并用**它自己的** `ARTIFACT_ROOT` 执行（表现为 `SKILL_ARTIFACT_UNAVAILABLE` 重试到 FAILED）。实测命中两次：一次是本地 dev 服务 `uvicorn muad_agent_worker.main:app --app-dir apps/agent-worker/src --reload --port 8002` 的 **reload 子进程**（命令行形如 `python -c from multiprocessing.spawn import spawn_main`，**不含** `-m uvicorn muad_`，逃过了既有孤儿进程判据），一次是刻意起的对照 Worker（`-m uvicorn muad_agent_worker.main`，被 `await_no_extra_services()` 判据抓到）。因此新增两处守卫：`_await_worker_claim` 用**真实库回读的 CLAIMED `lease_owner`** 钉死「本栈 Worker 领走这一行」（外来 owner 时 ~4s 内失败并指名 owner 与典型成因），`await_no_extra_services()` 覆盖 `-m uvicorn muad_*` 形态的额外服务进程；两处都在本轮以真实外来 Worker 验证过（守卫触发实测：`owner=['…:23655'] ≠ 本栈 …:24235`，3.74s 失败；另两轮由收尾判据抓到额外服务进程）。**收尾**：整跑后无残留服务进程（`ps` 无 `uvicorn muad_*`），`dfx-reli%` 租户在 task/control 相关表 0 行；`delivery:dedupe:task:{id}:final` 属产品设计（7d TTL），不删（删键会掩盖投递事实）。 | verified |

> E-06 独立覆盖三例：`test_e06_backoff_sequence_then_success`、`test_e06_placeholder_200_is_never_sent_and_exhaustion_writes_audit`、`test_e06_none_mode_produces_no_delivery_fact`。
> 本轮之前的一次 runner 记录（`failed` / `incomplete`）来自**污染环境**：当时本地 dev Worker 与验收栈争抢同一 PG 上的 Task，该失败已定位并登记为上表「环境前提」。
- S-11: verified — automated command passed; run_id=d2d5ad0694384734ade661e0fd8cf788 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=d2d5ad0694384734ade661e0fd8cf788 (confirmed_by: runner)
- S-11: failed — automated command failed; run_id=d4a61b870b844583b4c3b97326bd55cf (confirmed_by: runner)
- E-06: failed — automated command failed; run_id=d4a61b870b844583b4c3b97326bd55cf (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=ff33447f6f1b41b489cb50c7b2040e6b (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=ff33447f6f1b41b489cb50c7b2040e6b (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=dd3775deb539431782b9bcac87c6c1ea (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=dd3775deb539431782b9bcac87c6c1ea (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)

---
- [2026-09-29] started
- [2026-09-29] resumed (in-progress)
- [2026-09-29] completed (done)
## TASK-007: 安全验收：Egress 拒绝、Secret 全链路与日志脱敏

- **Status**: verified
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

- [x] [E-07][integration] 先写用例后改实现（若发现缺口）：以 `Egress Boundary → Audit/日志/Snapshot` 为真实边界编写用例：未命中 allowlist 的 `ctx.http` 调用**不发出**且写 DENY 审计；载荷含 canary 密钥时反查 `runtime_snapshot`/`egress_audit`/`tool_call_audit`/`model_invocation_audit` 四表、IM 出站文本与 bot 快照两侧**均无明文**；`>5 MiB` 响应被拒绝并审计。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_security.py -k e07 && uv run pytest -q tests/acceptance/test_secret_consumers.py tests/acceptance/im_gateway/test_secrets_and_readiness.py"]`。
- [x] [RULE-secret-001][integration] 作为唯一最终负责人：密钥明文只存各 Owner 表（模型 `api_key`、Bot `secret`、MCP `auth_secret`、凭据 `credential_json`；平台侧无自有密钥列、`project_platform.auth_secret` 为休眠列不得读写）、跨表以主键引用（无 `secret_ref`/SecretProvider）、明文出口仅三处受控例外（bot 快照、`resolve-credentials`、`resolve-definition`）且统一 `require_service_identity` 门控。verifier argv：`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`。
- [x] [RULE-log-001][integration] 作为唯一最终负责人：所有服务统一 logging-kit、仅配置 `LOG_DIR`（另读 `LOG_LEVEL`）、JSON 输出、自动携带 `trace_id/request_id/tenant_id`、敏感字段脱敏；并断言**两侧语义不同**——审计侧键在 before/after 中整个消失，日志侧值置换为 `***`。verifier argv：`["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]`。
- [x] 断言三处 internal 端点（`resolve-definition`/`resolve-credentials`/`resolve-egress-access`）的**匿名与越权访问必须 403**，且调用方（Worker/Runtime）带 `X-Internal-Service` 才可见明文——受控出口的两侧都要断言。
- [x] 显式边界（不修，只登记）：canary 机检**不含** `runtime.canonical_event`、`control.config_audit_log`、Skill package 与 `SKILL.md`；「不得进入」是约定而非全覆盖，新增落库面必须自查，不得以「canary 全绿」代替覆盖结论。
- [x] 断言「不返回假成功」：Egress 拒绝与 5 MiB 拒绝都必须显式失败（错误码/审计），不得静默降级为成功。
- [x] 收尾清理：canary 值一次性使用，运行后库内/日志目录/产物目录无该明文与残留对象；运行后无孤儿进程。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-07 | integration | Egress Boundary → Audit/日志/Snapshot | DENY 审计且调用不发出；canary 四表 + IM 出站 + bot 快照无明文；>5 MiB 拒绝 | tests/acceptance/dfx/test_dfx_security.py + tests/acceptance/test_secret_consumers.py + tests/acceptance/im_gateway/test_secrets_and_readiness.py / E-07 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_security.py -k e07 && uv run pytest -q tests/acceptance/test_secret_consumers.py tests/acceptance/im_gateway/test_secrets_and_readiness.py"]` | verified |
| RULE-secret-001 | integration | 密钥明文只存 Owner 表/三处受控出口 + 原 verifier 真实边界 | 无 `secret_ref`；休眠列不读写；受控出口门控；原 verifier 全部通过 | 原 verifier / RULE-secret-001 | `["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]` | verified |
| RULE-log-001 | integration | logging-kit 唯一出口与双通道脱敏 + 原 verifier 真实边界 | JSON 结构/关联字段/脱敏；审计丢键 vs 日志 `***`；原 verifier 全部通过 | 原 verifier / RULE-log-001 | `["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-07 | **真 RED（本需求少见的真实生产缺口）**：`test_e07_egress_response_over_five_mib_is_rejected_and_audited` **首跑失败**——`AssertionError: []`，盘面**只有 ALLOW 行、没有 DENY**。缺口：`packages/platform-sdk/src/muad_platform_sdk/egress_boundary.py` 的 `http_get`/`http_post` 在 `len(response.content) > policy.max_bytes` 时**只 `raise ResponseTooLargeError`、不落审计**，违反 design §3.4.5「`ctx.http` 响应 > 5 MiB 拒绝**并审计**」。修复（+16/−2）：`_audit` 增可选 `error_code`，新增 `_audit_too_large`（`decision="DENY"`、`error_code="RESPONSE_TOO_LARGE"`）并在两处超限抛错前调用；`audit_writer is None` 时既有行为不变。**独立复验**：把该调用撤掉 ⇒ 用例**复现变红**（`AssertionError: []`），按字节还原 ⇒ 复绿；回归 `tests/agent_runtime/test_egress_boundary.py` **6 passed**。另做 2 处扰动：去掉 `_check_audited` 的 DENY 分支 ⇒ 用例①红（`assert 0 == 1`，盘面只剩 ALLOW）；把 `mcp_server.auth_secret` 拼进 MCP 审计 target ⇒ 用例④红（`runtime.egress_audit: 1`，证明 canary 确从 Owner 表读出并走过 Egress 链路）。 | 独立复跑：`-k e07` → **6 passed in 9.48s**；配对 `tests/acceptance/test_secret_consumers.py` + `tests/acceptance/im_gateway/test_secrets_and_readiness.py` → **8 passed in 45.42s**；runner 判 **verified**（functional / exit 0）。 | `test_dfx_security.py::test_e07_egress_denied_host_is_not_sent_and_deny_is_audited` / `::test_e07_egress_response_over_five_mib_is_rejected_and_audited` / `::test_e07_internal_endpoints_gate_plaintext_behind_service_identity` / `::test_e07_canary_never_reaches_run_audit_tables` / `::test_e07_bot_snapshot_is_gated_and_im_outbound_carries_no_plaintext` / `::test_e07_audit_drops_keys_while_logs_replace_values` | **真实子进程栈**（Console/Runtime/Worker×2/IM Gateway/渠道探针/LLM 探针）+ 真实 PG/Redis + **真实本地 HTTP 探针**（外部端点由本地真实端点承载，非替身）。逐条实测：① 拒绝臂（allowlist 不含 host）抛 `ForbiddenEgressError` 且探针**零请求**，**放行臂用同一 URL** 证明可达（计数 0→1，即「零请求」非空断言），PG 回读**恰好 1 条** DENY（`target_type=HTTP`、`status_code IS NULL`）+ 1 条 ALLOW（200）；② 探针返回真实 **>5 MiB** 字节流，先证明探针被命中（失败非因连不上），再断言显式 `ResponseTooLargeError` + DENY（`error_code=RESPONSE_TOO_LARGE`）；③ 三处 internal 端点（`resolve-definition`/`resolve-credentials`/`resolve-egress-access`）逐条**三态**：匿名 **403 `FORBIDDEN` 且响应无明文** / 伪造 `X-Internal-Service` 同样 403 无明文 / **正确服务身份 200 且明文必现**（canary 分别以模型 `api_key` 与共享凭据 `credential_json` 为载体）；④ canary 同时写入 Owner 两表，经**真实 Run 调用真实 MCP 工具**（canary 作为 `Authorization` 真实出网）⇒ 四张审计表（`runtime_snapshot`/`egress_audit`/`tool_call_audit`/`model_invocation_audit`）按 `run_id` **各 ≥1 行**（非空）且按 `tenant_id` 的 `to_jsonb(row)::text ILIKE` 反查**零命中**，**阳性对照**：同一反查口径在两张 Owner 表上**必须命中**（证明扫描器本身有效）；⑤ bot 快照两侧（受信可见 canary / 匿名 403 无 canary）+ 真实 Task `FINAL_ONLY` 经 Worker→Gateway→渠道探针的**真实 IM 出站文本非空且无 canary**，Gateway 进程日志无 canary；⑥ **两类脱敏语义并置对照**：审计侧**丢键**（`write_config_audit` 落库后 `after_json == {"name":"ok"}`，键整个消失）vs 日志侧**值置换 `***`**（真实 logging-kit JSON 日志里 `api_key=***`，键仍在、明文不在）。**收尾**：canary 每次运行随机生成（一次性）；finalizer 停栈 → 清本租户自建行 → 清产物根 → 复核 **13 张表零 canary 明文** + 服务日志目录非空且零 canary；运行后**无孤儿进程**。 | verified |
| RULE-secret-001 | **无 RED（规则既有实现已满足；本任务在本行只做"唯一最终负责人"的覆盖与三处端点门控断言）**。 | verifier 独立复跑：`tests/test_logging_redaction.py` + `tests/acceptance/test_foundation_ops_audit.py` → **12 passed in 0.24s**。 | 原 verifier + `test_dfx_security.py::test_e07_internal_endpoints_gate_plaintext_behind_service_identity` / `::test_e07_canary_never_reaches_run_audit_tables` / `::test_e07_bot_snapshot_is_gated_and_im_outbound_carries_no_plaintext` / `::test_e07_audit_drops_keys_while_logs_replace_values` | 真实 PG Owner 表 + 真实 internal HTTP + 真实 Run/MCP 出网。密钥明文只存各 Owner 表（本任务以模型 `api_key` 与 `mcp_server.auth_secret` 两个载体实测读取并被 Egress/MCP 真实使用）；跨表以主键引用（无 `secret_ref`/SecretProvider）；**三处受控明文出口**（bot 快照 / `resolve-credentials` / `resolve-definition`）统一 `require_service_identity` 门控，且**两侧都断言**（匿名与伪造头 403 且无明文；正确服务身份 200 才现明文）；平台侧 `project_platform.auth_secret` 休眠列**未被本任务读写**。**覆盖面边界见 E-07 行**（canary 机检恰为四表 + IM 出站 + bot 快照，不含 `canonical_event`/`config_audit_log`/Skill package/`SKILL.md`）。 | verified（人工终态化；RULE 行不由 runner 回写） |
| RULE-log-001 | **无 RED（规则既有实现已满足）**。 | verifier 独立复跑：`tests/test_logging.py` + `tests/test_logging_redaction.py` + `tests/acceptance/test_foundation_logging.py` → **11 passed in 0.06s**。 | 原 verifier + `test_dfx_security.py::test_e07_audit_drops_keys_while_logs_replace_values` | 真实 logging-kit 子进程写 JSON 日志 ⇒ `api_key=***`（**值置换**：键仍在、明文不在），与审计侧**丢键**（键整个消失）**并置对照**、未混用两种语义；统一 logging-kit 入口、仅 `LOG_DIR`（另读 `LOG_LEVEL`）、JSON 保留键与 trace 关联字段口径由既有 verifier 覆盖。 | verified（人工终态化） |

> 本任务**如实登记的边界**（写进 `test_dfx_security.py` docstring，**不得**读作已覆盖）：① canary 机检范围**恰为**四张审计表 + IM 出站文本 + bot 快照两侧，**不含** `runtime.canonical_event`、`control.config_audit_log`、Skill package 与 `SKILL.md`；「不得进入」是约定而非全覆盖，新增落库面必须自查，**不得以「canary 全绿」代替覆盖结论**。② **`EgressBoundary` 尚未被 Run 内的 `SkillContext.http` 接线**（全仓无 `SkillContext` 构造点；Run 内唯一真实出网面是 MCP，`target_type=MCP`，已由 S-10 覆盖）⇒ 本文件**直接驱动生产类 `EgressBoundary` + 真实探针 + 真实 PG 审计**，未伪造 Run 内 `ctx.http`；是否接线属功能实现范畴，超出本验收任务。③ NFS 慢故障等边界不属本任务。
- E-07: verified — automated command passed; run_id=903ac46342b340cd897e186319eeaf02 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=0dedce87a21b4ac6928a0c50cfd68347 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)
- [2026-09-30] E-07 / RULE-secret-001 / RULE-log-001 终态：新增 `tests/acceptance/dfx/test_dfx_security.py`（6 例，真实子进程栈 + 真实本地 HTTP 探针）：Egress 拒绝（探针**零请求** + DENY 审计，**同一 URL 的放行臂**证明"零请求"非空断言）、**>5 MiB 显式失败并审计**、三处 internal 端点**三态门控**（匿名/伪造头 403 且无明文 vs 正确服务身份 200 明文必现）、canary 四表按 run_id 反查零命中（**含两张 Owner 表的阳性对照**证明扫描器有效）、bot 快照两侧 + 真实 Task 经 Worker→Gateway→渠道探针的 **IM 出站文本**无明文、**审计侧丢键 vs 日志侧 `***` 值置换并置对照**。三段 argv 独立复跑全绿（`-k e07` 6 passed + 配对 8 passed；RULE-log-001 11 passed；RULE-secret-001 12 passed）。
  **真 RED（本需求少见的真实生产缺口）**：`egress_boundary.py` 的超限分支**只抛 `ResponseTooLargeError`、不落 DENY 审计**（首跑 `AssertionError: []`，盘面只剩 ALLOW 行）⇒ 修复 +16/−2（`_audit` 增可选 `error_code`；新增 `_audit_too_large` 并在两处超限抛错前调用）；**独立复验**：撤掉该调用 ⇒ 复现变红，按字节还原 ⇒ 复绿；`tests/agent_runtime/test_egress_boundary.py` 6 passed 无回归。
  该生产改动落在 `packages/platform-sdk/**` ⇒ 触发 `harness-project-platform` 绑定与 scope pause ⇒ 已按其 verifier（`tests -k schema_parity` **35 passed**）+"不涉及实例/寻址配置、凭据模式、PlatformSession 缓存与 adapter_key 语义"的承接说明执行 `bind` + `resume`（scope 复核 `continue`）。
  边界如实登记：canary 机检**不含** `runtime.canonical_event`/`control.config_audit_log`/Skill package/`SKILL.md`；**`EgressBoundary` 尚未被 Run 内 `SkillContext.http` 接线**（全仓无 `SkillContext` 构造点，Run 内唯一真实出网面是 MCP）⇒ 本文件直接驱动**生产类** `EgressBoundary` + 真实探针 + 真实 PG 审计，未伪造 Run 内 `ctx.http`。

---
- [2026-09-30] started
- [2026-09-30] resumed (in-progress)
- [2026-09-30] completed (done)
## TASK-008: API 安全与租户隔离（RBAC/CSRF/ADMIN 门控/租户谓词）

- **Status**: verified
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

- [x] [E-08][integration] 以 `API → RBAC/CSRF/租户谓词` 为真实边界编写用例：非安全方法缺失与伪造 `X-CSRF-Token` → 403 且审计计数不变；BUILDER 访问 ADMIN 端点（含 `credentials`）→ 403；跨租户读取/写入不可见且不泄露存在性；**带伪造 `X-Tenant-Id` 时列表仍只含本账号租户数据**。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_api_security.py -k e08 && uv run pytest -q tests/console_auth/test_rbac.py tests/console_platform/test_credentials_api.py tests/agent_worker/test_tenant_guard.py"]`。
- [x] [RULE-auth-001][integration] 作为唯一最终负责人：三层授权关系与 Effective Capability（含 `is_deleted=false` 与资源/Agent `enabled`）、未授权资源不进 Prompt/ToolRegistry/Catalog；并覆盖 Console 侧的凭据路由 ADMIN + 账号派生租户、CSRF/会话/密码锁定/审计同事务口径。verifier argv：`["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`。
- [x] 断言「ADMIN 门控是前端隐藏 + 后端 403 兜底双层」时只断言**本模块可断言的后端兜底层**；前端隐藏归各前端 owner，如发现缺失只登记缺口不改跨模块行为。
- [x] 断言 CSRF 与会话 Cookie 属性：`muad_session` 为 httponly、CSRF Cookie 可读、两者 `samesite=strict`、`secure` 仅非 dev 打开。
- [x] 断言密码与会话口径不被削弱的边界：`argon2id`（`$argon2id$` 前缀）+ 最短 12 字符、连续 5 次失败锁 15 分钟且锁定时清零计数、未知用户与禁用账号走 dummy hash 等化时序统一回 `INVALID_CREDENTIALS`。
- [x] 收尾清理：跨租户对照样本自建自清（`finally` 删除），不污染共享开发库；运行后无孤儿进程。
- [x] 显式边界（不修，只登记）：租户隔离已于 2026-09-28 全量收敛为「账号派生」，`X-Tenant-Id` 仅对内部服务与公开登录生效（内部路由另有服务身份门控）；本任务只断言该口径，不改任何路由的租户来源。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-08 | integration | API → RBAC/CSRF/租户谓词 | 403 `FORBIDDEN` 且不落变更；跨租户不可见/不可写不泄露存在性；伪造租户头无效 | tests/acceptance/dfx/test_dfx_api_security.py + tests/console_auth/test_rbac.py + tests/console_platform/test_credentials_api.py + tests/agent_worker/test_tenant_guard.py / E-08 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_api_security.py -k e08 && uv run pytest -q tests/console_auth/test_rbac.py tests/console_platform/test_credentials_api.py tests/agent_worker/test_tenant_guard.py"]` | verified |
| RULE-auth-001 | integration | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | 三层授权与 Effective Capability；未授权不进 Prompt/Catalog；凭据路由 ADMIN + 账号派生租户；原 verifier 全部通过 | 原 verifier / RULE-auth-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-08 | **无 RED（验收任务，生产无缺口，如实登记）**：首跑即绿（`7 passed`）。为证明断言非空，做了 **7 处扰动取证**（改生产代码 → 对应断言失败 → 按字节还原 → `git status --porcelain -- apps/` 为空）：① `api/security.py::require_csrf` 的 `raise AppError(FORBIDDEN)` 改 `return` → CSRF 403 断言失败；② `api/router.py` 把 `credentials_router` 从 `admin` 组挂回 `authenticated` → BUILDER 凭据路由 403 断言失败；③ `api/accounts.py` 的 `TenantId = AccountTenantId` 改回 `HeaderTenantId` → 「伪造头不切租户」断言失败；④ `agent_repository.get()` 去掉 `tenant_id` 谓词 → 跨租户不可见/不可写断言失败；⑤ `auth_service._register_failure` 去掉 `failed_attempts = 0` → 「锁定时清零计数」断言失败；⑥ `hash_session_token` 改回明文 → 「会话按 sha256 摘要可查」断言失败；⑦ `api/auth.py` 登录不再写审计 → 「登录审计恰好 +1」断言失败。其中 ④ 首轮替换断言拦下（该行在文件内 5 处，未误改），第二次按 `get()` 方法块精确定位后重做。 | `-k e08` → **7 passed in 3.66s**；复合 argv 两段全过：本段 7 passed + `tests/console_auth/test_rbac.py`/`tests/console_platform/test_credentials_api.py`/`tests/agent_worker/test_tenant_guard.py` → **22 passed in 2.22s** | `test_dfx_api_security.py::test_e08_csrf_missing_or_forged_is_forbidden_without_audit` / `test_e08_builder_is_denied_admin_endpoints_and_admin_is_allowed` / `test_e08_cross_tenant_resource_is_invisible_and_not_writable` / `test_e08_forged_tenant_header_does_not_switch_tenant` / `test_e08_session_and_csrf_cookie_attributes` / `test_e08_password_policy_and_failed_login_lockout` / `test_e08_session_row_is_hashed_and_login_audit_shares_transaction` | **真实 Console uvicorn 子进程**（DFX 基座 `start_dfx_stack`，真实 HTTP + 真实 Cookie jar；非 ASGI 传输替身）+ **真实 PostgreSQL 逐行回读**（账号/会话/审计/资源）。①CSRF：缺失与伪造头均 403 `FORBIDDEN`，`config_audit_log` 的 `CREATE`/`CONSOLE_ACCOUNT` 计数不变且无账号行；正对照带正确头 → 200 且计数 +1。②门控：BUILDER 对 `accounts`/`users`/`user-credentials`/`shared-credential` 四条路径与写面 `POST /accounts`（带正确 CSRF）全 403；ADMIN 正对照 200/200，凭据路由 404（非 403，证明拒绝来自角色而非路由不存在）。③跨租户：`CROSS_TENANT` 的 Agent 不在列表；`GET /agents/{cross}` 与随机不存在 id **同为 404 且 error code 相同**（不泄露存在性）；`PUT`/`DELETE` 跨租户 → 404 且真实库回读 `name` 未变、`is_deleted=false`；跨租户账号不在 accounts 列表。④伪造头：`X-Tenant-Id` 换成随机租户后列表 200 且仍含本账号租户数据、不含他租户。⑤Cookie：`muad_session` 带 HttpOnly、`muad_csrf` 不带、两者 `SameSite=strict`、`Secure` 与 `SharedSettings().env != "dev"` 一致（本机 `ENV=dev` ⇒ 无 Secure，用例按环境判定而非写死）。⑥密码：11 字符 → 422 且无账号行；库内哈希 `$argon2id$` 前缀；未知用户与禁用账号同为 **401 `INVALID_CREDENTIALS`**（同码不泄露存在性）；第 1–4 次失败 `failed_attempts` 逐次递增且 `locked_until` 为空，第 5 次 → `locked_until` 非空且 `failed_attempts=0`，随后正确密码 → **423 `ACCOUNT_LOCKED`**。⑦会话与审计：cookie 值的 sha256 可在 `console_session` 查到（明文查询 0 行）、TTL 恰 12h、`revoked_at` 为空；登录 `config_audit_log` 恰好 +1 行、`actor_user_id` = 账号本人、`after_json={"username","role"}`、`before_json` 为空、载荷无 password。**收尾**：自建账号/会话/审计与跨租户对照样本在 fixture `finally` 删除（`console_account`/`console_session`/`config_audit_log` **不在** DFX 基座 `cleanup()` 覆盖内，不清会污染共享开发库）；运行后无残留服务进程。 | verified |
| RULE-auth-001 | **无 RED（规则既有实现已满足，验收补机检）**：verifier 首跑即绿。规则的三层授权/Effective Capability 面由原 verifier 承载；本任务的机检增量是 Console 侧的 CSRF/会话/密码口径（扰动 ①③⑤⑥⑦ 证明其非空转）。 | `test_user_side_relations.py -k s04` → **2 passed in 0.40s**；`tests -k schema_parity` → **35 passed, 1589 deselected in 3.62s** | 原 verifier `tests/console_platform/test_user_side_relations.py::test_s04_grant_and_revoke_share_application_service`/`::test_s04_unknown_agent_or_user_returns_not_found` + `tests -k schema_parity`；Console 侧增量见 E-08 七例 | 真实 Console HTTP + 真实 PostgreSQL（原 verifier 的真实边界）+ 本任务新增的真实 HTTP/PG 机检面。 | verified |

> 边界登记（本任务只断言、不改行为）：① ADMIN 门控的**前端隐藏**层归各前端 owner，本用例只断言可机检的后端 403 兜底；② 未授权资源不进 Prompt/ToolRegistry/Catalog 与「撤销只影响后续新 Run」归 **S-10（TASK-011）**；③ 三处 internal 端点的服务身份门控归 **E-07（TASK-007）**；④ 租户隔离自 2026-09-28 全量收敛为「账号派生」，`X-Tenant-Id` 仅对内部服务与公开登录生效——本任务只断言该口径，**未改任何路由的租户来源**；⑤ 会话滑动续期与登出幂等不在本 argv 内（由既有 `tests/console_auth/test_login.py` 承接）。
- E-08: verified — automated command passed; run_id=65b1695991bd4529bdba0ba990c92ab7 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=9ad0d941122c4215a485ae271fc7dc0c (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=abd14a904b1348e3b9b6fb17e2e79ae2 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=7d599cd8d80d4d48a0e11abc66bf08c1 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=66d0986a691843019a674b8dd8ef8b28 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=cf49bc596e5748fa8e283aa0fb5055a6 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=781a6f5cdfd742d3b6c14c244bd070a9 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)
- [2026-09-29] E-08 / RULE-auth-001 终态：新增 `tests/acceptance/dfx/test_dfx_api_security.py`（7 例，真实 Console uvicorn 子进程 + 真实 PostgreSQL 逐行回读），契约表/覆盖表/证据表三处 verified；7 处扰动取证证明断言非空；**无生产改动**（验收任务，生产无缺口 ⇒ 无 RED 如实登记）。

---
- [2026-09-29] started
- [2026-09-29] completed (done)
## TASK-009: 模型恢复验收（429/5xx/超时/deadline/cancel）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 14-dfx-acceptance.backend.design.md#3.4.6 模型恢复矩阵, 14-dfx-acceptance.backend.design.md#2.4.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-09
- **Files**: `tests/acceptance/dfx/test_dfx_model_recovery.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

FEAT-06 的模型恢复面。**验收对象是生产恢复链**：`AgentRunner._complete_with_recovery`（`packages/agent-core/.../runner.py:435`）+ `AuditedModelProvider`（`executor.py:555`），经真实 runtime 起 Run 取证；**原 design 点名的 `ModelGateway` 未接入生产**（全仓仅其单测引用），不再作为验收对象。

要求：429 优先按 `Retry-After`（取自 **HTTP 响应头**）等待后重试；5xx/529/连接重置/超时按**指数退避**重试；等待不超过剩余 deadline；cancel 优先于重试（每轮调用前检查）；重试计数与原因**逐 attempt** 写入 `runtime.model_invocation_audit`。

**两处设计意图当前未实现，本任务只登记不修（另立整改）**：① 退避**无 jitter**（全仓无 `jitter` 命中）；② `prompt too long` 的"一次 Context rebuild/compaction 后重试"**整条能力不存在**（全仓无 `compaction`/`rebuild`/`context_length` 机制）。

### Checklist

- [x] [E-09][integration] 以 **`Runtime 恢复链 → Provider`** 为真实边界编写用例（真实 runtime 起 Run + 真实本地 HTTP 探针承载 provider）：429（`Retry-After` 取自响应头）优先等待后重试、5xx 指数退避重试、连接超时、deadline 不足不重试、cancel 立即停止五类触发各自的预期行为；等待时长受剩余 deadline 约束、无无限等待。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_model_recovery.py -k e09 && uv run pytest -q tests/agent_runtime/test_model_recovery.py"]`。
- [x] Provider 侧用**真实本地 HTTP 探针**承载外部端点，禁止伪造外部响应为「已通过」。现成 `tests/e2e/openai_probe_app.py` **不支持** 429/`Retry-After`/挂死超时（只有 500 与固定延迟）⇒ 按 TASK-010/011 的既定做法在测试模块内自造**同性质的真实 HTTP 探针端点**（Runtime 经真实 HTTP 调用，非拦截、非响应改写），按用例注入 429+`Retry-After`、5xx、挂死、以及可脚本化的响应序列。
- [x] 断言 deadline 与 cancel 的优先级：构造「剩余 deadline 不足」与「等待中收到 cancel」两个对照样本，断言前者不重试、后者立即停止且重试计数不再增长。
- [x] 断言重试计数与原因**逐 attempt** 写入 `runtime.model_invocation_audit`（`status`/`retry_reason`/`attempt`/`run_id`），且**载荷不含密钥明文**——该表在设计上就没有任何 payload/密钥列（`api_key` 只进 `Authorization` 头），故断言口径为"表行内不含 api_key 明文 + 该表无 payload 列"。
- [x] 显式边界（**不修，只登记**，均已核对到 file:line）：① `ModelGateway` **未接入生产**（`model_gateway.py` 仅 `tests/agent_runtime/test_model_recovery.py` 引用）⇒ 待整改为"接线或删除，不得两套并存"；② 退避**无 jitter**（`runner.py:477`、`model_gateway.py:110` 均为纯 `base*2**attempt`）⇒ 待整改加 jitter；③ `prompt too long` 的"一次 rebuild/compaction 后重试"**未实现**（全仓无相关机制）⇒ 待整改（需先定上下文压缩策略）。三项**不得**写成通过或已覆盖。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；三处缺口按"显式边界（不修，只登记）"逐条落到 Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-09 | integration | Runtime 恢复链 → Provider（`AgentRunner._complete_with_recovery` + `AuditedModelProvider`） | `Retry-After`（响应头）优先；退避受 deadline 约束；cancel 优先；无无限等待；逐 attempt 计数与原因入 `model_invocation_audit`；**缺口登记**：无 jitter、`prompt too long` 未实现、`ModelGateway` 未接线 | tests/acceptance/dfx/test_dfx_model_recovery.py + tests/agent_runtime/test_model_recovery.py / E-09 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_model_recovery.py -k e09 && uv run pytest -q tests/agent_runtime/test_model_recovery.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-09 | **无 RED（验收任务；本任务断言的生产行为无缺口，首跑即绿）**。首跑与首次 gate 共暴露 5 处问题，全部出在**我的测试自身**、已当场修正：① 审计 `attempt` 是 **0 起算**（生产 writer `AuditedModelProvider._invoke` 的计数器），我误写 1 起算；② cancel 用例里我提前 `break` 出 SSE ⇒ 关流后执行器停摆、租约过期被 reaper 标 `RUN_ABANDONED` ⇒ 改为**在流内发取消并读完**；③④ 见"扰动取证"的 P2/P3（P2 暴露不变式由**两道守卫**共同保证；P3 暴露我的 cancel 断言过弱、已收紧为**时间有界**）；⑤ **首次 gate 被此文件打成 95 failed / 159 passed**：我在**同步**用例里用 `asyncio.run()` 访问 PG，而 `asyncio.run` 收尾会 `set_event_loop(None)` 置空**主线程**事件循环 ⇒ 同一 pytest 会话里其后所有依赖主线程 loop 的异步套件集体报 `RuntimeError: There is no current event loop in thread 'MainThread'`（复现：与 `test_skill_schema_constraints.py` 同跑即崩，旁证 95 个失败全在 `test_dfx_model_recovery.py` 之后的套件）。改用仓内既有的线程版 `run_db`（子线程独立事件循环）后，全量 `pytest tests/acceptance` **254 passed / 0 failed**（18.6 分钟）。为证明断言非空，做了 **4 处扰动取证**（改生产代码 → 对应断言变红 → 按字节还原 → `git status --porcelain -- apps/ packages/` 为空）：P1 `runner._retry_delay` 忽略 `retry_after` → 失败「退避未按 Retry-After=1s：实测 0.11s」；P2b `_retry_delay` 的 deadline 预判改为"钳制"**且** `_ensure_runnable` 的 deadline 阈值放大（两道守卫同时失效）→ 失败「deadline 不足时不得重试：4 次」（单改一道不会红）；P3 `_cancel_aware_sleep` 换成一次性 `asyncio.sleep(delay)` → 失败「取消未立即生效：30.1s（Retry-After=30s）」（收紧断言前此项**不会红**）；P4 `AuditedModelProvider` 审计写入短路 → 失败「审计行 []」。 | 独立复跑：`-k e09` → **6 passed in 6.76s**；配对 `tests/agent_runtime/test_model_recovery.py` → 3 passed；runner 判 **verified**（functional / exit 0）。 | `test_dfx_model_recovery.py::test_e09_rate_limit_retry_after_header_is_honoured` / `::test_e09_unavailable_backs_off_exponentially` / `::test_e09_connection_reset_is_retried` / `::test_e09_deadline_exhausted_fails_without_retrying` / `::test_e09_cancel_stops_retries_immediately` / `::test_e09_audit_rows_carry_attempts_and_no_secret_plaintext` | **真实 Runtime 服务（真实 HTTP）→ 真实 PostgreSQL**；provider 侧由**本模块自造的真实 HTTP 探针**承载（Runtime 经真实 HTTP 调用该端点，非拦截、非响应改写、无 monkeypatch）。逐条实测：① `Retry-After: 1` ⇒ 相邻两次调用间隔 **0.9–1.6s**（指数基线仅 0.1s，故能区分）；② 两次 500 ⇒ 间隔 0.1s → ≥0.2s（指数，**无 jitter**）；③ 连接重置 ⇒ 同样重试（`ModelUnavailableError` 类）；④ `deadline_ms=1000` + `Retry-After: 30` ⇒ **仅 1 次 provider 调用**、Run `FAILED`、落库 `error_code=COMMON_INTERNAL_ERROR`；⑤ 取消 ⇒ 返回 `CANCELLING`、Run 终态 `CANCELLED`，**发取消到终态 ≤5s**（当前 Retry-After=30s），且取消后 provider 调用数不再增长；⑥ 审计逐 attempt 一行（**0,1,2**；`RETRY/RATE_LIMITED`、`RETRY/UNAVAILABLE`、`OK`）、`run_id` 可反查、`api_key` 明文不出现在任何列（该表无 payload 列；探针侧确证密钥只走 `Authorization: Bearer`）。 | verified |

> **显式边界（不修，只登记；本任务不覆盖，另立整改）** —— 三处均已核对到 file:line，**不得**读作已通过或已覆盖：
> ① **退避无 jitter**：生产为纯指数 `DEFAULT_RETRY_BASE_SEC * 2**attempt`（`packages/agent-core/.../runner.py:477`；`model_gateway.py:110` 同形），全仓 `grep -i jitter` 无命中 ⇒ 本模块只断言指数形状，**不宣称 jitter 已被满足**。
> ② **`prompt too long` 的"一次 Context rebuild/compaction 后重试"未实现**：全仓无 `compaction`/`rebuild`/`context_length`/`maximum context` 机制（`context_builder.json_compact` 只是 JSON 压缩）⇒ 该 checklist 项**无可断言对象**，登记为缺口（需先定上下文压缩策略）。
> ③ **`ModelGateway` 未接入生产**：全仓仅被 `tests/agent_runtime/test_model_recovery.py` 引用；真实生产恢复链是 `AgentRunner._complete_with_recovery` + `AuditedModelProvider`（本任务已验证）⇒ 待整改为"接线或删除，不得两套并存"。
- E-09: verified — automated command passed; run_id=a5cc5b54d3b44898b5637d43ae5afe34 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=28bb2340633a4e88ad6e6129e8adab0b (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=e63cbdddaec84e6c9cef9038b07f5103 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=5a52d00158b148b4b3b2ffdf723c65a9 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=b65e40710b0948ae97ecaed43aec3e60 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)
- [2026-09-30] E-09 终态。**先按代码事实重写了 design 与任务**：design §3.4.6 改为"预期（要求）/ 代码现状（file:line）/ 约束"三列并新增"归属"行，§2.4.2 的 E-09 边界由 `ModelGateway → Provider` 改为**生产恢复链**（`AgentRunner._complete_with_recovery` + `AuditedModelProvider`）；任务 Description/Checklist/契约行同步，manifest 仅 E-09 行的 `boundary` 随之更新（其余 26 行状态与证据原样保留）。新增 `tests/acceptance/dfx/test_dfx_model_recovery.py`（6 例：真实 Runtime 服务 + 真实 PG + **本模块自造的真实 HTTP 故障探针**注入 429+`Retry-After` 响应头 / 500 / 连接重置 / 长退避）：`-k e09` **6 passed in 6.76s**，runner 判 **verified**（functional / exit 0）。**4 处扰动取证**：P1 忽略 `Retry-After`（实测 0.11s 即红）、P2b 两道 deadline 守卫同时失效（重试 4 次，单改一道不会红）、P3 取消感知失效（取消到终态 30.1s；**由此发现我原来的 cancel 断言过弱，已收紧为「发取消到终态 ≤5s」的时间有界判据**）、P4 审计写入短路（审计行 0）。**三处缺口登记（不修，另立整改）**：退避**无 jitter**、`prompt too long` 的"一次 rebuild/compaction"**未实现**、`ModelGateway` **未接线**（均核对到 file:line，不得读作已覆盖）。

---
- [2026-09-30] started
- [2026-09-30] completed (done)
## TASK-010: 黄金旅程：绑定、流式与中断恢复/取消

- **Status**: verified
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

- [x] [S-05][E2E] 以 `Browser/HTTP → Console → PG → IM Gateway` 为真实边界编写用例：未绑定用户发消息触发 `/bind` 后再次对话，断言身份稳定映射 PlatformUser、**不自动授予 Agent**（授权表无新增行）。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s05 && uv run pytest -q tests/acceptance/im_gateway/test_binding.py"]`。
- [x] [S-06][E2E] 以 `Gateway → Runtime SSE → Browser` 为真实边界编写用例：发起普通对话断言首事件 `run.created`、`message.delta` 按 seq 顺序到达、终态为 `run.completed`/`run.failed`、封套字段 `{run_id,seq,timestamp,type,data}` 完整。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s06 && uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py"]`。
- [x] [S-06][E2E] **显式边界登记（不冒充）**：本仓前端无 SSE 消费面，「Browser」臂在本需求内不可执行（自建流式页面属 design Out of Scope 的业务功能）；必须在 Acceptance Evidence 中如实登记该边界并给出「真实 Gateway→Runtime HTTP/SSE 链路已达成的断言清单」，不得写成完整覆盖。
- [x] [S-09][E2E] 以 `Runtime interrupt → resume/cancel` 为真实边界编写用例：触发澄清 → `WAITING_INPUT`；resume 后继续且 seq 不重排；另一 Run 执行 `cancel-active` → 返回 `CANCELLING` 并协作终态；无活跃 Run → `NO_ACTIVE_RUN`；`WAITING_INPUT` 直接 CAS 取消。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s09 && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"]`。
- [x] 流式断言只认已落库事件：`seq`/`timestamp` 沿用 `canonical_event` 持久值（禁止按连接自增），`: heartbeat` 不计 seq；`STREAM_TIMEOUT_SEC` 的「有界失败」用例须注入小值，不得依赖默认 300s。
- [x] 失败路径允许改写路由制造超时/错误，但**不得 fulfill 业务响应体**；成功路径（S-*）不得出现 `page.route(`（本任务为 pytest 链路，同口径适用于任何 HTTP 拦截）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | E2E | Browser/HTTP → Console → PG → IM Gateway | 身份稳定映射 PlatformUser；不自动授予 Agent | tests/acceptance/dfx/test_dfx_journeys.py + tests/acceptance/im_gateway/test_binding.py / S-05 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s05 && uv run pytest -q tests/acceptance/im_gateway/test_binding.py"]` | verified |
| S-06 | E2E | Gateway → Runtime SSE → Browser | 首事件 `run.created`；`message.delta` 顺序；终态事件；封套完整 | tests/acceptance/dfx/test_dfx_journeys.py + tests/acceptance/im_gateway/test_runtime_stream.py / S-06 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s06 && uv run pytest -q tests/acceptance/im_gateway/test_runtime_stream.py"]` | verified |
| S-09 | E2E | Runtime interrupt → resume/cancel | `WAITING_INPUT`→resume 继续；`CANCELLING` 协作终态；`NO_ACTIVE_RUN` | tests/acceptance/dfx/test_dfx_journeys.py + tests/acceptance/runtime/test_run_lifecycle.py / S-09 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_journeys.py -k s09 && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | **E2E 本阶段不执行 RED**（工作流口径：E2E 的 RED/GREEN 统归 `/cf-task:verify-e2e`）。文件已自检实跑，**不是「未执行」**：整文件 `6 passed in 20.91s`。 | **e2e_deferred**（终验归 verify-e2e）；自检实跑：`-k s05` → 1 passed，配对 `tests/acceptance/im_gateway/test_binding.py` → 4 passed in 17.27s | `test_dfx_journeys.py::test_s05_bind_maps_identity_stably_without_granting_agent` | **真实 `wss://` WS 探针 → 真实 IM Gateway 子进程 → 真实 Console `POST /internal/channel/bind` → 真实 PostgreSQL**（全程无替身、无 HTTP 拦截）。真实盘面回读：推 `/bind <code>` 后 WS 出站收到「绑定成功」；`control.channel_identity` 按增量 +1 且最新行 `platform_user_id` = 种子用户、`is_deleted=false`；`control.bind_code` 出现 `USED`；`control.agent_access_grant` 行数**不变**（不自动授予 Agent）；「再次对话」后新建 Run 的 `user_id` 仍是同一 PlatformUser。 | e2e_deferred |
| S-06 | 同上（E2E 不执行 RED）。 | **e2e_deferred**；自检实跑：`-k s06` → 1 passed，配对 `tests/acceptance/im_gateway/test_runtime_stream.py` → 5 passed in 18.66s | `test_dfx_journeys.py::test_s06_stream_reply_envelope_and_monotonic_seq` | **真实 Gateway → Runtime HTTP/SSE → 真实 WS 出站**。**已达成的断言清单**（Browser 渲染**不在**其中，见下）：① WS 出站收到流式增量帧（`aibot_respond_msg` 的 `body.stream.content`），证明 Gateway 把 Runtime 的流式内容真实转发出去了；② 落库 `runtime.canonical_event` 的流式帧首事件 `stream_type=run.created`、末事件 `run.completed`；③ `message.delta` 按 seq 递增，且整条事件流 seq 严格单调（业务帧 `stream_type IS NULL` 与流式帧共用 seq 空间）；④ 封套 `{run_id,seq,timestamp,type,data}` 四要素全部由**落库值**重建（`seq`/`create_time` 取持久值，禁按连接自增；`: heartbeat` 是注释帧、不落库故不占 seq）；⑤ Run 终态 status ∈ {COMPLETED, FAILED}。**边界登记：本仓前端无 SSE 消费面 ⇒「Browser」臂在本需求内不可执行**（自建流式页面属 design Out of Scope 的业务功能），故本行**不写成完整覆盖**。 | e2e_deferred |
| S-09 | 同上（E2E 不执行 RED）。 | **e2e_deferred**；自检实跑：`-k s09` → 4 passed，配对 `tests/acceptance/runtime/test_run_lifecycle.py` → 8 passed in 7.20s | `test_s09_waiting_input_resume_continues_without_seq_reset` / `test_s09_cancel_running_is_cooperative_and_terminal` / `test_s09_cancel_waiting_input_is_cas_cancelled` / `test_s09_cancel_active_without_active_run_returns_no_active_run` | **真实 Runtime 子进程 + 真实 PostgreSQL/Redis**。① 恢复：`WAITING_INPUT` Run 经**同会话再次发言**恢复 → SSE 首事件 `run.created` 且 `data.resumed is True`、`run_id` 不变、`seq > 1`（不重排）、`run_interrupt.status=RESOLVED`、该 Run 落库 seq 严格单调（快照取本租户真实执行留下的 `runtime_snapshot`）；② 运行中取消：给真实 LLM 探针注入 3s 延迟（改其进程 env 后**重启该真实探针**，非替身），流到首帧即调 `cancel-active` → 200 `CANCELLING`，协作终态 `run.completed` + `status=CANCELLED`，`run_record.cancel_requested=true`，Redis `run:cancel:{id}` 存在；③ `WAITING_INPUT` 直接 CAS：返回 `CANCELLED`（实测该路径**不置** `cancel_requested`）、interrupt `CANCELLED`、业务事件含 `CANCEL`、终态 `run.completed`；④ 无活跃 Run → 404 `NO_ACTIVE_RUN`。 | e2e_deferred |

> 另登记两条**不冒充**的边界：① `STREAM_TIMEOUT_SEC` 的「有界失败」子用例**本模块未覆盖** —— 该常量是 Gateway 模块常量（`muad_im_gateway/application/runtime_client.py`），**无 env 覆盖**，子进程形态下无法注入小值（不得依赖默认 300s 等它真超时）；该分支由 `tests/gateway/test_runtime_client.py`（monkeypatch `STREAM_TIMEOUT_SEC`）承接。② 企业微信真机重连（E-10）为 manual，不在本模块。
> 成功路径未使用任何 HTTP 拦截（文件内无路由改写、无 `page.route(` 同口径的响应伪造）；唯一的失败注入是**真实探针的 env 延迟 + 真实进程重启**。
- S-05: e2e_deferred — automated command e2e_deferred; run_id=c43871d766234fb28eb425febc585aca (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=c43871d766234fb28eb425febc585aca (confirmed_by: runner)
- S-09: e2e_deferred — automated command e2e_deferred; run_id=c43871d766234fb28eb425febc585aca (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=40c8e0dd57794a9798cc8a0cf2b2bb3e (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=40c8e0dd57794a9798cc8a0cf2b2bb3e (confirmed_by: runner)
- S-09: e2e_deferred — automated command e2e_deferred; run_id=40c8e0dd57794a9798cc8a0cf2b2bb3e (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)
- [2026-09-30] S-05/S-06/S-09 编写并登记（E2E）：新增 `tests/acceptance/dfx/test_dfx_journeys.py`（6 例；真实 `wss://` 渠道探针 → 真实 Gateway/Console/Runtime×2/Worker/LLM 探针子进程 → 真实 PostgreSQL/Redis）。三段契约 argv 自检全过（`-k s05` 1+4、`-k s06` 1+5、`-k s09` 4+8），整文件 `6 passed in 20.91s`；runner 判 `e2e_deferred`，终验归 `/cf-task:verify-e2e`。边界登记（不冒充）：Browser 臂不可执行（本仓前端无 SSE 消费面）、`STREAM_TIMEOUT_SEC` 有界失败子用例归既有 monkeypatch 用例。**无生产改动**。

---
- [2026-09-30] started
- [2026-09-30] completed (done)
## TASK-011: 执行路由、批量 fan-out/fan-in 与授权可见性/多 IM 路由

- **Status**: verified
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

- [x] [S-07][E2E] 以 `Runtime → Worker → Schedule` 为真实边界编写用例：分别触发同步 Skill、异步 Task、定时 Schedule，断言路由符合 `execution_mode`；Schedule 到点只创建一次 Task；ONCE 成功后 `COMPLETED` 且无 `next_fire_at`。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s07 && uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_schedules.py"]`。
- [x] [S-08][E2E] 以 `Worker Parent/Child → fan-in` 为真实边界编写用例：批量意图产生 Parent/Child，断言 Child 幂等键为 `parent:{parent_id}:{item_key}`、并发受限、fan-in 后 Parent CAS 完成并**只推最终结果一次**。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s08 && uv run pytest -q tests/acceptance/task_schedule/test_batch.py"]`。
- [x] [S-10][E2E] 以 `授权解析 → Prompt/ToolRegistry → IM 路由` 为真实边界编写用例：SELECTED/ALL 用户范围下未授权 Skill/MCP 不进入 Catalog/Prompt；撤销授权后新 Run 不可见而旧 Snapshot 不变；多 `bot_id` 路由同一 Agent。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_authorization_scope.py -k s10 && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"]`。
- [x] [E-10][manual] **保持 manual，不降级**：企业微信真机重连（`Gateway WS → 企业微信`）需真实凭据；执行条件为有真实凭据的环境，记录 `reconnect` 指标可见与 SDK backoff 重连成功；**环境受限时只记录「需真实凭据 + 原因」，绝不写成通过**。
- [x] 断言「撤销只影响后续新 Run」时，必须同时取旧 Run 的 Snapshot 前后哈希作对照，避免只断言新 Run 的行为就宣称该条目覆盖。
- [x] 断言无授权泄漏：对未授权资源构造真实 MCP/Skill 探针，断言其工具**未被调用**（调用计数为 0），而不是只断言返回体为空。
- [x] 收尾清理：批量/定时/多 bot 产生的 Task、Schedule、Binding、会话与审计行按租户清理为 0；运行后无残留 `uvicorn`/`muad_*.main` 进程。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；E-10 行按 manual 口径登记执行条件与确认人。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | E2E | Runtime → Worker → Schedule | `execution_mode` 路由；Schedule 只触发一次；ONCE `COMPLETED` 无 `next_fire_at` | tests/acceptance/dfx/test_dfx_routing.py + tests/acceptance/task_schedule/test_execution.py + test_schedules.py / S-07 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s07 && uv run pytest -q tests/acceptance/task_schedule/test_execution.py tests/acceptance/task_schedule/test_schedules.py"]` | verified |
| S-08 | E2E | Worker Parent/Child → fan-in | Child 幂等键；并发受限；fan-in CAS 且只推最终结果 | tests/acceptance/dfx/test_dfx_routing.py + tests/acceptance/task_schedule/test_batch.py / S-08 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_routing.py -k s08 && uv run pytest -q tests/acceptance/task_schedule/test_batch.py"]` | verified |
| S-10 | E2E | 授权解析 → Prompt/ToolRegistry → IM 路由 | 未授权不进 Catalog/Prompt；撤销只影响新 Run；多 bot_id 同一 Agent | tests/acceptance/dfx/test_dfx_authorization_scope.py + tests/acceptance/runtime/test_capability_snapshot.py / S-10 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_authorization_scope.py -k s10 && uv run pytest -q tests/acceptance/runtime/test_capability_snapshot.py"]` | verified |
| E-10 | manual | Gateway WS → 企业微信 | SDK backoff 重连成功、`reconnect` 指标可见；**标注「需真实凭据，环境受限时记录原因」** | 人工真机复验记录 / E-10 | - | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-07 | **E2E 本阶段不执行 RED**（工作流口径：E2E 的 RED/GREEN 归 `/cf-task:verify-e2e`）。文件已自检实跑（并由 TASK owner 独立复跑），**不是「未执行」**。 | **e2e_deferred**（终验归 verify-e2e）；独立复跑：`-k s07` → 3 passed in 29.79s，配对 `tests/acceptance/task_schedule/test_execution.py`+`test_schedules.py` → 5 passed in 52.68s | `test_dfx_routing.py::test_s07_async_skill_call_submits_immediate_task_and_run_completes` / `::test_s07_sync_skill_call_executes_inline_without_task_row` / `::test_s07_scheduled_once_fires_exactly_one_task_and_completes` | **真实 PG/Redis + 真实 uvicorn 子进程栈**（Console / Runtime / Worker×2，SchedulerLoop 内嵌在每个 Worker，无独立 scheduler 进程）；断言全部取自持久化盘面。① **ASYNC 路由**：Run 经真实 `execute_skill` 命中 `execution_mode=ASYNC` 的 Skill ⇒ `task.task_execution` **恰一行**（`task_type=SKILL`、`execution_mode=ASYNC`、`trigger_type=IMMEDIATE`、`source_run_id` 指向该 Run），`runtime.tool_call_audit` 有且仅有该工具且 `OK`，Run `COMPLETED` 且 Task 随后 `COMPLETED`。② **SYNC 路由**：命中 `execution_mode=SYNC` ⇒ **零 Task 行**，且脚本副作用真实发生（写入 `input.side_effect_path` 的文件内容为 `executed`）。③ **SCHEDULED**：真实 `POST /internal/schedules` 建 ONCE + 把 `next_fire_at` 推到期 ⇒ 到点**只创建一行** `trigger_type=SCHEDULED` 的 Task（跨一个 Scheduler 拍点仍为 1），ONCE 成功后 `status=COMPLETED`、`completed_at` 非空、**`next_fire_at IS NULL`**。 | e2e_deferred |
| S-08 | 同上（E2E 不执行 RED）。 | **e2e_deferred**；独立复跑：`-k s08` → 2 passed in 40.30s，配对 `tests/acceptance/task_schedule/test_batch.py` → 1 passed in 6.45s | `test_dfx_routing.py::test_s08_batch_fanout_parked_children_and_single_fan_in` / `::test_s08_batch_children_start_within_concurrency_limit` | **真实 Worker Parent/Child → fan-in**（同一真实子进程栈）。Parent：`task_type=BATCH`、终态 `COMPLETED`、`result_json` 汇总 `total==succeeded==4`、`delivery_mode=NONE`；4 个 Child：`parent_id`/`root_id` 指向 Parent、`status=COMPLETED`、**`idempotency_key == f"parent:{parent_id}:{item_key}"`**；**并发受限**以真实列断言（停放 Child 的 `not_before == 9999-12-31 23:59:59+00` 恰 2 个、可行恰 2 个，且全程轮询「非停放且未终态的 Child」从不超过 `max_concurrency`）；**fan-in 只推一次**以 `task_event` 中 `FAN_IN` **恰 1 条**断言。 | e2e_deferred |
| S-10 | 同上（E2E 不执行 RED）。 | **e2e_deferred**；独立复跑：`-k s10` → 3 passed in 8.33s，配对 `tests/acceptance/runtime/test_capability_snapshot.py` → 2 passed in 1.89s | `test_dfx_authorization_scope.py::test_s10_unauthorized_skill_and_mcp_absent_from_catalog_and_never_called` / `::test_s10_revocation_only_affects_new_run_snapshot` / `::test_s10_multiple_bot_ids_route_to_same_agent` | **授权解析 → Prompt/ToolRegistry → IM 路由**（真实解析查询 + 真实 Run + 真实 MCP 探针 + 真实 Console 内部端点）。① 未授权（`user_scope=SELECTED` 且无用户授权行）Skill/MCP **不在**该 Run 的 `runtime_snapshot.skill_catalog_json`/`mcp_catalog_json`，对照臂在内；② **未授权资源未被调用**：真实 MCP 探针的 `tools/call` 计数——可见探针 ≥1（正对照，证明探针真在收请求）而未授权探针**== 0**，`egress_audit` 仅出现可见 server 的 `mcp://<key>/<tool>` 且 `ALLOW`；③ **撤销只影响新 Run**：撤权（`is_deleted=true`）后新 Run 快照两者皆不可见，且**旧 Run 的 `runtime_snapshot.content_hash` 前后各读一次完全相等**、旧快照两个 catalog json 逐项不变（不是只断言新 Run 行为）；④ **多 bot_id 路由同一 Agent**：两个 `bot_id` 经 `POST /internal/channel/resolve` 均返回同一 `agent_id`。 | e2e_deferred |

> 本任务**如实登记的边界**（不冒充覆盖，写进两个文件的 docstring）：① **LLM 由本模块自带的脚本化真实 HTTP 探针承载**——既有 `tests.e2e.openai_probe_app` 的 tool_call 参数写死为 `{"query":"ping"}`，表达不了 `execute_skill` 需要的 `skill_key`/`input`；在「不改既有文件」约束下由本模块提供同性质的**真实 provider 端点**（Runtime 经真实 HTTP 调用，非拦截、非响应改写、无 monkeypatch）。② **SYNC 臂的 Skill 包自造**：Runtime 侧 `SkillPackage.load` 要求 `SKILL.md` frontmatter，而 `environment.build_skill_zip` 只写 `scripts/main.py`（仅够 Worker 侧执行）。③ **Prompt 文本不落库**：以 `skill_catalog_json`/`mcp_catalog_json` 作为 Prompt/ToolRegistry 的**持久等价证据**，不对 prompt 字符串直接断言。④ **停放窗口**：`not_before` 停放的 Child 会在兄弟终态时被 fan-in 释放（真实列被覆写），故「可行/停放」只在首个 Child 终态前可观测——本模块用自带慢速批量脚本把窗口拉到数秒并在窗口内断言。⑤ misfire / CRON 臂由 `test_dfx_recovery.py::test_b03_*` 与 `task_schedule/test_schedules.py` 承接，本模块不重复。
- S-07: e2e_deferred — automated command e2e_deferred; run_id=ed00e49fe1854bccade5b6cdd8bd5217 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=ed00e49fe1854bccade5b6cdd8bd5217 (confirmed_by: runner)
- S-10: e2e_deferred — automated command e2e_deferred; run_id=ed00e49fe1854bccade5b6cdd8bd5217 (confirmed_by: runner)
- E-10: verified — 2026-09-30 真机复验（真实 bot 凭据 + 企业微信线上端点）：认证成功 wecom_ws_connected=1（04:55:11.580）；经本地 TLS 中继切断真实连接后 wecom_bot_disconnected(connection_lost) + 指标归零 + state=BACKOFF，退避 1.0s 后重连成功 CONNECTED attempt=2（04:55:24.601），指标复归 1；重连由网关适配器监督退避执行（SDK 侧 max_reconnect_attempts=0 关闭其内部重连），reconnect 指标以 wecom_ws_connected（GET /metrics）与 state_changed 日志的 attempt 计数为证。端到端：企微发消息→入站帧→绑定落 channel_identity→对话 run COMPLETED（run.created→message.delta→run.completed）→回执 Reply ack received，用户确认实际收到回复；期间发现并修复回复协议误用（提交 6d142ab）。凭据仅经环境变量写入本机临时租户行，收尾随租户删除（库内残留 0）。 (confirmed_by: jahan)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=43b0590911994dd1bb086c81a1e5b749 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=43b0590911994dd1bb086c81a1e5b749 (confirmed_by: runner)
- S-10: e2e_deferred — automated command e2e_deferred; run_id=43b0590911994dd1bb086c81a1e5b749 (confirmed_by: runner)
- E-10: verified — 用户确认：「确认，署名 jahan」——按 2026-09-30 企业微信真机复验结论（真实 bot 凭据 + 线上端点：wecom_ws_connected=1 → 本地 TLS 中继切断真实连接 → wecom_bot_disconnected(connection_lost) + 指标归零 + BACKOFF → 退避 1.0s 后 CONNECTED attempt=2；端到端打消息→入站帧→绑定落 channel_identity→对话 run COMPLETED→回执 Reply ack received，用户确认实际收到回复） (confirmed_by: user:jahan)
- S-07: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)
- [2026-09-30] S-07/S-08/S-10 编写并登记（E2E）：新增 `tests/acceptance/dfx/test_dfx_routing.py`（S-07 三臂 SYNC/ASYNC/SCHEDULED、S-08 batch fan-out/fan-in）与 `tests/acceptance/dfx/test_dfx_authorization_scope.py`（S-10 授权可见性/撤销后旧快照不变/多 bot 同 Agent）；三段契约 argv 独立复跑全过（3+5 / 2+1 / 3+2），runner 判 `e2e_deferred`（终验归 verify-e2e）。边界如实登记 5 条：自造脚本化真实 HTTP provider（既有探针的 tool_call 参数写死、表达不了 `execute_skill` 参数）、自造 SYNC 包（Runtime 侧要求 `SKILL.md`）、Prompt 不落库改用快照 catalog 作等价证据、并发停放窗口只在首个 Child 终态前可观测、misfire/CRON 归他处。
- [2026-09-30] **E-10 真机复验通过（用户确认，署名 jahan）**：真实 bot 凭据 + 企业微信线上端点 —— 认证成功 `wecom_ws_connected=1`；真实断链后 `wecom_bot_disconnected(connection_lost)` + 指标归零 + `BACKOFF`，退避 1.0s 后 `CONNECTED attempt=2`；端到端「企微发消息 → 入站帧 → 绑定落 `channel_identity` → 对话 `run COMPLETED` → 回执 `Reply ack received`」全部实测，用户确认实际收到回复。复验中发现并修复回复协议误用（会话内回复须走 `aibot_respond_msg` + stream 体、主动投递体须 markdown —— 官方服务对 `msgtype=text` 一律回 `errcode=40008`），另提交 `6d142ab`（含两条守卫用例与各套件读帧统一到 `tests.e2e.wecom_probe_app.frame_text`）。

---
- [2026-09-30] started
- [2026-09-30] resumed (in-progress)
- [2026-09-30] completed (done)
## TASK-012: 无状态与 Snapshot 确定性（A/B Pod）

- **Status**: verified
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

- [x] [S-12][E2E] 以 `Runtime A/B Pod → PostgreSQL + Artifact Store` 为真实边界编写用例：Run R1 后更新配置（Agent/Model/Skill/MCP 任一版本或授权）；删除（真实终止）Pod；继续 Turn 2，断言 R1 Snapshot 的 `snapshot_hash`/内容**不漂移**、Turn 2 在新 Pod 重建上下文、无 sticky session 依赖。执行 argv：`["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_stateless.py -k s12 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"]`。
- [x] [RULE-snapshot-001][integration] 作为唯一最终负责人：每个新 Run/Task 执行前冻结 Snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；预算只属 execution snapshot 的 `budget`，Run 侧 `RuntimeSnapshot` 无 budget 列、等价载体是 `policy_json`，默认 `{"max_model_retries": 3}`）；配置/授权变更只影响后续新 Run/Task；终态与非终态写入均 CAS；`skills` 恒为 1；`api_key` 从快照与 hash 中剥离。verifier argv：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`。
- [x] 补 design 已登记的缺位断言：**「配置/授权变更只影响后续新 Run/Task」当前在指定 verifier 中缺该断言**（harness-snapshot 自注），本任务补齐「变更后旧 Run 仍用旧快照」的真实断言并登记该缺位已收敛；不得只依赖 resume 代码路径的间接证据。
- [x] 删除 Pod 用真实进程终止（`SIGKILL`），并以真实库回读该 Run 的状态与快照行；不用日志推断，不引入 sticky session 假设。
- [x] 断言 Snapshot 的确定性口径：hash 为 `"sha256:"` + canonical JSON（`sort_keys` + 紧凑分隔符 + `ensure_ascii=False`），两侧同口径比对。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-12 | E2E | Runtime A/B Pod → PostgreSQL + Artifact Store | R1 Snapshot 不漂移；Turn 2 新 Pod 重建上下文；无 sticky session | tests/acceptance/dfx/test_dfx_stateless.py + tests/acceptance/runtime/test_multipod_recovery.py / S-12 | `["bash","-lc","uv run pytest -q tests/acceptance/dfx/test_dfx_stateless.py -k s12 && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"]` | verified |
| RULE-snapshot-001 | integration | 冻结快照/终态 CAS + 原 verifier 真实边界 | 冻结与不漂移；变更只影响新 Run；CAS；`skills` 恒 1；`api_key` 剥离；原 verifier 全部通过 | 原 verifier / RULE-snapshot-001 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-12 | **无 RED（E2E 本阶段不执行 RED —— 工作流口径；且被断言的生产行为无缺口）**。为证明断言非空做了 **4 处扰动取证**（改生产代码 → 对应断言变红 → 按字节还原 → `git status --porcelain -- apps/ packages/` 为空）：P1 `_snapshot_model` 去掉 `data.pop("api_key")` → 红（`api_key` 进入 `model_json`）；P2 `_snapshot_hash` 的 model 改用未剥离版本 → 红（凭据轮换后 hash 漂移）；**P3 `_snapshot_hash` payload 去掉 `"agent"` → 首轮不变红**（我原先的"改配置"一步同时改了 `agent.instructions` 与 `model.params_json`，无法归因到每一腿）**⇒ 已加固为逐腿可归因后复跑即红**（失败文案「只改 Agent instructions 也必须改变 content_hash」）；P4 `reap_abandoned_runs` 的 `lease_until < now()` 改成永不命中 → 红（被杀 Pod 的 Run 30s 内未进终态 + reaper 用例同时红）。 | **e2e_deferred**（终验归 verify-e2e）；独立复跑：`-k s12` → **1 passed in 14.57s**，配对 `tests/acceptance/runtime/test_multipod_recovery.py` → 1 passed。 | `test_dfx_stateless.py::test_s12_stateless_pod_replacement_freezes_snapshot_and_deterministic_hash` | **真实 Runtime Pod 子进程**（uvicorn；各自独立 `POD_NAME`/端口/租约；Pod A 被**真实 `SIGKILL`**，断言 `returncode == -SIGKILL` 且进程不再存活）+ 真实 Console/Worker/LLM 探针 + 真实 PostgreSQL + 真实 artifact 根；断言全部库内回读（`run_db` 线程版，**不用 `asyncio.run`**）。事实链：① 同配置跨 Pod 的 `content_hash` **相等**（确定性）；② **真实 PG 凭据轮换**后 hash 仍相等、且轮换后的明文不出现在 `model_json`；③ **只改 `agent.instructions`** 的 Run 与再**只改 `model.params_json`** 的 Run **各自**推动 `content_hash`（逐腿可归因，且未改动的那一腿列保持不变）；④ R1 的快照行（`content_hash` + `agent_json`/`model_json`/`skill_catalog_json`/`mcp_catalog_json` + `run_id`/`snapshot_id`）在全部变更与杀 Pod 之后**逐列前后相等**；⑤ SIGKILL 后 R1 被 reaper 置 `FAILED` + `RUN_ABANDONED`；⑥ **Turn 2 在同一 `conversation_id` 上由 Pod B 完成**（无 sticky session 的直接证据）且其快照反映新配置。 | e2e_deferred |
| RULE-snapshot-001 | **无 RED（规则既有实现已满足；本任务补的是 spec 自注的验收缺位，属新增断言而非缺陷修复）**。 | verifier 独立复跑：`tests/agent_runtime/test_snapshot_freeze.py` + `tests/agent_runtime/test_run_reaper.py` → **4 passed in 0.25s**；`tests/agent_runtime -k "executor or resolve"` → **19 passed**。 | `tests/agent_runtime/test_snapshot_freeze.py::test_b104_definition_change_only_affects_new_runs`（**新增，收敛缺位**）+ 既有 `::test_b104_snapshot_hash_stable_across_key_rotation` / `::test_b104_snapshot_model_json_excludes_api_key` | 真实 PG（既有 `tenant`/`client` fixtures，落真实 `runtime.run_record`/`runtime.runtime_snapshot`）。**spec 自注的缺位已收敛**（`harness-snapshot.md` §Conventions 第 5 条：指定 verifier 只有 hash 稳定性与 `api_key` 剥离两例、缺"变更后旧 Run 仍用旧快照"）：新增用例做**两次单腿替换** —— 先只换 agent（`instructions`+`revision`）、再只换 model（`base_url`/`params`/`revision`），每次断言「新 Run 的 `content_hash` 变化且对应列反映新值、另一腿列不变」，并且**每一步之后都把旧 Run 的快照行与变更前逐列比对相等**（直接的列级冻结证据，不依赖 resume 行为的间接推断）。其余口径仍由既有用例承载：`sha256:`+canonical JSON 同口径、`skills` 恒 1、`api_key` 从 `model_json` 与 hash 输入剥离、终态/非终态写入 CAS（`test_run_reaper.py`）。 | verified（人工终态化；RULE 行不由 runner 回写） |

> 本任务**如实登记的边界**（写进 `test_dfx_stateless.py` docstring）：① Pod 是**同主机独立 uvicorn 子进程**（进程/端口/租约独立）——「跨 Pod」指独立进程实例，**不是**容器或主机级隔离；② 只覆盖 **Runtime 侧的 run 快照**；③ Prompt 文本本身不落库，故以冻结的快照列作为持久等价证据。本任务**未改任何生产代码**；对 verifier 的改动仅为**新增**一个用例（既有 2 例逐字未动）。
- S-12: e2e_deferred — automated command e2e_deferred; run_id=92889ec8baa04eba9b3c6494079ec92c (confirmed_by: runner)
- S-12: e2e_deferred — automated command e2e_deferred; run_id=f9bdffb00ff5428a8f2b194cd07f49f1 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=4b1b2bef6a034c70bb23947d256182e8 (confirmed_by: runner)

### Log
- [2026-09-28] created (draft)
- [2026-09-30] S-12 + RULE-snapshot-001 终态：新增 `tests/acceptance/dfx/test_dfx_stateless.py`（真实 Runtime **Pod 子进程 A/B** + **真实 `SIGKILL`** + 真实 Console/Worker/LLM 探针 + 真实 PG + 真实 artifact 根；断言链：同配置跨 Pod `content_hash` 相等 → 真实 PG 凭据轮换后 hash 仍相等 → **逐腿**（只改 `agent.instructions` / 只改 `model.params_json`）各自推动 hash 且另一腿列不变 → R1 快照行逐列前后相等 → SIGKILL 后 R1 被 reaper 置 `FAILED/RUN_ABANDONED` → **Turn 2 在同一 `conversation_id` 上由 Pod B 完成**（无 sticky session））。并在 verifier `tests/agent_runtime/test_snapshot_freeze.py` **新增** `test_b104_definition_change_only_affects_new_runs`，**收敛 spec 自注的验收缺位**（原 verifier 只有 hash 稳定性与 `api_key` 剥离两例，缺"配置/授权变更只影响后续新 Run"）；既有 2 例逐字未动。两组 argv 独立复跑全绿（S-12 1 passed + 兄弟 1 passed；verifier 4 passed + `-k "executor or resolve"` 19 passed）。**4 处扰动取证**：P1 剥离失效、P2 hash 含密钥、**P3 hash 去掉 agent ⇒ 首轮不变红（我的断言把两腿合并、无法归因）⇒ 加固为逐腿可归因后复跑即红**、P4 reaper 条件失效。**未改生产代码。**

---
- [2026-09-30] started
- [2026-09-30] completed (done)
## TASK-013: 上线门禁清单、收口清单与仓库级 verifier

- **Status**: verified
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

- [x] [S-13][manual] **保持 manual，需用户明确确认**：逐项核对 docs/09 §14 门禁清单，每项登记「通过记录或证据路径」；WeCom 真机项标注「需真实凭据」；**不允许以「未执行」冒充通过**。执行条件与确认人由用户给出。**已执行（2026-09-30）**：19 项逐项登记见下方「S-13 门禁核对」表（通过 11 / 部分 1 / 已执行待 E2E 终验 5 / 未执行（外部 CI） 2），用户确认并署名 jahan（`cf_acceptance_manifest.py --record-manual --scenario-id S-13 --confirmed-by jahan`）。
- [x] [RULE-test-001][E2E] 作为唯一最终负责人：跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确不得 mock 的真实边界；verifier argv：`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`。**已执行（2026-09-30 12:44:30Z→13:04:02Z，`CHAIN_EXIT=0`，见 `tests/dfx_inventory.py` 的同名在盘核对）**：`tests/acceptance` → 261 passed / 3 warnings / 1162.74s；前端 → `tsc --noEmit` + `vite build` ✓ 3437 modules / 2.88s；`e2e` → 真实 Playwright 4 passed / 2.9s。
- [x] 先写用例后建清单（结构性 RED）：`tests/dfx_inventory.py` 不存在时按登记命令执行必须先失败，再逐条补齐；核对内容包括：覆盖表每行（含 10 条 RULE 规则行）唯一负责人且全部终态、manifest 与覆盖表 id/level/boundary/owner/command/cwd 逐项一致、终态场景与规则行在 owner 的 Acceptance Evidence 中各自登记、每个任务契约表每一行全终态、done/verified 任务零未勾项、10 条 required 规则各有唯一负责人与可执行命令、E2E 命令指向真实在盘套件。**已执行**：结构性 RED 取证——文件缺失时 argv 退出码 4（`ERROR: file or directory not found: tests/dfx_inventory.py`、`no tests ran in 0.01s`）；建清单后首跑 **3 红且全部是真实缺口**（覆盖表 S-13/RULE-test-001 未终态、TASK-013 证据表残留占位行、契约表两行未终态），逐条补齐后转绿。**口径加强并已同步到文案**：不豁免收口任务自身（13-console-auth 的收窄复发点）、要求 cwd/真实边界也逐项一致、证据表不得残留占位行、RULE 行必须**恰好**等于 design 矩阵的 10 条 required 规则。
- [x] 以 mutation 验证清单有牙（改状态/删证据/伪造指向均应如期失败），并按字节还原后复跑全绿。执行 argv：`["uv","run","pytest","-q","tests/dfx_inventory.py"]`。**已执行（6 处扰动，全部如期红且消息指名条目，逐字节还原后复跑 14 passed）**：P1 覆盖表状态 `verified→planned`；P2 删 RULE-snapshot-001 证据表行；P2b 删其契约表行；P3 E2E 段 `-k s07→-k s999`（伪造用例名）；P4 删覆盖表 RULE-test-001 行；P5 manifest owner 漂移。**其中两处是扰动打出来的真实缺口**：① P2 首轮**不变红**——清单把 runner 自动写的 `- ID: <status> — …` 条目也当成证据登记，与 docstring 声明口径（只有 manual 接受条目）不符，已收紧 `_evidence_status`；② P2b 的首次误打（打到了契约表行）暴露出「契约表整行被删时无检查会发现」——行没了就没有非终态状态可查，已新增 `test_contract_tables_cover_every_acceptance_ref`。
- [x] 显式边界（不修，只登记）：门禁命令输出不得接入 `head` 一类会提前关闭的管道（SIGPIPE 打断 pytest 收尾并留下孤儿进程，导致后续随机失败）；本条 design 自注**当前无脚本约束**，只能在收口清单里做人工纪律登记，不得写成已机检。已登记于 `tests/dfx_inventory.py` 模块 docstring 的「人工纪律（无脚本约束）」段与下方 S-13 门禁核对表的执行纪律注。
- [x] 显式边界（不修，只登记）：`docs/09 §14` 中的 SCA/Mend、Image Scan 为 CI 外部工具报告，本任务登记其证据路径而不在本仓复现扫描。已在 S-13 门禁核对表第 1/2 行登记为「未执行（外部 CI）」——本仓 `.github/workflows/check.yml` 只有 backend/frontend 两个 job，`deploy/` 仅 k8s 清单，均无扫描步骤；证据路径 = 组织 CI/发布流水线报告。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。两条契约命令均已执行：S-13 为人工门禁核对（用户确认署名 jahan），RULE-test-001 为仓库级重链实跑（acceptance 261 passed + 前端 build ✓ + e2e 4 passed）；记录见下方 Evidence 两行与 Log。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-13 | manual | CI/环境全链路 | 每项门禁有通过记录或证据路径；WeCom 真机标注「需真实凭据」；未执行不得记通过 | 人工门禁核对记录 + docs/09 §14 / S-13 | - | verified |
| RULE-test-001 | E2E | 仓库级真实 E2E（HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | 跨 API/DB/Runtime/Browser 关键流程真实 E2E；分层不降级；原 verifier 全部通过 | tests/acceptance + e2e / RULE-test-001 | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-13 | **人工核对，不产自动 RED**：RED/GREEN 不适用于人工门禁核对；本任务核对的是「每项门禁有通过记录或证据路径、未执行不得记通过」，见下方「S-13 门禁核对」表的 19 行逐项登记。 | **verified（用户确认署名 jahan，2026-09-30）**：通过 11 项 / 部分 1 项（Secret Scan：测试面通过、扫描报告本仓无步骤）/ 已执行待 E2E 终验 5 项 / 未执行 2 项（SCA/Mend、Image Scan = 外部 CI）。 | TASK-013 Checklist 第 1 项 + 下方「S-13 门禁核对」表（19 行） | 核对表每行证据均为在盘事实：覆盖表/契约表/证据表、`tests/acceptance/**` 实跑记录、`.github/workflows/check.yml`（backend `make check`、frontend `npm run build`）、`deploy/k8s`、`docs/09 §14`、`uv run alembic -c migrations/alembic.ini heads` → `0014 (head)`；真机项凭据边界见 E-10 记录。 | verified |
| RULE-test-001 | 结构性 RED：`tests/dfx_inventory.py` 尚不存在时按登记 argv 执行 → `ERROR: file or directory not found: tests/dfx_inventory.py`、`no tests ran in 0.01s`、exit=4。 | 仓库级重链三段实跑 GREEN（2026-09-30 12:44:30Z→13:04:02Z，`CHAIN_EXIT=0`）：`uv run pytest -q tests/acceptance` → **261 passed, 3 warnings in 1162.74s**；`npm --prefix apps/console-platform/frontend run build` → `tsc --noEmit` + `vite build`，3437 modules / 2.88s；`npm --prefix e2e test` → **4 passed (2.9s)**（`e2e/tests/foundation.spec.ts` 的四条用例）。 | `tests/acceptance/dfx/*`（DFX 自有套件）+ `tests/dfx_inventory.py`（E2E 行与重链的在盘核对；`-k` 令牌必须命中真实用例名） | **真实 HTTP / PostgreSQL / Redis / Browser**：acceptance 段 = 真实 uvicorn 子进程栈（Console/Runtime/Worker/Gateway/LLM+MCP 探针）+ 真实 PG/Redis（共享本地实例，全程未停未重启）；build 段 = 真实前端产物；e2e 段 = 真实 Playwright + 系统 Chrome（`e2e/playwright.config.ts`）。**分层不降级**：E2E 行仍为 E2E，argv 未被改写为 unit/integration。 | verified |

**S-13 门禁核对（人工，docs/09 §14 = design §3.4.7 的 19 项；逐项登记「通过记录或证据路径」，未执行不得记通过）**

| # | 门禁项 | 登记（通过记录 / 证据路径） | 判定 |
|---|---|---|---|
| 1 | SCA/Mend | 本仓无该步骤：`.github/workflows/check.yml` 只有 backend（`make check`）与 frontend（`npm run build`）两个 job，`deploy/` 仅 k8s 清单。组织级要求见 `docs/基线-智能服务交付平台-V2.0-总体设计说明书.md` L715（「上线前通过组织规定的 SCA/Mend 流程进行漏洞和许可证检查」）。**证据路径 = 组织 CI 平台报告** | 未执行（外部 CI） |
| 2 | Image Scan | 同 #1：本仓不构建镜像、无扫描清单。**证据路径 = 发布流水线报告** | 未执行（外部 CI） |
| 3 | Secret Scan | 扫描报告同 #1（未执行）；密钥/明文外泄的测试面已通过：E-07 verified（canary 四表 + IM 出站 + bot 快照 + 三处 internal 端点门控 + `>5 MiB` 拒绝并审计），RULE-secret-001 / RULE-log-001 两条 verifier 真跑 | 部分（测试面通过；扫描报告未执行） |
| 4 | DB Migration Dry Run | `uv run alembic -c migrations/alembic.ini heads` → `0014 (head)`；CI 在全新 PG 上执行 `uv run alembic -c migrations/alembic.ini upgrade head`（`check.yml` backend job）；S-03 verified（ORM↔迁移↔OpenAPI parity） | 通过 |
| 5 | Golden Journey | S-05..S-12 七行 E2E：本地真跑 GREEN（各 TASK 的 Evidence 有 run 记录），**终验归 `/cf-task:verify-e2e`**（覆盖表现状 `e2e_deferred`） | 已执行，待 E2E 终验 |
| 6 | Egress Deny Test | E-07 verified：未命中 allowlist 拒绝（探针零请求）+ DENY 审计 + 同 URL 放行对照臂 | 通过 |
| 7 | ctx.http allowlist test | E-07 verified：命中放行 + `>5 MiB` 拒绝并落 DENY(`RESPONSE_TOO_LARGE`) 审计 | 通过 |
| 8 | Snapshot Determinism Test | S-12（`e2e_deferred`）：R1 快照行逐列前后相等 + 逐腿归因；RULE-snapshot-001 verifier（`tests/agent_runtime/test_snapshot_freeze.py`，含新增 `test_b104_definition_change_only_affects_new_runs`） | 已执行，待 E2E 终验 |
| 9 | Runtime A/B Stateless Test | S-12（`e2e_deferred`）：真实 `SIGKILL` Pod A → Pod B 在同一 `conversation_id` 完成 Turn 2 | 已执行，待 E2E 终验 |
| 10 | Runtime run lease reclaim test | E-04 verified：`SIGKILL` → Reaper 置 `FAILED/RUN_ABANDONED` + 会话释放 | 通过 |
| 11 | WeCom reconnect test | E-10 verified（**需真实凭据**，2026-09-30 真机复验、用户署名 jahan）：真实断链 → `BACKOFF` → `CONNECTED attempt=2` | 通过（manual 真机） |
| 12 | Model 429 recovery test | E-09 verified：`Retry-After` 响应头 + 指数退避 + deadline 守卫 + 取消感知 | 通过 |
| 13 | Worker lease reclaim test | E-04 verified：租约过期被其他 Worker reclaim（真实短租约 Worker + PG 权威盘面） | 通过 |
| 14 | Task deadline sweep test | E-05 verified：deadline 到期 CAS 失败终态 | 通过 |
| 15 | Schedule duplicate-fire test | S-07（`e2e_deferred`）：ONCE 到点只创建一行 Task（跨一个 Scheduler 拍点仍为 1） | 已执行，待 E2E 终验 |
| 16 | Schedule SKIP test | B-03 verified：错过触发只记 skip 不补发 | 通过 |
| 17 | Background final-delivery retry test | E-06 verified：真实退避间隔 10/20/40/80s、≤5 次、去重、`delivered=false` 不发送 | 通过 |
| 18 | Batch concurrency limit test | S-08（`e2e_deferred`）：停放列 + 非终态 Child 数不超 `max_concurrency` | 已执行，待 E2E 终验 |
| 19 | Log redaction test | E-07 + RULE-log-001 verified：logging-kit 唯一出口、双通道脱敏（审计丢键 vs 日志 `***`） | 通过 |

> **本表口径**：19 项中 11 项在本仓有已通过的真实执行记录（4/6/7/10/11/12/13/14/16/17/19），5 项属 E2E 行且本地已跑 GREEN 但终验归 `/cf-task:verify-e2e`（5/8/9/15/18），3 项为 CI 外部工具报告、本仓不复现（1/2 及其扫描报告部分 3）。**未执行项一律如实标注，不以「未执行」冒充通过**；企业微信真机项（#11）已标注「需真实凭据」并有真机复验署名。若部署侧采用分批发布，复用本表同一清单（design §3.4.7 注；本仓 `deploy/` 目前无灰度/分批编排）。
>
> **执行纪律（人工，无脚本约束）**：本次全部门禁命令均未接入 `| head` 一类会提前关闭的管道——SIGPIPE 会打断 pytest 收尾并留下共用同一测试库的孤儿服务进程（design §3.4.8）；每次运行前均已按 PPID=1 判据确认无残留进程。
- S-13: verified — S-13 人工门禁核对（docs/09 §14 共 19 项，逐项登记见 TASK-013 段「S-13 门禁核对」表）：通过 11 项（DB Migration Dry Run、Egress Deny、ctx.http allowlist、Runtime run lease reclaim、WeCom reconnect（真机复验，需真实凭据）、Model 429 recovery、Worker lease reclaim、Task deadline sweep、Schedule SKIP、Background final-delivery retry、Log redaction）；部分 1 项（Secret Scan：测试面 E-07 + 两条 verifier 通过，扫描报告本仓无步骤）；已执行待 E2E 终验 5 项（Golden Journey、Snapshot Determinism、Runtime A/B Stateless、Schedule duplicate-fire、Batch concurrency limit，终验归 /cf-task:verify-e2e）；未执行 2 项（SCA/Mend、Image Scan：本仓 CI 无扫描步骤，证据路径=组织 CI/发布流水线报告）。未执行项一律如实标注，不以「未执行」冒充通过。 (confirmed_by: jahan)

### Log
- [2026-09-28] created (draft)
- [2026-09-30] started
- [2026-09-30] **收口：新增 `tests/dfx_inventory.py`（14 项检查）＋ `RULE-test-001` 重链实跑 ＋ `S-13` 人工门禁核对（用户署名 jahan）**。① **结构性 RED 取证**：清单文件缺失时按登记 argv 执行 → `ERROR: file or directory not found`、`no tests ran in 0.01s`、exit=4。② 建清单后首跑 **3 红且全部是真实缺口**（覆盖表 S-13/RULE-test-001 未终态、TASK-013 证据表残留占位行、契约表两行未终态），逐条补齐后转绿。③ **6 处扰动取证**（改覆盖表状态 / 删规则证据表行 / 删规则契约表行 / 伪造 E2E `-k` 用例名 / 删覆盖表规则行 / 改 manifest owner）：均如期红且消息指名条目，逐字节还原（`filecmp` 校验）后复跑 **14 passed**。**扰动打出两个真实缺口**——(a) 「删规则证据表行」首轮**不变红**：清单把 runner 自动写的 `- ID: <status> — …` 条目也当作证据登记，与 docstring 声明口径不符 ⇒ 收紧 `_evidence_status`（非 manual 行必须有同 ID 证据表行）；(b) 该扰动首次误打到契约表行时暴露出「契约表整行被删无人发现」（行没了就没有非终态状态可查）⇒ 新增 `test_contract_tables_cover_every_acceptance_ref`。④ **口径比 11/12/13 更严**：不豁免收口任务自身（13-console-auth 的收窄复发点）、manifest 比对加上 cwd 与「真实边界」列、证据表禁占位行、RULE 行必须**恰好**等于 design `Spec Compliance Matrix` 的 10 条 required 规则（`spec-context.yml` 继承的 16 条是其超集，多出的 front/i18n/im/platform/time/ui 6 条不在 design 矩阵内、由各自域需求负责，不建 RULE 行）。⑤ **`RULE-test-001` 仓库级重链实跑 GREEN**（12:44:30Z→13:04:02Z，`CHAIN_EXIT=0`）：`tests/acceptance` 261 passed / 3 warnings / 1162.74s；前端 `tsc --noEmit` + `vite build` ✓ 3437 modules / 2.88s；`npm --prefix e2e test` → 真实 Playwright 4 passed / 2.9s。⑥ **S-13 人工门禁核对**：docs/09 §14 共 19 项逐项登记（通过 11 / 部分 1（Secret Scan 扫描报告本仓无步骤）/ 已执行待 E2E 终验 5 / 未执行 2（SCA/Mend、Image Scan = 外部 CI）），用户确认署名 jahan（`--record-manual --scenario-id S-13 --confirmed-by jahan`）；未执行项如实标注，不以「未执行」冒充通过。⑦ **登记边界**：`cf_acceptance_manifest.py --verify-plan` 对 6 条 path-mapped 继承规则报 `pending` + `plan_owner_missing`（与 TASK-001..012 同态；`cf_task_workflow.py finish` 不跑 verify-plan、`cf_spec_gate.py --stage code` 对本任务判 **pass**）——归档阶段若被 plan 门禁拦下，需按「N/A 逐项用户确认」或「局部 Plan 承接」另行处理，本任务不代签。
- [2026-09-30] completed (done)
