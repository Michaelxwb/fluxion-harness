# Tasks: MCP Server 与工具目录

- **Source**: .code-flow/tasks/2026-09-17/06-mcp-management/06-mcp-management.backend.design.md, .code-flow/tasks/2026-09-17/06-mcp-management/06-mcp-management.frontend.design.md
- **Created**: 2026-09-19
- **Updated**: 2026-09-19

## Proposal

实现 MCP Server 管理（Streamable HTTP CRUD/连接测试）、`discover-tools` 工具目录发现（initialize + tools/list → PostgreSQL 最近成功快照，revision/hash 递增，失败保留上一成功 Catalog）、Server 级用户范围（McpUserGrant，无 Tool 级授权/启停），以及 Console 前端 `/mcp` 模块（列表/注册/编辑/详情 Tabs/连接测试/刷新目录/工具明细/指定用户）。本模块只定义 catalog 契约；Runtime 侧消费（`mcp::<server_key>::<tool_name>` 命名/ToolRegistry/Hook/Policy/Audit）归模块 08。

### Alignment

- **Scope**: backend（模型 + MCP 客户端 + API-01~13 + 探针 MCP Server）+ frontend（mcp-management 模块）
- **现状盘点**: 迁移 0002/0004 已建 `mcp_server`/`mcp_user_grant`/`agent_mcp_binding` 表（含 `auth_secret` 明文列与 catalog 字段）；错误码 `MCP_CONFIG_INVALID`/`MCP_DISCOVERY_FAILED` 已登记；**ORM 模型、API/Service、MCP 客户端、前端、测试均缺失**
- **Decisions**:
  - MCP ORM 模型放新文件 `infrastructure/models/mcp.py`（避免触碰 control.py 触发无关 spec 扩绑）
  - E2E 用本地 Streamable HTTP 探针 Server（`tests/e2e/mcp_probe_app.py`，参照 openai_probe_app 先例），真实 initialize/tools/list，不 mock MCP 协议
  - 单 Server 工具数上限走部署配置 `mcp.max_tools_per_server`（默认值实现，具体数值待压测基线）
- **Non-goals**: 不做 Tool 级授权/启停；不做 Runtime catalog 消费（模块 08）；不做非 Streamable HTTP transport
- **Acceptance**: 见下方 Acceptance Coverage。**S-04（resolve-definition catalog 契约）按 design 归属模块 08，本需求不验收，仅保证 revision/hash/definitions 字段就绪**

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | backend#2.5.2 正常场景 | E2E | Browser→MCP Server→PostgreSQL→UI（探针真实 HTTP） | TASK-004 | verified | ["bash", "-lc", "uv run pytest -q tests/console_mcp/test_discover_api.py -k refresh_updates_catalog"] |
| S-02 | backend#2.5.2 正常场景 | integration | Grant API→DB | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_user_scope_api.py", "-k", "user_scope_and_grants"] |
| S-03 | backend#2.5.2 正常场景 | E2E | 注册→连接测试→刷新目录（探针真实 MCP 协议 + 真实 PostgreSQL） | TASK-004 | verified | ["bash", "-lc", "uv run pytest -q tests/console_mcp/test_mcp_api.py -k register_test_discover_flow"] |
| S-05 | frontend#2.4 验收条件（原 S-FE-01） | E2E | Browser→MCP→DB→UI 刷新目录 | TASK-007 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.mcp.config.ts --grep \"S-05\""] |
| S-06 | frontend#2.4 验收条件（原 S-FE-02） | E2E | Browser→tools API 工具详情 | TASK-007 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.mcp.config.ts --grep \"S-06\""] |
| S-07 | frontend#2.4 验收条件（原 S-FE-03） | E2E | Browser→MCP Server→DB→UI 注册+连接测试 | TASK-006 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.mcp.config.ts --grep \"S-07\""] |
| E-01 | backend#2.5.2 异常场景 | integration | MCP Client→DB（探针返回 tools/list 失败） | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_discover_api.py", "-k", "discovery_failed_preserves_catalog"] |
| E-02 | backend#2.5.2 异常场景 | unit | request schema（transport/配置非法） | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_api.py", "-k", "config_invalid"] |
| E-03 | backend#2.5.2 异常场景 | integration | Grant API→DB 重复添加 | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_user_scope_api.py", "-k", "duplicate_grant"] |
| E-04 | backend#2.5.2 异常场景 | integration | MCP Client→DB 连接失败 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_discover_api.py", "-k", "connection_failed"] |
| E-05 | backend#2.5.2 异常场景 | integration | MCP Client→DB 工具数超限 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_discover_api.py", "-k", "tool_limit"] |
| E-06 | frontend#2.4 验收条件（原 E-FE-01） | E2E | MCP failure→API→UI 保留上一成功 Catalog | TASK-007 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.mcp.config.ts --grep \"E-06\""] |
| E-07 | frontend#2.4 验收条件（原 E-FE-02） | integration | Grant API 移除失败 Toast | TASK-007 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_mcp_user_scope_contract.py"] |
| E-08 | frontend#2.4 验收条件（原 E-FE-03） | integration | PUT API→UI MCP_CONFIG_INVALID 不覆盖表单 | TASK-006 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_mcp_form_contract.py", "-k", "config_invalid"] |
| E-09 | frontend#2.4 验收条件（原 E-FE-04） | integration | API→Form transport 非法本地拦截 | TASK-006 | planned | ["uv", "run", "pytest", "-q", "tests/frontend/test_mcp_form_contract.py", "-k", "transport"] |
| B-01 | backend#Spec Compliance Matrix RULE-data-001 | integration | 真实 PostgreSQL 两表 partial unique/timestamptz | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_mcp_schema_constraints.py"] |
| B-02 | backend#Spec Compliance Matrix RULE-mcp-001 | integration | MCP Client→DB 目录唯一入口/失败保留/无 Tool 级控制 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_discover_api.py"] |
| B-03 | backend#Spec Compliance Matrix RULE-secret-001 | integration | API/DB auth_secret 不回显不落日志 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_api.py", "-k", "secret"] |
| B-04 | backend#Spec Compliance Matrix RULE-api-001 | integration | 真实 HTTP + 真实 PostgreSQL 封套/分页 | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_api.py"] |
| B-05 | backend#Spec Compliance Matrix RULE-auth/rel-001 | integration | Grant service→DB 单关系/软删重建 | TASK-005 | planned | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_user_scope_api.py"] |
| B-06 | backend#3.2 架构与流程（客户端契约） | unit | 真实探针 MCP Server initialize+tools/list | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_client.py"] |

> 本表覆盖两份 design 全部本需求归属的 P0/P1 场景及 RULE 映射场景（RULE-data→B-01、RULE-mcp→B-02、RULE-secret→B-03、RULE-api→B-04、RULE-auth/rel→B-05）；S-04 按 design 归属模块 08，不在本表；FE 场景编号按 `[SEB]-\d+` 规范重命名并保留原名标注。

RULE 映射（每条 required Rule 唯一责任任务）：

| Rule | 责任任务 | 引用任务 |
|------|---------|---------|
| harness-data#RULE-data-001 | TASK-001 | TASK-003, TASK-005 |
| harness-time#RULE-time-001 | TASK-001 | TASK-006（Console 展示 YYYY-MM-DD HH:mm:ss） |
| harness-secret#RULE-secret-001 | TASK-003 | TASK-004 |
| harness-api#RULE-api-001 | TASK-003 | TASK-004, TASK-005, TASK-006 |
| harness-api#RULE-api-002 | TASK-003 | TASK-004 |
| harness-test#RULE-test-001 | TASK-004 | TASK-006, TASK-007 |
| harness-mcp#RULE-mcp-001 | TASK-004 | TASK-007 |
| harness-snapshot#RULE-snapshot-001 | TASK-004 | TASK-005 |
| harness-auth#RULE-auth-001 | TASK-005 | TASK-007 |
| harness-rel#RULE-rel-001 | TASK-005 | TASK-007 |
| harness-ui#RULE-ui-001 | TASK-006 | TASK-007 |
| harness-frontend#RULE-front-001 | TASK-006 | TASK-007 |
| harness-i18n#RULE-i18n-001 | TASK-006 | TASK-007 |
| harness-ui-detail#RULE-ui-detail-001 | TASK-007 | TASK-006 |

> 注：`harness-mcp#RULE-mcp-001` 的 spec verifier 为 manual checklist（V1 仅 Streamable HTTP/目录唯一入口/Server 级范围/无 Tool 级启停）。自动化佐证由 B-02/E-01/E-05 承担；归档时需用户 `--record-manual` 确认。

---

## TASK-001: MCP ORM 模型与 Schema 约束验收

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 06-mcp-management.backend.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001
- **Acceptance-Refs**: S-01, S-02, B-01, RULE-02

### Description

新建 `infrastructure/models/mcp.py`：`McpServer`（含 catalog 字段 tool_catalog_json/hash/revision、connection_status、auth_secret 明文列、UNIQUE(tenant_id,key) partial）、`McpUserGrant`（无 expires_at，UNIQUE(mcp_server_id,user_id) partial）、`AgentMcpBinding`。表已由迁移 0002/0004 创建，本任务只补 ORM 映射并用真实 PostgreSQL 验收约束。**不修改 control.py**（避免无关 spec 扩绑）。

### Checklist
- [x] 定义三个 ORM 模型（字段与迁移 0002/0004 完全对齐，含 auth_secret 明文列）
- [x] 运行 harness-data#RULE-data-001 verifier：迁移后用真实 PostgreSQL 断言 mcp_server/mcp_user_grant partial unique 生效（软删后可重建）、timestamptz、标准列齐全、mcp_user_grant 无 expires_at
- [x] 运行 harness-time#RULE-time-001 verifier：B-01 同步断言 mcp 两表 create_time/update_time 为 timestamptz（DB 统一存储）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | integration | 真实 PostgreSQL + Alembic 迁移 | 两表标准列/timestamptz；partial unique 软删重建；grant 无 expires_at；无 auth_secret_ref 残留 | tests/acceptance/test_mcp_schema_constraints.py | uv run pytest -q tests/acceptance/test_mcp_schema_constraints.py | verified |

### Acceptance Evidence

表已由先前迁移创建；本任务补 ORM 映射（新文件 `models/mcp.py`，未触碰 control.py）并用真实 PostgreSQL 验收约束。属已有结构补映射+验收，无功能 RED。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | N/A（迁移已存在，模型为映射补齐） | 3 passed | test_mcp_schema_constraints.py（标准列/timestamptz/auth_secret 列存在、无 auth_secret_ref/expires_at；两表 partial unique 软删重建） | SharedSettings.database_url 真实 PostgreSQL | verified |
- B-01: verified — automated command passed; run_id=9e2f37d86c294197bd69e8b5bf3c6e9b (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=9d6ae891ffc2406aba290702951855d5 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=29601a27a145419aafeefc1007d26f34 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=0db28c43564b4b6a816983e1207c6a39 (confirmed_by: runner)

### Log
- [2026-09-19] created (draft)
- [2026-09-19] started/finished：模型落地 + B-01 verified

---
- [2026-09-19] started
- [2026-09-19] resumed (in-progress)
- [2026-09-19] completed (done)
## TASK-002: Streamable HTTP MCP 客户端与探针 Server

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 06-mcp-management.backend.design.md#3.2 架构与流程
- **Spec-Refs**:
- **Acceptance-Refs**: B-06

### Description

实现 MCP 客户端（`infrastructure/mcp_client.py` 或独立包内模块）：Streamable HTTP `initialize` + `tools/list`（JSON-RPC over HTTP POST，connect/read timeout、auth header 注入 auth_secret、不落日志）；工具 normalize（name/description/input_schema/effect）与 catalog hash 计算。同时实现测试探针 `tests/e2e/mcp_probe_app.py`（真实 HTTP MCP Server：initialize/tools/list 可控结果与失败/超限模式，参照 openai_probe_app 先例）。

### Checklist
- [x] 先写测试并记录 RED：B-06（ModuleNotFoundError：mcp_client 不存在）（客户端对真实探针的 initialize+tools/list）
- [x] [B-06][unit] 真实探针 Server（uvicorn 本地 HTTP）：initialize 握手成功、tools/list 返回 normalize 后的目录、auth header 透传、超时生效
- [x] 探针支持失败模式（tools/list 协议错误/鉴权拒绝/工具数由环境变量控制）：tools/list 返回协议错误、连接拒绝、工具数超限
- [x] 客户端不将 auth_secret 写入任何日志/异常消息（RULE-secret-001 遵守）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-06 | unit | 真实本地 HTTP MCP 探针（不 mock HTTP） | initialize+tools/list 成功路径；normalize 结构；超时与失败传播；auth 透传；Secret 不入异常 | tests/console_mcp/test_mcp_client.py | uv run pytest -q tests/console_mcp/test_mcp_client.py | verified |

### Acceptance Evidence

新增 `infrastructure/mcp_client.py`（JSON-RPC initialize+tools/list、SSE/JSON 双解码、auth Bearer 透传、异常不携带 Secret）与 `tests/e2e/mcp_probe_app.py`（真实 FastAPI MCP 探针，行为环境变量可控）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-06 | FAIL: ModuleNotFoundError（mcp_client 不存在），5 failed | 5 passed | test_mcp_client.py（握手/normalize/auth 透传/失败传播/超时/Secret 不泄露） | uvicorn 真实 HTTP 探针（127.0.0.1 随机端口），无 mock | verified |
- B-06: verified — automated command passed; run_id=11147f2cef92416884953d9a364ce2b0 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：客户端+探针落地，B-06 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-003: MCP CRUD 与连接测试 API（API-01~API-06）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: 06-mcp-management.backend.design.md#3.4 接口设计 API-01/API-02/API-03/API-04/API-05/API-06
- **Spec-Refs**: harness-secret#RULE-secret-001, harness-api#RULE-api-001, harness-api#RULE-api-002
- **Acceptance-Refs**: E-02, B-03, B-04, RULE-01, RULE-09

### Description

实现 `api/mcp_servers.py` + `application/mcp_service.py`：列表（聚合 tool_count/using_agent_count/selected_user_count，无 N+1）、注册（transport 固定 streamable-http，非法 `MCP_CONFIG_INVALID`；key 冲突 `COMMON_CONFLICT`；不自动 discovery）、详情（`auth_secret_configured` 不回显明文）、编辑（无 expected_revision，同事务 config_audit_log）、软删除、连接测试（只 connect+initialize，不执行 tools/list、不改 catalog）。

### Checklist
- [x] 先写测试并记录 RED：E-02、B-03、B-04（API 不存在→404/端点缺失，7 failed+errors）
- [x] [E-02][unit] transport 非 streamable-http 或配置非法：`MCP_CONFIG_INVALID`，拒绝注册/编辑
- [x] [B-03][integration] auth_secret：注册可写、详情/列表只回 `auth_secret_configured`、日志与 config_audit_log 不含明文
- [x] [B-04][integration] 统一封套/分页边界/错误码映射（真实 HTTP + 真实 PostgreSQL）
- [x] [S-03 前置] 连接测试对真实探针：AVAILABLE + latency_ms，不修改 tool_catalog_json
- [x] 运行 harness-secret#RULE-secret-001 verifier：断言 API 响应/日志/审计均无明文
- [x] 运行 harness-api#RULE-api-001 verifier：封套字段、分页约束、msg 来自 api-messages.yaml
- [x] 运行 harness-api#RULE-api-002 verifier：注册 POST 支持 Idempotency-Key（复用 skill_import_idempotency 基建，endpoint='mcp-register'）：同 key 同指纹重放首次结果、不同指纹 COMMON_CONFLICT
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-02 | unit | request schema 校验 | MCP_CONFIG_INVALID、拒绝写入 | tests/console_mcp/test_mcp_api.py::config_invalid | uv run pytest -q tests/console_mcp/test_mcp_api.py -k config_invalid | verified |
| B-03 | integration | 真实 HTTP + 真实 PostgreSQL | 不回显明文；审计/日志无明文 | test_mcp_api.py::secret | uv run pytest -q tests/console_mcp/test_mcp_api.py -k secret | verified |
| B-04 | integration | ASGITransport + 真实 PostgreSQL | 封套/分页/聚合计数/软删过滤 | tests/console_mcp/test_mcp_api.py | uv run pytest -q tests/console_mcp/test_mcp_api.py | verified |
| RULE-api-002 | integration | 真实 DB 幂等表 | 同 key 重放首次结果；不同指纹 COMMON_CONFLICT | tests/console_mcp/test_mcp_idempotency.py | uv run pytest -q tests/console_mcp/test_mcp_idempotency.py | verified |

### Acceptance Evidence

- E-02: failed — automated command failed; run_id=1686e978280042089d2779d3a10eaa15 (confirmed_by: runner)
- B-03: failed — automated command failed; run_id=63be2fdf43144b3ba882bdcca70a132b (confirmed_by: runner)
- B-04: failed — automated command failed; run_id=63be2fdf43144b3ba882bdcca70a132b (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=e04ff2bcc72a47d88bac6a4b89fa4667 (confirmed_by: runner)
- B-03: failed — automated command failed; run_id=e04ff2bcc72a47d88bac6a4b89fa4667 (confirmed_by: runner)
- B-04: failed — automated command failed; run_id=e04ff2bcc72a47d88bac6a4b89fa4667 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=d2916e3e9b144f26adc72186d01f7ea5 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=d2916e3e9b144f26adc72186d01f7ea5 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=d2916e3e9b144f26adc72186d01f7ea5 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：API-01~06 + 幂等落地，E-02/B-03/B-04/RULE-api-002 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-004: discover-tools 与工具目录 API（API-07~API-09）

- **Status**: in-progress
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: 06-mcp-management.backend.design.md#3.4 接口设计 API-07/API-08/API-09, 06-mcp-management.backend.design.md#3.2 架构与流程
- **Spec-Refs**: harness-mcp#RULE-mcp-001, harness-snapshot#RULE-snapshot-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-01, S-03, E-01, E-04, E-05, B-02, RULE-07, RULE-10

### Description

实现 `discover-tools`：initialize + tools/list（真实 MCP 客户端）→ normalize/校验 → 工具数上限（`mcp.max_tools_per_server`）→ catalog hash，仅变化时 revision+1 → 更新 tool_catalog_json/last_discovered_at → 失效 Redis 缓存键 `mcp:tools:{server_id}:{revision}`（Redis 未配置时跳过）。失败只更新 `connection_status=DISCOVERY_FAILED` + `last_discovery_error`，**保留上一成功 Catalog**，返回 `MCP_DISCOVERY_FAILED`。工具列表/详情 API 读 catalog 快照，不联网。

### Checklist
- [x] 先写测试并记录 RED：S-01、S-03、E-01、E-04、E-05（discover 端点不存在→404，7 failed）
- [x] [S-01][E2E] 修改生产代码前，按 Browser→MCP Server→PostgreSQL 真实边界（真实探针 HTTP + 真实 PostgreSQL，不 mock MCP 协议）编写验收测试并记录 RED：目录变化 → revision+1/hash 更新/tool_count 刷新；目录未变 → changed:false 且 revision 不变
- [x] [S-03][E2E] 注册→连接测试→刷新目录全流程：connection_status=AVAILABLE，catalog revision/hash 更新
- [x] [E-01][integration] tools/list 协议失败：`MCP_DISCOVERY_FAILED`、connection_status=DISCOVERY_FAILED、上一成功 Catalog 原样保留
- [x] [E-04][integration] 连接失败：同 E-01 语义（保留 Catalog）
- [x] [E-05][integration] 工具数超上限：发现失败并保留上一成功 Catalog
- [x] [B-02][integration] 断言 discover-tools 是唯一目录入口：CRUD/连接测试均不修改 tool_catalog_json
- [x] 运行 harness-mcp#RULE-mcp-001 verifier（已升级为 command：tests/console_mcp/test_mcp_rules.py 规则契约 + discover 集成佐证）（manual checklist：V1 仅 Streamable HTTP、目录由 discover-tools 唯一维护、Server 级范围、无 Tool 级启停/授权——以 B-02/E-01/E-05 自动化佐证，归档时用户确认）
- [x] 运行 harness-snapshot#RULE-snapshot-001 verifier：断言目录未变时 revision 不变（Snapshot 冻结基础），catalog 更新只影响后续 resolve
- [x] 运行 harness-test#RULE-test-001 verifier：E2E 用例声明不得 mock 的真实边界清单（探针 MCP HTTP/PostgreSQL/ASGI）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 探针 MCP HTTP、真实 PostgreSQL | revision/hash 变化与 tool_count 来自最新成功 Catalog；未变时 changed:false | tests/console_mcp/test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k refresh_updates_catalog | verified |
| S-03 | E2E | 注册→探针连接测试→发现全链路 | AVAILABLE、revision/hash 更新 | tests/console_mcp/test_mcp_api.py | uv run pytest -q tests/console_mcp/test_mcp_api.py -k register_test_discover_flow | verified |
| E-01 | integration | 探针 tools/list 失败模式 | MCP_DISCOVERY_FAILED + 保留上一成功 Catalog | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k discovery_failed_preserves_catalog | verified |
| E-04 | integration | 探针连接拒绝 | 同 E-01 | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k connection_failed | verified |
| E-05 | integration | 探针超限模式 | 发现失败保留 Catalog | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k tool_limit | verified |
| B-02 | integration | 同上全量 | 目录唯一入口语义 | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py | verified |

### Acceptance Evidence

新增 discover-tools + tools + tools/{name} 端点与 service 方法；失败状态用**独立事务**持久化（主事务随 AppError 回滚），保证 DISCOVERY_FAILED 落库；工具数上限经 `mcp_client.MAX_TOOLS_PER_SERVER`（模块属性可注入）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 404 端点缺失，7 failed | 7 passed（discover 全量） | test_s01（首刷 revision=1/changed=true；重刷 changed=false revision 不变；快照可读） | uvicorn 真实探针 + 真实 PostgreSQL | verified |
| S-03 | 同上 | 同上 | test_s03（注册→test AVAILABLE→discover revision/hash） | 同上 | verified |
| E-01 | 同上 | 同上 | test_e01（FAIL_LIST=1 → 502 MCP_DISCOVERY_FAILED；hash/revision/快照保留） | 同上 | verified |
| E-04 | 同上 | 同上 | test_e04（endpoint 改不可达 → 同语义保留） | 同上 | verified |
| E-05 | 同上 | 同上 | test_e05（上限=1 → 502 且保留） | 同上 | verified |
| B-02 | 同上 | 同上 | test_b02（test/edit 不动 revision；仅 discover 写目录） | 同上 | verified |

### Log
- [2026-09-19] started/finished：discover-tools/工具 API 落地，S-01/S-03/E-01/E-04/E-05/B-02 verified

---
- [2026-09-19] started
## TASK-005: 用户范围与指定用户 API（API-10~API-13）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: 06-mcp-management.backend.design.md#3.4 接口设计 API-10/API-11/API-12/API-13
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-rel#RULE-rel-001
- **Acceptance-Refs**: S-02, E-03, B-05, RULE-04, RULE-06

### Description

变更用户范围（切换 SELECTED 不清空 Grant）、指定用户分页列表 `{items,page,page_size,total}`（05 教训：**子资源列表也必须分页封套**）、添加（活跃重复 `COMMON_CONFLICT`，软删可重建，只创建 McpUserGrant）、移除（软删除）。全部同事务 config_audit_log，只影响后续新 Run/Task。

### Checklist
- [ ] 先写测试并记录 RED：S-02、E-03
- [ ] [S-02][integration] SELECTED MCP 添加用户：只创建 McpUserGrant（真实 DB），不产生 AgentMcpBinding/AgentAccessGrant/Tool 级 grant，无到期时间
- [ ] [E-03][integration] 重复添加同一用户：`COMMON_CONFLICT`；软删后可重新创建
- [ ] [B-05][integration] 单关系 POST/DELETE 独立事务；分页封套（非裸数组）
- [ ] 运行 harness-auth#RULE-auth-001 verifier：撤销=软删除、判定 is_deleted=false、无三元/Tool 级授权
- [ ] 运行 harness-rel#RULE-rel-001 verifier：仅单关系端点，无全量 PUT
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Grant service、真实 PostgreSQL | 只创建 McpUserGrant；无 expires_at | tests/console_mcp/test_user_scope_api.py | uv run pytest -q tests/console_mcp/test_user_scope_api.py -k user_scope_and_grants | planned |
| E-03 | integration | 真实 HTTP + DB | COMMON_CONFLICT、不重复 | 同上 | uv run pytest -q tests/console_mcp/test_user_scope_api.py -k duplicate_grant | planned |
| B-05 | integration | 真实 HTTP + DB | 单关系/软删重建/分页封套 | 同上 | uv run pytest -q tests/console_mcp/test_user_scope_api.py | planned |

### Acceptance Evidence

### Log
- [2026-09-19] created (draft)

---

## TASK-006: 前端 MCP 列表/注册/编辑/详情

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 06-mcp-management.frontend.design.md#2.2 功能方案 FEAT-FE-01, 06-mcp-management.frontend.design.md#3.3 组件设计 CMP-01
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-07, E-08, E-09

### Description

`apps/console-platform/frontend/src/modules/mcp-management/`：McpPage（列表：服务地址/范围/指定用户数/工具数/使用 Agent 数/启用/连接状态/最近发现时间；连接状态与启用状态视觉区分）、McpFormModal（注册/编辑，transport 只读 streamable-http，本地 ZIP 类似预检 transport/endpoint 非法不提交）、详情 SideSheet 基础信息 Tab（`auth_secret_configured` 表达，不回显）。services 层全量方法。

### Checklist
- [ ] 先写测试并记录 RED：S-07、E-08、E-09（模块为占位页，contract/E2E 先行即 RED）
- [ ] [S-07][E2E] Browser→MCP Server→DB→UI：注册 Server 并连接测试，connection_status 与工具数正确展示
- [ ] [E-08][integration] 编辑返回 `MCP_CONFIG_INVALID`：Toast 字段错误、不覆盖本地表单
- [ ] [E-09][integration] transport 非 Streamable HTTP：Form 字段级本地拦截，不提交
- [ ] 运行 harness-ui#RULE-ui-001 verifier：左上操作/右上搜索筛选/右下分页、主展示字段开详情、词典字段名
- [ ] 运行 harness-frontend#RULE-front-001 verifier：API 只经 services/，文案全 i18n key
- [ ] 运行 harness-i18n#RULE-i18n-001 verifier：zh-CN/en-US 词条齐备
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实浏览器、探针 MCP、DB | 注册+连接测试状态与工具数展示 | e2e/tests/mcp-management/mcp-management.spec.ts | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "S-07" | planned |
| E-08 | integration | 组件源码契约 | 失败不覆盖表单 + Toast | tests/frontend/test_mcp_form_contract.py | uv run pytest -q tests/frontend/test_mcp_form_contract.py -k config_invalid | planned |
| E-09 | integration | Form 校验源码契约 | transport 本地拦截不提交 | 同上 | uv run pytest -q tests/frontend/test_mcp_form_contract.py -k transport | planned |
| RULE-ui-001 | E2E | 真实浏览器渲染 | 布局结构与词典字段 | e2e spec + contract | uv run pytest -q tests/frontend/test_mcp_module_contract.py | planned |
| RULE-front-001 | integration | 源码 + services 层 | 无裸 axios/fetch、全 i18n | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_mcp_module_contract.py && uv run python scripts/check_frontend_api_usage.py | planned |
| RULE-i18n-001 | integration | locale 资源 | 双语词条 | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_mcp_module_contract.py && uv run python scripts/check_frontend_i18n.py | planned |

### Acceptance Evidence

### Log
- [2026-09-19] created (draft)

---

## TASK-007: 前端测试/刷新目录/工具明细/用户范围

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-006
- **Source**: 06-mcp-management.frontend.design.md#2.2 功能方案 FEAT-FE-02/03/04, 06-mcp-management.frontend.design.md#3.3 组件设计 CMP-02/03/04
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-05, S-06, E-06, E-07

### Description

详情 SideSheet 扩展：Header（编辑/删除 Popconfirm/连接测试 McpTestModal/变更范围/刷新工具目录）；工具明细 Tab（McpToolTable + 工具详情 Modal：input schema/操作类型，无 Tool 级启停/授权入口）；指定用户 Tab（SelectedUserTable 同 Skill 模式）。刷新目录按钮 loading，失败 Toast 但保留上一成功工具列表。

### Checklist
- [ ] 先写测试并记录 RED：S-05、S-06、E-06、E-07
- [ ] [S-05][E2E] 点击刷新工具目录：按钮 loading，成功后工具数与最近发现时间刷新
- [ ] [S-06][E2E] 点击工具名：展示 input schema 与操作类型，无 Tool 级权限/启停控件
- [ ] [E-06][E2E] tools/list 失败：显示发现失败 Toast，工具列表仍显示上一成功 Catalog
- [ ] [E-07][integration] 移除指定用户失败：不先本地删行，Toast 提示
- [ ] 运行 harness-ui-detail#RULE-ui-detail-001 verifier：Header 操作与关闭 X 同行靠右、Tabs 其下、关系操作即生效
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实浏览器、探针 MCP、DB | loading + 工具数/时间刷新 | e2e/tests/mcp-management/mcp-management.spec.ts | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "S-05" | planned |
| S-06 | E2E | Browser→tools API | schema/操作类型展示、无 Tool 级控制 | 同上 | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "S-06" | planned |
| E-06 | E2E | MCP failure→API→UI | Toast 失败 + 保留上一成功 Catalog | 同上 | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "E-06" | planned |
| E-07 | integration | 源码契约 | 失败不本地删行 + Toast | tests/frontend/test_mcp_user_scope_contract.py | uv run pytest -q tests/frontend/test_mcp_user_scope_contract.py | planned |
| RULE-ui-detail-001 | E2E | 真实浏览器渲染 | SideSheet 布局 | tests/frontend/test_mcp_detail_contract.py | uv run pytest -q tests/frontend/test_mcp_detail_contract.py | planned |

### Acceptance Evidence

### Log
- [2026-09-19] created (draft)
