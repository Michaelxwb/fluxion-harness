# Fluxion Architecture Specs

## Purpose

Fluxion 是无状态、插件化、Resource 驱动的 Agent Harness。Agent 指实际运行的 Runtime Service/Pod；Console/Control Plane 只管理 RuntimeProfile 等版本化资源；Web Chat 是正式用户 Channel。

## Spec Navigation

- `runtime-core.md` — Runtime 无状态、ExecutionSnapshot、Microkernel、Plugin/Hook 边界。
- `resource-registry.md` — Resource/Binding、SQLite/PostgreSQL Store、tenant scope、SecretRef。
- `console-channel.md` — Console、Web Chat、PlatformUser、`/bind` 与用户绑定。
- `workflow-capability.md` — Tool、Capability、Workflow、A2A 边界。
- `dfx.md` — 编码阶段 DFX、性能、安全、测试、观测与运维约束。

## Code Anchors

- 后端实现入口：`backend/src/fluxion/`。
- 前端应用边界：`frontend/apps/console/`、`frontend/apps/chat/`。
- 共享契约占位：`shared/contracts/`。
- 事实源入口：`README.md`、`AGENTS.md`、`docs/architecture/fluxion-architecture-baseline-v1.md`。
