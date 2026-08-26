# ADR-012: Spec Model 单一真相源

- **Status**: Accepted
- **Date**: 2026-08-26
- **Problem Driver**: `docs/problems/resource-spec-field-injection.md`

## Context

资源 spec 存在三层消费：**校验模型**（`SensitiveSpecModel` 子类，`extra="forbid"`）、**运行时**（直接 `spec_json.get(...)`）、**前端表单**（裸 JSON textarea）。三层各自维护键集合，必然静默漂移。`docs/problems/resource-spec-field-injection.md` 评审实证：

- `PolicyDefinition` 只许 `name`/`rules`，但运行时（`capabilities.py`）与展示层（`console_payloads.py`）读 `allowed_tools`/`denied_tools` → 能过校验的策略对运行时无用，运行时要的字段过不了校验。
- `capabilities.py` 读 MCP spec 的静态 `tools` 字段——不在 `MCPDefinition` 中（严格校验建不出），且 id 格式与真实 MCP 工具（`mcp__<server>__<tool>`）不匹配，集合上被三重交集吸收。
- `model_policy` 是 `dict[str, object]`，内部 6 个约定键（`provider`/`failover`/`model`/`timeout_ms`/`deadline_ms`/`max_rounds`）无校验，拼错键在校验层不报、运行时空链失败。
- 校验模型存在多个死字段（运行时零消费），用户填了无效。

根因不是"某个字段漏了"，而是**运行时绕过 spec model 直接读 dict**。

## Constraints

- 开发阶段，无生产数据迁移顾虑，可改核心 Contract（AGENTS.md 规则 25：改 Contract 先建本 ADR）。
- Everything Configurable is a Resource；Secret 不进 Resource Spec（规则 3/17）。
- ExecutionSnapshot 执行期不可变（ADR-005）。
- 前端仅允许 Semi Design（AGENTS.md 规则 20/前端规范 5）。
- 降低控制台用户理解成本是首要产品目标（用户不写 JSON、不记六类资源字段差异）。

## Options

1. **逐字段补齐**：哪里脱节补哪里（如仅给 `PolicyDefinition` 加两个工具字段）。治标，下一个字段仍会漂移。
2. **Spec Model 单一真相源**：运行时消费从 `model_validate(spec_json)` 后的 model 实例取值；前端表单由 `model_json_schema()` 派生。校验/运行时/展示三层物理同源，漂移不可能发生。
3. **前端独立维护结构化表单**：表单与后端契约并行维护 → 第三套键集合，重蹈覆辙。

## Decision

**采用方案 2：Spec Model 是校验、运行时、前端表单的唯一真相源。**

```text
pydantic spec model（contracts.py，extra="forbid"，全字段 Field(title=, description=)；title 即表单字段中文标签）
    ├── 校验：console 严格校验直接 model_validate（现状）
    ├── 运行时：消费点先 model_validate(spec_json) 再读 model 属性，禁止 spec_json.get
    └── 前端：GET /resources/{kind}/schema → model_json_schema() → Semi SchemaForm 渲染
```

配套契约变更（开发阶段一次性收口）：

1. **删死字段**：`RuntimeProfile.allowed_workflows/memory_policy/runtime_policy`、`SkillDefinition.description/capability_id/parameters`、`ModelProviderDefinition.name`、`PolicyDefinition.rules`。
2. **`ModelPolicy` 结构化**：`model_policy: dict → ModelPolicy`（6 键 typed + 默认 60000/120000/8，`extra="forbid"` + frozen）；`ExecutionSnapshot.model_resolution` 随之改型为 frozen `ModelPolicy`（替代 deepcopy 防护，强化 ADR-005）。
3. **`prompt: dict|str → str`**：dict 分支仅为兼容，无消费价值。
4. **`PolicyDefinition` 增加 `allowed_tools`/`denied_tools`**（均为运行时真读字段）。
5. **移除 `capabilities.py` 静态 MCP `tools` 读取路径**（`visible_tools`/`user_granted_tools`/`_mcp_tool_descriptors`）：用户级 MCP 授权语义保留在挂载层（mcp.py binding 检查）与 Skill 扩展（resolver `_effective_skill_selectors`），三集合交集框架（ADR-003）不动。
6. **前端表单 schema 派生**：新增 `GET /api/v1/resources/{resource_type}/schema`，返回 `{schema, defaults}`；前端 Semi 自渲染 `SchemaForm`（不引 `@rjsf`——spec 结构简单用不到其高级能力，且其自带 widget 与 Semi 风格割裂，违反规则 20）。严格校验仍由后端 `validateDraft` 承担，前端不重复实现校验逻辑。

## Trade-offs

- **破坏已存 spec 兼容**（死字段/`rules`/dict 型 `prompt` 的存量 spec 严格校验会拒）：开发阶段无生产数据，接受；测试夹具随契约更新。
- **运行时每执行多一次 `model_validate`**：ExecutionSnapshot 构建基线 P95 ≤ 20ms，单次 spec validate 为微秒级，可忽略。
- **删除静态 tools 路径改变 `EffectiveCapabilityResolver` 公开面**：`user_granted_tools`/`visible_tools` 移除后 user 维度与 agent 维度重合；有效交集结果数学上不变（`user = agent ∪ granted`，`user ∩ agent = agent`）。

## Failure Modes

- 宽松校验存入的草稿含已删字段 → 发布前严格校验兜底拒绝（`extra="forbid"`），错误信息指明多余字段。
- model_validate 在执行期才暴露坏 spec（草稿未经严格校验直接 publish 的旁路）→ Publish 路径强制走 `_definition_model` 严格校验（现状已有）。
- SchemaForm 遇到复杂 schema（oneOf/allOf）渲染退化 → 本 ADR 约束 spec model 保持"扁平 + 一层嵌套 + 简单数组"结构；超出时回到 JSON 高级模式逃生口。pydantic 对 Optional 字段的 `anyOf`（`[{type:...},{type:"null"}]`）与单值 `Literal` 的 `const` 已显式处理（resolveNode 取首个非 null 子模式 / const 按单选项下拉预填），不退化；inMemory schema 镜像必须忠实复刻这两种形状，否则单测与真后端漂移（曾导致 Optional 字段在真 UI 退化成「暂不支持的字段类型」而单测全绿）。

## Validation

- RS2：contracts 单测（per kind valid/invalid、`ModelPolicy` 拼错键拒、默认值）。
- RS3/RS4：snapshot 解析与 effective capability e2e 全绿。
- RS6：schema endpoint 集成测试（per kind）。
- RS7-RS9：前端 SchemaForm 与弹窗 Vitest。

## Revisit Conditions

- 出现需要 oneOf/allOf/条件字段的复杂资源 spec → 评估引入 JSON Schema Form 库或服务端表单描述协议。
- 生产阶段存量 spec 迁移需求 → 补 spec migration 工具（本 ADR 依赖"开发阶段无迁移"前提）。
