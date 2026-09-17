> **V1.3 详细设计覆盖说明**：本文件是此前总体/Playbook 基线快照。V1.3 已将项目平台认证修正为 PlatformAdapter + 外置 Session 模型，并允许一个 Agent 配置多个 IM 通道账号；同时删除业务 Console 中的系统/中间件状态监控。若本基线与 V1.3 详细设计冲突，以 V1.3 00~13 为准；后续总设/Playbook 应同步刷新。

> **用户范围补充口径**：Skill/MCP 不再使用 PUBLIC/PRIVATE 作为核心用户可见性语义；统一使用 `user_scope=ALL/SELECTED`。ALL 表示全部 Agent 授权用户，SELECTED 表示指定用户；两者都不能绕过 AgentAccessGrant 与 Agent Binding。

# MSS 智能服务交付平台 V2.0 总体设计说明书

> 文档类型：版本总体设计说明书
>
> 架构基线：Skill-first / Stateless Agent Runtime / Python Unified Backend
>
> 版本：V2.0
>
> 日期：2026-09-17

---

# 1. 引言

## 1.1 目的

本文用于定义 MSS 智能服务交付平台 V2.0 的总体架构、模块边界、关键系统流程、核心数据模型、技术选型与 DFX 设计约束，并作为后续模块详细设计、研发任务拆分、测试验收与架构评审的基线。

本次总体设计相对前序方案属于架构层面的重新收敛：不再以“接口原子化为 Capability + 平台 Workflow 编排”作为 Phase 1 主路径，而是在 `muad-openclaw` 已验证产品模型基础上，替换 OpenClaw Runtime 为自研无状态 Agent Runtime。

本总设主要面向：产品架构师、技术经理、模块负责人、后端/前端开发、测试、运维与安全评审人员。

## 1.2 适用范围和评审要求

### 1.2.1 适用范围

本设计覆盖 Phase 1：

- Console 控制面；
- Agent Runtime 执行面；
- IM Gateway 接入面；
- 企业微信 Bot；
- Agent、Skill、MCP、Model、用户、项目平台、凭据；
- Conversation 与 User Memory；
- Skill Runtime；
- RuntimeSnapshot；
- ToolRegistry；
- Egress Boundary；
- OpenTelemetry；
- PostgreSQL/Redis/Artifact Store（NFS-backed RWX PVC）/Secret Provider 外部依赖。

本设计明确**不在 Phase 1 实现** Worker Engine、完整 ServiceExecution、Workflow Designer、Capability Center、通用 Approval Center。

### 1.2.2 评审要求

总体设计评审重点验证：

1. 是否覆盖 Playbook Phase 1 黄金旅程；
2. Agent Runtime 是否真正无状态；
3. Console/Runtime/Gateway 边界是否清晰；
4. Skill-first 是否避免再次引入接口建模成本；
5. Credential 是否不会进入 Skill；
6. RuntimeSnapshot 是否保证一次 Run 版本确定性；
7. LangGraph 是否仅作为执行引擎而非平台领域模型；
8. Future Worker 是否可在不推翻现有 Skill 模型的前提下接入。

## 1.3 定义和缩写

| 名称 | 定义 |
|---|---|
| Agent | 面向用户的智能体定义，包括 Instructions、Model、Skill、MCP 与 Runtime 配置 |
| Agent Runtime | 无状态实时执行服务，负责 Agent reasoning、Skill/Tool/MCP 执行 |
| Console Platform | 控制面，负责 Agent/Skill/MCP/Model/User 等定义和治理 |
| IM Gateway | 统一渠道接入服务，V1 负责企业微信 Bot WebSocket |
| Skill | 使用 Python 开发、以 Artifact 发布的 SOP/业务方法代码 |
| Tool | Runtime 内部统一执行原语 |
| MCP | Model Context Protocol，标准外部工具接入方式 |
| RuntimeSnapshot | 一次 Run 开始时冻结的 Agent/Model/Skill/MCP/Policy 配置快照 |
| Canonical History | 不因 LLM Context 裁剪而改变的真实会话/执行事实 |
| Egress Boundary | Skill/Tool 访问业务系统前的统一可信出网边界 |
| User Memory | 跨 Conversation 的长期用户上下文，不代表实时业务事实 |
| PlatformUser | 平台统一用户身份 |
| ChannelIdentity | 外部渠道身份到 PlatformUser 的映射 |
| AgentAccessGrant | PlatformUser 使用 Agent 的授权关系 |

## 1.4 参考和引用

- 《MSS 智能服务交付平台——用户场景与 Playbook 旅程设计 V7》；
- 版本总体设计说明书模板 V3.2；
- `muad-openclaw` 已验证的用户、绑定、Skill、平台凭据、企业微信实践；
- `learn-agent` 中关于 Agent Harness、ToolRegistry、Skill Lazy Load、Context、Recovery 的工程实践；
- LangGraph 官方执行模型与 Checkpointer 设计。

## 1.5 总设任务书

| 目标项 | V2.0 要求 |
|---|---|
| 架构 | 三个核心后台 Image；Runtime 无状态；控制面与执行面解耦 |
| 性能 | Runtime 可水平扩展；Streaming 首包可观测；不因 Console 单副本限制用户执行 |
| 可靠性 | PostgreSQL 为权威状态；单 Runtime 崩溃不得导致 Conversation/Memory 丢失 |
| 安全 | Credential 不进入 Skill；Egress Allowlist；Tool/调用全审计 |
| 可测试性 | Skill 可本地 Mock；Agent Core 可脱离 HTTP 服务做单元测试 |
| 可调试性 | Run/Tool/Model/Egress 统一 trace_id/run_id |
| 可运维性 | 三 Image 独立健康检查、日志、指标与扩缩容 |
| 可扩展性 | Channel Adapter、Model Provider、Tool、MCP、Skill 均为可扩展端口 |
| 可复用性 | Agent Core、Contracts、Platform SDK 为共享 package |
| 迭代效率 | Skill 修改后重新打包上传即可供指定用户下一次 Run 使用 |

## 1.6 中台复用需求

平台应优先复用既有 MSS 业务微服务和项目平台能力，不复制客户、设备、扫描、策略、报告等业务逻辑。

内部业务平台通过 `PlatformClient/Egress Boundary` 访问；真实业务权限最终仍由被调用系统 RBAC/ACL 判定。

## 1.7 托管云需求

当前方案以私有化/内网 Kubernetes 部署为基线。若未来进入托管云，三核心服务保持无状态/可扩容设计，权威状态仍外置到托管数据库、缓存、对象存储与 Secret 服务。

---

# 2. 产品介绍

## 2.1 产品名称、型号、代号

产品名称：MSS 智能服务交付平台

架构版本：V2.0

内部代号：MUAD（沿用旧项目识别，最终产品命名可由产品侧确定）

## 2.2 对应需求文档

V2.0 以 Playbook V7 作为用户旅程需求基线，重点覆盖：

- 用户通过企业微信使用 Agent；
- Admin/Builder 在 Console 管理 Agent 相关资源；
- Skill 本地开发、上传、灰度、快速迭代；
- 用户授权与渠道身份绑定；
- 跨会话 User Memory；
- 无状态 Runtime；
- 运行审计和版本确定性。

## 2.3 关键应用场景

### 场景 1：策略检查

```text
用户：帮 A 客户做一次策略检查
→ Agent 识别 Skill
→ Skill 查询客户/设备
→ Agent 澄清范围
→ 用户确认
→ Skill 调业务平台
→ 返回检查结果与建议
```

### 场景 2：客户经营/交付数据分析

```text
用户提出分析目标
→ Skill 组合多个业务接口
→ Python 完成字段清洗、聚合、规则计算
→ Agent 生成业务解释
```

### 场景 3：Skill 快速修复

```text
发现数据字段变化
→ 开发者修改 Python
→ pytest
→ pack
→ 导入新 Artifact
→ user_scope=SELECTED 灰度
→ 验证
→ user_scope=ALL
```

### 场景 4：首次企微使用

```text
用户进入 Bot
→ /bind code
→ ChannelIdentity → PlatformUser
→ 校验 AgentAccessGrant
→ 开始会话
```

## 2.4 关键技术及解决途径

| 问题 | 技术方案 |
|---|---|
| OpenClaw Runtime 有状态 | 自研 Python Stateless Agent Runtime |
| Agent 执行框架 | LangGraph Library |
| SOP 开发成本 | Skill-first + Python SDK |
| 多 Skill Prompt 膨胀 | Catalog + Lazy Load |
| Tool/MCP 治理分散 | Unified ToolRegistry |
| 配置运行中漂移 | RuntimeSnapshot |
| 上下文无限膨胀 | Canonical History + ContextBuilder + Artifact |
| 模型接口波动 | ModelGateway + RecoveryPolicy |
| 企微接入 | Python IM Gateway + WeCom 官方 Python SDK |
| Skill 凭据安全 | Egress Boundary + SecretRef |

---

# 3. 硬件方案

## 3.1 基本硬件构成

不涉及专用硬件。系统部署于现有 Kubernetes/容器基础设施。

基础依赖：

- Linux 容器运行环境；
- PostgreSQL；
- Redis；
- S3-compatible Artifact Store（NFS-backed RWX PVC）；
- Secret Provider；
- 网络可达的业务平台与 MCP Server。

## 3.2 专用芯片说明

不涉及。模型推理由外部/内部模型服务提供，Agent Runtime 不要求本地 GPU。

---

# 4. 软件方案

## 4.1 方案选型

### 4.1.1 架构方案比较

| 方案 | 优点 | 问题 | 决策 |
|---|---|---|---|
| 接口=Capability + Workflow | 强治理、低代码可视化 | 建模成本高、数据处理复杂、SOP 开发慢 | 不作为 Phase 1 主路径 |
| 继续 OpenClaw Runtime | 现成 Skill/Channel | 有状态、Pod/用户绑定、生产扩展困难 | 替换 Runtime |
| Skill-first + 自研 Runtime | 开发快、贴合已验证实践、可无状态 | 需要自研 Harness | **采用** |

### 4.1.2 语言与框架

后端统一 Python：

- Python 3.12+；
- FastAPI；
- Pydantic v2；
- SQLAlchemy 2 Async；
- asyncpg；
- Alembic；
- LangGraph；
- LangChain Core/自研 Model Adapter；
- OpenTelemetry；
- uv/pytest。

前端：

- React；
- TypeScript；
- Vite；
- Semi Design。

## 4.2 软件总体架构

### 4.2.1 部署视图

```text
                    ┌────────────────────────┐
                    │   console-platform     │
                    │ FastAPI + React Static │
                    └───────────┬────────────┘
                                │ Definition/API
                                │
WeCom                    ┌──────▼─────────────┐
  │                      │   agent-runtime    │
  ▼                      │ Python + LangGraph │
┌───────────────┐ HTTP/SSE│ Stateless         │
│  im-gateway   ├────────►│ Agent Core        │
│ Python        │◄────────┤ Streaming         │
│ WeCom SDK     │         └───────┬───────────┘
└───────────────┘                 │
                                  │ Egress Boundary
                                  ▼
                         ┌───────────────────┐
                         │ Business / MCP    │
                         └───────────────────┘

External State:
PostgreSQL / Redis / Artifact Store（NFS-backed RWX PVC） / Secret Provider / OTel Backend
```

Phase 1 仅三个自研后台 Image：

```text
muad-console-platform
muad-agent-runtime
muad-im-gateway
```

### 4.2.2 代码视图

```text
muad/
├── apps/
│   ├── console-platform/
│   │   ├── backend/
│   │   ├── frontend/
│   │   └── Dockerfile
│   ├── agent-runtime/
│   │   ├── api/
│   │   ├── bootstrap/
│   │   ├── adapters/
│   │   └── Dockerfile
│   └── im-gateway/
│       ├── channels/
│       │   └── wecom/
│       └── Dockerfile
├── packages/
│   ├── agent-core/
│   ├── contracts/
│   ├── skill-sdk/
│   ├── platform-sdk/
│   └── common/
├── migrations/
├── tests/
└── deploy/
```

### 4.2.3 依赖约束

允许：

```text
console-platform → contracts/common
agent-runtime → agent-core/contracts/platform-sdk/common
im-gateway → contracts/common
agent-core → contracts/common
```

禁止：

```text
agent-core → console-platform
agent-runtime → console ORM/repository 实现
im-gateway → LangGraph/Agent reasoning
Skill → Runtime/Repository/Domain 内部模块
```

### 4.2.4 4+1 视图摘要

- **逻辑视图**：Control Plane / Execution Plane / Ingress Plane / External State。
- **开发视图**：Monorepo，多 package，严格依赖方向。
- **进程视图**：三个独立 Image，Runtime/Gateway 可水平扩容。
- **物理视图**：Kubernetes Deployment + 外部 PG/Redis/ObjectStore/SecretProvider。
- **场景视图**：以 Playbook U01、A03、D10 为黄金链路。

## 4.3 系统流程

### 4.3.1 流程一：企业微信普通对话

```text
1. WeCom SDK 接收消息
2. IM Gateway 转 ChannelEnvelope
3. bot_id 解析 agent_id
4. external_user_id 解析 PlatformUser
5. 校验 AgentAccessGrant
6. 调 Agent Runtime POST /v1/runs
7. Runtime 创建 RuntimeSnapshot
8. 加载 Conversation/UserMemory
9. 构建 Skill Catalog/ToolRegistry
10. LangGraph 执行
11. SSE stream 返回 Gateway
12. Gateway 转企微流式消息
13. Canonical Events/RunAudit 落库
```

关键接口：

```text
IM Gateway → Runtime: POST /v1/runs
Runtime → Console Internal API: get agent/runtime definition
Runtime → Storage: conversation/memory/checkpoint
Gateway → Console Internal API: identity/bot routing
```

### 4.3.2 流程二：Skill 执行业务平台调用

```text
LLM 判断需要某 Skill
→ load_skill
→ SkillExecutor.run
→ ctx.platform/http/mcp
→ Egress Boundary
   ├─ resolve actor credential
   ├─ resolve logical service
   ├─ allowlist check
   ├─ trace/audit
   └─ execute
→ result
→ ArtifactManager（大结果）
→ Agent 继续推理
```

### 4.3.3 流程三：Skill 导入与灰度

```text
IDE 开发
→ validate/test/pack
→ Console upload
→ manifest/secret/checksum validation
→ Artifact Store（NFS-backed RWX PVC） immutable artifact
→ user_scope=SELECTED
→ skill_user_grant
→ Runtime Resolver 下一次 Run 可见
→ 验证
→ user_scope=ALL
```

### 4.3.4 系统初始化流程

`console-platform`：

- DB migration 检查；
- 配置加载；
- SecretProvider/Storage 健康检查；
- 启动 API；
- 提供静态前端。

`agent-runtime`：

- 加载 Runtime 配置；
- 初始化 Model Provider registry；
- 初始化 ToolRegistry 基础工具；
- 初始化 Checkpointer/Storage Adapter；
- 不预加载某个用户/Agent 私有状态。

`im-gateway`：

- 加载启用的 BotAccount；
- 为每个 Bot 启动 WeCom WebSocket；
- 心跳/重连；
- 暴露 health/readiness。

### 4.3.5 系统退出流程

- 停止接收新请求；
- 当前 HTTP/SSE Run 进入 graceful shutdown window；
- Gateway 停止建立新会话并关闭 WebSocket；
- Flush audit/telemetry；
- 不依赖本地状态恢复业务事实。

## 4.4 数据结构

### 4.4.1 核心控制面实体

| 实体 | 核心字段 | 说明 |
|---|---|---|
| AgentDefinition | id, name, instructions, model_id, revision, enabled | Agent 定义 |
| AgentSkillBinding | agent_id, skill_id, enabled | Agent 可用 Skill |
| AgentMcpBinding | agent_id, mcp_server_id, enabled | Agent 可用 MCP |
| ModelDefinition | key, protocol, model_id, base_url, secret_ref, params, revision, enabled | 模型配置；V1.3 当前仅 OPENAI 协议 |
| Skill | id, key, name, scope, current_artifact_id | Skill 产品对象 |
| SkillArtifact | id, skill_id, version, checksum, object_uri, manifest | 不可变制品 |
| McpServer | id, endpoint, auth_ref, scope, enabled | MCP 服务 |
| PlatformUser | id, tenant_id, status | 平台用户 |
| AgentAccessGrant | user_id, agent_id | 用户 Agent 授权 |
| SkillUserGrant | user_id, skill_id | SELECTED Skill 灰度 |
| McpUserGrant | user_id, mcp_server_id | SELECTED MCP 灰度 |
| BotAccount | id, bot_id, secret_ref, agent_id, enabled | IM 路由 |
| ChannelIdentity | channel, external_user_id, platform_user_id | 身份映射 |
| ProjectPlatform | id, key, name, endpoint metadata | 业务项目平台 |
| UserCredentialRef | user_id, platform_id, secret_ref | 用户凭据引用 |

### 4.4.2 Runtime 实体

| 实体 | 核心字段 | 说明 |
|---|---|---|
| Conversation | id, user_id, agent_id, status | 会话 |
| RunRecord | id, conversation_id, snapshot_id, status, trace_id | 一次 Agent Run |
| RuntimeSnapshot | id, agent_revision, model_revision, skills_json, mcp_json, policy_json | 运行快照 |
| CanonicalEvent | run_id, seq, type, payload, created_at | 真实事件历史 |
| UserMemory | tenant_id, user_id, key/content/source, timestamps | 长期上下文 |
| Artifact | id, run_id, uri, media_type, size, checksum | 大结果/文件 |
| ToolCallAudit | run_id, tool_name, prepared_args_hash, status, latency | Tool 审计 |
| EgressAudit | run_id, actor, target, operation, result, latency | 出网审计 |
| ModelInvocationAudit | run_id, provider, model, retry_count, latency | 模型调用审计 |

通用字段统一：

```text
id
is_deleted
create_time
update_time
```

需要版本确定性的对象增加 `revision/version/checksum`。

## 4.5 模块设计

### 4.5.1 Console Platform

#### 模块职责

- Agent/Skill/MCP/Model CRUD；
- User/AgentAccessGrant；
- ProjectPlatform/Credential 管理；
- BotAccount/绑定码/ChannelIdentity；
- Skill Artifact 导入、校验、灰度；
- Runtime Definition Internal API；
- Run/Audit 查询；
- User Memory 查看/清理；
- React Console 静态资源。

#### 边界

不负责：

- Agent reasoning；
- Skill 执行；
- MCP Tool execute；
- WeCom WebSocket 长连接；
- Runtime 本地会话状态。

#### 关键内部接口

```text
GET /internal/runtime/agents/{agent_id}/definition
GET /internal/runtime/skills/{artifact_id}
GET /internal/runtime/users/{user_id}/context
POST /internal/channel/bind
GET /internal/channel/bots
GET /internal/auth/users/{user_id}/agents/{agent_id}
```

### 4.5.2 Agent Runtime

#### 模块职责

- Run API；
- RuntimeSnapshot；
- LangGraph Agent Loop；
- PromptBuilder；
- SkillRegistry/Loader/Executor；
- ToolRegistry；
- MCP Adapter；
- HookManager；
- PermissionPolicy；
- ContextManager；
- MemoryPort；
- ArtifactManager；
- ModelGateway/RecoveryPolicy；
- Egress Boundary；
- Streaming；
- Trace/Audit。

#### Agent Core 子模块

```text
agent-core/
├── agent/runner.py
├── agent/graph.py
├── prompt/builder.py
├── skill/registry.py
├── skill/loader.py
├── tools/registry.py
├── tools/executor.py
├── mcp/adapter.py
├── hooks/manager.py
├── policy/engine.py
├── context/builder.py
├── context/history.py
├── artifact/manager.py
├── model/gateway.py
├── model/recovery.py
└── telemetry/
```

#### LangGraph 边界

LangGraph 仅负责：

- graph execution；
- node transition；
- interrupt/resume；
- checkpoint integration。

不得让 Console Domain Model 直接等价于 StateGraph，不得让 Skill 依赖 LangGraph API。

### 4.5.3 IM Gateway

#### 模块职责

- WeCom Bot WebSocket；
- auth/heartbeat/reconnect（复用官方 SDK）；
- ChannelEnvelope；
- Bot → Agent routing；
- ChannelIdentity；
- `/bind /skills /new /stop`；
- IM 去重/ACK；
- Runtime SSE → WeCom Streaming；
- Future DeliveryRoute。

#### 边界

不负责：

- User Memory；
- Prompt；
- Model；
- Skill；
- 业务平台调用。

### 4.5.4 Skill SDK

Public API：

```python
ctx.platform.call(...)
ctx.http.get/post(...)
ctx.mcp.call(...)
ctx.artifact.write(...)
ctx.logger...
ctx.user
ctx.conversation
```

Skill 禁止 import `console-platform` 或 `agent-runtime` 内部 implementation package。

### 4.5.5 Egress Boundary

Phase 1 逻辑位于 Agent Runtime 内，但作为独立 package/port：

```text
CredentialResolver
ServiceResolver
AllowlistPolicy
EgressAuditWriter
RateLimiter
HttpExecutor
McpCredentialBridge
```

未来拆独立 Proxy 的触发条件：

- 引入不可信第三方 Skill；
- 需要网络 namespace 级隔离；
- 出网 QPS/扩容周期与 Runtime 显著不同；
- 统一服务网格/安全代理要求。

---

# 5. 关键特性设计

## 5.1 安全性设计

### 5.1.1 安全兜底机制

- AgentAccessGrant fail closed；
- Skill/MCP `user_scope=SELECTED` 时仅指定用户可见；未授权资源不进入 Runtime Catalog/ToolRegistry；
- Egress target 不在 allowlist 时拒绝；
- Secret 仅以 `secret_ref` 存储到业务表；
- Skill 日志/Artifact/Prompt 不注入 Secret；
- Tool PreparedCall 在授权后不可被参数篡改；
- 关键写操作未来通过 PermissionPolicy/Human Confirm 增强。

### 5.1.2 威胁建模摘要

关键威胁：

1. Prompt Injection 诱导调用越权工具；
2. 恶意/错误 Skill 访问非授权内网；
3. Secret 泄露到日志或 LLM；
4. Tool 参数在审批后发生 TOCTOU 修改；
5. SELECTED Skill 名称泄露；
6. Bot 身份与 Agent 授权混淆；
7. Runtime 本地状态导致跨用户串话。

核心控制：ToolRegistry、PermissionPolicy、Egress Boundary、Identity/Route/Auth 三分离、RuntimeSnapshot、外置状态。

### 5.1.3 安全设计

#### Tool 执行链

```text
LLM ToolCall
→ schema validate
→ pre_tool hook
→ policy
→ immutable PreparedToolCall
→ execute
→ post_tool hook
→ audit
```

#### Skill 出网链

```text
Skill logical request
→ Egress Boundary
→ actor credential resolve
→ target allowlist
→ audit context attach
→ external call
```

### 5.1.4 预使用组件安全

第三方组件至少包括：FastAPI、LangGraph、SQLAlchemy、企业微信 Python SDK、MCP SDK、React/Semi Design。上线前通过组织规定的 SCA/Mend 流程进行漏洞和许可证检查。

## 5.2 可靠性设计

### 5.2.1 能力目标定义

Phase 1 可靠性目标：

- Runtime 任意实例故障不导致用户/Memory/Conversation 权威数据丢失；
- Runtime 不依赖 sticky session；
- Gateway WebSocket 断开自动重连；
- Model 暂时性错误按 RecoveryPolicy 自动恢复；
- 当前 Run 因实例故障可能失败，但必须保留可定位事实；
- Phase 1 不承诺长任务 crash-resume。

### 5.2.2 故障场景分析

| 故障 | 行为 |
|---|---|
| Runtime Pod crash | 当前连接失败；历史/Memory 不丢；下一轮进入其他实例 |
| Console 暂时不可用 | 已缓存 Snapshot 的正在运行请求尽可能继续；新 Run Definition 获取失败时 fail fast |
| WeCom WS 断开 | SDK 自动重连；用户业务事实不在 Gateway 内存中 |
| PostgreSQL 不可用 | 新 Run fail closed；不得退化为本地状态 |
| Redis 不可用 | 只影响非权威协调/cache；不得导致业务事实丢失 |
| Artifact Store（NFS-backed RWX PVC） 不可用 | 大 Artifact 生成失败并明确报错；小文本结果仍可按策略返回 |
| Model 429/5xx | RecoveryPolicy retry/backoff/deadline |

### 5.2.3 业界竞争分析

本版本不以通用 Agent Platform 的功能数量作为竞争目标，优先保证：

- SOP 开发成本低；
- 生产无状态；
- 业务平台集成自然；
- Runtime 可观测可治理；
- Skill 快速迭代。

### 5.2.4 实现方案设计

- PostgreSQL 保存权威事实；
- Redis 仅用于 cache/pubsub/cancel hint 等非权威协调；
- Artifact Store（NFS-backed RWX PVC） 保存 Skill Artifact 与大结果；
- RuntimeSnapshot 固定版本；
- Model Recovery 有 deadline；
- Gateway 使用官方 SDK heartbeat/reconnect。

### 5.2.5 韧性设计

Phase 1 控制过载：

- Runtime 并发 Run 限制；
- Model Provider rate limit；
- Egress per-platform rate limit；
- Tool 最大调用次数；
- Run deadline；
- Context token budget；
- Artifact 大小限制；
- Skill 包大小限制。

## 5.3 可测试性设计

### 单元测试

- Agent Core 各 Registry/Builder/Policy 可纯 Python 单测；
- Skill 使用 MockSkillContext；
- Egress Resolver/Allowlist 可 Mock；
- RuntimeSnapshot determinism 测试。

### 集成测试

- Console → Runtime Definition；
- Runtime → PostgreSQL Checkpoint；
- Runtime → Test MCP；
- Gateway → Runtime SSE；
- WeCom SDK contract test。

### 黄金旅程 E2E

1. `/bind`；
2. Agent 授权；
3. Skill `user_scope=SELECTED` 指定用户验证；
4. 用户触发；
5. Runtime A/B 切换；
6. Skill v1→v2 Snapshot；
7. Egress allowlist 拒绝；
8. Model 429 recovery。

## 5.4 可调试性设计

统一关联字段：

```text
trace_id
run_id
conversation_id
platform_user_id
agent_id
snapshot_id
skill_artifact_id
tool_call_id
```

日志要求结构化 JSON；禁止打印 Secret Value。

Console 运行审计页支持按：用户、Agent、Run ID、时间、Skill、状态过滤。

## 5.5 可运维性设计

### 5.5.1 可运维性目标

三个 Image 独立：

```text
console-platform
agent-runtime
im-gateway
```

提供：

- `/healthz`；
- `/readyz`；
- Prometheus/OTel metrics；
- structured logs；
- trace export；
- graceful shutdown。

### 5.5.2 部署方案

- PostgreSQL/Redis/ObjectStore 不放入应用 compose；
- 生产环境作为外部依赖独立部署；
- Runtime/Gateway 可独立 HPA；
- Console 通常低副本；
- 同一 commit 构建三个带相同版本标识的 Image。

## 5.6 可扩展性设计

扩展端口：

```text
ModelProvider
ChannelAdapter
ToolDefinition
McpAdapter
SkillContext Port
MemoryPort
ArtifactStore
SecretProvider
EgressResolver
```

Future Worker 使用同一 `agent-core`，不得复制一套 Agent/Skill/MCP 实现。

## 5.7 可复用性设计

### 5.7.1 技术规范落地计划

- Pydantic contracts 作为 Python 服务共享 DTO；
- OpenAPI 用于跨服务协议；
- 不共享 ORM entity；
- package API 必须有稳定边界。

### 5.7.2 本版本可采用的公用代码

- FastAPI 基础组件；
- OTel 封装；
- DB base model；
- API response/error 规范；
- SecretRef；
- 企业微信官方 Python SDK；
- LangGraph Checkpointer。

### 5.7.3 本版本可产出的公用代码

- `agent-core`；
- `skill-sdk`；
- `platform-sdk`；
- `contracts`；
- ChannelAdapter SPI；
- ToolRegistry/HookManager。

### 5.7.4 设计文档更新约定

所有新模块必须回溯到 Playbook 用户旅程；如果没有用户旅程支撑，不得仅为了平台完整性进入 Phase 1。

## 5.8 系统隐私设计

- User Memory 按 tenant+user 隔离；
- Memory 不推断敏感画像；
- 凭据通过 SecretRef；
- Audit 保留必要标识，不保存明文认证信息；
- Artifact 根据业务敏感等级设置访问控制和生命周期；
- LLM Context 只注入完成当前任务所必需的数据。

## 5.9 软件质量及效能改进设计

- Monorepo；
- pre-commit/ruff/mypy（按团队成熟度落地）；
- pytest；
- API contract tests；
- architecture dependency test；
- DB migration gate；
- Docker image scan；
- Skill SDK compatibility test；
- Golden Journey CI。

---

# 6. 总结

## 6.1 与上一个版本的设计变化

### 6.1.1 Capability-first → Skill-first

#### 变化原因

接口作为最小产品单元增加开发者理解成本和 SOP 建模成本，并不能自然表达数据转换、条件、循环等程序逻辑。

#### 变化描述

- Capability 不再是 Phase 1 主对象；
- Skill 可以直接调用 Platform/HTTP/MCP；
- 数据处理保留 Python；
- Future 高治理需求再固化 Tool/Capability Adapter。

#### 影响

Console 对象减少，Skill SDK 与 Egress Boundary 重要性提升。

### 6.1.2 OpenClaw Runtime → 自研 Stateless Runtime

#### 变化原因

旧模式用户与 Pod/workspace 强绑定，扩容困难。

#### 变化描述

- Python + LangGraph；
- Runtime 无状态；
- 状态外置；
- RuntimeSnapshot；
- ToolRegistry；
- Skill Lazy Load；
- Canonical History/Context 分离。

### 6.1.3 五/四服务 → 三核心 Image

- console-platform；
- agent-runtime；
- im-gateway。

Worker 延后；Egress Boundary 逻辑保留但不独立 Image。

### 6.1.4 Node IM Gateway → Python IM Gateway

复用企业微信官方 Python AI Bot SDK，后端语言统一为 Python。

## 6.2 风险问题保证

| 风险 | 对策 |
|---|---|
| Skill 变成“大脚本” | SDK 边界、基础设施能力下沉、测试模板、代码规范 |
| Skill 任意出网 | Egress Boundary + allowlist + audit |
| LangGraph 绑死领域模型 | Agent Core Port/Adapter，LangGraph 仅执行层 |
| Context 膨胀 | Skill Lazy Load + Context Budget + Artifact |
| 配置中途变更 | RuntimeSnapshot |
| MCP 绕过治理 | MCP Tool 统一进 ToolRegistry |
| Runtime 本地状态 | Architecture Gate 禁止权威状态落本地 |
| Future Worker 重复建设 Agent | Worker 必须复用 agent-core |

## 6.3 尚未解决的问题

以下问题不阻塞 Phase 1，但需要后续以真实场景决策：

1. Worker Engine 的具体轻量实现还是引入成熟 durable engine；
2. 第三方不可信 Skill 是否允许进入平台；
3. 如果允许不可信 Skill，Egress Boundary 是否必须物理独立并配套 Sandbox；
4. WebChat 的首个真实接入时间；
5. User Memory 自动写入策略的产品化程度；
6. 浏览器自动化是否成为正式 Tool；
7. Capability/治理 Tool 的真实升级判据是否需要产品面。

---

# 7. 变更控制

## 7.1 变更列表

| 版本 | 日期 | 说明 |
|---|---|---|
| V2.0 | 2026-09-17 | 基于最新对齐结论重写：Skill-first、自研 Stateless Runtime、三 Image、Python IM Gateway、Worker 后置、Egress Boundary 内聚 |

---

# 附录 A：Phase 1 核心 API 草案

## Console Internal API

```text
GET  /internal/runtime/agents/{agent_id}/definition
GET  /internal/runtime/skills/{artifact_id}
GET  /internal/runtime/users/{user_id}/memory
GET  /internal/runtime/users/{user_id}/credentials/{platform_id}
GET  /internal/channel/bots
POST /internal/channel/bind
GET  /internal/auth/users/{user_id}/agents/{agent_id}
POST /internal/audit/egress
```

## Agent Runtime API

```text
POST /v1/runs
POST /v1/runs/{run_id}/resume
POST /v1/runs/{run_id}/cancel
GET  /v1/runs/{run_id}
GET  /v1/runs/{run_id}/events
```

`POST /v1/runs` 支持 SSE Streaming。

## IM Gateway Internal API

```text
POST /internal/messages/send      # 预留主动消息
GET  /internal/channels/health
POST /internal/channels/reload
```

---

# 附录 B：Phase 1 Architecture Gate

必须自动/人工检查：

1. Runtime package 不 import Console ORM/Repository；
2. Skill package 不 import Runtime internal package；
3. Runtime 不把 User/Conversation/Memory 写本地文件作为权威存储；
4. MCP Tool 不能绕过 ToolRegistry；
5. Skill 外部访问必须经 SkillContext/Egress Boundary；
6. Credential Value 不进入 RuntimeSnapshot/Prompt/Audit；
7. RuntimeSnapshot 中 Skill 使用 Artifact checksum；
8. 同一 Run 不因 Console 修改自动切换 Skill/Model；
9. Redis 不能成为权威业务状态源；
10. Phase 1 不新增 Worker/Workflow/Capability Center，除非先补充对应 Playbook 和架构评审。


### V1.3 Artifact Store 落地补充

V1 使用 NFS-backed RWX PVC；Runtime/Worker 使用 `emptyDir` 本地 SkillArtifactCache，cache miss 才从共享 PVC 复制；数据库/API 只保存 `storage_key`。
