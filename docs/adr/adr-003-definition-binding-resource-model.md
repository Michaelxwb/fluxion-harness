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

## Amendment (2026-08-26): Skill 扩展式能力模型

A1 review 发现：原文「EffectiveCapability = UserGrant ∩ AgentAllowlist ∩ TenantPolicy」
（严格交集）与 S_R18 契约冲突——S_R18 把「Binding 授予 profile `allowed_skills` 之外
的 Skill」明确为**期望特性**（扩展式能力）。经设计裁决，确认扩展模型为既有 spec，
对 ADR-003 做如下澄清，不改变实现：

- **Skill 维度允许扩展**：Binding 可授予 RuntimeProfile `allowed_skills` 之外的 Skill。
  扩展由 Resolver 的 `_effective_skill_selectors` 独立计算，不经过能力交集函数 `_allowed`，
  故 S_R18 的扩展语义不受 A1 fail-closed 收敛影响。
- **MCP / builtin tools 不允许扩展**：`allowed_tools` 是 Agent 层硬上限。Binding/UserGrant
  只能在 Agent allowlist 之内做交集（UserGrant ∩ AgentAllowlist ∩ TenantPolicy），不得越过。
  对应 `_allowed` 的 fail-closed：空 allowlist = 全拒（与 Runtime `tools.py` enforcement 对齐）。
- **Failure Modes 澄清**：原文「交集为空 → 静默无能力」对 MCP/builtin tools 仍然成立且为
  fail-closed；Skill 的扩展路径是**有意**的，不属于该 failure mode。

理由：Skill 是声明式能力（prompt/instructions），授予风险可控；MCP/builtin tools 是可执行
副作用通道，必须由 Agent allowlist 把守。两类能力安全边界不同，故扩展语义仅限 Skill。
