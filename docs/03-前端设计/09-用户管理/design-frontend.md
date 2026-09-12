# 用户管理 前端模块需求与设计简报

> **文档编号**: FE-09-V1.11  
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
| V1.13 | 2026-09-12 | 第三轮 Review 修复：凭据弹窗删除「状态（启用/停用）」可写控件，改为只读展示 `status`/`verified_at`/`credential_expires_at`/`session_expires_at`，并声明 V1 无「停用」概念（停用=删除 `CRED-API-04`）；Memory「置信度」标注来源为 `MEM-API-01` 的 `confidence`；补场景 `S-09-05`；场景 ID 前缀拆分（integration → `I-09-01`） |
| V1.13.1 | 2026-09-12 | Claude Code 第四轮 Review 修复：§3.4 补角色三条守卫（Z-12）与用户停用二次确认影响面（Z-02）；补场景 `S-09-06`、`E-09-02`；§4 补 `USR-API-04` 后端依赖 |

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
| 非范围（Out of Scope） | 不提供用户来源字段，不把 ChannelIdentity、AgentAccessGrant、ProjectCredential 合并成一张“绑定”表；**不提供平台凭据的「启用/停用」概念**——V1 停用即删除（`CRED-API-04`），`status` 为系统维护的只读状态。 |
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
| S-09-05 | FEAT-09-02 | E2E | 凭据只读状态与停用语义 | 在项目平台认证 Tab 打开配置 Modal 保存一份凭据 → 执行验证 → 删除该凭据 | 保存后只读区 `status=UNVERIFIED`；验证成功后转为 `VALID` 且 `verified_at` 有值；删除后回到「未配置」；弹窗内**不存在**「启用/停用」控件（DOM 中无该 select） |
| S-09-06 | FEAT-09-01 | E2E | 角色守卫 | Admin ①编辑自己 ②把他人升级为 Admin ③对最后一名启用 Admin 尝试降级 | ①角色下拉 `disabled` 且 hover 显示「不能修改自己的角色」；②提交前出现确认 Modal，正文为「将该用户升级为 Admin？Admin 可管理用户、授权、凭据与正式发布。」，取消后下拉回滚到原值；③非 `ADMIN` 角色选项与状态开关均 `disabled`，hover 显示「系统必须保留至少一名启用状态的 Admin」 |
| E-09-02 | FEAT-09-01 | E2E | 停用影响面 | Admin 停用一名已绑定 IM 身份的用户 | 确认 Modal 正文含「将立即中断该用户全部 IM 调用」与 `im_identity_count`/`agent_grant_count`/`project_credential_count` 三个计数；点击取消后用户 `status` 仍为 `ACTIVE`，列表与详情均未变 |
| E-09-01 | FEAT-09-03 | E2E | 授权与 IM 身份不混淆 | 查看三类 Tab | 平台认证/Agent 授权/IM 身份分列展示，操作互不影响 |
| I-09-01 | FEAT-09-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

> 场景编号说明：`E-09-01` 已被「授权与 IM 身份不混淆」占用，故用户停用影响面场景编为 `E-09-02`。

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

**列表**：用户名称、标识、**角色（`role`）**、状态、项目平台认证数、智能体授权数、IM 身份数、更新时间；**筛选**：角色（`role`）、状态（`status`）。冻结交互稿的「角色」列与筛选在 V1 保留（Admin 需按角色找人/筛人，`USR-API-01` 支持 `role` 参数）；V1.13 曾删除该列，V1.13.1 按原型恢复。

**列表查询**：通用契约见 FE-00 §3.4 `StandardListQuery`，本页领域筛选：`role`/`status`（另加 `keyword` 按名称/标识搜索），服务端筛选、改筛选重置 `page=1`、状态进 URL。

**项目平台认证**：平台、认证状态、账号摘要、最近验证时间、操作；Secret 不回显。

**凭据配置 Modal（D12，V1.13 冻结）**：按 `ProjectPlatform.auth_schema` 动态渲染输入控件，Secret 字段每次输入、不回显；保存与验证之外**没有**「状态（启用/停用）」可写控件。Modal 底部为**只读**状态区：

| 展示项 | DTO 字段 | 取值/说明 |
|---|---|---|
| 认证状态 | `status` | `UNVERIFIED` / `VALID` / `INVALID` / `EXPIRED`，由系统维护 |
| 最近验证时间 | `verified_at` | `YYYY-MM-DD HH:mm:ss` |
| 凭据有效期 | `credential_expires_at` | `YYYY-MM-DD HH:mm:ss`；失效提示重新配置 |
| 会话有效期 | `session_expires_at` | `YYYY-MM-DD HH:mm:ss`；由系统刷新 |

**停用语义**：V1 **没有**「停用某平台认证」这一状态；要停用即**删除**该条凭据（`CRED-API-04`），删除后该平台回到未配置。列表行操作只保留「配置/更新」「验证」「删除」（删除二次确认）。弹窗内**不存在**任何可写的「启用/停用」控件（DOM 中无该控件），`status` 只读；删除凭据的二次确认影响面按 `90` §1.2.1（删除后该平台回到「未配置」，需重新配置才能调用）。

**智能体授权**：智能体、标识、授权时间、状态、撤销；`+ 授权智能体`。

**IM 身份**：渠道、外部用户标识、绑定时间、状态、解除绑定。BindCode 创建响应一次展示明文和 `/bind CODE`，后续只显示存在/有效期/重新生成/作废。

**用户记忆（Memory Tab，Admin-only）**：只读列表——memory_key、值摘要、来源（`source_type`/`source_ref`）、置信度、更新时间；操作=单条删除（确认弹窗，MEM-API-03；按 memory_key 删除）。批量清理为逐条删除，不提供整表一键清空。删除 Memory 不影响业务审计记录（数据生命周期分离，总设 §5.5）。「置信度」列来自 **`MEM-API-01` 响应的 `confidence` 字段**（后端已补该字段，见模块 11）；响应无该字段时不渲染该列，前端不计算置信度。



**新增/编辑 DTO**：新增提交 display_name/role/status/description，不提交 user_key，成功显示服务器生成值；编辑先读取详情 revision 并原样回传，冲突保留表单并提示重新加载，不覆盖他人更新。平台认证区 credential_expires_at 与 session_expires_at 分开展示，前者失效提示重新配置，后者由系统刷新；Memory 使用 EXPLICIT/DERIVED/ADMIN 来源，仅 Admin 治理。

**角色三条守卫（Z-12，V1.13.1 冻结）**：新增/编辑表单的角色不是可自由升降的普通字段，按 `90` §8.2 的三条守卫实现：

| # | 守卫 | 控件行为 | 违反时的呈现 |
|---|---|---|---|
| 1 | 不能修改自己的角色 | 被编辑用户 = 当前登录用户时，角色下拉 `disabled` | tooltip：「不能修改自己的角色」；后端 `USR-API-04` 同步拒绝 |
| 2 | 升级为 Admin 需二次确认 | `role` 由 `BUILDER`/`END_USER` 改为 `ADMIN` 时，提交前弹确认 Modal | Modal 正文：「将该用户升级为 Admin？Admin 可管理用户、授权、凭据与正式发布。」；取消则角色下拉**回滚**到修改前的原值 |
| 3 | 最后一名启用 Admin 禁止降级或停用 | 目标用户是当前唯一 `status=ACTIVE` 的 Admin 时，`ADMIN` 以外的角色选项与状态开关均 `disabled` | tooltip：「系统必须保留至少一名启用状态的 Admin」；后端 `USR-API-04` 返回专用冲突错误码（已冻结，`E-USER-04`/`E-USER-05`），前端按字段级错误呈现并**回滚**控件到原值 |

守卫 1/3 的判定数据只读用户详情响应（`USR-API-03` 的 `role`/`status`）与当前会话身份（登录响应 `role`，FE-00 §3.3.1），**不读本地缓存、不硬编码 Admin 数量**；角色控件在详情请求 resolve 之前保持 `disabled`，避免先改后拉造成状态错乱。

**用户停用二次确认与影响面（Z-02）**：停用用户必须 Modal 二次确认，**不得**用无影响面提示的普通开关停用；正文按 `90` §1.2.1 给出「停用后将**立即中断该用户全部 IM 调用**」及受影响计数：IM 身份数 `im_identity_count`、Agent 授权数 `agent_grant_count`、项目平台认证数 `project_credential_count`，三者取自 `USR-API-01`/`USR-API-03` 响应；点击取消后 `status` 保持 `ACTIVE` 不变，不发出 `USR-API-04` 请求。

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
| 新增用户 Agent 授权 | `USR-API-06` | `POST /api/v1/users/{user_id}/agent-grants`（body: `agent_id` + `idempotency_key`） | 用户与 Agent 授权 |
| 撤销用户 Agent 授权 | `USR-API-06R` | `POST /api/v1/users/{user_id}/agent-grants/{grant_id}/revoke`（body: `idempotency_key`） | 用户与 Agent 授权 |
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
| RISK-09-03 | 角色守卫 1/3 与「最后一名启用 Admin」专用冲突错误码依赖后端 | 高 | 已冻结（`E-USER-04`/`E-USER-05`）：模块 18 `USR-API-04` 拒绝修改自己的角色（403 `SELF_ROLE_CHANGE_DENIED`）、拒绝最后一名启用 Admin 降级/停用（409 `LAST_ADMIN_PROTECTED`）；前端按字段级冲突回滚控件，不得只靠 UI 禁用 | S-09-06 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-09-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-09-01 | applied |
| BACKEND-18#ADMIN-GUARD-E-USER-04-05 | required | 自改角色 403 `SELF_ROLE_CHANGE_DENIED`、最后一名 Admin 409 `LAST_ADMIN_PROTECTED` | §3.4 | S-09-06 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
