<!-- V1.11 module-split metadata -->
> **文档分档**：`Full`（模板 `design-full.md`）  
> **分档理由**：会话/消息/长期 Memory 持久化与隔离  
> **文档边界**：本文件是该模块唯一详细设计事实源；DB Owner 与接口 Owner 必须落在本模块，不允许在其他模块重复定义。  
> **拆分原则**：仅按可独立评审/编码/验收的一级模块拆分；不按单表/单接口继续碎片化。

# Conversation 与 User Memory 模块需求与设计一体化文档

> **文档编号**: MOD-MEM-V1.11 模块分档拆分版
> **文档版本**: V1.11 模块分档拆分版
> **创建日期**: 2026-09-11
> **文档状态**: 设计基线草案（待仓库 Spec Context 绑定后进入正式评审）
> **模板**: `design-full.md`；生成流程按 `cf-task:align` 的复杂后端/架构模块路径执行。
> **上游基线**: V1.8 完整总体设计 + V6 完整 Playbook + Console V0.8 Final。


**评审边界说明**：
- 第 2 章是需求基线（What），禁止实现阶段自行改变领域语义；
- 第 3-4 章是设计基线（How），DB 与每个接口必须以本文为准；
- `Spec Compliance Matrix` 当前依据设计基线生成，因本轮未提供仓库 `spec-context.yml` 与代码目录，**不得声称已通过 cf-task:align 的 repo Spec Gate**；落码前必须在真实仓库执行 `refresh/catalog/bind`。


## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 产品/架构负责人 | 待定 | 需求定义、领域边界、与总设/Playbook 一致性 |
| 开发负责人 | 待定 | 技术方案、DB/API、实现 |
| 测试负责人 | 待定 | S/E/B 场景、E2E Gate |
| 安全/运维 | 待定 | Secret、隔离、发布、监控 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| V1.11 模块分档拆分版 | 2026-09-11 | ChatGPT / 待项目负责人确认 | 按 cf-task:align + design-full 从最新完整总设/Playbook/交互稿重新生成；细化 DB 与全部接口 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Conversation 与 User Memory |
| 模块ID | MOD-MEM |
| 所属系统/产品线 | Fluxion 通用智能服务执行框架 |
| 需求类型 | 中大型功能 / 架构演进 |
| 业务背景 | 用户希望跨会话、跨 Web/企业微信保持稳定使用上下文，但 Memory 不能替代业务平台实时权威数据。 |
| 核心目标 | 设计 Conversation/Message/UserMemory SoT、/new 语义、跨渠道身份复用和 Runtime 加载/受控写入接口。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|---|---|
| 目标用户 | End User、Agent Runtime、Admin（治理） |
| 当前问题 | 把 Memory/Session 放 Runtime 本地会随 Pod 丢失；把任务/设备/权限写入 Memory 会产生陈旧业务事实。 |
| 业务影响 | 用户体验不连续、跨渠道上下文错乱、错误业务决策。 |
| 预期价值 | 长期用户偏好跨会话可复用，同时实时业务事实始终通过 Capability 查询。 |

### 2.3 功能方案

#### 2.3.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-MEM-01 | Conversation | 用户×Agent 会话生命周期。 | P0 | Playbook U06 |
| FEAT-MEM-02 | Message | 消息持久化/大内容引用。 | P0 | 对话 |
| FEAT-MEM-03 | UserMemory | tenant+user 长期受控上下文。 | P0 | Playbook U06 |
| FEAT-MEM-04 | /new | 新 Conversation，不清 Memory。 | P0 | IM 命令 |
| FEAT-MEM-05 | Memory Governance | 查看/更新/撤销与来源追踪。 | P0 | 隐私治理 |

### 2.4 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | Conversation/Message/UserMemory DB、Runtime load/append、/new 语义、Memory policy/来源/撤销。 |
| 非范围（Out of Scope） | 业务平台实时数据、Execution Task State、向量语义 Memory 高级平台（后续）。 |
| 前置假设 | 上游总设、公共 DB/API 规范、外部基础设施可用 |
| 有意妥协 / 技术债 | 无；未来扩展必须有真实旅程驱动 |

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|---|---|---|---|
| RULE-MEM-01 | 边界 | UserMemory≠Conversation≠TaskState≠BusinessData。 | S-MEM-01 |
| RULE-MEM-02 | 跨渠道 | 同一 PlatformUser 切换渠道仍读取同一 UserMemory。 | S-MEM-02 |
| RULE-MEM-03 | 新会话 | /new 只创建新 Conversation，不清理 UserMemory/Grant。 | S-MEM-03 |
| RULE-MEM-04 | 权威数据 | Memory 不存当前设备、实时权限、任务状态等业务权威事实。 | S-MEM-04 |
| RULE-MEM-05 | 隔离 | 所有 Memory 按 tenant+user 隔离并带来源。 | S-MEM-05 |

#### 2.5.2 功能验收场景

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 归属 | 前置条件 | 操作步骤 | 预期结果 |
|---|---|---|---|---|---|---|---|---|
| S-MEM-01 | FEAT-MEM-03 | P0 | E2E | Runtime→PG | 本模块 | 用户偏好已写 | 新会话请求 | Runtime 注入同一偏好 |
| S-MEM-02 | FEAT-MEM-03 | P0 | E2E | WeCom/WebChat→same user→PG | 后置 → 模块 10 | 两渠道映射同一用户 | 分别发消息 | 读取一致 UserMemory |
| S-MEM-03 | FEAT-MEM-04 | P0 | E2E | /new→Conversation | 本模块 | 已有会话+Memory | 执行 /new | 新 conversation_id，Memory 保留 |
| S-MEM-04 | FEAT-MEM-05 | P0 | E2E | Admin API→PG→Runtime | 本模块 | Memory ACTIVE | 撤销 | 下一请求不再注入 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 归属 | 触发条件 | 系统行为 | 用户感知 |
|---|---|---|---|---|---|---|---|
| E-MEM-01 | FEAT-MEM-03 | integration | Memory policy validator | 本模块 | 写入实时设备/任务状态类禁用 category | MEMORY_CONTENT_FORBIDDEN | 提示改走 Capability |
| E-MEM-02 | FEAT-MEM-01 | integration | Conversation ownership | 本模块 | 用户访问他人 conversation_id | 404/403 fail closed | 不泄露存在性 |

#### 2.5.3 非功能指标

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|---|---|---|---|
| NFR-REL-01 | 可靠执行 | 不得因单 Runtime/Worker 进程退出丢失权威状态 | SIGKILL/E2E |
| NFR-SEC-01 | 可信身份 | LLM/客户端不得覆盖 tenant/actor/secret | 安全测试 |
| NFR-OBS-01 | 可追踪 | 关键路径可按 request_id/trace_id/execution_id 定位 | 集成/E2E |

## 3. 技术设计

### 3.1 方案选型

#### 3.1.1 关键决策记录

| 决策点 | 选择 | 被否决项 | 理由 | 可逆性 |
|---|---|---|---|---|
| Memory SoT | PostgreSQL 外置 | Runtime 本地 MEMORY.md/SQLite | 多副本一致 | 难 |
| V1 复杂度 | 结构化长期 Memory | 一开始全量向量记忆/自动画像 | 先满足真实旅程 | 易 |
| 业务数据 | 每次 Capability 查询 | 复制到 Memory | 避免陈旧 truth | 难 |

#### 3.1.2 技术栈

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 语言 | Python | 3.12+（仓库实际版本落码前确认） | 与 Agent/LLM 生态一致 |
| Web/API | FastAPI + Pydantic | 仓库实际版本确认 | 类型契约与异步 IO |
| ORM | SQLAlchemy 2 Async | 仓库实际版本确认 | 异步 PostgreSQL |
| 数据库 | PostgreSQL | 外部部署 | 业务 SoT |

### 3.2 架构设计

```mermaid
flowchart LR
  Channel["Channel Identity"] --> User["PlatformUser"]
  User --> Conv[(conversation)]
  Conv --> Msg[(message)]
  User --> Mem[(user_memory)]
  RT["Agent Runtime"] --> Conv
  RT --> Mem
  RT --> Agent["Agent Executor"]
  Agent --> Cap["Capability for live business data"]
```

#### 3.2.1 模块职责分层

| 层级 | 职责 | 禁止事项 |
|---|---|---|
| Handler/API | 协议解析、DTO、权限入口、统一错误映射 | 业务逻辑/直接 SQL |
| Application Service | 用例编排、事务边界、领域校验 | 依赖具体 Web 框架 |
| Domain | 领域对象/规则 | 基础设施依赖 |
| Repository/Port | 持久化/外部能力抽象 | 泄露 Secret/跨领域修改 |
| Adapter | PostgreSQL/HTTP/MCP 等实现 | 改变领域语义 |

#### 3.2.2 外部依赖清单

| 外部系统/模块 | 依赖类型 | 协议/接口 | 超时/一致性 | 降级策略 |
|---|---|---|---|---|
| PlatformUser/Grant | 身份/授权 | DB/Library | current | 无权 fail closed |
| Agent Runtime | 读写会话 | Library | 实时 | PG 不可用则对话不能持久 |
| ObjectStore | 大消息/附件 | Port | 外部 | 失败返回附件错误 |

### 3.3 数据设计

#### 3.3.1 表清单

| 表名 | 职责 | 所有权 |
|---|---|---|
| conversation | 当前会话事实；/new 创建新会话但不清空 User Memory/授权。 | Conversation 与 User Memory |
| message | Conversation 内消息记录，用于上下文/审计；不等于长期 Memory。 | Conversation 与 User Memory |
| user_memory | 跨 Conversation/Channel 的受控长期用户上下文；不得保存业务平台实时权威数据。 | Conversation 与 User Memory |

#### 表 `conversation`

**职责**：当前会话事实；/new 创建新会话但不清空 User Memory/授权。

| 字段名 | 类型 | 可空 | 默认值 | 索引 | 说明 |
|---|---|---|---|---|---|
| tenant_id | UUID | N |  | IDX | 租户 |
| platform_user_id | UUID | N |  | FK,IDX | 用户 |
| agent_id | UUID | N |  | FK,IDX | Agent |
| status | VARCHAR(32) | N | ACTIVE | IDX | ACTIVE/CLOSED |
| channel_type | VARCHAR(32) | Y |  | IDX | 最近入口渠道 |
| title | VARCHAR(512) | Y |  |  | 可选标题 |
| last_message_at | TIMESTAMPTZ | Y |  | IDX | 最后消息 |
| id | UUID | N | gen_random_uuid() | PK | 主键 |
| is_deleted | BOOLEAN | N | FALSE | IDX | 软删除标记；默认查询必须过滤 FALSE |
| create_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 创建时间 |
| update_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 更新时间；不可变表除外 |

**索引设计**

| 索引名 | 类型 | 字段 | 使用场景 |
|---|---|---|---|
| idx_conversation_user_agent | BTREE | tenant_id,platform_user_id,agent_id,last_message_at DESC | 会话历史 |

#### 表 `message`

**职责**：Conversation 内消息记录，用于上下文/审计；不等于长期 Memory。

| 字段名 | 类型 | 可空 | 默认值 | 索引 | 说明 |
|---|---|---|---|---|---|
| tenant_id | UUID | N |  | IDX | 租户 |
| conversation_id | UUID | N |  | FK,IDX | 会话 |
| role | VARCHAR(32) | N |  | IDX | USER/ASSISTANT/SYSTEM/TOOL |
| content | TEXT | Y |  |  | 小文本内容 |
| content_ref | VARCHAR(1024) | Y |  |  | 大内容/附件引用 |
| message_type | VARCHAR(32) | N | TEXT |  | TEXT/TOOL/FILE/EVENT |
| external_message_id | VARCHAR(512) | Y |  | IDX | 渠道消息去重 |
| trace_id | VARCHAR(128) | Y |  | IDX | 关联 Trace |
| id | UUID | N | gen_random_uuid() | PK | 主键 |
| is_deleted | BOOLEAN | N | FALSE | IDX | 软删除标记；默认查询必须过滤 FALSE |
| create_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 创建时间 |

**约束**

- content/content_ref 至少一个有效
- 渠道 external_message_id 可按 adapter scope 唯一

**索引设计**

| 索引名 | 类型 | 字段 | 使用场景 |
|---|---|---|---|
| idx_message_conversation | BTREE | tenant_id,conversation_id,create_time | 按序加载 |
| idx_message_external | BTREE | tenant_id,external_message_id | 去重/追踪 |

**不可变约束**：创建后禁止业务 UPDATE/DELETE；如需演进创建新记录并更新上层 current 指针。

#### 表 `user_memory`

**职责**：跨 Conversation/Channel 的受控长期用户上下文；不得保存业务平台实时权威数据。

| 字段名 | 类型 | 可空 | 默认值 | 索引 | 说明 |
|---|---|---|---|---|---|
| tenant_id | UUID | N |  | IDX | 租户 |
| platform_user_id | UUID | N |  | FK,IDX | 用户 |
| memory_key | VARCHAR(256) | N |  | IDX | 稳定 key/category |
| memory_value | JSONB | N | {} |  | 受控内容 |
| source_type | VARCHAR(64) | N |  |  | EXPLICIT/DERIVED/ADMIN |
| source_ref | VARCHAR(512) | Y |  |  | 来源 conversation/message |
| status | VARCHAR(32) | N | ACTIVE | IDX | ACTIVE/REVOKED |
| confidence | NUMERIC(5,4) | Y |  |  | 仅推断型使用 |
| revision | BIGINT | N | 1 |  | 并发版本 |
| id | UUID | N | gen_random_uuid() | PK | 主键 |
| is_deleted | BOOLEAN | N | FALSE | IDX | 软删除标记；默认查询必须过滤 FALSE |
| create_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 创建时间 |
| update_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 更新时间；不可变表除外 |

**约束**

- UNIQUE (tenant_id,platform_user_id,memory_key) WHERE is_deleted=false
- 不得写入实时客户设备/任务状态/权限等业务权威事实

**索引设计**

| 索引名 | 类型 | 字段 | 使用场景 |
|---|---|---|---|
| uk_user_memory_key | UNIQUE | tenant_id,platform_user_id,memory_key,is_deleted | 读取/更新 |
| idx_user_memory_status | BTREE | tenant_id,platform_user_id,status,is_deleted | Context 加载 |

#### 3.3.2 ER 图

```mermaid
erDiagram
    CONVERSATION {
      UUID tenant_id FK
      UUID platform_user_id FK
      UUID agent_id FK
      VARCHAR_32_ status
      VARCHAR_32_ channel_type
      VARCHAR_512_ title
    }
    MESSAGE {
      UUID tenant_id FK
      UUID conversation_id FK
      VARCHAR_32_ role
      TEXT content
      VARCHAR_1024_ content_ref
      VARCHAR_32_ message_type
    }
    USER_MEMORY {
      UUID tenant_id FK
      UUID platform_user_id FK
      VARCHAR_256_ memory_key
      JSONB memory_value
      VARCHAR_64_ source_type
      VARCHAR_512_ source_ref
    }
```

#### 3.3.3 数据一致性与软删除规则

- 所有 Framework 自建可变表使用 `is_deleted/create_time/update_time`；查询默认过滤 `is_deleted=false`。
- FK 只引用同一租户可见对象；跨租户引用必须在 Application Service 拒绝。
- Secret/Token/Password 不落业务表明文，只保存 `secret_ref/credential_ref`。
- 不可变 Release/Snapshot/Artifact 使用 append-only；需要更新时新建记录。
- `revision` 用于 direct-effect 配置的乐观并发和审计，不等同于发布版本。

### 3.4 接口设计

#### 3.4.1 接口清单

| 接口ID | 名称 | 形态 | 方法/签名 | 路径/用途 |
|---|---|---|---|---|
| CONV-API-01 | 会话列表 | HTTP | GET | /api/v1/conversations |
| CONV-API-02 | 会话详情 | HTTP | GET | /api/v1/conversations/{conversation_id} |
| CONV-API-03 | 创建新会话 | HTTP | POST | /api/v1/conversations |
| CONV-API-04 | 关闭会话 | HTTP | POST | /api/v1/conversations/{conversation_id}/close |
| MEM-API-01 | 用户 Memory 列表 | HTTP | GET | /api/v1/users/{user_id}/memory |
| MEM-API-02 | 写入/更新受控 Memory | HTTP | PUT | /api/v1/users/{user_id}/memory/{memory_key} |
| MEM-API-03 | 撤销 Memory | HTTP | DELETE | /api/v1/users/{user_id}/memory/{memory_key} |
| CONV-LIB-01 | 解析或创建 Conversation | Library | async def resolve_conversation(ctx: TrustedExecutionContext, agent_id: UUID, conversation_id: UUID \| None, channel_meta: ChannelMeta \| None) -> Conversation |  |
| MEM-LIB-01 | 加载长期 Memory | Library | async def load_user_memory(tenant_id: UUID, user_id: UUID, policy: MemoryPolicy) -> list[MemoryItem] |  |

#### CONV-API-01: 会话列表

**入口类型**：HTTP

**契约**：`GET /api/v1/conversations`

**认证/授权**：Admin/内部 WebChat 用户作用域

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| page | integer | N | 页码，从 1 开始；默认 1 |
| page_size | integer | N | 每页条数；默认 20，最大 100 |
| user_id | uuid | N | Admin 可筛选；用户端隐含当前用户 |
| agent_id | uuid | N | Agent |

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| items | array<ConversationSummary> | id/user/agent/status/title/last_message_at |
| total | integer | 总数 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

仅使用公共错误码。

**处理逻辑**

```text
按当前身份权限 tenant/user scope 查询；服务端分页。
```

#### CONV-API-02: 会话详情

**入口类型**：HTTP

**契约**：`GET /api/v1/conversations/{conversation_id}`

**认证/授权**：None

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| conversation | object | 元数据 |
| messages | array<Message> | 默认最近窗口；大历史另分页 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "conversation": {},
    "messages": []
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| CONVERSATION_NOT_FOUND | 不存在/无权限 | 404 |

**处理逻辑**

```text
ownership/role check → 加载会话 → 最近消息窗口。
```

#### CONV-API-03: 创建新会话

**入口类型**：HTTP

**契约**：`POST /api/v1/conversations`

**认证/授权**：None

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| agent_id | uuid | Y | 目标 Agent |
| channel_type | string | N | WEBCHAT 等 |

**请求示例**

```json
{
  "agent_id": "<agent_id>",
  "channel_type": "<channel_type>"
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| conversation_id | uuid | 新会话 |
| agent_id | uuid | Agent |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "conversation_id": "<conversation_id>",
    "agent_id": "<agent_id>"
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| AGENT_ACCESS_DENIED | 无 AgentAccessGrant | 403 |

**处理逻辑**

```text
require_agent_access → INSERT conversation；不清空 User Memory。
```

#### CONV-API-04: 关闭会话

**入口类型**：HTTP

**契约**：`POST /api/v1/conversations/{conversation_id}/close`

**认证/授权**：None

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| status | string | CLOSED |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "<status>"
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| CONVERSATION_NOT_FOUND | 不存在 | 404 |

**处理逻辑**

```text
ownership/role check → status=CLOSED；在途 Execution 不因会话关闭自动取消。
```

#### MEM-API-01: 用户 Memory 列表

**入口类型**：HTTP

**契约**：`GET /api/v1/users/{user_id}/memory`

**认证/授权**：Admin；未来用户自助页面可复用受限 DTO

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| page | integer | N | 页码，从 1 开始；默认 1 |
| page_size | integer | N | 每页条数；默认 20，最大 100 |
| status | string | N | ACTIVE/REVOKED |

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| items | array<UserMemoryView> | key/value/source/status/times |
| total | integer | 总数 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

仅使用公共错误码。

**处理逻辑**

```text
tenant/user scoped query；敏感推断按 policy 过滤。
```

#### MEM-API-02: 写入/更新受控 Memory

**入口类型**：HTTP

**契约**：`PUT /api/v1/users/{user_id}/memory/{memory_key}`

**认证/授权**：None

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| memory_value | object | Y | 结构化长期上下文 |
| source_type | string | Y | EXPLICIT/DERIVED/ADMIN |
| source_ref | string | N | 来源 |
| revision | integer | N | 已有时乐观锁 |

**请求示例**

```json
{
  "memory_value": {},
  "source_type": "<source_type>",
  "source_ref": "<source_ref>",
  "revision": 1
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| memory_key | string | key |
| revision | integer | 新 revision |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "memory_key": "<memory_key>",
    "revision": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| MEMORY_CONTENT_FORBIDDEN | 试图写实时业务权威数据/禁止类型 | 400 |
| REVISION_CONFLICT | 冲突 | 409 |

**处理逻辑**

```text
policy validator → upsert user_memory → audit；Derived 写入需满足 MemoryPolicy。
```

#### MEM-API-03: 撤销 Memory

**入口类型**：HTTP

**契约**：`DELETE /api/v1/users/{user_id}/memory/{memory_key}`

**认证/授权**：None

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| status | string | REVOKED |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "<status>"
  },
  "request_id": "req_xxx"
}
```

**错误码**

仅使用公共错误码。

**处理逻辑**

```text
soft revoke/status → audit；后续 Runtime 不再注入。
```

#### CONV-LIB-01: 解析或创建 Conversation

**入口类型**：Library

**函数签名**

```python
async def resolve_conversation(ctx: TrustedExecutionContext, agent_id: UUID, conversation_id: UUID | None, channel_meta: ChannelMeta | None) -> Conversation
```

**入参**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| ctx | TrustedExecutionContext | Y | 用户 |
| agent_id | uuid | Y | Agent |
| conversation_id | uuid | N | 已有会话 |

**返回**

| 字段 | 类型 | 说明 |
|---|---|---|
| conversation | Conversation | 会话 |

**异常/错误**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| CONVERSATION_ACCESS_DENIED | 归属不匹配 | 403 |

**处理逻辑**

```text
require_agent_access → 若 id 有值校验 user+agent ownership；否则创建新会话。
```

#### MEM-LIB-01: 加载长期 Memory

**入口类型**：Library

**函数签名**

```python
async def load_user_memory(tenant_id: UUID, user_id: UUID, policy: MemoryPolicy) -> list[MemoryItem]
```

**入参**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| tenant_id | uuid | Y | 租户 |
| user_id | uuid | Y | 用户 |
| policy | MemoryPolicy | Y | Agent 策略 |

**返回**

| 字段 | 类型 | 说明 |
|---|---|---|
| items | array<MemoryItem> | ACTIVE、策略允许的长期记忆 |

**处理逻辑**

```text
索引查询 ACTIVE memory → 按 policy/category 限制 → 返回；不读取业务平台实时事实。
```

### 3.5 质量实现方案

#### 3.5.1 性能与容量

| 热点路径 | 负载量级 | 潜在瓶颈 | 实现方案 | 目标值 |
|---|---|---|---|---|
| Conversation context load | 每请求 | 消息历史过大 | 只加载策略窗口/summary；历史分页 | 上下文 token 受模型限制 |
| Message history | 数据增长 | 大表扫描 | conversation_id+create_time 索引/归档策略 | 按容量演进 |

#### 3.5.2 可靠性

所有外部调用有 deadline；重试有界且只对可安全重试错误；业务权威状态外置；进程崩溃后能恢复或明确失败。

#### 3.5.3 安全性

用户只能读取自身 Conversation/Memory（Admin 有治理权限）；Memory 变更可审计；隐私删除/清理策略必须与软删除/法务要求一致。

#### 3.5.4 可观测性

日志、Metrics、Trace 统一关联 request_id/trace_id；Execution 路径附带 execution_id/step_id；错误码稳定。

#### 3.5.5 测试策略

Domain/validator 单测；Repository/Provider 真 PG/外部 Stub 集成；核心用户旅程做 E2E；安全和崩溃恢复不得只靠 mock。

## 4. 部署与运维

### 4.1 部署架构

随其所属运行角色部署；PostgreSQL/Redis/Object Store/Secret Provider/OTel Backend 均为外部依赖，不打包进应用 Compose/Helm。

### 4.2 发布与回滚

DB 变更使用向前兼容迁移；应用支持滚动回滚；若涉及不可变 Release/Artifact，只切换 current 指针，不覆盖历史。

### 4.3 监控告警

至少监控请求/调用量、错误率、延迟、外部依赖失败、关键队列/执行积压和配置加载失败；阈值由环境基线确定。

### 4.4 数据迁移

若无历史生产数据则直接按新 Schema 建表；若已有部署，使用 Alembic 等可回滚/可前滚迁移，禁止运行时隐式改表。

## 5. 风险与依赖

| 风险ID | 描述 | 影响 | 应对措施 | 验证场景 |
|---|---|---|---|---|
| RISK-MEM-01 | 跨模块边界在实现中被绕过 | 形成双事实源/不可测试 | Architecture Gate + code review | E2E/静态检查 |

## 6. 需求追溯矩阵

| 功能ID | 接口ID | 验证场景 | 测试层级 | 状态 |
|---|---|---|---|---|
| FEAT-MEM-01 | CONV-API-01, CONV-API-02 | E-MEM-02 | E2E/integration | 待实现/评审 |
| FEAT-MEM-02 | CONV-API-02, CONV-API-03 | 见 §2.5 | E2E/integration | 待实现/评审 |
| FEAT-MEM-03 | CONV-API-03, CONV-API-04 | S-MEM-01, S-MEM-02, E-MEM-01 | E2E/integration | 待实现/评审 |
| FEAT-MEM-04 | CONV-API-04, MEM-API-01 | S-MEM-03 | E2E/integration | 待实现/评审 |
| FEAT-MEM-05 | MEM-API-01, MEM-API-02 | S-MEM-04 | E2E/integration | 待实现/评审 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| DESIGN/MEM#RULE-MEM-01 | design-baseline | 约束实现与验收 | §2.5 RULE-MEM-01 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MEM#RULE-MEM-02 | design-baseline | 约束实现与验收 | §2.5 RULE-MEM-02 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MEM#RULE-MEM-03 | design-baseline | 约束实现与验收 | §2.5 RULE-MEM-03 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MEM#RULE-MEM-04 | design-baseline | 约束实现与验收 | §2.5 RULE-MEM-04 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MEM#RULE-MEM-05 | design-baseline | 约束实现与验收 | §2.5 RULE-MEM-05 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |

## 附录 A：接口与 DB 落码检查清单

- [ ] 每个 HTTP Endpoint 已有 DTO、鉴权、错误码、处理逻辑、测试。
- [ ] 每个 Library/CLI 接口有稳定签名和异常语义。
- [ ] 每张表字段、类型、NULL、默认值、唯一约束、索引、软删除策略已落实迁移。
- [ ] Request/Response 字段不接受客户端传入可信 tenant/actor/credential。
- [ ] 列表接口使用服务端分页，避免无界返回。
- [ ] 修改类接口有 revision/冲突语义；高风险/副作用路径满足幂等和审计。
- [ ] 仓库 Spec Context 已 refresh/catalog/bind 且 required Rules 映射到 S/E/B verifier。
