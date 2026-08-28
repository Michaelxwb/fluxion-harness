# Tasks: Phase 1 Closure Gate — 产品架构闭环

- **Source**: `.code-flow/tasks/2026-08-28/phase1-closure/phase1-closure.design.md`（源自 `fluxion-phase1-closure-detailed-remediation.md` P1C-01~09 + DoD，历史文档已移除、git 历史可查；及 `docs/migration/当前代码偏差与迁移.md` P0-1/P0-2）
- **Created**: 2026-08-28
- **Updated**: 2026-08-28

## Proposal

闭合 Phase 1 的产品数据链：AgentDefinition 状态收口到 envelope 唯一 SoT、Tool 从 plugin 语义统一到 `ResourceKind.TOOL`、新增 Product Agent API（agent_id 主坐标）并把 runtime-profiles 路由降为 internal、补齐 UserProfile Attribute（provenance）、以 ChannelAuthenticator 收口 Channel 身份冒充（已登记 S2 残留）、前端完成 Studio round-trip / Typed CapabilityPicker / Chat 产品信息展示 / Agent 制 Chat Access / Console IA 修正。Closure DoD 全部验收前，Phase 2–5 不进入大规模实施。

依据核实结论（2026-08-28 代码验证）：P1C-01/02/03/04/06/07/08/09 全部实锤；P1C-05 第一层已修复（本计划覆盖第二层产品信息展示）；P1C-06 断点在 Console 前端选择器与签发校验缺失（后端模型已切 agent_id）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | phase1-closure.design.md#2.5 验收条件 | integration | 真实 Registry Store（双库） | TASK-001 | planned |
| S-02 | phase1-closure.design.md#2.5 验收条件 | integration | Capability parser + Registry | TASK-002 | planned |
| S-03 | phase1-closure.design.md#2.5 验收条件 | E2E | Browser → Console API → Registry | TASK-007 | planned |
| S-04 | phase1-closure.design.md#2.5 验收条件 | E2E | Product API → Runtime 链路 | TASK-003 | planned |
| S-05 | phase1-closure.design.md#2.5 验收条件 | E2E | Console 用户页 → 签发 → Chat resolve | TASK-010 | planned |
| S-06 | phase1-closure.design.md#2.5 验收条件 | E2E | Web channel per-message Bearer | TASK-005 | planned |
| S-07 | phase1-closure.design.md#2.5 验收条件 | integration | users 服务 + 真实 Store | TASK-004 | planned |
| S-08 | phase1-closure.design.md#2.5 验收条件 | E2E | Browser → Console Shell | TASK-011 | planned |
| S-09 | phase1-closure.design.md#2.5 验收条件 | integration | 全仓 TypeScript strict | TASK-012 | planned |
| S-10 | phase1-closure.design.md#2.5 验收条件 | E2E | Chat → 产品 API | TASK-009 | planned |
| E-01 | phase1-closure.design.md#2.5 验收条件 | integration | WeCom 签名 / Mattermost token 验证 | TASK-005 | planned |
| E-02 | phase1-closure.design.md#2.5 验收条件 | integration | Chat Access 签发校验 | TASK-006 | planned |
| B-01 | phase1-closure.design.md#2.5 验收条件 | unit | Capability parser 边界 | TASK-002 | planned |
| B-02 | phase1-closure.design.md#2.5 验收条件 | E2E | 普通用户面文案扫描 | TASK-012 | planned |
| S-11 | phase1-closure.design.md#2.5 验收条件 | integration | 真实 EffectiveCapabilityResolver + 双租户 Store（Gate G1 真值表） | TASK-013 | planned |
| E-03 | phase1-closure.design.md#2.5 验收条件 | integration | grant + 运行时三重交集 | TASK-013 | planned |

> NFR-C-01（Channel 验证 P95≤20ms）由 TASK-005 承载；NFR-C-02（legacy 读取零失败）由 S-01（TASK-001）承载。

---

## TASK-001: AgentDefinition SoT 收口

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.1 方案选型, phase1-closure.design.md#3.3 数据设计
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-01, RULE-C-01

### Description

删除 `AgentDefinition` 内 `lifecycle`/`visibility` 字段（P1C-01）：status/visibility 唯一事实源 = `ResourceDefinition` envelope。`model_validator(mode="before")` 剥离 legacy `lifecycle`/`visibility` 键（兼容读存量 spec_json，不批量重写）；repository 写入不再包含 legacy 键；`validate_lifecycle` 一并移除。一次性检查脚本报告存量含 legacy 键的 spec（只报告不改写）。API/服务/测试统一读 envelope。

### Checklist

- [ ] 删除 `agents/definitions.py` 的 `visibility`/`lifecycle` 字段与 `validate_lifecycle`；加剥离 validator；`agents/repository.py` 写入路径去 legacy 键
- [ ] 存量检查脚本：扫描 spec_json 含 legacy 键的行并输出报告
- [ ] [S-01][integration] 修改生产代码前，编写验收测试并记录 RED：旧实现允许 spec.lifecycle=DRAFT 与 envelope status=PUBLISHED 不一致（证明偏差存在）
- [ ] [S-01] GREEN 断言：创建（含 legacy 键）→ publish → GET，status/visibility 只来自 envelope；spec 读取剥离 legacy 键；序列化输出不含 lifecycle/visibility
- [ ] **Spec verifier**：`RULE-fluxion-resource-001` — 运行 `python -m pytest backend/tests/agents/ backend/tests/registry/ -k agent_definition`（planned）：断言 envelope 版本化生命周期不变（DRAFT→PUBLISHED→版本递增）、status/visibility 唯一来自 envelope、SQLite/PG 同契约
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实 Registry Store（SQLite+PG） | envelope SoT 唯一；legacy 键剥离读；序列化无 legacy 字段 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-002: Tool ResourceKind 统一

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.1 方案选型
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-02, B-01, RULE-C-02

### Description

`CapabilityType.TOOL → ResourceKind.TOOL`（`agents/capabilities.py:21` 现映射 PLUGIN）；parser 接受 `tool:<id>@<version>`；TOOL capability 遇 `plugin:` 前缀 fail-closed 拒绝（明确错误，不静默转换）；`plugin:` 仅保留 Provider/Extension 语义。存量检查脚本报告 `plugin:` tool 绑定（只报告不改写）。同步 AgentDefinition CapabilityBinding / Capability Resolver / Product API / tests（Workflow DSL V2 由 Phase 3 修订简报承接）。

### Checklist

- [ ] 修改 `CapabilityType.TOOL` 映射为 `ResourceKind.TOOL`；parser 支持 `tool:` 前缀
- [ ] TOOL capability + `plugin:` ref → fail-closed 明确错误；存量检查脚本输出报告
- [ ] [S-02][integration] 修改生产代码前，编写验收测试并记录 RED：`tool:customer-query@1.0.0` 现解析不到 ResourceKind.TOOL（证明偏差存在）
- [ ] [S-02] GREEN 断言：`tool:` → `ResourceKind.TOOL` 且可被 Agent 使用；`type=tool + plugin:ref` → 明确错误
- [ ] [B-01][unit] 覆盖坏格式 ref / 未知 type / `plugin:` 当 tool → 全部明确报错不静默
- [ ] **Spec verifier**：`RULE-backend-quality-001` — 运行 `python -m pytest backend/tests/agents/ -k capability`（planned）+ `ruff check`/`mypy` scoped：断言 parser/mapping 全类型注解、fail-closed 不静默、错误信息含 slug 与整码
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 parser + Registry | `tool:`→TOOL；`plugin:` 拒绝 | planned | planned | planned |
| B-01 | unit | parser 纯函数 | 三类非法输入全部明确报错 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-003: Product Agent API + Internal 边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.2 架构设计, phase1-closure.design.md#3.4 接口设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: S-04, RULE-C-03

### Description

新增 Product API：`POST /api/v1/agents/{agent_id}/runs(:stream)` 与 `GET /api/v1/agents/{agent_id}`（产品面 displayName/icon/description/能力）；agent→runtime_profile 解析在服务层，产品面不暴露 runtime_profile_id（P1C-08）。`/api/v1/runtime-profiles/{id}/runs(:stream)` 迁移为 `/internal/v1/runtime-profiles/{id}/runs(:stream)`（internal/testing 专用），调用方同步更新并全量回归（RISK-C-03）。Product API 走统一 envelope。

### Checklist

- [ ] 新增 `api/agents.py`（runs/:stream/product get）+ 服务层 agent→runtime_profile 解析 use case
- [ ] `api/runtime.py` 路由迁 `/internal/v1/` 前缀；internal 调用方更新 + 回归
- [ ] [S-04][E2E] 修改生产代码前，编写验收测试并记录 RED：`POST /api/v1/agents/{agent_id}/runs` 不存在（证明偏差存在）
- [ ] [S-04] GREEN 断言：Product API 执行成功（agent 坐标）；响应与产品 GET 无 runtime_profile_id；`/internal/v1/runtime-profiles/{id}/runs` 可用
- [ ] **Spec verifier**：`RULE-fluxion-runtime-001` — 运行 `python -m pytest backend/tests/api/ backend/tests/services/ -k "agent_run or runtime_api"`（planned）：断言 Product→服务层→Runtime 编排无状态（不落 durable）、mechanics 内聚 internal
- [ ] **Spec verifier**：`RULE-fluxion-console-api-001` — 运行 S-04 verifier 用例（planned）：断言统一 envelope `{code, message, data, request_id}`、Handler 无手写响应结构、错误码命名空间
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | Product API → 服务层 → Runtime → Registry | agent 坐标执行成功；产品面零 runtime_profile_id；internal 路由可用 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-004: UserProfile Attribute 模型

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.3 数据设计
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-07, RULE-C-06

### Description

`UserProfileSpec` 保留为 BasicProfile（display_name/bio/timezone/language）；新增 `ProfileAttribute`（key/value/source/source_ref/confidence/is_explicit/user_editable/visibility/valid_from/valid_until/superseded_by）与 `profile_attributes` 表（双库契约，索引 `(tenant_id, platform_user_id)`），支撑 learned profile 与 provenance（P1C-09）。users 服务提供 upsert/get/list/delete；learned 自动写入受 UserPreference 停学 gate 约束（与 Phase 2 memory 域 gate 对齐）。

### Checklist

- [ ] `ProfileAttribute` 模型 + `profile_attributes` 表（幂等 DDL）+ 索引；users 服务 CRUD
- [ ] learned 自动写入接 UserPreference 停学 gate
- [ ] [S-07][integration] 修改生产代码前，编写验收测试并记录 RED：写入 attribute（source=conversation, confidence=0.98）→ 当前无 provenance 承载（证明偏差存在）
- [ ] [S-07] GREEN 断言：查看/修改/删除生效；provenance 保留；停学后无自动写入
- [ ] **Spec verifier**：`RULE-backend-database-001` — 运行 `python -m pytest backend/tests/contract/ -k profile_attribute`（planned，SQLite + PG `local-pg-test-env` 各一套）：断言双库同契约、索引生效、无 N+1
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | integration | 真实 users 服务 + 双库 Store | CRUD 生效；provenance 保留；停学 gate 生效 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-005: ChannelAuthenticator + 冒充负测试

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.2 架构设计, phase1-closure.design.md#3.5 质量实现方案
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-06, E-01, RULE-C-04, NFR-C-01

### Description

新增 `services/channel_auth.py`：`ChannelAuthenticator.verify(request) -> VerifiedChannelIdentity`（channel_type/external_user_id/verification_method/verified_at/claims），fail-closed。三实现：Web=Bearer Chat Access Token（per-message 校验，收口 `api/channel.py` 已登记的 S2 残留「逐消息信任 channel_user_id」）、WeCom=签名验证、Mattermost=webhook/bot token。`/channels/web/messages` 改经 authenticator；`/bind` 前匿名例外保留（H1 语义）。验证失败 → 401/403 拒绝 + AuditLog（verification_method），token/签名不入日志。未验证身份不得映射 PlatformUser。NFR-C-01：单消息验证 P95≤20ms。

### Checklist

- [ ] 实现 `ChannelAuthenticator` 抽象 + Web/WeCom/Mattermost 三实现；`api/channel.py` messages 接线（移除逐消息信任）
- [ ] 验证失败 AuditLog + 脱敏（token/签名零日志）
- [ ] [S-06][E2E] 修改生产代码前，编写验收测试并记录 RED：伪造他人 channel_user_id 当前可冒充（证明 S2 残留存在）
- [ ] [S-06] GREEN 断言：有效 token 通过；伪造请求 401/403 拒绝、不映射 PlatformUser
- [ ] [E-01][integration] 覆盖 WeCom 签名错误 / Mattermost token 错误 → 拒绝 + AuditLog 记录 verification_method
- [ ] NFR-C-01 基准：验证 P95≤20ms
- [ ] **Spec verifier**：`RULE-fluxion-console-001` — 运行 S-06/E-01 verifier 套件（planned）：断言未绑定仅 `/bind`、未验证身份不入 PlatformUser 映射、Web Chat 正式 Channel 语义保持
- [ ] **Spec verifier**：`RULE-backend-directory-001` — 运行 `python -m pytest backend/tests/architecture/ -k channel_auth`（planned，AST 守护）：断言 `services/channel_auth.py` 落点、api 层无领域逻辑、测试目录同构
- [ ] **Spec verifier**：`RULE-backend-logging-001` — 运行 E-01 verifier 用例（planned）：断言 AuditLog 关联 request_id/trace_id/tenant_id、structlog JSON、token/签名脱敏零泄露
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实 ASGI 栈 + Bearer token + 绑定数据 | 有效通过；伪造 401/403；不映射 PlatformUser | planned | planned | planned |
| E-01 | integration | 真实签名/token 验证路径 | 拒绝 + AuditLog（verification_method） | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-006: Agent-based Chat Access 签发收口（后端）

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.4 接口设计
- **Acceptance-Refs**: E-02, RULE-C-05

### Description

Chat Access 签发收口（后端半边；前端 UI 见 TASK-010）：`issueChatAccess(platformUserId, agentId)` 校验 agent 存在且 `status=PUBLISHED`，否则明确错误码拒绝（E-02）；签发记录 `agent_id` 为唯一授权坐标（后端 `channel_app.py` 已切 agent_id，本任务补校验闭环，消除「runtime_profile 资源 ID 被当 agentId 签发」的错配）。

### Checklist

- [ ] Console 服务层签发入口校验 agent published；错误码明确
- [ ] [E-02][integration] 修改生产代码前，编写验收测试并记录 RED：当前签发不校验 agent 存在/发布态（证明偏差存在）
- [ ] [E-02] GREEN 断言：不存在/未发布 agent → 拒绝 + 明确错误码；已发布 agent → 签发成功且记录 agent_id
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-02 | integration | 真实 Registry + 签发服务 | 未发布/不存在拒绝；published 通过且 agent_id 正确 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-007: Agent Studio 完整 round-trip

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-008
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.1 方案选型
- **Acceptance-Refs**: S-03, RULE-C-07

### Description

`saveDraft()`（`AgentStudioPage.tsx:99`）构建完整 typed `AgentDefinitionSpec`：补写 `runtime_profile_ref`/`capabilities`（来自 Typed Picker）/`memory_policy_ref`/`personalization_policy_ref`（P1C-03）。E2E round-trip：选择 RuntimeProfile、添加 Skill/Tool/MCP（typed）、设置 Memory/Personalization Policy → Save → GET → 全字段一致；禁止只断言 toast（remediation §6.4）。

### Checklist

- [ ] `saveDraft()` 写入完整 spec（五段字段 + typed capabilities）
- [ ] [S-03][E2E] 修改生产代码前，编写验收测试并记录 RED：选择 runtime_profile/tool/mcp/memory_policy → 保存 → GET 字段丢失（证明 P1C-03 存在）
- [ ] [S-03] GREEN 断言：全字段 round-trip 一致（含 binding type/capability_ref/version_pin）
- [ ] 保存后 UI 回填（编辑已有草稿时字段回显）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Browser → Console API → Registry | 全字段 round-trip 一致；binding 三字段完整 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-008: Typed CapabilityPicker

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#2.3.2 字段约束
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-03（协作，最终负责人 TASK-007）

### Description

`CapabilityPicker` 从 `selected: string[]` 改为 typed `CapabilitySelection[] { type, capabilityRef, versionPin }`（P1C-04）：选择展示「名称 + 类型 + 版本」（如「客户查询 Tool v1.2.0」），Builder 不输入内部 ResourceKind；onChange 上抛 typed 数组；保存后 binding 三字段完整。

### Checklist

- [ ] Picker 输出 typed `CapabilitySelection[]`；展示名称+类型+版本
- [ ] [S-03 协作][E2E] 与 TASK-007 联调断言：保存后 binding 含 `type`/`capability_ref`/`version_pin`
- [ ] **Spec verifier**：`RULE-frontend-component-001` — 运行组件契约测试（planned）：断言 props 只读、事件上抛、`CapabilitySelection` 类型导出并被 Studio 消费
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03（协作） | integration | 真实组件实例 + typed props | 选择产物为 typed 三元组；无 string-only 路径 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-009: Chat Agent 产品信息展示

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: phase1-closure.design.md#2.3 功能方案
- **Acceptance-Refs**: S-10, RULE-C-03

### Description

Chat 头部不再展示 raw `access.agentId`（`App.tsx:146` 现状，P1C-05 第二层）：经 `GET /api/v1/agents/{agent_id}`（TASK-003 产品 API）解析 displayName/icon 并展示；解析失败降级占位「智能体」（不暴露 raw id）。

### Checklist

- [ ] chat services 增加产品信息解析（经产品 API，in-memory/http 同契约）
- [ ] [S-10][E2E] 修改生产代码前，编写验收测试并记录 RED：头部当前显示 raw agentId（证明 P1C-05 第二层存在）
- [ ] [S-10] GREEN 断言：显示 displayName/icon；不显示 raw agent_id；失败降级占位
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | Chat → 真实产品 API（in-memory 同契约） | displayName/icon 展示；零 raw agent_id | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-010: User Agent Access UI

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-006
- **Source**: phase1-closure.design.md#2.3 功能方案
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-05, RULE-C-05

### Description

Console 用户页（`UsersChannelsPage.tsx`）选择器从 `listResources("runtime_profile")` 切换为 Agent 列表（agent_definition，产品模型展示），消除「RuntimeProfile 资源 ID 被当 agentId 签发」的错配（P1C-06）；文案去掉「运行态」（L248）；签发结果展示 agent 产品信息。

### Checklist

- [ ] 选择器数据源切 `agent_definition` 列表（产品模型 label）；状态命名清理（`setRuntimeProfileId` → 语义命名）
- [ ] [S-05][E2E] 修改生产代码前，编写验收测试并记录 RED：选择器当前列出 runtime_profile 资源（证明 P1C-06 存在）
- [ ] [S-05] GREEN 断言：选择真实 Agent → 签发 → chat 侧 resolve 到正确 Agent + 产品信息
- [ ] **Spec verifier**：`RULE-frontend-semi-001` — 运行 UI 规则套件（planned）：断言页面全 Semi 组件、react19-adapter 首导入保持、无第二套组件库
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | Console UI → 签发 API → chat resolve | Agent 选择签发；resolve 正确；产品信息展示 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-011: Console IA 修正

- **Status**: draft
- **Priority**: P0
- **Depends**:
- **Source**: phase1-closure.design.md#2.3 功能方案
- **Spec-Refs**: frontend-directory-structure#RULE-frontend-directory-001
- **Acceptance-Refs**: S-08, RULE-C-08

### Description

Console 信息架构三项修正（remediation §15.3–15.5）：默认视图 Overview；Build 下 Agents 单一一级入口（「新建智能体」从一级菜单降为 Agents 页内 CTA）；Binding 从 Governance 一级导航下沉（Agent Detail / User 360 / Platform Advanced 入口）。

### Checklist

- [ ] 默认视图 Overview；Build IA 单一 Agents 入口 + 页内新建 CTA；Binding 下沉
- [ ] [S-08][E2E] 修改生产代码前，编写验收测试并记录 RED：当前默认视图/重复一级菜单/Binding 一级暴露（证明偏差存在）
- [ ] [S-08] GREEN 断言：三项全部成立；既有页面路由可达无回归
- [ ] **Spec verifier**：`RULE-frontend-directory-001` — 运行目录纪律扫描（planned）：断言调整后的页面/组件落点符合 `src/pages/`/`src/components/` 约定
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | E2E | 真实 Console Shell + Router | 默认 Overview；单一 Agents 入口；Binding 非一级 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-012: Closure 质量门禁（typecheck + 术语）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007, TASK-008, TASK-009, TASK-010, TASK-011
- **Source**: phase1-closure.design.md#2.5 验收条件
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001
- **Acceptance-Refs**: S-09, B-02, RULE-C-09

### Description

Closure DoD 质量门禁：`pnpm -r typecheck` strict 全绿（S-09）；普通用户面术语扫描 denylist=0（chat 全部页面 + console 产品面，B-02，复用既有 terminology 测试模式并扩展 console 产品面）；无裸 `fetch`/`any`/`@ts-ignore` 滥用抽查。

### Checklist

- [ ] typecheck strict 门禁跑通并记录结果；术语套件扩展 console 产品面
- [ ] [S-09][integration] 修改生产代码前，运行 typecheck 记录基线（如已有失败项逐项列出）
- [ ] [S-09] GREEN 断言：closure 修改面 typecheck 全绿
- [ ] [B-02][E2E] 修改生产代码前，编写验收测试并记录 RED：遍历普通用户面 → denylist 术语出现次数 = 0
- [ ] **Spec verifier**：`RULE-frontend-quality-001` — 运行 `pnpm -r typecheck` + 术语套件 + 质量扫描（planned）：断言 strict 全绿、denylist=0、无裸 fetch/any 滥用
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | integration | 全仓 TypeScript 编译 | strict 全绿 | planned | planned | planned |
| B-02 | E2E | 真实页面文案遍历 | denylist=0 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)

---

## TASK-013: Tool UserGrant 维度恢复 + Capability 命名收口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: phase1-closure.design.md#2.3 功能方案, phase1-closure.design.md#3.2 架构设计（v0.2 并入 `docs/migration/当前代码偏差与迁移.md` P0-1/P0-2）
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-11, E-03, RULE-C-10

### Description

恢复 Tool 的用户授权维度（migration P0-1 / ADR-A002 / ARCH-06）：移除 `services/runtime_tool_ops.py:174` 的 `user_tools = agent_tools` 折叠（其注释自引 ADR-012 的 user=agent 推导，该推导已被 ADR-A006 撤销），`_effective_tool_policy` 的 user 维度改由真实 User Tool Grant 解析；`UserDomainService.grant`（`users/service.py:184` 现拒绝 tool-capability）支持 Tool grant（或引入统一 UserCapabilityBinding Store）。命名收口（P0-2）：`AgentDefinition.capabilities: CapabilityBinding[]` 改名 `AgentCapabilityReference`/Allowlist 语义——它只表达 ref/version/type 上限，不承载用户 ownership。验收对齐 `docs/development/架构验收Gate.md` G1 真值表：同一 AgentDefinition 下不同用户实际 Tool list 与调用结果不同；UserGrant/AgentAllowlist/TenantPolicy 任一缺失即 deny（fail-closed）。Skill 扩展语义不受影响（ADR-003 修正案）。

### Checklist

- [ ] `_effective_tool_policy` 移除 `user_tools = agent_tools` 折叠，user 维度接真实 User Tool Grant；执行链三重交集 fail-closed 语义不变
- [ ] `UserDomainService.grant` 支持 Tool grant（或统一 UserCapabilityBinding Store）
- [ ] `CapabilityBinding` → `AgentCapabilityReference` 命名收口（模型/API/UI/fixture 同步，不留双模型）
- [ ] [S-11][integration] 修改生产代码前，编写验收测试并记录 RED：当前 user_tools=agent_tools 且 grant 拒绝 Tool——User-A/User-B 无法有不同 Tool 授权（证明 P0-1 存在）
- [ ] [S-11] GREEN 断言（Gate G1）：同一 AgentDefinition 下 User-A/User-B 实际 Tool list 与调用结果不同且正确；负向矩阵三行全拒
- [ ] [E-03][integration] GREEN 断言：grant Tool 成功；未授权 Tool 调用 fail-closed；Skill 扩展语义不回归
- [ ] **Spec verifier**：`RULE-fluxion-workflow-001` — 运行 `python -m pytest backend/tests/services/ backend/tests/runtime/ -k "tool_policy or effective_capability"`（planned）：断言 Tool 是 Agent-facing invocation contract（与 Plugin 实现载体分离）、三重交集 fail-closed、执行链无第二套授权拼装（REQ-CAP-006）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-11 | integration | 真实 EffectiveCapabilityResolver + 双租户 Store | G1 真值表全过；负向矩阵全拒；A/B 用户 Tool list 不同 | planned | planned | planned |
| E-03 | integration | 真实 grant 服务 + 运行时 tool policy | grant Tool 成功；未授权调用 fail-closed；Skill 扩展不回归 | planned | planned | planned |

### Acceptance Evidence

> `cf-task-start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

### Log
- [2026-08-28] created (draft)（v0.2 并入 migration P0-1/P0-2）
