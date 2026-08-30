# ADR-A002 Capability 定义、绑定与授权

**决策**：

- Definition 描述能力；
- User/Tenant Binding/Grant 描述主体差异；
- AgentDefinition 只保存 Allowlist/Reference；
- 能力类型四类（skill / tool / mcp / workflow）统一按 `visibility`（public/private）+ 管理员 `grant()` per-user 授权；
- Tool/MCP 额外 `AgentAllowlist ∩ TenantPolicy`（缺任一维度 fail-closed）；
- Workflow 是一等能力（`CapabilityType.WORKFLOW`），内部 step 沿用发起用户授权（frozen effective 图进 durable 上下文）；
- EffectiveCapability 由单一 Resolver 输出。

当前 `user_tools = agent_tools` 明确标记为待整改架构违反，不作为兼容行为保留。
