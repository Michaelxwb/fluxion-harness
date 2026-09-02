# ADR-A009 Tool 与 Plugin 领域边界（Capability vs Extension）

**引用**：REQ-CAP-007、REQ-CAP-005、ARCH-09 Typed Spec SoT、ADR-A004（Microkernel 与扩展信任边界）、规则 25。

**背景**：

历史 `TOOL == PLUGIN` 建模（`PluginType.TOOL_PROVIDER`、`PLUGIN` kind 承载 tool）混淆了「Agent 业务能力」与「Runtime 技术扩展」两个层级：Tool 无法独立授权，Plugin 的 install/load 生命周期与 Tool 的授权生命周期纠缠，Console 把 Plugin 当 Tool 展示。REQ-CAP-007 只作了一句声明，未上升为领域不变量。

**决策**：Tool 与 Plugin 彻底拆分，二者是**不同层级**概念，不得建模成同一概念或同一 ResourceKind。

- **Tool**（Capability）= Agent 可选择、授权、调用的业务能力（Agent-facing invocation contract）。参与 Capability Resolution，遵循 Agent/User/Tenant Capability Governance。
- **Plugin**（Extension）= 扩展 Fluxion Runtime/Platform 自身能力的技术扩展机制。不参与 Capability Resolution，遵循 Plugin Permission / Sandbox / Isolation。

| 维度 | Tool | Plugin |
|------|------|--------|
| 谁使用 | Agent / Workflow | Fluxion Runtime / Platform |
| Agent 可直接调用 | 是 | 否 |
| 用户可授权 | 是 | 否 |
| Capability Resolution | 参与 | 不参与 |
| 生命周期 | Tool Definition | Plugin install/load/enable/disable |
| 权限模型 | Capability Governance | Plugin Permission / Sandbox |

**关键不变量**：

- **Plugin 可「提供 Tool 实现」≠ Plugin 就是 Tool**：Plugin 注册 Tool Executor，Agent 看到和授权的是 ToolDefinition（如 `query_customer_database`），不是 Plugin（如 `database-plugin`）。
- **`ModelProvider` / `MCP Server` / `Channel` 均 ≠ Plugin**：即使未来 Plugin SPI 实现这些 Adapter，也不得在领域模型里把它们重新定义成 `PLUGIN` Resource。

**代价**：

- 存量 `PLUGIN(plugin_type=tool_provider)` 需拆分：Tool 侧迁到 Tool Resource，扩展侧落到 Plugin SPI（与 ADR-A008 的 `PLUGIN(model_provider)` 退出模型链同向）；
- `PluginType.TOOL_PROVIDER` 从「Tool 的类型」降级为「Tool 的 SPI 实现载体」。

**失败模式**：

- 若 Plugin 重新进入 Agent 能力配置或 Capability Resolution，本 ADR 边界被打破，需重新决策；
- 若 `ModelProvider`/`MCP Server`/`Channel` 被定义为 `PLUGIN` Resource，则领域模型回归历史混淆。

**验收**：

- 领域模型无 `PluginType.TOOL_PROVIDER` 承载 Tool；Tool 为一等 Capability Resource；
- Plugin 不出现在 Agent 能力配置与 Capability Resolution；
- Contract Test 覆盖「Plugin 提供 Tool 实现时，Agent 授权对象是 Tool 而非 Plugin」。

**重新评估条件**：

- 若 Plugin 需要被用户感知/授权（进入 Agent 能力配置），说明边界需要调整，重新决策。
