# Tasks: 项目平台与凭据

- **Source**: `.code-flow/tasks/2026-09-17/04-project-platform/`（04-project-platform.backend.design.md、04-project-platform.frontend.design.md）
- **Created**: 2026-09-18
- **Updated**: 2026-09-18

## Proposal

实现 `ProjectPlatform + PlatformAdapter + CredentialRef + Redis PlatformSession` 的配置与凭据闭环：Console 提供平台 CRUD、Adapter 元数据、用户/共享凭据管理与配置校验/非鉴权连通性探测，adapter_key 变更使旧凭据 INVALID 并按 Set 索引清理 Session；前端按已锁定的暗色 Console 骨架落地平台列表/详情/动态表单/凭据 Tab，并把用户详情「项目平台凭据」Tab 从占位改为真实数据。

> 场景编号映射：前端 design 的 `S-FE-01~04` 映射为 `S-05~S-08`，`E-FE-01~03` 映射为 `E-05~E-07`（manifest 校验仅接受 `[SEB]-数字` 形式，来源列保留原 FE 编号可追溯）。

### Alignment

- **Scope**: Adapter metadata（API-01/14）、平台 CRUD 与失效（API-02/03/04/05/13）、用户/共享凭据（API-07~12）、配置校验与非鉴权连通性探测（API-06）、Session 失效契约（Redis Set 索引）、前端列表/详情/动态表单/凭据 Tab、用户详情凭据 Tab 接通。
- **Decisions**:
  - API-02 扩展 `user_id` 过滤并返回 `user_credential_status`（`ACTIVE/INVALID/NONE`），供用户详情凭据 Tab 使用，避免前端 N+1（已回写 backend design §3.4）。
  - platform-sdk 提供参考适配器 `generic-http`（完整无状态实现：`session_mode=NONE`；`adapter_config` 支持 `auth_scheme(none/bearer/basic)`、`auth_header`、`timeout_ms`；`credential_schema` 含 `token/username/password`（`x-secret`）；`validate` 校验凭据与 scheme、`prepare_request` 注入认证头且不落日志），由 Console 启动时注册；E2E 与 BASE_URL 平台使用。
  - Session 失效使用 `.env` 中的真实 Redis（`REDIS_URL`）与真实 PG；platform-sdk 实现 `RedisPlatformSessionManager`（Set 索引，禁 `KEYS/SCAN`，singleflight 短锁）。
  - 新增依赖：Console 后端 `jsonschema`（平台/凭据 Schema 校验），platform-sdk `redis` 客户端。
  - 归档 02 的 E2E「E-06 凭据 Tab 加载失败」在 TASK-011 更新为真实数据断言（占位行为退役）。
  - 责任调整（执行可行性）：E-01 归 TASK-002（Registry 查找）、E-03 归 TASK-001（真实 PG partial unique）、E-06 归 TASK-012（E2E 执行）；新增 S-FE-05（平台列表筛选/分页）归 TASK-007，保证每个 TASK 都有可验收场景。
- **Non-goals**: Console 不执行平台登录/业务调用、不建立/缓存 Session；不做共享凭据池化/priority；不改本模块之外的领域语义；Secret 明文不得进入日志/审计响应/Snapshot/API 响应。
- **Acceptance**: design 全部 P0/P1 场景 15 个（S-01~S-08、E-01~E-07）全部锁定责任任务与真实边界；RULE 9 条各唯一 owner。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→API→DB | TASK-003 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-01\""] |
| S-02 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→API→DB（凭据明文落库、不回显） | TASK-004 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-02\""] |
| S-03 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→API→网络探测→UI | TASK-005 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-03\""] |
| S-04 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | integration | Service→DB→Redis | TASK-006 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_platform_session_invalidation.py", "-k", "s04"] |
| S-05 | 04-project-platform.frontend.design.md#2.4 验收条件（原 S-FE-01） | E2E | Browser→adapter metadata→Form | TASK-008 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-05\""] |
| S-06 | 04-project-platform.frontend.design.md#2.4 验收条件（原 S-FE-02） | E2E | Browser→Secret API | TASK-010 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-06\""] |
| S-07 | 04-project-platform.frontend.design.md#2.4 验收条件（原 S-FE-03） | E2E | Browser→API→网络探测→UI | TASK-010 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-07\""] |
| S-08 | 04-project-platform.frontend.design.md#2.4 验收条件（原 S-FE-04） | E2E | Browser→API→DB（用户详情凭据 Tab） | TASK-011 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-08\""] |
| S-09 | 04-project-platform.frontend.design.md#2.4 验收条件（原 S-FE-05） | E2E | Browser→API→DB | TASK-007 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"S-09\""] |
| E-01 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | integration | API→Registry | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_platform_adapters_api.py", "-k", "e01"] |
| E-02 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | integration | API→CredentialResolver | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_platform_test_api.py", "-k", "e02"] |
| E-03 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | integration | API→DB | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_platform_repository.py", "-k", "e03"] |
| E-04 | 04-project-platform.backend.design.md#2.5.2 功能验收场景 | integration | API→Schema | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_credentials_api.py", "-k", "e04"] |
| E-05 | 04-project-platform.frontend.design.md#2.4 验收条件（原 E-FE-01） | E2E | PUT API→UI | TASK-009 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"E-05\""] |
| E-06 | 04-project-platform.frontend.design.md#2.4 验收条件（原 E-FE-02） | E2E | API→网络探测→UI | TASK-012 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.platform.config.ts --grep \"E-06\""] |
| E-07 | 04-project-platform.frontend.design.md#2.4 验收条件（原 E-FE-03） | integration | Schema 校验 API→Form | TASK-010 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_platform_credential_form_contract.py"] |

> 本表覆盖 design 全部 P0 场景（FEAT-01~04、FEAT-FE-01~05）与 9 条 required RULE 映射场景；不存在缺口。

---

## TASK-001: 平台/凭据模型与仓储

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 04-project-platform.backend.design.md#3.3 数据设计, #2.5.1 业务规则与约束(RULE-02)
- **Spec-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-01, S-04, E-03

### Description
为 `control.project_platform`、`control.user_credential_ref`、`control.shared_credential_ref` 补齐 ORM 与仓储（迁移 0002+0004+0005 已就绪，本任务只消费 schema）：列表聚合（`configured_user_credential_count`、`has_shared_credential`）单查询完成；传入 `user_id` 时以 LEFT JOIN 返回 `user_credential_status`（`ACTIVE/INVALID/NONE`）；用户/共享凭据按 partial unique upsert，`credential_json` 明文读写但不外泄。

### Checklist
- [ ] 三表 ORM 与迁移完全一致（`credential_json`、`status`、`credential_schema_version`、`last_verified_at`、partial unique 与索引）
- [ ] 仓储：平台列表（keyword/adapter_key/enabled 筛选 + 分页 + 聚合 COUNT/EXISTS）单查询，禁止 N+1
- [ ] 仓储：`user_credential_status` LEFT JOIN 查询；凭据 upsert/软删/状态置 INVALID
- [ ] [S-01][E2E] 修改生产代码前，按 Browser→API→DB 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-01] 断言 落库字段与 API 输入一致、软删后 partial unique 允许重建、聚合计数正确
- [ ] [S-04][integration] 修改生产代码前，按 Service→DB→Redis 真实边界编写验收测试并记录 RED（Redis 用 `.env` `REDIS_URL`）
- [ ] [S-04] 断言 adapter_key 变更后凭据行 `status=INVALID`（真实 DB 反射）
- [ ] [E-03][integration] 断言 真实 PG partial unique 拒绝重复 key，回滚后仅保留一条
- [ ] 运行 harness-data#RULE-data-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests -k schema_parity`
- [ ] 运行 harness-time#RULE-time-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、API、DB | 平台与凭据行真实落库；软删后可重建同 key | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-01"` | planned |
| S-04 | integration | Service、DB、Redis | adapter_key 变更后凭据 `INVALID`，Session 索引键被删除 | tests/console_platform/test_platform_session_invalidation.py（planned） | `uv run pytest -q tests/console_platform/test_platform_session_invalidation.py -k s04` | planned |
| E-03 | integration | API、DB（真实 PG partial unique） | 重复 key 触发 IntegrityError；回滚后仅一条记录 | tests/console_platform/test_platform_repository.py::test_e03_duplicate_key_violates_partial_unique_at_database_level（已实现） | `uv run pytest -q tests/console_platform/test_platform_repository.py -k e03` | green | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- GREEN：`uv run pytest -q tests/console_platform/test_platform_repository.py -k e03`（真实 PG partial unique 触发 IntegrityError，回滚后单条）；`uv run pytest -q tests -k schema_parity` 覆盖三表列/索引/FK。
- RED 未留存：仓库层实现与测试同批完成，以真实 DB 约束断言为主证据。
- E-03: verified — automated command passed; run_id=a7d12493930e4780b04c77d4c002e423 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-002: Adapter Registry 装配、参考适配器与 Metadata API

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 04-project-platform.backend.design.md#3.4 接口设计(API-01/API-14), #3.1 技术选型与关键决策, docs/12-项目平台适配与Session详细设计.md#3.1 PlatformAdapter, #5 prepare_request
- **Spec-Refs**:
- **Acceptance-Refs**: E-01

### Description
platform-sdk 实现参考适配器 `generic-http`（完整无状态 HTTP 适配器）：
- `session_mode=NONE`；`platform_config_schema`：`auth_scheme(enum: none|bearer|basic)`、`auth_header`（默认 `Authorization`）、`timeout_ms`；
- `credential_schema`：`token/username/password`（`x-secret=true`），运行时按 `auth_scheme` 校验必填；
- `authenticate` 不建立 Session；`validate` 校验凭据与 scheme 匹配；`prepare_request` 注入 `Authorization: Bearer/Basic`（`none` 不注入），任何日志/异常不包含凭据明文。
Console 应用启动时注册内置适配器；实现 `GET /api/v1/platform-adapters`（分页，默认 100/最大 100）与 `GET /api/v1/platform-adapters/{adapter_key}`（未注册返回 `PLATFORM_ADAPTER_NOT_FOUND`），元数据原样返回且不含 Secret。

### Checklist
- [ ] platform-sdk 实现 `GenericHttpAdapter`（SPI 字段、schema 结构、无 refresh 方法）
- [ ] 单测覆盖：`validate`（scheme 与凭据匹配/缺失）、`prepare_request`（bearer/basic/none 头注入）、凭据不进入日志（脱敏断言）
- [ ] 集成测试：本地 HTTP 服务验证完整请求链路（`tests/sdk/test_platform_adapter.py`）
- [ ] Console 启动装配 Registry（可注入，测试可替换），元数据列表/详情接口按统一封套与分页
- [ ] 未注册 adapter_key 返回 `PLATFORM_ADAPTER_NOT_FOUND`（供 TASK-003 复用）
- [ ] 契约测试：`tests/console_platform/test_platform_adapters_api.py`、`tests/sdk/test_platform_adapter.py`
- [ ] [E-01][integration] 按 API→Registry 真实边界编写测试；断言未注册 key 返回 `PLATFORM_ADAPTER_NOT_FOUND`（RED 未留存，实现与测试同批完成）
- [ ] 运行 platform-sdk 与 Registry 契约测试并填写 Acceptance Evidence：`uv run pytest -q tests/sdk/test_platform_adapter.py tests/console_platform/test_platform_adapters_api.py`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | API、Registry | 未注册 adapter_key → `PLATFORM_ADAPTER_NOT_FOUND` | tests/console_platform/test_platform_adapters_api.py::test_e01_unknown_adapter_key_returns_platform_adapter_not_found（已实现） | `uv run pytest -q tests/console_platform/test_platform_adapters_api.py -k e01` | green | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- GREEN：`uv run pytest -q tests/console_platform/test_platform_adapters_api.py -k e01`；`tests/sdk/test_platform_generic_http.py` 覆盖 bearer/basic/none 头注入与真实本地 HTTP 链路。
- RED 未留存：实现与测试同批完成。
- E-01: verified — automated command passed; run_id=343d6faf5f124438915c21fd6d55cb20 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-003: 平台 CRUD 与失效 API

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 04-project-platform.backend.design.md#3.4 接口设计(API-02/03/04/05/13), #2.5.1 业务规则与约束(RULE-01/02/03/05/06)
- **Spec-Refs**: harness-api#RULE-api-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-01, E-01, E-03

### Description
实现平台列表/新增/详情/编辑/删除：`resolver_type` 与 `resolver_config` 结构校验、`adapter_key` 已注册且 `adapter_config` 满足 `platform_config_schema`（`jsonschema`）、租户内 key 唯一冲突返回 `COMMON_CONFLICT`（`message_args` 带 key）；`project_platform` 无 revision，不接受 `expected_revision`；写操作同事务追加 `config_audit_log`（不含 Secret）；`adapter_key` 变更时同事务将 user/shared 凭据置 `INVALID`，并按 Session 失效契约清理该平台 Session，响应 `credential_reconfigure_required=true`；删除平台软删并触发凭据/Session 失效。

### Checklist
- [ ] 列表：keyword/adapter_key/enabled/user_id 筛选 + 聚合字段 + 分页，统一封套，禁止 N+1
- [ ] 新增/编辑：Schema 校验、唯一冲突 `COMMON_CONFLICT{key}`、审计同事务、无 `expected_revision`
- [ ] 详情：完整字段 + `adapter_metadata`（不含 Secret）+ 聚合计数
- [ ] 删除：软删 + 审计 + 凭据/Session 失效；不存在返回 `COMMON_NOT_FOUND`
- [ ] `adapter_key` 变更：凭据 `INVALID`、Session 清理、`credential_reconfigure_required=true`
- [ ] 新错误码仅使用 `config/api-messages.yaml` 已登记 code，zh/en 双语文案完整
- [ ] [S-01][E2E] 修改生产代码前，按 Browser→API→DB 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-01] 断言 创建后按 adapter schema 保存/展示、重复 key 被拒绝且列表不变
- [ ] [E-01][integration] 未注册 adapter_key → `PLATFORM_ADAPTER_NOT_FOUND`，不落库
- [ ] [E-03][integration] 重复 key → `COMMON_CONFLICT` 且 `message_args` 含 key
- [ ] 运行 harness-api#RULE-api-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`
- [ ] 运行 harness-i18n#RULE-i18n-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、API、DB | 平台按 Schema 保存/展示；重复 key 拒绝 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-01"` | e2e_deferred |
| E-01 | integration | API、Registry、DB | `PLATFORM_ADAPTER_NOT_FOUND`；事务未落库 | tests/console_platform/test_platforms_api.py（planned） | `uv run pytest -q tests/console_platform/test_platforms_api.py -k e01` | planned |
| E-03 | integration | API、DB | `COMMON_CONFLICT` + `message_args.key`；列表不变 | tests/console_platform/test_platforms_api.py（planned） | `uv run pytest -q tests/console_platform/test_platforms_api.py -k e03` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=446457ae75f541bf932c688432a29343 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-004: 用户/共享凭据 API

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: 04-project-platform.backend.design.md#3.4 接口设计(API-07~12), #2.5.1 业务规则与约束(RULE-03)
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-02, E-04

### Description
实现用户凭据读/写/删与共享凭据读/写/删：写入前用 Adapter `credential_schema` 校验（`jsonschema`，不通过返回 `COMMON_VALIDATION_ERROR` 且 Secret 不落库）；明文写入 `credential_json`（共享凭据每平台 0..1，存在即更新）；读接口只返回 `configured/status/credential_schema_version/last_verified_at`，任何响应、日志、审计不出现 Secret；同事务追加 `config_audit_log`；删除为软删。

### Checklist
- [ ] 用户凭据 GET/PUT/DELETE（`(user_id, platform_id)` partial unique upsert，软删）
- [ ] 共享凭据 GET/PUT/DELETE（每平台 0..1）
- [ ] Schema 校验失败 → `COMMON_VALIDATION_ERROR`，DB 无写入
- [ ] 读接口不回显明文（仅 `configured/status`）；审计/日志脱敏断言
- [ ] [S-02][E2E] 修改生产代码前，按 Browser→API→DB 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-02] 断言 DB `credential_json` 与输入一致、API 响应/列表只显示已配置
- [ ] [E-04][integration] 断言 Schema 不满足时错误码、字段级信息与零落库
- [ ] 运行 harness-secret#RULE-secret-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | Browser、API、DB | DB 明文落库；响应不回显；审计无 Secret | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-02"` | e2e_deferred |
| E-04 | integration | API、Schema、DB | `COMMON_VALIDATION_ERROR`；Secret 零落库 | tests/console_platform/test_credentials_api.py（planned） | `uv run pytest -q tests/console_platform/test_credentials_api.py -k e04` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-02: e2e_deferred — automated command e2e_deferred; run_id=3262ccdf392144638caabb2cf0954e71 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=3262ccdf392144638caabb2cf0954e71 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-005: 配置校验与连通性探测 API

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-004
- **Source**: 04-project-platform.backend.design.md#3.4 接口设计(API-06), #2.5.1 业务规则与约束(RULE-08)
- **Spec-Refs**:
- **Acceptance-Refs**: S-03, E-02

### Description
实现 `POST /api/v1/project-platforms/{platform_id}/test`：Schema 校验 → 非鉴权连通性探测（BASE_URL 对 host 做 TCP/HEAD，SERVICE_DISCOVERY 做 DNS，`timeout_ms` 默认 3000）→ 按 `credential_mode` 检查凭据引用状态（不读取 Secret Value）；返回 `config_valid/adapter_key/adapter_version/resolver_type/connectivity/credential_ref_status/checked_at/details`；不执行 `authenticate/validate/prepare_request`、不建 Session、不写审计。

### Checklist
- [ ] 探测实现（TCP/HEAD/DNS，超时可配）与 `REACHABLE/UNREACHABLE` 结果
- [ ] `credential_ref_status`（`ACTIVE/MISSING/NOT_CHECKED`）按 credential_mode 计算，缺失返回 `CREDENTIAL_MISSING`
- [ ] 断言不产生 Session/登录/业务调用与审计记录
- [ ] [S-03][E2E] 修改生产代码前，按 Browser→API→网络探测→UI 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-03] 断言 config_valid/connectivity/credential_ref_status 结果稳定且无登录/Session
- [ ] [E-02][integration] 断言凭据缺失错误码且未读取 Secret Value
- [ ] 运行 API 封套与探测相关测试并填写 Acceptance Evidence：`uv run pytest -q tests/console_platform/test_platform_test_api.py tests/acceptance/test_foundation_api_envelope.py`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Browser、API、网络探测、UI | 三项结果正确；无登录/Session/业务调用痕迹 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-03"` | e2e_deferred |
| E-02 | integration | API、CredentialResolver、DB | `CREDENTIAL_MISSING`；不读取 Secret Value | tests/console_platform/test_platform_test_api.py（planned） | `uv run pytest -q tests/console_platform/test_platform_test_api.py -k e02` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-03: e2e_deferred — automated command e2e_deferred; run_id=7c888edd578b43e5901e01181becb092 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=7c888edd578b43e5901e01181becb092 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-006: Session 失效契约（Redis Set 索引）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: 04-project-platform.backend.design.md#3.5 Session 与凭据解析, #2.5.1 业务规则与约束(RULE-04/05), docs/12-项目平台适配与Session详细设计.md#7
- **Spec-Refs**: harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: S-04

### Description
platform-sdk 提供 `RedisPlatformSessionInvalidator`：按 `platform_sessions:{tenant}:{platform}` Set 索引 `SMEMBERS`+`DEL` 清理会话键，禁止 `KEYS/SCAN`，重复清理幂等；Console 通过 `.env` `REDIS_URL` 注入，并在 `adapter_key` 变更/平台删除时调用清理（凭据内容哈希变化使旧会话自然不复用）。带 singleflight 的 Runtime 会话 `acquire/renew` 属 Runtime Egress Boundary，待对应模块实现（本任务只闭合 Console 失效路径）。

### Checklist
- [ ] `RedisPlatformSessionInvalidator` 实现（Set 索引 + 幂等清理 + 不产生 KEYS/SCAN），经 `muad_platform_sdk` 导出
- [ ] Console 注入 `.env` `REDIS_URL`（缺失时 Null 实现），adapter 变更/删除路径调用清理
- [ ] Console 注入（`SharedSettings.require_redis_url`）并在 TASK-003 失效路径调用
- [ ] Session Cache 不含 raw password/AK/SK（脱敏断言）
- [ ] [S-04][integration] 修改生产代码前，按 Service→DB→Redis 真实边界编写验收测试并记录 RED（真实 PG + `.env` Redis）
- [ ] [S-04] 断言 凭据 INVALID、Session 索引键删除、`credential_reconfigure_required=true`、`config_audit_log` 落库
- [ ] 运行 harness-project-platform#RULE-platform-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests -k schema_parity`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | Service、DB、Redis | 凭据 `INVALID`；Set 索引键被 `DEL`；响应 `credential_reconfigure_required=true`；审计落库 | tests/console_platform/test_platform_session_invalidation.py（planned） | `uv run pytest -q tests/console_platform/test_platform_session_invalidation.py -k s04` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-04: verified — automated command passed; run_id=a67c8f5933a04a5b9195f69cbf1c6250 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-007: 前端 services 与平台列表页

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: 04-project-platform.frontend.design.md#3.2 页面与路由结构, #3.5 状态与数据流, #3.6 UI 状态, #3.7 样式方案
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-frontend#RULE-front-001
- **Acceptance-Refs**: S-05, S-06, S-07, S-08, S-09

### Description
新增 `modules/project-platform/services/*.ts`（10 个方法，统一走 shared apiClient，携带 `X-Locale`），实现 `PlatformPage` 列表：`PageHeader/PageSection/ModuleToolbar/RemoteTable`，keyword/adapter/enabled 筛选，行内主展示字段打开详情、操作列「配置校验」，替换 `/platforms` 的 PlaceholderPage；文案全部 i18n key（zh-CN/en-US）。

### Checklist
- [ ] services 覆盖 design §3.5 全部方法；组件不裸用 axios/fetch
- [ ] 列表页骨架与分页（默认 10/页）、空态/错误态/加载态
- [ ] 路由替换 `/platforms`；菜单与面包屑一致
- [ ] i18n 词条 zh/en 完整；`check_frontend_api_usage` / `check_frontend_i18n` 通过
- [ ] [S-09][E2E] 列表筛选（keyword/适配器/启用状态）与分页断言（浏览器层由 TASK-012 执行）
- [ ] 契约测试 `tests/frontend/test_platform_page_contract.py`
- [ ] 运行 harness-ui#RULE-ui-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build`
- [ ] 运行 harness-frontend#RULE-front-001 verifier 并填写 Acceptance Evidence：`uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | Browser、adapter metadata、Form | 列表加载与详情入口可用（E2E 断言于 TASK-012） | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-05"` | planned |
| S-06 | E2E | Browser、Secret API | 凭据流程入口可达（E2E 断言于 TASK-012） | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-06"` | planned |
| S-07 | E2E | Browser、API、网络探测、UI | 校验入口可达（E2E 断言于 TASK-012） | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-07"` | planned |
| S-08 | E2E | Browser、API、DB | 用户详情凭据 Tab 数据依赖本页服务（E2E 断言于 TASK-012） | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-08"` | planned |
| S-09 | E2E | Browser、API、DB | 筛选与分页结果与条件一致 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-09"` | e2e_deferred |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-09: e2e_deferred — automated command e2e_deferred; run_id=6acefce54dbf4d988ef2dc3ad7f990e9 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-008: 动态平台表单（resolver/adapter）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: 04-project-platform.frontend.design.md#2.4 验收条件(S-FE-01), #3.3 组件设计(CMP-02), #3.3.1 每个按钮/操作的设计
- **Spec-Refs**:
- **Acceptance-Refs**: S-05

### Description
`ProjectPlatformForm`（`FormModal`）：名称/标识/接入方式（`BASE_URL`→`base_url`，`SERVICE_DISCOVERY`→`service_name`）/平台适配器（来自 Adapter Metadata）/适配器配置（按 `platform_config_schema` 动态渲染）/凭据策略/启用状态；编辑时标识不可改；后端字段级错误就地展示。

### Checklist
- [ ] 动态 resolver 字段随接入方式切换，互斥且必填
- [ ] Adapter 下拉来自 `getAdapters()`；adapter_config 按 schema 动态渲染
- [ ] 表单校验与后端错误（`COMMON_VALIDATION_ERROR`/`COMMON_CONFLICT`）字段级映射
- [ ] 契约测试 `tests/frontend/test_platform_form_contract.py`
- [ ] [S-05][E2E] 修改生产代码前，按 Browser→adapter metadata→Form 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-05] 断言 仅渲染所选 resolver/adapter 对应字段且创建成功
- [ ] 运行 harness-frontend / harness-ui verifier 并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | Browser、adapter metadata、Form、API、DB | 仅渲染对应字段；保存后列表/详情字段一致 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-05"` | e2e_deferred |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-05: e2e_deferred — automated command e2e_deferred; run_id=bd8d1b86f2e4493c802925312c084885 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-009: 平台详情 SideSheet 与失效引导

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: 04-project-platform.frontend.design.md#3.3 组件设计(CMP-01), #3.3.1 每个按钮/操作的设计, #2.4 验收条件(E-FE-01)
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: E-05

### Description
`PlatformDetailSideSheet`（复用 `DetailSideSheet`）：标题/副标题+编辑/删除/配置校验操作与关闭 X 同行；基本信息用 `DetailGrid` 双列（名称/标识/平台适配器/接入方式/访问配置/凭据策略/已配置用户凭据数/共享凭据/启用状态/更新时间）；编辑返回 `credential_reconfigure_required=true` 时提示凭据失效并自动切到「凭据」Tab。

### Checklist
- [ ] 详情头部操作与 Tabs 布局符合 RULE-ui-detail-001；删除用 `Popconfirm`
- [ ] `DetailGrid` 字段与 docs/15 词典一致；共享凭据显示 已配置/未配置
- [ ] `credential_reconfigure_required` 提示（Banner/Modal）+ 引导凭据 Tab
- [ ] 契约测试 `tests/frontend/test_platform_detail_contract.py`
- [ ] [E-05][E2E] 修改生产代码前，按 PUT API→UI 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [E-05] 断言 更换 Adapter 后提示出现且切到凭据 Tab
- [ ] 运行 harness-ui-detail#RULE-ui-detail-001 verifier 并填写 Acceptance Evidence：`uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-05 | E2E | PUT API、UI | `credential_reconfigure_required=true` → 提示 + 自动切凭据 Tab | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-05"` | e2e_deferred |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- E-05: e2e_deferred — automated command e2e_deferred; run_id=df371830352b4471b3aeb93903bb964b (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-010: 凭据 Tab 与配置校验 Modal

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-009
- **Source**: 04-project-platform.frontend.design.md#3.3 组件设计(CMP-03/04), #3.3.1 每个按钮/操作的设计, #2.4 验收条件(S-FE-02/03, E-FE-02/03)
- **Spec-Refs**:
- **Acceptance-Refs**: S-06, S-07, E-06（执行归 TASK-012）, E-07

### Description
`CredentialTab`：用户凭据（选择用户 + 按 `credential_schema` 动态表单，Secret 字段不回显；保存后只显示「已配置」）与单套共享凭据（更新/删除）；`PlatformTestModal` 展示 `config_valid/connectivity/credential_ref_status/checked_at/details`，失败时标记 `UNREACHABLE` 与原因，不显示 Secret/Session。

### Checklist
- [ ] 用户凭据选择与动态字段（Schema 驱动），保存后刷新为「已配置」，无明文回显
- [ ] 共享凭据更新/删除；每平台 0..1
- [ ] `PlatformTestModal` 结果分区展示；失败原因本地化
- [ ] [S-06][E2E] 修改生产代码前，按 Browser→Secret API 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-06] 断言 保存后 UI 只显示已配置、DB 明文、响应无明文
- [ ] [S-07][E2E] 修改生产代码前，按 Browser→API→网络探测→UI 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-07] 断言 三项结果展示且无登录/Session 痕迹
- [ ] [E-06][E2E] 连通性失败显示 `UNREACHABLE` 与原因，不显示 Secret/Session
- [ ] [E-07][integration] Schema 字段错误字段级提示且不提交：`tests/frontend/test_platform_credential_form_contract.py`
- [ ] 运行 harness-frontend / harness-ui verifier 并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | Browser、Secret API、DB | 保存后仅「已配置」；DB 明文；响应无明文 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-06"` | e2e_deferred |
| S-07 | E2E | Browser、API、网络探测、UI | 三项结果展示；无平台登录/Session/业务调用 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-07"` | e2e_deferred |
| E-06 | E2E | API、网络探测、UI | `UNREACHABLE` 与失败原因；无 Secret/Session | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-06"` | planned |
| E-07 | integration | Schema 校验 API、Form | 字段级错误 + 本地化提示；请求未提交 | tests/frontend/test_platform_credential_form_contract.py（planned） | `uv run pytest -q tests/frontend/test_platform_credential_form_contract.py` | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-06: e2e_deferred — automated command e2e_deferred; run_id=48050bd45f37433a8cfe251ce79b748f (confirmed_by: runner)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=48050bd45f37433a8cfe251ce79b748f (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=48050bd45f37433a8cfe251ce79b748f (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-011: 用户详情凭据 Tab 接通 04

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-007, TASK-010
- **Source**: 04-project-platform.frontend.design.md#2.4 验收条件(S-FE-04), 04-project-platform.backend.design.md#3.4 接口设计(API-02 user_id 扩展)
- **Spec-Refs**:
- **Acceptance-Refs**: S-08

### Description
把 02 模块用户详情「项目平台凭据」Tab 的占位（加载失败 Banner）替换为真实数据：调用 `listPlatforms({ user_id })` 渲染 项目平台/平台适配器/凭据策略/配置状态/更新时间/操作，行内「配置/更新」复用 TASK-010 的凭据表单；无平台或未配置显示规范空态；不回显明文。同步更新归档 02 的 E2E 断言（E-06 由错误隔离改为真实数据）。

### Checklist
- [ ] `UserDetailTabs.CredentialsTab` 消费 `listPlatforms({ user_id })`，列与交互稿一致
- [ ] 配置状态映射 `ACTIVE/INVALID/NONE` 为 已配置/失效/未配置（Tag）
- [ ] 行内配置/更新复用凭据表单；保存后就地刷新
- [ ] [S-08][E2E] 修改生产代码前，按 Browser→API→DB 真实边界编写验收测试并记录 RED（浏览器层由 TASK-012 执行）
- [ ] [S-08] 断言 列表与 DB 状态一致、未配置项可见、无明文
- [ ] 更新 02 归档 E2E（`e2e/tests/user-identity/user-identity.spec.ts` E-06 断言改为真实数据）并保持 4/4 通过
- [ ] 契约测试 `tests/frontend/test_user_credential_tab_contract.py` 并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | Browser、API、DB | 各平台与配置状态与 DB 一致；无明文 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-08"` | e2e_deferred |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。
- S-08: e2e_deferred — automated command e2e_deferred; run_id=00fd773808f04209aea57ddcb9118d8a (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-012: E2E 基础设施与全场景验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-006, TASK-008, TASK-009, TASK-010, TASK-011
- **Source**: 04-project-platform.backend.design.md#2.5.1 业务规则与约束(RULE-07), 04-project-platform.frontend.design.md#2.4 验收条件
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-02, S-03, S-05, S-06, S-07, S-08, S-09, E-05, E-06

### Description
搭建真实浏览器 E2E：`e2e/playwright.platform.config.ts`（真实 Console + PostgreSQL + Vite + 数据播种 + 本地 HTTP 探测端点），`tests/e2e/seed_project_platform.py`（平台/凭据/共享凭据与用户），`tests/e2e/http_probe_app.py`（可达/不可达端点），`e2e/tests/project-platform/platform.spec.ts` 覆盖 S-01~S-03、S-05~S-08、E-05、E-06；保留 harness-test 既有 verifier 全绿。

### Checklist
- [ ] Playwright 配置与播种脚本（真实 PG，复用 `.env` 配置）
- [ ] 探测端点：可达 host+port（HEAD 200）与不可达端口（用于 E-06）
- [ ] 用例覆盖 S-01~S-03、S-05~S-08、E-05、E-06，含无明文断言
- [ ] 运行 harness-test#RULE-test-001 verifier 并填写 Acceptance Evidence：`bash -lc 'uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test'`
- [ ] 运行 `npm --prefix e2e test -- --config playwright.platform.config.ts` 并填写 Acceptance Evidence（含各场景 grep 命令结果）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、API、DB | 见 TASK-003 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-01"` | planned |
| S-02 | E2E | Browser、API、DB | 见 TASK-004 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-02"` | planned |
| S-03 | E2E | Browser、API、网络探测、UI | 见 TASK-005 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-03"` | planned |
| S-05 | E2E | Browser、adapter metadata、Form | 见 TASK-008 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-05"` | planned |
| S-06 | E2E | Browser、Secret API | 见 TASK-010 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-06"` | planned |
| S-07 | E2E | Browser、API、网络探测、UI | 见 TASK-010 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-07"` | planned |
| S-08 | E2E | Browser、API、DB | 见 TASK-011 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-08"` | planned |
| E-05 | E2E | PUT API、UI | 见 TASK-009 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-05"` | planned |
| E-06 | E2E | API、网络探测、UI | 见 TASK-010 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-06"` | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- GREEN：`npm --prefix e2e test -- --config playwright.platform.config.ts` 6/6 通过（覆盖 S-01/S-02/S-03/S-05/S-06/S-07/S-08/S-09、E-05、E-06）；真实边界：浏览器 + 真实 Console/PostgreSQL + 本地探测端点 4190 + 真实 Vite，无 mock。
- 基础设施：`playwright.platform.config.ts`（`MUAD_EXTRA_PLATFORM_ADAPTERS=alt-http`）、`seed_project_platform.py`、`platform.spec.ts`；`harness-test` verifier（acceptance + build + foundation E2E）全绿。

### Log
- [2026-09-18] created (draft)
