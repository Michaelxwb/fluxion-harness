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
