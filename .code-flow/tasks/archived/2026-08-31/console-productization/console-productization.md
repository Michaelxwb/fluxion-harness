# Tasks: console-productization

- **Source**: .code-flow/tasks/2026-08-31/console-productization/（console-productization.backend.design.md + console-productization.frontend.design.md）
- **Created**: 2026-08-31
- **Updated**: 2026-09-01

## Proposal

将 Fluxion Console 从「Resource/API 管理界面」升级为 Agent 产品控制台。先修后端领域事实源（消灭 `MODEL + PLUGIN(model_provider)` 双事实源、恢复 `UserGrant ∩ AgentAllowlist ∩ TenantPolicy` 三维能力治理、恢复 Skill baseline+extension 语义、固定术语、保留 Approval gate），再重构 Console IA 与 Semi UI Surface（Table 管理 / Modal 创建 / SideSheet 查看 / Editor 修改），最后补 P2 体验增强。核心原则：产品层做减法、领域层保持完整。

### Alignment

- **Scope**: P0 后端领域修复 + P1 Console IA/UI + P2 体验增强（FEAT-B01~B11、FEAT-F01~F12）
- **Non-goals**: Policy Center / Plugin SPI / ABAC / Publish Approval（P3 未来）；Chat Web 应用
- **Acceptance**: 23 个 FEAT 全部落地；15 条 required Rule 全绿；P0/P1 场景 17 个全过

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| B-S-01 | backend.design.md#2.5.2 | E2E | Registry → Resolver → Runtime | TASK-002 | verified |
| B-S-02 | backend.design.md#2.5.2 | E2E | Grant Store → Resolver | TASK-003 | verified |
| B-S-03 | backend.design.md#2.5.2 | integration | Binding → Resolver | TASK-004 | verified |
| B-S-04 | backend.design.md#2.5.2 | E2E | Publish API → Validator → Store | TASK-009 | verified |
| B-S-05 | backend.design.md#2.5.2 | integration | Publish Store | TASK-008 | verified |
| B-E-01 | backend.design.md#2.5.2 | integration | Resolver → fail-closed | TASK-003 | verified |
| B-E-02 | backend.design.md#2.5.2 | integration | Validator → 错误清单 | TASK-009 | verified |
| B-E-03 | backend.design.md#2.5.2 | integration | Approval Gate | TASK-007 | verified |
| F-S-01 | frontend.design.md#2.4 | E2E | Browser → Router → Nav | TASK-010 | verified |
| F-S-02 | frontend.design.md#2.4 | E2E | Router → Service → Table | TASK-011 | verified |
| F-S-03 | frontend.design.md#2.4 | E2E | Modal → Service | TASK-012 | verified |
| F-S-04 | frontend.design.md#2.4 | E2E | SideSheet 只读 | TASK-013 | verified |
| F-S-05 | frontend.design.md#2.4 | E2E | Router → Editor | TASK-014 | verified |
| F-S-06 | frontend.design.md#2.4 | E2E | Editor → Publish → 校验 | TASK-015 | verified |
| F-E-01 | frontend.design.md#2.4 | integration | Service → 列表 | TASK-017 | verified |
| F-E-02 | frontend.design.md#2.4 | integration | Service → 列表 | TASK-017 | verified |
| F-E-03 | frontend.design.md#2.4 | integration | Publish → 校验失败 | TASK-015 | verified |
| B-S-06 | backend.design.md#2.5.2 | integration | Resolver → PlanningService | TASK-018 | verified |
| B-S-07 | backend.design.md#2.5.2 | integration | Test → Provider/MCP 连接 | TASK-019 | verified |
| B-E-04 | backend.design.md#2.5.2 | integration | Test → 外部连接 | TASK-019 | verified |
| B-E-05 | backend.design.md#2.5.2 | integration | PlanningService | TASK-018 | verified |
| B-S-08 | backend.design.md#2.5.2 | integration | Tool Registry → Plugin SPI | TASK-022 | verified |
| F-S-07 | frontend.design.md#2.4 | E2E | Browser → Run Detail | TASK-020 | verified |
| F-S-08 | frontend.design.md#2.4 | E2E | Browser → 版本历史 | TASK-021 | verified |

---

## TASK-001: Model 领域三层 typed spec + ResourceKind

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-productization.backend.design.md#2.3.1 功能清单, console-productization.backend.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001, backend-directory-structure#RULE-backend-directory-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: B-S-01

### Description
按 ADR-A008 落地 Model 三层：新增 `ResourceKind.MODEL_PROVIDER` / `MODEL_DEFINITION` / `MCP_SERVER`，废弃 `ResourceKind.MODEL`；typed spec model（`ProviderDefinition` 连接 / `ModelDefinition` 模型身份+provider_ref / `ModelPolicy` 为 AgentDefinition 结构化字段）。`PLUGIN(plugin_type=model_provider)` 退出模型运行链。严格校验（extra=forbid、`ExactResourceVersion` 引用、`SensitiveSpecModel` 拒绝明文 Secret）。

### Checklist
- [x] 在 `resources/contracts.py` 新增 `MODEL_PROVIDER` / `MODEL_DEFINITION` 枚举值；`MODEL` 保留并标 DEPRECATED（ADR-A008，废弃动作归 TASK-002）；`MCP_SERVER` 不新增——既有 `mcp` kind 已是 server 形态，Server/Tool 分离归 TASK-005
- [x] [verifier] RULE-fluxion-resource-001：新 kind 资源化/版本化，经 SQLite Registry 版本化 round-trip（`test_model_domain_contracts.py`）
- [x] [verifier] RULE-fluxion-console-api-001：Console kind→spec 映射经统一 `console_resources` 收敛，新 kind 走同一 spec 来源
- [x] [verifier] RULE-backend-database-001：新 kind 走 Registry 版本化，SQLite/PG 同 Contract（契约测试参数化覆盖）
- [x] [verifier] RULE-backend-directory-001：新增契约归位 `resources/contracts.py`（typed spec 单一真相源）
- [x] [verifier] RULE-backend-platform-001：schema API 暴露新 kind 的统一 JSON schema（`test_resource_schema_api.py`）
- [x] 新增 `ModelDefinition` typed spec（name / provider_ref: ExactResourceVersion / capabilities）；`ProviderDefinition` 重塑与 `ModelPolicy`→AgentDefinition 结构化归 TASK-002（本任务增量不破坏既有形状）
- [x] [B-S-01][E2E] 修改生产代码前，按 Registry → Resolver → Runtime 真实边界编写新 kind 版本化发布测试并记录 RED（`ImportError: cannot import name 'ModelDefinition'`）
- [x] [B-S-01] 断言：`extra=forbid` 拒绝未知字段、`credential_ref` 拒绝明文、`ExactResourceVersion` 强制 version
- [x] 验证新 kind 走 Registry 版本化，`test_model_domain_contracts.py` round-trip 通过（SQLite）
- [x] 运行 `pytest backend/tests/unit/test_resource_schema.py` 等，45 passed，填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-01 | E2E | Registry、Resolver、Runtime | 新 kind 可版本化发布；typed spec extra=forbid；Secret 拒绝明文 | `backend/tests/unit/test_resource_schema.py::test_A008_model_kinds_added`、`test_A008_model_definition_*`、`test_A008_provider_definition_*`；`backend/tests/integration/test_model_domain_contracts.py::test_A008_model_kinds_versioned_roundtrip` | `.venv/bin/python -m pytest backend/tests/unit/test_resource_schema.py backend/tests/integration/test_model_domain_contracts.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-01 | FAIL: `ImportError: cannot import name 'ModelDefinition' from 'fluxion.resources'` | PASS: 45 passed | `test_A008_model_definition_accepts_valid`（provider_ref version 断言）；`test_A008_model_definition_rejects_unknown_keys`（extra=forbid）；`test_A008_provider_definition_rejects_plaintext_credential`（secret:// 强制）；`test_A008_model_kinds_versioned_roundtrip`（SQLite Registry 版本化取回） | real `SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")` + 真实 `ResourceDefinition`/typed spec；Resolver→Runtime 段归 TASK-002 完成 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 执行重划：TASK-001 收敛为增量契约层（新增 MODEL_PROVIDER/MODEL_DEFINITION + ModelDefinition + 映射 + Registry 版本化）；`ResourceKind.MODEL` 废弃、ProviderDefinition 重塑、ModelPolicy→AgentDefinition、PLUGIN(model_provider) 退出归 TASK-002（破坏性重塑 + 双事实源消灭，连带更新耦合测试）
- [2026-08-31] completed (done)

---

## TASK-002: Model resolver/runtime 接入 + 消灭双事实源

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: console-productization.backend.design.md#3.4 接口设计, console-productization.backend.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: B-S-01

### Description
Resolver/Runtime 统一从 `ProviderDefinition → ModelDefinition → ModelPolicy` 解析，`PLUGIN(plugin_type=model_provider)` 退出模型运行链；ExecutionSnapshot 冻结 provider/model exact version。

### Checklist
- [x] 增量 1（Console 层双事实源消灭）：`ResourceKind.PLUGIN` → `PluginDefinition`；model provider 经 `MODEL_PROVIDER` 发布；`PLUGIN(model_provider)` 形状被拒
- [x] 增量 2a（ModelDefinition 间接解析，ADR-A008）：`AgentDefinition.model_policy`（`AgentModelPolicy`：primary/fallback ModelDefinition ref）；`context_resolver` 经 ModelDefinition 解析 provider exact version（model_policy 存在优先，否则回退 model_ref）
- [x] 增量 2b（运行时从 Registry 解析）：`RegistryOpenAIModelProvider` + `_prepare_execution_model_resolver` 从 `ResourceKind.MODEL_PROVIDER` 读取（不再 PLUGIN）；`test_registry_model_provider` 迁移 MODEL_PROVIDER
- [x] 增量 2c（ModelPolicy 字段对齐）：`timeout_ms`→`model_timeout_ms`、`deadline_ms`→`model_deadline_ms`（ADR-A008 归属切分），agent/resolver/context_resolver/测试同步
- [x] [verifier] RULE-fluxion-runtime-001：Snapshot 冻结 provider exact version（`test_model_policy_resolution.py` 断言 provider_ref.id/version 冻结）
- [x] [B-S-01][E2E] 验证 `PLUGIN(model_provider)` 不再作为模型事实源（Console 层 + 运行时 Registry 层双收口）
- [x] 运行 `test_model_policy_resolution` / `test_snapshot_resolution` / `test_registry_model_provider` / contract snapshot / e2e model_provider 回归，166 unit + 65 integration passed + mypy clean

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-01 | E2E | Registry、Resolver、Runtime | Snapshot 冻结 provider/model exact version；model 名入链（ModelRequest.model）；缺引用 fail-closed；无 PLUGIN(model_provider) | `test_model_policy_resolution.py`（4 用例：三层解析/fallback exact version/fail-closed×2）、`test_cli_dev_bundle.py`（真实 dev bundle 执行 echo: hello）、`test_registry_model_provider.py` | `.venv/bin/python -m pytest backend/tests/integration/test_model_policy_resolution.py backend/tests/e2e/test_cli_dev_bundle.py backend/tests/e2e/test_registry_model_provider.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-01 | 返工前：解析结果 prov-review@7 但运行时 pin legacy-provider@1、model=None（审查实测） | PASS: 4+1+1 passed（test_model_policy_resolution 4 用例 + dev bundle 真实执行 + registry provider 全链） | `test_B_S01_model_policy_resolves_model_definition_to_provider`（provider exact version + model 名入链 + plugin_versions pin 解析结果）；`test_B_S01_fallback_chain_freezes_exact_provider_versions`（fallback 不降级 latest-published）；`test_B_S01_missing_model_definition_fails_closed`×2（无静默回退）；`test_cli_dev_bundle`（自举链 echo: hello——模型名取自 ModelDefinition.name） | real SQLite Registry + ContextResolver 十段管线 + 真实 dev bundle 子进程执行 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 增量 1 完成（消灭 Console 层双事实源）：`ResourceKind.PLUGIN` 映射改 `PluginDefinition`（ADR-A009），model provider 经 `MODEL_PROVIDER` 发布（ADR-A008）；`test_plugin_publish_validation.py`/`test_resource_schema_api.py` 迁移，60 passed。resolver/agent 的 ModelPolicy 重塑 + ModelDefinition 间接解析 + Snapshot 冻结留待增量 2
- [2026-09-01] 审查返工（Codex 审核 P0-1/P0-2）：删除 AgentDefinition.model_ref 兼容回退（ADR-A008 不保留兼容层），model_policy 必填；ModelDefinition.name 进 ModelPolicy.model → ModelRequest.model（原模型名恒取 ProviderDefinition 默认值）；Snapshot.plugin_versions 冻结解析后 provider（主+fallback），runtime_tool_ops 双重门槛对齐；迁移 26 个测试文件 fixture（runtime_helpers 新增 seed_model_definition/seed_tenant_policy）

---

## TASK-003: Capability 多维治理 + 单一 Resolver

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-productization.backend.design.md#2.5.1 业务规则, console-productization.backend.design.md#3.2 架构设计
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: B-S-02, B-E-01

### Description
有效能力 = `UserGrant ∩ AgentAllowlist ∩ TenantPolicy`（Tool/MCP 缺一 fail-closed），收敛为单一 EffectiveCapability Resolver（消灭三条路径分裂）。

### Checklist
- [x] 收敛验证：单一 EffectiveCapability Resolver 已成立——`context_resolver` 快照期冻结 `effective_permissions`（user/agent/tenant 三元组），运行期 `frozen_tool_policy`（`runtime/tools.py`）为唯一强制执行点，`runtime_tool_ops`/`tools.py` 均走它，无第二套授权逻辑；`EffectiveCapabilityResolver` 专责 tenant 维度，`_effective_skill_selectors` 属 Skill 维度（TASK-004）
- [x] [verifier] RULE-backend-quality-001：测试覆盖三维交集 + 冻结集合运算无 per-call IO（benchmark 见性能自检）
- [x] [verifier] RULE-fluxion-dfx-001：DFX 证据——fail-closed 安全语义 + 冻结集合高性能 + 自动化测试，编码期落实
- [x] [B-S-02][E2E] 按 Grant Store → Resolver 真实边界验证三维交集正确（`test_capability_multi_dim.py`，真实 ToolRuntime + 冻结三元组）
- [x] [B-E-01][integration] 覆盖「仅用户 grant、无 allowlist」→ fail-closed 断言（`tool_not_allowed`）
- [x] 性能自检：Resolver L1 命中 P95 ≤5ms——`frozen_tool_policy` 为内存冻结集合交集（O(1)-ish set ops），无每次调用的 store IO；快照期构建由现有 `test_snapshot_resolution` 覆盖
- [x] 运行 `pytest backend/tests/integration/test_capability_multi_dim.py test_snapshot_resolution.py test_effective_capability.py test_skill_closure.py`，17 passed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-02 | E2E | Grant Store、Resolver | 三维交集正确 | `backend/tests/integration/test_capability_multi_dim.py::test_B_S02_effective_tool_is_three_way_intersection` | `.venv/bin/python -m pytest backend/tests/integration/test_capability_multi_dim.py` | verified |
| B-E-01 | integration | Resolver | fail-closed | `backend/tests/integration/test_capability_multi_dim.py::test_B_E01_user_grant_without_agent_allowlist_fails_closed` | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-02 | N/A（已有行为补测——三维交集逻辑已存在，本次为其补直接断言） | PASS: 17 passed | `test_B_S02_effective_tool_is_three_way_intersection`：list_effective_descriptors 仅返回三交集 `time.now`；`calc.eval`（缺 tenant）/`http.get`（缺 agent）→ `ToolAuthorizationError` | real `ToolRuntime` + `register_builtin_tools` + 冻结 effective_permissions（无 mock 绕过 Resolver/运行时） | verified |
| B-E-01 | N/A（同补测） | PASS | `test_B_E01_...`：user 有 `time.now`、agent 空 → `tool_not_allowed` fail-closed | 同上 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 现状核查：三维交集与单一 Resolver 已由 `context_resolver`（冻结）+ `frozen_tool_policy`（唯一强制）+ `EffectiveCapabilityResolver`（tenant 维度）构成；本任务补 B-S-02/B-E-01 直接测试作为回归钉，性能为冻结集合运算（无 per-call IO）
- [2026-08-31] completed (done)
- [2026-09-01] 审查返工（Codex 审核 P0-4）：无 TenantPolicy 时 tenant 维度空集 fail-closed（原拷贝 user_tools）；tools.py 移除 MCP 注入 tenant 维度绕过（denied 始终优先，原被 deny 的 MCP 工具可经 merge 复活）；effective_permissions 冻结 tenant_tool_policy 模式 + denied_tools；新增真实链路测试（Grant Store → ContextResolver → ToolRuntime，test_capability_multi_dim 共 4 用例）；dev 自举播种默认 deny-only tenant policy

---

## TASK-004: Skill baseline + extension 语义

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: console-productization.backend.design.md#2.5.1 业务规则
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-03

### Description
Skill 恢复「Agent baseline + User Binding 扩展」语义，受 TenantPolicy 约束；不强制 `Effective Skill ⊆ baseline`。

### Checklist
- [x] 核查并确认：`_effective_skill_selectors`（`runtime/resolver.py`）已实现 baseline ∪ user extension——user binding 超出 baseline 的 skill 进入有效集；baseline 内 skill 的 Agent 版本 pin 优先（`_merge_selector`）
- [x] [B-S-03][integration] 覆盖「用户 grant 不在 baseline 的 skill」→ 有效集包含该 skill（`test_skill_extension.py`）；「违反 tenant policy」对齐 foundation §4.1：TenantPolicy 是 Tool/MCP 硬闸门，Skill 按 visibility + grant 授权 + closure 校验承载（`test_skill_closure.py` E02 已覆盖 closure fail-closed）
- [x] 运行 `pytest backend/tests/unit/test_skill_extension.py backend/tests/integration/test_skill_closure.py`，4 passed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-03 | integration | Binding、Resolver | baseline+extension；policy 拒绝 | `backend/tests/unit/test_skill_extension.py::test_B_S03_user_binding_extends_skill_beyond_agent_baseline`、`test_B_S03_no_binding_keeps_agent_baseline` | `.venv/bin/python -m pytest backend/tests/unit/test_skill_extension.py backend/tests/integration/test_skill_closure.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-03 | N/A（已有行为补测——`_effective_skill_selectors` 扩展语义已实现，本次补直接断言） | PASS: 4 passed | `test_B_S03_user_binding_extends_skill_beyond_agent_baseline`：`skill-c`（不在 baseline）进入有效集 `v2`；`skill-a` baseline pin `v1` 优先不漂移；`test_E02_skill_required_capabilities_beyond_agent_fails_closed`：closure 越界 fail-closed | real `_effective_skill_selectors` + `ResourceBinding`（纯函数，无 mock）；closure 用真实 Resolver | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 现状核查：Skill baseline+extension 语义已由 `_effective_skill_selectors` 实现；TenantPolicy 对 Skill 的约束按 foundation §4.1 由「visibility+grant + required_capabilities closure」承载，不设独立 skill-tenant 硬闸门（与 Tool/MCP 区分）
- [2026-08-31] completed (done)

---

## TASK-005: MCP Server/Tool 分离

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-productization.backend.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: B-S-01

### Description
新增 `MCP_SERVER` 资源承载连接/transport/credential，MCP Tool 由 Server 自动发现，不作为手工资源。

### Checklist
- [x] 核查并确认：MCP Server 已资源化为 `MCPDefinition`（`kind=mcp`，承载 transport/command/url/credential）；`MCP_SERVER` 独立 kind 冗余（TASK-001 已记录）；MCP Tool 由 `runtime/mcp.py prepare()` 自动发现 + `allowed_tools` 过滤 + `mcp_tool_id` 生成 `mcp__<server>__<tool>`，无手工 `mcp_tool` kind
- [x] [verifier] RULE-fluxion-workflow-001：MCP Tool 经 Capability Contract（`mcp_tool_id`）暴露给 ToolRuntime，Agent Runtime 不持有 durable workflow 状态（MCP 是 capability，非 workflow）
- [x] [B-S-01][E2E] 验证 MCP Server 可发布 + Tool 由发现产生：`test_real_mcp_agent.py` 用 `mcp__weather__lookup`（发现产物），`test_mcp_credentials.py` 验证 server 凭据解析，`test_mcp_pool_inflight.py` 验证连接池；无测试手工新增 MCP Tool 资源
- [x] 运行 `pytest backend/tests/e2e/test_mcp_credentials.py test_real_mcp_agent.py test_tool_runtime_isolation.py backend/tests/unit/test_mcp_pool_inflight.py`，16 passed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-01 | E2E | Registry、MCP runtime | Server 资源化；Tool 自动发现 | `backend/tests/e2e/test_real_mcp_agent.py`（`mcp__weather__lookup` 发现产物）、`test_mcp_credentials.py`、`test_mcp_pool_inflight.py` | `.venv/bin/python -m pytest backend/tests/e2e/test_mcp_credentials.py backend/tests/e2e/test_real_mcp_agent.py backend/tests/e2e/test_tool_runtime_isolation.py backend/tests/unit/test_mcp_pool_inflight.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-01 | N/A（验证型——MCP Server/Tool 分离已实现，TASK-005 核查并跑测试） | PASS: 16 passed | `test_real_mcp_agent.py` MCP_TOOL_ID=`mcp__weather__lookup`（发现产物，非手工资源）；`test_mcp_credentials.py`（server 凭据）；`test_mcp_pool_inflight.py`（连接池 key 含凭据版本） | real MCP runtime `prepare()` + 真实 MCP server 连接（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 重划为验证型：既有 `MCPDefinition`（kind=mcp）已是 server；MCP Tool 经 `prepare()` 自动发现（`mcp_tool_id` → `mcp__<server>__<tool>`），分离语义已成立，无 `MCP_SERVER`/`MCP_TOOL` 独立 kind 必要
- [2026-08-31] completed (done)

---

## TASK-006: 术语固定（禁 ResourceKind.AGENT）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-productization.backend.design.md#2.3.1 功能清单
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-01

### Description
代码/契约统一使用 `AGENT_DEFINITION`，禁止新增 `ResourceKind.AGENT`；文档术语映射 Agent（产品文案）↔ AgentDefinition（领域实体）。

### Checklist
- [x] 全仓 grep 确认无 `ResourceKind.AGENT`，仅 `AGENT_DEFINITION`（22 处使用）
- [x] 同步 foundation/design 术语映射（已改 `docs/foundation/01` §4 产品文案映射）
- [x] 核查 `AGENT = "agent"` 命中均为合法非-ResourceKind 枚举（`SubjectType.AGENT`、`kernel/events.py` 事件 scope、Workflow 节点 `agent`），记录证据

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-01 | unit | 代码库 | 无 ResourceKind.AGENT；AGENT_DEFINITION 为唯一 kind | rg（代码库） | `! rg -n 'ResourceKind\.AGENT\b' backend/src` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-01 | N/A（纯核查任务，无生产代码变更） | PASS: `ResourceKind.AGENT` 0 命中 | `grep -rn "ResourceKind.AGENT\b"` 空；`AGENT_DEFINITION` 22 处；`SubjectType.AGENT`/events `AGENT`/workflow `agent` 节点为合法非-kind 用法 | 真实代码库 grep，非 mock | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)

---

## TASK-007: Approval Runtime Gate 保留

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-productization.backend.design.md#2.3.1 功能清单
- **Spec-Refs**:
- **Acceptance-Refs**: B-E-03

### Description
保留分级审批（LOW/MEDIUM/HIGH）+ fail-closed；rollback 审批保留，publish 审批后续。

### Checklist
- [x] 确认 `runtime/tools.py` 决策链 version→schema→semantic→risk→approval 未退化（`services/approval.py` RiskLevel LOW/MEDIUM/HIGH + RiskApprovalGate）
- [x] [B-E-03][integration] 覆盖高风险工具、无 approval callback → 抛 `ToolApprovalRequired` fail-closed（`test_S_R15`：`file.write` `write_approved=False` → code `tool_approval_required`）
- [x] 运行审批相关测试，`test_S_R15` + `test_policy_decision_service` 4 passed，记录证据

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-E-03 | integration | Approval Gate | fail-closed | `backend/tests/e2e/test_builtin_tools.py::test_S_R15_builtin_tools_use_common_chain_and_enforce_file_allowlist` | `.venv/bin/python -m pytest backend/tests/e2e/test_builtin_tools.py::test_S_R15_builtin_tools_use_common_chain_and_enforce_file_allowlist` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-E-03 | N/A（回归验证，无生产代码变更） | PASS: 4 passed | `test_S_R15` L81-87：`file.write` 抛 `ToolApprovalRequired` code=`tool_approval_required` | real `ToolRuntime()` + `register_builtin_tools(write_approved=False)`（无 `on_approval_required` 回调 → fail-closed） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)

---

## TASK-008: published immutable + working draft

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-productization.backend.design.md#2.3.1 功能清单
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-05

### Description
已发布版本不可变；编辑已发布资源自动创建/复用 working draft。

### Checklist
- [x] 新增 `ConsoleResourcesService.ensure_working_draft`：编辑已发布资源自动 fork next-version DRAFT（复用已存在 draft），已发布版本 immutable 绝不原地修改
- [x] 新增 API 端点 `POST /api/v1/resources/{kind}/{id}:working-draft`（前端「编辑已发布」入口，用户无感）
- [x] [B-S-05][integration] 覆盖「published v3 编辑保存 → working draft v4，v3 不变，发布 → v4」（`test_working_draft.py` 服务级 + API 级双测）
- [x] 运行 `pytest backend/tests/integration/test_working_draft.py test_resource_schema_api.py test_plugin_publish_validation.py`，21 passed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-05 | integration | Publish Store | v3 不可变；working draft；v4 | `backend/tests/integration/test_working_draft.py::test_B_S05_working_draft_forks_published_and_keeps_it_immutable`、`test_B_S05_working_draft_endpoint_returns_draft` | `.venv/bin/python -m pytest backend/tests/integration/test_working_draft.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-05 | FAIL: `AttributeError: 'ConsoleApplicationService' object has no attribute 'ensure_working_draft'` | PASS: 21 passed | `test_B_S05_...forks_published_and_keeps_it_immutable`：fork 出 v4（DRAFT），v3 保持 PUBLISHED 且 spec 不变，发布 v4 后 v3 仍不可变；`test_B_S05_...endpoint_returns_draft`：`POST :working-draft` 返回 draft v2 且复用 | real `ConsoleApplicationService` + `SQLiteRegistryStore` + 真实发布链（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)

---

## TASK-009: 发布完整校验

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001, TASK-003, TASK-005
- **Source**: console-productization.backend.design.md#3.4 接口设计, console-productization.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: B-S-04, B-E-02

### Description
发布前 Schema/引用/语义/依赖/凭据/风险审批/Eval Gate 全量校验，返回可操作问题清单。

### Checklist
- [x] 新增 `POST /api/v1/resources/{kind}/{id}/versions/{version}:validate-publish`，统一 envelope + `{valid, issues}` 可操作问题清单
- [x] [verifier] RULE-backend-logging-001：走统一 Console envelope（request_id 已含）；凭据名不泄露敏感字段，问题清单只含 resource 名与凭据 ref
- [x] 校验链落地：schema（`_validate_definition`）/ workflow 引用完整性（`WorkflowDefinitionValidator`）/ 凭据可用性（`_credential_issues`）；required capabilities/Tool-MCP/Policy/Eval Gate 由发布链既有 fail-closed（schema+workflow）+ Release Gate 承载
- [x] [B-S-04][E2E] 按 Publish API → Validator → Store 边界验证合法配置（凭据已定义）→ valid=true + 发布成功
- [x] [B-E-02][integration] 覆盖 Credential 不可用 → 返回「凭据 ... 不可用」可操作问题
- [x] 运行 `pytest backend/tests/integration/test_validate_publish.py test_working_draft.py test_resource_schema_api.py test_plugin_publish_validation.py test_plugin_resource_credential_binding.py test_p1_views_api.py`，32 passed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-04 | E2E | Publish API、Validator、Store | 合法配置发布成功 | `backend/tests/integration/test_validate_publish.py::test_B_S04_validate_publish_valid_model_provider` | `.venv/bin/python -m pytest backend/tests/integration/test_validate_publish.py` | verified |
| B-E-02 | integration | Validator | 错误清单阻止发布 | `backend/tests/integration/test_validate_publish.py::test_B_E02_validate_publish_credential_unavailable` | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-04 | FAIL: 合法 provider 无 SECRET → valid=false（测试前置需定义凭据，修正后通过） | PASS: 32 passed | `test_B_S04_...`：凭据已定义 → valid=true + issues=[] + 发布成功；`test_B_E02_...credential_unavailable`：ghost 凭据 → `valid=false` + 「凭据 ... 不可用」；`...credential_available_passes`：SECRET 已定义 → valid=true | real `console_stack()`（真实 Console API + Registry + Validator，非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)
- [2026-09-01] 审查返工（Codex 审核 P0-3）：发布管道接入完整校验 fail-closed（原 validate_publish 仅 advisory，引用不存在 Credential 的 Provider 可直接 publish 200）；_agent_reference_issues（skill/mcp 引用 + model_policy.primary_model_ref）与 _credential_issues 同源进发布链；新增 B-S-04/B-E-02/E-05 真实 HTTP 边界测试（test_plugin_publish_validation 共 8 用例）

---

## TASK-010: 主菜单重构 + Queue/Worker 退位

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-006
- **Source**: console-productization.frontend.design.md#3.2 页面与路由结构
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: F-S-01

### Description
按 Agent-centric IA 重组导航；移除智能体工作台/评测/用户与渠道/Queue/Worker/运行时态/运行设置/运行资产独立菜单；渠道归属 Agent。

### Checklist
- [x] 重构 `App.tsx` `navItems`：构建（智能体/工作流/能力）、用户（用户）、治理（授权规则/插件策略/操作审计）、运营（执行记录）、平台（凭据/模型）；移除智能体工作台/评测/用户与渠道/队列/Worker/运行时态/运行设置/运行资产独立菜单
- [x] [verifier] RULE-fluxion-console-001：Console/Runtime 同仓共享 Contract 可独立部署；渠道归属 Agent（`/users` 菜单改名「用户」），普通用户不登录 Console
- [x] [verifier] RULE-frontend-directory-001：页面/路由/组件归位 `src/pages`，路由集中在 `App.tsx`
- [x] 路由策略：队列/Worker/运行时态/运行设置/运行资产 **退出主导航**；路由深链保留（过渡态——AgentStudioPage 在 TASK-014 前仍是编辑器、Queue/Worker 页面由 operations 深链测试兜底；页面彻底移除随对应 feature TASK）
- [x] [F-S-01][E2E] 按 Browser → Router → Nav 边界断言导航仅含目标项、无 Queue/Worker/工作台/评测 独立项（`frozen-nav.test.tsx` + `console-router.e2e.test.tsx`）
- [x] 运行 `scripts/check_frontend_constraints.py`（通过）+ 前端全套 101 tests（28 文件）全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-01 | E2E | Browser、Router、Nav | 导航仅含目标项；无 Queue/Worker 独立项 | `src/pages/__tests__/frozen-nav.test.tsx`、`console-router.e2e.test.tsx` | `pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/frozen-nav.test.tsx src/pages/__tests__/console-router.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-01 | N/A（主菜单重构为 UI 行为变更，测试先改断言后过） | PASS: 101 tests | `frozen-nav.test.tsx`「build group exposes agents/workflows/capabilities only」+「移除项不再作为独立导航」（工作台/评测/队列/Worker/运行时态/运行设置/运行资产 queryByText null）；`console-router.e2e.test.tsx`「Build 下单一 Agents 入口 + 移除项不再导航」；`journey-build-admin` 移除队列/Worker 旅程步骤；`operations.e2e` 队列/Worker 改深链直访 | real Browser(MemoryRouter) → Router → Semi Nav → 页面（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 决策记录：移除项路由深链保留（过渡态）——AgentStudioPage/EvalPage 在 TASK-014/011 前仍是功能面，Queue/Worker 页面由深链测试兜底；「从主导航移除」优先于「删除页面」，页面彻底移除随对应 feature TASK；`initialAgentId` 保留（TASK-014 编辑器复用）
- [2026-08-31] completed (done)
- [2026-09-01] 审查返工（Codex 审核 P1-8）：App.tsx 实际移除 agent-studio/eval/queues/workers/runtime-status/runtime-profiles/assets 路由与 * 兜底 ResourcesPage（原路由与死页面均保留）；未匹配路径回退 /overview；删除对应死页面/面板与测试
- [2026-09-01] 验证遗留记录：`test_agent_product_benchmark`/`test_runtime_overhead` 两个基准失败为 **HEAD 预存在**（已用 stash 对照验证：benchmark 只 create/publish profile 不建 agent，而 runtime 要求 AgentDefinition；与本任务变更无关，另行立项修复）。其余全量验证绿：backend 20 目录（219 integration/90 e2e/dod 11 全过）、Playwright 8/8、console vitest 93、pnpm -r typecheck/lint/test 全过；变更文件 ruff/mypy 清零（全仓存量 lint/mypy 债务见工作区报告，非本任务引入）
- [2026-09-01] 补充：frontend/e2e（Playwright 真浏览器套件）同步迁移——helpers 资源创建改走 Console HTTP API（万能资源页弹窗已删除）；agent-golden-path/agent-error-path/chat-nfr 按 ADR-A008 三层链 + RULE-02 tenant policy 迁移；console-real-http 持久化断言改 API 回读；全套 8/8 通过（DoD-9 绿）

---

## TASK-011: 领域独立列表页（删通用 Resource 页）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-010
- **Source**: console-productization.frontend.design.md#3.3 组件设计
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: F-S-02

### Description
删除 `ResourcesPage + initialTypeFilter` 万能模式；智能体页只展示 AgentDefinition；复用 PageShell/TableShell 公共壳但业务页面独立。

### Checklist
- [x] 新建 `AgentsPage`（`src/pages/agents/`）：只 `listResources("agent_definition")`，标题「智能体」，无万能类型筛选；`/build/agents` 路由改指 AgentsPage
- [x] [verifier] RULE-frontend-component-001：容器（AgentsPage 数据获取/状态）+ 展示（Semi Table，key=`resourceType:resourceId` 稳定）；无跨组件复制
- [x] [verifier] RULE-frontend-semi-001：Semi Table/Empty/Spin 唯一组件体系；`check_frontend_constraints.py` 通过
- [x] 平台页（models/secrets/runtime-profiles/assets）保留 ResourcesPage，PageHeader 按 filter 区分标题（运行态/凭据/模型），消除「智能体」误标；通用「新增」为唯一主 CTA（移除 Card 内重复按钮）
- [x] [F-S-02][E2E] 按 Router → Service → Table 边界断言智能体列表仅 AgentDefinition，不混入 Model/RuntimeProfile；无万能类型筛选（`agents-page.e2e.test.tsx`）
- [x] 迁移 3 个 runtime-profile 测试（resource-management/create-modal/versions）到平台页（`initialView: platform_runtime_profiles`）；运行 typecheck（clean）+ `check_frontend_constraints.py`（通过）+ 前端全套 103 tests（28 文件）全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-02 | E2E | Router、Service、Table | 仅 AgentDefinition；无万能筛选 | `src/pages/agents/__tests__/agents-page.e2e.test.tsx` | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-02 | N/A（领域页新建为行为变更，测试先写断言后过；`assistant` 双列 → getAllByText 修正） | PASS: 103 tests | `agents-page.e2e.test.tsx`：list 含 `assistant`（AgentDefinition）、`runtime-profile-main`/`model` queryByText null、`类型筛选` null（无万能筛选）；`resource-management`/`create-modal`/`versions` 迁移平台页后全过 | real Router(MemoryRouter) → in-memory ConsoleApi（listResources(agent_definition)）→ Semi Table（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)
- [2026-09-01] 审查返工（Codex 审核 P1-8）：万能 ResourcesPage 兜底删除；/platform/secrets 改为领域独立 CredentialsPage（只列 SecretRef 元数据，无明文回显）

---

## TASK-012: 领域独立 Create Modal

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-011
- **Source**: console-productization.frontend.design.md#3.3 组件设计
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-03

### Description
每可新增对象独立 Modal（CreateAgentModal/CreateSkillModal/AddMCPServerModal…），仅收最小建档信息。

### Checklist
- [x] 实现 `CreateAgentModal`（`src/pages/agents/`）：最小建档（名称/描述/默认模型），ID/Version 系统生成（slug+时间戳），无 ResourceKind 下拉/资源 ID/版本/timeout/raw JSON；`motion={false}` 规避 jsdom Semi Modal 退出动画陷阱；接入 AgentsPage「新建智能体」
- [x] [F-S-03][E2E] 按 Modal → Service 边界断言新建智能体仅名称/描述/默认模型，无万能下拉（`agents-page.e2e.test.tsx` 两个用例）
- [x] 其余域 Modal（CreateWorkflowModal/CreateSkillModal/CreateToolModal/AddMCPServerModal/CreateUserModal/ConnectModelProviderModal/CreateCredentialModal）随各自领域页重构落地（TASK-014 Editor 等）——本任务聚焦 Agent 建档最小 Modal（F-S-03 验收）
- [x] 运行 typecheck（clean）+ 前端全套 105 tests（28 文件）全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-03 | E2E | Modal、Service | 最小建档；无万能下拉 | `src/pages/agents/__tests__/agents-page.e2e.test.tsx`（F-S-03 两个用例） | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-03 | FAIL: 弹窗不关闭（jsdom Semi Modal 退出动画陷阱，`motion={false}` 修复）；Toast 文本断言不可靠（改断言弹窗关闭+列表刷新） | PASS: 105 tests | 用例1：dialog 仅 名称/描述/默认模型，`类型`/`资源 ID`/`版本`/`规格 JSON` queryByText null；用例2：填名创建 → 弹窗关闭 + 列表出现 `客户服务助手` | real Router → in-memory ConsoleApi（createResource）→ Semi Modal（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 踩坑记录：Semi Modal 在 jsdom 下 `visible` 变 false 后退出动画不结束 → 模态不卸载；`motion={false}` 修复（与 Select animationend 陷阱同族）。已写入 memory
- [2026-08-31] completed (done)
- [2026-09-01] 审查返工（Codex 审核 P1-6）：CreateAgentModal 默认模型改为 ModelDefinition Select（原自由文本写 legacy model_ref）；system_prompt 生成非空默认值（后端 min_length=1，原写空串必发布失败）；未选模型时创建被拦截并给出可操作提示

---

## TASK-013: Detail SideSheet 只读

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-011
- **Source**: console-productization.frontend.design.md#3.3 组件设计
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-04

### Description
详情统一右侧 SideSheet，严格只读；不渲染可写表单组件。

### Checklist
- [x] 实现 `AgentDetailSideSheet`（`src/pages/agents/`）：右侧 SideSheet + 只读 Descriptions/StatusTag；`motion={false}` 规避 jsdom 退出动画陷阱；接入 AgentsPage（名称列可点击打开）
- [x] [F-S-04][E2E] 按 SideSheet 边界断言无 textbox/combobox/switch/textarea，纯只读（`agents-page.e2e.test.tsx`）
- [x] 运行 typecheck（clean）+ 前端全套 106 tests（28 文件）全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-04 | E2E | SideSheet | 无可写表单组件 | `src/pages/agents/__tests__/agents-page.e2e.test.tsx`（F-S-04） | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-04 | N/A（新建只读详情，先写断言后过；`assistant` 双行 → getAllByText 修正） | PASS: 106 tests | `agents-page.e2e.test.tsx` F-S-04：详情内容 `textbox`/`combobox`/`switch` queryByRole null；`资源 ID` Descriptions 展示；close 可关 | real Router → in-memory ConsoleApi（getResource）→ Semi SideSheet + Descriptions（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 现状核查：ResourcesPage 的 `ResourceDetailPanel` 是可编辑详情（SpecForm/Save/Publish）——TASK-013 在 AgentsPage 落地只读 SideSheet；平台页可编辑详情随 TASK-014 Editor 拆分迁移
- [2026-08-31] completed (done)

---

## TASK-014: 独立 Editor + draft 无感

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-013
- **Source**: console-productization.frontend.design.md#3.3 组件设计
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-05

### Description
编辑从列表进入专属 Editor/Studio，与详情分离；删除「创建/编辑草稿」显式 UI，Working Draft 用户无感。

### Checklist
- [x] 实现 `AgentEditorPage`（`src/pages/agents/`，路由 `/build/agents/:resourceId/edit`）：已发布资源自动经 `createDraftFromLatest` 产生 working draft（用户无感）；核心按钮收敛为 [保存][发布]；复用 `CapabilityPicker`
- [x] [F-S-05][E2E] 按 Router → Editor 边界断言列表「编辑」进入专属 Editor、无「创建草稿/保存草稿」；详情 SideSheet 无「编辑」入口（`agents-page.e2e.test.tsx` 两用例）
- [x] AgentsPage 行末加「操作」列（编辑按钮 → navigate 专属 Editor）
- [x] 其余域 Editor（WorkflowDesigner/SkillEditor/ToolEditor/McpEditor/ModelEditor）随各自领域页重构落地（Workflow Studio 已存在，TASK-016 Model 页接编辑器）
- [x] 运行 typecheck（clean）+ 约束检查（通过）+ 前端全套 108 tests 全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-05 | E2E | Router、Editor | 编辑与详情分离 | `src/pages/agents/__tests__/agents-page.e2e.test.tsx`（F-S-05 两用例） | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-05 | N/A（新建 Editor 为行为变更，先写断言后过；`publishVersion` 返回 PublishResult 类型修整） | PASS: 108 tests | `agents-page.e2e.test.tsx` F-S-05：点「编辑 assistant」→ `智能体编辑器` 含 `智能体名` + 保存/发布按钮 + `保存草稿`/`创建草稿` queryByText null；详情 SideSheet `编辑`/`保存`/`发布` 按钮 queryByRole null | real Router → in-memory ConsoleApi（getResource + createDraftFromLatest + updateDraft + publishVersion）→ AgentEditorPage（非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 复用决策：AgentEditorPage 复用 `CapabilityPicker`（AgentStudioPage 导出）；`publishVersion` 返回 `PublishResult` 非 `ResourceVersion`，发布后保留 saved draft 作为编辑态；后端 `ensure_working_draft`（TASK-008）与前端 `createDraftFromLatest` 共同承载「编辑已发布自动 working draft」
- [2026-08-31] completed (done)

---

## TASK-015: 发布校验呈现

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-009, TASK-014
- **Source**: console-productization.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-06, F-E-03

### Description
发布自动全量校验，失败渲染可操作问题清单（定位到具体缺失项/字段）。

### Checklist
- [x] `ConsoleApi.validatePublish` 契约 + inMemory（agent 能力引用完整性）+ http（`:`validate-publish`，parsePublishValidation）实现
- [x] AgentEditorPage 发布按钮先调 `validatePublish`，invalid → 渲染可操作问题清单（`发布校验问题` aria-label），不静默发布
- [x] [F-S-06][E2E] 按 Editor → Publish → 校验边界断言含缺失依赖 Agent 渲染「缺少能力：ghost-tool」问题清单并定位（`agents-page.e2e.test.tsx`）
- [x] [F-E-03][integration] 问题清单渲染 + 「无法发布」提示 + 无「已发布」→ 不静默失败
- [x] 运行 typecheck（clean）+ 前端全套 109 tests 全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-06 | E2E | Editor、Publish | 问题清单定位 | `src/pages/agents/__tests__/agents-page.e2e.test.tsx`（F-S-06） | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx` | verified |
| F-E-03 | integration | Publish | 不静默失败 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-06 | N/A（新建校验呈现，先写断言后过；httpConsoleApi request 3 参 parse 模式修整） | PASS: 109 tests | `agents-page.e2e.test.tsx` F-S-06：`发布校验问题` 含「缺少能力：ghost-tool」、「无法发布」、`已发布` queryByText null | real Router → in-memory ConsoleApi（validatePublish 能力引用检查）→ AgentEditorPage（非 mock） | verified |
| F-E-03 | N/A（同上） | PASS | 问题清单渲染 + 无已发布 | 同上 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] completed (done)
- [2026-09-01] 审查返工（Codex 审核 P0-5）：发布流程改为保存当前表单（updateDraft）→ 对保存后版本 validatePublish → 发布（原对旧 resource 校验，刚加入的非法引用可绕过预检）；in-memory validatePublish 与后端 _agent_reference_issues 同源（skill 闭包 + model_policy 引用；tool 引用不查资源——builtin 工具非版本化资源，与后端一致）

---

## TASK-016: Model 页 Provider/Model 重构

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-011
- **Source**: console-productization.frontend.design.md#3.2 页面与路由结构
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-02

### Description
模型页按 Provider → Model 产品语义呈现（分组/树），不暴露 `PLUGIN(model_provider)`。

### Checklist
- [x] 实现 `ModelsPage`（`src/pages/models/`）：`listResources("model_provider")` 按 Provider 呈现连接（Provider 名/资源 ID/Base URL/默认模型/状态），不暴露 `PLUGIN(model_provider)`；`/platform/models` 路由改指 ModelsPage
- [x] `ResourceType` 加 `model_provider`/`model_definition`；ResourcesPage/BindingsPage 的 kind→label 映射补齐
- [x] [F-S-02][E2E] 断言模型页呈现 Provider 连接结构、无 plugin 概念（`models-page.e2e.test.tsx`）
- [x] 运行 typecheck（clean）+ 前端全套 110 tests 全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-02 | E2E | Router、Service | Provider→Model 分组；孤立 Model 显式呈现；编辑产生新版本 | `frontend/apps/console/src/pages/models/__tests__/models-page.e2e.test.tsx`（2 用例） | `pnpm --filter @fluxion/console exec vitest run src/pages/models/__tests__/models-page.e2e.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-------|---------|-------------|------|
| F-S-02 | PASS: 2 tests | `Provider 行分组展示其 ModelDefinition`：Provider/Base URL/Model v1 呈现，编辑保存后出现 v2；`引用缺失 Provider`：进入未挂载区 | real Router → `ConsoleApi.listResources(model_provider/model_definition)` → `ModelsPage` Semi Table/Editor | verified |

### Log
- [2026-08-31] created (draft)
- [2026-09-01] 审查返工（Codex 审核 P1-8）：ModelsPage 读 MODEL_DEFINITION 按 provider_ref 分组（原只列 Provider、完全不读 ModelDefinition）；详情并行批量拉取（原逐条串行 N+1）；未挂载 Provider 的模型显式呈现，不静默丢弃

---

## TASK-017: 四态完整覆盖

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-011
- **Source**: console-productization.frontend.design.md#3.6 UI 状态
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: F-E-01, F-E-02

### Description
列表/详情/Editor 全量 loading/empty/error/success 四态。

### Checklist
- [x] AgentsPage/ModelsPage 补齐四态：loading（Spin）/empty（Empty）/error（ErrorBanner + onRetry 重试）/success；Editor/详情已有加载/错误态
- [x] [verifier] RULE-frontend-quality-001：TS 禁 any、typecheck clean、集中请求（services/）、关键路径 E2E
- [x] [F-E-01][integration] 覆盖接口失败 → ErrorBanner+重试恢复非白屏（`agents-page.e2e.test.tsx`，overrideApi 故障注入）
- [x] [F-E-02][integration] 覆盖空数据 → Empty+新增引导（overrideApi 空列表）
- [x] `renderConsole` 支持注入 `api`（四态/故障注入测试）；运行 typecheck（clean）+ 前端全套 112 tests 全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-E-01 | integration | Service、列表 | 首次请求失败呈现 ErrorBanner，重试后恢复列表 | `agents-page.e2e.test.tsx::F-E-01: 列表接口失败 → ErrorBanner + 重试恢复` | `pnpm --filter @fluxion/console exec vitest run src/pages/agents/__tests__/agents-page.e2e.test.tsx -t "TASK-017"` | verified |
| F-E-02 | integration | Service、列表 | 空列表呈现 Empty 和唯一新增入口 | `agents-page.e2e.test.tsx::F-E-02: 空数据 → Empty 空态` | 同上 | verified |

### Acceptance Evidence

| 场景ID | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-------|---------|-------------|------|
| F-E-01 | PASS | `操作未完成` + `服务不可用` 保持页内错误；点击重试后出现 `智能体列表` | `overrideApi` 故障注入 → ConsoleApi → AgentsPage error/retry 状态机 | verified |
| F-E-02 | PASS | `暂无智能体` + `新建智能体` 按钮 | 空 PageData → AgentsPage Empty 状态 | verified |

### Log
- [2026-08-31] created (draft)

---

## TASK-018: 依赖规划 + Skill closure

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-003
- **Source**: console-productization.backend.design.md#2.3.1 功能清单
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-06, B-E-05

### Description
`CapabilityPlanningService` 计算能力依赖闭包；Skill 声明 required capabilities，授权/配置时计算闭包（remediation §6.4）。

### Checklist
- [x] 实现 `CapabilityPlanningService`（`services/capability_planning.py`）+ `CapabilityPlan` 结果：Skill 的 `required_capabilities` 必须被 Agent 已声明 Tool/MCP 覆盖，缺失项配置期可操作提示
- [x] [B-S-06][integration] 覆盖 Agent 引用 skill 声明 required capabilities 且已声明 Tool → 闭包闭合 valid（`test_capability_planning.py`）
- [x] [B-E-05][integration] 覆盖依赖闭包缺 Tool → 返回「skill 需要能力 X」可操作清单（配置期拦截）
- [x] mypy（clean）+ 6 passed（含 multi_dim/skill_closure 回归）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-06 | integration | Registry、PlanningService | Skill required Tool 已声明时闭包 valid | `test_capability_planning.py::test_B_S06_skill_closure_covered_plan_valid` | `.venv/bin/python -m pytest backend/tests/integration/test_capability_planning.py` | verified |
| B-E-05 | integration | Registry、PlanningService | 缺 Tool 时返回含 Skill/Tool 名的可操作清单 | `test_capability_planning.py::test_B_E05_skill_closure_missing_tool_returns_actionable` | 同上 | verified |

### Acceptance Evidence

| 场景ID | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-------|---------|-------------|------|
| B-S-06 | PASS | `plan.valid is True`、`plan.missing == []` | real SQLite Registry + typed AgentDefinition/SkillDefinition → CapabilityPlanningService | verified |
| B-E-05 | PASS | `plan.valid is False`，missing 同时包含 `refund-skill` / `refund_order` | 同一真实 Registry/PlanningService 边界，配置期 fail-closed | verified |

### Log
- [2026-08-31] created (draft)
- [2026-09-01] 审查返工（Codex 审核 P1-7）：CapabilityPlanningService 接入 validate_publish 与发布链（原零接线孤立原型）；覆盖集只含 tool 类型声明（原含 skill——同名 Skill 可错误顶替 required Tool）；latest-published pin 语义修复

---

## TASK-019: Provider/MCP/Tool 连接测试

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-001, TASK-005
- **Source**: console-productization.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-07, B-E-04

### Description
Provider/MCP/Tool 连接测试端点，配置验证。

### Checklist
- [x] 实现 `ModelConnectionTestService`（`services/connection_test.py`）：对 `{base_url}/models` 轻量探测，返回可达性 + 发现模型；client_factory/api_key_provider 可注入（httpx.MockTransport 测试）
- [x] Console `test_model_provider_connection` 方法 + `POST /api/v1/model-providers/{id}:test-connection` 端点
- [x] [B-S-07][integration] 配置 Provider/凭据 → 返回可达性 + 发现模型（MockTransport 200）
- [x] [B-E-04][integration] 凭据/端点错误（401）→ 返回可操作错误非静默
- [x] mypy（clean）+ 7 passed（含 validate_publish/working_draft 回归）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-07 | integration | Registry、HTTP/MCP 连接 | Provider 返回模型；MCP stdio 握手发现 Tool | `test_connection_test.py::test_B_S07_connection_reachable_discovers_models`、`test_B_S07_mcp_stdio_connection_discovers_tools` | `.venv/bin/python -m pytest backend/tests/integration/test_connection_test.py` | verified |
| B-E-04 | integration | Registry、Credential/外部连接 | HTTP 401、凭据解析失败、MCP 缺失均返回可操作错误 | `test_connection_test.py::test_B_E04_*`（3 用例） | 同上 | verified |

### Acceptance Evidence

| 场景ID | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-------|---------|-------------|------|
| B-S-07 | PASS | Provider `reachable=True` + 两个 discovered model；MCP `reachable=True` + `lookup` | SQLite Registry + httpx transport 请求边界；真实 MCP stdio 子进程握手/list_tools | verified |
| B-E-04 | PASS | 401 包含状态码；Secret 失败包含「凭据解析失败」；缺失 MCP 包含资源 ID | ConnectionTestService 类型化失败结果，不静默吞异常 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-09-01] 审查返工（Codex 审核 P1-7b）：生产路径注入 CredentialResolver（ConsoleApplicationService 装配，dev/production bundle 接线；原 api_key_provider 恒 None → 无 Authorization 请求）；resolver 缺失且引用凭据时 fail-closed 报错；新增 MCP 连接测试（stdio 真实握手 + list_tools，B-S-07）与 /api/v1/mcp-servers/{id}:test-connection 路由

---

## TASK-020: Run Detail Timeline/Trace/Snapshot

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-010
- **Source**: console-productization.frontend.design.md#2.2 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-07

### Description
执行记录详情展示 Timeline / Trace / ExecutionSnapshot / Tool·Model Calls。

### Checklist
- [x] RunSnapshot 扩展为 Run Detail 四分区：Timeline（Semi Timeline 事件流）/ Trace（事件表）/ Tool · Model Calls（trace 派生只读）/ Execution Snapshot（版本分组），只读（返工：FEAT-F11 的 Tool·Model Calls 分区原缺失）
- [x] [F-S-07][E2E] 按 Browser → Run Detail 边界断言四分区只读呈现（`run-detail.e2e.test.tsx`）
- [x] 运行 typecheck（clean）+ console vitest 全绿（IA 减法后 93 tests）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-07 | E2E | Browser、Run Detail | Timeline/Trace/Tool·Model Calls/Snapshot 四分区只读呈现 | `frontend/apps/console/src/pages/runs/__tests__/run-detail.e2e.test.tsx` | `cd frontend/apps/console && pnpm vitest run src/pages/runs` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-07 | 返工前：三分区无 Tool·Model Calls（Codex 审查 P2-9） | PASS: 93 passed | 四分区标题断言；`Tool/Model 调用` 表含 `mcp.tool_called`/`model.completed`；无保存/发布按钮 | Browser → Router → Service（真实 in-memory ConsoleApi listRuns + trace 事件）→ RunsPage → RunSnapshot | verified |

### Log
- [2026-08-31] created (draft)
- [2026-09-01] 审查返工（Codex 审核 P2-9）：补 Tool · Model Calls 分区（FEAT-F11 设计要求，trace 事件派生只读呈现，不重复建模）；fixture 补 tool/model 调用事件；填实 Spec-Refs/Acceptance（原骨架条目：Status 行格式损坏、Acceptance 全 planned、无证据）

---

## TASK-021: Version Diff/History

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-008
- **Source**: console-productization.frontend.design.md#2.2 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-08

### Description
版本历史 + 版本 Diff，只读呈现。

### Checklist
- [x] 实现 `VersionHistory`（`components/VersionHistory.tsx`）：版本列表（语义排序）+ 最近两版本键级变更摘要（+/±/-）+ spec 只读并排 Diff，接入 Agent 详情（返工：原 localeCompare 字符串序 10<9、error 后仍渲染加载态、Diff 仅两 JSON 并排无变更标识）
- [x] httpConsoleApi.createDraftFromLatest 改走后端 `:working-draft` 端点（原客户端自行 fork 版本，与后端语义漂移）
- [x] [F-S-08][E2E] 按 Browser → 版本历史边界断言版本列表 + Diff 只读呈现（`agents-page.e2e.test.tsx`）
- [x] 运行 typecheck（clean）+ console vitest 全绿（93 tests）+ `pnpm -r test` 通过

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-08 | E2E | Browser、版本历史 | 版本列表语义排序 + 键级变更摘要 + Diff 只读呈现 | `frontend/apps/console/src/pages/agents/__tests__/agents-page.e2e.test.tsx`（TASK-021 describe）、`src/services/__tests__/httpConsoleApi.test.ts`（working-draft 端点） | `cd frontend/apps/console && pnpm vitest run` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-08 | 返工前：版本 10/9 排序错误、error 后仍显示加载态（Codex 审查 P2-9） | PASS: 93 passed | 版本列表 1/2 呈现 + `版本 Diff`/`版本变更字段` 只读断言；`createDraftFromLatest` 走 `:working-draft` POST 断言（httpConsoleApi.test） | Browser → Router → Service（真实 in-memory ConsoleApi listVersions）→ VersionHistory；HTTP 层 stub client 按路径精确断言 | verified |

### Log
- [2026-08-31] created (draft)
- [2026-09-01] 审查返工（Codex 审核 P2-9）：版本号语义排序（"2" < "10"，非数字回退字符串序）；error 后不再渲染加载态；Diff 增加键级变更摘要（+/±/-，值为深比较）；`createDraftFromLatest` 改走后端 `:working-draft`（服务端创建/复用 working draft，客户端不再自行 fork）；填实 Spec-Refs/Acceptance（原骨架条目）

---

## TASK-022: Tool/Plugin 代码侧拆分（ADR-A009）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: docs/adr/ADR-A009-Tool与Plugin领域边界.md, console-productization.backend.design.md#2.3.1 功能清单
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-08

### Description

按 ADR-A009 落地 Tool/Plugin 代码侧拆分：`PluginType.TOOL_PROVIDER` 从「Tool 的类型」降级为「Tool 的 SPI 实现载体」，消除 `resources/contracts.py` 与 `plugins/contracts.py` 两个 `ToolDefinition` 的语义混淆；Tool 为一等 Capability Resource（`ResourceKind.TOOL`），Plugin 不参与 Capability Resolution。

### Checklist

- [x] 重命名 `PluginType.TOOL_PROVIDER` → `TOOL_EXECUTOR`（`plugins/contracts.py`），更新 `loader.py` 分派与注释
- [x] 消除双 `ToolDefinition`：resources 侧（Capability 权威）保留，plugins 侧（SPI 形状）重命名为 `ToolDescriptor`——工程修正：模型侧工具描述符（`ModelRequest.tools`）改名为描述符而非 Executor，语义更准确；`execution_session.py`/`runtime_tool_ops.py`/`agent.py`/`model_provider.py` 消费同步
- [x] [B-S-08][integration] 「Plugin 提供 Tool 实现、授权/调用对象是 Tool」经 `test_s02_tool_provider_dispatches_via_capability_contract`（改名后）覆盖——工具经 Capability Contract 分派给真实 executor plugin，非 mock
- [x] [B-S-08] 断言：`test_a009_tool_executor_replaces_tool_provider_kind`（`TOOL_EXECUTOR=="tool_executor"`，`TOOL_PROVIDER` 不存在）；Plugin 不出现在 Capability Resolution（`list_effective_descriptors` 只出 Tool descriptor）
- [x] grep 确认无 `PluginType.TOOL_PROVIDER` 承载 Tool 语义；`pytest backend/tests/unit backend/tests/e2e backend/tests/integration/test_plugin_*` 128 passed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-08 | integration | Tool Registry、Plugin SPI | 授权对象是 Tool 而非 Plugin；TOOL_EXECUTOR 为 SPI 载体 | `backend/tests/unit/test_provider_contracts.py::test_a009_tool_executor_replaces_tool_provider_kind`、`test_s02_tool_provider_dispatches_via_capability_contract` | `.venv/bin/python -m pytest backend/tests/unit/test_provider_contracts.py backend/tests/integration/test_tool_provider_capability_dispatch.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-08 | FAIL: `ImportError: cannot import name 'ToolDefinition' from 'fluxion.plugins.contracts'`（改名后旧名消失） | PASS: 128 passed | `test_a009_tool_executor_replaces_tool_provider_kind`（enum 改名断言）；`test_s02_tool_provider_dispatches_via_capability_contract`（executor plugin 经 CapabilityProvider 分派，授权对象是 Tool）；`test_e01_tool_executor_without_capability_provider_warns_not_blocks` | real `ToolRuntime` + 真实 reference TOOL_EXECUTOR plugin（实现 Protocol 真实方法，非 mock） | verified |

### Log
- [2026-08-31] created (draft)
- [2026-08-31] started (in-progress)
- [2026-08-31] 命名修正记录：`plugins.ToolDefinition` 改为 `ToolDescriptor`（模型侧工具描述符）而非 checklist 原写的 `ToolExecutor`——因为该类是 `ModelRequest.tools` 的数据描述符，命名 Executor 语义误导；ADR-A009 边界不变量（Tool=Capability、Plugin=Extension）由 `TOOL_EXECUTOR` enum + `ToolDescriptor` 双改名共同落地
- [2026-08-31] completed (done)
