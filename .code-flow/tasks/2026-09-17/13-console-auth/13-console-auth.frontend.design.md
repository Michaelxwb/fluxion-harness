# Console 账号与访问控制 前端模块需求与设计简报

> **文档编号**: FE-AUTH-V1.0
> **文档版本**: v1.1
> **创建日期**: 2026-09-17
> **文档状态**: 设计评审中
> **模板**: design-frontend.md
> **交互基线**: `智能服务交付平台-V1.4-交互稿.html`
> **源文档**: `详细设计-V1.4`（docs/03 §3.1/§3.3/§11、docs/07 §1、docs/15）

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | Console 账号与访问控制（前端） |
| Owner | muad-console-platform |
| 前端目录 | `apps/console-platform/frontend/src/auth/`、`src/pages/LoginPage.tsx`、`src/api/auth.ts`、`src/api/client.ts`、`src/config/menu.ts`、`src/layout/AppLayout.tsx`、`src/locales/` |
| 前置模块 | 01-platform-foundation（ConsoleShell / ApiClient / i18n 基础） |
| 后端契约 | 13-console-auth.backend.design.md（API-01..API-06） |
| 非职责 | 不实现账号 CRUD 详情页（后置）；不引入 Redux/Zustand；组件不裸用 axios/fetch |

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 开发负责人 | muad-console-platform | 技术方案、代码实现 |
| 设计/交互 | fluxion-harness 设计组 | 登录页与 Shell 用户区视觉 |
| 测试负责人 | 14-dfx-acceptance | 浏览器 E2E 与角色守卫验收 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v0.1 | 2026-09-17 | muad-console-platform | 初始草稿：按已实现前端基线整理 |
| v1.0 | 2026-09-17 | muad-console-platform | 需求评审通过 |
| v1.1 | 2026-09-18 | muad-console-platform | 对齐 V1.4 决策（docs/17）：交互基线更新为 V1.4；补充角色过滤与 CSRF/401 数据流 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | Console 账号与访问控制（前端） |
| 需求类型 | 页面/组件/交互实现（安全入口） |
| 业务背景 | Console 无自助注册；浏览器入口必须完成登录、会话续存、角色过滤与统一错误呈现，且所有请求经统一 ApiClient |
| 核心目标 | 提供登录页与会话引导，让未登录用户无法看到任何业务页面，让 Builder 看不到仅 ADMIN 的入口，并让 401/403/CSRF 失败有确定性的 UI 行为 |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-FE-01 | 登录页 | 用户名/密码表单、必填校验、提交 loading、失败 Toast、成功进入概览 | P0 | US-01 |
| FEAT-FE-02 | 会话引导与路由守卫 | 启动调用 `/auth/me`；加载态 Spin；未登录跳转 `/login`；`RequireAuth` 包裹 ConsoleShell | P0 | US-01 |
| FEAT-FE-03 | 统一 ApiClient | 自动注入 `X-Locale`、`X-Request-Id`、`X-CSRF-Token`；Envelope `code != 0` Toast；401 跳转登录 | P0 | US-05 |
| FEAT-FE-04 | 角色过滤 | 菜单按 `adminOnly` 过滤（用户菜单仅 ADMIN）；路由级 `RequireRole` 守卫；凭据类入口（ProjectPlatform 凭据页签/按钮）仅 ADMIN，前端隐藏 + 后端 403 兜底 | P0 | US-02 |
| FEAT-FE-05 | 当前用户与退出 | Header 显示 `display_name · 角色`；下拉退出登录并回到 `/login` | P0 | US-01 |
| FEAT-FE-06 | 账号管理入口（ADMIN，后置） | 账号列表/新建入口与时间展示；当前为占位路由，页面随后续迭代落地 | P1 | US-03 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | 登录页、AuthProvider 会话状态、`RequireAuth`/`RequireRole`、ApiClient 拦截器（locale/request-id/CSRF/401/Toast）、菜单与路由角色过滤、退出登录、zh-CN/en-US 文案 |
| Out of Scope | 不做自助注册、找回密码、记住我、第三方登录；不做账号详情/改密的完整页面（后续迭代）；不新增状态库；不在 localStorage/内存持久化密码或令牌 |
| 有意妥协 / 技术债 | `/users` 当前为占位页；账号管理与改密页面待实现；凭据类操作在 ProjectPlatform 页面的角色隐藏由 04 模块负责，本模块只提供守卫与依赖 |

### 2.4 验收条件

> 交互层面的可验证场景，写到可转 E2E / 组件交互测试断言的粒度。测试层级只能填 `unit` / `integration` / `E2E` / `manual`；跨路由、服务请求、状态更新和最终 UI 的用户流程必须标为 `E2E`。关键真实边界列出不得 mock 的浏览器、路由、状态或服务边界。

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|---|
| S-FE-01 | FEAT-FE-01 | P0 | E2E | Browser → Router → ApiClient → API → Postgres | 输入正确用户名/密码并提交 | 跳转 `/`；概览占位页可见；Header 显示显示名与角色；请求带 `X-Locale`/`X-Request-Id`/`X-CSRF-Token` |
| S-FE-02 | FEAT-FE-02 | P0 | E2E | Browser → Router → `/auth/me` | 登录后刷新页面 | 启动显示 Spin；`/me` 成功后回到原路由；不闪回登录页 |
| S-FE-03 | FEAT-FE-03 | P0 | E2E | Locale → ApiClient → API msg → UI | 切换到 English 后触发一个业务错误 | 页面文案与 Toast `msg` 均为 en-US；刷新后语言保持 |
| S-FE-04 | FEAT-FE-04 | P0 | E2E | AuthContext → menu → Router → 04 凭据入口 | ADMIN 与 BUILDER 分别登录 | ADMIN 可见“用户”菜单并可进 `/users`；BUILDER 菜单无“用户”；ProjectPlatform 凭据页签/操作仅 ADMIN 可见（04 模块复用 `RequireRole`，后端 403 兜底） |
| S-FE-05 | FEAT-FE-05 | P0 | E2E | UI → logout API → Router | Header 下拉点击退出登录 | 返回 `/login`；再访问受保护路由仍跳登录页 |
| S-FE-06 | FEAT-FE-06 | P1 | integration | Router → ADMIN 守卫 → 列表 | ADMIN 打开 `/users`；账号列表展示 `last_login_at` | 时间按 `YYYY-MM-DD HH:mm:ss` 展示；BUILDER 被重定向（后置页面落地后生效） |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|---|---|---|---|---|---|
| E-FE-01 | FEAT-FE-03 | integration | ApiClient 响应拦截 → Router | 任一请求返回 401 且当前不在 `/login` | `window.location.assign('/login')`；不残留加载态；不出现未捕获异常 |
| E-FE-02 | FEAT-FE-01 | integration | LoginPage → API 错误 → Toast | 错误密码（`INVALID_CREDENTIALS`）或锁定（`ACCOUNT_LOCKED`） | Toast 展示本地化 `msg`；停留登录页；按钮 loading 复位；密码框不残留已提交值以外的日志 |
| E-FE-03 | FEAT-FE-04 | integration | RequireRole → Router | BUILDER 直接输入 `/users` | 重定向 `/`，不发起该页面数据请求 |
| E-FE-04 | FEAT-FE-03 | integration | ApiClient 请求拦截 → CSRF | 非安全方法在 `muad_csrf` 缺失/过期时提交 | 后端 403 `FORBIDDEN`；Toast 提示；不误显示成功态 |
| E-FE-05 | FEAT-FE-02 | integration | AuthProvider → `/me` 失败 | 启动时 `/me` 返回 401 或网络错误 | 进入 `/login`；无空白页、无重定向循环 |

**边界场景**

| 场景ID | 测试层级 | 关键真实边界 | 字段/条件 | 边界值 | 预期行为 |
|---|---|---|---|---|---|
| B-FE-01 | unit | LoginPage 表单校验 | 用户名/密码为空 | 空字符串 | 表单必填提示，不发起请求 |
| B-FE-02 | unit | i18n 资源 | locale key 双语完整性 | zh-CN/en-US | `make i18n-check` 通过，无缺失 key |

**非功能指标**

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|---|---|---|---|
| NFR-FE-01 | 登录页首屏可交互 | 待定 | Lighthouse / 实测 |
| NFR-FE-02 | 交互失败反馈时延 | 待定（请求返回后立即 Toast） | 实测 |

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 框架 | React + TypeScript + Vite | React 18.3.x / TS 5.7 | 与 01-platform-foundation 一致 |
| 状态管理 | React Context（AuthProvider）+ 页面局部 state | - | 认证状态全局唯一，无需引入 Redux/Zustand |
| 路由 | react-router-dom | 6.x | 嵌套路由 + 守卫组合 |
| 样式方案 | Semi Design tokens + 内联布局常量 | `@douyinfe/semi-ui` 2.84.x | 与 Console 视觉一致，避免魔法值扩散 |
| 数据请求 | axios 统一 `api` 实例（`src/api/`） | axios 1.x | 集中注入 locale/CSRF/401 行为 |
| 国际化 | react-i18next，locale 仅 `zh-CN/en-US` | - | 与后端 `X-Locale` 协商一致 |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| 登录页 | `/login` | 独立居中 Card（不套 ConsoleShell） | 未登录唯一可达页面 |
| Console 主体 | `/*` | `RequireAuth → AppLayout` | 概览/Agent/Skill/MCP/模型/项目平台/后台任务/定时任务/运行审计 |
| 用户管理（仅 ADMIN） | `/users` | `RequireRole role="ADMIN"` | 当前为占位页，后置落地账号列表 |

守卫规则：

```text
/login            -> 已登录则 Navigate "/"（登录页自检）
RequireAuth       -> loading 显示 Spin；无账号 Navigate "/login"
RequireRole       -> 无账号 Navigate "/login"；角色不符 Navigate "/"
```

### 3.3 组件设计

**组件树**

```text
<App>
├─ <LoginPage>                     # 容器：表单提交 + 跳转
└─ <RequireAuth>
   └─ <AppLayout>                  # 容器：菜单过滤 + 用户区 + Outlet
      ├─ <Nav>                     # 展示：menuItems 过滤后的固定十项
      ├─ <Header>
      │  ├─ <LocaleSwitch>         # Select：zh-CN / en-US
      │  └─ <UserDropdown>         # 展示：display_name · 角色 + 退出
      └─ <Outlet>
         └─ <RequireRole role="ADMIN"> <PlaceholderPage/> </RequireRole>   # /users
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-01 | `AuthProvider` | 容器 | 模块内 | 启动 `/me`、account/loading 状态、login/logout |
| CMP-02 | `RequireAuth` | 容器 | 全模块复用 | 会话守卫 |
| CMP-03 | `RequireRole` | 容器 | 后续 ADMIN 页面复用（含凭据类页面） | 角色守卫 |
| CMP-04 | `LoginPage` | 容器 | 模块内 | 登录表单、错误 Toast、跳转 |
| CMP-05 | `AppLayout` | 容器 | 01 提供，本模块消费 | 菜单过滤、Header 用户区、Outlet |
| CMP-06 | `api`（Axios 实例） | 服务 | 全模块复用 | locale/request-id/CSRF/Envelope/401 |
| CMP-07 | `menuItems` | 配置 | 全模块复用 | 固定十项菜单与 `adminOnly` 标记 |

**必须复用公共组件**：`ConsoleShell / ModuleToolbar / RemoteTable / EntityLink / DetailSideSheet / DetailTabs / FormModal / StatusTag / DateTimeText / ConfirmAction / EmptyState / ErrorState / PaginationFooter / LocaleSwitch`。登录页与本模块守卫不得另造壳层；`/users` 后续页面必须复用公共列表/详情组件。

#### 3.3.1 每个按钮/操作的设计

| 位置 | 按钮/链接 | Semi 组件 | 层级 | 行为 | Service/API | 二次确认 |
|---|---|---|---|---|---|---|
| LoginPage | 登录 | `Button htmlType="submit" theme="solid" loading` | primary | 校验 → `login()` → 跳转 `/` | `POST /api/v1/auth/login` | 否 |
| Header | 中文 / English | `Select` | locale | 切换并持久化 `muad.locale`；ApiClient 后续请求带新 `X-Locale` | `-` | 否 |
| Header | 用户下拉 | `Dropdown` | user | 显示 `display_name · 角色`；点击退出 | `POST /api/v1/auth/logout` | 否 |
| 路由守卫 | 自动重定向 | `Navigate` | guard | 未登录→`/login`；越权→`/` | `-` | 否 |

统一规则：主操作 `theme="solid" type="primary"`；危险操作 `Popconfirm`；详情全局操作与关闭 X 同行靠右；Tab 内关系操作完成即生效。

### 3.4 组件接口契约

```ts
export type ConsoleRole = 'ADMIN' | 'BUILDER';

export interface ConsoleAccount {
  id: string;
  username: string;
  display_name: string;
  role: ConsoleRole;
}

export interface AuthContextValue {
  account: ConsoleAccount | null;
  loading: boolean;
  login(username: string, password: string): Promise<void>;
  logout(): Promise<void>;
}

export function RequireAuth(props: { children: ReactElement }): ReactElement;
export function RequireRole(props: { role: ConsoleRole; children: ReactElement }): ReactElement;
```

| Props | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `RequireAuth.children` | ReactElement | 是 | - | 受保护路由元素 |
| `RequireRole.role` | `ConsoleRole` | 是 | - | 允许的角色白名单（单角色） |
| `RequireRole.children` | ReactElement | 是 | - | 角色匹配时渲染 |

| Events / 回调 | 载荷类型 | 触发时机 |
|---|---|---|
| `AuthContextValue.login` | `(username: string, password: string) => Promise<void>` | 登录页提交 |
| `AuthContextValue.logout` | `() => Promise<void>` | Header 下拉退出 |

组件内禁止直接修改 props；账号状态只能经 `AuthProvider` 更新。

### 3.5 状态与数据流

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|---|---|---|---|
| account | shared（AuthContext） | `ConsoleAccount \| null` | AuthProvider 写；守卫/Header/Dropdown 读 |
| loading | shared（AuthContext） | boolean | 启动 `/me` 期间 true |
| submitting | local（LoginPage） | boolean | 登录提交期间按钮 loading |
| locale | shared（i18next + localStorage `muad.locale`） | `zh-CN \| en-US` | LocaleSwitch 写；ApiClient/页面读 |
| 菜单可见项 | 派生（`menuItems.filter(adminOnly)`） | `MenuItem[]` | AppLayout 按 account.role 计算 |

**数据流**

```text
用户操作 → Page/Context → src/api/auth.ts 或 src/api/client.ts
  → Axios 拦截器（X-Locale / X-Request-Id / X-CSRF-Token）
  → 后端 Envelope → 拦截器判 code / 401
  → State → 重渲染（Toast / Navigate / Header）
```

**数据获取层**：API 调用必须经 `src/api/` 封装；组件/展示层不出现裸 `fetch`/`axios`。密码只在提交函数参数中存在，不写入任何 store/localStorage/sessionStorage。

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|---|---|---|
| `login(username, password)` | `POST /api/v1/auth/login` | `AuthProvider.login` / `LoginPage` |
| `logout()` | `POST /api/v1/auth/logout` | `AuthProvider.logout` / Header 下拉 |
| `fetchCurrentAccount()` | `GET /api/v1/auth/me` | `AuthProvider` 启动 effect |
| `changePassword(payload)` | `POST /api/v1/auth/password` | 后续改密页面（后置） |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 启动会话引导 | 居中 `Spin size="large"` | - | 进入 `/login` | 渲染 ConsoleShell |
| 登录页 | 提交按钮 loading | - | Toast（本地化 `msg`）+ 表单保留 | 跳转 `/` |
| 菜单/Header | 账号加载完成后渲染 | - | 无账号跳登录 | 显示 `display_name · 角色` |
| 账号列表（后置） | Table loading | Empty + 清筛选 | ErrorState + 重试 | Table + 右下 Pagination；时间列 `YYYY-MM-DD HH:mm:ss` |
| 修改密码（后置） | 保存按钮 loading | - | Form 字段错误 + Toast | Toast + 关闭表单 |

### 3.7 样式方案

| 维度 | 约定 |
|---|---|
| 样式与逻辑分离 | 登录页与 Shell 使用 Semi 组件与 token；不内联业务逻辑颜色/间距魔法值（布局常量除外） |
| 设计 tokens | 颜色/间距/字号引用 Semi token（如 `--semi-color-bg-1`） |
| 响应式断点 | 登录 Card 固定 360px 居中；Console 列表/详情沿用 01 断点（<900px 双列降单列） |
| 列表规范 | 后续账号列表遵循“左上操作 + 右上搜索筛选 + 右下分页”，不重复页签标题 |

### 3.8 可访问性与兼容性

| 维度 | 要求 |
|---|---|
| 可访问性 | 登录表单 label 与必填提示；按钮可键盘触发；错误 Toast 不替代字段级提示；路由跳转后焦点回到页面主标题（后续增强） |
| 浏览器/设备兼容 | Chrome/Edge 企业当前版本为主；Safari 做开发兼容验证 |

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|---|---|---|---|---|
| RISK-FE-01 | 硬编码中文或漏翻译 | 切换语言后文案不一致 | 所有文案走 `t(key)`；`make i18n-check` 双语检查 | S-FE-03, B-FE-02 |
| RISK-FE-02 | 401 处理不当导致循环跳转/空白页 | 用户无法恢复会话 | 仅当非 `/login` 时跳转；`/me` 失败收敛为未登录 | E-FE-01, E-FE-05 |
| RISK-FE-03 | CSRF 令牌读取/回传失败 | 变更请求全部 403 | 统一请求拦截器读 `muad_csrf`；后端 403 时 Toast 提示 | E-FE-04 |
| RISK-FE-04 | 密码/令牌进入前端持久化 | 安全泄漏 | 只保留在请求参数；不写 localStorage/sessionStorage；日志脱敏 | S-FE-01, E-FE-02 |
| RISK-FE-05 | 角色过滤仅前端 | 越权可通过直接调用 API | 前端过滤仅改善体验；后端 `require_admin` 为最终边界 | E-FE-03 |

## Spec Compliance Matrix

> 从需求目录 `spec-context.yml` 继承并逐 Rule 回填。required Rule 必须有具体设计落点和 verifier/验收场景；N/A 只接受逐项用户确认。

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-platform#RULE-i18n-001` | required | 前端页面 zh-CN/en-US；`X-Locale` 协商；只加词条不改框架 | §3.1/§3.5；locale 文件 | S-FE-03, B-FE-02 + verifier | applied |
| `harness-platform#RULE-ui-001` | required | React + Semi；固定十项菜单；左上操作/右上筛选/右下分页；主展示字段进详情 | §3.2/§3.3；`menu.ts` 固定十项 | S-FE-01, S-FE-04 + verifier | applied |
| `harness-platform#RULE-ui-detail-001` | required | 本模块登录页无详情；账号管理后置页面必须复用 01 公共 `DetailSideSheet`（标题/操作/X 同行、Tabs 在其下），不另造 | §3.3 组件复用约束；§3.6 | S-FE-06 + verifier | applied |
| `harness-platform#RULE-front-001` | required | API 只经 `src/api/`，组件不裸用 axios/fetch；文案只用 i18n key；列表/详情遵循 UI 规范 | §3.5 数据获取层；§3.3.1 | S-FE-01, S-FE-06 + verifier | applied |
| `harness-platform#RULE-secret-001` | required | 密码/令牌不进入前端持久化或页面日志；仅随请求发送 | §3.5 数据获取层；RISK-FE-04 | E-FE-02 + verifier | applied |
| `harness-platform#RULE-time-001` | required | 时间展示统一 `YYYY-MM-DD HH:mm:ss`（账号列表 `last_login_at` 等） | §3.6 | S-FE-06 + verifier | applied |
| `harness-platform#RULE-test-001` | required | 登录/退出/守卫/401/CSRF 关键流程 E2E，不得 mock 浏览器、路由、Cookie 与真实 API | §2.4 关键真实边界列 | S-FE-01..S-FE-05 + verifier | applied |

## 附录：术语表

| 术语 | 定义 |
|---|---|
| AuthProvider | 持有当前 Console 账号与会话加载状态的 React Context |
| RequireAuth / RequireRole | 路由守卫：分别校验登录态与角色 |
| 双提交 CSRF | Cookie `muad_csrf` 与 Header `X-CSRF-Token` 同值校验 |
| adminOnly | 菜单项的仅 ADMIN 可见标记 |
| Envelope | 后端统一响应体 `{code,msg,data,trace_id,request_id,timestamp}` |

---

*文档结束*
