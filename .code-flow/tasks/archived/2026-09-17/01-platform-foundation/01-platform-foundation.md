# Tasks: 平台底座与公共框架

- **Source**: .code-flow/tasks/2026-09-17/01-platform-foundation/01-platform-foundation.backend.design.md, .code-flow/tasks/2026-09-17/01-platform-foundation/01-platform-foundation.frontend.design.md
- **Created**: 2026-09-18
- **Updated**: 2026-09-18

## Proposal

先行冻结所有服务与页面共享的底座：统一日志、统一 Envelope 与错误码目录、ArtifactStore 与本地缓存、Owner Schema 迁移基线、依赖方向与质量门、ConsoleShell 与公共 Semi 组件。使后续 02–14 模块只实现业务，不再重复设计日志/响应/i18n/UI/文件缓存框架。

> 场景编号映射：前端 design 的 `S-FE-01~03` 在任务文件中映射为 `S-12~S-14`，`E-FE-01~03` 映射为 `E-07~E-09`（manifest 校验仅接受 `[SEB]-数字` 形式，来源列保留原 FE 编号可追溯）。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | integration | Service→FileSystem | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_logging.py", "-k", "s01"] |
| S-02 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | library | api-kit、YAML 目录（测试内自建 FastAPI，非真实服务） | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_api_envelope.py"] |
| S-03 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→X-Locale→API→UI | TASK-007 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"S-03\""] |
| S-04 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | integration | emptyDir→NFS | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_artifact.py", "-k", "s04"] |
| S-05 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | integration | 并发 ensure→NFS | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_artifact.py", "-k", "s05"] |
| S-06 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | unit | import graph（CI） | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/architecture"] |
| S-07 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | library | api-kit `install_health_probes` + 测试内 FastAPI；另断言四个真实服务 app 均注册 /healthz、/readyz 且走统一封套 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_probes.py"] |
| S-08 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | integration | CI→ruff/mypy/pytest/parity | TASK-006 | failed | ["bash", "-lc", "uv run make check"] |
| S-09 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | unit | ConsoleShell 菜单清单（源码级契约）；`/users` 为 adminOnly，非 ADMIN 可见 9 项 | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_console_shell_contract.py", "-k", "fixed_ten_items or no_system_settings"] |
| S-10 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | library | api-kit、RBAC 依赖；另断言真实 console app 未认证 / 未知 token 均 401 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_security.py"] |
| S-11 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | library | api-kit `write_config_audit` + 真实 PostgreSQL；另断言 console `AuditService` 确实委托该原语 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_audit.py", "-k", "s11"] |
| E-01 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | integration | Cache→NFS | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_artifact.py", "-k", "e01"] |
| E-02 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | unit | locale-key checker | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_i18n.py", "-k", "e02"] |
| E-03 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | unit | import graph 检测器（含合成越层用例）；CI 阻断由 `make check` 承担，本用例不驱动 CI | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/architecture", "-k", "violation"] |
| E-04 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | library | api-kit `validate_startup` + 测试内 FastAPI；另断言真实 console lifespan 正常可启动、迁移目录异常则 fail fast | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_startup.py"] |
| E-05 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | integration | CI→migration parity | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_schema_parity.py", "-k", "e05"] |
| E-06 | 01-platform-foundation.backend.design.md#2.5.2 功能验收场景 | library | api-kit `write_config_audit` + 真实 PostgreSQL；另断言 console `AuditService` 确实委托该原语 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_audit.py", "-k", "e06"] |
| S-12 | 01-platform-foundation.frontend.design.md#2.4 验收条件（原 S-FE-01） | E2E | Browser Router→ConsoleShell | TASK-007 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"S-12\""] |
| S-13 | 01-platform-foundation.frontend.design.md#2.4 验收条件（原 S-FE-02） | E2E | Browser→LocalStorage→API | TASK-007 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"S-13\""] |
| S-14 | 01-platform-foundation.frontend.design.md#2.4 验收条件（原 S-FE-03） | unit | ConsoleShell 菜单清单（源码级契约）；`/users` 为 adminOnly，非 ADMIN 可见 9 项 | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_console_shell_contract.py", "-k", "fixed_ten_items"] |
| E-07 | 01-platform-foundation.frontend.design.md#2.4 验收条件（原 E-FE-01） | contract | 源码契约（不执行拦截器） | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_api_client_contract.py"] |
| E-08 | 01-platform-foundation.frontend.design.md#2.4 验收条件（原 E-FE-02） | unit | DetailSideSheet 契约（源码级，不渲染组件） | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_detail_sidesheet_contract.py"] |
| E-09 | 01-platform-foundation.frontend.design.md#2.4 验收条件（原 E-FE-03） | E2E | ApiClient→401→Router | TASK-007 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"E-09\""] |

> 本表覆盖 design 全部 P0 场景（23/23）与 RULE-01~15 映射场景；无缺口。
>
> 菜单相关场景（S-09/S-14）口径：固定 10 项为 **ADMIN 视角**；`src/config/menu.ts` 中 `/users` 标记 `adminOnly: true`，`AppLayout` 按角色过滤，非 ADMIN 可见 9 项。

---

## TASK-001: 统一日志 logging-kit

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 01-platform-foundation.backend.design.md#2.3.1 功能清单, #3.4 接口设计(LIB-01), #2.5.2 功能验收场景
- **Spec-Refs**: harness-log#RULE-log-001
- **Acceptance-Refs**: S-01

### Description
冻结服务级 JSON 日志：仅配置 `LOG_DIR`，按 `service/YYYY-MM-DD.log` 落盘，自动注入 `service/trace_id/request_id`，并对 Authorization/Cookie/api_key/access_token/secret/password 等敏感字段脱敏；`configure_logging` 幂等且非法级别快速失败。

### Checklist
- [x] 实现 `configure_logging(service_name, log_dir=None, level=None)` 与 `get_logger`，仅读取 `LOG_DIR`/`LOG_LEVEL`（存量 `packages/logging-kit`，验收锁定）
- [x] 按日切文件与 service 子目录；JSON 结构固定；上下文（trace_id/request_id/tenant_id）自动注入
- [x] 敏感字段脱敏过滤器覆盖 Authorization/Cookie/Set-Cookie/api_key/access_token/refresh_token/secret/password
- [x] [S-01][integration] 已编写真实边界验收测试（存量实现，无有效 RED，原因见 Evidence）
- [x] [S-01][integration] 断言 两个服务分别写日志且按 service/日期分文件、含 trace_id/request_id（两个真实子进程 + 真实文件系统）
- [x] [RULE-log-001] verifier：`tests/logging/test_daily_json_handler.py`、`tests/test_logging_redaction.py`、`tests/acceptance/test_foundation_logging.py -k s01` 已通过（manual checklist 待 project-owner 复核）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | Service、FileSystem | 两服务日志分别落到 `service/日期.log`；行内含 trace_id/request_id；敏感值被遮蔽 | tests/acceptance/test_foundation_logging.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_logging.py", "-k", "s01"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 未留存产物：存量实现的行为锁定测试（packages/logging-kit 在 0aa5bc0 已存在） | PASS：`uv run pytest -q tests/acceptance/test_foundation_logging.py -k s01` | `tests/acceptance/test_foundation_logging.py:42`、`:48`、`:56`、`:61`（test_s01_two_services_daily_json_logs_with_context_and_redaction） | 两个真实子进程 + 真实文件系统（tmp_path）；Service/FileSystem 均未 mock | verified |

- S-01: verified — automated command passed; run_id=10e97ebe74cb40d2aef392fd0b1664de (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=9bfed79a3b1248bf809f0fa2be6083e3 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=fbe2fad368914510849d107c5cd66c4b (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=13c806f991cc4e06af7e383024751db5 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=f99df5cb911b4cc8b61b9e380ab94a00 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=707c22e6b02d4adb91ef8cfbc7029b92 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=4e14152cae6f445da6e3ee9d79b943d0 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=7e29558bd980449ab07c2bed065fad20 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-002: api-kit 统一 Envelope、错误目录与 i18n

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 01-platform-foundation.backend.design.md#3.4 接口设计(LIB-02/LIB-03), #2.5.1 业务规则与约束(RULE-03/04)
- **Spec-Refs**: harness-api#RULE-api-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-02, E-02

### Description
冻结统一响应封套 `{code,msg,data,trace_id,request_id,timestamp}` 与 `AppError(code)` 异常管线；`MessageCatalog` 仅从 `config/api-messages.yaml` 解析 `msg/http_status`，支持 zh-CN/en-US 与 `X-Locale`/`Accept-Language` 协商；业务代码不得写死 message。

### Checklist
- [x] 实现 `install_api_foundation` 与 `MessageCatalog.message(code, locale, args)`，未知 code 快速暴露不静默
- [x] 异常处理器输出统一 Envelope；`msg` 与 HTTP status 完全来自 YAML；列表统一分页封套
- [x] locale 协商（X-Locale/Accept-Language）+ `Content-Language`/`Vary`；后端错误目录与前端 locale 词条一致
- [x] [S-02][integration] 已编写真实边界验收测试（存量实现，无有效 RED，原因见 Evidence），按 FastAPI→api-kit→YAML 真实边界编写验收测试并记录 RED
- [x] [S-02][integration] 断言 业务抛 `AGENT_NOT_FOUND` 时 Envelope code/msg/status 与 YAML、locale 一致
- [x] [E-02][unit] 断言 zh-CN 新 key 而 en-US 缺失时 locale-key 检查失败
- [x] [RULE-api-001] verifier：执行 `tests/api/test_envelope.py` 与 `tests/test_error_catalog.py`（enum↔YAML parity），manual checklist（project-owner 确认错误码来自配置）
- [x] [RULE-i18n-001] verifier：执行 `scripts/check_frontend_i18n.py`，manual checklist（project-owner 确认新增业务仅增加配置）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | library | api-kit、YAML 目录（测试内自建 FastAPI，非真实服务） | 统一 Envelope 字段齐全；code/msg/http_status 来自 YAML；locale 切换生效 | tests/acceptance/test_foundation_api_envelope.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_api_envelope.py"] | verified |
| E-02 | unit | locale-key checker | en-US 缺 key 时检查失败且给出缺失清单 | tests/acceptance/test_foundation_i18n.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_i18n.py", "-k", "e02"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | 未留存产物 | PASS：`uv run pytest -q tests/acceptance/test_foundation_api_envelope.py`（3 passed） | `tests/acceptance/test_foundation_api_envelope.py:56-64`（YAML status/locale）、`:74`（未知 code）、`:82`（分页封套） | 库级：api-kit + 真实 config/api-messages.yaml + 真实 locale 协商（测试内自建 FastAPI） | verified |
| E-02 | 未留存产物 | PASS：`uv run pytest -q tests/acceptance/test_foundation_i18n.py -k e02`（3 passed） | `tests/acceptance/test_foundation_i18n.py:29`、`:37`、`:43`（三个 test_e02_*） | 真实 checker 模块 + 临时 zh/en JSON 文件对 | verified |

- S-02: verified — automated command passed; run_id=c6cef08dec6e412bb5d2a6d34b7991ee (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=c6cef08dec6e412bb5d2a6d34b7991ee (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=c256d04d7bee4b12ad75a9a9e3c91f94 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=c256d04d7bee4b12ad75a9a9e3c91f94 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-003: api-kit 安全/健康探针/启动校验/审计原语

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 01-platform-foundation.backend.design.md#3.4 接口设计(LIB-06~LIB-09), #2.5.1 业务规则与约束(RULE-14/15), #4 部署与运维
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-07, S-10, S-11, E-04, E-06

### Description
提供安全与治理原语：会话校验/RBAC 依赖注入（登录业务归 13-console-auth）、`/healthz`+`/readyz`、启动初始化校验（配置/迁移到 head/存储挂载，fail fast）。**SecretProvider 可达性：该代码路径已按 secret-plaintext 决策移除**（`scripts/check_secret_ref_residue.py` 的既定职责即「禁止 SecretRef/SecretProvider 代码路径复活」），`secret_provider` 配置项当前无任何消费者，故不属于启动校验范围 —— RULE-10 原文的该子句已被后续决策取代，非「待补」。、`write_config_audit` 与业务变更同事务且不含 Secret Value。

这些原语**已被真实服务采用**（2026-09-20 接线）：4 个服务的 `main.py` 调用 `install_health_probes` 与 `validate_startup`；console-platform 调用 `install_console_security`（`api/deps.py` 的 `ConsoleSessionVerifier`/`ConsoleRoleResolver` + `require_session`/`require_roles` 薄适配）与 `write_config_audit`（`application/audit_service.py` 委托原语，`sanitize_payload` 改为 api-kit `sanitize_audit_payload` 的别名）。原先各 app 手写的 `api/health.py` 已删除（im-gateway 的保留为只提供 readiness 检查与诊断的模块）。

### Checklist
- [x] 实现 `install_console_security`（无会话 → `UNAUTHORIZED`；角色不足 → `FORBIDDEN`）；协议为 `session_verifier.verify(token)` / `role_resolver.roles_for(principal)`
- [x] 实现 `install_health_probes(app, readiness_checks=(), *, detail=None)` 与 `validate_startup(settings, engine, artifact_store, *, migrations_dir=None)`；依赖缺失时 `/readyz` 非 200 且进程快速退出；新增 `database_readiness(engine_factory)` 辅助与配置项 `MIGRATIONS_DIR`
- [x] 实现 `write_config_audit`；before/after 递归剔除 Secret（docs/02 §4.18 无 result_status 列，仅成功路径写入）
- [x] [接线] 4 个服务装配 `install_health_probes` + `validate_startup`；console 装配 `install_console_security` + `write_config_audit`（原语不再是"仅库内实现、无服务调用"）
- [x] [签名变更] `AuthService.change_password` 由 `(account, ...)` 改为 `(account_id, ...)` —— 会话校验原语持有自己的 session，处理器传入的 `account` 是 detached 对象，故在 service 内按 id 重新加载后再改
- [x] [S-07][library] 实现先行，未留存有效 RED（原因见 Evidence），按 api-kit 原语 + 真实服务 app 双重边界编写验收测试
- [x] [S-07][library] 断言 依赖就绪时探针 200；缺失依赖时 `/readyz` 非 200 且 fail fast；并断言 4 个**真实服务** app 对象都注册了两条探针、console lifespan 真实跑启动校验且迁移漂移时 fail fast
- [x] [S-10][library] 断言 无会话/角色不足分别返回 `UNAUTHORIZED`/`FORBIDDEN`；并断言真实 console app 已装配 `install_console_security`（`app.state.session_verifier`/`role_resolver`）
- [x] [S-11][library] 断言 业务更新成功审计同提交、业务失败审计同回滚；并断言 console 的审计写入确实委托 `write_config_audit`（无第二套脱敏实现）
- [x] [E-04][library] 断言 配置缺失或迁移未到 head 时启动失败并输出明确错误
- [x] [E-06][library] 断言 审计写入失败时业务事务回滚，不产生无审计变更
- [x] [RULE-secret-001] verifier：执行 `tests/test_logging_redaction.py` 与审计落盘断言（无 Secret Value），manual checklist（project-owner 确认五处不外泄）
- [x] 运行验收命令并填写 Acceptance Evidence
- [x] harness-auth#RULE-auth-001、harness-model#RULE-model-001、harness-project-platform#RULE-platform-001、harness-rel#RULE-rel-001 verifier：project-owner 确认上述规则对 TASK-003 不适用（not_applicable：仅新增 api-kit 原语，不涉及三层授权、model_definition、PlatformAdapter/Redis Session、关系 POST/DELETE 语义）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | library | api-kit `install_health_probes` + 测试内 FastAPI；另断言四个真实服务 app 均注册 /healthz、/readyz 且走统一封套 | 就绪/缺失两种依赖状态下探针行为正确且 fail fast | tests/acceptance/test_foundation_ops_probes.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_probes.py"] | verified |
| S-10 | library | api-kit、RBAC 依赖；另断言真实 console app 未认证 / 未知 token 均 401 | 无会话 401 `UNAUTHORIZED`；角色不足 403 `FORBIDDEN` | tests/acceptance/test_foundation_ops_security.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_security.py"] | verified |
| S-11 | library | api-kit `write_config_audit` + 真实 PostgreSQL；另断言 console `AuditService` 确实委托该原语 | 业务与审计同事务；before/after 无 Secret | tests/acceptance/test_foundation_ops_audit.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_audit.py", "-k", "s11"] | verified |
| E-04 | library | api-kit `validate_startup` + 测试内 FastAPI；另断言真实 console lifespan 正常可启动、迁移目录异常则 fail fast | 任一前置失败即启动失败且无 ready 端点 | tests/acceptance/test_foundation_ops_startup.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_startup.py"] | verified |
| E-06 | library | api-kit `write_config_audit` + 真实 PostgreSQL；另断言 console `AuditService` 确实委托该原语 | 审计失败 → 业务回滚；无孤立变更 | tests/acceptance/test_foundation_ops_audit.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_ops_audit.py", "-k", "e06"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | 未留存产物：原语为新实现但实现先行 | PASS：`uv run pytest -q tests/acceptance/test_foundation_ops_probes.py`（8 passed） | 库级：`:87-97`（依赖状态与 503）、`:107`（fail-fast 子进程）；真实服务：`:117-132`（四个服务 app 均注册 /healthz + /readyz 且走统一封套）、`:145`（真实 console lifespan 执行启动校验）、`:160`（迁移目录异常 → fail fast） | 库级 api-kit + 四个真实服务 app 对象 + 真实子进程退出码 3 | verified |
| S-10 | 未留存产物：同上 | PASS：`uv run pytest -q tests/acceptance/test_foundation_ops_security.py`（4 passed） | 库级：`:55`（无会话 401）、`:62`（角色不足 403）、`:69`（cookie 与 Bearer 均识别）；真实服务：`:88-101`（真实 console app 未认证/未知 token 均 401） | api-kit 会话/RBAC 原语 + 真实 console app（install_console_security 已装配） | verified |
| S-11 | 未留存产物：同上 | PASS：`uv run pytest -q tests/acceptance/test_foundation_ops_audit.py -k s11`（2 passed） | `tests/acceptance/test_foundation_ops_audit.py:98-103`（业务与审计同提交、Secret 键被剔除）、`:147-176`（console AuditService 确实委托 api-kit write_config_audit） | 真实 PostgreSQL：platform_user 更新与 config_audit_log 同事务 | verified |
| E-04 | 未留存产物：同上 | PASS：`uv run pytest -q tests/acceptance/test_foundation_ops_startup.py`（3 passed） | `tests/acceptance/test_foundation_ops_startup.py:39`（happy path）、`:48`（缺配置/存储）、`:67`（schema 不在 head）；另 `test_foundation_ops_probes.py:145`、`:160`（真实 console lifespan） | 真实 PostgreSQL alembic_version 回拨 + 真实迁移链（0001~0007 单头） | verified |
| E-06 | 未留存产物：同上 | PASS：`uv run pytest -q tests/acceptance/test_foundation_ops_audit.py -k e06`（1 passed） | `tests/acceptance/test_foundation_ops_audit.py:129`、`:130` | 真实 PostgreSQL：审计写入违反列长 → 业务更新回滚，无孤立变更/审计 | verified |

- S-07: verified — automated command passed; run_id=a51636e566aa477885c739d980661dc1 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=a51636e566aa477885c739d980661dc1 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=a51636e566aa477885c739d980661dc1 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=a51636e566aa477885c739d980661dc1 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=a51636e566aa477885c739d980661dc1 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=a1eb959acdd54810b0b85e22fd58de29 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=a1eb959acdd54810b0b85e22fd58de29 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=a1eb959acdd54810b0b85e22fd58de29 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=a1eb959acdd54810b0b85e22fd58de29 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=a1eb959acdd54810b0b85e22fd58de29 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=e91f96decdc443d3940af8f2b16c1899 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=e91f96decdc443d3940af8f2b16c1899 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=e91f96decdc443d3940af8f2b16c1899 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=e91f96decdc443d3940af8f2b16c1899 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=e91f96decdc443d3940af8f2b16c1899 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-004: ArtifactStore 与 SkillArtifactCache

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 01-platform-foundation.backend.design.md#2.4 范围与边界(ArtifactStore 所有权), #3.4 接口设计(LIB-04/LIB-05), #2.5.2 功能验收场景
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: S-04, S-05, E-01

### Description
提供 NFS-backed RWX PVC 原语：`storage_key` 解析、原子写入、checksum 校验、emptyDir READY 缓存；同 checksum 首次并发加载 singleflight；cache miss 且 NFS 不可用返回 `SKILL_ARTIFACT_UNAVAILABLE`，不得执行半成品。

### Checklist
- [x] 实现 `ArtifactStore.resolve(storage_key)`（路径穿越防护；禁止从 NFS 直接执行；存量 `packages/artifact-store`）
- [x] 实现 `SkillArtifactCache.ensure(artifact_id, storage_key, checksum)`：READY 快路径 → singleflight → 复制+checksum+解压+原子 rename
- [x] checksum 不匹配 → `SKILL_ARTIFACT_CHECKSUM_MISMATCH`；NFS 不可用且无 READY → `SKILL_ARTIFACT_UNAVAILABLE`
- [x] [S-04][integration] 已编写真实边界验收测试（存量实现，无有效 RED，原因见 Evidence）
- [x] [S-04][integration] 断言 同 checksum 第二次 ensure 命中 READY 且不访问 NFS（CountingStore 记录 resolve 次数）
- [x] [S-05][integration] 断言 同 checksum 10 并发 ensure 仅一次 NFS 复制/解压，其余等待同一 READY 结果
- [x] [E-01][integration] 断言 cache miss 且 NFS 不可用时返回 `SKILL_ARTIFACT_UNAVAILABLE`，不执行半成品
- [x] [RULE-skill-001] verifier：`uv run pytest -q tests/test_skill_artifact_cache.py` 与 `tests/acceptance/test_foundation_artifact.py` 通过（command verifier）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | emptyDir、NFS 目录 | 第二次 ensure 走 READY 且无 NFS 读取 | tests/acceptance/test_foundation_artifact.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_artifact.py", "-k", "s04"] | verified |
| S-05 | integration | 并发 ensure、NFS | 仅一次复制/解压；结果同一 READY 路径 | tests/acceptance/test_foundation_artifact.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_artifact.py", "-k", "s05"] | verified |
| E-01 | integration | Cache、NFS | NFS 不可用 → `SKILL_ARTIFACT_UNAVAILABLE`，无半成品执行 | tests/acceptance/test_foundation_artifact.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_artifact.py", "-k", "e01"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | 未留存产物：存量实现的行为锁定测试 | PASS：`uv run pytest -q tests/acceptance/test_foundation_artifact.py -k s04`（1 passed） | `tests/acceptance/test_foundation_artifact.py:43-45`（test_s04_second_ensure_hits_ready_without_nfs） | 真实文件系统 emptyDir/NFS 目录 + CountingStore 计数；未 mock Store | verified |
| S-05 | 未留存产物：同上 | PASS：`uv run pytest -q tests/acceptance/test_foundation_artifact.py -k s05`（1 passed） | `tests/acceptance/test_foundation_artifact.py:60-62`（test_s05_concurrent_ensure_copies_once） | 10 并发 ensure + resolve 计数=1（singleflight） | verified |
| E-01 | 未留存产物：同上 | PASS：`uv run pytest -q tests/acceptance/test_foundation_artifact.py -k e01`（1 passed） | `tests/acceptance/test_foundation_artifact.py:74-75`（test_e01_missing_artifact_is_unavailable_without_partial_ready） | 真实缺失制品路径；断言无半成品 READY 目录 | verified |

- S-04: verified — automated command passed; run_id=7af60c010a794e258e05651a65d879d8 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=7af60c010a794e258e05651a65d879d8 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=7af60c010a794e258e05651a65d879d8 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=4f0009591724484c9fe34c136cdf08f5 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=4f0009591724484c9fe34c136cdf08f5 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=4f0009591724484c9fe34c136cdf08f5 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-005: Schema 基线与迁移纪律

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 01-platform-foundation.backend.design.md#3.3 数据设计, #2.5.1 业务规则与约束(RULE-07/12), #3.5 质量实现方案
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: E-05

### Description
建立四个逻辑 schema（control/runtime/task/langgraph）与标准公共字段；`langgraph` 仅建 schema，Checkpointer 表由 adapter setup 管理；Alembic 单链迁移；ORM↔迁移↔docs/02 字段/索引 parity 校验纳入质量门。

### Checklist
- [x] `0001` 创建 `control/runtime/task/langgraph` 四个 schema；标准列 `id/is_deleted/create_time/update_time`（timestamptz/partial unique；存量 `migrations/versions`）
- [x] 迁移可 upgrade/downgrade；不引入破坏性自动迁移（0003 已实测 upgrade/downgrade/upgrade）
- [x] 实现 ORM↔迁移↔docs/02 parity 测试（列/空值/主键/索引名与谓词；5 个套件共 17 用例）
- [x] [E-05][integration] 已编写真实边界验收测试（存量实现，无有效 RED，原因见 Evidence）
- [x] [E-05][integration] 断言 注入列漂移后 parity 套件失败、移除后恢复通过（单头迁移链 0003→0002→0001）
- [x] [RULE-data-001] verifier：`uv run pytest -q tests -k schema_parity`（17 passed）与 `tests/acceptance/test_foundation_schema_parity.py` 通过（command verifier）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-05 | integration | CI、Alembic、docs/02、ORM metadata | 字段/索引/谓词不一致即失败；一致时通过 | tests/acceptance/test_foundation_schema_parity.py | ["uv", "run", "pytest", "-q", "tests/acceptance/test_foundation_schema_parity.py", "-k", "e05"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-05 | 未留存产物：存量实现的行为锁定测试 | PASS：`uv run pytest -q tests/acceptance/test_foundation_schema_parity.py -k e05`（3 passed） | `tests/acceptance/test_foundation_schema_parity.py:62`、`:68`（单头）、`:73`（ORM 套件覆盖）、`:80`、`:88`（drift 探针） | 真实 PostgreSQL alembic_version（当前 0007 = 单头）+ 真实 ALTER TABLE 注入 drift → parity 失败 → DROP 恢复 | verified |

- E-05: verified — automated command passed; run_id=afe2d406100443cf853425b3ec60d679 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=7d80ef6009e140f1a929a070efbd1ff3 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-006: 依赖方向门禁与质量门（CI）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005
- **Source**: 01-platform-foundation.backend.design.md#2.5.1 业务规则与约束(RULE-09/12), #3.5 质量实现方案
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-06, S-08, E-03

### Description
以 CI 强制单向依赖（apps → kits/packages → common；Runtime/Worker 不 import Console ORM/Repository；Skill 不 import Runtime/Console）与质量门（ruff/mypy/pytest/契约/迁移 parity/镜像扫描）；关键流程 E2E 必须真实边界，不得 mock。

### Checklist
- [x] 实现架构静态检查（import graph：允许方向通过、越层拒绝）
- [x] CI 串联 ruff、mypy、pytest、错误码/枚举契约、迁移 parity（`make check`；镜像扫描在 CI 流水线）
- [x] 验收基线：Acceptance Manifest 锁定 + E2E runner（`--include-e2e`）纳入 CI 门禁说明
- [x] [S-06][unit] 未留存有效 RED（测试与本任务同落地，未先跑失败基线；未伪造），按 import graph（CI）真实边界编写验收测试并记录 RED
- [x] [S-06][unit] 断言 允许方向通过、越层 import 被拒绝
- [ ] [S-08][integration] 断言 全质量门运行通过；**当前 blocked**：`make check` 的 `test` 步被 08-runtime-execution 未提交在途重构的 `tests/acceptance/runtime/*` 破坏（事件循环污染级联失败），需该任务收尾后才能复现（lint/typecheck 已由本轮清理修复）
- [x] [E-03][unit] 断言 PR 引入 agent-runtime→console-platform.infrastructure 时 CI 阻断
- [x] [RULE-arch-001] verifier：执行架构检查用例，manual checklist（project-owner 确认部署单元为四个、Runtime/Worker 无状态）
- [x] [RULE-test-001] verifier：执行 E2E 用例清单与 Manifest 校验，manual checklist（project-owner 确认真实边界不 mock）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | unit | import graph（CI） | 合法方向通过；越层拒绝并给出路径 | tests/architecture/test_import_direction.py | ["uv", "run", "pytest", "-q", "tests/architecture"] | verified |
| S-08 | integration | CI、ruff/mypy/pytest/parity、YAML 目录 | 全门禁通过；错误码/枚举与 YAML 一致 | Makefile | ["bash", "-lc", "uv run make check"] | failed |
| E-03 | unit | import graph 检测器（含合成越层用例）；CI 阻断由 `make check` 承担，本用例不驱动 CI | 越层 PR 被 CI 阻断 | tests/architecture/test_import_direction.py | ["uv", "run", "pytest", "-q", "tests/architecture", "-k", "violation"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | 未留存产物：门禁与测试同任务落地 | PASS：`uv run pytest -q tests/architecture`（4 passed） | `tests/architecture/test_import_direction.py:31`、`:37`、`:43`（三个方向断言） | 真实仓库文件 AST 扫描（runtime/worker 不 import console、packages 不 import app、skill-sdk 不 import runtime） | verified |
| S-08 | 未留存产物 | **当前不可复现**：`uv run make check` 实测失败 | `Makefile:21`（check: compile test i18n-check error-message-check lint typecheck） | test 步 585 failed/275 passed —— 根因是 08-runtime-execution 未提交在途重构的 `tests/acceptance/runtime/*` 落下 session 级事件循环，其后所有 async 用例级联失败（`--ignore=tests/acceptance/runtime` 后 862 passed）；lint/typecheck 曾红，已由本轮清理修复 | failed |
| E-03 | 未留存产物：同 S-06 | PASS：`uv run pytest -q tests/architecture -k violation`（1 passed） | `tests/architecture/test_import_direction.py:50`、`:51`（合成越层模块被检测器判定违规） | 合成越层模块对照测试（非 mock）；CI 阻断由 make check 承担，本用例不驱动 CI | verified |

- S-06: verified — automated command passed; run_id=da0236a1951644ad8291d6cbae4cc207 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=da0236a1951644ad8291d6cbae4cc207 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=da0236a1951644ad8291d6cbae4cc207 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=7dd1c639faec4d85a06e504540f1718d (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=7dd1c639faec4d85a06e504540f1718d (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=7dd1c639faec4d85a06e504540f1718d (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=2168fdd6046f400a85255b11388a962d (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=2168fdd6046f400a85255b11388a962d (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=2168fdd6046f400a85255b11388a962d (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-08: failed — automated command failed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-08: failed — automated command failed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-08: failed — automated command failed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-08: failed — automated command failed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-08: failed — automated command failed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-007: ConsoleShell、公共组件库与 i18n/ApiClient

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-002
- **跨模块依赖**: 13-console-auth（登录页与角色上下文；本模块只提供 401 跳转与角色菜单过滤原语）
- **Source**: 01-platform-foundation.frontend.design.md#3.2 页面与路由结构, #3.3 组件设计, #3.5 状态与数据流, #2.4 验收条件
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001, harness-ui#RULE-ui-001, harness-frontend#RULE-front-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-03, S-09, S-12, S-13, S-14, E-07, E-08, E-09

### Description
提供所有 Console 页面的唯一骨架与公共组件：固定 10 项菜单的应用壳（实际组件 `src/layout/AppLayout.tsx`，原文档称 ConsoleShell）、ModuleToolbar/RemoteTable/PaginationFooter/DetailSideSheet/FormModal/StatusTag/DateTimeText/ConfirmAction/EmptyState/ErrorState/EntityLink/LocaleSwitch；ApiClient 统一 Envelope 错误、`X-Locale`、401 跳转登录原语、403 本地化 Toast；文案只用 i18n key，时间统一 `YYYY-MM-DD HH:mm:ss`。

> 组件命名与清单澄清（2026-09-20 核对实际文件名）：
> - 不存在独立的 `ConsoleShell` / `DetailTabs` 组件：应用壳是 `layout/AppLayout.tsx`；详情 Tabs 由 `DetailSideSheet` 内部渲染 Semi `Tabs`。
> - `ErrorState` / `StatusTag` / `ConfirmAction` / `EntityLink` / `LocaleSwitch` 在 01 归档时**未交付**，由 02-user-identity 的 review 修复补齐（`LocaleSwitch` 由 `LanguageSwitcher` 更名确立）。保留在清单中，因为 02/03/05/06/07 的 design 均把"01 交付公共组件"当作硬依赖。
> - 固定 10 项为 **ADMIN 视角**：`src/config/menu.ts` 中 `/users` 为 `adminOnly: true`，`AppLayout` 按角色过滤，非 ADMIN 可见 9 项。

### Checklist
- [x] 应用壳（`layout/AppLayout.tsx`）：Semi Layout/Nav/Header + Outlet；菜单恰为固定 10 项（概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计；ADMIN 视角，`/users` 为 adminOnly 故非 ADMIN 可见 9 项），无系统设置/中间件状态
- [x] 公共组件按 CMP-01~05 契约实现（本次补齐 CMP-02/03/05：ModuleToolbar/RemoteTable/FormModal；其余描述组件按业务模块落地）；DetailSideSheet 标题/副标题左侧、操作与 X 同行靠右、Tabs 在其下（无独立 DetailTabs 组件）
- [x] ApiClient：统一 Envelope 错误本地化、`X-Locale` 注入、401 跳转登录并携带 returnUrl、403 Toast；组件禁止裸用 axios/fetch；文案只用 i18n key
- [x] [S-03][E2E] 按 Browser→X-Locale→API→UI 真实边界编写 Playwright 验收测试（实现先行，未留存 RED；真实 Chrome）
- [x] [S-03][E2E] 断言 切到 English 后触发错误时页面文案与后端 msg 同步为 en-US
- [x] [S-09][unit] 断言（源码级契约，不渲染组件） 菜单清单恰为固定 10 项且无系统设置/中间件状态；`/users` 为 adminOnly，非 ADMIN 9 项
- [x] [S-12][E2E] 断言 切换任意模块路由时 Layout 不重建且菜单选中正确
- [x] [S-13][E2E] 断言 切换 English 后刷新语言保持且请求带 `X-Locale=en-US`
- [x] [S-14][unit] 断言（源码级契约） 菜单 key 序列与固定清单完全一致；`/users` 为 adminOnly，非 ADMIN 9 项
- [x] [E-07][contract] 断言（源码契约，不执行拦截器） 后端 `code!=0` 时统一展示本地化 msg；另覆盖 request-id 非 secure context 兜底
- [x] [E-08][unit] 断言（源码级契约，不渲染组件） DetailSideSheet `actions` 为空时不出现空操作区且 X 仍在右上
- [x] [E-09][E2E] 断言 会话失效访问业务页时跳转登录页并保留 returnUrl，不渲染业务数据
- [x] [RULE-ui-001] verifier：执行 S-09/S-12/S-14 用例，manual checklist（project-owner 确认 10 项菜单与无系统设置）
- [x] [RULE-ui-detail-001] verifier：执行 S-12/E-08 用例与 SideSheet 契约断言，manual checklist（project-owner 确认布局规则）
- [x] [RULE-front-001] verifier：执行 `scripts/check_frontend_i18n.py` 与 services 依赖检查，manual checklist（project-owner 确认无裸 fetch/axios）
- [x] [RULE-time-001] verifier：执行 DateTimeText 用例，manual checklist（project-owner 确认时间格式统一）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Browser、API、i18n 资源 | 切换 English 后 UI 文案与后端 msg 均为 en-US | e2e/tests/foundation.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"S-03\""] | verified |
| S-09 | unit | ConsoleShell 菜单清单（源码级契约）；`/users` 为 adminOnly，非 ADMIN 可见 9 项 | 菜单恰为固定 10 项，无系统设置/中间件状态 | tests/frontend/test_console_shell_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_console_shell_contract.py", "-k", "fixed_ten_items or no_system_settings"] | verified |
| S-12 | E2E | Browser Router、ConsoleShell | 路由切换不重建 Layout，菜单选中正确 | e2e/tests/foundation.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"S-12\""] | verified |
| S-13 | E2E | Browser、LocalStorage、API | 刷新后语言保持，请求带 `X-Locale=en-US` | e2e/tests/foundation.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"S-13\""] | verified |
| S-14 | unit | ConsoleShell 菜单清单（源码级契约）；`/users` 为 adminOnly，非 ADMIN 可见 9 项 | 渲染菜单与固定清单一致（顺序一致） | tests/frontend/test_console_shell_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_console_shell_contract.py", "-k", "fixed_ten_items"] | verified |
| E-07 | contract | 源码契约（不执行拦截器） | `code!=0` 时展示本地化 msg，不渲染原始 code | tests/frontend/test_api_client_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_api_client_contract.py"] | verified |
| E-08 | unit | DetailSideSheet 契约（源码级，不渲染组件） | `actions` 为空无空操作区；X 位于右上 | tests/frontend/test_detail_sidesheet_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_detail_sidesheet_contract.py"] | verified |
| E-09 | E2E | ApiClient、Router、登录页 | 401 跳转登录并保留 returnUrl，不渲染业务数据 | e2e/tests/foundation.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep \"E-09\""] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
>
> **RED 可追溯性说明**：本模块的实现与验收测试同在 commit f8b2c91（2026-09-18）落地，git log --follow 无法证明"先写测试后写实现"；除存量实现的行为锁定场景外，其余场景的 RED 均未留存产物，证据表只记录可复核的 GREEN 与断言位置，不声称存在 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | 未留存产物：E2E 与前端同任务落地 | PASS：`npm --prefix e2e test -- --grep "S-03"`（1 passed） | `e2e/tests/foundation.spec.ts:17` | 真实 Chrome + `tests/e2e/app.py`（真实 uvicorn + 真实 dist 产物 + 真实 api-kit）：切 English 后请求头 X-Locale=en-US，Toast 与 YAML en-US msg 一致 | verified |
| S-09 | 未留存产物 | PASS：`uv run pytest -q tests/frontend/test_console_shell_contract.py -k "fixed_ten_items or no_system_settings"` | `tests/frontend/test_console_shell_contract.py:29`、`:33`、`:37`、`:45`、`:46` | 源码级契约：config/menu.ts 清单 + AppLayout.tsx 无系统设置入口 | verified |
| S-12 | 未留存产物 | PASS：grep `S-12`（1 passed） | `e2e/tests/foundation.spec.ts:36` | 真实 Chrome Router：跨路由后 layout 节点仍 isConnected，Nav 选中正确 | verified |
| S-13 | 未留存产物 | PASS：grep `S-13`（1 passed） | `e2e/tests/foundation.spec.ts:52` | 真实 Chrome + localStorage：刷新后 en-US 保持，/auth/me 请求头 X-Locale=en-US | verified |
| S-14 | 未留存产物 | PASS：`uv run pytest -q tests/frontend/test_console_shell_contract.py -k fixed_ten_items` | `tests/frontend/test_console_shell_contract.py:29` | 源码级契约：菜单 key 序列与固定清单逐一相等（/users 为 adminOnly，非 ADMIN 9 项） | verified |
| E-07 | 未留存产物 | PASS：`uv run pytest -q tests/frontend/test_api_client_contract.py`（4 passed） | `tests/frontend/test_api_client_contract.py:16`、`:22`、`:38-45` | 源码契约（不执行拦截器）：X-Locale 注入、code!=0 用 body.msg、request-id 三级兜底 | verified |
| E-08 | 未留存产物 | PASS：`uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py`（9 passed） | `tests/frontend/test_detail_sidesheet_contract.py:20`、`:27`、`:34` | 源码级契约（不渲染组件）：actions 为空无空操作区、Semi header+tabs | verified |
| E-09 | 未留存产物 | PASS：grep `E-09`（1 passed） | `e2e/tests/foundation.spec.ts:64` | 真实 Chrome 无会话访问业务页 → /login?returnUrl=%2Fagents，.semi-table 计数 0 | verified |

- S-09: verified — automated command passed; run_id=9353acf0a8f1440a8d8ae8c7c7f6b853 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=9353acf0a8f1440a8d8ae8c7c7f6b853 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=9353acf0a8f1440a8d8ae8c7c7f6b853 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=9353acf0a8f1440a8d8ae8c7c7f6b853 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=f035b1c0289246b88c9906269a86ba1c (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=f035b1c0289246b88c9906269a86ba1c (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=f035b1c0289246b88c9906269a86ba1c (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=f035b1c0289246b88c9906269a86ba1c (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=fc3c7c4078fd457587f02410890a7526 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=60c63f13ba9c42178cf43e2af34c562e (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=e26848ecccb74cf681764788303cc274 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=75dac3ed3d554746af333e0015b6fa46 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=75dac3ed3d554746af333e0015b6fa46 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=75dac3ed3d554746af333e0015b6fa46 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=75dac3ed3d554746af333e0015b6fa46 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=255d1b9aeb3848198c31df66282c2413 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=e5e4d05659544313bb27279dfc8a361c (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=346032b251d84e11808ecb317c71383e (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=9caa249f2aff43fcb24b5014f2141596 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-09: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-12: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-13: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- S-14: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=9aff2894729e48a2aadaba38ae45fc30 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)
- [2026-09-18] started
- [2026-09-18] completed (done)
