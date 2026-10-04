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
- Choose the optimal design over a compatibility shim — no back-compat layers, dual-write, or transitional adapters, and no keeping a wrong design just to match older spec text. **The spec follows the code's facts**: fix the code, then rewrite the spec to describe what is actually true. (Stated twice: 2026-09-17 and 2026-09-30; instances: row lock instead of retry-on-conflict, process-group kill instead of swallowing termination errors.)
- Compaction must never fail a Run: every compression layer degrades to "no compression" — a failed summary keeps the original history, a failed batch artifact write rolls the whole round back so the results stay inline — and degrading stays visible (warning log + metric, e.g. `context_compaction_total{layer="*",status="FAILED"}`), never silently swallowed. (Instance: `RuntimeContextCompactor.compact` try/except in `apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py:111-121`; batch rollback in `apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py:198-239`.)

## Forbidden Patterns
- Hard-coded secrets or credentials
- Unparameterized SQL
- Network calls inside tight loops

<!-- code-flow:spec-loading schema=1 start -->
## 验收怎么跑（全量测试）

- **pytest 验收**：`make acceptance`（= `uv run pytest -q tests/acceptance`）。
  **不需要任何环境变量**：`tests/acceptance/conftest.py` 的 session fixture 会自动建一个空库
  （`muad_acc_<uuid>`，空库 + `alembic upgrade head`）、Redis 用 10–15 号，跑完自动 drop；
  每轮开销约 0.8s。若看到"需要 CREATEDB"的提示，那是一次性环境前提（本机 dev 实例的 `muad`
  角色缺 `CREATEDB`，用超级用户 `ALTER ROLE muad CREATEDB;` 即可）。
- **浏览器验收（Playwright）**：`make acceptance-e2e DOMAIN=<域>`（域如 `console-auth`、
  `overview-dashboard`、`audit-observability`、`task-schedule`、`agent`）。命令已含前端
  `npm run build` —— preview 跑的是构建产物，不 build 会拿到陈旧产物。
- **不要**用共享 dev 库跑验收，也**不要**为了"清干净"去删 dev 库的行（那是开发数据）。
  必须独立库的原因与机制见 `.code-flow/specs/test/harness-test.md`（claim 与投递选取不带租户谓词
  + 各栈出站端点是栈级 env ⇒ 共用库时跨套件互相污染，表现为"单跑绿、串跑红"）。
- **跑单测/验收前先停 dev 服务**：`dev.sh` 起的进程与测试共用同一套 PG/Redis，其中
  `--reload` 的 Worker 会**领走本租户的任务**（claim 不带租户谓词）并用自己的 `ARTIFACT_ROOT`
  执行，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类"莫名其妙"的失败。跑前先查残留进程
  （`ps -eo pid,etime,command | grep -E "pytest|uvicorn|vite"`），别把这种情况当实现缺陷去改代码。
- 排查用逃生阀：`MUAD_ACCEPTANCE_SHARED_DB=1` 可让 pytest 验收退回共享库（仅用于对比排查）。

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
4. 新增/修改规范时优先用 ✅/❌ 代码对照示例表达 —— 写进目标 spec 的 `## Conventions` 段（**没有独立的 `## Examples` 段**：20 份 harness spec 一律是 `## Rules` / `## Conventions` / `## Avoid` 三个 H2，示例以 ✅/❌ 内联在 Conventions 的条目下。**不要引入 H3**：spec 的 rule/verifier 解析只认 H2，H3 会被当成规则正文错位。

## 收尾提交纪律（Done Gate）

- 任务收尾提交的信息**必须以 Done Gate 的实际裁决为准**：先拿到 `cf_task_workflow.py finish` 的返回，再写提交信息。禁止把 `finish` 与 `git commit` 串在同一条 `&&` 链里抢先写入结论——本项目曾因此在同一需求上连续写错三次提交信息（先误写 pass、后反向误写 block），虽经 `--amend` 改正，错误结论仍进过历史。
- Gate `block` 时不得写「Gate pass」；已 pass 时也不得写「阻塞待查」。提交信息只陈述已发生的事实，不写预期。
- 规则门禁/verifier 的失败若**单跑通过、整跑偶发失败**，先怀疑环境残留（孤儿进程、共享测试库），复跑一次再下结论，不要直接改实现。

## Task Documents (cf-task workflow)

- `.code-flow/specs/shared/` holds PRD/design templates（含前端 `design-frontend.md`）used by `/cf-task:prd` and `/cf-task:align`
- 一个需求的 prd / design / tasks 同放需求目录 `.code-flow/tasks/<日期>/<需求>/`；全栈需求可有 `<需求>.frontend.design.md` + `<需求>.backend.design.md`，`/cf-task:plan <需求目录>` 合并拆解，`/cf-task:archive` 按整个需求目录归档（旧扁平布局仍兼容）
- Workflow: `/cf-task:prd` → `.prd.md` → `/cf-task:align <.prd.md>` → `.design.md`(s) → `/cf-task:plan <需求目录>` → tasks
- Templates are read by the commands themselves; you do not need to pre-load them
