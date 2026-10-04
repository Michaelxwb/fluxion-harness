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
- **Acceptance**: 11 条场景（B-01/02/03/04、E-01..06、S-01）+ 6 条业务规则（RULE-01..06）全部有唯一负责人与可执行命令。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout |
|---|---|---|---|---|---|---|---|---|
| E-04 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG：execution snapshot 的 `policy_json` | TASK-001 | verified | uv run pytest -q tests/agent_runtime/test_context_compaction_config.py | . | 600 |
| B-01 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：消息组切分与头尾保留 | TASK-002 | verified | uv run pytest -q tests/agent_core/test_context_compactor.py | . | 600 |
| B-03 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：micro 降级与占位符 | TASK-002 | verified | uv run pytest -q tests/agent_core/test_context_compactor.py | . | 600 |
| B-02 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：整轮批次预算选取 | TASK-003 | verified | uv run pytest -q tests/agent_runtime/test_artifact_round_budget.py | . | 600 |
| B-04 | context-compaction.design.md#2.5.2 验收场景 | unit | 纯逻辑：摘要五字段精确校验 | TASK-004 | verified | uv run pytest -q tests/agent_runtime/test_context_summary.py | . | 600 |
| E-02 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG + 共享产物存储 | TASK-004 | verified | uv run pytest -q tests/agent_runtime/test_context_compaction_artifacts.py | . | 600 |
| E-01 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG：从 canonical_event 重建 | TASK-005 | planned | uv run pytest -q tests/agent_runtime/test_context_rebuild.py | . | 600 |
| E-03 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 PG：canonical_event 审计行 | TASK-005 | planned | uv run pytest -q tests/agent_runtime/test_context_events.py | . | 600 |
| E-05 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实 agent-runtime `/metrics`（api-kit 目录） | TASK-007 | planned | uv run pytest -q tests/agent_runtime/test_context_metrics.py | . | 600 |
| E-06 | context-compaction.design.md#2.5.2 验收场景 | integration | 真实请求装配 → 模型 HTTP 探针 | TASK-008 | planned | uv run pytest -q tests/agent_runtime/test_context_memory_budget.py | . | 600 |
| S-01 | context-compaction.design.md#2.5.2 验收场景 | E2E | 真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针 | TASK-006 | planned | uv run pytest -q tests/acceptance/im_gateway/test_context_compaction.py | . | 1200 |

> 本表覆盖 design §2.5.2 全部 11 条场景（B-01..04、E-01..06、S-01，含本次回填的 E-05/E-06）与 §2.5.1 全部 6 条业务规则（RULE-01..06 的负责人见各 TASK 的 Acceptance-Refs）。

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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-004
- **Source**: context-compaction.design.md#2.3 功能方案, context-compaction.design.md#3.3 数据设计, context-compaction.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-data#RULE-data-001, harness-arch#RULE-arch-001
- **Acceptance-Refs**: E-01, E-03, RULE-05

### Description

压缩**真的发生**时落一行 `runtime.canonical_event` 审计事件：`event_type='CONTEXT_SUMMARY'`（`payload_json={covers_up_to_seq, summary{5 fields}, transcript_artifact_id, bytes_before, bytes_after}`）与 `event_type='CONTEXT_COMPACTED'`（`payload_json={layers{layer:{fired, groups, bytes_saved}}, summary_event_seq?}`）。重建语义：取**最新**一份覆盖事件作前缀，其后再按 seq 顺序应用后续事件，最后跑前三层压缩 —— 结果必须与当时真正发给模型的那份**逐字节一致**。不新增表、无迁移。

### Checklist

- [ ] [E-01][integration] 先登记并编写 tests/agent_runtime/test_context_rebuild.py；真实边界：真实 PostgreSQL（`runtime.canonical_event` 重建链）；断言"同一份 canonical_event 重建两次 + 与真实发出的请求对比"**逐字节一致**；验证设计约定并登记证据，记录 RED/GREEN
- [ ] [E-03][integration] 先登记并编写 tests/agent_runtime/test_context_events.py；真实边界：真实 PostgreSQL；断言压缩发生时**恰好**多一行压缩事件、字段（层级/省下字节/摘要引用）齐；未压缩时不多行；记录 RED/GREEN
- [ ] [RULE-05][integration] 作为唯一最终负责人：摘要文本是**权威历史**（落库、参与重建），transcript 是**存档**（落共享产物），二者不得互换——以 E-01 的重建链与 E-02 的产物落点两侧对照取证
- [ ] 事件追加走既有 `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py` 的行锁 seq 分配路径（不得自造第二套 seq 分配）；`payload_json` 为 `jsonb`，关键查询字段不得只藏在 JSON
- [ ] 复用既有 `canonical_event(run_id, seq)` 索引，不新增索引
- [ ] verifier harness-data#RULE-data-001：执行规范元数据的原始命令 `uv run pytest -q tests -k schema_parity`，保持规范责任，记录门禁裁决
- [ ] verifier harness-arch#RULE-arch-001：执行规范元数据的原始命令 `uv run pytest -q tests/architecture`，保持规范责任，记录门禁裁决
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | 真实 PostgreSQL：canonical_event 重建 | 重建两次同结果；与真实发出的请求逐字节一致 | tests/agent_runtime/test_context_rebuild.py | uv run pytest -q tests/agent_runtime/test_context_rebuild.py | planned |
| E-03 | integration | 真实 PostgreSQL：canonical_event | 压缩发生时恰好多一行；字段齐；未压缩不多行 | tests/agent_runtime/test_context_events.py | uv run pytest -q tests/agent_runtime/test_context_events.py | planned |
| RULE-05 | integration | 真实 PG + 共享产物存储 | 摘要是权威历史（参与重建）；transcript 是存档；不互换 | tests/agent_runtime/test_context_rebuild.py | uv run pytest -q tests/agent_runtime/test_context_rebuild.py | planned |

### Acceptance Evidence

> functional 的 RED/GREEN 与逐条断言证据由 `cf-task-start` 在编码期登记；全部 functional 状态 verified 后任务才可 done。

### Log

- [2026-10-04] created (draft)

---

## TASK-006: 接线请求构建缝与端到端验收（FEAT-01..05 串联）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-004, TASK-005
- **Source**: context-compaction.design.md#3.1 方案选型, context-compaction.design.md#3.2 架构设计, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: S-01

### Description

把 `RequestCompactor` 接进 `AgentRunner` 组装 `ModelRequest` 的位置（`packages/agent-core/src/muad_agent_core/agent/runner.py:302-318` 的 `_call_model`），在调 provider **之前**统一执行；`CompactionPort`/`SummaryPort` 由 runtime 侧适配器实现（落盘 / 写事件 / 调摘要模型）。这是全需求唯一的模型请求组装点，S-01 在此链路端到端取证。

### Checklist

- [ ] [S-01][E2E] 先登记并编写 tests/acceptance/im_gateway/test_context_compaction.py；真实边界：真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针（`tests/e2e/openai_probe_app.py`，禁止伪造外部响应）；成功路径不得出现 `page.route(` 一类拦截；验证设计约定并登记证据，E2E 延后 verify-e2e
- [ ] 接线位置必须是 `AgentRunner` 组装 `ModelRequest` 的唯一处（不得在 `context_builder` 里再压一遍，否则同一份历史两种口径）
- [ ] 端口实现全部在 runtime 适配器侧（`packages/*` 不得 import 四个 app 模块，依赖方向单向）
- [ ] 压缩整体包在 try 内：异常退化到"不压缩"，日志只记字节数与层级、不记内容
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | 真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针 | 长会话 + 大工具结果 + 多工具回合后：开头诉求仍在、无孤儿 TOOL、模型收到的 prompt 含省略标记或摘要、压缩事件落库 | tests/acceptance/im_gateway/test_context_compaction.py | uv run pytest -q tests/acceptance/im_gateway/test_context_compaction.py | planned |

### Acceptance Evidence

> E2E 场景延后到需求级 verify-e2e 执行；`cf-task-start` 在编码期登记实现与接线证据。

### Log

- [2026-10-04] created (draft)

---

## TASK-007: 压缩指标（FEAT-06）

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-002, TASK-003, TASK-004
- **Source**: context-compaction.design.md#3.5 质量实现方案, context-compaction.design.md#2.5.2 验收场景
- **Spec-Refs**:
- **Acceptance-Refs**: E-05

### Description

把 `context_compaction_total{layer,outcome}`、`context_compaction_bytes_saved_total{layer}`、`context_summary_total{outcome}`、`context_summary_tokens_total` 登记进 agent-runtime 的 metric catalog（`apps/agent-runtime/src/muad_agent_runtime/metrics.py` 的 `CATALOG`），无流量时也要在 `/metrics` 目录可见；label 低基数（layer/outcome），不带资源 ID。

### Checklist

- [ ] [E-05][integration] 先登记并编写 tests/agent_runtime/test_context_metrics.py；真实边界：真实 agent-runtime `/metrics`（api-kit 目录）；断言**无流量也暴露目录**、触发后计数与省下字节递增、label 无高基数维度；验证设计约定并登记证据，记录 RED/GREEN
- [ ] 指标名与 label 进 `CATALOG` 后经 `install_metrics` 暴露；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志
- [ ] 指标记录点不得进入热点路径的额外 IO（与 design §3.5 的"无 N+1、无循环内 IO"一致）
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-05 | integration | 真实 agent-runtime `/metrics`（api-kit 目录） | 无流量也可见四级计数器目录；label 低基数；触发后计数与省下字节递增 | tests/agent_runtime/test_context_metrics.py | uv run pytest -q tests/agent_runtime/test_context_metrics.py | planned |

### Acceptance Evidence

> functional 的 RED/GREEN 与逐条断言证据由 `cf-task-start` 在编码期登记；全部 functional 状态 verified 后任务才可 done。

### Log

- [2026-10-04] created (draft)

---

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
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008
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
