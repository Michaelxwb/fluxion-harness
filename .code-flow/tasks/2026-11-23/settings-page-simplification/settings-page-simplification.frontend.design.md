---
design_template: frontend
---
# 系统设置页精简：Console 前端设计

> v0.2 · 2026-11-27 · 待评审 · FEAT-01、FEAT-03、FEAT-05、FEAT-07

## 入口与页面

不新增页面、不改路由。继续使用「系统设置」单页；字段由后端设置目录（dataclass 派生）自动渲染。前端只负责：

- 分组/分区展示顺序；
- 字段词条、单位、范围与说明；
- 保存错误落到字段；
- 契约测试断言“页面字段集 = 后端字段集”。

**不采用**“前端隐藏字段”：隐藏字段仍然存在 schema、consumer、i18n 与迁移里，管理员既看不到也排查不了。字段必须从后端 schema 与页面同时消失。字段数为 0 的分组（本需求的 `mcp`）不渲染。

## 页面结构

设置页按“租户决策”重新分区；后端分组键不变，前端只做展示分区：

| 展示分区 | 包含分组 | 字段数 |
|---|---|---:|
| 上下文与执行 | `compaction`（4）、`agent`（5） | 9 |
| 任务与调度 | `task`（4）、`schedule`（6） | 10 |
| 记忆与学习 | `memory`（11）、`learning`（1） | 12 |
| 容量与模型池 | `capacity`（3）、`model_pool`（5） | 8 |
| 认证与本地化 | `auth`（4）、`locale`（2） | 6 |
| 集成 | `im`（2）、`egress`（2） | 4 |
| 数据保留 | `artifact`（1）、`webchat`（8） | 9 |
| **合计** | **14 个分组** | **58** |

`compaction` 的 4 个字段来自 `conversation-context-retention`；本需求不重复定义其词条。`mcp` 分组没有租户字段，不渲染。

## 字段与词条

### 删除/下沉的字段与词条

| 分组 | 删除/下沉的字段 | 删除的词条 |
|---|---|---|
| `task` | `misfire_grace_sec`、`delivery_max_attempts`、`delivery_backoff_base_sec` | 对应 `settings.field.task.*` |
| `memory` | `max_recall_bytes`、`recall_default_limit`、`max_memory_bytes_per_user`、`consolidation_min_user_messages`、`consolidation_max_input_bytes`、`consolidation_max_output_tokens`、`consolidation_timeout_sec`、`consolidation_max_candidates`、`consolidation_max_attempts`、`consolidation_retry_delay_sec`、`consolidation_lease_sec` | 对应 `settings.field.memory.*` |
| `learning` | `signal_detection_enabled`、`retrieval_min_score`、`retrieval_preference_items`、`retrieval_preference_bytes`、`experience_min_success_runs`、`experience_auto_activate`、`experience_trial_runs`、`experience_trial_max_regression`、`verification_max_attempts`、`eval_min_improvement`、`eval_min_cases`、`eval_max_cases`、`eval_max_items_per_case` | 对应 `settings.field.learning.*` |
| `artifact` | `max_archive_files`、`cleanup_batch_size` | 对应 `settings.field.artifact.*` |
| `im` | `inbound_batch_quiet_ms`、`inbound_batch_max_wait_ms` | 对应 `settings.field.im.*` |
| `mcp` | `max_tools_per_server` | `settings.field.mcp.max_tools_per_server`；整个分组不渲染 |
| `capacity` | `model_wait_timeout_sec` | `settings.field.capacity.model_wait_timeout_sec` |
| `webchat` | `upload_staging_ttl_sec`、`history_page_size`、`notification_poll_sec`、`reconnect_base_ms`、`reconnect_cap_ms`、`reconnect_max_attempts`、`stream_replay_retention_hours`、`max_conversations_per_entry` | 对应 `settings.field.webchat.*` |
| `schedule` | `max_fire_history_days`、`max_execution_history_days`、`max_snapshot_bytes`、`confirmation_ttl_sec` | 对应 `settings.field.schedule.*` |

`schedule` 新增一个合并字段 `history_retention_days`（“调度历史保留期”），说明写“触发记录和执行历史共享保留期；在途执行与待投递记录受保护”。

### 保留字段的词条与展示

- `agent` 5 项全部保留；词条不变，并明确“交互式 Run 与后台 Agentic 任务共用这份执行预算”。
- `auth`、`locale`、`egress`、`model_pool` 的词条不变。
- `task` 只保留：`default_deadline_hours`、`execution_timeout_sec`、`max_attempts`、`batch_max_concurrency`。`execution_timeout_sec` 说明写“直接 Skill 任务的单次子进程超时；Agentic 执行使用 Agent 执行预算”。
- `memory` 只保留：写入开关、注入总开关、注入条数/字节、推断注入、任务线索注入、单用户条数、三个 TTL、consolidation 开关。
- `learning` 只保留：`learning_enabled`。
- `artifact` 只保留：`retention_days`。
- `im` 只保留：`progress_interval_sec`、`inbound_batch_max_attachments`。
- `capacity` 只保留：租户/用户未结束 Run 上限、队列等待上限。
- `webchat` 只保留：访问/会话 TTL、文本/上传大小、每消息上传数、通知保留、历史保留、会话保留。
- `schedule` 只保留：配额、最小间隔、每日 Fire/Agentic、并发、历史保留期。
- `model_pool` 5 项维持现状，本需求不改。

### IM 进度的作用域说明

`im.progress_interval_sec` 的标签改为 **「IM 回复进度节拍」**，说明写：

> 仅对经 IM Gateway 回复的 IM 渠道（当前为企微/远程 Connector 路由渠道）生效。WebChat 使用 SSE 流式事件与心跳，不受此设置影响。

不得把该字段描述成“所有聊天入口的进度节拍”。若后续需要 WebChat 进度节拍，另立需求。

### 分组说明

每个分组保留一行说明，明确“这里是租户策略”；被下沉的技术参数不再显示，运维通过部署配置档案管理。不得用“高级设置”折叠隐藏已删除字段。

## 校验与错误

- 保留字段的 min/max/unit 继续由后端目录下发；前端不写死范围。
- 旧字段被提交时按未知键拒绝，错误按字段路径落到控件；不新增前端兼容分支。
- 删除字段不产生空的表单行；分组内没有字段时整组不渲染（本需求的 `mcp`）。

## WebChat 客户端

`history_page_size`、`notification_poll_sec`、`reconnect_*` 从设置页移除，但 client-config 响应形状不变，值来自后端 `SharedSettings`；WebChat 前端不需要改类型或逻辑。设置页不再显示这些项。

IM 入站静默窗口/最长等待同理：Connector/Gateway 用共享环境值，设置页保留回复进度与单批附件上限。

## 审计与可观测

- 设置保存审计的 `changed_keys` 不再包含被删字段；迁移追加 revision 的审计可在 Console 版本历史中看到。
- 旧 revision 恢复时前端显示后端返回的“schema 过旧”错误，不静默恢复。
- 迁移后设置页首屏字段数从 101 降到 58；可在组件测试中断言。

## 验收与测试

- 真实用户场景验收在**内网/UAT**执行：接真实 MSSP/MSSW、真实模型、真实企微；dev/local 只用 fake 平台做契约与 UI 回归，不作为“真实感受”验收。
- 内网/UAT 的 `user-facing` 配置必须与生产一致；基础设施配置允许不同。
- 人工场景见 `settings-page-simplification.scenarios.md` 的 S-01、S-02、S-03、S-04、S-05、S-06、E-01、E-02、E-03、E-04、E-05、B-01、B-02、B-03、R-01、R-02。
- 前端契约测试：
  - 总叶子数 58、14 个分组；
  - 每个分组精确字段集（不允许“多一个隐藏字段”）；
  - `mcp` 分组不渲染；
  - 被删字段不在 DOM、不在 catalog、不在 i18n；
  - WebChat client-config 仍返回分页/轮询/重连字段，值来自后端配置；
  - IM 进度字段带“仅 IM 渠道”说明；
  - 保存错误仍落到保留字段。
- i18n 同步 `zh-CN` / `en-US`，删除被删字段词条，新增 `schedule.history_retention_days` 与 `memory.injection_enabled` 词条。

## 发布

前端产物随 Console 镜像发布。发布后：

1. 打开系统设置，确认 14 个分组、58 个字段、无被删字段、无 MCP 分组；
2. 保存一个保留字段并刷新；
3. 打开 WebChat，确认历史分页、通知轮询、重连参数仍按后端配置工作；
4. 打开旧 revision 恢复，确认“schema 过旧”错误可见；
5. 检查 IM 进度字段说明，确认不会误导为 WebChat 节拍。
