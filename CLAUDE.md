# Project Guidelines

## Team Identity
- Team: fluxion-harness
- Project: fluxion-harness
- Language: Python 3.12+ (FastAPI/SQLAlchemy) + TypeScript 5.7 (React 18 Console, Fastify Gateway)

## Core Principles
- All changes must include tests
- Single responsibility per function (<= 50 lines)
- No loose typing or silent exception handling
- Handle errors explicitly
- Tenant-scoped existence checks: scope `count` to the tenant (`count(tenant_id)`), never a global count — a global count lets another tenant's rows hide that the default tenant has no account and nobody can log in (instance: Console startup self-check `_warn_if_no_accounts()`)
- Choose the optimal design over a compatibility shim — no back-compat layers, dual-write, or transitional adapters, and no keeping a wrong design just to match older spec text. **The spec follows the code's facts**: fix the code, then rewrite the spec to describe what is actually true.
- Compaction must never fail a Run: every compression layer degrades to "no compression" — a failed summary keeps the original history, a failed batch artifact write rolls the whole round back so the results stay inline — and degrading stays visible (warning log + metric, e.g. `context_compaction_total{layer="*",status="FAILED"}`), never silently swallowed. (Instance: `RuntimeContextCompactor.compact` try/except in `apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py`; batch rollback in `apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py` 的 `persist_round_results_with_session`.)

## Forbidden Patterns
- Hard-coded secrets or credentials
- Unparameterized SQL
- Network calls inside tight loops

## 验收怎么跑（全量测试）

- **pytest**：`make acceptance`（自动建空库 + Redis 10–15 号，无需环境变量）。
- **浏览器**：`make acceptance-e2e DOMAIN=<域>`（含前端 build；preview 跑构建产物）。
- **不要**共用 dev 库跑验收；跑前先停 dev 服务并查残留进程（`--reload` Worker 会抢任务，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类假故障）。
- **全量校验**：由 CI 或手动执行；v1 的 `task finish` 只跑当前 TASK 声明的相关测试，`task archive` 只应用 Spec Delta 并移动需求，不执行测试。
- **触发词**：用户说“复用 CI 流水线 / 复用 CI 缓存 / 用 CI 跑归档 / 用 CI 结果归档”时，不要直接跑本地全量；先读 `docs/operations/ci-validation-cache.md`，按 runbook 触发 `workflow_dispatch`、下载 artifact，再执行对应验收。**默认用 kimi-webbridge 控制已登录浏览器完成触发和 artifact 下载，不要等用户提供 token/gh。**
- 隔离机制、残留处理与逃生阀 `MUAD_ACCEPTANCE_SHARED_DB=1` 见 `.code-flow/specs/test/harness-test.md`。

## Spec 编写要求

- Specs 只写规范与编码要求，不写会随代码/配置变化的实现值（指标名、超时/重试秒数、计数/容量上限、默认值、端口/IP、源码行号）；需要时引用配置或契约所有者（`*Settings`、`MAX_*`、ConfigMap 键），具体值以代码/配置为准。设计模板中的示例占位值除外。
- 使用 JSON frontmatter（`description`、`next_rule`）和路径身份；规则放在 `## Rules`，保留稳定的 `RULE-*` ID。新增规则的编号来自 `next_rule`。
- 修改已接受规则时通过 Task 的 Spec Delta；archive 应用 Delta，并保留 Applied Deltas 记录。
- 新增/修改规范用 ✅/❌ 对照示例，写进 `## Conventions`；harness spec 只有 `## Rules` / `## Conventions` / `## Avoid` 三个 H2，不引入 H3。

## 收尾提交纪律

- 任务收尾提交信息**以 `code-flow task finish` 的实际结果为准**：先拿到返回，再写提交信息；禁止把 `finish` 与 `git commit` 串在同一条 `&&` 链里抢先写结论。
- 测试失败时不得写“完成”；提交信息只陈述已发生的事实。
- 排查/方案类请求先给证据和根因，再对齐方案，不盲猜或先改代码；门禁/测试“单跑通过、整跑偶发失败”先查残留（孤儿进程、共享测试库），复跑一次再下结论。

## Task Documents

- 一个需求一份任务文档 `.code-flow/tasks/<日期>/<需求>/<需求>.md`，包含多个 `## TASK-NNN` 段。
- `workflow: full`：PRD/design/scenarios 与任务文档同放需求目录；模板在 `.code-flow/templates/`。
- 流程：`cf-task:prd` → `.prd.md` → `cf-task:align` → design → `cf-task:plan` → 任务文档。
- 全栈需求可有 `<需求>.frontend.design.md` + `<需求>.backend.design.md`；`cf-task:plan <需求目录>` 合并拆解，`cf-task:archive` 按整个需求目录归档。
- 完成前用 `code-flow task finish <任务文档> --task TASK-NNN` 执行声明测试；`cf-task:archive` 只应用 Spec Delta 并移动需求。

<!-- code-flow:v1 start -->
使用全局 code-flow CLI，以及 `.code-flow/config.json`、`.code-flow/specs/`、`.code-flow/tasks/`、`.code-flow/templates/` 中的当前项目数据。

一个需求一份任务文档 `<requirement>.md`，包含多个 `## TASK-NNN` 段。通过任务文档路径 + TASK ID，或 prompt Hook 提供的 session token 选择 Task；不要根据状态推断。

实现前读取当前 Task 的 Spec 引用。Spec 引用是相对于 `.code-flow/specs` 的路径，不带 `.md`。

工作流级别来自 `.code-flow/config.json` 的 `workflow`：`hook`（默认）使用 Spec 注入加一份带声明测试的任务文档，不要求 PRD/design/scenarios；`full` 增加 PRD/design/scenarios 流程。设计文档是项目资产，不作为 prompt 输入；只有 Spec 会被注入。

`full` 工作流按阶段推进：PRD、design/scenarios、任务文档分别输出后停下等待用户复核；确认前不要进入下一步，也不要开始写代码。scenarios 是给人工执行的 Markdown 步骤，不是 E2E 代码。

文档流程：`cf-task:prd` → `cf-task:align` → `cf-task:plan` → `cf-task:start`；全部 TASK done 后在协调工作区执行 `cf-task:archive`。Spec 检查/沉淀用 `cf-spec`，健康检查用 `cf-check`。

标记完成前，用 `task finish <task-document>.md --task TASK-NNN` 执行声明的测试。测试应限定在有意义的 unit/integration 回归；优先一次框架调用，使用框架自己的 workers。

archive 应用声明的 Spec Delta 并移动需求；CI 独立执行完整回归和 E2E。使用 `cf-task:prd`、`cf-task:align` 和 `cf-task:plan` 处理文档流程；不要为每个 Task 建一个文件。

Hook 诊断永不阻止工作。缺少绑定时要显式提供任务文档 + TASK ID，或重新执行 task start。
<!-- code-flow:v1 end -->
