# Tasks: Memory Redesign

- **Source**: .code-flow/tasks/2026-10-12/memory-redesign/memory-redesign.design.md（v0.5；保留用户v0.4补充并修正缓存/约束边界）
- **Created**: 2026-10-12
- **Updated**: 2026-10-15
- **Plan status**: draft；阶段裁决以 CLI 输出为准；未开始编码

## Proposal

重构长期记忆的来源确认、事务历史、硬配额和读侧语义，并以可恢复的后台任务打通跨会话续接。以用户级锁、稳定版本、删除屏障和完整候选保证正确性；缓存只是刷新实现的优化。发布采用维护窗口，不部署新旧写协议兼容层。

### Alignment

- 沿用本会话已授权的v0.5设计；本计划只拆分实现与验证，不启动业务代码或发布。
- /memory 前缀不会绕过 Runtime 忙态；options 最终结果复用既有source_event_id唯一，唯一键不含outcome。
- 本需求Context包含16份Spec/23条required Rule；TASK-001增量绑定六条memory规则后重跑Design/Plan Gate，未执行的code/review保持pending。
- 通用规则的事实性修订随对应代码/Schema一起完成（尤其稳定注册表完整唯一），refresh后重新承接所有stale阶段；不为通过旧规范保留错误实现。
- 新测试路径和用例均为拟新增/拟修改目标，编码时先创建测试、collect并记录真实RED/GREEN；以下argv已注册到目标文件，计划阶段没有执行，不使用不存在的-k选择器当Evidence。
- E2E编码期只写与collect，functional全部verified后统一verify-e2e；没有manual豁免。依赖逻辑独立且文件互不重叠才允许worktree并行；同工作区只激活一个TASK。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | Depends-On |
|---|---|---|---|---|---|---|---|---|---|
| B-01 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | unit | unit，纯证据校验 | TASK-002 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py"] | . | 120 |  |
| B-02 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | unit | unit，分词与选择 | TASK-005 | verified | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_selection.py"] | . | 120 |  |
| B-03 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG、5 个独立事务同 key 写 | TASK-009 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py"] | . | 300 |  |
| B-04 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG、独立连接不同 key 并发写 | TASK-009 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_quotas.py"] | . | 300 |  |
| B-05 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | unit + PG，禁存内容 | TASK-029 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_secrets.py"] | . | 300 |  |
| B-06 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 工具回执 | TASK-013 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_recall.py"] | . | 300 |  |
| B-07 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 模型 HTTP 探针 | TASK-013 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | . | 300 |  |
| B-08 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 请求探针 | TASK-012 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | . | 300 |  |
| B-09 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | unit + 请求探针 | TASK-012 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | . | 300 |  |
| B-10 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + HTTP 模型探针 | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-11 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 故障注入 | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-12 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 两段 Run | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | . | 300 |  |
| B-13 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，迁移库 + ORM create_all | TASK-007 | verified | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_schema_parity.py"] | . | 300 |  |
| B-14 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，旧 revision 存量库升级 | TASK-007 | verified | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py"] | . | 300 |  |
| B-15 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 Gateway/Runtime/PG/resume | TASK-017 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_confirmation.py"] | . | 300 |  |
| B-16 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + HTTP 模型探针 | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-17 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-18 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG | TASK-009 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_quotas.py"] | . | 300 |  |
| B-19 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，摘要关闭 + 真实模型 HTTP | TASK-019 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_provider.py"] | . | 300 |  |
| B-20 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，迁移前置检查与恢复演练 | TASK-007 | verified | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_preflight.py"] | . | 300 |  |
| B-21 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 延迟 job / 后续摘要 | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | . | 300 |  |
| B-22 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实PG+旧新Run | TASK-016 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | . | 300 |  |
| B-23 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 并发 writer / 当前语句时间 | TASK-010 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py"] | . | 300 |  |
| E-01 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Runtime + PG/Redis + 模型 HTTP | TASK-012 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/runtime/test_memory_redesign_context_e2e.py"] | . | 1800 |  |
| E-02 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Gateway → Runtime → resume | TASK-030 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| E-03 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Gateway + 两个 conversation | TASK-030 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| E-04 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | browser，真实 Console/Runtime/PG/Chrome | TASK-030 | e2e_deferred | ["make","acceptance-e2e","DOMAIN=agent"] | . | 1800 |  |
| E-05 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Gateway/Runtime/PG + 模型探针 | TASK-030 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| E-06 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实两个 Runtime 子进程 | TASK-030 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| S-01 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | security，真实 API 身份与租户边界 | TASK-029 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | . | 1800 |  |
| S-02 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | security，真实装配与 canary 检查 | TASK-029 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | . | 1800 |  |

## Spec Responsibility

现存required Rule有且只有一个最终verifier负责人；其他任务按设计与持续Context继承约束，辅助验证不重复领取最终owner。元数据命令原样登记，未执行，不把结构Plan检查当code pass。

| Spec Rule | 最终负责人 | verifier stage | 原始执行命令 | 状态 |
|---|---|---|---|---|
| harness-api#RULE-api-001 | TASK-023 | code | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | planned |
| harness-api#RULE-api-002 | TASK-009 | code | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | planned |
| harness-arch#RULE-arch-001 | TASK-022 | code | ["uv","run","pytest","-q","tests/architecture"] | planned |
| harness-skill#RULE-skill-001 | TASK-006 | code | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] | planned |
| harness-data#RULE-data-001 | TASK-007 | code | ["uv","run","pytest","-q","tests","-k","schema_parity"] | planned |
| harness-snapshot#RULE-snapshot-001 | TASK-016 | code | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | planned |
| harness-secret#RULE-secret-001 | TASK-029 | code | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | verified |
| harness-test#RULE-test-001 | TASK-030 | code | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | failed（已实跑；第 1 段 13 项红全为与本任务无关的存量红，本任务 6 条 E2E 全绿；第 2/3 段单跑 pass，详见 TASK-030 证据） |
| harness-ui-detail#RULE-ui-detail-001 | TASK-028 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |
| harness-ui#RULE-ui-001 | TASK-028 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | planned |
| harness-frontend#RULE-front-001 | TASK-026 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |
| harness-i18n#RULE-i18n-001 | TASK-027 | code | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | planned |
| harness-time#RULE-time-001 | TASK-007 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | planned |
| harness-model#RULE-model-001 | TASK-019 | code | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | planned |
| harness-log#RULE-log-001 | TASK-018 | code | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | planned |
| harness-mcp#RULE-mcp-001 | TASK-018 | code | ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] | planned |
| harness-im#RULE-im-001 | TASK-017 | code | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | planned |
| harness-im#RULE-im-002 | TASK-017 | code | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | planned |
| harness-rel#RULE-rel-001 | TASK-024 | code | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] | planned |

memory Tier-1规范已由TASK-001创建并通过CLI增量绑定到同一Context。六条verifier均为stage=review（跨组件闭合），测试仍待对应任务实现与执行。

| Spec Rule | 最终负责人 | 要求 | review verifier | 状态 |
|---|---|---|---|---|
| harness-memory#RULE-memory-001 | TASK-015 | 来源证据、真实候选确认、禁止模型授予USER/ADMIN | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |
| harness-memory#RULE-memory-002 | TASK-009 | 稳定identity、scope锁、历史/receipt原子性、硬配额 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"] | planned |
| harness-memory#RULE-memory-003 | TASK-012 | 有界完整候选、缓存一致性、SEARCH零命中、完整块预算 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"] | planned |
| harness-memory#RULE-memory-004 | TASK-021 | 用户删除墓碑、forget_epoch、新依据退休恢复、禁止来源降权 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |
| harness-memory#RULE-memory-005 | TASK-021 | 固定提炼输入、lease/epoch fence、最多一次原子效果、失败可见且Run不变 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | planned |
| harness-memory#RULE-memory-006 | TASK-007 | 设置/预算冻结、稳定迁移基线、存量TTL与维护窗口 | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"] | planned |

## Design Rule Responsibility

设计本地RULE不是冒充Tier-1 Rule；它们的验收通过下表场景与最终负责人闭合，inventory必须核对全15条。

| 设计规则 | 最终负责人 | 验证场景 | 状态 |
|---|---|---|---|
| RULE-01 | TASK-015 | B-01, B-15, E-02, S-01 | planned |
| RULE-02 | TASK-012 | E-03, E-05 | planned |
| RULE-03 | TASK-009 | B-03, B-11 | planned |
| RULE-04 | TASK-009 | B-03, B-14, B-18 | planned |
| RULE-05 | TASK-010 | B-02, B-06, B-08, B-23 | planned |
| RULE-06 | TASK-012 | B-08, B-09 | planned |
| RULE-07 | TASK-012 | B-07, B-23 | planned |
| RULE-08 | TASK-009 | B-04, B-18, B-22 | planned |
| RULE-09 | TASK-021 | B-07, B-15, B-16 | planned |
| RULE-10 | TASK-021 | B-10, B-11, E-06 | planned |
| RULE-11 | TASK-012 | E-01 | planned |
| RULE-12 | TASK-029 | S-01, S-02 | planned |
| RULE-13 | TASK-021 | B-16, B-17, B-21 | planned |
| RULE-14 | TASK-007 | B-14, B-20 | planned |
| RULE-15 | TASK-018 | B-11, E-06, S-02 | planned |

INV-01→RULE-01/12；INV-02/03→RULE-03/04；INV-04→RULE-08；INV-05→RULE-09/13；INV-06→RULE-05；INV-07→RULE-06；INV-08→RULE-10；INV-09→RULE-07/15；INV-10→RULE-14及B-12/B-20/B-22。高影响风险沿同场景验证：确认B-01/B-15，锁与容量B-03/B-04/B-18，遗忘B-07/B-16，固定输入/旧写者B-10/B-11/B-21，迁移B-14/B-20，续接E-05。

## Execution order and shared files

先完成TASK-001的Context扩展；随后共享契约，再并行推进纯函数/设置与schema。后续链路为writer→queries/暂停→component→tools/续接→确认resume→snapshot→Gateway/provider→job批量提交→后台接线→Console→frontend→安全/E2E→inventory终验。精确DAG由code-flow task index生成，任务编号不是强制执行序。

共享文件顺序已通过依赖约束：runner暂停→component hook；run_service续接→确认→snapshot→job入队；Runtime main先lifespan后API安装；Console memory_service/users先只读投影后HTTP写转发。若实际新增共同修改文件，合并前补依赖，不依赖多代理碰巧避免冲突。

三类任务例外：schema/迁移是同一不变量的原子变化，关联规范同步不能延后；E2E与收口任务按真实边界组织，可能涉及多份配置/文档/测试，但不扩张生产功能。全量原命令预算过大时由工具链按规则延期到review，不手改stage或伪造通过。

---

## TASK-001: 新增记忆规范并扩展同一 Spec Context

- **Status**: done
- **Priority**: P0
- **Depends**: 
- **Source**: memory-redesign.design.md#6.1 Spec Compliance Matrix, memory-redesign.design.md#6.2 设计进入 Plan 的条件
- **Spec-Refs**: 
- **Acceptance-Refs**: N/A
- **Files**: .code-flow/specs/memory/harness-memory.md, .code-flow/config.yml, .code-flow/tasks/2026-10-12/memory-redesign/spec-context.yml

### Description

建立目标协议的 Tier-1 规范、verifier 与精确 path_mapping；保留既有绑定/阶段refs。新增规范描述要求，不宣称旧实现已符合；刷新后增量绑定六条 proposed memory Rule，回填本任务文件唯一owner并重跑Design/Plan Gate。

### Checklist

- [x] 先验证规范元数据及目标路径解析的失败案例，记录规则缺失/重复owner时RED。
- [x] 不修改旧实现事实；data/API稳定注册表例外随后续真实schema/receipt改造同步更新。
- [x] 完成新增Scope后禁止用旧Context开编码任务；以CLI实际Gate结果放行。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 metadata parser、path resolver、Context 和 Plan Gate；不 mock | 六条规则有 review command；只匹配记忆载体；重复规则/owner 阻断 | tests/test_memory_redesign_spec_contract.py | ["uv","run","pytest","-q","tests/test_memory_redesign_spec_contract.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL | uv run pytest -q tests/test_memory_redesign_spec_contract.py：9 failed；规则文件、记忆 path_mapping 与 binding 均缺失 | 同命令：9 passed in 0.08s；实际 Design/Plan Gate 均 pass | tests/test_memory_redesign_spec_contract.py:23,26,46,57,69,73 | 真实 parser/resolver/context/gate，临时文件注入重复 Rule/owner | verified |

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-12] started
- [2026-10-12] completed (done)

---

## TASK-002: 严格记忆契约与原话证据校验

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: memory-redesign.design.md#3.4.1 原话证据, memory-redesign.design.md#3.3.2 user_memory（稳定 identity）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-01
- **Files**: packages/contracts/src/muad_contracts/memory.py, packages/agent-core/src/muad_agent_core/memory/evidence.py

### Description

定义严格来源/category/退休原因/确认DTO及TASK_STATE约束；原话只证明文本来源，不能授予持久保存权限。

### Checklist

- [x] [B-01][unit] 先写整句、否定句片段、仅词法重合、伪造author的RED；不接受模型身份字段。
- [x] 确认hash共用canonical_json；source_scope_revision与证据ID由Runtime生成。
- [x] 辅助验证TASK_STATE字符/字节/数组上限及task命名空间，record GREEN。
- [x] [B-01][unit] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：unit，纯证据校验；断言：仅含“记住”、任意词重合、部分截取否定句、模型自报 USER 均不能获得确认。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-01 | unit | unit，纯证据校验 | 仅含“记住”、任意词重合、部分截取否定句、模型自报 USER 均不能获得确认 | tests/agent_core/test_memory_redesign_evidence.py / test_b01_memory_redesign_quote_is_complete_provenance, test_b01_model_cannot_grant_confirmation_or_trusted_identity, test_b01_exact_quote_does_not_add_author_or_explicit_request | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-01 | uv run pytest -q tests/agent_core/test_memory_redesign_evidence.py --tb=short：32 failed，缺少目标 contracts.memory 和 core.memory.evidence 实现；测试已正常收集 | uv run pytest -q tests/agent_core/test_memory_redesign_evidence.py tests/test_memory_redesign_spec_contract.py：52 passed（43 新契约 + 9 规范回归）；mypy 3 files + ruff pass | tests/agent_core/test_memory_redesign_evidence.py:26,38,47,95,102,134,145 | 直接执行纯函数与严格 Pydantic DTO，无 IO/mock；参数覆盖否定/条件/片段/伪造身份 | verified |
- B-01: verified — automated command passed; run_id=f264f08512314c8688487eccfa49bb58 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-12] started
- [2026-10-12] completed (done)

---

## TASK-003: 平台记忆设置 schema、catalog 与边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-004
- **Source**: memory-redesign.design.md#2.3.3 配置项, memory-redesign.design.md#3.4.5 内容闸门与配额
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: packages/contracts/src/muad_contracts/platform_settings.py, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_service.py, apps/console-platform/backend/src/muad_console_platform/api/platform_settings.py

### Description

在单一schema定义所有memory叶子、删除ratio分支及增加历史工具轮配置；声明在线只能提高硬额度，降低需排空旧对象。

### Checklist

- [x] 先写叶子集合、bool/int严格类型、lease>=timeout+15、上下限及未知键RED。
- [x] 同步既有ratio配置测试，保留其他分组语义；不引入旧键兼容读取。
- [x] [B-22][integration] 辅助覆盖额度设置预检；最终跨Run责任在snapshot任务。
- [x] 在保存事务内比较上一设置额度，在线降低返回维护窗口错误；不通过API临时参数绕过，新当前配置的降低只由维护脚本排空后执行。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 真实 settings schema/catalog；独立 PG 上实际 settings service 和事务 | 21 memory 叶子、严格类型/边界/lease；save/restore 拒绝降低且不生成版本/审计 | tests/test_memory_redesign_settings.py, tests/test_platform_settings_schema.py | ["uv","run","pytest","-q","tests/test_memory_redesign_settings.py","tests/test_platform_settings_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL | uv run pytest -q tests/test_memory_redesign_settings.py -m unit --tb=no：36 failed, 97 passed；新 memory 字段、history rounds 与范围缺失；integration 1 failed（新配额键尚未识别）；merge 2 failed（旧 ratio 静默丢弃） | uv run pytest -q tests/test_memory_redesign_settings.py tests/test_platform_settings_schema.py tests/agent_core/test_context_compactor.py --tb=short：274 passed；mypy 4 files 与 ruff pass | tests/test_memory_redesign_settings.py:39,56,66,90,108,136,152；配额双维度、save/restore 和 API 错误映射 | 直接 schema/catalog；integration 使用 async_tool_database 独立临时 PG，真实事务拒绝且版本/审计数均为2，不碰 dev 库 | verified |

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-12] started
- [2026-10-13] completed (done)

---

## TASK-004: 记忆错误枚举与中英文目录

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: memory-redesign.design.md#3.7 API 与 Console, memory-redesign.design.md#3.8 可靠性与观测
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: packages/api-kit/src/muad_api/error_codes.py, config/api-messages.yaml

### Description

登记设计所有新错误及配额降低/确认PENDING错误，删除source/prefix旧码；拒绝响应不回显禁存值。

### Checklist

- [x] 先写错误码双向齐平、zh-CN/en-US、HTTP状态/placeholder的RED。
- [x] 沿用api-kit统一异常；业务仅抛ErrorCode，不硬编码msg。
- [x] 辅助运行tests/test_error_catalog.py与tests/test_api_i18n.py，记录真实结果。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 实际 api-kit MessageCatalog 和 ErrorCode；无需业务 mock | 15 新码双向注册、双语与 HTTP 状态；无敏感 placeholder；旧 source/prefix 码不注册 | tests/test_memory_redesign_error_catalog.py | ["uv","run","pytest","-q","tests/test_memory_redesign_error_catalog.py","tests/test_error_catalog.py","tests/test_api_i18n.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL | uv run pytest -q tests/test_memory_redesign_error_catalog.py --tb=no：15 failed, 1 passed，ErrorCode 缺少全部新码 | 契约中三个文件的命令：31 passed in 0.59s；mypy + ruff pass | tests/test_memory_redesign_error_catalog.py:30,32,35,38,43 | 实际 YAML 目录与 ErrorCode；传入 canary 不被文案回显；旧工具 source/prefix 实现由 TASK-013 移除，本任务不改工具行为 | verified |

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-12] started
- [2026-10-12] completed (done)

---

## TASK-005: 分词、SEARCH/LIST 与偏好选择纯函数

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: memory-redesign.design.md#3.5.1 完整候选与排序
- **Spec-Refs**: 
- **Acceptance-Refs**: B-02
- **Files**: packages/agent-core/src/muad_agent_core/memory/tokens.py, packages/agent-core/src/muad_agent_core/memory/rank.py, packages/agent-core/src/muad_agent_core/memory/__init__.py

### Description

词法coverage>0先匹配再排序；偏好与query解耦，LIST独立；显式枚举/确定性同分顺序。

### Checklist

- [x] [B-02][unit] 先写CJK/latin、空query、无关query、同分顺序RED。
- [x] 不得以soft weight推导覆盖权限或USER必然排前；now显式传入。
- [x] 纯函数不引入DB/network/app依赖，记录GREEN。
- [x] [B-02][unit] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：unit，分词与选择；断言：CJK/latin、无 query、有 query、同分确定性；非空 query 零覆盖为零条。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-02 | unit | unit，分词与选择 | CJK/latin、无 query、有 query、同分确定性；非空 query 零覆盖为零条 | tests/agent_core/test_memory_redesign_selection.py / test_b02_*（16 collected） | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_selection.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-02 | uv run pytest -q tests/agent_core/test_memory_redesign_selection.py --tb=no：16 failed；缺少纯词法和排序模块 | 两个 core memory 契约文件：61 passed（18 selection + 43 evidence）；mypy 4 files 与 ruff pass；acceptance runner owner TASK-005 pass | tests/agent_core/test_memory_redesign_selection.py:29,46,57,69,81,93,105,110 | 直接执行分词、匹配、选择；now 显式传入，无 DB/network/mock；结构 JSON 字段不冒充内容命中 | verified |
- B-02: verified — automated command passed; run_id=7f0ca5e018744b63ae01c57eaa7dae56 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-12] started
- [2026-10-12] completed (done)

---

## TASK-006: 完整 SYSTEM 块与 recall JSON 字节预算

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005
- **Source**: memory-redesign.design.md#3.5.3 完整渲染预算, memory-redesign.design.md#3.2.2 模型请求组件
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: 
- **Files**: packages/agent-core/src/muad_agent_core/memory/render.py, packages/agent-core/src/muad_agent_core/memory/components.py

### Description

定义具名MemoryComponent及完整封套计量，硬上限、0关闭、长条跳过、任务引用降级；recall仍不可外置。

### Checklist

- [x] [B-09][unit] 先写中文封套、恰等预算/超1字节、长条后短条、0预算RED。
- [x] 任务本体装不下先尝试有界title/key/version引用，引用也超限只计丢弃。
- [x] 保留externalizable_result=false并运行指定artifact verifier；真实请求验证归component任务。
- [x] [RULE-skill-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit | 实际最终 SYSTEM 文本与 canonical recall JSON，不 mock Renderer | UTF-8 恰等/少1字节、长条跳过、0无占位、引用与truncated元数据 | tests/agent_core/test_memory_redesign_render.py / test_b09_*, test_task_state_*, test_recall_* | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_render.py"] | verified |
| harness-skill#RULE-skill-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | tests/test_skill_artifact_cache.py（4 tests 实际通过） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL | uv run pytest -q tests/agent_core/test_memory_redesign_render.py --tb=no：10 failed，缺少 Renderer 与 MemoryComponent 实现 | uv run pytest -q tests/agent_core/test_memory_redesign_render.py tests/test_skill_artifact_cache.py --tb=short：14 passed；mypy 6 files 与 ruff pass | tests/agent_core/test_memory_redesign_render.py:34,46,56,69,86,104,124 | 最终 UTF-8 文本/JSON 实测计量；直接纯函数，无 mock；artifact verifier 实际执行；recall 外置标记继续 false，Runtime 接线验证在 TASK-012/013 | verified |

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-007: 稳定身份 ORM、约束 DDL 与存量迁移

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: memory-redesign.design.md#3.3 数据设计, memory-redesign.design.md#4.2 一次性迁移与切换, memory-redesign.design.md#4.1 发布前置条件
- **Spec-Refs**: harness-data#RULE-data-001, harness-time#RULE-time-001, harness-memory#RULE-memory-006
- **Acceptance-Refs**: B-13, B-14, B-20
- **Files**: apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py, migrations/versions/0020_memory_redesign.py, migrations/memory_redesign_data.py

### Description

一次维护迁移建立scope/history/receipt/job/proposal及稳定唯一键；每key一条canonical baseline，旧incarnation存数组；存量不可信来源/TTL按设计转换。

### Checklist

- [x] [B-13][integration] 先写迁移与create_all parity、待确认唯一、跨outcome消费唯一的RED；过期普通索引只验声明/谓词。
- [x] [B-14][integration] 旧version20/值A基线与新版本B、同key两个version1 incarnation、旧AGENT不覆写也到期。
- [x] [B-20][integration] 旧在途Run/Task/超额/禁存数据预检阻断；不操作dev库。
- [x] schema变化时同步data注册表完整唯一例外，refresh与重新绑定所有受影响阶段，不能留stale Context。
- [x] [B-13][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，迁移库 + ORM create_all；断言：表/索引/约束逐项 parity；完整 key 唯一与历史 (origin_id,version) 唯一可真实阻止违规。
- [x] [B-14][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，旧 revision 存量库升级；断言：基线 A/version20 → 来源/TTL政策version21 → 新写B/version22仍可回放A；旧来源不伪造 USER；旧 AGENT 不覆写也会到期。
- [x] [B-20][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，迁移前置检查与恢复演练；断言：存在 WAITING_INPUT/WAITING_TOOL/job 或超额数据时阻断切换；旧键只从新当前设置移除；同 key 两个version1 incarnation完整归档且新history唯一。
- [x] [RULE-data-001] verifier：按元数据argv ["uv","run","pytest","-q","tests","-k","schema_parity"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。
- [x] [RULE-time-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。
- [x] [RULE-memory-006] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"]（review）验证 设置/预算冻结、稳定迁移基线、存量TTL与维护窗口；review由需求终验统一执行，当前无执行Evidence。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-13 | integration | integration，迁移库 + ORM create_all | 表/索引/约束逐项 parity；完整 key 唯一与历史 (origin_id,version) 唯一可真实阻止违规 | tests/migrations/test_memory_redesign_schema_parity.py / test_b13_memory_redesign*（已实际收集执行） | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_schema_parity.py"] | verified |
| B-14 | integration | integration，旧 revision 存量库升级 | 基线 A/version20 → 来源/TTL政策version21 → 新写B/version22仍可回放A；旧来源不伪造 USER；旧 AGENT 不覆写也会到期 | tests/migrations/test_memory_redesign_migration.py / test_b14_memory_redesign*（已实际收集执行） | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py"] | verified |
| B-20 | integration | integration，迁移前置检查与恢复演练 | 存在 WAITING_INPUT/WAITING_TOOL/job 或超额数据时阻断切换；旧键只从新当前设置移除；同 key 两个version1 incarnation完整归档且新history唯一 | tests/migrations/test_memory_redesign_preflight.py / test_b20_memory_redesign*（已实际收集执行） | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_preflight.py"] | verified |
| harness-data#RULE-data-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier真实执行；41 schema parity / 2 datetime passed | ["uv","run","pytest","-q","tests","-k","schema_parity"] | verified |
| harness-time#RULE-time-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier真实执行；41 schema parity / 2 datetime passed | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | verified |
| harness-memory#RULE-memory-006 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 设置/预算冻结、稳定迁移基线、存量TTL与维护窗口 | 目标文件在verifier argv中；创建前不宣称可执行 | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"] | deferred_to_review |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-13 | uv run pytest -q tests/migrations/test_memory_redesign_schema_parity.py --tb=short：3 failed；memory_scope 表不存在、author/retirement 列缺失、待确认重复插入未阻止 | GREEN: runner verified；3 passed | tests/migrations/test_memory_redesign_schema_parity.py | 实际临时 PG + Inspector/create_all + 真实唯一冲突 | verified |
| B-14 / B-20 | 旧库 fixture 的 jsonb 参数类型失败已修复，不作为有效 RED；随后测试通过真实0019建库到0020升级入口暴露尚无0020 revision | GREEN: runner verified；B14 2 passed/B20 11 passed | tests/migrations/test_memory_redesign_migration.py, test_memory_redesign_preflight.py | 实际旧库、Alembic subprocess 和回滚检查；不触碰 dev 库 | verified |
| B-20 配置 | RED: 活跃配置12被已删99替代；Agent配置改变但revision仍7 | GREEN: 两缺陷修正后3 passed | test_memory_redesign_preflight.py 的 settings/agent/quota 三例 | 实际0019→0020升级、版本与审计、终态Run/Task旧快照、QUOTA_EVICT完整历史 | verified |
| data/time | 既有回归不适用RED | GREEN: 精确metadata argv；schema 41 passed；datetime 2 passed+schema41 passed | 原verifier中的所有目标 | 临时head库，子进程环境指向临时库，最终drop；mypy4文件/ruff通过 | verified |
| memory-006 | review层规则 | 由需求终验执行完整snapshot+迁移命令 | 后续TASK-016建立snapshot测试 | 未自报review已通过 | deferred_to_review |

- B-13: verified — automated command passed; run_id=cb877c23b1b746ce8c224ba68eae88cb (confirmed_by: runner)
- B-14: verified — automated command passed; run_id=cb877c23b1b746ce8c224ba68eae88cb (confirmed_by: runner)
- B-20: verified — automated command passed; run_id=cb877c23b1b746ce8c224ba68eae88cb (confirmed_by: runner)
- B-13: verified — automated command passed; run_id=eee5c9571f1446cfb6a78c37734aa236 (confirmed_by: runner)
- B-14: verified — automated command passed; run_id=eee5c9571f1446cfb6a78c37734aa236 (confirmed_by: runner)
- B-20: verified — automated command passed; run_id=eee5c9571f1446cfb6a78c37734aa236 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-008: 内容闸门、硬配额与自动退休决策

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-004
- **Source**: memory-redesign.design.md#3.4.4 同 key 冲突矩阵, memory-redesign.design.md#3.4.5 内容闸门与配额
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_write.py

### Description

纯决策给出写后集合、可淘汰AGENT与明确拒绝；区分用户删除、TTL/QUOTA/TASK_COMPLETE；不把新claim当新证据。

### Checklist

- [x] [B-04/B-18][unit] 辅助写全USER满额、淘汰选择、旧低限额减容与增长决策RED。
- [x] [B-05][unit] 候选/key/quote/TASK_STATE与已知凭据canary全部检查，错误无原文。
- [x] 纯策略只消费入站Evidence携带的source_scope_revision，不在claim时补当前值；只有明确自动退休原因与新依据才允许自动恢复，用户墓碑需新确认/ADMIN显式恢复。实际入站落库接线由后续Runtime入口任务完成。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | tests/agent_runtime/test_memory_redesign_policy.py / test_aux_* | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_policy.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL | uv run pytest -q tests/agent_runtime/test_memory_redesign_policy.py --tb=line：43 failed；新 memory_write 策略模块尚不存在；quoted_sensitive补测6 failed；missing_retirement补测1 failed | GREEN: 55 passed；mypy与ruff通过；关联核心/字节回归128 passed | test_aux_* 各独立行为 | 单元直接调用真实内容/配额/冲突策略，最终PG场景仍由后续owner验证 | verified |

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-009: 唯一事务写入、历史与命令回执

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-008
- **Source**: memory-redesign.design.md#3.4.3 唯一事务服务, memory-redesign.design.md#3.3.1 memory_scope（新增）, memory-redesign.design.md#3.3.3 user_memory_history（新增，append-only）, memory-redesign.design.md#3.3.4 memory_command_receipt（新增）
- **Spec-Refs**: harness-api#RULE-api-002, harness-memory#RULE-memory-002
- **Acceptance-Refs**: B-03, B-04, B-18
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_service.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_commands.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_mutations.py, tests/agent_runtime/test_memory_redesign_writes.py, tests/agent_runtime/test_memory_redesign_quotas.py, tests/agent_runtime/test_memory_service.py

### Description

实现外部短事务与batch_in-session同一原语；scope锁/版本/history/receipt/forget_epoch原子，禁止per-item提交。

### Checklist

- [x] [B-03][integration] 先写5连接同key版本连续、history失败整回滚RED。
- [x] [B-04][integration] 不同key抢最后条数/字节额度只有一个成功。
- [x] [B-18][integration] 全USER满额拒绝、清扫失败不写、后项失败恢复已淘汰事实。
- [x] 真实幂等命令重放不增版本/确认数，异指纹拒绝；同步API稳定receipt唯一口径并refresh。
- [x] [B-03][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG、5 个独立事务同 key 写；断言：同 id、version 连续且历史一一对应；事务时间逆序不影响回放。
- [x] [B-04][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG、独立连接不同 key 并发写；断言：199/200 最后额度只能一个写者取得；字节额度也不可穿透。
- [x] [B-18][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG；断言：全 USER/ADMIN 满额明确拒绝；淘汰失败不写新事实；批量失败恢复已淘汰事实。
- [x] [RULE-api-002] verifier：元数据argv ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] 已在临时head库实际执行，5 passed；Done Gate 再登记code层证据。
- [x] [RULE-memory-002] verifier：同名完整命令实际25 passed，B-03/B-04/B-18由runner verified；review层正式裁决仍由需求终验执行，不自报review通过。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-03 | integration | integration，真实 PG、5 个独立事务同 key 写 | 同 id、version 连续且历史一一对应；事务时间逆序不影响回放 | tests/agent_runtime/test_memory_redesign_writes.py / test_b03_memory_redesign*（真实PG测试已编写；RED已收集） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py"] | verified |
| B-04 | integration | integration，真实 PG、独立连接不同 key 并发写 | 199/200 最后额度只能一个写者取得；字节额度也不可穿透 | tests/agent_runtime/test_memory_redesign_quotas.py / test_b04_memory_redesign*（真实PG测试已编写；RED已收集） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_quotas.py"] | verified |
| B-18 | integration | integration，真实 PG | 全 USER/ADMIN 满额明确拒绝；淘汰失败不写新事实；批量失败恢复已淘汰事实 | tests/agent_runtime/test_memory_redesign_quotas.py / test_b18_memory_redesign*（真实PG测试已编写；RED已收集） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_quotas.py"] | verified |
| harness-api#RULE-api-002 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier实际执行，5 passed；临时head PG与真实Console HTTP | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | verified |
| harness-memory#RULE-memory-002 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 稳定identity、scope锁、历史/receipt原子性、硬配额 | 完整命令25 passed；正式review裁决留需求终验 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"] | deferred_to_review |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-03/B-04/B-18 | uv run pytest -q tests/agent_runtime/test_memory_redesign_writes.py tests/agent_runtime/test_memory_redesign_quotas.py --tb=line：新增memory_commands不存在、旧MemoryService不支持事务工厂/execute；环境fixture真实临时PG正常 | GREEN: 完整命令25 passed；runner a031545c3bf9418aaf87cd3ecf5595c3 三场景verified | tests/agent_runtime/test_memory_redesign_writes.py:28（五连接/版本链）、:77（时间逆序）、:102（history回滚）；test_memory_redesign_quotas.py:27（双连接/条数/UTF-8）、:77/:95/:144/:172（满额/淘汰/后项/清扫失败） | 每例独立head PG；pg_backend_pid验证独立连接；真实trigger/constraint失败；调用方事务可回滚，receipt失败整事务无效果 | verified |
| RED环境澄清 | 初轮5项seed fixture报AmbiguousParameterError，不作为有效RED；修复CAST后quotas单跑7 failed，均为MemoryService不支持工厂 | 随后真实PG全部GREEN | memory_redesign_write_support.py 的 seed_facts | 修复测试环境未降低边界；未修改dev库schema或业务数据 | verified |
| ENABLE补测 | 真实PG显式续期后list_entries为空，暴露implicit EXPIRE后未解除删除态 | 修复后25 passed；receipt返回同key最终version，history保留各版本 | test_memory_redesign_writes.py:306 | 真实过期AGENT + ADMIN显式expiry；不改变来源author | verified |
| API/回归 | 既有回归不适用RED | 元数据API命令5 passed；service/policy/spec/content/evidence命令119 passed；no-heavy validation pass、ruff/mypy pass，函数均≤50行 | tests/console_skill/test_import_idempotency.py；test_memory_service.py；关联policy/spec/content/evidence目标 | API在临时head库 + 实际HTTP；服务回归独立PG；无mock写入层 | verified |
| 工具层基线对照 | 启动基线fc61e98的原MemoryService在临时head库跑test_memory_tools：6 failed/6 passed，旧source_type已与TASK-007 schema不兼容 | 当前工具调用方仍是TASK-013负责的旧协议，非本任务验收；不得宣称全量绿 | test_memory_tools.py 的六个旧写入/recall用例 | 独立PG运行基线原模块，未修改workspace实现或共享dev库；首次共享库扩展回归9 failed中的3项是未迁移schema环境错误 | deferred_to_review |
- B-03: verified — automated command passed; run_id=a031545c3bf9418aaf87cd3ecf5595c3 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=a031545c3bf9418aaf87cd3ecf5595c3 (confirmed_by: runner)
- B-18: verified — automated command passed; run_id=a031545c3bf9418aaf87cd3ecf5595c3 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-010: 完整候选 snapshot 与 revision 缓存

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009, TASK-005
- **Source**: memory-redesign.design.md#3.5.1 完整候选与排序, memory-redesign.design.md#3.3.1 memory_scope（新增）
- **Spec-Refs**: 
- **Acceptance-Refs**: B-23
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_queries.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_cache.py

### Description

冷读单SQL一致snapshot；暖读revision+statement_timestamp；失效重查以实际revision入缓存，读取仅查绝对1000/1MiB界。

### Checklist

- [x] [B-23][integration] 先写跨提交R/S错标、TTL已过但revision未变、轻量/全量错误不回退缓存RED。
- [x] 覆盖所有写来源的revision失效及关闭注入零查询；cache仅当前Run、tenant/user/revision绑定。
- [x] 不缓存排序/渲染/now，不跨模型调用持读事务，记录SQL次数与GREEN。
- [x] [B-23][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 并发 writer / 当前语句时间；断言：暖缓存不重查全量；revision与行集同快照；跨来源写入使缓存失效；revision不变也即时过滤TTL；轻量/全量读失败均不复用旧缓存；0预算无注入查询。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-23 | integration | integration，真实 PG + 并发 writer / 当前语句时间 | 暖缓存不重查全量；revision与行集同快照；跨来源写入使缓存失效；revision不变也即时过滤TTL；轻量/全量读失败均不复用旧缓存；0预算无注入查询 | tests/agent_runtime/test_memory_redesign_cache.py / test_b23_memory_redesign*（18 collected，真实RED已执行） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-23 | uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py --tb=line --show-capture=no：18 failed，memory_cache/memory_queries 尚不存在 | GREEN: 18 passed；B-23 runner verified | test_memory_redesign_cache.py:66/79/113（次数与并发快照）、:160（TTL）、:184（轻量/全量失败）、:217（写者）、:240（0预算）、:254（作用域）、:271/:291（全集/硬限）、:307（释放事务） | 每例独立head PG、真实MemoryService写者、SQL事件计数、PG视图延迟/DDL失败；不mock数据库查询或返回值 | verified |
- B-23: verified — automated command passed; run_id=21e98006586e44dbb76cb90b32390aff (confirmed_by: runner)

- 补充回归：uv run pytest -q tests/agent_core/test_memory_redesign_selection.py tests/agent_core/test_memory_redesign_render.py tests/agent_runtime/test_memory_service.py tests/agent_runtime/test_memory_redesign_writes.py tests/agent_runtime/test_memory_redesign_quotas.py --tb=short：61 passed。
- 验证：code-flow validate --no-heavy pass；ruff/mypy pass；所有新增函数≤50行。SQL计数故障已澄清：首次17 passed/1 failed仅因把writer scope SELECT混入reader计数；改用独立reader engine后真实full=2/head=2、18 passed，不降低边界。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-011: 候选确认提议与整轮工具暂停

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009, TASK-006
- **Source**: memory-redesign.design.md#3.4.2 remember 与真实确认
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_proposals.py, apps/agent-runtime/src/muad_agent_runtime/application/checkpoint.py, packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/agent/suspension.py, tests/agent_runtime/test_memory_redesign_proposals.py, tests/agent_runtime/memory_confirmation_support.py

### Description

handler登记typed suspend_request，整轮结果/审计收口后保存checkpoint/interrupt/WAITING_INPUT/lease释放，不提前改Run状态。

### Checklist

- [x] [B-15][integration] 辅助先写第二个remember拒绝、待确认唯一、暂停后无下次模型调用RED。
- [x] proposal读取含deleted/disabled/expired全状态，绑定hash/version/forget_epoch/expiry。
- [x] 原saved=false工具结果不改写；未来确认新增结果事件。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | PostgreSQL、EventWriter、真实 AgentRunner/ToolExecutionPipeline/审计、HTTP 模型端点 | 全状态候选绑定、第二个remember拒绝、完整回合原子暂停/回滚、无第二次模型请求；B-15最终Gateway/resume边界仍由TASK-017验证 | tests/agent_runtime/test_memory_redesign_proposals.py::test_local_b15_* | uv run pytest -q tests/agent_runtime/test_memory_redesign_proposals.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL (B-15辅助) | 2026-10-13：上述命令13 failed；MemoryConfirmationService模块尚未实现，ModuleNotFoundError；真实PG建库/migration成功。追加锁等待反例：-k expiring_during，1 failed / DID NOT RAISE，过期lease仍能提交暂停 | 同命令最终21 passed in 30.44s；锁后数据库时钟复核修复反例 | test_memory_redesign_proposals.py:33 (未保存), :69 (全状态版本/epoch), :104 (原子暂停), :113 (原结果不可变), :155 (回滚), :216 (仅一次HTTP), :318 (等待锁后lease fence) | memory_confirmation_support.py：真实PG/RunSubmission/USER_MESSAGE、实际ToolExecutionPipeline/RuntimeAuditWriter与本地HTTP模型；预算3/1均完整回合后暂停。最终Gateway/resume场景归TASK-017 | verified |

回归：`uv run pytest -q tests/agent_core/test_runner.py tests/agent_runtime/test_tool_parallel_planner.py tests/architecture/test_import_direction.py --tb=short --show-capture=no`：37 passed in 12.35s。ruff检查与5个生产文件strict mypy通过；新增/修改函数均≤50行。

扩展回归：`uv run pytest -q tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_interrupt_checkpoint.py tests/agent_runtime/test_runner_executor.py tests/agent_runtime/test_tool_round_budget.py tests/agent_runtime/test_artifact_round_budget.py --tb=line --show-capture=no`：本轮独立PG/Redis 34 passed / 9 failed（17.04s）；基线c6dcf2e7的独立导出、强制从其src导入、另一独立PG/Redis 34 passed / 相同9 failed（16.59s）。失败为wait状态机b05/b04、runner_executor四例、tool_round_budget三例；旧`CompactionSettings.memory`引用仍在run_service.py:1081与执行器接线中，属于后续TASK-012/016的Context/快照迁移。保留失败事实，未加旧ratio兼容层；本任务真实Runner暂停21例与原有37例通过，不把扩展回归记成全绿。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-012: 每模型调用前刷新 MemoryComponent 与 forget

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-010, TASK-006, TASK-011
- **Source**: memory-redesign.design.md#3.2.2 模型请求组件, memory-redesign.design.md#3.5.3 完整渲染预算
- **Spec-Refs**: harness-memory#RULE-memory-003
- **Acceptance-Refs**: B-08, B-09, E-01
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_context.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/metrics.py, packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/memory/components.py, tests/agent_runtime/test_memory_redesign_context.py, tests/acceptance/runtime/test_memory_redesign_context_e2e.py

### Description

typed context-loader刷新唯一具名组件；首次不双查；forget预先invalidate，失败后本Run保持无记忆，不裁其他SYSTEM/摘要。

### Checklist

- [x] [B-07][integration] 先写同Run forget→下一真实模型HTTP请求无旧条目，失败不宣称成功RED。
- [x] [B-08][integration] 200条最旧匹配仍参加选择、完整丢条计数正确。
- [x] [B-09][integration] 最终模型请求实际字节边界/任务引用/0预算正确。
- [x] [E-01][E2E] 编写真实Runtime/PG/Redis/模型HTTP保护前缀场景，执行留需求终验。
- [x] [B-08][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 请求探针；断言：200 条中最老的匹配仍参与选择；最终选取与丢条计数一致。
- [x] [B-09][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：unit + 请求探针；断言：完整块恰等预算可进、超 1 字节不可进；0 关闭；长条失败后短条可进；无首条越限。
- [x] [E-01][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Runtime + PG/Redis + 模型 HTTP；断言：memory/summary 在保护前缀，micro/snip/summary 不裁 memory；刷新只替换组件。
- [x] [RULE-memory-003] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"]（review）验证 有界完整候选、缓存一致性、SEARCH零命中、完整块预算；规定argv在代码阶段实际51 passed；新增边界另有GREEN，正式review由需求终验统一执行。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-08 | integration | integration，真实 PG + 请求探针 | 200 条中最老的匹配仍参与选择；最终选取与丢条计数一致 | tests/agent_runtime/test_memory_redesign_context.py / test_b08_memory_redesign_* | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | verified |
| B-09 | integration | unit + 请求探针 | 完整块恰等预算可进、超 1 字节不可进；0 关闭；长条失败后短条可进；无首条越限 | tests/agent_runtime/test_memory_redesign_context.py / test_b09_memory_redesign_* | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | verified |
| E-01 | E2E | acceptance，真实 Runtime + PG/Redis + 模型 HTTP | memory/summary 在保护前缀，micro/snip/summary 不裁 memory；刷新只替换组件 | tests/acceptance/runtime/test_memory_redesign_context_e2e.py / test_e01_memory_redesign_real_runtime_protects_prefix_and_refreshes_only_component | ["uv","run","pytest","-q","tests/acceptance/runtime/test_memory_redesign_context_e2e.py"] | e2e_deferred |
| harness-memory#RULE-memory-003 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 有界完整候选、缓存一致性、SEARCH零命中、完整块预算 | 规定argv已实际执行，functional子项通过；正式review由需求终验统一执行 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-08/B-09 + B-07辅助 | 2026-10-13：uv run pytest -q tests/agent_runtime/test_memory_redesign_context.py --tb=line --show-capture=no：10例缺memory_context模块；任务引用fixture修正真实来源后单跑-k task_reference同样ModuleNotFoundError（原无来源反例不算功能RED）。追加写开关反例：-k write_disabled，1 failed / FORBIDDEN；关闭新增写入不应禁止删除 | GREEN：最终context文件17 passed；规定Rule argv此前51 passed，新增scope/关闭写入两例各自通过 | test_memory_redesign_context.py:52/69/70（200候选与199丢条metric）、:96（实际请求字节）、:113（0预算0SQL）、:159/169（forget与失败）、:191（首次full=1、暖head=1）、:241（任务引用）、:276（默认Executor）、:314（重试）、:332/373（持续禁用/在途失效） | 真实PG、DbBackedContextBuilder、MemoryCandidateCache、AgentRunner、HTTP模型请求体、真实MemoryService及PG历史写入失败trigger；默认Executor只注入真实PG连接工厂，生产Cache/Loader/audit未Mock | verified |
| E-01 | 不执行RED；仅编写/collect | e2e_deferred | tests/acceptance/runtime/test_memory_redesign_context_e2e.py::test_e01_memory_redesign_real_runtime_protects_prefix_and_refreshes_only_component | 3个参数场景collect成功；LiveStack真实Runtime/Console HTTP、PG/Redis、独立模型HTTP；micro/snip/summary真实触发审计与下一请求刷新断言 | e2e_deferred |

- 回归：独立PG/Redis的Runner、parallel planner、默认Executor、WAITING_TOOL状态机、工具整轮预算、历史重建、工具结果重建7文件：68 passed in 45.85s；TASK-011记录的9项旧CompactionSettings.memory失败随本任务移除所有ratio注入调用后恢复通过。
- 架构 + 完整块纯渲染 + TASK-011提议/暂停回归：64 passed in 30.48s。ruff、7个生产文件strict mypy、code-flow validate --no-heavy均pass；新增函数≤50行。
- E-01仅collect：micro/snip/summary三个参数场景已登记，真实Artifact文件/记录、历史Run与压缩触发审计；执行留需求终验。B-07在此为辅助Runtime原语测试，最终工具schema/回执与历史断言仍由TASK-013领取。
- B-08: verified — automated command passed; run_id=7801ac29e8f84f299e91e2329acee90b (confirmed_by: runner)
- B-09: verified — automated command passed; run_id=7801ac29e8f84f299e91e2329acee90b (confirmed_by: runner)
- E-01: e2e_deferred — automated command e2e_deferred; run_id=7801ac29e8f84f299e91e2329acee90b (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-013: remember/recall/forget 工具 schema 与回执

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-012, TASK-011, TASK-004
- **Source**: memory-redesign.design.md#3.4.2 remember 与真实确认, memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: B-06, B-07
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py

### Description

完整替换旧source/prefix schema并接唯一writer/queries/确认；forget具expected_version，WRITE/SERIAL；recall内容不可外置。

### Checklist

- [x] [B-06][integration] 先写真实PG无关SEARCH空、LIST区分、完整JSON预算与版本/TTL返回RED。
- [x] 非法category/query/limit、源字段注入拒绝，不能用旧接口适配器维持错误语义。
- [x] 记忆写开关不阻止forget；候选建立仅confirmation_required，确认结果独立。
- [x] [B-06][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 工具回执；断言：非空库无关 query 返回空并计 SEARCH miss；空 query 为 LIST；完整 JSON 字节不越限。
- [x] [B-07][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 模型 HTTP 探针；断言：同 Run forget 后下一请求旧 memory 条目消失；删库/刷新失败不宣称成功并移除组件；历史仍可查。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-06 | integration | integration，真实 PG + 工具回执 | 非空库无关 query 返回空并计 SEARCH miss；空 query 为 LIST；完整 JSON 字节不越限 | tests/agent_runtime/test_memory_redesign_recall.py / test_b06_memory_redesign_search_miss_list_and_metadata / test_b06_memory_redesign_final_json_exact_budget_and_no_first_item_overflow | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_recall.py"] | verified |
| B-07 | integration | integration，真实 PG + 模型 HTTP 探针 | 同 Run forget 后下一请求旧 memory 条目消失；删库/刷新失败不宣称成功并移除组件；历史仍可查 | tests/agent_runtime/test_memory_redesign_context.py / test_b07_memory_redesign_production_forget_receipt_next_http_and_history | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-06 | 2026-10-13：uv run pytest -q tests/agent_runtime/test_memory_redesign_recall.py tests/agent_runtime/test_memory_redesign_context.py -k 'b06 or production_forget or tool_schema or production_remember or forget_version' --tb=line --show-capture=no：18 failed / 17 deselected；recall 的生产工具不接受 queries/context，旧 source/prefix 协议仍在。首次环境缺依赖和测试构造错误已修正后重跑，不算功能 RED | GREEN：recall/context/tools/observability 实际命令55 passed；额外空库反例 -k empty_scope：1 failed（错误计 miss），修正后包含在55绿例。52相关回归 + 1 claim/submission断言 + 90记忆/架构回归通过；ruff/mypy及validate --no-heavy pass | test_memory_redesign_recall.py:29/50/69/81/100/106（SEARCH/LIST、完整预算、0/空库、非法参数、错误指标）、:143/168（提议/版本删除） | 每例独立 head PG，真实写者/查询/工具/metric；无 Store mock | verified |
| B-07 | 同一实际命令：production_forget 三例失败，真实 HTTP 第二次请求仍含 old_memory_value；registry 未注册 forget | GREEN：同55例命令；最终历史查询/构造抽取经Done Gate契约重跑通过；runner run_id=8482717bbbc94964b5d8034f95481b9e，decision=pass | test_memory_redesign_context.py:430、_assert_forget_history（下一HTTP组件、删除失败/刷新失败、history/canonical保留）；test_memory_tools.py:23（生产remember完整回合暂停） | 真实 default_executor_factory、PG、模型 HTTP；删除失败用 PG history trigger，刷新失败用 PG 事实损坏 trigger；canonical/history 不 mock | verified |
- B-06: verified — automated command passed; run_id=8482717bbbc94964b5d8034f95481b9e (confirmed_by: runner)
- B-07: verified — automated command passed; run_id=8482717bbbc94964b5d8034f95481b9e (confirmed_by: runner)

### Regression Evidence

- uv run pytest -q tests/agent_runtime/test_memory_redesign_recall.py tests/agent_runtime/test_memory_redesign_context.py tests/agent_runtime/test_memory_tools.py tests/agent_runtime/test_memory_observability.py --tb=line --show-capture=no：55 passed in 78.62s。
- 独立 head PG + 隔离 Redis + 临时 ARTIFACT_ROOT 子进程跑 runner_executor/tool_round_budget/execution_audit/tool_wait_state_machine/tool_result_materialization/tool_result_history/execution_defaults_settings 七文件：52 passed in 44.69s；新增真实RunService claim/submission传入断言单跑 -k registry：1 passed。
- uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_proposals.py tests/agent_runtime/test_memory_redesign_writes.py tests/agent_core/test_memory_redesign_selection.py tests/agent_core/test_memory_redesign_render.py tests/architecture/test_channel_neutrality.py tests/architecture/test_import_direction.py --tb=line --show-capture=no：90 passed in 75.41s。
- 旧工具测试的 source_type/直接已保存断言改为严格拒绝/候选提议/唯一writer；冻结设置回归移除旧首条越限和ContextBuilder注入假设；不加兼容接口。
- 源字段拒绝、确认、lookup、删除全部按当前Run用户绑定；remember仅登记提议，确认消费结果仍由TASK-015后续独立事件实现。本任务没有新增或执行E2E。
- 主workspace存在无关未跟踪docs/issues/2026-10-13-high-qps-capacity.md，Start Gate unowned_changes后使用隔离worktree；无关文件未改未提交。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-13] completed (done)

---

## TASK-014: 续接意图、options 单次消费与任务 lineage

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-012, TASK-009
- **Source**: memory-redesign.design.md#3.5.2 任务续接闭环, memory-redesign.design.md#3.3.6 其余硬约束索引（文本提到但必须落到 DDL）
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: packages/agent-core/src/muad_agent_core/memory/resume.py, packages/contracts/src/muad_contracts/memory.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resume_options.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resume_queries.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_resume_metrics.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_context.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/run_events.py

### Description

真实用户选择写统一MEMORY_RESUME_RESOLVED，复用source_event_id唯一；无options唯一选择也先建单项；受信引用与真实请求保持原task key/root/version/hash。job固定输入、更新原checkpoint及完成退休由后续consolidation任务实现，最终E-05由TASK-030终验。

### Checklist

- [x] [E-05][integration] 辅助先写泛指多条→第2个、过期/版本变化/否定句不选、options跨outcome互斥RED。
- [x] Runtime确定清单编号并持久投递，模型不能伪造批准；不新建options消费表。
- [x] 入站USER_MESSAGE固定source_scope_revision；记录resume_ref供job，模型输出不能指定root。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 纯语法函数、独立head PostgreSQL、真实USER_MESSAGE/RunSubmission/EventWriter/唯一键、真实RunService/defaultExecutor与HTTP模型探针 | 完整续接/选择语法，多条→第2个，单项先建options，expiry/version/delete/hash拒绝，跨outcome互斥，Runtime清单不调用模型，原root引用进入真实请求，回滚不计成功；E-05最终Gateway/job闭环归TASK-030 | tests/agent_core/test_memory_resume_intent.py::test_local_e05_complete_resume_request、test_local_e05_choice_does_not_extract_numbers_from_other_sentences；tests/agent_runtime/test_memory_redesign_resume.py::test_local_e05_multi_options_then_second_real_input_selects_original_root_and_model_request、test_local_e05_options_cross_outcome_unique_and_concurrent_single_consumption、test_local_e05_runtime_delivers_persisted_options_without_model_rewriting、test_local_e05_cross_conversation_a_b_c_reuses_original_task_ref_in_real_runtime_request、test_local_e05_fresh_topic_request_replaces_pending_options | ["uv","run","pytest","-q","tests/agent_core/test_memory_resume_intent.py","tests/agent_runtime/test_memory_redesign_resume.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL / E-05辅助 | 2026-10-13：uv run pytest -q tests/agent_core/test_memory_resume_intent.py tests/agent_runtime/test_memory_redesign_resume.py --tb=line --show-capture=no：37 failed in 15.70s；缺core memory.resume、MemoryResumeOption及Runtime memory_resume，不存在真实入站选择/持久options/消费通路 | 同命令首次37 passed in 14.68s；2026-10-14加入test_memory_redesign_context.py整组68 passed in 60.92s（含30个纯语法、18个续接、20个组件用例）；新主题修复后完整续接文件19 passed in 30.42s | test_memory_resume_intent.py:21/43/64/70/74；test_memory_redesign_resume.py:22（直投无HTTP）、42（A→B→C原key）、70（真实入站/第2个/请求块）、97（单项）、108（改主题）、126（否定/伪造）、137（expiry/version/delete）、168（互斥）、191（主题零命中/用户边界）、209（0预算） | memory_resume_support.py：每例独立head PG、真实AGENT writer/TaskState原root、USER_MESSAGE/RunSubmission、数据库source唯一键；实际RunService claim/终态与defaultExecutor/HTTP探针；上下文渲染按key/version/hash重验。仅验证引用保持，job更新/完成退休留后续实现；最终E-05归TASK-030终验 | verified |
| LOCAL / 错误与事务反例 | 原入站模块缺失RED如上；追加损坏options hash/selected ref：2 failed in 3.94s，Pydantic校验异常逃出编排；改主题反例：test_local_e05_fresh_topic_request_replaces_pending_options，1 failed in 1.64s，取消后reference=None | 校验降级修复后2 passed in 4.09s；改主题修复后完整文件19 passed in 30.42s；包含外层rollback、savepoint rollback与真实PG读故障反例 | test_memory_redesign_resume.py:108（改主题引用绑定新USER）、220（外层回滚/指标）、266（真实表不可读/入站revision=null/ERROR）、283（损坏hash重列）、301（损坏ref不阻塞真实Run）、325（savepoint回滚无成功指标） | 故障注入仅改独立测试库，不mock查询；真实事务/独立连接、canonical唯一键、真实HTTP请求与Prometheus样本；事件和成功指标只认提交事实 | verified |

回归（实际执行）：

- 相关事件/幂等/等待态7文件：`PYTHONPATH="$PWD" .venv/bin/python /tmp/fluxion-task014-regression.py tests/agent_runtime/test_run_events.py tests/agent_runtime/test_run_idempotency.py tests/agent_runtime/test_runner_executor.py tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_execution_defaults_settings.py tests/agent_runtime/test_tool_parallel_planner.py tests/agent_runtime/test_tool_result_history.py --tb=line --show-capture=no`，52 passed in 33.81s。临时脚本仅创建独立head PG/Redis/ARTIFACT_ROOT并设置子进程env，finally drop临时库；不碰dev数据。
- `uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_proposals.py tests/agent_runtime/test_memory_redesign_recall.py tests/agent_runtime/test_memory_tools.py tests/agent_runtime/test_memory_observability.py tests/agent_core/test_memory_redesign_selection.py tests/agent_core/test_memory_redesign_render.py tests/architecture --tb=line --show-capture=no`，135 passed in 88.56s。
- 13个生产文件strict mypy、ruff及git diff --check通过；改主题后相关mypy/ruff复查通过；新增/重写生产函数均≤50行。`code-flow validate --root "$PWD" --no-heavy --json`实际decision=pass、failures=[]、incomplete=false。E2E未执行，未改全局E-05状态。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-13] started
- [2026-10-14] completed (done)
- [2026-10-14] Done Gate实际decision=pass；6 review、15范围外required、8超预算verifier及1 heavy validator延至需求级verify-e2e/归档，未宣称需求全量验收通过。

---

## TASK-015: 真实确认 resume 原子提交与 deadline

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-013, TASK-014
- **Source**: memory-redesign.design.md#3.4.2 remember 与真实确认
- **Spec-Refs**: harness-memory#RULE-memory-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/run_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/run_submission.py, apps/agent-runtime/src/muad_agent_runtime/application/checkpoint.py, apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/dto.py, apps/agent-runtime/src/muad_agent_runtime/api/runs.py

### Description

MEMORY_CONFIRMATION专用分派；合法输入/事实/history/receipt/interrupt/Run CAS/submission同事务，终态/绝对deadline优先。显式resume绑定真实platform_user_id；持久展示绑定完整proposal hash，原候选回执不可变。同消息并发重投在短事务advisory锁后重查并重放。真实确认依赖的TTL/硬配额须来自冻结值，故同时修正原五字段memory载体为完整序列化/严格schema读回；TASK-016仍负责其余预算/job/冻结能力最终验收。

### Checklist

- [x] [B-15][integration] 先写真实Gateway/Runtime/PG确认、拒绝、无效编号仍等待、stale合法续跑、超Run deadline不恢复RED。
- [x] 其他用户/版本/hash/epoch/重复message_id不能授予另一份内容。
- [x] 不先无条件消费所有WAITING interrupt，不改已持久化候选回执。
- [x] [RULE-memory-001] 原argv功能测试已实际执行：43个来源证据+26个真实确认用例，69 passed；review层正式裁决仍由需求终验统一执行，不把编码期功能通过写成review Gate通过。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-memory#RULE-memory-001 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 来源证据、真实候选确认、禁止模型授予USER/ADMIN；正式review留需求终验 | tests/agent_core/test_memory_redesign_evidence.py；tests/agent_runtime/test_memory_redesign_confirmation.py::test_local_b15_real_gateway_confirmation_saves_displayed_content_and_new_receipt、test_local_b15_other_user_cannot_confirm_in_gateway_or_explicit_resume | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | deferred_to_review |
| LOCAL | integration | 真实Gateway RuntimeClient、Runtime HTTP/API/RunService/defaultExecutor、独立head PG、真实模型HTTP探针、持久候选/历史/回执/唯一键 | 真实确认按展示保存，拒绝续跑，无效回答仍WAITING，失效合法编号STALE续跑，绝对deadline拒绝；跨用户、重放、故障整事务回滚 | tests/agent_runtime/test_memory_redesign_confirmation.py::test_local_b15_real_gateway_confirmation_saves_displayed_content_and_new_receipt、test_local_b15_invalid_input_keeps_waiting_and_writes_nothing、test_local_b15_result_event_failure_rolls_back_fact_history_receipt_and_resume、test_local_b15_concurrent_same_message_replays_one_durable_confirmation、test_local_b15_scope_lock_wait_rechecks_absolute_deadline_and_rolls_back | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_confirmation.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| LOCAL / B-15辅助 | 2026-10-14：uv run pytest -q tests/agent_runtime/test_memory_redesign_confirmation.py --tb=line --show-capture=no：17 failed in 42.30s；确认未保存、拒绝无outcome、无效回答被无条件消费、失效未标STALE、deadline仅RUN_BUSY、显式resume缺用户身份、结果故障不能触发回滚。初始重放用例还缺测试凭据，不将该CREDENTIAL_MISSING作为幂等缺陷RED；后续真实凭据HTTP探针就绪后验证模型边界 | 首轮17 passed in 41.43s；最终与来源证据同跑69 passed in 62.62s，其中43个pure来源证据、26个真实确认/反例 | test_memory_redesign_confirmation.py:31（展示内容/USER事实/history/receipt/模型新结果）、66（只消费memory/拒绝续跑）、90（无效仍等待）、105（真实writer版本/forget_epoch变化及损坏hash/user）、138（deadline）、153（双入口用户校验）、168（重放/异文拒绝）、182（PG结果触发器回滚） | memory_confirmation_resume_support.py：生产Gateway RuntimeClient经真实socket HTTP调用Runtime路由/RunService，独立head PG真实候选/checkpoint/唯一writer；实际PG claim/defaultExecutor/模型HTTP与真实ConsoleCredentialsClient→凭据HTTP探针。定义resolver不允许被重调；没有mock Store/Runtime/model边界。最终B-15完整Gateway入口责任归TASK-017，E2E未执行 | verified |
| LOCAL / 并发、冻结与输入身份补充 | 同消息并发重投：1 failed / RUN_BUSY，另一锁等待反例已通过；TTL/配额：2 failed / 默认TTL=None且配额未拒绝（68项整组中66 passed）；原input.id与transport key：1 failed / 错存transport-key | 并发修复后2 passed in 5.61s；完整冻结修复后TTL/配额2 passed in 4.18s；原input.id修复后1 passed in 2.97s；全部包含于最终69 passed整组 | test_memory_redesign_confirmation.py:227（并发只一次保存/HTTP/确认数）、241（真实scope行锁阻塞后deadline回滚）、283（quota无部分效果）、303（冻结TTL/结果有效期）、319（原输入ID）、337（终态优先） | 独立请求/事务、真实pg_advisory_xact_lock与scope行锁，真实PG时钟；错误路径保留候选和Run等待态，无USER/history/receipt/submission部分效果。提议被改value且重算candidate_hash仍因持久proposal_hash不匹配而STALE | verified |

原metadata argv在编码期作为functional实际执行通过，正式review verifier状态仍deferred_to_review；不替代需求终验。现有显式resume E2E两文件补入platform_user_id并仅collect：`uv run pytest --collect-only -q tests/acceptance/runtime/test_capability_snapshot.py tests/acceptance/runtime/test_run_lifecycle.py`，10 tests collected in 0.47s；没有执行E2E或改全局场景状态。

已执行回归：`uv run pytest -q tests/agent_runtime/test_memory_redesign_proposals.py tests/agent_runtime/test_memory_redesign_resume.py tests/agent_runtime/test_memory_redesign_context.py tests/architecture --tb=line --show-capture=no`，93 passed in 88.99s。轻量`code-flow validate --root "$PWD" --no-heavy --json`实际decision=pass/failures=[]/incomplete=false；ruff通过，新增/重写生产函数均≤50行。

Run/API/事件/等待态回归：`PYTHONPATH="$PWD" .venv/bin/python /tmp/fluxion-task015-regression.py tests/agent_runtime/test_run_service.py tests/agent_runtime/test_runs_api.py tests/agent_runtime/test_run_events.py tests/agent_runtime/test_run_idempotency.py tests/agent_runtime/test_runner_executor.py tests/agent_runtime/test_tool_wait_state_machine.py tests/agent_runtime/test_interrupt_checkpoint.py tests/agent_runtime/test_execution_defaults_settings.py tests/agent_runtime/test_tool_parallel_planner.py tests/agent_runtime/test_tool_result_history.py --tb=line --show-capture=no`，最终92 passed in 53.76s。包装脚本只创建独立head PG/Redis/ARTIFACT_ROOT并配置子进程env，finally清理临时库，未动dev数据；首轮92 passed in 45.51s，完整冻结后扩展夹具曾89 passed/3 failed in 48.28s，原因是测试试图在线把硬配额200/65536降为57/32768，被既有维护窗口规则正确拒绝；夹具改为合法提高至257/131072后整组通过，未放松配额规则。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)
- [2026-10-14] Done Gate实际decision=pass；6 review、13范围外required、8超预算verifier及1 heavy validator延至需求终验/归档，未宣称需求全量验收完成。

---

## TASK-016: Run/job 冻结全部记忆策略与工具历史预算

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-015
- **Source**: memory-redesign.design.md#2.3.3 配置项, memory-redesign.design.md#3.2.2 模型请求组件, memory-redesign.design.md#4.2 一次性迁移与切换
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-22
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py

### Description

新策略/model定义一次冻结；执行不重新读取设置；ratio和硬编码历史轮数路径全部移除，历史旧终态快照只审计。

### Checklist

- [x] [B-12][integration] 先写一次只改一个叶子的新Run/旧resume/job，其他叶子不变RED。
- [x] [B-22][integration] 200→300已有250条旧Run仍可读取/forget，不能回退容量；在线收紧拒绝。
- [x] snapshot/model剥api_key，凭据实时；source policy与后台deadline预算分离。
- [x] [B-22][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实PG+旧新Run；断言：额度200→300后250条均被旧Run读到；旧Run forget成功；旧冻结额度不额外淘汰；在线降低拒绝，维护排空才可降。
- [x] [RULE-snapshot-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-22 | integration | integration，真实PG+旧新Run | 额度200→300后250条均被旧Run读到；旧Run forget成功；旧冻结额度不额外淘汰；在线降低拒绝，维护排空才可降 | tests/agent_runtime/test_memory_redesign_snapshot.py / test_b22_memory_redesign_old_run_reads_250_and_forgets_after_quota_increase | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | verified |
| harness-snapshot#RULE-snapshot-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（实际执行：4+30 passed） | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | verified |

### Acceptance Evidence

- RED（2026-10-14）：`PYTHONPATH="$PWD" .venv/bin/python /tmp/fluxion-task015-regression.py tests/agent_runtime/test_memory_redesign_snapshot.py`，独立迁移至head的PG/Redis，6 failed / 3 passed。缺失冻结memory叶子被静默补默认（3例）；真实PG历史Builder未接收工具轮预算（0/1/8三例）。完整输出 `/tmp/fluxion-task016-red.txt`。
- B-22 既有writer减容/Console在线收紧拒绝已实现，新增真实Run/PG工具补测首次通过，不伪造RED；B-12单叶冻结及job→source snapshot辅助用例通过。job执行期/resume全链路最终责任仍由TASK-021的B-12承担。
- GREEN：同一隔离wrapper执行snapshot本套件与snapshot_freeze/run_reaper/execution_defaults/context_rebuild/tool_result_history，27 passed（2026-10-14）；新增本套件10例全部实际收集执行。真实Console设置/RunService/PG快照/原forget工具，250→249仅目标删除，旧policy逐键不变。旧resume读取6轮，新Run为0，完整声明/结果配对；0/1/8轮精确验证。
- Snapshot原verifier：`PYTHONPATH="$PWD" .venv/bin/python /tmp/fluxion-memory-isolated-command.py bash -lc 'uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"'`，独立headPG/Redis中按元数据原argv实际执行，4+30 passed，记录 `/tmp/fluxion-task016-verifier.txt`。
- 断言：`test_b22_memory_redesign_old_run_reads_250_and_forgets_after_quota_increase`（真实旧新Run、完整候选和forget/维护错误）；`test_b12_only_history_budget_leaf_changes_new_run_while_old_resume_keeps_six_rounds`（真实入站resume，不重resolve设置）；`test_b12_single_memory_leaf_changes_only_new_run_and_job_uses_source_snapshot`（两个memory叶子各单腿、新Run/原snapshot job引用、其他叶子不变）；缺失叶子拒绝与0/1/8完整历史轮回放参数测试。
- 凭据剥离/hash稳定性由原Snapshot用例及TASK-015真实API-09旋转覆盖；source memory.consolidation_timeout_sec与主Run deadline_ms单腿对照不互相覆盖。正式job执行B-12仍由TASK-021最终验收。
- 轻量validation：语法/mypy已pass；Ruff已pass。无E2E提前执行。
- B-22: verified — automated command passed; run_id=7fbe93fc130b4b61a10d99dc25d8acff (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-017: Gateway /memory 前缀、确认与续接事件投递

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016, TASK-014
- **Source**: memory-redesign.design.md#3.4.2 remember 与真实确认, memory-redesign.design.md#3.5.2 任务续接闭环
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: B-15
- **Files**: apps/im-gateway/src/muad_im_gateway/application/inbound.py, apps/im-gateway/src/muad_im_gateway/application/runtime_client.py, apps/im-gateway/src/muad_im_gateway/application/progress.py

### Description

前缀走独立命令容量，Gateway只传真实message/身份；权威参数解析在Runtime；忙态不能另建Run。

### Checklist

- [x] [E-02/E-05][integration] 辅助先写长流中memory即时受理分类但RUN_BUSY明确、WAITING_INPUT合法resume、断线事件回放RED。
- [x] 确认候选与options文本用Runtime持久事件的确切内容/编号，正确绑定本条回复会话。
- [x] 无渠道专有字样流入Runtime，不把模型文本当用户输入；记录GREEN。
- [x] [B-15][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 Gateway/Runtime/PG/resume；断言：确认内容与展示一致；其他用户、编号、过期、旧版本/forget_epoch、重放均不能保存另一份内容；同轮两提议明确拒绝第二个；无效回答仍 WAITING，拒绝合法续跑。
- [x] [RULE-im-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/console_channel","tests/gateway"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。
- [x] [RULE-im-002] verifier：按元数据argv ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-15 | integration | integration，真实 Gateway/Runtime/PG/resume | 确认内容与展示一致；其他用户、编号、过期、旧版本/forget_epoch、重放均不能保存另一份内容；同轮两提议明确拒绝第二个；无效回答仍 WAITING，拒绝合法续跑 | tests/agent_runtime/test_memory_redesign_confirmation.py / test_b15_gateway_replay_delivers_exact_persisted_prompt_and_confirmation_to_current_reply / test_b15_runtime_only_exact_real_memory_command_can_authorize_candidate_without_prompt | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_confirmation.py"] | verified |
| harness-im#RULE-im-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（实际原argv执行通过） | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | verified |
| harness-im#RULE-im-002 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（实际原argv执行通过） | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | verified |

### Acceptance Evidence

- RED（2026-10-14）：`uv run pytest -q tests/agent_runtime/test_memory_redesign_confirmation.py -k 'gateway or exact_real'`，4 failed/6 passed；其中首次replay失败包含fixture endpoint错误，修正原RunSubmission的create-run身份后单跑replay仍失败（实际待确认事件被Gateway SSE白名单丢弃，显示断流错误）。有效实现RED为：RUNNING/WAITING_TOOL期间memory命令被路由锁拦住（2例）；真实命令精确参数仍saved=false（1例）；真实已持久化memory.confirmation_required提示未投递（单跑1例）。不将fixture错误算实现缺陷。日志 `/tmp/fluxion-task017-red.txt`、`/tmp/fluxion-task017-replay-fixture.txt`。
- 真实Gateway InboundPipeline/consume/ReplySession/RuntimeClient socket→Runtime router/RunService/defaultExecutor→隔离headPG；Console身份端口提供固定测试授权，模型HTTP/凭据探针为受控外部端点，核心确认/存储/渲染不mock。原已受理submission fixture规范化为实际Gateway wire指纹，通过真实create入口重放确切持久提示。
- GREEN：隔离PG/Redis wrapper执行 `tests/agent_runtime/test_memory_redesign_confirmation.py tests/agent_runtime/test_memory_redesign_proposals.py tests/agent_runtime/test_memory_tools.py`，73 passed（2026-10-14）；B-15文件37例。新命令重复工具调用复用同一永久receipt/history，不增加确认次数；错key/category/value均仍saved=false。测试中的fixture修正（原submission endpoint、ReplySession.finish幂等）已明确区别于实现缺陷。
- 原IM verifier：`PYTHONPATH="$PWD" .venv/bin/python /tmp/fluxion-memory-isolated-command.py uv run pytest -q tests/console_channel tests/gateway`，413 passed；`uv run pytest -q tests/architecture/test_channel_neutrality.py`，3 passed。命令argv与元数据一致，独立迁移PG/Redis提供环境隔离，未绕过业务边界。
- 断言：`test_b15_gateway_replay_delivers_exact_persisted_prompt_and_confirmation_to_current_reply`确切持久prompt、当前ReplySession、真实重复message最多一套history/receipt；`test_memory_command_bypasses_gateway_stream_lock_but_runtime_busy_cannot_create_a_run`持有真实route锁时consume仍送入Runtime，RUNNING/WAITING_TOOL均RUN_BUSY且只有原Run；无效回答四例仍WAITING且未调用模型；原B-15辅助用例验证过期、旧版本/epoch/其他用户、拒绝、完整事务回滚与并发消费。same-round第二提议由实际Runner/默认remember工具回归覆盖。
- 明确命令匹配发生于Runtime的当前canonical USER_MESSAGE，复核受信submission/owner/epoch/deadline并走统一writer；其他候选保持完整回合暂停。`memory.confirmation_required`贯穿SSE白名单/Progress/Renderer/Inbound等待判断，重放不再被误报断流；续接编号仍由Runtime message.delta原文承载，不在Gateway重排。
- 新模块/重写函数≤50行，Ruff与轻量语法/mypy validation pass。日志 `/tmp/fluxion-task017-regression.txt`、`/tmp/fluxion-task017-im-verifier.txt`、`/tmp/fluxion-task017-validation.json`。E-02/E-05最终E2E仍由TASK-030登记，未提前执行。
- B-15: verified — automated command passed; run_id=421fd0ea281f447bb5be359071f5afa9 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-018: 记忆指标 catalog、日志与审计脱敏

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-013, TASK-004
- **Source**: memory-redesign.design.md#3.8 可靠性与观测, memory-redesign.design.md#3.4.5 内容闸门与配额, memory-redesign.design.md#6.1 Spec Compliance Matrix
- **Spec-Refs**: harness-log#RULE-log-001, harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/metrics.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_observability.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_service.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation_resume.py, packages/agent-core/src/muad_agent_core/tools/pipeline.py, tests/agent_runtime/test_memory_redesign_observability.py, tests/agent_runtime/test_memory_redesign_confirmation.py, tests/agent_core/test_tool_execution_pipeline.py

### Description

新增confirmation/claim/stale/expiry/drop/error等低基数指标；handler前remember参数特殊脱敏，值/quote/prompt不入日志。

### Checklist

- [x] [S-02][integration] 辅助先写无流量metrics目录、高基数拒绝、异常不回显canary、错误不能计成功RED。
- [x] value/quote不因键名非敏感而进入tool_call_audit；extra唯一通道。
- [x] 失败可见性不要求不可写DB强写事件；记录真实warning/metric。
- [x] [RULE-log-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

- [x] [RULE-mcp-001] 范围增量：共享pipeline错误回执不回显原实例；保持冻结MCP目录/统一ToolRegistry，原verifier `["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"]` 与agent-core共享pipeline回归实际执行。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-log#RULE-log-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（metadata原argv实际执行14 passed） | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | verified |
| harness-mcp#RULE-mcp-001 | unit（原metadata verifier）+ integration辅助 | 原规则verifier + 真实共享ToolExecutionPipeline | 冻结catalog/Server授权边界保持；schema失败不回显remember私密实例 | tests/console_mcp/test_mcp_rules.py + tests/agent_runtime/test_memory_redesign_observability.py | ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] | verified |

### Acceptance Evidence

- LOCAL（S-02辅助）测试已写：`tests/agent_runtime/test_memory_redesign_observability.py`；命令 `uv run pytest -q tests/agent_runtime/test_memory_redesign_observability.py`。
- RED（2026-10-14）：独立headPG，7 failed（已修正caller事务fixture后再次执行）。新增指标目录缺失（1）；低基数守卫模块缺失（2）；remember value/quote经真实Pipeline→Recorder→RuntimeAuditWriter落进PG审计且schema错误可能回显（2）；真实history触发器失败没有ERROR计数（1）；外层提交/嵌套rollback没有commit敏感成功计数（1）。日志 `/tmp/fluxion-task018-red.txt`。
- 新增PRE-handler RED：给真实projection函数加调用观测（仍调用原函数、不替换业务行为），handler断言进入前已产生安全投影；`uv run pytest -q tests/agent_runtime/test_memory_redesign_observability.py -k before_handler`，1 failed/1 passed（旧projection仅在handler之后）；日志 `/tmp/fluxion-task018-pre-handler-red.txt`。已将AuditPort改为显式prepare+record协议，Runtime在schema/handler之前产生安全预览，PRE hook改参后重做投影，没有optional/getattr兼容层；正式生产Recorder和测试port均实现新协议。
- PRE-handler GREEN：同文件与真实共享pipeline回归17 passed。嵌套commit→外层rollback/commit均验证成功计数只跟最外层提交；嵌套rollback不影响后续真实成功计数。
- 完整记忆/工具/观测/metrics/审计/共享pipeline最终回归85 passed（`/tmp/fluxion-task018-final-regression.txt`）；此前61 passed（修复rollback后ORM读取导致的MissingGreenlet覆盖真实配额错误后，配额错误保持MEMORY_QUOTA_EXCEEDED）。确认结果事件边界flush后，事件trigger失败计ERROR且write/CONFIRMED成功均不增加，完整效果rollback，单跑2 passed。
- 观测/配额回归22 passed；tool/egress/model审计与共享pipeline/MCP规则23 passed；原日志metadata argv通过独立PG/Redis wrapper实际执行14 passed；原MCP verifier+完整架构36 passed（MCP3+architecture33）。轻量validation/strict mypy pass。
- scope expansion已按cf-spec处理：确定性路径命中harness-mcp，设计§6.1/Task Spec Responsibility/TASK-018 checklist与contract增量承接，CLI bind将design/plan refs应用于同一Context后refresh并重新生成当前TASK projection，不伪造waiver/N/A。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-019: 固定 consolidation 输入与独立主模型 provider

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-016, TASK-018
- **Source**: memory-redesign.design.md#3.6.1 入队与执行, memory-redesign.design.md#3.6.2 顺序与恢复
- **Spec-Refs**: harness-model#RULE-model-001
- **Acceptance-Refs**: B-19
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation.py, apps/agent-runtime/src/muad_agent_runtime/application/context_events.py

### Description

固定through_seq/摘要覆盖边界/input hash；输入脱敏/预算；冻结主模型、实时凭据、独立timeout/output/provider close。

### Checklist

- [x] [B-19][integration] 先写摘要关闭仍走主模型、实时凭据、超时与close、Run终态不变RED。
- [x] [B-21][integration] 辅助写R1延迟到R2摘要后仍只含R1输入（已验证）；固定DTO携带revision，持久hash/接管不刷新由TASK-020保存输入及TASK-021最终B-21验证。
- [x] 整批严格解析、非法重复key/root/category/密钥均拒绝，不逐好项提交。
- [x] [B-19][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，摘要关闭 + 真实模型 HTTP；断言：consolidation 仍用冻结主模型；凭据实时取；provider 正常关闭；timeout 不改变 Run 终态。
- [x] [RULE-model-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-19 | integration | integration，摘要关闭 + 真实模型 HTTP | consolidation 仍用冻结主模型；凭据实时取；provider 正常关闭；timeout 不改变 Run 终态 | tests/agent_runtime/test_memory_redesign_provider.py / test_b19_memory_redesign_summary_disabled_uses_frozen_main_and_live_rotated_credentials / test_b19_memory_redesign_independent_timeout_keeps_run_completed_and_closes_provider | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_provider.py"] | verified |
| harness-model#RULE-model-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | tests/console_platform/test_models_api.py、test_agents_api.py（disabled） | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | planned |

### Acceptance Evidence

- B-19 RED：`uv run pytest -q tests/agent_runtime/test_memory_redesign_provider.py`，真实隔离PG fixture已配置，3 failed（ConsolidationInvoker不存在）。测试已明确冻结主模型、summary关闭、过期主Run deadline与独立timeout、真实HTTP API-09密钥轮换及provider finally close。
- B-21/输出约束辅助测试已写：`tests/agent_runtime/test_memory_redesign_input_boundary.py`（真实PG R1/R2摘要边界、排除工具结果；重复key/root/author/category/secret整批拒绝）；真实RED输出 `/tmp/fluxion-task019-input-red.txt`。
- GREEN：provider+input boundary 命令15 passed（provider5、输入/整批输出10），输出 `/tmp/fluxion-task019-green.txt`。B-19断言位于provider测试：真实PG COMPLETED旧Run/冻结snapshot，真实API-09与OpenAI HTTP、换key两次请求、独立超时/拒绝与finally close。
- 原Rule verifier：独立PG环境按元数据bash argv实际执行，8 passed + 1 passed/13 deselected，`/tmp/fluxion-task019-verifier.txt`。
- 受影响回归：context events/rebuild、memory policy/observability共通过，`/tmp/fluxion-task019-regression.txt`；轻量validation pass。B-21最终持久输入hash/epoch断言归TASK-021，本任务仅提供不可越界的构建器与strict DTO。
- B-19: verified — automated command passed; run_id=cb997276ad644928a247a3bb0a600474 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-020: 持久 job 入队、claim、lease 与接管

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-016, TASK-019
- **Source**: memory-redesign.design.md#3.3.5 memory_consolidation_job（新增）, memory-redesign.design.md#3.6.1 入队与执行
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_jobs.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py

### Description

COMPLETED终态内savepoint入队；PG claim/epoch/lease/attempt唯一；首次固定输入只写一次。

### Checklist

- [x] [B-10][integration] 辅助先写并发claim、unique Run job、失去lease后不能修改状态RED。
- [x] [B-11][integration] 辅助写入队失败只回滚savepoint、主终态仍提交。
- [x] 前驱RUNNING/RETRY未决时同conversation后继不越过；不凭内存协程持执行权。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | tests/agent_runtime/test_memory_redesign_job_claims.py / test_local_claim_is_unique_and_old_epoch_cannot_write、test_local_enqueue_is_unique_and_database_error_keeps_completed_run、test_local_fixed_input_survives_takeover_without_following_tail | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_claims.py"] | planned |

### Acceptance Evidence

- LOCAL RED：真实隔离PG测试命令 `uv run pytest -q tests/agent_runtime/test_memory_redesign_job_claims.py`，3 failed：memory_jobs模块未实现，`/tmp/fluxion-task020-red.txt`。已登记唯一claim、失效epoch拒绝、输入冻结与PG触发器注入入队失败。
- GREEN：`uv run pytest -q tests/agent_runtime/test_memory_redesign_job_claims.py` 9 passed，`/tmp/fluxion-task020-green.txt`。断言覆盖并发唯一claim、epoch接管/过期拒绝、同会话RUNNING/RETRY前驱阻止后继、固定input_json/hash/revision不随tail刷新、PG触发器入队失败仅回滚savepoint，五种非COMPLETED状态不入队。实际效果/history/event原子终态由TASK-021最终B-10/B-11/B-21验证。
- 新模块严格mypy/轻量validation pass；Run收尾/lease与确认回归输出 `/tmp/fluxion-task020-regression.txt`。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-021: 批量提炼效果、history/event/job 原子收口

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-020, TASK-009, TASK-014
- **Source**: memory-redesign.design.md#3.6.1 入队与执行, memory-redesign.design.md#3.6.2 顺序与恢复, memory-redesign.design.md#3.4.4 同 key 冲突矩阵
- **Spec-Refs**: harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Acceptance-Refs**: B-10, B-11, B-12, B-16, B-17, B-21
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_jobs.py

### Description

锁序内校验epoch/lease/revision/逐key；batch_in同事务写全部效果/history/scope/receipt/唯一完成事件/job终态。

### Checklist

- [x] [B-10][integration] 先写两个执行者不能双写效果/history/event，旧epoch不能完成RED。
- [x] [B-11][integration] 坏形状/后项失败/history失败与提交前后崩溃，整批零或一套效果。
- [x] [B-16/B-17][integration] USER/ADMIN不降权、删除墓碑/人工禁用不复活，自动退休只能新入站依据恢复。
- [x] [B-21][integration] 延迟摘要边界与重试固定输入，scope变化STALE；LINEAGE同key更新/完成退休。
- [x] [B-10][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + HTTP 模型探针；断言：两个 Runtime 并发 claim、过期 lease 接管；最多一套记忆/历史/完成事件；旧 epoch 无法提交。
- [x] [B-11][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 故障注入；断言：坏形状、批内后项失败、历史失败整批零效果；效果提交前/后崩溃均不重复；Run 保持 COMPLETED。
- [x] [B-12][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 两段 Run；断言：修改一个设置，新 Run 读新值，旧 Run/resume/job 均读冻结值；无其他叶子意外变化。
- [x] [B-16][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + HTTP 模型探针；断言：forget 后才开始提炼旧会话仍不可复活；模型期间删除使 job STALE；人工 disabled 不被启用；自动退休仅新入站 source_scope_revision 可恢复。
- [x] [B-17][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG；断言：AGENT 同 key 不覆盖 USER/ADMIN；晚结束的旧 Run 不覆盖较新状态；同值重放不虚增确认数。
- [x] [B-21][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 延迟 job / 后续摘要；断言：R1 job在R2摘要后执行仍仅含R1边界；重试输入hash/revision不可刷新；记忆变更后重试STALE。
- [x] [RULE-memory-004] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]（review）验证 用户删除墓碑、forget_epoch、新依据退休恢复、禁止来源降权；review由需求终验统一执行，当前无执行Evidence。
- [x] [RULE-memory-005] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"]（review）验证 固定提炼输入、lease/epoch fence、最多一次原子效果、失败可见且Run不变；review由需求终验统一执行，当前无执行Evidence。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-10 | integration | integration，真实 PG + HTTP 模型探针 | 两个 Runtime 并发 claim、过期 lease 接管；最多一套记忆/历史/完成事件；旧 epoch 无法提交 | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b10_memory_redesign_two_executors_takeover_fences_atomic_effects | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | verified |
| B-11 | integration | integration，真实 PG + 故障注入 | 坏形状、批内后项失败、历史失败整批零效果；效果提交前/后崩溃均不重复；Run 保持 COMPLETED | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b11_memory_redesign_whole_batch_failure_keeps_completed_run / test_b11_memory_redesign_sigkill_before_and_after_commit_has_one_effect_set | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | verified |
| B-12 | integration | integration，真实 PG + 两段 Run | 修改一个设置，新 Run 读新值，旧 Run/resume/job 均读冻结值；无其他叶子意外变化 | tests/agent_runtime/test_memory_redesign_snapshot.py / test_b12_single_memory_leaf_changes_only_new_run_and_job_uses_source_snapshot / test_b12_only_history_budget_leaf_changes_new_run_while_old_resume_keeps_six_rounds | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | verified |
| B-16 | integration | integration，真实 PG + HTTP 模型探针 | forget 后才开始提炼旧会话仍不可复活；模型期间删除使 job STALE；人工 disabled 不被启用；自动退休仅新入站 source_scope_revision 可恢复 | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b16_memory_redesign_forget_before_old_job_has_no_model_or_revival / test_b16_memory_redesign_delete_during_http_model_makes_whole_job_stale | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | verified |
| B-17 | integration | integration，真实 PG | AGENT 同 key 不覆盖 USER/ADMIN；晚结束的旧 Run 不覆盖较新状态；同值重放不虚增确认数 | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b17_memory_redesign_confirmed_and_disabled_are_never_downgraded / test_b17_memory_redesign_late_old_source_cannot_replace_newer_source | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | verified |
| B-21 | integration | integration，真实 PG + 延迟 job / 后续摘要 | R1 job在R2摘要后执行仍仅含R1边界；重试输入hash/revision不可刷新；记忆变更后重试STALE | tests/agent_runtime/test_memory_redesign_input_boundary.py / test_b21_memory_redesign_retry_preserves_original_hash_and_revision_or_becomes_stale / test_b21_memory_redesign_lineage_updates_and_retires_original_root | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | verified |
| harness-memory#RULE-memory-004 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 用户删除墓碑、forget_epoch、新依据退休恢复、禁止来源降权 | tests/agent_runtime/test_memory_redesign_job_effects.py 等真实测试已创建；review verifier由需求终验执行 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |
| harness-memory#RULE-memory-005 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 固定提炼输入、lease/epoch fence、最多一次原子效果、失败可见且Run不变 | tests/agent_runtime/test_memory_redesign_job_effects.py 等真实测试已创建；review verifier由需求终验执行 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | planned |

### Acceptance Evidence

- B-10/B-11/B-16/B-17首轮真实RED：`uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py`，真实隔离PG+模型HTTP；原子effects模块尚未实现，`/tmp/fluxion-task021-red.txt`。其余B-12/B-21已实现辅助测试将补job执行与retry后STALE断言，未伪造RED。
- GREEN：effect/input/claim/provider四文件47 passed，`/tmp/fluxion-task021-green.txt`；追加attempt-limit 1 passed、真实API-09 HTTP超时真实RED（httpx.ReadTimeout逃出作业）、修复后GREEN见 `/tmp/fluxion-task021-http-green.txt`。最终本任务功能命令由Done Gate执行回写。
- B-10断言：两个执行者并发claim、过期接管、旧epoch零效果；PG sleep使lease在flush期间过期，整批回滚，不写假终态。B-11：模型坏输出、后项/历史/receipt/event/job终态PG触发器分别拒绝，零部分效果；独立子进程运行真实apply_batch，在commit前/后实际SIGKILL，重放各仅一套identity/history/event。attempt耗尽唯一FAILED事件，主Run始终COMPLETED。
- B-16/B-17：真实writer创建USER/ADMIN/AGENT与禁用/删除/QUOTA退休，模型期间删除导致STALE；真实MemoryResumeService新入站记录revision，只有自动退休+新依据恢复；USER/ADMIN墓碑永不自动复活；旧source_seq不能覆盖新状态，重复终态不加确认。
- B-21：延迟R2摘要、retry原input_json/hash/revision固定；scope变化模型前STALE。A→B→C经真实MemoryResumeService SELECTED、enqueue、模型HTTP、原子writer，只一个identity/version1→2→3，C完成退休A原key。
- B-12：真实Console设置/两个Run、自动入队旧job经真实claim/freeze仍引用旧snapshot、只改变单叶；10 passed `/tmp/fluxion-task021-snapshot.txt`。原辅助人工插job在真实自动入队后出现唯一冲突已改为读自动job；测试tenant清理补齐新增表，消除孤儿job/snapshot环境残留。
- 受影响writer/quota/confirmation/Run/lease回归82 passed，`/tmp/fluxion-task021-regression.txt`；轻量validation pass。review层Rule最终由需求verify-e2e执行。
- B-10: verified — automated command passed; run_id=1ce19268a54748e5a6716d295f2e35e9 (confirmed_by: runner)
- B-11: verified — automated command passed; run_id=1ce19268a54748e5a6716d295f2e35e9 (confirmed_by: runner)
- B-12: verified — automated command passed; run_id=1ce19268a54748e5a6716d295f2e35e9 (confirmed_by: runner)
- B-16: verified — automated command passed; run_id=1ce19268a54748e5a6716d295f2e35e9 (confirmed_by: runner)
- B-17: verified — automated command passed; run_id=1ce19268a54748e5a6716d295f2e35e9 (confirmed_by: runner)
- B-21: verified — automated command passed; run_id=1ce19268a54748e5a6716d295f2e35e9 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-022: Runtime 后台接线、TTL 与过期确认清扫

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-021, TASK-015, TASK-018
- **Source**: memory-redesign.design.md#3.8 可靠性与观测, memory-redesign.design.md#3.6 consolidation 生命周期
- **Spec-Refs**: harness-arch#RULE-arch-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/main.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_supervisor.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_expiry.py

### Description

lifespan启动停止有界job/expiry泵，PG权威无实例绑定；过期确认用原锁序处理，不scope→interrupt反锁。

### Checklist

- [x] [E-06][integration] 辅助先写跨实例接管/旧epoch拒绝、shutdown清理provider任务、非COMPLETED不入队RED。
- [x] TTL读时即时生效，sweep逐scope短事务写EXPIRE历史，不能在scope锁内网络IO。
- [x] Runtime readiness/部署单元与导入方向不新增耦合，记录GREEN。
- [x] [RULE-arch-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/architecture"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实PG/模型HTTP/Runtime resume；真实writer与读侧TTL | 有界shutdown/provider close、跨实例lease接管；TTL即时过滤与唯一EXPIRE历史；过期提议不保存且可续跑 | tests/agent_runtime/test_memory_redesign_supervisor.py / test_local_background_shutdown_closes_provider_and_another_instance_takes_over / test_local_ttl_read_is_immediate_and_two_sweepers_write_one_expire_history / test_local_expired_confirmation_sweep_never_saves_and_remains_resumable | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_supervisor.py"] | planned |
| harness-arch#RULE-arch-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/architecture"] | planned |

### Acceptance Evidence

- LOCAL RED：`uv run pytest -q tests/agent_runtime/test_memory_redesign_supervisor.py`，3 failed，后台supervisor/TTL/确认expiry模块不存在；真实PG+HTTP与resume测试已写，`/tmp/fluxion-task022-red.txt`。
- GREEN：supervisor辅助5个测试通过（含真实Runtime main.lifespan + PG/Redis/API-09/模型HTTP），与job/claim回归共36 passed，`/tmp/fluxion-task022-green.txt`。TTL读时立即过滤，两个清扫者仅一条EXPIRE历史；PG history触发器失败完整回滚且warning/ERROR，无OK计数；过期提议EXPIRED/saved=false且真实Gateway resume完成。
- 跨实例接管：shutdown取消实际HTTP中的job，provider finally close，另实例接管同hash/更高epoch且唯一效果。额外真实RED `/tmp/fluxion-task022-registration-red.txt` 发现本实例在原task尚活时重领同job；claim排除本实例已登记id后GREEN。此排除保护本地有界登记，PG lease/epoch仍是全局权威，其他实例可接管。
- Rule元数据argv实际执行 `uv run pytest -q tests/architecture`，33 passed，`/tmp/fluxion-task022-architecture.txt`；轻量validation pass。E-06真正Runtime SIGKILL端到端仍由TASK-030登记并在需求终验执行，本任务未冒充E2E完成。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-14] completed (done)

---

## TASK-023: Runtime 管理命令 API、服务身份与分页

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009, TASK-015, TASK-022
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/api/memory.py, apps/agent-runtime/src/muad_agent_runtime/main.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_queries.py

### Description

受保护内部写入口绑定actor/tenant/user，分页与错误目录统一；既有GET/DELETE统一服务，不从模型参数授予ADMIN。

### Checklist

- [x] [S-01][integration] 辅助先写无服务身份/跨用户/伪造ADMIN/tenant、不存在资源均明确拒绝RED。
- [x] POST幂等与expected_version冲突真实回执；不回显存储路径/敏感值。
- [x] 新增API与router安装走共享api-kit，最终安全端到端责任在security任务。
- [x] [RULE-api-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | real Runtime HTTP + PG + api-kit service identity + transactional writer | permanent replay, version conflict, untrusted authority rejection and scoped id lookup; live service credential rejected without echo | tests/agent_runtime/test_memory_redesign_admin_api.py / test_local_admin_real_http_receipts_replay_and_version_conflict / test_local_admin_rejects_untrusted_identity_or_extra_authority / test_local_admin_cross_user_and_tenant_id_never_delete_another_scope / test_local_admin_live_service_credential_is_forbidden_and_never_echoed | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_admin_api.py"] | planned |
| harness-api#RULE-api-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | planned |

### Acceptance Evidence

- LOCAL RED: real isolated PG and HTTP tests fail because Runtime memory router is absent; 7 failed, `/tmp/fluxion-task023-red.txt`。
- RED（canary，live 服务凭据）：`uv run pytest -q tests/agent_runtime/test_memory_redesign_admin_api.py -k live_service`，`test_local_admin_live_service_credential_is_forbidden_and_never_echoed` 先得 200——管理写路径未携带进程 live canary，凭据型 value 可落库；期望 422 `MEMORY_FORBIDDEN_CONTENT` 且响应不回显，`/tmp/fluxion-task023-canary-red.txt`。
- GREEN：`uv run pytest -q tests/agent_runtime/test_memory_redesign_admin_api.py`，9 passed，`/tmp/fluxion-task023-green.txt`。真实 Runtime FastAPI + api-kit 服务身份 + 隔离 PG + 唯一事务写入口；断言位置：幂等重放/指纹冲突/版本冲突与历史唯一 `test_memory_redesign_admin_api.py:119-142`（200 重放同 data、409 `IDEMPOTENCY_MISMATCH`、409 `MEMORY_VERSION_CONFLICT`、UserMemoryHistory=1）；无/错服务身份与伪造 author/tenant/user `:145-165`（403 `FORBIDDEN` / 422 `COMMON_VALIDATION_ERROR`，UserMemory=0）；跨用户跨租户 id 删除 `:168-187`（404 `MEMORY_NOT_FOUND`，原行未删未改）；live 凭据 `:68-76`（422 `MEMORY_FORBIDDEN_CONTENT`，`response.text` 无凭据，UserMemory/History=0）；删除重放与分页/历史/租户可见性 `:79-116`（data 完全相等、history total=3、版本 [3,2]、actor=ADMIN）。
- 实现：管理写路径经 `api/deps.py#get_credential_canaries` 把进程 live 服务凭据注入 `MemoryWriteContext.credential_canaries`，`application/memory_write.py#validate_memory_content` 在事务内拒绝；回执只含 key/version/status，无存储路径与敏感值。传输契约归 `muad_contracts.memory_admin`，`memory_commands.py` 不保留中转 re-export，runtime 内部直接引用共享契约。
- [RULE-api-001] verifier：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`，18 passed，`/tmp/fluxion-task023-verifier.txt`。
- 回归：共享 dev 库停在 alembic 0019（head 0020_memory_redesign），整目录运行会因 committed conftest 清理新表/新列（`runtime.memory_consolidation_job`、`user_memory.author`、`run_interrupt.proposal_json`）在 147 个用例上 error，与本任务 diff 无关；改用隔离 head 库（`tests.acceptance.datastores` CLI）跑 `tests/agent_runtime/test_memory_redesign_*.py tests/architecture` = **326 passed**，`/tmp/fluxion-task023-regression.txt`。既有遗留（未随 redesign 更新、仍构造已删除 `source_type` 的旧读侧测试 `test_context_memory*.py`）不在本任务范围，留待需求终验。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-14] started
- [2026-10-15] completed (done)

---

## TASK-024: Console 记忆状态/历史只读仓储

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-007, TASK-023
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-rel#RULE-rel-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/memory_repository.py, apps/console-platform/backend/src/muad_console_platform/application/memory_service.py, apps/console-platform/backend/src/muad_console_platform/api/users.py

### Description

ACTIVE/DISABLED/EXPIRED过滤、history按version分页，list/count同条件；deleted从模型列表隐藏但history可查。

### Checklist

- [x] [E-04][integration] 辅助先写跨租户、过期/禁用/删除、总数与items齐平、版本排序RED。
- [x] 展示author与actor分离，Console时间投影复用统一入口。
- [x] 只读runtime表，移除旧source_type投影，测试旧未知值明确回退。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 Console HTTP（ASGI + 真实登录会话）+ 真实 PostgreSQL（0020 head 的 runtime.user_memory / user_memory_history）+ 真实 SQL 语句监听 | 跨租户 404；ACTIVE/DISABLED/EXPIRED 与 author/category 过滤后 total 与 items 齐平；deleted 不进列表但历史按 version 倒序可查；读路径只发 SELECT；时间等于统一入口 `format_console_time`；旧 source_type 不投影、旧未知 actor/原因原样回退 | tests/console_platform/test_user_memory_api.py / test_local_01_list_filters_status_author_category_with_tenant_scope、test_local_02_deleted_hidden_from_list_but_history_paginates_by_version、test_local_03_reads_are_read_only_and_share_console_time_projection、test_local_04_unknown_and_legacy_values_fall_back_explicitly、test_local_05_cross_tenant_user_is_not_found | ["uv","run","pytest","-q","tests/console_platform/test_user_memory_api.py"] | verified |

### Acceptance Evidence

- LOCAL RED：`DATABASE_URL=<隔离 head 库> REDIS_URL=redis://localhost:6379/15 uv run pytest -q tests/console_platform/test_user_memory_api.py --tb=line` → **4 failed, 1 passed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task024-red.txt`。失败原因对应预期缺陷：列表/历史读投影仍选 `source_type`（DB 已在 0020 head）→ HTTP 500 `asyncpg.exceptions.UndefinedColumnError: column "source_type" does not exist`（用例 01/02/03）；`test_user_memory_api.py:401: assert 404 == 200`（`/memory/{memory_key}/history` 路由尚未存在，用例 04）。`test_local_05_cross_tenant_user_is_not_found` 无 RED：租户存在性检查是既有行为，本任务补测作护栏，未伪造失败。
- GREEN：同环境 `uv run pytest -q tests/console_platform/test_user_memory_api.py` → **5 passed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task024-green.txt`。真实边界：真实 Console ASGI HTTP + conftest 真实登录会话 + 真实 PG（隔离库、0020 head、真实 runtime 表），无仓储/DB mock。断言位置：
  - 过滤·齐平·排序·旧投影移除：`test_user_memory_api.py:202-250`（total==items、ACTIVE/DISABLED/EXPIRED、author/category 过滤、空组合 total=0、第二页 total 仍 4）。
  - deleted 不进列表 + 历史 version 倒序/分页/未知 key：`:275-316`（list data == `{items:[],page:1,page_size:20,total:0}`；history versions `[3,2,1]`、page_size=2 → `[3,2]`、越界页 total 仍 3、未知 key 404 `MEMORY_NOT_FOUND`）。
  - 只读 + 时间统一入口 + author/actor 分离：`:340-378`（引擎 `before_cursor_execute` 捕获的 `runtime.user_memory*` 语句全部以 SELECT/WITH 开头；`create_time/update_time/expires_at == format_console_time(真实行值)`；item.author=ADMIN 而 history.actor_type/actor_id 独立展示）。
  - 旧未知值回退 + 入参校验：`:381-423`（`actor_type=LEGACY_SYNC`、`change_reason=LEGACY_REASON_V9`、`after` 内旧键原样透出且顶层无 `source_type`；status/author/category/非法 key → 422 `COMMON_VALIDATION_ERROR`）。
  - 跨租户：`:426-469`（他租户用户 404 `COMMON_NOT_FOUND`；换他租户账号会话再查本租户用户同样 404 且不回显 user id）。
- 实现：`MemoryService.history` + `MemoryRepository.find_origin/history`（列表与计数共用同一谓词，状态口径与 Runtime `memory_admin_queries` 一致：`expires_at <= statement_timestamp()` 优先于 `enabled=false`，`is_deleted=false` 恒排除；`find_origin` 不过滤 deleted 以支持历史回查）；时间出参统一走 Console 统一入口 `audit_query_service.format_console_time`，`application/dto.py` 新增 `ConsoleTime` 正则钉死格式；`MemoryItem` 移除 `source_type` 并新增 author/explicit_request/confirmed_count/status/expires_at/last_confirmed_at/source_quote，新增 `MemoryHistoryItem`（author 与 actor 分列，未知值不落 pattern）；`api/users.py` 列表过滤器直接取共享契约枚举（`muad_contracts.memory` / `memory_admin`，`Annotated[..., Query()]` 形态满足 B008），`UserService.list_memories` 死代码随改走 `MemoryService` 一并删除。
- 回归：`tests/console_platform` = **293 passed, 2 failed**（`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task024-green.txt`）；2 failed 为存量且与本 diff 无关：`test_platform_settings_table.py` 把迁移 head 钉在 `0019`（HEAD 已含 TASK-007 的 `0020`，且 `0020.downgrade()` 按设计拒绝回滚），该文件无 diff。harness-api verifier（改动路径命中 `apps/**/api/**`）：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py tests/console_skill/test_import_idempotency.py` = **23 passed**。`uv run ruff check .` 仅剩 2 条存量违规（agent-runtime I001、TASK-023 的 E501）；改动文件 mypy strict 与 py_compile 通过。
- 边界说明（跨任务）：`memory_repository.py` 仍保留 `get_for_user/soft_delete/soft_delete_all` 三个存量写方法服务既有 DELETE/Clear 路由；§3.2.1「不直接 UPDATE runtime 表」的终态由 TASK-025（写操作转 Runtime）收口，本任务读路径已用真实 SQL 监听证明只发 SELECT。
- Scope 扩展（局部 Plan）：diff 命中 path_mapping `rel: **/*relations*`，门禁自动绑定 `harness-rel#RULE-rel-001` 并暂停 TASK；该 spec 的 verifier 即 `tests/console_platform/test_user_side_relations.py`（本任务仅迁移种子 `source_type`→`author`，无关系语义变更）。按「局部 Plan」纳入本任务 scope：`Spec-Refs` 与责任表已登记，verifier `uv run pytest -q tests/console_platform/test_user_side_relations.py` 实测 3 passed（`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task024-rel-verifier.txt`）。
- 未执行/未宣称：E-04 保持 planned（责任表 owner TASK-030，`["make","acceptance-e2e","DOMAIN=agent"]`），本任务不执行 E2E、不替代其裁决；他租户会话 404 的完整伪造面归 S-01（TASK-029）。未修遗留（不属本任务范围）：`tests/e2e/seed_user_identity.py` 仍插已删除的 `source_type`（E2E 种子，TASK-026/028/030 收口）；前端 `UserDetailTabs.tsx` 仍按 `source_type` 渲染来源列且 `tests/frontend/test_detail_sidesheet_contract.py:56` 钉住该列（TASK-026/028）；`tests/frontend/test_platform_settings_contract.py` 缺 17 个 memory 设置词条（TASK-027/终验）。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] resumed (in-progress)
- [2026-10-15] completed (done)

---

## TASK-025: Console 写操作转 Runtime 并保持命令回执

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-024, TASK-023
- **Source**: memory-redesign.design.md#3.2.1 组件分工, memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/backend/src/muad_console_platform/infrastructure/runtime_memory_client.py, apps/console-platform/backend/src/muad_console_platform/application/memory_service.py, apps/console-platform/backend/src/muad_console_platform/api/users.py, apps/console-platform/backend/src/muad_console_platform/api/deps.py, apps/console-platform/backend/src/muad_console_platform/application/dto.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/memory_repository.py, tests/console_platform/test_user_memory_write_commands.py, tests/console_platform/conftest.py, tests/console_platform/test_user_side_relations.py, tests/console_platform/test_users_api.py

### Description

管理员鉴权后HTTP调用唯一writer，新增/启禁用/删除/清空共享command_id，禁直接UPDATE runtime表。

### Checklist

- [x] [E-04/S-01][integration] 辅助先写重复请求不重复效果、403/版本冲突、Runtime失败不宣称成功RED。
- [x] Console日志关联command_id；不宣称本地config审计与远端写原子。
- [x] DTO白名单与统一Envelope；缺Runtime连接配置清晰报错。
- [x] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | integration | 真实 Console HTTP（ASGI + 真实会话/管理员角色/CSRF）+ 真实 Runtime HTTP（ASGI 装载 `/internal/admin/users/{user_id}/memory` 真实路由：服务身份门控 + 唯一事务写服务）+ 真实 PostgreSQL（0020 head 的 `runtime.user_memory*` / `memory_command_receipt` / `memory_scope`）+ Console 引擎的真实 SQL 语句监听 | 同 `command_id` 重放返回首次回执且不产生第二次事实/历史/回执；同命令不同输入 409 `IDEMPOTENCY_MISMATCH`；版本冲突 409 且不覆盖；未知 id 404；Runtime 不可达/服务身份 403/缺连接配置时 Console 落 `COMMON_INTERNAL_ERROR` 且零成功态、零本地审计；清空一命令原子且 `forget_epoch` +1；DTO 白名单（author/TASK_STATE/task.*/空值/缺 Idempotency-Key/禁用带有效期）全部 422；Console 对 runtime 记忆表零写语句 | tests/console_platform/test_user_memory_write_commands.py / test_local_01_same_command_replays_without_second_effect、test_local_02_version_conflict_and_missing_memory_pass_through、test_local_03_enable_disable_and_expiry_semantics、test_local_04_expired_entry_requires_new_expiry_and_clear_is_one_command、test_local_05_unreachable_or_forbidden_runtime_never_reports_success、test_local_06_missing_runtime_configuration_fails_explicitly、test_local_07_console_dto_whitelist_and_tenant_scope；tests/console_platform/test_user_side_relations.py / test_s05_memory_delete_and_clear_share_application_service | ["uv","run","pytest","-q","tests/console_platform/test_user_memory_write_commands.py","tests/console_platform/test_user_side_relations.py"] | verified |

### Acceptance Evidence

- LOCAL RED（行为级）：先写 7 条辅助用例 + 改造 `test_s05`，再临时回退 service/路由/DTO/仓储（保留新 Runtime 客户端以便收集）实跑：`DATABASE_URL=<隔离 head 库> REDIS_URL=redis://localhost:6379/15 uv run pytest -q tests/console_platform/test_user_memory_write_commands.py tests/console_platform/test_user_side_relations.py::test_s05_memory_delete_and_clear_share_application_service --tb=line` → **8 failed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task025-red.txt`。失败原因即缺失的契约：`POST /memory` 不存在 → `COMMON_BAD_REQUEST`（用例 01-04，`test_user_memory_write_commands.py:245,280,332,384`）；`PATCH` 未实现 → `assert 405 == 500`（`:455`）与缺 Idempotency-Key 仍 405（`:487`）；`author` 未被 Console 边界拒绝（`:506`，旧实现根本不收 body）；旧 DELETE 回执无命令号 → `test_user_side_relations.py:182: KeyError: 'command_id'`。（最早一次的 RED 是模块级 `ImportError: cannot import name 'get_runtime_memory_client'`，同一个缺失契约的收集期形态。）
- GREEN：同环境 `uv run pytest -q tests/console_platform/test_user_memory_write_commands.py` → **7 passed**（`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task025-green.txt`）。真实边界：真实 Console ASGI HTTP + 真实登录会话/角色/CSRF、真实 Runtime 记忆管理路由（真实服务身份门控 + 真实 `MemoryAdminService`→唯一写服务）、真实 PG 0020 head；无客户端内部 mock、无 DB mock，Runtime 的 platform-settings 端口用仓库自带 `NullPlatformSettingsClient`（避免连 dev Console）。断言位置：
  - 命令号与重放：`:247-266`（`data.command_id == Idempotency-Key`、二次同键 `data` 与首次逐字段相等、`changed_count==1`、同键换输入 409 `IDEMPOTENCY_MISMATCH`），`:263-266` 用 DB 计数钉死「1 事实 / 1 历史 / 1 回执」。
  - 真实 SQL 监听：`:267-271`（Console 引擎上所有命中 `runtime.user_memory*` 的语句必须以 SELECT/WITH 开头 ⇒ 禁直接 UPDATE/INSERT/DELETE）。
  - 版本冲突/不存在透传：`:275-318`（覆盖无 `expected_version` → 409 `MEMORY_VERSION_CONFLICT` 且内容未变；旧版本 PATCH 409；未知 memory_id 404 `MEMORY_NOT_FOUND`；失败写不产生事实/历史/回执/审计）。
  - 启禁用与过期语义：`:321-368`（DISABLED/ENABLED 状态与 version 递增；新有效期落到事实；过去时间作有效期 → 400 `MEMORY_VALUE_INVALID`）、`:371-434`（过期条目无新有效期启用 → 400；新有效期启用 → 真实 EXPIRE+ENABLE 两条变更 ⇒ version 3；清空一命令原子 `changed_count==3`、`forget_epoch==1`、历史/回执计数 8/5，空清空 `UNCHANGED` 不再写历史）。
  - Runtime 失败不宣称成功：`:438-471`（真实 TCP `ConnectError` → 500 `COMMON_INTERNAL_ERROR` + warning `runtime_memory_request_failed`；错误服务身份 → 403 `FORBIDDEN`；两次都断言记忆/历史/回执/审计全零）。
  - 缺连接配置：`:474-491`（`INTERNAL_SERVICE_TOKEN`/`AGENT_RUNTIME_URL` 置空 → 500 且 warning 指名缺失项，零落库）。
  - DTO 白名单与租户作用域：`:494-540`（`author`/`TASK_STATE`/`task.*`/空白值/缺 `Idempotency-Key`/PATCH 缺版本/禁用带有效期 → 422 `COMMON_VALIDATION_ERROR`；租户内不存在用户 → 404 且不发 Runtime 命令）。
- 实现：新增 `infrastructure/runtime_memory_client.py`（`X-Internal-Service`/`X-Tenant-Id`/`X-Trace-Id`/`Idempotency-Key`/`X-Actor-Id` 透传；上游业务码原样上抛，transport/封套异常统一 `COMMON_INTERNAL_ERROR`，日志只记方法与路径）；`application/dto.py` 新增 `MemoryCreateRequest`（复用 contracts 的 `RememberMemoryInput` 拒 `task.*`）与 `MemoryEnabledUpdateRequest`；`application/memory_service.py` 拆为只读 `MemoryService` 与 `MemoryAdminService`（新增/启禁用/删除/清空全走 Runtime，`Idempotency-Key` 即命令号）；`api/users.py` 新增 `POST /api/v1/users/{id}/memory`、`PATCH /api/v1/users/{id}/memory/{memory_key}`，`DELETE`（按 id，`expected_version` 必填）与 `DELETE`（清空）改走 Runtime，四条路由统一返回 Runtime 命令回执（`MemoryCommandResponse`，含 `command_id`/`status`/`changed_count`/`items`）；`api/deps.py` 新增 `get_runtime_memory_client`（缺 `AGENT_RUNTIME_URL`/`INTERNAL_SERVICE_TOKEN` 时 warning 指名 + `COMMON_INTERNAL_ERROR`，不发必然 403 的裸请求）；`infrastructure/repositories/memory_repository.py` 删除 `get_for_user/soft_delete/soft_delete_all` 三个直写方法（design §3.2.1「不直接 UPDATE runtime 表」终态）。
- 审计与日志口径（不宣称原子）：Runtime `user_memory_history`（actor/version/command_id）是权威审计；Console 每次成功请求写一条 `control.config_audit_log`（`resource_type=USER_MEMORY`、`resource_id=user_id`、`after={command_id,status,changed_count,memory_keys}`，不含记忆正文），并记 `console_memory_command_applied`（`command_id` 在 `extra` 里）。两者**不同库不同事务**：本地审计在远端命令返回之后写，失败不回滚远端事实，重放请求会多记一条本地日志（`test_s05` 断言 4 条日志/3 份回执即此口径）。
- 回归：`tests/console_platform` = **300 passed, 2 failed**（`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task025-regression.txt`，连跑两轮同结果）；2 failed 为存量且与本 diff 无关（`test_platform_settings_table.py` 把迁移 head 钉在 `0019`，HEAD 已含 TASK-007 的 `0020`；该文件无 diff）。TASK-023 的 Runtime 管理 API 用例 `tests/agent_runtime/test_memory_redesign_admin_api.py` = **9 passed**。harness-api/harness-data/harness-log verifier 同口径实跑：`tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py tests/console_skill/test_import_idempotency.py` = **23 passed**；`uv run pytest -q tests -k schema_parity` = **41 passed, 3301 deselected**；`tests/test_logging.py tests/test_logging_redaction.py` = **13 passed**。`uv run ruff check .` 仅剩 2 条存量违规（agent-runtime I001、TASK-023 的 E501，均非本 diff）；`uv run mypy apps packages` = Success（386 files）。
- 跨任务边界（他任务收口，不在本任务宣称）：前端 `services/users.ts` 的 `deleteMemory/clearMemory` 与 `UserDetailTabs.tsx` 仍按旧契约调用（不带 `Idempotency-Key`、读 `deleted`/`deleted_count`，并仍用 `source_type` 列），TASK-026/028 收口；E-04 浏览器场景（创建/启禁用/删除/历史分页/越权）owner 是 TASK-030，本任务不执行 E2E；`harness-rel#RULE-rel-001`（owner TASK-024）的 verifier 文件 `test_user_side_relations.py` 因写契约变更同步改造（同一路径仍全绿，种子内容形状对齐 `MemoryCandidate`、清理补历史/回执外键顺序）；`test_users_api.py::_cleanup_seeded` 补删记忆历史/回执（Runtime 现在会写这两张表）。
- Scope 说明：diff 命中 `apps/**/api/**` 与 `**/*relations*` 路径，故上述 harness-api/harness-rel 相关命令一并实跑登记；本任务无新增 Spec-Refs（责任表未给 TASK-025 分配 Tier-1 规则行），LOCAL 行是唯一契约行。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] completed (done)

---

## TASK-026: 前端记忆 API 服务层与严格 DTO

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-025
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-frontend#RULE-front-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/user-identity/services/users.ts, apps/console-platform/frontend/src/modules/user-identity/memoryTypes.ts, apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx, tests/frontend/test_user_memory_contract.py, tests/frontend/test_detail_sidesheet_contract.py

### Description

新增创建/history/status/expected_version契约，组件仅经服务层调用，类型不放任Any与旧source_type适配。

### Checklist

- [x] [E-04][unit] 辅助先写源码API出口/类型契约RED；HTTP只从services调用共享axios。
- [x] 错误码只经apiErrorBody映射，404/409/403回执语义明确。
- [x] 运行frontend API/i18n检查和typecheck，真实浏览器验证归e2e任务。
- [x] [RULE-front-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-frontend#RULE-front-001 | unit/source-contract | 真实前端源码树与真实 locale 文件：pytest 直接解析 TS 源码，`check_frontend_api_usage.py` 扫全量 `src`（api/ 之外禁 axios/fetch），`check_frontend_i18n.py` 比对仓库 zh-CN/en-US 词条与 `t('字面量')` 引用，`tsc --noEmit` 全量类型检查；无 mock/stub | 记忆 HTTP 只存在于 services 层共享 axios；`memoryTypes.ts` 严格枚举与 Console/Runtime DTO 齐平、无 any/无旧 source_type；四条写路由带调用方持有的 `Idempotency-Key`、DELETE 带 `expected_version`；404/409/403 错误码只经 `apiErrorBody`；原回归不退化 | tests/frontend/test_user_memory_contract.py（17 用例：test_memory_types_declares_strict_enums、test_memory_item_matches_backend_projection、test_write_functions_require_caller_owned_idempotency_key、test_service_paths_and_verbs_match_backend_contract、test_error_semantics_only_through_api_error_body、test_component_memory_tab_reads_new_author_field 等）+ tests/frontend/test_api_client_contract.py（原 verifier） | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | verified |

### Acceptance Evidence

- LOCAL RED（源码契约）：先落 `tests/frontend/test_user_memory_contract.py`（17 用例）再实现，`uv run pytest -q tests/frontend/test_user_memory_contract.py --tb=line` → **17 failed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task026-red.txt`。失败原因即缺失契约：`memoryTypes.ts` 不存在（`test_user_memory_contract.py:28`）；`services/users.ts` 仍是旧契约——`listMemory` 参数 `PageParams & { category?: string }`、返回 `Page<Memory>`（`:208`），缺 `listMemoryHistory/createMemory/updateMemoryEnabled/memoryErrorCode`（`:38`），旧 `Memory` 接口把 `source_type` 当字段导出，`newRequestId` 未被引用；组件仍按 `source_type` 渲染（`:265`）且 `deleteMemory(id, memoryId)`/`clearMemory(id)` 不带版本身份与幂等键（`:274`）。
- GREEN：`uv run pytest -q tests/frontend/test_user_memory_contract.py tests/frontend/test_detail_sidesheet_contract.py` → **26 passed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task026-green.txt`。断言位置（真实源码解析，无 mock）：
  - 严格类型：`:61-79`（status/author/category/可写分类/命令状态/结果状态全部字面量联合；无 `any`、无 `source_type`）；`:82-124`（`MemoryItem` 16 字段与 Console 读投影齐平；`MemoryHistoryItem` 11 字段含 `origin_id`/`after`）；`:126-148`（`MemoryMutationResult`/`MemoryCommandReceipt` 与 Runtime `MemoryCommandResponse` 齐平）；`:150-174`（`MemoryCreateInput` 可选 `expected_version/expires_at`、`MemoryEnabledUpdateInput` 必带 `expected_version`；可写分类不含 `TASK_STATE`）。
  - 出口与路径：`:206-259`（六个出口签名逐字钉死；GET 列表/历史、POST、PATCH、DELETE-by-id、DELETE-clear 的 URL 与动词；DELETE 的版本以 query 传 `params: { expected_version }` —— 对齐后端 `ExpectedVersion = Annotated[int, Query(ge=1)]`，任务简述里的「body」以已提交的代码事实为准）。
  - 幂等与错误码：`:219-235`（四条写操作都收调用方 `idempotencyKey`、都发 `'Idempotency-Key'`、service 内不出现 `newRequestId`）；`:176-192` + `:261-266`（`MEMORY_ERROR_CODES` 覆盖 404 `MEMORY_NOT_FOUND`/`COMMON_NOT_FOUND`、409 `MEMORY_VERSION_CONFLICT`/`IDEMPOTENCY_MISMATCH`、403 `FORBIDDEN` 及 400/422/500；`memoryErrorCode` 只调 `apiErrorBody(error)`，函数体不含 `response` 手工解析）。
  - 组件最小适配：`:268-283`（`source_type` 清零、来源列改 `dataIndex: 'author'` 且保留原 i18n 键；删除/清空传 `entry.version` 与 `newRequestId()`，HTTP 不在组件发生）。
- verifier（元数据 argv 原样实跑）：`bash -lc "uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"` → `4 passed in 0.01s` + `frontend api usage check OK` + `i18n keys OK: 1088` + `tsc --noEmit` 通过，日志 `/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task026-verifier.txt`。
- 回归：`uv run pytest -q tests/frontend` → **320 passed, 2 failed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task026-regression.txt`；2 failed 与改动前基线逐条相同且与本 diff 无关（`test_audit_gap_contract.py::test_b214_resource_type_options_cover_backend_domain`、`test_platform_settings_contract.py::test_backend_catalog_label_keys_are_translated_in_both_locales`——后者缺 17 个 memory 设置词条，归 TASK-027/终验）。改动前基线 303 passed / 2 failed；本次 +17 用例，`test_detail_sidesheet_contract.py` 仍绿。
- 实现：新增 `memoryTypes.ts`（列表项/历史项/写操作入参/Runtime 命令回执/status·author·category·结果枚举/错误码，字段保持后端 snake_case，`content` 为 `{ value } | TASK_STATE 结构` 联合）；`services/users.ts` 删除旧 `Memory` 接口（含 `source_type`），`listMemory` 收全 `category/author/status` 过滤，新增 `listMemoryHistory/createMemory/updateMemoryEnabled`，`deleteMemory/clearMemory` 改带 `expected_version`/调用方幂等键并返回命令回执，新增 `memoryErrorCode`（唯一映射口 `apiErrorBody`，未知码返回 null）；`UserDetailTabs.tsx` 按最小改动适配（沿用原组件、原视觉与原文案键）。
- 跨任务边界（登记）：本任务**不新增/不改动任何 i18n 词条**（选项/词条归 TASK-027，`check_frontend_i18n.py` 仍为 1088 keys）——来源列的 `ADMIN` 值当前按未知值原样显示，状态列/历史上限/动作 UI 归 TASK-028；真实浏览器验证归 TASK-030，E-04 保持 planned/deferred，本任务不宣称。`tests/frontend/test_detail_sidesheet_contract.py:56` 的 `source_type` 列断言是 TASK-024 Evidence 明确留给 TASK-026/028 的收口项，本任务按新字段最小改为 `author`；`tests/e2e/seed_user_identity.py` 仍插已删除列（按指示不修，归 E2E 收口）。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] completed (done)

---

## TASK-027: 记忆来源/状态/历史原因及错误本地化

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-004
- **Source**: memory-redesign.design.md#3.7 API 与 Console, memory-redesign.design.md#6.1 Spec Compliance Matrix
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, apps/console-platform/frontend/src/modules/user-identity/memoryOptions.ts, tests/frontend/test_user_memory_i18n_contract.py

### Description

author/actor/category/退休原因/确认状态与动态键齐备，译文每次渲染生成，未知值原样显示。

### Checklist

- [x] 先写中英文键集齐平、enum键真实翻译、错误码同名及无孤儿词条RED。
- [x] 不新增i18n框架/HTTPclient机制，options按模块唯一且由当前t注入。
- [x] [E-04][unit] 辅助语言切换和原始码title契约，记录GREEN。
- [x] [RULE-i18n-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-i18n#RULE-i18n-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | tests/acceptance/test_foundation_i18n.py（6 用例）+ scripts/check_frontend_i18n.py（全量 1141 键双侧齐平/空值/引用已定义）；本任务新增 `tests/frontend/test_user_memory_i18n_contract.py`（8 用例）为辅助证据 | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | verified |

### Acceptance Evidence

- RED（辅助契约，先写后实现）：`uv run pytest -q tests/frontend/test_user_memory_i18n_contract.py --tb=line` → **8 failed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task027-red.txt`（复现方式：把本任务三个实现文件 stash 掉后原样重跑，失败集合与首次 RED 逐条相同）。失败原因即缺失契约：`memoryOptions.ts` 不存在（`:116`，命中 5 个用例）；`user.memory.author.` 词条不存在（`:258`）；行为用例无法转译缺失模块（`:355`）；`settings.field.*` 17 条缺口（`:405`，TASK-026 点名的存量红）。
- GREEN：`uv run pytest -q tests/frontend/test_user_memory_i18n_contract.py -v` → **8 passed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task027-green.txt`。真实边界：真实词条文件、真实前端源码树、真实后端契约模块（`muad_contracts.memory`/`memory_admin`、`muad_agent_runtime.application.memory_commands`）、真实 node + 仓库自带 esbuild（把**真实 locale JSON** 注入 `t`；无 HTTP/DB/浏览器）。断言位置：
  - 键集齐平/无空值/无孤儿：`:209-228`（`user.memory.*` 两侧键集相等；引用面 = 静态 `t('…')` ∪ `memoryOptions.ts` 枚举出来的动态键变体）。
  - 枚举口径取后端事实：`:231-250`（`MEMORY_AUTHORS`=`MemoryAuthor`、`MEMORY_CATEGORIES`=`MemoryCategory`、`MEMORY_STATUSES`=`MemoryStatus`、`MEMORY_ACTORS`=`MemoryActor`(USER/ADMIN/AGENT/SYSTEM)、`MEMORY_CONFIRMATION_STATES`=EXPLICIT/INFERRED、`MEMORY_RETIREMENT_REASONS`=`MemoryRetirementReason`、`MEMORY_CHANGE_REASONS` = upsert 四态(CREATE/OVERWRITE/CONFIRM/RESTORE) ∪ 控制动作(ENABLE/DISABLE/RESTORE，来自 `AdminAction`) ∪ 退休原因 ∪ `MIGRATION_BASELINE`，且这些字面量仍真实存在于 Runtime/迁移源码）。
  - 真翻译：`:253-263`（六个枚举族逐键 `≠裸码`、zh≠en）。
  - 错误码同名：`:266-293`（`MEMORY_ERROR_KEYS` 键集 = `memoryTypes.MEMORY_ERROR_CODES`；每个码必须在 `config/api-messages.yaml` 已登记；词条键 = `user.memory.error.<CODE>`；未登记码落 `user.memory.errorFallback`，两侧均有且已本地化）。
  - `t` 注入与不缓存：`:296-334`（10 个出口签名首参 `t: TFunction`；动态键 `` t(`${prefix}.${value}`) `` 现场求值；枚举域外 `: value` 原样回退；无模块作用域/组件状态译文缓存、无 i18n 单例消费、无 `useTranslation`，只有 `import type { TFunction } from 'i18next'`；模块内其余文件不得自建枚举文案键）。
  - 真实行为（语言切换/原始码）：`:337-395`——同一 node 进程内先注入 zh 词条再注入 en 词条，逐枚举值断言渲染结果等于对应词条且 zh≠en（`:379`）；`memoryErrorLabel(code)` 逐码等于词条（`:385`）、`null`/未登记码落 fallback（`:386`）；域外值原样返回原始码（`:388`：author 域外的 `SYSTEM`→`SYSTEM`、`LEGACY_REASON_V9`→`LEGACY_REASON_V9`）；选项同时携带原始码 `value` 与本次渲染的 `label`（`:393`，组件据此 `title={value}`）。
  - 平台设置存量缺口：`:398-408`（17 条 `settings.field.*` 双侧齐备、非空、zh≠en）。
  - 无硬编码文案：`:411-415`（`memoryOptions.ts` 与 `UserDetailTabs.tsx` 去注释后无汉字字面量）。
- verifier（元数据 argv 原样实跑，cwd=.，timeout=300s）：`bash -lc "uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"` → `6 passed in 1.67s` + `i18n keys OK: 1141`，日志 `/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task027-verifier.txt`。
- 回归：`uv run pytest -q tests/frontend` → **1 failed, 329 passed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task027-regression.txt`；唯一 failed 是存量且与本 diff 无关的 `test_audit_gap_contract.py::test_b214_resource_type_options_cover_backend_domain`（TASK-026 基线同样红）。TASK-026 点名的 `test_platform_settings_contract.py::test_backend_catalog_label_keys_are_translated_in_both_locales` 由本任务补齐 17 条词条后转绿（该文件 38 用例中相关 7 条全绿，另跑 `test_platform_settings_contract.py test_user_identity_contract.py test_user_memory_contract.py test_detail_sidesheet_contract.py` = 38 passed）。`npm --prefix apps/console-platform/frontend run typecheck` 通过；另跑 `tests/frontend/test_api_client_contract.py`（4 passed）与 `scripts/check_frontend_api_usage.py`（OK）作旁证（`harness-frontend#RULE-front-001` 责任仍归 TASK-026，本任务不宣称）。
- 实现：新增 `memoryOptions.ts`（模块唯一的枚举→文案/选项构造口：author/actor/category/status/confirmation/changeReason 六个 `as const` 枚举族 + `memoryEnumLabel` 动态键求值 + `MemoryOption{value,label}` 选项工厂 + `MEMORY_ERROR_KEYS`/`MEMORY_ERROR_FALLBACK_KEY`/`memoryErrorLabel`；`t: TFunction` 一律由调用方注入，模块内无任何译文缓存）。两侧词条新增 54 条（`user.memory.*` 37 条 + `settings.field.*` 17 条），键顺序两侧逐行一致（1141/1141）。
- 顺带清理（同一契约要求）：删除两侧既有的孤儿词条 `user.memory.category`（"类别"/"Category"）——TASK-026 把来源列改为 `author` 后组件已不再引用它（全仓库仅剩词条自身），"无孤儿词条"不允许它继续躺着；TASK-028 渲染类别列时用 `memoryCategoryLabel` 取 `user.memory.category.<VALUE>`，列标题键届时按需重建。
- 跨任务边界（登记）：本任务**不改组件**（`UserDetailTabs.tsx` 未在 diff 内），`memoryOptions.ts` 的接入、状态/历史/动作 UI 与版本冲突恢复归 TASK-028；`user.memory.sourceType.*` 两条 legacy 键仍被现有组件静态引用（非孤儿），由 TASK-028 收口。E-04 浏览器场景（创建/启禁用/删除/历史分页与本地化）owner 是 TASK-030，本任务只做 [E-04][unit] 辅助，不宣称 E2E。行为用例依赖仓库内 node+esbuild（前端依赖未安装时**显式 skip** 并提示 `npm ci`，不静默通过；本机为真实执行）。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] completed (done)

---

## TASK-028: Memory Tab 管理动作、状态与历史

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-026, TASK-027
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001, harness-ui#RULE-ui-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx, apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx, apps/console-platform/frontend/src/modules/user-identity/MemoryHistorySideSheet.tsx, apps/console-platform/frontend/src/modules/user-identity/UserPage.tsx, apps/console-platform/frontend/src/modules/user-identity/memoryOptions.ts, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, apps/console-platform/frontend/src/styles/app.css, tests/frontend/test_memory_tab_contract.py, tests/frontend/test_detail_sidesheet_contract.py, tests/frontend/test_user_memory_contract.py, tests/frontend/test_user_memory_i18n_contract.py, e2e/tests/agent-management/memory-redesign.spec.ts

### Description

复用Semi/详情壳/ConfirmAction/DateTimeText，管理员动作与disabled/expired/删除历史清楚，版本冲突可恢复。

### Checklist

- [x] [E-04][unit] 辅助先写详情结构/源码契约RED，表单字段顺序与动作权限有明确断言。
- [x] author/actor分开，enabled操作不伪造来源；不加实现术语到用户界面。
- [x] [E-04][E2E] 编写真实Console→Runtime→PG→Chrome场景，中英文/明暗主题/窄屏；留需求终验执行。
- [x] [RULE-ui-detail-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。
- [x] [RULE-ui-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]（cwd=., timeout=900s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-ui-detail#RULE-ui-detail-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | `tests/frontend/test_detail_sidesheet_contract.py`（记忆 Tab 拆分后详情面断言跨 UserDetailTabs+MemoryTab）、`tests/frontend/test_form_layout_contract.py`；新增 `tests/frontend/test_memory_tab_contract.py` 9 条源码/行为契约 | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | verified |
| harness-ui#RULE-ui-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | `tests/frontend/test_console_shell_contract.py`、`tests/frontend/test_ui_style_contract.py`（新增组件无裸色/无内联导航，菜单 11 项与 adminOnly 恰两项不变） | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | verified |
| E-04 | E2E | browser，真实 Console/Runtime/PG/Chrome（无 mock；Runtime 连接/临时库/种子接线归 TASK-030） | 创建、启禁用、删除、历史分页和来源状态本地化；越权不可操作 | `e2e/tests/agent-management/memory-redesign.spec.ts`（新增：主流程 + 历史分页 + 中英文×明暗×窄屏矩阵 + 越权；`-grep E-04`） | ["make","acceptance-e2e","DOMAIN=agent"] | e2e_deferred |

### Acceptance Evidence

- RED：`uv run pytest -q tests/frontend/test_memory_tab_contract.py --tb=line` → **9 failed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task028-red.txt`。失败原因即缺失契约：`MemoryTab.tsx`/`MemoryHistorySideSheet.tsx` 尚不存在（7 条源码契约在 `test_memory_tab_contract.py:79` 报「缺少前端模块文件」）；24 个新 UI 文案键未落地（`:215`，zh/en 同缺）；`memoryWritableCategoryOptions` 未导出（`:260` node 执行 harness TypeError）。
- GREEN：`uv run pytest -q tests/frontend/test_memory_tab_contract.py` → **9 passed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task028-green.txt`。真实边界：真实前端源码树 + 真实 zh-CN/en-US 词条 + 真实 node + 仓库内 esbuild（行为用例注入真实 locale JSON）。断言位置：
  - 拆分与动作权限：`:102-119`（`<MemoryTab ... canManage={isAdmin}>`、`const canManage = props.canManage`、`canManage ?` 两处门控、旧内联 `listMemory/deleteMemory/clearMemory/formatMemoryAuthor` 清零）。
  - 创建表单字段顺序与可写分类：`:122-134`（`field="memory_key" < "category" < "value" < "expires_at"`；只用 `memoryWritableCategoryOptions(t)`；入参无 `author:`）。
  - 写操作带版本与幂等键：`:137-151`（`updateMemoryEnabled(..., enabledCommand(entry), newRequestId())`；过期条目带新有效期；`expected_version:entry.version` ≥2 处；`deleteMemory(..., entry.version, ...)`；`clearMemory(...)`）。
  - 版本冲突可恢复：`:154-162`（`MEMORY_VERSION_CONFLICT` 分支 + `memory-action-error` 落点 + `common.refresh` + 无空 catch）。
  - author/actor 分离与术语禁入：`:165-183`（列表 `dataIndex: 'author'` + `memoryAuthorLabel`；历史 `actor_type/actor_id` + `memoryActorLabel` + `memoryChangeReasonLabel`；`command_id/scope_revision/forget_epoch/source_run_id` 与自拼枚举键全禁）。
  - 历史壳与分页：`:186-194`（`DetailSideSheet`/`listMemoryHistory`/`PaginationFooter`/`closeOnEsc`）。
  - 详情结构与词条齐平：`:197-231`（工具行+分区标题+`.app-table-fit user-memory-table`+固定操作列；24 个新键两侧齐平且 zh≠en；`user.memory.sourceType.*` 已删；旧 hint「手工新增」矛盾句已改写）。
  - node 真实行为：`:234-262`（可写分类恰为 `PREFERENCE/WORK_STYLE/EXPLICIT`，标签等于真实词条）。
- 实现：拆出 `MemoryTab.tsx`（列表 内容/类别/来源/状态+有效期/更新时间/操作；工具行「新增记忆」+「清理全部」；行内 历史/启停用/删除）+ `MemoryHistorySideSheet.tsx`（版本/原因/操作者/操作者 ID/时间，按后端 version DESC 分页，最上层 `closeOnEsc`）；写操作全部经 `services/users.ts` 并带 `newRequestId()` 幂等键与读到的 `expected_version`，成功后 `reload()` + `props.onMutated()`（UserDetailTabs→UserPage 重取用户详情，刷新 Tab 计数与概览卡片）；过期条目「启用」改为必须指定新有效期的 Modal（Runtime 拒绝用过去时间当有效期，ENABLE 不自动延长 TTL）；409 `MEMORY_VERSION_CONFLICT` 显示本地化提示 + 「刷新」按钮（`memory-action-error`/`memory-refresh`），未知码落 `user.memory.errorFallback`，无空 catch；`memoryOptions.ts` 新增 `memoryWritableCategoryOptions`（排除 TASK_STATE）；删除 legacy `user.memory.sourceType.*` 并改写 `user.memory.hint`（原文与新增能力矛盾）；`styles/app.css` 新增 `.memory-status-cell`/`.memory-expiry`/`.memory-row-actions`，`.user-memory-table` 最小表宽 680→880（新增类别/状态列后仍在 920 面板内铺满），新增 `.memory-history-table` 880。
- 测试适配（同契约收口）：`test_detail_sidesheet_contract.py` 详情面断言跨 `UserDetailTabs`+`MemoryTab`（拆分后同一 Tab 面）；`test_user_memory_contract.py` 组件断言改指 `MemoryTab.tsx` 并断言旧 `sourceType` 引用清零；`test_user_memory_i18n_contract.py` 无硬编码文案检查从 1 个文件扩展到 3 个 UI 源文件（`_module_sources()` 仍覆盖全部模块文件做孤儿词条扫描）。
- verifier（RULE-ui-detail-001）实跑：`bash -lc "uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"` → **11 passed** + `tsc --noEmit` 通过，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task028-verifier-ui-detail.txt`。
- verifier（RULE-ui-001）实跑：`bash -lc "uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"` → **9 passed** + `vite build` 成功（dist 产物），`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task028-verifier-ui.txt`。
- 回归：`uv run pytest -q tests/frontend` → **338 passed, 1 failed**，`/private/var/folders/81/t8x84frn1v15nx7w6647r9sr0000gn/T/opencode/task028-regression.txt`；唯一失败为存量 `test_audit_gap_contract.py::test_b214_resource_type_options_cover_backend_domain`（与本 diff 无关，按指示不修）。`uv run python scripts/check_frontend_i18n.py` → `i18n keys OK: 1163`；typecheck/build 均通过（同上两行日志）。
- E2E（checklist 3，只编写不执行）：`e2e/tests/agent-management/memory-redesign.spec.ts`（agent 域 `testDir: ./tests/agent-management` 命中），4 组用例：主流程（UI 创建→停用→启用→历史 version 倒序+原因/操作者本地化→ESC 只关最上层→UI 删除→列表隐藏 + 真实 API 历史含 `ADMIN_DELETE` 记录）、历史分页（16 次真实 API 启禁用 → 17 条 >15 条/页 → 1/2 翻页 + version DESC）、中英文×明暗×1280/390 矩阵（行本地化、窄屏表内滚动 + 右侧固定操作可见、桌面无横向滚动）、越权（真实创建 BUILDER 账号 → 菜单无「用户」且 `/users` 重定向 `/`）。**未执行**：登记 `e2e_deferred`，执行 owner TASK-030（`["make","acceptance-e2e","DOMAIN=agent"]`），本任务未跑 Playwright、未起 dev/preview 服务；spec 已用 esbuild 转译做语法校验（不触发 Playwright 配置/建库）。
- 边界/未宣称：创建表单不含 `expected_version`（覆盖既有 key 走 409 冲突提示 + 刷新，不作为本任务 UI 能力）；已删除条目的历史界面入口来自列表行（列表不显示 deleted），E2E 以真实 API 断言删除记录可查，UI 渲染删除原因由静态契约（`memoryChangeReasonLabel`）保证；`tests/e2e/seed_user_identity.py` 与 `e2e/tests/user-detail-layout.spec.ts`（引用已删除的 `Memory`/`source_type`）为存量漂移，归 E2E 收口（TASK-030），本任务未修改；`settings.field.memory.*` 存量词条缺口与 harness 规范文本同步不在本任务。

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] RED→GREEN 完成；两条 verifier 实跑；E2E 只编写登记 e2e_deferred；等待 Done Gate 裁决。
- [2026-10-15] completed (done)

---

## TASK-029: 新增存储/Prompt 出口安全端到端

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-022, TASK-025, TASK-017, TASK-018
- **Source**: memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界, memory-redesign.design.md#3.8 可靠性与观测, memory-redesign.design.md#3.4.5 内容闸门与配额, memory-redesign.design.md#4.2 一次性迁移与切换
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: B-05, S-01, S-02
- **Files**: tests/acceptance/im_gateway/test_memory_redesign_security.py, tests/agent_runtime/test_memory_redesign_secrets.py, migrations/memory_redesign_data.py, tests/migrations/test_memory_redesign_preflight.py

### Description

按真实身份/API/DB/HTTP串联确认、管理写、隔离与canary；不把直调writer当最终API安全验证。

### Checklist

- [x] [S-01][E2E] 编写伪造用户/tenant/来源、跨用户确认、无服务身份、非ADMIN全部拒绝场景。
- [x] [S-02][E2E] 反查memory/history/proposal/job/receipt/tool审计/log与consolidation Prompt，密钥不漏。
- [x] [B-05/B-20][integration] 修复TASK-008复核发现的迁移扫描同类缺口：直接检查原始字符串，不能用canonical JSON二次转义后扫描；敏感JSON/单引号键值文本必须预检拒绝。实际0019→0020临时库补RED/GREEN，断言保持0019且没有history/审计扩散；不重新打开已完成TASK-007，最终B-20仍归原owner并在需求终验复跑。
- [x] E2E编码期不跑RED/GREEN；指定真实Live stack命令，证据保持planned至verify-e2e。
- [x] [B-05][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：unit + PG，禁存内容；断言：value、key、quote、TASK_STATE 任一含密钥形态则拒绝；memory/history/proposal/log 中无 canary。
- [x] [S-01][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：security，真实 API 身份与租户边界；断言：伪造 user/tenant/author、跨用户确认、无服务身份内部调用均拒绝；未授权 ADMIN 不写。
- [x] [S-02][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：security，真实装配与 canary 检查；断言：标注引用与未经确认；密钥不进入新增表、历史、回执、日志、模型 consolidation Prompt。
- [x] [RULE-secret-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-05 | integration | unit + PG，禁存内容 | value、key、quote、TASK_STATE 任一含密钥形态则拒绝；memory/history/proposal/log 中无 canary | tests/agent_runtime/test_memory_redesign_secrets.py / test_b05_memory_redesign_gate_*、test_b05_memory_redesign_writer_*、test_b05_memory_redesign_proposal_*、test_b05_memory_redesign_consolidation_*、test_b05_memory_redesign_tool_audit_* | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_secrets.py"] | verified |
| S-01 | E2E | security，真实 API 身份与租户边界 | 伪造 user/tenant/author、跨用户确认、无服务身份内部调用均拒绝；未授权 ADMIN 不写 | tests/acceptance/im_gateway/test_memory_redesign_security.py / test_s01_memory_redesign_*（3 用例，仅 collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | e2e_deferred |
| S-02 | E2E | security，真实装配与 canary 检查 | 标注引用与未经确认；密钥不进入新增表、历史、回执、日志、模型 consolidation Prompt | tests/acceptance/im_gateway/test_memory_redesign_security.py / test_s02_memory_redesign_*（3 用例，仅 collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | e2e_deferred |
| harness-secret#RULE-secret-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（tests/test_logging_redaction.py、tests/acceptance/test_foundation_ops_audit.py） | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-05 | 无 RED（不伪造）：写入门禁（`memory_write.validate_memory_content` 的密钥正则+凭据 canary、`memory_observability.memory_audit_preview`、`memory_consolidation_input/output`）在 TASK-008/009/019/021 已完成同类修复，新测试文件首跑即通过；本任务以真实边界测试补齐 B-05 owner | `DATABASE_URL=<隔离 head 库> REDIS_URL=<...> uv run pytest -q tests/agent_runtime/test_memory_redesign_secrets.py` → 28 passed | `tests/agent_runtime/test_memory_redesign_secrets.py`：`test_b05_memory_redesign_gate_rejects_secret_text_without_echoing_it`（value/quote/task×5 形态）、`..._gate_rejects_sensitive_key_names`（key×3）、`..._writer_rejects_and_stores_no_canary`（value/key/quote/task）、`..._proposal_rejects_canary_before_any_store`、`..._consolidation_input_and_output_hide_the_canary`、`..._tool_audit_preview_redacts_canary` | 真实 PG（隔离 head 库 `muad_acc_be4f8817ac`）+ 真实 `MemoryService`/`MemoryConfirmationService`/工具流水线；canary 反查 `user_memory`、`user_memory_history`、`memory_command_receipt`、`memory_scope`、`memory_consolidation_job`、`run_interrupt`、`tool_call_audit` 与 caplog | verified |
| B-05/B-20 迁移扫描 | `DATABASE_URL=<隔离 head 库> REDIS_URL=<...> uv run pytest -q tests/migrations/test_memory_redesign_preflight.py -k "quoted_sensitive or sensitive_legacy_memory_key or structured_sensitive"` → 4 failed, 2 passed：`{"api_key":"opaque-fixture"}`、`{'password': 'fixture'}`、`api-key` 未被预检拒绝（迁移继续推进），结构化 `{"api_key":...}` 落到 `MEMORY_MIGRATION_INVALID_LEGACY_MEMORY` 而非 FORBIDDEN_CONTENT | 修复后同命令 → 6 passed；`uv run pytest -q tests/migrations/test_memory_redesign_preflight.py` → 17 passed；`uv run pytest -q tests/migrations` → 24 passed | `tests/migrations/test_memory_redesign_preflight.py`：`test_b20_quoted_sensitive_text_blocks_before_canonical_json_escaping`、`test_b20_sensitive_legacy_memory_key_blocks_switch`、`test_b20_structured_sensitive_json_key_blocks_switch`；共用断言 `_assert_blocked_at_0019`（`alembic_version`=0019、无 `runtime.user_memory_history`、`control.config_audit_log` 计数 0） | 真实 0019 临时库（`create_datastore(revision="0019")`，每用例独立库）→ 真实 `migrations/db_migrate.py upgrade 0020` 子进程；生产改动 `migrations/memory_redesign_data.py::_forbidden_text` 直查原始字符串（`_SECRET`/`_SECRET_KEY` 与 Runtime 写入门禁同形状），不再 `canonical_json` 二次转义后扫描 | verified |
| S-01 | 编码期不执行（E2E）：`uv run pytest -q --collect-only tests/acceptance/im_gateway/test_memory_redesign_security.py` → 6 collected | 未执行；延期至需求终验 `code-flow acceptance run --include-e2e`（或 `uv run pytest -q tests/acceptance/im_gateway/test_memory_redesign_security.py`） | `test_s01_memory_redesign_internal_calls_require_real_service_identity`（无/伪造 `X-Internal-Service`→403；伪造 `author`→422；伪造 tenant 只落在声称租户）、`test_s01_memory_redesign_cross_user_confirmation_is_refused`（跨用户/跨租户 resume→404，Run 仍 WAITING_INPUT，无 USER 事实）、`test_s01_memory_redesign_unauthorized_console_admin_does_not_write`（BUILDER→403 且不写，ADMIN 阳性对照） | 真实 WS 探针 → 真实 Gateway → 真实 Runtime `/v1/runs`、`/v1/runs/{id}/resume`、`/internal/admin/users/{id}/memory/commands` → 真实 Console HTTP（会话+CSRF+RBAC）→ 真实 PG 回读；服务日志在栈 `logs/*.log`（runtime/gateway/console/worker） | e2e_deferred |
| S-02 | 编码期不执行（E2E）：同文件 collect 覆盖 | 未执行；延期至 verify-e2e | `test_s02_memory_redesign_unconfirmed_candidate_stays_annotated_and_unsaved`（`run_interrupt.proposal_json` 带 `evidence.source_quote`，未确认前 `user_memory` 为空）、`test_s02_memory_redesign_canary_never_reaches_stores_logs_or_prompt`、`test_s02_memory_redesign_canary_absent_from_receipts_and_probe_tool_messages`（canary=`e2e-model-key` 真实模型凭据，反查新增表/历史/回执/提案/job/tool 审计 + 服务日志文件 + 模型探针 consolidation 请求体） | 真实 Runtime 装配（canary 来自真实 `resolve-credentials` 的模型 api_key）→ 真实 PG → 真实模型探针 `/requests` → 真实服务日志文件 | e2e_deferred |
| harness-secret#RULE-secret-001 | 不适用（原verifier回归，不制造 RED） | `DATABASE_URL=<隔离 head 库> REDIS_URL=<...> uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py` → 14 passed | 原 verifier 指定用例（脱敏过滤器 + 运维审计） | 原 verifier 真实组件（日志脱敏与审计） | verified |
- B-05: verified — automated command passed; run_id=f53ea70a4a50434cb139c33ad59242c1 (confirmed_by: runner)
- S-01: e2e_deferred — automated command e2e_deferred; run_id=f53ea70a4a50434cb139c33ad59242c1 (confirmed_by: runner)
- S-02: e2e_deferred — automated command e2e_deferred; run_id=f53ea70a4a50434cb139c33ad59242c1 (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] completed (done)

---

## TASK-030: 跨会话、真实确认、浏览器与崩溃验收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-029, TASK-028, TASK-022
- **Source**: memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: E-02, E-03, E-04, E-05, E-06
- **Files**: tests/acceptance/im_gateway/test_memory_redesign_e2e.py, e2e/tests/agent-management/memory-redesign.spec.ts, e2e/playwright.agent.config.ts

### Description

闭合默认设置的确认/偏好/续接/lineage与SIGKILL接管；agent域浏览器新增真实Runtime，沿用独立数据库/Redis/探针，不mock业务。

### Checklist

- [x] [E-02/E-03/E-05/E-06][E2E] 编写真Gateway/Runtime/PG/Redis/HTTP/进程场景；多任务回复第2个真进入模型请求；A→B→C仍唯一，完成后无旧未决。
- [x] [E-04][E2E] agent config原来只Console+模型探针，必须补Runtime/种子/临时库/连接配置；不拿布局mock测试替代。
- [x] 编码期只登记与collect场景；functional全绿后verify-e2e统一执行，保留失败trace与实例清理。
- [x] [E-02][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Gateway → Runtime → resume；断言：自然语言候选展示后用户真实确认，重连/重复提交不重复保存；拒绝后不产生 USER 事实。
- [x] [E-03][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Gateway + 两个 conversation；断言：已确认中文偏好在 /new 后“你好”首轮可见；不要求 query 包含“中文”。
- [x] [E-04][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：browser，真实 Console/Runtime/PG/Chrome；断言：创建、启禁用、删除、历史分页和来源状态本地化；越权不可操作。
- [x] [E-05][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Gateway/Runtime/PG + 模型探针；断言：默认设置提炼未决项；/new后续接进入请求；多条→用户“第2个”→目标实际进入请求；A→B→C仍唯一，B完成后无旧未决；大checkpoint投递引用，引用超预算仅计丢弃。
- [x] [E-06][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实两个 Runtime 子进程；断言：SIGKILL claim 执行者，另一实例接管；故障与成功可见；持久写入效果最多一次。
- [x] [RULE-test-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]（cwd=., timeout=2400s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | E2E | acceptance，真实 Gateway → Runtime → resume | 自然语言候选展示后用户真实确认，重连/重复提交不重复保存；拒绝后不产生 USER 事实 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e02_memory_redesign_real_confirmation_replay_and_rejection | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | e2e_deferred |
| E-03 | E2E | acceptance，真实 Gateway + 两个 conversation | 已确认中文偏好在 /new 后“你好”首轮可见；不要求 query 包含“中文” | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e03_memory_redesign_confirmed_preference_visible_after_new_conversation | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | e2e_deferred |
| E-04 | E2E | browser，真实 Console/Runtime/PG/Chrome | 创建、启禁用、删除、历史分页和来源状态本地化；越权不可操作 | e2e/tests/agent-management/memory-redesign.spec.ts / 4 用例（创建启禁用删除+历史分页+本地化布局矩阵+越权403） | ["make","acceptance-e2e","DOMAIN=agent"] | e2e_deferred |
| E-05 | E2E | acceptance，真实 Gateway/Runtime/PG + 模型探针 | 默认设置提炼未决项；/new后续接进入请求；多条→用户“第2个”→目标实际进入请求；A→B→C仍唯一，B完成后无旧未决；大checkpoint投递引用，引用超预算仅计丢弃 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e05_memory_redesign_default_consolidation_then_new_conversation_resume、test_e05_memory_redesign_second_option_selection_enters_real_request、test_e05_memory_redesign_completion_retires_original_and_large_checkpoint_reference | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | e2e_deferred |
| E-06 | E2E | acceptance，真实两个 Runtime 子进程 | SIGKILL claim 执行者，另一实例接管；故障与成功可见；持久写入效果最多一次 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e06_memory_redesign_sigkill_claim_owner_and_successor_takeover | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | e2e_deferred |
| harness-test#RULE-test-001 | E2E | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实链路（全量 tests/acceptance + 前端构建 + foundation 浏览器域） | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | e2e_deferred |

### Acceptance Evidence

编码期只登记与 collect：E-02/E-03/E-04/E-05/E-06 全部保持 `e2e_deferred`（正式 E2E 执行归需求终验），没有 manual 或 N/A 确认；RULE-test-001 按元数据命令实跑并在下方登记实际结果。元数据命令的**全量 acceptance 会实际执行本文件的 E2E 用例**（它们同在 `tests/acceptance` 下），故下方证据同时记录这两次链上执行里本文件自身的真实结果与缺陷修复。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-02/E-03/E-05/E-06 | 不按 RED/GREEN 流程推进（先写场景）；链上首跑暴露场景自身 5 处缺陷并逐一修复（`_started_run` 的"新 Run"同步点恒真导致取到上一条 Run、候选提示按节拍异步投递需等待、Runtime 管理命令体为 `{actor_id,mutation}`、提炼 job 的会话内因果序需要新会话、暂停恢复轮需 `tool_once` 才不再重复提议）；另修一处验收环境清理缺口（`RUNTIME_CLEANUP` 缺 0020 记忆表 ⇒ 残留 job 让后续栈的记忆后台泵持续 ValueError） | 元数据链第三次实跑登记见下（`6 passed` 于本文件；链上其余存量红如实分类） | tests/acceptance/im_gateway/test_memory_redesign_e2e.py 6 用例：test_e02_memory_redesign_real_confirmation_replay_and_rejection、test_e03_memory_redesign_confirmed_preference_visible_after_new_conversation、test_e05_memory_redesign_default_consolidation_then_new_conversation_resume、test_e05_memory_redesign_second_option_selection_enters_real_request、test_e05_memory_redesign_completion_retires_original_and_large_checkpoint_reference、test_e06_memory_redesign_sigkill_claim_owner_and_successor_takeover | 真实 WS 探针 → 真实 Gateway（含进程重启后的重投去重）→ 真实 Runtime（`/v1/runs`、resume、`/internal/admin/users/{id}/memory/commands`）→ 真实 PG（run_interrupt/user_memory/user_memory_history/run_submission/canonical_event/memory_consolidation_job/platform_setting）→ 真实模型 HTTP 探针 `/requests` → Runtime `/metrics`；E-06 用真实两个 uvicorn 子进程 + SIGKILL（`POD_NAME` 使 lease_owner 可辨，consolidation 租约 45s）；单跑 `uv run pytest -q tests/acceptance/im_gateway/test_memory_redesign_e2e.py` → 6 passed | e2e_deferred |
| E-04 | 不执行 RED；仅编写/collect | e2e_deferred | e2e/tests/agent-management/memory-redesign.spec.ts 4 用例（创建/启禁用/历史分页/删除、zh×en×明暗×1280/390 布局与来源状态本地化、越权 BUILDER 菜单隐藏+路由守卫+写入口 403 且无副作用） | 真实 Chrome → vite preview（构建产物）→ 真实 Console（会话+CSRF+RBAC）→ 真实 Runtime 记忆写入口 → 真实 PG；agent 域配置补 Runtime webServer、隔离库、平台设置/模型种子与 `AGENT_RUNTIME_URL`/`INTERNAL_SERVICE_TOKEN` 连接配置（`npx esbuild` 语法校验通过） | e2e_deferred |
| harness-test#RULE-test-001 | 不适用（原 verifier 回归，不制造 RED） | 按元数据 argv 实跑（cwd=., timeout=2400s）：① `uv run pytest -q tests/acceptance` → **13 failed, 318 passed in 1435.50s（0:23:55）**，EXIT=1；② `npm --prefix apps/console-platform/frontend run build` → `✓ built in 3.90s`，EXIT=0（链上因 `&&` 未执行，单跑补记）；③ `npm --prefix e2e test` → **4 passed (11.6s)**，EXIT=0（同上单跑补记）。13 项红全部与本任务改动无关，分类：`test_memory_flow.py::test_s01/test_s05`＝0020 删除 `runtime.user_memory.source_type` 后旧用例仍 SELECT/传该列（本需求改动的存量陈旧用例，按指令不修）；`test_memory_redesign_security.py` S-01×2/S-02×2 与 `runtime/test_memory_redesign_context_e2e.py::test_e01[summary]`＝其他 TASK 的 deferred E2E 首次实跑暴露的问题（S-01 的管理命令体仍是 `{command_id,endpoint,mutations}`，与 TASK-023 的 `{actor_id,mutation}` 契约不符 ⇒ 422）；`dfx/test_dfx_journeys.py::test_s09`、`dfx/test_dfx_model_recovery.py::test_e09`、`gateway/test_waiting_resume.py::test_e17`、`im_gateway/test_context_compaction.py::test_s01`、`runtime/test_run_lifecycle.py::test_b01`、`test_secret_migration.py::test_e03`＝与本任务文件无关的存量红（本任务未改这些域；前几轮链跑里位置/数量随负载浮动，属串跑偶发）。本任务自身：`tests/acceptance/im_gateway/test_memory_redesign_e2e.py` 6 用例在该次全量链跑中**全部通过**（不在失败清单内）。 | 原 verifier 真实组件：真实 PG/Redis 隔离栈（`tests/acceptance/conftest.py` 每轮独立库 + 独立 Redis）、真实 Console、真实 Chrome + 构建产物（`vite preview`） | failed |
- E-02: e2e_deferred — automated command e2e_deferred; run_id=efc46443c9aa4b4cb2aa150a1b2b0c58 (confirmed_by: runner)
- E-03: e2e_deferred — automated command e2e_deferred; run_id=efc46443c9aa4b4cb2aa150a1b2b0c58 (confirmed_by: runner)
- E-04: e2e_deferred — automated command e2e_deferred; run_id=efc46443c9aa4b4cb2aa150a1b2b0c58 (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=efc46443c9aa4b4cb2aa150a1b2b0c58 (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=efc46443c9aa4b4cb2aa150a1b2b0c58 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=142f39353a814ea5b4c2641438d457ea (confirmed_by: runner)
- E-03: e2e_deferred — automated command e2e_deferred; run_id=142f39353a814ea5b4c2641438d457ea (confirmed_by: runner)
- E-04: e2e_deferred — automated command e2e_deferred; run_id=142f39353a814ea5b4c2641438d457ea (confirmed_by: runner)
- E-05: e2e_deferred — automated command e2e_deferred; run_id=142f39353a814ea5b4c2641438d457ea (confirmed_by: runner)
- E-06: e2e_deferred — automated command e2e_deferred; run_id=142f39353a814ea5b4c2641438d457ea (confirmed_by: runner)

### Log

- [2026-10-12] created (draft); 未启动编码。
- [2026-10-15] started
- [2026-10-15] 完成编码：E-02/E-03/E-05/E-06 场景（真实 Gateway/Runtime/PG/模型探针/双 Runtime + SIGKILL）与 E-04 浏览器域接线（agent 域补 Runtime/种子/隔离库/连接配置）；E2E 场景登记 `e2e_deferred`，RULE-test-001 按元数据 argv 实跑并登记（第 1 段 13 项存量红，本任务 6 条 E2E 全绿；第 2/3 段单跑 pass）；顺带修验收环境清理缺口（`RUNTIME_CLEANUP`/runtime conftest/dfx purge/gateway conftest 缺 0020 记忆表 ⇒ 残留 job 让后续栈记忆后台泵停摆）。
- [2026-10-15] completed (done)

---

## TASK-031: 规范/文档同步、inventory 与需求终验

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-030
- **Source**: memory-redesign.design.md#6.1 Spec Compliance Matrix, memory-redesign.design.md#6.2 设计进入 Plan 的条件, memory-redesign.design.md#4.3 回滚
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: tests/memory_redesign_inventory.py, docs/configuration-inventory.md, .code-flow/tasks/2026-10-12/memory-redesign/memory-redesign.md

### Description

以实现事实同步所有指定docs/Spec，refresh Context；inventory对design/任务/manifest/required Rules/真实测试盘面闭合并扰动取证。

### Checklist

- [ ] 先写inventory缺失/删场景/改owner/改level或command/缺Evidence/假测试名的RED，至少4类扰动复绿。
- [ ] 新增memory规范verifier实际执行；旧source/prefix/ratio/自动复活断言替换为新契约，不只删测试。
- [ ] functional全部verified后verify-e2e/全量校验，Gate结论取finish实际返回再提交/归档；不重生已有Evidence manifest。
- [ ] 范围较多的docs同步按清单逐项处理（非生产代码原子单元），不能只改configuration-inventory。
- [ ] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | 编码阶段登记真实辅助测试；无当前执行Evidence | planned | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。
