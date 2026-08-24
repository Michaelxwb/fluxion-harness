# ADR-010: Trusted / Untrusted Plugin 边界

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P10

## Context

"万物皆插件"同时引入安全与稳定性风险：第三方 Python Plugin 进 Runtime Process 可能访问内存、其他租户上下文、文件、网络和 Secret，也可能阻塞 Event Loop 或导致 Pod Crash。

## Constraints

- Everything is a Plugin ≠ Everything runs in-process。
- 必须控制网络、文件系统、Secret 可见范围、CPU/Memory、Timeout、Tenant Context、供应链来源。

## Options

1. 所有 Plugin 一律 in-process。
2. 所有 Plugin 一律隔离（重）。
3. 按信任等级分派：可信基础设施插件 in-process，不可信/业务扩展 out-of-process。

## Decision

**按信任等级分派：**

```text
Trusted Infrastructure Plugin   → in-process（Core infrastructure）
Untrusted / Business Extension → MCP / RPC / Sandbox / isolated worker
```

- 以 `trust_level` 元数据声明，Runtime 据此决定执行边界。
- 安全敏感调用在进入前经过 Policy Intersection（EffectiveCapability），并受 timeout/隔离约束。

## Trade-offs

- 换取信任与性能的平衡，代价是需要维护信任分级与隔离通道（MCP/RPC/Sandbox）。

## Failure Modes

- 高信任 Plugin 被误标 → 供应链/评审控制 + `trust_level` 审计。
- 不可信 Plugin 阻塞 Event Loop → 隔离执行 + timeout + 资源限制。

## Validation

- E-R05：Plugin 信任边界与隔离执行。
- E-R03/E-R07：越权/未授权调用拒绝（policy intersection）。
- Fault injection：单个 Plugin 故障不拖垮 Runtime。

## Revisit Conditions

- 隔离通道成本过高，或出现必须 in-process 的可信业务扩展形态。
