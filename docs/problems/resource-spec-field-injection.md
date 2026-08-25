# 资源规格字段：运行时注入真相与修复建议

> **状态**:评审中  
> **日期**:2026-08-25  
> **背景**:控制台「新增运行资产」弹窗只有 类型/资源 ID/版本/规格 JSON 四个裸字段,用户不知道怎么填、不知道六种类型怎么选、不知道 ID 与 JSON 格式。为解决这个问题,从核心运行时**自底向上**梳理了六类资源 spec 字段的真实消费路径——每个字段都要有缘由,规则可以改。本文记录梳理结果与修复建议,供评审。

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

即「**能通过校验的策略对运行时无用,运行时/展示需要的字段通不过校验**」。`denied_tools` 另有注:当前只在 `visible_tools`(services 层无调用点)里用,实际放行路径不生效。

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
    denied_tools: list[str] = Field(default_factory=list)          # 新增:工具黑名单(运行时读,当前执行路径不生效)
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
