# 02 Capability 与 Tool 详细设计

## 1. 领域模型

```text
CapabilityDefinition
 ├─ Skill
 ├─ MCP
 └─ Tool
       │
       └─ implementation_ref -> builtin/plugin/mcp/http/workflow adapter...

User/Tenant Binding/Grant
AgentCapabilityAllowlist
TenantPolicy
          │
          v
EffectiveCapability
```

## 2. AgentDefinition 改造

当前 `CapabilityBinding` 位于 AgentDefinition，字段只有 ref/version/type，但名称表达错误。改为 `AgentCapabilityReference`（或 AllowlistEntry）。

它只允许：capability_ref、type、允许的 version constraint/pin。禁止：CredentialRef、user config、user enable state、user ownership。

## 3. Tool/MCP 授权

真值表：

| User | Agent | Tenant | 结果 |
|------|-------|--------|------|
| 0    | *     | *      | deny |
| 1    | 0     | *      | deny |
| 1    | 1     | 0      | deny |
| 1    | 1     | 1      | allow |

当前 `runtime_tool_ops._effective_tool_policy()` 的 `user_tools = agent_tools` 必须移除。

UserDomainService `grant()` 必须支持 Tool grant，或引入统一 `UserCapabilityBinding` Store。不能让 Tool 的用户维度只存在于 AgentDefinition。

## 4. MCP

当前 MCP Runtime 已按 user binding 获取 MCP、resolve Credential、per-execution cache config，方向保留。MCP server-level grant 与 discovered tool allowlist 需要共同进入 EffectiveCapability，不允许只靠挂载成功就自动授权所有 Tool。

## 5. Skill（与 Tool/MCP 统一授权）

Skill 与 Tool/MCP 三者「按用户授权」语义一致，统一走 `visibility` + `grant()`（capability_grants）：

- public / tenant：全租户用户可用，无需 grant；
- private：仅被管理员 grant 的用户可用；
- exact version pin 进 Snapshot；
- `allowed_tools` 继续参与 Tool/MCP 的 `∩`，不因 Skill 放行 Tool（fail-closed）。

差异仅在：Tool/MCP 有副作用，额外保留 `AgentAllowlist ∩ TenantPolicy` 安全层；Skill 无此层。

## 6. Tool 与 Plugin

不要永久保持 `TOOL == PLUGIN`。Tool 是 Agent-facing capability；Plugin 是实现机制。一个 Plugin 可以提供多个 Tool，也可以提供 Model/Sandbox/SemanticStore 等非 Tool 扩展。

## 7. 执行安全顺序

建议统一：

`Descriptor exists → EffectiveCapability → Semantic/Risk Policy → Approval → before_tool Hook → Execute → after_tool Hook → Trace`

未经授权的参数不进入高权限 Hook，避免敏感参数泄漏。
