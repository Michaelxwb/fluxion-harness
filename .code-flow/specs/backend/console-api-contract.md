---
id: fluxion-console-api-contract
description: Fluxion Console/Control Plane 后端统一响应、错误码、请求上下文、结构化日志与审计边界
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-fluxion-console-api-001
    type: manual
    config:
      checklist: 检查所有 Console API 是否走统一响应/异常中间件，所有请求是否具备 request_id/trace_id，日志字段、脱敏和 Audit 边界是否符合规范。
      owner: project-owner
---

# Console API、响应与日志统一规范

## Rules

- [RULE-fluxion-console-api-001] Console/Control Plane 的 HTTP 响应、业务错误、请求上下文和日志必须通过统一基础设施封装，业务 Handler/Service 不得各自拼装响应或自行定义日志字段。

## Guidance

### 1. 统一响应结构

所有 JSON API 使用：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "request_id": "req_xxx"
}
```

约束：

- `code` 为整数；`0` 表示成功，非 `0` 为业务/平台错误码。
- `message` 为面向调用方的稳定说明，不直接暴露异常堆栈、SQL、Secret 或内部实现。
- `data` 成功时为具体 Payload；无返回值时为 `null`。
- `request_id` 必须与响应头 `X-Request-ID` 一致。
- HTTP Status 表达 HTTP 语义，业务 `code` 表达平台语义，二者不能互相替代。
- Handler 禁止手写 `{code, message, data, request_id}` 字面量，统一通过 `success()` / `failure()` 或统一 Response Factory。
- FastAPI/Pydantic Response Model 必须从共享响应 Contract 派生。

分页统一放在 `data`：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "page": 1,
    "page_size": 20,
    "total": 0
  },
  "request_id": "req_xxx"
}
```

### 2. 统一异常处理

- Domain/Application 层抛出类型化 `FluxionError` / `BusinessError`，不得直接返回 HTTP Response。
- API 层通过全局 Exception Handler 将异常映射为 HTTP Status + Business Code。
- Validation Error、Authentication、Authorization、NotFound、Conflict、DependencyUnavailable、InternalError 使用统一映射。
- 未识别异常统一返回内部错误码，日志保留堆栈，响应不暴露堆栈。

### 3. 错误码命名空间

V1 固定：

```text
0       成功
30xxx   通用请求/校验
31xxx   Resource
32xxx   Binding
33xxx   Publish/Version
34xxx   Identity/Bind/Channel
35xxx   Auth/AuthZ
36xxx   Workflow/Capability 引用
39xxx   Console 内部/依赖错误
```

错误码必须集中定义，禁止 Handler 内硬编码。

### 4. Request Context

入口中间件必须建立：

```text
request_id
trace_id
tenant_id
actor_id
route
method
client_ip
user_agent
```

- 若客户端 `X-Request-ID` 合法则沿用，否则生成新的 request_id。
- `X-Request-ID` 必须回写响应头。
- request_id 必须进入 Log、Audit、Trace 和 API Response。
- tenant_id / actor_id 必须来自可信认证上下文，禁止直接信任普通请求 Body。

### 5. 统一结构化日志

V1 使用 Python 标准 logging + `structlog` JSON Renderer，日志输出 stdout/stderr，由部署平台采集。

每条请求完成日志至少包含：

```text
timestamp
level
service
environment
event
request_id
trace_id
tenant_id
actor_id
method
route
status_code
biz_code
latency_ms
```

资源操作按需增加：

```text
resource_type
resource_id
resource_version
binding_id
publish_id
execution_id
channel_type
```

异常日志增加：

```text
error_type
error_code
stack
```

要求：

- 正常请求不打印完整 request/response body。
- ERROR 必须保留 stack；WARN/ERROR 必须包含 error_code。
- 高频健康检查默认不输出 INFO access log 或进行采样。
- 禁止业务代码使用 `print()`。
- Service/Repository 使用封装 Logger 获取 Context，不重复手传 request_id。

### 6. 脱敏

统一 Redaction Processor 至少屏蔽：

```text
password
token
access_token
refresh_token
authorization
cookie
secret
client_secret
bind_code
credential
api_key
```

- Key 匹配大小写不敏感。
- Bind Code 完整明文只允许在“创建 code 的一次响应”中出现；不得进入日志、Audit、Trace 和数据库。
- Credential/Secret 只记录引用 ID 或状态。

### 7. Log 与 Audit 的边界

日志不是审计事实源。

必须进入 AuditLog 的操作：

- Publish / Rollback / Deprecate
- Resource/Binding 权限变化
- Policy 变化
- Bind 成功/失败的安全事件
- CredentialRef 变更
- 管理员高风险操作

AuditLog 使用独立 Schema 和持久化，不依赖日志采集是否成功。

## Patterns

- `RequestContextMiddleware`：生成/恢复请求上下文。
- `ApiResponse[T]`、`PageData[T]`：统一响应类型。
- `ExceptionMapper`：异常到 HTTP + Business Code。
- `get_logger()`：返回已绑定 Request Context 的结构化 Logger。
- `RedactionProcessor`：日志字段级脱敏。
- `AuditService`：高影响操作独立持久化。

## Avoid

- 禁止 Handler 手写统一响应字典。
- 禁止业务层直接依赖 FastAPI Response/HTTPException。
- 禁止日志记录完整 Authorization/Cookie/Bind Code/Credential。
- 禁止把 Audit 只写日志。
- 禁止异常被 `except Exception: return failure(...)` 静默吞掉。
