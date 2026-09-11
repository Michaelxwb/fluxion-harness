<!-- V1.11 module-split metadata -->
> **文档分档**：`Full`（模板 `design-full.md`）  
> **分档理由**：ModelConfig、Secret、Provider 调用和流式运行  
> **文档边界**：本文件是该模块唯一详细设计事实源；DB Owner 与接口 Owner 必须落在本模块，不允许在其他模块重复定义。  
> **拆分原则**：仅按可独立评审/编码/验收的一级模块拆分；不按单表/单接口继续碎片化。

# 模型配置与调用 模块需求与设计一体化文档

> **文档编号**: MOD-MODEL-V1.11 模块分档拆分版
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
| 模块名称 | 模型配置与调用 |
| 模块ID | MOD-MODEL |
| 所属系统/产品线 | Fluxion 通用智能服务执行框架 |
| 需求类型 | 中大型功能 / 架构演进 |
| 业务背景 | V1 模型接入收敛为 OpenAI-Compatible；一个 Agent 选择一个 ModelConfig；模型配置保存后对新请求直接生效，不做产品级版本发布。 |
| 核心目标 | 设计 ModelConfig DB/API、Secret 管理、连通测试、流式 ModelClient、timeout/参数合并和 observability。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|---|---|
| 目标用户 | Builder/Admin、Agent Runtime |
| 当前问题 | 模型新增入口曾缺失；Provider/模型名/Secret 若混在 Agent 或环境变量中，难管理、难测试、不同 Agent 难复用。 |
| 业务影响 | Console 不完整、Secret 风险、运行配置漂移。 |
| 预期价值 | 统一可复用模型配置，一个 Agent 一个模型，调用行为可观测且可切换。 |

### 2.3 功能方案

#### 2.3.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-MODEL-01 | Model CRUD | OpenAI-compatible 配置。 | P0 | Console 模型 |
| FEAT-MODEL-02 | Secret | API Key 外部存储。 | P0 | 安全 |
| FEAT-MODEL-03 | Connection Test | 最小真实请求验证。 | P0 | Builder |
| FEAT-MODEL-04 | ModelClient | 流式/工具调用统一 Event。 | P0 | Agent Executor |
| FEAT-MODEL-05 | Direct-effect | revision/audit，无 Draft/Publish。 | P0 | 总体设计 |

### 2.4 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | model_config、CRUD/test、Secret ref、OpenAI-compatible async streaming client、usage/latency/error。 |
| 非范围（Out of Scope） | 多模型路由策略、A/B、模型 Marketplace、Agent 多模型编排。 |
| 前置假设 | 上游总设、公共 DB/API 规范、外部基础设施可用 |
| 有意妥协 / 技术债 | 无；未来扩展必须有真实旅程驱动 |

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|---|---|---|---|
| RULE-MODEL-01 | 协议 | V1 protocol 固定 OPENAI_COMPATIBLE。 | S-MODEL-01 |
| RULE-MODEL-02 | Agent | 一个 Agent V1 只引用一个 ModelConfig。 | S-MODEL-02 |
| RULE-MODEL-03 | Secret | API key 不落 DB/响应/日志明文。 | S-MODEL-03 |
| RULE-MODEL-04 | 生效 | 编辑 direct-effect + revision，新请求使用 current。 | S-MODEL-04 |
| RULE-MODEL-05 | Deadline | 模型调用必须有 request deadline，禁止无限 retry。 | S-MODEL-05 |

#### 2.5.2 功能验收场景

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 归属 | 前置条件 | 操作步骤 | 预期结果 |
|---|---|---|---|---|---|---|---|---|
| S-MODEL-01 | FEAT-MODEL-01 | P0 | E2E | Console→API→Secret+PG | 本模块 | Builder | 新增模型 | 列表可见且 Secret 仅 configured |
| S-MODEL-02 | FEAT-MODEL-03 | P0 | E2E | API→OpenAI-compatible endpoint | 本模块 | 配置有效 | Test | 返回 ok/latency/provider request id |
| S-MODEL-03 | FEAT-MODEL-04 | P0 | E2E | AgentExecutor→ModelClient SSE | 后置 → 模块 04 | Agent 引用模型 | 对话 | 逐 delta/tool events |
| S-MODEL-04 | FEAT-MODEL-05 | P0 | E2E | PUT→Runtime resolve | 本模块 | Agent 正在服务 | 更新 model_name r2 | 新请求使用 r2，不需 publish |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 归属 | 触发条件 | 系统行为 | 用户感知 |
|---|---|---|---|---|---|---|---|
| E-MODEL-01 | FEAT-MODEL-03 | integration | Model test | 本模块 | 401/timeout/protocol invalid | 稳定 MODEL_* 错误 | 不泄露 key |
| E-MODEL-02 | FEAT-MODEL-05 | integration | revision | 本模块 | 旧 revision PUT | REVISION_CONFLICT | 刷新后重试 |

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
| 协议 | OpenAI-Compatible only | 多个 Provider 专有适配 | V1 收敛 | 未来可扩展 |
| 模型关系 | Agent 1→1 ModelConfig | 策略多模型 | 避免过度设计 | 中 |
| Secret | SecretProvider ref | DB ciphertext/plain | 安全边界 | 中 |

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
  Console["Model CRUD/Test"] --> Config[(model_config)]
  Console --> Secret["Secret Provider"]
  Agent["Agent Executor"] --> Client["ModelClient"]
  Client --> Config
  Client --> Secret
  Client --> API["OpenAI-Compatible Endpoint"]
  Client --> OTel["Usage/Latency/Trace"]
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
| Secret Provider | API Key | Port | fail closed | 不可降级 |
| OpenAI-compatible endpoint | 模型 | HTTPS/SSE | deadline | 稳定模型错误 |
| Agent Definition | FK | DB | current | 一个 Agent 一个模型 |

### 3.3 数据设计

#### 3.3.1 表清单

| 表名 | 职责 | 所有权 |
|---|---|---|
| model_config | OpenAI-Compatible 模型配置；V1 一个 Agent 选择一个 ModelConfig；编辑 direct-effect + revision。 | 模型配置与调用 |

#### 表 `model_config`

**职责**：OpenAI-Compatible 模型配置；V1 一个 Agent 选择一个 ModelConfig；编辑 direct-effect + revision。

| 字段名 | 类型 | 可空 | 默认值 | 索引 | 说明 |
|---|---|---|---|---|---|
| tenant_id | UUID | N |  | IDX | 租户 |
| name | VARCHAR(256) | N |  | IDX | 配置名称 |
| key | VARCHAR(128) | N |  | UK | 稳定标识 |
| protocol | VARCHAR(32) | N | OPENAI_COMPATIBLE |  | V1 固定协议 |
| base_url | VARCHAR(1024) | N |  |  | OpenAI-compatible API Base URL |
| model_name | VARCHAR(256) | N |  | IDX | 实际模型名 |
| api_key_secret_ref | VARCHAR(512) | N |  |  | Secret Provider 引用 |
| default_parameters | JSONB | N | {} |  | temperature/max_tokens 等受控参数 |
| extra_headers | JSONB | N | {} |  | 非 Secret Header；Secret Header 仍用 ref |
| request_timeout_ms | INTEGER | N | 60000 |  | 请求 deadline，必须 >0 |
| enabled | BOOLEAN | N | TRUE | IDX | 是否可用于新请求 |
| revision | BIGINT | N | 1 |  | 乐观并发/审计版本 |
| id | UUID | N | gen_random_uuid() | PK | 主键 |
| is_deleted | BOOLEAN | N | FALSE | IDX | 软删除标记；默认查询必须过滤 FALSE |
| create_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 创建时间 |
| update_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 更新时间；不可变表除外 |

**约束**

- UNIQUE (tenant_id,key) WHERE is_deleted=false
- protocol=OPENAI_COMPATIBLE
- api_key_secret_ref 不得保存明文 Key

**索引设计**

| 索引名 | 类型 | 字段 | 使用场景 |
|---|---|---|---|
| uk_model_config_key | UNIQUE | tenant_id,key | 稳定引用 |
| idx_model_config_status | BTREE | tenant_id,enabled,is_deleted | 模型列表/解析 |

#### 3.3.2 ER 图

本模块没有需要单独表达的多表 ER；跨模块关系见《数据库表所有权与字段索引》。

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
| MODEL-API-01 | 模型列表 | HTTP | GET | /api/v1/models |
| MODEL-API-02 | 新增模型 | HTTP | POST | /api/v1/models |
| MODEL-API-03 | 模型详情 | HTTP | GET | /api/v1/models/{model_id} |
| MODEL-API-04 | 编辑模型 | HTTP | PUT | /api/v1/models/{model_id} |
| MODEL-API-05 | 模型连通性测试 | HTTP | POST | /api/v1/models/{model_id}/test |
| MODEL-LIB-01 | 模型流式调用 | Library | async def stream_model(ctx: TrustedExecutionContext, model_id: UUID, messages: list[Message], options: ModelCallOptions) -> AsyncIterator[ModelEvent] |  |

#### MODEL-API-01: 模型列表

**入口类型**：HTTP

**契约**：`GET /api/v1/models`

**认证/授权**：None

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| page | integer | N | 页码，从 1 开始；默认 1 |
| page_size | integer | N | 每页条数；默认 20，最大 100 |
| keyword | string | N | name/key/model_name |
| enabled | boolean | N | 状态 |

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| items | array<ModelSummary> | 不返回 api_key_secret_ref 原值 |
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
Builder/Admin 鉴权 → tenant scoped 查询 → Secret 字段只返回 configured=true。
```

#### MODEL-API-02: 新增模型

**入口类型**：HTTP

**契约**：`POST /api/v1/models`

**认证/授权**：Builder/Admin

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | Y | 配置名 |
| key | string | Y | 稳定 Key |
| base_url | string | Y | OpenAI-compatible URL |
| model_name | string | Y | 模型名 |
| api_key | string | Y | 仅请求中出现，写 Secret Provider |
| default_parameters | object | N | 受控默认参数 |
| request_timeout_ms | integer | N | 默认 60000 |
| enabled | boolean | N | 默认 true |

**请求示例**

```json
{
  "name": "<name>",
  "key": "<key>",
  "base_url": "<base_url>",
  "model_name": "<model_name>",
  "api_key": "<api_key>",
  "default_parameters": {},
  "request_timeout_ms": 1,
  "enabled": true
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | ModelConfig ID |
| key | string | 稳定 Key |
| revision | integer | 1 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "<id>",
    "key": "<key>",
    "revision": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| MODEL_KEY_EXISTS | key 重复 | 409 |
| MODEL_PROTOCOL_UNSUPPORTED | V1 仅 OpenAI-Compatible | 400 |
| SECRET_WRITE_FAILED | Secret Provider 写入失败 | 502 |

**处理逻辑**

```text
校验 URL/参数 → SecretProvider.put(api_key) → INSERT model_config(secret_ref) → audit；若 DB 失败补偿删除刚写 Secret。
```

#### MODEL-API-03: 模型详情

**入口类型**：HTTP

**契约**：`GET /api/v1/models/{model_id}`

**认证/授权**：None

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | ID |
| name | string | 名称 |
| key | string | Key |
| protocol | string | OPENAI_COMPATIBLE |
| base_url | string | Base URL |
| model_name | string | 模型 |
| default_parameters | object | 默认参数 |
| request_timeout_ms | integer | 超时 |
| api_key_configured | boolean | 是否配置 Secret |
| enabled | boolean | 状态 |
| revision | integer | revision |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "<id>",
    "name": "<name>",
    "key": "<key>",
    "protocol": "<protocol>",
    "base_url": "<base_url>",
    "model_name": "<model_name>",
    "default_parameters": {},
    "request_timeout_ms": 1,
    "api_key_configured": true,
    "enabled": true,
    "revision": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| MODEL_NOT_FOUND | 模型不存在 | 404 |

**处理逻辑**

```text
tenant scoped 查询；永不返回 Secret。
```

#### MODEL-API-04: 编辑模型

**入口类型**：HTTP

**契约**：`PUT /api/v1/models/{model_id}`

**认证/授权**：None

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | Y | 名称 |
| base_url | string | Y | URL |
| model_name | string | Y | 模型名 |
| api_key | string | N | 非空才替换 Secret |
| default_parameters | object | N | 参数 |
| request_timeout_ms | integer | Y | >0 |
| enabled | boolean | Y | 状态 |
| revision | integer | Y | 乐观锁 |

**请求示例**

```json
{
  "name": "<name>",
  "base_url": "<base_url>",
  "model_name": "<model_name>",
  "api_key": "<api_key>",
  "default_parameters": {},
  "request_timeout_ms": 1,
  "enabled": true,
  "revision": 1
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | ID |
| revision | integer | 新 revision |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "<id>",
    "revision": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| MODEL_NOT_FOUND | 不存在 | 404 |
| REVISION_CONFLICT | 并发冲突 | 409 |

**处理逻辑**

```text
校验 revision → 可选 rotate Secret → UPDATE config/revision → audit；新请求使用新 revision，运行中调用按其开始时 resolve 语义。
```

#### MODEL-API-05: 模型连通性测试

**入口类型**：HTTP

**契约**：`POST /api/v1/models/{model_id}/test`

**认证/授权**：Builder/Admin

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| prompt | string | N | 默认短探针，不存储 |
| timeout_ms | integer | N | 不得超过模型配置 hard limit |

**请求示例**

```json
{
  "prompt": "<prompt>",
  "timeout_ms": 1
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| ok | boolean | 测试结果 |
| latency_ms | integer | 耗时 |
| provider_request_id | string | 可选下游 request id |
| model_name | string | 实际模型 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "ok": true,
    "latency_ms": 1,
    "provider_request_id": "<provider_request_id>",
    "model_name": "<model_name>"
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| MODEL_DISABLED | 已禁用 | 409 |
| MODEL_AUTH_FAILED | 认证失败 | 502 |
| MODEL_TIMEOUT | 超时 | 504 |
| MODEL_PROTOCOL_ERROR | 响应不兼容 | 502 |

**处理逻辑**

```text
读取当前模型 → SecretProvider.resolve → OpenAI-compatible client 发最小请求 → 解析 → 返回脱敏结果；不写 Conversation/Execution。
```

#### MODEL-LIB-01: 模型流式调用

**入口类型**：Library

**函数签名**

```python
async def stream_model(ctx: TrustedExecutionContext, model_id: UUID, messages: list[Message], options: ModelCallOptions) -> AsyncIterator[ModelEvent]
```

**入参**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| ctx | TrustedExecutionContext | Y | 可信上下文 |
| model_id | uuid | Y | 模型 |
| messages | array<Message> | Y | 上下文 |
| options | ModelCallOptions | N | 受控覆盖项 |

**返回**

| 字段 | 类型 | 说明 |
|---|---|---|
| events | AsyncIterator<ModelEvent> | delta/tool_call/usage/completed |

**异常/错误**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| MODEL_DISABLED | 禁用 | 409 |
| MODEL_TIMEOUT | deadline | 504 |

**处理逻辑**

```text
resolve ModelConfig → resolve Secret → 合并允许覆盖的 options → 流式请求 → 统一 event/error/usage telemetry。
```

### 3.5 质量实现方案

#### 3.5.1 性能与容量

| 热点路径 | 负载量级 | 潜在瓶颈 | 实现方案 | 目标值 |
|---|---|---|---|---|
| Model stream | 用户热路径 | 网络/首 token 延迟 | 真流式、连接池、合理 timeout；不缓冲完整响应 | 以 provider 实测 |
| Model list | 低频 | 无 | key/status 索引 | 低频 |

#### 3.5.2 可靠性

所有外部调用有 deadline；重试有界且只对可安全重试错误；业务权威状态外置；进程崩溃后能恢复或明确失败。

#### 3.5.3 安全性

Base URL 需要 SSRF allowlist/部署策略；extra_headers 中 Secret 必须以 ref 表示；Prompt/response 日志按隐私策略采样/脱敏。

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
| RISK-MODEL-01 | 自定义 base_url SSRF | 高 | URL allowlist/DNS/IP policy + network egress policy | S-MODEL-01 |
| RISK-MODEL-02 | 模型配置修改影响新请求 | 中 | revision/audit + test before enable | S-MODEL-04 |

## 6. 需求追溯矩阵

| 功能ID | 接口ID | 验证场景 | 测试层级 | 状态 |
|---|---|---|---|---|
| FEAT-MODEL-01 | MODEL-API-01, MODEL-API-02 | S-MODEL-01 | E2E/integration | 待实现/评审 |
| FEAT-MODEL-02 | MODEL-API-02, MODEL-API-03 | 见 §2.5 | E2E/integration | 待实现/评审 |
| FEAT-MODEL-03 | MODEL-API-03, MODEL-API-04 | S-MODEL-02, E-MODEL-01 | E2E/integration | 待实现/评审 |
| FEAT-MODEL-04 | MODEL-API-04, MODEL-API-05 | S-MODEL-03 | E2E/integration | 待实现/评审 |
| FEAT-MODEL-05 | MODEL-API-05, MODEL-LIB-01 | S-MODEL-04, E-MODEL-02 | E2E/integration | 待实现/评审 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| DESIGN/MODEL#RULE-MODEL-01 | design-baseline | 约束实现与验收 | §2.5 RULE-MODEL-01 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MODEL#RULE-MODEL-02 | design-baseline | 约束实现与验收 | §2.5 RULE-MODEL-02 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MODEL#RULE-MODEL-03 | design-baseline | 约束实现与验收 | §2.5 RULE-MODEL-03 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MODEL#RULE-MODEL-04 | design-baseline | 约束实现与验收 | §2.5 RULE-MODEL-04 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/MODEL#RULE-MODEL-05 | design-baseline | 约束实现与验收 | §2.5 RULE-MODEL-05 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |

## 附录 A：接口与 DB 落码检查清单

- [ ] 每个 HTTP Endpoint 已有 DTO、鉴权、错误码、处理逻辑、测试。
- [ ] 每个 Library/CLI 接口有稳定签名和异常语义。
- [ ] 每张表字段、类型、NULL、默认值、唯一约束、索引、软删除策略已落实迁移。
- [ ] Request/Response 字段不接受客户端传入可信 tenant/actor/credential。
- [ ] 列表接口使用服务端分页，避免无界返回。
- [ ] 修改类接口有 revision/冲突语义；高风险/副作用路径满足幂等和审计。
- [ ] 仓库 Spec Context 已 refresh/catalog/bind 且 required Rules 映射到 S/E/B verifier。
