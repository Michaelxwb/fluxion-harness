# Tasks: Console 账号与访问控制

- **Source**: .code-flow/tasks/2026-09-17/13-console-auth/（全部 design：13-console-auth.backend.design.md、13-console-auth.frontend.design.md）
- **Created**: 2026-09-27
- **Updated**: 2026-09-27
- **Plan-State**: planned（用户已确认写入；各 TASK 保持 draft，功能与 E2E 验收尚未执行）

## Proposal

把 Console 的**账号与访问控制**收敛为可验收的安全入口：登录（argon2id + 5 次锁定 + 未知用户假哈希等化时序）、会话（PG 唯一权威、令牌只存 sha256、12h TTL + 剩余 <50% 滑动续期）、登出撤销、改密、ADMIN 账号管理（统一分页封套、永不返回 `password_hash`）、Cookie + 双提交 CSRF、RBAC 与全局认证依赖、CLI `create-admin` 与无账号启动告警，以及**账号创建/登录成功的审计归因**（`actor_user_id = console_account.id`，与业务变更同事务且脱敏）。前端补齐登录页与会话引导、路由守卫（`RequireAuth`/`RequireRole`）、统一 ApiClient 拦截器（locale/request-id/CSRF/401/Toast）、菜单与凭据入口的角色过滤。

**基线现状（拆解时核对，影响任务形态）**：后端认证链（`api/auth.py` 的 login/logout/me/password）、前端（`auth/AuthContext.tsx`、`pages/LoginPage.tsx`、`api/client.ts`、`LocaleSwitch`）与既有测试（`tests/console_auth/` 五个文件）**均已存在**；design §2.4 的三项技术债中，**①账号列表分页已在本仓早前收敛完成**（`api/accounts.py` 已用 `paginate` + `page_size` 边界），**②账号创建/登录成功的审计未写入**（`auth_service.py` 对 `config_audit_log` 零引用）为真实缺口，③改密不撤销既有会话属 V1 既定行为。共 12 个原子任务：P0 11、P1 1（其中 TASK-007 含 `harness-api#RULE-api-002` 的承接）。

## Design Alignment

2026-09-27 拆解前完成三处对齐（均已写入任务文件，不代表实现或 verifier 已通过）：

- **前端场景 ID 重编**：前端 design 声明的 `S-FE-01..06` / `E-FE-01..05` / `B-FE-01..02` 带中缀，会被 manifest/runner **静默忽略**（12-overview-dashboard 已踩）。并入后端同一数字序列：`S-FE-01..06 → S-09..S-14`、`E-FE-01..05 → E-09..E-13`、`B-FE-01..02 → B-04..B-05`。合计 S 14 / E 13 / B 5 = 32 场景。
- **`S-FE-06`（→S-14）按 design 自注限定范围**：该场景预期结果含「账号列表展示 `last_login_at` 且时间格式 `YYYY-MM-DD HH:mm:ss`」，但两份 design 均把账号管理页面列为**后置、随后续迭代落地**（前端 §2.3 技术债：`/users` 当前为占位页），且场景原文自带「（后置页面落地后生效）」。故本需求内**只验证守卫部分**（ADMIN 可进 `/users` 占位页、BUILDER 被重定向到 `/` 且不发起该页数据请求），列表与时间展示部分随账号页面迭代落地，并在该场景的 Acceptance Evidence 中显式登记该边界，不冒充完整验证。
- **`harness-api#RULE-api-002` 承接（用户决定）**：该规则约束「创建/上传类 POST 的 `Idempotency-Key` 幂等」，而本模块**确有创建类 POST**（`POST /api/v1/accounts`）——与 12（只读模块、判 N/A）情形不同。故**承接**：给创建账号加 `Idempotency-Key` 支持（复用既有共享幂等表，不新建第二张），归 TASK-007，并补自有场景 **B-08**（同 key 同指纹重放不重复创建 / 异指纹 `IDEMPOTENCY_MISMATCH` / 并发落败者读首次结果）。
- **验收目录改名避让包名冲突（实验确认）**：原计划用 `tests/acceptance/console_auth/`，但 `tests/acceptance/*` 的每个子目录都是**独立顶层包**（pytest basedir 落在 `tests/acceptance/`），会与既有 `tests/console_auth/` 形成**同名包冲突**——实测同时收集两处直接 `ModuleNotFoundError: No module named 'console_auth.test_probe'`（`console_auth` 被解析到 `tests/console_auth`）。故改名为 `tests/acceptance/console_auth_flow/`（既有套件零改动）。执行命令属 manifest 的锁定字段，故按规程**重跑 plan 验证并重建 manifest**。
- **spec id 校正**：两份 design 的 Spec Compliance Matrix 写的是 legacy `harness-platform#RULE-*`，按现行分域 spec 校正为 `harness-api#RULE-api-001`、`harness-auth#RULE-auth-001`、`harness-data#RULE-data-001`、`harness-i18n#RULE-i18n-001`、`harness-secret#RULE-secret-001`、`harness-log#RULE-log-001`、`harness-test#RULE-test-001`、`harness-time#RULE-time-001`、`harness-rel#RULE-rel-001`、`harness-ui#RULE-ui-001`、`harness-ui-detail#RULE-ui-detail-001`、`harness-frontend#RULE-front-001`（共 **13** 条 required，与 Context 绑定一致）。禁止重新选择或降级。

## Task Overview

| TASK | 优先级 | 标题 | 依赖 | 来源章节 | 验收 | Checklist |
|---|---|---|---|---|---|---|
| TASK-001 | P0 | 账号审计归因补齐（创建/登录成功） | 无 | backend 2.3 FEAT-08；2.5.1 RULE-10；3.4 API-06 | S-08(integration), E-07(integration), RULE-log-001(integration), RULE-secret-001(integration) | 6 |
| TASK-002 | P0 | 认证真实验收环境与种子 | 无 | backend 2.5.2；3.3 | B-06(integration) | 5 |
| TASK-003 | P0 | 登录链真实验收 | 002 | backend 2.5.2；3.2 登录流程；3.4 API-01 | S-01(E2E), E-01(integration), E-02(integration), E-06(integration), B-01(integration), B-03(integration), RULE-auth-001(integration) | 8 |
| TASK-004 | P0 | 会话链真实验收（续期/过期撤销） | 002 | backend 3.2 会话解析；3.4 API-03 | S-02(integration), E-03(integration), B-02(integration) | 6 |
| TASK-005 | P0 | 登出撤销真实验收 | 002 | backend 3.4 API-02 | S-03(E2E), RULE-rel-001(integration) | 5 |
| TASK-006 | P0 | 修改密码真实验收 | 002 | backend 3.4 API-04 | S-04(E2E) | 5 |
| TASK-007 | P0 | 账号管理与访问控制真实验收（含创建幂等） | 002 | backend 3.4 API-05/06；2.5.1 RULE-05/06 | S-05(E2E), S-06(E2E), E-04(integration), E-05(integration), E-08(integration), B-08(integration), RULE-api-001(integration), RULE-api-002(integration) | 11 |
| TASK-008 | P0 | CLI create-admin 与启动自检验收 | 002 | backend 3.4 形态 B；2.3 FEAT-07 | S-07(integration) | 5 |
| TASK-009 | P1 | 数据契约承接（两表结构/索引） | 002 | backend 3.3 数据设计 | B-07(integration), RULE-data-001(integration) | 5 |
| TASK-010 | P0 | 前端 auth service 与 ApiClient 拦截器契约 | 无 | frontend 3.5 数据获取层；3.3 CMP-06 | S-11(E2E), B-05(unit), RULE-front-001(integration), RULE-i18n-001(integration) | 7 |
| TASK-011 | P0 | 登录页/会话引导与守卫/角色过滤/退出 | 010 | frontend 3.2 路由；3.3 组件树；3.6 UI 状态 | S-09(E2E), S-10(E2E), S-12(E2E), S-13(E2E), S-14(integration), E-11(integration), E-12(integration), B-04(unit), RULE-ui-001(integration), RULE-ui-detail-001(integration), RULE-time-001(integration) | 9 |
| TASK-012 | P0 | 前端 E2E 验收与收口 | 003, 005, 006, 007, 011 | frontend 2.4；backend 2.5.2；6 需求追溯 | E-09(integration), E-10(integration), E-13(integration), RULE-test-001(E2E) | 7 |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 argv | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | E2E | Browser → Console HTTP → PostgreSQL | TASK-003 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-01\""] | . | 1200 |  |
| S-02 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | Service → 真实 PostgreSQL（会话续期） | TASK-004 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s02"] | . | 600 |  |
| S-03 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | E2E | Browser → Console HTTP → PostgreSQL | TASK-005 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-03\""] | . | 1200 |  |
| S-04 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | E2E | Browser → Console HTTP → PostgreSQL | TASK-006 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-04\""] | . | 1200 |  |
| S-05 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | E2E | Browser → Console HTTP → PostgreSQL | TASK-007 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-05\""] | . | 1200 |  |
| S-06 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | E2E | Browser → Console HTTP → PostgreSQL | TASK-007 | e2e_deferred | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-06\""] | . | 1200 |  |
| S-07 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | CLI → 真实 PostgreSQL；真实 lifespan 启动 → 日志 | TASK-008 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s07"] | . | 600 |  |
| S-08 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | Service → 真实 PostgreSQL（审计与变更同事务） | TASK-001 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s08"] | . | 600 |  |
| S-09 | 13-console-auth.frontend.design.md#2.4 验收条件 | E2E | Browser → Router → ApiClient → Console HTTP → PostgreSQL | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-09\""] | . | 1200 |  |
| S-10 | 13-console-auth.frontend.design.md#2.4 验收条件 | E2E | Browser → Router → `/auth/me` | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-10\""] | . | 1200 |  |
| S-11 | 13-console-auth.frontend.design.md#2.4 验收条件 | E2E | Locale → ApiClient → API msg → UI | TASK-010 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-11\""] | . | 1200 |  |
| S-12 | 13-console-auth.frontend.design.md#2.4 验收条件 | E2E | AuthContext → menu → Router → 凭据入口 | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-12\""] | . | 1200 |  |
| S-13 | 13-console-auth.frontend.design.md#2.4 验收条件 | E2E | UI → logout API → Router | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-13\""] | . | 1200 |  |
| S-14 | 13-console-auth.frontend.design.md#2.4 验收条件 | integration | Router → ADMIN 守卫（列表展示部分随账号页面迭代） | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-14\""] | . | 900 |  |
| E-01 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 真实 PostgreSQL（失败计数） | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e01"] | . | 600 |  |
| E-02 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 真实 PostgreSQL（锁定状态机） | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e02"] | . | 600 |  |
| E-03 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 真实 PostgreSQL（会话失效） | TASK-004 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e03"] | . | 600 |  |
| E-04 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → CSRF 校验 | TASK-007 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e04"] | . | 600 |  |
| E-05 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → RBAC | TASK-007 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e05"] | . | 600 |  |
| E-06 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 真实 PostgreSQL（禁用账号不泄露状态） | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e06"] | . | 600 |  |
| E-07 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | Service → 真实 PostgreSQL（审计脱敏） | TASK-001 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e07"] | . | 600 |  |
| E-08 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 全局认证（公开端点白名单） | TASK-007 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e08"] | . | 600 |  |
| E-09 | 13-console-auth.frontend.design.md#2.4 验收条件 | integration | ApiClient 响应拦截 → Router（401 跳登录，不循环） | TASK-012 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-09\""] | . | 900 |  |
| E-10 | 13-console-auth.frontend.design.md#2.4 验收条件 | integration | LoginPage → API 错误 → Toast | TASK-012 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-10\""] | . | 900 |  |
| E-11 | 13-console-auth.frontend.design.md#2.4 验收条件 | integration | RequireRole → Router（越权重定向，不发数据请求） | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-11\""] | . | 900 |  |
| E-12 | 13-console-auth.frontend.design.md#2.4 验收条件 | integration | ApiClient 请求拦截 → CSRF（后端 403 兜底） | TASK-011 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-12\""] | . | 900 |  |
| E-13 | 13-console-auth.frontend.design.md#2.4 验收条件 | integration | AuthProvider → `/me` 失败（无空白页/无循环） | TASK-012 | planned | ["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-13\""] | . | 900 |  |
| B-01 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 真实 PostgreSQL（第 4/5 次失败临界） | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b01"] | . | 600 |  |
| B-02 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | Service → 真实 PostgreSQL（剩余 6h / <6h 临界） | TASK-004 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b02"] | . | 600 |  |
| B-03 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | API → 真实 PostgreSQL（多租户同名） | TASK-003 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b03"] | . | 600 |  |
| B-04 | 13-console-auth.frontend.design.md#2.4 验收条件 | unit | LoginPage 表单校验（不发请求） | TASK-011 | planned | ["uv","run","pytest","-q","tests/frontend/test_console_auth_contract.py","-k","b04"] | . | 600 |  |
| B-05 | 13-console-auth.frontend.design.md#2.4 验收条件 | unit | i18n 资源双语完整 | TASK-010 | planned | ["uv","run","pytest","-q","tests/frontend/test_console_auth_contract.py","-k","b05"] | . | 600 |  |
| B-06 | 13-console-auth.backend.design.md#2.5.2 功能验收场景 | integration | 真实 Console 进程 + 真实 PostgreSQL + 租户级清理 | TASK-002 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_environment.py"] | . | 600 |  |
| B-07 | 13-console-auth.backend.design.md#3.3 数据设计 | integration | 真实 PostgreSQL 表结构/索引 + 原 verifier 真实边界 | TASK-009 | planned | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_schema_contract.py","-k","b07"] | . | 600 |  |
| B-08 | 13-console-auth.backend.design.md#3.4 接口设计 | integration | 真实 Console HTTP → 共享幂等表(PostgreSQL) | TASK-007 | verified | ["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b08"] | . | 600 |  |
| RULE-log-001 | 13-console-auth.backend#2.5.1 业务规则与约束 | integration | 统一 logging-kit 出口 + 原 verifier 真实边界 | TASK-001 | planned | ["bash","-lc","uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py"] | . | 600 |  |
| RULE-secret-001 | 13-console-auth.backend#2.5.1 业务规则与约束 | integration | 密码 argon2id / 令牌 sha256 / 三层脱敏 + 原 verifier 真实边界 | TASK-001 | planned | ["bash","-lc","uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"] | . | 600 |  |
| RULE-auth-001 | 13-console-auth.backend#Spec Compliance Matrix | integration | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | TASK-003 | planned | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | . | 600 |  |
| RULE-api-001 | 13-console-auth.backend#Spec Compliance Matrix | integration | 统一封套/分页边界/catalog 错误码 + 原 verifier 真实边界 | TASK-007 | planned | ["bash","-lc","uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"] | . | 1200 |  |
| RULE-data-001 | 13-console-auth.backend#Spec Compliance Matrix | integration | 真实 PostgreSQL 表结构/索引 + 原 verifier 真实边界 | TASK-009 | planned | ["uv","run","pytest","-q","tests","-k","schema_parity"] | . | 600 |  |
| RULE-rel-001 | 13-console-auth.backend#Spec Compliance Matrix | integration | 真实 Console HTTP 单端点原子变更 + 原 verifier 真实边界 | TASK-005 | planned | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] | . | 600 |  |
| RULE-front-001 | 13-console-auth.frontend#Spec Compliance Matrix | integration | 前端源码契约 + 仓库检查脚本 + 真实 tsc | TASK-010 | planned | ["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 900 |  |
| RULE-i18n-001 | 13-console-auth.frontend#Spec Compliance Matrix | integration | 双侧词条 + 原 verifier 真实边界 | TASK-010 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | . | 1200 |  |
| RULE-ui-001 | 13-console-auth.frontend#Spec Compliance Matrix | integration | 前端源码契约 + 真实构建 + 原 verifier 真实边界 | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | . | 1200 |  |
| RULE-ui-detail-001 | 13-console-auth.frontend#Spec Compliance Matrix | integration | 公共详情组件契约 + 真实 tsc + 原 verifier 真实边界 | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | . | 900 |  |
| RULE-time-001 | 13-console-auth.backend#Spec Compliance Matrix | integration | 时间展示口径 + 原 verifier 真实边界 | TASK-011 | planned | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | . | 1200 |  |
| RULE-test-001 | 13-console-auth.frontend#Spec Compliance Matrix | E2E | 仓库级真实 E2E（HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | TASK-012 | planned | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | . | 2400 |  |
| RULE-api-002 | 13-console-auth.backend#Spec Compliance Matrix | integration | 真实 Console HTTP → 幂等表(PostgreSQL) + 原 verifier 真实边界 | TASK-007 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py -k b08 && uv run pytest -q tests/console_skill/test_import_idempotency.py"] | . | 1200 |  |

> 本表覆盖两份 design 中全部 P0/P1 场景（后端 S-01..S-08、E-01..E-08、B-01..B-03；前端重编后的 S-09..S-14、E-09..E-13、B-04..B-05，共 **32 个**）与 **12 条 required Spec Rule**；每个场景与规则有且仅有一个最终负责人；无 manual 场景；E2E 层级不降级。B-06/B-07/B-08 为「无 design 场景的任务」补的自有集成场景（B-08 是 `harness-api#RULE-api-002` 的验证场景）。

---

## TASK-001: 账号审计归因补齐（创建/登录成功）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 13-console-auth.backend.design.md#2.3 功能方案, 13-console-auth.backend.design.md#2.5.1 业务规则与约束, 13-console-auth.backend.design.md#3.4 接口设计, 13-console-auth.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-08, E-07, RULE-log-001, RULE-secret-001
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/auth_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/auth.py`, `apps/console-platform/backend/src/muad_console_platform/api/accounts.py`, `tests/acceptance/console_auth_flow/test_auth_acceptance.py`
- **Estimate**: 半天级（含审计接入与脱敏取证）

### Description

补齐 FEAT-08 / 技术债②：账号创建（`CONSOLE_ACCOUNT/CREATE`）与登录成功（`LOGIN`）必须写入 `control.config_audit_log`，`actor_user_id = console_account.id`，与业务变更**同一事务**；审计载荷不得含密码/令牌明文。基线 `auth_service.py` 对 `config_audit_log` 零引用，是本需求唯一的实现缺口。

### Checklist

- [x] [S-08][integration] 以真实 Service → 真实 PostgreSQL 为边界编写用例：登录态下执行一次受保护写操作 + 创建账号，断言写操作审计 `actor_user_id == 登录 console_account.id` 且与变更同事务（回滚则审计不落库）；账号创建/登录成功的 `CONSOLE_ACCOUNT` 行 `action ∈ {CREATE, LOGIN}`。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s08"]`。
- [x] [E-07][integration] 变更载荷含 `password/secret/token/api_key` 时，审计 JSON 中这些键被剔除、库内反查无明文。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e07"]`。
- [x] [RULE-log-001][integration] 作为唯一最终负责人：断言审计写入经统一 logging-kit 出口、字段结构一致且敏感键脱敏。verifier argv：`["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]`。
- [x] [RULE-secret-001][integration] 作为唯一最终负责人：密码只存 argon2id、令牌只存 sha256，审计/日志无 Secret 明文。verifier argv：`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`。
- [x] 实现或补齐：复用 api-kit `write_config_audit(session, ...)`（只 INSERT 不 commit，与业务共用事务边界）；载荷经 `sanitize_audit_payload` 脱敏；登录成功审计不得记录密码或令牌。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录；函数 ≤50 行、强类型、显式异常处理。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | integration | Service → 真实 PostgreSQL | 审计 `actor_user_id` = 登录账号；与变更同事务；`CREATE`/`LOGIN` 落库 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / S-08 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s08"]` | verified |
| E-07 | integration | Service → 真实 PostgreSQL | 敏感键被剔除；库内反查无明文 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-07 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e07"]` | verified |
| RULE-log-001 | integration | logging-kit 出口 + 原 verifier 真实边界 | 审计字段结构与脱敏；原 verifier 全部通过 | 原 verifier / RULE-log-001 | `["bash","-lc","uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py tests/acceptance/test_foundation_logging.py"]` | verified |
| RULE-secret-001 | integration | 三层脱敏(PostgreSQL+logging-kit) + 原 verifier 真实边界 | 密码/令牌无明文；原 verifier 全部通过 | 原 verifier / RULE-secret-001 | `["bash","-lc","uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-08 | **真实 RED**：接入审计前按登记 argv 执行 → `test_s08_account_create_and_login_write_audit_with_actor` **FAILED**（`assert login_rows` 为空——登录成功不写任何审计）。同批的 `test_s08_failed_create_leaves_no_audit_row` 当时**空转通过**（根本没有审计写入），故实现后必须以「它仍然通过」来证明同事务语义成立（见 GREEN 列）。 | 接入 API 层审计后整文件 **3 passed**；既有 `tests/console_auth/` 回归 **31 passed**（登录新增审计写入未影响既有断言）；`RULE-log-001` 原 verifier **11 passed**、`RULE-secret-001` 原 verifier **12 passed**；ruff 干净。 | `test_s08_account_create_and_login_write_audit_with_actor`：真实登录 → `control.config_audit_log` 中 `resource_id=admin_id` 且 `action='LOGIN'` 的行存在，`actor_user_id == admin_id`、`resource_type='CONSOLE_ACCOUNT'`、`tenant_id` 正确、`trace_id` 与 `source_ip` 非空；随后 ADMIN 创建账号 → `resource_id=新账号 id` 且 `action='CREATE'` 的行**恰好一条**，`actor_user_id == 创建者 admin_id`（**不是**被创建的账号）、`before_json is None`、`after_json` 非空。`test_s08_failed_create_leaves_no_audit_row`：**实现后仍然通过**——租户内 `CONSOLE_ACCOUNT/CREATE` 计数在重名创建 409 前后不变（审计与业务变更同事务，失败不落行）。`test_e07_audit_payload_strips_sensitive_keys`：请求体含 `password`，审计行 JSON 中既无该键也无明文（`password_hash` 亦不出现）。 | 真实 Console ASGI 全栈（真实登录会话 + CSRF）+ 真实 PostgreSQL（`config_audit_log` 用 SQLAlchemy 逐行回读、按 `resource_id`/`tenant_id` 计数）；审计原语为 api-kit `write_config_audit`（只 INSERT 不 commit），与业务共用请求级 session。**同事务语义的取证边界**：本任务以「共享 session 写入 + 失败请求不新增审计行」取证；更强的"事务内可见性 + 显式 rollback 回读"证据由既有 AGENT 审计用例（`tests/console_auth/test_audit.py`）沿同一代码路径承载。 | verified |
| RULE-log-001 | 见上（S-08 的 RED 即本规则取证过程）。 | 原 verifier 复跑通过：`tests/test_logging.py` + `test_logging_redaction.py` + `test_foundation_logging.py` → **11 passed**。 | 原 verifier 断言（logging-kit 统一出口、JSON 结构、trace/request/tenant 字段、敏感字段脱敏） | 真实 logging-kit 出口 + 真实日志文件；无 mock。 | verified |
| RULE-secret-001 | 见上（E-07 的 RED 即本规则取证过程）。 | 原 verifier 复跑通过：`tests/test_logging_redaction.py` + `tests/acceptance/test_foundation_ops_audit.py` → **12 passed**。 | 原 verifier 断言（密钥明文边界、审计/日志脱敏） | 真实 logging-kit 出口与审计表；无 mock。 | verified |

**实现中的判断点（如实登记）**：

- **审计写在 API 层而非 service 层**：沿用仓库既有做法（`api/agents.py` 的 `_actor(account, request)` + `AuditService.record_config_change`），使「actor = 当前登录账号」与「与业务变更共用同一请求级 session（因而同事务）」两点同时成立；`AuthService` 不自行 commit。
- **登录审计的 actor 是账号自身**：登录动作的发起者就是该账号（无需 Console 会话），故 `actor_user_id = account.id`；载荷只写 `username`/`role` 等非敏感标识，不写密码、不写令牌。
- **创建账号审计的 actor 是创建者**：`POST /api/v1/accounts` 属 ADMIN 组，`account: CurrentAccount` 即创建者——这正是 S-08 要区分的点（若误取被创建账号则断言会失败）。
- **同事务证据的强度已如实标注**：`failed_create` 用例在实现前是空转的，实现后仍通过才构成证据；更强的"事务内可见性"证据见证据表末列所述边界，未在此冒充。
- S-08: verified — automated command passed; run_id=fbc11ea84a6b4920b09ce9ea0b903b58 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=fbc11ea84a6b4920b09ce9ea0b903b58 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=c7c30d1d4eaf4847ae5d3ce694d7c3a7 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=c7c30d1d4eaf4847ae5d3ce694d7c3a7 (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-002: 认证真实验收环境与种子

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 13-console-auth.backend.design.md#2.5.2 功能验收场景, 13-console-auth.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-06
- **Files**: `tests/acceptance/console_auth_flow/__init__.py`, `tests/acceptance/console_auth_flow/environment.py`, `tests/acceptance/console_auth_flow/test_environment.py`
- **Estimate**: 半天级；外部服务启动与等待另计

### Description

为后端场景建立真实验收环境：真实 Console 进程（真实 HTTP/Cookie/CSRF）+ 真实 PostgreSQL，租户级种子（ADMIN/BUILDER 账号、可控 `failed_attempts/locked_until`、可控 `expires_at` 的会话）与收尾清理，使 TASK-003..009 的断言有真实数据可依。

### Checklist

- [x] [B-06][integration] 以真实 Console 进程 + 真实 PostgreSQL 为边界编写用例：种子后按 `control.console_account` / `control.console_session` 逐行回读断言字段与状态符合预期。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_environment.py"]`。
- [x] 种子须覆盖：ADMIN 与 BUILDER 各一；一个 `enabled=false` 账号；一个 `locked_until > now` 的账号；一个 `expires_at` 剩余 <6h 的会话（供 B-02 临界）。
- [x] 收尾清理：租户内账号/会话残留为 0；用例结束不得残留 uvicorn/`muad_*.main` 进程（任何失败路径都要执行收尾）。
- [x] 复用既有验收栈原语（`ServiceProcess`/`free_port`/`run_db`），不另造进程管理。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN 与真实边界证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-06 | integration | 真实 Console 进程 + 真实 PostgreSQL + 租户级清理 | 种子字段/状态逐行回读一致；清理后残留 0；无孤儿进程 | tests/acceptance/console_auth_flow/test_environment.py / B-06 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_environment.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-06 | **真实 RED（两处，均为环境自身缺陷）**：① 首轮 `test_b06_seed_produces_expected_rows` 与 `test_b06_purge_is_idempotent_and_leaves_no_residue` 失败，报 `ProgrammingError` —— `control.console_session` **没有 `tenant_id` 列**（它按 `account_id` 关联），不能按租户列直接计数；这与 12-overview-dashboard 归档时踩到的是同一个既有事实。② 会话种子可用性断言触发 httpx 的 per-request cookie 弃用告警。 | 加专用计数器 `count_tenant_sessions()`（按「本租户账号名下的会话」计数）+ 把 cookie 移到 client 构造后：整文件 **5 passed**；ruff 干净；运行后无 uvicorn/`muad_*.main` 残留。 | `test_b06_stack_boots_and_dependencies_are_ready`（`/healthz` → `data.status=ok`、`/readyz` → `data.status=ready`，探针经 api-kit 封套）；`test_b06_seed_produces_expected_rows`（`console_account` 精确 4 行 + 会话精确 2 行——共享开发库里还有其它租户数据，取到精确数即证明租户隔离生效）；`test_b06_seeded_session_token_actually_authenticates`（**库内只存 sha256，但拿种子返回的明文令牌请求 `/auth/me` 必须 200 且返回该 builder**——这条是后续续期/撤销/越权场景的地基）；`test_b06_seeded_edge_accounts_are_usable_bounds`（禁用账号 → 401 `INVALID_CREDENTIALS`；锁定账号 → 423 `ACCOUNT_LOCKED`；ADMIN → 200 且 `role=ADMIN`——三个边界样本都真能用）；`test_b06_purge_is_idempotent_and_leaves_no_residue`（连续 purge 两次后账号/会话/审计行全 0，且清理前先断言 >0 保证灵敏度） | 真实 Console uvicorn 子进程（失败路径也走 `stop_auth_stack`）+ 真实 PostgreSQL 逐行回读与 SQL 计数 + 真实 HTTP 登录/会话请求；未 mock 业务服务、未覆盖业务路由。进程与 DB 原语复用 09 真实验收栈。 | verified |

**实现中的判断点（如实登记）**：

- **`control.console_session` 不按租户计数**：该表无 `tenant_id`，按 `account_id` 关联；专用计数器 `count_tenant_sessions()` 用子查询归属到本租户账号，避免"列不存在"的假失败（12 归档时同一坑）。
- **边界样本必须"真能用"而非只是数据**：种子不止写入行，B-06 还用真实 HTTP 证明三个样本的行为（禁用 401 / 锁定 423 / ADMIN 200），并证明**明文令牌能认证**——否则后续场景（B-02 续期、E-03 撤销）会建在沙地上。
- **审计行纳入清理**：`purge_tenant()` 连 `config_audit_log` 一并清（TASK-001 起登录/创建会写审计），否则重复运行会累积残留、污染精确计数断言。
- **按需最小栈**：本环境的 S-07（CLI + lifespan）需要真实进程与启动日志，故起真实 Console；其余集成场景仍走并发套件的 ASGI 夹具（更快的真实 PG 边界）。
- B-06: verified — automated command passed; run_id=5c95f9ea051d4dc8b455babe99b855bd (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=f58b7ab1bb09462baebf41f6692088a2 (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-003: 登录链真实验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#2.5.2 功能验收场景, 13-console-auth.backend.design.md#3.2 架构与流程, 13-console-auth.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-01, E-01, E-02, E-06, B-01, B-03, RULE-auth-001
- **Files**: `tests/acceptance/console_auth_flow/test_auth_acceptance.py`, `e2e/tests/console-auth.spec.ts`, `e2e/playwright.console-auth.config.ts`
- **Estimate**: 半天级

### Description

登录链的真实验收：成功登录（Cookie 双发、`last_login_at`、库内仅 sha256）、密码错误（计数 +1、不建会话）、5 次锁定状态机、禁用账号不泄露状态（未知用户假哈希等化时序）、多租户同名歧义、第 4/5 次失败临界。

### Checklist

- [x] [S-01][E2E] 以 Browser → Console HTTP → PostgreSQL 真实边界编写用例：已建档账号正确密码登录 → 200 `code=0`、`data={id,username,display_name,role}`、`muad_session` 为 HttpOnly 且 `muad_csrf` 可读、`last_login_at` 更新、库内会话只有 sha256 哈希（无令牌明文）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-01\""]`。
- [x] [E-01][integration] 正确用户名 + 错误密码 → 401 `INVALID_CREDENTIALS`、不建会话、不发 Cookie、`failed_attempts` +1。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e01"]`。
- [x] [E-02][integration] 连续 5 次错误后用正确密码 → 第 5 次后 `failed_attempts=0`、`locked_until=now+15min`；锁定期内 423 `ACCOUNT_LOCKED`（含正确密码）。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e02"]`。
- [x] [E-06][integration] 禁用账号 + 正确密码 → 401 `INVALID_CREDENTIALS`（不暴露"已禁用"）。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e06"]`。
- [x] [B-01][integration] 第 4 次失败 `failed_attempts=4` 不锁；第 5 次进入锁定并重置计数。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b01"]`。
- [x] [B-03][integration] 无 `X-Tenant-Id` 且跨租户重名 → 401 `INVALID_CREDENTIALS`（入口日志告警）；带租户头时正确账号可登录。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b03"]`。
- [x] [RULE-auth-001][integration] 作为唯一最终负责人：Console 访问控制只用 ADMIN/BUILDER 与统一依赖，不引入三元授权/绑定开关/到期授权，Console 账号不进入 Effective Capability。verifier argv：`["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py","-k","s04"]` 与 `-k schema_parity` 联合。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | Browser → Console HTTP → PostgreSQL | 200 封套 + 双 Cookie 属性 + `last_login_at` + 库内仅 sha256 | e2e/tests/console-auth.spec.ts / S-01 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-01\""]` | e2e_deferred |
| E-01 | integration | API → 真实 PostgreSQL | 401 契约码；无会话/无 Cookie；计数 +1 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-01 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e01"]` | verified |
| E-02 | integration | API → 真实 PostgreSQL | 锁定状态机与 423；锁定期内不校验密码 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-02 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e02"]` | verified |
| E-06 | integration | API → 真实 PostgreSQL | 禁用与未知/错误密码同码同态 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-06 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e06"]` | verified |
| B-01 | integration | API → 真实 PostgreSQL | 第 4/5 次临界行为 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / B-01 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b01"]` | verified |
| B-03 | integration | API → 真实 PostgreSQL | 跨租户同名歧义拒绝 + 带租户头可登录 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / B-03 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b03"]` | verified |
| RULE-auth-001 | integration | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界 | 两角色模型；无三元授权/绑定开关；账号不进 Effective Capability；原 verifier 全部通过 | tests/acceptance/console_auth_flow/test_auth_acceptance.py + 原 verifier / RULE-auth-001 | `["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | **验收类（不制造 RED）**：登录链基线已实现，故按仓库既定口径「验收类不制造实现 RED」，未伪造失败。真实记录的是**基础设施首次落地的可运行性**：新增 `e2e/playwright.console-auth.config.ts` 与 `tests/e2e/seed_console_auth.py` 后，先 `npm run build` 产出真实构建物再执行登记 argv，一次通过（Console 起在派生端口 8802）。 | 登记 argv → **1 passed (871ms)**；运行后无 uvicorn/vite/playwright 残留。 | `S-01 登录成功签发双 Cookie、更新 last_login_at 且库内只存令牌哈希`：经**浏览器上下文**（`page.request`）发真实登录请求 → 200 且 `code="0"`、`data` 键集恰为 `{display_name,id,role,username}`、`role=ADMIN`；`muad_session` 下发且 **HttpOnly=true**、`SameSite=Strict`，`muad_csrf` 下发且 **HttpOnly=false**（JS 可读，双提交前提）；库内核对（真实 PostgreSQL）：`last_login_at` 已更新、会话行 ≥1、**库内 token_hash 恰等于该明文令牌的 sha256**、且**明文不存在于库内** | 真实 Chromium（channel=chrome）+ 真实 Console 进程（真实 PostgreSQL + 真实 Set-Cookie）+ **真实前端构建产物**（vite preview 反代 `/api`）；库内断言经 `pnpm` 同款 `uv run python -m tests.e2e.seed_console_auth check` 在真实库上完成，令牌明文经**环境变量**传递（不进 argv/进程列表）。 | e2e_deferred（本地已 GREEN，终验归 verify-e2e） |
| E-01 | 验收类（登录链基线已实现）——首跑即 GREEN，未伪造失败。 | `-k e01` → 1 passed；整文件 8 passed（含 TASK-001 的 3 条）。 | `test_e01_wrong_password_increments_counter_without_session`：错误密码 → 401 `INVALID_CREDENTIALS`；响应**不含** `muad_session`/`muad_csrf`；库内 `failed_attempts == 1`；该账号会话行数为 0 | 真实 Console ASGI 全栈（登录会话 + CSRF）+ 真实 PostgreSQL 逐行回读与计数；无 mock。 | verified |
| E-02 | 验收类，同上。 | `-k e02` → 1 passed。 | `test_e02_fifth_failure_locks_and_correct_password_gets_423`：连续 5 次错误均 401 → 库内 `failed_attempts == 0`（**达阈值后重置**）、`locked_until > now`；随后用**正确密码** → **423 `ACCOUNT_LOCKED`** 且不建会话 | 同上（真实 PG 回读锁定状态机）。 | verified |
| E-06 | 验收类，同上。 | `-k e06` → 1 passed。 | `test_e06_disabled_account_is_indistinguishable_from_wrong_password`：先断言种子账号确实 `enabled=false`（避免空转），再以**正确密码**登录 → 401 `INVALID_CREDENTIALS`（与密码错误同码同态，不暴露"已禁用"），且不建会话 | 同上。 | verified |
| B-01 | 验收类，同上。 | `-k b01` → 1 passed。 | `test_b01_fourth_failure_does_not_lock`：逐次断言 `failed_attempts` 为 1/2/3/4 **且** `locked_until is None`（第 4 次不锁）；第 5 次进入锁定由 E-02 取证 | 同上。 | verified |
| B-03 | 验收类，同上。 | `-k b03` → 1 passed。 | `test_b03_cross_tenant_duplicate_username_requires_tenant_header`：在另一租户造同名账号后，**不带** `X-Tenant-Id` 登录 → 401（全局解析歧义拒绝）；**带**租户头 → 200 且返回本租户账号；用例自建自清（finally 删除另一租户账号） | 同上（真实 PG 造跨租户同名数据）。 | verified |
| RULE-auth-001 | 验收类（随本任务一并取证）。 | 原 verifier 复跑通过：`tests/console_platform/test_user_side_relations.py -k s04` + `pytest tests -k schema_parity` → **35 passed**。 | 原 verifier 断言（Console 访问控制只用 ADMIN/BUILDER 与统一依赖；无三元授权/绑定开关/到期授权；Console 账号不进入 Effective Capability） | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界。 | verified |

**实现中的判断点（如实登记）**：

- **登录链的 RED 为何缺失**：本模块认证链在基线中已实现（`api/auth.py` + `tests/console_auth/` 既有五个测试文件），本任务属**验收类**，故无实现级 RED——按仓库既定口径如实记录「验收类不制造 RED」，未伪造失败。这与 TASK-001（审计缺口，有真实 RED）形成对照。
- **S-01 用浏览器上下文发请求而非驱动登录表单**：design 给 S-01 的边界是 `Browser → API → Postgres`，断言对象是**登录契约**（封套字段、双 Cookie 属性、库内令牌形态）；驱动表单属前端场景 S-09（→ S-09 由 TASK-011 取证）。两者边界不重叠、也不降级。
- **令牌明文不进 argv**：库内核对需要明文令牌算 sha256 对比，故经**环境变量**传参（argv 会出现在进程列表）；同时断言「明文不存在于库内」，把该检查做成双向的。
- **B-03 自建自清**：跨租户同名数据由用例在真实库里造并 `finally` 删除，不污染共享开发库。
- S-01: e2e_deferred — automated command e2e_deferred (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=f13bf54521084fe0aa04889c94a75304 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=f13bf54521084fe0aa04889c94a75304 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=f13bf54521084fe0aa04889c94a75304 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=f13bf54521084fe0aa04889c94a75304 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=f13bf54521084fe0aa04889c94a75304 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=f13bf54521084fe0aa04889c94a75304 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=f3d69a1e89234b14826211fc1f9350aa (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=f3d69a1e89234b14826211fc1f9350aa (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=f3d69a1e89234b14826211fc1f9350aa (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=f3d69a1e89234b14826211fc1f9350aa (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=f3d69a1e89234b14826211fc1f9350aa (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=f3d69a1e89234b14826211fc1f9350aa (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-004: 会话链真实验收（续期/过期撤销）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#3.2 架构与流程, 13-console-auth.backend.design.md#3.4 接口设计, 13-console-auth.backend.design.md#2.5.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-02, E-03, B-02
- **Files**: `tests/acceptance/console_auth_flow/test_auth_acceptance.py`
- **Estimate**: 半天级

### Description

会话解析与滑动续期的真实验收：剩余 <6h 续期为 now+12h 并更新 `last_seen_at`；过期/撤销/未知令牌一律 401 `UNAUTHORIZED`；剩余恰为 6h 时不续期、小于 6h 时续期（临界）。

### Checklist

- [x] [S-02][integration] 构造剩余 <6h 的会话后调用 `/auth/me` → 200 返回账号，`expires_at` 续期为 now+12h、`last_seen_at` 更新（库内回读）。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s02"]`。
- [x] [E-03][integration] 过期、已撤销、未知令牌访问 `/me` → 401 `UNAUTHORIZED`，Envelope 含 `trace_id`。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e03"]`。
- [x] [B-02][integration] 剩余恰为 6h 不续期；小于 6h 续期到 now+12h。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b02"]`。
- [x] 断言会话读取只认未软删、未撤销、未过期三条件同时成立（构造三者各自的失效样本）。
- [x] 时间断言用绝对时刻比较（不靠字符串截断），存储为 timestamptz。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | Service → 真实 PostgreSQL | 续期到 now+12h；`last_seen_at` 更新 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / S-02 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s02"]` | verified |
| E-03 | integration | API → 真实 PostgreSQL | 三类失效同码；封套含 trace_id | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-03 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e03"]` | verified |
| B-02 | integration | Service → 真实 PostgreSQL | 6h 临界不续期 / <6h 续期 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / B-02 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b02"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-02 | **验收类（不制造 RED）**：会话解析与滑动续期在基线已实现（`AuthService`/`api/auth.py`），按仓库既定口径未伪造失败。 | `-k s02` → 1 passed；整文件 11 passed（含 TASK-001/003 的 8 条）。 | `test_s02_sliding_renewal_extends_expiry_when_under_half`：在真实库中为 builder 签发**剩余 3h** 的会话 → 以该令牌调 `/auth/me` 得 200 且返回本账号 → 回读断言 `expires_at` **严格增大**且续期后剩余落在 `(11h, 12h]`（即目标 now+12h），`last_seen_at` 不早于原值 | 真实 Console ASGI 全栈 + 真实 PostgreSQL（会话行签发前后各回读一次）；无 mock。 | verified |
| E-03 | 验收类，同上。 | `-k e03` → 1 passed。 | `test_e03_expired_revoked_and_unknown_tokens_are_unauthorized`：分别构造**已过期**（剩余 -1h）、**已撤销**（`revoked_at` 置位）、**未知**令牌 → 三者均 401、`code=="UNAUTHORIZED"`、且封套含非空 `trace_id` | 同上（三类失效样本都在真实库里构造，非内存替身）。 | verified |
| B-02 | 验收类，同上。 | `-k b02` → 1 passed。 | `test_b02_six_hour_boundary_renews_only_below`：剩余**略多于 6h** → `/auth/me` 200 且 `expires_at` **不变**（不续期）而 `last_seen_at` 仍更新；剩余**略少于 6h** → `expires_at` 增大（续期） | 同上。 | verified |

**实现中的判断点（如实登记）**：

- **6h 边界取样用 ±5s 而非"恰好 6h"**：会话在请求前写入、请求后回读，wall-clock 必然流逝；若按字面取"恰好 6h"，回读时剩余必已 <6h 而触发续期——断言会与执行速度耦合、变成 flaky 用例。改取临界**两侧**各 5s，既检验"`>6h` 不续期 / `<6h` 续期"的分支，又不依赖亚秒精度。该取舍已写在用例注释里。
- **三类失效样本都从真实库构造**：过期用负剩余、撤销直接置 `revoked_at`、未知用随机串——避免只测"一种失效"就宣称覆盖 E-03。
- **断言用绝对时刻比较**：`expires_at` 直接比 TimeZone-aware datetime，不做字符串截断或"看起来变大了"这类弱断言。
- **顺手清掉弃用告警**：首次实现用 httpx 的 per-request `cookies=` 触发 DeprecationWarning，改为显式 `Cookie` 请求头（语义相同、无告警）。
- S-02: verified — automated command passed; run_id=591d55dbbb164b2c86a1541e62c548be (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=591d55dbbb164b2c86a1541e62c548be (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=591d55dbbb164b2c86a1541e62c548be (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=95eb9e01841b4c02a83438c3714b76b7 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=95eb9e01841b4c02a83438c3714b76b7 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=95eb9e01841b4c02a83438c3714b76b7 (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-005: 登出撤销真实验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#3.4 接口设计, 13-console-auth.backend.design.md#2.5.2 功能验收场景
- **Spec-Refs**: harness-rel#RULE-rel-001
- **Acceptance-Refs**: S-03, RULE-rel-001
- **Files**: `tests/acceptance/console_auth_flow/test_auth_acceptance.py`, `e2e/tests/console-auth.spec.ts`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

登出撤销的真实验收：带 CSRF 登出 → `{logged_out:true}`、Cookie 清除、会话 `revoked_at` 非空、再次 `/me` 401；重复登出幂等。

### Checklist

- [x] [S-03][E2E] 以 Browser → Console HTTP → PostgreSQL 真实边界编写用例：登录后带 CSRF 调用登出 → 200 `{logged_out:true}`、Cookie 被清除、库内会话 `revoked_at` 非空、再次 `/me` 401。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-03\""]`。
- [x] [RULE-rel-001][integration] 作为唯一最终负责人：本模块无关系集合写接口；账号/会话/密码均为单端点单事务原子变更，禁止全量 PUT 覆盖。verifier argv：`["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]`。
- [x] 重复登出幂等：已撤销/不存在会话再次登出仍返回成功（不报错）。
- [x] 登出后不得残留可用的 `muad_session`（响应头断言清除属性）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | E2E | Browser → Console HTTP → PostgreSQL | `{logged_out:true}`；Cookie 清除；`revoked_at` 非空；再访问 401 | e2e/tests/console-auth.spec.ts / S-03 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-03\""]` | e2e_deferred |
| RULE-rel-001 | integration | 真实 Console HTTP + 原 verifier 真实边界 | 无关系集合写接口；单端点原子变更；原 verifier 全部通过 | 原 verifier / RULE-rel-001 | `["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | **验收类（不制造 RED）**：登出链基线已实现；真实记录的是**接口层的语义边界**——`logout` 路由位于认证组，故"重复登出"会先被会话校验挡下（401），design §3.4 描述的「不存在或已撤销直接返回成功（幂等）」只能在 service 层取证。该发现改变了本任务幂等证据的取证层级（见判断点）。 | 登记 argv `-g "S-03"` → **1 passed (763ms)**；集成侧 `-k s03` 两条 → 通过（整文件 13 passed）；`RULE-rel-001` 原 verifier → **3 passed**；运行后无 uvicorn/vite/playwright 残留。 | `S-03 登出撤销会话并清除 Cookie，已撤销令牌再访问 /me 得 401`（浏览器）：真实登录 → 带 `X-CSRF-Token` 调登出 → 200 且 `data.logged_out===true`；`muad_session` 已从浏览器上下文清除；库内核对（按**该令牌**查会话）`session_found=true` 且 `revoked=true`；再用已撤销令牌访问 `/auth/me` → **401**。集成侧 `test_s03_logout_revokes_session_and_blocks_reuse`：同一链路在真实 PG 上回读 `revoked_at` 非空并断言复用被拒；`test_s03_logout_is_idempotent_at_service_layer`：`AuthService.logout` 对未知/空令牌直接返回不抛错 | 真实 Chromium（channel=chrome）+ 真实 Console 进程（真实 Cookie 清除 + Set-Cookie 过期属性）+ 真实 PostgreSQL（按令牌核对 `revoked_at`）；库内核对经环境变量传令牌（不进 argv）。 | e2e_deferred（本地已 GREEN，终验归 verify-e2e） |
| RULE-rel-001 | 验收类（随本任务一并取证）。 | 原 verifier 复跑通过：`tests/console_platform/test_user_side_relations.py` → **3 passed**。 | 原 verifier 断言（本模块无关系集合写接口；账号/会话/密码均为单端点单事务原子变更，禁止全量 PUT 覆盖） | 真实 Console HTTP + 真实 PostgreSQL + 原 verifier 真实边界。 | verified |

**实现中的判断点（如实登记）**：

- **幂等的取证层级由接口结构决定**：`/api/v1/auth/logout` 挂在**认证组**（`authenticated.include_router(auth_router)`），因此"用已撤销令牌重复登出"会先被 `get_current_account` 判 401——API 层不可能观察到"重复登出返回成功"。design §3.4 的幂等描述针对的是 **service 层**（`取 muad_session → sha256 查会话；不存在或已撤销直接返回成功`），故幂等断言落在 `AuthService.logout` 上。这是**按代码事实调整取证层级**，不是把断言降级。
- **库内核对按令牌而非租户聚合**：`check-logout` 用令牌的 sha256 定位该会话并断言 `revoked_at` 置位。同文件里 S-01 也会登录（留下未撤销会话），若按"租户内全部会话均已撤销"断言，整跑时必然误报。
- **Cookie 清除是真实浏览器断言**：`page.context().cookies()` 在登出后不再含 `muad_session`——验的是真实 Set-Cookie 过期行为，而不是读响应头字符串。
- S-03: e2e_deferred — automated command e2e_deferred; run_id=a95150741d954e45a1befb5fd02beef7 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=8a03dc91a1ae47e981cc67aeeb6c828f (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-006: 修改密码真实验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#3.4 接口设计, 13-console-auth.backend.design.md#2.5.2 功能验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-04
- **Files**: `tests/acceptance/console_auth_flow/test_auth_acceptance.py`, `e2e/tests/console-auth.spec.ts`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

修改密码的真实验收：登录后提交正确当前密码 + 合规新密码 → `{changed:true}`；旧密码登录 401、新密码登录 200。

### Checklist

- [x] [S-04][E2E] 以 Browser → Console HTTP → PostgreSQL 真实边界编写用例：登录后带 CSRF 提交 `current_password` + 合规 `new_password` → 200 `{changed:true}`；随后旧密码登录 401 `INVALID_CREDENTIALS`、新密码登录 200。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-04\""]`。
- [x] 断言库内 `password_hash` 为新 argon2id 哈希且不等于旧值（真实回读），无明文落库。
- [x] 断言容器：新密码 < 12 位 → 422 `COMMON_VALIDATION_ERROR`；当前密码错误 → 401 `INVALID_CREDENTIALS`。
- [x] 改密不得撤销其他既有会话（design §2.4 技术债③的 V1 既定行为），以既有会话仍可用为断言。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | E2E | Browser → Console HTTP → PostgreSQL | `{changed:true}`；新旧密码登录结果反转；哈希更新且无明文 | e2e/tests/console-auth.spec.ts / S-04 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-04\""]` | e2e_deferred |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-04 | **验收类（不制造 RED）**：改密链基线已实现；本任务真实记录的是**用例间的状态污染风险**——S-04 会真的改掉某个账号的密码，而同文件后续用例（S-12 以 builder 登录）依赖种子密码，故设计为「用 builder 账号 + 断言完成后**改回原密码**」并在末尾断言还原成功。 | 登记 argv `-g "S-04"` → **1 passed (562ms)**；集成侧 `-k s04` 两条 → 通过（整文件 15 passed）；运行后无 uvicorn/vite/playwright 残留。 | `S-04 修改密码成功后旧密码失效、新密码可用（用后即改回…）`（浏览器）：真实登录 → 带 CSRF 提交正确当前密码 + 合规新密码 → 200 且 `data.changed===true`；**旧密码登录 401**、**新密码登录 200**；随后用新密码改回原密码并断言旧（原）密码重新可用。集成侧 `test_s04_change_password_rotates_hash_and_invalidates_old_password`：除同一链路外，还回读库内 `password_hash` 断言**已轮换**且以 `$argon2id$` 开头，并断言**既有会话仍可用**（design §2.4 技术债③：V1 不撤销其他会话）；`test_s04_short_new_password_and_wrong_current_password_are_rejected`：新密码 <12 位 → 422 `COMMON_VALIDATION_ERROR`、当前密码错误 → 401 `INVALID_CREDENTIALS`，且失败后原密码仍可登录（失败路径不改动密码） | 真实 Chromium + 真实 Console 进程（真实会话与 CSRF）+ 真实 PostgreSQL（`password_hash` 逐行回读比较）。 | e2e_deferred（本地已 GREEN，终验归 verify-e2e） |

**实现中的判断点（如实登记）**：

- **用例自重还原，避免污染同文件其它场景**：E2E spec 以 `workers: 1` 共用一份租户与种子；S-04 若把 builder 密码改成新值就不还原，后续 S-12（ADMIN/BUILDER 菜单差异）会登录失败。故 S-04 用 builder（不动 admin，S-01/S-03/S-13 都依赖 admin）、并在断言完成后**改回原密码**且断言还原成功。这是本仓"共享租户的顺序假设"那类坑的正面处理，而不是靠执行顺序侥幸。
- **哈希轮换做库内比较而非"看起来变了"**：直接比较改密前后的 `password_hash` 值并断言前缀为 `$argon2id$`，避免只验接口返回而漏掉"其实没写库"。
- **失败路径也断言副作用为零**：<12 位与当前密码错误两条边界之后，再断言原密码仍能登录——否则一个"失败却已改密"的实现会漏过。**注意**：「不撤销既有会话」在本任务被作为**断言固定下来**，它是 design §2.4 明示的 V1 既定行为（技术债③），若将来改为撤销会话，此断言会如期失败并提醒同步设计。
- S-04: e2e_deferred — automated command e2e_deferred; run_id=34418a26008c441b9ba9464130fd182f (confirmed_by: runner)
- S-04: e2e_deferred — automated command e2e_deferred; run_id=affc042eb4cb4e41acaed5b901a142f2 (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-007: 账号管理与访问控制真实验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#3.4 接口设计, 13-console-auth.backend.design.md#2.5.1 业务规则与约束, 13-console-auth.backend.design.md#2.5.2 功能验收场景
- **Spec-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002
- **Acceptance-Refs**: S-05, S-06, E-04, E-05, E-08, B-08, RULE-api-001, RULE-api-002
- **Files**: `tests/acceptance/console_auth_flow/test_auth_acceptance.py`, `e2e/tests/console-auth.spec.ts`
- **Estimate**: 半天级

### Description

账号管理与访问控制的真实验收：ADMIN 创建 BUILDER 账号（无 `password_hash`）、新账号可登录、列表为统一分页封套且字段与 docs/15 一致；CSRF 缺失/伪造 403、BUILDER 越权 403、未登录访问受保护端点 401 且 `/healthz` 与 `/auth/login` 保持公开。

### Checklist

- [x] [S-05][E2E] ADMIN 登录后创建 BUILDER 账号 → 200 返回账号（**无 `password_hash`**）；新账号可登录；账号列表含新账号。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-05\""]`。
- [x] [S-06][E2E] ADMIN 打开账号列表 → 返回账号集合，字段与 docs/15 一致且无敏感字段。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-06\""]`。
- [x] [E-04][integration] 非安全方法缺失或伪造 `X-CSRF-Token` → 403 `FORBIDDEN` 且请求不落业务变更。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e04"]`。
- [x] [E-05][integration] BUILDER 访问 `/api/v1/accounts` → 403 `FORBIDDEN`，不发生查询/写入。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e05"]`。
- [x] [E-08][integration] 未登录访问 `/api/v1/agents` 或 `/api/v1/accounts` → 401 `UNAUTHORIZED`；`/healthz`、`/auth/login` 公开。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e08"]`。
- [x] [RULE-api-001][integration] 作为唯一最终负责人：统一 Envelope 与列表分页（`page>=1`、`1<=page_size<=100`），错误码只来自 `config/api-messages.yaml`。verifier argv：`["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`。
- [x] 断言同租户重名 → 409 `ACCOUNT_USERNAME_EXISTS`（含并发唯一约束兜底），且列表分页边界（`page_size=101` → 422）。
- [x] [B-08][integration] 创建账号支持 `Idempotency-Key`：同 key 同指纹重放返回**同一账号且不重复创建**（库内账号数不变）；同 key 异指纹返回 `IDEMPOTENCY_MISMATCH`；并发同 key 由 partial unique 兜底、落败者读取首次结果。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b08"]`。
- [x] [RULE-api-002][integration] 作为唯一最终负责人：创建/上传类 POST 的 `Idempotency-Key` 幂等（**0.1 承接**：本模块确有创建类 POST，与 12 的只读模块不同）。verifier argv：`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`。
- [x] 实现或补齐：复用仓内**既有共享幂等表**（不新建第二张表，口径同 11 的审计导出），指纹 = 规范化 JSON 的 sha256（含 endpoint/tenant/actor/资源与关键参数）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | E2E | Browser → Console HTTP → PostgreSQL | 创建成功且响应无 `password_hash`；新账号可登录；列表含之 | e2e/tests/console-auth.spec.ts / S-05 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-05\""]` | e2e_deferred |
| S-06 | E2E | Browser → Console HTTP → PostgreSQL | 列表为分页封套；字段与 docs/15 一致；无敏感字段 | e2e/tests/console-auth.spec.ts / S-06 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-06\""]` | e2e_deferred |
| E-04 | integration | API → CSRF 校验 | 403 且无业务变更落库 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-04 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e04"]` | verified |
| E-05 | integration | API → RBAC | 403 且无查询/写入 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-05 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e05"]` | verified |
| E-08 | integration | API → 全局认证 | 401 契约码；公开端点白名单生效 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / E-08 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","e08"]` | verified |
| RULE-api-001 | integration | 真实 Console HTTP + 原 verifier 真实边界 | 封套/分页边界/catalog 错误码；原 verifier 全部通过 | 原 verifier / RULE-api-001 | `["bash","-lc","uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py"]` | verified |
| B-08 | integration | 真实 Console HTTP → 共享幂等表(PostgreSQL) | 同 key 同指纹重放同一账号且不重复创建；异指纹 `IDEMPOTENCY_MISMATCH`；并发落败者读首次结果 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / B-08 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","b08"]` | verified |
| RULE-api-002 | integration | 真实 Console HTTP → 幂等表(PostgreSQL) + 原 verifier 真实边界 | 创建类 POST 幂等；异指纹 409；原 verifier 全部通过 | tests/acceptance/console_auth_flow/test_auth_acceptance.py + 原 verifier / RULE-api-002 | `["bash","-lc","uv run pytest -q tests/acceptance/console_auth_flow/test_auth_acceptance.py -k b08 && uv run pytest -q tests/console_skill/test_import_idempotency.py"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | 验收类（创建账号链基线已实现；本任务的**真实 RED 在 B-08 的幂等实现上**，见该行）。 | 登记 argv `-g "S-05"` → **1 passed (519ms)**。 | `S-05 ADMIN 创建 BUILDER 账号…`（浏览器）：ADMIN 真实登录 → 带 CSRF 创建 BUILDER → 200 且 `data` 键集恰为 `{display_name,id,role,username}`、**断言 `password_hash` 不在响应中**；新账号**真实登录成功**；重新以 ADMIN 拉列表断言新账号在册 | 真实 Chromium + 真实 Console（真实会话/CSRF）+ 真实 PostgreSQL。 | e2e_deferred（本地已 GREEN，终验归 verify-e2e） |
| S-06 | 验收类。 | `-g "S-06"` → **1 passed (175ms)**。 | `S-06 ADMIN 账号列表为分页封套且无敏感字段`：`data` 键集恰为 `{items,page,page_size,total}`、`page=1`、`page_size=20`、`total>=1`；**逐项断言键集恰为四字段**且 `password_hash`/`failed_attempts`/`locked_until` 均不出现 | 同上（真实列表响应体逐项校验）。 | e2e_deferred（终验归 verify-e2e） |
| E-04 | 验收类。 | `-k e04` → 1 passed（整文件 24 passed）。 | `test_e04_missing_or_forged_csrf_is_forbidden`：缺失与**伪造** `X-CSRF-Token` 两种情形均 403 `FORBIDDEN`；并断言租户内 `CONSOLE_ACCOUNT/CREATE` 审计计数不变（CSRF 失败**不落业务变更**） | 真实 Console ASGI 全栈 + 真实 PostgreSQL 计数回读。 | verified |
| E-05 | 验收类。 | `-k e05` → 1 passed。 | `test_e05_builder_cannot_reach_accounts`：BUILDER 读列表与创建账号**均** 403 `FORBIDDEN`；创建被拒后审计计数不变 | 同上。 | verified |
| E-08 | 验收类。 | `-k e08` → 1 passed。 | `test_e08_unauthenticated_rejected_while_public_routes_stay_open`：未登录访问 `/api/v1/agents`、`/api/v1/accounts` 均 401 `UNAUTHORIZED`；`/healthz` 200、`/auth/login` 仍可用（公开白名单未被收口破坏） | 同上。 | verified |
| B-08 | **真实 RED（本任务唯一的实现缺口）**：`create_account` 原先不接受 `Idempotency-Key`，接入前该用例失败于「第二次带同 key 的请求仍会走创建路径」；实现后转 GREEN。附带发现并修复一处**连带破坏**：`create_account` 返回类型由 `ConsoleAccount` 改为 `(account, replayed)` 后，`cli.py` 的调用点仍在解包单个对象（`account = await service.create_account(...)`），若不同步修会让 `create-admin` 静默拿到 tuple——已同步修复（CLI 不传 key，`replayed` 恒为 False）。 | `-k b08` → 1 passed；联合 argv（B-08 + 原 verifier）→ 1 passed + **5 passed**；整文件 24 passed；ruff/mypy（改动文件）干净。 | `test_b08_account_creation_is_idempotent_by_key`：同 key **同指纹**重放 → 200 且返回**同一账号 id**；租户内账号数**只 +1**（重放不重复建号）；**审计也只 +1**（重放不产生新变更、不写新审计）；同 key **异指纹**（role 改 ADMIN）→ 409 `IDEMPOTENCY_MISMATCH` 且账号数不变 | 真实 Console ASGI 全栈 + 真实 PostgreSQL（账号行数与审计行数双向计数）+ 共享幂等表（复用 `control.skill_import_idempotency`，**未新建第二张表**）。 | verified |
| RULE-api-001 | 验收类（随本任务一并取证）。 | 原 verifier 复跑通过 → **18 passed**。 | 原 verifier 断言（统一封套、列表分页边界、catalog 错误码） | 真实 Console HTTP + 原 verifier 真实边界。 | verified |
| RULE-api-002 | 本规则为**承接项**（用户决定），RED 见 B-08 行。 | 联合 argv 通过：B-08 1 passed + 原 verifier `tests/console_skill/test_import_idempotency.py` **5 passed**。 | 创建类 POST 的 `Idempotency-Key` 幂等：partial unique + 指纹（规范化 JSON 的 sha256，含 endpoint/tenant/资源与关键参数）+ 同 key 同指纹重放 + 异指纹 409 + advisory lock 串行化并发 | 真实 Console HTTP + 真实 PostgreSQL 幂等表 + 原 verifier 真实边界。 | verified |

**实现中的判断点（如实登记）**：

- **未新建第二张幂等表**：复用仓内既有的 `control.skill_import_idempotency`（含 `tenant_id/idempotency_key/endpoint/request_fingerprint/response_json`），口径同 11-audit-observability 的审计导出所述「复用共享幂等表」。
- **幂等实现沿用 `channel_service` 的三件套**：`pg_advisory_xact_lock`（按 `tenant|key|endpoint` 派生锁键）→ 重放查询（同 key 异指纹即 `IDEMPOTENCY_MISMATCH`）→ 首次落库记录响应。仓库既有多个模块各自实现该模式（`channel_service`、`submissions`、`run_submission`），故本处**沿用同款**而非另立抽象，避免跨模块重构。
- **重放不写新审计**：`create_account` 返回 `(account, replayed)`，API 层据此跳过审计——重放没有产生新变更，写审计会让"变更条数"与事实不符。该行为被 B-08 用**审计计数**断言固定。
- **接口签名变更的连带修复**：`create_account` 改为返回元组属必要代价（调用方需要知道是否重放），但**必须同步全部调用点**——`cli.py` 是第二个调用方，已在同一任务内修复并纳入回归（S-07 会覆盖 CLI 路径）。此处若非主动 grep 调用方，会留下一个静默的类型错配。
- S-05: e2e_deferred — automated command e2e_deferred; run_id=35cb55ae90c245d09ec473ba3784b211 (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=35cb55ae90c245d09ec473ba3784b211 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=35cb55ae90c245d09ec473ba3784b211 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=35cb55ae90c245d09ec473ba3784b211 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=35cb55ae90c245d09ec473ba3784b211 (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=35cb55ae90c245d09ec473ba3784b211 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=0a3df697e790499ca90e4c2866826317 (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=0a3df697e790499ca90e4c2866826317 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=0a3df697e790499ca90e4c2866826317 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=0a3df697e790499ca90e4c2866826317 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=0a3df697e790499ca90e4c2866826317 (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=0a3df697e790499ca90e4c2866826317 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=30304a0f6a4643ebae7454bebebedb0d (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=30304a0f6a4643ebae7454bebebedb0d (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=30304a0f6a4643ebae7454bebebedb0d (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=30304a0f6a4643ebae7454bebebedb0d (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=30304a0f6a4643ebae7454bebebedb0d (confirmed_by: runner)
- B-08: verified — automated command passed; run_id=30304a0f6a4643ebae7454bebebedb0d (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-008: CLI create-admin 与启动自检验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#3.4 接口设计, 13-console-auth.backend.design.md#2.3 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-07
- **Files**: `tests/acceptance/console_auth_flow/test_auth_acceptance.py`, `apps/console-platform/backend/src/muad_console_platform/application/auth_service.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

CLI 与启动自检的真实验收：默认租户无账号时 lifespan 记录 `console_no_accounts_run_cli_create_admin` 告警；`create-admin --username ... --role ADMIN` 输出账号信息且 exit 0；建档后告警消失并可登录；密码 <12 位时退出码 2 且不写库。

自检口径修正（本次实现缺口）：`AuthService.has_any_account()` 原为 `count_all() > 0`（全库判定），但 `main.py` 已按 `default_tenant_id` 构造服务——全库判定会让任一租户有账号就掩盖「默认租户无账号 ⇒ 无法登录」的静默故障，也使 S-07 无法在共享库上构造前置。改为按本租户 `count()` 判定（design 3.4 同步措辞）。

### Checklist

- [x] [S-07][integration] 以 CLI → 真实 PostgreSQL 与真实 lifespan 启动 → 日志为边界编写用例：默认租户无账号时启动断言告警落盘；执行 `create-admin` 断言 stdout 含账号信息、exit 0；再启动断言告警消失且该账号可登录。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s07"]`。
- [x] 断言 `--password` 长度 < 12 时退出码 2 且**不发起 DB 写入**。
- [x] 断言 `--role` 默认 ADMIN、`--tenant` 默认 default；stdout 文案与 design 一致。
- [x] 断言无 `DATABASE_URL` 时记录 `console_account_check_skipped_database_url_missing`、查询异常记录 `console_account_check_failed`（不静默吞错）。
- [x] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | integration | CLI → 真实 PostgreSQL；真实 lifespan → 日志 | 告警按**本租户**判定并落盘；stdout/exit 0；建档后可登录；短密码 exit 2 不写库 | tests/acceptance/console_auth_flow/test_auth_acceptance.py / S-07 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_acceptance.py","-k","s07"]` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-07 | **真实 RED（本任务唯一的实现缺口）**：`AuthService.has_any_account()` 原为 `count_all() > 0`（**全库**判定），而 `main.py:39` 已按 `default_tenant_id` 构造服务——共享开发库另有 60 个活跃账号，故全新空租户启动时自检判定「有账号」。契约 argv 首跑：`test_s07_cli_create_admin_and_startup_warning` 失败于首次断言——落盘文件 `logs-before/muad-console-platform/2026-09-27.log` **记录数 = 0**（`assert None is not None`），同批另 3 个用例 passed。实现改为按本租户 `count()` 判定后转 GREEN。 | `-k s07` → **4 passed（7.68s）**；整文件（TASK-001..007 + 008）**28 passed**；`tests/acceptance/console_auth_flow/` 整目录 **28 passed**；ruff 干净；`auth_service.py` mypy 干净。 | `test_s07_cli_create_admin_and_startup_warning`：①无账号租户启动 → `{LOG_DIR}/muad-console-platform/{date}.log` 出现 `console_no_accounts_run_cli_create_admin`（断言 `level=WARNING`、`service=muad-console-platform`，且先回读该租户账号数 = 0）；②CLI 建档 stdout **逐字**等于 `created console account <u> role=ADMIN tenant=<t>` 且 exit 0；③短密码 exit 2、stderr 含 `at least 12 characters`、**该用户名查无此行**（不写库）；④建档后重启 → 先回读账号数 = 1 再断言落盘无该告警，并**真实 HTTP 登录** 200 且 `role=ADMIN`。`test_s07_cli_defaults_to_admin_role_and_default_tenant`：省略 flag → stdout `role=ADMIN tenant=default`，库内行 `tenant_id=default`、`role=ADMIN`。`test_s07_self_check_skips_without_database_url`：`DATABASE_URL=""` → 落盘恰为该一条 `console_account_check_skipped_database_url_missing`。`test_s07_self_check_logs_query_failure`：库可达但无该表 → 落盘恰为该一条 `console_account_check_failed`（`level=ERROR` 且 `exception` 字段含真实 `UndefinedTableError` 堆栈）。 | 真实 uvicorn Console 子进程（真实 lifespan，起两次）+ **真实 logging-kit 落盘文件**（只解析 `{LOG_DIR}/muad-console-platform/{date}.log`，不误取子进程 stdout 捕获文件）+ 真实 PostgreSQL（账号数/行内容回读与精确清理）+ 真实 CLI 子进程（`sys.executable -m muad_console_platform.cli`）+ 真实 HTTP 登录。未 mock 任何组件。 | verified |

**实现中的判断点（如实登记）**：

1. **口径裁决（用户确认）**：design S-07 原文「空库启动」在共享开发库上不可构造（`muad` 角色无 `CREATEDB`，环境内也无空库），且全库判定会掩盖「默认租户无账号 ⇒ 无法登录」的静默故障。经用户裁决改为**按 `default_tenant_id` 租户判定**，design `#2.5.2 S-07` 与 `#3.4 形态 B` 的措辞同步改写为「默认租户无账号」。
2. **缺席断言的强度**：④的「告警消失」是缺席断言，可信度来自**同构对照**——两次启动的进程形态、环境变量、租户完全一致，唯一差异是该租户是否已有账号；run1 已证明同一落盘链路会产出该记录，run2 前先回读账号数 = 1。故排除「链路静默失效」。
3. **两个告警分支的可达性（如实记录，非本次改动引入）**：`console_account_check_skipped_database_url_missing` 在真实 lifespan 中不可达——`validate_startup` 会先以 `database_url is not configured` 阻断启动，故以真实子进程直接驱动 `_warn_if_no_accounts()`（真实 logging 落盘，无 mock）；`console_account_check_failed` 的边界取「库可达但 `control.console_account` 不存在」（系统库 `postgres`，只读、报错即返回）。**未覆盖**：连接级失败（端口不可达 / 目标库不存在）不被 `except SQLAlchemyError` 捕获（实测 asyncpg 的连接异常未被 SQLAlchemy 包装），该类失败在 lifespan 中先被 `validate_startup` 阻断，属既有设计边界，本次未改动实现。
4. **`--tenant` 默认值用例的清理**：`default` 是共享租户，故按用户名精确清理（`_purge_account`），未使用租户级清理。
5. **校验器状态（与实现分开看）**：`.code-flow/validation.yml` 的 mypy 校验器对 `tests/acceptance/console_auth_flow/*.py` **在改动前即为红**（同目录未改动的 `test_environment.py` 同样报 `Source file found twice under different module names`；HEAD 版本实测 11 errors），根因是 `tests/acceptance/` 缺 `__init__.py` 而子目录有，属既有环境/工具配置问题，不在本任务范围内。
- S-07: verified — automated command passed; run_id=702123c9bad54706bcf9ffbae1d04cd2 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=4e22cd7dd494483d9b77e8e52d1fb711 (confirmed_by: runner)

### Log
- [2026-09-27] created (draft)

---
- [2026-09-27] started
- [2026-09-27] completed (done)
## TASK-009: 数据契约承接（两表结构/索引）

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: 13-console-auth.backend.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-07, RULE-data-001
- **Files**: `tests/acceptance/console_auth_flow/test_auth_schema_contract.py`
- **Estimate**: 15–60 分钟；超出先拆分

### Description

承接 `harness-data`（由 `**/models/**` 与 `migrations/**` 路径绑定）：`control.console_account` / `control.console_session` 的标准列、软删 partial unique、`timestamptz`、同 schema 物理 FK 与索引，与 ORM 元数据一致。

### Checklist

- [ ] [B-07][integration] 以真实 PostgreSQL 表结构/索引 + 原 verifier 真实边界编写用例：断言两表的标准列齐备（`id/is_deleted/create_time/update_time`）、`uq_console_account_tenant_username` 为 `WHERE is_deleted = false` 的 partial unique、`uq_console_session_token_hash` 同理、时间列为 `timestamptz`、`console_session.account_id` 为同 schema 物理 FK、`ix_console_session_account_expires` 存在。执行 argv：`["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_schema_contract.py","-k","b07"]`。
- [ ] [RULE-data-001][integration] 作为唯一最终负责人：标准列/软删唯一/timestamptz/同 Owner FK/jsonb 口径。verifier argv：`["uv","run","pytest","-q","tests","-k","schema_parity"]`。
- [ ] 断言 ORM 元数据与实际 DDL 一致（schema parity），列缺失或类型漂移即失败。
- [ ] 不得以「迁移文件写了」代替对真实库结构的断言（读 `information_schema`/`pg_indexes`）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-07 | integration | 真实 PostgreSQL 表结构/索引 + 原 verifier 真实边界 | 标准列/partial unique/timestamptz/同 schema FK/索引齐备 | tests/acceptance/console_auth_flow/test_auth_schema_contract.py / B-07 | `["uv","run","pytest","-q","tests/acceptance/console_auth_flow/test_auth_schema_contract.py","-k","b07"]` | planned |
| RULE-data-001 | integration | 真实 PostgreSQL + 原 verifier 真实边界 | 上述口径；原 verifier 全部通过 | 原 verifier / RULE-data-001 | `["uv","run","pytest","-q","tests","-k","schema_parity"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-27] created (draft)

---

## TASK-010: 前端 auth service 与 ApiClient 拦截器契约

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: 13-console-auth.frontend.design.md#3.5 状态与数据流, 13-console-auth.frontend.design.md#3.3 组件设计, 13-console-auth.frontend.design.md#2.4 验收条件
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-11, B-05, RULE-front-001, RULE-i18n-001
- **Files**: `apps/console-platform/frontend/src/api/`, `apps/console-platform/frontend/src/locales/`, `tests/frontend/test_console_auth_contract.py`, `e2e/tests/console-auth.spec.ts`
- **Estimate**: 半天级

### Description

前端请求层契约：`src/api/` 收口 auth 四方法与拦截器（注入 `X-Locale`/`X-Request-Id`/`X-CSRF-Token`、Envelope `code != 0` Toast、401 跳登录）；文案全走 i18n key 且两侧齐备；密码/令牌不进任何持久化。

### Checklist

- [ ] [B-05][unit] i18n 资源双语完整：登录页与本模块全部词条在 zh-CN/en-US 齐备非空。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_console_auth_contract.py","-k","b05"]`。
- [ ] [S-11][E2E] 切换到 English 后触发一个业务错误 → 页面文案与 Toast `msg` 均为 en-US；刷新后语言保持。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-11\""]`。
- [ ] [RULE-front-001][integration] 作为唯一最终负责人：API 只经 `src/api/`，组件不裸用 axios/fetch；文案只用 i18n key；引用的键已定义。verifier argv：`["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`。
- [ ] [RULE-i18n-001][integration] 作为唯一最终负责人：zh-CN/en-US 双侧覆盖；新增业务只加词条；`X-Locale`/`Accept-Language` 协商。verifier argv：`["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`。
- [ ] 契约断言：拦截器注入三头；401 仅在非 `/login` 时 `window.location.assign('/login')`；密码/令牌不出现在 localStorage/sessionStorage。
- [ ] 断言组件/展示层不出现裸 `fetch`/`axios`（仅 `src/api/`）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN、断言位置与真实组件记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-05 | unit | i18n 资源双语完整 | 双侧齐备非空、无缺失 key | tests/frontend/test_console_auth_contract.py / B-05 | `["uv","run","pytest","-q","tests/frontend/test_console_auth_contract.py","-k","b05"]` | planned |
| S-11 | E2E | Locale → ApiClient → API msg → UI | 页面与 Toast 均为 en-US；刷新后语言保持 | e2e/tests/console-auth.spec.ts / S-11 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-11\""]` | planned |
| RULE-front-001 | integration | 前端源码契约 + 仓库检查脚本 + 真实 tsc | 服务层收口/无裸请求/词条键已定义；原 verifier 全部通过 | 原 verifier / RULE-front-001 | `["bash","-lc","uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]` | planned |
| RULE-i18n-001 | integration | 双侧词条 + 原 verifier 真实边界 | 双侧覆盖；只加词条；原 verifier 全部通过 | 原 verifier / RULE-i18n-001 | `["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-27] created (draft)

---

## TASK-011: 登录页/会话引导与守卫/角色过滤/退出

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-010
- **Source**: 13-console-auth.frontend.design.md#3.2 页面与路由结构, 13-console-auth.frontend.design.md#3.3 组件设计, 13-console-auth.frontend.design.md#3.6 UI 状态, 13-console-auth.frontend.design.md#2.4 验收条件
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-09, S-10, S-12, S-13, S-14, E-11, E-12, B-04, RULE-ui-001, RULE-ui-detail-001, RULE-time-001
- **Files**: `apps/console-platform/frontend/src/pages/LoginPage.tsx`, `apps/console-platform/frontend/src/auth/AuthContext.tsx`, `apps/console-platform/frontend/src/layout/AppLayout.tsx`, `apps/console-platform/frontend/src/config/menu.ts`, `tests/frontend/test_console_auth_contract.py`, `e2e/tests/console-auth.spec.ts`
- **Estimate**: 半天级

### Description

前端安全入口的验收与补齐：登录页（必填校验、提交 loading、失败 Toast、成功进概览）、会话引导（启动 Spin → `/me` → 回原路由）、`RequireAuth`/`RequireRole` 守卫、菜单与凭据入口角色过滤、Header 用户区与退出；文案与时间展示遵循既有规范。

### Checklist

- [ ] [B-04][unit] LoginPage 表单必填校验：用户名/密码为空时不发起请求，给出字段级提示。执行 argv：`["uv","run","pytest","-q","tests/frontend/test_console_auth_contract.py","-k","b04"]`。
- [ ] [S-09][E2E] 正确用户名/密码提交 → 跳转 `/`；概览可见；Header 显示显示名与角色；请求带 `X-Locale`/`X-Request-Id`/`X-CSRF-Token`。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-09\""]`。
- [ ] [S-10][E2E] 登录后刷新页面 → 启动 Spin；`/me` 成功后回到原路由；不闪回登录页。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-10\""]`。
- [ ] [S-12][E2E] ADMIN 与 BUILDER 分别登录 → ADMIN 可见「用户」菜单并可进 `/users`；BUILDER 菜单无「用户」；凭据类入口仅 ADMIN 可见（04 模块复用 `RequireRole`，后端 403 兜底）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-12\""]`。
- [ ] [S-13][E2E] Header 下拉退出 → 返回 `/login`；再访问受保护路由仍跳登录页。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-13\""]`。
- [ ] [S-14][integration] **按 design 自注限定范围**：仅验证守卫部分——ADMIN 可进 `/users`（占位页渲染）、BUILDER 被重定向到 `/` 且不发起该页数据请求；`last_login_at` 的列表展示与 `YYYY-MM-DD HH:mm:ss` 格式随账号管理页面迭代落地（本需求内不在范围，须在证据中显式登记该边界）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-14\""]`。
- [ ] [E-11][integration] BUILDER 直接输入 `/users` → 重定向 `/`，不发起该页面数据请求。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-11\""]`。
- [ ] [E-12][integration] 非安全方法在 `muad_csrf` 缺失/过期时提交 → 后端 403 `FORBIDDEN`、Toast 提示、不误显示成功态。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-12\""]`。
- [ ] [RULE-ui-001][integration] / [RULE-ui-detail-001][integration] / [RULE-time-001][integration] 作为各自唯一最终负责人，验证骨架与固定十项菜单、详情组件复用约束（本模块无详情页，账号管理后置页面须复用公共 `DetailSideSheet`）、时间展示口径。verifier argv 分别为 shell/style + build、detail_sidesheet + typecheck、datetime + schema_parity（见契约行）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-04 | unit | LoginPage 表单校验（不发请求） | 空值给字段级提示且无请求 | tests/frontend/test_console_auth_contract.py / B-04 | `["uv","run","pytest","-q","tests/frontend/test_console_auth_contract.py","-k","b04"]` | planned |
| S-09 | E2E | Browser → Router → ApiClient → Console HTTP → PostgreSQL | 跳转 `/`；Header 显示名与角色；三个请求头齐备 | e2e/tests/console-auth.spec.ts / S-09 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-09\""]` | planned |
| S-10 | E2E | Browser → Router → `/auth/me` | 刷新后 Spin → 回原路由；不闪回登录页 | e2e/tests/console-auth.spec.ts / S-10 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-10\""]` | planned |
| S-12 | E2E | AuthContext → menu → Router → 凭据入口 | ADMIN/BUILDER 菜单与路由差异；凭据入口仅 ADMIN | e2e/tests/console-auth.spec.ts / S-12 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-12\""]` | planned |
| S-13 | E2E | UI → logout API → Router | 回 `/login`；受保护路由仍跳登录 | e2e/tests/console-auth.spec.ts / S-13 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-13\""]` | planned |
| S-14 | integration | Router → ADMIN 守卫（列表展示部分随账号页面迭代） | ADMIN 可进占位页；BUILDER 重定向且不发起数据请求 | e2e/tests/console-auth.spec.ts / S-14 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"S-14\""]` | planned |
| E-11 | integration | RequireRole → Router | 重定向 `/`；无该页数据请求 | e2e/tests/console-auth.spec.ts / E-11 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-11\""]` | planned |
| E-12 | integration | ApiClient 请求拦截 → CSRF | 403 + Toast；不显示成功态 | e2e/tests/console-auth.spec.ts / E-12 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-12\""]` | planned |
| RULE-ui-001 | integration | 前端源码契约 + 真实构建 | 骨架/固定十项菜单/主展示字段入口；原 verifier 全部通过 | 原 verifier / RULE-ui-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]` | planned |
| RULE-ui-detail-001 | integration | 公共详情组件契约 + 真实 tsc | 复用 `DetailSideSheet` 不另造；原 verifier 全部通过 | 原 verifier / RULE-ui-detail-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]` | planned |
| RULE-time-001 | integration | 时间展示口径 + 原 verifier 真实边界 | `YYYY-MM-DD HH:mm:ss`；timestamptz | 原 verifier / RULE-time-001 | `["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-27] created (draft)

---

## TASK-012: 前端 E2E 验收与收口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-005, TASK-006, TASK-007, TASK-011
- **Source**: 13-console-auth.frontend.design.md#2.4 验收条件, 13-console-auth.backend.design.md#2.5.2 功能验收场景, 13-console-auth.backend.design.md#6 需求追溯矩阵
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: E-09, E-10, E-13, RULE-test-001
- **Files**: `e2e/playwright.console-auth.config.ts`, `e2e/tests/console-auth.spec.ts`, `tests/e2e/seed_console_auth.py`, `tests/console_auth_inventory.py`
- **Estimate**: 半天级

### Description

前端异常路径的最终验收（401 跳转不循环、登录错误 Toast、`/me` 失败不空白不漏环）与**收口**：建立仓库级清单用例核对覆盖表/契约表/manifest/证据/规则归属的闭合，并承接 `RULE-test-001` 的仓库级真实 E2E verifier。

### Checklist

- [ ] [E-09][integration] 任一请求返回 401 且当前不在 `/login` → `window.location.assign('/login')`；不残留加载态、不出现未捕获异常、**不产生重定向循环**（以请求计数断言）。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-09\""]`。
- [ ] [E-10][integration] 错误密码（`INVALID_CREDENTIALS`）或锁定（`ACCOUNT_LOCKED`）→ Toast 展示本地化 `msg`、停留登录页、按钮 loading 复位。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-10\""]`。
- [ ] [E-13][integration] 启动时 `/me` 返回 401 或网络错误 → 进入 `/login`；无空白页、无重定向循环。执行 argv：`["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-13\""]`。
- [ ] [RULE-test-001][E2E] 作为唯一最终负责人，承接仓库级真实 E2E 边界（真实 HTTP/PostgreSQL/Redis/浏览器），verifier argv：`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`。
- [ ] 收口清单 `tests/console_auth_inventory.py`：覆盖表唯一负责人且除本收口任务外全终态、manifest 与覆盖表一致、owner 的 Acceptance-Refs 登记、终态场景在 owner Evidence 中登记、契约行全终态、done/verified 任务零未勾项、**12 条 required 规则各有唯一负责人与可执行命令**、E2E 命令指向真实在盘套件且成功路径无路由拦截。
- [ ] 以 mutation 验证清单断言有牙（改状态/删证据/塞 mock 均应如期失败）。
- [ ] 执行上述契约命令，填写 Acceptance Evidence 的 RED/GREEN 与真实边界记录。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-09 | integration | ApiClient 响应拦截 → Router | 401 跳登录；无循环；无未捕获异常 | e2e/tests/console-auth.spec.ts / E-09 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-09\""]` | planned |
| E-10 | integration | LoginPage → API 错误 → Toast | 本地化 msg；停留登录页；loading 复位 | e2e/tests/console-auth.spec.ts / E-10 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-10\""]` | planned |
| E-13 | integration | AuthProvider → `/me` 失败 | 进 `/login`；无空白页；无循环 | e2e/tests/console-auth.spec.ts / E-13 | `["bash","-lc","cd e2e && npx playwright test --config playwright.console-auth.config.ts -g \"E-13\""]` | planned |
| RULE-test-001 | E2E | 仓库级真实 E2E（真实 HTTP/PostgreSQL/Redis/Browser）+ 原 verifier 真实边界 | 跨 API/DB/Browser 关键流程真实 E2E；分层不降级；原 verifier 全部通过 | tests/acceptance + e2e / RULE-test-001 | `["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-09-27] created (draft)
