# ADR-006: Microkernel + Plugin Runtime

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P08

## Context

Model Provider、Memory、Context、Tool、Skill、MCP、Channel、Storage、Sandbox、Approval、Guardrail、Observability 等能力如果持续直接写入 Core，会重新形成巨型应用。

## Constraints

- Kernel 应尽可能小，只维护 context、lifecycle、typed event、plugin registry、contracts、execution。
- 一切可扩展能力通过稳定 Contract 插件化；Agent Loop 本身也可替换。
- Everything is a Plugin ≠ Everything runs in-process（见 ADR-010）。

## Options

1. 能力全部内联进 Core（旧模型）。
2. Microkernel + 稳定 Contract 插件化。

## Decision

**Microkernel + Plugin Runtime。** Kernel 不直接承担具体 Model/Memory/Tool/MCP/Channel 实现；Agent Loop、Model、Tool、Skill、MCP、Memory、Context、Storage、Sandbox、Approval、Guardrail、Auth、Observability 均为插件 Capability。

```text
kernel/ → context / lifecycle / event / plugin / contracts / execution
plugins/ → agent_loop / model / tool / skill / mcp / memory / storage / ...
```

## Trade-offs

- 换取 Core 复杂度可控与扩展解耦，代价是 Plugin Contract 需要长期稳定、早期可能频繁调整（兼容性风险）。

## Failure Modes

- Kernel 反向依赖具体 Plugin → 用 architecture dependency test 强制禁止。
- Plugin Contract 破坏 → versioned capability contract + compatibility manifest。

## Validation

- E-R05：Plugin 加载/卸载不影响 Core。
- Architecture dependency test：Kernel 不依赖 Console/具体 Plugin。

## Revisit Conditions

- 插件化带来的 Contract 维护成本超过能力扩展收益。
