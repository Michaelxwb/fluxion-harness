# Tasks: 用户自建 Skill 与统一沙箱执行

- **Source**: `.code-flow/tasks/2026-11-06/user-skill-sandbox/`（backend/frontend design + scenarios 合并）
- **Created**: 2026-11-06
- **Updated**: 2026-11-07

## Proposal

把用户自建 Skill 的能力拆成可独立演示的 1–2 天任务：先打通 `/v1` 门控、bwrap 执行器、session-manager、workspace 与清理，再落草稿/审批/下载/前端，最后做真实老 Skill 和 E2E 验收。所有 Skill 与 `workspace_shell` 共用同一套沙箱、配额、审计和错误码；workspace 独立 PVC，生产依赖由管理员预装系统镜像。

## Execution Contract

- 每个 TASK 是 1–2 天可独立演示能力；编码前先写局部行为测试并记录 RED，再实现和记录 GREEN。
- 单 worktree 一次只激活一个 TASK；无依赖且文件不相交的任务可按 `cf-task-start` 批次并行。
- E2E 只编写/登记，编码期不执行 RED/GREEN；统一由 verify-e2e 终验。
- 每个 required Rule 只有一个最终责任 TASK；其他任务通过依赖/契约引用，不重复认领。
- pytest/验收前先停 dev 服务，使用隔离验收库与独立 Redis 槽；不删共享 dev 数据。

## Task Index

| 任务 | 阶段 | 能力 | 优先级 | 预估 | 依赖 | 场景数 |
|---|---|---|---|---|---|---|
| TASK-001 | P1 | 未鉴权 `/v1` 调用被拒绝，Gateway 的 JSON/SSE/重连带内部身份通过 | P0 | 1.5d | - | 2 |
| TASK-002 | P1 | bwrap 沙箱可启动/超时/取消，完整后代被回收；bwrap 不可用时 fail closed | P0 | 2d | - | 3 |
| TASK-003 | P1 | 已批准 Skill 在只读包/独立 workspace 中执行，stdout 流式有界，输出文件登记为产物 | P0 | 2d | TASK-002 | 5 |
| TASK-004 | P1 | Skill 执行/草稿 Shell 可通过 session-manager 获取登录态；旧 `login-mssp-platform` 不改代码可运行 | P0 | 1.5d | TASK-003 | 3 |
| TASK-005 | P1 | user + Agent workspace 隔离可写，`SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR` 注入；workspace_shell 可用且无 session-manager | P0 | 2d | TASK-007 | 3 |
| TASK-006 | P1 | workspace 超限被拒绝；30 天未访问目录被清理，活跃执行跳过，多副本不重复 | P0 | 1.5d | TASK-005, TASK-007 | 4 |
| TASK-007 | P1 | Skill/Artifact/Draft/Workspace schema、迁移、`source` 契约和 execution snapshot 可用 | P0 | 2d | - | 4 |
| TASK-008 | P2 | 用户可创建/读写草稿、校验打包、提交 PENDING、查询状态和驳回原因 | P0 | 2d | TASK-007 | 2 |
| TASK-009 | P2 | 草稿 Shell 可执行 Python/Node/session-manager，合法变更按 revision CAS 回写 | P0 | 1.5d | TASK-002, TASK-003, TASK-008 | 2 |
| TASK-010 | P2 | 管理员可批准/驳回；owner 只能覆盖自己的 PENDING；审批自动授权/绑定；已审批锁定 | P0 | 2d | TASK-007, TASK-008 | 7 |
| TASK-011 | P2 | 管理员可下载待审/已审批包并写审计；checksum 不匹配拒绝；一键解绑+软删 | P0 | 1.5d | TASK-003, TASK-010 | 3 |
| TASK-012 | P2 | 管理员上传 `source=SYSTEM` 自动通过、当前租户全局生效、无需绑定 | P0 | 1d | TASK-007, TASK-010 | 2 |
| TASK-013 | Cross | 新增/扩展接口统一封套与错误码；审计/指标可查；日志不泄密 | P0 | 2d | TASK-007 | 2 |
| TASK-014 | P2 | 管理员在 Console 完成 source/审批筛选、审批/驳回、下载、解绑软删、系统 Skill 上传 | P0 | 2d | TASK-010, TASK-011, TASK-012, TASK-013 | 7 |
| TASK-015 | Cross | 管理员通过镜像构建预装 Python/Node 依赖；Skill 直接 import；workspace 临时安装不作为生产依赖 | P1 | 1d | - | 2 |
| TASK-016 | Cross | 验收场景有可收集测试骨架；管理员审批/解绑 E2E 可按触发路径运行；manifest 可锁定 | P0 | 2d | TASK-001, TASK-003, TASK-005, TASK-010, TASK-014 | 2 |
| TASK-017 | Cross | 现有 MCP catalog、Memory 写入/召回、模型选择、渠道中立性回归全绿 | P0 | 1.5d | TASK-003, TASK-005, TASK-007 | 2 |
| TASK-018 | P1 | 本地/内网 K8s 部署包含独立 workspace PVC，runtime/worker 挂载，canary 通过 | P0 | 1.5d | TASK-005, TASK-006 | 2 |
| TASK-019 | P2 | `login-mssp-platform` 不改代码运行；`mss-report-skill` 最小改造后运行 | P0 | 1.5d | TASK-003, TASK-004, TASK-005 | 2 |

## Plan Compliance Matrix

| Rule | Owner TASK | Verifier |
|---|---|---|
| `harness-skill#RULE-skill-001` | TASK-003 | uv run pytest -q tests/agent_core/test_sandboxed_skill_executor.py tests/acceptance/user_skill_sandbox/test_skill_execution.py |
| `harness-secret#RULE-secret-001` | TASK-003 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_sandbox_boundary.py |
| `harness-project-platform#RULE-platform-001` | TASK-004 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_session_manager.py tests/platform_sdk/test_skill_bridge.py |
| `harness-worker#RULE-worker-001` | TASK-006 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_workspace_cleanup.py tests/agent_worker/test_worker_maintenance.py |
| `harness-time#RULE-time-001` | TASK-006 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_workspace_cleanup.py -k ttl |
| `harness-data#RULE-data-001` | TASK-007 | uv run pytest -q tests/migrations/test_skill_sandbox_schema.py tests/console_skill/test_skill_repository.py |
| `harness-snapshot#RULE-snapshot-001` | TASK-007 | uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py -k snapshot |
| `harness-api#RULE-api-002` | TASK-008 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_draft_idempotency.py |
| `harness-auth#RULE-auth-001` | TASK-010 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_skill_authorization.py tests/console_skill/test_skill_rbac.py |
| `harness-rel#RULE-rel-001` | TASK-010 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_skill_authorization.py -k relation |
| `harness-api#RULE-api-001` | TASK-013 | uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py |
| `harness-i18n#RULE-i18n-001` | TASK-013 | uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py -k i18n |
| `harness-log#RULE-log-001` | TASK-013 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_audit_logging.py |
| `harness-frontend#RULE-front-001` | TASK-014 | uv run pytest -q tests/frontend/test_skill_management_ui.py |
| `harness-ui#RULE-ui-001` | TASK-014 | uv run pytest -q tests/frontend/test_skill_management_ui.py -k layout |
| `harness-ui-detail#RULE-ui-detail-001` | TASK-014 | uv run pytest -q tests/frontend/test_skill_management_ui.py -k detail |
| `harness-e2e#RULE-e2e-001` | TASK-016 | npm run skill-self-service-approve && npm run skill-self-service-delete |
| `harness-test#RULE-test-001` | TASK-016 | uv run pytest -q tests/architecture tests/acceptance/user_skill_sandbox |
| `harness-im#RULE-im-001` | TASK-017 | uv run pytest -q tests/architecture/test_channel_neutrality.py |
| `harness-im#RULE-im-002` | TASK-017 | uv run pytest -q tests/architecture/test_channel_neutrality.py |
| `harness-mcp#RULE-mcp-001` | TASK-017 | uv run pytest -q tests/frontend/test_mcp_module_contract.py |
| `harness-memory#RULE-memory-001` | TASK-017 | uv run pytest -q tests/test_memory_redesign_spec_contract.py |
| `harness-memory#RULE-memory-002` | TASK-017 | uv run pytest -q tests/test_memory_redesign_spec_contract.py |
| `harness-memory#RULE-memory-003` | TASK-017 | uv run pytest -q tests/test_memory_redesign_spec_contract.py |
| `harness-memory#RULE-memory-004` | TASK-017 | uv run pytest -q tests/test_memory_redesign_spec_contract.py |
| `harness-memory#RULE-memory-005` | TASK-017 | uv run pytest -q tests/test_memory_redesign_spec_contract.py |
| `harness-memory#RULE-memory-006` | TASK-017 | uv run pytest -q tests/test_memory_redesign_spec_contract.py |
| `harness-model#RULE-model-001` | TASK-017 | uv run pytest -q tests/contracts/test_capacity_webchat_model_contracts.py tests/frontend/test_model_form_contract.py |
| `harness-arch#RULE-arch-001` | TASK-018 | uv run pytest -q tests/architecture/test_k8s_deploy_manifests.py tests/architecture/test_capacity_webchat_runtime_worker_deploy.py |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console 内部 API → Draft Store/PVC → PG revision | TASK-008 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_01.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-02 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Runtime 工具 → 草稿副本 → bwrap → 变更校验 → Console 回写 | TASK-009 | verified | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_02.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-03 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Runtime broker → Console 凭据解析 → Redis Session → Unix socket → state 文件 | TASK-004 | e2e_deferred | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_03.py"] | . | 600 | P0 | deferred | local | - | - | - | no |
| S-04 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → SkillValidator → Artifact Store → PG | TASK-008 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_04.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-05 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → 审批事务 → PG → 审计 | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_05.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-06 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → 状态机 → PG | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_06.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-07 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → Artifact Store → 下载响应 → 审计 | TASK-011 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_07.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-08 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → 解绑/软删事务 → PG → 审计 | TASK-011 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_08.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-09 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → Skill/Artifact → effective 查询 | TASK-012 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_09.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-10 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Runtime/Worker → SandboxedSkillExecutor → bwrap → stdout/exit code | TASK-003 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_10.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-11 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Runtime 工具 → WorkspaceShellRunner → bwrap → Pod 网络 → workspace venv/node_modules | TASK-005 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_11.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| B-01 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Workspace 元数据 → PVC 目录 → 两次执行 | TASK-005 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_01.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-13 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 真实 Runtime → 真实 Console/Redis → 真实 mssp 平台 → 旧 Skill 脚本 | TASK-019 | manual_pending | - | . | 600 | P0 | manual | external | - | - | - | no |
| S-14 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 真实 Runtime/Worker → 真实平台 → 改造后的旧 Skill | TASK-019 | manual_pending | - | . | 600 | P0 | manual | external | - | - | - | no |
| S-15 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | SandboxedSkillExecutor → 只读 /skill → 可写 /workspace | TASK-003 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_15.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-01 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | SandboxedSkillExecutor → 子进程管道 → 有界读取 | TASK-003 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_01.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-02 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | SandboxRunner → bwrap → 完整进程树 | TASK-002 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_02.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-03 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | bwrap --clearenv + 最小 mount + 子进程环境 | TASK-003 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_03.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-04 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Gateway → Runtime /v1 → 服务身份校验 | TASK-001 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_04.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| S-20 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Gateway SSE 客户端 → Runtime /v1/runs/{id}/events → 重连 | TASK-001 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_20.py"] | . | 600 | P0 | auto | local-api | - | - | - | no |
| E-05 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | workspace_shell → bwrap → PATH/socket 检查 | TASK-005 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_05.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-22 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 真实 Console/Redis → 真实平台受保护接口 | TASK-004 | manual_pending | - | . | 600 | P0 | manual | external | - | - | - | no |
| E-06 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Workspace quota check → PVC | TASK-006 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_06.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-24 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Worker workspace.maintenance → 元数据 → PVC | TASK-006 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_24.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| B-02 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 多 Worker 并发 → PG advisory lock/认领 | TASK-006 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_02.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-26 | user-skill-sandbox.backend.design.md#2.5 验收条件 | unit | SandboxRunner 工厂 → 平台检测 → dev-unsafe 开关 | TASK-002 | stale | ["uv","run","pytest","-q","tests/agent_core/test_sandbox_runner.py"] | . | 600 | P1 | auto | none | - | - | - | no |
| E-07 | user-skill-sandbox.backend.design.md#2.5 验收条件 | unit | SandboxRunner → bwrap 探测 | TASK-002 | stale | ["uv","run","pytest","-q","tests/agent_core/test_sandbox_runner.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| E-08 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → owner 校验 → PG | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_08.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| B-03 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → 状态机 → PG | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_03.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-09 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console API → 审批状态校验 | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_09.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-10 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console 审批事务 → AgentAccessGrant 校验 | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_10.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-11 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 两个并发事务 → artifact 行锁/状态复核 | TASK-010 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_11.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| B-04 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Console/Worker/Runtime effective 查询 | TASK-012 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_04.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-12 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | integration | Console API → 管理员权限校验 | TASK-014 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_12.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-35 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | component | React component + mocked Console API | TASK-014 | planned | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-36 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | component | React component + mocked Console API | TASK-014 | planned | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-37 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | component | React component + mocked Console API | TASK-014 | planned | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| E-13 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | component | React component + mocked Console API | TASK-014 | planned | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | . | 600 | P1 | auto | none | - | - | - | no |
| S-39 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和管理员权限 | TASK-014 | planned | ["npm","run","skill-self-service-approve"] | . | 1800 | P0 | auto | browser | - | 审批是真实浏览器 + 真实登录/权限 + 真实 PG 事务 + 列表刷新的关键旅程；component 只能验 UI，integration 不能验浏览器交互、路由和真实鉴权组合。 | ["apps/console-platform/frontend/src/modules/skill-management/**", "apps/console-platform/backend/src/muad_console_platform/api/skills.py", "apps/console-platform/backend/src/muad_console_platform/application/skill_service.py", "apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/skill_repository.py"] | yes |
| S-40 | user-skill-sandbox.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和关系删除 | TASK-014 | planned | ["npm","run","skill-self-service-delete"] | . | 1800 | P1 | auto | browser | - | 破坏性操作 + 二次确认 + 真实关系删除 + 真实鉴权，低层测试无法覆盖完整浏览器旅程和权限组合。 | ["apps/console-platform/frontend/src/modules/skill-management/**", "apps/console-platform/backend/src/muad_console_platform/api/skills.py", "apps/console-platform/backend/src/muad_console_platform/application/skill_service.py", "apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/skill_repository.py"] | no |
| S-41 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 审计表/指标端点 → 查询 | TASK-013 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_41.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| E-14 | user-skill-sandbox.backend.design.md#2.5 验收条件 | contract | API 错误码 schema / 消息配置 | TASK-013 | stale | ["uv","run","pytest","-q","tests/contracts/test_skill_sandbox_contracts.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| E-15 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Artifact Store → checksum 校验 → 执行入口 | TASK-011 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_15.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| E-16 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Worker workspace.maintenance → 删除失败 → 状态/指标 | TASK-006 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_16.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| S-45 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 现有 MCP catalog、Memory 写入/召回、模型选择测试 | TASK-017 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_45.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-46 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 架构扫描 tests/architecture/test_channel_neutrality.py | TASK-017 | planned | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_46.py"] | . | 600 | P0 | auto | none | - | - | - | no |
| S-47 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | SandboxedSkillExecutor → SKILL_OUTPUT_DIR → ArtifactStore → 现有附件交付 | TASK-003 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_47.py"] | . | 600 | P0 | auto | local | - | - | - | no |
| S-48 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | 镜像依赖清单 → runtime/worker 镜像 → bwrap 系统 site-packages → Skill import/require | TASK-015 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_48.py"] | . | 600 | P1 | auto | local | - | - | - | no |
| E-17 | user-skill-sandbox.backend.design.md#2.5 验收条件 | integration | Pod A 临时安装 → Pod B/重建后执行同一 Skill | TASK-015 | stale | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_17.py"] | . | 600 | P1 | auto | local | - | - | - | no |

---

## TASK-001: `/v1` 服务身份门控与 Gateway/SSE 内部身份

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: user-skill-sandbox.backend.design.md#3.4 接口设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-api#RULE-api-001, harness-auth#RULE-auth-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: E-04, S-20
- **Estimate**: 1.5d
- **Capability**: 未鉴权 `/v1` 调用被拒绝，Gateway 的 JSON/SSE/重连带内部身份通过

### Description

给 Runtime `/v1` 受信 router 加服务身份依赖，补齐 Gateway/Console/Worker 出站头；SSE 只在建连/重连校验，不让 Token 进入浏览器或沙箱。

### Checklist

- [ ] 交付 `未鉴权 `/v1` 调用被拒绝，Gateway 的 JSON/SSE/重连带内部身份通过`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] 实现 `require_internal_service` 的启动缓存 Token + 异步本地比较，覆盖 runs/conversations
- [ ] Gateway `create_run/submit_run/resume_run/open_events/open_event_stream/get_run/cancel_run/cancel_active/create_conversation` 统一带 `X-Internal-Service`
- [ ] SSE 建连/重连校验一次；浏览器传入的同名头被剥离
- [ ] [E-04][integration] 无/错/对 header 三组断言，真实 Runtime ASGI + 服务 Token
- [ ] [S-20][integration] 首次建连与重连都带身份，无逐事件校验
- [ ] [harness-arch#RULE-arch-001][verifier] 运行 `uv run pytest -q tests/architecture/test_k8s_deploy_manifests.py tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | Gateway → Runtime /v1 → 服务身份校验 | 无 header 拒绝；错误 header 拒绝；正确 header 成功；浏览器传入的 header 不被透传 | tests/acceptance/user_skill_sandbox/test_e_04.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_04.py"] | stale |
| S-20 | integration | Gateway SSE 客户端 → Runtime /v1/runs/{id}/events → 重连 | 首次建连带服务身份；重连带服务身份；无/错误身份被拒绝；不逐事件校验、不增加网络往返 | tests/acceptance/user_skill_sandbox/test_s_20.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_20.py"] | stale |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-04 | - | - | - | - | pending |
| S-20 | - | - | - | - | pending |
- E-04: verified — automated command passed; run_id=29f9d028f2c849e3bb38dbc621ab1965 (confirmed_by: runner)
- S-20: verified — automated command passed; run_id=29f9d028f2c849e3bb38dbc621ab1965 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=00205986bfa349c98a0c13ad0723360a (confirmed_by: runner)
- S-20: verified — automated command passed; run_id=00205986bfa349c98a0c13ad0723360a (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=9f23ab3198224d76830ff288225db214 (confirmed_by: runner)
- S-20: verified — automated command passed; run_id=9f23ab3198224d76830ff288225db214 (confirmed_by: runner)
- E-04: stale — input changed: CHK-a5e8781f55ad (confirmed_by: runtime)
- S-20: stale — input changed: CHK-65644e7dc2a5 (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] completed (done)

---

## TASK-002: bwrap Runner、policy、资源限制与进程树回收

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: user-skill-sandbox.backend.design.md#3.1 方案选型, user-skill-sandbox.backend.design.md#3.2 架构设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-skill#RULE-skill-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: E-02, E-07, S-26
- **Estimate**: 2d
- **Capability**: bwrap 沙箱可启动/超时/取消，完整后代被回收；bwrap 不可用时 fail closed

### Description

实现 `SandboxRunner`/`BwrapRunner`/`SandboxPolicy`：argv、env allowlist、只读/可写挂载、rlimit、tmpfs、超时/取消、`killpg`、`--die-with-parent`；本地 `dev-unsafe` 只允许显式开启。

### Checklist

- [x] 交付 `bwrap 沙箱可启动/超时/取消，完整后代被回收；bwrap 不可用时 fail closed`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 实现 `SandboxSpec`/`SandboxPolicy` 与 bwrap argv 构造，所有字段服务端生成
- [x] env 只保留 allowlist + `SKILL_*`/Task 上下文；不传服务 Token/DB/Redis/SA Token
- [x] 超时、取消、父进程退出、setsid、双重 fork、后台进程都回收完整后代
- [x] bwrap 不可用/版本不兼容/policy 非法时 fail closed；生产禁止 `dev-unsafe`
- [x] [E-02][integration] 超时/取消/后台进程/双重 fork 无孤儿
- [x] [E-07][unit] bwrap 缺失/版本不兼容返回 `SANDBOX_UNAVAILABLE`，不回退宿主执行
- [x] [S-26][unit] macOS 原生 Dev 未显式开启 `dev-unsafe` 时拒绝执行
- [x] [harness-arch#RULE-arch-001][verifier] 运行 `uv run pytest -q tests/architecture/test_k8s_deploy_manifests.py tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | integration | SandboxRunner → bwrap → 完整进程树 | 超时/取消后无孤儿进程；进程组/后代被回收；资源释放，后续执行可正常启动 | tests/acceptance/user_skill_sandbox/test_e_02.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_02.py"] | stale |
| E-07 | unit | SandboxRunner → bwrap 探测 | 返回 SANDBOX_UNAVAILABLE；不回退宿主执行；日志/指标可见 | tests/agent_core/test_sandbox_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_sandbox_runner.py"] | stale |
| S-26 | unit | SandboxRunner 工厂 → 平台检测 → dev-unsafe 开关 | 未显式开启 dev-unsafe 时拒绝执行；显式开启时执行并留下审计；生产配置不能启用 dev-unsafe | tests/agent_core/test_sandbox_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_sandbox_runner.py"] | stale |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-02 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_02.py` → failed：容器内 `ModuleNotFoundError: No module named 'muad_agent_core.sandbox'` | 同命令 → `1 passed in 10.67s`；探针输出 timeout 3017ms/cancel 522ms，三场景 `orphans=[]`，超时后再次执行 `exit_code=0` | `tests/acceptance/user_skill_sandbox/test_e_02.py::test_e02_timeout_cancel_parent_death_reap_complete_tree`；探针 `e_02_probe.py::_async_main` | macOS 无 bwrap，测试把探针放进 `fx-sandbox-test:local`（bwrap 0.13.2，`--security-opt seccomp=unconfined`）真实执行；探针内走真实 `BwrapRunner`/`bwrap`/PID namespace，未 mock | verified |
| E-07 | `uv run pytest -q tests/agent_core/test_sandbox_runner.py` → collection error：`ModuleNotFoundError: No module named 'muad_agent_core.sandbox'` | 同命令 → `16 passed`；缺失/0.11.0/0.13.1 三类真实可执行探测分别 fail closed / fail closed / 返回 `BwrapRunner` | `tests/agent_core/test_sandbox_runner.py::test_missing_bwrap_fails_closed_with_visible_event`、`::test_incompatible_bwrap_version_fails_closed`、`::test_supported_bwrap_probe_returns_bwrap_runner` | 临时目录内真实 `bwrap` 脚本被 `subprocess` 执行；工厂事件 + WARNING 日志可见，未回退宿主 runner | verified |
| S-26 | 同 E-07 的 collection error（同一文件） | `16 passed`；darwin+bwrap 模式拒绝、dev-unsafe 缺 policy flag 拒绝、显式 dev-unsafe 真实子进程执行并产生审计事件；`env=prod` 配置在 `SharedSettings` 与工厂两层拒绝 | `tests/agent_core/test_sandbox_runner.py::test_macos_without_explicit_dev_unsafe_is_refused`、`::test_dev_unsafe_requires_policy_flag_and_emits_audit`、`::test_dev_unsafe_mode_is_only_allowed_in_dev` | 真实 `DevUnsafeRunner` 子进程 + 真实 `caplog`/observer 审计事件；rlimit 由子进程 `resource.getrlimit` 读回（FSIZE/NOFILE） | verified |
- E-02: verified — automated command passed; run_id=1174a509b5024964b7a05fdd7e17dc84 (confirmed_by: runner)
- S-26: verified — automated command passed; run_id=1174a509b5024964b7a05fdd7e17dc84 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=1174a509b5024964b7a05fdd7e17dc84 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=edebe6f1d8f44771ad754302851164c8 (confirmed_by: runner)
- S-26: verified — automated command passed; run_id=edebe6f1d8f44771ad754302851164c8 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=edebe6f1d8f44771ad754302851164c8 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=ab159137af234923bb4af8cf566d2fd0 (confirmed_by: runner)
- S-26: verified — automated command passed; run_id=ab159137af234923bb4af8cf566d2fd0 (confirmed_by: runner)
- E-07: verified — automated command passed; run_id=ab159137af234923bb4af8cf566d2fd0 (confirmed_by: runner)
- E-02: stale — input changed: CHK-28858b5b8379 (confirmed_by: runtime)
- S-26: stale — input changed: CHK-7d605d4fff3d (confirmed_by: runtime)
- E-07: stale — input changed: CHK-7d605d4fff3d (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] RED: `tests/agent_core/test_sandbox_runner.py` 与 `tests/acceptance/user_skill_sandbox/test_e_02.py` 均因 `muad_agent_core.sandbox` 不存在失败（E-07/S-26 collection error；E-02 在真实 bwrap 镜像内 `ModuleNotFoundError`）。
- [2026-11-06] GREEN: 新增 `muad_agent_core.sandbox`（spec/policy/bwrap/runner/workspace）后 `16 passed`；E-02 探针在真实 bwrap 0.13.2 中验证超时/取消/父进程退出后 `orphans=[]` 且可再次执行。
- [2026-11-06] 首次 finish blocked：`harness-arch#RULE-arch-001` 的 `tests/architecture` 因 base 上 ConfigMap `WORKSPACE_*` 无 typed 字段/消费点而红（workspace PVC 提交早于 TASK-005 代码）。补齐 `SharedSettings` 的 workspace 配置 + `WorkspaceLayout` 投影，并把旧「唯一共享卷=artifacts」断言更新为 artifacts + workspaces 两个 RWX 共享卷；`tests/architecture` 212 passed。
- [2026-11-06] completed (done)

---

## TASK-003: SandboxedSkillExecutor、流式输出、只读包与产物登记

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: user-skill-sandbox.backend.design.md#3.2 架构设计, user-skill-sandbox.backend.design.md#3.4 接口设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-skill#RULE-skill-001, harness-secret#RULE-secret-001, harness-api#RULE-api-001
- **Rule-Owner-Refs**: harness-skill#RULE-skill-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-10, S-15, S-47, E-01, E-03
- **Estimate**: 2d
- **Capability**: 已批准 Skill 在只读包/独立 workspace 中执行，stdout 流式有界，输出文件登记为产物

### Description

把所有 `source` 的 Skill 脚本路由到 `SandboxedSkillExecutor`：`script_command` 改 `-E -s -B`，只读 `/skill`，独立 `/workspace`，流式有界读取，超限终止/丢弃，输出文件按边界登记 artifact。

### Checklist

- [x] 交付 `已批准 Skill 在只读包/独立 workspace 中执行，stdout 流式有界，输出文件登记为产物`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 实现 `SandboxedSkillExecutor.execute(request)`，统一 `SkillExecutor` 协议；`source` 缺失/非法拒绝执行
- [x] `script_command` 使用 `python -E -s -B <script>`，入口脚本目录可 import 相邻模块
- [x] 已批准包只读挂载；每次执行独立 workspace；不写共享 READY 缓存
- [x] stdout/stderr 按块读取，超限终止/有界丢弃；不能 `communicate()` 收全量
- [x] 扫描 `SKILL_OUTPUT_DIR`：文件数/大小/路径/symlink 边界，登记 artifact，交付走现有 `deliver_artifact`
- [x] [S-10][integration] 已批准 Skill 只读执行 + 结构化结果 + 审计/指标
- [x] [S-15][integration] 写 `/skill` 失败，并发执行互不影响，READY checksum 不变
- [x] [S-47][integration] 输出目录文件登记为 artifact，超限/ symlink 被拒绝
- [x] [E-01][integration] 持续输出不撑大父进程内存，超限有界失败
- [x] [E-03][integration] 沙箱读不到服务 Token/DB/Redis/SA Token/敏感挂载
- [x] [harness-skill#RULE-skill-001][verifier] 运行 `uv run pytest -q tests/agent_core/test_sandboxed_skill_executor.py tests/acceptance/user_skill_sandbox/test_skill_execution.py`
- [x] [harness-secret#RULE-secret-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_sandbox_boundary.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-10 | integration | Runtime/Worker → SandboxedSkillExecutor → bwrap → stdout/exit code | 脚本在只读包和独立 workspace 中执行；stdout/stderr/exit code 与现有 CLI 契约一致；结构化 JSON 解析成功；执行审计和指标产生 | tests/acceptance/user_skill_sandbox/test_s_10.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_10.py"] | stale |
| S-15 | integration | SandboxedSkillExecutor → 只读 /skill → 可写 /workspace | 写 /skill 失败；两个并发执行互不影响；共享 READY 缓存 checksum 不变 | tests/acceptance/user_skill_sandbox/test_s_15.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_15.py"] | stale |
| S-47 | integration | SandboxedSkillExecutor → SKILL_OUTPUT_DIR → ArtifactStore → 现有附件交付 | 输出文件的相对路径、大小、checksum 被登记；超过文件数/大小上限的产物被拒绝或截断并可见；symlink/越界路径不登记；交付仍走现有 deliver_artifact/附件机制 | tests/acceptance/user_skill_sandbox/test_s_47.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_47.py"] | stale |
| E-01 | integration | SandboxedSkillExecutor → 子进程管道 → 有界读取 | 父进程内存有界，不随输出无限增长；超过上限返回 SANDBOX_OUTPUT_LIMIT 或截断标记；子进程被终止/回收 | tests/acceptance/user_skill_sandbox/test_e_01.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_01.py"] | stale |
| E-03 | integration | bwrap --clearenv + 最小 mount + 子进程环境 | 脚本读不到 INTERNAL_SERVICE_TOKEN、DATABASE_URL、REDIS_URL；读不到 /mnt、/proc、/var/run、Docker socket、SA Token；只读包和 workspace 可见 | tests/acceptance/user_skill_sandbox/test_e_03.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_03.py"] | stale |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-skill#RULE-skill-001` | integration | 真实 Artifact Store/只读包/checksum | 只读包/READY/不可变/checksum | uv run pytest -q tests/agent_core/test_sandboxed_skill_executor.py tests/acceptance/user_skill_sandbox/test_skill_execution.py | verified |
| `harness-secret#RULE-secret-001` | integration | 真实沙箱环境/日志/审计；不输出密钥 | 沙箱无服务 Token/DB/Redis/SA Token | uv run pytest -q tests/acceptance/user_skill_sandbox/test_sandbox_boundary.py | verified |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-10 | `uv run pytest -q tests/agent_core/test_sandboxed_skill_executor.py` 在基线 worktree（58c024f2）→ ImportError：`cannot import name 'OutputLimits' from 'muad_agent_core.skill'` | 同命令 → `22 passed in 4.51s`；真实 bwrap 探针 `test_s_10.py` → `result.status=SUCCEEDED`、`exit_code=0`、结构化 JSON、`/skill` 只读、`SKILL_OUTPUT_DIR` 产物登记、observer 事件 | `tests/acceptance/user_skill_sandbox/test_s_10.py::test_s10_sandboxed_executor_runs_readonly_package_and_reports_audit`；`tests/agent_core/test_sandboxed_skill_executor.py::test_execute_uses_e_s_b_flags_and_imports_adjacent_module` | macOS 无 bwrap：探针在 `muad-skill-executor-probe:8e8cd7dcaec3`（基于 `fx-sandbox-test:local`，bwrap 0.13.2）中真实执行；`--security-opt seccomp=unconfined`，真实 `BwrapRunner`/PID namespace，未 mock | verified |
| S-15 | 同一基线 worktree：`uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_15.py` → 探针 ImportError `OutputLimits` | 同命令 → `1 passed`；两个并发执行各自 `/workspace/outputs/exec-*`、写 `/skill` 返回 `OSError`、READY sentinel 与包树 checksum 前后一致 | `tests/acceptance/user_skill_sandbox/test_s_15.py::test_s15_readonly_package_concurrent_workspaces_and_ready_unchanged` | 真实 bwrap 只读 bind `/skill`、可写 bind `/workspace`；READY 文件由测试现场放入并重读，未 mock 文件系统 | verified |
| S-47 | 同一基线 worktree：探针 ImportError `OutputLimits` | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_47.py` → `2 passed`；相对路径/大小/checksum 登记，symlink（文件/目录）跳过，文件数超限 `SANDBOX_OUTPUT_LIMIT` 可见；`OutputArtifactWriter` 写真实 PG `runtime.artifact` + NFS，`AttachmentToolSet.deliver_artifact` 成功取出同一 artifact | `tests/acceptance/user_skill_sandbox/test_s_47.py::test_s47_outputs_are_bounded_relative_paths_and_symlinks_are_skipped`、`::test_s47_registered_output_is_deliverable_through_existing_artifact_path` | 真实 bwrap 产出文件；登记走真实 `NfsArtifactStore`/PG；只有外部渠道客户端用记录桩，ArtifactStore→deliver_artifact 边界未 mock | verified |
| E-01 | 同一基线 worktree：探针 ImportError `OutputLimits` | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_01.py` → `1 passed`；32 MiB stdout 洪泛下 `stdout_bytes=65536`、父进程 RSS 增量 444 KiB、`orphans=[]`、`SANDBOX_OUTPUT_LIMIT` | `tests/acceptance/user_skill_sandbox/test_e_01.py::test_e01_stdout_flood_is_bounded_and_process_tree_reaped` | 真实 bwrap 子进程管道按 64 KiB 块读取；脚本结束后扫描 `/proc/*/cmdline` 无残留 | verified |
| E-03 | 同一基线 worktree：探针 ImportError `OutputLimits` | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_03.py` → `1 passed`；`INTERNAL_SERVICE_TOKEN`/`DATABASE_URL`/`REDIS_URL`/AWS/`MUAD_SESSION_SOCKET`/`SKILL_TOKEN` 全为 None；`/mnt`、`/proc/1`、docker.sock、SA token 不存在；`/skill` 可读不可写、`/workspace` 可写；stdout/stderr 无密钥 | `tests/acceptance/user_skill_sandbox/test_e_03.py::test_e03_secret_boundary_and_minimal_mounts` | 真实 `bwrap --clearenv` + 最小 mount + 真实 UID 10001；宿主/请求两层 canary 均验证 | verified |
- S-10: verified — automated command passed; run_id=0ade5b89be5b440b9203bfd8e9b65b92 (confirmed_by: runner)
- S-15: verified — automated command passed; run_id=0ade5b89be5b440b9203bfd8e9b65b92 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=0ade5b89be5b440b9203bfd8e9b65b92 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=0ade5b89be5b440b9203bfd8e9b65b92 (confirmed_by: runner)
- S-47: verified — automated command passed; run_id=0ade5b89be5b440b9203bfd8e9b65b92 (confirmed_by: runner)
- S-10: verified — automated command passed; run_id=68af797eced44a468072a9eb7cd6a3b0 (confirmed_by: runner)
- S-15: verified — automated command passed; run_id=68af797eced44a468072a9eb7cd6a3b0 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=68af797eced44a468072a9eb7cd6a3b0 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=68af797eced44a468072a9eb7cd6a3b0 (confirmed_by: runner)
- S-47: verified — automated command passed; run_id=68af797eced44a468072a9eb7cd6a3b0 (confirmed_by: runner)
- S-10: stale — input changed: CHK-f9149a38b729 (confirmed_by: runtime)
- S-15: stale — input changed: CHK-3cef22145e00 (confirmed_by: runtime)
- E-01: stale — input changed: CHK-35cdf38d7a1c (confirmed_by: runtime)
- E-03: stale — input changed: CHK-7acb78f3a8a8 (confirmed_by: runtime)
- S-47: stale — input changed: CHK-83ee77dbbbd4 (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] RED：在 `58c024f2` 基线 worktree 运行新用例，单元测试 `test_sandboxed_skill_executor.py` 与 bwrap 探针（S-15）均因 `muad_agent_core.skill` 缺 `OutputLimits`/`SandboxedSkillExecutor` 失败（ImportError/collection error）。
- [2026-11-06] GREEN：`SandboxedSkillExecutor` + 统一 `SkillExecutor.execute(request)`、`-E -s -B`、只读 `/skill`、独立 `/workspace`、流式有界输出、`SKILL_OUTPUT_DIR` 产物登记；Runtime/Worker 默认装配改为沙箱执行器并冻结 source/workspace。验收：单元+skill verifier `22 passed`；secret verifier `1 passed`；S-10/S-15/S-47/E-01/E-03 `7 passed`。
- [2026-11-06] 说明：本机 macOS 无 bwrap，验收探针在 `muad-skill-executor-probe:8e8cd7dcaec3`（`fx-sandbox-test:local` + 依赖）中运行真实 bwrap；S-47 的 ArtifactStore→deliver_artifact 用真实 PG/NFS，仅外部渠道客户端是记录桩。
- [2026-11-06] completed (done)

---

## TASK-004: session-manager 注入、state 文件与旧 stdout 兼容

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: user-skill-sandbox.backend.design.md#3.2 架构设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-project-platform#RULE-platform-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: S-03, S-13, S-22
- **Estimate**: 1.5d
- **Capability**: Skill 执行/草稿 Shell 可通过 session-manager 获取登录态；旧 `login-mssp-platform` 不改代码可运行

### Description

按 Skill `platforms` 和受信执行身份注入 broker/socket，强制普通只读 state 文件；`session-manager get-state` stdout 同时返回旧版 `platforms` list。

### Checklist

- [x] 交付 `Skill 执行/草稿 Shell 可通过 session-manager 获取登录态；旧 `login-mssp-platform` 不改代码可运行`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 按受信 Run/Task 身份、凭据模式和平台权限计算允许集合，不只信 Skill 声明
- [x] 注入 `MUAD_SESSION_SOCKET`/PATH，bind `/bin`；`workspace_shell` 不注入
- [x] state 文件放执行专属只读目录，不用 memfd/`/proc`；执行结束删除
- [x] stdout 返回 `sessionStateFile` + `platforms` list（platform/source/expiresAt）；state 文件内部仍是 dict
- [x] broker 连接/并发/请求/state 文件数有执行级预算；Cookie 不写日志
- [x] [S-03][integration] 草稿 Shell 获取 mssp state，执行结束文件删除
- [x] [S-13][manual] 旧 login-mssp-platform 不改代码通过真实平台（登记 manual_pending，待内网真实平台）
- [x] [S-22][manual] 有效/无/无效 Cookie 正负向对照（登记 manual_pending，待内网受保护接口）
- [x] [harness-project-platform#RULE-platform-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_session_manager.py tests/platform_sdk/test_skill_bridge.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | Runtime broker → Console 凭据解析 → Redis Session → Unix socket → state 文件 | session-manager get-state 返回 sessionStateFile 和 platforms list；state 文件内可读取 cookies 与 requestProfile；执行结束 state 文件删除；未授权平台返回 PLATFORM_SESSION_REQUIRED | tests/acceptance/user_skill_sandbox/test_s_03.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_03.py"] | e2e_deferred |
| S-13 | integration | 真实 Runtime → 真实 Console/Redis → 真实 mssp 平台 → 旧 Skill 脚本 | 旧脚本不修改即可调用 session-manager get-state；stdout platforms list 包含 mssp 和 expiresAt；退出码 0，输出 ok/expiresAt | manual | - | manual_pending |
| S-22 | integration | 真实 Console/Redis → 真实平台受保护接口 | 有效 Cookie 200；无 Cookie 403；无效 Cookie 403；响应正文不写入证据/日志 | manual | - | manual_pending |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-project-platform#RULE-platform-001` | integration | 真实平台 adapter/Redis/Session；不 mock 登录态 | PlatformSession/adapter 语义不变 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_session_manager.py tests/platform_sdk/test_skill_bridge.py | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-03 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_03.py` → failed：旧 stdout 无 `platforms`，旧脚本 `KeyError: 'platforms'`；bwrap 场景 `FileNotFoundError: session-manager` | 同命令 → `2 passed`；`uv run pytest -q tests/acceptance/user_skill_sandbox/test_session_manager.py` → `4 passed`；bwrap 探针 `state_path=/tmp/fx-session/state/state-*.json`、`proc_path=false`、`bin_sh=true`、`leftover_session_dirs=[]` | `tests/acceptance/user_skill_sandbox/test_s_03.py::test_s03_session_state_file_and_legacy_stdout`、`::test_s03_unauthorized_platform_returns_platform_session_required`；`test_session_manager.py::test_sandboxed_executor_exposes_session_manager_inside_bwrap`、`::test_session_manager_state_is_bound_to_execution_and_deleted` | 真实 Redis 隔离库 + 真实 `AuthPlatformServer` HTTP 登录 + Console resolve 记录桩（校验 `X-Internal-Service`/租户/actor）+ 真实 Unix socket + 真实 `session_cli.py`；bwrap 在 `muad-skill-executor-probe`（真实 `BwrapRunner`，bwrap 0.13.2）内执行，未 mock 登录态/文件系统 | verified |
| S-13 | - | manual_pending：旧脚本契约由 S-03 的 legacy probe（stdout `platforms` list + state dict + expiresAt）覆盖；真实内网 mssp 平台凭据不可得 | `tests/acceptance/user_skill_sandbox/test_s_03.py` | 验收方式：内网真实 Runtime/Console/Redis + 真实 mssp 平台运行未修改的 `login-mssp-platform`，断言退出码 0、stdout 含 mssp/expiresAt；由 TASK-019 在目标环境执行并回填 | manual_pending |
| S-22 | - | manual_pending：需要真实受保护业务接口与浏览器 Cookie；本地只有测试平台桩 | - | 验收方式：`TEST_COOKIE_MODE=valid/none/invalid` 对照 200/403/403，响应正文不写入证据/日志；内网执行时只记录状态码与脱敏字段 | manual_pending |
- S-03: e2e_deferred — automated command e2e_deferred; run_id=1167f7e8c2bf4e32b37e18e65a83bf24 (confirmed_by: runner)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] RED：`tests/platform_sdk/test_skill_bridge.py` collection ImportError（`SessionBrokerBudget` 不存在）；`test_s_03.py` 旧 stdout `KeyError: 'platforms'`；bwrap 探针 `FileNotFoundError: session-manager`（`SandboxedSkillExecutor` 未注入）。
- [2026-11-06] GREEN：broker 改为执行专属普通 state 文件（`state/` 只读 bind，不再 memfd/`/proc`），stdout 同时返回 `platforms` list；`SandboxedSkillExecutor` 注入 `MUAD_SESSION_SOCKET`/PATH 与 broker mounts，`workspace_shell` 不注入；`SkillPlatformAccess.allowed_keys()` 用 Console 决策收敛候选平台；broker 增加连接/请求/state 文件预算与不泄密错误路径。验收：broker `10 passed`、S-03 `2 passed`、session-manager `4 passed`；RULE-platform-001 verifier 同命令通过。
- [2026-11-06] S-13/S-22：需要内网真实 mssp 平台/受保护接口与凭据，登记 manual_pending（边界与验收方式见 Acceptance Evidence），由需求级终验确认。
- [2026-11-06] completed (done)

---

## TASK-005: WorkspaceStore、数据/输出目录与 workspace_shell

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: user-skill-sandbox.backend.design.md#3.2 架构设计, user-skill-sandbox.backend.design.md#3.3 数据设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-skill#RULE-skill-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: B-01, S-11, E-05
- **Estimate**: 2d
- **Capability**: user + Agent workspace 隔离可写，`SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR` 注入；workspace_shell 可用且无 session-manager

### Description

实现 `WorkspaceStore`/`WorkspaceShellRunner`：独立 PVC `/mnt/muad-workspaces`，只 bind 当前用户+Agent 目录到 `/workspace`，acquire/release lease，注入 data/output 目录；workspace_shell 全 Agent 注册、无 skill_key、无 session-manager。

### Checklist

- [x] 交付 `user + Agent workspace 隔离可写，`SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR` 注入；workspace_shell 可用且无 session-manager`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] WorkspaceStore 按 tenant+user+agent 定位，独立 `muad-workspaces-rwx`；不读 artifacts 目录
- [x] 执行前 acquire lease/touch，执行后 release；输出目录按 execution_id 独立
- [x] `SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR` 注入；空值路径处理与原子写约定
- [x] `workspace_shell` 无条件注册，不接受 `skill_key`；默认系统依赖，pip/npm 仅临时验证
- [x] [B-01][integration] 同 user+Agent 跨会话共享 data，不同 Agent 隔离
- [x] [S-11][integration] workspace_shell 命令/临时安装/无 session-manager/配额限制
- [x] [E-05][integration] session-manager 不在 PATH、无 socket
- [x] [harness-data#RULE-data-001][verifier] 运行 `uv run pytest -q tests/migrations/test_skill_sandbox_schema.py tests/acceptance/user_skill_sandbox/test_workspace_store.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-01 | integration | Workspace 元数据 → PVC 目录 → 两次执行 | 同 Agent 两次执行可读同一 data 文件；不同 Agent 看不到对方 workspace；outputs 每次执行独立；跨 tenant/user 不可见 | tests/acceptance/user_skill_sandbox/test_b_01.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_01.py"] | stale |
| S-11 | integration | Runtime 工具 → WorkspaceShellRunner → bwrap → Pod 网络 → workspace venv/node_modules | 普通 shell 命令成功；默认使用系统预装依赖；pip/npm 临时安装写入 workspace，不写系统目录；临时安装跨 Pod/重建后不保证存在，不作为生产依赖；无 session-manager；输出/超时/配额限制生效 | tests/acceptance/user_skill_sandbox/test_s_11.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_11.py"] | stale |
| E-05 | integration | workspace_shell → bwrap → PATH/socket 检查 | session-manager 不在 PATH；MUAD_SESSION_SOCKET 不存在；调用 session-manager 返回 command not found/明确错误 | tests/acceptance/user_skill_sandbox/test_e_05.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_05.py"] | stale |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-01 | 同命令 → `1 error during collection`：`ImportError: cannot import name 'WorkspaceLayout' from 'muad_agent_core.sandbox'`（实现前） | 同命令 → `4 passed`；`test_workspace_store.py` + `test_b_01.py` 联合 `8 passed` | `tests/acceptance/user_skill_sandbox/test_b_01.py::test_b01_same_agent_keeps_data_and_isolates_outputs_per_execution`、`::test_b01_other_agent_user_tenant_cannot_see_workspace`、`::test_b01_workspace_root_does_not_touch_artifact_root` | 真实 PG（`task.workspace`/`task.workspace_lease`）+ `/tmp` workspace 根 + `muad-skill-deps-probe` 内真实 bwrap 0.13.1；bwrap 只 bind 当前 Agent 目录到 `/workspace`，`/workspace/../<other-tenant>` 与 `/mnt` 不可见；两次执行共享 data、outputs 按 execution 独立；artifacts 根零写入 | verified |
| S-11 | 同命令 → `1 error during collection`：`ImportError: cannot import name 'WorkspaceStore'`（实现前） | 同命令 → `4 passed`；探针内 `WorkspaceShellRunner` → `BwrapRunner` → bwrap 0.13.1 全链真实执行 | `.../test_s_11.py::test_s11_workspace_shell_runs_command_with_system_dependencies`、`::test_s11_temp_installs_stay_inside_workspace_and_are_not_guaranteed`、`::test_s11_timeout_and_output_limits_are_enforced_with_recovery`、`::test_s11_workspace_shell_is_registered_for_every_agent_without_skill_key` | 系统基线 `tabulate` 直接 import；真实 pip/npm 临时安装全部落在 workspace（`system_hits=[]`），新 Pod/新 Agent workspace `ModuleNotFoundError`；超时 `SANDBOX_TIMEOUT`、输出上限 `SANDBOX_OUTPUT_LIMIT`，失败后同一 workspace 可继续执行；配额拒绝/TTL 清理由依赖 TASK-006 承担，本任务不重复实现 | verified |
| E-05 | 同命令 → `1 failed`；探针 `ImportError: cannot import name 'WorkspaceLeaseRecord' from 'muad_agent_core.sandbox'`（实现前） | 同命令 → `1 passed in 3.11s` | `tests/acceptance/user_skill_sandbox/test_e_05.py::test_e05_workspace_shell_has_no_session_manager_path_or_socket` | 真实 bwrap 内 PATH=`/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin`；`shutil.which('session-manager')=None`；`MUAD_SESSION_SOCKET=null`；`session-manager get-state` → exit 127、`sh: session-manager: command not found` | verified |
- S-11: verified — automated command passed; run_id=bad66a2d4d3c4825896508574214e7e9 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=bad66a2d4d3c4825896508574214e7e9 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=bad66a2d4d3c4825896508574214e7e9 (confirmed_by: runner)
- S-11: verified — automated command passed; run_id=11f8d87e57f548c7966bacf673f211e1 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=11f8d87e57f548c7966bacf673f211e1 (confirmed_by: runner)
- E-05: verified — automated command passed; run_id=11f8d87e57f548c7966bacf673f211e1 (confirmed_by: runner)
- S-11: stale — input changed: CHK-d45c13e873eb (confirmed_by: runtime)
- B-01: stale — input changed: CHK-3036fe86f429 (confirmed_by: runtime)
- E-05: stale — input changed: CHK-7fd060f09f01 (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] RED: 新增 B-01/S-11/E-05 与 WorkspaceStore 测试在实现前失败：B-01/S-11/WorkspaceStore/tool 用例 collection error（`ImportError: cannot import name 'WorkspaceLayout'/'WorkspaceStore'/'WorkspaceDeleting'`），E-05 探针失败于 `ImportError: cannot import name 'WorkspaceLeaseRecord'`。
- [2026-11-06] GREEN: `uv run pytest -q tests/acceptance/user_skill_sandbox/{test_workspace_store,test_b_01,test_s_11,test_e_05}.py tests/agent_runtime/test_workspace_shell_tool.py` → `19 passed`；`tests/migrations/test_skill_sandbox_schema.py + test_workspace_store.py` → `11 passed`；`tests/architecture` → `215 passed`。
- [2026-11-06] completed (done)

---

## TASK-006: workspace 配额、TTL 与 Worker 清理循环

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-007
- **Source**: user-skill-sandbox.backend.design.md#3.3 数据设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案, user-skill-sandbox.backend.design.md#4.1 部署架构
- **Spec-Refs**: harness-worker#RULE-worker-001, harness-time#RULE-time-001, harness-data#RULE-data-001
- **Rule-Owner-Refs**: harness-worker#RULE-worker-001, harness-time#RULE-time-001
- **Acceptance-Refs**: E-06, S-24, B-02, E-16
- **Estimate**: 1.5d
- **Capability**: workspace 超限被拒绝；30 天未访问目录被清理，活跃执行跳过，多副本不重复

### Description

实现配额检查、`last_access_time`、TTL、`workspace.maintenance` 循环、DB 认领/租约、`DELETING`/`DELETED` 状态和指标；不新增 CronJob/管理员 Schedule。

### Checklist

- [x] 交付 `workspace 超限被拒绝；30 天未访问目录被清理，活跃执行跳过，多副本不重复`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 配额：每 workspace 500 MiB、每用户 500 MiB、文件数 5,000；超限 `WORKSPACE_QUOTA_EXCEEDED`
- [x] TTL：outputs/data 30 天，last_access_time 由执行/Shell 更新
- [x] Worker 内置 `workspace.maintenance` 循环，多副本用 DB 认领/advisory lock；活跃租约跳过
- [x] 先标记 DELETING，再删文件，最后 DELETED；失败保留状态、日志和指标，下轮重试
- [x] [E-06][integration] 超限拒绝，不静默截断
- [x] [S-24][integration] 过期删除、活跃跳过、指标记录
- [x] [B-02][integration] 两副本只有一个认领
- [x] [E-16][integration] 删除失败可见且可重试
- [x] [harness-worker#RULE-worker-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_workspace_cleanup.py tests/agent_worker/test_worker_maintenance.py`
- [x] [harness-time#RULE-time-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_workspace_cleanup.py -k ttl`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-06 | integration | Workspace quota check → PVC | 超限返回 WORKSPACE_QUOTA_EXCEEDED；不静默截断/不写系统目录；审计/指标记录 | tests/acceptance/user_skill_sandbox/test_e_06.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_06.py"] | stale |
| S-24 | integration | Worker workspace.maintenance → 元数据 → PVC | 过期目录被删除；活跃目录跳过；outputs/data 均按 30 天 TTL；清理指标记录扫描/删除/跳过 | tests/acceptance/user_skill_sandbox/test_s_24.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_24.py"] | stale |
| B-02 | integration | 多 Worker 并发 → PG advisory lock/认领 | 只有一个副本删除；另一个副本跳过；不重复计数/不重复删除 | tests/acceptance/user_skill_sandbox/test_b_02.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_02.py"] | stale |
| E-16 | integration | Worker workspace.maintenance → 删除失败 → 状态/指标 | 保留 DELETING/失败状态；记日志和指标；下一轮可重试，不静默吞掉 | tests/acceptance/user_skill_sandbox/test_e_16.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_16.py"] | stale |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-worker#RULE-worker-001` | integration | 真实 PG/Worker 维护循环 | PG 权威/租约/清理幂等 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_workspace_cleanup.py tests/agent_worker/test_worker_maintenance.py | passed |
| `harness-time#RULE-time-001` | integration | 真实 PG timestamptz/TTL | 时间/时区/TTL 一致 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_workspace_cleanup.py -k ttl | passed |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| E-06 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_06.py` → collection `ImportError: cannot import name 'WorkspaceQuotaExceeded'` | 同命令 → `3 passed`；超限拒绝 + 文件数 + 用户聚合三组真实 PG/文件系统断言 | `test_e_06.py::test_e06_workspace_size_quota_rejects_without_truncating`、`::test_e06_file_count_quota_rejects`、`::test_e06_user_aggregate_quota_rejects_new_agent_workspace` | 真实 PostgreSQL `task.workspace`/`task.workspace_lease` + 真实稀疏文件（501 MiB/5,001 文件）+ `workspace_quota_rejected_total` 指标与 warning 日志；拒绝后文件字节数不变、无活跃租约、无系统目录写入 | verified |
| S-24 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_24.py` → `ModuleNotFoundError: muad_agent_worker.worker.workspace_maintenance` | 同命令 → `2 passed`；过期删除、活跃跳过、30 天边界均通过 | `test_s_24.py::test_s24_expired_workspace_is_deleted_and_active_lease_is_skipped`、`::test_ttl_cutoff_is_30_days_and_uses_postgres_timestamptz` | 真实 PG 行 + 真实 workspace 目录；PG `now()` 作为时间权威；删除后 `data/` 与 `outputs/` 同时消失，活跃租约目录原样保留；`workspace_cleanup_total{status=SCANNED/DELETED/SKIPPED}` 逐项计数 | verified |
| B-02 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_b_02.py` → 同上模块缺失 | 同命令 → `1 passed`；两个副本并发只产生一次删除与一次 DELETED 计数 | `test_b_02.py::test_b02_two_replicas_delete_once` | 两个独立 `WorkspaceMaintenanceLoop` 同时扫同一真实 PG；`FOR UPDATE SKIP LOCKED` + 会话级 advisory lock 使第二个副本认领失败；目录只删一次、行不重复 | verified |
| E-16 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_16.py` → 同上模块缺失 | 同命令 → `1 passed`；首轮失败保留 DELETING，次轮重试成功 | `test_e_16.py::test_e16_failure_keeps_deleting_state_and_retries_next_round` | 真实 PG 状态机 + 真实目录；只注入 `OSError` 删除失败（场景前置），断言 `cleanup_error`、错误日志、`FAILED` 指标、目录仍在；下一轮真实 `shutil.rmtree` 删除并置 DELETED | verified |
- E-06: verified — automated command passed; run_id=2ff9476312e0429e8452eb03dc46974b (confirmed_by: runner)
- S-24: verified — automated command passed; run_id=2ff9476312e0429e8452eb03dc46974b (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=2ff9476312e0429e8452eb03dc46974b (confirmed_by: runner)
- E-16: verified — automated command passed; run_id=2ff9476312e0429e8452eb03dc46974b (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=a5a6c252be4f4c28b8f6bdaf712819d3 (confirmed_by: runner)
- S-24: verified — automated command passed; run_id=a5a6c252be4f4c28b8f6bdaf712819d3 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=a5a6c252be4f4c28b8f6bdaf712819d3 (confirmed_by: runner)
- E-16: verified — automated command passed; run_id=a5a6c252be4f4c28b8f6bdaf712819d3 (confirmed_by: runner)
- E-06: verified — automated command passed; run_id=403ce4aea25d446e95d124ec3912dff4 (confirmed_by: runner)
- S-24: verified — automated command passed; run_id=403ce4aea25d446e95d124ec3912dff4 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=403ce4aea25d446e95d124ec3912dff4 (confirmed_by: runner)
- E-16: verified — automated command passed; run_id=403ce4aea25d446e95d124ec3912dff4 (confirmed_by: runner)
- E-06: stale — input changed: CHK-c2aafcb05a89 (confirmed_by: runtime)
- S-24: stale — input changed: CHK-9a49a7a1d0d7 (confirmed_by: runtime)
- B-02: stale — input changed: CHK-41b9fa6e3b8f (confirmed_by: runtime)
- E-16: stale — input changed: CHK-c5cd45a2684a (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] RED: 新增 `test_e_06.py`/`test_s_24.py`/`test_b_02.py`/`test_e_16.py`/`test_workspace_cleanup.py`/`tests/agent_worker/test_worker_maintenance.py` 先跑 → 6 个模块 collection error（`WorkspaceQuotaExceeded`/`WorkspaceQuotaPolicy`/`workspace_maintenance` 不存在）。
- [2026-11-06] GREEN: 配额/TTL/维护循环实现后，四个验收场景 `3+2+1+1 passed`；verifier `9 passed` 与 `-k ttl` `1 passed`；`tests/agent_worker` 331 passed、`tests/agent_runtime` 1202 passed、`tests/architecture` 215 passed、`user_skill_sandbox` 72 passed、迁移/契约 16 passed。
- [2026-11-06] finish 首次 block：`finish validation failed: Python 类型检查`（`main.py` 固定长度 tuple 追加 workspace 循环）；改为 list 后 `uv run mypy apps packages` 524 files 通过，重跑 finish。
- [2026-11-07] completed (done)

---

## TASK-007: 数据模型、迁移、契约与快照扩展

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: user-skill-sandbox.backend.design.md#3.3 数据设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-01, S-04, S-10, B-04
- **Estimate**: 2d
- **Capability**: Skill/Artifact/Draft/Workspace schema、迁移、`source` 契约和 execution snapshot 可用

### Description

新增 skill.source/author/owner_user_id、artifact 审批字段、skill_draft、task.workspace/workspace_lease；迁移回填存量；ResolvedSkill 增加 source，execution snapshot 冻结 source/artifact/workspace/platform keys。

### Checklist

- [x] 交付 `Skill/Artifact/Draft/Workspace schema、迁移、`source` 契约和 execution snapshot 可用`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 新增/修改模型、迁移、索引；存量回填 `source=ADMIN`、`review_status=APPROVED`
- [x] 单 Skill 最多一个 PENDING 的 partial unique；owner/key、review、last_access_time 索引
- [x] `ResolvedSkill.source`、snapshot `skills[]` 冻结 source/artifact/checksum/workspace/platform keys
- [x] 迁移可重复执行/回滚；旧数据/旧客户端缺失字段有明确默认或拒绝
- [x] [S-01][integration] 草稿表/revision 读写
- [x] [S-04][integration] PENDING artifact 字段完整
- [x] [S-10][integration] 执行快照含 source/artifact/workspace
- [x] [B-04][integration] 系统 Skill effective 查询字段一致
- [x] [harness-data#RULE-data-001][verifier] 运行 `uv run pytest -q tests/migrations/test_skill_sandbox_schema.py tests/console_skill/test_skill_repository.py`
- [x] [harness-snapshot#RULE-snapshot-001][verifier] 运行 `uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py -k snapshot`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | Console 内部 API → Draft Store/PVC → PG revision | 草稿创建成功，路径按 tenant/user/draft_key 隔离；写入、读取、列目录、删除单文件和删除整个草稿均成功；跨用户访问同一 draft_key 返回 not found/forbidden；revision 冲突返回 DRAFT_REVISION_CONFLICT | tests/acceptance/user_skill_sandbox/test_s_01.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_01.py"] | referenced |
| S-04 | integration | Console API → SkillValidator → Artifact Store → PG | 校验返回 pass/warn 明细；打包生成 checksum、文件清单和 PENDING artifact；记录 submitted_by、submitted_agent、submitted_channel、author；重复提交不产生多个 PENDING | tests/acceptance/user_skill_sandbox/test_s_04.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_04.py"] | referenced |
| S-10 | integration | Runtime/Worker → SandboxedSkillExecutor → bwrap → stdout/exit code | 脚本在只读包和独立 workspace 中执行；stdout/stderr/exit code 与现有 CLI 契约一致；结构化 JSON 解析成功；执行审计和指标产生 | tests/acceptance/user_skill_sandbox/test_s_10.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_10.py"] | referenced |
| B-04 | integration | Console/Worker/Runtime effective 查询 | list/count/catalog/snapshot/execution 均包含；不需要 binding；普通 Skill 查询不被误放大 | tests/acceptance/user_skill_sandbox/test_b_04.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_04.py"] | referenced |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-data#RULE-data-001` | integration | 真实 PG 迁移/schema | 标准列/软删唯一/timestamptz/jsonb | uv run pytest -q tests/migrations/test_skill_sandbox_schema.py tests/console_skill/test_skill_repository.py | passed |
| `harness-snapshot#RULE-snapshot-001` | contract | 真实 execution snapshot/contract | source/artifact/workspace/platform keys 冻结 | uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py -k snapshot | passed |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | `uv run pytest -q tests/acceptance/user_skill_sandbox` → collection error：`SkillDraft`/`SkillSource` 未实现 | 3 passed（`test_s_01.py`）；`uv run pytest -q tests/acceptance/user_skill_sandbox` → 7 passed | `tests/acceptance/user_skill_sandbox/test_s_01.py:31,55,76` | 真实 PostgreSQL `control.skill_draft`；`SkillDraftRepository` revision CAS（过期返回 None）与 tenant/owner 隔离；文件/PVC 路径由 TASK-008 扩展 | passed |
| S-04 | 0037 迁移缺席（`Can't locate revision identified by '0037'`）；实现前同目录验收被 `SkillSource`/`SkillDraft` collection error 中断，`source`/`review_status` 字段不存在 | 2 passed（`test_s_04.py`）；`uv run pytest -q tests/acceptance/user_skill_sandbox` → 7 passed | `tests/acceptance/user_skill_sandbox/test_s_04.py:69,101` | 真实 PostgreSQL：PENDING artifact 的 submitted_by/submitted_agent/submitted_channel/author 可读回；`uq_skill_artifact_one_pending` 拒绝第二个 PENDING，批准后可插入新 PENDING | passed |
| S-10 | `ImportError: cannot import name 'SkillSource' from 'muad_contracts'` | 1 passed（`test_s_10.py`）；`uv run pytest -q tests/acceptance/user_skill_sandbox` → 7 passed | `tests/acceptance/user_skill_sandbox/test_s_10.py:92,101` | 真实 PostgreSQL `task.task_execution.execution_snapshot_json`：重读后 source/artifact/checksum/workspace/platforms 与 `snapshot_hash` 一致；bwrap/stdout 由 TASK-003 扩展 | passed |
| B-04 | `ImportError: cannot import name 'SkillSource' from 'muad_contracts'` | 1 passed（`test_b_04.py`）；`uv run pytest -q tests/acceptance/user_skill_sandbox` → 7 passed | `tests/acceptance/user_skill_sandbox/test_b_04.py:119,155` | 真实 PostgreSQL `control.skill`+`agent_skill_binding` → `SkillRepository` → execution snapshot：SYSTEM/APPROVED 字段一致；SYSTEM 无需 binding 的可见性由 TASK-012 扩展 | passed |

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] completed (done)

---

## TASK-008: 草稿 API、校验打包、提交 PENDING 与状态查询

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: user-skill-sandbox.backend.design.md#3.3 数据设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-api#RULE-api-002, harness-api#RULE-api-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-api#RULE-api-002
- **Acceptance-Refs**: S-01, S-04
- **Estimate**: 2d
- **Capability**: 用户可创建/读写草稿、校验打包、提交 PENDING、查询状态和驳回原因

### Description

实现草稿内部 API/工具、SkillValidator 扩展、zip/checksum/PENDING artifact、`skill_draft_status`/`skill_draft_review`；提交支持幂等键。

### Checklist

- [x] 交付 `用户可创建/读写草稿、校验打包、提交 PENDING、查询状态和驳回原因`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 草稿 CRUD + revision CAS + 路径/配额校验；跨用户隔离
- [x] `skill_draft_review` 只读校验；`skill_draft_submit` 打包/checksum/PENDING
- [x] `skill_draft_status` 返回最近 artifact/review_status/review_note
- [x] 导入/提交 `Idempotency-Key`：规范化指纹含 endpoint/tenant/actor，重放首次结果
- [x] [S-01][integration] 草稿读写/冲突/越权
- [x] [S-04][integration] 校验/打包/PENDING/幂等
- [x] [harness-api#RULE-api-002][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_draft_idempotency.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | Console 内部 API → Draft Store/PVC → PG revision | 草稿创建成功，路径按 tenant/user/draft_key 隔离；写入、读取、列目录、删除单文件和删除整个草稿均成功；跨用户访问同一 draft_key 返回 not found/forbidden；revision 冲突返回 DRAFT_REVISION_CONFLICT | tests/acceptance/user_skill_sandbox/test_s_01.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_01.py"] | stale |
| S-04 | integration | Console API → SkillValidator → Artifact Store → PG | 校验返回 pass/warn 明细；打包生成 checksum、文件清单和 PENDING artifact；记录 submitted_by、submitted_agent、submitted_channel、author；重复提交不产生多个 PENDING | tests/acceptance/user_skill_sandbox/test_s_04.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_04.py"] | stale |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-api#RULE-api-002` | integration | 真实 PG 幂等表/并发 | 同 key 同指纹重放，不同指纹冲突 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_draft_idempotency.py | passed |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_01.py` → 2 failed（内部草稿 API 未注册，404） | 5 passed（`test_s_01.py`）；同目录 `uv run pytest -q tests/acceptance/user_skill_sandbox` → 33 passed | `tests/acceptance/user_skill_sandbox/test_s_01.py:195,218,238,348` | 真实 ASGI 内部 API → `SkillDraftStore` 独立临时 PVC 根 → 真实 PostgreSQL `control.skill_draft` revision CAS；跨用户/租户 404、路径穿越 400、文件数配额 413；删除草稿后目录与 DB 软删一致 | passed |
| S-04 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_04.py` → 4 failed（内部草稿 API 404） | 4 passed（`test_s_04.py`）；幂等验收集 4 passed | `tests/acceptance/user_skill_sandbox/test_s_04.py:311,389,453` | 真实内部 API → `review_package`/`validated_package` → 不可变 artifact zip（checksum 回读一致）→ 真实 PG PENDING artifact + partial unique；review 无 artifact/无 revision 副作用；重复提交同 revision 只保留一个 PENDING，新 revision 将旧 PENDING 置 SUPERSEDED | passed |
- S-01: verified — automated command passed; run_id=448d50e40d774bc080b0f56ed8807449 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=448d50e40d774bc080b0f56ed8807449 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=ba11a5ecbfb34fdab8c79114c996b945 (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=ba11a5ecbfb34fdab8c79114c996b945 (confirmed_by: runner)
- S-01: stale — input changed: CHK-d8c2a60db0c1 (confirmed_by: runtime)
- S-04: stale — input changed: CHK-fa9379eba61b (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] completed (done)

---

## TASK-009: 草稿 Sandbox Shell 与 revision CAS 回写

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-003, TASK-008
- **Source**: user-skill-sandbox.backend.design.md#3.2 架构设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-skill#RULE-skill-001, harness-project-platform#RULE-platform-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-02, S-03
- **Estimate**: 1.5d
- **Capability**: 草稿 Shell 可执行 Python/Node/session-manager，合法变更按 revision CAS 回写

### Description

Runtime `skill_draft_shell` 调用 Console 内部 API 取草稿 zip；在沙箱执行；执行后校验变更路径/扩展名/大小/symlink，按 revision 回写。

### Checklist

- [x] 交付 `草稿 Shell 可执行 Python/Node/session-manager，合法变更按 revision CAS 回写`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 工具名唯一 `skill_draft_shell`，不接受旧别名/兼容名
- [x] 只作用于当前用户当前草稿副本；不挂生产 PVC/其他草稿
- [x] 支持 `python -m py_compile`、`python scripts/run.py`、`node --check`、`session-manager get-state`
- [x] 变更校验 + revision CAS；冲突 `DRAFT_REVISION_CONFLICT`
- [x] [S-02][integration] Python/Node 执行 + 合法回写 + 越界拒绝
- [x] [S-03][integration] 草稿 Shell session-manager state 获取
- [x] [harness-skill#RULE-skill-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_draft_shell.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | Runtime 工具 → 草稿副本 → bwrap → 变更校验 → Console 回写 | python scripts/run.py 和 node --check 可执行；变更文件按 revision CAS 回写到草稿；越界路径/symlink/超大文件被拒绝；命令超时/输出超限错误码正确 | tests/acceptance/user_skill_sandbox/test_s_02.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_02.py"] | verified |
| S-03 | integration | Runtime broker → Console 凭据解析 → Redis Session → Unix socket → state 文件 | session-manager get-state 返回 sessionStateFile 和 platforms list；state 文件内可读取 cookies 与 requestProfile；执行结束 state 文件删除；未授权平台返回 PLATFORM_SESSION_REQUIRED | tests/acceptance/user_skill_sandbox/test_s_03.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_03.py"] | deferred |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-02 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_02.py` → 1 error（`ModuleNotFoundError: muad_agent_core.skill.draft_shell`，草稿 Shell 未实现） | 5 passed（`test_s_02.py`）；S-02/S-03/规则套件合计 15 passed | `tests/acceptance/user_skill_sandbox/test_s_02.py:162,164,203,206,209,240,243,301,339` | 真实 `SkillToolSet` 工具注册与参数校验 → 真实 Console ASGI 内部 API（archive/apply）→ 真实 PostgreSQL revision CAS；Docker 内真实 bwrap 执行 python/node/session-manager；symlink/`.exe`/超 1KiB/sleep 超时/输出超限/并发 CAS/跨 owner 全部按错误码拒绝且 revision 不变 | passed |
| S-03 | 同批 RED：`test_s_03.py` 收集失败（`muad_agent_core.skill.draft_shell` 不存在） | 4 passed（`test_s_03.py`，含 2 条草稿 Shell 用例） | `tests/acceptance/user_skill_sandbox/test_s_03.py:167,177,201` | 真实 Console 凭据解析桩 → 真实 Redis Session → Unix socket → 真实 `session-manager` CLI → state 文件；草稿声明 mssp 时返回 sessionStateFile/platforms 并可读 cookies/requestProfile，执行结束 state 删除；未授权平台返回 PLATFORM_SESSION_REQUIRED | passed |
- S-02: verified — automated command passed; run_id=ccc0fdd82f5841828b201ed139d146f7 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=95712d5b421c460689e59178afb4860d (confirmed_by: runner)

### Log

- [2026-11-06] created (draft)
- [2026-11-07] started
- [2026-11-07] completed (done)

---

## TASK-010: 审批事务、owner 覆盖、授权与绑定

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-008
- **Source**: user-skill-sandbox.backend.design.md#3.3 数据设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-rel#RULE-rel-001
- **Rule-Owner-Refs**: harness-auth#RULE-auth-001, harness-rel#RULE-rel-001
- **Acceptance-Refs**: S-05, S-06, B-03, E-08, E-09, E-10, E-11
- **Estimate**: 2d
- **Capability**: 管理员可批准/驳回；owner 只能覆盖自己的 PENDING；审批自动授权/绑定；已审批锁定

### Description

实现 PENDING/APPROVED/REJECTED/SUPERSEDED 状态机、owner 校验、单 PENDING partial unique、审批行锁事务、自动 `SkillUserGrant`/`AgentSkillBinding`、`AGENT_ACCESS_REVOKED`。

### Checklist

- [x] 交付 `管理员可批准/驳回；owner 只能覆盖自己的 PENDING；审批自动授权/绑定；已审批锁定`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] owner 只认 `skill.owner_user_id`；不同用户同 key `SKILL_KEY_EXISTS`
- [x] 同一 owner 覆盖旧 PENDING -> SUPERSEDED；单 Skill 最多一个 PENDING
- [x] 审批事务锁 artifact 行、复核 PENDING、切 current_artifact_id、写 grant/binding/audit
- [x] 审批时复核 AgentAccessGrant；撤销返回 `AGENT_ACCESS_REVOKED`，不自动恢复
- [x] 已审批锁定；驳回必须 review_note；驳回可重提
- [x] [S-05][integration] 批准 + 自动授权/绑定 + 审计
- [x] [S-06][integration] 驳回/原因/重提
- [x] [B-03][integration] 同 owner PENDING 覆盖
- [x] [E-08][integration] 跨用户同 key 拒绝
- [x] [E-09][integration] 已审批锁定
- [x] [E-10][integration] AgentAccessGrant 撤销
- [x] [E-11][integration] 并发审批/提交单方成功
- [x] [harness-auth#RULE-auth-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_skill_authorization.py tests/console_skill/test_skill_rbac.py`
- [x] [harness-rel#RULE-rel-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_skill_authorization.py -k relation`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | Console API → 审批事务 → PG → 审计 | artifact 变 APPROVED，current_artifact_id 更新；提交者获得 SkillUserGrant；上传时 Agent 获得 AgentSkillBinding；审计记录 actor/skill/artifact/agent | tests/acceptance/user_skill_sandbox/test_s_05.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_05.py"] | stale |
| S-06 | integration | Console API → 状态机 → PG | 驳回要求 review_note，状态变 REJECTED；用户可看到驳回原因；修改后可重新提交新 PENDING | tests/acceptance/user_skill_sandbox/test_s_06.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_06.py"] | stale |
| B-03 | integration | Console API → 状态机 → PG | 旧 PENDING 变 SUPERSEDED；新 PENDING 成为唯一待审；旧包审批返回冲突 | tests/acceptance/user_skill_sandbox/test_b_03.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_03.py"] | stale |
| E-08 | integration | Console API → owner 校验 → PG | B 返回 SKILL_KEY_EXISTS；A 的 PENDING 不受影响；B 看不到 A 的草稿/PENDING | tests/acceptance/user_skill_sandbox/test_e_08.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_08.py"] | stale |
| E-09 | integration | Console API → 审批状态校验 | 用户提交返回 SKILL_APPROVED_LOCKED；现有 artifact 不变 | tests/acceptance/user_skill_sandbox/test_e_09.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_09.py"] | stale |
| E-10 | integration | Console 审批事务 → AgentAccessGrant 校验 | 审批失败 AGENT_ACCESS_REVOKED；不自动恢复授权；artifact 仍 PENDING，管理员可决定后续处理 | tests/acceptance/user_skill_sandbox/test_e_10.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_10.py"] | stale |
| E-11 | integration | 两个并发事务 → artifact 行锁/状态复核 | 只有一方成功；另一方得到明确冲突；不出现 current_artifact_id 指向未批准版本 | tests/acceptance/user_skill_sandbox/test_e_11.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_11.py"] | stale |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-auth#RULE-auth-001` | integration | 真实 PG/授权关系/HTTP | owner/审批/grant/binding/effective | uv run pytest -q tests/acceptance/user_skill_sandbox/test_skill_authorization.py tests/console_skill/test_skill_rbac.py | planned |
| `harness-rel#RULE-rel-001` | integration | 真实 PG 关系表 | 单关系 POST/DELETE，无全量 PUT | uv run pytest -q tests/acceptance/user_skill_sandbox/test_skill_authorization.py -k relation | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-05 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_05.py` → 1 failed（审批路由未注册，404 COMMON_NOT_FOUND） | 1 passed（`test_s_05.py`）；同批 8 passed（S-05/S-06/B-03/E-08/E-09/E-10/E-11） | `tests/acceptance/user_skill_sandbox/test_s_05.py:49,57,59,101` | 真实管理员登录 + Console API → 审批事务 → 真实 PostgreSQL；artifact APPROVED、`current_artifact_id` 切换、SkillUserGrant/AgentSkillBinding 各一行、`SKILL_REVIEW` 审计含 actor/skill/artifact/agent；列表 `review_status=PENDING/APPROVED` 过滤与详情回读一致 | passed |
| S-06 | 同批 RED：审批路由 404 | 1 passed（`test_s_06.py`） | `tests/acceptance/user_skill_sandbox/test_s_06.py:42,52,71,80,98` | 真实 Console 审批 API → 真实 PG：无 note 422 COMMON_VALIDATION_ERROR；有 note 置 REJECTED 且内部 status 读回原因；修改草稿后可重提新 PENDING，旧 REJECTED 保留 | passed |
| B-03 | 同批 RED：审批路由 404 | 1 passed（`test_b_03.py`） | `tests/acceptance/user_skill_sandbox/test_b_03.py:54,61,67,77` | 真实提交/审批 API → 真实 PG：同 owner 新提交把旧 PENDING 置 SUPERSEDED；单 Skill 仅一个 PENDING；旧包审批 409 且 current 不被错误切换，新包审批成功后才切换 | passed |
| E-08 | 先测通过（owner 校验为 TASK-008 既有行为）；新增用例固化跨用户同 key 409 与不可见性 | 1 passed（`test_e_08.py`） | `tests/acceptance/user_skill_sandbox/test_e_08.py:53,78,102` | 真实内部 API/通道 effective 查询 → 真实 PG：B 同 key 提交 409 SKILL_KEY_EXISTS；A 的 PENDING 仍唯一；B 读 A 草稿/状态均为 DRAFT_NOT_FOUND；PENDING 不进入任何用户 effective 列表 | passed |
| E-09 | 同批 RED：审批路由 404 | 1 passed（`test_e_09.py`） | `tests/acceptance/user_skill_sandbox/test_e_09.py:52,58,63,72` | 真实审批 + 草稿提交 API → 真实 PG：批准后同 revision 重放与修改后新 revision 提交均 409 SKILL_APPROVED_LOCKED；artifact 总数与 current 保持不变 | passed |
| E-10 | 同批 RED：审批路由 404 | 1 passed（`test_e_10.py`） | `tests/acceptance/user_skill_sandbox/test_e_10.py:47,54,79,89` | 真实撤销 `AgentAccessGrant` → 审批事务复核 → 409 AGENT_ACCESS_REVOKED；artifact 仍 PENDING、无 grant/binding、授权未被自动恢复；管理员仍可驳回 | passed |
| E-11 | 并发审批用例 RED（审批路由 404）；并发提交用例先测通过（TASK-008 草稿 CAS） | 2 passed（`test_e_11.py`） | `tests/acceptance/user_skill_sandbox/test_e_11.py:47,57,101,120,129` | 两个独立真实 HTTP 请求/数据库事务：并发审批一 200 一 409，current 指向唯一 APPROVED artifact；并发不同版本提交一 200 一 409，仅一个 PENDING 且 current 为空 | passed |
- S-05: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- E-08: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- E-09: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- E-10: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- E-11: verified — automated command passed; run_id=8ec7612252354507988c0f16b322e63b (confirmed_by: runner)
- S-05: stale — input changed: CHK-e8f74f96e6e3 (confirmed_by: runtime)
- S-06: stale — input changed: CHK-7602fe3c28e6 (confirmed_by: runtime)
- E-08: stale — input changed: CHK-c5a693c6ca28 (confirmed_by: runtime)
- B-03: stale — input changed: CHK-271bf7724d0e (confirmed_by: runtime)
- E-09: stale — input changed: CHK-a2fa43288567 (confirmed_by: runtime)
- E-10: stale — input changed: CHK-a5ff90feccf4 (confirmed_by: runtime)
- E-11: stale — input changed: CHK-148108b28cec (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] completed (done)

---

## TASK-011: Artifact 下载、checksum 与解绑软删

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-010
- **Source**: user-skill-sandbox.backend.design.md#3.4 接口设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-skill#RULE-skill-001, harness-api#RULE-api-001, harness-auth#RULE-auth-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-07, S-08, E-15
- **Estimate**: 1.5d
- **Capability**: 管理员可下载待审/已审批包并写审计；checksum 不匹配拒绝；一键解绑+软删

### Description

实现 artifact 下载、checksum 校验、`SKILL_ARTIFACT_DOWNLOAD` 审计；`DELETE ?unbind=true` 解除所有 AgentSkillBinding/用户 grant 并软删 Skill，保留 artifact/审计。

### Checklist

- [ ] 交付 `管理员可下载待审/已审批包并写审计；checksum 不匹配拒绝；一键解绑+软删`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] 管理员可下载 PENDING/APPROVED；校验 artifact 归属/checksum；不暴露 storage_key
- [ ] 下载写审计；普通用户不能下载他人包
- [ ] 解绑+软删单事务；二次确认参数；记录解绑 Agent 列表
- [ ] 软删后执行入口拒绝；artifact/审批/审计保留
- [ ] [S-07][integration] 下载 + checksum + 审计
- [ ] [S-08][integration] 解绑+软删+保留证据
- [ ] [E-15][integration] checksum 不匹配拒绝执行/下载
- [ ] [harness-skill#RULE-skill-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_artifact_download.py`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-07 | integration | Console API → Artifact Store → 下载响应 → 审计 | 两种状态均可下载；响应包含正确 zip/checksum；写 SKILL_ARTIFACT_DOWNLOAD 审计；普通用户不能下载他人 artifact | tests/acceptance/user_skill_sandbox/test_s_07.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_07.py"] | planned |
| S-08 | integration | Console API → 解绑/软删事务 → PG → 审计 | 所有 AgentSkillBinding 解除；Skill 软删，执行入口拒绝；artifact/审批/审计保留；二次确认与审计记录存在 | tests/acceptance/user_skill_sandbox/test_s_08.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_08.py"] | planned |
| E-15 | integration | Artifact Store → checksum 校验 → 执行入口 | 返回 SKILL_ARTIFACT_INTEGRITY_ERROR；不执行、不下载损坏包；记录审计/告警 | tests/acceptance/user_skill_sandbox/test_e_15.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_15.py"] | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-07 | - | - | - | - | pending |
| S-08 | - | - | - | - | pending |
| E-15 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)

---

## TASK-012: 系统 Skill（source=SYSTEM）与 effective 查询

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007, TASK-010
- **Source**: user-skill-sandbox.backend.design.md#3.3 数据设计, user-skill-sandbox.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-auth#RULE-auth-001, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-09, B-04
- **Estimate**: 1d
- **Capability**: 管理员上传 `source=SYSTEM` 自动通过、当前租户全局生效、无需绑定

### Description

扩展导入 source、系统 Skill 自动 APPROVED/ALL/enabled；统一 list/count/catalog/snapshot/execution effective 查询；系统 Skill 不允许普通用户修改。

### Checklist

- [ ] 交付 `管理员上传 `source=SYSTEM` 自动通过、当前租户全局生效、无需绑定`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] 导入 source=SYSTEM/ADMIN；SYSTEM 自动通过、user_scope=ALL、enabled=true
- [ ] 系统 Skill 不需要 AgentSkillBinding；所有 effective 查询入口一致
- [ ] 普通用户不能修改/删除系统 Skill；管理员删除走软删/审计
- [ ] [S-09][integration] 系统 Skill 上传/全局生效/无绑定
- [ ] [B-04][integration] list/count/catalog/snapshot/execution 一致
- [ ] [harness-auth#RULE-auth-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_system_skill.py tests/console_skill/test_resolve_skills.py`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-09 | integration | Console API → Skill/Artifact → effective 查询 | 自动 APPROVED、user_scope=ALL、enabled=true；不需要 AgentSkillBinding；任意用户/Agent 的 effective 查询可见；普通用户不能修改/删除 | tests/acceptance/user_skill_sandbox/test_s_09.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_09.py"] | planned |
| B-04 | integration | Console/Worker/Runtime effective 查询 | list/count/catalog/snapshot/execution 均包含；不需要 binding；普通 Skill 查询不被误放大 | tests/acceptance/user_skill_sandbox/test_b_04.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_b_04.py"] | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-09 | - | - | - | - | pending |
| B-04 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)

---

## TASK-013: 统一错误码、API 封套、i18n、审计与指标

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: user-skill-sandbox.backend.design.md#3.4 接口设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-api#RULE-api-001, harness-i18n#RULE-i18n-001, harness-log#RULE-log-001
- **Rule-Owner-Refs**: harness-api#RULE-api-001, harness-i18n#RULE-i18n-001, harness-log#RULE-log-001
- **Acceptance-Refs**: S-41, E-14
- **Estimate**: 2d
- **Capability**: 新增/扩展接口统一封套与错误码；审计/指标可查；日志不泄密

### Description

补 error catalog、retryable、HTTP 状态、zh-CN/en-US 词条；审计 SKILL_*/WORKSPACE_CLEANUP；指标 execution/cleanup/quota/review；logging-kit 脱敏。

### Checklist

- [x] 交付 `新增/扩展接口统一封套与错误码；审计/指标可查；日志不泄密`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 错误码表含 `SKILL_KEY_EXISTS`/`SKILL_APPROVED_LOCKED`/`DRAFT_*`/`SANDBOX_*`/`WORKSPACE_*`/`PLATFORM_SESSION_*`
- [x] API-01/详情保留现有 `platforms`/`platform_label` 字段，不因 source/审批改造移除
- [x] Console/内部 API 统一封套与分页上限；业务只抛 error code
- [x] zh-CN/en-US 词条；用户侧不含堆栈/宿主路径/Token
- [x] 审计覆盖创建/提交/审批/下载/解绑/执行/清理；指标可查
- [x] [S-41][integration] 审计字段/指标/脱敏
- [x] [E-14][contract] 错误码/HTTP/retryable/i18n 一致
- [x] [harness-api#RULE-api-001][verifier] 运行 `uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py`
- [x] [harness-i18n#RULE-i18n-001][verifier] 运行 `uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py -k i18n`
- [x] [harness-log#RULE-log-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_audit_logging.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-41 | integration | 审计表/指标端点 → 查询 | 审计包含 actor/skill/artifact/agent/run/结果；指标包含执行/失败/清理计数；不包含 Cookie/Token/密钥原文 | tests/acceptance/user_skill_sandbox/test_s_41.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_41.py"] | stale |
| E-14 | contract | API 错误码 schema / 消息配置 | 每个错误码有 msg、http_status、retryable；用户侧不泄露堆栈/宿主机路径/Token；zh-CN/en-US 均有词条 | tests/contracts/test_skill_sandbox_contracts.py | ["uv","run","pytest","-q","tests/contracts/test_skill_sandbox_contracts.py"] | stale |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-api#RULE-api-001` | contract | 统一封套/错误码 schema | 封套/分页/错误码一致 | uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py | passed |
| `harness-i18n#RULE-i18n-001` | contract | 错误码/词条配置 | zh-CN/en-US 词条一致 | uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py -k i18n | passed |
| `harness-log#RULE-log-001` | integration | 真实 logging-kit 输出 | 单行/关联字段/脱敏 | uv run pytest -q tests/acceptance/user_skill_sandbox/test_audit_logging.py | passed |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-41 | `test_s_41.py` 首次收集失败：`WORKSPACE_CLEANUP_METRIC` 与 `skill_audit` 事件契约不存在；`test_audit_logging.py` 在 `_record_execution` 无日志时 `FileNotFoundError` | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_41.py` → 2 passed；`uv run pytest -q tests/acceptance/user_skill_sandbox/test_audit_logging.py` → 2 passed | `test_s41_skill_lifecycle_audit_rows_are_complete_and_sanitized`、`test_s41_metrics_endpoints_expose_execution_failure_cleanup_and_review`、`test_s41_skill_execution_log_is_single_line_and_correlated`、`test_s41_sensitive_log_fields_are_redacted` | 真实 PostgreSQL `control.config_audit_log` 回查 + 真实 `/metrics` ASGI HTTP 端点 + logging-kit 落盘日志；敏感 canary 不出现在审计/指标/日志 | verified |
| E-14 | 首次 3 failed：设计表错误码缺失、`MessageSpec.retryable` 不存在、`WORKSPACE_QUOTA_EXCEEDED` 未登记 | `uv run pytest -q tests/contracts/test_skill_sandbox_contracts.py` → 10 passed；`-k i18n` → 1 passed | `test_e14_skill_sandbox_codes_have_status_and_retryable`、`test_e14_skill_sandbox_error_messages_i18n_are_safe`、`test_e14_error_envelope_is_localized_and_contains_no_internal_detail` | 真实 `config/api-messages.yaml` + `MessageCatalog` + 真实 ASGI 错误封套；18 个错误码逐项核对 HTTP/retryable/双语/无泄露 | verified |
- S-41: verified — automated command passed; run_id=d1413a5a5461414890ee9d7b95bc0951 (confirmed_by: runner)
- E-14: verified — automated command passed; run_id=d1413a5a5461414890ee9d7b95bc0951 (confirmed_by: runner)
- S-41: stale — input changed: CHK-8acf6c8efcf7 (confirmed_by: runtime)
- E-14: stale — input changed: CHK-efc5e13c10c2 (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] completed (done)

---

## TASK-014: Console `/skills` 审批、下载、解绑与系统 Skill 上传

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-010, TASK-011, TASK-012, TASK-013
- **Source**: user-skill-sandbox.frontend.design.md#3.2 页面与路由结构, user-skill-sandbox.frontend.design.md#3.3 组件设计, user-skill-sandbox.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**: harness-frontend#RULE-front-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001, harness-i18n#RULE-i18n-001
- **Rule-Owner-Refs**: harness-frontend#RULE-front-001, harness-ui#RULE-ui-001, harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: S-35, S-36, S-37, E-12, E-13, S-39, S-40
- **Estimate**: 2d
- **Capability**: 管理员在 Console 完成 source/审批筛选、审批/驳回、下载、解绑软删、系统 Skill 上传

### Description

扩展 `/skills`：source/审批状态筛选、审批抽屉/Sheet、下载、解绑+软删二次确认、系统 Skill 导入；service 层 + 组件四态 + i18n/可访问性。

### Checklist

- [ ] 交付 `管理员在 Console 完成 source/审批筛选、审批/驳回、下载、解绑软删、系统 Skill 上传`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] 列表增加 source/review_status/author/agent_count；筛选与 pending 标记
- [ ] 保留现有 artifact `platforms` 列/详情 hint（`SkillPlatforms`），不因 source/审批改造移除
- [ ] 审批抽屉展示 artifact/校验/提交者/Agent/review_note；批准/驳回
- [ ] 下载、解绑+软删二次确认、系统 Skill 上传 source 选择
- [ ] service 层调用；组件 loading/empty/error/success；无权限隐藏/禁用
- [ ] [S-35][component] 审批抽屉
- [ ] [S-36][component] 解绑软删确认
- [ ] [S-37][component] source/审批筛选
- [ ] [E-12][integration] 非管理员拒绝
- [ ] [E-13][component] 空态/错误/无权限
- [ ] [S-39][E2E] 管理员真实浏览器审批
- [ ] [S-40][E2E] 管理员真实浏览器解绑软删
- [ ] [harness-frontend#RULE-front-001][verifier] 运行 `uv run pytest -q tests/frontend/test_skill_management_ui.py`
- [ ] [harness-ui#RULE-ui-001][verifier] 运行 `uv run pytest -q tests/frontend/test_skill_management_ui.py -k layout`
- [ ] [harness-ui-detail#RULE-ui-detail-001][verifier] 运行 `uv run pytest -q tests/frontend/test_skill_management_ui.py -k detail`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-35 | component | React component + mocked Console API | 展示 source、author、提交者、Agent、校验结果、文件清单、review_note；批准/驳回按钮按权限和状态启用；驳回必须填写原因 | tests/frontend/test_skill_management_ui.py | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | planned |
| S-36 | component | React component + mocked Console API | 展示将解绑的 Agent 列表；二次确认前不调用删除 API；确认后调用删除并展示结果 | tests/frontend/test_skill_management_ui.py | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | planned |
| S-37 | component | React component + mocked Console API | source 标签/筛选生效；PENDING 标记可见；分页/关键字/筛选组合正确 | tests/frontend/test_skill_management_ui.py | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | planned |
| E-12 | integration | Console API → 管理员权限校验 | 审批、下载他人包、解绑、软删、系统 Skill 上传均返回 forbidden；不泄露他人资源存在性 | tests/acceptance/user_skill_sandbox/test_e_12.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_12.py"] | planned |
| E-13 | component | React component + mocked Console API | 空态有说明和下一步；错误有重试且不丢筛选；无权限隐藏/禁用审批操作；状态不只靠颜色 | tests/frontend/test_skill_management_ui.py | ["uv","run","pytest","-q","tests/frontend/test_skill_management_ui.py"] | planned |
| S-39 | E2E | 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和管理员权限 | 状态变为 APPROVED；当前 artifact 更新；提交者 grant 和上传 Agent binding 出现；审计记录可查询 | e2e/tests/skill-self-service-approve.spec.ts | ["npm","run","skill-self-service-approve"] | planned |
| S-40 | E2E | 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和关系删除 | Skill 软删并从列表消失；Agent 绑定和用户 grant 解除；审计记录包含解绑 Agent 列表；普通用户无法执行该 Skill | e2e/tests/skill-self-service-delete.spec.ts | ["npm","run","skill-self-service-delete"] | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-frontend#RULE-front-001` | component | service 层 + mocked API | 组件不裸用 axios/fetch | uv run pytest -q tests/frontend/test_skill_management_ui.py | planned |
| `harness-ui#RULE-ui-001` | component | Semi Design 列表/筛选；mocked API | 列表布局/筛选/操作列 | uv run pytest -q tests/frontend/test_skill_management_ui.py -k layout | planned |
| `harness-ui-detail#RULE-ui-detail-001` | component | DetailSideSheet + mocked API | SideSheet/操作列/详情不重复 | uv run pytest -q tests/frontend/test_skill_management_ui.py -k detail | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-35 | - | - | - | - | pending |
| S-36 | - | - | - | - | pending |
| S-37 | - | - | - | - | pending |
| E-12 | - | - | - | - | pending |
| E-13 | - | - | - | - | pending |
| S-39 | - | - | - | - | pending |
| S-40 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)

---

## TASK-015: 系统依赖基线（镜像预装）

- **Status**: done
- **Priority**: P1
- **Depends**: -
- **Source**: user-skill-sandbox.backend.design.md#3.5 质量实现方案, user-skill-sandbox.backend.design.md#4.1 部署架构
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-skill#RULE-skill-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-48, E-17
- **Estimate**: 1d
- **Capability**: 管理员通过镜像构建预装 Python/Node 依赖；Skill 直接 import；workspace 临时安装不作为生产依赖

### Description

把批准依赖加入 requirements/base image，重建 runtime/worker；禁止运行时修改 Pod site-packages；文档/验收覆盖跨 Pod 不可复现。

### Checklist

- [x] 交付 `管理员通过镜像构建预装 Python/Node 依赖；Skill 直接 import；workspace 临时安装不作为生产依赖`，并提供可重复的演示命令/操作（1–2 天可完成）
- [x] 依赖清单/镜像构建流程与 image digest 记录
- [x] 所有副本一致；不在运行 Pod 安装系统包
- [x] workspace 临时安装标记为验证，TTL/配额/清理生效
- [x] [S-48][integration] 预装依赖直接 import/require
- [x] [E-17][integration] 临时安装在新 Pod 不存在，生产路径要求预装
- [x] [harness-arch#RULE-arch-001][verifier] 运行 `uv run pytest -q tests/architecture/test_k8s_deploy_manifests.py`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-48 | integration | 镜像依赖清单 → runtime/worker 镜像 → bwrap 系统 site-packages → Skill import/require | Skill 不需要 workspace 安装即可 import/require；所有 Runtime/Worker 副本依赖版本一致；image digest 和依赖变更记录可查；不在运行中的 Pod 修改系统 site-packages | tests/acceptance/user_skill_sandbox/test_s_48.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_48.py"] | stale |
| E-17 | integration | Pod A 临时安装 → Pod B/重建后执行同一 Skill | 新 Pod 不保证存在该依赖，import 失败可见；生产路径要求管理员预装到系统镜像；临时安装计入 workspace 配额，可被 TTL 清理 | tests/acceptance/user_skill_sandbox/test_e_17.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_e_17.py"] | stale |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-48 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_48.py` → `4 failed in 2.49s`：脚本/基线/Dockerfile 接线尚不存在（`FileNotFoundError: scripts/skill_system_deps.py`） | 同命令 → `4 passed`；与 E-17 联合 `7 passed in 130.41s`；预装 `tabulate`/`uuid` 在真实 bwrap 中直接 import/require；runtime/worker 记录同为 `baseline_sha256=e0361c1c...`；运行用户对 site-packages/Node 全局目录不可写 | `tests/acceptance/user_skill_sandbox/test_s_48.py::test_s48_baseline_is_declared_and_both_images_install_it`、`::test_s48_skill_imports_python_and_requires_node_inside_bwrap_without_workspace`、`::test_s48_image_digest_and_dependency_record_are_verifiable`；`tests/architecture/test_skill_system_deps.py` | 探针镜像由带 bwrap 的基础镜像 + 同一 `build/docker-build/skill-deps/install.sh` 真实构建；bwrap `--unshare-all`、UID 10001；`record/verify-records/change` 读取真实 `docker image inspect` digest 与容器内实际版本，未 mock | verified |
| E-17 | `uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_17.py` → `3 failed in 2.44s`：缺少基线脚本与运维文档，无法建立 Pod A/Pod B 边界 | 同命令 → `3 passed`；与 S-48 联合 `7 passed in 130.41s`；Pod A 在 workspace `pip install --target` 成功，Pod B 空 workspace 报 `ModuleNotFoundError: e17_demo_dep`；系统基线 `tabulate` 仍可 import；删除 workspace 后临时依赖不可恢复 | `tests/acceptance/user_skill_sandbox/test_e_17.py::test_e17_temp_install_does_not_survive_a_new_pod`、`::test_e17_temp_install_is_confined_to_workspace_and_removed_by_cleanup`、`::test_e17_policy_requires_image_preinstall_and_counts_workspace_usage` | 两个真实容器 + 真实 bwrap + 真实 pip；临时安装只在 `/workspace`（含 build/target），未写入系统 site-packages/Node 全局目录；TTL 清理以删除 workspace 的最终效果验证，配额运行时实现归 TASK-005/006 | verified |
- S-48: verified — automated command passed; run_id=1c47d35f8a2042e1b7c45521db235837 (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=1c47d35f8a2042e1b7c45521db235837 (confirmed_by: runner)
- S-48: verified — automated command passed; run_id=94f8146b6c694d8aad1e0f06a5578392 (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=94f8146b6c694d8aad1e0f06a5578392 (confirmed_by: runner)
- S-48: verified — automated command passed; run_id=f7299e96522d491191da4ef54a15d32e (confirmed_by: runner)
- E-17: verified — automated command passed; run_id=f7299e96522d491191da4ef54a15d32e (confirmed_by: runner)
- S-48: stale — input changed: CHK-62857f0bce9d (confirmed_by: runtime)
- E-17: stale — input changed: CHK-d491931df65a (confirmed_by: runtime)

### Log

- [2026-11-06] created (draft)
- [2026-11-06] started
- [2026-11-06] completed (done)

---

## TASK-016: 验收骨架、E2E 与 manifest 锁定

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-003, TASK-005, TASK-010, TASK-014
- **Source**: user-skill-sandbox.backend.design.md#2.5 验收条件, user-skill-sandbox.scenarios.md
- **Spec-Refs**: harness-e2e#RULE-e2e-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-e2e#RULE-e2e-001, harness-test#RULE-test-001
- **Acceptance-Refs**: S-39, S-40
- **Estimate**: 2d
- **Capability**: 验收场景有可收集测试骨架；管理员审批/解绑 E2E 可按触发路径运行；manifest 可锁定

### Description

建立 `tests/acceptance/user_skill_sandbox/`、component/contract 测试骨架和 Playwright 技能审批/解绑用例；登记 check/test_ids，锁定 acceptance manifest。

### Checklist

- [ ] 交付 `验收场景有可收集测试骨架；管理员审批/解绑 E2E 可按触发路径运行；manifest 可锁定`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] 为 49 个场景建立测试骨架/具体测试 ID 与命令（未实现模块的 import 放测试体内）
- [ ] E2E 只保留 S-39/S-40，真实浏览器 + 真实后端 + 真实 PG；非 smoke 带 trigger_paths
- [ ] 登记 pytest/Playwright check；锁定 manifest；运行收集/质量门禁
- [ ] [S-39][E2E] 管理员审批关键旅程
- [ ] [S-40][E2E] 管理员解绑软删关键旅程
- [ ] [harness-e2e#RULE-e2e-001][verifier] 运行 `npm run skill-self-service-approve && npm run skill-self-service-delete`
- [ ] [harness-test#RULE-test-001][verifier] 运行 `uv run pytest -q tests/architecture tests/acceptance/user_skill_sandbox`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-39 | E2E | 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和管理员权限 | 状态变为 APPROVED；当前 artifact 更新；提交者 grant 和上传 Agent binding 出现；审计记录可查询 | e2e/tests/skill-self-service-approve.spec.ts | ["npm","run","skill-self-service-approve"] | referenced |
| S-40 | E2E | 真实浏览器 → Console 前端 → Console API → 真实 PG；真实登录和关系删除 | Skill 软删并从列表消失；Agent 绑定和用户 grant 解除；审计记录包含解绑 Agent 列表；普通用户无法执行该 Skill | e2e/tests/skill-self-service-delete.spec.ts | ["npm","run","skill-self-service-delete"] | referenced |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-e2e#RULE-e2e-001` | E2E | 真实浏览器 + 真实后端 + 真实 PG | 审批/解绑关键旅程；成功路径不 mock | npm run skill-self-service-approve && npm run skill-self-service-delete | planned |
| `harness-test#RULE-test-001` | integration | 真实 PG/Redis/HTTP/浏览器边界 | 分层/真实边界/E2E 精度 | uv run pytest -q tests/architecture tests/acceptance/user_skill_sandbox | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-39 | - | - | - | - | pending |
| S-40 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)

---

## TASK-017: MCP/Memory/Model/IM 非干扰回归

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-005, TASK-007
- **Source**: user-skill-sandbox.backend.design.md#3.2 架构设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002, harness-mcp#RULE-mcp-001, harness-memory#RULE-memory-001, harness-memory#RULE-memory-002, harness-memory#RULE-memory-003, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-memory#RULE-memory-006, harness-model#RULE-model-001
- **Rule-Owner-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002, harness-mcp#RULE-mcp-001, harness-memory#RULE-memory-001, harness-memory#RULE-memory-002, harness-memory#RULE-memory-003, harness-memory#RULE-memory-004, harness-memory#RULE-memory-005, harness-memory#RULE-memory-006, harness-model#RULE-model-001
- **Acceptance-Refs**: S-45, S-46
- **Estimate**: 1.5d
- **Capability**: 现有 MCP catalog、Memory 写入/召回、模型选择、渠道中立性回归全绿

### Description

新增 Skill/Workspace 代码不修改 MCP/Memory/Model 协议；补非干扰回归和渠道中立性架构测试。

### Checklist

- [ ] 交付 `现有 MCP catalog、Memory 写入/召回、模型选择、渠道中立性回归全绿`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] MCP catalog/tool registry 不变
- [ ] Memory 写入/召回/软删/版本/consolidation 不变
- [ ] Agent 模型选择无平台默认回退
- [ ] 核心域零渠道专有字样/取件形状
- [ ] [S-45][integration] MCP/Memory/Model 非干扰
- [ ] [S-46][integration] 渠道中立性
- [ ] [harness-im#RULE-im-001][verifier] 运行 `uv run pytest -q tests/architecture/test_channel_neutrality.py`
- [ ] [harness-im#RULE-im-002][verifier] 运行 `uv run pytest -q tests/architecture/test_channel_neutrality.py`
- [ ] [harness-mcp#RULE-mcp-001][verifier] 运行 `uv run pytest -q tests/frontend/test_mcp_module_contract.py`
- [ ] [harness-memory#RULE-memory-001][verifier] 运行 `uv run pytest -q tests/test_memory_redesign_spec_contract.py tests/migrations/test_memory_redesign_migration.py tests/migrations/test_memory_redesign_schema_parity.py`
- [ ] [harness-memory#RULE-memory-002][verifier] 运行 `uv run pytest -q tests/test_memory_redesign_spec_contract.py -k lock`
- [ ] [harness-memory#RULE-memory-003][verifier] 运行 `uv run pytest -q tests/test_memory_redesign_spec_contract.py -k query`
- [ ] [harness-memory#RULE-memory-004][verifier] 运行 `uv run pytest -q tests/test_memory_redesign_spec_contract.py -k delete`
- [ ] [harness-memory#RULE-memory-005][verifier] 运行 `uv run pytest -q tests/test_memory_redesign_spec_contract.py -k consolidation`
- [ ] [harness-memory#RULE-memory-006][verifier] 运行 `uv run pytest -q tests/test_memory_redesign_spec_contract.py -k migration`
- [ ] [harness-model#RULE-model-001][verifier] 运行 `uv run pytest -q tests/contracts/test_capacity_webchat_model_contracts.py tests/frontend/test_model_form_contract.py`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-45 | integration | 现有 MCP catalog、Memory 写入/召回、模型选择测试 | MCP catalog/tool registry 行为不变；Memory 写入/召回/软删/版本行为不变；Agent 模型选择不出现平台默认回退 | tests/acceptance/user_skill_sandbox/test_s_45.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_45.py"] | planned |
| S-46 | integration | 架构扫描 tests/architecture/test_channel_neutrality.py | 核心域零渠道专有字样；核心域不向 ResolveDefinitionRequest.channel 传字面量；渠道字样只出现在允许表面 | tests/acceptance/user_skill_sandbox/test_s_46.py | ["uv","run","pytest","-q","tests/acceptance/user_skill_sandbox/test_s_46.py"] | planned |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-im#RULE-im-001` | integration | 真实架构扫描；不 mock | 核心域零渠道专有形状 | uv run pytest -q tests/architecture/test_channel_neutrality.py | planned |
| `harness-im#RULE-im-002` | integration | 真实架构扫描；不 mock | 渠道差异只在适配器 | uv run pytest -q tests/architecture/test_channel_neutrality.py | planned |
| `harness-mcp#RULE-mcp-001` | integration | 现有 MCP catalog/tool registry | MCP 行为不变 | uv run pytest -q tests/frontend/test_mcp_module_contract.py | planned |
| `harness-memory#RULE-memory-001` | integration | 现有 Memory 测试 | Memory 确认/写入不变 | uv run pytest -q tests/test_memory_redesign_spec_contract.py | planned |
| `harness-memory#RULE-memory-002` | integration | 现有 Memory 测试 | 锁序/唯一身份/事务不变 | uv run pytest -q tests/test_memory_redesign_spec_contract.py | planned |
| `harness-memory#RULE-memory-003` | integration | 现有 Memory 测试 | 查询/注入/缓存不变 | uv run pytest -q tests/test_memory_redesign_spec_contract.py | planned |
| `harness-memory#RULE-memory-004` | integration | 现有 Memory 测试 | 删除/tombstone/退休不变 | uv run pytest -q tests/test_memory_redesign_spec_contract.py | planned |
| `harness-memory#RULE-memory-005` | integration | 现有 Memory 测试 | consolidation job 不变 | uv run pytest -q tests/test_memory_redesign_spec_contract.py | planned |
| `harness-memory#RULE-memory-006` | integration | 现有 Memory 测试 | 冻结/迁移不变 | uv run pytest -q tests/test_memory_redesign_spec_contract.py | planned |
| `harness-model#RULE-model-001` | contract | 模型契约/选择逻辑 | 无平台默认回退 | uv run pytest -q tests/contracts/test_capacity_webchat_model_contracts.py tests/frontend/test_model_form_contract.py | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-45 | - | - | - | - | pending |
| S-46 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)

---

## TASK-018: 部署：workspace PVC、Helm、ConfigMap 与 canary

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-005, TASK-006
- **Source**: user-skill-sandbox.backend.design.md#4.1 部署架构, user-skill-sandbox.backend.design.md#4.2 发布与回滚
- **Spec-Refs**: harness-arch#RULE-arch-001
- **Rule-Owner-Refs**: harness-arch#RULE-arch-001
- **Acceptance-Refs**: S-26, E-07
- **Estimate**: 1.5d
- **Capability**: 本地/内网 K8s 部署包含独立 workspace PVC，runtime/worker 挂载，canary 通过

### Description

新增 `muad-workspaces-rwx`、runtime/worker volumeMount、Helm `global.workspaces`、ConfigMap 配额/TTL；更新内网部署/初始化文档；执行 bwrap/PVC canary。

### Checklist

- [ ] 交付 `本地/内网 K8s 部署包含独立 workspace PVC，runtime/worker 挂载，canary 通过`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] 新增 PVC + kustomization；runtime/worker 挂载 `/mnt/muad-workspaces`
- [ ] Helm runtime/worker values/templates；console/gateway/connector 不变
- [ ] ConfigMap workspace 配额/TTL；内网文档同步
- [ ] bwrap/PVC writable canary；记录 image digest/节点指纹
- [ ] [S-26][unit] macOS dev-unsafe/fail closed
- [ ] [E-07][unit] bwrap 不可用 fail closed
- [ ] [harness-arch#RULE-arch-001][verifier] 运行 `uv run pytest -q tests/architecture/test_k8s_deploy_manifests.py tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-26 | unit | SandboxRunner 工厂 → 平台检测 → dev-unsafe 开关 | 未显式开启 dev-unsafe 时拒绝执行；显式开启时执行并留下审计；生产配置不能启用 dev-unsafe | tests/agent_core/test_sandbox_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_sandbox_runner.py"] | referenced |
| E-07 | unit | SandboxRunner → bwrap 探测 | 返回 SANDBOX_UNAVAILABLE；不回退宿主执行；日志/指标可见 | tests/agent_core/test_sandbox_runner.py | ["uv","run","pytest","-q","tests/agent_core/test_sandbox_runner.py"] | referenced |

**Spec Verifier**

| Rule | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 执行命令 | 状态 |
|---|---|---|---|---|---|
| `harness-arch#RULE-arch-001` | integration | 真实 k8s manifest/Helm 渲染；不 mock 部署单元 | 四单元不变；workspace PVC 挂载正确 | uv run pytest -q tests/architecture/test_k8s_deploy_manifests.py tests/architecture/test_capacity_webchat_runtime_worker_deploy.py | planned |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-26 | - | - | - | - | pending |
| E-07 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)

---

## TASK-019: 老 Skill 兼容改造与验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005
- **Source**: user-skill-sandbox.backend.design.md#3.4 接口设计, user-skill-sandbox.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-project-platform#RULE-platform-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-13, S-14
- **Estimate**: 1.5d
- **Capability**: `login-mssp-platform` 不改代码运行；`mss-report-skill` 最小改造后运行

### Description

按老 Skill 兼容契约改造数据/输出目录、Cookie、base_url、Linux 路径、调度、webhook；真实平台验收。

### Checklist

- [ ] 交付 ``login-mssp-platform` 不改代码运行；`mss-report-skill` 最小改造后运行`，并提供可重复的演示命令/操作（1–2 天可完成）
- [ ] `SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR` 路径适配
- [ ] session-manager 替代 cookies.txt；base_url 用 requestProfile
- [ ] POSIX 路径/平台 Schedule/移除 webhook 硬编码
- [ ] [S-13][manual] login-mssp-platform 不改代码
- [ ] [S-14][manual] mss-report-skill 改造验收
- [ ] [harness-project-platform#RULE-platform-001][verifier] 运行 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_legacy_skills.py`
- [ ] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-13 | integration | 真实 Runtime → 真实 Console/Redis → 真实 mssp 平台 → 旧 Skill 脚本 | 旧脚本不修改即可调用 session-manager get-state；stdout platforms list 包含 mssp 和 expiresAt；退出码 0，输出 ok/expiresAt | manual | - | manual_pending |
| S-14 | integration | 真实 Runtime/Worker → 真实平台 → 改造后的旧 Skill | 报告写入 SKILL_OUTPUT_DIR；cache/logs/companies 写入 SKILL_DATA_DIR；不再写 Skill 包根目录；平台地址来自 requestProfile | manual | - | manual_pending |

### Acceptance Evidence

> 编码期由 `cf-task-start` 填写 RED/GREEN、断言位置与真实边界证据；manual 场景登记原因/边界/验收方式。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-13 | - | - | - | - | pending |
| S-14 | - | - | - | - | pending |

### Log

- [2026-11-06] created (draft)
