# 智能体管理 前端模块需求与设计简报

> **文档编号**: FE-03-V1.11  
> **文档版本**: V1.11 模块分档拆分版  
> **创建日期**: 2026-09-11  
> **文档状态**: 交互基线已冻结，待仓库 Spec Context 绑定  
> **模板**: `design-frontend.md`  
> **交互事实源**: `../90-Console交互规格.md` + `../00-Console公共框架/design-frontend.md`（跨页范式）；字段/API/错误码以本页 §3.4/§3.5 与后端 Owner 模块 §3.4 为准

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
| V1.13 | 2026-09-12 | 第三轮 Review 修复：用户授权 Tab 对 Builder 定死为「只读（可见列表、无写按钮）」；补 `CH-API-02` 403 断言与「不渲染保存按钮」断言；悬空 FEAT 引用（用户授权场景）改挂 `FEAT-03-05`；场景 ID 前缀拆分（integration → `I-03-01`） |
| V1.13.1 | 2026-09-12 | Claude Code：第四轮 Review 修复——§3.4 新增「授权/绑定编辑合同（Z-06）」（全量加载+预勾选、保存前「新增 N 个 / 移除 M 个」差异确认并列出移除对象、一次 `PUT` 全量覆盖携带 `revision`、409 保留弹窗），覆盖 `USR-API-08`/`AGENT-API-05/06/07`/`USR-API-06`；补场景 `S-03-04` |
| V1.14.1 第五轮契约同步 | 2026-09-13 | 第五轮 D8~D15 契约同步（ADR-064）：授权编辑由「`PUT` 全量覆盖 + 乐观锁/409」改为**单条显式操作**（Agent 侧增授权 `POST /api/v1/agents/{agent_id}/grants`、撤授权 `POST /api/v1/agents/{agent_id}/grants/{grant_id}/revoke`；用户侧 `POST /api/v1/users/{user_id}/agent-grants[/{grant_id}/revoke]`），保留「全量加载 + 预勾选 + 差异确认」交互，删除基于集合覆盖与乐观锁的冲突措辞，补「授权读侧返回每条授权有效状态」与场景 `S-03-04` 断言；`AGENT-API-05/06/07` 保持各自 `agent_definition.revision` 语义、表述改为「交互一致，提交语义按各自 Owner 模块定义」 |
| V1.14.2 第六轮 Review 收敛 | 2026-09-13 | 设计修复 | C1：`AGENT-API-05/06/07` 三个 PUT 合并为一次 `PATCH /api/v1/agents/{agent_id}/bindings`，`AGENT-API-08` 删除（有效能力改由详情 bindings 带 `origin` 就地展示）；授权写入口唯一化——删 `USR-API-08/08R`，改走 User 侧 `USR-API-06/06R`，`USR-API-07` 标注只读反查；授权/绑定拆成「逐条即时列表」+「批量差异确认弹窗」两个组件（P1-22/P1-28）；同步 API 映射与场景 S-03-02/S-03-04 |
| V1.14.3 | 2026-09-14 | 契约修复：字段、提交语义与验收场景同步后端 Owner，详见变更记录 13。 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 智能体管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Agent Definition 是配置对象，直接生效但资源绑定、IM 接入和用户授权应在独立 Tab 管理。 |
| 核心目标 | 管理 Agent 基础配置、直接能力、Skill、可调用服务、IM 接入和用户授权，并明确每种关系方向。 |
| 路由 | `/console/agents, /console/agents/:id` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-03-01 | 智能体列表 | 模型/资源计数/IM/授权用户摘要 | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-02 | 基础配置 | 单模型/提示词/Memory | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-03 | 资源绑定 | 能力/Skill/可调用服务 | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-04 | IM 接入 | WeCom Bot 1:1 Agent | P0 | V0.8 / Playbook / 总设 |
| FEAT-03-05 | 用户授权 | Admin 管理 AgentAccessGrant | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 管理 Agent 基础配置、直接能力、Skill、可调用服务、IM 接入和用户授权，并明确每种关系方向。 |
| 非范围（Out of Scope） | 不在新增 Agent 表单中配置能力、Skill、知识、可调用服务、IM、用户授权。 |
| 有意妥协 / 技术债 | 仓库技术栈、组件 API 细节待真实 repo scan 后锁定；产品字段和交互语义已冻结。 |

### 2.4 验收条件

**通用规则**

- Console 仅面向 Builder/Admin，End User 不进入 Console。
- Semi Design 标准列表：左上一个主动作，右上筛选/搜索，中间 Table，右下 PageSize + Pagination。
- **详情即只读态**：详情页承载当前值，写操作走独立 Modal 或独立编辑页（不在详情 Drawer 内直接编辑）；不另开「第二个只读页」，避免详情与编辑的字段集各自漂移。
- Secret 不回显；创建后不可变 key 在编辑态只读。
- 所有时间显示 `YYYY-MM-DD HH:mm:ss`。
- 页面组件不得直接裸调用 fetch/axios，统一经 `services/` 或等价数据访问层。

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-03-01 | FEAT-03-01 | E2E | 必选模型选项 | Builder 新增智能体选择模型 | 模型下拉来自 MODEL-API-01 安全选项（无 Secret）；保存成功 |
| S-03-02 | FEAT-03-03 | E2E | 绑定一次提交 | Builder 在能力/Skill Tab 点绑定并确认差异 | 选择弹窗展示可绑定对象；确认后**只发一次** `PATCH /api/v1/agents/{agent_id}/bindings`（三类集合 + `revision`）；Tab 列表即时刷新，且**不发出** `AGENT-API-05/06/07` 三个 PUT；绑定条目就地显示 `origin`（`DIRECT_BINDING`/`SKILL_DECLARED`/`SERVICE_DERIVED`），**无独立『有效能力分析』入口** |
| S-03-03 | FEAT-03-05 | E2E | IM 接入配置 | Admin 配置企微 Bot 密钥 | 保存后仅显示 secret_configured=true；连接状态只读展示 |
| S-03-04 | FEAT-03-05 | E2E | 授权差异确认与单条操作 | Admin 在本页用户授权 Tab 取消勾选 1 名已授权用户后保存 | 弹窗打开时已授权用户**默认勾选**（候选全量加载，`grant_id` 来自 `USR-API-07` 只读反查）；取消勾选后显示「新增 0 个 / 移除 1 个」并列出该用户名称；确认后发出**一次** `POST /api/v1/users/{user_id}/agent-grants/{grant_id}/revoke`（body 含 `idempotency_key`）——请求打到 **User 侧**接口，且**不发出**任何 `PUT` 全量覆盖与任何 `/agents/{id}/grants` 路径；读侧该条目保留 `revoked_at` 并可追溯 `granted_by`；重放同一 `idempotency_key` 幂等返回；两个 Admin 分别操作不同用户时**互不覆盖** |
| E-03-01 | FEAT-03-05 | E2E | 角色写权限边界 | Builder 打开智能体详情：① 查看用户授权 Tab；② 直接调用 `CH-API-02` 保存企微 Bot 密钥 | 用户授权 Tab **可见且只读**（列表正常渲染，无「添加用户」「撤销授权」按钮）；IM Tab **不渲染**保存/停用按钮；网络面板中 `CH-API-02` 返回 **403**，错误映射为无权限提示且不白屏 |
| I-03-01 | FEAT-03-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 智能体管理 | `/console/agents, /console/agents/:id` | ConsoleLayout | 管理 Agent 基础配置、直接能力、Skill、可调用服务、IM 接入和用户授权，并明确每种关系方向。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-03-01 | AgentListPage | 容器 | 标准列表 |
| CMP-03-02 | AgentBaseForm | 容器 | 基础新增/编辑 |
| CMP-03-03 | AgentDetailTabs | 容器 | 资源/IM/授权 Tabs |
| CMP-03-04 | AgentWeComPanel | 容器 | Bot 配置+连接状态 |
| CMP-03-05 | AgentGrantPanel | 容器 | 授权用户 |
| CMP-03-06 | BindingInlineList | 展示 | 绑定逐条即时列表（无保存按钮） |
| CMP-03-07 | BindingBatchDialog | 容器 | 绑定批量差异确认弹窗（一次 PATCH） |
| CMP-03-08 | GrantInlineList | 展示 | 授权逐条即时列表（无保存按钮） |
| CMP-03-09 | GrantBatchDialog | 容器 | 授权批量差异确认弹窗（逐条 POST） |

### 3.4 组件接口契约与字段

**基础字段**：名称、标识（创建后不可改）、说明、模型配置（V1 一个）、系统提示词、记忆策略、状态。

**直接能力 Tab**：能力名称/标识/实现类型/风险/状态/来源=直接绑定；支持绑定/解绑。

**Skill Tab**：仅选择已启用且校验通过 Skill；展示 current artifact checksum/SDK/归属标签。

**可调用服务 Tab**：表示 Agent 可调用的子服务，不等价于 Service.primary_agent。

**IM Tab**：企业微信、WebSocket、Bot ID、Secret 输入、启用状态、连接状态/最近连接时间。

**用户授权 Tab**：Admin 添加/撤销用户；**Builder 只读**——可见该 Tab 与授权列表，但**不渲染**任何写按钮（无「添加用户」「撤销授权」），且直接调用写接口得 403。不采用「隐藏或只读二选一」（T-23 定死）。

**写入口唯一（V1.14.2 裁决）**：`agent_access_grant` 只有一个事实源，因此**只有一个写入口**——User 侧 `USR-API-06` / `USR-API-06R`。Agent 侧的 `USR-API-08` / `USR-API-08R` 已删除；`USR-API-07`（`GET /api/v1/agents/{agent_id}/users`）保留为**唯一反向只读视图**，本 Tab 的列表与预勾选由它 + `USR-API-05` 提供，`grant_id` 取自读侧。

**授权/绑定编辑合同（Z-06，ADR-064 + V1.14.2 收敛）**：两类编辑**交互一致（全量加载 + 差异确认），提交语义不同**（`90` §3.7）：

1. **用户授权 —— 单条操作，唯一写入口在 User 侧**：增授权 `POST /api/v1/users/{user_id}/agent-grants`（body `{agent_id, idempotency_key}`），撤授权 `POST /api/v1/users/{user_id}/agent-grants/{grant_id}/revoke`（body `{idempotency_key}`）。`USR-API-07` 反向只读视图供本页列表与预勾选；`USR-API-08/08R` **已删除**（同一事实源不得有两个写入口）。并发安全由单条操作的结构保证：两个 Admin 分别操作不同用户互不覆盖；同一 (user, agent) 重复操作按 `idempotency_key` 幂等返回——因此不存在「授权已被他人修改，请重新加载」的乐观锁提示。
2. **Agent 绑定 —— 一次 `PATCH`，带 `revision`**：直接能力 / Skill / 可调用服务三类绑定合并为**一个** `PATCH /api/v1/agents/{agent_id}/bindings`（body 含三类集合 + `revision`）。`AGENT-API-05/06/07` 三个 PUT 与 `AGENT-API-08`（有效能力分析）**已删除**——有效能力不再单独取接口，由 Agent 详情的 bindings 直接带 `origin`（`DIRECT_BINDING` / `SKILL_DECLARED` / `SERVICE_DERIVED`）就地展示。乐观锁保留：`revision` 不匹配返回 409，保留弹窗内容并提示重新加载。

通用交互（两者相同）：

1. **已授权预勾选**：打开编辑弹窗时**全量加载候选对象**并**预勾选当前已授权/已绑定对象**（不是「只列未授权行再逐行增删」）；
2. **保存前差异确认**：提交前显示差异摘要「**新增 N 个 / 移除 M 个**」，并**逐条列出被移除对象的名称与标识**（不得只显示计数）；N=M=0 时保存按钮禁用并提示「无变更」。

**两个组件，两种提交语义（V1.14.2，消除「一个 UI 两种语义」）**：

| 组件 | 用途 | 提交语义 |
|---|---|---|
| `CMP-03-06 BindingInlineList` / `CMP-03-08 GrantInlineList` | Tab 内的**逐条即时列表**：每行一个开关/解绑按钮，改动立即生效 | 立即发一次请求（绑定：先基于已完整加载的该维度集合计算结果，`PATCH .../bindings` 提交该维度完整 ID 集合 + 当前 `revision`，未改维度省略；授权：一条 `POST .../agent-grants` 或 `.../{grant_id}/revoke`）。**无保存按钮；绑定仍按维度整体替换，授权仍是单条操作**。集合未完整加载时禁用绑定写操作；409 保留用户意图、重新加载后再次确认，不自动覆盖 |
| `CMP-03-07 BindingBatchDialog` / `CMP-03-09 GrantBatchDialog` | **批量差异确认弹窗**：仅用于一次性增删多个对象 | 差异确认后提交（绑定：一次 `PATCH .../bindings` 含三类完整集合 + `revision`；授权：**逐条** `POST`，不合并为集合写） |

**授权读侧**：返回**每条授权的有效状态**（含 `grant_id`/`enabled`/`granted_by`/`granted_at`/`revoked_at`），弹窗按该集合预勾选；撤销后条目保留可追溯的 `revoked_at`，不靠前端推断。

**依赖选择**：模型下拉经 MODEL-API-01 的 Builder 安全 DTO（id/name/model_name/enabled）查询，保存 model_config_id；无可用模型明确空态，不要求 Builder 手填 UUID 或获取 Secret。MemoryPolicy 表单字段只有 `allowed_keys`（P2-9 收敛：值/条数上限是平台常量，不做表单）；敏感授权/IM Bot 写操作仅 Admin——**`CH-API-02 保存 Agent WeCom 接入` 授权收紧为仅 Admin（D8）**，Builder 页面不渲染保存按钮且直接调用得 403。

**列表查询**：通用契约见 FE-00 §3.4 `StandardListQuery`，本页领域筛选：`model_id`/`enabled`（另加 `keyword` 按名称/标识搜索），服务端筛选、改筛选重置 `page=1`、状态进 URL。

**行内新增绑定的可验证样例**（请求必须通过后端 `agent-bindings-schema`）：

<!-- contract:agent-binding-inline-example -->
```json
{
  "before": {
    "capability_ids": [
      "00000000-0000-0000-0000-000000000001",
      "00000000-0000-0000-0000-000000000002"
    ],
    "skill_ids": [
      "00000000-0000-0000-0000-000000000004"
    ],
    "service_ids": [],
    "revision": 1
  },
  "request": {
    "capability_ids": [
      "00000000-0000-0000-0000-000000000001",
      "00000000-0000-0000-0000-000000000002",
      "00000000-0000-0000-0000-000000000003"
    ],
    "revision": 1
  },
  "after": {
    "capability_ids": [
      "00000000-0000-0000-0000-000000000001",
      "00000000-0000-0000-0000-000000000002",
      "00000000-0000-0000-0000-000000000003"
    ],
    "skill_ids": [
      "00000000-0000-0000-0000-000000000004"
    ],
    "service_ids": [],
    "revision": 2
  }
}
```
<!-- /contract:agent-binding-inline-example -->

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/03Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Agent 列表 | `AGENT-API-01` | `GET /api/v1/agents` | Agent Core 与 Agent Executor |
| 创建 Agent | `AGENT-API-02` | `POST /api/v1/agents` | Agent Core 与 Agent Executor |
| Agent 详情 | `AGENT-API-03` | `GET /api/v1/agents/{agent_id}` | Agent Core 与 Agent Executor |
| 编辑 Agent 基本配置 | `AGENT-API-04` | `PUT /api/v1/agents/{agent_id}` | Agent Core 与 Agent Executor |
| 编辑 Agent 绑定（直接能力/Skill/可调用服务，一次提交） | `AGENT-API-05` | `PATCH /api/v1/agents/{agent_id}/bindings`（body 含三类集合 + `revision`） | Agent Core 与 Agent Executor |
| 读取 Agent WeCom 接入 | `CH-API-01` | `GET /api/v1/agents/{agent_id}/channel/wecom` | Channel Gateway |
| 保存 Agent WeCom 接入 | `CH-API-02` | `PUT /api/v1/agents/{agent_id}/channel/wecom` | Channel Gateway |
| Agent 反向授权用户（**只读反查**） | `USR-API-07` | `GET /api/v1/agents/{agent_id}/users` | 用户与 Agent 授权 |
| 新增用户 Agent 授权（**唯一写入口**） | `USR-API-06` | `POST /api/v1/users/{user_id}/agent-grants`（body: `agent_id` + `idempotency_key`） | 用户与 Agent 授权 |
| 撤销用户 Agent 授权（**唯一写入口**） | `USR-API-06R` | `POST /api/v1/users/{user_id}/agent-grants/{grant_id}/revoke`（body: `idempotency_key`） | 用户与 Agent 授权 |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无 Agent | 错误+重试 | 列表 |
| IM连接状态 | 局部 loading | 未配置 | 连接异常诊断 | 已连接/断开状态 |
| 用户授权 | loading | 暂无授权 | 权限失败 | 授权表 |

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
| RISK-03-01 | 把 Skill 依赖自动变成 Agent 直接 Tool | 高 | 只显示 effective capability 分析，不自动创建 binding | E2E/Integration |
| RISK-03-02 | Bot ID 复用到多个 Agent | 高 | 前端校验 + 后端唯一约束 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-03-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-03-01 | applied |
| Console-V0.8#GRANT-EDIT-CONTRACT-Z06 | required | 授权/绑定编辑统一交互（预勾选 + 差异确认 + 逐条显式操作提交；ADR-064 单条授权操作，`idempotency_key` 幂等，不用集合覆盖/乐观锁） | §3.4 | S-03-04 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
