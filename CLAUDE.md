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

<!-- code-flow:spec-loading schema=1 start -->
## 验收怎么跑（全量测试）

- **pytest**：`make acceptance`（自动建空库 + Redis 10–15 号，无需环境变量）。
- **浏览器**：`make acceptance-e2e DOMAIN=<域>`（含前端 build；preview 跑构建产物）。
- **不要**共用 dev 库跑验收；跑前先停 dev 服务并查残留进程（`--reload` Worker 会抢任务，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类假故障）。
- 隔离机制、残留处理与逃生阀 `MUAD_ACCEPTANCE_SHARED_DB=1` 见 `.code-flow/specs/test/harness-test.md`。

## Spec Workflow (schema 1)

- If `.code-flow/.active-task.json` exists, validate it and `spec-context.yml`, then use only the active TASK's `Spec-Refs`, Design refs, and Acceptance Contract. Never reselect Specs from Catalog.
- Without an active TASK, explicit file paths use deterministic `path_mapping` constraints; prompts without paths receive the Spec Catalog for exploration or creation of the next Context.
- PRD, Design, Plan, Start, Coding, and Done inherit one persisted Context. Required rules must be applied and verified before their stage gate passes.
- A corrupt marker, Context hash drift, or required scope expansion is `SPEC_WORKFLOW_BLOCKED`; run `cf-spec refresh/doctor` instead of falling back.
- Tier 0 `_map.md` files are navigation only. Rule constraints live in metadata-bearing Tier 1 Specs.

Do NOT ask the user which Specs to load—the Context-first router is authoritative.
<!-- code-flow:spec-loading schema=1 end -->

## 合规反馈协议（quality_loop）

1. 编辑代码后收到 **Spec 合规反馈 (auto-check)** 时，先按提示修正违规，再继续当前任务
2. 用户表示某条反馈是误报（"这是误报"/"忽略这个检查"）时，代为执行：
   `python3 .code-flow/scripts/cf_feedback.py ignore <check-id>`
   （check-id 见反馈中的 `规则: <spec>#<check-id>`；同一规则误报达阈值会自动停用）
3. 会话收尾被校验拦回（cf-stop 反馈未过项）时，修复后再结束；不要绕过
4. 新增/修改规范用 ✅/❌ 对照示例，写进 `## Conventions`；harness spec 只有 `## Rules` / `## Conventions` / `## Avoid` 三个 H2，**不引入 H3**（解析只认 H2）。

## 收尾提交纪律（Done Gate）

- 任务收尾提交信息**以 Done Gate 的实际裁决为准**：先拿到 `cf_task_workflow.py finish` 的返回，再写提交信息；禁止把 `finish` 与 `git commit` 串在同一条 `&&` 链里抢先写结论（本项目曾连续写错三次，`--amend` 后错误结论仍进过历史）。
- Gate `block` 时不得写「Gate pass」；已 pass 时也不得写「阻塞待查」。提交信息只陈述已发生的事实。
- 排查/方案类请求先给证据和根因，再对齐方案，不盲猜或先改代码；门禁/verifier「单跑通过、整跑偶发失败」先查残留（孤儿进程、共享测试库），复跑一次再下结论。

## Task Documents (cf-task workflow)

- `.code-flow/specs/shared/` holds PRD/design templates（含前端 `design-frontend.md`）used by `/cf-task:prd` and `/cf-task:align`
- 一个需求的 prd / design / tasks 同放需求目录 `.code-flow/tasks/<日期>/<需求>/`；全栈需求可有 `<需求>.frontend.design.md` + `<需求>.backend.design.md`，`/cf-task:plan <需求目录>` 合并拆解，`/cf-task:archive` 按整个需求目录归档（旧扁平布局仍兼容）
- Workflow: `/cf-task:prd` → `.prd.md` → `/cf-task:align <.prd.md>` → `.design.md`(s) → `/cf-task:plan <需求目录>` → tasks
- Templates are read by the commands themselves; you do not need to pre-load them
