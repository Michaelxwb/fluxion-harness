---
schema: 1
requirement: fluxion-key-journeys-non-smoke
scenarios:
  - id: S-01
    title: BUILDER 看不到授权维护控件，普通 MCP 编辑保留用户范围
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL；Agent/MCP 嵌套详情壳
    preconditions: [ADMIN 可创建 Agent/MCP, BUILDER 会话]
    steps:
      - "ADMIN 创建 Agent 与 SELECTED MCP"
      - "BUILDER 打开两个详情并编辑保存"
    expected:
      - "BUILDER 无授权维护控件"
      - "普通编辑成功且 user_scope 保持 SELECTED"
    why_e2e: 跨 Agent/MCP 两个详情壳与嵌套 Tabs 的权限可见性只有真实浏览器渲染能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/agent-management/**, apps/console-platform/frontend/src/modules/mcp-management/**, apps/console-platform/backend/src/muad_console_platform/api/mcp_servers.py]
    smoke: false
    design_refs: [console-auth#S-15]
    rules: []
    checks: []

  - id: S-02
    title: 概览七天趋势连续补零，图表/状态切换不额外取数
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console 聚合 → PostgreSQL
    preconditions: [种子运行/任务数据]
    steps:
      - "打开概览"
      - "切换趋势与任务状态"
    expected:
      - "趋势 7 点连续补零"
      - "切换不触发新聚合请求"
    why_e2e: 真实聚合窗口补零与图表切换的取数行为只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/overview-dashboard/**, apps/console-platform/backend/src/muad_console_platform/application/overview_query_service.py]
    smoke: false
    design_refs: [overview-dashboard#S-02]
    rules: []
    checks: []

  - id: S-03
    title: 失效定时任务深链不白屏、不伪造数据
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console/Worker → PostgreSQL 404
    preconditions: [不存在的 scheduleId]
    steps:
      - "直接打开 /schedules?scheduleId=不存在"
    expected:
      - "页面不白屏且不展示伪造数据"
    why_e2e: 深链 404 的前端兜底与路由接线只有真实浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/task-schedule/**, apps/console-platform/backend/src/muad_console_platform/api/schedules.py]
    smoke: false
    design_refs: [overview-dashboard#E-04]
    rules: []
    checks: []

  - id: S-04
    title: 模型启停即时生效，删除后列表消失
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL
    preconditions: [已配置模型]
    steps:
      - "切换启用状态"
      - "删除模型并观察列表"
    expected:
      - "启停即时生效"
      - "删除后列表移除"
    why_e2e: 启停 CAS 与真实列表回流只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/model-management/**, apps/console-platform/backend/src/muad_console_platform/application/model_service.py]
    smoke: false
    design_refs: [model-management#S-03, model-management#S-06]
    rules: []
    checks: []

  - id: S-05
    title: stale revision 提交保留表单并提示刷新重试
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL（CAS 409）
    preconditions: [已打开编辑 Modal]
    steps:
      - "后台推进 revision"
      - "用旧 revision 保存"
    expected:
      - "Modal 保留并提示刷新重试"
      - "不覆盖他人修改"
    why_e2e: 真实并发下的 CAS 冲突与 Modal 状态只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/agent-management/**, apps/console-platform/backend/src/muad_console_platform/application/agent_service.py]
    smoke: false
    design_refs: [agent-management#E-07]
    rules: []
    checks: []

  - id: S-06
    title: 绑定已被删除的 Skill 时 Toast 且 Tab 状态不变
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL 软删除
    preconditions: [已选中 Skill, 服务端删除该 Skill]
    steps:
      - "选中后删除 Skill"
      - "再提交绑定"
    expected:
      - "Toast 提示"
      - "Tab 状态不变"
      - "不出现不存在的绑定"
    why_e2e: 真实软删除与前端残留选择的竞态只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/agent-management/**, apps/console-platform/backend/src/muad_console_platform/api/agents.py]
    smoke: false
    design_refs: [agent-management#E-10]
    rules: []
    checks: []

  - id: S-07
    title: Agent 最近活动只读区块展示时间/用户/操作/对象/结果
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL（审计投影）
    preconditions: [Agent 有审计活动]
    steps:
      - "打开 Agent 详情查看最近活动"
    expected:
      - "五列来自真实审计投影"
      - "无操作按钮"
    why_e2e: 真实审计投影到 Agent 详情的跨模块接线只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/agent-management/**, apps/console-platform/backend/src/muad_console_platform/api/audits.py]
    smoke: false
    design_refs: [agent-management#S-09]
    rules: []
    checks: []

  - id: S-08
    title: Agent 关系选择器排除已有项，添加/移除/解除后恢复
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL（Skill/MCP/用户关系）
    preconditions: [已绑定与未绑定的关系资源]
    steps:
      - "分别操作 Skill/MCP/用户授权选择器"
    expected:
      - "已有项不出现在候选"
      - "添加后移除"
      - "解除后恢复候选"
    why_e2e: 三套真实关系表与下拉候选的服务端投影只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/agent-management/**, apps/console-platform/backend/src/muad_console_platform/api/agents.py]
    smoke: false
    design_refs: [agent-management#S-13]
    rules: []
    checks: []

  - id: S-09
    title: 记忆管理创建/启禁用/历史分页/删除与清空重试幂等
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → Runtime → PostgreSQL
    preconditions: [ADMIN 会话, 目标用户]
    steps:
      - "创建/启禁用/查看历史/删除记忆"
      - "响应丢失后重试清空"
    expected:
      - "真实落库与历史分页"
      - "重试复用同一命令号不产生第二条"
    why_e2e: Console→Runtime 记忆写命令与前端命令号只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/user-identity/**, apps/agent-runtime/src/muad_agent_runtime/application/memory/**]
    smoke: false
    design_refs: [agent-management#E-04]
    rules: []
    checks: []

  - id: S-10
    title: 非 ADMIN 不能进入用户管理，也看不到记忆动作
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL 403
    preconditions: [BUILDER 会话]
    steps:
      - "BUILDER 直接访问用户管理并调用记忆写入口"
    expected:
      - "前端无入口"
      - "后端写入口 403"
    why_e2e: 前端守卫与后端拒绝同时成立只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/user-identity/**, apps/console-platform/backend/src/muad_console_platform/api/users.py]
    smoke: false
    design_refs: [agent-management#E-04]
    rules: []
    checks: []

  - id: S-11
    title: 导入内置归档逻辑生成的 Node greeting Skill
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL + 产物根
    preconditions: [内置归档 fixture]
    steps:
      - "导入 Node greeting Skill 并观察列表"
    expected:
      - "版本 READY"
      - "产物文件存在"
    why_e2e: 文件上传→产物落盘→版本状态只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/skill-management/**, apps/console-platform/backend/src/muad_console_platform/api/skills.py]
    smoke: false
    design_refs: [skill-management#S-05m]
    rules: []
    checks: []

  - id: S-12
    title: ALL 范围的指定用户 Tab 只提示不提供维护操作
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL
    preconditions: [ALL 范围 Skill]
    steps:
      - "切换 Skill 范围为 ALL 并打开指定用户 Tab"
    expected:
      - "提示无需指定"
      - "无添加用户控件"
    why_e2e: 范围切换后的服务端投影与 Tab 渲染只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/skill-management/**, apps/console-platform/backend/src/muad_console_platform/api/skills.py]
    smoke: false
    design_refs: [skill-management#S-07]
    rules: []
    checks: []

  - id: S-13
    title: SELECTED 范围远端搜索添加指定用户
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL
    preconditions: [SELECTED Skill, 可搜索用户]
    steps:
      - "远端搜索用户并添加授权"
    expected:
      - "用户出现在已选列表"
      - "关系真实落库"
    why_e2e: 远端搜索与关系保存的接线只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/skill-management/**, apps/console-platform/backend/src/muad_console_platform/api/skills.py]
    smoke: false
    design_refs: [skill-management#S-07b]
    rules: []
    checks: []

  - id: S-14
    title: MCP 注册弹窗排版与保存后自动发现
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → MCP 探针
    preconditions: [MCP 探针]
    steps:
      - "打开注册弹窗检查排版"
      - "保存后等待自动发现"
    expected:
      - "字段/说明可见"
      - "工具数正确"
    why_e2e: 弹窗排版与真实发现链路的接线只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/mcp-management/**, apps/console-platform/backend/src/muad_console_platform/api/mcp_servers.py]
    smoke: false
    design_refs: [mcp-management#S-07c]
    rules: []
    checks: []

  - id: S-15
    title: ADMIN 有系统设置入口，BUILDER 无入口且直接访问被拒
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL
    preconditions: [ADMIN/BUILDER 会话]
    steps:
      - "比较两个角色的菜单与直接访问 /settings"
    expected:
      - "ADMIN 有入口且可进"
      - "BUILDER 无入口且被守卫拦截"
    why_e2e: 菜单投影、路由守卫与跨租户可见性只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/layout/**, apps/console-platform/frontend/src/App.tsx, apps/console-platform/frontend/src/modules/settings/**]
    smoke: false
    design_refs: [settings#S-03]
    rules: []
    checks: []

  - id: S-16
    title: ?taskId= 深链直接打开任务详情，刷新后仍定位同一条
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console/Worker → PostgreSQL
    preconditions: [已存在的 Task]
    steps:
      - "打开深链并刷新"
    expected:
      - "详情打开且刷新后仍定位同一 Task"
    why_e2e: 深链路由与真实数据定位只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/task-schedule/**, apps/console-platform/backend/src/muad_console_platform/api/tasks.py]
    smoke: false
    design_refs: [task-schedule#S-FE-07]
    rules: []
    checks: []

  - id: S-17
    title: ?scheduleId= 深链预置筛选且筛选显式可见可清除
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console/Worker → PostgreSQL
    preconditions: [已存在的 Schedule]
    steps:
      - "打开 scheduleId 深链并清除筛选"
    expected:
      - "筛选预置且可见"
      - "清除后恢复全量"
    why_e2e: 查询参数→筛选状态→列表取数的接线只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/task-schedule/**, apps/console-platform/backend/src/muad_console_platform/api/tasks.py]
    smoke: false
    design_refs: [task-schedule#S-FE-08]
    rules: []
    checks: []

  - id: S-18
    title: 审计列表渲染真实行，Trace/类型/结果筛选与重置
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL 四表聚合
    preconditions: [审计种子行]
    steps:
      - "打开列表"
      - "按 Trace/类型/结果筛选"
      - "重置"
    expected:
      - "筛选收窄且重置还原真实行"
    why_e2e: 四表聚合投影与筛选接线只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/audit-observability/**, apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py]
    smoke: false
    design_refs: [audit-observability#S-06]
    rules: []
    checks: []

  - id: S-19
    title: 审计详情只读展示真实字段与关联，无操作按钮
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL
    preconditions: [审计种子行]
    steps:
      - "从 Trace/主展示字段打开详情"
    expected:
      - "字段/关联来自真实后端"
      - "只读无操作按钮"
    why_e2e: 只读详情与关联投影的接线只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/audit-observability/**, apps/console-platform/backend/src/muad_console_platform/api/audits.py]
    smoke: false
    design_refs: [audit-observability#S-07]
    rules: []
    checks: []

  - id: S-20
    title: Run 节点按需加载、重复展开缓存、刷新重取
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console/Worker → PostgreSQL 投影
    preconditions: [含事件节点的 Run]
    steps:
      - "展开节点"
      - "收起再展开"
      - "刷新详情"
    expected:
      - "按需请求一次"
      - "缓存命中"
      - "刷新后重取"
    why_e2e: 嵌套详情按需请求与缓存行为只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/run-observability/**, apps/console-platform/backend/src/muad_console_platform/api/runs.py]
    smoke: false
    design_refs: [run-observability#S-25]
    rules: []
    checks: []

  - id: S-21
    title: 直接打开并刷新 /chat/{entryId}，静态资源/Token 交换/SSE 经真实入口代理
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 入口代理 → Console/Gateway/Runtime → PostgreSQL
    preconditions: [已签发 Chat Token, 入口代理]
    steps:
      - "直接打开 /chat/{entryId} 并刷新"
    expected:
      - "静态资源/Token 交换/SSE 全部成功"
      - "CHAT 不经管理登录守卫"
    why_e2e: 入口代理分流、SPA 回退与认证面拆分只有真实浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/webchat/**, apps/im-gateway/**, e2e/support/webchat-fixtures.ts]
    smoke: false
    design_refs: [webchat#S-109]
    rules: []
    checks: []

  - id: S-22
    title: Token 签发原文一次可见、TTL/版本约束与撤销后旧会话失效
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → Console → PostgreSQL；真实 Cookie/TTL
    preconditions: [ADMIN 会话, 两浏览器上下文]
    steps:
      - "签发/重置 Token"
      - "观察原文与 TTL"
      - "用旧 Token/Cookie 继续"
    expected:
      - "原文只显示一次"
      - "旧 Token/Cookie 被拒且长流有界关闭"
    why_e2e: Token 版本、TTL 与多会话竞态只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/user-identity/**, apps/im-gateway/**, packages/contracts/src/muad_contracts/delivery.py]
    smoke: false
    design_refs: [webchat#S-114, webchat#E-110]
    rules: []
    checks: []

  - id: S-23
    title: 容量耗尽时排队阶段可见、刷新恢复、取消停止等待
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 入口代理 → Gateway/Runtime → PostgreSQL
    preconditions: [容量参数收紧, 模型探针]
    steps:
      - "耗尽容量后发送"
      - "刷新恢复"
      - "取消等待"
    expected:
      - "排队阶段可见"
      - "刷新恢复同一状态"
      - "取消停止等待"
    why_e2e: 真实准入/排队状态与 SSE 恢复只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/webchat/**, apps/agent-runtime/**, apps/im-gateway/**]
    smoke: false
    design_refs: [webchat#S-09, webchat#S-108]
    rules: []
    checks: []

  - id: S-24
    title: 断网/刷新恢复同一 Run、正文与附件且不重复提交
    priority: P0
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 入口代理 → Gateway/Runtime → PostgreSQL；真实 SSE 中断
    preconditions: [进行中的 Run]
    steps:
      - "断网/刷新后恢复"
      - "观察正文与附件"
    expected:
      - "恢复同一 Run 与内容"
      - "不重复提交/显示"
    why_e2e: 真实 fetch/SSE 中断、重连与游标补齐只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/webchat/**, apps/im-gateway/**]
    smoke: false
    design_refs: [webchat#S-103]
    rules: []
    checks: []

  - id: S-25
    title: 文字+附件 202 绑定真实 Run，增量/终态只结算一次并可下载
    priority: P1
    level: E2E
    exec: deferred
    env: browser
    components: [frontend, backend]
    boundary: 真实 Chrome → 入口代理 → Gateway/Runtime → PostgreSQL + 附件存储
    preconditions: [已上传附件, 模型探针]
    steps:
      - "发送文字+附件"
      - "观察 202/增量/终态"
      - "下载附件"
    expected:
      - "绑定真实 Run"
      - "终态只结算一次"
      - "下载字节正确"
    why_e2e: 真实 file chooser/upload/download 与 202 幂等只有真栈浏览器能证明。
    trigger_paths: [apps/console-platform/frontend/src/modules/webchat/**, apps/im-gateway/**, apps/agent-runtime/**]
    smoke: false
    design_refs: [webchat#S-102, webchat#E-14]
    rules: []
    checks: []
---

# 非 smoke 关键旅程

> 这是 smoke-15 之外的 key E2E 白名单，全部 `smoke: false`，按 `trigger_paths` 或 nightly 执行。
> 关系选择 3 条合为 S-08；记忆 4 条合为 S-09/S-10；Webchat 的入口/Token/容量/断流/附件按旅程合并。
> 测试映射见各条 `design_refs` 与标题；ID 为本文件内部编号，不与原域场景 ID 冲突。
