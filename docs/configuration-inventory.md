# 全仓配置盘点与生效边界

审计日期：2026-10-04。基于当前 `main`；本文件是现状盘点与目标分类，尚未实现系统设置页或迁移配置来源。

## 分类结论

| 类别 | 目标存放位置 | 修改后的生效方式 |
|---|---|---|
| business | Console 系统设置 → 持久化业务设置 | 保存成功后，后续请求/新 Run/新任务立即读取已提交版本，不等待缓存 TTL、不重启 |
| business-resource | 已有 Agent / 模型 / MCP / 项目平台页面中的资源配置 | 保留资源级覆盖；系统设置只提供平台默认，不复制现有资源表 |
| environment | `.env`；集群同义项通过 Secret / ConfigMap / Deployment 注入 | 校验启动配置，重启消费此配置的服务；不放进系统设置 |
| code | 协议、状态枚举、schema、字段长度、算法不变量、UI 原语 | 随代码发布，不变成可任意填写的环境变量或系统设置 |

“立即生效”暂按**保存成功后开始的新操作立即使用新配置**理解。执行中的 Run / Task 继续用其 execution snapshot；这是现有快照机制的明确行为，不应把已经执行的任务中途换策略。若需要执行中更新，必须逐字段定义可动态变更范围，不能整体覆盖 snapshot。

## 盘点范围与完整明细

遍历 `apps/`、`packages/` 下全部 297 个 Python 文件与 98 个 TS/TSX 文件：Python 大写命名常量（含类内枚举）、配置/策略类默认值、带 timeout/ttl/limit/budget 等语义的函数默认参数、直接环境读取和部分内联 I/O 参数，以及前端大写命名常量。另人工核对 `.env.example`、`scripts/dev.sh`、Vite 和 k8s 部署配置。

完整声明明细见 [configuration-inventory.csv](configuration-inventory.csv)。CSV 每行包含源码路径、行号、名称、当前默认表达式、声明类型、目标分类和理由；重复声明不等于独立设置项。只读取 `.env.example` 的键名，不读取或输出真实 `.env` 的秘密值。

扫描不把每个 SQL `.limit(1)`、计数器初始零、CSS 尺寸或循环算术都当作配置；调用方传入且没有默认值的参数不属于常量默认。本清单是当前源码快照，不能宣称通过语法扫描穷尽所有隐含业务策略。

声明点合计 **995**：`code` 777、`environment` 113、`business` 93、`business-resource` 12。其中大量 `code` 行是状态码、事件名和协议文本。

## SharedSettings：45 项逐项归属

11 项移入业务设置，34 项保留环境设置。表中环境键名是现有 Pydantic 字段名对应的大写键，不是新增别名。

| 现有字段 / 环境键 | 当前默认 | 目标 |
|---|---|---|
| `env` / `ENV` | `'dev'` | .env + 重启 |
| `log_dir` / `LOG_DIR` | `'./.data/logs'` | .env + 重启 |
| `log_level` / `LOG_LEVEL` | `'INFO'` | .env + 重启 |
| `default_locale` / `DEFAULT_LOCALE` | `'zh-CN'` | 系统设置 |
| `default_tenant_id` / `DEFAULT_TENANT_ID` | `'default'` | .env + 重启 |
| `api_messages_file` / `API_MESSAGES_FILE` | `'./config/api-messages.yaml'` | .env + 重启 |
| `default_timezone` / `DEFAULT_TIMEZONE` | `'Asia/Shanghai'` | 系统设置 |
| `database_url` / `DATABASE_URL` | `None` | .env + 重启 |
| `redis_url` / `REDIS_URL` | `None` | .env + 重启 |
| `internal_service_token` / `INTERNAL_SERVICE_TOKEN` | `None` | .env + 重启 |
| `channel_probe_url` / `CHANNEL_PROBE_URL` | `None` | .env + 重启 |
| `wecom_ws_url` / `WECOM_WS_URL` | `None` | .env + 重启 |
| `wecom_ws_ca_file` / `WECOM_WS_CA_FILE` | `None` | .env + 重启 |
| `artifact_root` / `ARTIFACT_ROOT` | `'./.data/artifacts'` | .env + 重启 |
| `artifact_retention_days` / `ARTIFACT_RETENTION_DAYS` | `30` | 系统设置 |
| `mcp_max_tools_per_server` / `MCP_MAX_TOOLS_PER_SERVER` | `200` | 系统设置 |
| `skill_cache_root` / `SKILL_CACHE_ROOT` | `'./.data/skill-cache'` | .env + 重启 |
| `migrations_dir` / `MIGRATIONS_DIR` | `'./migrations/versions'` | .env + 重启 |
| `context_settings_cache_ttl_sec` / `CONTEXT_SETTINGS_CACHE_TTL_SEC` | `10` | .env + 重启 |
| `console_platform_url` / `CONSOLE_PLATFORM_URL` | `'http://127.0.0.1:8000'` | .env + 重启 |
| `agent_runtime_url` / `AGENT_RUNTIME_URL` | `'http://127.0.0.1:8001'` | .env + 重启 |
| `agent_worker_url` / `AGENT_WORKER_URL` | `'http://127.0.0.1:8002'` | .env + 重启 |
| `im_gateway_url` / `IM_GATEWAY_URL` | `'http://127.0.0.1:8003'` | .env + 重启 |
| `im_progress_interval_sec` / `IM_PROGRESS_INTERVAL_SEC` | `Field(default=5.0, ge=1.0)` | 系统设置 |
| `im_progress_updates_per_second` / `IM_PROGRESS_UPDATES_PER_SECOND` | `Field(default=10.0, ge=1.0)` | .env + 重启 |
| `run_lease_sec` / `RUN_LEASE_SEC` | `60` | .env + 重启 |
| `run_heartbeat_sec` / `RUN_HEARTBEAT_SEC` | `20` | .env + 重启 |
| `run_reaper_interval_sec` / `RUN_REAPER_INTERVAL_SEC` | `30` | .env + 重启 |
| `run_event_heartbeat_sec` / `RUN_EVENT_HEARTBEAT_SEC` | `15` | .env + 重启 |
| `task_lease_sec` / `TASK_LEASE_SEC` | `60` | .env + 重启 |
| `task_heartbeat_sec` / `TASK_HEARTBEAT_SEC` | `20` | .env + 重启 |
| `task_cancel_check_sec` / `TASK_CANCEL_CHECK_SEC` | `2` | .env + 重启 |
| `task_default_deadline_hours` / `TASK_DEFAULT_DEADLINE_HOURS` | `24` | 系统设置 |
| `worker_poll_interval_sec` / `WORKER_POLL_INTERVAL_SEC` | `5` | .env + 重启 |
| `task_max_attempts` / `TASK_MAX_ATTEMPTS` | `3` | 系统设置 |
| `batch_max_concurrency` / `BATCH_MAX_CONCURRENCY` | `8` | 系统设置 |
| `batch_platform_limit` / `BATCH_PLATFORM_LIMIT` | `16` | .env + 重启 |
| `scheduler_poll_interval_sec` / `SCHEDULER_POLL_INTERVAL_SEC` | `10` | .env + 重启 |
| `scheduler_batch_size` / `SCHEDULER_BATCH_SIZE` | `100` | .env + 重启 |
| `task_deadline_sweep_interval_sec` / `TASK_DEADLINE_SWEEP_INTERVAL_SEC` | `30` | .env + 重启 |
| `misfire_grace_sec` / `MISFIRE_GRACE_SEC` | `60` | 系统设置 |
| `delivery_poll_interval_sec` / `DELIVERY_POLL_INTERVAL_SEC` | `5` | .env + 重启 |
| `delivery_batch_size` / `DELIVERY_BATCH_SIZE` | `20` | .env + 重启 |
| `delivery_max_attempts` / `DELIVERY_MAX_ATTEMPTS` | `5` | 系统设置 |
| `delivery_backoff_base_sec` / `DELIVERY_BACKOFF_BASE_SEC` | `5` | 系统设置 |

业务项的归属理由：

- `default_locale` / `default_timezone`：平台业务默认；已有用户语言、每条 Schedule 的时区继续优先，不通过全局修改重写既有 Schedule。
- `artifact_retention_days`：产物生命周期策略；下一次清理操作读取新值。当前 CLI 读取 settings，还没有系统设置源。
- `mcp_max_tools_per_server`：发现/接入规模策略；新发现操作立即使用，已经创建的 Run 工具快照保持不变。
- `im_progress_interval_sec`：面向用户的状态刷新频率；后续 tick/新回复采用新值。`im_progress_updates_per_second` 留在环境，作为服务资源预算上限。
- `task_default_deadline_hours` / `task_max_attempts` / `batch_max_concurrency` / `misfire_grace_sec`：新任务或新调度的默认策略；已持久化的 deadline、attempt 与计划字段不偷偷重写。
- `delivery_max_attempts` / `delivery_backoff_base_sec`：新投递操作的重试策略；修改不重置已经发生的尝试次数。具体投递生命周期是否冻结，必须在落地时明确。
- `batch_platform_limit`：平台容量上限，服务启动配置；业务 batch 并发不能超过它。
- `context_settings_cache_ttl_sec`：当前缓存技术参数；10 秒 TTL 不满足立即生效，不能保留成新方案的可见延迟。

## 系统设置应管理的业务默认

### 上下文压缩：15 个叶子字段

以下字段默认值已集中在 `packages/agent-core/.../context/settings.py`，Runtime 读取缝已存在，但平台设置源当前返回空字典，尚未读取设置表。

| 设置路径 | 当前默认 |
|---|---|
| `compaction.snip.enabled` | `True` |
| `compaction.snip.max_groups` | `50` |
| `compaction.snip.keep_head_groups` | `3` |
| `compaction.snip.keep_tail_groups` | `20` |
| `compaction.tool_result.persist_threshold_bytes` | `8 * 1024` |
| `compaction.tool_result.round_budget_bytes` | `200000` |
| `compaction.tool_result.preview_head_bytes` | `2000` |
| `compaction.tool_result.preview_tail_bytes` | `2000` |
| `compaction.micro.enabled` | `False` |
| `compaction.micro.keep_recent_tool_groups` | `3` |
| `compaction.summary.enabled` | `False` |
| `compaction.summary.threshold_bytes` | `50000` |
| `compaction.summary.model_ref` | `None` |
| `compaction.memory.budget_ratio` | `0.2` |
| `compaction.history_budget_messages` | `40` |

联动校验必须保留：保留头尾组数与触发阈值的关系、预览头尾与预算的关系、摘要启用需要有效 `model_ref`、memory 比例边界及未知键拒绝。系统设置选择现有模型定义，不能在此复制 endpoint/key 管理。

### 其他业务策略声明

下表列出目前散落在模块中的业务默认；同值不同语义不能合并，同语义的引用/前后端副本必须收敛到同一键。所有范围上限最终由服务端校验，前端只负责展示与预检。

| 现有声明 | 默认表达式 | 来源 |
|---|---|---|
| `MAX_ARCHIVE_FILES` | `2000` | [archive_tools.py:21](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/archive_tools.py#L21) |
| `ARCHIVE_BYTES_LIMIT` | `MAX_OUTPUT_BYTES` | [archive_tools.py:23](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/archive_tools.py#L23) |
| `MAX_INLINE_IMAGES` | `5` | [inbound.py:34](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/inbound.py#L34) |
| `MAX_OUTPUT_BYTES` | `50 * 1024 * 1024` | [output_service.py:17](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/output_service.py#L17) |
| `TOOL_RESULT_ARTIFACT_BYTES` | `8 * 1024` | [tool_results.py:23](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py#L23) |
| `PREVIEW_HEAD_BYTES` | `2000` | [tool_results.py:27](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py#L27) |
| `PREVIEW_TAIL_BYTES` | `2000` | [tool_results.py:28](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py#L28) |
| `MAX_READ_BYTES` | `50 * 1024 * 1024` | [tools.py:107](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L107) |
| `MAX_TEXT_CHARS` | `20000` | [tools.py:109](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L109) |
| `DEFAULT_SEARCH_HITS` | `5` | [tools.py:126](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L126) |
| `MAX_SEARCH_HITS` | `20` | [tools.py:127](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L127) |
| `SEARCH_CONTEXT_CHARS` | `80` | [tools.py:129](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L129) |
| `DEFAULT_LIST_LIMIT` | `20` | [tools.py:145](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L145) |
| `MAX_LIST_LIMIT` | `50` | [tools.py:146](../apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py#L146) |
| `MAX_HISTORY_TOOL_ROUNDS` | `6` | [context_builder.py:35](../apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py#L35) |
| `MAX_INJECTED_MEMORIES` | `10` | [context_builder.py:39](../apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py#L39) |
| `MAX_INJECTED_BYTES` | `2048` | [context_builder.py:40](../apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py#L40) |
| `MAX_INLINE_RESULT_BYTES` | `256 * 1024` | [executor.py:92](../apps/agent-runtime/src/muad_agent_runtime/application/executor.py#L92) |
| `RECALL_DEFAULT_LIMIT` | `10` | [memory_tools.py:61](../apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py#L61) |
| `RECALL_MAX_LIMIT` | `20` | [memory_tools.py:63](../apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py#L63) |
| `MAX_RECALL_BYTES` | `4096` | [memory_tools.py:66](../apps/agent-runtime/src/muad_agent_runtime/application/memory_tools.py#L66) |
| `MAX_RESOURCE_BYTES` | `256 * 1024` | [skill_tools.py:38](../apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py#L38) |
| `MAX_CASCADE_DEPTH` | `8` | [task_cancel.py:23](../apps/agent-worker/src/muad_agent_worker/application/task_cancel.py#L23) |
| `INITIAL_PRIORITY` | `100` | [task_service.py:28](../apps/agent-worker/src/muad_agent_worker/application/task_service.py#L28) |
| `MAX_RESULT_CHARS` | `1500` | [messages.py:22](../apps/agent-worker/src/muad_agent_worker/delivery/messages.py#L22) |
| `MAX_ERROR_CHARS` | `300` | [messages.py:23](../apps/agent-worker/src/muad_agent_worker/delivery/messages.py#L23) |
| `DEFAULT_WAIT_SEC` | `60` | [execution_outcomes.py:12](../apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py#L12) |
| `EXECUTION_TIMEOUT_SEC` | `300.0` | [executor.py:28](../apps/agent-worker/src/muad_agent_worker/worker/executor.py#L28) |
| `RETRY_BACKOFF_BASE_SEC` | `5` | [service.py:35](../apps/agent-worker/src/muad_agent_worker/worker/service.py#L35) |
| `SESSION_MAX_AGE_SEC` | `12 * 60 * 60` | [security.py:13](../apps/console-platform/backend/src/muad_console_platform/api/security.py#L13) |
| `DEFAULT_FETCH_TTL_SEC` | `300.0` | [artifact_fetch_tokens.py:27](../apps/console-platform/backend/src/muad_console_platform/application/artifact_fetch_tokens.py#L27) |
| `TTL_ENV` | `'ARTIFACT_FETCH_TTL_SEC'` | [artifact_fetch_tokens.py:32](../apps/console-platform/backend/src/muad_console_platform/application/artifact_fetch_tokens.py#L32) |
| `MIN_PASSWORD_LENGTH` | `12` | [auth_service.py:22](../apps/console-platform/backend/src/muad_console_platform/application/auth_service.py#L22) |
| `MAX_FAILED_ATTEMPTS` | `5` | [auth_service.py:23](../apps/console-platform/backend/src/muad_console_platform/application/auth_service.py#L23) |
| `LOCK_DURATION` | `timedelta(minutes=15)` | [auth_service.py:24](../apps/console-platform/backend/src/muad_console_platform/application/auth_service.py#L24) |
| `SESSION_TTL` | `timedelta(hours=12)` | [auth_service.py:25](../apps/console-platform/backend/src/muad_console_platform/application/auth_service.py#L25) |
| `SLIDE_THRESHOLD` | `SESSION_TTL / 2` | [auth_service.py:26](../apps/console-platform/backend/src/muad_console_platform/application/auth_service.py#L26) |
| `BIND_CODE_TTL` | `timedelta(minutes=10)` | [channel_service.py:41](../apps/console-platform/backend/src/muad_console_platform/application/channel_service.py#L41) |
| `ZIP_BYTES_LIMIT` | `50 * 1024 * 1024` | [skill_validator.py:23](../apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py#L23) |
| `UNPACKED_BYTES_LIMIT` | `200 * 1024 * 1024` | [skill_validator.py:24](../apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py#L24) |
| `ENTRY_LIMIT` | `2000` | [skill_validator.py:25](../apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py#L25) |
| `MAX_ATTACHMENT_BYTES` | `50 * MIB` | [attachment_gate.py:29](../apps/im-gateway/src/muad_im_gateway/application/attachment_gate.py#L29) |
| `MAX_ATTACHMENTS_PER_MESSAGE` | `5` | [attachment_gate.py:31](../apps/im-gateway/src/muad_im_gateway/application/attachment_gate.py#L31) |
| `PROGRESS_INTERVAL_SEC` | `5.0` | [progress.py:104](../apps/im-gateway/src/muad_im_gateway/application/progress.py#L104) |
| `DELTA_FLUSH_INTERVAL_SEC` | `0.5` | [stream_renderer.py:24](../apps/im-gateway/src/muad_im_gateway/application/stream_renderer.py#L24) |
| `DEFAULT_STREAM_FLUSH_INTERVAL_SEC` | `0.5` | [adapter.py:76](../apps/im-gateway/src/muad_im_gateway/channels/wecom/adapter.py#L76) |

此外，`AgentPolicy.max_turns=20`、`max_tool_calls=30`、`deadline_ms=120000`、`max_model_retries=3` 作为平台默认可纳入执行策略；已有 `runtime_config` 覆盖继续优先。`BudgetPolicy.max_messages=40` 应复用 `compaction.history_budget_messages`，不新增另一个“历史条数”键。`memory_write` 是现有 Agent 级配置，未显式配置时默认 `True`（`application/memory_tools.py:104`）；需要平台默认时同样遵循“资源覆盖 > 平台设置 > schema 默认”。

`SESSION_TTL` 与 `SESSION_MAX_AGE_SEC` 必须共用一个会话策略；`SLIDE_THRESHOLD` 是派生值，不能作为独立输入。变更会话时长只影响之后签发/续期的会话，不应把所有已签发会话立即改成新到期时间。

### 已有资源页面继续管理的配置

| 资源 | 当前来源 | 系统设置边界 |
|---|---|---|
| Agent | `runtime_config_json`；执行次数、工具预算、memory 写入、压缩覆盖 | 只管理未覆盖的默认；显式 Agent 覆盖优先 |
| 模型 | `model_definition.params_json`、endpoint、auth_secret | 继续模型页面管理；平台默认只能引用既有模型 |
| MCP | `connect_timeout_ms`、`tool_cache_ttl_sec`；现有 DTO 范围 100–60000 ms、1–86400 sec | 继续 MCP 页面管理；MCP 连接超时和平台内部 HTTP 超时是不同概念 |
| 项目平台 | `adapter_config_json.timeout_ms`（默认 3000 ms）、认证方式与出网 allowlist | 继续项目平台页面管理；全局出网默认不允许覆盖资源更严格的安全约束 |
| Task / Schedule | 显式 deadline、priority、max_concurrency、时区与 input_template | 系统设置提供创建默认；不改写既有行 |
| 用户 / 渠道凭据 | 已有 auth_secret / Secret 字段与用户偏好 | 不复制到普通设置 JSON 或公共前端响应 |

## 环境设置：新增收口的重点

除了 SharedSettings 表中的 34 项，以下模块常量/入口应统一进入启动配置，结束散落的硬编码。完整逐声明值在 CSV。

| 环境配置组 | 当前声明 / 默认 | 消费服务 |
|---|---|---|
| 内部 HTTP | `RESOLVE_TIMEOUT_SEC=5`、`DEFAULT_TIMEOUT_SEC=5`、Runtime→Gateway `DELIVERY_TIMEOUT_SEC=30`、Gateway→Runtime `REQUEST_TIMEOUT_SEC=10` / `STREAM_TIMEOUT_SEC=300`、Gateway→Console 5 | 各 HTTP client 的服务；按用途保留不同键，不能仅因都叫 timeout 合并 |
| 模型传输 | OpenAI Provider `DEFAULT_TIMEOUT_SEC=60` | Runtime；传输读超时与 Run 业务 deadline 分开 |
| MCP 运行传输 | `MCP_CALL_TIMEOUT_SEC=10`；Console MCP client 5000 ms 默认 | Runtime/Console；先核对是否应继承资源的连接参数 |
| 取消/事件循环 | Runtime cancel poll 0.25 sec、队列等待 0.05 sec、replay poll 0.5 sec、Run/Task cancel hint TTL 1800 sec | Runtime/Worker |
| Gateway 去重 | 入站 TTL 600 sec；出站 TTL 604800 sec；in-flight TTL 30 sec、等待 2 sec、轮询 0.1 sec | Gateway；内部工作窗口要满足超时关系 |
| Gateway 资源 | 并发 handler 8；bot 快照轮询 30 sec / 最多 100 页；技能最多 20 页 / 页间 0.05 sec | Gateway；配置刷新机制另需替换轮询才能保证业务立即生效 |
| WeCom 连接 | 重连 backoff 1→30 sec、liveness 1 sec、media ref TTL 300 sec / 容量 256 | Gateway |
| WeCom I/O | 下载 timeout 30 sec、状态发送 timeout 5 sec | Gateway；技术 I/O 参数从环境读取 |
| 有界内存与工作批次 | Fetch token 容量 4096、附件文本 cache 4、cleanup batch 500、Run timeline 200、MCP 最多 50 页 | 对应 Console/Runtime 消费服务 |
| 子进程 | stdout/stderr 捕获 64 KiB、终止 grace 2 sec | Runtime/Worker 中执行 Skill 的入口 |
| 清理调度 | orphan grace 3600 sec | Console 清理入口；保留 CLI 本次操作覆盖 |
| Console 装配 | `MUAD_EXTRA_PLATFORM_ADAPTERS`、`STATIC_DIR=/app/static` | Console，插件装配与静态资源路径重启生效 |
| 运行身份 | `POD_NAME` | Runtime，由部署注入；不能从系统设置切换实例身份 |
| 本地前端代理 | `MUAD_API_TARGET`（缺省 `http://127.0.0.1:8000`）、Vite dev 5173 / preview 4173 | 重启 Vite；生产代理属于部署配置 |
| 监听端口 | `scripts/dev.sh` 的 Console/Runtime/Worker/Gateway 8000/8001/8002/8003 | 启动脚本与健康检查、URL 必须共同由环境派生 |

`.env.example` 当前只有 17 个显式赋值键；SharedSettings 的环境类字段中，有 21 项未在该示例显式声明。实际代码可从进程环境读取它们，但示例没有形成完整的运维契约。需补 DEFAULT_TENANT_ID、INTERNAL_SERVICE_TOKEN、CHANNEL_PROBE_URL、WECOM_WS_URL、WECOM_WS_CA_FILE、MIGRATIONS_DIR、CONTEXT_SETTINGS_CACHE_TTL_SEC 与租约/心跳/轮询/批次/容量项。

集群环境不要修改文件期待 Pod 自动更新：Secret/ConfigMap 注入的环境项通过 rollout 重启生效。CPU/memory request/limit、replicas、probe 配置属于 Deployment 的容量与运行配置，应由同一部署参数源渲染；它们不能仅通过应用读取 `.env` 就改变。镜像、构建目标、TypeScript 编译设置不是业务系统设置。

前端 `.env` 中的 `VITE_*` 往往在构建时固化；只重启后端不会更新静态包。因此业务设置必须从运行时 API 获取，技术参数使用同源相对 URL / 反向代理；不把密钥注入前端构建变量。

## 保留在代码的常量

| 类别 | 例子 | 理由 |
|---|---|---|
| 协议契约 | HTTP 状态 401/429/500、CSRF Header/Cookie 名、SSE 事件类型、MCP namespace、snapshot schema 版本 | 改值会破坏协议/数据解释，不属于环境调参 |
| 状态与枚举 | RunStatus/TaskStatus、角色、审计 outcome、工具名称 | 必须按 schema 和状态机发布 |
| 第三方硬上限 | WeCom `UPLOAD_CHUNK_BYTES=512 KiB`、`MAX_UPLOAD_CHUNKS=100` | 现有适配器注明是渠道协议上限；业务限额可更小，不能任意扩大协议能力 |
| 存储契约 | `MAX_KEY_CHARS=64`、`MAX_VALUE_CHARS=512`、绑定码长度、字段长度、索引与 naming convention | 改动需对应 schema/安全设计，不开放普通设置 |
| 算法规则 | 指数退避公式、AES 算法、protected SYSTEM 前缀、tool 分组完整性、快照 hash | 参数可调，正确性规则继续在代码中 |
| UI 原语 | 菜单/路由、icon 映射、页面分页选项、终态数组、浏览器轮询有限次数 | 日常展示实现，不能把所有 const 都做成系统设置；用户偏好可在用户设置单独管理 |
| 文本/标识 | metric catalog、错误码、i18n key、工具说明、文件 magic | 不是业务参数；本次不增加“任意改系统提示/关闭脱敏”等入口 |

API 默认 page size=20、max=100 与某些 UI 默认 10 属当前接口/展示契约；本次不自动变成全局业务配置。若产品确实需要统一分页，应另建可版本化约定，不让管理员任意改变 API 最大长度。

## 必须收敛的重复/冲突

1. **历史预算**：CompactionSettings.history_budget_messages=40、BudgetPolicy.max_messages=40 是同一预算的不同消费者，直接复用冻结值。
2. **工具结果默认**：`TOOL_RESULT_ARTIFACT_BYTES/PREVIEW_*` 是 schema 默认的调用别名；8 KiB 与 2000/2000 只保留 schema 单一来源。
3. **会话时长**：auth_service 12h 与 security Cookie Max-Age 12h 必须同源；滑动阈值派生，不独立设置。
4. **上传限制**：Skill ZIP 50 MiB / 解压 200 MiB / 2000 项是导入策略；前端 SKILL_ZIP_LIMIT_BYTES 复用服务端公共配置。附件输入 50 MiB/5 项、输出 50 MiB、归档 2000 项是不同操作的限额，不仅凭同值合并。
5. **模型执行**：AgentPolicy deadline=120000 ms、ModelGateway deadline=60000 ms、executor model timeout=120 sec、Provider I/O timeout=60 sec、重试 3 次（多处）与 backoff 0.1/0.05 sec 不是完整一致的一套链路。需定义总体 deadline → 单次请求预算 → 重试预算的层级，避免总预算尚未耗尽而内层提前退出。
6. **策略默认快照**：`DEFAULT_POLICY=snapshot_policy()` 在模块导入时创建；系统设置落地后不能把它当动态默认源，必须在 Run 创建事务中按 settings revision 生成冻结策略。
7. **产物路径**：SharedSettings 默认 `./.data/artifacts` / `./.data/skill-cache` 与 bootstrap 中 `/mnt/muad-artifacts` / `/var/cache/muad/skills` 不一致；去掉裸 getenv 的第二套默认，统一启动 settings。
8. **默认租户**：CLI DEFAULT_TENANT='default' 与 SharedSettings.default_tenant_id 重复，CLI 应读取同一启动设置。
9. **生效方式混杂**：部分请求临时构建 SharedSettings、部分服务/engine 用启动对象或 lru_cache，`.env` 修改当前并非严格“一律重启后才生效”；需要全部环境配置启动加载，业务配置通过独立持久化 source 读取，不能再用重建 SharedSettings 模拟热更新。
10. **间接资源限制**：session/cookie 时长、ZIP 前后端、model 总 deadline/传输 timeout、batch 默认并发/平台容量、WeCom 可配置业务文件限制/固定渠道上传限额都需联动校验。

## “保存后立即生效”的落地契约

- 平台默认按 tenant 隔离持久化，修改仅限授权管理员；提交时做类型、范围、引用与联动校验，记录操作者、版本和审计。敏感值走现有 Secret 机制，公共接口只返回前端所需限额。
- Console 设置事务提交成功后返回新 revision；Runtime/Worker/Gateway 不能凭进程内旧对象、10 秒 TTL 或 30 秒 Bot 快照轮询继续执行新操作。
- 建议把业务设置集中由 Console 持有，通过内部 HTTP 取得权威 revision/快照。每个新 Run/Task/业务操作在边界拿一次最新快照，执行中不每轮查库；这样四个服务无需重启、无跨 Pod 广播是否到达的歧义。
- 不能只用 Redis Pub/Sub + TTL 宣称立即生效：离线/重连/漏消息节点仍可能用旧值。如果需要缓存或推送，在每次新操作边界校验权威 revision，推送只作提速；设置不可读时明确失败，不悄悄使用过时默认。
- Run/Task 保存 settings revision 和实际冻结策略；更改默认不覆盖已有 Agent 显式配置。页面要显示“平台默认 / 资源覆盖 / 本次实际值”，避免管理员改了默认却误以为覆盖也被改了。
- 保存返回成功的操作之后开始的新请求必须见到新 revision；已经在取快照的并发请求按明确事务时序确定版本，不能把分布式“同时”当作含糊的立即生效。
- 对 IM 状态节拍这类确需正在执行中调整的展示设置，在 tick/发送边界单独读版本并替换节拍；Run 内容预算/授权/模型/产物执行策略保持 execution snapshot 一致性。

建议系统设置页面分组：上下文压缩、Agent 执行默认、任务与投递、记忆与附件、产物生命周期、认证安全、语言/时区与 IM 展示。已有 Agent/模型/MCP/平台管理页继续保留。

## 后续实现顺序

1. 确认逐项分类与立即生效的执行中边界，形成设置 schema/校验/权限/API 合同。
2. 建业务设置表与 revision，接四服务读取边界，先验证多实例保存后新操作即时看到新版本与执行中快照不变。
3. 建系统设置菜单、页面与公共配置读取；同步修改当前“禁止系统设置入口”的 shell 测试和事实性文档。
4. 去掉业务项的环境入口与散落默认，环境项统一进启动 settings + `.env.example` + 部署参数，避免兼容别名和双写。
5. 分层验收：业务保存后即时生效、资源覆盖优先、跨租户隔离、权限与审计；环境项重启生效、非法组合拒绝启动；第三方协议上限不会被业务配置绕过。

本轮仅盘点，未修改业务运行代码、数据库结构、`.env`、系统设置 UI 或部署配置。
