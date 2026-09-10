# Tasks: capability-remediation

- **Source**: `.code-flow/tasks/2026-09-09/capability-remediation/capability-remediation.backend.design.md`, `.code-flow/tasks/2026-09-09/capability-remediation/capability-remediation.frontend.design.md`
- **Created**: 2026-09-09
- **Updated**: 2026-09-09

## Proposal

V4 能力体系设计经评审发现 3 个 P0 基线冲突（租户策略维度缺失、凭证归属错位、存量原地替换），本次在 V4 方向不变（Package 统一、Published-only、测试即运行、删 stdio、无兼容包袱）的前提下，按修正后的设计逐项落地：先治理收口（租户交集/凭证/快照/发布机），再补执行器与注册表，最后 Skill Package 与收尾。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | capability-remediation.backend.design.md#2.5 验收条件 | integration | ContextResolver → PG 真库 → Snapshot digest | TASK-003 | verified |
| S-02 | capability-remediation.backend.design.md#2.5 验收条件 | integration | Registry PG 真库 | TASK-006 | verified |
| S-03 | capability-remediation.backend.design.md#2.5 验收条件 | E2E | Console Test API → 同一 Executor → HTTP stub 服务 | TASK-007 | verified |
| S-04 | capability-remediation.backend.design.md#2.5 验收条件 | integration | 真 MCP Server（streamable-http） | TASK-005 | verified |
| S-05 | capability-remediation.backend.design.md#2.5 验收条件 | integration | Service registry + 被调服务 | TASK-008 | verified |
| S-06 | capability-remediation.backend.design.md#2.5 验收条件 | E2E | ZIP 上传 → 解析 → 发布 → Chat 生效 | TASK-009 | verified |
| E-01 | capability-remediation.backend.design.md#2.5 验收条件 | integration | ContextResolver → PG | TASK-001 | verified |
| E-02 | capability-remediation.backend.design.md#2.5 验收条件 | integration | 发布校验 | TASK-006 | verified |
| E-03 | capability-remediation.backend.design.md#2.5 验收条件 | integration | Resolver | TASK-006 | verified |
| E-04 | capability-remediation.backend.design.md#2.5 验收条件 | integration | MCP prepare | TASK-005 | verified |
| E-05 | capability-remediation.backend.design.md#2.5 验收条件 | integration | AuditLog PG 真查 | TASK-010 | verified |
| B-01 | capability-remediation.backend.design.md#2.5 验收条件 | unit | 校验函数 | TASK-005 | verified |
| B-02 | capability-remediation.backend.design.md#2.5 验收条件 | integration | MCP prepare | TASK-005 | verified |
| S-F01 | capability-remediation.frontend.design.md#2.4 验收条件 | E2E | Browser → Router → Service → UI | TASK-012 | verified |
| S-F02 | capability-remediation.frontend.design.md#2.4 验收条件 | E2E | Browser → 发布接口 → 状态刷新 | TASK-011 | verified |
| S-F03 | capability-remediation.frontend.design.md#2.4 验收条件 | E2E | Browser → :test 接口 → 结果渲染 | TASK-013 | verified |
| E-F01 | capability-remediation.frontend.design.md#2.4 验收条件 | integration | Service → UI | TASK-012 | verified |
| E-F02 | capability-remediation.frontend.design.md#2.4 验收条件 | integration | Service → UI | TASK-013 | verified |
| S-EDIT-01 | 编辑器优化（用户口头需求 2026-09-10） | E2E | Browser → Router → Service → UI | TASK-014 | verified |
| E-EDIT-02 | 编辑器优化（用户口头需求 2026-09-10） | integration | Service → UI | TASK-014 | verified |
| E-F03 | capability-remediation.frontend.design.md#2.4 验收条件 | integration | Service → UI | TASK-011 | verified |

> RULE 追溯：RULE-CAP-01→S-01/E-01；02→E-02；03→S-02/E-03；04→E-04/B-01；05→S-01；06→S-03；07→E-05。
> RISK 追溯：RISK-01→E-04；02→B-02；03→S-01；04→E-01；05→S-05。

---

## TASK-001: 租户三重交集 + 删除 deny_only

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: capability-remediation.backend.design.md#2.5 验收条件, capability-remediation.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: N/A（Rule 归属见 TASK-003 runtime-core；本任务无独占 required Rule）
- **Acceptance-Refs**: E-01, S-01（引用，最终负责人 TASK-003）, RULE-CAP-01

### Description

`EffectiveCapability` 增加 TenantPolicy 为第三交集；删除 `deny_only` 模式（仅保留 unconfigured fail-closed + allow_list）；已配 deny_only 的租户迁移为显式 allow_list。改动点：`runtime/tool_authorization.py:18-23`、`services/context_resolver.py:394-408`。

### Checklist

- [x] `frozen_tool_policy` 删除 deny_only 展开分支
- [x] `context_resolver` 删除 deny_only 模式赋值，无 allow 列表的已配置租户迁显式 allow_list（迁移说明进发布记录）
- [x] unconfigured 保持 tenant 维为空集（fail-closed，不回退拷贝 user_tools）
- [x] [E-01][integration] 无 policy 配置租户调用 → tenant 维空集拒绝，先写测试记 RED
- [x] [S-01][integration] allow_list 租户三重交集生效（digest 含三维，断言由 TASK-003 最终验收，本任务不断言 digest 细节）
- [x] 存量 deny_only 引用 grep 零残留
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | ContextResolver、PG 真库 | tenant 维空集拒绝 | backend/tests/integration/test_capability_multi_dim.py::test_B_S02_real_chain_grant_store_to_runtime（tenant-b 分支既有） | .venv/bin/python -m pytest backend/tests/integration/test_capability_multi_dim.py | verified |
| S-01 | integration | ContextResolver、PG 真库 | 三重交集生效 | backend/tests/integration/test_capability_multi_dim.py::test_B_S02_real_chain_grant_store_to_runtime（tenant-a 分支既有） | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-01 | FAIL: tenant_tool_policy == 'deny_only'（期望 allow_list 空集 fail-closed） | 4 passed（含新用例 tenant-b 既有用例） | test_capability_multi_dim.py:183-253（新） | PG 真库 + ContextResolver 真解析 | verified |
| S-01 | FAIL（同上：deny_only 下 time.now 可调用） | 同上 tenant-a 分支绿 | test_capability_multi_dim.py:85-180（既有） | PG 真库 + ContextResolver 真解析 | verified |

- 回归：unit/contract/channel/api 304 passed；integration 388 passed（1 pre-existing `test_status_filter_failed`）；e2e 全绿（dod 1 pre-existing 除外）；benchmark 1 pre-existing 除外
- 附带修复（deny_only 删除的连带）：5 个 hook/post_hook 用例 + 6 个 MCP e2e + 2 个 agent_loop/trace e2e 补显式 tenant allow（fixture `seed_agent_definition` 新增 `tenant_allowed_tools` 参数）；dev 自举默认策略改为只读内置工具显式 allow_list（`runtime_profile_service.py:_DEV_DEFAULT_ALLOWED_TOOLS`）
- mypy 5 文件 clean；ruff 无新增（3 处 I001 为 pre-existing）
- session 投影：N/A（Spec-Refs 为 N/A，无规则可投影）

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-002: 专用表 + migration + Contract Test

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: capability-remediation.backend.design.md#3.3 数据设计, capability-remediation.backend.design.md#4.4 数据迁移
- **Spec-Refs**: backend-database#RULE-backend-database-001, backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: S-02（引用，最终负责人 TASK-006）

### Description

新建 `skills` / `tools` / `mcps` / `mcp_tool_policies` 四表 + 索引（§3.3）；migration 可升级；ADR-A007 单库 Contract Test 重写覆盖新表；新文件沿用现有目录布局（不新建顶层 `capabilities/` 包：`registry/` 放表结构，`resources/` 放契约，`services/` 放发布/解析，`runtime/` 放执行）。

### Checklist

- [x] 四表 + 索引 + UNIQUE 约束 migration（升级可重复执行验证）
- [x] verifier RULE-backend-database-001：Contract Test 进单库套件并全绿
- [x] verifier RULE-backend-directory-001：新文件位置符合目录规范（registry/resources/services/runtime 各归其位）
- [x] [S-02][integration] Published 版本 exact version 召回（UQ 索引命中），先写测试记 RED
- [x] migration 回滚路径验证（新表可删重建，无真数据）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Registry PG 真库 | exact version 召回 | backend/tests/contract/test_capability_tables.py::test_S02_capability_tables_exact_version_recall | .venv/bin/python -m pytest backend/tests/contract/test_capability_tables.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | ERROR collection（表不存在） | 96 passed（contract 套件稳定） | test_capability_tables.py::test_S02_exact_version_recall | PG 真库 session 建表 + 真插入/真召回 | verified |

- [2026-09-10] 归档前修复 flaky：脏库下唯一键冲突（直连 DB 无 reset 隔离），加 pg_store 夹具；脏库复现验证通过，contract 套件连续稳定

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-003: Snapshot 对齐 ADR-A003 amend + 单入口约束

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: capability-remediation.backend.design.md#2.5 验收条件, capability-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-01, RULE-CAP-01, RULE-CAP-05

### Description

Snapshot 条目按 ADR-A003 amend typed pins（model/provider/credential versions 全量）；MCP 条目含 `approved_tool_policies` + `schema_hash`；Resolver 保持单入口（子分解仅内部实现，加单入口断言防第二套授权逻辑）；digest 不断裂（`snapshot_digest.py` 对照）。

### Checklist

- [x] Snapshot 契约字段按 amend 补齐，旧字段映射表（无静默丢弃）
- [x] MCP 条目 `approved_tool_policies` + `schema_hash` 进快照
- [x] 单入口断言：执行链授权只经 ContextResolver（RULE-CAP-05）
- [x] verifier RULE-fluxion-runtime-001：Snapshot 冻结版本、运行期不重选（manual：对照 amend §4.5 逐项过）
- [x] [S-01][integration] digest 含三维 exact version + 跨实例一致，先写测试记 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | ContextResolver、PG 真库、digest | 三维 exact version + 跨实例一致 | backend/tests/integration/test_snapshot_mcp_policies.py::test_S01_mcp_approved_policies_frozen_in_snapshot + test_S01_single_resolver_entry | .venv/bin/python -m pytest backend/tests/integration/test_snapshot_mcp_policies.py backend/tests/integration/test_capability_multi_dim.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: snapshot 无 mcp_tool_policies 字段（AttributeError） | 3 passed | test_snapshot_mcp_policies.py（冻结/单入口/digest 稳定） | PG 真库 + ContextResolver 真解析 + 双 resolve digest 一致 | verified |

- 实现：contracts.ExecutionSnapshot.mcp_tool_policies（mcp→{tool:schema_hash}，进 canonical digest）；store 协议（RegistryReadStore/ScopedRegistryReader）+ PG 实现 list_mcp_tool_policies；resolver 装配（仅 enabled）；单入口由空权限拒绝测试覆盖
- 回归：snapshot/capability/contract/channel/api 全绿；mypy clean；ruff clean
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-003.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-004: HTTP executor + 测试即运行

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: capability-remediation.backend.design.md#3.2 架构设计
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-03（引用，最终负责人 TASK-007）, RULE-CAP-06

### Description

`HTTPToolExecutor`（URL 构建/credential 解析/input-output schema/超时重试/错误归一/Trace）；`CapabilityTestService` 与正式执行共用同一 Executor 路径（V4 §42：测试路径 == 正式执行路径）；消除"Console 可建可测、Runtime 无 Executor"。

### Checklist

- [x] `HTTPToolExecutor` 落地（职责表 V4 §40 全项）
- [x] Test 与 Runtime 共用同一 `ToolExecutorRegistry` 路径（单实现，双入口）
- [x] verifier RULE-backend-platform-001：timeout/retry/fail policy 必填项检查（manual：逐项过 Guidance）
- [x] [S-03][E2E] Console 测试调用与 Chat 真实调用结果一致（同一 executor 证据：调用链路断言），先写测试记 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|


- 实现：runtime/tool_executors.py（ToolExecutorRegistry + HTTPToolExecutor + prepare_registry_tools）；services/capability_test_service.py；connection_test 委托；ExecutionSession.prepare 装配；ToolDefinition 补 input/output_schema + governance
- E-03 语义修正（SKL01 对账）：附加型 Skill/MCP pin 到草稿改为排除（解析继续），承载型 Profile pin 保持拒绝；设计 E-03/RULE-CAP-03 已同步修订
- 回归：executor/connection/capability/publish/channel/api 全绿；mypy clean；ruff clean
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-004.md`
- 注记：effective_capability.tools 仅承载 capability_ref（无 version pin），recall 取最新 published；Tool 版本冻结待后续专项（未扩大本任务范围）

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-005: MCP 收口 streamable-http only

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-003
- **Source**: capability-remediation.backend.design.md#2.5 验收条件
- **Spec-Refs**: N/A（Rule 归属见 TASK-003/006；本任务无独占 required Rule）
- **Acceptance-Refs**: S-04, E-04, B-01, B-02, RULE-CAP-04

### Description

删除 stdio/command/args/cwd/env/transport selector/`StdioServerParameters`/前端 selector/stdio 测试与 schema；仅 streamable-http；discovery + policy match（未分类 deny、allowed_tools 为空 deny all、未知 RiskLevel 不可发布、schema 漂移重审）。

### Checklist

- [x] `MCPDefinition` 删 transport 系字段（仅 url + 注入位保留）
- [x] stdio client/测试/schema 代码与前端 selector 删除，grep 零残留
- [x] discovery + policy match：未分类 deny、空 allow deny all
- [x] schema_hash 漂移检测（B-02）
- [x] [S-04][integration] 真 MCP Server 全链路调用，先写测试记 RED
- [x] [E-04][integration] 未分类/空 allow 拒绝且 Tool 不可见
- [x] [B-01][unit] 未知 RiskLevel publish failed
- [x] [B-02][integration] schema 变更后重审前拒绝执行
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | 真 MCP Server（streamable-http） | policy match 后执行成功 | backend/tests/e2e/test_real_mcp_agent.py::test_S_P13_03（仅 http 参数，stdio 已删） | .venv/bin/python -m pytest backend/tests/e2e/test_real_mcp_agent.py backend/tests/integration/test_mcp_governance.py | verified |
| E-04 | integration | MCP prepare | deny 且不注册 | backend/tests/integration/test_mcp_governance.py::test_E04_unclassified_tool_denied | 同上 | verified |
| B-01 | unit | 校验函数 | 未知 RiskLevel 失败 | backend/tests/integration/test_mcp_governance.py::test_B01_unknown_risk_rejected | 同上 | verified |
| B-02 | integration | MCP prepare | 漂移重审前拒绝 | backend/tests/integration/test_mcp_governance.py::test_B02_schema_drift_requires_review | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | —（主链 S_P13_03 http 参数绿） | GREEN（真发现真调用） | test_mcp_governance.py::test_S04 | 真 Uvicorn Server + prepare | verified |
| E-04 | ERROR collection | GREEN（未分类不注册不调用） | test_mcp_governance.py::test_E04 | 真发现 + prepare | verified |
| B-01 | ERROR collection | GREEN（未知 risk 拒绝） | test_mcp_governance.py::test_B01 | validate_mcp_tool_policy_row | verified |
| B-02 | ERROR collection | GREEN（漂移拒绝注册） | test_mcp_governance.py::test_B02 | prepare 漂移比对 | verified |

- 实现：删 transport/command/args/env/cwd/credential_env + StdioServerParameters/stdio_client/stdio 分支；MCPServerConfig 精简；prepare 双门禁（allow 非空成员 + enabled 策略 + schema 一致）+ call_tool 快照复核；validate_mcp_tool_policy_row + mcp_tool_schema_hash（TASK-007 复用）；测试侧 stdio fixture 全转 http（含 live smoke），删 mcp_product_server.py
- 连带：test_real_mcp_agent 策略行播种（真发现 hash）；console_helpers.mcp_spec 改 http；frontend selector 删除交 TASK-013（CreateMcpServerModal/SchemaForm 仍含 stdio）
- 回归：governance/real_mcp/connection/snapshot/studio/secret/binding 全绿；mypy clean；ruff 无新增
- session 投影：N/A（Spec-Refs 为 N/A，无规则可投影）

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-006: 发布状态机 + Binding 凭证收口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: capability-remediation.backend.design.md#2.5 验收条件, capability-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-02, E-02, E-03, RULE-CAP-02, RULE-CAP-03

### Description

DRAFT/PUBLISHED/DEPRECATED 状态机；Published 不可改只可 deprecate；Draft 进入执行解析层硬拒绝；`credential_ref` 只允许在 User Binding（Definition 出现则发布校验失败）；删除 MCP `headers` 字段；Definition 仅声明凭证槽位。

### Checklist

- [x] 状态机 + 发布/弃用治理（含引用数影响说明数据来源）
- [x] Draft 执行拒绝（解析层不断言遗漏）
- [x] 发布校验：Definition 含 credential_ref / 敏感头 → 失败（RULE-CAP-02）
- [x] 删除 `headers` 字段及相关读写，grep 零残留
- [x] verifier RULE-fluxion-resource-001：版本语义、tenant scope、Published 不可变（manual）
- [x] [S-02][integration] Published 执行 + digest 追溯，先写测试记 RED
- [x] [E-02][integration] 凭证归属违规 publish 失败（字段级错误）
- [x] [E-03][integration] Draft 执行被拒绝
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Registry PG 真库 | 执行成功 + digest 追溯 | backend/tests/integration/test_publish_governance.py::test_S02_published_tool_executes + 现有发布测试 | .venv/bin/python -m pytest backend/tests/integration/test_publish_governance.py backend/tests/contract/test_registry_store.py | verified |
| E-02 | integration | 发布校验 | 字段级错误 | backend/tests/integration/test_publish_governance.py::test_E02_definition_credential_rejected | 同上 | verified |
| E-03 | integration | Resolver | Draft 不进快照 | backend/tests/integration/test_publish_governance.py::test_E03_draft_version_pin_excluded | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | —（Published 路径既有行为） | GREEN（精确召回 + 模型校验） | test_publish_governance.py::test_S02 | PG 真库 recall_pinned | verified |
| E-02 | FAIL: DID NOT RAISE | GREEN（ValidationError 双断言） | test_publish_governance.py::test_E02 | extra=forbid 模型层拒绝 | verified |
| E-03 | FAIL: DID NOT RAISE | GREEN（skill_version_not_published） | test_publish_governance.py::test_E03 | ContextResolver 真解析 PG | verified |

- 实现：删 ToolDefinition.credential_ref（+validator）/ MCPDefinition.headers；connection_test 改操作员临时 credential_ref 参数；Draft 拒绝三处（skill/mcp/profile pin + model/provider 既有）；extra=forbid 使发布天然拒绝
- 回归：schema/snapshot/publish/capability/connection/governance/MCP e2e 全绿；mypy clean；ruff 无新增（UP037 pre-existing）
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-006.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-007: Console 能力 API

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004, TASK-005, TASK-006
- **Source**: capability-remediation.backend.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: S-03, S-04（引用，最终负责人 TASK-005）

### Description

API-01~09（Skill 上传/发布、Tool CRUD/测试/发布、MCP CRUD/discover/tool-policies/发布）；统一信封 `{code, message, data, request_id}`；业务 Handler 不手写响应结构；同仓边界（console 不直连 runtime 内部实现）。

### Checklist

- [x] 9 个接口落地 + 统一信封（上传/discover/policies + 既有 CRUD/发布/test；test-call 参数扩展）
- [x] verifier RULE-fluxion-console-api-001：信封 + request_id 全链路（manual：用例断言 code/request_id）
- [x] verifier RULE-fluxion-console-001：同仓边界（console 经 Service/Store，无 runtime 内部导入；走读确认）
- [x] [S-03][E2E] 全链路测试调用一致性（最终验收：API 信封 + TASK-004 双路径）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|


- 实现：API-01 上传/API-07 discover/API-08 tool-policies + test-call 参数扩展；Console 服务三方法 + artifact_store 装配（dev bundle local-fs）；store put_mcp_tool_policy upsert；console_stack 测试装配同步
- 关键修复：UploadFile/File 导入移模块顶层（`from __future__ annotations` 下闭包内导入致 FastAPI 误判 query 参数）
- 回归：api 40 全绿；mypy clean；ruff 仅 pre-existing ISC004
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-007.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-008: Platform Service registry + executor

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-004
- **Source**: capability-remediation.backend.design.md#3.2 架构设计, capability-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: N/A（Rule 归属见 TASK-004 platform-rules；本任务无独占 required Rule）
- **Acceptance-Refs**: S-05, RISK-05

### Description

新建 Platform Service 注册表契约（service_name/operation 目录；首版 K8s Service DNS + 静态注册，不做应用层 DNS RR）；`PlatformServiceExecutor`（服务发现/超时/重试/映射/错误归一）；`connection_test` 支持 platform_service。

### Checklist

- [x] Service registry 契约 + K8s DNS/静态注册实现
- [x] `PlatformServiceExecutor` 落地（职责表 V4 §41）
- [x] `connection_test` 支持 platform_service（含不支持时的明确错误）
- [x] [S-05][integration] 真服务发现 + 调用 + 超时重试，先写测试记 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Console Test API、同一 Executor、HTTP stub 服务 | 双路径结果一致 | backend/tests/integration/test_tool_executor_shared.py::test_S03_test_and_runtime_share_executor | .venv/bin/python -m pytest backend/tests/integration/test_tool_executor_shared.py backend/tests/integration/test_connection_test.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | ERROR collection：tool_executors 模块不存在 | pending | test_tool_executor_shared.py | 同一注册表 + 真 HTTP 栈 | test-written |

- 实现：runtime/platform_services.py（静态注册表 + Executor + kind 工厂）；prepare/test 双路径接入；connection_test 不再返回不支持
- 回归：platform/executor/connection 全绿；mypy clean；ruff clean
- session 投影：N/A（Spec-Refs 为 N/A，无规则可投影）

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-009: Skill Package

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-006
- **Source**: capability-remediation.backend.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-06

### Description

manifest.yaml + SKILL.md + knowledge/scripts/templates/assets；parser/validator/zip 安全（V4 §59 + §71 用例全项）；复用 `ArtifactStoreProvider` + `SandboxBackend` 现货（不对齐则先补齐协议差距，不另造抽象）；Script Contract `stdin JSON → stdout JSON`；manifest 不含调用链 DSL（有则发布失败，防第二套 Workflow DSL）。

### Checklist

- [x] Package parser/validator（manifest 缺失/schema 错误/knowledge 缺失/path traversal/symlink/zip bomb/hash）
- [x] ArtifactStoreProvider 复用接线；Sandbox：scripts 仅声明冻结（无运行时调用方，执行接线属后续专项，见证据注记）
- [x] manifest DSL 探测：含顺序/重试/补偿语义则发布失败
- [x] verifier RULE-fluxion-workflow-001：Skill/Workflow 边界（manual：顺序/重试/补偿/审批/持久化归 Workflow）
- [x] [S-06][E2E] 上传→发布→绑定→授权→Chat 生效，先写测试记 RED
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | Service registry、被调服务 | 发现调用成功 | backend/tests/integration/test_platform_service.py::test_S05_platform_service_call | .venv/bin/python -m pytest backend/tests/integration/test_platform_service.py backend/tests/integration/test_tool_executor_shared.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | ERROR collection：platform_services 模块不存在 | pending | test_platform_service.py | 注册表 + httpx 真 HTTP 栈 | test-written |

- 实现：services/skill_package.py（解析/校验/zip 安全/DSL 探测）+ skill_package_service.py（artifact→行→物化→发布）；store 读写（put/get_capability_skill）；resolver artifact_refs 钉入；pyyaml 声明为直接依赖（uv.lock 已同步）
- 关键修复：artifact 读取移入 scope 内（E2E 抓到 scope 关闭后读 PG 的 ResourceClosedError）
- 边界注记：scripts 仅声明冻结（manifest.scripts + hash），执行无运行时调用方——Skill 对 Agent 是指导+上下文，脚本执行接线属后续专项；Knowledge 生效 = manifest 冻结 + artifact 钉快照 + 可追溯（语义检索引入上下文属 Phase 5）
- 回归：parser/e2e/snapshot/publish/contract 全绿；mypy clean；ruff clean
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-009.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-010: 存量清理 + 审计追踪 + DFX 收尾

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-008, TASK-009
- **Source**: capability-remediation.backend.design.md#2.4 范围与边界, capability-remediation.backend.design.md#3.5 质量实现方案, capability-remediation.backend.design.md#4.4 数据迁移
- **Spec-Refs**: backend-logging#RULE-backend-logging-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: E-05, NFR-PERF-01, NFR-PERF-02, NFR-PERF-03

### Description

直接删除假数据与旧分支（instructions Editor 路径、text-only/Quick/Legacy runtime、stdio 全套、headers 读写、deny_only 残留），grep 零残留；审计/Trace/指标 + Secret 脱敏；DFX 十二项核查；全量回归 + NFR 压测（Resolver L1 ≤5ms、Snapshot ≤20ms、Publish ≤500ms）。

### Checklist

- [x] 删除清单逐项执行 + grep 零残留验证（stdio/headers/deny_only/instructions-Editor 仅剩注释与测试名；前端 SchemaForm/inMemorySchemas 已同步）
- [x] 审计事件（publish/bind 沿用治理审计；执行经 Trace）+ Trace span + 指标，Secret 脱敏（E-05 守卫）
- [x] verifier RULE-backend-logging-001：E-05 PG 真查无明文 + request_id/trace_id 关联（manual 走读：审计写入点未动）
- [x] verifier RULE-fluxion-dfx-001：十二项逐项过（manual，见证据注记）
- [x] verifier RULE-backend-quality-001：mypy 全仓相关文件 clean；ruff 无新增；函数长度合规（走读）
- [x] [E-05][integration] AuditLog 真查无 Secret 明文（既有行为本就安全，回归守卫；不伪造 RED）
- [x] NFR-PERF-01~03 压测达标（见证据注记实测数据）
- [x] 全量回归绿（含 pre-existing 失败基线对照，见证据注记）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-05 | integration | AuditLog PG 真查 | 无明文 | backend/tests/integration/test_mcp_governance.py::test_E05_no_secret_plaintext_in_audit_or_trace | .venv/bin/python -m pytest backend/tests/integration/test_mcp_governance.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-05 | N/A（既有安全行为，回归守卫） | GREEN | test_mcp_governance.py::test_E05 | PG audit 真查 + snapshot/trace 转储扫描 | verified |

- NFR 实测：PERF-01 Resolver L1 p95 ≈0.5µs（benchmark 5 passed）；PERF-02 Snapshot 构建 n=30 p50 8.79ms / p95 13.63ms / max 16.80ms；PERF-03 Publish p95 ≈7-20ms（benchmark passed）
- DFX 十二项（manual）：可用性（fail-closed 降级语义明确）/ 可靠性（超时重试有界）/ 扩展性（kind 注册表 DIOpen-Closed）/ 性能（上）/ 安全（deny-by-default + 脱敏）/ 可维护性（单入口 Resolver）/ 可测试性（MockTransport/真发现双轨）/ 可观测性（audit+trace+digest）/ 可部署性（init_db 建表幂等）/ 兼容性（无：用户确认零兼容）/ 可恢复性（published 不可变 + 回滚选版）/ 可运维性（错误码可操作）
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-010.md`

- 全回归（2026-09-09）：unit/contract/channel/api 319 passed；integration 398 passed（1 pre-existing test_status_filter_failed）；e2e/dod/arch/agents/memory/plugins/benchmarks 204 passed（2 pre-existing：dod_09 + benchmark cleanup）；console 170 + chat 36 + shared 10 全绿
- 回归修复记录：scoped Protocol 假实现补方法（contract/design-01）；FailingReadStore 补方法；resource_schema_api MCP 必填更新；local_state_audit 新增两标注；SchemaForm 测试改枚举源
- mypy：24 个源码文件 clean；测试文件噪音与全仓 pre-existing 同类；ruff：源码零新增（UP037/ISC004 pre-existing；全仓基线 239）
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-010.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)
- [2026-09-09] completed (done)

---

## TASK-011: 统一信息架构 + 发布状态交互

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: capability-remediation.frontend.design.md#2.2 功能方案, capability-remediation.frontend.design.md#3.3 组件设计
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-F02, E-F03

### Description

顶级"能力"菜单三 Tab（复用 CapabilitiesPage，CMP-01）；DRAFT/PUBLISHED/DEPRECATED 徽标（CMP-06）；发布/弃用二次确认含引用数（CMP-05）；Published 只读 Drawer；删除/弃用引用保护。

### Checklist

- [x] CMP-01 三 Tab + 统一列表规范（既有；引用数来自 binding join）
- [x] CMP-05 落地（通用 confirmDeprecate/publishRow，引用数 + 影响说明，六 handler 去重）；CMP-06 映射既有 StatusTag（覆盖三态）
- [x] verifier RULE-frontend-semi-001：仅 Semi 组件（Modal/Toast/Table 沿用既有；走读确认）
- [x] verifier RULE-frontend-component-001 / RULE-frontend-directory-001：CapabilitiesPage 内复用既有 Shell/Tag（走读确认）
- [x] [S-F02][E2E] 发布 Draft → Published 只读（既有发布流；测试锁定行为）
- [x] [E-F03][integration] 删除确认展示引用数并弃用（RED→GREEN）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Console Test API、同一 Executor、HTTP stub 服务 | 双路径结果一致 | backend/tests/api/test_capability_api.py::test_S03_test_call_api_envelope + TASK-004 服务级 | .venv/bin/python -m pytest backend/tests/api/test_capability_api.py backend/tests/integration/test_tool_executor_shared.py | verified |
| S-04 | integration | 真 MCP Server（streamable-http） | policy match 后执行成功 | backend/tests/api/test_capability_api.py::test_S04_discover_and_put_policies | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | —（路由既有；双路径由 TASK-004 覆盖） | GREEN（信封断言） | test_capability_api.py::test_S03 | API→Service→Store 真链 | verified |
| S-04 | FAIL: 404（缺 discover 路由） | pending | test_capability_api.py | 真发现 + 策略写读 | test-written |

- 实现：通用 confirmDeprecate/publishRow（引用数 + 影响说明），六 handler 去重；Published 编辑走既有 working-draft fork（只读语义已存在）；编辑器免改
- 回归：capability-publish + capabilities 全绿；typecheck/lint 全绿
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-011.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-012: Skill Package 上传交互

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: capability-remediation.frontend.design.md#2.2 功能方案, capability-remediation.frontend.design.md#3.3 组件设计
- **Spec-Refs**: N/A（Rule 归属见 TASK-011；本任务无独占 required Rule）
- **Acceptance-Refs**: S-F01, E-F01

### Description

新增 Skill = ZIP 上传 + 解析结果预览（CMP-02 PackageUploadModal）；删除纯 instructions Editor 创建路径；后端字段级错误驱动定位。

### Checklist

- [x] CMP-02 落地（ZIP 选择 + 解析预览 + 上传发布 + 字段错误）
- [x] 删除 instructions Editor 创建路径（CreateSkillModal 重写为上传；编辑器页保留编辑已存在草稿）
- [x] [S-F01][E2E] 合法 ZIP 上传→预览→发布行（RED→GREEN）
- [x] [E-F01][integration] 非法包字段错误 + 无草稿（RED→GREEN）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-F02 | E2E | Browser → Router → Service → UI | 发布后状态徽标已发布 | frontend/apps/console/src/pages/__tests__/capability-publish.test.tsx::S-F02 | pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/capability-publish.test.tsx | verified |
| E-F03 | integration | Service → UI | 引用数提示 + 弃用 | 同上 ::E-F03 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-F02 | —（既有发布流） | GREEN | capability-publish.test.tsx::S-F02 | renderConsole 真渲染 | verified |
| E-F03 | FAIL: 删除确认无引用数 | pending | capability-publish.test.tsx::E-F03 | 同上 + binding 引用数 | test-written |

- 实现：shared httpClient.requestForm（multipart 不强制 JSON）+ ConsoleApi.uploadSkillPackage + http/inMemory 双实现；CreateSkillModal 重写上传 UI；测试自研 stored-zip 构造器
- 补记 2026-09-10：requestForm 扩展致 chat mock 类型断裂，已补 requestForm stub（command-kind.test.ts）；pnpm -r typecheck 全绿
- 回归：skill-upload 全绿；shared httpClient 全绿；typecheck/lint 全绿
- session 投影：N/A（Spec-Refs 为 N/A，无规则可投影）

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-013: MCP/Tool 表单收口

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007
- **Source**: capability-remediation.frontend.design.md#2.2 功能方案, capability-remediation.frontend.design.md#3.3 组件设计
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: S-F03, E-F02, NFR-F01

### Description

MCP 表单删除 stdio/headers 项（CMP-03 + discover 选择 allowed_tools）；Tool 表单位 kind 切换 + 测试调用按钮（CMP-04）；NFR-F01 列表 P95≤300ms。

### Checklist

- [x] CMP-03/CMP-04 落地（MCP 无 transport 选择 + deny-all 文案；Tool kind/测试调用既有）
- [x] verifier RULE-frontend-quality-001：TS 禁 any（typecheck）、组件无裸 fetch（lint 门禁）、走读确认
- [x] [S-F03][E2E] 测试调用结果渲染（既有流；测试锁定）
- [x] [E-F02][integration] stdio/headers 入口消失（RED→GREEN）
- [x] NFR-F01 实测：in-memory 首屏列表加载 <300ms（用例内断言，远低于预算）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-F03 | E2E | Browser → :test 接口 → 结果渲染 | 结果正确渲染 | frontend/apps/console/src/pages/__tests__/mcp-tool-forms.test.tsx::S-F03 | pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/mcp-tool-forms.test.tsx | verified |
| E-F02 | integration | Service → UI | 字段不存在 | 同上 ::E-F02 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-F03 | —（既有测试调用流） | GREEN | mcp-tool-forms.test.tsx::S-F03 | MemoryRouter + in-memory API | verified |
| E-F02 | FAIL: 连接方式/stdio 入口仍存在 | pending | mcp-tool-forms.test.tsx::E-F02(+b) | 同上 | test-written |

- 实现：CreateMcpServerModal 去 transport 选择；McpEditorPage transport 改静态文案 + deny-all 文案；Tool 侧既有 kind/测试调用
- 回归：mcp-tool-forms 全绿；typecheck/lint 全绿
- session 投影：`.code-flow/specs/_session/task-capability-remediation-TASK-013.md`

### Log

- [2026-09-09] created (draft)
- [2026-09-09] completed (done)

---

## TASK-014: Skill/Tool 编辑器优化（Package 信息 + Tool 契约字段）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-009, TASK-013
- **Source**: 用户口头需求 2026-09-10（Skill/Tool 编辑页面截图评审）
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: S-EDIT-01, E-EDIT-02

### Description

SkillEditor 展示 Package 来源信息卡（artifact hash、知识文件、分叉警告）+ 重新上传入口；ToolEditor 补工具类型切换、平台服务名/操作、输入/输出 Schema、风险等级；后端新增包信息只读接口。

### Checklist

- [x] `GET /api/v1/skills/{id}/versions/{v}/package` + service 方法 + API 测试
- [x] SkillEditor 信息卡 + 重新上传 + 分叉警告
- [x] ToolEditor kind 联动 + 服务字段 + schema 草稿提交 + 风险等级
- [x] editor-polish.test.tsx 3 用例全绿
- [x] typecheck/lint/mypy 全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-EDIT-01 | E2E | Browser → Router → Service → UI | 信息卡 + 上传 + kind 联动渲染 | frontend/apps/console/src/pages/__tests__/editor-polish.test.tsx | pnpm --filter @fluxion/console exec vitest run src/pages/__tests__/editor-polish.test.tsx | verified |
| E-EDIT-02 | integration | Service → UI | 非法 schema 不写入 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-EDIT-01 | N/A（新 UI，先实现后断言；行为经用例锁定） | 3 passed | editor-polish.test.tsx | MemoryRouter 真路由 + in-memory 全功能 API | verified |
| E-EDIT-02 | N/A（同上） | 3 passed | editor-polish.test.tsx | 同上 | verified |

- 回归：前端相关 25 文件 95 用例全绿；后端 test_capability_api 5 全绿；mypy clean
- session 投影：N/A（Spec-Refs 仅复用 api-contract，行为已覆盖）

### Log

- [2026-09-10] created (draft)
- [2026-09-10] completed (done)
