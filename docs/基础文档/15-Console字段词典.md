# Console 字段词典与一致性规范

> 本文是 Console 字段命名的单一 UI 口径。数据库可保留技术字段名；API/UI 同一领域含义必须使用同一个中文名。字段级契约以 DTO 与接口测试为准。

## 1. 命名规则

- UI 文案使用中文领域名；标识符、API 字段保持英文。
- DB 的 JSON 列保留 `_json` 后缀；API/UI 使用无后缀名（如 `resolver_config`）。
- 同一含义在所有列表、详情、Modal、Drawer 中使用同一名称。
- 禁止裸用“状态”作为跨模块列名；必须带对象前缀（启用状态/任务状态/调度状态/投递状态/连接状态/测试状态）。
- 时间统一 `YYYY-MM-DD HH:mm:ss`，出参带 offset，前端按浏览器/租户时区渲染。
- ID 展示用短 ID + 前缀（Run/Task），完整 ID 在详情/复制动作中提供。
- Secret 默认只显示“已配置/未配置”，不回显明文。

---

## 2. 全局字段词典

| 技术字段/语义 | UI 名称 | 适用模块 |
| ------------ | ------- | -------- |
| `name` | 名称 | 全部 |
| `key` | 标识 | Agent/Skill/MCP/Model/ProjectPlatform |
| `enabled` | 启用状态 | Agent/Skill/MCP/Model/ProjectPlatform |
| `status`（User） | 启用状态 | 用户（ACTIVE/DISABLED） |
| `connection_status` | 连接状态 | MCP |
| `last_test_status` | 测试状态 | Model |
| Task `status` | 任务状态 | 后台任务 |
| Schedule `status` | 调度状态 | 定时任务 |
| `delivery_status` | 投递状态 | 后台任务/任务详情 |
| Task `id` | 任务 ID | 后台任务 |
| `trigger_type` | 触发方式 | 后台任务 |
| `skill_execution_mode` | 执行模式 | 后台任务/Skill |
| `intent_key` | 业务意图 | 后台任务/定时任务 |
| Task `schedule_id` | 定时任务 ID | 后台任务（SCHEDULED 来源，可点） |
| `actor_user_id` | 执行用户 | 后台任务/定时任务 |
| Task child progress | 子任务进度 | 已终态子任务数/子任务总数；无子任务显示 `0/0` |
| Schedule `cron_expr` | Cron 表达式 | 定时任务 |
| Schedule `timezone` | 时区 | 定时任务 |
| Schedule `end_reason` | 结束原因 | 定时任务 |
| Schedule `config_version` | 配置版本 | 定时任务 |
| `revision` | 修订版本 | Agent/Model/平台设置 |
| `platform_label` | 平台标签 | Skill |
| Agent `instructions` | 系统 Prompt | Agent |
| Skill current artifact | 当前版本 | Skill |
| `user_scope` | 用户范围 | Skill/MCP |
| `auth_config` | 认证配置 | MCP |
| `secret_header` | 密钥请求头 | MCP |
| `auth_secret` | 认证密钥 | MCP（只回 `auth_secret_configured`） |
| `base_url` | Base URL | Model |
| `model_id` | 模型 ID | Model |
| `protocol` | 协议 | Model |
| `resolver_type` | 接入方式 | ProjectPlatform |
| `resolver_config_json` | 访问配置 | ProjectPlatform（API/UI 名 `resolver_config`） |
| `adapter_key` | 平台适配器 | ProjectPlatform |
| `adapter_config_json` | 适配器配置 | ProjectPlatform（API/UI 名 `adapter_config`） |
| `credential_mode` | 凭据策略 | ProjectPlatform |
| `external_user_id` | 外部用户 ID | User IM 身份 |
| `bot_id` | bot_id | Agent IM / User IM |
| MCP Tool `effect` | 操作类型 | MCP Tool |
| `last_discovered_at` | 最近工具发现时间 | MCP |
| `started_at` | 开始时间 | Task |
| `finished_at` | 完成时间 | Task |
| `deadline_at` | 任务截止时间 | Task |
| `next_fire_at` | 下次触发时间 | Schedule |
| `last_fire_at` | 最近触发时间 | Schedule |
| `resource_type` | 审计类型 | 运行审计 |
| `run_id`/`task_id` | 关联对象 | 运行审计（短 ID + 前缀） |
| `resource_id` | 资源 ID | 运行审计 |
| `actor_user_id` | 操作用户 | 运行审计 |
| `action` | 动作 | 运行审计 |
| `result_status` | 执行结果 | 运行审计 |
| `trace_id` | Trace ID | 运行审计 |
| Memory `memory_key` | 记忆键 | 用户记忆 |
| Memory `author` | 来源 | USER/ADMIN/AGENT |
| Memory `status` | 状态 | ACTIVE/DISABLED/EXPIRED（由 enabled+expires_at 派生） |
| Memory `version` | 版本 | 用户记忆/版本历史 |
| Memory history `change_reason` | 变更原因 | 版本历史 |
| Memory history `actor_type`/`actor_id` | 操作者/操作者 ID | 版本历史 |
| Learning `state` | 状态 | 经验 |
| Learning `family` | 任务族 | 经验 |
| Learning `score` | 评分 | 经验 |
| Learning report `status` | 报告状态 | 经验 |
| Setting `group` | 设置分组 | 系统设置 |
| Setting `key` | 设置项 | 系统设置 |
| Setting `value` | 当前值 | 系统设置 |
| Setting `default_value` | 默认值 | 系统设置 |
| Setting `revision` | 版本 | 系统设置 |
| Setting `updated_by` | 更新人 | 系统设置 |

---

## 3. 状态字段规范

| 对象 | 字段名 | 状态/展示 |
| ---- | ------ | --------- |
| Agent/Skill/MCP/Model/ProjectPlatform | 启用状态 | 启用/停用（`enabled`） |
| User | 启用状态 | ACTIVE/DISABLED |
| MCP | 连接状态 | 连接成功/失败/未连接 |
| Model | 测试状态 | 未测试/测试中/成功/失败 |
| Task | 任务状态 | QUEUED/RUNNING/WAITING_EXTERNAL/WAITING_HUMAN/COMPLETED/FAILED/CANCELLED |
| Schedule | 调度状态 | ACTIVE/PAUSED/NEEDS_ATTENTION/ENDED/CANCELLED |
| Delivery | 投递状态 | PENDING/SENT/FAILED/NONE |
| Skill 审核 | 审核状态 | PENDING/APPROVED/REJECTED/SUPERSEDED |
| 经验 | 状态 | PROPOSED/TRIAL/ACTIVE/RETIRED |
| 记忆 | 状态 | ACTIVE/DISABLED/EXPIRED |

规则：

- “启用状态”只表达管理员是否允许使用；“连接/测试/调度/任务/投递”是不同事实，不能合并成一列。
- `needs_attention` 是“需关注”徽标，不等于任务状态；等待回答（WAITING_HUMAN）与需关注是两类事实。
- `ENDED` 只表示计划结束，不表示业务成功；业务结果看 Task。

---

## 4. 数量字段规则

列表数字字段必须体现数量：

```text
Skill 数量
MCP 数量
IM 通道数
授权用户数
使用 Agent 数
指定用户数
工具数
已配置用户凭据数
Agent 授权数
IM 身份数
用户记忆数
子任务数
```

详情 Tab 可保持对象名（使用 Agent/指定用户/IM 身份/用户记忆）。

---

## 5. 资源特殊规则

### 5.1 Model

```text
标识    = 平台内部稳定 key
模型 ID = 模型请求中的 model 字段
Base URL = 模型服务根地址
协议    = 当前仅 OpenAI-compatible
```

- 不设计“默认模型”；Agent 必须显式选择模型。
- 模型凭据只显示“已配置/未配置”，不默认回显。
- 模型池的成员/额度组按“模型成员/额度组”展示，不与模型 ID 混用。

### 5.2 ProjectPlatform

```text
接入方式   = BASE_URL / SERVICE_DISCOVERY（resolver_type）
访问配置   = base_url / service_name（resolver_config）
平台适配器 = Adapter key（adapter_key）
适配器配置 = platform_config_schema 动态字段（adapter_config）
凭据策略   = USER_ONLY / SHARED_ONLY / USER_THEN_SHARED / NONE
```

- 不用一个“访问地址/服务发现”文本框承载两种语义。
- DB 保留 `_json` 后缀；API/UI 使用无后缀名。

### 5.3 MCP

```text
认证方式 = 无认证 / Bearer Token / 自定义请求头
认证配置 = headers（非密静态头）+ secret_header（承载密钥的头名）
认证密钥 = auth_secret（只回 configured）
```

- `secret_header` 为空 → `Authorization: Bearer`；非空 → 原值写入该头。
- 静态头不得含密钥；当前仅 Streamable HTTP，不支持 stdio。

### 5.4 Task / Schedule

- 任务详情展示：任务状态、触发方式、执行用户、Skill、子任务进度、开始/完成/截止、投递状态、失败原因。
- 定时详情展示：调度状态、结束原因、Cron/时区、下次/最近触发、配置版本、历史 Task。
- 任务与定时是“实例/定义”关系，互相可达；已删计划的历史 Task 保留，目标不可读时给错误态，不落回空列表。

### 5.5 Memory

```text
记忆键 = memory_key（小写字母/数字/点/连字符，最长 64）
内容   = value（原文，最长 512）
类别   = PREFERENCE / WORK_STYLE / EXPLICIT / TASK_STATE
来源   = author：USER / ADMIN / AGENT
状态   = ACTIVE / DISABLED / EXPIRED
版本   = version（跨软删/恢复连续递增）
有效期 = expires_at（可空）
变更原因 = CREATE/OVERWRITE/CONFIRM/RESTORE/ENABLE/DISABLE/MIGRATION_BASELINE/
           USER_DELETE/ADMIN_DELETE/EXPIRE/QUOTA_EVICT/TASK_COMPLETE
操作者 = actor_type：USER/ADMIN/AGENT/SYSTEM
```

- 来源与操作者分列，不合并。
- 未知/历史值原样显示，不映射改写。
- 管理员录入的来源由 Runtime 派生，前端不提供来源选择。

### 5.6 系统设置

- 按分组展示（Agent/运行/模型/容量/IM 等）；每项显示当前值、默认值、版本、更新人/时间。
- 保存产生新 revision；历史保留；回滚也生成新版本。
- 敏感键只显示“已配置/未配置”，不回显明文。

### 5.7 经验管理

- 展示经验状态、任务族、评分、报告状态、来源、最近评估。
- 状态动作：启用/退役/重新提炼；未知状态原样显示。

---

## 6. 关联数量来源

所有数量由权威关系查询/COUNT 得出，不在资源对象重复存储：

```text
Agent 授权用户数 <- AgentAccessGrant COUNT
Skill 使用 Agent 数 <- AgentSkillBinding COUNT
MCP 使用 Agent 数 <- AgentMcpBinding COUNT
ProjectPlatform 已配置用户凭据数 <- UserCredentialRef COUNT
ProjectPlatform 共享凭据 <- SharedCredentialRef（已配置/未配置，每平台 0..1）
```

---

## 7. 列表、详情与深链

- 主展示字段即详情入口；不额外放“详情/查看”按钮。
- 操作列只保留真实动作：编辑、启停、刷新、取消、解绑、调用测试等。
- Drawer 标题、对象级操作、关闭 X 同行；操作按钮靠右。
- 关系操作保存后立即影响后续新 Run/Task；已有 Snapshot 不漂移。
- 跨页定位用列表页 + camelCase 查询参数；接收页定义初值语义，不回写 URL。
- 隐式筛选必须可见可清除（如 scheduleId 标签），避免用户被困在不可见条件里。
- 目标失效（已删/越权）给明确错误态，不静默落回空列表。

---

## 8. 一致性与测试

| 场景 | 断言 |
| ---- | ---- |
| 同一含义跨页面 | 中文名一致（字段词典） |
| 状态列 | 必带对象前缀，不裸用“状态” |
| 数量列 | 名称体现数量，且来自 COUNT |
| 时间 | `YYYY-MM-DD HH:mm:ss` + offset |
| Secret | 默认不回显；只显示 configured |
| 关联数量 | 不存冗余字段；从关系表 COUNT |
| 深链 | 键名成对、参数可刷新、目标失效有错误态 |
| 任务/定时 | 实例/定义关系、历史保留、错误态不落空列表 |

机检参考：字段词典契约测试、深链契约、状态标签、数量来源、Secret 不回显、列表/详情字段一致性。

---

*文档结束*
