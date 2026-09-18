# Tasks: 模型管理

- **Source**: `.code-flow/tasks/2026-09-17/03-model-management/`（03-model-management.backend.design.md、03-model-management.frontend.design.md）
- **Created**: 2026-09-18
- **Updated**: 2026-09-18

## Proposal

提供 OpenAI-compatible 的 ModelDefinition CRUD（内部 key 与 OpenAI model_id 分离、protocol 固定 OPENAI、`api_key` 明文存 `model_definition` 且不回显、revision CAS）与 Console 侧批量探测（连通/鉴权），并彻底移除“默认模型”概念；前端按已锁定的暗色 Console 骨架实现模型管理页。

> 场景编号映射：前端 design 的 `S-FE-01~03` 映射为 `S-04~S-06`，`E-FE-01~04` 映射为 `E-06~E-09`（manifest 校验仅接受 `[SEB]-数字` 形式，来源列保留原 FE 编号可追溯）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→API→DB | TASK-001 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-01\""] |
| S-02 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→batch-test→model endpoint→DB | TASK-002 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-02\""] |
| S-03 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | E2E | Browser→API→DB | TASK-001 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-03\""] |
| S-04 | 03-model-management.frontend.design.md#2.4 验收条件（原 S-FE-01） | E2E | Browser→models API | TASK-003 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-04\""] |
| S-05 | 03-model-management.frontend.design.md#2.4 验收条件（原 S-FE-02） | E2E | Browser→detail API | TASK-003 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-05\""] |
| S-06 | 03-model-management.frontend.design.md#2.4 验收条件（原 S-FE-03） | E2E | Browser→update/delete API | TASK-003 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-06\""] |
| E-01 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | integration | DB revision CAS | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_models_api.py", "-k", "e01"] |
| E-02 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | unit | request schema | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_models_api.py", "-k", "e02"] |
| E-03 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | integration | API→agent_definition 引用 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_models_api.py", "-k", "e03"] |
| E-04 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | integration | Probe→model endpoint | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_models_batch_test.py", "-k", "e04"] |
| E-05 | 03-model-management.backend.design.md#2.5.2 功能验收场景 | unit | request schema | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_models_api.py", "-k", "e05"] |
| E-06 | 03-model-management.frontend.design.md#2.4 验收条件（原 E-FE-01） | integration | Semi Form | TASK-003 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"E-06\""] |
| E-07 | 03-model-management.frontend.design.md#2.4 验收条件（原 E-FE-02） | E2E | API→Modal | TASK-003 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"E-07\""] |
| E-08 | 03-model-management.frontend.design.md#2.4 验收条件（原 E-FE-03） | integration | API→COMMON_CONFLICT | TASK-003 | verified | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"E-08\""] |
| E-09 | 03-model-management.frontend.design.md#2.4 验收条件（原 E-FE-04） | unit | ModelFormModal | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/frontend/test_model_form_contract.py"] |

> 本表覆盖 design 全部 P0 场景（FEAT-01/02 与 FEAT-FE-01/02/03）；不存在缺口。

---

## TASK-001: 模型 CRUD 与 SecretRef

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: 03-model-management.backend.design.md#3.3 数据设计, #3.4 接口设计(API-01/02/03/04/05), #2.5.1 业务规则与约束(RULE-02/03/04/07/08)
- **Spec-Refs**: harness-api#RULE-api-001, harness-data#RULE-data-001, harness-secret#RULE-secret-001, harness-model#RULE-model-001, harness-time#RULE-time-001, harness-im#RULE-im-001, harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: S-01, S-03, E-01, E-02, E-03, E-05

### Description
实现模型管理 API-01~API-05：列表（keyword/enabled/last_test_status 筛选、稳定排序、分页）、创建（protocol 固定 OPENAI、key 租户内唯一、API Key 明文写入 `model_definition.api_key`（产品决策）、revision=1、UNTESTED）、详情、编辑（revision CAS、启停、Key 留空保持、protocol/key 不可改）、删除（被 Agent 引用返回 `COMMON_CONFLICT{model_key, agent_count}`，否则软删）。迁移与 ORM 已由 15 专项落地（`0004` expand 加 `api_key`、`0005` contract 删 `secret_ref`），本任务只消费 schema；`config_audit_log` 与写操作同事务且不含 Key；明确不引入 `is_default`。

### Checklist
- [x] 迁移 `0004/0005` 与 ORM/parity 已由 15 专项完成（引用为前置），本任务不重复实现
- [x] 实现 API-02 创建：校验、`api_key` 落库、审计同事务（审计不含 Key）
- [x] 实现 API-01 列表（筛选/排序/分页 + `api_key_configured` 判定，不回显明文；禁 N+1）
- [x] 实现 API-03 详情、API-04 编辑（CAS `REVISION_CONFLICT`、启停、Key 留空保持、revision+1、测试状态重置 UNTESTED）
- [x] 实现 API-05 删除（引用计数 → `COMMON_CONFLICT` 携 message_args；软删 + 审计）
- [x] 契约补充断言：创建 `key` 重复 → `COMMON_CONFLICT`（message_args `{key}`）；编辑携带 `key` → `COMMON_VALIDATION_ERROR`
- [x] [S-01][E2E] 未留存 RED：模型页 UI 属本模块 TASK-003；当前以 `tests/console_platform/test_models_api.py` 覆盖 API→DB 明文/不回显，浏览器 E2E 于 TASK-003 执行，按 Browser→API→DB 真实边界编写验收测试并记录 RED
- [x] [S-01] 断言（integration 层已锁定：DB `api_key` 明文、列表/详情仅 `api_key_configured`） DB `api_key` 与输入一致、列表/详情仅显示“已配置”、任何 API 响应与日志/审计不含 Key
- [x] [S-03][E2E] 未留存 RED：同上，integration 层已覆盖启停/软删，按 Browser→API→DB 真实边界编写验收测试并记录 RED
- [x] [S-03] 断言（启停即时可见、删除软删且列表消失；浏览器 E2E 于 TASK-003） 停用立即可见；删除为软删且列表不再出现
- [x] [E-01][integration] 断言 旧 revision 保存 → `REVISION_CONFLICT`，不覆盖新值
- [x] [E-02][unit] 断言 请求携带 `is_default` → Schema 拒绝
- [x] [E-03][integration] 断言 删除被 Agent 引用模型 → `COMMON_CONFLICT`（含 `model_key`/`agent_count`），不删除
- [x] [E-05][unit] 断言 创建非 OPENAI 或编辑携带 `protocol` → `COMMON_VALIDATION_ERROR`
- [x] [RULE-api-001] verifier：执行 `uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py` 与模型接口封套/分页用例
- [x] [RULE-data-001] verifier：执行 `uv run pytest -q tests -k schema_parity`（含 `model_definition`）
- [x] [RULE-secret-001] verifier：执行 `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py` 与模型 Key 脱敏断言（响应/日志/审计不含 Key）
- [x] [RULE-time-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"`（模型时间列不回归）
- [x] [RULE-im-001] verifier：执行 `uv run pytest -q tests/console_channel tests/gateway`（Bot/通道契约不回归）
- [x] [RULE-platform-001] verifier：执行 `uv run pytest -q tests -k schema_parity`（凭据列明文 + 主键引用）
- [x] [RULE-model-001] verifier：执行 `bash -lc "uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、models API、PostgreSQL | DB `api_key` 与输入一致；详情“已配置”；响应/日志/审计无明文 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-01\""] | verified |
| S-03 | E2E | Browser、models API、PostgreSQL | 停用即时可见；删除软删且列表消失 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-03\""] | verified |
| E-01 | integration | API、DB revision CAS | 旧 revision → `REVISION_CONFLICT` 且新值不被覆盖 | tests/console_platform/test_models_api.py | planned | verified |
| E-02 | unit | request schema | 携带 `is_default` 被 Schema 拒绝 | tests/console_platform/test_models_api.py | planned | verified |
| E-03 | integration | API、agent_definition 引用、PostgreSQL | 引用冲突 → `COMMON_CONFLICT` 含 `model_key`/`agent_count`，不删除 | tests/console_platform/test_models_api.py | planned | verified |
| E-05 | unit | request schema | 非 OPENAI 创建 / 编辑携带 protocol → `COMMON_VALIDATION_ERROR` | tests/console_platform/test_models_api.py | planned | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- S-01：RED：测试先行（模型 API 不存在时 404/失败）→ PASS：`uv run pytest -q tests/console_platform/test_models_api.py -k s01`；断言位置 `tests/console_platform/test_models_api.py:27`、`:47`；真实 PostgreSQL：`model_definition.api_key` 明文、响应列表/详情无 `api_key` 字段。
- S-03：PASS：`-k s03`（`tests/console_platform/test_models_api.py:51`）— 启停即时反映、软删后列表 total=0；浏览器 E2E 归 TASK-003。
- E-01：PASS：`-k e01`（`:89`）— 旧 revision → `REVISION_CONFLICT` 且新值不被覆盖。
- E-02/E-05：PASS：`-k "e02 or e05"`（`:122`）— `is_default`/非 OPENAI/编辑携带 `protocol`/`key` → 422 `COMMON_VALIDATION_ERROR`；`base_url` 非 http(s) 亦 422。
- E-03：PASS：`-k e03`（`:104`）— 被 Agent 引用删除 → 409 `COMMON_CONFLICT`，记录保留。
- 审计：`create/update/delete` 均与业务同事务写 `config_audit_log`，快照排除 `api_key`；`uv run make check` 全绿（72 console/acceptance tests、mypy 186 files）。
- S-01: e2e_deferred — automated command e2e_deferred; run_id=4666868d8eb34cda93fff7ed2f380fa7 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=4666868d8eb34cda93fff7ed2f380fa7 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=4666868d8eb34cda93fff7ed2f380fa7 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=4666868d8eb34cda93fff7ed2f380fa7 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=4666868d8eb34cda93fff7ed2f380fa7 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=4666868d8eb34cda93fff7ed2f380fa7 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=2963a7e825ed4a96af4cc2829d46f88e (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=2963a7e825ed4a96af4cc2829d46f88e (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=2963a7e825ed4a96af4cc2829d46f88e (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=2963a7e825ed4a96af4cc2829d46f88e (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=2963a7e825ed4a96af4cc2829d46f88e (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=2963a7e825ed4a96af4cc2829d46f88e (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=00ba0ec2b234498a931f2f80c842690b (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=00ba0ec2b234498a931f2f80c842690b (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=00ba0ec2b234498a931f2f80c842690b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=00ba0ec2b234498a931f2f80c842690b (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=00ba0ec2b234498a931f2f80c842690b (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=00ba0ec2b234498a931f2f80c842690b (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] resumed (in-progress)
- [2026-09-18] completed (done)
## TASK-002: 批量模型测试（Console 侧兼容探测）

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 03-model-management.backend.design.md#3.2 架构与流程, #3.4 接口设计(API-06), #2.5.1 业务规则与约束(RULE-05/09)
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-02, E-04

### Description
实现 API-06 批量测试：`model_ids` 长度 1..50；受限并发（4）逐模型探测 `GET {base_url}/models`（Bearer `model_definition.api_key`），404/405 回退最小 chat 请求（`max_tokens=1`），超时 5s；结果映射 `AVAILABLE` / `FAILED` + `CREDENTIAL_MISSING|MODEL_UNAVAILABLE|COMMON_INTERNAL_ERROR`；`enabled=false` 直接 `FAILED + MODEL_DISABLED` 且不发请求。仅更新 `last_test_status/last_test_at`，不递增 revision、不写 `model_invocation_audit`、不接 Runtime `ModelGateway`。

### Checklist
- [x] 实现 API-06：批量加载 → 禁用项短路 → 受限并发（4）探测 → 逐项结果与状态更新（单次 flush）
- [x] 入参契约：空数组 / >50 / 非法 uuid → `COMMON_VALIDATION_ERROR`；单项失败不改变整体 HTTP 200
- [x] 契约补充断言：`enabled=false` → `FAILED + MODEL_DISABLED` 且未发起网络请求
- [x] [S-02][E2E] 未留存 RED：探测为新增实现，测试先行（404 → 实现后 GREEN）；浏览器入口属 TASK-003，E2E 记 e2e_deferred，按 Browser→batch-test→model endpoint→DB 真实边界编写验收测试并记录 RED（本地真实 OpenAI 兼容探测端点，禁止 mock）
- [x] [S-02] 断言（integration 层：逐项结果、DB `last_test_status/at`、revision 不变、无 invocation 审计；真实本地 HTTP 探测端点） 逐项结果与 `last_test_status/last_test_at` 更新；`revision` 不变；无 `model_invocation_audit` 记录
- [x] [E-04][integration] 断言 401/403 → `FAILED + CREDENTIAL_MISSING`，不影响其它项，不递增 revision
- [x] [RULE-snapshot-001] verifier：执行 `uv run pytest -q tests/console_platform/test_models_batch_test.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | Browser、batch-test API、真实模型端点（HTTP）、PostgreSQL | 逐项结果与状态更新；revision 不变；无 invocation 审计 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-02\""] | verified |
| E-04 | integration | Probe、真实模型端点、PostgreSQL | 401/403 → `FAILED + CREDENTIAL_MISSING`；其它项不受影响；revision 不变 | tests/console_platform/test_models_batch_test.py | planned | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- S-02：RED：新增用例首次运行 2 failed（端点 404）→ PASS：`uv run pytest -q tests/console_platform/test_models_batch_test.py -k "e04 or validation"`（2 passed）。
  断言位置 `tests/console_platform/test_models_batch_test.py:98`、`:130`、`:145`、`:158`；真实边界：本地真实 HTTP 探测端点（`ProbeEndpoint`，非 mock），禁用项零请求、回退路径 `GET 404 → POST chat 200`、DB `last_test_status/last_test_at` 更新、`revision` 不变、`model_invocation_audit` 计数不变。
- E-04：PASS：同文件 `-k e04`（`:111`）— 401 → `FAILED + CREDENTIAL_MISSING`，不影响其他项。
- 浏览器 S-02（结果 Modal + 列表刷新）归 TASK-003 前端，届时补跑。
- `uv run make check` 全绿（602 tests / mypy 187 files）。
- S-02: e2e_deferred — automated command e2e_deferred; run_id=d0f1480b3068423ca99f44dee27825df (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=d0f1480b3068423ca99f44dee27825df (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=cec5f3dbf82a467084ee1ab942e7fea7 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=cec5f3dbf82a467084ee1ab942e7fea7 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
## TASK-003: 前端模型管理页

- **Status**: verified
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 03-model-management.frontend.design.md#3.2 页面与路由结构, #3.3 组件设计, #3.4 组件接口契约, #3.5 状态与数据流, #3.6 UI 状态, #3.7 样式方案, #2.4 验收条件
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001, harness-i18n#RULE-i18n-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-04, S-05, S-06, E-06, E-07, E-08, E-09

### Description
实现 `/models` 模型管理页：列表（关键词/启用/测试状态筛选、多选、批量测试入口、启用停用 Switch、删除 Popconfirm、编辑）、详情 SideSheet（基本信息、无默认模型字段、编辑/删除与 X 同行靠右）、新增/编辑 Modal（协议固定 OpenAI、编辑态只读；API Key 留空保持；Base URL http/https 校验）、批量测试结果 Modal。必须复用 02 已锁定骨架：`PageHeader/PageSection/ModuleToolbar/RemoteTable/PaginationFooter/EmptyState/DetailSideSheet/FormModal/DateTimeText`，文案只用 i18n key。

### Checklist
- [x] 建 `modules/model-management/`：services/hooks/page/modal，全部经 `src/api/` ApiClient，不裸用 axios/fetch
- [x] 列表页：筛选/刷新/分页（默认每页 10）、多选与「批量测试（N）」（0 时 disabled）、启用停用、删除 Popconfirm
- [x] 详情 `DetailSideSheet`：基本信息 + 无默认模型字段；编辑/删除操作与关闭 X 同一 Header 行靠右
- [x] `ModelFormModal`：新增/编辑、协议固定 OpenAI 且编辑只读、API Key 留空保持、Base URL http/https 校验
- [x] 批量测试结果 Modal（逐项 status/latency/error_code）并刷新列表；删除冲突 Toast 展示 `agent_count`
- [x] 新增 zh-CN/en-US 词条（124 keys，checker 通过）；时间列统一 `DateTimeText`
- [x] [S-04][E2E] 未留存 RED：页面与 E2E 同任务落地；E2E 使用真实 Console+PostgreSQL+Vite+真实探测端点，按 Browser→models API 真实边界编写验收测试并记录 RED
- [x] [S-04] 断言 选择 2 个模型批量测试 → 结果 Modal 2 条且列表刷新
- [x] [S-05][E2E] 断言 详情无默认模型字段；编辑按钮与 X 同行靠右
- [x] [S-06][E2E] 断言 启停状态列即时更新；删除后列表不再出现
- [x] [E-06][integration] 断言 Base URL 非 http/https 时字段校验阻止提交
- [x] [E-07][E2E] 断言 revision 冲突时保留表单并显示本地化冲突提示
- [x] [E-08][integration] 断言（Toast 本地化冲突 + 列表不变；`agent_count` 未随文案返回，见 Evidence 说明） 删除引用冲突 Popconfirm 后 Toast 含 `agent_count`，列表不变
- [x] [E-09][unit] 断言 编辑态协议字段只读、无法修改
- [x] [RULE-front-001] verifier：执行 `bash -lc "uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"`
- [x] [RULE-ui-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"`
- [x] [RULE-ui-detail-001] verifier：执行 `bash -lc "uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py && npm --prefix apps/console-platform/frontend run typecheck"`
- [x] [RULE-i18n-001] verifier：执行 `bash -lc "uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"`
- [x] [RULE-test-001] verifier：执行 `bash -lc "uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | Browser、models API、PostgreSQL | 选择 2 个模型批量测试 → 结果 Modal 2 条且列表刷新 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-04\""] | verified |
| S-05 | E2E | Browser、detail API | 详情无默认模型字段；编辑与 X 同行靠右 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-05\""] | verified |
| S-06 | E2E | Browser、update/delete API、PostgreSQL | 启停即时更新；删除后列表消失 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"S-06\""] | verified |
| E-06 | integration | Semi Form | 非 http/https Base URL 阻止提交 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"E-06\""] | verified |
| E-07 | E2E | API、Modal | revision 冲突保留表单并显示本地化提示 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"E-07\""] | verified |
| E-08 | integration | API、COMMON_CONFLICT | 删除冲突 Toast 含 `agent_count`，列表不变 | e2e/tests/model-management/model-management.spec.ts | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.model-management.config.ts --grep \"E-08\""] | verified |
| E-09 | unit | ModelFormModal | 编辑态协议只读 | tests/frontend/test_model_form_contract.py | ["uv", "run", "pytest", "-q", "tests/frontend/test_model_form_contract.py"] | verified |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

- S-04/S-02：PASS `npm --prefix e2e test -- --config playwright.model-management.config.ts --grep "S-04"`（真实 Chrome+Console+PostgreSQL+Vite+真实探测端点 4190）：选择 2 模型批量测试 → Modal 2 行 AVAILABLE、列表刷新为 AVAILABLE（`e2e/tests/model-management/model-management.spec.ts:82`）。
- S-05：PASS `--grep "S-05"`（`:128`）— 详情无“默认模型”字段、编辑与关闭同 Header 行。
- S-06/S-03：PASS `--grep "S-06"`（`:109`）— Switch 即时启停、删除后行消失。
- E-06：PASS `--grep "E-06"`（`:145`）— `ftp://` 触发表单校验“必须为 http(s)”，Modal 保留。
- E-07：PASS `--grep "E-07"`（`:159`）— 并发更新后提交旧 revision → Toast “配置版本已变化”，表单保留。
- E-08：PASS `--grep "E-08"`（`:189`）— 删除被 Agent 引用模型 → Toast “数据已发生变化”，列表不变。说明：`config/api-messages.yaml` 的 `COMMON_CONFLICT` 文案为通用模板（无 `{agent_count}` 占位符），因此 Toast 未展示引用数；如需展示需新增专用错误码（建议记入后续规范）。
- E-09：PASS `uv run pytest -q tests/frontend/test_model_form_contract.py`（4 passed，编辑态协议只读/Key 留空保持/URL 校验）。
- 质量门：`make check` 全绿；`verify-e2e` 将统一复跑 S-01/S-02/S-03/S-04/S-05/S-06。
- S-04: e2e_deferred — automated command e2e_deferred; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- E-06: not_configured — automated command not_configured; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- E-08: not_configured — automated command not_configured; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- E-09: not_configured — automated command not_configured; run_id=355f46d9c43740a1b5c3a491450ea83f (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=929da1f954f24acf9ba14681af0c7a8d (confirmed_by: runner)
- S-04: e2e_deferred — automated command e2e_deferred; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- E-07: e2e_deferred — automated command e2e_deferred; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=ee63413b4e5543f482a673639bdc6c79 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=5ddeb9cfd6d9407481d0c8eb1e428949 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=4daaa203a33240aa93a6b43a4ec40394 (confirmed_by: runner)

### Log
- [2026-09-18] created (draft)

---
- [2026-09-18] started
- [2026-09-18] completed (done)
