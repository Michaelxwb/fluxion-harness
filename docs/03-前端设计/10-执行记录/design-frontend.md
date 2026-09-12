# 执行记录 前端模块需求与设计简报

> **文档编号**: FE-10-V1.11  
> **文档版本**: V1.11 模块分档拆分版  
> **创建日期**: 2026-09-11  
> **文档状态**: 交互基线已冻结，待仓库 Spec Context 绑定  
> **模板**: `design-frontend.md`  
> **交互事实源**: `../90-Console交互规格.md` + `../archive/fluxion-console-interaction-prototype-v0.8-final.html`（已归档：仅作交互形态参考，冲突以 90-规格 + 后端授权列为准）

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 前端负责人 | 待定 | 页面、路由、组件、services、E2E |
| 产品/交互 | 待定 | V0.8 交互与字段契约 |
| 后端 Owner | 见 API 映射 | DTO/权限/错误码 Contract |

### 1.2 修订历史

| 版本 | 日期 | 变更描述 |
|---|---|---|
| V1.11 | 2026-09-11 | 从大一统 Console 文档拆成独立产品模块设计 |
| V1.13 | 2026-09-12 | 第三轮 Review 修复：补 `resource_scope` 脱敏摘要展示与按范围引用筛选（D15）及场景 `S-10-04`；声明进度事件仅 STAGE 级与完成/失败（D13）、进入人工等待由平台自动推送通知（D1）、Console 不含会话管理（D9）；场景 ID 前缀拆分（integration → `I-10-01`） |
| V1.13.1 | 2026-09-12 | 第四轮 Review 修复：D5=A 业务范围按最小 typed scope 口径改为直接展示 `resource_scope.refs[]`（`{type, refs[], attributes?}`；V1 无 Scope Schema 与 `Schema Hash`，不再按类型元数据做投影脱敏），同步 `FEAT-10-01`、`S-10-04` 与 §3.4 字段契约；B2 列表/详情改渲染 `current_step_name`（业务可读），`current_step` 仅作技术标识；D4=A 详情动作拆为「继续/终止/重新执行/重新投递」（`EXE-API-07`）并补 API 映射；D6=A Builder 执行可见范围（只看自己创建的 Service 与被授权 Agent 相关执行，无权限不返回）；Z-14 搜索范围补 `trace_id`；Z-11 动作禁用态原因 tooltip 与 `available_actions` 门控写死；D7 两项显式后置写入 Out of Scope；补场景 `S-10-05`/`S-10-06`/`S-10-07`/`E-10-02` 与 `RISK-10-03/04` |
| V1.14.1 第五轮契约同步 | 2026-09-13 | 第五轮 D8~D15 契约同步：执行源三态 `{FORMAL, TEST, CAPABILITY_TEST}`（默认仍只查 `FORMAL`，测试面板查 `TEST`，能力测试面板查 `CAPABILITY_TEST`）；Builder 可见范围以 ADR-052/`RULE-SVC-09`/`EXE-API-01` 为准的**并集**（删除交集表述，`S-10-06` 补一致性声明）；普通取消与人工决策分开映射（ADR-065：运行态走 `EXE-API-03`，`WAITING_HUMAN` 才走 `EXE-API-05`）；投递状态按逻辑消息取有效尝试（ADR-066：重投成功后收敛 `DELIVERED`，补 `DELIVERY_IN_FLIGHT`(409)） |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 执行记录 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Execution 是同步/异步/混合执行的统一运营视图，AsyncTask 只是 Step 子状态。 |
| 核心目标 | 提供统一执行列表、只读详情 Timeline、AsyncTask 展开、结果/Artifact/投递/错误诊断。 |
| 路由 | `/console/executions, /console/executions/:id` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-10-01 | 执行列表 | 状态/投递/时间/业务范围筛选和详情，列表展示 `resource_scope.refs`（范围引用） | P0 | V0.8 / Playbook / 总设 |
| FEAT-10-02 | 执行详情 | Timeline + AsyncTask + Artifact | P0 | V0.8 / Playbook / 总设 |
| FEAT-10-03 | 继续/终止/重新执行/重新投递 | 四个动作按 `available_actions` + 角色受控暴露；禁用态给出原因 | P1 | V0.8 / Playbook / 总设 |
| FEAT-10-04 | 人工审批 | WAITING_HUMAN 状态展示等待原因/上下文/倒计时，支持继续/终止 | P1 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 提供统一执行列表、只读详情 Timeline、AsyncTask 展开、结果/Artifact/投递/错误诊断。 |
| 非范围（Out of Scope） | 不设 Async Task 一级页面；列表行操作只有“详情”；**不提供会话（Conversation Run）管理**——Console 无会话页面，前端不引用 `CONV-API-01..04`（D9）；**显式后置（D7，暂不建设）**：① END_USER 自助重试——IM 命令集不含 `/retry`，失败执行只由 Admin 在 Console 发起；② Admin 查看「来源会话消息」入口——`EXE-API-02` 虽返回 `conversation_id`，Console 不提供会话消息追溯视图。两项均不出现入口、按钮或路由。 |
| 有意妥协 / 技术债 | 仓库技术栈、组件 API 细节待真实 repo scan 后锁定；产品字段和交互语义已冻结。 |

### 2.4 验收条件

**通用规则**

- Console 仅面向 Builder/Admin，End User 不进入 Console。
- Semi Design 标准列表：左上一个主动作，右上筛选/搜索，中间 Table，右下 PageSize + Pagination。
- 详情默认只读；新增/编辑使用独立 Modal 或独立编辑页，不在详情 Drawer 内直接编辑。
- Secret 不回显；创建后不可变 key 在编辑态只读。
- 所有时间显示 `YYYY-MM-DD HH:mm:ss`。
- 页面组件不得直接裸调用 fetch/axios，统一经 `services/` 或等价数据访问层。

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-10-01 | FEAT-10-01 | E2E | 多维筛选 | 按状态=WAITING_HUMAN、投递状态=FAILED、trace_id 筛选 | 仅返回匹配执行；字段与 EXE-API-01 DTO 一致 |
| S-10-02 | FEAT-10-04 | E2E | 人工审批闭环 | 对 WAITING_HUMAN 执行点"继续" | 展示 waiting_reason/上下文/倒计时；RESUME 后状态流转；超时后按 HUMAN_TIMEOUT 失败呈现且无操作按钮 |
| S-10-03 | FEAT-10-02 | E2E | Timeline 与 AsyncTask | 展开失败步骤的异步任务 | external task id/轮询状态/结果或错误完整展示 |
| S-10-04 | FEAT-10-01 | E2E | 业务范围筛选与引用展示 | 按「投递状态=FAILED」+ 业务范围引用筛选列表 | 仅返回同时匹配两项条件的执行；列表「业务范围」列显示 `resource_scope.refs[]`（范围引用；不展示 `attributes`、不出现原始 `resource_scope_json`、不出现跨租户标识）；详情与列表一致 |
| S-10-05 | FEAT-10-02 | E2E | 当前阶段业务可读 | 打开一个 `current_step=deliver`（snapshot 中该步骤 `name` = 「交付结果」）的执行详情 | 列表与详情的「当前阶段」列/字段渲染 **「交付结果」**（`current_step_name`），**不出现** `deliver` 作为主文案；hover 该单元格显示步骤 key `deliver`；`current_step_name` 为空时显示 `—` |
| S-10-06 | FEAT-10-01 | E2E | Builder 执行可见范围 | Builder 打开执行列表；并用 URL 直接访问一个由他人创建、且未授权 Agent 相关的 execution id | 列表**只包含**自己创建的 Service 的执行与被授权 Agent 相关的执行（并集：两侧各自单独成立即可见；无权限执行不返回，非前端隐藏）；直接访问越权 ID 显示「执行不存在」（`EXECUTION_NOT_FOUND` 404 映射），**不显示** 403 无权限页；详情页四个动作按钮均不渲染。口径与 ADR-052、`RULE-SVC-09`、`EXE-API-01` 完全一致，不得写成交集 |
| S-10-07 | FEAT-10-03 | E2E | 动作门控与禁用原因 | Admin 依次打开 ①`SUCCEEDED` 且 `delivery_status=DELIVERED` 的执行 ②`status=RETRY_WAIT` 的执行 | ①「终止」「重新执行」「重新投递」均为**禁用态**（不是消失），hover 原因分别为「执行已结束（SUCCEEDED/FAILED/CANCELLED）」与「投递尚未失败（当前 DELIVERED）」；②「终止」禁用并显示「等待自动重试，第 N 次」（N 取 `retry_count`+1，后端返回上限时才显示 `/M`）；`available_actions` 含 CANCEL 的执行「终止」可点击 |
| E-10-01 | FEAT-10-03 | E2E | 重新执行打开新执行 | 对 `FAILED` 执行点「重新执行」后打开响应返回的 `new_execution_id` | 新详情显示 `parent_execution_id` 指向原执行；原执行仍为 FAILED；重复提交复用同一幂等键不产生第二个执行 |
| E-10-02 | FEAT-10-03 | E2E | 重新投递不新建执行 | 对 `delivery_status=UNKNOWN` 的执行点「重新投递」并在二次确认中取消，再确认一次 | 取消后无请求发出、`delivery_status` 保持 UNKNOWN；确认后发出 `POST /api/v1/executions/{id}/redeliver`，**不跳转新执行**，`deliveries[]` 新增一次 attempt 且**保留历史失败行**；**刷新后 `delivery_status` 收敛为 `DELIVERED`（新尝试成功时）**，不再是“回到 PENDING”；同一逻辑消息已有非终态尝试时后端返回 409 `DELIVERY_IN_FLIGHT` 并就地提示；`DELIVERED` 的执行该按钮为禁用态并提示「投递尚未失败（当前 DELIVERED）」 |
| I-10-01 | FEAT-10-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 组件库 | Semi Design | 项目约束 | 与 Console 既定 UI 规范一致 |
| 状态 | Server state + 页面 local state | 待 repo scan | 避免把表单/list query 变成全局事实源 |
| 数据请求 | `services/` 统一封装 | 待 repo scan | 组件中禁止裸 fetch/axios |
| Router | 复用仓库现有 Router | 待 repo scan | 不凭文档替换技术栈 |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| 执行记录 | `/console/executions, /console/executions/:id` | ConsoleLayout | 提供统一执行列表、只读详情 Timeline、AsyncTask 展开、结果/Artifact/投递/错误诊断。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-10-01 | ExecutionListPage | 容器 | 列表 |
| CMP-10-02 | ExecutionDetailPage | 容器 | 摘要+Timeline |
| CMP-10-03 | ExecutionTimeline | 展示 | Step/AsyncTask/Progress |
| CMP-10-04 | ArtifactLinks | 展示 | 结果制品 |
| CMP-10-05 | HumanApprovalPanel | 容器 | WAITING_HUMAN 人工审批区块（waiting_reason/context 摘要/deadline 倒计时 + 继续/终止） |
| CMP-10-06 | ExecutionActionBar | 容器 | 详情动作区：按 `available_actions` + 角色渲染「继续/终止/重新执行/重新投递」，禁用态给原因 tooltip |

### 3.4 组件接口契约与字段

**执行九态**：PENDING/RUNNING/WAITING/WAITING_HUMAN/RETRY_WAIT/CANCELLING/SUCCEEDED/FAILED/CANCELLED。Human 超时是 FAILED/error_code=HUMAN_TIMEOUT，原因单列，不另造执行状态。

**列表 DTO**：完全采用 EXE-API-01 ExecutionSummary：service_name/agent_name/user_name、execution_mode/type、status、delivery_status、trace_id、retry_count、channel_source、**current_step_name（业务可读步骤名，列表展示）**、current_step（step_key，仅技术标识，hover/次级位置）、**resource_scope.refs（范围引用）**、started_at/finished_at。统一 current_step_name，空值显示“—”。筛选与 Query 同名（含 delivery_status、execution_source、**业务范围引用**）；`execution_source` 取值为 `{FORMAL, TEST, CAPABILITY_TEST}`，**默认仍只查 `FORMAL`**，服务测试面板查 `TEST`，能力测试面板查 `CAPABILITY_TEST`（能力测试执行不进默认执行列表）。

**搜索范围包含「追踪标识」（Z-14）**：列表搜索框覆盖「执行编号 / 服务 / 智能体 / 触发用户 / **`trace_id`**」；`trace_id` 另提供独立筛选输入（`EXE-API-01` Query 已有 `trace_id`）。两者都走**服务端**筛选，前端不做客户端过滤。

**Builder 可见范围（ADR-052/ADR-067，V1.14.1）**：Admin 看本租户全部执行；**Builder 看到**①自己创建的 Service（`service_definition.created_by = self`）的执行 **∪** ②自己被授权 Agent（`AgentAccessGrant`）相关的执行——**并集（UNION），两侧各自单独成立即可见**（与 ADR-052、后端 `RULE-SVC-09`、`EXE-API-01` 完全一致，**不得写成交集**）。列表与详情同规则，**无权限的执行不返回**（不是返回后前端隐藏）；对越权 execution id 的详情请求按 `EXECUTION_NOT_FOUND`(404) 呈现（不返回 403，不泄露存在性）。Builder 的执行列表与详情**只读**，四个动作按钮一律不渲染。

**业务范围（D15，按 D5=A 最小 typed scope 口径）**：`resource_scope` 为 `{type, refs[], attributes?}`；列表与详情均只展示**范围引用 `refs[]`**（Builder / Admin 均可见），**不展示** `attributes`、不展示原始 `resource_scope_json`、不展示跨租户标识。列表支持按范围引用筛选（后端 `scope_refs` 直接投影 `refs`），候选值取自后端返回的可选范围引用集合，前端不硬编码范围枚举。V1 无 Scope Schema 与 `Schema Hash`，不再按类型元数据做投影脱敏。

**详情**：EXE-API-02 ExecutionView + steps/async_tasks/progress_events/artifacts/deliveries/commands。Timeline 采用后端 time/type/status/duration_ms/summary，AsyncTask external_task_id=null 显示“提交结果待确认”，不得伪造 ID。产物按钮经 EXE-API-06 下载字节流，不缓存 302 或对象链接。

**进度事件（D13）**：只接收与展示 **STAGE 级**阶段事件与**完成/失败**事件，不展示 token 级或步骤内部细粒度进度流；前端不自行合成进度百分比。

**人工等待通知（D1）**：执行进入 `WAITING_HUMAN` 时由**平台自动**向触发用户渠道推送通知，不依赖 Console 操作或服务作者额外编排；Console 只读展示该通知状态，不提供「手动补发」按钮。

**投递状态**：NONE/PENDING/SENDING/RETRY_WAIT/DELIVERED/FAILED/UNKNOWN；**按逻辑消息取有效尝试聚合**（ADR-066）——同一逻辑消息多次投递时 `delivery_status` 取该消息**有效尝试**（最新一次）的状态，`deliveries[]` 仍展示全部历史尝试（含历史失败行），因此重新投递成功后整体状态收敛为 `DELIVERED` 而不是长期停在 `FAILED`。业务成功且 UNKNOWN 显示“执行完成，通知送达待确认”，不显示业务重试按钮冒充投递恢复。未知投递停止自动重发，用户可在 IM 用 /result 主动取件。

**人工面板**：WAITING_HUMAN 时展示 waiting_reason/context_summary/human_deadline 倒计时，Admin 且 available_actions 包含相应动作才显示“继续/终止”。此处的「终止」走 `EXE-API-05`（`decision=CANCEL`），**只提交 decision/idempotency_key/comment，禁止 input**；接受响应后显示“决策已提交”并刷新状态，不假定已经执行。deadline 409 与版本/决策冲突可定位，保留原上下文。Builder 只读。**普通取消不再走 `EXE-API-05`**；对 RUNNING 等非等待状态调 `EXE-API-05` 返回 409 `EXECUTION_NOT_WAITING_HUMAN`。

**取消/重试（D4=A：两个动作，不合并；ADR-065）**：详情动作为四个按钮——「继续」「终止」「**重新执行**」（`EXE-API-04`，仅 `status=FAILED`）「**重新投递**」（`EXE-API-07`，仅有效投递尝试 `delivery_status ∈ {FAILED, UNKNOWN}`），按 `available_actions` + 角色（Admin）渲染，禁用态给出原因；不存在含义模糊的单一「重试」按钮。**「终止」按状态分流**：`status=WAITING_HUMAN` 时用 `EXE-API-05`（`decision=CANCEL`）；其余可取消状态（PENDING/RUNNING/WAITING/RETRY_WAIT）用 `EXE-API-03`（`POST /cancel`），两者最终收敛到同一 CANCEL 语义。Admin 根据 available_actions 操作；重新执行成功打开 new_execution_id 并显示 parent_execution_id，重复请求复用幂等键；`UNKNOWN` 的重新投递**必须二次确认**并提示可能重复通知（不宣称 exactly-once），`DELIVERED`/`NONE` 情形按钮不可用（后端 409 `EXECUTION_DELIVERY_NOT_RETRYABLE`；同一逻辑消息已有非终态尝试时 409 `DELIVERY_IN_FLIGHT`）；按执行终态禁止不合法操作。Actor/权限最终以后端为准。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/10Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Execution 列表 | `EXE-API-01` | `GET /api/v1/executions` | Service 与 Execution |
| Execution 详情/Timeline | `EXE-API-02` | `GET /api/v1/executions/{execution_id}` | Service 与 Execution |
| 取消 Execution | `EXE-API-03` | `POST /api/v1/executions/{execution_id}/cancel`（**Console 必须调用**，用于其余可取消状态 PENDING/RUNNING/WAITING/RETRY_WAIT） | Service 与 Execution |
| 重试失败 Execution | `EXE-API-04` | `POST /api/v1/executions/{execution_id}/retry` | Service 与 Execution |
| 重新投递结果 | `EXE-API-07` | `POST /api/v1/executions/{execution_id}/redeliver` | Service 与 Execution |
| 下载产物 | `EXE-API-06` | `GET /api/v1/executions/{execution_id}/artifacts/{artifact_id}/download` | Service 与 Execution |
| 人工审批决策 | `EXE-API-05` | `POST /api/v1/executions/{execution_id}/resume`（**只用于 `status=WAITING_HUMAN`** 的「继续（RESUME）/终止（CANCEL）」决策；body: `decision=RESUME / CANCEL`） | Service 与 Execution |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无执行 | 错误+重试 | 列表 |
| Timeline | Skeleton | 无步骤异常 | 加载失败 | 时间线 |
| 取消 | 按钮 loading | 不支持时禁用 + 原因 tooltip | CANCELLING/远端不可取消说明 | 状态刷新 |
| 重新投递 | 按钮 loading + 二次确认 | 不适用（有效尝试为 `DELIVERED`/`NONE`）时禁用 + 原因 tooltip | `EXECUTION_DELIVERY_NOT_RETRYABLE`(409) / `DELIVERY_IN_FLIGHT`(409) 定位提示 | deliveries 列表新增一次 attempt（保留历史失败行），`delivery_status` 按有效尝试收敛（成功即 `DELIVERED`） |

### 3.7 样式与交互规范

- 不重复大页面标题/说明，左侧菜单 + breadcrumb 已表达当前位置。
- 列表页 page size 属于当前列表，不设置全局 page size。
- 行操作固定在最右侧；危险操作二次确认。
- 详情只读，编辑与详情分离。

### 3.8 可访问性与兼容性

- Modal/Drawer 打开后焦点进入容器，关闭后恢复触发点。
- 所有图标按钮具备可访问名称；Form 错误与字段关联。
- 表格主操作可键盘访问。

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|---|---|---|---|---|
| RISK-10-01 | 单独建立 Async Task 菜单导致状态割裂 | 高 | AsyncTask 只在 Execution Detail 展开 | E2E/Integration |
| RISK-10-02 | 取消按钮假成功 | 高 | 展示 CANCELLING 和 remote cancel capability | E2E/Integration |
| RISK-10-03 | 本页三处契约依赖后端同轮同步（D6=A 可见范围、B2 `current_step_name`、D4=A `EXE-API-07`） | 高 | `EXE-API-01/02` 需补 `current_step_name` 并把授权从「本租户全量」改为 Builder 范围；`EXE-API-07` 已在模块 05 登记（L731） | E2E/Integration |
| RISK-10-04 | 两个「重试」语义被实现者合并回一个按钮 | 中 | 文案与门控写死为两个动作（`EXE-API-04` / `EXE-API-07`），并断言 `EXE-API-07` 不新建执行 | S-10-07, E-10-02 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-10-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-10-01 | applied |
| BACKEND-05#ADR-052-BUILDER-SCOPE | required | Builder 执行可见范围为自建 Service 与被授权 Agent 的**并集**（两侧各自单独成立即可见；越权 404 `EXECUTION_NOT_FOUND`） | §3.4 | S-10-06 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
