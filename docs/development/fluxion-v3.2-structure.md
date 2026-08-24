# Fluxion Harness V3.2 — Codex / code-flow 直接开发结构

## 1. V3 目标

V3 的目标不是再增加一套 Fluxion 自己的 Coding Workflow，而是让仓库完全服从已经接入的 code-flow：

```text
架构/设计文档
    ↓
Tier-1 Specs
    ↓
code-flow TASK
    ↓
cf-task-start
    ↓
Start Gate + Spec Session
    ↓
RED → 实现 → GREEN
    ↓
Acceptance Evidence
    ↓
Stop/Done Gate
```

Fluxion 只维护项目事实、Contract 和 TASK，不维护第二套 active phase 状态机。

## 1.1 领域语义

- Agent = 实际部署的 Agent Runtime Service/Pod。
- RuntimeProfile = Console 管理的运行态配置。
- Console 不创建 Pod；Kubernetes/Helm 负责 Runtime Pod 生命周期。
- 所有 Pod 从统一 Registry 读取 RuntimeProfile/UserRuntimeState/TenantPolicy。

## 2. 项目结构

```text
fluxion-harness/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── package.json
├── pnpm-workspace.yaml
│
├── backend/
│   ├── src/fluxion/
│   │   ├── kernel/
│   │   ├── resources/
│   │   ├── registry/
│   │   ├── plugins/
│   │   ├── runtime/
│   │   ├── protocols/
│   │   ├── services/
│   │   ├── repositories/
│   │   ├── models/
│   │   ├── errors/
│   │   ├── observability/
│   │   └── api/
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── contract/
│       └── e2e/
│
├── frontend/
│   ├── apps/
│   │   ├── console/
│   │   └── chat/
│   └── packages/
│       └── shared/
│
├── shared/
│   └── contracts/
│
├── docs/
│   ├── architecture/
│   ├── problems/
│   ├── design/
│   ├── development/
│   └── adr/
│
├── deploy/
│   ├── local/
│   ├── docker/
│   └── helm/
│
├── scripts/
│   └── check_frontend_constraints.py
│
└── .code-flow/
    ├── config.yml
    ├── validation.yml
    ├── specs/
    │   ├── architecture/
    │   ├── backend/
    │   │   └── console-api-contract.md
    │   └── frontend/
    │       └── semi-design.md
    └── tasks/2026-08-23/
        ├── phase-01-resource-foundation/resource-foundation.md
        ├── phase-02-runtime-kernel/runtime-kernel.md
        ├── phase-03-plugin-hook/plugin-hook.md
        ├── phase-04-tool-skill-mcp/tool-skill-mcp.md
        ├── phase-05-runtime-api-cli/runtime-api-cli.md
        ├── phase-06-control-plane-api/control-plane-api.md
        ├── phase-07-console-web/console-web.md
        ├── phase-08-web-chat-bind/web-chat-bind.md
        └── phase-09-publish-outbox-audit/publish-outbox-audit.md
        ├── phase-10-workflow-management/workflow-management.md
        ├── phase-11-governance-eval-views/governance-eval-views.md
        └── phase-12-quality-hardening/quality-hardening.md
```

## 3. Console 统一 Response

Console/Channel API 统一：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "request_id": "req_xxx"
}
```

- Response Factory 唯一出口。
- Domain/Application 层抛类型化异常，不返回 HTTP Response。
- 全局 Exception Mapper 转换 HTTP Status + Business Code。
- `request_id` 同步写入 `X-Request-ID`。

错误码：

```text
30xxx 通用请求/校验
31xxx Resource
32xxx Binding
33xxx Publish/Version
34xxx Identity/Bind/Channel
35xxx Auth/AuthZ
36xxx Workflow/Capability 引用
39xxx 内部/依赖
```

## 4. Console 统一日志

V1 使用 Python logging + structlog JSON Renderer。

Request Context：

```text
request_id trace_id tenant_id actor_id
method route client_ip user_agent
```

请求完成日志：

```text
timestamp level service environment event
request_id trace_id tenant_id actor_id
method route status_code biz_code latency_ms
```

Resource/Publish 场景增加 resource_id/version/publish_id 等字段。

统一脱敏 password/token/authorization/cookie/secret/bind_code/credential/api_key。

普通日志不替代 AuditLog。Publish、Rollback、Binding/权限/Policy、Bind、CredentialRef 变化必须进入独立 Audit。

## 5. code-flow 原生命令

```text
cf-task-status
cf-task-start resource-foundation TASK-001
cf-task-start runtime-kernel TASK-002
cf-task-start plugin-hook TASK-003
cf-task-start tool-skill-mcp TASK-004
cf-task-start runtime-api-cli TASK-005

cf-task-start control-plane-api TASK-101
cf-task-start console-web TASK-102
cf-task-start web-chat-bind TASK-103
cf-task-start publish-outbox-audit TASK-104
cf-task-start workflow-management TASK-105
cf-task-start governance-eval-views TASK-106
cf-task-start quality-hardening TASK-107
```

TASK 文件已经包含 Status、Source、Spec-Refs、Acceptance-Refs、Acceptance Contract、Acceptance Evidence 和 Definition of Done。

## 6. 前端

- React 19
- TypeScript
- Vite
- Semi Design 2.102.x
- `@douyinfe/semi-icons`
- `@douyinfe/semi-ui/react19-adapter`

`validation.yml` 会调用 `scripts/check_frontend_constraints.py`：

- 禁止 antd / @ant-design/icons / MUI。
- 检查 Semi Design 依赖。
- `main.tsx` 存在后检查 React19 Adapter 导入顺序。

## 7. DFX

所有 TASK 都继续继承：

- Availability
- Reliability
- Scalability
- Performance
- Security
- Maintainability
- Testability
- Observability
- Deployability
- Compatibility
- Recoverability
- Operability

DFX 必须通过测试/指标/Trace/故障注入给出证据，而不是只写在文档中。
