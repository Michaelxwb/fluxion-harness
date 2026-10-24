# 非 smoke 真实栈回归套件

> 这三套不是 component 候选：它们依赖真实 Worker/Runtime/Gateway、真实 SSE、真实 PG 种子
> 或进程状态，mock 会改变被测语义。按 0.8 口径登记为 **non-smoke 回归**，不进入 PR smoke /
> changed 门禁；由 push 的完整 `e2e` job（或 nightly/release）执行。

| 套件 | 配置 | 条数 | 真实边界 | 为什么不能下沉 component | 触发范围 |
|---|---|---:|---|---|---|
| task-schedule 回归 | `playwright.task-schedule-regression.config.ts` | 26 | stub 会话 + 真实 Worker/PG + `__e2e` 种子/状态推进 | 真实领取、轮询、调度与状态机；mock 无法证明 Worker 行为 | `apps/agent-worker/**`、`apps/console-platform/frontend/src/modules/task-schedule/**` |
| webchat 回归 | `playwright.webchat-regression.config.ts` | 32 | 真实入口代理 + Console/Gateway/Runtime/Worker + 模型/企微探针 + 真实 PG/Redis/SSE | 真实 SSE、共享 Cookie 罐、跨服务投递与租户隔离；mock 会退化成 UI 断言 | `apps/im-gateway/**`、`apps/agent-runtime/**`、`apps/console-platform/frontend/src/modules/webchat/**` |
| run-observability 回归 | `playwright.run-observability-regression.config.ts` | 11 | 真实 Console/Worker + 真实投影 + 种子状态推进 | 真实审计投影、分页边界与状态推进；component mock 无法覆盖 | `apps/console-platform/frontend/src/modules/run-observability/**`、`apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py` |

补充：

- `foundation`（默认 `playwright.config.ts`）4 条是桩后端 contract smoke，属于 `RULE-test-001`
  的浏览器腿，不计入本表。
- 这三套与 `*-e2e` 关键配置严格互补（`grep`/`grepInvert`），同一批用例不会被重复执行。
- 2026-10-23 实测：task 26 passed、webchat 32 passed、run 11 passed。
- 若未来要把其中某条下沉 component/integration，必须先让该条脱离共享种子/顺序假设，并补齐
  对应的真实边界替代（SSE 探针、进程故障注入等）；不能只删浏览器断言。
