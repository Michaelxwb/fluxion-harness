# Fluxion Harness V3.2

Fluxion 是一个 **无状态、插件化、Resource 驱动** 的 Agent Harness。Agent Runtime 与 Console/Control Plane 同仓开发、配套发布；`AgentDefinition` 指逻辑/产品 Agent，`RuntimeInstance` 指实际运行的 Runtime Pod/Process，Console 操作 AgentDefinition 而不管理 RuntimeInstance 生命周期，Web Chat 是正式用户 Channel。

## 事实源

- `AGENTS.md`：仓库级不可违反规则。
- `docs/foundation/`：问题与目标 / 核心需求（REQ-*） / 架构原则（ARCH-*）。
- `docs/architecture/总体架构.md`：总体架构基线。
- `docs/adr/`：ADR-A001~A006（长期取舍）。
- `docs/design/01~07`：领域详细设计。
- `docs/design/08-用户旅程与体验设计.md`：用户旅程与体验基线（功能立项须引用 UJ-\* 步骤）。
- `docs/development/架构验收Gate.md`：G1~G9 架构验收 Gate。
- `docs/migration/当前代码偏差与迁移.md`：当前代码偏差与 P0 整改。
- `.code-flow/specs/architecture/`：Fluxion 架构 required Specs。
- `.code-flow/specs/backend/console-api-contract.md`：Console 统一响应/异常/日志/Audit 规范。
- `.code-flow/specs/frontend/semi-design.md`：Semi Design 前端规范。


## 术语语义

```text
AgentDefinition
= 逻辑/产品 Agent（Console 创建和发布的对象）

RuntimeInstance
= 实际运行的 Runtime Pod / Process（共享 RuntimePool 承载）

RuntimeProfile
= 执行机制配置（超时/重试/并发/预算），所有 RuntimeInstance 从 Registry 读取

UserRuntimeState
= 用户 Skill/MCP/Tool/Credential/Profile/Memory 等绑定与状态

ExecutionSnapshot
= 一次执行固定的 AgentDefinition + RuntimeProfile + UserRuntimeState + TenantPolicy 版本集合
```

所有 RuntimeInstance 读取同一个 Registry；同一个 `tenant_id + user_id + agent_id` 在任意实例上必须得到等价运行态。

## 运行时服务拆分（规则 14）

同一镜像按 `FLUXION_ROLE` 分派为三个独立进程，互不影响、独立扩缩：

```text
api      Control Plane（Console / Chat / Workspace / Eval / Operations）
runtime  AgentLoop 执行（无状态，按 Agent 负载横向扩）
worker   DBOS durable workflow 执行（按队列负载扩）
```

数据库表结构由 `scripts/init_db.py` 初始化（幂等建表，PG/SQLite 双库），服务进程不建表。

## 仓库结构

```text
backend/src/fluxion/       Runtime + Control Plane 后端
backend/tests/             unit / integration / E2E / contract
frontend/apps/console/     超管 Console Web
frontend/apps/chat/        普通用户 Web Chat Channel
frontend/packages/shared/  前端共享类型、Semi 业务组件、API Client、主题
shared/contracts/          跨语言 Schema / OpenAPI / Event Contract
deploy/                    local / docker / helm
.code-flow/                Coding Spec、Hook、TASK、Gate
```

## 前端基线

- React 19 + TypeScript + Vite
- Semi Design `@douyinfe/semi-ui@2.102.x`
- `@douyinfe/semi-icons@2.102.x`
- React 19 入口最先加载：

```ts
import '@douyinfe/semi-ui/react19-adapter';
```

禁止引入 Ant Design 等第二套通用 UI 组件库。

## Console 后端基础规范

所有 Console/Channel JSON API 统一：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "request_id": "req_xxx"
}
```

请求必须建立 RequestContext，结构化 JSON 日志必须关联 `request_id/trace_id/tenant_id/actor_id`；Publish、Rollback、Binding、Policy、Bind 等高影响操作写独立 AuditLog。

## code-flow 原生 Coding 流程

Fluxion **不维护第二套任务激活脚本**。使用 code-flow 自带命令：

```text
cf-task:status
cf-task:start <任务名> [TASK-xxx]
cf-task:plan <设计简报路径>
cf-task:archive <任务名>
```

`cf-task:start` 自己负责：

```text
前置检查
→ Context refresh
→ Start Gate
→ Active Marker
→ Task Session
→ 先写验收测试
→ RED
→ 实现
→ GREEN
→ Acceptance Evidence
→ 自动完成检查
```

已完成批次归档至 `.code-flow/tasks/archived/`（含 2026-08-31 的 `runtime-architecture-closure` 架构收口整改 TASK-001~011）；当前活跃任务见 `.code-flow/tasks/` 下各日期目录。
