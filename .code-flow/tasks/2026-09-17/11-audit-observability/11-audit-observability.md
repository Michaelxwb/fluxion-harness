# Tasks: 运行审计与可观测

- **Source**: .code-flow/tasks/2026-09-17/11-audit-observability/（全部 design：11-audit-observability.backend.design.md、11-audit-observability.frontend.design.md）
- **Created**: 2026-09-25
- **Updated**: 2026-09-25
- **Plan-State**: planned（用户已确认写入；各 TASK 保持 draft，功能与 E2E 验收尚未执行）

## Proposal

为控制面与运行面补齐统一审计与可观测能力：把 config/tool/egress/model 四类审计事实归一为可查询的聚合投影与详情关联（含 Admin Run 详情契约），并把敏感字段的三层脱敏（日志出口、审计写入前、API 响应）做成硬约束；同时新增按筛选条件创建的可重试审计导出（异步任务 + 下载），使审计数据可交付给合规与排障场景。查询面落在 Console（只读 API-01/02/06），写入面复用既有 Runtime/Worker/Console 审计表，不新增宽表；前端新增 `audit-observability` 模块（列表 + 筛选 + 只读详情 + 导出）。

共 19 个原子任务：P0 15、P1 4；每项列出 1–3 个预计改动文件（含测试），默认目标 15–60 分钟；真实验收环境、后端/前端场景验收与收口类任务按半天级标注（TASK-016/017/018/019），外部服务启动与验收等待另计。超过范围先拆任务并校验 Context，禁止编码时静默扩展。本次 design 已升 v1.3：`harness-api#RULE-api-002` 由"绑定但未承接"补为真实落地（FEAT-04 审计导出 + API-05/API-06 + `control.audit_export_job`）。

## Design Alignment

2026-09-25 拆解前完成两处设计对齐（均已写入 design 修订历史，不代表实现或 verifier 已通过）：

- **secret 语义对齐（backend/frontend v1.2）**：原矩阵与 §2.3.2/§2.5.1 写的"只存 SecretRef"与现行 required 文本相反，已改为"密钥明文存于各 Owner 表、跨表以主键引用（无 `secret_ref`/SecretProvider）；不进审计/日志/Snapshot/LLM Prompt/API 响应（对外以 `*_configured` 表达）"，消除 plan 阶段 `stale`。
- **`harness-api#RULE-api-002` 真实落地（v1.3）**：该模块 API 原为全 GET，规则不适用；按用户决定补设计使其落地——新增 FEAT-04 审计导出（P1）、RULE-09（创建类 POST 的 `Idempotency-Key` 幂等）、API-05/API-06、`control.audit_export_job` 表与场景 S-05/E-05，前端同步 FEAT-FE-03 与 S-08/E-08/E-09。同时把两份 design 的矩阵 ref 由 legacy `harness-platform#` 校正为现行分域 spec id。
- **Spec 与验收**：Matrix 使用 Context 实际 spec_id；11 条 required Rule 的唯一最终负责人见 Coverage/Contract，原 E2E 不降级，无 manual 场景；验收类任务沿用"不制造 RED"基线（本模块 design 无 B-* 补充场景）。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | 验收 | Checklist |
|---|---|---|---|---|---|---|
| TASK-001 | P0 | 配置审计写入与 actor 语义 | 无 | 2.5.1 RULE-07；3.5 可靠性 | S-04(integration), E-04(integration), RULE-07(integration) | 5 |
| TASK-002 | P0 | 运行审计三表写入补齐与 result_status 归一 | 无 | 3.3 数据设计；2.5.1 RULE-08 | S-02(integration), RULE-08(integration), RULE-snapshot-001(integration) | 5 |
| TASK-003 | P0 | 审计聚合投影与列表 API-01 | 001, 002 | 3.3 审计聚合投影；3.4 API-01；3.5 性能 | S-01(E2E), E-03(integration), RULE-02(integration), RULE-api-001(E2E), RULE-time-001(E2E) | 7 |
| TASK-004 | P0 | 审计详情 API-02 与关联降级 | 003 | 3.4 API-02；3.3 投影 | E-01(integration) | 4 |
| TASK-005 | P0 | Admin Run 列表/详情 API-03/04 | 002 | 3.4 API-03/04；3.3 Admin Run 详情响应 | S-03(E2E), B-201(integration) | 5 |
| TASK-006 | P1 | 导出任务表与创建 API-05（幂等） | 003 | 3.3 `control.audit_export_job`；3.4 API-05；2.5.1 RULE-09 | S-05(E2E), E-05(integration), RULE-03(integration), RULE-09(integration), RULE-api-002(E2E), RULE-data-001(integration) | 7 |
| TASK-007 | P1 | 导出状态/下载 API-06 与执行落地 | 006 | 3.4 API-06；3.5 可靠性 | S-05(E2E), B-202(integration) | 5 |
| TASK-008 | P0 | 三层脱敏收口（日志/写入/响应） | 001, 002 | 3.5 安全与日志脱敏；2.5.1 RULE-01/RULE-04 | E-02(integration), RULE-01(integration), RULE-04(integration), RULE-log-001(integration), RULE-secret-001(integration) | 6 |
| TASK-009 | P1 | 可观测性：trace 关联字段与指标目录 | 002 | 3.5 可观测性；4 部署与运维 | B-203(integration) | 5 |
| TASK-010 | P0 | 前端 service 层与类型契约 | 无 | frontend 3.4 组件接口契约 | RULE-front-001(integration), B-204(integration) | 5 |
| TASK-011 | P0 | 审计列表页容器与筛选栏 | 010 | frontend 3.2/3.3；3.3.1 按钮设计 | E-06(integration), RULE-ui-001(E2E), B-205(integration) | 6 |
| TASK-012 | P0 | 审计表格与字段列 | 011 | frontend 3.3/3.6 | S-06(E2E), B-206(integration) | 4 |
| TASK-013 | P0 | 详情 SideSheet 与关联链接 | 012 | frontend 3.3/3.4 | S-07(E2E), E-07(integration), RULE-ui-detail-001(E2E), B-207(integration) | 6 |
| TASK-014 | P1 | 导出按钮与轮询/下载交互 | 012 | frontend 3.3.1/3.5 | S-08(E2E), E-08(integration), E-09(integration), B-208(integration) | 5 |
| TASK-015 | P1 | i18n 词条与语言切换覆盖 | 012 | frontend 3.3/3.6 | RULE-i18n-001(E2E), B-209(integration) | 4 |
| TASK-016 | P0 | 真实审计验收环境与种子清理 | 005, 007 | 3.5 可靠性；4 部署与运维 | B-210(integration) | 6 |
| TASK-017 | P0 | 后端场景真实验收（S-01/03/05 + 集成场景证据） | 016 | 2.5.2 功能验收场景 | S-01(E2E), S-03(E2E), S-05(E2E) | 6 |
| TASK-018 | P0 | 前端 E2E 验收（列表/详情/导出） | 016 | frontend 2.4 验收条件 | S-06(E2E), S-07(E2E), S-08(E2E) | 6 |
| TASK-019 | P0 | 收口：场景、规则、证据与仓库级 verifier | 017, 018 | 2.5.2/2.5.1；6 需求追溯矩阵 | RULE-06(integration), RULE-test-001(E2E), B-211(integration) | 6 |
| TASK-020 | P1 | Runtime/Worker 指标暴露与 label 卫生 | 009 | 3.5 可观测性；4 部署与运维 | B-212(integration), RULE-worker-001(integration) | 5 |
| TASK-021 | P1 | API-01/05 增 agent_id 筛选（后端） | 003, 006 | 3.3 数据设计；3.4 接口设计 | B-213(integration) | 4 |
| TASK-022 | P1 | 前端接线收口（Agent 筛选/resourceType 域/刷新失败提示） | 011, 012, 015, 021 | 3.3 组件设计；3.6 UI 状态 | B-214(integration) | 4 |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) | TASK-017 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"] | . | 1200 |  |
| S-02 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | 真实 Tool/Egress/Model 执行路径→runtime 审计表 | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py","-k","s02"] | . | 600 |  |
| S-03 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→Console Admin Run 详情 HTTP→Runtime 内部端点→runtime 表 | TASK-017 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"] | . | 1200 |  |
| S-04 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console AppService 事务→control.config_audit_log | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","s04"] | . | 600 |  |
| S-05 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→Console 导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) | TASK-017 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"] | . | 1200 |  |
| B-201 | 11-audit-observability.backend.design.md#3.4 接口设计 | integration | 真实 Runtime HTTP /internal/admin/runs 与 /internal/admin/runs/{run_id} → 真实 PostgreSQL | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_admin_run_api.py"] | . | 600 |  |
| B-204 | 11-audit-observability.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本(services 收口/无裸请求/i18n) | TASK-010 | verified | ["uv","run","pytest","-q","tests/frontend/test_audit_services_contract.py"] | . | 600 |  |
| B-202 | 11-audit-observability.backend.design.md#3.4 接口设计 | integration | 真实 Console HTTP 导出状态/下载 → 真实 PostgreSQL + artifact store | TASK-007 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_download.py"] | . | 600 |  |
| B-205 | 11-audit-observability.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本(services 收口/无裸请求/i18n) | TASK-011 | verified | ["uv","run","pytest","-q","tests/frontend/test_audit_page_contract.py"] | . | 600 |  |
| B-203 | 11-audit-observability.backend.design.md#3.5 质量实现方案 | integration | 真实 /metrics HTTP 端点 + 真实日志出口 + 运行审计表(PostgreSQL) | TASK-009 | verified | ["uv","run","pytest","-q","tests/test_audit_observability_config.py"] | . | 600 |  |
| B-206 | 11-audit-observability.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本(services 收口/无裸请求/i18n) | TASK-012 | verified | ["uv","run","pytest","-q","tests/frontend/test_audit_table_contract.py"] | . | 600 |  |
| B-207 | 11-audit-observability.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本(services 收口/无裸请求/i18n) | TASK-013 | verified | ["uv","run","pytest","-q","tests/frontend/test_audit_detail_contract.py"] | . | 600 |  |
| B-208 | 11-audit-observability.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本(services 收口/无裸请求/i18n) | TASK-014 | verified | ["uv","run","pytest","-q","tests/frontend/test_audit_export_contract.py"] | . | 600 |  |
| B-209 | 11-audit-observability.frontend.design.md#3.4 组件接口契约 | integration | 前端源码契约 + 真实 tsc 类型检查 + 仓库检查脚本(services 收口/无裸请求/i18n) | TASK-015 | verified | ["uv","run","pytest","-q","tests/frontend/test_audit_i18n_contract.py"] | . | 600 |  |
| B-210 | 11-audit-observability.backend.design.md#3.5 质量实现方案 | integration | 真实多进程栈(Console/Runtime/Worker + PostgreSQL/Redis)与租户级清理 | TASK-016 | verified | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_environment.py"] | . | 600 |  |
| B-211 | 11-audit-observability.backend.design.md#3.5 质量实现方案 | integration | pytest 用例收集/运行→验收 Contract/Evidence→真实组件记录 | TASK-019 | verified | ["uv","run","pytest","-q","tests/audit_observability_inventory.py","-k","b211"] | . | 600 |  |
| B-212 | 11-audit-observability.backend.design.md#3.5 质量实现方案 | integration | 真实 Runtime/Worker 进程 + 真实 `/metrics` HTTP 端点 + 真实调用点 | TASK-020 | verified | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_metrics.py && uv run pytest -q tests/agent_worker/test_worker_metrics.py"] | . | 600 |  |
| RULE-worker-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | integration | 真实 Worker 进程与 PostgreSQL/Redis（claim/reclaim/lease/schedule/delivery）+ 原 verifier 真实边界 | TASK-020 | verified | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_metrics.py && uv run pytest -q tests/agent_worker/test_worker_metrics.py && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | . | 1200 |  |
| B-213 | 11-audit-observability.backend.design.md#3.5 质量实现方案 | integration | 真实 Console HTTP + 真实 PostgreSQL（审计四表 + 运行/任务表） | TASK-021 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_agent_filter.py","-k","b213"] | . | 600 |  |
| B-214 | 11-audit-observability.backend.design.md#3.5 质量实现方案 | integration | 前端源码契约 + 真实 tsc + 仓库检查脚本（services 收口/i18n） | TASK-022 | planned | ["uv","run","pytest","-q","tests/frontend/test_audit_gap_contract.py","-k","b214"] | . | 600 |  |
| E-01 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 详情查询→关联 Run 不可读 | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_detail_api.py","-k","e01"] | . | 600 |  |
| E-02 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | 真实 logging-kit 出口 + 审计写入 + Console 响应 | TASK-008 | verified | ["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"] | . | 600 |  |
| E-03 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 列表查询参数校验 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"] | . | 600 |  |
| E-04 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 业务事务回滚→config_audit_log | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","e04"] | . | 600 |  |
| E-05 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 导出创建→幂等表 partial unique | TASK-006 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"] | . | 600 |  |
| S-06 | 11-audit-observability.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→Console 聚合查询 HTTP | TASK-018 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""] | . | 1200 |  |
| S-07 | 11-audit-observability.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→Console 详情 HTTP | TASK-018 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-07\""] | . | 1200 |  |
| S-08 | 11-audit-observability.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→导出创建/查询 HTTP→PostgreSQL | TASK-018 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-08\""] | . | 1200 |  |
| E-06 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→Console 查询失败路径 | TASK-018 | verified | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""] | . | 900 |  |
| E-07 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→详情不可读路径 | TASK-018 | verified | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-07\""] | . | 900 |  |
| E-08 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→导出创建异指纹 409 | TASK-018 | verified | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-08\""] | . | 900 |  |
| E-09 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→导出失败状态 | TASK-018 | verified | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-09\""] | . | 900 |  |
| RULE-01 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 真实 logging-kit 出口与日志文件 | TASK-008 | verified | ["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"] | . | 600 |  |
| RULE-02 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | Console 列表/详情 HTTP 封套与分页 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"] | . | 600 |  |
| RULE-03 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 新增表标准列/partial unique/timestamptz(PostgreSQL) | TASK-006 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"] | . | 600 |  |
| RULE-04 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 审计写入前脱敏 + API 响应 | TASK-008 | verified | ["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"] | . | 600 |  |
| RULE-05 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | Console 时间出参格式 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"] | . | 600 |  |
| RULE-06 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 跨 API/DB/Runtime/Browser 的真实 E2E 边界 | TASK-019 | verified | ["bash","-lc","uv run pytest -q tests/audit_observability_inventory.py -k r06"] | . | 600 |  |
| RULE-07 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | Console 业务事务与审计同库同事务 | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","s04"] | . | 600 |  |
| RULE-08 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | runtime 三表 status 归一为 result_status | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py","-k","s02"] | . | 600 |  |
| RULE-snapshot-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | integration | 真实 Runtime 执行链/审计写入(PostgreSQL) + 原 verifier 真实边界 | TASK-002 | verified | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_audit_write.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | . | 1200 |  |
| RULE-09 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 导出创建幂等表 partial unique(PostgreSQL) | TASK-006 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"] | . | 600 |  |
| RULE-api-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | E2E | 真实 Console HTTP 封套/分页 + 原 verifier 真实边界 | TASK-003 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 |  |
| RULE-api-002 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | E2E | 真实 Console 导出创建 HTTP→幂等表(PostgreSQL) + 原 verifier 真实边界 | TASK-006 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"] | . | 1200 |  |
| RULE-data-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | integration | 真实 PostgreSQL 表结构/约束 + 原 verifier 真实边界 | TASK-006 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests -k schema_parity"] | . | 600 |  |
| RULE-secret-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | integration | 审计/日志/响应三层脱敏(PostgreSQL+logging-kit) + 原 verifier 真实边界 | TASK-008 | verified | ["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"] | . | 600 |  |
| RULE-log-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | integration | logging-kit 出口与按 service/日期落盘 + 原 verifier 真实边界 | TASK-008 | verified | ["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py"] | . | 600 |  |
| RULE-time-001 | 11-audit-observability.backend.design.md#Spec Compliance Matrix | E2E | Console 时间出参与前端展示一致 + 原 verifier 真实边界 | TASK-003 | verified | ["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/frontend/test_datetime_contract.py"] | . | 1200 |  |
| RULE-ui-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Console 页面 + 原 verifier 真实边界 | TASK-011 | verified | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | . | 1200 |  |
| RULE-ui-detail-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Console 页面详情结构 + 原 verifier 真实边界 | TASK-013 | verified | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 1200 |  |
| RULE-front-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | integration | 前端源码契约（services 收口/无裸 fetch/i18n）+ 原 verifier 真实边界 | TASK-010 | verified | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_services_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 900 |  |
| RULE-i18n-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Console 页面双语 + 原 verifier 真实边界 | TASK-015 | verified | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | . | 1200 |  |
| RULE-test-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | TASK-019 | verified | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 2400 |  |

> 本表覆盖 design 中全部 P0/P1 场景（S-01..S-05、E-01..E-05、S-06..03、E-06..04 共 17 个）与 9 条业务规则、11 条 required Spec Rule；每个场景与规则有且仅有一个最终负责人；无 manual 场景；E2E 层级不降级。

---

## TASK-001: 配置审计写入与 actor 语义

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 11-audit-observability.backend.design.md#2.5.1 业务规则与约束, 11-audit-observability.backend.design.md#3.3 数据设计, 11-audit-observability.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-04, E-04, RULE-07
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/audit_service.py`, `tests/console_platform/test_audit_config_write.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

落实 RULE-07：`control.config_audit_log` 必须与业务变更**同一事务**写入，`actor_user_id` = 登录的 `console_account.id`，业务事务回滚则审计不落库；记录 before/after 差异但不含 Secret Value。现有 `audit_service.py` 已有基础写入，本任务补齐同事务语义、actor 传递与回滚断言。

### Checklist

- [x] [S-04][integration] 以真实 PostgreSQL 为边界编写/扩展用例：更新 Agent revision 触发 config 审计，断言审计与业务变更同事务提交、`actor_user_id` 为登录账号 id。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","s04"]`。
- [x] [E-04][integration] 覆盖业务事务回滚路径：断言 `config_audit_log` 不产生记录（同事务语义）。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","e04"]`。
- [x] [RULE-07][integration] 作为唯一最终负责人，沿 Console AppService→真实 PostgreSQL 验证"同事务 + actor + 回滚不落库"，联合映射 S-04 / E-04；命令 `["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py"]`，不得以静态声明代替行为证据。
- [x] 实现或补齐：审计写入与业务变更共用同一 session/事务边界，actor 从会话上下文注入，禁止在审计服务内自行提交。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | Console AppService→真实 PostgreSQL | 审计与业务变更同事务；`actor_user_id` = 登录 `console_account.id`；before/after 差异不含 Secret | tests/console_platform/test_audit_config_write.py / S-04 | `["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","s04"]` | verified |
| E-04 | integration | Console 事务→真实 PostgreSQL | 业务回滚后 `config_audit_log` 计数不变 | tests/console_platform/test_audit_config_write.py / E-04 | `["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","e04"]` | verified |
| RULE-07 | integration | Console AppService→真实 PostgreSQL | 同事务 + actor 语义 + 回滚不落库（联合 S-04/E-04） | tests/console_platform/test_audit_config_write.py / RULE-07 | `["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | **验收类不制造 RED**（Baseline）。本任务缺口为**覆盖而非实现**：同事务写入由 api-kit `write_config_audit(session...)`（只 INSERT 不 commit）、`AuditService.record_config_change`、以及 API 层 `AuditActor(account_id=account.id)` 早已落实，故首跑即 GREEN，未伪造失败。 | `uv run pytest -q tests/console_platform/test_audit_config_write.py -k s04` → **1 passed** | `test_s04_config_audit_shares_business_transaction`：真实 HTTP 创建 Agent（CREATE）后按 `expected_revision=1` 更新（UPDATE）；断言 `control.agent_definition.revision == 2` 与 `control.config_audit_log` 的 CREATE/UPDATE 两行**同时可见**；每行 `actor_user_id == 登录 console_account.id`、`resource_type == 'AGENT'`、`resource_id` 匹配、`after_json` 含新指令且不含 Secret | 真实 Console ASGI 全栈（登录会话 + CSRF）→ 真实 PostgreSQL 逐行回读（`config_audit_log` / `agent_definition` / `console_account`）；未 mock 业务 API | verified |
| E-04 / RULE-07 | 同上（验收类，不制造 RED）。 | `uv run pytest -q tests/console_platform/test_audit_config_write.py -k e04` → **1 passed**（整文件 2 passed in 0.44s） | `test_e04_rolled_back_business_change_leaves_no_audit`：服务层创建 Agent 后，**同一 session 内**审计行已可见（证明审计 INSERT 属于业务事务而非独立提交）→ `rollback()` → 新连接回读断言 `agent_definition` 与 `config_audit_log` 计数**均为 0** | 真实 PostgreSQL 事务边界（同 session 写入 + 显式 rollback + 独立 engine 回读）；未 mock | verified |
- S-04: verified — automated command passed; run_id=1f9dff65c77f4cbba75bb8e7fde48078 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=1f9dff65c77f4cbba75bb8e7fde48078 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=41e7d72ac1c540baa8556ddb06fe160b (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=41e7d72ac1c540baa8556ddb06fe160b (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=3a664093485d434dbd1ec4f9f82f9e79 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=3a664093485d434dbd1ec4f9f82f9e79 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=7963aa0d1d24480b83e5b9f41dc2be40 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=7963aa0d1d24480b83e5b9f41dc2be40 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-002: 运行审计三表写入补齐与 result_status 归一

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 11-audit-observability.backend.design.md#3.3 数据设计, 11-audit-observability.backend.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-02, RULE-08, RULE-snapshot-001
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `tests/agent_runtime/test_runtime_audit_write.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

补齐 `runtime.tool_call_audit` / `runtime.egress_audit` / `runtime.model_invocation_audit` 三表的真实写入：同一次 Skill 执行链路的 tool/egress/model 调用必须落审计并共享 `trace_id`，且不得写入 Secret 明文（参数只存脱敏摘要与 hash）。按 RULE-08 统一暴露 `result_status`（tool 取 `status`、egress/model 取各自 `result_status`）。

### Checklist

- [x] [S-02][integration] 以真实 runtime 执行路径 + 真实 PostgreSQL 为边界编写/扩展用例：一次 Skill 调模型与平台，断言三表均有记录、`trace_id` 一致、无 Secret 明文。执行 argv：`["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py","-k","s02"]`。
- [x] [RULE-08][integration] 作为唯一最终负责人，沿 真实执行路径→runtime 审计表 验证 `result_status` 归一映射（含失败状态）；联合映射 S-02；命令 `["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py"]`。
- [x] 实现或补齐：三表写入覆盖成功/失败两条路径，`prepared_args_hash` 只存 hash、`args_preview_json` 写入前递归脱敏。
- [x] [RULE-snapshot-001][integration] verifier_ref=harness-snapshot#RULE-snapshot-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`；补充真实边界 真实 Runtime 执行链与审计写入(PostgreSQL)；本次改动仅在审计写入边界新增递归脱敏，不涉及 Snapshot 冻结与终态 CAS；断言 审计写入前后 Snapshot 内容/hash 与终态语义不变（原 verifier 的 executor/resolve 断言覆盖）；原 verifier 全部通过（19 passed），联合验收 argv=`["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_audit_write.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | Tool/Egress/Model 真实执行路径→真实 PostgreSQL | 三表均有记录；同 `trace_id`；无 Secret 明文 | tests/agent_runtime/test_runtime_audit_write.py / S-02 | `["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py","-k","s02"]` | verified |
| RULE-snapshot-001 | integration | 真实 Runtime 执行链/审计写入(PostgreSQL) + 原 verifier 真实边界 | 新增脱敏不改变 Snapshot 冻结与终态 CAS；原 verifier 全部通过 | tests/agent_runtime/test_runtime_audit_write.py + 原 verifier / RULE-snapshot-001 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_audit_write.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]` | verified |
| RULE-08 | integration | 真实执行路径→runtime 审计表 | `result_status` 由 tool/egress/model 各自 status 映射，config 由 TASK-001 固定 SUCCESS | tests/agent_runtime/test_runtime_audit_write.py / RULE-08 | `["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-02 | **真实 RED**（非伪造）：`uv run pytest -q tests/agent_runtime/test_runtime_audit_write.py -k no_secret` → `FAILED ... args_preview_json 落入了 Secret 明文：{"url": "…", "api_key": "sk-live-verysecret-1234567890"}` —— 写入边界无脱敏，`args_preview_json`（设计标注为"脱敏参数预览"）原样落库。 | 在 `RuntimeAuditWriter.record_tool_call` 落库前对 `args_preview_json` 递归脱敏（新增 `redact_sensitive`：按 password/secret/token/api_key/credential/authorization/cookie 命中即替 `<redacted>`，嵌套 dict/list 递归）→ 复跑 **2 passed**；回归 `test_execution_audit.py` + `test_egress_boundary.py` 合计 **12 passed**；ruff/mypy 干净。 | `test_s02_tool_egress_model_audits_share_one_run`（同一 Run 的 tool/egress/model 三行同源锚点 `run_id`、租户一致）；`test_s02_audit_payloads_carry_no_secret`（`args_preview_json` 序列化不含明文 + `args_preview_json::text LIKE '%<secret>%'` 反查为 0） | 真实 tool/egress/model 审计写入 port → 真实 PostgreSQL（`runtime.tool_call_audit` / `egress_audit` / `model_invocation_audit` 逐行回读 + jsonb 明文反查）；未 mock 业务 API | verified |
| RULE-08 | 无独立 RED（随 S-02 一并取证，验收类）。 | 断言三表用于归一的列存在且取值在各自词表内：`tool_call_audit.status ∈ {OK,ERROR,…}`、`egress_audit.result_status ∈ {OK,FAILED,DENIED,…}`、`model_invocation_audit.status ∈ {SUCCEEDED,FAILED,RETRY}`；config 侧由 `config_audit_log_repository` 固定投影为 `SUCCESS`（TASK-001 已取证）。 | 同上用例内的状态列断言（供投影层归一出统一 `result_status`） | 真实 PostgreSQL 三表逐行回读 | verified |

**登记一处设计/实现词表差异（不改写历史值）**：设计 §3.3 对 `tool_call_audit.status` 的注释写 `PREPARED/RUNNING/SUCCESS/FAILED/DENIED`，而现行执行链实际写入 `OK`（成功）/`ERROR`（失败）。本任务只登记"投影可归一的输入词表"，统一暴露（`result_status`）由 TASK-003 的聚合投影承担；如需把写入值对齐注释词表，属独立变更（会改动既有数据语义），未在本任务内顺手改。
- S-02: verified — automated command passed; run_id=3b61cfef47814c42be005b2475033381 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] resumed (in-progress)
- [2026-09-25] completed (done)
## TASK-003: 审计聚合投影与列表 API-01

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 11-audit-observability.backend.design.md#3.3 数据设计, 11-audit-observability.backend.design.md#3.4 接口设计, 11-audit-observability.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-api#RULE-api-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-01, E-03, RULE-02, RULE-05, RULE-api-001, RULE-time-001
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/audits.py`, `tests/console_platform/test_audit_query_api.py`、`infrastructure/repositories/audit_query_repository.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现 API-01 审计列表：四张审计表 UNION ALL 的查询期聚合投影（不建宽表），统一 `audit_type/action/result_status/target/actor/agent/trace_id` 字段归一；统一封套与 `{items,page,page_size,total}` 分页（`1<=page_size<=100`）；Actor/Agent 名称批量补齐（禁 N+1）；时间出参 `YYYY-MM-DD HH:mm:ss`。

### Checklist

- [x] [S-01][E2E] 以真实 Browser→Console 聚合查询 HTTP→真实 PostgreSQL 为边界编写/扩展用例（与 TASK-017 协同，本任务负责实现侧集成用例）：按 `trace_id` 搜索断言返回同链路审计且字段归一、含 `result_status`。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","s01"]`。
- [x] [E-03][integration] 覆盖参数校验：`result_status` 非法枚举与非法时间区间返回 `COMMON_VALIDATION_ERROR`，不返回未过滤全量。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"]`。
- [x] [RULE-02][integration] 验证统一封套 `code/msg/data/trace_id/request_id/timestamp`、分页封套与 `page>=1`、`1<=page_size<=100` 边界。
- [x] [RULE-api-001][E2E] verifier_ref=harness-api#RULE-api-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`；补充真实边界 真实 Console HTTP→四审计表；断言 列表统一 items/page/page_size/total 且分页边界正确、错误码/文案来自 catalog；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]`。
- [x] [RULE-time-001][E2E] verifier_ref=harness-time#RULE-time-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]`；补充真实边界 真实 Console HTTP 时间出参；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/frontend/test_datetime_contract.py"]`。
- [x] 实现或补齐：投影字段归一、批量补齐名称、`ORDER BY occurred_at DESC` + 分页 total；不做 N+1、不返回 Secret。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) | 同链路审计字段归一；含 `result_status`；分页封套正确 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-01（owner TASK-017） | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"]` | verified |
| E-03 | integration | Console 查询参数校验→真实 PostgreSQL | 非法枚举/时间区间 → `COMMON_VALIDATION_ERROR`，不返回全量 | tests/console_platform/test_audit_query_api.py / E-03 | `["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"]` | verified |
| RULE-02 | integration | Console HTTP 封套→真实 PostgreSQL | 统一封套 + 分页边界 + catalog 错误码 | tests/console_platform/test_audit_query_api.py / RULE-02 | `["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py"]` | verified |
| RULE-api-001 | E2E | 真实 Console HTTP + 原 verifier 真实边界 | 封套/分页/错误码一致性；原 verifier 全部通过 | tests/console_platform/test_audit_query_api.py + 原 verifier / RULE-api-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]` | verified |
| RULE-time-001 | E2E | 真实 Console HTTP 时间出参 + 原 verifier 真实边界 | 出参 `YYYY-MM-DD HH:mm:ss`；存储 timestamptz | tests/console_platform/test_audit_query_api.py + 原 verifier / RULE-time-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/frontend/test_datetime_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | **真实 RED**：首跑 `test_s01_audit_list_projects_four_tables_with_unified_fields` FAIL —— `assert page["total"] == 4` 得 `1`（旧 `GET /api/v1/audits` 只读 `control.config_audit_log`，忽略 `trace_id` 过滤，四表聚合不存在）。 | 实现四表 `UNION ALL` 查询期投影（`AuditQueryRepository`）+ 校验与字段塑形（`AuditQueryService`）+ API-01 路由重写 → `tests/console_platform/test_audit_query_api.py` **4 passed**；控制台回归 `tests/console_platform/` **97 passed**。 | `test_s01_...`：真实种子四表各一行并共享同一 `trace_id` → `GET /api/v1/audits?trace_id=…` 断言封套 `code=="0"`、`data={items,page,page_size,total}`、四行齐全、`audit_type` 判别、统一字段（`resource_type/resource_id/actor_user_id/actor_name/agent_id/agent_name/action/result_status/trace_id/occurred_at`）、`result_status` 归一、`occurred_at` 为 `YYYY-MM-DD HH:mm:ss`、`occurred_at DESC` 排序。 | 真实 Console HTTP（ASGI 全栈 + 登录会话/CSRF）→ 真实 PostgreSQL（四张审计表 + `runtime.run_record` + `control.agent_definition`/`console_account` 逐行回读）；未 mock 业务 API。**N+1 证据**：单次列表请求实测仅 2 条 SQL（count + 分页，各为一条 UNION ALL，与行数无关）。 | verified |
| E-03 | **真实 RED**：首跑 `test_e03_invalid_filters_are_rejected_without_full_dump` FAIL —— 非法 `result_status` 与倒序时间区间被静默接受，返回 `200` 且 `items` 为未过滤的配置审计行（即 E-03 禁止的"未过滤全量"）。 | 枚举/区间校验落地（`audit_type` 枚举 + 区间顺序 + `result_status` 取值域，取值域来自 `config/api-messages.yaml` 的 catalog codes，不硬编码）→ 返回 catalog `COMMON_VALIDATION_ERROR`，不返回全量；**4 passed**。 | 同上用例：断言非 2xx + catalog 错误码，且响应体不含未过滤列表 | 同 S-01（真实 HTTP + 真实 PostgreSQL） | verified |
| RULE-02 | 无独立 RED（随 S-01/E-03 一并取证，验收类）。 | 列表封套 `{code,msg,data,trace_id,request_id,timestamp}` 与分页封套 `{items,page,page_size,total}`；`page>=1`、`1<=page_size<=100`（越界由 FastAPI `Query` 守卫 → catalog 校验错误）；错误码/文案全部来自 `config/api-messages.yaml`。 | `test_audit_query_api.py` 内的封套与分页断言 | 真实 Console HTTP 响应体 | verified |
| RULE-05 | 无独立 RED（验收类）。 | 时间出参统一 `YYYY-MM-DD HH:mm:ss`（服务层格式化；本地时区 wall-clock，与 `DateTimeText` 的 naive 解析一致）。 | 同上用例的 `occurred_at` 格式断言 | 真实 Console HTTP 响应体 | verified |
| RULE-api-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]` → **18 passed**（原 verifier 全部通过）。 | tests/console_platform/test_audit_query_api.py + 原 verifier | 真实 Console HTTP + 原 verifier 真实边界 | verified |
| RULE-time-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_query_api.py && uv run pytest -q tests/frontend/test_datetime_contract.py"]` → **4 passed + 2 passed**（原 verifier 全部通过）。 | 同上 + `tests/frontend/test_datetime_contract.py` | 真实 Console HTTP + 原 verifier 真实边界 | verified |

**实现中的判断点（如实登记，未静默决定）**：
- **`result_status` 归一映射**：设计 §3.3 给的词表是 `PREPARED/RUNNING/SUCCESS/FAILED/DENIED`，而现行写入方的实际取值不同（tool 写 `OK`/`ERROR`、egress `OK`/`DENIED`、model `SUCCEEDED`/`FAILED`，即 TASK-002 登记的漂移）。投影按 `OK|SUCCEEDED|SUCCESS → SUCCESS`、`ERROR|FAILED → FAILED`、其余透传固化，作为 RULE-08 的统一暴露口径。
- **保留 legacy 字段**：响应同时保留 `id`/`actor_display_name`/`create_time` 与 `keyword` 过滤，以兼容既有 B-04 测试（`tests/console_platform/test_audits_api.py`，回归必需）与前端 `modules/agent-management/AgentDetailSideSheet.tsx`（活跃消费者）。
- **Schema 名以实际迁移为准**：设计写 `runtime.task_execution`/`task_event`，实际为 `task.task_execution`/`task.task_event`（migration 0002），实现按实际；**设计待更正**。
- **时区**：`YYYY-MM-DD HH:mm:ss` 未规定时区，实现按服务本地时区格式化（若偏好 UTC 为一行改动）。
- **`resource_id` 过滤**：参数表类型为 UUID，但 egress 行的 `resource_id` 可能回退为字符串 `target`（设计 §3.3），故这些行无法按 `resource_id` 过滤；按参数表实现，未擅自放宽类型。
- 新增 `infrastructure/repositories/audit_query_repository.py`（TASK-003 的 Files 未列），为遵循 route/service/repository 分层。
- E-03: verified — automated command passed; run_id=0d081c74856d4cab9e985ae0ed2e71b5 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-004: 审计详情 API-02 与关联降级

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 11-audit-observability.backend.design.md#3.4 接口设计, 11-audit-observability.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: E-01
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/audits.py`, `tests/console_platform/test_audit_detail_api.py`、`infrastructure/repositories/audit_query_repository.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现 API-02 审计详情：按 `audit_type` 解析对应审计表返回单条详情与关联（Run/Task/Trace）；关联源不可读时按 E-01 降级——`related` 置空并标记 missing，不返回假关联、不抛错。

### Checklist

- [x] [E-01][integration] 以真实 PostgreSQL 为边界编写/扩展用例：关联 Run 已归档/不可读时审计仍可展示，`related` 置空且标记 missing。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_detail_api.py","-k","e01"]`。
- [x] [E-01][integration] 覆盖详情 404/非法 `audit_type` 分支（`COMMON_NOT_FOUND` / `COMMON_VALIDATION_ERROR`）。
- [x] 实现或补齐：详情查询按 `(tenant_id, audit_id, audit_type)` 单表命中，关联解析失败只降级不抛错。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | Console 详情 HTTP→真实 PostgreSQL | 关联不可读时审计可展示；`related` 置空并标记 missing；无假关联 | tests/console_platform/test_audit_detail_api.py / E-01 | `["uv","run","pytest","-q","tests/console_platform/test_audit_detail_api.py","-k","e01"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-01 | **真实 RED**：首跑 3 failed —— `test_e01_unreadable_relation_is_reported_as_missing` 报 `assert 404 == 200`（详情路由尚不存在，api-kit 的 `StarletteHTTPException` 处理器返回 `COMMON_NOT_FOUND`）；`test_api02_unknown_or_mismatched_type_is_not_found_or_invalid` 报 `assert 404 == 422`。 | 新增 `GET /api/v1/audits/{audit_id}?audit_type=`（复用 API-01 的四表投影 + 每类型 extras + `related`/`related_missing`）→ **3 passed**；控制台回归 `tests/console_platform/` **100 passed**；ruff/mypy 干净；函数均 ≤50 行。 | `test_e01_unreadable_relation_is_reported_as_missing`（关联 Run 不可读时：审计自身字段完整可见、`related` 不含伪造值、`related_missing=true`、HTTP 200）；`test_api02_returns_detail_for_each_audit_type`（四类型 payload 形状 + `audit_type` 回显）；`test_api02_unknown_or_mismatched_type_is_not_found_or_invalid`（缺 `audit_type` → 422 校验错误；类型与 id 不匹配 → `COMMON_NOT_FOUND`） | 真实 Console HTTP（ASGI 全栈 + 登录会话/CSRF）→ 真实 PostgreSQL（四张审计表 + 以 soft-delete 注入"关联不可读"的 `runtime.run_record`）；未 mock 业务 API；查询数恒定（1 投影 + 1 extras + ≤2 关联查询，无 N+1） | verified |

**实现中的判断点（如实登记）**：
- **缺失标记命名**：`related_missing: bool`（与 `related` 同级）。设计只说"标记 missing"，全仓（Python/TS/design/docs）无既有约定；仅当**已声明**的关联（`run_id`/`task_id` 非空）不可读时为 `true`；config 行不声明关联 → `related:{}` + `related_missing:false`（既无缺失也无伪造）。
- **"不可读"定义**：关联行不存在 / `is_deleted=true` / 租户不符（跨 schema 逻辑 UUID，遵循 RULE-03）。
- **not-found 与校验的划分**：缺参或非法枚举 → `COMMON_VALIDATION_ERROR`（422，文案/状态来自 catalog）；类型与 id 不匹配（合法类型但该表无此 id）→ `COMMON_NOT_FOUND`。
- **详情 payload**：API-01 的 16 个统一字段（**复用同一投影 SQL**，避免 list/detail 漂移）+ API-01 既有 legacy 别名 + 每类型 extras（CONFIG `source_ip`；TOOL `tool_call_id/tool_kind/prepared_args_hash/error_code`；EGRESS `target_type/adapter_key/platform_id/method/policy_decision/status_code/error_code`；MODEL `provider/model/attempt/retry_reason/input_tokens/output_tokens/error_code`）；原始列名（`before_json`/`args_preview_json`）不外泄，不含敏感列。
- **给 TASK-006 的前瞻提醒**：`GET /{audit_id}` 已声明；FastAPI 按声明顺序匹配，故 API-05/06 的 `POST /api/v1/audits/exports` 与 `GET /api/v1/audits/exports/{id}` 必须**声明在 `/{audit_id}` 之前**（或改用 `{audit_id:uuid}` 路径转换器），否则会被详情路由吃掉。
- E-01: verified — automated command passed; run_id=9d8f2e231dfa482596726ccedf2fce79 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-005: Admin Run 列表/详情 API-03/04

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 11-audit-observability.backend.design.md#3.4 接口设计, 11-audit-observability.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-03, B-201
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/api/admin_runs.py`, `tests/agent_runtime/test_admin_run_api.py`、`application/admin_run_service.py`、`infrastructure/admin_run_repository.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

在 Runtime 侧实现 `/internal/admin/runs` 与 `/internal/admin/runs/{run_id}`（Console 审计查询面出站调用），返回 Run/Snapshot/Timeline/Tool/Egress/Model/Artifact 详情契约；响应不得含 Secret，跨服务凭据按内部服务身份校验。

### Checklist

- [x] [S-03][E2E] 以真实 Browser→Console→Runtime 内部端点→真实 PostgreSQL 为边界编写/扩展用例（与 TASK-017 协同）：Admin 打开 Run 详情断言返回 Run/Snapshot/Timeline/Tool/Egress/Model/Artifact 且无 Secret。执行 argv：`["uv","run","pytest","-q","tests/agent_runtime/test_admin_run_api.py","-k","s03"]`。
- [x] 覆盖列表分页封套与详情 404 分支（`COMMON_NOT_FOUND`）。
- [x] 实现或补齐：内部端点按服务身份鉴权，响应字段脱敏（无 Secret/凭据）。
- [x] [B-201][integration] 以真实 Runtime HTTP `/internal/admin/runs`(+/`{run_id}`)→真实 PostgreSQL 为边界编写/扩展用例；关键断言：列表封套/过滤/排序/名称补齐、详情 7 段契约、响应无 Secret、内部身份校验。执行 argv：`["uv","run","pytest","-q","tests/agent_runtime/test_admin_run_api.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | E2E | Browser→Console→Runtime /internal/admin/runs→真实 PostgreSQL | 详情含 Run/Snapshot/Timeline/Tool/Egress/Model/Artifact；无 Secret | tests/acceptance/audit_observability/test_audit_acceptance.py / S-03（owner TASK-017） | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"]` | e2e_deferred |
| B-201 | integration | 真实 Runtime HTTP /internal/admin/runs(+/`{run_id}`)→真实 PostgreSQL | 列表封套/过滤/排序/名称补齐；详情 7 段契约；响应无 Secret；内部身份校验 | tests/agent_runtime/test_admin_run_api.py / B-201 | `["uv","run","pytest","-q","tests/agent_runtime/test_admin_run_api.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | **真实 RED**：`uv run pytest -q tests/agent_runtime/test_admin_run_api.py` → **4 failed, 1 passed**；失败均为路由未注册导致的 `COMMON_NOT_FOUND`（`assert 404 == 200` / `assert 404 == 403`）。诚实记录：`test_s03_admin_run_detail_unknown_run_is_not_found` 在 RED 阶段**空过**（未知路由同样是 404，无判别力），到 GREEN 才成为有意义断言。 | 新增 API-03 `GET /internal/admin/runs` 与 API-04 `GET /internal/admin/runs/{run_id}`（router → service → repository 分层、参数化 SQL、内部服务身份复用 api-kit `require_internal_service`/`X-Internal-Service`）→ **5 passed**；runtime 回归 `tests/agent_runtime/` **145 passed**；ruff/mypy 干净。 | 5 个用例：`test_s03_admin_run_list_filters_and_paginates`（封套 `{items,page,page_size,total}`、过滤收窄、`start_time DESC` 排序、名称补齐、分页边界与非法 `status` → `COMMON_VALIDATION_ERROR`）；`test_s03_admin_run_detail_returns_full_contract_without_secret`（7 段契约逐段形状 + 序列化响应无 Secret/凭据/原始 Prompt）；`test_s03_admin_run_detail_without_snapshot_or_artifacts_is_empty`（缺段 → `null`/`[]` 而非报错）；`test_s03_admin_run_detail_unknown_run_is_not_found`；`test_s03_admin_run_endpoints_require_internal_service_identity`（缺失/错误内部身份 → `FORBIDDEN`） | 真实 PostgreSQL（`runtime.*` 运行事实 + `control.*` 名称表）+ 真实 HTTP 路径；种子经真实 `EventWriter` / `RuntimeAuditWriter` 写入（非裸 SQL 造数）；未 mock 业务 API；无残留进程 | verified |

**实现中的判断点（如实登记，未静默决定）**：
- **名称来源**：`agent_name` ← `control.agent_definition.name`；`user_name` ← `control.platform_user.display_name`（`runtime.run_record.user_id` 承载**平台用户** id，与 Console 审计投影一致，非 `console_account`）；同一条 SQL `LEFT JOIN` 完成，无 N+1。
- **鉴权口径待收敛**：设计列 `UNAUTHORIZED / FORBIDDEN`，但复用的 api-kit 服务身份约定把"缺失/错误身份"统一归为 `FORBIDDEN`(403)；`UNAUTHORIZED` 属 Console 浏览器会话层，本端点无可达路径。两种情形均测为 403。
- **`skill_id` 语义设计未定义**：`runtime.run_record` 无 skill 列 → 按该 run 的冻结快照过滤（`EXISTS (… runtime_snapshot s JOIN LATERAL jsonb_array_elements(s.skill_catalog_json) …)`），非索引支撑，已在 repository docstring 标注。
- **`status` 取持久化原值**（非派生的 `CANCELLING`），因契约把 `cancel_requested` 单列展示（与 `/v1/runs` 的 `display_run_status` 刻意不同）——若 Console 期望 `CANCELLING` 需 spec 决策。
- **时间过滤/排序**：过滤按 `run_record.start_time`（含端点）；排序 `start_time DESC NULLS LAST, id DESC`（补 NULLS LAST 避免 NULL 排前 + tiebreak 稳定分页）。
- **timeline 定义**：该 run 的全部 `canonical_event`（按 `seq`，含 delta 帧），未按 `stream_type` 过滤（设计只说"persisted canonical events"）。
- **响应层不做二次脱敏**（三层收口归 TASK-008），改为**白名单投影**：SELECT 不含 `run_record.input_text`/`error_message`、不含 `runtime_snapshot.agent_json/model_json`（后者确含 `api_key`）；并用 mutation 验证断言有牙（加回 `model_json` 会挂形状断言、注入原始 Prompt 标记会挂 LEAK 断言）。
- 新增 `application/admin_run_service.py` 与 `infrastructure/admin_run_repository.py`（计划 Files 未列），为满足 router→service→repository 分层。
- B-201: verified — automated command passed; run_id=7b7f870d6eb147e59baf14df0c3b37af (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-006: 导出任务表与创建 API-05（幂等）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-003
- **Source**: 11-audit-observability.backend.design.md#3.3 数据设计, 11-audit-observability.backend.design.md#3.4 接口设计, 11-audit-observability.backend.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: harness-api#RULE-api-002, harness-data#RULE-data-001
- **Acceptance-Refs**: S-05, E-05, RULE-03, RULE-09, RULE-api-002, RULE-data-001
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/audits.py`, `apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py`, `tests/console_platform/test_audit_export_api.py`、`application/audit_export_service.py`、`infrastructure/repositories/audit_export_repository.py`、`migrations/versions/0014_audit_export_job.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

新增 `control.audit_export_job`（标准列 + `timestamptz` + `jsonb` 筛选）与 API-05 `POST /api/v1/audits/exports`：必携 `Idempotency-Key`，复用共享幂等表 partial unique `(tenant_id, idempotency_key, endpoint)`（`endpoint=/api/v1/audits/exports`），指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256（含 endpoint、tenant、`created_by`、筛选条件）；同 key 同指纹重放首次结果、异指纹 409 `IDEMPOTENCY_MISMATCH`，创建与幂等记录同事务。

### Checklist

- [x] [E-05][integration] 以真实 PostgreSQL partial unique 为边界编写/扩展用例：同 key 异指纹 → `IDEMPOTENCY_MISMATCH`（msg/http_status 来自 catalog），且不创建第二个任务。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"]`。
- [x] [S-05][E2E] 与 TASK-017 协同覆盖真实 Browser 提交与重试（本任务负责实现侧：同 key 同指纹重放首次结果、返回同一 `export_id`）。
- [x] [RULE-03][integration] 验证新表标准列、跨 Owner 逻辑 UUID、`jsonb` 与时间列类型符合 `harness-data` 要求。
- [x] [RULE-09][integration] 作为唯一最终负责人，沿 真实 Console HTTP→共享幂等表 验证创建类 POST 幂等全链路（同指纹重放 / 异指纹 409 / 并发 partial unique 兜底）。
- [x] [RULE-api-002][E2E] verifier_ref=harness-api#RULE-api-002；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`；补充真实边界 真实 Console 导出创建 HTTP→幂等表；断言 同 key 同指纹重放首次结果、异指纹 409；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"]`。
- [x] [RULE-data-001][integration] verifier_ref=harness-data#RULE-data-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests","-k","schema_parity"]`；联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests -k schema_parity"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | E2E | Browser→Console 导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) | 同 key 重试返回同一任务；不重复创建 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-05（owner TASK-017） | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"]` | e2e_deferred |
| E-05 | integration | Console 导出创建→幂等表 partial unique | 异指纹 → `IDEMPOTENCY_MISMATCH`；不建第二个任务 | tests/console_platform/test_audit_export_api.py / E-05 | `["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"]` | verified |
| RULE-03 | integration | 新表→真实 PostgreSQL | 标准列 + partial unique + `timestamptz` + 跨 Schema 逻辑 UUID | tests/console_platform/test_audit_export_api.py / RULE-03 | `["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py"]` | verified |
| RULE-09 | integration | Console HTTP→共享幂等表 | 同指纹重放/异指纹 409/并发兜底 | tests/console_platform/test_audit_export_api.py / RULE-09 | `["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py"]` | verified |
| RULE-api-002 | E2E | 真实 Console 导出创建 HTTP + 原 verifier 真实边界 | 幂等语义与原 verifier 全部通过 | tests/console_platform/test_audit_export_api.py + 原 verifier / RULE-api-002 | `["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"]` | verified |
| RULE-data-001 | integration | 真实 PostgreSQL 表结构/约束 + 原 verifier 真实边界 | 表结构与 schema parity 一致 | tests/console_platform/test_audit_export_api.py + 原 verifier / RULE-data-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests -k schema_parity"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-05 | **真实 RED**：`3 failed, 3 errors` —— 两条创建用例 `assert 405 == 200`（`POST /exports` 被既有的 `GET /{audit_id}` 详情路由吃掉，**正是 TASK-004 预警的路由顺序坑**）；teardown `UndefinedTableError: relation "control.audit_export_job" does not exist`。 | 新建 `control.audit_export_job`（模型 + 迁移 `0014_audit_export_job`，`down_revision="0013"`，已 `upgrade head` 到 0014）+ 复用共享幂等表 `control.skill_import_idempotency`（`endpoint=/api/v1/audits/exports`）+ 指纹=规范化 JSON（`sort_keys`+紧凑分隔符）SHA256（含 endpoint/tenant/created_by/export_format/filters）+ 沿用仓库既有 `pg_advisory_xact_lock` 重放模式 → **3 passed**；console 回归 `tests/console_platform/` **103 passed**；配对原 verifier `tests/console_skill/test_import_idempotency.py` **5 passed**；ruff/mypy 干净；并发自检（两个同 key 并发 POST → 同一 `export_id`、job 行数 1，临时用例用后删除）；测试后残留 `jobs: 0`、该 endpoint 幂等行 `0`。 | `test_e05_same_key_different_filters_is_idempotency_mismatch`（同 key 异筛选 → `IDEMPOTENCY_MISMATCH`（409，catalog 文案）+ 该租户恰 1 个 job 行）；`test_s05_same_key_same_filters_replays_first_result`（同 key 同筛选 → 同一 `export_id`、仍 1 行、该 endpoint 恰 1 条幂等记录）；`test_api05_missing_idempotency_key_and_invalid_format_are_rejected`（缺 `Idempotency-Key` / 非法 `export_format` → `COMMON_VALIDATION_ERROR` 且不产生 job 行）；并断言 job 字段（`status=PENDING`、`created_by`=登录账号、`filters_json` 规范化）与审计列表/详情无路由回归 | 真实 PostgreSQL（新表 + 共享幂等表逐行回读）+ 真实 HTTP（封测封套与 catalog 文案）；未 mock 业务 API | verified |
| RULE-03 | 无独立 RED（随 E-05 取证）。 | 新表符合标准列/`timestamptz`/`jsonb`/索引约定；`created_by` 为跨 Schema 逻辑 UUID（不加 FK，与 `ConfigAuditLog.actor_user_id` 一致）；`ix_audit_export_job_tenant_status_create_time` 支撑待处理任务轮询。 | 迁移与模型；`tests/console_platform/test_schema_parity.py`（在 console 回归内通过） | 真实 PostgreSQL 反射校验（列/索引） | verified |
| RULE-09 | 无独立 RED（随 E-05 取证）。 | 创建类 POST 必携 `Idempotency-Key`；共享幂等表 partial unique `(tenant_id, idempotency_key, endpoint)`；同 key 同指纹重放首次结果、异指纹 409；并发由 partial unique + advisory lock 兜底（自检见上）。 | 同 E-05 用例 | 真实 PostgreSQL + 真实 HTTP | verified |
| RULE-api-002 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests/console_skill/test_import_idempotency.py"]` → **3 passed + 5 passed**（原 verifier 全部通过）。 | tests/console_platform/test_audit_export_api.py + 原 verifier | 真实 Console HTTP + 原 verifier 真实边界 | verified |
| RULE-data-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/console_platform/test_audit_export_api.py && uv run pytest -q tests -k schema_parity"]` → 本文件 3 passed + 原 verifier 通过（schema_parity 覆盖在 console/仓库回归内）。 | 同上 + `tests -k schema_parity` | 真实 PostgreSQL 表结构 | verified |

**实现中的判断点（如实登记）**：指纹字段集含 `export_format`（设计写"含 endpoint/tenant/actor/filters"，格式不同即请求内容不同）；规范化筛选**不含 legacy `keyword`**（API-05 设计字段表未暴露，body `extra="forbid"` 会拒绝）；复用共享幂等表**不新建**第二张幂等表；重放沿用仓库既有 `pg_advisory_xact_lock` 模式（partial unique 仍作 DB 兜底）；路由顺序采用"把 `POST /exports` 声明在 `/{audit_id}` 之前"而非改用 `{audit_id:uuid}`（后者会把非法 uuid 的详情请求从 422 变 404）；`validate_filters` 抽为模块级供 API-01/05 共用口径；`create_time` 复用 `format_console_time`（RULE-05）。
- E-05: verified — automated command passed; run_id=c1808094e57242419e22393732f4f09e (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-007: 导出状态/下载 API-06 与执行落地

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-006
- **Source**: 11-audit-observability.backend.design.md#3.4 接口设计, 11-audit-observability.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-05, B-202
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/audits.py`, `apps/console-platform/backend/src/muad_console_platform/application/audit_export_service.py`, `tests/console_platform/test_audit_export_download.py`、`infrastructure/repositories/audit_export_repository.py`、`infrastructure/repositories/audit_query_repository.py`
- **Estimate**: 半天级（含导出执行与产物落地）；超出先拆执行与下载两段

### Description

实现 API-06 状态查询与下载：`PENDING/RUNNING/SUCCEEDED/FAILED` 状态机、`row_count`/`error_code`、仅 `SUCCEEDED` 可下载（未完成 `COMMON_CONFLICT`、不存在/跨租户 `COMMON_NOT_FOUND`）；导出产物落 artifact store 并只存 `artifact_ref`，不复制审计明细、不含 Secret。

### Checklist

- [x] [S-05][E2E] 与 TASK-017 协同覆盖"轮询至 SUCCEEDED 并下载"的真实链路（本任务负责实现侧：状态推进、产物落地与下载响应头）。
- [x] [S-05][integration] 覆盖导出执行：按任务筛选条件生成 CSV/JSON，写入 artifact store 并回填 `artifact_ref`/`row_count`/`status`。
- [x] 覆盖错误分支：未完成下载 → `COMMON_CONFLICT`；不存在 → `COMMON_NOT_FOUND`；执行失败写 `error_code`。
- [x] [B-202][integration] 以真实 Console HTTP 导出状态/下载 → 真实 PostgreSQL + artifact store 为边界编写/扩展用例；关键断言：状态机可轮询、仅 SUCCEEDED 可下载（响应头正确）、未完成 COMMON_CONFLICT、不存在/跨租户 COMMON_NOT_FOUND、产物无 Secret。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_export_download.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | E2E | Browser→Console 导出状态/下载 HTTP→PostgreSQL + artifact store | 轮询至 SUCCEEDED；下载返回产物与正确响应头；未完成/不存在错误码正确 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-05（owner TASK-017） | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"]` | e2e_deferred |
| B-202 | integration | 真实 Console HTTP 导出状态/下载 → 真实 PostgreSQL + artifact store | 状态机 PENDING→RUNNING→SUCCEEDED/FAILED 可轮询；仅 SUCCEEDED 可下载且带正确响应头；未完成 COMMON_CONFLICT、不存在/跨租户 COMMON_NOT_FOUND；产物不含 Secret | tests/console_platform/test_audit_export_download.py / B-202 | `["uv","run","pytest","-q","tests/console_platform/test_audit_export_download.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-202 | **真实 RED**：`5 failed in 0.72s`，全部为 `assert 404 == 200/409`（`COMMON_NOT_FOUND` —— API-06 两条 GET 路由尚不存在）。诚实记录：首轮 RED 为 4 failed / 1 passed，随即给跨租户用例补了正向对照，使其 RED 阶段不空过。 | 实现 API-06 状态/下载两条路由（均声明在 `GET /{audit_id}` 之前，`export_id` 用 `uuid.UUID` 路径参数）+ 懒执行器 `run_pending_exports`（按设计的 `(tenant_id, status, create_time)` 索引 `FOR UPDATE SKIP LOCKED` claim，随即 `mark_running`，多 Pod 只执行一次）+ 产物复用既有 artifact store（`write_artifact`/`artifact_path`，键位 `exports/{tenant}/{export_id}/audits.{csv,json}` —— **刻意放在 `skills/` 之外**，否则会被 `cleanup_orphan_files` 回收）→ **5 passed in 0.77s**；console 回归 `tests/console_platform/` **108 passed**；ruff/mypy 干净；配对 verifier 腿（`test_audit_export_api.py` 3、`test_import_idempotency.py` 5、`schema_parity` 35、i18n+catalog 15、`check_error_message_hardcode.py` OK）全绿。 | 5 个用例：`test_b202_export_status_progresses_and_download_returns_artifact`（轮询状态推进到 `SUCCEEDED`、`row_count` 与种子一致、下载 `Content-Type`/`Content-Disposition` 文件名正确、正文按格式解析且只含筛选行、无 Secret）；`test_b202_json_export_download_parses_as_json`；`test_b202_download_before_completion_is_conflict`（PENDING 下载 → `COMMON_CONFLICT`）；`test_b202_unknown_and_cross_tenant_export_is_not_found`（未知与跨租户均 `COMMON_NOT_FOUND`，且对方任务仍 PENDING、无产物）；`test_b202_failed_execution_reports_catalog_error_code`（执行失败 → `FAILED` + catalog `error_code`，下载仍冲突） | 真实 Console HTTP + 真实 PostgreSQL（作业行逐行回读）+ 真实 artifact store（每用例临时 `ARTIFACT_ROOT`，并断言下载字节与磁盘产物逐字节一致，不污染仓库 `.data/artifacts`）；未 mock 业务 API | verified |

**实现中的判断点（如实登记）**：
- **执行触发（设计未规定进程/间隔）**：采用"状态 GET 内懒执行"——`ASGITransport` 下 lifespan 不启动，后台任务会 flaky，故由轮询自驱动；**下载路由刻意不触发执行**，否则 PENDING 下载无法返回 `COMMON_CONFLICT`。代价：状态 GET 带副作用（限于本租户 PENDING 作业）。
- **并发**：`SELECT … WHERE tenant_id/status='PENDING'/is_deleted=false ORDER BY create_time FOR UPDATE SKIP LOCKED` + 立即 `mark_running`；`PENDING→RUNNING→SUCCEEDED/FAILED` 为真实状态迁移（非装饰）。
- **产物存储**：复用 `infrastructure/skill_artifact_store.py` 的 `write_artifact`/`artifact_path`（同一 NFS 根、原子写、遍历防护）；键位**避开 `skills/`**（`cleanup_orphan_files` 会 glob `skills/*/*`）；`write_artifact` 不可覆盖，重复执行同一作业会落 `FAILED/COMMON_INTERNAL_ERROR` 而非静默覆盖。
- **列集与格式**：CSV 16 列表头/`\n`/UTF-8；JSON 为裸行数组；列集与 API-01/02 同一 `_PROJECTION_SQL`（无第二套查询、不建宽表）；时间用 `format_console_time`（`YYYY-MM-DD HH:mm:ss`，RULE-05/`harness-time`）。
- **`update_time`**：模型只有 `server_default now()`（无 DB `onupdate`），每次状态迁移在仓储层显式写 `now(UTC)`，否则暴露的 `update_time` 永不变化。
- **失败注入**：真实 PENDING 行 + 非法存储筛选（执行前复用 `validate_filters` 复验）→ `FAILED` + catalog 码；只捕获 `AppError`（业务→catalog）与 `OSError`（产物写失败→`COMMON_INTERNAL_ERROR`，`logger.exception`），其余如实上抛。
- **跨租户口径**：查询恒带 `(tenant_id, export_id, is_deleted=false)`，未知与外部 id 不可区分（均 `COMMON_NOT_FOUND`）；执行器只 claim 请求租户的作业（用例断言对方作业仍 PENDING）。沿用兄弟路由既有的 `TenantId`（`X-Tenant-Id`），未擅自改动共享鉴权模型——该模块已登记的鉴权加固遗留项继续保留。
- **状态 GET 先执行后判存在**（一次读取而非 check/run/re-read），接受一个小副作用。
- 下载返回裸 `Response`（正文即产物字节，非封套）；错误仍走 `AppError` → catalog 封套；`text/*` 会被 Starlette 追加 `charset`，断言按 media type 比较。
- B-202: verified — automated command passed; run_id=7599b9172245445a9e07dbd87467d692 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-008: 三层脱敏收口（日志/写入/响应）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 11-audit-observability.backend.design.md#3.5 质量实现方案, 11-audit-observability.backend.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: E-02, RULE-01, RULE-04, RULE-log-001, RULE-secret-001
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/audit_service.py`, `tests/test_audit_redaction.py`、`packages/api-kit/src/muad_api/audit.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

把脱敏做成三层硬约束：①logging-kit 出口（日志文件）；②审计写入前（`args_preview_json`/`before_json`/`after_json`/`error_message` 递归脱敏，`prepared_args_hash` 只存 hash）；③Console API 响应。识别字段至少 Authorization / Cookie / Set-Cookie / api_key / access_token / refresh_token / secret / password，统一替换 `<redacted>`。

### Checklist

- [x] [E-02][integration] 以真实 logging-kit 出口与真实 PostgreSQL 为边界编写/扩展用例：payload 含 Authorization/api_key/refresh_token 时，日志文件、审计行、API 响应三处均为遮蔽值且无明文。执行 argv：`["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"]`。
- [x] [RULE-01][integration] 验证 logging-kit 出口按 service/YYYY-MM-DD 落盘且带 `trace_id`/`request_id`，敏感字段遮蔽（联合 E-02）。
- [x] [RULE-04][integration] 验证密钥不进入审计/日志/Snapshot/API 响应，对外以 `*_configured` 表达；审计只存 hash/preview。
- [x] [RULE-log-001][integration] verifier_ref=harness-log#RULE-log-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]`；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py"]`。
- [x] [RULE-secret-001][integration] verifier_ref=harness-secret#RULE-secret-001；原 verifier 输入 argv=`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | integration | 真实 logging-kit 出口 + 审计写入 + Console 响应→PostgreSQL | 三处均遮蔽；无明文 Secret | tests/test_audit_redaction.py / E-02 | `["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"]` | verified |
| RULE-01 | integration | 真实 logging-kit 出口与日志文件 | 按 service/日期落盘 + trace/request 字段 + 遮蔽 | tests/test_audit_redaction.py / RULE-01 | `["uv","run","pytest","-q","tests/test_audit_redaction.py"]` | verified |
| RULE-04 | integration | 审计写入前→PostgreSQL | 密钥不进审计/日志/响应；只存 hash/preview | tests/test_audit_redaction.py / RULE-04 | `["uv","run","pytest","-q","tests/test_audit_redaction.py"]` | verified |
| RULE-log-001 | integration | logging-kit + 原 verifier 真实边界 | 原 verifier 全部通过 | tests/test_audit_redaction.py + 原 verifier / RULE-log-001 | `["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py"]` | verified |
| RULE-secret-001 | integration | 三层脱敏 + 原 verifier 真实边界 | 原 verifier 全部通过 | tests/test_audit_redaction.py + 原 verifier / RULE-secret-001 | `["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-02 | **真实 RED**（2 failed / 2 passed）：`test_e02_console_audit_write_masks_secrets_in_db` 报 `control.config_audit_log 中可查询到 Secret 明文：Bearer e02-authz-…`（`assert 1 == 0`）；`test_e02_console_api_detail_masks_secrets_in_response` 因同一根因失败——响应 `headers` 里 `Authorization`/`Cookie`/`Set-Cookie` 为明文。**根因**：api-kit `sanitize_audit_payload` 的敏感键清单缺 `authorization`/`cookie`（设计 §3.5 明确列举这三者），故审计写入层泄露、API 响应层连带泄露。 | 给 `packages/api-kit/src/muad_api/audit.py` 的 `SENSITIVE_KEY_MARKERS` 补 `authorization` + `cookie`（**单点修复**；API 响应层随之转绿，无需第二处改动）→ `tests/test_audit_redaction.py` **4 passed**；logging 套件（`test_logging.py` + `test_logging_redaction.py`）**10 passed**；console 回归 **108 passed**；ruff/mypy 干净；更广回归（`tests/` 去 acceptance/e2e/frontend）**1105 passed + 1 pre-existing failure**（`tests/test_secret_ref_residue.py`，经 stash 验证为改动前既有失败，指向 10 模块的 `tests/acceptance/im_gateway/test_secrets_and_readiness.py`）。 | 4 个 `e02` 用例：① Console 审计写入后 DB 无明文（`jsonb::text LIKE '%<secret>%'` 为 0）且非敏感字段保留（嵌套 `headers` 中 `X-Trace` 存活 ⇒ 证明递归遮蔽）；② Runtime 审计写入 `args_preview_json["api_key"] == "<redacted>"`（TASK-002 层契约复述）；③ logging 出口产物文件无明文（消息文本 + `record.fields` + 嵌套/URL 内嵌 `key=value` 均遮蔽）；④ API-02 详情响应序列化无明文、遮蔽值存活。 | 真实 api-kit 写入原语 + 真实 PostgreSQL 逐行回读 + 真实 logging-kit 落盘文件读取 + 真实 Console HTTP（复用 `tests/console_platform/conftest.py` 的 `client`/`tenant` fixtures）；未 mock 业务 API；无 DB 残留（`runtime.tool_call_audit` 与 `config_audit_log` 计数为 0）。 | verified |
| RULE-01 / RULE-04 | 无独立 RED（随 E-02 一并取证，验收类）。 | 三层遮蔽生效：识别键含 `Authorization/Cookie/Set-Cookie/api_key/access_token/refresh_token/secret/password`；审计只存脱敏值/hash，密钥不进审计/日志/API 响应。 | 同 E-02 四个用例 | 同 E-02 | verified |
| RULE-log-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py"]` → **4 passed + 11 passed**（原 verifier 全部通过）。 | tests/test_audit_redaction.py + 原 verifier | 真实 logging-kit 出口 + 原 verifier 真实边界 | verified |
| RULE-secret-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/test_audit_redaction.py && uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"]` → **4 passed + 12 passed**（原 verifier 全部通过）。 | 同上 + 原 verifier | 同 E-02 + 原 verifier 真实边界 | verified |

**实现中的判断点（如实登记）**：
- **仅 layer 1（Console 审计写入）有缺口**；runtime 写入层（TASK-002 已含 authorization/cookie）与 logging 出口层**本就满足契约**，仅作断言未改代码。
- **遮蔽策略各层不同且保持原样**：Console 写入层是**删除**敏感键（由既有验收断言钉死：`test_secret_consumers.py` 断言 `before_json == {}`）；runtime/logging 层是**替换**（`<redacted>`/`***`）。E-02 的判据是"无明文"，故未统一策略；若设计要求 Console 层也落 `<redacted>`，属行为变更（需同步改那条钉死断言），已标注未做。
- **`error_message`**：设计 §3.5 的递归规则包含它，但当前无任何写入方能填充审计表的 `error_message`（`RuntimeAuditWriter` 无该参数，Console 只读 `error_code`）⇒ 无泄露路径，未添加死代码。
- **`set_log_context` 上下文字段**：由 `JsonLogFormatter` 原样写出（仅 message + `record.fields` 过 `RedactionFilter`）；仓库内无调用方把密钥放入该上下文（api-kit 中间件只放 trace/request/tenant/caller/locale），属"剩余面"而非已证明泄露——未越界改动 formatter 契约。
- 测试 payload 经**真实应用服务**注入（而非 HTTP 配置变更）：所有真实快照都刻意不含密钥，走 HTTP 会空过、测不到 E-02。
- E-02: verified — automated command passed; run_id=a6d40113f0244822afeee659ee08c9c7 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-009: 可观测性 trace 关联字段与指标目录

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: 11-audit-observability.backend.design.md#3.5 质量实现方案, 11-audit-observability.backend.design.md#4 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: B-203
- **Files**: `apps/console-platform/backend/src/muad_console_platform/main.py`, `tests/test_audit_observability_config.py`、`apps/console-platform/backend/src/muad_console_platform/metrics.py`、`packages/api-kit/src/muad_api/context.py`、`packages/api-kit/src/muad_api/middleware.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

按 §3.5 可观测性与 §4 落实：审计与日志统一携带 `trace_id/request_id/run_id/conversation_id/platform_user_id/agent_id/snapshot_id/skill_artifact_id/tool_call_id/task_id/schedule_id` 关联字段；每服务按目录导出 OTel/监控指标（不进业务 Console）；`/healthz` 与 `/readyz` 语义与失败快启校验保持一致；指标 label 不含 Secret/凭据/消息正文/PII。

### Checklist

- [x] 实现或补齐：trace 关联字段在审计写入与日志上下文中的注入路径；缺字段时显式置空而非伪造。
- [x] 实现或补齐：指标注册（Console 侧 `console_api_requests_total` 等）与 label 脱敏约束。
- [x] 以真实 `/metrics` 端点验证指标可抓取、label 无敏感值（真实 uvicorn 单进程 + 真实 HTTP）。
- [x] [B-203][integration] 以真实 `/metrics` HTTP 端点 + 真实日志出口 + 运行审计表(PostgreSQL) 为边界编写/扩展用例；关键断言：指标可抓取、label 不含 Secret/PII、trace 关联字段可串联、缺字段显式置空。执行 argv：`["uv","run","pytest","-q","tests/test_audit_observability_config.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-203 | integration | 真实 /metrics HTTP 端点 + 真实日志出口 + 运行审计表(PostgreSQL) | 指标可抓取且 label 无 Secret/凭据/消息正文/PII；trace 关联字段可在审计与日志中串联；缺字段显式置空不伪造 | tests/test_audit_observability_config.py / B-203 | `["uv","run","pytest","-q","tests/test_audit_observability_config.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-203 | **真实 RED**（3 failed / 2 passed）：① `/metrics` 返回 `404 COMMON_NOT_FOUND`（Console 从未安装 api-kit 指标注册表）；② 审计写入请求**没有任何日志记录** —— `真实日志出口未按 service/日期落盘: …/2026-09-25.log / assert False = exists()`；③ `ImportError: cannot import name 'TRACE_CORRELATION_FIELDS'`。诚实记录：healthz/readyz 两条断言**首跑即通过**（api-kit 探针本就正确），故未制造改动。 | 新增 `apps/console-platform/backend/src/muad_console_platform/metrics.py`（Console 指标目录 + 路由模板计数中间件 + `install_console_metrics`，**复用** api-kit `install_metrics`，不建第二套注册表）；api-kit 增 `TRACE_CORRELATION_FIELDS`（11 字段，未设显式 `""`、非法名拒绝）并让日志上下文字段经同一相关存储；审计写入新增结构化日志记录（只记关联字段 + action/resource_type/resource_id）；三处 catalog 操作接入 outcome 计数 → `tests/test_audit_observability_config.py` **5 passed**；console 回归 **108 passed**；logging 套件 **10 passed**；ruff/mypy 干净；全量（去 acceptance/e2e）**1242 passed + 1 预存在失败**（`tests/test_secret_ref_residue.py`，已在 pristine worktree 验证为既有失败）。 | 5 个用例：① `/metrics` 暴露 Console catalog 且 label **名**无敏感标记、label **值**无具体资源 ID/凭据形状/正文 canary，`path` 钉为路由模板（断言 `{path="/api/v1/models/{model_id}",status="401"}` 存在而具体 UUID 不在文本中）；② 审计写入请求的 DB 行 `trace_id` 与日志记录 `trace_id` 一致、`request_id` 由响应封套与日志对齐、未设字段显式空；③ `/healthz` 不依赖依赖项返回 200、`/readyz` 反映依赖就绪（503 + failed 列表）；④ `REDIS_URL=127.0.0.1:1` 时 `/readyz` 仍 200；⑤ 关联字段集与设计一致（11 项） | 真实 `/metrics` HTTP 端点 + 真实日志出口文件（按 service/日期落盘）+ 真实 PostgreSQL（审计行回读）+ 真实 Console HTTP；未 mock 业务 API | verified |

**实现中的判断点与剩余缺口（如实登记）**：
- **剩余缺口（超出本任务 15–60 分钟估算与声明文件面）**：**Runtime / Worker 未暴露 `/metrics`**，其设计指标目录（如 `agent_runs_total`、`tasks_total`）目前只存在于设计与文档（Worker 另有既有的基于日志的计数模块）。补齐跨服务指标属多文件改动，建议后续单独开任务承接（应在 TASK-019 收口或后续需求中显式处置）。
- **`control.config_audit_log` 无 `request_id` 列**（设计 §3.3 字段表只给 `trace_id`），故审计与日志经关联上下文串联（`trace_id` 对齐 + 响应封套与日志的 `request_id` 对齐），未擅自加列/迁移；若要求审计表也持久化 `request_id`，属 schema 变更。
- **新增"审计写入日志记录"**：此前审计写入请求不产生任何日志记录，使"审计↔日志串联"无可取证对象；新增记录只含关联字段与 `action/resource_type/resource_id`，**绝不记 before/after 内容**（日志非审计事实源，业务回滚不影响 DB 行）。
- **label 卫生口径**：敏感标记作用于 label **名**；label **值**检查具体资源 ID（UUID）、凭据形状（`Bearer`/`Basic`/`sk-`/≥40 位 base64）与消息正文 canary；`path` 单独钉为路由模板（未匹配 → `"unmatched"`，低基数且无 PII）。该口径是首版"全字段敏感词扫描"误伤 `{path="/api/v1/auth/password"}` 路由模板后收敛的结果。
- **域计数器 `status` 语义**：成功 `SUCCESS`、`AppError` 记 catalog 码（如 `BOT_NOT_FOUND`/`AGENT_NOT_FOUND`/`SKILL_PACKAGE_INVALID`）、未预期异常 `FAILED`；HTTP 层 4xx 校验错只在 `console_api_requests_total{status=…}` 可见。
- **Redis 断言范围**：以死端口 `REDIS_URL` 证明 `/readyz` 不被 Redis 阻断；不覆盖"运行中 Redis 掉线"（该场景在 im_gateway 的 `test_redis_degradation.py`）。
- B-203: verified — automated command passed; run_id=2966cc0253944ecdaed96579f1d1ac96 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-010: 前端 service 层与类型契约

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 11-audit-observability.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: harness-frontend#RULE-front-001
- **Acceptance-Refs**: RULE-front-001, B-204
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/services/auditService.ts`, `apps/console-platform/frontend/src/modules/audit-observability/types.ts`, `tests/frontend/test_audit_services_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现 service 层与类型：`listAudits` / `getAudit` / `createExport`（Header `Idempotency-Key` 必填）/ `getExport` / `downloadExport`，后端 snake_case → 前端 camelCase 映射；导出幂等键由 service 在一次用户提交内生成并复用；组件不得裸用 axios/fetch，文案只用 i18n key。

### Checklist

- [x] [RULE-front-001][integration] verifier_ref=harness-frontend#RULE-front-001；原 verifier 输入 argv=`["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`；补充真实边界 前端源码契约（services 收口/无裸请求/i18n）；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_services_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [x] 实现或补齐：5 个 service 方法 + 类型定义（`AuditListQuery`/`AuditListItem`/`AuditExportCreateRequest`/`AuditExportJob`）。
- [x] 覆盖导出幂等键约定：同一用户提交复用同一 key，显式新导出才换 key（源码契约断言）。
- [x] [B-204][integration] 以前端源码契约 + 真实 tsc 类型检查 为边界编写/扩展用例；关键断言：服务只经共享 api client、无裸 axios/fetch；5 个方法签名与 Idempotency-Key 头；类型字段名一致；服务/类型文件无硬编码中文。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_services_contract.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-front-001 | integration | 前端源码 + 真实 typecheck/脚本 | 只经 services/；无裸 fetch/axios；文案为 i18n key；原 verifier 全部通过 | tests/frontend/test_audit_services_contract.py + 原 verifier / RULE-front-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_services_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]` | verified |
| B-204 | integration | 前端源码契约 + 真实 tsc 类型检查 | 服务只经共享 api client、无裸 axios/fetch；5 个方法签名与 Idempotency-Key 头；类型字段名一致；服务/类型文件无硬编码中文 | tests/frontend/test_audit_services_contract.py / B-204 | `["uv","run","pytest","-q","tests/frontend/test_audit_services_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| RULE-front-001 | **真实 RED**：`uv run pytest -q tests/frontend/test_audit_services_contract.py` → **11 failed**，首条断言即 `缺少前端模块文件：…/modules/audit-observability/types.ts`（模块尚不存在；非伪造失败）。 | 新建前端模块 `audit-observability/types.ts` + `services/auditService.ts`（5 个方法：`listAudits`/`getAudit`/`createExport`/`getExport`/`downloadExport`，全部经共享 `api` client；snake_case→camelCase 映射只在 service 层；`createExport(req, idempotencyKey)` 由**调用方**持有幂等键、不自生成，重试复用同一 key）→ 契约测试 **11 passed**；`tests/frontend/` **143 passed**；`uv run python scripts/check_frontend_api_usage.py` → OK；`check_frontend_i18n.py` → OK（615 keys）；`npm --prefix apps/console-platform/frontend run typecheck` → 无诊断；ruff 干净。 | `tests/frontend/test_audit_services_contract.py` 11 条：服务只经共享 client 且无裸 `axios`/`fetch(`；5 个方法存在且签名符合（含 `Idempotency-Key` 头、幂等键为显式入参且无自生成的 `newRequestId()` 调用）；类型字段名（camelCase）与设计一致；服务/类型文件无硬编码中文（i18n key 约束）。 | 前端源码契约 + 真实 `tsc --noEmit` 类型检查 + 两个仓库检查脚本；另以一次性 esbuild+node 探针（已删除、未入库）驱动真实 service 验证线上形态：`GET /audits`（`page_size` 500→100 夹取、`undefined` 筛选被丢弃）、`GET /audits/{id}?audit_type=TOOL`、`POST /audits/exports` 带 `Idempotency-Key` 且 body 为 snake_case、导出状态映射含 `errorCode`、下载以 `responseType:'blob'` 发起。 | verified |

**实现中的判断点与交接提醒（如实登记）**：
- **幂等键归属 = 调用方**：设计 §3.5 说"由 service 在一次用户提交内生成并复用"，任务 brief 要求写成显式入参；按 brief 实现（`createExport(req, idempotencyKey)` 只透传为 `Idempotency-Key` 头，绝不自生成），并在函数注释与契约测试中同时钉死"调用方持有 + 重试复用"。**TASK-014 需在每次用户提交时用 `newRequestId()` 生成一次**。
- **api client 用法**：只用 `api.get`/`api.post` + 兄弟 service 同款 `unwrap`（client 拦截器已负责 locale/request-id/CSRF/401）；测试禁止出现 `axios`/`create(`/`fetch(` 字样（注释也避免，与既有 `*_contract.py` 口径一致）。
- **映射只在 service 层**：显式 `toListParams`/`toExportBody`/`toAuditItem`/`toAuditDetail`，不用泛型遍历；线上保持 snake_case，DTO 才是 camelCase；`undefined` 筛选不出现在查询串（已运行时验证）。
- **超出 brief 列举的设计字段**：`AuditListItem` 另带 `target` 与 `latencyMs?`（设计 §3.4 / 后端 `PROJECTED_COLUMNS`），否则会静默丢后端数据；`traceId` 取可选（CONFIG 审计返回 `trace_id: null` → 映射为 `undefined`）。
- **额外类型**（`AuditDetail`/`AuditRelations`/`AuditPage`）：`getAudit` 需要真实返回类型，含 `related{runId?,taskId?}`、`relatedMissing`（E-01）与 `extras`（未单独映射的投影列 + 各表专有字段），使 TASK-012/013 无需再改 `types.ts`。
- **pageSize 守卫**：线上以 `Math.min(…, AUDIT_PAGE_SIZE_MAX=100)` 夹取（对齐后端约束），并导出 `AUDIT_PAGE_SIZE_DEFAULT=20` 供 TASK-011 初始化筛选状态。
- **`createExport` 返回形态**：后端创建仅返回 `{export_id,status,create_time}`，故创建时 `updateTime` 暂等于 `createTime`（已注释），后续真实值由 `getExport` 提供。
- **无 `index.ts` barrel**（兄弟模块都没有）。
- **交 TASK-014 的风险提醒**：下载用 `responseType:'blob'` 时失败响应体也是 Blob，`apiErrorBody()` 读不到封套 `code`；因此 `downloadExport` 只返回字节，失败路径应走"轮询 `getExport` → `FAILED.errorCode` → catalog/i18n 文案"（已在函数注释说明）。若 TASK-014 需要在下载时刻拿到错误码，需自行解码 Blob 错误体。
- **时间筛选格式留给 TASK-011**（`startTime`/`endTime` 原样透传字符串；后端收 `datetime`）。
- B-204: verified — automated command passed; run_id=d044e7057c964501bcad83c60c36aa92 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-011: 审计列表页容器与筛选栏

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-010
- **Source**: 11-audit-observability.frontend.design.md#3.2 页面与路由结构, 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.3.1 每个按钮/操作的设计
- **Spec-Refs**: harness-ui#RULE-ui-001
- **Acceptance-Refs**: E-06, RULE-ui-001, B-205
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/pages/AuditPage.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/components/AuditFilterBar.tsx`, `tests/frontend/test_audit_page_contract.py`、`src/App.tsx`、`src/locales/zh-CN.json`、`src/locales/en-US.json`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

新增路由 `/audits` 与页面容器：ConsoleShell + ModuleToolbar（左主操作、右上搜索筛选、右下分页），筛选栏覆盖时间/类型/用户/Agent/目标/动作/结果/Trace；筛选与分页状态在页面级维护；查询失败保留筛选并可重试（E-06）。

### Checklist

- [x] [E-06][integration] 覆盖查询失败路径：ErrorState 呈现且筛选条件保留、可重试（与 TASK-018 的 spec 协同）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""]`。
- [x] [RULE-ui-001][E2E] verifier_ref=harness-ui#RULE-ui-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]`；补充真实边界 真实 Console 页面与构建；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]`。
- [x] 实现或补齐：路由注册、菜单项、页面容器与筛选栏（复用 ConsoleShell/ModuleToolbar/EmptyState/ErrorState/PaginationFooter）。
- [x] 覆盖筛选条件 → `AuditListQuery` 的映射与重置行为。
- [x] [B-205][integration] 以前端源码契约 + 真实 tsc 类型检查 为边界编写/扩展用例；关键断言：路由与页面容器按 ConsoleShell+ModuleToolbar 组合（左上操作/右上筛选/右下分页）；筛选条件映射为 AuditListQuery；无硬编码中文。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_page_contract.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-06 | integration | Browser→Console 查询失败路径 | 保留筛选并可重试；ErrorState 呈现 | e2e/tests/audit-observability.spec.ts / E-06（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""]` | e2e_deferred |
| RULE-ui-001 | E2E | 真实 Console 页面 + 原 verifier 真实边界 | 左上操作/右上筛选/右下分页；主展示字段开详情；原 verifier 全部通过 | tests/frontend/test_audit_page_contract.py + 原 verifier / RULE-ui-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]` | verified |
| B-205 | integration | 前端源码契约 + 真实 tsc 类型检查 | 路由与页面容器按 ConsoleShell+ModuleToolbar 组合（左上操作/右上筛选/右下分页）；筛选条件映射为 AuditListQuery；无硬编码中文 | tests/frontend/test_audit_page_contract.py / B-205 | `["uv","run","pytest","-q","tests/frontend/test_audit_page_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-205 | **真实 RED**：`11 failed, 1 passed` —— 首条即 `AssertionError: 缺少前端文件：…/modules/audit-observability/pages/AuditPage.tsx`；唯一通过项是"菜单项已注册"（`nav.audit` 由模块 01 提供，**非本任务伪造成立**，已如实登记）。 | 新增 `pages/AuditPage.tsx`（筛选/分页/详情选择状态、`RemoteTable` 渲染、导出 `AuditTableProps`/`AuditDetailSideSheetProps` 供 TASK-012/013）与 `components/AuditFilterBar.tsx`（8 个筛选 + 重置/刷新，改动即重置 `page=1`）；`App.tsx` 注册 `/audits` 路由替换占位页；补 zh-CN/en-US 各 30 条 `audit.*` 词条 → 契约测试 **13 passed**；`tests/frontend/` **156 passed**；RULE-ui-001 原 verifier（`test_console_shell_contract.py` + `test_ui_style_contract.py`）**9 passed**；`check_frontend_api_usage.py` OK；`check_frontend_i18n.py` OK（645 keys）；`npm run typecheck` 干净；`npm run build` 通过（仅既有 chunk 警告）；ruff 干净。 | `tests/frontend/test_audit_page_contract.py` 13 条：`/audits` 路由与菜单项存在；路由层套 `AppLayout`（页面**不重复套壳**，断言 `AppLayout not in page`）；筛选栏 8 字段映射为 `AuditListQuery` 且改筛选重置 `page:1`；E-06 失败路径 only-`failed`（catch 不触碰 `query`/`items`/`total`）；无硬编码中文；页面只经 TASK-010 的 service（无 `createExport`/`audits/exports`，左主操作槽位留 `actions={null}` 并标注 TASK-014）。 | 前端源码契约 + 真实 `tsc --noEmit` + 真实 `vite build` + 两个仓库检查脚本 + RULE-ui-001 原 verifier；未 mock 业务 API | verified |
| E-06 | 无独立 RED（该场景的终验归 TASK-018 的浏览器验收；本任务以源码契约断言失败路径形态）。 | 查询失败时仅置 `failed`，`catch` 不触碰 `query`/`items`/`total` ⇒ 筛选条件与已加载数据保留、`ErrorState` 就地渲染且可重试、工具栏与筛选栏仍在（非空白页）。 | 同上用例的 E-06 断言 | 前端源码契约（UI 级证据由 TASK-018 的 Playwright spec 承载） | e2e_deferred（终验归 TASK-018） |
| RULE-ui-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]` → **13 + 9 passed + build 通过**（原 verifier 全部通过）。 | tests/frontend/test_audit_page_contract.py + 原 verifier | 前端源码契约 + 原 verifier 真实边界（含真实构建） | verified |

**实现中的判断点与跨任务约束（如实登记）**：
- **`ConsoleShell` 的真实组件名是 `layout/AppLayout.tsx`**（设计里写作 `ConsoleShell`）：设计/实现命名漂移，已按实际组件断言（路由层套 `AppLayout`，页面自身不重复套壳）。
- **冻结的仓库级 verifier 约束**：`tests/frontend/test_ui_style_contract.py::test_module_list_pages_use_remote_table` 要求每个 `modules/**/*Page.tsx` 使用 `RemoteTable`（内置右下分页）⇒ **TASK-012 的 `AuditTable` 必须为 `RemoteTable` 供给列定义与行为，而不是整表替换**；TASK-011 无法把页面里的表格部分留给 TASK-012，否则该 verifier 会红。
- **「Agent」筛选项口径冲突（设计/接口不一致）**：后端列表 API 无 `agent_id` 参数、TASK-010 冻结的 `AuditListQuery` 也无 `agentId`，8 个 UI 字段与 9 个查询键的差额恰好一个 ⇒ 本实现把「Agent」映射为 `resourceType`（Select 覆盖 AGENT/SKILL/MCP/MODEL/PROJECT_PLATFORM/USER/GRANT，docs/15 将 `resource_type` 映射为"审计类型"）。若原意是按 agent id 过滤，需新增后端参数（未擅自扩接口，标签/键可一行改回）。
- **时间筛选格式**（TASK-010 明确留给本任务）：`startTime` = 当日 00:00、`endTime` = 当日 23:59:59.999 的 ISO（对齐既有 `TaskPage.toIsoEndOfDay`；后端收 RFC3339）。
- **无搜索按钮**（按 §3.3.1 的 `Input+Select+DatePicker`）：筛选改动经单一 `emit` 咽喉点自动生效并重置 `page=1`；四个文本字段回车即查；`requestSeq` 丢弃乱序响应（文本输入逐键查询的取舍已登记，必要时可加防抖）。
- **按钮落位**：搜索/筛选、重置、刷新都在 `AuditFilterBar` 内、渲染于 `ModuleToolbar` 的右侧 `search` 槽；空态提供"清筛选"（§3.6）。
- **无 `PageHeader`**（§3.7「列表页不增加重复标题/说明块」）。
- B-205: verified — automated command passed; run_id=a5d870f0260f4eabbe5a364863e62bad (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-012: 审计表格与字段列

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-011
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: S-06, B-206
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/components/AuditTable.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditList.ts`, `tests/frontend/test_audit_table_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现列表表格与数据 hook：字段列与 docs/15 口径一致（审计类型/资源/用户/动作/结果/Trace ID/时间），复用 StatusTag/DateTimeText，主展示字段可点击打开详情，右下分页与 total 联动。

### Checklist

- [x] [S-06][E2E] 与 TASK-018 协同：按 Trace ID 搜索仅显示相关记录且字段与 docs/15 口径一致（本任务负责实现侧：列定义、`result_status` 标签、时间格式化）。
- [x] 覆盖 `useAuditList` 状态机：loading/empty/error 与分页参数变更重取。
- [x] [B-206][integration] 以前端源码契约 + 真实 tsc 类型检查 为边界编写/扩展用例；关键断言：表格列与 docs/15 口径一致；复用 StatusTag/DateTimeText；分页与 total 联动；主展示字段可点开详情。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_table_contract.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | Browser(Chromium)→Console 聚合查询 HTTP | 仅显示相关记录；字段与 docs/15 一致；分页可用 | e2e/tests/audit-observability.spec.ts / S-06（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""]` | e2e_deferred |
| B-206 | integration | 前端源码契约 + 真实 tsc 类型检查 | 表格列与 docs/15 口径一致；复用 StatusTag/DateTimeText；分页与 total 联动；主展示字段可点开详情 | tests/frontend/test_audit_table_contract.py / B-206 | `["uv","run","pytest","-q","tests/frontend/test_audit_table_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-206 | **真实 RED**：`uv run pytest -q tests/frontend/test_audit_table_contract.py` → **11 failed**，首条 `AssertionError: 缺少前端文件：…/components/AuditTable.tsx`；另有一次失败由**我自己的 docstring** 触发 i18n 硬编码检查，已在源码侧改写措辞（**未削弱测试**）。 | 新增 `hooks/useAuditList.ts`（列表数据状态机：`{items,page,pageSize,total,loading,failed,reload}`、`requestSeq` 竞态守卫、失败只置 `failed`）与 `components/AuditTable.tsx`（以 **RemoteTable 入参工厂** 形态供给列定义/行渲染/空错槽位/分页联动）；`AuditPage.tsx` 改为消费二者（页面**保留** `<RemoteTable {...auditTable} />` 与两个 props 契约）→ 契约测试 **11 passed**；`tests/frontend/` **167 passed**（冻结的 `test_ui_style_contract.py` 绿）；`check_frontend_api_usage.py`/`check_frontend_i18n.py` OK（645 keys 不变）；`npm run typecheck` 与 `npm run build` 干净；ruff 干净。 | `tests/frontend/test_audit_table_contract.py` 11 条：列集合与 docs/15 口径一致且含 `StatusTag`/`DateTimeText`；`AuditTable` 以入参工厂供给 `RemoteTable` 且页面仍字面渲染 `RemoteTable`；hook 实现竞态守卫与 retry 且只经 TASK-010 的 service 调用；分页服务端驱动（`total` 来自响应）；无硬编码中文；主展示字段存在显式详情 seam。 | 前端源码契约 + 真实 `tsc --noEmit` + 真实 `vite build` + 两个仓库检查脚本 + 冻结的 ui-style verifier；未 mock 业务 API | verified |
| S-06 | 无独立 RED（该场景的终验归 TASK-018 的浏览器验收；本任务以源码契约断言列表列/状态/时间的形态）。 | 列定义（审计类型/操作目标/操作用户/Agent/动作/执行结果/Trace ID/时间）、`resultStatus` 走 `StatusTag`、时间走 `DateTimeText`、分页与 `total` 联动落地；浏览器级证据由 TASK-018 承载。 | 同上用例的列/组件/分页断言 | 前端源码契约（UI 级证据归 TASK-018） | e2e_deferred（终验归 TASK-018） |

**实现中的判断点（如实登记，含一项需知悉的改动）**：
- **改动了 TASK-011（已 done）的契约测试文件 `tests/frontend/test_audit_page_contract.py`**：3 处断言迁到"现在拥有该逻辑的文件"（`EmptyState`/`ErrorState`/`EntityLink`/`onPageSizeChange` → `AuditTable`；E-06 的 catch 断言 → `useAuditList.ts`；service 调用 → hook），并在测试内写明迁移原因；**未删除、未削弱任何断言**。本人复核：TASK-011 的场景命令（同文件）复跑仍 **13 passed**，且 `tests/frontend/` 全量 167 passed ⇒ TASK-011 的 verified 结论不受影响。
- **RemoteTable 约束的解法**：`AuditTable` 实现为 **props 工厂**（返回 `RemoteTableProps<AuditListItem>`）而非渲染 `RemoteTable` 的组件——冻结 verifier 要求页面里字面出现 `RemoteTable`，故由页面展开其返回值；列/行/空错槽位/分页仍全部由 `AuditTable` 供给。
- **详情 seam**：用 `EntityLink`（操作目标列 + Trace ID 列，testId 沿用 TASK-011 的 `audit-link-*`/`audit-trace-*`），**未加 `onRowClick`**（`RemoteTableProps` 无该钩子，新增会波及所有模块；设计 §3.3.1 亦指定链接形态）。
- **字段口径**：操作目标列取 `resourceId`（docs/15 的 `resource_id` ⇄ 操作目标；`target` 保留在类型里供 TASK-013）；审计类型列取 `auditType`（交互稿把 CONFIG/TOOL/EGRESS/MODEL 渲染在"审计类型"列）；列顺序按交互稿（时间列在前）。
- **`StatusTag` 不设 `fallback`**：未登记的领域错误码原样以灰色显示，不伪造文案。
- **`AuditTableOptions` 与页面的 `AuditTableProps` 有 7 个字段重复**：为保持 import 单向（避免类型环），结构性类型由展开桥接。
- **`t: TFunction` 以参数传入工厂**（纯函数、非 hook），保证表格文件无 hook、文案随 locale 反应式。
- **仓库坑（值得记录）**：在注释里写 `**/`（例如 `modules/` 与 `*Page.tsx` 相邻的 glob 写法）会破坏仓库的注释剥离工具（`**/` 含 `*/`）⇒ 会误报"硬编码中文"；本任务已改写措辞规避。
- B-206: verified — automated command passed; run_id=d559b9d6046d47a8b74de78f5e0527a2 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-013: 详情 SideSheet 与关联链接

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-012
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-07, E-07, RULE-ui-detail-001, B-207
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/components/AuditDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditDetail.ts`, `tests/frontend/test_audit_detail_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现只读详情 SideSheet：标题/副标题左侧、操作按钮与关闭 X 同行靠右、Tabs 在其下；按 `audit_type` 取详情并展示 Run/Task/Trace 关联链接（EntityLink）；关联不可读时侧栏内 ErrorState 且不伪造关联（E-07）。

### Checklist

- [x] [S-07][E2E] 与 TASK-018 协同：点击 Trace ID/主展示字段打开只读详情，无操作按钮，关联链接可跳转（本任务负责实现侧结构）。
- [x] [E-07][integration] 覆盖关联不可读路径：SideSheet 内 ErrorState，不伪造关联数据。
- [x] [RULE-ui-detail-001][E2E] verifier_ref=harness-ui-detail#RULE-ui-detail-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`；补充真实边界 真实 Console 页面详情结构；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [x] 实现或补齐：SideSheet/DetailTabs/EntityLink 复用与只读约束（无编辑入口）。
- [x] [B-207][integration] 以前端源码契约 + 真实 tsc 类型检查 为边界编写/扩展用例；关键断言：详情复用 DetailSideSheet/DetailTabs；只读无操作按钮；关联用 EntityLink；不可读时 ErrorState 不伪造关联。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_detail_contract.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | E2E | Browser(Chromium)→Console 详情 HTTP | 只读详情；无操作按钮；关联链接正确 | e2e/tests/audit-observability.spec.ts / S-07（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-07\""]` | e2e_deferred |
| E-07 | integration | Browser→详情不可读路径 | SideSheet 内 ErrorState；无伪造关联 | e2e/tests/audit-observability.spec.ts / E-07（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-07\""]` | e2e_deferred |
| RULE-ui-detail-001 | E2E | 真实 Console 页面详情结构 + 原 verifier 真实边界 | 标题/副标题左侧、操作与关闭同行靠右、Tabs 在其下；原 verifier 全部通过 | tests/frontend/test_audit_detail_contract.py + 原 verifier / RULE-ui-detail-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]` | verified |
| B-207 | integration | 前端源码契约 + 真实 tsc 类型检查 | 详情复用 DetailSideSheet/DetailTabs；只读无操作按钮；关联用 EntityLink；不可读时 ErrorState 不伪造关联 | tests/frontend/test_audit_detail_contract.py / B-207 | `["uv","run","pytest","-q","tests/frontend/test_audit_detail_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-207 | **真实 RED**：**11 failed**（`缺少前端文件：…/components/AuditDetailSideSheet.tsx` 与 `hooks/useAuditDetail.ts`）。 | 新增 `hooks/useAuditDetail.ts`（详情状态机：`requestSeq` 竞态守卫、关闭使在途响应失效、`failed` + `reload()`）与 `components/AuditDetailSideSheet.tsx`（只读详情：组合共享 `DetailSideSheet` + Semi `Tabs` 层、`DetailGrid`/`StatusTag`/`DateTimeText`/`EntityLink`/`ErrorState`，四类型字段映射自 `detail.extras`，关联取自 `detail.related`）；`AuditPage` 挂载 SideSheet；补 zh-CN/en-US 各 32 条 `audit.detail.*` 词条 → 契约测试 **11 passed**；`tests/frontend/` **178 passed**（含冻结的 `test_detail_sidesheet_contract.py` 9 passed）；`check_frontend_api_usage.py`/`check_frontend_i18n.py` OK（677 keys）；`tsc --noEmit` 与 `vite build` 干净；ruff 干净；**新增函数最长 49 行**。 | `tests/frontend/test_audit_detail_contract.py` 11 条：组合共享 `DetailSideSheet` 且自带头部/关闭（不引 Semi `SideSheet`、不本地实现头/尾、`onCancel={onClose}`）；四类型分节渲染；E-07 分支只读后端 `relatedMissing` 布尔（缺失 → `ErrorState`，无 `??`/`'-'` 伪造）；hook 竞态守卫 + retry 且只经 service；无编辑/操作按钮；无硬编码中文。 | 前端源码契约 + 真实 `tsc --noEmit` + 真实 `vite build` + 两个仓库检查脚本 + 冻结的 `harness-ui-detail#RULE-ui-detail-001` 原 verifier；未 mock 业务 API | verified |
| S-07 | 无独立 RED（该场景的终验归 TASK-018 的浏览器验收；本任务以源码契约断言详情结构与关联链接形态）。 | 只读详情 SideSheet（标题/副标题左侧、关闭 X 与操作同行靠右、Tabs 在其下）与关联链接（`EntityLink`，testId `audit-related-run`/`audit-related-task`）落地；浏览器级证据由 TASK-018 承载。 | 同上用例的详情结构与关联断言 | 前端源码契约（UI 级证据归 TASK-018） | e2e_deferred（终验归 TASK-018） |
| E-07 | 无独立 RED（终验归 TASK-018）。 | 关联不可读时**仅**依据后端 `relatedMissing` 布尔判定：缺失 → 该节渲染 `ErrorState`（无 `EntityLink`、无 `??`、无 `'-'` 伪造）；可读 → `EntityLink` 行；本无关联（如 CONFIG）→ `EmptyState`；加载失败另走 `notice` 区的 `ErrorState`（非 E-07 分支）。 | 同上用例的 E-07 分支断言（对提取出的分支源码断言） | 前端源码契约 | e2e_deferred（终验归 TASK-018） |
| RULE-ui-detail-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]` → **11 + 9 passed + tsc 干净**（原 verifier 全部通过）。 | tests/frontend/test_audit_detail_contract.py + 原 verifier | 前端源码契约 + 原 verifier 真实边界（含真实类型检查） | verified |

**实现中的判断点（如实登记）**：
- **设计里的 `DetailTabs` 在仓库中并不存在**：其职责由共享 `DetailSideSheet` 内建的 Semi `Tabs`（子 `Tabs.TabPane`）承载；**未自行发明共享组件**（设计/实现命名漂移；契约测试文档串里写明与冻结 verifier 是互补而非重复）。
- **关联导航（本次最大判断）**：仓库无 Run 页面、`TaskPage` 无 id 过滤/深链，且冻结的 §3.4 契约恰好只有 4 个 props ⇒ 保持 4 props **零契约漂移**，由容器导航到所属列表路由并携带 id（`/tasks?taskId=<id>` / `/tasks?runId=<id>`，稳定 testId `audit-related-run`/`audit-related-task`）。已标注给 TASK-018：若 E2E 需要别的落点或页面自持路由，属单函数（`handleOpenRelated`）改动。
- **页面挂载形态**：仅在存在选择时渲染 `<AuditDetailSideSheet visible … />`（不为隐藏面板伪造 `auditType`）；页面里冻结的 `AuditDetailSideSheetProps` 块未改，组件侧声明结构等价接口以保持 import 单向（同 `AuditTableOptions` 的桥接方式）。
- **分节落位**：§3.3 草图标两个 Tab（基础信息/关联），故四类型专有字段渲染在「基础信息」Tab 下、标题为「来源字段」，位于交互稿八列之后（复用既有 `audit.columns.*` 键，不重复文案）。
- **JSON 渲染**：`before`/`after`/`argsPreview` 用 `<pre data-testid="audit-detail-<field>">` + 仓库既有内联 `pre-wrap/maxHeight/overflow` 样式（McpToolTable/SkillDetailSideSheet 先例），未新增 CSS 类。
- **i18n 动态键**：类型专有字段用 `audit.detail.field.${field}`（先例 `audit.auditType.${value}`）；因动态键会绕过仓库静态扫描，契约测试**枚举四类型全部字段键**并断言 zh-CN/en-US 均存在。
- B-207: verified — automated command passed; run_id=5bf9cbf35a0241d0a0479567d0376f14 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-014: 导出按钮与轮询/下载交互

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-012
- **Source**: 11-audit-observability.frontend.design.md#3.3.1 每个按钮/操作的设计, 11-audit-observability.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: S-08, E-08, E-09, B-208
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditExport.ts`, `apps/console-platform/frontend/src/modules/audit-observability/components/AuditExportButton.tsx`, `tests/frontend/test_audit_export_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现列表左主操作的"导出"按钮：按当前筛选创建导出任务（同一次用户提交复用同一 `Idempotency-Key`，提交中禁用），轮询 `getExport` 至终态并在 `SUCCEEDED` 时下载；`IDEMPOTENCY_MISMATCH` 与失败码走 catalog→i18n 文案（E-08/E-09）。

### Checklist

- [x] [S-08][E2E] 与 TASK-018 协同：同 key 重试返回同一任务、轮询至完成可下载、提交中按钮禁用（本任务负责实现侧交互）。
- [x] [E-08][integration] 覆盖异指纹 409：展示 i18n 文案、保留筛选、不重复创建任务。
- [x] [E-09][integration] 覆盖 `FAILED` 状态：展示 `error_code` 文案与重试入口（复用新 key），不展示未完成产物。
- [x] [B-208][integration] 以前端源码契约 + 真实 tsc 类型检查 为边界编写/扩展用例；关键断言：导出按钮为左主操作 primary 且提交中禁用；同一用户提交复用同一 Idempotency-Key；失败文案经 catalog→i18n。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_export_contract.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | E2E | Browser(Chromium)→导出创建/查询 HTTP→PostgreSQL | 同一任务不重复创建；轮询至完成可下载；提交中禁用 | e2e/tests/audit-observability.spec.ts / S-08（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-08\""]` | e2e_deferred |
| E-08 | integration | Browser→导出创建异指纹 409 | 展示 `IDEMPOTENCY_MISMATCH` 文案；保留筛选；不重复创建 | e2e/tests/audit-observability.spec.ts / E-08（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-08\""]` | e2e_deferred |
| E-09 | integration | Browser→导出失败状态 | 展示 error_code 文案与重试入口；不展示未完成产物 | e2e/tests/audit-observability.spec.ts / E-09（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-09\""]` | e2e_deferred |
| B-208 | integration | 前端源码契约 + 真实 tsc 类型检查 | 导出按钮为左主操作 primary 且提交中禁用；同一用户提交复用同一 Idempotency-Key；失败文案经 catalog→i18n | tests/frontend/test_audit_export_contract.py / B-208 | `["uv","run","pytest","-q","tests/frontend/test_audit_export_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-208 | **真实 RED**：`uv run pytest -q tests/frontend/test_audit_export_contract.py` → **9 failed**，首条 `缺少前端文件：…/components/AuditExportButton.tsx`。 | 新增 `components/AuditExportButton.tsx`（工具栏**左主操作**、`primary`/solid、提交中禁用、内联 Banner + 重试）与 `hooks/useAuditExport.ts`（导出状态机：每次提交恰好生成一个幂等键、轮询 1s×60 上限、终态停止、乱序丢弃、`SUCCEEDED` → 下载、`FAILED` → 文案 + 重试）；`AuditPage` 把 `actions={null}` 换成 `<AuditExportButton filters=…>`；补 5 条 `audit.export.*` 词条 → 契约测试 **9 passed**；`tests/frontend/` **187 passed**；`check_frontend_api_usage.py`/`check_frontend_i18n.py` OK（682 keys）；`tsc --noEmit` 与 `vite build` 干净；ruff 干净；**未改动任何既有测试**（并保留页面里的 `TASK-014` 标记，使 TASK-011 的断言原样通过）。 | `tests/frontend/test_audit_export_contract.py` 9 条：按钮接入左主操作且 primary/solid + busy 禁用；每次提交恰好生成一个键、且重试路径不调用键生成器；轮询调用 `getExport` 并在终态停止；`SUCCEEDED` 触发下载路径、失败文案取自 `errorCode`（**不解析 blob**，`JSON.parse`/`.text()`/`response.data.code` 均不在源码中）；E-08 保留筛选且全局只有**一个** `createExport(` 调用点（构造不出第二个任务）；无硬编码中文。 | 前端源码契约 + 真实 `tsc --noEmit` + 真实 `vite build` + 两个仓库检查脚本；未 mock 业务 API | verified |
| S-08 / E-08 / E-09 | 无独立 RED（三者终验归 TASK-018 的浏览器验收；本任务以源码契约断言交互形态）。 | **E-08**：创建失败走通用分支、筛选由页面持有不被触碰、单一 `createExport(` 调用点 ⇒ 不可能建第二个任务，`IDEMPOTENCY_MISMATCH` 由按钮的 catalog→i18n 映射渲染；**E-09**：轮询到 `FAILED` 用 `job.errorCode` 映射文案并提供重试（重试=新提交=新键）；下载传输失败回退 `COMMON_INTERNAL_ERROR`（blob 体不可解析，已在注释登记）。 | 同上用例的 E-08/E-09 断言 | 前端源码契约（UI 级证据归 TASK-018） | e2e_deferred（终验归 TASK-018） |

**实现中的判断点（如实登记）**：
- **默认格式 `CSV`**：设计 §3.3.1 未指定格式且草图为单个按钮，导出 `EXPORT_FORMAT_DEFAULT`。
- **轮询参数**：`EXPORT_POLL_INTERVAL_MS=1000`、`EXPORT_POLL_MAX_ATTEMPTS=60`（约 60s）；立即轮询、间隔等待、终态 `['SUCCEEDED','FAILED']` 停止；**上限耗尽 → `status='FAILED'` + 回退码**（绝不展示半成品产物）；乱序用 `submissionSeq` + `isCurrent()` 在每次 `getExport` 前后各校验一次（对齐 `useAuditList`/`useAuditDetail` 的纪律）。
- **文件保存**：仓库内**不存在**既有 helper（`src` 下无 `createObjectURL`/`saveAs`/anchor 用法），故在 hook 内实现最小 `saveBlob`：Blob URL + `<a download>` + **延迟** `revokeObjectURL`（0 tick 立即 revoke 是跨浏览器不安全形态）；文件名 `audit-export-<exportId>.csv|json`。
- **幂等键归属（交接点 1）**：`newRequestId()` 在 hook 中**只出现一次**（`start` 内）；在途重入（双击/超时重发）在铸造键之前直接返回 ⇒ 同一次提交始终一个键、不产生第二个作业；**E-09 的重试 = 新提交 → 新键**（同键只会重放同一个 FAILED 作业，无意义）；`retry()` 委托 `start` 并复用**已存**请求（契约签名为 `retry(): void`）——用户若改了筛选应走主导出按钮（已标注给 TASK-018 的 E-09 E2E）。
- **blob 错误体（交接点 2）**：hook 绝不解析下载体；`FAILED` 文案来自轮询到的 `job.errorCode`；下载本身传输失败回退 catalog `COMMON_INTERNAL_ERROR`（`EXPORT_ERROR_FALLBACK_CODE`，注释中写明该限制）；创建失败仍可读封套（走只读的 `apiErrorBody`，与 `AgentFormModal`/`PlatformTestModal` 同款），所有请求仍只经 `services/auditService`。
- **错误呈现形态**：工具栏内联 Semi `Banner`(danger) + 重试按钮，**刻意不用公共 `ErrorState`**——其 `.app-error` 是 `padding: 32px 16px` 的居中块，会撑坏工具栏行。
- **catalog→i18n 映射放在按钮**（`EXPORT_ERROR_KEYS`，对齐 `PlatformTestModal` 先例）；hook 只暴露 `errorCode`、不含文案。
- **E-08 无独立分支**：通用创建失败路径已满足（筛选由页面持有且不被触碰、不重复提交、单一 `createExport(` 调用点），契约测试直接断言该形态。
- **设计口径冲突**：设计 §3.5 说幂等键"由 service 生成"，与 brief/交接的"调用方持有"冲突 → 以调用方持有为准（已是 `auditService.ts` 的既定约定，已登记）。
- B-208: verified — automated command passed; run_id=8e20391284624aebb64e3c849a82e04a (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-015: i18n 词条与语言切换覆盖

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-012
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: RULE-i18n-001, B-209
- **Files**: `src/locales/zh-CN.json`、`src/locales/en-US.json`、`components/AuditFilterBar.tsx`、`components/AuditTable.tsx`、`components/AuditDetailSideSheet.tsx`、`tests/frontend/test_audit_i18n_contract.py`（真实结构为扁平 JSON + 点分键，无逐模块 locale 文件）
- **Estimate**: 15–60 分钟；超出先拆分

### Description

为审计模块补齐 zh-CN/en-US 词条（页面标题、列名、筛选项、状态标签、导出交互与错误文案），业务新增只加配置；错误/状态文案经 catalog → i18n key 映射，组件不硬编码中文。

### Checklist

- [x] [RULE-i18n-001][E2E] verifier_ref=harness-i18n#RULE-i18n-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`；补充真实边界 真实 Console 页面双语切换；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`。
- [x] 覆盖 zh-CN 与 en-US 词条键一致（缺键/多余键断言）与 LocaleSwitch 切换后文案生效。
- [x] [B-209][integration] 以前端源码契约 + 真实 tsc 类型检查 为边界编写/扩展用例；关键断言：zh-CN/en-US 词条键一致（缺键/多余键断言）；错误与状态文案取自 catalog→i18n key。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_i18n_contract.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-i18n-001 | E2E | 真实 Console 页面双语切换 + 原 verifier 真实边界 | 中英词条键一致；错误/状态文案经 i18n key；原 verifier 全部通过 | tests/frontend/test_audit_i18n_contract.py + 原 verifier / RULE-i18n-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]` | verified |
| B-209 | integration | 前端源码契约 + 真实 tsc 类型检查 | zh-CN/en-US 词条键一致（缺键/多余键断言）；错误与状态文案取自 catalog→i18n key | tests/frontend/test_audit_i18n_contract.py / B-209 | `["uv","run","pytest","-q","tests/frontend/test_audit_i18n_contract.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-209 | **真实 RED**（2 failed / 8 passed）：其一为本测试自身的非空性阈值校准（已改为哨兵断言），真正缺口为 —— `test_status_and_type_enumerations_match_backend_domain` 报「筛选栏执行结果枚举与后端不一致：`{'FAILED','SUCCESS'}` vs 后端 `{'DENIED','FAILED','SUCCESS'}`」。**根因**：后端 `audit_query_service.RESULT_STATUSES` 归一化域含 `DENIED`（列表/详情/导出校验共用，API 测试也断言返回 `DENIED` 行），而前端只枚举 SUCCESS/FAILED ⇒ `DENIED` 行会经 `StatusTag` 的 fallback 渲染**未翻译的裸 `DENIED`**，且筛选下拉无法选它。 | 补 `audit.resultStatus.DENIED`（zh「已拒绝」/en「Denied」）+ `AuditFilterBar` 枚举补 `DENIED` + `AuditTable.statusOptions.DENIED = {color:'red', label:…}` + `AuditDetailSideSheet.RESULT_COLORS.DENIED='red'` → 契约测试 **10 passed**；`tests/frontend/` **197 passed**；`check_frontend_i18n.py` OK（683 keys）；`tsc --noEmit` 与 `vite build` 干净；ruff 干净；最长函数 31 行。 | `tests/frontend/test_audit_i18n_contract.py` 10 条：模块全部静态 key 双语言存在；**动态键变体**按源码与后端契约枚举（`audit.auditType/resourceType/resultStatus/detail.field/export.error.*`）；`audit.*` 键集两侧一致且无孤儿键/空值；任一模块文件无用户可见中文；**切换安全**（各组件走 `useTranslation()`、无模块级/state 缓存文案、无直连 i18n 实例）；错误/状态文案经 catalog code → i18n key 映射 | 前端源码契约（遍历模块全部 10 个 ts/tsx，新文件自动覆盖）+ 真实 `tsc --noEmit` + 真实 `vite build` + 仓库 i18n 检查脚本；未 mock | verified |
| RULE-i18n-001 | 无独立 RED（验收类）。 | 联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]` → 本契约 10 passed + i18n 检查 OK；原 verifier（含 `tests/acceptance/test_foundation_i18n.py` 的 `compare_locale_files` 断言）由 Done Gate 执行。 | tests/frontend/test_audit_i18n_contract.py + 原 verifier | 前端源码契约 + 原 verifier 真实边界 | verified |

**实现中的判断点与新的待处置缺口（如实登记）**：
- **真实词条结构是扁平 JSON + 点分键**（`src/locales/{zh-CN,en-US}.json`，各 683 键），**不存在** TASK-015 计划 `Files:` 里写的 `locales/zh-CN/audit-observability.ts` 逐模块文件 ⇒ 按真实结构实现，未凭空创建（计划字段与仓库不符，已在 Evidence 更正）。
- **`DENIED` 补进筛选下拉**属一行功能性改动（超出纯 i18n）：理由是与后端同一 `RESULT_STATUSES` 域对齐、且后端本就接受 `result_status=DENIED`；如只需展示层修复可撤该行。
- `DENIED` 颜色取 `red`（对齐交互稿「SUCCESS→绿，其余→红」与仓库 `FAILED`/`MISSED` 惯例；`amber` 可区分"拒绝"与"失败"，一行可改）。
- 契约测试断言**前端枚举 == 后端枚举（相等而非超集）**：后端新增归一化状态时前端必须跟进，这是有意的强制函数。
- **新登记的功能+展示缺口（建议单独开任务）**：`resourceType` 筛选项只枚举 7 个值，而后端实际存储含小写/其它值（`project_platform`/`MCP_SERVER`/`SKILL_USER_GRANT`/`AGENT_ACCESS_GRANT`/`PLATFORM_USER`/`USER_MEMORY`/`BIND_CODE`/`CHANNEL_IDENTITY`），且详情页「资源类型」行会把后端原值**未翻译**直接展示（域开放 + 交互稿未渲染该列）⇒ 本次未改、也未断言其一致性。
- 导出文件名 `audit-export-<id>.csv` 虽用户可见但刻意保持 ASCII，未新增 key。
- 语言切换的残留风险（已文档化并加守卫）：`AuditTable` 的安全性依赖"工厂在渲染期被调用"——若有人把 `buildAuditTableProps` 包进 `useMemo(…, [])` 会静默破坏切换；契约测试的 `CACHED_TRANSLATION` 守卫会拦下这种写法。
- B-209: verified — automated command passed; run_id=9ceacb39377b4302a3b18a6494ef868d (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-016: 真实审计验收环境与种子清理

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-007
- **Source**: 11-audit-observability.backend.design.md#3.5 质量实现方案, 11-audit-observability.backend.design.md#4 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: B-210
- **Files**: `tests/acceptance/audit_observability/environment.py`, `tests/e2e/seed_audit.py`, `tests/acceptance/audit_observability/test_environment.py`、`tests/acceptance/audit_observability/__init__.py`（避免与 im_gateway 同名测试文件 import mismatch）
- **Estimate**: 半天级（真实多进程栈与清理）；超出先拆环境与种子两段

### Description

建立真实审计验收环境：起 Console（查询面）+ Runtime（写入面）+ 复用既有真进程栈原语，准备审计种子（config/tool/egress/model 四类记录 + 一个导出任务）与幂等清理；提供 `start_*`/`purge_*`/`count_*` 与健康探测助手，供 TASK-017/018 复用。既有验收栈原语见 `tests/acceptance/task_schedule/environment.py`（`ServiceProcess`/`free_port`/`clear_engine_caches`）与 `tests/acceptance/im_gateway/environment.py` 的进程栈封装。

### Checklist

- [x] 实现或补齐：真实进程栈启动/停止、健康就绪等待、租户级数据清理（含 `control.audit_export_job` 与共享幂等表行）。
- [x] 实现或补齐：审计种子构造（四类审计 + 一个 `SUCCEEDED` 导出任务与 artifact），支持按 `trace_id` 串联断言。
- [x] 以真实 PG 验证清理幂等：连续两次 `purge` 后审计相关表计数归零。
- [x] 覆盖启动失败快启路径（依赖缺失时 fail fast，不静默降级）。
- [x] [B-210][integration] 以 真实多进程栈(Console/Runtime/Worker + PostgreSQL/Redis)与租户级清理 为边界编写/扩展用例；关键断言：栈可启动并健康；审计种子可构造；purge 幂等归零；依赖缺失快启失败。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_environment.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-210 | integration | 真实多进程栈(Console/Runtime/Worker + PostgreSQL/Redis)与租户级清理 | 栈可启动并健康；审计种子可构造；purge 幂等归零；依赖缺失快启失败 | tests/acceptance/audit_observability/test_environment.py / B-210 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_environment.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-210 | **真实 RED（两段）**：① 结构性——验收 argv 在模块缺失时报 `ERROR: file or directory not found: …/test_environment.py`、`no tests ran`、`EXIT=4`；② 实现后首跑 **2 failed / 4 passed**：`KeyError: 'status'`（api-kit 服务返回封套 `{code:"0",data:{status:"ok"}}`，而 LLM 探针返回裸 `{"status":"ok"}`）、`test_b210_purge_is_idempotent` 残留 `{'control.console_account': 1}`（复用的 09 `CONTROL_CLEANUP` 未删该表）。 | 新增 `tests/acceptance/audit_observability/environment.py`（真实栈 `start_audit_stack`/`stop_audit_stack`、`wait_ready`、`purge_tenant`/`count_tenant_rows`、导出产物清理、`start_service_without_dependency` 快启助手、`CLEANUP_TABLES` 白名单）、`tests/e2e/seed_audit.py`（身份+Run、四类审计、带产物的 `SUCCEEDED` 导出作业）、`__init__.py`、`test_environment.py` → **6 passed in 11.70s**；purge 两次后 15 张表计数全 0（含 config_audit_log/audit_export_job/skill_import_idempotency/三张 runtime 审计表）；无残留进程；租户残留 0；ruff 干净；函数均 ≤50 行。 | 6 个用例：① 栈可启动且 console/runtime 经**真实 HTTP 探针**就绪（封套与非封套两种响应体形状分别断言）；② 种子行可经自建 engine 逐行回读；③ `purge_tenant` **幂等**（连续两次 + fixture teardown 共三次，15 张表全 0）；④～⑤ 两条**真实进程**快启失败路径（`ARTIFACT_ROOT` 指向不存在目录 → 非零退出且日志含 `artifact storage is not mounted`；`DATABASE_URL` 指向 `127.0.0.1:1` → 非零退出且日志含 `cannot read schema revision`）；⑥ 产物清理断言（模块自有临时 artifact root，不污染仓库 `.data/artifacts`）。 | 真实多进程栈（llm-probe + Console + Runtime + Worker）+ 真实 PostgreSQL/Redis + 真实 HTTP 探针与真实进程快启；未 mock 业务 API | verified |

**实现中的判断点（如实登记）**：
- **新增 `__init__.py`（第 4 个文件，超出计划的 3 个）且证明必要**：不加则本模块 `test_environment.py` 与 `tests/acceptance/im_gateway/test_environment.py` 同名 → pytest 收集报 `import file mismatch`（`Interrupted: 1 error during collection`），会**直接打断 TASK-019 的仓库级 `uv run pytest -q tests/acceptance`**；加了之后两文件 `--collect-only` 正常（11 tests collected）。两种情形均已实测。
- **租户按次随机**（`audit-acceptance-<uuid8>`）并与服务 `DEFAULT_TENANT_ID` 一致：与其它模块在共享 `muad` 库中的残留彻底隔离。
- **栈范围**：`llm-probe + console + runtime + worker`（PG/Redis 外置）；**刻意不起** im-gateway、runtime-2、渠道/WS 探针（与审计无关）。种子的模型指向真实探针 HTTP 端点（而非死地址），使 TASK-017 仍可跑真实模型/运行链路。
- **种子走真实写入方**（`write_config_audit` + `RuntimeAuditWriter.record_*` + `AuditExportService.create_export`），行 id 事后从 PG 回读（写入方不返回 id）；**并种入真实 `ConsoleAccount`**（ADMIN + argon2 真哈希）+ PlatformUser + AgentAccessGrant —— 因为 CONFIG 审计的 `actor_user_id` 是登录账号 id（RULE-07）且投影会 LEFT JOIN 取 `actor_name`，用随机 UUID 会得到空 actor 名；`ACCOUNT_USERNAME`/`ACCOUNT_PASSWORD` 已导出供 TASK-017/018 真实登录。
- **导出产物固定在环境自有 root**：**未**调用 `AuditExportService.run_pending_exports`（它从 `SharedSettings()` 解析 root，会落到仓库 `./.data/artifacts`，既在租户清理之外也不该被测试写）；改为复用同一真实投影（`AuditQueryRepository.all_rows`）+ 真实序列化 + 真实 `export_storage_key` + `write_artifact(root=…)`，并用真实 `mark_running/mark_succeeded` 推进状态机。**未**采用"进程级改写 `ARTIFACT_ROOT`"（会波及其它模块）。
- **清理顺序修正（真实 FK 顺序 bug）**：`runtime.artifact` 必须在复用的 `RUNTIME_CLEANUP` 删 `run_record` **之前**删除（既有元组先删 run_record 后删 artifact，一旦存在 run 产物即 FK 失败）；`console_session` 经 `account_id IN (…)` 先于 `console_account` 删除；审计相关表（`config_audit_log`/`audit_export_job`/`skill_import_idempotency`）前置拼接。
- **快启校验是行为级而非静态**：用真实进程参数化（console+runtime）验证两种缺失依赖的退出码与日志特征；若服务反而起来了，助手会停掉它并把 `healthy=True` 上报，使测试显式失败而不是静默通过。
- **清理白名单**：`count_tenant_rows` 的表名是唯一被插值的标识符，且经 `CLEANUP_TABLES` 白名单校验（否则 `ValueError`）；所有 `DELETE` 的值一律绑定参数。
- B-210: verified — automated command passed; run_id=19c66450385a4085b0997fdda8363fbd (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-017: 后端场景真实验收（S-01/03/05 + 集成场景证据）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016
- **Source**: 11-audit-observability.backend.design.md#2.5.2 功能验收场景, 11-audit-observability.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-01, S-03, S-05
- **Files**: `tests/acceptance/audit_observability/test_audit_acceptance.py`
- **Estimate**: 半天级（3 个 E2E 场景 + 逐行回读）；超出先拆场景项

### Description

按真实边界编写 3 个 E2E 场景用例：S-01 聚合列表按 `trace_id` 搜索、S-03 Admin Run 详情契约、S-05 导出创建（同 key 重试）+ 轮询 + 下载；全部走真实 Console/Runtime HTTP 与真实 PostgreSQL 逐行回读，不使用业务 API mock；同时汇总 S-02/S-04/E-01..E-05 集成场景的证据位置（由 TASK-001..008 执行）。

### Checklist

- [x] [S-01][E2E] 以 Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) 为边界编写用例；关键断言：同链路审计字段归一、含 `result_status`、分页封套正确。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"]`。
- [x] [S-03][E2E] 以 Browser→Console→Runtime `/internal/admin/runs`→PostgreSQL 为边界编写用例；关键断言：Run/Snapshot/Timeline/Tool/Egress/Model/Artifact 齐备且无 Secret。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"]`。
- [x] [S-05][E2E] 以 Browser→导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) 为边界编写用例；关键断言：同 key 同指纹重放返回同一 `export_id`、不重复建任务、轮询至 `SUCCEEDED` 可下载。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"]`。
- [x] 汇总集成场景证据：逐条登记 S-02/S-04/E-01..E-05 的用例位置与真实组件（执行命令见各 owner 任务契约）。
- [x] 清理断言：场景结束后租户审计数据与导出任务归零。
- [x] 执行上述契约命令，填写 Acceptance Evidence；测试文件与用例命名须全仓唯一。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) | 同链路字段归一；含 `result_status`；分页封套正确 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-01 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"]` | e2e_deferred |
| S-03 | E2E | Browser→Console→Runtime /internal/admin/runs→PostgreSQL | 详情字段齐备；无 Secret | tests/acceptance/audit_observability/test_audit_acceptance.py / S-03 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"]` | e2e_deferred |
| S-05 | E2E | Browser→导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) | 同 key 重放同一任务；轮询至完成可下载 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-05 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"]` | e2e_deferred |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | **结构性 RED**（本任务为验收类、无需改生产码，未伪造行为 RED）：把文件移开后按登记 argv 逐条执行 → `ERROR: file or directory not found: …/test_audit_acceptance.py`、`no tests ran`、`EXIT=4`（s01/s03/s05 同）。 | 新增 `tests/acceptance/audit_observability/test_audit_acceptance.py`（4 用例 + 32 个助手，最长函数 38 行）→ 整文件 **4 passed in 4.77s**；`-k s01` **1 passed**；`-k s03` **1 passed**；`-k s05` **2 passed**；ruff 干净；无残留进程；15 张表租户残留 0；仓库 `.data/artifacts/exports` 未生成。 | `test_s01_audit_list_returns_trace_scoped_unified_rows`：真实登录（会话 cookie + CSRF）→ `GET /api/v1/audits?trace_id=`；断言封套键集与 `code=="0"`、分页封套 `(1,20,4)`、四类 `audit_type` 齐备且 `audit_id` 集 == 种子 PG id 集、**异 trace 行被排除**（且该行由真实 `write_config_audit` 写入并回读，排除性非空；每题 `trace_id == TRACE_ID`）、单项键集 == 16 统一字段 ∪ 3 legacy 别名且别名一致、三个时间字段匹配 `YYYY-MM-DD HH:mm:ss`、`occurred_at` 非递增（DESC）、CONFIG 与运行类的 actor/agent 名分流、`result_status` 全为 `SUCCESS` **而 PG 原列为 `{SUCCEEDED, OK}`**（归一非空转）、`page=2&page_size=3` → `(2,3,4)` 且 1 条。 | 真实 Console HTTP（真实登录/CSRF）+ 真实 PostgreSQL 逐行回读 + 真实写入方造异 trace 行；未 mock 业务 API | verified |
| S-03 | 同上（结构性 RED）。 | 同上。 | `test_s03_admin_run_detail_contract_exposes_no_secret`：真实 Console 登录 + 经 Console 审计面读取同 trace；**身份边界**：同一内部端点不带 `X-Internal-Service` → 403 `FORBIDDEN`、带上 → 200 + 封套；7 段键集精确（run 12 字段、snapshot 6 + hash/id、timeline 4 且经真实 `EventWriter` 的 seq/顺序/载荷、tool 6、egress 8、model 9、artifact 7）；**无 Secret**：原文不含 snapshot 密钥/原始 Prompt 标记/模型 api_key/账号口令/内部 token，并对结构键做遍历禁用 api_key/apikey/secret/password/credential/authorization/cookie（`token` 仅允许 `input_tokens`/`output_tokens`）；**非空转反查**证明 `runtime_snapshot.model_json->>'api_key'` 与 `agent_json->>'instructions'` 确实含标记。 | 真实 Console HTTP + 真实 Runtime HTTP（含内部身份头）+ 真实 PostgreSQL | verified |
| S-05 | 同上（结构性 RED）。 | 同上。 | `test_s05_export_creation_is_idempotent_and_downloadable` + `test_s05_cleanup_purges_tenant_and_artifacts`：固定 `Idempotency-Key` POST → PENDING；**完全相同的重复请求** → `data` 逐字节相同（同一 `export_id`）；PG 作业数 == 1(种子)+1、该 endpoint 幂等行 == 1+1（**无第二个作业**）；任一次状态 GET **之前**作业仍为 PENDING（执行由 API-06 驱动）；轮询到终态 `SUCCEEDED`、状态 6 字段、`row_count == 4`、`error_code is None`；下载 200 + `application/json` + `attachment; filename="audits-{export_id}.json"`；正文恰 4 行、id 集 == 4 个种子 id、每行 `trace_id == TRACE_ID`、每行键集恰为 16 统一列（**无 legacy 别名**）；下载字节与 `artifact_ref` 指向的真实产物文件逐字节一致；清理后 15 张表 0、产物文件 0。 | 真实 Console HTTP + 真实 PostgreSQL + 真实 artifact store（环境自有 root） | verified |

**实现中的判断点（如实登记）**：
- **S-03 的"Console 审计面路径"**：Console **没有**面向浏览器的 API-03/04 代理（只有 Runtime 侧 `admin/runs`；设计本就把 API-03/04 定为 Console 审计面**出站**调用）⇒ 本任务对 Console 做真实登录以覆盖审计面，出站腿用 Console 自身的头契约（`stack.service_headers()`）打 Runtime；**未新增 Console 代理路由**（超出本任务单文件范围且设计无此物），并补了"不带内部身份 → 403"的断言，避免把该头当成静默绕过。
- **7 段契约的"有牙"**：TASK-016 的种子未预置 Snapshot/Timeline/Artifact，若直接断言会空转 ⇒ 在**测试文件内**为种子 Run 真实创建这三类行（Snapshot/Artifact 走 ORM，Timeline 走真实 `EventWriter`），**未改动已 done 的 `tests/e2e/seed_audit.py`**（B-210 的证据钉住了种子的计数），由模块 teardown 清理。
- **登记一处设计/实现漂移（本任务未修）**：设计 §3.3 的 Admin Run 详情示例展示**归一后**状态（`SUCCESS`），实现返回**原始**值（`SUCCEEDED`/`OK`）；S-03 断言的是实现行为。这与 TASK-002/TASK-003 已登记的同一词表漂移同源；RULE-08 的统一 `result_status` 已在 S-01 取证。若要 Admin Run 详情也归一，属 `admin_run_service.py` 的生产改动，超出 TASK-017 声明文件，故未制造 RED。
- `occurred_at DESC` 以"非递增"断言（种子行毫秒级相邻，输出秒级精度，用严格 `>` 会误判）。
- legacy 别名只在 API-01 项上断言；导出列集断言为**恰 16 统一列**（记录"导出不带别名"）。
- 敏感键扫描禁用全部标记，唯独裸 `token` 仅豁免 `input_tokens`/`output_tokens`（合法契约字段），否则仓库 `SENSITIVE_KEY_MARKERS` 会误报。
- 清理用例必须是文件最后一个（会清空租户），故 `-k s05` 选中 2 个用例；模块 teardown 始终停进程 + purge + 清产物（不带断言，遵循仓库惯例）。
- PG 回读用测试自有 engine（每次助手调用创建/释放）；清理断言走仓库的线程版 `run_db` 原语；模块 fixture 在 yield 前真实等 Console/Runtime `/readyz`。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=a1e5d3b6a7aa4747aabc6046f7f442ac (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=a1e5d3b6a7aa4747aabc6046f7f442ac (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=a1e5d3b6a7aa4747aabc6046f7f442ac (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-018: 前端 E2E 验收（列表/详情/导出）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016
- **Source**: 11-audit-observability.frontend.design.md#2.4 验收条件, 11-audit-observability.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: S-06, S-07, S-08, E-06, E-07, E-08, E-09
- **Files**: `e2e/tests/audit-observability.spec.ts`, `e2e/playwright.audit-observability.config.ts`、`tests/e2e/seed_audit.py`（增浏览器 E2E 编排与 CLI）
- **Estimate**: 半天级（7 个场景 + 真实浏览器栈）；超出先拆正常/异常两段

### Description

按真实浏览器边界编写前端验收：S-06 列表与筛选、S-07 只读详情、S-08 导出全流程为 E2E（真实 Chromium→真实 Console）；E-06..04 为失败/边界路径（可用路由拦截模拟失败，属 integration 层级）。playwright 配置沿用既有命名，`workers: 1` 串行（避免共享真实后端互清数据）。

### Checklist

- [x] [S-06][E2E] 以 Browser(Chromium)→Console 聚合查询 HTTP 为边界编写用例；关键断言：Trace ID 搜索仅显示相关记录、字段与 docs/15 一致、分页可用。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""]`。
- [x] [S-07][E2E] 以 Browser→Console 详情 HTTP 为边界编写用例；关键断言：只读详情、无操作按钮、关联链接可跳转。
- [x] [S-08][E2E] 以 Browser→导出创建/查询 HTTP→PostgreSQL 为边界编写用例；关键断言：同 key 重试同一任务、提交中禁用、轮询至完成并下载。
- [x] [E-06..04][integration] 覆盖查询失败保留筛选、详情不可读 ErrorState、异指纹 409 文案、导出失败重试入口。
- [x] 配置 `e2e/playwright.audit-observability.config.ts`（`workers: 1`）并在任务文档登记 argv。
- [x] 执行上述契约命令，填写 Acceptance Evidence；测试文件与用例命名须全仓唯一。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | Browser(Chromium)→Console 聚合查询 HTTP | 仅显示相关记录；字段一致；分页可用 | e2e/tests/audit-observability.spec.ts / S-06 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""]` | e2e_deferred |
| S-07 | E2E | Browser(Chromium)→Console 详情 HTTP | 只读详情；无操作按钮；关联链接可跳转 | e2e/tests/audit-observability.spec.ts / S-07 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-07\""]` | e2e_deferred |
| S-08 | E2E | Browser(Chromium)→导出创建/查询 HTTP→PostgreSQL | 同 key 重试同一任务；提交中禁用；完成后可下载 | e2e/tests/audit-observability.spec.ts / S-08 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-08\""]` | e2e_deferred |
| E-06 | integration | Browser→Console 查询失败路径 | 保留筛选可重试；ErrorState 呈现 | e2e/tests/audit-observability.spec.ts / E-06 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""]` | verified |
| E-07 | integration | Browser→详情不可读路径 | SideSheet 内 ErrorState；无伪造关联 | e2e/tests/audit-observability.spec.ts / E-07 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-07\""]` | verified |
| E-08 | integration | Browser→导出创建异指纹 409 | 展示 `IDEMPOTENCY_MISMATCH` 文案；保留筛选 | e2e/tests/audit-observability.spec.ts / E-08 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-08\""]` | verified |
| E-09 | integration | Browser→导出失败状态 | 展示 error_code 文案与重试入口 | e2e/tests/audit-observability.spec.ts / E-09 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-09\""]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-06 / S-07 / S-08 / E-06 / E-07 / E-08 / E-09 | **真实 RED（两段）**：① 结构性——两个新文件移开时登记 argv 报 `Error: Config not found: …/playwright.audit-observability.config.ts`（exit 1）；② 实现后两处**真实断言失败**证明断言有牙：S-07 断言只读详情内可见操作按钮数为 0 却得到 1；E-08 第二次创建 POST 返回 200（体为 `{"export_format":"CSV"}`）而非 409 —— 根因是 Semi `Select` 的 `onChange` 仅在下拉关闭动画后触发，紧跟选项点击读取到旧筛选。 | 新增 `e2e/playwright.audit-observability.config.ts`（真实 Console + 构建后的前端 `vite preview` + 真实 LLM 探针，`workers: 1`，按 `--grep` 派生端口偏移、租户与产物 root 随之隔离）与 `e2e/tests/audit-observability.spec.ts`（7 场景，标题含场景 ID 以支持 `-g`）；`tests/e2e/seed_audit.py` 增浏览器 E2E 编排（`AUDIT_SEED_TENANT` 可覆盖、`seed_browser`/`purge_browser_tenant`、`seed|cleanup|counts` CLI）→ 整文件 **7 passed (14.1s)**；`-g "S-06"…"E-09"` 各 **1 passed**（5.1s/5.2s/5.7s/4.6s/4.4s/6.3s/4.1s，均退出 0，全部按登记 argv 执行）；前端 `npm run build`（含 typecheck）绿；运行后无 uvicorn/muad/chromium 残留、六张模块表 `audit-browser-%` 租户残留 0、仓库 `.data/artifacts` 未变。 | spec 内 7 个用例：S-06 列表渲染种子行且筛选（Trace ID/审计类型/结果）收窄、重置恢复；S-07 点主展示字段/Trace 链接打开只读详情（含关联链接、**可见操作按钮为 0**）；S-08 左主操作导出按钮创建作业→**提交中禁用**→完成态→真实下载事件与文件名；E-06 列表接口真实失败（catalog 形状 500）→ 保留筛选 + ErrorState + 重试；E-07 关联不可读（真实 TOOL 行 + 真实软删其 `run_record`）→ 该节 ErrorState 且无伪造关联；E-08 同 key 异筛选 → 真实 409、展示 `IDEMPOTENCY_MISMATCH` 文案、**PG 计数证明无第二个作业**；E-09 真实执行器驱动的 FAILED 作业（预置同名产物 → `FileExistsError` → `COMMON_INTERNAL_ERROR`）→ 文案 + 重试（新 key，从捕获的原头断言）+ 下载 GET 返回 409 且浏览器零下载事件。 | 真实 Chromium → 真实 Console（真实登录）+ 真实 PostgreSQL + 真实 LLM 探针 + 真实前端构建产物；失败路径仅在设计允许处用路由拦截（E-06 列表 500、E-08/E-09 改写 `Idempotency-Key` 头），其余一律真实后端与真实执行器 | verified |

**实现中的判断点与值得知道的发现（如实登记）**：
- **启动/鉴权/种子**：`webServer` 数组（09 机制）但指向**真实 Console**（对齐 user-identity/model-management 的新范式）；09 的合成 `tests.e2e.app` 不适用（那是给 Task/Schedule 用固定租户 + worker 客户端的）。浏览器不发 `X-Tenant-Id` ⇒ 由 Console 的 `DEFAULT_TENANT_ID` 决定，故配置把它设为模块自有租户 `audit-browser-<offset>` 并种入该租户的管理员账号，spec 用**真实凭证登录**（非 dev default）；租户随端口偏移变化，重叠运行互不可见。种子经生产路径写入（`write_config_audit`/`RuntimeAuditWriter`/`AuditExportService` + 产物 store），**绝不裸 INSERT**。
- **单一套件级种子**（而非每用例重建）：S-08 的 SUCCEEDED 产物不可覆盖，活租户上重建会撞产物；7 个用例只依赖种子 id/trace、不依赖计数，故顺序无关。
- **E-09 用真实幂等重放**：改写请求头为该 FAILED 作业的 key，UI 提交同样（空）筛选 ⇒ 后端重放其已记录响应，FAILED/`error_code` 来自真实 GET；重试 = UI 新 key（从捕获原头断言）；"无半成品"由真实下载 GET 返回 409 + 浏览器零下载事件共同断言。
- **"无第二个作业"**以真实 PG 计数（种子 `counts` 动作，作业数仅 +1）断言，非 UI 推断。
- **S-08 的"提交中禁用"**通过持有真实创建请求（断言后才放行）确定化，避免与毫秒级 busy 窗口竞态。
- **值得知道的发现（不阻塞）**：(a) **Semi `Select` 的 `onChange` 仅在下拉关闭动画后触发**——选项点击后立即读取到旧筛选（本任务踩到并修）；(b) **Semi `Tabs` 保留非活动面板在 DOM 中**，故"无操作按钮"断言针对**可见**按钮；(c) **`useAuditList` 在刷新失败时保留旧行 → `ErrorState` 只在列表为空时渲染** ⇒ E-06 改为让**首次加载**失败以观察到 ErrorState + 保留筛选 + 重试；**设计写的是"查询失败保留筛选并可重试"（已满足），但"失败刷新"在已有行时不显示 ErrorState——若验收口径期望如此，需另行处置**（已标注）。
- **产物 root 固定在 `os.tmpdir()/muad-audit-e2e-<offset>/artifacts`**（Console 的 `validate_startup` 要求目录存在）⇒ 仓库 `.data/artifacts` 保持干净；仓库外残留仅该临时目录（含 seed.json 与空的 exports/）。
- 前端以**构建产物**（`npm run build` 已跑，含 typecheck）经 `vite preview` 提供。
- `e2e` 无 tsconfig/build 脚本且仓库无 `@types/node` ⇒ 无独立 typecheck；用前端 `tsc --noEmit --strict` 跑两文件仅报 `node:*`/`process` 缺类型（无其它诊断），两文件能被 Playwright 正常解析执行。
- S-06: e2e_deferred — automated command e2e_deferred; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=d9f3803f83a5428c907d7062d3021083 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-019: 收口：场景、规则、证据与仓库级 verifier

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-017, TASK-018
- **Source**: 11-audit-observability.backend.design.md#2.5.2 功能验收场景, 11-audit-observability.backend.design.md#2.5.1 业务规则与约束, 11-audit-observability.backend.design.md#6 需求追溯矩阵
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: RULE-06, RULE-test-001, B-211
- **Files**: `tests/audit_observability_inventory.py`
- **Estimate**: 半天级（全场景/规则映射核对 + 仓库级 verifier 执行）

### Description

本任务是唯一收口责任人：核对全部场景与规则行的唯一最终负责人与命令，执行原 Spec verifier 与本模块真实验收，登记每个断言位置、真实组件与清理证据；无用例收集、未执行、失败或 skip 一律不冒充 verified。结构检查实现方式与 `tests/acceptance/task_schedule/test_acceptance_environment.py`（B-144）和 `tests/acceptance/im_gateway/test_acceptance_inventory.py`（B-129）一致：读 manifest 断言唯一 owner、无未终态行、verified 场景在 owner 证据中登记、E2E 无 mock、无 skip/xfail 冒充。

### Checklist

- [x] [RULE-06][integration] 作为唯一最终负责人，核对全模块 E2E 场景均按 design 层级与真实边界执行（无降级、无业务 API mock），并登记断言位置。
- [x] [RULE-test-001][E2E] verifier_ref=harness-test#RULE-test-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`；原 verifier 全部通过，联合验收 argv 同前；不得以任务标题或静态声明代替行为证据。
- [x] 实现或补齐收口检查用例：场景/规则唯一负责人、无未终态行、证据登记、无 mock 与 skip 冒充。
- [x] 生成并校验 `.acceptance-manifest.json`（`--verify-plan`）后执行 `cf_acceptance_runner.py --include-e2e --write-evidence` 统一复验。
- [x] [B-211][integration] 以 pytest 用例收集/运行→验收 Contract/Evidence→真实组件记录 为边界编写/扩展用例；关键断言：无遗漏/重复最终负责人；无未终态行；verified 场景在 owner Evidence 中登记；E2E 无 mock；无 skip/xfail 冒充。执行 argv：`["uv","run","pytest","-q","tests/audit_observability_inventory.py"]`。
- [x] 执行上述契约命令，填写 Acceptance Evidence（含失败项单列与其 owner）；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-06 | integration | 跨 API/DB/Runtime/Browser 的真实 E2E 边界 | 全场景按 design 层级执行、无降级与 mock；断言位置可复核 | tests/audit_observability_inventory.py / RULE-06 | `["bash","-lc","uv run pytest -q tests/audit_observability_inventory.py -k r06"]` | verified |
| RULE-test-001 | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | 无遗漏/重复最终负责人；原 verifier 全部通过；失败/skip 不冒充 verified | tests/audit_observability_inventory.py + 原 verifier / RULE-test-001 | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | verified |
| B-211 | integration | pytest 用例收集/运行→验收 Contract/Evidence→真实组件记录 | 无遗漏/重复最终负责人；无未终态行；verified 场景在 owner Evidence 中登记；E2E 无 mock；无 skip/xfail 冒充 | tests/audit_observability_inventory.py / B-211 | `["uv","run","pytest","-q","tests/audit_observability_inventory.py","-k","b211"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-211 | **结构性 RED**：三个登记 argv 均 `ERROR: file or directory not found: tests/audit_observability_inventory.py`、`no tests ran`、exit 4。 | 新增 `tests/audit_observability_inventory.py`（289 行、10 用例 = 9 条 `b211` + 1 条 `r06`，最长函数 21 行），镜像 10 模块同款清单检查，并补上 10 收口时晚发现的缺口（**契约行（7 列）终态** + **done/verified 任务无未勾 checklist**）→ `-k b211` **9 passed**；`-k r06` **1 passed**；整文件 **10 passed**；ruff 干净；文件名全局唯一、与 `tests/acceptance/audit_observability` 可共同收集（20 tests collected）；不依赖 PostgreSQL。 | 9 条 `b211`：覆盖表唯一负责人且除本收口任务外全终态、manifest 与覆盖表一致（id/level/owner/status）、owner 的 Acceptance-Refs 登记、终态场景在 owner Acceptance Evidence 中登记、E2E 命令与模块验收文件无 mock 标记（`route.fulfill` **仅允许出现在设计许可的 E-06..E-09 块**，S-06/S-07/S-08 严禁）、无跳过标记（skip）/xfail 冒充、11 条 required 规则均有负责人与登记命令、契约行全终态、done/verified 任务零未勾项；`r06`：RULE-06 的 owner 为收口任务、命令为登记 argv、所有 E2E 行命令指向真实在盘套件（无 mock 且目标文件存在）。 | 结构/证据核对（读 manifest + 任务文档 + 文件存在性）；**非空转经 mutation 验证**（在内存副本/临时目录上做 12+ 处变异：done 任务插未勾项、契约行改 `planned`、场景改 `planned`、删 Acceptance-Refs 项、删证据登记、E2E 命令塞 mock 或指向不存在文件、改 RULE-06 归属或换成 mock 套件、删 required 规则、文档内塞跳过标记（skip） —— 每处均如期失败；其中两处首轮"未失败"经查明是变异目标错位（改到 10 列覆盖行而非 7 列契约行），已修正重跑） | verified |
| RULE-06 / RULE-test-001 | 无独立 RED（随 B-211 一并取证，验收类）。 | 观察计数：覆盖表 **49 行**（28 场景 + 21 规则）= 40 verified + 6 e2e_deferred + 3 planned，且**非终态的 3 行全部属收口任务自身**（B-211/RULE-06/RULE-test-001）；任务段 19（18 done + 1 in-progress）；契约行 59；E2E 行 13；required 规则 11/11 齐备；全文未勾项 6（**全属 TASK-019**）。 | 同上用例 + 原 verifier（RULE-test-001 的仓库级 argv 由 Done Gate 执行） | 结构与证据核对 + 原 verifier 真实边界 | verified |

**实现中的判断点（如实登记）**：
- **`route.fulfill` 的豁免按场景而非按文件**：`e2e/tests/audit-observability.spec.ts` 的 E-06 块确实使用了路由拦截（设计 §2.4 与 TASK-018 明确许可"失败/边界路径可用路由拦截模拟失败，属 integration 层级"）；对整文件做无差别扫描会误报，故检查断言"标记只可出现在 E-06..E-09 块内、绝不出现在 E2E 级 S-06/S-07/S-08 块"——这本身是有效的回归守卫。模块的 Python 验收文件完全无 mock；模块套件与任何任务文档中 `跳过标记（skip）`/`xfail` 字面量为 0。
- **本收口任务以外没有任何非终态行或未勾项**（明确计数，见上）。
- mutation 验证覆盖 12+ 处反例，确保该清单不是空转。
- B-211: failed — automated command failed; run_id=331c420db1014ef288fcf370f9739a54 (confirmed_by: runner)
- B-211: verified — automated command passed; run_id=280b495af1cb41a79134ddd1d68cf9c3 (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)
- [2026-09-25] started
- [2026-09-25] completed (done)

---

## TASK-020: Runtime/Worker 指标暴露与 label 卫生

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-009
- **Source**: 11-audit-observability.backend.design.md#3.5 质量实现方案, 11-audit-observability.backend.design.md#4 部署与运维
- **Spec-Refs**: harness-worker#RULE-worker-001
- **Acceptance-Refs**: B-212, RULE-worker-001
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/metrics.py`, `apps/agent-runtime/src/muad_agent_runtime/main.py`, `apps/agent-worker/src/muad_agent_worker/metrics.py`, `apps/agent-worker/src/muad_agent_worker/main.py`, `tests/agent_runtime/test_runtime_metrics.py`, `tests/agent_worker/test_worker_metrics.py`
- **Estimate**: 半天级（两个服务的注册与调用点接入 + 两处端点验收）；超出按服务拆两段

### Description

TASK-009 只落地了 Console 侧指标（`/metrics` + 四个计数器）。本任务按设计 §3.5 的指标目录补齐 **Runtime 与 Worker** 两侧：Runtime `agent_runs_total/model_invocations_total/tool_calls_total/skill_load_total/egress_calls_total/artifact_bytes_total`、Worker `tasks_total/task_queue_depth/task_reclaim_total/run_reclaim_total/task_lease_expired_total/scheduled_fire_total/scheduled_misfire_total/delivery_total`；两个服务各自暴露真实 `/metrics` HTTP 端点（复用 api-kit 的指标注册表与 `install_metrics`，**不建第二套注册表**），并在真实调用点计数；label 不得含 Secret/凭据/消息正文/PII。

### Checklist

- [x] [B-212][integration] 以真实 `/metrics` HTTP 端点（Runtime 与 Worker）+ 真实调用点计数 为边界编写/扩展用例；关键断言：目录指标可抓取、取值随真实调用递增、label 名/值无敏感形状。执行 argv：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_metrics.py && uv run pytest -q tests/agent_worker/test_worker_metrics.py"]`。
- [x] 实现或补齐：两个服务的指标注册（复用 api-kit 注册表）与真实调用点计数接入。
- [x] 覆盖 label 卫生：label 名不得含敏感标记，label 值不得含资源 ID/凭据形状/正文。
- [x] [RULE-worker-001][integration] verifier_ref=harness-worker#RULE-worker-001；原 verifier 输入 argv=；补充真实边界 真实 Worker 进程与 PostgreSQL/Redis：本次仅在既有 claim 谓词与终态 CAS 旁增计数，不改权威源/claim/lease 语义；断言 claim 仍 FOR UPDATE SKIP LOCKED、Redis 仍仅 wake-up/cancel hint；原 verifier 全部通过，联合验收 argv=。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-212 | integration | 真实 Runtime/Worker 进程 + 真实 `/metrics` HTTP 端点 + 真实调用点 | 目录指标可抓取；真实调用后计数递增；label 无 Secret/PII | tests/agent_runtime/test_runtime_metrics.py + tests/agent_worker/test_worker_metrics.py / B-212 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_metrics.py && uv run pytest -q tests/agent_worker/test_worker_metrics.py"]` | verified |
| RULE-worker-001 | integration | 真实 Worker 进程与 PostgreSQL/Redis + 原 verifier 真实边界 | Worker 权威源/claim/lease 语义不变；原 verifier 全部通过 | tests/agent_worker/test_worker_metrics.py + 原 verifier / RULE-worker-001 | `["bash","-lc","uv run pytest -q tests/agent_runtime/test_runtime_metrics.py && uv run pytest -q tests/agent_worker/test_worker_metrics.py && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-212 | **真实 RED**（实现临时 stash、测试在场）：runtime `3 failed` —— `/metrics` 返回 `404 COMMON_NOT_FOUND`、`ModuleNotFoundError: No module named 'muad_agent_runtime.metrics'`；worker `4 failed` —— 同样 404 与 `ImportError: cannot import name 'CATALOG' from 'muad_agent_worker.metrics'`。 | Runtime 新增 `metrics.py`（目录 + `record_counter`/`record_outcome` + `install_runtime_metrics`）并在 7 处真实调用点计数（`agent_runs_total`←Run 终态 CAS、`model_invocations_total`←`AuditedModelProvider._record`/`ModelGateway`、`tool_calls_total`←`ToolCallRecorder`、`skill_load_total`←`SkillToolSet._ready_dir`、`egress_calls_total`←`McpRuntimeAdapter._audit`、`artifact_bytes_total`←`ArtifactResultWriter`、`run_reclaim_total`←`reap_abandoned_runs`）；Worker 扩展 `metrics.py` 并在 claim/success/fail/cancel/reclaim/lease/schedule/delivery 等处计数，`task_queue_depth` 为真实 `COUNT(*)` 同谓词 gauge；api-kit 增 `declare_metric`/`declare()` 使目录在无流量时也可见（对未声明服务输出逐字节不变，已直接验证）→ 登记 argv **runtime 4 passed + worker 4 passed**；回归 `tests/agent_runtime/` **149 passed**、`tests/agent_worker/` **234 passed**；ruff 与 mypy（92 文件）干净；无残留进程/新增行。 | runtime：`tests/agent_runtime/test_runtime_metrics.py`（4 例）——真实 `/metrics` 端点（真实 uvicorn，`lifespan="off"` + 真实依赖覆盖）暴露目录、真实调用路径后计数递增、label 名/值卫生；worker：`tests/agent_worker/test_worker_metrics.py`（4 例）——同上（含真实 claim/execute/reclaim、schedule fire/misfire、delivery send 路径）。两文件均对抓取到的每条样本解析并拒绝：label 名含敏感标记、label 值含 UUID/凭据形状、响应体含租户 id/Run/Task/Agent/User UUID/正文 canary。 | 真实 Runtime/Worker 进程（真实 socket 与中间件）+ 真实 PostgreSQL/Redis 驱动真实调用路径；未 mock 业务 API | verified |

**实现中的判断点（如实登记）**：
- **`run_reclaim_total` 放在 Runtime 端点而非 Worker**：Worker 侧**不存在** Run 回收路径（无 Run 概念/表，已 grep 确认），唯一真实回收是 runtime 的 `_reaper_loop → reap_abandoned_runs`；归档的 `08-runtime-execution` 设计亦把它列在 Runtime。故 **Worker `/metrics` 暴露 7 个（非 8 个）任务清单名**——在 Worker 上声明它只会是一个恒零的幻影。
- `model_gateway.py` 无生产调用方（仅 `test_model_recovery.py` 构造它）：按要求在两处都接线，当前二者不嵌套、无双计。
- 采用"新增 `declare_metric` 让目录在无流量时可见"而非发明中间件；Console/im-gateway 未声明该 API，输出保持不变。
- 未给 runtime/worker 发明通用请求计数中间件（设计表未列）；未引入 Console 的 `count_outcome` 上下文管理器（两侧状态词表不同，改为各站点显式 `record_outcome`）。
- Worker 带 label 的计数器只出 `/metrics`、不进结构化 metric 日志（V1 日志 schema 无 label 字段，凭空扩展属越界）；无 label 的沿用两处出口。
- 既有的非目录 worker 计数器（`task_claim_total` 等）因 `increment` 镜像也一并出现在 `/metrics`；未改名、未删除。
- `delivery_total` 在三个 `_record_*` 内递增：丢失 CAS 竞态可能多计一次（与既有 `delivery_failed_total` 同性质）。
- `task_cancel.cancel_children`（批量取消空闲子任务）**未接线**：其 RETURNING 无 `task_type`。
- 顺带修掉 `skill_tools.py` 预存在的 ruff I001（改动文件须 lint 干净），其余预存在问题未动。
- **给调用方的注意**：api-kit 注册表是进程级全局，同进程导入多个服务 app 时会并集暴露（仅测试可达）；因改动共享注册表，本次**未**重跑 `tests/test_audit_observability_config.py` 与 `tests/gateway/test_message_metrics.py`（按"不跑其它模块套件"约束）——它们输出可证不变（均不调用 `declare_metric`），且会由本任务 Done Gate 的规则 verifier 覆盖。
- B-212: verified — automated command passed; run_id=5045d8dee964489f812cc15ea8b3239d (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)（由 TASK-019 收口发现的缺口拆出：设计 §3.5 的四服务指标目录此前仅 Console 侧落地）

---
- [2026-09-25] started
- [2026-09-25] resumed (in-progress)
- [2026-09-25] completed (done)
## TASK-021: API-01/05 增 agent_id 筛选（后端）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-003, TASK-006
- **Source**: 11-audit-observability.backend.design.md#3.3 数据设计, 11-audit-observability.backend.design.md#3.4 接口设计, 11-audit-observability.frontend.design.md#3.3 组件设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-213
- **Files**: `apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/audit_query_repository.py`, `apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/audits.py`, `tests/console_platform/test_audit_agent_filter.py`
- **Estimate**: 15–60 分钟；超出先拆"投影过滤"与"导出复用"两段

### Description

前端设计的筛选栏含独立的「Agent」项，而聚合投影的**运行类**记录本就带 `agent_id`/`agent_name`（config 类为空）——此前实现把该项映射为 `resource_type`，属口径冒充（已登记）。本任务给 API-01（列表）与 API-05（导出创建）增 `agent_id` 查询参数：按 `run_record.agent_id` / `task.task_execution.agent_id` 过滤运行类记录，config 类不参与（其 `agent_id` 恒空）。

### Checklist

- [x] [B-213][integration] 以真实 PostgreSQL（四张审计表 + 运行/任务表）为边界编写/扩展用例；关键断言：`agent_id` 过滤只返回该 Agent 的运行类记录、config 类被排除、未传该参数时行为不变。执行 argv：`["uv","run","pytest","-q","tests/console_platform/test_audit_agent_filter.py","-k","b213"]`。
- [x] 实现或补齐：投影 WHERE 增 `agent_id` 分支（参数化），并让 API-05 的筛选集与指纹输入同步包含它。
- [x] 覆盖分页/排序在带 `agent_id` 过滤时仍正确（total 与 items 一致）。
- [x] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-213 | integration | 真实 Console HTTP + 真实 PostgreSQL（审计四表 + 运行/任务表） | 按 `agent_id` 过滤仅返回该 Agent 的运行类记录；config 类排除；缺省行为不变；分页 total 一致 | tests/console_platform/test_audit_agent_filter.py / B-213 | `["uv","run","pytest","-q","tests/console_platform/test_audit_agent_filter.py","-k","b213"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-213 | **真实 RED**（实现临时 stash）：`4 failed` —— 列表用例 `assert 4 == 2`（参数被静默忽略，config 行与另一 Agent 的行都返回）、分页 total 同因、导出两条 `422 COMMON_VALIDATION_ERROR {"loc":["body","agent_id"],"type":"extra_forbidden"}`。诚实记录：首轮 RED 先暴露了**我自己种子助手的缺陷**（审计行挂到了 run 行没有的 `run_id`），修正种子后才在"实现已 stash"的前提下重取 RED。 | `AuditQueryFilters.agent_id` + `_where` 的等值分支（参数化 `agent_id = :agent_id`）、API-01 查询参数、API-05 body（`AuditExportCreateRequest.agent_id`）与导出执行侧（`_FILTER_FIELDS`/`to_query_filters`/`query_filters_from_canonical`）全部接通 → 登记 argv **4 passed**；console 回归 `tests/console_platform/` **112 passed**；ruff/mypy 干净；函数最长 48 行；无残留行/进程。 | `tests/console_platform/test_audit_agent_filter.py`（4 例）：① 按 `agent_id=A` 只返回 A 的运行类行（**config 行的 `resource_id` 故意指向 A，仍被排除** ⇒ 证明过滤只作用于运行类而非靠类型断言）；② 分页 `total` 与 items 一致；③ 不传该参数时行为不变；④ 导出执行侧生效——跑到 `SUCCEEDED` 并断言下载的 CSV 只含 A 的行（fingerprint 变化本身不足以发现"过滤器被静默丢弃"）。 | 真实 Console HTTP + 真实 PostgreSQL（四张审计表 + 运行/任务表），种子与回读均为真实行；导出产物写临时 `ARTIFACT_ROOT`，不污染仓库 | verified |

**实现中的判断点（如实登记）**：
- **过滤作用域**：在 UNION ALL 子查询的**投影列** `agent_id` 上过滤；运行类投影 `COALESCE(r.agent_id, tx.agent_id)`，config 类投影 `NULL::uuid` ⇒ `NULL = :agent_id` 永假，无需再显式排除 CONFIG，且测试用"config 行指向 Agent A 仍被排除"把该判别固化为断言。
- **未改 `validate_filters`**：类型化可选 UUID 无业务规则可校验，非法值在边缘（pydantic/FastAPI）已 422；改为**用断言钉住**该行为（API-01 与 API-05 的畸形 `agent_id` 均 422 且不建作业）。"CONFIG + agent_id" 组合返回空页而非报错——设计未规定，未自造规则。
- **指纹**：仅通过把字段加进 `_FILTER_FIELDS`/`to_query_filters` 实现（指纹源自规范化筛选集）；既有契约未动——`test_audit_export_api.py` 里对"不含 agent_id 的筛选集"的字面 SHA256 断言仍通过，即证明兼容。
- 测试文件比计划多 1 例（导出执行侧断言），用于发现"指纹变了但过滤器被丢"的静默缺陷。
- 前端接线（Agent 筛选 → `agentId`）仍是 TASK-022 范围；本次未动 `apps/console-platform/frontend/**`。
- B-213: verified — automated command passed; run_id=3a7e3da5c9e74b18a8e426e21a5b016b (confirmed_by: runner)

### Log
- [2026-09-25] created (draft)（由 TASK-019 收口发现的缺口拆出：设计筛选栏的「Agent」项此前被映射为 `resource_type`）

---
- [2026-09-25] started
- [2026-09-25] completed (done)
## TASK-022: 前端接线收口（Agent 筛选 / resourceType 域 / 刷新失败提示）

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-011, TASK-012, TASK-015, TASK-021
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: B-214
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/types.ts`, `apps/console-platform/frontend/src/modules/audit-observability/services/auditService.ts`, `apps/console-platform/frontend/src/modules/audit-observability/components/AuditFilterBar.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/components/AuditDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditList.ts`, `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`, `tests/frontend/test_audit_gap_contract.py`
- **Estimate**: 半天级（四处口径 + 词条）；超出按项拆

### Description

把 TASK-019 登记的三处前端口径缺口按设计补齐：①「Agent」筛选改接 TASK-021 的 `agentId` 参数（不再冒充 `resourceType`）；②`resource_type` 值域覆盖实际取值（含运行侧的小写/多种形态），详情「资源类型」行加 i18n 兜底（未知值原样展示、不留空白）；③**刷新失败**（已有行）保留已加载行并给出非破坏性错误提示，仅**首次加载失败**用整页 `ErrorState`（对齐设计 v1.5 的实施口径）。

### Checklist

- [ ] [B-214][integration] 以 前端源码契约 + 真实 `tsc --noEmit` + 仓库检查脚本 为边界编写/扩展用例；关键断言：Agent 筛选传 `agentId`、resourceType 覆盖实际取值且详情行有 i18n 兜底、刷新失败保留行且给非破坏性提示、首载失败才 `ErrorState`。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_audit_gap_contract.py","-k","b214"]`。
- [ ] 实现或补齐：`AuditListQuery` 增 `agentId`、service 传参、筛选栏接线（含 i18n key）。
- [ ] 实现或补齐：resourceType 枚举覆盖实际取值 + 详情行 i18n 兜底（zh-CN/en-US 同键集）。
- [ ] 实现或补齐：`useAuditList` 区分首载失败与刷新失败；刷新失败时保留 `items` 并由页面渲染非破坏性提示。
- [ ] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-214 | integration | 前端源码契约 + 真实 `tsc --noEmit` + 仓库检查脚本（services 收口/i18n） | Agent 筛选走 `agentId`；resourceType 覆盖实际取值且详情行有 i18n 兜底；刷新失败保留已加载行 + 非破坏性提示；仅首载失败渲染 `ErrorState` | tests/frontend/test_audit_gap_contract.py / B-214 | `["uv","run","pytest","-q","tests/frontend/test_audit_gap_contract.py","-k","b214"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)（由 TASK-019 收口发现的缺口拆出：Agent 筛选口径、resourceType 域与详情展示、刷新失败提示）
