# 用户管理 前端模块需求与设计简报

> **文档编号**: FE-09-V1.11  
> **文档版本**: V1.11 模块分档拆分版  
> **创建日期**: 2026-09-11  
> **文档状态**: 交互基线已冻结，待仓库 Spec Context 绑定  
> **模板**: `design-frontend.md`  
> **交互事实源**: `../90-Console交互规格.md` + `../fluxion-console-interaction-prototype-v0.8-final.html`

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

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 用户管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | 用户是 ChannelIdentity、长期 Memory、项目平台凭据和 AgentAccessGrant 的平台主体。 |
| 核心目标 | 管理用户基本信息、项目平台认证、Agent 授权、IM 身份与 BindCode，保持三类关系互不混淆。 |
| 路由 | `/console/users, /console/users/:id` |
| 角色 | Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-09-01 | 用户 CRUD | 基本信息无“来源”字段 | P0 | V0.8 / Playbook / 总设 |
| FEAT-09-02 | 项目平台认证 | 每用户×平台最多一条 | P0 | V0.8 / Playbook / 总设 |
| FEAT-09-03 | Agent 授权 | AgentAccessGrant | P0 | V0.8 / Playbook / 总设 |
| FEAT-09-04 | IM 身份 | ChannelIdentity + BindCode | P0 | V0.8 / Playbook / 总设 |
| FEAT-09-05 | 用户记忆（Memory） | 只读查看 + 单条删除/清理（Playbook U06） | P1 | Playbook U06 / 总设 §5.5 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 管理用户基本信息、项目平台认证、Agent 授权、IM 身份与 BindCode、用户 Memory 查看/清理，保持各关系互不混淆。 |
| 非范围（Out of Scope） | 不提供用户来源字段，不把 ChannelIdentity、AgentAccessGrant、ProjectCredential 合并成一张“绑定”表。 |
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
| S-09-01 | FEAT-09-01 | E2E | 标识自动生成 | Admin 新增用户（仅名称/角色） | user_key 服务端生成并展示一次；无需手工输入 |
| S-09-02 | FEAT-09-02 | E2E | 认证配置动态表单 | 按平台 auth_schema 渲染表单并保存凭据 | 保存成功 Secret 不回显；verify 返回状态 |
| S-09-03 | FEAT-09-05 | E2E | Memory 治理 | Admin 查看用户记忆并删除单条 | 列表只读+单条删除确认；删除后列表刷新 |
| S-09-04 | FEAT-09-04 | E2E | 绑定码生命周期 | 生成绑定码→/bind 使用→作废 | 明文仅创建时显示一次；同一 user+渠道同时仅一个 PENDING |
| E-09-01 | FEAT-09-03 | E2E | 授权与 IM 身份不混淆 | 查看三类 Tab | 平台认证/Agent 授权/IM 身份分列展示，操作互不影响 |
| E-09-01 | FEAT-09-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 用户管理 | `/console/users, /console/users/:id` | ConsoleLayout | 管理用户基本信息、项目平台认证、Agent 授权、IM 身份与 BindCode，保持三类关系互不混淆。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-09-01 | UserListPage | 容器 | 列表 |
| CMP-09-02 | UserDetailTabs | 容器 | 三类配置 |
| CMP-09-03 | CredentialModal | 容器 | 按 auth_schema 动态表单 |
| CMP-09-04 | AgentGrantPanel | 容器 | 授权/撤销 |
| CMP-09-05 | IdentityPanel | 容器 | 身份/解绑 |
| CMP-09-06 | BindCodePanel | 容器 | 生成/复制/重新生成/作废 |

### 3.4 组件接口契约与字段

**列表**：用户名称、标识、状态、项目平台认证数、智能体授权数、IM 身份数、更新时间。

**项目平台认证**：平台、认证状态、账号摘要、最近验证时间、操作；Secret 不回显。

**智能体授权**：智能体、标识、授权时间、状态、撤销；`+ 授权智能体`。

**IM 身份**：渠道、外部用户标识、绑定时间、状态、解除绑定。BindCode 创建响应一次展示明文和 `/bind CODE`，后续只显示存在/有效期/重新生成/作废。

**用户记忆（Memory Tab，Admin-only）**：只读列表——memory_key、值摘要、来源（source_type/source_ref）、置信度、更新时间；操作=单条删除（确认弹窗，MEM-API-03；按 memory_key 删除）。批量清理为逐条删除，不提供整表一键清空。删除 Memory 不影响业务审计记录（数据生命周期分离，总设 §5.5）。



**新增/编辑 DTO**：新增提交 display_name/role/status/description，不提交 user_key，成功显示服务器生成值；编辑先读取详情 revision 并原样回传，冲突保留表单并提示重新加载，不覆盖他人更新。平台认证区 credential_expires_at 与 session_expires_at 分开展示，前者失效提示重新配置，后者由系统刷新；Memory 使用 EXPLICIT/DERIVED/ADMIN 来源，仅 Admin 治理。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/09Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| 用户列表 | `USR-API-01` | `GET /api/v1/users` | 用户与 Agent 授权 |
| 创建用户 | `USR-API-02` | `POST /api/v1/users` | 用户与 Agent 授权 |
| 用户详情 | `USR-API-03` | `GET /api/v1/users/{user_id}` | 用户与 Agent 授权 |
| 编辑用户 | `USR-API-04` | `PUT /api/v1/users/{user_id}` | 用户与 Agent 授权 |
| 获取用户 Agent 授权 | `USR-API-05` | `GET /api/v1/users/{user_id}/agent-grants` | 用户与 Agent 授权 |
| 覆盖用户 Agent 授权 | `USR-API-06` | `PUT /api/v1/users/{user_id}/agent-grants` | 用户与 Agent 授权 |
| 用户平台认证列表 | `CRED-API-01` | `GET /api/v1/users/{user_id}/platform-credentials` | Auth 与项目平台 |
| 保存用户平台认证 | `CRED-API-02` | `PUT /api/v1/users/{user_id}/platform-credentials/{platform_id}` | Auth 与项目平台 |
| 验证用户平台认证 | `CRED-API-03` | `POST /api/v1/users/{user_id}/platform-credentials/{platform_id}/verify` | Auth 与项目平台 |
| 删除用户平台认证 | `CRED-API-04` | `DELETE /api/v1/users/{user_id}/platform-credentials/{platform_id}` | Auth 与项目平台 |
| 用户 IM 身份列表 | `CH-API-03` | `GET /api/v1/users/{user_id}/channel-identities` | Channel Gateway |
| 解除 IM 身份 | `CH-API-04` | `DELETE /api/v1/users/{user_id}/channel-identities/{identity_id}` | Channel Gateway |
| 用户记忆列表 | `MEM-API-01` | `GET /api/v1/users/{user_id}/memory` | Conversation 与 User Memory |
| 删除单条记忆 | `MEM-API-03` | `DELETE /api/v1/users/{user_id}/memory/{memory_key}` | Conversation 与 User Memory |
| 生成绑定码 | `CH-API-05` | `POST /api/v1/users/{user_id}/bind-codes` | Channel Gateway |
| 读取当前绑定码状态 | `CH-API-06` | `GET /api/v1/users/{user_id}/bind-codes/current` | Channel Gateway |
| 作废绑定码 | `CH-API-07` | `POST /api/v1/users/{user_id}/bind-codes/{bind_code_id}/revoke` | Channel Gateway |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 用户列表 | Skeleton | 暂无用户 | 错误+重试 | 列表 |
| Credential | loading | 未配置 | 验证失败 | 已配置/最近验证 |
| BindCode | 生成 loading | 无有效 code | 过期/作废 | 创建后一次明文显示 |

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
| RISK-09-01 | 把 /bind 当 Agent 授权 | 高 | UI 明确 IM 身份与 Agent 授权是独立 Tab | E2E/Integration |
| RISK-09-02 | 绑定码明文长期回显 | 高 | 仅创建响应一次展示；存储 hash | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-09-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-09-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
