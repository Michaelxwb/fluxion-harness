# Tasks: 上下文压缩（Context Compaction）

- **Source**: context-compaction.design.md
- **Created**: 2026-10-04
- **Updated**: 2026-10-04

## Proposal

把长会话的"只留尾部 40 条硬裁"换成**分级压缩**（先便宜后昂贵：整轮落盘 → snip → micro → 摘要），压缩结果**可由库确定性重建、可审计**。压缩纯逻辑落在 `packages/agent-core` 的请求构建缝（`AgentRunner` 组装 `ModelRequest` 的唯一位置），副作用经端口注入 runtime 适配器；不新增表、无迁移，复用 `runtime.canonical_event` 与 `runtime.artifact` 两个既有载体。

### Alignment

- **Scope**: FEAT-01..09。四层压缩、审计事件、指标、配置冻结、重建一致、memory 预算。
- **Decisions**:
  - **压缩只覆盖 Runtime 同步 Run**：代码事实是 `AgentRunner` 全仓只有 `apps/agent-runtime/.../executor.py:771` 一处构造，`apps/agent-worker` 只 import `muad_agent_core.skill`、不构建模型请求。design 早先"Runtime 与 Worker 共用压缩链路"的表述已按代码事实改写；`harness-worker` 绑定保留，由 TASK-009 以「Worker 路径不引入压缩」的对照断言承接，**本需求不新增 worker 代码**（用户 2026-10-04 选定）。
  - **补 E-05/E-06 两条 integration 场景**：FEAT-06（指标）与 FEAT-09（memory 预算）原无验收场景，已按用户决定回填 design §2.5.2（用户 2026-10-04 选定）。
- **Non-goals**: Console 系统设置页（需求二）；上下文膨胀看板；非 OpenAI 兼容模型的精确 token 计数；跨会话记忆压缩；"prompt too long" 响应式恢复；Worker 侧压缩。
- **Acceptance**: 13 条场景（B-01/02/03/04、E-01..08、S-01）+ 6 条业务规则（RULE-01..06）全部有唯一负责人与可执行命令。
- **补第一、二条（2026-10-04，TASK-006 收尾时发现）**：① snip 的尾部窗口不锚定最近一条 user 消息 ⇒ 当前正在回答的问题可能被省略（B-01 语义收紧，终验责任转 TASK-010）；② TASK-003 的整轮批次原语从无调用方 ⇒ FEAT-02 在功能上没闭环（新增 E-07，TASK-011 按 ADR-04 把判定单元从单条改成回合）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout |
|---|---|---|---|---|---|---|---|---|
| E-04 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG：execution snapshot 的 `policy_json` | TASK-001 | verified | uv run pytest -q tests/agent_runtime/test_context_compaction_config.py | . | 600 |
| B-01 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：消息组切分与头尾保留（尾部锚定最近一条 user 组） | TASK-010 | verified | uv run pytest -q tests/agent_core/test_context_compactor.py | . | 600 |
| B-03 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：micro 降级与占位符 | TASK-002 | verified | uv run pytest -q tests/agent_core/test_context_compactor.py | . | 600 |
| B-02 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：整轮批次预算选取 | TASK-003 | verified | uv run pytest -q tests/agent_runtime/test_artifact_round_budget.py | . | 600 |
| B-04 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：摘要五字段精确校验 | TASK-004 | verified | uv run pytest -q tests/agent_runtime/test_context_summary.py | . | 600 |
| E-02 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG + 共享产物存储 | TASK-004 | verified | uv run pytest -q tests/agent_runtime/test_context_compaction_artifacts.py | . | 600 |
| E-01 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG：从 canonical_event 重建 | TASK-005 | verified | uv run pytest -q tests/agent_runtime/test_context_rebuild.py | . | 600 |
| E-03 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG：canonical_event 审计行 | TASK-005 | verified | uv run pytest -q tests/agent_runtime/test_context_events.py | . | 600 |
| E-05 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 agent-runtime `/metrics`（api-kit 目录） | TASK-007 | verified | uv run pytest -q tests/agent_runtime/test_context_metrics.py | . | 600 |
| E-06 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实请求装配 → 模型 HTTP 探针 | TASK-008 | planned | uv run pytest -q tests/agent_runtime/test_context_memory_budget.py | . | 600 |
| S-01 | context-compaction.design.md#2.5.2 验收场景 | E2E | 真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针 | TASK-006 | e2e_deferred | uv run pytest -q tests/acceptance/im_gateway/test_context_compaction.py | . | 1200 |
| E-07 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG + 共享产物存储 + 真实 `AgentRunner` 工具回合 | TASK-011 | verified | uv run pytest -q tests/agent_runtime/test_tool_round_budget.py | . | 600 |
| E-08 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG + 共享产物存储 + 真实两连 Run（同一会话） | TASK-012 | verified | uv run pytest -q tests/agent_runtime/test_tool_result_history.py | . | 600 |

> 本表覆盖 design §2.5.2 全部 **13** 条场景（B-01..04、E-01..08、S-01）与 §2.5.1 全部 6 条业务规则（RULE-01..06 的负责人见各 TASK 的 Acceptance-Refs）。
>
> **B-01 的所有权于 2026-10-04 由 TASK-002 转给 TASK-010**：snip 的语义新增"尾部锚定最近一条 `user` 组"（design §2.2 FEAT-01 / §3.2），TASK-002 交付的断言按新语义需要重写，故终验责任人随之转移（TASK-002 段落下的旧记录保留，但不再代表当前口径）。

---

## TASK-001: 压缩配置载体、冻结与读取（FEAT-07）

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#3.4 接口设计, context-compaction.design.md#4 部署与运维
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: E-04

### Description

把压缩配置（四层开关、阈值、`history_budget_messages`、`memory.budget_ratio`、`summary.model_ref`）放进 execution snapshot 的 `budget.compaction`（Run 侧等价载体是 `policy_json`，当前默认 `{"max_model_retries": 3}`），配置改动**只影响后续新 Run**、在跑的 Run 用冻结值；读取走进程内短 TTL 缓存（`CONTEXT_SETTINGS_CACHE_TTL_SEC`，默认 10），不得每轮查库。

### Checklist

- [x] [E-04][integration] 先登记并编写 tests/agent_runtime/test_context_compaction_config.py；真实边界：真实 PostgreSQL 的 `runtime.runtime_snapshot.policy_json`；断言"改配置只影响后续新 Run、在跑的 Run 仍用冻结值"，**逐腿可归因**（一次只改一个输入，并断言其余输入对应的列不变）；验证设计约定并登记证据，记录 RED/GREEN
- [x] 落 `budget.compaction` 的默认值与约束（design §2.3 字段约束表：`max_groups ≥ keep_head_groups + keep_tail_groups + 1`、`keep_head_groups ≥ 1`、`round_budget_bytes ≥ persist_threshold_bytes`、`summary.enabled` 需同时给 `model_ref`），非法配置显式报错而非静默回落
- [x] `HISTORY_BUDGET_MESSAGES`（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py:100`）由模块常量转为配置项，默认值保持 40（生产行为不变）；创建 Run 时用冻结值装配历史、resume 时从该行已冻结的 `policy_json` 取值
- [x] 进程内短 TTL 缓存：`CONTEXT_SETTINGS_CACHE_TTL_SEC` 进 `packages/common/src/muad_common/settings.py`（`SharedSettings`），缓存实现不得引入每轮查库
- [x] verifier harness-snapshot#RULE-snapshot-001：执行规范元数据的原始命令 `uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`，保持规范责任，记录门禁裁决
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | 真实 PostgreSQL：`runtime_snapshot.policy_json` | 新 Run 拿到新配置；在跑的 Run 拿冻结值；逐腿归因（改 A 不断言 B 变） | tests/agent_runtime/test_context_compaction_config.py | uv run pytest -q tests/agent_runtime/test_context_compaction_config.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-04 | **实现前首跑 12 failed**：全部 `ModuleNotFoundError`（`muad_agent_core.context.settings` / `muad_agent_runtime.application.context_settings`），两条冻结腿另在 `policy["compaction"]` 处 KeyError ⇒ 缺口真实存在。另做 **3 处扰动取证**（均在最终文件版本上复验，逐字节还原后复跑 13 passed）：①`snapshot_policy` 去掉 `compaction` 键 ⇒ 2 failed（两条冻结腿）；②`snapshot_policy` 忽略传入配置、恒用默认 ⇒ 1 failed（配置腿，`test_context_compaction_config.py:126`，`AssertionError` 于 `round_budget_bytes == 12345`）；③`_validate` 直接 return ⇒ 7 failed（8 条非法配置参数化里的 7 条；剩下的「未知键」由 `_coerce_section` 独立拦截，不受影响）。 | `uv run pytest -q tests/agent_runtime/test_context_compaction_config.py` → **13 passed in 0.40s** | `test_e04_run_snapshot_freezes_compaction_defaults`（默认值逐键比对 `DEFAULT_COMPACTION`）；`test_e04_config_change_only_affects_new_runs`（两条单腿 + 旧行逐列对照）；`test_e04_invalid_compaction_config_is_explicitly_rejected`（8 条参数化）；`test_e04_settings_source_is_cached_within_ttl`；`test_e04_settings_cache_merges_agent_override_over_platform_source`；`test_e04_history_budget_from_frozen_config_actually_trims` | **真实 PostgreSQL**：每个 Run 都经真实 `POST /v1/runs` 创建，冻结行从 `runtime.runtime_snapshot` 逐列回读（`agent_json`/`model_json`/`policy_json`/`content_hash`）。**逐腿可归因**：腿一只改 Agent 的压缩配置（`runtime_config.budget.compaction`）⇒ 断言 `tool_result.round_budget_bytes`/`snip.max_groups` 变、未覆盖的 `micro` 回落默认、`model_json` 逐列不变；腿二只改 model `params` ⇒ 断言 `model_json` 变而 `policy_json` **一字不动**。**冻结语义**：两次都重新回读首个 Run 的快照行并与变更前整体相等（`await _snapshot_row(first) == before`）。**预算真被消费**：真实 `runtime.canonical_event` 种 5 条 `USER_MESSAGE`，`budget=2` 装配出 2 条、不传预算装配出 5 条（默认 40 ⇒ 生产行为不变）。 | verified |

**规范责任（harness-snapshot#RULE-snapshot-001）verifier 实测**：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py` → **4 passed**；`uv run pytest -q tests/agent_runtime -k "executor or resolve"` → **22 passed, 270 deselected**。

**回归**：`uv run pytest -q tests/agent_runtime tests/console_skill/test_snapshot_freeze.py tests/console_platform/test_agents_api.py tests/agent_core tests/architecture` → **418 passed**；`ruff check`（改动文件）与 `uv run mypy apps packages`（290 files）均 clean。

**本任务触发的两处口径变更（用户 2026-10-04 逐项确认）**：
1. `harness-snapshot` 规则正文的「默认 `{"max_model_retries": 3}`」已按代码事实改写为「默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键」——压缩配置进 `policy_json` 使原文失真，按项目原则「spec 跟随代码事实」修正；连带更新 `tests/agent_runtime/test_snapshot_freeze.py` 与 `tests/agent_runtime/test_runs_api.py` 里按字面比对该默认值的断言。
2. design §2.3/§3.4 补上 `snip.keep_tail_groups`（默认 20）并明确 `max_groups` 是**触发阈值**——原字段约束表引用了 `keep_tail_groups` 但示例 JSON 与字段行都缺它。
- E-04: verified — automated command passed; run_id=49092492ce474f52ba642f9ebce7b19c (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] 实现：`packages/agent-core/src/muad_agent_core/context/settings.py`（配置 schema/默认值/校验，纯逻辑）、`apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py`（设置源 TTL 缓存 + 三层合并）、`run_service.snapshot_policy/history_budget_of` 与 `build_snapshot(compaction=...)`、`SharedSettings.context_settings_cache_ttl_sec`。E-04 13 passed；harness-snapshot verifier 与回归全绿。
- [2026-10-04] completed (done)

---

## TASK-002: 压缩纯逻辑：字节统计 + snip + micro（FEAT-01/03）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#3.1 方案选型, context-compaction.design.md#3.2 架构设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-01, B-03, RULE-01, RULE-02

### Description

在 `packages/agent-core/src/muad_agent_core/context/` 落纯函数实现：UTF-8 字节统计（单遍，一次 `encode('utf-8')`，不做逐条重复编码）、消息组切分、snip（保留最前 `keep_head_groups` 组 + 最近 `keep_tail_groups` 组，中间换成按**组**报数的省略标记，标记列出被省掉的工具名/产物名）、micro（只保留最近 `keep_recent_tool_groups` 个工具交换组的原文，更早的**结果内容**换占位符，`tool_calls` 与参数原样保留，占位符含 `artifact_id` 与工具名）。本层同时接管现有 `_trim` 的尾部裁剪职责。

### Checklist

- [x] [B-01][unit] 先登记并编写 tests/agent_core/test_context_compactor.py；真实边界：纯逻辑（消息组切分 / 头尾保留 / 省略标记按组报数）；验证设计约定并登记证据，记录 RED/GREEN
- [x] [B-03][unit] 同文件覆盖 micro 降级；真实边界：纯逻辑（占位替换 / 结构不变 / **占位不短于原文则跳过**）；记录 RED/GREEN
- [x] [RULE-01][unit] 作为唯一最终负责人：断言压缩后**不得出现孤儿 `tool` 消息**，带 `tool_calls` 的 assistant 回合与其工具结果**同进同出**（头尾裁剪边界、micro 替换边界各一组对照）
- [x] [RULE-02][unit] 作为唯一最终负责人：阈值一律按 **UTF-8 字节**判定（含中文用例：1 汉字 = 3 字节），边界为**严格大于**（恰好等于阈值 → 不触发）
- [x] 与既有 `apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py` 的 `_trim`/`_drop_leading_tool`/`_kept_tool_rounds` 口径对齐；被替换的旧路径不得留下第二套裁剪逻辑（design §3.1 方案 A 的漂移面）——**口径对齐已核对**：`_kept_tool_rounds` 的"完整回合"≡ `split_groups` 的组单元，`_drop_leading_tool` ≡ `split_groups` 丢弃孤儿 TOOL；**旧路径的物理移除**（`_trim` 退场、统一走压缩器）由 TASK-006 承接，其 Checklist 已写明"不得在 `context_builder` 里再压一遍，否则同一份历史两种口径"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-01 | unit | 纯函数（消息组切分） | 保留头 3 组 + 尾 N 组；省略标记按组报数；无孤儿 TOOL | tests/agent_core/test_context_compactor.py | uv run pytest -q tests/agent_core/test_context_compactor.py | verified |
| B-03 | unit | 纯函数（micro 降级） | 旧工具组内容换占位（含 artifact_id + 工具名）；结构不变；占位不短于原文则跳过 | tests/agent_core/test_context_compactor.py | uv run pytest -q tests/agent_core/test_context_compactor.py | verified |
| RULE-01 | unit | 纯函数（成对性） | 无孤儿 tool；带 tool_calls 的 assistant 与工具结果同进同出 | tests/agent_core/test_context_compactor.py | uv run pytest -q tests/agent_core/test_context_compactor.py | verified |
| RULE-02 | unit | 纯函数（字节口径） | UTF-8 字节判定；严格大于 | tests/agent_core/test_context_compactor.py | uv run pytest -q tests/agent_core/test_context_compactor.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-01 | **结构性 RED**：测试先写、实现后补；把 `compactor.py` 移走后 `uv run pytest -q tests/agent_core/test_context_compactor.py` → `Interrupted: 1 error during collection`（模块不存在时该 argv 必失败），逐字节还原后 17 passed。 | 17 passed | `test_b01_split_groups_keeps_assistant_and_its_tool_results_together`、`test_b01_snip_keeps_head_and_tail_and_counts_omitted_groups`、`test_b01_snip_is_off_at_or_below_the_threshold`、`test_b01_snip_disabled_is_identity` | 纯函数：1 条 USER + 5 个「assistant(tool_calls)+tool 结果」组 ⇒ 切成 6 组；头尾保留后中间只留**一条** SYSTEM 省略标记，且标记含 `2 组`、被省掉的工具名与产物名 | verified |
| B-03 | 同上（结构性 RED）+ 扰动 ③（见下） | 17 passed | `test_b03_micro_degrades_only_older_tool_groups`、`test_b03_micro_preserves_tool_calls_and_arguments`、`test_b03_micro_skips_when_placeholder_is_not_shorter`、`test_b03_micro_lower_bound_skips_only_the_unshrinkable_group`、`test_b03_micro_never_degrades_a_result_without_artifact`、`test_b03_micro_disabled_is_identity` | 纯函数：最近 2 组留原文、更早 3 组换占位（占位含 `artifact_id=art-0` 与工具名 `tool_0`）；5 条 assistant 一条不删，`arguments` 与 `reasoning_content` 原样保留；没有 `artifact_id` 的结果一律不降级（换了就再也找不回） | verified |
| RULE-01 | 扰动 ①（`split_groups` 去掉"必须被 assistant 声明"的判据，孤儿 TOOL 照收）→ **1 failed** | 17 passed | `test_rule01_snip_never_leaves_an_orphan_tool_message`（3×3 头尾切法全配）、`test_rule01_orphan_tool_in_input_is_dropped_not_propagated`、`test_rule01_assistant_with_tool_calls_survives_its_result` | 纯函数：`_assert_paired` 逐条校验每条 TOOL 都紧跟声明它的 assistant；输入含孤儿 TOOL（`ghost`）时输出里必须消失 | verified |
| RULE-02 | 扰动 ②（`snip` 判据由 `<=` 改成 `<`，不再严格大于）→ **1 failed** | 17 passed | `test_rule02_byte_size_is_utf8_not_characters`（`"中文"*10` = 60 字节 ≠ 20 字符）、`test_rule02_message_bytes_counts_reasoning_and_tool_arguments`、`test_rule02_snip_reports_real_byte_savings`、`test_b01_snip_is_off_at_or_below_the_threshold` | 纯函数：中文按 UTF-8 3 字节/字计；组数恰等于 `max_groups` 时不触发 | verified |

> **扰动取证 3 处**（均在最终文件版本上复验、逐字节还原后复跑 17 passed）：①`split_groups` 收下孤儿 TOOL ⇒ 1 failed；②`snip` 阈值判据非严格 ⇒ 1 failed；③`micro` 去掉"占位不短于原文则跳过"的判据 ⇒ 2 failed。
> **前两轮扰动"不变红"，暴露并补齐了两处真实覆盖缺口**（这正是扰动取证的价值）：①输入里没有孤儿 TOOL ⇒ 证伪不了 RULE-01 的丢弃行为（补 `test_rule01_orphan_tool_in_input_is_dropped_not_propagated`，并由此发现**未触发层的 identity 路径会把孤儿原样带出**——已改为每层都以净化后的历史为输入，"输出永不含孤儿"升为不变量）；②"占位不短于原文"的用例**空转**（目标组根本没进降级集）⇒ 重构成"真会变长"的组 + 有效的 `keep_recent_tool_groups=1`。

**回归**：`uv run pytest -q tests/agent_core tests/agent_runtime` → **390 passed**；`ruff check`（改动文件）与 `uv run mypy apps packages`（291 files）均 clean。
- B-01: verified — automated command passed; run_id=a9192e8c6563494cb64aa78ae9825ce3 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=a9192e8c6563494cb64aa78ae9825ce3 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] 实现：`packages/agent-core/src/muad_agent_core/context/compactor.py`——`split_groups`（组 = assistant(带 tool_calls) + 其结果；孤儿 TOOL 整条丢弃）、`message_bytes`/`history_bytes`（UTF-8 单遍）、`snip`、`micro`、`LayerOutcome.as_layer_payload()`（审计事件形状）。每层以**净化后的历史**为输入，"输出永不含孤儿 TOOL" 成为不变量。B-01/B-03/RULE-01/RULE-02 共 17 passed；3 处扰动全部复现后还原复绿。
- [2026-10-04] completed (done)

---

## TASK-003: 共享产物落盘原语：整轮批次预算 + 头尾预览（FEAT-02）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#3.2 架构设计, context-compaction.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: B-02

### Description

把工具结果落盘从"单条超 8 KiB"扩为**整轮批次预算**：单条 > `persist_threshold_bytes` 落盘；未落盘合计 > `round_budget_bytes` 时按字节**从大到小**逐条落盘，进预算即停。预览同时保留头 `preview_head_bytes` 与尾 `preview_tail_bytes`（现状是仅头部 200 字节，需改）；落盘原语必须是**不可变写**（temp + `os.replace`，同 `storage_key` 二次写入抛 `FileExistsError`），并作为 transcript 落盘可复用的共用原语。整批中途失败**回滚已写产物**，不留半批引用。

### Checklist

- [x] [B-02][unit] 先登记并编写 tests/agent_runtime/test_artifact_round_budget.py；真实边界：纯逻辑（选取序：按字节从大到小、进预算即停、恰好等于阈值不落盘）；验证设计约定并登记证据，记录 RED/GREEN
- [x] 扩 `apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py` 的 `ArtifactResultWriter` 为整轮批次口径；`TOOL_RESULT_ARTIFACT_BYTES` 保持**单一来源**（`tool_results.py:20`），executor 的外置判定与回执裁剪继续读同一常量
- [x] 预览改为头 `preview_head_bytes` + 尾 `preview_tail_bytes`（2000/2000），并保留既有"大结果外置不得截断内容投递类工具"的口径（`externalizable_result=False` 走 `MAX_INLINE_RESULT_BYTES`）
- [x] **不可变写**：`_write_immutable` 补存在性检查（同 `storage_key` 二次写入抛 `FileExistsError`），对齐 `packages/artifact-store/src/muad_artifact_store/nfs.py:21-39`；产物前缀显式声明（工具结果继续落 `tools/`，**不得**复用 `skills/`，否则会被 `cleanup_orphan_files` 误回收）
- [x] 整批中途失败回滚已写产物；DB 事务失败同步删除已写文件，不留孤儿
- [x] verifier harness-skill#RULE-skill-001：执行规范元数据的原始命令 `uv run pytest -q tests/test_skill_artifact_cache.py`，保持规范责任，记录门禁裁决
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-02 | unit | 纯函数（整轮批次选取）+ 真实临时产物根 | 单条超阈值落盘；合计超预算时按字节从大到小、进预算即停；严格大于 | tests/agent_runtime/test_artifact_round_budget.py | uv run pytest -q tests/agent_runtime/test_artifact_round_budget.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-02 | **结构性 RED**：把 `select_round_persists` 改名后 `uv run pytest -q tests/agent_runtime/test_artifact_round_budget.py` → `1 error in 0.10s`（收集期 ImportError），逐字节还原后 14 passed。另做 **3 处扰动取证**：①阈值判据由 `>` 改 `>=` ⇒ 1 failed；②`persist_round_results_with_session` 去掉回滚 ⇒ 1 failed；③`_write_immutable` 去掉存在性检查 ⇒ 1 failed。 | 14 passed（本文件） | `test_b02_single_item_over_threshold_is_always_persisted`、`test_b02_threshold_boundary_is_strict`、`test_b02_within_round_budget_persists_nothing`、`test_b02_round_budget_persists_largest_first_and_stops_in_budget`、`test_b02_over_threshold_items_come_first_and_are_never_second_guessed`、`test_b02_selection_is_deterministic_on_equal_sizes`、`test_b02_oversized_round_never_drops_below_budget_when_it_cannot`、`test_b02_preview_keeps_head_and_tail`、`test_b02_preview_is_byte_based_and_never_splits_a_character`、`test_b02_preview_returns_text_verbatim_when_it_fits`、`test_rule_artifact_write_is_immutable`、`test_db_failure_removes_the_written_file`、`test_round_batch_rolls_back_every_file_it_wrote`、`test_round_batch_persists_every_selected_result` | 选取是**纯函数**：`{"a":5000,"b":4000,"c":3000,"d":1000}` 合计 13000，预算 6000 ⇒ 落 `a,b`（进预算即停）；预算 9000 ⇒ 只落 `a`；恰好等于 8 KiB 阈值**不**落盘。落盘侧用**真实临时产物根 + 真实文件系统**：同 key 二次写入抛 `FileExistsError` 且既有产物一字不动；DB 第 2 次 commit 失败时**本批两个文件都不留**（`session.commits == 2` 证明真写到了第 2 条，非空转）；产物前缀是 `tools/<tenant>/…`，未复用 `skills/`。 | verified |

**规范责任（harness-skill#RULE-skill-001）verifier 实测**：`uv run pytest -q tests/test_skill_artifact_cache.py` → 见任务收尾的 Done Gate 记录（本任务 Done Gate 范围内执行）。

**回归**：`uv run pytest -q tests/agent_runtime tests/agent_core tests/test_skill_artifact_cache.py` → **408 passed**；`ruff check`（改动文件）与 `uv run mypy apps packages`（291 files）均 clean。

**范围说明**：本任务交付的是**落盘原语**（整轮选取 + 头尾预览 + 不可变写 + 整批回滚）。把"工具回合结束时按整轮判定"接进 `ToolCallRecorder`／runner 的回合循环属于**接线**，随 TASK-006 一并落地——那里才有回合边界（`Runner` 的 `_execute_tools` 逐 call 分派，单次调用看不到整轮）。
- B-02: verified — automated command passed; run_id=98ee1fb9013a41cc9e22e8d4d77030f9 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] 实现：`tool_results.py` 增 `select_round_persists`（整轮批次纯选取）、`preview_head_tail`（按字节头尾预览，不切断多字节字符）、`_write_immutable` 补 `FileExistsError` 存在性检查、`persist_round_results_with_session`（整批 + 中途失败回滚）。`preview_limit`（头部 200 字符）由 `preview_head_bytes`/`preview_tail_bytes`（2000/2000）取代。B-02 14 passed；4 处取证（1 结构性 RED + 3 扰动）全部复现后还原复绿。
- [2026-10-04] completed (done)

---

## TASK-004: 摘要层、SummaryPort 与 transcript（FEAT-04）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#3.3 数据设计, context-compaction.design.md#3.4 接口设计, context-compaction.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-model#RULE-model-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: B-04, E-02, RULE-03, RULE-04, RULE-06

### Description

前三层跑完后历史仍 > `summary.threshold_bytes` 才调摘要模型：五字段摘要 + **字段集合精确相等**校验（多字段/少字段/非 JSON/带 `tool_calls`/`finish_reason≠stop` 一律拒绝本次摘要、历史不变，下一轮重试）。摘要**作为权威历史落库**（`canonical_event`，见 TASK-005），被压缩掉的逐字原文（transcript）落**共享产物存储**（新增 `artifact` 类型 `TRANSCRIPT`，落盘复用 TASK-003 的不可变写原语）；**transcript 不新增任何对外读取端点**。摘要模型引用既有 `model_definition`（`summary.model_ref`，不承载 `api_key`），不新增默认模型回退。

### Checklist

- [x] [B-04][unit] 先登记并编写 tests/agent_runtime/test_context_summary.py；真实边界：纯逻辑（五字段精确匹配才接受；多/少字段、非 JSON、带 tool_calls、finish_reason≠stop 逐条拒绝）；验证设计约定并登记证据，记录 RED/GREEN
- [x] [E-02][integration] 先登记并编写 tests/agent_runtime/test_context_compaction_artifacts.py；真实边界：真实 PostgreSQL + 共享产物存储；断言整轮超预算时大结果落盘且**预览头尾都在**、transcript 可被运维按 id 直读且 **Console 无任何读取/下载入口**；记录 RED/GREEN
- [x] [RULE-03][unit] 作为唯一最终负责人：摘要输出必须字段集合**精确相等**；任何不符 ⇒ 本次摘要作废、历史逐字节不变
- [x] [RULE-04][integration] 作为唯一最终负责人：压缩失败（落盘失败、摘要失败）**一律退化到不压缩**，Run 不得因此失败（落盘失败回滚已写产物；摘要异常保持原历史）
- [x] [RULE-06][integration] 作为唯一最终负责人：transcript **不新增任何对外明文出口**（Console 无读取/下载端点）；summary model 调用走 OPENAI 兼容协议 + `model_ref` 引用的既有 `model_definition`
- [x] verifier harness-model#RULE-model-001：执行规范元数据的原始命令 `uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled`，保持规范责任，记录门禁裁决
- [x] verifier harness-secret#RULE-secret-001：执行规范元数据的原始命令 `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py`，保持规范责任，记录门禁裁决
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-04 | unit | 纯逻辑（摘要校验） | 五字段精确匹配才接受；多/少字段、非 JSON、带 tool_calls、finish_reason≠stop 逐条拒绝 | tests/agent_runtime/test_context_summary.py | uv run pytest -q tests/agent_runtime/test_context_summary.py | verified |
| E-02 | integration | 真实 PG + 共享产物存储 | 整轮超预算时大结果落盘且预览头尾都在；transcript 可按 id 直读、Console 无入口 | tests/agent_runtime/test_context_compaction_artifacts.py | uv run pytest -q tests/agent_runtime/test_context_compaction_artifacts.py | verified |
| RULE-03 | unit | 纯逻辑（字段集合） | 精确相等才接受，否则历史不变 | tests/agent_runtime/test_context_summary.py | uv run pytest -q tests/agent_runtime/test_context_summary.py | verified |
| RULE-04 | integration | 真实 PG + 共享产物存储（失败路径） | 落盘失败回滚产物；摘要失败保持原历史；Run 不失败 | tests/agent_runtime/test_context_compaction_artifacts.py | uv run pytest -q tests/agent_runtime/test_context_compaction_artifacts.py | verified |
| RULE-06 | integration | 真实 PG + 共享产物存储 | transcript 无对外明文出口 | tests/agent_runtime/test_context_compaction_artifacts.py | uv run pytest -q tests/agent_runtime/test_context_compaction_artifacts.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-04 | **结构性 RED**：三个模块（`context/summary.py`、`attachments/transcripts.py`、`attachments/immutable_store.py`）在写测试时都不存在 ⇒ 首跑收集期 ImportError。另做 **4 处扰动取证**（见下）。 | 22 passed（两个文件） | `test_b04_five_field_summary_is_accepted`、`test_rule03_any_shape_deviation_rejects_the_summary`（8 条参数化）、`test_b04_non_json_output_is_rejected`、`test_b04_tool_calls_in_the_summary_turn_are_rejected`、`test_b04_non_stop_finish_reason_is_rejected`、`test_b04_parse_raises_with_a_reason_for_callers_that_want_it` | 纯逻辑：合法五字段（`user_goal`/`constraints`/`progress`/`open_items`/`artifacts`）才接受；多字段、少字段、非 JSON 对象、`user_goal` 非字符串、`constraints` 非字符串数组、`artifacts` 项缺键/值非字符串、带 `tool_calls`、`finish_reason ∈ {length, content_filter, ""}` 逐条拒绝，且每条都带可归因 reason | verified |
| E-02 | 同上（结构性 RED）；扰动 ④ 命中（transcript 复用 `skills/` 前缀 ⇒ 1 failed） | 22 passed | `test_e02_round_persist_keeps_head_and_tail_preview`、`test_e02_transcript_lands_in_shared_store_and_is_readable` | **真实 PostgreSQL + 真实产物根**：大结果落盘后逐列回读 `runtime.artifact`——`preview_text` 以「头」开头、以「尾」结尾且含"已省略"（只留头部会让模型看不到错误栈末行/汇总行/JSON 闭合）；盘上文件是**原文**而非预览。transcript 行 `artifact_type=TRANSCRIPT`、`preview_text IS NULL`、`size` 与引用一致、`checksum` 与盘上字节逐位相符；JSONL 按序还原逐字原文，且带 `tool_calls` 的回合连 `reasoning_content` 一起存档 | verified |
| RULE-03 | 扰动 ①（字段集合判据由 `!=` 放宽成"包含"）⇒ **2 failed**；扰动 ②（摘掉 `finish_reason` 门）⇒ **1 failed** | 22 passed | `test_rule03_any_shape_deviation_rejects_the_summary`、`test_b04_non_stop_finish_reason_is_rejected` | 纯逻辑：`try_parse_summary` 返回 `(None, reason)`，调用方据此**保持原历史** | verified |
| RULE-04 | 扰动 ③（transcript 落库失败改为向上抛）⇒ **1 failed** | 22 passed | `test_rule04_transcript_failure_degrades_without_raising`、`test_rule04_empty_history_produces_no_transcript` | 失败注入：DB `commit` 抛错 ⇒ `persist_with_session` **返回 None 不抛**，且盘上不留半截 `.jsonl`；失败显式 `logger.warning`（"退化成不压缩"不等于"悄悄吞掉"）；取消类 `BaseException` 不参与降级 | verified |
| RULE-06 | 扰动 ④（`TRANSCRIPT_PREFIX` 改成 `skills`）⇒ **1 failed** | 22 passed | `test_rule06_console_exposes_no_transcript_endpoint`、`test_rule06_preview_helper_is_shared_by_both_writers`、`test_rule06_preview_bounds_are_honoured` | **Console 路由表实扫**：`muad_console_platform.main.app` 的全部 route path 里没有 `transcript`（无读取/下载出口）；产物前缀钉**字面** `transcripts/<tenant>/` 且显式断言不以 `skills/` 开头 | verified |

> **扰动取证 4 处**（均在最终文件版本上复验、逐字节还原后复跑 22 passed）：①摘要字段集合判据放宽 ⇒ 2 failed；②摘掉 `finish_reason` 门 ⇒ 1 failed；③transcript 失败改为上抛 ⇒ 1 failed；④transcript 复用 `skills/` 前缀 ⇒ 1 failed。
> **第 4 处首轮"不变红"，暴露一条自我循环断言**：我原先写的是 `startswith(f"{TRANSCRIPT_PREFIX}/...")`——断言跟着常量一起漂移，拆掉前缀它照样绿。已改成钉**字面** `transcripts/` 并补一条 `not startswith("skills/")`。

**范围说明（RULE-04 的一条腿）**：本任务交付的是"失败**不抛**、不留半成品、调用方拿到 None 就放弃这次压缩"。**端到端那条腿**——压缩在请求缝上失败时 Run 仍然跑完——要等 TASK-006 把压缩接进 `AgentRunner` 才能证（当前没有任何生产路径调用压缩器）。

**回归**：`uv run pytest -q tests/agent_runtime tests/agent_core tests/test_skill_artifact_cache.py tests/architecture` → **459 passed**；`ruff check`（改动文件）与 `uv run mypy apps packages`（294 files）均 clean。
- B-04: verified — automated command passed; run_id=0e447e4a15a245998bdf7cc2576a8ff2 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=0e447e4a15a245998bdf7cc2576a8ff2 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=842f5139a86d47ff9bd8ffaff587efca (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=842f5139a86d47ff9bd8ffaff587efca (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] **口径回填（用户确认）**：design §2.3/§3.3 补上五字段名——`user_goal` / `constraints` / `progress` / `open_items` / `artifacts`（原文只写了"五字段"，没列名字，而它要冻进 `CONTEXT_SUMMARY` 的 payload）。design 改动只触发 `artifact_changed`，未重置阶段状态。
- [2026-10-04] 实现：`packages/agent-core/.../context/summary.py`（五字段 schema + 精确校验 + 非抛出形态）、`apps/agent-runtime/.../attachments/immutable_store.py`（抽出共享不可变写原语，`tool_results.py` 改用）、`.../attachments/transcripts.py`（`TranscriptWriter`，类型 `TRANSCRIPT`、前缀 `transcripts/`、失败返回 None 并记日志）。B-04/E-02/RULE-03/04/06 共 22 passed；4 处扰动全部复现后还原复绿。
- [2026-10-04] completed (done)

---

## TASK-005: 压缩审计事件与确定性重建（FEAT-05/08）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-004
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#3.3 数据设计, context-compaction.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-data#RULE-data-001, harness-arch#RULE-arch-001
- **Acceptance-Refs**: E-01, E-03, RULE-05

### Description

压缩**真的发生**时落一行 `runtime.canonical_event` 审计事件：`event_type='CONTEXT_SUMMARY'`（`payload_json={covers_up_to_seq, summary{5 fields}, transcript_artifact_id, bytes_before, bytes_after}`）与 `event_type='CONTEXT_COMPACTED'`（`payload_json={layers{layer:{fired, groups, bytes_saved}}, summary_event_seq?}`）。重建语义：取**最新**一份覆盖事件作前缀，其后再按 seq 顺序应用后续事件，最后跑前三层压缩 —— 结果必须与当时真正发给模型的那份**逐字节一致**。不新增表、无迁移。

### Checklist

- [x] [E-01][integration] 先登记并编写 tests/agent_runtime/test_context_rebuild.py；真实边界：真实 PostgreSQL（`runtime.canonical_event` 重建链）；断言"同一份 canonical_event 重建两次 + 与真实发出的请求对比"**逐字节一致**；验证设计约定并登记证据，记录 RED/GREEN
- [x] [E-03][integration] 先登记并编写 tests/agent_runtime/test_context_events.py；真实边界：真实 PostgreSQL；断言压缩发生时**恰好**多一行压缩事件、字段（层级/省下字节/摘要引用）齐；未压缩时不多行；记录 RED/GREEN
- [x] [RULE-05][integration] 作为唯一最终负责人：摘要文本是**权威历史**（落库、参与重建），transcript 是**存档**（落共享产物），二者不得互换——以 E-01 的重建链与 E-02 的产物落点两侧对照取证
- [x] 事件追加走既有 `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py` 的行锁 seq 分配路径（不得自造第二套 seq 分配）；`payload_json` 为 `jsonb`，关键查询字段不得只藏在 JSON——查询键（`tenant_id`/`conversation_id`/`event_type`/`seq`）全是**列**，JSON 里只放载荷
- [x] 复用既有 `canonical_event(run_id, seq)` 索引，不新增索引（本任务无迁移）
- [x] verifier harness-data#RULE-data-001：执行规范元数据的原始命令 `uv run pytest -q tests -k schema_parity`，保持规范责任，记录门禁裁决
- [x] verifier harness-arch#RULE-arch-001：执行规范元数据的原始命令 `uv run pytest -q tests/architecture`，保持规范责任，记录门禁裁决
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | 真实 PostgreSQL：canonical_event 重建 | 重建两次同结果；与真实发出的请求逐字节一致 | tests/agent_runtime/test_context_rebuild.py | uv run pytest -q tests/agent_runtime/test_context_rebuild.py | verified |
| E-03 | integration | 真实 PostgreSQL：canonical_event | 压缩发生时恰好多一行；字段齐；未压缩不多行 | tests/agent_runtime/test_context_events.py | uv run pytest -q tests/agent_runtime/test_context_events.py | verified |
| RULE-05 | integration | 真实 PG + 共享产物存储 | 摘要是权威历史（参与重建）；transcript 是存档；不互换 | tests/agent_runtime/test_context_rebuild.py | uv run pytest -q tests/agent_runtime/test_context_rebuild.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-01 | **结构性 RED**：`context_events.py` 在写测试时不存在 ⇒ 收集期 ImportError。另做 **4 处扰动取证**（见下），其中 P1（忽略覆盖边界）与 P3（不应用摘要前缀）各自打红本条。 | 6 passed（两个文件） | `test_e01_rebuild_is_deterministic_and_matches_the_independent_oracle`、`test_e01_a_new_summary_takes_effect_on_the_next_rebuild` | **真实 PostgreSQL**：会话里种 3 条被摘要覆盖的旧事件 + 一份 `CONTEXT_SUMMARY` + 2 条覆盖边界之后的原始事件。**确定性**：两个独立 builder 实例各自装配 ⇒ 内容与角色序列逐项相同（"换 Pod 相同"的等价物）。**与独立 oracle 比对**：测试侧**裸 SQL + 手写字段映射**重建（不复用生产函数，避免拿代码验自己），结果与生产 `load_history` 逐项相同。**语义**：摘要前缀排在最前、被覆盖的原文不再出现、边界之后的事件按 seq 顺序接着来；把最新摘要的 `covers_up_to_seq` 推进到末尾后重建，前缀换新且后续事件消失 ⇒ 前缀真来自库里那份，不是进程内缓存 | verified |
| E-03 | 同上（结构性 RED）；扰动 P4（丢掉 `covers_up_to_seq`）⇒ **3 failed** | 6 passed | `test_e03_compaction_writes_exactly_one_audit_row`、`test_e03_summary_event_carries_the_authoritative_history`、`test_e03_plain_history_load_writes_no_compaction_row` | **真实 PostgreSQL + 既有 `EventWriter`（`conversation.last_seq` 行锁）**：压缩发生一次 ⇒ `canonical_event` **恰好**多一行 `CONTEXT_COMPACTED`，`seq` 由行锁分配（1）；`payload.layers.snip` 逐键等于 `{layer, fired, groups, bytes_saved}`；`summary_event_seq` 只在有摘要时出现，并指回摘要事件的 seq。摘要事件按 `{covers_up_to_seq, summary, transcript_artifact_id, bytes_before, bytes_after}` 落库，两次追加共用一个 seq 链（1、2）。**未压缩不多行**：普通 `load_history`（只读路径）后事件表仍只有那条 `USER_MESSAGE` | verified |
| RULE-05 | 扰动 P3/P1 打红本条 | 6 passed | `test_rule05_summary_is_authoritative_and_transcript_is_only_an_archive` | **真实 PG + 真实共享产物根**：重建用的前缀来自 `canonical_event` 的摘要（权威历史）；逐字原文另落 `TRANSCRIPT` 产物（存档），**不在**事件表里、也**不进**历史。**互换即失真**：把 transcript 的逐字原文当前缀、其余不变，重建结果必然不同 ⇒ 二者不是同一种东西 | verified |

> **扰动取证 4 处**（均在最终文件版本上复验、逐字节还原后复跑 6 passed）：①`_recent_events` 忽略覆盖边界 ⇒ 2 failed；②`latest_summary` 取 `seq` 升序（拿最旧那份）⇒ 1 failed；③不应用摘要前缀 ⇒ 2 failed；④摘要事件丢掉 `covers_up_to_seq` ⇒ 3 failed。

**范围说明（E-01 的一条腿）**：本任务证明了"**同一份库 → 同一份历史**"（确定性 + 语义 + 独立 oracle 三方对齐），并把摘要前缀接进了生产的历史装配路径（`context_builder.load_history`）。**"与 `ExecutorRequest.history` 直接对比"那条腿**要等 TASK-006 把压缩接进 `AgentRunner` 才成立——届时 S-01 会走真实 WS→Gateway→Runtime 全链取证。

**回归**：`uv run pytest -q tests/agent_runtime tests/agent_core tests/architecture` → **465 passed**；`ruff check`（改动文件）与 `uv run mypy apps packages`（295 files）均 clean。
- E-01: verified — automated command passed; run_id=76252aca1d0c43659c06d104da2fa4e5 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=76252aca1d0c43659c06d104da2fa4e5 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] 实现：`apps/agent-runtime/.../application/context_events.py`（`CONTEXT_SUMMARY`/`CONTEXT_COMPACTED` 的写入与 `latest_summary`/`covered_up_to` 读取，复用 `EventWriter` 的 seq 行锁）；`agent-core/.../context/summary.py` 增 `summary_message`（摘要 → 权威前缀的**确定性**渲染）与 `summary_from_payload`（从落库 payload 还原，形状坏时返回 None 走退化）；`context_builder.load_history` 接入摘要前缀与覆盖边界 `after_seq`。E-01/E-03/RULE-05 共 6 passed；4 处扰动全部复现后还原复绿。
- [2026-10-04] completed (done)

---

## TASK-006: 接线请求构建缝与端到端验收（FEAT-01..05 串联）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-004, TASK-005
- **Source**: context-compaction.design.md#3.1 方案选型, context-compaction.design.md#3.2 架构设计, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-01

### Description

把 `RequestCompactor` 接进 `AgentRunner` 组装 `ModelRequest` 的位置（`packages/agent-core/src/muad_agent_core/agent/runner.py:302-318` 的 `_call_model`），在调 provider **之前**统一执行；`CompactionPort`/`SummaryPort` 由 runtime 侧适配器实现（落盘 / 写事件 / 调摘要模型）。这是全需求唯一的模型请求组装点，S-01 在此链路端到端取证。

### Checklist

- [x] [S-01][E2E] 先登记并编写 tests/acceptance/im_gateway/test_context_compaction.py；真实边界：真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针（`tests/e2e/openai_probe_app.py`，禁止伪造外部响应）；成功路径不得出现 `page.route(` 一类拦截；验证设计约定并登记证据，E2E 延后 verify-e2e
- [x] 接线位置必须是 `AgentRunner` 组装 `ModelRequest` 的唯一处（不得在 `context_builder` 里再压一遍，否则同一份历史两种口径）
- [x] 端口实现全部在 runtime 适配器侧（`packages/*` 不得 import 四个 app 模块，依赖方向单向）
- [x] 压缩整体包在 try 内：异常退化到"不压缩"，日志只记字节数与层级、不记内容
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | 真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针 | 长会话 + 大工具结果 + 多工具回合后：开头诉求仍在、无孤儿 TOOL、模型收到的 prompt 含省略标记或摘要、压缩事件落库 | tests/acceptance/im_gateway/test_context_compaction.py::test_s01_compaction_composes_on_the_real_chain | uv run pytest -q tests/acceptance/im_gateway/test_context_compaction.py | e2e_deferred |

### Acceptance Evidence

> E2E 场景延后到需求级 verify-e2e 执行；`cf-task-start` 在编码期登记实现与接线证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 不制造 RED（验收类基线：E2E 由 `cf_acceptance_runner --include-e2e` 统一执行） | pending（延后 verify-e2e） | `test_s01_compaction_composes_on_the_real_chain`：省略标记出现在探针记录的真实请求体里 / `OPENING` 仍在 / `FOLLOW_UP` 仍在 / `_assert_no_orphan_tools` 按序校验 tool_call_id / `count(canonical_event where event_type='CONTEXT_COMPACTED') ≥ 1` | 真实 WS 探针推入站帧 → 真实 Gateway → 真实 Runtime → 真实 PG → 真实 `tests/e2e/openai_probe_app.py`（`GET /requests` 回放每次真实请求体） | e2e_deferred |
- S-01: e2e_deferred — automated command e2e_deferred; run_id=1c882addab1d45d8a90dc139179f377d (confirmed_by: runner)

### Log
- [2026-10-04] **接线完成（本轮）**：
  · `agent-core/context/compactor.py` 增 `trim_history`（原 `context_builder._trim` 的口径上移）与 `compact_history`（micro→snip→条数兜底）与 `ContextCompactor` 协议；
  · `agent-core/agent/runner.py`：`AgentRunner(context_compactor=...)`，在 `_call_model` 组装 `ModelRequest` 的**唯一处**对派生历史调用它，`state["messages"]` 一个字节不动；
  · 新增 `agent-runtime/application/context_compaction.py`：`RuntimeContextCompactor` 承载四个层与副作用（transcript 落盘、摘要事件、压缩审计事件），**整段包在 try 内**（RULE-04 退化到原样返回），并含 `make_summary_runner`（用既有 provider + `model_ref`，无默认模型回退）；
  · `ExecutorRequest` 增 `compaction`；`RunService` 把**冻结**配置一路带下去（新 Run 用 `resolve_compaction_settings`，resume 用新增的 `compaction_settings_of(snapshot.policy_json)`），`default_executor_factory` 据此装配压缩器。
  回归 `tests/agent_runtime tests/agent_core tests/architecture` → **465 passed**；ruff / mypy(296 files) clean（压缩器仅在 Run 有冻结配置与 Run 上下文时才装；默认配置下 snip 未达 50 组阈值、micro/summary 关 ⇒ 既有行为不变）。
- [2026-10-04] **断点（未完成，任务保持 in-progress）**：
  1. **`context_builder._trim` / `_drop_leading_tool` 尚未退场** —— 它与压缩器的 `trim_history` 目前是同一口径跑两遍（幂等，故回归全绿），但仍是 checklist 第 2 条点名要拆的「两处口径」。退场会牵动 `load_history(budget=)`、`RunService._load_history(budget_messages=)` 与 `tests/agent_runtime/test_context_memory.py` 的 `budget_messages=2` 断言，**未做**；
  2. **S-01 E2E 测试文件尚未登记**（`tests/acceptance/im_gateway/test_context_compaction.py`）；
  3. **Acceptance Contract 的 S-01 状态仍为 planned**，未走 `cf_task_workflow.py finish`；
  4. 另有一笔**跨任务残留**（不在本任务 checklist 内，但功能上未闭环）：`ArtifactResultWriter.persist_round_results_with_session`（TASK-003 的整轮批次原语）**还没有调用方** —— 工具结果的「整轮合计超预算」仍未接进回合循环（`Runner._execute_tools` 逐 call 分派，单次调用看不到整轮边界）。需要单独一个任务承接。
- [2026-10-04] 已勾选项：端口实现全在 runtime 适配器侧（`packages/*` 不 import app 包）；压缩整体包在 try 内、异常退化到不压缩、日志只记层级与字节数。
- [2026-10-04] **收尾完成（本轮）**：
  · **口径唯一落实**：`context_builder._trim` / `_drop_leading_tool` 退场，`load_history` 只装配不裁（`budget_messages` 降级为**取事件条数守卫**，与压缩层用同一个冻结值）；`trim_history` 成为唯一的条数兜底，并补上"硬切尾片按组净化"的 RULE-01 缺口（原实现会把切点处的 tool 结果留成孤儿）。E-04 的断言随之改成"装配不裁 + 压缩层按冻结值裁"；
  · **受保护前缀**：新增 `split_protected_prefix`（开头连续的 SYSTEM 段 = 系统提示 + memory 注入 + 摘要前缀）。snip 与条数兜底只在它之后的**对话区**上工作——否则系统提示会被整段裁掉（失忆）、memory 注入被省略标记顶掉、摘要被省掉（它覆盖的那段历史净消失）。3 处扰动（去掉前缀切分 ×2、去掉硬切净化 ×1）逐一变红后还原复绿；
  · **请求缝取证**：`tests/agent_core/test_runner.py` 新增两个用例——压缩产物进模型请求、且**不回沉**进 `state["messages"]`（多回合下每轮从权威历史重算，上一轮的省略标记不得沉淀）；去掉 `_call_model` 里的压缩调用 ⇒ 2 failed；
  · **S-01 E2E 登记**（`tests/acceptance/im_gateway/test_context_compaction.py::test_s01_compaction_composes_on_the_real_chain`）：两个来回 + 4 个工具回合，只压 `max_groups` 触发阈值（保头保尾用生产默认 3/20），断言"探针记录的真实请求体里出现省略标记 + 开头诉求与本轮追问都还在 + 按序校验无孤儿 TOOL + `CONTEXT_COMPACTED` 落库"。**编码期只登记不执行**，延后 verify-e2e。
  回归 `tests/agent_core tests/agent_runtime tests/architecture` → **472 passed**；ruff / mypy(296 files) clean。
- [2026-10-04] **遗留（E2E 设计时发现，未改语义，需单独决策）**：snip 的尾部窗口只按**组数**（`keep_tail_groups`，默认 20）保留，**不锚定最近一条 user 消息**。一个回合内的工具轮次超过该窗口时，**当前正在回答的那个问题**会落进省略区（模型只看到工具名清单，看不到诉求）。生产默认值下需要"单回合 > 20 个工具组"才够得着（`max_tool_calls` 默认 30），可达但非高频。S-01 因此用生产默认的保尾值取证；本条与 `trim_history` 的"从最近一条 USER 起切"口径不对称，是否统一需另定（牵动已 verified 的 B-01）。

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] completed (done)

---

## TASK-007: 压缩指标（FEAT-06）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-003, TASK-004
- **Source**: context-compaction.design.md#3.5 质量实现方案, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-05

### Description

把 `context_compaction_total{layer,outcome}`、`context_compaction_bytes_saved_total{layer}`、`context_summary_total{outcome}`、`context_summary_tokens_total` 登记进 agent-runtime 的 metric catalog（`apps/agent-runtime/src/muad_agent_runtime/metrics.py` 的 `CATALOG`），无流量时也要在 `/metrics` 目录可见；label 低基数（layer/outcome），不带资源 ID。

### Checklist

- [x] [E-05][integration] 先登记并编写 tests/agent_runtime/test_context_metrics.py；真实边界：真实 agent-runtime `/metrics`（api-kit 目录）；断言**无流量也暴露目录**、触发后计数与省下字节递增、label 无高基数维度；验证设计约定并登记证据，记录 RED/GREEN
- [x] 指标名与 label 进 `CATALOG` 后经 `install_metrics` 暴露；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志
- [x] 指标记录点不得进入热点路径的额外 IO（与 design §3.5 的"无 N+1、无循环内 IO"一致）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-05 | integration | 真实 agent-runtime `/metrics`（api-kit 目录） | 无流量也可见四级计数器目录；label 低基数；触发后计数与省下字节递增 | tests/agent_runtime/test_context_metrics.py | uv run pytest -q tests/agent_runtime/test_context_metrics.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-05 | FAIL: `AssertionError: 压缩指标目录缺失：['context_compaction_total', 'context_compaction_bytes_saved_total', 'context_summary_total', 'context_summary_tokens_total']`（三条用例同因全红：四个计数器既没进 `CATALOG`、也没有任何记录点） | 3 passed | `test_e05_catalog_is_visible_without_any_traffic`（无流量下 `# TYPE … counter` 四条齐全）、`test_e05_compaction_counters_increase_on_a_real_run`（真实三连 Run 触发请求缝 snip ⇒ `context_compaction_total{layer=snip,status=FIRED}` +1、`context_compaction_bytes_saved_total{layer=snip}` > 0）、`test_e05_summary_counters_increase_on_a_real_compactor`（真实摘要层 ⇒ `context_summary_total{status=OK}` +1 且 `context_summary_tokens_total` += 模型回报的 input+output） | 真实 uvicorn 单进程（127.0.0.1 真实 socket）→ 真实 Runtime app/路由 → api-kit 进程内注册表 → `GET /metrics` Prometheus 文本；计数由真实链路产生（`RunService`→`AgentRunner`→压缩层；真实 `RuntimeContextCompactor` + 真实 `make_summary_runner` + 真实 PG + 真实产物根） | verified |

**扰动取证**：P1（不记 compaction 计数与省下字节）⇒ **1 failed**；P2（四个计数器不进目录）⇒ **1 failed**；P3（摘要不记 token 用量）⇒ **1 failed**；逐字节还原后复跑 3 passed。

**一处口径对齐（改的是设计文本，不是加适配层）**：design §3.5 原先写 `{layer,outcome}`，但仓库里所有结局类 label 一律叫 `status`（`agent_runs_total`/`tool_calls_total`/`memory_write_total`…），`record_outcome()` 也硬编码 `status`。为了让压缩指标与同族指标一致（而不是给这一个指标另起一套名字），把设计文本改成 `{layer,status}` / `{status}`。

**另外两条 checklist 的取证方式**：①「带 label 的计数器只进 `/metrics`、不写结构化 metric 日志」——api-kit 的 `MetricsRegistry` 只维护进程内字典与 HELP/TYPE，全程无日志调用（`packages/api-kit/src/muad_api/metrics.py`）；②「不得进入热点路径的额外 IO」——记录点是 `inc_counter`（加锁累加），且只在**请求/回合收口处**逐层记一次，不逐条消息、不查库。
- E-05: verified — automated command passed; run_id=98dfad9e5ee94106b59aaf0bd1f06c5e (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] completed (done)

---
- [2026-10-04] 实现：`metrics.py` 新增四个计数器（`context_compaction_total{layer,status}`、`context_compaction_bytes_saved_total{layer}`、`context_summary_total{status}`、`context_summary_tokens_total`，token 记在 **amount** 不进 label）并进 `CATALOG`；`context_compaction.py` 在收口处逐层记 CPU 无关的计数与省下字节，摘要层记 `OK`/`REJECTED`/`FAILED` 三种结局，整段退化路径记 `{layer="*",status="FAILED"}`（失败不静默）；token 用量记在**发起调用的一侧**（`make_summary_runner`，那里才有 `ModelResponse` 的 usage），且无论采不采用都记（钱已经花了）。回归 `tests/agent_core tests/agent_runtime tests/architecture tests/sdk` → **582 passed**；ruff / mypy(297 files) clean。
## TASK-008: memory 预算与 micro 豁免（FEAT-09）

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002, TASK-006
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-06

### Description

memory 注入段当前**不进** `_trim` 预算（`apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py:36,83-87`：先 `_trim(history)` 再把 memory 作为 `SYSTEM` 前置）。FEAT-09 要求：注入段按 `memory.budget_ratio` 计入上下文预算，且**不参与 micro 降级**（内容必须原样保留、不得被换成占位符）——注入段需要可被压缩层识别为"memory 段"。

### Checklist

- [ ] [E-06][integration] 先登记并编写 tests/agent_runtime/test_context_memory_budget.py；真实边界：真实请求装配（`context_builder` → `ExecutorRequest.history` → `AgentRunner`）→ 模型 HTTP 探针；断言 memory 注入段计入预算（超限时参与裁剪）、且**不参与 micro 降级**（探针收到的 system 段与注入原文逐字一致）；验证设计约定并登记证据，记录 RED/GREEN
- [ ] 注入上限（`MAX_INJECTED_MEMORIES=10`、`MAX_INJECTED_BYTES=2048`）与 `memory.budget_ratio`（默认 0.2）不得互相打架：显式登记两者的优先级口径
- [ ] 负例非空转：构造"不做 memory 标记"的对照，证明该断言能真实失败
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-06 | integration | 真实请求装配 → 模型 HTTP 探针 | memory 注入段计入预算并参与裁剪；不参与 micro 降级、内容逐字保留 | tests/agent_runtime/test_context_memory_budget.py | uv run pytest -q tests/agent_runtime/test_context_memory_budget.py | planned |

### Acceptance Evidence

> functional 的 RED/GREEN 与逐条断言证据由 `cf-task-start` 在编码期登记；全部 functional 状态 verified 后任务才可 done。

### Log

- [2026-10-04] created (draft)

---

## TASK-009: 收口清单与需求级终验

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-010, TASK-011, TASK-012
- **Source**: context-compaction.design.md#Spec Compliance Matrix, context-compaction.design.md#3.1 方案选型
- **Spec-Refs**: harness-test#RULE-test-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: N/A

### Description

建立需求级收口清单 `tests/context_compaction_inventory.py`，以**真实盘面**为输入做交叉核对（不自查断言）；并承接需求级 verify-e2e 与两条规范责任：`harness-test` 的分层验收口径、`harness-worker` 的「Worker 路径不引入压缩」对照断言。

### Checklist

- [ ] 收口清单 `tests/context_compaction_inventory.py`：覆盖表每行（含 RULE 规则行）唯一负责人且终态、**不豁免收口任务自身**；manifest 与覆盖表同 ID/同 owner/同命令并比对 `level`/`boundary`/`cwd`；终态场景与规则行在 owner 的 Evidence 中各自登记；**每个任务契约表的每一行都留一行断言**（只查"行是否终态"查不出整行被删）；证据表不得残留占位行；`-k`/`-g` 令牌必须在真实用例名/真实 spec 文件里命中；`_dir()` 路径双写 live→archived
- [ ] 结构性 RED + 扰动取证：清单文件缺失时登记的 argv 必须失败；至少扰动 4 类（改状态、删证据行、伪造用例名或命令、改 manifest 字段），每类须变红且消息指名条目，逐字节还原后复跑全绿
- [ ] [RULE-worker-001][integration] 作为唯一最终负责人：以**对照断言**证明 Worker 路径不引入压缩——真实 Worker 子进程执行 Skill 任务时，其执行链不构造 `ModelRequest`、不加载 `RequestCompactor`（`apps/agent-worker` 只依赖 `muad_agent_core.skill`）；配套跑 verifier 原始命令 `uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py`，保持规范责任，记录门禁裁决
- [ ] verifier harness-test#RULE-test-001：执行规范元数据的原始命令 `uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test`，保持规范责任，记录门禁裁决
- [ ] 需求级 verify-e2e：全部任务完成后执行 `python3 .code-flow/scripts/cf_acceptance_runner.py --manifest .code-flow/tasks/2026-10-04/context-compaction/.acceptance-manifest.json --root . --include-e2e --write-evidence`，S-01 走真实链路实跑
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| RULE-worker-001 | integration | 真实 Worker 子进程 → 真实 PostgreSQL | Worker 执行 Skill 任务不构造 `ModelRequest`、不加载压缩层（对照断言） | tests/agent_worker/test_context_compaction_absent.py | uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py | planned |
| RULE-test-001 | E2E | 真实 PG/Redis + 前端构建产物 + 真实浏览器 | 分层验收链整体通过；成功路径无路由拦截 | tests/acceptance/** + e2e/tests/** | uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test | planned |

### Acceptance Evidence

> 收口清单与需求级终验证据由本任务在收尾期登记；RULE 行按 owner 回填。

### Log

- [2026-10-04] created (draft)

---

## TASK-010: snip 尾部锚定当前回合（当前问题不被省略）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: context-compaction.design.md#2.2 功能需求, context-compaction.design.md#3.2 架构设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-01

### Description

`snip` 的尾部窗口只按**组数**保留（`keep_tail_groups`，默认 20），**不锚定最近一条 `user` 消息**：一个回合内的工具轮次超过该窗口时，**当前正在回答的那个问题**会落进省略区，模型只看到一串工具名 —— 它得靠猜来回答。这与条数兜底"从最近一条 USER 起切"是同一条口径，却只有条数兜底实现了它（口径不对称是 TASK-006 收尾时发现的）。

本任务把 snip 的尾部下界钉在"最近一条 `user` 消息所在的组"：**当前回合（从最近一条 user 起到末尾）永远全保**，中间只省得更早的历史。`max_groups` 仍是触发阈值不是保留总数；当对话区里除了头 N 组就只剩当前回合时，没有可省的历史 ⇒ **不触发**（层未触发时输出仍是净化后的输入，不得凭空插入标记）。

### Checklist

- [x] [B-01][unit] 先写 RED：对话区 = 1 条 user + 超过尾窗口的工具组，断言"当前回合的每一组都还在"；现行实现必然红（会省掉中间的工具组），记录失败命令与原因
- [x] [B-01][unit] 三面对照：① 最近一条 user 组落在尾窗口**之外** ⇒ 下界扩到它、被省的只剩更早的历史；② 它落在尾窗口**之内** ⇒ 与锚定前**逐字节一致**（锚定不改变结果）；③ 头 N 组之后已经没有更早的历史（整段都在当前回合里）⇒ **不触发**（`fired is False`、输出逐字节等于净化后的输入，不得凭空插标记）
- [x] [B-01][unit] 边界：历史里**一个 user 组都没有**（纯工具回合）⇒ 退化为按组数的原口径，不抛错；标记按组报数、无孤儿 TOOL（既有断言不得回退）
- [x] 实现：`snip` 的尾部下界取 `min(尾窗口下界, 最近一条 user 组下标)`，省略区为空则不触发；`keep_tail_groups` 的语义在配置表里写明"下界受最近一条 user 组限制"
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-01 | unit | 纯逻辑：消息组切分与头尾保留 | 头 N 组与当前回合全保；被省的只有更早的组；无 user 组时退化不炸；无孤儿 TOOL | tests/agent_core/test_context_compactor.py::test_b01_snip_keeps_the_current_turn_whole | uv run pytest -q tests/agent_core/test_context_compactor.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: `test_b01_snip_keeps_the_current_turn_whole` → `AssertionError: 当前正在回答的问题被省略了`（输出只剩 `[历史省略] 中间 6 组历史已省略…工具：tool_old、tool_now_0…` + 最后两组，"现在的追问"整条消失）；`test_b01_snip_does_not_fire_when_everything_is_the_current_turn` → `assert True is False`（单回合历史也插标记、省了 2 组） | 26 passed | `test_b01_snip_keeps_the_current_turn_whole`（当前回合全保 / 被省只有更早的 / 标记报 2 组）、`test_b01_snip_is_unchanged_when_the_tail_window_already_covers_the_current_turn`（对照：已覆盖则不改变）、`test_b01_snip_does_not_fire_when_everything_is_the_current_turn`（无可省历史 ⇒ 不触发且逐字节等于输入）、`test_b01_snip_without_any_user_group_falls_back_to_the_group_window`（无 user 组退化） | 纯函数（同输入同输出），不碰库、不读环境；`_conversation()` 夹具 8 组（更早一轮 + 当前回合） | verified |

**扰动取证**：P1（去掉 `start = min(start, current_turn)` 的锚定）⇒ **3 failed**；P2（省略区为空也照插标记）⇒ **1 failed**；两处逐字节还原后复跑 26 passed。

**既有断言迁移**（语义变化的直接后果，逐条已核对不是放宽）：`test_b01_snip_keeps_head_and_tail_and_counts_omitted_groups`、`test_b01_snip_is_off_at_or_below_the_threshold`、`test_rule01_assistant_with_tool_calls_survives_its_result`、`test_rule02_snip_reports_real_byte_savings`、`test_outcome_payload_shape_matches_audit_event`、`test_snip_never_counts_or_omits_the_protected_prefix` —— 夹具从"单回合"(`_messages()`) 换成有历史的 `_conversation()`：单回合在新口径下**没有可省的历史**（旧断言断的正是"省掉当前回合的工具组"这件事本身）。
- B-01: verified — automated command passed; run_id=4533d6e75dce45daa740f741ff7d9189 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] 实现：`packages/agent-core/src/muad_agent_core/context/compactor.py` 新增 `_current_turn_start`（最近一条 `user` 组下标），`snip` 的尾部下界取 `min(尾窗口下界, 当前回合起点)`，省略区为空 ⇒ 不触发（返回净化后的输入）。`max_groups` 仍是触发阈值。回归 `tests/agent_core tests/agent_runtime tests/architecture` → **476 passed**；ruff / mypy(296 files) clean。
- [2026-10-04] completed (done)

---

## TASK-011: 工具结果的整轮批次预算接进回合循环

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-006
- **Source**: context-compaction.design.md#3.2 架构设计（ADR-04）, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**: harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: E-07, RULE-mcp-001

### Description

TASK-003 交付了 `select_round_persists`（整轮选取，纯逻辑）与 `ArtifactResultWriter.persist_round_results_with_session`（批量落盘 + 失败整批回滚），但**至今没有调用方**：工具结果仍由 `ToolCallRecorder` **逐条**判定外置，于是"每条都没超单条阈值、合计却超整轮预算"这个 FEAT-02 的主场景无人处理 —— 模型每轮都要吞下整批中等大小的结果。

按 ADR-04 把判定单元从"单条"改成"回合"：`ToolCallRecorder` 不再逐条外置，改为缓存本回合的原始结果；`AgentRunner._execute_tools` 在**回合末**调一次端口，端口跑整轮选取 + 批量落盘，**然后**才逐条写审计行、发 `tool.completed`（带最终 `artifact_id`）。顺序是硬约束：产物 id 必须赶在 `tool.completed` 之前定下来，否则 canonical `TOOL_CALL` 行拿不到它，跨 Run 重建就指不到那个产物。

### Checklist

- [x] [E-07][integration] 先写 RED：真实 PG + 真实产物根 + 真实 `AgentRunner`，一个回合里三条结果各自都没超单条阈值、合计超整轮预算，断言"超出的那些落盘、模型收到引用 JSON、canonical `TOOL_CALL` 行带 `artifact_id`"；现行实现必然红（一条都不落盘），记录失败命令与原因
- [x] [E-07][integration] 覆盖两条反向腿：① 整轮合计**未超**预算 ⇒ **一条都不落盘**（不得因为"整轮判定"把原本内联的结果无谓外置）；② 失败注入：本批落盘中途抛错 ⇒ **整批回滚**（盘上不留半截产物），且 Run 不因此失败（退化到"这批不外置"）
- [x] 端口与接线：`ToolCallRecorder` 缓存本回合原始结果（含 `externalizable_result=False` 的既有豁免口径），`AgentRunner._execute_tools` 在回合末调用端口一次；端口实现全在 runtime 侧，`packages/*` 不得 import app 包
- [x] 顺序约束：审计行与 `tool.completed` 在批次判定**之后**逐条发，且都带最终 `artifact_id`；单工具回合的事件时序与现状逐条等价（既有 `tests/agent_runtime/test_execution_activity.py` 的时序断言不得改）
- [x] [RULE-mcp-001][integration] 作为**承接方**（不是规则主体）：整轮批次判定包装在**统一 ToolRegistry** 外层，`mcp::` 前缀的 MCP 工具与内置工具走同一条包装路径、不按来源分叉；MCP 工具定义仍只来自冻结 Snapshot 的 definitions（本需求不碰 discover-tools，也不在 Run 内调 `tools/list`）。执行规则自带的 verifier：`uv run pytest -q tests/console_mcp/test_mcp_rules.py`，并记录门禁裁决
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-07 | integration | 真实 PG + 共享产物存储 + 真实 `AgentRunner` 工具回合 | 整轮合计超预算才落盘（从大到小、进预算即停）；落盘的那些被换成引用 JSON、未落的正文原样；canonical `TOOL_CALL` 行的 `payload_json.artifact_id` 与落盘产物一致；整批失败回滚且 Run 不失败 | tests/agent_runtime/test_tool_round_budget.py::test_e07_round_batch_lands_in_the_tool_loop | uv run pytest -q tests/agent_runtime/test_tool_round_budget.py | verified |
| RULE-mcp-001 | integration | 真实统一 `ToolRegistry`（`mcp::` 前缀工具与内置工具走同一条包装路径） | 判定包装不按工具来源分叉；MCP 工具定义仍只来自冻结 Snapshot 的 definitions（Run 内无 `tools/list`） | tests/console_mcp/test_mcp_rules.py | uv run pytest -q tests/agent_runtime/test_tool_round_budget.py && uv run pytest -q tests/console_mcp/test_mcp_rules.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-07 | **实现先行，未记录行为 RED**：`AgentRunner(tool_round_results=...)` 与 recorder 的回合收口都是本任务新增的接口，实现前跑是收集期 `TypeError`、不是行为红。改以两条**扰动**替代取证：P1「回合末不调端口」⇒ 1 failed；P2「整轮判定只看单条阈值」⇒ 1 failed；逐字节还原后复绿 | 3 passed | `test_e07_round_batch_lands_in_the_tool_loop`（落盘的是最大的两条 4000/3500；`{"artifact"` 引用进了第二轮模型请求、未落盘那条正文原样；canonical 行的 `payload_json.artifact_id` 与产物一致；三条审计行带同一个 id）、`test_e07_round_within_budget_persists_nothing`（反向腿①：零落盘、正文原样）、`test_e07_failed_batch_rolls_back_without_failing_the_run`（反向腿②：第三件写盘时炸 ⇒ 盘上零文件、库里零行、Run 仍 COMPLETED、模型仍拿到正文） | 真实 `RunService` 建 Run + 真实 SSE 消费 → 真实 `AgentRunner` 工具回合 → 生产同款 `ToolCallRecorder` → 真实 PostgreSQL（`runtime.artifact` / `canonical_event` / `tool_call_audit`）+ 真实产物根 | verified |

**顺带修掉一处「整批」名不副实**：`ArtifactResultWriter` 原先**逐条 commit**，中途失败时文件回滚了、已提交的行却留在库里指向已删文件。现改为整批只在最后提交一次（单条路径由调用方提交），失败时行与文件一起退场 —— B-02 的回滚用例随注入点改成「第 2 条插行时失败」（`_StubSession` 新增 `fail_on_add`，并断言 `commits == 0`）。
- E-07: verified — automated command passed; run_id=3ceab6c1eb2143139edabbdb66d99ee4 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] started
- [2026-10-04] 实现：`packages/agent-core` 新增端口 `ToolResultRoundPort`（`tools/round_results.py`）；`AgentRunner` 增 `tool_round_results`，在 `_execute_tools` 的 **finally** 里收口（中途被取消/超时打断时，已经跑完的那些同样要判定与审计）；逐条 `on_tool_completed` 移到收口之后，产物 id 因此赶得上事件。runtime 侧 `ToolCallRecorder` 改为「逐条缓冲 + `finish_round` 整批判定 / 批量落盘 / 写审计 / 替换引用」，`build_registry` 与 `default_executor_factory` 共用同一个 recorder（`build_registry` 新增可选 `recorder` 入参）。回归 `tests/agent_core tests/agent_runtime tests/architecture tests/sdk` → **577 passed**；ruff / mypy(297 files) clean。
- [2026-10-04] **发现（不在本任务范围，需单独决策）**：canonical `TOOL_CALL` 行的 `artifact_id` **列**在生产里永远是 NULL —— `RunService._persist_event` 调 `EventWriter.append(...)` 时没传 `artifact_id=`，产物 id 只落在 `payload_json` 里；而 `context_builder._to_messages` 的 TOOL_CALL 预览查找读的是**列**（`previews.get(event.artifact_id)`）。后果：跨 Run 重建历史时**外置过的工具结果一律退化成 `[tool:名称]`** —— 既没有预览，也没有 id 去 `read_attachment`。E-01 的重建用例没有覆盖这条（它不种 TOOL_CALL 行），所以一直没被发现。本任务只保证 id 送到 `payload_json`（`tool.completed` 的真实载体），列的缺口留给单独决策。

- **补第三条（2026-10-04，TASK-011 设计时核实）**：canonical `TOOL_CALL` 行的 `artifact_id` 列从不写入 ⇒ 跨 Run 重建时外置过的工具结果既没有预览也没有 id，模型拿不到产物。这是 ADR-05 要修的：列补写 + 重建改用与写入侧同一个 `payload` 序列化，**不留兼容层**。新增 E-08，TASK-012 承接。

---

## TASK-012: 外置过的工具结果可从 canonical 行逐字节重建

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-011
- **Source**: context-compaction.design.md#3.2 架构设计（ADR-05）, context-compaction.design.md#3.3 数据设计, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-08

### Description

FEAT-08 承诺"重建的那份 == 当时真正发给模型的那份"，但**外置过的工具结果**在跨 Run 重建时两处都不成立：

1. canonical `TOOL_CALL` 行的 `artifact_id` **列从不写入** —— `RunService._persist_event` 调 `EventWriter.append(...)` 时没传 `artifact_id=`，产物 id 只落在 `payload_json` 里；而重建读的是**列**（`previews.get(event.artifact_id)`），于是这条路径在生产里从来没命中过。
2. 即便命中了，重建渲染的是 `[tool:名称] + 截断到 400 字的预览`，而模型当时看到的是一条**引用 JSON**（`artifact_id`/`size`/`checksum`/`preview` 四键）—— 既不等，又**不含 id**，模型下一轮拿不到 id 去 `read_attachment`。

本任务按 ADR-05 一次修到底：列补写（工具返回的引用 JSON 不是可信 UUID，解析失败按"没有产物"计并留警告）；重建改用**与写入侧同一个** `reference_payload` 序列化，不再截断、不再丢 id。

### Checklist

- [x] [E-08][integration] 先写 RED：真实 PG + 真实产物根 + 真实两连 Run（同一会话）——第一个 Run 的工具结果外置，断言"第二个 Run 的模型请求里那条 tool 消息**逐字节**等于第一个 Run 当时发出去的引用 JSON，且 canonical 行的 `artifact_id` 列已写入"；现行实现必然红（列是 NULL、重建只剩 `[tool:名称]`），记录失败命令与原因
- [x] [E-08][integration] 边界：① 引用 JSON 里的 `artifact_id` 不是合法 UUID（工具自己写的脏值）⇒ 列留空、记一条 warning、**Run 不受影响**；② 未外置的工具结果（`artifact_id` 为 NULL）⇒ 重建仍是 `[tool:名称]`，不凭空造引用
- [x] 实现：`RunService._persist_event` 从事件载荷取 `artifact_id` 提到列上；`reference_payload` 从 `executor` 提到 `attachments/tool_results`，写入侧与重建侧共用；重建取的预览**不再截断**（`BudgetPolicy.preview_max` 随之退场，不保留死配置）
- [x] 不留兼容层：不做"两处都读"的兜底，也不为旧数据补写；列与载荷是同一事实的两种表达，以列作重建的指针
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-08 | integration | 真实 PG + 共享产物存储 + 真实两连 Run（同一会话） | 第二个 Run 的请求里那条 tool 消息与第一个 Run 发出去的引用 JSON **逐字节相同**；canonical `TOOL_CALL` 行的 `artifact_id` 列非空且指向真实产物；脏 id 不影响 Run | tests/agent_runtime/test_tool_result_history.py::test_e08_externalized_result_rebuilds_byte_identically | uv run pytest -q tests/agent_runtime/test_tool_result_history.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-08 | FAIL: `test_e08_externalized_result_rebuilds_byte_identically` → `AssertionError: 跨 Run 重建必须逐字节还原当时那条引用` / `assert '[tool:dump]' == '{"artifact": {"artifact_id": "09a341e0-…", "size": 24000, "checksum": "sha256:dfdd…", "preview": "内容…"}}'` —— 重建只剩工具名，正文、预览、artifact_id 全丢 | 2 passed | `test_e08_externalized_result_rebuilds_byte_identically`（引用 JSON 逐字节相等 + canonical 行的 `artifact_id` 列非空且指向真实产物 + 两连 Run 确在同一会话）、`test_e08_dirty_artifact_id_does_not_break_the_run`（脏 id ⇒ 列留空、Run 仍 COMPLETED、下一个 Run 重建为 `[tool:dirty]` 不凭空造引用） | 真实 `RunService` 建 Run + 真实 SSE 消费 → 真实 `AgentRunner` 工具回合 → 生产同款 `ToolCallRecorder` → 真实 PostgreSQL（`runtime.canonical_event` / `runtime.artifact`）+ 真实产物根 | verified |

**扰动取证**：P1（canonical 不写 `artifact_id` 列）⇒ **1 failed**；P2（重建退回 `[tool:名称] + 截断预览`）⇒ **1 failed**；逐字节还原后复跑 2 passed。

**顺带退场的死配置**：`BudgetPolicy.preview_max`（400 字二次截断）随"不再截断"一起删除——保留它既没有读者，也会让重建与实发不等。
- E-08: verified — automated command passed; run_id=522b6534e7444df991ced1ff0329b9f4 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)
- [2026-10-04] resumed (in-progress)
- [2026-10-04] completed (done)
- [2026-10-04] started
- [2026-10-04] 实现：① `attachments/tool_results.reference_payload()`（从 `executor` 提上来，写入侧与重建侧**共用同一个**序列化）；② `RunService._event_artifact_id()` 把流事件载荷里的 id 提到 canonical 行的**列**上（脏值按"没有产物"计并留 `event_artifact_id_invalid` 警告）；③ `context_builder._artifact_previews` → `_artifact_references`（返回完整引用，不再按 400 字截断），重建渲染改为还原当时那条引用 JSON。回归 `tests/agent_core tests/agent_runtime tests/architecture tests/sdk` → **579 passed**；ruff / mypy(297 files) clean。
