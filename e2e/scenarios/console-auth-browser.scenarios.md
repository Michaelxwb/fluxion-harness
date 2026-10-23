---
schema: 1
requirement: console-auth-browser-regression
scenarios:
  - id: S-01
    title: 登录成功签发会话，库内只存令牌哈希并更新 last_login_at
    priority: P0
    level: integration
    exec: auto
    env: db
    components: [backend]
    boundary: 真实 Console ASGI → 真实 PostgreSQL（console_session / console_account）
    preconditions: [真实空库 + alembic head, 种子管理员账号]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.backend.design.md#2.5.2 功能验收场景
    rules: []
    checks: []

  - id: S-03
    title: 登出撤销会话，已撤销令牌再访问 /me 得 401
    priority: P0
    level: integration
    exec: auto
    env: db
    components: [backend]
    boundary: 真实 Console ASGI → 真实 PostgreSQL（revoked_at）
    preconditions: [已登录会话]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.backend.design.md#2.5.2 功能验收场景
    rules: []
    checks: []

  - id: S-04
    title: 修改密码后旧密码失效、新密码可用
    priority: P1
    level: integration
    exec: auto
    env: db
    components: [backend]
    boundary: 真实 Console ASGI → 真实 PostgreSQL（argon2id 哈希轮换）
    preconditions: [种子 BUILDER 账号]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.backend.design.md#2.5.2 功能验收场景
    rules: []
    checks: []

  - id: S-05
    title: ADMIN 创建 BUILDER 账号，新账号可登录且出现在列表
    priority: P1
    level: integration
    exec: auto
    env: db
    components: [backend]
    boundary: 真实 Console ASGI → 真实 PostgreSQL（console_account + 审计）
    preconditions: [ADMIN 已登录]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.backend.design.md#2.5.2 功能验收场景
    rules: []
    checks: []

  - id: S-06
    title: ADMIN 账号列表为分页封套且无敏感字段
    priority: P1
    level: integration
    exec: auto
    env: db
    components: [backend]
    boundary: 真实 Console ASGI → 真实 PostgreSQL（分页 + 字段投影）
    preconditions: [ADMIN 已登录, 租户内至少一个账号]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.backend.design.md#2.5.2 功能验收场景
    rules: []
    checks: []

  - id: S-09
    title: 登录成功进入概览，Header 显示显示名与角色，刷新保持原路由且请求带三头
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → vite preview → 真实 Console → 真实 PostgreSQL；真实 Set-Cookie
    preconditions: [隔离库 + 种子 ADMIN 账号]
    steps:
      - 打开 /login，输入 ADMIN 用户名与密码并提交
      - 观察落地页、Header 显示名与角色
      - 刷新页面并观察请求头
    expected:
      - 落地概览页且菜单选中「概览」
      - Header 显示种子显示名与本地化角色
      - 登录响应下发 HttpOnly/SameSite 的会话 Cookie 与 JS 可读的 CSRF Cookie
      - 刷新后仍在原路由，/me 请求带 X-Locale、X-Request-Id、X-CSRF-Token
    why_e2e: 只有真实浏览器能证明 Set-Cookie 属性、Cookie 罐行为、整页刷新后的启动链路与 axios 三头注入同时成立；integration 只能证明服务端下发头，component 只能证明 mock 下的渲染。
    trigger_paths:
      - apps/console-platform/frontend/src/api/**
      - apps/console-platform/frontend/src/auth/**
      - apps/console-platform/frontend/src/layout/**
      - apps/console-platform/frontend/src/pages/LoginPage.tsx
      - apps/console-platform/backend/src/muad_console_platform/api/auth.py
      - apps/console-platform/backend/src/muad_console_platform/application/auth_service.py
    smoke: true
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: S-10
    title: 刷新时先出现启动 Spin，/me 成功后回到原路由且不闪回登录页
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend]
    boundary: 真实 Chrome 整页加载 → 真实 /auth/me → 文档级导航序列
    preconditions: [已登录并停留在 /agents]
    steps:
      - 在 /agents 刷新页面
      - 观察启动加载态与最终 URL
    expected:
      - 启动引导期间出现 Spin
      - /me 成功后回到 /agents
      - 整段导航序列不包含 /login
    why_e2e: 启动 Spin 是文档加载前的瞬时 DOM 状态，只有真实浏览器整页加载能观察；component 的 mock 无法证明真实 /me 时序与不闪回登录页。
    trigger_paths:
      - apps/console-platform/frontend/src/auth/**
      - apps/console-platform/frontend/src/App.tsx
    smoke: true
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: S-11
    title: 切换到 English 后登录失败的业务错误与页面文案均为 en-US，刷新后语言保持
    priority: P1
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock API（登录 401 + /me 401）
    preconditions: [localStorage 语言为 en-US]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: S-12
    title: ADMIN 与 BUILDER 的菜单差异：用户入口仅 ADMIN
    priority: P0
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock API（/auth/me 角色投影）
    preconditions: [mock ADMIN 或 BUILDER 账号]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: S-13
    title: Header 下拉退出返回 /login，再访问受保护路由仍跳登录页
    priority: P1
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock API（logout 200）
    preconditions: [mock ADMIN 已登录]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: S-14
    title: ADMIN 可进 /users；BUILDER 被重定向到 / 且不发起该页数据请求
    priority: P0
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock API（ADMIN/BUILDER 角色切换）
    preconditions: [mock /auth/me 可切换角色]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: S-15
    title: BUILDER 看不到授权维护控件，普通 MCP 编辑保留原用户范围
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL；嵌套 SideSheet/Tabs
    preconditions: [ADMIN 可创建 Agent 与 MCP Server, BUILDER 已登录]
    steps:
      - ADMIN 创建一个 Agent 与一个 SELECTED 范围的 MCP Server
      - BUILDER 打开 Agent 详情的「用户授权」与 MCP 详情的「指定用户」
      - BUILDER 编辑 MCP Server 并保存
    expected:
      - BUILDER 看不到授权维护控件
      - 普通 MCP 编辑成功且 user_scope 保持 SELECTED
    why_e2e: 该旅程跨 Agent/MCP 两个详情壳与嵌套 Tabs，只有真实浏览器渲染真实组件历史（曾因嵌套 Tabs 卸载对 null children 抛错）能证明；integration 无法覆盖控件可见性与嵌套卸载。
    trigger_paths:
      - apps/console-platform/frontend/src/modules/agent-management/**
      - apps/console-platform/frontend/src/modules/mcp-management/**
      - apps/console-platform/backend/src/muad_console_platform/api/mcp_servers.py
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: E-09
    title: 业务请求 401 时响应拦截跳转登录页，不循环且无未捕获异常
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend]
    boundary: 真实 Chrome window.location 整页导航 + axios 响应拦截器
    preconditions: [已登录并停留在业务页, 业务接口与 /me 判定未授权]
    steps:
      - 在业务页触发一次 401
      - 观察 URL、渲染结果与请求次数
    expected:
      - 整页跳转到登录页并渲染登录表单
      - 不出现重定向循环
      - 无未捕获异常，业务请求不反复重试
    why_e2e: 拦截器使用 window.location 做整页重定向，只有真实浏览器能证明「不循环、不白屏、不残留加载态」；component 的 mock 无法覆盖真实文档导航。
    trigger_paths:
      - apps/console-platform/frontend/src/api/client.ts
      - apps/console-platform/frontend/src/auth/**
    smoke: true
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: E-10
    title: 错误密码被拒：Toast 展示本地化消息、停留登录页、按钮 loading 复位
    priority: P1
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock API（登录 401）
    preconditions: [未登录]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: E-11
    title: BUILDER 直接输入 /users 被重定向到 /，且不发起该页数据请求
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend]
    boundary: 真实 Chrome Router 守卫 + 真实网络请求观测
    preconditions: [BUILDER 已登录]
    steps:
      - 打开 /
      - 地址栏直接输入 /users
    expected:
      - 被重定向到 /
      - 全程不发起 /api/v1/users 请求
    why_e2e: 「请求从未发出」只有真实浏览器的网络栈能证明；integration 只能证明 403，component 的 mock 会自己接收请求。
    trigger_paths:
      - apps/console-platform/frontend/src/App.tsx
      - apps/console-platform/frontend/src/auth/**
    smoke: true
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: E-12
    title: CSRF 缺失时退出登录被 403 拒绝：Toast 提示且不误显示成功态
    priority: P1
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock API（logout 403）
    preconditions: [mock ADMIN 已登录, CSRF Cookie 缺失]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []

  - id: E-13
    title: 启动时 /me 返回 401 或网络错误：进入登录页、无空白页、无循环
    priority: P0
    level: component
    exec: auto
    env: browser
    components: [frontend]
    boundary: 真实构建产物 + mock /me（401 与网络错误两条腿）
    preconditions: [存在失效会话或 /me 不可达]
    steps: []
    expected: []
    trigger_paths: []
    smoke: false
    design_refs:
      - 13-console-auth.frontend.design.md#2.4 验收条件
    rules: []
    checks: []
---

# console-auth 浏览器验收场景（试点）

> 本文件是 console-auth 浏览器回归的唯一场景源。E2E 只保留真实浏览器 + 真实 Console
> + 真实 PostgreSQL 才能证明的关键旅程；其余按 0.8 口径下沉到 component / integration。
> 场景 ID 沿用 `13-console-auth` 的既有编号，不重新编号；S-15 是本次试点新增的
> 「BUILDER 权限可见性」旅程（原 spec 中未编号的 review 用例）。
>
> 归档 manifest（`archived/2026-09-17/13-console-auth/.acceptance-manifest.json`）保持
> 历史快照，不回改；其 `-g S-01` 等命令不再对应当前 E2E 套件。重放归档验收时按本表
> 的层级映射执行：E2E = S-09/S-10/S-15/E-09/E-11；component = S-11/S-12/S-13/S-14/
> E-10/E-12/E-13；integration = S-01/S-03/S-04/S-05/S-06。

