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

### 1.3 Console 菜单（信息架构）

概览、服务、智能体、能力、Skill、知识库（规划，置灰）、模型、项目平台、用户（Admin）、执行记录、审计查询（Admin）。不单设 Channel/认证配置/系统设置/异步任务一级菜单。

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

## 2.2 新增服务

| 字段 | 类型 | 必填 | 创建后可编辑 | 校验/说明 |
|---|---|---:|---:|---|
| 服务名称 | input | 是 | 是 | 1..N 字符，具体长度 API 约束 |
| 服务标识 | input | 是 | 否 | key 格式；唯一 |
| 服务说明 | textarea | 否 | 是 | |
| 主智能体 | select | **是** | Draft 中可调整 | 只选 enabled Agent |
| 执行方式 | select | 是 | 是 | |
| 启用状态 | switch/select | 是 | 是 | |

创建后进入 `/services/:id/edit`。

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
| 失败策略 | 是 | FAIL_FAST / RETRY / MANUAL（等待人工）/ SKIP_ON_ERROR |
| 最大重试 | 条件 | |
| 人工说明 | Human 时 | |
| 步骤说明 | 否 | |

详情模式不出现新增/编辑/删除按钮。

### Tab C 业务范围

至少展示/编辑：

- Input Schema；
- Output Schema；
- Resource Scope Type（如果 Service 需要）；
- Scope Schema / Schema Hash；
- 业务说明。

具体项目字段不进入 Framework 固定列。

### Tab D 测试与发布

Builder：

- `校验`；
- `运行测试`；
- 测试输入；
- Validation checklist；
- 测试结果/Execution ref。

Admin 增加：

- `正式发布`；
- Release 预览；
- 发布确认。

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

## 3.7 用户授权 Tab

Admin 可：

- 添加用户；
- 撤销授权。

Builder 可隐藏该 Tab 或只读，按最终角色实现选择；DB 事实源仍 `AgentAccessGrant`。

---

## 4. 能力

## 4.1 公共字段

| 字段 | 类型 | 必填 | 编辑 | 含义 |
|---|---|---:|---:|---|
| 能力名称 | input | 是 | 是 | 人类展示 |
| 能力标识 | input | 是 | 否 | `customer.get` 等稳定 key |
| 说明 | textarea | 是 | 是 | 何时使用、做什么、副作用 |
| 输入参数定义 | JSON Schema | 是 | 是 | Tool input |
| 输出结果定义 | JSON Schema | 建议 | 是 | stable output |
| 风险等级 | select | 是 | 是 | 低/中/高 |
| 是否有副作用 | bool | 是 | 是 | 写操作治理 |
| 幂等性 | select | 是 | 是 | |
| 实现类型 | select | 是 | **否** | Platform Service/HTTP/MCP/Sandbox |
| 状态 | | 是 | 是 | |

## 4.2 Platform Service 专属

| 字段 | 必填 | 说明 |
|---|---:|---|
| 项目平台 | **是** | 只有本类型出现 |
| 服务名 | 是 | 注册发现 service name |
| 操作/接口 | 是 | |
| 认证模式 | 是 | 当前用户项目平台认证/共享/无，按支持 |
| 超时时间 | 是 | |
| 重试配置 | 否/默认 | 幂等约束 |
| 自动分页 | 否 | |

## 4.3 HTTP 专属

- Method *；
- URL/Base URL *；
- Path；
- Request Mapping；
- Headers（非 Secret 常量）；
- 共享认证配置（可选）；
- timeout；
- retry；
- 自动分页。

**不显示项目平台字段**。

## 4.4 MCP 专属

- MCP server/config；
- Tool name *；
- 共享认证（可选）；
- timeout；
- 参数映射（按需）。

不显示项目平台。

## 4.5 Sandbox 专属

- 受控 executable/entrypoint；
- 参数 template；
- workspace policy；
- timeout；
- CPU/内存/输出限制；
- network policy；
- env allowlist。

不能提供自由 shell textarea。

## 4.6 Data Retrieval Policy

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

## 6.1 列表

- 名称；
- 标识；
- 协议（V1 `OpenAI 兼容`）；
- Base URL；
- 模型名称；
- API Key 状态；
- 启用；
- 更新时间。

## 6.2 新增/编辑

- 名称 *；
- 标识 *（创建后不可改）；
- Base URL *；
- Model Name *；
- API Key * / 更新；
- 默认参数；
- 状态。

没有“Internal Gateway 协议”；没有模型发布版本。

---

## 7. 项目平台

## 7.1 列表

```text
平台名称 / 平台标识 / 用户认证方式 / 平台服务能力数 / 状态 / 更新时间 / 操作
```

左上 `+ 新增项目平台`。

## 7.2 新增

- 项目平台名称 *；
- 标识 *；
- 说明；
- 状态。

创建后认证模板可在详情/编辑中管理。

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
- 状态；
- 项目平台认证数；
- 智能体授权数；
- IM 身份数；
- 更新时间；
- 操作。

不包含“来源”。

## 8.2 基本信息

简单新增/编辑。

## 8.3 项目平台认证 Tab

表：

```text
项目平台 / 认证状态 / 账号摘要 / 最近验证时间 / 操作
```

一个 ProjectPlatform 最多一条。

配置 Modal 根据 ProjectPlatform.auth_schema 动态渲染。Secret 字段每次更新输入，不回显。

## 8.4 智能体授权 Tab

表：

```text
智能体 / 标识 / 授权时间 / 状态 / 操作=撤销
```

`+ 授权智能体`。

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
| 当前阶段 | current_step（步骤 key） |
| 追踪标识 | trace_id |
| 开始时间 | |
| 结束时间 | 未结束显示 `—` |
| 操作 | 只有“详情” |

列表投递筛选 Query=delivery_status；业务状态与投递状态独立。投递枚举 NONE/PENDING/SENDING/RETRY_WAIT/DELIVERED/FAILED/UNKNOWN；UNKNOWN 明确送达未确认，不触发业务重试。Builder 只读，取消/重试/审批仅 Admin；重试返回 new_execution_id 后打开新执行。

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
- current_step（当前阶段）。

人工审批区块（status=WAITING_HUMAN 时显示）：

- waiting_reason（等待原因）；
- context 摘要（审批上下文）；
- deadline 倒计时；超时后区块切换为「人工超时（HUMAN_TIMEOUT）」失败呈现，不再显示操作按钮；
- 操作：「继续」（EXE-API-05, decision=RESUME）、「终止」（EXE-API-05, decision=CANCEL）。

Timeline：

```text
time / step / type / status / duration / summary
```

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

概览只展示业务运营摘要：

- Service/Agent/Skill/Capability 数量；
- 今日 Execution；
- 失败/运行中；
- 待发布 Draft；
- 最近执行。

不展示 PG/Redis/ObjectStore 健康状态卡片。基础设施由运维系统负责。

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
