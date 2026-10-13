# Tasks: Memory Redesign

- **Source**: .code-flow/tasks/2026-10-12/memory-redesign/memory-redesign.design.md（v0.5；保留用户v0.4补充并修正缓存/约束边界）
- **Created**: 2026-10-12
- **Updated**: 2026-10-13
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
| B-05 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | unit + PG，禁存内容 | TASK-029 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_secrets.py"] | . | 300 |  |
| B-06 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 工具回执 | TASK-013 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_recall.py"] | . | 300 |  |
| B-07 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 模型 HTTP 探针 | TASK-013 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | . | 300 |  |
| B-08 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 请求探针 | TASK-012 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | . | 300 |  |
| B-09 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | unit + 请求探针 | TASK-012 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_context.py"] | . | 300 |  |
| B-10 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + HTTP 模型探针 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-11 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 故障注入 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-12 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 两段 Run | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | . | 300 |  |
| B-13 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，迁移库 + ORM create_all | TASK-007 | verified | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_schema_parity.py"] | . | 300 |  |
| B-14 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，旧 revision 存量库升级 | TASK-007 | verified | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py"] | . | 300 |  |
| B-15 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 Gateway/Runtime/PG/resume | TASK-017 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_confirmation.py"] | . | 300 |  |
| B-16 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + HTTP 模型探针 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-17 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | . | 300 |  |
| B-18 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG | TASK-009 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_quotas.py"] | . | 300 |  |
| B-19 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，摘要关闭 + 真实模型 HTTP | TASK-019 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_provider.py"] | . | 300 |  |
| B-20 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，迁移前置检查与恢复演练 | TASK-007 | verified | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_preflight.py"] | . | 300 |  |
| B-21 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 延迟 job / 后续摘要 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | . | 300 |  |
| B-22 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实PG+旧新Run | TASK-016 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | . | 300 |  |
| B-23 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | integration | integration，真实 PG + 并发 writer / 当前语句时间 | TASK-010 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py"] | . | 300 |  |
| E-01 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Runtime + PG/Redis + 模型 HTTP | TASK-012 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/runtime/test_memory_redesign_context_e2e.py"] | . | 1800 |  |
| E-02 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Gateway → Runtime → resume | TASK-030 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| E-03 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Gateway + 两个 conversation | TASK-030 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| E-04 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | browser，真实 Console/Runtime/PG/Chrome | TASK-030 | planned | ["make","acceptance-e2e","DOMAIN=agent"] | . | 1800 |  |
| E-05 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实 Gateway/Runtime/PG + 模型探针 | TASK-030 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| E-06 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | acceptance，真实两个 Runtime 子进程 | TASK-030 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | . | 1800 |  |
| S-01 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | security，真实 API 身份与租户边界 | TASK-029 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | . | 1800 |  |
| S-02 | memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界 | E2E | security，真实装配与 canary 检查 | TASK-029 | planned | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | . | 1800 |  |

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
| harness-secret#RULE-secret-001 | TASK-029 | code | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | planned |
| harness-test#RULE-test-001 | TASK-030 | code | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | planned |
| harness-ui-detail#RULE-ui-detail-001 | TASK-028 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |
| harness-ui#RULE-ui-001 | TASK-028 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | planned |
| harness-frontend#RULE-front-001 | TASK-026 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |
| harness-i18n#RULE-i18n-001 | TASK-027 | code | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | planned |
| harness-time#RULE-time-001 | TASK-007 | code | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | planned |
| harness-model#RULE-model-001 | TASK-019 | code | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | planned |
| harness-log#RULE-log-001 | TASK-018 | code | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | planned |
| harness-im#RULE-im-001 | TASK-017 | code | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | planned |
| harness-im#RULE-im-002 | TASK-017 | code | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | planned |

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-012, TASK-009
- **Source**: memory-redesign.design.md#3.5.2 任务续接闭环, memory-redesign.design.md#3.3.6 其余硬约束索引（文本提到但必须落到 DDL）
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_resume.py, apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py

### Description

真实用户选择写统一MEMORY_RESUME_RESOLVED，复用source_event_id唯一；无options唯一选择也先建单项；跨会话继续更新原task key。

### Checklist

- [ ] [E-05][integration] 辅助先写泛指多条→第2个、过期/版本变化/否定句不选、options跨outcome互斥RED。
- [ ] Runtime确定清单编号并持久投递，模型不能伪造批准；不新建options消费表。
- [ ] 入站USER_MESSAGE固定source_scope_revision；记录resume_ref供job，模型输出不能指定root。
- [ ] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | 编码阶段登记真实辅助测试；无当前执行Evidence | planned | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-015: 真实确认 resume 原子提交与 deadline

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-013, TASK-014
- **Source**: memory-redesign.design.md#3.4.2 remember 与真实确认
- **Spec-Refs**: harness-memory#RULE-memory-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_confirmation.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/api/runs.py

### Description

MEMORY_CONFIRMATION专用分派；合法输入/事实/history/receipt/interrupt/Run CAS/submission同事务，终态/绝对deadline优先。

### Checklist

- [ ] [B-15][integration] 先写真实Gateway/Runtime/PG确认、拒绝、无效编号仍等待、stale合法续跑、超Run deadline不恢复RED。
- [ ] 其他用户/版本/hash/epoch/重复message_id不能授予另一份内容。
- [ ] 不先无条件消费所有WAITING interrupt，不改已持久化候选回执。
- [ ] [RULE-memory-001] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]（review）验证 来源证据、真实候选确认、禁止模型授予USER/ADMIN；review由需求终验统一执行，当前无执行Evidence。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-memory#RULE-memory-001 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 来源证据、真实候选确认、禁止模型授予USER/ADMIN | 目标文件在verifier argv中；创建前不宣称可执行 | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-016: Run/job 冻结全部记忆策略与工具历史预算

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-015
- **Source**: memory-redesign.design.md#2.3.3 配置项, memory-redesign.design.md#3.2.2 模型请求组件, memory-redesign.design.md#4.2 一次性迁移与切换
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-22
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py

### Description

新策略/model定义一次冻结；执行不重新读取设置；ratio和硬编码历史轮数路径全部移除，历史旧终态快照只审计。

### Checklist

- [ ] [B-12][integration] 先写一次只改一个叶子的新Run/旧resume/job，其他叶子不变RED。
- [ ] [B-22][integration] 200→300已有250条旧Run仍可读取/forget，不能回退容量；在线收紧拒绝。
- [ ] snapshot/model剥api_key，凭据实时；source policy与后台deadline预算分离。
- [ ] [B-22][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实PG+旧新Run；断言：额度200→300后250条均被旧Run读到；旧Run forget成功；旧冻结额度不额外淘汰；在线降低拒绝，维护排空才可降。
- [ ] [RULE-snapshot-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-22 | integration | integration，真实PG+旧新Run | 额度200→300后250条均被旧Run读到；旧Run forget成功；旧冻结额度不额外淘汰；在线降低拒绝，维护排空才可降 | tests/agent_runtime/test_memory_redesign_snapshot.py / test_b22_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | planned |
| harness-snapshot#RULE-snapshot-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-017: Gateway /memory 前缀、确认与续接事件投递

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-016, TASK-014
- **Source**: memory-redesign.design.md#3.4.2 remember 与真实确认, memory-redesign.design.md#3.5.2 任务续接闭环
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: B-15
- **Files**: apps/im-gateway/src/muad_im_gateway/application/inbound.py, apps/im-gateway/src/muad_im_gateway/application/runtime_client.py, apps/im-gateway/src/muad_im_gateway/application/progress.py

### Description

前缀走独立命令容量，Gateway只传真实message/身份；权威参数解析在Runtime；忙态不能另建Run。

### Checklist

- [ ] [E-02/E-05][integration] 辅助先写长流中memory即时受理分类但RUN_BUSY明确、WAITING_INPUT合法resume、断线事件回放RED。
- [ ] 确认候选与options文本用Runtime持久事件的确切内容/编号，正确绑定本条回复会话。
- [ ] 无渠道专有字样流入Runtime，不把模型文本当用户输入；记录GREEN。
- [ ] [B-15][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 Gateway/Runtime/PG/resume；断言：确认内容与展示一致；其他用户、编号、过期、旧版本/forget_epoch、重放均不能保存另一份内容；同轮两提议明确拒绝第二个；无效回答仍 WAITING，拒绝合法续跑。
- [ ] [RULE-im-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/console_channel","tests/gateway"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。
- [ ] [RULE-im-002] verifier：按元数据argv ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-15 | integration | integration，真实 Gateway/Runtime/PG/resume | 确认内容与展示一致；其他用户、编号、过期、旧版本/forget_epoch、重放均不能保存另一份内容；同轮两提议明确拒绝第二个；无效回答仍 WAITING，拒绝合法续跑 | tests/agent_runtime/test_memory_redesign_confirmation.py / test_b15_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |
| harness-im#RULE-im-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | planned |
| harness-im#RULE-im-002 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-018: 记忆指标 catalog、日志与审计脱敏

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-013, TASK-004
- **Source**: memory-redesign.design.md#3.8 可靠性与观测, memory-redesign.design.md#3.4.5 内容闸门与配额
- **Spec-Refs**: harness-log#RULE-log-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/metrics.py, apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_observability.py

### Description

新增confirmation/claim/stale/expiry/drop/error等低基数指标；handler前remember参数特殊脱敏，值/quote/prompt不入日志。

### Checklist

- [ ] [S-02][integration] 辅助先写无流量metrics目录、高基数拒绝、异常不回显canary、错误不能计成功RED。
- [ ] value/quote不因键名非敏感而进入tool_call_audit；extra唯一通道。
- [ ] 失败可见性不要求不可写DB强写事件；记录真实warning/metric。
- [ ] [RULE-log-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-log#RULE-log-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-019: 固定 consolidation 输入与独立主模型 provider

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-016, TASK-018
- **Source**: memory-redesign.design.md#3.6.1 入队与执行, memory-redesign.design.md#3.6.2 顺序与恢复
- **Spec-Refs**: harness-model#RULE-model-001
- **Acceptance-Refs**: B-19
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation.py, apps/agent-runtime/src/muad_agent_runtime/application/context_events.py

### Description

固定through_seq/摘要覆盖边界/input hash；输入脱敏/预算；冻结主模型、实时凭据、独立timeout/output/provider close。

### Checklist

- [ ] [B-19][integration] 先写摘要关闭仍走主模型、实时凭据、超时与close、Run终态不变RED。
- [ ] [B-21][integration] 辅助写R1延迟到R2摘要后仍只含R1输入；hash/revision不能在接管时刷新。
- [ ] 整批严格解析、非法重复key/root/category/密钥均拒绝，不逐好项提交。
- [ ] [B-19][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，摘要关闭 + 真实模型 HTTP；断言：consolidation 仍用冻结主模型；凭据实时取；provider 正常关闭；timeout 不改变 Run 终态。
- [ ] [RULE-model-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-19 | integration | integration，摘要关闭 + 真实模型 HTTP | consolidation 仍用冻结主模型；凭据实时取；provider 正常关闭；timeout 不改变 Run 终态 | tests/agent_runtime/test_memory_redesign_provider.py / test_b19_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_provider.py"] | planned |
| harness-model#RULE-model-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-020: 持久 job 入队、claim、lease 与接管

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-007, TASK-016, TASK-019
- **Source**: memory-redesign.design.md#3.3.5 memory_consolidation_job（新增）, memory-redesign.design.md#3.6.1 入队与执行
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_jobs.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py

### Description

COMPLETED终态内savepoint入队；PG claim/epoch/lease/attempt唯一；首次固定输入只写一次。

### Checklist

- [ ] [B-10][integration] 辅助先写并发claim、unique Run job、失去lease后不能修改状态RED。
- [ ] [B-11][integration] 辅助写入队失败只回滚savepoint、主终态仍提交。
- [ ] 前驱RUNNING/RETRY未决时同conversation后继不越过；不凭内存协程持执行权。
- [ ] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | 编码阶段登记真实辅助测试；无当前执行Evidence | planned | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-021: 批量提炼效果、history/event/job 原子收口

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-020, TASK-009, TASK-014
- **Source**: memory-redesign.design.md#3.6.1 入队与执行, memory-redesign.design.md#3.6.2 顺序与恢复, memory-redesign.design.md#3.4.4 同 key 冲突矩阵
- **Spec-Refs**: harness-memory#RULE-memory-004, harness-memory#RULE-memory-005
- **Acceptance-Refs**: B-10, B-11, B-12, B-16, B-17, B-21
- **Files**: apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_jobs.py

### Description

锁序内校验epoch/lease/revision/逐key；batch_in同事务写全部效果/history/scope/receipt/唯一完成事件/job终态。

### Checklist

- [ ] [B-10][integration] 先写两个执行者不能双写效果/history/event，旧epoch不能完成RED。
- [ ] [B-11][integration] 坏形状/后项失败/history失败与提交前后崩溃，整批零或一套效果。
- [ ] [B-16/B-17][integration] USER/ADMIN不降权、删除墓碑/人工禁用不复活，自动退休只能新入站依据恢复。
- [ ] [B-21][integration] 延迟摘要边界与重试固定输入，scope变化STALE；LINEAGE同key更新/完成退休。
- [ ] [B-10][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + HTTP 模型探针；断言：两个 Runtime 并发 claim、过期 lease 接管；最多一套记忆/历史/完成事件；旧 epoch 无法提交。
- [ ] [B-11][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 故障注入；断言：坏形状、批内后项失败、历史失败整批零效果；效果提交前/后崩溃均不重复；Run 保持 COMPLETED。
- [ ] [B-12][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 两段 Run；断言：修改一个设置，新 Run 读新值，旧 Run/resume/job 均读冻结值；无其他叶子意外变化。
- [ ] [B-16][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + HTTP 模型探针；断言：forget 后才开始提炼旧会话仍不可复活；模型期间删除使 job STALE；人工 disabled 不被启用；自动退休仅新入站 source_scope_revision 可恢复。
- [ ] [B-17][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG；断言：AGENT 同 key 不覆盖 USER/ADMIN；晚结束的旧 Run 不覆盖较新状态；同值重放不虚增确认数。
- [ ] [B-21][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：integration，真实 PG + 延迟 job / 后续摘要；断言：R1 job在R2摘要后执行仍仅含R1边界；重试输入hash/revision不可刷新；记忆变更后重试STALE。
- [ ] [RULE-memory-004] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]（review）验证 用户删除墓碑、forget_epoch、新依据退休恢复、禁止来源降权；review由需求终验统一执行，当前无执行Evidence。
- [ ] [RULE-memory-005] verifier：规范已由TASK-001建立并绑定，按 ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"]（review）验证 固定提炼输入、lease/epoch fence、最多一次原子效果、失败可见且Run不变；review由需求终验统一执行，当前无执行Evidence。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-10 | integration | integration，真实 PG + HTTP 模型探针 | 两个 Runtime 并发 claim、过期 lease 接管；最多一套记忆/历史/完成事件；旧 epoch 无法提交 | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b10_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | planned |
| B-11 | integration | integration，真实 PG + 故障注入 | 坏形状、批内后项失败、历史失败整批零效果；效果提交前/后崩溃均不重复；Run 保持 COMPLETED | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b11_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | planned |
| B-12 | integration | integration，真实 PG + 两段 Run | 修改一个设置，新 Run 读新值，旧 Run/resume/job 均读冻结值；无其他叶子意外变化 | tests/agent_runtime/test_memory_redesign_snapshot.py / test_b12_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_snapshot.py"] | planned |
| B-16 | integration | integration，真实 PG + HTTP 模型探针 | forget 后才开始提炼旧会话仍不可复活；模型期间删除使 job STALE；人工 disabled 不被启用；自动退休仅新入站 source_scope_revision 可恢复 | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b16_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | planned |
| B-17 | integration | integration，真实 PG | AGENT 同 key 不覆盖 USER/ADMIN；晚结束的旧 Run 不覆盖较新状态；同值重放不虚增确认数 | tests/agent_runtime/test_memory_redesign_job_effects.py / test_b17_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py"] | planned |
| B-21 | integration | integration，真实 PG + 延迟 job / 后续摘要 | R1 job在R2摘要后执行仍仅含R1边界；重试输入hash/revision不可刷新；记忆变更后重试STALE | tests/agent_runtime/test_memory_redesign_input_boundary.py / test_b21_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | planned |
| harness-memory#RULE-memory-004 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 用户删除墓碑、forget_epoch、新依据退休恢复、禁止来源降权 | 目标文件在verifier argv中；创建前不宣称可执行 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |
| harness-memory#RULE-memory-005 | integration | 真实PG/Runtime/模型HTTP及冻结数据；纯函数子项直接验证 | 固定提炼输入、lease/epoch fence、最多一次原子效果、失败可见且Run不变 | 目标文件在verifier argv中；创建前不宣称可执行 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-022: Runtime 后台接线、TTL 与过期确认清扫

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-021, TASK-015, TASK-018
- **Source**: memory-redesign.design.md#3.8 可靠性与观测, memory-redesign.design.md#3.6 consolidation 生命周期
- **Spec-Refs**: harness-arch#RULE-arch-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/main.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_supervisor.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_expiry.py

### Description

lifespan启动停止有界job/expiry泵，PG权威无实例绑定；过期确认用原锁序处理，不scope→interrupt反锁。

### Checklist

- [ ] [E-06][integration] 辅助先写跨实例接管/旧epoch拒绝、shutdown清理provider任务、非COMPLETED不入队RED。
- [ ] TTL读时即时生效，sweep逐scope短事务写EXPIRE历史，不能在scope锁内网络IO。
- [ ] Runtime readiness/部署单元与导入方向不新增耦合，记录GREEN。
- [ ] [RULE-arch-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/architecture"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-arch#RULE-arch-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/architecture"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-023: Runtime 管理命令 API、服务身份与分页

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-009, TASK-015, TASK-022
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: 
- **Files**: apps/agent-runtime/src/muad_agent_runtime/api/memory.py, apps/agent-runtime/src/muad_agent_runtime/main.py, apps/agent-runtime/src/muad_agent_runtime/application/memory_queries.py

### Description

受保护内部写入口绑定actor/tenant/user，分页与错误目录统一；既有GET/DELETE统一服务，不从模型参数授予ADMIN。

### Checklist

- [ ] [S-01][integration] 辅助先写无服务身份/跨用户/伪造ADMIN/tenant、不存在资源均明确拒绝RED。
- [ ] POST幂等与expected_version冲突真实回执；不回显存储路径/敏感值。
- [ ] 新增API与router安装走共享api-kit，最终安全端到端责任在security任务。
- [ ] [RULE-api-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-api#RULE-api-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-024: Console 记忆状态/历史只读仓储

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-007, TASK-023
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/memory_repository.py, apps/console-platform/backend/src/muad_console_platform/application/memory_service.py, apps/console-platform/backend/src/muad_console_platform/api/users.py

### Description

ACTIVE/DISABLED/EXPIRED过滤、history按version分页，list/count同条件；deleted从模型列表隐藏但history可查。

### Checklist

- [ ] [E-04][integration] 辅助先写跨租户、过期/禁用/删除、总数与items齐平、版本排序RED。
- [ ] 展示author与actor分离，Console时间投影复用统一入口。
- [ ] 只读runtime表，移除旧source_type投影，测试旧未知值明确回退。
- [ ] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | 编码阶段登记真实辅助测试；无当前执行Evidence | planned | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-025: Console 写操作转 Runtime 并保持命令回执

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-024, TASK-023
- **Source**: memory-redesign.design.md#3.2.1 组件分工, memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: 
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/backend/src/muad_console_platform/infrastructure/runtime_memory_client.py, apps/console-platform/backend/src/muad_console_platform/application/memory_service.py, apps/console-platform/backend/src/muad_console_platform/api/users.py

### Description

管理员鉴权后HTTP调用唯一writer，新增/启禁用/删除/清空共享command_id，禁直接UPDATE runtime表。

### Checklist

- [ ] [E-04/S-01][integration] 辅助先写重复请求不重复效果、403/版本冲突、Runtime失败不宣称成功RED。
- [ ] Console日志关联command_id；不宣称本地config审计与远端写原子。
- [ ] DTO白名单与统一Envelope；缺Runtime连接配置清晰报错。
- [ ] 本任务不领取最终场景owner；辅助测试仍必须有真实文件、RED/GREEN与证据，并与责任表中对应验收衔接；不能仅用no_owned_scenarios作为验证结论。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| LOCAL | unit/integration | 本任务纯逻辑或真实存储载体，按Checklist具体边界 | Checklist列出的独立行为及失败反例，最终场景由责任表owner验证 | 编码阶段登记真实辅助测试；无当前执行Evidence | planned | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-026: 前端记忆 API 服务层与严格 DTO

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-025
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-frontend#RULE-front-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/user-identity/services/users.ts, apps/console-platform/frontend/src/modules/user-identity/memoryTypes.ts

### Description

新增创建/history/status/expected_version契约，组件仅经服务层调用，类型不放任Any与旧source_type适配。

### Checklist

- [ ] [E-04][unit] 辅助先写源码API出口/类型契约RED；HTTP只从services调用共享axios。
- [ ] 错误码只经apiErrorBody映射，404/409/403回执语义明确。
- [ ] 运行frontend API/i18n检查和typecheck，真实浏览器验证归e2e任务。
- [ ] [RULE-front-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-frontend#RULE-front-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-027: 记忆来源/状态/历史原因及错误本地化

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002, TASK-004
- **Source**: memory-redesign.design.md#3.7 API 与 Console, memory-redesign.design.md#6.1 Spec Compliance Matrix
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, apps/console-platform/frontend/src/modules/user-identity/memoryOptions.ts

### Description

author/actor/category/退休原因/确认状态与动态键齐备，译文每次渲染生成，未知值原样显示。

### Checklist

- [ ] 先写中英文键集齐平、enum键真实翻译、错误码同名及无孤儿词条RED。
- [ ] 不新增i18n框架/HTTPclient机制，options按模块唯一且由当前t注入。
- [ ] [E-04][unit] 辅助语言切换和原始码title契约，记录GREEN。
- [ ] [RULE-i18n-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-i18n#RULE-i18n-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-028: Memory Tab 管理动作、状态与历史

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-026, TASK-027
- **Source**: memory-redesign.design.md#3.7 API 与 Console
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001, harness-ui#RULE-ui-001
- **Acceptance-Refs**: 
- **Files**: apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx, apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx, apps/console-platform/frontend/src/modules/user-identity/MemoryHistorySideSheet.tsx

### Description

复用Semi/详情壳/ConfirmAction/DateTimeText，管理员动作与disabled/expired/删除历史清楚，版本冲突可恢复。

### Checklist

- [ ] [E-04][unit] 辅助先写详情结构/源码契约RED，表单字段顺序与动作权限有明确断言。
- [ ] author/actor分开，enabled操作不伪造来源；不加实现术语到用户界面。
- [ ] [E-04][E2E] 编写真实Console→Runtime→PG→Chrome场景，中英文/明暗主题/窄屏；留需求终验执行。
- [ ] [RULE-ui-detail-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]（cwd=., timeout=600s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。
- [ ] [RULE-ui-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]（cwd=., timeout=900s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-ui-detail#RULE-ui-detail-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |
| harness-ui#RULE-ui-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-029: 新增存储/Prompt 出口安全端到端

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-022, TASK-025, TASK-017, TASK-018
- **Source**: memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界, memory-redesign.design.md#3.8 可靠性与观测, memory-redesign.design.md#3.4.5 内容闸门与配额, memory-redesign.design.md#4.2 一次性迁移与切换
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: B-05, S-01, S-02
- **Files**: tests/acceptance/im_gateway/test_memory_redesign_security.py, tests/agent_runtime/test_memory_redesign_secrets.py, migrations/memory_redesign_data.py, tests/migrations/test_memory_redesign_preflight.py

### Description

按真实身份/API/DB/HTTP串联确认、管理写、隔离与canary；不把直调writer当最终API安全验证。

### Checklist

- [ ] [S-01][E2E] 编写伪造用户/tenant/来源、跨用户确认、无服务身份、非ADMIN全部拒绝场景。
- [ ] [S-02][E2E] 反查memory/history/proposal/job/receipt/tool审计/log与consolidation Prompt，密钥不漏。
- [ ] [B-05/B-20][integration] 修复TASK-008复核发现的迁移扫描同类缺口：直接检查原始字符串，不能用canonical JSON二次转义后扫描；敏感JSON/单引号键值文本必须预检拒绝。实际0019→0020临时库补RED/GREEN，断言保持0019且没有history/审计扩散；不重新打开已完成TASK-007，最终B-20仍归原owner并在需求终验复跑。
- [ ] E2E编码期不跑RED/GREEN；指定真实Live stack命令，证据保持planned至verify-e2e。
- [ ] [B-05][integration] 先写目标测试并真实记录RED→实现→GREEN；不得Mock：unit + PG，禁存内容；断言：value、key、quote、TASK_STATE 任一含密钥形态则拒绝；memory/history/proposal/log 中无 canary。
- [ ] [S-01][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：security，真实 API 身份与租户边界；断言：伪造 user/tenant/author、跨用户确认、无服务身份内部调用均拒绝；未授权 ADMIN 不写。
- [ ] [S-02][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：security，真实装配与 canary 检查；断言：标注引用与未经确认；密钥不进入新增表、历史、回执、日志、模型 consolidation Prompt。
- [ ] [RULE-secret-001] verifier：按元数据argv ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]（cwd=., timeout=300s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-05 | integration | unit + PG，禁存内容 | value、key、quote、TASK_STATE 任一含密钥形态则拒绝；memory/history/proposal/log 中无 canary | tests/agent_runtime/test_memory_redesign_secrets.py / test_b05_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_secrets.py"] | planned |
| S-01 | E2E | security，真实 API 身份与租户边界 | 伪造 user/tenant/author、跨用户确认、无服务身份内部调用均拒绝；未授权 ADMIN 不写 | tests/acceptance/im_gateway/test_memory_redesign_security.py / test_s01_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | planned |
| S-02 | E2E | security，真实装配与 canary 检查 | 标注引用与未经确认；密钥不进入新增表、历史、回执、日志、模型 consolidation Prompt | tests/acceptance/im_gateway/test_memory_redesign_security.py / test_s02_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_security.py"] | planned |
| harness-secret#RULE-secret-001 | integration | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

---

## TASK-030: 跨会话、真实确认、浏览器与崩溃验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-029, TASK-028, TASK-022
- **Source**: memory-redesign.design.md#2.5.2 验收场景与不得 mock 的边界
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: E-02, E-03, E-04, E-05, E-06
- **Files**: tests/acceptance/im_gateway/test_memory_redesign_e2e.py, e2e/tests/agent-management/memory-redesign.spec.ts, e2e/playwright.agent.config.ts

### Description

闭合默认设置的确认/偏好/续接/lineage与SIGKILL接管；agent域浏览器新增真实Runtime，沿用独立数据库/Redis/探针，不mock业务。

### Checklist

- [ ] [E-02/E-03/E-05/E-06][E2E] 编写真Gateway/Runtime/PG/Redis/HTTP/进程场景；多任务回复第2个真进入模型请求；A→B→C仍唯一，完成后无旧未决。
- [ ] [E-04][E2E] agent config原来只Console+模型探针，必须补Runtime/种子/临时库/连接配置；不拿布局mock测试替代。
- [ ] 编码期只登记与collect场景；functional全绿后verify-e2e统一执行，保留失败trace与实例清理。
- [ ] [E-02][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Gateway → Runtime → resume；断言：自然语言候选展示后用户真实确认，重连/重复提交不重复保存；拒绝后不产生 USER 事实。
- [ ] [E-03][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Gateway + 两个 conversation；断言：已确认中文偏好在 /new 后“你好”首轮可见；不要求 query 包含“中文”。
- [ ] [E-04][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：browser，真实 Console/Runtime/PG/Chrome；断言：创建、启禁用、删除、历史分页和来源状态本地化；越权不可操作。
- [ ] [E-05][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实 Gateway/Runtime/PG + 模型探针；断言：默认设置提炼未决项；/new后续接进入请求；多条→用户“第2个”→目标实际进入请求；A→B→C仍唯一，B完成后无旧未决；大checkpoint投递引用，引用超预算仅计丢弃。
- [ ] [E-06][E2E] 编写/collect场景，延期至verify-e2e，不在编码期跑RED/GREEN；不得Mock：acceptance，真实两个 Runtime 子进程；断言：SIGKILL claim 执行者，另一实例接管；故障与成功可见；持久写入效果最多一次。
- [ ] [RULE-test-001] verifier：按元数据argv ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]（cwd=., timeout=2400s）实际执行并登记；不得Mock的边界/断言见下方Contract。不得把未执行标verified。

### Acceptance Contract

| 场景ID / 规则 | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | E2E | acceptance，真实 Gateway → Runtime → resume | 自然语言候选展示后用户真实确认，重连/重复提交不重复保存；拒绝后不产生 USER 事实 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e02_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | planned |
| E-03 | E2E | acceptance，真实 Gateway + 两个 conversation | 已确认中文偏好在 /new 后“你好”首轮可见；不要求 query 包含“中文” | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e03_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | planned |
| E-04 | E2E | browser，真实 Console/Runtime/PG/Chrome | 创建、启禁用、删除、历史分页和来源状态本地化；越权不可操作 | e2e/tests/agent-management/memory-redesign.spec.ts / test_e04_memory_redesign（拟新增；实际实现前collect） | ["make","acceptance-e2e","DOMAIN=agent"] | planned |
| E-05 | E2E | acceptance，真实 Gateway/Runtime/PG + 模型探针 | 默认设置提炼未决项；/new后续接进入请求；多条→用户“第2个”→目标实际进入请求；A→B→C仍唯一，B完成后无旧未决；大checkpoint投递引用，引用超预算仅计丢弃 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e05_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | planned |
| E-06 | E2E | acceptance，真实两个 Runtime 子进程 | SIGKILL claim 执行者，另一实例接管；故障与成功可见；持久写入效果最多一次 | tests/acceptance/im_gateway/test_memory_redesign_e2e.py / test_e06_memory_redesign（拟新增；实际实现前collect） | ["uv","run","pytest","-q","tests/acceptance/im_gateway/test_memory_redesign_e2e.py"] | planned |
| harness-test#RULE-test-001 | E2E | 原verifier真实组件与本任务设计边界；不可mock业务API/数据库/浏览器 | 本任务行为满足该Rule且原回归不退化；以元数据命令裁决 | 原verifier指定真实测试（已在仓库；本轮未执行） | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | planned |

### Acceptance Evidence

计划阶段未执行测试或verifier。编码时逐项记录实际RED、GREEN、断言位置/真实边界；E2E保持planned/deferred，直到需求终验；没有manual或N/A确认。

### Log

- [2026-10-12] created (draft); 未启动编码。

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
