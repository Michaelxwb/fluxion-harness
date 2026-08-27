# Fluxion Harness V3.2

Fluxion 是一个 **无状态、插件化、Resource 驱动** 的 Agent Harness。Agent Runtime 与 Console/Control Plane 同仓开发、配套发布；Agent 指实际运行的 Runtime Service/Pod，Console 只管理 RuntimeProfile 等运行态资源，Web Chat 是正式用户 Channel。

## 事实源

- `AGENTS.md`：仓库级不可违反规则。
- `docs/architecture/fluxion-architecture-baseline-v1.md`：总体架构基线。
- `docs/problems/design-drivers.md`：P01-P22 设计依据。
- `docs/design/fluxion-runtime-design-v1.7.md`：Runtime 详细设计。
- `docs/design/fluxion-console-design-v1.6.md`：Console / Control Plane / Web Chat 详细设计。
- `.code-flow/specs/architecture/`：Fluxion 架构 required Specs。
- `.code-flow/specs/backend/console-api-contract.md`：Console 统一响应/异常/日志/Audit 规范。
- `.code-flow/specs/frontend/semi-design.md`：Semi Design 前端规范。


## Agent / RuntimeProfile 语义

```text
Agent
= 实际运行的 Agent Runtime Service / Runtime Pod

RuntimeProfile
= Console 创建和发布的 Agent 运行态配置

UserRuntimeState
= 用户 Skill/MCP/Credential/Profile/Memory 等绑定与状态

ExecutionSnapshot
= 一次执行固定的 RuntimeProfile + UserRuntimeState + TenantPolicy 版本集合
```

所有 Runtime Pod 读取同一个 Registry；同一个 `tenant_id + user_id + runtime_profile_id` 在任意 Pod 上必须得到等价运行态。

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
# v2.2 rolling-wave：phase1 已实现（2026-08-27）；phase2-6 设计简报已就绪（2026-08-28，待 cf-task:plan 拆解 TASK）
cf-task:start phase1-product-architecture TASK-001
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

v1 批次已归档至 `.code-flow/tasks/archived/`（2026-08-23 实现批次、2026-08-26 ADR 简报）；当前活跃任务见 `.code-flow/tasks/2026-08-27/`（phase1 已实现）与 `.code-flow/tasks/2026-08-28/`（phase2-6 设计简报，待 `cf-task:plan`）。
