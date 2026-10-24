---
schema: 1
requirement: fluxion-smoke-15
scenarios:
  - id: S-01
    title: 登录 → 概览 → 刷新保持；401 统一跳登录；BUILDER 越权重定向
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → preview → 真实 Console → 真实 PostgreSQL；真实 Cookie 与整页导航
    preconditions: [隔离库 + 种子 ADMIN/BUILDER]
    steps:
      - ADMIN 登录并刷新，观察落地页与请求头
      - 业务接口返回 401，观察是否整页跳登录且不循环
      - BUILDER 直接访问 /users
    expected:
      - 登录后进入概览且刷新保持原路由；会话 Cookie 属性与三头正确
      - 401 落登录页且无循环、无未捕获异常
      - BUILDER 被重定向且全程不发起 /users 数据请求
    why_e2e: 真实 Set-Cookie、整页 401 导航与“请求从未发出”只有真实浏览器能证明；integration 只能证明服务端 403，component 只能证明 mock 渲染。
    trigger_paths: []
    smoke: true
    design_refs: [console-auth#S-09, console-auth#E-09, console-auth#E-11]
    rules: []
    checks: []

  - id: S-02
    title: 概览真实聚合与卡片跳转筛选
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console 聚合查询 → 真实 PostgreSQL
    preconditions: [种子运行/任务/审计数据]
    steps:
      - 打开概览并观察 KPI、资源与图表
      - 点击资源卡、Agent 排行、失败任务与模型指标
    expected:
      - 六项 KPI/资源/四块图表来自一次真实聚合请求
      - 跳转携带正确筛选并落在可用的列表/审计页
    why_e2e: 聚合 SQL → DTO → 图表/路由 query 的跨页接线只有真栈浏览器能证明；组件 mock 无法覆盖真实聚合与路由联动。
    trigger_paths: []
    smoke: true
    design_refs: [overview-dashboard#S-03, overview-dashboard#S-04]
    rules: []
    checks: []

  - id: S-03
    title: 模型池新增：物理 Key → 逻辑模型 → 成员绑定
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL；模型探针真实 HTTP
    preconditions: [真实模型探针]
    steps:
      - 新建物理 Key 并填写端点/密钥
      - 创建逻辑模型并观察成员自动绑定
      - 对模型执行批量测试
    expected:
      - 模型与成员真实落库，明文密钥不回显
      - 批量测试返回探针真实结果
    why_e2e: 物理凭据→模型→成员的关系保存、密钥不回显与探针真实调用只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [model-management#S-01, model-management#S-10, model-management#S-11]
    rules: []
    checks: []

  - id: S-04
    title: Agent 编辑 Prompt 与 Skill/MCP 绑定
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL
    preconditions: [种子模型, 已导入 Skill 与 MCP]
    steps:
      - 编辑 Agent 系统 Prompt 并保存
      - 在详情中添加/解除 Skill 与 MCP 绑定
    expected:
      - revision 递增且详情显示新 Prompt
      - 绑定即时生效，解除/再绑正常
    why_e2e: revision CAS、关系保存与详情壳接线需要真栈浏览器；component 只能证明表单渲染。
    trigger_paths: []
    smoke: true
    design_refs: [agent-management#S-07, agent-management#S-08, agent-management#S-11]
    rules: []
    checks: []

  - id: S-05
    title: Skill 导入与新版本
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL + 产物根
    preconditions: [合法 Skill ZIP]
    steps:
      - 导入 Skill 并观察列表
      - 在详情导入新版本并查看版本记录/SKILL.md
    expected:
      - 导入后 DB/NFS 落库且列表默认 SELECTED
      - 新版本记录、当前版本与正文正确
    why_e2e: 文件上传→产物落盘→版本记录→正文渲染的整链只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [skill-management#S-05, skill-management#S-06]
    rules: []
    checks: []

  - id: S-06
    title: MCP 注册 → 连接测试 → 工具发现
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL → 真实 MCP 探针
    preconditions: [MCP 探针]
    steps:
      - 注册 MCP Server 并保存
      - 执行连接测试与刷新工具目录
      - 打开工具明细
    expected:
      - connection_status 与工具数来自真实发现
      - 工具明细展示名称/操作类型，无 Tool 级启停
    why_e2e: 真实 MCP 协议发现与嵌套详情壳只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [mcp-management#S-07, mcp-management#S-05, mcp-management#S-06]
    rules: []
    checks: []

  - id: S-07
    title: 项目平台创建、resolver 表单与用户凭据
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL；适配器/平台探针
    preconditions: [平台探针]
    steps:
      - 创建平台并切换 resolver 类型
      - 保存用户凭据并观察详情
    expected:
      - 表单只渲染所选 resolver 字段
      - 凭据真实落库、保存后只显示配置状态不回显明文
    why_e2e: resolver 动态表单、凭据保存与详情投影的接线只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [project-platform#S-01, project-platform#S-05, project-platform#S-02, project-platform#S-06]
    rules: []
    checks: []

  - id: S-08
    title: 用户创建、详情四计数与绑定码
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL
    preconditions: [种子 Agent/平台/身份/记忆]
    steps:
      - 新增用户并打开详情
      - 查看四个 Tab 计数并生成绑定码
    expected:
      - 列表与详情字段来自后端
      - 四计数与来源一致；绑定码展示 code 与过期时间
    why_e2e: 四张来源表计数聚合与绑定码签发只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [user-identity#S-06, user-identity#S-01, user-identity#S-03, user-identity#S-08, user-identity#S-07]
    rules: []
    checks: []

  - id: S-09
    title: 系统设置保存、版本号与生效提示
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL（版本行）
    preconditions: [ADMIN 会话]
    steps:
      - 修改设置并保存
      - 观察版本号与提示
    expected:
      - 版本号递增、提示新操作立即生效
      - 库内确有新版本
    why_e2e: append-only 版本行与设置页接线需要真栈浏览器；component 只能证明表单。
    trigger_paths: []
    smoke: true
    design_refs: [settings#S-01]
    rules: []
    checks: []

  - id: S-10
    title: 后台任务列表取消
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console/Worker → 真实 PostgreSQL；真实轮询
    preconditions: [运行中的任务]
    steps:
      - 在列表点击取消并确认
      - 观察终态与列表同步
    expected:
      - 确认前不发请求；确认后终态 CANCELLED、按钮消失、列表同步
    why_e2e: popconfirm 竞态 + 轮询 + 列表同步只有真栈浏览器能证明；历史 flake 高发。
    trigger_paths: []
    smoke: true
    design_refs: [task-schedule#S-FE-02]
    rules: []
    checks: []

  - id: S-11
    title: 定时任务暂停/恢复/删除与历史
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console/Worker → 真实 PostgreSQL
    preconditions: [ACTIVE 定时任务, 历史 Task]
    steps:
      - 暂停、恢复并删除定时任务
      - 打开历史查看来源 Task
    expected:
      - 状态与按钮随生命周期正确变化
      - 删除后历史 Task 仍可查并可跳转
    why_e2e: 调度状态机与历史可达性需要真栈浏览器；component 无法覆盖真实 Worker 状态。
    trigger_paths: []
    smoke: true
    design_refs: [task-schedule#B-138, task-schedule#S-FE-01]
    rules: []
    checks: []

  - id: S-12
    title: 审计导出建任务、下载与幂等重放
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console → 真实 PostgreSQL + 导出产物
    preconditions: [审计种子行]
    steps:
      - 点击主导出并等待完成
      - 以同一幂等键重复提交
    expected:
      - 完成后按文件名下载真实产物
      - 同键重放复用同一 export_id，不重复建任务
    why_e2e: 浏览器下载、真实导出执行器与幂等键只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [audit-observability#S-08]
    rules: []
    checks: []

  - id: S-13
    title: Run 详情关联 Task 与工具结果跳转
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 真实 Console/Worker → 真实 PostgreSQL 投影
    preconditions: [含关联 Task/工具事件的 Run]
    steps:
      - 从审计行打开 Run 详情
      - 打开关联 Task 与工具结果节点
    expected:
      - 关联面板互跳且不堆叠
      - 工具结果/模型汇总来自真实投影
    why_e2e: 嵌套 SideSheet、关联替换与按需事件加载只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [run-observability#S-21, run-observability#S-26]
    rules: []
    checks: []

  - id: S-14
    title: Webchat Token 登录 → 聊天 → 流式回复 → 刷新恢复
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 入口代理 → Console/Gateway/Runtime → 真实 PostgreSQL；真实 SSE
    preconditions: [ADMIN 签发的 Chat Token, 模型探针]
    steps:
      - 打开 /chat 输入 Token 进入
      - 发送消息并观察流式回复
      - 刷新并恢复同一会话
    expected:
      - Token 交换与授权 Agent 正确
      - 流式回复落库且刷新后历史恢复
    why_e2e: 真实 SSE、入口代理与共享 Cookie 罐只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [webchat#S-101]
    rules: []
    checks: []

  - id: S-15
    title: Webchat 同浏览器 A→B 身份切换与撤销隔离
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 入口代理 → 四服务 → 真实 PostgreSQL；真实 SSE
    preconditions: [两个用户与 Token]
    steps:
      - A 登录并产生消息/草稿
      - 退出 A 输入 B Token
      - 撤销 A 的会话并观察长流
    expected:
      - 旧流/消息/草稿清空，B 认证后才拉 B 历史
      - 撤销时长流有界关闭、私有状态清空，不跳 Console 登录
    why_e2e: 共享 Cookie 罐 + 真实 SSE + React 状态清空只有真栈浏览器能证明。
    trigger_paths: []
    smoke: true
    design_refs: [webchat#S-112, webchat#E-20, webchat#E-101]
    rules: []
    checks: []
---

# Fluxion 15 条 smoke E2E

> 本文件是跨域的关键旅程白名单（全部 `smoke: true`），不是需求级场景全量。
> 其余关键 E2E（各域 `*-e2e` / 关键配置）保留为 non-smoke，按 `trigger_paths` 或 nightly 执行。
> 现有测试映射：S-01→console-auth S-09/E-09/E-11；S-02→overview S-03/S-04；S-03→model S-01/S-10/S-11；
> S-04→agent S-07/S-08/S-11；S-05→skill S-05/S-06；S-06→mcp S-07/S-05/S-06；S-07→platform S-01/S-05/S-02/S-06；
> S-08→user S-06/S-01/S-03/S-08/S-07；S-09→settings S-01；S-10→task S-FE-02；S-11→task B-138/S-FE-01；
> S-12→audit S-08；S-13→run S-21/S-26；S-14→webchat S-101；S-15→webchat S-112/E-20/E-101。
