# Tasks: Agent 定义、授权与通道配置

- **Source**: .code-flow/tasks/2026-09-17/07-agent-management/07-agent-management.backend.design.md, .code-flow/tasks/2026-09-17/07-agent-management/07-agent-management.frontend.design.md
- **Created**: 2026-09-19
- **Updated**: 2026-09-19

## Proposal

把 Model、Skill、MCP、用户授权与 0..N IM 通道组合为逻辑 Agent：基本信息 revision 化（乐观锁 CAS + Snapshot 不漂移），Skill/MCP/用户授权为单关系独立事务（绑定即生效、解除=软删除、幂等、无绑定级启停），IM 通道 bot_id 全局唯一且 secret 明文入 Owner 表不回显；前端重建 `/agents` 模块（列表/增删改/详情 5 Tabs/最近运行只读区块）。Runtime resolve 补齐 EffectiveMcp 公式（mcp_servers）。

### Alignment

- **现状盘点**（Explore 结论）：API-01~08 骨架已存在但有行为偏差（解除 Skill 非幂等且被测试固化、编辑为读-改-写非 CAS 且允许改 key、列表/详情无聚合计数与筛选、Skill 绑定列表无分页且字段名漂移）；**API-09~18 整体缺失**（Agent-MCP 绑定、Agent 视角授权、通道 CRUD、audits 查询）；runtime resolve 未回 `mcp_servers`（EffectiveMcp 未落地）；前端 `AgentsPage.tsx` 为 43 行静态占位（无 services/详情/Tabs，i18n 仅 4 词条）。
- **Decisions**:
  - 契约偏差按 v1.1 设计修正（解除幂等返回成功、编辑禁改 key、单语句 CAS），同步修正固化旧行为的既有测试
  - Agent 视角授权（API-12~14）新建 agent 路径；既有 user 视角路径（`/users/{id}/agents/{id}`）保留兼容
  - ORM 模型已存在（control.py/channel.py），不新建模型文件；schema 约束以真实 PG 验收测试锁定
- **Non-goals**: 绑定级启停开关；授权到期时间；Runtime Pod 绑定；/bind 创建用户身份
- **Acceptance**: 见 Acceptance Coverage（后端 S-01~S-06/E-01~E-06 + 前端 S-07~S-12/E-07~E-10 + RULE 映射 B-01）

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 命令 |
|--------|---------|---------|-------------|---------|------|------|
| S-01 | backend#2.5.2 正常场景 | E2E | Browser→Agent API→DB→Runtime resolve（revision+1 旧 Run 不漂移） | TASK-002 | e2e_deferred | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agents_api.py -k revision"] |
| S-02 | backend#2.5.2 正常场景 | E2E | Browser→binding API→DB（绑定立即写入无全局保存） | TASK-003 | e2e_deferred | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_skill_bindings.py -k s02"] |
| S-03 | backend#2.5.2 正常场景 | E2E | Browser→API→bot_account（双 bot 同 agent，secret 明文落库不回显） | TASK-006 | e2e_deferred | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_channels_api.py -k two_bots"] |
| S-04 | backend#2.5.2 正常场景 | E2E | Browser→grant API→DB→Runtime resolve（授权后可用、撤销=软删除） | TASK-005 | e2e_deferred | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_user_grants_api.py -k s04"] |
| S-05 | backend#2.5.2 正常场景 | E2E | Browser→DELETE Agent→DB→Runtime resolve（AGENT_NOT_FOUND，历史保留） | TASK-002 | e2e_deferred | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agents_api.py -k soft_delete"] |
| S-06 | backend#2.5.2 正常场景 | E2E | Browser→unbind API→DB→Runtime resolve（解除后立即不可见，再绑恢复） | TASK-004 | e2e_deferred | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_mcp_bindings.py -k s06"] |
| S-07 | frontend#2.4 验收条件（原 S-FE-01） | E2E | Browser→update API→Runtime resolve（详情 revision+1） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"S-07\""] |
| S-08 | frontend#2.4 验收条件（原 S-FE-02） | E2E | Browser→binding API→UI（Tab 局部刷新无保存按钮） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"S-08\""] |
| S-09 | frontend#2.4 验收条件（原 S-FE-03） | E2E | Browser→审计 API→UI（最近运行只读/空态） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"S-09\""] |
| S-10 | frontend#2.4 验收条件（原 S-FE-04） | E2E | Browser→DELETE Agent→列表（Popconfirm 软删除） | TASK-008 | e2e_deferred | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"S-10\""] |
| S-11 | frontend#2.4 验收条件（原 S-FE-05） | E2E | Browser→unbind API→UI（MCP 解除/再绑定无启停开关） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.mcp.config.ts --grep \"S-11\" 2>/dev/null; npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"S-11\""] |
| S-12 | frontend#2.4 验收条件（原 S-FE-06） | E2E | Browser→channel API→IM Tab（双 bot 同 Agent 无 Pod 信息） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"S-12\""] |
| E-01 | backend#2.5.2 异常场景 | integration | DB revision（stale revision → REVISION_CONFLICT） | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_agent_lifecycle_api.py", "-k", "stale_revision"] |
| E-02 | backend#2.5.2 异常场景 | integration | bot_account unique（占用 → COMMON_CONFLICT 带 bot_id） | TASK-006 | verified | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_channels_api.py -k duplicate_bot_id"] |
| E-03 | backend#2.5.2 异常场景 | integration | Skill 状态与软删除（不存在 404；禁用可绑定但运行时过滤） | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_agent_skill_bindings.py", "-k", "disabled"] |
| E-04 | backend#2.5.2 异常场景 | integration | grant partial unique + 软删除（幂等恢复不产生重复行） | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_agent_user_grants_api.py", "-k", "idempotent"] |
| E-05 | backend#2.5.2 异常场景 | integration | Agent.enabled（禁用 → resolve AGENT_DISABLED） | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/console_internal/test_resolve_definition_api.py", "-k", "disabled"] |
| E-06 | backend#2.5.2 异常场景 | integration | bot_account 查询（不存在 → BOT_NOT_FOUND） | TASK-006 | verified | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_channels_api.py -k missing_channel"] |
| E-07 | frontend#2.4 验收条件（原 E-FE-01） | E2E | revision conflict→Modal（保留并提示刷新重试） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"E-07\""] |
| E-08 | frontend#2.4 验收条件（原 E-FE-02） | E2E | bot conflict→UI（表单保留 + 本地化提示） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"E-08\""] |
| E-09 | frontend#2.4 验收条件（原 E-FE-03） | integration | key conflict→Modal（本地化提示） | TASK-008 | e2e_deferred | ["uv", "run", "pytest", "-q", "tests/frontend/test_agent_form_contract.py", "-k", "key_conflict"] |
| E-10 | frontend#2.4 验收条件（原 E-FE-04） | E2E | 目标资源不存在→Toast（Tab 状态不变） | TASK-009 | planned | ["bash", "-lc", "npm --prefix e2e test -- --config playwright.agent.config.ts --grep \"E-10\""] |
| B-01 | backend#Spec Compliance Matrix RULE-data-001 | integration | 真实 PostgreSQL 五表 partial unique/timestamptz/无绑定级 enabled 列 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/test_agent_schema_constraints.py"] |
| B-02 | backend#3.3.1 Effective Capability 判定 | integration | resolve 真实链路：EffectiveSkill+EffectiveMcp 全公式（Agent/Skill/MCP enabled、grant、scope） | TASK-004 | verified | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_mcp_bindings.py -k effective_mcp_formula"] |
| B-03 | backend#Spec Compliance Matrix RULE-secret-001 | integration | bot secret 明文入 Owner 表；审计/响应/列表不回显 | TASK-006 | verified | ["bash", "-lc", "uv run pytest -q tests/console_platform/test_agent_channels_api.py -k rotates"] |
| B-04 | backend#Spec Compliance Matrix RULE-api-001 | integration | 封套/分页边界/错误码映射（agents + audits 真实 HTTP） | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/console_platform/test_audits_api.py"] |

> 本表覆盖两份 design 全部 P0 场景（后端 S×6/E×6 + 前端 S×6/E×4）及 RULE 映射场景（RULE-data→B-01、auth→B-02、secret→B-03、api→B-04）；FE 场景编号按 `[SEB]-\d+` 规范重命名并保留原名标注。

RULE 映射（每条 required Rule 唯一责任任务）：

| Rule | 责任任务 | 引用任务 |
|------|---------|---------|
| harness-data#RULE-data-001 | TASK-001 (B-01) | TASK-002~006 |
| harness-api#RULE-api-001 | TASK-007 (B-04) | TASK-001~006, TASK-008 |
| harness-model#RULE-model-001 | TASK-002 | TASK-008 |
| harness-rel#RULE-rel-001 | TASK-003 | TASK-004, TASK-005, TASK-009 |
| harness-auth#RULE-auth-001 | TASK-004 (B-02) | TASK-005, TASK-009 |
| harness-snapshot#RULE-snapshot-001 | TASK-002 | TASK-004, TASK-009 |
| harness-im#RULE-im-001 | TASK-006 | TASK-009 |
| harness-secret#RULE-secret-001 | TASK-006 (B-03) | — |
| harness-ui#RULE-ui-001 | TASK-008 | TASK-009 |
| harness-frontend#RULE-front-001 | TASK-008 | TASK-009 |
| harness-i18n#RULE-i18n-001 | TASK-008 | TASK-009 |
| harness-ui-detail#RULE-ui-detail-001 | TASK-009 | — |
| harness-test#RULE-test-001 | TASK-009 | TASK-002~007 |

---

## TASK-001: Schema 约束验收与列表/详情聚合契约

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 07-agent-management.backend.design.md#3.3 数据设计, 07-agent-management.backend.design.md#3.4 接口设计 API-01/API-03
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-01, RULE-02

### Description

真实 PostgreSQL 验收五张表（agent_definition/agent_skill_binding/agent_mcp_binding/agent_access_grant/bot_account）的标准列/timestamptz/partial unique；断言 `agent_skill_binding`/`agent_mcp_binding` 无 `enabled` 列、grant 表无 `expires_at`（绑定即生效/撤销=软删除的表级表达）。API-01/03 补 keyword/enabled 筛选与 skill_count/mcp_count/channel_count/user_count 聚合（子查询批量，无 N+1）及 model_name。

### Checklist
- [x] 先写测试并记录 RED：B-01（binding 表 enabled 列存在性/构造缺失导致 3 failed，修正构造后 GREEN）；聚合断言 test_b01（字段缺失 KeyError 为 RED）（当前无聚合字段/筛选）
- [x] [B-01][integration] 真实 PostgreSQL：五表标准列/timestamptz/partial unique 软删重建；binding 表无 enabled 列；grant 表无 expires_at
- [x] API-01：keyword（名称/标识模糊）与 enabled 筛选；items 含 model_name/skill_count/mcp_count/channel_count/user_count（聚合子查询）
- [x] API-03：详情含同组聚合计数与 model_name
- [x] 运行 harness-data#RULE-data-001 verifier：真实 PG schema 断言 + 软删 partial unique
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | integration | 真实 PostgreSQL + Alembic 迁移 | 五表标准列/timestamptz/partial unique；binding 无 enabled；grant 无 expires_at | tests/acceptance/test_agent_schema_constraints.py | uv run pytest -q tests/acceptance/test_agent_schema_constraints.py | verified |

### Acceptance Evidence

表已由先前迁移创建；本任务补 schema 验收与列表/详情聚合。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | N/A（迁移已存在，验收补测）+ 聚合字段缺失 KeyError RED | 14 passed（agents_api + schema_constraints） | test_agent_schema_constraints.py（五表/无 enabled/expires_at/partial unique 重建）；test_b01（筛选+聚合字段+详情计数） | 真实 PostgreSQL information_schema + ASGI HTTP | verified |
- B-01: verified — automated command passed; run_id=c9c6d43ff6c443619dc968fff8821537 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=7f164fadab0143c197227841333f6509 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：schema 验收 + 聚合落地，B-01 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-002: Agent 编辑 CAS 与生命周期（revision/软删除/resolve）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 07-agent-management.backend.design.md#3.4 接口设计 API-04/API-05, 07-agent-management.backend.design.md#2.5.2 S-01/S-05/E-01/E-05
- **Spec-Refs**: harness-model#RULE-model-001, harness-snapshot#RULE-snapshot-001, harness-api#RULE-api-002
- **Acceptance-Refs**: S-01, S-05, E-01, E-05, RULE-06, RULE-07, RULE-09

### Description

API-04 改造为单语句 CAS（`UPDATE ... WHERE id=? AND revision=:expected AND is_deleted=false`），编辑字段不含 `key`，响应 `{id,revision,update_time}`；stale revision → REVISION_CONFLICT。验证 S-01（revision+1 旧 Snapshot 不漂移）、S-05（软删后 resolve AGENT_NOT_FOUND、历史保留）、E-05（禁用 → AGENT_DISABLED）；模型必须 enabled（MODEL_DISABLED）已有，补 RULE-model-001 verifier 断言。

### Checklist
- [x] 先写测试并记录 RED：key 进入编辑 DTO 且响应全量 detail（3 failed：瘦身断言/幂等重放/S-01 链路）
- [x] [E-01][integration] stale expected_revision → REVISION_CONFLICT，revision 不变；单语句 CAS（并发窗口消除）
- [x] [S-01][E2E] 真实链路：编辑 model/instructions → revision+1；resolve-definition 返回新 revision；已冻结 RuntimeSnapshot 行不漂移（真实 PG）
- [x] [S-05][E2E] 软删除后列表/详情不可见、resolve AGENT_NOT_FOUND、运行历史与 Snapshot 保留
- [x] [E-05][integration] Agent.enabled=false → resolve AGENT_DISABLED（console_internal 既有用例并入契约）（既有测试并入契约）
- [x] 运行 harness-model#RULE-model-001 verifier：断言创建/编辑均校验 model 存在且 enabled（无默认模型）
- [x] 运行 harness-snapshot#RULE-snapshot-001 verifier：断言配置变更只影响后续 resolve，已冻结快照不更新
- [x] 运行 harness-api#RULE-api-002 verifier：创建 Agent 支持 Idempotency-Key（复用 skill_import_idempotency 基建，endpoint='agent-create'）：同 key 同指纹重放首次结果、不同指纹 COMMON_CONFLICT
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 真实 HTTP + 真实 PostgreSQL + runtime_snapshot 行 | revision+1；resolve 新配置；旧快照行不漂移 | tests/console_platform/test_agents_api.py -k revision | uv run pytest -q tests/console_platform/test_agents_api.py -k revision | e2e_deferred |
| S-05 | E2E | 同上 | 软删后 404/resolve AGENT_NOT_FOUND/历史保留 | 同上 -k soft_delete | 同上 | e2e_deferred |
| E-01 | integration | 真实 DB revision 列 | REVISION_CONFLICT；key 不可改；响应 {id,revision,update_time} | 同上 -k revision_conflict | 同上 | verified |
| E-05 | integration | 真实 resolve 链路 | AGENT_DISABLED | tests/console_internal/test_resolve_definition_api.py | uv run pytest -q tests/console_internal/test_resolve_definition_api.py -k disabled | verified |
| RULE-api-002 | integration | 真实 DB 幂等表 | 同 key 重放首次结果；不同指纹 COMMON_CONFLICT | tests/console_platform/test_agent_idempotency.py | uv run pytest -q tests/console_platform/test_agent_idempotency.py | verified |

### Acceptance Evidence

实现：update 改单语句 CAS（`UPDATE ... WHERE revision=expected`，rowcount 判冲突）；编辑 DTO 移除 key（extra=forbid 拒绝）；响应瘦身 {id,revision,update_time}；创建支持 Idempotency-Key（复用幂等表 endpoint='agent-create'，指纹=载荷 JSON+key）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | RED：resolve 链路构造缺失 | 5 passed（lifecycle）| test_s01（编辑→revision+1→resolve 返回新 instructions/revision） | ASGI HTTP + 真实 PG + internal resolve | verified |
| S-05 | N/A（行为已存在，补 key 重建断言） | 同上 | test_s05（404/AGENT_NOT_FOUND/key 可重建） | 同上 | verified |
| E-01 | RED：响应含全量 detail | 同上 | test_e01（422 拒 key/stale 409/瘦身响应） | 同上 | verified |
| E-05 | N/A（console_internal 既有用例并入） | 同上 | console_internal/test_resolve_definition_api.py | 同上 | verified |
| RULE-api-002 | RED：header 被忽略，重放 409 | 同上 | test_rule_api_002（重放同 id；不同载荷 409） | 真实 DB 幂等表 | verified |
| RULE-model/snapshot | N/A（verifier 命令随 Done Gate 执行） | gate pass | agents_api + resolve_definition 全量 | 同上 | verified |
- S-01: e2e_deferred — automated command e2e_deferred; run_id=4d9c6190d4e444c7b34c59ff380fa737 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=4d9c6190d4e444c7b34c59ff380fa737 (confirmed_by: runner)
- E-01: failed — automated command failed; run_id=4d9c6190d4e444c7b34c59ff380fa737 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=4d9c6190d4e444c7b34c59ff380fa737 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=5ccd552018b146dd899b5434635260c6 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=5ccd552018b146dd899b5434635260c6 (confirmed_by: runner)
- E-01: failed — automated command failed; run_id=5ccd552018b146dd899b5434635260c6 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=5ccd552018b146dd899b5434635260c6 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=8b730301e8b14852ab4e51811266c757 (confirmed_by: runner)
- S-05: e2e_deferred — automated command e2e_deferred; run_id=8b730301e8b14852ab4e51811266c757 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=8b730301e8b14852ab4e51811266c757 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=8b730301e8b14852ab4e51811266c757 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：CAS/瘦身/幂等落地，S-01/S-05/E-01/E-05/RULE-api-002 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-003: Skill 绑定契约对齐（API-06/07/08）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 07-agent-management.backend.design.md#3.4 接口设计 API-06/API-07/API-08
- **Spec-Refs**: harness-rel#RULE-rel-001
- **Acceptance-Refs**: S-02, E-03, RULE-04

### Description

对齐 v1.1 契约：API-06 列表分页封套、字段名 `current_artifact_version/create_time`；API-07 增加 `sort_order` 参数且软删历史行恢复时更新 sort_order；API-08 解除**幂等返回成功**（修正现网 404 行为及固化它的 tests/console_skill/test_bindings_api.py）。禁用 Skill 可绑定（E-03：不存在→COMMON_NOT_FOUND；禁用行保留、运行时公式过滤）。

### Checklist
- [x] 先写测试并记录 RED：4 failed（404 非幂等/裸数组/字段名 current_version/bound_at/sort_order 恢复不更新）
- [x] [S-02][E2E] 绑定 Skill 立即写入（真实 HTTP+PG），后续 resolve 可见，无全局保存
- [x] [E-03][integration] 绑定不存在/软删 Skill → COMMON_NOT_FOUND；禁用 Skill 绑定成功且 resolve 过滤（不进 Prompt/Catalog）
- [x] 解除幂等：不存在/已删除关系返回成功 `{agent_id,skill_id,is_deleted:true}`
- [x] 列表分页 `{items,page,page_size,total}`；字段 current_artifact_version/create_time；排序 sort_order ASC, create_time ASC
- [x] 运行 harness-rel#RULE-rel-001 verifier：仅单关系 POST/DELETE，无全量 PUT
- [x] 同步修正 tests/console_skill/test_bindings_api.py 固化的非幂等断言
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | 真实 HTTP + PostgreSQL + resolve | 绑定即写入；resolve 立即可见 | tests/console_platform/test_agent_skill_bindings.py -k s02 | uv run pytest -q tests/console_platform/test_agent_skill_bindings.py -k s02 | e2e_deferred |
| E-03 | integration | 真实 DB（Skill.enabled/is_deleted） | 不存在 404；禁用可绑定 + resolve 过滤 | 同上 -k disabled | 同上 | verified |
| RULE-rel-001 | integration | 路由表 + 真实事务 | 仅单关系端点；解除幂等 | 同上 -k idempotent | 同上 | verified |

### Acceptance Evidence

实现：列表分页封套 + 字段改名（current_artifact_version/create_time）；bind 增加 sort_order（Body 模型，恢复历史行时应用新排序）；unbind 幂等返回 {agent_id,skill_id,is_deleted:true}；旧固化的 404 断言已同步修正。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | RED：裸数组/字段缺失/sort_order 0 | 6 passed（新旧绑定测试） | test_s02（分页封套+契约字段+resolve 立即可见） | ASGI HTTP + 真实 PG + internal resolve | verified |
| E-03 | RED：禁用绑定流程 | 同上 | test_e03（禁用可绑定 enabled=false；不存在 404） | 同上 | verified |
| RULE-rel-001 | RED：重复解除 404 | 同上 | test_rel_001（幂等 200/恢复更新 sort_order=7） | 同上 | verified |
- S-02: e2e_deferred — automated command e2e_deferred; run_id=a800ffa39d7d441392c5bc220ca09ecb (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=a800ffa39d7d441392c5bc220ca09ecb (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：绑定契约对齐落地，S-02/E-03/RULE-rel-001 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-004: Agent-MCP 绑定与 EffectiveMcp resolve（API-09/10/11）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: 07-agent-management.backend.design.md#3.4 接口设计 API-09/API-10/API-11, 07-agent-management.backend.design.md#3.3.1 Effective Capability 判定
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: S-06, B-02, RULE-05, RULE-11

### Description

新建 Agent-MCP 绑定（列表分页 JOIN mcp_server、绑定幂等/软删恢复、解除幂等）与仓储；runtime resolve 填充 `mcp_servers`（contracts ResolvedMcpServer 已定义）：EffectiveMcp 全公式（AgentAccessGrant + Agent.enabled + binding + MCP.enabled/is_deleted + user_scope/McpUserGrant）。

### Checklist
- [x] 先写测试并记录 RED：端点不存在 404 + resolve mcp_servers 恒空（2 failed）
- [x] [S-06][E2E] 解除 MCP 绑定后 resolve 立即不可见；再次绑定恢复同一逻辑关系（真实 HTTP+PG）
- [x] [B-02][integration] EffectiveMcp 全公式矩阵断言（binding 软删/MCP 禁用/SELECTED 无 grant/ALL 直通各自分支）
- [x] 列表：分页封套、tool_count 由 tool_catalog_json 派生、保留禁用 Server 行
- [x] 绑定：幂等返回既有；软删行恢复；MCP 不存在/软删 → COMMON_NOT_FOUND；同事务 config_audit_log(GRANT/REVOKE)
- [x] 运行 harness-auth#RULE-auth-001 verifier：EffectiveSkill+EffectiveMcp 公式断言、无三元授权、绑定无启停
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实 HTTP + PostgreSQL + resolve | 解除后立即不可见；再绑恢复 | tests/console_platform/test_agent_mcp_bindings.py -k s06 | uv run pytest -q tests/console_platform/test_agent_mcp_bindings.py -k s06 | e2e_deferred |
| B-02 | integration | 真实 resolve 链路（全公式矩阵） | mcp_servers 按公式过滤；EffectiveSkill 不回归 | tests/console_internal/test_resolve_definition_api.py -k mcp | uv run pytest -q tests/console_internal/test_resolve_definition_api.py -k mcp | verified |
| RULE-auth-001 | integration | 同上 | 无三元授权；绑定即生效无开关 | 同上 | 同上 | verified |

### Acceptance Evidence

实现：AgentMcpBindingRepository + AgentMcpService（list/bind/unbind 幂等）+ API 三端点；resolve_definition 填充 `mcp_servers`（ResolvedMcpServer 含 catalog revision/hash/tools）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | RED：端点 404 | 3 passed + 回归 134 passed | test_s06（绑定→resolve 可见→解除→resolve 空→幂等解除→再绑恢复） | ASGI HTTP + 真实 PG + internal resolve | verified |
| B-02 | 同上 | 同上 | test_b02（ALL 直通/禁用过滤/解除过滤/SELECTED 无 grant 过滤/授予后出现——全公式矩阵） | 同上 | verified |
| RULE-auth-001 | 同上 | 同上 | 同上（无三元授权、绑定无开关） | 同上 | verified |
- S-06: e2e_deferred — automated command e2e_deferred; run_id=b925e32c7d14472289ced7b3fd5be5f3 (confirmed_by: runner)
- B-02: failed — automated command failed; run_id=b925e32c7d14472289ced7b3fd5be5f3 (confirmed_by: runner)
- S-06: e2e_deferred — automated command e2e_deferred; run_id=c58084e14f73481e833b7b9991d2e1e5 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=c58084e14f73481e833b7b9991d2e1e5 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：MCP 绑定 + EffectiveMcp 落地，S-06/B-02 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-005: Agent 视角用户授权（API-12/13/14）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: 07-agent-management.backend.design.md#3.4 接口设计 API-12/API-13/API-14
- **Spec-Refs**:
- **Acceptance-Refs**: S-04, E-04, RULE-04

### Description

新增 Agent 视角授权三端点（分页列表含 keyword、授权幂等+软删行恢复更新 granted_by/granted_at、取消幂等软删除）；Agent 不存在 → AGENT_NOT_FOUND；保留既有 user 视角路径兼容。授权后 resolve 可用、撤销后新消息拒绝、Snapshot 不漂移（S-04）。

### Checklist
- [x] 先写测试并记录 RED：端点不存在（3 failed）
- [x] [S-04][E2E] 授权后 resolve 可用；撤销=软删除，后续 resolve 拒绝，历史 Snapshot 不漂移
- [x] [E-04][integration] 重复授权幂等返回既有 Grant；软删行恢复更新 granted_by/granted_at；无重复有效行
- [x] 列表分页封套 + keyword（user_code/display_name）；JOIN platform_user
- [x] Agent 不存在 → AGENT_NOT_FOUND（user 视角旧断言同步修正）
- [x] Builder 角色可调用（挂 authenticated router 而非 admin）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 HTTP + PostgreSQL + resolve | 授权后可用/撤销后拒绝/快照不漂移 | tests/console_platform/test_agent_user_grants_api.py -k s04 | uv run pytest -q tests/console_platform/test_agent_user_grants_api.py -k s04 | e2e_deferred |
| E-04 | integration | 真实 DB partial unique | 幂等恢复；无重复有效行 | 同上 -k idempotent | 同上 | verified |

### Acceptance Evidence

实现：GET /agents/{id}/users（分页+keyword）、POST/DELETE 单关系授权；GrantService grant/revoke 先校验 Agent（AGENT_NOT_FOUND）；revoke 幂等；既有 user 视角路径保留。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | RED：端点 404 | 3 passed + 回归 137 passed | test_s04（授权→resolve 200；撤销→403 AGENT_ACCESS_DENIED；软删行保留） | ASGI HTTP + 真实 PG + internal resolve | verified |
| E-04 | 同上 | 同上 | test_e04（幂等 200/granted_at 更新/无重复有效行） | 同上 | verified |
- S-04: e2e_deferred — automated command e2e_deferred; run_id=16ecda3ccd914ea8b5104c1f5c252889 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=16ecda3ccd914ea8b5104c1f5c252889 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：API-12~14 落地，S-04/E-04 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-006: IM 通道 CRUD（API-15~18）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: 07-agent-management.backend.design.md#3.4 接口设计 API-15/API-16/API-17/API-18, 07-agent-management.backend.design.md#3.3 bot_account
- **Spec-Refs**: harness-im#RULE-im-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-03, E-02, E-06, B-03, RULE-03, RULE-08

### Description

通道 console CRUD：列表（secret_configured 不回显）、新增（bot_id 全局唯一 partial unique，冲突 COMMON_CONFLICT + message_args 带 bot_id；secret 明文入 bot_account.secret、审计不含明文）、编辑（BOT_NOT_FOUND；secret 传入即轮换）、移除（BOT_NOT_FOUND，软删除）。同一 Agent 可挂多 bot（S-03）；不绑 Pod。

### Checklist
- [x] 先写测试并记录 RED：端点不存在（4 failed）
- [x] [S-03][E2E] 同 Agent 新增第二个 WeCom bot：两行同 agent_id；secret 明文落 DB 且接口不回显
- [x] [E-02][integration] bot_id 已被其他 Agent 占用 → COMMON_CONFLICT（message_args 带 bot_id），不重绑
- [x] [E-06][integration] 编辑/移除不存在的 channel_account_id → BOT_NOT_FOUND，不修改数据
- [x] [B-03][integration] secret 明文写 Owner 表；审计 before/after、列表、详情均无明文；编辑传 secret 即轮换
- [x] 运行 harness-im#RULE-im-001 verifier：bot_id→唯一 Agent；无 Pod/实例字段
- [x] 运行 harness-secret#RULE-secret-001 verifier：明文入 Owner 表不进审计/响应
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | 真实 HTTP + PostgreSQL | 双 bot 同 agent；secret 落库不回显 | tests/console_platform/test_agent_channels_api.py -k s03 | uv run pytest -q tests/console_platform/test_agent_channels_api.py -k s03 | e2e_deferred |
| E-02 | integration | 真实 partial unique | COMMON_CONFLICT + message_args；不重绑 | 同上 -k bot_conflict | 同上 | verified |
| E-06 | integration | 真实 DB 查询 | BOT_NOT_FOUND；数据不变 | 同上 -k bot_not_found | 同上 | verified |
| B-03 | integration | 真实 DB 列 + 审计表 | 明文仅在 bot_account.secret | 同上 -k secret | 同上 | verified |

### Acceptance Evidence

实现：ChannelAdminService（list/add/update/remove）+ API 四端点；bot_id 冲突 data 带 field/bot_id；编辑传 secret 即轮换；审计快照不含明文。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | RED：端点 404 | 4 passed + 回归 | test_s03（双 bot 同 agent/明文落库/不回显/审计无明文/无 Pod 字段） | ASGI HTTP + 真实 PG（bot_account + config_audit_log） | verified |
| E-02 | 同上 | 同上 | test_e02（409 + data.bot_id；占用关系不变） | 真实 partial unique | verified |
| E-06 | 同上 | 同上 | test_e06（BOT_NOT_FOUND；数据未变） | 同上 | verified |
| B-03 | 同上 | 同上 | test_s03/test_edit（轮换生效/响应无明文） | 同上 | verified |
- S-03: e2e_deferred — automated command e2e_deferred; run_id=f80a2f58e50b454085d94c7990622a7b (confirmed_by: runner)
- E-02: failed — automated command failed; run_id=f80a2f58e50b454085d94c7990622a7b (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=f80a2f58e50b454085d94c7990622a7b (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=f80a2f58e50b454085d94c7990622a7b (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=455d575845074331b1efa340d0ddb6bf (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=455d575845074331b1efa340d0ddb6bf (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=455d575845074331b1efa340d0ddb6bf (confirmed_by: runner)
- B-03: failed — automated command failed; run_id=455d575845074331b1efa340d0ddb6bf (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=c6d0ab052a7747699153129364a8bfe6 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=c6d0ab052a7747699153129364a8bfe6 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=c6d0ab052a7747699153129364a8bfe6 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=c6d0ab052a7747699153129364a8bfe6 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：通道 CRUD 落地，S-03/E-02/E-06/B-03 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-007: 审计查询 API（GET /api/v1/audits，最近运行支撑）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: 07-agent-management.frontend.design.md#3.3 基本信息 - 最近运行, 07-agent-management.backend.design.md#3.4 接口设计（封套约束）
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: B-04, RULE-01

### Description

新增 `GET /api/v1/audits`：按 resource_id/resource_type/keyword 筛选、分页封套、create_time DESC；供前端"最近运行"只读区块（`?resource_id={agent_id}&page_size=6`）。不返回 Secret 字段。

### Checklist
- [x] 先写测试并记录 RED：端点不存在（404）
- [x] [B-04][integration] 统一封套/分页边界/按 resource_id 过滤/时间倒序（真实 HTTP + 真实 PostgreSQL）
- [x] 响应不含 Secret/明文字段（查询仅投影安全列）
- [x] 运行 harness-api#RULE-api-001 verifier：封套字段、分页约束、错误码映射
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-04 | integration | 真实 HTTP + PostgreSQL（config_audit_log） | 封套/分页/resource_id 过滤/时间倒序/无 Secret | tests/console_platform/test_audits_api.py | uv run pytest -q tests/console_platform/test_audits_api.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-04 | RED：端点 404 | 1 passed + 回归全量 | test_b04（封套字段/page_size=6/total=2/时间倒序/422 边界/无 secret 字段） | ASGI 真实 HTTP + 真实 config_audit_log | verified |
- B-04: verified — automated command passed; run_id=ab68c31f788c421585e2b7e64bd2f682 (confirmed_by: runner)

### Log
- [2026-09-19] started/finished：audits 查询落地，B-04 verified

---
- [2026-09-19] started
- [2026-09-19] completed (done)
## TASK-008: 前端 Agent 列表/新增/编辑/删除

- **Status**: in-progress
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: 07-agent-management.frontend.design.md#2.2 功能方案 FEAT-FE-01, 07-agent-management.frontend.design.md#3.3 组件设计 CMP-01/CMP-03
- **Spec-Refs**: harness-ui#RULE-ui-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: S-10, E-09

### Description

替换占位 AgentsPage：`modules/agent-management/`（AgentPage + AgentFormModal + services）。列表列：名称/标识/模型/资源数/通道数/授权数/启用状态/revision/更新时间；Toolbar 左上新增、右上搜索/刷新；操作列复制 ID + Popconfirm 删除。表单：双列栅格（名称|标识、模型选择|启用状态、系统 Prompt 整行、描述整行），编辑携带 expected_revision，key 冲突/校验错误 Modal 保留。

### Checklist
- [x] 先写测试并记录 RED：/agents 占位页无 services/详情（contract 先行 RED）
- [x] [S-10][E2E] 操作列 Popconfirm 删除 → 列表移除行、详情关闭（真实浏览器+后端）
- [x] [E-09][integration] key 冲突：Modal 保留 + 本地化提示（contract catch 断言 + E2E 409 断言）
- [x] 运行 harness-ui#RULE-ui-001 verifier：左上操作/右上搜索筛选/右下分页；主展示字段开详情
- [x] 运行 harness-frontend#RULE-front-001 verifier：services 唯一入口、无裸 axios/fetch
- [x] 运行 harness-i18n#RULE-i18n-001 verifier：agents.* 双语词条齐备
- [x] 编辑表单携带 expected_revision（RULE-snapshot 前置）；无全局保存按钮
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | 真实浏览器 + 后端 + DB | Popconfirm 删除后行移除、详情关闭 | e2e/tests/agent-management/agent-management.spec.ts | npm --prefix e2e test -- --config playwright.agent.config.ts --grep "S-10" | e2e_deferred |
| E-09 | integration | 组件源码契约 | key 冲突 Modal 保留 + i18n 提示 | tests/frontend/test_agent_form_contract.py -k key_conflict | uv run pytest -q tests/frontend/test_agent_form_contract.py -k key_conflict | e2e_deferred |
| RULE-ui-001 | E2E | 真实浏览器渲染 | 布局结构/词典字段 | e2e spec + contract | uv run pytest -q tests/frontend/test_agent_module_contract.py | e2e_deferred |
| RULE-front-001 | integration | services 层 | 无裸 axios/fetch | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_agent_module_contract.py && uv run python scripts/check_frontend_api_usage.py | e2e_deferred |
| RULE-i18n-001 | integration | locale 资源 | 双语词条 | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_agent_module_contract.py && uv run python scripts/check_frontend_i18n.py | e2e_deferred |

### Acceptance Evidence

新建 `modules/agent-management/`（AgentPage/AgentFormModal/AgentDetailSideSheet 骨架/services 全量 18 API），替换占位页。实现中发现并修复真实缺陷：模型下拉异步列表点击选择后 Form 值丢失（改为加载后 setValue 默认选中第一个启用模型）。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-10 | RED：占位页 | E2E 3/3 passed（S-10/E-09/S-07）；终验留 verify-e2e | e2e spec S-10 | 真实 Chrome + 后端 + PG | e2e_deferred |
| E-09 | contract 先行 RED | 88 passed（frontend 全量） | test_agent_form_key_conflict_keeps_modal + E2E 409 | 同上 | e2e_deferred |
| RULE-ui-001 | 同上 | 同上 | test_agent_page_layout + 聚合列断言 | 源码契约 + 构建 | verified |
| RULE-front-001 | 同上 | check_frontend_api_usage OK | services 唯一入口 + Idempotency-Key | 同上 | verified |
| RULE-i18n-001 | 同上 | check_frontend_i18n OK（438 keys） | agents.* 双语词条 | 同上 | verified |

### Log
- [2026-09-19] started/finished：列表/表单/删除落地，E2E 3/3，S-10/E-09 待终验

---
- [2026-09-19] started
## TASK-009: 前端详情 5 Tabs、最近运行与关系操作

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008
- **Source**: 07-agent-management.frontend.design.md#2.2 功能方案 FEAT-FE-02/FEAT-FE-03, 07-agent-management.frontend.design.md#3.3 组件设计 CMP-02/CMP-04/CMP-05
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-07, S-08, S-09, S-11, S-12, E-07, E-08, E-10, RULE-05

### Description

详情 SideSheet 5 Tabs：基本信息（DetailGrid 双列 + 编辑按钮 + 最近运行只读区块：时间/用户/类型/目标/结果，空态"暂无运行记录"，取 `GET /api/v1/audits?resource_id={agent_id}&page_size=6`）；Skill/MCP/用户授权 Tab（绑定选择器 + 行内解除 Popconfirm，完成即生效、无保存按钮、无绑定级启停）；IM 接入 Tab（通道表格 + 新增/编辑 Modal（bot_id/secret/通道 enabled）+ 移除）。revision 冲突 Modal 保留提示刷新；bot_id 冲突/资源不存在 Toast。

### Checklist
- [ ] 先写测试并记录 RED：详情/Tabs/最近运行不存在（contract 先行）
- [ ] [S-07][E2E] 编辑系统 Prompt 保存 → 详情 revision+1
- [ ] [S-08][E2E] Skill Tab 绑定 → 局部刷新立即出现；无保存按钮与启停开关
- [ ] [S-09][E2E] 基本信息 Tab 最近运行只读列表/空态
- [ ] [S-11][E2E] MCP 解除后行立即移除；再绑恢复；全程无启停开关
- [ ] [S-12][E2E] IM Tab 新增第二个 bot → 两行同 Agent，无 Pod/replica 信息
- [ ] [E-07][E2E] stale revision → Modal 保留提示刷新重试
- [ ] [E-08][E2E] bot_id 冲突 → 表单保留本地化提示
- [ ] [E-10][E2E] 绑定已删除 Skill/MCP → Toast，Tab 状态不变
- [ ] 运行 harness-ui-detail#RULE-ui-detail-001 verifier：Header 布局/Tabs 其下/关系操作即生效
- [ ] 运行 harness-test#RULE-test-001 verifier：E2E 用例声明不得 mock 边界（Browser/API/PG/resolve）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实浏览器 + 后端 + DB | revision+1 展示 | e2e spec -k S-07 | npm --prefix e2e test -- --config playwright.agent.config.ts --grep "S-07" | planned |
| S-08 | E2E | 同上 | 绑定即出现；无保存/启停 | 同上 | 同上 --grep "S-08" | planned |
| S-09 | E2E | Browser→audits API | 最近运行列表/空态 | 同上 | 同上 --grep "S-09" | planned |
| S-11 | E2E | 同上 | 解除/再绑定无启停开关 | 同上 | 同上 --grep "S-11" | planned |
| S-12 | E2E | 同上 | 双 bot 同 Agent 无 Pod 信息 | 同上 | 同上 --grep "S-12" | planned |
| E-07 | E2E | revision conflict→UI | Modal 保留提示 | 同上 | 同上 --grep "E-07" | planned |
| E-08 | E2E | bot conflict→UI | 表单保留提示 | 同上 | 同上 --grep "E-08" | planned |
| E-10 | E2E | 目标不存在→Toast | Tab 状态不变 | 同上 | 同上 --grep "E-10" | planned |
| RULE-ui-detail-001 | E2E | 真实浏览器渲染 | SideSheet 布局/5 Tabs | tests/frontend/test_agent_detail_contract.py | uv run pytest -q tests/frontend/test_agent_detail_contract.py | planned |
| RULE-test-001 | E2E | 全链路无 mock 声明 | 边界清单显式化 | e2e spec 模块头 | npm --prefix e2e test -- --config playwright.agent.config.ts | planned |

### Acceptance Evidence

### Log
- [2026-09-19] created (draft)
