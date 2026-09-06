# ADR-A008 Model 领域第二阶段：ModelDefinition 一等资源 + 消灭双事实源

**引用**：ADR-A007、REQ-EXE-001、ARCH-09 Typed Spec SoT、规则 25（Contract 变更须经 ADR）。

**背景**：

ADR-A007 决策了 `ProviderDefinition（连接）→ ModelDefinition（模型）→ ModelPolicy（运行机制）` 三层，但把「ModelDefinition 拆为一等资源、消灭双事实源」留作第二阶段未展开。当前代码现实：

- `ResourceKind.MODEL` 语义是「模型供应商」而非「模型名」（ADR-A007 §25）；
- `PLUGIN` 经 `plugin_type="model_provider"`（`resources/contracts.py:303`）又表达 provider；
- 二者并存，构成 `MODEL + PLUGIN(model_provider)` 双事实源，Console 与 Resolver/Runtime 无法保证同一解析。

**候选**：

1. 仅把 `PLUGIN(model_provider)` 迁到 `MODEL`，不拆 ModelDefinition（单层，模型名仍是自然键）；
2. 落地三层：`ProviderDefinition` + `ModelDefinition` 一等资源 + `ModelPolicy` 结构化字段，废弃 `MODEL`、`PLUGIN(model_provider)` 退出模型链；
3. 保持现状，仅加注释。

**决策**：选 2（开发阶段，接受重构，一次性收口）。

三层定型：

- **`ProviderDefinition`**（Resource，`kind=model_provider`）＝连接维度：`protocol` / `base_url` / `credential_ref` / `default_model` / `request_timeout_ms` / `max_retries`。
- **`ModelDefinition`**（Resource，`kind=model_definition`）＝模型身份 + provider 映射：`name`（自然键，如 `deepseek-chat`）/ `provider_ref`（`ExactResourceVersion`）/ `capabilities`（`context_window` / `tool_calling` / `vision` / `max_tokens`）。
- **`ModelPolicy`**（AgentDefinition 结构化字段，**非独立 Resource**）＝路由 + 执行机制：`primary_model_ref`（`ExactResourceVersion`）/ `fallback_model_refs`（`list[ExactResourceVersion]`）/ `model_timeout_ms` / `model_deadline_ms`。

消灭双事实源：

- 废弃 `ResourceKind.MODEL`（存量迁移为 `ProviderDefinition`）；
- `PLUGIN(plugin_type=model_provider)` 退出模型运行链；`PluginType.MODEL_PROVIDER` 仅保留 SPI 协议层（`plugins/model_provider.py` 的注册/加载），不再作为 Console 可配置的模型资源。

timeout / retry / failover 归属切分（消除 ModelPolicy 与 RuntimeProfile 重叠）：

| 维度 | 归属 | 字段 |
|------|------|------|
| HTTP 连接超时/重试 | `ProviderDefinition` | `request_timeout_ms` / `max_retries` |
| 模型调用超时/截止 | `ModelPolicy` | `model_timeout_ms` / `model_deadline_ms` |
| 路由/回退 | `ModelPolicy` | `primary_model_ref` / `fallback_model_refs` |
| Agent 运行机制 | `RuntimeProfile` | `max_rounds` / `concurrency` / `budget` |

跨 provider failover：`ModelDefinition` 同名可多实例（`deepseek-chat@providerA` / `deepseek-chat@providerB`），`ModelPolicy.fallback_model_refs` 引用另一 `ModelDefinition` exact version 实现回退。

**代价**：

- Contract 变更（规则 25），存量 fixture/API/UI 需迁移：`MODEL` → `ProviderDefinition`，`PLUGIN(model_provider)` → `ProviderDefinition`；
- `AgentDefinition` 增 `model_policy` 字段（`primary_model_ref` / `fallback_model_refs`），原 `model_ref` 语义由 ModelPolicy 承接；
- 不保留 `MODEL` / `PLUGIN(model_provider)` 兼容层（同类迁移已开先例）。

**失败模式**：

- `ModelPolicy.primary_model_ref` 指向的 ModelDefinition 缺失 → 无模型可解析 → fail-closed 不发起调用；
- `ModelDefinition.provider_ref` 缺失 → 无连接可建立 → fail-closed；
- fallback 链全部失败 → 返回 `model_provider_error`，不静默降级。

**验收**：

- `ProviderDefinition` / `ModelDefinition` 为 typed versioned Resource（`extra=forbid`），经 `_validate_definition` 严格校验；
- `ModelPolicy` 为 `AgentDefinition` 结构化字段，引用均为 `ExactResourceVersion`，进 ExecutionSnapshot 冻结 exact version；
- `PLUGIN(model_provider)` 不出现在 Snapshot 模型解析链；
- Contract Test 覆盖 provider/model resolve 与跨 provider failover（`test_snapshot_resolution` + `test_model_provider`）。

**重新评估条件**：

- 若「同一模型名跨大量 provider」需求密度暴增，可把 `ModelDefinition` 进一步拆为「模型身份」与「provider binding」两层；否则维持本 ADR 的三层定型。

---

## Amend 2026-09-03：Provider Credential 真相源统一与 Binding 逻辑 ID 继承

**引用**：ADR-A003（amend 2026-09-03）、REQ-SEC-002、ARCH-04、ARCH-09、规则 25。

**背景**（2026-09-02 核实）：

- 本 ADR 定型的 `ProviderDefinition.credential_ref`（必填）与 `ResourceBinding.credential_ref`（`kind=model_provider` binding）构成新的双事实源：Console 连接测试/发布校验读 spec（`connection_test.py:73-75`、`console_resource_validation.py:203-238`），Runtime 只读 binding（`runtime/model_providers.py:96-131`，优先级仅 User → Tenant，无 spec 回退）；
- 无任何 binding 时 Runtime 报 `model provider binding not found`——「Console 配了 credential 却跑不起来」；
- 更隐蔽：binding 存在但 `credential_ref=None` 时 Runtime 以 `api_key=None` 静默出站裸调（`model_providers.py:122-123`）；
- `resource_version_selector` 在 MODEL_PROVIDER binding 匹配中被完全忽略（`:109-116` 仅比对 `resource_id`），Console 却允许填精确版本——死配置制造「已绑版本」错觉。

**决策**：

1. **EffectiveCredential 单链**（消灭双源）：

```text
EffectiveCredential =
      User Binding Credential Override
      ?? Tenant Binding Credential Override
      ?? ProviderDefinition.credential_ref（默认真相源）
```

   - **Binding 是 Override，不是 Provider 可运行的强制前提**：仅配置 spec `credential_ref`（无任何 binding）即可运行；
   - 链上无可解析 credential → fail-closed `provider_credential_unresolvable`，禁止 `api_key=None` 出站；
   - Snapshot 冻结最终选择的 Credential Ref / Version（ADR-A003 amend）。

2. **Binding 逻辑 ID 继承**（方案 B，做干净）：

   - MODEL_PROVIDER Binding 绑定**逻辑 Provider ID**，credential/override 跨 Provider 版本继承（credential 是连接级配置，不随 Provider 版本变化）；
   - **移除版本 selector 死配置**：Console 建 model-provider binding 不再暴露 `version_selector` 输入；`resource_version_selector` 字段语义仅保留给有版本语义的 binding kind（policy/skill 等），对 MODEL_PROVIDER 无效；
   - 未来出现版本级敏感参数时引入显式 typed override 字段，不允许隐式混用。

3. **发布校验**：Provider 发布（validate-publish）检查 `spec.credential_ref` 指向的 Secret 存在且可用。

**代价**：

- Console binding 创建表单与 `console_governance` 契约调整（model-provider 类型去掉 version 输入）；
- Runtime credential 解析链重写（spec 回退 + fail-closed）；
- 既有「必须建 binding 才能跑」的 fixture/e2e 全部需要重新审视（部分应改为只配 spec）。

**失败模式**：

- User/Tenant binding 存在但其 `credential_ref` 指向的 Secret 已删除 → **跳过该 override 继续回退**还是 **fail-closed**？——fail-closed：显式 override 指向的 Secret 缺失是配置错误，不得静默降级到 spec credential（错误码 `provider_credential_unresolvable`，信息标明是哪级 override 失效）；
- spec `credential_ref` 为空或指向 Secret 缺失（且无可用 override）→ 发布校验拦截；运行期兜底 fail-closed。

**验收**：

- 仅配置 `ProviderDefinition.credential_ref`（无任何 binding）→ 模型调用成功（B-S-02）；
- override 优先级 User > Tenant > spec 契约测试（B-S-03）；
- 链上无可解析 credential → fail-closed，无 `api_key=None` 出站（B-E-02）；
- MODEL_PROVIDER binding 匹配语义（逻辑 ID、selector 不参与）钉死契约测试（B-E-03）。

**重新评估条件**：

- 若 Provider 出现版本级敏感参数（如某版本起强制 mTLS client cert），引入 typed override；届时再评估 binding 是否需要版本感知。

