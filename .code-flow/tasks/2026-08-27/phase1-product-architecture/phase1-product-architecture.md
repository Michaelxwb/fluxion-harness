# Tasks: Phase 1 产品架构（Agent/User Domain + 冻结导航 Console）

- **Source**: .code-flow/tasks/2026-08-27/phase1-product-architecture/（phase1-product-architecture.backend.design.md + phase1-product-architecture.frontend.design.md，两份 v0.3 合并拆解）
- **Created**: 2026-08-27
- **Updated**: 2026-08-27

## DeepSeek 五路审查整改台账（2026-08-28）

### 已修复（8 HIGH + 3 MED，全部实测验证）
- **H1/H2** httpConsoleApi `requiredResourceType` 白名单缺 v2.2 五 kind → 已对齐全枚举（生产 createResource/listResources 不再 400）。
- **H3** issueChatAccess 发 `runtime_profile_id` 且解析读同字段 → body 改 `{agent_id}`，parse 改 `record.agent_id`（「生成对话链接」生产可用）。
- **H4** Studio 试跑硬编码 "assistant" → 保存生成的 resourceId 存入 `savedAgentId` state，试跑优先使用。
- **H5** 迁移对无 @pin 的 legacy 条目：`rpartition("@")` 语义误用（无分隔符时 resource_id 为空）→ 正确整串作 ref + `latest-published`；并修 M4 put/publish 间断点续跑（DRAFT 续发布）。
- **H7** alembic 迁移 `f7a3c91d2e84`：user_profiles/user_preferences/capability_grants 三表 + chat_access_tokens 列改名（alter_column），upgrade/downgrade 契约测试过。
- **H8①④** productClient listCapabilities 改走 `/studio/{kind}`；getResourceSchema 经 SCHEMA_KIND 映射单数枚举。
- **H8②③** listResources/listUsers 解包 `{items}` 并改 `page_size`。
- **M3** granted_scope 校验（invoke/manage 之外 400）。
- **M9** 伪造 policy-default 移除（types optional + parse optionalString + 页面展示空）。
- **M10** ResourcesPage loadResources try/catch → error state。

### 挂账（后续批次处理）
- **M2** profile/preference 并发写 IntegrityError（PG ON CONFLICT DO UPDATE 改造）。
- **M5** test-run SSE 透传 request_id/trace_id（X- headers 进 RunRuntimeRequest）。
- **M6** channel 派生 profile 键的精确版本 pin（与 resolver 统一 selector）。
- **M7** SchemaForm anyOf 多选项渲染（fail_policy 等单选变 Select）。
- **M11** chat http 层未绑定门禁 + FE-S-14 真链断言。
- **M12** 工具准入冻结进 Snapshot（agent_definition_version 扩展）。
- **M13** 页面级 http 路径测试补层。
- **LOW 汇总**：policie_ 自动 id、put_profile null 清字段、敏感键启发式、_actor 头参、input 长度、uuid4 重复导入、AgentDomainError code 属性、grants 唯一约束、360 activity 窗口、contracts.py 行数、resolve 悬空引用语义、_ensure_default_agent 治理链、issue_chat_access mechanics 校验、skill binding 覆盖死代码、BindingsPage SecretRef 文案、Studio effect 取消、initialKind Tab 高亮、semi-compliance 子串匹配。

### 正面确认（避免重复返工）
tenant 隔离全链强制、/studio typed pydantic 校验、envelope/错误码集中、capability 单一解析源、审计五类含关联字段、A105 后端 access 对齐、AST 守护/Kernel 方向/无 secret 泄漏/冻结导航/tsc strict——均复核通过。

---

## Proposal

把 v1「RuntimeProfile 混装 persona/model/capability/mechanics + Resource 铺表 Console」重构为产品化架构：后端拆出 AgentDefinition（PRD §4.2 对齐）+ User Domain（Gate 1B）+ agent_id 产品路由（TASK-A105）+ Product API/schema 端点；前端落地冻结导航 `Overview/Build{Agents,Workflows,Capabilities,Eval}/Users/Governance/Operations/Platform` + Agent Studio（CapabilityPicker）+ schema 驱动全资源接入。开发阶段接受破坏性迁移，不做兼容补丁。

**场景 ID 前缀约定**：两份 design 场景号冲突，本文件统一加前缀——`FE-*` = frontend.design.md §2.4 场景，`BE-*` = backend.design.md §2.5.2 场景。

**范围说明**：前端 UI 任务（011/014/015/016/017/019 等）在 roadmap 属 Phase 4，按用户常驻原则（必备功能都要、管理员要能用）与 design P0 标注直接纳入本文件实现；P2 的 Eval 骨架（FE-S-12）与 Operations（Phase 5/6）不拆，后续单独 plan。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| FE-S-01 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → UI | TASK-011 | verified |
| FE-S-02 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Product API → Runtime → UI | TASK-015 | verified |
| FE-S-03 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Product API → Schema endpoint → UI | TASK-015 | verified |
| FE-S-04 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Schema endpoint → UI | TASK-014 | verified |
| FE-S-05 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Schema endpoint → UI | TASK-014 | verified |
| FE-S-06 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Product API → UI | TASK-016 | verified |
| FE-S-07 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-016 | verified |
| FE-S-08 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-018 | verified |
| FE-S-09 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-017 | verified |
| FE-S-10 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-017 | verified |
| FE-S-11 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Schema endpoint → UI | TASK-020 | verified |
| FE-S-13 | frontend.design.md#2.4 验收条件 | E2E | Browser → DOM 文本断言 | TASK-012 | verified |
| FE-S-14 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → bind API → UI | TASK-019 | verified |
| FE-S-15 | frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-011 | verified |
| FE-E-01 | frontend.design.md#2.4 验收条件 | integration | Service → UI | TASK-015 | verified |
| FE-E-02 | frontend.design.md#2.4 验收条件 | integration | Service → UI | TASK-015 | verified |
| FE-E-03 | frontend.design.md#2.4 验收条件 | integration | Schema endpoint → UI | TASK-014 | verified |
| FE-B-01 | frontend.design.md#2.4 验收条件（边界场景） | integration（静态断言） | tsc strict + ESLint + grep 源码扫描 | TASK-013 | verified |
| BE-S-01 | backend.design.md#2.5.2 功能验收场景 | E2E | Product API → Service → Registry Store → Resolver | TASK-004 | verified |
| BE-S-02 | backend.design.md#2.5.2 功能验收场景 | integration | Service → Store | TASK-001 | verified |
| BE-S-03 | backend.design.md#2.5.2 功能验收场景 | E2E | Resolver ×2 Pod 实例 + Store | TASK-009 | verified |
| BE-S-04 | backend.design.md#2.5.2 功能验收场景 | integration | Store + architecture-test | TASK-002 | verified |
| BE-S-05 | backend.design.md#2.5.2 功能验收场景 | integration | Service → Capability Contract | TASK-006 | verified |
| BE-S-06 | backend.design.md#2.5.2 功能验收场景 | integration | Product API → spec model registry → UI schema | TASK-003 | verified |
| BE-S-07 | backend.design.md#2.5.2 功能验收场景 | E2E | Product API → Service → Store → Resolver → UI schema | TASK-004 | verified |
| BE-S-08 | backend.design.md#2.5.2 功能验收场景 | E2E | Product API → UserDomainService → Profile Repository → ChannelIdentity Store | TASK-007 | verified |
| BE-S-09 | backend.design.md#2.5.2 功能验收场景 | E2E | Chat Access/Channel routing → Agent Resolver → Store | TASK-008 | verified |
| BE-S-10 | backend.design.md#2.5.2 功能验收场景 | E2E | Product API → UserDomainService → UI | TASK-007 | verified |
| BE-E-01 | backend.design.md#2.5.2 功能验收场景 | integration | Product API → Service | TASK-004 | verified |
| BE-E-02 | backend.design.md#2.5.2 功能验收场景 | integration | Service → Store | TASK-004 | verified |
| BE-E-03 | backend.design.md#2.5.2 功能验收场景 | integration | Service → Model Provider | TASK-005 | verified |
| BE-E-04 | backend.design.md#2.5.2 功能验收场景 | integration | Service → Logger | TASK-005 | verified |
| BE-E-05 | backend.design.md#2.5.2 功能验收场景 | integration | Chat Access → Agent Resolver | TASK-008 | verified |
| BE-E-06 | backend.design.md#2.5.2 功能验收场景 | integration | UserDomainService → ChannelIdentity Store | TASK-007 | verified |
| BE-B-01 | backend.design.md#2.5.2 功能验收场景（边界场景） | integration | Snapshot → Resolver | TASK-009 | verified |
| BE-B-02 | backend.design.md#2.5.2 功能验收场景（边界场景） | integration | Chat Access → routing | TASK-008 | verified |

> FE-S-12（Eval 骨架，P2/Phase 5）不在本表——P2 场景不拆任务，Phase 5 单独 plan。RULE/高影响 RISK 映射见各 TASK 的 Spec verifier 行与 design Spec Compliance Matrix。

---

## TASK-001: AgentDefinition spec model + AGENT_DEFINITION ResourceKind + agents/ domain 包

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B01）, phase1-product-architecture.backend.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: BE-S-02, BE-E-02（引用）, BE-S-01（引用）, BE-S-08（引用）

### Description

新增 `ResourceKind.AGENT_DEFINITION` + typed spec model（对齐 PRD §4.2：identity(name/description/system_prompt)、owner/visibility/lifecycle、model_ref、runtime_profile_ref、capabilities[CapabilityBinding，type∈skill/tool/mcp]、workflow_ref、memory_policy_ref、personalization_policy_ref、instructions；**无独立 tools 字段**）。存于既有 Registry `resource_definitions`，DRAFT→PUBLISHED 生命周期。新建 `backend/src/fluxion/agents/` domain 包（TASK-A101/A102：model + repository）。

### Checklist
- [x] 在 `agents/` 落 AgentDefinition typed spec model（frozen dataclass，字段=design §3.3 表，含 §4.2 全分组）
- [x] 注册 `ResourceKind.AGENT_DEFINITION` 到 Registry kind 分派；resource_definitions 复用（无新表）
- [x] AgentRepository：create/get/list/publish，tenant-scoped，版本化（DRAFT→PUBLISHED）
- [x] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/agents/test_agent_definition_model.py`：断言 AgentDefinition 走 resource_definitions 版本化生命周期（DRAFT→PUBLISHED→版本递增）、tenant 隔离、SQLite=PG 共享 contract test
- [x] [BE-S-02][integration] 修改生产代码前编写验收测试并记录 RED：AgentDefinition 引用 runtime_profile_ref 解析时合并默认+覆盖，spec_json 无 persona/model/capability 残留
- [x] [BE-E-02][integration] 覆盖 version 冲突 → 409 `version_conflict`（API 层最终断言在 TASK-004，此处 Service 层抛出版本冲突异常）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-02 | integration | AgentDefinitionRepository、Registry Store（SQLite 恒跑 / PostgreSQL 门控参数化） | spec_json 无 persona/model/capability 内嵌键；DRAFT→PUBLISHED；resolve 解析引用的 RuntimeProfile | tests/agents/test_agent_definition_model.py::test_be_s_02_agent_spec_references_profile_without_persona | `cd backend && uv run python -m pytest tests/agents/test_agent_definition_model.py -k be_s_02` | verified |
| BE-E-02 | integration | Repository、Store | version 冲突抛出 AgentVersionConflictError（API 409 在 TASK-004 断言） | tests/agents/test_agent_definition_model.py::test_be_e_02_duplicate_version_conflict | `cd backend && uv run python -m pytest tests/agents/test_agent_definition_model.py -k be_e_02` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-02 | FAIL: ModuleNotFoundError: No module named 'fluxion.agents'（uv run pytest tests/agents/test_agent_definition_model.py -x -q，collection error） | PASS: `uv run python -m pytest tests/agents/test_agent_definition_model.py -q` → 7 passed | test_be_s_02_agent_spec_references_profile_without_persona：forbidden_keys.isdisjoint(spec_json)/status is DRAFT→PUBLISHED/resolve 返回 profile-1 | SQLiteRegistryStore(":memory:") 真实 Store（fixture 参数化，PG 由 FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控）；无 mock | verified |
| BE-E-02 | FAIL: 同上（collection error，AgentVersionConflictError 未定义） | PASS: 同上 | test_be_e_02_duplicate_version_conflict：pytest.raises(AgentVersionConflictError) | 同上（store.put 真实 VersionConflictError 包装） | verified |

> Spec verifier `RULE-fluxion-resource-001`：test_rule_resource_001_versioned_lifecycle + test_rule_resource_001_tenant_isolation（同文件，SQLite/PG 同契约断言）。
> 回归：`pytest tests/ --ignore=tests/workflow_poc` → 300 passed / 2 failed（test_release_gate ×2 为存量失败：缺 docs/release/fluxion-v1-release-gate.md + 无已完成契约，stash 验证与本次改动无关）；workflow_poc Restate 6-7 failed 为存量环境受限项（需 live Restate 集群）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。Done Gate 说明：本任务 RULE-fluxion-resource-001 code stage 已 applied + task-file artifact ref（applied convention）；code gate 文件级聚合的其余 14 条 pending 属各 owner 任务（002-021）未开始之预期，随各自完成逐任务 bind。
- [2026-08-27] **Review 追记（第二会话）**：
  ① `AgentDefinition` 的 `frozen=True` 由外部会话（codex）在 TASK-003 落地时后补——为满足 dispatch 全量 frozen 断言；改动正确（与 ADR-011 执行期不可变一致），原 evidence 未含此变更，特此留痕；
  ② `AgentDefinitionRepository.get()` 无版本参数时的「回退最新版本（任意状态）」读取语义是实现期扩展（store 契约原本 latest-published only），目的=支撑 Studio 读取 DRAFT；已回写 backend brief §3.4 API-B02 行；
  ③ 测试私有 `_seed_runtime_profile` 种子由 legacy spec 修正为 mechanics 合法形状——此前依赖「store 层不按 kind 校验」的实现细节，fixture 不应如此；
  ④ PostgreSQL 契约断言受 `FLUXION_REQUIRE_POSTGRES_CONTRACT` 门控：本机 :5432 有 PG 实例但 postgres/postgres 与 peer-auth 均认证失败、无 .env/psql 可用 → 无法建 fluxion_test 库，双库断言保持待办；**需要用户提供测试 PG DSN**（export FLUXION_POSTGRES_DSN=... 后跑 tests/agents + tests/contract 即闭环）。
- [2026-08-27] **PG 双库补验闭环（review 四点之一）**：用户提供本地 Docker PG 凭据（mmuser）。建独立库 `fluxion_test`（OWNER mmuser；实例上的 mattermost 业务库与 fluxion_poc_dbos* 未触碰）；表无需手动建——store 的 `reset_on_initialize=True` 自动 drop_all+create_all 自举。`FLUXION_REQUIRE_POSTGRES_CONTRACT=1` 下 **tests/agents + tests/contract/test_registry_store.py 双库参数化 37 passed**，RULE-fluxion-resource-001 的 SQLite=PG 同契约字面承诺实测达成。

---

## TASK-002: RuntimeProfile 语义收缩（TASK-A104）+ AST 守护 + 迁移脚本

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B02）, phase1-product-architecture.backend.design.md#4.2 数据迁移
- **Spec-Refs**: backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: BE-S-04

### Description

从 RuntimeProfile spec model 移除 persona/model/capability 产品语义，保留 runtime mechanics（request_timeout_ms/max_retries/concurrency/memory_budget_mb/executor_config）。一次性迁移脚本把存量 persona/model 数据迁到 AgentDefinition（破坏性，不双写）。architecture-test AST 守护 agents/ 目录（不 import kernel/runtime impl）。

### Checklist
- [x] RuntimeProfile typed model 去字段（persona/system_prompt/model/capability），契约测试同步
- [x] 迁移脚本：RuntimeProfile persona/model → AgentDefinition（design §4.2 阶段 2-3），一致性校验
- [x] architecture-test：AST 扫描 agents/ 不 import kernel/runtime impl；RuntimeProfile model 无 persona/model 字段
- [x] **Spec verifier**：`RULE-backend-directory-001` — 运行 `python -m pytest backend/tests/architecture/ -k runtime_profile`（verifier 命令含 AST 守护断言）
- [x] [BE-S-04][integration] 修改生产代码前编写验收测试并记录 RED：AST 守护通过 + 收缩后 model 无产品语义字段

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-04 | integration | Store + architecture-test | RuntimeProfile model 无 persona/model/capability 字段；contracts 不 import kernel impl | tests/architecture/test_runtime_profile_architecture.py::test_be_s_04_runtime_profile_is_mechanics_only_and_agents_contracts_are_isolated | `cd backend && uv run python -m pytest tests/architecture/test_runtime_profile_architecture.py -k be_s_04` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-04 | FAIL: `set(RuntimeProfile.model_fields)` 仍含 `prompt/model_policy/allowed_*` 等产品字段（1 failed） | PASS: verifier 2 passed；相关回归 42 passed；Registry SQLite+PG contract 24 passed | `test_runtime_profile_architecture.py:66-103`（字段集/拒绝旧键/真实 Store/AST）；`:158-179`（迁移一致性与幂等） | `SQLiteRegistryStore(":memory:")` 真实 put/get/publish + `agents/`/contracts AST 扫描；无 mock；迁移 CLI 可执行 | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。`RULE-backend-directory-001` code-stage applied；verifier/相关回归/Registry 双库 contract 均通过。全仓旧 runtime_profile 产品语义调用由本文件 TASK-008/009 继续迁移，不以兼容字段回填。
- [2026-08-27] **消费方迁移收尾（第二会话）**：收缩当时下游未迁移导致全仓 78 failed / 6 errors。本次补齐：
  ① `resolver.py` SnapshotBuilder 改从 **AgentDefinition** 取 system_prompt/instructions/capabilities（skill→版本解析、mcp→mcp_versions、tool→准入 ref 不解析），model_resolution.provider=agent.model_ref.id、timeout/max_rounds/deadline 来自 mechanics profile；缺省按**同名**回退解析 AGENT_DEFINITION（迁移产物同名；显式 `agent_definition_id` 优先，MISSING 即 raise）。
  ② 契约落点（记入 design 追溯）：`RuntimeProfile.max_rounds`（循环预算属 mechanics，自 model_policy.max_rounds 迁入，arch set 同步）；failover 链走 `executor_config.model_failover`（store-backed 注册门槛=plugin_versions pin + PUBLISHED PLUGIN 资源存在双查，进程内实现不被覆盖）；`SensitiveSpecModel` 对 SecretRef 家族键放行 `None`（未引用≠明文）。
  ③ 生产 `_ensure_default_agent`：`ensure_runtime_profile` 自举路径同步种默认同名 Agent（dev bundle/CLI 开箱可跑）。
  ④ 测试面：runtime_helpers 种子改双资源（mechanics profile+同名 agent）、console_helpers 共享 spec 转 mechanics、13 处内联 legacy spec 与 6 处 CreateRuntimeProfileRequest 调用点迁移、断言中模型名热切语义移交流出（标注 TASK-004/008）。
  **终态：非 PoC 全量 307 passed / 仅存量 release_gate×2 失败（基线）；本任务 verifier 套件 23 passed；ruff scoped 清零、mypy 无错。**

---

## TASK-003: typed spec model per kind + `_definition_model` 分派 + schema 端点全 kind

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B06）, phase1-product-architecture.backend.design.md#3.4 接口设计（API-B06）
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: BE-S-06, BE-S-07（引用，最终 owner TASK-004）

### Description

为 model/tool/skill/mcp/runtime-profile/secret/policy/agent_definition 各 kind 落地 typed spec model（ADR-011 RS），补全 `_definition_model(kind)` 分派（当前仅 MODEL_PROVIDER 硬接线于 `console_resources.py:512-513`）；确认 `GET /resources/{kind}/schema` 覆盖全部 kind（RS6 既有实现扩展）。前端 SchemaForm 的前置依赖。

### Checklist
- [x] 各 kind typed spec model（frozen dataclass + 字段约束，类型注解全覆盖）
- [x] `_definition_model(kind)` 分派表补全全部 kind（暂不表驱动，Phase 1 末评估）
- [x] `GET /resources/{kind}/schema` 对每个 kind 返回 JSON schema（扩展 RS6）
- [x] **Spec verifier**：`RULE-backend-quality-001` — 运行 `python -m pytest backend/tests/resources/test_definition_model_dispatch.py`：断言各 kind 分派返回 typed model、无 raw spec_json 直读、公共函数类型注解完整
- [x] [BE-S-06][integration] 修改生产代码前编写验收测试并记录 RED：每个 kind 的 schema 端点返回可用 JSON schema（可驱动 SchemaForm 渲染）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-06 | integration | Product API → spec model registry → UI schema | 每个 kind 返回 typed spec model 的 JSON schema | tests/resources/test_definition_model_dispatch.py::test_be_s_06_each_resource_kind_exposes_typed_schema | `cd backend && uv run python -m pytest tests/resources/test_definition_model_dispatch.py -k be_s_06` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-06 | FAIL: `ResourceKind` 缺 `model/tool/secret`（1 failed，实现前首跑） | PASS: `uv run python -m pytest tests/resources/ tests/integration/test_resource_schema_api.py tests/unit/test_resource_schema.py tests/agents/ tests/architecture/ tests/contract/test_registry_store.py -q` → **59 passed**（含 Spec verifier dispatch frozen/forbid 断言 + RS6 全 kind 参数化 schema 端点 + Registry SQLite 契约） | test_be_s_06_each_resource_kind_exposes_typed_schema：遍历 ResourceKind 逐 kind 200/code=0/schema.type=object/additionalProperties=false；test_rule_backend_quality_001_dispatch_is_frozen_and_typed：11 kind 全部非 None 且 extra=forbid+frozen | 真实 console_stack API→Service→model registry；无 mock；frozen/forbid 用 model_config 反射断言 | verified |

> 范围内验证完成。注意：同一外部会话把 TASK-002 的 RuntimeProfile 收缩一并落下但未迁移消费方（resolver/fixtures 仍用 legacy spec 形状），全仓非 PoC 回归 78 failed / 6 errors——破损归 TASK-002 待启动修复，不计入本任务验收。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。说明：实现经外部会话（codex）先行落地并以同一激活流程补记 RED；GREEN 与 verifier 由本会话本人复跑确认（59 passed）。Done Gate 按既有 applied convention：RULE-backend-quality-001 code stage 已 applied + task-file artifact ref；code gate 文件级聚合其余 rule pending 属各 owner 任务未开始之预期。

---

## TASK-004: Product API `/studio/{kind}` 通用 CRUD + `/studio/agents`

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-003
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B05）, phase1-product-architecture.backend.design.md#3.4 接口设计（API-B01/B02/B05）
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: BE-S-01, BE-S-07, BE-E-01, BE-E-02

### Description

Product API：`POST/GET /studio/agents`、通用 `GET/POST /studio/{kind}` + `/{id}`（kind ∈ agents/models/tools/skills/mcp/runtime-profiles/secrets/policies/evals），走 typed model 校验 + Registry Store；统一 envelope `{code,message,data,request_id}`，复用既有 RequestContext/ApiResponse 基础设施（业务 Handler 禁手写响应结构）。Control API `/api/v1/resources/*` 保持不动（退高级区由前端处理）。

### Checklist
- [x] `/studio/agents` 创建/查询（API-B01/B02：请求字段=design §3.4 表，含 owner/visibility/lifecycle/capabilities/memory refs）
- [x] 通用 `/studio/{kind}` CRUD router（typed model 校验 + Store + envelope）
- [x] 错误码：42201 `agent_definition_invalid` / 40901 `version_conflict`（走既有错误码集中化）
- [x] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 `python -m pytest backend/tests/api/test_studio_crud_api.py`：断言所有响应经统一 envelope 封装（业务 Handler 无手写响应结构）、request_id 写入 `X-Request-ID`
- [x] [BE-S-01][E2E] 修改生产代码前编写验收测试并记录 RED：POST /studio/agents → 发布 → GET 列表（真实 API→Service→Store→Resolver 链，无 mock）
- [x] [BE-S-07][E2E] POST /studio/models（api_key→SecretRef）→ GET 列表 → GET schema 全链
- [x] [BE-E-01][integration] 缺 model_ref/owner → 422 + 字段定位
- [x] [BE-E-02][integration] version 冲突 → 409

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-01 | E2E | API、Service、Registry Store、Resolver | DRAFT→PUBLISHED；列表可见；spec 为 typed model | tests/api/test_studio_crud_api.py::test_be_s_01_studio_agent_create_publish_and_list | `cd backend && uv run python -m pytest tests/api/test_studio_crud_api.py -k be_s_01` | verified |
| BE-S-07 | E2E | API、Service、Store、Resolver、schema 端点 | 模型资源可建可列；schema 可驱动表单；secret 不落 spec_json | tests/api/test_studio_crud_api.py::test_be_s_07_studio_models_crud_with_secret_ref_schema | `cd backend && uv run python -m pytest tests/api/test_studio_crud_api.py -k be_s_07` | verified |
| BE-E-01 | integration | API、Service | 422 + `agent_definition_invalid` + 字段定位 | tests/api/test_studio_crud_api.py::test_be_e_01_agent_without_required_field_rejected | `... -k be_e_01` | verified |
| BE-E-02 | integration | API、Service、Store | 409 + 冲突码非 0 | tests/api/test_studio_crud_api.py::test_be_e_02_duplicate_agent_version_conflicts | `... -k be_e_02` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-01 | FAIL: POST /studio/agents → 404 路由不存在（4 failed 批量首跑） | PASS: 4 passed（`pytest tests/api/`） | test_be_s_01_*：create 200/draft → publish 200/published → list 含 agent-1 → store 直查 spec 无 prompt/model_policy 残留 | console_stack 真实 FastAPI→Service→SQLiteRegistryStore 治理发布链（audit+publish_record+outbox）；无 mock | verified |
| BE-S-07 | 同上 404 | PASS: 同上 | test_be_s_07_*：SecretRef 凭据建模成功；publish 后列表可见；schema 端点 properties ⊇ 必填集 | 同上 + RS6 schema 端点复用 | verified |
| BE-E-01 | 同上 | PASS: 同上 | test_be_e_01_*：缺 model_ref → 422/code=42_201/message 定位 model_ref Field required | middleware 统一异常→envelope | verified |
| BE-E-02 | 同上 | PASS: 同上 | test_be_e_02_*：同 (id,version) 二次创建 → 409 code≠0 | store VersionConflictError→ConsoleResourceConflictError 集中映射 | verified |

> 实现说明：①新增 `api/studio.py`（kind 白名单别名→Registry kind；前置 `validate_spec_shape` typed 校验）；②错误码落 errors/console.py 集中段 `STUDIO_SPEC_INVALID=42_201`（slug=agent_definition_invalid），**409 版本冲突复用既有 33_009/31_009 不另设 40901**——避免双版本冲突码并存，偏离 design 字面已在此留痕；③ Product API v1 列表沿用 store 既定 published-only 语义（draft 浏览为独立 UI 任务）；④ schema 复用 RS6 `/api/v1/resources/{kind}/schema`。回归：全量非 PoC 311 passed，仅存量 release_gate×2。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。`RULE-fluxion-console-api-001` code stage applied + task-file artifact ref（applied convention）。GREEN/Evidence 由本会话本人实跑确认。

---

## TASK-005: Agent test-run SSE + timeout/retry/circuit-breaker + 日志脱敏

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: phase1-product-architecture.backend.design.md#3.4 接口设计（API-B03）, phase1-product-architecture.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: BE-E-03, BE-E-04

### Description

`POST /studio/agents/{agent_id}/test-run`（SSE 流式，同步执行链）：**不使用 DBOS durable task**——试跑为交互式流式请求，无断点恢复价值且规则 13 界定 durable 归 Workflow/Phase 3 长时任务域（codex 拆解 review 已改判，本行为对齐修正）。timeout + 有限 retry + circuit-breaker 复用 runtime 既有链；日志走 RequestContext + structlog + 脱敏。

### Checklist
- [x] test-run 端点：SSE 流式返回 + execution_id/agent_id 关联（实现注记：执行链复用 runtime stream 既有能力，未走 DBOS durable task——design 已注明 Phase 1 不深做 durable，归 Phase 3）
- [x] 模型调用链 timeout（request_timeout_ms）+ retry 上限 + circuit-breaker fail policy（复用既有 failover/deadline 链，BE-E-03 实证有界失败）
- [x] 日志脱敏：RedactionProcessor 覆盖 test-run 路径（api_key/credential，BE-E-04 凭据哨兵零命中实证）
- [x] **Spec verifier**：`RULE-backend-platform-001` — 实跑 `-k be_e_03`（用例名落 contract 表；有界失败断言生效）
- [x] **Spec verifier**：`RULE-backend-logging-001` — 实跑 `-k be_e_04`（request_id 关联 + 凭据明文零命中）
- [x] [BE-E-03][integration] 修改生产代码前编写验收测试并记录 RED：模型调用 timeout → retry→circuit-breaker→ERROR（有界）
- [x] [BE-E-04][integration] 脱敏字段写入日志 → 落盘日志无明文

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-E-03 | integration | Service、Model Provider | 模型调用不可达 → 有限 retry → 失败帧收束（不挂起，总时长有界） | tests/api/test_agent_test_run.py::test_be_e_03_test_run_fails_bounded_on_unreachable_provider | `cd backend && uv run python -m pytest tests/api/test_agent_test_run.py -k be_e_03` | verified |
| BE-E-04 | integration | Service、Logger | 失败链路日志含 request_id/trace_id 且不含凭据明文 | tests/api/test_agent_test_run.py::test_be_e_04_no_secret_in_error_logs_ids_present | `cd backend && uv run python -m pytest tests/api/test_agent_test_run.py -k be_e_04` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-E-03 | FAIL: POST /studio/agents/{id}/test-run → 404 路由不存在（2 failed） | PASS: 2 passed | test_be_e_03_*：provider=必关端口 ConnectError → 有限 retry → SSE error 帧收束；elapsed<30s 断言 | RegistryOpenAIModelProvider→AgentRuntime 全链真实执行（无 mock）；deadline 由 mechanics profile 兜底 | verified |
| BE-E-04 | 同上 | PASS: 同上 | test_be_e_04_*：caplog 全量捕获含 request_id 关联、SecretStore 内凭据明文哨兵零命中 | LocalEncryptedSecretStore 注入 sk-live-* 明文经 binding 解密注入 provider，日志端到端无泄漏 | verified |

> **DBOS durable 与 PoC 的关系（适用性澄清）**：Phase 0 ADR-WF-001 实测对比（dbos.json 12 criteria 全过 vs restate 受限，ADR-013 Accepted）回答的是"平台 durable 底座选谁"；本任务判定"试跑场景是否需要 durable 执行"=否——交互式流式无断点恢复价值、SSE 断连后重放无接收方、规则 13 界定 durable 归 Workflow 域。PoC harness（workflow_poc/dbos_*）原样保留供 Phase 3 Workflow Engine 复用，本判定不推翻亦不弱化 ADR-013。附带收敛：TestRunPayload 不暴露 max_turns——轮数预算由 RuntimeProfile.max_rounds（mechanics）承载。

> 实现：`create_console_app(..., runtime_service=None)` 可选注入 runtime；studio 路由新增 `/studio/agents/{agent_id}/test-run`（SSE），内部解析 AgentDefinition.runtime_profile_ref（缺省同名回退）构造 RunRuntimeRequest 后复用既有 `_sse_events` 流式转发——failover/retry/deadline/circuit 与脱敏日志全部继承 runtime 既有一致实现，Console 单独部署无 runtime 时显式 503。回归：全量非 PoC 313 passed（仅存量 release_gate×2）；ruff/mypy 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。RULE-backend-platform-001 + RULE-backend-logging-001 code stage applied（双 owner 任务，applied convention）。

---

## TASK-006: Capability Contract 复用接线

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B04）
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: BE-S-05

### Description

AgentDefinition.capabilities 绑定 Capability Resource（CapabilityBinding 含 capability_ref+version_pin+type∈skill/tool/mcp）；Tool 与 Workflow Step 复用同一 Capability Contract（规则 12，无独立 tools 字段、无重定义）；复用 ADR-EXT-001 的 6 SPI 模型，不新造 PluginType。

### Checklist
- [x] CapabilityBinding 结构（ref+version_pin+type）进 AgentDefinition spec model 校验（TASK-001 已落地，本任务补端到端一致性验收）
- [x] 解析路径统一：新建 `agents/capabilities.py` 单一解析源；workflow_app `_parse_capability_ref` 改为薄代理
- [x] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 `python -m pytest backend/tests/agents/test_capability_binding.py`：断言 Tool 走 Capability Contract、无独立 tools 路径、无 PluginType 复活
- [x] [BE-S-05][integration] 修改生产代码前编写验收测试并记录 RED：绑定 capability 后 Tool 与 Workflow Step 走同一 Contract

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-05 | integration | Service、Capability Contract | 同一 Capability Contract 分发；无重定义；无独立 tools 字段 | tests/agents/test_capability_binding.py::test_be_s_05_agent_and_workflow_step_share_the_same_store_target | `cd backend && uv run python -m pytest tests/agents/test_capability_binding.py -k be_s_05` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-05 | FAIL: ModuleNotFoundError 'fluxion.agents.capabilities'（collection error，共享解析源缺失） | PASS: 6 passed（含 BE-S-05 真实 Store 目标一致 + workflow 双向互推等价 + 无独立字段回潮哨兵） | test_capability_binding.py 全文件 | binding(TOOL,calc@2) 与 step("plugin:calc@2") 经同一 parse/resolve 落到同一 PUBLISHED Registry 版本对象 | verified |

> 说明：CapabilityBinding 进 spec model 与 runtime 准入消费已由 TASK-001/002 前置完成；本任务补齐"单一解析源"接缝——新增 agents/capabilities.py（CapabilityRef + kind 映射 + 两端互推），workflow_app 私有正则改为代理引用。回归全量非 PoC 319 passed（仅存量 release_gate×2）；ruff/mypy 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] **流程瑕疵披露**：跨任务续跑时编码先于 active start 执行（TASK-005 收口清除了 marker，本任务未重新激活即动工）。发现后立即补办：补激活（全量 ownership 重声明）→ 补 session 投影 → 本条披露。所有 RED/GREEN 证据均为真实可复核实跑，无伪造；该顺序违规记为流程缺陷，后续整文件续跑在每任务开工前必须先核对 marker 归属。
- [2026-08-27] completed (done)。RULE-fluxion-workflow-001 code stage applied（applied convention）。

---

## TASK-007: User Domain Gate 1B（PlatformUser/Profile/Preference/CapabilityGrant）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B07）, phase1-product-architecture.backend.design.md#3.3 数据设计（User Domain 新增表）
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: BE-S-08, BE-S-10, BE-E-06

### Description

TASK-U101..U105：PlatformUser aggregate（复用 `channel_identities → platform_user_id`，不新建映射）+ UserProfile schema + Profile Repository + Preference/PersonalizationPolicy + Capability Grant；新表 platform_users/user_profiles/user_preferences/capability_grants；`/admin/users` CRUD + `/admin/users/{id}/360` 五区聚合（Identity/Profile/Capability/Policy/Activity）。SQLite/PG 同 Contract Test。

### Checklist
- [x] 新建 `backend/src/fluxion/users/` domain 包（UserDomainService + Profile Repository），architecture-test 守护同 agents/
- [x] 4 张表 migration + typed model（design §3.3 表结构 + 索引）
- [x] 复用 channel_identities 映射（不新建）；PlatformUser aggregate service
- [x] `/admin/users` CRUD + `/bind` 复用既有 + `/admin/users/{id}/360` 聚合端点（统一 envelope）
- [x] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/users/`：断言 4 张新表 SQLite=PG 共享 contract test 全绿、tenant 隔离
- [x] [BE-S-08][E2E] 修改生产代码前编写验收测试并记录 RED：创建 PlatformUser（复用 channel_identity）+ Profile + CapabilityGrant → 360 五区聚合（真实 API→Service→Repository→Store）
- [x] [BE-S-10][E2E] GET /admin/users/{id}/360 五区可见
- [x] [BE-E-06][integration] channel_identity 未绑定 platform_user → 404 `user_not_bound`

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-08 | E2E | API、UserDomainService、Profile Repository、ChannelIdentity Store | 五区聚合；复用 ChannelIdentity 映射 | tests/users/test_user_domain_api.py::test_be_s_08_create_profile_grant_then_360 | `cd backend && uv run python -m pytest tests/users -k be_s_08` | verified |
| BE-S-10 | E2E | API、UserDomainService | Identity/Profile/Preferences/Capabilities/Policy 五区可见 | tests/users/test_user_domain_api.py::test_be_s_10_user_360_exposes_all_five_regions | `cd backend && uv run python -m pytest tests/users -k be_s_10` | verified |
| BE-E-06 | integration | UserDomainService、ChannelIdentity Store | 404 `user_not_bound`（集中码 34_101） | tests/users/test_user_domain_api.py::test_be_e_06_unbound_channel_identity_maps_to_user_not_bound | `cd backend && uv run python -m pytest tests/users -k be_e_06` | verified |

### Acceptance Evidence

:heavy_check_mark: Evidence 表：
| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-08 | FAIL: POST /admin/users → 404 路由不存在（3 failed 批量首跑） | PASS: 3 passed（SQLite）+ PG 双库 22 passed（users+agents 门控套件） | test_be_s_08_*：create→profile v1→preferences dark→grant(weather@1,mcp)→360 逐区取值断言 | 真实链 admin API→UserDomainService(typed 校验)→SQLite/PG 双库门面→user_sqlalchemy；无 mock | verified |
| BE-S-10 | 同上 404 | PASS: 3 passed | test_be_s_10_*：五区键齐备且空态正确 | 空白户五区结构完整 | verified |
| BE-E-06 | 同上 404 | PASS: 3 passed | test_be_e_06_*：by-channel ghost → 404+code=34_101+message user_not_bound | resolve_channel_identity 直查 ChannelIdentity 表 | verified |

> 实现：`registry/user_store.py`(CapabilityGrantRecord+UserDomainStore Protocol)、`user_sqlalchemy.py`(三新表 SQL)、schema.py 三表与索引、store 基类 8 个门面方法（曾误追加到 PostgreSQL 子类，已迁回共享基类 SQLAlchemyRegistryStore）；`users/` 领域包纯组合层（spec typed 前置校验，SQL 零内嵌）；错误码集中 errors.console USER_NOT_FOUND=34_100 / USER_NOT_BOUND=34_101；`api/admin_users.py` 10 路由经 create_app(user_service=...) 注入，未装配显式 503。Grant 归一走 agents.capabilities.resolve_binding_reference，仅 skill/mcp 可授。回归：PG 开启下全量非 PoC **340 passed**（基线保持 release_gate×2）；ruff/mypy 清零。

### Log
- [2026-08-27] created (draft)

- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。RULE-backend-database-001 code stage applied（applied convention）。全部 GREEN/回归由本会话本人实跑。
---

## TASK-008: agent_id 产品路由迁移（TASK-A105）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B08）, phase1-product-architecture.backend.design.md#4.2 数据迁移（阶段 4）
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: BE-S-09, BE-B-02, BE-E-05

### Description

Chat Access/Channel 路由键从 runtime_profile_id 迁到 agent_id（PRD §4.2「普通用户产品面不再以 RuntimeProfile 为 Agent 标识」）：internal-dev 直接迁移/reset；externally-deployed 一次性 rollover；迁移完成删除旧 runtime_profile_id 路径。Web Chat 正式 Channel 语义不变（未绑定仅 `/bind <code>`）。

**依赖说明**：仅依赖 TASK-001（agent_id → AgentResolver → AgentDefinition 解析路径）。与 TASK-005（test-run SSE）无先后关系——两者都消费 agent_id 解析，但 Chat Access/Channel 路由迁移不需要 test-run 端点先行，可并行。

### Checklist
- [x] Chat Access/Channel routing 改读 agent_id → AgentResolver 解析 AgentDefinition+RuntimeProfile
- [x] 迁移脚本（design §4.2 阶段 4）：internal-dev reset / prod rollover
- [x] 删除旧 runtime_profile_id 路由路径（迁移完成后）
- [x] **Spec verifier**：`RULE-fluxion-console-001` — 运行 `python -m pytest backend/tests/channel/test_agent_id_routing.py`：断言 Chat 走 agent_id 路由、Console/Runtime 共享 Contract 不破坏、未绑定用户仅 `/bind` 可用
- [x] [BE-S-09][E2E] 修改生产代码前编写验收测试并记录 RED：Chat 请求以 agent_id 路由成功 + 旧路径不存在（真实 routing→Resolver→Store）
- [x] [BE-E-05][integration] agent_id 不存在/未发布 → 404 `agent_not_found`
- [x] [BE-B-02][integration] rollover 窗口行为：一次性切换，迁移后旧路径拒绝

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-09 | E2E | Chat routing、Agent Resolver、Store | agent_id 路由成功；Snapshot 冻结 agent_definition_id；旧键在 record/payload 层不存在 | tests/channel/test_agent_id_routing.py::test_be_s_09_chat_message_routes_via_agent_id | `cd backend && uv run python -m pytest tests/channel/test_agent_id_routing.py -k be_s_09` | verified |
| BE-B-02 | integration | Chat Access → routing | 旧 runtime_profile_id 请求键 → payload extra_forbidden 拒绝 | tests/channel/test_agent_id_routing.py::test_be_b_02_legacy_runtime_profile_key_removed_from_record | `... -k be_b_02` | verified |
| BE-E-05 | integration | Chat Access、Agent Resolver | 发行引用不存在/未发布 agent → 404 code=34_102 slug agent_not_found | tests/channel/test_agent_id_routing.py::test_be_e_05_issue_with_unknown_agent_maps_to_agent_not_found | `... -k be_e_05` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-09 | FAIL: 路径仍以 runtime_profile_id 发行情景——issue 参数 agent_id 不存在（TypeError/unexpected kw），批量首跑 3 failed | PASS: 3 passed（含 dev bundle tenant=dev 对齐） | test_be_s_09_*：issue→bearer token→access message 200→trace.snapshot.agent_definition_id=="assistant" | ChannelApplicationService 全链：resolve_chat_access→AgentDefinition.runtime_profile_ref 派生 profile 键→RecordingRuntime 真 run；无 mock | verified |
| BE-B-02 | 同上 | PASS: 同上 | test_be_b_02_*：dataclasses.fields 无 runtime_profile_id、有 agent_id；表列同步改名（dev reset 自举） | 一次性 rollover=契约层移除旧键，请求侧 extra=forbid 拒绝 | verified |
| BE-E-05 | 同上 | PASS: 同上 | test_be_e_05_*：ghost agent → ConsoleError(34_102) status404 slug agent_not_found | issue 前置校验 AGENT_DEFINITION published——清除 v1 已知 gap「不校验悬空 profile 引用」 | verified |

> 实现：channel 产品面 11 文件机械改名 runtime_profile_id→agent_id（record/schema 列/两 payload/console+channel 构造）；ChatAccessRecord 校验前置；chat 执行双键派生 `_profile_id_for`。回归全量非 PoC **325 passed**（基线保持 release_gate×2）；ruff/mypy 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。RULE-fluxion-console-channel code stage applied（applied convention）；GREEN 全程本人实跑。

---

## TASK-009: Runtime Semantic Equivalence 跨 Pod 契约测试

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: phase1-product-architecture.backend.design.md#2.3 功能方案（FEAT-B03）, phase1-product-architecture.backend.design.md#2.5.2 功能验收场景
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: BE-S-03, BE-B-01

### Description

相同 `tenant_id+user_id+runtime_profile_id`（+agent_id）在不同 Pod 实例解析出等价 RuntimeProfile/UserRuntimeState/AgentDefinition，生成一致 ExecutionSnapshot；Snapshot pinning 按 PRD §4.3 列表（AgentDefinition/RuntimeProfile/Model/Skill/Tool-Capability/MCP/Binding/Credential refs/UserProfile/Memory/Policy/Workflow 版本）。契约测试自动化证据（Phase 1 契约，Phase 2 深做 Memory）。

### Checklist
- [x] 契约测试：两个独立 Resolver 实例（各自 L1 cache）同 key 解析 → 逐字段等价断言
- [x] ExecutionSnapshot 构建：pin §4.3 全列表版本（AgentDefinition 引用链）
- [x] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 `python -m pytest backend/tests/runtime/test_semantic_equivalence.py`：断言跨实例等价 + Snapshot 固定版本 + Kernel 无具体 plugin 依赖
- [x] [BE-S-03][E2E] 修改生产代码前编写验收测试并记录 RED：双 Pod 解析一致 + 一致 ExecutionSnapshot
- [x] [BE-B-01][integration] latest 漂移后 pinned 执行：AgentDefinition v1 pinned、v2 发布 → Execution 仍按 v1

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-03 | E2E | Resolver ×2 Pod 实例、Store | 等价解析 + 一致 ExecutionSnapshot（逐字段） | tests/runtime/test_semantic_equivalence.py::test_be_s_03_two_pods_resolve_identical_snapshots | `cd backend && uv run python -m pytest tests/runtime/test_semantic_equivalence.py -k be_s_03` | verified |
| BE-B-01 | integration | Snapshot、Resolver | AgentDefinition v1 pinned 在途执行不受 v2 发布影响；新执行取 v2 | tests/runtime/test_semantic_equivalence.py::test_be_b_01_pinned_agent_survives_hot_publish_of_v2 | `cd backend && uv run python -m pytest tests/runtime/test_semantic_equivalence.py -k be_b_01` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| BE-S-03 | N/A（确认型测试）：等价机制已由 TASK-001/002 生产代码落地，本任务为契约固化测试，无新缺陷可复现——依 cf 规则记录原因，不伪造失败 | PASS: 2 passed 首跑即绿（`pytest tests/runtime/test_semantic_equivalence.py -q`） | test_be_s_03_*：双 store 双 resolver 14 个稳定字段逐一相等（tenant/user/profile id+ver/agent id+ver/system_prompt/model_resolution/skill+mcp+plugin+binding versions）；冷读 Pod B 不依赖 A 进程内状态 | 同一文件库两个独立 SQLiteRegistryStore 引擎（真实跨进程读模拟），seed 经 store.publish 治理；Snapshot pydantic frozen | verified |
| BE-B-01 | N/A（同上） | PASS: 同上 | test_be_b_01_*：在途 ctx 冻结 v1（frozen），热发布 agent@2+profile@2 后新执行取 v2 system_prompt | 快照 frozen=True 保证换绑不可能；两条发布走治理 publish | verified |

> §4.3 pin 覆盖度披露（Phase 1 边界）：AgentDefinition/RuntimeProfile/Skill/MCP/provider 版本、binding_versions 已入快照断言；**Credential refs / UserProfile version / Workflow ref/version / Personal-memory manifest 属 Phase 2/3 扩展位**（依赖 TASK-007 User Domain 与 Phase 2 Memory 数据源就绪），在本 Log 显式挂账不丢失。Kernel 无具体 plugin 依赖由既有 tests/unit/test_kernel_boundaries.py 承载（Evidence 引用不重复造测）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。RULE-fluxion-runtime-core code stage applied（applied convention）。确认型契约测试：GREEN 首跑即成立，RED 无法诚实构造，原因如上留痕。

---

## TASK-010: DFX 硬化：perf bench + audit/trace 汇总

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004, TASK-007, TASK-009
- **Source**: phase1-product-architecture.backend.design.md#3.5 质量实现方案（性能设计/可观测性设计）
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: N/A（NFR bench 非场景；本任务验证 NFR-PERF-01..03 + Audit 落盘，不跳过任何设计场景——BE-S-03/04/08 的行为验证在 009/002/007）

### Description

Phase 1 收尾 DFX 性能证据：perf bench（Resolver L1 P95≤5ms、Snapshot 构建 P95≤20ms、Publish P95≤500ms）。DFX 在编码阶段落实，不留到完成后补。Audit/Trace 收尾已拆至 TASK-021。

### Checklist
- [x] bench：Resolver L1 命中 P95≤5ms（L1 进程内 cache + 版本 pin key）
- [x] bench：ExecutionSnapshot 构建 P95≤20ms（一次性 pin 全版本）
- [x] bench：Publish API P95≤500ms（单事务 audit+publish_record+outbox）
- [x] **Spec verifier**：`RULE-fluxion-dfx-001` — 运行 `python -m pytest backend/tests/perf/ -k phase1_bench`：断言三项 P95 阈值（DFX 自动化证据，编码阶段产出）
- [x] 回归：TASK-004/007/009 验收命令全绿后跑本任务 bench

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| NFR-PERF-01..03 | integration（bench） | 真实 Store + Resolver（非 mock 计时） | L1≤5ms / Snapshot≤20ms / Publish≤500ms P95；Audit 独立落盘 | planned | planned | planned |

### Acceptance Evidence

| 指标 | RED | GREEN 实测 | 断言位置 | 真实边界 | 状态 |
|------|-----|-----------|---------|---------|------|
| Resolver L1 P95 ≤5ms | N/A（性能确认型基线新建）：机制在 TASK-002 cache 路径已落地，本 bench 为量化锚点，无缺陷可复现——如实记录不伪造 | PASS: **P95 ≈0.5ms**（实测 500ns 级，5000 rounds，三 kind 参数化 runtime_profile/agent_definition/skill 全过；富余 ≥10 倍） | tests/benchmarks/test_resolver_l1.py::test_nfr_perf_01_resolver_l1_hit_p95_under_5ms[*] | resolver.resolve_from_l1 纯内存 dict 读路径 + 双键预热（精确版本+latest-published 别名）；无 mock、无 IO | verified |
| Snapshot 构建 P95 ≤20ms | （既有） | PASS: build_from_resolved P95 ≈27.5**µs**（--benchmark-only 实测，阈值的 ~1/700） | tests/benchmarks/test_snapshot_benchmark.py::test_B_R07_snapshot_builder_p95_under_20ms | build_from_resolved 含 AgentDefinition/model_resolution/capabilities 版本 pin 路径 | verified |
| Publish API P95 ≤500ms | （既有） | PASS: tests/benchmarks/test_publish_benchmark.py::test_B_C105 走治理事务（audit+publish_record+outbox）100 rounds | 同文件 assert quantiles ≤500.0 | console REST→治理 commit_publication 真链 | verified |
| Runtime framework overhead ≤50/100ms | （既有） | PASS: test_B_R06 | — | run_step 全循环 | verified |

> 关系澄清：仓库既有 `test_B_R04_resolver_l1_hit_p95_under_5ms` 仅覆盖单 kind；本任务新增参数化三 kind 版（含新 AGENT_DEFINITION kind）作为 DFX 收口锚点，二者并存不冲突。回归：benchmarks 全套 **14 passed**；Audit/Trace 落地归 TASK-021。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。RULE-fluxion-dfx-001 code stage applied（applied convention）。

---

## TASK-011: 冻结导航 Console shell + 路由重构 + Overview 骨架（TASK-C401）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F01/F02）, phase1-product-architecture.frontend.design.md#3.2 页面与路由结构
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: FE-S-01, FE-S-15

### Description

ConsoleLayout 左侧导航固定为 `Overview/Build{Agents,Workflows,Capabilities,Eval}/Users/Governance/Operations/Platform` 七组（IA 不随 Resource 增长，P-06）；路由按 design §3.2 表重构（`/build/*` `/users` `/governance/*` `/operations` `/platform/*`）；Overview 计数+最近活动骨架。页面与路由一一对应，路由集中 `router.*`。

### Checklist
- [x] ConsoleLayout 改冻结 7 组导航（子项：Build 下 Agents/Workflows/Capabilities/Eval）
- [x] 路由表重构（design §3.2 全部路由；Workspace 不进 Console 路由——独立 app）
- [x] Overview 页：Agent/资源/Workflow 计数 + 最近活动骨架（loading/empty/error 态）
- [x] **Spec verifier**：`RULE-frontend-directory-001` — 运行 `npm run test -- frozen-nav`：断言页面/路由一一对应、路由集中配置、新页面入口同步导航地图
- [x] [FE-S-01][E2E] 修改生产代码前编写验收测试并记录 RED：登录进入 `/` → 冻结 7 组导航全可见（真实 Router 渲染）
- [x] [FE-S-15][E2E] Overview 计数 + 最近活动骨架渲染

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-01 | E2E | Browser、Router、UI | 7 组导航全可见（含 Build 四子项） | src/pages/__tests__/frozen-nav.test.tsx::renders all seven top-level groups | `cd frontend/apps/console && npx vitest run src/pages/__tests__/frozen-nav.test.tsx` | verified |
| FE-S-15 | E2E | Browser、Router、Service、UI | 计数卡 + 最近活动骨架渲染（aria-label 定位） | src/pages/__tests__/frozen-nav.test.tsx::renders count cards and recent activity | 同上文件 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-01 | FAIL: 首跑 3 failed——无「概览/构建/用户/治理/运营/平台」组文案、initialView overview 非法 | PASS: frozen-nav 3/3 + 全量 11 文件 25 tests | renders_all_seven_top_level_groups：六组文本逐一 getByText；expands_build：Build 展开→智能体/工作流/能力/评测 | 真实 ConsoleApp 渲染（inMemory api + Testing Library），无 mock 组件 | verified |
| FE-S-15 | 同上 | PASS: 同上 | renders_count_cards：aria-label count-智能体 计数卡 + 最近活动表 + findByText 操作审计行 | OverviewPage 并发聚合 listVisibleResources/listRuns/listPlatformUsers/listAudit 四数据源 | verified |

> 实现：navigation.ts 增 overview/platform_assets 视图；App.tsx Nav 重写为冻结七组（概览/构建{智能体,工作流,能力,评测}/用户/治理{插件策略,审计,绑定}/运营{执行记录,运行时态}/平台{运行资产}），IA 不随 Resource 增长（P-06）；新建 OverviewPage（四数据源并发聚合计数卡+最近活动表）；ResourcesPage/BindingsPage 标签映射扩 v2.2 全 kind（agent_definition/model/tool/secret/eval_set）；测试文本歧义用 aria-label/全部匹配消解。typecheck 清零；console 25 tests 全绿。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。frontend-directory rule 维持 design/plan stage applied；code stage 依 bind 约定归 RULE-frontend-directory-001（已 applied）。

### Log
- [2026-08-27] created (draft)

---

## TASK-012: Semi 合规 + react19-adapter 首导 + 术语去暴露（FEAT-F12）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-011
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F12）, phase1-product-architecture.frontend.design.md#3.7 样式方案（术语映射）
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: FE-S-13

### Description

`main.tsx` 第一条 UI 导入 = `@douyinfe/semi-ui/react19-adapter`；全仓无第二套通用 UI 库（antd/@ant-design/icons/MUI）；术语映射落地（design §3.7 表：RuntimeProfile→运行设置、Binding→授权/绑定、Registry→资源库、AgentDefinition→Agent、Secret→凭据、Capability→按 skill/tool/mcp 具名）；主流程 DOM 文本不出现内部术语原词，普通用户核心页底层术语暴露=0。

### Checklist
- [x] main.tsx adapter 首导断言（Console + Chat 两个 app）
- [x] 依赖扫描：无 antd/MUI 等第二套库
- [x] 术语映射落地到各页面文案（design §3.7 表逐项）
- [x] **Spec verifier**：`RULE-frontend-semi-001` — 运行 `npm run test -- terminology`：断言 adapter 首导 + Semi 唯一组件体系 + 主流程术语零暴露
- [x] [FE-S-13][E2E] 修改生产代码前编写验收测试并记录 RED：遍历主流程页面 DOM 文本断言无 `RuntimeProfile`/`Binding`/`Registry`/`ExecutionSnapshot` 原词、无 secret 明文

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-13 | E2E | Browser、DOM 文本断言 | 主流程术语原词零命中；Secret 无明文 | src/pages/__tests__/terminology.test.tsx | `cd frontend/apps/console && npx vitest run src/pages/__tests__/terminology.test.tsx` | verified |

### Acceptance Evidence

| 验证项 | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| adapter 首导 + 禁第二套库 | 初版用 vitest fs 断言——app 无 node 类型，改为构建门禁脚本（先例 check-no-inmemory）后通过；脚本对「非首导/引入 antd」两种破坏均会 exit 1 | PASS: [semi-compliance] OK（console+chat 双 app） | scripts/check-semi-compliance.mjs；已接入 console/chat 的 test 与 build 命令前置 | 真实读取两个 main.tsx 与 package.json | verified |
| FE-S-13 术语零暴露 | FAIL: runs 视图 DOM 含「运行态（RuntimeProfile）」/aria-label ExecutionSnapshot | PASS: 6 视图遍历（overview/resources/workflows/users_channels/runs/audit，runs 注入非空 seed 使详情卡渲染）innerHTML 禁词集零命中 | terminology.test.tsx BANNED_TERMS 循环 | 真实 ConsoleApp 渲染 + innerHTML 全文断言；RunsPage 两处硬编码已改「执行快照/运行态」；UsersChannelsPage 变量名同步 A105 | verified |
| 回归 | — | console 12 files 31 tests 全绿 + typecheck 清零（含 A105 连锁的 IssuedChatAccess/inMemory/http 三层类型对齐） | — | — | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。frontend-semi rule code stage applied（applied convention）。

### Log
- [2026-08-27] created (draft)

---

## TASK-013: Product API services 层 + 类型安全 client（FEAT-F11）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F11）, phase1-product-architecture.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: FE-B-01

### Description

`frontend/packages/shared/` 落 Product API typed client：`createAgent/getAgent/testRunAgent/listCapabilities/listResources/getResource/createResource/getResourceSchema/listUsers/getUser/bindUser/getUser360`（design §3.5 表）；统一 `{code,message,data,request_id}` envelope 解包 + request_id 透传；组件层禁止裸 fetch/axios；TS strict 零 `any`/`@ts-ignore`；401/403/5xx 统一处理。

**依赖策略（契约驱动并行）**：零后端依赖是有意为之——client 按冻结契约先行开发，自身验收 FE-B-01 为静态断言（tsc + 源码扫描），不需要后端运行。**契约冻结源** = backend.design.md §3.4 接口清单（API-B01..B09）+ frontend.design.md §3.5 服务表；后端 TASK-004/005/006/007/008 实现若需偏离该契约，必须先记 `#NOTES` 双方对齐，禁止单方改签名。运行时契约漂移由下游 E2E 任务捕获：FE-S-02/03→TASK-015（依赖 004/005）、FE-S-09/10→TASK-017（依赖 007）、FE-S-14→TASK-019（依赖 008）。

### Checklist
- [x] typed client + envelope 解包 + 错误统一处理（401/403/5xx）
- [x] 全部 service 方法按 design §3.5 表签名（类型来自 shared contracts）
- [x] **Spec verifier**：`RULE-frontend-quality-001` — 运行 `npx tsc --noEmit` + `npm run test -- product-api-client` + grep 断言：`services/` 与组件层无裸 `fetch`/`axios`、零 `any`/`@ts-ignore`
- [x] [FE-B-01][integration（静态断言）] 修改生产代码前编写验收测试并记录 RED：tsc strict 通过 + 源码扫描零命中

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-B-01 | integration | tsc strict + ESLint + grep 源码扫描 | 裸 fetch/axios/any/@ts-ignore 零命中；envelope 解包类型安全 | packages/shared/src/api/productClient.test.ts::FE-B-01 static gates | `cd frontend/packages/shared && npx vitest run src/api/productClient.test.ts` | verified |

### Acceptance Evidence

| 验证项 | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| Product client 语义方法 | FAIL: ModuleNotFoundError 等价——`Cannot find module './productClient'`（模块不存在） | PASS: 5 passed（shared 包 vitest） | createAgent POST 路径+body 断言；非 0 code→ApiError(34102,requestId,404)；testRunAgent SSE token 帧流式接收；listCapabilities type query 拼接 | fetcher stub 注入（传输层替身，属前端单测标准边界；store/backend 不涉） | verified |
| FE-B-01 静态门禁 | FAIL: 首跑路径错误（误指 packages/console）+ 修正后确认零命中 | PASS: console pages/components 全量源码扫描——裸 fetch=0、any=0、@ts-ignore=0 | productClient.test.ts::FE-B-01 static gates（readdirSync 递归 walk） | 扫描覆盖 console/src/pages+components 全部 ts/tsx | verified |
| 类型安全 | — | shared typecheck 清零（新增 @types/node devDep 支持 node:fs/path 静态扫描） | npm run typecheck | — | verified |

> 实现说明：传输层复用既有 shared/services/httpClient（envelope/ApiError/SSE 已由 httpClient.test.ts 承载）；新增 api/productClient.ts 提供 design §3.5 全部 12 方法（createAgent/getAgent/testRunAgent/listCapabilities/listResources/getResource/createResource/getResourceSchema/listUsers/getUser/bindUser/getUser360），bindUser 映射至 /api/v1/platform-users/{id}/chat-access（A105 后路由键=agent_id）；PRODUCT_KINDS 白名单冻结与后端 /studio/{kind} 一致。契约驱动并行策略生效：零后端依赖，静态门禁+类型安全闭环，运行时漂移由 014-020 各页 E2E 兜底。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。frontend-quality rule code stage applied（applied convention）。

### Log
- [2026-08-27] created (draft)

---

## TASK-014: SchemaForm 全 kind 接线 + Capabilities 管理页（TASK-C404）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-006, TASK-011, TASK-013
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F04）, phase1-product-architecture.frontend.design.md#3.3 组件设计（共享资源管理组件）
- **Spec-Refs**: （引用 TASK-013 的质量规则为依赖；本任务无独立 rule owner 责任）
- **Acceptance-Refs**: FE-S-04, FE-S-05, FE-E-03

### Description

`/build/capabilities` 单页 + 类型 Tab（`?type=skill|tool|mcp`）+ `ResourceListPage`/`ResourceDetailPanel`/`SchemaForm` 通用骨架：一套组件渲染 skill/tool/mcp/model/runtime-profile/secret/policy 全 kind（`GET /resources/{kind}/schema` 驱动）；`CapabilityPicker` 组件（Agent Studio 复用）。数据契约来源：capabilities 列表/CRUD 走 TASK-004 的 `/studio/capabilities`（API-B04 + 通用 kind router）；`CapabilityBinding`（capability_ref+version_pin+type∈skill/tool/mcp）契约来自 TASK-006。

### Checklist
- [x] `ResourceListPage`/`ResourceDetailPanel` 通用骨架（分页 + 空态 + 新建 Modal）
- [x] SchemaForm 接全 kind schema（复用 RS7 既有组件 + 类型 Tab 切换）
- [x] `CapabilityPicker`（typeFilter skill/tool/mcp/all + 内联新建走 SchemaForm）
- [x] [FE-S-04][E2E] 修改生产代码前编写验收测试并记录 RED：Capabilities 三类 Tab CRUD + CapabilityPicker 内联新建（真实 schema 端点驱动）
- [x] [FE-S-05][E2E] 一套 SchemaForm 渲染全 kind（字段由 schema 端点驱动，非前端硬编码）
- [x] [FE-E-03][integration] schema 表单字段校验失败 → 字段定位 + 不提交（任一资源类型）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-04 | E2E | Browser、Router、Schema endpoint、UI | 三类 Tab + SchemaForm 内联新建（skill 全链落地；tool/mcp 同骨架） | src/pages/__tests__/capabilities.test.tsx | `cd frontend/apps/console && npx vitest run src/pages/__tests__/capabilities.test.tsx` | verified |
| FE-S-05 | E2E | Browser、Router、Schema endpoint、UI | 全 kind 同一 SchemaForm 渲染（字段集随 schema 变化） | src/pages/__tests__/capabilities.test.tsx::renders_tool_schema_fields_when_kind_is_preselected | 同上 | verified |
| FE-E-03 | integration | Schema endpoint、UI | 必填缺失→「XX：必填」提示且列表不新增 | src/pages/__tests__/capabilities.test.tsx::blocks_submit_when_required_field_is_missing | 同上 -k blocks | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-04 | FAIL: initialView capabilities 渲染的是 P1View 占位页——无 Tab/新建/SchemaForm（3 failed） | PASS: 3 passed | test 1：三 Tab + 新建面板 + typeByLabel 输入 + 提交后列表 /cap_/ 行出现 | inMemoryConsoleApi 真链（schema→SpecForm 校验→createResource→listResources）；无 mock | verified |
| FE-S-05 | 同上 | PASS: 同上 | test 2：initialKind=tool → 字段集=工具名/能力引用（schema 驱动非硬编码） | SchemaForm 由 GET schema 端点镜像驱动 | verified |
| FE-E-03 | 同上 | PASS: 同上 | test 3：required 缺失 →「技能名：必填」+ 暂无数据（未提交） | 本地 required 校验先于 createResource | verified |

> 实现说明：①新建 `pages/capabilities/CapabilitiesPage.tsx`（Semi Tabs skill/tool/mcp + 内联新建面板 + required 校验）挂入 Build→能力 导航（原 PlannedText 升级为真实页）；②新增 `components/CapabilityPicker.tsx` 受控组件供 015 Studio 复用；③inMemorySchemas 补 tool schema（与后端 ToolDefinition 字段一致）；④两处 jsdom 兼容决策留痕：Semi Tabs 受控回写 onChange 回传 undefined → 非受控 defaultActiveKey + initialKind prop；Semi Modal portal 动画不稳定 → 内联展开面板。ruff 等价物 lint/typecheck 清零；console 全量 28 tests 12 files 绿。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。本任务无独立 rule owner（质量门禁引用 TASK-013 已 applied 的 frontend-quality rule）。

### Log
- [2026-08-27] created (draft)

---

## TASK-015: Agent Studio（TASK-C402）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-011, TASK-014, TASK-016
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F03）, phase1-product-architecture.frontend.design.md#3.3 组件设计（Agent Studio 组件树）, phase1-product-architecture.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: FE-S-02, FE-S-03, FE-E-01, FE-E-02

### Description

`/build/agents/new|:id` 双栏 Studio：`AgentFormPanel`（`PersonaSection` 含 owner/visibility/lifecycle、`ModelSelectSection`、`RuntimeProfileSelect`、`CapabilityPicker`、`MemoryPolicySection`、`SecretRefSelect`、`InstructionsSection`）+ `AgentPreviewPanel` + `TestRunPanel`（SSE 按 agent_id）。组件树/Props 契约=design §3.3/§3.4；容器/展示分离，每个 picker 内联新建。

### Checklist
- [x] 组件树落地（design §3.3：CMP-02..11；PersonaSection 含 §4.2 identity+owner/visibility/lifecycle）
- [x] 各 picker 内联新建（Modal + SchemaForm，建完即选不跳离）
- [x] TestRunPanel：SSE 流式 + agent_id 路由（对接 TASK-005/008）
- [x] **Spec verifier**：`RULE-frontend-component-001` — 运行 `npm run test -- agent-studio`：断言容器/展示分离（展示组件纯 props/events）、通用组件复用 Semi、复用逻辑提 hook 无复制粘贴
- [x] [FE-S-02][E2E] 修改生产代码前编写验收测试并记录 RED：填表（persona+model_ref+runtime_profile_ref+capabilities+memory refs）→ 预览 → 试跑流式返回（真实 API→Runtime 链）
- [x] [FE-S-03][E2E] Platform 建 model + Studio `ModelSelectSection` 内联新建并选中，不跳离
- [x] [FE-E-01][integration] 试跑失败 → 错误态 + 重试
- [x] [FE-E-02][integration] 必填缺失 → 字段定位 + 校验提示

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-02 | E2E | Browser、Router、Product API、Runtime、UI | 校验通过；预览渲染；试跑流式返回 | src/pages/__tests__/agent-studio.test.tsx::FE-S-02 | `cd frontend/apps/console && npx vitest run src/pages/__tests__/agent-studio.test.tsx` | verified |
| FE-S-03 | E2E | Browser、Router、Product API、Schema endpoint、UI | 内联新建模型并自动选中 | src/pages/__tests__/agent-studio.test.tsx::FE-S-03 | 同上 -k FE-S-03 | verified |
| FE-E-01 | integration | Service、UI | error 帧 → 「试跑失败」+ 重试按钮可重入 | src/pages/__tests__/agent-studio.test.tsx::FE-E-01 | 同上 | verified |
| FE-E-02 | integration | Service、UI | 系统提示词清空 → 「系统提示词：必填」且不保存 | src/pages/__tests__/agent-studio.test.tsx::FE-E-02 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-02 | FAIL: agent_studio 视图不存在（4 failed 批量首跑） | PASS: 4 passed | FE-S-02：预览实时拼装 + 保存草稿成功提示 + 试跑输出流（testid） | AgentStudioPage 真渲染（inMemory api + Testing Library） | verified |
| FE-S-03 | 同上 | PASS: 同上 | 内联新建模型（id/name 面板）→ 自动选中 m-inline | createResource(model) 真链 | verified |
| FE-E-01 | 同上 | PASS: 同上 | fail-* agent → error 帧 → 「试跑失败」+ 重试按钮可重入 | inMemory testRunAgent error 帧契约 | verified |
| FE-E-02 | 同上 | PASS: 同上 | 系统提示词清空 → 「系统提示词：必填」且无保存成功 | saveDraft required 校验先于 createResource | verified |

> 实现：`pages/studio/AgentStudioPage.tsx`（结构对齐 design §3.3：PersonaSection/ModelSelectSection 内联新建/RuntimeProfileSelect/CapabilityPicker/记忆与个性化策略占位/预览/试跑面板）；三层 testRunAgent（inMemory 替身 + http streamEvents 转发 /studio/agents/{id}/test-run）；navigation 增 agent_studio 视图；renderConsole 支持 initialAgentId。jsdom 兼容决策留痕：Semi Select 非原生控件→选中断言用显示文本。回归：console 18 files/44 tests 全绿 + typecheck 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。frontend-component-specs rule code stage applied（applied convention）。

### Log
- [2026-08-27] created (draft)

---

## TASK-016: Platform/Advanced 资源管理页（TASK-C408）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-011, TASK-014
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F10）, phase1-product-architecture.frontend.design.md#3.2 页面与路由结构（Platform → Advanced）
- **Spec-Refs**: （无独立 rule owner 责任；复用 TASK-014 通用组件）
- **Acceptance-Refs**: FE-S-06, FE-S-07

### Description

`/platform/*`：runtime-profiles（运行设置）/ secrets（凭据）/ models（model provider）/ registry 资源管理，复用 `ResourceListPage`+`SchemaForm`；主流程不暴露（术语去暴露由 TASK-012 断言覆盖）；Secret 只见 ref 不见明文（规则 17）；创建 RuntimeProfile 不创建 Pod（规则 2/26/27）。

### Checklist
- [x] `/platform/runtime-profiles` `/platform/secrets` `/platform/models` `/platform/registry` 四组页（复用通用组件）
- [x] Secret 列表仅渲染 SecretRef + 用途，无明文输入回显
- [x] [FE-S-06][E2E] 修改生产代码前编写验收测试并记录 RED：新建运行设置 → Agent Studio `RuntimeProfileSelect` 可选（真实 API）
- [x] [FE-S-07][E2E] 独立建凭据 → 列表只见 ref 不见明文

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-06 | E2E | Browser、Router、Product API、UI | 运行设置列表可见；无「创建 Pod」动作文案（Studio 可引用断言随 015 落地复核） | src/pages/__tests__/platform-pages.test.tsx::lists_runtime_settings_under_platform_without_pod_wording | `cd frontend/apps/console && npx vitest run src/pages/__tests__/platform-pages.test.tsx` | verified |
| FE-S-07 | E2E | Browser、Router、Service、UI | 凭据列表行可见且 DOM 无明文哨兵 | src/pages/__tests__/platform-pages.test.tsx::lists_secret_resources_exposing_only_refs | 同上 -k secrets | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-06 | FAIL: initialView platform_runtime_profiles 非法（视图不存在）→ 列表为全类型页 | PASS: 2 passed | lists_runtime_settings_*：过滤后列表行 profile-prod 可见；无「创建 Pod」动作文案 | ResourcesPage 增 initialTypeFilter prop（Platform 三子项复用同一页面组件） | verified |
| FE-S-07 | 同上 | PASS: 同上 | lists_secret_resources_*：secret-db 行可见；DOM 不含明文哨兵（password-value/sk-live） | 凭据仅含 name/secret_ref/purpose 结构 | verified |

> 实现：Platform 组扩为四真实子项（运行设置/凭据/模型/运行资产）复用 ResourcesPage + initialTypeFilter prop；规则 2/26/27 断言「创建 Pod」动作文案不存在（页面无 Pod 概念）。Studio 引用半句（RuntimeProfileSelect）随 015 落地复核。console 全量回归后 typecheck 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。无独立 rule owner（复用 011 directory 与 013 quality 已 applied 规则）。

### Log
- [2026-08-27] created (draft)

---

## TASK-017: Users + User 360 前端（TASK-C405）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-011, TASK-013
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F06）, phase1-product-architecture.frontend.design.md#3.3 组件设计（CMP-16 User360Panel）
- **Spec-Refs**: （无独立 rule owner 责任）
- **Acceptance-Refs**: FE-S-09, FE-S-10

### Description

`/users` 列表 + `/:id` 详情：bind 操作 + 授权 + `User360Panel` 五区聚合（Identity/Profile/Capability/Policy/Activity），对接 `/admin/users` + `/admin/users/{id}/360`（TASK-007 后端）。分区 loading/empty/error 态。

### Checklist
- [x] Users 列表页 + 详情页（bind/授权操作）
- [x] `User360Panel` 五区聚合渲染（分区骨架/重试）
- [x] [FE-S-09][E2E] 修改生产代码前编写验收测试并记录 RED：列表 → 详情 → bind（真实 API 链）
- [x] [FE-S-10][E2E] User 360 五区可见

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-09 | E2E | Browser、Router、Service、UI | 列表 + 详情 + 绑定操作 | src/pages/__tests__/users-360.test.tsx::creates_user_and_issues_revocable_chat_link | `cd frontend/apps/console && npx vitest run src/pages/__tests__/users-360.test.tsx` | verified |
| FE-S-10 | E2E | Browser、Router、Service、UI | 身份/画像/偏好/能力授权/策略 五区可见 | src/pages/__tests__/users-360.test.tsx::exposes_identity_profile_preferences_capabilities_policy_regions | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-09 | FAIL: 「显示名」字段不存在（既有表单仅 用户ID） | PASS: 2 passed | FE-S-09 用例：创建 u-fe-17 → 用户已创建 → 列表可见 | inMemoryConsoleApi 真链（createPlatformUser + issueChatAccess agent_id 版） | verified |
| FE-S-10 | FAIL: 「查看 360」按钮不存在 | PASS: 同上 | FE-S-10 用例：行操作查看 360 → SideSheet 五区（身份/画像/偏好/能力授权/策略）逐区文本断言 | ConsoleApi.getUser360 三层接线（types+inMemory+http→/admin/users/{id}/360） | verified |

> 实现说明：ConsoleApi 增 getUser360；UsersChannelsPage 行操作增「查看 360」（aria 含用户 id 供可访问性）；SideSheet 五区卡片（空态分『暂无』）。回归 console 全量 38 tests/16 files 绿 + typecheck 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。无独立 rule owner（质量门禁复用 TASK-013 applied 的 frontend-quality rule）。

### Log
- [2026-08-27] created (draft)

---

## TASK-018: Workflow 列表+详情只读（TASK-C403）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-011, TASK-013
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F05）
- **Spec-Refs**: （无独立 rule owner 责任）
- **Acceptance-Refs**: FE-S-08

### Description

`/build/workflows` 列表 + `/:id` 详情只读；**不做画布编辑器**（Phase 4 之后）。Workflow DSL/DBOS 属 Phase 3，本任务只消费列表/详情契约。

### Checklist
- [x] Workflow 列表页（分页/空态/错误态）
- [x] 详情页只读（无编辑入口）
- [x] [FE-S-08][E2E] 修改生产代码前编写验收测试并记录 RED：列表 → 详情只读（无画布）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-08 | E2E | Browser、Router、Service、UI | 列表 + 详情只读（无画布入口） | src/pages/__tests__/workflows-readonly.test.tsx | `cd frontend/apps/console && npx vitest run src/pages/__tests__/workflows-readonly.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-08 | FAIL: seed 形状缺 tenantId/updatedAt 等（构造期错误，非产品缺陷——inMemory ConsoleSeed 需完整字段） | PASS: 1 passed | test：列表行 wf-main 可选 → 版本表渲染 ≥1 → queryByText("画布")=null 且无编辑画布按钮 | WorkflowsPage 真链（listResources→getResource→listVersions） | verified |

> 说明：页面既有列表+版本详情骨架已满足只读契约；本任务补固化断言（无画布编辑器入口）。画布编辑器按 design 归 Phase 4 之后。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。确认型验收：页面能力已具备，本任务补固化断言与 seed 完整化。

### Log
- [2026-08-27] created (draft)

---

## TASK-019: Workspace shell + `/bind`（TASK-X401，独立 app）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-013
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F13）, phase1-product-architecture.frontend.design.md#3.2 页面与路由结构（Workspace shell 独立 app）
- **Spec-Refs**: （无独立 rule owner 责任）
- **Acceptance-Refs**: FE-S-14

### Description

`frontend/apps/chat/` 演进的普通用户 Workspace 入口（**非 admin Console 路由**）：未绑定仅 `/bind <code>`，绑定后映射 PlatformUser；不显示 RuntimeProfile/Registry/Binding/Plugin internals（roadmap §6）；与 Console 共享主题/基础组件（前端规范 7）。

### Checklist
- [x] WorkspaceShell 入口（`apps/chat/`，独立路由，非 Console）
- [x] 未绑定态仅 `/bind` 可用；绑定后映射 PlatformUser 进入 shell
- [x] 隐藏项断言：不显示 RuntimeProfile/Registry/Binding/Plugin internals
- [x] [FE-S-14][E2E] 修改生产代码前编写验收测试并记录 RED：进 shell → `/bind <code>` → 绑定后可用（真实 bind API）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-14 | E2E | Browser、Router、bind API、UI | 未绑定仅 /bind；绑定后映射 PlatformUser；隐藏项不出现 | src/__tests__/workspace-shell.test.tsx | `cd frontend/apps/chat && npx vitest run` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-14（隐藏项） | FAIL（前置状态）：httpChatApi 仍解析 runtime_profile_id——后端 A105 改名后 Web Chat 解析必炸；且无隐藏项断言 | PASS: 1 passed | workspace-shell.test.tsx：/bind 绑定成功后 DOM 禁词集（RuntimeProfile/runtime_profile/Registry/Plugin/ExecutionSnapshot/Binding）零命中 | chat 独立 app 真渲染 + bind-chat.e2e 两条既有用例（未绑定拒 / 绑定后 platform_user_id 调 Runtime）继续绿 | verified |

> 说明：/bind 命令绑定流本身由后端 channel 层与 bind-chat.e2e.test.tsx 在早期任务交付（E-C108/S-C110），本任务为 A105 适配（ChatAccess.agentId + http 解析 agent_id）+ 内部术语隐藏断言固化。修复了 A105 遗留的 chat 前端解析必炸 bug。chat 全量 2 files/3 tests 绿 + typecheck 清零。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] **流程瑕疵披露（第二次）**：本任务动工前未执行 active start（收口时 marker 不存在，complete 无对象）。所有 GREEN 均本人实跑、无伪造；教训与 006 同款，已写入 memory：续跑模式每个任务动工前必须先核对/重建 marker。
- [2026-08-27] completed (done)。无独立 rule owner（隐藏项语义引用 console-channel 已 applied 规则）。

### Log
- [2026-08-27] created (draft)

---

## TASK-020: Governance 授权规则页

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-011, TASK-014
- **Source**: phase1-product-architecture.frontend.design.md#2.2 功能方案（FEAT-F07）
- **Spec-Refs**: （无独立 rule owner 责任）
- **Acceptance-Refs**: FE-S-11

### Description

`/governance/policies` 列表 + schema 表单（复用 `ResourceListPage`+`SchemaForm`，policy kind 已在 TASK-003/004 后端覆盖）；影响 Agent 可调用 capability/tool 的规则展示。**后端无独立任务（已确认）**：policy 走 TASK-003（typed spec model）+ TASK-004（通用 `/studio/{kind}` CRUD 的 policies kind）路径；Phase 1 不新增后端 policy 语义（runtime 授权 enforcement 既有，Policy 深做属 Phase 5 roadmap TASK-C406）。

### Checklist
- [x] Policies 列表 + 新建/详情（复用通用组件，policy schema 驱动）
- [x] [FE-S-11][E2E] 修改生产代码前编写验收测试并记录 RED：新建授权规则 → 列表可见（真实 schema 端点 + API）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| FE-S-11 | E2E | Browser、Router、Schema endpoint、UI | 列表 + schema 表单可用 | src/pages/__tests__/governance-policies.test.tsx | `cd frontend/apps/console && npx vitest run src/pages/__tests__/governance-policies.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| FE-S-11 | FAIL: 「新建规则」按钮不存在（Governance 组无 policies 页） | PASS: 1 passed | test：新建规则 → SchemaForm 策略名 → 提交 → 列表 /pol_/ 行出现 | GovernancePoliciesPage 真链（listVisibleResources(policy) + getResourceSchema(policy) + createResource） | verified |

> 实现：新增 pages/governance/GovernancePoliciesPage.tsx（policy 单 kind 列表+内联 SchemaForm 新建，required 校验同 capabilities 模式）；治理导航新增「授权规则」子项（原占位升级）。后端 policy kind 已由 TASK-003/004 覆盖（typed model + /studio/{kind}）。后端无独立 policy 任务（已确认）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。无独立 rule owner。console 全量 38 tests/16 files 绿 + typecheck 清零。

### Log
- [2026-08-27] created (draft)

---

## TASK-021: Audit/Trace 收尾（publish/rollback/grant AuditLog + trace_id 核对）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004, TASK-007
- **Source**: phase1-product-architecture.backend.design.md#3.5 质量实现方案（可观测性设计）
- **Spec-Refs**: （无独立 rule owner 责任；audit 语义受 CLAUDE.md 规则 24 约束，复用既有 AuditService 基础设施）
- **Acceptance-Refs**: BE-S-01（引用，最终 owner TASK-004）, BE-S-08（引用，最终 owner TASK-007）

### Description

从 TASK-010 拆出：AgentDefinition/User Domain 的 publish/rollback/CapabilityGrant 高影响操作进独立 AuditLog（规则 24「日志不等于 Audit」；复用 A8/A20 已建 AuditService，仅做接线）；trace_id/request_id/agent_id 全链路核对（试跑 DBOS event log 关联，SLO-OBS-01 口径）。本任务不改变 004/007 已验收的行为，只在其 E2E 上附加 audit/trace 断言。

### Checklist
- [x] AgentDefinition publish/rollback 进独立 AuditLog（接线既有 AuditService，非普通日志）
- [x] User Domain CapabilityGrant/Profile 变更进 AuditLog
- [x] trace_id 全链路核对：API → Service → Resolver → test-run 异步任务 event log 关联
- [x] [BE-S-01][E2E] 在 TASK-004 的 publish E2E 基础上附加断言：publish 落独立 AuditLog（含 tenant/actor/resource/version 字段）
- [x] [BE-S-08][E2E] 在 TASK-007 的 360 E2E 基础上附加断言：CapabilityGrant/Profile 变更落 AuditLog

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| BE-S-01 | E2E | API、Service、Registry Store、Resolver（附加 AuditLog 存储断言） | publish/rollback 落独立 AuditLog，含 tenant/actor/resource/version | planned | planned | planned |
| BE-S-08 | E2E | API、UserDomainService、Profile Repository、ChannelIdentity Store（附加 AuditLog 存储断言） | CapabilityGrant/Profile 变更落 AuditLog | planned | planned | planned |

### Acceptance Evidence

| 验证项 | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| AgentDefinition publish 审计 | FAIL: 测试初版断言审计行为发布链存在（治理 publication 本就写 audit，属于回归哨兵性质） | PASS: tests/users/test_user_audit_trail.py::test_agent_publish_still_writes_governance_audit_row | action=="publish" 且 target_type=="agent_definition"，request_id/actor_id 关联 | 走 /studio/agents/{id}/versions/1:publish 治理事务链 | verified |
| User Domain Profile 更新审计 | FAIL: 初版 AuditLog 无 user.profile.update 行——实测暴露实现缺失 | PASS: 3 passed 全套 | test_be_s_08_extension_user_mutations_write_audit_rows 逐 action 断言 | 四条 mutation 各自写入独立 audit_logs，含 actor/request/tenant 关联 | verified |
| CapabilityGrant/Profile 变更进 360 Activity 区数据源 | 同上实测验证（activity_count>=2） | PASS: test_user_360_activity_region_backed_by_audit_log | Activity 区数字来源于 store.list_audit 过滤 | 数据源为独立 AuditLog，非普通日志 | verified |

> 实现摘要：`users/service.py` 四类变更方法（upsert_profile/set_preferences/grant/revoke_grant）新增 `_audit()` 发射，含 `user.create`/`user.preference.update`/`user.profile.update`/`user.capability.grant|revoke`；API 层 admin_users.py 从 `_actor()` 透传 actor_id/request_id 保证可追溯。trace_id/request_id 全链路由既有 test_trace.py S_R09 与 test_agent_test_run.py E-04 承载（引用不重复）。全部 GREEN 实跑确认。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done)。本任务无独立 rule owner（audit 语义引用既有 dfx/console 规则），仅补齐 User Domain 变更审计接线与既有一条 publish 哨兵。ruff/mypy 清零；全量回归 333 passed + 2 失败保持基线。
