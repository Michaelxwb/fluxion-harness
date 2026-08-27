# Tasks: Unified Extension Model（ADR-EXT-001）

- **Source**: `.code-flow/tasks/2026-08-26/adr-ext-001-extension-model/adr-ext-001-extension-model.design.md`
- **Created**: 2026-08-27
- **Updated**: 2026-08-27

## Proposal

Phase 0 ADR 级 contract-shaping：把 `PluginType` 死类型（TOOL/MEMORY/STORAGE）删除/改名，定义 6 个保留类型各自的 typed Provider SPI Protocol + Registry Protocol，把 `PluginLoader._register_model_provider` 特例泛化为 per-PluginType registry 分派（reference binding，非生产），并写 architecture import-lint test 阻断 `kernel/` 反向依赖 concrete provider。不枚举 Phase 1/5 具体 provider 实现（pgvector/S3/SecretProvider 生产实现延后，design §11 Rolling-wave 禁止提前枚举）；S-01/S-04 用假实现 plugin 证明分派与既有 ADR-010 isolation 仍生效。

### Alignment

- **Scope**: (1) 6 个保留 PluginType 的 Provider SPI Protocol 形状；(2) PluginLoader per-PluginType registry 分派泛化（reference binding）；(3) dead PluginType 删除（STORAGE 拆分、TOOL→TOOL_PROVIDER、MEMORY 由 ADR-MEM-001 删除）；(4) Plugin 作为 versioned Resource 的 SecretRef/Binding credential 路径；(5) architecture import-lint test。
- **Decisions**:
  - 契约/绑定分离：TASK-001 只定义 Protocol（纯 contract 层）；in-memory typed registry 实例 + loader 分派归 TASK-002（reference binding）。
  - B-03 由 design 标 `manual→static`，落地转为 architecture test/grep（static），原因：当前无 file-location lint 工具（design §2.5.2 已记录）。
  - S-01/S-04 用假实现（test double）plugin 证明分派/isolation，不引生产 provider。
- **Non-goals**: Phase 1/5 具体 provider 实现（pgvector/S3/SecretProvider resolve）；MEMORY_PROVIDER 终态（已由 ADR-MEM-001 决议 delete）；trust/isolation 重决（ADR-010 已落地，本 ADR 只引用）；前端面（无前端 surface）。
- **Acceptance**: S-01..S-04 + E-01/E-02 + B-01..B-03 全 GREEN；6 条 required Rule 各有唯一 owner 且 verifier 落地；§8 ADR-006/010 对齐声明已在 design。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | E2E | import graph: `kernel/` + `plugins/loader.py` → 只到 Protocol 模块 | TASK-002 | verified |
| S-02 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | integration | `ToolProvider.capabilities()` → `CapabilityDescriptor`；ToolRuntime dispatch | TASK-003 | verified |
| S-03 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | integration | `resource_definitions` 行 + `resource_bindings.credential_ref` | TASK-004 | verified |
| S-04 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | E2E | trust_level → execution_mode 分派 + fault injection | TASK-005 | verified |
| E-01 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | integration | registry + `_loaded` state after exception | TASK-002 | verified |
| E-02 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | integration | import-lint 静态测试 | TASK-002 | verified |
| B-01 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | unit | `runtime_checkable` Protocol 检查 | TASK-001 | verified |
| B-02 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | unit | `PluginType` enum 成员 | TASK-001 | verified |
| B-03 | adr-ext-001-extension-model.design.md#2.5.2 功能验收场景 | static | file-location lint（design 标 manual→static，落地转 architecture test，已记录原因） | TASK-001 | verified |

> 本表覆盖 design 全部 P0 场景（S-01..S-04）+ 异常（E-01/E-02）+ 边界（B-01..B-03）；RULE-EXT-01→S-01/E-02、RULE-EXT-02→B-02、RULE-EXT-03→S-04、RULE-EXT-04→S-03；RISK-01→E-01、RISK-02→E-02、RISK-03→B-02、RISK-05→B-03 均映射。无缺口。

---

## TASK-001: Provider SPI Contract 形状 + PluginType enum 终态 + 契约目录落点

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: adr-ext-001-extension-model.design.md#2.3.2 字段约束, adr-ext-001-extension-model.design.md#3.4 接口设计, adr-ext-001-extension-model.design.md#3.2 架构设计
- **Spec-Refs**: backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: B-01, B-02, B-03

### Description

定义 5 个新 Provider SPI Protocol（ToolProvider / SemanticStoreProvider / ArtifactStoreProvider / SecretProvider）+ 各自 Registry Protocol + HookRegistryProtocol（对齐 ADR-007 typed-lifecycle-hook）；PluginType enum 终态化（删 TOOL/MEMORY/STORAGE，新增 TOOL_PROVIDER/ARTIFACT_STORE/SEMANTIC_STORE/SECRET_PROVIDER，补 HOOK——当前 enum 无 HOOK）；契约落点 `plugins/contracts.py`，超 500 行（CLAUDE.md 硬约束）拆 `plugins/providers/` 子包，深度 ≤ 3。纯 contract 层：只定义 Protocol + enum + Manifest 字段，不含 in-memory registry 实例与 loader 分派（归 TASK-002）。ModelProvider/ModelProviderRegistryProtocol（`contracts.py:125/143`）为参考实现不动。

### Checklist

- [x] [B-01][unit] 定义 5 Provider SPI Protocol（`@runtime_checkable`，tenant_id/user_id 首参强制），真实边界=Protocol + 假实现 test double，断言：缺 `search` 的 SemanticStoreProvider 假实现 → `isinstance` 校验拒绝。先写测试记录 RED
- [x] [B-02][unit] PluginType enum 终态 = {MODEL_PROVIDER, TOOL_PROVIDER, ARTIFACT_STORE, SEMANTIC_STORE, SECRET_PROVIDER, HOOK}，真实边界=enum，断言：旧 TOOL/MEMORY/STORAGE 值引用报 AttributeError。先写测试记录 RED
- [x] [B-03][static] architecture test/grep，真实边界=文件落点，断言：Provider SPI Protocol 仅在 `plugins/contracts`（或 `plugins/providers/`）定义，深度 ≤ 3，不散落 `kernel/`/`services/`。design 标 manual→static，记录原因：当前无 file-location lint 工具
- [x] [backend-directory-structure#RULE-backend-directory-001] verifier：`grep -rn "class .*Provider(Protocol)\|class .*RegistryProtocol(Protocol)" src/fluxion/plugins/` 仅命中 contracts/providers 子包 + 目录深度 ≤ 3 断言
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | unit | `runtime_checkable` Protocol + 假实现 test double | 缺 required method（SemanticStore 缺 `search`/Secret 缺 `resolve`/Artifact 缺 `delete`）→ `isinstance` 拒绝；完整假实现→接受；ToolProvider=CapabilityProvider 别名；`resolve` 签名 tenant_id 首参 | `tests/unit/test_provider_contracts.py::test_b01_semantic_store_isinstance_rejects_missing_search` 等 5 例 | `uv run pytest backend/tests/unit/test_provider_contracts.py -k b01 -v` | verified |
| B-02 | unit | `PluginType` enum 成员 | enum 终态 = {MODEL_PROVIDER,TOOL_PROVIDER,ARTIFACT_STORE,SEMANTIC_STORE,SECRET_PROVIDER,HOOK} 6 成员；旧 TOOL/MEMORY/STORAGE 引用 AttributeError；value 字符串稳定 | `tests/unit/test_provider_contracts.py::test_b02_plugin_type_enum_terminal_members` 等 3 例 | `uv run pytest backend/tests/unit/test_provider_contracts.py -k b02 -v` | verified |
| B-03 | static | 文件落点（AST architecture test） | plugins/ 内 (Provider\|RegistryProtocol) Protocol 仅 contracts/providers；深度 ≤3；6 SPI `__module__=="fluxion.plugins.contracts"`；ModelProvider 参考实现未破坏 | `tests/unit/test_provider_contracts.py::test_b03_six_spis_defined_in_contracts_only` 等 4 例 | `uv run pytest backend/tests/unit/test_provider_contracts.py -k b03 -v` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-01 | FAIL: `ImportError: cannot import 'SemanticStoreProvider'/'SecretProvider'/'ArtifactStoreProvider'/'ToolProvider'`（contracts.py 未定义 4 新 SPI）+ `resolve` 签名无 tenant_id 首参 | PASS: `-k b01` 5 passed（isinstance 拒绝缺方法假实现、接受完整假实现；ToolProvider is CapabilityProvider；`inspect.signature(resolve)` 参数序 = [self,tenant_id,secret_ref]） | `tests/unit/test_provider_contracts.py:62-145`（isinstance True/False 断言 + 签名序断言） | `@runtime_checkable` Protocol + test double（非 mock），运行时真实 `isinstance` 校验；`contracts.py:SemanticStoreProvider/ArtifactStoreProvider/SecretProvider` | verified |
| B-02 | FAIL: `AttributeError: PluginType has no attribute 'TOOL_PROVIDER'`；旧 TOOL/MEMORY/STORAGE 仍存在（DID NOT RAISE）；value 字符串不一致 | PASS: `-k b02` 3 passed（set(PluginType)==6 成员；旧 TOOL/MEMORY/STORAGE raise AttributeError；value 字符串稳定） | `tests/unit/test_provider_contracts.py:152-176` | `PluginType(StrEnum)` 真实 enum 成员枚举；`contracts.py:14-21` | verified |
| B-03 | FAIL: `test_b03_six_spis_defined_in_contracts_only`（6 SPI 未定义→getattr None）；`test_b03_provider_protocols_in_plugins_only...` green-before（既有 ModelProvider/CapabilityProvider/ModelProviderRegistryProtocol 已正确落点 contracts）+ `test_b03_model_provider_remains_reference_contract` green-before | PASS: `-k b03` 4 passed（plugins/ 内 Provider/RegistryProtocol Protocol 全落点 contracts；深度 ≤3；6 SPI `__module__=="fluxion.plugins.contracts"`；ModelProvider 参考实现未破坏） | `tests/unit/test_provider_contracts.py:189-243` | AST 扫描 `src/fluxion/plugins/` + `__module__` 精确断言（非 mock，真实源码 AST） | verified |

> 回归：`uv run pytest backend/tests/unit/ backend/tests/integration/ --ignore=backend/tests/workflow_poc -q` → **118 passed**（enum rename 未破坏 loader/model_provider/trust 等既有路径）。
>
> B-03 说明（cf-task:start 规则 #7）：静态落点测试在 RED 阶段既有 Provider Protocol 已正确落点 `contracts.py`，故 `test_b03_provider_protocols_in_plugins_only...`/`depth`/`ModelProvider reference` 三例 green-before；RED 由 `test_b03_six_spis_defined_in_contracts_only`（6 新 SPI 未定义）承载。GREEN 后 4 例全绿验证新增 6 SPI 落点。
>
> B-03 范围说明：design verifier 显式 grep `src/fluxion/plugins/`（plugins-scoped）；本测试按 design 范围扫描 `plugins/` + 精确 6 SPI `__module__` 断言。`services/approval.py:ApprovalProvider` 非 6 统一模型 SPI（Approval 非 PluginType），不在 ADR-EXT-001 范围，保持不动（surgical）。
>
> 真实边界无 mock 绕过：B-01 用 test double（非 mock 框架）+ 运行时 `isinstance`；B-02 直接 enum 自省；B-03 AST 扫描真实源码。TASK-001 无外部凭据/真实 LLM 调用（纯契约形状），S-P13-07 约束不适用。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done) — B-01/B-02/B-03 verified（15 acceptance + 118 regression passed）；6 SPI Protocol + Registry 落点 contracts.py；PluginType 终态 6 成员；ToolProvider=CapabilityProvider 别名；test_plugin_trust.py TOOL→TOOL_PROVIDER 已同步

---

## TASK-002: PluginLoader 泛化（per-PluginType registry 分派）+ 回滚 + Kernel 契约隔离 architecture test

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-ext-001-extension-model.design.md#3.4 接口设计, adr-ext-001-extension-model.design.md#3.5 质量实现方案, adr-ext-001-extension-model.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-01, E-01, E-02

### Description

`_register_model_provider`（`loader.py:99-112`，仅接 MODEL_PROVIDER）特例泛化为 `_register_provider` + `_REGISTRY_BY_TYPE` 分派（design §3.4 reference binding，非生产；in-memory typed registry 实例实现 Registry Protocol 注入）。沿用既有回滚（`loader.py:78-81` pop `_loaded`/`_records` + `shutdown()`，不静默吞异常）。写 architecture import-lint test 阻断 `kernel/`→`plugins/<concrete>` 反向依赖 + provider 路径 `spec_json.get` 反模式。用假实现 plugin 证明分派，不引生产 provider。MEMORY 已由 ADR-MEM-001 删除，不在 `_REGISTRY_BY_TYPE`。

### Checklist

- [x] [S-01][E2E] 真实边界=import graph(`kernel/`+`plugins/loader.py`)+假实现 plugin，断言：加载 SemanticStore/ArtifactStore/SecretProvider 假实现 → 按 PluginType 分派进对应 typed registry；`kernel/`+`loader.py` 全程不 import 具体 impl 模块。先写测试记录 RED
- [x] [E-01][integration] 真实边界=registry + `_loaded` state，断言：provider `setup()` 抛异常 → 无 partial registry entry、无残留 `_loaded` 记录（沿用 `loader.py:76-82` 回滚）。先写测试记录 RED
- [x] [E-02][integration] 真实边界=import-lint 静态测试，断言：`kernel/` import `plugins/<concrete>` 或 provider 路径用 `spec_json.get` → architecture test 失败阻断。先写测试记录 RED
- [x] [fluxion-runtime-core#RULE-fluxion-runtime-001] verifier：import-lint 命令（grep `kernel/` 不 import `plugins/<concrete>`）+ S-01 dispatch 不触达具体 impl 断言
- [x] [backend-code-quality-performance#RULE-backend-quality-001] verifier：E-01 回滚断言 + `_register_provider` 类型注解/不静默吞异常 + B-01 Protocol isinstance 校验（B-01 由 TASK-001 负责场景，本任务引用为依赖）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | import graph: `kernel/`+`plugins/loader.py` → 只到 Protocol 模块；假实现 plugin（test double，非 mock 框架） | PluginLoader 按 PluginType 分派进对应 typed registry（Semantic/Artifact/Secret 各自命中）；kernel/+loader.py 全程不 import 具体 impl 模块 | `tests/integration/test_plugin_loader_dispatch.py::test_s01_dispatches_fake_providers_to_typed_registries` + `::test_s01_loader_imports_only_contracts_protocol` | `uv run pytest backend/tests/integration/test_plugin_loader_dispatch.py -k s01 -v` | verified |
| E-01 | integration | registry + `_loaded` state after exception（真实 PluginLoader + 真实 InMemoryProviderRegistry，非 mock） | provider `setup()` 抛异常 → 无 partial typed registry entry、无残留 `_loaded`/`_records` 记录（沿用 `loader.py:76-82` 回滚） | `tests/integration/test_plugin_loader_dispatch.py::test_e01_setup_failure_leaves_no_partial_typed_registry` + `::test_e01_registration_failure_rolls_back_loaded_state` | `uv run pytest backend/tests/integration/test_plugin_loader_dispatch.py -k e01 -v` | verified |
| E-02 | integration | import-lint 静态测试（AST 扫描真实源码） | `kernel/` import 任何 `plugins/<concrete>` → 失败；`plugins/loader.py` import `plugins/<concrete>` → 失败；provider 路径用 `spec_json.get` → 失败 | `tests/unit/test_plugin_architecture.py::test_e02_kernel_no_concrete_plugin_import` + `::test_e02_loader_no_concrete_plugin_import` + `::test_e02_no_spec_json_get_in_provider_paths` | `uv run pytest backend/tests/unit/test_plugin_architecture.py -v` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: `test_s01_dispatches_fake_providers_to_typed_registries` → `AttributeError: 'PluginLoader' object has no attribute '_registries'`（typed 分派未实现）；`test_s01_loader_imports_only_contracts_protocol` green-before（loader 已只 import contracts） | PASS: `-k s01` 2 passed（Semantic/Artifact/Secret 假实现各自分派进对应 typed registry、不串台；loader.py AST import=={fluxion.plugins.contracts}） | `tests/integration/test_plugin_loader_dispatch.py:121-142`（typed registry resolve + provider_ids 不串台断言）、`:162-170`（AST import 白名单断言） | 真实 `PluginLoader` + 真实 `InMemoryProviderRegistry`（`loader.py:InMemoryProviderRegistry`）；假实现 plugin 为 test double（非 mock 框架），运行时真实 `isinstance` 校验 + 真实 `register/resolve`；AST 扫描 `loader.py` 真实源码 import | verified |
| E-01 | FAIL: `test_e01_setup_failure...` → `AttributeError: no attribute '_registries'`（typed registry 未实现）；`test_e01_registration_failure_rolls_back_loaded_state` → `DID NOT RAISE PluginLoadError`（loader 未对缺 protocol 的 typed provider 做 isinstance 拒绝） | PASS: `-k e01` 2 passed（setup 抛异常→semantic typed registry 空 + `_loaded` 空；缺 protocol→`PluginLoadError "lacks"` + 回滚 `_loaded` 空、typed registry 无 partial entry） | `tests/integration/test_plugin_loader_dispatch.py:226-243`（setup 失败 + 注册失败双回滚断言） | 真实 `PluginLoader`（`load():76-82` 既有回滚 pop `_loaded`/`_records` + `suppress(shutdown)` 非静默吞）+ 真实 `InMemoryProviderRegistry.provider_ids()` 空断言 | verified |
| E-02 | green-before：`test_e02_kernel_no_concrete_plugin_import`/`test_e02_loader_no_concrete_plugin_import`/`test_e02_no_spec_json_get_in_provider_paths` 三例 RED 阶段已通过（kernel/ 已不 import plugins、loader.py 已只 import contracts、plugins/ 无 spec_json.get） | PASS: `-v` 3 passed（kernel/ 无 concrete plugin import；loader.py 无 concrete import + plugin import ⊆ {contracts}；plugins/ 无 `spec_json.get` 调用） | `tests/unit/test_plugin_architecture.py:55-58`（kernel concrete import 断言）、`:65-72`（loader concrete + contracts 白名单断言）、`:99-101`（spec_json.get AST 断言） | AST 扫描 `src/fluxion/kernel/` + `plugins/loader.py` + `plugins/` 真实源码 import/Call 节点（非 mock） | verified |

> E-02 说明（cf-task:start 规则 #7）：E-02 为静态 import-lint 守卫测试。RED 阶段 `loader.py` 已只 import contracts、`kernel/` 已不 import plugins、`plugins/` 无 `spec_json.get`，故三例 green-before。TASK-002 真实 RED 由 S-01（typed dispatch 未实现：`_registries` 不存在→AttributeError）与 E-01（lacks-protocol 未拒绝→DID NOT RAISE）承载。GREEN 后 E-02 守卫证明泛化未引入 concrete import 或 `spec_json.get` 回归。
>
> 真实边界无 mock 绕过：S-01/E-01 用真实 `PluginLoader` + 真实 `InMemoryProviderRegistry` + test double plugin（非 mock 框架）；E-02 AST 扫描真实源码。TASK-002 无外部凭据/真实 LLM 调用（纯分派契约 + 静态 import-lint），S-P13-07 约束不适用。
>
> 回归：`uv run pytest backend/tests/unit backend/tests/integration --ignore=backend/tests/integration/test_workflow_poc.py -q` → **125 passed**（TASK-001 的 118 + TASK-002 新增 7；loader 泛化未破坏 trust/model_provider/boundaries 既有路径，`MinimalPlugin` TOOL_PROVIDER 无 `capabilities()` 仍可加载、`BrokenModelPlugin` 仍 match "lacks complete"）。

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done) — S-01/E-01/E-02 verified（7 acceptance + 125 regression passed）；`_register_model_provider`→`_register_provider` per-PluginType 分派（`_PROVIDER_PROTOCOL` map + `InMemoryProviderRegistry`）；MODEL_PROVIDER 路径不变（`MinimalPlugin`/`BrokenModelPlugin` 既有断言不破坏）；E-02 import-lint 守卫证明 kernel/loader 无 concrete import、无 `spec_json.get` 回归

---

## TASK-003: TOOL_PROVIDER ↔ Capability Contract 对齐 + ToolRuntime dispatch

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: adr-ext-001-extension-model.design.md#3.4 接口设计, adr-ext-001-extension-model.design.md#2.5.2 功能验收场景
- **Spec-Refs**: fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-02

### Description

TOOL_PROVIDER SPI（TASK-001 已定义形状）与 ADR-009 Capability Contract 对齐：`ToolProvider.capabilities() -> list[CapabilityDescriptor]`；Tool 是 Agent-facing Adapter，业务逻辑在 Capability（RULE-EXT 痛点"Tool 是 Agent-facing Adapter，Capability 才承载业务能力"）。ToolRuntime 经 Capability Contract 解析 dispatch tool 调用。验证 tool 调用链对齐，不实现具体 tool 业务。

### Notes（决断记录，2026-08-27，用户已确认）

**Explore 结论（dispatch-path map，2026-08-27）**：开源 V1 中**不存在 Capability 执行/分发层**。
- `ToolRuntime`（`runtime/tools.py:157-178`）直接 dispatch 到 bare `ToolExecutor` 闭包，不经任何 Capability 抽象；`ToolDescriptor.capability_id` 仅作 trace metadata，不用于 resolve 可执行 capability。
- `PluginLoader` 只把 TOOL_PROVIDER plugin 的 `capabilities()` 记为惰性 `CapabilityDescriptor` metadata（`loader.py:111-113,177-180`）；TOOL_PROVIDER 被显式排除出 typed registry 分派（`loader.py:46-49,167`）。
- `PluginLoader` 根本未接入 `RuntimeApplicationService`（仅测试实例化它）；生产 ToolRuntime 注册方只有 `register_builtin_tools`（`runtime/builtin_tools.py:33`）与 `RegistryMCPRuntime.prepare`（`runtime/mcp.py:243`）。
- `runtime/capabilities.py` 实为 `EffectiveCapabilityResolver`（租户 tool policy allow/deny），非 capability 执行。
- ADR-009 显式决议：Capability Center/Registry 归业务接入层，**不在开源 V1**（`docs/adr/adr-009…:28,32`）。

**与 S-02 的冲突**：design §2.5.2 S-02 指定真实边界 = `ToolRuntime dispatch` + "Agent 调用 TOOL_PROVIDER plugin 的 tool" + "tool 经 Capability Contract 解析"。这要求一条 PluginLoader→Capability→ToolRuntime 桥接，**该桥接在产品代码中不存在**，且 design §3.4 显式把"真实 registry 注入、完整错误路径、测试覆盖"延后到 Phase 5 TASK-E501。

**结论**：S-02 按字面无法满足，除非 (a) 降级真实边界/测试层级（cf-task:start #3.1 禁止 agent 自行降级），或 (b) 在产品中新建 Capability 桥接层（核心 Contract 变更，与 ADR-009"不在开源 V1"+ Phase-5 延后冲突，需 ADR 修订），或 (c) 把 S-02 整体延后 Phase 5（design 自身已延后真实注入；TASK-001 B-01 已在契约层验证 `ToolProvider is CapabilityProvider` 对齐）。

**决断（3a-test，test-local adapter，用户已确认）**：经核对 design §3.4 line 311 / 技术债(2) line 108，"具体 provider 实现接线"明确延后 Phase 5 TASK-E501，reference binding 范围只覆盖 PluginLoader 泛化（TASK-002 已做）。故"产品级 plugin→ToolRuntime adapter"（3a-prod，用户原选）触及 design 延后范围、需 ADR 修订；改采 test-local adapter：adapter 为测试装配（reference binding），驱动真实 `PluginLoader.load` + 真实 `capabilities()→CapabilityDescriptor` + 真实 `ToolRuntime.call` dispatch，证明 contract 可 bind + dispatch 跑通 + capability_id 经 Capability Contract 解析。adapter 非声明的真实边界（声明边界只有 capabilities()→descriptor 与 ToolRuntime dispatch 两端），不构成 mock 绕过/层级降级；层级 integration（S-02 设计层级）；产品代码零变更，对齐 design Phase-5 延后。用户已知情未打断，按 3a-test 推进。完整 Capability 执行层分离（独立 Capability 对象 + 生产 registry 注入）归 Phase 5。

### Checklist

- [x] [S-02][integration] 真实边界=`ToolProvider.capabilities()`→`CapabilityDescriptor` + ToolRuntime dispatch，断言：加载 TOOL_PROVIDER 假实现 plugin → Agent 调用其 tool → 经 Capability Contract（ADR-009）解析；plugin 是 Adapter，业务在 Capability。先写测试记录 RED
- [x] [fluxion-workflow-capability#RULE-fluxion-workflow-001] verifier：S-02 dispatch 断言 + Tool=Adapter/Capability 承载业务边界
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | `ToolProvider.capabilities()`→`CapabilityDescriptor`；ToolRuntime dispatch（真实 PluginLoader.load + 真实 ToolRuntime.call，adapter 为测试装配非被测边界） | TOOL_PROVIDER plugin 的 tool 经 Capability Contract 解析（capability_id 端到端一致）；plugin 是 Adapter；业务在 Capability（reference 占位） | `tests/integration/test_tool_provider_capability_dispatch.py::test_s02_tool_provider_dispatches_via_capability_contract` | `uv run pytest backend/tests/integration/test_tool_provider_capability_dispatch.py -v` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | green-before：reference binding 验证——产品原语（`PluginLoader.load`+`LoadedPlugin.capabilities`+`ToolRuntime.register/call`+`CapabilityDescriptor`）在 TASK-001/002 已落地，adapter 为测试装配；真实 RED 由 TASK-001 B-01（`ToolProvider=CapabilityProvider` 契约形状定义）承载。不得伪造失败（cf-task #7） | PASS: 1 passed（capability_id 端到端一致：`CapabilityDescriptor.capability_id`==`ToolDescriptor.capability_id`==trace `tool.completed`/`tool.policy_decision` 的 `capability_id`；result `{"echo":"hello"}` 来自 `plugin.execute` 证明业务在 Capability；trace 有 `tool.policy_decision`+`tool.completed` 证明真实 ToolRuntime dispatch） | `test_tool_provider_capability_dispatch.py:142-148`（isinstance CapabilityProvider/ToolProvider + capabilities()→descriptor 断言）、`:151-153`（ToolDescriptor.capability_id 来自 descriptor 断言）、`:160-162`（ToolRuntime dispatch result 断言）、`:164-170`（trace capability_id 端到端一致断言） | 真实 `PluginLoader.load`+真实 `_EchoToolPlugin` 实现 `CapabilityProvider`（运行时 `isinstance` 校验）+真实 `ToolRuntime.call`（`_execute`+`emit tool.policy_decision/tool.completed`，非 mock）；adapter 为测试装配（reference binding），非 S-02 声明的真实边界 | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done) — S-02 verified（1 acceptance + 75 integration regression passed）；3a-test test-local adapter 证明 contract 可 bind + ToolRuntime dispatch 跑通；产品级 adapter 接线延后 Phase 5（design line 108/311），零产品代码变更

---

## TASK-004: Plugin 作为 versioned Resource 发布/绑定 + SecretRef credential 路径

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: adr-ext-001-extension-model.design.md#3.3 数据设计, adr-ext-001-extension-model.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-03

### Description

Plugin 作为 versioned Resource kind 可发布（复用既有 `resource_definitions` kind=plugin + publish/rollback 治理，A8/A9 已落地）；credential 走 `resource_bindings.credential_ref`（SecretRef），spec_json 无明文 secret（RULE-EXT-04）。不实现 SecretProvider 生产 resolve（Phase 5），只锁 credential binding 路径形状。无新增表（复用 `resource_definitions`/`resource_bindings`，design §3.3）。

### Checklist

- [x] [S-03][integration] 真实边界=`resource_definitions` 行 + `resource_bindings.credential_ref`，断言：发布 Plugin Resource（kind=plugin, 版本化）+ 绑定 SECRET_PROVIDER credential → 入 `resource_definitions`；credential 走 `resource_bindings.credential_ref`（SecretRef）；spec_json 无明文 secret。先写测试记录 RED
- [x] [fluxion-resource-registry#RULE-fluxion-resource-001] verifier：S-03 断言 + SecretRef/Binding 路径 + spec_json 无明文 secret
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | `resource_definitions` 行 + `resource_bindings.credential_ref` | Plugin 入 resource_definitions（kind=plugin 版本化）；credential 走 credential_ref（SecretRef）；spec_json 无明文 secret | `test_plugin_resource_credential_binding.py::test_s03_*`（3 用例：发布+绑定行级 / spec_json 拒明文 / credential_ref 必须 secret://） | `uv run pytest backend/tests/integration/test_plugin_resource_credential_binding.py -xvs` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | green-before：已有行为补测——产品原语（`ResourceKind.PLUGIN` + `ResourceBinding.credential_ref` + secret:// validator + `assert_no_plaintext_secret` + `SQLiteRegistryStore.put/publish/put_binding`）在 RS 阶段已落地，S-03 集成验证为 green-before；真实 RED 由 RS 阶段契约定义承载（`ResourceKind.PLUGIN` enum 加入 + credential_ref + secret:// validator + `assert_no_plaintext_secret` 落地时的 RED）。不得伪造失败（cf-task #7） | PASS: 3 passed（Plugin Resource kind=plugin 经真实 `store.put`+`publish` 落 `resource_definitions` 行 kind=plugin/status=published/version，`store.get` 行级回读一致；credential 经真实 `store.put_binding` 落 `resource_bindings` 行 resource_type=plugin/credential_ref=secret://，`store.list_bindings` 行级回读一致；`ResourceDefinition.validate_definition` 拒绝 spec_json 明文 secret；`ResourceBinding.validate_binding` 强制 credential_ref=secret://） | `test_plugin_resource_credential_binding.py:60`（published.kind==PLUGIN 发布断言）、`:72-73`（fetched 行级 kind/status 回读断言）、`:99`（bound.resource_type==PLUGIN）、`:102`（bound.credential_ref==secret:// 行级断言）、`:111`（spec_json 拒明文 secret）、`:125`（credential_ref 必须 secret://） | 真实 `SQLiteRegistryStore`（sqlite+aiosqlite:///:memory:，非 mock）+ 真实 `put`/`publish`/`get` 触达 `resource_definitions` 行 + 真实 `put_binding`/`list_bindings` 触达 `resource_bindings.credential_ref` 行；validator 为真实 `ResourceDefinition`/`ResourceBinding` model 校验（非 mock） | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done) — S-03 verified（3 acceptance + 78 integration regression passed）；Plugin kind=plugin 经真实 store.put/publish 落 resource_definitions 行 + credential 经真实 put_binding 落 resource_bindings.credential_ref(secret://) + spec_json/credential_ref validator 拒明文；零产品代码变更（原语 RS 阶段已落地）

---

## TASK-005: Trust 分派 + 故障隔离 + typed manifest 超时/失败策略

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: adr-ext-001-extension-model.design.md#3.5 质量实现方案, adr-ext-001-extension-model.design.md#2.5.2 功能验收场景, adr-ext-001-extension-model.design.md#2.5.1 业务规则与约束
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-04

### Description

验证 trust/isolation 由 ADR-010 既有机制强制（复用 `_enforce_trust`/`TrustLevel`/`execution_mode`，`loader.py:90-97`，本 ADR 不重决——RULE-EXT-03）；经泛化 loader（TASK-002）分派 untrusted→isolated；每个保留类型 manifest 带 typed timeout/fail_policy/scope（Hook 对齐 ADR-007 priority/timeout/fail_policy/scope）；fault injection 注入单 plugin crash 不拖垮 Runtime。不重决 trust，只验证统一模型下既有 isolation 仍生效。

### Checklist

- [x] [S-04][E2E] 真实边界=trust_level→execution_mode 分派 + fault injection，断言：加载 untrusted plugin → 走 isolated（ADR-010）；fault injection 注入单 plugin crash → Runtime 不拖垮；每个保留类型 manifest 带 timeout/fail_policy/scope。先写测试记录 RED
- [x] [fluxion-dfx#RULE-fluxion-dfx-001] verifier：S-04 isolation + fault 不拖垮断言 + 每保留类型 typed timeout/fail_policy/scope
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | trust_level → execution_mode 分派 + fault injection | untrusted 走 isolated（ADR-010）；单 plugin crash 不拖垮 Runtime；每保留类型 manifest 带 timeout/fail_policy/scope | `test_plugin_trust_isolation_fault.py::test_s04_*`（3 用例：untrusted+isolated 加载 / setup crash fault injection 不拖垮 / Hook typed timeout·fail_policy·scope 形状） | `uv run pytest backend/tests/e2e/test_plugin_trust_isolation_fault.py -xvs` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | green-before：已有行为补测——产品原语（`_enforce_trust`/`TrustLevel`/`execution_mode` ADR-010 loader.py:131-138 + `HookRegistration` priority/timeout_ms/fail_policy/scope ADR-007 events.py:48-57 + `HookRegistryProtocol` contracts.py:260-263 + setup 异常传播 loader.py:109 + per-plugin `_loaded`/`_records` 隔离 + 回滚 loader.py:117-123 + PluginLoader per-PluginType 泛化分派 TASK-002）在 ADR-010/007 + TASK-001/002 已落地，S-04 E2E 验证为 green-before；真实 RED 由 ADR-010/007 契约定义承载。不得伪造失败（cf-task #7） | PASS: 3 passed（untrusted+ISOLATED 经真实 `_enforce_trust` 允许加载=走 isolated ADR-010；fault injection 单 plugin setup crash 经真实 setup 异常传播 + per-plugin `_loaded`/`_records` 隔离，幸存 plugin A 仍可用 + crash plugin B 无残留 + 可 `shutdown_all`=不拖垮 Runtime；Hook typed 形状经真实 `HookRegistration` 字段 priority/timeout_ms/fail_policy/scope + `HookRegistryProtocol` register/ordered + HOOK 不进 `_PROVIDER_PROTOCOL`=走 HookRegistryProtocol §3.4 L323） | `test_plugin_trust_isolation_fault.py:116-118`（untrusted+isolated 加载断言）、`:134`（幸存 plugin A 加载）、`:141-144`（crash 不拖垮 + 无残留断言）、`:156-157`（HookRegistryProtocol 形状）、`:160-162`（HookRegistration typed 字段断言）、`:166`（HOOK 不进 typed provider registry） | 真实 `PluginLoader`（真实 `_enforce_trust` ADR-010 + 真实 setup lifecycle + 真实 fault injection setup crash，非 mock）+ 真实 `HookRegistration`/`HookRegistryProtocol` ADR-007 model 校验（非 mock）；RULE-EXT-03：trust/isolation 不重决，只验证统一模型下既有 isolation 仍生效；其他类型 timeout/fail_policy/scope 具体值 Rolling-wave（§307） | verified |

### Log
- [2026-08-27] created (draft)
- [2026-08-27] started (in-progress)
- [2026-08-27] completed (done) — S-04 verified（3 acceptance + 95 e2e regression passed, 1 skipped 外部凭据）；untrusted+isolated 经真实 _enforce_trust 加载 + setup crash fault injection 不拖垮 Runtime + Hook typed timeout/fail_policy/scope 形状（HookRegistration ADR-007）；零产品代码变更（原语 ADR-010/007 + TASK-001/002 已落地）；RULE-EXT-03 不重决 trust
