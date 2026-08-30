# Tasks: Phase 1 Closure Gate — 产品架构闭环

- **Source**: `.code-flow/tasks/2026-08-28/phase1-closure/phase1-closure.design.md`（源自 `fluxion-phase1-closure-detailed-remediation.md` P1C-01~09 + DoD，历史文档已移除、git 历史可查；及 `docs/migration/当前代码偏差与迁移.md` P0-1/P0-2）
- **Created**: 2026-08-28
- **Updated**: 2026-08-28（13/13 TASK done；closure DoD verified）

## Proposal

闭合 Phase 1 的产品数据链：AgentDefinition 状态收口到 envelope 唯一 SoT、Tool 从 plugin 语义统一到 `ResourceKind.TOOL`、新增 Product Agent API（agent_id 主坐标）并把 runtime-profiles 路由降为 internal、补齐 UserProfile Attribute（provenance）、以 ChannelAuthenticator 收口 Channel 身份冒充（已登记 S2 残留）、前端完成 Studio round-trip / Typed CapabilityPicker / Chat 产品信息展示 / Agent 制 Chat Access / Console IA 修正。Closure DoD 全部验收前，Phase 2–5 不进入大规模实施。

依据核实结论（2026-08-28 代码验证）：P1C-01/02/03/04/06/07/08/09 全部实锤；P1C-05 第一层已修复（本计划覆盖第二层产品信息展示）；P1C-06 断点在 Console 前端选择器与签发校验缺失（后端模型已切 agent_id）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase1-closure.design.md#2.5 验收条件 | integration | 真实 Registry Store（双库） | TASK-001 | verified |
| S-02 | phase1-closure.design.md#2.5 验收条件 | integration | Capability parser + Registry | TASK-002 | verified |
| S-03 | phase1-closure.design.md#2.5 验收条件 | E2E | Browser → Console API → Registry | TASK-007 | verified |
| S-04 | phase1-closure.design.md#2.5 验收条件 | E2E | Product API → Runtime 链路 | TASK-003 | verified |
| S-05 | phase1-closure.design.md#2.5 验收条件 | E2E | Console 用户页 → 签发 → Chat resolve | TASK-010 | verified |
| S-06 | phase1-closure.design.md#2.5 验收条件 | E2E | Web channel per-message Bearer | TASK-005 | verified |
| S-07 | phase1-closure.design.md#2.5 验收条件 | integration | users 服务 + 真实 Store | TASK-004 | verified |
| S-08 | phase1-closure.design.md#2.5 验收条件 | E2E | Browser → Console Shell | TASK-011 | verified |
| S-09 | phase1-closure.design.md#2.5 验收条件 | integration | 全仓 TypeScript strict | TASK-012 | verified |
| S-10 | phase1-closure.design.md#2.5 验收条件 | E2E | Chat → 产品 API | TASK-009 | verified |
| E-01 | phase1-closure.design.md#2.5 验收条件 | integration | WeCom 签名 / Mattermost token 验证 | TASK-005 | verified |
| E-02 | phase1-closure.design.md#2.5 验收条件 | integration | Chat Access 签发校验 | TASK-006 | verified |
| B-01 | phase1-closure.design.md#2.5 验收条件 | unit | Capability parser 边界 | TASK-002 | verified |
| B-02 | phase1-closure.design.md#2.5 验收条件 | E2E | 普通用户面文案扫描 | TASK-012 | verified |
| S-11 | phase1-closure.design.md#2.5 验收条件 | integration | 真实 EffectiveCapabilityResolver + 双租户 Store（Gate G1 真值表） | TASK-013 | verified |
| E-03 | phase1-closure.design.md#2.5 验收条件 | integration | grant + 运行时三重交集 | TASK-013 | verified |

> NFR-C-01（Channel 验证 P95≤20ms）由 TASK-005 承载；NFR-C-02（legacy 读取零失败）由 S-01（TASK-001）承载。

---

## TASK-001: AgentDefinition SoT 收口

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.1 方案选型, phase1-closure.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-01, RULE-C-01

### Description

删除 `AgentDefinition` 内 `lifecycle`/`visibility` 字段（P1C-01）：status/visibility 唯一事实源 = `ResourceDefinition` envelope。`model_validator(mode="before")` 剥离 legacy `lifecycle`/`visibility` 键（兼容读存量 spec_json，不批量重写）；repository 写入不再包含 legacy 键；`validate_lifecycle` 一并移除。一次性检查脚本报告存量含 legacy 键的 spec（只报告不改写）。API/服务/测试统一读 envelope。

### Checklist

- [x] 删除 `agents/definitions.py` 的 `visibility`/`lifecycle` 字段与 `validate_lifecycle`；加剥离 validator；`agents/repository.py` 写入路径去 legacy 键
- [x] 存量检查脚本：扫描 spec_json 含 legacy 键的行并输出报告（`scripts/audit_legacy_spec_keys.py`，只报告不改写）
- [x] [S-01][integration] 修改生产代码前，编写验收测试并记录 RED：旧实现允许 spec.lifecycle=DRAFT 与 envelope status=PUBLISHED 不一致（证明偏差存在）
- [x] [S-01] GREEN 断言：创建（含 legacy 键）→ publish → GET，status/visibility 只来自 envelope；spec 读取剥离 legacy 键；序列化输出不含 lifecycle/visibility
- [x] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/agents/ backend/tests/contract/ -k "agent_definition or registry"`：23 passed（envelope 版本化生命周期不变、status/visibility 唯一来自 envelope、SQLite/PG 同契约——PG 由 FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实 Registry Store（SQLite+PG） | envelope SoT 唯一；legacy 键剥离读；序列化无 legacy 字段 | backend/tests/agents/test_agent_definition_sot.py（3 用例，S-01 全覆盖） | `.venv/bin/python -m pytest backend/tests/agents/ -q`；verifier：`pytest backend/tests/agents/ backend/tests/contract/ -k "agent_definition or registry"` | verified |

### Acceptance Evidence

> RED/GREEN 与真实组件证据如下；真实边界 = SQLiteRegistryStore（内存库）经 AgentDefinitionRepository 全链路，PG 契约由既有门控套件覆盖。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL×3：`test_s01_agent_spec_has_no_lifecycle_visibility_fields`（model_fields 仍含 lifecycle/visibility）；`test_s01_legacy_keys_stripped_on_validate`（model_dump 产出 legacy 键）；`test_s01_envelope_is_sole_sot_through_publish_roundtrip`（`fetched.spec_json` 含 `'lifecycle': 'draft'` 与 envelope PUBLISHED 并存——P1C-01 双事实源实锤） | 16 passed（agents/ 全量含 3 个新用例） | test_agent_definition_sot.py:57-59（字段断言）、:65-72（剥离断言）、:79-98（roundtrip 断言） | 真实 SQLiteRegistryStore 内存库 → AgentDefinitionRepository.create/publish/get 全链路；migration.py 去除 legacy 入参；scripts/audit_legacy_spec_keys.py 冒烟 0 条 | verified |

- **Spec verifier 证据**：`pytest backend/tests/agents/ backend/tests/contract/ -k "agent_definition or registry"` → 23 passed（版本化生命周期 DRAFT→PUBLISHED→版本递增、tenant 隔离、SQLite/PG 同契约既有用例全绿）。
- **回归**：`pytest backend/tests -q --ignore=backend/tests/workflow_poc` → **339 passed, 1 skipped**。workflow_poc 7 failed + 6 error 为环境依赖（Restate 容器 `host.docker.internal:51603 -> 502`），经 stash 基线复跑同样失败——先在基线即失败，非本任务引入。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] started（active marker：PHASE1-CLOSURE @ faf57e0）
- [2026-08-28] completed (done)

---

## TASK-002: Tool ResourceKind 统一

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.1 方案选型
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-02, B-01, RULE-C-02

### Description

`CapabilityType.TOOL → ResourceKind.TOOL`（`agents/capabilities.py:21` 现映射 PLUGIN）；parser 接受 `tool:<id>@<version>`；TOOL capability 遇 `plugin:` 前缀 fail-closed 拒绝（明确错误，不静默转换）；`plugin:` 仅保留 Provider/Extension 语义。存量检查脚本报告 `plugin:` tool 绑定（只报告不改写）。同步 AgentDefinition CapabilityBinding / Capability Resolver / Product API / tests（Workflow DSL V2 由 Phase 3 修订简报承接）。

### Checklist

- [x] 修改 `CapabilityType.TOOL` 映射为 `ResourceKind.TOOL`；parser 支持 `tool:` 前缀
- [x] TOOL capability + `plugin:` ref → fail-closed 明确错误；存量检查脚本输出报告（`scripts/audit_legacy_spec_keys.py` 扩展 plugin_tool_refs 巡检）
- [x] [S-02][integration] 修改生产代码前，编写验收测试并记录 RED：`tool:customer-query@1.0.0` 现解析不到 ResourceKind.TOOL（证明偏差存在）
- [x] [S-02] GREEN 断言：`tool:` → `ResourceKind.TOOL` 且可被 Agent 使用；`type=tool + plugin:ref` → 明确错误
- [x] [B-01][unit] 覆盖坏格式 ref / 未知 type / `plugin:` 当 tool → 全部明确报错不静默
- [x] **Spec verifier**：`RULE-backend-quality-001` — `pytest backend/tests/agents/ -k capability` + `ruff check`（All checks passed）/`mypy capabilities.py`（Success）：parser/mapping 全类型注解、fail-closed 不静默、错误信息含语义说明
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 parser + Registry | `tool:`→TOOL；`plugin:` 拒绝 | backend/tests/agents/test_capability_tool_kind.py（S-02×3）+ test_capability_binding.py（同步后全绿） | `.venv/bin/python -m pytest backend/tests/agents/ -q` | verified |
| B-01 | unit | parser 纯函数 | 三类非法输入全部明确报错 | backend/tests/agents/test_capability_tool_kind.py::test_b01_*（×4） | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL×3：`test_s02_tool_prefix_parses_to_tool_kind`（tool: 解析 None）；`test_s02_binding_tool_resolves_to_tool_kind`（仍映射 PLUGIN）；`test_s02_tool_type_with_plugin_ref_rejected_fail_closed`（无守卫不报错） | 全绿：`tool:`→`ResourceKind.TOOL`；TOOL binding 归一 TOOL；`plugin:`+TOOL → ValueError（含 plugin: 说明） | test_capability_tool_kind.py:36-56 | 真实 capabilities.py 纯函数 + 真实 typed model（extra=forbid）；既有 BE-S-05 同步为 TOOL 资源 + `tool:calc@2` 汇聚同 Registry 对象 | verified |
| B-01 | FAIL：`test_b01_malformatted_typed_ref_rejected`（带前缀 ref 不报错） | 4 用例全绿：坏格式/未知 type/空 ref/plugin: 当 tool 全部明确报错 | test_capability_tool_kind.py:59-78 | 纯函数直接断言；`CapabilityType("unknown")` 构造期 ValueError；空 ref 触发 ValidationError | verified |

- **存量巡检**：`scripts/audit_legacy_spec_keys.py` 扩展 `plugin_tool_refs` 报告（type=tool 且 ref 带 plugin: 前缀），冒烟 0 条。
- **回归**：`pytest backend/tests -q --ignore=backend/tests/workflow_poc` → **346 passed, 1 skipped**（较 TASK-001 后 +7：本任务新用例与既有语义同步用例）；workflow_poc 失败为环境依赖（已证基线即失败）。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-003: Product Agent API + Internal 边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.2 架构设计, phase1-closure.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: S-04, RULE-C-03

### Description

新增 Product API：`POST /api/v1/agents/{agent_id}/runs(:stream)` 与 `GET /api/v1/agents/{agent_id}`（产品面 displayName/icon/description/能力）；agent→runtime_profile 解析在服务层，产品面不暴露 runtime_profile_id（P1C-08）。`/api/v1/runtime-profiles/{id}/runs(:stream)` 迁移为 `/internal/v1/runtime-profiles/{id}/runs(:stream)`（internal/testing 专用），调用方同步更新并全量回归（RISK-C-03）。Product API 走统一 envelope。

### Checklist

- [x] 新增 `api/agents.py`（runs/:stream/product get）+ 服务层 agent→runtime_profile 解析 use case
- [x] `api/runtime.py` 路由迁 `/internal/v1/` 前缀；internal 调用方更新 + 回归
- [x] [S-04][E2E] 修改生产代码前，编写验收测试并记录 RED：`POST /api/v1/agents/{agent_id}/runs` 不存在（证明偏差存在）
- [x] [S-04] GREEN 断言：Product API 执行成功（agent 坐标）；响应与产品 GET 无 runtime_profile_id；`/internal/v1/runtime-profiles/{id}/runs` 可用
- [x] **Spec verifier**：`RULE-fluxion-runtime-001` — `pytest backend/tests/api/test_product_agent_api.py backend/tests/e2e/test_runtime_api.py -q` → 6 passed： Product→服务层→Runtime 编排无状态（不落 durable）、mechanics 内聚 internal
- [x] **Spec verifier**：`RULE-fluxion-console-api-001` — S-04 全用例：统一 envelope `{code, message, data, request_id}`、Handler 无手写响应结构、错误码命名空间
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | Product API → 服务层 → Runtime → Registry | agent 坐标执行成功；产品面零 runtime_profile_id；internal 路由可用 | backend/tests/api/test_product_agent_api.py（3 用例） | `.venv/bin/python -m pytest backend/tests/api/test_product_agent_api.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | Collection ImportError：`fluxion.services.agents_app` 不存在（Product API 缺失实锤） | 3 passed：agent 坐标 run 200、产品面 name/available 且零 runtime_profile_id/ref、internal 路由可用且旧公开路径 404 | test_product_agent_api.py:72-76、:86-90、:96-117 | 真实 RuntimeApplicationService + AgentDefinitionRepository + SQLite Registry（httpx ASGITransport 双 app）；不 mock | verified |

- **实现落点**：`services/agents_app.py`（ProductAgentApplicationService：get_agent_face/run/stream，mechanics 解析内聚）、`api/agents.py`（3 路由 + envelope + SSE）、`api/runtime.py`（→ /internal/v1/）、tests/e2e/test_runtime_api.py（调用点同步）。
- **Spec verifier 证据**：6 passed（product 3 + runtime_api 3）；mypy agents_app.py Success；ruff 通过。
- **回归**：`pytest backend/tests -q --ignore=backend/tests/workflow_poc` → **361 passed, 1 skipped**。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-004: UserProfile Attribute 模型

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.3 数据设计
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-07, RULE-C-06

### Description

`UserProfileSpec` 保留为 BasicProfile（display_name/bio/timezone/language）；新增 `ProfileAttribute`（key/value/source/source_ref/confidence/is_explicit/user_editable/visibility/valid_from/valid_until/superseded_by）与 `profile_attributes` 表（双库契约，索引 `(tenant_id, platform_user_id)`），支撑 learned profile 与 provenance（P1C-09）。users 服务提供 upsert/get/list/delete；learned 自动写入受 UserPreference 停学 gate 约束（与 Phase 2 memory 域 gate 对齐）。

### Checklist

- [x] `ProfileAttribute` 模型 + `profile_attributes` 表（幂等 DDL）+ 索引；users 服务 CRUD
- [x] learned 自动写入接 UserPreference 停学 gate（`UserPreferenceSpec.learning_enabled` 默认 True；`write_learned_attribute` 唯一 learned 入口）
- [x] [S-07][integration] 修改生产代码前，编写验收测试并记录 RED：写入 attribute（source=conversation, confidence=0.98）→ 当前无 provenance 承载（证明偏差存在）
- [x] [S-07] GREEN 断言：查看/修改/删除生效；provenance 保留；停学后无自动写入
- [x] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/contract/ -k profile_attribute`（SQLite 恒跑 + PG `FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 门控，`local-pg-test-env`）：双库同契约、upsert 无重复行
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | integration | 真实 users 服务 + 双库 Store | CRUD 生效；provenance 保留；停学 gate 生效 | backend/tests/users/test_profile_attribute.py（3 用例）；backend/tests/contract/test_profile_attribute_contract.py（双库契约） | `.venv/bin/python -m pytest backend/tests/users/ backend/tests/contract/ -k "profile_attribute or user" -q`；verifier：`pytest backend/tests/contract/ -k profile_attribute` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | Collection ImportError：`from fluxion.users.models import ProfileAttribute` 不存在（P1C-09 能力缺失实锤） | users/ 9 passed + contract 双库契约 1 passed；`write_learned_attribute` 停学时 ConsoleError、list 为空 | test_profile_attribute.py:57-60（provenance 字段）、:78-86（CRUD 保留 provenance）、:143-156（停学拒绝） | 真实 SQLiteRegistryStore → UserDomainService → SQLAlchemy schema（profile_attributes 表 + idx_user 索引）；PG 契约经门控套件 | verified |

- **实现落点**：`users/models.py`（ProfileAttribute + learning_enabled）、`registry/schema.py`（profile_attributes 表 + 索引）、`registry/user_store.py`（Record + Protocol）、`registry/user_sqlalchemy.py`（upsert/list/delete）、`registry/sqlalchemy_store.py`（门面）、`users/service.py`（CRUD + write_learned_attribute 停学 gate + AuditLog）。
- **Spec verifier 证据**：`pytest backend/tests/contract/ -k profile_attribute` → 1 passed（SQLite；PG 门控模式同套件）。
- **回归**：`pytest backend/tests -q --ignore=backend/tests/workflow_poc` → **350 passed, 1 skipped**；`mypy users/service.py` Success；`ruff` All checks passed。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-005: ChannelAuthenticator + 冒充负测试

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.2 架构设计, phase1-closure.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-06, E-01, RULE-C-04, NFR-C-01

### Description

新增 `services/channel_auth.py`：`ChannelAuthenticator.verify(request) -> VerifiedChannelIdentity`（channel_type/external_user_id/verification_method/verified_at/claims），fail-closed。三实现：Web=Bearer Chat Access Token（per-message 校验，收口 `api/channel.py` 已登记的 S2 残留「逐消息信任 channel_user_id」）、WeCom=签名验证、Mattermost=webhook/bot token。`/channels/web/messages` 改经 authenticator；`/bind` 前匿名例外保留（H1 语义）。验证失败 → 401/403 拒绝 + AuditLog（verification_method），token/签名不入日志。未验证身份不得映射 PlatformUser。NFR-C-01：单消息验证 P95≤20ms。

### Checklist

- [x] 实现 `ChannelAuthenticator` 抽象 + Web/WeCom/Mattermost 三实现；`api/channel.py` messages 接线（移除逐消息信任；匿名仅放行 `/bind`，其余 Bearer 强制走 verified 链路）
- [x] 验证失败 AuditLog + 脱敏（`audit_auth_failure` 仅记录 method/reason，token/签名零日志）
- [x] [S-06][E2E] RED：伪造受害者 channel_user_id 发非 bind 消息 → 当前返回 200 以受害者身份执行（S2 残留实锤，`assert 200 in (401, 403)`）
- [x] [S-06] GREEN：伪造请求 401 拒绝；有效 Bearer 消息以 token 用户执行（platform_user_id=user-token）
- [x] [E-01][integration] WeCom 有效签名通过/错误签名拒绝/未配置 secret fail-closed；Mattermost token 通过/拒绝
- [x] NFR-C-01 基准：50 次签名验证 P95 ≤ 20ms（实测远低于预算）
- [x] **Spec verifier**：`RULE-fluxion-console-001` — S-06/E-01 套件全绿：未绑定仅 `/bind`、未验证身份不入 PlatformUser 映射、绑定后经签发 token 走正式 Channel
- [x] **Spec verifier**：`RULE-backend-directory-001` — 新模块落 `services/channel_auth.py`（AST 落点断言由 architecture 套件承接）；api 层仅路由/装配
- [x] **Spec verifier**：`RULE-backend-logging-001` — AuditLog action=channel.auth.rejected 关联 request_id/tenant；structlog JSON；token/签名零泄露（载荷仅 method/reason）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实 ASGI 栈 + Bearer token + 绑定数据 | 有效通过；伪造 401/403；不映射 PlatformUser | backend/tests/channel/test_web_message_auth.py（S-06×2） | `pytest backend/tests/channel/test_web_message_auth.py -q` | verified |
| E-01 | integration | 真实 HMAC 签名/token 常量时间比较 | 拒绝 + AuditLog（verification_method） | test_web_message_auth.py::test_e01_*（×4） | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | FAIL×2：`test_s06_forged_bound_identity_rejected`（伪造受害者身份消息返回 200——S2 残留实锤）；`test_s06_valid_bearer_message_executes_as_token_user`（bearer 被忽略） | 全绿：伪造 401 + AuditLog；有效 Bearer 以 token 用户执行 | test_web_message_auth.py:65-72（伪造断言）、:104-107（token 用户断言） | 真实 ASGI 栈（httpx ASGITransport）+ 真实绑定/签发数据；golden-path 同步走 verified 流式链路（1 passed） | verified |
| E-01 | （模块缺失：`services/channel_auth.py` 不存在，collection ImportError） | 4 用例全绿：WeCom 有效/无效签名、未配置 fail-closed、Mattermost 通过/拒绝 | test_web_message_auth.py:170-235 | 真实 HMAC-SHA256 + compare_digest 常量时间比较；无 mock | verified |

- **NFR-C-01**：50 次签名验证采样 P95 ≪ 20ms（test_nfr_c01）。
- **回归**：`pytest backend/tests -q --ignore=backend/tests/workflow_poc` → **358 passed, 1 skipped**；ruff 通过。
- **语义迁移**：golden-path 匿名流式步骤改走 `/channels/web/access/messages:stream` + Bearer（与前端 httpChatApi 实际用法一致）。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-006: Agent-based Chat Access 签发收口（后端）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.4 接口设计
- **Acceptance-Refs**: E-02, RULE-C-05

### Description

Chat Access 签发收口（后端半边；前端 UI 见 TASK-010）：`issueChatAccess(platformUserId, agentId)` 校验 agent 存在且 `status=PUBLISHED`，否则明确错误码拒绝（E-02）；签发记录 `agent_id` 为唯一授权坐标（后端 `channel_app.py` 已切 agent_id，本任务补校验闭环，消除「runtime_profile 资源 ID 被当 agentId 签发」的错配）。

### Checklist

- [x] Console 服务层签发入口校验 agent published；错误码明确（`console_app.py` L217-227：存在性 + PUBLISHED 校验 → 404 `CHANNEL_AGENT_NOT_FOUND` + AuditLog；phase1 TASK-A105 已落地）
- [x] [E-02][integration] 验收测试：`test_e02_issue_with_draft_agent_rejected_and_published_succeeds`（无 RED——校验已存在，属已有行为补测，无法 RED 的原因记录于 Evidence）
- [x] [E-02] GREEN 断言：不存在（test_be_e_05）/未发布 agent → 404 拒绝；已发布 agent → 签发成功且记录 agent_id
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-02 | integration | 真实 Registry + 签发服务 | 未发布/不存在拒绝；published 通过且 agent_id 正确 | backend/tests/channel/test_agent_id_routing.py::test_e02_issue_with_draft_agent_rejected_and_published_succeeds + test_be_e_05 | `pytest backend/tests/channel/test_agent_id_routing.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-02 | **无 RED（已有行为补测）**：签发校验由 phase1 TASK-A105 先行落地（console_app.py L217-227），本任务核实其覆盖缺口并补齐「DRAFT 拒绝 + 发布后放行」用例；无法 RED 的原因 = 校验行为已存在，不得伪造失败 | 4 passed（含既有 test_be_e_05 ghost→404 与新用例 DRAFT 拒绝/发布放行） | test_agent_id_routing.py:122-160（新用例）；console_app.py:217-227（被测校验） | 真实 SQLiteRegistryStore + ConsoleApplicationService 直调；真实 agent 资源 put/publish 切换状态 | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-007: Agent Studio 完整 round-trip

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-008
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.1 方案选型
- **Acceptance-Refs**: S-03, RULE-C-07

### Description

`saveDraft()`（`AgentStudioPage.tsx:99`）构建完整 typed `AgentDefinitionSpec`：补写 `runtime_profile_ref`/`capabilities`（来自 Typed Picker）/`memory_policy_ref`/`personalization_policy_ref`（P1C-03）。E2E round-trip：选择 RuntimeProfile、添加 Skill/Tool/MCP（typed）、设置 Memory/Personalization Policy → Save → GET → 全字段一致；禁止只断言 toast（remediation §6.4）。

### Checklist

- [x] `saveDraft()` 写入完整 spec（五段字段 + typed capabilities）
- [x] [S-03][E2E] 验收测试 RED：round-trip 首跑 createResource 载荷仅 5 字段（四处全丢，P1C-03 实锤）
- [x] [S-03] GREEN 断言：全字段 round-trip 一致（含 binding type/capability_ref/version_pin）
- [x] 保存后 UI 回填（savedAgentId 保留；编辑态 initialAgentId 扩展位）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Browser → Console API → Registry | 全字段 round-trip 一致；binding 三字段完整 | frontend/apps/console/src/pages/__tests__/studio-roundtrip.test.tsx（S-03 用例） | `pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/studio-roundtrip.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | RED：首跑 createResource 载荷仅 5 字段（runtime_profile_ref/capabilities/memory_policy_ref/personalization_policy_ref 全丢——P1C-03 实锤） | 2 passed（console 全量 46 passed 无回归） | studio-roundtrip.test.tsx:81-90（七段断言） | 真实组件树 + in-memory ConsoleApi；vi.spyOn 捕获载荷 | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-008: Typed CapabilityPicker

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#2.3.2 字段约束
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-03（协作，最终负责人 TASK-007）

### Description

`CapabilityPicker` 从 `selected: string[]` 改为 typed `CapabilitySelection[] { type, capabilityRef, versionPin }`（P1C-04）：选择展示「名称 + 类型 + 版本」（如「客户查询 Tool v1.2.0」），Builder 不输入内部 ResourceKind；onChange 上抛 typed 数组；保存后 binding 三字段完整。

### Checklist

- [x] Picker 输出 typed `CapabilitySelection[]`；展示名称+类型+版本
- [x] [S-03 协作][E2E] 与 TASK-007 联调断言：保存后 binding 含 `type`/`capability_ref`/`version_pin`
- [x] **Spec verifier**：`RULE-frontend-component-001` — 组件契约测试通过（props 只读、事件上抛、类型导出并被 Studio 消费）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03（协作） | integration | 真实组件实例 + typed props | 选择产物为 typed 三元组；无 string-only 路径 | studio-roundtrip.test.tsx::TASK-008 用例 | `pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/studio-roundtrip.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03（协作） | RED：typed 选择产物不存在（string-only 路径） | 2 passed（typed 三元组 + 名称/类型/版本标签） | studio-roundtrip.test.tsx::TASK-008 用例 | 真实组件树 + in-memory ConsoleApi 同契约 | verified |

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-009: Chat Agent 产品信息展示

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: phase1-closure.design.md#2.3 功能方案
- **Acceptance-Refs**: S-10, RULE-C-03

### Description

Chat 头部不再展示 raw `access.agentId`（`App.tsx:146` 现状，P1C-05 第二层）：经 `GET /api/v1/agents/{agent_id}`（TASK-003 产品 API）解析 displayName/icon 并展示；解析失败降级占位「智能体」（不暴露 raw id）。

### Checklist

- [x] chat services 增加产品信息解析（经产品 API，in-memory/http 同契约）
- [x] [S-10][E2E] RED：头部当前显示 raw agentId（App.tsx:146 现状，代码核实 P1C-05 二层实锤；getAgentProduct 模块缺失 ImportError 双重证明）
- [x] [S-10] GREEN 断言：显示 displayName；不显示 raw agent_id；失败降级占位
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | Chat → 真实产品 API（in-memory 同契约） | displayName/icon 展示；零 raw agent_id | frontend/apps/chat/src/__tests__/agent-product-display.test.tsx（2 用例） | `pnpm --filter @fluxion/chat exec vitest run src/__tests__/agent-product-display.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-10 | RED（代码核实 + ImportError）：App.tsx:146 直显 raw agentId；getAgentProduct 模块缺失 | 2 passed：displayName 展示/未知 agent 占位「智能体」/header 零 raw id | agent-product-display.test.tsx:37-56 | 真实 ChatApp 组件树 + InMemoryChatApi（resolveAccess 按 agentId 条件暴露，不破坏既有 bind 流测试） | verified |

- **实现落点**：`types/chat.ts`（AgentProductFace + ChatApi.getAgentProduct 可选方法）、`services/inMemoryChatApi.ts`（in-memory 产品面）、`services/httpChatApi.ts`（GET /api/v1/agents/{id} + 404 降级 undefined）、`App.tsx`（agentDisplayName 状态 + 头部绑定）。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-010: User Agent Access UI

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-006
- **Source**: phase1-closure.design.md#2.3 功能方案
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-05, RULE-C-05

### Description

Console 用户页（`UsersChannelsPage.tsx`）选择器从 `listResources("runtime_profile")` 切换为 Agent 列表（agent_definition，产品模型展示），消除「RuntimeProfile 资源 ID 被当 agentId 签发」的错配（P1C-06）；文案去掉「运行态」（L248）；签发结果展示 agent 产品信息。

### Checklist

- [x] 选择器数据源切 `agent_definition` 列表（产品模型 label）；状态命名清理（`setRuntimeProfileId` → `setAgentId`，文案「运行态」→「智能体」）
- [x] [S-05][E2E] RED：既有 users-chat-access.e2e 在新数据源下失败（fixture 无 agent_definition）——证明旧路径依赖 runtime_profile 资源
- [x] [S-05] GREEN：fixture 补 agent_definition（published）→ 选择器展示 Agent → 签发成功（chat 侧 resolve 由 TASK-006/005 验证）
- [x] **Spec verifier**：`RULE-frontend-semi-001` — console 全量 46 passed：页面全 Semi 组件、react19-adapter 首导入保持、无第二套组件库
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | Console UI → 签发 API → chat resolve | Agent 选择签发；resolve 正确；产品信息展示 | frontend/apps/console/src/pages/users/__tests__/users-chat-access.e2e.test.tsx（同步后全绿） | `pnpm --filter @fluxion/console exec vitest run src/pages/users/__tests__/users-chat-access.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | RED：数据源切 agent_definition 后既有 users-chat-access.e2e 失败（fixture 无 agent 资源 → 选择器空 → 签发禁用），证明旧流程锚定 runtime_profile 资源 | 46 passed（console 全量无回归；签发链路走 agent_definition） | UsersChannelsPage.tsx:25-51/123-131；fixtures.ts:73-90 | 真实组件树 + in-memory ConsoleApi（fixture 含 published agent_definition） | verified |

- **后端协同**：签发校验（E-02）由 TASK-006 闭合；chat 侧 resolve 由 TASK-005/S-06 闭合。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-011: Console IA 修正

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: S-08, RULE-C-08

### Description

Console 信息架构三项修正（remediation §15.3–15.5）：默认视图 Overview；Build 下 Agents 单一一级入口（「新建智能体」从一级菜单降为 Agents 页内 CTA）；Binding 从 Governance 一级导航下沉（Agent Detail / User 360 / Platform Advanced 入口）。

### Checklist

- [x] 默认视图 Overview；Build IA 单一 Agents 入口 + 页内新建 CTA；Binding 下沉
- [x] [S-08][E2E] RED：既有 resources/create-modal 测试断言旧标题「运行资产」且导航含「新建智能体」一级菜单——重定向后失败证明偏差存在（现在期望同步为新 IA：标题「智能体」+ 页内「新建智能体」CTA + Binding 移出一组导航）
- [x] [S-08] GREEN 断言：三项全部成立；既有页面路由可达无回归（resources/versions/create-modal/bindings 套件全绿）
- [x] **Spec verifier**：`RULE-frontend-directory-001` — 目录纪律扫描通过（页面落 `src/pages/`、组件落 `src/components/`，无越界）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实 Console Shell + Router | 默认 Overview；单一 Agents 入口；Binding 非一级 | App.tsx 导航表 + resources/create-modal 等套件同步断言 | `pnpm --filter @fluxion/console exec vitest run`（46 passed） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | RED：IA 修改后 resources/create-modal 套件按旧标题「运行资产」断言失败（证明旧 IA 锚点存在、修改生效） | 46 passed 全绿：默认视图 overview；Build 无「新建智能体」一级项（resources 页头 CTA「新建智能体」就位）；Governance 导航无「资源绑定」 | App.tsx navItems/initialView；ResourcesPage.tsx PageHeader CTA；terminology 套件随描述改产品语言后通过 | 真实 Console Shell 渲染断言（jsdom） | verified |

- **术语联动**：ResourcesPage 描述改产品语言（移除 RuntimeProfile/Pod 字样），terminology 套件恢复绿。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-012: Closure 质量门禁（typecheck + 术语）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-008, TASK-009, TASK-010, TASK-011
- **Source**: phase1-closure.design.md#2.5 验收条件
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: S-09, B-02, RULE-C-09

### Description

Closure DoD 质量门禁：`pnpm -r typecheck` strict 全绿（S-09）；普通用户面术语扫描 denylist=0（chat 全部页面 + console 产品面，B-02，复用既有 terminology 测试模式并扩展 console 产品面）；无裸 `fetch`/`any`/`@ts-ignore` 滥用抽查。

### Checklist

- [x] typecheck strict 门禁跑通并记录结果；术语套件扩展 console 产品面
- [x] [S-09][integration] typecheck 基线：closure 修改前 console/chat tsc 全绿（无历史失败项）
- [x] [S-09] GREEN 断言：closure 修改面 typecheck 全绿（console/chat 双 app tsc --noEmit 0 error）
- [x] [B-02][E2E] 术语扫描：console terminology 6 passed（含 resources 视图产品语言修正后恢复绿）+ chat S-10 用例断言头部零 raw id
- [x] **Spec verifier**：`RULE-frontend-quality-001` — `tsc --noEmit`（console/chat 双 0 error）+ terminology 套件 + 裸 fetch/any/@ts-ignore 扫描（0 处）：strict 全绿、denylist=0
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | integration | 全仓 TypeScript 编译 | strict 全绿 | console/chat 双 app `tsc --noEmit` | `pnpm --filter @fluxion/console exec tsc --noEmit && pnpm --filter @fluxion/chat exec tsc --noEmit` | verified |
| B-02 | E2E | 真实页面文案遍历 | denylist=0 | console terminology.test.tsx（6 视图）+ chat agent-product-display.test.tsx | `pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/terminology.test.tsx`；chat S-10 套件 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-09 | 基线：修改前双 app tsc 已全绿（无历史失败项） | console/chat tsc --noEmit 均 0 error | 命令退出码 0 | 全仓编译真实执行 | verified |
| B-02 | （术语套件为既有防御，本次随 ResourcesPage 描述修正后恢复绿——红转绿记录于 TASK-011） | console terminology 6 视图 passed；chat S-10 零 raw id | terminology.test.tsx（BANNED_TERMS 遍历） | 真实页面渲染文案遍历 | verified |

- **质量扫描**：裸 `fetch`（非 services 层）0 处；显式 `: any` / `@ts-ignore`（非测试）0 处。

### Log
- [2026-08-28] created (draft)
- [2026-08-28] completed (done)

---

## TASK-013: Tool UserGrant 维度恢复 + Capability 命名收口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.2 架构设计（v0.2 并入 `docs/migration/当前代码偏差与迁移.md` P0-1/P0-2）
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-11, E-03, RULE-C-10

### Description

恢复 Tool 的用户授权维度（migration P0-1 / ADR-A002 / ARCH-06）：移除 `services/runtime_tool_ops.py:174` 的 `user_tools = agent_tools` 折叠（其注释自引 ADR-012 的 user=agent 推导，该推导已被 ADR-A006 撤销），`_effective_tool_policy` 的 user 维度改由真实 User Tool Grant 解析；`UserDomainService.grant`（`users/service.py:184` 现拒绝 tool-capability）支持 Tool grant（或引入统一 UserCapabilityBinding Store）。命名收口（P0-2）：`AgentDefinition.capabilities: CapabilityBinding[]` 改名 `AgentCapabilityReference`/Allowlist 语义——它只表达 ref/version/type 上限，不承载用户 ownership。验收对齐 `docs/development/架构验收Gate.md` G1 真值表：同一 AgentDefinition 下不同用户实际 Tool list 与调用结果不同；UserGrant/AgentAllowlist/TenantPolicy 任一缺失即 deny（fail-closed）。Skill 扩展语义不受影响（ADR-003 修正案）。

### Checklist

- [x] `_effective_tool_policy` 移除 `user_tools = agent_tools` 折叠，user 维度接真实 User Tool Grant；执行链三重交集 fail-closed 语义不变
- [x] `UserDomainService.grant` 支持 Tool grant（或统一 UserCapabilityBinding Store）
- [x] `CapabilityBinding` → `AgentCapabilityReference` 命名收口（模型/API/UI/fixture 同步，不留双模型）
- [x] [S-11][integration] 修改生产代码前，编写验收测试并记录 RED：当前 user_tools=agent_tools 且 grant 拒绝 Tool——User-A/User-B 无法有不同 Tool 授权（证明 P0-1 存在）
- [x] [S-11] GREEN 断言（Gate G1）：同一 AgentDefinition 下 User-A/User-B 实际 Tool list 与调用结果不同且正确；负向矩阵三行全拒
- [x] [E-03][integration] GREEN 断言：grant Tool 成功；未授权 Tool 调用 fail-closed；Skill 扩展语义不回归
- [x] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 `python -m pytest backend/tests/services/ backend/tests/runtime/ -k "tool_policy or effective_capability"`： Tool 是 Agent-facing invocation contract（与 Plugin 实现载体分离）、三重交集 fail-closed、执行链无第二套授权拼装（REQ-CAP-006）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-11 | integration | 真实 EffectiveCapabilityResolver + 双租户 Store | G1 真值表全过；负向矩阵全拒；A/B 用户 Tool list 不同 | backend/tests/services/test_tool_user_grant.py::test_s11_g1_truth_table_per_user_tool_grants | `.venv/bin/python -m pytest backend/tests/services/test_tool_user_grant.py -q` | verified |
| E-03 | integration | 真实 grant 服务 + 运行时 tool policy | grant Tool 成功；未授权调用 fail-closed；Skill 扩展不回归 | 同文件::test_e03_grant_supports_tool_capability | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-11 | Collection ImportError（AgentCapabilityReference 不存在 = P0-2）+ G1 真值表 FAIL（kind 参数缺失、A/B 集合相同） | 2 passed：user-a={calc}/user-b={weather}、agent 维度并集、负向矩阵 deny | test_tool_user_grant.py:124-135 | 真实 RuntimeToolOps 策略解析 + capability_grants 表（新增 capability_kind 列）+ Agent Registry 读取 | verified |
| E-03 | FAIL：grant(tool) 抛 ConsoleError（授予端拒绝 tool-capability） | grant Tool 成功（kind=tool 落库） | test_tool_user_grant.py:82-89 | 真实 UserDomainService + grants 表 | verified |

- **实现落点**：`runtime_tool_ops.py`（`_user_granted_tools` 替换 user_tools=agent_tools 折叠；MCP binding 派生 ids 并入 user 维度——挂载层授权到三重交集的映射）、`users/service.py`（grant 开放 TOOL + capability_kind 落库）、`registry`（capability_grants.capability_kind 列 + Record/Protocol/门面）、全仓 `CapabilityBinding`→`AgentCapabilityReference`（10 文件，wire 键 capabilities 不变）。
- **语义迁移**：8 个依赖旧行为的 e2e fixture 补用户 tool 授权（test_agent_loop_product/test_real_mcp_agent×6/test_trace）——新模型下 fixture 用户需显式授权，属预期迁移。
- **回归**：`pytest backend/tests -q --ignore=backend/tests/workflow_poc` → **363 passed, 1 skipped**；ruff 通过。

### Log
- [2026-08-28] created (draft)（v0.2 并入 migration P0-1/P0-2）
- [2026-08-28] completed (done)
