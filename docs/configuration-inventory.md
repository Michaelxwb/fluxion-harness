# 全仓配置盘点与生效边界

审计日期：2026-10-04；2026-10-05 随平台设置需求（TASK-001..TASK-015）迁移结果校准；2026-10-06 随概览指标化（`/overview/metrics` 聚合常量与 SQL）校准；同日随后续全站列表页量统一 15（前端分页展示契约）校准。基于当前 `main`。
本文件是**迁移后的现状盘点与分类结论**：Console 系统设置页已交付，平台设置文档按租户持久化（`control.platform_setting`，append-only 版本行），41 个叶子已接入该唯一源，散落的重复默认已按收敛清单收敛。逐项清单见 [configuration-inventory.csv](configuration-inventory.csv)。

## 分类结论

| 类别 | 存放位置 | 生效方式 |
|---|---|---|
| business | Console 系统设置（`control.platform_setting` 版本表）→ 按租户持久化 | 保存成功后，后续请求 / 新 Run / 新任务立即读取已提交版本，不等待缓存 TTL、不重启 |
| business-resource | 已有 Agent / 模型 / MCP / 项目平台页面中的资源配置 | 资源级覆盖优先；系统设置只提供平台默认，不复制现有资源表 |
| environment | `.env`；集群同义项通过 Secret / ConfigMap / Deployment 注入 | 校验启动配置，重启消费此配置的服务；不放进系统设置 |
| code | 协议、状态枚举、schema、字段长度、算法不变量、UI 原语，以及各服务持有的实现常量（含限额、节拍、超时） | 随代码发布，运行期不可改；不经环境变量或系统设置任意改写 |

“立即生效”指**保存成功后开始的新操作立即使用新配置**。执行中的 Run / Task 继续用其 execution snapshot；这是既有快照机制的明确行为，不把已执行的任务中途换策略（`RULE-01`）。

## 盘点范围与完整明细

遍历 `apps/`、`packages/` 下全部 **309** 个 Python 文件与 **113** 个 TS/TSX 文件（不含 `node_modules`）：Python 大写命名常量（含类内枚举）、配置/策略类默认值、带 timeout/ttl/limit/budget 等语义的函数默认参数、直接环境读取和部分内联 I/O 参数，以及前端大写命名常量。另人工核对 `.env.example`、`scripts/dev.sh`、Vite 和 k8s 部署配置。

完整声明明细见 [configuration-inventory.csv](configuration-inventory.csv)。CSV 每行包含源码路径、行号、名称、当前默认表达式、声明类型、目标分类和理由；重复声明不等于独立设置项。只读取 `.env.example` 的键名，不读取或输出真实 `.env` 的秘密值。

扫描不把每个 SQL `.limit(1)`、计数器初始零、CSS 尺寸或循环算术都当作配置；调用方传入且没有默认值的参数不属于常量默认。本清单是当前源码快照，不能宣称通过语法扫描穷尽所有隐含业务策略。

**当前声明点合计 1013**：`code` 855、`environment` 114、`business` 32、`business-resource` 12。其中大量 `code` 行是状态码、事件名、协议文本与各服务持有的实现常量。分类由 `tests/test_configuration_inventory.py` 的机检与分类断言兜底（路径/行号/符号/默认值直接与源码 AST 对齐；分类主张逐条钉住）。

## SharedSettings：当前 33 项全部是环境设置

迁移已把 12 个业务键从启动 settings 摘除（`default_locale`、`default_timezone`、`artifact_retention_days`、`mcp_max_tools_per_server`、`im_progress_interval_sec`、`task_default_deadline_hours`、`task_max_attempts`、`batch_max_concurrency`、`misfire_grace_sec`、`delivery_max_attempts`、`delivery_backoff_base_sec`、`context_settings_cache_ttl_sec`）；它们的值现由平台设置提供（见下节）。`.env.example` 已补全为 **33 个键**，与 `SharedSettings` 的字段集一一对应，形成完整运维契约。

余下 33 项**全部保留为环境项**（`packages/common/src/muad_common/settings.py`），分三类：

| 类别 | 字段 |
|---|---|
| 运行环境与路径 | `env` / `log_dir` / `log_level` / `api_messages_file` / `artifact_root` / `skill_cache_root` / `migrations_dir` |
| 连接与身份 | `default_tenant_id` / `database_url` / `redis_url` / `internal_service_token` / `channel_probe_url` / `wecom_ws_url` / `wecom_ws_ca_file` |
| 服务 URL 与租约/心跳/轮询/批次 | `console_platform_url` / `agent_runtime_url` / `agent_worker_url` / `im_gateway_url` / `run_lease_sec` / `run_heartbeat_sec` / `run_reaper_interval_sec` / `run_event_heartbeat_sec` / `task_lease_sec` / `task_heartbeat_sec` / `task_cancel_check_sec` / `worker_poll_interval_sec` / `batch_platform_limit` / `scheduler_poll_interval_sec` / `scheduler_batch_size` / `task_deadline_sweep_interval_sec` / `delivery_poll_interval_sec` / `delivery_batch_size` / `im_progress_updates_per_second` |

`im_progress_updates_per_second` 是每机器人状态刷新的**服务资源预算上限**，留在环境；面向用户的刷新节拍是平台设置 `im.progress_interval_sec`（见下节），两者语义不同。

## 平台设置已接管的业务默认（41 个叶子）

设置文档 schema、默认值与跨字段联动校验落在 `packages/contracts/src/muad_contracts/platform_settings.py`（9 个分组的唯一权威声明，Console 与三个执行服务共用）。分组与叶子：

- `compaction`（15）：`snip.{enabled,max_groups,keep_head_groups,keep_tail_groups}`、`tool_result.{persist_threshold_bytes,round_budget_bytes,preview_head_bytes,preview_tail_bytes}`、`micro.{enabled,keep_recent_tool_groups}`、`summary.{enabled,threshold_bytes,model_ref}`、`memory.budget_ratio`、`history_budget_messages`
- `agent`（4）：`max_turns` / `max_tool_calls` / `deadline_ms` / `max_model_retries`
- `task`（6）：`default_deadline_hours` / `max_attempts` / `batch_max_concurrency` / `misfire_grace_sec` / `delivery_max_attempts` / `delivery_backoff_base_sec`
- `memory`（5）：`write_enabled` / `max_injected_memories` / `max_injected_bytes` / `max_recall_bytes` / `recall_default_limit`
- `artifact`（3）：`retention_days` / `max_archive_files` / `cleanup_batch_size`
- `auth`（4）：`min_password_length` / `max_failed_attempts` / `lock_duration_minutes` / `session_ttl_hours`
- `locale`（2）：`default_locale` / `default_timezone`
- `im`（1）：`progress_interval_sec`
- `mcp`（1）：`max_tools_per_server`

联动校验必须保留（服务端单一来源，前端只做预检）：保留头尾组数与触发阈值的关系、预览头尾与预算的关系、摘要启用需要有效 `model_ref`（引用既有 `model_definition`，不引入平台默认模型）、memory 比例边界与召回条数上界、`task.batch_max_concurrency ≤ batch_platform_limit`（环境项）、未知键拒绝（fail-closed）。

认证策略在登录 / 改密 / 续期边界读当前值校验；`session_ttl_hours` 与 Cookie `Max-Age` **同源**（登录时同一次读取），滑动阈值是**派生值**（会话自身窗口的一半），不是独立设置项。改会话时长只影响之后签发 / 续期的会话，不追改已签发会话，存量密码不回改。

41 个叶子在 CSV 里的默认值来源标为 `business`：设置文档 schema 自身（`contracts/platform_settings.py`）、`agent` 分组的 `AgentPolicy` 字段与 `budget.py` 的 `DEFAULT_MAX_*`、`BudgetPolicy.max_messages`（与 `compaction.history_budget_messages` 收敛为同一冻结值）、以及 `model_gateway` 消费这些默认的参数入口。

### Agent / 模型 / 压缩覆盖的优先级

Agent 级 `runtime_config.budget.compaction` 仍按 `merge_compaction_payload` 覆盖平台默认；`AgentPolicy.from_runtime_config` 的 Agent 覆盖优先于平台设置冻结值。资源覆盖 > 平台设置 > schema 默认，三级不变。

## 留在各服务代码的实现常量（本期明确不迁入平台设置）

下列常量是业务限额或节拍，但按 design §2.4（非范围③ / 技术债③）**留在各自服务**，运行期不可改；CSV 标为 `code`：

- 附件与工具限额：`MAX_OUTPUT_BYTES` / `MAX_INLINE_IMAGES` / `MAX_READ_BYTES` / `MAX_TEXT_CHARS` / `MAX_INLINE_RESULT_BYTES` / `MAX_RESOURCE_BYTES`（Runtime）、`MAX_ATTACHMENT_BYTES` / `MAX_ATTACHMENTS_PER_MESSAGE`（Gateway）
- Skill 导入限额：`ZIP_BYTES_LIMIT` / `UNPACKED_BYTES_LIMIT` / `ENTRY_LIMIT`（Console `skill_validator`，服务端单一来源，经已认证只读端点 `API-05` 暴露；前端 `SKILL_ZIP_LIMIT_BYTES` 副本尚未接线，见收敛清单第 4 条）
- 任务 / 投递 / 执行默认：`INITIAL_PRIORITY` / `MAX_CASCADE_DEPTH` / `MAX_RESULT_CHARS` / `MAX_ERROR_CHARS` / `DEFAULT_WAIT_SEC` / `EXECUTION_TIMEOUT_SEC` / `RETRY_BACKOFF_BASE_SEC`
- IM 展示节拍（非 `im.progress_interval_sec`）：`DELTA_FLUSH_INTERVAL_SEC` / `DEFAULT_STREAM_FLUSH_INTERVAL_SEC`
- TTL：`BIND_CODE_TTL`、取件 token 的默认 TTL（`DEFAULT_FETCH_TTL_SEC`，可经环境变量 `ARTIFACT_FETCH_TTL_SEC` 覆盖）
- 上下文历史回放守卫：`MAX_HISTORY_TOOL_ROUNDS`

这些值随代码发布；如需运营可调，须另立需求把它们纳入平台设置 schema（`RULE-10`：同一业务语义只有一处权威声明）。

### 已有资源页面继续管理的配置

| 资源 | 当前来源 | 系统设置边界 |
|---|---|---|
| Agent | `runtime_config_json`；执行次数、工具预算、memory 写入、压缩覆盖 | 只管理未覆盖的默认；显式 Agent 覆盖优先 |
| 模型 | `model_definition.params_json`、endpoint、auth_secret | 继续模型页面管理；平台默认只能引用既有模型 |
| MCP | `connect_timeout_ms`、`tool_cache_ttl_sec`；DTO 范围 100–60000 ms、1–86400 sec | 继续 MCP 页面管理；平台设置只给接入规模默认 `mcp.max_tools_per_server`，连接参数与它不同概念 |
| 项目平台 | `adapter_config_json.timeout_ms`（默认 3000 ms）、认证方式与出网 allowlist | 继续项目平台页面管理；全局出网默认不允许覆盖资源更严格的安全约束 |
| Task / Schedule | 显式 deadline、priority、max_concurrency、时区与 input_template | 系统设置提供创建默认；不改写既有行 |
| 用户 / 渠道凭据 | 已有 auth_secret / Secret 字段与用户偏好 | 不复制到普通设置 JSON 或公共前端响应 |

## 环境设置：新增收口的重点

除 `SharedSettings` 的 33 项外，以下模块常量 / 入口统一进入启动配置，结束散落的硬编码。完整逐声明值在 CSV。

| 环境配置组 | 当前声明 / 默认 | 消费服务 |
|---|---|---|
| 内部 HTTP | `RESOLVE_TIMEOUT_SEC=5`、`DEFAULT_TIMEOUT_SEC=5`、Runtime→Gateway `DELIVERY_TIMEOUT_SEC=30`、Gateway→Runtime `REQUEST_TIMEOUT_SEC=10` / `STREAM_TIMEOUT_SEC=300`、Gateway→Console 5 | 各 HTTP client 的服务；按用途保留不同键，不能仅因都叫 timeout 合并 |
| 模型传输 | OpenAI Provider `DEFAULT_TIMEOUT_SEC=60` | Runtime；传输读超时与 Run 业务 deadline 分开 |
| MCP 运行传输 | `MCP_CALL_TIMEOUT_SEC=10`；Console MCP client 5000 ms 默认 | Runtime/Console；先核对是否应继承资源的连接参数 |
| 取消 / 事件循环 | Runtime cancel poll 0.25 sec、队列等待 0.05 sec、replay poll 0.5 sec、Run / Task cancel hint TTL 1800 sec | Runtime/Worker |
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

`deploy/k8s/base/configmap.yaml` 是集群侧同义项的唯一注入点（`ARTIFACT_ROOT` / `SKILL_CACHE_ROOT` 等）。**不再有第二套默认**：bootstrap 不硬编码 `/mnt/muad-artifacts` 一类路径，全部经启动 settings（`ADR-08`）。

集群环境不要修改文件期待 Pod 自动更新：Secret / ConfigMap 注入的环境项通过 rollout 重启生效。CPU / memory request / limit、replicas、probe 配置属于 Deployment 的容量与运行配置，应由同一部署参数源渲染；它们不能仅通过应用读取 `.env` 就改变。镜像、构建目标、TypeScript 编译设置不是业务系统设置。

前端 `.env` 中的 `VITE_*` 往往在构建时固化；只重启后端不会更新静态包。因此业务设置必须从运行时 API 获取，技术参数使用同源相对 URL / 反向代理；不把密钥注入前端构建变量。

## 保留在代码的常量

| 类别 | 例子 | 理由 |
|---|---|---|
| 协议契约 | HTTP 状态 401/429/500、CSRF Header/Cookie 名、SSE 事件类型、MCP namespace、snapshot schema 版本 | 改值会破坏协议 / 数据解释，不属于环境调参 |
| 状态与枚举 | RunStatus / TaskStatus、角色、审计 outcome、工具名称 | 必须按 schema 和状态机发布 |
| 第三方硬上限 | WeCom `UPLOAD_CHUNK_BYTES=512 KiB`、`MAX_UPLOAD_CHUNKS=100` | 现有适配器注明是渠道协议上限；业务限额可更小，不能任意扩大协议能力 |
| 存储契约 | `MAX_KEY_CHARS=64`、`MAX_VALUE_CHARS=512`、绑定码长度、字段长度、索引与 naming convention | 改动需对应 schema / 安全设计，不开放普通设置 |
| 算法规则 / 不变量 | 指数退避公式、AES 算法、protected SYSTEM 前缀、tool 分组完整性、快照 hash、`MIN_PASSWORD_LENGTH_FLOOR`、`RECALL_MAX_LIMIT` | 参数可调，正确性规则继续在代码中；不变量不得被任何租户压低 |
| UI 原语 | 菜单 / 路由、icon 映射、页面分页选项、终态数组、浏览器轮询有限次数 | 日常展示实现；用户偏好可在用户设置单独管理 |
| 文本 / 标识 | metric catalog、错误码、i18n key、工具说明、文件 magic | 不是业务参数；本次不增加“任意改系统提示 / 关闭脱敏”等入口 |

API 默认 page size=20、max=100 与 UI 页量（2026-10-06 起全站统一 15）属当前接口 / 展示契约；本次不自动变成全局业务配置。若产品确实需要统一分页，应另建可版本化约定，不让管理员任意改变 API 最大长度。

## 必须收敛的重复 / 冲突（逐条现状）

| # | 项 | 现状 | 依据 |
|---|---|---|---|
| 1 | 历史预算（`CompactionSettings.history_budget_messages` 与 `BudgetPolicy.max_messages`） | **已收敛** | `context_builder.BudgetPolicy.max_messages` 派生自 `default_compaction_settings().history_budget_messages`，同源 |
| 2 | 工具结果默认（`TOOL_RESULT_ARTIFACT_BYTES` / `PREVIEW_*` 与 schema 默认） | **已收敛** | `ADR-05`：三个模块常量删除，`ToolResultSettings` 为唯一来源；CSV 中不再出现这些常量 |
| 3 | 会话时长（auth_service 与 Cookie `Max-Age`） | **已收敛** | `auth.session_ttl_hours` 与 Cookie `Max-Age` 登录时同一次读取；`SESSION_TTL` / `SLIDE_THRESHOLD` 常量删除，阈值派生（`ADR-12`） |
| 4 | 上传限制（Skill ZIP / 解压 / 项数；前端副本） | **部分收敛** | 服务端限额为单一来源并经 `API-05` 只读端点暴露；**前端 `SKILL_ZIP_LIMIT_BYTES` 副本仍在、尚未接线**（`技术债③`）。附件输入 / 输出 / 归档是不同操作的限额，本就不凭同值合并 |
| 5 | 模型执行（deadline / 重试 / 退避） | **已收敛** | `ADR-07`：`agent.deadline_ms` 为总预算，单次请求预算与重试预算由它派生；`MAX_MODEL_RETRIES` / `MAX_RETRIES_DEFAULT` / `AgentPolicy.max_model_retries` 三处合一；退避基数为算法参数（`code`） |
| 6 | 策略默认快照（`DEFAULT_POLICY` 导入期求值） | **已收敛** | `DEFAULT_POLICY` 现在只是“无平台设置时的 schema 默认快照”，不再是运行期动态默认源；新 Run 的 `policy_json` 一律在 Run 创建事务内取平台快照 + Agent 覆盖后冻结 |
| 7 | 产物路径（`SharedSettings` 默认与 bootstrap 硬编码） | **已收敛** | `ADR-08`：删除裸 `getenv` 第二套默认，产物 / 技能缓存路径全部经启动 settings，集群侧由 ConfigMap 注入 |
| 8 | 默认租户（CLI `DEFAULT_TENANT` 与 `SharedSettings.default_tenant_id`） | **已收敛** | `ADR-08`：CLI 改为读 `SharedSettings().default_tenant_id` |
| 9 | 生效方式混杂（临时构造 `SharedSettings` / 进程内缓存） | **部分收敛** | `ADR-03`：删除 `ContextSettingsCache` 与 `context_settings_cache_ttl_sec`，设置快照只在业务操作边界取一次。**但“请求内临时构造 `SharedSettings()`”的全仓收口明确不在本次**（`技术债②`），仍属已知债 |
| 10 | 间接资源限制（batch 并发 / 会话时长 / 模型预算 / 渠道限额） | **已收敛（渠道限额除外）** | 联动校验在 `validate_platform_settings`（`batch_max_concurrency ≤ batch_platform_limit`、snip / preview / summary / memory 关系）；会话时长同源、模型预算分层；WeCom 渠道固定上传限额**明确留在代码**（第三方硬上限，不随业务设置放宽） |

## “保存后立即生效”的落地契约（已交付）

- 平台默认按 tenant 隔离持久化（append-only 版本行，`control.platform_setting`），写入仅限授权管理员；提交时做类型、范围、引用与联动校验，记录操作者、版本和审计。敏感值走现有 Secret 机制，公共接口只返回前端所需限额。
- Console 设置事务提交成功后返回新 revision；新 Run / 新 Task / 新业务操作在边界各取一次最新快照（`ADR-04`），执行中对象继续用其冻结快照。设置源不可读时业务操作明确失败，不悄悄使用过时默认（`RULE-06`）。
- 已认证会话的解析**不依赖设置可读**（`ADR-12`）：设置读取只发生在登录 / 续期这两个策略生效点，避免一条坏设置行掐断该租户全部已认证请求。
- Run / Task 保存 settings revision 与实际冻结策略；更改默认不覆盖已有 Agent 显式配置。设置页展示“平台默认 / 资源覆盖 / 本次实际值”。

建议系统设置页面分组：上下文压缩、Agent 执行默认、任务与投递、记忆与附件、产物生命周期、认证安全、语言 / 时区与 IM 展示。已有 Agent / 模型 / MCP / 平台管理页继续保留。

## 遗留与后续

1. 前端 `SKILL_ZIP_LIMIT_BYTES` 副本尚未接线到只读限额端点（收敛项 4）。
2. 请求内临时构造 `SharedSettings()` 的全仓收口是已知债，未在本次完成（`技术债②`）。
3. ~~`muad_contracts.platform_settings` 的 8 个非压缩分组（`agent` / `task` / `memory` / `artifact` / `auth` / `locale` / `im` / `mcp`）的 schema 默认值尚未进入 CSV~~ **已补齐**（2026-10-05，TASK-012）：这 8 个分组的 26 个 schema 默认值已进入 CSV 并标 `business`，机检的 AST 类清单同步扩展（`tests/test_configuration_inventory.py`），E-16 机检绿。
