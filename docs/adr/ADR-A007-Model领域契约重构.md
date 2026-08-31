# ADR-A007 Model 领域契约重构（ProviderDefinition → ModelDefinition → ModelPolicy）

**引用**：REQ-AGT-001、REQ-EXE-001、ARCH-09 Typed Spec SoT、ARCH-12 Core Contract 扩展。

**背景**：

当前 Model 领域有两个 typed model，但职责混合：

- `ModelProviderDefinition` 把「连接信息」（protocol/base_url/credential_ref）、「模型选择」（model）、「重试参数」（request_timeout_ms/max_retries）绑在同一资源里——同一供应商换模型、或同模型换连接，都必须复制整个 provider 资源；
- `ModelPolicy` 把「引用」（provider/failover/model，均为裸 string）与「运行机制」（timeout_ms/deadline_ms/max_rounds）绑在同一对象里，且 provider/model 用裸 string 引用、无版本 pin，违反 ARCH-09 单一 typed 真相源（引用型字段应是 `ExactResourceVersion`）。

**候选**：

1. 保持现状（不拆分），仅把 ModelPolicy 内裸 string 改为 `ExactResourceVersion`；
2. 拆成三层 `ProviderDefinition`（连接）→ `ModelDefinition`（模型）→ `ModelPolicy`（运行机制 + typed 引用），三者为独立 versioned Resource；
3. 最小重命名：仅把 `ModelProviderDefinition` 改名 `ProviderDefinition`，不动字段。

**决策**：选 2（分两阶段落地，本 ADR 收口第一阶段）。

第一阶段（本次落地）：

- `ProviderDefinition`（Resource，kind=model_provider，由 `ModelProviderDefinition` 更名，`MODEL`/`PLUGIN` 两 kind 均映射它）：`protocol` / `base_url` / `credential_ref` + 默认 `model`（连接与凭据 + 默认模型；`request_timeout_ms`/`max_retries` 归连接维度）；
- `ModelPolicy`（保持结构化、非独立 Resource）：`provider` → `provider_ref: ExactResourceVersion`、`failover` → `list[ExactResourceVersion]`（version pin，REQ-EXE-002）；`model` 保留为 string（模型名是自然键、无版本语义）；`timeout_ms` / `deadline_ms` / `max_rounds` 保留为运行机制。

第二阶段（后续，本 ADR 不展开）：`ResourceKind.MODEL` 当前语义是「模型供应商」（studio models 视图），不是「模型名」；要把模型名从 `ProviderDefinition` 拆出为一等资源，需先引入独立 `ResourceKind`（如 `model_definition`）并让 `AgentDefinition` 增模型引用字段——模型名与 provider 完全解耦。

**代价**：

- 契约级变更（规则 25），存量 fixture/API/UI 需迁移（`allowed_tools`→`required_capabilities` 同类迁移已开先例，不保留兼容层）；
- `ModelProviderDefinition` 的 `request_timeout_ms`/`max_retries` 归属需定：连接超时/重试随 `ProviderDefinition` 承载（连接维度），执行超时/截止随 `ModelPolicy`（执行维度）——避免重试语义跨层重复。

**失败模式**：

- `ModelPolicy.provider_ref` 引用缺失 → 无 provider 可解析，`_provider_chain` 返回空 → 不发起模型调用（fail-closed）；
- provider/failover 引用为 typed 后，version pin 缺失时由 `ExactResourceVersion` 强制 version 字段，不再静默裸 string。

**验收**：

- `ProviderDefinition` 为 typed versioned Resource，经 `_validate_definition` 严格校验（extra=forbid）；
- `ModelPolicy.provider_ref`/`failover` 为 `ExactResourceVersion`，ExecutionSnapshot 冻结 exact version（REQ-EXE-002）；
- Contract Test 覆盖 provider 引用 resolve 与 fail-closed 路径。

**重新评估条件**：

- 若出现「同一 provider 大量模型、或同一模型跨多个 provider」的需求密度，证明三层拆分值得独立 versioned 资源；否则可降级为候选 3（仅重命名）并撤回本 ADR。
