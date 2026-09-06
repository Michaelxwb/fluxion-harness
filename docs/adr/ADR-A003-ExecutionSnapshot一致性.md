# ADR-A003 ExecutionSnapshot 一致性

Execution 开始时固定所有影响行为的资源/绑定/策略版本。执行中发布只影响下一次 Execution。

Snapshot 必须不仅能算 digest，还能追溯实际 EffectiveCapability 与相关 Binding/Policy exact version。

---

## Amend 2026-09-03：typed pins 定型与运行期消费收口

**引用**：ADR-A010、ADR-A011、REQ-EXE-002、ARCH-07、规则 25。

**背景**（2026-09-02 核实）：

- Snapshot 以 `plugin_versions` 承载模型 provider pins（`plugin_versions = model_provider_pins`，`context_resolver.py:335`），字段语义混用；消费方 `runtime_tool_ops.py:59`、`runtime/agent.py:402`、`console_payloads.py:116` 把它当 provider pin 读；
- `credential_versions` 构建时冻结，但全后端无运行期消费者；运行期（`runtime/model_providers.py:77-131`）每次模型调用实时重 resolve provider spec / binding / credential，不读 Snapshot——执行中配置漂移可影响进行中的 Execution，违背「执行不可变」；
- `memory_policy_ref` / `personalization_policy_ref` / `workflow_ref` 契约字段自引入以来从未被赋值；
- ModelDefinition 版本未 pin（`ResolvedModelRoute` 仅 `provider_ref: ExactResourceVersion` + `model: str` 模型名字符串）。

**决策**：

1. **typed pins 定型**（`resources/contracts.py` ExecutionSnapshot 契约）：

```text
skill_versions      （既有，保留）
mcp_versions        （既有，保留）
policy_versions     （既有，保留）
credential_versions （既有，保留；语义 = 运行期解密所依据的 Secret 版本）
model_versions      （新增：ModelDefinition exact version pin）
provider_versions   （新增：ProviderDefinition exact version pin）
plugin_versions     （废弃：模型 provider pins 迁移至 provider_versions；迁移后字段从契约移除）
```

2. **运行期消费收口**：Provider / Binding 选择、Credential 引用选择在 Snapshot 构建期（ContextResolver）一次收口；运行期只按冻结的 ref/version 解密（SecretStore 读取）与实例化 provider，不得重新选择。Runtime 不再自行决定用哪个 Provider / Credential / Memory source（§4.5 整改要求）。
3. **`ResolvedModelRoute` 补 ModelDefinition exact version pin**（模型路由从「provider 版本 + 模型名」升级为「provider 版本 + 模型版本」）。
4. **未赋值字段处置**：`memory_policy_ref` / `personalization_policy_ref` 从 Snapshot 契约移除（memory 以 `memory_manifest` entry refs 摘要承载）；`workflow_ref` 不进 Agent ExecutionSnapshot——workflow 执行上下文由 durable workflow 自身状态承载（规则 13：Workflow durable state 不进入 Agent Runtime）。

**代价**：

- `plugin_versions` 迁移涉及三个运行期消费方与既有测试断言；
- 运行期不再实时重选 binding：执行开始后新建的 user binding override 对**进行中**的 Execution 不生效（下一次 Execution 生效）——这是「执行不可变」的应有语义。

**失败模式**：

- 运行期按冻结 ref 解密时 Secret 已被删除 → fail-closed `credential_unresolvable`（明确报错，不以 `api_key=None` 出站）；
- 冻结的 provider/model 版本在 Registry 已 deprecate → 执行继续用冻结版本完成（published 不可变版本仍可解析），仅记录告警。

**验收**：

- typed pins 契约测试：`provider_versions` / `model_versions` 精确 pin，`plugin_versions` 不再出现（B-S-06）；
- 同一 Execution 期间发布新 provider 版本 / 新增 binding 不影响执行中版本与 credential 选择（B-S-07）；
- 生产装配下 `credential_versions` 为真实解析版本而非占位（B-E-04，配合 ADR-A010 同期整改的装配注入）。

**重新评估条件**：

- 若 Memory policy 资源化立项（个人记忆策略成为 versioned Resource），恢复 `memory_policy_ref` 并赋值；
- 若 Binding 出现版本级敏感参数（见 ADR-A008 amend），评估 pin 粒度是否需细化到 binding 版本。
