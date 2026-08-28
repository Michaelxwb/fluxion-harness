# ADR-A002 Capability 定义、绑定与授权

**决策**：

- Definition 描述能力；
- User/Tenant Binding/Grant 描述主体差异；
- AgentDefinition 只保存 Allowlist/Reference；
- Tool/MCP = `UserGrant ∩ AgentAllowlist ∩ TenantPolicy`；
- Skill 的扩展式语义单独处理；
- EffectiveCapability 由单一 Resolver 输出。

当前 `user_tools = agent_tools` 明确标记为待整改架构违反，不作为兼容行为保留。
