# Console 交互规格 V0.8 / V1.9.1 详细字段契约

> **用途**：本文是交互稿 HTML 的文字合同，用于反推 API DTO 与 DB。  
> **事实优先级**：已评审交互结论 > 旧前端文档；字段变更必须同步本文、API 基线和 DB 追溯矩阵。  
> **展示约束**：所有业务字段中文；技术 key/schema/path 可英文；所有时间展示 `YYYY-MM-DD HH:mm:ss`。

## 1. 全局页面规范

### 1.1 列表

| 区域 | 规范 |
|---|---|
| 左上 | 一个主要动作：新增/导入 |
| 右上 | 筛选、搜索、刷新 |
| 中间 | Semi Table |
| 行末 | 详情/编辑/少量领域操作 |
| 右下 | Page Size + Pagination |
| 标题 | 不重复大标题/说明，使用左菜单 + breadcrumb |

### 1.2 Modal / Drawer

- 新增与编辑是不同表单状态，不用一个“资源类型万能 Modal”代替各类型字段；
- 详情默认只读；
- 危险操作必须确认；
- 创建后不可改字段在编辑中只读；
- Secret 不回显；
- Admin-only 操作（如发布）对 Builder 不渲染按钮，而非点击后拒绝。

### 1.2.1 危险操作确认与影响面文案（Z-02）

“危险操作必须确认”按**爆炸半径**落到具体控件，不是所有停用都要确认：

| 操作 | 确认方式 | 影响面正文（确认 Modal 正文，不省略） |
|---|---|---|
| **项目平台停用** | Modal 二次确认 + 影响面正文 | “停用后，该平台的 **N 个平台服务能力**将不可运行，**M 名已配置该平台认证的用户**的认证将失效。” N 取 `PLAT-API-01`/`PLAT-API-03` 的 `capability_count`；M 取平台详情的凭据引用计数（`credential_user_count`，后端待补）；M 未返回时正文只显示 N，并追加“用户认证影响待后端确认”，**不显示 0** |
| **用户停用** | Modal 二次确认 + 影响面正文 | “停用后将**立即中断该用户全部 IM 调用**”（进行中的会话不再响应）；正文列出受影响计数：IM 身份数 `im_identity_count`、Agent 授权数 `agent_grant_count`、项目平台认证数 `project_credential_count`（均取自 `USR-API-01`/`USR-API-03` 响应） |
| **服务紧急停用/恢复** | Modal 二次确认（保持既有，SVC-API-10） | 已发布服务不再接收新执行请求；**进行中的执行不中断**；Draft 编辑只读展示该开关 |
| **删除用户平台凭据** | Modal 二次确认（保持既有，CRED-API-04） | 删除后该平台回到「未配置」，需重新配置才能调用；不影响该用户其他平台凭据 |
| **能力 / 模型 / Agent / Skill 停用** | **普通开关，无二次确认** | 风险低（可即时恢复、无跨对象连带失效），仅切换 `enabled` |
| **最后一名启用 Admin 降级/停用** | **禁止**（控件禁用 + 原因 tooltip） | 见 §8.2；后端 `USR-API-04` 必须同步校验 |

通用约束：确认 Modal 的主按钮文案与动作同名（“停用”/“删除”），取消按钮为默认焦点；影响面正文中的计数为**只读文本**，不提供“查看明细”跳转以外的写操作。

### 1.3 Console 菜单（信息架构）

概览、服务、智能体、能力、Skill、知识库（规划中，**可点击进入规划说明页，不置灰**）、模型、项目平台、用户（Admin）、执行记录、审计查询（Admin）。不单设 Channel/认证配置/系统设置/异步任务一级菜单。

**Console 不提供会话管理**（D9）：会话（Conversation Run）与 Checkpoint 属后端内部能力，Console 无对应页面或入口，前端不引用 `CONV-API-01..04`。

### 1.4 登录与会话

登录页 `/login`（用户名/密码 → `POST /api/v1/auth/login`）；Bearer Token 12 小时；401 统一回登录页；角色（ADMIN/BUILDER）来自登录响应。详见 FE-00 §3.3.1。

---

## 2. 服务

## 2.1 列表字段

| 中文字段 | DTO/领域含义 | 可搜索/筛选 | 说明 |
|---|---|---|---|
| 服务名称 | `name` | 搜索 | 展示名 |
| 服务标识 | `key` | 搜索 | 稳定唯一 |
| 主智能体 | `primary_agent` | 可筛 | 创建必选 |
| 执行方式 | `execution_type` | 筛选 | AGENTIC（智能体自主）/DETERMINISTIC（固定流程）/HYBRID（混合），枚举已冻结 |
| 已发布版本 | `current_release` | | 未发布显示 `—` |
| 草稿状态 | draft dirty state | 筛选 | 有未发布修改 |
| 启用状态 | `enabled` | 筛选 | |
| 更新时间 | `update_time` | 排序 | 完整时间 |
| 操作 | | | 详情、编辑 |

**列表筛选：三个正交维度各自独立成控件（Z-07）**——不用单一「状态」把“草稿状态”和“启用状态”合并：

| 筛选项 | Query 参数 | 取值 | 说明 |
|---|---|---|---|
| 草稿状态 | `draft_state` | `dirty` / `clean` | “有未发布修改”是 Draft 属性，与启用状态无关 |
| 启用状态 | `enabled` | `true` / `false` | `SVC-API-10` 的即时开关 |
| 执行方式 | `execution_type` | `AGENTIC` / `DETERMINISTIC` / `HYBRID` | 枚举已冻结 |

三者可同时生效（AND）；概览「待发布 Draft」卡跳转 `/console/services?draft_state=dirty`。切换任一项筛选重置 `page=1` 并把筛选写进 URL 查询串（见 FE-00 §3.4 `StandardListQuery`）。

## 2.2 新增服务

| 字段 | 类型 | 必填 | 创建后可编辑 | 校验/说明 |
|---|---|---:|---:|---|
| 服务名称 | input | 是 | 是 | 1..N 字符，具体长度 API 约束 |
| 服务标识 | input | 是 | 否 | key 格式；唯一 |
| 服务说明 | textarea | 否 | 是 | |
| 主智能体 | select | **是** | Draft 中可调整 | 只选 enabled Agent |
| 执行方式 | select | 是 | 是 | |
| 启用状态 | switch/select | 是 | 是 | |

创建后进入 `/console/services/:id/edit`。

**服务路由登记（T-47）**

| 路由 | 用途 | 说明 |
|---|---|---|
| `/console/services` | 列表 | 左上「+ 新增服务」 |
| `/console/services/new` | 新增 | 独立创建页/Modal，字段见 §2.2 |
| `/console/services/:id` | 只读详情 | 无新增/编辑/删除控件 |
| `/console/services/:id/edit` | Draft 编辑 | Tab 子路由：`?tab=basic`（Tab A 基本信息）/ `?tab=steps`（Tab B 执行编排）/ `?tab=scope`（Tab C 业务范围）/ `?tab=release`（Tab D 测试与发布），默认 `?tab=basic`；非法 tab 回落 `basic` |

`/console/services/:id/edit` 为唯一 Draft 编辑入口；编辑态顶部提供「返回详情」。

**即时启停**：创建时 enabled 是初始状态；创建后仅 Admin 的独立“紧急停用/恢复”动作调用 SVC-API-10，Draft 编辑只读展示开关。发布不覆盖 enabled。详见 FE-02。

## 2.3 服务编辑

### Tab A 基本信息

同上；展示 Draft revision 与 current release。

基本字段与步骤持久化以模块 05 ServiceDraft JSON Schema 为准，主 Agent 修改写 draft_payload.primary_agent_id；所有字段均在 FE-02 列出映射。测试请求必须带 draft_revision，默认 DRY_RUN，TEST 结果显示修订/expired 且不入运营统计。

### Tab B 执行编排

列表列：

```text
顺序 / 步骤名称 / 类型 / 执行对象 / 失败策略 / 超时 / 操作
```

`+ 新增步骤` 必须可点击。

Step Form：

| 字段 | 必填 | 说明 |
|---|---:|---|
| 步骤名称 | 是 | 业务可读 |
| Step 类型 | 是 | Agent / Capability / Wait / Human / Delivery |
| 执行对象 | 条件必填 | Agent/Capability 等 |
| 输入来源/映射 | 按类型 | JSON Pointer 映射 INPUT/STEP 或 literal；见 SVC-API-04 schema |
| 输出变量 | 否 | 给后续步骤引用 |
| 超时时间 | 否/默认 | |
| 失败策略 | 是 | `FAIL_FAST` / `SKIP_ON_ERROR` / `RETRY` / `MANUAL`（MANUAL = 转人工等待） |
| 最大重试 | 条件 | 仅 `RETRY` 时出现 |
| 人工策略 `human_policy` | 是 | 三选一，**控件无空态**：`never`（本步不转人工）/ `on_uncertainty`（语义不确定或失败时可转人工）/ `always`（强制人工检查点）；控制器默认选中项随模块 05 step schema 的默认值，前后端共用同一 JSON Schema 样例测试。**取代原型「人工介入（否/必要时/始终）」**（映射：否→`never`、必要时→`on_uncertainty`、始终→`always`） |
| 人工说明 | Human 时 | 进入等待时对用户展示的说明 |
| Step 类型 = Wait：`wait_seconds`（等待时长） | **条件必填** | Step 类型 = Wait 时**必填**；单位秒，正整数 |
| Step 类型 = Human：`human_deadline_seconds`（人工时限） | 条件可选 | Step 类型 = Human 时出现；单位秒，默认 86400；**不可为空**（留空按默认值提交） |
| Step 类型 = Delivery：`deliverable_template`（交付模板） | **条件必填** | Step 类型 = Delivery 时**必填**；受控变量语法文本（见 D6），空值前端就地报错且不发请求 |
| 步骤说明 | 否 | |

条件必填规则：控件随 Step 类型切换显隐；隐藏的必填字段不参与校验，显示的必填字段为空时就地报错（错误定位到该控件）且不发起请求。详情模式不出现新增/编辑/删除按钮。

**`human_policy` 与既有控件的关系（三者正交，不互相替代）**：

1. **Step 类型 = Human** 是*结构*上的人工检查点（该步骤的执行对象就是人）；`human_policy` 是*策略*声明。Human 步骤固定为人工检查点：其 `human_policy` 控件只读显示 `always` 并给出说明，不允许改成 `never`。
2. **失败策略 = `MANUAL`（转人工等待）** 是*失败路径*转人工；`human_policy=never` 与失败策略 `MANUAL` 语义冲突，同时选中时在 `human_policy` 控件就地报错且不发起 `SVC-API-04`。
3. **`on_uncertainty`** 覆盖“语义不确定（`SEMANTIC_INVALID`）或失败时可转人工”，不改变步骤的正常路径；**`always`** 表示本步必须经人工确认后才能继续。

### Tab C 业务范围

至少展示/编辑：

- Input Schema；
- Output Schema；
- **范围类型**（`resource_scope.type`）：下拉，取值来自后端 `resource_scope_types` 白名单（当前部署 Integration manifest 声明）；
- **范围引用**（`resource_scope.refs[]`）：标签输入/多选；对外展示与筛选用的范围引用，执行列表的 `scope_refs` 直接投影它；
- **范围属性**（`resource_scope.attributes`）：按所选类型**动态渲染**，仅渲染该类型声明的字段，未声明字段不渲染也不提交；
- 业务说明。

**类型白名单为空时**（当前部署未声明 `resource_scope_types`）：本 Tab 提示“当前部署未声明范围类型”，**不渲染类型控件**，也不提供空枚举兜底或前端硬编码枚举。

**已删除**（D5=A）：Service 级 **Scope JSON Schema** 与 **`Schema Hash`**。`resource_scope` 收敛为最小 typed 形态 `{type, refs[], attributes?}`，不再有 scope schema 参与快照/摘要，也不再按类型元数据做投影脱敏；Input/Output Schema 仍按模块 05 的 Draft Schema 展示/编辑。

具体项目字段不进入 Framework 固定列。

### Tab D 测试与发布

Builder：

- `校验`；
- `运行测试`；
- 测试输入；
- **测试用户**（`test_user_id`，见下）；
- Validation checklist；
- 测试结果/Execution ref。

Admin 增加：

- `正式发布`；
- Release 预览；
- 发布确认。

**测试用户控件（B13，`SVC-API-06` 的 `test_user_id` 必填落点）**：

| 项 | 规范 |
|---|---|
| 位置 | 测试弹窗内「测试输入」上方 |
| 显示条件 | **仅当**该服务引用的能力实现中存在 `auth_mode=USER_PLATFORM`（用户平台认证）时显示；否则不渲染该控件（无用户认证上下文） |
| 默认值 | **当前登录用户**（来自登录响应的 `username`/`user_id`）；不使用空占位 |
| 可选范围 | 下拉候选为当前调用者有权选择的测试用户；Builder 只能选其权限范围内的测试用户（`SVC-API-06` 的 `TEST_USER_ACCESS_INVALID`(403) 不得由前端“事先过滤掉”来掩盖） |
| 提交 | 随测试请求提交 `test_user_id`；控件隐藏时该字段仍提交默认的当前登录用户 |
| 错误呈现 | 后端返回 `TEST_USER_ACCESS_INVALID`(403) 时错误定位到该控件并在弹窗内保留输入 |

---

## 3. 智能体

## 3.1 列表

| 字段 | 说明 |
|---|---|
| 智能体名称 | |
| 智能体标识 | |
| 模型配置 | V1 单模型 |
| 直接能力数 | 仅 AgentCapabilityBinding |
| Skill 数 | |
| 知识库数 | 当前规划 |
| 已授权用户数 | AgentAccessGrant |
| IM 接入 | 已配置/未配置 |
| 状态 | |
| 更新时间 | |
| 操作 | 详情/编辑基础信息 |

## 3.2 新增/编辑基础字段

| 字段 | 必填 | 编辑 | 说明 |
|---|---:|---:|---|
| 智能体名称 | 是 | 是 | |
| 智能体标识 | 是 | 否 | |
| 智能体说明 | 否 | 是 | |
| 模型配置 | 是 | 是 | V1 一个 |
| 系统提示词 | 是 | 是 | |
| 记忆策略 | 是/默认 | 是 | |
| 状态 | 是 | 是 | 保存对新请求立即生效 |

不在新增表单出现：能力、Skill、知识、可调用服务、IM、用户授权。

## 3.3 Agent 直接能力 Tab

操作：

- `绑定能力`；
- `解除绑定`。

表格：

```text
能力名称 / 标识 / 实现类型 / 风险 / 状态 / 来源=直接绑定 / 操作
```

注意：Skill 内部 manifest dependencies **不在这里自动生成 binding**。

## 3.4 Agent Skill Tab

操作：

- `绑定 Skill`；
- `解除绑定`。

列表只选择已启用且校验通过 Skill。

显示当前 artifact checksum/SDK/归属标签可帮助判断，但 Agent 关系绑定的是 SkillDefinition（运行解析 current artifact 或 Service Snapshot 指定 artifact）。

## 3.5 可调用服务 Tab

表示 Agent 能调用哪些其他 Service，不是“这个 Agent 是哪些 Service 的 primary Agent”。

需在 UI 帮助中明确两个方向。

## 3.6 IM 接入 Tab

V1 WeCom：

| 字段 | 必填 | 说明 |
|---|---:|---|
| 渠道 | 固定 | 企业微信 |
| 接入方式 | 固定 | WebSocket |
| Bot ID | 是 | 全局唯一，一个 Bot 一个 Agent |
| Secret | 是 | 新增/更新输入，不回显 |
| 启用状态 | 是 | |
| 连接状态 | 只读 | 诊断 |
| 最近连接时间 | 只读 | 完整时间 |

按钮：

- 保存/更新；
- 停用；
- 可选“测试连接/重新连接”（实现前需确认 Gateway 支持，不能只是前端假按钮）。

**写权限（T-23 / D8）**：本 Tab 全部写操作（`CH-API-02` 保存/更新、停用）**仅 Admin**。Builder 打开该 Tab 为只读（不渲染保存/停用按钮），且直接调用 `CH-API-02` 得 403。连接状态与最近连接时间对 Builder 只读可见。

## 3.7 用户授权 Tab

Admin 可：

- 添加用户；
- 撤销授权。

**Builder 定死为只读**（T-23，V1.13 冻结）：Builder **可见**该 Tab 与授权列表，但**不渲染**任何写按钮（无「添加用户」「撤销授权」；无「保存」）；不是「隐藏或只读二选一」。Builder 直接调用 `USR-API-08` 得 403。DB 事实源仍 `AgentAccessGrant`。

**授权/绑定编辑合同（Z-06，`PUT` 全量覆盖类的统一规则）**

`USR-API-08`（Agent 授权用户）、`USR-API-06`（用户 Agent 授权）、`AGENT-API-05/06/07`（直接能力/Skill/可调用服务）都是**全量集合覆盖**写接口，交互统一为：

1. **已授权预勾选**：打开编辑弹窗时，候选列表**全量加载**并**预勾选当前已授权对象**（不是“只列未授权对象再逐行增删”）；
2. **保存前差异确认**：提交前展示差异摘要——“**新增 N 个 / 移除 M 个**”，并列出被移除对象的名称；N=M=0 时保存按钮禁用并提示“无变更”；
3. **提交 `PUT` 全量覆盖**：一次 `PUT` 提交完整集合（含未变更项）；冲突语义以后端 409/幂等覆盖为准（`agent_access_grant` 无独立 `revision`，后端 18 L237；`AGENT-API-05/06/07` 携带的是 `agent_definition` 本体 `revision`）；409 冲突时保留弹窗内容并提示“授权已被他人修改，请重新加载”（防止两个 Admin 静默互相覆盖）。

差异摘要中的移除项必须逐条列出对象名与标识，不得只显示计数。

---

## 4. 能力

## 4.1 公共字段

| 字段 | 类型 | 必填 | 编辑 | DTO 字段名 | 含义 |
|---|---|---:|---:|---|---|
| 能力名称 | input | 是 | 是 | `name` | 人类展示 |
| 能力标识 | input | 是 | 否 | `key` | `customer.get` 等稳定 key |
| 说明 | textarea | 是 | 是 | `description` | 何时使用、做什么、副作用 |
| 输入参数定义 | JSON Schema | 是 | 是 | `input_schema` | Tool input |
| 输出结果定义 | JSON Schema | **是** | 是 | `output_schema` | stable output |
| 风险等级 | select | 是 | 是 | `risk_level` | 低/中/高 |
| 是否有副作用 | select | 是 | 是 | `side_effect` | 四值：`none` / `read` / `write` / `destructive`（默认 `none`） |
| 幂等性 | select | 是 | 是 | `idempotency_semantics` | 三值：`NONE`（不幂等）/ `KEYED`（调用方提供幂等键）/ `NATURAL`（天然幂等） |
| 直调策略 | select | 是 | 是 | `invocation_policy` | `DIRECT`（可被 Agent/Skill 短时直调，**默认**）/ `EXECUTION_ONLY`（必须经 Execution/Worker 可靠执行）。help 文案：“只读但长跑、需人工检查点、有外部副作用 → 选 `EXECUTION_ONLY`”。服务端强制：`side_effect=write`/`destructive` 或 `risk_level=HIGH` 时只能为 `EXECUTION_ONLY`，非法组合 400 `CAPABILITY_CONTRACT_INVALID` |
| 实现类型 | select | 是 | **否** | `implementation_type` | Platform Service/HTTP/MCP/Sandbox |
| 启用状态 | 只读 | — | 创建后由编辑页控制 | `enabled` | 新增**不提交** `enabled`（Create DTO 不接受该字段）；创建后默认启用，由编辑页 `PUT` 控制 |

**新增表单提交边界**：Create（`CAP-API-02`）只接受上表标注为可提交的字段；`enabled`、`revision`、`implementation` 的只读派生项不在 Create 请求体中。编辑（`CAP-API-04`）才允许改 `enabled`。

**归属边界（V1.13 冻结，适用 §4.2–§4.5 全部实现类型）**

- 四类实现配置的字段名**逐字**取自模块 07 的 `capability-implementation-schema`（判别式 `implementation_type`），前端只做控件渲染与必填校验，不另立字段名。
- **超时与重试不属于 `config`**，属于 `execution_policy`：`deadline_ms` / `max_retries` / `backoff_ms`；表单上独立成组，不放进实现配置区块。
- **分页配置不属于 `config`**，属于 `data_retrieval_policy`（见 §4.6）。

## 4.2 Platform Service 专属

| 字段 | 必填 | 说明 |
|---|---:|---|
| `project_platform_id` | **是** | 只有本类型出现；下拉来自 `PLAT-API-01` 安全选项（`name`/`key`/`configured`/`enabled`） |
| `service_key` | 是 | 注册发现 service name |
| `auth_mode` | 是 | 当前用户项目平台认证/共享/无，按支持 |
| `request_mapping` | 是 | 入参映射 |
| `response_mapping` | 是 | 出参映射 |

## 4.3 HTTP 专属

| 字段 | 必填 | 说明 |
|---|---:|---|
| `method` | 是 | |
| `base_url` | 是 | |
| `path` | 否 | |
| `query_mapping` | 否 | |
| `body_mapping` | 否 | |
| `header_keys` | 否 | 仅非 Secret Header 的 **key 列表**；值走 Secret ref |
| `response_mapping` | 是 | |

**不显示项目平台字段**。

## 4.4 MCP 专属

| 字段 | 必填 | 说明 |
|---|---:|---|
| `server_key` | 是 | MCP server 注册键 |
| `tool_name` | 是 | |
| `input_mapping` | 否 | 参数映射 |
| `response_mapping` | 是 | |

不显示项目平台。

## 4.5 Sandbox 专属

| 字段 | 必填 | 说明 |
|---|---:|---|
| `capability_key` | 是 | 受控能力键（非自由命令） |
| `entrypoint` | 是 | 受控 executable |
| `argv` | 否 | 参数模板 |
| `env_allowlist` | 否 | 允许透传的环境变量名 |
| `network_policy` | 是 | |
| `max_output_bytes` | 是 | 输出上限 |

不能提供自由 shell textarea。

## 4.6 Data Retrieval Policy

本节全部字段属于后端的 `data_retrieval_policy`，**不属于** `implementation.config`（见 §4.2 归属边界）。

### 通用开关

`是否分页`：否/是。

### Page

| 字段 | 必填 |
|---|---:|
| 页码参数名 | 是 |
| 每页数量参数名 | 是 |
| 每页数量 | 是 |
| 起始页 | 是 |
| 数据列表路径 | 是 |
| 总数路径 | 否 |
| has_more 路径 | 否 |
| 短页结束 | 是（显式 bool） |
| 最大页数 | 是 |
| 最大数据量 | 是 |
| 最大总执行时间 | 是 |
| 重复页检测 | 是 |

固定说明：

> `items == []` 是 Page/Offset 无更多数据的兜底；`short_page_terminates` 表单默认开启（true，V1.12 裁决），下游可能产生中间短页时必须允许显式关闭；仅当保证“非最后页必满”时才可安全依赖短页终止。

### Offset/Limit

字段同理：offset 参数、limit 参数、start offset、limit。

### Cursor

- cursor 请求参数；
- 初始 cursor（可空）；
- items path；
- next cursor path *；
- has_more 可选；
- max pages/items/time。

### Result Policy

交互至少在详情中明确系统行为：

- 小结果 inline；
- 大结果按实现聚合/Artifact；
- 不直接把 10000 rows 无限制塞入 LLM。

## 4.7 能力测试（Z-04，`CAP-API-05`）

原型的能力页没有测试入口，本节为设计已冻结的 P0 功能（`FEAT-04-05`）。

| 项 | 规范 |
|---|---|
| 控件位置 | 能力**详情页顶部**「测试」按钮（与「编辑」并列）；列表行内**不**放测试（测试需要 JSON 输入，行内无法承载） |
| 权限 | **Builder + Admin**（与模块 07 `CAP-API-05` 授权列一致）；对 Builder 不隐藏按钮 |
| 弹窗输入 | ① 测试输入：按该能力 `input_schema` 渲染的 JSON 编辑器（Schema 不合法的 JSON 就地报错，不发请求）；② **测试用户**（`test_user_id`）：**仅当**该能力的 `auth_mode=USER_PLATFORM`（平台用户认证；凭据来源唯一由实现层 `auth_mode` 表达，不再由能力声明）时显示，默认当前登录用户；③ 结果模式（`result_mode`）：`INLINE` / `SUMMARY`，默认跟随后端实现策略 |
| 输出 | 成功：`ok=true` + 归一化输出（inline 直接展开；外置时显示 summary + `artifact_id` 与「下载产物」入口，经 `EXE-API-06` 受权字节流，不下发对象存储直链）；**耗时**取自 `stats.latency`（毫秒，前端渲染为 `N ms`，不使用客户端计时）；失败：`ok=false` + 脱敏错误码/消息，错误定位到输入控件 |
| 统计行 | 显示 `stats` 的 `downstream_calls` / `pages` / `items` / `retries`（后端返回才显示，缺字段不渲染该项，前端不补 0） |
| 追踪 | 显示响应 `trace_id`（可复制），便于与执行记录/审计对齐 |
| 错误呈现 | `DRY_RUN_UNSUPPORTED` 等能力级错误在弹窗内以错误条展示，不清空已填输入；弹窗不提供“自动改配置”快捷操作 |

---

## 5. Skill

## 5.1 列表

| 字段 | 来源 |
|---|---|
| Skill 名称 | current artifact manifest |
| Skill 标识 | SkillDefinition/key |
| 归属平台 | manifest `platform_label` 文本 |
| SDK 版本 | manifest |
| 入口文件 | manifest |
| 依赖能力数 | manifest derived index |
| 使用智能体数 | reverse binding |
| 校验状态 | import validation |
| 状态 | Definition |
| 更新时间 | 当前 artifact/definition |

## 5.2 导入 Skill

按钮：`导入 Skill`。

字段只有：

- Skill 制品：local file `.zip/.tar.gz` *。

流程：

1. 选择文件；
2. 解析并校验；
3. 展示只读预览；
4. 通过后确认导入。

预览：

- name/key/description；
- platform_label；
- sdk_version；
- entrypoint；
- capabilities[]；
- checksum；
- Manifest Schema；
- SDK compatibility；
- entrypoint；
- Capability existence；
- basic secret scan。

## 5.3 Skill 详情

所有 Tab 只读。

### 基本信息

来自 manifest/current artifact。

### 能力依赖

表：

```text
能力名称 / 能力标识 / 实现类型 / 实际项目平台 / 能力状态 / 来源=Manifest
```

页面明确：

- Skill 依赖来自 manifest；
- platform_label 不是 FK；
- 实际项目平台是根据 Capability Implementation 计算。

### 使用智能体

反向查询 AgentSkillBinding，不能从此页绑定/解绑。

### 制品版本

```text
Artifact ID / 当前标记 / 文件 / SDK / Checksum / 校验 / 导入时间
```

按钮仅顶部“导入新版本”。

### 校验结果

展示每项 validation 与错误/警告。

---

## 6. 模型

**字段级写权限（D2=A，V1.13.1 取代“新增/编辑/测试仅 Admin”）**：模型的新增/编辑**对 Builder 开放**——按钮对 Builder 渲染；但**敏感字段 `api_key`/`extra_headers` 仅 Admin 可写**，Builder 表单中这两个控件**不渲染**、提交体**不含**这些字段；非 Admin 请求体中出现 `api_key`/`extra_headers`（含 `null`/空串）一律 **403 `FIELD_ADMIN_ONLY`** 且**原子拒绝**（同请求的非敏感字段变更也不生效，模块 19）。`base_url`/`default_parameters`/`request_timeout_seconds` **不是凭据**，对 Builder 在详情与表单均可见可写（追加裁决#4；`06-接口设计基线`三态投影）。模型**连通性测试仍仅 Admin**（模块 19 `MODEL-API-05` 授权列）：Builder 不渲染测试按钮，直调得 403。

## 6.1 列表

| 列 | DTO 字段 | 可见性 |
|---|---|---|
| 配置名称 | `name` | Builder / Admin |
| 标识 | `key` | Builder / Admin |
| 协议 | `protocol` | Builder / Admin |
| 模型名称 | `model_name` | Builder / Admin |
| 启用状态 | `enabled` | Builder / Admin |
| API Key 状态 | `api_key_configured` | Builder / Admin |
| 更新时间 | `update_time`（`YYYY-MM-DD HH:mm:ss`） | Builder / Admin |

列表**不渲染**接口地址（`base_url`）、默认参数（`default_parameters`）、额外请求头（`extra_headers`）——列表对两角色都不渲染；其中 `base_url`/`default_parameters` 在详情与表单对 Builder 可见可写（见 §6.2），`extra_headers` 仅 Admin 可见。协议 V1 固定 `OpenAI 兼容`。

## 6.2 新增/编辑

| 字段 | 类型 | 必填 | 创建后编辑 | DTO 字段名 | 校验/说明 |
|---|---|---:|---:|---|---|
| 配置名称 | input | 是 | 是 | `name` | 1..N |
| 标识 | input | 是 | 否 | `key` | 创建后只读 |
| 接口地址 | input | 是 | 是 | `base_url` | Builder + Admin 均可填（`MODEL-API-02` 必填，不渲染则 Builder 无法新增）；列表对两角色都不渲染 |
| 模型名称 | input | 是 | 是 | `model_name` | |
| API Key | password | 新增必填 / 更新留空 | 是 | `api_key` | **仅 Admin 渲染**（D2=A 敏感字段）；不回显；编辑留空 = 不修改；Builder 提交体不含该字段 |
| 请求超时时间（秒） | number | 是 | 是 | `request_timeout_seconds` | 单位**秒**，默认 `60`，范围 1..600，超过范围就地报错 |
| 默认参数 | JSON | 否 | 是 | `default_parameters` | 温度/最大输出令牌数等，**非凭据，Builder + Admin 均可填** |
| 额外请求头 | JSON 文本域 | 否 | 是 | `extra_headers` | 仅 Admin 渲染；示例 `{"X-Tenant":"prod"}`；**仅非敏感 Header，不得放凭据** |
| 启用状态 | switch | 是 | 是 | `enabled` | |

**密钥状态字段名统一为 `api_key_configured`**（不使用 `secret_configured`）；列表与详情均按此字段渲染「已配置 / 未配置」，永不回显密钥值。

**角色可见性（V1.13.1 按 D2=A 更新）**：列表对两角色返回同一组字段（`key`/`name`/`protocol`/`model_name`/`enabled`/`revision`/`api_key_configured`，不含 `base_url`/`default_parameters`/`extra_headers`，模块 19 已冻结）。**新增/编辑表单**中 Builder 可填 `name`/`key`/`model_name`/`protocol`/`base_url`/`default_parameters`/`request_timeout_seconds`/`enabled`；**仅 Admin 可填** `api_key`（Secret 引用）与 `extra_headers`（可能承载网关凭据）——Builder 表单不渲染这两个控件且请求体不含它们。**详情**：Builder 可见其可写字段（`base_url`/`default_parameters`/`request_timeout_seconds`），`api_key`/`extra_headers` 两角色均不回显。新增/编辑按钮对 Builder 渲染；**测试按钮仅 Admin 渲染**。

没有“Internal Gateway 协议”；没有模型发布版本。

## 6.3 连通性测试（Z-04，`MODEL-API-05`）

原型没有模型测试入口，本节为设计已冻结的 P0 功能（`FEAT-07-02`）。

| 项 | 规范 |
|---|---|
| 控件位置 | ① 列表**行内**「测试」；② 模型**详情页顶部**「测试」。两处打开同一 `ModelTestDialog` |
| 权限 | **仅 Admin（已定调）**（与模块 19 `MODEL-API-05` 授权列一致）：测试会用已存 Secret 向 `base_url` 发起真实出站请求，属「凭据使用 + 出站探测」，**不放开给 Builder**；Builder 两处按钮**均不渲染**，直调得 403 |
| 输入 | 仅两个可选参数：`prompt`（留空即用后端默认短探针，**不落库、不回显**）与 `timeout_seconds`（默认取模型的 `request_timeout_seconds`，范围 1..600，**不得超过**该 hard limit；超出就地报错） |
| 输出 | **只做 ping**：`ok`（成功/失败）+ `latency_ms`（前端渲染为 `N ms`）+ `model_name`（实际模型）+ 可选 `provider_request_id`（可复制）。**不返回模型输出内容、不显示 prompt 响应文本** |
| 失败呈现 | 弹窗内错误条显示脱敏 `error_code`/`error_message`；超时显示明确错误（非“未知错误”）；不因失败清空已填的超时值 |
| 副作用 | 测试结果不写入执行记录、不产生 Execution、不影响运营统计；仅落审计 |
| 并发 | 测试为同步请求，按钮 loading 期间不可重复点击 |

---

## 7. 项目平台

## 7.1 列表

```text
平台名称 / 平台标识 / 用户认证方式 / 平台服务能力数 / 状态 / 更新时间 / 操作
```

左上 `+ 新增项目平台`。**字段级写权限（D2=A，V1.13.1 取代“写操作仅 Admin”）**：新增/编辑**对 Builder 开放**（按钮对 Builder 渲染），Builder 可填 `name`/`key`/`description`/`enabled`；**`auth_type` 与 `auth_schema` 仅 Admin 可写**——Builder 表单中这两个控件**不渲染**，提交体**不含**它们。**验证认证模板（`PLAT-API-05`）仅 Admin**（模板本身属 Admin 管理面），Builder 不渲染该按钮。

**配置态**：列表与详情的「用户认证方式」列依赖 `configured`（后端派生：`auth_type != UNCONFIGURED`）。`configured=false` 时该列显示「待配置」，并以该平台的凭据编辑/验证入口禁用（Hover 说明“需先配置认证模板”）。`configured` 与 `enabled` **来自 `PLAT-API-01` 响应**，前端不自行推导。

**「用户认证方式」列的渲染（Y-08）**：不是裸 key，渲染 `auth_type` 对应 Provider 注册项的 **`display_name`**（模块 12 的 Provider 注册项**新增**该字段，当前注册签名 `register_provider(kind, key, provider, source)` 需带上展示名）。前端**不写本地 key→中文映射表**：响应提供该展示名则渲染，未提供时**回退显示 `auth_type` 原值**（不显示空白、不猜中文）。`auth_type=UNCONFIGURED` 仍按上一条显示「待配置」。

**停用二次确认与影响面（Z-02）**：停用平台是**跨对象**危险操作（见 §1.2.1），必须 Modal 二次确认并显示影响面正文——“停用后，该平台的 N 个平台服务能力将不可运行，M 名已配置该平台认证的用户的认证将失效”（计数来源与缺失处理见 §1.2.1）。**不得**用无影响面提示的普通开关停用平台。

**角色可见性**：Builder 可见 `key`/`name`/`auth_type`/`configured`/`enabled`/`revision`；**不可见** `auth_schema`、任何认证头与 Secret 字段。

## 7.2 新增

Builder 与 Admin 共用同一个新增表单，差异只体现在认证字段：

| 字段 | 类型 | 必填 | 创建后可编辑 | 可见/可写角色 |
|---|---|---:|---:|---|
| 项目平台名称 | input | 是 | 是 | Builder + Admin |
| 标识 | input | 是 | 否 | Builder + Admin（创建后只读） |
| 说明 | textarea | 否 | 是 | Builder + Admin |
| 状态 | switch | 是 | 是 | Builder + Admin |
| 用户认证方式（`auth_type`） | select | 否 | 是 | **仅 Admin 渲染**（Builder 表单不出现该控件，创建时由后端置 `UNCONFIGURED`） |

创建后认证模板（`auth_schema`）可在详情/编辑中由 **Admin** 管理；Builder 打开详情/编辑时该区块**只读或隐藏**，仅显示「待配置 / 已配置」状态。

新增请求体：Builder 提交 `name`/`key`/`description`/`enabled`；Admin 可另提交 `auth_type`（非 UNCONFIGURED 时必须为模块 12 已注册 AUTH Provider 的 key，否则 `AUTH_PROVIDER_NOT_REGISTERED`(422)）。**Builder 的请求体携带 `auth_type`/`auth_schema`（含 `null`/`{}`）时后端返回 403 `FIELD_ADMIN_ONLY` 并原子拒绝**（同请求的 `name`/`description` 变更也不生效，模块 09），不由前端“先过滤”掩盖。

## 7.3 用户认证模板

V1 每个平台一个模板。字段定义至少支持：

```text
字段名/key
中文标签
输入类型
是否必填
是否 Secret
校验规则/提示
```

例如 MSS：

```text
username  用户名  text      required
password  密码    password  required secret
```

这里定义的是协议/字段结构，不保存某个用户的账号。

---

## 8. 用户

## 8.1 列表

- 用户名称；
- 用户标识；
- **角色（`role`：END_USER / BUILDER / ADMIN；可筛选，`USR-API-01` 支持 `role` 查询参数）**；
- 状态；
- 项目平台认证数；
- 智能体授权数；
- IM 身份数；
- 更新时间；
- 操作。

不包含“来源”。冻结交互稿的「角色」列与筛选在 V1 保留（Admin 需要按角色找人/筛人）；此前设计文档删除该列与原型冲突，已按原型恢复。

## 8.2 基本信息

新增/编辑字段：用户名称（`display_name`）/ 用户标识（`user_key`，仅新增展示一次，服务端生成）/ 角色（`role`）/ 状态（`status`）/ 说明（`description`）；编辑回传 `revision`。列表不含“来源”。

**角色的三条守卫（Z-12，V1.13.1 新增）**——角色不是可以随便下拉升降的普通字段：

| # | 守卫 | 控件行为 | 违反时的呈现 |
|---|---|---|---|
| 1 | **不能修改自己的角色** | 当被编辑用户 = 当前登录用户时，角色下拉**禁用** | tooltip：“不能修改自己的角色”；后端 `USR-API-04` 同步拒绝 |
| 2 | **升级为 Admin 需二次确认** | 角色由 `BUILDER`（或 `END_USER`）改为 `ADMIN` 时，保存前弹 Modal 二次确认 | Modal 正文：“将该用户升级为 Admin？Admin 可管理用户、授权、凭据与正式发布。” 取消则回滚下拉选中项 |
| 3 | **最后一名启用 Admin 禁止降级或停用** | 当目标用户是当前**唯一** `status=ACTIVE` 的 Admin 时：角色下拉的 `ADMIN` 以外选项**禁用**、状态开关**禁用** | tooltip：“系统必须保留至少一名启用状态的 Admin”；后端 `USR-API-04` 同步校验并返回专用错误码（已冻结，`E-USER-04`/`E-USER-05`），前端按冲突呈现为字段级错误并**回滚**控件到原值 |

守卫 1 与 3 的判定数据来自用户详情（`role`/`status`）与当前登录用户身份，**不依赖前端本地缓存**；页面加载后先取详情再启用角色控件（loading 期间控件禁用，避免先改后拉造成状态错乱）。

**停用二次确认与影响面（Z-02）**：用户停用必须 Modal 二次确认，正文按 §1.2.1 的格式给出“将立即中断该用户全部 IM 调用”与受影响计数（IM 身份数/Agent 授权数/项目平台认证数）。**不得**用无影响面提示的普通开关停用用户。

## 8.3 项目平台认证 Tab

表：

```text
项目平台 / 认证状态 / 账号摘要 / 最近验证时间 / 操作
```

一个 ProjectPlatform 最多一条。

配置 Modal 根据 ProjectPlatform.auth_schema 动态渲染。Secret 字段每次更新输入，不回显。

**只读状态区（D12，V1.13 冻结）**：Modal 底部为**只读**状态展示，**没有**「状态（启用/停用）」这类可写控件：

| 展示项 | DTO 字段 | 说明 |
|---|---|---|
| 认证状态 | `status` | `UNVERIFIED` / `VALID` / `INVALID` / `EXPIRED`，由系统维护 |
| 最近验证时间 | `verified_at` | `YYYY-MM-DD HH:mm:ss` |
| 凭据有效期 | `credential_expires_at` | `YYYY-MM-DD HH:mm:ss`；失效提示重新配置 |
| 会话有效期 | `session_expires_at` | `YYYY-MM-DD HH:mm:ss`；由系统刷新 |

**停用做法**：V1 不存在「停用某平台认证」这一状态语义；要停用即**删除**该条凭据（`CRED-API-04`），删除后该平台回到「未配置」。列表行操作只保留「配置/更新」「验证」「删除」（删除二次确认，影响面正文见 §1.2.1）。

弹窗内**不存在**任何可写的「启用/停用」控件（DOM 中无该控件）；`status` 只读展示，用户不能通过 Console 把凭据置为 VALID/停用。

## 8.4 智能体授权 Tab

表：

```text
智能体 / 标识 / 授权时间 / 状态 / 操作=撤销
```

`+ 授权智能体`。编辑交互按 §3.7 的统一规则：**已授权预勾选 + 保存前差异确认（新增 N / 移除 M）+ `PUT` 全量覆盖**（`USR-API-06`）。

## 8.5 IM 身份 Tab

### 已绑定身份

```text
渠道 / 外部用户标识 / 绑定时间 / 状态 / 操作=解除绑定
```

### 绑定码

未有有效 code：

```text
[生成绑定码]
```

创建后：

```text
绑定码：ABCD-7281       # 仅创建响应/当前页面一次
有效期：2026-09-11 10:30:00
命令：/bind ABCD-7281
[复制命令] [重新生成] [作废]
```

刷新/重新打开如果后端不保存可反查明文，则只显示：

```text
存在待使用绑定码 / 有效期 / [重新生成] [作废]
```

这比后续强行回显 code 更安全。

说明文本：

> IM 身份用于建立外部渠道用户与平台用户映射；智能体访问权限由“智能体授权”单独控制。

---

**跨页面合同同步（V1.13）**：Skill 首导/新版本均先 mode=preview 返回 preview_token，确认才 mode=commit；取消不落正式记录。Model/ProjectPlatform 写操作仅 Admin，Builder 通过安全只读 DTO 选择依赖。平台创建缺省 auth_type=UNCONFIGURED，配置模板前不可认证运行。用户编辑回传 revision，凭据与 Session 有效期分开。Artifact 通过受权字节流/渠道原生文件交付；不向用户暴露短时 ObjectStore URL。

## 9. 执行记录

## 9.1 列表

| 字段 | 说明 |
|---|---|
| 执行编号 | |
| 服务/智能体 | 触发对象 |
| 触发用户 | |
| 执行模式 | 同步/异步/混合展示语义 |
| 执行状态 | 含 WAITING_HUMAN（等待人工）、RETRY_WAIT（等待重试）；人工超时按失败呈现「人工超时（HUMAN_TIMEOUT）」 |
| 投递状态 | 独立 |
| 接入渠道 | channel_source（V1=企业微信；API 发起显示 `—`） |
| 当前阶段 | `current_step_name`（业务可读步骤名）；`current_step`（step_key）只作技术标识，在 hover/次级位置展示（B2） |
| 业务范围 | `resource_scope.refs[]`（范围引用；Builder / Admin 均可见） |
| 追踪标识 | trace_id |
| 开始时间 | |
| 结束时间 | 未结束显示 `—` |
| 操作 | 只有“详情” |

列表投递筛选 Query=delivery_status；业务状态与投递状态独立。投递枚举 NONE/PENDING/SENDING/RETRY_WAIT/DELIVERED/FAILED/UNKNOWN；UNKNOWN 明确送达未确认，不触发业务重试。Builder 只读，取消/重试/审批仅 Admin；重试返回 new_execution_id 后打开新执行。

**搜索范围包含「追踪标识」（Z-14）**：列表搜索框覆盖「执行编号 / 服务 / 智能体 / 触发用户 / **`trace_id`**」；`trace_id` 同时提供独立筛选输入（`EXE-API-01` Query 已有 `trace_id`）。两者都走**服务端**筛选，不做客户端过滤。

**Builder 可见范围（D6=A，V1.13.1）**：Admin 看本租户全部执行；**Builder 只看到**①自己创建的 Service（`service_definition.created_by = self`）的执行，②自己被授权 Agent（`AgentAccessGrant`）相关的执行——两者的**交集（INTERSECT）**（后端 05 L1582；场景：自建 Service + 未授权 Agent → 不可见）。**无权限的执行不返回**（不是返回后前端隐藏）；对越权 execution id 的详情请求按 `EXECUTION_NOT_FOUND`(404) 呈现，不返回 403（不泄露存在性）。

**「重新执行」与「重新投递」是两个动作（D4=A）**：列表行操作仍只有「详情」，四个动作都在详情（见 §9.2）；列表与详情**不再出现**含义模糊的单一「重试」按钮。

**业务范围展示与筛选（D15；按 D5=A 的最小 typed scope 口径）**：`resource_scope` 为 `{type, refs[], attributes?}`，执行列表与详情只展示**范围引用 `refs[]`**；不展示 `attributes`、不展示原始 `resource_scope_json`、不展示跨租户标识。列表支持按范围引用筛选（后端 `scope_refs` 直接投影 `refs`，Query 与执行范围的引用键同名），筛选值来源于后端返回的可选范围引用集合，前端不硬编码范围枚举。V1 无 Service 级 scope schema 与 `Schema Hash`，不再按类型元数据做投影脱敏。

## 9.2 详情

顶部摘要：

- execution id；
- service/release；
- agent/user/conversation；
- status；
- mode；
- start/end；
- result/artifact；
- error；
- trace_id（追踪标识）；
- retry_count（重试次数）；
- channel_source（接入渠道）；
- `current_step_name`（当前阶段，业务可读；hover 显示 `current_step` 步骤 key）；
- resource_scope.refs（业务范围引用，Builder/Admin 可见）。

人工审批区块（status=WAITING_HUMAN 时显示）：

- waiting_reason（等待原因）；
- context 摘要（审批上下文）；
- deadline 倒计时；超时后区块切换为「人工超时（HUMAN_TIMEOUT）」失败呈现，不再显示操作按钮；
- 操作：「继续」（EXE-API-05, decision=RESUME）、「终止」（EXE-API-05, decision=CANCEL）。

**详情动作区：四个按钮 + 门控 + 禁用原因（D4=A / Z-11）**——动作**一律按 `available_actions`（后端按当前状态计算）× 角色**渲染：

| 按钮 | API | 可用条件 | 禁用时 tooltip 原因（示例，文案随状态变化） |
|---|---|---|---|
| 继续 | `EXE-API-05`（decision=RESUME） | `status=WAITING_HUMAN` 且 `available_actions` 含 RESUME；仅 Admin | “执行不在等待人工状态” / “执行已进入取消流程” |
| 终止 | `EXE-API-05`（decision=CANCEL） | `available_actions` 含 CANCEL；仅 Admin | “执行已结束（SUCCEEDED/FAILED/CANCELLED）” / “等待自动重试，第 2/3 次” |
| 重新执行 | `EXE-API-04` | `status=FAILED` 且 `available_actions` 含 RETRY；仅 Admin | “仅失败的执行可重新执行” / “执行仍在运行中” |
| 重新投递 | `EXE-API-07` | `delivery_status ∈ {FAILED, UNKNOWN}`；仅 Admin | “投递尚未失败（当前 DELIVERED）” / “等待自动重试，第 2/3 次” |

- **Builder**：`available_actions` 为 `[]` → 四个按钮**都不渲染**（不是禁用态）。
- **禁用而非隐藏**：处于 `available_actions` 之外但状态相关的动作以**禁用态 + 原因 tooltip** 呈现，避免“按钮忽隐忽现”；与角色无关的动作（Admin-only）对 Builder 一律不渲染。
- tooltip 中“第 N/M 次”的 N 取执行当前重试次数（`retry_count`），M 取后端返回的最大重试上限；M 未返回时省略 “/M”，不臆造上限。
- 「重新执行」成功返回 `new_execution_id`：打开新执行详情并显示 `parent_execution_id`；原执行状态**不变**。「重新投递」**不新建执行**：触发一次新的投递 attempt（遵守 `attempt_token`/epoch 语义，幂等键防重复点击），成功后 `delivery_status` 回到 `PENDING` 并刷新 deliveries 列表。
- 动作不可回滚时给出确认（终止需二次确认）；提交期间按钮 loading，不接受重复点击。

> **进入等待时的用户通知（D1）**：执行进入 `WAITING_HUMAN` 时由**平台自动**向触发用户的渠道推送通知（无需用户在 Console 操作、也不依赖服务作者额外编排 DELIVERY 步骤）。Console 侧只读展示该通知是否已生成；不提供「手动补发」按钮。

Timeline：

```text
time / step / type / status / duration / summary
```

**进度事件（D13）**：只接收与展示 **STAGE 级**阶段事件与**完成/失败**事件，不展示 token 级或步骤内部的细粒度进度流；Console 不自行合成进度条百分比。

Async Task 展开：

- external task id；
- task status；
- submitted/polled/completed；
- result artifact；
- cancel supported；
- error。

不设 Async Task 一级页面。

---

## 10. 概览

概览只展示业务运营摘要，卡片与 `OPS-API-04` 字段一一对应：

| 卡片 | 字段 | 点击行为 |
|---|---|---|
| 服务数 | `service_count` | 跳 `/console/services` |
| 智能体数 | `agent_count` | 跳 `/console/agents` |
| Skill 数 | `skill_count` | 跳 `/console/skills` |
| 能力数 | `capability_count` | 跳 `/console/capabilities` |
| **项目平台数**（B14） | **`project_platform_count`** | 跳 `/console/project-platforms`（无附加筛选）。**后端 `OPS-API-04` 待补该字段**；字段缺失时该卡不渲染（不显示 0 冒充真实计数） |
| 今日执行 | `today_execution_total`（失败 `today_execution_failed`、运行中 `running_execution_count` 作副指标） | 跳 `/console/executions` |
| **今日人工超时**（Z-10） | **`today_human_timeout`** | 跳 `/console/executions?status=FAILED&error_code=HUMAN_TIMEOUT`（`EXE-API-01` 已支持按 `error_code` 过滤，后端 05） |
| 待发布 Draft | `pending_publish_draft_count` | 跳 `/console/services?draft_state=dirty`（Z-07；筛选值即 `draft_state=dirty`，与 §2.1 的三个独立筛选一致） |
| 最近执行 | `recent_executions` | 点击行进入 `/console/executions/:id` |

计数口径为 `execution_source=FORMAL`（今日）；卡片数值来自 `OPS-API-04` 的短 TTL 缓存。不展示 PG/Redis/ObjectStore 健康状态卡片。基础设施由运维系统负责。

---

## 11. Knowledge

规划页：

- 状态“规划中”；
- 说明“外部知识库接入字段/API/DB 尚未冻结”；
- 无新增按钮；
- 无假列表。

---

## 12. IM 命令产品语义

| 命令 | Gateway/Runtime 行为 |
|---|---|
| `/bind CODE` | Gateway 建立 ChannelIdentity |
| `/skills` | 当前 Agent 已绑定/启用 Skill，按 platform_label 分组 |
| `/new` | 创建新 Conversation，Agent 不变 |
| `/stop` | 取消当前 Run/Execution |

未来新增命令应统一走 Command Registry，不能让不同 Channel 各写一套不同语义。

**显式后置（D7）**：V1 **不新增** `/retry`（END_USER 自助重试）——失败执行的重试只由 Admin 在 Console 发起（`EXE-API-04`/`EXE-API-07`）；Console 也**不提供**「来源会话消息」追溯入口（`EXE-API-02` 返回的 `conversation_id` 不作为 Console 页面入口），与 §1.3「Console 不提供会话管理」一致。详见 `README.md` 的「显式后置」。

---

## 13. 页面 → API 关键映射

| 页面动作 | API |
|---|---|
| 新增 Agent | `POST /agents` |
| Agent 绑定直接能力 | `PUT /agents/{id}/capabilities` |
| Agent 绑定 Skill | `PUT /agents/{id}/skills` |
| Agent WeCom | `PUT /agents/{id}/channel/wecom` |
| 导入 Skill | `POST /skills/import` |
| Skill 新版本 | `POST /skills/{id}/artifacts` |
| 新增 Service | `POST /services` |
| 保存 Draft | `PUT /services/{id}/draft` |
| Service Publish | `POST /services/{id}/publish` |
| 用户 Agent Grant | `PUT /users/{id}/agent-grants` |
| 用户平台认证 | `PUT /users/{id}/platform-credentials/{platform}` |
| 生成 BindCode | `POST /users/{id}/bind-codes` |
| Execution 详情 | `GET /executions/{id}` |
| 重新执行 Execution | `POST /executions/{id}/retry`（`EXE-API-04`） |
| 重新投递 | `POST /executions/{id}/redeliver`（`EXE-API-07`，已登记，后端 05 L731） |
| 审计日志查询 | `GET /audit-logs`（`AUDIT-API-01`） |

---

## 14. 交互验收 Gate

提交前逐项验证：

1. 每个列表左主按钮存在并可点；
2. 所有筛选/搜索不是占位假控件；
3. Pagination/PageSize 可操作；
4. 新增字段和 API Create DTO 完全对应；
5. 编辑字段和 Update DTO 完全对应；
6. 详情字段后端可提供；
7. 所有时间完整；
8. Secret 不回显；
9. Builder/Admin 菜单和按钮差异；
10. Service tabs/step buttons 可点击；
11. Skill 不出现编辑/绑定能力；
12. Execution 只有详情一个入口；
13. User IM Tab 同时含 identity 和 bind code；
14. Capability 分页字段按类型动态完整；
15. Knowledge 不伪实现。

---

## 15. 审计查询（Z-09，Admin）

原型早于审计设计（无该菜单项），本节为设计已冻结的内容（`AUDIT-API-01` + FE-11）。

### 15.1 菜单与路由

- 左菜单「审计查询」，**仅 Admin 渲染**；Builder 无菜单入口，直接访问 `/console/audit-logs` 回落到无权限提示且直调 `AUDIT-API-01` 得 403。
- 路由 `/console/audit-logs`，ConsoleLayout 子路由。

### 15.2 列表列

| 列 | DTO 字段 | 说明 |
|---|---|---|
| 时间 | `time` | `YYYY-MM-DD HH:mm:ss` |
| 操作人 | `actor` | 用户名 + 用户标识 |
| 动作 | `action` | `PUBLISH` / `BIND` / `GRANT` / `CREDENTIAL` / `DISABLE` / `DELETE` 等 |
| 资源类型 | `resource_type` | |
| 资源标识 | `resource_id` | |
| 结果 | `result` | `SUCCESS` / `DENIED` / `FAILED` |
| trace 关联 | `execution_id` / `trace_id` | `execution_id` 非空 → 「查看执行」跳 `/console/executions/:id`；为空 → 只显示可复制的 `trace_id` 标签，**不构造死链** |

### 15.3 筛选

时间范围（`from`/`to`，**必填**，默认近 7 天；缺失时不发请求并就地提示；客户端必填、服务端可选，防全表扫描）、操作人（`actor_user_id`）、资源类型（`resource_type`）、动作（`action`）。全部**服务端**筛选，切换任一筛选重置 `page=1`（FE-00 §3.4）。

### 15.4 行展开：`details.changed_fields`

行展开展示**字段级 diff**，而不是一坨 JSON 字面量：

- 结构：`changed_fields: [{field, before?, after?, before_hash?, after_hash?, diff_ref?}]`，按「字段名 / 原值 / 新值」三列渲染；大字段条目（带 `before_hash`/`after_hash`）渲染 hash 前 8 位 + 「大字段，仅记录 hash」标记，`diff_ref` 仅作可复制文本，不提供内容下载；
- **脱敏口径**：Secret 类字段（`api_key`、`*secret*`、`password`、`*token*`、`*_ref`）**原值与新值一律渲染为 `***`**，仅显示「已变更」标记；后端只记录“已变更”标记的字段，前端**不推断**原值；
- 超长值在与内截断，hover 展示完整文本；不提供下载；
- 兼容：响应无 `changed_fields` 时，行展开按普通键值展示 details 的其余脱敏内容，**不显示空块**。

### 15.5 主操作与空态

- 左上**没有**新增/导入主操作；右上为筛选/刷新；右下 PageSize + Pagination；
- 行内**没有**任何写操作（`audit_log` 不可变，无编辑/删除/导出）；
- 空态：「暂无审计事件」；筛选无结果时为空态 + 「清除筛选」。
