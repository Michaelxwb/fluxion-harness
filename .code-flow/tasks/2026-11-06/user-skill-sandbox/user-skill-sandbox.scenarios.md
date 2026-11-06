---
schema: 1
requirement: user-skill-sandbox
scenarios:
  - id: S-01
    title: 用户创建草稿并完成文件读写删
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console 内部 API → Draft Store/PVC → PG revision
    preconditions:
      - 已登录租户用户
      - 该租户与用户存在
    steps: []
    expected:
      - 草稿创建成功，路径按 tenant/user/draft_key 隔离
      - 写入、读取、列目录、删除单文件和删除整个草稿均成功
      - 跨用户访问同一 draft_key 返回 not found/forbidden
      - revision 冲突返回 DRAFT_REVISION_CONFLICT
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-data#RULE-data-001
      - harness-api#RULE-api-001
    checks: []

  - id: S-02
    title: 草稿 Shell 执行 Python/Node 并回写合法变更
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: Runtime 工具 → 草稿副本 → bwrap → 变更校验 → Console 回写
    preconditions:
      - 用户已有草稿与入口脚本
      - 沙箱可用
    steps: []
    expected:
      - python scripts/run.py 和 node --check 可执行
      - 变更文件按 revision CAS 回写到草稿
      - 越界路径/symlink/超大文件被拒绝
      - 命令超时/输出超限错误码正确
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.5
    rules:
      - harness-skill#RULE-skill-001
      - harness-secret#RULE-secret-001
    checks: []

  - id: S-03
    title: 草稿 Shell 通过 session-manager 获取平台登录态
    priority: P0
    level: integration
    exec: deferred
    env: local
    components: [backend, sandbox, db]
    boundary: Runtime broker → Console 凭据解析 → Redis Session → Unix socket → state 文件
    preconditions:
      - 草稿声明 mssp 平台
      - 测试用户有平台凭据和 Agent 访问权
    steps: []
    expected:
      - session-manager get-state 返回 sessionStateFile 和 platforms list
      - state 文件内可读取 cookies 与 requestProfile
      - 执行结束 state 文件删除
      - 未授权平台返回 PLATFORM_SESSION_REQUIRED
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.4
    rules:
      - harness-project-platform#RULE-platform-001
      - harness-secret#RULE-secret-001
    checks: []

  - id: S-04
    title: 草稿校验通过并提交 PENDING artifact
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → SkillValidator → Artifact Store → PG
    preconditions:
      - 草稿包含 SKILL.md、muad.skill.json 和入口脚本
    steps: []
    expected:
      - 校验返回 pass/warn 明细
      - 打包生成 checksum、文件清单和 PENDING artifact
      - 记录 submitted_by、submitted_agent、submitted_channel、author
      - 重复提交不产生多个 PENDING
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-api#RULE-api-001
      - harness-api#RULE-api-002
      - harness-secret#RULE-secret-001
    checks: []

  - id: S-05
    title: 管理员批准 PENDING 并自动授权/绑定
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → 审批事务 → PG → 审计
    preconditions:
      - 存在 PENDING artifact
      - 管理员有审批权限
      - 提交者 AgentAccessGrant 仍 active
    steps: []
    expected:
      - artifact 变 APPROVED，current_artifact_id 更新
      - 提交者获得 SkillUserGrant
      - 上传时 Agent 获得 AgentSkillBinding
      - 审计记录 actor/skill/artifact/agent
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-rel#RULE-rel-001
      - harness-snapshot#RULE-snapshot-001
    checks: []

  - id: S-06
    title: 管理员驳回并填写原因，用户修改后重提
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → 状态机 → PG
    preconditions:
      - 存在 PENDING artifact
    steps: []
    expected:
      - 驳回要求 review_note，状态变 REJECTED
      - 用户可看到驳回原因
      - 修改后可重新提交新 PENDING
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-api#RULE-api-001
    checks: []

  - id: S-07
    title: 管理员下载 PENDING/APPROVED artifact 并写审计
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → Artifact Store → 下载响应 → 审计
    preconditions:
      - 存在 PENDING 和 APPROVED artifact
      - 管理员已登录
    steps: []
    expected:
      - 两种状态均可下载
      - 响应包含正确 zip/checksum
      - 写 SKILL_ARTIFACT_DOWNLOAD 审计
      - 普通用户不能下载他人 artifact
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules:
      - harness-secret#RULE-secret-001
      - harness-api#RULE-api-001
    checks: []

  - id: S-08
    title: 管理员一键解绑并软删已审批 Skill
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → 解绑/软删事务 → PG → 审计
    preconditions:
      - Skill 已审批且绑定多个 Agent
      - 管理员已登录
    steps: []
    expected:
      - 所有 AgentSkillBinding 解除
      - Skill 软删，执行入口拒绝
      - artifact/审批/审计保留
      - 二次确认与审计记录存在
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-rel#RULE-rel-001
      - harness-api#RULE-api-001
    checks: []

  - id: S-09
    title: 系统 Skill 上传后当前租户全局生效
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → Skill/Artifact → effective 查询
    preconditions:
      - 管理员上传 source=SYSTEM 的合法包
    steps: []
    expected:
      - 自动 APPROVED、user_scope=ALL、enabled=true
      - 不需要 AgentSkillBinding
      - 任意用户/Agent 的 effective 查询可见
      - 普通用户不能修改/删除
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-snapshot#RULE-snapshot-001
    checks: []

  - id: S-10
    title: 用户执行已批准 Skill 并返回结构化结果
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: Runtime/Worker → SandboxedSkillExecutor → bwrap → stdout/exit code
    preconditions:
      - Skill 已批准且用户有权限
      - 沙箱可用
    steps: []
    expected:
      - 脚本在只读包和独立 workspace 中执行
      - stdout/stderr/exit code 与现有 CLI 契约一致
      - 结构化 JSON 解析成功
      - 执行审计和指标产生
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.5
    rules:
      - harness-skill#RULE-skill-001
      - harness-snapshot#RULE-snapshot-001
      - harness-worker#RULE-worker-001
    checks: []

  - id: S-11
    title: workspace_shell 执行命令并安装依赖到 workspace
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: Runtime 工具 → WorkspaceShellRunner → bwrap → Pod 网络 → workspace venv/node_modules
    preconditions:
      - 用户有 Agent 访问权
      - workspace 可用且未超配额
    steps: []
    expected:
      - 普通 shell 命令成功
      - pip/npm 安装写入 workspace，不写系统目录
      - 无 session-manager
      - 输出/超时/配额限制生效
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.5
    rules:
      - harness-arch#RULE-arch-001
      - harness-secret#RULE-secret-001
      - harness-log#RULE-log-001
    checks: []

  - id: B-01
    title: 同一用户同一 Agent 跨会话共享 data，不同 Agent 隔离
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Workspace 元数据 → PVC 目录 → 两次执行
    preconditions:
      - 同一用户有两个 Agent
      - 两个 Agent 都执行过 workspace_shell
    steps: []
    expected:
      - 同 Agent 两次执行可读同一 data 文件
      - 不同 Agent 看不到对方 workspace
      - outputs 每次执行独立
      - 跨 tenant/user 不可见
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.5
    rules:
      - harness-data#RULE-data-001
      - harness-auth#RULE-auth-001
    checks: []

  - id: S-13
    title: 旧 Skill login-mssp-platform 不改代码运行
    priority: P0
    level: integration
    exec: manual
    env: external
    components: [backend, sandbox, platform]
    boundary: 真实 Runtime → 真实 Console/Redis → 真实 mssp 平台 → 旧 Skill 脚本
    preconditions:
      - 内网真实平台凭据可用
      - Skill 已导入/批准
      - 沙箱可用
    steps: []
    expected:
      - 旧脚本不修改即可调用 session-manager get-state
      - stdout platforms list 包含 mssp 和 expiresAt
      - 退出码 0，输出 ok/expiresAt
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.4
    rules:
      - harness-project-platform#RULE-platform-001
    checks: []

  - id: S-14
    title: 旧 Skill mss-report-skill 最小改造后运行
    priority: P0
    level: integration
    exec: manual
    env: external
    components: [backend, sandbox, platform]
    boundary: 真实 Runtime/Worker → 真实平台 → 改造后的旧 Skill
    preconditions:
      - 完成 SKILL_DATA_DIR/SKILL_OUTPUT_DIR/base_url/路径/调度/webhook 改造
      - 内网真实平台凭据可用
    steps: []
    expected:
      - 报告写入 SKILL_OUTPUT_DIR
      - cache/logs/companies 写入 SKILL_DATA_DIR
      - 不再写 Skill 包根目录
      - 平台地址来自 requestProfile
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules:
      - harness-project-platform#RULE-platform-001
      - harness-secret#RULE-secret-001
    checks: []

  - id: S-15
    title: 已批准包只读且每次执行独立工作目录
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: SandboxedSkillExecutor → 只读 /skill → 可写 /workspace
    preconditions:
      - 已批准 Skill 包含会写自身目录的脚本
    steps: []
    expected:
      - 写 /skill 失败
      - 两个并发执行互不影响
      - 共享 READY 缓存 checksum 不变
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.5
    rules:
      - harness-skill#RULE-skill-001
    checks: []

  - id: E-01
    title: stdout 洪泛被流式有界处理
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: SandboxedSkillExecutor → 子进程管道 → 有界读取
    preconditions:
      - 测试脚本持续输出大量数据
    steps: []
    expected:
      - 父进程内存有界，不随输出无限增长
      - 超过上限返回 SANDBOX_OUTPUT_LIMIT 或截断标记
      - 子进程被终止/回收
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-skill#RULE-skill-001
      - harness-log#RULE-log-001
    checks: []

  - id: E-02
    title: 超时/取消/后台进程/双重 fork 被完整回收
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: SandboxRunner → bwrap → 完整进程树
    preconditions:
      - 测试脚本创建后台进程、setsid、双重 fork
    steps: []
    expected:
      - 超时/取消后无孤儿进程
      - 进程组/后代被回收
      - 资源释放，后续执行可正常启动
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-worker#RULE-worker-001
      - harness-arch#RULE-arch-001
    checks: []

  - id: E-03
    title: 沙箱内不可见服务 Token、DB/Redis、SA Token 和敏感挂载
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: bwrap --clearenv + 最小 mount + 子进程环境
    preconditions:
      - Runtime/Worker 持有服务 Token 和外部连接串
    steps: []
    expected:
      - 脚本读不到 INTERNAL_SERVICE_TOKEN、DATABASE_URL、REDIS_URL
      - 读不到 /mnt、/proc、/var/run、Docker socket、SA Token
      - 只读包和 workspace 可见
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-secret#RULE-secret-001
      - harness-arch#RULE-arch-001
    checks: []

  - id: E-04
    title: /v1 受信接口服务身份门控
    priority: P0
    level: integration
    exec: auto
    env: local-api
    components: [backend, gateway]
    boundary: Gateway → Runtime /v1 → 服务身份校验
    preconditions:
      - Runtime 配置了 INTERNAL_SERVICE_TOKEN
    steps: []
    expected:
      - 无 header 拒绝
      - 错误 header 拒绝
      - 正确 header 成功
      - 浏览器传入的 header 不被透传
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4/§3.5
    rules:
      - harness-api#RULE-api-001
      - harness-auth#RULE-auth-001
      - harness-arch#RULE-arch-001
    checks: []

  - id: S-20
    title: SSE 建连和重连携带服务身份
    priority: P0
    level: integration
    exec: auto
    env: local-api
    components: [backend, gateway]
    boundary: Gateway SSE 客户端 → Runtime /v1/runs/{id}/events → 重连
    preconditions:
      - Run 事件流可用
    steps: []
    expected:
      - 首次建连带服务身份
      - 重连带服务身份
      - 无/错误身份被拒绝
      - 不逐事件校验、不增加网络往返
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4/§3.5
    rules:
      - harness-api#RULE-api-001
      - harness-auth#RULE-auth-001
    checks: []

  - id: E-05
    title: workspace_shell 默认没有 session-manager
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: workspace_shell → bwrap → PATH/socket 检查
    preconditions:
      - 用户有 Agent 访问权
    steps: []
    expected:
      - session-manager 不在 PATH
      - MUAD_SESSION_SOCKET 不存在
      - 调用 session-manager 返回 command not found/明确错误
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.4
    rules:
      - harness-project-platform#RULE-platform-001
      - harness-secret#RULE-secret-001
    checks: []

  - id: S-22
    title: 真实平台 Cookie 正负向对照
    priority: P0
    level: integration
    exec: manual
    env: external
    components: [backend, sandbox, platform]
    boundary: 真实 Console/Redis → 真实平台受保护接口
    preconditions:
      - 内网真实平台凭据可用
      - 受保护测试接口可用
    steps: []
    expected:
      - 有效 Cookie 200
      - 无 Cookie 403
      - 无效 Cookie 403
      - 响应正文不写入证据/日志
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.5
    rules:
      - harness-project-platform#RULE-platform-001
      - harness-secret#RULE-secret-001
    checks: []

  - id: E-06
    title: workspace 超过配额时拒绝写入/执行
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Workspace quota check → PVC
    preconditions:
      - workspace 已接近配额
    steps: []
    expected:
      - 超限返回 WORKSPACE_QUOTA_EXCEEDED
      - 不静默截断/不写系统目录
      - 审计/指标记录
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.5
    rules:
      - harness-data#RULE-data-001
    checks: []

  - id: S-24
    title: TTL 清理过期 workspace 且跳过活跃执行
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Worker workspace.maintenance → 元数据 → PVC
    preconditions:
      - 一个过期 workspace 和一个活跃 workspace
    steps: []
    expected:
      - 过期目录被删除
      - 活跃目录跳过
      - outputs/data 均按 30 天 TTL
      - 清理指标记录扫描/删除/跳过
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-worker#RULE-worker-001
      - harness-time#RULE-time-001
    checks: []

  - id: B-02
    title: 多副本清理只有一个副本认领成功
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: 多 Worker 并发 → PG advisory lock/认领
    preconditions:
      - 两个 Worker 使用同一测试库
      - 存在过期 workspace
    steps: []
    expected:
      - 只有一个副本删除
      - 另一个副本跳过
      - 不重复计数/不重复删除
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-worker#RULE-worker-001
      - harness-data#RULE-data-001
    checks: []

  - id: S-26
    title: macOS 原生 Dev 默认 fail closed，显式 dev-unsafe 才执行
    priority: P1
    level: unit
    exec: auto
    env: none
    components: [backend]
    boundary: SandboxRunner 工厂 → 平台检测 → dev-unsafe 开关
    preconditions:
      - 非 Linux 平台
    steps: []
    expected:
      - 未显式开启 dev-unsafe 时拒绝执行
      - 显式开启时执行并留下审计
      - 生产配置不能启用 dev-unsafe
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2
    rules:
      - harness-arch#RULE-arch-001
    checks: []

  - id: E-07
    title: bwrap 不可用时 fail closed
    priority: P0
    level: unit
    exec: auto
    env: none
    components: [backend]
    boundary: SandboxRunner → bwrap 探测
    preconditions:
      - 模拟 bwrap 不存在或版本不兼容
    steps: []
    expected:
      - 返回 SANDBOX_UNAVAILABLE
      - 不回退宿主执行
      - 日志/指标可见
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2/§3.5
    rules:
      - harness-arch#RULE-arch-001
    checks: []

  - id: E-08
    title: 不同用户提交同名 Skill 被拒绝
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → owner 校验 → PG
    preconditions:
      - 用户 A 已有同名 Skill/PENDING
      - 用户 B 提交同 key
    steps: []
    expected:
      - B 返回 SKILL_KEY_EXISTS
      - A 的 PENDING 不受影响
      - B 看不到 A 的草稿/PENDING
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-data#RULE-data-001
    checks: []

  - id: B-03
    title: 同一 owner 覆盖自己的 PENDING
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → 状态机 → PG
    preconditions:
      - 同一 owner 已有 PENDING
    steps: []
    expected:
      - 旧 PENDING 变 SUPERSEDED
      - 新 PENDING 成为唯一待审
      - 旧包审批返回冲突
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-data#RULE-data-001
    checks: []

  - id: E-09
    title: 已审批 Skill 被用户覆盖时锁定
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → 审批状态校验
    preconditions:
      - Skill 已 APPROVED
    steps: []
    expected:
      - 用户提交返回 SKILL_APPROVED_LOCKED
      - 现有 artifact 不变
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules:
      - harness-auth#RULE-auth-001
    checks: []

  - id: E-10
    title: 审批时 AgentAccessGrant 已撤销
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console 审批事务 → AgentAccessGrant 校验
    preconditions:
      - PENDING 提交后，提交者对该 Agent 的访问权被撤销
    steps: []
    expected:
      - 审批失败 AGENT_ACCESS_REVOKED
      - 不自动恢复授权
      - artifact 仍 PENDING，管理员可决定后续处理
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules:
      - harness-auth#RULE-auth-001
    checks: []

  - id: E-11
    title: 并发审批和用户提交只有一方成功
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: 两个并发事务 → artifact 行锁/状态复核
    preconditions:
      - 同一 PENDING 同时被管理员批准、用户提交新版
    steps: []
    expected:
      - 只有一方成功
      - 另一方得到明确冲突
      - 不出现 current_artifact_id 指向未批准版本
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.5
    rules:
      - harness-auth#RULE-auth-001
      - harness-data#RULE-data-001
    checks: []

  - id: B-04
    title: 系统 Skill 在所有 effective 查询入口一致
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console/Worker/Runtime effective 查询
    preconditions:
      - 存在 source=SYSTEM 的 Skill
    steps: []
    expected:
      - list/count/catalog/snapshot/execution 均包含
      - 不需要 binding
      - 普通 Skill 查询不被误放大
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3/§3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-snapshot#RULE-snapshot-001
    checks: []

  - id: E-12
    title: 非管理员执行审批/下载/解绑/系统上传被拒绝
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Console API → 管理员权限校验
    preconditions:
      - 普通用户已登录
    steps: []
    expected:
      - 审批、下载他人包、解绑、软删、系统 Skill 上传均返回 forbidden
      - 不泄露他人资源存在性
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules:
      - harness-auth#RULE-auth-001
      - harness-api#RULE-api-001
    checks: []

  - id: S-35
    title: 审批抽屉展示包信息并支持批准/驳回
    priority: P0
    level: component
    exec: auto
    env: none
    components: [frontend]
    boundary: React component + mocked Console API
    preconditions:
      - 提供 PENDING artifact fixture
    steps: []
    expected:
      - 展示 source、author、提交者、Agent、校验结果、文件清单、review_note
      - 批准/驳回按钮按权限和状态启用
      - 驳回必须填写原因
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - frontend design §3.3/§3.4/§3.6
    rules:
      - harness-ui#RULE-ui-001
      - harness-ui-detail#RULE-ui-detail-001
      - harness-frontend#RULE-front-001
    checks: []

  - id: S-36
    title: 解绑+软删二次确认展示影响范围
    priority: P0
    level: component
    exec: auto
    env: none
    components: [frontend]
    boundary: React component + mocked Console API
    preconditions:
      - 提供已绑定多个 Agent 的 Skill fixture
    steps: []
    expected:
      - 展示将解绑的 Agent 列表
      - 二次确认前不调用删除 API
      - 确认后调用删除并展示结果
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - frontend design §3.3/§3.4/§3.6
    rules:
      - harness-ui#RULE-ui-001
      - harness-ui-detail#RULE-ui-detail-001
      - harness-rel#RULE-rel-001
    checks: []

  - id: S-37
    title: /skills 按 source 和审批状态筛选
    priority: P0
    level: component
    exec: auto
    env: none
    components: [frontend]
    boundary: React component + mocked Console API
    preconditions:
      - 提供 SYSTEM/ADMIN/USER 和不同审批状态 fixture
    steps: []
    expected:
      - source 标签/筛选生效
      - PENDING 标记可见
      - 分页/关键字/筛选组合正确
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - frontend design §3.2/§3.5
    rules:
      - harness-ui#RULE-ui-001
      - harness-frontend#RULE-front-001
    checks: []

  - id: E-13
    title: 列表/抽屉空态、错误、无权限状态
    priority: P1
    level: component
    exec: auto
    env: none
    components: [frontend]
    boundary: React component + mocked Console API
    preconditions:
      - 空数据、接口错误、无管理员权限
    steps: []
    expected:
      - 空态有说明和下一步
      - 错误有重试且不丢筛选
      - 无权限隐藏/禁用审批操作
      - 状态不只靠颜色
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - frontend design §3.6/§3.8
    rules:
      - harness-ui#RULE-ui-001
      - harness-i18n#RULE-i18n-001
    checks: []

  - id: S-39
    title: 管理员在 Console 完成 Skill 审批
    priority: P0
    level: E2E
    exec: auto
    env: browser
    components: [frontend, backend, db]
    boundary: 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和管理员权限
    preconditions:
      - 已有 PENDING artifact
      - 管理员账号可登录 Console
    steps:
      - 打开 /skills，按审批状态筛选 PENDING
      - 打开目标 Skill 的审批抽屉
      - 检查包信息、校验结果、提交者、Agent
      - 填写审批意见并点击批准
      - 刷新列表并重新打开详情
    expected:
      - 状态变为 APPROVED
      - 当前 artifact 更新
      - 提交者 grant 和上传 Agent binding 出现
      - 审计记录可查询
    why_e2e: 审批是真实浏览器 + 真实登录/权限 + 真实 PG 事务 + 列表刷新的关键旅程；component 只能验 UI，integration 不能验浏览器交互、路由和真实鉴权组合。
    trigger_paths:
      - apps/console-platform/frontend/src/modules/skill-management/**
      - apps/console-platform/backend/src/muad_console_platform/api/skills.py
      - apps/console-platform/backend/src/muad_console_platform/application/skill_service.py
      - apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/skill_repository.py
    smoke: true
    design_refs:
      - backend design §3.4
      - frontend design §3.2/§3.5
    rules:
      - harness-e2e#RULE-e2e-001
      - harness-ui#RULE-ui-001
      - harness-ui-detail#RULE-ui-detail-001
      - harness-api#RULE-api-001
      - harness-auth#RULE-auth-001
    checks: []

  - id: S-40
    title: 管理员在 Console 解绑并软删 Skill
    priority: P1
    level: E2E
    exec: auto
    env: browser
    components: [frontend, backend, db]
    boundary: 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和关系删除
    preconditions:
      - 已审批 Skill 绑定多个 Agent
      - 管理员账号可登录 Console
    steps:
      - 打开 /skills，进入目标 Skill 详情
      - 点击删除，查看将解绑的 Agent 列表
      - 二次确认删除
      - 刷新列表并检查审计
    expected:
      - Skill 软删并从列表消失
      - Agent 绑定和用户 grant 解除
      - 审计记录包含解绑 Agent 列表
      - 普通用户无法执行该 Skill
    why_e2e: 破坏性操作 + 二次确认 + 真实关系删除 + 真实鉴权，低层测试无法覆盖完整浏览器旅程和权限组合。
    trigger_paths:
      - apps/console-platform/frontend/src/modules/skill-management/**
      - apps/console-platform/backend/src/muad_console_platform/api/skills.py
      - apps/console-platform/backend/src/muad_console_platform/application/skill_service.py
      - apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/skill_repository.py
    smoke: false
    design_refs:
      - backend design §3.4
      - frontend design §3.3/§3.5
    rules:
      - harness-e2e#RULE-e2e-001
      - harness-auth#RULE-auth-001
      - harness-rel#RULE-rel-001
      - harness-ui-detail#RULE-ui-detail-001
    checks: []

  - id: S-41
    title: 审批、执行、下载、清理审计与指标可查询
    priority: P1
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: 审计表/指标端点 → 查询
    preconditions:
      - 完成一次审批、一次执行、一次下载、一次清理
    steps: []
    expected:
      - 审计包含 actor/skill/artifact/agent/run/结果
      - 指标包含执行/失败/清理计数
      - 不包含 Cookie/Token/密钥原文
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-log#RULE-log-001
      - harness-secret#RULE-secret-001
    checks: []

  - id: E-14
    title: 错误码、HTTP 状态和 retryable 契约一致
    priority: P0
    level: contract
    exec: auto
    env: none
    components: [backend]
    boundary: API 错误码 schema / 消息配置
    preconditions:
      - 错误码表已实现
    steps: []
    expected:
      - 每个错误码有 msg、http_status、retryable
      - 用户侧不泄露堆栈/宿主机路径/Token
      - zh-CN/en-US 均有词条
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4/§3.5
    rules:
      - harness-api#RULE-api-001
      - harness-i18n#RULE-i18n-001
    checks: []

  - id: E-15
    title: Artifact checksum 不匹配时拒绝执行/下载
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Artifact Store → checksum 校验 → 执行入口
    preconditions:
      - 篡改 artifact 文件或 checksum 记录
    steps: []
    expected:
      - 返回 SKILL_ARTIFACT_INTEGRITY_ERROR
      - 不执行、不下载损坏包
      - 记录审计/告警
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-skill#RULE-skill-001
    checks: []

  - id: E-16
    title: 清理失败可见且下轮可重试
    priority: P1
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: Worker workspace.maintenance → 删除失败 → 状态/指标
    preconditions:
      - 模拟 PVC 删除失败
    steps: []
    expected:
      - 保留 DELETING/失败状态
      - 记日志和指标
      - 下一轮可重试，不静默吞掉
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
    rules:
      - harness-worker#RULE-worker-001
      - harness-log#RULE-log-001
    checks: []

  - id: S-45
    title: MCP/Memory/Model 现有能力非干扰回归
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, db]
    boundary: 现有 MCP catalog、Memory 写入/召回、模型选择测试
    preconditions:
      - 现有测试套件可用
    steps: []
    expected:
      - MCP catalog/tool registry 行为不变
      - Memory 写入/召回/软删/版本行为不变
      - Agent 模型选择不出现平台默认回退
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2
    rules:
      - harness-mcp#RULE-mcp-001
      - harness-memory#RULE-memory-001
      - harness-memory#RULE-memory-002
      - harness-memory#RULE-memory-003
      - harness-memory#RULE-memory-004
      - harness-memory#RULE-memory-005
      - harness-memory#RULE-memory-006
      - harness-model#RULE-model-001
    checks: []

  - id: S-46
    title: 核心域渠道中立性架构测试
    priority: P0
    level: integration
    exec: auto
    env: none
    components: [backend]
    boundary: 架构扫描 tests/architecture/test_channel_neutrality.py
    preconditions:
      - 现有架构测试可用
    steps: []
    expected:
      - 核心域零渠道专有字样
      - 核心域不向 ResolveDefinitionRequest.channel 传字面量
      - 渠道字样只出现在允许表面
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2
    rules:
      - harness-im#RULE-im-001
      - harness-im#RULE-im-002
    checks: []

  - id: S-47
    title: Skill 输出文件登记为可交付产物
    priority: P0
    level: integration
    exec: auto
    env: local
    components: [backend, sandbox, db]
    boundary: SandboxedSkillExecutor → SKILL_OUTPUT_DIR → ArtifactStore → 现有附件交付
    preconditions:
      - 已批准 Skill 在输出目录写报告文件
    steps: []
    expected:
      - 输出文件的相对路径、大小、checksum 被登记
      - 超过文件数/大小上限的产物被拒绝或截断并可见
      - symlink/越界路径不登记
      - 交付仍走现有 deliver_artifact/附件机制
    why_e2e: ""
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4/§3.5
    rules:
      - harness-skill#RULE-skill-001
      - harness-secret#RULE-secret-001
    checks: []

---
