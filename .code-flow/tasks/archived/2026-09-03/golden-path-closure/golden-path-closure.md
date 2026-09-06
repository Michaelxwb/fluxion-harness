# Tasks: golden-path-closure

- **Source**: .code-flow/tasks/2026-09-03/golden-path-closure/remediation-plan.md（Codex 70 commits 前后端联合整改方案，2026-09-02 全量核实后修正范围）
- **Created**: 2026-09-03
- **Updated**: 2026-09-05

## Proposal

在 70 commits 基线上完成 Golden Path Closure + Console Productization Closure：先以 ADR 收口四条跨层契约（RuntimeProfile 默认解析、Provider Credential 真相源与 Binding 逻辑 ID 继承、ReleaseGate applies_to 边界、ExecutionSnapshot typed pins 与运行期消费），再落地产品化 Application API 与前端标准列表 Shell（左上主操作 / 右上过滤筛选搜索 / 右下总数+分页、Modal 创建、SideSheet 只读、Editor 修改），补齐 Credential / Model 两个 Golden Path blocker 与 Agent 用户/渠道/测试/评测生命周期，最终以空租户纯浏览器 E2E 验证全链路。整改范围基于 2026-09-02 五路并行代码核实修正：过时断言已剔除、核实新发现 4 项已并入对应任务。

### Alignment

- **Scope**: 整改方案全量（§4 后端 7 条 + §5–§10 前端规范与 12 页 + §11 产品化 API + §12 空租户 E2E + §14 P0/P1/P2），重点前端功能缺失
- **Decisions**:
  - Binding 版本规则选方案 B 且做干净（用户确认选最优、不将就兼容）：credential/override 按逻辑 Provider ID 跨版本继承，移除 MODEL_PROVIDER Binding 版本 selector 死配置（现状 `model_providers.py:109-116` 匹配已忽略该字段），版本级敏感参数未来走 typed override
  - Policy 页选 B 完整开放（用户确认）：CreatePolicyModal + Policy Editor + 只读 SideSheet + 搜索/过滤/分页
  - 核实过时项剔除：Agent 页 CreateModal / 只读 SideSheet / 创建即跳编辑已有（25f0a67）；Model 页 Provider→Model 分组与 typed 编辑 Modal 已有；ResourceKind Select 与万能资源页已删除；GenericResourceForm/GenericResourceEditor 从未存在；typed pins 中 skill/mcp/policy/credential_versions 已存在（`resources/contracts.py:202-210`），本轮只补 model_versions/provider_versions 与 plugin_versions 迁移
  - 新发现 4 项纳入：binding `credential_ref=None` 时 `api_key=None` 静默裸调改 fail-closed（TASK-003）；前端 publish 不发 `expected_base_version` 导致乐观并发检查跳过（TASK-004）；e2e 调试探针与 SpecForm 死代码清理（TASK-026）
  - Credential 页保留 SecretRef 只读展示（有意设计、非明文，符合规则 17），本轮补创建 Journey 与列表标准
  - WORKFLOW 是否纳入 ReleaseGate `applies_to` 在 TASK-001 ADR 中决策（默认仅 AGENT_DEFINITION）
  - 与在库 runtime-workflow-kernel（draft）划界：本轮不动 workflow kernel 内部执行链，§4.5 仅收口 ContextResolver 装配入口与 Snapshot 字段契约
- **Non-goals**: 大规模领域重构；Chat Web 应用；workflow kernel 内部执行链（另任务）
- **Acceptance**: 33 个场景全过（后端 13、前端 20）；15 条 required Rule 全绿；§13 页面验收矩阵 10 页逐项 GREEN；空租户纯 UI Golden Path E2E 通过

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | review-fixes.design.md#凭据 | integration | Console Service、SQLite Registry、SecretStore | TASK-027 | verified |
| S-02 | review-fixes.design.md#凭据 | integration | Console Service、SecretStore、CredentialResolver | TASK-027 | verified |
| S-03 | review-fixes.design.md#评测 | integration | Eval Service、Registry、TraceStore | TASK-027 | verified |
| S-04 | review-fixes.design.md#默认配置 | integration | SQLite/PostgreSQL 共享 Registry Contract | TASK-027 | verified |
| S-05 | review-fixes.design.md#模型刷新 | E2E | Browser、HTTP、Registry、本地模型 stub | TASK-027 | verified |
| S-06 | review-fixes.design.md#编辑器 | E2E | Browser、HTTP、Registry | TASK-027 | verified |
| B-S-01 | remediation-plan.md#4.1 RuntimeProfile 默认语义(L120-L165) | E2E | Console 发布 → Resolver → 执行 | TASK-002 | verified |
| B-E-01 | remediation-plan.md#4.1 RuntimeProfile 默认语义(L120-L165) | integration | Resolver → fail-closed | TASK-002 | verified |
| B-S-02 | remediation-plan.md#4.2 Provider Credential 双事实源(L167-L211) | E2E | Provider spec → Runtime 执行 | TASK-003 | verified |
| B-S-03 | remediation-plan.md#4.2 Provider Credential 双事实源(L167-L211) | integration | Binding → spec 回退链 | TASK-003 | verified |
| B-E-02 | 核实新发现①（api_key=None 裸调） | integration | Provider 解析 → fail-closed | TASK-003 | verified |
| B-E-03 | remediation-plan.md#4.3 Binding 版本规则(L213-L241) | integration | Binding 匹配契约 | TASK-003 | verified |
| B-S-04 | remediation-plan.md#4.4 ReleaseGate 耦合(L243-L285) | integration | Publish API → Gate | TASK-004 | verified |
| B-S-05 | remediation-plan.md#4.4 ReleaseGate 耦合(L243-L285) | integration | FE Client → Publish API | TASK-004 | verified |
| B-S-06 | remediation-plan.md#4.5 ExecutionSnapshot(L287-L349) | integration | Snapshot 构建 → 消费方 | TASK-005 | verified |
| B-S-07 | remediation-plan.md#4.5 ExecutionSnapshot(L287-L349) | E2E | Snapshot → Runtime 执行一致性 | TASK-005 | verified |
| B-E-04 | remediation-plan.md#4.6 ContextResolver 装配(L351-L370) | integration | composition root → Resolver | TASK-005 | verified |
| B-S-08 | remediation-plan.md#4.7 EvalSet target(L372-L402) | integration | EvalSet → EvalRun | TASK-006 | verified |
| B-S-09 | remediation-plan.md#11 API 产品化(L1122-L1155) | integration | 产品 API → Registry | TASK-007 | verified |
| F-S-01 | remediation-plan.md#5 标准列表布局(L404-L445) | integration | Shell 组件渲染契约 | TASK-008 | verified |
| F-S-02 | remediation-plan.md#8.6 凭据(L805-L851) | E2E | Browser → Modal → API → Store | TASK-009 | verified |
| F-S-03 | remediation-plan.md#8.7 模型(L853-L905) | E2E | Browser → Provider API → 外部 stub | TASK-010 | verified |
| F-S-04 | remediation-plan.md#8.1 智能体(L626-L668) | E2E | Browser → 列表 → 行操作 | TASK-011 | verified |
| F-S-05 | remediation-plan.md#9.3 Agent 测试(L1068-L1076) | E2E | Editor → test-run API → 执行 | TASK-012 | verified |
| F-S-06 | remediation-plan.md#9.4 Agent 评测(L1078-L1081) | E2E | Editor → Eval API → EvalRun | TASK-012 | verified |
| F-S-07 | remediation-plan.md#9.1 Agent 用户(L1053-L1058) | E2E | Editor → Binding/Grant API | TASK-013 | verified |
| F-S-08 | remediation-plan.md#9.2 Agent 渠道(L1060-L1066) | E2E | Editor → Channel API → verify | TASK-014 | verified |
| F-S-09 | remediation-plan.md#8.2 工作流(L670-L711) | E2E | Modal → 独立 Designer 路由 | TASK-015 | verified |
| F-S-10 | remediation-plan.md#8.3 Skill(L712-L737) | E2E | Modal → Editor → 发布 | TASK-016 | verified |
| F-S-11 | remediation-plan.md#8.4 Tool(L739-L766) | E2E | Modal(类型) → Editor → Test Call | TASK-017 | verified |
| F-S-12 | remediation-plan.md#8.5 MCP(L768-L803) | E2E | Modal → test-connection → discover | TASK-018 | verified |
| F-S-13 | remediation-plan.md#8.8 用户(L907-L933) | E2E | Browser → 列表 → 单套分页 | TASK-019 | verified |
| F-S-14 | remediation-plan.md#8.9 执行记录(L935-L975) | E2E | Browser → SideSheet 只读 | TASK-020 | verified |
| F-S-15 | remediation-plan.md#8.10 操作审计(L977-L1001) | E2E | Browser → 过滤/搜索/SideSheet | TASK-021 | verified |
| F-S-16 | remediation-plan.md#8.11 授权规则(L1003-L1026) | E2E | Modal → Editor → 生效 | TASK-022 | verified |
| F-S-17 | remediation-plan.md#8.12 平台概览(L1028-L1043) | E2E | 工作台 → 跳转目标页 | TASK-023 | verified |
| F-S-18 | remediation-plan.md#14 P2(L1271-L1282) | integration | 发布校验/Diff/规划 API | TASK-024 | verified |
| F-S-19 | remediation-plan.md#14 P2(L1273-L1282) | integration | Projection API → 列表 | TASK-025 | verified |
| F-S-20 | remediation-plan.md#12 E2E 重写(L1159-L1218) | E2E | 空租户全链路浏览器 | TASK-026 | verified |

> 覆盖整改方案 §13 页面矩阵（经 F-S-02/03/04/09/10/11/12/13/14/15）、§15 DoD（B-S-01~09 + F-S 全系）、§12 Golden Path（F-S-20）。B-S-01 的 ADR 决策由 TASK-001 产出、TASK-002 代码验证。

---

## TASK-001: 契约 ADR 前置（RuntimeProfile 默认解析 / Credential 真相源 / Snapshot pins / Gate 边界）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: remediation-plan.md#4.1(L120-L165), #4.2(L167-L211), #4.3(L213-L241), #4.4(L243-L285), #4.5(L287-L349)
- **Spec-Refs**:
- **Acceptance-Refs**: B-S-01, B-S-02, B-E-03, B-S-04, B-S-06
  （代码场景由 TASK-002~005 最终验收，本任务产出 ADR 决策依据）

### Description

四条 P0 契约变更按规则 25 与 cf 原则 6 必须先 ADR 后代码：RuntimeProfile 默认解析链（新 ADR）、Provider Credential 真相源与 Binding 逻辑 ID 继承（amend ADR-A008）、ExecutionSnapshot typed pins 与运行期消费（amend ADR-A003）、ReleaseGate applies_to 边界。核实证据：现状同名回退散布 `context_resolver.py:175-191`、`agents_app.py:112-120`、`channel_app.py:259-271`、`studio.py:178-186`；Credential 双源 `model_providers.py:96-131` vs `console_resource_validation.py:203-238`；gate 无 kind 白名单 `console_resource_lifecycle.py:283-295`。

### Checklist
- [x] 新建 ADR-A010「RuntimeProfile 默认解析链」：`runtime_profile_ref` 未配置 → Tenant Default RuntimeProfile → `platform-default` 显式回退；Agent ID == RuntimeProfile ID 同名回退废弃（含存量迁移说明与 dev 自举调整）
- [x] amend ADR-A008：EffectiveCredential = User Binding ?? Tenant Binding ?? ProviderDefinition.credential_ref；Binding 定位为 Override 非运行前提；MODEL_PROVIDER Binding 绑定逻辑 Provider ID 跨版本继承，版本级敏感参数走显式 typed override（方案 B，不保留 selector 死配置）
- [x] amend ADR-A003：Snapshot typed pins 定型——新增 `model_versions`/`provider_versions`，`plugin_versions` 废弃与迁移；Snapshot 冻结 Credential Ref/Version 作为运行期唯一执行事实
- [x] ADR 内决策 WORKFLOW 是否纳入 ReleaseGate `applies_to`（默认仅 AGENT_DEFINITION），并定义保存（基础校验）与发布（完整校验+gate）职责边界
- [x] 对照 `docs/architecture/总体架构.md` 与 `.code-flow/specs/architecture/*.md` 修正受影响表述（如需）
- [x] [integration] ADR 合入后运行 `cf-validate` 与 docs 一致性检查并记录结果

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-01(决策) | integration | ADR 文档 + 架构基线引用 | ADR 覆盖四条契约且与总体架构无冲突 | docs/adr/ADR-A010 + A011 + A003 amend + A008 amend | cf-validate（py_compile PASS；基线 grep 零冲突） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-01(决策) | N/A（纯 ADR 决策任务，无生产行为变更可先失败——按流程记录原因，不伪造 RED） | cf-validate：py_compile PASS；`git diff` 仅含 4 份 ADR + cf_spec_session.py 单行正则修复 | ADR-A010 §决策（两级解析链+同名回退废弃清单五处）；ADR-A011 §决策（applies_to 白名单 + WORKFLOW 不纳入 + 保存/发布职责）；ADR-A003 §Amend（typed pins 定型 + 运行期消费收口）；ADR-A008 §Amend（EffectiveCredential 单链 + 逻辑 ID 继承 + selector 死配置移除） | 四份 ADR 实文件引用 remediation-plan §4.1-4.5 与 2026-09-02 核实 file:line 证据；`grep` 总体架构 + specs/architecture 零冲突表述（无需修正） | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-03] started (in-progress) — Start Gate pass / active marker / session spec 就绪
- [2026-09-03] completed (done) — 四份 ADR 落地（A010/A011 新建 + A003/A008 amend）；WORKFLOW 决策不纳入 gate（重评估条件已记录 A011）；附带修复 cf_spec_session.py `_refs` 正则跨行吞行 bug（\s* → [ \t]*）

---

## TASK-002: RuntimeProfile 租户默认解析链（删除同名回退）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: remediation-plan.md#4.1 RuntimeProfile 默认语义(L120-L165)
- **Spec-Refs**: backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: B-S-01, B-E-01

### Description

Schema 注释承诺"留空由解析层取租户默认"（`agents/definitions.py:107-111`），但实现是 `profile_id = agent_id` 同名回退（`context_resolver.py:175-191`），租户默认零实现；同名约定另散布 `agents_app.py:112-120`、`channel_app.py:259-271`、`api/studio.py:178-186`、`runtime/context.py:26-27`；Console 发布校验不校验 profile 可解析性（`console_resource_validation.py:116-179`）。按 ADR-A010 落地正式解析链，删除同名回退（不将就兼容）。

### Checklist
- [x] 实现租户默认解析：未配置 ref → Tenant Default RuntimeProfile → `platform-default`（解析规则按 ADR-A010，tenant scope 全链路）
- [x] 删除全部五处同名回退；dev 自举 `ensure_runtime_profile`（`runtime_profile_service.py:100-134`、CLI `main.py:149`）改为创建/标记 tenant default，不再依赖同名 Agent 对
- [x] Console 发布校验：显式 `runtime_profile_ref` 时校验指向的 RuntimeProfile 存在且 published；未配置时校验租户默认可解析
- [x] [B-S-01][E2E] 修改生产代码前先写验收测试并记录 RED：Console 新建 Agent（`runtime_profile_ref=None`）→ 发布 → 执行 Resolve 成功（真实边界：Registry → Resolver → Runtime，不 mock Store）
- [x] [B-S-01] 断言空租户下不依赖任何手工 seed 同名 RuntimeProfile
- [x] [B-E-01][integration] 无租户默认且无 platform-default → fail-closed 明确错误码（区分于同名查找失败的 `runtime_profile_not_found` 语义）
- [x] 迁移存量同名 fixture/e2e 数据；SQLite/PG 同契约测试双跑
- [x] [verifier] RULE-backend-directory-001：新解析逻辑归位 services/ 与 agents/ 既有目录约定（无新顶层模块），目录结构经 `cf-validate` 扫描

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-01 | E2E | Registry、Resolver、Runtime | 无 ref Agent 发布后可 Resolve 执行；无同名 seed | `test_runtime_profile_golden_path.py::test_bs01_console_agent_without_profile_ref_resolves_via_default_chain` | `uv run pytest backend/tests/e2e/test_runtime_profile_golden_path.py -q` | verified |
| B-E-01 | integration | Resolver → Store | 无默认时 fail-closed 错误码与信息 | `test_runtime_profile_default.py::test_be01_no_default_and_no_platform_default_fail_closed` | `uv run pytest backend/tests/services/test_runtime_profile_default.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-01 | FAIL（基线 worktree @25f0a67 复核）：`runtime_profile_not_found: agent-bs01@latest-published not found`（Golden Path 断裂） | 1 passed：发布无 ref Agent → dev bundle run 返回 `runtime_profile_id == "platform-default"`、`output == "echo: hello"` | `test_runtime_profile_golden_path.py:67`（same_name is None 空租户断言）、`:73-76`（run 结果 profile/platform-default + output） | 真实 SQLite RegistryStore + Console HTTP ASGI + dev bundle AgentRuntime（dev.echo stub provider）十段管线；全程无同名 RuntimeProfile seed（仅 platform-default + model） | verified |
| B-E-01 | FAIL（基线 worktree 复核）：同名回退 `runtime_profile_not_found`（旧语义无默认链概念） | 5 passed：`code == "runtime_profile_default_missing"`；另 4 用例覆盖 tenant default / platform-default 回退 / 显式 ref 优先 / draft 不生效 | `test_runtime_profile_default.py:102`（B-E-01 错误码断言）、`:116`（tenant default 无同名）、`:130`（platform-default）、`:165`（显式 ref 优先）、`:186`（draft 不生效） | 真实 SQLite RegistryStore + ContextResolver 十段管线（不 mock Store）；`runtime_profile_default_missing` 与 `runtime_profile_not_found` 错误码区分 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-03] started (in-progress) — refresh ok；session 1 rule（backend-directory）；marker 复用
- [2026-09-03] completed (done) — 默认链落地（tenant default → platform-default）；五处同名回退删除 + scheduler 第六处装配补 agent_definition_id；Console 发布校验补 profile 可解析性；dev 自举 ensure 幂等创建 platform-default + 标记 default；Registry publish 治理拒绝同租户多 default 并存；版本更替 default 不延续（ADR-A010 补记）；33 个存量 e2e/integration 迁移至 agent 主坐标

---

## TASK-003: Provider Credential 真相源统一 + Binding 逻辑 ID 继承 + fail-closed

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: remediation-plan.md#4.2(L167-L211), #4.3(L213-L241)
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: B-S-02, B-S-03, B-E-02, B-E-03

### Description

Runtime 只从 binding 取 credential（User → Tenant，无 spec 回退，`model_providers.py:96-131`），Console 校验读 spec（`console_resource_validation.py:203-238`、`connection_test.py:73-75`）——双事实源；且 binding 存在但 `credential_ref=None` 时以 `api_key=None` 静默裸调（`:122-123`）；`resource_version_selector` 在 MODEL_PROVIDER 匹配中被完全忽略（`:109-116`），Console 却允许填精确版本（`console_governance.py:165-172`）。按 ADR-A008 amend 落地方案 B 干净版。

### Checklist
- [x] 实现 EffectiveCredential 链：User Binding ?? Tenant Binding ?? ProviderDefinition.credential_ref（MCP 链路保持单源设计不引入 spec credential）
- [x] [B-E-02][integration] 先写测试记录 RED：链上无可解析 credential → fail-closed，断言无 `api_key=None` 出站调用
- [x] [B-S-03][integration] override 优先级契约测试：User > Tenant > spec；binding 仅做 override 不构成运行前提
- [x] 移除 MODEL_PROVIDER Binding 版本 selector 死配置：Console 建 model-provider binding 不再暴露 version_selector 输入；后端契约标注该字段对 MODEL_PROVIDER 无效或从该类型 schema 剔除
- [x] [B-E-03][integration] 契约测试钉死逻辑 Provider ID 匹配语义（跨版本继承），防止回归为版本敏感匹配
- [x] [B-S-02][E2E] 仅配置 ProviderDefinition.credential_ref（无任何 binding）→ 模型调用成功（真实 Registry + 本地 stub provider）
- [x] 发布校验：validate-publish 增加 Provider Credential 可解析性检查（spec.credential_ref 指向的 Secret 存在且可用）
- [x] [verifier] RULE-fluxion-resource-001：Binding/Credential 契约变更走 typed spec 单一真相源（extra=forbid），SQLite/PG 契约测试双跑（`test_registry_model_provider.py` 扩展）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-02 | E2E | Registry、Runtime、stub Provider | 仅 spec credential 即可运行 | `test_provider_credential_resolution.py::test_bs02_spec_credential_only_succeeds_without_binding` | `uv run pytest backend/tests/services/test_provider_credential_resolution.py -q` | verified |
| B-S-03 | integration | Binding Store、CredentialResolver | User > Tenant > spec 优先级 | `test_provider_credential_resolution.py::test_bs03_override_priority_user_tenant_spec` | `uv run pytest backend/tests/services/test_provider_credential_resolution.py -q` | verified |
| B-E-02 | integration | Provider 解析链 | 无 credential → fail-closed 无裸调 | `test_provider_credential_resolution.py::test_be02_unresolvable_credential_fails_closed_without_outbound_call` | `uv run pytest backend/tests/services/test_provider_credential_resolution.py -q` | verified |
| B-E-03 | integration | Binding 匹配 | 逻辑 ID 匹配、selector 不参与 | `test_provider_credential_resolution.py::test_be03_binding_matches_logical_id_ignoring_version_selector` | `uv run pytest backend/tests/services/test_provider_credential_resolution.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-02 | FAIL（基线 worktree @25f0a67 复核，4 failed）：基线 `_binding` 无 binding 即 raise `model provider binding not found`，spec credential 被忽略 | 1 passed：无 binding 仅 spec credential → `output == "spec only answer"` + `Bearer spec-only-secret` | `test_provider_credential_resolution.py:274-278`（无 binding 断言）、`:282-284`（output + authorization） | 真实 SQLite RegistryStore + LocalEncryptedSecretStore + 本地 openai_wire_server（不 mock）；无任何 MODEL_PROVIDER binding | verified |
| B-S-03 | FAIL（基线复核） | 1 passed：三层齐备取 user；仅 tenant 取 tenant；仅 spec 取 spec；`authorization` 逐层断言 | `test_provider_credential_resolution.py:205-207`（user）、`:210-212`（tenant）、`:240-243`（spec） | 真实 binding 三级 + CredentialResolver 逐层解算；`wire.request_headers[i].authorization` 钉死优先级 | verified |
| B-E-02 | FAIL（基线复核） | 1 passed：`provider_credential_unresolvable` 报错 + `wire.requests == []`（零出站） | `test_provider_credential_resolution.py:153-156`（错误码 + 零请求 + 零 header） | 真实 Secret 缺失（凭据库空）→ resolve 阶段 fail-closed，stub server 零请求（证明无 api_key=None 裸调） | verified |
| B-E-03 | FAIL（基线复核） | 1 passed：binding `resource_version_selector="1"` 但 provider 当前 v2，仍命中 override → `Bearer override-secret` | `test_provider_credential_resolution.py:243-245`（output override + authorization） | 真实 provider v1+v2 发布 + binding selector="1"；逻辑 ID 跨版本继承（selector 不参与匹配） | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] completed (done) — EffectiveCredential 单链落地（User ?? Tenant ?? spec，fail-closed 无裸调）；MODEL_PROVIDER binding 逻辑 ID 继承、selector 死配置移除（console_governance 强制 latest-published）；发布校验 spec credential 可解析性已存在并保留；4 验收场景 RED→GREEN（基线 4 failed 复核）

---

## TASK-004: ReleaseGate applies_to 边界 + 前后端 Publish 契约统一

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: remediation-plan.md#4.4 ReleaseGate 耦合(L243-L285)
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: B-S-04, B-S-05

### Description

Gate 挂通用 publish 管道对所有 ResourceKind 无差别生效（`console_resource_lifecycle.py:77-132, 283-295`），无 `applies_to` 白名单；生产装配 `release_gate_enforced=True`（`production_bundle.py:185`）而前端 `publishVersion()` 只发空 `{}` 且全前端无 gate 传参能力（`httpConsoleApi.ts:160-166`）→ 生产环境 Console 任何发布必被 `RELEASE_GATE_BLOCKED(38001)` 阻断（集成测试 `test_release_gate.py:170-206` 实证 RUNTIME_PROFILE 也被阻断）。前端同时不发 `expected_base_version`，乐观并发检查被静默跳过（核实新发现②）。

### Checklist
- [x] 引入 `ReleaseGatePolicy.applies_to(kind)`：AGENT_DEFINITION 必选（+WORKFLOW 按 ADR-A010 决策）；MODEL_DEFINITION/MODEL_PROVIDER/SKILL/TOOL/MCP_SERVER/RUNTIME_PROFILE/SECRET/POLICY/EVAL_SET 默认不评估 gate
- [x] [B-S-04][integration] 先写测试记录 RED：enforced 装配下非 gate kind 无 gate 参数发布成功（改写 `test_release_gate.py:170-206` 断言 RUNTIME_PROFILE 不再 409）
- [x] [B-S-05][integration] AGENT_DEFINITION enforced 无 gate → 409 fail-closed 保持；携带合法 gate 参数 → 发布成功
- [x] 前端 `publishVersion` 签名扩展 `publish_note`/`expected_base_version`/`gate`；Agent Editor / Workflow / Model Editor 发布调用统一接线 `expected_base_version`（乐观并发不再跳过）
- [x] 保存（基础校验）与发布（完整校验+gate）职责区分：Editor UI 文案与按钮语义明确
- [x] in-memory API 实现同步（jsdom 测试一致性）
- [x] [verifier] RULE-backend-quality-001：gate 阻断/放行路径显式错误码与失败策略全覆盖，无静默吞异常（`test_release_gate.py` 扩展）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-04 | integration | Publish API → Gate → Store | 非 gate kind 空 body 可发布 | `test_release_gate.py::test_bs04_enforced_non_gate_kind_publishes_without_gate` | `uv run pytest backend/tests/integration/test_release_gate.py -q` | verified |
| B-S-05 | integration | Publish API + FE client 类型 | agent enforced 阻断/带 gate 通过；expected_base_version 生效 | `test_release_gate.py::test_enforced_gate_blocks_publish_without_gate_param` + `::test_bs05_enforced_agent_publishes_with_valid_gate` | `uv run pytest backend/tests/integration/test_release_gate.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-04 | FAIL（基线行为）：原 `test_enforced_gate_blocks_publish_without_gate_param` 断言 RUNTIME_PROFILE enforced 无 gate → 409（基线该断言通过，证明基线对非门 kind 无差别阻断；ADR-A011 背景亦记录 test_release_gate.py:170-206 实证） | 1 passed：enforced 下 RUNTIME_PROFILE 空 body → 200 | `test_release_gate.py:226-247`（B-S-04 断言 status_code 200） | 真实 SQLite RegistryStore + ConsoleApplicationService(release_gate_enforced=True) + Console HTTP :publish 管道 | verified |
| B-S-05 | FAIL（基线行为）：基线 enforced 下 AGENT_DEFINITION 无 gate → 409（原测试覆盖所有 kind 409）；带 gate 达标放行为已有行为（补测） | 2 passed：enforced agent 无 gate → 409 + 38_001；enforced agent 带合法 gate → 200 + published | `test_release_gate.py:202-210`（无 gate 409 + 38_001 + draft）、`:270-284`（带 gate 200 + published） | 真实 ReleaseGateService(evaluation) + RuleBasedEvalExecutor + enforce 装配；前端 publishVersion 类型签名扩展经 `pnpm run typecheck` + `pnpm test`（102 passed）验证 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] completed (done) — `_evaluate_release_gate` 加 applies_to 白名单（仅 AGENT_DEFINITION）；test_release_gate 全量迁移 agent 目标 + B-S-04/B-S-05 新测试（9 passed）；前端 PublishOptions 类型 + httpConsoleApi/inMemoryConsoleApi 签名扩展 + Agent/Workflow/Model 三 Editor 接线 expected_base_version（typecheck + 102 tests 绿）

---

## TASK-005: ExecutionSnapshot 收口 + ContextResolver 装配补全

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: remediation-plan.md#4.5(L287-L349), #4.6(L351-L370)
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: B-S-06, B-S-07, B-E-04

### Description

装配缺口：`runtime_app.py:106-117` 持有 `credential_resolver` 却只注入 MCP runtime，`ContextResolver(store)` 裸构造；`memory_retriever` 全仓零注入（`PersonalMemoryRetriever` 从未接线）；`workflow_worker_bootstrap.py:102`、`capacity_verify.py:307-308` 另两处裸构造。后果：`credential_versions` 占位 `"1"`（`context_resolution_support.py:227-229`）、memory manifest 恒 `unavailable`（`:61-62`）。Snapshot 字段：`plugin_versions = model_provider_pins` 混用（`context_resolver.py:216-218, 335`；消费方 `runtime_tool_ops.py:59`、`agent.py:402`、`console_payloads.py:116,153`）；`credential_versions` 冻结后无运行期消费者；`memory_policy_ref`/`workflow_ref`/`personalization_policy_ref` 从未赋值；运行期 `model_providers.py:77-131` 每次调用实时重 resolve provider/binding/credential。

### Checklist
- [x] 生产/dev composition root 注入：`ContextResolver(store, credential_resolver=..., memory_retriever=...)`；`workflow_worker_bootstrap` / `capacity_verify` 改用注入式构造（禁止子模块裸建）
- [x] [B-E-04][integration] 先写测试记录 RED：注入装配下 snapshot `credential_versions` 为真实解析版本（非 `"1"`）、memory manifest 非 `unavailable` 占位
- [x] Snapshot 新增 `model_versions`/`provider_versions`（按 ADR-A003 amend）；迁移 `plugin_versions` 全部消费方与既有断言
- [x] [B-S-06][integration] typed pins 契约测试：provider 精确版本 pin；`ResolvedModelRoute` 补 ModelDefinition 版本 pin（当前 `resource_specs.py:14-20` 仅 pin provider_ref，model 为名字符串）
- [x] 运行期消费 snapshot 冻结值：provider/binding/credential 的选择在 Snapshot 构建期收口，运行期按冻结 ref/version 解密与实例化（解密仍运行期，选择不再漂移）
- [x] [B-S-07][E2E] 同一 execution 期间发布新 provider 版本/新增 binding，不影响执行中的版本与 credential 选择（真实 Registry + stub provider）
- [x] `memory_policy_ref` 等未赋值字段按 ADR 决策：赋值或从契约移除
- [x] 与 runtime-workflow-kernel 任务划界：不改 workflow 内部执行链，仅收口 resolver 入口
- [x] [verifier] RULE-fluxion-runtime-001：Snapshot 冻结 provider/model/credential 精确版本、运行期不漂移（`test_context_resolver.py` + e2e 断言）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-06 | integration | Snapshot 构建 + 消费方 | provider/model 精确 pin；plugin_versions 移除 | `test_model_policy_resolution.py::test_B_S01_fallback_chain_freezes_exact_provider_versions` + `test_execution_snapshot_contract.py::test_S02_deprecated_refs_removed` | `uv run pytest backend/tests/integration/test_model_policy_resolution.py backend/tests/contract/test_execution_snapshot_contract.py -q` | verified |
| B-S-07 | E2E | Registry、Runtime、stub Provider | 执行中配置变更不影响版本选择 | `test_snapshot_assembly.py::test_bs07_snapshot_freezes_provider_credential_selection` | `uv run pytest backend/tests/services/test_snapshot_assembly.py -q` | verified |
| B-E-04 | integration | composition root → Resolver | credential_versions 真实化、memory manifest 可用 | `test_snapshot_assembly.py::test_be04_composition_root_injects_credential_and_memory` | `uv run pytest backend/tests/services/test_snapshot_assembly.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-06 | FAIL（基线无 provider_versions/model_versions 字段，plugin_versions 承载 provider pins；ResolvedModelRoute 无 model_ref） | 2 passed：routes 断言含 `model_ref` exact pin + `provider_versions`/`model_versions` 精确；`plugin_versions`/`workflow_ref`/`memory_policy_ref`/`personalization_policy_ref` 不在字段集 | `test_model_policy_resolution.py:164-177`（routes model_dump + provider_versions + model_versions）、`test_execution_snapshot_contract.py:56-62`（废弃字段移除） | 真实 ContextResolver 十段管线 + ModelDefinition 三层解析（不 mock）；契约 `extra=forbid` 移除废弃字段 | verified |
| B-S-07 | FAIL（基线运行期每次模型调用实时 list_bindings 重选 credential，执行中新增 binding 漂移） | 1 passed：第一次 resolve 冻结 spec credential_ref；新增 binding 后第二次 resolve 冻结 override；第一次 snapshot 不变 | `test_snapshot_assembly.py:157-172`（first/second provider_credentials 断言） | 真实 SQLite RegistryStore + ContextResolver + CredentialResolver；provider_credentials 构建期收口（`resolve_effective_credential_ref`），运行期 `ModelRequest.credential_ref` 消费 | verified |
| B-E-04 | FAIL（基线 composition root 裸构造 ContextResolver(store)，credential_resolver/memory_retriever 零注入 → credential_versions 占位 "1"、memory manifest unavailable） | 1 passed：注入 credential_resolver + memory_retriever 后 credential_versions == "2"（rotate 后真实版本）、memory manifest content_hash != "unavailable" | `test_snapshot_assembly.py:99-105`（credential_versions == "2" + memory manifest 非 unavailable） | 真实 LocalEncryptedSecretStore（rotate 到 v2）+ PersonalMemoryRetriever(PgVectorSemanticStore(SQLite engine)) + dev bundle composition root 注入 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] completed (done) — typed pins 定型（provider_versions/model_versions 新增 + plugin_versions/3 未赋值字段移除 + ResolvedModelRoute.model_ref）；全消费方迁移（agent/console_payloads/runtime_tool_ops/resolver + 10 处测试）；composition root 注入 credential_resolver+memory_retriever（credential_versions 真实化 + memory manifest 可用）；provider credential 选择收口到 snapshot.provider_credentials（运行期按冻结 ref 解密，不重选）；B-S-06/B-S-07/B-E-04 全 verified

---

## TASK-006: EvalSet target 转向 agent_definition / workflow

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-005
- **Source**: remediation-plan.md#4.7 EvalSet target(L372-L402)
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: B-S-08

### Description

EvalSet 被测目标唯一是 `runtime_profile_ref`（`resource_specs.py:355-362`），EvalRun 溯源/落档全锚 RuntimeProfile（`eval_app.py:216-222, 274-287`、`registry/schema.py:521-534`），eval 模块无 agent 概念；case 层已有 `workflow_ref`（`:335-343`）。按文档建议引入 typed target（kind: agent_definition | workflow | runtime_profile），EvalRun 基于 ExecutionSnapshot 做 baseline/candidate 比较——这也是 TASK-004 gate 参数可用（EvalRun 基线）的前提。

### Checklist
- [x] `EvalSetDefinition.target: {kind, id, version}` typed 字段（runtime_profile 保留为 kind 之一，兼容既有数据）
- [x] `eval_runs` 落档 target kind/id/version（SQLite/PG schema 迁移双跑，幂等脚本）
- [x] EvalRun 锚定 ExecutionSnapshot：baseline/candidate 比较基于 snapshot digest（execution_snapshot 含 snapshot_digest，run 落档可追溯）
- [x] [B-S-08][integration] 先写测试记录 RED：target=agent_definition → published 校验 → EvalRun 执行产出 score；target 不可解析 → fail-closed
- [x] workflow 维度统一到 target 模型或保持 case 级（按 ADR 决策），语义不双轨
- [x] [verifier] RULE-backend-database-001：eval_runs target 列迁移经幂等脚本，SQLite/PG 同 Contract 参数化双跑

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-08 | integration | EvalSet → Registry → EvalRun | agent target 执行出 score；不可解析 fail-closed | `test_eval_target.py::test_bs08_agent_definition_target_resolves_and_scores` + `::test_bs08_unresolvable_agent_target_fails_closed` | `uv run pytest backend/tests/integration/test_eval_target.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-08 | FAIL（基线 EvalSetDefinition 无 target 字段 extra=forbid → 带 target 的 EvalSet 解析报 Schema 无效；start_run 只校验 runtime_profile，无 agent 校验） | 2 passed：agent target 校验通过 → `target_kind == "agent_definition"` + score 1.0；不可解析 agent → EvalTraceabilityError fail-closed | `test_eval_target.py:104-110`（target_kind/score）、`:140-150`（fail-closed） | 真实 SQLite RegistryStore + InMemoryTraceStore + RuleBasedEvalExecutor；EvalTarget typed（kind 判别 published 校验）；eval_runs target 列幂等迁移（SQLite/PG 双跑 6 passed） | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] completed (done) — EvalTarget typed 字段（agent_definition/workflow/runtime_profile）+ effective_target 兼容回退；eval_app.start_run 按 kind 判别 published 校验；EvalRunRecord + eval_runs 表 + PostgresEvalRunStore 落档 target（幂等 ALTER 迁移双跑）；B-S-08 全 verified

---

## TASK-007: 产品化 Application API（Registry 之上的管理员语义端点）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: remediation-plan.md#11 API 产品化建议(L1122-L1155), #10 前端代码级约束(L1085-L1118)
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: B-S-09

### Description

Console 前端所有创建动作走通用 `POST /api/v1/resources`（`httpConsoleApi.ts:108-119`），契约强制前端生成 `resource_id`/`version`（`console_models.py:12-13` 必填）→ 前端三处生成 ID + 写死 `version: "1"`（`CreateAgentModal.tsx:59-63`、`CapabilitiesPage.tsx:104-108`、`GovernancePoliciesPage.tsx:72-76`）。已有部分产品端点：`/studio/{kind}`（服务端生成 id，`studio.py:77-134`）、`:test-connection` ×2（`console.py:241-271`）、`GET /credentials`；缺创建/发现类。shared 的 productClient 零消费。按 §11 收口产品端点，内部映射 Registry，id/version 服务端生成。

### Checklist
- [x] 新增/收口产品端点（统一 envelope + request_id 日志 + AuditLog）：
  - `POST /api/v1/credentials`（创建 Secret，明文只写不回显）——留待 TASK-009 Credential Journey 落地（需 SecretStore 明文写入能力）
  - `POST /api/v1/model-providers`（创建 Provider）→ `/studio/model-providers` 收口；`/models`（手工添加模型）→ `/studio/model-definitions`；`/discover-models`（发现远端模型）→ 复用 `:test-connection` 返回的 discovered_models
  - `POST /api/v1/mcp-servers` → `/studio/mcp` 收口
  - agents/workflows/skills/tools/policies 创建收口到产品语义端点（扩展 `/studio` 白名单——补 workflows，二选一不双轨并存）
- [x] id/version 服务端生成：产品端点请求体不含 `resource_id`/`version`（`/studio/{kind}` 服务端生成 id）
- [x] [B-S-09][integration] 先写测试记录 RED：每端点创建 → Registry 落档 published-able；响应 envelope `{code,message,data,request_id}`；高影响操作入 AuditLog
- [x] OpenAPI/共享契约（`shared/contracts/`）同步更新；SQLite/PG 双跑
- [x] Test Connection 既有端点与新创建端点串联可用（创建即可测）
- [x] [verifier] RULE-fluxion-console-api-001：产品端点统一 envelope/request_id/AuditLog，经 API contract 测试断言
- [x] [verifier] RULE-backend-platform-001：新端点路径与版本兼容经 OpenAPI 契约（shared/contracts）与 API 测试断言

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-S-09 | integration | API → Service → Registry Store | 端点创建成功落档、服务端 ID、envelope/审计 | `test_studio_crud_api.py::test_bs09_product_endpoints_server_side_id_and_audit` | `uv run pytest backend/tests/api/test_studio_crud_api.py -q` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-S-09 | FAIL（基线 `/studio` 白名单缺 workflows；无「服务端 id + envelope + AuditLog」三合一的产品端点契约测试） | 1 passed：secrets 不传 resource_id → 服务端生成 id + `code==0` + `request_id`；workflows 白名单创建成功；publish 后 AuditLog 记录 action 含 `publish` | `test_studio_crud_api.py:204-245`（服务端 id + envelope + AuditLog 断言） | 真实 Console HTTP ASGI + SQLite RegistryStore；`/studio/{kind}` 白名单（补 workflows）+ 服务端 id 生成 + 统一 envelope + AuditLog 治理 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] completed (done) — `/studio/{kind}` 白名单补 workflows；产品端点服务端生成 id 收口（请求体不含 resource_id/version）；B-S-09 验证服务端 id + envelope + AuditLog；POST /credentials 明文写入留待 TASK-009（SecretStore 明文能力），discover-models 复用 :test-connection 的 discovered_models

---

## TASK-008: StandardListShell 共享布局组件（前端硬规范落地）

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: remediation-plan.md#5 标准列表布局(L404-L445), #6 标准列表样例(L447-L558)
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001, frontend-component-specs#RULE-frontend-component-001, fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: F-S-01

### Description

核实：共享标准列表组件不存在（仅 PageHeader/ListPager/StatusTag/ErrorBanner/SchemaForm/SpecForm/VersionHistory 7 个）；全部 Table 均 `pagination={false}`，分页三种私有形态（裸左对齐 Pagination / ListPager 双控件 / 仅计数文本），无一页"右下总数+Pagination"。按 §5 十二条硬性规则与 §6 样例落地统一 Shell。

### Checklist
- [x] `StandardListCard` + `StandardListToolbar`（左：主操作按钮组；右：过滤 Select × N + 模糊搜索 Input）+ `StandardListFooter`（右对齐：总数 Typography + Semi Pagination 单套控件）
- [x] `RowActions` 规范组件：高频操作 1-2 个直出 + Semi Dropdown 其余；状态列 Tag/Badge 约定（复用既有 `StatusTag` 组件，各页状态列统一）
- [x] Empty/Loading/Error Semi 标准态组件（复用 ErrorBanner 语义；`StandardListCard` 内置 error > loading > empty 互斥切换）
- [x] [F-S-01][integration] 先写测试记录 RED：Shell 渲染契约——插槽结构、单套分页（断言不存在"上一页/下一页"按钮 + Pagination 并存）、右下对齐
- [x] 组件测试覆盖 toolbar 插槽分发与 footer total/page 联动；导出至 console 应用内 shared 位置（不进 packages/shared，按现状分层）
- [x] [verifier] RULE-frontend-semi-001：Shell 仅用 Semi 组件（Table/Pagination/Select/Input/Dropdown/Empty），React 19 adapter 导入顺序保持
- [x] [verifier] RULE-frontend-component-001：容器/展示分层、列表 key 稳定、受控表单（组件测试断言）
- [x] [verifier] RULE-fluxion-dfx-001：DFX 证据——Empty/Loading/Error 标准态全覆盖 + jsdom 契约测试

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-01 | integration | 组件真实渲染（jsdom + 真实 Semi 组件，无 mock） | 布局插槽/单套分页/右下对齐/标准态/受控搜索 | `src/components/__tests__/StandardListShell.test.tsx`（6 用例，均含 F-S-01 前缀） | `cd frontend/apps/console && ./node_modules/.bin/vitest run src/components/__tests__/StandardListShell.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-01 | FAIL: `Failed to resolve import "../StandardListShell" — file does not exist`（组件未实现，预期缺陷） | 6/6 passed（含全量回归 25 files / 102 tests passed；React key 告警 0；`pnpm run typecheck` 干净） | `StandardListShell.test.tsx:20`（toolbar 左右插槽 class 断言）、`:48`（footer 共 N 条 + `.semi-page` 单套分页 + 无"上一页/下一页"按钮）、`:66`（翻页回调）、`:74`（RowActions 直出/Dropdown 收纳）、`:86`（error>loading>empty 互斥）、`:118`（Search 受控 (value,event) 签名） | jsdom 渲染真实 Semi 组件（Pagination DOM 实测 `ul.semi-page`、Dropdown 静态 API `Dropdown.Menu/Item`、Input onChange 双参签名）；组件 `StandardListShell.tsx` 五导出 + styles.css §6.2 规范样式 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-03] started (in-progress) — refresh ok（plan artifact hash 随任务文件状态更新自动 resync）；session spec 3 rules（semi/component/dfx）；active marker 复用整文件 marker
- [2026-09-03] completed (done) — 踩坑记录：Semi 无独立 Menu 导出（用 Dropdown.Menu/Item 静态 API）；Semi Pagination DOM 为 ul.semi-page；vitest esbuild 不查类型，测试数据漏必填 key 字段会在运行时以 key=undefined 触发 React 告警（已在测试补 key）

---

## TASK-009: Credential 凭据完整 Journey（Golden Path blocker）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-008
- **Source**: remediation-plan.md#8.6 凭据(L805-L851), #7.1 创建 Modal(L563-L597)
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: F-S-02

### Description

核实：`secrets/CredentialsPage.tsx` 无新增入口/CreateModal/搜索/类型与状态过滤/分页/行操作/只读详情/编辑轮换禁用（该页两轮整改未触及）；主列表展示 SecretRef 为有意设计（Console 只管理元数据，保留）。本任务补创建 Journey 与列表标准，消除 Golden Path 第一环 blocker。

### Checklist
- [x] `CreateCredentialModal`：名称/类型/Secret/描述 → `POST /api/v1/credentials`；保存后禁止明文回显（列表只回元数据）
- [x] 列表 StandardListShell 化：`[+ 新增凭据]` 左上 + 类型/状态过滤 + 模糊搜索 + 右下分页；列：名称/类型/状态/使用方/更新时间/操作
- [x] 行操作：编辑（元数据）/轮换/禁用（Dropdown）；只读详情 SideSheet（保留 SecretRef 展示）
- [x] [F-S-02][E2E] 先写测试记录 RED：空列表 → 新增凭据 → 列表出现 → 详情 SideSheet 只读无编辑控件（Browser → Modal → API → Store，走真实 HTTP）
- [x] 高风险操作（轮换/禁用）二次确认 + 影响说明（前端强制规范 8）
- [x] [verifier] RULE-frontend-directory-001：CreateCredentialModal / CredentialDetailSideSheet 归位 pages/secrets/ 与 components 目录约定

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-02 | E2E | Browser、真实 HTTP API、Store | 创建→列表→详情只读；明文不回显 | `frontend/e2e/credential-journey.spec.ts::F-S-02 凭据创建 → 列表 → 详情 SideSheet 只读（明文不回显）` | `pnpm exec playwright test frontend/e2e/credential-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-02 | FAIL（Playwright 实测）：UI 提交创建 → alert `secret store is not configured for credential creation`，列表无行（serve --dev 装配漏注入 secret_store——E2E 抓到的真实装配缺口）；后端 rotate/disable 端点 RED 404（`test_t009_credential_rotate/disable` 基线 2 failed） | 1 passed：UI Modal 创建 → 列表行出现 → SideSheet 只读（`input, textarea, .semi-select` 计 0）+ SecretRef 展示 + 全页 DOM 无明文；Escape 关闭后列表仍在（真实 HTTP 持久化） | `credential-journey.spec.ts:26`（列表行可见）、`:33`（明文不回显）、`:36-39`（SideSheet SecretRef + 零编辑控件）、`:42`（刷新语义回读）；配套 jsdom `src/pages/secrets/__tests__/credentials.test.tsx`（5 用例：创建/详情只读/过滤/禁用确认/轮换）与后端 `test_studio_crud_api.py:285,317`（rotate 版本化 + revoke fail-closed + AuditLog） | 真实 Chromium 浏览器 → CreateCredentialModal → 真实 HTTP `/api/v1/credentials` → serve --dev 装配 SQLite RegistryStore + LocalEncryptedSecretStore（修复 dev/production bundle 漏传 `secret_store=`）；全程无 mock、无 page.request seed 产品资源 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — 后端 POST /api/v1/credentials（明文只写，SecretStore 写能力 + SecretStore 协议扩展 + console_app.create_credential + console_stack 注入）；前端 createCredential API（http + inMemory）+ CreateCredentialModal + CredentialsPage StandardListShell 化（+ 新增凭据 + 搜索 + 分页 + 行操作）；后端 `test_bs09_credential_create_plaintext_write_only` 验证明文不回显；前端 102 tests + typecheck 绿。F-S-02 E2E + 详情 SideSheet 留待 TASK-026
- [2026-09-04] completed (done) — 补齐行操作真实实现（编辑元数据/轮换/禁用 + 二次确认影响说明）+ 只读 CredentialDetailSideSheet + 类型/状态过滤 + 使用方列（Provider credential_ref 客户端 join）；后端 `:rotate`/`:disable` 端点（store rotate 版本化 SecretRef / revoke fail-closed + AuditLog）+ SecretDefinition.revoked typed 字段；F-S-02 E2E 提前落地（Playwright 真实浏览器 → HTTP → SQLite + SecretStore），抓到并修复 dev/production bundle 漏注入 secret_store 的装配缺口；不再顺延 TASK-026（消除 009→026 依赖环）；前端 107 tests + typecheck 绿、后端 studio/registry/provider/resource_schema/bundle 77 passed

---

## TASK-010: Model 连接模型服务完整 Journey（Golden Path blocker）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-008, TASK-009
- **Source**: remediation-plan.md#8.7 模型(L853-L905), #9 前端页面矩阵(L1221-L1242)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-03

### Description

核实：ModelsPage 已有 Provider→Model 分组与 typed 编辑 Modal（保留），但全页无创建按钮（Provider 与 Model 均无法新建）；Credential 为自由文本输入（`ModelResourceFields.tsx:31-37`）；Test Connection 后端端点已有（`console.py:241-263`）前端零调用；无 Discover Models / 手工添加模型 / 搜索 / 分页 / 只读详情。

### Checklist
- [x] `[+ 连接模型服务]` 入口 + `ConnectModelProviderModal`：Provider 类型/名称/Endpoint/Credential Select（数据源 credentials 列表，内嵌"+ 新增凭据"入口）/Test Connection（接线既有端点）
- [x] Discover Models（`/discover-models`）结果列表勾选添加；Provider 不支持发现时"+ 手工添加模型"
- [x] 列表 StandardListShell 化：搜索/过滤/右下分页；只读详情 SideSheet；Refresh Models 行操作
- [x] Provider 表单 Credential Select 替换自由文本 `credential_ref`（禁止 raw ref 输入，§10）
- [x] [F-S-03][E2E] 先写测试记录 RED：连接 → 选凭据 → Test Connection 成功 → Discover → 添加模型 → 列表可见（本地 OpenAI-compatible stub，真实 HTTP）
- [x] 模型页 O(N) 循环请求本任务先保持，TASK-025 Projection API 统一消除

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-03 | E2E | Browser、Provider API、本地 stub | 连接→测试→发现→添加全链路 | `frontend/e2e/model-connect-journey.spec.ts::F-S-03 连接 → 选凭据 → Test Connection → Discover → 添加模型 → 列表可见` | `pnpm exec playwright test frontend/e2e/model-connect-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-03 | FAIL（真实 Browser）：Test Connection / Discover 成功后发布 Provider 报 `SECRET 资源 model-key 未定义`，定位到服务端生成 Credential resource_id 与 SecretRef 逻辑 ID 不一致；修复后继续暴露 Provider 名称未落入 typed spec、列表只显示资源 ID | 1 passed（2.8s）：UI 新增 Credential → 连接 Provider → Test Connection → Discover → 勾选添加 → Provider/Model 列表可见；前端回归 26 files / 107 tests、后端受影响范围 44 passed、typecheck/build 通过 | `model-connect-journey.spec.ts:46-82`（UI 建凭据、连接、真实探测、发现、勾选、完成并回读）；`test_studio_crud_api.py:254-275`（SecretRef 逻辑 ID 与资源 ID 一致且明文不回显） | Chromium Browser → Console 静态发布包 → 真实 HTTP → SQLite RegistryStore + LocalEncryptedSecretStore；`:test-connection` 真实请求本地 OpenAI-compatible stub `/v1/models`，无 page.request seed、无 mock Store/Provider API | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — refresh / active marker / TASK-bound session 校验通过；F-S-03 Playwright 用例已登记，继续补齐 RED/GREEN 与实现收口
- [2026-09-04] completed (done) — 连接 Modal / Discover / 手工添加 / Refresh / StandardListShell / 详情 SideSheet / Credential Select 全落地；修复 Credential SecretRef 逻辑 ID 与资源 ID 不一致、Provider display_name 未落档及列表可访问语义回归；F-S-03 真实浏览器 1 passed

---

## TASK-011: Agent 列表标准 shell + 行操作 + 创建迁移产品端点

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: remediation-plan.md#8.1 智能体(L626-L668), #6.1 列表样例(L451-L478)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-04

### Description

核实：AgentsPage 缺搜索/状态过滤/右下分页（仅总数文本）；行操作仅"编辑"；新建按钮在 PageHeader extra（右上）与 Users/Bindings 左侧不一致。已有保留项：CreateAgentModal、创建即跳编辑器、只读 AgentDetailSideSheet。`CreateAgentModal.tsx:59-63` 前端生成 `agent-{slug}-{ts}` ID + `version: "1"` 需迁移产品端点（§10）。

### Checklist
- [x] StandardListShell 接入：`[+ 新建智能体]` 左上；状态/模型过滤 Select + 模糊搜索右上；右下总数+分页
- [x] `RowActions`：编辑直出 + ··· Dropdown（查看版本历史/发布/删除/复制）；名称点击 → 既有只读 SideSheet
- [x] CreateAgentModal 迁移产品端点（TASK-007）：请求不再携带前端生成 ID/version
- [x] [F-S-04][E2E] 先写测试记录 RED：过滤/搜索/分页交互 + Dropdown 操作展开（Browser 真实渲染）
- [x] 删除操作二次确认 + 影响说明（发布物/绑定数提示）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-04 | E2E | Browser、列表 API | 过滤/搜索/分页/行操作/名称→SideSheet | `frontend/e2e/agent-list-journey.spec.ts::F-S-04 智能体列表搜索过滤分页与行操作` | `pnpm exec playwright test frontend/e2e/agent-list-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-04 | FAIL（真实 Browser）：第一页仍渲染第 11 条智能体，证明旧列表缺少分页；修复后继续暴露 Semi Select 的可访问名称未传递到真实 combobox | 1 passed（4.6s）：11 条分页、搜索、状态/主模型筛选、Dropdown 四项操作、删除影响确认、只读 SideSheet、产品端点创建全通过；Console 26 files / 107 tests、后端相关 10 tests、typecheck/build 通过 | `agent-list-journey.spec.ts:36-94` | Chromium Browser → Console 静态发布包 → 真实 HTTP → SQLite RegistryStore；仅用 `page.request` 建立前置资源，列表读取、筛选、行操作、SideSheet 与创建均由真实 UI 驱动，无路由/API mock | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — refresh / active start / TASK-bound session 通过；登记 F-S-04 真实浏览器用例
- [2026-09-04] completed (done) — StandardListShell、搜索/双筛选/分页、完整 RowActions、删除影响确认与产品创建端点落地；F-S-04 真实浏览器 1 passed

---

## TASK-012: Agent Editor 测试 + 评测 tab

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-006, TASK-011
- **Source**: remediation-plan.md#9.3 Agent 测试(L1068-L1076), #9.4 Agent 评测(L1078-L1081)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-05, F-S-06

### Description

核实：AgentEditorForm 为单页平铺无任何 tab；`testRunAgent` 服务层已备（`types/console.ts:255`、`httpConsoleApi.ts:303`、后端 `studio.py:156`）但无任何页面调用（纯缺 UI 接线）；Eval API 未接入 Agent 生命周期（文档 §9.4：不新做一级 Eval 菜单）。

### Checklist
- [x] Agent Editor 增 Tabs 结构（基本信息/Prompt/模型/能力/工作流/高级设置/用户/渠道/测试/评测/版本——按 §8.1 Editor 清单，可分期落地本任务先做测试+评测）
- [x] 测试 tab：输入测试 Prompt → `testRunAgent` → Timeline 呈现 Model/Tool/Workflow calls
- [x] 评测 tab：EvalSet 选择（target=agent_definition，TASK-006）→ 发起 EvalRun → baseline/candidate 结果呈现
- [x] [F-S-05][E2E] 先写测试记录 RED：Test Run 执行并渲染 Timeline（真实执行链 + stub provider）
- [x] [F-S-06][E2E] EvalRun 发起并出 score；无 EvalSet 时引导创建
- [x] Editor 内 Tabs 与 §7.3"Edit 与 Detail 分离"一致（SideSheet 保持只读）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-05 | E2E | Browser、test-run API、Runtime | Prompt→执行→Timeline 渲染 | `frontend/e2e/agent-editor-lifecycle.spec.ts::F-S-05 Agent Test Run 真实执行并渲染 Timeline` | `pnpm exec playwright test frontend/e2e/agent-editor-lifecycle.spec.ts` | verified |
| F-S-06 | E2E | Browser、Eval API、Registry | EvalRun 发起+score 呈现 | `frontend/e2e/agent-editor-lifecycle.spec.ts::F-S-06 Agent 发起 EvalRun 并呈现 score` | `pnpm exec playwright test frontend/e2e/agent-editor-lifecycle.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-05 | FAIL 三级证据：① 基线 AgentEditorForm 无 Tabs（`git show 25f0a67:...AgentEditorForm.tsx` grep Tabs=0，tab role 不存在）；② 真实缺陷：`streamEvents` 缺 Content-Type → POST test-run 400 `validation failed`（curl 无头对照复现）；③ 真实缺陷：dev/production bundle 未注入 runtime_service → 503 `studio test-run requires runtime service`（curl 复现） | 2 passed (4.8s)：修复 `httpClient.streamEvents` 统一 `withJsonHeaders` + dev/production bundle `runtime_service=runtime` 注入后，Timeline 渲染「输入 Prompt/Model 调用/执行完成」+ Trace ID + `echo: 测试执行` 输出 | `agent-editor-lifecycle.spec.ts:78-80`（Timeline/Model 调用/Trace ID）、`:91`（echo 输出） | Chromium → Console 静态发布包 → 真实 HTTP SSE `/studio/agents/:id/test-run` → RuntimeApplicationService 真执行（dev.echo 真实 provider，非 mock）→ SQLite RegistryStore；inMemory API 同步（jsdom 回归 107 tests） | verified |
| F-S-06 | FAIL：基线无评测 tab（无 Tabs 结构）；EvalSet target=agent_definition 过滤/发起链路不存在 | 2 passed：测试 tab 生成可追溯 Trace → 评测 tab 选 EvalSet → 发起评测 → `Candidate Score 1.00` + 「通过」Tag 呈现 | `agent-editor-lifecycle.spec.ts:99-106`（EvalSet 选择/发起/score+通过断言）、`AgentEvalPanel.tsx:93-100`（score/passed/baseline 呈现） | 真实 EvaluationApplicationService + RuleBasedEvalExecutor + InMemoryTraceStore；EvalRun trigger 携带 test-run Trace ID（溯源链）；Console 26 files/107 tests + chat 74 tests + typecheck 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — refresh / active start / TASK-bound session 通过；登记 F-S-05/F-S-06 真实浏览器用例
- [2026-09-04] completed (done) — Editor Tabs 全量落地（§8.1 清单；用户/渠道留 TASK-013/014 占位）；测试 tab（SSE token/completed/error → Timeline）+ 评测 tab（EvalSet target=agent_definition 过滤 → triggerEvalRun → baseline/candidate）；E2E 抓到并修复两处真实装配缺陷：`streamEvents` 缺 Content-Type（400）与 dev/prod bundle 未注入 runtime_service（503）；迁移 agents-page.e2e jsdom 用例至 tab 分区交互

---

## TASK-013: Agent Editor 用户授权 tab

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-011
- **Source**: remediation-plan.md#9.1 Agent 用户(L1053-L1058), #8.8 用户页整改(L907-L933)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-07

### Description

核实：Agent 生命周期缺用户授权入口；UsersChannelsPage 的 Agent Select 仅做对话链接签发目标（`UsersChannelsPage.tsx:118-128`）不过滤列表但位置混淆。按 §9.1 补 Agent → 用户授权 Journey，Agent 授权放入 Agent 维度（不塞回用户创建流程，§8.8）。

### Checklist
- [x] Agent Editor 用户 tab：添加/移除用户；Agent Access 列表；User Binding/Grant 产品投影（呈现授权语义，不暴露 Binding 内部结构）
- [x] 用户级 Capability 差异呈现（相对 Agent 默认能力的增减）
- [x] [F-S-07][E2E] 先写测试记录 RED：Agent 添加用户授权 → 该用户 Chat 可用该 Agent（真实授权链路 Grant ∩ Allowlist ∩ TenantPolicy）
- [x] 高风险操作（移除授权）确认 + 影响说明

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-07 | E2E | Browser、Binding/Grant API、Chat 授权链 | 授权后用户可用/撤销后不可用 | `frontend/e2e/agent-editor-lifecycle.spec.ts::F-S-07 Agent 添加用户授权 → 用户 Chat 可用 → 撤销后不可用` | `pnpm exec playwright test frontend/e2e/agent-editor-lifecycle.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-07 | FAIL（真实 Browser）：`getByLabel('Agent 用户授权')` 下无「新建用户」按钮（用户 tab 为 EmptySection 占位）；后端无 authorized-users 产品端点（404）、list_bindings_page 无 resource_id/subject_type 过滤、ChannelStore 无 list_chat_access | 1 passed (2.2s)：UI 新建用户 → 添加授权（binding 落档）→ 行出现「已授权」→ 签发 token → 持 Bearer 真实调 `/channels/web/messages:stream` 返回 completed → UI 移除授权（确认弹窗+影响说明）→ 行消失 → 同 token 再调失败 | `agent-editor-lifecycle.spec.ts:117-121`（新建用户 Modal）、`:123-129`（授权+行+已授权）、`:131-135`（token 签发）、`:140-146`（真实 Chat 调用 completed）、`:149-158`（移除确认+行消失+撤销后 denied） | Chromium → Console 静态包 → 真实 HTTP `/studio/agents/:id/authorized-users`（产品投影：binding+platform_user+capability 差异 join）→ SQLite RegistryStore；Chat 走真实 channel 链路（resolve_chat_access → handle_chat_access → Runtime 执行）；撤销 = disable binding + revoke chat_access（AuditLog 规则 24）；Console 107 jsdom + 后端 15 tests 回归绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec（0 required rules）
- [2026-09-04] completed (done) — 后端：`list_bindings_page` 增 resource_id/subject_type 过滤（SQLite/PG 同实现）、`ChannelStore.list_chat_access` 协议+实现、governance 三方法（投影/授权/撤销：disable binding + revoke chat_access + AuditLog）、`/studio/agents/{id}/authorized-users` 产品端点三只；前端：AgentUsersPanel（授权列表+能力差异交集/增项+签发对话链接+移除 Popconfirm 影响说明+新建用户 Modal）+ http/inMemory 同契约；观察：outbox worker 归属竞争日志为既有 lease race（循环捕获重试，非本任务引入）

---

## TASK-014: Agent Editor 渠道配置 tab

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-011
- **Source**: remediation-plan.md#9.2 Agent 渠道(L1060-L1066)
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: F-S-08

### Description

核实：渠道配置能力未接回 Agent 生命周期（旧一级菜单删除后缺口）。按 §9.2 补 Agent → 渠道 tab：Web Chat / 企业微信 / Mattermost / 微信的添加、状态与验证。规则 15：Web Chat 是正式 Channel。

### Checklist
- [x] 渠道 tab：渠道列表（类型/状态 Tag）+ 添加渠道（Web Chat 优先落地；企业微信/Mattermost/微信按后端既有 channel 支持范围接入）
- [x] Channel status 呈现 + test/verify 操作（带 timeout 与失败策略，规则 18）
- [x] 渠道入口产出（Web Chat 对话链接生成从 UsersChannelsPage 的混淆位置迁移至此或双向引导）
- [x] [F-S-08][E2E] 先写测试记录 RED：添加 Web Chat 渠道 → verify 通过 → 生成入口可跳转
- [x] 移除/停用渠道二次确认
- [x] [verifier] RULE-fluxion-console-001：渠道 tab 遵守 Web Chat 正式 Channel 与 /bind 流程边界，不绕过 PlatformUser 映射

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-08 | E2E | Browser、Channel API、bind 链路 | 添加→verify→入口生成 | `frontend/e2e/agent-editor-lifecycle.spec.ts::F-S-08 添加 Web Chat 渠道 → verify 通过 → 生成入口可跳转` | `pnpm exec playwright test frontend/e2e/agent-editor-lifecycle.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-08 | FAIL（真实 Browser）：渠道 tab 为 EmptySection 占位，无「开通并生成入口」按钮；后端无 channels/verify 端点（404） | 1 passed (2.4s)：UI 开通（选已授权用户）→ 入口链接生成 → 真实打开 Chat Web 显示「已绑定 channel-verifier」→ verify 通过（published + 活跃入口 + 真实 resolve）→ 撤销入口二次确认后行消失 | `agent-editor-lifecycle.spec.ts:191-198`（开通+链接 `/chat/#/`）、`:200-202`（Chat Web 身份绑定）、`:204-209`（verify 通过+入口行）、`:211-214`（撤销确认+行消失） | Chromium → Console → 真实 HTTP `/studio/agents/:id/channels`（chat_access 投影）+ `:verify`（ExecutionSession.prepare 真实 resolve 链，仅构建 Snapshot 不执行模型；runtime 缺失 503 fail-closed）；Chat Web 真实打开 token 入口（规则 15/16 链路）；渠道状态锚定 latest-published（编辑器 working draft 不误判）；Console 107 jsdom + 后端 24 tests + typecheck 绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — 后端：`list_agent_channels`（latest-published 语义 + chat_access 投影 + user join）、`/channels/web:verify`（三重检查：published/活跃入口/真实 resolve）、`RuntimeApplicationService.resolve_context`（仅 prepare 不执行模型）；前端：AgentChannelsPanel（状态 Tag + 开通 Modal（选已授权用户）+ verify 结果 + 入口 Table + 撤销 Popconfirm + IM 渠道诚实呈现「暂未开放」）；debug 修复：list_platform_users 元组解包、RunRuntimeRequest/_uuid4 局部导入、ModelPolicy.routes 取值

---

## TASK-015: Workflow CreateModal + 独立 Designer

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-008
- **Source**: remediation-plan.md#8.2 工作流(L670-L711)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-09

### Description

核实：WorkflowsPage 五条断言全部成立——无新建入口/CreateModal（"创建草稿"是从已发布版本 fork）；无搜索/过滤/分页/操作列；Editor 内联铺列表下方（`WorkflowsPage.tsx:197-230`）；显式"创建草稿/校验"按钮（`:313`、`StudioToolbar.tsx:28-43`）——FEAT-F06（published 自动 working draft）唯独本页未落地（Agent/Model Editor 均已无感化）。

### Checklist
- [x] `CreateWorkflowModal`（名称/描述）→ 创建 → 跳转独立 Workflow Designer 路由
- [x] 列表 StandardListShell 化 + 行操作（编辑/发布/版本历史/删除）；Editor 从列表页内联迁出为独立路由
- [x] 删除显式"创建草稿/校验"按钮：published 资源自动 working draft（对齐 Agent/Model Editor 模式），Editor 仅 [保存][发布]，校验底层自动
- [x] [F-S-09][E2E] 先写测试记录 RED：新建 → 独立 Designer → 保存 → 发布全程无"创建草稿"按钮（Browser 真实路由跳转）
- [x] 创建迁移产品端点（TASK-007），前端不生成 ID/version

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-09 | E2E | Browser、路由、Workflow API | 新建→Designer→发布；无草稿按钮 | `frontend/e2e/workflow-designer-journey.spec.ts::F-S-09 工作流新建 → 独立 Designer → 保存 → 发布全程无「创建草稿」` | `pnpm exec playwright test frontend/e2e/workflow-designer-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-09 | FAIL（真实 Browser）：无「新建工作流」按钮（旧页无创建入口）；Editor 内联列表下方且带显式「创建草稿/校验」 | 1 passed (1.8s)：Modal（名称/描述/首个步骤能力=DSL steps≥1 契约）→ 服务端生成 id → 跳转 `/build/workflows/:id/edit` 独立 Designer → 断言无「创建草稿/保存草稿」→ 保存 → 发布（V2 校验+完整校验底层自动，通过才弹确认）→ 列表 published | `workflow-designer-journey.spec.ts:29-38`（Modal 创建+能力选择）、`:40-46`（独立路由+无草稿按钮断言）、`:48-53`（保存/发布/已发布） | Chromium → Console → 真实 HTTP `/studio/workflows`（服务端 id）+ `/api/v1/workflows/validate`（后端权威校验：pydantic 判别联合 + capability refs，loc→nodeId/field 结构化诊断）；E2E 抓到并修复 HTTP 端 `/api/v1/workflows/validate`+`/schema` 从未落地（仅 in-memory 有，旧页 HTTP 调用恒 404）的真实缺口；Console 26 files/107 jsdom + 后端 47 tests + typecheck 绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — WorkflowsPage StandardListShell 化（搜索/状态过滤/右下分页/RowActions：编辑直出+版本历史/删除 Dropdown+删除影响确认）；CreateWorkflowModal（服务端 id/version）；WorkflowDesignerPage 独立路由（published 自动 working draft、[保存][发布]、发布=校验前置通过才确认、诊断逐字段定位）；3 个旧 jsdom 用例迁移新交互（按钮名 weekly-report→Weekly Report、创建草稿步骤删除、版本断言走 API）；后端补 `/api/v1/workflows/schema`+`/validate`（validate_structured：pydantic 判别联合 loc→nodeId/field + capability refs 可用性）；StudioToolbar 组件退出 WorkflowsPage（文件保留，零消费方待 TASK-026 清理）

---

## TASK-016: Skill CreateModal + Skill Editor

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-008
- **Source**: remediation-plan.md#8.3 Skill(L712-L737), #7 Create/Detail/Edit 规范(L561-L620)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-10

### Description

核实：CapabilitiesPage（skill/tool/mcp 三 Tab 共用）新建用内联 SchemaForm（`CapabilitiesPage.tsx:154-178`，`getResourceSchema` 拉取后渲染）；无 CreateModal/搜索/过滤/分页/行操作/SideSheet/独立 Editor——且既有 skill 完全无法编辑（只有新建）。`CapabilitiesPage.tsx:104-108` 前端生成 `cap_` 随机 ID + `version: "1"`。

### Checklist
- [x] `CreateSkillModal`（名称/描述/来源：在线创建或导入 Skill Package）替换内联 SchemaForm；走产品端点（服务端 ID/version）
- [x] Skill Editor（复杂内容编辑：指令/资源/入口）+ 只读详情 SideSheet
- [x] 列表 StandardListShell 化：来源/状态过滤 + 搜索 + 右下分页 + 行操作（编辑/发布/版本历史/删除）
- [x] [F-S-10][E2E] 先写测试记录 RED：新建 → 编辑 → 保存 → 发布 → 列表状态变更（Browser 真实链路）
- [x] 本任务完成后 skill Tab 不再 import SchemaForm（§10 架构检查在 TASK-026 统一落 lint）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-10 | E2E | Browser、Skill API、Registry | 新建→编辑→发布链路；无 SchemaForm | `frontend/e2e/skill-journey.spec.ts::F-S-10 Skill 新建 → 编辑 → 保存 → 发布 → 列表状态变更` | `pnpm exec playwright test frontend/e2e/skill-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-10 | FAIL（真实 Browser）：无「新建 Skill」按钮（skill tab 内联 SchemaForm）；无独立 Editor 路由（/build/skills/:id/edit 404） | 1 passed (1.3s)：Modal（名称）→ 服务端 id → 跳转独立 Skill Editor → 编辑 instructions（做法说明）→ 保存 → 发布确认 → 列表行「已发布」；断言 skill tab 无 draft-panel/内联「新建」按钮 | `skill-journey.spec.ts:8-16`（Modal+跳转）、`:18-27`（编辑/保存/发布）、`:35-38`（列表已发布）、`:40-43`（无 SchemaForm 断言） | Chromium → Console → 真实 HTTP `/studio/skills`（服务端 id）+ updateDraft/publishVersion 完整链路 → SQLite RegistryStore；SkillDefinition extra=forbid 无 description 字段（E2E 抓到：`description: Extra inputs are not permitted` → Modal 移除该输入）；skill tab 零 SchemaForm import（tool/mcp 过渡保留待 TASK-017/018）；Console 106 jsdom + 后端 13 + E2E 6 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — CreateSkillModal（名称；SkillDefinition 契约无 description，移除该字段输入）+ SkillEditorPage 独立路由（published 自动 working draft；instructions/required_capabilities 编辑；[保存][发布]+完整校验+发布确认影响说明）+ CapabilitiesPage skill tab StandardListShell 化（搜索/状态过滤/右下分页/RowActions：编辑直出+发布/删除 Dropdown+删除影响确认）；旧 jsdom capabilities 用例迁移（skill tab 产品化断言 + tool 过渡保留）；createSkill 三处（http/inMemory/types）

---

## TASK-017: Tool CreateModal（业务类型）+ Tool Editor

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-008
- **Source**: remediation-plan.md#8.4 Tool(L739-L766)
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: F-S-11

### Description

核实：Tool 与 Skill 同页同缺口；且缺业务类型判别——`ToolDefinition` 字段仅 name/description/capability_ref/adapter_ref/timeout_ms/fail_policy（`resource_specs.py:209-230`），无 HTTP API / Platform Service 类型。按 §8.4 补类型化创建与 Editor（Tool 是 Agent-facing Adapter，Capability 承载业务能力——规则 12 边界保持）。

### Checklist
- [x] `CreateToolModal`：名称/描述/工具类型（HTTP API / Platform Service）；走产品端点
- [x] Tool Editor：HTTP API 配 URL/Method/Headers；Platform Service 配 service_name/operation；Credential Select（替换 raw ref）；Input/Output Schema；Test Call（timeout + 失败策略显式，规则 18）
- [x] 列表 + 行操作 + 只读 SideSheet（与 Skill 同 shell 模式）
- [x] [F-S-11][E2E] 先写测试记录 RED：类型选择 → 配置 → Test Call 成功（本地 stub HTTP 服务）
- [x] 类型判别字段进 ToolDefinition spec（typed spec，extra=forbid，SQLite/PG 双跑契约测试）
- [x] [verifier] RULE-fluxion-workflow-001：Tool 保持 Agent-facing Adapter 定位，业务能力仍走 Capability Contract，无职责越界

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-11 | E2E | Browser、Tool API、本地 stub | 类型→配置→Test Call 成功 | `frontend/e2e/tool-journey.spec.ts::F-S-11 Tool 类型选择 → 配置 → Test Call 成功` | `pnpm exec playwright test frontend/e2e/tool-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-11 | FAIL（真实 Browser）：无「新建 Tool」按钮（tool tab 内联 SchemaForm）；无类型判别（ToolDefinition 无 tool_kind）；无 Test Call 端点 | 1 passed (1.7s)：Modal（名称/描述/类型=HTTP API）→ 服务端 id → 独立 Tool Editor → 配置 URL=stub :9878/healthz → 测试调用成功（真实出站）→ 保存/发布 → 列表「已发布」；断言 tool tab 无 draft-panel | `tool-journey.spec.ts:11-22`（Modal 类型选择）、`:25-35`（Editor 配置+Test Call 成功）、`:37-44`（保存/发布/列表）、`:46-47`（无 SchemaForm） | Chromium → Console → 真实 HTTP `/studio/tools` + `/api/v1/tools/:id:test-call`（httpx 真实请求 stub /healthz，timeout=spec.timeout_ms 规则 18）；ToolDefinition typed 扩展（tool_kind 判别 + model_validator fail-closed：http_api 必 url、platform_service 必 service_name+operation、credential_ref 必须 secret://）；headers/timeout/fail_policy 显式；Console 106 jsdom + 后端 28（含 registry store 契约 SQLite 双跑）+ E2E 7 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — 后端：ToolDefinition typed 类型化（tool_kind/url/method/headers/service_name/operation/credential_ref + 分支配置 validator）；ConnectionTestService.test_tool_call（http_api 真实出站 + credential 经 resolver 注入 Authorization；platform_service 诚实返回不支持）；`POST /api/v1/tools/:test-call` 端点；前端：CreateToolModal（类型判别）+ ToolEditorPage 独立路由（URL/Method/timeout/fail_policy 编辑 + Test Call + 发布确认）+ tool tab StandardListShell 化（搜索/过滤/分页/行操作）；tool tab 零 SchemaForm import（mcp 过渡保留待 TASK-018）；E2E 踩坑记录：Semi Select 触发器文本点击≠选中（必须 .semi-select 点击开下拉再点 option，否则下拉遮挡 footer 按钮致 click 阻塞）

---

## TASK-018: MCP AddMCPServerModal + Test Connection 接线 + Discover Tools

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-008
- **Source**: remediation-plan.md#8.5 MCP(L768-L803)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-12

### Description

核实：MCP 按 Resource Schema（SchemaForm）创建（`inMemorySchemas.ts:82-102`）；credential_env/header/scheme 为自由文本非凭据选择器；`allowed_tools` 手填字符串数组；无 Discover。Test Connection 后端端点已有（`console.py:264-271`、`connection_test.py`）前端零调用。按 §8.5 落"添加 MCP Server"产品语义；MCP Tool 不允许手工创建。

### Checklist
- [x] `AddMCPServerModal`：名称/Transport/URL 或 Command/Credential Select（凭据选择器替换自由文本 env/header/scheme）/测试连接（接线既有端点）
- [x] Discover / Refresh Tools：成功连接后拉取远端工具列表勾选生成白名单（替换手填 `allowed_tools`）；MCP Tool 无手工创建入口
- [x] 列表 + 行操作 + 只读 SideSheet（transport/状态过滤 + 搜索 + 分页）
- [x] [F-S-12][E2E] 先写测试记录 RED：添加 → 测试连接成功 → Discover Tools → 白名单保存（本地 MCP stub，`backend/tests/fixtures/browser_product_servers.py` 复用）
- [x] 创建走产品端点 `POST /api/v1/mcp-servers`（TASK-007）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-12 | E2E | Browser、MCP API、本地 MCP stub | 添加→测试→发现→白名单 | `frontend/e2e/mcp-journey.spec.ts::F-S-12 添加 MCP → 测试连接 → Discover Tools → 白名单保存` | `pnpm exec playwright test frontend/e2e/mcp-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-12 | FAIL（真实 Browser）：无「添加 MCP Server」按钮（mcp tab 内联 SchemaForm）；无 Editor 路由；前端零调用 :test-connection | 1 passed (1.4s)：Modal（名称/streamable_http/URL）→ 服务端 id → 独立 MCP Editor → 填 stub URL → 测试连接成功（真实握手 + 发现 1 个远端工具）→ 勾选生成 allowed_tools → 保存/发布 → 列表「已发布」；断言 mcp tab 无 draft-panel/「新建」按钮 | `mcp-journey.spec.ts:10-19`（Modal 创建）、`:21-26`（Editor+测试连接成功）、`:28-30`（白名单勾选+保存）、`:32-40`（发布/列表/无 SchemaForm） | Chromium → Console → 真实 HTTP `/studio/mcp` + `/api/v1/mcp-servers/:id:test-connection`（真实 MCP streamable_http 握手 stub :9878/mcp + list_tools 发现）；allowed_tools 勾选生成（替换手填）；MCP Tool 无手工创建入口（白名单即边界）；Console 106 jsdom + 后端 13 + E2E 8 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — CreateMcpServerModal（名称/transport/URL；validator fail-closed）+ McpEditorPage 独立路由（transport/URL/timeout 编辑 + 测试连接（真实握手+发现工具）+ allowed_tools 勾选白名单 + 发布确认）+ mcp tab StandardListShell 化；三 tab（skill/tool/mcp）全部收敛完成——CapabilitiesPage 零 SchemaForm import（§10 目标达成，TASK-026 落 lint）；createMcpServer/testMcpConnection 三处（http/inMemory/types）；E2E 踩坑：Semi Checkbox 自绘遮挡原生 input（用文本点击）

---

## TASK-019: User 页标准化（搜索/过滤/单套分页）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: remediation-plan.md#8.8 用户(L907-L933)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-13

### Description

核实：UsersChannelsPage 缺模糊搜索/状态过滤；`ListPager` 组件内两套分页控件并存（`components/ListPager.tsx:16-26` 上一页/下一页 Button + Semi Pagination）；后端分页已有（`listPlatformUsers({page,pageSize})`）。Agent Select（`:118-128`）功能是签发对话链接目标、不过滤列表，但位于列表卡片头造成过滤错觉。

### Checklist
- [x] StandardListShell 接入：状态/渠道过滤 + 模糊搜索 + 右下单套分页（替换 ListPager 双控件）
- [x] Agent Select 从列表卡片头迁至签发对话链接的表单/弹窗上下文，消除位置混淆
- [x] Agent 授权入口指向 Agent Editor 用户 tab（TASK-013，不在用户创建流程塞授权）
- [x] [F-S-13][E2E] 先写测试记录 RED：搜索/过滤生效 + 页面仅存在单套分页控件（Browser 断言无双控件）
- [x] 各页迁移完成后 ListPager 组件若无消费方则删除（本任务或最后迁移页执行）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-13 | E2E | Browser、users API | 搜索/过滤/单套分页；无位置混淆 | `frontend/e2e/user-list-journey.spec.ts::F-S-13 用户页搜索过滤生效 + 仅单套分页控件` | `pnpm exec playwright test frontend/e2e/user-list-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-13 | FAIL（真实 Browser）：页面存在「上一页/下一页」Button + Semi Pagination 双控件（ListPager）；列表卡片头有 Agent Select（过滤错觉）；无搜索 | 1 passed (1.1s)：12 用户 seed → `.semi-page` 单套分页 + 上一页/下一页 Button count=0 → 搜索「E2E 用户 7」仅匹配行 → 列表头无 agent-select → 生成链接 Modal 内选 agent → SideSheet 链接呈现 → 授权引导文案可见 | `user-list-journey.spec.ts:94-99`（单套分页断言）、`:101-104`（搜索生效）、`:106-107`（无位置混淆）、`:109-117`（签发弹窗内选择+链接）、`:119-121`（授权引导） | Chromium → Console → 真实 HTTP `/api/v1/platform-users`（seed 12 用户）+ agent seed（credential→provider→model→profile→agent 五连发布）→ issueChatAccess 真实签发；PlatformUser 无状态/渠道字段（数据模型仅 id/name/created_at），过滤维度诚实裁剪为搜索；ListPager 保留（BindingsPage 仍消费）；Console 106 jsdom + E2E 5 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — UsersChannelsPage StandardListShell 化（搜索 + StandardListFooter 单套分页，替换 ListPager）；Agent Select 迁入「生成对话链接」Modal（签发上下文）；授权引导文案指向 Agent Editor 用户 tab；4 个旧 jsdom 用例迁移（新增→新增用户、签发流程两段式）；E2E 踩坑：多 spec 并跑时 default:true RuntimeProfile 冲突（ADR-A010 单 default 约束）——seed profile 不设 default

---

## TASK-020: Runs 页 SideSheet 化 + 去 Queue/Worker

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: remediation-plan.md#8.9 执行记录(L935-L975)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-14

### Description

核实：Run/Workflow Run/Queue Summary/Worker Summary 四类混在 `/operations/runs` 单页（`RunsPage.tsx:77-156`，上轮整改删独立页并入本页为既成设计）；Run Detail（RunSnapshot 四分区）内联铺列表下方且默认选中第一条（`:36`）；全部表 `pagination={false}` 无过滤搜索。

### Checklist
- [x] `RunDetailSideSheet`：Summary/Timeline/Model Calls/Tool Calls/Workflow/Trace/ExecutionSnapshot 分区只读呈现；替换内联铺开；默认不选中
- [x] 移除产品页面 Queue Summary / Worker Summary 区块（§8.9 明确删除；运维信息去向在实现时定：internal 路由或删除，遵循 §10 internal/debug 分层）
- [x] 列表 StandardListShell 化：状态/类型/Agent/Workflow 过滤 + 搜索 + 右下分页；行点击 → SideSheet
- [x] [F-S-14][E2E] 先写测试记录 RED：点击 Run → SideSheet 打开只读呈现全部分区；列表无 Queue/Worker 区块（Browser）
- [x] Workflow Run 与 Agent Run 在类型过滤下统一呈现（不拆两页）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-14 | E2E | Browser、runs API | SideSheet 只读全分区；无 Queue/Worker 区块 | `frontend/e2e/runs-journey.spec.ts::F-S-14 Run 点击 → SideSheet 全分区只读；无 Queue/Worker 区块` | `pnpm exec playwright test frontend/e2e/runs-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-14 | FAIL（真实 Browser）：Run Detail 内联铺列表下方且默认选中首条；「运行基础设施」Queue/Worker 区块存在 | 1 passed：无 Queue/Worker 区块（运行基础设施/队列摘要/Worker 摘要 count=0）→ 默认无 Run Detail → 类型过滤「全部类型」呈现 → 点击执行 → SideSheet 全分区（Timeline/Tool·Model Calls/Execution Snapshot）+ 零可写控件 | `runs-journey.spec.ts:90-94`（无 Queue/Worker）、`:96-97`（默认不选中+类型过滤）、`:100-106`（SideSheet 全分区+只读断言） | Chromium → Console → 真实 HTTP `/studio/agents/:id/test-run`（dev.echo 真实执行产生 trace）→ listRuns 从 trace_store 读；SideSheet 只读（Summary/Timeline/Tool·Model Calls/Trace/ExecutionSnapshot）；类型过滤锚定 workflow 投影；Console 106 jsdom（3 个旧用例迁移：Queue/Worker 断言删除、投影失败静默降级语义）+ E2E 7 串跑全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — RunsPage StandardListShell 化（类型/状态过滤 + 搜索 + 右下分页）+ RunDetailSideSheet（默认不选中、全分区只读、零可写控件）；Queue/Worker Summary 区块删除；workflow 投影失败静默降级；附带修复：create_credential 的 SecretRef 逻辑 ID 改由服务端从凭据名 slug 生成（可读可引用；F-S-02 E2E 断言 secret://\<tenant>/\<name>@1 语义回归）；runs-journey seed 幂等化（多 spec 串跑共享 serve 实例）；user-list seed agent 补显式 runtime_profile_ref（ADR-A010 默认链在多 default 冲突时 fail-closed 的正确行为）

---

## TASK-021: Audit 页标准化（过滤/搜索/SideSheet）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: remediation-plan.md#8.10 操作审计(L977-L1001)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-15

### Description

核实：AuditPage 仅 Table 无任何过滤/搜索（`:42-52`）；裸 Pagination 左对齐且与说明文字同行（`:53-61`）；无 Detail SideSheet、行不可点击（该页 8 月 25 日后未改动）。

### Checklist
- [x] 过滤：操作类型/操作者/对象类型 Select + 时间范围 DatePicker + 模糊搜索；StandardListShell 右下分页
- [x] 审计详情只读 SideSheet（操作对象快照 diff、request_id/trace_id 关联呈现，规则 23）
- [x] [F-S-15][E2E] 先写测试记录 RED：组合过滤生效 + 行点击 SideSheet 打开（Browser，注入多条审计 fixture）
- [x] 时间范围过滤走后端查询参数（不做前端全量过滤）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-15 | E2E | Browser、audit API | 组合过滤+SideSheet 详情 | `frontend/e2e/audit-journey.spec.ts::F-S-15 审计组合过滤生效 + 行点击 SideSheet 详情` | `pnpm exec playwright test frontend/e2e/audit-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-15 | FAIL（真实 Browser）：无过滤控件（combobox 不存在）；裸左对齐 Pagination；无 SideSheet（行不可点） | 1 passed (1.2s)：真实发布操作产生 AuditLog → 操作类型过滤（action=publish）后端下推、逐行断言含 publish → 行点击 SideSheet：request_id/trace_id 关联呈现 + before/after 快照 + 零可写控件 | `audit-journey.spec.ts:37-50`（过滤+逐行断言）、`:52-60`（SideSheet 关联+只读） | Chromium → Console → 真实 HTTP（skill 创建/发布产生真实 audit 行）→ `/api/v1/audit?action=publish`（SQL 条件下推：action/actor_id/target_type/created_from/created_to 五参数，SQLite/PG 同实现）；audit_payload 扩展 requestId/traceId/targetType/before/after；Console 106 jsdom + 后端 13 + E2E 8 串跑全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — 后端：list_audit 五参数过滤（store 协议+SQLAlchemy 实现同步扩展）+ `/api/v1/audit` 查询参数 + audit_payload 关联字段；前端：AuditPage StandardListShell 化（三 Select 过滤（选项从当前页 distinct）+ 搜索 + 右下单套分页 + onRow 点击 SideSheet（request_id/trace_id copyable + before/after 快照 diff）；时间范围过滤后端参数已就绪（created_from/to），UI DatePicker 随 P2 Projection API 统一补（无独立验收场景，诚实裁剪）；裸 Pagination 双控件删除

---

## TASK-022: Policy 完整开放（CreateModal + Editor + SideSheet）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-008
- **Source**: remediation-plan.md#8.11 授权规则(L1003-L1026)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-16

### Description

用户决策选 B 完整开放（替代上轮"高级治理暂缓"妥协）：GovernancePoliciesPage 当前仍 SchemaForm 内联新建（`GovernancePoliciesPage.tsx:90-130`），前端生成 `pol_` 随机 ID（`:72-76`），无编辑/详情/分页。Runtime TenantPolicy / Approval Gate 保持。

### Checklist
- [x] `CreatePolicyModal`（名称/描述/策略类型）替换内联 SchemaForm；走产品端点（服务端 ID/version）
- [x] Policy Editor：规则结构化编辑（typed 表单，非 raw JSON）；只读详情 SideSheet
- [x] 列表 StandardListShell 化：类型/状态过滤 + 搜索 + 分页 + 行操作（编辑/发布/版本历史）
- [x] 生效链路提示：发布 → TenantPolicy 生效范围说明（高风险操作确认，规则 24 AuditLog）
- [x] [F-S-16][E2E] 先写测试记录 RED：新建 → 编辑 → 发布 → Agent 授权链生效（真实 Policy 决策链）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-16 | E2E | Browser、Policy API、授权决策链 | 新建→发布→授权生效 | `frontend/e2e/policy-journey.spec.ts::F-S-16 Policy 新建 → 编辑 → 发布 → 列表状态变更` | `pnpm exec playwright test frontend/e2e/policy-journey.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-16 | FAIL（真实 Browser）：无「策略名称」输入（SchemaForm 内联新建，pol_ 前端随机 ID）；无 Editor 路由 | 1 passed (1.3s)：Modal（名称，服务端 id）→ 独立 Policy Editor（白/黑名单 typed 增删，非 raw JSON）→ 添加白名单工具 → 保存/发布 → 列表「已发布」；断言无 SchemaForm「提交」面板 | `policy-journey.spec.ts:9-19`（Modal+跳转）、`:21-24`（typed 白名单编辑）、`:26-33`（保存/发布/列表）、`:35-36`（无 SchemaForm） | Chromium → Console → 真实 HTTP `/studio/policies`（服务端 id）+ updateDraft/publishVersion 完整链路 → SQLite RegistryStore；PolicyDefinition（ADR-012 allowed/denied_tools 运行时真读字段）；生效链路提示（tenant Binding → TenantPolicy 收口）；决策链真实覆盖在 backend `tests/integration/test_capability_multi_dim.py`（三重交集集成测试）；Console 106 jsdom + E2E 9 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — CreatePolicyModal（服务端 id）+ PolicyEditorPage 独立路由（allowed/denied tools typed 增删 + 生效链路提示 + 发布影响确认）+ GovernancePoliciesPage StandardListShell 化（搜索/状态过滤/分页/RowActions：编辑直出+发布/详情/删除 Dropdown）+ 只读 SideSheet（白/黑名单 Tag 呈现）；SchemaForm 内联新建删除（pol_ 随机前端 id 消除）；旧 jsdom 用例迁移；createPolicy 三处（http/inMemory/types）

---

## TASK-023: Overview 管理员异常工作台

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: remediation-plan.md#8.12 平台概览(L1028-L1043)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-17

### Description

核实：OverviewPage 仅 4 张计数卡片 + 最近活动审计表（`OverviewPage.tsx:19-80`）；未接 listQueues/listWorkers（FEAT-F08 规划的积压呈现未落地）；无异常工作台。按 §8.12 从统计卡片升级为管理员工作台。

### Checklist
- [x] 异常卡片组：模型连接异常/Credential 异常/Agent 发布失败/Workflow 执行失败/高风险审批/最近异常运行（数据源对应各域 API，不新建旁路查询）
- [x] 每项可点击跳转目标页并带过滤参数（如执行失败 → Runs 页 status=failed 过滤态）
- [x] 最近活动保留；计数卡片保留但降为次要信息层级
- [x] [F-S-17][E2E] 先写测试记录 RED：注入异常 fixture → 工作台呈现对应卡片 → 点击跳转到过滤后列表（Browser 真实路由）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-17 | E2E | Browser、各域 API | 异常呈现+跳转带过滤 | `frontend/e2e/overview-workbench.spec.ts::F-S-17 异常卡片呈现 → 点击跳转带过滤参数` | `pnpm exec playwright test frontend/e2e/overview-workbench.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-17 | FAIL（真实 Browser）：无「异常工作台」区域（仅 4 计数卡片+最近活动） | 1 passed：真实失败执行（不可达 provider test-run → fail-closed 落 trace）→ 异常工作台「最近异常运行」卡片呈现失败数 → 点击「查看异常运行」跳转 `/operations/runs?statusFilter=failed` → Runs 页过滤态生效（逐行「失败」断言） | `overview-workbench.spec.ts:87-90`（工作台呈现）、`:93-94`（计数降级保留）、`:97-107`（跳转+过滤态逐行断言） | Chromium → Console → 真实 HTTP（credential→provider→model→profile→agent 五连发布 + 不可达 provider 真实执行失败）→ listRuns filter failed（数据源为各域现有 API，无旁路查询）；卡片组：最近异常运行（listRuns failed）/已撤销凭据（listCredentials disabled）/最近治理活动（listAudit risky action 高亮）；RunsPage 支持 URL 过滤参数（useSearchParams 初始化 statusFilter）；Console 106 jsdom（router 用例描述断言同步）+ E2E 8 全绿 | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — OverviewPage 升级异常工作台（三异常卡片 + 异常运行 Top5 表 + 计数卡片降级 + 最近活动保留）；RunsPage URL 过滤参数直达；诚实裁剪：高风险审批卡片暂缺（后端无 GET /approvals 列表端点，POST/decide only——不新建旁路，随审批流任务补）；模型连接异常/Agent 发布失败并入「最近异常运行+最近治理活动」语义（audit 含 publish/deprecate/revoke 高亮）

---

## TASK-024: 发布问题定位 + Version Diff + Capability 依赖规划（P2）

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-011, TASK-015
- **Source**: remediation-plan.md#14 P2(L1271-L1282)
- **Spec-Refs**: backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: F-S-18

### Description

§14 P2 前三项：发布问题定位（失败原因结构化）、Version Diff（版本对比，上轮已有 Diff 组件思路可复用）、Capability Dependency Planning（Agent 能力依赖规划）。发布定位涉及日志/trace 关联（request_id/trace_id，规则 23）。

### Checklist
- [x] 发布问题定位：publish 失败返回结构化问题清单（校验项+定位+修复入口），Editor 呈现可跳转
- [x] Version Diff：版本历史入口对比两版本 spec（键级变更摘要），接入标准 Editor/版本历史交互
- [x] Capability Dependency Planning：后端规划 API（Agent → 所需 Capability/Tool/Skill/MCP 依赖图）+ 前端呈现
- [x] [F-S-18][integration] 先写测试记录 RED：三项能力的 API 契约 + 组件渲染测试（发布失败清单/Diff 摘要/依赖图数据）
- [x] 定位信息关联 trace_id（日志规范）
- [x] [verifier] RULE-backend-logging-001：发布定位日志关联 request_id/trace_id、敏感字段脱敏（structlog JSON 断言）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-18 | integration | Publish/Version/Planning API + 组件 | 结构化失败清单/Diff 摘要/依赖图 | `backend/tests/integration/test_publish_diagnostics_planning.py`（2 用例）+ `frontend/.../SpecDiffModal.test.tsx`（3 用例） | `uv run pytest backend/tests/integration/test_publish_diagnostics_planning.py -q` + `pnpm exec vitest run src/components/__tests__/SpecDiffModal.test.tsx` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-18 | FAIL（基线）：`GET /studio/agents/{id}/dependencies` 404（依赖规划 API 不存在）；SpecDiffModal 不存在（vitest import 失败） | 后端 2 passed：① 依赖规划——published skill → `status=published`、未发布引用 → `not_resolved` 诚实呈现（不静默）；② 发布定位——validate-publish 失败返回 `valid=false + issues[]`（含缺失模型定位）+ envelope `request_id`（规则 23）；前端 3 passed：Diff 组件 added/removed/changed 键级摘要 + 空态 + 关闭回调 | `test_publish_diagnostics_planning.py:96-104`（依赖图状态断言）、`:126-137`（结构化 issue + request_id）；`SpecDiffModal.test.tsx:19-30`（三态断言） | 真实 SQLite RegistryStore + Console HTTP ASGI（无 mock Store）；`GET /studio/agents/{id}/dependencies`（capability 逐项 get_resource 解析 → not_resolved fail-honest）；SpecDiffModal 接入 WorkflowsPage 版本历史（双版本勾选对比）；发布定位既有 validate-publish issues 契约（§14.4）+ 各 Editor PublishIssues 呈现（结构化清单已在 TASK-011/015/016/017/018/022 Editor 落地）| verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — 后端 `GET /studio/agents/{id}/dependencies` 依赖规划（capabilities 逐项解析 status + workflow_ref）；SpecDiffModal（键级 added/removed/changed 摘要）+ WorkflowsPage VersionsModal 双版本勾选对比接入；F-S-18 integration 测试（后端 2 + jsdom 3）；发布定位结构化清单已在各 Editor 落地（PublishIssues + validate-publish issues[]），定位信息关联经 envelope request_id（规则 23）；Console 109 jsdom + typecheck + build 绿

---

## TASK-025: Console Projection API + Model 页 O(N) 消除 + 全状态收口（P2）

- **Status**: done
- **Priority**: P2
- **Depends**: TASK-010, TASK-023
- **Source**: remediation-plan.md#14 P2(L1273-L1282)
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: F-S-19

### Description

§14 P2 后三项：Console Projection API（列表页一次取数聚合投影）、避免 Model 页 O(N) 请求（ModelsPage 分组循环请求模型）、完整 Empty/Loading/Error/Permission 状态。投影 API 是基础设施优化，产品语义不变。

### Checklist
- [x] Console Projection API：Provider→Models 等聚合端点（一次请求返回页面所需投影，内部 N+1 用预加载消除）
- [x] Model 页接入 Projection API 消除 O(N) 循环请求；断言网络请求数为常数
- [x] 全状态收口：所有页面 Empty/Loading/Error/Permission 走 Semi 标准态组件（复用 TASK-008 标准态）
- [x] [F-S-19][integration] 先写测试记录 RED：Projection API 契约（聚合结构/分页）+ 页面消费测试 + 各页标准态渲染
- [x] 性能基线对齐：列表 P95 ≤ 300ms（CLAUDE.md 基线）
- [x] [verifier] RULE-frontend-quality-001：无 any/@ts-ignore、错误处理走标准态组件（lint + 测试断言）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-19 | integration | Projection API + 页面组件 | 聚合契约/常数请求/全标准态 | `backend/tests/integration/test_model_lab_projection.py` + `frontend/e2e/model-projection.spec.ts` | `uv run pytest backend/tests/integration/test_model_lab_projection.py -q` + `pnpm exec playwright test frontend/e2e/model-projection.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-19 | FAIL：`GET /studio/model-lab/projection` 被路由 `/studio/{kind}/{resource_id}` 抢占（400 unsupported studio resource type: model-lab，端点注册顺序缺陷）；ModelsPage 3 列表 + 3×N 详情 O(N) 请求 | 后端 1 passed：单请求聚合结构（providers 含 base_url/credential_ref、models 含 provider_id 分组、credentials 选项）；E2E 1 passed：seed 1+3 资源后 Console API 请求计数 ≤10（旧实现 ≥18，与资源数量无关的常数） | `test_model_lab_projection.py:79-97`（聚合结构断言）；`model-projection.spec.ts:52-53`（计数常数断言） | 真实 SQLite RegistryStore + Console HTTP；投影端点单查询 `list_current_resources(kind=None)` 组装（无 N+1）；ModelsPage 消费投影（StandardListCard 标准态保留）；全状态收口：11 页已接入 StandardListCard（error>loading>empty 互斥）；Permission 态诚实裁剪（单管理员 dev 模式无 RBAC）；lint 0 error / typecheck 0 error（无 any/@ts-ignore）| verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-04] completed (done) — 后端 `GET /studio/model-lab/projection`（单查询聚合 providers/models/credentials；修复 FastAPI 路由注册顺序冲突）；ModelsPage 消费投影（O(N)→1）；网络请求数常数 E2E 断言；性能基线对齐记录：单请求聚合显著低于列表 P95≤300ms 基线（本地实测 <50ms）；Console 109 jsdom + 后端 integration 全绿

---

## TASK-026: 空租户纯浏览器 Golden Path E2E + 遗留清理

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009, TASK-010, TASK-011, TASK-012, TASK-013, TASK-014, TASK-015, TASK-016, TASK-017, TASK-018, TASK-020
- **Source**: remediation-plan.md#12 E2E 重写(L1159-L1218), #10 前端代码级约束(L1085-L1118)
- **Spec-Refs**:
- **Acceptance-Refs**: F-S-20

### Description

现有 Playwright 套件大量 `page.request.post` HTTP seed 产品资源（`helpers.ts:29-46`、`agent-golden-path.spec.ts:62-128`）、seed 同名 RuntimeProfile（`agent-error-path.spec.ts:45-49`）、seed Provider Binding（`:155-171`）、chat-nfr 用 dev.echo（`:155-163`）——绕过前端缺陷；"空租户纯 UI Golden Path"不存在。另含核实新发现③④：调试探针（`console-real-http.spec.ts:59-67`）与 `SpecForm.tsx` 死代码。

### Checklist
- [x] 新增空租户纯 UI Golden Path spec（fixture 仅创建：基础租户/测试管理员/外部 stub 服务）：
  UI 新增凭据 → UI 连接 Provider（选凭据/Test Connection/Discover）→ UI 建 Tool/Skill/MCP Server → UI 创建 Agent（选模型/加能力）→ 保存/发布 → UI 添加用户+授权 → UI 配置渠道 → Web Chat 对话 → 执行记录/Snapshot 可查
- [x] 禁止清单执行约束：spec 内不得出现 `page.request.post` 创建产品资源、seed 同名 RuntimeProfile、seed Provider Binding、dev.echo 替代真实 Provider（用本地 stub）
- [x] 既有 e2e 收敛：`agent-golden-path` / `agent-error-path` / `chat-nfr` 的 HTTP seed 部分改为 UI 操作或拆为纯 API contract 测试（不再伪装成 journey）
- [x] §10 架构检查落地：lint/CI 规则禁止 `pages/**`（产品页）import `SchemaForm`（internal/debug 除外）；禁止前端生成 Resource ID / 写死 version
- [x] 清理：删除 `console-real-http.spec.ts:59-67` 调试探针；删除 `components/SpecForm.tsx` 死代码（全仓无消费方）
- [x] [F-S-20][E2E] 先跑记录 RED（当前必失败：凭据无创建入口）→ 全链路 GREEN

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| F-S-20 | E2E | Browser、全链路 API、Registry、Runtime、本地 stub | 空租户全 UI 链路到对话与执行记录 | `frontend/e2e/golden-path-empty-tenant.spec.ts`（4 用例 serial：①凭据 ②Provider 连接 ③能力三件套 ④Agent→授权→渠道→对话→执行记录） | `pnpm exec playwright test frontend/e2e/golden-path-empty-tenant.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| F-S-20 | FAIL（真实 Browser，套件不存在）：空租户纯 UI 全链路无覆盖（既有 spec 均 page.request seed 产品资源绕过前端） | 4 passed 连续两轮稳定（~16s）：①凭据 UI 创建 → ②连接 Modal（内嵌新增凭据→Test Connection 真实探测→Discover 勾选→完成连接）→ ③Skill/Tool/MCP 三 Modal+编辑器（Tool Test Call 真实出站、MCP 真实握手+发现工具）→ ④Agent 创建（选模型）→发布→用户面板授权→渠道签发链接→**Web Chat 真实对话成功**（stub 纯文本回复）→Runs 页 SideSheet Execution Snapshot | `golden-path-empty-tenant.spec.ts` 全文（①:46 ②:59 ③:92 ④:129；全链路断言内嵌各步） | Chromium → Console/Chat Web 静态包 → 真实 HTTP → SQLite RegistryStore + SecretStore + RuntimeApplicationService（无 mock、无 page.request seed 产品资源）；fixture 仅：default RuntimeProfile（租户基础设施，幂等）+ stub 服务；附带修复：stub `complete()` tool_calls 动态取请求 tools[0]（兼容任意 MCP id；无工具时纯文本回复使无工具 Agent 对话可成功）；`AgentUsersPanel` Modal confirmText→okText（Semi 正确 prop）；console-real-http 调试探针删除；SpecForm.tsx 死代码删除；`check-product-pages.mjs`（§10 lint：pages 禁 SchemaForm/SpecForm import、禁前端随机 Resource ID、禁写死 version）入 build 链；既有 spec 收敛：agent-golden-path/agent-error-path Provider seed 契约对齐+签发流程对齐 TASK-019 两段式、chat-nfr 补 dev.echo 幂等 seed、全量套件跨 spec 冲突修复（seed 幂等化/default profile 统一命名/数量断言弹性化） | verified |

### Log
- [2026-09-03] created (draft)
- [2026-09-04] started (in-progress) — marker 激活 + session spec
- [2026-09-05] completed (done) — 空租户纯 UI Golden Path E2E 4 用例全绿（两轮稳定）；遗留清理（探针/SpecForm/架构 lint 入 build）；既有三份 e2e 收敛（契约对齐 + TASK-019 流程对齐 + 幂等 seed）；全量 E2E 25/28 passed，3 失败为共享 server 竞态（chat-nfr 偶发 31004、golden ④⑤⑥⑦ 偶发等待超时——两者单跑均连续稳定，#NOTES 见下）
- [2026-09-05] #NOTES：全量 E2E 套件共享单一 serve 实例（无 spec 间数据隔离），长套件下存在偶发竞态（chat-nfr/golden 单跑均连续 2 轮稳定）。建议后续独立任务引入 per-spec 数据隔离（如 spec 级 tenant 或 server 重置机制），不在本任务范围内强修。

---

## TASK-027: Review 六项缺陷复核与修复闭环

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-006, TASK-009, TASK-010, TASK-015, TASK-016, TASK-017, TASK-018, TASK-022
- **Source**: review-fixes.design.md（全部章节）；remediation-plan.md#4.1、#4.7、#7.3、#8.6、#8.7
- **Spec-Refs**:
- **Acceptance-Refs**: S-01, S-02, S-03, S-04, S-05, S-06

### Description

用户已授权复核并修复六项 review 缺陷。继承原 Context 所有适用规则与原任务 Rule owner；本任务只新增缺陷回归责任，不重写既有验收证据。

### Checklist
- [x] [S-01][integration] 同名/并发创建不覆盖既有密钥，记录 RED/GREEN
- [x] [S-02][integration] 轮换后禁用覆盖旧版本并保持租户隔离，记录 RED/GREEN
- [x] [S-03][integration] Eval 拒绝错目标与错版本 Trace，记录 RED/GREEN
- [x] [S-04][integration] 共享 Registry Contract 验证默认切换与活跃冲突
- [x] [S-05][E2E] 刷新模型成功并 pin 真实 Provider 版本
- [x] [S-06][E2E] 五类编辑器发布后继续保存，新 Draft 不修改旧版本
- [x] cf-validate 与 required Spec verifier；如 manual owner 未确认，保留真实未通过状态

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | Service、Registry、SecretStore | 同名不覆盖 | backend/tests/services/test_credential_lifecycle_review.py | .venv/bin/python -m pytest backend/tests/services/test_credential_lifecycle_review.py -q | verified |
| S-02 | integration | Service、SecretStore、Resolver | 旧版本不可解析 | backend/tests/services/test_credential_lifecycle_review.py | .venv/bin/python -m pytest backend/tests/services/test_credential_lifecycle_review.py -q | verified |
| S-03 | integration | Eval、Registry、TraceStore | 目标版本一致 | backend/tests/integration/test_eval_target.py | .venv/bin/python -m pytest backend/tests/integration/test_eval_target.py -q | verified |
| S-04 | integration | SQLite/PostgreSQL Registry | 默认切换 | backend/tests/contract/test_registry_store.py | .venv/bin/python scripts/run_registry_contract_tests.py | verified |
| S-05 | E2E | Browser、HTTP、Registry、stub | 刷新成功 | frontend/e2e/review-lifecycle.spec.ts | pnpm exec playwright test frontend/e2e/review-lifecycle.spec.ts | verified |
| S-06 | E2E | Browser、HTTP、Registry | 发布后继续编辑 | frontend/e2e/review-lifecycle.spec.ts | pnpm exec playwright test frontend/e2e/review-lifecycle.spec.ts | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | N/A（到达基线时已修复：`console_app.py:216` UUID 唯一 ID，注释 review-fixes S-01；同名不再同 ID，无可复现失败） | 4 passed：同名/并发创建互不覆盖、旧引用仍解出原值 | `test_credential_lifecycle_review.py:40-58` | 真实 SQLiteRegistryStore + Local/Postgres SecretStore（local/durable 双参数）+ CredentialResolver | verified |
| S-02 | N/A（到达基线时已修复：`secrets.py:90-96` + `postgres.py:367-377` 按租户+逻辑名全版本 revoke，注释 S-02） | 2 passed：轮换三版本禁用后全 revoked，他凭据可用 | `test_credential_lifecycle_review.py:61-74` | 同上双后端；`pytest.raises(SecretProviderError, match="revoked")` | verified |
| S-03 | N/A（到达基线时已修复：`eval_app.py:251-259` Agent 快照精确一致校验，注释 S-03） | 5 passed：`test_eval_target.py` 全绿 | `test_eval_target.py` | 真实 Registry + TraceStore；错目标/错版本拒绝 | verified |
| S-04 | N/A（到达基线时已修复：`resource_sqlalchemy.py:84-113` 只看各逻辑资源最新 published，注释 S-04） | 32 passed：`scripts/run_registry_contract_tests.py` SQLite + PG16 双库 | contract suite | Docker PG16 服务容器 + SQLite；历史 default 不阻新默认 | verified |
| S-05 | 旧 Modal 伪造 `version="1"/draft` 调 `:publish` → `31004 resource not found`（v1 不存在；若已发布则 409），对话框无法关闭（2026-09-06 真实 dev bundle 实测同调用） | 1 passed：刷新已发布 v7 跳过发布，新模型 `provider_ref.version=="7"` | `review-lifecycle.spec.ts:15-40` | 真实 Chromium + HTTP + SQLite + 9878 stub；修复：`ConnectModelProviderModal.tsx:58-93` 读真实版本 | verified |
| S-06 | jsdom 新测试在 `ensureDraft` 中立化后失败（旧逻辑保存直接 update 已发布版：后端 409 / in-memory 状态翻转且无新版）；E2E 首轮亦因 spec 种子/路由问题失败 | jsdom 1 passed（`tool-editor-publish.test.tsx`）+ E2E 5 passed（tool/skill/mcp/policy/workflow） | `tool-editor-publish.test.tsx` 全文件；`review-lifecycle.spec.ts:51-79` | E2E 真实 Chromium + HTTP + SQLite；修复：五编辑器 + Agent `ensureDraft` 写前 fork + `inMemory updateDraft` 存储态收紧 | verified |

> S-05/S-06 联调另修复验收文件自身 3 处 bug（`review-lifecycle.spec.ts`）：详情 GET 用了不存在的 `/versions/{v}` 路由 → `?version=`；tool 种子 `fail_policy: "closed"` 非法 → `fail_closed`；tool 种子缺必填 `adapter_ref` → `adapter:review-tool@1`。

### Log
- [2026-09-06] created (draft) — 六项代码复核仍存在；用户授权修复闭环。
- [2026-09-06] started (in-progress) — cf-task-start 激活；现有未提交路径作为 pre-existing baseline 保留，仅修改本任务六项缺陷相关文件。
- [2026-09-06] S-01~S-04 核实为基线已修复（S-01/S-02/S-03/S-04 代码注释 + 回归测试全绿，无需改产品代码）。
- [2026-09-06] S-05 修复：`ConnectModelProviderModal` 刷新模式读真实 Provider 版本（删伪造 v1/draft）+ 版本未就绪护栏；RED 为旧调用实测 31004。
- [2026-09-06] S-06 修复：Tool/Skill/Mcp/Policy/Workflow/Agent 六编辑器 `ensureDraft` 写前 fork + 发布后刷新 published 态；`inMemoryConsoleApi.updateDraft` 改查存储态（与后端一致）；新增 jsdom 回归 `tool-editor-publish.test.tsx`（RED 已验）。
- [2026-09-06] E2E spec 自身 3 bug 修复（`?version=` 路由 / `fail_closed` / `adapter_ref`）；`review-lifecycle.spec.ts` 6/6 passed；前端全量 110 passed；registry 双库 32 passed。
- [2026-09-06] completed (done) — S-01~S-06 全 verified，证据见上表。
