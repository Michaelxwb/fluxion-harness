# Console公共框架 前端模块需求与设计简报

> **文档编号**: FE-00-V1.11  
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
| V1.13 | 2026-09-12 | 第三轮 Review 修复：场景 ID 前缀拆分（E2E 保留 `E-00-01`，integration 改名 `I-00-01`） |
| V1.13.1 | 2026-09-12 | Claude Code：第四轮 Review 修复——§3.4 新增 `StandardListQuery` 统一列表查询契约（Z-08：`page`/`page_size`/`keyword`/`sort`，服务端筛选、筛选变更重置 `page=1`、URL 为唯一事实源），并补场景 `S-00-03` |
| V1.14.1 第五轮契约同步 | 2026-09-13 | 第五轮 D8~D15 契约同步（ADR-067/D14）：§3.3.1 登录与身份合同补身份再查询入口 `GET /api/v1/auth/me`（`AUTH-API-01`，Owner=模块 09，返回 `{user_id, username, role, tenant_id}`），说明需要 `user_id` 或以当前身份渲染角色 UI 时读该接口、不以登录会话快照为准；登录响应契约 `{token, username, role}` 不变 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Console公共框架 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | 原 Console 设计把路由、列表规范、角色控制、详情/编辑规则散落在各页面，容易产生重复实现。 |
| 核心目标 | 提供所有 Console 页面共用的 Shell、路由守卫、标准列表、错误映射、时间格式和 Secret 展示规范。 |
| 路由 | `/console/*` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-00-01 | ConsoleLayout | 侧栏、breadcrumb、角色上下文、RouteOutlet | P0 | V0.8 / Playbook / 总设 |
| FEAT-00-02 | StandardListPage | 统一列表布局与 page/page_size 查询模型 | P0 | V0.8 / Playbook / 总设 |
| FEAT-00-03 | RouteGuard | Builder/Admin 页面与按钮权限 | P0 | V0.8 / Playbook / 总设 |
| FEAT-00-04 | ErrorMapper | 统一 HTTP/领域错误到 UI | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 提供所有 Console 页面共用的 Shell、路由守卫、标准列表、错误映射、时间格式和 Secret 展示规范。 |
| 非范围（Out of Scope） | 不建设 End User Console、不建设系统设置/基础设施运维页。 |
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
| S-00-01 | FEAT-00-01 | E2E | 登录与角色上下文 | 以 Builder 登录后进入 /console | 菜单不含用户/审计；角色上下文= BUILDER |
| S-00-02 | FEAT-00-01 | E2E | 401 统一处理 | token 过期后任意操作 | 跳转 /login；重新登录回原页面 |
| S-00-03 | FEAT-00-02 | E2E | 统一列表查询契约 | 在服务列表输入 keyword 并切换「启用状态」筛选，然后刷新页面 | 列表请求查询串为 `keyword=<输入值>&enabled=false&page=1&page_size=20`（服务端筛选；Network 面板中**无**无筛选的全量列表请求）；地址栏查询串与请求一致；刷新后 keyword、启用状态与页码均保留；在 `page=2` 时改动任一筛选后，下一次请求的 `page` 重置为 `1` |
| E-00-01 | FEAT-00-01 | E2E | Admin-only 接口 403 | Builder 直接调用 SVC-API-07 发布 | 错误映射为无权限提示，不白屏 |
| I-00-01 | FEAT-00-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

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
| Console公共框架 | `/console/*` | ConsoleLayout | 提供所有 Console 页面共用的 Shell、路由守卫、标准列表、错误映射、时间格式和 Secret 展示规范。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-00-01 | ConsoleLayout | 容器 | 布局、导航、角色上下文 |
| CMP-00-02 | StandardListPage | 容器 | 列表查询、筛选、分页、主操作 |
| CMP-00-03 | ReadOnlyDetail | 展示 | 只读详情壳 |
| CMP-00-04 | EntityFormModal | 容器 | 新增/编辑表单壳 |

### 3.3.1 登录与身份合同（V1.12 已冻结，代码已实现）

- 登录：`POST /api/v1/auth/login`（username/password）→ `{token, username, role}`；Bearer Token 有效期 12 小时，仅存 SHA-256 摘要；
- 注销：`POST /api/v1/auth/logout`（吊销当前 token）；
- 初始账号：`python -m apps.platform_api.bootstrap_admin <user> <pass>` 一次性创建 ADMIN，已存在则拒绝；
- 守卫：除 `POST /api/v1/auth/login` 外，所有 `/api/v1/*` 需 Bearer 会话（RULE-API-02）；Admin-only 接口 Builder 调用返回 403；失效/过期 token 返回 401，前端统一跳登录页；
- 角色来源：登录响应的 `role`（ADMIN/BUILDER）即 ConsoleLayout 角色上下文；**身份与角色的权威再查询入口为 `GET /api/v1/auth/me`（`AUTH-API-01`，Owner=模块 09，返回 `{user_id, username, role, tenant_id}`）**——需要 `user_id`（如服务测试的测试用户默认值）或以当前身份渲染角色相关 UI 时读该接口，不以登录响应的会话快照为准（V1.14.1）。

### 3.4 组件接口契约与字段

**标准列表契约**

| 区域 | 约束 |
|---|---|
| 左上 | 仅一个主动作（新增/导入） |
| 右上 | 搜索、筛选、刷新 |
| 中间 | Semi Table |
| 行末 | 详情/编辑/少量领域动作 |
| 右下 | PageSize + Pagination |

**`StandardListQuery`（统一列表查询契约，Z-08）**

所有 Console 列表页共用同一个查询模型，**序列化方式与分页语义全局一致**：

| 参数 | 类型/默认 | 说明 |
|---|---|---|
| `page` | int，默认 `1` | 页码 |
| `page_size` | int，默认 `20`，最大 `100` | 每页条数；page size 属于当前列表，不设全局值 |
| `keyword` | string | 关键词搜索 |
| `sort` | string，形如 `update_time_desc` | 排序；**未知取值由后端返回 422** |

页面可在该模型上追加**领域筛选参数**（如服务列表 `draft_state` / `enabled` / `execution_type`，见 `90` §2.1），但序列化方式与分页语义必须完全一致。

行为规则（三条）：

1. 筛选**一律服务端执行**：不得预载全量列表再在前端过滤（Network 面板中不出现无筛选的全量列表请求）；
2. 修改任一筛选/搜索/排序**重置 `page=1`**；
3. 筛选状态写入 **URL 查询串**：链接可分享、刷新后保留、后退可恢复。

URL 是列表筛选状态的**唯一事实源**——列表查询状态不得放进全局 store，只来自路由查询串 + 页面 local state。

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/00Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 标准列表 | 骨架屏 | 标准空态 | 错误提示+重试 | 表格+分页 |
| 详情 | Skeleton | — | 错误态 | 只读字段 |

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
| RISK-00-01 | 页面自行实现列表导致交互漂移 | 高 | 强制复用 StandardListPage | E2E/Integration |
| RISK-00-02 | 角色控制只藏按钮但后端无权限 | 高 | 前端只做体验控制，后端仍做真实授权 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-00-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-00-01 | applied |
| Console-V0.8#STANDARD-LIST-QUERY-Z08 | required | 列表查询统一 `StandardListQuery`（服务端筛选、改筛选重置 `page=1`、URL 唯一事实源） | §3.4 | S-00-03 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
