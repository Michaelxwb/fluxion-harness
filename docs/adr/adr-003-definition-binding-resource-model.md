# ADR-003: Definition + Binding 资源模型

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P03

## Context

同一用户的不同 Agent 各自维护 Skill/MCP/Credential 导致重复配置与跨 Agent 数据不一致。Agent 不应成为用户资源的 Owner。

## Constraints

- Skill/MCP/Plugin 需要共享定义但具有用户/租户差异。
- Scope 至少支持 system/public、tenant、user/private。
- EffectiveCapability = UserGrant ∩ AgentAllowlist ∩ TenantPolicy。

## Options

1. Skill/MCP 直接挂到 Agent 上（旧模型）。
2. Definition + Binding 分离，Binding 归 User/Tenant。
3. 全部扁平 Resource，不区分定义与绑定。

## Decision

**Definition + Binding 分离：**

```text
SkillDefinition  → SkillBinding(scope=user/tenant)
MCPDefinition   → MCPBinding(scope=user/tenant)
PluginDefinition → PluginBinding(scope=global/tenant/agent/user)
```

Definition 描述"是什么"；Binding 描述"谁能用、怎么配置、用什么凭证"。Agent 只声明 Allowlist。

## Trade-offs

- 换取用户资源一致性与跨 Agent 共享，代价是资源种类翻倍、解析链路变复杂。
- 统一由 Resource Resolver 计算 EffectiveCapability，保持单点。

## Failure Modes

- Binding 引用越权资源 → 跨 tenant 泄露。用 E-C102 拒绝 + fail closed。
- Agent Allowlist 与 Binding 交集为空 → 静默无能力。用 E-R03 验证。

## Validation

- S-R04 / E-R03：EffectiveCapability 交集正确。
- E-C102：跨 tenant Binding 拒绝。

## Revisit Conditions

- 出现无需共享定义的单一归属资源且 Binding 成为纯开销。
