<!-- V1.11 module-split metadata -->
> **文档分档**：`Full`（模板 `design-full.md`）  
> **分档理由**：ProjectPlatform、用户凭据、Secret/Auth Resolver  
> **文档边界**：本文件是该模块唯一详细设计事实源；DB Owner 与接口 Owner 必须落在本模块，不允许在其他模块重复定义。  
> **拆分原则**：仅按可独立评审/编码/验收的一级模块拆分；不按单表/单接口继续碎片化。

# Auth 与项目平台 模块需求与设计一体化文档

> **文档编号**: MOD-AUTH-V1.11 模块分档拆分版
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
| 模块名称 | Auth 与项目平台 |
| 模块ID | MOD-AUTH |
| 所属系统/产品线 | Fluxion 通用智能服务执行框架 |
| 需求类型 | 中大型功能 / 架构演进 |
| 业务背景 | ProjectPlatform 是 MSS/CRM/ERP 业务平台对象，承担用户认证模板；ProjectIntegration 是代码装配机制，两者已正式拆分。 |
| 核心目标 | 设计 ProjectPlatform、User×ProjectPlatform 唯一 Credential、Secret/AuthProvider resolve、全部管理/验证接口。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|---|---|
| 目标用户 | Admin/Builder、Capability Runtime、Integration Developer |
| 当前问题 | 若平台对象与代码 Integration 混用，认证、部署和 Console CRUD 混乱；若 Skill 自带账号则多用户权限无法复用。 |
| 业务影响 | 凭据泄露、共享账号越权、每个 Skill 重复登录、项目扩展不可维护。 |
| 预期价值 | 用户认证统一外置且按真实用户复用；Platform Service 能透明获得当前用户 AuthContext。 |

### 2.3 功能方案

#### 2.3.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-AUTH-01 | ProjectPlatform CRUD | 业务平台和 auth schema。 | P0 | Console 项目平台 |
| FEAT-AUTH-02 | User Credential | User×Platform 唯一认证。 | P0 | 用户详情 |
| FEAT-AUTH-03 | Secret Storage | 敏感值外部 Secret Provider。 | P0 | 安全 |
| FEAT-AUTH-04 | Credential Verify | AuthProvider 真实验证。 | P0 | 可用性 |
| FEAT-AUTH-05 | Runtime Resolve | Platform Service request-scoped AuthContext。 | P0 | Capability |

### 2.4 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | ProjectPlatform、UserProjectCredential、auth_schema、Secret ref、AuthProvider resolve/verify。 |
| 非范围（Out of Scope） | ProjectIntegration manifest、Channel identity、AgentAccessGrant、HTTP/MCP 共享认证（归 Capability Implementation）。 |
| 前置假设 | 上游总设、公共 DB/API 规范、外部基础设施可用 |
| 有意妥协 / 技术债 | 无；未来扩展必须有真实旅程驱动 |

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|---|---|---|---|
| RULE-AUTH-01 | 分层 | ProjectPlatform 与 ProjectIntegration 不共表/不共 CRUD。 | S-AUTH-01 |
| RULE-AUTH-02 | 唯一 | 同一 User×ProjectPlatform V1 最多一套 Credential。 | S-AUTH-02 |
| RULE-AUTH-03 | Secret | DB/日志/Skill 不保存明文 Secret。 | S-AUTH-03 |
| RULE-AUTH-04 | 类型 | 只有 PLATFORM_SERVICE 使用用户 ProjectPlatform Credential。 | S-AUTH-04 |
| RULE-AUTH-05 | 动态 | Execution 恢复时使用 current Credential/权限，不冻结旧认证。 | S-AUTH-05 |

#### 2.5.2 功能验收场景

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 归属 | 前置条件 | 操作步骤 | 预期结果 |
|---|---|---|---|---|---|---|---|---|
| S-AUTH-05 | FEAT-AUTH-04 | P1 | integration | 恢复使用当前凭据 | 本模块 | Execution 等待期间凭据被更换 | Worker 恢复执行 | 使用 current Credential，不使用冻结旧认证 |
| S-AUTH-01 | FEAT-AUTH-01 | P0 | E2E | Console→API→PG | 本模块 | Admin | 创建 MSS 平台/auth schema | 项目平台列表可见 |
| S-AUTH-02 | FEAT-AUTH-02 | P0 | E2E | User tab→SecretProvider→PG | 本模块 | 用户/平台存在 | 保存账号 | DB 只有 credential_ref，Secret Provider 有值 |
| S-AUTH-03 | FEAT-AUTH-04 | P0 | E2E | Credential→AuthProvider→external | 本模块 | 已保存 credential | Verify | status VALID/INVALID 可追踪 |
| S-AUTH-04 | FEAT-AUTH-05 | P0 | E2E | Capability→AuthResolver | 后置 → 模块 07 | Platform Service 调用 | invoke | 当前用户 AuthContext 注入下游 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 归属 | 触发条件 | 系统行为 | 用户感知 |
|---|---|---|---|---|---|---|---|
| E-AUTH-01 | FEAT-AUTH-02 | integration | Unique constraint | 本模块 | 重复 user×platform 保存 | 更新同一记录/revision | 不创建第二套账号 |
| E-AUTH-02 | FEAT-AUTH-05 | E2E | Auth resolve | 本模块 | Credential 删除/过期 | fail closed | 不降级共享账号 |

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
| 平台对象 | ProjectPlatform DB 产品对象 | 复用 ProjectIntegration | 运行/部署与业务认证职责不同 | 难 |
| Credential | User×Platform 唯一 | User×Agent×Protocol | 减少重复且符合业务身份 | 中 |
| Secret | 外部 SecretProvider ref | DB 加密明文 | 缩小数据泄露面 | 中 |

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
  Admin["Console"] --> PP[(project_platform)]
  Admin --> Cred["Credential API"] --> Secret["External Secret Provider"]
  Cred --> UC[(user_project_credential)]
  Cap["Platform Service Capability"] --> Resolver["AuthResolver"]
  Resolver --> UC
  Resolver --> Secret
  Resolver --> Provider["AuthProvider"] --> Biz["External Project Platform"]
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
| Secret Provider | Secret SoT | Port | 强安全边界 | 不可用 fail closed |
| AuthProvider | 认证适配 | SPI | deadline | 验证/解析失败 |
| External business platform | 最终认证/权限 | 项目协议 | 外部 | 由其最终判定业务权限 |

### 3.3 数据设计

#### 3.3.1 表清单

| 表名 | 职责 | 所有权 |
|---|---|---|
| project_platform | MSS/CRM/ERP 等业务平台产品对象；承载用户认证模板并被 PLATFORM_SERVICE Capability 引用。与 ProjectIntegration 分离。 | Auth 与项目平台 |
| user_project_credential | 同一 PlatformUser × ProjectPlatform 的唯一用户认证投影；敏感值在 Secret Provider。 | Auth 与项目平台 |

#### 表 `project_platform`

**职责**：MSS/CRM/ERP 等业务平台产品对象；承载用户认证模板并被 PLATFORM_SERVICE Capability 引用。与 ProjectIntegration 分离。

| 字段名 | 类型 | 可空 | 默认值 | 索引 | 说明 |
|---|---|---|---|---|---|
| tenant_id | UUID | N |  | IDX | 租户 |
| name | VARCHAR(256) | N |  | IDX | 平台名称 |
| key | VARCHAR(128) | N |  | UK | 稳定标识 |
| description | TEXT | Y |  |  | 说明 |
| auth_type | VARCHAR(64) | N | UNCONFIGURED |  | UNCONFIGURED 或已注册 AuthProvider 类型 |
| auth_schema | JSONB | N | {} |  | 用户需填写字段的 JSON Schema；不含 Secret 值 |
| enabled | BOOLEAN | N | TRUE | IDX | 新解析是否允许 |
| revision | BIGINT | N | 1 |  | direct-effect revision |
| id | UUID | N | gen_random_uuid() | PK | 主键 |
| is_deleted | BOOLEAN | N | FALSE | IDX | 软删除标记；默认查询必须过滤 FALSE |
| create_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 创建时间 |
| update_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 更新时间 |

**约束**

- UNIQUE (tenant_id,key) WHERE is_deleted=false
- auth_type=UNCONFIGURED 当且仅当 configured=false（派生 DTO）；此时 auth_schema={}，不能保存/验证凭据或运行 PLATFORM_SERVICE。其他类型必须已注册且模板校验通过。

**索引设计**

| 索引名 | 类型 | 字段 | 使用场景 |
|---|---|---|---|
| uk_project_platform_key | UNIQUE | tenant_id,key | 按 key 引用 |
| idx_project_platform_status | BTREE | tenant_id,enabled,is_deleted | 控制面列表 |

#### 表 `user_project_credential`

**职责**：同一 PlatformUser × ProjectPlatform 的唯一用户认证投影；敏感值在 Secret Provider。

| 字段名 | 类型 | 可空 | 默认值 | 索引 | 说明 |
|---|---|---|---|---|---|
| tenant_id | UUID | N |  | IDX | 租户 |
| platform_user_id | UUID | N |  | FK,IDX | 用户 |
| project_platform_id | UUID | N |  | FK,IDX | 项目平台 |
| credential_ref | VARCHAR(512) | N |  |  | Secret Provider 引用 |
| external_session_ref | VARCHAR(512) | Y |  |  | 可选外部 Session 引用 |
| credential_expires_at | TIMESTAMPTZ | Y |  | IDX | 长期凭据有效期；空表示由 Provider 验证 |
| session_expires_at | TIMESTAMPTZ | Y |  | IDX | 短期 Session 有效期，过期触发刷新 |
| session_generation | BIGINT | N | 0 |  | 刷新/撤销的 CAS 代次 |
| refresh_owner | UUID | Y |  |  | 同凭据刷新租约 |
| refresh_lease_expires_at | TIMESTAMPTZ | Y |  |  | 刷新到期；其他请求有限退避等待 |
| status | VARCHAR(32) | N | UNVERIFIED | IDX | 长期凭据 UNVERIFIED/VALID/INVALID/EXPIRED；Session 过期不改本状态 |
| verified_at | TIMESTAMPTZ | Y |  |  | 最近验证时间 |
| metadata | JSONB | N | {} |  | 非敏感元信息 |
| revision | BIGINT | N | 1 |  | 乐观并发 |
| id | UUID | N | gen_random_uuid() | PK | 主键 |
| is_deleted | BOOLEAN | N | FALSE | IDX | 软删除标记；默认查询必须过滤 FALSE |
| create_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 创建时间 |
| update_time | TIMESTAMPTZ | N | CURRENT_TIMESTAMP |  | 更新时间 |

**约束**

- UNIQUE (tenant_id,platform_user_id,project_platform_id) WHERE is_deleted=false
- 明文用户名/密码/token 不得进入 metadata/log

**索引设计**

| 索引名 | 类型 | 字段 | 使用场景 |
|---|---|---|---|
| uk_user_project_credential | UNIQUE(partial) | tenant_id,platform_user_id,project_platform_id | WHERE is_deleted=false |
| idx_user_project_credential_expiry | BTREE | tenant_id,status,credential_expires_at,is_deleted | 过期扫描/验证 |

#### 3.3.2 ER 图

```mermaid
erDiagram
    PROJECT_PLATFORM {
      UUID tenant_id FK
      VARCHAR_256_ name
      VARCHAR_128_ key
      TEXT description
      VARCHAR_64_ auth_type
      JSONB auth_schema
    }
    USER_PROJECT_CREDENTIAL {
      UUID tenant_id FK
      UUID platform_user_id FK
      UUID project_platform_id FK
      VARCHAR_512_ credential_ref
      VARCHAR_512_ external_session_ref
      TIMESTAMPTZ credential_expires_at
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
| PLAT-API-01 | 项目平台列表 | HTTP | GET | /api/v1/project-platforms |
| PLAT-API-02 | 新增项目平台 | HTTP | POST | /api/v1/project-platforms |
| PLAT-API-03 | 项目平台详情 | HTTP | GET | /api/v1/project-platforms/{platform_id} |
| PLAT-API-04 | 编辑项目平台 | HTTP | PUT | /api/v1/project-platforms/{platform_id} |
| PLAT-API-05 | 验证认证 Schema | HTTP | POST | /api/v1/project-platforms/{platform_id}/validate-auth-schema |
| CRED-API-01 | 用户平台认证列表 | HTTP | GET | /api/v1/users/{user_id}/platform-credentials |
| CRED-API-02 | 保存用户平台认证 | HTTP | PUT | /api/v1/users/{user_id}/platform-credentials/{platform_id} |
| CRED-API-03 | 验证用户平台认证 | HTTP | POST | /api/v1/users/{user_id}/platform-credentials/{platform_id}/verify |
| CRED-API-04 | 删除用户平台认证 | HTTP | DELETE | /api/v1/users/{user_id}/platform-credentials/{platform_id} |
| AUTH-LIB-01 | 运行时用户认证解析 | Library | async def resolve_user_platform_auth(ctx: TrustedExecutionContext, project_platform_id: UUID) -> AuthContext |  |

#### PLAT-API-01: 项目平台列表

**入口类型**：HTTP

**契约**：`GET /api/v1/project-platforms`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；Builder + Admin；Builder 仅安全只读 DTO（ADR-021）

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| page | integer | N | 页码，从 1 开始；默认 1 |
| page_size | integer | N | 每页条数；默认 20，最大 100 |
| keyword | string | N | name/key |
| enabled | boolean | N | 状态 |

**请求体**：无。

**安全投影**：Builder 可读 id/key/name/enabled/revision；模型还可读 model_name/protocol，平台还可读 auth_type/configured。内部地址、额外认证头、Secret ref/值、用户凭据仅 Admin 管理 DTO 可见；敏感写/测试接口仅 Admin。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| items | array<ProjectPlatformSummary> | 含 capability_count |
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
Builder/Admin tenant scoped 查询 + capability implementation 聚合。
```

#### PLAT-API-02: 新增项目平台

**入口类型**：HTTP

**契约**：`POST /api/v1/project-platforms`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；仅 Admin（ADR-021）

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | Y | 名称 |
| key | string | Y | 稳定 Key |
| description | string | N | 说明 |
| auth_type | string/null | N | 缺省或 null 均规范化为 UNCONFIGURED；后续 Admin 配置认证模板 |
| auth_schema | object | N | UNCONFIGURED 只允许 {}；已配置时必须符合注册 Provider 模板 |
| enabled | boolean | N | 默认 true |

**请求示例**

```json
{
  "name": "<name>",
  "key": "<key>",
  "description": "<description>",
  "auth_type": null,
  "auth_schema": {},
  "enabled": true
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 平台 ID |
| key | string | Key |
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
| PROJECT_PLATFORM_KEY_EXISTS | key 重复 | 409 |
| AUTH_SCHEMA_INVALID | Schema 非法或包含不允许的 secret 默认值 | 400 |

**处理逻辑**

```text
将缺省/null auth_type 规范化为 UNCONFIGURED；此时 auth_schema 必须 {}。其他类型校验已注册 Provider 的模板并保存；返回 id/key/revision/configured=(auth_type!=UNCONFIGURED)。enabled 是资产开关，不代表已配置认证。UNCONFIGURED 时保存 Credential、认证验证、PLATFORM_SERVICE 测试/运行均 AUTH_PLATFORM_UNCONFIGURED（409），不能隐式调用 USERNAME_PASSWORD。
```

#### PLAT-API-03: 项目平台详情

**入口类型**：HTTP

**契约**：`GET /api/v1/project-platforms/{platform_id}`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；Builder + Admin；Builder 仅安全只读 DTO（ADR-021）

**请求体**：无。

**安全投影**：Builder 可读 id/key/name/enabled/revision；模型还可读 model_name/protocol，平台还可读 auth_type/configured。内部地址、额外认证头、Secret ref/值、用户凭据仅 Admin 管理 DTO 可见；敏感写/测试接口仅 Admin。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | ID |
| name | string | 名称 |
| key | string | Key |
| auth_type | string | 类型 |
| auth_schema | object | 认证字段定义 |
| enabled | boolean | 状态 |
| revision | integer | revision |
| capability_count | integer | Platform Service 数 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "<id>",
    "name": "<name>",
    "key": "<key>",
    "auth_type": "<auth_type>",
    "auth_schema": {},
    "enabled": true,
    "revision": 1,
    "capability_count": 1
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| PROJECT_PLATFORM_NOT_FOUND | 不存在 | 404 |

**处理逻辑**

```text
读取平台 + 聚合能力数；不返回任何用户 Credential。
```

#### PLAT-API-04: 编辑项目平台

**入口类型**：HTTP

**契约**：`PUT /api/v1/project-platforms/{platform_id}`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；仅 Admin（ADR-021）

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | Y | 名称 |
| description | string | N | 说明 |
| auth_type | string | Y | 认证类型 |
| auth_schema | object | Y | 认证字段定义 |
| enabled | boolean | Y | 状态 |
| revision | integer | Y | 乐观锁 |

**请求示例**

```json
{
  "name": "<name>",
  "description": "<description>",
  "auth_type": null,
  "auth_schema": {},
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
| PROJECT_PLATFORM_NOT_FOUND | 不存在 | 404 |
| REVISION_CONFLICT | 冲突 | 409 |
| AUTH_SCHEMA_INCOMPATIBLE | 认证 schema 变更与现有 credential 不兼容；需要迁移策略 | 409 |

**处理逻辑**

```text
读取 current → 校验 revision/schema → 若 schema 字段破坏性变更，要求显式 force/migration（V1 默认拒绝）→ UPDATE → audit。
```

#### PLAT-API-05: 验证认证 Schema

**入口类型**：HTTP

**契约**：`POST /api/v1/project-platforms/{platform_id}/validate-auth-schema`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；仅 Admin（ADR-021）

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| auth_schema | object | Y | 待验证 JSON Schema |

**请求示例**

```json
{
  "auth_schema": {}
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| valid | boolean | 是否合法 |
| errors | array<object> | 路径/错误 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "valid": true,
    "errors": []
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| AUTH_SCHEMA_INVALID | Schema 不合法 | 400 |

**处理逻辑**

```text
纯校验，不持久化；检查支持字段类型、required、禁止明文 default secret。
```

#### CRED-API-01: 用户平台认证列表

**入口类型**：HTTP

**契约**：`GET /api/v1/users/{user_id}/platform-credentials`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；仅 Admin（ADR-021）

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| items | array<UserPlatformCredentialView> | platform/status/credential_expires_at/verified_at/configured_fields_masked |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": []
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| USER_NOT_FOUND | 用户不存在 | 404 |

**处理逻辑**

```text
Admin/本人受控访问 → JOIN platform + credential；Secret 只返回字段是否已配置，绝不返回明文。
```

#### CRED-API-02: 保存用户平台认证

**入口类型**：HTTP

**契约**：`PUT /api/v1/users/{user_id}/platform-credentials/{platform_id}`

**认证/授权**：Admin；或未来本人自助端点需独立授权，Console V1 Admin 管理

**请求体**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| values | object | Y | 按 project_platform.auth_schema 填写的实际值；敏感值仅本次请求 |
| revision | integer | N | 已有记录时乐观锁 |

**请求示例**

```json
{
  "values": {},
  "revision": 1
}
```

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| status | string | UNVERIFIED/VALID |
| revision | integer | 新 revision |
| credential_configured | boolean | 已保存 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "<status>",
    "revision": 1,
    "credential_configured": true
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| PROJECT_PLATFORM_NOT_FOUND | 平台不存在/禁用 | 404 |
| CREDENTIAL_SCHEMA_INVALID | 字段不满足 auth_schema | 400 |
| REVISION_CONFLICT | 并发冲突 | 409 |
| SECRET_WRITE_FAILED | Secret 写入失败 | 502 |

**处理逻辑**

```text
解析平台 auth_schema → 校验 values → SecretProvider.put/rotate → transaction upsert user_project_credential(ref only) → audit（只记录字段名/状态）→ 返回。
```

#### CRED-API-03: 验证用户平台认证

**入口类型**：HTTP

**契约**：`POST /api/v1/users/{user_id}/platform-credentials/{platform_id}/verify`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；仅 Admin（ADR-021）

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| valid | boolean | 验证结果 |
| status | string | VALID/INVALID/EXPIRED |
| verified_at | datetime | 验证时间 |
| message | string | 脱敏结果 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "valid": true,
    "status": "<status>",
    "verified_at": "2026-09-11 10:00:00",
    "message": "<message>"
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| CREDENTIAL_NOT_CONFIGURED | 未配置 | 404 |
| AUTH_PROVIDER_FAILED | 认证 Provider/外部系统不可用 | 502 |

**处理逻辑**

```text
resolve Secret → AuthProvider.verify → 更新 status/verified_at/expiry → audit；失败不返回外部原始敏感响应。
```

#### CRED-API-04: 删除用户平台认证

**入口类型**：HTTP

**契约**：`DELETE /api/v1/users/{user_id}/platform-credentials/{platform_id}`

**认证/授权**：登录会话（中间件解析，RULE-API-02）；仅 Admin（ADR-021）

**请求体**：无。

**响应 data**

| 字段 | 类型 | 说明 |
|---|---|---|
| deleted | boolean | 是否删除/撤销 |

**响应示例**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "deleted": true
  },
  "request_id": "req_xxx"
}
```

**错误码**

| 错误码 | 场景 | HTTP 状态 |
|---|---|---|
| CREDENTIAL_NOT_CONFIGURED | 不存在 | 404 |

**处理逻辑**

```text
先在 DB 标记软删除/失效 → 触发 SecretProvider.delete；Secret 删除失败记录告警并重试清理，不恢复已撤销业务授权。
```

#### AUTH-LIB-01: 运行时用户认证解析

**契约**：`Library resolve_user_platform_auth(ctx, project_platform_id)`

**认证/授权**：可信 actor/tenant；当前 user/platform/Credential 有效

返回 request-scoped AuthContext，不含可泄露到日志/LLM 的值。

1. 检查 platform 已配置且 enabled；读取 current Credential。INVALID/EXPIRED 或 credential_expires_at<=now 拒绝 USER_PLATFORM_CREDENTIAL_INVALID；UNVERIFIED 先调用 Provider.verify，失败拒绝。仅 session_expires_at 到期不能使长期 Credential 失效。
2. Session 可用则 AuthProvider.materialize(ctx, credential_material, session)；SESSION_EXPIRED 转步骤 3，CREDENTIAL_INVALID 标记长期无效并拒绝。
3. 对该 tenant+credential 用 PG CAS 获取短刷新租约，不持数据库事务做网络调用。Provider.refresh_or_login(ctx, credential_material, previous_session) 支持 refresh 或重新登录，返回 SessionRef/session_expires_at；没有永久 refresh endpoint 的 Provider 走 login，不要求用户重填有效凭据。
4. 回写时比较 credential.revision/session_generation/refresh_owner 且当前未撤销，session_generation+1，释放刷新租约；轮换/撤销使 generation 增长，旧刷新结果不得覆盖。其他实例在调用 deadline 内有界退避等待，超时 AUTH_REFRESH_BUSY（503）；刷新进程崩溃后租约到期可恢复。
5. materialize 新 Session 后调用外部系统，仍失败按 AUTH_SESSION_UNAVAILABLE（502）分类，不无限刷新。Secret 只在宿主请求内存；grant 和安全状态仍动态。

**Provider SPI**：`verify(ctx, credential)->CredentialValidity`；`materialize(ctx, credential, session)->AuthContext`；`refresh_or_login(ctx, credential, previous_session)->SessionMaterial`；`invalidate(ctx, session)->None`。外部撤销返回 CREDENTIAL_INVALID；临时失败返回 RETRYABLE/AUTH_SESSION_UNAVAILABLE。每个方法有 deadline；invalidate 失败仅记录待清理，不能撤销本地已撤销事实。

**验证**：credential 有效而 session 到期实际刷新；两个 Worker 同时恢复只接受一个 generation；刷新期间 Credential 撤销/轮换，旧结果无法写回。

### 3.5 质量实现方案

#### 3.5.1 性能与容量

| 热点路径 | 负载量级 | 潜在瓶颈 | 实现方案 | 目标值 |
|---|---|---|---|---|
| Runtime auth resolve | 每 Platform Service 调用 | 频繁 Secret/Session 查询 | 短生命周期安全 cache/session ref；过期实时校验 | 不牺牲撤销语义 |
| 用户认证列表 | Console 低频 | JOIN | User×Platform unique/index | 低频 |

#### 3.5.2 可靠性

所有外部调用有 deadline；重试有界且只对可安全重试错误；业务权威状态外置；进程崩溃后能恢复或明确失败。

#### 3.5.3 安全性

Credential 明文仅存在请求内存与 SecretProvider 短 lease；日志/audit 只记录字段名、状态、ref，禁止 value。

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
| RISK-AUTH-01 | Secret rotate DB/Provider 两边不一致 | 高 | 写 Secret→事务更新 ref→补偿清理旧 ref | S-AUTH-02 |
| RISK-AUTH-02 | 无用户凭据时错误降级共享账号 | 高 | auth_mode typed + fail closed test | E-AUTH-02 |

## 6. 需求追溯矩阵

| 功能ID | 接口ID | 验证场景 | 测试层级 | 状态 |
|---|---|---|---|---|
| FEAT-AUTH-01 | PLAT-API-01, PLAT-API-02 | S-AUTH-01 | E2E/integration | 待实现/评审 |
| FEAT-AUTH-02 | PLAT-API-02, PLAT-API-03 | S-AUTH-02, E-AUTH-01 | E2E/integration | 待实现/评审 |
| FEAT-AUTH-03 | PLAT-API-03, PLAT-API-04 | 见 §2.5 | E2E/integration | 待实现/评审 |
| FEAT-AUTH-04 | PLAT-API-04, PLAT-API-05 | S-AUTH-03 | E2E/integration | 待实现/评审 |
| FEAT-AUTH-05 | PLAT-API-05, CRED-API-01 | S-AUTH-04, E-AUTH-02 | E2E/integration | 待实现/评审 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| DESIGN/AUTH#RULE-AUTH-01 | design-baseline | 约束实现与验收 | §2.5 RULE-AUTH-01 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/AUTH#RULE-AUTH-02 | design-baseline | 约束实现与验收 | §2.5 RULE-AUTH-02 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/AUTH#RULE-AUTH-03 | design-baseline | 约束实现与验收 | §2.5 RULE-AUTH-03 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/AUTH#RULE-AUTH-04 | design-baseline | 约束实现与验收 | §2.5 RULE-AUTH-04 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |
| DESIGN/AUTH#RULE-AUTH-05 | design-baseline | 约束实现与验收 | §2.5 RULE-AUTH-05 / §3 | S/E/B 场景 | applied；仓库 spec-context 待绑定 |

## 附录 A：接口与 DB 落码检查清单

- [ ] 每个 HTTP Endpoint 已有 DTO、鉴权、错误码、处理逻辑、测试。
- [ ] 每个 Library/CLI 接口有稳定签名和异常语义。
- [ ] 每张表字段、类型、NULL、默认值、唯一约束、索引、软删除策略已落实迁移。
- [ ] Request/Response 字段不接受客户端传入可信 tenant/actor/credential。
- [ ] 列表接口使用服务端分页，避免无界返回。
- [ ] 修改类接口有 revision/冲突语义；高风险/副作用路径满足幂等和审计。
- [ ] 仓库 Spec Context 已 refresh/catalog/bind 且 required Rules 映射到 S/E/B verifier。
