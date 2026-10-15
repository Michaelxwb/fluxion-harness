# 15 Console 字段词典与一致性规范

## 1. 目的

本文件是 V1.4 Console 字段命名的单一 UI 口径。数据库可以保留技术字段名（JSON 列保留 `_json` 后缀，API/UI 使用无后缀名），但同一个领域含义在所有列表、详情、Modal、Drawer 中必须使用同一个中文字段名。

## 2. 全局字段词典

| 技术字段/语义 | UI 名称 | 适用模块 |
|---|---|---|
| `name` | 名称 | 全部 |
| `key` | 标识 | Agent/Skill/MCP/Model/ProjectPlatform |
| `enabled` | 启用状态 | Agent/Skill/MCP/Model/ProjectPlatform |
| `status` | 启用状态 | User（ACTIVE/DISABLED） |
| `connection_status` | 连接状态 | MCP |
| `last_test_status` | 测试状态 | Model |
| Task `status` | 任务状态 | 后台任务 |
| Schedule `status` | 调度状态 | 定时任务 |
| `delivery_status` | 投递状态 | 后台任务 |
| Task `id` | 任务 ID | 后台任务 |
| `trigger_type` | 触发方式 | 后台任务 |
| `execution_mode` | 执行模式 | 后台任务 / Skill Artifact |
| `intent_key` | 业务意图 | 后台任务 / 定时任务 |
| Task `schedule_id` | 定时任务 ID | 后台任务（`trigger_type=SCHEDULED` 时的来源；详情中为可点链接，指向定时任务详情） |
| Task `actor_user_id` | 执行用户 | 后台任务 / 定时任务 |
| Task child progress | 子任务进度 | 后台任务（`已终态子任务数/子任务总数`，无子任务显示 `0/0`） |
| Schedule `cron_expr` | Cron 表达式 | 定时任务 |
| Schedule `timezone` | 时区 | 定时任务 |
| `revision` | 修订版本 | Agent/Model |
| `platform_label` | 平台标签 | Skill |
| Agent `instructions` | 系统 Prompt | Agent |
| Skill current artifact version | 当前版本 | Skill |
| `user_scope` | 用户范围 | Skill/MCP |
| `base_url` | Base URL | Model |
| Model `model_id` | 模型 ID | Model |
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
| `run_id` / `task_id` | 关联对象（列表：Run/Task 前缀 + 短 id；CONFIG 行无关联） | 运行审计 |
| `resource_id` | 资源 ID（列表 CONFIG 行回退为 `资源类型/短id`） | 运行审计 |
| `actor_user_id` | 操作用户 | 运行审计 |
| `action` | 动作 | 运行审计 |
| `result_status` | 执行结果 | 运行审计（映射规则见 07 §10.11） |
| `trace_id` | Trace ID | 运行审计 |
| Memory `memory_key` | 记忆键 | 用户记忆 |
| Memory `author` | 来源 | 用户记忆（USER 用户已确认 / ADMIN 管理员录入 / AGENT Agent 推断） |
| Memory `status` | 状态 | 用户记忆（ACTIVE 生效中 / DISABLED 已停用 / EXPIRED 已过期，由 enabled+expires_at 派生） |
| Memory `version` | 版本 | 用户记忆 / 版本历史 |
| Memory history `change_reason` | 变更原因 | 版本历史 |
| Memory history `actor_type` / `actor_id` | 操作者 / 操作者 ID | 版本历史（与来源 author 分列，不合并） |

## 3. 状态字段规则

禁止裸用“状态”作为跨模块列名：

```text
Agent / Skill / MCP / Model / ProjectPlatform -> 启用状态（enabled）
User -> 启用状态（status: ACTIVE/DISABLED）
MCP -> 连接状态
Model -> 测试状态
Task -> 任务状态
Schedule -> 调度状态
Delivery -> 投递状态
```

## 4. 数量字段规则

列表展示数字时字段名必须体现数量：

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
```

详情 Tab 名称可以保持对象名，例如“使用 Agent”“指定用户”“IM 身份”。

## 5. Model 特殊规则

```text
标识    = 平台内部稳定 key
模型 ID = OpenAI API 中的 model 字段
Base URL = OpenAI-compatible 根地址
协议    = OpenAI（创建时固定，编辑不可修改）
```

不得再使用“模型标识”同时表达 key 和 model ID。

## 6. ProjectPlatform 特殊规则

```text
接入方式   = BASE_URL / SERVICE_DISCOVERY      （resolver_type）
访问配置   = base_url / service_name           （resolver_config_json）
平台适配器 = Adapter key                       （adapter_key）
适配器配置 = platform_config_schema 动态字段    （adapter_config_json）
凭据策略   = USER_ONLY / SHARED_ONLY / USER_THEN_SHARED / NONE（credential_mode）
```

DB 列名保留 `_json` 后缀；API 请求/响应与 UI 使用无后缀名（`resolver_config` / `adapter_config`）。
禁止使用一个“访问地址 / 服务发现”文本字段承载两个语义。

## 7. 关联数量来源

所有数量由权威关系查询/COUNT 得出，不在资源对象重复存储：

```text
Agent 授权用户数 <- agent_access_grant COUNT
Skill 使用 Agent 数 <- agent_skill_binding COUNT
MCP 使用 Agent 数 <- agent_mcp_binding COUNT
ProjectPlatform 已配置用户凭据数 <- user_credential_ref COUNT
ProjectPlatform 共享凭据 <- shared_credential_ref（已配置/未配置，每平台 0..1，无 priority）
```

## 8. 列表与详情入口

- 主展示字段即详情入口；不放“详情/查看”按钮；
- 操作列只保留编辑、启停、刷新、取消、解绑、调用测试等真实动作；
- Drawer 标题、对象级操作和关闭 X 在同一行，操作按钮固定靠右；
- 关系操作保存后立即影响后续新 Run/Task；已有 RuntimeSnapshot 不漂移。

## 9. 用户记忆字段

用户详情“用户记忆”页签与版本历史使用同一组中文名（实现见 Console `MemoryItem`/`MemoryHistoryItem`）：

```text
记忆键     = memory_key（小写字母/数字/点/连字符，最长 64）
内容       = content（事实为 value，最长 512 字符，按原文保存）
类别       = PREFERENCE 偏好 / WORK_STYLE 工作方式 / EXPLICIT 用户显式要求 / TASK_STATE 任务线索
来源       = author：USER 用户已确认 / ADMIN 管理员录入 / AGENT Agent 推断
状态       = ACTIVE 生效中 / DISABLED 已停用 / EXPIRED 已过期（由 enabled 与 expires_at 派生）
版本       = version（跨软删/恢复连续递增）
有效期     = expires_at（可空；过期条目重新启用必须指定新的有效期）
变更原因   = change_reason：CREATE 新建 / OVERWRITE 覆盖更新 / CONFIRM 用户确认更新 /
             RESTORE 恢复 / ENABLE 启用 / DISABLE 停用 / MIGRATION_BASELINE 迁移基线 /
             USER_DELETE 用户删除 / ADMIN_DELETE 管理员删除 / EXPIRE 到期自动退休 /
             QUOTA_EVICT 配额淘汰 / TASK_COMPLETE 任务完成退休
操作者     = actor_type：USER 用户本人 / ADMIN 管理员 / AGENT Agent 自动任务 / SYSTEM 系统后台任务
```

规则：

- **来源与操作者分列**：列表展示 `author`（内容来源/确认级别）；版本历史展示 `actor_type`/`actor_id`
  （谁执行了这次修改）。两者语义不同，禁止用一列承载。
- **未知值原样显示**：历史遗留的 `author`/`actor_type`/`change_reason` 不做映射改写，直接显示原值。
- 管理员写入口的 `author` 由 Runtime 派生（管理员录入），前端不提供来源选择控件。
- 已删除条目不在列表出现，但版本历史仍可查询。


## Model 特别约束

Model 页面不设计“默认模型”。`ModelDefinition` 只定义可选模型；`AgentDefinition.model_id` 显式指定 Agent 当前使用模型。DB/API 不提供 `is_default`。
