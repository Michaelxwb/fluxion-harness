# 资源规格字段：运行时注入真相与修复建议

> **状态**:已闭环（落地见 §6，决策见 [ADR-012](../adr/adr-012-spec-model-single-source-of-truth.md)）  
> **日期**:2026-08-25（评审）/ 2026-08-26（落地闭环）  
> **背景**:控制台「新增运行资产」弹窗只有 类型/资源 ID/版本/规格 JSON 四个裸字段,用户不知道怎么填、不知道六种类型怎么选、不知道 ID 与 JSON 格式。为解决这个问题,从核心运行时**自底向上**梳理了六类资源 spec 字段的真实消费路径——每个字段都要有缘由,规则可以改。本文记录梳理结果与修复建议,供评审。  
> **注**:§2/§3 为评审时刻的历史快照（字段真相表已归档，权威定义以 `contracts.py` spec model 为唯一真相源，见 ADR-012）。其中两处 hedge 已被后续修复超越，已就地标注「⚠️ 已修正，见 §6」。

**涉及文件**:
- `backend/src/fluxion/resources/contracts.py` — 全部 spec 校验模型(权威字段定义)
- `backend/src/fluxion/runtime/resolver.py` — 运行态挂载解析(skill/mcp/plugin/policy/guardrail)
- `backend/src/fluxion/runtime/agent.py` — model_policy 约定键消费
- `backend/src/fluxion/runtime/mcp.py` — MCP 连接配置
- `backend/src/fluxion/runtime/capabilities.py` — 工具放行 / 策略白名单
- `backend/src/fluxion/services/runtime_tool_ops.py` — agent 工具白名单合并
- `backend/src/fluxion/services/console_payloads.py` — 控制台展示负载
- `backend/src/fluxion/services/console_resources.py` — 控制台校验器
- `backend/src/fluxion/services/workflow_app.py` — 工作流 capability_ref 校验

---

## 1. 问题

1. **用户侧**:新增资源弹窗没有任何填法指引。用户不知道:① 每个字段怎么填;② 运行态/技能/MCP/插件/策略/工作流 有什么区别、该选哪个;③ 资源 ID 和 JSON 的格式怎么填。
2. **深层问题(梳理中暴露)**:「校验模型」与「运行时消费」存在脱节,部分字段是死字段,其中最严重的是**策略**:能通过校验的策略对运行时无用,运行时需要的字段通不过校验(见 §3.1)。

校验的「宽松/严格」两级结构(来源 `console_resources.py:461-483`):
- **创建(存草稿)宽松**:`ResourceDefinition` 只校验 id/version 非空、长度、spec 无明文 secret(`contracts.py:289-317`),不按类型强校验 spec。
- **点「校验」/「发布」严格**:非工作流类型跑 `_definition_model(kind).model_validate(spec)`(`console_resources.py:475-483`),且所有 spec 模型继承 `SensitiveSpecModel`(`extra="forbid"`, `contracts.py:126-132`)→ **顶层只能写模型里定义的字段,多写一个即校验失败**。
- **workflow 走独立 DSL 校验**(`_validate_definition` 对 WORKFLOW 返回 None,走 `WorkflowDefinitionValidator`);workflow 校验失败返回 HTTP 400,其余类型返回 `{valid:false}`。

---

## 2. 自底向上的字段真相(每字段给 文件:行号 依据)

> 读法:先看**校验模型**里有哪些字段(决定能不能填),再看**运行时真读哪些**(决定填了有没有用)。「死字段」= 校验允许但运行时零消费。

### 2.1 运行态 `RuntimeProfile`(`contracts.py:135-149`)

| 字段 | 类型 | 运行时消费 | 依据 |
|---|---|---|---|
| `prompt` | dict \| str | **必填**;注入 system prompt | 模型必填;resolver 把 profile 解析进 snapshot |
| `model_policy` | dict | 内部键 `provider`/`failover`/`model`/`timeout_ms`/`deadline_ms`/`max_rounds` 决定模型链与超时 | `agent.py:338-361` |
| `allowed_skills` | list[str] | 挂载技能资源(selector `id` 或 `id@version`) | `resolver.py:355 _profile_selectors` |
| `allowed_mcps` | list[str] | 挂载 MCP server(还需用户级 binding 才可用) | `resolver.py:355` |
| `allowed_tools` | list[str] | agent 工具白名单(tool id) | `runtime_tool_ops.py:175` |
| `plugin_bindings` | list[str] | 挂载模型供应商插件(还需 plugin binding 提供密钥) | `resolver.py:355` |
| `guardrail_policy` | str | 按 `id@version` 解析 POLICY 资源,**仅取版本**进 `snapshot.policy_version`(trace/audit 展示,不做执行期强制) | `resolver.py:295-305` |
| `display_name` | str | 仅 UI 展示,运行时零消费 | — |
| `allowed_workflows` | list[str] | **死字段**,运行时零消费 | — |
| `memory_policy` | dict\|str | **死字段**,运行时零消费 | — |
| `runtime_policy` | dict\|str | **死字段**,运行时零消费 | — |

### 2.2 技能 `SkillDefinition`(`contracts.py:152-158`)

| 字段 | 类型 | 运行时消费 | 依据 |
|---|---|---|---|
| `name` | str | **仅校验必填**;活路径按 `resource.id` 归组,不读 name | `resolver.py:337 _skill_instructions` 以 `skill.id` 为键 |
| `description` | str | 死路径(见下) | — |
| `instructions` | str | **真读**:拼成 `## Skill: <skill_id>\n<instructions>` 注入 system prompt | `resolver.py:337` → `snapshot.skill_instructions`(`:230`)→ `agent.py:384-390` |
| `allowed_tools` | list[str] | **真读**:并入 agent 工具白名单(**放行**语义) | `resolver.py:346` → `snapshot.skill_allowed_tools`(`:231`)→ `runtime_tool_ops.py:175` |
| `capability_id` | str | 死路径 | — |
| `parameters` | dict | 死路径 | — |

> `name`/`description`/`capability_id`/`parameters` 只在 `DeclarativeSkillRuntime.register_resource`(`skills.py`)被读,而该运行时在 services 层**从未被实例化**(grep 全仓无调用点)→ 死代码路径。

### 2.3 MCP `MCPDefinition`(`contracts.py:161-183`)

| 字段 | 类型 | 运行时消费 | 依据 |
|---|---|---|---|
| `transport` | `stdio`\|`streamable_http` | **真读**:决定连接分支 | `mcp.py:377` |
| `command` | str | stdio 下**运行时必填**(缺了抛 `MCPTransportError`),启动命令 | `mcp.py:381` |
| `args` / `env` / `cwd` | — | stdio 分支:命令参数 / 环境变量 / 工作目录 | `mcp.py:392/382/386` |
| `url` | str | streamable_http 下**运行时必填**,服务地址 | `mcp.py:400` |
| `headers` | dict | http 分支:额外头 | `mcp.py:401` |
| `credential_env` | str | stdio:密钥注入到哪个环境变量名 | `mcp.py:383-385` |
| `credential_header` | str | http:密钥注入到哪个 header 名(默认 `Authorization`) | `mcp.py:402` |
| `credential_scheme` | str | http:header 前缀(默认 `Bearer`) | `mcp.py:403` |
| `timeout_ms` | int | 连接/读超时(默认 30000,gt=0) | `mcp.py:378` |
| `allowed_tools` | list[str] | 按 server 工具名白名单**过滤**(收敛语义) | `mcp.py:379,239,283` |
| `name` / `display_name` | — | 运行时**不消费**(工具 id 用 `resource.id`),仅展示 | — |

> **密钥不在 spec 里**:来自 binding 的 `credential_ref`(`secret://…`,`contracts.py:351-353`),经 `CredentialResolver` 解析后注入 `env[credential_env]` 或 `headers[credential_header]`。

### 2.4 插件(控制台按 `ModelProviderDefinition` 校验,`contracts.py:186-201`)

| 字段 | 类型 | 运行时消费 | 依据 |
|---|---|---|---|
| `plugin_type` | 必须 `"model_provider"` | 真读 | `contracts.py:188`(Literal 唯一值) |
| `protocol` | 必须 `"openai_compatible"` | 真读 | `contracts.py:189` |
| `base_url` | str | 真读;须 `http(s)://` | `contracts.py:190,197` |
| `model` | str | 真读;非空 | `contracts.py:191,199` |
| `request_timeout_ms` | int | 默认 60000(gt=0) | `contracts.py:192` |
| `max_retries` | int | 默认 1(ge=0) | `contracts.py:193` |
| `name` | str | **不消费**,仅校验必填 | — |

> **API key 不填在 spec**(明文 secret 会被拒)。密钥来自 **plugin binding 的 `credential_ref`**。
> **`PluginDefinition`(`contracts.py:204-208`,tool/memory/storage/hook 插件包)在控制台资源层无人消费**:`_definition_model(PLUGIN)` 固定返回 `ModelProviderDefinition`(`console_resources.py:469`)。即控制台 JSON 资源只能建「模型供应商」这一种插件形态,tool/memory/storage/hook 是运行时插件包由 loader 装载,不走控制台资源创建。

### 2.5 策略 `PolicyDefinition`(`contracts.py:211-213`)——**最严重脱节**

| 字段 | 类型 | 运行时消费 | 依据 |
|---|---|---|---|
| `name` | str | 仅展示(`console_payloads.py:63`) | — |
| `rules` | list[dict] | **死字段,零读取**(grep 仅定义处一处) | `contracts.py:213` |
| `allowed_tools` | list[str] | **真读**:并入 tenant_policy 工具白名单 | `capabilities.py:104` |
| `denied_tools` | list[str] | **真读**(当前执行路径实际不生效,见 §3.1) | `capabilities.py:105` |

> **矛盾**:`allowed_tools`/`denied_tools` **不在校验模型里**(`extra="forbid"` → 点校验会被拒),但运行时(`capabilities.py:104-105`)和**控制台展示层**(`console_payloads.py:67-68`)都在读它们。工具放行机制见 §3.1。

### 2.6 工作流 `WorkflowDefinition`(`contracts.py:225-244`)

| 字段 | 类型 | 消费 | 依据 |
|---|---|---|---|
| `name` | str | 必填(1-256) | `contracts.py:226` |
| `engine_ref` | str | 必填,须 `workflow-engine://` 前缀 | `contracts.py:236` |
| `steps` | list[`WorkflowStepDefinition`] | 必填(1-200);每步 `id`(必填)/ `capability_ref`(必填,格式 `(skill\|mcp\|plugin):<id>@<version>`,`workflow_app.py:15`)/ `depends_on`(可选,无环)/ `input`(可选 dict) | `contracts.py:216-244` |
| `description` / `display_name` | — | 展示用 | — |

> **现状**:无真正执行引擎;校验/发布即跑 DSL 校验(step id 唯一、依赖无环、capability_ref 可用性)。

---

## 3. 发现的不一致(「规则可改」的候选)

### 3.1 策略三层脱节(最严重)

- **校验层**:`PolicyDefinition` 只许 `name` + `rules`,`extra="forbid"`(`contracts.py:211-213`)。
- **运行时**:只读 `allowed_tools`/`denied_tools`(`capabilities.py:104-105`)。
- **展示层**:控制台 policy 负载已经在读 `allowed_tools`/`denied_tools`(`console_payloads.py:67-68`)。

**实证**(本机复现):

```
PolicyDefinition.model_validate({'name': 'tenant-policy', 'allowed_tools': ['list_pr']})
→ validation error for PolicyDefinition
  allowed_tools: Extra inputs are not permitted [type=extra_forbidden]

但运行时实际读取键: ['allowed_tools', 'denied_tools']; rules 零读取。
```

即「**能通过校验的策略对运行时无用,运行时/展示需要的字段通不过校验**」。`denied_tools` 另有注:当前只在 `visible_tools`(services 层无调用点)里用,实际放行路径不生效。 ⚠️ **已修正，见 §6**——`denied_tools` 已在 `_effective_tool_policy` 中从 user/agent/tenant 三维度统一移除，进入执行路径生效。

### 3.2 工具放行机制(为什么 allowed_* 语义不一样)

`ToolRuntime` 的放行条件是三集合交集 `tool_id ∈ user_grants ∩ agent_allowlist ∩ tenant_policy`(`tools.py:104,127,129`):
- `agent_allowlist` = `profile.allowed_tools ∪ skill.allowed_tools`(`runtime_tool_ops.py:175`)
- `user_grants` = `agent_allowlist ∪ granted_mcp`(用户级 MCP binding)
- `tenant_policy` = 策略 `allowed_tools` 并集,未配置则回退 `user_grants`(`runtime_tool_ops.py:128-144`)

推论:`user_grants ∩ agent_allowlist = agent_allowlist` → **MCP 工具即使被用户 binding 授予,也必须同时出现在 `profile.allowed_tools` 或 `skill.allowed_tools` 才会对模型可见/可调用**。

### 3.3 死字段清单(校验允许,运行时零消费)

| 资源 | 死字段 |
|---|---|
| 运行态 | `allowed_workflows`、`memory_policy`、`runtime_policy` |
| 技能 | `description`、`capability_id`、`parameters`(`name` 仅校验必填) |
| MCP | `name`、`display_name`(展示用) |
| 插件 | `name`(仅校验必填);整个 `PluginDefinition`(tool/memory/storage/hook)在控制台无人消费 |
| 策略 | `rules`(零读取) |

### 3.4 嵌套 dict 内部键:「任意写」≠「有效」

`model_policy`/`memory_policy`/`runtime_policy`/`prompt` 是 `dict[str, object] | str`,`extra="forbid"` 只管顶层,内部键写什么都能过校验。但内部键的有效性由**运行时按约定键消费**决定(`agent.py:338-361` 只读 `provider`/`failover`/`timeout_ms`/`deadline_ms`/`max_rounds`)。键写错/漏写 → 校验通过,但运行时调用模型时才失败/链为空。

---

## 4. 修复建议

### 4.1 后端:修 `PolicyDefinition`(低风险,建议直接实施)

`contracts.py:211-213` 增加两个字段,使校验与「运行时 + 展示」两层对齐:

```python
class PolicyDefinition(SensitiveSpecModel):
    name: str
    rules: list[dict[str, object]] = Field(default_factory=list)  # 保留(向后兼容)
    allowed_tools: list[str] = Field(default_factory=list)         # 新增:工具白名单(运行时真读)
    denied_tools: list[str] = Field(default_factory=list)          # 新增:工具黑名单(运行时读,⚠️ 当前执行路径不生效——见 §6 已修正)
```

- 证据:运行时 `capabilities.py:104-105` 与展示层 `console_payloads.py:67-68` 都已读这两个键,只有校验模型缺。
- 影响面:grep 无任何测试断言 PolicyDefinition 拒绝这两个字段;需**补一个后端单测**(带 `allowed_tools` 的 policy 校验通过、`_validate_definition(POLICY, spec).valid is True`)。
- 配套:文档/弹窗中策略模板随之用真字段(§4.3),`denied_tools` 当前执行路径不生效这一点如实标注。

### 4.2 弹窗重设计(两种形态,待决策)

**形态 A —— JSON 模板 + 逐字段提示(推荐,与现有 草稿→校验→发布 的 JSON 工作流一致)**
- 弹窗内常驻 6 行类型说明(是什么 + 什么时候选),选中行高亮。
- 切换类型自动换 JSON 模板。
- 模板上方/下方逐字段列「字段名 — 为什么需要 — 怎么填」。

**形态 B —— 结构化表单**
- 按类型渲染字段级输入控件(运行态拆成 提示词/模型策略/挂载清单 等具体输入框),自动拼 JSON。
- 改动更大,需新组件 + 表单到 JSON 的映射。

### 4.3 各类型 JSON 模板(基于 §2 真实字段;死字段不进模板)

| 类型 | 模板 |
|---|---|
| 运行态 | `{ "display_name": "我的运行态", "prompt": "你是一名高效可靠的助手。", "model_policy": { "provider": "deepseek-provider", "timeout_ms": 60000 }, "allowed_skills": [], "allowed_mcps": [], "allowed_tools": [], "plugin_bindings": [] }` |
| 技能 | `{ "name": "我的技能", "description": "", "instructions": "…固化给助手的任务做法…", "allowed_tools": [] }` |
| MCP(http) | `{ "name": "my-mcp", "display_name": "我的 MCP", "transport": "streamable_http", "url": "https://…", "headers": {}, "credential_header": "Authorization", "credential_scheme": "Bearer", "timeout_ms": 30000, "allowed_tools": [] }`(另有 stdio 变体:`transport:"stdio"` + `command` + 可选 `args`/`env`/`cwd`/`credential_env`) |
| 插件 | `{ "name": "deepseek", "plugin_type": "model_provider", "protocol": "openai_compatible", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "request_timeout_ms": 60000, "max_retries": 1 }`(注:API key 不填在 spec,配在插件绑定 secret 引用) |
| 策略(修模型后) | `{ "name": "tenant-policy", "allowed_tools": ["list_pr"], "denied_tools": [] }` |
| 工作流 | `{ "name": "我的流程", "description": "", "engine_ref": "workflow-engine://default", "steps": [ { "id": "step-1", "capability_ref": "skill:my-skill@v1", "depends_on": [], "input": {} } ] }` |

---

## 5. 决策点

1. **策略模型修复**(§4.1)是否随本文档评审通过后实施?
2. **弹窗形态**(§4.2)选 A 还是 B?
3. 本文档放置于 `docs/problems/` 是否认可?(备选:`docs/design/`、`docs/development/`)

---

## 6. 结论与落地（2026-08-26 闭环）

决策采纳 [ADR-012](../adr/adr-012-spec-model-single-source-of-truth.md) 方案 2：**spec model 是校验 / 运行时 / 前端表单的唯一真相源**，弹窗形态取 §4.2 的 **B（结构化表单）**。整改 RS1–RS10 已全量落地，后端 223 passed / 1 skipped（live-smoke planned，见 S-P13-07）、前端 20 passed、生产构建（check-no-inmemory + tsc -b + vite build）全绿。

### 6.1 后端契约收口（`contracts.py`）

> **⚠️ 后续 phase1（TASK-A104 收缩，commit 9e0270d）进一步收口**：`RuntimeProfile` 收缩为纯运行机制字段（`request_timeout_ms/max_retries/max_rounds/concurrency/memory_budget_mb/executor_config`，`contracts.py:173-215`），`prompt/model_policy/allowed_skills/allowed_mcps/allowed_tools/plugin_bindings/guardrail_policy/display_name` 不再位于 RuntimeProfile；§4.3「API key 不填在 spec」亦被 `ModelProviderDefinition.credential_ref`（`contracts.py:293`，`secret://`）超越。本文 §2/§3 字段表保留为评审时刻快照。

- **删死字段**：`RuntimeProfile.allowed_workflows/memory_policy/runtime_policy`、`SkillDefinition.description/capability_id/parameters`、`ModelProviderDefinition.name`、`PolicyDefinition.rules` 全部移除（`extra="forbid"` 后顶层不可能再写这些键）。
- **`ModelPolicy` 结构化**：`model_policy` 由 `dict[str, object]` 改为 typed `ModelPolicy`（6 键 `provider/failover/model/timeout_ms/deadline_ms/max_rounds`，默认 60000/120000/8，`extra="forbid"` + `frozen=True`）；`ExecutionSnapshot.model_resolution` 直接持有 frozen 实例（强化 ADR-005）。
- **`prompt: dict|str → str`**；**`PolicyDefinition` 增 `allowed_tools`/`denied_tools`**（均为运行时真读）。
- 运行时消费点全部改为 `model_validate(spec_json)` 后读 model 属性，禁止 `spec_json.get`。

### 6.2 ⚠️ hedge 更正：`denied_tools` 已进执行路径

评审时刻（§3.1/§4.1）记 `denied_tools`「只在 `visible_tools`、实际放行路径不生效」。此结论已被 task #2（S6+A1）超越：`runtime_tool_ops._effective_tool_policy` 现将 `policy_denied` 从 `user_tools`/`agent_tools`/`tenant_tools` **三维度统一移除**后再做三重交集，`denied_tools` 始终优先于白名单拒绝，安全洞已封。

### 6.3 静态 MCP `tools` 路径移除（ADR-012 §5）

`capabilities.py` 对 MCP spec 的静态 `tools` 字段读取路径已整体移除：该字段不在 `MCPDefinition`（严格校验建不出）、工具 id 与真实 MCP 运行时（`mcp__<server>__<tool>`）不匹配、集合上被三重交集吸收。用户级 MCP 授权语义保留在挂载层（`mcp.py` binding 检查）与 Skill 扩展（`resolver._effective_skill_selectors`），`EffectiveCapabilityResolver` 的 user 维度与 agent 维度重合（`user_tools = agent_tools`），有效交集结果数学上不变（`user = agent ∪ granted`，`user ∩ agent = agent`）。

### 6.4 前端表单：schema 派生自后端 model（用户不再手写 JSON）

- 新增 `GET /api/v1/resources/{resource_type}/schema` → `model_json_schema()`（route 注册在 `/{resource_id}` 之前，避免被吞）。
- 前端 `SchemaForm` 自渲染：string→Input/TextArea、integer→InputNumber、enum→Select、boolean→Switch、array→动态增删、结构化 object→边框分组、`dict[str,str]`→键值对；清空可选字段即整键移除（不提交空串/空数组）。
- 创建弹窗（`ResourcesPage`）与草稿编辑（`ResourceDetailPanel`）均接入 `SpecForm`（结构化默认 + 「高级 JSON 模式」逃逸舱）；严格校验仍由后端 `validateDraft` 承担，前端不重复实现。
- inMemory 镜像（`inMemorySchemas.ts`）仅供离线开发/组件测试，线上构建不打包；`check-no-inmemory` 守卫已放宽 inMemory fixture 间互引（仍拦截生产源码引用 inMemory API）。

### 6.5 未覆盖（不在本整改范围）

- S1+S2 auth boundary：用户决定「后续再补」，不在 RS 范围。
- A21 `execution_id` 幂等/去重：独立项，未启动。
- 复杂 schema（oneOf/allOf/条件字段）：ADR-012 revisit 条件，届时评估引入表单库。
