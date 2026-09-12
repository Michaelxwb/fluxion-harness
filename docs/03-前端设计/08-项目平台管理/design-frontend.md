# 项目平台管理 前端模块需求与设计简报

> **文档编号**: FE-08-V1.11  
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
| V1.13 | 2026-09-12 | 第三轮 Review 修复：§3.4 加角色可见性列并去掉误抄的「模型可见 protocol/model_name」措辞；列表/详情依赖 `PLAT-API-01` 的 `configured`（未配置显示「待配置」且凭据入口禁用）；写明 `configured`/`enabled` 的响应来源；场景 ID 前缀拆分（integration → `I-08-01`） |
| V1.13.1 | 2026-09-12 | 第四轮 Review 修复（D2=A 字段级授权，与 `90` §7.1 一致）：`PLAT-API-02/04` 由「仅 Admin」改为 **Builder + Admin（敏感字段仅 Admin）**——Builder 可填 `name`/`key`/`description`/`enabled`，`auth_type`/`auth_schema` 控件不渲染且提交体不含，只读展示 `auth_type` + `configured`；非 Admin 携带 `auth_type`/`auth_schema` → 403 `FIELD_ADMIN_ONLY`且原子拒绝；`PLAT-API-05` 与 `CRED-API-*` 维持整接口仅 Admin；重写 §3.4 角色可见性与 `E-08-01` 断言；§3.4 补「用户认证方式」列渲染 `display_name`（未提供回退 `auth_type` 原值、前端不写本地 key→中文映射表）与「停用二次确认与影响面（Z-02）」块（N=`capability_count`、M=`credential_user_count` 后端待补时只显示 N + 「用户认证影响待后端确认」、不用普通开关停用）；新增场景 `S-08-03`、`S-08-04`；§4 补 `RISK-08-02`/`RISK-08-03`/`RISK-08-04` |
| V1.14.2 第六轮 Review 收敛 | 2026-09-13 | 设计修复 | `PLAT-API-02/04` 由「Builder + Admin（敏感字段仅 Admin）」收紧为**整接口仅 Admin**：Builder 只读（`PLAT-API-01/03`），不渲染新增/编辑/验证按钮，直调得 403 `ADMIN_REQUIRED`；删除字段级 403 `FIELD_ADMIN_ONLY` 伪装与相关断言；同步角色可见性、未配置态、`E-08-01`、`RISK-08-02`、组件表 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 项目平台管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | ProjectPlatform 是 MSS/CRM/ERP 等业务平台产品对象，负责定义用户认证字段模板，并被 Platform Service Capability 引用。 |
| 核心目标 | 管理平台基本信息和唯一认证模板，不存储某个用户的实际账号 Secret。 |
| 路由 | `/console/project-platforms` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-08-01 | 平台 CRUD | 名称/标识/说明/状态 | P0 | V0.8 / Playbook / 总设 |
| FEAT-08-02 | 认证模板 | 动态字段 schema | P0 | V0.8 / Playbook / 总设 |
| FEAT-08-03 | Schema 校验 | 发布前验证字段结构 | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 管理平台基本信息和唯一认证模板，不存储某个用户的实际账号 Secret。 |
| 非范围（Out of Scope） | 不承担 ProjectIntegration/Provider Registry 的部署扩展配置。 |
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
| S-08-01 | FEAT-08-01 | E2E | 创建即未配置态 | Admin 仅填名称/标识创建平台 | 创建成功；认证模板显示"未配置"（UNCONFIGURED），不隐式生成用户名密码 |
| S-08-02 | FEAT-08-02 | E2E | 认证模板配置 | 在详情中添加字段定义并保存 | auth_schema 更新；已配置后修改有破坏性提示 |
| S-08-03 | FEAT-08-01 | E2E | 停用影响面（Z-02） | Admin 在平台列表停用一个被 N 个能力引用、有 M 名用户配置认证的平台；先点「取消」，再点「停用」确认（含 M 未返回的后端版本） | 确认 Modal 正文与 `90` §1.2.1 冻结文案一致，含「N 个平台服务能力将不可运行」与「M 名已配置该平台认证的用户的认证将失效」；M 未返回时正文只显示 N 并含「用户认证影响待后端确认」，**不出现捏造的 `0`**；点「取消」后该行 `enabled` **不变**且未发出停用请求；点「停用」后发出停用请求且该行状态翻转 |
| S-08-04 | FEAT-08-02 | E2E | 认证方式展示名 | Admin 查看一个已配置 `auth_type` 的平台；另用省略展示名的响应再渲染一次 | 「用户认证方式」列渲染该 Provider 注册项的 `display_name`（如「用户名密码」），**不出现**裸 key；响应省略展示名时**回退渲染 `auth_type` 原值**（不为空白、不猜中文） |
| E-08-01 | FEAT-08-01 | E2E | Builder 只读边界（整接口仅 Admin） | Builder 创建 PLATFORM_SERVICE 能力选平台；打开平台管理列表与一个平台详情；直调 `PLAT-API-02` 新增平台 | 平台下拉来自 `PLAT-API-01` 安全只读投影（`name`/`key`/`configured`/`enabled`）；列表与详情正常渲染但 **DOM 无「新增」「编辑」「验证认证模板」按钮**，行内也无编辑入口；直调 `PLAT-API-02`（无论携带什么字段）得 **403 `ADMIN_REQUIRED`**，前端映射为无权限提示且不白屏；`configured=false` 的行「用户认证方式」列显示「待配置」，凭据编辑/验证入口为**禁用态并给出原因**（对 Builder 不渲染） |
| I-08-01 | FEAT-08-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| 项目平台管理 | `/console/project-platforms` | ConsoleLayout | 管理平台基本信息和唯一认证模板，不存储某个用户的实际账号 Secret。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-08-01 | ProjectPlatformListPage | 容器 | 列表 |
| CMP-08-02 | ProjectPlatformForm | 容器 | 基础字段 |
| CMP-08-03 | AuthSchemaEditor | 容器 | 认证字段模板编辑（**整页仅 Admin 可达**；Builder 打开详情时该区块只读，仅显示「待配置 / 已配置」状态，且页面无编辑入口） |

### 3.4 组件接口契约与字段

**列表**

| 列 | DTO 字段 | 必填 | Admin 可见 | Builder 可见 | 说明 |
|---|---|---:|---:|---:|---|
| 平台名称 | `name` | 是 | 是 | 是 | |
| 平台标识 | `key` | 是 | 是 | 是 | 创建后不可改 |
| 用户认证方式 | `auth_type` | — | 是 | 是 | **不渲染裸 key**：渲染该 `auth_type` 对应 Provider 注册项的 **`display_name`**（Y-08）；响应未提供展示名时**回退渲染 `auth_type` 原值**（不显示空白、不猜中文）；前端**不写本地 key→中文映射表**；`configured=false` 时显示「待配置」 |
| Platform Service 能力数 | `capability_count`（反查计数） | — | 是 | 是 | 停用影响面的 N 取该字段（`PLAT-API-01`/`PLAT-API-03`） |
| 配置态 | `configured` | — | 是 | 是 | 后端派生（`auth_type != UNCONFIGURED`），来自 `PLAT-API-01` 响应 |
| 状态 | `enabled` | — | 是 | 是 | 来自 `PLAT-API-01` 响应 |
| 版本 | `revision` | — | 是 | 是 | |
| 更新时间 | `update_time` | — | 是 | 是 | `YYYY-MM-DD HH:mm:ss` |

**新增**：名称、标识、说明、状态（`enabled`）。

**认证模板字段**：field key、中文标签、input type、required、secret、校验规则/提示。V1 每个平台一个模板。

**角色授权（V1.14.2 收敛：整接口仅 Admin）**：`PLAT-API-02`（新增）/ `PLAT-API-04`（编辑）**收紧为整接口仅 Admin**——平台承载用户认证模板，模板即凭据契约，字段级拆分（Builder 可改 `name`/`description`、仅 Admin 可改 `auth_type`/`auth_schema`）会让同一次表单提交产生"部分生效"的语义，且前端必须复刻后端的字段级 403 判定（`FIELD_ADMIN_ONLY`）才能对齐按钮可见性——这是把授权规则复制到前端。

- **Builder 只读**：可访问 `PLAT-API-01`（列表）/ `PLAT-API-03`（详情），可见 `key` / `name` / `auth_type` / `configured` / `enabled` / `revision`；**不渲染**「新增」「编辑」「验证认证模板」按钮，列表行也不提供编辑入口（`90` §7.1 同步）。
- Builder 直接调用 `PLAT-API-02/04` 得 **403 `ADMIN_REQUIRED`**（接口级拒绝，不再是 `FIELD_ADMIN_ONLY` 字段级拒绝）。
- Builder 仍可在能力表单选择平台（`PLAT-API-01` 安全只读投影），这是其定义 PLATFORM_SERVICE 能力的必要依赖。

**未配置态**：Admin 新增时可只提交 name/key/description（auth_type 缺省或 null 统一存 UNCONFIGURED，auth_schema={}），列表 configured=false 显示“待配置”。Admin 保存模板后变 configured=true；未配置平台的凭据编辑/验证入口禁用并说明原因；不将空模板当可用用户名密码认证。

**列表查询**：通用契约见 FE-00 §3.4 `StandardListQuery`，本页领域筛选：`enabled`（另加 `keyword` 按名称/标识搜索），服务端筛选、改筛选重置 `page=1`、状态进 URL。

**停用二次确认与影响面（Z-02）**：停用项目平台是**跨对象**危险操作（见 `../90-Console交互规格.md` §1.2.1），**不得**用普通开关停用——必须 Modal 二次确认，主按钮文案「停用」、取消为默认焦点，确认正文为影响面正文（不省略）：

```text
停用后，该平台的 N 个平台服务能力将不可运行，M 名已配置该平台认证的用户的认证将失效。
```

| 项 | 规范 |
|---|---|
| N | 取 `PLAT-API-01` / `PLAT-API-03` 的 `capability_count`（列表「Platform Service 能力数」列同源） |
| 计数呈现 | 为只读文本，不提供写操作；取消按钮为默认焦点，取消后 `enabled` 不变 |

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/08Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| 项目平台列表 | `PLAT-API-01` | `GET /api/v1/project-platforms` | Auth 与项目平台 |
| 新增项目平台 | `PLAT-API-02` | `POST /api/v1/project-platforms` | Auth 与项目平台 |
| 项目平台详情 | `PLAT-API-03` | `GET /api/v1/project-platforms/{platform_id}` | Auth 与项目平台 |
| 编辑项目平台 | `PLAT-API-04` | `PUT /api/v1/project-platforms/{platform_id}` | Auth 与项目平台 |
| 验证认证 Schema | `PLAT-API-05` | `POST /api/v1/project-platforms/{platform_id}/validate-auth-schema` | Auth 与项目平台 |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无平台 | 错误+重试 | 列表 |
| Schema Editor | loading | 无字段 | 校验错误定位字段 | 模板 |

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
| RISK-08-01 | 把用户实际凭据写进平台模板 | 高 | 模板只定义结构，用户值走 Credential API/Secret Provider | E2E/Integration |
| RISK-08-02 | 若后端把 `PLAT-API-02/04` 改回 Builder 可写，前端必须重新长出字段级 403 判定 | 中 | V1.14.2 已收敛为整接口仅 Admin（`ADMIN_REQUIRED`）：前端只渲染 Admin 的写按钮，不需要复刻字段级授权；后端若变更授权列须同步本页与 `90` §7.1，否则会出现“按钮渲染了但提交 403” | E2E/Integration |
| RISK-08-04 | 模块 `12` Provider 注册项 `display_name` 待补 | 中 | 响应未提供展示名时前端回退渲染 `auth_type` 原值；**不写本地 key→中文映射表**，避免与注册表漂移 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-08-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-08-01 | applied |
| BACKEND-09#FIELD-ADMIN-ONLY | required | 非 Admin 携带 `auth_type`/`auth_schema` 即 403 且原子拒绝 | §3.4 | E-08-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
