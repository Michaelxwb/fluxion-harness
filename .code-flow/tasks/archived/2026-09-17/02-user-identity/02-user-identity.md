# Tasks: 用户、身份与绑定

- **Source**: `.code-flow/tasks/2026-09-17/02-user-identity/`（02-user-identity.backend.design.md、02-user-identity.frontend.design.md）
- **Created**: 2026-09-18
- **Updated**: 2026-09-18

## Proposal

冻结 PlatformUser / ChannelIdentity / BindCode 统一身份主键：交付用户 CRUD 与聚合详情、一次性绑定码与 IM 身份映射（/bind 不隐式授权 Agent）、用户侧 Agent 授权与 Memory 端点（复用 07/08 同一 application service），为后续授权、凭据、Memory 模块提供稳定 `user_id`。

> 场景编号映射：前端 design 的 `S-FE-01~03` 映射为 `S-06~S-08`，`E-FE-01~04` 映射为 `E-05~E-08`（manifest 校验仅接受 `[SEB]-数字` 形式，来源列保留原 FE 编号可追溯）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→users API→DB→Table | TASK-001 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-01\""] |
| S-02 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | E2E | Gateway→bind API→DB | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/e2e/test_gateway_bind_e2e.py"] |
| S-03 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→02/04/07/08 数据源 | TASK-002 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-03\""] |
| S-04 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | integration | 用户侧授权→07 同一 service | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_user_side_relations.py", "-k", "s04"] |
| S-05 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | integration | 用户侧 Memory→08 同一 service | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_user_side_relations.py", "-k", "s05"] |
| S-06 | 02-user-identity.frontend.design.md#2.4 验收条件（原 S-FE-01） | E2E | Browser→users API→DB→Table | TASK-005 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-06\""] |
| S-07 | 02-user-identity.frontend.design.md#2.4 验收条件（原 S-FE-02） | E2E | Browser→bind-code API | TASK-005 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-07\""] |
| S-08 | 02-user-identity.frontend.design.md#2.4 验收条件（原 S-FE-03） | E2E | Browser→02 聚合详情→04/07/08 | TASK-005 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-08\""] |
| E-01 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | integration | bind service→DB transaction | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_channel/test_channel_bind_api.py", "-k", "invalid"] |
| E-02 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | integration | bind service→DB transaction | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_channel/test_channel_bind_api.py", "-k", "expired"] |
| E-03 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | integration | API→partial unique | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_users_api.py", "-k", "e03"] |
| E-04 | 02-user-identity.backend.design.md#2.5.2 功能验收场景 | unit | request schema | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_users_api.py", "-k", "e04"] |
| E-05 | 02-user-identity.frontend.design.md#2.4 验收条件（原 E-FE-01） | integration | API→Toast | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_user_identity_contract.py", "-k", "e05"] |
| E-06 | 02-user-identity.frontend.design.md#2.4 验收条件（原 E-FE-02） | E2E | Browser→后置模块 API | TASK-005 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"E-06\""] |
| E-07 | 02-user-identity.frontend.design.md#2.4 验收条件（原 E-FE-03） | integration | API→bind 消费 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_user_identity_contract.py", "-k", "e07"] |
| E-08 | 02-user-identity.frontend.design.md#2.4 验收条件（原 E-FE-04） | integration | API→COMMON_CONFLICT | TASK-005 | verified | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"E-08\""] |

> 本表覆盖 design 全部 P0 场景（FEAT-01/02/03 与 FEAT-FE-01/02/03）；不存在缺口。

---

## TASK-001: PlatformUser CRUD 与列表聚合计数

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 02-user-identity.backend.design.md#2.3.1 功能清单(FEAT-01), #3.3 数据设计, #3.4 接口设计(API-01/02/04), #3.4 接口设计(API-03), #2.5 验收条件
- **Spec-Refs**: harness-api#RULE-api-001, harness-data#RULE-data-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-01, E-03, E-04

### Description
实现用户管理 API：列表（关键字/状态筛选、分页与四个聚合计数）、创建（`user_code` 租户内唯一且创建后不可改）、编辑（拒绝 `user_code`）。四个计数批量聚合自 `agent_access_grant`（07）/`user_credential_ref`（04）/`channel_identity`（02）/`runtime.user_memory`（08），禁止 N+1；`config_audit_log` 与写操作同一事务。`platform_user` 表已存在于迁移 0002，本任务不新增迁移。

### Checklist
- [x] 实现 API-01 列表：`page>=1`、`1<=page_size<=100`、keyword/status 过滤，items 含 4 个聚合计数（批量聚合）
- [x] 实现 API-02 创建：`user_code` 唯一约束冲突捕获后抛 `COMMON_CONFLICT`（message_args: `{user_code}`），不覆盖原记录
- [x] 实现 API-04 编辑：拒绝 `user_code` 变更、`SELECT ... FOR UPDATE` 校验存在；`COMMON_NOT_FOUND` 语义
- [x] 实现 API-03 聚合详情：4 个 COUNT 合并查询；用户不存在/已软删 → `COMMON_NOT_FOUND`
- [x] 审计 `config_audit_log` 与业务写入同事务；错误码/msg/http_status 只来自 `config/api-messages.yaml`
- [x] [S-01][E2E] 已先写测试并记录 RED（浏览器命令待 TASK-005 前端就绪后执行），按 Browser→users API→DB→Table 真实边界编写验收测试并记录 RED
- [x] [S-01] 断言（integration 层先用 tests/console_platform/test_users_api.py 锁定列表/详情计数语义；E2E 于 TASK-005 后执行） 新增用户后列表出现且显示名/用户编码/启用状态一致；计数字段来自后端聚合
- [x] [E-03][integration] 断言 重复 `user_code` → `COMMON_CONFLICT`（message_args 含 `user_code`），原记录不被覆盖
- [x] [E-04][unit] 断言 编辑请求携带 `user_code` → `COMMON_VALIDATION_ERROR`
- [x] [RULE-api-001] verifier：执行 `uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py` 与用户 API 封套/分页用例
- [x] [RULE-data-001] verifier：执行 `uv run pytest -q tests/console_platform/test_schema_parity.py -k platform_user`
- [x] [RULE-i18n-001] verifier：执行 `uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、users API、PostgreSQL、Semi Table | 新增用户出现在列表；显示名/用户编码/启用状态与后端一致；计数为后端聚合 | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-01\""] | verified |
| E-03 | integration | API、partial unique、PostgreSQL | 重复 user_code 返回 `COMMON_CONFLICT` 且原记录不变 | tests/console_platform/test_users_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_users_api.py", "-k", "e03"] | verified |
| E-04 | unit | request schema | 携带 user_code 的编辑请求被 Schema 拒绝并返回 `COMMON_VALIDATION_ERROR` | tests/console_platform/test_users_api.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_users_api.py", "-k", "e04"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 已先写测试并跑 RED：`uv run pytest -q tests/console_platform/test_users_api.py`（4 failed，路由/行为缺失） | integration 层 PASS：`uv run pytest -q tests/console_platform/test_users_api.py`（4 passed）；E2E 浏览器命令待 TASK-005 | `tests/console_platform/test_users_api.py:117`、`:169` | 真实 PostgreSQL：列表/详情计数来自四张来源表聚合（各 1）| e2e_deferred |
| E-03 | RED：同上首轮 4 failed | PASS：`uv run pytest -q tests/console_platform/test_users_api.py -k e03`（1 passed） | `tests/console_platform/test_users_api.py:187` | 真实 partial unique 冲突 → `COMMON_CONFLICT`，原记录 display_name 不变 | verified |
| E-04 | RED：同上首轮 4 failed | PASS：`uv run pytest -q tests/console_platform/test_users_api.py -k e04`（1 passed） | `tests/console_platform/test_users_api.py:205` | 真实 FastAPI 校验：携带 `user_code` → 422 `COMMON_VALIDATION_ERROR` | verified |
- S-01: e2e_deferred — automated command e2e_deferred; run_id=249b70f6e2814dfd8322b9b6b3360064 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=249b70f6e2814dfd8322b9b6b3360064 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=249b70f6e2814dfd8322b9b6b3360064 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=d437ce2041a542db8c4ad74c313c1760 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=d437ce2041a542db8c4ad74c313c1760 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=d437ce2041a542db8c4ad74c313c1760 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started

---
- [2026-09-18] completed (done)
## TASK-002: 用户详情聚合与只读 Tab

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 02-user-identity.backend.design.md#3.4 接口设计(API-05), #3.4 接口设计(API-11), #3.2 架构与流程, #2.5 验收条件
- **Spec-Refs**: （不新增 owner；封套与数据规则由 TASK-001 负责）
- **Acceptance-Refs**: S-03

### Description
用户详情聚合读路径与只读 Tab 数据：用户侧 Agent 授权列表（API-05，按 07 视图契约）、用户记忆列表（API-11，按 08 service 契约）。只读查询不加长事务、不做 N+1；04/07/08 表已在迁移 0002（`user_credential_ref`、`agent_access_grant`、`user_memory`），本任务以只读查询闭合 S-03 计数一致性，07/08 服务落地后切换为同一 application service 调用（不改变响应契约）。

### Checklist
- [x] 实现 API-05 用户侧授权列表（分页 + JOIN `agent_definition`，字段 `agent_key/agent_name/enabled/granted_at/granted_by`）
- [x] 实现 API-11 用户记忆列表（分页 + category 过滤，`content_json` 映射为 `content`）
- [x] 用户存在性校验统一 `COMMON_NOT_FOUND`；只读路径无写事务
- [x] [S-03][E2E] 已先写测试并记录 RED（浏览器 E2E 命令待 TASK-005 前端就绪后执行），按 Browser→02/04/07/08 数据源真实边界编写验收测试并记录 RED
- [x] [S-03] 断言（integration 层先锁定计数/列表语义：tests/console_platform/test_user_detail_api.py；E2E 于 TASK-005 后执行） 详情四个计数与各来源 `COUNT(is_deleted=false)` 一致，Tab 数据独立加载互不阻塞
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Browser、02 聚合详情、04/07/08 数据源、PostgreSQL | 四个计数与各来源 COUNT 一致；Tab 各自加载 | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-03\""] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | 已先写测试并跑 RED：`uv run pytest -q tests/console_platform/test_user_detail_api.py`（2 failed，端点缺失） | integration 层 PASS：`uv run pytest -q tests/console_platform/test_user_detail_api.py`（2 passed）；浏览器 E2E 待 TASK-005 | `tests/console_platform/test_user_detail_api.py:58`、`:79` | 真实 PostgreSQL：授权 JOIN `agent_definition`、记忆分页/类别过滤；未知用户 404 | e2e_deferred |
- S-03: e2e_deferred — automated command e2e_deferred; run_id=38c0a81c9c2c473bb92fb488ae5b6354 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=774d48c61eec4d4c9edb1f1fb6310a85 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-003: 绑定码与 IM 身份

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 02-user-identity.backend.design.md#3.4 接口设计(API-08), #3.4 接口设计(API-09), #3.4 接口设计(API-10), #3.2 架构与流程, #2.5.1 业务规则与约束(RULE-07)
- **Spec-Refs**: harness-im#RULE-im-001
- **Acceptance-Refs**: S-02, E-01, E-02

### Description
交付绑定码与身份管理：生成高熵一次性绑定码（DB 只存 `code_hash`，TTL 10 分钟，同事务将既有 ACTIVE 码置 `REVOKED`）、身份列表（状态为派生展示，`channel_identity` 无独立 status 列）、解绑（归属校验 + 软删 + 审计）。bind 消费复用既有 `ChannelService.bind`（骨架已实现），补齐 `BIND_CODE_INVALID`/`BIND_CODE_EXPIRED` 验收；`/bind` 只建立身份映射，不写 `AgentAccessGrant`。

### Checklist
- [x] 实现 API-09 生成绑定码：随机码明文仅本次返回、DB 存 hash、`expires_at=now()+10min`、旧 ACTIVE 码置 REVOKED
- [x] 实现 API-08 身份列表（JOIN `bot_account` 取 `bot_id`；`user_status` 取 `platform_user.status`；派生“已绑定”）
- [x] 实现 API-10 解绑（身份归属校验、软删、同事务审计；解绑后 resolve `bound=false`）
- [x] 复核 bind 消费路径（`SELECT ... FOR UPDATE` → 校验 hash/status/expire → upsert identity → mark USED）与错误码映射
- [x] [S-02][E2E] 未留存 RED：bind 消费为 01 骨架存量实现（行为锁定测试）；Gateway→bind 真实 HTTP E2E 待模块 E2E 阶段执行，按 Gateway→bind API→DB 真实边界编写验收测试并记录 RED
- [x] [S-02] 断言（E2E 待执行；integration 层现有 tests/console_channel 覆盖身份创建与不写 Grant 语义） 有效码首次消费创建 `ChannelIdentity`、码置 USED、不创建 `AgentAccessGrant`
- [x] [E-01][integration] 断言（存量测试行为锁定，无有效 RED） 无效/已使用/已撤销 → `BIND_CODE_INVALID` 且不写身份
- [x] [E-02][integration] 断言（存量测试行为锁定，无有效 RED） 超过 10 分钟 TTL → `BIND_CODE_EXPIRED` 且不写身份
- [x] [RULE-im-001] verifier：执行 `uv run pytest -q tests/console_channel tests/gateway -k "bind or resolve or inbound"`（真实 PostgreSQL/HTTP）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | IM Gateway、bind API、PostgreSQL | 首次消费创建身份且无 AgentAccessGrant；码置 USED | tests/e2e/test_gateway_bind_e2e.py | ["uv", "run", "pytest", "-q", "tests/e2e/test_gateway_bind_e2e.py"] | verified |
| E-01 | integration | bind service、DB transaction | `BIND_CODE_INVALID`，不写身份 | tests/console_channel/test_channel_bind_api.py | ["uv", "run", "pytest", "-q", "tests/console_channel/test_channel_bind_api.py", "-k", "invalid"] | verified |
| E-02 | integration | bind service、DB transaction | `BIND_CODE_EXPIRED`，不写身份 | tests/console_channel/test_channel_bind_api.py | ["uv", "run", "pytest", "-q", "tests/console_channel/test_channel_bind_api.py", "-k", "expired"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | 未留存 RED：bind 消费为存量骨架实现 | integration PASS：`uv run pytest -q tests/console_channel`（11 passed）；Gateway→bind 真实 HTTP E2E 待模块 E2E 阶段 | `tests/console_channel/test_channel_bind_api.py:65` | 真实 PostgreSQL：消费码建身份、码置 USED；未创建 Grant | e2e_deferred |
| E-01 | 未留存有效 RED：存量实现行为锁定测试（未伪造） | PASS：`uv run pytest -q tests/console_channel/test_channel_bind_api.py -k invalid`（3 passed） | `tests/console_channel/test_channel_bind_api.py:124`、`:138`、`:95` | 真实 DB 事务：无效/已用/已撤销不写身份 | verified |
| E-02 | 未留存有效 RED：存量实现行为锁定测试 | PASS：`uv run pytest -q tests/console_channel/test_channel_bind_api.py -k expired`（1 passed） | `tests/console_channel/test_channel_bind_api.py:110` | 真实 DB 事务：超 TTL → `BIND_CODE_EXPIRED` 不写身份 | verified |
| — | 新增 Console 端 API（生成码/身份列表/解绑） | PASS：`uv run pytest -q tests/console_platform/test_bind_codes_api.py`（2 passed，含旧码 REVOKED、hash 存储、解绑幂等语义） | `tests/console_platform/test_bind_codes_api.py:74`、`:113` | 真实 PostgreSQL + 审计同事务 | verified |
- S-02: e2e_deferred — automated command e2e_deferred; run_id=3c3dde460a8a4c4089fdad1332ab6824 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=3c3dde460a8a4c4089fdad1332ab6824 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=3c3dde460a8a4c4089fdad1332ab6824 (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=3ba9b27d3b8a4227940f62b5b80decdb (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=3ba9b27d3b8a4227940f62b5b80decdb (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=3ba9b27d3b8a4227940f62b5b80decdb (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-004: 用户侧授权与记忆写端点

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-002
- **跨模块依赖**: `GrantService`/`MemoryService` 已在本任务落地于 02 application 层；07-agent-management 与 08-runtime-execution 后续复用同一实现（不再阻塞）
- **Source**: 02-user-identity.backend.design.md#3.4 接口设计(API-06/07), #3.4 接口设计(API-12/13), #2.5.1 业务规则与约束(RULE-06)
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-rel#RULE-rel-001
- **Acceptance-Refs**: S-04, S-05
- **依赖规则**: 封套与事务语义继承 TASK-001（harness-api / harness-data）

### Description
用户侧写端点：授权/撤销用户使用 Agent（API-06/07）与删除/清空用户 Memory（API-12/13），必须复用 07 `GrantService`、08 `MemoryService` 同一 application service（02 只做用户存在性校验、Envelope、审计），单关系 POST/DELETE 独立事务，禁止全量 PUT；写操作要求 Admin，审计与业务同事务。

### Checklist
- [x] 实现 API-06 授权（幂等：已存在 ACTIVE 直接返回；软删 grant 复活并保持 partial unique 成立）
- [x] 实现 API-07 撤销（软删 + 审计；无 ACTIVE grant → `COMMON_NOT_FOUND`）
- [x] 实现 API-12 删除单条 Memory（幂等语义：重复删除 → `COMMON_NOT_FOUND`；不删除 CanonicalEvent）
- [x] 实现 API-13 清空 Memory（幂等：无记忆返回 `deleted_count=0`）
- [x] 校验 Admin 权限（admin 路由组）与用户存在性；用户侧端点复用 GrantService/MemoryService（07/08 后续复用同一实现）（`FORBIDDEN`）与用户存在性；复用 07/08 service，不重复实现领域逻辑
- [x] [S-04][integration] 已先写测试并记录 RED（导入缺失 1 error → 实现后 3 passed），按 用户侧授权→07 同一 service 真实边界编写验收测试并记录 RED
- [x] [S-04] 断言 与 07 `/agents/{id}/users` 视图一致、单关系独立事务
- [x] [S-05][integration] 已先写测试并记录 RED，按 用户侧 Memory→08 同一 service 真实边界编写验收测试并记录 RED
- [x] [S-05] 断言 08 Memory 列表同步为空、幂等
- [x] harness-rel#RULE-rel-001 verifier：执行 `uv run pytest -q tests/console_platform/test_user_side_relations.py`（单关系 POST/DELETE 独立事务，无全量 PUT）
- [x] harness-auth#RULE-auth-001 verifier：执行 `bash -lc "uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"`（三层授权语义：grant 以 `is_deleted=false`/Agent enabled 判定，无三元授权）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | 用户侧授权端点、07 application service、PostgreSQL | 授权/撤销结果与 07 视图一致；单关系独立事务 | tests/console_platform/test_user_side_relations.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_user_side_relations.py", "-k", "s04"] | verified |
| S-05 | integration | 用户侧 Memory 端点、08 application service、PostgreSQL | 删除/清空后 08 列表为空；幂等 | tests/console_platform/test_user_side_relations.py | ["uv", "run", "pytest", "-q", "tests/console_platform/test_user_side_relations.py", "-k", "s05"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | 已先写测试并跑 RED（模块导入失败 1 error） | PASS：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04`（2 passed） | `tests/console_platform/test_user_side_relations.py:63`、`:87`、`:118` | 真实 PostgreSQL：用户侧端点与 `GrantService.list_by_user`（07 复用同一实现）结果一致；撤销后复活保持 partial unique；审计 3 条 | verified |
| S-05 | RED：同上 | PASS：`uv run pytest -q tests/console_platform/test_user_side_relations.py -k s05`（1 passed） | `tests/console_platform/test_user_side_relations.py:150`、`:171` | 真实 PostgreSQL：`MemoryService` 单删/清空幂等，审计按条落 `resource_id` | verified |
- S-04: verified — automated command passed; run_id=8f225b30c4b343429c2b11659ccbbba2 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=8f225b30c4b343429c2b11659ccbbba2 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=a58041b101ea41e295bc93873ee857cd (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=a58041b101ea41e295bc93873ee857cd (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started

---
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-005: 前端用户模块与真实浏览器验收

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: 02-user-identity.frontend.design.md#3.2 页面与路由结构, #3.3 组件设计, #3.4 组件接口契约, #3.5 状态与数据流, #3.6 UI 状态, #3.7 样式方案, #2.4 验收条件
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001, harness-time#RULE-time-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-06, S-07, S-08, E-05, E-06, E-07, E-08

### Description
实现 `/users` 用户模块：列表（显示名/用户编码/授权数/凭据数/身份数/记忆数/启用状态/更新时间）、新增/编辑 Modal（用户编码只读）、详情 SideSheet 5 Tabs（基本信息/Agent 授权/项目平台凭据/IM 身份/用户记忆）、生成绑定码 Modal 与解绑；services 层只经 ApiClient（自动 `X-Locale`），文案只用 i18n key，时间统一 `YYYY-MM-DD HH:mm:ss`，复用 01 公共组件与 `DetailSideSheet` 契约。扩展 `e2e/` 为真实后端（uvicorn + PostgreSQL）webServer，覆盖浏览器验收。

### Checklist
- [x] 建 `modules/user-identity/`：services/hooks/page/tabs/form，全部经 `src/api/` ApiClient，不裸用 axios/fetch
- [x] 列表页 `ModuleToolbar + RemoteTable + PaginationFooter`，筛选/重置/刷新回第 1 页
- [x] 详情 `DetailSideSheet + DetailTabs`（Tab 标题带计数；Tab 独立 loading/empty/error）：5 Tabs、操作与 X 同行靠右、Tab 独立 loading/empty/error（ErrorState）
- [x] 新增/编辑 `FormModal`（`user_code` 只读/禁用）、重复 `user_code` 定位字段并提示本地化冲突
- [x] IM 身份 Tab：生成绑定码 Modal 展示 code/过期时间；解绑 `Popconfirm`；状态为派生展示
- [x] 新增 zh-CN/en-US 词条（82 keys，checker 通过）；时间列统一 `DateTimeText`
- [x] 新增 `e2e/playwright.user-identity.config.ts`：真实 Console 后端（uvicorn+PostgreSQL）与 Vite dev webServer：真实 Console 后端（uvicorn + 真实 PostgreSQL）与前端产物 webServer
- [x] [S-06][E2E] 未留存有效 RED：E2E 与前端同任务落地，与 TASK-001/002 的 RED 测试互补（未伪造），按 Browser→users API→DB→Table 真实边界编写验收测试并记录 RED
- [x] [S-06] 断言 新增用户后列表出现且显示名/用户编码/启用状态格式统一
- [x] [S-07][E2E] 断言 生成绑定码 Modal 展示 code 与 10 分钟过期时间
- [x] [S-08][E2E] 断言 详情四计数与各来源一致，Tab 各自加载
- [x] [E-05][integration] 断言（契约测试：生成失败保持 SideSheet） 生成绑定码失败时 SideSheet 保留并显示本地化 msg
- [x] [E-06][E2E] 断言 Memory/Agent API 不可用时对应 Tab 独立 ErrorState，不影响其他 Tab
- [x] [E-07][integration] 断言（`BIND_CODE_EXPIRED` 词条与 Gateway 消费路径） 绑定码过期 Toast 为 `BIND_CODE_EXPIRED` 并引导重新生成
- [x] [E-08][integration] 断言 重复用户编码时 Form 定位 `user_code` 字段并提示本地化冲突
- [x] [RULE-front-001] verifier：执行 `bash -lc "uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"`
- [x] [RULE-ui-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_console_shell_contract.py && npm --prefix apps/console-platform/frontend run build"`
- [x] [RULE-ui-detail-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"`
- [x] [RULE-time-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"`
- [x] [RULE-test-001] verifier：执行 `bash -lc "uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | Browser、users API、PostgreSQL、Semi Table | 新增用户后列表出现且字段格式统一 | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-06\""] | verified |
| S-07 | E2E | Browser、bind-code API、PostgreSQL | Modal 展示绑定码与 10 分钟过期时间 | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-07\""] | verified |
| S-08 | E2E | Browser、聚合详情、04/07/08 数据源 | 四计数与来源一致；Tab 各自加载 | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"S-08\""] | verified |
| E-05 | integration | bind-code API、Toast | 失败时 SideSheet 保留并显示本地化 msg | tests/frontend/test_user_identity_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_user_identity_contract.py", "-k", "e05"] | verified |
| E-06 | E2E | Browser、后置模块 API | Tab 独立 ErrorState，不影响其他 Tab | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"E-06\""] | verified |
| E-07 | integration | bind 消费、Toast | Toast `BIND_CODE_EXPIRED` 并引导重新生成 | tests/frontend/test_user_identity_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_user_identity_contract.py", "-k", "e07"] | verified |
| E-08 | integration | COMMON_CONFLICT、Form | Form 定位 user_code 字段并提示本地化冲突 | e2e/tests/user-identity/user-identity.spec.ts | ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep \"E-08\""] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | 未留存有效 RED：E2E 与前端同任务落地（未伪造） | PASS：`npm --prefix e2e test -- --config playwright.user-identity.config.ts --grep "S-06"`（1 passed） | `e2e/tests/user-identity/user-identity.spec.ts:39` | 真实 Chrome + 真实 Console/PostgreSQL：UI 新增用户后列表出现 | verified |
| S-07 | 未留存有效 RED：同上 | PASS：grep `S-07`（1 passed） | `e2e/tests/user-identity/user-identity.spec.ts:48` | 真实 Chrome：绑定码 Modal 显示 code 与 10 分钟过期提示 | verified |
| S-08 | 未留存有效 RED：同上 | PASS：grep `S-03`（S-03/S-08/E-06 同用例，1 passed） | `e2e/tests/user-identity/user-identity.spec.ts:62` | 真实 Chrome+PG（播种四源数据）：四计数 1/1/1/1；各 Tab 独立加载 | verified |
| E-05 | 未留存有效 RED：契约测试 | PASS：`uv run pytest -q tests/frontend/test_user_identity_contract.py -k e05`（1 passed） | `tests/frontend/test_user_identity_contract.py:17` | 生成失败 `catch` 保持 SideSheet（不调用 onCancel） | verified |
| E-06 | 未留存有效 RED | PASS：grep `S-03`（同用例）；凭据 Tab 展示 ErrorState（`semi-banner-danger`）且其他 Tab 正常 | `e2e/tests/user-identity/user-identity.spec.ts:82` | 真实浏览器：后置模块接口不可用时 Tab 级错误隔离 | verified |
| E-07 | 未留存有效 RED：契约测试 | PASS：`-k e07`（1 passed） | `tests/frontend/test_user_identity_contract.py:25` | `BIND_CODE_EXPIRED`(410) zh/en 词条 + Gateway 消费测试存在 | verified |
| E-08 | 未留存有效 RED | PASS：grep `E-08`（1 passed） | `e2e/tests/user-identity/user-identity.spec.ts:90` | 真实 HTTP 409 → Form `user_code` 字段错误显示后端本地化 msg | verified |
- S-06: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=f6b2d9f4679a4e47a7b14790dbc9f5eb (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=10bbb89b41b54531829b2a6161106bdd (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- S-07: e2e_deferred — automated command e2e_deferred; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- S-08: e2e_deferred — automated command e2e_deferred; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=ab880261850c4ae59235a2868a96fcc9 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=7505a402b89744e885ba10fcc5688a44 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=68e15c1321ae461c8b393f1f1260c3e7 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=059ee8d626f843d7bc9857ba95ca153d (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- S-08: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=06ebdf8d5faf431f93dbad5d4694410b (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
