---
design_template: full
---
# 系统设置页精简：后端设计

> v0.2 · 2026-11-27 · 待评审 · FEAT-01 至 FEAT-08

## 1. 方案选型

**D1 · 以“配置所有者”而不是“页面观感”决定去留。**
每个叶子只允许一个所有者：

- **平台设置（control.platform_setting）**：租户级业务策略、安全边界、硬配额、保留期、功能开关；
- **部署配置（SharedSettings / ConfigMap / Secret）**：随部署环境或规模变化的并发、资源、连接、超时、租约、重试/退避、批大小、客户端传输参数、算法阈值；
- **代码不变量**：协议、状态机、schema 边界、跨字段关系（如 `consolidation_lease_sec >= timeout + 15`）、安全规则。代码里不再保留“运营可调但没有任何配置入口”的隐藏默认。

前端不提供“隐藏字段”：`build_groups` 按 schema 全量派生，隐藏等于制造看不见的配置。

**D2 · 非压缩分组从 97 个叶子降到 54 个。**
本需求删除/下沉/合并 45 个非压缩字段，新增 2 个合并/总开关字段；`compaction` 已是 4 个字段（已完成需求）。整页 101 → **58**（净减 43）。字段级分类见 §2.1。

**D3 · Agent 执行 5 项全保留，并与后台任务共用同一套预算。**
`max_turns` / `max_tool_calls` / `deadline_ms` / `max_model_retries` / `max_identical_failures` 全部保留为租户设置；交互式 Run 与后台 `AGENTIC` 任务都从 `PlatformSettings.agent` + Agent `runtime_config.budget` 覆盖派生同一份 `AgentPolicy`，并在各自创建边界冻结。直接 `SKILL` 任务没有模型/工具循环，只保留 `task.execution_timeout_sec` 作为子进程超时。

**D4 · 下沉值进入完整部署配置，而不是代码常量。**
v0.1 的“能进代码就进代码”被评审否决：内网/生产需要能看见、能调整这些值。本需求新增 **42 个环境键**，并交付：

- `deploy/k8s/base/configmap.yaml`：全部非敏感键；
- `deploy/k8s/overlays/intranet/` 与 `deploy/k8s/overlays/production/`：两套取值；
- `docs/configuration-profiles.md`：逐键默认/内网/生产对照；
- 机检：键集、值合法性、Secret 隔离、与容量 profile 一致。

**D5 · 迁移追加 revision，不原地改历史。**
迁移读取每个租户最新的 `settings_json`，删除被删字段、写入合并/新增字段，追加 revision+1 并写审计；旧 revision 恢复时按“schema 过旧”显式拒绝。Agent `runtime_config_json` 同步清理。`compaction` 已稳定为 4 个字段，本迁移只删除非压缩旧键，不改写压缩字段。

**D6 · 用字段清单、配置档案和统一预算契约做守卫。**
新增字段 inventory、配置 profile 契约测试和 Agent 预算一致性测试；任何新增字段/环境键必须显式登记。

**被否方案**：

- 仅在前端隐藏（schema/consumer/i18n 仍维护，管理员无法排查）；
- 把内部调参整体搬进一个“高级设置”页面（配置面没有缩小，只是换了入口）；
- 把下沉值全部写成代码常量、只靠发布调整（内网/生产不可见、不可按环境取值）；
- 把记忆注入条数/字节合并成一个布尔开关（会把租户已有预算静默改成全局默认）；
- 让后台任务保留自有的回合/工具/模型重试默认（同一租户出现两套 Agent 上限）。

## 2. 契约与数据

### 2.1 字段分类与目标

以下只列非 `compaction` 分组；`compaction` 见 `conversation-context-retention.backend.design.md`。

#### `agent`（5 → 5，保留）

`max_turns` / `max_tool_calls` / `deadline_ms` / `max_model_retries` / `max_identical_failures` 全部保留：执行预算与失败保护属于租户业务策略。内网真实任务触发上限时由管理员调高，不改成无上限。

**统一预算契约**：

1. `AgentPolicy` 只由 `AgentPolicy.from_runtime_config(agent.runtime_config, base=PlatformSettings.agent)` 构建；
2. 交互式 Run 在 `snapshot_policy` 中冻结全部 5 个叶子；
3. 后台 `AGENTIC` 任务在 admission 时从同一构建函数取 5 个叶子，写入 `SnapshotBudget`；
4. Run 发起的 Task 不再只把 `agent.runtime_config.budget` 写进快照：`TaskSubmissionContext` 携带 Run 已冻结的 `AgentPolicy`，`build_task_snapshot` 把它写入 `budget` 的 `max_steps` / `max_tool_calls` / `deadline_ms` / `max_model_retries` / `max_identical_failures`；
5. Worker 只读冻结值；缺键只允许旧快照（发布前排空），不得按当前租户值补写。

**当前代码缺口（本需求必须修）**：

- `snapshot_policy` 只冻结 4 个 Agent 叶子，漏 `max_identical_failures`；
- `build_task_snapshot` 的 `budget` 只合并 `agent.runtime_config.budget`，Run 发起的任务会回落到 `AgentPolicy()` schema 默认，而不是租户平台设置；
- `agentic_skills._timeout()` 用“到 Task deadline 的剩余时间”作为 Skill 工具超时，没有受 Agent `deadline_ms` 约束。

#### `task`（7 → 4）

| 字段 | 处理 | 新承载 | 消费点 |
|---|---|---|---|
| `default_deadline_hours` | 保留 | — | Task 创建默认 |
| `execution_timeout_sec` | 保留 | — | 直接 `SKILL` 任务的单次子进程超时；Agentic 路径不把它当 Agent 预算 |
| `max_attempts` | 保留 | — | Task 重试预算 |
| `batch_max_concurrency` | 保留 | — | 租户级批次并发上限；仍受 `SharedSettings.batch_platform_limit` 硬上限约束 |
| `misfire_grace_sec` | **下沉部署配置** | `TASK_MISFIRE_GRACE_SEC = 60` | `scheduler/service.py` |
| `delivery_max_attempts` | **下沉部署配置** | `TASK_DELIVERY_MAX_ATTEMPTS = 5` | `delivery/service.py`、`message_outbox.py` |
| `delivery_backoff_base_sec` | **下沉部署配置** | `TASK_DELIVERY_BACKOFF_BASE_SEC = 5` | `delivery/service.py`、`message_outbox.py` |

#### `memory`（21 → 11）

保留：`write_enabled`、`injection_enabled`（新增总开关）、`max_injected_memories`、`max_injected_bytes`、`inject_inferred`、`task_state_injection_enabled`、`max_entries_per_user`、`ttl_days_user`、`ttl_days_agent`、`ttl_days_task_state`、`consolidation_enabled`。

`injection_enabled=false` 停止自动注入记忆与任务线索；显式召回工具仍可用。保留 `max_injected_memories` / `max_injected_bytes`，不把两个预算合并成布尔开关。

下沉部署配置：

| 字段 | 新承载 |
|---|---|
| `max_recall_bytes` | `MEMORY_MAX_RECALL_BYTES = 4096` |
| `recall_default_limit` | `MEMORY_RECALL_DEFAULT_LIMIT = 10` |
| `max_memory_bytes_per_user` | `MEMORY_MAX_BYTES_PER_USER = 65536` |
| `consolidation_min_user_messages` | `MEMORY_CONSOLIDATION_MIN_USER_MESSAGES = 1` |
| `consolidation_max_input_bytes` | `MEMORY_CONSOLIDATION_MAX_INPUT_BYTES = 32768` |
| `consolidation_max_output_tokens` | `MEMORY_CONSOLIDATION_MAX_OUTPUT_TOKENS = 1024` |
| `consolidation_timeout_sec` | `MEMORY_CONSOLIDATION_TIMEOUT_SEC = 30` |
| `consolidation_max_candidates` | `MEMORY_CONSOLIDATION_MAX_CANDIDATES = 8` |
| `consolidation_max_attempts` | `MEMORY_CONSOLIDATION_MAX_ATTEMPTS = 2` |
| `consolidation_retry_delay_sec` | `MEMORY_CONSOLIDATION_RETRY_DELAY_SEC = 5` |
| `consolidation_lease_sec` | `MEMORY_CONSOLIDATION_LEASE_SEC = 60`；启动校验 `lease ≥ timeout + 15` |

`harness-memory` 的租约/超时/输入输出约束保留为代码不变量；`memory_jobs` / `memory_consolidation` 从 `SharedSettings` 读取执行参数，Run 创建边界把会改变执行语义的值冻结进 `policy_json` 的 `memory_limits` 子节。

#### `learning`（14 → 1）

保留：`learning_enabled`。

| 字段 | 处理 |
|---|---|
| `signal_detection_enabled` | **删除**：全仓无 consumer，属于死字段 |
| `retrieval_min_score` | `LEARNING_RETRIEVAL_MIN_SCORE = 0.35` |
| `retrieval_preference_items` | `LEARNING_RETRIEVAL_PREFERENCE_ITEMS = 3` |
| `retrieval_preference_bytes` | `LEARNING_RETRIEVAL_PREFERENCE_BYTES = 512` |
| `experience_min_success_runs` | `LEARNING_EXPERIENCE_MIN_SUCCESS_RUNS = 3` |
| `experience_auto_activate` | `LEARNING_EXPERIENCE_AUTO_ACTIVATE = PROCEDURE,FAILURE_PATTERN`（CSV） |
| `experience_trial_runs` | `LEARNING_EXPERIENCE_TRIAL_RUNS = 20` |
| `experience_trial_max_regression` | `LEARNING_EXPERIENCE_TRIAL_MAX_REGRESSION = 0.05` |
| `verification_max_attempts` | `LEARNING_VERIFICATION_MAX_ATTEMPTS = 2` |
| `eval_min_improvement` | `LEARNING_EVAL_MIN_IMPROVEMENT = 0.05` |
| `eval_min_cases` | `LEARNING_EVAL_MIN_CASES = 10` |
| `eval_max_cases` | `LEARNING_EVAL_MAX_CASES = 50` |
| `eval_max_items_per_case` | `LEARNING_EVAL_MAX_ITEMS_PER_CASE = 10` |

`FrozenLearning` 不再从 `policy_json` 读取被删键；会改变执行语义的学习参数在 Run 创建边界从 `SharedSettings` 冻结进 `policy_json.learning`，旧快照按“缺键 = 当前部署默认”解释（发布前排空在飞 Run）。

#### `artifact`（3 → 1）

保留：`retention_days`。

| 字段 | 新承载 |
|---|---|
| `max_archive_files` | `ARTIFACT_MAX_ARCHIVE_FILES = 2000` |
| `cleanup_batch_size` | `ARTIFACT_CLEANUP_BATCH_SIZE = 500`；CLI `--limit` 仍可单次覆盖 |

#### `auth`（4 → 4，保留）

`min_password_length` / `max_failed_attempts` / `lock_duration_minutes` / `session_ttl_hours` 全部保留：安全边界。

#### `locale`（2 → 2，保留）

`default_locale` / `default_timezone` 保留。

#### `im`（4 → 2）

保留：`progress_interval_sec`（仅 IM 渠道回复生命周期）、`inbound_batch_max_attachments`（单批附件业务上限）。

| 字段 | 新承载 |
|---|---|
| `inbound_batch_quiet_ms` | `IM_INBOUND_BATCH_QUIET_MS = 300` |
| `inbound_batch_max_wait_ms` | `IM_INBOUND_BATCH_MAX_WAIT_MS = 5000` |

**作用域事实**：`progress_interval_sec` 只在 `im-gateway/application/inbound.py` 的回复生命周期读取；WebChat 走 `webchat_service.admit_sync` + SSE 流，不经过 `inbound.consume`，也不读取该字段。WebChat 的心跳/增量节拍由 `RUN_EVENT_HEARTBEAT_SEC`、`STREAM_DELTA_FLUSH_INTERVAL_MS` / `STREAM_DELTA_FLUSH_BYTES` 管理。

#### `mcp`（1 → 0）

`max_tools_per_server` 下沉部署配置 `MCP_MAX_TOOLS_PER_SERVER = 200`；MCP 连接参数继续由 MCP 页面管理。分组没有字段后不渲染。

#### `capacity`（4 → 3）

保留：`max_open_runs_per_tenant` / `max_open_runs_per_user` / `queue_wait_timeout_sec`。

`model_wait_timeout_sec` 下沉部署配置 `CAPACITY_MODEL_WAIT_TIMEOUT_SEC = 30`；Run 创建边界把它冻结进 `policy_json.capacity`，旧快照缺键按当前部署默认解释。

#### `model_pool`（5 → 5，本需求不动）

`dispatch_epoch_sec` / `input_estimate_multiplier` / `interactive_weight` / `background_weight` / `aging_interval_sec` 维持现状。模型池调度单独需求讨论；本需求不新增/删除/下沉该分组字段。

#### `webchat`（16 → 8）

保留：`access_ttl_days`、`session_ttl_hours`、`text_max_bytes`、`upload_max_bytes`、`upload_max_per_message`、`notification_retention_days`、`history_retention_days`、`conversation_retention_days`。

| 字段 | 新承载 |
|---|---|
| `upload_staging_ttl_sec` | `WEBCHAT_UPLOAD_STAGING_TTL_SEC = 86400` |
| `history_page_size` | `WEBCHAT_HISTORY_PAGE_SIZE = 50`，client-config 仍返回 |
| `notification_poll_sec` | `WEBCHAT_NOTIFICATION_POLL_SEC = 5.0`，client-config 仍返回 |
| `reconnect_base_ms` | `WEBCHAT_RECONNECT_BASE_MS = 500`，client-config 仍返回 |
| `reconnect_cap_ms` | `WEBCHAT_RECONNECT_CAP_MS = 30000`，client-config 仍返回 |
| `reconnect_max_attempts` | `WEBCHAT_RECONNECT_MAX_ATTEMPTS = 10`，client-config 仍返回 |
| `stream_replay_retention_hours` | `WEBCHAT_STREAM_REPLAY_RETENTION_HOURS = 24` |
| `max_conversations_per_entry` | `WEBCHAT_MAX_CONVERSATIONS_PER_ENTRY = 50` |

**关键**：client-config 的响应形状不变（前端不感知字段已从平台设置移除），值来自 `SharedSettings`；`upload_staging_ttl_sec` 由 Runtime 清理任务使用；流重放保留与每入口会话上限由对应清理/归档服务使用。

#### `egress`（2 → 2，保留）

`enabled` / `rules_json` 保留：安全边界。

#### `schedule`（9 → 6）

保留：`max_schedules_per_user`、`min_schedule_interval_sec`、`max_fires_per_day_per_user`、`max_agentic_runs_per_day_per_user`、`max_concurrent_agentic_executions_per_user`、`history_retention_days`（新增，合并原 Fire/执行历史保留期）。

| 字段 | 处理 |
|---|---|
| `max_fire_history_days` | **合并** → `history_retention_days`；迁移取两者较大值，只延长不缩短 |
| `max_execution_history_days` | **合并** → `history_retention_days` |
| `max_snapshot_bytes` | `SCHEDULE_MAX_SNAPSHOT_BYTES = 262144`；`ScheduleSnapshotTooLarge` 语义不变 |
| `confirmation_ttl_sec` | `SCHEDULE_CONFIRMATION_TTL_SEC = 900` |

### 2.2 计数

| 分组 | 当前 | 保留 | 净变化 |
|---|---:|---:|---:|
| `compaction` | 4 | 4 | 0（已完成需求） |
| `agent` | 5 | 5 | 0 |
| `task` | 7 | 4 | -3 |
| `memory` | 21 | 11 | -10（删/下沉 11、新增总开关 1） |
| `learning` | 14 | 1 | -13（删 1、下沉 12） |
| `artifact` | 3 | 1 | -2 |
| `auth` | 4 | 4 | 0 |
| `locale` | 2 | 2 | 0 |
| `im` | 4 | 2 | -2 |
| `mcp` | 1 | 0 | -1 |
| `capacity` | 4 | 3 | -1 |
| `model_pool` | 5 | 5 | 0（单独需求） |
| `webchat` | 16 | 8 | -8 |
| `egress` | 2 | 2 | 0 |
| `schedule` | 9 | 6 | -3（删/下沉 4、新增合并字段 1） |
| **合计** | **101** | **58** | **-43** |

非压缩：97 → 54（净减 43）。删除 45 个叶子、新增 2 个（`memory.injection_enabled`、`schedule.history_retention_days`）。

### 2.3 环境键清单（42 个新增）

| 分组 | 环境键 | 默认 | 原字段 |
|---|---|---:|---|
| task | `TASK_MISFIRE_GRACE_SEC` | 60 | `misfire_grace_sec` |
| task | `TASK_DELIVERY_MAX_ATTEMPTS` | 5 | `delivery_max_attempts` |
| task | `TASK_DELIVERY_BACKOFF_BASE_SEC` | 5 | `delivery_backoff_base_sec` |
| memory | `MEMORY_MAX_RECALL_BYTES` | 4096 | `max_recall_bytes` |
| memory | `MEMORY_RECALL_DEFAULT_LIMIT` | 10 | `recall_default_limit` |
| memory | `MEMORY_MAX_BYTES_PER_USER` | 65536 | `max_memory_bytes_per_user` |
| memory | `MEMORY_CONSOLIDATION_MIN_USER_MESSAGES` | 1 | `consolidation_min_user_messages` |
| memory | `MEMORY_CONSOLIDATION_MAX_INPUT_BYTES` | 32768 | `consolidation_max_input_bytes` |
| memory | `MEMORY_CONSOLIDATION_MAX_OUTPUT_TOKENS` | 1024 | `consolidation_max_output_tokens` |
| memory | `MEMORY_CONSOLIDATION_TIMEOUT_SEC` | 30 | `consolidation_timeout_sec` |
| memory | `MEMORY_CONSOLIDATION_MAX_CANDIDATES` | 8 | `consolidation_max_candidates` |
| memory | `MEMORY_CONSOLIDATION_MAX_ATTEMPTS` | 2 | `consolidation_max_attempts` |
| memory | `MEMORY_CONSOLIDATION_RETRY_DELAY_SEC` | 5 | `consolidation_retry_delay_sec` |
| memory | `MEMORY_CONSOLIDATION_LEASE_SEC` | 60 | `consolidation_lease_sec` |
| learning | `LEARNING_RETRIEVAL_MIN_SCORE` | 0.35 | `retrieval_min_score` |
| learning | `LEARNING_RETRIEVAL_PREFERENCE_ITEMS` | 3 | `retrieval_preference_items` |
| learning | `LEARNING_RETRIEVAL_PREFERENCE_BYTES` | 512 | `retrieval_preference_bytes` |
| learning | `LEARNING_EXPERIENCE_MIN_SUCCESS_RUNS` | 3 | `experience_min_success_runs` |
| learning | `LEARNING_EXPERIENCE_AUTO_ACTIVATE` | `PROCEDURE,FAILURE_PATTERN` | `experience_auto_activate` |
| learning | `LEARNING_EXPERIENCE_TRIAL_RUNS` | 20 | `experience_trial_runs` |
| learning | `LEARNING_EXPERIENCE_TRIAL_MAX_REGRESSION` | 0.05 | `experience_trial_max_regression` |
| learning | `LEARNING_VERIFICATION_MAX_ATTEMPTS` | 2 | `verification_max_attempts` |
| learning | `LEARNING_EVAL_MIN_IMPROVEMENT` | 0.05 | `eval_min_improvement` |
| learning | `LEARNING_EVAL_MIN_CASES` | 10 | `eval_min_cases` |
| learning | `LEARNING_EVAL_MAX_CASES` | 50 | `eval_max_cases` |
| learning | `LEARNING_EVAL_MAX_ITEMS_PER_CASE` | 10 | `eval_max_items_per_case` |
| artifact | `ARTIFACT_MAX_ARCHIVE_FILES` | 2000 | `max_archive_files` |
| artifact | `ARTIFACT_CLEANUP_BATCH_SIZE` | 500 | `cleanup_batch_size` |
| im | `IM_INBOUND_BATCH_QUIET_MS` | 300 | `inbound_batch_quiet_ms` |
| im | `IM_INBOUND_BATCH_MAX_WAIT_MS` | 5000 | `inbound_batch_max_wait_ms` |
| mcp | `MCP_MAX_TOOLS_PER_SERVER` | 200 | `max_tools_per_server` |
| capacity | `CAPACITY_MODEL_WAIT_TIMEOUT_SEC` | 30 | `model_wait_timeout_sec` |
| webchat | `WEBCHAT_UPLOAD_STAGING_TTL_SEC` | 86400 | `upload_staging_ttl_sec` |
| webchat | `WEBCHAT_HISTORY_PAGE_SIZE` | 50 | `history_page_size` |
| webchat | `WEBCHAT_NOTIFICATION_POLL_SEC` | 5.0 | `notification_poll_sec` |
| webchat | `WEBCHAT_RECONNECT_BASE_MS` | 500 | `reconnect_base_ms` |
| webchat | `WEBCHAT_RECONNECT_CAP_MS` | 30000 | `reconnect_cap_ms` |
| webchat | `WEBCHAT_RECONNECT_MAX_ATTEMPTS` | 10 | `reconnect_max_attempts` |
| webchat | `WEBCHAT_STREAM_REPLAY_RETENTION_HOURS` | 24 | `stream_replay_retention_hours` |
| webchat | `WEBCHAT_MAX_CONVERSATIONS_PER_ENTRY` | 50 | `max_conversations_per_entry` |
| schedule | `SCHEDULE_MAX_SNAPSHOT_BYTES` | 262144 | `max_snapshot_bytes` |
| schedule | `SCHEDULE_CONFIRMATION_TTL_SEC` | 900 | `confirmation_ttl_sec` |

新增键必须先登记默认值、消费点和取值范围；`.env.example`、base ConfigMap、内网/生产 overlay、配置档案同步更新。`SharedSettings` 启动校验保留跨字段约束（例如 `MEMORY_CONSOLIDATION_LEASE_SEC ≥ ...`、容量与模型池连接数关系）。

### 2.4 数据与迁移

**表**：无新增表；`control.platform_setting` 继续 append-only。

**迁移步骤**（幂等；只处理非压缩分组）：

1. 读取每个租户最新的 `settings_json`。
2. 删除 §2.1 的 45 个被删字段（数组/对象键）；保留其余键。
3. `memory`：写入 `injection_enabled = old.max_injected_memories > 0 && old.max_injected_bytes > 0`；`max_injected_memories` / `max_injected_bytes` 原值保留。
4. `schedule`：写入 `history_retention_days = max(old.max_fire_history_days, old.max_execution_history_days)`；删除两个旧键；审计记录原值。
5. 追加 `revision+1`，`actor_user_id=UUID(int=0)`，审计 `action=RESTORE`（沿用系统迁移口径），`before/after` 完整记录。
6. 遍历 `control.agent_definition.runtime_config_json`，删除被删字段的覆盖键（尤其 `memory`/`learning`/`task`/`webchat` 下若存在的旧键）；其他键逐字保留。
7. 旧 revision 读取：`PlatformSettingsService.read_revision` 对含旧键的文档返回显式“schema 过旧”错误（400），不静默补默认；`restore` 同样拒绝。
8. 开发阶段可 `TRUNCATE control.platform_setting` 并清理 Agent 覆盖，重建最新版本；迁移脚本必须幂等。
9. `compaction` 已是 4 个字段：迁移不得删除/改写 `history_budget_tokens`、`tool_result.persist_threshold_bytes`、`summary.*`；迁移测试包含压缩分组回归。

**快照兼容**：发布前排空在飞 Run/Task（沿用部署文档的维护窗口）。历史终态快照只读；旧快照恢复执行按“schema 过旧”显式拒绝，不做双读/补默认的兼容层。`policy_json` 新增 `memory_limits`，`learning`/`capacity` 子节按需写入；Task `execution_snapshot` 的 `budget` 增加 5 个 Agent 叶子，`TASK_SNAPSHOT_SCHEMA_VERSION` 相应升级。

**配置迁移**：环境键不落库；部署时由 base ConfigMap + overlay 提供。缺少新键时 `SharedSettings` 使用默认并记录 warning，不读取平台设置旧字段。

## 3. 接口设计

### 3.1 Console 设置目录

- `build_groups` 继续按 schema 全量派生；本需求不新增 API。
- `_FIELD_META` 删除被删字段的条目；保留字段的 min/max/unit 不变；`mcp` 分组没有字段后不渲染。
- `im.progress_interval_sec` 的标签改为“IM 回复进度节拍”，说明写明只对 IM 渠道生效。
- 分组字段数由 101 降到 58；`readonly_notes` 不需要新增。

### 3.2 WebChat client-config

- 响应字段保持不变；`history_page_size` / `notification_poll_sec` / `reconnect_*` 从 `SharedSettings` 取值，不再从平台设置快照取。
- `upload_staging_ttl_sec` 不在 client-config 返回；Runtime 清理任务使用 `SharedSettings`。
- `stream_replay_retention_hours` / `max_conversations_per_entry` 由对应清理/归档服务从 `SharedSettings` 读取。

### 3.3 IM 入站批次与进度

- Gateway/Connector 的 `InboundBatchPolicy` 从 `SharedSettings` 的 `IM_INBOUND_BATCH_*` 取值；`im.inbound_batch_max_attachments` 仍来自平台设置。
- `im.progress_interval_sec` 仍来自平台设置；WebChat 不读取它。
- Connector 的 `platform_settings.py` 保留 `progress_interval_sec` 与 `inbound_batch_max_attachments` 的映射；静默窗口/最长等待用共享环境值。

### 3.4 后台任务统一预算

- `TaskSubmissionContext` 增加 `agent_policy: AgentPolicy`（来自 Run 已冻结策略）；`build_task_snapshot` 把它写入 `budget` 的 5 个叶子。
- 定时任务 admission 的 `_snapshot_budget` 从 `AgentPolicy.from_runtime_config(agent.runtime_config, base=PlatformSettings.agent)` 取值，与交互式 Run 同一构建函数；Agent 级 `runtime_config.budget` 覆盖同样适用于定时任务，旧 payload/Revision budget 仍不参与。
- `agentic_skills._timeout()` 改为 `min(agent.deadline_ms / 1000, remaining_task_deadline)`；直接 Skill 任务仍用 `task.execution_timeout_sec`。
- Worker 的 `agent_policy(frozen)` 只读冻结值；缺键按旧快照显式失败或 schema 默认（仅限已排空的旧版本）。

### 3.5 部署配置档案

**文件布局**：

```
deploy/k8s/
  base/
    configmap.yaml            # 全部非敏感键 + base 默认
  overlays/
    intranet/
      kustomization.yaml
      configmap-patch.yaml    # 内网取值（Redis cluster、WECOM、TLS 1.2 等）
    production/
      kustomization.yaml
      configmap-patch.yaml    # 生产取值（容量 profile 锁定值、渠道开关）
docs/configuration-profiles.md # 逐键默认/内网/生产对照
```

**契约**：

- base ConfigMap 键集 == `SharedSettings` 字段集 - Secret 键集；不得出现未知键。按当前 146 个字段 + 新增 42 个计算，共 188 个环境字段，其中 5 个 Secret，ConfigMap 覆盖 183 个非敏感键。
- Secret 键集由 `SharedSettings` 的 `repr=False` 字段唯一派生（当前 5 个：数据库/Redis/内部令牌/Webhook 签名/外部断言因子），不另写硬编码清单。
- Secret 键只出现在 `secret.example.yaml` / `with-connector/secret-connector.example.yaml`，ConfigMap 契约测试显式拒绝。
- overlay 只写与 base 不同的值；最终渲染结果由测试合并后校验，不允许“缺键靠代码默认”。
- 内网/生产值通过 `SharedSettings(**merged_env)` 构造校验；任何跨字段约束失败都阻断发布。
- 生产容量值必须来自锁定的 capacity profile；配置档案测试比对关键容量键与 profile。
- 配置档案文档由脚本生成/校验（如 `scripts/render_configuration_profiles.py`），避免手工漂移。

**配置分层：用户面 vs 基础设施**

每个配置键在档案中声明 `scope`：

- `user-facing`：会影响用户实际感受的值，例如 Agent 预算相关默认、记忆/学习阈值、IM 入站聚合、WebChat 客户端分页/轮询/重连、调度确认时效、MCP 工具规模、平台通道/模型接入开关。**内网/UAT 与生产默认必须一致**；差异必须进入显式 allowlist 并写明原因、批准人和失效条件。
- `infrastructure`：只随部署规模/网络/容量变化的值，例如 Redis seed、DB 池、副本/并发、SSE 缓冲、资源上限、清理批大小、日志级别。允许按环境不同，但必须在档案中写明取值依据。

`tests/test_deployment_config_profiles.py` 增加 parity 断言：`user-facing` 键在内网与生产 overlay 中逐键相等；allowlist 之外的差异直接失败。这样内网/UAT 的真实场景测试才有生产代表性。

如果当前只有一个内网集群、生产集群尚未就绪：不制造两套假差异。把内网作为 **production-like 验收环境**，`production` overlay 只作为上线前待填模板（`user-facing` 键从内网复制，`infrastructure` 键待生产集群确认），并在配置档案中明确标记 `template` 状态。等生产集群就绪后再补齐基础设施值，不改变用户面配置。

**已知环境差异（示例，具体值以档案文件为准）**：

| 键 | scope | 内网 | 生产 |
|---|---|---|---|
| `ENV` | infrastructure | `intranet` | `prod` |
| `REDIS_MODE` | infrastructure | `cluster` | `cluster` |
| `REDIS_CLUSTER_SEEDS` | infrastructure | 内网 3 主 seed | 生产 3 主 seed |
| `WECOM_MEDIA_MAX_TLS_VERSION` | infrastructure | `TLSv1_2` | 由生产安全策略决定 |
| `GATEWAY_ROUTED_CHANNELS` | user-facing | `WECOM` | 与内网一致（除非渠道范围不同） |
| `RUNTIME_EXECUTION_CONCURRENCY` | infrastructure | 按内网容量 profile | 按生产容量 profile |
| `GATEWAY_MAX_SSE_SUBSCRIBERS` | infrastructure | 按内网容量 profile | 按生产容量 profile |

**租户设置基线**：除环境配置外，租户级平台设置也要有 production-like 基线，例如 `deploy/profiles/platform-settings.production-like.json`（`agent` 5 项、`memory`、`learning`、`im`、`webchat`、`capacity`、`schedule` 等）。该文件由 `parse_platform_settings` 校验，不含 Secret/资源 ID；内网/UAT 与生产按同一份基线配置，并在验收记录中保留两边实际值的 diff。基线变更走正常设置保存与审计，不由部署脚本静默覆盖。

**真实场景验收环境**：

- dev/CI 只用 `tools/fake-business-platform` 的 fake MSSP/MSSW 做契约、迁移和 UI 回归；不得把 fake 结果当成真实用户感受验收。
- 内网/UAT 必须接真实 MSSP/MSSW：`EXTERNAL_ASSERTION_PLATFORMS_JSON` 写入真实验签因子（Secret，`optional: true`），重启 Console 与 Gateway；管理员建入口、绑定真实用户；按 `docs/operations/mssw-channel-deploy.md` 验证平台列表与会话。
- 真实场景清单至少覆盖：MSSP/MSSW 登录与入口、流式对话、停止、上传/下载、历史/通知、企微进度、后台 Agentic/Skill 任务、修改 Agent 上限后的新 Run/新任务。
- 证据记录 run id、平台请求 id、截图、关键日志与生效配置；不得记录 Secret 明文。
- 生产发布门禁：没有内网/UAT 真实场景验收记录，不得以“dev 通过”作为上线依据。

### 3.6 设置保存错误

- 旧字段被提交时按未知键拒绝（现有 fail-closed 行为），错误走 `harness-api#RULE-api-001` 的统一封套。
- 环境字段不再出现在字段路径中，不会产生“字段不存在”之外的兼容分支。

## 4. 质量实现方案

### 4.1 正确性

- 保留下沉字段的**当前默认值**，逐字段写等价测试；不得在迁移时改变默认。
- `AgentPolicy` 的 5 个叶子在交互式 Run 与后台 Agentic 任务间保持一致；测试直接比较两边的冻结值。
- `FrozenLearning` / `MemoryPolicySettings` / `PoolExecutionPolicy` 的字段来源调整后，冻结 JSON 的键集变化必须同步读取方；旧行按“缺少键 = 当前部署默认”处理，不补平台默认。
- `task.batch_max_concurrency` 保留；测试断言实际并发仍取“租户设置 ≤ `batch_platform_limit`”的较小值。
- WebChat client-config 字段形状不变；契约测试断言值来自 `SharedSettings` 且与前端期望一致。
- `memory.injection_enabled=false` 时自动注入路径不产生记忆/任务线索；显式召回仍可用。
- `schedule.history_retention_days` 迁移取较大值；清理任务不得因合并提前删除 Fire/执行历史。

### 4.2 迁移与兼容

- 迁移真实 PostgreSQL 测试：旧文档含全部被删字段 + Agent 覆盖旧键 → 迁移 → 设置读取/保存/新 Run/新任务准入成功；旧 revision 恢复返回 400。
- 迁移幂等：重复执行不产生多余 revision；第二次无变更时不追加空 revision（或追加但 `changed_keys` 为空，需明确）。
- 压缩字段回归：本迁移后 `compaction` 仍为 4 个字段，`settings_json` 仍满足压缩 schema。
- 部署排空：发布前排空在飞 Run/Task；旧快照恢复执行显式拒绝，不静默补默认。

### 4.3 可观测性

- 迁移日志：tenant_id、before/after revision、删除字段数、合并前后值、Agent 行数；不记录敏感值。
- 设置读取失败：现有 `platform_settings_fetch_total{result="failed"}` 保持。
- 配置启动日志：打印非敏感的生效值（如 `batch_platform_limit`、新增环境键的分组计数），便于部署核对。
- 配置档案渲染失败：发布预检非零退出。

## 5. 测试与验证

- `tests/settings_field_inventory.py`（新增）：字段分类清单、保留/下沉/合并/删除集合、consumer 路径、i18n 键、页面字段集与 schema 逐字段比对。
- `tests/test_platform_settings_schema.py`：新 schema 字段集/默认值/范围；被删字段拒绝；`memory.injection_enabled` 与 `schedule.history_retention_days` 的默认/边界。
- `tests/console_platform/test_platform_settings_api.py`：设置目录字段集、分组计数、保存/读取、旧文档迁移。
- `tests/frontend/` 设置页契约：总叶子数 58、每个分组精确字段集、被删字段不在 DOM/API、`mcp` 分组不渲染。
- `tests/migrations/test_settings_page_simplification.py`（新增，真实 PG）：旧文档 + Agent 覆盖迁移、记忆总开关计算、调度保留期合并、旧 revision 拒绝、幂等、压缩分组回归。
- `tests/agent_runtime/test_agent_policy_freeze.py`（新增）：`snapshot_policy` 冻结全部 5 个 Agent 叶子；Run policy 与 `AgentPolicy.from_runtime_config` 一致。
- `tests/agent_worker/test_task_budget_bridge.py`（新增）：Run 发起的 Task 快照携带 5 个叶子；定时 Agentic 任务与交互式 Run 的预算一致；修改设置只影响新对象。
- `tests/agent_worker/test_agentic_skill_timeout.py`（新增/改写）：Agentic Skill 工具超时不超过 Agent deadline；直接 Skill 任务仍用 `task.execution_timeout_sec`。
- `tests/test_deployment_config_profiles.py`（新增）：`SharedSettings` 键集 == `.env.example`；base+overlay == 全部非敏感键；Secret 隔离；内网/生产值通过校验；生产容量键与 locking profile 一致；`user-facing` 键在内网/生产之间 parity（allowlist 除外）；`platform-settings.production-like.json` 能通过 `parse_platform_settings`。
- `tests/acceptance/real_platform/`（人工验收清单）：真实 MSSP/MSSW 连接、入口绑定、流式/停止/上传/下载/历史/通知、企微进度、后台任务；不纳入 CI，证据按场景文档记录。
- consumer 测试：`tests/agent_worker/test_batch_fanout.py`、`tests/agent_worker/test_delivery*.py`、`tests/agent_runtime/test_memory_redesign*.py`（consolidation 默认值）、`tests/gateway/test_inbound_batch*.py`、`tests/test_webchat_client_config.py`、`tests/agent_worker/test_schedule_snapshot*.py`、`tests/console_platform/test_artifact_cleanup*.py`。
- `tests/test_configuration_inventory.py`：重新生成 CSV，删除 `policy-field` 行，新增 `shared-setting` 行。
- `tests/settings_page_simplification_inventory.py`：本需求 TASK/场景/测试闭合表（按 `harness-test` 口径）。

## 6. 发布与回滚

- **发布顺序**：排空在飞 Run/Task → 本需求迁移 → 部署新 ConfigMap/overlay → 新镜像滚动。
- **迁移 Job**：用 Console 镜像 + Alembic/数据脚本；事务内追加 revision 与审计；失败整事务回滚。
- **配置发布**：先 apply base ConfigMap 与目标 overlay，再滚动四个 Deployment；ConfigMap 更新不会自动触发滚动。
- **回滚**：新代码读到旧设置文档会 fail-closed；回滚镜像前必须按反向迁移恢复旧字段（或开发阶段直接重置）。配置回滚到上一版 ConfigMap + 重启 Pod；不得声称“只回退镜像”。
- **环境键**：更新 `.env.example`、base ConfigMap、内网/生产 overlay、配置档案与启动校验；漏配时使用代码默认并记录 warning，不回落到平台设置旧字段。
- **真实场景门禁**：生产发布前必须完成内网/UAT 真实 MSSP/MSSW 场景验收；`user-facing` 配置 parity 检查通过；dev/fake 平台结果不能替代。
- **监控**：迁移后观察 `platform_settings_fetch_total`、设置保存失败率、新 Run/任务准入失败率、Task 投递/调度错误率、WebChat 重连/分页错误率、Agent deadline/turn 上限指标。

## 7. 风险与依赖

| 风险ID | 描述 | 影响 | 缓解方案 |
|---|---|---|---|
| RISK-01 | 默认值漂移 | 行为变化 | 逐字段等价测试；迁移前后对比 |
| RISK-02 | consumer 漏改 | 运行时读不到字段 | 字段清单 + 全仓 grep + consumer 测试 |
| RISK-03 | 迁移遗漏 Agent 覆盖或旧 revision | 读取/准入失败 | 真实 PG 迁移测试；旧 revision 显式拒绝 |
| RISK-04 | 后台任务预算与交互式 Run 不一致 | 同一租户两套上限 | 统一 `AgentPolicy` 构建函数 + 快照对比测试 |
| RISK-05 | 42 个环境键漏配/漂移 | 内网/生产行为不一致 | base 全量 + overlay 差异 + 配置档案测试 |
| RISK-06 | WebChat client-config 形状变化 | 前端断连/分页异常 | 保持响应形状，值来自 `SharedSettings`；契约测试 |
| RISK-07 | 迁移误改已稳定的压缩字段 | 压缩 schema 被破坏 | 迁移只删除非压缩键；压缩分组回归测试 |
| RISK-08 | 内网/UAT 与生产用户面配置漂移 | 内网通过、生产体验不同 | `user-facing` parity 测试 + 差异 allowlist |
| RISK-09 | 真实 MSSP/MSSW 不可达/凭据未就绪 | 真实场景验收阻塞 | 提前申请联调窗口与 Secret；blocked 场景显式记录，不以 fake 替代 |

## 8. 规范引用

| 规范 | 约束 |
|---|---|
| `api/harness-api#RULE-api-001` | 设置校验错误走统一封套与错误码 → §3.6 |
| `persistence/harness-data#RULE-data-001` | 版本表追加、审计、JSONB 不原地改历史 → §2.4 |
| `frontend/harness-frontend` | 设置页只通过契约访问 API；不写死字段清单 → §3.1/§5 |
| `ui/harness-ui` | 字段/分组展示与错误定位沿用现有组件 → §3.1 |
| `i18n/harness-i18n` | 词条增删同步 zh-CN/en-US → §5 |
| `memory/harness-memory` | consolidation 租约/超时/输入输出不变量保留为代码不变量 → §2.1 |
| `worker/harness-worker` | Task 调度/投递默认值下沉后仍按部署环境与代码常量生效 → §2.1/§3.4 |
| `im/harness-im` | 入站批次行为不变，Connector/Gateway 同源；进度节拍只作用于 IM 渠道 → §3.3 |
| `test/harness-test` | 配置清单重生成、真实 PG 迁移测试、需求 inventory → §5 |
| `arch/harness-arch#RULE-arch-001` | 四个部署单元边界不变 → §3.5 |

---

*文档结束*
