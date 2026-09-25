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
| TASK-009 | P1 | 可观测性：trace 关联字段与指标目录 | 002 | 3.5 可观测性；4 部署与运维 | — | 4 |
| TASK-010 | P0 | 前端 service 层与类型契约 | 无 | frontend 3.4 组件接口契约 | RULE-front-001(integration) | 4 |
| TASK-011 | P0 | 审计列表页容器与筛选栏 | 010 | frontend 3.2/3.3；3.3.1 按钮设计 | E-06(integration), RULE-ui-001(E2E) | 5 |
| TASK-012 | P0 | 审计表格与字段列 | 011 | frontend 3.3/3.6 | S-06(E2E) | 3 |
| TASK-013 | P0 | 详情 SideSheet 与关联链接 | 012 | frontend 3.3/3.4 | S-07(E2E), E-07(integration), RULE-ui-detail-001(E2E) | 5 |
| TASK-014 | P1 | 导出按钮与轮询/下载交互 | 012 | frontend 3.3.1/3.5 | S-08(E2E), E-08(integration), E-09(integration) | 4 |
| TASK-015 | P1 | i18n 词条与语言切换覆盖 | 012 | frontend 3.3/3.6 | RULE-i18n-001(E2E) | 3 |
| TASK-016 | P0 | 真实审计验收环境与种子清理 | 005, 007 | 3.5 可靠性；4 部署与运维 | — | 5 |
| TASK-017 | P0 | 后端场景真实验收（S-01/03/05 + 集成场景证据） | 016 | 2.5.2 功能验收场景 | S-01(E2E), S-03(E2E), S-05(E2E) | 6 |
| TASK-018 | P0 | 前端 E2E 验收（列表/详情/导出） | 016 | frontend 2.4 验收条件 | S-06(E2E), S-07(E2E), S-08(E2E) | 6 |
| TASK-019 | P0 | 收口：场景、规则、证据与仓库级 verifier | 017, 018 | 2.5.2/2.5.1；6 需求追溯矩阵 | RULE-06(integration), RULE-test-001(E2E) | 5 |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) | TASK-017 | verified | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"] | . | 1200 |  |
| S-02 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | 真实 Tool/Egress/Model 执行路径→runtime 审计表 | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_audit_write.py","-k","s02"] | . | 600 |  |
| S-03 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→Console Admin Run 详情 HTTP→Runtime 内部端点→runtime 表 | TASK-017 | planned | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"] | . | 1200 |  |
| S-04 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console AppService 事务→control.config_audit_log | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","s04"] | . | 600 |  |
| S-05 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→Console 导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) | TASK-017 | planned | ["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"] | . | 1200 |  |
| B-201 | 11-audit-observability.backend.design.md#3.4 接口设计 | integration | 真实 Runtime HTTP /internal/admin/runs 与 /internal/admin/runs/{run_id} → 真实 PostgreSQL | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_admin_run_api.py"] | . | 600 |  |
| B-202 | 11-audit-observability.backend.design.md#3.4 接口设计 | integration | 真实 Console HTTP 导出状态/下载 → 真实 PostgreSQL + artifact store | TASK-007 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_download.py"] | . | 600 |  |
| E-01 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 详情查询→关联 Run 不可读 | TASK-004 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_detail_api.py","-k","e01"] | . | 600 |  |
| E-02 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | 真实 logging-kit 出口 + 审计写入 + Console 响应 | TASK-008 | verified | ["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"] | . | 600 |  |
| E-03 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 列表查询参数校验 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"] | . | 600 |  |
| E-04 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 业务事务回滚→config_audit_log | TASK-001 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_config_write.py","-k","e04"] | . | 600 |  |
| E-05 | 11-audit-observability.backend.design.md#2.5.2 功能验收场景 | integration | Console 导出创建→幂等表 partial unique | TASK-006 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"] | . | 600 |  |
| S-06 | 11-audit-observability.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→Console 聚合查询 HTTP | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""] | . | 1200 |  |
| S-07 | 11-audit-observability.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→Console 详情 HTTP | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-07\""] | . | 1200 |  |
| S-08 | 11-audit-observability.frontend.design.md#2.4 验收条件 | E2E | Browser(Chromium)→导出创建/查询 HTTP→PostgreSQL | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-08\""] | . | 1200 |  |
| E-06 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→Console 查询失败路径 | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""] | . | 900 |  |
| E-07 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→详情不可读路径 | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-07\""] | . | 900 |  |
| E-08 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→导出创建异指纹 409 | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-08\""] | . | 900 |  |
| E-09 | 11-audit-observability.frontend.design.md#2.4 验收条件 | integration | Browser→导出失败状态 | TASK-018 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-09\""] | . | 900 |  |
| RULE-01 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 真实 logging-kit 出口与日志文件 | TASK-008 | verified | ["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"] | . | 600 |  |
| RULE-02 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | Console 列表/详情 HTTP 封套与分页 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"] | . | 600 |  |
| RULE-03 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 新增表标准列/partial unique/timestamptz(PostgreSQL) | TASK-006 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_export_api.py","-k","e05"] | . | 600 |  |
| RULE-04 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 审计写入前脱敏 + API 响应 | TASK-008 | verified | ["uv","run","pytest","-q","tests/test_audit_redaction.py","-k","e02"] | . | 600 |  |
| RULE-05 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | Console 时间出参格式 | TASK-003 | verified | ["uv","run","pytest","-q","tests/console_platform/test_audit_query_api.py","-k","e03"] | . | 600 |  |
| RULE-06 | 11-audit-observability.backend.design.md#2.5.1 业务规则与约束 | integration | 跨 API/DB/Runtime/Browser 的真实 E2E 边界 | TASK-019 | planned | ["bash","-lc","uv run pytest -q tests/audit_observability_inventory.py -k r06"] | . | 600 |  |
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
| RULE-ui-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Console 页面 + 原 verifier 真实边界 | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | . | 1200 |  |
| RULE-ui-detail-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Console 页面详情结构 + 原 verifier 真实边界 | TASK-013 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 1200 |  |
| RULE-front-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | integration | 前端源码契约（services 收口/无裸 fetch/i18n）+ 原 verifier 真实边界 | TASK-010 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_services_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 900 |  |
| RULE-i18n-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 真实 Console 页面双语 + 原 verifier 真实边界 | TASK-015 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | . | 1200 |  |
| RULE-test-001 | 11-audit-observability.frontend.design.md#Spec Compliance Matrix | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | TASK-019 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 2400 |  |

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

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: 11-audit-observability.backend.design.md#3.5 质量实现方案, 11-audit-observability.backend.design.md#4 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: N/A（可观测性基建，行为证据并入 S-01 的 trace 串联断言与 TASK-017 的验收）
- **Files**: `apps/console-platform/backend/src/muad_console_platform/main.py`, `tests/test_audit_observability_config.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

按 §3.5 可观测性与 §4 落实：审计与日志统一携带 `trace_id/request_id/run_id/conversation_id/platform_user_id/agent_id/snapshot_id/skill_artifact_id/tool_call_id/task_id/schedule_id` 关联字段；每服务按目录导出 OTel/监控指标（不进业务 Console）；`/healthz` 与 `/readyz` 语义与失败快启校验保持一致；指标 label 不含 Secret/凭据/消息正文/PII。

### Checklist

- [ ] 实现或补齐：trace 关联字段在审计写入与日志上下文中的注入路径；缺字段时显式置空而非伪造。
- [ ] 实现或补齐：指标注册（Console 侧 `console_api_requests_total` 等）与 label 脱敏约束。
- [ ] 以真实 `/metrics` 端点验证指标可抓取、label 无敏感值（真实 uvicorn 单进程 + 真实 HTTP）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| N/A | integration | 真实 `/metrics` 端点 + 真实日志出口 | 指标可抓取、label 无 Secret/PII；trace 字段可串联 | tests/test_audit_observability_config.py / 全部用例 | `["uv","run","pytest","-q","tests/test_audit_observability_config.py"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、关键断言位置与真实组件证据。本任务为基建类，无独立场景行；其行为证据并入 S-01（trace 串联）与 TASK-017 的验收记录。

### Log
- [2026-09-25] created (draft)

---

## TASK-010: 前端 service 层与类型契约

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: 11-audit-observability.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: harness-frontend#RULE-front-001
- **Acceptance-Refs**: RULE-front-001
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/services/auditService.ts`, `apps/console-platform/frontend/src/modules/audit-observability/types.ts`, `tests/frontend/test_audit_services_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现 service 层与类型：`listAudits` / `getAudit` / `createExport`（Header `Idempotency-Key` 必填）/ `getExport` / `downloadExport`，后端 snake_case → 前端 camelCase 映射；导出幂等键由 service 在一次用户提交内生成并复用；组件不得裸用 axios/fetch，文案只用 i18n key。

### Checklist

- [ ] [RULE-front-001][integration] verifier_ref=harness-frontend#RULE-front-001；原 verifier 输入 argv=`["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`；补充真实边界 前端源码契约（services 收口/无裸请求/i18n）；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_services_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [ ] 实现或补齐：5 个 service 方法 + 类型定义（`AuditListQuery`/`AuditListItem`/`AuditExportCreateRequest`/`AuditExportJob`）。
- [ ] 覆盖导出幂等键约定：同一用户提交复用同一 key，显式新导出才换 key（源码契约断言）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-front-001 | integration | 前端源码 + 真实 typecheck/脚本 | 只经 services/；无裸 fetch/axios；文案为 i18n key；原 verifier 全部通过 | tests/frontend/test_audit_services_contract.py + 原 verifier / RULE-front-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_services_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-011: 审计列表页容器与筛选栏

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-010
- **Source**: 11-audit-observability.frontend.design.md#3.2 页面与路由结构, 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.3.1 每个按钮/操作的设计
- **Spec-Refs**: harness-ui#RULE-ui-001
- **Acceptance-Refs**: E-06, RULE-ui-001
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/pages/AuditPage.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/components/AuditFilterBar.tsx`, `tests/frontend/test_audit_page_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

新增路由 `/audits` 与页面容器：ConsoleShell + ModuleToolbar（左主操作、右上搜索筛选、右下分页），筛选栏覆盖时间/类型/用户/Agent/目标/动作/结果/Trace；筛选与分页状态在页面级维护；查询失败保留筛选并可重试（E-06）。

### Checklist

- [ ] [E-06][integration] 覆盖查询失败路径：ErrorState 呈现且筛选条件保留、可重试（与 TASK-018 的 spec 协同）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""]`。
- [ ] [RULE-ui-001][E2E] verifier_ref=harness-ui#RULE-ui-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]`；补充真实边界 真实 Console 页面与构建；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]`。
- [ ] 实现或补齐：路由注册、菜单项、页面容器与筛选栏（复用 ConsoleShell/ModuleToolbar/EmptyState/ErrorState/PaginationFooter）。
- [ ] 覆盖筛选条件 → `AuditListQuery` 的映射与重置行为。
- [ ] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-06 | integration | Browser→Console 查询失败路径 | 保留筛选并可重试；ErrorState 呈现 | e2e/tests/audit-observability.spec.ts / E-06（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""]` | planned |
| RULE-ui-001 | E2E | 真实 Console 页面 + 原 verifier 真实边界 | 左上操作/右上筛选/右下分页；主展示字段开详情；原 verifier 全部通过 | tests/frontend/test_audit_page_contract.py + 原 verifier / RULE-ui-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_page_contract.py && uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-012: 审计表格与字段列

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-011
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: S-06
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/components/AuditTable.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditList.ts`, `tests/frontend/test_audit_table_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现列表表格与数据 hook：字段列与 docs/15 口径一致（审计类型/资源/用户/动作/结果/Trace ID/时间），复用 StatusTag/DateTimeText，主展示字段可点击打开详情，右下分页与 total 联动。

### Checklist

- [ ] [S-06][E2E] 与 TASK-018 协同：按 Trace ID 搜索仅显示相关记录且字段与 docs/15 口径一致（本任务负责实现侧：列定义、`result_status` 标签、时间格式化）。
- [ ] 覆盖 `useAuditList` 状态机：loading/empty/error 与分页参数变更重取。
- [ ] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | Browser(Chromium)→Console 聚合查询 HTTP | 仅显示相关记录；字段与 docs/15 一致；分页可用 | e2e/tests/audit-observability.spec.ts / S-06（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-013: 详情 SideSheet 与关联链接

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-012
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-07, E-07, RULE-ui-detail-001
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/components/AuditDetailSideSheet.tsx`, `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditDetail.ts`, `tests/frontend/test_audit_detail_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现只读详情 SideSheet：标题/副标题左侧、操作按钮与关闭 X 同行靠右、Tabs 在其下；按 `audit_type` 取详情并展示 Run/Task/Trace 关联链接（EntityLink）；关联不可读时侧栏内 ErrorState 且不伪造关联（E-07）。

### Checklist

- [ ] [S-07][E2E] 与 TASK-018 协同：点击 Trace ID/主展示字段打开只读详情，无操作按钮，关联链接可跳转（本任务负责实现侧结构）。
- [ ] [E-07][integration] 覆盖关联不可读路径：SideSheet 内 ErrorState，不伪造关联数据。
- [ ] [RULE-ui-detail-001][E2E] verifier_ref=harness-ui-detail#RULE-ui-detail-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`；补充真实边界 真实 Console 页面详情结构；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [ ] 实现或补齐：SideSheet/DetailTabs/EntityLink 复用与只读约束（无编辑入口）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | E2E | Browser(Chromium)→Console 详情 HTTP | 只读详情；无操作按钮；关联链接正确 | e2e/tests/audit-observability.spec.ts / S-07（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-07\""]` | planned |
| E-07 | integration | Browser→详情不可读路径 | SideSheet 内 ErrorState；无伪造关联 | e2e/tests/audit-observability.spec.ts / E-07（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-07\""]` | planned |
| RULE-ui-detail-001 | E2E | 真实 Console 页面详情结构 + 原 verifier 真实边界 | 标题/副标题左侧、操作与关闭同行靠右、Tabs 在其下；原 verifier 全部通过 | tests/frontend/test_audit_detail_contract.py + 原 verifier / RULE-ui-detail-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_detail_contract.py && uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-014: 导出按钮与轮询/下载交互

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-012
- **Source**: 11-audit-observability.frontend.design.md#3.3.1 每个按钮/操作的设计, 11-audit-observability.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: S-08, E-08, E-09
- **Files**: `apps/console-platform/frontend/src/modules/audit-observability/hooks/useAuditExport.ts`, `apps/console-platform/frontend/src/modules/audit-observability/components/AuditExportButton.tsx`, `tests/frontend/test_audit_export_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

实现列表左主操作的"导出"按钮：按当前筛选创建导出任务（同一次用户提交复用同一 `Idempotency-Key`，提交中禁用），轮询 `getExport` 至终态并在 `SUCCEEDED` 时下载；`IDEMPOTENCY_MISMATCH` 与失败码走 catalog→i18n 文案（E-08/E-09）。

### Checklist

- [ ] [S-08][E2E] 与 TASK-018 协同：同 key 重试返回同一任务、轮询至完成可下载、提交中按钮禁用（本任务负责实现侧交互）。
- [ ] [E-08][integration] 覆盖异指纹 409：展示 i18n 文案、保留筛选、不重复创建任务。
- [ ] [E-09][integration] 覆盖 `FAILED` 状态：展示 `error_code` 文案与重试入口（复用新 key），不展示未完成产物。
- [ ] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | E2E | Browser(Chromium)→导出创建/查询 HTTP→PostgreSQL | 同一任务不重复创建；轮询至完成可下载；提交中禁用 | e2e/tests/audit-observability.spec.ts / S-08（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-08\""]` | planned |
| E-08 | integration | Browser→导出创建异指纹 409 | 展示 `IDEMPOTENCY_MISMATCH` 文案；保留筛选；不重复创建 | e2e/tests/audit-observability.spec.ts / E-08（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-08\""]` | planned |
| E-09 | integration | Browser→导出失败状态 | 展示 error_code 文案与重试入口；不展示未完成产物 | e2e/tests/audit-observability.spec.ts / E-09（owner TASK-018） | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-09\""]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-015: i18n 词条与语言切换覆盖

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-012
- **Source**: 11-audit-observability.frontend.design.md#3.3 组件设计, 11-audit-observability.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: RULE-i18n-001
- **Files**: `apps/console-platform/frontend/src/locales/zh-CN/audit-observability.ts`, `apps/console-platform/frontend/src/locales/en-US/audit-observability.ts`, `tests/frontend/test_audit_i18n_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

为审计模块补齐 zh-CN/en-US 词条（页面标题、列名、筛选项、状态标签、导出交互与错误文案），业务新增只加配置；错误/状态文案经 catalog → i18n key 映射，组件不硬编码中文。

### Checklist

- [ ] [RULE-i18n-001][E2E] verifier_ref=harness-i18n#RULE-i18n-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`；补充真实边界 真实 Console 页面双语切换；原 verifier 全部通过，联合验收 argv=`["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`。
- [ ] 覆盖 zh-CN 与 en-US 词条键一致（缺键/多余键断言）与 LocaleSwitch 切换后文案生效。
- [ ] 执行上述契约命令，填写 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-i18n-001 | E2E | 真实 Console 页面双语切换 + 原 verifier 真实边界 | 中英词条键一致；错误/状态文案经 i18n key；原 verifier 全部通过 | tests/frontend/test_audit_i18n_contract.py + 原 verifier / RULE-i18n-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_audit_i18n_contract.py && uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-016: 真实审计验收环境与种子清理

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-007
- **Source**: 11-audit-observability.backend.design.md#3.5 质量实现方案, 11-audit-observability.backend.design.md#4 部署与运维
- **Spec-Refs**:
- **Acceptance-Refs**: N/A（验收基建，行为证据由 TASK-017/018 的场景承载）
- **Files**: `tests/acceptance/audit_observability/environment.py`, `tests/e2e/seed_audit.py`, `tests/acceptance/audit_observability/test_environment.py`
- **Estimate**: 半天级（真实多进程栈与清理）；超出先拆环境与种子两段

### Description

建立真实审计验收环境：起 Console（查询面）+ Runtime（写入面）+ 复用既有真进程栈原语，准备审计种子（config/tool/egress/model 四类记录 + 一个导出任务）与幂等清理；提供 `start_*`/`purge_*`/`count_*` 与健康探测助手，供 TASK-017/018 复用。既有验收栈原语见 `tests/acceptance/task_schedule/environment.py`（`ServiceProcess`/`free_port`/`clear_engine_caches`）与 `tests/acceptance/im_gateway/environment.py` 的进程栈封装。

### Checklist

- [ ] 实现或补齐：真实进程栈启动/停止、健康就绪等待、租户级数据清理（含 `control.audit_export_job` 与共享幂等表行）。
- [ ] 实现或补齐：审计种子构造（四类审计 + 一个 `SUCCEEDED` 导出任务与 artifact），支持按 `trace_id` 串联断言。
- [ ] 以真实 PG 验证清理幂等：连续两次 `purge` 后审计相关表计数归零。
- [ ] 覆盖启动失败快启路径（依赖缺失时 fail fast，不静默降级）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| N/A | integration | Console/Runtime 真实进程 + 真实 PostgreSQL | 栈可启动并健康；种子可构造；清理幂等归零 | tests/acceptance/audit_observability/test_environment.py / 全部用例 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_environment.py"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、关键断言位置与真实组件证据。本任务为验收基建，无独立场景行。

### Log
- [2026-09-25] created (draft)

---

## TASK-017: 后端场景真实验收（S-01/03/05 + 集成场景证据）

- **Status**: draft
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

- [ ] [S-01][E2E] 以 Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) 为边界编写用例；关键断言：同链路审计字段归一、含 `result_status`、分页封套正确。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"]`。
- [ ] [S-03][E2E] 以 Browser→Console→Runtime `/internal/admin/runs`→PostgreSQL 为边界编写用例；关键断言：Run/Snapshot/Timeline/Tool/Egress/Model/Artifact 齐备且无 Secret。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"]`。
- [ ] [S-05][E2E] 以 Browser→导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) 为边界编写用例；关键断言：同 key 同指纹重放返回同一 `export_id`、不重复建任务、轮询至 `SUCCEEDED` 可下载。执行 argv：`["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"]`。
- [ ] 汇总集成场景证据：逐条登记 S-02/S-04/E-01..E-05 的用例位置与真实组件（执行命令见各 owner 任务契约）。
- [ ] 清理断言：场景结束后租户审计数据与导出任务归零。
- [ ] 执行上述契约命令，填写 Acceptance Evidence；测试文件与用例命名须全仓唯一。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Browser→Console 聚合查询 HTTP→四张审计表(PostgreSQL) | 同链路字段归一；含 `result_status`；分页封套正确 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-01 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s01"]` | planned |
| S-03 | E2E | Browser→Console→Runtime /internal/admin/runs→PostgreSQL | 详情字段齐备；无 Secret | tests/acceptance/audit_observability/test_audit_acceptance.py / S-03 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s03"]` | planned |
| S-05 | E2E | Browser→导出创建/查询 HTTP→幂等表与导出任务(PostgreSQL) | 同 key 重放同一任务；轮询至完成可下载 | tests/acceptance/audit_observability/test_audit_acceptance.py / S-05 | `["uv","run","pytest","-q","tests/acceptance/audit_observability/test_audit_acceptance.py","-k","s05"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-018: 前端 E2E 验收（列表/详情/导出）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-016
- **Source**: 11-audit-observability.frontend.design.md#2.4 验收条件, 11-audit-observability.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: S-06, S-07, S-08, E-06, E-07, E-08, E-09
- **Files**: `e2e/tests/audit-observability.spec.ts`, `e2e/playwright.audit-observability.config.ts`
- **Estimate**: 半天级（7 个场景 + 真实浏览器栈）；超出先拆正常/异常两段

### Description

按真实浏览器边界编写前端验收：S-06 列表与筛选、S-07 只读详情、S-08 导出全流程为 E2E（真实 Chromium→真实 Console）；E-06..04 为失败/边界路径（可用路由拦截模拟失败，属 integration 层级）。playwright 配置沿用既有命名，`workers: 1` 串行（避免共享真实后端互清数据）。

### Checklist

- [ ] [S-06][E2E] 以 Browser(Chromium)→Console 聚合查询 HTTP 为边界编写用例；关键断言：Trace ID 搜索仅显示相关记录、字段与 docs/15 一致、分页可用。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""]`。
- [ ] [S-07][E2E] 以 Browser→Console 详情 HTTP 为边界编写用例；关键断言：只读详情、无操作按钮、关联链接可跳转。
- [ ] [S-08][E2E] 以 Browser→导出创建/查询 HTTP→PostgreSQL 为边界编写用例；关键断言：同 key 重试同一任务、提交中禁用、轮询至完成并下载。
- [ ] [E-06..04][integration] 覆盖查询失败保留筛选、详情不可读 ErrorState、异指纹 409 文案、导出失败重试入口。
- [ ] 配置 `e2e/playwright.audit-observability.config.ts`（`workers: 1`）并在任务文档登记 argv。
- [ ] 执行上述契约命令，填写 Acceptance Evidence；测试文件与用例命名须全仓唯一。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | Browser(Chromium)→Console 聚合查询 HTTP | 仅显示相关记录；字段一致；分页可用 | e2e/tests/audit-observability.spec.ts / S-06 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-06\""]` | planned |
| S-07 | E2E | Browser(Chromium)→Console 详情 HTTP | 只读详情；无操作按钮；关联链接可跳转 | e2e/tests/audit-observability.spec.ts / S-07 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-07\""]` | planned |
| S-08 | E2E | Browser(Chromium)→导出创建/查询 HTTP→PostgreSQL | 同 key 重试同一任务；提交中禁用；完成后可下载 | e2e/tests/audit-observability.spec.ts / S-08 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"S-08\""]` | planned |
| E-06 | integration | Browser→Console 查询失败路径 | 保留筛选可重试；ErrorState 呈现 | e2e/tests/audit-observability.spec.ts / E-06 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-06\""]` | planned |
| E-07 | integration | Browser→详情不可读路径 | SideSheet 内 ErrorState；无伪造关联 | e2e/tests/audit-observability.spec.ts / E-07 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-07\""]` | planned |
| E-08 | integration | Browser→导出创建异指纹 409 | 展示 `IDEMPOTENCY_MISMATCH` 文案；保留筛选 | e2e/tests/audit-observability.spec.ts / E-08 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-08\""]` | planned |
| E-09 | integration | Browser→导出失败状态 | 展示 error_code 文案与重试入口 | e2e/tests/audit-observability.spec.ts / E-09 | `["bash","-lc","cd e2e && npx playwright test --config playwright.audit-observability.config.ts -g \"E-09\""]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)

---

## TASK-019: 收口：场景、规则、证据与仓库级 verifier

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-017, TASK-018
- **Source**: 11-audit-observability.backend.design.md#2.5.2 功能验收场景, 11-audit-observability.backend.design.md#2.5.1 业务规则与约束, 11-audit-observability.backend.design.md#6 需求追溯矩阵
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: RULE-06, RULE-test-001
- **Files**: `tests/audit_observability_inventory.py`
- **Estimate**: 半天级（全场景/规则映射核对 + 仓库级 verifier 执行）

### Description

本任务是唯一收口责任人：核对全部场景与规则行的唯一最终负责人与命令，执行原 Spec verifier 与本模块真实验收，登记每个断言位置、真实组件与清理证据；无用例收集、未执行、失败或 skip 一律不冒充 verified。结构检查实现方式与 `tests/acceptance/task_schedule/test_acceptance_environment.py`（B-144）和 `tests/acceptance/im_gateway/test_acceptance_inventory.py`（B-129）一致：读 manifest 断言唯一 owner、无未终态行、verified 场景在 owner 证据中登记、E2E 无 mock、无 skip/xfail 冒充。

### Checklist

- [ ] [RULE-06][integration] 作为唯一最终负责人，核对全模块 E2E 场景均按 design 层级与真实边界执行（无降级、无业务 API mock），并登记断言位置。
- [ ] [RULE-test-001][E2E] verifier_ref=harness-test#RULE-test-001；原 verifier 输入 argv=`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`；原 verifier 全部通过，联合验收 argv 同前；不得以任务标题或静态声明代替行为证据。
- [ ] 实现或补齐收口检查用例：场景/规则唯一负责人、无未终态行、证据登记、无 mock 与 skip 冒充。
- [ ] 生成并校验 `.acceptance-manifest.json`（`--verify-plan`）后执行 `cf_acceptance_runner.py --include-e2e --write-evidence` 统一复验。
- [ ] 执行上述契约命令，填写 Acceptance Evidence（含失败项单列与其 owner）；全部 verified 后才可 done。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-06 | integration | 跨 API/DB/Runtime/Browser 的真实 E2E 边界 | 全场景按 design 层级执行、无降级与 mock；断言位置可复核 | tests/audit_observability_inventory.py / RULE-06 | `["bash","-lc","uv run pytest -q tests/audit_observability_inventory.py -k r06"]` | planned |
| RULE-test-001 | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | 无遗漏/重复最终负责人；原 verifier 全部通过；失败/skip 不冒充 verified | tests/audit_observability_inventory.py + 原 verifier / RULE-test-001 | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部场景/规则状态 verified 后任务才可 done。

### Log
- [2026-09-25] created (draft)
